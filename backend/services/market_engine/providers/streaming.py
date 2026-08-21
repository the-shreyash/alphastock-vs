"""Streaming tick provider — the seam a pushed feed enters the platform through.

WHAT THIS IS
------------
A :class:`~services.market_engine.providers.base.MarketDataProvider` that is fed
by something else pushing into it, rather than by the gateway pulling from it.
It completes the chain MARKET_DATA_ARCHITECTURE.md has always specified and the
platform has never had::

    a pushed feed
          ↓  canonical MarketTick (services/market_engine/ticks.py)
    StreamingTickProvider.on_raw()          ← this module
          ↓  Market Gateway sink
    Market Gateway  →  Source Manager  →  Event Bus  →  Market Engine

WHY IT IS GENERIC AND NAMES NO FEED
------------------------------------
This module is in the Market Engine, which may not import the broker layer
(pinned by `test_the_market_engine_never_imports_a_broker_module`) and must be
able to resolve, rank and deliver a streaming feed without knowing that brokers
exist as a concept. That is not tidiness: it is what lets a broker WebSocket, a
licensed exchange feed and a future vendor feed be *the same kind of thing* to
the Source Manager, so priority ordering stays provider metadata instead of
becoming a chain of `if broker == …`.

So the construction direction is: the side that owns the feed builds one of
these, names it, sets `owner_user_id`, and registers it through the Market
Gateway. This module never reaches back. A second, entirely fictional feed
therefore needs zero lines here — which is what
`test_a_second_fictional_broker_uses_the_same_seam` exists to keep true.

WHY THERE IS NO NORMALIZER FAMILY FOR THE *PUSH* DIRECTION
-----------------------------------------------------------
Every other provider returns its own raw payload shape and the gateway
normalizes it, because a provider shape is the provider's business and the
platform's shape is the platform's. On the push side the two are already the
same object: what arrives is a :class:`~services.market_engine.ticks.MarketTick`,
the platform's own canonical tick, produced at the feed's own adapter boundary.

That is also why :meth:`StreamingTickProvider.on_raw` is strict about unknown
keys. Accepting a record with a field `MarketTick` does not define would mean
something upstream sent a *feed-shaped* payload rather than a canonical one, and
silently dropping the extra key would let that go unnoticed until the day the
extra key was the only identifier the record had. Rejecting it is what makes
"no raw feed payload reaches the Market Engine" a property of this boundary
instead of a habit of its callers.

The *pull* direction is different and does have a family: see
:meth:`StreamingTickProvider.fetch_quote` and `normalizer.py`'s `canonical`
family, which turns one canonical tick into the platform's StockQuote shape.

═══════════════════════════════════════════════════════════════════════
D4.5 — THE READINESS GATE, AND WHY QUOTES IS NOW DECLARED
═══════════════════════════════════════════════════════════════════════

D4.4 shipped this provider declaring `TICKS` and deliberately **not** `QUOTES`.
Its reasoning, quoted from that sprint, is still the reasoning here:

    Declaring QUOTES would make this provider outrank the polled baseline
    (priority 1 vs 3) for every quote request its owner makes, the moment it
    registered — which is the feed *switch*, performed without the
    make-before-break gate MARKET_DATA_ARCHITECTURE.md requires: connect the
    new provider, confirm first valid data, *then* release the old one.

D4.5 builds that gate, so the capability can be declared. The invariant it
enforces is the one that matters:

    **A provider does not become the primary quote source by declaring QUOTES.
    It becomes eligible by proving it can produce valid canonical data, and it
    stops being eligible the instant that stops being true.**

Declaring a capability and being resolvable for it are two different things, and
this class is where they come apart. `capabilities` says what this provider
could serve; :meth:`is_eligible_for` says whether it may serve it *now*.

THE STATE MODEL
---------------
Three distinctions, in increasing strength — the CONNECTED != READY != PRIMARY
line D4.5 exists to draw::

    REGISTERED   constructed; nothing has been established
    CONNECTING   `connect()` entered — a session is being established
    CONNECTED    a session exists.  NOT evidence that data will ever arrive.
    SUBSCRIBED   instruments have been requested.  Still not evidence.
    READY        a valid canonical tick has actually arrived on this link.
    FAILED       the link reported failure; evidence discarded
    DISCONNECTED the link is down; evidence discarded

**PRIMARY is deliberately not a state on this class.** Being primary is the
*outcome* of one resolution in the Source Manager — the head of the chain for a
given capability and context — and storing it here would create a second,
lagging copy of a fact the resolver already computes. Two providers could then
both believe they were primary, which is exactly the state
MARKET_DATA_ARCHITECTURE.md forbids for one quote stream. With promotion
expressed purely as "which provider does `resolve_feed` return", it cannot
happen: there is one function, it returns one head, and it recomputes from
current readiness every time. That is what makes promotion atomic without a
lock, a transaction, or a handover protocol.

WHAT COUNTS AS EVIDENCE
-----------------------
Exactly one thing: a record that survived :meth:`_coerce` into a canonical
:class:`MarketTick`, accepted while the link was up and instruments were
subscribed. Not "the socket opened", not "authentication succeeded", not "a
subscribe frame was sent", and not the passage of time — a sleep-based gate
would promote a feed that connected and then said nothing at all, which is the
precise failure mode a make-before-break switch exists to prevent.

Evidence is *per link*. A reconnect discards it: a feed that ticked once, died,
and came back has proved nothing about the new connection, and promoting it on
the strength of the old one would be promoting on a memory.

PER-SYMBOL COVERAGE, AND WHY A QUOTE NEEDS MORE THAN READINESS
---------------------------------------------------------------
Readiness makes the feed eligible for the quote capability at all. Coverage
decides *which instruments* it may answer for, and it is the mechanism behind
MARKET_DATA_ARCHITECTURE.md's rule that a feed covering NSE equities does not
disqualify the baseline from serving a US index that feed does not carry.

A quote needs a price, so the feed may answer for a symbol only if it holds a
recent tick for it. Two consequences, both wanted:

  * the baseline keeps serving every instrument the feed does not stream, in the
    same request, with no caller aware that two providers were involved;
  * a feed whose ticks stop being delivered stops covering anything within
    :data:`DEFAULT_TICK_MAX_AGE_SECONDS` and the baseline resumes — a backstop
    beneath the explicit link-down signal, evaluated lazily at resolution time
    with no timer and no poll loop.

WHAT A TICK-DERIVED QUOTE DOES AND DOES NOT CARRY
--------------------------------------------------
A canonical tick carries symbol, exchange, price, volume and an ingest
timestamp. It carries no previous close, so a quote derived from it has no
`change` / `change_pct`, and no OHLC. That is honest rather than convenient:
the platform states what the feed actually sent and fabricates nothing, which
CLAUDE.md's data rules require. Recorded as a known limitation in TASK.md — the
canonical tick grows those fields when a real feed that populates them lands,
not before, because a contract nothing populates is worse than an absent one.
"""

from __future__ import annotations

import logging
import time
from dataclasses import fields as dataclass_fields
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from services.market_engine.providers.base import (
    Capability,
    MarketDataProvider,
    ProviderKind,
    ResolutionContext,
    SourceTier,
)
from services.market_engine.ticks import MarketTick, MarketTickError

logger = logging.getLogger(__name__)

#: Priority 1 in the Provider Priority Algorithm — above a licensed exchange
#: feed (2) and the polled baseline (3). A pushed feed is the freshest data the
#: platform can obtain, so it leads; the polled baseline remains the permanent
#: floor beneath it.
#:
#: Priority alone never promotes anything: a provider outranks the baseline only
#: among the candidates that survived eligibility, and this provider is not a
#: candidate for a quote until the readiness gate below has opened.
STREAMING_FEED_PRIORITY = 1

#: The exact field set a canonical tick record may carry. Read off the dataclass
#: rather than written out, so a field added to the canonical tick cannot be
#: rejected here by an out-of-date literal.
TICK_FIELDS = frozenset(f.name for f in dataclass_fields(MarketTick))

#: How old the newest tick for a symbol may be before this feed stops answering
#: quotes for it.
#:
#: The number is a judgement, and the direction of the judgement is what matters:
#: the baseline it falls back to is 15–60 seconds delayed, so answering from a
#: tick older than that is strictly worse than falling back, *and* it would be
#: labelled `streaming` while being older than the delayed tier. Two minutes
#: leaves room for a genuinely illiquid instrument that simply has not traded,
#: while keeping a silently dead link from serving yesterday's price as live.
DEFAULT_TICK_MAX_AGE_SECONDS = 120.0


class FeedReadiness(str, Enum):
    """How far a pushed feed has got towards being usable.

    See the module docstring for what separates the members and why PRIMARY is
    not one of them.
    """

    REGISTERED = "registered"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    SUBSCRIBED = "subscribed"
    READY = "ready"
    FAILED = "failed"
    DISCONNECTED = "disconnected"


#: States in which the feed's link is considered up. `READY` is a strict
#: refinement of `SUBSCRIBED`, which is a strict refinement of `CONNECTED` — the
#: whole point of the enum — so link-level questions ask this set rather than
#: comparing to a single member and silently excluding the stronger states.
LINK_UP_STATES = frozenset({
    FeedReadiness.CONNECTED,
    FeedReadiness.SUBSCRIBED,
    FeedReadiness.READY,
})

#: Capabilities served by pushing. A feed is a legitimate answer for these while
#: its link is up, because nothing is *fetched* for them: the question they
#: answer is "is a live stream attached to this request", and a stream that has
#: not ticked yet is still attached. The quote capability is the one that
#: displaces the baseline, and it is gated on readiness instead.
LINK_LEVEL_CAPABILITIES = frozenset({Capability.TICKS, Capability.DEPTH})

#: Called with (provider, previous_state, new_state) on every readiness
#: transition. Bound by the Market Gateway at registration, for the same reason
#: the tick sink is: a provider that chose its own listener could announce a
#: promotion around the gateway.
ReadinessListener = Callable[["StreamingTickProvider", FeedReadiness, FeedReadiness], Awaitable[None]]


class StreamingTickProvider(MarketDataProvider):
    """A market-data provider whose data is pushed into it as canonical ticks.

    One instance per feed connection. For a per-user feed that means one per
    (owner, feed) pair, with `owner_user_id` set — which is what makes
    :meth:`MarketDataProvider.is_eligible_for` refuse to serve it to anybody
    else, by construction rather than by every call site remembering to check.
    """

    kind = ProviderKind.STREAMING
    tier = SourceTier.STREAMING
    #: QUOTES is declared, and declaring it grants nothing on its own — see the
    #: readiness section of the module docstring. TICKS is what the feed serves
    #: from the moment its link is up.
    capabilities = frozenset({Capability.TICKS, Capability.QUOTES})
    normalizer_key = "canonical"
    priority = STREAMING_FEED_PRIORITY

    def __init__(
        self,
        name: str,
        *,
        owner_user_id: Optional[str] = None,
        priority: int = STREAMING_FEED_PRIORITY,
        tick_max_age_seconds: float = DEFAULT_TICK_MAX_AGE_SECONDS,
    ) -> None:
        super().__init__()
        name = (name or "").strip()
        if not name:
            raise ValueError("a streaming provider needs a stable name")
        self.name = name
        self.owner_user_id = str(owner_user_id) if owner_user_id else None
        self.priority = priority
        self.tick_max_age_seconds = float(tick_max_age_seconds)
        self._accepted = 0
        self._rejected = 0
        self._readiness = FeedReadiness.REGISTERED
        #: Newest tick per canonical symbol, with the monotonic instant it
        #: arrived. Bounded by the subscribed instrument set, which the owner
        #: chose — this is not an unbounded cache of everything ever seen.
        self._last_tick: Dict[str, Tuple[MarketTick, float]] = {}
        self._readiness_listener: Optional[ReadinessListener] = None
        self._last_failure: str = ""

    # ── Readiness ────────────────────────────────────────

    @property
    def readiness(self) -> FeedReadiness:
        return self._readiness

    @property
    def is_link_up(self) -> bool:
        """Whether a session exists. NOT a statement that data is arriving."""
        return self._connected and self._readiness in LINK_UP_STATES

    @property
    def is_ready(self) -> bool:
        """Whether this feed has proved it can produce valid canonical data.

        The gate. Distinct from health, which is evidence accumulated from past
        *calls*, and distinct from connectedness, which is evidence of nothing
        at all: a feed that authenticated, subscribed and then went silent is
        connected, healthy by every counter the platform keeps, and cannot
        serve a single price.
        """
        return self._connected and self._readiness is FeedReadiness.READY

    def bind_readiness_listener(self, listener: Optional[ReadinessListener]) -> None:
        """Point this feed's readiness transitions at the Market Gateway.

        Bound at registration and cleared at unregistration, exactly like
        :meth:`MarketDataProvider.bind_sink`. A promotion or demotion is a
        platform-level event, and announcing it is the gateway's job — Developer
        Rule 2 forbids anything reaching consumers around the gateway, and that
        applies to a status change as much as to a price.
        """
        self._readiness_listener = listener

    async def _advance(self, state: FeedReadiness, *, reason: str = "") -> bool:
        """Move to `state`, notifying the listener when it actually changed.

        Returns True on a real transition. Idempotent by design: a feed that
        reports its link up twice, or delivers a second tick after readiness,
        must not produce a second promotion — repeated lifecycle events are
        normal on a reconnecting transport and a duplicate promotion would put
        two identical status events on the bus for one fact.
        """
        if state is self._readiness:
            return False
        previous, self._readiness = self._readiness, state
        self._last_failure = reason if state is FeedReadiness.FAILED else ""
        logger.info(
            "Streaming provider %s readiness %s -> %s%s",
            self.name, previous.value, state.value,
            f" ({reason})" if reason else "",
        )
        listener = self._readiness_listener
        if listener is not None:
            await listener(self, previous, state)
        return True

    def _discard_evidence(self) -> None:
        """Forget every tick this link produced.

        Called whenever the link drops. Readiness is evidence about *this*
        connection, and prices from a connection that no longer exists must not
        answer a quote — nor let a reconnected feed skip the gate on the
        strength of what the previous one sent.
        """
        self._last_tick.clear()

    # ── Lifecycle ────────────────────────────────────────

    async def connect(self) -> None:
        """Establish the platform-side session for this feed.

        Idempotent. The transport itself lives with whoever owns the feed; what
        happens here is that the provider becomes willing to receive. It is
        explicitly *not* readiness — see the module docstring.
        """
        if self.is_ready:
            # Idempotent in the strong sense: re-establishing a session that is
            # already delivering must not walk the gate backwards and demote a
            # feed that is serving perfectly well.
            return
        await self._advance(FeedReadiness.CONNECTING)
        await super().connect()
        await self._advance(
            FeedReadiness.SUBSCRIBED if self._subscribed else FeedReadiness.CONNECTED
        )

    async def disconnect(self) -> None:
        """Tear the session down. Idempotent."""
        await super().disconnect()
        self._discard_evidence()
        await self._advance(FeedReadiness.DISCONNECTED)

    async def mark_link_down(self, reason: str = "") -> bool:
        """The feed's transport reports its connection lost.

        The demotion half of make-before-break, and the reason failover here
        needs no polling: the side that owns the socket already knows the moment
        it dies, so it says so, and the next resolution — the very next one —
        ranks the baseline first again. Nothing waits for a health counter to
        escalate and nothing checks on a timer.

        Distinct from :meth:`disconnect` because the provider stays *registered*:
        a dropped socket that is reconnecting is not an ended entitlement, and
        unregistering on every blip would churn the registry and throw away the
        feed's diagnostics. It becomes un-resolvable, not absent.
        """
        self._discard_evidence()
        return await self._advance(
            FeedReadiness.FAILED if reason else FeedReadiness.DISCONNECTED,
            reason=reason,
        )

    async def mark_link_up(self) -> bool:
        """The feed's transport reports its connection established (or re-established).

        Never promotes: it moves the feed no further than CONNECTED/SUBSCRIBED,
        because "the socket is open" is the single most tempting and most wrong
        readiness signal there is. Readiness is re-earned by the next valid tick.
        """
        if not self._connected:
            await super().connect()
        self._discard_evidence()
        # A feed already in READY is demoted back to SUBSCRIBED here, not left
        # alone: a link that came back is a *new* link, its predecessor's
        # evidence has just been discarded, and readiness must be re-earned on
        # the connection that actually exists.
        return await self._advance(
            FeedReadiness.SUBSCRIBED if self._subscribed else FeedReadiness.CONNECTED
        )

    # ── Subscription ─────────────────────────────────────

    async def subscribe(self, symbols: Iterable[str]) -> Tuple[str, ...]:
        """Request instruments, advancing the gate from CONNECTED to SUBSCRIBED.

        A feed with no subscription can never become ready, which is the correct
        default rather than an oversight: the owner of the feed is the only
        party that knows what it asked the wire for, so a feed that never said
        cannot claim to be delivering it.
        """
        active = await super().subscribe(symbols)
        if active and self._readiness is FeedReadiness.CONNECTED:
            await self._advance(FeedReadiness.SUBSCRIBED)
        return active

    async def unsubscribe(self, symbols: Iterable[str]) -> Tuple[str, ...]:
        remaining = await super().unsubscribe(symbols)
        for symbol in set(self._last_tick) - set(remaining):
            self._last_tick.pop(symbol, None)
        if not remaining and self._readiness in (FeedReadiness.SUBSCRIBED, FeedReadiness.READY):
            await self._advance(FeedReadiness.CONNECTED)
        return remaining

    # ── Entitlement, readiness and coverage ──────────────

    def is_eligible_for(self, context: ResolutionContext) -> bool:
        """Whether this feed may serve `context` — the whole gate, in one place.

        Three filters, strongest last:

        1. **Entitlement.** A per-user feed is legally that user's own data
           (MARKET_DATA_ARCHITECTURE.md, Category 2) and may never be resolved
           for anybody else. Inherited unchanged from the base class, so a
           failure of the switching logic cannot widen it.
        2. **Link, for the pushed capabilities.** TICKS and DEPTH are answered
           by a stream existing, and a stream with a live link exists.
        3. **Readiness and coverage, for everything else** — which today means
           the quote capability, the one that displaces the baseline. The feed
           must have proved itself on this link *and* hold a recent price for
           the instrument being asked about.

        A context with no capability (diagnostics, `entitled_for`) is treated as
        the link-level question: it is asking whether this feed applies to the
        request at all, not who wins it.
        """
        if not super().is_eligible_for(context):
            return False
        if context.capability is None or context.capability in LINK_LEVEL_CAPABILITIES:
            return self.is_link_up
        if not self.is_ready:
            return False
        if context.symbol:
            return self.covers(context.symbol)
        # A capability-scoped request with no instrument is not a request for
        # data — every fetch path in the gateway supplies the symbol it is
        # asking about. What reaches here with no symbol is the *reporting*
        # question: `status()`, `active_tier()`, `diagnostics()` asking which
        # feed this user is on. A ready feed is the honest answer to that, and
        # answering `False` would report the baseline's tier to a user whose
        # data is genuinely arriving live.
        #
        # Per-instrument truth is not lost by this: every quote that actually
        # leaves the gateway is stamped with the tier of the provider that
        # answered *it*, so an instrument the feed does not stream is still
        # labelled delayed on the payload the consumer receives.
        return True

    def covers(self, symbol: Any) -> bool:
        """Whether this feed holds a usable, recent price for `symbol`."""
        return self._fresh_tick(symbol) is not None

    @property
    def covered_symbols(self) -> Tuple[str, ...]:
        """Symbols this feed can currently answer a quote for."""
        return tuple(sorted(s for s in self._last_tick if self._fresh_tick(s) is not None))

    def _fresh_tick(self, symbol: Any) -> Optional[MarketTick]:
        if not self.is_ready:
            return None
        key = str(symbol or "").strip().upper()
        entry = self._last_tick.get(key)
        if entry is None:
            return None
        tick, arrived_at = entry
        if (time.monotonic() - arrived_at) > self.tick_max_age_seconds:
            return None
        return tick

    # ── Pull surface ─────────────────────────────────────

    async def fetch_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """The last streamed price for `symbol`, in this provider's raw shape.

        Raw here *is* the canonical tick — `normalizer.py`'s `canonical` family
        turns it into the platform's StockQuote. Nothing is computed, inferred
        or filled in: the fields a tick does not carry stay absent rather than
        being reconstructed from a stale reference (see the module docstring).

        Raises when the feed cannot answer, rather than returning nothing.
        Resolution filters this provider out for a symbol it does not cover, so
        reaching here means a caller bypassed resolution — and an empty return
        would end the request with no quote at all, because the gateway
        advances its failover chain on an exception and deliberately not on an
        empty result. Raising is what hands the request to the baseline.
        """
        tick = self._fresh_tick(symbol)
        if tick is None:
            raise self._unsupported(Capability.QUOTES)
        return tick.as_dict()

    # ── Push surface ─────────────────────────────────────

    async def on_raw(self, payload: Any) -> int:
        """Accept one pushed payload — a canonical tick, or a batch of them.

        Returns how many records were accepted. Nothing raises: a feed frame is
        a batch, and one unusable record must not cost the rest of the batch
        their prices nor drop a live connection. That is the same discipline the
        canonical boundary one layer down already applies, for the same reason.

        A batch that yields nothing usable emits nothing at all, rather than an
        empty delivery. The gateway then has one shape for "nothing arrived"
        instead of two.

        This is also the only place readiness is *earned* (D4.5). A batch in
        which every record was rejected is not evidence — a feed delivering a
        shape this boundary does not recognise has demonstrated the opposite of
        readiness — so the gate stays shut and the baseline keeps the quote.
        """
        records = payload if isinstance(payload, (list, tuple)) else [payload]
        ticks: List[MarketTick] = []
        rejected = 0

        for record in records:
            try:
                ticks.append(self._coerce(record))
            except MarketTickError as exc:
                rejected += 1
                logger.warning("Provider %s rejected a pushed record: %s", self.name, exc)

        self._accepted += len(ticks)
        self._rejected += rejected

        if not ticks:
            if rejected:
                # Loud, because a feed whose every record is rejected looks
                # exactly like a quiet market from outside and means the
                # opposite.
                logger.error(
                    "Provider %s accepted none of %d pushed records — the feed is delivering "
                    "a shape this boundary does not recognise",
                    self.name,
                    rejected,
                )
            return 0

        arrived_at = time.monotonic()
        for tick in ticks:
            self._last_tick[tick.symbol] = (tick, arrived_at)

        await self._earn_readiness()
        await self._emit(ticks)
        return len(ticks)

    async def _earn_readiness(self) -> None:
        """Promote to READY on valid data, if the link and subscription allow it.

        The promotion is one assignment and it is atomic with respect to
        resolution for the same reason the demotion is: the Source Manager reads
        readiness at resolve time and never caches it, so the request before
        this transition resolves to the baseline and the request after resolves
        to this feed, with no interval in which both are the primary quote
        source.

        A feed whose link is down, or which never declared a subscription, is
        not promoted however good its data looks — a record arriving on a dead
        link is a record from a link the platform cannot ask anything of.
        """
        if self._readiness is FeedReadiness.SUBSCRIBED and self._connected:
            await self._advance(FeedReadiness.READY)

    def _coerce(self, record: Any) -> MarketTick:
        """One pushed record → a canonical tick, or :class:`MarketTickError`.

        Already-canonical instances pass through; dicts are rebuilt field by
        field from the closed set. An unrecognised field is refused rather than
        dropped — see the module docstring.
        """
        if isinstance(record, MarketTick):
            return record
        if not isinstance(record, dict):
            raise MarketTickError(f"pushed record is {type(record).__name__}, not a canonical tick")

        unknown = sorted(set(record) - TICK_FIELDS)
        if unknown:
            raise MarketTickError(
                f"pushed record carries non-canonical field(s) {unknown} — "
                "only canonical market ticks may cross this boundary"
            )

        ingested_at = record.get("ingested_at")
        fields: Dict[str, Any] = {
            "symbol": record.get("symbol"),
            "price": record.get("price"),
            "exchange": record.get("exchange"),
            "volume": record.get("volume"),
        }
        if isinstance(ingested_at, str) and ingested_at.strip():
            fields["ingested_at"] = ingested_at
        try:
            return MarketTick(**fields)
        except MarketTickError:
            raise
        except (TypeError, ValueError) as exc:
            raise MarketTickError(f"pushed record is not a usable tick: {exc}")

    # ── Introspection ────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        return {
            **super().describe(),
            "accepted_records": self._accepted,
            "rejected_records": self._rejected,
            "readiness": self._readiness.value,
            "covered_symbols": len(self.covered_symbols),
            "last_failure": self._last_failure,
        }

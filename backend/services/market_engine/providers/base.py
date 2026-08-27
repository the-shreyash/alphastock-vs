"""Provider Adapter contract — the only place provider-specific code may live.

MARKET_DATA_ARCHITECTURE.md is authoritative for this boundary. Its Developer
Rule 9 states the whole point of this module: adding a provider must be

    new adapter + new normalizer + registry entry. Nothing else.

If a future provider forces a change to the Market Engine, the Event Bus, the AI
layer, or the frontend, the design has been breached and the breach — not the
symptom — is what gets fixed.

WHAT AN ADAPTER IS
------------------
An adapter speaks exactly one provider's protocol and returns that provider's
*raw* payload shape. It does not normalize, does not cache into the platform
cache, does not touch the Event Bus, Redis, the database, or the Market Engine,
and contains no business logic (no scanner rules, no P&L maths, no alerting).
The Market Gateway owns all of that, which keeps normalization in one testable
place instead of one per provider.

WHY THE FETCH SURFACE IS CAPABILITY-GATED
-----------------------------------------
Providers differ enormously in coverage: Yahoo carries global indices and
commodities but no order-book depth; a broker WebSocket carries tick-level
depth for NSE/BSE instruments but knows nothing about the FTSE. Rather than
force every adapter to implement every method — which produces a wall of stub
methods that lie about what a provider can do — each adapter *declares* its
capabilities and implements only those. Every unimplemented method raises
:class:`CapabilityUnavailable`, and the Source Manager filters candidates by
capability before ever calling one (MARKET_DATA_ARCHITECTURE.md, "Resolution
procedure", step 3). A provider that cannot serve a symbol universe simply
falls through to the next one for that universe.

THE PUSH SURFACE (D4.4)
-----------------------
D1 deliberately omitted `subscribe` / `unsubscribe` / `on_raw` — it shipped one
request/response provider and no consumer able to receive a pushed tick, so the
surface would have been plumbing nothing implemented and nothing called (ADR-028).
D4.4 lands it, together with the first provider that pushes and the gateway sink
that receives.

The shape is the one MARKET_DATA_ARCHITECTURE.md specifies, with one deliberate
asymmetry: `subscribe` / `unsubscribe` are *pull-direction* calls the platform
makes on a provider, and are meaningful for both families (a polling provider
adds a symbol to its poll set, a streaming provider sends a subscribe frame), so
they live on the base class and default to bookkeeping only. `on_raw` is the
*push-direction* call the provider makes on the platform, is meaningless for a
provider that cannot push, and therefore defaults to raising — a polling
provider that somehow gets a payload pushed into it fails loudly instead of
delivering data through a path that was never designed to carry it.

WHAT MAKES A PROVIDER LEGITIMATELY "STREAMING" (D4.4)
-----------------------------------------------------
Three declarations that must agree, enforced by
:meth:`ProviderRegistry.register` rather than by review:

    a push capability (TICKS / DEPTH)  ⟺  kind=STREAMING  ⟹  on_raw() overridden
    tier=STREAMING                     ⟹  kind=STREAMING

The implication that matters is the second one. `tier` is what the AI calibrates
its language against and what the UI renders as "Live"; `kind` is how the data
physically arrives. A provider that polls on a timer and declares
`tier=STREAMING` would have the platform tell a user a 30-second-old number is
live — polling disguised as streaming, which CLAUDE.md's data rules forbid
outright. Registration rejects it. See ADR-034.
"""
from __future__ import annotations

import logging
from abc import ABC
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SourceTier(str, Enum):
    """Freshness tier — the ONLY provenance any consumer below the Source
    Manager may see.

    MARKET_DATA_ARCHITECTURE.md deliberately replaces the provider name with
    this value on every event that leaves the gateway: the frontend may render
    "Live" or "Delayed", and the AI may calibrate its language ("live price" vs
    "as of 10:42 AM"), but neither may ever learn *who* produced a quote. The
    real provider id survives only in gateway logs and internal metrics.
    """

    STREAMING = "streaming"
    DELAYED = "delayed"


class ProviderKind(str, Enum):
    """How data physically arrives from the provider.

    Distinct from :class:`SourceTier`, which is what consumers are told. A
    licensed exchange feed and a broker WebSocket are both STREAMING/streaming,
    but the distinction matters *inside* the gateway: polling providers are
    driven by a poll loop and have no push surface, streaming providers hold a
    persistent connection whose silence is itself a health signal.
    """

    POLLING = "polling"
    STREAMING = "streaming"


class Capability(str, Enum):
    """A unit of market data a provider can serve.

    Each member corresponds 1:1 to a `fetch_*` method on
    :class:`MarketDataProvider`, so the Source Manager can resolve a provider
    for a request without knowing anything about the provider itself.
    """

    QUOTES = "quotes"                    # single-instrument quote
    UNIVERSE_QUOTES = "universe_quotes"  # batched quotes for the tracked universe
    INDICES = "indices"                  # index levels + market overview
    SECTORS = "sectors"                  # sector performance
    MOVERS = "movers"                    # gainers / losers
    GLOBAL_MARKETS = "global_markets"    # non-Indian indices
    COMMODITIES = "commodities"          # commodities + forex
    OHLC = "ohlc"                        # candles / chart series
    SEARCH = "search"                    # instrument search

    # Declared but not served by any D1 provider. Present because the Source
    # Manager must be able to *resolve nothing* for them rather than have call
    # sites invent a provider — a broker adapter (D3) fills them in.
    TICKS = "ticks"
    DEPTH = "depth"


class ProviderState(str, Enum):
    """Health state of a single provider connection.

    UNKNOWN   registered but never yet exercised — no evidence either way
    UP        serving normally
    DEGRADED  failing intermittently — still a candidate, ranked lower
    DOWN      failing consistently — filtered out of resolution entirely

    WHY `UNKNOWN` EXISTS (D2)
    -------------------------
    D1 started every provider at UP, which asserted health the provider had
    never demonstrated: a broker adapter registered one millisecond ago and a
    baseline that has served ten thousand clean requests reported identically,
    on a diagnostics surface whose entire job is to tell an operator which
    providers are actually working. UNKNOWN is the honest initial value and it
    is the state D3's probation window starts from — a recovered provider
    re-enters probation as UNKNOWN rather than claiming UP on one success.

    For *selection* (D2) UNKNOWN ranks alongside UP, not below it. This is
    deliberate and load-bearing: ranking it below UP would mean a freshly
    registered priority-1 broker feed could never overtake a healthy priority-3
    baseline, because it can only leave UNKNOWN by being called and it can only
    be called by being selected. See `HEALTH_RANK` in `source_manager.py`.
    """

    UNKNOWN = "unknown"
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True)
class ResolutionContext:
    """Everything the Source Manager is allowed to know about a request.

    WHY THIS REPLACED THE BARE `user_id` PARAMETER (D2)
    ---------------------------------------------------
    D1 threaded an `Optional[str] user_id` through every resolution entry point
    and ignored it, as a seam for per-user selection. D2 makes the seam real,
    and the moment it is real a bare string stops being enough: entitlement is
    per user, but coverage is per *instrument* — MARKET_DATA_ARCHITECTURE.md's
    priority rules are explicit that "a broker feed covering NSE equities does
    not disqualify Yahoo from serving a US index the broker doesn't carry", and
    that decision needs the symbol and the exchange, not the user.

    Bundling them into one frozen object now means D3's broker adapter adds a
    field here instead of adding a fifth keyword argument to eleven gateway
    methods, eleven Source Manager methods, and every call site of both.

    Every field is optional. A context with nothing set is the honest
    representation of a platform-wide read with no user attached — a scheduled
    universe refresh, a scanner sweep, a warm-up job — and resolves to the
    globally entitled providers, which is exactly right.
    """

    #: Whose request this is. `None` = a platform-level read with no user.
    user_id: Optional[str] = None

    #: Instrument being requested, in StockAssist's internal symbol convention.
    #: Reserved for per-symbol coverage filtering (D3); unused in D2 because no
    #: registered provider has partial coverage to filter on.
    symbol: Optional[str] = None

    #: Venue (`NSE`, `BSE`, …). Same status as `symbol`.
    exchange: Optional[str] = None

    #: What is being resolved (D4.5). Set by
    #: :meth:`ProviderRegistry.candidates_for`, never by a caller, so a provider
    #: reading it always sees the capability it is actually being considered for.
    #:
    #: WHY IT LIVES HERE RATHER THAN AS A SECOND ARGUMENT
    #: --------------------------------------------------
    #: `is_eligible_for` is the documented extension point for per-provider
    #: eligibility rules, and D4.5 needs one that differs *by capability*: a
    #: pushed feed is a legitimate answer to "is a live tick stream attached to
    #: this user" the moment its connection is up, and is NOT a legitimate answer
    #: to "who serves this user's quotes" until it has proved it can produce
    #: valid data. Two different questions, one method — so the question has to
    #: travel with the context. Adding a parameter instead would change the
    #: signature of every override and every call site of a method whose whole
    #: purpose is to be overridden, which is the churn this dataclass exists to
    #: avoid.
    capability: Optional[Capability] = None

    def for_capability(self, capability: Optional[Capability]) -> "ResolutionContext":
        """This context, scoped to `capability`. Returns `self` when unchanged."""
        if capability is self.capability:
            return self
        return ResolutionContext(
            user_id=self.user_id,
            symbol=self.symbol,
            exchange=self.exchange,
            capability=capability,
        )

    @classmethod
    def for_user(cls, user_id: Optional[str]) -> "ResolutionContext":
        """Build a context from a bare user id.

        The compatibility shim for the `user_id=` keyword D1 put on every
        gateway method and that call sites outside the Market Engine still
        pass. Keeping it as a named constructor rather than overloading the
        resolve signature keeps exactly one code path inside the resolver.
        """
        return cls(user_id=user_id)


#: The context used when a caller supplies none. Module-level and frozen so the
#: common path allocates nothing.
GLOBAL_CONTEXT = ResolutionContext()


class CapabilityUnavailable(RuntimeError):
    """Raised when a provider is asked for data it never declared it could serve.

    This is a programming error, not a runtime condition: the Source Manager
    filters by capability before resolving, so reaching this means a call site
    bypassed resolution and reached for an adapter directly.
    """


class ProviderContractError(RuntimeError):
    """Raised when an adapter's declarations contradict each other (D4.4).

    Registration-time only, and fatal on purpose. Every condition it reports is
    a statement the adapter makes about itself that cannot be true — a delayed
    poll loop claiming the streaming tier, a streaming provider with nothing to
    push, a push capability with no `on_raw` to receive on. None of them can be
    detected later by observing behaviour: they all produce a provider that
    registers cleanly, resolves cleanly, and serves either nothing or a lie.
    """


#: Capabilities that can only be served by pushing — a feed delivers them, a
#: request/response call cannot. Declaring one is what commits an adapter to the
#: streaming contract checked at registration.
#:
#: DEPTH is here alongside TICKS although no provider serves it yet: order-book
#: depth is a stream by nature, and leaving it out would let the first depth
#: provider register as a polling adapter and quietly poll an order book.
PUSH_CAPABILITIES = frozenset({Capability.TICKS, Capability.DEPTH})


#: Consecutive failures before a provider is considered degraded, then down.
#: Two thresholds rather than one because a single blip must not cost a provider
#: its primary slot — MARKET_DATA_ARCHITECTURE.md's flap-suppression concern in
#: its simplest useful form. Full latency scoring and probation windows are
#: Phase 5 of that document; D1 needs only "stop asking a provider that is
#: consistently answering with errors".
DEGRADED_AFTER_FAILURES = 3
DOWN_AFTER_FAILURES = 8


@dataclass
class ProviderHealth:
    """Rolling health of one provider, owned by the adapter, read by the
    Source Manager.

    Deliberately counter-based rather than time-window based: a time window
    needs a clock source, a background sweeper, and tests that manipulate time,
    all to answer a question ("is this provider answering?") that consecutive
    failure counts already answer correctly for a polled provider.
    """

    state: ProviderState = ProviderState.UNKNOWN
    consecutive_failures: int = 0
    total_calls: int = 0
    total_errors: int = 0
    total_empty: int = 0
    last_success_at: Optional[str] = None
    last_error_at: Optional[str] = None
    last_error_class: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "total_calls": self.total_calls,
            "total_errors": self.total_errors,
            "total_empty": self.total_empty,
            "last_success_at": self.last_success_at,
            "last_error_at": self.last_error_at,
            "last_error_class": self.last_error_class,
        }


class MarketDataProvider(ABC):
    """Base class every market-data provider adapter implements.

    Subclasses set :attr:`name`, :attr:`kind`, :attr:`tier`,
    :attr:`capabilities` and :attr:`normalizer_key`, then override only the
    `fetch_*` methods matching the capabilities they declared.
    """

    #: Stable provider identifier — "yahoo", "zerodha", "nse_licensed", …
    #: Appears in gateway logs and the registry ONLY. It must never reach a
    #: normalized event, an API response, the AI, or the frontend.
    name: str = "unnamed"

    #: How data arrives (poll loop vs persistent connection).
    kind: ProviderKind = ProviderKind.POLLING

    #: What consumers are told about freshness. Set honestly: labelling a
    #: 15-minute-delayed poll as STREAMING would make the AI say "live price"
    #: about a stale number, which CLAUDE.md's data rules forbid outright.
    tier: SourceTier = SourceTier.DELAYED

    #: Capabilities this adapter actually serves.
    capabilities: frozenset = frozenset()

    #: Which normalizer family in `normalizer.py` understands this provider's
    #: raw payload shape. Separate from `name` so two providers sharing a wire
    #: format (e.g. two brokers on the same vendor API) can share a normalizer.
    normalizer_key: str = "unknown"

    #: Resolution priority — lower wins. Mirrors the Provider Priority
    #: Algorithm in MARKET_DATA_ARCHITECTURE.md:
    #:     1 connected broker WebSocket
    #:     2 licensed exchange feed
    #:     3 polled baseline (Yahoo) — the permanent floor
    #:
    #: Priority is provider *metadata*, declared here by the adapter that owns
    #: it, never a branch in application code. That is what keeps the tier
    #: ordering in one readable place instead of scattered across
    #: `if yahoo / elif zerodha / else upstox` chains.
    priority: int = 100

    #: Whose entitlement this provider is served under. `None` means a
    #: platform-wide entitlement — Yahoo, a licensed feed — available to every
    #: request. A user id binds the adapter to exactly that user.
    #:
    #: This is the Category 2 cornerstone of MARKET_DATA_ARCHITECTURE.md made
    #: enforceable: a broker feed is legally the *user's* data, consumed on
    #: their behalf with their own session, and must never serve anybody else.
    #: D3 registers one broker adapter per connected user with this set; D2
    #: builds the filter so that when it does, cross-user leakage is impossible
    #: by construction rather than by every future call site remembering to
    #: check.
    owner_user_id: Optional[str] = None

    def __init__(self) -> None:
        self._health = ProviderHealth()
        self._connected = False
        #: Symbols this provider has been asked to deliver, in request order.
        self._subscribed: Dict[str, None] = {}
        #: Where pushed payloads go. Set by the Market Gateway when the provider
        #: is registered, never by the provider itself — a provider that chose
        #: its own sink could deliver around the gateway, which Developer Rule 2
        #: forbids ("nothing may bypass the Market Gateway").
        self._sink: Optional[Callable[["MarketDataProvider", Any], Awaitable[None]]] = None

    # ── Lifecycle ────────────────────────────────────────

    async def connect(self) -> None:
        """Establish the provider session. Must be idempotent.

        A polling provider over a stateless HTTP API has nothing to establish,
        so the default is a no-op that simply records the connected flag. A
        streaming adapter overrides this to open its WebSocket.
        """
        self._connected = True

    async def disconnect(self) -> None:
        """Tear the session down. Must be idempotent."""
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_ready(self) -> bool:
        """Whether this provider can serve *right now*, as opposed to in principle.

        Separate from health, which is evidence accumulated from past calls. A
        streaming provider that has been registered but whose connection is not
        up has no failures to its name and is still unusable; readiness is how
        it says so without having to fail a request first to prove it.

        Polling providers over a stateless API are ready as soon as they exist,
        which is what the default expresses.
        """
        return True

    @property
    def is_on_probation(self) -> bool:
        """Whether this provider is usable but has not yet *proved* it is reliable.

        The READY != STABLE line (D5.2). Readiness says a provider can serve
        right now; probation says the platform has not yet seen enough of it to
        let it displace a provider that has been serving reliably. The Source
        Manager reads this as a ranking term, never as an eligibility filter:
        a probationary provider still answers when nothing steadier is left,
        because refusing to serve is strictly worse than serving from a feed
        that is merely young.

        The default is False, and it is a statement rather than a convenience.
        Probation is evidence about *one connection*: a provider with no link to
        lose — the polled baseline over a stateless HTTP API — has no
        per-connection reliability to demonstrate, and what can be said about
        its trustworthiness is already said by health. Only a provider that
        implements the readiness gate implements this with it.
        """
        return False

    # ── Push surface (D4.4) ──────────────────────────────
    #
    # MARKET_DATA_ARCHITECTURE.md's adapter contract, rule 5: "polling adapters
    # expose the same interface as streaming adapters ... the rest of the system
    # cannot distinguish the two". `subscribe` therefore lives here rather than
    # on a streaming subclass, and the default keeps the symbol set so a polling
    # adapter that wants a poll set gets one for free.

    async def subscribe(self, symbols: Iterable[str]) -> Tuple[str, ...]:
        """Begin delivering data for `symbols`. Returns the full active set.

        Idempotent and additive: re-subscribing a symbol already active is a
        no-op rather than a duplicate. Symbols are canonicalized on the way in
        (uppercase, trimmed) so the set is keyed the same way every other market
        surface keys instruments.
        """
        for symbol in symbols or ():
            key = str(symbol or "").strip().upper()
            if key:
                self._subscribed.setdefault(key, None)
        return self.subscribed_symbols

    async def unsubscribe(self, symbols: Iterable[str]) -> Tuple[str, ...]:
        """Stop delivering data for `symbols`. Returns the remaining active set."""
        for symbol in symbols or ():
            self._subscribed.pop(str(symbol or "").strip().upper(), None)
        return self.subscribed_symbols

    @property
    def subscribed_symbols(self) -> Tuple[str, ...]:
        return tuple(self._subscribed)

    def bind_sink(self, sink: Optional[Callable[["MarketDataProvider", Any], Awaitable[None]]]) -> None:
        """Point this provider's pushed output at the Market Gateway.

        Called by the gateway at registration and cleared at unregistration.
        A provider whose sink is None drops what it is handed rather than
        buffering it: an unbound provider is one nothing is listening to, and
        holding ticks for a listener that may never arrive is how a per-user
        feed becomes an unbounded per-user buffer.
        """
        self._sink = sink

    @property
    def has_sink(self) -> bool:
        return self._sink is not None

    async def on_raw(self, payload: Any) -> int:
        """Receive one pushed payload from this provider's own transport.

        The push direction of the contract, and the mirror image of `fetch_*`:
        those are gated by capability because a provider that cannot serve
        quotes must fail loudly rather than return nothing, and this is gated
        for the same reason. A provider with no push capability has no path
        designed to carry pushed data, so delivering it anyway would mean data
        entering the platform through an unreviewed route.

        Returns the number of records accepted, so a caller can tell "the feed
        is delivering" from "the feed is connected and everything it sends is
        being rejected" — two states that look identical from outside and mean
        opposite things.
        """
        raise self._unsupported(Capability.TICKS)

    async def _emit(self, payload: Any) -> None:
        """Hand one validated record to the gateway sink, if one is bound."""
        sink = self._sink
        if sink is None:
            return
        await sink(self, payload)

    # ── Health ───────────────────────────────────────────

    def health(self) -> ProviderHealth:
        """Current health. Read by the Source Manager during resolution."""
        return self._health

    def record_success(self, *, empty: bool = False) -> Optional[ProviderState]:
        """Record a successful call. Returns the new state if it changed.

        `empty` marks a call that succeeded at the transport level but returned
        nothing. It is counted separately and does NOT reset the failure streak
        on its own — a provider answering 200-with-no-data for every symbol is
        not healthy, and treating it as healthy is exactly how a silently empty
        feed keeps its primary slot.
        """
        self._health.total_calls += 1
        if empty:
            self._health.total_empty += 1
            return None

        self._health.last_success_at = _now_iso()
        self._health.consecutive_failures = 0
        return self._transition(ProviderState.UP)

    def record_failure(self, exc: BaseException) -> Optional[ProviderState]:
        """Record a failed call. Returns the new state if it changed."""
        self._health.total_calls += 1
        self._health.total_errors += 1
        self._health.consecutive_failures += 1
        self._health.last_error_at = _now_iso()
        self._health.last_error_class = type(exc).__name__

        failures = self._health.consecutive_failures
        if failures >= DOWN_AFTER_FAILURES:
            return self._transition(ProviderState.DOWN)
        if failures >= DEGRADED_AFTER_FAILURES:
            return self._transition(ProviderState.DEGRADED)
        return None

    def reset_health(self) -> None:
        """Drop all health state. Startup and tests only."""
        self._health = ProviderHealth()

    def _transition(self, state: ProviderState) -> Optional[ProviderState]:
        if self._health.state == state:
            return None
        previous = self._health.state
        self._health.state = state
        logger.info(
            "Provider %s health %s -> %s (consecutive_failures=%d)",
            self.name, previous.value, state.value,
            self._health.consecutive_failures,
        )
        return state

    # ── Capability-gated fetch surface ───────────────────
    #
    # Every method returns the provider's RAW payload shape. Normalization is
    # the gateway's job. Each default raises so that an adapter which declares
    # a capability but forgets to implement it fails loudly at the first call
    # instead of silently returning nothing — a provider that quietly serves
    # empty data is indistinguishable from a market with no movers.

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    # ── Entitlement ──────────────────────────────────────

    def is_eligible_for(self, context: ResolutionContext) -> bool:
        """Whether this provider may serve `context`.

        Step 1 of the Resolution procedure in MARKET_DATA_ARCHITECTURE.md —
        "build the candidate list: every provider whose entitlement applies to
        this user". Capability and health are separate filters applied
        alongside it (steps 2 and 3).

        The default implements entitlement scope alone, which is all any D2
        provider has to express. A broker adapter (D3) overrides this to add
        session liveness — a connected-but-unauthenticated broker is entitled
        and still not usable — and a partial-coverage feed overrides it to
        consult `context.symbol`. Overriding is the extension point precisely
        so that per-provider entitlement rules stay inside the provider module,
        where Developer Rule 1 requires provider-specific logic to live.
        """
        if self.owner_user_id is None:
            return True
        return context.user_id == self.owner_user_id

    def _unsupported(self, capability: Capability) -> CapabilityUnavailable:
        return CapabilityUnavailable(
            f"provider {self.name!r} does not serve capability {capability.value!r}"
        )

    async def fetch_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        raise self._unsupported(Capability.QUOTES)

    async def fetch_universe_quotes(self) -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.UNIVERSE_QUOTES)

    async def fetch_indices(self) -> Optional[Dict[str, Any]]:
        raise self._unsupported(Capability.INDICES)

    async def fetch_sectors(self) -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.SECTORS)

    async def fetch_gainers(self, count: int = 5) -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.MOVERS)

    async def fetch_losers(self, count: int = 5) -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.MOVERS)

    async def fetch_global_markets(self) -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.GLOBAL_MARKETS)

    async def fetch_commodities(self) -> Dict[str, Any]:
        raise self._unsupported(Capability.COMMODITIES)

    async def fetch_chart(self, symbol: str, period: str = "1D") -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.OHLC)

    async def search(self, query: str, limit: int = 10) -> Optional[List[Dict[str, Any]]]:
        raise self._unsupported(Capability.SEARCH)

    # ── Introspection ────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        """Diagnostic snapshot. Carries the provider name, so this is for
        admin/diagnostics surfaces and logs only — never for a market event,
        an AI context, or a live UI surface."""
        return {
            "name": self.name,
            "kind": self.kind.value,
            "tier": self.tier.value,
            "priority": self.priority,
            "connected": self._connected,
            "ready": self.is_ready,
            "on_probation": self.is_on_probation,
            "subscriptions": len(self._subscribed),
            "scope": "global" if self.owner_user_id is None else "user",
            "capabilities": sorted(c.value for c in self.capabilities),
            "health": self._health.as_dict(),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r} tier={self.tier.value}>"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

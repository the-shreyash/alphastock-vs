"""Market Gateway — the single choke point through which all market data enters.

Nothing may bypass this gateway (MARKET_DATA_ARCHITECTURE.md, Developer Rule 2).

Pipeline:

    Provider Adapter -> Gateway -> Normalize -> Validate -> Stamp tier
                                -> Event Bus -> Market Engine -> Frontend

WHAT D1 CHANGED HERE
--------------------
Before D1 this module imported `services.real_market` directly and passed a
hardcoded `provider="yahoo"` to the normalizer — which made the gateway itself
the platform's largest piece of Yahoo-specific code, and meant a second provider
could not be added without editing it. It now:

  * asks the Source Manager which provider serves each request, by capability;
  * calls that provider through the `MarketDataProvider` contract;
  * normalizes with the provider's own `normalizer_key` rather than a literal;
  * records the call's outcome against provider health, which is what makes
    failover automatic — a provider that fails consistently stops being
    resolved, and the tier below it takes over with no switching code;
  * stamps `source_tier` on normalized events so consumers learn freshness
    without ever learning provenance.

Yahoo Finance did not change role: it is still the baseline feed serving every
request. It simply sits behind the contract now.

WHAT D2 CHANGED HERE
--------------------
The gateway now asks the Source Manager for a *failover chain* rather than a
single provider, and walks it: if the preferred provider raises, the next
eligible one is tried inside the same request. D1 failed over only across
requests, once health counters had escalated, which left a window where the
platform returned nothing to users whose baseline feed was healthy throughout.

It also records an explicit unavailable state (`status["last_unavailable"]`,
carrying an `UnavailableReason` and no provider name) instead of returning a
bare empty default that a caller could not distinguish from "the provider
answered, there was nothing to report".

Resolution is now context-aware: methods that know an instrument supply it, so
per-user entitlement and per-symbol coverage are decided inside the Source
Manager and the provider adapters — never at a call site.

WHAT THIS GATEWAY IS NOT
------------------------
Not a cache (Redis and the Market Engine own caching — the provider client
caches its own HTTP reads). Not a business-rules engine (the Market Engine owns
processing). Not a fan-out layer (the Event Bus owns distribution).

Usage:
    from services.market_engine import market_gateway
    quote = await market_gateway.get_quote("RELIANCE")
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, TypeVar

from observability import instruments
from services.market_engine.event_bus import event_bus
from services.market_engine.normalizer import (
    normalize_stock_quote,
    normalize_index_quote,
    normalize_news_article,
    normalize_sector_data,
)
from services.market_engine.providers import (
    Capability,
    MarketDataProvider,
    ProviderContractError,
    ResolutionContext,
    YahooPollingAdapter,
    provider_registry,
)
from services.market_engine.ticks import MarketTick
from services.market_engine.source_manager import FeedChangeReason, source_manager
from services.market_engine.validator import (
    validate_stock_quote,
    validate_index_quote,
    validate_batch,
    validate_sector_data,
    is_market_hours,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Keys of the index sub-dicts inside a market overview payload, and the display
#: name each one carries. The provider returns them keyed by position with no
#: name of their own, and `validate_index_quote` rejects a nameless index — so
#: the gateway supplies the name at the normalization boundary. Without this the
#: normalization step silently never applied and every overview passed through
#: raw. See ADR-028.
#: Bus topic carrying canonical ticks out of the gateway (D4.4).
#:
#: `market.` rather than `tick.` deliberately: the event bridge routes by leading
#: domain segment and already maps `market` to the `market` socket channel, so a
#: tick lands where every other market event lands with no routing-table change.
TICK_TOPIC = "market.tick"

#: How many instruments `get_prices` resolves at once.
#:
#: Small on purpose — see the reasoning in `get_prices`. It is a failure-blast
#: bound first and a throughput knob second: eight in flight turns a 100-symbol
#: cold read from ~100 sequential round trips into ~13, while keeping the number
#: of requests that can hit an already-dead provider before failover to eight.
_PRICE_RESOLUTION_CONCURRENCY = 8

OVERVIEW_INDEX_NAMES = {
    "nifty": "NIFTY 50",
    "bank_nifty": "BANK NIFTY",
    "sensex": "SENSEX",
}


def register_default_providers() -> None:
    """Register the providers available without any per-user entitlement.

    Today that is Yahoo Finance alone — the permanent floor of the priority
    list. Broker adapters are registered per user when a broker connection
    becomes active (D3) and unregistered when it ends, which is why this
    function is about *default* providers rather than all of them.

    Idempotent: the registry ignores a duplicate name, so a re-entrant startup
    path cannot produce two Yahoo adapters with divergent health counters.
    """
    if "yahoo" not in provider_registry:
        provider_registry.register(YahooPollingAdapter())


class MarketGateway:
    """Central gateway for all market data operations."""

    def __init__(self) -> None:
        self._initialized = False
        self._start_time: Optional[str] = None
        #: The most recent unresolvable request, or None while the feed is
        #: being served. Provider-free by construction — it carries a capability
        #: and an `UnavailableReason`, never a provider name — so it is safe on
        #: the `status` property, which status endpoints reach.
        self._last_unavailable: Optional[Dict[str, Any]] = None

    async def initialize(self) -> None:
        """Start the gateway and publish readiness + initial feed status."""
        register_default_providers()
        # Broker lifecycle events (D3). Subscribing at gateway initialisation
        # rather than at import keeps the Source Manager's subscription tied to
        # the same lifecycle as everything else it needs, and makes it absent in
        # tests that never initialise the gateway.
        source_manager.subscribe_broker_events()
        for provider in provider_registry.all():
            await provider.connect()

        self._start_time = datetime.now(timezone.utc).isoformat()
        self._initialized = True
        await event_bus.publish("market.gateway.ready", {
            "start_time": self._start_time,
            "market_hours": is_market_hours(),
        })
        # `force` because the first status is news to every consumer even though
        # nothing has "changed" — a subscriber that connects after startup needs
        # the tier indicator to have a value.
        await source_manager.publish_status(force=True)
        logger.info("MarketGateway initialized")

    @property
    def status(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "start_time": self._start_time,
            "market_hours": is_market_hours(),
            "event_bus_subscribers": event_bus.subscriber_count,
            "event_bus_types": event_bus.event_types,
            "recent_events_count": len(event_bus.recent_events(limit=100)),
            # Tier and feed state only — this property is reachable from status
            # endpoints, so it may not carry provider identity.
            "feed": source_manager.status(),
            "last_unavailable": self._last_unavailable,
        }

    @property
    def diagnostics(self) -> Dict[str, Any]:
        """Provider-level detail, including provider names. Admin surfaces only."""
        return source_manager.diagnostics()

    # ── Public-contract provenance ───────────────────────

    def source_tier(
        self,
        capability: Capability = Capability.QUOTES,
        *,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        """Freshness tier currently serving `capability` — `"streaming"`,
        `"delayed"`, or None when nothing is.

        The ONLY provenance a REST response may carry (MARKET_DATA_ARCHITECTURE.md,
        Developer Rule 4). Route handlers stamp `source_tier` with this instead of
        a literal.

        WHY THIS EXISTS ON THE GATEWAY (D2/DD-1)
        ----------------------------------------
        Until DD-1 the public contract carried `source: "yahoo_finance"` — a
        hardcoded literal, written by hand at each route. Two things were wrong
        with it. It named a provider on a public surface, which Rule 4 forbids
        outright. And it was a *constant*: the day a broker feed serves a quote,
        a literal still says "yahoo_finance", so the field does not merely leak
        provenance, it reports the wrong provenance. `InvestmentAdvisor.jsx`
        branched on exactly that string to decide whether to render "Live market
        data" or "Fallback data", so a streaming broker quote would have been
        labelled "Fallback data" to the user.

        Reading it from the Source Manager makes the field track reality with no
        route ever learning who is serving. Routes that already go through a
        gateway *read* get the tier stamped on the payload and do not need this;
        it is for composite payloads (the market overview) and for the routes
        still on their own data path (DD-1 residue, recorded in TASK.md).
        """
        tier = source_manager.active_tier(capability, user_id=user_id)
        return tier.value if tier else None

    # ── Streaming provider registration (D4.4) ───────────
    #
    # The gateway is where a pushed feed joins the platform, for the same reason
    # it is where a polled one does: Developer Rule 2 says nothing may bypass it,
    # and a provider that registered itself and delivered into its own consumer
    # would be a second entry point for market data with none of the health,
    # tier stamping or event fan-out this one performs.
    #
    # Whoever owns the feed constructs the provider and calls this; the gateway
    # never constructs one, and never learns what kind of feed is behind it.

    async def register_streaming_provider(self, provider: MarketDataProvider) -> bool:
        """Register a pushed feed and bind its output to this gateway.

        Returns True when the provider is registered and connected. Raises
        :class:`ProviderContractError` — from the registry's own contract check —
        when the provider's declarations contradict each other; that is a
        programming error in the adapter and is not something a caller can
        degrade around.

        `replace=True` because a feed re-registering under the same name is a
        *reconnection*, and the reconnected provider is the live one. Ignoring it
        as a duplicate (the registry's default) would leave the platform holding
        a provider bound to a socket that no longer exists.
        """
        provider.bind_sink(self._ingest_ticks)
        # D4.5: readiness transitions are announced through the gateway for the
        # same reason ticks are delivered through it — nothing reaches a
        # consumer around this choke point (Developer Rule 2), and a promotion
        # is a consumer-visible fact. Bound only on providers that have the
        # surface: the contract for a pushed feed is `on_raw`, and a readiness
        # gate is a property of the streaming *implementation*, not of every
        # provider the gateway may be handed.
        binder = getattr(provider, "bind_readiness_listener", None)
        if callable(binder):
            binder(self._on_provider_readiness)
        try:
            provider_registry.register(provider, replace=True)
        except ProviderContractError:
            provider.bind_sink(None)
            if callable(binder):
                binder(None)
            raise
        await provider.connect()
        logger.info(
            "Streaming provider registered: %s (tier=%s, priority=%d, scope=%s)",
            provider.name, provider.tier.value, provider.priority,
            "global" if provider.owner_user_id is None else "user",
        )
        await source_manager.publish_status()
        return True

    async def unregister_streaming_provider(
        self,
        name: str,
        *,
        change_reason: Optional[FeedChangeReason] = None,
    ) -> bool:
        """Drop a pushed feed. Returns True when one was actually removed.

        Disconnect and unbind before unregistering: an entitlement that has
        ended must stop being resolvable *and* stop being able to deliver, and
        doing only the first would leave a live socket pushing into a gateway
        that no longer lists its provider.

        `change_reason` (D5.13) is the caller's answer to "why", passed
        straight through to the owner's `provider.status` and nowhere else. It
        exists because this is the last moment anyone can answer: one line
        below, the provider is out of the registry and the tier that is about
        to move has nothing left to explain it (LIM-D5.5-2). It is optional
        because most callers genuinely do not know, and a guessed cause on a
        consumer surface is worse than none.
        """
        provider = provider_registry.get(name)
        if provider is None:
            return False
        # D5.13 — UNBIND THE READINESS LISTENER *BEFORE* DISCONNECTING.
        # `disconnect()` drives the feed's readiness backwards, which fired
        # `_on_provider_readiness` and published a user-scoped `provider.status`
        # announcing the demotion — from inside the teardown, and therefore
        # before the unregistration below could publish the same demotion *with
        # its reason*. Change gating then suppressed the second one as a repeat,
        # so the explained event never reached the bus at all: the user was told
        # their tier had moved and never told why, which is precisely
        # LIM-D5.5-2 reappearing one layer down from where it was fixed.
        #
        # Silencing it is also right on its own terms. A readiness transition
        # produced by tearing a provider down is an artifact of the teardown,
        # not news about the user's feed; the authoritative statement about
        # what this user is now on is the one this method makes below, once the
        # provider is actually out of the registry.
        binder = getattr(provider, "bind_readiness_listener", None)
        if callable(binder):
            binder(None)
        try:
            await provider.disconnect()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Streaming provider %s failed to disconnect cleanly: %s", name, exc)
        provider.bind_sink(None)
        provider_registry.unregister(name)
        # D5.7: drop any failure cool-down this feed was serving. Not a recovery
        # claim — a re-attached feed is a new instance with fresh UNKNOWN health
        # and would never be re-admitted on the old one's ladder anyway; this
        # only stops the register accumulating an entry per feed that has ever
        # been down.
        # D5.8 makes this reach the shared store too, for the same reason: an
        # unregistered provider's shared record is evidence about a subject that
        # no longer exists. (For a streaming feed there is no shared record —
        # `health_is_shared` is False — and this is the local drop it always was.)
        await source_manager.forget_health_recovery_shared(provider)
        owner = provider.owner_user_id
        if owner:
            # Announce the demotion *before* forgetting the cached per-user
            # status: publishing is change-gated against that cache, and
            # clearing it first would make the last event look like a repeat of
            # nothing and be suppressed — the user would be dropped back to the
            # baseline without ever being told.
            await source_manager.publish_status(user_id=owner, change_reason=change_reason)
            source_manager.forget_user_status(owner)
        # Scoped to the owner on purpose. The platform view changes too — the
        # registry lost a provider — but one account's entitlement is not news
        # about the platform's feed, and this publish is broadcast to everyone.
        await source_manager.publish_status()
        return True

    async def _on_provider_readiness(self, provider: MarketDataProvider,
                                     previous: Any, current: Any) -> None:
        """A pushed feed changed state — promote or demote its owner's feed.

        Both axes of a feed's state arrive here: readiness (D4.5) and stability
        (D5.2). Deliberately handled identically and not branched on, because
        the consumer-visible fact is the same in both cases — the tier serving
        this user may have moved — and the answer is the same too: republish the
        owner's status and let resolution speak for itself.

        There is nothing to *do* here beyond announcing it, and that is the
        design rather than an omission. Promotion is not an action the gateway
        performs on a provider: resolution recomputes eligibility from current
        readiness on every request, so the switch has already happened by the
        time this runs, atomically, for the one user who owns the feed. What is
        left is telling that user's consumers their tier moved.

        Scoped to the owner. A per-user feed changing state must not publish a
        platform-wide status — that would tell every other user's tier indicator
        that something changed for them when nothing did.
        """
        owner = provider.owner_user_id
        logger.info(
            "Streaming feed %s state %s -> %s (owner=%s)",
            provider.name,
            getattr(previous, "value", previous),
            getattr(current, "value", current),
            "user" if owner else "global",
        )
        if owner:
            await source_manager.publish_status(user_id=owner)
        else:
            await source_manager.publish_status()

    async def _ingest_ticks(self, provider: MarketDataProvider,
                            ticks: List[MarketTick]) -> None:
        """The sink every pushed tick arrives through — validate, stamp, publish.

        One event per *batch*, not per tick. A feed frame is already a batch of
        up to hundreds of packets, and one bus event per packet would put a
        Redis round-trip behind every price change on every connected account.

        The published payload carries `source_tier` and no provider identity, so
        a consumer learns the data is live without learning who produced it
        (Developer Rule 4). It carries `user_id` when the feed is owned by one:
        the event bridge delivers a payload with a `user_id` to that user alone,
        which is what keeps a feed consumed under one user's entitlement from
        being broadcast to every socket on the market channel. For a
        platform-wide feed there is no `user_id` and the event fans out normally.
        """
        if not ticks:
            return
        if await source_manager.record_success_shared(provider):
            await source_manager.publish_status()

        payload: Dict[str, Any] = {
            "ticks": [tick.as_dict() for tick in ticks],
            "count": len(ticks),
            "source_tier": provider.tier.value,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        if provider.owner_user_id:
            payload["user_id"] = provider.owner_user_id
        await event_bus.publish(TICK_TOPIC, payload)

    # ── Provider invocation ──────────────────────────────

    async def _serve_with_provider(
        self,
        capability: Capability,
        operation: str,
        invoke: Callable[[MarketDataProvider], Awaitable[T]],
        default: T,
        *,
        user_id: Optional[str] = None,
        context: Optional[ResolutionContext] = None,
        subsystem: str = "market_data",
    ) -> "tuple[Optional[MarketDataProvider], T]":
        """Resolve providers for `capability`, call them in order, record outcomes.

        Returns `(provider, payload)` for whichever provider actually answered.
        The provider comes back with the payload because the caller needs it to
        pick a normalizer and stamp a tier, and resolving a second time at the
        call site could pick a *different* provider than the one that answered —
        normalizing a Yahoo payload with a broker's normalizer is exactly the
        class of bug this boundary exists to prevent. With the failover chain
        below that is no longer a hypothetical: the answering provider is
        routinely not the preferred one.

        FAILOVER WITHIN THE REQUEST (D2)
        --------------------------------
        The Source Manager returns an ordered chain, not a single pick, and this
        method walks it: preferred provider, and on failure the next eligible
        one, until something answers. MARKET_DATA_ARCHITECTURE.md's failover
        diagram, executed.

        D1 called the head of the chain alone and re-raised. Failover was real
        but only *between* requests — a provider had to accumulate
        `DOWN_AFTER_FAILURES` consecutive failures before the registry stopped
        offering it, and every request in between returned nothing to a user
        whose baseline feed was healthy the whole time. The health counters
        still do their job (a DOWN provider is dropped from resolution entirely,
        so later requests do not pay for its timeout first); the chain closes the
        window before they trip.

        Only an *exception* advances the chain. An empty result does not: an
        empty gainers list at 3am is the correct answer, and failing over on it
        would double every provider call on a quiet market while producing the
        same empty list. Emptiness is recorded against health, where a provider
        answering 200-with-no-data forever is already visible.

        When the chain is exhausted the last exception is re-raised untouched,
        which preserves D1's contract exactly: the public methods below differ
        in whether they contain their own failures, and several are called by
        routes whose error handling depends on seeing the exception.
        Instrumentation and health bookkeeping observe control flow; they never
        alter it.

        `default` is returned only when there is no provider at all to ask — the
        feed is genuinely unavailable. It is the caller's own empty value (None,
        [], {}), never a substitute for real market data: fabricating a price to
        fill a gap is forbidden by CLAUDE.md and by MARKET_DATA_ARCHITECTURE.md's
        failover rules alike.

        `subsystem` labels the metric. Note the provider *label* stays
        "market_data" rather than becoming the adapter's name: one time series
        per provider identity would tie the metric vocabulary to the provider
        set, and `observability/instruments.py` keeps that vocabulary frozen on
        purpose.
        """
        # D5.8 — one awaitable prelude, then the same synchronous resolution.
        # `prepare` refreshes the candidates' health from the shared store and
        # atomically claims at most one recovery trial per DOWN provider, so what
        # this worker ranks is what the deployment has observed and the trial it
        # may spend is one no other worker is spending.
        shared = await source_manager.prepare(capability, context, user_id=user_id)
        resolution = source_manager.resolve_feed(
            capability, context, user_id=user_id, shared=shared
        )

        if not resolution.available:
            # The explicit unavailable state. `reason` distinguishes "nothing is
            # registered" from "this user is entitled to nothing" from "no
            # provider serves depth" from "everything is in outage" — four
            # incidents that D1 logged identically.
            self._last_unavailable = {
                "capability": capability.value,
                "operation": operation,
                "reason": resolution.reason.value if resolution.reason else None,
                "at": datetime.now(timezone.utc).isoformat(),
            }
            logger.warning(
                "Gateway: feed unavailable for capability=%s (%s) reason=%s",
                capability.value, operation,
                resolution.reason.value if resolution.reason else "unknown",
            )
            await source_manager.publish_status()
            return None, default

        self._last_unavailable = None
        last_error: Optional[BaseException] = None

        for attempt, provider in enumerate(resolution.chain):
            try:
                with instruments.track_provider(subsystem, operation) as call:
                    result = await invoke(provider)
                    empty = not result
                    if empty:
                        call.empty()
            except Exception as exc:
                last_error = exc
                if await source_manager.record_failure_shared(provider, exc):
                    await source_manager.publish_status()
                remaining = len(resolution.chain) - attempt - 1
                logger.warning(
                    "Gateway: provider call failed for %s (%s): %s — %d alternative(s) left",
                    capability.value, operation, exc, remaining,
                )
                continue

            if await source_manager.record_success_shared(provider, empty=empty):
                await source_manager.publish_status()
            return provider, result

        # Chain exhausted: every eligible provider raised. Re-raise the last
        # error rather than degrading to `default`, because this is a failure to
        # serve, not an absence of providers, and the callers below deliberately
        # differ on how they handle each.
        #
        # `last_error` cannot be None here — the loop body either returns or
        # sets it, and an empty chain was handled above — but this is a raise
        # statement, so the fallback is spelled out rather than asserted: a bare
        # `assert` vanishes under `python -O` and would turn an unreachable
        # branch into `TypeError: exceptions must derive from BaseException`,
        # replacing the provider's real error with a confusing one.
        if last_error is None:  # pragma: no cover - unreachable
            return None, default
        raise last_error

    async def _serve(
        self,
        capability: Capability,
        operation: str,
        invoke: Callable[[MarketDataProvider], Awaitable[T]],
        default: T,
        *,
        user_id: Optional[str] = None,
        context: Optional[ResolutionContext] = None,
    ) -> T:
        """`_serve_with_provider` for callers that return the provider's payload
        unchanged and so have no use for the provider itself."""
        _provider, result = await self._serve_with_provider(
            capability, operation, invoke, default,
            user_id=user_id, context=context,
        )
        return result

    @staticmethod
    def _context_for(symbol: Optional[str], user_id: Optional[str]) -> ResolutionContext:
        """Build the resolution context for an instrument-scoped request.

        The gateway already knows the symbol at every call site that has one, so
        it supplies it rather than making callers thread it through. That is
        what lets MARKET_DATA_ARCHITECTURE.md's per-symbol rule — "a broker feed
        covering NSE equities does not disqualify Yahoo from serving a US index
        the broker doesn't carry" — be implemented in D3 purely inside a
        provider's `is_eligible_for`, with no change to any call site.
        """
        return ResolutionContext(user_id=user_id, symbol=symbol)

    # ── Single stock quote ───────────────────────────────

    async def get_prices(
        self,
        symbols: Sequence[str],
        *,
        user_id: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """`{SYMBOL: quote}` for a set of instruments, resolved for one user.

        WHY THIS EXISTS (D5.15)
        -----------------------
        The platform's live price broadcast — the message every dashboard,
        watchlist and market page actually renders — did not come through this
        gateway at all. It called Yahoo directly, from
        `heartbeat_engine._collect_prices`, behind a five-minute bundle cache,
        and broadcast one global map to every socket. Three consequences, all
        observed rather than reasoned about:

        * **a broker feed could never reach the screen.** The loop had no user,
          so no per-user provider was ever a candidate; a user promoted to their
          own live feed still saw the shared baseline, and the tier indicator
          (D5.14) correctly said `streaming` beside prices that were not.
        * **the prices were up to five minutes old** while being presented as
          live, which is the "non-changing dashboard" symptom D5.15 opened with.
        * **it bypassed Developer Rule 2.** Nothing that reaches a consumer may
          route around this choke point, and the single most-rendered number in
          the product did.

        Per symbol, because eligibility is per symbol: MARKET_DATA_ARCHITECTURE's
        rule that "a broker feed covering NSE equities does not disqualify Yahoo
        from serving a US index the broker doesn't carry" is only honoured if
        each instrument is resolved on its own. A symbol whose resolution fails
        or whose quote does not validate is **omitted** rather than carried with
        a null price — an absent price renders as the last known one, a null
        renders as a hole.

        Callers that fan out to many users should read
        :meth:`baseline_prices_are_shared` first; it says when one resolution
        can legitimately serve everybody.
        """
        wanted = list(dict.fromkeys(
            str(s).strip().upper() for s in (symbols or []) if str(s or "").strip()))
        if not wanted:
            return {}

        # RESOLVED CONCURRENTLY, BUT BOUNDED (D5.16).
        #
        # Sequentially, a hundred-symbol watchlist on a cold cache is a hundred
        # round trips end to end: ~20 s for one `GET /api/watchlist`, and — less
        # visibly — most of the 15-second budget of the price broadcast loop
        # that calls this for the whole dashboard universe every cycle. The
        # per-symbol resolution D5.15 introduced is correct and was serialised.
        #
        # The bound is the part that is not incidental. Unbounded, a dead
        # provider is discovered by every symbol independently: a hundred
        # concurrent requests all fail, all advance the failover chain, and the
        # provider's health is marked down a hundred times over — the same
        # "failover works, after N failed requests" shape D2 closed. With a
        # small window, at most `_PRICE_RESOLUTION_CONCURRENCY` requests can be
        # in flight against a provider before the rest observe the state its
        # failure produced.
        semaphore = asyncio.Semaphore(_PRICE_RESOLUTION_CONCURRENCY)

        async def _resolve(symbol: str):
            async with semaphore:
                return symbol, await self._quote(symbol, user_id=user_id)

        out: Dict[str, Dict[str, Any]] = {}
        results = await asyncio.gather(
            *(_resolve(symbol) for symbol in wanted), return_exceptions=True)
        for result in results:
            # `_quote` already swallows its own failures; this catches only a
            # cancellation or a programming error, and one symbol's must not
            # cost the batch.
            if isinstance(result, BaseException):
                logger.warning(f"Gateway: price resolution raised: {result}")
                continue
            symbol, quote = result
            if quote and quote.get("price") is not None:
                out[symbol] = quote
        return out

    def baseline_prices_are_shared(self, user_id: Optional[str]) -> bool:
        """Whether this user's prices are the same ones everybody else gets.

        True when the user has no provider of their own in play, which is the
        normal case and the one that makes a fan-out affordable: with no
        per-user provider eligible, resolving for this user and resolving for
        the platform choose from the identical candidate set and therefore
        return the identical answer. A caller may compute the platform map once
        and reuse it for every user this returns True for.

        Deliberately asked of the Source Manager rather than inferred from
        "does this user have a broker connected": a connected broker with no
        registered feed, an unready feed, a feed on probation and a feed that
        lost its link all still resolve to the baseline, and a caller that
        guessed from the connection would send those users a second, redundant
        resolution — or worse, believe their prices were personal when they were
        not.
        """
        return source_manager.active_tier(user_id=user_id) == source_manager.active_tier()

    async def _quote(self, symbol: str, *, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Resolve, normalize, validate and tier-stamp one quote.

        The body `get_quote` used to be, extracted so the batch path above and
        the single-quote path below cannot drift — in particular so they cannot
        come to disagree about which provider answered or what tier to stamp.
        What stays in `get_quote` and *not* here is the `price.updated` publish:
        one bus event per symbol per user per cycle would put a Redis round-trip
        behind every price on every dashboard, and the batch path has its own
        delivery.
        """
        try:
            provider, raw = await self._serve_with_provider(
                Capability.QUOTES, "get_quote",
                lambda p: p.fetch_quote(symbol), None,
                context=self._context_for(symbol, user_id),
            )
        except Exception as exc:
            logger.warning(f"Gateway: quote fetch failed for {symbol}: {exc}")
            return None

        if not raw:
            return None

        normalized = normalize_stock_quote(raw, provider=_normalizer_key(provider))
        if not normalized:
            return None

        result = validate_stock_quote(normalized)
        if not result.valid:
            logger.warning(f"Gateway: quote validation failed for {symbol}: {result.errors}")
            return None

        _stamp_tier(normalized, provider)
        return normalized

    async def get_quote(self, symbol: str, *, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch, normalize, validate a single stock quote."""
        normalized = await self._quote(symbol, user_id=user_id)
        if not normalized:
            return None

        # D6.3 — WHOSE PRICE IS THIS, AND WHO MAY BE TOLD.
        #
        # `price` is a public domain, so the bridge broadcasts an event with no
        # `user_id` to every socket on the `market` channel. That is right when
        # the platform baseline answered. It was wrong whenever a *broker* feed
        # did: `_publish_ticks`, twenty lines up, already stamps
        # `provider.owner_user_id` onto every tick for exactly this reason — a
        # broker feed is legally the account holder's data, consumed under their
        # own session and entitlement (MARKET_DATA_ARCHITECTURE.md, Category 2),
        # and D6.0's S2 named republishing a broker-derived fact to non-owners as
        # a defect in its own right. This path published the same class of value,
        # resolved through the same per-user promotion, to everybody.
        #
        # The predicate is the Source Manager's own, not a new concept and not a
        # guess from "does this user have a broker connected": `_quote` returns
        # the quote and not the provider, and `baseline_prices_are_shared` is the
        # documented answer to "are this user's prices the ones everybody else
        # gets". False means a provider of their own is in play, so the event is
        # addressed to them and the bridge delivers it by `send_to_user`.
        #
        # A user on the shared baseline still broadcasts, byte for byte as
        # before, which is every user who has no broker feed promoted.
        payload: Dict[str, Any] = {
            "symbol": symbol,
            "price": normalized.get("price"),
            "change_pct": normalized.get("change_pct"),
        }
        if user_id and not self.baseline_prices_are_shared(user_id):
            payload["user_id"] = str(user_id)
        await event_bus.publish("price.updated", payload)

        return normalized

    # ── Batch universe quotes ────────────────────────────

    async def get_universe_quotes(self, *, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch all universe quotes, normalize and validate."""
        try:
            provider, raw_quotes = await self._serve_with_provider(
                Capability.UNIVERSE_QUOTES, "get_universe_quotes",
                lambda p: p.fetch_universe_quotes(), [], user_id=user_id,
            )
        except Exception as exc:
            logger.warning(f"Gateway: universe fetch failed: {exc}")
            return []

        if not raw_quotes:
            return []

        normalizer_key = _normalizer_key(provider)
        normalized = []
        for raw in raw_quotes:
            norm = normalize_stock_quote(raw, provider=normalizer_key)
            if norm:
                _stamp_tier(norm, provider)
                normalized.append(norm)

        valid, errors = validate_batch(normalized, validate_stock_quote)
        if errors:
            logger.debug(f"Gateway: {len(errors)} universe quotes rejected")

        return valid

    # ── Index quotes ─────────────────────────────────────

    async def get_indices(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Fetch market overview with normalized indices.

        Normalized index fields are merged *over* the raw sub-dict rather than
        replacing it, so overview-level keys the provider supplies — notably
        `available`, which consumers branch on — survive normalization. An index
        that fails validation is left exactly as the provider sent it rather
        than dropped: the overview is a composite, and losing one index must not
        cost the caller the other two.
        """
        try:
            overview = await self._serve(
                Capability.INDICES, "get_indices",
                lambda p: p.fetch_indices(), {}, user_id=user_id,
            )
        except Exception as exc:
            logger.warning(f"Gateway: overview fetch failed: {exc}")
            return {}

        if not overview:
            return {}

        for key, display_name in OVERVIEW_INDEX_NAMES.items():
            raw_idx = overview.get(key)
            if not raw_idx:
                continue
            normalized = normalize_index_quote({"name": display_name, **raw_idx})
            if not normalized:
                continue
            if validate_index_quote(normalized).valid:
                overview[key] = {**raw_idx, **normalized}

        return overview

    # ── Sector data ──────────────────────────────────────

    async def get_sectors(self, *, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch, normalize, and validate sector performance."""
        try:
            raw_sectors = await self._serve(
                Capability.SECTORS, "get_sectors",
                lambda p: p.fetch_sectors(), [], user_id=user_id,
            )
        except Exception as exc:
            logger.warning(f"Gateway: sector fetch failed: {exc}")
            return []

        if not raw_sectors:
            return []

        # Drop anything that is not a row before normalizing.
        # MARKET_DATA_ARCHITECTURE.md: "Unknown or malformed payloads are logged
        # and dropped — they never propagate." A provider answering with a dict
        # where a list belongs would otherwise be iterated into its *keys*, and
        # `normalize_sector_data("some_key")` raises `AttributeError` — an
        # unhandled 500 on a dashboard route, from a provider that merely
        # returned the wrong shape. Found by the DD-1 route migration: this path
        # became reachable the moment `/api/market/sectors` started reading
        # through the gateway instead of returning the provider payload raw.
        if not isinstance(raw_sectors, (list, tuple)):
            logger.warning(
                "Gateway: sector payload was %s, expected a list — dropped",
                type(raw_sectors).__name__,
            )
            return []

        normalized = []
        for raw in raw_sectors:
            if not isinstance(raw, dict):
                logger.debug("Gateway: dropped non-dict sector row (%s)", type(raw).__name__)
                continue
            norm = normalize_sector_data(raw)
            if norm:
                normalized.append(norm)

        valid, _ = validate_batch(normalized, validate_sector_data)

        await event_bus.publish("sector.updated", {
            "count": len(valid),
            "top": valid[0]["name"] if valid else None,
        })

        return valid

    # ── Gainers / Losers ─────────────────────────────────

    # The methods below deliberately do NOT catch: their callers own the error
    # handling, and adding a try/except here to make instrumentation tidier
    # would change which failures reach a route handler. `_serve` records the
    # failure against provider health and re-raises, which is the whole
    # contract it was written to.

    async def get_gainers(self, count: int = 5, *, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self._serve(
            Capability.MOVERS, "get_gainers",
            lambda p: p.fetch_gainers(count), [], user_id=user_id,
        )

    async def get_losers(self, count: int = 5, *, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self._serve(
            Capability.MOVERS, "get_losers",
            lambda p: p.fetch_losers(count), [], user_id=user_id,
        )

    # ── Global markets ───────────────────────────────────

    async def get_global_markets(self, *, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self._serve(
            Capability.GLOBAL_MARKETS, "get_global_markets",
            lambda p: p.fetch_global_markets(), [], user_id=user_id,
        )

    # ── Gift Nifty ───────────────────────────────────────

    async def get_gift_nifty(self) -> Dict[str, Any]:
        """Pre-market Nifty futures read. Always returns a payload; check
        `available` — no licensed NSE IX feed is connected by default.

        Served by its own priority-ordered adapter chain in `gift_nifty.py`
        rather than through the provider registry: Gift Nifty trades on NSE IX,
        a venue none of the registry's providers carries, so it has no candidate
        to resolve. Folding it into the registry is a D2 consolidation.
        """
        from services.market_engine.gift_nifty import get_gift_nifty
        with instruments.track_provider("market_data", "get_gift_nifty") as call:
            result = await get_gift_nifty()
            # `available: false` is this endpoint's normal state without a
            # licensed feed, so it is recorded as `empty` rather than `ok` —
            # otherwise a permanently unavailable feed reads as a healthy one.
            if not result or not result.get("available"):
                call.empty()
            return result

    # ── Economic calendar ────────────────────────────────

    async def get_calendar(self, days_ahead: int = 30, days_behind: int = 7) -> Dict[str, Any]:
        """Economic calendar. A scheduled-events collector, not a price feed —
        it has no provider adapter for the same reason Gift Nifty does not."""
        from services.market_engine.economic_calendar import get_calendar
        with instruments.track_provider("market_data", "get_calendar") as call:
            result = await get_calendar(days_ahead=days_ahead, days_behind=days_behind)
            if not result:
                call.empty()
            return result

    # ── Commodities ──────────────────────────────────────

    async def get_commodities(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._serve(
            Capability.COMMODITIES, "get_commodities",
            lambda p: p.fetch_commodities(), {}, user_id=user_id,
        )

    # ── FII / DII ────────────────────────────────────────

    async def get_fii_dii(self) -> Dict[str, Any]:
        """Institutional activity from NSE India's public API.

        Not behind the provider registry: this is a different provider (NSE,
        not Yahoo) reached through the same client module, and giving it an
        adapter means giving NSE India an adapter — D2 work, tracked in
        TASK.md, not something to half-do here.
        """
        from services.real_market import fetch_real_fii_dii
        with instruments.track_provider("market_data", "get_fii_dii") as call:
            result = await fetch_real_fii_dii()
            if not result:
                call.empty()
            return result

    # ── News (via news_service) ──────────────────────────

    async def get_news(self, force: bool = False) -> List[Dict[str, Any]]:
        from services.news_service import fetch_news

        # Labelled provider="news", not "market_data": a news outage and a price
        # outage have different owners, different severities and different
        # user-visible effects, and a single `provider` bucket for both would
        # make the one alert that fires say nothing about which.
        try:
            with instruments.track_provider("news", "get_news") as call:
                raw_articles = await fetch_news(force=force)
                if not raw_articles:
                    call.empty()
        except Exception as exc:
            logger.warning(f"Gateway: news fetch failed: {exc}")
            return []

        normalized = [normalize_news_article(a) for a in (raw_articles or [])]
        return [a for a in normalized if a.get("title")]

    # ── Chart data ───────────────────────────────────────

    async def get_chart(self, symbol: str, period: str = "1D",
                        *, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self._serve(
            Capability.OHLC, "get_chart",
            lambda p: p.fetch_chart(symbol, period), [],
            context=self._context_for(symbol, user_id),
        )

    # ── Instrument search ────────────────────────────────

    async def search(self, query: str, limit: int = 10,
                     *, user_id: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """Search instruments. None means the provider failed (the caller picks
        a fallback); `[]` means the provider answered with no matches."""
        return await self._serve(
            Capability.SEARCH, "search",
            lambda p: p.search(query, limit), None, user_id=user_id,
        )

    # ── Chart patterns ───────────────────────────────────

    async def get_patterns(self, symbol: str) -> Dict[str, Any]:
        """Candlestick/chart pattern detection.

        Pattern detection is Market Engine analysis rather than provider data —
        it consumes OHLC and emits signals. It currently lives in the Yahoo
        client module and is called directly for that reason; relocating it into
        the Market Engine is D2 work (ADR-028).
        """
        from services.real_market import detect_chart_patterns
        with instruments.track_provider("market_data", "get_patterns") as call:
            result = await detect_chart_patterns(symbol)
            if not result:
                call.empty()
            return result


def _normalizer_key(provider: Optional[MarketDataProvider]) -> str:
    """Which normalizer family understands this provider's payloads.

    Falls back to Yahoo's when resolution produced nothing, because the only
    way to reach a normalizer with no provider is a payload that came from the
    baseline path anyway — and an unknown key would silently degrade the quote
    to the passthrough normalizer instead of failing visibly.
    """
    return provider.normalizer_key if provider else "yahoo"


def _stamp_tier(event: Dict[str, Any], provider: Optional[MarketDataProvider]) -> Dict[str, Any]:
    """Stamp freshness provenance on a normalized event, in place.

    `source_tier` is the ONLY provenance permitted to leave the gateway
    (MARKET_DATA_ARCHITECTURE.md, "Normalization"). `ingested_at` gives every
    consumer — the AI especially — the timestamp it needs to say "as of 10:42"
    instead of pleading ignorance when data is stale.
    """
    event["source_tier"] = provider.tier.value if provider else None
    event["ingested_at"] = datetime.now(timezone.utc).isoformat()
    return event


# Module-level singleton
market_gateway = MarketGateway()

# Register the baseline provider at import, not at `initialize()`.
#
# MARKET_DATA_ARCHITECTURE.md makes Yahoo the permanent floor: a user may never
# end up with no provider while Yahoo is reachable. Deferring registration to
# `initialize()` would make that guarantee conditional on a startup hook having
# run, and every caller that reaches the gateway without one — a worker process,
# a CLI script, a test importing the module directly — would silently resolve to
# no provider and read as "feed unavailable" when the feed is fine.
# Registration touches no network and no event loop; `initialize()` still owns
# connecting the providers and announcing readiness.
register_default_providers()

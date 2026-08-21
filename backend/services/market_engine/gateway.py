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
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

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
from services.market_engine.source_manager import source_manager
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
        try:
            provider_registry.register(provider, replace=True)
        except ProviderContractError:
            provider.bind_sink(None)
            raise
        await provider.connect()
        logger.info(
            "Streaming provider registered: %s (tier=%s, priority=%d, scope=%s)",
            provider.name, provider.tier.value, provider.priority,
            "global" if provider.owner_user_id is None else "user",
        )
        await source_manager.publish_status()
        return True

    async def unregister_streaming_provider(self, name: str) -> bool:
        """Drop a pushed feed. Returns True when one was actually removed.

        Disconnect and unbind before unregistering: an entitlement that has
        ended must stop being resolvable *and* stop being able to deliver, and
        doing only the first would leave a live socket pushing into a gateway
        that no longer lists its provider.
        """
        provider = provider_registry.get(name)
        if provider is None:
            return False
        try:
            await provider.disconnect()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Streaming provider %s failed to disconnect cleanly: %s", name, exc)
        provider.bind_sink(None)
        provider_registry.unregister(name)
        await source_manager.publish_status()
        return True

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
        if source_manager.record_success(provider):
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
        resolution = source_manager.resolve_feed(capability, context, user_id=user_id)

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
                if source_manager.record_failure(provider, exc):
                    await source_manager.publish_status()
                remaining = len(resolution.chain) - attempt - 1
                logger.warning(
                    "Gateway: provider call failed for %s (%s): %s — %d alternative(s) left",
                    capability.value, operation, exc, remaining,
                )
                continue

            if source_manager.record_success(provider, empty=empty):
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

    async def get_quote(self, symbol: str, *, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch, normalize, validate a single stock quote."""
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

        await event_bus.publish("price.updated", {
            "symbol": symbol,
            "price": normalized.get("price"),
            "change_pct": normalized.get("change_pct"),
        })

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

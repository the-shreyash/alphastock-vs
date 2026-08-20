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
    YahooPollingAdapter,
    provider_registry,
)
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

    async def initialize(self) -> None:
        """Start the gateway and publish readiness + initial feed status."""
        register_default_providers()
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
        }

    @property
    def diagnostics(self) -> Dict[str, Any]:
        """Provider-level detail, including provider names. Admin surfaces only."""
        return source_manager.diagnostics()

    # ── Provider invocation ──────────────────────────────

    async def _serve_with_provider(
        self,
        capability: Capability,
        operation: str,
        invoke: Callable[[MarketDataProvider], Awaitable[T]],
        default: T,
        *,
        user_id: Optional[str] = None,
        subsystem: str = "market_data",
    ) -> "tuple[Optional[MarketDataProvider], T]":
        """Resolve a provider for `capability`, call it, and record the outcome.

        Returns `(provider, payload)`. The provider comes back with the payload
        because the caller needs it to pick a normalizer and stamp a tier, and
        resolving a second time at the call site could pick a *different*
        provider than the one that actually answered — normalizing a Yahoo
        payload with a broker's normalizer is exactly the class of bug this
        boundary exists to prevent.

        Provider exceptions are re-raised untouched. That is deliberate: the
        public methods below differ in whether they contain their own failures,
        and several of them are called by routes whose error handling depends on
        seeing the exception. Instrumentation and health bookkeeping observe
        control flow here; they never alter it.

        `default` is returned only when there is no provider at all to ask —
        the feed is genuinely unavailable. It is the caller's own empty value
        (None, [], {}), never a substitute for real market data: fabricating a
        price to fill a gap is forbidden by CLAUDE.md and by
        MARKET_DATA_ARCHITECTURE.md's failover rules alike.

        `subsystem` labels the metric. Note the provider *label* stays
        "market_data" rather than becoming the adapter's name: one time series
        per provider identity would tie the metric vocabulary to the provider
        set, and `observability/instruments.py` keeps that vocabulary frozen on
        purpose.
        """
        provider = source_manager.resolve(capability, user_id=user_id)
        if provider is None:
            logger.warning(
                "Gateway: no provider available for capability %s (%s)",
                capability.value, operation,
            )
            await source_manager.publish_status()
            return None, default

        try:
            with instruments.track_provider(subsystem, operation) as call:
                result = await invoke(provider)
                empty = not result
                if empty:
                    call.empty()
        except Exception as exc:
            if source_manager.record_failure(provider, exc):
                await source_manager.publish_status()
            raise

        if source_manager.record_success(provider, empty=empty):
            await source_manager.publish_status()
        return provider, result

    async def _serve(
        self,
        capability: Capability,
        operation: str,
        invoke: Callable[[MarketDataProvider], Awaitable[T]],
        default: T,
        *,
        user_id: Optional[str] = None,
    ) -> T:
        """`_serve_with_provider` for callers that return the provider's payload
        unchanged and so have no use for the provider itself."""
        _provider, result = await self._serve_with_provider(
            capability, operation, invoke, default, user_id=user_id,
        )
        return result

    # ── Single stock quote ───────────────────────────────

    async def get_quote(self, symbol: str, *, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch, normalize, validate a single stock quote."""
        try:
            provider, raw = await self._serve_with_provider(
                Capability.QUOTES, "get_quote",
                lambda p: p.fetch_quote(symbol), None, user_id=user_id,
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

        normalized = []
        for raw in raw_sectors:
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
            lambda p: p.fetch_chart(symbol, period), [], user_id=user_id,
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

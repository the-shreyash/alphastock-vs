"""Market Gateway — unified entry point for all market data access.

Wraps the existing real_market.py with normalization, validation, caching,
and event publishing. All market data flows through the gateway so the rest
of the platform never talks to provider-specific APIs directly.

Usage:
    from services.market_engine import MarketGateway
    gateway = MarketGateway()
    quote = await gateway.get_quote("RELIANCE")
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.market_engine.event_bus import event_bus
from services.market_engine.normalizer import (
    normalize_stock_quote,
    normalize_index_quote,
    normalize_news_article,
    normalize_sector_data,
)
from services.market_engine.validator import (
    validate_stock_quote,
    validate_index_quote,
    validate_batch,
    validate_sector_data,
    is_market_hours,
)

logger = logging.getLogger(__name__)


class MarketGateway:
    """Central gateway for all market data operations.

    Pipeline: Provider -> Normalize -> Validate -> Cache -> Event Bus
    """

    def __init__(self) -> None:
        self._initialized = False
        self._start_time: Optional[str] = None

    async def initialize(self) -> None:
        """Start the gateway and publish a readiness event."""
        self._start_time = datetime.now(timezone.utc).isoformat()
        self._initialized = True
        await event_bus.publish("market.gateway.ready", {
            "start_time": self._start_time,
            "market_hours": is_market_hours(),
        })
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
        }

    # ── Single stock quote ───────────────────────────────

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch, normalize, validate a single stock quote."""
        from services.real_market import fetch_real_stock_quote

        try:
            raw = await fetch_real_stock_quote(symbol)
        except Exception as exc:
            logger.warning(f"Gateway: quote fetch failed for {symbol}: {exc}")
            return None

        if not raw:
            return None

        normalized = normalize_stock_quote(raw, provider="yahoo")
        if not normalized:
            return None

        result = validate_stock_quote(normalized)
        if not result.valid:
            logger.warning(f"Gateway: quote validation failed for {symbol}: {result.errors}")
            return None

        await event_bus.publish("price.updated", {
            "symbol": symbol,
            "price": normalized.get("price"),
            "change_pct": normalized.get("change_pct"),
        })

        return normalized

    # ── Batch universe quotes ────────────────────────────

    async def get_universe_quotes(self) -> List[Dict[str, Any]]:
        """Fetch all universe quotes, normalize and validate."""
        from services.real_market import fetch_all_universe_quotes

        try:
            raw_quotes = await fetch_all_universe_quotes()
        except Exception as exc:
            logger.warning(f"Gateway: universe fetch failed: {exc}")
            return []

        if not raw_quotes:
            return []

        normalized = []
        for raw in raw_quotes:
            norm = normalize_stock_quote(raw, provider="yahoo")
            if norm:
                normalized.append(norm)

        valid, errors = validate_batch(normalized, validate_stock_quote)
        if errors:
            logger.debug(f"Gateway: {len(errors)} universe quotes rejected")

        return valid

    # ── Index quotes ─────────────────────────────────────

    async def get_indices(self) -> Dict[str, Any]:
        """Fetch market overview with normalized indices."""
        from services.real_market import fetch_real_market_overview

        try:
            overview = await fetch_real_market_overview()
        except Exception as exc:
            logger.warning(f"Gateway: overview fetch failed: {exc}")
            return {}

        if not overview:
            return {}

        for key in ("nifty", "bank_nifty", "sensex"):
            raw_idx = overview.get(key)
            if raw_idx:
                normalized = normalize_index_quote(raw_idx)
                if normalized:
                    vr = validate_index_quote(normalized)
                    if vr.valid:
                        overview[key] = normalized

        return overview

    # ── Sector data ──────────────────────────────────────

    async def get_sectors(self) -> List[Dict[str, Any]]:
        """Fetch, normalize, and validate sector performance."""
        from services.real_market import fetch_real_sectors

        try:
            raw_sectors = await fetch_real_sectors()
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

    async def get_gainers(self, count: int = 5) -> List[Dict[str, Any]]:
        from services.real_market import fetch_real_gainers
        return await fetch_real_gainers(count)

    async def get_losers(self, count: int = 5) -> List[Dict[str, Any]]:
        from services.real_market import fetch_real_losers
        return await fetch_real_losers(count)

    # ── Global markets ───────────────────────────────────

    async def get_global_markets(self) -> List[Dict[str, Any]]:
        from services.real_market import fetch_real_global_markets
        return await fetch_real_global_markets()

    # ── Commodities ──────────────────────────────────────

    async def get_commodities(self) -> List[Dict[str, Any]]:
        from services.real_market import fetch_real_commodities
        return await fetch_real_commodities()

    # ── FII / DII ────────────────────────────────────────

    async def get_fii_dii(self) -> Dict[str, Any]:
        from services.real_market import fetch_real_fii_dii
        return await fetch_real_fii_dii()

    # ── News (via news_service) ──────────────────────────

    async def get_news(self, force: bool = False) -> List[Dict[str, Any]]:
        from services.news_service import fetch_news

        try:
            raw_articles = await fetch_news(force=force)
        except Exception as exc:
            logger.warning(f"Gateway: news fetch failed: {exc}")
            return []

        normalized = [normalize_news_article(a) for a in (raw_articles or [])]
        return [a for a in normalized if a.get("title")]

    # ── Chart data ───────────────────────────────────────

    async def get_chart(self, symbol: str, period: str = "1D") -> Dict[str, Any]:
        from services.real_market import fetch_real_chart_data
        return await fetch_real_chart_data(symbol, period)

    # ── Chart patterns ───────────────────────────────────

    async def get_patterns(self, symbol: str) -> Dict[str, Any]:
        from services.real_market import detect_chart_patterns
        return await detect_chart_patterns(symbol)


# Module-level singleton
market_gateway = MarketGateway()

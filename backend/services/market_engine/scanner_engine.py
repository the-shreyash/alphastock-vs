"""Scanner Engine — advanced stock scanner with filters and strategy presets.

Scans the stock universe with configurable technical and fundamental filters,
then applies strategy-specific scoring to surface relevant opportunities.

Filters:
    price_min / price_max       Price range
    volume_min                  Minimum volume
    sector                      Sector filter
    rsi_min / rsi_max           RSI range
    change_pct_min / max        Day change range
    volume_ratio_min            Minimum volume ratio
    macd_bullish                MACD > signal only

Strategy presets:
    intraday    — High volume, moderate RSI, positive momentum
    swing       — MACD bullish crossover, healthy RSI, good volume
    momentum    — Strong day change, high volume, RSI > 50
    breakout    — Volume spike, positive change, RSI rising
    reversal    — Oversold RSI, volume pickup, MACD turning
    value       — Low RSI, stable sector, lower volatility
    growth      — Strong trend, sector leading, high momentum
    dividend    — Stable large-caps, moderate RSI, low volatility
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.market_engine.event_bus import event_bus

logger = logging.getLogger(__name__)


# ── Strategy presets ─────────────────────────────────

STRATEGY_PRESETS = {
    "intraday": {
        "label": "Intraday Scalp",
        "description": "High-volume stocks with momentum for same-day trades",
        "filters": {
            "volume_ratio_min": 1.3,
            "rsi_min": 40,
            "rsi_max": 70,
            "change_pct_min": 0.3,
        },
        "sort_key": "volume_ratio",
        "sort_desc": True,
    },
    "swing": {
        "label": "Swing Trade",
        "description": "MACD bullish crossover setups for 3-10 day holds",
        "filters": {
            "macd_bullish": True,
            "rsi_min": 40,
            "rsi_max": 65,
            "volume_ratio_min": 1.0,
        },
        "sort_key": "change_pct",
        "sort_desc": True,
    },
    "momentum": {
        "label": "Momentum Play",
        "description": "Strong uptrend with high participation",
        "filters": {
            "change_pct_min": 1.0,
            "rsi_min": 50,
            "volume_ratio_min": 1.2,
        },
        "sort_key": "change_pct",
        "sort_desc": True,
    },
    "breakout": {
        "label": "Breakout Scanner",
        "description": "Volume spikes with positive price action",
        "filters": {
            "volume_ratio_min": 1.8,
            "change_pct_min": 0.5,
        },
        "sort_key": "volume_ratio",
        "sort_desc": True,
    },
    "reversal": {
        "label": "Reversal Finder",
        "description": "Oversold stocks showing signs of recovery",
        "filters": {
            "rsi_max": 35,
            "volume_ratio_min": 1.0,
        },
        "sort_key": "rsi",
        "sort_desc": False,  # Lowest RSI first
    },
    "value": {
        "label": "Value Pick",
        "description": "Stable stocks at attractive technical levels",
        "filters": {
            "rsi_min": 30,
            "rsi_max": 50,
        },
        "sort_key": "rsi",
        "sort_desc": False,
    },
    "growth": {
        "label": "Growth Leader",
        "description": "Stocks in strong uptrends with sector leadership",
        "filters": {
            "macd_bullish": True,
            "rsi_min": 50,
            "change_pct_min": 0.5,
        },
        "sort_key": "change_pct",
        "sort_desc": True,
    },
    "dividend": {
        "label": "Dividend Stable",
        "description": "Large-cap stable stocks with low volatility",
        "filters": {
            "rsi_min": 35,
            "rsi_max": 65,
        },
        "sort_key": "volume_ratio",
        "sort_desc": False,  # Lower vol-ratio = more stable
    },
}


def get_presets() -> List[Dict[str, Any]]:
    """Return all available scanner presets for the frontend."""
    return [
        {
            "key": key,
            "label": preset["label"],
            "description": preset["description"],
        }
        for key, preset in STRATEGY_PRESETS.items()
    ]


def _passes_filters(quote: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    """Check if a quote passes all specified filters."""
    price = quote.get("price") or 0
    rsi = quote.get("rsi")
    volume = quote.get("volume") or 0
    volume_ratio = quote.get("volume_ratio") or 0
    change_pct = quote.get("change_pct") or 0
    macd = quote.get("macd") or 0
    macd_signal = quote.get("macd_signal") or 0
    sector = (quote.get("sector") or "").lower()

    if "price_min" in filters and price < filters["price_min"]:
        return False
    if "price_max" in filters and price > filters["price_max"]:
        return False
    if "volume_min" in filters and volume < filters["volume_min"]:
        return False
    if "sector" in filters and filters["sector"]:
        if sector != filters["sector"].lower():
            return False
    if rsi is not None:
        if "rsi_min" in filters and rsi < filters["rsi_min"]:
            return False
        if "rsi_max" in filters and rsi > filters["rsi_max"]:
            return False
    if "change_pct_min" in filters and change_pct < filters["change_pct_min"]:
        return False
    if "change_pct_max" in filters and change_pct > filters["change_pct_max"]:
        return False
    if "volume_ratio_min" in filters and volume_ratio < filters["volume_ratio_min"]:
        return False
    if filters.get("macd_bullish") and macd <= macd_signal:
        return False

    return True


async def scan(
    strategy: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    sector: Optional[str] = None,
    limit: int = 15,
    source: str = "api",
    publish: bool = True,
) -> Dict[str, Any]:
    """Run the scanner with optional strategy preset and/or custom filters.

    Pipeline:
        1. Fetch universe quotes via gateway
        2. Merge preset filters with custom filters (custom wins)
        3. Apply all filters
        4. Sort by the preset's sort key (or change_pct default)
        5. Return top N results with metadata

    Returns:
        {
            "strategy": str,
            "strategy_label": str,
            "results": [...],
            "total_scanned": int,
            "total_matched": int,
            "filters_applied": {...},
            "scanned_at": str,
        }
    """
    from services.market_engine.gateway import market_gateway

    quotes = await market_gateway.get_universe_quotes()
    if not quotes:
        return {
            "strategy": strategy,
            "strategy_label": "Custom",
            "results": [],
            "total_scanned": 0,
            "total_matched": 0,
            "filters_applied": {},
            "available": False,
            "note": "Live market data is temporarily unavailable.",
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    # Build effective filters
    effective_filters = {}
    preset = STRATEGY_PRESETS.get(strategy or "")
    if preset:
        effective_filters.update(preset["filters"])

    # Custom filters override preset
    if filters:
        effective_filters.update(filters)

    # Sector from param overrides filter
    if sector:
        effective_filters["sector"] = sector

    # Apply filters
    matched = [q for q in quotes if _passes_filters(q, effective_filters)]

    # Sort
    sort_key = "change_pct"
    sort_desc = True
    if preset:
        sort_key = preset.get("sort_key", "change_pct")
        sort_desc = preset.get("sort_desc", True)

    matched.sort(
        key=lambda q: q.get(sort_key) or 0,
        reverse=sort_desc,
    )

    results = matched[:limit]

    strategy_label = preset["label"] if preset else "Custom Scan"

    # `source` lets consumers tell worker-driven refreshes apart from
    # API-triggered scans — the frontend refetches only on "worker" events,
    # otherwise its own fetch would emit this event and loop (Sprint R4).
    if publish:
        await event_bus.publish("scanner.updated", {
            "source": source,
            "strategy": strategy or "custom",
            "matched": len(matched),
            "top": results[0]["symbol"] if results else None,
        })

    return {
        "strategy": strategy or "custom",
        "strategy_label": strategy_label,
        "results": results,
        "total_scanned": len(quotes),
        "total_matched": len(matched),
        "filters_applied": effective_filters,
        "available": True,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }

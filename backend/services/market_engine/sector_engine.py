"""Sector Engine — deep sector analysis with rotation detection.

Provides:
    - Sector strength scoring (momentum + breadth + leadership)
    - Sector rotation detection (money flow between sectors)
    - Per-sector leaders and laggards
    - Sector momentum classification
    - Sector breadth (advance/decline ratio within each sector)
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.market_engine.event_bus import event_bus

logger = logging.getLogger(__name__)


def _classify_momentum(change_pct: float) -> str:
    """Classify sector momentum from its change percentage."""
    if change_pct >= 1.5:
        return "strong_bullish"
    elif change_pct >= 0.5:
        return "bullish"
    elif change_pct >= -0.5:
        return "neutral"
    elif change_pct >= -1.5:
        return "bearish"
    else:
        return "strong_bearish"


def _compute_sector_breadth(stocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute advance/decline breadth for a list of stocks in a sector."""
    advancing = sum(1 for s in stocks if (s.get("change_pct") or 0) > 0)
    declining = sum(1 for s in stocks if (s.get("change_pct") or 0) < 0)
    unchanged = len(stocks) - advancing - declining
    total = len(stocks) or 1

    return {
        "advancing": advancing,
        "declining": declining,
        "unchanged": unchanged,
        "total": len(stocks),
        "advance_ratio": round(advancing / total, 2),
    }


def _strength_score(
    change_pct: float,
    breadth: Dict[str, Any],
    rank: int,
    total_sectors: int,
) -> float:
    """Compute a 0-100 sector strength score."""
    score = 50.0

    # Change contribution (±30)
    score += min(30, max(-30, change_pct * 15))

    # Breadth contribution (±20)
    advance_ratio = breadth.get("advance_ratio", 0.5)
    score += (advance_ratio - 0.5) * 40

    # Rank contribution (top = +10, bottom = -10)
    if total_sectors > 1:
        percentile = 1.0 - (rank / (total_sectors - 1))
        score += (percentile - 0.5) * 20

    return round(max(0, min(100, score)), 1)


async def analyze_sectors() -> Dict[str, Any]:
    """Run a comprehensive sector analysis.

    Returns:
        {
            "sectors": [
                {
                    "name": str,
                    "change_pct": float,
                    "momentum": str,
                    "strength_score": float,
                    "breadth": {...},
                    "leaders": [...],
                    "laggards": [...],
                    "stock_count": int,
                },
                ...
            ],
            "rotation": {
                "inflow": [sector names gaining strength],
                "outflow": [sector names losing strength],
            },
            "top_sector": str,
            "weakest_sector": str,
            "analyzed_at": str,
        }
    """
    from services.market_engine.gateway import market_gateway

    quotes, raw_sectors = await asyncio.gather(
        market_gateway.get_universe_quotes(),
        market_gateway.get_sectors(),
    )

    if not raw_sectors:
        return {
            "sectors": [],
            "rotation": {"inflow": [], "outflow": []},
            "top_sector": None,
            "weakest_sector": None,
            "available": False,
            "note": "Sector data temporarily unavailable.",
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }

    # Group stocks by sector
    stocks_by_sector: Dict[str, List[Dict]] = {}
    for q in (quotes or []):
        sec = q.get("sector", "")
        if sec:
            stocks_by_sector.setdefault(sec, []).append(q)

    total_sectors = len(raw_sectors)
    enriched_sectors = []

    for rank, raw_sec in enumerate(raw_sectors):
        name = raw_sec.get("name") or raw_sec.get("sector", "")
        change_pct = raw_sec.get("change_pct") or 0

        sector_stocks = stocks_by_sector.get(name, [])
        sector_stocks.sort(key=lambda s: s.get("change_pct") or 0, reverse=True)

        breadth = _compute_sector_breadth(sector_stocks)
        momentum = _classify_momentum(change_pct)
        strength = _strength_score(change_pct, breadth, rank, total_sectors)

        leaders = [
            {"symbol": s["symbol"], "name": s.get("name", ""), "change_pct": s.get("change_pct", 0)}
            for s in sector_stocks[:3]
        ]
        laggards = [
            {"symbol": s["symbol"], "name": s.get("name", ""), "change_pct": s.get("change_pct", 0)}
            for s in sector_stocks[-3:] if sector_stocks
        ]

        enriched_sectors.append({
            "name": name,
            "change_pct": round(change_pct, 2),
            "momentum": momentum,
            "strength_score": strength,
            "breadth": breadth,
            "leaders": leaders,
            "laggards": laggards,
            "stock_count": len(sector_stocks),
        })

    # Sort by strength score for rotation analysis
    enriched_sectors.sort(key=lambda s: s["strength_score"], reverse=True)

    # Rotation: top 3 = inflow, bottom 3 = outflow
    inflow = [s["name"] for s in enriched_sectors[:3] if s["change_pct"] > 0]
    outflow = [s["name"] for s in enriched_sectors[-3:] if s["change_pct"] < 0]

    top = enriched_sectors[0]["name"] if enriched_sectors else None
    weakest = enriched_sectors[-1]["name"] if enriched_sectors else None

    await event_bus.publish("sector.analyzed", {
        "count": len(enriched_sectors),
        "top_sector": top,
        "weakest_sector": weakest,
        "inflow": inflow,
        "outflow": outflow,
    })

    return {
        "sectors": enriched_sectors,
        "rotation": {"inflow": inflow, "outflow": outflow},
        "top_sector": top,
        "weakest_sector": weakest,
        "available": True,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

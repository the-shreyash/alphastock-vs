"""Ranking Engine — multi-dimensional stock scoring system.

Scores each stock across multiple dimensions and produces an overall
opportunity score. The ranking engine does NOT make investment decisions;
it provides structured scores to the AI system and scanner.

Dimensions:
    momentum    — RSI zone, day change, short-term price action
    trend       — MACD position, EMA alignment, trend direction
    volume      — Volume ratio, participation, accumulation
    risk        — Volatility, drawdown proximity, circuit risk
    news        — Sentiment score from news pipeline
    sector      — Sector relative strength, rotation position
    liquidity   — Avg volume, market cap tier
    ai_confidence — Composite conviction from technical + pattern signals

Overall opportunity_score is a weighted composite of all dimensions.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from services.market_engine.event_bus import event_bus

logger = logging.getLogger(__name__)

# Dimension weights (sum to 1.0)
DIMENSION_WEIGHTS = {
    "momentum": 0.20,
    "trend": 0.18,
    "volume": 0.15,
    "risk": 0.12,
    "news": 0.08,
    "sector": 0.10,
    "liquidity": 0.07,
    "ai_confidence": 0.10,
}



def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def score_momentum(quote: Dict[str, Any]) -> Tuple[float, str]:
    """Score momentum from RSI and day change. Returns (0-100, reason)."""
    rsi = quote.get("rsi") or 50.0
    change_pct = quote.get("change_pct") or 0.0

    score = 50.0
    reasons = []

    # RSI positioning
    if 50 <= rsi <= 65:
        score += 25
        reasons.append(f"RSI {rsi:.0f} in bullish zone")
    elif 40 <= rsi < 50:
        score += 10
        reasons.append(f"RSI {rsi:.0f} neutral-constructive")
    elif 30 <= rsi < 40:
        score += 15
        reasons.append(f"RSI {rsi:.0f} oversold bounce potential")
    elif rsi < 30:
        score += 20
        reasons.append(f"RSI {rsi:.0f} deeply oversold")
    elif 65 < rsi <= 75:
        score += 5
        reasons.append(f"RSI {rsi:.0f} strong but nearing overbought")
    else:
        score -= 10
        reasons.append(f"RSI {rsi:.0f} overbought")

    # Day change
    if change_pct >= 2.0:
        score += 20
        reasons.append(f"Strong +{change_pct:.1f}% day move")
    elif change_pct >= 0.5:
        score += 10
        reasons.append(f"Positive {change_pct:+.1f}% today")
    elif change_pct <= -2.0:
        score -= 10
        reasons.append(f"Weak {change_pct:.1f}% decline")

    return _clamp(score), "; ".join(reasons)


def score_trend(quote: Dict[str, Any]) -> Tuple[float, str]:
    """Score trend from MACD position. Returns (0-100, reason)."""
    macd = quote.get("macd") or 0.0
    macd_signal = quote.get("macd_signal") or 0.0

    score = 50.0
    reasons = []

    if macd > macd_signal:
        score += 30
        reasons.append("MACD bullish crossover")
        if macd > 0:
            score += 10
            reasons.append("MACD above zero line")
    else:
        score -= 10
        reasons.append("MACD bearish")
        if macd < 0:
            score -= 5

    return _clamp(score), "; ".join(reasons)


def score_volume(quote: Dict[str, Any]) -> Tuple[float, str]:
    """Score volume participation. Returns (0-100, reason)."""
    volume_ratio = quote.get("volume_ratio") or 1.0

    score = 50.0
    reasons = []

    if volume_ratio >= 2.0:
        score += 35
        reasons.append(f"Very high volume {volume_ratio:.1f}x avg")
    elif volume_ratio >= 1.5:
        score += 25
        reasons.append(f"Elevated volume {volume_ratio:.1f}x avg")
    elif volume_ratio >= 1.1:
        score += 10
        reasons.append(f"Above-avg volume {volume_ratio:.1f}x")
    elif volume_ratio < 0.5:
        score -= 15
        reasons.append(f"Low volume {volume_ratio:.1f}x avg")

    return _clamp(score), "; ".join(reasons)


def score_risk(quote: Dict[str, Any]) -> Tuple[float, str]:
    """Score risk (higher = lower risk = better). Returns (0-100, reason)."""
    change_pct = abs(quote.get("change_pct") or 0.0)
    rsi = quote.get("rsi") or 50.0

    # Start high (low risk), deduct for risk factors
    score = 80.0
    reasons = []

    if change_pct > 5.0:
        score -= 30
        reasons.append(f"High volatility {change_pct:.1f}% move")
    elif change_pct > 3.0:
        score -= 15
        reasons.append(f"Elevated volatility {change_pct:.1f}%")

    if rsi > 75 or rsi < 25:
        score -= 15
        reasons.append(f"Extreme RSI {rsi:.0f}")

    if not reasons:
        reasons.append("Moderate risk profile")

    return _clamp(score), "; ".join(reasons)


def score_news(sentiment_score: float = 0.5) -> Tuple[float, str]:
    """Score news sentiment (0-1 input). Returns (0-100, reason)."""
    score = sentiment_score * 100
    if sentiment_score >= 0.7:
        reason = "Positive news sentiment"
    elif sentiment_score <= 0.3:
        reason = "Negative news sentiment"
    else:
        reason = "Neutral news sentiment"
    return _clamp(score), reason


def score_sector(
    sector_rank: Optional[int] = None,
    total_sectors: int = 12,
    sector_change: Optional[float] = None,
) -> Tuple[float, str]:
    """Score sector relative strength. Returns (0-100, reason)."""
    score = 50.0
    reasons = []

    if sector_rank is not None and total_sectors > 0:
        # Top third = strong, bottom third = weak
        percentile = 1.0 - (sector_rank / total_sectors)
        score = percentile * 100
        if percentile > 0.66:
            reasons.append(f"Sector ranked #{sector_rank + 1}/{total_sectors} (leading)")
        elif percentile > 0.33:
            reasons.append(f"Sector ranked #{sector_rank + 1}/{total_sectors} (mid)")
        else:
            reasons.append(f"Sector ranked #{sector_rank + 1}/{total_sectors} (lagging)")

    if sector_change is not None:
        if sector_change > 1.0:
            score += 10
            reasons.append(f"Sector up {sector_change:+.1f}%")
        elif sector_change < -1.0:
            score -= 10
            reasons.append(f"Sector down {sector_change:.1f}%")

    if not reasons:
        reasons.append("Sector data unavailable")

    return _clamp(score), "; ".join(reasons)


def score_liquidity(quote: Dict[str, Any]) -> Tuple[float, str]:
    """Score liquidity from avg volume. Returns (0-100, reason)."""
    avg_vol = quote.get("avg_volume") or 0

    if avg_vol >= 5_000_000:
        return 90.0, "Very high liquidity"
    elif avg_vol >= 1_000_000:
        return 75.0, "Good liquidity"
    elif avg_vol >= 500_000:
        return 60.0, "Moderate liquidity"
    elif avg_vol >= 100_000:
        return 40.0, "Low liquidity"
    else:
        return 25.0, "Very low liquidity"


def score_ai_confidence(
    quote: Dict[str, Any],
    patterns: Optional[List[Dict]] = None,
) -> Tuple[float, str]:
    """Composite AI confidence from technicals + patterns."""
    score = 50.0
    reasons = []

    # Technical composite
    rsi = quote.get("rsi") or 50
    vol_ratio = quote.get("volume_ratio") or 1.0
    macd = quote.get("macd") or 0
    macd_signal = quote.get("macd_signal") or 0

    if 45 <= rsi <= 65 and vol_ratio >= 1.2 and macd > macd_signal:
        score += 30
        reasons.append("Strong technical alignment")
    elif macd > macd_signal and vol_ratio >= 1.0:
        score += 15
        reasons.append("Moderate technical setup")

    # Pattern signals
    if patterns:
        for p in patterns[:2]:
            if p.get("signal") == "bullish":
                score += 12
                reasons.append(f"Bullish {p.get('pattern', 'pattern')}")
            elif p.get("signal") == "bearish":
                score -= 8
                reasons.append(f"Bearish {p.get('pattern', 'pattern')}")

    if not reasons:
        reasons.append("Neutral technical setup")

    return _clamp(score), "; ".join(reasons)


def rank_stock(
    quote: Dict[str, Any],
    patterns: Optional[List[Dict]] = None,
    news_sentiment: float = 0.5,
    sector_rank: Optional[int] = None,
    total_sectors: int = 12,
    sector_change: Optional[float] = None,
) -> Dict[str, Any]:
    """Rank a single stock across all dimensions.

    Returns a dict with per-dimension scores, reasons, and overall
    opportunity_score (0-100 weighted composite).
    """
    dimensions = {}

    m_score, m_reason = score_momentum(quote)
    dimensions["momentum"] = {"score": m_score, "reason": m_reason}

    t_score, t_reason = score_trend(quote)
    dimensions["trend"] = {"score": t_score, "reason": t_reason}

    v_score, v_reason = score_volume(quote)
    dimensions["volume"] = {"score": v_score, "reason": v_reason}

    r_score, r_reason = score_risk(quote)
    dimensions["risk"] = {"score": r_score, "reason": r_reason}

    n_score, n_reason = score_news(news_sentiment)
    dimensions["news"] = {"score": n_score, "reason": n_reason}

    s_score, s_reason = score_sector(sector_rank, total_sectors, sector_change)
    dimensions["sector"] = {"score": s_score, "reason": s_reason}

    l_score, l_reason = score_liquidity(quote)
    dimensions["liquidity"] = {"score": l_score, "reason": l_reason}

    a_score, a_reason = score_ai_confidence(quote, patterns)
    dimensions["ai_confidence"] = {"score": a_score, "reason": a_reason}

    # Weighted composite
    opportunity_score = sum(
        dimensions[dim]["score"] * weight
        for dim, weight in DIMENSION_WEIGHTS.items()
    )
    opportunity_score = round(_clamp(opportunity_score), 1)

    # Signal classification
    if opportunity_score >= 75:
        signal = "strong_buy"
    elif opportunity_score >= 60:
        signal = "buy"
    elif opportunity_score >= 45:
        signal = "neutral"
    elif opportunity_score >= 30:
        signal = "sell"
    else:
        signal = "strong_sell"

    return {
        "symbol": quote.get("symbol", ""),
        "name": quote.get("name", ""),
        "price": quote.get("price"),
        "change_pct": quote.get("change_pct"),
        "sector": quote.get("sector", ""),
        "opportunity_score": opportunity_score,
        "signal": signal,
        "dimensions": dimensions,
    }


async def rank_universe(
    top_n: int = 10,
    sector_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Rank the entire stock universe and return top-N opportunities.

    Fetches live data, sectors, and patterns concurrently, then scores
    each stock through the ranking engine.
    """
    from services.market_engine.gateway import market_gateway

    quotes, sectors = await asyncio.gather(
        market_gateway.get_universe_quotes(),
        market_gateway.get_sectors(),
    )

    if not quotes:
        return []

    # Build sector lookup
    sector_rank_map = {}
    sector_change_map = {}
    for i, s in enumerate(sectors):
        name = s.get("name") or s.get("sector", "")
        sector_rank_map[name] = i
        sector_change_map[name] = s.get("change_pct", 0)
    total_sectors = len(sectors) or 1

    # Optional sector filter
    if sector_filter:
        sf = sector_filter.strip().lower()
        quotes = [q for q in quotes if (q.get("sector") or "").lower() == sf]

    # Score each stock
    ranked = []
    for q in quotes:
        sector = q.get("sector", "")
        ranking = rank_stock(
            quote=q,
            patterns=None,  # Skip patterns for bulk ranking (too slow per-stock)
            news_sentiment=0.5,
            sector_rank=sector_rank_map.get(sector),
            total_sectors=total_sectors,
            sector_change=sector_change_map.get(sector),
        )
        ranked.append(ranking)

    # Sort by opportunity_score descending
    ranked.sort(key=lambda r: r["opportunity_score"], reverse=True)

    await event_bus.publish("scanner.updated", {
        "ranked_count": len(ranked),
        "top_symbol": ranked[0]["symbol"] if ranked else None,
        "top_score": ranked[0]["opportunity_score"] if ranked else None,
    })

    return ranked[:top_n]

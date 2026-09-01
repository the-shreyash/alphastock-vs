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
from datetime import datetime, timezone
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


#: Which quote fields each dimension actually reads.
#:
#: D5.19 — THE DIFFERENCE BETWEEN "NEUTRAL" AND "WE DO NOT KNOW".
#:
#: Every scorer above coalesces its inputs — `quote.get("rsi") or 50.0`,
#: `macd or 0.0`, `avg_volume or 0` — so an absent input scores as a real
#: reading and, worse, produces a real-sounding sentence. Measured live on
#: 2026-09-01, when the universe path carried no technicals at all, the engine
#: reported for RELIANCE (8.3M shares traded that morning):
#:
#:     momentum   95.0  "RSI 50 in bullish zone; Strong +2.6% day move"
#:     trend      40.0  "MACD bearish"
#:     liquidity  25.0  "Very low liquidity"
#:
#: Three sentences, one of which came from the market. Five of the eight
#: dimensions were byte-identical across every ranked stock.
#:
#: `derive_technicals` now supplies those inputs, which fixes today's instance.
#: This table fixes the class: a newly listed stock has no 26-bar MACD, a
#: suspended one has no volume, and on those the coalescing returns. Since the
#: product is about to render these sentences to a user as the reason to buy
#: something, the engine has to be able to say "I do not know" — and the only
#: way to say it is to check the inputs before trusting the output.
#:
#: The SCORES are deliberately unchanged. Withholding a reason changes what the
#: platform claims; changing a weight changes what it recommends, and the brief
#: is explicit that the scoring logic stands unless the audit proves it wrong.
#: It proved the explanations wrong, not the arithmetic.
DIMENSION_INPUTS: Dict[str, Tuple[str, ...]] = {
    "momentum": ("rsi", "change_pct"),
    "trend": ("macd", "macd_signal"),
    "volume": ("volume_ratio",),
    "risk": ("change_pct", "rsi"),
    "liquidity": ("avg_volume",),
}

#: Dimensions whose inputs do not come from the quote at all.
#:
#: `news` is passed a sentiment score by the caller (`rank_universe` passes a
#: literal 0.5 — see LIM-D5.19-3), `sector` is passed a rank and a change, and
#: `ai_confidence` is a composite of the technicals plus optional patterns.
#: Their availability is decided at the call site, not by this table.
_DIMENSIONS_SCORED_FROM_ARGUMENTS = ("news", "sector", "ai_confidence")

#: A dimension at exactly this score said nothing either way.
NEUTRAL_SCORE = 50.0

#: How many reasons the explanation carries.
#:
#: Three, because the surface is a card under a price and the question is "why
#: this stock", not "dump the model". The list is ordered by contribution, so
#: the three that survive are the three that actually moved the score.
MAX_EVIDENCE_ITEMS = 3


def dimension_is_supported(dimension: str, quote: Dict[str, Any]) -> bool:
    """Whether `quote` carries the inputs `dimension` claims to have scored.

    A dimension with no entry in :data:`DIMENSION_INPUTS` is scored from the
    caller's arguments rather than the quote, and is not this function's to
    answer — it returns True and the caller decides.
    """
    required = DIMENSION_INPUTS.get(dimension)
    if required is None:
        return True
    # EVERY input, not any of them. The scorers append one clause per input, so
    # a dimension with one input missing does not produce a shorter sentence —
    # it produces the same sentence with a fabricated clause in it. `momentum`
    # scored on a real +2.6% day move and an absent RSI still reads
    # "RSI 50 in bullish zone; Strong +2.6% day move", and the half that is
    # invented is the half a reader would weigh most.
    return all(quote.get(field) is not None for field in required)


def build_evidence(dimensions: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The ranked "why this stock" list, drawn only from what was actually scored.

    Three rules, each of which exists because breaking it produces a sentence
    the platform cannot stand behind:

    * **Only available dimensions.** An absent MACD is not a reason to buy.
    * **Only non-neutral scores.** A dimension sitting at 50 contributed
      nothing; listing it pads the explanation to look thorough.
    * **Only the scorer's own words.** The strings are lifted verbatim from the
      dimension that produced them and are never composed here, which is what
      `test_evidence_text_is_the_scorers_own_reason` enforces.

    Ordered by weighted distance from neutral, so the first line is the factor
    that most moved the score rather than the first key in a dict.
    """
    scored = []
    for dim, info in dimensions.items():
        if not info.get("available"):
            continue
        reason = (info.get("reason") or "").strip()
        if not reason:
            continue
        score = info.get("score")
        if score is None or score == NEUTRAL_SCORE:
            continue
        contribution = round(
            (score - NEUTRAL_SCORE) * DIMENSION_WEIGHTS.get(dim, 0.0), 2
        )
        if not contribution:
            continue
        scored.append(
            {
                "dimension": dim,
                "score": score,
                "reason": reason,
                "contribution": contribution,
            }
        )

    scored.sort(key=lambda item: abs(item["contribution"]), reverse=True)
    return scored[:MAX_EVIDENCE_ITEMS]


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

    def _record(name: str, scored: Tuple[float, str], available: bool = True) -> None:
        """One dimension's score, and whether its reason may be shown.

        The score is recorded either way — withholding it would change
        `opportunity_score`, and this sprint deliberately does not. The reason
        is dropped when the inputs were absent, because a sentence the data
        does not support is worse than no sentence. See DIMENSION_INPUTS.
        """
        score, reason = scored
        supported = available and dimension_is_supported(name, quote)
        dimensions[name] = {
            "score": score,
            "reason": reason if supported else None,
            "available": supported,
        }

    _record("momentum", score_momentum(quote))
    _record("trend", score_trend(quote))
    _record("volume", score_volume(quote))
    _record("risk", score_risk(quote))
    # `news_sentiment` is a caller argument, and `rank_universe` passes a fixed
    # 0.5 for every stock (LIM-D5.19-3). A constant is not evidence about a
    # particular stock, so the dimension is unavailable at exactly the neutral
    # value the caller supplies when it has nothing real to say.
    _record("news", score_news(news_sentiment), available=news_sentiment != 0.5)
    _record(
        "sector",
        score_sector(sector_rank, total_sectors, sector_change),
        available=sector_rank is not None or sector_change is not None,
    )
    _record("liquidity", score_liquidity(quote))
    # A composite of the technicals plus optional patterns: it can only speak
    # when at least one of the technicals it reads is present.
    _record(
        "ai_confidence",
        score_ai_confidence(quote, patterns),
        available=bool(patterns) or dimension_is_supported("momentum", quote),
    )

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
        # The user-facing answer to "why this stock?", assembled from the
        # dimensions above and from nothing else. May legitimately be empty —
        # see `build_evidence` and CLAUDE.md's rule against inventing data.
        "evidence": build_evidence(dimensions),
    }


async def rank_universe(
    top_n: int = 10,
    sector_filter: Optional[str] = None,
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Rank the entire stock universe and return top-N opportunities.

    Fetches live data and sectors concurrently, then scores each stock.

    D5.19 — `user_id` IS WHAT LETS A CONNECTED BROKER SERVE THIS SURFACE.
    Without it every call resolved in `GLOBAL_CONTEXT`, where the only
    registered provider is the platform's delayed baseline — so a user with an
    authenticated, healthy, promoted broker feed covering all 31 universe
    symbols could not be served by it here at any ranking, because their
    identity never reached the resolver. D5.18 watched that happen: 266
    `market.tick` events in 40 seconds on a promoted per-user broker feed,
    while Top Opportunities showed delayed prices throughout.

    Optional, because this is a market-wide read a signed-out visitor may make.
    Absent, it resolves the platform baseline — exactly what it did for
    everybody before.
    """
    from services.market_engine.gateway import market_gateway

    quotes, sectors = await asyncio.gather(
        market_gateway.get_universe_quotes(user_id=user_id),
        # Sectors are scoped too. A ranking whose prices came from a broker and
        # whose sector rotation came from the platform baseline would score a
        # live price against a stale sector, and the mismatch would be invisible
        # in the output.
        market_gateway.get_sectors(user_id=user_id),
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


async def rank_universe_report(
    top_n: int = 10,
    sector_filter: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """`rank_universe` plus the freshness of the data it ranked.

    D5.19 — WHY THE TIER IS PART OF THE RANKING AND NOT A SEPARATE CALL.
    `/market/ranking` used to return rows and nothing else, so the surface
    rendering them — "Top Opportunities", the most prominent recommendation on
    the dashboard — had no way to say whether it was showing a live broker
    price or a 15-second-delayed baseline. A user with a connected broker saw
    delayed prices with no indication that they were delayed.

    The tier is read with the SAME `user_id` the quotes were resolved with. Any
    other value would describe a different resolution than the one that
    produced these rows — the label has to come from the answer, not from a
    second question. It is `source_tier` and never a provider name
    (MARKET_DATA_ARCHITECTURE.md, Developer Rule 4).
    """
    from services.market_engine.gateway import market_gateway
    from services.market_engine.providers.base import Capability

    ranked = await rank_universe(
        top_n=top_n, sector_filter=sector_filter, user_id=user_id
    )
    return {
        "rankings": ranked,
        "count": len(ranked),
        "available": bool(ranked),
        "source_tier": market_gateway.source_tier(
            Capability.UNIVERSE_QUOTES, user_id=user_id
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

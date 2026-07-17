"""Gift Nifty collector — pre-market indicator for the Indian session.

Gift Nifty (formerly SGX Nifty) is a Nifty 50 futures contract traded on NSE
International Exchange (NSE IX) at GIFT City. Because it trades while the NSE
cash market is closed, it is the single best pre-market read on where Nifty is
likely to open — which is exactly why the Morning Report leads with it.

Why this module has no working provider today
─────────────────────────────────────────────
Gift Nifty is **not** available from any feed this platform currently holds:

    Yahoo Finance     no ticker exists (probed: GIFTNIFTY, NIFTY_F1.NS, IN=F)
    Alpha Vantage     no Indian index futures coverage
    Zerodha Kite      NSE/BSE instruments only — NSE IX is a separate venue

Obtaining it requires an NSE IX data subscription or a licensed vendor feed.
Rather than derive, interpolate, or otherwise invent a number — CLAUDE.md and
MARKET_DATA_ARCHITECTURE.md both forbid simulating production market data — this
collector reports the quote as explicitly unavailable, with a reason the UI shows
verbatim. An honest gap is a feature: a trader who sees "awaiting licensed feed"
knows to look elsewhere, whereas a trader shown a plausible fabricated number
does not.

How a real feed plugs in
────────────────────────
Register an adapter at startup and every consumer — Morning Report, AI context,
frontend — picks it up with no further change:

    from services.market_engine import gift_nifty

    async def _nse_ix_quote():
        ...  # returns {"value": float, "previous_close": float} or None

    gift_nifty.register_adapter("nse_ix", _nse_ix_quote, tier="streaming")

Adapters are tried in registration order and the first non-None result wins,
mirroring the provider-priority model in MARKET_DATA_ARCHITECTURE.md (broker /
licensed feed first, polled baseline last). An adapter that raises is logged and
skipped so one bad provider can never take the Morning Report down with it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from services.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

CACHE_KEY = "market_engine:gift_nifty"
CACHE_TTL = 60  # Gift Nifty moves continuously; a stale pre-market read misleads.

# An adapter returns a raw quote — {"value", "previous_close"} — or None when the
# provider has nothing. Normalization into the platform shape happens here, so
# adapters stay thin and provider-shaped.
GiftNiftyAdapter = Callable[[], Awaitable[Optional[Dict[str, Any]]]]

# (name, adapter, tier) in priority order.
_adapters: List[Tuple[str, GiftNiftyAdapter, str]] = []

UNAVAILABLE_NOTE = (
    "Gift Nifty requires an NSE IX data subscription, which is not connected. "
    "No pre-market futures quote is available."
)


def register_adapter(name: str, adapter: GiftNiftyAdapter, *, tier: str = "delayed") -> None:
    """Register a Gift Nifty provider. Later registrations are lower priority.

    `tier` is the freshness contract the AI calibrates its language against
    ("streaming" → "live", "delayed" → "as of HH:MM"). It never identifies the
    provider — per AI_AGENT_SYSTEM.md the AI must not know who produced a quote.
    """
    if any(existing == name for existing, _, _ in _adapters):
        logger.warning("Gift Nifty adapter %r already registered — ignoring", name)
        return
    _adapters.append((name, adapter, tier))
    logger.info("Gift Nifty adapter registered: %s (tier=%s)", name, tier)


def reset_adapters() -> None:
    """Drop all adapters. Tests only."""
    _adapters.clear()


def _unavailable() -> Dict[str, Any]:
    return {
        "available": False,
        "value": None,
        "change": None,
        "change_pct": None,
        "previous_close": None,
        "source_tier": None,
        "note": UNAVAILABLE_NOTE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _normalize(raw: Dict[str, Any], tier: str) -> Optional[Dict[str, Any]]:
    """Turn a provider quote into the platform shape, or None if unusable.

    A Gift Nifty print without a previous close still carries signal (the level
    itself), so `change`/`change_pct` degrade to None rather than rejecting the
    whole quote.
    """
    value = raw.get("value")
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        logger.warning("Gift Nifty: rejecting non-positive value %r", value)
        return None

    prev = raw.get("previous_close")
    change = change_pct = None
    if prev is not None:
        try:
            prev = float(prev)
            if prev > 0:
                change = round(value - prev, 2)
                change_pct = round((value - prev) / prev * 100, 2)
            else:
                prev = None
        except (TypeError, ValueError):
            prev = None

    return {
        "available": True,
        "value": round(value, 2),
        "change": change,
        "change_pct": change_pct,
        "previous_close": prev,
        "source_tier": tier,
        "note": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_gift_nifty(force: bool = False) -> Dict[str, Any]:
    """Current Gift Nifty quote, or an explicit unavailable payload.

    Always returns a dict — callers render `available` and never need to guard
    for None. Unavailability is cached too: with no adapters registered the
    answer cannot change within the TTL, and callers shouldn't retry a known gap
    on every request.
    """
    if not force:
        cached = await cache_get(CACHE_KEY)
        if cached:
            return cached

    result: Optional[Dict[str, Any]] = None
    for name, adapter, tier in _adapters:
        try:
            raw = await adapter()
        except Exception as exc:
            logger.warning("Gift Nifty adapter %r failed: %s", name, exc)
            continue
        if not raw:
            continue
        normalized = _normalize(raw, tier)
        if normalized:
            result = normalized
            break
        logger.warning("Gift Nifty adapter %r returned an unusable quote", name)

    if result is None:
        result = _unavailable()

    await cache_set(CACHE_KEY, result, CACHE_TTL)
    return result

"""Scanner Worker — novelty detection for the continuous live scanner (Sprint R4).

The heartbeat engine re-runs the breakout / volume / momentum scans every
couple of minutes, so the same stock would re-trigger the same hit on every
cycle. This module is the flood gate: `filter_novel()` keeps a per
(kind, symbol) cooldown so a published `scanner.*` hit event always means a
NEW opportunity, never a repeat of one already streamed to the feed.

It also holds the momentum detector: pure functions over the cached universe
quotes (no network here) that compare each cycle's day-change against the
previous cycle's snapshot, so `scanner.momentum` fires only for stocks that
are strongly up AND still accelerating — not for anything merely sitting on a
big green candle all day.

State is process-local by design: only the heartbeat process produces these
events, the hot path is sub-millisecond, and a restart harmlessly re-arms the
cooldowns. `reset_state()` exists for test isolation.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────
HIT_COOLDOWN_MINUTES = 30    # matches heartbeat's ALERT_DEDUP_MINUTES
MOMENTUM_MIN_CHANGE_PCT = 2.0  # day change required to qualify as momentum
MOMENTUM_MIN_ACCEL = 0.3     # extra % gained since the previous cycle

# ── Worker state (process-local) ──────────────────────────────────────────
_recent_hits: Dict[Tuple[str, str], datetime] = {}
_prev_change: Dict[str, float] = {}


def reset_state() -> None:
    """Clear all worker state (test isolation)."""
    _recent_hits.clear()
    _prev_change.clear()


def filter_novel(
    kind: str,
    candidates: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return only candidates not seen for `kind` within the cooldown window.

    Survivors are recorded so the next cycle suppresses them; expired entries
    are pruned to bound memory. Kinds are independent — a breakout hit does
    not suppress a volume-spike hit for the same symbol.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=HIT_COOLDOWN_MINUTES)

    for key, seen_at in list(_recent_hits.items()):
        if seen_at < cutoff:
            del _recent_hits[key]

    novel = []
    for candidate in candidates or []:
        symbol = candidate.get("symbol")
        if not symbol:
            continue
        key = (kind, symbol)
        if key in _recent_hits:
            continue
        _recent_hits[key] = now
        novel.append(candidate)
    return novel


def detect_momentum(
    quotes: List[Dict[str, Any]],
    prev_change: Dict[str, float],
) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    """Pure momentum pass over one cycle of universe quotes.

    A stock qualifies when its day change is >= MOMENTUM_MIN_CHANGE_PCT and it
    is either newly above that threshold or has accelerated by at least
    MOMENTUM_MIN_ACCEL since the previous cycle's snapshot. Returns the
    candidates (quote + prev_change_pct + acceleration) and the new snapshot
    map for the next cycle.
    """
    candidates: List[Dict[str, Any]] = []
    new_snapshot: Dict[str, float] = {}

    for quote in quotes or []:
        symbol = quote.get("symbol")
        change_pct = quote.get("change_pct")
        if not symbol or change_pct is None:
            continue
        new_snapshot[symbol] = change_pct

        if change_pct < MOMENTUM_MIN_CHANGE_PCT:
            continue

        previous = prev_change.get(symbol)
        newly_hot = previous is None or previous < MOMENTUM_MIN_CHANGE_PCT
        acceleration = round(change_pct - previous, 2) if previous is not None else None
        accelerating = acceleration is not None and acceleration >= MOMENTUM_MIN_ACCEL

        if newly_hot or accelerating:
            candidates.append({
                **quote,
                "prev_change_pct": previous,
                "acceleration": acceleration,
            })

    candidates.sort(key=lambda q: q.get("change_pct") or 0, reverse=True)
    return candidates, new_snapshot


def momentum_pass(quotes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stateful wrapper: run detect_momentum against the previous cycle's
    snapshot and persist the new snapshot for the next cycle."""
    global _prev_change
    candidates, _prev_change = detect_momentum(quotes, _prev_change)
    return candidates

"""Live Portfolio Stream (Sprint R5).

Closes the audit's §4.8 Portfolio gap (REALTIME_MIGRATION_PLAN.md): the doc's
flow *Broker WebSocket → Portfolio Service → Redis → Socket → Portfolio Card →
PnL Updated → Number Animation → Allocation Chart Updates* needs a server-side
recompute step that turns raw price movement into a per-user portfolio snapshot
event. This module is that step.

Producers:
  • Heartbeat ``task_monitor_portfolio``    → reason ``monitor`` (periodic)
  • Broker tick stream (``broker_engine``)  → reason ``broker_tick`` (throttled)
  • Broker portfolio sync                   → reason ``broker_sync``

Each producer ends in ``publish_snapshot`` which publishes a
``portfolio.updated`` event onto the market event bus. The R2 event bridge
delivers it per-user (payload carries ``user_id``) on the ``portfolio``
channel, so only that user's open sockets receive it.

The snapshot itself is computed by ``services.portfolio_engine`` (the single
source of truth for holdings/P&L/allocation) — this module never re-implements
portfolio math. No fabricated data: when a live quote is missing the engine
falls back to the last real broker mark, and users without holdings emit
nothing.

Broker ticks arrive multiple times per second, so tick-driven recomputes are
throttled per user (``TICK_EMIT_INTERVAL``). State is process-local (matches
the single-heartbeat deployment model, same trade-off as scanner_worker);
``reset_state()`` exists for tests.
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Optional

from services import portfolio_engine

logger = logging.getLogger(__name__)

# Minimum seconds between tick-driven snapshot emissions per user. Periodic
# (monitor/sync) emissions bypass the gate but still stamp it, so a tick right
# after a monitor pass does not double-emit.
TICK_EMIT_INTERVAL = 3.0

# Per-user last emission time (time.monotonic()).
_last_emit: dict = {}

# Bound + staleness horizon for the throttle map (PH3.6).
#
# Every entry here is a float that stops being meaningful `TICK_EMIT_INTERVAL`
# seconds after it is written — but nothing removed it, so the map retained one
# key per user who had ever received a tick, for the life of the process. Each
# entry is small; the objection is not its size but its shape. It is a per-user
# structure with no expiry, which grows monotonically with cumulative signups
# rather than with concurrent activity, and that is the shape that looks
# perfectly healthy for months.
#
# A user idle for `_STALE_AFTER_SECONDS` cannot be affected by dropping their
# stamp: the next emission is allowed either way, since the throttle only ever
# suppresses within a 3-second window.
_MAX_TRACKED_USERS = 4096
_STALE_AFTER_SECONDS = 300.0


def reset_state() -> None:
    """Clear throttle state (tests)."""
    _last_emit.clear()


def _prune(now: float) -> None:
    """Drop stamps that can no longer suppress anything, then bound the rest."""
    for user_id in [u for u, ts in _last_emit.items() if (now - ts) >= _STALE_AFTER_SECONDS]:
        _last_emit.pop(user_id, None)
    overflow = len(_last_emit) - _MAX_TRACKED_USERS + 1
    if overflow > 0:
        for user_id, _ in sorted(_last_emit.items(), key=lambda kv: kv[1])[:overflow]:
            _last_emit.pop(user_id, None)


def _tick_allowed(user_id: str, now: Optional[float] = None) -> bool:
    now = time.monotonic() if now is None else now
    last = _last_emit.get(str(user_id))
    return last is None or (now - last) >= TICK_EMIT_INTERVAL


def _stamp(user_id: str, now: Optional[float] = None) -> None:
    now = time.monotonic() if now is None else now
    key = str(user_id)
    if len(_last_emit) >= _MAX_TRACKED_USERS and key not in _last_emit:
        _prune(now)
    _last_emit[key] = now


def throttle_stats() -> dict:
    """Size snapshot for the resource probe."""
    return {"tracked_users": len(_last_emit), "max_tracked_users": _MAX_TRACKED_USERS}


# --------------------------------------------------------------------------- #
# Quotes (light live map: cached 2d Yahoo quote + factual sector metadata)
# --------------------------------------------------------------------------- #
async def quotes_map(symbols: list) -> dict:
    """{UPPER_SYMBOL: quote|None} from the cached Yahoo layer, enriched with
    reference sector metadata (needed by the allocation-by-sector event).

    Deliberately uses the cheap 2d quote (price/day-change) instead of the 3mo
    indicator quote — the live snapshot needs marks, not RSI/MACD."""
    import asyncio

    from market_data import get_stock_meta
    from services.real_market import fetch_yahoo_quote

    uniq = list({(s or "").upper() for s in symbols if s})
    if not uniq:
        return {}
    results = await asyncio.gather(
        *[fetch_yahoo_quote(s, "2d") for s in uniq], return_exceptions=True
    )
    out = {}
    for sym, res in zip(uniq, results):
        if isinstance(res, Exception) or not res or res.get("price") is None:
            out[sym] = None
            continue
        meta = get_stock_meta(sym) or {}
        out[sym] = {
            "price": res.get("price"),
            "change_pct": res.get("change_pct"),
            "sector": meta.get("sector", ""),
        }
    return out


# --------------------------------------------------------------------------- #
# Snapshot payload (pure — easy to unit test)
# --------------------------------------------------------------------------- #
def snapshot_payload(holdings: list, user_id: str, reason: str) -> dict:
    """Build the ``portfolio.updated`` event payload from enriched holdings.

    Realized P&L is intentionally absent: it only changes when a trade closes
    (the REST intelligence bundle owns it); streaming a zero would be wrong.
    The flat ``total_pnl``/``total_unrealized_pnl``/``open_positions`` keys
    keep the pre-R5 ``portfolio_update`` consumer contract (Dashboard card)."""
    pnl = portfolio_engine.compute_pnl(holdings)
    allocation = portfolio_engine.compute_allocation(holdings)
    return {
        "user_id": str(user_id),
        "reason": reason,
        "pnl": {
            "invested": pnl["invested"],
            "current_value": pnl["current_value"],
            "unrealized": pnl["unrealized"],
            "unrealized_pct": pnl["unrealized_pct"],
            "best": pnl["best"],
            "worst": pnl["worst"],
        },
        "allocation": allocation,
        "holdings": [
            {
                "symbol": h.get("symbol"),
                "current_price": h.get("current_price"),
                "current_value": h.get("current_value"),
                "pnl": h.get("pnl"),
                "pnl_pct": h.get("pnl_pct"),
                "day_change_pct": h.get("day_change_pct"),
            }
            for h in holdings
        ],
        "open_positions": len(holdings),
        # Legacy-compat flat fields (pre-R5 `portfolio_update` contract).
        "total_pnl": pnl["unrealized"],
        "total_unrealized_pnl": pnl["unrealized"],
    }


# --------------------------------------------------------------------------- #
# Publishers
# --------------------------------------------------------------------------- #
async def publish_snapshot(db, user_id: str,
                           quotes_map_func: Optional[Callable[[list], Awaitable[dict]]] = None,
                           reason: str = "monitor",
                           price_override: Optional[dict] = None) -> Optional[dict]:
    """Recompute the user's live portfolio and publish ``portfolio.updated``.

    ``price_override`` ({UPPER_SYMBOL: price}) lets broker ticks supersede the
    cached quote price while keeping quote metadata (sector/day-change).
    Returns the published payload, or None when the user has no holdings."""
    fetch = quotes_map_func or quotes_map

    async def _quotes(symbols: list) -> dict:
        base = await fetch(symbols)
        if not price_override:
            return base
        merged = dict(base or {})
        for sym, price in price_override.items():
            sym = (sym or "").upper()
            if price is None:
                continue
            merged[sym] = {**(merged.get(sym) or {}), "price": price}
        return merged

    holdings = await portfolio_engine.build_holdings(db, {"_id": user_id}, _quotes)
    if not holdings:
        return None
    payload = snapshot_payload(holdings, user_id, reason)

    from services.market_engine.event_bus import event_bus
    await event_bus.publish("portfolio.updated", payload)
    _stamp(user_id)
    return payload


async def apply_broker_ticks(db, user_id: str, broker: str, ticks: list,
                             quotes_map_func: Optional[Callable[[list], Awaitable[dict]]] = None) -> Optional[dict]:
    """Fold live broker ticks into the user's portfolio and stream the result.

    Ticks are canonical ``MarketTick`` dicts (`services/market_engine/ticks.py`):
    ``symbol`` (canonical, uppercase) and ``price``, with no broker instrument
    identifier anywhere in them. The broker's own identifier — a Kite integer,
    an Upstox instrument key — is resolved to a symbol at the broker boundary
    (`services/brokers/instruments.py`, D4.3) and never reaches this module.

    That resolution used to happen *here*, joining ``instrument_token`` against
    ``db.holdings``. It made this core service depend on one broker's identifier
    format, duplicated the join into `trade_stream`, and gave a
    symbol-identified broker no join key at all — so its users' live P&L stopped
    updating with nothing raised or logged.

    Fresh marks are still persisted (so REST reads reflect the live broker price
    even between Yahoo refreshes) and a throttled ``portfolio.updated`` follows."""
    if not ticks or db is None:
        return None
    if not _tick_allowed(user_id):
        return None

    ticked = {
        (t.get("symbol") or "").upper(): t.get("price")
        for t in ticks
        if t.get("symbol") and t.get("price") is not None
    }
    if not ticked:
        return None

    # The holdings read stays: it is a *portfolio* join (which rows to re-mark),
    # not an identity join, and both sides of it are canonical symbols.
    docs = await db.holdings.find({"user_id": user_id, "broker": broker}).to_list(500)
    override = {}
    for d in docs:
        sym = (d.get("symbol") or "").upper()
        price = ticked.get(sym)
        if price is None or not sym:
            continue
        override[sym] = float(price)
        qty = float(d.get("quantity") or 0)
        await db.holdings.update_one(
            {"user_id": user_id, "broker": broker, "symbol": d.get("symbol")},
            {"$set": {
                "last_price": float(price),
                "market_value": round(float(price) * qty, 2),
            }},
        )
    if not override:
        return None

    return await publish_snapshot(
        db, user_id, quotes_map_func=quotes_map_func,
        reason="broker_tick", price_override=override,
    )

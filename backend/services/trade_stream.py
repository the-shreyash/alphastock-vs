"""Live Trade Stream (Sprint R6).

Closes the audit's §4.9 Trade Monitor gap (REALTIME_MIGRATION_PLAN.md): the
doc's flow *Position Open → PnL Streams Live → Target 1 → Trailing Stop →
Target 2 → Exit* needs a server-side step that turns price movement into a
per-user open-trades snapshot event. This module is that step — the trade
counterpart of ``services/portfolio_stream.py`` (Sprint R5).

Producers:
  • Heartbeat ``task_monitor_trades``          → reason ``monitor`` (periodic)
  • Scheduler ``trade_monitor_job`` (engine)   → reason ``engine`` (post-cycle)
  • Broker tick stream (``broker_engine``)     → reason ``broker_tick`` (throttled)

Each producer ends in ``publish_user_trades`` which publishes a per-user
``trade.updated`` event onto the market event bus. The R2 bridge delivers it
per-user (payload carries ``user_id``) on the ``trades`` channel — replacing
the pre-R6 ``trade_update`` **broadcast**, which leaked every user's open-trade
P&L to every connected socket.

P&L math is delegated to ``services.trading_engine`` helpers so streamed rows,
engine decisions and REST reads share one source of truth (short-aware,
``quantity_open``-based — the legacy producers ignored both). No fabricated
data: a trade whose live quote is missing streams without price/P&L fields.

Broker ticks arrive multiple times per second, so tick-driven recomputes are
throttled per user (``TICK_EMIT_INTERVAL``). State is process-local (matches
the single-heartbeat deployment model, same trade-off as portfolio_stream);
``reset_state()`` exists for tests.
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Optional

from services import trading_engine

logger = logging.getLogger(__name__)

# Minimum seconds between tick-driven emissions per user. Periodic (monitor/
# engine) emissions bypass the gate but still stamp it, so a tick right after
# a monitor pass does not double-emit.
TICK_EMIT_INTERVAL = 3.0

# Per-user last emission time (time.monotonic()).
_last_emit: dict = {}


def reset_state() -> None:
    """Clear throttle state (tests)."""
    _last_emit.clear()


def _tick_allowed(user_id: str, now: Optional[float] = None) -> bool:
    now = time.monotonic() if now is None else now
    last = _last_emit.get(str(user_id))
    return last is None or (now - last) >= TICK_EMIT_INTERVAL


def _stamp(user_id: str, now: Optional[float] = None) -> None:
    _last_emit[str(user_id)] = time.monotonic() if now is None else now


def _price_of(quote) -> Optional[float]:
    """Accept {price: x} quote dicts or bare numbers; None when unavailable."""
    if quote is None:
        return None
    if isinstance(quote, (int, float)):
        return float(quote)
    price = quote.get("price")
    return float(price) if price is not None else None


# --------------------------------------------------------------------------- #
# Payload builders (pure — easy to unit test)
# --------------------------------------------------------------------------- #
def trade_payload(trade: dict, price: Optional[float] = None) -> dict:
    """Build one live trade row.

    Reuses the trading engine's P&L helpers — short-aware and based on
    ``quantity_open`` (open remainder after partial exits), never the original
    quantity. Price/P&L fields are included only when a live price exists.
    """
    qty_open = trading_engine._qty_open(trade)
    row = {
        "trade_id": str(trade.get("_id")),
        "symbol": trade.get("symbol"),
        "type": (trade.get("type") or "BUY").upper(),
        "status": trade.get("status"),
        "is_paper": bool(trade.get("is_paper")),
        "entry_price": trade.get("entry_price"),
        "quantity": trade.get("quantity"),
        "quantity_open": qty_open,
        "realized_pnl": trade.get("realized_pnl"),
        "stop_loss": trade.get("stop_loss"),
        "best_price": trade.get("best_price"),
        "targets_hit": [h.get("level") for h in (trade.get("targets_hit") or [])],
    }
    if price is not None:
        per_share = trading_engine._per_share_pnl(trade, price)
        entry = trade.get("entry_price") or 0
        row["current_price"] = price
        row["unrealized_pnl"] = round(per_share * qty_open, 2)
        row["unrealized_pnl_pct"] = round(per_share / entry * 100, 2) if entry else 0
    return row


def snapshot_payload(trades: list, quotes: dict, user_id: str, reason: str) -> dict:
    """Build the per-user ``trade.updated`` event payload.

    ``quotes`` maps UPPER_SYMBOL → quote dict (``{"price": ...}``) or bare
    price. Trades without a quote still appear (structure/targets/SL are
    real), just without streamed price/P&L.
    """
    rows = [
        trade_payload(t, _price_of(quotes.get((t.get("symbol") or "").upper())))
        for t in trades
    ]
    return {
        "user_id": str(user_id),
        "reason": reason,
        "count": len(rows),
        "trades": rows,
    }


# --------------------------------------------------------------------------- #
# Publishers
# --------------------------------------------------------------------------- #
async def publish_user_trades(db, user_id: str, quotes: dict,
                              reason: str = "monitor") -> Optional[dict]:
    """Publish a ``trade.updated`` snapshot for one user's OPEN trades.

    Paper trades are included (the Trade Monitor shows them); engine actions
    and broker ticks still never touch them. Returns the published payload,
    or None when the user has no open trades (silent, like portfolio_stream).
    """
    trades = await db.trades.find(
        {"user_id": str(user_id), "status": "OPEN"}).to_list(200)
    if not trades:
        return None
    payload = snapshot_payload(trades, quotes or {}, user_id, reason)

    from services.market_engine.event_bus import event_bus
    await event_bus.publish("trade.updated", payload)
    _stamp(user_id)
    return payload


async def publish_all(db, quotes: dict, reason: str = "monitor") -> int:
    """Publish one ``trade.updated`` snapshot per user with OPEN trades.

    ``quotes`` is the shared prefetch for the whole pass (one fan-out per
    cycle, never per user). Returns the number of users notified.
    """
    open_trades = await db.trades.find({"status": "OPEN"}).to_list(500)
    by_user: dict = {}
    for t in open_trades:
        by_user.setdefault(str(t.get("user_id")), []).append(t)

    from services.market_engine.event_bus import event_bus
    for user_id, trades in by_user.items():
        payload = snapshot_payload(trades, quotes or {}, user_id, reason)
        await event_bus.publish("trade.updated", payload)
        _stamp(user_id)
    return len(by_user)


async def apply_broker_ticks(db, user_id: str, broker: str, ticks: list,
                             quotes_map_func: Optional[Callable[[list], Awaitable[dict]]] = None) -> Optional[dict]:
    """Fold live broker ticks into the user's open trades and stream the result.

    Ticks are ``[{instrument_token, last_price}]`` (official broker feed).
    Tokens map to symbols through the user's synced ``db.holdings`` rows (same
    approach as portfolio_stream — trades in symbols the broker account does
    not hold simply wait for the 60s monitor). Non-ticked open symbols are
    marked from ``quotes_map_func`` (defaults to the cached Yahoo layer) so the
    snapshot is always complete. Throttled per user.
    """
    if not ticks or db is None:
        return None
    if not _tick_allowed(user_id):
        return None

    token_price = {
        t.get("instrument_token"): t.get("last_price")
        for t in ticks
        if t.get("instrument_token") is not None and t.get("last_price") is not None
    }
    if not token_price:
        return None

    docs = await db.holdings.find({"user_id": user_id, "broker": broker}).to_list(500)
    override = {}
    for d in docs:
        price = token_price.get(d.get("instrument_token"))
        sym = (d.get("symbol") or "").upper()
        if price is None or not sym:
            continue
        override[sym] = float(price)
    if not override:
        return None

    # Only recompute when a ticked symbol is actually an open (non-paper)
    # position — broker ticks never drive paper trades.
    trades = await db.trades.find(
        {"user_id": str(user_id), "status": "OPEN"}).to_list(200)
    live_symbols = {(t.get("symbol") or "").upper()
                    for t in trades if not t.get("is_paper")}
    if not live_symbols & set(override):
        return None

    quotes: dict = {}
    missing = [s for s in {(t.get("symbol") or "").upper() for t in trades}
               if s and s not in override]
    if missing:
        try:
            fetch = quotes_map_func
            if fetch is None:
                from services.portfolio_stream import quotes_map as fetch
            quotes = {k: v for k, v in ((await fetch(missing)) or {}).items() if v}
        except Exception as e:
            logger.warning(f"Trade stream quote backfill failed: {e}")
    quotes.update({sym: {"price": price} for sym, price in override.items()})

    payload = snapshot_payload(trades, quotes, user_id, reason="broker_tick")

    from services.market_engine.event_bus import event_bus
    await event_bus.publish("trade.updated", payload)
    _stamp(user_id)
    return payload

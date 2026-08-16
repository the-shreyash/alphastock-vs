"""Canonical analytics query filters (PH3.8).

FILTERS ONLY. NO ARITHMETIC.
----------------------------
This module builds Mongo filter documents and nothing else. It exists because
the same three scoping decisions were being re-made, inconsistently, at every
call site that touched `db.trades`:

1. **Is a paper trade included?** `build_risk_summary` excluded them.
   `build_holdings` excluded them. `build_intelligence`'s realised P&L — inside
   the same function as `build_holdings` — included them, as did the trade
   journal and `GET /api/trades/pnl`. One collection, two meanings of "my
   trades", decided independently in six places.
2. **What counts as closed?** Some call sites test ``status != "OPEN"``, others
   ``status == "CLOSED"``. The lifecycle writes ``CLOSED``, ``TARGET_HIT`` and
   ``SL_HIT``, so the second form silently drops every trade that exited at a
   target or a stop — which is most of them.
3. **Which day is "today"?** Every call site wrote its own UTC-day prefix match.

Centralising the *filters* fixes all three without moving any P&L arithmetic out
of `services.trading_engine` / `services.portfolio_engine`, which stay the
single source of truth for the math. A filter builder is a scoping decision; the
scoping decisions are what drifted.
"""
from __future__ import annotations

from typing import Optional

from analytics.periods import Window

#: Trade statuses that mean the position is finished and `pnl` is final.
CLOSED_STATUSES = ("CLOSED", "TARGET_HIT", "SL_HIT")


def live_trades(user_id=None) -> dict:
    """Real-money trades. Paper trades are excluded.

    ``{"is_paper": {"$ne": True}}`` and not ``{"is_paper": False}``: trades
    created before paper trading existed have no ``is_paper`` field at all, and
    an equality match would drop every one of them.
    """
    flt = {"is_paper": {"$ne": True}}
    if user_id is not None:
        flt["user_id"] = user_id
    return flt


def paper_trades(user_id=None) -> dict:
    """Virtual-capital trades only."""
    flt = {"is_paper": True}
    if user_id is not None:
        flt["user_id"] = user_id
    return flt


def closed(base: Optional[dict] = None) -> dict:
    """Narrow a trade filter to finished positions.

    Uses ``status != "OPEN"`` rather than an ``$in`` over the known closed
    states. That is the deliberately *wider* of the two, and it is the right
    one here: a trade in a status nobody anticipated is still not open, and a
    realised-P&L total that silently omits it is worse than one that includes
    it. `analytics.quality.check_trade` reports unknown statuses so the gap is
    visible rather than merely tolerated.
    """
    return {**(base or {}), "status": {"$ne": "OPEN"}}


def open_positions(base: Optional[dict] = None) -> dict:
    return {**(base or {}), "status": "OPEN"}


def in_window(base: Optional[dict], field: str, window: Window) -> dict:
    """Add a half-open ``[start, end)`` constraint on a UTC-ISO string field.

    A no-op for the unbounded ``all`` window, so callers never branch.
    """
    return {**(base or {}), **window.filter_for(field)}


def closed_in_window(window: Window, user_id=None, paper: bool = False) -> dict:
    """Trades that CLOSED inside ``window`` — the realised-P&L population.

    Also requires ``exit_time`` to exist. A closed trade with no exit_time
    cannot be dated, so including it in a period total would attribute it to
    whichever period happened to be asked for; `analytics.quality` reports it
    as `closed_without_exit_time` instead.
    """
    base = paper_trades(user_id) if paper else live_trades(user_id)
    flt = in_window(closed(base), "exit_time", window)
    flt.setdefault("exit_time", {})
    if isinstance(flt["exit_time"], dict):
        flt["exit_time"]["$ne"] = None
    return flt


def entered_in_window(window: Window, user_id=None, paper: bool = False) -> dict:
    """Trades that were ENTERED inside ``window`` — the activity population."""
    base = paper_trades(user_id) if paper else live_trades(user_id)
    return in_window(base, "entry_time", window)


def wins(base: dict) -> dict:
    """A win is strictly positive realised P&L.

    Breakeven (``pnl == 0``) is neither a win nor a loss. It was counted as a
    loss before PH3.8, which mattered more than it sounds: `reset_paper_capital`
    force-closes open paper trades with ``pnl: 0``, so resetting a paper account
    manufactured a run of recorded losses and pushed the displayed win rate
    down for as long as those rows existed.
    """
    return {**base, "pnl": {"$gt": 0}}


def losses(base: dict) -> dict:
    return {**base, "pnl": {"$lt": 0}}


def breakeven(base: dict) -> dict:
    return {**base, "pnl": 0}


def sum_pnl_pipeline(match: dict) -> list:
    """Aggregation that totals ``pnl`` in the database rather than in Python.

    Replaces the ``find(...).to_list(500)`` + Python ``sum`` idiom, which had
    two defects at once: the cap silently truncated an active trader's all-time
    figure to whichever 500 documents Mongo returned first (no sort was
    applied, so *which* 500 was undefined), and every document crossed the wire
    to compute one scalar.
    """
    return [
        {"$match": match},
        {"$group": {"_id": None, "total": {"$sum": "$pnl"}, "count": {"$sum": 1}}},
    ]


async def sum_pnl(db, match: dict) -> tuple:
    """``(total_pnl, trade_count)`` for ``match``. Unbounded and cheap."""
    async for row in db.trades.aggregate(sum_pnl_pipeline(match)):
        return round(row.get("total") or 0.0, 2), int(row.get("count") or 0)
    return 0.0, 0

"""Analytics source-data quality checks (PH3.8).

WHAT THIS IS AND WHAT IT DELIBERATELY IS NOT
--------------------------------------------
This module **inspects and reports**. It never writes, never repairs and never
excludes a record on its own initiative. That restraint is the design:

* Silently mutating production records so a dashboard adds up is how a data
  problem becomes an unrecoverable data problem. The bad row is the evidence.
* Silently *excluding* bad rows is nearly as bad — the metric becomes correct
  and quietly incomplete, and nobody ever learns the collection is damaged.

So the contract is: quantify, name, and hand the operator a decision. Where a
metric must exclude a record to stay defensible (a trade with no usable
timestamp cannot be attributed to a day), the exclusion happens in the metric
and is *counted*, so the count can be surfaced rather than lost.

WHY THESE PARTICULAR CHECKS
---------------------------
Each one below corresponds to a state PH3.8 found reachable in the current
code, not to a generic data-hygiene checklist:

* ``pnl`` set while ``exit_time`` is absent — the shape that crashed the
  end-of-day report job (F-2), because a metric filtered on one field and
  dereferenced the other.
* ``status`` outside the known set — the lifecycle writes CLOSED, TARGET_HIT
  and SL_HIT, while several metrics test only ``!= "OPEN"`` and others test
  ``== "CLOSED"``. A status nobody anticipated is counted by one and dropped by
  the other.
* ``quantity_open`` exceeding ``quantity``, or a closed trade with quantity
  still open — partial-exit bookkeeping that has drifted.
* ``pnl`` disagreeing with the entry/exit/quantity it claims to summarise —
  the check that catches a P&L written by a path that got the side wrong.
* Paper trades carrying real-money markers, and vice versa.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from analytics.periods import to_datetime

#: Trade lifecycle states the application writes. Anything else is an anomaly:
#: a legacy row, an import, or a code path nobody remembered.
KNOWN_TRADE_STATUS = frozenset({
    "OPEN", "CLOSED", "TARGET_HIT", "SL_HIT",
})

#: Statuses that mean "this trade is finished and its pnl is final".
CLOSED_TRADE_STATUS = frozenset({"CLOSED", "TARGET_HIT", "SL_HIT"})

#: Tolerance when re-deriving pnl from entry/exit/quantity. Prices and P&L are
#: stored as float rupees and rounded to 2dp at several independent points, so
#: an exact comparison would flag every well-formed trade. One rupee is far
#: below anything that indicates a real sign or arithmetic error, and far above
#: accumulated float noise on a realistic position.
PNL_TOLERANCE_INR = 1.0


@dataclass
class Issue:
    """One data-quality finding on one record."""

    code: str
    record_id: str
    detail: str
    severity: str = "warning"   # warning | error

    def as_dict(self) -> dict:
        return {"code": self.code, "record_id": self.record_id,
                "detail": self.detail, "severity": self.severity}


@dataclass
class Report:
    """The outcome of a quality scan."""

    scanned: int = 0
    issues: list = field(default_factory=list)

    def add(self, code, record_id, detail, severity="warning"):
        self.issues.append(Issue(code, str(record_id), detail, severity))

    @property
    def clean(self) -> bool:
        return not self.issues

    @property
    def errors(self) -> list:
        return [i for i in self.issues if i.severity == "error"]

    def counts(self) -> dict:
        out: dict = {}
        for issue in self.issues:
            out[issue.code] = out.get(issue.code, 0) + 1
        return out

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "issue_count": len(self.issues),
            "error_count": len(self.errors),
            "by_code": self.counts(),
            # Bounded: a scan of a damaged collection must not return a
            # million-line payload that itself becomes the outage.
            "issues": [i.as_dict() for i in self.issues[:100]],
            "truncated": len(self.issues) > 100,
        }


def _num(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _check_trade_lifecycle(trade: dict, tid, closed: bool, report: Report) -> None:
    """Status and timestamp invariants."""
    status = trade.get("status")
    pnl = trade.get("pnl")
    exit_time = trade.get("exit_time")
    entry_time = trade.get("entry_time")

    if status not in KNOWN_TRADE_STATUS:
        report.add("unknown_status", tid,
                   f"status={status!r} is outside the known set "
                   f"{sorted(KNOWN_TRADE_STATUS)}; metrics that test '!= OPEN' and "
                   f"metrics that test '== CLOSED' will disagree about this trade.",
                   "error")
    if not entry_time:
        report.add("missing_entry_time", tid,
                   "no entry_time; the trade cannot be attributed to a day.", "error")
    elif to_datetime(entry_time) is None:
        report.add("invalid_entry_time", tid,
                   f"entry_time={entry_time!r} is not a parseable timestamp.", "error")
    if closed and not exit_time:
        report.add("closed_without_exit_time", tid,
                   "closed trade has no exit_time; it is invisible to every "
                   "period-scoped P&L metric and cannot be dated.", "error")
    if exit_time and to_datetime(exit_time) is None:
        report.add("invalid_exit_time", tid,
                   f"exit_time={exit_time!r} is not a parseable timestamp.", "error")
    if pnl is not None and not exit_time:
        report.add("pnl_without_exit_time", tid,
                   "pnl is set but exit_time is absent — the exact shape that made "
                   "`exit_time.startswith(today)` raise AttributeError (F-2).",
                   "error")
    if status == "OPEN" and pnl is not None:
        report.add("open_with_pnl", tid,
                   "an OPEN trade carries a final pnl; realised-P&L metrics will "
                   "double-count it against the position that is still live.", "error")


def _check_trade_quantities(trade: dict, tid, closed: bool, report: Report) -> None:
    """Quantity and price invariants, including partial-exit bookkeeping."""
    qty = _num(trade.get("quantity"))
    qty_open = _num(trade.get("quantity_open"))
    if qty is not None and qty <= 0:
        report.add("non_positive_quantity", tid, f"quantity={qty}.", "error")
    if qty is not None and qty_open is not None:
        if qty_open > qty:
            report.add("quantity_open_exceeds_quantity", tid,
                       f"quantity_open={qty_open} > quantity={qty}.", "error")
        if qty_open < 0:
            report.add("negative_quantity_open", tid, f"quantity_open={qty_open}.", "error")
        if closed and qty_open > 0:
            report.add("closed_with_open_quantity", tid,
                       f"closed trade still reports quantity_open={qty_open}; "
                       "exposure metrics will count a position that no longer exists.")

    entry = _num(trade.get("entry_price"))
    if entry is not None and entry <= 0:
        report.add("non_positive_entry_price", tid, f"entry_price={entry}.", "error")


def _check_trade_pnl(trade: dict, tid, closed: bool, report: Report) -> None:
    """Re-derive pnl from entry/exit/quantity and compare.

    Catches a sign error on a short, a P&L written against the wrong quantity,
    and a partial exit whose final pnl was never reconciled with what was
    actually booked.
    """
    entry = _num(trade.get("entry_price"))
    exit_price = _num(trade.get("exit_price"))
    pnl_value = _num(trade.get("pnl"))
    qty = _num(trade.get("quantity"))
    if not closed or not qty:
        return
    if entry is None or exit_price is None or pnl_value is None:
        return
    # A trade closed through partial exits legitimately differs: earlier tranches
    # were booked at other prices, which `realized_pnl` records. Only flag when
    # no partial booking exists to explain the difference.
    if trade.get("targets_hit") or trade.get("bookings"):
        return
    side = -1.0 if (trade.get("type") or "BUY").upper() == "SELL" else 1.0
    expected = side * (exit_price - entry) * qty
    if abs(expected - pnl_value) > PNL_TOLERANCE_INR:
        report.add("pnl_mismatch", tid,
                   f"stored pnl={pnl_value} but entry/exit/quantity imply "
                   f"{round(expected, 2)} (side={'SELL' if side < 0 else 'BUY'}).",
                   "error")


def check_trade(trade: dict, report: Report) -> None:
    """Validate one trade document against the invariants analytics rely on."""
    tid = trade.get("_id", "<no id>")
    closed = trade.get("status") in CLOSED_TRADE_STATUS
    _check_trade_lifecycle(trade, tid, closed, report)
    _check_trade_quantities(trade, tid, closed, report)
    _check_trade_pnl(trade, tid, closed, report)
    if trade.get("is_paper") and trade.get("broker"):
        report.add("paper_trade_with_broker", tid,
                   "a paper trade carries a broker link; it may be counted as real "
                   "order flow.", "error")


async def scan_trades(db, user_id=None, limit: int = 5000) -> Report:
    """Scan the trades collection (or one user's trades) for analytics-breaking
    states. Bounded by ``limit`` — an unbounded scan of the busiest collection
    in the product is itself a production incident."""
    report = Report()
    query = {"user_id": user_id} if user_id else {}
    async for trade in db.trades.find(query).limit(limit):
        report.scanned += 1
        check_trade(trade, report)
    return report


async def scan_portfolio_snapshots(db, user_id: str, limit: int = 2000) -> Report:
    """Scan one user's equity snapshots for the states that corrupt a curve."""
    report = Report()
    snaps = await db.portfolio_snapshots.find({"user_id": user_id}).limit(limit).to_list(limit)
    seen_dates = {}
    for snap in snaps:
        report.scanned += 1
        sid = snap.get("_id", "<no id>")
        day = snap.get("date")
        if not day:
            report.add("missing_date", sid, "snapshot has no date; it cannot be "
                                            "ordered on the curve.", "error")
        elif day in seen_dates:
            # `record_snapshot` upserts on {user_id, date} so this should be
            # impossible; if it happens the uniqueness assumption has broken and
            # one day is counted twice on the equity curve.
            report.add("duplicate_snapshot_date", sid,
                       f"a second snapshot exists for {day}.", "error")
        else:
            seen_dates[day] = sid

        value = _num(snap.get("current_value"))
        invested = _num(snap.get("invested"))
        if value is None or value < 0:
            report.add("invalid_current_value", sid, f"current_value={snap.get('current_value')!r}.",
                       "error")
        if invested is None or invested < 0:
            report.add("invalid_invested", sid, f"invested={snap.get('invested')!r}.", "error")
        pnl = _num(snap.get("pnl"))
        if value is not None and invested is not None and pnl is not None:
            if abs((value - invested) - pnl) > PNL_TOLERANCE_INR:
                report.add("snapshot_pnl_mismatch", sid,
                           f"pnl={pnl} but current_value − invested = "
                           f"{round(value - invested, 2)}.", "error")
    return report


async def scan_payments(db, limit: int = 5000) -> Report:
    """Scan payment records.

    On the current installation this scans an empty (in fact non-existent)
    collection and reports zero — which is the honest result, and the point:
    every revenue metric in the admin portal is computed WITHOUT this data.
    """
    report = Report()
    if "payments" not in await db.list_collection_names():
        return report
    async for payment in db.payments.find({}).limit(limit):
        report.scanned += 1
        pid = payment.get("_id", "<no id>")
        amount = _num(payment.get("amount"))
        if amount is None:
            report.add("missing_amount", pid, "payment has no numeric amount.", "error")
        elif amount < 0:
            report.add("negative_amount", pid, f"amount={amount}.", "error")
        if not payment.get("status"):
            report.add("missing_status", pid,
                       "payment has no status; captured, pending, failed and refunded "
                       "records cannot be told apart.", "error")
        if not payment.get("currency"):
            report.add("missing_currency", pid,
                       "payment has no currency; amounts cannot be summed safely.", "error")
        created = payment.get("created_at")
        if not created or to_datetime(created) is None:
            report.add("invalid_created_at", pid,
                       f"created_at={created!r} is missing or unparseable; the payment "
                       "cannot be placed in a revenue period.", "error")
    return report

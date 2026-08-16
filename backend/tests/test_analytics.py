"""PH3.8 — Analytics & Data Integrity.

WHAT THIS SUITE IS FOR
----------------------
Analytics defects do not crash. They produce a number, the number renders,
somebody reads it, and nothing anywhere reports a problem. Four of the defects
this sprint found had been shipping for months in exactly that way — one of
them crashed a nightly job on every single run and the crash was swallowed by a
broad `except` into a log line nobody read.

So this suite is written against *observable numbers*, not against
implementation details:

* Every defect PH3.8 fixed has a test that FAILS ON THE OLD CODE. Where the old
  behaviour was a specific wrong value, the test asserts the right value AND
  names the wrong one, so a regression is legible in the failure output rather
  than requiring archaeology.
* Every classification in `analytics.registry` is asserted structurally, so the
  inventory cannot drift away from the code it describes.
* The MOCK metrics are asserted to *still be flagged*. That is the load-bearing
  guarantee of an audit sprint: PH3.8 did not remove the fabricated numbers, so
  the only thing standing between them and a reader is the flag, and a test has
  to hold it in place until PH3.9 removes them.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from analytics import contract, periods, queries, registry
from analytics.contract import ContractError
from analytics.periods import IST, UnknownPeriod
from tests._fakedb import FakeDB

# No marker: hermetic is the default and is defined by the ABSENCE of markers
# (see the taxonomy note in pyproject.toml). Nothing here touches the network,
# a database, or the clock.


def _run(coro):
    """Drive one coroutine on a private event loop, leaving the process's loop
    policy exactly as it was found.

    Not `asyncio.get_event_loop()`: some suite earlier in the default run closes
    the main-thread loop, so that form raised `RuntimeError: There is no current
    event loop` for 23 of these tests in a full run while every one of them
    passed in isolation — a test-harness fault that reads exactly like an
    application defect. Not bare `asyncio.run()` either: it leaves the policy's
    current loop set to `None`, which would export this same problem to whatever
    runs next. Everything driven here is FakeDB-backed and holds no loop-bound
    resources, so a fresh loop per call is free.
    """
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(previous)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _ist(y, m, d, hh=12, mm=0):
    """An instant expressed in IST, returned as an aware UTC datetime."""
    return datetime(y, m, d, hh, mm, tzinfo=IST).astimezone(timezone.utc)


def _trade(**kw):
    """A well-formed closed real-money trade, overridable per test."""
    base = {
        "_id": ObjectId(),
        "user_id": "u1",
        "symbol": "TCS",
        "type": "BUY",
        "status": "CLOSED",
        "entry_price": 100.0,
        "quantity": 10,
        "exit_price": 110.0,
        "pnl": 100.0,
        "pnl_percent": 10.0,
        "entry_time": _iso(_ist(2026, 8, 16, 10)),
        "exit_time": _iso(_ist(2026, 8, 16, 14)),
    }
    base.update(kw)
    return base


# =========================================================================== #
# 1. TIME WINDOWS AND THE TIMEZONE STRATEGY (Step 5)
# =========================================================================== #

class TestPeriods:
    """The IST-boundary window resolver.

    The whole point of this module is the 05:30 IST seam, so most of these
    tests sit on it deliberately.
    """

    def test_ist_day_boundary_is_not_the_utc_day_boundary(self):
        """00:10 IST on the 17th is 18:40 UTC on the 16th.

        This is the exact instant every pre-PH3.8 metric got wrong: the UTC-day
        idiom would date it to the 16th. It belongs to the 17th.
        """
        moment = datetime(2026, 8, 16, 18, 40, tzinfo=timezone.utc)
        assert periods.ist_date(moment).isoformat() == "2026-08-17"
        today = periods.resolve("today", now=moment)
        assert today.contains(moment)
        assert not periods.resolve("yesterday", now=moment).contains(moment)

    def test_early_morning_ist_is_not_yesterday(self):
        """03:00 IST is *today*, though the UTC date still reads yesterday.

        Between 00:00 and 05:30 IST the UTC calendar date lags by one, which is
        where the daily trade counter used to reset in the middle of the night.
        """
        moment = _ist(2026, 8, 17, 3, 0)
        assert moment.astimezone(timezone.utc).date().isoformat() == "2026-08-16"
        assert periods.resolve("today", now=moment).contains(moment)

    def test_windows_are_half_open_and_partition_time(self):
        """An instant belongs to exactly one of today / yesterday, never both."""
        now = _ist(2026, 8, 16, 12)
        midnight = _ist(2026, 8, 16, 0, 0)
        today = periods.resolve("today", now=now)
        yesterday = periods.resolve("yesterday", now=now)
        assert today.contains(midnight)
        assert not yesterday.contains(midnight)
        assert yesterday.end == today.start          # no gap
        assert yesterday.end_iso == today.start_iso   # and none in the strings

    def test_last_n_days_are_whole_days_ending_today(self):
        now = _ist(2026, 8, 16, 12)
        window = periods.resolve("7d", now=now)
        assert window.contains(_ist(2026, 8, 10, 0, 1))    # 7th day back, included
        assert not window.contains(_ist(2026, 8, 9, 23, 59))  # 8th day back, excluded
        assert window.contains(now)

    def test_month_and_year_windows(self):
        now = _ist(2026, 8, 16, 12)
        assert periods.resolve("mtd", now=now).contains(_ist(2026, 8, 1, 0, 0))
        assert not periods.resolve("mtd", now=now).contains(_ist(2026, 7, 31, 23, 59))
        prev = periods.resolve("prev_month", now=now)
        assert prev.contains(_ist(2026, 7, 15))
        assert not prev.contains(_ist(2026, 8, 1, 0, 1))
        assert periods.resolve("ytd", now=now).contains(_ist(2026, 1, 1, 0, 0))
        assert not periods.resolve("ytd", now=now).contains(_ist(2025, 12, 31, 23, 59))

    def test_january_prev_month_crosses_the_year(self):
        prev = periods.resolve("prev_month", now=_ist(2026, 1, 10))
        assert prev.contains(_ist(2025, 12, 25))
        assert not prev.contains(_ist(2026, 1, 1, 0, 1))

    def test_all_window_is_unbounded_and_says_so(self):
        window = periods.resolve("all")
        assert window.bounded is False
        assert window.start_iso is None
        assert window.mongo_range() == {}
        assert window.filter_for("exit_time") == {}   # spreads to nothing
        assert window.contains(_iso(_ist(1999, 1, 1)))

    def test_unknown_period_raises_rather_than_widening(self):
        """A typo must fail, not silently become 'all time'."""
        with pytest.raises(UnknownPeriod):
            periods.resolve("last_week")

    @pytest.mark.parametrize("bad", [None, "", "not-a-date", 42, [], {}])
    def test_unparseable_timestamps_are_outside_every_window(self, bad):
        """A record with no usable timestamp is never attributed to a period."""
        assert periods.to_datetime(bad) is None
        assert not periods.resolve("today").contains(bad)
        assert not periods.resolve("all").contains(bad)

    def test_z_suffix_and_naive_timestamps_parse(self):
        assert periods.to_datetime("2026-08-16T10:00:00Z") is not None
        naive = periods.to_datetime("2026-08-16T10:00:00")
        assert naive is not None and naive.tzinfo is not None

    def test_window_of_days_clamps_zero(self):
        """?days=0 produced a zero-width window that looked like 'no trades'."""
        assert periods.window_of_days(0).contains(periods.now_utc())

    def test_session_semantics(self):
        weekday_noon = _iso(_ist(2026, 8, 17, 12, 0))       # Monday
        assert periods.session_date(weekday_noon)["in_session"] is True
        assert periods.session_date(_iso(_ist(2026, 8, 17, 8, 0)))["in_session"] is False
        saturday = _iso(_ist(2026, 8, 15, 12, 0))
        assert periods.session_date(saturday)["is_trading_day"] is False


# =========================================================================== #
# 2. THE ANALYTICS CONTRACT (Step 4)
# =========================================================================== #

class TestContract:

    def test_unavailable_metric_cannot_carry_a_value(self):
        """The core guarantee: unavailable is not zero.

        An unavailable metric that could hold 0 would be indistinguishable from
        a measured zero the moment it crossed the HTTP boundary — which is the
        precise way `revenue_today: 0` has been lying.
        """
        metric = contract.unavailable("revenue_today", note="no payment records exist")
        assert metric.value is None
        assert metric.status == contract.UNAVAILABLE
        assert metric.trustworthy is False
        with pytest.raises(ContractError):
            contract.Metric(name="x", value=0, provenance=contract.UNAVAILABLE,
                            status=contract.UNAVAILABLE, note="why")

    def test_mock_provenance_forces_mock_status(self):
        """A fabricated number cannot be marked available in the other field."""
        with pytest.raises(ContractError):
            contract.Metric(name="mrr", value=1, provenance=contract.MOCK,
                            status=contract.AVAILABLE, note="why")

    def test_unavailable_and_mock_require_a_reason(self):
        with pytest.raises(ContractError):
            contract.Metric(name="x", provenance=contract.UNAVAILABLE,
                            status=contract.UNAVAILABLE)
        with pytest.raises(ContractError):
            contract.mock("x", 1, note="")

    @pytest.mark.parametrize("field,value", [
        ("provenance", "probably"), ("status", "fine"), ("unit", "bananas"),
    ])
    def test_invalid_vocabulary_is_rejected_at_construction(self, field, value):
        with pytest.raises(ContractError):
            contract.Metric(**{"name": "x", "value": 1, field: value})

    def test_empty_is_distinct_from_unavailable(self):
        """'You have no trades yet' is not 'we cannot compute this'."""
        assert contract.empty("win_rate").status == contract.EMPTY
        assert contract.empty("win_rate").provenance == contract.DERIVED

    def test_trustworthiness(self):
        assert contract.real("n", 5).trustworthy
        assert contract.derived("n", 5).trustworthy
        assert not contract.mock("n", 5, note="fabricated").trustworthy
        assert not contract.unavailable("n", note="no data").trustworthy

    def test_envelope_summarises_provenance(self):
        env = contract.envelope([
            contract.real("a", 1),
            contract.mock("b", 2, note="fabricated"),
            contract.unavailable("c", note="no source"),
        ], period="today", surface="test")
        assert env["provenance_summary"] == {"real": 1, "derived": 0, "mock": 1,
                                             "unavailable": 1}
        assert env["trustworthy"] is False
        assert env["metrics"]["c"]["value"] is None
        assert env["period"]["timezone"] == "Asia/Kolkata"

    def test_serialised_metric_always_declares_provenance_and_status(self):
        for metric in (contract.real("a", 1), contract.mock("b", 2, note="x"),
                       contract.unavailable("c", note="x"), contract.empty("d")):
            payload = metric.as_dict()
            assert payload["provenance"] in contract.PROVENANCE
            assert payload["status"] in contract.STATUSES
            assert payload["calculated_at"]


# =========================================================================== #
# 3. THE INVENTORY (Steps 1, 2, 13)
# =========================================================================== #

class TestRegistry:
    """The inventory is code so it cannot drift. These assertions are what
    make that true."""

    def test_every_entry_is_structurally_complete(self):
        for spec in registry.REGISTRY:
            assert spec.provenance in contract.PROVENANCE, spec.key
            assert spec.key and spec.label and spec.surface, spec.key
            assert spec.source, f"{spec.key} names no source of truth"
            assert spec.calculation, f"{spec.key} does not say how it is computed"
            assert spec.window in list(periods.PERIODS), spec.key
            assert spec.audience in ("user", "admin"), spec.key

    def test_metric_keys_are_unique(self):
        keys = [s.key for s in registry.REGISTRY]
        assert len(keys) == len(set(keys))

    def test_every_mock_carries_a_ph39_replacement_plan(self):
        """A MOCK entry with no named production source is not a handoff, it is
        a complaint. This is the assertion that makes the PH3.9 inventory a
        specification."""
        for spec in registry.ph39_inventory():
            assert spec.required_source, f"{spec.key} does not name its real source"
            assert spec.priority in ("P1", "P2", "P3"), f"{spec.key} is unprioritised"
            assert spec.note, f"{spec.key} does not explain why it is not real"

    def test_registry_endpoints_exist_on_the_live_route_table(self):
        """An inventory that names a route the app does not serve is stale."""
        from server import app
        live = {(m, r.path) for r in app.routes
                for m in getattr(r, "methods", set()) or set()}
        for endpoint in registry.endpoints():
            method, _, path = endpoint.partition(" ")
            # Path parameters are named differently in the registry prose.
            matches = [p for m, p in live if m == method and _paths_match(p, path)]
            assert matches, f"registry names {endpoint}, which the app does not serve"

    def test_inventory_covers_all_four_classes(self):
        summary = registry.summary()
        for provenance in contract.PROVENANCE:
            assert summary[provenance] > 0, f"nothing classified {provenance}"

    def test_ph39_inventory_is_priority_ordered(self):
        priorities = [s.priority for s in registry.ph39_inventory()]
        assert priorities == sorted(priorities)

    def test_revenue_metrics_are_not_classified_real(self):
        """A guard against the single most tempting future mistake: making the
        revenue numbers look real by reclassifying them instead of by wiring a
        payment provider."""
        for key in ("admin.mrr", "admin.arr", "admin.revenue_today",
                    "admin.revenue_series"):
            assert registry.get(key).provenance == contract.MOCK, key


def _paths_match(route_path: str, registry_path: str) -> bool:
    """Compare a FastAPI route path to the inventory's prose form, tolerating
    differently-named path parameters (`{symbol}` vs `{ticker}`)."""
    import re
    normalise = lambda p: re.sub(r"\{[^}]+\}", "{}", p.rstrip("/"))  # noqa: E731
    return normalise(route_path) == normalise(registry_path)


# =========================================================================== #
# 4. TRADE SCOPING FILTERS (Step 3 — source of truth)
# =========================================================================== #

class TestQueries:

    def test_live_trades_include_legacy_rows_without_the_flag(self):
        """`is_paper: {$ne: True}` and not `is_paper: False`.

        Trades written before paper trading existed have no `is_paper` field.
        An equality match would silently drop every one of them from real-money
        analytics.
        """
        flt = queries.live_trades("u1")
        assert flt["is_paper"] == {"$ne": True}

    def test_closed_uses_ne_open_not_eq_closed(self):
        """The lifecycle also writes TARGET_HIT and SL_HIT.

        `status == "CLOSED"` drops every trade that exited at a target or a
        stop — which is most of them.
        """
        assert queries.closed()["status"] == {"$ne": "OPEN"}

    def test_closed_in_window_requires_a_usable_exit_time(self):
        window = periods.resolve("today")
        flt = queries.closed_in_window(window, "u1")
        assert flt["exit_time"]["$ne"] is None
        assert flt["exit_time"]["$gte"] == window.start_iso
        assert flt["exit_time"]["$lt"] == window.end_iso

    def test_win_loss_breakeven_are_three_disjoint_populations(self):
        base = queries.closed(queries.live_trades("u1"))
        assert queries.wins(base)["pnl"] == {"$gt": 0}
        assert queries.losses(base)["pnl"] == {"$lt": 0}
        assert queries.breakeven(base)["pnl"] == 0

    def test_sum_pnl_totals_in_the_database(self):
        db = FakeDB(trades=[
            _trade(pnl=100.0), _trade(pnl=-40.0), _trade(pnl=0.0),
            _trade(pnl=9999.0, is_paper=True),
            _trade(pnl=None, status="OPEN", exit_time=None),
        ])
        total, count = _run(queries.sum_pnl(
            db, queries.closed(queries.live_trades("u1"))))
        assert total == 60.0            # 100 − 40 + 0; paper and open excluded
        assert count == 3


# =========================================================================== #
# 5. FINANCIAL CORRECTNESS — the defects (Step 6)
# =========================================================================== #

class TestPaperTradeIsolation:
    """F-1. Paper trades share `db.trades` with real ones and were counted in
    every real-money statistic."""

    @staticmethod
    def _mixed_db():
        return FakeDB(
            trades=[
                _trade(pnl=-500.0, pnl_percent=-5.0, symbol="TCS"),
                _trade(pnl=9000.0, pnl_percent=90.0, symbol="INFY", is_paper=True),
            ],
            holdings=[],
        )

    def test_journal_reports_real_money_only(self):
        from services.trade_journal import get_performance_stats
        stats = _run(get_performance_stats(self._mixed_db(), "u1", days=3650))
        # Pre-PH3.8 this reported +8,500 at a 50% win rate.
        assert stats["all_time"]["total_pnl"] == -500.0
        assert stats["all_time"]["win_rate"] == 0.0
        assert stats["all_time"]["total"] == 1
        assert stats["scope"] == "live"

    def test_journal_still_reports_paper_separately(self):
        """Excluded, not hidden. Removing the figures would lose information."""
        from services.trade_journal import get_performance_stats
        stats = _run(get_performance_stats(self._mixed_db(), "u1", days=3650))
        assert stats["paper"]["all_time"]["total_pnl"] == 9000.0

    def test_portfolio_realized_pnl_excludes_paper(self):
        from services import portfolio_engine

        async def quotes(_):
            return {}

        bundle = _run(portfolio_engine.build_intelligence(
            self._mixed_db(), {"_id": "u1"}, quotes))
        assert bundle["pnl"]["realized"] == -500.0

    def test_setup_stats_exclude_paper(self):
        from services.trade_journal import get_setup_success_rates
        db = FakeDB(trades=[
            _trade(pnl=-500.0, pnl_percent=-5.0, setup_type="MOMENTUM"),
            _trade(pnl=9000.0, pnl_percent=90.0, setup_type="MOMENTUM", is_paper=True),
        ])
        out = _run(get_setup_success_rates(db, "u1"))
        assert out["setups"][0]["total_trades"] == 1
        assert out["setups"][0]["win_rate"] == 0.0


class TestWinRateOutcomes:
    """F-3. A breakeven close was scored as a loss."""

    def test_breakeven_is_neither_a_win_nor_a_loss(self):
        from services.trade_journal import calc_stats
        stats = calc_stats([{"pnl": 5.0}, {"pnl": -5.0}, {"pnl": 0.0}])
        assert (stats["wins"], stats["losses"], stats["breakeven"]) == (1, 1, 1)
        # Pre-PH3.8: wins=1, losses=2, win_rate=33.3.
        assert stats["win_rate"] == pytest.approx(33.3)
        assert stats["wins"] + stats["losses"] + stats["breakeven"] == stats["total"]

    def test_reset_paper_capital_marks_synthetic_closes(self):
        """F-3b. A capital reset force-closes positions at a fabricated ₹0.

        Without the marker those rows are indistinguishable from real breakeven
        exits and quietly pollute every paper statistic.
        """
        from services.paper_trade import reset_paper_capital
        oid = ObjectId()
        db = FakeDB(
            trades=[_trade(_id=oid, user_id=str(oid), is_paper=True, status="OPEN",
                           pnl=None, exit_time=None)],
            users=[{"_id": oid, "paper_capital": 50000.0}],
        )
        _run(reset_paper_capital(str(oid), db))
        assert db.trades.docs[0]["close_reason"] == "capital_reset"


class TestPartialExits:
    """F-6. Profit booked at target 1 was invisible to every realised-P&L
    metric — including the one that enforces the daily loss limit."""

    def test_partial_exit_records_a_dated_booking(self):
        from services.trading_engine import apply_partial_exit
        trade = {"type": "BUY", "entry_price": 100.0, "quantity": 100,
                 "quantity_open": 100, "realized_pnl": 0.0, "status": "OPEN"}
        update = apply_partial_exit(trade, 110.0, 50, "TARGET_HIT")
        assert update["realized_pnl"] == 500.0
        assert update["quantity_open"] == 50
        assert len(update["bookings"]) == 1
        assert update["bookings"][0]["pnl"] == 500.0
        assert periods.to_datetime(update["bookings"][0]["at"]) is not None

    def test_daily_loss_budget_sees_booked_partials(self):
        from services.trading_engine import build_risk_summary
        db = FakeDB(trades=[_trade(
            status="OPEN", pnl=None, exit_time=None, quantity=100, quantity_open=50,
            stop_loss=95.0, realized_pnl=500.0,
            bookings=[{"at": periods.now_utc().isoformat(), "quantity": 50,
                       "price": 110.0, "pnl": 500.0, "reason": "TARGET_HIT"}],
        )])
        summary = _run(build_risk_summary(
            db, {"_id": "u1", "capital": 100000, "max_daily_loss": 2000,
                 "max_trades_per_day": 5}))
        assert summary["realized_pnl_today"] == 500.0   # was 0

    def test_closed_trade_bookings_are_not_double_counted(self):
        """Once a trade closes, `pnl` is the total of every booking."""
        from services.trading_engine import build_risk_summary
        now = periods.now_utc().isoformat()
        db = FakeDB(trades=[_trade(
            pnl=500.0, exit_time=now, quantity=100, quantity_open=0,
            stop_loss=95.0, realized_pnl=500.0,
            bookings=[{"at": now, "quantity": 100, "price": 110.0,
                       "pnl": 500.0, "reason": "TARGET_HIT"}],
        )])
        summary = _run(build_risk_summary(db, {"_id": "u1", "capital": 100000}))
        assert summary["realized_pnl_today"] == 500.0   # not 1000


class TestShortSideConventions:
    """Sign conventions on a SELL (short) position."""

    def test_short_profits_when_price_falls(self):
        from services.trading_engine import apply_partial_exit
        trade = {"type": "SELL", "entry_price": 100.0, "quantity": 10,
                 "quantity_open": 10, "realized_pnl": 0.0, "status": "OPEN"}
        update = apply_partial_exit(trade, 90.0, 10, "TARGET_HIT")
        assert update["pnl"] == 100.0
        assert update["pnl_percent"] == 10.0

    def test_short_loses_when_price_rises(self):
        from services.trading_engine import apply_partial_exit
        trade = {"type": "SELL", "entry_price": 100.0, "quantity": 10,
                 "quantity_open": 10, "realized_pnl": 0.0, "status": "OPEN"}
        assert apply_partial_exit(trade, 110.0, 10, "SL_HIT")["pnl"] == -100.0


class TestEquityCurve:
    """F-8 and F-10."""

    @staticmethod
    def _snapshots(entries):
        return FakeDB(portfolio_snapshots=[
            {"user_id": "u1", "date": d, "current_value": v, "invested": i,
             "pnl": round(v - i, 2)} for d, v, i in entries])

    def test_range_is_calendar_days_not_snapshot_count(self):
        """F-10. `snaps[-30:]` sliced the LIST, so a monthly snapshot cadence
        made `range=1M` return thirty *months*."""
        from services import portfolio_engine
        today = periods.ist_date()
        # One snapshot per month for the last 24 months.
        entries = []
        for back in range(24, -1, -1):
            day = (today.replace(day=1) - timedelta(days=30 * back))
            entries.append((day.isoformat(), 100000.0, 100000.0))
        out = _run(portfolio_engine.get_performance(self._snapshots(entries), "u1", "1M"))
        # At most two monthly marks can fall inside 30 calendar days, so the
        # curve is either unavailable or very short — never 25 points.
        assert out.get("points", 0) <= 2

    def test_unknown_range_raises(self):
        from services import portfolio_engine
        with pytest.raises(ValueError):
            _run(portfolio_engine.get_performance(self._snapshots([]), "u1", "5Y"))

    def test_capital_inflow_is_flagged_not_reported_as_a_return(self):
        """F-8. Depositing ₹1L into a ₹1L portfolio reported +100%.

        PH3.8 does not invent a time-weighted return — the flow ledger does not
        exist. It stops the number from lying silently.
        """
        from services import portfolio_engine
        today = periods.ist_date()
        out = _run(portfolio_engine.get_performance(self._snapshots([
            ((today - timedelta(days=2)).isoformat(), 100000.0, 100000.0),
            ((today - timedelta(days=1)).isoformat(), 200000.0, 200000.0),
        ]), "u1", "1M"))
        assert out["available"] is True
        assert out["pct_return"] == 100.0          # unchanged, still reported
        assert out["flow_adjusted"] is False
        assert out["flows_detected"] is True       # ...but now declared
        assert out["invested_change"] == 100000.0
        assert "added or withdrew" in out["caveat"]
        # A deposit is not a "best day".
        assert out["best_day"] is None

    def test_genuine_gain_is_not_flagged_as_a_flow(self):
        from services import portfolio_engine
        today = periods.ist_date()
        out = _run(portfolio_engine.get_performance(self._snapshots([
            ((today - timedelta(days=2)).isoformat(), 100000.0, 100000.0),
            ((today - timedelta(days=1)).isoformat(), 110000.0, 100000.0),
        ]), "u1", "1M"))
        assert out["flows_detected"] is False
        assert out["caveat"] == ""
        assert out["best_day"]["pct"] == 10.0

    def test_fewer_than_two_snapshots_is_unavailable_not_a_flat_line(self):
        from services import portfolio_engine
        out = _run(portfolio_engine.get_performance(
            self._snapshots([(periods.ist_date().isoformat(), 1.0, 1.0)]), "u1", "ALL"))
        assert out["available"] is False
        assert out["curve"] == []
        assert "reason" in out


class TestEndOfDayReport:
    """F-2. The nightly job crashed on every run, and the P&L it would have
    reported was the whole platform's."""

    def test_open_trade_does_not_crash_the_job(self):
        """`exit_time` is explicitly None on an open trade, so
        `t.get("exit_time", "").startswith(...)` raised AttributeError — caught
        by a broad `except`, logged, and silently no report for anybody."""
        from services.scheduler import eod_report_job
        db = FakeDB(
            trades=[_trade(status="OPEN", pnl=None, exit_time=None)],
            users=[{"_id": ObjectId()}], market_analysis=[], notifications=[],
        )
        _run(eod_report_job(db))
        assert db.market_analysis.docs, "no EOD report was written"

    def test_each_user_is_told_their_own_pnl(self):
        """Every user used to receive the platform-wide sum labelled as theirs —
        a wrong personal number AND a cross-tenant disclosure."""
        from services.scheduler import eod_report_job
        now = periods.now_utc().isoformat()
        db = FakeDB(
            trades=[
                _trade(user_id="u1", pnl=1000.0, exit_time=now),
                _trade(user_id="u2", pnl=-400.0, exit_time=now),
            ],
            users=[], market_analysis=[], notifications=[],
        )
        _run(eod_report_job(db))
        messages = {n["user_id"]: n["message"] for n in db.notifications.docs}
        assert "+1000.00" in messages["u1"] and "600" not in messages["u1"]
        assert "-400.00" in messages["u2"]

    def test_users_who_did_not_trade_are_not_notified(self):
        from services.scheduler import eod_report_job
        db = FakeDB(trades=[], users=[{"_id": ObjectId()}],
                    market_analysis=[], notifications=[])
        _run(eod_report_job(db))
        assert db.notifications.docs == []

    def test_paper_trades_are_not_an_end_of_day_trading_result(self):
        from services.scheduler import eod_report_job
        now = periods.now_utc().isoformat()
        db = FakeDB(
            trades=[_trade(user_id="u1", pnl=5000.0, exit_time=now, is_paper=True)],
            users=[], market_analysis=[], notifications=[],
        )
        _run(eod_report_job(db))
        assert db.market_analysis.docs[0]["eod_report"]["total_pnl"] == 0
        assert db.notifications.docs == []


class TestPaperPnl:
    """F-9 and the TARGET_HIT/SL_HIT gap."""

    def test_target_hit_paper_trades_are_counted_as_realised(self):
        """`status == "CLOSED"` dropped them from realised P&L and from the
        open count — they vanished from the account."""
        from services.paper_trade import get_paper_pnl
        db = FakeDB(trades=[
            _trade(is_paper=True, status="TARGET_HIT", pnl=300.0),
            _trade(is_paper=True, status="SL_HIT", pnl=-100.0),
        ])
        out = _run(get_paper_pnl("u1", db))
        assert out["realized_pnl"] == 200.0
        assert out["closed_trades"] == 2

    def test_missing_marks_are_reported_not_silently_zero(self, monkeypatch):
        from services import paper_trade, real_market

        async def no_quote(_symbol):
            return None

        monkeypatch.setattr(real_market, "fetch_real_stock_quote", no_quote)
        db = FakeDB(trades=[_trade(is_paper=True, status="OPEN",
                                   pnl=None, exit_time=None)])
        out = _run(paper_trade.get_paper_pnl("u1", db))
        assert out["marks_unavailable"] == 1
        assert out["complete"] is False


# =========================================================================== #
# 6. DATA QUALITY (Step 8)
# =========================================================================== #

class TestDataQuality:

    def test_clean_trade_produces_no_issues(self):
        from analytics.quality import Report, check_trade
        report = Report()
        check_trade(_trade(), report)
        assert report.clean, report.counts()

    @pytest.mark.parametrize("mutation,code", [
        ({"status": "PARTIALLY_FILLED"}, "unknown_status"),
        ({"exit_time": None}, "pnl_without_exit_time"),
        ({"entry_time": None}, "missing_entry_time"),
        ({"entry_time": "not-a-date"}, "invalid_entry_time"),
        ({"quantity": 0}, "non_positive_quantity"),
        ({"quantity": 10, "quantity_open": 20}, "quantity_open_exceeds_quantity"),
        ({"quantity_open": -1}, "negative_quantity_open"),
        ({"entry_price": 0}, "non_positive_entry_price"),
        ({"pnl": 4242.0}, "pnl_mismatch"),
        ({"is_paper": True, "broker": "zerodha"}, "paper_trade_with_broker"),
    ])
    def test_broken_states_are_detected(self, mutation, code):
        from analytics.quality import Report, check_trade
        report = Report()
        check_trade(_trade(**mutation), report)
        assert code in report.counts(), f"expected {code}, got {report.counts()}"

    def test_short_pnl_is_not_flagged_as_a_mismatch(self):
        """The re-derivation must be side-aware or every short trips it."""
        from analytics.quality import Report, check_trade
        report = Report()
        check_trade(_trade(type="SELL", entry_price=110.0, exit_price=100.0,
                           pnl=100.0), report)
        assert "pnl_mismatch" not in report.counts()

    def test_partially_exited_trade_is_not_flagged(self):
        from analytics.quality import Report, check_trade
        report = Report()
        check_trade(_trade(pnl=1234.0, targets_hit=[{"level": 1}]), report)
        assert "pnl_mismatch" not in report.counts()

    def test_quality_scan_never_mutates(self):
        """The module reports; it does not repair. A scan that silently fixed
        production data would destroy the evidence."""
        from analytics.quality import scan_trades
        broken = _trade(status="WEIRD", pnl=99999.0)
        before = dict(broken)
        db = FakeDB(trades=[broken])
        report = _run(scan_trades(db))
        assert not report.clean
        assert db.trades.docs[0] == before

    def test_duplicate_snapshot_dates_are_detected(self):
        from analytics.quality import scan_portfolio_snapshots
        db = FakeDB(portfolio_snapshots=[
            {"_id": 1, "user_id": "u1", "date": "2026-08-16",
             "current_value": 100.0, "invested": 90.0, "pnl": 10.0},
            {"_id": 2, "user_id": "u1", "date": "2026-08-16",
             "current_value": 200.0, "invested": 90.0, "pnl": 110.0},
        ])
        assert "duplicate_snapshot_date" in _run(
            scan_portfolio_snapshots(db, "u1")).counts()

    def test_payments_scan_on_an_empty_platform_is_honest(self):
        """Zero issues over zero records — which is the point: every revenue
        metric in the admin portal is computed without this data."""
        from analytics.quality import scan_payments
        report = _run(scan_payments(FakeDB()))
        assert report.scanned == 0 and report.clean

    def test_malformed_payment_is_detected(self):
        from analytics.quality import scan_payments
        db = FakeDB(payments=[{"_id": 1, "amount": -5}])
        codes = _run(scan_payments(db)).counts()
        assert {"negative_amount", "missing_status", "missing_currency",
                "invalid_created_at"} <= set(codes)

    def test_report_payload_is_bounded(self):
        from analytics.quality import Report
        report = Report()
        for i in range(250):
            report.add("code", i, "detail")
        payload = report.as_dict()
        assert len(payload["issues"]) == 100 and payload["truncated"] is True
        assert payload["issue_count"] == 250


# =========================================================================== #
# 7. API BEHAVIOUR, EMPTY STATES AND AUTHORIZATION (Steps 10, 11)
# =========================================================================== #

class TestTradePnlEndpoint:

    def test_empty_account_reports_zero_trades_not_a_win_rate(self, client,
                                                              fake_db, auth_headers):
        body = client.get("/api/trades/pnl", headers=auth_headers).json()
        assert body["total_trades"] == 0
        assert body["win_rate"] == 0
        assert body["wins"] == body["losses"] == body["breakeven"] == 0

    def test_single_trade(self, client, fake_db, test_user, auth_headers):
        fake_db.trades.docs.append(_trade(user_id=str(test_user["_id"]), pnl=250.0))
        body = client.get("/api/trades/pnl", headers=auth_headers).json()
        assert body["total_pnl"] == 250.0
        assert body["win_rate"] == 100.0

    def test_outcomes_and_paper_isolation_over_the_api(self, client, fake_db,
                                                       test_user, auth_headers):
        uid = str(test_user["_id"])
        fake_db.trades.docs.extend([
            _trade(user_id=uid, pnl=100.0),
            _trade(user_id=uid, pnl=-50.0),
            _trade(user_id=uid, pnl=0.0),
            _trade(user_id=uid, pnl=9999.0, is_paper=True),
            _trade(user_id=uid, status="OPEN", pnl=None, exit_time=None),
        ])
        body = client.get("/api/trades/pnl", headers=auth_headers).json()
        assert body["total_pnl"] == 50.0
        assert (body["wins"], body["losses"], body["breakeven"]) == (1, 1, 1)
        assert body["win_rate"] == pytest.approx(33.3)
        assert body["open_trades"] == 1
        assert body["scope"] == "live" and body["basis"] == "gross"

    def test_today_is_the_ist_day(self, client, fake_db, test_user, auth_headers):
        uid = str(test_user["_id"])
        today = periods.resolve("today")
        fake_db.trades.docs.extend([
            _trade(user_id=uid, pnl=100.0, exit_time=today.start_iso),
            # One second before the IST day began — yesterday.
            _trade(user_id=uid, pnl=777.0,
                   exit_time=_iso(periods.to_datetime(today.start_iso)
                                  - timedelta(seconds=1))),
        ])
        body = client.get("/api/trades/pnl", headers=auth_headers).json()
        assert body["today_pnl"] == 100.0
        assert body["total_pnl"] == 877.0
        assert body["window"]["timezone"] == "Asia/Kolkata"

    def test_requires_authentication(self, client, fake_db):
        assert client.get("/api/trades/pnl").status_code == 401

    def test_never_returns_another_users_trades(self, client, fake_db,
                                                test_user, other_user, auth_headers):
        fake_db.trades.docs.append(_trade(user_id=str(other_user["_id"]), pnl=5000.0))
        body = client.get("/api/trades/pnl", headers=auth_headers).json()
        assert body["total_pnl"] == 0
        assert body["total_trades"] == 0


class TestAdminAnalyticsContract:
    """Step 7. PH3.8 does not remove the admin mocks — so these tests hold the
    flags in place until PH3.9 does."""

    def test_user_analytics_flags_its_fabricated_metrics(self, client, fake_db,
                                                         admin_headers):
        body = client.get("/api/admin/analytics/users", headers=admin_headers).json()
        assert set(body["mock_metrics"]) == {
            "dau", "mau", "retention_rate", "churn_rate", "growth_rate"}
        metrics = body["analytics"]["metrics"]
        assert metrics["retention_rate"]["provenance"] == "mock"
        assert metrics["total_users"]["provenance"] == "real"
        assert body["analytics"]["trustworthy"] is False
        for name in body["mock_metrics"]:
            assert metrics[name]["note"], f"{name} is flagged with no reason"

    def test_revenue_series_declares_itself_fabricated(self, client, fake_db,
                                                       admin_headers):
        body = client.get("/api/admin/analytics/revenue", headers=admin_headers).json()
        assert body["status"] == "mock"
        assert all(point["mock"] is True for point in body["daily_revenue"])
        assert body["required_source"]

    def test_payment_stats_flag_every_revenue_figure(self, client, fake_db,
                                                     admin_headers):
        body = client.get("/api/admin/payments/stats", headers=admin_headers).json()
        for name in ("mrr", "arr", "revenue_today", "revenue_week",
                     "pending_payments", "refunds", "failed_payments"):
            assert name in body["mock_metrics"], name
        # The role counts beside them ARE real and must not be over-flagged.
        assert body["analytics"]["metrics"]["premium_count"]["provenance"] == "real"

    def test_feature_usage_separates_real_counts_from_invented_percentages(
            self, client, fake_db, admin_headers):
        body = client.get("/api/admin/analytics/features", headers=admin_headers).json()
        by_name = {f["name"]: f for f in body["features"]}
        assert by_name["AI Chat"]["usage_count_provenance"] == "derived"
        assert by_name["Backtesting"]["usage_count_provenance"] == "mock"
        assert all(f["percentage_provenance"] == "mock" for f in body["features"])

    def test_dashboard_names_its_fabricated_fields(self, client, fake_db, admin_headers):
        body = client.get("/api/admin/dashboard", headers=admin_headers).json()
        assert {"revenue_today", "mrr", "arr"} <= set(body["mock_metrics"])
        assert body["window"]["timezone"] == "Asia/Kolkata"

    def test_api_health_declares_that_it_is_not_probing(self, client, fake_db,
                                                        admin_headers):
        body = client.get("/api/admin/apis/health", headers=admin_headers).json()
        assert body["status"] == "mock"
        assert "overall_status" in body["mock_metrics"]

    def test_ai_status_declares_its_literals(self, client, fake_db, admin_headers):
        body = client.get("/api/admin/ai/status", headers=admin_headers).json()
        assert {"latency_ms", "failures", "estimated_cost"} <= set(body["mock_metrics"])


class TestAdminAnalyticsAuthorization:
    """Step 11. Admin analytics expose platform-wide business data; they must
    stay admin-only."""

    ROUTES = [
        "/api/admin/dashboard",
        "/api/admin/analytics/users",
        "/api/admin/analytics/revenue",
        "/api/admin/analytics/features",
        "/api/admin/payments/stats",
        "/api/admin/ai/status",
        "/api/admin/ai/usage",
        "/api/admin/apis/health",
    ]

    @pytest.mark.parametrize("route", ROUTES)
    def test_anonymous_is_rejected(self, client, fake_db, route):
        assert client.get(route).status_code == 401

    @pytest.mark.parametrize("route", ROUTES)
    def test_ordinary_user_is_rejected(self, client, fake_db, auth_headers, route):
        assert client.get(route, headers=auth_headers).status_code == 403

    @pytest.mark.parametrize("route", ROUTES)
    def test_admin_is_admitted(self, client, fake_db, admin_headers, route):
        assert client.get(route, headers=admin_headers).status_code == 200


class TestJournalEndpoints:

    def test_stats_empty_state(self, client, fake_db, auth_headers):
        body = client.get("/api/journal/stats", headers=auth_headers).json()
        assert body["all_time"]["total"] == 0
        assert body["all_time"]["win_rate"] == 0
        assert body["scope"] == "live" and body["basis"] == "gross"

    def test_setup_stats_empty_state_explains_itself(self, client, fake_db,
                                                     auth_headers):
        body = client.get("/api/journal/setup-stats", headers=auth_headers).json()
        assert body["setups"] == []
        assert body["empty_reason"]

    def test_stats_are_user_scoped(self, client, fake_db, test_user,
                                   other_user, auth_headers):
        fake_db.trades.docs.append(_trade(user_id=str(other_user["_id"]), pnl=9999.0))
        body = client.get("/api/journal/stats", headers=auth_headers).json()
        assert body["all_time"]["total"] == 0


class TestPortfolioAnalyticsEndpoints:

    def test_summary_exposes_an_unambiguous_alias(self, client, fake_db, auth_headers):
        """`total_pnl` is lifetime UNREALISED P&L; the Dashboard labelled it
        "Today's P/L" (F-7). The legacy key stays for existing consumers, and
        `unrealized_pnl` is the name new code reads."""
        body = client.get("/api/portfolio/summary", headers=auth_headers).json()
        assert body["unrealized_pnl"] == body["total_pnl"]
        assert body["scope"] == "live"

    def test_performance_unavailable_state_is_not_a_zero_curve(self, client, fake_db,
                                                               auth_headers):
        body = client.get("/api/portfolio/performance", headers=auth_headers).json()
        assert body["available"] is False
        assert body["curve"] == []
        assert body["reason"]

    def test_performance_rejects_an_unknown_range(self, client, fake_db, auth_headers):
        response = client.get("/api/portfolio/performance?range=DECADE",
                              headers=auth_headers)
        assert response.status_code == 400
        assert "DECADE" in response.json()["detail"]

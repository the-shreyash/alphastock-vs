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
* PH3.8 asserted that the MOCK metrics were still *flagged* — the load-bearing
  guarantee of an audit sprint, which removed nothing. **PH3.9 removed them**,
  so those assertions inverted: `TestAdminAnalyticsMockRemoval` now names each
  old fabricated value and asserts it can no longer be returned. Naming the
  specific wrong value matters — a test that only checks "the field is not
  mock-flagged" passes again the moment somebody reintroduces the formula
  without the flag.
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

    def test_every_unanswerable_metric_names_what_would_answer_it(self):
        """An UNAVAILABLE entry with no named production source is not a
        handoff, it is a complaint."""
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

    def test_no_metric_is_classified_mock(self):
        """PH3.9's headline assertion. The registry held 17 MOCK entries; the
        removal sprint's whole purpose was to take that to zero, and this is
        what stops a nineteenth fabricated metric being added quietly later."""
        mocks = [s.key for s in registry.by_provenance(contract.MOCK)]
        assert mocks == [], f"fabricated metrics are back in the registry: {mocks}"

    def test_the_other_three_classes_are_all_populated(self):
        for provenance in (contract.REAL, contract.DERIVED, contract.UNAVAILABLE):
            assert registry.summary()[provenance] > 0, f"nothing classified {provenance}"

    def test_ph39_inventory_is_priority_ordered(self):
        priorities = [s.priority for s in registry.ph39_inventory()]
        assert priorities == sorted(priorities)

    def test_every_ph38_mock_records_what_ph39_did_to_it(self):
        """The registry is the record of the removal, not just of the current
        state. Each of these was MOCK before PH3.9; each must say what happened.
        Without this, "which mocks were removed and what replaced them" is only
        answerable from a changelog, which drifts."""
        removed = {
            "admin.revenue_today", "admin.mrr", "admin.arr", "admin.revenue_series",
            "admin.revenue_window_totals", "admin.payment_states", "admin.dau",
            "admin.mau", "admin.retention_rate", "admin.churn_rate",
            "admin.growth_rate", "admin.feature_usage_pct",
            "admin.ai_provider_latency", "admin.ai_estimated_cost",
            "admin.api_health", "admin.redis_status", "research.backtest.synthetic",
        }
        assert len(removed) == 17, "PH3.8 classified exactly 17 metrics MOCK"
        for key in sorted(removed):
            spec = registry.get(key)
            assert spec is not None, f"{key} vanished from the registry entirely"
            assert spec.provenance != contract.MOCK, key
            assert spec.ph39_resolution, f"{key} does not record what PH3.9 did to it"

    def test_revenue_metrics_are_never_real_or_derived(self):
        """A guard against the single most tempting future mistake: making the
        revenue numbers look real by reclassifying them, instead of by wiring a
        payment provider. UNAVAILABLE is the only honest class for these until
        `analytics.sources.payments_integration()` says otherwise."""
        for key in ("admin.mrr", "admin.arr", "admin.revenue_today",
                    "admin.revenue_series", "admin.revenue_window_totals",
                    "admin.payment_states", "admin.arpu"):
            assert registry.get(key).provenance == contract.UNAVAILABLE, key


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


#: Every admin analytics surface. `mock_metrics` must be empty on all of them —
#: parametrised rather than repeated so a surface added later is covered by
#: construction, the same technique PH3.5 used for the authorization sweep.
ADMIN_ANALYTICS_ROUTES = [
    "/api/admin/dashboard",
    "/api/admin/analytics/users",
    "/api/admin/analytics/revenue",
    "/api/admin/analytics/features",
    "/api/admin/payments/stats",
    "/api/admin/ai/status",
    "/api/admin/ai/usage",
    "/api/admin/apis/health",
    "/api/admin/system/health",
]


class TestAdminAnalyticsMockRemoval:
    """PH3.9. The inverse of PH3.8's `TestAdminAnalyticsContract`.

    PH3.8 asserted the fabricated numbers were *flagged*; these assert they are
    *gone*, and each one names the specific old value. That matters: a test that
    only checks "not mock-flagged" goes green the moment somebody reinstates the
    formula and forgets the flag — which is precisely how these numbers got into
    production the first time.
    """

    @pytest.mark.parametrize("route", ADMIN_ANALYTICS_ROUTES)
    def test_no_admin_surface_returns_a_fabricated_metric(self, client, fake_db,
                                                          admin_headers, route):
        body = client.get(route, headers=admin_headers).json()
        assert body.get("mock_metrics") == [], f"{route} still declares mocks"

    @pytest.mark.parametrize("route", ADMIN_ANALYTICS_ROUTES)
    def test_no_admin_surface_declares_mock_provenance_anywhere(
            self, client, fake_db, admin_headers, route):
        """A sweep of the whole payload, not just the summary field. A response
        can drop `mock_metrics` and still carry `provenance: "mock"` on a nested
        metric — and the analytics contract makes that state unconstructible, so
        finding one means somebody bypassed the contract."""
        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("provenance", "status"):
                        assert value != "mock", f"{route} carries {key}=mock"
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
        walk(client.get(route, headers=admin_headers).json())

    # -- revenue ------------------------------------------------------------ #
    def test_revenue_is_unavailable_not_zero(self, client, fake_db, admin_headers):
        """The distinction the whole sprint turns on. ₹0 says 'we measured and
        found no revenue'; null plus a reason says 'we have no revenue source'."""
        body = client.get("/api/admin/payments/stats", headers=admin_headers).json()
        for name in ("mrr", "arr", "revenue_today", "revenue_week",
                     "revenue_month", "revenue_year",
                     "pending_payments", "refunds", "failed_payments"):
            assert body[name] is None, f"{name} came back as {body[name]!r}, not null"
            metric = body["analytics"]["metrics"][name]
            assert metric["status"] == "unavailable", name
            assert metric["value"] is None, name
            assert metric["note"], f"{name} is unavailable with no reason"

    def test_revenue_today_is_not_a_payment_count_times_499(self, client, fake_db,
                                                            admin_headers):
        """The exact old formula: `count(all payments) x 499`, not a sum, not
        date-filtered. Seeded with three payment documents of unmistakable
        amounts, so the old implementation would answer 1497 and any naive
        "just sum the collection" replacement would answer 30."""
        fake_db.payments.docs.extend([
            {"amount": 10, "status": "created"},
            {"amount": 10, "status": "failed"},
            {"amount": 10, "status": "captured"},
        ])
        body = client.get("/api/admin/dashboard", headers=admin_headers).json()
        assert body["revenue_today"] is None, (
            "revenue must not be computed from payment documents while the platform "
            "has no payment integration — the pre-PH3.9 code reported 3 x 499 = 1497")
        assert body["mrr"] is None and body["arr"] is None

    def test_mrr_is_not_role_counts_times_a_hardcoded_price(self, client, fake_db,
                                                            admin_headers):
        """Pre-PH3.9: pro/premium x 499 + elite x 999. Two pro users and one
        elite would have reported MRR 1997 / ARR 23964 — every one of them
        granted by an admin with no payment involved."""
        fake_db.users.docs.extend([
            {"_id": ObjectId(), "email": "a@x.com", "role": "pro"},
            {"_id": ObjectId(), "email": "b@x.com", "role": "pro"},
            {"_id": ObjectId(), "email": "c@x.com", "role": "elite"},
        ])
        body = client.get("/api/admin/payments/stats", headers=admin_headers).json()
        assert body["mrr"] is None and body["arr"] is None
        # The role counts themselves are real and must NOT be suppressed with them.
        assert body["premium_count"] == 2
        assert body["elite_count"] == 1
        assert body["analytics"]["metrics"]["premium_count"]["provenance"] == "real"

    def test_revenue_series_is_empty_not_a_generated_curve(self, client, fake_db,
                                                           admin_headers):
        """Pre-PH3.9: 30 points of `2500 + i*150 + (500 if i % 7 == 0)`, with no
        database access at all. Empty — not thirty points at zero, which would
        still be the claim 'we measured thirty days and found nothing'."""
        body = client.get("/api/admin/analytics/revenue", headers=admin_headers).json()
        assert body["daily_revenue"] == []
        assert body["status"] == "unavailable"
        assert body["backfillable"] is False
        assert body["required_source"]

    def test_refund_endpoint_no_longer_audits_a_refund_it_did_not_perform(
            self, client, fake_db, admin_headers):
        """PH3.5's D-4, fixed here. It returned `{"success": true}` for any
        string while writing `payment.refunded` to the immutable audit log —
        telling an operator a customer was refunded, and recording it, when
        nobody was. The audit-log assertion is the important half."""
        before = len(fake_db.admin_audit_logs.docs)
        response = client.post("/api/admin/payments/nonexistent-id/refund",
                               headers=admin_headers)
        assert response.status_code == 501, "the refund stub must not report success"
        assert response.json()["detail"]
        assert len(fake_db.admin_audit_logs.docs) == before, (
            "a refund that did not happen must not appear in the audit log")

    # -- user analytics ----------------------------------------------------- #
    def test_dau_counts_session_activity_not_signups(self, client, fake_db,
                                                     test_user, auth_headers,
                                                     admin_headers):
        """Pre-PH3.9 DAU was today's SIGNUP count. Seeded here with the two
        populations deliberately disjoint: three users signed up today and none
        of them has a session, while two *other* users were active today. The
        old code answers 3; the correct answer is 2."""
        now = datetime.now(timezone.utc)
        fake_db.users.docs.extend([
            {"_id": ObjectId(), "email": f"new{i}@x.com", "role": "user",
             "created_at": now.isoformat()} for i in range(3)
        ])
        fake_db.sessions.docs.extend([
            {"session_id": "s1", "user_id": "active-1", "last_used_at": now.isoformat()},
            {"session_id": "s2", "user_id": "active-2", "last_used_at": now.isoformat()},
            # Same user twice: DAU is DISTINCT users, not session count.
            {"session_id": "s3", "user_id": "active-1", "last_used_at": now.isoformat()},
            # Yesterday — outside today's window.
            {"session_id": "s4", "user_id": "active-9",
             "last_used_at": (now - timedelta(days=2)).isoformat()},
        ])
        body = client.get("/api/admin/analytics/users", headers=admin_headers).json()
        assert body["dau"] == 2, (
            "DAU must count distinct users active today, not today's signups "
            f"(signups today = {body['today_signups']})")

    def test_mau_is_unavailable_because_sessions_are_reaped(self, client, fake_db,
                                                            admin_headers):
        """The PH3.8 inventory prescribed a 30-day query over db.sessions. It
        cannot be answered: the TTL index deletes a session one refresh lifetime
        (7 days by default) after last use, so a 30-day window would report a
        7-day count under a 30-day label."""
        body = client.get("/api/admin/analytics/users", headers=admin_headers).json()
        assert body["mau"] is None
        metric = body["analytics"]["metrics"]["mau"]
        assert metric["status"] == "unavailable"
        assert "retain" in metric["note"] or "TTL" in metric["note"]

    def test_retention_and_churn_are_unavailable_not_literals(self, client, fake_db,
                                                              admin_headers):
        """Pre-PH3.9 these were the literals 78.5 and 4.2 — constants that did
        not move when users left."""
        body = client.get("/api/admin/analytics/users", headers=admin_headers).json()
        assert body["retention_rate"] is None, "78.5 was a constant in the source"
        assert body["churn_rate"] is None, "4.2 was a constant in the source"

    def test_growth_rate_is_computed_from_signups_not_the_literal_12_8(
            self, client, fake_db, admin_headers):
        """Four signups in the current 30-day window against two in the one
        before it is +100%. The old code answered 12.8 regardless."""
        now = datetime.now(timezone.utc)
        fake_db.users.docs.extend(
            [{"_id": ObjectId(), "email": f"cur{i}@x.com",
              "created_at": (now - timedelta(days=1)).isoformat()} for i in range(4)]
            + [{"_id": ObjectId(), "email": f"prev{i}@x.com",
                "created_at": (now - timedelta(days=40)).isoformat()} for i in range(2)]
        )
        body = client.get("/api/admin/analytics/users", headers=admin_headers).json()
        assert body["growth_rate"] == 100.0, body["growth_rate"]
        comparison = body["analytics"]["metrics"]["growth_rate"]["comparison"]
        assert comparison["current"] == 4 and comparison["previous"] == 2

    def test_growth_rate_from_a_zero_base_is_unavailable_not_infinite(
            self, client, fake_db, admin_headers):
        """The first signup of a platform's life is not +100% growth and not
        +infinity. With an empty comparison period there is no base, so there is
        no percentage."""
        fake_db.users.docs.append({
            "_id": ObjectId(), "email": "first@x.com",
            "created_at": datetime.now(timezone.utc).isoformat()})
        body = client.get("/api/admin/analytics/users", headers=admin_headers).json()
        assert body["growth_rate"] is None
        assert body["analytics"]["metrics"]["growth_rate"]["status"] == "unavailable"

    # -- feature usage ------------------------------------------------------ #
    def test_feature_usage_drops_the_invented_percentages_and_empty_rows(
            self, client, fake_db, admin_headers):
        """Pre-PH3.9: ten rows, a fixed descending percentage list (85, 72, 68,
        55, ...) unrelated to the count beside it, and seven counts that were
        the literal 0 because nothing measures those features."""
        body = client.get("/api/admin/analytics/features", headers=admin_headers).json()
        names = [f["name"] for f in body["features"]]
        assert names == ["AI Chat", "Trading", "Notifications"], names
        assert all(f["adoption_pct"] is None for f in body["features"])
        assert all("percentage" not in f for f in body["features"])
        for uncounted in ("Stock Scanner", "Morning Report", "Backtesting"):
            assert uncounted not in names, (
                f"{uncounted} is not measured anywhere; a row reading 0 is a "
                "measurement claim nothing supports")

    def test_feature_usage_counts_are_still_real(self, client, fake_db,
                                                 test_user, admin_headers):
        fake_db.chat_messages.docs.extend([{"user_id": "u", "content": "hi"}] * 5)
        body = client.get("/api/admin/analytics/features", headers=admin_headers).json()
        chat = next(f for f in body["features"] if f["name"] == "AI Chat")
        assert chat["usage_count"] == 5
        assert chat["usage_count_provenance"] == "derived"

    # -- AI and platform health --------------------------------------------- #
    def test_ai_status_reports_no_literal_latency_or_zero_failures(
            self, client, fake_db, admin_headers):
        """Pre-PH3.9: latency_ms 1200 (Claude) / 900 (Gemini), failures 0,
        fallbacks 0 — literals sitting beside live counters, so an operator
        could not see an outage the platform was already measuring."""
        body = client.get("/api/admin/ai/status", headers=admin_headers).json()
        latencies = [p["p95_latency_ms"] for p in body["providers"]]
        assert 1200 not in latencies and 900 not in latencies
        for provider in body["providers"]:
            assert "latency_ms" not in provider, "the literal field is gone"
            # No traffic yet must read as None, never as an instantaneous 0.
            assert provider["p95_latency_ms"] is None
            assert provider["status"] == "no_traffic"
        assert body["estimated_cost"] is None, "a per-token cost is not recorded"
        # The process-scope caveat must travel with every counter-derived number.
        assert body["scope"]["basis"] == "process_lifetime"

    def test_ai_usage_drops_the_invented_per_user_cost(self, client, fake_db,
                                                       admin_headers):
        fake_db.chat_messages.docs.extend([{"user_id": "u1", "content": "hi"}] * 3)
        body = client.get("/api/admin/ai/usage", headers=admin_headers).json()
        assert body["top_users"][0]["message_count"] == 3
        assert body["top_users"][0]["estimated_cost"] is None, (
            "was count x 0.011 — a flat per-message rate in an ambiguous currency")

    def test_api_health_probes_rather_than_reading_credentials(
            self, client, fake_db, admin_headers):
        """Pre-PH3.9 `overall_status` was the constant "healthy" — this page
        reported a healthy platform during a total provider outage."""
        body = client.get("/api/admin/apis/health", headers=admin_headers).json()
        assert body["overall_status"] != "healthy", (
            "'healthy' was the hardcoded constant; the derived vocabulary is "
            "operational / degraded / no_traffic")
        assert body["status"] == "derived"
        for api in body["apis"]:
            assert "latency_ms" not in api
            assert "requests_today" not in api
            if not api["instrumented"]:
                assert api["status"] == "not_measured", (
                    f"{api['name']} is not instrumented and must not claim a status")

    def test_api_health_no_longer_invents_a_razorpay_integration(
            self, client, fake_db, admin_headers):
        """It reported `status: "configured"` beside a 300ms latency for a
        payment integration that does not exist anywhere in the codebase."""
        body = client.get("/api/admin/apis/health", headers=admin_headers).json()
        names = [a["name"] for a in body["apis"]]
        assert not any("Razorpay" in n for n in names), names
        # Nor the per-vendor market-data rows, which the gateway architecture
        # deliberately makes unanswerable (MARKET_DATA_ARCHITECTURE.md).
        assert not any(n in ("Yahoo Finance", "Alpha Vantage") for n in names), names

    def test_system_health_probes_redis_and_the_scheduler(self, client, fake_db,
                                                          admin_headers):
        """Pre-PH3.9: redis was the literal "not_configured" (stale from before
        PH2.7 shipped a Redis client) and the scheduler was the constant
        "running" — which stayed "running" after the scheduler died."""
        body = client.get("/api/admin/system/health", headers=admin_headers).json()
        assert body["mock_metrics"] == []
        assert "source" in body["redis"] or "note" in body["redis"]
        scheduler = body["scheduler"]
        assert scheduler["source"] == "apscheduler.running"
        # In a hermetic test the scheduler is genuinely not running, and the
        # endpoint must say so rather than reporting the old constant.
        assert scheduler["status"] == "stopped"
        assert scheduler["running"] is False

    def test_dashboard_health_badges_are_probed_not_literal(self, client, fake_db,
                                                            admin_headers):
        body = client.get("/api/admin/dashboard", headers=admin_headers).json()
        assert "api_health" not in body, "the literal 'healthy' field is gone"
        assert body["server_health"] == "serving"
        assert "dependencies" in body
        assert body["window"]["timezone"] == "Asia/Kolkata"

    def test_dashboard_renames_ai_requests_to_what_it_actually_counts(
            self, client, fake_db, admin_headers):
        """`ai_requests_today` counted stored chat messages — both the user turn
        and the assistant turn — so it overstated provider calls by roughly 2x.
        The value was right; the name was a claim it could not support."""
        body = client.get("/api/admin/dashboard", headers=admin_headers).json()
        assert "ai_requests_today" not in body
        assert body["chat_messages_today"] == 0


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

"""PH3.9 — Mock Removal & Production Data Integrity.

WHAT THIS SUITE PROVES, AND WHY IT IS SHAPED THIS WAY
-----------------------------------------------------
`test_analytics.py` owns the *endpoint-level* removal assertions (its
`TestAdminAnalyticsMockRemoval` class, which is the direct inverse of the
PH3.8 class that pinned the mocks in place). This file owns the two things that
do not belong there:

1. **The units PH3.9 added** — `analytics.sources`, `analytics.platform_health`
   and `analytics.periods.preceding` — tested directly, at the layer that makes
   the guarantee. PH3.8 recorded a method note worth repeating: three of its
   suites "found" a HIGH defect that did not exist because they asserted a
   guarantee at the wrong layer. A gate that refuses a window wider than the
   session retention horizon is a property of `active_users`, not of an HTTP
   route, and testing it through six layers of FastAPI would prove less.

2. **The counter-tests for formulas that no longer exist.** The sprint brief
   asks for these explicitly, and they are the ones most worth writing: a test
   asserting "the field is not flagged as mock" passes again the moment somebody
   reinstates the formula and forgets the flag. So each one below *names the old
   arithmetic* and seeds data chosen to make the old answer and the new answer
   unmistakably different.

WHAT IS DELIBERATELY NOT ASSERTED HERE
--------------------------------------
That the revenue aggregation returns correct sums. It cannot be reached: it is
gated on `payments_integration()`, which is False. What IS asserted is the
gate's behaviour and the aggregation's *policy* — that pending, created and
failed payments are excluded from the captured set — because that policy is the
part somebody will get wrong under deadline when a provider is finally wired,
and it costs nothing to pin it now.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from analytics import contract, periods, platform_health, sources
from analytics.periods import IST, UnknownPeriod
from tests._fakedb import FakeDB

# Hermetic by default — no marker. Nothing here touches the network, a real
# database, or the process clock.


def _run(coro):
    """Drive one coroutine on a private event loop (see test_analytics._run)."""
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


# =========================================================================== #
# 1. THE COMPARISON WINDOW (analytics.periods.preceding)
# =========================================================================== #

class TestPrecedingWindow:
    """Growth needs a base, and a hand-rolled base is how the two halves of a
    growth figure end up covering different spans."""

    def test_the_base_abuts_the_window_exactly(self):
        """Half-open windows must partition time: no gap, no overlap. An event
        at the boundary belongs to exactly one of the pair."""
        window = periods.resolve("30d")
        base = periods.preceding(window)
        assert base.end == window.start
        assert base.start < base.end

    def test_the_base_is_the_same_length_as_the_window(self):
        """A 30-day count over a 31-day base reports a calendar artefact as a
        business trend."""
        for key in ("today", "7d", "30d", "90d", "ytd"):
            window = periods.resolve(key)
            base = periods.preceding(window)
            assert (base.end - base.start) == (window.end - window.start), key

    def test_yesterday_is_the_window_preceding_today(self):
        today = periods.resolve("today")
        assert periods.preceding(today).start == periods.resolve("yesterday").start

    def test_an_unbounded_window_has_no_predecessor(self):
        """'The 30 days before all of time' has no answer, and returning one
        anyway is how a growth rate divides by a window that does not exist."""
        with pytest.raises(UnknownPeriod):
            periods.preceding(periods.resolve("all"))


# =========================================================================== #
# 2. REVENUE — the gate, and the accounting policy behind it
# =========================================================================== #

class TestPaymentsIntegrationGate:

    def test_the_platform_reports_no_payment_integration(self):
        """The structural finding PH3.8 made and PH3.9 acts on: `db.payments`
        has no writer anywhere in the codebase."""
        status = sources.payments_integration()
        assert status["integrated"] is False
        assert status["reason"]
        assert status["collection"] == "db.payments"

    def test_revenue_is_unavailable_even_when_the_collection_has_documents(self):
        """**The load-bearing test of the revenue half of this sprint.**

        The gate is the INTEGRATION, not the emptiness of the collection. Gating
        on emptiness is how the first stray document flips revenue to
        "available" and reports it as fact — which is exactly the shape of the
        defect PH3.8 found (`count(payments) x 499` read 0 only because nothing
        had ever been written).
        """
        db = FakeDB(payments=[
            {"amount": 999999, "status": "captured",
             "captured_at": _iso(datetime.now(timezone.utc))},
        ])
        metric = _run(sources.revenue(db, periods.resolve("today"), name="revenue_today"))
        assert metric.status == contract.UNAVAILABLE
        assert metric.value is None, "a stray document must not become revenue"
        assert metric.note

    def test_an_unavailable_metric_cannot_carry_a_value_at_all(self):
        """Enforced by the contract at construction, so a route cannot ship an
        unavailable metric with a number in it even by mistake."""
        with pytest.raises(contract.ContractError):
            contract.Metric(name="revenue", value=0, provenance=contract.UNAVAILABLE,
                            status=contract.UNAVAILABLE, note="x")

    def test_mrr_needs_more_than_payment_records(self):
        """A one-off capture is not recurring revenue. Summing captures over a
        month is not MRR, and the note must say what is actually required."""
        metric = _run(sources.subscription_revenue(FakeDB(), name="mrr"))
        assert metric.status == contract.UNAVAILABLE
        assert "subscription" in metric.note.lower()

    def test_intent_and_failure_states_are_not_revenue(self):
        """The accounting policy, pinned before any money exists. `created` and
        `pending` are intents; `authorized` is a hold, not a capture; `failed`
        and `cancelled` are not revenue in any sense. Getting this wrong is the
        classic revenue-reporting bug, and it is much cheaper to pin now than to
        discover from a finance discrepancy later."""
        for status in ("created", "pending", "authorized", "requires_action",
                       "failed", "cancelled", "expired", "refunded"):
            assert status not in sources.CAPTURED_STATUSES, status
        assert "captured" in sources.CAPTURED_STATUSES

    def test_the_captured_aggregation_excludes_uncaptured_payments(self):
        """The real aggregation, exercised directly past the gate — so the code
        a payment integration will switch on is tested rather than aspirational.
        Only the two captured rows count: 150, not 1150."""
        now = datetime.now(timezone.utc)
        db = FakeDB(payments=[
            {"amount": 100, "status": "captured", "captured_at": _iso(now)},
            {"amount": 50, "status": "paid", "captured_at": _iso(now)},
            {"amount": 500, "status": "pending", "captured_at": _iso(now)},
            {"amount": 500, "status": "failed", "captured_at": _iso(now)},
        ])
        total = _run(sources._sum_captured(db, periods.resolve("today")))
        assert total["amount"] == 150
        assert total["count"] == 2

    def test_the_captured_aggregation_respects_the_window(self):
        """A capture from six months ago is not today's revenue."""
        now = datetime.now(timezone.utc)
        db = FakeDB(payments=[
            {"amount": 100, "status": "captured", "captured_at": _iso(now)},
            {"amount": 900, "status": "captured",
             "captured_at": _iso(now - timedelta(days=180))},
        ])
        assert _run(sources._sum_captured(db, periods.resolve("today")))["amount"] == 100
        assert _run(sources._sum_captured(db, None))["amount"] == 1000


# =========================================================================== #
# 3. ACTIVE USERS — and the retention horizon that bounds them
# =========================================================================== #

class TestActiveUsers:

    def _sessions(self, *entries):
        return FakeDB(sessions=[
            {"session_id": f"s{i}", "user_id": uid, "last_used_at": _iso(when)}
            for i, (uid, when) in enumerate(entries)
        ])

    def test_distinct_users_not_session_count(self):
        """Three sessions, two users. A user on a phone and a laptop is one
        active user."""
        now = datetime.now(timezone.utc)
        db = self._sessions(("u1", now), ("u1", now), ("u2", now))
        metric = _run(sources.active_users(db, periods.resolve("today"), name="dau"))
        assert metric.value == 2
        assert metric.provenance == contract.DERIVED

    def test_activity_outside_the_window_is_excluded(self):
        now = datetime.now(timezone.utc)
        db = self._sessions(("u1", now), ("u2", now - timedelta(days=3)))
        assert _run(sources.active_users(db, periods.resolve("today"),
                                         name="dau")).value == 1

    def test_the_ist_day_boundary_is_used_not_the_utc_day(self):
        """A UTC day rolls at 05:30 IST. A session used at 02:00 IST is part of
        today's IST session day, and the pre-PH3.8 idiom put it in yesterday."""
        now = datetime(2026, 8, 16, 2, 0, tzinfo=IST).astimezone(timezone.utc)
        window = periods.resolve("today", now=now)
        db = self._sessions(("u1", now))
        assert _run(sources.active_users(db, window, name="dau")).value == 1

    def test_an_empty_window_reports_zero_which_is_a_real_measurement(self):
        """Distinct from UNAVAILABLE. 'Nobody was active today' is a true fact
        about the platform; it is measured, and it is allowed to be zero."""
        metric = _run(sources.active_users(FakeDB(), periods.resolve("today"),
                                           name="dau"))
        assert metric.value == 0
        assert metric.status == contract.AVAILABLE

    def test_a_window_wider_than_the_retention_horizon_is_refused(self):
        """**The correction to PH3.8's inventory.**

        It prescribed a 30-day distinct-user query over `db.sessions`. The
        collection has a TTL index deleting a session one refresh lifetime
        (7 days by default) after last use, so a 30-day window asks for rows the
        database has already removed — producing a 7-day count under a 30-day
        label, undercounting more the longer ago a user churned.
        """
        db = self._sessions(("u1", datetime.now(timezone.utc)))
        metric = _run(sources.active_users(db, periods.resolve("30d"), name="mau"))
        assert metric.status == contract.UNAVAILABLE
        assert metric.value is None, (
            "a truncated count under a full-window label is worse than no count")
        assert "retain" in metric.note

    def test_the_refusal_is_self_correcting(self, monkeypatch):
        """Raise the refresh TTL past thirty days and the same call answers,
        because the data really would be there. The gate is a property of the
        configuration, not a hardcoded 'MAU is impossible'."""
        monkeypatch.setattr(sources, "session_retention_seconds",
                            lambda: 60 * 86400)
        db = self._sessions(("u1", datetime.now(timezone.utc)))
        metric = _run(sources.active_users(db, periods.resolve("30d"), name="mau"))
        assert metric.status == contract.AVAILABLE
        assert metric.value == 1

    def test_the_horizon_is_read_from_configuration(self):
        from security.jwt import refresh_ttl_seconds
        assert sources.session_retention_seconds() == refresh_ttl_seconds()


# =========================================================================== #
# 4. SIGNUP GROWTH
# =========================================================================== #

class TestSignupGrowth:

    def _users(self, *offsets_in_days):
        now = datetime.now(timezone.utc)
        return FakeDB(users=[
            {"email": f"u{i}@x.com", "created_at": _iso(now - timedelta(days=d))}
            for i, d in enumerate(offsets_in_days)
        ])

    def test_growth_is_period_over_period(self):
        """Four in the current 30-day window, two in the one before it: +100%."""
        db = self._users(1, 2, 3, 4, 40, 45)
        metric = _run(sources.signup_growth(db, periods.resolve("30d"),
                                            name="growth_rate"))
        assert metric.value == 100.0
        assert metric.comparison["current"] == 4
        assert metric.comparison["previous"] == 2

    def test_a_decline_is_reported_as_negative(self):
        """The literal it replaces (12.8) could never be negative — which is
        its own tell."""
        db = self._users(1, 40, 41, 42, 43)
        metric = _run(sources.signup_growth(db, periods.resolve("30d"),
                                            name="growth_rate"))
        assert metric.value == -75.0

    def test_growth_from_a_zero_base_is_unavailable_not_infinite(self):
        db = self._users(1, 2)
        metric = _run(sources.signup_growth(db, periods.resolve("30d"),
                                            name="growth_rate"))
        assert metric.status == contract.UNAVAILABLE
        assert metric.value is None
        assert "2" in metric.note, "the absolute count is still reported"

    def test_no_signups_at_all_is_still_unavailable_not_zero_percent(self):
        """0% growth would mean 'we grew by nothing', which is a different claim
        from 'there is no base to compare against'."""
        metric = _run(sources.signup_growth(FakeDB(), periods.resolve("30d"),
                                            name="growth_rate"))
        assert metric.status == contract.UNAVAILABLE


# =========================================================================== #
# 5. RETENTION AND CHURN — unavailable, with reasons that survive review
# =========================================================================== #

class TestUnavailableEngagementMetrics:

    def test_retention_names_the_source_it_needs(self):
        metric = sources.retention_rate(name="retention_rate")
        assert metric.status == contract.UNAVAILABLE
        assert metric.value is None
        assert "back-fill" in metric.note or "reconstruct" in metric.note

    def test_churn_is_blocked_on_the_same_integration_as_mrr(self):
        metric = sources.churn_rate(name="churn_rate")
        assert metric.status == contract.UNAVAILABLE
        assert sources.NO_PAYMENT_INTEGRATION in metric.note

    def test_neither_returns_its_old_literal(self):
        assert sources.retention_rate(name="r").value != 78.5
        assert sources.churn_rate(name="c").value != 4.2


# =========================================================================== #
# 6. PLATFORM HEALTH — read from real counters, with the process caveat
# =========================================================================== #

class TestPlatformHealth:

    @pytest.fixture(autouse=True)
    def _isolated_metrics(self):
        """Counters are process-global; reset around each test so one test's
        recorded call cannot make another's assertion pass."""
        from observability import metrics
        metrics.reset_for_tests()
        yield
        metrics.reset_for_tests()

    def test_no_traffic_reports_none_not_zero_latency(self):
        """A provider nobody has called has no p95. Reporting 0ms would say
        'instantaneous', which is the opposite of the truth."""
        rows = platform_health.api_health({})["apis"]
        market = next(r for r in rows if r["provider"] == "market_data")
        assert market["p95_latency_ms"] is None
        assert market["status"] == "no_traffic"
        assert market["requests_since_start"] == 0

    def test_a_failing_provider_shows_as_degraded(self):
        from observability import metrics
        metrics.provider_requests_total.inc(4, ("market_data", "get_quote", "ok"))
        metrics.provider_requests_total.inc(1, ("market_data", "get_quote", "error"))
        market = next(r for r in platform_health.api_health({})["apis"]
                      if r["provider"] == "market_data")
        assert market["status"] == "degraded"
        assert market["requests_since_start"] == 5
        assert market["error_rate_pct"] == 20.0

    def test_empty_responses_are_reported_separately_from_errors(self):
        """A market-data feed answering 200 with no rows is the failure mode a
        status-code check misses entirely: every dashboard stays green while the
        product shows yesterday's prices."""
        from observability import metrics
        metrics.provider_requests_total.inc(8, ("market_data", "get_quote", "ok"))
        metrics.provider_requests_total.inc(2, ("market_data", "get_quote", "empty"))
        market = next(r for r in platform_health.api_health({})["apis"]
                      if r["provider"] == "market_data")
        assert market["empty_rate_pct"] == 20.0
        assert market["error_rate_pct"] == 0.0

    def test_uninstrumented_integrations_report_not_measured(self):
        """Never a green badge for something nobody is watching. `configured` is
        a fact about the environment; it is not evidence the service works, and
        conflating the two was this endpoint's original defect."""
        rows = platform_health.api_health({"broker_zerodha": True})["apis"]
        broker = next(r for r in rows if r["provider"] == "broker_zerodha")
        assert broker["configured"] is True
        assert broker["status"] == platform_health.NOT_MEASURED
        assert broker["p95_latency_ms"] is None

    def test_the_vendor_rows_the_gateway_forbids_are_absent(self):
        """MARKET_DATA_ARCHITECTURE.md: the Source Manager's upstream choice is
        deliberately invisible above the gateway, so per-vendor latency cannot
        be sourced honestly and must not be offered."""
        names = [r["name"] for r in platform_health.api_health({})["apis"]]
        for vendor in ("Yahoo Finance", "Alpha Vantage", "Razorpay"):
            assert vendor not in names, vendor

    def test_latency_is_a_p95_bucket_bound_not_a_mean(self):
        """Ninety-nine fast calls and one very slow one. The mean is ~0.1s and
        hides the outage; the p95 bound must land in the fast region while the
        slow call remains visible in the counter."""
        from observability import metrics
        for _ in range(99):
            metrics.provider_request_duration_seconds.observe(
                0.05, ("market_data", "get_quote"))
        metrics.provider_request_duration_seconds.observe(
            20.0, ("market_data", "get_quote"))
        bound = platform_health._latency_bound(
            metrics.provider_request_duration_seconds, ("market_data",))
        assert bound == 50.0, "p95 of 100 observations is the 95th, which is 0.05s"

    def test_ai_fallbacks_count_simulated_responses(self):
        """`fallbacks: 0` was a literal. Every simulated response is a user who
        got a canned answer because no real model replied — the single most
        important number on the AI page."""
        from observability import metrics
        metrics.ai_requests_total.inc(3, ("simulated", "ok"))
        metrics.ai_requests_total.inc(1, ("claude", "ok"))
        report = platform_health.ai_providers({"claude": True})
        assert report["fallbacks_since_start"] == 3

    def test_ai_failures_come_from_the_real_error_counter(self):
        from observability import metrics
        metrics.ai_requests_total.inc(2, ("claude", "error"))
        metrics.ai_request_errors_total.inc(2, ("claude", "timeout"))
        claude = next(p for p in platform_health.ai_providers({})["providers"]
                      if p["provider"] == "claude")
        assert claude["failures_since_start"] == 2
        assert claude["status"] == "degraded"
        assert claude["error_classes"] == {"timeout": 2}

    def test_counter_derived_numbers_carry_the_process_scope_caveat(self):
        """A counter answers 'since this process started', never 'today'. PH3.8's
        inventory said to rewire 'AI requests today' to `ai_requests_total`;
        doing that literally would have swapped a fabricated number for a
        mislabelled one."""
        scope = platform_health.process_scope()
        assert scope["basis"] == "process_lifetime"
        assert scope["process_uptime_seconds"] >= 0
        assert "restart" in scope["note"]
        for report in (platform_health.api_health({}), platform_health.ai_providers({})):
            assert report["scope"]["basis"] == "process_lifetime"

    def test_no_field_produced_here_is_named_today(self):
        """A structural guard on the naming rule, not a spot check."""
        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)
        for report in (platform_health.api_health({}), platform_health.ai_providers({})):
            for key in keys(report):
                assert "today" not in key, f"{key} claims a calendar window"

    def test_the_scheduler_is_asked_rather_than_assumed(self):
        """The old value was the constant "running", which stayed "running"
        after the scheduler died — green exactly when it needed to be red. In a
        hermetic test the scheduler genuinely is not running."""
        status = platform_health.scheduler_status()
        assert status["source"] == "apscheduler.running"
        assert status["running"] is False
        assert status["status"] == "stopped"


# =========================================================================== #
# 7. BACKTESTING — the fabricated fallback, and its absence
# =========================================================================== #

class TestSyntheticBacktestRemoved:

    def test_the_fabricating_function_no_longer_exists(self):
        """Named directly, because this is the one whose reintroduction would be
        hardest to spot from an endpoint assertion: the fabricated result was
        passed through the same `_compute_metrics` as the real path, so it came
        back looking exactly like measured statistics."""
        from services import backtest_engine
        assert not hasattr(backtest_engine, "_synthetic_backtest")

    def test_the_module_no_longer_imports_random(self):
        """`randint(10, 16)` of 20 trades meant the win rate was always 50–80%
        and a losing strategy could not be represented."""
        import inspect

        from services import backtest_engine
        source = inspect.getsource(backtest_engine)
        assert "random.randint" not in source
        assert "random.uniform" not in source

    def test_missing_history_raises_rather_than_inventing_a_result(self, monkeypatch):
        from services import backtest_engine

        class _Ticker:
            def __init__(self, *a, **kw):
                pass

            def history(self, **kw):
                raise RuntimeError("provider unreachable")

        monkeypatch.setitem(__import__("sys").modules, "yfinance",
                            type("yf", (), {"Ticker": _Ticker}))
        with pytest.raises(backtest_engine.HistoricalDataUnavailable):
            _run(backtest_engine.run_backtest("TCS", "2025-01-01", "2025-06-01",
                                              "RSI_STRATEGY", 2.0, 4.0))

    def test_the_route_answers_503_not_a_fabricated_200(self, client, fake_db,
                                                        monkeypatch):
        """503, not 500: the request was valid and the strategy is fine; an
        upstream data source is unavailable, which is retryable. Before PH3.9
        this path returned 200 with an invented 50–80% win rate."""
        class _Ticker:
            def __init__(self, *a, **kw):
                pass

            def history(self, **kw):
                raise RuntimeError("provider unreachable")

        monkeypatch.setitem(__import__("sys").modules, "yfinance",
                            type("yf", (), {"Ticker": _Ticker}))
        response = client.post("/api/backtest", json={
            "symbol": "TCS", "start_date": "2025-01-01", "end_date": "2025-06-01",
            "strategy": "RSI_STRATEGY", "stop_loss_pct": 2.0, "target_pct": 4.0,
        })
        assert response.status_code == 503
        body = response.json()
        assert "win_rate" not in body
        assert body["detail"]

    def test_the_failure_detail_does_not_leak_the_provider_error(self, client,
                                                                 fake_db, monkeypatch):
        """A provider's own message can embed a URL or a key. The class of
        failure is returned; the unabridged error goes to the log."""
        class _Ticker:
            def __init__(self, *a, **kw):
                pass

            def history(self, **kw):
                raise RuntimeError("https://api.example.com/?apikey=SECRET123")

        monkeypatch.setitem(__import__("sys").modules, "yfinance",
                            type("yf", (), {"Ticker": _Ticker}))
        response = client.post("/api/backtest", json={
            "symbol": "TCS", "start_date": "2025-01-01", "end_date": "2025-06-01",
            "strategy": "RSI_STRATEGY", "stop_loss_pct": 2.0, "target_pct": 4.0,
        })
        assert "SECRET123" not in response.text


# =========================================================================== #
# 8. AUTHORIZATION AND TENANT ISOLATION (Step 14)
# =========================================================================== #

class TestPh39Authorization:
    """The new sources read platform-wide data — sessions, users, payments — so
    the boundary matters more after this sprint, not less."""

    ROUTES = [
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

    @pytest.mark.parametrize("route", ROUTES)
    def test_anonymous_is_rejected(self, client, fake_db, route):
        assert client.get(route).status_code == 401

    @pytest.mark.parametrize("route", ROUTES)
    def test_an_ordinary_user_is_rejected(self, client, fake_db, auth_headers, route):
        assert client.get(route, headers=auth_headers).status_code == 403

    @pytest.mark.parametrize("route", ROUTES)
    def test_an_admin_is_admitted(self, client, fake_db, admin_headers, route):
        assert client.get(route, headers=admin_headers).status_code == 200

    def test_the_refund_endpoint_is_still_admin_only(self, client, fake_db,
                                                     auth_headers):
        """501 must not become a way around the guard: authorization is checked
        before the not-implemented answer."""
        assert client.post("/api/admin/payments/x/refund").status_code == 401
        assert client.post("/api/admin/payments/x/refund",
                           headers=auth_headers).status_code == 403

    def test_dau_does_not_expose_who_was_active(self, client, fake_db, admin_headers):
        """An aggregate is the whole point. A user id or an email in this
        payload would turn a platform metric into a disclosure of individual
        users' activity."""
        now = datetime.now(timezone.utc)
        fake_db.sessions.docs.extend([
            {"session_id": "s1", "user_id": "secret-user-id",
             "ip": "203.0.113.9", "user_agent": "Firefox",
             "last_used_at": _iso(now)},
        ])
        text = client.get("/api/admin/analytics/users", headers=admin_headers).text
        assert "secret-user-id" not in text
        assert "203.0.113.9" not in text
        assert "Firefox" not in text

    def test_payment_stats_expose_no_provider_internals(self, client, fake_db,
                                                        admin_headers):
        body = client.get("/api/admin/payments/stats", headers=admin_headers).text
        for leak in ("mongodb://", "Traceback", "site-packages", "motor", "pymongo"):
            assert leak not in body, leak


# =========================================================================== #
# 9. QUERY COST (Step 13)
# =========================================================================== #

class TestPh39QueryCost:
    """Replacing literals with database reads is exactly how an N+1 gets
    introduced, so the new queries are counted rather than assumed."""

    def _counting_db(self, **collections):
        db = FakeDB(**collections)
        counts = {"n": 0}
        # dict.fromkeys, not a set: wrapping the same collection twice would
        # double-count every query through it and make this harness lie.
        for name in dict.fromkeys(list(collections) + ["sessions", "users", "payments"]):
            collection = getattr(db, name)
            for method in ("count_documents",):
                original = getattr(collection, method)

                async def wrapper(*a, _o=original, **kw):
                    counts["n"] += 1
                    return await _o(*a, **kw)
                setattr(collection, method, wrapper)
            original_agg = collection.aggregate

            def agg_wrapper(*a, _o=original_agg, **kw):
                counts["n"] += 1
                return _o(*a, **kw)
            collection.aggregate = agg_wrapper
        return db, counts

    def test_active_users_is_one_query_regardless_of_session_count(self):
        """Flat in the data, not one query per user — the shape that looks fine
        in development forever and then does not."""
        now = datetime.now(timezone.utc)
        for population in (10, 500):
            db, counts = self._counting_db(sessions=[
                {"session_id": f"s{i}", "user_id": f"u{i % 50}",
                 "last_used_at": _iso(now)} for i in range(population)
            ])
            _run(sources.active_users(db, periods.resolve("today"), name="dau"))
            assert counts["n"] == 1, f"{population} sessions cost {counts['n']} queries"

    def test_signup_growth_is_exactly_two_counts(self):
        """One for the window, one for its base. Not a scan of the collection."""
        now = datetime.now(timezone.utc)
        db, counts = self._counting_db(users=[
            {"email": f"u{i}@x.com", "created_at": _iso(now - timedelta(days=i))}
            for i in range(200)
        ])
        _run(sources.signup_growth(db, periods.resolve("30d"), name="growth_rate"))
        assert counts["n"] == 2

    def test_unavailable_revenue_issues_no_query_at_all(self):
        """The gate short-circuits before touching the database. An admin
        dashboard load must not scan a collection to conclude it has no data."""
        db, counts = self._counting_db(payments=[])
        _run(sources.revenue(db, periods.resolve("today"), name="revenue_today"))
        _run(sources.subscription_revenue(db, name="mrr"))
        _run(sources.payment_state_count(db, sources.FAILED_STATUSES, name="failed"))
        assert counts["n"] == 0

    def test_the_dau_query_shape_matches_the_index(self):
        """`{last_used_at, user_id}` is created at startup (server.py). The
        query must lead with `last_used_at` for that index to serve it."""
        window = periods.resolve("today")
        assert list(window.filter_for("last_used_at")) == ["last_used_at"]

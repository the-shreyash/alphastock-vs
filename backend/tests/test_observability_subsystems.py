"""PH3.7 — subsystem observability: classification, instrumentation, failure signals.

Hermetic. No network, no Mongo, no Redis — every dependency failure in here is
injected, which is the point: the value of this suite is that it exercises the
*failure* paths, and those are precisely the paths a healthy test environment
never reaches on its own.

WHAT THIS SUITE IS FOR, BEYOND COVERAGE
---------------------------------------
Three classes of regression, each of which has a history of shipping silently:

1. **A metric label becomes unbounded.** Cardinality bugs do not fail anything
   locally; they take out the monitoring backend weeks later, in production,
   under load. `TestCardinality` asserts the bounds directly.
2. **Instrumentation changes control flow.** A tracker that swallows an
   exception, or that turns a handled failure into an unhandled one, converts a
   visible incident into a wrong answer. Every tracker test asserts on
   propagation as well as on the counter.
3. **A secret reaches a log line or a metric label.** `TestRedaction` asserts
   the negative — that specific sensitive values are absent from the recorded
   output — because "we were careful" is not a property a test can check and
   "this string does not appear" is.
"""
import asyncio
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from observability import errors, health, instruments, metrics, mongo_monitor
from observability import routes as obs_routes
from observability.middleware import apply_observability


@pytest.fixture(autouse=True)
def clean_metrics():
    """Reset every piece of process-global telemetry state around each test.

    The health registry is included because two tests here register checks;
    leaving one behind would make an unrelated suite's readiness assertion fail
    depending on file ordering, which is the specific kind of failure that
    teaches people to distrust a suite.
    """
    from services import scheduler as _scheduler

    metrics.reset_for_tests()
    mongo_monitor.reset_for_tests()
    _scheduler._job_started_at.clear()
    yield
    metrics.reset_for_tests()
    mongo_monitor.reset_for_tests()
    _scheduler._job_started_at.clear()
    health.clear_checks()
    health.clear_cache()


def series(metric, labels):
    """The value of one labelled series, for readable assertions."""
    return metric.value(labels)


# --------------------------------------------------------------------------- #
# Error classification (Step 7)                                                 #
# --------------------------------------------------------------------------- #
class TestErrorClassification:
    def test_the_vocabulary_is_closed_and_small(self):
        # A classification scheme is only useful if it is exhaustive and
        # bounded; if this number starts drifting upward, the labels have
        # stopped being a taxonomy and started being free text.
        assert len(errors.ERROR_CLASSES) == 13
        assert all(isinstance(c, str) and c.islower() for c in errors.ERROR_CLASSES)

    def test_every_classification_is_in_the_vocabulary(self):
        samples = [
            ValueError("x"), RuntimeError("x"), KeyError("x"),
            TimeoutError("x"), asyncio.CancelledError(),
            errors.ConfigurationError("x"), json.JSONDecodeError("x", "y", 0),
        ]
        for exc in samples:
            assert errors.is_error_class(errors.classify_exception(exc))

    def test_pymongo_errors_classify_as_database(self):
        import pymongo.errors

        assert errors.classify_exception(pymongo.errors.PyMongoError("x")) == errors.DATABASE
        # The subsystem beats the failure mode: a server-selection timeout is a
        # MongoDB problem, and routing it to DATABASE is what puts the page in
        # front of the person who can act on it. See the module docstring.
        assert errors.classify_exception(
            pymongo.errors.ServerSelectionTimeoutError("x")
        ) == errors.DATABASE

    def test_a_mongo_timeout_is_still_recognisable_as_a_timeout(self):
        import pymongo.errors

        # The escape hatch for the rule above: classification routes the alert,
        # `is_timeout` decides whether a retry makes sense. Both must work on
        # the same exception.
        assert errors.is_timeout(pymongo.errors.ServerSelectionTimeoutError("x"))

    def test_httpx_errors_classify_as_external_provider(self):
        import httpx

        assert errors.classify_exception(httpx.ConnectError("x")) == errors.EXTERNAL_PROVIDER
        assert errors.classify_exception(httpx.ReadTimeout("x")) == errors.EXTERNAL_PROVIDER

    def test_a_bare_timeout_with_no_subsystem_is_a_timeout(self):
        assert errors.classify_exception(asyncio.TimeoutError()) == errors.TIMEOUT

    def test_cancellation_is_classified_and_never_counted(self):
        # CancelledError inherits from BaseException, so a `except Exception`
        # never sees it — and a shutdown that cancels twelve in-flight
        # operations must not register as twelve failures.
        assert errors.classify_exception(asyncio.CancelledError()) == errors.CANCELLED
        instruments.record_exception("database", asyncio.CancelledError())
        assert series(metrics.subsystem_errors_total, ("database", errors.CANCELLED)) == 0
        assert metrics.subsystem_errors_total.collect() == []

    def test_an_unknown_exception_falls_back_to_internal(self):
        class NeverSeenBefore(Exception):
            pass

        assert errors.classify_exception(NeverSeenBefore()) == errors.INTERNAL

    def test_the_classifier_never_raises(self):
        class Hostile(Exception):
            @property
            def __class__(self):  # pragma: no cover - deliberately pathological
                raise RuntimeError("no introspection for you")

        # A classifier called from an `except` block that can itself throw would
        # turn a handled error into an unhandled one. It must always answer.
        assert errors.is_error_class(errors.classify_exception(Hostile()))

    @pytest.mark.parametrize(
        "status,expected",
        [
            (200, None), (302, None),
            (400, errors.VALIDATION), (401, errors.AUTHENTICATION),
            (403, errors.AUTHORIZATION), (408, errors.TIMEOUT),
            (429, errors.RATE_LIMIT), (500, errors.INTERNAL),
            (503, errors.UNAVAILABLE), (504, errors.TIMEOUT),
        ],
    )
    def test_http_status_classification(self, status, expected):
        assert errors.classify_status(status) == expected


# --------------------------------------------------------------------------- #
# The keystone metric                                                           #
# --------------------------------------------------------------------------- #
class TestSubsystemErrors:
    def test_a_failure_lands_on_the_keystone_series(self):
        instruments.record_exception("database", RuntimeError("boom"))
        assert series(metrics.subsystem_errors_total, ("database", errors.INTERNAL)) == 1

    def test_an_unregistered_subsystem_is_refused_not_recorded_verbatim(self, caplog):
        # The whole defence against an unbounded `subsystem` label. A typo at a
        # call site must produce a loud log line and one shared bucket, never a
        # new series.
        with caplog.at_level(logging.ERROR):
            instruments.record_error("marketdata", errors.INTERNAL)  # note: no underscore
        assert series(metrics.subsystem_errors_total, ("marketdata", errors.INTERNAL)) == 0
        assert series(metrics.subsystem_errors_total, (instruments.UNKNOWN, errors.INTERNAL)) == 1
        assert any("unregistered subsystem" in r.message for r in caplog.records)

    def test_an_unregistered_error_class_folds_into_internal(self):
        instruments.record_error("database", "definitely_not_a_class")
        assert series(metrics.subsystem_errors_total, ("database", errors.INTERNAL)) == 1


# --------------------------------------------------------------------------- #
# Authentication (Step 2B)                                                      #
# --------------------------------------------------------------------------- #
class TestAuthMetrics:
    def test_audit_events_are_counted_through_the_audit_logger(self):
        from security import audit

        asyncio.run(audit.log_event(audit.LOGIN_FAILURE, email="a@b.test"))
        assert series(metrics.auth_events_total, (audit.LOGIN_FAILURE, "failure")) == 1
        # A failure also reaches the keystone series, so "which subsystem is
        # failing?" is answerable without knowing the auth event vocabulary.
        assert series(metrics.subsystem_errors_total, ("auth", errors.AUTHENTICATION)) == 1

    def test_a_success_is_counted_but_is_not_an_error(self):
        from security import audit

        asyncio.run(audit.log_event(audit.LOGIN_SUCCESS, email="a@b.test"))
        assert series(metrics.auth_events_total, (audit.LOGIN_SUCCESS, "success")) == 1
        assert series(metrics.subsystem_errors_total, ("auth", errors.AUTHENTICATION)) == 0

    def test_an_unregistered_event_name_cannot_create_a_series(self):
        from security import audit

        # The event name is a metric label and callers pass it as a string, so
        # an unregistered value is the cardinality risk. It must collapse.
        asyncio.run(audit.log_event("attacker_supplied_" + "x" * 50))
        assert series(metrics.auth_events_total, (audit._UNREGISTERED_EVENT_LABEL, "info")) == 1
        assert len(metrics.auth_events_total.collect()) == 1


# --------------------------------------------------------------------------- #
# MongoDB (Step 2C)                                                             #
# --------------------------------------------------------------------------- #
class _Event:
    """A stand-in for a pymongo monitoring event (the listeners only getattr)."""

    def __init__(self, command_name="find", duration_micros=1500, failure=None):
        self.command_name = command_name
        self.duration_micros = duration_micros
        self.failure = failure


class TestMongoMetrics:
    def test_a_successful_command_records_latency_and_an_ok_outcome(self):
        mongo_monitor.CommandMetricsListener().succeeded(_Event("find", 2500))
        assert series(metrics.mongodb_commands_total, ("find", "ok")) == 1
        assert metrics.mongodb_command_duration_seconds.snapshot(("find",))["count"] == 1
        assert metrics.mongodb_command_duration_seconds.snapshot(("find",))["sum"] == pytest.approx(0.0025)

    def test_a_failed_command_is_classified_as_a_database_failure(self):
        mongo_monitor.CommandMetricsListener().failed(
            _Event("update", 900, failure={"code": 11000, "errmsg": "dup key"})
        )
        assert series(metrics.mongodb_commands_total, ("update", "error")) == 1
        assert series(metrics.mongodb_command_errors_total, ("update", "duplicate_key")) == 1
        assert series(metrics.subsystem_errors_total, ("database", errors.DATABASE)) == 1

    def test_an_unmapped_error_code_becomes_a_bounded_label(self):
        mongo_monitor.CommandMetricsListener().failed(
            _Event("find", 100, failure={"code": 31337})
        )
        assert series(metrics.mongodb_command_errors_total, ("find", "code_31337")) == 1

    def test_a_server_error_message_never_becomes_a_label(self):
        # `errmsg` routinely embeds the failing query, and on a connection fault
        # it embeds the credentialed connection URI. Only the code is used.
        secret = "mongodb://admin:hunter2@db:27017"
        mongo_monitor.CommandMetricsListener().failed(
            _Event("find", 100, failure={"code": 6, "errmsg": secret})
        )
        rendered = metrics.registry.render_prometheus()
        assert "hunter2" not in rendered
        assert secret not in rendered
        assert 'reason="host_unreachable"' in rendered

    def test_the_command_label_space_is_capped(self):
        listener = mongo_monitor.CommandMetricsListener()
        for i in range(mongo_monitor._MAX_COMMAND_NAMES + 25):
            listener.succeeded(_Event(f"cmd{i}", 100))
        distinct = {labels[0] for _, labels, _ in metrics.mongodb_commands_total.collect()}
        assert "other" in distinct
        assert len(distinct) <= mongo_monitor._MAX_COMMAND_NAMES + 1

    def test_a_listener_never_raises_on_a_malformed_event(self):
        # PyMongo invokes listeners on the caller's stack: an exception here
        # would turn a successful query into a failed one.
        class Broken:
            @property
            def command_name(self):
                raise RuntimeError("nope")

        mongo_monitor.CommandMetricsListener().succeeded(Broken())
        mongo_monitor.CommandMetricsListener().failed(Broken())

    def test_pool_occupancy_tracks_checkouts(self):
        listener = mongo_monitor.PoolMetricsListener()

        class Options:
            max_pool_size = 100

        class Created:
            options = Options()

        listener.pool_created(Created())
        listener.connection_checked_out(object())
        listener.connection_checked_out(object())
        assert series(metrics.mongodb_pool_connections, ("checked_out",)) == 2
        assert series(metrics.mongodb_pool_connections, ("max",)) == 100
        listener.connection_checked_in(object())
        assert series(metrics.mongodb_pool_connections, ("checked_out",)) == 1

    def test_pool_occupancy_never_goes_negative(self):
        # A gauge that reads -3 after one missed event is a gauge nobody trusts
        # again, and pool occupancy is the metric you consult during exactly the
        # kind of failover where events get lost.
        listener = mongo_monitor.PoolMetricsListener()
        listener.connection_checked_in(object())
        listener.connection_checked_in(object())
        assert series(metrics.mongodb_pool_connections, ("checked_out",)) == 0

    def test_a_cleared_pool_resets_occupancy(self):
        listener = mongo_monitor.PoolMetricsListener()
        listener.connection_checked_out(object())
        listener.connection_checked_out(object())
        # A failover discards every connection; the matching check-ins never
        # arrive, so decrementing one at a time would strand the gauge high
        # forever.
        listener.pool_cleared(object())
        assert series(metrics.mongodb_pool_connections, ("checked_out",)) == 0

    def test_the_listeners_satisfy_pymongos_type_check(self):
        # pymongo validates listeners with isinstance, not duck typing: a
        # duck-typed listener is rejected at client construction with a
        # TypeError, taking the whole application down at import. This asserts
        # the thing that actually broke during development.
        from pymongo.monitoring import CommandListener, ConnectionPoolListener

        registered = mongo_monitor.listeners()
        assert any(isinstance(x, CommandListener) for x in registered)
        assert any(isinstance(x, ConnectionPoolListener) for x in registered)

    def test_registration_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("MONGO_COMMAND_METRICS", "0")
        assert mongo_monitor.listeners() == []


# --------------------------------------------------------------------------- #
# WebSocket (Step 2E)                                                           #
# --------------------------------------------------------------------------- #
class TestWebSocketMetrics:
    def test_connections_and_disconnects_are_counted_separately_by_reason(self):
        instruments.record_ws_connection("accepted")
        instruments.record_ws_disconnect("client")
        instruments.record_ws_disconnect("error")
        assert series(metrics.websocket_connections_total, ("accepted",)) == 1
        assert series(metrics.websocket_disconnects_total, ("client",)) == 1
        assert series(metrics.websocket_disconnects_total, ("error",)) == 1

    def test_a_fanout_costs_one_increment_regardless_of_audience(self):
        instruments.record_ws_fanout("broadcast", failures=0)
        assert series(metrics.websocket_broadcasts_total, ("broadcast",)) == 1
        assert series(metrics.websocket_send_failures_total, ("broadcast",)) == 0

    def test_fanout_failures_are_added_in_one_sized_increment(self):
        instruments.record_ws_fanout("channel", failures=17)
        assert series(metrics.websocket_send_failures_total, ("channel",)) == 17
        # Each failed send is a dead socket, so the disconnect ledger has to
        # balance — otherwise connections and disconnects drift apart and
        # neither number means anything.
        assert series(metrics.websocket_disconnects_total, ("reaped",)) == 17
        assert series(metrics.subsystem_errors_total, ("websocket", errors.INTERNAL)) == 1

    def test_the_real_connection_manager_records_a_clean_close(self):
        import server

        manager = server.ConnectionManager()
        manager.disconnect(object(), "user-1")
        assert series(metrics.websocket_disconnects_total, ("client",)) == 1

    def test_the_real_connection_manager_distinguishes_an_abnormal_close(self):
        import server

        manager = server.ConnectionManager()
        manager.disconnect(object(), "user-1", reason="error")
        assert series(metrics.websocket_disconnects_total, ("error",)) == 1
        assert series(metrics.websocket_disconnects_total, ("client",)) == 0

    def test_a_broadcast_to_a_dead_socket_is_counted(self):
        import server

        class DeadSocket:
            async def send_text(self, payload):
                raise ConnectionResetError("gone")

        manager = server.ConnectionManager()
        manager.active.add(DeadSocket())
        asyncio.run(manager.broadcast({"type": "test"}))
        assert series(metrics.websocket_broadcasts_total, ("broadcast",)) == 1
        assert series(metrics.websocket_send_failures_total, ("broadcast",)) == 1
        # And the socket is still reaped: instrumentation must not change what
        # the method does.
        assert manager.active == set()


# --------------------------------------------------------------------------- #
# Background tasks (Step 2F)                                                    #
# --------------------------------------------------------------------------- #
class TestBackgroundTaskMetrics:
    def test_a_task_that_completes_records_a_start_and_a_completion(self):
        from infrastructure import tasks

        async def scenario():
            async def work():
                return None

            tasks.registry.spawn("unit-test-loop", work())
            await asyncio.sleep(0.02)

        asyncio.run(scenario())
        assert series(metrics.background_task_starts_total, ("unit-test-loop",)) == 1
        assert series(
            metrics.background_task_terminations_total, ("unit-test-loop", "completed")
        ) == 1
        assert metrics.background_task_duration_seconds.snapshot(("unit-test-loop",))["count"] == 1

    def test_a_crashed_task_is_recorded_as_failed_and_as_a_subsystem_error(self):
        from infrastructure import tasks

        async def scenario():
            async def work():
                raise RuntimeError("loop structure failed")

            tasks.registry.spawn("crashing-loop", work())
            await asyncio.sleep(0.02)

        asyncio.run(scenario())
        assert series(metrics.background_task_terminations_total, ("crashing-loop", "failed")) == 1
        assert series(metrics.subsystem_errors_total, ("background_task", errors.INTERNAL)) == 1

    def test_a_cancelled_task_is_not_an_error(self):
        from infrastructure import tasks

        async def scenario():
            async def work():
                await asyncio.sleep(30)

            tasks.registry.spawn("cancelled-loop", work())
            await asyncio.sleep(0.01)
            await tasks.registry.cancel_all()
            await asyncio.sleep(0.01)

        asyncio.run(scenario())
        assert series(metrics.background_task_terminations_total, ("cancelled-loop", "cancelled")) == 1
        # A clean shutdown cancels every loop. If that counted as a failure,
        # every deploy would look like an incident.
        assert series(metrics.subsystem_errors_total, ("background_task", errors.INTERNAL)) == 0

    def test_start_times_are_not_retained_after_a_task_ends(self):
        # This module exists to fix a leak; its own bookkeeping must not be one.
        from infrastructure import tasks

        async def scenario():
            for i in range(5):
                async def work():
                    return None

                tasks.registry.spawn(f"transient-{i}", work())
            await asyncio.sleep(0.05)

        asyncio.run(scenario())
        assert tasks.registry._started_at == {}


# --------------------------------------------------------------------------- #
# Scheduler (Step 2F — the cron half)                                           #
# --------------------------------------------------------------------------- #
class _JobEvent:
    """A stand-in for an APScheduler job event (the listener only getattrs)."""

    def __init__(self, code, job_id="trade_monitor", exception=None):
        self.code = code
        self.job_id = job_id
        self.exception = exception


class TestSchedulerMetrics:
    def test_a_successful_run_records_an_execution_and_a_duration(self):
        from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_SUBMITTED
        from services import scheduler

        scheduler._on_job_event(_JobEvent(EVENT_JOB_SUBMITTED))
        scheduler._on_job_event(_JobEvent(EVENT_JOB_EXECUTED))
        assert series(metrics.scheduler_job_runs_total, ("trade_monitor", "executed")) == 1
        assert metrics.scheduler_job_duration_seconds.snapshot(("trade_monitor",))["count"] == 1

    def test_a_raising_job_is_recorded_as_an_error(self):
        from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_SUBMITTED
        from services import scheduler

        scheduler._on_job_event(_JobEvent(EVENT_JOB_SUBMITTED, "eod_report"))
        scheduler._on_job_event(
            _JobEvent(EVENT_JOB_ERROR, "eod_report", exception=RuntimeError("boom"))
        )
        assert series(metrics.scheduler_job_runs_total, ("eod_report", "error")) == 1
        assert series(metrics.subsystem_errors_total, ("scheduler", errors.INTERNAL)) == 1

    def test_a_missed_run_is_recorded_with_no_duration(self):
        """The failure mode unique to cron: the job did not run at all.

        Nothing inside a job body can report this, which is why the listener
        exists. Recording a zero duration would drag the job's latency
        distribution toward zero at exactly the moment it is being skipped for
        taking too long.
        """
        from apscheduler.events import EVENT_JOB_MISSED
        from services import scheduler

        scheduler._on_job_event(_JobEvent(EVENT_JOB_MISSED, "market_scanner"))
        assert series(metrics.scheduler_job_runs_total, ("market_scanner", "missed")) == 1
        assert metrics.scheduler_job_duration_seconds.snapshot(("market_scanner",))["count"] == 0
        assert series(metrics.subsystem_errors_total, ("scheduler", errors.INTERNAL)) == 1

    def test_the_pending_map_does_not_leak_on_the_error_path(self):
        # The naive implementation pops only on success, so every failing run
        # strands an entry — a leak in the module written to observe leaks.
        from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_SUBMITTED
        from services import scheduler

        for i in range(20):
            scheduler._on_job_event(_JobEvent(EVENT_JOB_SUBMITTED, f"job-{i}"))
            scheduler._on_job_event(_JobEvent(EVENT_JOB_ERROR, f"job-{i}"))
        assert scheduler._job_started_at == {}

    def test_the_listener_never_raises(self):
        # APScheduler dispatches listeners inline; an exception here propagates
        # into the scheduler's own loop.
        from services import scheduler

        class Hostile:
            @property
            def job_id(self):
                raise RuntimeError("nope")

        scheduler._on_job_event(Hostile())
        scheduler._on_job_event(None)


# --------------------------------------------------------------------------- #
# External providers (Step 2G)                                                  #
# --------------------------------------------------------------------------- #
class TestProviderMetrics:
    def test_a_successful_call_records_ok_and_latency(self):
        with instruments.track_provider("market_data", "get_quote"):
            pass
        assert series(metrics.provider_requests_total, ("market_data", "get_quote", "ok")) == 1
        assert metrics.provider_request_duration_seconds.snapshot(
            ("market_data", "get_quote")
        )["count"] == 1

    def test_an_empty_response_is_neither_ok_nor_an_error(self):
        # The market-data failure a status-code check cannot see: 200 with no
        # rows, every panel green, stale prices on screen.
        with instruments.track_provider("market_data", "get_quote") as call:
            call.empty()
        assert series(metrics.provider_requests_total, ("market_data", "get_quote", "empty")) == 1
        assert series(metrics.provider_requests_total, ("market_data", "get_quote", "ok")) == 0
        assert metrics.provider_errors_total.collect() == []

    def test_an_exception_is_recorded_and_re_raised_untouched(self):
        import httpx

        original = httpx.ConnectError("refused")
        with pytest.raises(httpx.ConnectError) as caught:
            with instruments.track_provider("market_data", "get_quote"):
                raise original
        # Identity, not just type: a tracker that wraps or replaces the
        # exception destroys the caller's ability to handle it.
        assert caught.value is original
        assert series(metrics.provider_requests_total, ("market_data", "get_quote", "error")) == 1
        assert series(metrics.provider_errors_total, ("market_data", errors.EXTERNAL_PROVIDER)) == 1
        assert series(metrics.subsystem_errors_total, ("market_data", errors.EXTERNAL_PROVIDER)) == 1

    def test_cancellation_is_neither_a_success_nor_a_failure(self):
        with pytest.raises(asyncio.CancelledError):
            with instruments.track_provider("news", "get_news"):
                raise asyncio.CancelledError()
        assert metrics.provider_requests_total.collect() == []
        assert metrics.provider_errors_total.collect() == []
        # The time was really spent, so latency is still observed.
        assert metrics.provider_request_duration_seconds.snapshot(("news", "get_news"))["count"] == 1

    def test_a_provider_maps_to_its_own_subsystem(self):
        with pytest.raises(RuntimeError):
            with instruments.track_provider("news", "get_news"):
                raise RuntimeError("x")
        assert series(metrics.subsystem_errors_total, ("news", errors.INTERNAL)) == 1
        assert series(metrics.subsystem_errors_total, ("market_data", errors.INTERNAL)) == 0

    def test_an_unregistered_provider_cannot_create_a_series(self, caplog):
        with caplog.at_level(logging.ERROR):
            with instruments.track_provider("some_new_vendor", "fetch"):
                pass
        labels = {lbl[0] for _, lbl, _ in metrics.provider_requests_total.collect()}
        assert labels == {instruments.UNKNOWN}


# --------------------------------------------------------------------------- #
# AI providers (Step 2G / Step 6)                                               #
# --------------------------------------------------------------------------- #
class TestAIMetrics:
    def test_a_successful_call_records_ok_and_latency(self):
        with instruments.track_ai("claude"):
            pass
        assert series(metrics.ai_requests_total, ("claude", "ok")) == 1
        assert metrics.ai_request_duration_seconds.snapshot(("claude",))["count"] == 1

    def test_a_returned_error_response_is_recorded_as_a_failure(self):
        # THE case that matters. Every AI provider in this codebase catches
        # broadly and returns an AIResponse carrying an error string rather than
        # raising, so without an explicit report a total provider outage would
        # record as 100% success.
        with instruments.track_ai("claude") as call:
            call.failed("rate limit exceeded, please retry")
        assert series(metrics.ai_requests_total, ("claude", "error")) == 1
        assert series(metrics.ai_request_errors_total, ("claude", errors.RATE_LIMIT)) == 1

    def test_an_error_string_is_classified_but_never_recorded(self):
        leaky = "401 unauthorized: key sk-ant-api03-SUPERSECRET is invalid"
        with instruments.track_ai("claude") as call:
            call.failed(leaky)
        rendered = metrics.registry.render_prometheus()
        assert "SUPERSECRET" not in rendered
        assert "sk-ant" not in rendered
        # Classified as configuration: a bad key is a deploy fault, not an
        # outage, and the two need different responses.
        assert series(metrics.ai_request_errors_total, ("claude", errors.CONFIGURATION)) == 1

    def test_unconfigured_is_counted_but_records_no_latency(self):
        with instruments.track_ai("gemini") as call:
            call.unconfigured()
        assert series(metrics.ai_requests_total, ("gemini", "unconfigured")) == 1
        # No call was made, so a ~0s observation would drag the p50 of a
        # partially-configured deployment toward zero and hide real latency.
        assert metrics.ai_request_duration_seconds.snapshot(("gemini",))["count"] == 0

    def test_the_simulated_provider_reports_itself(self):
        from services.ai_provider import SimulatedProvider

        response = asyncio.run(SimulatedProvider().complete([]))
        assert response.provider == "simulated"
        # The single most important AI counter: users are receiving canned
        # answers, the request succeeded, and nothing else in the system says so.
        assert series(metrics.ai_requests_total, ("simulated", "unconfigured")) == 1

    def test_an_unconfigured_claude_provider_records_and_does_not_call_out(self):
        from services.claude_provider import ClaudeProvider

        response = asyncio.run(ClaudeProvider().complete([], model="m"))
        assert response.error == "missing_api_key"
        assert series(metrics.ai_requests_total, ("claude", "unconfigured")) == 1

    def test_no_model_name_reaches_a_metric_label(self):
        # Model ids come from configuration, so they are operator-supplied and
        # therefore unbounded in principle. They belong on the log line.
        with instruments.track_ai("claude"):
            pass
        rendered = metrics.registry.render_prometheus()
        assert "model=" not in rendered


# --------------------------------------------------------------------------- #
# Event bus                                                                     #
# --------------------------------------------------------------------------- #
class TestEventBusMetrics:
    def test_a_publish_with_no_subscriber_is_still_counted(self):
        from services.market_engine.event_bus import EventBus

        asyncio.run(EventBus().publish("price.updated", {"symbol": "X"}))
        assert series(metrics.event_bus_events_total, ("price.updated",)) == 1

    def test_a_failing_handler_is_counted_as_a_lost_domain_action(self):
        from services.market_engine.event_bus import EventBus

        async def broken(event):
            raise RuntimeError("handler bug")

        bus = EventBus()
        bus.subscribe("trade.closed", broken)
        asyncio.run(bus.publish("trade.closed", {}))
        assert series(metrics.event_bus_events_total, ("trade.closed",)) == 1
        assert series(metrics.event_bus_handler_failures_total, ("trade.closed",)) == 1
        assert series(metrics.subsystem_errors_total, ("event_bus", errors.INTERNAL)) == 1

    def test_no_event_payload_reaches_a_label(self):
        from services.market_engine.event_bus import EventBus

        asyncio.run(EventBus().publish("price.updated", {"symbol": "RELIANCE", "user_id": "u-42"}))
        rendered = metrics.registry.render_prometheus()
        assert "RELIANCE" not in rendered
        assert "u-42" not in rendered


# --------------------------------------------------------------------------- #
# Readiness: configuration (Step 5)                                             #
# --------------------------------------------------------------------------- #
class _Report:
    def __init__(self, errors_):
        self.errors = errors_


class TestConfigurationReadiness:
    def test_a_valid_configuration_passes(self):
        probe = health.make_config_probe(lambda: _Report([]))
        assert asyncio.run(probe()) is True

    def test_an_invalid_configuration_fails_readiness(self):
        probe = health.make_config_probe(lambda: _Report(["MONGO_URL missing"]))
        with pytest.raises(RuntimeError):
            asyncio.run(probe())

    def test_the_failure_detail_names_no_secret(self):
        probe = health.make_config_probe(
            lambda: _Report(["JWT_SECRET is weak", "STRIPE_KEY missing"])
        )
        try:
            asyncio.run(probe())
        except RuntimeError as exc:
            # A count, never the names: an unauthenticated caller learning which
            # secret a deployment is missing is a reconnaissance gift, and the
            # names are already in the boot log where they belong.
            assert "JWT_SECRET" not in str(exc)
            assert "STRIPE_KEY" not in str(exc)
            assert "2" in str(exc)
        else:  # pragma: no cover
            pytest.fail("probe should have raised")

    def test_a_missing_report_is_a_skip_not_a_failure(self):
        # A monitoring gap must not take a healthy instance out of rotation.
        probe = health.make_config_probe(lambda: None)
        assert asyncio.run(probe()) is None

    def test_the_real_application_registers_a_critical_configuration_check(self):
        """Assert the wiring, not just the helper.

        Registration happens at `server` import time. Other suites call
        `health.clear_checks()` in their own fixtures, so asserting on the live
        registry here would be order-dependent — the registration is therefore
        re-run against the real report and the real registry inside the test.
        """
        import server

        health.register_check(
            "configuration",
            health.make_config_probe(lambda: server._config_report),
            critical=True,
        )
        assert "configuration" in health.registered_checks()

        results = asyncio.run(health.run_checks(use_cache=False))
        config_result = next(r for r in results if r.name == "configuration")
        assert config_result.critical is True
        # The test environment boots, so its configuration validates; a failing
        # verdict here would mean the boot should not have succeeded.
        assert config_result.healthy is True
        assert health.is_ready(results) is True

    def test_an_invalid_configuration_removes_the_instance_from_rotation(self):
        health.clear_checks()
        health.clear_cache()
        health.register_check(
            "configuration",
            health.make_config_probe(lambda: _Report(["MONGO_URL missing"])),
            critical=True,
        )
        results = asyncio.run(health.run_checks(use_cache=False))
        # Critical means exactly this: readiness goes false and the load
        # balancer stops sending traffic here.
        assert health.is_ready(results) is False


# --------------------------------------------------------------------------- #
# Frontend error ingest (Step 9)                                                #
# --------------------------------------------------------------------------- #
@pytest.fixture
def ingest_client():
    app = FastAPI()
    app.include_router(obs_routes.router)
    apply_observability(app)
    return TestClient(app)


class TestClientErrorIngest:
    def test_a_valid_report_is_counted(self, ingest_client):
        response = ingest_client.post(
            "/api/observability/client-errors",
            json={"kind": "render", "name": "TypeError", "message": "x is undefined"},
        )
        assert response.status_code == 204
        assert series(metrics.frontend_errors_total, ("render",)) == 1

    def test_an_unknown_kind_is_refused_rather_than_recorded(self, ingest_client):
        # `kind` is a metric label arriving in an anonymous request body — the
        # textbook cardinality attack. It must be validated against a closed set.
        response = ingest_client.post(
            "/api/observability/client-errors",
            json={"kind": "../../etc/passwd", "message": "x"},
        )
        assert response.status_code == 204
        assert metrics.frontend_errors_total.collect() == []
        assert series(metrics.frontend_reports_rejected_total, ("unknown_kind",)) == 1

    def test_a_rejected_report_still_answers_204(self, ingest_client):
        # A browser that is already broken must not be handed an error response
        # to handle; that is how a reporting path becomes a retry loop.
        assert ingest_client.post(
            "/api/observability/client-errors", content=b"not json"
        ).status_code == 204
        assert series(metrics.frontend_reports_rejected_total, ("unparseable",)) == 1

    def test_free_text_fields_are_clipped(self, ingest_client, caplog):
        with caplog.at_level(logging.WARNING, logger="observability.routes"):
            ingest_client.post(
                "/api/observability/client-errors",
                json={"kind": "uncaught", "message": "A" * 5000, "stack": "B" * 50000},
            )
        record = next(r for r in caplog.records if getattr(r, "event", "") == "frontend_error")
        assert len(record.error_message) <= obs_routes._MAX_MESSAGE
        assert len(record.stack) <= obs_routes._MAX_STACK

    def test_newlines_are_stripped_so_a_report_cannot_forge_a_log_line(self, ingest_client, caplog):
        forged = 'x"\n2026-01-01 CRITICAL security breach detected'
        with caplog.at_level(logging.WARNING, logger="observability.routes"):
            ingest_client.post(
                "/api/observability/client-errors",
                json={"kind": "uncaught", "message": forged},
            )
        record = next(r for r in caplog.records if getattr(r, "event", "") == "frontend_error")
        assert "\n" not in record.error_message
        assert "\r" not in record.error_message

    def test_the_endpoint_is_exempt_from_csrf(self):
        # It must work when nothing else does — before login, during a chunk
        # load, and via sendBeacon, which cannot set a custom header at all.
        from security.csrf import _DEFAULT_EXEMPT_PATHS

        assert "/api/observability/client-errors" in _DEFAULT_EXEMPT_PATHS

    def test_the_endpoint_is_registered_on_the_real_application(self):
        import server

        paths = {getattr(r, "path", None) for r in server.app.routes}
        assert "/api/observability/client-errors" in paths


# --------------------------------------------------------------------------- #
# Cardinality (Step 6)                                                          #
# --------------------------------------------------------------------------- #
class TestCardinality:
    def test_no_new_metric_carries_an_unbounded_label_name(self):
        # A denylist rather than an allowlist: the failure mode is someone
        # ADDING a label, so the test has to fail on names nobody thought about
        # rather than pass on the ones we did.
        forbidden = {
            "user", "user_id", "email", "symbol", "trade_id", "session",
            "session_id", "ip", "token", "path", "url", "message", "error_message",
            "key", "model", "query",
        }
        offenders = {
            (m.name, label)
            for m in metrics.registry.metrics()
            for label in m.label_names
            if label in forbidden
        }
        assert offenders == set()

    def test_every_metric_stays_under_the_series_ceiling(self):
        # The backstop for a label the denylist above did not anticipate.
        for _ in range(metrics.MAX_SERIES_PER_METRIC + 50):
            instruments.record_error("database", errors.DATABASE)
        for i in range(metrics.MAX_SERIES_PER_METRIC + 50):
            metrics.provider_requests_total.inc(labels=("market_data", f"op{i}", "ok"))
        # MAX accepted series plus the single `<overflow>` bucket everything
        # past the ceiling folds into — the ceiling bounds *distinct accepted*
        # combinations, and the overflow series is what keeps the signal
        # degrading rather than disappearing.
        assert len(metrics.provider_requests_total.collect()) == metrics.MAX_SERIES_PER_METRIC + 1
        assert any(
            labels[1] == metrics.OVERFLOW_LABEL
            for _, labels, _ in metrics.provider_requests_total.collect()
        )
        # And the breach is itself observable, which is the alert.
        assert metrics.registry.dropped_series() > 0

    def test_the_exposition_document_is_well_formed_with_every_family_populated(self):
        instruments.record_error("database", errors.DATABASE)
        instruments.record_auth_event("login_failure", "failure")
        instruments.record_ws_fanout("broadcast", 1)
        instruments.record_task_start("loop")
        instruments.record_event_published("price.updated", 1)
        instruments.record_mongo_command("find", 0.01, ok=True)
        with instruments.track_provider("market_data", "get_quote"):
            pass
        with instruments.track_ai("claude"):
            pass

        rendered = metrics.registry.render_prometheus()
        assert rendered.endswith("\n")
        for line in rendered.splitlines():
            if line.startswith("#"):
                assert line.startswith("# HELP ") or line.startswith("# TYPE ")
            else:
                # `name{labels} value` — a malformed line makes a scraper reject
                # the WHOLE document, not just the offending series.
                assert " " in line
                assert float(line.rsplit(" ", 1)[1]) is not None


# --------------------------------------------------------------------------- #
# Redaction (Step 10)                                                           #
# --------------------------------------------------------------------------- #
class TestRedaction:
    #: Secret-shaped strings, one per class of thing this application actually
    #: handles. Reused across the sweeps below so a new leak path is caught by
    #: whichever sweep covers it rather than by whichever happened to use the
    #: right sample.
    SECRETS = [
        "sk-ant-api03-REALKEYMATERIAL",
        "eyJhbGciOiJIUzI1NiJ9.PAYLOAD.SIGNATURE",
        "hunter2",
        "user@example.com",
        "mongodb://admin:pw@host:27017/db",
        "5f8d0d55b54764421b7156c3",
    ]

    def test_no_free_text_path_lets_a_secret_reach_the_exposition_document(self):
        """Drive every instrument that accepts FREE TEXT with secret-shaped input.

        This is the real risk surface, and the distinction matters enough to
        state: the closed-vocabulary labels (`subsystem`, `provider`, `outcome`,
        `reason`, `kind`) are validated against frozen sets and cannot carry an
        arbitrary value at all. The dangerous paths are the ones that accept a
        string and have to *derive* a label from it — an AI provider's error
        message, an exception, a MongoDB failure document, a browser report.
        Each of those has a plausible implementation that would echo its input
        into a label, and each is swept here.

        Written as one test over all of them because the risk is not that a
        specific call site leaks — it is that a *new* one does, and a single
        sweep is the thing that keeps failing when someone adds an instrument
        with a careless label.
        """
        import httpx

        for secret in self.SECRETS:
            # An AI provider's returned error string.
            with instruments.track_ai("claude") as call:
                call.failed(f"request failed: {secret}")
            # An exception message from an outbound HTTP call.
            try:
                with instruments.track_provider("market_data", "get_quote"):
                    raise httpx.ConnectError(f"cannot reach {secret}")
            except httpx.ConnectError:
                pass
            # A MongoDB server failure document.
            mongo_monitor.CommandMetricsListener().failed(
                _Event("find", 100, failure={"code": 13, "errmsg": secret})
            )
            # A generic subsystem failure carrying the secret in its message.
            instruments.record_exception("database", RuntimeError(secret))

        rendered = metrics.registry.render_prometheus()
        for secret in self.SECRETS:
            assert secret not in rendered, f"{secret} leaked into the metrics document"

    def test_a_closed_vocabulary_label_cannot_carry_an_arbitrary_value(self):
        """The other half: prove the frozen sets actually refuse, not just document.

        A validation helper that logs and then records the value anyway would
        pass every test above while leaking on the first misuse.
        """
        for secret in self.SECRETS:
            instruments.record_error(secret, secret)
            instruments.record_ws_disconnect(secret)
            instruments.record_ws_connection(secret)
            instruments.record_ws_fanout(secret, failures=1)
            instruments.record_task_end("loop", secret)
            instruments.record_auth_event("login_failure", secret)

        rendered = metrics.registry.render_prometheus()
        for secret in self.SECRETS:
            assert secret not in rendered, f"{secret} was accepted as a closed-vocabulary label"
        assert instruments.UNKNOWN in rendered

    def test_ai_error_text_never_reaches_the_document(self):
        leaks = [
            "invalid api key sk-proj-ABCDEF",
            "Bearer eyJhbGciOiJIUzI1NiJ9.abc.def",
            "authentication failed for user@example.com",
        ]
        for text in leaks:
            with instruments.track_ai("gemini") as call:
                call.failed(text)
        rendered = metrics.registry.render_prometheus()
        for text in leaks:
            assert text not in rendered
        assert "sk-proj-ABCDEF" not in rendered
        assert "user@example.com" not in rendered

    def test_provider_exception_messages_never_reach_the_document(self):
        import httpx

        with pytest.raises(httpx.ConnectError):
            with instruments.track_provider("broker_zerodha", "place_order"):
                raise httpx.ConnectError("failed to connect to https://api.kite.trade?token=SECRET")
        rendered = metrics.registry.render_prometheus()
        assert "SECRET" not in rendered
        assert "kite.trade" not in rendered

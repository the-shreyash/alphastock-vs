"""PH2.5 — observability tests: health probes, metrics, structured logging, correlation.

Hermetic (no network, no Mongo, no Redis), in the established style: pure-unit
tests against the policy functions, plus wire-level tests that assert the real
HTTP representation through a `TestClient`.

The wire tests deliberately use a throwaway FastAPI app rather than the real
`server.app` wherever the assertion is about the *middleware contract* — it
keeps the test independent of the 40-router application and of whatever the
suite's other fixtures have done to `server.db`. Tests that must prove the real
application is wired correctly (endpoints registered, rate-limit exemptions,
middleware ordering) use `server.app` explicitly.
"""
import asyncio
import json
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from observability import context, health, metrics, runtime
from observability import logging as obs_logging
from observability import routes as obs_routes
from observability.middleware import (
    UNMATCHED_ROUTE,
    apply_observability,
    route_template,
)


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def clean_observability_state():
    """Reset every piece of process-global observability state around each test.

    Metrics, the health registry, the readiness cache and the lifecycle are all
    deliberately process-global (there is one process, and its telemetry is a
    genuine singleton). That makes them shared mutable state across a test
    session, which is exactly how order-dependent test failures are born — so
    each test gets a clean slate, and leaves one behind.
    """
    metrics.reset_for_tests()
    health.clear_checks()
    health.clear_cache()
    health.lifecycle.reset()
    yield
    metrics.reset_for_tests()
    health.clear_checks()
    health.clear_cache()
    health.lifecycle.reset()


@pytest.fixture
def instrumented_app():
    """A minimal app carrying the real middleware and the real operational routes."""
    app = FastAPI()

    @app.get("/echo/{item_id}")
    async def echo(item_id: str):
        return {"item_id": item_id}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("intentional failure")

    app.include_router(obs_routes.router)
    apply_observability(app)
    return app


@pytest.fixture
def dev_env(monkeypatch):
    """Force a non-production posture (the default for most assertions here)."""
    monkeypatch.setenv("APP_ENV", "development")


@pytest.fixture
def prod_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")


# --------------------------------------------------------------------------- #
# Request correlation                                                           #
# --------------------------------------------------------------------------- #
class TestRequestCorrelation:
    def test_generated_id_is_a_32_char_hex_string(self):
        rid = context.new_request_id()
        assert re.fullmatch(r"[0-9a-f]{32}", rid)

    def test_generated_ids_are_unique(self):
        assert len({context.new_request_id() for _ in range(1000)}) == 1000

    @pytest.mark.parametrize(
        "value",
        ["abc12345", "a" * 128, "trace-01_ab.cd:ef", "0123456789abcdef"],
    )
    def test_plausible_inbound_ids_are_accepted(self, value):
        assert context.is_valid_request_id(value)
        assert context.resolve_request_id(value) == value

    @pytest.mark.parametrize(
        "value,why",
        [
            (None, "absent"),
            ("", "empty"),
            ("short", "below the 8-char floor"),
            ("a" * 129, "above the 128-char ceiling"),
            ("has space", "whitespace"),
            ("inject\nlog line", "newline — the log-injection vector"),
            ('quote"break', "double quote — would break JSON encoding"),
            ("back\\slash", "backslash — would break JSON encoding"),
            ("\x00nullbyte", "control character"),
            ("../../etc/passwd", "path traversal characters"),
        ],
    )
    def test_implausible_inbound_ids_are_replaced_not_trusted(self, value, why):
        """An attacker-controlled header must never reach a log line verbatim."""
        assert not context.is_valid_request_id(value), why
        resolved = context.resolve_request_id(value)
        assert resolved != value
        assert re.fullmatch(r"[0-9a-f]{32}", resolved)

    def test_no_request_id_outside_a_request(self):
        assert context.current_request_id() == context.NO_REQUEST_ID
        assert context.current_request_id_or_none() is None

    def test_bind_and_reset_restore_the_previous_context(self):
        token = context.bind("abcdef0123456789", method="GET", path="/x")
        assert context.current_request_id() == "abcdef0123456789"
        assert context.current().method == "GET"
        context.reset(token)
        assert context.current_request_id() == context.NO_REQUEST_ID

    def test_context_is_isolated_between_concurrent_tasks(self):
        """The property that makes this safe under async interleaving.

        A module-level global or threading.local would leak one request's ID onto
        another's log lines the moment two requests interleaved on the loop.
        """
        observed = {}

        async def worker(name, rid):
            token = context.bind(rid)
            await asyncio.sleep(0)  # force a suspension point between the two
            observed[name] = context.current_request_id()
            context.reset(token)

        async def main():
            await asyncio.gather(
                worker("a", "aaaaaaaaaaaaaaaa"),
                worker("b", "bbbbbbbbbbbbbbbb"),
            )

        asyncio.run(main())
        assert observed == {"a": "aaaaaaaaaaaaaaaa", "b": "bbbbbbbbbbbbbbbb"}

    def test_response_carries_a_generated_request_id(self, instrumented_app):
        response = TestClient(instrumented_app).get("/echo/42")
        rid = response.headers.get("X-Request-ID")
        assert rid and re.fullmatch(r"[0-9a-f]{32}", rid)

    def test_valid_inbound_request_id_is_propagated(self, instrumented_app):
        client = TestClient(instrumented_app)
        response = client.get("/echo/42", headers={"X-Request-ID": "edge-generated-01"})
        assert response.headers["X-Request-ID"] == "edge-generated-01"

    def test_malformed_inbound_request_id_is_replaced(self, instrumented_app):
        client = TestClient(instrumented_app)
        response = client.get("/echo/42", headers={"X-Request-ID": "bad id"})
        assert response.headers["X-Request-ID"] != "bad id"
        assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])

    def test_each_request_gets_a_distinct_id(self, instrumented_app):
        client = TestClient(instrumented_app)
        first = client.get("/echo/1").headers["X-Request-ID"]
        second = client.get("/echo/2").headers["X-Request-ID"]
        assert first != second

    def test_request_id_reaches_a_handler_via_the_context(self, dev_env):
        """The end-to-end property: an untouched handler sees the request's ID."""
        seen = {}
        app = FastAPI()

        @app.get("/inside")
        async def inside():
            seen["rid"] = context.current_request_id()
            return {"ok": True}

        apply_observability(app)
        response = TestClient(app).get("/inside")
        assert seen["rid"] == response.headers["X-Request-ID"]
        assert seen["rid"] != context.NO_REQUEST_ID

    def test_error_responses_also_carry_the_id(self, instrumented_app):
        """A user only needs an ID when something went wrong — so it must be there."""
        response = TestClient(instrumented_app).get("/no-such-route")
        assert response.status_code == 404
        assert "X-Request-ID" in response.headers


# --------------------------------------------------------------------------- #
# Health endpoints                                                              #
# --------------------------------------------------------------------------- #
class TestLiveness:
    def test_liveness_is_200_and_dependency_free(self, instrumented_app):
        """Liveness must pass even with every dependency failing.

        This is THE liveness invariant: a DB-coupled liveness probe turns a
        database blip into a fleet-wide restart storm.
        """
        async def always_fails():
            raise RuntimeError("mongo is down")

        health.register_check("mongodb", always_fails, critical=True)

        response = TestClient(instrumented_app).get("/api/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_liveness_reports_uptime_and_lifecycle(self, instrumented_app):
        body = TestClient(instrumented_app).get("/api/health/live").json()
        assert body["lifecycle"] == health.STARTING
        assert body["uptime_seconds"] >= 0
        assert body["service"] == runtime.service_name()

    def test_probe_responses_are_never_cached(self, instrumented_app):
        """A cached health response is a lie in both directions."""
        for path in ("/api/health/live", "/api/health/ready", "/api/health/startup"):
            response = TestClient(instrumented_app).get(path)
            assert "no-store" in response.headers.get("Cache-Control", "")


class TestReadiness:
    def test_ready_when_checks_pass_and_startup_complete(self, instrumented_app):
        async def ok():
            return True

        health.register_check("mongodb", ok, critical=True)
        health.lifecycle.mark_started()

        response = TestClient(instrumented_app).get("/api/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["checks"][0]["name"] == "mongodb"
        assert body["checks"][0]["status"] == health.PASS

    def test_not_ready_when_a_critical_check_fails(self, instrumented_app):
        async def broken():
            raise RuntimeError("connection refused")

        health.register_check("mongodb", broken, critical=True)
        health.lifecycle.mark_started()

        response = TestClient(instrumented_app).get("/api/health/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["checks"][0]["status"] == health.FAIL

    def test_a_failing_NON_critical_check_still_reports_ready(self, instrumented_app):
        """Degraded-but-serving must be representable, not rounded up to "down"."""
        async def ok():
            return True

        async def broken():
            raise RuntimeError("redis unreachable")

        health.register_check("mongodb", ok, critical=True)
        health.register_check("redis", broken, critical=False)
        health.lifecycle.mark_started()

        response = TestClient(instrumented_app).get("/api/health/ready")
        assert response.status_code == 200
        by_name = {c["name"]: c for c in response.json()["checks"]}
        assert by_name["redis"]["status"] == health.FAIL
        assert by_name["redis"]["critical"] is False

    def test_unconfigured_dependency_is_skipped_not_failed(self, instrumented_app):
        """An unconfigured Redis is a valid deployment, not a fault."""
        async def not_configured():
            return None

        health.register_check("redis", not_configured, critical=True)
        health.lifecycle.mark_started()

        response = TestClient(instrumented_app).get("/api/health/ready")
        assert response.status_code == 200
        assert response.json()["checks"][0]["status"] == health.SKIP

    def test_not_ready_before_startup_completes(self, instrumented_app):
        async def ok():
            return True

        health.register_check("mongodb", ok, critical=True)
        # lifecycle deliberately left at STARTING
        response = TestClient(instrumented_app).get("/api/health/ready")
        assert response.status_code == 503
        assert response.json()["reason"] == "startup incomplete"

    def test_not_ready_while_draining(self, instrumented_app):
        """The property that makes a deploy quiet: fail readiness before teardown."""
        async def ok():
            return True

        health.register_check("mongodb", ok, critical=True)
        health.lifecycle.mark_started()
        health.lifecycle.mark_stopping()

        response = TestClient(instrumented_app).get("/api/health/ready")
        assert response.status_code == 503
        assert response.json()["reason"] == "shutting down"
        assert response.json()["lifecycle"] == health.STOPPING

    def test_a_hung_probe_times_out_rather_than_hanging_readiness(self):
        async def hangs():
            await asyncio.sleep(30)
            return True

        health.register_check("mongodb", hangs, critical=True, timeout=0.05)
        results = asyncio.run(health.run_checks(use_cache=False))
        assert results[0].status == health.FAIL
        assert "timeout" in results[0].detail

    def test_probes_run_in_parallel_not_serially(self):
        """Readiness latency must be max(probe), not sum(probe)."""
        async def slow():
            await asyncio.sleep(0.15)
            return True

        for name in ("a", "b", "c", "d"):
            health.register_check(name, slow, critical=True)

        async def timed():
            start = asyncio.get_event_loop().time()
            await health.run_checks(use_cache=False)
            return asyncio.get_event_loop().time() - start

        elapsed = asyncio.run(timed())
        # Serial would be ~0.6s; parallel ~0.15s. 0.4 separates them decisively
        # without being flaky on a loaded CI runner.
        assert elapsed < 0.4

    def test_results_are_cached_so_pollers_do_not_storm_the_database(self):
        calls = {"n": 0}

        async def counting():
            calls["n"] += 1
            return True

        health.register_check("mongodb", counting, critical=True)

        async def poll_repeatedly():
            for _ in range(5):
                await health.run_checks(use_cache=True)

        asyncio.run(poll_repeatedly())
        assert calls["n"] == 1

    def test_cache_can_be_bypassed(self):
        calls = {"n": 0}

        async def counting():
            calls["n"] += 1
            return True

        health.register_check("mongodb", counting, critical=True)

        async def poll_repeatedly():
            for _ in range(3):
                await health.run_checks(use_cache=False)

        asyncio.run(poll_repeatedly())
        assert calls["n"] == 3


class TestStartupProbe:
    def test_503_while_starting(self, instrumented_app):
        response = TestClient(instrumented_app).get("/api/health/startup")
        assert response.status_code == 503
        assert response.json()["status"] == "starting"

    def test_200_once_started(self, instrumented_app):
        health.lifecycle.mark_started()
        response = TestClient(instrumented_app).get("/api/health/startup")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "started"
        assert body["started_at"] is not None

    def test_startup_remains_satisfied_while_draining(self, instrumented_app):
        """Startup answers "did it boot?", not "is it serving?" — a draining
        instance has certainly booted, and conflating the two would make the
        orchestrator restart a container that is shutting down cleanly."""
        health.lifecycle.mark_started()
        health.lifecycle.mark_stopping()
        assert TestClient(instrumented_app).get("/api/health/startup").status_code == 200


class TestLifecycle:
    def test_transitions(self):
        assert health.lifecycle.state == health.STARTING
        assert not health.lifecycle.is_started
        assert not health.lifecycle.accepts_traffic

        health.lifecycle.mark_started()
        assert health.lifecycle.state == health.READY
        assert health.lifecycle.is_started
        assert health.lifecycle.accepts_traffic

        health.lifecycle.mark_stopping()
        assert health.lifecycle.state == health.STOPPING
        assert health.lifecycle.is_started
        assert not health.lifecycle.accepts_traffic

    def test_a_late_startup_callback_cannot_resurrect_a_draining_process(self):
        health.lifecycle.mark_stopping()
        health.lifecycle.mark_started()
        assert health.lifecycle.state == health.STOPPING


class TestProbeDetailSanitisation:
    def test_production_hides_the_exception_message(self, prod_env):
        """A pymongo error stringifies with the connection URI — password included."""
        async def leaky():
            raise RuntimeError(
                "connection refused to mongodb://admin:hunter2@prod-db.internal:27017"
            )

        health.register_check("mongodb", leaky, critical=True)
        results = asyncio.run(health.run_checks(use_cache=False))
        detail = results[0].detail
        assert detail == "RuntimeError"
        assert "hunter2" not in detail
        assert "prod-db.internal" not in detail

    def test_development_includes_the_message_for_debuggability(self, dev_env):
        async def broken():
            raise RuntimeError("connection refused to localhost:27017")

        health.register_check("mongodb", broken, critical=True)
        results = asyncio.run(health.run_checks(use_cache=False))
        assert "localhost:27017" in results[0].detail


# --------------------------------------------------------------------------- #
# Metrics                                                                       #
# --------------------------------------------------------------------------- #
class TestMetricPrimitives:
    def test_counter_accumulates(self):
        c = metrics.Counter("t_counter", "help", ("label",))
        c.inc(labels=("a",))
        c.inc(2, labels=("a",))
        c.inc(labels=("b",))
        assert c.value(("a",)) == 3
        assert c.value(("b",)) == 1

    def test_counter_rejects_a_negative_increment(self):
        c = metrics.Counter("t_counter_neg", "help")
        c.inc(5)
        c.inc(-3)
        assert c.value() == 5

    def test_gauge_goes_both_ways(self):
        g = metrics.Gauge("t_gauge", "help")
        g.inc(5)
        g.dec(2)
        assert g.value() == 3
        g.set(10)
        assert g.value() == 10

    def test_histogram_buckets_are_cumulative(self):
        h = metrics.Histogram("t_hist", "help", buckets=(0.1, 1.0, 10.0))
        for value in (0.05, 0.5, 5.0):
            h.observe(value)
        snap = h.snapshot()
        assert snap["buckets"][0.1] == 1     # 0.05
        assert snap["buckets"][1.0] == 2     # 0.05, 0.5
        assert snap["buckets"][10.0] == 3    # all three
        assert snap["count"] == 3
        assert snap["sum"] == pytest.approx(5.55)

    def test_histogram_bucket_boundary_is_inclusive(self):
        """"le" means less-than-or-EQUAL; an off-by-one here silently skews p99."""
        h = metrics.Histogram("t_hist_edge", "help", buckets=(0.1, 1.0))
        h.observe(0.1)
        assert h.snapshot()["buckets"][0.1] == 1

    def test_cardinality_ceiling_folds_new_series_into_overflow(self, monkeypatch):
        """The backstop against a mislabelling bug exhausting memory."""
        monkeypatch.setattr(metrics, "MAX_SERIES_PER_METRIC", 3)
        c = metrics.Counter("t_overflow", "help", ("id",))
        for i in range(10):
            c.inc(labels=(str(i),))
        assert c.value((metrics.OVERFLOW_LABEL,)) == 7
        assert c.dropped == 7

    def test_existing_series_keep_updating_past_the_ceiling(self, monkeypatch):
        """Refusing to update an already-tracked series would make graphs lie."""
        monkeypatch.setattr(metrics, "MAX_SERIES_PER_METRIC", 2)
        c = metrics.Counter("t_overflow_existing", "help", ("id",))
        c.inc(labels=("a",))
        c.inc(labels=("b",))
        c.inc(labels=("c",))  # overflow
        c.inc(labels=("a",))  # still tracked
        assert c.value(("a",)) == 2

    def test_a_label_arity_mismatch_does_not_raise(self):
        """A metrics bug must never be able to 500 a route."""
        c = metrics.Counter("t_arity", "help", ("a", "b"))
        c.inc(labels=("only-one",))  # wrong arity
        assert c.value((metrics.OVERFLOW_LABEL, metrics.OVERFLOW_LABEL)) == 1


class TestPrometheusExposition:
    def test_render_includes_help_type_and_samples(self):
        metrics.http_requests_total.inc(labels=("GET", "/api/x", "200"))
        text = metrics.registry.render_prometheus()
        assert "# HELP http_requests_total" in text
        assert "# TYPE http_requests_total counter" in text
        assert 'http_requests_total{method="GET",route="/api/x",status="200"} 1' in text

    def test_histogram_renders_bucket_sum_and_count_with_plus_inf(self):
        metrics.http_request_duration_seconds.observe(0.02, labels=("GET", "/api/x"))
        text = metrics.registry.render_prometheus()
        assert 'http_request_duration_seconds_bucket{method="GET",route="/api/x",le="+Inf"} 1' in text
        assert 'http_request_duration_seconds_count{method="GET",route="/api/x"} 1' in text
        assert "http_request_duration_seconds_sum" in text

    def test_document_ends_with_a_newline(self):
        """Some scrapers reject an exposition document without a trailing newline."""
        assert metrics.registry.render_prometheus().endswith("\n")

    def test_label_values_are_escaped(self):
        """One unescaped quote makes the WHOLE document unparseable, discarding
        every metric in it — not just the offending series."""
        c = metrics.registry.counter("t_escape", "help", ("label",))
        c.inc(labels=['va"lue',])
        rendered = metrics.registry.render_prometheus()
        assert r'label="va\"lue"' in rendered

    def test_every_line_is_a_comment_or_a_sample(self):
        """A cheap structural parse of the whole document."""
        metrics.http_requests_total.inc(labels=("GET", "/api/x", "200"))
        metrics.http_request_duration_seconds.observe(0.1, labels=("GET", "/api/x"))
        for line in metrics.registry.render_prometheus().splitlines():
            if not line or line.startswith("#"):
                continue
            assert re.match(
                r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{.*\})? [-+]?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$",
                line,
            ), line

    def test_bucket_bounds_render_without_padding(self):
        """`le="0.005"`, not `le="0.005000"` — and no truncation of a small sum."""
        metrics.http_request_duration_seconds.observe(0.0000042, labels=("GET", "/api/x"))
        text = metrics.registry.render_prometheus()
        assert 'le="0.005"' in text
        assert 'le="0.005000"' not in text
        assert "http_request_duration_seconds_sum" in text
        # The observation is far below six decimals; fixed formatting would have
        # rendered it as 0.000004 or 0.000000.
        sum_line = [
            line for line in text.splitlines()
            if line.startswith("http_request_duration_seconds_sum")
        ][0]
        assert float(sum_line.rsplit(" ", 1)[1]) == pytest.approx(0.0000042)


class TestRequestMetrics:
    def test_a_request_updates_all_four_signals(self, instrumented_app):
        TestClient(instrumented_app).get("/echo/42")

        assert metrics.http_requests_total.value(("GET", "/echo/{item_id}", "200")) == 1
        assert metrics.http_request_duration_seconds.snapshot(("GET", "/echo/{item_id}"))["count"] == 1
        # In-flight returns to zero once the request completes.
        assert metrics.http_requests_in_flight.value() == 0

    def test_route_TEMPLATE_is_the_label_never_the_raw_path(self, instrumented_app):
        """The cardinality rule. Labelling by raw path is one series per ID —
        unbounded memory in this process AND in whatever scrapes it."""
        client = TestClient(instrumented_app)
        for item_id in ("1", "2", "3", "abc", "68f2a1b4c9d3e5f7a1b2c3d4"):
            client.get(f"/echo/{item_id}")

        assert metrics.http_requests_total.value(("GET", "/echo/{item_id}", "200")) == 5
        rendered = metrics.registry.render_prometheus()
        assert "68f2a1b4c9d3e5f7a1b2c3d4" not in rendered
        assert 'route="/echo/{item_id}"' in rendered

    def test_unmatched_requests_collapse_into_one_bucket(self, instrumented_app):
        """A scanner walking random URLs must not be able to create series."""
        client = TestClient(instrumented_app)
        for path in ("/wp-admin", "/.env", "/phpmyadmin", "/../etc/passwd"):
            client.get(path)

        assert metrics.http_requests_total.value(("GET", UNMATCHED_ROUTE, "404")) == 4
        rendered = metrics.registry.render_prometheus()
        assert "wp-admin" not in rendered
        assert "phpmyadmin" not in rendered

    def test_4xx_counts_as_a_client_error(self, instrumented_app):
        TestClient(instrumented_app).get("/nope")
        assert metrics.http_request_errors_total.value(("GET", UNMATCHED_ROUTE, "client")) == 1

    def test_an_unhandled_exception_is_recorded_as_its_own_kind(self, instrumented_app):
        client = TestClient(instrumented_app, raise_server_exceptions=False)
        client.get("/boom")
        assert metrics.http_request_errors_total.value(("GET", "/boom", "exception")) == 1

    def test_in_flight_returns_to_zero_even_when_a_handler_raises(self, instrumented_app):
        """A missed decrement is PERMANENT — the gauge would read as a
        saturation incident for the life of the process."""
        client = TestClient(instrumented_app, raise_server_exceptions=False)
        for _ in range(3):
            client.get("/boom")
        assert metrics.http_requests_in_flight.value() == 0

    def test_probe_traffic_is_still_counted(self, instrumented_app):
        """Probes are not access-logged by default, but they ARE measured."""
        TestClient(instrumented_app).get("/api/health/live")
        assert metrics.http_requests_total.value(("GET", "/api/health/live", "200")) == 1


class TestMetricsEndpoint:
    def test_serves_prometheus_text_by_default(self, instrumented_app, dev_env):
        response = TestClient(instrumented_app).get("/api/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "# TYPE http_requests_total counter" in response.text

    def test_json_format_is_available(self, instrumented_app, dev_env):
        response = TestClient(instrumented_app).get("/api/metrics?format=json")
        assert response.status_code == 200
        body = response.json()
        assert "http_requests_total" in body["metrics"]
        assert body["metrics"]["http_requests_total"]["type"] == "counter"

    def test_open_outside_production(self, instrumented_app, dev_env):
        assert TestClient(instrumented_app).get("/api/metrics").status_code == 200

    def test_fails_closed_in_production_without_a_token(self, instrumented_app, prod_env, monkeypatch):
        """Defaulting to open is how metrics endpoints end up in Shodan."""
        monkeypatch.delenv("METRICS_TOKEN", raising=False)
        monkeypatch.delenv("METRICS_ALLOW_UNAUTHENTICATED", raising=False)
        response = TestClient(instrumented_app).get("/api/metrics")
        assert response.status_code == 403
        assert "METRICS_TOKEN" in response.json()["detail"]

    def test_production_accepts_a_bearer_token(self, instrumented_app, prod_env, monkeypatch):
        monkeypatch.setenv("METRICS_TOKEN", "s3cret-scrape-token-value")
        client = TestClient(instrumented_app)
        assert client.get("/api/metrics").status_code == 401
        ok = client.get(
            "/api/metrics", headers={"Authorization": "Bearer s3cret-scrape-token-value"}
        )
        assert ok.status_code == 200

    def test_production_accepts_the_dedicated_header(self, instrumented_app, prod_env, monkeypatch):
        monkeypatch.setenv("METRICS_TOKEN", "s3cret-scrape-token-value")
        response = TestClient(instrumented_app).get(
            "/api/metrics", headers={"X-Metrics-Token": "s3cret-scrape-token-value"}
        )
        assert response.status_code == 200

    def test_a_wrong_token_is_rejected(self, instrumented_app, prod_env, monkeypatch):
        monkeypatch.setenv("METRICS_TOKEN", "s3cret-scrape-token-value")
        response = TestClient(instrumented_app).get(
            "/api/metrics", headers={"X-Metrics-Token": "wrong"}
        )
        assert response.status_code == 401

    def test_explicit_opt_out_allows_unauthenticated_production_access(
        self, instrumented_app, prod_env, monkeypatch
    ):
        monkeypatch.delenv("METRICS_TOKEN", raising=False)
        monkeypatch.setenv("METRICS_ALLOW_UNAUTHENTICATED", "1")
        assert TestClient(instrumented_app).get("/api/metrics").status_code == 200


# --------------------------------------------------------------------------- #
# Diagnostics                                                                   #
# --------------------------------------------------------------------------- #
class TestDiagnostics:
    def test_reports_build_and_runtime_facts(self, instrumented_app, dev_env, monkeypatch):
        monkeypatch.setenv("APP_VERSION", "1.4.2")
        monkeypatch.setenv("VCS_REF", "a1b2c3d")
        monkeypatch.setenv("BUILD_DATE", "2026-07-22T10:00:00Z")

        body = TestClient(instrumented_app).get("/api/diagnostics").json()
        assert body["build"] == {
            "version": "1.4.2",
            "revision": "a1b2c3d",
            "build_date": "2026-07-22T10:00:00Z",
        }
        assert body["environment"] == "development"
        assert body["uptime_seconds"] >= 0
        assert body["process"]["pid"] > 0
        assert body["lifecycle"] == health.STARTING

    def test_version_falls_back_to_an_honest_dev_marker(self, monkeypatch):
        monkeypatch.delenv("APP_VERSION", raising=False)
        assert runtime.service_version() == runtime.DEFAULT_VERSION

    def test_dependencies_are_presence_only_never_values(
        self, instrumented_app, dev_env, monkeypatch
    ):
        """The security line: report THAT something is configured, never WHAT."""
        monkeypatch.setenv("MONGO_URL", "mongodb://admin:hunter2@db.internal:27017")
        monkeypatch.setenv("REDIS_URL", "redis://:sup3rs3cret@cache.internal:6379")

        raw = TestClient(instrumented_app).get("/api/diagnostics").text
        assert "hunter2" not in raw
        assert "sup3rs3cret" not in raw
        assert "db.internal" not in raw
        assert "cache.internal" not in raw

        body = json.loads(raw)
        assert body["dependencies"] == {"mongodb": "configured", "redis": "configured"}

    def test_unconfigured_dependency_is_reported_as_such(
        self, instrumented_app, dev_env, monkeypatch
    ):
        monkeypatch.delenv("REDIS_URL", raising=False)
        body = TestClient(instrumented_app).get("/api/diagnostics").json()
        assert body["dependencies"]["redis"] == "not_configured"

    def test_no_environment_variable_names_or_values_leak(
        self, instrumented_app, dev_env, monkeypatch
    ):
        monkeypatch.setenv("JWT_SECRET", "a-very-secret-signing-key-value-here")
        monkeypatch.setenv("CLAUDE_API_KEY", "sk-ant-not-a-real-key")
        raw = TestClient(instrumented_app).get("/api/diagnostics").text
        assert "a-very-secret-signing-key-value-here" not in raw
        assert "sk-ant-not-a-real-key" not in raw

    def test_gated_in_production(self, instrumented_app, prod_env, monkeypatch):
        monkeypatch.delenv("METRICS_TOKEN", raising=False)
        monkeypatch.delenv("METRICS_ALLOW_UNAUTHENTICATED", raising=False)
        assert TestClient(instrumented_app).get("/api/diagnostics").status_code == 403


# --------------------------------------------------------------------------- #
# Structured logging                                                            #
# --------------------------------------------------------------------------- #
def _format_json(record_factory_kwargs=None, **extra):
    """Format one record through StructuredFormatter and return the parsed dict."""
    formatter = obs_logging.StructuredFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=(record_factory_kwargs or {}).get("msg", "hello %s"),
        args=(record_factory_kwargs or {}).get("args", ("world",)),
        exc_info=(record_factory_kwargs or {}).get("exc_info"),
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return json.loads(formatter.format(record))


class TestStructuredLogging:
    def test_record_is_valid_json_with_the_expected_schema(self):
        payload = _format_json()
        for field in ("timestamp", "level", "logger", "message", "service", "environment", "version", "request_id"):
            assert field in payload, field
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"
        assert payload["message"] == "hello world"

    def test_timestamp_is_iso8601_utc(self):
        payload = _format_json()
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$", payload["timestamp"])

    def test_request_id_is_injected_from_the_context_without_a_call_site_change(self):
        """The property that makes this work across 12,000 lines of existing
        logging calls: an untouched logger.info() still gets correlated."""
        token = context.bind("abcdef0123456789", method="POST", path="/api/trades")
        try:
            payload = _format_json()
        finally:
            context.reset(token)
        assert payload["request_id"] == "abcdef0123456789"
        assert payload["method"] == "POST"
        assert payload["path"] == "/api/trades"

    def test_sentinel_request_id_outside_a_request(self):
        assert _format_json()["request_id"] == context.NO_REQUEST_ID

    def test_extra_fields_are_emitted_as_structured_fields(self):
        payload = _format_json(route="/api/trades/{trade_id}", status_code=201, duration_ms=12.5)
        assert payload["route"] == "/api/trades/{trade_id}"
        assert payload["status_code"] == 201
        assert payload["duration_ms"] == 12.5

    def test_sensitive_extra_fields_are_redacted(self):
        """Uses the SAME marker list as the audit log — one list, two consumers."""
        payload = _format_json(
            password="hunter2",
            access_token="eyJhbGciOi...",
            api_key="sk-ant-123",
            csrf_token="abc",
            user_id="507f1f77bcf86cd799439011",
        )
        assert payload["password"] == "[REDACTED]"
        assert payload["access_token"] == "[REDACTED]"
        assert payload["api_key"] == "[REDACTED]"
        assert payload["csrf_token"] == "[REDACTED]"
        # Non-sensitive fields survive — over-redaction destroys the log's value.
        assert payload["user_id"] == "507f1f77bcf86cd799439011"

    def test_nested_sensitive_fields_are_redacted(self):
        payload = _format_json(details={"user": {"email": "a@b.com", "password": "hunter2"}})
        assert payload["details"]["user"]["password"] == "[REDACTED]"
        assert payload["details"]["user"]["email"] == "a@b.com"

    @pytest.mark.parametrize(
        "message,secret",
        [
            ("auth failed for token=eyJhbGciOiJIUzI1NiJ9", "eyJhbGciOiJIUzI1NiJ9"),
            ("connecting with password: hunter2", "hunter2"),
            ('using api_key="sk-ant-abc123"', "sk-ant-abc123"),
            ("header Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ],
    )
    def test_credential_shaped_values_in_free_text_are_scrubbed(self, message, secret, monkeypatch):
        """Defence in depth over 12,000 lines of pre-existing f-string logging."""
        monkeypatch.setenv("LOG_SCRUB_MESSAGES", "1")
        payload = _format_json({"msg": message, "args": ()})
        assert secret not in payload["message"]
        assert "[REDACTED]" in payload["message"]

    @pytest.mark.parametrize(
        "message",
        [
            "trade 68f2 exited at 1420.50 (+2.3%)",
            # Regression: the scrubber's first revision accepted bare whitespace
            # as a key/value separator, so this real message from the config
            # validator came out as "...username:password [REDACTED] database...".
            "MONGO_URL carries no username:password — the database is either "
            "unauthenticated or every query will fail authentication.",
            "password policy rejected the submitted value",
            "rotating the api_key next quarter",
        ],
    )
    def test_scrubbing_leaves_ordinary_prose_alone(self, message, monkeypatch):
        """A scrubber that damages legitimate messages costs more than it saves."""
        monkeypatch.setenv("LOG_SCRUB_MESSAGES", "1")
        payload = _format_json({"msg": message, "args": ()})
        assert payload["message"] == message

    def test_exception_is_structured_and_its_message_scrubbed(self, monkeypatch):
        monkeypatch.setenv("LOG_SCRUB_MESSAGES", "1")
        try:
            raise ValueError("refused: mongodb password=hunter2")
        except ValueError:
            import sys

            payload = _format_json({"msg": "boom", "args": (), "exc_info": sys.exc_info()})
        assert payload["exception"]["type"] == "ValueError"
        assert "hunter2" not in payload["exception"]["message"]
        assert "stacktrace" in payload["exception"]

    def test_unserialisable_extra_values_do_not_break_the_record(self):
        class Opaque:
            def __repr__(self):
                return "<opaque>"

        payload = _format_json(thing=Opaque())
        assert payload["thing"] == "<opaque>"

    def test_a_broken_format_string_does_not_raise(self):
        """A logging bug must not be able to hide its own cause."""
        payload = _format_json({"msg": "value is %d", "args": ("not-an-int",)})
        assert "unformattable" in payload["message"]


class TestLoggingConfiguration:
    def test_format_defaults_to_json_in_production(self, prod_env, monkeypatch):
        monkeypatch.delenv("LOG_FORMAT", raising=False)
        assert obs_logging.log_format() == obs_logging.FORMAT_JSON

    def test_format_defaults_to_text_in_development(self, dev_env, monkeypatch):
        monkeypatch.delenv("LOG_FORMAT", raising=False)
        assert obs_logging.log_format() == obs_logging.FORMAT_TEXT

    def test_explicit_format_wins(self, prod_env, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "text")
        assert obs_logging.log_format() == obs_logging.FORMAT_TEXT

    def test_level_resolution(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "debug")
        assert obs_logging.log_level() == logging.DEBUG
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        assert obs_logging.log_level() == logging.WARNING

    def test_an_invalid_level_falls_back_rather_than_failing_the_boot(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
        assert obs_logging.log_level() == logging.INFO

    def test_configure_replaces_handlers_rather_than_appending(self, monkeypatch):
        """A library that called basicConfig would otherwise cause every line to
        be printed twice, in two different formats."""
        root = logging.getLogger()
        original = list(root.handlers)
        try:
            logging.basicConfig()
            obs_logging.reset_for_tests()
            obs_logging.configure_logging(force=True)
            assert len(root.handlers) == 1
        finally:
            root.handlers = original
            obs_logging.reset_for_tests()

    def test_probe_access_logging_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("LOG_HEALTH_REQUESTS", raising=False)
        assert obs_logging.log_health_requests() is False


class TestAccessLog:
    def test_one_line_per_request_with_the_route_template(self, instrumented_app, caplog):
        with caplog.at_level(logging.INFO, logger=obs_logging.ACCESS_LOGGER_NAME):
            TestClient(instrumented_app).get("/echo/68f2a1b4c9d3e5f7")

        records = [r for r in caplog.records if r.name == obs_logging.ACCESS_LOGGER_NAME]
        assert len(records) == 1
        assert records[0].route == "/echo/{item_id}"
        assert records[0].status_code == 200
        assert records[0].duration_ms >= 0

    def test_query_strings_are_never_logged(self, instrumented_app, caplog):
        """Nothing in a request is likelier to carry a credential: an OAuth code,
        a reset token, a shared link key."""
        with caplog.at_level(logging.INFO, logger=obs_logging.ACCESS_LOGGER_NAME):
            TestClient(instrumented_app).get("/echo/1?token=super-secret&code=oauth-code")

        records = [r for r in caplog.records if r.name == obs_logging.ACCESS_LOGGER_NAME]
        assert len(records) == 1
        assert "super-secret" not in records[0].http_path
        assert "oauth-code" not in records[0].http_path
        assert "?" not in records[0].http_path

    def test_severity_follows_the_status_code(self, instrumented_app, caplog):
        with caplog.at_level(logging.INFO, logger=obs_logging.ACCESS_LOGGER_NAME):
            client = TestClient(instrumented_app, raise_server_exceptions=False)
            client.get("/echo/1")   # 200
            client.get("/missing")  # 404
        levels = {
            r.status_code: r.levelno
            for r in caplog.records
            if r.name == obs_logging.ACCESS_LOGGER_NAME
        }
        assert levels[200] == logging.INFO
        assert levels[404] == logging.WARNING

    def test_probe_traffic_is_not_access_logged_by_default(
        self, instrumented_app, caplog, monkeypatch
    ):
        """~26,000 identical lines a day across three replicas is a bill, and it
        pushes the lines that matter out of the retention window."""
        monkeypatch.delenv("LOG_HEALTH_REQUESTS", raising=False)
        with caplog.at_level(logging.INFO, logger=obs_logging.ACCESS_LOGGER_NAME):
            client = TestClient(instrumented_app)
            client.get("/api/health/live")
            client.get("/api/health/ready")

        assert not [r for r in caplog.records if r.name == obs_logging.ACCESS_LOGGER_NAME]

    def test_probe_traffic_can_be_logged_on_demand(self, instrumented_app, caplog, monkeypatch):
        monkeypatch.setenv("LOG_HEALTH_REQUESTS", "1")
        with caplog.at_level(logging.INFO, logger=obs_logging.ACCESS_LOGGER_NAME):
            TestClient(instrumented_app).get("/api/health/live")

        assert [r for r in caplog.records if r.name == obs_logging.ACCESS_LOGGER_NAME]


# --------------------------------------------------------------------------- #
# Route template extraction                                                     #
# --------------------------------------------------------------------------- #
class TestRouteTemplate:
    def test_unmatched_scope_returns_the_shared_bucket(self):
        assert route_template({"path": "/whatever"}) == UNMATCHED_ROUTE

    def test_prefers_path_format(self):
        class FakeRoute:
            path_format = "/api/trades/{trade_id}"
            path = "/api/trades/{trade_id}"

        assert route_template({"route": FakeRoute()}) == "/api/trades/{trade_id}"

    def test_falls_back_to_path_when_path_format_is_absent(self):
        class FakeRoute:
            path = "/api/x"

        assert route_template({"route": FakeRoute()}) == "/api/x"


# --------------------------------------------------------------------------- #
# Wiring into the real application                                              #
# --------------------------------------------------------------------------- #
class TestApplicationWiring:
    def test_every_operational_endpoint_is_registered_on_the_real_app(self):
        import server

        paths = {route.path for route in server.app.routes if hasattr(route, "path")}
        for expected in (
            "/api/health",
            "/api/health/live",
            "/api/health/ready",
            "/api/health/startup",
            "/api/metrics",
            "/api/diagnostics",
        ):
            assert expected in paths, expected

    def test_the_legacy_liveness_contract_is_preserved(self, client):
        """`/api` returning status="running" is asserted by the PH2.4 CI smoke
        tests and by docker/healthcheck.sh. It must not drift."""
        body = client.get("/api").json()
        assert body["status"] == "running"

    def test_the_portfolio_health_route_is_untouched(self):
        """/api/monitor/health is an AUTHENTICATED AI portfolio analysis and is
        unrelated to process health, despite the name."""
        import server

        paths = {route.path for route in server.app.routes if hasattr(route, "path")}
        assert "/api/monitor/health" in paths

    def test_operational_paths_are_exempt_from_the_rate_limiter(self):
        """A probe cadence that trips the limiter makes the limiter manufacture
        the outage it exists to prevent."""
        from security.rate_limit import _MIDDLEWARE_EXEMPT_PATHS

        for path in (
            "/api/health",
            "/api/health/live",
            "/api/health/ready",
            "/api/health/startup",
            "/api/metrics",
            "/api/diagnostics",
        ):
            assert path in _MIDDLEWARE_EXEMPT_PATHS, path

    def test_request_id_is_exposed_to_browser_javascript(self):
        """Without the CORS exposure the header arrives but JS cannot read it,
        so the frontend could never show a user their correlation ID."""
        from security.cors import EXPOSE_HEADERS

        assert "X-Request-ID" in EXPOSE_HEADERS

    def test_observability_middleware_is_outermost(self):
        """It must run FIRST so rejections from inner middleware are counted and
        carry a request ID."""
        import server

        stack = [m.cls.__name__ for m in server.app.user_middleware]
        # Starlette stores user_middleware in reverse execution order:
        # index 0 is the outermost / first to run.
        assert stack[0] == "ObservabilityMiddleware"

    def test_the_real_app_stamps_a_request_id(self, client):
        response = client.get("/api")
        assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])

    def test_audit_records_pick_up_the_context_request_id(self):
        """PH1.10 already had a request_id field; before PH2.5 nothing generated
        one, so it was None on every record."""
        from security.audit import request_context

        token = context.bind("abcdef0123456789")
        try:
            _, _, rid = request_context(None)
        finally:
            context.reset(token)
        assert rid == "abcdef0123456789"


# --------------------------------------------------------------------------- #
# Overhead                                                                      #
# --------------------------------------------------------------------------- #
class TestOverhead:
    def test_instrumentation_cost_per_request_is_negligible(self):
        """Observability that slows the API is a bad trade. This is a coarse
        guard against a future change adding something expensive (a DB write, a
        `psutil` call, a `datetime.now()` per field) to the request path."""
        import time

        plain = FastAPI()

        @plain.get("/x")
        async def x_plain():
            return {"ok": True}

        instrumented = FastAPI()

        @instrumented.get("/x")
        async def x_instrumented():
            return {"ok": True}

        apply_observability(instrumented)

        def measure(app):
            client = TestClient(app)
            for _ in range(20):  # warm up imports/routing
                client.get("/x")
            start = time.perf_counter()
            for _ in range(200):
                client.get("/x")
            return (time.perf_counter() - start) / 200

        baseline = measure(plain)
        with_obs = measure(instrumented)
        overhead_ms = (with_obs - baseline) * 1000

        # The real cost is tens of microseconds; 2ms is a deliberately loose
        # ceiling so this cannot flake on a contended CI runner while still
        # catching an order-of-magnitude regression.
        assert overhead_ms < 2.0, f"instrumentation overhead {overhead_ms:.3f}ms/request"

    def test_health_endpoint_latency_is_bounded(self, instrumented_app):
        import time

        async def ok():
            return True

        health.register_check("mongodb", ok, critical=True)
        health.lifecycle.mark_started()
        client = TestClient(instrumented_app)

        client.get("/api/health/live")
        start = time.perf_counter()
        for _ in range(50):
            client.get("/api/health/live")
        live_ms = (time.perf_counter() - start) / 50 * 1000

        assert live_ms < 20.0, f"liveness took {live_ms:.2f}ms"

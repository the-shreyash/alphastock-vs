"""In-process application metrics (PH2.5).

WHY THIS MODULE EXISTS
----------------------
Logs answer "what happened to this one request?". Metrics answer "what is
happening to all of them?" — a question logs cannot answer at production volume
without an aggregation pipeline. Four numbers cover most of an on-call shift:
how many requests, how slow, how many failed, how many are in flight right now.
Google's "four golden signals" (traffic, latency, errors, saturation) are that
list; this module records all four.

WHY NO prometheus_client DEPENDENCY
-----------------------------------
The sprint scope explicitly excludes running Prometheus, and adding a dependency
to a production image for a few counters is a poor trade: another package to
pin, patch and audit, plus a multiprocess-mode footgun that bites the first time
`WEB_CONCURRENCY` goes above 1. What *is* worth adopting is the **text exposition
format**, which is a stable, trivially generated, human-readable de-facto
standard. Emitting it costs one string builder and means PH2.10 can point a
scraper at `/api/metrics` with zero re-instrumentation — and any other collector
(OpenTelemetry, Datadog, Vector) can read it too.

THE CARDINALITY RULE — the mistake that takes monitoring systems down
---------------------------------------------------------------------
A metric series is identified by its name *and its full label set*. Every
distinct combination is a separate time series with its own memory here and its
own storage in whatever scrapes it. Labelling a request counter with the raw URL
path is therefore an outage waiting to happen: `/api/trades/{trade_id}` becomes
one series per trade ID, a vulnerability scanner walking random URLs adds one
series per 404, and memory grows without bound in the application *and* in the
monitoring backend — which is how a metrics change takes out the system it was
supposed to observe.

So: labels are **route templates** (`/api/trades/{trade_id}`), never raw paths;
unmatched requests collapse to a single `<unmatched>` bucket; and there is a
hard per-metric series ceiling (:data:`MAX_SERIES_PER_METRIC`) after which new
label combinations fold into `<overflow>` and a dedicated counter records that
it happened. The ceiling is a backstop against a bug in the label plumbing —
if `metrics_series_dropped_total` is ever non-zero, that is the alert.

CONCURRENCY AND COST
--------------------
Values live in plain dicts behind one `threading.Lock`. The lock is held for a
dict lookup and an addition — nanoseconds — and it is a lock rather than bare
dict mutation because a multi-worker deployment may run the ASGI app alongside
threadpool-executed sync endpoints. Rendering, bucket summation and process
introspection all happen at *scrape* time, so the request path pays only the
increment.
"""
from __future__ import annotations

import bisect
import logging
import os
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Policy constants                                                              #
# --------------------------------------------------------------------------- #
# Per-metric ceiling on distinct label combinations. 500 is generous for this
# API — roughly (routes x methods x status codes) with headroom — while still
# being a number that cannot hurt a scraper. Tunable for an unusual deployment,
# floored at 1 so a typo'd "0" cannot silently disable all metrics.
MAX_SERIES_PER_METRIC = max(1, int(os.environ.get("METRICS_MAX_SERIES", "500")))

# The label value every over-ceiling combination folds into.
OVERFLOW_LABEL = "<overflow>"

# Latency histogram buckets, in seconds, upper-bound inclusive ("le" semantics).
# Chosen for a JSON API in front of Mongo and third-party market-data providers:
# dense from 5ms to 500ms where nearly all traffic lives and where an SLO is
# actually set, then sparse out to 10s to catch provider stalls. Buckets are
# fixed at import: changing them mid-process would corrupt the cumulative
# counts, and changing them between deploys resets the histogram (a known,
# accepted cost of the format — worth stating so nobody "just adds one bucket"
# during an incident and then distrusts the graph).
DEFAULT_LATENCY_BUCKETS: Tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

# Prometheus content type for the text exposition format, version 0.0.4.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

_LabelValues = Tuple[str, ...]


# --------------------------------------------------------------------------- #
# Exposition-format escaping                                                    #
# --------------------------------------------------------------------------- #
def _escape_help(text: str) -> str:
    """Escape HELP text: backslash and newline only, per the format spec."""
    return text.replace("\\", r"\\").replace("\n", r"\n")


def _escape_label_value(value: str) -> str:
    """Escape a label value: backslash, double quote and newline.

    Unescaped, any of the three produces a malformed exposition document that a
    scraper rejects *wholesale* — one bad label value would discard every metric
    in the response, not just its own.
    """
    return (
        value.replace("\\", r"\\")
        .replace('"', r"\"")
        .replace("\n", r"\n")
    )


def _render_labels(names: Sequence[str], values: _LabelValues) -> str:
    if not names:
        return ""
    pairs = ",".join(
        f'{name}="{_escape_label_value(str(value))}"'
        for name, value in zip(names, values)
    )
    return "{" + pairs + "}"


def _format_value(value: float) -> str:
    """Render a sample value.

    Integral floats are emitted without a decimal point so counters read as
    counters (`1234`, not `1234.0`). Everything else uses Python's shortest
    round-trip float repr — `0.005`, not `0.005000`. Fixed-precision formatting
    was the first implementation and it was wrong twice over: it padded every
    histogram bucket bound with meaningless zeros (`le="0.005000"` next to
    `le="1"` in the same metric), and at six decimals it would silently truncate
    a `_sum` that had accumulated into the microseconds.
    """
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


# --------------------------------------------------------------------------- #
# Metric families                                                               #
# --------------------------------------------------------------------------- #
class _Metric:
    """Shared base: name, help text, label schema, and the series ceiling.

    Subclasses own their value storage. `_lock` guards every mutation; it is
    per-metric rather than global so an unrelated counter can never contend with
    the request-latency histogram on the hot path.
    """

    type_name = "untyped"

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = ()):
        self.name = name
        self.help_text = help_text
        self.label_names: Tuple[str, ...] = tuple(label_names)
        self._lock = threading.Lock()
        self._dropped = 0

    def _key(self, label_values: Sequence[str], existing: Dict[_LabelValues, object]) -> _LabelValues:
        """Normalize label values into a storage key, applying the ceiling.

        Called with `_lock` held. A label set that is already present is always
        accepted (the ceiling bounds *distinct* series, and refusing to update an
        existing one would make graphs lie). A new set beyond the ceiling folds
        into the overflow series so the signal degrades instead of disappearing.
        """
        if len(label_values) != len(self.label_names):
            # A caller/label-schema mismatch is a programming error. Fail loudly
            # in the logs but never raise: a metrics bug must not 500 a route.
            logger.error(
                "metric %s expects labels %s, got %d value(s) — recording as overflow",
                self.name, self.label_names, len(label_values),
            )
            return tuple(OVERFLOW_LABEL for _ in self.label_names)

        key = tuple(str(v) for v in label_values)
        if key in existing or len(existing) < MAX_SERIES_PER_METRIC:
            return key
        self._dropped += 1
        return tuple(OVERFLOW_LABEL for _ in self.label_names)

    @property
    def dropped(self) -> int:
        return self._dropped

    def collect(self) -> List[Tuple[str, _LabelValues, float]]:  # pragma: no cover - interface
        """(sample_name, label_values, value) triples for exposition."""
        raise NotImplementedError


class Counter(_Metric):
    """A value that only ever increases (and resets to zero on restart).

    Counters are the right shape for "how many": a scraper computes rates from
    successive samples, so a restart is a visible reset rather than corrupted
    data. Never use one for a value that can go down — that is a gauge.
    """

    type_name = "counter"

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = ()):
        super().__init__(name, help_text, label_names)
        self._values: Dict[_LabelValues, float] = {}

    def inc(self, amount: float = 1.0, labels: Sequence[str] = ()) -> None:
        if amount < 0:
            logger.error("counter %s got a negative increment (%s); ignored", self.name, amount)
            return
        with self._lock:
            key = self._key(labels, self._values)
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, labels: Sequence[str] = ()) -> float:
        with self._lock:
            return self._values.get(tuple(str(v) for v in labels), 0.0)

    def collect(self) -> List[Tuple[str, _LabelValues, float]]:
        with self._lock:
            return [(self.name, key, val) for key, val in self._values.items()]


class Gauge(_Metric):
    """A value that goes up and down — in-flight requests, uptime, memory."""

    type_name = "gauge"

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = ()):
        super().__init__(name, help_text, label_names)
        self._values: Dict[_LabelValues, float] = {}

    def set(self, value: float, labels: Sequence[str] = ()) -> None:
        with self._lock:
            key = self._key(labels, self._values)
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, labels: Sequence[str] = ()) -> None:
        with self._lock:
            key = self._key(labels, self._values)
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, labels: Sequence[str] = ()) -> None:
        self.inc(-amount, labels)

    def value(self, labels: Sequence[str] = ()) -> float:
        with self._lock:
            return self._values.get(tuple(str(v) for v in labels), 0.0)

    def collect(self) -> List[Tuple[str, _LabelValues, float]]:
        with self._lock:
            return [(self.name, key, val) for key, val in self._values.items()]


class Histogram(_Metric):
    """Bucketed observations — latency, above all.

    WHY A HISTOGRAM AND NOT AN AVERAGE: a mean latency is the single most
    misleading number in operations. If 99 requests take 10ms and one takes 10
    seconds, the mean is 110ms and every dashboard looks fine while a user sits
    on a broken page. Buckets preserve the shape of the distribution, so p95/p99
    can be computed by the scraper and the tail — where the outages live — stays
    visible.

    Storage is cumulative-by-bucket, matching the exposition format: each bucket
    holds the count of observations **less than or equal to** its upper bound,
    plus `_sum` and `_count`.
    """

    type_name = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_LATENCY_BUCKETS,
    ):
        super().__init__(name, help_text, label_names)
        self.buckets: Tuple[float, ...] = tuple(sorted(buckets))
        self._counts: Dict[_LabelValues, List[int]] = {}
        self._sums: Dict[_LabelValues, float] = {}
        self._totals: Dict[_LabelValues, int] = {}

    def observe(self, value: float, labels: Sequence[str] = ()) -> None:
        with self._lock:
            key = self._key(labels, self._counts)
            counts = self._counts.get(key)
            if counts is None:
                counts = [0] * len(self.buckets)
                self._counts[key] = counts
                self._sums[key] = 0.0
                self._totals[key] = 0
            # bisect_left finds the first bucket whose upper bound is >= value,
            # i.e. the "le" bucket this observation belongs to. Incrementing from
            # there to the end keeps the counts cumulative, which is what the
            # format requires and what lets a scraper subtract adjacent buckets.
            index = bisect.bisect_left(self.buckets, value)
            for i in range(index, len(counts)):
                counts[i] += 1
            self._sums[key] += value
            self._totals[key] += 1

    def snapshot(self, labels: Sequence[str] = ()) -> dict:
        """Counts/sum/total for one label set — used by tests and the JSON view."""
        key = tuple(str(v) for v in labels)
        with self._lock:
            return {
                "buckets": dict(zip(self.buckets, self._counts.get(key, [0] * len(self.buckets)))),
                "sum": self._sums.get(key, 0.0),
                "count": self._totals.get(key, 0),
            }

    def collect(self) -> List[Tuple[str, _LabelValues, float]]:
        """Flatten to `_bucket` / `_sum` / `_count` samples.

        The `le` bucket bound is appended as an extra label value; the renderer
        knows to widen the label-name list for `_bucket` samples only.
        """
        out: List[Tuple[str, _LabelValues, float]] = []
        with self._lock:
            for key, counts in self._counts.items():
                for bound, count in zip(self.buckets, counts):
                    out.append((f"{self.name}_bucket", key + (_format_value(bound),), float(count)))
                # +Inf is mandatory and equals the total observation count.
                out.append((f"{self.name}_bucket", key + ("+Inf",), float(self._totals[key])))
                out.append((f"{self.name}_sum", key, self._sums[key]))
                out.append((f"{self.name}_count", key, float(self._totals[key])))
        return out


# --------------------------------------------------------------------------- #
# Registry                                                                      #
# --------------------------------------------------------------------------- #
class Registry:
    """The collection of metrics this process exposes.

    A class rather than module globals so the test suite can build an isolated
    registry per test — shared mutable counters across tests produce the kind of
    order-dependent failures that erode trust in a suite. The application uses
    the module-level default instance.
    """

    def __init__(self) -> None:
        self._metrics: Dict[str, _Metric] = {}
        self._lock = threading.Lock()
        # Callbacks evaluated at render time, for values that are cheaper (or
        # only correct) when sampled on demand: uptime, process RSS, health
        # status. This is what keeps the request path free of that work.
        self._collectors: List[Callable[[], None]] = []

    # -- registration ------------------------------------------------------- #
    def _register(self, metric: _Metric) -> _Metric:
        with self._lock:
            existing = self._metrics.get(metric.name)
            if existing is not None:
                # Idempotent: re-importing a module (or a test re-running wiring)
                # must return the same instance rather than silently replacing a
                # live metric and zeroing its history.
                return existing
            self._metrics[metric.name] = metric
            return metric

    def counter(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> Counter:
        return self._register(Counter(name, help_text, label_names))  # type: ignore[return-value]

    def gauge(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> Gauge:
        return self._register(Gauge(name, help_text, label_names))  # type: ignore[return-value]

    def histogram(
        self,
        name: str,
        help_text: str,
        label_names: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_LATENCY_BUCKETS,
    ) -> Histogram:
        return self._register(Histogram(name, help_text, label_names, buckets))  # type: ignore[return-value]

    def add_collector(self, fn) -> None:
        """Register a zero-arg callable run before each render.

        It must be fast and it must not raise; a raising collector is caught and
        logged so one broken gauge cannot blank the whole metrics response
        (which would be a monitoring outage on top of whatever it was reporting).
        """
        self._collectors.append(fn)

    def metrics(self) -> Iterable[_Metric]:
        with self._lock:
            return list(self._metrics.values())

    # -- rendering ---------------------------------------------------------- #
    def _run_collectors(self) -> None:
        for fn in list(self._collectors):
            try:
                fn()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("metrics collector %r failed: %s", getattr(fn, "__name__", fn), exc)

    def render_prometheus(self) -> str:
        """The full exposition document, ready to serve."""
        self._run_collectors()
        lines: List[str] = []
        for metric in sorted(self.metrics(), key=lambda m: m.name):
            samples = metric.collect()
            # A metric with no observations yet is emitted as HELP/TYPE only.
            # Skipping it entirely would make a dashboard panel read "no data"
            # rather than "zero", which are very different during an incident.
            lines.append(f"# HELP {metric.name} {_escape_help(metric.help_text)}")
            lines.append(f"# TYPE {metric.name} {metric.type_name}")
            for sample_name, label_values, value in samples:
                names = metric.label_names
                if sample_name.endswith("_bucket"):
                    names = names + ("le",)
                lines.append(
                    f"{sample_name}{_render_labels(names, label_values)} {_format_value(value)}"
                )
        # The exposition format requires a trailing newline; some scrapers reject
        # a document without one.
        return "\n".join(lines) + "\n"

    def snapshot(self) -> dict:
        """A JSON-shaped view of the same data.

        Exists for humans and for the test suite: `curl /api/metrics?format=json`
        during an incident is far easier to read than the text format, and a test
        asserting on a parsed dict is clearer than one grepping strings.
        """
        self._run_collectors()
        out: dict = {}
        for metric in sorted(self.metrics(), key=lambda m: m.name):
            series = []
            for sample_name, label_values, value in metric.collect():
                names = metric.label_names
                if sample_name.endswith("_bucket"):
                    names = names + ("le",)
                series.append(
                    {
                        "sample": sample_name,
                        "labels": dict(zip(names, label_values)),
                        "value": value,
                    }
                )
            out[metric.name] = {
                "type": metric.type_name,
                "help": metric.help_text,
                "series": series,
            }
        return out

    def dropped_series(self) -> int:
        return sum(m.dropped for m in self.metrics())


# --------------------------------------------------------------------------- #
# The application's metrics                                                     #
# --------------------------------------------------------------------------- #
# One module-level registry, mirroring how `security.audit` exposes one default
# logger: instrumentation call sites should not have to locate a registry.
registry = Registry()

# -- The four golden signals ------------------------------------------------- #
# Traffic. `route` is a template and `status` a numeric code, so cardinality is
# (routes x methods x codes) — bounded and stable.
http_requests_total = registry.counter(
    "http_requests_total",
    "Total HTTP requests handled, by method, route template and status code.",
    ("method", "route", "status"),
)

# Latency. Note it is NOT labelled by status: a histogram is the most expensive
# metric per series (one series per bucket), and mixing status into it would
# multiply that by ~15 for a question ("how slow are the 500s?") that is better
# answered from logs.
http_request_duration_seconds = registry.histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds, by method and route template.",
    ("method", "route"),
)

# Errors. Derivable from http_requests_total, but kept separate and explicit:
# `kind` distinguishes a client's fault (4xx) from ours (5xx) from an unhandled
# exception that never produced a response at all — three very different pages
# in the middle of the night.
http_request_errors_total = registry.counter(
    "http_request_errors_total",
    "HTTP requests that failed, by method, route template and error kind "
    "(client=4xx, server=5xx, exception=unhandled).",
    ("method", "route", "kind"),
)

# Saturation. The number a restart-loop or a stuck dependency shows first: it
# climbs monotonically while latency looks normal, because the slow requests
# have not finished yet and so have not been recorded anywhere else.
http_requests_in_flight = registry.gauge(
    "http_requests_in_flight",
    "HTTP requests currently being processed.",
)

# -- Application lifecycle --------------------------------------------------- #
app_start_time_seconds = registry.gauge(
    "app_start_time_seconds",
    "Unix timestamp at which this process started (constant; uptime = now - this).",
)
app_uptime_seconds = registry.gauge(
    "app_uptime_seconds",
    "Seconds since this process started.",
)
app_info = registry.gauge(
    "app_info",
    "Build and environment metadata; the value is always 1 (the info-metric idiom).",
    ("version", "environment", "python_version", "revision"),
)

# -- Dependency health ------------------------------------------------------- #
# Populated from the last readiness evaluation rather than by probing at scrape
# time: a scraper must never be able to drive load onto MongoDB.
dependency_up = registry.gauge(
    "dependency_up",
    "Last observed health of a dependency: 1 healthy, 0 unhealthy "
    "(-1 not configured / skipped).",
    ("dependency",),
)
health_check_duration_seconds = registry.histogram(
    "health_check_duration_seconds",
    "Duration of the most recent dependency health probes, in seconds.",
    ("dependency",),
    buckets=(0.005, 0.025, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# -- Self-instrumentation ---------------------------------------------------- #
# If this is ever non-zero, the label plumbing has a bug and the metrics above
# are quietly incomplete. It is the one metric whose purpose is to be alerted on
# at any value greater than zero.
metrics_series_dropped_total = registry.gauge(
    "metrics_series_dropped_total",
    "Label combinations folded into the overflow series because a metric hit "
    "METRICS_MAX_SERIES. Non-zero means instrumentation is mislabelled.",
)


# PH2.6. Non-zero means the log-file writer could not keep up and the files on
# disk have gaps — either the volume is too slow or the level is too verbose for
# it. Deliberately a metric rather than a log line: the one thing you cannot
# rely on to report a broken log pipeline is the log pipeline.
log_records_dropped_total = registry.gauge(
    "log_records_dropped_total",
    "Log records discarded because the file-sink queue was full (PH2.6). "
    "Non-zero means log files on this instance are incomplete.",
)


def _collect_runtime_gauges() -> None:
    """Render-time collector for uptime and the overflow counters."""
    app_uptime_seconds.set(max(0.0, time.time() - _PROCESS_START_WALL))
    metrics_series_dropped_total.set(registry.dropped_series())
    try:
        # Imported lazily and at scrape time. `log_streams` imports nothing from
        # this module, so a module-level import would be safe today — but it
        # would make the metrics registry a boot-order dependency of the logging
        # pipeline, and the logging pipeline is configured first, on purpose.
        from observability import log_streams

        log_records_dropped_total.set(float(log_streams.dropped_records()))
    except Exception:  # pragma: no cover - defensive
        pass


_PROCESS_START_WALL = time.time()
app_start_time_seconds.set(_PROCESS_START_WALL)
registry.add_collector(_collect_runtime_gauges)


def _collect_process_gauges() -> None:
    """Render-time collector for process resource usage.

    `psutil` is already a dependency (the admin system-health endpoint uses it).
    Sampling happens here, at scrape time, precisely because `cpu_percent` and
    `memory_info` are syscalls that have no business running on a request path.
    Best-effort: in a restricted container `/proc` may be unreadable, and losing
    two gauges is not a reason to fail the metrics response.
    """
    try:
        import psutil

        process = psutil.Process()
        process_resident_memory_bytes.set(float(process.memory_info().rss))
        process_open_fds.set(float(process.num_fds()))
    except Exception:
        # Deliberately silent: this runs on every scrape, so logging a warning
        # here would produce one line per scrape interval forever on any platform
        # where psutil is restricted. The absent metric is itself the signal.
        pass


process_resident_memory_bytes = registry.gauge(
    "process_resident_memory_bytes",
    "Resident set size of this process, in bytes.",
)
process_open_fds = registry.gauge(
    "process_open_fds",
    "Number of open file descriptors held by this process.",
)
registry.add_collector(_collect_process_gauges)


# --------------------------------------------------------------------------- #
# In-process resource structures (PH3.6)                                        #
#                                                                               #
# WHY THESE ARE NOT COVERED BY process_resident_memory_bytes                     #
#                                                                               #
# RSS is what an operator watches, and it is the wrong instrument for the leaks  #
# this application can actually have. A dict that gains one key per WebSocket    #
# connection and never drops it costs a few hundred bytes; a hundred thousand    #
# connections later it is the largest object in the process, and for the whole   #
# first hour it moves RSS less than Python's allocator does between two idle     #
# samples. PH3.6 found two such maps — one keyed by an unauthenticated query     #
# parameter — and neither was visible on any dashboard while it grew.            #
#                                                                               #
# A leak is a SHAPE, not a size: a count that only ever rises. These gauges      #
# expose the counts, so the shape is alertable. Every one of them corresponds to #
# a structure with a documented bound in                                         #
# docs/performance/PH3_MEMORY_STABILITY.md §19 — a series that climbs past its   #
# budget and stays there is the alert, not the absolute number.                  #
#                                                                               #
# Collected at scrape time from in-process containers: every value below is a    #
# `len()`, so a scrape costs nothing and cannot perturb what it measures. The    #
# collector is registered by `server.py`, which is the module that knows what a  #
# socket manager is — the same split as the Redis families above.                #
# --------------------------------------------------------------------------- #
websocket_connections = registry.gauge(
    "websocket_connections",
    "Live WebSocket connections held by this process.",
)

# The one that leaked. It must track `websocket_connections` — specifically, it
# must fall back to zero when connections do. A flat-topped staircase here with
# `websocket_connections` at zero is a retention bug, and is exactly what PH3.6
# fixed.
websocket_tracked_users = registry.gauge(
    "websocket_tracked_users",
    "Distinct user ids with at least one live socket. Must return to zero when "
    "websocket_connections does; a floor above zero means per-user entries are "
    "being retained after disconnect.",
)

websocket_channel_subscriptions = registry.gauge(
    "websocket_channel_subscriptions",
    "Sockets holding a channel subscription set.",
)

background_tasks_running = registry.gauge(
    "background_tasks_running",
    "Supervised background tasks currently running (infrastructure/tasks.py).",
)

event_bus_subscribers = registry.gauge(
    "event_bus_subscribers",
    "Handlers registered on the in-process market event bus. Constant in a "
    "healthy process — a rising value means listeners are being registered "
    "repeatedly, which duplicates delivery rather than losing it.",
)

# One series per bounded cache rather than one gauge each, because they share a
# single question ("is it under its ceiling?") and `cache` is a fixed, tiny label
# set — no user id, symbol or key ever appears here.
app_cache_entries = registry.gauge(
    "app_cache_entries",
    "Entry count of each bounded in-process cache, by name. Compare against the "
    "ceilings in docs/performance/PH3_MEMORY_STABILITY.md §19.",
    ("cache",),
)


# --------------------------------------------------------------------------- #
# Redis (PH2.7)                                                                 #
#                                                                               #
# Two families, kept separate on purpose:                                        #
#                                                                               #
#   redis_*         what THIS PROCESS's client is experiencing — pool occupancy, #
#                   circuit state, command outcomes and latency. Collected from  #
#                   in-process counters at render time, so a scrape costs        #
#                   nothing and can never add load to Redis.                     #
#   redis_server_*  what the SERVER reports — memory, clients, evictions.        #
#                   Sampled by a background task on a fixed cadence              #
#                   (REDIS_STATS_INTERVAL_SECONDS), never at scrape time, for    #
#                   the same reason `dependency_up` is published from the        #
#                   readiness result rather than probed on scrape: whoever can   #
#                   reach /api/metrics must not be able to generate dependency   #
#                   load by scraping faster.                                     #
#                                                                               #
# The gauges live here rather than in `infrastructure/` so that the metric names #
# this process exposes are all declared in one file — the thing you grep when a  #
# dashboard panel says "no data". `infrastructure.redis_client` registers the    #
# collector that fills them.                                                     #
# --------------------------------------------------------------------------- #
redis_up = registry.gauge(
    "redis_up",
    "1 when this process holds a live Redis connection, 0 otherwise. Zero with "
    "REDIS_URL set means the backend is running on its in-process fallback.",
)

# The single most useful Redis series to alert on. It is a leading indicator:
# the circuit opens *before* users notice, because the fallback keeps serving.
redis_circuit_state = registry.gauge(
    "redis_circuit_state",
    "Circuit breaker state: 0 closed (healthy), 1 half-open (probing recovery), "
    "2 open (degraded to in-process fallback).",
)

redis_pool_connections = registry.gauge(
    "redis_pool_connections",
    "Connection-pool occupancy by state (in_use / available / max). in_use "
    "approaching max means commands are queueing for a connection.",
    ("state",),
)

# `outcome` is bounded to four values (ok / error / connection_error /
# unavailable) and `operation` to the handful of verbs services/cache.py uses,
# so cardinality stays flat regardless of traffic. A key would go here in a
# careless implementation and make the series count unbounded.
redis_commands_total = registry.counter(
    "redis_commands_total",
    "Redis operations attempted, by operation and outcome. outcome=unavailable "
    "means the command was never sent (Redis unconfigured or circuit open).",
    ("operation", "outcome"),
)

redis_command_duration_seconds = registry.histogram(
    "redis_command_duration_seconds",
    "Redis command round-trip latency in seconds, by operation.",
    ("operation",),
    # Redis answers in tens of microseconds on a local network, so the default
    # HTTP buckets (which start at 5ms) would put every healthy command in the
    # first bucket and make the histogram useless. These start at 100µs, where
    # the interesting variation actually is.
    buckets=(0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.5),
)

redis_connection_errors_total = registry.counter(
    "redis_connection_errors_total",
    "Connection-level Redis failures (refused, reset, timed out, loading). "
    "Distinguished from command errors, which say nothing about the link.",
)

# -- Pub/Sub ----------------------------------------------------------------- #
# A steadily climbing reconnect count with no corresponding Redis restart is the
# signature of subscribers being dropped for slow consumption — see
# `client-output-buffer-limit pubsub` in docker/redis/redis.conf.
redis_pubsub_reconnects_total = registry.counter(
    "redis_pubsub_reconnects_total",
    "Times a Pub/Sub subscriber re-established its subscription after losing it.",
    ("channel",),
)

redis_pubsub_messages_total = registry.counter(
    "redis_pubsub_messages_total",
    "Pub/Sub messages by channel and disposition (received / published / "
    "dropped / handler_error).",
    ("channel", "disposition"),
)

# -- Server-reported (background sampler) ------------------------------------ #
redis_server_memory_used_bytes = registry.gauge(
    "redis_server_memory_used_bytes",
    "Redis server memory in use, as reported by INFO. Compare against "
    "redis_server_memory_max_bytes — the ratio is the eviction pressure.",
)
redis_server_memory_max_bytes = registry.gauge(
    "redis_server_memory_max_bytes",
    "Configured maxmemory in bytes (0 means unlimited, which for a cache is a "
    "pending OOM kill).",
)
redis_server_connected_clients = registry.gauge(
    "redis_server_connected_clients",
    "Clients currently connected to the Redis server, across all replicas.",
)
redis_server_evicted_keys_total = registry.gauge(
    "redis_server_evicted_keys_total",
    "Keys evicted by the maxmemory policy since server start. Steady growth is "
    "normal for an LRU cache at capacity; a sudden jump means the working set "
    "outgrew maxmemory.",
)
redis_server_expired_keys_total = registry.gauge(
    "redis_server_expired_keys_total",
    "Keys removed at TTL expiry since server start.",
)
redis_server_rejected_connections_total = registry.gauge(
    "redis_server_rejected_connections_total",
    "Connections refused because maxclients was reached. Any non-zero value is "
    "a connection leak or an unplanned scale-up.",
)


# --------------------------------------------------------------------------- #
# Subsystem failures (PH3.7)                                                    #
#                                                                               #
# THE KEYSTONE SERIES. Operator question #2 — "which subsystem is failing?" —    #
# is answerable from this one metric and nothing else. Everything below it in    #
# this file is detail you reach for *after* this has told you where to look.     #
#                                                                               #
# Both labels are closed vocabularies: `subsystem` is SUBSYSTEMS in              #
# observability.instruments, `error_class` is ERROR_CLASSES in                   #
# observability.errors. Neither can grow at runtime, which is what makes a       #
# metric that every failure path in the application writes to safe to have.      #
# --------------------------------------------------------------------------- #
subsystem_errors_total = registry.counter(
    "subsystem_errors_total",
    "Failures by subsystem and error class. The first metric to look at during "
    "an incident: it says which part of the system is broken and how.",
    ("subsystem", "error_class"),
)


# --------------------------------------------------------------------------- #
# Authentication (PH3.7)                                                        #
#                                                                               #
# Fed from `security.audit`, which is the one place every auth-relevant event    #
# already passes through — so this needs no new call sites and cannot drift out  #
# of step with the audit trail. The audit log answers "who did what"; this       #
# answers "how often, right now", which is the only one of the two you can put   #
# a threshold on.                                                                #
#                                                                               #
# `event` is the audit event constant (a fixed list of ~30 in                    #
# security/audit.py) and `outcome` is success/failure/info. No user id, email,   #
# IP or token ever becomes a label here — see the redaction rules in §Security   #
# of docs/architecture/OBSERVABILITY.md. Attribution is the audit log's job;     #
# it has access control, and a metrics endpoint does not.                        #
# --------------------------------------------------------------------------- #
auth_events_total = registry.counter(
    "auth_events_total",
    "Security-relevant authentication and session events, by event name and "
    "outcome. Sourced from the audit trail; carries no user identity.",
    ("event", "outcome"),
)


# --------------------------------------------------------------------------- #
# MongoDB (PH3.7)                                                               #
#                                                                               #
# Collected through pymongo's own command-monitoring hooks                       #
# (observability/mongo_monitor.py), not by wrapping call sites. There are        #
# several hundred `await db.<collection>.<op>()` calls in this backend; any      #
# scheme that requires touching them instruments whatever was written before it  #
# and nothing written after, and the first query someone adds during an incident #
# is invisible. The driver's listener sees every command by construction.        #
#                                                                               #
# Labelled by command name only (`find`, `update`, `aggregate`, …) — a fixed     #
# vocabulary of ~20 defined by the wire protocol. NOT by collection: 21          #
# collections today is bounded, but `db[name]` with a computed name is one       #
# refactor away from unbounded, and the collection is in the log line anyway.    #
# --------------------------------------------------------------------------- #
mongodb_commands_total = registry.counter(
    "mongodb_commands_total",
    "MongoDB commands completed, by command name and outcome (ok / error).",
    ("command", "outcome"),
)

mongodb_command_duration_seconds = registry.histogram(
    "mongodb_command_duration_seconds",
    "MongoDB command round-trip latency in seconds, by command name.",
    ("command",),
    # Mongo on a local network answers in single-digit milliseconds, so the
    # default HTTP buckets (first bound 5ms) would pile every healthy query into
    # bucket one. These start at 250µs. The 10s tail exists to catch a query
    # waiting on server selection during a failover.
    buckets=(0.00025, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 1.0, 5.0, 10.0),
)

mongodb_command_errors_total = registry.counter(
    "mongodb_command_errors_total",
    "MongoDB commands that failed, by command name and driver failure code. "
    "A rising count with mongodb_commands_total flat means the driver cannot "
    "reach a server at all.",
    ("command", "reason"),
)

# Pool saturation is the failure that looks like slowness: every command waits
# for a connection, latency climbs across the board, and nothing reports an
# error. `checked_out` approaching `max` is the tell.
mongodb_pool_connections = registry.gauge(
    "mongodb_pool_connections",
    "MongoDB connection-pool occupancy by state (checked_out / max).",
    ("state",),
)


# --------------------------------------------------------------------------- #
# WebSocket (PH3.7)                                                             #
#                                                                               #
# WHY COUNTERS PER FAN-OUT AND NOT PER SOCKET                                    #
#                                                                                #
# A broadcast to 500 sockets four times a second is 2,000 sends. Counting each   #
# one means 2,000 lock acquisitions per second on the realtime hot path to       #
# learn a number that `websocket_broadcasts_total * connections` already         #
# approximates. So a fan-out increments the broadcast counter ONCE, and adds     #
# failures in a single increment sized by the number of dead sockets. Two lock   #
# operations per broadcast regardless of audience size.                          #
#                                                                                #
# The gauges above (websocket_connections, websocket_tracked_users, …) remain    #
# the authority on *how many* connections exist; these counters cover the flow   #
# — arrivals, departures and delivery failures — which a gauge cannot show.      #
# --------------------------------------------------------------------------- #
websocket_connections_total = registry.counter(
    "websocket_connections_total",
    "WebSocket connection attempts by outcome (accepted / rejected).",
    ("outcome",),
)

websocket_disconnects_total = registry.counter(
    "websocket_disconnects_total",
    "WebSocket disconnections by reason (client = clean close, error = the "
    "socket raised, reaped = detected dead during a fan-out). A spike in "
    "reaped/error against a flat client count is a network or backpressure "
    "problem, not users navigating away.",
    ("reason",),
)

websocket_broadcasts_total = registry.counter(
    "websocket_broadcasts_total",
    "Fan-out operations performed, by kind (broadcast / channel / user). "
    "Counted once per fan-out, not once per recipient.",
    ("kind",),
)

websocket_send_failures_total = registry.counter(
    "websocket_send_failures_total",
    "Individual socket sends that raised during a fan-out, by kind. Incremented "
    "in one step per fan-out by the number of failures.",
    ("kind",),
)


# --------------------------------------------------------------------------- #
# Background tasks (PH3.7)                                                      #
#                                                                               #
# `background_tasks_running` (PH3.6, above) says how many are alive. It cannot   #
# distinguish "running healthily for six hours" from "crashed and respawned      #
# forty times", because both read as the same steady gauge value. These do.      #
#                                                                                #
# `task` is the supervised task's registered name — a fixed list declared at     #
# boot in server.py, and `infrastructure.tasks` refuses duplicates, so the label #
# space equals the number of loops the application has.                          #
# --------------------------------------------------------------------------- #
background_task_starts_total = registry.counter(
    "background_task_starts_total",
    "Supervised background tasks started, by name. In a healthy process this "
    "equals one per task for the life of the process; any increase after boot "
    "means something restarted.",
    ("task",),
)

background_task_terminations_total = registry.counter(
    "background_task_terminations_total",
    "Supervised background tasks that stopped, by name and outcome "
    "(completed / failed / cancelled). `failed` on a perpetual loop is always "
    "a defect — the loop's own try/except should have contained it.",
    ("task", "outcome"),
)

background_task_duration_seconds = registry.histogram(
    "background_task_duration_seconds",
    "Lifetime of a supervised background task, in seconds, by name.",
    ("task",),
    # Nothing like the HTTP buckets: these tasks are perpetual loops that should
    # live as long as the process. The dense end is deliberately at the SHORT
    # side — a loop that exits after 2s is the crash-loop signature, and that is
    # the only part of this distribution anyone will ever look at.
    buckets=(1.0, 5.0, 30.0, 120.0, 600.0, 3600.0, 21600.0, 86400.0),
)


# --------------------------------------------------------------------------- #
# Scheduler (PH3.7)                                                             #
#                                                                               #
# APScheduler owns the cron work (morning analysis, market scanner, trade        #
# monitor, exit reminder, EOD report, portfolio snapshot); `background_task_*`   #
# above covers the perpetual loops and does NOT see any of it. Both are          #
# "background", and conflating them would hide the failure mode that only cron   #
# has: **a job that does not run at all**. A perpetual loop announces its own    #
# death by stopping; a scheduled job that never fires produces no event, no      #
# error and no gap anyone notices until a user asks where their morning report   #
# went.                                                                          #
#                                                                                #
# `missed` is therefore the outcome worth watching most. APScheduler emits it    #
# when a run is skipped because its misfire grace period elapsed — the           #
# signature of an event loop that was blocked, or of a previous run of the same  #
# job still executing. `trade_monitor` runs every 60s during market hours, so    #
# missed runs there mean live positions went unchecked.                          #
#                                                                                #
# `job` is the registered job id — six string literals in                        #
# services/scheduler.py, so the label space is the number of cron jobs.          #
# --------------------------------------------------------------------------- #
scheduler_job_runs_total = registry.counter(
    "scheduler_job_runs_total",
    "Scheduled job executions by job id and outcome (executed / error / "
    "missed). `missed` means the run was skipped entirely — the failure a "
    "success-rate alert on the other two cannot see.",
    ("job", "outcome"),
)

scheduler_job_duration_seconds = registry.histogram(
    "scheduler_job_duration_seconds",
    "Scheduled job execution time in seconds, by job id.",
    ("job",),
    # These jobs fan out across the whole user base and call providers and the
    # AI, so seconds-to-minutes is normal and the useful signal is the tail. A
    # job approaching its own interval (trade_monitor at 60s) is about to start
    # missing runs, which is why the buckets straddle 60.
    buckets=(0.1, 0.5, 1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)


# --------------------------------------------------------------------------- #
# External providers (PH3.7)                                                    #
#                                                                               #
# Market data, brokers, news and notification delivery. `provider` is a logical  #
# name from a fixed table (observability.instruments.PROVIDERS), never a         #
# hostname or URL, and `operation` is the gateway verb (`get_quote`,             #
# `get_indices`, …) rather than a path — so a symbol, an order id or a query     #
# string can never reach a label.                                                #
#                                                                                #
# `outcome` is deliberately three-valued: ok / error / empty. "Empty" is the     #
# failure mode a market-data feed actually has — it answers 200 with no rows,    #
# every dashboard stays green, and the product silently shows yesterday's        #
# prices. An error-rate alert cannot see that; this can.                          #
# --------------------------------------------------------------------------- #
provider_requests_total = registry.counter(
    "provider_requests_total",
    "Outbound requests to an external provider, by provider, operation and "
    "outcome (ok / empty / error). `empty` means the call succeeded and "
    "returned nothing usable — the failure mode a status-code check misses.",
    ("provider", "operation", "outcome"),
)

provider_request_duration_seconds = registry.histogram(
    "provider_request_duration_seconds",
    "External provider call latency in seconds, by provider and operation.",
    ("provider", "operation"),
    # Third parties are slow and variable; the interesting region is 100ms–10s,
    # and the 30s bucket catches calls sitting on a connect timeout.
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0),
)

provider_errors_total = registry.counter(
    "provider_errors_total",
    "External provider failures, by provider and error class.",
    ("provider", "error_class"),
)


# --------------------------------------------------------------------------- #
# AI providers (PH3.7)                                                          #
#                                                                               #
# Split from the provider family above because the questions differ: an AI call  #
# costs money, takes seconds rather than milliseconds, and degrades to a         #
# simulated response instead of an error — so `ai_requests_total{provider=       #
# "simulated"}` climbing is the real alert, and it is not an error at all.       #
#                                                                                #
# NO MODEL LABEL, DELIBERATELY. A model id is read from configuration, so it is  #
# an operator-supplied string and therefore unbounded in principle; it is also   #
# the field most likely to churn (every model release). It is recorded on the    #
# structured log line instead, where cardinality costs nothing.                  #
#                                                                                #
# NOTHING FROM THE PROMPT OR THE RESPONSE IS RECORDED HERE — not the text, not   #
# a hash of it, not a token count broken down by user. Prompts carry portfolio   #
# positions and personal financial context.                                      #
# --------------------------------------------------------------------------- #
ai_requests_total = registry.counter(
    "ai_requests_total",
    "AI model requests by provider and outcome (ok / error / unconfigured). "
    "Growth in provider=\"simulated\" means real AI is unavailable and users "
    "are receiving canned responses while every other metric looks healthy.",
    ("provider", "outcome"),
)

ai_request_duration_seconds = registry.histogram(
    "ai_request_duration_seconds",
    "AI model request latency in seconds, by provider.",
    ("provider",),
    # Seconds to tens of seconds. Starting at 0.25s because nothing real is
    # faster, and running to 120s because that is where a stalled stream sits.
    buckets=(0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0),
)

ai_request_errors_total = registry.counter(
    "ai_request_errors_total",
    "AI model request failures, by provider and error class.",
    ("provider", "error_class"),
)


# --------------------------------------------------------------------------- #
# In-process event bus (PH3.7)                                                  #
#                                                                               #
# `event_bus_subscribers` (PH3.6) bounds the handler registry. These cover       #
# delivery: an event published whose handler raised is a silently dropped        #
# domain action — a portfolio that never resynced, a notification never sent —   #
# and before this it produced one log line and no signal.                        #
#                                                                                #
# `event_type` is the dotted literal from the publish call site (17 of them,     #
# all string constants in source). A symbol or user id would be a cardinality    #
# bug and is why the label is the type, never the payload.                       #
# --------------------------------------------------------------------------- #
event_bus_events_total = registry.counter(
    "event_bus_events_total",
    "Events published on the in-process market event bus, by type.",
    ("event_type",),
)

event_bus_handler_failures_total = registry.counter(
    "event_bus_handler_failures_total",
    "Event-bus handler invocations that raised, by event type. Each one is a "
    "domain action that silently did not happen.",
    ("event_type",),
)


# --------------------------------------------------------------------------- #
# Frontend runtime failures (PH3.7)                                             #
#                                                                               #
# A React crash, an unhandled promise rejection or a failed chunk load produces  #
# NOTHING on the server: the request that served the bundle returned 200, no     #
# exception was raised, no log line was written, and the user is looking at a    #
# blank page. This family is the only server-side evidence that any of it        #
# happened, fed by `POST /api/observability/client-errors`.                      #
#                                                                               #
# `kind` is a closed vocabulary validated at the endpoint (render / unhandled_   #
# rejection / uncaught / chunk_load / api / websocket). The error MESSAGE is     #
# never a label — it is attacker-controlled text arriving on an unauthenticated  #
# endpoint, which makes it the most dangerous possible label value in this       #
# entire file.                                                                   #
# --------------------------------------------------------------------------- #
frontend_errors_total = registry.counter(
    "frontend_errors_total",
    "Client-side runtime failures reported by the browser, by kind. "
    "chunk_load rising sharply after a deploy means users are holding stale "
    "index.html against purged bundles.",
    ("kind",),
)

frontend_reports_rejected_total = registry.counter(
    "frontend_reports_rejected_total",
    "Client error reports refused for failing validation, by reason. Non-zero "
    "means either a client bug or someone probing the ingest endpoint.",
    ("reason",),
)


# --------------------------------------------------------------------------- #
# Instrumentation helpers — the API the middleware actually calls               #
# --------------------------------------------------------------------------- #
def record_request(method: str, route: str, status: int, duration_seconds: float) -> None:
    """Record one completed HTTP request across all four signals.

    One function so a call site cannot update three of the four metrics and
    forget the fourth, leaving dashboards subtly inconsistent. Never raises:
    wrapped whole, because it is called from a `finally` block on the request
    path and an exception here would replace a real response with a 500.
    """
    try:
        status_str = str(status)
        http_requests_total.inc(labels=(method, route, status_str))
        http_request_duration_seconds.observe(duration_seconds, labels=(method, route))
        if 400 <= status < 500:
            http_request_errors_total.inc(labels=(method, route, "client"))
        elif status >= 500:
            http_request_errors_total.inc(labels=(method, route, "server"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("failed to record request metrics: %s", exc)


def record_exception(method: str, route: str) -> None:
    """Record a request whose handler raised without producing a response."""
    try:
        http_request_errors_total.inc(labels=(method, route, "exception"))
    except Exception:  # pragma: no cover - defensive
        pass


def record_dependency_health(name: str, healthy: Optional[bool], duration_seconds: float) -> None:
    """Publish the outcome of one dependency probe.

    ``healthy=None`` means "not configured" and maps to -1 rather than 0: a
    Redis that was never configured is not the same condition as a Redis that is
    down, and an alert rule must be able to tell them apart.
    """
    try:
        value = -1.0 if healthy is None else (1.0 if healthy else 0.0)
        dependency_up.set(value, labels=(name,))
        health_check_duration_seconds.observe(duration_seconds, labels=(name,))
    except Exception:  # pragma: no cover - defensive
        pass


def set_app_info(version: str, environment: str, python_version: str, revision: str) -> None:
    """Publish build metadata as the conventional `_info` gauge (value 1).

    The idiom exists because labels cannot be attached to a scrape as a whole:
    a constant-1 gauge carrying them lets a query join runtime metadata onto any
    other series (e.g. "p99 latency, by deployed version").
    """
    try:
        app_info.set(1.0, labels=(version, environment, python_version, revision))
    except Exception:  # pragma: no cover - defensive
        pass


def reset_for_tests() -> None:
    """Zero every metric. Test-support only — never call from application code.

    Exists so metric assertions are order-independent; without it, one test's
    requests inflate the next test's counters and the suite fails only when run
    in a different order.
    """
    for metric in registry.metrics():
        with metric._lock:  # noqa: SLF001 - deliberate test-support access
            metric._dropped = 0
            if isinstance(metric, (Counter, Gauge)):
                metric._values.clear()
            elif isinstance(metric, Histogram):
                metric._counts.clear()
                metric._sums.clear()
                metric._totals.clear()

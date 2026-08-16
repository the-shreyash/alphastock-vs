"""Subsystem instrumentation — the API call sites actually use (PH3.7).

WHY A LAYER BETWEEN CALL SITES AND `metrics`
--------------------------------------------
`observability.metrics` declares *what* series exist. Nothing stops a call site
importing it and writing to them directly, and for the four HTTP signals that is
exactly what the middleware does. But once instrumentation spreads past one
module, direct access has three failure modes that all end the same way — a
dashboard that is quietly wrong:

1. **Partial updates.** A provider call has a counter, a histogram and an error
   counter. A call site that increments two of the three produces a panel where
   "requests" and "latency observations" disagree, and nobody can tell which
   number to believe. Here, one `with` block updates all of them or none.
2. **Invented labels.** `provider="yahoo_finance"` in one file and
   `provider="yahoo"` in another are two series, two graph lines and one alert
   rule that matches half the traffic. This module holds the closed vocabularies
   (:data:`SUBSYSTEMS`, :data:`PROVIDERS`) and refuses anything outside them.
3. **Instrumentation that raises.** A metrics bug that escapes into a trading
   endpoint is strictly worse than no metrics (design rule 1 in
   `observability/__init__.py`). Every entry point here is wrapped whole, once,
   rather than relying on every call site to remember a try/except.

THE SHAPE OF THE API
--------------------
Two forms, chosen by what the call site can offer:

* **Context managers** (:class:`track_provider`, :class:`track_ai`) where the
  work is a single block. They time it, classify any exception, record the
  outcome, and re-raise untouched — instrumentation observes control flow, it
  never alters it.
* **`record_*` functions** where the event is a point in time (an auth outcome,
  a socket disconnect, a task start) or where the surrounding code already has
  its own error handling that must not be disturbed.

WHY A SYNCHRONOUS CONTEXT MANAGER AROUND `await`
------------------------------------------------
``with track_provider(...): await fetch()`` is correct and is the intended usage.
The manager is entered and exited on the same task, around the await, so it
measures wall-clock time including every suspension — which is the number that
matters for an outbound call. An async context manager would add a coroutine
frame per call to measure the same thing. If the awaited work is cancelled, the
``CancelledError`` passes through ``__exit__`` and classifies as
:data:`errors.CANCELLED`, which is recorded as neither a success nor a failure:
a shutdown cancelling twelve in-flight provider calls must not read as an
incident.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from observability import errors, metrics

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Closed vocabularies                                                           #
#                                                                               #
# These are the `subsystem` and `provider` label spaces. They are frozen sets    #
# rather than conventions because a label space that is only documented is a     #
# label space that grows. Adding a member is a deliberate edit here, reviewed    #
# alongside whatever alert rules reference it.                                   #
# --------------------------------------------------------------------------- #

#: Answers "which part of the system is failing?". Deliberately coarse — this is
#: the top level of a diagnosis, not the whole of it. A dozen buckets is a
#: readable dashboard legend; forty is a wall of noise nobody reads twice.
SUBSYSTEMS = frozenset({
    "http",             # the request pipeline itself
    "auth",             # authentication, sessions, tokens
    "database",         # MongoDB
    "cache",            # Redis and the in-process fallback
    "websocket",        # the realtime tier
    "background_task",  # supervised perpetual loops
    "scheduler",        # APScheduler cron jobs
    "event_bus",        # in-process domain event fan-out
    "market_data",      # market data providers, via the gateway
    "broker",           # broker APIs
    "news",             # news providers
    "notification",     # email / WhatsApp / Telegram delivery
    "ai",               # model providers
    "config",           # configuration faults
})

#: Logical provider names. A *logical* name, never a hostname, base URL or
#: SDK class: those change with a config edit and would silently fork a series.
PROVIDERS = frozenset({
    "market_data",
    "broker_zerodha",
    "broker_upstox",
    "broker_crypto",
    "news",
    "email",
    "whatsapp",
    "telegram",
})

#: AI provider names, matching `AIProvider.name` in services/ai_provider.py.
AI_PROVIDERS = frozenset({"claude", "gemini", "simulated"})

#: Fallback label for a value outside its vocabulary. Distinct from the metrics
#: module's `<overflow>` (a ceiling breach) — this one means a call site passed
#: something unregistered, which is a code defect, not a volume problem.
UNKNOWN = "<unknown>"

# Outcome vocabularies, kept here so a typo is a NameError at import rather
# than a new series at runtime.
OK = "ok"
ERROR = "error"
EMPTY = "empty"
UNCONFIGURED = "unconfigured"

# --------------------------------------------------------------------------- #
# Closed label sets for the point-in-time recorders                             #
#                                                                               #
# `track_provider` and `track_ai` validate their provider against a frozen set   #
# already. The `record_*` functions below take their labels as plain strings,    #
# and every one of those strings is a source literal at the call site today —    #
# but "today" is not a guarantee, and the one thing a metric label may never be  #
# is a value that arrived from outside the process. Validating here means a      #
# future call site that passes a variable produces a loud log line and one       #
# shared bucket, instead of a new series per value.                              #
#                                                                               #
# Not applied to `event`, `task` and `event_type`: those are genuinely open      #
# sets that grow with the application (a new audit event, a new loop, a new      #
# domain event), bounded upstream by their own registries — `security.audit`'s   #
# `_EVENT_REGISTRY` folds an unregistered event into one bucket before it        #
# reaches here — and backstopped by MAX_SERIES_PER_METRIC. Freezing them here    #
# would mean a new background loop is invisible until someone edits this file,   #
# which trades a real cardinality risk for a certain observability gap.          #
# --------------------------------------------------------------------------- #
AUTH_OUTCOMES = frozenset({"success", "failure", "info"})
WS_CONNECTION_OUTCOMES = frozenset({"accepted", "rejected"})
WS_DISCONNECT_REASONS = frozenset({"client", "error", "reaped"})
WS_FANOUT_KINDS = frozenset({"broadcast", "channel", "user"})
TASK_OUTCOMES = frozenset({"completed", "failed", "cancelled"})


def _bounded(value: str, allowed: frozenset, what: str) -> str:
    """``value`` if it is in ``allowed``, else :data:`UNKNOWN` and a loud log."""
    if value in allowed:
        return value
    logger.error(
        "unregistered %s label %r — recording as %s", what, value, UNKNOWN,
        extra={"event": "instrumentation_defect", "label": what},
    )
    return UNKNOWN


def _subsystem(name: str) -> str:
    if name in SUBSYSTEMS:
        return name
    logger.error(
        "unregistered subsystem label %r — recording as %s", name, UNKNOWN,
        extra={"event": "instrumentation_defect", "subsystem": name},
    )
    return UNKNOWN


def _provider(name: str, allowed: frozenset) -> str:
    if name in allowed:
        return name
    logger.error(
        "unregistered provider label %r — recording as %s", name, UNKNOWN,
        extra={"event": "instrumentation_defect", "provider": name},
    )
    return UNKNOWN


# --------------------------------------------------------------------------- #
# The keystone: subsystem failures                                              #
# --------------------------------------------------------------------------- #
def record_error(subsystem: str, error_class: str) -> None:
    """Record one failure against a subsystem, by class.

    The lowest-level entry point; everything else in this module funnels here so
    that `subsystem_errors_total` is complete by construction rather than by
    every author remembering to also increment it.
    """
    try:
        cls = error_class if errors.is_error_class(error_class) else errors.INTERNAL
        metrics.subsystem_errors_total.inc(labels=(_subsystem(subsystem), cls))
    except Exception:  # pragma: no cover - defensive
        pass


def record_exception(subsystem: str, exc: BaseException) -> str:
    """Classify ``exc``, record it against ``subsystem``, return the class.

    Returns the class so the caller can put it on its log line without
    classifying twice — the log field and the metric label are then guaranteed
    to agree, which is the whole point of having both.

    Cancellation is classified and returned but **not counted**: a cancelled
    operation is a shutdown or a client that went away, and counting it would
    make every deploy look like an error spike.
    """
    try:
        cls = errors.classify_exception(exc)
        if cls != errors.CANCELLED:
            record_error(subsystem, cls)
        return cls
    except Exception:  # pragma: no cover - defensive
        return errors.INTERNAL


# --------------------------------------------------------------------------- #
# Authentication                                                                #
# --------------------------------------------------------------------------- #
def record_auth_event(event: str, outcome: str) -> None:
    """Count one authentication/session event.

    Called from `security.audit.emit`, which every auth-relevant code path
    already goes through — so this cannot drift out of step with the audit
    trail, and adding an audit event automatically adds it to the metric.

    ``event`` is an audit event constant; the audit module's registry bounds the
    set. A failure outcome also lands on `subsystem_errors_total{subsystem=
    "auth"}` so the keystone metric stays complete.
    """
    try:
        resolved = _bounded(outcome, AUTH_OUTCOMES, "auth outcome")
        metrics.auth_events_total.inc(labels=(event, resolved))
        if resolved == "failure":
            record_error("auth", errors.AUTHENTICATION)
    except Exception:  # pragma: no cover - defensive
        pass


# --------------------------------------------------------------------------- #
# WebSocket                                                                     #
# --------------------------------------------------------------------------- #
def record_ws_connection(outcome: str) -> None:
    """Count a connection attempt. ``outcome`` is "accepted" or "rejected"."""
    try:
        resolved = _bounded(outcome, WS_CONNECTION_OUTCOMES, "websocket outcome")
        metrics.websocket_connections_total.inc(labels=(resolved,))
        if resolved == "rejected":
            record_error("websocket", errors.AUTHENTICATION)
    except Exception:  # pragma: no cover - defensive
        pass


def record_ws_disconnect(reason: str) -> None:
    """Count a disconnection. ``reason`` is "client", "error" or "reaped"."""
    try:
        metrics.websocket_disconnects_total.inc(
            labels=(_bounded(reason, WS_DISCONNECT_REASONS, "websocket disconnect reason"),)
        )
    except Exception:  # pragma: no cover - defensive
        pass


def record_ws_fanout(kind: str, failures: int = 0) -> None:
    """Record one fan-out and its failures in two increments, whatever the size.

    See the cost note on `websocket_broadcasts_total`: counting per recipient
    would put thousands of lock acquisitions per second on the realtime path to
    learn something the connection gauge already implies.

    ``failures`` is added in a single sized increment; each failed send also
    means a dead socket, which is recorded as a `reaped` disconnect so the
    connect/disconnect ledger balances.
    """
    try:
        resolved = _bounded(kind, WS_FANOUT_KINDS, "websocket fanout kind")
        metrics.websocket_broadcasts_total.inc(labels=(resolved,))
        if failures > 0:
            metrics.websocket_send_failures_total.inc(float(failures), labels=(resolved,))
            metrics.websocket_disconnects_total.inc(float(failures), labels=("reaped",))
            record_error("websocket", errors.INTERNAL)
    except Exception:  # pragma: no cover - defensive
        pass


# --------------------------------------------------------------------------- #
# Background tasks                                                              #
# --------------------------------------------------------------------------- #
def record_task_start(task: str) -> None:
    try:
        metrics.background_task_starts_total.inc(labels=(task,))
    except Exception:  # pragma: no cover - defensive
        pass


def record_task_end(task: str, outcome: str, duration_seconds: Optional[float] = None) -> None:
    """Record a supervised task stopping.

    ``outcome`` is "completed", "failed" or "cancelled". Only "failed" reaches
    `subsystem_errors_total` — a cancelled task is a clean shutdown, and a
    completed one is a task that was never perpetual to begin with.
    """
    try:
        resolved = _bounded(outcome, TASK_OUTCOMES, "background task outcome")
        metrics.background_task_terminations_total.inc(labels=(task, resolved))
        if duration_seconds is not None:
            metrics.background_task_duration_seconds.observe(
                max(0.0, duration_seconds), labels=(task,)
            )
        if resolved == "failed":
            record_error("background_task", errors.INTERNAL)
    except Exception:  # pragma: no cover - defensive
        pass


# --------------------------------------------------------------------------- #
# Scheduler (APScheduler cron jobs)                                             #
# --------------------------------------------------------------------------- #
SCHEDULER_OUTCOMES = frozenset({"executed", "error", "missed"})


def record_scheduler_run(job: str, outcome: str,
                         duration_seconds: Optional[float] = None) -> None:
    """Record one scheduled-job run.

    ``missed`` records no duration — nothing ran, so there is nothing to time,
    and observing a zero would pull the job's latency distribution toward zero
    exactly when it is being skipped for taking too long.
    """
    try:
        resolved = _bounded(outcome, SCHEDULER_OUTCOMES, "scheduler outcome")
        metrics.scheduler_job_runs_total.inc(labels=(job, resolved))
        if duration_seconds is not None and resolved != "missed":
            metrics.scheduler_job_duration_seconds.observe(
                max(0.0, duration_seconds), labels=(job,)
            )
        if resolved in ("error", "missed"):
            record_error("scheduler", errors.INTERNAL)
    except Exception:  # pragma: no cover - defensive
        pass


# --------------------------------------------------------------------------- #
# Event bus                                                                     #
# --------------------------------------------------------------------------- #
def record_event_published(event_type: str, handler_failures: int = 0) -> None:
    try:
        metrics.event_bus_events_total.inc(labels=(event_type,))
        if handler_failures > 0:
            metrics.event_bus_handler_failures_total.inc(
                float(handler_failures), labels=(event_type,)
            )
            record_error("event_bus", errors.INTERNAL)
    except Exception:  # pragma: no cover - defensive
        pass


# --------------------------------------------------------------------------- #
# MongoDB (fed by observability.mongo_monitor)                                  #
# --------------------------------------------------------------------------- #
def record_mongo_command(command: str, duration_seconds: float, *, ok: bool,
                         reason: str = "") -> None:
    """Record one completed MongoDB command.

    ``reason`` is the driver's failure code name for a failed command — a small
    fixed vocabulary from the wire protocol, never a server message (which can
    embed a collection name, a document, or the connection URI).
    """
    try:
        outcome = OK if ok else ERROR
        metrics.mongodb_commands_total.inc(labels=(command, outcome))
        metrics.mongodb_command_duration_seconds.observe(
            max(0.0, duration_seconds), labels=(command,)
        )
        if not ok:
            metrics.mongodb_command_errors_total.inc(labels=(command, reason or UNKNOWN))
            record_error("database", errors.DATABASE)
    except Exception:  # pragma: no cover - defensive
        pass


def record_mongo_pool(checked_out: int, max_size: int) -> None:
    try:
        metrics.mongodb_pool_connections.set(float(checked_out), labels=("checked_out",))
        metrics.mongodb_pool_connections.set(float(max_size), labels=("max",))
    except Exception:  # pragma: no cover - defensive
        pass


# --------------------------------------------------------------------------- #
# External providers                                                            #
# --------------------------------------------------------------------------- #
class track_provider:
    """Time and classify one outbound call to an external provider.

    ::

        with instruments.track_provider("market_data", "get_quote") as call:
            raw = await fetch_real_stock_quote(symbol)
            if not raw:
                call.empty()

    Records latency and an outcome of ok / empty / error, plus the error class
    on failure. Exceptions propagate untouched.

    ``subsystem`` defaults to the provider's own family so that a market-data
    outage lands on `subsystem_errors_total{subsystem="market_data"}` without
    the call site restating it.
    """

    __slots__ = ("provider", "operation", "subsystem", "_started", "_empty", "_outcome")

    #: Which subsystem bucket each provider's failures belong to.
    _SUBSYSTEM_OF = {
        "market_data": "market_data",
        "broker_zerodha": "broker",
        "broker_upstox": "broker",
        "broker_crypto": "broker",
        "news": "news",
        "email": "notification",
        "whatsapp": "notification",
        "telegram": "notification",
    }

    def __init__(self, provider: str, operation: str, *, subsystem: Optional[str] = None):
        self.provider = _provider(provider, PROVIDERS)
        self.operation = operation
        self.subsystem = subsystem or self._SUBSYSTEM_OF.get(self.provider, "market_data")
        self._started = 0.0
        self._empty = False
        self._outcome: Optional[str] = None

    def empty(self) -> None:
        """Mark the call as having succeeded but returned nothing usable.

        The market-data failure that no status code shows: a 200 with an empty
        body leaves every error-rate panel green while the product serves stale
        prices. Alert on `provider_requests_total{outcome="empty"}` rising as a
        share of the total, not on its absolute value — some operations are
        legitimately empty outside market hours.
        """
        self._empty = True

    def __enter__(self) -> "track_provider":
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = time.perf_counter() - self._started
        try:
            if exc is not None:
                cls = errors.classify_exception(exc)
                if cls == errors.CANCELLED:
                    # Neither success nor failure: a cancelled outbound call is a
                    # shutdown or a client that hung up. Latency is still recorded
                    # (the time was really spent) but no outcome is claimed.
                    metrics.provider_request_duration_seconds.observe(
                        duration, labels=(self.provider, self.operation)
                    )
                    return False
                self._outcome = ERROR
                metrics.provider_errors_total.inc(labels=(self.provider, cls))
                record_error(self.subsystem, cls)
            else:
                self._outcome = EMPTY if self._empty else OK
            metrics.provider_requests_total.inc(
                labels=(self.provider, self.operation, self._outcome)
            )
            metrics.provider_request_duration_seconds.observe(
                duration, labels=(self.provider, self.operation)
            )
        except Exception:  # pragma: no cover - defensive
            pass
        # Always False: never suppress. Instrumentation that swallows an
        # exception turns a visible failure into a wrong answer.
        return False


def record_provider_failure(provider: str, operation: str, exc: BaseException) -> str:
    """Record a provider failure the caller already caught.

    For the call sites that cannot use :class:`track_provider` because their
    ``try/except`` predates it and returns a fallback value rather than
    re-raising. Returns the error class for the log line.
    """
    name = _provider(provider, PROVIDERS)
    subsystem = track_provider._SUBSYSTEM_OF.get(name, "market_data")
    try:
        cls = errors.classify_exception(exc)
        metrics.provider_requests_total.inc(labels=(name, operation, ERROR))
        metrics.provider_errors_total.inc(labels=(name, cls))
        record_error(subsystem, cls)
        return cls
    except Exception:  # pragma: no cover - defensive
        return errors.INTERNAL


# --------------------------------------------------------------------------- #
# AI providers                                                                  #
# --------------------------------------------------------------------------- #
class track_ai:
    """Time and classify one AI model request.

    ::

        with instruments.track_ai("claude") as call:
            response = await client.messages.create(...)
            if response.error:
                call.failed(response.error)

    Two ways to report a failure, because AI providers have both: an exception
    from the SDK, and a well-formed response object carrying an error string.
    The second is the common one in this codebase — every provider catches
    broadly and returns `AIResponse(error=...)` so a model outage degrades the
    feature instead of failing the request. Without :meth:`failed`, that entire
    class of failure would record as a success.

    :meth:`unconfigured` records the third state: no API key, so no call was
    made. Not an error — but `ai_requests_total{outcome="unconfigured"}`
    climbing means users are getting simulated answers, which is the condition
    everything else looks healthy during.
    """

    __slots__ = ("provider", "_started", "_state", "_exc_class")

    def __init__(self, provider: str):
        self.provider = _provider(provider, AI_PROVIDERS)
        self._started = 0.0
        self._state: Optional[str] = None
        self._exc_class = errors.AI_PROVIDER

    def failed(self, detail: object = None) -> None:
        """Mark a returned-but-failed response. ``detail`` is used only to
        classify — it is never recorded, because a provider error string can
        contain a request id, an account identifier or an echoed prompt."""
        self._state = ERROR
        if isinstance(detail, BaseException):
            self._exc_class = errors.classify_exception(detail, default=errors.AI_PROVIDER)
        elif isinstance(detail, str):
            self._exc_class = _classify_ai_error_text(detail)

    def unconfigured(self) -> None:
        self._state = UNCONFIGURED

    def __enter__(self) -> "track_ai":
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = time.perf_counter() - self._started
        try:
            if exc is not None:
                cls = errors.classify_exception(exc, default=errors.AI_PROVIDER)
                if cls == errors.CANCELLED:
                    return False
                self._state, self._exc_class = ERROR, cls
            outcome = self._state or OK
            metrics.ai_requests_total.inc(labels=(self.provider, outcome))
            # Unconfigured means no call was made, so there is no latency to
            # record — observing a ~0s "request" would drag the p50 of a
            # partially-configured deployment toward zero and hide real latency.
            if outcome != UNCONFIGURED:
                metrics.ai_request_duration_seconds.observe(duration, labels=(self.provider,))
            if outcome == ERROR:
                metrics.ai_request_errors_total.inc(labels=(self.provider, self._exc_class))
                record_error("ai", self._exc_class)
        except Exception:  # pragma: no cover - defensive
            pass
        return False


# Substring probes for classifying a provider's error *string*. Crude by
# necessity — the SDK already flattened its exception into text by the time this
# runs — but it only ever picks between members of the closed vocabulary, so the
# worst case is a failure filed under the generic AI_PROVIDER class. The strings
# themselves are never recorded anywhere.
_AI_ERROR_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("rate limit", "rate_limit", "429", "quota", "resource_exhausted"), errors.RATE_LIMIT),
    (("authentication", "unauthorized", "401", "invalid api key", "missing_api_key",
      "permission_denied", "api key"), errors.CONFIGURATION),
    (("timeout", "timed out", "deadline"), errors.TIMEOUT),
    (("overloaded", "529", "503", "unavailable"), errors.UNAVAILABLE),
)


def _classify_ai_error_text(text: str) -> str:
    lowered = (text or "").lower()
    for needles, cls in _AI_ERROR_HINTS:
        if any(needle in lowered for needle in needles):
            return cls
    return errors.AI_PROVIDER

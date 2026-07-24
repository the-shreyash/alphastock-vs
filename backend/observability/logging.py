"""Structured logging (PH2.5).

WHY THIS MODULE EXISTS
----------------------
Before this sprint the entire backend's logging configuration was one line:

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

Three problems, in ascending order of severity.

**It produced prose, not data.** `2026-07-22 09:41:07 - server - ERROR - trade
exit failed for 68f2a1... : timeout` is readable by a human staring at one
terminal, and almost useless at scale. "How many 5xx did `/api/trades/{id}` serve
in the last hour?" becomes a regex against free text — a regex that breaks the
next time someone rewords the message. Structured logs make each field a real
field, so the same question is a filter: `level=ERROR AND route="/api/trades/{trade_id}"`.

**It ran too late.** The call sat at line 5392 of `server.py`, *after* every
import — so roughly forty modules that log at import time wrote through the
default root handler with a different format, and the boot sequence (the part
you most want to read when a container will not start) came out unstructured.

**Nothing correlated.** No request ID meant no way to gather the lines belonging
to one request, which is the single most useful operation in a log system.

WHY JSON, AND WHY TO STDOUT ONLY
--------------------------------
JSON because every log platform — CloudWatch, Loki, Elasticsearch, Datadog —
parses it natively with no fragile grok pattern in between. Stdout because that
is the twelve-factor contract: a containerised process should never manage log
files, rotation or shipping. It writes to stdout, the container runtime captures
it, and the platform ships it. That remains the default and the recommended
path, and it is the seam a collector attaches to with no application change.

PH2.6 added optional, opt-in **file sinks** on top of that — split by stream,
size-rotated, compressed and retention-bounded — for the deployments that have
no collector yet or a compliance rule that requires durable local retention.
They are an addition, never a replacement: stdout is unconditional, so
`docker logs` and any attached collector behave exactly as before. The
machinery lives in `observability.log_streams` and `observability.log_rotation`;
it is installed from `configure_logging` below so that this module remains the
one place logging is configured. See `docs/operations/LOGGING.md`.

Text output remains the default outside production, because a developer reading
a terminal wants a line, not a JSON object.

WHAT IS DELIBERATELY *NOT* LOGGED
---------------------------------
* **Query strings.** Nothing else in an HTTP request is as likely to carry a
  credential: OAuth `code`/`state`, a password-reset token, a shared link key.
  The access log records the route template and the path with the query string
  already stripped.
* **Request and response bodies.** They contain passwords on `/api/auth/login`,
  and personal and financial data everywhere else.
* **Headers.** `Authorization` and `Cookie` are credentials verbatim.
* **Anything whose field name looks sensitive**, via the same redactor
  `security.audit` uses (`security.audit.redact_fields`) — one list, so a marker
  added for a new credential type protects both the audit log and this one.

Message-body scrubbing is a *defence in depth* on top of that, not a substitute:
a 12,000-line codebase contains a lot of pre-existing f-string log calls, and
`LOG_SCRUB_MESSAGES` catches the `token=...`-shaped mistakes that structured
fields cannot.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from observability import context, log_streams, runtime

# --------------------------------------------------------------------------- #
# Configuration                                                                 #
# --------------------------------------------------------------------------- #
LOG_LEVEL_ENV = "LOG_LEVEL"
LOG_FORMAT_ENV = "LOG_FORMAT"          # json | text
LOG_SCRUB_ENV = "LOG_SCRUB_MESSAGES"   # 1 (default) | 0
LOG_HEALTH_ENV = "LOG_HEALTH_REQUESTS"  # 0 (default) | 1

FORMAT_JSON = "json"
FORMAT_TEXT = "text"

# The access-log logger. A dedicated name so a platform can route, sample or
# retain request logs separately from application logs — they have very
# different volume and very different value per line.
ACCESS_LOGGER_NAME = "stockassist.access"

# `logging.LogRecord` attributes that are part of the record machinery rather
# than caller-supplied data. Anything on a record that is NOT in this set arrived
# via `logger.info(..., extra={...})` and is therefore a structured field the
# caller wants emitted — which is how this formatter supports arbitrary fields
# without every call site needing to know about it.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)

# Defensive scrub for credentials embedded in a free-text message. Matches
# `key=value`, `key: value` and `key="value"` for a small set of unambiguous key
# names, and replaces the VALUE only — the key is left visible so the log still
# says what kind of thing was redacted. Bounded and anchored on a word boundary
# so it cannot run away on a long line.
#
# The `(?:bearer|basic|token)\s+` value alternative is not decoration: without
# it, `Authorization: Bearer eyJhbGci...` matches with `Bearer` as the *value*,
# the scheme word gets redacted, and the actual credential is left in the log —
# a scrubber that reports success while leaking is worse than none at all.
#
# An explicit `=` or `:` separator is REQUIRED. An earlier revision also accepted
# bare whitespace, and it corrupted ordinary prose: the configuration validator's
# own warning, "MONGO_URL carries no username:password — the database is either
# unauthenticated…", came out as "…username:password [REDACTED] database is…",
# because the em dash after the word "password" was read as a value. A scrubber
# that quietly damages legitimate log messages costs more than it saves; a
# credential in a log line is essentially always written as `key=value` or
# `key: value`, so requiring the separator loses nothing real.
_MESSAGE_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|bearer)\b"
    r"(\s*[=:]\s*)"
    r"("
    r"\"[^\"]{1,512}\""
    r"|'[^']{1,512}'"
    r"|(?:bearer|basic|token)\s+[^\s,;&\"']{1,512}"
    r"|[^\s,;&\"']{1,512}"
    r")"
)
_REDACTED = "[REDACTED]"

# Structured fields THIS package emits, which are known non-sensitive and are
# therefore exempt from the audit redactor's (deliberately broad) key markers.
# See `_safe_extras` for why the exemption exists rather than the markers being
# loosened. Keep this list short and add to it only for a field whose name and
# contents are both controlled by observability code — never for an application
# field, whose value the caller chooses.
_SCHEMA_FIELDS = frozenset(
    {
        "event",           # the log event name, e.g. "http_request"
        "method",          # HTTP verb
        "route",           # route TEMPLATE, never a raw path
        "http_path",       # raw path, query string already stripped
        "status_code",     # contains the marker substring "code" — see _safe_extras
        "duration_ms",
        "client_ip",
        "user_agent",
        "startup_seconds",
        "uptime_seconds",
        "health_checks",
        # Fields of the boot-time logging-configuration report.
        "level", "format", "scrub_messages", "log_health_requests", "destination",
        "file_sinks",      # PH2.6 file-sink summary (nested dict, no secrets)
        # Build provenance emitted at startup.
        "version", "revision", "environment",
    }
)

# Third-party loggers whose default verbosity would bury this application's own
# output. Each is a deliberate decision, not a blanket silence:
#   uvicorn.access — replaced wholesale by our access log, which carries the
#       request ID and the route template that uvicorn's cannot know about.
#       Leaving both on doubles request-log volume and gives two lines per
#       request that disagree about the route.
#   httpx/httpcore — INFO logs one line per outbound HTTP call; the market-data
#       layer makes thousands per minute.
#   pymongo/motor — INFO includes per-command and topology chatter.
#   apscheduler — INFO logs every job submission and completion on every tick.
# All remain at WARNING, so genuine problems still surface.
_NOISY_LOGGERS = {
    "uvicorn.access": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "pymongo": logging.WARNING,
    "motor": logging.WARNING,
    "apscheduler": logging.WARNING,
    "asyncio": logging.WARNING,
}


def log_level() -> int:
    """Resolved log level; an unrecognised value falls back to INFO with a warning.

    Falling back rather than raising is deliberate: a typo in `LOG_LEVEL` must
    not be able to prevent a production container from starting. Losing debug
    verbosity is recoverable; a boot loop at 3am is not.
    """
    raw = os.environ.get(LOG_LEVEL_ENV, "").strip().upper()
    if not raw:
        return logging.INFO
    level = logging.getLevelName(raw)
    if isinstance(level, int):
        return level
    print(
        f"[observability] unrecognised {LOG_LEVEL_ENV}={raw!r}; using INFO",
        file=sys.stderr,
    )
    return logging.INFO


def log_format() -> str:
    """`json` or `text`. Defaults to json in production, text elsewhere."""
    raw = os.environ.get(LOG_FORMAT_ENV, "").strip().lower()
    if raw in (FORMAT_JSON, FORMAT_TEXT):
        return raw
    from security.cookies import is_production

    return FORMAT_JSON if is_production() else FORMAT_TEXT


def scrub_messages_enabled() -> bool:
    return os.environ.get(LOG_SCRUB_ENV, "1").strip().lower() not in ("0", "false", "no")


def log_health_requests() -> bool:
    """Whether probe traffic is access-logged. Off by default.

    A liveness probe every 10s across 3 replicas is ~26,000 log lines a day that
    all say the same thing. That is not merely noise: on a per-GB-ingested log
    platform it is a bill, and it pushes the lines that matter out of a retention
    window. Metrics still count every probe, so the traffic is not invisible —
    it is just not narrated.
    """
    return os.environ.get(LOG_HEALTH_ENV, "0").strip().lower() in ("1", "true", "yes")


# --------------------------------------------------------------------------- #
# Scrubbing                                                                     #
# --------------------------------------------------------------------------- #
def scrub_message(message: str) -> str:
    """Blank credential-shaped values inside a free-text log message."""
    if not message or not scrub_messages_enabled():
        return message
    return _MESSAGE_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", message)


def _safe_extras(record: logging.LogRecord) -> Dict[str, Any]:
    """Caller-supplied structured fields from the record, redacted.

    Values are passed through `security.audit.redact_fields` (the same recursive
    key-marker redactor the audit log uses) and then through a JSON-serialisable
    check — a stray `ObjectId` or `datetime` in an `extra` dict would otherwise
    raise inside the formatter, and an exception in a log handler is the one kind
    of bug that hides its own cause.

    Fields in `_SCHEMA_FIELDS` bypass redaction. That exemption is narrow and
    load-bearing. The audit redactor's markers are *deliberately* broad — its
    own docstring argues that a false-positive redact is harmless — and for
    audit metadata that is true. It is not true here: `status_code` contains the
    substring `code` (a marker, present for OAuth authorization codes), so
    without this allowlist every access-log line would report
    `"status_code": "[REDACTED]"` and the most-queried field in the log would be
    destroyed. The right fix is to name the fields this module owns and knows
    are non-sensitive, not to weaken a security control for everyone else.
    """
    extras: Dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
            continue
        extras[key] = value
    if not extras:
        return {}

    exempt = {k: v for k, v in extras.items() if k in _SCHEMA_FIELDS}
    untrusted = {k: v for k, v in extras.items() if k not in _SCHEMA_FIELDS}
    if untrusted:
        try:
            from security.audit import redact_fields

            untrusted = redact_fields(untrusted)
        except Exception:  # pragma: no cover - defensive
            # Redaction is a security control: if it cannot run, drop the fields
            # rather than emit them unredacted. Losing structured context is
            # recoverable; logging a credential is not.
            untrusted = {k: "[REDACTION-UNAVAILABLE]" for k in untrusted}

    return {k: _jsonable(v) for k, v in {**exempt, **untrusted}.items()}


def _record_context(record: logging.LogRecord) -> context.RequestContext:
    """The request context belonging to `record`.

    Prefers the snapshot `log_streams.BoundedQueueHandler.prepare` attached on
    the calling thread, and falls back to the live context variable.

    Both paths are needed, and which one applies depends on WHERE the record is
    formatted. The stdout handler formats inline, on the task that logged, so
    the context variable is correct there and no snapshot exists. File handlers
    format on the queue listener thread, whose context is empty — there the
    snapshot is the only surviving copy. Reading the context variable in both
    cases silently produced `request_id: "-"` in every file while stdout looked
    fine, which defeats the entire point of correlation IDs on the one sink that
    persists.
    """
    request_id = getattr(record, "_ctx_request_id", None)
    if request_id is None:
        return context.current()
    return context.RequestContext(
        request_id=request_id,
        method=getattr(record, "_ctx_method", "-"),
        path=getattr(record, "_ctx_path", "-"),
    )


def _jsonable(value: Any) -> Any:
    """Coerce a value into something `json.dumps` accepts, without raising."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    try:
        return str(value)
    except Exception:  # pragma: no cover - defensive
        return "<unserialisable>"


# --------------------------------------------------------------------------- #
# Formatters                                                                    #
# --------------------------------------------------------------------------- #
class StructuredFormatter(logging.Formatter):
    """One JSON object per line — the machine-readable production format.

    Correlation fields are injected by the *formatter* rather than requiring
    every call site to pass them, which is what makes this work across a
    12,000-line codebase that was never written with request IDs in mind: an
    untouched `logger.warning("...")` deep inside a service still comes out with
    the right `request_id` attached, because the value is read from the context
    variable at format time on the task that is doing the logging.
    """

    def __init__(self) -> None:
        super().__init__()
        # Resolved once — these are constant for the process lifetime, and this
        # runs on every single log record.
        self._service = runtime.service_name()
        self._version = runtime.service_version()
        self._environment = runtime.environment()

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception as exc:  # pragma: no cover - a broken %-format in a caller
            message = f"<unformattable log message: {exc.__class__.__name__}>"

        ctx = _record_context(record)
        payload: Dict[str, Any] = {
            # ISO-8601 UTC with milliseconds. Sortable as a string, unambiguous
            # across hosts, and the format every log platform parses natively.
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_message(message),
            "service": self._service,
            "environment": self._environment,
            "version": self._version,
            "request_id": ctx.request_id,
        }
        if ctx.method != "-":
            payload["method"] = ctx.method
        if ctx.path != "-":
            payload["path"] = ctx.path

        payload.update(_safe_extras(record))

        if record.exc_info:
            exc_type, exc_value, _ = record.exc_info
            payload["exception"] = {
                "type": getattr(exc_type, "__name__", str(exc_type)),
                # The exception *message* goes through the same scrubber as the
                # log message: a pymongo connection error stringifies with the
                # credentials embedded in the URI.
                "message": scrub_message(str(exc_value)),
                "stacktrace": self.formatException(record.exc_info),
            }
        if record.stack_info:
            payload["stack_info"] = record.stack_info

        try:
            return json.dumps(payload, default=str, separators=(",", ":"))
        except Exception:  # pragma: no cover - last-resort
            # Never let a formatter failure swallow a log line: degrade to
            # something readable rather than emitting nothing.
            return json.dumps(
                {
                    "timestamp": payload["timestamp"],
                    "level": record.levelname,
                    "logger": record.name,
                    "message": "<log record could not be serialised>",
                    "request_id": ctx.request_id,
                }
            )


class HumanFormatter(logging.Formatter):
    """The development format: one readable line, with the request ID kept.

    Same information, optimised for eyes instead of parsers. The request ID is
    included (short form) because correlation is just as useful when debugging
    locally — and because a developer who never sees the field will not think to
    use it in production.
    """

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        base = scrub_message(base)
        ctx = _record_context(record)
        if ctx.request_id != context.NO_REQUEST_ID:
            # First 8 characters: enough to disambiguate visually in a local
            # session, short enough not to dominate the line.
            base = f"{base}  [req {ctx.request_id[:8]}]"
        return base


# --------------------------------------------------------------------------- #
# Configuration entry point                                                     #
# --------------------------------------------------------------------------- #
_configured = False


def configure_logging(*, force: bool = False) -> Dict[str, Any]:
    """Install the process-wide logging configuration. Idempotent.

    Replaces `logging.basicConfig`, and must be called as early as possible in
    `server.py` — before the application imports run — so that boot-time output
    is structured too.

    Existing root handlers are *removed*, not added to. A library that called
    `basicConfig` during import (several do) leaves a handler behind, and the
    result is every line printed twice in two different formats — which looks
    like duplicated events and is genuinely confusing during an incident.
    """
    global _configured
    if _configured and not force:
        return describe()

    level = log_level()
    fmt = log_format()

    # stdout, not stderr: application logs are the process's normal output, and
    # a platform that treats stderr as "errors" would otherwise flag every INFO
    # line. Genuine crash output (an unhandled traceback from the interpreter)
    # still goes to stderr on its own.
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(StructuredFormatter() if fmt == FORMAT_JSON else HumanFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # PH2.6 — optional durable file sinks, split by stream and rotated.
    #
    # This is an ADDITION to stdout, never a replacement: `docker logs`,
    # `kubectl logs` and any already-attached collector keep working unchanged.
    # It is installed from here, rather than offering a second configuration
    # entry point, so that "one logging configuration" stays literally true.
    #
    # The formatter is ALWAYS the JSON one, even when stdout is human-readable
    # text — a log file is read by a collector or by `jq`, and it is also where
    # redaction has to be trusted the most, since a file persists. Passing the
    # formatter in (rather than having log_streams import it) is what keeps the
    # dependency arrow one-way and cycle-free.
    file_sinks = log_streams.attach_file_sinks(root, StructuredFormatter())

    for name, noisy_level in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(noisy_level)

    # Timestamps are always UTC. A container fleet that logs local time cannot be
    # correlated at all across regions, and DST makes an hour of history either
    # ambiguous or missing once a year.
    logging.Formatter.converter = time.gmtime

    _configured = True
    return describe(file_sinks=file_sinks)


def describe(file_sinks: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The effective logging configuration — logged once at boot.

    Worth emitting explicitly: "why are there no debug logs?", "why is this not
    JSON?" and (PH2.6) "where are the files, and what stops them growing?" are
    otherwise answered by reading deployment YAML.
    """
    sinks = file_sinks if file_sinks is not None else log_streams.describe()
    return {
        "level": logging.getLevelName(log_level()),
        "format": log_format(),
        "scrub_messages": scrub_messages_enabled(),
        "log_health_requests": log_health_requests(),
        # stdout is unconditional; files are additive. Reported as one string so
        # a single grep of the boot log answers "is anything being written to
        # disk on this host?"
        "destination": "stdout+files" if sinks.get("enabled") else "stdout",
        "file_sinks": sinks,
    }


def reset_for_tests() -> None:
    """Allow a subsequent `configure_logging()` to take effect. Tests only."""
    global _configured
    _configured = False
    log_streams.stop_file_sinks()


# --------------------------------------------------------------------------- #
# Access log                                                                    #
# --------------------------------------------------------------------------- #
_access_logger = logging.getLogger(ACCESS_LOGGER_NAME)


def log_request(
    *,
    method: str,
    route: str,
    path: str,
    status: int,
    duration_ms: float,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Emit the single access-log line for a completed request.

    One line per request, not two (no "request started" line): under load a
    start line doubles volume to tell you something the completion line already
    implies, and the in-flight *gauge* answers "what is running right now?" far
    better than counting unmatched start lines ever could.

    Severity is derived from the status code so a log platform's default
    "errors" view is immediately correct: 5xx is ours (ERROR), 4xx is the
    caller's (WARNING — visible, but not a page), everything else INFO.

    ``path`` has already had its query string removed by the middleware; see the
    module docstring on why that is not optional.
    """
    if status >= 500:
        level = logging.ERROR
    elif status >= 400:
        level = logging.WARNING
    else:
        level = logging.INFO

    _access_logger.log(
        level,
        "%s %s %s %.2fms",
        method,
        route,
        status,
        duration_ms,
        extra={
            "event": "http_request",
            "method": method,
            "route": route,
            "http_path": path,
            "status_code": status,
            "duration_ms": round(duration_ms, 2),
            "client_ip": client_ip,
            "user_agent": user_agent,
        },
    )

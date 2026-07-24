"""Log stream separation and the file-sink pipeline (PH2.6).

WHY SEPARATE STREAMS
--------------------
PH2.5 produces one stream of JSON records to stdout. For a log platform that is
ideal — you index once and query with a filter. For everything else it is not,
because the five kinds of record this application emits have genuinely different
operational properties:

============  ==========  ==============  ========================================
Stream        Volume      Retention want  Who reads it
============  ==========  ==============  ========================================
access        Highest     Days            Capacity planning, latency triage
application   High        Days            Debugging a specific failure
security      Low         Months          Abuse investigation, rate-limit tuning
audit         Lowest      Months / years  Compliance, incident forensics
error         Low         Weeks           "What is broken right now?"
============  ==========  ==============  ========================================

Storing an audit record — "this admin changed that user's role" — under the same
retention rule as an access log line means either paying to keep 26 million
`GET /api/health` lines for a year, or deleting the audit trail after a week.
Both are wrong, and no amount of querying fixes it after the data is gone.
Separating at write time is what makes per-stream retention possible at all.

The separation is derived entirely from **logger names**, which already carry
the information:

    security.audit.events   → audit    (the AuditRecord event stream, PH1.10)
    security.*              → security (CORS/CSRF/rate-limit/audit internals)
    stockassist.access      → access   (the PH2.5 access log)
    everything else         → application
    any logger at ERROR+    → error

Not one call site changes. That is the whole reason the routing key is the
logger name and not, say, a field the caller has to remember to pass.

`error.log` is deliberately a **view, not a partition**: an ERROR from
`stockassist.access` appears in both `access.log` and `error.log`. Making it
exclusive would mean the access log silently loses exactly its 5xx lines — the
ones you go to the access log to find.

WHY EVERY FILE HANDLER SITS BEHIND A QUEUE
------------------------------------------
`logging.FileHandler.emit` does a blocking `write()`. In a synchronous
thread-per-request server that costs the thread that logged. In an asyncio
server it costs **the event loop**, and therefore every other request currently
in flight — a slow or contended disk turns into latency on requests that never
touched a file. Rotation is worse: a rollover with compression is on the order
of a second, and inline it would stall the entire process.

So the root logger gets one `QueueHandler`; a single background thread owns
every file handler and does all the I/O, all the rotation and all the
compression. The request path does an in-memory `put_nowait` and returns.

The queue is **bounded**. An unbounded queue in front of a stalled disk is a
memory leak with extra steps, and the failure mode is that the process is
OOM-killed — losing the logs anyway, plus the service. When the queue is full,
records are dropped and counted, and the count is exported as
`log_records_dropped_total` so the loss is visible rather than silent. Dropping
telemetry to keep a trading backend alive is the right trade; doing it without
telling anyone is not.

WHY FILES ARE OFF BY DEFAULT
----------------------------
Because stdout is still correct. `LOG_TO_FILES` is opt-in for the deployments
that need durable local logs — a single VM with no collector, a compliance
retention rule, an air-gapped install. Turning it on adds a sink; it never
removes stdout, so `docker logs`, `kubectl logs` and any collector already
attached keep working exactly as before.
"""
from __future__ import annotations

import atexit
import copy
import logging
import logging.handlers
import os
import queue
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from observability import context, log_rotation

# --------------------------------------------------------------------------- #
# Configuration                                                                 #
# --------------------------------------------------------------------------- #
LOG_TO_FILES_ENV = "LOG_TO_FILES"
LOG_DIR_ENV = "LOG_DIR"
LOG_FILE_STREAMS_ENV = "LOG_FILE_STREAMS"
LOG_QUEUE_SIZE_ENV = "LOG_QUEUE_SIZE"

# `/var/log/<service>` is the FHS location for exactly this, which means an
# operator, a log agent's default config and a monitoring runbook all already
# expect it. Writing under the application directory instead — `/app/logs`,
# `./logs` — is the mistake this default exists to prevent: it mixes mutable
# runtime state into a code tree that PH2.1 deliberately made read-only-ish
# (root-owned source, non-root process), and it disappears with the container.
DEFAULT_LOG_DIR = "/var/log/stockassist"

# ~10k records is a few MB of memory and several seconds of headroom at
# realistic rates — enough to absorb a rotation, a compression pass or a brief
# disk stall without dropping anything, and small enough that a permanently
# stuck writer costs bounded memory rather than the process.
DEFAULT_QUEUE_SIZE = 10_000
MIN_QUEUE_SIZE = 100
MAX_QUEUE_SIZE = 1_000_000


def _warn(message: str) -> None:
    """stderr, not `logging` — see log_rotation._warn for why."""
    print(f"[observability.log_streams] {message}", file=sys.stderr)


def file_logging_enabled() -> bool:
    return os.environ.get(LOG_TO_FILES_ENV, "0").strip().lower() in ("1", "true", "yes", "on")


def log_directory() -> str:
    return os.environ.get(LOG_DIR_ENV, "").strip() or DEFAULT_LOG_DIR


def queue_size() -> int:
    return log_rotation._env_int(
        LOG_QUEUE_SIZE_ENV, DEFAULT_QUEUE_SIZE, MIN_QUEUE_SIZE, MAX_QUEUE_SIZE
    )


# --------------------------------------------------------------------------- #
# Stream taxonomy                                                               #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LogStream:
    """One logical log stream and the rule that routes records into it."""

    name: str
    filename: str
    # Empty means "not selected by name". Matching is prefix-on-dot-boundary, so
    # `security` claims `security.csrf` but never `securityfoo`.
    logger_prefixes: Tuple[str, ...]
    min_level: int
    description: str
    # Exclusive streams form an ordered partition: the first whose prefixes
    # match wins, and the catch-all takes the remainder. `error` is not
    # exclusive — it copies records that are already in another stream.
    exclusive: bool = True


AUDIT_LOGGER = "security.audit.events"
SECURITY_LOGGER_PREFIX = "security"
ACCESS_LOGGER = "stockassist.access"

# ORDER IS SIGNIFICANT — most specific first. `security.audit.events` is a child
# of `security`, so if the security stream were checked first it would swallow
# every audit record and `audit.log` would be permanently empty. This is the
# single easiest thing to get wrong in this file.
AUDIT_STREAM = LogStream(
    name="audit",
    filename="audit.log",
    logger_prefixes=(AUDIT_LOGGER,),
    min_level=logging.NOTSET,
    description="Security audit events (PH1.10 taxonomy). Longest retention.",
)
SECURITY_STREAM = LogStream(
    name="security",
    filename="security.log",
    logger_prefixes=(SECURITY_LOGGER_PREFIX,),
    min_level=logging.NOTSET,
    description="Security subsystem logs: CORS, CSRF, rate limiting, secrets, sessions.",
)
ACCESS_STREAM = LogStream(
    name="access",
    filename="access.log",
    logger_prefixes=(ACCESS_LOGGER,),
    min_level=logging.NOTSET,
    description="One line per HTTP request. Highest volume, shortest retention.",
)
APPLICATION_STREAM = LogStream(
    name="application",
    filename="application.log",
    logger_prefixes=(),  # catch-all
    min_level=logging.NOTSET,
    description="Everything not claimed by another stream: business logic, services, boot.",
)
ERROR_STREAM = LogStream(
    name="error",
    filename="error.log",
    logger_prefixes=(),
    min_level=logging.ERROR,
    description="A copy of every ERROR/CRITICAL record from all streams. A view, not a partition.",
    exclusive=False,
)

EXCLUSIVE_STREAMS: Tuple[LogStream, ...] = (
    AUDIT_STREAM,
    SECURITY_STREAM,
    ACCESS_STREAM,
    APPLICATION_STREAM,  # must stay last: it is the catch-all
)
ALL_STREAMS: Tuple[LogStream, ...] = EXCLUSIVE_STREAMS + (ERROR_STREAM,)
STREAM_NAMES: Tuple[str, ...] = tuple(s.name for s in ALL_STREAMS)


def _matches(logger_name: str, prefixes: Sequence[str]) -> bool:
    """Dot-boundary prefix match. `security` claims `security.csrf`, not `securityx`."""
    for prefix in prefixes:
        if logger_name == prefix or logger_name.startswith(prefix + "."):
            return True
    return False


class StreamFilter(logging.Filter):
    """Routes a record to one stream's handler. Installed as a handler filter.

    `logging.Handler.handle` runs filters before `emit`, and `QueueListener`
    goes through `handle`, so these are honoured on the listener thread exactly
    as they would be inline.

    **The exclusion check runs before the claim check, and that ordering is the
    whole mechanism.** Each handler's filter is independent — there is no shared
    "has this record been routed yet?" state to consult — so "first match wins"
    has to be realised by giving every stream the prefixes claimed by the
    streams ahead of it in `EXCLUSIVE_STREAMS` (see `preceding_prefixes`).
    Without it, `security.audit.events` matches the `security` prefix as well as
    its own and every audit record is written to two files: the audit trail is
    still complete, but `security.log` inherits the audit stream's much longer
    retention requirement by accident.
    """

    def __init__(self, stream: LogStream, excluded_prefixes: Sequence[str] = ()) -> None:
        super().__init__()
        self.stream = stream
        self._excluded = tuple(excluded_prefixes)

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - stdlib name
        if record.levelno < self.stream.min_level:
            return False
        if self._excluded and _matches(record.name, self._excluded):
            return False
        if self.stream.logger_prefixes:
            return _matches(record.name, self.stream.logger_prefixes)
        # No prefixes: the catch-all (whose exclusion list is every claimed
        # prefix) or the error view (which excludes nothing, by design).
        return True


def preceding_prefixes(stream: LogStream) -> Tuple[str, ...]:
    """Prefixes claimed by exclusive streams ordered ahead of `stream`.

    For the catch-all — last in the tuple — this is every claimed prefix, so one
    uniform rule covers both cases. Non-exclusive streams (`error`) exclude
    nothing: they are copies of records another stream already owns.
    """
    if not stream.exclusive:
        return ()
    claimed: List[str] = []
    for candidate in EXCLUSIVE_STREAMS:
        if candidate is stream:
            break
        claimed.extend(candidate.logger_prefixes)
    return tuple(claimed)


def selected_streams() -> Tuple[LogStream, ...]:
    """The streams to write to file, per `LOG_FILE_STREAMS`. Defaults to all.

    A subset is genuinely useful: a deployment that already ships everything to
    a collector but must retain the audit trail locally for compliance sets
    `LOG_FILE_STREAMS=audit,security` and writes ~1 MB a day instead of ~1 GB.
    """
    raw = os.environ.get(LOG_FILE_STREAMS_ENV, "").strip()
    if not raw:
        return ALL_STREAMS
    wanted = {part.strip().lower() for part in raw.split(",") if part.strip()}
    unknown = wanted - set(STREAM_NAMES)
    if unknown:
        _warn(
            f"{LOG_FILE_STREAMS_ENV} names unknown stream(s) {sorted(unknown)}; "
            f"known streams are {list(STREAM_NAMES)}"
        )
    chosen = tuple(s for s in ALL_STREAMS if s.name in wanted)
    if not chosen:
        _warn(f"{LOG_FILE_STREAMS_ENV}={raw!r} selected no known stream; writing all streams")
        return ALL_STREAMS
    return chosen


# --------------------------------------------------------------------------- #
# Log directory                                                                 #
# --------------------------------------------------------------------------- #
def ensure_log_dir(directory: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Create and verify the log directory. Returns `(path, error)`.

    Returns an error string instead of raising, and every caller degrades to
    stdout-only. A container that will not boot because `/var/log/stockassist`
    is not writable is a worse outcome than one that boots and tells you, in the
    logs you can still read, that it could not open the log files. This mirrors
    `LOG_LEVEL`'s existing fall-back-and-warn behaviour.

    Writability is proved by actually creating and removing a file, not by
    `os.access`. `os.access` answers a question about the *permission bits*,
    which is a different question from "can this process write here" the moment
    a read-only mount, a full filesystem, an SELinux label or a container user
    namespace is involved — and all four are normal in production.
    """
    path = directory or log_directory()
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        return None, f"cannot create log directory {path}: {exc}"

    probe = os.path.join(path, f".write-probe-{os.getpid()}")
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
    except OSError as exc:
        return None, f"log directory {path} is not writable: {exc}"
    finally:
        try:
            os.remove(probe)
        except OSError:  # pragma: no cover - defensive
            pass
    return path, None


# --------------------------------------------------------------------------- #
# Bounded queue plumbing                                                        #
# --------------------------------------------------------------------------- #
_dropped_lock = threading.Lock()
_dropped_records = 0


def dropped_records() -> int:
    """Records discarded because the file-sink queue was full.

    Exported as the `log_records_dropped_total` metric. Non-zero means the disk
    could not keep up with the application: the log files have gaps, and either
    the volume is too slow or the log level is too verbose for it.
    """
    return _dropped_records


def _record_drop() -> None:
    global _dropped_records
    with _dropped_lock:
        _dropped_records += 1


def reset_drop_counter() -> None:
    """Tests only."""
    global _dropped_records
    with _dropped_lock:
        _dropped_records = 0


class BoundedQueueHandler(logging.handlers.QueueHandler):
    """A `QueueHandler` that drops (and counts) instead of blocking, and that
    does not mutilate the record on the way through.

    Two deliberate departures from the stdlib:

    **`enqueue` never blocks.** `Queue.put` on a full queue would block the
    caller — which is the event loop — until the writer thread caught up. That
    converts a slow disk into an application-wide stall, the exact failure this
    queue exists to prevent.

    **`prepare` keeps the record intact.** The stdlib's `prepare` formats the
    message and then clears `args`, `exc_info`, `exc_text` and `stack_info`, so
    the record survives pickling onto a `multiprocessing.Queue`. Ours is an
    in-process `queue.Queue`, so nothing is pickled — and running the stdlib
    version would strip `exc_info` and silently drop **every stack trace from
    every file log**, while stdout kept them. A logging pipeline that loses
    tracebacks on one sink and not the other is a genuinely awful thing to
    debug, so this is worth the override.

    It also **snapshots the request context onto the record**, which is what
    makes correlation survive the queue at all. `observability.context` is a
    `contextvars.ContextVar`, and a `ContextVar` is bound to the task/thread
    that set it. The stdout handler formats inline on the request's own thread
    and therefore reads the right value; the file handlers format on the
    LISTENER thread, which has its own empty context and would read the default.
    The result — before this snapshot existed — was `request_id` present on
    stdout and `"-"` in every file, for the same record. That asymmetry is worse
    than having no request ID at all: the field is there, it looks authoritative,
    and it is wrong precisely when someone is grepping the files during an
    incident because the platform is down.

    The snapshot is taken here because `prepare` runs on the CALLING thread,
    which is the last moment the context still exists. Attribute names are
    underscore-prefixed so `logging._safe_extras` skips them — they are
    plumbing, not caller-supplied structured fields.

    The record is shallow-copied before its lazy `%`-args are resolved, because
    the same record object is also handed to the stdout handler; mutating it
    here would reach across into that sink.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        prepared = copy.copy(record)
        try:
            # Resolve `logger.info("x=%s", obj)` NOW, on the calling thread.
            # Deferring it to the listener would render whatever `obj` happens
            # to contain milliseconds later — a real source of "the log says
            # something that was never true".
            prepared.msg = record.getMessage()
            prepared.args = None
        except Exception:  # pragma: no cover - a broken %-format in a caller
            prepared.args = None
        ctx = context.current()
        prepared._ctx_request_id = ctx.request_id
        prepared._ctx_method = ctx.method
        prepared._ctx_path = ctx.path
        return prepared

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            _record_drop()


# --------------------------------------------------------------------------- #
# Pipeline lifecycle                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class _Pipeline:
    listener: Optional[logging.handlers.QueueListener] = None
    queue_handler: Optional[BoundedQueueHandler] = None
    file_handlers: List[log_rotation.RotatingLogFileHandler] = field(default_factory=list)
    directory: Optional[str] = None
    policy: Optional[log_rotation.RotationPolicy] = None
    streams: Tuple[str, ...] = ()
    root: Optional[logging.Logger] = None


_pipeline = _Pipeline()
_atexit_registered = False


def attach_file_sinks(
    root_logger: logging.Logger,
    formatter: logging.Formatter,
) -> Dict[str, Any]:
    """Install the file-sink pipeline on `root_logger`. Returns a summary dict.

    Called only from `observability.logging.configure_logging`, so there remains
    exactly ONE place that configures logging — this module supplies the
    machinery, it does not offer a second entry point.

    `formatter` is injected rather than imported to keep the dependency arrow
    pointing one way (`logging` → `log_streams` → `log_rotation`); importing the
    formatter here would close a cycle. It is always the JSON formatter, even
    when stdout is human-readable text: a file is read by a collector or by
    `jq`, never by staring at a terminal, and a mixed-format archive cannot be
    parsed by anything.
    """
    global _atexit_registered

    stop_file_sinks()

    if not file_logging_enabled():
        return {"enabled": False, "reason": f"{LOG_TO_FILES_ENV} is not set"}

    directory, error = ensure_log_dir()
    if error or directory is None:
        _warn(f"{error} — continuing with stdout logging only")
        return {"enabled": False, "reason": error}

    policy = log_rotation.resolve_policy()
    streams = selected_streams()

    handlers: List[log_rotation.RotatingLogFileHandler] = []
    for stream in streams:
        path = os.path.join(directory, stream.filename)
        try:
            handler = log_rotation.RotatingLogFileHandler(path, policy)
        except Exception as exc:  # pragma: no cover - defensive
            _warn(f"could not open {path}: {exc}")
            continue
        handler.setFormatter(formatter)
        # Exclusions come from the FULL stream list, never from the selected
        # subset: `LOG_FILE_STREAMS=security` must not cause `security.log` to
        # start absorbing audit records simply because `audit.log` was not
        # requested. Selection controls which files exist, not what belongs in
        # them.
        handler.addFilter(StreamFilter(stream, preceding_prefixes(stream)))
        handlers.append(handler)

    if not handlers:
        return {"enabled": False, "reason": "no file handler could be opened"}

    record_queue: "queue.Queue[Any]" = queue.Queue(maxsize=queue_size())
    queue_handler = BoundedQueueHandler(record_queue)
    # respect_handler_level=False: routing is done by the filters above, and
    # every handler is left at level 0 so the root logger's level remains the
    # single place verbosity is decided.
    listener = logging.handlers.QueueListener(record_queue, *handlers, respect_handler_level=False)
    listener.start()
    root_logger.addHandler(queue_handler)

    _pipeline.listener = listener
    _pipeline.queue_handler = queue_handler
    _pipeline.file_handlers = handlers
    _pipeline.directory = directory
    _pipeline.policy = policy
    _pipeline.streams = tuple(s.name for s in streams)
    _pipeline.root = root_logger

    if not _atexit_registered:
        # The listener runs on a daemon thread, which the interpreter kills at
        # exit without draining. Without this, the last few seconds of logs
        # before a shutdown — the most interesting seconds there are — would be
        # lost from the files while appearing normally on stdout.
        atexit.register(stop_file_sinks)
        _atexit_registered = True

    return describe()


def stop_file_sinks() -> None:
    """Drain the queue, flush and close every file handler. Idempotent."""
    listener, queue_handler, handlers, root = (
        _pipeline.listener,
        _pipeline.queue_handler,
        _pipeline.file_handlers,
        _pipeline.root,
    )
    _pipeline.listener = None
    _pipeline.queue_handler = None
    _pipeline.file_handlers = []
    _pipeline.root = None
    _pipeline.directory = None
    _pipeline.policy = None
    _pipeline.streams = ()

    # Detach FIRST so nothing new is enqueued while the listener is stopping.
    if root is not None and queue_handler is not None:
        try:
            root.removeHandler(queue_handler)
        except Exception:  # pragma: no cover - defensive
            pass
    if listener is not None:
        try:
            listener.stop()  # enqueues the sentinel and joins the thread
        except Exception:  # pragma: no cover - defensive
            pass
    for handler in handlers:
        try:
            handler.close()
        except Exception:  # pragma: no cover - defensive
            pass


def flush() -> None:
    """Block until everything queued so far has reached disk.

    `QueueListener._monitor` calls `task_done()` for every record it processes,
    so `Queue.join()` is an exact "the writer has caught up" barrier — no sleeps
    and no polling, which is what makes the tests in
    `test_log_infrastructure.py` deterministic rather than flaky.
    """
    listener = _pipeline.listener
    if listener is None:
        return
    try:
        listener.queue.join()
    except Exception:  # pragma: no cover - defensive
        pass
    for handler in _pipeline.file_handlers:
        try:
            handler.flush()
        except Exception:  # pragma: no cover - defensive
            pass


def active() -> bool:
    return _pipeline.listener is not None


def describe() -> Dict[str, Any]:
    """The effective file-sink configuration, for the boot summary."""
    if _pipeline.listener is None:
        return {"enabled": False, "reason": f"{LOG_TO_FILES_ENV} is not set"}
    policy = _pipeline.policy or log_rotation.RotationPolicy()
    streams = _pipeline.streams
    return {
        "enabled": True,
        "directory": _pipeline.directory,
        "streams": list(streams),
        "queue_size": queue_size(),
        "rotation": policy.describe(),
        # The number an operator actually needs before enabling this: the
        # worst-case disk footprint, stated up front rather than discovered.
        "worst_case_bytes_total": policy.worst_case_bytes * len(streams),
        "current_bytes": (
            log_rotation.directory_usage_bytes(_pipeline.directory)
            if _pipeline.directory
            else 0
        ),
    }

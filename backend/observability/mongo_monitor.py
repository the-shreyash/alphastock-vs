"""MongoDB command instrumentation (PH3.7).

WHY THE DRIVER'S LISTENER AND NOT A WRAPPER
-------------------------------------------
This backend issues several hundred distinct `await db.<collection>.<op>()`
calls. Any instrumentation scheme that requires touching them has the same
defect: it covers what was written before the sprint and nothing written after
it. The first query someone adds during an incident — which is exactly when
someone adds a query — is invisible, and the gap is silent, because a missing
metric looks identical to a metric that is legitimately zero.

PyMongo publishes every command it sends through
``pymongo.monitoring.CommandListener``, registered once on the client. It sees
every operation by construction, including the ones the ODM issues on its own
behalf and the ones a future author has not written yet. There is no way to
forget it, and no call site knows it exists.

WHAT IS DEliberately NOT RECORDED
---------------------------------
``CommandStartedEvent.command`` is the full BSON command document. For this
application that means, variously: a user's email address in a login lookup, a
bcrypt hash in a password update, a broker access token in a credential write,
and every field of every trade. **None of it is read here.** This module touches
``command_name`` (a wire-protocol verb: `find`, `update`, `aggregate`) and
``duration_micros``, and nothing else.

``CommandFailedEvent.failure`` is also avoided as free text: it is a server
error document whose ``errmsg`` routinely embeds the failing query, and on a
connection fault it embeds the connection URI *with credentials*. Only the
integer ``code`` is used, mapped through a small fixed table to a label. An
unmapped code becomes `code_<n>`, which is bounded by MongoDB's error-code space
and carries no data.

THREADING
---------
PyMongo invokes listeners synchronously on whichever thread issued the command,
and pool events on the driver's monitor threads. The metrics registry is already
lock-guarded per metric, so counter updates are safe as-is. The one piece of
state this module owns — the checked-out connection count — has its own lock.

COST
----
Two dict lookups and an integer add per command, on a code path that just did
network I/O. Measured at well under 1% of command latency (see
docs/architecture/OBSERVABILITY.md §Overhead). Registration is opt-out via
``MONGO_COMMAND_METRICS=0`` for the case where that turns out to be wrong on
someone's hardware.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

from observability import instruments

logger = logging.getLogger(__name__)

# PyMongo validates registered listeners with `isinstance`, not by attribute
# presence, so the listeners below must genuinely subclass its ABCs — duck
# typing is rejected at client construction with a TypeError. The import is
# still guarded: `observability` is imported by modules that must remain usable
# without a database driver (the log-rotation tooling, the config validator),
# and a hard import here would make `import observability.metrics` fail in an
# environment that has no pymongo. Falling back to `object` keeps the module
# importable; `listeners()` then returns nothing, because there is no driver to
# register with anyway.
try:  # pragma: no cover - import shape depends on the environment
    from pymongo.monitoring import CommandListener as _CommandListenerBase
    from pymongo.monitoring import ConnectionPoolListener as _PoolListenerBase

    _PYMONGO_AVAILABLE = True
except Exception:  # pragma: no cover - driver-less environment
    _CommandListenerBase = object  # type: ignore[assignment,misc]
    _PoolListenerBase = object  # type: ignore[assignment,misc]
    _PYMONGO_AVAILABLE = False

#: Server error codes worth distinguishing on a dashboard. Everything else
#: becomes `code_<n>`; the point of the table is that the common failures get
#: readable names, not that it is exhaustive.
_ERROR_CODE_NAMES = {
    6: "host_unreachable",
    7: "host_not_found",
    11600: "interrupted_at_shutdown",
    11602: "interrupted_due_to_replset_state_change",
    13: "unauthorized",
    18: "authentication_failed",
    50: "max_time_ms_expired",
    89: "network_timeout",
    91: "shutdown_in_progress",
    189: "primary_stepped_down",
    262: "exceeded_time_limit",
    11000: "duplicate_key",
    43: "cursor_not_found",
}

#: Cap on the distinct `command` label values accepted. The wire protocol has
#: roughly twenty verbs, so anything past this is a driver version emitting
#: something unexpected rather than legitimate growth — fold it into one bucket
#: instead of letting it walk the metric's series ceiling.
_MAX_COMMAND_NAMES = 40
_seen_commands: set[str] = set()
_seen_lock = threading.Lock()


def enabled() -> bool:
    """False disables registration entirely. Default on."""
    return os.environ.get("MONGO_COMMAND_METRICS", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _command_label(name: Optional[str]) -> str:
    if not name:
        return "unknown"
    text = str(name)[:40]
    with _seen_lock:
        if text in _seen_commands:
            return text
        if len(_seen_commands) >= _MAX_COMMAND_NAMES:
            return "other"
        _seen_commands.add(text)
    return text


def _failure_reason(failure: Any) -> str:
    """A bounded label from a failure document, using the code and nothing else.

    See the module docstring: ``errmsg`` can contain the query that failed and,
    for connection faults, the credentialed connection URI. The code is an
    integer and cannot.
    """
    try:
        if isinstance(failure, dict):
            code = failure.get("code")
            if isinstance(code, int):
                return _ERROR_CODE_NAMES.get(code, f"code_{code}")
        return "unknown"
    except Exception:  # pragma: no cover - defensive
        return "unknown"


class CommandMetricsListener(_CommandListenerBase):  # type: ignore[misc,valid-type]
    """Records latency and outcome for every MongoDB command.

    Every method is wrapped: an exception raised inside a driver callback
    surfaces on the *caller's* stack, so a bug here would turn a successful
    query into a failed one. That is the single outcome instrumentation may
    never cause, and it is why each handler swallows rather than propagates.

    The methods take synthetic objects as readily as driver events — they only
    use ``getattr`` — which is what lets the tests exercise them without a
    running MongoDB.
    """

    def started(self, event: Any) -> None:  # noqa: D102 - driver callback
        # Nothing to do. Duration arrives on the terminal event, and the command
        # document available here is exactly what must not be touched.
        return None

    def succeeded(self, event: Any) -> None:  # noqa: D102 - driver callback
        try:
            instruments.record_mongo_command(
                _command_label(getattr(event, "command_name", None)),
                (getattr(event, "duration_micros", 0) or 0) / 1_000_000.0,
                ok=True,
            )
        except Exception:  # pragma: no cover - defensive
            pass

    def failed(self, event: Any) -> None:  # noqa: D102 - driver callback
        try:
            instruments.record_mongo_command(
                _command_label(getattr(event, "command_name", None)),
                (getattr(event, "duration_micros", 0) or 0) / 1_000_000.0,
                ok=False,
                reason=_failure_reason(getattr(event, "failure", None)),
            )
        except Exception:  # pragma: no cover - defensive
            pass


class PoolMetricsListener(_PoolListenerBase):  # type: ignore[misc,valid-type]
    """Tracks connection-pool occupancy.

    WHY THIS IS WORTH A LISTENER OF ITS OWN: pool exhaustion is the MongoDB
    failure that produces no errors. Every command waits for a free connection,
    every latency percentile rises together, `mongodb_commands_total` keeps
    climbing and nothing is logged — because from the driver's point of view
    nothing went wrong, it just queued. `checked_out` sitting at `max` is the
    only direct evidence, and PH3.6 left the pool unmeasured after fixing its
    `maxIdleTimeMS` default (finding M-8).

    The count is maintained rather than sampled because pymongo exposes no
    public accessor for it. Checkouts and check-ins are balanced by the driver,
    and `connection_check_out_failed` fires *instead of* a checkout, so the
    counter cannot drift — but it is clamped at zero anyway, since a gauge that
    goes negative after one missed event is a gauge nobody trusts again.
    """

    def __init__(self) -> None:
        self._checked_out = 0
        self._max = 0
        self._lock = threading.Lock()

    def _publish(self) -> None:
        instruments.record_mongo_pool(self._checked_out, self._max)

    # -- pool lifecycle ------------------------------------------------------- #
    def pool_created(self, event: Any) -> None:  # noqa: D102 - driver callback
        try:
            options = getattr(event, "options", None)
            size = getattr(options, "max_pool_size", None) if options else None
            with self._lock:
                if isinstance(size, int):
                    self._max = size
                self._publish()
        except Exception:  # pragma: no cover - defensive
            pass

    def pool_cleared(self, event: Any) -> None:  # noqa: D102 - driver callback
        # A cleared pool discards every connection — typically a failover. The
        # checked-out count is reset rather than decremented one by one, because
        # the corresponding check-in events will never arrive.
        try:
            with self._lock:
                self._checked_out = 0
                self._publish()
        except Exception:  # pragma: no cover - defensive
            pass

    def pool_ready(self, event: Any) -> None:  # noqa: D102 - driver callback
        return None

    def pool_closed(self, event: Any) -> None:  # noqa: D102 - driver callback
        try:
            with self._lock:
                self._checked_out = 0
                self._publish()
        except Exception:  # pragma: no cover - defensive
            pass

    # -- connection lifecycle -------------------------------------------------- #
    def connection_created(self, event: Any) -> None:  # noqa: D102
        return None

    def connection_ready(self, event: Any) -> None:  # noqa: D102
        return None

    def connection_closed(self, event: Any) -> None:  # noqa: D102
        return None

    def connection_check_out_started(self, event: Any) -> None:  # noqa: D102
        return None

    def connection_check_out_failed(self, event: Any) -> None:  # noqa: D102
        # Fires instead of a checkout, so nothing is decremented. It is its own
        # signal: a pool that cannot hand out a connection within
        # waitQueueTimeoutMS is saturated or the server is gone.
        try:
            instruments.record_error("database", "database")
        except Exception:  # pragma: no cover - defensive
            pass

    def connection_checked_out(self, event: Any) -> None:  # noqa: D102
        try:
            with self._lock:
                self._checked_out += 1
                self._publish()
        except Exception:  # pragma: no cover - defensive
            pass

    def connection_checked_in(self, event: Any) -> None:  # noqa: D102
        try:
            with self._lock:
                self._checked_out = max(0, self._checked_out - 1)
                self._publish()
        except Exception:  # pragma: no cover - defensive
            pass


_command_listener = CommandMetricsListener()
_pool_listener = PoolMetricsListener()


def listeners() -> list:
    """The listeners to pass as ``event_listeners=`` when building the client.

    Returns an empty list when disabled, so the call site is unconditional::

        AsyncIOMotorClient(url, event_listeners=mongo_monitor.listeners(), ...)

    Registered at client construction rather than through
    ``pymongo.monitoring.register()`` (the global registry) because the global
    form also instruments every throwaway client the test suite and the backup
    scripts create, mixing their commands into the application's series.
    """
    if not _PYMONGO_AVAILABLE:  # pragma: no cover - driver-less environment
        return []
    if not enabled():
        logger.info("MongoDB command metrics disabled by MONGO_COMMAND_METRICS")
        return []
    return [_command_listener, _pool_listener]


def reset_for_tests() -> None:
    """Clear the command-name allow-list. Test support only."""
    with _seen_lock:
        _seen_commands.clear()
    _pool_listener._checked_out = 0  # noqa: SLF001 - deliberate test-support access
    _pool_listener._max = 0  # noqa: SLF001

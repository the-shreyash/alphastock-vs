"""Supervised background tasks (PH3.6).

THE TWO BUGS THIS MODULE EXISTS TO FIX
--------------------------------------
Every perpetual loop in this backend was started the same way::

    asyncio.create_task(market_broadcast_loop())

and the returned task was thrown away. That single line carries two distinct
defects, and the second one is the expensive one.

**1. The reference.** ``asyncio`` keeps only a *weak* reference to a running
task. The documentation is explicit about the consequence — "save a reference to
the result of this function, to avoid a task disappearing mid-execution" — and a
loop that vanishes leaves no exception, no log line and no traceback. It simply
stops producing market broadcasts, and the symptom, days later, is "the
dashboard went quiet", which nobody attributes to garbage collection.

**2. The shutdown.** A task nobody holds is a task nobody can cancel. The
application's ``shutdown()`` handler closed the scheduler, the broker streams,
the Redis pool, the HTTP pool and finally the Mongo client — while four loops
kept running against all of them. Each one then raised inside its own
``except Exception`` and logged an error, so a *clean* stop emitted a burst of
connection failures that is indistinguishable in the logs from a crash. Worse,
`market_broadcast_loop` and `_price_stream_loop` both perform Mongo reads, so a
loop that wakes up after ``client.close()`` is doing I/O against a closed client
during the 30s ``stop_grace_period``.

WHAT THIS GUARANTEES
--------------------
**A strong reference for the task's whole life**, released on completion so a
task that ends on its own is not retained forever — the registry must not become
the leak it was written to prevent.

**Deterministic cancellation, bounded.** ``cancel_all()`` cancels every task and
waits, but only for ``SHUTDOWN_GRACE_SECONDS``. A shutdown that blocks forever on
a loop stuck in a socket read gets SIGKILLed by the orchestrator, and SIGKILL is
precisely what the compose file's ``stop_grace_period`` exists to avoid.

**One task per name.** ``spawn()`` refuses to start a second task under a name
that is already running and closes the coroutine it was handed, so a retried
boot cannot leave two market-broadcast loops publishing to the same sockets.
Duplicate delivery is markedly harder to notice than no delivery — this is the
same rule, for the same reason, that ``infrastructure/redis_pubsub.py`` applies
to Pub/Sub subscriptions.

**A crashed task is visible.** A supervised task that raises is logged with its
traceback rather than being swallowed into a never-retrieved exception. This
module does NOT restart it: every loop here already has its own
``try/except`` inside ``while True``, so reaching this handler means the loop
structure itself failed, and silently respawning a loop that cannot run is how a
crash loop gets built.

WHAT IT DELIBERATELY IS NOT
---------------------------
Not a scheduler (APScheduler owns cron — see ``services/scheduler.py``), not a
work queue, and not a supervisor with restart policy. It is a registry with a
shutdown path, which is the thing that was missing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Coroutine, Dict, List, Optional

from observability import instruments

logger = logging.getLogger(__name__)

#: How long ``cancel_all()`` waits for tasks to finish after cancelling them.
#: Comfortably inside the 30s ``stop_grace_period`` in docker-compose.yml, with
#: room left for the Redis, HTTP-pool and Mongo teardown that follows it.
SHUTDOWN_GRACE_SECONDS = 5.0


class BackgroundTaskRegistry:
    """Named, supervised asyncio tasks with a bounded shutdown."""

    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task] = {}
        # Start times, for the lifetime histogram (PH3.7). Keyed by the task
        # object's id rather than its name, so a task that is restarted under
        # the same name cannot inherit the previous run's start time and report
        # a lifetime spanning both. Entries are removed in `_on_done`, which
        # runs for every terminal state, so this cannot grow.
        self._started_at: Dict[int, float] = {}

    # -- lifecycle ----------------------------------------------------------- #
    def spawn(self, name: str, coro: Coroutine[Any, Any, Any]) -> Optional[asyncio.Task]:
        """Start `coro` as a supervised task named `name`.

        Returns the task, or None when a task under that name is already
        running — in which case the coroutine passed in is closed rather than
        left un-awaited, which would otherwise emit a
        "coroutine was never awaited" RuntimeWarning at an unrelated point in
        the program and send whoever reads it looking in the wrong place.
        """
        existing = self._tasks.get(name)
        if existing is not None and not existing.done():
            logger.warning(
                "Background task %r already running — not starting a second", name,
                extra={"event": "background_task_duplicate", "task": name},
            )
            coro.close()
            return None

        task = asyncio.create_task(coro, name=name)
        self._tasks[name] = task
        self._started_at[id(task)] = time.monotonic()
        task.add_done_callback(self._on_done)
        logger.info(
            "Background task started: %s", name,
            extra={"event": "background_task_started", "task": name},
        )
        instruments.record_task_start(name)
        return task

    def _on_done(self, task: asyncio.Task) -> None:
        """Release the reference and surface an unexpected exit.

        Registered as a done-callback so a task that ends on its own is dropped
        from the registry — without this the registry would retain every task
        object (and its frames, and everything they close over) for the life of
        the process, which is the shape of leak this module is meant to remove.
        """
        name = task.get_name()
        if self._tasks.get(name) is task:
            del self._tasks[name]
        # Popped unconditionally, before any early return below: `_on_done` runs
        # for every terminal state including cancellation, and a start time left
        # behind on the cancelled path would be the exact shape of leak this
        # module exists to prevent (PH3.6).
        started = self._started_at.pop(id(task), None)
        lifetime = None if started is None else max(0.0, time.monotonic() - started)

        if task.cancelled():
            instruments.record_task_end(name, "cancelled", lifetime)
            return
        exc = task.exception()
        if exc is not None:
            instruments.record_task_end(name, "failed", lifetime)
        else:
            instruments.record_task_end(name, "completed", lifetime)
        if exc is not None:
            # Retrieved deliberately: an unretrieved task exception is reported
            # by asyncio at garbage-collection time, detached from the moment it
            # happened and from any request context.
            logger.error(
                "Background task %r exited with an exception", name,
                exc_info=exc,
                extra={"event": "background_task_failed", "task": name},
            )
        else:
            logger.info(
                "Background task %r finished", name,
                extra={"event": "background_task_finished", "task": name},
            )

    async def cancel(self, name: str) -> bool:
        """Cancel one task by name and wait for it. Returns True if it existed."""
        task = self._tasks.get(name)
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=SHUTDOWN_GRACE_SECONDS)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:  # pragma: no cover - defensive
            pass
        return True

    async def cancel_all(self) -> int:
        """Cancel every task and wait, bounded by SHUTDOWN_GRACE_SECONDS.

        Returns the number of tasks that were still running. Never raises:
        shutdown must complete even if a task's cancellation path is broken.
        """
        tasks: List[asyncio.Task] = [t for t in self._tasks.values() if not t.done()]
        if not tasks:
            return 0
        for task in tasks:
            task.cancel()
        try:
            await asyncio.wait(tasks, timeout=SHUTDOWN_GRACE_SECONDS)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Background task shutdown wait raised (ignored): %s", exc)
        stragglers = [t.get_name() for t in tasks if not t.done()]
        if stragglers:
            # Named, because "shutdown took the full grace period" is only
            # actionable if you know which loop refused to stop.
            logger.warning(
                "Background tasks did not stop within %.1fs: %s",
                SHUTDOWN_GRACE_SECONDS, ", ".join(stragglers),
                extra={"event": "background_task_shutdown_timeout"},
            )
        else:
            logger.info(
                "Cancelled %d background task(s)", len(tasks),
                extra={"event": "background_tasks_cancelled", "count": len(tasks)},
            )
        self._tasks.clear()
        return len(tasks)

    # -- introspection -------------------------------------------------------- #
    @property
    def running(self) -> List[str]:
        return sorted(name for name, t in self._tasks.items() if not t.done())

    def stats(self) -> Dict[str, Any]:
        """Snapshot for the resource probe and diagnostics."""
        return {"count": len(self._tasks), "running": self.running}

    def reset_for_tests(self) -> None:
        """Drop the registry without awaiting. Tests that spawned real tasks
        must call :meth:`cancel_all` instead."""
        self._tasks.clear()
        self._started_at.clear()


#: Process-wide registry. One per process, like the Redis manager.
registry = BackgroundTaskRegistry()


def spawn(name: str, coro: Coroutine[Any, Any, Any]) -> Optional[asyncio.Task]:
    return registry.spawn(name, coro)


async def cancel_all() -> int:
    return await registry.cancel_all()


def stats() -> Dict[str, Any]:
    return registry.stats()

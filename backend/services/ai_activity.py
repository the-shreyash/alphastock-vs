"""AI live-activity emitter (Sprint R7).

Turns a blocking AI call into a *visible* one. Instead of the frontend showing a
static "Thinking…", the AI pipeline publishes a truthful, run-correlated step
timeline that streams to the user in real time:

    Recalling your context  →  Consulting Claude  →  Composing your answer  →  Done

The timeline is delivered over the existing event bus → WebSocket bridge. Every
event carries a ``user_id`` so the bridge routes it to that user's sockets only
(see services/realtime/event_bridge.py), and a ``run_id`` so the client can
correlate a burst of steps with the specific request it fired.

Events (all on the ``ai`` domain / socket channel):

    ai.run.started    { user_id, run_id, session_id, steps:[label…], total,
                        started_at }
    ai.step           { user_id, run_id, session_id, index, total, label,
                        status: "running" | "done" | "warning" }
    ai.run.completed  { user_id, run_id, session_id, status: "done" | "warning",
                        duration_ms }

Design rules, per REALTIME_SYSTEM.md → "AI Thinking Process" / "Never fake AI
progress":

  * Steps must map to work the code actually performs — the labels are supplied
    by the caller, one per real stage.
  * Telemetry is best-effort: a publish failure is logged and swallowed so the
    live timeline can never break the AI response itself.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from services.market_engine.event_bus import event_bus

logger = logging.getLogger(__name__)

# Event names — kept as constants so producers/consumers agree on the contract.
EVENT_RUN_STARTED = "ai.run.started"
EVENT_STEP = "ai.step"
EVENT_RUN_COMPLETED = "ai.run.completed"

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_WARNING = "warning"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Step:
    """Async context manager for a single stage of an :class:`AIRun`.

    On enter it marks the stage ``running``; on a clean exit ``done``; on an
    exception ``warning`` (and the exception is re-raised so the caller's own
    error handling still runs). All emission is best-effort.

    A stage that handles its own failure — degrading a section rather than
    aborting the pipeline — must call :meth:`warn`, so the timeline reports the
    partial result honestly instead of a ``done`` for work that did not succeed::

        async with run.step() as step:
            try:
                data = await fetch()
            except Exception:
                step.warn()      # section degrades; run continues
                data = []
    """

    def __init__(self, run: "AIRun", index: int) -> None:
        self._run = run
        self._index = index
        self._degraded = False

    def warn(self) -> None:
        """Mark this stage as degraded — it finishes ``warning``, not ``done``."""
        self._degraded = True
        self._run._degraded = True

    async def __aenter__(self) -> "_Step":
        await self._run._emit_step(self._index, STATUS_RUNNING)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        degraded = bool(exc_type) or self._degraded
        if exc_type:
            self._run._degraded = True
        await self._run._emit_step(
            self._index, STATUS_WARNING if degraded else STATUS_DONE
        )
        return False  # never suppress the caller's exception


class AIRun:
    """A correlated, per-user AI step timeline.

    Usage::

        run = AIRun(user_id, session_id, ["Recalling your context",
                                          "Consulting Claude",
                                          "Composing your answer"])
        await run.start()
        async with run.step():          # "Recalling your context"
            ...
        async with run.step():          # "Consulting Claude"
            ...
        await run.complete()

    ``step()`` advances through the labels in order. Emission never raises, so
    the surrounding AI logic is unaffected if the event bus/WebSocket layer is
    unavailable.
    """

    def __init__(
        self,
        user_id: object,
        session_id: Optional[str],
        steps: List[str],
        run_id: Optional[str] = None,
    ) -> None:
        self.user_id = str(user_id) if user_id is not None else None
        self.session_id = session_id
        self.steps = list(steps or [])
        self.total = len(self.steps)
        self.run_id = run_id or uuid.uuid4().hex
        self._cursor = -1
        self._t0 = time.monotonic()
        self._completed = False
        # Set when any step failed or degraded, so complete() can default the
        # run's status to `warning` rather than claiming an unqualified success.
        self._degraded = False

    # ---- internal emission (best-effort) ----
    async def _publish(self, event_type: str, data: dict) -> None:
        try:
            await event_bus.publish(event_type, data)
        except Exception as exc:  # telemetry must never break the AI call
            logger.debug("AIRun publish failed for %s: %s", event_type, exc)

    def _base(self) -> dict:
        return {
            "user_id": self.user_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
        }

    async def _emit_step(self, index: int, status: str) -> None:
        if index < 0 or index >= self.total:
            return
        await self._publish(
            EVENT_STEP,
            {
                **self._base(),
                "index": index,
                "total": self.total,
                "label": self.steps[index],
                "status": status,
            },
        )

    # ---- public API ----
    async def start(self) -> "AIRun":
        """Announce the run and the full step plan (all steps pending)."""
        if not self.total:
            return self
        await self._publish(
            EVENT_RUN_STARTED,
            {
                **self._base(),
                "steps": self.steps,
                "total": self.total,
                "started_at": _now_iso(),
            },
        )
        return self

    def step(self) -> _Step:
        """Return a context manager for the next stage (running → done/warning)."""
        self._cursor += 1
        return _Step(self, self._cursor)

    async def complete(self, status: str = STATUS_DONE) -> None:
        """Close the run. Safe to call once; further calls are ignored.

        A run in which any step degraded completes ``warning`` even when the
        caller asks for ``done`` — the pipeline produced a partial result and
        the timeline must say so.
        """
        if self._completed or not self.total:
            return
        self._completed = True
        if status == STATUS_DONE and self._degraded:
            status = STATUS_WARNING
        await self._publish(
            EVENT_RUN_COMPLETED,
            {
                **self._base(),
                "status": status,
                "duration_ms": int((time.monotonic() - self._t0) * 1000),
            },
        )

#!/usr/bin/env python3
"""Measure whether the backend's in-process resources return to baseline (PH3.6).

WHAT THIS ANSWERS THAT THE LOAD HARNESS DOES NOT
------------------------------------------------
`scripts/load/load-test.sh` drives a real server over HTTP and reports RSS and
open file descriptors from the outside. That is the right instrument for
throughput and for the resources the operating system can see, and PH3.5 used it
to establish that neither grows over a few minutes.

It cannot see the structures that actually leak in this application. A Python
dict that gains one key per WebSocket connection and never drops it costs a few
hundred bytes; a hundred thousand connections later it is the largest object in
the process, and for the entire first hour it is invisible under allocator
noise — RSS moves more than that between two idle samples. **A leak is a shape,
not a size**, and the shape is only legible if you count the entries.

So this probe drives the application's own objects in-process and reports the
one number that distinguishes a cache from a leak: does the structure return
toward its starting size when the activity that filled it stops?

    baseline -> load -> settle -> baseline'

`baseline' > baseline` after the settle phase is the finding. `peak > baseline`
during the load phase is just the program working.

RSS IS REPORTED AND IS NOT THE VERDICT
--------------------------------------
Process RSS is printed because it is what an operator sees on a dashboard, but
no conclusion here rests on it. Python returns freed arenas to the allocator,
not always to the OS; RSS therefore falls late, partially, or not at all, and
reading a flat RSS as "no leak" is exactly the mistake PH3.5 §18 warned this
sprint against making in the other direction. The structure counts are the
evidence. RSS is context.

USAGE
-----
    cd backend && python scripts/resource_probe.py
    cd backend && python scripts/resource_probe.py --cycles 5000
    cd backend && python scripts/resource_probe.py --json out.json

Runs fully hermetic: FakeDB, no network, no Redis, no server. Exit status is 1
if any tracked structure fails to return to its baseline, so it is usable as a
check and not only as a report.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _testenv  # noqa: E402

_testenv.apply()

import server  # noqa: E402
from infrastructure import tasks as background_tasks  # noqa: E402
from services import ai_context_builder, cache, portfolio_stream, trade_stream  # noqa: E402
from services.brokers.stream import stream_manager  # noqa: E402
from services.market_engine.event_bus import event_bus  # noqa: E402


# --------------------------------------------------------------------------- #
# Snapshot                                                                      #
# --------------------------------------------------------------------------- #
def snapshot() -> Dict[str, Any]:
    """One reading of every resource this sprint tracks.

    Structure counts first, because they are the evidence; process counters
    last, because they are context. Every value is cheap to read — the probe
    must not perturb what it measures.
    """
    snap: Dict[str, Any] = {
        # WebSocket manager: three parallel maps, all of which must empty.
        "ws_active": len(server.ws_manager.active),
        "ws_channels": len(server.ws_manager.channels),
        "ws_user_keys": len(server.ws_manager.user_connections),
        # Caches and per-user maps.
        "ai_context_entries": ai_context_builder.cache_stats()["entries"],
        "cache_memory_keys": len(cache._memory),
        "portfolio_throttle_users": portfolio_stream.throttle_stats()["tracked_users"],
        "trade_throttle_users": trade_stream.throttle_stats()["tracked_users"],
        # Event plumbing.
        "event_bus_handlers": event_bus.subscriber_count,
        "event_bus_log": len(event_bus.recent_events(limit=10_000)),
        # Task and stream registries.
        "background_tasks": background_tasks.stats()["count"],
        "broker_streams": len(stream_manager._streams),
    }

    try:
        snap["asyncio_tasks"] = len(asyncio.all_tasks())
    except RuntimeError:  # not inside a loop
        snap["asyncio_tasks"] = 0

    try:
        import psutil

        proc = psutil.Process()
        snap["rss_mb"] = round(proc.memory_info().rss / 1024 / 1024, 1)
        snap["open_fds"] = proc.num_fds()
        snap["threads"] = proc.num_threads()
    except Exception:  # pragma: no cover - psutil restricted
        snap["rss_mb"] = None
        snap["open_fds"] = None
        snap["threads"] = None

    return snap


# The two correctness classes, and keeping them apart is the point.
#
# A bounded cache that still holds entries when the run ends is WORKING. Judging
# it by "did it return to zero" would report every cache in the process as a
# leak and, worse, would train the reader to ignore this section. The question a
# cache has to answer is different: did it stay under its own ceiling?

#: Lifecycle structures. Every entry corresponds to a live thing — a socket, a
#: handler, a task, a stream — so once the activity stops, the count must come
#: back to where it started. Anything left is something that was created and
#: never released.
MUST_RETURN = (
    "ws_active",
    "ws_channels",
    "ws_user_keys",
    "event_bus_handlers",
    "background_tasks",
    "broker_streams",
)

#: Bounded caches: (metric, ceiling). Judged against their documented bound, not
#: against baseline. A cache over its own ceiling means the eviction path did not
#: run — which is precisely the thing a constant in the source cannot prove and
#: only a run can.
MUST_STAY_BOUNDED = (
    ("ai_context_entries", ai_context_builder._CACHE_MAX_ENTRIES),
    ("cache_memory_keys", cache._MEMORY_MAX_KEYS),
    ("portfolio_throttle_users", portfolio_stream._MAX_TRACKED_USERS),
    ("trade_throttle_users", trade_stream._MAX_TRACKED_USERS),
    ("event_bus_log", event_bus._max_log_size),
)


# --------------------------------------------------------------------------- #
# Fakes                                                                         #
# --------------------------------------------------------------------------- #
class _FakeSocket:
    """The three methods `ConnectionManager` calls. Nothing else."""

    __slots__ = ("sent", "dead")

    def __init__(self, dead: bool = False) -> None:
        self.sent = 0
        self.dead = dead

    async def accept(self) -> None:
        return None

    async def send_text(self, payload: str) -> None:
        # A real send yields to the loop; so does this, because the bug class
        # being probed (a container mutated mid-iteration) only appears at an
        # await point.
        await asyncio.sleep(0)
        if self.dead:
            raise ConnectionResetError("socket closed")
        self.sent += 1


# --------------------------------------------------------------------------- #
# Scenarios                                                                     #
# --------------------------------------------------------------------------- #
async def scenario_ws_cycles(cycles: int) -> None:
    """connect -> subscribe -> receive -> disconnect, `cycles` times.

    A distinct `user_id` per cycle on purpose: the id comes from an
    unauthenticated query parameter, so "the same user reconnecting" is the
    optimistic case and "a new key every time" is the one an anonymous client
    can produce at will.
    """
    mgr = server.ws_manager
    for i in range(cycles):
        user_id = f"probe-user-{i}"
        ws = _FakeSocket()
        await mgr.connect(ws, user_id)
        mgr.subscribe(ws, ["market", "trades"])
        await mgr.broadcast_to_channel("market", {"type": "tick", "i": i})
        await mgr.send_to_user(user_id, {"type": "hello"})
        mgr.disconnect(ws, user_id)


async def scenario_ws_dirty_disconnects(cycles: int) -> None:
    """Sockets that die without a clean close, reaped through `broadcast`.

    This is the path a dropped connection actually takes — no `disconnect()`
    call, just a send that raises — and it is a different code path from the
    tidy one above.
    """
    mgr = server.ws_manager
    for batch in range(max(1, cycles // 50)):
        for i in range(50):
            await mgr.connect(_FakeSocket(dead=True), f"probe-dirty-{batch}-{i}")
        await mgr.broadcast({"type": "sweep"})


async def scenario_ws_broadcast_churn(cycles: int) -> None:
    """Broadcast while sockets connect and disconnect concurrently.

    Reproduces PH3.5's L-2: `broadcast` awaited inside a loop over the live
    socket set, so a disconnect landing during the await raised
    `RuntimeError: Set changed size during iteration` and silently dropped the
    message to every client after the mutation point.
    """
    mgr = server.ws_manager
    live = [_FakeSocket() for _ in range(60)]
    for i, ws in enumerate(live):
        await mgr.connect(ws, f"probe-churn-{i}")

    async def churn() -> None:
        for i in range(cycles):
            ws = _FakeSocket()
            await mgr.connect(ws, f"probe-churn-new-{i}")
            await asyncio.sleep(0)
            mgr.disconnect(ws, f"probe-churn-new-{i}")

    await asyncio.gather(mgr.broadcast({"type": "market_update"}), churn())
    for i, ws in enumerate(live):
        mgr.disconnect(ws, f"probe-churn-{i}")


async def scenario_ai_context(users: int) -> None:
    """One chat-context write per distinct user.

    Drives `_cache_store` rather than `build_chat_context` so the probe measures
    retention without needing a market provider — the assembly path is covered
    by the test suite; what is measured here is what the cache keeps.
    """
    ctx = ai_context_builder.ChatContext(
        text="live market block " * 200,
        live_market_available=True,
        sections={"portfolio": {"holdings": [{"symbol": "X", "qty": 1}] * 20}},
    )
    for i in range(users):
        ai_context_builder._cache_store(f"probe-chat-{i}", ctx)
        await asyncio.sleep(0)


async def scenario_stream_throttle(users: int) -> None:
    """One portfolio + trade emission stamp per distinct user."""
    for i in range(users):
        portfolio_stream._stamp(f"probe-stream-{i}")
        trade_stream._stamp(f"probe-stream-{i}")
        await asyncio.sleep(0)


async def scenario_event_bus(events: int) -> None:
    """Publish through the bus with a subscriber attached, then detach it."""
    seen: List[int] = []

    async def handler(event: Dict[str, Any]) -> None:
        seen.append(1)

    event_bus.subscribe("probe.*", handler)
    try:
        for i in range(events):
            await event_bus.publish("probe.tick", {"i": i})
    finally:
        event_bus.unsubscribe("probe.*", handler)


async def scenario_task_lifecycle(rounds: int) -> None:
    """Spawn and cancel a supervised task repeatedly.

    Checks two things at once: that the registry releases a cancelled task, and
    that spawning under a name already in use does not create a second one.
    """
    async def forever() -> None:
        while True:
            await asyncio.sleep(3600)

    for _ in range(rounds):
        background_tasks.spawn("probe-loop", forever())
        background_tasks.spawn("probe-loop", forever())  # refused; must not leak
        await background_tasks.registry.cancel("probe-loop")


# --------------------------------------------------------------------------- #
# Runner                                                                        #
# --------------------------------------------------------------------------- #
async def run(cycles: int) -> Dict[str, Any]:
    phases: List[Dict[str, Any]] = []

    def mark(label: str) -> Dict[str, Any]:
        gc.collect()
        snap = snapshot()
        snap["phase"] = label
        phases.append(snap)
        return snap

    baseline = mark("T0 baseline")

    await scenario_ws_cycles(cycles)
    mark("T1 ws connect/disconnect")

    await scenario_ws_dirty_disconnects(cycles)
    mark("T2 ws dirty disconnects")

    await scenario_ws_broadcast_churn(min(cycles, 500))
    mark("T3 broadcast under churn")

    await scenario_ai_context(cycles)
    mark("T4 ai chat context")

    await scenario_stream_throttle(cycles)
    mark("T5 portfolio/trade throttle")

    await scenario_event_bus(cycles)
    mark("T6 event bus publish")

    await scenario_task_lifecycle(min(cycles, 200))
    mark("T7 task spawn/cancel")

    # Settle: nothing runs, everything that self-releases gets the chance to.
    for _ in range(5):
        await asyncio.sleep(0)
    settled = mark("T8 settled")

    retained = {
        key: {"baseline": baseline[key], "settled": settled[key]}
        for key in MUST_RETURN
        if settled[key] > baseline[key]
    }
    # Peak across every phase, not the settled value: a cache that briefly
    # exceeded its ceiling and was pruned afterwards still exceeded it.
    unbounded = {
        key: {"peak": max(p[key] for p in phases), "ceiling": ceiling}
        for key, ceiling in MUST_STAY_BOUNDED
        if max(p[key] for p in phases) > ceiling
    }
    bounded = {
        key: {"peak": max(p[key] for p in phases), "ceiling": ceiling}
        for key, ceiling in MUST_STAY_BOUNDED
    }
    return {"phases": phases, "baseline": baseline, "settled": settled,
            "retained": retained, "unbounded": unbounded, "bounded": bounded,
            "cycles": cycles}


COLUMNS = (
    ("ws_active", "act"),
    ("ws_channels", "chan"),
    ("ws_user_keys", "users"),
    ("ai_context_entries", "aictx"),
    ("cache_memory_keys", "cache"),
    ("portfolio_throttle_users", "pthr"),
    ("trade_throttle_users", "tthr"),
    ("event_bus_handlers", "subs"),
    ("background_tasks", "btask"),
    ("asyncio_tasks", "atask"),
    ("broker_streams", "bstrm"),
    ("rss_mb", "rssMB"),
    ("open_fds", "fds"),
    ("threads", "thr"),
)


def render(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    header = f"{'phase':<28}" + "".join(f"{label:>7}" for _, label in COLUMNS)
    lines.append(header)
    lines.append("-" * len(header))
    for snap in result["phases"]:
        row = f"{snap['phase']:<28}"
        for key, _ in COLUMNS:
            value = snap.get(key)
            row += f"{'-' if value is None else value:>7}"
        lines.append(row)

    lines.append("")
    lines.append("LIFECYCLE — must return to baseline once activity stops")
    if result["retained"]:
        for key, delta in result["retained"].items():
            lines.append(f"  FAIL {key}: {delta['baseline']} -> {delta['settled']} retained")
    else:
        lines.append(
            f"  OK   all {len(MUST_RETURN)} structures returned to baseline "
            f"after {result['cycles']} cycles"
        )

    lines.append("")
    lines.append("CACHES — must stay under their own ceiling (peak across all phases)")
    for key, info in result["bounded"].items():
        verdict = "FAIL" if key in result["unbounded"] else "OK  "
        lines.append(f"  {verdict} {key}: peak {info['peak']} / ceiling {info['ceiling']}")

    lines.append("")
    lines.append("RSS is context, not the verdict — see this file's docstring.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cycles", type=int, default=2000,
                        help="iterations per scenario (default: 2000)")
    parser.add_argument("--json", dest="json_path",
                        help="also write the full result to this path")
    args = parser.parse_args()

    result = asyncio.run(run(args.cycles))
    print(render(result))

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(result, indent=2))
        print(f"\nWrote {args.json_path}")

    return 1 if (result["retained"] or result["unbounded"]) else 0


if __name__ == "__main__":
    sys.exit(main())

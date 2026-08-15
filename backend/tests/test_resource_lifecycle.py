"""Resource lifecycle regression tests (PH3.6).

WHAT THESE TESTS ARE FOR, AND WHAT THEY REFUSE TO DO
----------------------------------------------------
Every test here asserts a **structure count**, never a byte count and never a
wall-clock duration. That is deliberate, and it is the same rule PH3.4 §3 set for
the performance suite: an assertion on RSS measures the CI runner's allocator,
the machine's memory pressure and whatever else the runner is doing — it goes red
on a busy box and green on a quiet one that just regressed. A leak in this
application is a container that gains an entry and never loses it, so the entry
count is both the cause and the only stable thing to assert.

The second rule is that a bounded cache and a leaking map are judged
differently. A cache holding entries after a run is working; the question for it
is whether the eviction path *ran*, which a constant in the source cannot show
and only a run past the ceiling can. Several tests below therefore push past the
bound on purpose rather than reading `_CACHE_MAX_ENTRIES` and trusting it.

Each test corresponds to a confirmed finding in
`docs/performance/PH3_MEMORY_STABILITY.md` §16 and fails on the pre-PH3.6 code.

`asyncio.run` per test rather than pytest-asyncio, matching
`tests/test_event_bridge.py` and `tests/test_redis_infrastructure.py`: the suite
has no async plugin, and adding one for this file would make its tests run under
different machinery from every other async test here.
"""
import asyncio
import warnings

import pytest

import server
from infrastructure import tasks as background_tasks
from services import ai_context_builder, portfolio_stream, trade_stream
from services.brokers.stream import BrokerStream, BrokerStreamManager


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Doubles                                                                       #
# --------------------------------------------------------------------------- #
class FakeSocket:
    """The three methods `ConnectionManager` calls, and a delivery counter."""

    def __init__(self, dead=False):
        self.received = []
        self.dead = dead

    async def accept(self):
        return None

    async def send_text(self, payload):
        # A real send suspends. The bug class these tests cover — a container
        # mutated while it is being iterated — exists ONLY at an await point, so
        # a double that never yields would pass against the broken code.
        await asyncio.sleep(0)
        if self.dead:
            raise ConnectionResetError("socket closed")
        self.received.append(payload)


# --------------------------------------------------------------------------- #
# M-1 — ConnectionManager per-user map retention                                #
# --------------------------------------------------------------------------- #
class TestConnectionManagerUserMap:
    """`user_connections` is keyed by an UNAUTHENTICATED query parameter, so a
    key retained per connection is a key an anonymous caller can mint at will."""

    def test_clean_disconnect_drops_the_user_key(self):
        async def scenario():
            mgr = server.ConnectionManager()
            ws = FakeSocket()
            await mgr.connect(ws, "u1")
            assert mgr.user_connections["u1"] == {ws}

            mgr.disconnect(ws, "u1")

            assert "u1" not in mgr.user_connections
            assert mgr.active == set()
            assert mgr.channels == {}

        _run(scenario())

    def test_repeated_cycles_leave_nothing_behind(self):
        """1,000 connect/disconnect cycles with a distinct id each time.

        The pre-PH3.6 code left 1,000 empty sets here — measured, not inferred.
        A distinct id per cycle is the realistic adversarial case, not a
        contrived one: nothing authenticates the id.
        """
        async def scenario():
            mgr = server.ConnectionManager()
            for i in range(1000):
                ws = FakeSocket()
                await mgr.connect(ws, f"user-{i}")
                mgr.subscribe(ws, ["market"])
                mgr.disconnect(ws, f"user-{i}")

            assert mgr.user_connections == {}
            assert mgr.active == set()
            assert mgr.channels == {}

        _run(scenario())

    def test_a_users_other_sockets_survive_one_disconnecting(self):
        """The key must go only when the LAST socket for that user goes.

        Without this, "drop the key when the set is empty" could be satisfied by
        the much simpler and completely wrong "drop the key on any disconnect",
        which would break multi-tab users.
        """
        async def scenario():
            mgr = server.ConnectionManager()
            first, second = FakeSocket(), FakeSocket()
            await mgr.connect(first, "u1")
            await mgr.connect(second, "u1")

            mgr.disconnect(first, "u1")

            assert mgr.user_connections["u1"] == {second}
            await mgr.send_to_user("u1", {"type": "still-here"})
            assert len(second.received) == 1

            mgr.disconnect(second, "u1")
            assert "u1" not in mgr.user_connections

        _run(scenario())

    def test_reap_drops_the_user_key_for_sockets_that_died(self):
        """The dropped-connection path: no `disconnect()` call, just a raising
        send. This is how most connections actually end, and it is a different
        code path from the tidy one above."""
        async def scenario():
            mgr = server.ConnectionManager()
            for i in range(200):
                await mgr.connect(FakeSocket(dead=True), f"dead-{i}")

            await mgr.broadcast({"type": "sweep"})

            assert mgr.active == set()
            assert mgr.channels == {}
            assert mgr.user_connections == {}

        _run(scenario())


# --------------------------------------------------------------------------- #
# M-3 — broadcast iterating a live set (PH3.5 finding L-2)                       #
# --------------------------------------------------------------------------- #
class TestBroadcastUnderChurn:
    def test_broadcast_survives_concurrent_disconnects(self):
        """Every live socket receives the message even while the set is mutated.

        The assertion that matters is the delivery count, not the absence of an
        exception. On the pre-PH3.6 code this raised `RuntimeError: Set changed
        size during iteration` — but had it not raised, it would have silently
        skipped every socket past the mutation point, and only counting
        deliveries catches that.
        """
        async def scenario():
            mgr = server.ConnectionManager()
            live = [FakeSocket() for _ in range(40)]
            for i, ws in enumerate(live):
                await mgr.connect(ws, f"live-{i}")

            async def churn():
                for i in range(40):
                    temp = FakeSocket()
                    await mgr.connect(temp, f"temp-{i}")
                    await asyncio.sleep(0)
                    mgr.disconnect(temp, f"temp-{i}")

            await asyncio.gather(mgr.broadcast({"type": "market_update"}), churn())

            assert all(len(ws.received) == 1 for ws in live)

        _run(scenario())

    def test_send_to_user_survives_concurrent_disconnects(self):
        """`send_to_user` iterated the live per-user set for the same reason."""
        async def scenario():
            mgr = server.ConnectionManager()
            sockets = [FakeSocket() for _ in range(20)]
            for ws in sockets:
                await mgr.connect(ws, "u1")

            async def churn():
                for i in range(20):
                    temp = FakeSocket()
                    await mgr.connect(temp, "u1")
                    await asyncio.sleep(0)
                    mgr.disconnect(temp, "u1")

            await asyncio.gather(mgr.send_to_user("u1", {"type": "x"}), churn())

            assert all(len(ws.received) == 1 for ws in sockets)

        _run(scenario())


# --------------------------------------------------------------------------- #
# M-2 — AI chat-context micro-cache                                             #
# --------------------------------------------------------------------------- #
class TestAIContextCache:
    @pytest.fixture(autouse=True)
    def _clean(self):
        ai_context_builder.reset_cache()
        yield
        ai_context_builder.reset_cache()

    def _ctx(self, marker="x"):
        return ai_context_builder.ChatContext(text=marker, live_market_available=True)

    def test_cache_stays_under_its_ceiling(self):
        """Ten times the ceiling, written as distinct users.

        The pre-PH3.6 cache checked its 8-second TTL on READ only and evicted
        nothing, so this left 5,120 live `ChatContext` objects — each carrying a
        rendered markdown block and the structured sections behind it.
        """
        ceiling = ai_context_builder._CACHE_MAX_ENTRIES
        for i in range(ceiling * 10):
            ai_context_builder._cache_store(f"user-{i}", self._ctx())

        assert ai_context_builder.cache_stats()["entries"] <= ceiling

    def test_expired_entries_are_swept_before_anything_is_evicted(self):
        """A stale entry must be reclaimed in preference to a fresh one.

        Written as a behaviour rather than by inspecting the prune function: the
        fresh entry is the one a user is actively using, and evicting it while
        keeping a 999-second-old one would be a working bound with useless
        semantics.
        """
        ceiling = ai_context_builder._CACHE_MAX_ENTRIES
        stale_at = -1000.0
        for i in range(ceiling):
            ai_context_builder._cache_store(f"stale-{i}", self._ctx(), now=stale_at)
        ai_context_builder._cache_store("fresh", self._ctx("fresh"))

        # Trip the bound so a prune runs.
        ai_context_builder._cache_store("newcomer", self._ctx())

        assert "fresh" in ai_context_builder._cache
        assert ai_context_builder.cache_stats()["entries"] <= ceiling

    def test_a_live_entry_is_still_served_within_its_ttl(self):
        """The bound must not have cost the cache its reason to exist."""
        ai_context_builder._cache_store("u", self._ctx("cached"))
        _stamped, ctx = ai_context_builder._cache["u"]
        assert ctx.text == "cached"


# --------------------------------------------------------------------------- #
# M-5 — per-user emission throttle maps                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("module", [portfolio_stream, trade_stream],
                         ids=["portfolio_stream", "trade_stream"])
class TestStreamThrottleMaps:
    def test_map_stays_under_its_ceiling(self, module):
        module.reset_state()
        try:
            ceiling = module._MAX_TRACKED_USERS
            for i in range(ceiling * 3):
                module._stamp(f"user-{i}")
            assert module.throttle_stats()["tracked_users"] <= ceiling
        finally:
            module.reset_state()

    def test_throttling_still_works_after_bounding(self, module):
        """The bound must not have turned the throttle into a no-op.

        Without this, deleting `_stamp`'s body entirely would pass the ceiling
        test above perfectly.
        """
        module.reset_state()
        try:
            module._stamp("u1", now=100.0)
            assert module._tick_allowed("u1", now=100.5) is False
            assert module._tick_allowed("u1", now=100.0 + module.TICK_EMIT_INTERVAL) is True
        finally:
            module.reset_state()


# --------------------------------------------------------------------------- #
# M-4 — supervised background tasks                                             #
# --------------------------------------------------------------------------- #
async def _forever():
    while True:
        await asyncio.sleep(3600)


class TestBackgroundTaskRegistry:
    def test_registry_holds_a_strong_reference(self):
        """asyncio holds only a weak reference to a running task; the registry
        is what keeps a perpetual loop from being collected mid-execution."""
        async def scenario():
            registry = background_tasks.BackgroundTaskRegistry()
            task = registry.spawn("loop", _forever())
            assert registry.running == ["loop"]
            assert registry._tasks["loop"] is task
            await registry.cancel_all()

        _run(scenario())

    def test_cancel_all_stops_every_task_and_empties_the_registry(self):
        async def scenario():
            registry = background_tasks.BackgroundTaskRegistry()
            for i in range(5):
                registry.spawn(f"loop-{i}", _forever())

            cancelled = await registry.cancel_all()

            assert cancelled == 5
            assert registry.running == []
            assert registry.stats()["count"] == 0

        _run(scenario())

    def test_duplicate_spawn_is_refused_without_leaking_a_coroutine(self):
        """A refused spawn must CLOSE the coroutine it was handed.

        Leaving it un-awaited leaks the frame and emits a
        "coroutine ... was never awaited" RuntimeWarning at whatever unrelated
        point the GC gets to it — which sends the next reader looking in the
        wrong file.
        """
        async def scenario():
            registry = background_tasks.BackgroundTaskRegistry()
            registry.spawn("loop", _forever())
            with warnings.catch_warnings():
                warnings.simplefilter("error", RuntimeWarning)
                assert registry.spawn("loop", _forever()) is None
            assert registry.stats()["count"] == 1
            await registry.cancel_all()

        _run(scenario())

    def test_a_task_that_finishes_is_released(self):
        """The registry must not become the leak it was written to prevent."""
        async def scenario():
            registry = background_tasks.BackgroundTaskRegistry()

            async def quick():
                return None

            registry.spawn("quick", quick())
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert registry.stats()["count"] == 0

        _run(scenario())

    def test_a_task_that_raises_is_released_and_reported(self, caplog):
        async def scenario():
            registry = background_tasks.BackgroundTaskRegistry()

            async def boom():
                raise ValueError("loop structure failed")

            registry.spawn("boom", boom())
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert registry.stats()["count"] == 0

        with caplog.at_level("ERROR"):
            _run(scenario())
        assert any("exited with an exception" in r.message for r in caplog.records)

    def test_cancel_all_is_safe_when_nothing_is_running(self):
        async def scenario():
            registry = background_tasks.BackgroundTaskRegistry()
            assert await registry.cancel_all() == 0

        _run(scenario())


class TestHeartbeatEngineLifecycle:
    def test_start_then_stop_leaves_no_running_task(self, monkeypatch):
        """`start_engine` is a no-op under the suite's DISABLE_BACKGROUND_ENGINE,
        so the flag is lifted for exactly this test — the lifecycle is the thing
        under test and cannot be observed with the engine disabled."""
        from services import heartbeat_engine

        monkeypatch.delenv("DISABLE_BACKGROUND_ENGINE", raising=False)
        monkeypatch.setattr(heartbeat_engine, "_started", False)

        async def scenario():
            heartbeat_engine.start_engine(db=None, ws_manager=None)
            try:
                running = background_tasks.registry.running
                assert heartbeat_engine.HEARTBEAT_TASK in running
                assert heartbeat_engine.PRICE_STREAM_TASK in running
            finally:
                await heartbeat_engine.stop_engine()

            running = background_tasks.registry.running
            assert heartbeat_engine.HEARTBEAT_TASK not in running
            assert heartbeat_engine.PRICE_STREAM_TASK not in running
            assert heartbeat_engine._started is False

        _run(scenario())


# --------------------------------------------------------------------------- #
# M-6 — broker stream registry                                                  #
# --------------------------------------------------------------------------- #
class TestBrokerStreamRegistry:
    def test_discard_removes_a_stream_that_ended_on_its_own(self):
        """The token-expiry path. `discard` rather than `stop_stream` because the
        caller runs inside the stream's own task — `stop_stream` would cancel and
        then await the task doing the calling."""
        mgr = BrokerStreamManager()
        stream = BrokerStream("u1", "zerodha", {"access_token": "expired-token"})
        mgr._streams[("u1", "zerodha")] = stream

        assert mgr.discard("u1", "zerodha") is True
        assert mgr._streams == {}
        assert mgr.status() == []

    def test_discarding_an_unknown_stream_is_a_no_op(self):
        mgr = BrokerStreamManager()
        assert mgr.discard("nobody", "zerodha") is False


# --------------------------------------------------------------------------- #
# M-7 — event bridge subscriber registration                                    #
# --------------------------------------------------------------------------- #
class TestEventBridgeIdempotency:
    def test_starting_the_bridge_twice_delivers_each_event_once(self):
        """Duplicate delivery is harder to notice than no delivery: the UI simply
        updates twice. The assertion is therefore on the delivery count, not on
        the handler count."""
        from services.market_engine.event_bus import event_bus
        from services.realtime import event_bridge

        delivered = []

        class RecordingManager:
            async def broadcast_to_channel(self, channel, message):
                delivered.append(message)

            async def send_to_user(self, user_id, message):
                delivered.append(message)

        async def scenario():
            await event_bridge.start_event_bridge(RecordingManager())
            await event_bridge.start_event_bridge(RecordingManager())

            assert event_bus.subscriber_count == before + 1

            await event_bus.publish("price.updated", {"symbol": "TCS", "price": 1.0})
            assert len(delivered) == 1

        event_bridge.reset_for_tests()
        before = event_bus.subscriber_count
        try:
            _run(scenario())
        finally:
            event_bridge.reset_for_tests()


# --------------------------------------------------------------------------- #
# Step 4 — MongoDB connection configuration                                     #
# --------------------------------------------------------------------------- #
class TestMongoConnectionOptions:
    def test_idle_connections_are_reaped(self):
        """The one driver default PH3.6 changed.

        pymongo's `maxIdleTimeMS` default is None — a pooled connection is never
        closed for being idle, so the pool is a ratchet that only goes up until
        the process restarts.
        """
        assert server.MONGO_CLIENT_OPTIONS["maxIdleTimeMS"] == 60_000

    def test_pool_and_selection_bounds_are_explicit(self):
        """These match pymongo's own defaults. The assertion is that they are
        *stated* — PH2.8's "connection-pool sizing documented" was displaced to
        PH2.8b, so until PH3.6 the deployed configuration existed only as library
        defaults nobody had read."""
        options = server.MONGO_CLIENT_OPTIONS
        assert options["maxPoolSize"] == 100
        assert options["minPoolSize"] == 0
        assert options["serverSelectionTimeoutMS"] == 30_000
        assert options["connectTimeoutMS"] == 20_000

    def test_options_reach_the_driver(self):
        """Without this, the dict above could be a decorative constant that the
        client is never actually built with."""
        pool_options = server.client.options.pool_options
        assert pool_options.max_pool_size == 100
        assert pool_options.max_idle_time_seconds == 60.0


# --------------------------------------------------------------------------- #
# Observability — the bounded structures must be visible in production          #
# --------------------------------------------------------------------------- #
class TestResourceGauges:
    """A bound nobody can observe is a bound nobody will notice breaking.

    PH3.6's two confirmed leaks both grew for the life of a process without
    appearing on any dashboard, because RSS moves more between two idle samples
    than a few thousand dict keys weigh.
    """

    def _series(self):
        from observability import metrics

        return metrics.registry.render_prometheus()

    def test_every_bounded_structure_has_a_series(self):
        rendered = self._series()
        for name in (
            "websocket_connections",
            "websocket_tracked_users",
            "websocket_channel_subscriptions",
            "background_tasks_running",
            "event_bus_subscribers",
        ):
            assert f"\n{name} " in f"\n{rendered}", f"{name} is not exposed"

        for cache in ("ai_chat_context", "market_memory_fallback",
                      "portfolio_throttle", "trade_throttle"):
            assert f'app_cache_entries{{cache="{cache}"}}' in rendered

    def test_the_gauges_report_real_counts(self):
        """Asserts a *change*, not an absolute value.

        A collector that hard-coded zero would satisfy the presence test above
        perfectly, and zero is the value these gauges hold almost all the time.
        """
        from observability import metrics

        async def scenario():
            sockets = [FakeSocket() for _ in range(3)]
            for i, ws in enumerate(sockets):
                await server.ws_manager.connect(ws, f"gauge-user-{i}")

        try:
            _run(scenario())
            rendered = self._series()
            assert "\nwebsocket_connections 3\n" in f"\n{rendered}\n"
            assert "\nwebsocket_tracked_users 3\n" in f"\n{rendered}\n"
        finally:
            for ws in list(server.ws_manager.active):
                server.ws_manager.disconnect(ws, None)
            server.ws_manager.active.clear()
            server.ws_manager.channels.clear()
            server.ws_manager.user_connections.clear()

        rendered = self._series()
        assert "\nwebsocket_connections 0\n" in f"\n{rendered}\n"
        assert "\nwebsocket_tracked_users 0\n" in f"\n{rendered}\n"

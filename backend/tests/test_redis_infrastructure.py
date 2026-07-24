"""PH2.7 — Production Redis infrastructure tests (hermetic, no real Redis).

WHY THERE IS NO REAL REDIS HERE
-------------------------------
Every behaviour this sprint added is a *failure* behaviour: the circuit opens
after N connection errors, the subscriber reconnects with backoff after being
dropped, the cache falls back when a command fails, the breaker half-opens and
closes again on recovery. Those are exactly the states a real Redis will not
enter on demand. A test that needs `docker kill redis` to exercise its assertion
either does not run in CI or runs flakily, and the reliability code — the code
that only executes during an incident — ends up as the least-tested code in the
repository. That is precisely backwards.

So the client is a fake whose failure schedule the test dictates. What that
cannot verify is the wire protocol, and it does not need to: redis-py owns that.
What it verifies is the state machine this sprint wrote, which is what can be
wrong.

The manual verification that DOES need a live server (restart recovery, AOF
reload, measured latency) is scripted in docs/infrastructure/REDIS.md §Verification.
"""
import asyncio
import json

import pytest

from infrastructure import redis_client, redis_pubsub
from infrastructure.redis_client import CLOSED, HALF_OPEN, OPEN, RedisManager, RedisSettings


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Fakes                                                                         #
# --------------------------------------------------------------------------- #
class FakeConnectionError(Exception):
    """Stands in for redis.exceptions.ConnectionError.

    The manager classifies errors through `is_connection_error`, which consults
    the real redis exception hierarchy — so tests that want connection-level
    behaviour must raise something that hierarchy recognises. OSError is in the
    tuple and needs no redis import, which keeps these tests runnable even if
    redis-py is absent.
    """


class FakePool:
    def __init__(self, max_connections=24):
        self.max_connections = max_connections
        self._in_use_connections = set()
        self._available_connections = []


class FakeRedis:
    """A Redis client whose failure schedule the test controls."""

    def __init__(self, *, fail_ping=False, fail_times=0):
        self.connection_pool = FakePool()
        self.fail_ping = fail_ping
        self.fail_times = fail_times
        self.calls = []
        self.closed = False
        self.store = {}

    def _maybe_fail(self):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OSError("connection refused")

    async def ping(self):
        self.calls.append("ping")
        if self.fail_ping:
            raise OSError("connection refused")
        self._maybe_fail()
        return True

    async def get(self, key):
        self.calls.append(("get", key))
        self._maybe_fail()
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.calls.append(("set", key, ex))
        self._maybe_fail()
        self.store[key] = value
        return True

    async def delete(self, key):
        self.calls.append(("delete", key))
        self._maybe_fail()
        self.store.pop(key, None)
        return 1

    async def mget(self, keys):
        self.calls.append(("mget", tuple(keys)))
        self._maybe_fail()
        return [self.store.get(k) for k in keys]

    async def publish(self, channel, message):
        self.calls.append(("publish", channel))
        self._maybe_fail()
        return 1

    async def info(self):
        self.calls.append("info")
        self._maybe_fail()
        return {
            "redis_version": "7.2.5",
            "uptime_in_seconds": 120,
            "connected_clients": 4,
            "used_memory": 1048576,
            "maxmemory": 268435456,
            "maxmemory_policy": "allkeys-lru",
            "evicted_keys": 7,
            "expired_keys": 11,
            "rejected_connections": 0,
            "aof_enabled": 1,
            "aof_last_write_status": "ok",
        }

    async def aclose(self):
        self.closed = True


def make_manager(monkeypatch, client=None, **settings_overrides):
    """A manager wired to a fake client, with the environment neutralised."""
    monkeypatch.setenv("REDIS_URL", "redis://:pw@redis:6379/0")
    settings = RedisSettings.from_env({"REDIS_URL": "redis://:pw@redis:6379/0"})
    for key, value in settings_overrides.items():
        object.__setattr__(settings, key, value)
    manager = RedisManager(settings)
    fake = client if client is not None else FakeRedis()
    monkeypatch.setattr(manager, "_build_client", lambda: fake)
    return manager, fake


# --------------------------------------------------------------------------- #
# Configuration                                                                 #
# --------------------------------------------------------------------------- #
def test_settings_use_documented_defaults():
    s = RedisSettings.from_env({"REDIS_URL": "redis://x"})
    assert s.max_connections == 24
    assert s.connect_timeout == 1.5
    assert s.socket_timeout == 2.0
    assert s.health_check_interval == 30.0
    assert s.retry_attempts == 2
    assert s.circuit_failure_threshold == 5
    assert s.circuit_reset_seconds == 10.0


def test_settings_clamp_and_survive_garbage():
    """A typo in an ops variable must not stop a trading backend from booting."""
    s = RedisSettings.from_env({
        "REDIS_URL": "redis://x",
        "REDIS_MAX_CONNECTIONS": "not-a-number",
        "REDIS_CIRCUIT_RESET_SECONDS": "99999",     # above the clamp
        "REDIS_CONNECT_TIMEOUT_SECONDS": "0.0001",  # below the clamp
    })
    assert s.max_connections == 24      # fell back to the default
    assert s.circuit_reset_seconds == 600.0
    assert s.connect_timeout == 0.1


def test_unconfigured_when_no_url():
    assert RedisSettings.from_env({}).configured is False


@pytest.mark.parametrize("url,expected", [
    ("redis://:hunter2@redis:6379/0", "redis://***@redis:6379/0"),
    ("redis://user:pw@10.0.0.5:6380/1", "redis://***@10.0.0.5:6380/1"),
    ("redis://redis:6379/0", "redis://redis:6379/0"),
    ("", ""),
])
def test_sanitized_url_never_leaks_the_password(url, expected):
    """redis-py stringifies connection errors with the target in them, and this
    payload is served over HTTP. The redaction is a security control."""
    assert redis_client.sanitized_url(url) == expected
    assert "hunter2" not in redis_client.sanitized_url(url)


# --------------------------------------------------------------------------- #
# Connection management                                                         #
# --------------------------------------------------------------------------- #
def test_client_is_built_once_and_reused(monkeypatch):
    """The pool exists to be reused; rebuilding per call would defeat it."""
    manager, fake = make_manager(monkeypatch)

    async def run():
        a = await manager.get_client()
        b = await manager.get_client()
        return a, b

    a, b = _run(run())
    assert a is b is fake
    assert fake.calls.count("ping") == 1  # verified once, at construction


def test_concurrent_cold_start_builds_one_client(monkeypatch):
    """N concurrent requests on a cold process must not build N pools."""
    manager, _ = make_manager(monkeypatch)
    built = []
    original = manager._build_client

    def counting_build():
        built.append(1)
        return original()

    monkeypatch.setattr(manager, "_build_client", counting_build)

    async def run():
        return await asyncio.gather(*(manager.get_client() for _ in range(10)))

    clients = _run(run())
    assert len(built) == 1
    assert all(c is clients[0] for c in clients)


def test_get_client_returns_none_when_unconfigured(monkeypatch):
    manager, _ = make_manager(monkeypatch)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert _run(manager.get_client()) is None


def test_failed_connect_returns_none_and_does_not_raise(monkeypatch):
    """Degradation is a supported path, not an exception."""
    manager, _ = make_manager(monkeypatch, client=FakeRedis(fail_ping=True))
    assert _run(manager.get_client()) is None
    assert manager.stats()["connection_errors_total"] == 1


def test_execute_returns_ok_false_instead_of_raising(monkeypatch):
    manager, fake = make_manager(monkeypatch)

    async def run():
        await manager.get_client()
        fake.fail_times = 1
        return await manager.execute("get", lambda r: r.get("k"), default="fallback")

    ok, value = _run(run())
    assert ok is False
    assert value == "fallback"


def test_pool_stats_survive_a_missing_private_attribute(monkeypatch):
    """redis-py exposes pool occupancy only privately. A library rename must
    degrade the diagnostics endpoint, not break it."""
    fake = FakeRedis()
    del fake.connection_pool._in_use_connections
    manager, _ = make_manager(monkeypatch, client=fake)

    async def run():
        await manager.get_client()
        return manager.pool_stats()

    stats = _run(run())
    assert stats["in_use"] is None
    assert stats["available"] == 0


# --------------------------------------------------------------------------- #
# Circuit breaker                                                               #
# --------------------------------------------------------------------------- #
def test_circuit_opens_after_threshold_consecutive_failures(monkeypatch):
    manager, fake = make_manager(monkeypatch, circuit_failure_threshold=3)

    async def run():
        await manager.get_client()
        states = []
        for _ in range(3):
            fake.fail_times = 1
            await manager.execute("get", lambda r: r.get("k"))
            states.append(manager.circuit_state)
        return states

    states = _run(run())
    assert states == [CLOSED, CLOSED, OPEN]


def test_open_circuit_short_circuits_without_touching_redis(monkeypatch):
    """The whole point: while open, a command costs microseconds, not a full
    connect timeout."""
    manager, fake = make_manager(monkeypatch, circuit_failure_threshold=1,
                                 circuit_reset_seconds=600)

    async def run():
        await manager.get_client()
        fake.fail_times = 1
        await manager.execute("get", lambda r: r.get("k"))
        assert manager.circuit_state == OPEN
        before = len(fake.calls)
        ok, _ = await manager.execute("get", lambda r: r.get("k"))
        return ok, before, len(fake.calls)

    ok, before, after = _run(run())
    assert ok is False
    assert before == after  # no command was sent


def test_a_single_failure_does_not_open_the_circuit(monkeypatch):
    """This is the regression test for the pre-PH2.7 `_redis_failed` latch: one
    transient blip used to disable Redis for the life of the process."""
    manager, fake = make_manager(monkeypatch, circuit_failure_threshold=5)

    async def run():
        await manager.get_client()
        fake.fail_times = 1
        await manager.execute("get", lambda r: r.get("k"))
        state_after_blip = manager.circuit_state
        ok, _ = await manager.execute("get", lambda r: r.get("k"))
        return state_after_blip, ok, manager.circuit_state

    state_after_blip, ok, final = _run(run())
    assert state_after_blip == CLOSED
    assert ok is True          # the very next command still went to Redis
    assert final == CLOSED


def test_success_resets_the_consecutive_failure_count(monkeypatch):
    """Four failures spread around a success must not add up to five."""
    manager, fake = make_manager(monkeypatch, circuit_failure_threshold=3)

    async def run():
        await manager.get_client()
        for _ in range(2):
            fake.fail_times = 1
            await manager.execute("get", lambda r: r.get("k"))
        await manager.execute("get", lambda r: r.get("k"))  # success
        for _ in range(2):
            fake.fail_times = 1
            await manager.execute("get", lambda r: r.get("k"))
        return manager.circuit_state

    assert _run(run()) == CLOSED


def test_command_errors_do_not_open_the_circuit(monkeypatch):
    """A WRONGTYPE is a healthy server answering a buggy call site. Counting it
    would let one code bug disable the cache for everything."""
    manager, _ = make_manager(monkeypatch, circuit_failure_threshold=2)

    async def boom(_r):
        raise ValueError("WRONGTYPE Operation against a key holding the wrong kind of value")

    async def run():
        await manager.get_client()
        for _ in range(5):
            await manager.execute("get", boom)
        return manager.circuit_state, manager.stats()

    state, stats = _run(run())
    assert state == CLOSED
    assert stats["failures_total"] == 5
    assert stats["connection_errors_total"] == 0


def test_circuit_half_opens_after_cooldown_then_closes_on_recovery(monkeypatch):
    """The behaviour the old latch never had: it notices the dependency came back."""
    manager, fake = make_manager(monkeypatch, circuit_failure_threshold=1,
                                 circuit_reset_seconds=0.05)

    async def run():
        await manager.get_client()
        fake.fail_times = 1
        await manager.execute("get", lambda r: r.get("k"))
        assert manager.circuit_state == OPEN

        await asyncio.sleep(0.08)          # cooldown elapses
        # The pool was dropped when the circuit opened, so this rebuilds it.
        ok, _ = await manager.execute("get", lambda r: r.get("k"))
        return ok, manager.circuit_state

    ok, state = _run(run())
    assert ok is True
    assert state == CLOSED


def test_failed_trial_reopens_the_circuit_immediately(monkeypatch):
    """A half-open trial that fails goes straight back to OPEN with a fresh
    cooldown — it does not need to re-reach the threshold."""
    manager, fake = make_manager(monkeypatch, circuit_failure_threshold=1,
                                 circuit_reset_seconds=0.05)

    async def run():
        await manager.get_client()
        fake.fail_times = 1
        await manager.execute("get", lambda r: r.get("k"))
        await asyncio.sleep(0.08)
        fake.fail_ping = True              # the trial's reconnect fails
        ok, _ = await manager.execute("get", lambda r: r.get("k"))
        return ok, manager.circuit_state

    ok, state = _run(run())
    assert ok is False
    assert state == OPEN


def test_opening_the_circuit_drops_the_pool(monkeypatch):
    """Every pooled connection is presumed dead. Keeping them means the trial
    likely picks a stale one and fails for the wrong reason — so the breaker
    would never close."""
    manager, fake = make_manager(monkeypatch, circuit_failure_threshold=1)

    async def run():
        await manager.get_client()
        fake.fail_times = 1
        await manager.execute("get", lambda r: r.get("k"))
        await asyncio.sleep(0)  # let the scheduled teardown task run
        return manager.stats()["connected"], fake.closed

    connected, closed = _run(run())
    assert connected is False
    assert closed is True


# --------------------------------------------------------------------------- #
# Diagnostics                                                                   #
# --------------------------------------------------------------------------- #
def test_stats_payload_is_complete_and_redacted(monkeypatch):
    manager, _ = make_manager(monkeypatch)

    async def run():
        await manager.get_client()
        await manager.execute("get", lambda r: r.get("k"))
        return manager.stats()

    stats = _run(run())
    for key in ("configured", "connected", "url", "circuit_state", "commands_total",
                "failures_total", "connection_errors_total", "pool", "settings"):
        assert key in stats
    assert stats["connected"] is True
    assert stats["commands_total"] == 1
    assert "pw" not in stats["url"]
    assert stats["url"] == "redis://***@redis:6379/0"


def test_server_info_is_parsed_and_published(monkeypatch):
    manager, _ = make_manager(monkeypatch)

    async def run():
        return await manager.refresh_server_info()

    info = _run(run())
    assert info["used_memory_bytes"] == 1048576.0
    assert info["maxmemory_bytes"] == 268435456.0
    assert info["connected_clients"] == 4.0
    assert info["maxmemory_policy"] == "allkeys-lru"
    assert info["sampled_at"] > 0


def test_server_info_is_empty_when_redis_is_down(monkeypatch):
    """A stale gauge is worse than no gauge — the caller must be able to tell."""
    manager, _ = make_manager(monkeypatch, client=FakeRedis(fail_ping=True))
    assert _run(manager.refresh_server_info()) == {}


def test_ping_returns_latency_or_none(monkeypatch):
    manager, _ = make_manager(monkeypatch)
    latency = _run(manager.ping())
    assert latency is not None and latency >= 0

    down, _ = make_manager(monkeypatch, client=FakeRedis(fail_ping=True))
    assert _run(down.ping()) is None


def test_close_is_idempotent(monkeypatch):
    manager, fake = make_manager(monkeypatch)

    async def run():
        await manager.get_client()
        await manager.close()
        await manager.close()
        return fake.closed

    assert _run(run()) is True


# --------------------------------------------------------------------------- #
# Cache facade — behaviour must be unchanged, fallback must engage              #
# --------------------------------------------------------------------------- #
@pytest.fixture
def cache_with_fake(monkeypatch):
    """Point services.cache's module-level manager at a fake."""
    from services import cache as cache_mod

    monkeypatch.setenv("REDIS_URL", "redis://:pw@redis:6379/0")
    manager, fake = make_manager(monkeypatch)
    monkeypatch.setattr(redis_client, "manager", manager)
    cache_mod._memory.clear()
    yield cache_mod, manager, fake
    cache_mod._memory.clear()


def test_cache_roundtrip_through_redis(cache_with_fake):
    cache_mod, _, fake = cache_with_fake

    async def run():
        await cache_mod.cache_set("k", {"v": 1}, ttl=60)
        return await cache_mod.cache_get("k")

    assert _run(run()) == {"v": 1}
    # Went to Redis, and did NOT also populate the in-process fallback: two
    # copies of the same entry with independent TTLs is how replicas diverge.
    assert ("set", "k", 60) in fake.calls
    assert cache_mod._memory == {}


def test_cache_miss_in_redis_does_not_fall_through_to_memory(cache_with_fake):
    """A Redis hit is authoritative *including a miss*. Falling through would
    resurrect a value cached before Redis became the source of truth."""
    cache_mod, _, _ = cache_with_fake
    cache_mod._memory["k"] = {"data": "stale", "ts": __import__(
        "datetime").datetime.now(__import__("datetime").timezone.utc), "ttl": 999}

    assert _run(cache_mod.cache_get("k")) is None


def test_cache_falls_back_to_memory_when_redis_fails(cache_with_fake):
    cache_mod, manager, fake = cache_with_fake

    async def run():
        await manager.get_client()
        fake.fail_times = 1
        await cache_mod.cache_set("k", {"v": 2}, ttl=60)   # write falls back
        fake.fail_times = 1
        return await cache_mod.cache_get("k")              # read falls back

    assert _run(run()) == {"v": 2}
    assert "k" in cache_mod._memory


def test_cache_set_rejects_unserializable_before_touching_redis(cache_with_fake):
    """An encoding bug must not be mistaken for a Redis failure — and must not
    count against the circuit breaker."""
    cache_mod, manager, fake = cache_with_fake

    # A dict with a tuple key. `default=str` rescues unserializable *values* —
    # which is why almost nothing reaches this guard — but it is never consulted
    # for keys, so this raises TypeError inside json.dumps.
    unserializable = {(1, 2): "tuple keys are not JSON"}

    async def run():
        await manager.get_client()
        before = len(fake.calls)
        await cache_mod.cache_set("bad", unserializable, ttl=60)
        return before, len(fake.calls), manager.stats()

    before, after, stats = _run(run())
    assert before == after
    assert stats["connection_errors_total"] == 0
    assert cache_mod._memory == {}


def test_cache_get_many_uses_one_mget(cache_with_fake):
    cache_mod, _, fake = cache_with_fake
    fake.store = {"a": json.dumps(1), "b": json.dumps(2)}

    out = _run(cache_mod.cache_get_many(["a", "b", "missing"]))
    assert out == {"a": 1, "b": 2}
    assert sum(1 for c in fake.calls if isinstance(c, tuple) and c[0] == "mget") == 1


def test_cache_delete_always_clears_the_local_copy(cache_with_fake):
    """A force-refresh that leaves a stale value in this replica's fallback
    appears to have done nothing — on that one replica."""
    cache_mod, manager, fake = cache_with_fake

    async def run():
        await manager.get_client()
        await cache_mod.cache_set("k", 1, ttl=60)
        cache_mod._memory_store("k", "stale", 60)
        fake.fail_times = 1                    # the Redis DELETE fails
        await cache_mod.cache_delete("k")
        return cache_mod._memory

    assert _run(run()) == {}


def test_cache_publish_reports_whether_redis_took_it(cache_with_fake):
    cache_mod, manager, fake = cache_with_fake

    async def run():
        assert await cache_mod.cache_publish("sa:events", {"x": 1}) is True
        fake.fail_times = 1
        return await cache_mod.cache_publish("sa:events", {"x": 2})

    assert _run(run()) is False


def test_cache_is_a_noop_without_redis(monkeypatch):
    """The single-process deployment: no Redis, no errors, no subscriber."""
    from services import cache as cache_mod

    monkeypatch.delenv("REDIS_URL", raising=False)
    manager = RedisManager(RedisSettings.from_env({}))
    monkeypatch.setattr(redis_client, "manager", manager)
    cache_mod._memory.clear()

    async def run():
        await cache_mod.cache_set("k", {"v": 1}, ttl=60)
        value = await cache_mod.cache_get("k")
        published = await cache_mod.cache_publish("sa:events", {"x": 1})
        task = await cache_mod.start_pubsub_listener("sa:events", lambda p: None)
        return value, published, task

    value, published, task = _run(run())
    assert value == {"v": 1}      # served from the in-process store
    assert published is False
    assert task is None
    cache_mod._memory.clear()


def test_legacy_module_attributes_still_exist():
    """Removing a module attribute other code imported is a silent behaviour
    change. These are shims and are documented as such."""
    from services import cache as cache_mod

    assert hasattr(cache_mod, "_redis_client")
    assert hasattr(cache_mod, "_redis_failed")
    assert hasattr(cache_mod, "_get_redis")
    assert cache_mod._MEMORY_MAX_KEYS == 1024


# --------------------------------------------------------------------------- #
# Pub/Sub                                                                       #
# --------------------------------------------------------------------------- #
class FakePubSub:
    """A subscription that yields a scripted sequence, then breaks."""

    def __init__(self, script):
        self.script = list(script)
        self.subscribed = []
        self.unsubscribed = []
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed.append(channel)

    async def unsubscribe(self, channel):
        self.unsubscribed.append(channel)

    async def aclose(self):
        self.closed = True

    async def get_message(self, ignore_subscribe_messages=True, timeout=None):
        if not self.script:
            await asyncio.sleep(0.01)
            return None
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakePubSubClient:
    def __init__(self, pubsub):
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self, ignore_subscribe_messages=True):
        return self._pubsub

    async def aclose(self):
        self.closed = True


def _message(payload):
    return {"type": "message", "data": json.dumps(payload)}


def test_subscriber_delivers_decoded_messages(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://:pw@redis:6379/0")
    received = []
    pubsub = FakePubSub([_message({"n": 1}), _message({"n": 2})])
    sub = redis_pubsub.PubSubSubscriber("sa:events", lambda p: _collect(received, p))
    monkeypatch.setattr(sub, "_connect_client",
                        lambda: _async_return(FakePubSubClient(pubsub)))

    async def run():
        await sub.start()
        await asyncio.sleep(0.05)
        await sub.stop()

    _run(run())
    assert received == [{"n": 1}, {"n": 2}]
    assert pubsub.subscribed == ["sa:events"]


def test_subscriber_reconnects_after_a_dropped_connection(monkeypatch):
    """THE regression test for this sprint. Before PH2.7 the listener ended
    permanently on the first exception and nothing looked broken."""
    monkeypatch.setenv("REDIS_URL", "redis://:pw@redis:6379/0")
    received = []
    connections = [
        FakePubSub([_message({"n": 1}), OSError("connection reset by peer")]),
        FakePubSub([_message({"n": 2})]),
    ]
    made = []

    def connect():
        pubsub = connections[min(len(made), len(connections) - 1)]
        made.append(pubsub)
        return _async_return(FakePubSubClient(pubsub))

    sub = redis_pubsub.PubSubSubscriber("sa:events", lambda p: _collect(received, p))
    monkeypatch.setattr(sub, "_connect_client", connect)
    monkeypatch.setattr(sub, "_backoff", staticmethod(lambda attempt: 0.01))

    async def run():
        await sub.start()
        await asyncio.sleep(0.15)
        stats = sub.stats()
        await sub.stop()
        return stats

    stats = _run(run())
    assert {"n": 1} in received
    assert {"n": 2} in received          # delivered by the SECOND connection
    assert stats["reconnects_total"] >= 1
    assert len(made) >= 2


def test_backoff_grows_exponentially_and_is_capped_and_jittered():
    delays = [redis_pubsub.PubSubSubscriber._backoff(n) for n in range(1, 12)]
    # Bounded by the cap at every attempt, including the far tail.
    assert all(d <= redis_pubsub.BACKOFF_CAP_SECONDS * 1.5 for d in delays)
    # Growth, not a flat retry.
    assert delays[8] > delays[0]
    # Jitter: identical inputs must not produce identical delays across a fleet.
    assert len({round(redis_pubsub.PubSubSubscriber._backoff(5), 6) for _ in range(20)}) > 1


def test_bad_payload_is_dropped_without_killing_the_subscription(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://:pw@redis:6379/0")
    received = []
    pubsub = FakePubSub([
        {"type": "message", "data": "{not json"},
        _message({"n": 1}),
    ])
    sub = redis_pubsub.PubSubSubscriber("sa:events", lambda p: _collect(received, p))
    monkeypatch.setattr(sub, "_connect_client",
                        lambda: _async_return(FakePubSubClient(pubsub)))

    async def run():
        await sub.start()
        await asyncio.sleep(0.05)
        stats = sub.stats()
        await sub.stop()
        return stats

    stats = _run(run())
    assert received == [{"n": 1}]
    assert stats["dropped_total"] == 1
    assert stats["reconnects_total"] == 0    # the connection survived


def test_raising_handler_is_contained(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://:pw@redis:6379/0")
    seen = []

    async def handler(payload):
        seen.append(payload)
        raise RuntimeError("handler exploded")

    pubsub = FakePubSub([_message({"n": 1}), _message({"n": 2})])
    sub = redis_pubsub.PubSubSubscriber("sa:events", handler)
    monkeypatch.setattr(sub, "_connect_client",
                        lambda: _async_return(FakePubSubClient(pubsub)))

    async def run():
        await sub.start()
        await asyncio.sleep(0.05)
        stats = sub.stats()
        await sub.stop()
        return stats

    stats = _run(run())
    assert len(seen) == 2                    # one bad message did not stop the loop
    assert stats["handler_errors_total"] == 2
    assert stats["reconnects_total"] == 0


def test_stop_unsubscribes_and_closes(monkeypatch):
    """A cancelled task skips the clean unsubscribe and leaves a client entry on
    the server until TCP keepalive reaps it."""
    monkeypatch.setenv("REDIS_URL", "redis://:pw@redis:6379/0")
    pubsub = FakePubSub([])
    client = FakePubSubClient(pubsub)
    sub = redis_pubsub.PubSubSubscriber("sa:events", lambda p: _async_return(None))
    monkeypatch.setattr(sub, "_connect_client", lambda: _async_return(client))

    async def run():
        await sub.start()
        await asyncio.sleep(0.03)
        await sub.stop()
        return sub.running, pubsub.unsubscribed, pubsub.closed, client.closed

    running, unsubscribed, ps_closed, client_closed = _run(run())
    assert running is False
    assert unsubscribed == ["sa:events"]
    assert ps_closed is True
    assert client_closed is True


def test_registry_never_creates_a_duplicate_subscription(monkeypatch):
    """Duplicate delivery is harder to notice than no delivery — the UI just
    updates twice."""
    monkeypatch.setenv("REDIS_URL", "redis://:pw@redis:6379/0")
    redis_pubsub.reset_for_tests()
    started = []

    async def fake_start(self):
        started.append(self.channel)
        self._task = asyncio.create_task(asyncio.sleep(5))
        return True

    monkeypatch.setattr(redis_pubsub.PubSubSubscriber, "start", fake_start)

    async def run():
        a = await redis_pubsub.start_subscriber("sa:events", lambda p: None)
        b = await redis_pubsub.start_subscriber("sa:events", lambda p: None)
        channels = redis_pubsub.active_channels()
        await redis_pubsub.stop_all()
        return a, b, channels

    a, b, channels = _run(run())
    assert a is b
    assert started == ["sa:events"]
    assert channels == ["sa:events"]


def test_start_is_a_noop_without_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    redis_pubsub.reset_for_tests()

    async def run():
        return await redis_pubsub.start_subscriber("sa:events", lambda p: None)

    assert _run(run()) is None
    assert redis_pubsub.active_channels() == []


def test_subscriber_stats_shape(monkeypatch):
    sub = redis_pubsub.PubSubSubscriber("sa:events", lambda p: None)
    stats = sub.stats()
    for key in ("channel", "running", "connected", "messages_total", "dropped_total",
                "handler_errors_total", "reconnects_total", "last_error"):
        assert key in stats


# --------------------------------------------------------------------------- #
# Health probe integration                                                      #
# --------------------------------------------------------------------------- #
def test_redis_probe_skips_when_unconfigured(monkeypatch):
    from observability import health

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(redis_client, "manager", RedisManager(RedisSettings.from_env({})))
    assert _run(health.make_redis_probe()()) is None


def test_redis_probe_passes_and_fails_through_the_shared_client(monkeypatch):
    """The probe must report what the APPLICATION experiences, not what a fresh
    private connection would have seen."""
    from observability import health

    manager, _ = make_manager(monkeypatch)
    monkeypatch.setattr(redis_client, "manager", manager)
    assert _run(health.make_redis_probe()()) is True

    down, _ = make_manager(monkeypatch, client=FakeRedis(fail_ping=True))
    monkeypatch.setattr(redis_client, "manager", down)
    assert _run(health.make_redis_probe()()) is False


def test_probe_reports_false_while_the_circuit_is_open(monkeypatch):
    from observability import health

    manager, fake = make_manager(monkeypatch, circuit_failure_threshold=1,
                                 circuit_reset_seconds=600)
    monkeypatch.setattr(redis_client, "manager", manager)

    async def run():
        await manager.get_client()
        fake.fail_times = 1
        await manager.execute("get", lambda r: r.get("k"))
        return await health.make_redis_probe()()

    assert _run(run()) is False


# --------------------------------------------------------------------------- #
# Metrics                                                                       #
# --------------------------------------------------------------------------- #
def test_redis_metric_families_are_registered():
    from observability import metrics

    rendered = metrics.registry.render_prometheus()
    for name in ("redis_up", "redis_circuit_state", "redis_pool_connections",
                 "redis_commands_total", "redis_command_duration_seconds",
                 "redis_connection_errors_total", "redis_pubsub_reconnects_total",
                 "redis_pubsub_messages_total", "redis_server_memory_used_bytes",
                 "redis_server_connected_clients"):
        assert f"# TYPE {name} " in rendered


def test_command_latency_buckets_resolve_sub_millisecond():
    """The default HTTP buckets start at 5ms, which would put every healthy Redis
    command in the first bucket and make the histogram useless."""
    from observability import metrics

    buckets = metrics.redis_command_duration_seconds.buckets
    assert buckets[0] <= 0.0001
    assert sum(1 for b in buckets if b < 0.005) >= 5


def test_circuit_gauge_encodes_all_three_states():
    from infrastructure.redis_client import _CIRCUIT_GAUGE_VALUE

    assert _CIRCUIT_GAUGE_VALUE[CLOSED] == 0.0
    assert _CIRCUIT_GAUGE_VALUE[HALF_OPEN] == 1.0
    assert _CIRCUIT_GAUGE_VALUE[OPEN] == 2.0


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
async def _collect(sink, payload):
    sink.append(payload)


def _async_return(value):
    async def _coro():
        return value
    return _coro()

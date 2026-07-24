"""Shared cache layer for market/news data.

Uses Redis when REDIS_URL is configured (production), and falls back to a
per-process in-memory store otherwise (local development / tests). Values must be
JSON-serializable. TTL is set at write time.

Also provides a thin Redis Pub/Sub layer (Sprint R2) used to fan real-time events
across processes. When REDIS_URL is unset, publish is a no-op and no subscriber is
started — a single process relies solely on the in-process event bus, so nothing
breaks in local development or tests.

WHAT CHANGED IN PH2.7 (and what deliberately did not)
------------------------------------------------------
Every function below keeps its exact signature and its exact contract. What moved
is the *connection*: this module no longer creates or owns a Redis client. It
calls ``infrastructure.redis_client``, which owns pooling, retry, circuit
breaking and reconnection for the whole process.

That is not a tidiness refactor. The client this module used to build had a
one-way latch::

    except Exception:
        _redis_failed = True        # never cleared, for the life of the process

One transient failure — a Redis restart during a deploy, a two-second partition,
an AOF-rewrite pause — permanently demoted the process to its in-memory
fallback. Nothing raised, no request failed, no alert fired; the process just
silently stopped sharing cache state with its peers until someone restarted it
for an unrelated reason. The replacement is a circuit breaker, which does the
same protective thing (stop hammering a dead dependency) but *re-tests* it and
closes again when it recovers.

The split of responsibilities is worth stating, because it is the reason this
file is short:

    infrastructure/redis_client.py   the connection — pool, retry, breaker
    services/cache.py (here)         the policy — JSON encoding, TTLs, the
                                     in-memory fallback, batching decisions

Nothing in ``infrastructure/`` knows what a quote is; nothing here knows what a
socket timeout is.

THE FALLBACK IS A REAL MODE, NOT AN ERROR PATH
-----------------------------------------------
When Redis is unavailable, every read and write silently uses ``_memory``. The
process keeps serving; what it loses is *sharing*. Two replicas will hold
divergent caches, and each will serve slightly different data for the same
symbol until the TTLs expire. That is the correct trade for market data — stale
by seconds beats a 500 — and it is exactly why Redis is registered as a
NON-critical health check in server.py. It would be the wrong trade for anything
authoritative, which is why sessions, rate-limit counters and audit records live
in MongoDB instead (see backend/security/sessions.py).
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from infrastructure import redis_client, redis_pubsub
from observability import metrics

logger = logging.getLogger(__name__)

_memory: dict = {}
# Bound for the in-memory fallback store (Sprint R9). Without Redis the process
# would otherwise accumulate every key ever written (expired entries are only
# dropped when re-read). When the bound is hit we sweep expired entries first,
# then evict oldest-written entries if still over.
_MEMORY_MAX_KEYS = 1024

# ── Backwards-compatibility shims (PH2.7) ──────────────────────────────────────
# These two names were this module's connection state before the client moved to
# `infrastructure.redis_client`. They are retained because the existing test
# suite monkeypatches them to force a "no Redis" state, and because a module
# attribute that vanishes is a silent behaviour change for anyone who imported
# it. They are no longer read by anything here; the real state lives in
# `redis_client.manager` and is visible via /api/diagnostics/redis.
_redis_client = None
_redis_failed = False


def _redis_url() -> str:
    """The configured Redis URL, or "" when Redis is not in use."""
    return redis_client.redis_url()


async def _get_redis():
    """The shared, pooled Redis client — or None when Redis is unavailable.

    Retained as the module's historical accessor. It now returns the *process-wide*
    client rather than one owned by this module, and "unavailable" now includes
    "the circuit breaker is open", which unlike the old `_redis_failed` flag is a
    temporary condition that resolves itself.
    """
    return await redis_client.get_client()


# ============================== Cache operations ==============================

async def cache_get(key: str):
    """Return the cached value for `key`, or None if missing/expired."""
    ok, raw = await redis_client.execute("get", lambda r: r.get(key))
    if ok:
        # A Redis hit is authoritative, including a miss: `raw is None` means the
        # key genuinely is not there, so falling through to `_memory` would be
        # wrong — it would resurrect a value this process cached before Redis
        # became the source of truth.
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            # A value written by an older schema, or by something that is not
            # this module. Treat as a miss rather than raising: the caller
            # re-fetches and overwrites it, which is self-healing.
            logger.warning("Cache: undecodable value at %s; treating as miss", key)
            return None

    entry = _memory.get(key)
    if not entry:
        return None
    age = (datetime.now(timezone.utc) - entry["ts"]).total_seconds()
    if age >= entry["ttl"]:
        _memory.pop(key, None)
        return None
    return entry["data"]


def _memory_store(key: str, value, ttl: int):
    """Write to the bounded in-memory fallback store."""
    if len(_memory) >= _MEMORY_MAX_KEYS and key not in _memory:
        _prune_memory()
    _memory[key] = {"data": value, "ts": datetime.now(timezone.utc), "ttl": ttl}


def _prune_memory():
    """Sweep expired entries; evict oldest-written if still at the bound."""
    now = datetime.now(timezone.utc)
    expired = [
        k for k, e in _memory.items()
        if (now - e["ts"]).total_seconds() >= e["ttl"]
    ]
    for k in expired:
        _memory.pop(k, None)
    overflow = len(_memory) - _MEMORY_MAX_KEYS + 1
    if overflow > 0:
        oldest = sorted(_memory.items(), key=lambda kv: kv[1]["ts"])[:overflow]
        for k, _ in oldest:
            _memory.pop(k, None)


async def cache_set(key: str, value, ttl: int):
    """Store `value` under `key` for `ttl` seconds."""
    ttl = max(1, int(ttl))
    try:
        payload = json.dumps(value, default=str)
    except (TypeError, ValueError) as exc:
        # Serialize BEFORE touching Redis. Doing it inside the Redis call would
        # make an unserializable value look like a Redis failure, which is a
        # genuinely misleading diagnosis — and it would count against the circuit
        # breaker, letting one bad call site degrade the cache for everything.
        logger.warning("Cache: value for %s is not JSON-serializable: %s", key, exc)
        return

    ok, _ = await redis_client.execute("set", lambda r: r.set(key, payload, ex=ttl))
    if ok:
        return
    _memory_store(key, value, ttl)


async def cache_delete(key: str):
    """Invalidate a cache entry (used by force-refresh endpoints)."""
    await redis_client.execute("delete", lambda r: r.delete(key))
    # Always clear the local copy too, whatever Redis did. A force-refresh
    # endpoint that leaves a stale value in this process's fallback would appear
    # to have done nothing — and would do so only on the replica that served the
    # refresh request, which is a memorably confusing bug to chase.
    _memory.pop(key, None)


# ============ Batched operations (Sprint R9 — Redis optimization) ============

async def cache_get_many(keys):
    """Return {key: value} for every cached, unexpired key in `keys`.

    Missing/expired keys are simply omitted. Against Redis this is ONE MGET
    round-trip instead of len(keys) sequential GETs — the universe-quote warm-up
    uses it to collapse ~50 round-trips into one.

    Why the round-trip count is the number that matters: each GET is ~100µs of
    Redis work and ~0.5ms of network+event-loop overhead, so fifty sequential
    GETs cost ~25ms of wall clock almost entirely spent waiting. One MGET costs
    one round-trip. This is the single highest-leverage Redis optimization in the
    codebase and the reason `cache_set_many` exists alongside it.
    """
    keys = [k for k in (keys or []) if k]
    if not keys:
        return {}

    ok, raw = await redis_client.execute("mget", lambda r: r.mget(keys))
    if ok and raw is not None:
        out = {}
        for key, val in zip(keys, raw):
            if val is None:
                continue
            try:
                out[key] = json.loads(val)
            except (TypeError, ValueError):
                continue
        return out

    now = datetime.now(timezone.utc)
    out = {}
    for key in keys:
        entry = _memory.get(key)
        if not entry:
            continue
        if (now - entry["ts"]).total_seconds() >= entry["ttl"]:
            _memory.pop(key, None)
            continue
        out[key] = entry["data"]
    return out


async def cache_set_many(mapping: dict, ttl: int):
    """Store every {key: value} in `mapping` for `ttl` seconds.

    Against Redis this is one pipelined round-trip instead of len(mapping)
    sequential SETs.

    ``transaction=False`` — a plain command pipeline, not MULTI/EXEC. These are
    independent cache writes with no invariant between them, so the atomicity of
    a transaction would buy nothing while making Redis block on the whole batch
    and, under Cluster, refuse it outright for spanning multiple slots.
    """
    if not mapping:
        return
    ex = max(1, int(ttl))

    encoded = {}
    for key, value in mapping.items():
        try:
            encoded[key] = json.dumps(value, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("Cache: value for %s is not JSON-serializable: %s", key, exc)

    async def _pipeline(r):
        pipe = r.pipeline(transaction=False)
        for key, payload in encoded.items():
            pipe.set(key, payload, ex=ex)
        return await pipe.execute()

    if encoded:
        ok, _ = await redis_client.execute("pipeline_set", _pipeline)
        if ok:
            return
    for key, value in mapping.items():
        _memory_store(key, value, ex)


# ============ Redis Pub/Sub (cross-process event fan-out) ============

async def cache_publish(channel: str, payload) -> bool:
    """Publish a JSON-serializable payload to a Redis channel.

    Returns True if the message was published to Redis, False when Redis is
    unavailable (single-process mode — the caller's in-process delivery already
    covers local subscribers, so this is a safe no-op).

    Note what the return value does and does not mean. True means Redis accepted
    the message, NOT that anyone received it: Pub/Sub is fire-and-forget and a
    publish to a channel with zero subscribers succeeds. See
    infrastructure/redis_pubsub.py on why that is acceptable for this payload and
    would not be for anything where a missed message is a lost fact.
    """
    try:
        body = json.dumps(payload, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning("Cache publish: payload for %s is not serializable: %s", channel, exc)
        return False

    ok, _ = await redis_client.execute("publish", lambda r: r.publish(channel, body))
    if ok:
        metrics.redis_pubsub_messages_total.inc(labels=(channel, "published"))
    return bool(ok)


async def start_pubsub_listener(
    channel: str,
    handler: Callable[[dict], Awaitable[None]],
) -> Optional[asyncio.Task]:
    """Subscribe to a Redis channel and dispatch decoded messages to `handler`.

    Returns the background asyncio.Task, or None when Redis is not configured (no
    subscriber is started and no error is raised). Each incoming message is
    JSON-decoded; malformed payloads are logged and skipped.

    PH2.7 changed the guarantees behind this signature, not the signature:

    * the subscription now **reconnects** with exponential backoff and jitter
      instead of ending permanently on the first disconnect;
    * it uses a **dedicated connection**, so it cannot consume one of the shared
      pool's connections for the life of the process;
    * calling it twice for the same channel returns the **existing** subscriber
      rather than creating a duplicate subscription that delivers every event
      twice;
    * it no longer waits for the connection before returning — startup must not
      block on a dependency that is permitted to be down.

    For lifecycle control beyond "start it", use ``infrastructure.redis_pubsub``
    directly; the subscriber object it returns exposes stop() and stats().
    """
    subscriber = await redis_pubsub.start_subscriber(channel, handler)
    if subscriber is None:
        return None
    return subscriber.task

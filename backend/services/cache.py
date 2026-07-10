"""Shared cache layer for market/news data.

Uses Redis when REDIS_URL is configured (production), and falls back to a
per-process in-memory store otherwise (local development / tests). Values
must be JSON-serializable. TTL is set at write time.

Also provides a thin Redis Pub/Sub layer (Sprint R2) used to fan real-time
events across processes. When REDIS_URL is unset, publish is a no-op and the
listener is never started — a single process relies solely on the in-process
event bus, so nothing breaks in local development or tests.
"""
import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_memory: dict = {}
_redis_client = None
_redis_failed = False


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "").strip()


async def _get_redis():
    """Lazily create the async Redis client. Disable Redis for this process
    if the package is missing or the connection cannot be established."""
    global _redis_client, _redis_failed
    if _redis_failed or not _redis_url():
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(
            _redis_url(), encoding="utf-8", decode_responses=True,
            socket_connect_timeout=3, socket_timeout=3,
        )
        await _redis_client.ping()
        logger.info("Cache layer: Redis connected")
        return _redis_client
    except Exception as e:
        logger.warning(f"Cache layer: Redis unavailable, using in-memory cache ({e})")
        _redis_failed = True
        _redis_client = None
        return None


async def cache_get(key: str):
    """Return the cached value for `key`, or None if missing/expired."""
    r = await _get_redis()
    if r is not None:
        try:
            raw = await r.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception as e:
            logger.warning(f"Cache get failed for {key}: {e}")
    entry = _memory.get(key)
    if not entry:
        return None
    age = (datetime.now(timezone.utc) - entry["ts"]).total_seconds()
    if age >= entry["ttl"]:
        _memory.pop(key, None)
        return None
    return entry["data"]


async def cache_set(key: str, value, ttl: int):
    """Store `value` under `key` for `ttl` seconds."""
    r = await _get_redis()
    if r is not None:
        try:
            await r.set(key, json.dumps(value, default=str), ex=max(1, int(ttl)))
            return
        except Exception as e:
            logger.warning(f"Cache set failed for {key}: {e}")
    _memory[key] = {"data": value, "ts": datetime.now(timezone.utc), "ttl": ttl}


async def cache_delete(key: str):
    """Invalidate a cache entry (used by force-refresh endpoints)."""
    r = await _get_redis()
    if r is not None:
        try:
            await r.delete(key)
        except Exception as e:
            logger.warning(f"Cache delete failed for {key}: {e}")
    _memory.pop(key, None)


# ============ Redis Pub/Sub (cross-process event fan-out) ============

async def cache_publish(channel: str, payload) -> bool:
    """Publish a JSON-serializable payload to a Redis channel.

    Returns True if the message was published to Redis, False when Redis is
    unavailable (single-process mode — the caller's in-process delivery already
    covers local subscribers, so this is a safe no-op)."""
    r = await _get_redis()
    if r is None:
        return False
    try:
        await r.publish(channel, json.dumps(payload, default=str))
        return True
    except Exception as e:
        logger.warning(f"Cache publish failed for {channel}: {e}")
        return False


async def start_pubsub_listener(
    channel: str,
    handler: Callable[[dict], Awaitable[None]],
) -> Optional[asyncio.Task]:
    """Subscribe to a Redis channel and dispatch decoded messages to `handler`.

    Returns the background asyncio.Task, or None when Redis is not configured/
    reachable (no listener is started and no error is raised). Each incoming
    message is JSON-decoded; malformed payloads are logged and skipped."""
    r = await _get_redis()
    if r is None:
        return None
    try:
        pubsub = r.pubsub()
        await pubsub.subscribe(channel)
    except Exception as e:
        logger.warning(f"Cache pubsub subscribe failed for {channel}: {e}")
        return None

    async def _listen():
        try:
            async for message in pubsub.listen():
                if message is None or message.get("type") != "message":
                    continue
                raw = message.get("data")
                try:
                    payload = json.loads(raw)
                except Exception:
                    logger.warning(f"Pubsub {channel}: dropped non-JSON message")
                    continue
                try:
                    await handler(payload)
                except Exception as e:
                    logger.error(f"Pubsub {channel} handler error: {e}", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Pubsub {channel} listener stopped: {e}")

    logger.info(f"Cache layer: Redis pub/sub listening on '{channel}'")
    return asyncio.create_task(_listen())

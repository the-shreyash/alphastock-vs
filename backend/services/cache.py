"""Shared cache layer for market/news data.

Uses Redis when REDIS_URL is configured (production), and falls back to a
per-process in-memory store otherwise (local development / tests). Values
must be JSON-serializable. TTL is set at write time.
"""
import os
import json
import logging
from datetime import datetime, timezone

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

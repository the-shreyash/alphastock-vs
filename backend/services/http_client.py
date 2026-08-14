"""Shared, pooled outbound HTTP clients for provider calls (PH3.4).

THE PROBLEM THIS SOLVES, AND THE MEASUREMENT THAT FOUND IT
----------------------------------------------------------
Every provider call in this codebase was written as::

    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(url, ...)

which is correct, readable, and throws away the connection afterwards. On the
market-data path that is not a detail. `fetch_yahoo_quote` is called **once per
symbol**, fanned out with `asyncio.gather` — 12 symbols for a watchlist, up to 50
for open positions, more for the universe scan — and each call therefore opened
its own TCP connection and performed its own TLS handshake **to the same host**,
then closed it.

Measured against the real Yahoo Finance endpoint, 10 symbols per batch, three
batches, warm DNS (PH3.4 §12):

    per-call client (the previous behaviour)   min 854.0 ms   median 876.6 ms
    shared pooled client                       min 227.9 ms   median 543.4 ms
                                               -> 3.75x faster at the minimum

The handshake was the request. Nothing about the application's own logic, the
database, or the payload changed to get that back.

WHY OPT-IN RATHER THAN A MODULE-LEVEL CLIENT
--------------------------------------------
An `httpx.AsyncClient` holds connections bound to the event loop that created
them. A module-level client constructed at import time is the same mistake as a
module-level Motor client, and this repository has already paid for that one:
`tests/conftest.py` documents `RuntimeError: Event loop is closed` thrown from
inside Motor on the second database-backed request, because FastAPI's
synchronous `TestClient` runs **every request on a fresh loop** via
`asyncio.run`. A shared HTTP client would reproduce that failure on the provider
path, and it would surface as a flaky market-data test rather than as what it is.

So the pool is **opened explicitly by the application lifespan** and keyed by the
running loop. When no pool has been opened for the current loop — which is every
`TestClient` request, and any script that imports a service without booting the
app — `client_for()` falls back to constructing a per-call client and closing it,
i.e. **exactly the previous behaviour**. Tests are unchanged by construction, and
there is no configuration in which a caller gets a client belonging to a dead
loop.

WHY KEYED BY TIMEOUT
--------------------
Call sites use different timeouts (8s for quotes, 10s for most, 12s for the
slower scrapes) and those values are deliberate reliability settings, not
incidental. Sharing one client would mean picking one timeout for all of them —
lengthening some, which lets a slow provider hold a request open longer than its
author intended. Instead there is one pool per distinct timeout. The hot path
(`fetch_yahoo_quote`, timeout 8) is a single bucket and gets full connection
reuse, which is where the 3.75x lives.

SECURITY POSTURE IS UNCHANGED
-----------------------------
TLS verification is httpx's default (on) and is not touched here. No credential,
cookie or header is stored on a shared client: every call site continues to pass
its own headers per request, so a pooled connection cannot carry one caller's
headers into another caller's request. `follow_redirects` stays a per-call
decision for the same reason.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

#: Connection-pool bounds. `max_connections` caps concurrent sockets per pool and
#: is the reason this change cannot turn into an accidental hammering of a
#: provider: the universe scan fans out over more symbols than this, and httpx
#: queues the excess rather than opening a socket per symbol. That is *stricter*
#: than the previous behaviour, where every concurrent call opened its own
#: connection with no ceiling at all.
#:
#: `keepalive_expiry` is deliberately short. A connection idle longer than this is
#: dropped, so the pool cannot hold sockets open against a provider that would
#: rather we did not — and a provider that silently closes an idle connection
#: costs one retried request, not a stuck pool.
MAX_CONNECTIONS = 20
MAX_KEEPALIVE_CONNECTIONS = 10
KEEPALIVE_EXPIRY_SECONDS = 30.0

#: (event loop, timeout) -> pooled client. Keyed by the loop because a client's
#: connections belong to the loop that opened them; keyed by timeout because the
#: per-call timeouts are meaningful (see module docstring).
_pools: Dict[Tuple[int, float], httpx.AsyncClient] = {}

#: The loop the pool was opened for. `None` means "no pool" and selects the
#: per-call fallback — the pre-PH3.4 behaviour.
_owner_loop_id: Optional[int] = None


def _limits() -> httpx.Limits:
    return httpx.Limits(
        max_connections=MAX_CONNECTIONS,
        max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS,
    )


async def open_pool() -> None:
    """Enable connection pooling for the currently running event loop.

    Called once from the application's startup event. Idempotent: calling it
    again for the same loop is a no-op, and calling it for a *different* loop
    (a process that restarts its loop) closes the previous pools first rather
    than leaking them.
    """
    global _owner_loop_id
    loop_id = id(asyncio.get_running_loop())
    if _owner_loop_id == loop_id:
        return
    if _owner_loop_id is not None:
        await close_pool()
    _owner_loop_id = loop_id
    logger.info(
        "Outbound HTTP connection pooling enabled "
        "(max_connections=%d, max_keepalive=%d, keepalive_expiry=%.0fs)",
        MAX_CONNECTIONS, MAX_KEEPALIVE_CONNECTIONS, KEEPALIVE_EXPIRY_SECONDS,
        extra={"event": "http_pool_opened"},
    )


async def close_pool() -> None:
    """Close every pooled client. Called from the application's shutdown event.

    Runs before the process exits so provider connections are closed politely
    rather than reset — and, in a rolling deploy, so the instance being replaced
    is not holding sockets open against a provider while it drains.
    """
    global _owner_loop_id
    pools, _pools_local = list(_pools.items()), None
    _pools.clear()
    _owner_loop_id = None
    for (_loop_id, _timeout), client in pools:
        try:
            await client.aclose()
        except Exception as exc:  # pragma: no cover - defensive on teardown
            logger.warning("Error closing pooled HTTP client: %s", exc)
    if pools:
        logger.info("Closed %d pooled HTTP client(s).", len(pools),
                    extra={"event": "http_pool_closed"})


def _pooled(timeout: float) -> Optional[httpx.AsyncClient]:
    """The pooled client for this loop and timeout, or None if pooling is off.

    Returns None — rather than creating a pool on demand — when no pool has been
    opened for the running loop. That is what keeps the test suite on the
    previous per-call behaviour without any test having to know this module
    exists.
    """
    if _owner_loop_id is None:
        return None
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:  # pragma: no cover - not called outside a loop
        return None
    if loop_id != _owner_loop_id:
        return None

    key = (loop_id, float(timeout))
    client = _pools.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=timeout, limits=_limits())
        _pools[key] = client
    return client


@asynccontextmanager
async def client_for(timeout: float = 10.0):
    """Yield an HTTP client for one or more provider requests.

    Drop-in replacement for ``async with httpx.AsyncClient(timeout=T) as c``.

    When pooling is active the yielded client is **shared and must not be
    closed** — hence the two branches below rather than one. When it is not, the
    client is private to this block and is closed on exit, exactly as before.
    """
    pooled = _pooled(timeout)
    if pooled is not None:
        yield pooled
        return
    async with httpx.AsyncClient(timeout=timeout) as client:
        yield client


def pool_stats() -> dict:
    """Introspection for the performance regression tests and diagnostics."""
    return {
        "enabled": _owner_loop_id is not None,
        "pools": len(_pools),
        "timeouts": sorted({t for _, t in _pools}),
        "max_connections": MAX_CONNECTIONS,
        "max_keepalive_connections": MAX_KEEPALIVE_CONNECTIONS,
    }

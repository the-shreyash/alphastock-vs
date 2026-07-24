"""The one Redis client this process owns (PH2.7).

WHAT THIS REPLACES AND WHY
--------------------------
Before this module there were two independent Redis clients in the backend, and
one of them had a bug that no test could catch because it only appears in
production:

``services/cache.py`` created a client lazily and, on the first failure, set a
module-level ``_redis_failed = True``. That flag was never cleared. One transient
blip — a Redis restart during a deploy, a two-second network partition, an AOF
rewrite pause — permanently demoted that process to its in-memory fallback for
its entire lifetime. The cache still worked, so nothing alerted; the process just
silently stopped sharing state with its peers and stopped receiving cross-process
realtime events, until someone restarted it for an unrelated reason.

``observability/health.py`` worked around that by building a *brand new* client
on every readiness poll, which is why its docstring says it cannot reuse the
cache's helper. That is correct given the bug, and it means a TCP connect,
AUTH round-trip and teardown several times a minute, forever.

Both problems have the same root cause: there was no component that owned "the
connection to Redis" as a thing with a lifecycle. This module is that component.

THE THREE MECHANISMS, AND WHAT EACH ONE IS ACTUALLY FOR
-------------------------------------------------------
They are frequently confused, and using one where another is needed is how a
system ends up either hammering a dead dependency or ignoring a live one.

**Connection pool** — amortizes connection setup. A TCP handshake plus AUTH is
~1ms on a local network; doing it per operation would dominate the cost of a
cache GET that Redis itself answers in ~100µs. The pool also *bounds* concurrency:
without a ceiling, a burst of 5000 concurrent requests opens 5000 sockets and
hits Redis's ``maxclients`` — turning a traffic spike into a hard outage.

**Retry** — absorbs the failure that will succeed if tried again immediately.
A connection dropped mid-command, a socket timeout under momentary load. Scope
is one operation, budget is a few hundred milliseconds. Retrying is only ever
correct for *idempotent* work, which is why it is enabled for connection errors
and timeouts and not blanket-enabled: a ``TimeoutError`` on an ``INCR`` may mean
the command actually ran.

**Circuit breaker** — absorbs the failure that will NOT succeed if tried again.
Redis is down. Every operation now costs a full connect timeout before failing,
and every one of them is a coroutine holding an event-loop slot. With a 3-second
connect timeout and a fallback that would have answered instantly, a dead
dependency makes the *application* slow — the classic cascading failure, where
the outage is caused by the retry traffic rather than by the original fault.
The breaker's job is to fail in microseconds instead of seconds while the
dependency is down, and — the part the old ``_redis_failed`` latch never did —
to periodically let one request through to find out when it comes back.

    CLOSED ──(N consecutive connection failures)──▶ OPEN
      ▲                                              │
      │                                     (cooldown elapses)
      │                                              ▼
      └────────(trial operation succeeds)──── HALF_OPEN
                                                     │
                                        (trial fails)│
                                                     ▼
                                                   OPEN

HALF_OPEN admits exactly one trial operation. Admitting all of them would mean
that at the moment Redis recovers, every replica's entire backlog arrives at once
— a reconnect storm against a server that has just finished loading its AOF and
is at its most fragile.

DEGRADATION IS A FIRST-CLASS PATH, NOT AN ERROR PATH
-----------------------------------------------------
No function here raises on a Redis failure. ``execute()`` returns ``(ok, value)``
and callers fall back. This is not defensive sloppiness — it is the product
decision that a market-data cache miss must never become a user-visible 500.
What the caller loses is *sharing*, not *function*: with Redis down each replica
serves from its own in-process cache and delivers realtime events to its own
sockets. Correct for one replica, degraded but serving for several.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
Sentinel, Cluster, and read replicas. All are out of scope for PH2.7 and none is
a drop-in: they change failover semantics, key routing and the meaning of a
multi-key command. The seam for them is this module — ``_build_client()`` is the
only place in the backend that constructs a Redis connection, so the migration is
bounded to one function. See docs/infrastructure/REDIS.md §Failover.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, TypeVar
from urllib.parse import urlsplit, urlunsplit

from observability import metrics

logger = logging.getLogger(__name__)

T = TypeVar("T")

# --------------------------------------------------------------------------- #
# Circuit states                                                                #
# --------------------------------------------------------------------------- #
# Strings, not an enum, so they serialise straight into the diagnostics payload
# an operator reads — matching the convention in observability/health.py.
CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"

# Numeric encoding for the Prometheus gauge. Ordered by severity so that
# `max_over_time(redis_circuit_state[5m])` is a meaningful alert expression.
_CIRCUIT_GAUGE_VALUE = {CLOSED: 0.0, HALF_OPEN: 1.0, OPEN: 2.0}


# --------------------------------------------------------------------------- #
# Configuration                                                                 #
# --------------------------------------------------------------------------- #
def _env_float(name: str, default: float, *, lo: float, hi: float,
               environ: Optional[Dict[str, str]] = None) -> float:
    """Read a float tunable, clamped. A malformed value falls back to the
    default rather than crashing boot: a typo in an ops variable must not stop a
    trading backend from starting."""
    raw = (environ if environ is not None else os.environ).get(name, "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, float(raw)))
    except ValueError:
        logger.warning("%s=%r is not a number; using %s", name, raw, default)
        return default


def _env_int(name: str, default: int, *, lo: int, hi: int,
             environ: Optional[Dict[str, str]] = None) -> int:
    raw = (environ if environ is not None else os.environ).get(name, "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        logger.warning("%s=%r is not an integer; using %s", name, raw, default)
        return default


@dataclass(frozen=True)
class RedisSettings:
    """Everything tunable about the connection, resolved once from the
    environment.

    Frozen and constructed from an explicit ``environ`` mapping so tests can
    exercise a configuration without mutating the process environment — the same
    pattern ``security.secrets`` uses for its resolution matrix.
    """

    url: str
    max_connections: int
    connect_timeout: float
    socket_timeout: float
    health_check_interval: float
    retry_attempts: int
    circuit_failure_threshold: int
    circuit_reset_seconds: float
    stats_interval: float

    @property
    def configured(self) -> bool:
        return bool(self.url)

    @classmethod
    def from_env(cls, environ: Optional[Dict[str, str]] = None) -> "RedisSettings":
        env = environ if environ is not None else os.environ
        return cls(
            url=env.get("REDIS_URL", "").strip(),
            # 24 connections per replica. Comfortably above this backend's
            # concurrency (the cache is used on request paths that are already
            # awaiting a provider HTTP call, so few are in Redis at once) and far
            # below the server's `maxclients 512` even at a dozen replicas.
            # Raising it is rarely the fix for slowness — a saturated pool almost
            # always means a slow *command*, which a bigger pool only lets you
            # run more of.
            max_connections=_env_int(
                "REDIS_MAX_CONNECTIONS", 24, lo=1, hi=512, environ=env),
            # Connect must be strictly shorter than the readiness probe timeout
            # (HEALTH_PROBE_TIMEOUT_SECONDS, default 2.0), or a Redis that is
            # merely slow to accept makes readiness time out instead of
            # reporting a failed Redis check — the same symptom with a much worse
            # diagnosis.
            connect_timeout=_env_float(
                "REDIS_CONNECT_TIMEOUT_SECONDS", 1.5, lo=0.1, hi=30.0, environ=env),
            # Per-command ceiling. Redis answers in microseconds; 2s means the
            # server is blocked (a fork, a huge value, a `KEYS` someone
            # committed) and waiting longer only ties up the event loop.
            socket_timeout=_env_float(
                "REDIS_SOCKET_TIMEOUT_SECONDS", 2.0, lo=0.1, hi=60.0, environ=env),
            # redis-py PINGs a pooled connection before use if it has been idle
            # longer than this. It is what makes a Redis RESTART invisible:
            # after a restart every pooled connection is dead but looks fine
            # until written to, and without this the first N operations after a
            # restart each fail once. 30s is well inside the server's
            # `tcp-keepalive 300`.
            health_check_interval=_env_float(
                "REDIS_HEALTH_CHECK_INTERVAL_SECONDS", 30.0, lo=0.0, hi=3600.0,
                environ=env),
            # Attempts *after* the first try, for connection-level errors only.
            # 2 covers the overwhelmingly common case (one dead pooled
            # connection) without turning a real outage into a latency
            # multiplier.
            retry_attempts=_env_int(
                "REDIS_RETRY_ATTEMPTS", 2, lo=0, hi=10, environ=env),
            # 5 consecutive failures, not 1. A single failure is noise —
            # precisely the noise the old `_redis_failed` latch mistook for a
            # verdict.
            circuit_failure_threshold=_env_int(
                "REDIS_CIRCUIT_FAILURE_THRESHOLD", 5, lo=1, hi=100, environ=env),
            # How long the breaker stays open before admitting one trial. Long
            # enough that a down Redis is not probed continuously, short enough
            # that recovery is picked up within a few seconds of it happening.
            circuit_reset_seconds=_env_float(
                "REDIS_CIRCUIT_RESET_SECONDS", 10.0, lo=0.5, hi=600.0, environ=env),
            # Background INFO sampling cadence for the memory/clients gauges.
            # 0 disables it. Sampled on a timer rather than at scrape time
            # because a metrics scraper must never be able to drive load onto the
            # dependency it is observing — the same rule observability/health.py
            # applies to MongoDB.
            stats_interval=_env_float(
                "REDIS_STATS_INTERVAL_SECONDS", 30.0, lo=0.0, hi=3600.0, environ=env),
        )


def redis_url() -> str:
    """The configured URL, or "" when Redis is not in use.

    Read live from the environment rather than cached, because the test suite
    (and `security.secrets.load_secrets`) sets REDIS_URL after import.
    """
    return os.environ.get("REDIS_URL", "").strip()


def sanitized_url(url: Optional[str] = None) -> str:
    """A URL safe to put in a log line or an HTTP response.

    ``redis://:hunter2@redis:6379/0`` → ``redis://***@redis:6379/0``. This is not
    paranoia: redis-py's ``ConnectionError`` stringifies to a message containing
    the connection target, and this codebase's own diagnostics endpoint is
    reachable by anything that can reach the service. `observability/health.py`
    documents the same hazard for pymongo.
    """
    raw = url if url is not None else redis_url()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "<unparseable>"
    if not parts.hostname:
        return "<unparseable>"
    host = parts.hostname
    netloc = f"***@{host}" if (parts.password or parts.username) else host
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# --------------------------------------------------------------------------- #
# Error classification                                                          #
# --------------------------------------------------------------------------- #
def is_connection_error(exc: BaseException) -> bool:
    """Does this exception mean "the link to Redis is broken"?

    The distinction drives the circuit breaker, and getting it wrong is costly in
    both directions. A ``WRONGTYPE`` or an OOM-from-``noeviction`` is an
    application-level answer from a perfectly healthy server: counting it as a
    connection failure would open the breaker and disable a working Redis because
    of a code bug. Conversely, treating a ``ConnectionResetError`` as a normal
    command failure means the breaker never opens and every operation pays the
    full timeout while the server is down.
    """
    try:
        from redis.exceptions import (
            AuthenticationError,
            BusyLoadingError,
            ConnectionError as RedisConnectionError,
            TimeoutError as RedisTimeoutError,
        )
    except Exception:  # pragma: no cover - redis not installed
        return isinstance(exc, (OSError, asyncio.TimeoutError))

    # BusyLoadingError = the server is up but replaying its AOF and refusing
    # commands. Counting it opens the breaker for the duration of the load, which
    # is exactly right: there is nothing to be gained by querying it meanwhile,
    # and the half-open trial detects the end of loading within one cooldown.
    #
    # AuthenticationError is included because a wrong password fails every
    # operation identically and forever; failing fast surfaces it as an open
    # breaker with a clear last_error instead of as uniform latency.
    return isinstance(
        exc,
        (RedisConnectionError, RedisTimeoutError, BusyLoadingError,
         AuthenticationError, OSError, asyncio.TimeoutError),
    )


# --------------------------------------------------------------------------- #
# The manager                                                                   #
# --------------------------------------------------------------------------- #
class RedisManager:
    """Owns the connection pool, the circuit breaker and the counters.

    A class rather than module globals — mirroring ``observability.metrics``'
    ``Registry`` — so the test suite can build an isolated instance instead of
    unpicking shared state between tests. The application uses the module-level
    :data:`manager` singleton, and the module-level functions below delegate to
    it so call sites never have to locate it.
    """

    def __init__(self, settings: Optional[RedisSettings] = None) -> None:
        self._settings = settings or RedisSettings.from_env()
        self._client: Any = None
        # Guards client construction only. Commands themselves are NOT
        # serialized — a lock around every operation would make the pool
        # pointless and turn Redis into a single-flight bottleneck.
        self._connect_lock: Optional[asyncio.Lock] = None

        # Circuit state
        self._state: str = CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        # Set while a HALF_OPEN trial is in flight, so only one operation is
        # admitted. Plain bool rather than a lock: this runs on one event loop
        # and the check-and-set below never awaits between the two.
        self._trial_in_flight = False

        # Counters (cumulative, process lifetime)
        self._commands = 0
        self._failures = 0
        self._connection_errors = 0
        self._circuit_opens = 0
        self._connects = 0
        self._last_error: Optional[str] = None
        self._last_error_at: Optional[float] = None
        self._last_success_at: Optional[float] = None
        self._connected_at: Optional[float] = None

        # Background INFO sampler
        self._sampler: Optional[asyncio.Task] = None
        self._server_info: Dict[str, Any] = {}
        self._closing = False

    # -- configuration ------------------------------------------------------ #
    @property
    def settings(self) -> RedisSettings:
        return self._settings

    def reload_settings(self, environ: Optional[Dict[str, str]] = None) -> None:
        """Re-read the environment. Test support, and the hook a future
        hot-reload would use. Does not disturb a live client — call
        :meth:`close` first if the URL changed."""
        self._settings = RedisSettings.from_env(environ)

    @property
    def configured(self) -> bool:
        # Deliberately consults the live environment rather than the snapshot:
        # `security.secrets.load_secrets()` materialises REDIS_URL into
        # os.environ during boot, which happens after this module is imported.
        return bool(redis_url())

    # -- circuit breaker ---------------------------------------------------- #
    @property
    def circuit_state(self) -> str:
        return self._state

    def _admits_traffic(self) -> bool:
        """May an operation proceed right now? Also performs the OPEN →
        HALF_OPEN transition, since that transition is driven by time rather
        than by an event and there is no timer to do it."""
        if self._state == CLOSED:
            return True
        if self._state == OPEN:
            if (time.monotonic() - self._opened_at) < self._settings.circuit_reset_seconds:
                return False
            self._state = HALF_OPEN
            self._trial_in_flight = True
            logger.info(
                "Redis circuit half-open — admitting one trial operation",
                extra={"event": "redis_circuit_half_open"},
            )
            return True
        # HALF_OPEN: exactly one trial at a time.
        if self._trial_in_flight:
            return False
        self._trial_in_flight = True
        return True

    def _record_success(self) -> None:
        self._last_success_at = time.time()
        self._consecutive_failures = 0
        self._trial_in_flight = False
        if self._state != CLOSED:
            logger.info(
                "Redis circuit closed — dependency recovered",
                extra={"event": "redis_circuit_closed"},
            )
            self._state = CLOSED

    def _record_failure(self, exc: BaseException, *, connection_level: bool) -> None:
        self._failures += 1
        self._last_error = f"{exc.__class__.__name__}: {exc}"[:300]
        self._last_error_at = time.time()

        if not connection_level:
            # A command-level error says nothing about the link. Do not let a
            # WRONGTYPE bug disable a healthy Redis.
            self._trial_in_flight = False
            return

        self._connection_errors += 1
        metrics.redis_connection_errors_total.inc()

        if self._state == HALF_OPEN:
            # The trial failed: straight back to OPEN with a fresh cooldown,
            # without waiting for the threshold again.
            self._trial_in_flight = False
            self._open_circuit()
            return

        self._consecutive_failures += 1
        if self._consecutive_failures >= self._settings.circuit_failure_threshold:
            self._open_circuit()

    def _open_circuit(self) -> None:
        already_open = self._state == OPEN
        self._state = OPEN
        self._opened_at = time.monotonic()
        self._consecutive_failures = 0
        if not already_open:
            self._circuit_opens += 1
            logger.warning(
                "Redis circuit OPEN — degrading to in-process fallback for %.1fs (%s)",
                self._settings.circuit_reset_seconds, self._last_error,
                extra={
                    "event": "redis_circuit_open",
                    "cooldown_seconds": self._settings.circuit_reset_seconds,
                },
            )
        # Drop the pool. Every pooled connection is presumed dead, and keeping
        # them means the half-open trial is likely to pick a stale one and fail
        # for a reason unrelated to whether Redis recovered — the breaker would
        # then never close. Scheduled rather than awaited because this runs
        # inside a failure path that must not itself block or raise.
        self._schedule_pool_reset()

    def _schedule_pool_reset(self) -> None:
        client, self._client = self._client, None
        self._connected_at = None
        if client is None:
            return

        async def _drop() -> None:
            try:
                await client.aclose()
            except AttributeError:  # pragma: no cover - redis-py < 5
                try:
                    await client.close()
                except Exception:
                    pass
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Redis pool teardown raised (ignored): %s", exc)

        try:
            asyncio.get_running_loop().create_task(_drop())
        except RuntimeError:  # pragma: no cover - no loop (shutdown/tests)
            pass

    # -- client lifecycle --------------------------------------------------- #
    def _get_connect_lock(self) -> asyncio.Lock:
        """Created lazily on the running loop.

        An ``asyncio.Lock`` constructed at import time binds to whatever loop
        exists then — which in this codebase is the wrong one, a failure
        ``observability/health.py`` and ``tests/conftest.py`` both already
        document.
        """
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        return self._connect_lock

    def _build_client(self) -> Any:
        """Construct the pooled client.

        THE ONLY PLACE in the backend that constructs a Redis connection. A
        future Sentinel or Cluster migration edits this function and nothing
        else — which is the point of routing every consumer through this module.
        """
        import redis.asyncio as aioredis
        from redis.asyncio.retry import Retry
        from redis.backoff import ExponentialBackoff
        from redis.exceptions import (
            ConnectionError as RedisConnectionError,
            TimeoutError as RedisTimeoutError,
        )

        s = self._settings
        # Exponential backoff *within* a single operation's retries: ~8ms, then
        # ~16ms, capped at 512ms. Deliberately sub-second — this budget is spent
        # inside a user's request, and anything longer belongs to the circuit
        # breaker's cooldown, not to a retry loop.
        retry = Retry(ExponentialBackoff(cap=0.512, base=0.008), s.retry_attempts)

        return aioredis.from_url(
            s.url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=s.max_connections,
            socket_connect_timeout=s.connect_timeout,
            socket_timeout=s.socket_timeout,
            # OS-level keepalive on every pooled socket, so a peer that vanished
            # without a FIN is detected rather than discovered on next use. The
            # server-side half of this is `tcp-keepalive 300` in redis.conf.
            socket_keepalive=True,
            health_check_interval=s.health_check_interval,
            retry=retry,
            # Retry only these. Notably absent: everything else. A retry on a
            # command the server *answered* would re-run a mutation.
            retry_on_error=[RedisConnectionError, RedisTimeoutError],
            retry_on_timeout=True,
        )

    async def get_client(self) -> Optional[Any]:
        """The shared client, or None when Redis is unconfigured, the breaker is
        open, or the connection cannot be established.

        Never raises. The None return is the documented, supported path — see
        the module docstring on degradation.
        """
        if not self.configured or self._closing:
            return None
        if not self._admits_traffic():
            return None
        if self._client is not None:
            return self._client

        async with self._get_connect_lock():
            # Re-check under the lock: while this coroutine waited, another may
            # have built the client. Without this, a cold start with N
            # concurrent requests builds N pools and discards N-1.
            if self._client is not None:
                return self._client
            try:
                client = self._build_client()
                await client.ping()
            except Exception as exc:
                self._record_failure(exc, connection_level=is_connection_error(exc))
                logger.warning(
                    "Redis connect failed (%s): %s",
                    sanitized_url(), exc,
                    extra={"event": "redis_connect_failed"},
                )
                return None
            self._client = client
            self._connects += 1
            self._connected_at = time.time()
            self._record_success()
            logger.info(
                "Redis connected: %s (pool=%d, health_check=%.0fs)",
                sanitized_url(), self._settings.max_connections,
                self._settings.health_check_interval,
                extra={"event": "redis_connected"},
            )
            return client

    async def execute(
        self,
        operation: str,
        fn: Callable[[Any], Awaitable[T]],
        *,
        default: Optional[T] = None,
    ) -> Tuple[bool, Optional[T]]:
        """Run one Redis operation with full instrumentation and no exceptions.

        Returns ``(ok, value)``. ``ok is False`` means the caller should use its
        fallback — it does not distinguish "Redis is down" from "the breaker is
        open", because the caller's action is identical either way, and the
        distinction is available in :meth:`stats` for whoever needs it.

        ``operation`` is a *low-cardinality* label (``get``, ``set``, ``mget``…),
        never a key. A key would make the metric label space unbounded, which is
        the mistake ``METRICS_MAX_SERIES`` exists to catch.
        """
        client = await self.get_client()
        if client is None:
            metrics.redis_commands_total.inc(labels=(operation, "unavailable"))
            return False, default

        started = time.perf_counter()
        try:
            value = await fn(client)
        except Exception as exc:
            duration = time.perf_counter() - started
            connection_level = is_connection_error(exc)
            self._commands += 1
            self._record_failure(exc, connection_level=connection_level)
            metrics.redis_command_duration_seconds.observe(duration, labels=(operation,))
            metrics.redis_commands_total.inc(
                labels=(operation, "connection_error" if connection_level else "error"))
            logger.warning(
                "Redis %s failed: %s", operation, exc,
                extra={"event": "redis_command_failed", "redis_operation": operation},
            )
            return False, default

        duration = time.perf_counter() - started
        self._commands += 1
        self._record_success()
        metrics.redis_command_duration_seconds.observe(duration, labels=(operation,))
        metrics.redis_commands_total.inc(labels=(operation, "ok"))
        return True, value

    async def ping(self) -> Optional[float]:
        """Round-trip latency in seconds, or None when Redis is unavailable.

        Used by the readiness probe and the diagnostics endpoint. Goes through
        :meth:`execute`, so a ping participates in the breaker like any other
        operation — which is what makes the probe honest: it reports the state
        the application actually experiences, not the state a fresh private
        connection would have seen.
        """
        started = time.perf_counter()
        ok, _ = await self.execute("ping", lambda r: r.ping())
        return (time.perf_counter() - started) if ok else None

    # -- server introspection ----------------------------------------------- #
    async def refresh_server_info(self) -> Dict[str, Any]:
        """Sample the server-side facts an operator needs: memory, clients,
        persistence, keyspace.

        Bare ``INFO``, which returns Redis's *default* section set. Deliberately
        not ``INFO all`` / ``INFO everything``: those add ``commandstats`` and
        ``latencystats``, hundreds of lines rebuilt on every call — a real cost
        to pay every 30 seconds for data nothing here reads. When you do want
        them, ask for them by hand during an incident:
        ``redis-cli INFO commandstats``.
        """
        ok, raw = await self.execute("info", lambda r: r.info())
        if not ok or not isinstance(raw, dict):
            return {}

        def _num(key: str) -> Optional[float]:
            val = raw.get(key)
            try:
                return float(val) if val is not None else None
            except (TypeError, ValueError):
                return None

        info = {
            "redis_version": raw.get("redis_version"),
            "uptime_seconds": _num("uptime_in_seconds"),
            "connected_clients": _num("connected_clients"),
            "blocked_clients": _num("blocked_clients"),
            "used_memory_bytes": _num("used_memory"),
            "used_memory_rss_bytes": _num("used_memory_rss"),
            "maxmemory_bytes": _num("maxmemory"),
            "maxmemory_policy": raw.get("maxmemory_policy"),
            # >1.5 sustained is the signal that `activedefrag` is worth its CPU
            # cost. See the note at the bottom of docker/redis/redis.conf.
            "mem_fragmentation_ratio": _num("mem_fragmentation_ratio"),
            "evicted_keys": _num("evicted_keys"),
            "expired_keys": _num("expired_keys"),
            "keyspace_hits": _num("keyspace_hits"),
            "keyspace_misses": _num("keyspace_misses"),
            "total_connections_received": _num("total_connections_received"),
            "rejected_connections": _num("rejected_connections"),
            "pubsub_channels": _num("pubsub_channels"),
            "aof_enabled": _num("aof_enabled"),
            "aof_last_write_status": raw.get("aof_last_write_status"),
            "aof_rewrite_in_progress": _num("aof_rewrite_in_progress"),
            "rdb_last_bgsave_status": raw.get("rdb_last_bgsave_status"),
            "sampled_at": time.time(),
        }
        self._server_info = info
        _publish_server_gauges(info)
        return info

    @property
    def server_info(self) -> Dict[str, Any]:
        """The last sampled INFO snapshot (possibly empty/stale). Never triggers
        a round-trip — callers on a request path must not be able to."""
        return dict(self._server_info)

    # -- background sampler -------------------------------------------------- #
    async def start_stats_sampler(self) -> bool:
        """Begin periodic INFO sampling. Idempotent.

        Why a background task rather than sampling at scrape time: a Prometheus
        collector runs synchronously during rendering and cannot await, and —
        more importantly — sampling on scrape lets anyone who can reach
        ``/api/metrics`` generate Redis load by scraping faster. A fixed-cadence
        sampler decouples the two.
        """
        if self._sampler is not None and not self._sampler.done():
            return True
        if not self.configured or self._settings.stats_interval <= 0:
            return False

        async def _loop() -> None:
            interval = self._settings.stats_interval
            while not self._closing:
                try:
                    await self.refresh_server_info()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Redis stats sampler iteration failed: %s", exc)
                # Jittered, so that N replicas started by the same deploy do not
                # all INFO the server on the same tick forever.
                await asyncio.sleep(interval * random.uniform(0.85, 1.15))

        self._sampler = asyncio.create_task(_loop(), name="redis-stats-sampler")
        logger.info(
            "Redis stats sampler started (every %.0fs)", self._settings.stats_interval)
        return True

    async def stop_stats_sampler(self) -> None:
        task, self._sampler = self._sampler, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # -- diagnostics --------------------------------------------------------- #
    def pool_stats(self) -> Dict[str, Optional[int]]:
        """In-use / available / max connections.

        redis-py exposes these only as private attributes, so every read is
        defensive: a library upgrade that renames them must degrade to "unknown"
        rather than break the diagnostics endpoint an operator is using *because*
        something is already broken.
        """
        client = self._client
        pool = getattr(client, "connection_pool", None) if client is not None else None
        if pool is None:
            return {"in_use": None, "available": None, "max": self._settings.max_connections}
        in_use = getattr(pool, "_in_use_connections", None)
        available = getattr(pool, "_available_connections", None)
        return {
            "in_use": len(in_use) if in_use is not None else None,
            "available": len(available) if available is not None else None,
            "max": getattr(pool, "max_connections", self._settings.max_connections),
        }

    def stats(self) -> Dict[str, Any]:
        """The full connection-side snapshot. Synchronous and allocation-cheap —
        safe to call from a metrics collector."""
        s = self._settings
        return {
            "configured": self.configured,
            "connected": self._client is not None,
            "url": sanitized_url(),
            "circuit_state": self._state,
            "consecutive_failures": self._consecutive_failures,
            "circuit_opens_total": self._circuit_opens,
            "commands_total": self._commands,
            "failures_total": self._failures,
            "connection_errors_total": self._connection_errors,
            "connects_total": self._connects,
            "connected_at": self._connected_at,
            "last_success_at": self._last_success_at,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
            "pool": self.pool_stats(),
            "settings": {
                "max_connections": s.max_connections,
                "connect_timeout_seconds": s.connect_timeout,
                "socket_timeout_seconds": s.socket_timeout,
                "health_check_interval_seconds": s.health_check_interval,
                "retry_attempts": s.retry_attempts,
                "circuit_failure_threshold": s.circuit_failure_threshold,
                "circuit_reset_seconds": s.circuit_reset_seconds,
                "stats_interval_seconds": s.stats_interval,
            },
        }

    # -- shutdown ------------------------------------------------------------ #
    async def close(self) -> None:
        """Release everything. Safe to call twice, and safe to call when Redis
        was never configured."""
        self._closing = True
        await self.stop_stats_sampler()
        client, self._client = self._client, None
        self._connected_at = None
        if client is None:
            return
        try:
            await client.aclose()
        except AttributeError:  # pragma: no cover - redis-py < 5
            try:
                await client.close()
            except Exception:
                pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Redis client close raised (ignored): %s", exc)
        logger.info("Redis client closed", extra={"event": "redis_closed"})

    def reset_for_tests(self) -> None:
        """Return to a pristine state without touching the event loop."""
        self._client = None
        self._connect_lock = None
        self._state = CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False
        self._commands = 0
        self._failures = 0
        self._connection_errors = 0
        self._circuit_opens = 0
        self._connects = 0
        self._last_error = None
        self._last_error_at = None
        self._last_success_at = None
        self._connected_at = None
        self._server_info = {}
        self._sampler = None
        self._closing = False
        self._settings = RedisSettings.from_env()


# --------------------------------------------------------------------------- #
# Module-level singleton + delegating API                                       #
# --------------------------------------------------------------------------- #
manager = RedisManager()


async def get_client() -> Optional[Any]:
    return await manager.get_client()


async def execute(
    operation: str,
    fn: Callable[[Any], Awaitable[T]],
    *,
    default: Optional[T] = None,
) -> Tuple[bool, Optional[T]]:
    return await manager.execute(operation, fn, default=default)


async def ping() -> Optional[float]:
    return await manager.ping()


def stats() -> Dict[str, Any]:
    return manager.stats()


def server_info() -> Dict[str, Any]:
    return manager.server_info


async def refresh_server_info() -> Dict[str, Any]:
    return await manager.refresh_server_info()


async def start_stats_sampler() -> bool:
    return await manager.start_stats_sampler()


async def close() -> None:
    await manager.close()


def is_configured() -> bool:
    return manager.configured


# --------------------------------------------------------------------------- #
# Metrics plumbing                                                              #
# --------------------------------------------------------------------------- #
def _publish_server_gauges(info: Dict[str, Any]) -> None:
    """Copy the sampled INFO numbers onto their gauges."""
    def _set(gauge, key: str) -> None:
        value = info.get(key)
        if isinstance(value, (int, float)):
            gauge.set(float(value))

    _set(metrics.redis_server_memory_used_bytes, "used_memory_bytes")
    _set(metrics.redis_server_memory_max_bytes, "maxmemory_bytes")
    _set(metrics.redis_server_connected_clients, "connected_clients")
    _set(metrics.redis_server_evicted_keys_total, "evicted_keys")
    _set(metrics.redis_server_expired_keys_total, "expired_keys")
    _set(metrics.redis_server_rejected_connections_total, "rejected_connections")


def _collect_client_gauges() -> None:
    """Render-time collector for the connection-side gauges.

    Registered with the metrics registry at import. Reads only in-process state —
    no Redis round-trip — so a scrape can never add load to Redis. The
    server-side numbers come from the background sampler instead.
    """
    snapshot = manager.stats()
    metrics.redis_up.set(1.0 if snapshot["connected"] else 0.0)
    metrics.redis_circuit_state.set(_CIRCUIT_GAUGE_VALUE.get(snapshot["circuit_state"], 0.0))
    pool = snapshot["pool"]
    for state in ("in_use", "available", "max"):
        value = pool.get(state)
        if value is not None:
            metrics.redis_pool_connections.set(float(value), labels=(state,))


metrics.registry.add_collector(_collect_client_gauges)

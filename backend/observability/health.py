"""Health checks and process lifecycle (PH2.5).

WHY THERE ARE THREE PROBES AND NOT ONE
--------------------------------------
"Is it healthy?" is three different questions asked by three different systems
that take three very different actions on the answer. Conflating them is the
single most common — and most damaging — observability mistake in a containerised
deployment.

**Liveness — "should this process be killed and restarted?"**
Asked by Docker/Kubernetes on a timer. A failure gets the container *destroyed*.
It must therefore check only what a restart can fix: that the process exists and
its event loop is still turning. It must **never** touch MongoDB, Redis or a
provider API. The reason is worth stating precisely, because the mistake is
subtle and its consequences are total: if liveness depends on the database, then
a 60-second database blip makes *every replica simultaneously* report unhealthy,
the orchestrator restarts *all of them at once*, and the empty caches and
reconnect storm of a full cold start hit the database that was already
struggling. A recoverable dependency wobble becomes a self-inflicted full outage
— and because the restarts keep failing, it does not end when the database
recovers. `backend/docker/healthcheck.sh` has carried this rationale since PH2.1;
this module is where it becomes a first-class endpoint.

**Readiness — "should this instance receive traffic right now?"**
Asked by the load balancer / service mesh. A failure removes the instance from
the pool but *leaves it running*. This is the probe that verifies dependencies:
an instance that cannot reach MongoDB cannot serve a useful response, so it
should stop being sent requests — and should come straight back when the
dependency recovers, with no restart and no cold start.

**Startup — "has this instance finished booting?"**
Asked while a container is coming up, and it exists to protect slow starts from
the liveness probe. This backend's startup creates ~20 Mongo indexes, restores
broker sessions, initialises the market gateway and starts four background
loops. Without a startup probe, an aggressive liveness timer kills the container
mid-boot, forever — a crash loop whose cause looks like nothing at all, because
the logs stop in a different place each time.

DESIGN DECISIONS
----------------
* **Probes run in parallel** (`asyncio.gather`). Serial probes make readiness
  latency the *sum* of every dependency's, so adding a third dependency can push
  the probe past the balancer's timeout and take the instance out of rotation
  for a reason unrelated to its health.
* **Every probe has a hard timeout.** A TCP connection to a partitioned host
  does not fail — it hangs, for minutes. An un-timed-out probe means readiness
  never answers, which the balancer reads as failure anyway, but only after it
  has held a connection open the whole time.
* **Results are cached for a couple of seconds.** In a real deployment the
  liveness timer, the readiness timer, the ingress controller and an uptime
  monitor all poll independently; without a cache, a busy cluster generates a
  continuous `ping` load against MongoDB purely to ask whether MongoDB is up.
  The TTL is small enough that recovery is still detected within one poll cycle.
* **Failure details are sanitised in production.** A pymongo connection error
  stringifies to something containing the connection URI — which contains the
  password. Outside production the message is included (truncated) because a
  developer needs it; in production only the exception class name is reported.
  The full error is always written to the application log, where it belongs.
* **A failing probe never raises.** Any exception is a failed check, not a 500.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional

from observability import metrics

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Tunables                                                                      #
# --------------------------------------------------------------------------- #
# Per-probe ceiling. 2s is longer than a healthy Mongo/Redis ping by two orders
# of magnitude, and short enough to answer well inside a typical 5s balancer
# timeout even if every dependency is hung.
DEFAULT_PROBE_TIMEOUT = float(os.environ.get("HEALTH_PROBE_TIMEOUT_SECONDS", "2.0"))

# How long a readiness result may be reused. Set to 0 to disable caching (useful
# in tests, and honest about the trade: no cache means every poller probes).
READINESS_CACHE_TTL = float(os.environ.get("HEALTH_CACHE_TTL_SECONDS", "2.0"))

# Check outcomes. Strings rather than an enum so they serialise directly into
# the JSON payload an operator (or a k8s event) reads.
PASS = "pass"
FAIL = "fail"
SKIP = "skip"

# Lifecycle states, in order.
STARTING = "starting"
READY = "ready"
STOPPING = "stopping"


# --------------------------------------------------------------------------- #
# Lifecycle state                                                              #
# --------------------------------------------------------------------------- #
class _Lifecycle:
    """Where this process is in its life.

    Module-level singleton state, because there is exactly one process and its
    lifecycle is a genuine global. Kept in a class so tests can reset it without
    reaching for `global`.

    STOPPING matters more than it appears. On SIGTERM the app should *first*
    fail readiness and only then tear down: the balancer notices within one poll
    interval and stops sending new requests, so the in-flight ones drain into a
    process that still works. Without it, the container starts closing its Mongo
    client while the balancer is still routing to it, and a deploy sheds a small
    burst of 500s every single time — the classic "why does every release show a
    blip?" that nobody ever gets around to explaining.
    """

    def __init__(self) -> None:
        self._state = STARTING
        self._started_at: Optional[float] = None

    def mark_started(self) -> None:
        if self._state == STOPPING:
            # Guard against an out-of-order startup callback resurrecting a
            # process that has already begun draining.
            return
        self._state = READY
        self._started_at = time.time()

    def mark_stopping(self) -> None:
        self._state = STOPPING

    def reset(self) -> None:
        """Test support only."""
        self._state = STARTING
        self._started_at = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_started(self) -> bool:
        return self._state in (READY, STOPPING)

    @property
    def accepts_traffic(self) -> bool:
        return self._state == READY

    @property
    def started_at(self) -> Optional[float]:
        return self._started_at


lifecycle = _Lifecycle()


# --------------------------------------------------------------------------- #
# Check model                                                                   #
# --------------------------------------------------------------------------- #
@dataclass
class CheckResult:
    """The outcome of one dependency probe."""

    name: str
    status: str
    duration_ms: float
    critical: bool
    detail: Optional[str] = None

    @property
    def healthy(self) -> Optional[bool]:
        """True/False, or None when the dependency is not configured."""
        if self.status == SKIP:
            return None
        return self.status == PASS

    def to_dict(self) -> dict:
        payload = {
            "name": self.name,
            "status": self.status,
            "critical": self.critical,
            "duration_ms": round(self.duration_ms, 2),
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass
class HealthCheck:
    """A registered dependency probe.

    ``probe`` returns ``True`` (healthy), ``False`` (unhealthy) or ``None``
    ("not configured — skip"). The three-way return is what lets an optional
    dependency like Redis be reported honestly: this codebase's cache layer falls
    back to an in-process dict when `REDIS_URL` is unset (services/cache.py), so
    an unconfigured Redis is a valid deployment, not a fault — but a *configured*
    Redis that fails to answer certainly is.

    ``critical`` decides whether a failure removes the instance from the load
    balancer. Non-critical checks are reported but do not fail readiness, which
    is how a degraded-but-serving state gets represented instead of being
    rounded up to "down".
    """

    name: str
    probe: Callable[[], Awaitable[Optional[bool]]]
    critical: bool = True
    timeout: float = DEFAULT_PROBE_TIMEOUT


_checks: Dict[str, HealthCheck] = {}


def register_check(
    name: str,
    probe: Callable[[], Awaitable[Optional[bool]]],
    *,
    critical: bool = True,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> None:
    """Register (or replace) a dependency probe.

    Replacement is intentional: re-registering under the same name during a test
    or a reload swaps the probe rather than accumulating duplicates that would
    each hit the dependency on every readiness poll.
    """
    _checks[name] = HealthCheck(name=name, probe=probe, critical=critical, timeout=timeout)
    logger.debug("health check registered: %s (critical=%s)", name, critical)


def registered_checks() -> List[str]:
    return sorted(_checks)


def clear_checks() -> None:
    """Test support only."""
    _checks.clear()


# --------------------------------------------------------------------------- #
# Failure-detail sanitisation                                                   #
# --------------------------------------------------------------------------- #
_MAX_DETAIL_CHARS = 200


def _safe_detail(exc: BaseException) -> str:
    """A failure description that is safe to return over HTTP.

    In production: the exception class name only. `ServerSelectionTimeoutError`
    stringifies to a message embedding the full Mongo connection URI —
    credentials included — and readiness is reachable by anything that can reach
    the service. Outside production the message is included and truncated,
    because a developer staring at `/api/health/ready` needs to know *which*
    host refused the connection. The unabridged error goes to the log either way.
    """
    from security.cookies import is_production

    if is_production():
        return exc.__class__.__name__
    text = f"{exc.__class__.__name__}: {exc}"
    return text[:_MAX_DETAIL_CHARS] + ("…" if len(text) > _MAX_DETAIL_CHARS else "")


# --------------------------------------------------------------------------- #
# Probe execution                                                               #
# --------------------------------------------------------------------------- #
async def _run_one(check: HealthCheck) -> CheckResult:
    """Execute one probe under timeout. Never raises."""
    started = time.perf_counter()
    try:
        outcome = await asyncio.wait_for(check.probe(), timeout=check.timeout)
        duration = (time.perf_counter() - started) * 1000
        if outcome is None:
            result = CheckResult(check.name, SKIP, duration, check.critical, "not configured")
        elif outcome:
            result = CheckResult(check.name, PASS, duration, check.critical)
        else:
            result = CheckResult(check.name, FAIL, duration, check.critical, "probe returned false")
    except asyncio.TimeoutError:
        duration = (time.perf_counter() - started) * 1000
        # A timeout is reported distinctly from a refusal: "hung" and "refused"
        # point at different causes (network partition vs. process down) and the
        # runbook for each is different.
        logger.warning("health check %s timed out after %.0fms", check.name, duration)
        result = CheckResult(
            check.name, FAIL, duration, check.critical,
            f"timeout after {check.timeout:g}s",
        )
    except Exception as exc:
        duration = (time.perf_counter() - started) * 1000
        # Full error to the log (private), sanitised summary to the response
        # (public). exc_info gives on-call the stack trace they need.
        logger.warning("health check %s failed: %s", check.name, exc, exc_info=True)
        result = CheckResult(check.name, FAIL, duration, check.critical, _safe_detail(exc))

    metrics.record_dependency_health(result.name, result.healthy, result.duration_ms / 1000.0)
    return result


@dataclass
class _CachedResults:
    at: float = 0.0
    results: List[CheckResult] = field(default_factory=list)


_cache = _CachedResults()
_cache_lock: Optional[asyncio.Lock] = None


def _get_cache_lock() -> asyncio.Lock:
    """Lazily create the lock on the running loop.

    Not created at import: an `asyncio.Lock` constructed before the event loop
    exists binds to the wrong loop, which is exactly the failure this codebase
    already documents for Motor in `tests/conftest.py`.
    """
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def clear_cache() -> None:
    """Test support only."""
    _cache.at = 0.0
    _cache.results = []


async def run_checks(*, use_cache: bool = True) -> List[CheckResult]:
    """Run every registered probe in parallel, honouring the result cache."""
    now = time.monotonic()
    if use_cache and READINESS_CACHE_TTL > 0 and _cache.results and (now - _cache.at) < READINESS_CACHE_TTL:
        return _cache.results

    checks = list(_checks.values())
    if not checks:
        return []

    lock = _get_cache_lock()
    async with lock:
        # Re-check under the lock: while this coroutine waited, a concurrent
        # poller may have already refreshed the cache. Without this second look,
        # N simultaneous probes become N *serialised* probe rounds — slower than
        # having no lock at all.
        now = time.monotonic()
        if use_cache and READINESS_CACHE_TTL > 0 and _cache.results and (now - _cache.at) < READINESS_CACHE_TTL:
            return _cache.results

        results = list(await asyncio.gather(*(_run_one(check) for check in checks)))
        _cache.at = time.monotonic()
        _cache.results = results
        return results


def is_ready(results: List[CheckResult]) -> bool:
    """Readiness verdict: every *critical* check passed or was skipped.

    A skipped (unconfigured) dependency does not block readiness — see
    :class:`HealthCheck` on why an unconfigured Redis is a valid deployment.
    """
    return all(r.status != FAIL for r in results if r.critical)


# --------------------------------------------------------------------------- #
# Report builders — the payload shapes the endpoints serve                      #
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def liveness_report() -> dict:
    """Liveness payload. Touches nothing external, by design."""
    from observability import runtime

    return {
        "status": "ok",
        "service": runtime.service_name(),
        "lifecycle": lifecycle.state,
        "uptime_seconds": round(runtime.uptime_seconds(), 3),
        "timestamp": _now_iso(),
    }


async def readiness_report(*, use_cache: bool = True) -> tuple[dict, bool]:
    """Readiness payload plus the verdict the caller turns into 200/503."""
    from observability import runtime

    results = await run_checks(use_cache=use_cache)
    checks_ok = is_ready(results)
    # Draining and still-booting instances are not ready regardless of how
    # healthy their dependencies are — the whole point of STOPPING.
    ready = checks_ok and lifecycle.accepts_traffic

    reason = None
    if not lifecycle.accepts_traffic:
        reason = "shutting down" if lifecycle.state == STOPPING else "startup incomplete"
    elif not checks_ok:
        reason = "one or more critical dependencies are unhealthy"

    payload = {
        "status": "ready" if ready else "not_ready",
        "service": runtime.service_name(),
        "lifecycle": lifecycle.state,
        "checks": [r.to_dict() for r in results],
        "timestamp": _now_iso(),
    }
    if reason:
        payload["reason"] = reason
    return payload, ready


def startup_report() -> tuple[dict, bool]:
    """Startup payload plus the verdict (200 once boot completed, else 503)."""
    from observability import runtime

    started = lifecycle.is_started
    return (
        {
            "status": "started" if started else "starting",
            "service": runtime.service_name(),
            "lifecycle": lifecycle.state,
            "uptime_seconds": round(runtime.uptime_seconds(), 3),
            "started_at": (
                datetime.fromtimestamp(lifecycle.started_at, tz=timezone.utc).isoformat()
                if lifecycle.started_at
                else None
            ),
            "timestamp": _now_iso(),
        },
        started,
    )


# --------------------------------------------------------------------------- #
# Built-in probes                                                               #
# --------------------------------------------------------------------------- #
def make_mongo_probe(db_provider: Callable[[], object]) -> Callable[[], Awaitable[Optional[bool]]]:
    """A MongoDB probe over a *late-binding* db accessor.

    `db_provider` is a zero-arg callable rather than a captured handle, matching
    the pattern `security.audit` and `security.rate_limit` already use here: the
    probe always sees the current handle, and the test suite's `FakeDB`
    monkeypatch of `server.db` is honoured with no special wiring.

    The probe is `ping` — the cheapest command the server offers. Deliberately
    not a `find` or a `dbStats`: a health check must not be able to add
    measurable load to the dependency it is checking, and a probe that reads real
    data will eventually be the reason someone disables health checking.
    """

    async def probe() -> Optional[bool]:
        db = db_provider()
        if db is None:
            return None
        await db.command("ping")
        return True

    return probe


def make_redis_probe() -> Callable[[], Awaitable[Optional[bool]]]:
    """A Redis probe that reports honestly when Redis is not configured.

    Returns None (skip) when `REDIS_URL` is unset — the supported single-process
    deployment, where `services/cache.py` falls back to an in-process dict.

    PH2.7 CHANGED HOW THIS CONNECTS, AND THE CHANGE IS THE POINT.

    This probe used to build a brand-new client on every poll and throw it away.
    That was a deliberate workaround: the only alternative at the time was
    `services.cache._get_redis()`, which latched a permanent `_redis_failed` flag
    on first failure and returned None forever — so a probe built on it would
    have reported Redis "not configured" after one transient blip and never
    noticed its recovery. A monitoring lie, and precisely the state you most need
    to see.

    The latch is gone (see `infrastructure/redis_client.py`), so the workaround
    can go with it, and dropping it fixes two problems rather than one:

    * **No more connection churn.** A TCP connect + AUTH + teardown several times
      a minute, forever, per replica — a health check quietly generating a
      meaningful share of the connection load it exists to watch.
    * **The probe now measures what the application experiences.** A private
      connection answers "could *a* connection reach Redis?". The shared client
      answers "can the pool this process actually uses reach Redis?" — which is
      the question readiness is being asked. A pool full of dead sockets, or an
      open circuit breaker, is invisible to the first and reported by the second.

    Latency is recorded by `infrastructure.redis_client.execute`, so a slow
    probe shows up in `redis_command_duration_seconds{operation="ping"}` as well
    as in `health_check_duration_seconds{dependency="redis"}`.
    """

    async def probe() -> Optional[bool]:
        from infrastructure import redis_client

        if not redis_client.is_configured():
            return None
        # When the breaker is OPEN this returns None immediately without a
        # round-trip, so the probe reports "unhealthy" in microseconds instead of
        # waiting out a connect timeout on every poll. Once the cooldown elapses,
        # this call becomes the half-open trial — which means the readiness poll
        # is what detects recovery, on a cadence that already exists.
        return (await redis_client.ping()) is not None

    return probe


def make_config_probe(
    report_provider: Callable[[], object],
) -> Callable[[], Awaitable[Optional[bool]]]:
    """A readiness probe over the boot-time configuration verdict (PH3.7).

    WHY READINESS SHOULD ASSERT THIS AT ALL, GIVEN THE BOOT ALREADY DOES

    `server.py` calls `secrets.validate_config()` at import, which raises and
    fails the process closed when a *required* value is missing. So on the happy
    path this probe can only ever pass, and that is a fair objection to it.

    It earns its place on the unhappy paths, which are the ones readiness exists
    for. The requirement set is environment-dependent: a value that is merely a
    warning in development becomes an error in production, so an instance whose
    `APP_ENV` is wrong is exactly the instance whose boot validation was too
    lenient. Configuration is also reloadable (`secrets.reload_secrets`), and a
    reload that degrades the verdict has to be able to remove the instance from
    the load balancer rather than leave it serving. And an operator reading
    `/api/health/ready` during an incident should be able to rule configuration
    in or out from the response itself instead of inferring it from the absence
    of a crash.

    WHY IT COSTS NOTHING

    It does not re-validate. It reads the report object produced once at boot —
    an attribute access — which is what keeps readiness cheap enough to poll
    every few seconds. Re-resolving secrets here would mean file I/O on every
    poll from every replica, against the one subsystem where a partial read is
    most dangerous.

    WHY IT LEAKS NOTHING

    `ConfigReport` is presence-only by construction: names and booleans, never
    values. Only the boolean `ok` crosses into the probe result, and the failure
    detail is the count of failing names, not the names — an unauthenticated
    caller learning *which* secret a deployment is missing is a reconnaissance
    gift, and the names are already in the boot log where they belong.
    """

    async def probe() -> Optional[bool]:
        report = report_provider()
        if report is None:
            # Nothing wired the report in. Reported as "not configured" rather
            # than as a failure: a missing probe input is an instrumentation
            # gap, and failing readiness on it would take a healthy instance out
            # of service because of a monitoring bug.
            return None
        errors = getattr(report, "errors", None) or []
        if errors:
            raise RuntimeError(f"configuration invalid ({len(errors)} error(s))")
        return True

    return probe

"""Operational HTTP endpoints (PH2.5).

    GET /api/health          aggregate summary (human entry point)
    GET /api/health/live     liveness   — should this container be restarted?
    GET /api/health/ready    readiness  — should it receive traffic?
    GET /api/health/startup  startup    — has it finished booting?
    GET /api/metrics            Prometheus text exposition (gated in production)
    GET /api/diagnostics        build/runtime facts        (gated in production)
    GET /api/diagnostics/redis  Redis connection/pubsub/server (gated in production)

WHY THE `/api` PREFIX
---------------------
Every route this backend serves lives under `/api`, and the deployment topology
depends on it: the ingress routes `/api/*` here and everything else to the
frontend bundle, and `security.rate_limit`'s middleware only engages on paths
starting with `/api`. A bare `/health` would be invisible to the ingress and
would silently bypass the middleware pipeline's own assumptions. The Kubernetes
convention of `/healthz` is a convention, not a requirement — matching the
deployment is worth more than matching the convention.

`/api/monitor/health` already exists and is **not** related: it returns an
AI-powered *portfolio* health analysis for an authenticated user. The naming
collision is unfortunate and predates this sprint; it is called out here so
nobody points a load balancer at it and gets a 401 every ten seconds.

WHY HEALTH IS PUBLIC AND METRICS IS NOT
---------------------------------------
Health endpoints must be reachable by infrastructure that holds no credentials —
a Docker `HEALTHCHECK`, a load balancer, a kubelet. Authenticating them is a
common and self-defeating instinct: the probe fails, the orchestrator restarts a
perfectly healthy container, and the credential becomes a new way for the
deployment to break. What they return is therefore kept minimal and boring: a
status, a lifecycle state, dependency names with pass/fail. No hostnames, no
versions, no error strings in production (`observability.health._safe_detail`).

Metrics and diagnostics are different. Metrics enumerate every route in the API
and expose traffic volumes and error rates — a reconnaissance gift and a
commercially sensitive one. Diagnostics reveal the exact build. Neither is
needed by any credential-less prober, so in production both are gated behind a
shared token; outside production they are open, because an authenticated
`/api/metrics` in development is a barrier to the exact habit this sprint is
trying to build.
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from observability import health, metrics, runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Observability"])

# Shared secret for `/api/metrics` and `/api/diagnostics` in production.
METRICS_TOKEN_ENV = "METRICS_TOKEN"
# Escape hatch for a deployment where the endpoints are already unreachable from
# outside — a private scrape network, a mesh-only listener, a sidecar. Explicit
# and named so that choosing it is a decision someone wrote down, not a default
# that quietly left metrics open on the internet.
METRICS_PUBLIC_ENV = "METRICS_ALLOW_UNAUTHENTICATED"


def _metrics_token() -> str:
    return os.environ.get(METRICS_TOKEN_ENV, "").strip()


def _metrics_public() -> bool:
    return os.environ.get(METRICS_PUBLIC_ENV, "").strip().lower() in ("1", "true", "yes")


def require_operational_access(request: Request) -> None:
    """Authorize a request to the metrics/diagnostics surface. Raises 401/403.

    Outside production: always allowed.

    In production, in order:
      * `METRICS_ALLOW_UNAUTHENTICATED=1` → allowed (network-level protection is
        the operator's declared choice);
      * no `METRICS_TOKEN` configured → **403, fail closed**. The alternative —
        defaulting to open — is how metrics endpoints end up indexed by
        Shodan. The error text names the variable, because an operator debugging
        their own 403 deserves the answer rather than a scavenger hunt;
      * token configured → require it, compared with `hmac.compare_digest` so a
        wrong guess cannot be refined byte-by-byte from response timing.

    Accepted as either `Authorization: Bearer <token>` (what a Prometheus
    `authorization` scrape config sends) or `X-Metrics-Token: <token>` (simpler
    for a curl during an incident).
    """
    from security.cookies import is_production

    if not is_production() or _metrics_public():
        return

    expected = _metrics_token()
    if not expected:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Operational endpoints are disabled in production until "
                f"{METRICS_TOKEN_ENV} is set (or {METRICS_PUBLIC_ENV}=1 for a "
                f"network-isolated scrape path)."
            ),
        )

    presented = request.headers.get("x-metrics-token", "")
    if not presented:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            presented = auth[7:].strip()

    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing operational access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# --------------------------------------------------------------------------- #
# Health                                                                        #
# --------------------------------------------------------------------------- #
# `Cache-Control: no-store` on every probe response. A CDN or reverse proxy that
# caches a health check for even 30 seconds turns it into a lie in both
# directions: a cached 200 keeps traffic flowing to a dead instance, and a cached
# 503 keeps a recovered one out of rotation.
_NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate"}


@router.get("/health/live", summary="Liveness probe")
async def health_live() -> Response:
    """Is the process alive and its event loop turning?

    Answers 200 unconditionally — and that is not a tautology, it is the design.
    For this response to be produced at all, the event loop must have scheduled
    the coroutine and the ASGI stack must be able to serialize a reply, which is
    exactly the set of failures a restart can fix. A deadlocked or wedged process
    never gets here and the probe times out, which the orchestrator reads as
    failure.

    It touches **no** dependency. See `observability.health` for why coupling
    liveness to MongoDB converts a database blip into a fleet-wide restart storm.
    """
    return JSONResponse(health.liveness_report(), headers=_NO_STORE)


@router.get("/health/ready", summary="Readiness probe")
async def health_ready() -> Response:
    """Should this instance be sent traffic right now?

    Verifies every registered critical dependency (MongoDB; Redis when
    configured) in parallel and under timeout, and additionally requires that
    startup has completed and shutdown has not begun.

    503 is a *correct* answer here, not an error — it is how an instance asks to
    be taken out of rotation while it recovers or drains. `status` in the body is
    the machine-readable verdict; `checks[]` says which dependency caused it.
    """
    payload, ready = await health.readiness_report()
    return JSONResponse(payload, status_code=200 if ready else 503, headers=_NO_STORE)


@router.get("/health/startup", summary="Startup probe")
async def health_startup() -> Response:
    """Has the application finished its startup sequence?

    This backend's boot builds ~20 Mongo indexes, restores broker sessions,
    initialises the market gateway and starts four background loops. Until that
    finishes the process is alive but not functional. A startup probe lets an
    orchestrator wait — with a generous budget — instead of applying the
    liveness timer to a boot that legitimately takes longer than a request.
    """
    payload, started = health.startup_report()
    return JSONResponse(payload, status_code=200 if started else 503, headers=_NO_STORE)


@router.get("/health", summary="Aggregate health summary")
async def health_summary() -> Response:
    """Human entry point: lifecycle, uptime and every dependency in one payload.

    Deliberately *not* the endpoint to point infrastructure at — it does
    everything readiness does plus more, and its status code follows readiness.
    It exists so an operator has one URL to open, rather than three.
    """
    payload, ready = await health.readiness_report()
    payload["uptime_seconds"] = round(runtime.uptime_seconds(), 3)
    payload["started_at"] = runtime.started_at()
    return JSONResponse(payload, status_code=200 if ready else 503, headers=_NO_STORE)


# --------------------------------------------------------------------------- #
# Metrics                                                                       #
# --------------------------------------------------------------------------- #
@router.get("/metrics", summary="Application metrics (Prometheus exposition)")
async def metrics_endpoint(request: Request, format: str = "prometheus") -> Response:
    """Serve the metric registry.

    Default output is the Prometheus text exposition format, so a future scrape
    config is the only work PH2.10 has to do. `?format=json` returns the same
    data as a JSON document — much easier to read (and to assert on) than the
    text format when someone is reading it directly during an incident.

    All aggregation happens here, at scrape time, by design: the request path
    only ever increments integers.
    """
    require_operational_access(request)

    if format.lower() == "json":
        return JSONResponse(
            {
                "service": runtime.service_name(),
                "environment": runtime.environment(),
                "metrics": metrics.registry.snapshot(),
            },
            headers=_NO_STORE,
        )
    return PlainTextResponse(
        metrics.registry.render_prometheus(),
        media_type=metrics.CONTENT_TYPE,
        headers=_NO_STORE,
    )


# --------------------------------------------------------------------------- #
# Diagnostics                                                                   #
# --------------------------------------------------------------------------- #
@router.get("/diagnostics", summary="Runtime and build diagnostics")
async def diagnostics(request: Request) -> Response:
    """What is running, since when, and built from what.

    Ends the "which version is actually deployed?" argument in one request, and
    makes a crash loop visible as a repeatedly-resetting uptime.

    Reports facts about the deployment only. It never reads a secret's value —
    `dependencies` is presence-only (`configured` / `not_configured`), obtained
    through the secrets registry's own presence accessor.
    """
    require_operational_access(request)
    return JSONResponse(
        runtime.runtime_info(lifecycle=health.lifecycle.state),
        headers=_NO_STORE,
    )


@router.get("/diagnostics/redis", summary="Redis connection and server diagnostics")
async def redis_diagnostics(request: Request, refresh: bool = False) -> Response:
    """Everything about this process's Redis connection, in one payload (PH2.7).

    `/api/health/ready` answers one bit — is Redis reachable. This answers the
    questions you actually have at 3am when that bit is 0, or (more often) when
    it is 1 and something is still wrong:

      * **connection** — is the pool connected, how many connections are in use,
        what is the circuit breaker doing, when did it last succeed and what was
        the last error;
      * **pubsub** — is each subscriber connected *right now*, how many times has
        it reconnected, how many messages has it delivered and how many did its
        handler reject. This is the section that has no equivalent in a ping: a
        process can pass every Redis health check while its subscription is dead,
        which is exactly the failure PH2.7 fixed;
      * **server** — memory against maxmemory, connected clients, evictions,
        AOF status. Sampled in the background, so the age of the sample is
        reported alongside it rather than implied.

    Gated behind the same operational token as `/api/metrics`: the payload names
    the Redis host and reports the deployment's internal topology. The URL is
    always redacted (`redis://***@host:6379/0`) — that redaction is in
    `redis_client.sanitized_url` and applies to logs too, because redis-py's
    connection errors stringify to a message containing the password.

    `?refresh=1` forces a fresh INFO round-trip instead of returning the last
    sample. Off by default so that a monitor pointed at this URL cannot drive
    load onto Redis by polling it.
    """
    require_operational_access(request)

    from infrastructure import redis_client, redis_pubsub

    if refresh and redis_client.is_configured():
        await redis_client.refresh_server_info()

    return JSONResponse(
        {
            "service": runtime.service_name(),
            "environment": runtime.environment(),
            "connection": redis_client.stats(),
            "pubsub": {
                "channels": redis_pubsub.active_channels(),
                "subscribers": redis_pubsub.subscriber_stats(),
            },
            "server": redis_client.server_info(),
        },
        headers=_NO_STORE,
    )

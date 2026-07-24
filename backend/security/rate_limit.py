"""Centralized rate limiting & abuse protection (PH1.7).

Single source of truth for how StockAssist AI throttles requests and mitigates
brute-force / flooding abuse. Before this sprint the only mechanism was an inline
``db.login_attempts`` lockout scoped to ``/api/auth/login`` (SECURITY_ARCHITECTURE
§21). That logic is **folded into this module** so there is exactly one limiter,
one storage model, and one set of policies — no two lockout systems running in
parallel on the same endpoint.

What this provides:

* **Named, per-endpoint policies** (login, register, refresh, password, the
  authenticated-API tier, the public-API tier) — each a ``(limit, window,
  scope)`` triple, every field env-overridable so a deployment can retune without
  a code change.
* **A pluggable storage interface** (``RateLimitStore``) so the current
  MongoDB-backed store can be swapped for Redis later *without touching a single
  caller*. Mongo is used now — matching the pre-existing persistence choice for
  ``login_attempts`` — because it is already wired, durable across restarts, and
  shared across worker processes (an in-memory counter would silently reset on
  deploy and never see sibling workers). The interface is intentionally the exact
  shape a Redis ``INCR``/``EXPIRE`` store implements.
* **Fixed-window counting + progressive lockout.** A window counter bounds the
  request rate; once a policy is tripped a lockout is written with an automatic
  expiry, and (for ``escalate`` policies like login) each repeated trip doubles
  the lockout up to a cap — turning a persistent attacker's cost up exponentially
  while a one-off burst clears on its own.
* **``Retry-After`` on every rejection**, plus ``X-RateLimit-*`` headers on
  throttled tiers, so clients back off correctly instead of hammering.

Two integration surfaces:

1. **Inline** (``server.py`` auth endpoints) — login/register/refresh call the
   limiter directly because their identity (ip:email, session id) comes from the
   request body / token, and login must count *failures only* (a successful login
   must not consume the budget). ``peek`` / ``record_failure`` / ``reset`` serve
   that failure-counted pattern; ``check`` serves count-every-request endpoints.
2. **Middleware** (``RateLimitMiddleware``) — a platform-wide tier applied to all
   ``/api`` traffic: authenticated requests are limited per user, anonymous ones
   per client IP. This is the endpoint-flooding backstop.

Fail-closed intent with a pragmatic exception: a *storage* error must never take
the whole API down, so a store exception degrades to "allow" and is logged
(availability over a hard fail for the throttle layer specifically — the auth,
CSRF, and token layers remain fail-closed). Every *policy* decision itself is
strict.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from security import jwt as jwt_service
from security.cookies import ACCESS_TOKEN_COOKIE

logger = logging.getLogger(__name__)

COLLECTION = "rate_limits"


# --------------------------------------------------------------------------- #
# Policy model                                                                  #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RateLimitPolicy:
    """One throttling rule.

    ``scope`` is documentation of how the *identity* is keyed by the caller
    (``ip``, ``ip:account``, ``session``, ``user``) — the limiter itself is
    identity-agnostic and just namespaces counters by ``name``. ``block_seconds``
    is the lockout applied once the policy is tripped; ``None`` means "until the
    current window resets". ``escalate`` doubles the lockout on each repeated trip
    up to ``max_block_seconds`` (progressive penalty)."""
    name: str
    limit: int
    window_seconds: int
    scope: str
    block_seconds: Optional[int] = None
    escalate: bool = False
    max_block_seconds: int = 3600


@dataclass
class RateLimitResult:
    """Outcome of a limiter decision. ``retry_after``/``reset`` are seconds and an
    absolute epoch respectively; ``remaining`` is best-effort headroom for headers."""
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    reset: int


# --------------------------------------------------------------------------- #
# Storage interface + MongoDB implementation                                    #
# --------------------------------------------------------------------------- #
class RateLimitStore:
    """Abstract counter/lockout store. A Redis implementation would provide the
    same five methods (``INCR``+``EXPIRE`` for counting, a keyed value with TTL
    for the lockout) with no change to :class:`RateLimiter` or its callers."""

    async def count(self, key: str, window_seconds: int) -> Tuple[int, int]:
        raise NotImplementedError

    async def hit(self, key: str, window_seconds: int) -> Tuple[int, int]:
        raise NotImplementedError

    async def get_block(self, key: str) -> Optional[Tuple[int, int]]:
        raise NotImplementedError

    async def get_trips(self, key: str) -> int:
        raise NotImplementedError

    async def set_block(self, key: str, until_epoch: int, trips: int,
                        expires_epoch: Optional[int] = None) -> None:
        raise NotImplementedError

    async def clear(self, key: str) -> None:
        raise NotImplementedError


def _as_dt(epoch: int) -> datetime:
    """A TTL-index-friendly UTC datetime a few seconds past ``epoch`` so Mongo
    reaps the document only after it can no longer affect a decision."""
    return datetime.fromtimestamp(epoch + 5, tz=timezone.utc)


class MongoRateLimitStore(RateLimitStore):
    """Fixed-window counter + lockout backed by one MongoDB collection.

    Each window is a bucket document keyed ``(key, kind='count', window_start)``;
    the equality fields are seeded on upsert (MongoDB copies a query's equality
    predicates into an inserted document), so ``$inc`` alone maintains the count
    with no ``$setOnInsert`` needed — which also keeps it working against the
    in-memory test double. A single lockout document per key
    (``kind='block'``) records ``blocked_until`` and the escalating ``trips``
    count. A TTL index on ``expires_at`` reaps both.

    Concurrency note: ``hit`` is a non-atomic increment-then-read (two ops), so
    two simultaneous requests can momentarily read the same count — a benign
    over/under-count of at most the concurrency width, acceptable for a throttle
    and closed by an atomic Redis ``INCR`` store later. It is never wrong in the
    direction that *locks out a legitimate user early* under normal load."""

    def __init__(self, collection):
        self._col = collection

    @staticmethod
    def _window(window_seconds: int) -> Tuple[int, int, int]:
        now = int(time.time())
        start = now - (now % window_seconds)
        return now, start, start + window_seconds

    async def count(self, key: str, window_seconds: int) -> Tuple[int, int]:
        _, start, reset = self._window(window_seconds)
        doc = await self._col.find_one({"key": key, "kind": "count", "window_start": start})
        return (int(doc.get("count", 0)) if doc else 0), reset

    async def hit(self, key: str, window_seconds: int) -> Tuple[int, int]:
        _, start, reset = self._window(window_seconds)
        flt = {"key": key, "kind": "count", "window_start": start}
        await self._col.update_one(
            flt, {"$inc": {"count": 1}, "$set": {"expires_at": _as_dt(reset)}}, upsert=True
        )
        doc = await self._col.find_one(flt)
        return (int(doc.get("count", 1)) if doc else 1), reset

    async def get_block(self, key: str) -> Optional[Tuple[int, int]]:
        """The *active* lockout (``blocked_until`` still in the future), or None."""
        doc = await self._col.find_one({"key": key, "kind": "block"})
        if not doc:
            return None
        until = int(doc.get("blocked_until", 0))
        if until > int(time.time()):
            return until, int(doc.get("trips", 0))
        return None

    async def get_trips(self, key: str) -> int:
        """The persisted trip count regardless of whether the block is still
        active — this is what lets a progressive penalty keep escalating across
        successive lockouts (an expired block still remembers how many times the
        identity has been tripped, until its longer memory TTL reaps the doc)."""
        doc = await self._col.find_one({"key": key, "kind": "block"})
        return int(doc.get("trips", 0)) if doc else 0

    async def set_block(self, key: str, until_epoch: int, trips: int,
                        expires_epoch: Optional[int] = None) -> None:
        # The doc lives until ``expires_epoch`` (the escalation-memory horizon),
        # which outlives the block itself so ``trips`` survives to inform the next
        # lockout. Defaults to the block's own end when no memory is requested.
        expires = expires_epoch if expires_epoch is not None else until_epoch
        await self._col.update_one(
            {"key": key, "kind": "block"},
            {"$set": {"blocked_until": until_epoch, "trips": trips, "expires_at": _as_dt(expires)}},
            upsert=True,
        )

    async def clear(self, key: str) -> None:
        await self._col.delete_many({"key": key})


# --------------------------------------------------------------------------- #
# The limiter                                                                   #
# --------------------------------------------------------------------------- #
class RateLimiter:
    """Applies :class:`RateLimitPolicy` decisions against a :class:`RateLimitStore`.

    Instantiate per call with the live store (``RateLimiter(store).check(...)``),
    mirroring the ``SessionStore(db)`` pattern, so nothing captures a stale DB
    handle and tests can inject the in-memory double."""

    def __init__(self, store: RateLimitStore):
        self._store = store

    def _key(self, policy: RateLimitPolicy, identity: str) -> str:
        return f"{policy.name}:{identity}"

    async def _blocked(self, key: str) -> Optional[RateLimitResult]:
        block = await self._store.get_block(key)
        if block is None:
            return None
        until, _ = block
        return RateLimitResult(False, 0, 0, max(until - int(time.time()), 1), until)

    async def _trip(self, policy: RateLimitPolicy, key: str, reset: int) -> RateLimitResult:
        """Record a policy trip and return the resulting lockout decision.

        The trip count is read from the store's *persisted* history (not just the
        active block) so an ``escalate`` policy keeps doubling the lockout across
        successive lockouts, up to ``max_block_seconds``."""
        now = int(time.time())
        trips = await self._store.get_trips(key) + 1
        base = policy.block_seconds if policy.block_seconds is not None else max(reset - now, 1)
        if policy.escalate and trips > 1:
            base = min(base * (2 ** (trips - 1)), policy.max_block_seconds)
        until = now + max(base, 1)
        # Keep the trip history alive past the block so the next trip can escalate.
        memory = max(policy.window_seconds, policy.max_block_seconds)
        await self._store.set_block(key, until, trips, expires_epoch=until + memory)
        # Audit the lockout (PH1.10). This is the single choke point where a
        # policy is actually tripped — inline (login/register/refresh/password)
        # and middleware paths both land here — so one hook covers every limiter
        # without per-caller duplication. The identity is stored verbatim (it may
        # embed an email for ip:account policies — expected in an audit record);
        # audit.log_event is fail-safe, so a logging error can never break the
        # throttle. Imported lazily to avoid a hard import-time dependency.
        try:
            from security import audit
            await audit.log_event(
                audit.RATE_LIMIT_TRIGGERED, target=key,
                metadata={"policy": policy.name, "scope": policy.scope,
                          "trips": trips, "retry_after": until - now},
            )
        except Exception:  # pragma: no cover - defensive; audit is best-effort
            pass
        return RateLimitResult(False, policy.limit, 0, until - now, until)

    async def peek(self, policy: RateLimitPolicy, identity: str) -> RateLimitResult:
        """Read-only check used *before* an attempt (e.g. login): is this identity
        currently locked out, or already at its limit? Does NOT consume budget."""
        key = self._key(policy, identity)
        blocked = await self._blocked(key)
        if blocked:
            return blocked
        count, reset = await self._store.count(key, policy.window_seconds)
        if count >= policy.limit:
            return RateLimitResult(False, policy.limit, 0, max(reset - int(time.time()), 1), reset)
        return RateLimitResult(True, policy.limit, policy.limit - count, 0, reset)

    async def record_failure(self, policy: RateLimitPolicy, identity: str) -> RateLimitResult:
        """Count one failed attempt (login/password). Reaching the limit arms the
        lockout. Returns the post-increment decision."""
        key = self._key(policy, identity)
        count, reset = await self._store.hit(key, policy.window_seconds)
        if count >= policy.limit:
            return await self._trip(policy, key, reset)
        return RateLimitResult(True, policy.limit, policy.limit - count, 0, reset)

    async def check(self, policy: RateLimitPolicy, identity: str) -> RateLimitResult:
        """Count-every-request decision (registration, refresh, API tiers). The
        request that pushes the count *past* the limit is rejected and arms the
        lockout; earlier ones pass with decreasing headroom."""
        key = self._key(policy, identity)
        blocked = await self._blocked(key)
        if blocked:
            return blocked
        count, reset = await self._store.hit(key, policy.window_seconds)
        if count > policy.limit:
            return await self._trip(policy, key, reset)
        return RateLimitResult(True, policy.limit, policy.limit - count, 0, reset)

    async def reset(self, policy: RateLimitPolicy, identity: str) -> None:
        """Clear all counters/lockout for an identity — called on a successful
        login so a legitimate user is never punished for prior typos."""
        await self._store.clear(self._key(policy, identity))


def get_limiter(db) -> RateLimiter:
    """A limiter bound to the live DB's ``rate_limits`` collection. Cheap to
    construct per call (like ``SessionStore(db)``)."""
    return RateLimiter(MongoRateLimitStore(getattr(db, COLLECTION)))


# --------------------------------------------------------------------------- #
# Policies — env-overridable defaults (SECURITY.md / roadmap PH1.7).            #
# Override with RATE_LIMIT_<NAME>="<limit>/<window_seconds>", e.g.              #
# RATE_LIMIT_LOGIN="5/900".                                                      #
# --------------------------------------------------------------------------- #
def _policy(name: str, limit: int, window: int, scope: str, **kw) -> RateLimitPolicy:
    raw = os.environ.get(f"RATE_LIMIT_{name.upper()}", "").strip()
    if raw and "/" in raw:
        try:
            l_str, w_str = raw.split("/", 1)
            limit, window = int(l_str), int(w_str)
        except ValueError:
            logger.warning("Invalid RATE_LIMIT_%s=%r; using default %d/%d.", name.upper(), raw, limit, window)
    return RateLimitPolicy(name, limit, window, scope, **kw)


# 5 attempts / 15 min per ip:account, escalating lockout — folds in the prior
# db.login_attempts logic (was 5 / 15 min, non-escalating).
LOGIN = _policy("login", 5, 15 * 60, "ip:account", escalate=True)
# 5 registrations / hour per IP — anti account-spam.
REGISTER = _policy("register", 5, 60 * 60, "ip")
# 20 refreshes / min per session — a legitimate SPA refreshes far less often.
REFRESH = _policy("refresh", 20, 60, "session")
# 5 / hour per ip:account — password-change/reset endpoints (future PH1.x).
PASSWORD = _policy("password", 5, 60 * 60, "ip:account")
# Platform tiers.
AUTHENTICATED_API = _policy("api_user", 120, 60, "user")
PUBLIC_API = _policy("api_ip", 60, 60, "ip")


# --------------------------------------------------------------------------- #
# Request helpers (shared by inline callers and the middleware)                 #
# --------------------------------------------------------------------------- #
def client_ip(request: Request) -> str:
    """Best-effort client IP. Honors the first hop of ``X-Forwarded-For`` behind a
    trusted proxy, else the socket peer. ``unknown`` if neither is available (so a
    key is always well-formed)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _authenticated_user_id(request: Request) -> Optional[str]:
    """The ``sub`` of the request's access token (cookie or Bearer), or ``None``.
    Pure decode, no DB — used only to pick the per-user vs per-IP tier."""
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
    if not token:
        return None
    try:
        claims = jwt_service.decode_token(token, expected_type="access")
    except jwt_service.TokenError:
        return None
    return claims.get("sub")


def _rate_headers(result: RateLimitResult) -> dict:
    headers = {
        "X-RateLimit-Limit": str(result.limit),
        "X-RateLimit-Remaining": str(max(result.remaining, 0)),
        "X-RateLimit-Reset": str(result.reset),
    }
    if not result.allowed:
        headers["Retry-After"] = str(result.retry_after)
    return headers


def raise_if_limited(result: RateLimitResult, detail: str = "Too many requests. Please try again later.") -> None:
    """Turn a denied decision into a ``429`` with ``Retry-After`` for inline
    endpoint use. Imported lazily to keep this module FastAPI-optional."""
    if result.allowed:
        return
    from fastapi import HTTPException
    raise HTTPException(status_code=429, detail=detail, headers=_rate_headers(result))


# --------------------------------------------------------------------------- #
# Platform-wide middleware                                                       #
# --------------------------------------------------------------------------- #
# Endpoints with their own stricter *inline* limits — excluded from the broad
# tier so their specific policy is authoritative and requests aren't double
# counted. Matched by exact path.
_MIDDLEWARE_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh",
    "/api",              # health check — hit by load balancers/probes
    # Operational endpoints (PH2.5). Exempt for the same reason as /api, and the
    # reason is worth being explicit about: a probe cadence of one request every
    # 10 seconds, multiplied by a kubelet + a load balancer + an uptime monitor,
    # would consume the anonymous per-IP budget and start returning 429. The
    # orchestrator reads that 429 as "unhealthy" and restarts a container that
    # was fine — the rate limiter would be manufacturing the outage it exists to
    # prevent. Metrics/diagnostics are gated by their own token in production
    # (observability/routes.py), so exempting them costs nothing.
    "/api/health",
    "/api/health/live",
    "/api/health/ready",
    "/api/health/startup",
    "/api/metrics",
    "/api/diagnostics",
}


class RateLimitMiddleware:
    """Platform-wide flooding backstop over all ``/api`` traffic.

    Authenticated requests are limited per user (``AUTHENTICATED_API``), anonymous
    ones per client IP (``PUBLIC_API``). Endpoints with their own inline limits and
    the health check are exempt; preflight ``OPTIONS`` and non-HTTP scopes pass
    through. A storage failure degrades to allow (logged) so the throttle can
    never take the API down. ``X-RateLimit-*`` headers are added to every counted
    response; a rejection is a ``403``-free ``429`` with ``Retry-After``.

    ``db_provider`` is a zero-arg callable returning the live DB handle, so the
    middleware always uses the current handle (and tests can swap in the double)
    rather than capturing one at wiring time."""

    def __init__(self, app: ASGIApp, db_provider) -> None:
        self.app = app
        self._db_provider = db_provider

    def _exempt(self, request: Request) -> bool:
        path = request.url.path
        if request.method.upper() == "OPTIONS":
            return True
        if not path.startswith("/api"):
            return True
        return path in _MIDDLEWARE_EXEMPT_PATHS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if self._exempt(request):
            await self.app(scope, receive, send)
            return

        user_id = _authenticated_user_id(request)
        if user_id:
            policy, identity = AUTHENTICATED_API, f"user:{user_id}"
        else:
            policy, identity = PUBLIC_API, f"ip:{client_ip(request)}"

        try:
            result = await get_limiter(self._db_provider()).check(policy, identity)
        except Exception:  # storage failure → fail open for availability (logged)
            logger.exception("Rate-limit store error; allowing request.")
            await self.app(scope, receive, send)
            return

        if not result.allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please slow down.", "code": "RATE_LIMITED"},
                headers=_rate_headers(result),
            )
            await response(scope, receive, send)
            return

        extra = _rate_headers(result)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                for k, v in extra.items():
                    headers.append((k.encode("latin-1"), v.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def apply_rate_limiting(app, db_provider) -> None:
    """Attach the platform-wide rate-limit middleware.

    Wired *before* CORS and the security headers (so they wrap it — a ``429`` still
    carries CORS + security headers a browser needs to read it) yet *inside* them,
    i.e. it runs after the cheap origin/header stamping but before route dispatch
    and any handler/DB work (SECURITY_ARCHITECTURE.md §27)."""
    app.add_middleware(RateLimitMiddleware, db_provider=db_provider)
    logger.info(
        "Rate limiting enabled (login %d/%ds, register %d/%ds, refresh %d/%ds, "
        "api-user %d/%ds, api-ip %d/%ds; MongoDB store).",
        LOGIN.limit, LOGIN.window_seconds, REGISTER.limit, REGISTER.window_seconds,
        REFRESH.limit, REFRESH.window_seconds, AUTHENTICATED_API.limit,
        AUTHENTICATED_API.window_seconds, PUBLIC_API.limit, PUBLIC_API.window_seconds,
    )

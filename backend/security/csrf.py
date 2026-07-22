"""Centralized CSRF protection (PH1.7).

Single source of truth for how StockAssist AI defends **cookie-authenticated**,
state-changing requests against Cross-Site Request Forgery. No CSRF token is
minted, read, or validated outside this module — that keeps the token format,
the binding rule, and the exemption policy in one auditable place instead of
scattered per-route checks.

Why a token layer on top of ``SameSite=Lax`` (SECURITY_ARCHITECTURE.md §18):
``SameSite=Lax`` (``security.cookies``) is the effective baseline — the browser
withholds the auth cookies on the classic cross-site sub-request vectors
(``<img>``, auto-submitting ``<form>``, cross-site ``fetch``). But ``Lax`` still
attaches cookies on *top-level* cross-site navigations (a link/redirect the
attacker lures the victim into), which for a ``POST`` form is a real residual
CSRF surface, and a misconfigured ``SameSite=None`` deployment loses the baseline
entirely. A synchronizer/double-submit token closes that gap independently of the
cookie's ``SameSite`` attribute — defense in depth.

Design — **signed double-submit, bound to the session** (OWASP "signed
double-submit cookie"):

* On session issuance (login / register / OAuth) and on every refresh, the
  server plants a ``csrf_token`` cookie. Unlike the auth cookies it is **not**
  ``HttpOnly`` — a same-origin script must be able to read it to echo it back in
  the ``X-CSRF-Token`` request header. It carries no secret: it is a nonce plus
  an HMAC over ``session_id + nonce`` keyed by a server secret.
* A mutating request is accepted only when the header token and the cookie token
  are **both present, equal** (double-submit — a cross-site attacker can send the
  victim's cookie but cannot read its value to forge the matching header, because
  same-origin policy hides the cookie value and our CORS allowlist forbids
  reading cross-origin responses), **and the token's HMAC verifies against the
  session the request authenticates as** (binding — a token minted for another
  session, or a hand-crafted value, is rejected). Constant-time comparisons
  throughout.

* **Bearer requests are exempt by design.** The StockAssist SPA authenticates
  with an ``Authorization: Bearer`` token held in ``localStorage`` (see
  ``server.get_current_user``). A cross-site attacker cannot read that token
  (same-origin policy) and cannot attach a custom ``Authorization`` header on a
  cross-site request without a CORS preflight our allowlist denies — so a
  Bearer-authenticated request is inherently CSRF-safe and carries no ambient
  cookie authority to abuse. CSRF enforcement therefore targets exactly the
  cookie-only attack surface: a request that authenticates via the ambient
  ``access_token`` cookie with **no** Bearer header. This is also what makes the
  layer non-breaking — the existing Bearer-based client is unaffected, while a
  future cookie-only client is already protected.

* **Fail closed.** A cookie-authenticated mutating request with a missing,
  malformed, mismatched, or wrong-session token is a ``403`` — never silently
  allowed. Safe methods (``GET``/``HEAD``/``OPTIONS``/``TRACE``) and the
  unauthenticated auth-bootstrap endpoints (login/register/refresh/OAuth, which
  establish or rotate the very session the token binds to) are exempt.

* **Secret & production posture.** The HMAC key is ``CSRF_SECRET`` when set,
  otherwise the already-required ``JWT_SECRET`` (domain-separated by a fixed
  message prefix so the two uses can never collide). No weak default. Cookie
  ``Secure``/``SameSite``/``Domain`` are resolved through ``security.cookies`` so
  the CSRF cookie shares the exact production-hardened posture of the auth
  cookies and the two policies can never drift.
"""
from __future__ import annotations

import hmac
import logging
import os
from hashlib import sha256
from typing import Optional, Set

import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

# Reuse the single cookie-posture primitives so the CSRF cookie's
# Secure/SameSite/Domain can never drift from the auth cookies (PH1.3).
from security.cookies import (
    cookie_domain,
    _resolved_flags,  # (secure, samesite) resolved as a consistent pair
    ACCESS_TOKEN_COOKIE,
)
from security import jwt as jwt_service

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Identity constants — one place, no magic strings at call sites.               #
# --------------------------------------------------------------------------- #
CSRF_COOKIE = "csrf_token"          # readable by JS (NOT HttpOnly) — echoed in the header
CSRF_HEADER = "x-csrf-token"        # compared case-insensitively (ASGI headers are lowercased)
CSRF_COOKIE_PATH = "/"
CSRF_TOKEN_MAX_AGE = 604800         # 7d — matches the refresh-token lifetime (session horizon)

# HMAC domain-separation prefix: even if CSRF_SECRET is unset and we fall back to
# JWT_SECRET, a CSRF MAC can never be confused with (or replayed as) a JWT.
_MAC_PREFIX = "stockassist.csrf.v1"

# HTTP methods that never change state — exempt from CSRF (RFC 7231 safe methods).
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Auth-bootstrap endpoints exempt from CSRF: they are either unauthenticated or
# operate on the cookie that establishes/rotates the session the token binds to
# (a forged refresh/login is harmless — it only rotates the victim's own tokens
# or performs a login the attacker cannot observe). Enforcing here would instead
# break the legitimate cookie-only refresh path. Matched by exact path.
_DEFAULT_EXEMPT_PATHS: Set[str] = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/api/auth/google/session",
    "/api/auth/google/login-url",
    # Identity-recovery entrypoints (PH1.8). These carry their own single-use,
    # signed authorization (a recovery token) or are anonymous (forgot-password),
    # so they rely on NO ambient cookie authority and are CSRF-safe by
    # construction — exactly like the auth-bootstrap paths above. Exempting them
    # also keeps them working for a logged-out user (who has no CSRF cookie).
    # The *authenticated* recovery actions (change-password, verify-email/request)
    # are deliberately NOT here — they stay CSRF-protected for cookie clients.
    "/api/auth/verify-email",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
}


# --------------------------------------------------------------------------- #
# Secret resolution                                                             #
# --------------------------------------------------------------------------- #
def _csrf_secret() -> str:
    """The HMAC key. ``CSRF_SECRET`` if configured, else the required
    ``JWT_SECRET``. No weak default — a missing ``JWT_SECRET`` raises (fail
    closed), exactly as ``security.jwt`` does."""
    configured = os.environ.get("CSRF_SECRET", "").strip()
    if configured:
        return configured
    return jwt_service.get_secret()


# --------------------------------------------------------------------------- #
# Token mint / verify — nonce + HMAC(secret, prefix|sid|nonce), bound to a sid  #
# --------------------------------------------------------------------------- #
def _sign(session_id: str, nonce: str) -> str:
    msg = f"{_MAC_PREFIX}.{session_id}.{nonce}".encode("utf-8")
    return hmac.new(_csrf_secret().encode("utf-8"), msg, sha256).hexdigest()


def issue_token(session_id: str) -> str:
    """Mint a CSRF token bound to ``session_id``.

    Format ``<nonce>.<hex-hmac>``. The nonce makes each token unique; the HMAC
    binds it to the session and makes it unforgeable without the server secret."""
    nonce = secrets.token_urlsafe(24)
    return f"{nonce}.{_sign(session_id, nonce)}"


def verify_token(token: Optional[str], session_id: str) -> bool:
    """True iff ``token`` is a well-formed, correctly-signed token for
    ``session_id``. Constant-time signature comparison; never raises."""
    if not token or not session_id:
        return False
    nonce, _, sig = token.partition(".")
    if not nonce or not sig:
        return False
    return hmac.compare_digest(sig, _sign(session_id, nonce))


def tokens_match(header_token: Optional[str], cookie_token: Optional[str]) -> bool:
    """Constant-time equality for the double-submit check (header vs cookie)."""
    if not header_token or not cookie_token:
        return False
    return hmac.compare_digest(header_token, cookie_token)


# --------------------------------------------------------------------------- #
# Cookie set / clear — shares the auth-cookie Secure/SameSite/Domain posture.   #
# The CSRF cookie is deliberately NOT HttpOnly (the SPA must read it).          #
# --------------------------------------------------------------------------- #
def set_csrf_cookie(response: Response, session_id: str) -> str:
    """Plant a freshly-minted CSRF cookie bound to ``session_id`` and return the
    token (so a caller may also surface it in a JSON body if desired)."""
    token = issue_token(session_id)
    secure, samesite = _resolved_flags()
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        httponly=False,          # the SPA reads this to echo it in X-CSRF-Token
        secure=secure,
        samesite=samesite,
        max_age=CSRF_TOKEN_MAX_AGE,
        path=CSRF_COOKIE_PATH,
        domain=cookie_domain(),
    )
    return token


def clear_csrf_cookie(response: Response) -> None:
    """Drop the CSRF cookie on logout, mirroring its set attributes so the
    browser actually deletes it."""
    secure, samesite = _resolved_flags()
    response.delete_cookie(
        key=CSRF_COOKIE,
        path=CSRF_COOKIE_PATH,
        domain=cookie_domain(),
        secure=secure,
        httponly=False,
        samesite=samesite,
    )


# --------------------------------------------------------------------------- #
# Request-level decision                                                         #
# --------------------------------------------------------------------------- #
def _bearer_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    return None


def _session_id_from_cookie(request: Request) -> Optional[str]:
    """The ``sid`` of the session this request authenticates as *via cookie*, or
    ``None`` when there is no valid access-token cookie. Pure token decode — no
    database call. An expired/invalid cookie yields ``None`` so the auth layer
    (not CSRF) owns that rejection."""
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        return None
    try:
        claims = jwt_service.decode_token(token, expected_type="access")
    except jwt_service.TokenError:
        return None
    return claims.get("sid")


def requires_csrf(request: Request, exempt_paths: Set[str]) -> bool:
    """Whether this request must present a valid CSRF token.

    True only for a mutating method, a non-exempt path, **no** Bearer header
    (Bearer requests are CSRF-safe by construction — see module docstring), and a
    genuine cookie-authenticated session. Everything else is out of scope for
    CSRF and handled by the auth layer or is inherently safe."""
    if request.method.upper() in SAFE_METHODS:
        return False
    if request.url.path in exempt_paths:
        return False
    if _bearer_token(request) is not None:
        return False
    return _session_id_from_cookie(request) is not None


def validate(request: Request) -> bool:
    """Validate the double-submit token for a request already determined to
    require CSRF. Requires header==cookie AND a signature bound to the cookie
    session. Returns False (→ 403) on any failure."""
    session_id = _session_id_from_cookie(request)
    if not session_id:
        return False
    header_token = request.headers.get(CSRF_HEADER)
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if not tokens_match(header_token, cookie_token):
        return False
    return verify_token(header_token, session_id)


# --------------------------------------------------------------------------- #
# Middleware                                                                     #
# --------------------------------------------------------------------------- #
class CSRFMiddleware:
    """Pure-ASGI CSRF guard.

    Runs the ``requires_csrf`` → ``validate`` decision on every ``http`` request
    and short-circuits a failing one with a ``403`` before it reaches any route
    handler. WebSocket upgrades pass through untouched. The request body is never
    read, so the middleware is safe for streaming and does not interfere with
    downstream body parsing."""

    def __init__(self, app: ASGIApp, exempt_paths: Optional[Set[str]] = None) -> None:
        self.app = app
        self.exempt_paths = set(exempt_paths) if exempt_paths else set(_DEFAULT_EXEMPT_PATHS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if requires_csrf(request, self.exempt_paths) and not validate(request):
            # Audit the rejection (PH1.10). Fail-safe: security.audit never raises,
            # so a logging hiccup can never turn a clean 403 into a 500. Imported
            # lazily to keep this module free of a hard audit dependency at import.
            try:
                from security import audit
                await audit.log_event(
                    audit.CSRF_VALIDATION_FAILURE, request=request,
                    metadata={"path": request.url.path, "method": request.method},
                )
            except Exception:  # pragma: no cover - defensive; audit is best-effort
                pass
            response = JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid", "code": "CSRF_FAILED"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def apply_csrf_protection(app, exempt_paths: Optional[Set[str]] = None) -> None:
    """Attach the centralized CSRF guard to a FastAPI/Starlette app.

    Wired so that CORS and the security headers wrap it (added *before* them),
    which means a ``403`` CSRF rejection still carries the CORS and security
    headers a browser needs to read it (see SECURITY_ARCHITECTURE.md §27)."""
    app.add_middleware(CSRFMiddleware, exempt_paths=exempt_paths)
    logger.info("CSRF protection enabled (signed double-submit, Bearer-exempt).")

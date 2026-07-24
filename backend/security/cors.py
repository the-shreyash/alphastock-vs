"""Centralized Cross-Origin Resource Sharing (CORS) policy (PH1.4).

Single source of truth for how StockAssist AI answers cross-origin browser
requests. No CORS configuration may live anywhere else — that is what
guarantees a consistent, production-safe posture (origin allowlisting,
credentials, methods, headers) and removes the previous wildcard default that
was both unsafe and specification-invalid alongside credentials.

Why this exists (see PRODUCTION_HARDENING.md / PRODUCTION_ROADMAP.md PH1.4):

The prior configuration was::

    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],

That defaults to ``Access-Control-Allow-Origin: *`` **with credentials
enabled**. The Fetch standard forbids this combination: a browser will refuse
to expose a credentialed response whose ACAO is the wildcard, so it is at once
insecure (any origin is trusted) and broken (real credentialed requests fail).
This module replaces it with an environment-driven, exact-match origin
allowlist.

Design decisions:

* **Origins** — the canonical source is ``CORS_ALLOWED_ORIGINS`` (comma
  separated, exact scheme+host+port, no trailing slash). Legacy
  ``CORS_ORIGINS`` and ``FRONTEND_URL`` are still honored as inputs so existing
  deployments keep working (backward compatible). A literal ``*`` is stripped
  from any source — a wildcard can never enter the allowlist. Outside
  production, when nothing is configured, a small set of local dev origins
  (``http://localhost:3000``, ``http://localhost:5173``) is assumed so the app
  runs out of the box. In production nothing is assumed: an unconfigured
  allowlist is empty and every cross-origin request is rejected (fail closed).
* **Exact match, no regex** — Starlette's ``CORSMiddleware`` compares the
  request ``Origin`` against this list verbatim, so an unknown origin never
  receives ``Access-Control-Allow-Origin`` and the browser blocks it.
* **Credentials** — allowed (``True``): the app authenticates with HttpOnly
  session cookies sent via ``withCredentials``. This is *only* ever safe
  because origins are an exact allowlist and never the wildcard — the two
  invariants are enforced together here.
* **Methods** — restricted to the verbs the REST API actually uses rather than
  ``*``. ``OPTIONS`` is included so the middleware can answer preflight.
* **Request headers** — restricted to the headers the frontend actually sends
  (``Authorization``, ``Content-Type``, ``Accept``, ``Origin``,
  ``X-Requested-With``) rather than reflecting ``*``.
* **Exposed response headers** — none. Authentication is cookie-based, so the
  client never needs to read a custom response header; nothing extra is
  exposed.

Note on OAuth: the Google OAuth *redirect-URI* allowlist
(``_allowed_google_redirect_uris`` in ``server.py``) is PH1.2 scope and derives
its origins from ``FRONTEND_URL`` / ``CORS_ORIGINS`` independently. Deployments
should continue to set ``FRONTEND_URL`` for OAuth regardless of the CORS var
they choose.
"""
from __future__ import annotations

import logging
import os
from typing import List

from starlette.middleware.cors import CORSMiddleware

# Reuse the single production-detection primitive so environment semantics never
# drift between the cookie policy and the CORS policy.
from security.cookies import is_production

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Policy constants — one place, no magic values scattered at the call site.     #
# --------------------------------------------------------------------------- #
# Canonical origin variable (single source of truth). The two names below are
# legacy inputs kept only for backward compatibility.
CORS_ALLOWED_ORIGINS_ENV = "CORS_ALLOWED_ORIGINS"
LEGACY_CORS_ORIGINS_ENV = "CORS_ORIGINS"
LEGACY_FRONTEND_URL_ENV = "FRONTEND_URL"

# Assumed only outside production when nothing is configured, so local dev works
# without ceremony. CRA defaults to :3000, Vite to :5173.
DEFAULT_DEV_ORIGINS = ("http://localhost:3000", "http://localhost:5173")

# Verbs the REST API actually serves. OPTIONS lets the middleware answer
# preflight requests.
ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

# Request headers the frontend actually sends. Anything else is rejected at
# preflight instead of being blanket-reflected.
ALLOWED_HEADERS = ["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"]

# Response headers the browser is permitted to read. Cookie-based auth needs
# none; the one exception is the request-correlation ID.
#
# X-Request-ID (PH2.5): without this entry the browser receives the header but
# JavaScript cannot read it — the CORS spec hides every response header from
# script unless it is explicitly exposed. That would defeat the point of the ID:
# the frontend could not surface it in an error toast, and a user reporting a
# failure would have nothing to quote. Exposing it leaks nothing — it is an
# opaque value this server generated for this request.
EXPOSE_HEADERS: List[str] = ["X-Request-ID"]

# How long (seconds) a browser may cache a preflight response.
PREFLIGHT_MAX_AGE = 600


# --------------------------------------------------------------------------- #
# Origin allowlist resolution                                                   #
# --------------------------------------------------------------------------- #
def _split_origins(raw: str) -> List[str]:
    """Parse a comma-separated origin string into normalized origins.

    Normalization: trim whitespace, drop a trailing slash (``Origin`` headers
    never carry one, so ``https://app.example.com/`` would otherwise never
    match), and drop empties. A literal ``*`` is discarded here so a wildcard
    can never reach the allowlist regardless of which variable supplied it.
    """
    origins: List[str] = []
    for part in raw.split(","):
        origin = part.strip().rstrip("/")
        if not origin:
            continue
        if origin == "*":
            logger.warning(
                "Ignoring wildcard '*' in CORS origin configuration: a wildcard "
                "origin is unsafe and specification-invalid with credentials."
            )
            continue
        origins.append(origin)
    return origins


def allowed_origins() -> List[str]:
    """Resolve the exact-match origin allowlist from the environment.

    Order of precedence for inputs (all are merged, de-duplicated, order
    preserved): ``CORS_ALLOWED_ORIGINS`` (canonical), then legacy
    ``CORS_ORIGINS`` and ``FRONTEND_URL``. Outside production, if none of those
    yield an origin, the local development defaults are assumed. In production
    nothing is assumed — an empty result means every cross-origin request is
    rejected (fail closed).
    """
    merged: List[str] = []
    merged.extend(_split_origins(os.environ.get(CORS_ALLOWED_ORIGINS_ENV, "")))
    merged.extend(_split_origins(os.environ.get(LEGACY_CORS_ORIGINS_ENV, "")))
    merged.extend(_split_origins(os.environ.get(LEGACY_FRONTEND_URL_ENV, "")))

    if not merged and not is_production():
        merged.extend(DEFAULT_DEV_ORIGINS)

    # De-duplicate while preserving first-seen order.
    seen = set()
    unique: List[str] = []
    for origin in merged:
        if origin not in seen:
            seen.add(origin)
            unique.append(origin)
    return unique


# --------------------------------------------------------------------------- #
# Middleware assembly                                                           #
# --------------------------------------------------------------------------- #
def cors_kwargs() -> dict:
    """The exact keyword arguments for Starlette's ``CORSMiddleware``.

    Kept as a discrete function so both ``apply_cors`` and the test-suite assert
    against one authoritative shape. ``allow_origins`` is always a concrete
    list — never a wildcard — so ``allow_credentials=True`` is safe by
    construction.
    """
    return {
        "allow_origins": allowed_origins(),
        "allow_credentials": True,
        "allow_methods": ALLOWED_METHODS,
        "allow_headers": ALLOWED_HEADERS,
        "expose_headers": EXPOSE_HEADERS,
        "max_age": PREFLIGHT_MAX_AGE,
    }


def apply_cors(app) -> None:
    """Attach the hardened CORS middleware to a FastAPI/Starlette app.

    The one place CORS is wired into the application. Resolves the allowlist at
    attach time (i.e. from the environment present when the app is constructed),
    logs the effective posture, and warns loudly if a production deployment has
    an empty allowlist so a misconfiguration is visible rather than silent.
    """
    kwargs = cors_kwargs()
    origins = kwargs["allow_origins"]
    if not origins:
        if is_production():
            logger.warning(
                "CORS allowlist is EMPTY in production — every cross-origin "
                "browser request will be rejected. Set %s to your frontend "
                "origin(s).",
                CORS_ALLOWED_ORIGINS_ENV,
            )
        else:
            logger.info("CORS allowlist is empty (development).")
    else:
        logger.info("CORS allowlist: %s", ", ".join(origins))
    app.add_middleware(CORSMiddleware, **kwargs)

"""Centralized HTTP response security headers (PH1.4b).

Single source of truth for the security-relevant headers StockAssist AI adds to
**every** HTTP response. No security header may be set anywhere else — that is
what guarantees a consistent, production-safe posture (anti-clickjacking,
anti-MIME-sniffing, transport security, a real Content-Security-Policy, and the
cross-origin isolation family) and prevents the per-route header drift that a
scattered ``response.headers[...] = ...`` approach inevitably produces.

Why this exists (see PRODUCTION_HARDENING.md / PRODUCTION_ROADMAP.md PH1.4b):

Before this sprint the application emitted **no** security response headers at
all. A browser talking to the API therefore had none of the standard
protections a security scanner (and a real attacker) checks for: the responses
could be framed, MIME-sniffed, and leaked referrers, and there was no transport
pinning or content policy. This module closes that gap in one centralized,
environment-driven place.

Design decisions:

* **One centralized ASGI middleware** — ``SecurityHeadersMiddleware`` sets the
  headers on the outgoing ``http.response.start`` message. A pure-ASGI
  middleware (rather than ``BaseHTTPMiddleware``) is used deliberately: it does
  not buffer the response body, so it is safe for streaming/SSE responses and
  background tasks, and it touches only the ``http`` scope (WebSocket upgrades
  pass straight through untouched).

* **Enforce, don't suggest** — our values overwrite any header an inner handler
  may have set, so the security posture cannot be silently weakened downstream.

* **No unsafe defaults** — the default Content-Security-Policy is the strict,
  API-appropriate ``default-src 'none'`` lockdown. There is no ``unsafe-inline``
  and no ``unsafe-eval`` anywhere. Everything is overridable by environment
  variable so a deployment can relax a single directive without touching code,
  but the *shipped* default is the safe one.

* **HSTS only over HTTPS / production** — ``Strict-Transport-Security`` is a
  browser commitment that can lock users out of a site reachable only over
  plain HTTP, so it is emitted **only** when the request is actually secure
  (HTTPS, honoring ``X-Forwarded-Proto`` behind a TLS-terminating proxy) or when
  running in production (which is HTTPS by policy — mirroring how
  ``security.cookies`` forces ``Secure`` in production). It is never sent on a
  plain-HTTP development request.

* **Cross-origin isolation** — ``Cross-Origin-Opener-Policy: same-origin`` and
  ``Cross-Origin-Resource-Policy: same-origin`` are safe and on by default: CORP
  only blocks *no-cors* cross-origin loads, so the credentialed **CORS** frontend
  (which uses ``mode: cors``, governed by ``security.cors``) is unaffected while
  the API can no longer be embedded as an opaque subresource.
  ``Cross-Origin-Embedder-Policy: require-corp`` is implemented but **off by
  default** — it provides no protection to the API's own JSON bytes yet would
  break same-origin HTML tooling (e.g. Swagger UI) that pulls cross-origin
  subresources lacking CORP. Deployments that want full cross-origin isolation
  opt in via ``CROSS_ORIGIN_EMBEDDER_POLICY``.

* **Future nonce-based CSP** — the policy is nonce-capable today: if the
  configured policy contains a literal ``{nonce}`` placeholder, the middleware
  generates a fresh per-request nonce, substitutes it into the header, and
  exposes it on ``request.state.csp_nonce`` so an HTML-rendering handler can
  stamp the same nonce onto its ``<script nonce=...>`` tags. The default JSON
  API policy uses no nonce, so this costs nothing until it is needed.

* **Deprecated headers** — the legacy, buggy ``X-XSS-Protection`` auditor is
  explicitly neutralized (``0``) rather than left to a browser default; it has
  itself been a source of vulnerabilities and is superseded by CSP.

This module owns its own environment parsing and reuses
``security.cookies.is_production`` so the production-detection primitive never
drifts between the cookie, CORS, and header policies.
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Dict, List, Optional, Tuple

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Reuse the single production-detection primitive so environment semantics never
# drift between the cookie policy, the CORS policy, and this header policy.
from security.cookies import is_production

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Environment-variable names — one place, no magic strings at call sites.       #
# --------------------------------------------------------------------------- #
HSTS_ENABLE_ENV = "HSTS_ENABLE"
HSTS_MAX_AGE_ENV = "HSTS_MAX_AGE"
HSTS_INCLUDE_SUBDOMAINS_ENV = "HSTS_INCLUDE_SUBDOMAINS"
HSTS_PRELOAD_ENV = "HSTS_PRELOAD"

CSP_ENV = "CONTENT_SECURITY_POLICY"
PERMISSIONS_POLICY_ENV = "PERMISSIONS_POLICY"
REFERRER_POLICY_ENV = "REFERRER_POLICY"
X_FRAME_OPTIONS_ENV = "X_FRAME_OPTIONS"
COOP_ENV = "CROSS_ORIGIN_OPENER_POLICY"
CORP_ENV = "CROSS_ORIGIN_RESOURCE_POLICY"
COEP_ENV = "CROSS_ORIGIN_EMBEDDER_POLICY"


# --------------------------------------------------------------------------- #
# Policy defaults — production-safe, no unsafe values anywhere.                  #
# --------------------------------------------------------------------------- #
# HSTS: two years, include subdomains, do NOT auto-commit to the browser preload
# list (preload is a hard-to-reverse public commitment; opt in per deployment).
DEFAULT_HSTS_MAX_AGE = 63072000  # 2 years, in seconds
DEFAULT_HSTS_INCLUDE_SUBDOMAINS = True
DEFAULT_HSTS_PRELOAD = False

# Content-Security-Policy default directives for a JSON API: deny everything,
# forbid this response from being framed, pin the document base, and forbid form
# submissions. Assembled in insertion order. No 'unsafe-*' values, ever.
DEFAULT_CSP_DIRECTIVES: Dict[str, str] = {
    "default-src": "'none'",
    "base-uri": "'none'",
    "form-action": "'none'",
    "frame-ancestors": "'none'",
}

# Anti-clickjacking for legacy browsers that predate CSP frame-ancestors. Kept
# alongside (not instead of) the CSP directive as defense in depth.
DEFAULT_X_FRAME_OPTIONS = "DENY"

# Leak only the origin (never the path/query) on cross-origin navigations, and
# nothing at all on an HTTPS→HTTP downgrade. The modern, balanced default.
DEFAULT_REFERRER_POLICY = "strict-origin-when-cross-origin"

# Disable the powerful browser features the API never legitimately needs. An
# empty allowlist "()" means "no origin, including self". Widely supported set.
DEFAULT_PERMISSIONS_POLICY = ", ".join(
    f"{feature}=()"
    for feature in (
        "accelerometer",
        "autoplay",
        "camera",
        "display-capture",
        "encrypted-media",
        "fullscreen",
        "geolocation",
        "gyroscope",
        "magnetometer",
        "microphone",
        "midi",
        "payment",
        "picture-in-picture",
        "usb",
        "screen-wake-lock",
        "xr-spatial-tracking",
    )
)

# Cross-origin isolation family. COOP/CORP are safe on by default (see module
# docstring); COEP is opt-in (unset ⇒ header omitted).
DEFAULT_COOP = "same-origin"
DEFAULT_CORP = "same-origin"

# The deprecated legacy XSS auditor: explicitly turned OFF (its heuristics have
# themselves introduced vulnerabilities; CSP is the modern replacement).
X_XSS_PROTECTION_VALUE = "0"


# --------------------------------------------------------------------------- #
# Small environment helpers                                                     #
# --------------------------------------------------------------------------- #
def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


# --------------------------------------------------------------------------- #
# Strict-Transport-Security                                                      #
# --------------------------------------------------------------------------- #
def hsts_enabled() -> bool:
    """Whether HSTS may be emitted at all.

    Defaults to ``True`` in production and ``False`` elsewhere, so a plain-HTTP
    development environment never pins itself to HTTPS. A staging deployment that
    already serves HTTPS can force it on with ``HSTS_ENABLE=true``.
    """
    return _env_flag(HSTS_ENABLE_ENV, is_production())


def hsts_max_age() -> int:
    """``max-age`` in seconds; non-integer configuration falls back to the default."""
    raw = os.environ.get(HSTS_MAX_AGE_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_HSTS_MAX_AGE
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d.", HSTS_MAX_AGE_ENV, raw, DEFAULT_HSTS_MAX_AGE)
        return DEFAULT_HSTS_MAX_AGE
    return value if value >= 0 else DEFAULT_HSTS_MAX_AGE


def strict_transport_security_value() -> str:
    """Assemble the ``Strict-Transport-Security`` header value."""
    parts = [f"max-age={hsts_max_age()}"]
    if _env_flag(HSTS_INCLUDE_SUBDOMAINS_ENV, DEFAULT_HSTS_INCLUDE_SUBDOMAINS):
        parts.append("includeSubDomains")
    if _env_flag(HSTS_PRELOAD_ENV, DEFAULT_HSTS_PRELOAD):
        parts.append("preload")
    return "; ".join(parts)


def _request_is_https(scope: Scope) -> bool:
    """True when the request reached us over TLS.

    Checks the ASGI ``scheme`` first, then ``X-Forwarded-Proto`` so a deployment
    behind a TLS-terminating proxy (where the app itself speaks plain HTTP) is
    still recognized as secure.
    """
    if scope.get("scheme") == "https":
        return True
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-proto":
            return value.decode("latin-1").split(",")[0].strip().lower() == "https"
    return False


def should_send_hsts(scope: Scope) -> bool:
    """HSTS is sent only when enabled AND the connection is actually secure.

    Production counts as secure (HTTPS by policy, matching how cookies force
    ``Secure`` in production); outside production the request must genuinely be
    HTTPS. This is what keeps a plain-HTTP dev origin from pinning itself.
    """
    if not hsts_enabled():
        return False
    return is_production() or _request_is_https(scope)


# --------------------------------------------------------------------------- #
# Content-Security-Policy (nonce-capable)                                        #
# --------------------------------------------------------------------------- #
def _default_csp() -> str:
    """The strict API CSP assembled from :data:`DEFAULT_CSP_DIRECTIVES`."""
    return "; ".join(f"{directive} {value}" for directive, value in DEFAULT_CSP_DIRECTIVES.items())


def csp_template() -> str:
    """The configured CSP *template* (may contain a ``{nonce}`` placeholder).

    ``CONTENT_SECURITY_POLICY`` overrides the built-in default verbatim, so a
    deployment can serve HTML (e.g. relax for Swagger UI or a nonce-based policy)
    without a code change.
    """
    return _env_str(CSP_ENV, _default_csp())


def csp_uses_nonce(template: Optional[str] = None) -> bool:
    """Whether the active CSP template requests a per-request nonce."""
    tmpl = csp_template() if template is None else template
    return "{nonce}" in tmpl


def content_security_policy(nonce: Optional[str] = None) -> str:
    """Resolve the CSP header value, substituting ``nonce`` if the template uses it."""
    template = csp_template()
    if "{nonce}" in template:
        return template.replace("{nonce}", nonce or "")
    return template


def generate_nonce() -> str:
    """A cryptographically-strong, base64url CSP nonce."""
    return secrets.token_urlsafe(16)


# --------------------------------------------------------------------------- #
# Cross-origin isolation family                                                 #
# --------------------------------------------------------------------------- #
def coep_value() -> Optional[str]:
    """The ``Cross-Origin-Embedder-Policy`` value, or ``None`` when disabled.

    COEP is opt-in: the header is emitted only when ``CROSS_ORIGIN_EMBEDDER_POLICY``
    is explicitly set (typically to ``require-corp``). Left unset it is omitted,
    which is the safe default for a JSON API (see module docstring).
    """
    raw = os.environ.get(COEP_ENV)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


# --------------------------------------------------------------------------- #
# Header assembly                                                               #
# --------------------------------------------------------------------------- #
def static_security_headers() -> Dict[str, str]:
    """The security headers that do not depend on the request.

    Resolved fresh from the environment on each call (cheap) so the test-suite
    and the middleware assert against one authoritative shape. HSTS and the CSP
    nonce are handled separately because they are request-dependent.
    """
    headers: Dict[str, str] = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": _env_str(X_FRAME_OPTIONS_ENV, DEFAULT_X_FRAME_OPTIONS),
        "Referrer-Policy": _env_str(REFERRER_POLICY_ENV, DEFAULT_REFERRER_POLICY),
        "Permissions-Policy": _env_str(PERMISSIONS_POLICY_ENV, DEFAULT_PERMISSIONS_POLICY),
        "Cross-Origin-Opener-Policy": _env_str(COOP_ENV, DEFAULT_COOP),
        "Cross-Origin-Resource-Policy": _env_str(CORP_ENV, DEFAULT_CORP),
        "X-XSS-Protection": X_XSS_PROTECTION_VALUE,
    }
    coep = coep_value()
    if coep is not None:
        headers["Cross-Origin-Embedder-Policy"] = coep
    return headers


def resolve_headers(scope: Scope) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    """Compute the full header set for one request.

    Returns ``(headers, nonce)`` where ``nonce`` is the generated CSP nonce (or
    ``None`` when the policy uses none), so the caller can stash it on the
    request state for a downstream HTML handler.
    """
    headers: List[Tuple[str, str]] = list(static_security_headers().items())

    template = csp_template()
    nonce: Optional[str] = None
    if "{nonce}" in template:
        nonce = generate_nonce()
    headers.append(("Content-Security-Policy", content_security_policy(nonce)))

    if should_send_hsts(scope):
        headers.append(("Strict-Transport-Security", strict_transport_security_value()))

    return headers, nonce


# --------------------------------------------------------------------------- #
# Middleware                                                                     #
# --------------------------------------------------------------------------- #
class SecurityHeadersMiddleware:
    """Pure-ASGI middleware that stamps the centralized security headers.

    Only ``http`` responses are touched (WebSocket upgrades pass through). Header
    values are *enforced* — they overwrite any value an inner handler set — so the
    posture cannot be weakened downstream. The response body is never buffered,
    keeping the middleware safe for streaming responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        header_pairs, nonce = resolve_headers(scope)

        # Expose the nonce to downstream handlers via request.state.csp_nonce so a
        # future HTML response can stamp matching <script nonce=...> tags.
        if nonce is not None:
            scope.setdefault("state", {})["csp_nonce"] = nonce

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for key, value in header_pairs:
                    response_headers[key] = value  # overwrite: enforce our value
            await send(message)

        await self.app(scope, receive, send_wrapper)


def apply_security_headers(app) -> None:
    """Attach the centralized security-header middleware to a FastAPI/Starlette app.

    The one place security response headers are wired into the application. Added
    after (and therefore *outside*) the CORS middleware so that even
    CORS-generated responses — preflight replies and rejected-origin errors —
    carry the security headers.
    """
    app.add_middleware(SecurityHeadersMiddleware)
    logger.info(
        "Security headers enabled (HSTS %s, CSP nonce %s, COEP %s).",
        "on" if hsts_enabled() else "off",
        "on" if csp_uses_nonce() else "off",
        coep_value() or "off",
    )

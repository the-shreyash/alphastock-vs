"""Centralized authentication-cookie policy (PH1.3).

Single source of truth for how StockAssist AI issues and clears **every**
authentication-related cookie. No auth cookie may be set or deleted outside
this module — that is what guarantees a consistent security posture
(``Secure`` / ``HttpOnly`` / ``SameSite`` / ``Path`` / ``Max-Age``) across
login, registration, refresh, logout, and the Google OAuth flow, and removes
the duplicated ``set_cookie`` literals that previously drifted apart across
call sites.

Design decisions (see PRODUCTION_HARDENING.md / PRODUCTION_ROADMAP.md PH1.3):

* **HttpOnly** — always ``True``. JavaScript never needs to read these cookies;
  the app talks to the API with ``withCredentials`` and the browser attaches
  them automatically. HttpOnly keeps them out of reach of XSS.
* **Secure** — driven by ``COOKIE_SECURE`` and **forced ``True`` in
  production** (``APP_ENV=production``). Local development defaults to ``False``
  so the cookies work over plain-HTTP ``http://localhost`` without a cert. A
  production deployment can never accidentally ship insecure cookies: the
  environment override is ignored when ``APP_ENV=production`` (closes R-04).
* **SameSite** — defaults to ``Lax`` (a solid CSRF baseline: cookies are not
  sent on cross-site sub-requests, only on top-level navigations). Configurable
  via ``COOKIE_SAMESITE`` for deployments that split the frontend and API onto
  genuinely cross-site domains (``None`` — which the browser only honors
  alongside ``Secure``). The short-lived OAuth-state cookie is never ``Strict``
  because it must survive the top-level redirect back from Google.
* **Path** — access and refresh tokens use ``/`` so a single ``logout`` (and a
  single browser) reliably clears them and no duplicate-name cookies can accrue
  at different paths (a real footgun that silently breaks refresh). The OAuth
  ``state`` cookie is scoped to ``/api/auth`` — it is only ever read there, is
  single-use, and is burned immediately after the exchange.
* **Domain** — optional ``COOKIE_DOMAIN`` (e.g. ``.stockassist.ai``) to share
  the session across ``app.`` and ``api.`` subdomains. Unset by default (host-
  only cookie), which is the safest choice for single-host and localhost.
* **Clearing** — deletion mirrors the exact ``key`` + ``path`` + ``domain`` +
  security attributes used when setting, otherwise the browser keeps the old
  cookie. ``clear_auth_cookies`` is the one true logout primitive.

Session-fixation note: login, registration and OAuth all mint fresh tokens and
overwrite the cookies in place (same name + path), so a pre-authentication
cookie value can never be promoted to an authenticated session.
"""
from __future__ import annotations

import os
from typing import Literal, Optional, Tuple

from fastapi import Response

# --------------------------------------------------------------------------- #
# Cookie identity + lifetime constants (one place, no magic numbers at call    #
# sites). Max-Age values mirror the corresponding JWT lifetimes in server.py;  #
# JWT lifetime tuning itself is PH1.6 and out of scope here.                    #
# --------------------------------------------------------------------------- #
ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"
OAUTH_STATE_COOKIE = "g_oauth_state"
# Broker OAuth `state` double-submit cookie (D6.1 / S1). A separate name and a
# separate path from the Google one because they guard different flows and must
# never be interchangeable: a state minted for Google sign-in being accepted by
# the broker callback would reintroduce exactly the cross-flow confusion the
# namespaced server-side record exists to prevent.
BROKER_OAUTH_STATE_COOKIE = "b_oauth_state"

ACCESS_TOKEN_MAX_AGE = 86400        # 24h — matches create_access_token() exp
REFRESH_TOKEN_MAX_AGE = 604800      # 7d  — matches create_refresh_token() exp
OAUTH_STATE_MAX_AGE = 600           # 10m — single-use CSRF state, short-lived
BROKER_OAUTH_STATE_MAX_AGE = 600    # 10m — mirrors the server-side record's TTL

AUTH_COOKIE_PATH = "/"              # access/refresh: readable app-wide, cleared in one shot
OAUTH_STATE_PATH = "/api/auth"      # state: only ever read on the auth routes
BROKER_OAUTH_STATE_PATH = "/api/brokers"  # broker state: only read on the broker routes

SameSite = Literal["lax", "strict", "none"]


# --------------------------------------------------------------------------- #
# Environment-driven policy resolution                                          #
# --------------------------------------------------------------------------- #
def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_production() -> bool:
    """True when running under a production environment."""
    return os.environ.get("APP_ENV", "development").strip().lower() == "production"


def cookie_secure() -> bool:
    """Whether to set the ``Secure`` flag.

    Forced ``True`` in production regardless of any environment override, so an
    insecure cookie can never ship to production. Outside production it follows
    ``COOKIE_SECURE`` (default ``False`` for plain-HTTP local development).
    """
    if is_production():
        return True
    return _env_flag("COOKIE_SECURE", False)


def cookie_samesite() -> SameSite:
    """Configured ``SameSite`` policy, defaulting to the safe ``lax``.

    Unknown values fall back to ``lax`` rather than emitting an invalid
    attribute the browser would reject.
    """
    val = os.environ.get("COOKIE_SAMESITE", "lax").strip().lower()
    if val not in ("lax", "strict", "none"):
        return "lax"
    return val  # type: ignore[return-value]


def cookie_domain() -> Optional[str]:
    """Optional cookie ``Domain``; ``None`` (host-only) when unset."""
    domain = os.environ.get("COOKIE_DOMAIN", "").strip()
    return domain or None


def _resolved_flags() -> Tuple[bool, SameSite]:
    """Resolve (secure, samesite) as a consistent pair.

    Browsers **ignore** a ``SameSite=None`` cookie that is not also ``Secure``,
    which would silently drop the session. When someone asks for ``None`` in an
    insecure (non-production) context, degrade to ``Lax`` so the cookie is still
    accepted. In production ``secure`` is always ``True``, so ``None`` is honored.
    """
    secure = cookie_secure()
    samesite = cookie_samesite()
    if samesite == "none" and not secure:
        samesite = "lax"
    return secure, samesite


# --------------------------------------------------------------------------- #
# Setters                                                                       #
# --------------------------------------------------------------------------- #
def set_access_cookie(response: Response, access_token: str) -> None:
    """Set the access-token cookie with the hardened, centrally-resolved flags."""
    secure, samesite = _resolved_flags()
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=ACCESS_TOKEN_MAX_AGE,
        path=AUTH_COOKIE_PATH,
        domain=cookie_domain(),
    )


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Set the refresh-token cookie with the hardened, centrally-resolved flags."""
    secure, samesite = _resolved_flags()
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=REFRESH_TOKEN_MAX_AGE,
        path=AUTH_COOKIE_PATH,
        domain=cookie_domain(),
    )


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Set both session cookies. Used by login, registration and OAuth — each of
    which mints fresh tokens, so this always overwrites any prior session value
    in place (session-fixation guard)."""
    set_access_cookie(response, access_token)
    set_refresh_cookie(response, refresh_token)


def set_oauth_state_cookie(response: Response, state: str) -> None:
    """Plant the short-lived OAuth CSRF ``state`` cookie.

    Scoped to ``/api/auth`` and never ``Strict`` — the cookie must be present
    when Google performs the top-level redirect back to the app, and ``Strict``
    would withhold it on that cross-site navigation.
    """
    secure, samesite = _resolved_flags()
    if samesite == "strict":
        samesite = "lax"
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=OAUTH_STATE_MAX_AGE,
        path=OAUTH_STATE_PATH,
        domain=cookie_domain(),
    )


def set_broker_oauth_state_cookie(response: Response, state: str) -> None:
    """Plant the short-lived broker-OAuth ``state`` cookie (D6.1 / S1).

    Scoped to ``/api/brokers`` (the only routes that read it) and never
    ``Strict``, for the same reason the Google state cookie is not: the broker
    performs a **top-level redirect** back to
    ``GET /api/brokers/{broker}/callback``, and ``Strict`` would withhold the
    cookie on exactly that navigation — turning a correct callback into a
    rejected one.

    This cookie is planted on the response to ``GET /api/brokers/{broker}/login-url``,
    which the SPA fetches with credentials. Without ``withCredentials`` the
    browser discards a cross-origin ``Set-Cookie`` silently, so this control and
    the frontend cookie fix (D6-L1) are one change, not two.
    """
    secure, samesite = _resolved_flags()
    if samesite == "strict":
        samesite = "lax"
    response.set_cookie(
        key=BROKER_OAUTH_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=BROKER_OAUTH_STATE_MAX_AGE,
        path=BROKER_OAUTH_STATE_PATH,
        domain=cookie_domain(),
    )


# --------------------------------------------------------------------------- #
# Clearers — mirror the set attributes so the browser actually deletes them     #
# --------------------------------------------------------------------------- #
def clear_auth_cookies(response: Response) -> None:
    """Remove both session cookies. The single logout primitive — matches the
    key + path + domain + security attributes used when setting so the browser
    reliably drops them."""
    secure, samesite = _resolved_flags()
    for key in (ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE):
        response.delete_cookie(
            key=key,
            path=AUTH_COOKIE_PATH,
            domain=cookie_domain(),
            secure=secure,
            httponly=True,
            samesite=samesite,
        )


def clear_broker_oauth_state_cookie(response: Response) -> None:
    """Burn the broker ``state`` cookie after the callback (matches its scope).

    Called on **every** exit path of the callback — success, rejection and
    error alike. A state cookie that outlives its flow is a state cookie that
    can be paired with a second attempt.
    """
    secure, samesite = _resolved_flags()
    if samesite == "strict":
        samesite = "lax"
    response.delete_cookie(
        key=BROKER_OAUTH_STATE_COOKIE,
        path=BROKER_OAUTH_STATE_PATH,
        domain=cookie_domain(),
        secure=secure,
        httponly=True,
        samesite=samesite,
    )


def clear_oauth_state_cookie(response: Response) -> None:
    """Burn the OAuth ``state`` cookie after the exchange (matches its scope)."""
    secure, samesite = _resolved_flags()
    if samesite == "strict":
        samesite = "lax"
    response.delete_cookie(
        key=OAUTH_STATE_COOKIE,
        path=OAUTH_STATE_PATH,
        domain=cookie_domain(),
        secure=secure,
        httponly=True,
        samesite=samesite,
    )

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

#: Fallback only. The real values are derived from the JWT lifetimes at set
#: time (see ``access_cookie_max_age`` / ``refresh_cookie_max_age``); these
#: constants remain for the deployments and tests that reference them by name.
ACCESS_TOKEN_MAX_AGE = 900          # 15m — mirrors jwt.DEFAULT_ACCESS_TTL_SECONDS
REFRESH_TOKEN_MAX_AGE = 604800      # 7d  — mirrors jwt.DEFAULT_REFRESH_TTL_SECONDS
OAUTH_STATE_MAX_AGE = 600           # 10m — single-use CSRF state, short-lived
BROKER_OAUTH_STATE_MAX_AGE = 600    # 10m — mirrors the server-side record's TTL

AUTH_COOKIE_PATH = "/"              # access/refresh: readable app-wide, cleared in one shot
OAUTH_STATE_PATH = "/api/auth"      # state: only ever read on the auth routes
BROKER_OAUTH_STATE_PATH = "/api/brokers"  # broker state: only read on the broker routes

SameSite = Literal["lax", "strict", "none"]


# --------------------------------------------------------------------------- #
# Lifetimes — derived from the token they carry, never guessed                  #
# --------------------------------------------------------------------------- #
def access_cookie_max_age() -> int:
    """Browser lifetime of the access cookie: exactly the access token's own.

    D6.2 / D. This was a hardcoded 86400 with a comment claiming it "matches
    create_access_token() exp" — which it had not since PH1.6 set the access TTL
    to 15 minutes. The browser therefore kept, and re-sent, a **provably dead
    credential for another 23¾ hours**, including across restarts. Nothing
    accepted it (the JWT's own ``exp`` is what authenticates), so the cost was
    not authentication — it was a long-lived bearer credential persisted on disk
    for no reason, and a comment that would have misled the next person to read
    it. Deriving the value means the two can never drift again.
    """
    from security.jwt import access_ttl_seconds  # local: avoids an import cycle
    return access_ttl_seconds()


def refresh_cookie_max_age() -> int:
    """Browser lifetime of the refresh cookie: exactly the refresh token's own."""
    from security.jwt import refresh_ttl_seconds  # local: avoids an import cycle
    return refresh_ttl_seconds()


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
        max_age=access_cookie_max_age(),
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
        max_age=refresh_cookie_max_age(),
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


# --------------------------------------------------------------------------- #
# Deployment-topology coherence (D6.2 / D)                                      #
# --------------------------------------------------------------------------- #
#: Optional public origin of THIS API (scheme+host[+port], no path), e.g.
#: ``https://api.example.com``. Purely declarative: nothing reads it at request
#: time. It exists so the checks below can be *decided* rather than guessed —
#: without it the module knows every browser origin that may talk to the API but
#: not the origin the API itself answers on, which is exactly the comparison
#: that determines whether a host-only cookie can work at all.
API_PUBLIC_ORIGIN_ENV = "API_PUBLIC_ORIGIN"


def _origin_host(origin: str) -> str:
    """Host of an origin string, lowercased, port stripped. ``""`` if unparseable."""
    from urllib.parse import urlsplit
    try:
        host = urlsplit(origin.strip()).hostname or ""
    except ValueError:
        return ""
    return host.lower()


def _domain_covers(domain: str, host: str) -> bool:
    """Whether a cookie ``Domain`` attribute would be sent to ``host``.

    Browser rule, verbatim: a leading dot is ignored, and the cookie is sent to
    the domain itself and to any subdomain of it. It is NOT sent to a sibling or
    a parent."""
    base = domain.lstrip(".").lower()
    if not base or not host:
        return False
    return host == base or host.endswith("." + base)


def cookie_policy_warnings() -> list:
    """Incoherences between the cookie policy and the deployment topology.

    D6.2 / D. Every piece of this configuration is individually defensible and
    the *combination* is what decides whether a browser session works at all —
    which is a property nobody can check by reading one file. The failure it is
    written for is silent and total: the API keeps answering, the CORS
    allowlist keeps matching, and every cookie-authenticated mutation 403s
    because the SPA cannot read a ``csrf_token`` that the browser filed under a
    host the page is not on. That is not a condition to discover from a user
    report.

    Each returned string names a concrete, decidable problem. An empty list is
    not proof of a correct deployment — where ``API_PUBLIC_ORIGIN`` is unset the
    sharpest check cannot run, and the last entry says so rather than implying
    a clean bill of health.
    """
    from security.cors import allowed_origins  # local: cors imports this module

    warnings: list = []
    origins = allowed_origins()
    frontend_hosts = {h for h in (_origin_host(o) for o in origins) if h}
    domain = cookie_domain()
    samesite = cookie_samesite()
    api_host = _origin_host(os.environ.get(API_PUBLIC_ORIGIN_ENV, ""))

    # 1. A Domain that does not cover a browser origin we accept: those cookies
    #    are simply never sent to (or readable by) that origin.
    if domain:
        uncovered = sorted(h for h in frontend_hosts if not _domain_covers(domain, h))
        if uncovered:
            warnings.append(
                f"COOKIE_DOMAIN={domain!r} does not cover allowed origin host(s) "
                f"{', '.join(uncovered)} — the browser will never send the session "
                f"cookies to them, so those origins cannot authenticate."
            )
        if api_host and not _domain_covers(domain, api_host):
            warnings.append(
                f"COOKIE_DOMAIN={domain!r} does not cover this API's own host "
                f"{api_host!r} — the browser will reject the Set-Cookie outright."
            )

    # 2. Host-only cookies with the SPA on a different host than the API. The
    #    auth cookies would not be sent, and the (deliberately readable) CSRF
    #    cookie would be invisible to the page that has to echo it.
    if not domain:
        if api_host:
            offsite = sorted(h for h in frontend_hosts if h != api_host)
            if offsite:
                warnings.append(
                    f"COOKIE_DOMAIN is unset, so cookies are host-only on {api_host!r}, "
                    f"but allowed origin host(s) {', '.join(offsite)} differ — the SPA "
                    f"cannot read csrf_token and every cookie-authenticated mutation "
                    f"will 403. Set COOKIE_DOMAIN to a parent domain covering both."
                )
        elif len(frontend_hosts) > 1:
            warnings.append(
                f"COOKIE_DOMAIN is unset (host-only cookies) but {len(frontend_hosts)} "
                f"distinct allowed origin hosts are configured "
                f"({', '.join(sorted(frontend_hosts))}) — at most one of them can be "
                f"this API's host, so the rest cannot authenticate."
            )

    # 3. SameSite=None requested but silently degraded to Lax (see _resolved_flags).
    if samesite == "none" and not cookie_secure():
        warnings.append(
            "COOKIE_SAMESITE=none was requested without Secure cookies, so it has "
            "been degraded to 'lax' (browsers ignore a non-Secure SameSite=None "
            "cookie). A genuinely cross-site frontend will not keep a session."
        )

    # 4. A cross-site split needs SameSite=None, and no amount of Domain fixes it.
    if api_host and samesite in ("lax", "strict"):
        cross_site = sorted(
            h for h in frontend_hosts
            if h != api_host and not (domain and _domain_covers(domain, h)
                                      and _domain_covers(domain, api_host))
        )
        if cross_site:
            warnings.append(
                f"SameSite={samesite!r} with the SPA on {', '.join(cross_site)} and the "
                f"API on {api_host!r} — if those are different registrable domains the "
                f"browser withholds the cookies on every XHR. Use COOKIE_SAMESITE=none "
                f"(with Secure) for a genuinely cross-site split."
            )

    if not api_host:
        warnings.append(
            f"{API_PUBLIC_ORIGIN_ENV} is not set, so the cookie/CORS topology could "
            f"only be partially checked. Set it to this API's public origin "
            f"(e.g. https://api.example.com) to enable the full check."
        )
    return warnings

"""Centralized JWT issuance and verification (PH1.6).

Single source of truth for how StockAssist AI mints and validates JSON Web
Tokens. No JWT may be encoded or decoded outside this module — that is what
guarantees one consistent claim set, one algorithm, and one validation policy
across login, registration, refresh, logout, OAuth, and every protected route,
instead of per-call-site ``pyjwt.encode``/``decode`` literals that drift apart.

This module is deliberately **pure and framework-agnostic** (no FastAPI, no
database): it only turns identities into signed tokens and signed tokens back
into validated claims. The stateful half of the session — refresh-token
families, rotation, reuse detection, revocation — lives in
``security.sessions`` (the ``SessionStore``), which this module knows nothing
about. Callers (``server.py``) compose the two.

Design decisions (see SECURITY_ARCHITECTURE.md §9/§11/§12 and
PRODUCTION_HARDENING.md finding H11 / risk R-06, PRODUCTION_ROADMAP.md PH1.6):

* **Short access, rotating refresh.** Access tokens default to 15 minutes and
  refresh tokens to 7 days (both env-configurable). A stolen access token is
  useful for minutes, not a day; the refresh token is rotated on every use by
  the ``SessionStore`` so a captured refresh token is single-use.
* **Full registered-claim set.** Every token now carries ``iat`` (issued-at,
  the anchor for ``password_changed_at`` invalidation), ``jti`` (unique id —
  the handle the session store rotates and revokes), ``aud`` (audience) and
  ``iss`` (issuer) so a token minted for another StockAssist service — or a
  raw HS256 token from an unrelated system that happens to share the secret —
  is rejected, plus ``sid`` (the owning session/family) and ``ver`` (token
  schema version).
* **Token versioning is a global kill-switch.** ``TOKEN_VERSION`` is pinned in
  code (like ``BCRYPT_ROUNDS`` in ``security.passwords``); bumping it in a
  reviewed diff invalidates every token in circulation at once — the emergency
  lever for a secret compromise or a claim-shape change.
* **Validation is strict and fail-closed.** ``decode_token`` requires all of
  ``exp/iat/aud/iss/sub/jti/type/ver/sid`` to be present and correct. A token
  missing any of them (e.g. a pre-PH1.6 token, or a downgraded one) is
  rejected. This is a deliberate clean cutover: existing sessions re-login once
  after deploy (see the migration note in SECURITY_ARCHITECTURE.md §12).
* **Typed, framework-neutral errors.** Verification raises ``TokenExpired`` or
  ``TokenInvalid`` (never a raw ``pyjwt`` type and never an ``HTTPException``);
  the web layer maps both to a generic 401 without leaking which check failed.
* **No secret default.** The signing secret is read from ``JWT_SECRET`` with no
  fallback — a missing secret is a hard configuration error, never a silent
  weak default.
* **Tokens are never logged.** Nothing here logs a token or its signature; the
  ``jti`` (an opaque id, not the credential) is the only token-derived value
  that leaves this module.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Optional, Union

import jwt as pyjwt

# --------------------------------------------------------------------------- #
# Policy constants (one place, no magic numbers at call sites).                #
# --------------------------------------------------------------------------- #
JWT_ALGORITHM = "HS256"

# Token schema version. Bump (in a reviewed diff) to invalidate every token in
# circulation simultaneously — a global, immediate kill-switch that needs no
# per-user state. Pinned in code on purpose, exactly like the bcrypt cost.
TOKEN_VERSION = 1

# Defaults mirror SECURITY.md; both are overridable per deployment via env so a
# UX regression can be rolled back without a code change (roadmap PH1.6).
DEFAULT_ACCESS_TTL_SECONDS = 15 * 60          # 15 minutes
DEFAULT_REFRESH_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

DEFAULT_ISSUER = "stockassist-ai"
DEFAULT_AUDIENCE = "stockassist-ai-app"

_ACCESS = "access"
_REFRESH = "refresh"

# Claims every valid token must carry. Presence is enforced at decode time so a
# token that predates this claim set (or has been stripped down) fails closed.
_REQUIRED_CLAIMS = ("exp", "iat", "aud", "iss", "sub", "jti", "type", "ver", "sid")


# --------------------------------------------------------------------------- #
# Typed errors — framework-neutral so the web layer owns the HTTP mapping.     #
# --------------------------------------------------------------------------- #
class TokenError(Exception):
    """Base class for all token-verification failures."""


class TokenExpired(TokenError):
    """The token's signature is valid but it is past its ``exp``."""


class TokenInvalid(TokenError):
    """The token is malformed, mis-signed, has the wrong audience/issuer/type/
    version, or is missing a required claim. Deliberately does not distinguish
    the specific cause to the caller (the 401 must stay generic)."""


# --------------------------------------------------------------------------- #
# Environment-driven configuration (small pure resolvers, like security.cookies)#
# --------------------------------------------------------------------------- #
def get_secret() -> str:
    """The HS256 signing secret. Required — no default, a missing value raises."""
    return os.environ["JWT_SECRET"]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


def access_ttl_seconds() -> int:
    """Access-token lifetime in seconds (``JWT_ACCESS_TTL_SECONDS``, default 15m)."""
    return _env_int("JWT_ACCESS_TTL_SECONDS", DEFAULT_ACCESS_TTL_SECONDS)


def refresh_ttl_seconds() -> int:
    """Refresh-token lifetime in seconds (``JWT_REFRESH_TTL_SECONDS``, default 7d)."""
    return _env_int("JWT_REFRESH_TTL_SECONDS", DEFAULT_REFRESH_TTL_SECONDS)


def issuer() -> str:
    """Expected ``iss`` claim (``JWT_ISSUER``, default ``stockassist-ai``)."""
    return os.environ.get("JWT_ISSUER", "").strip() or DEFAULT_ISSUER


def audience() -> str:
    """Expected ``aud`` claim (``JWT_AUDIENCE``, default ``stockassist-ai-app``)."""
    return os.environ.get("JWT_AUDIENCE", "").strip() or DEFAULT_AUDIENCE


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_jti() -> str:
    """A fresh, unguessable token id. This is the handle the ``SessionStore``
    stores, rotates, and revokes — it is an identifier, never a credential."""
    return secrets.token_urlsafe(24)


def _encode(payload: dict) -> str:
    return pyjwt.encode(payload, get_secret(), algorithm=JWT_ALGORITHM)


def _base_claims(subject: str, ttl_seconds: int, token_type: str,
                 session_id: str, jti: str) -> dict:
    issued = _now()
    return {
        "sub": subject,
        "type": token_type,
        "sid": session_id,
        "jti": jti,
        "iat": issued,
        "exp": issued + _timedelta(ttl_seconds),
        "aud": audience(),
        "iss": issuer(),
        "ver": TOKEN_VERSION,
    }


def _timedelta(seconds: int):
    from datetime import timedelta
    return timedelta(seconds=seconds)


# --------------------------------------------------------------------------- #
# Issuance                                                                       #
# --------------------------------------------------------------------------- #
def create_access_token(user_id: str, email: str, session_id: str,
                        jti: Optional[str] = None) -> str:
    """Mint a short-lived access token bound to ``session_id``.

    ``jti`` defaults to a fresh id; callers rarely need to pin it. ``email`` is
    carried on the access token only (it is convenience context for the API,
    not an identity key — ``sub`` is the identity)."""
    claims = _base_claims(user_id, access_ttl_seconds(), _ACCESS, session_id,
                          jti or new_jti())
    claims["email"] = email
    return _encode(claims)


def create_refresh_token(user_id: str, session_id: str, jti: str) -> str:
    """Mint a refresh token for ``session_id`` carrying ``jti``.

    Unlike the access token, ``jti`` is **required**: the session store records
    it as the family's current token and rejects any refresh whose ``jti`` no
    longer matches (rotation + reuse detection). Callers allocate the ``jti``,
    persist it on the session, then mint the token with it — so the stored id
    and the token's id can never disagree."""
    claims = _base_claims(user_id, refresh_ttl_seconds(), _REFRESH, session_id, jti)
    return _encode(claims)


# --------------------------------------------------------------------------- #
# Verification                                                                  #
# --------------------------------------------------------------------------- #
def decode_token(token: str, expected_type: str) -> dict:
    """Verify a token and return its claims, or raise a typed ``TokenError``.

    Checks, in order: signature (HS256, ``JWT_SECRET``), ``exp``, ``iat``,
    audience, issuer, presence of every required claim, then the ``type`` and
    ``ver`` match. Any failure raises ``TokenExpired`` (past ``exp``) or
    ``TokenInvalid`` (everything else) — never a raw ``pyjwt`` exception, so
    the caller maps exactly two outcomes to a generic 401.
    """
    try:
        claims = pyjwt.decode(
            token,
            get_secret(),
            algorithms=[JWT_ALGORITHM],
            audience=audience(),
            issuer=issuer(),
            options={"require": list(_REQUIRED_CLAIMS)},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise TokenExpired(str(exc)) from exc
    except pyjwt.InvalidTokenError as exc:
        # Wrong signature/audience/issuer/immature/malformed/missing-claim all
        # collapse to a single generic invalid outcome.
        raise TokenInvalid(str(exc)) from exc

    if claims.get("type") != expected_type:
        raise TokenInvalid(f"expected {expected_type} token")
    if claims.get("ver") != TOKEN_VERSION:
        raise TokenInvalid("stale token version")
    return claims


# --------------------------------------------------------------------------- #
# password_changed_at / global invalidation support                             #
# --------------------------------------------------------------------------- #
def _to_epoch(moment: Union[datetime, str, int, float, None]) -> Optional[int]:
    """Coerce a datetime / ISO-8601 string / epoch number to a UTC epoch int.

    Returns ``None`` for a missing or unparseable value — the caller treats
    that as "no cutoff set" (fail-open on a malformed timestamp rather than
    locking a user out of a valid session)."""
    if moment is None:
        return None
    if isinstance(moment, (int, float)):
        return int(moment)
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment)
        except ValueError:
            return None
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return int(moment.timestamp())
    return None


def token_issued_before(claims: dict, cutoff: Union[datetime, str, int, float, None]) -> bool:
    """True when the token was issued strictly before ``cutoff``.

    The middleware uses this against ``user.password_changed_at`` (and any
    future global "sessions invalidated at" marker): a password change or a
    logout-all sets the cutoff to now, and every token minted earlier — access
    *and* refresh — is treated as stale on its next use. A one-second grace is
    applied so a token minted in the same second as the change is not
    spuriously rejected (clock granularity, not a security-relevant window)."""
    cutoff_epoch = _to_epoch(cutoff)
    if cutoff_epoch is None:
        return False
    iat = claims.get("iat")
    if isinstance(iat, datetime):
        iat = int(iat.timestamp())
    if not isinstance(iat, (int, float)):
        # No usable iat — decode_token requires it, so this only happens for a
        # claim set built by hand; treat as stale to fail closed.
        return True
    return int(iat) < cutoff_epoch - 1

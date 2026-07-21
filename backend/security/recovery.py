"""Centralized identity-recovery tokens: verification, reset, single-use (PH1.8).

Single source of truth for how StockAssist AI mints, verifies, and *burns* the
one-time tokens behind email verification and password reset. No recovery token
is created, validated, or consumed outside this module — that keeps the token
format, the expiry policy, and the single-use guarantee in one auditable place
instead of per-flow rules that drift apart (exactly the discipline
``security.jwt`` / ``security.csrf`` established for their own credentials).

Two purposes, two lifetimes (SECURITY_ARCHITECTURE.md §29, roadmap PH1.8):

* ``PURPOSE_VERIFY_EMAIL`` — proves control of the address at sign-up. Long
  fuse (24h default): a user may not check mail immediately.
* ``PURPOSE_RESET_PASSWORD`` — authorizes a credential change for someone who
  cannot log in. Short fuse (30m default): it grants a password change, so it
  must be useless minutes after it lands in the inbox.

Design — **signed handle + authoritative DB record** (why both):

* The token handed to the user is ``<token_id>.<hmac>``. ``token_id`` is an
  unguessable, opaque id; ``hmac`` is ``HMAC(secret, prefix|purpose|user_id|
  token_id)``. The signature makes a token unforgeable without the server
  secret and binds it to exactly one user + one purpose — a verification token
  can never be replayed as a reset token, nor moved to another account.
* A signature alone cannot be *single-use* (it is stateless — the same string
  verifies forever until it expires). So every token also has a row in the
  ``recovery_tokens`` collection carrying ``issued_at`` / ``expires_at`` /
  ``used_at``. Expiry and single-use are enforced against that authoritative
  record, and ``consume`` burns it with an **atomic** compare-and-set
  (``used_at: None → now``) so two concurrent redemptions cannot both win
  (replay protection, not just best-effort).
* Issuing a fresh token for a purpose **invalidates the user's outstanding
  unused ones** of that purpose, so a "resend" or a second "forgot password"
  leaves exactly one live link — a stolen-but-superseded link is already dead.

Secret & posture (mirrors ``security.csrf``):

* HMAC key is ``RECOVERY_SECRET`` when set, else the already-required
  ``JWT_SECRET`` — no weak default, fail-closed on a missing secret. A fixed,
  domain-separating message prefix means a recovery MAC can never be confused
  with a CSRF MAC or a JWT even when they share the fallback secret.
* Nothing here logs a token, its signature, or its id-as-credential; the record
  the store persists holds only the opaque ``token_id`` (useless without the
  secret) — never the signed value handed to the user.

Framework-agnostic and DB-injected (``RecoveryStore(db)``), exactly like
``SessionStore``: the web layer composes it, and tests run it against the
in-memory ``FakeDB`` with no real Mongo.
"""
from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional

from security import jwt as jwt_service

COLLECTION = "recovery_tokens"

# --------------------------------------------------------------------------- #
# Purposes — one place, no magic strings at call sites.                         #
# --------------------------------------------------------------------------- #
PURPOSE_VERIFY_EMAIL = "email_verification"
PURPOSE_RESET_PASSWORD = "password_reset"
PURPOSES = frozenset({PURPOSE_VERIFY_EMAIL, PURPOSE_RESET_PASSWORD})

# Default lifetimes (seconds). Both env-overridable so an ops tweak needs no code
# change (roadmap PH1.6 established the pattern for token TTLs).
DEFAULT_VERIFY_TTL_SECONDS = 24 * 60 * 60   # 24 hours
DEFAULT_RESET_TTL_SECONDS = 30 * 60         # 30 minutes

# HMAC domain-separation prefix: even when RECOVERY_SECRET is unset and we fall
# back to JWT_SECRET, a recovery MAC can never be confused with a CSRF MAC or a
# JWT (each uses a distinct, versioned prefix).
_MAC_PREFIX = "stockassist.recovery.v1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Configuration (small pure resolvers, like security.jwt).                      #
# --------------------------------------------------------------------------- #
def _recovery_secret() -> str:
    """The HMAC key. ``RECOVERY_SECRET`` if configured, else the required
    ``JWT_SECRET``. No weak default — a missing ``JWT_SECRET`` raises (fail
    closed), exactly as ``security.jwt`` / ``security.csrf`` do."""
    configured = os.environ.get("RECOVERY_SECRET", "").strip()
    if configured:
        return configured
    return jwt_service.get_secret()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


def verify_email_ttl_seconds() -> int:
    """Email-verification token lifetime (``RECOVERY_VERIFY_TTL_SECONDS``, 24h)."""
    return _env_int("RECOVERY_VERIFY_TTL_SECONDS", DEFAULT_VERIFY_TTL_SECONDS)


def reset_password_ttl_seconds() -> int:
    """Password-reset token lifetime (``RECOVERY_RESET_TTL_SECONDS``, 30m)."""
    return _env_int("RECOVERY_RESET_TTL_SECONDS", DEFAULT_RESET_TTL_SECONDS)


def ttl_seconds(purpose: str) -> int:
    """Lifetime for ``purpose``. Used both to stamp ``expires_at`` and to tell
    the user how long their link is good for."""
    if purpose == PURPOSE_VERIFY_EMAIL:
        return verify_email_ttl_seconds()
    if purpose == PURPOSE_RESET_PASSWORD:
        return reset_password_ttl_seconds()
    raise ValueError(f"unknown recovery purpose: {purpose!r}")


# --------------------------------------------------------------------------- #
# Token mint / verify — <token_id>.<hmac>, bound to (purpose, user_id).         #
# --------------------------------------------------------------------------- #
def _sign(purpose: str, user_id: str, token_id: str) -> str:
    msg = f"{_MAC_PREFIX}.{purpose}.{user_id}.{token_id}".encode("utf-8")
    return hmac.new(_recovery_secret().encode("utf-8"), msg, sha256).hexdigest()


def _format(token_id: str, purpose: str, user_id: str) -> str:
    return f"{token_id}.{_sign(purpose, user_id, token_id)}"


def _signature_ok(token: str, purpose: str, user_id: str) -> bool:
    """Constant-time check that ``token`` carries a valid signature for
    ``(purpose, user_id)``. Structural/crypto validation only — the record's
    expiry and single-use state are checked separately against the DB."""
    token_id, _, sig = (token or "").partition(".")
    if not token_id or not sig:
        return False
    return hmac.compare_digest(sig, _sign(purpose, user_id, token_id))


def _token_id(token: str) -> Optional[str]:
    token_id, _, sig = (token or "").partition(".")
    if not token_id or not sig:
        return None
    return token_id


# --------------------------------------------------------------------------- #
# Store — authoritative record; expiry + single-use enforcement.                #
# --------------------------------------------------------------------------- #
@dataclass
class RecoveryToken:
    """A minted token plus the metadata a caller needs to email it and describe
    its lifetime. ``value`` is the only thing that ever goes to the user."""
    value: str
    token_id: str
    purpose: str
    user_id: str
    expires_at: datetime
    ttl_seconds: int


class RecoveryStore:
    """Durable single-use recovery-token store, scoped to an injected DB handle.

    Instantiate per call with the live handle — ``RecoveryStore(db).issue(...)``
    — so the module never captures a stale reference and tests can swap in the
    in-memory ``FakeDB``.
    """

    def __init__(self, db):
        self._col = getattr(db, COLLECTION)

    # -- issuance ----------------------------------------------------------- #
    async def issue(self, user_id: str, purpose: str, *,
                    invalidate_prior: bool = True) -> RecoveryToken:
        """Mint a fresh single-use token for ``user_id`` + ``purpose``.

        By default first invalidates the user's outstanding *unused* tokens of
        this purpose, so a resend / repeated forgot-password leaves exactly one
        live link. Returns the ``RecoveryToken`` — only ``.value`` is safe to
        hand to the user; the store never persists that signed value.
        """
        if purpose not in PURPOSES:
            raise ValueError(f"unknown recovery purpose: {purpose!r}")
        user_id = str(user_id)
        if invalidate_prior:
            await self.invalidate_outstanding(user_id, purpose)

        now = _now()
        life = ttl_seconds(purpose)
        expires_at = now + timedelta(seconds=life)
        token_id = secrets.token_urlsafe(24)
        await self._col.insert_one({
            "token_id": token_id,
            "user_id": user_id,
            "purpose": purpose,
            "issued_at": now.isoformat(),
            "expires_at": expires_at,   # datetime → a TTL index reaps at expiry
            "used_at": None,
        })
        return RecoveryToken(
            value=_format(token_id, purpose, user_id),
            token_id=token_id,
            purpose=purpose,
            user_id=user_id,
            expires_at=expires_at,
            ttl_seconds=life,
        )

    async def invalidate_outstanding(self, user_id: str, purpose: str) -> int:
        """Mark every still-unused token of this purpose as used, returning how
        many were retired. Called before issuing a new token (one live link at a
        time) and after a successful password change (belt-and-suspenders: any
        pending reset link is dead once the password already changed)."""
        result = await self._col.update_many(
            {"user_id": str(user_id), "purpose": purpose, "used_at": None},
            {"$set": {"used_at": _now().isoformat(), "invalidated": True}},
        )
        return int(getattr(result, "modified_count", 0))

    # -- verification / consumption ---------------------------------------- #
    async def verify(self, token: str, purpose: str) -> Optional[dict]:
        """Return the live record for ``token`` iff it is well-formed, correctly
        signed for ``purpose``, unused, and unexpired — else ``None``. Does NOT
        burn the token (use ``consume`` to redeem). No timing branch reveals
        *which* check failed; every failure is a plain ``None``."""
        token_id = _token_id(token)
        if not token_id:
            return None
        record = await self._col.find_one({"token_id": token_id})
        if not record:
            return None
        if record.get("purpose") != purpose:
            return None
        if record.get("used_at"):
            return None
        if self._is_expired(record):
            return None
        user_id = str(record.get("user_id") or "")
        if not _signature_ok(token, purpose, user_id):
            return None
        return record

    async def consume(self, token: str, purpose: str) -> Optional[dict]:
        """Verify then **atomically burn** the token, returning its record on
        success or ``None`` on any failure (invalid / expired / already used /
        lost the compare-and-set race). Single-use + replay protection live
        here: the update matches only while ``used_at`` is still ``None``, so at
        most one caller can ever redeem a given token."""
        record = await self.verify(token, purpose)
        if not record:
            return None
        result = await self._col.update_one(
            {"token_id": record["token_id"], "used_at": None},
            {"$set": {"used_at": _now().isoformat()}},
        )
        if not getattr(result, "modified_count", 0):
            # Someone else consumed it between verify and here — treat as spent.
            return None
        return record

    # -- internals ---------------------------------------------------------- #
    @staticmethod
    def _is_expired(record: dict) -> bool:
        expires_at = record.get("expires_at")
        if expires_at is None:
            return False
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at)
            except ValueError:
                return False
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return _now() >= expires_at
        return False

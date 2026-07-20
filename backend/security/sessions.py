"""Server-side session store: refresh-token families, rotation, revocation (PH1.6).

The stateful counterpart to ``security.jwt``. A **session** here is a
refresh-token *family*: one durable record that tracks the single refresh token
currently valid for one login on one device, plus enough context (device / IP /
timestamps) for a future "active sessions" screen (PH1.10). Access tokens are
NOT tracked — they are stateless and short-lived by design (SECURITY_ARCHITECTURE
§9); the store is consulted only at the refresh boundary, which is the one place
rotation and theft-detection actually happen.

Why a MongoDB collection (not the Redis cache layer): rotation with reuse
detection needs an *authoritative, durable* record of "which refresh token is
current for this family". The in-memory/Redis cache (``services.cache``) is a
best-effort, evictable store — losing a session record there would silently drop
reuse detection or log users out on a cache flush. Sessions live in Mongo next
to the users they belong to, survive restarts, and a TTL index reaps them at
expiry. The collection is injected (``SessionStore(db)``) so tests run against
the in-memory ``FakeDB`` with no real Mongo.

Rotation + reuse detection (the core of R-06):

* Every refresh **rotates**: the presented refresh token is accepted only if
  its ``jti`` equals the family's ``current_jti``; on success a new ``jti``
  becomes current and the old token is dead forever (single-use).
* Replaying an already-rotated refresh token (``jti`` no longer current, but the
  family still exists) is the signature of a stolen token being used after the
  legitimate client already rotated. That **revokes the entire family**: both
  the thief's and the victim's tokens stop working, converting a silent
  compromise into a visible re-login.
* A revoked or expired family refreshes to nothing — 401.

Nothing in this module stores or logs a raw token. The ``jti`` it persists is an
opaque identifier, not the signed credential.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from security.jwt import refresh_ttl_seconds

COLLECTION = "sessions"

# Rotation outcomes. Callers branch on these; only ROTATED issues new tokens.
ROTATED = "rotated"                 # valid, single-use honored → new tokens minted
REUSE_DETECTED = "reuse_detected"   # replay of a rotated token → family revoked → 401
NOT_FOUND = "not_found"             # unknown family (cleaned up / never existed) → 401
REVOKED = "revoked"                 # family already revoked (logout / prior reuse) → 401
EXPIRED = "expired"                 # family past its absolute expiry → 401

# Revocation reasons — recorded on the session for audit/PH1.10, never surfaced
# to the client (the 401 stays generic).
REASON_LOGOUT = "logout"
REASON_LOGOUT_ALL = "logout_all"
REASON_REUSE = "refresh_reuse_detected"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_session_id() -> str:
    """A fresh, unguessable session/family id (the token ``sid``)."""
    return secrets.token_urlsafe(24)


@dataclass
class RotationResult:
    """Outcome of a refresh attempt. ``ok`` is True only for a clean rotation;
    ``session`` is the (possibly now-revoked) record for context/audit."""
    outcome: str
    session: Optional[dict] = None

    @property
    def ok(self) -> bool:
        return self.outcome == ROTATED


class SessionStore:
    """Durable refresh-token-family store, scoped to an injected DB handle.

    Instantiate per call with the live handle — ``SessionStore(db).rotate(...)``
    — so the module never captures a stale reference and tests can swap in the
    in-memory ``FakeDB``.
    """

    def __init__(self, db):
        self._col = getattr(db, COLLECTION)

    # -- lifecycle ---------------------------------------------------------- #
    async def create(self, user_id: str, jti: str, *,
                     user_agent: Optional[str] = None,
                     ip: Optional[str] = None) -> str:
        """Open a new session (family) for ``user_id`` with ``jti`` as its first
        current refresh id. Captures device/IP context for PH1.10. Returns the
        new ``session_id`` (the value that goes into every token's ``sid``)."""
        now = _now()
        session_id = new_session_id()
        await self._col.insert_one({
            "session_id": session_id,
            "user_id": str(user_id),
            "current_jti": jti,
            "refresh_count": 0,
            "user_agent": user_agent,
            "ip": ip,
            "created_at": now.isoformat(),
            "last_used_at": now.isoformat(),
            "expires_at": now + timedelta(seconds=refresh_ttl_seconds()),
            "revoked": False,
            "revoked_at": None,
            "revoked_reason": None,
        })
        return session_id

    async def rotate(self, session_id: str, presented_jti: str,
                     new_jti: str) -> RotationResult:
        """Attempt to rotate a session's refresh token.

        Accepts the refresh only when the family exists, is neither revoked nor
        expired, and ``presented_jti`` is the family's current id. On success
        ``new_jti`` becomes current (old token dead), the counter and
        ``last_used_at`` advance, and the absolute expiry slides forward by a
        full refresh lifetime so an actively-used session is never logged out
        mid-use. A presented id that no longer matches a still-live family is
        treated as token theft and revokes the whole family."""
        session = await self._col.find_one({"session_id": session_id})
        if not session:
            return RotationResult(NOT_FOUND)
        if session.get("revoked"):
            return RotationResult(REVOKED, session)
        if self._is_expired(session):
            return RotationResult(EXPIRED, session)

        if presented_jti != session.get("current_jti"):
            # Replay of an already-rotated token against a live family → theft.
            await self._revoke_doc(session_id, REASON_REUSE)
            return RotationResult(REUSE_DETECTED, session)

        now = _now()
        await self._col.update_one(
            {"session_id": session_id},
            {"$set": {
                "current_jti": new_jti,
                "last_used_at": now.isoformat(),
                "expires_at": now + timedelta(seconds=refresh_ttl_seconds()),
            }, "$inc": {"refresh_count": 1}},
        )
        return RotationResult(ROTATED, session)

    async def revoke(self, session_id: str, *, reason: str = REASON_LOGOUT) -> bool:
        """Revoke a single session (e.g. logout of the current device). Returns
        True if a live session was revoked, False if none matched / already
        revoked. Idempotent and best-effort — never raises for a missing id."""
        result = await self._col.update_one(
            {"session_id": session_id, "revoked": False},
            {"$set": {"revoked": True, "revoked_at": _now().isoformat(),
                      "revoked_reason": reason}},
        )
        return bool(getattr(result, "modified_count", 0))

    async def revoke_all_for_user(self, user_id: str, *,
                                  reason: str = REASON_LOGOUT_ALL) -> int:
        """Revoke every live session for ``user_id`` (logout-all-devices).

        Returns the number of sessions revoked. This is the data/service-layer
        primitive PH1.10's "sign out everywhere" UI will call; combined with a
        ``password_changed_at`` bump it also invalidates outstanding *access*
        tokens, not just refresh tokens."""
        result = await self._col.update_many(
            {"user_id": str(user_id), "revoked": False},
            {"$set": {"revoked": True, "revoked_at": _now().isoformat(),
                      "revoked_reason": reason}},
        )
        return int(getattr(result, "modified_count", 0))

    # -- reads (PH1.10 groundwork) ----------------------------------------- #
    async def get(self, session_id: str) -> Optional[dict]:
        return await self._col.find_one({"session_id": session_id})

    async def is_active(self, session_id: str) -> bool:
        """True when the session exists, is not revoked, and has not expired."""
        session = await self._col.find_one({"session_id": session_id})
        return bool(
            session and not session.get("revoked") and not self._is_expired(session)
        )

    async def list_for_user(self, user_id: str, *, active_only: bool = True) -> list:
        """All sessions for a user, newest first — the future sessions screen.

        ``active_only`` filters to live sessions; passing ``False`` includes
        revoked/expired ones for an audit view."""
        query: dict = {"user_id": str(user_id)}
        if active_only:
            query["revoked"] = False
        sessions = await self._col.find(query).sort("created_at", -1).to_list(100)
        if active_only:
            sessions = [s for s in sessions if not self._is_expired(s)]
        return sessions

    # -- internals ---------------------------------------------------------- #
    async def _revoke_doc(self, session_id: str, reason: str) -> None:
        await self._col.update_one(
            {"session_id": session_id},
            {"$set": {"revoked": True, "revoked_at": _now().isoformat(),
                      "revoked_reason": reason}},
        )

    @staticmethod
    def _is_expired(session: dict) -> bool:
        expires_at = session.get("expires_at")
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

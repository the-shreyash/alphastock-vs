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

The rotation grace window (D6.2 / F)
------------------------------------
Strict single-use rotation has one benign failure mode, and for a trading
dashboard it is not a rare one: **two browser tabs**. Cookies are shared across
tabs, the access token expires for all of them at the same instant, and each tab
runs its own independent refresh queue — so both POST the *same* refresh token
within milliseconds of each other. One rotates; the other presents a token that
is no longer current against a family that is very much alive, which is
bit-for-bit the signature of theft. The user is signed out of every tab and a
CRITICAL "token replay detected" record is written, for doing nothing but
opening a second tab.

So a presented token that is the **immediately previous** ``jti`` and arrives
within ``JWT_REFRESH_GRACE_SECONDS`` (default 10) of the rotation that retired
it is answered with the family's *current* token pair instead of a revocation —
outcome ``GRACE_REPLAY``. No new rotation happens, so the window cannot be
walked forward by repeated replay: the grace is anchored to a single rotation
instant, and only one token generation is ever forgiven.

This does not weaken theft detection in any way that matters. An attacker
holding a stolen refresh token does not need to race the legitimate client — it
can simply use the token first and get a full rotation, which is what reuse
detection catches on the *victim's* next refresh. What the window forgives is
exactly the case that carries no new information: a replay so close to the
rotation that it cannot be distinguished from the same browser asking twice.
Everything outside it — the replay an hour later, the replay from another
device, the second replay of the same retired token — still revokes the family.
See RFC 9700 §4.14.2, which explicitly contemplates a short grace for
concurrency.

Nothing in this module stores or logs a raw token. The ``jti`` it persists is an
opaque identifier, not the signed credential.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from security.jwt import refresh_ttl_seconds

COLLECTION = "sessions"

# Rotation outcomes. Callers branch on these; ROTATED and GRACE_REPLAY issue
# tokens, everything else is a 401.
ROTATED = "rotated"                 # valid, single-use honored → new tokens minted
GRACE_REPLAY = "grace_replay"       # benign concurrent refresh → re-issue current pair
REUSE_DETECTED = "reuse_detected"   # replay of a rotated token → family revoked → 401
NOT_FOUND = "not_found"             # unknown family (cleaned up / never existed) → 401
REVOKED = "revoked"                 # family already revoked (logout / prior reuse) → 401
EXPIRED = "expired"                 # family past its absolute expiry → 401

#: How long the immediately-previous refresh ``jti`` stays acceptable after the
#: rotation that retired it (D6.2 / F). Deliberately small: it is sized for two
#: tabs racing on the same machine, not for any kind of retry. ``0`` restores
#: strict single-use, which is what the theft-detection tests configure so the
#: strict path is still exercised rather than merely assumed.
DEFAULT_ROTATION_GRACE_SECONDS = 10


def rotation_grace_seconds() -> int:
    """Configured grace window (``JWT_REFRESH_GRACE_SECONDS``), never negative."""
    raw = os.environ.get("JWT_REFRESH_GRACE_SECONDS", "").strip()
    if not raw:
        return DEFAULT_ROTATION_GRACE_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_ROTATION_GRACE_SECONDS


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
    """Outcome of a refresh attempt. ``ok`` is True for a clean rotation and for
    a benign within-grace concurrent replay; ``session`` is the (possibly
    now-revoked) record for context/audit."""
    outcome: str
    session: Optional[dict] = None
    #: The refresh ``jti`` the caller must mint its new refresh token with.
    #:
    #: For a clean rotation that is the ``new_jti`` the caller supplied, which
    #: is now current. For a grace replay nothing rotated, so it is the ``jti``
    #: that was already current — re-issuing it hands the second tab the same
    #: token the first one just received, which is the correct answer: they
    #: share one cookie jar and one session. ``None`` for every failure.
    issued_jti: Optional[str] = None

    @property
    def ok(self) -> bool:
        """True when the caller should issue tokens.

        Both a clean rotation and a within-grace concurrent replay produce a
        usable session; they differ only in which ``jti`` is minted against
        (``issued_jti``) and in what gets audited."""
        return self.outcome in (ROTATED, GRACE_REPLAY)


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
            # The generation this one retired, and when (D6.2 / F). A brand-new
            # family has retired nothing, so there is nothing to forgive yet.
            "previous_jti": None,
            "previous_jti_at": None,
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
            # Not the current token. Before calling it theft, check whether it
            # is the generation we retired a moment ago — the two-tab race
            # (D6.2 / F). Anything else, including a second replay of the same
            # retired token, is a replay against a live family → theft.
            if self._within_rotation_grace(session, presented_jti):
                return RotationResult(
                    GRACE_REPLAY, session,
                    issued_jti=session.get("current_jti"),
                )
            await self._revoke_doc(session_id, REASON_REUSE)
            return RotationResult(REUSE_DETECTED, session)

        now = _now()
        await self._col.update_one(
            {"session_id": session_id},
            {"$set": {
                "current_jti": new_jti,
                # Remember exactly one retired generation, and the instant it
                # was retired. Overwriting rather than appending is what keeps
                # the grace anchored: only the most recent rotation is ever
                # forgivable, so the window cannot be walked forward.
                "previous_jti": presented_jti,
                "previous_jti_at": now.isoformat(),
                "last_used_at": now.isoformat(),
                "expires_at": now + timedelta(seconds=refresh_ttl_seconds()),
            }, "$inc": {"refresh_count": 1}},
        )
        return RotationResult(ROTATED, session, issued_jti=new_jti)

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
    def _within_rotation_grace(session: dict, presented_jti: str) -> bool:
        """True when ``presented_jti`` is the generation retired by the most
        recent rotation and that rotation happened within the grace window.

        A grace of ``0`` disables this entirely and restores strict single-use.
        A malformed or missing timestamp is treated as *outside* the window —
        the fail-closed direction, since the consequence of getting it wrong is
        forgiving a replay that should have revoked a family."""
        grace = rotation_grace_seconds()
        if grace <= 0:
            return False
        previous_jti = session.get("previous_jti")
        if not previous_jti or presented_jti != previous_jti:
            return False
        rotated_at = session.get("previous_jti_at")
        if isinstance(rotated_at, str):
            try:
                rotated_at = datetime.fromisoformat(rotated_at)
            except ValueError:
                return False
        if not isinstance(rotated_at, datetime):
            return False
        if rotated_at.tzinfo is None:
            rotated_at = rotated_at.replace(tzinfo=timezone.utc)
        return (_now() - rotated_at) <= timedelta(seconds=grace)

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

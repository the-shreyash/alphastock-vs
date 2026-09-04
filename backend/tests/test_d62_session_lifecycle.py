"""D6.2 — SESSION LIFECYCLE HARDENING, server side (scope F and D).

D6.2 is mostly a client-side sprint: the HTTP refresh queue, the four-state
session machine and the WebSocket re-auth path all live in the SPA. What the
backend owed was an audit of the refresh/session mechanism against the sprint's
list — rotation, single-use, reuse detection, expiry, revocation, logout,
**concurrent refresh**, deleted-user — and the instruction was to add tests
rather than rewrite anything already correct.

Seven of those eight were already correct and are pinned here so that stays
true. The eighth was not, and it is the one this file exists for:

    CONCURRENT REFRESH. Strict single-use rotation treats two browser tabs as a
    token thief. Cookies are shared across tabs, the access token expires for
    all of them at the same instant, and each tab runs its own refresh queue —
    so both POST the same refresh token milliseconds apart. One rotated; the
    other presented a retired token against a live family, which is bit-for-bit
    the signature of theft, so the family was revoked, every tab was signed out
    and a CRITICAL "token replay detected" record was written. For opening a
    second tab. The rotation grace window (security/sessions.py) forgives
    exactly that case and nothing else.

The other half of this file covers the deployment-topology checker added for
scope D — the cookie/CORS/CSRF configuration only works as one system, and a
mismatch between its parts fails silently and totally.

Hermetic: `SessionStore` runs against the in-memory `FakeDB` through
`asyncio.run`, endpoint tests use the shared `client`/`fake_db` fixtures.
"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from security import cookies as cookie_policy
from security.sessions import (
    DEFAULT_ROTATION_GRACE_SECONDS,
    GRACE_REPLAY,
    REUSE_DETECTED,
    REVOKED,
    ROTATED,
    SessionStore,
    rotation_grace_seconds,
)
from tests._fakedb import FakeDB

REG_PASSWORD = "S3cure!Passw0rd"


def _run(coro):
    return asyncio.run(coro)


def _expect_closed(ws, *, timeout=3.0):
    """Assert the server closed ``ws``, without the test being able to hang.

    A bare `ws.receive_json()` is the natural assertion and the wrong one: when
    the control under test regresses, the socket simply stays open and the read
    blocks forever — so a broken control produces a hung suite instead of a red
    test, which is the worst possible failure mode for a regression guard.
    (Verified: the first mutation run against this file hung until it was
    killed.) The read therefore happens on a **daemon** thread with a deadline —
    daemon so that a thread still blocked on a socket that was never closed
    cannot hold the interpreter open at exit either.
    """
    import queue
    import threading

    from starlette.websockets import WebSocketDisconnect

    outcome: "queue.Queue" = queue.Queue(maxsize=1)

    def _read():
        try:
            outcome.put(("message", ws.receive()))
        except WebSocketDisconnect:
            outcome.put(("closed", None))
        except Exception as exc:  # a closed transport raises in several shapes
            outcome.put(("closed", exc))

    threading.Thread(target=_read, daemon=True).start()
    try:
        kind, payload = outcome.get(timeout=timeout)
    except queue.Empty:
        raise AssertionError(
            "the socket is still open and still delivering: the server did not " "close it when the session was revoked"
        )
    if kind == "closed":
        return
    assert payload.get("type") == "websocket.close", f"expected a close frame, got {payload!r}"


def _register(client, email="d62@example.com"):
    return client.post(
        "/api/auth/register",
        json={
            "name": "D62 Tester",
            "email": email,
            "password": REG_PASSWORD,
        },
    )


def _age_rotation(db, sid, *, seconds):
    """Backdate the recorded rotation instant, so a replay lands outside the
    grace window without the test having to sleep."""
    _run(
        db.sessions.update_one(
            {"session_id": sid},
            {"$set": {"previous_jti_at": (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()}},
        )
    )


def _rotation_instant_in_the_future(db, sid, *, seconds=60):
    """Record the rotation as having happened ``seconds`` in the FUTURE.

    Deliberately unphysical, and the only construction that isolates the
    ``grace <= 0`` guard from the passage of real time: a future instant makes
    the elapsed time negative, and a negative elapsed is *inside* every window,
    including a zero-second one. With this in place the strict-single-use test
    can only pass because the guard exists — see
    `test_grace_zero_restores_strict_single_use` and its twin.
    """
    _run(
        db.sessions.update_one(
            {"session_id": sid},
            {"$set": {"previous_jti_at": (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()}},
        )
    )


#: Every field of a refresh-token family whose movement would extend, refresh or
#: re-anchor the session. A grace replay must move none of them (ADR-061).
LIFETIME_FIELDS = (
    "expires_at",
    "last_used_at",
    "refresh_count",
    "previous_jti",
    "previous_jti_at",
    "current_jti",
    "session_id",
)


# =========================================================================== #
# F — the rotation grace window                                               #
# =========================================================================== #
class TestRotationGraceWindow:
    def test_default_window_is_short_and_configurable(self, monkeypatch):
        monkeypatch.delenv("JWT_REFRESH_GRACE_SECONDS", raising=False)
        assert rotation_grace_seconds() == DEFAULT_ROTATION_GRACE_SECONDS
        assert DEFAULT_ROTATION_GRACE_SECONDS <= 30, (
            "The window is sized for two tabs racing on one machine. Anything "
            "long enough to be useful as a retry budget is long enough to be "
            "useful to somebody replaying a stolen token."
        )
        monkeypatch.setenv("JWT_REFRESH_GRACE_SECONDS", "3")
        assert rotation_grace_seconds() == 3

    def test_malformed_and_negative_windows_are_safe(self, monkeypatch):
        monkeypatch.setenv("JWT_REFRESH_GRACE_SECONDS", "not-a-number")
        assert rotation_grace_seconds() == DEFAULT_ROTATION_GRACE_SECONDS
        monkeypatch.setenv("JWT_REFRESH_GRACE_SECONDS", "-60")
        assert rotation_grace_seconds() == 0

    def test_second_tab_replaying_the_retired_token_is_not_theft(self):
        """The whole point: tab A rotates, tab B presents the same token."""
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))  # tab A
        res = _run(store.rotate(sid, "jti-0", "jti-9"))  # tab B, same token

        assert res.outcome == GRACE_REPLAY
        assert res.ok, "a grace replay must still issue a usable token pair"
        assert _run(store.is_active(sid)) is True, "the family must survive"

    def test_grace_replay_issues_the_CURRENT_jti_not_a_new_one(self):
        """The subtle half. Minting `new_jti` here would hand tab B a refresh
        token the store does not consider current — so B's *next* refresh would
        present a stranger's jti and trip reuse detection, revoking the family
        one round trip later. The bug would look like "logging out at random,
        fifteen minutes after opening a second tab"."""
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))
        res = _run(store.rotate(sid, "jti-0", "jti-9"))

        assert res.issued_jti == "jti-1"
        assert res.issued_jti != "jti-9"
        # And the token it names really is the one that still works.
        assert _run(store.rotate(sid, "jti-1", "jti-2")).outcome == ROTATED

    def test_grace_replay_does_not_rotate(self):
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))
        before = _run(store.get(sid))["refresh_count"]

        _run(store.rotate(sid, "jti-0", "jti-9"))

        after = _run(store.get(sid))
        assert after["refresh_count"] == before, "grace must not consume a rotation"
        assert after["current_jti"] == "jti-1"

    def test_a_grace_replay_extends_nothing(self):
        """The security half of the window: a replay is *forgiven*, never
        *rewarded*.

        `test_grace_replay_does_not_rotate` pins two fields. This pins the whole
        record, because the field that would matter most if it moved is the one
        nobody would think to check: `expires_at`. A rotation slides the family's
        absolute expiry forward by a full refresh lifetime, so if the grace path
        ever shared that write, anybody holding one retired refresh token could
        keep a family alive indefinitely by replaying it — without ever holding a
        *current* token, and without the replay ever being audited as theft. The
        same argument applies to `last_used_at` (an idle session would look
        active), to `refresh_count` (the rotation budget would be miscounted) and
        to `previous_jti_at` (re-anchoring the window is exactly the walk-forward
        `test_the_window_cannot_be_walked_forward` forbids).

        R1 -> rotate to R2, then R1 -> grace replay, snapshotting the stored
        family either side of the replay. The correct diff is the empty one: the
        grace path performs no write at all.
        """
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-R1"))
        assert _run(store.rotate(sid, "jti-R1", "jti-R2")).outcome == ROTATED

        # deepcopy because a snapshot that aliases the stored document would
        # compare equal to itself no matter what the replay did.
        before = deepcopy(_run(store.get(sid)))
        replay = _run(store.rotate(sid, "jti-R1", "jti-R3"))
        after = deepcopy(_run(store.get(sid)))

        assert replay.outcome == GRACE_REPLAY, "setup did not produce the case under test"
        for field in LIFETIME_FIELDS:
            assert after[field] == before[field], (
                f"a grace replay moved {field!r}: {before[field]!r} -> {after[field]!r}. "
                "The grace path must not write to the family at all."
            )
        # Belt as well as braces: nothing else moved either, including fields a
        # future change might add.
        assert after == before, f"a grace replay wrote to the family: {before!r} -> {after!r}"

    def test_the_no_extension_snapshot_can_actually_fail(self):
        """The falsifying twin. The test above asserts that a diff is empty; on
        its own it cannot distinguish "nothing changed" from "the snapshot does
        not observe change". So run the identical snapshot across a *real*
        rotation and require every lifetime field to move.

        `last_used_at` and `expires_at` are moved off their written values first,
        so their movement is a matter of construction rather than of clock
        resolution — a rotation two microseconds after the snapshot would
        otherwise be free to produce an identical timestamp on a coarse clock and
        make this twin flaky. `expires_at` is pulled *in* to a minute from now
        rather than backdated: an expiry in the past would make the family
        expired and the rotation would never happen.
        """
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-R1"))
        _run(store.rotate(sid, "jti-R1", "jti-R2"))
        now = datetime.now(timezone.utc)
        _run(
            db.sessions.update_one(
                {"session_id": sid},
                {
                    "$set": {
                        "last_used_at": (now - timedelta(hours=1)).isoformat(),
                        "expires_at": now + timedelta(seconds=60),
                    }
                },
            )
        )

        before = deepcopy(_run(store.get(sid)))
        assert _run(store.rotate(sid, "jti-R2", "jti-R3")).outcome == ROTATED
        after = deepcopy(_run(store.get(sid)))

        moved = {f for f in LIFETIME_FIELDS if after[f] != before[f]}
        assert moved == {
            "expires_at",
            "last_used_at",
            "refresh_count",
            "previous_jti",
            "previous_jti_at",
            "current_jti",
        }, f"the snapshot does not observe the fields it claims to pin; it saw {moved}"
        assert after["session_id"] == before["session_id"], "a rotation must not re-key the family"

    def test_the_window_cannot_be_walked_forward(self):
        """Repeated replay must not keep re-arming the grace. Only ONE
        generation is ever forgivable and the anchor is the rotation instant, so
        a token retired long ago is still theft no matter how many benign
        replays happened since."""
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))
        assert _run(store.rotate(sid, "jti-0", "jti-9")).outcome == GRACE_REPLAY
        _age_rotation(db, sid, seconds=3600)

        assert _run(store.rotate(sid, "jti-0", "jti-9")).outcome == REUSE_DETECTED
        assert _run(store.is_active(sid)) is False

    def test_replay_outside_the_window_still_revokes_the_family(self):
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))
        _age_rotation(db, sid, seconds=DEFAULT_ROTATION_GRACE_SECONDS + 5)

        assert _run(store.rotate(sid, "jti-0", "jti-2")).outcome == REUSE_DETECTED
        assert _run(store.rotate(sid, "jti-1", "jti-3")).outcome == REVOKED

    def test_a_token_two_generations_old_is_never_forgiven(self):
        """Only the immediately-previous generation is in the window. A token
        from before that is unambiguously stale and its presentation is theft."""
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))
        _run(store.rotate(sid, "jti-1", "jti-2"))

        assert _run(store.rotate(sid, "jti-0", "jti-9")).outcome == REUSE_DETECTED

    def test_a_fabricated_jti_is_theft_even_inside_the_window(self):
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))

        assert _run(store.rotate(sid, "guessed", "jti-9")).outcome == REUSE_DETECTED

    @pytest.mark.parametrize("configured", ["0", "-60"])
    def test_grace_zero_restores_strict_single_use(self, monkeypatch, configured):
        """Prove the `grace <= 0` guard, not the passage of time.

        The obvious version of this test — set the window to zero, rotate,
        replay — is green even with `if grace <= 0: return False` deleted from
        `_within_rotation_grace`. By the time the replay is evaluated a few
        microseconds of real time have elapsed, so `elapsed <= timedelta(0)` is
        already False and the replay falls through to theft anyway. It asserts a
        true outcome for a reason that has nothing to do with the branch it is
        named after, and it cannot fail when that branch is removed.

        So the rotation instant is pinned in the future: elapsed is negative,
        which is inside a zero-second window. The guard is now the only thing in
        the code that can produce REUSE_DETECTED. The twin below runs this exact
        setup with a positive window and gets GRACE_REPLAY, which proves the
        construction really does land inside the window — i.e. that this test is
        capable of failing.

        Both spellings of "off" are covered: an explicit `0`, and a negative
        value, which `rotation_grace_seconds()` clamps to 0. A clamp that leaked
        a negative through would reach the same guard, so the branch is what is
        under test either way.
        """
        monkeypatch.setenv("JWT_REFRESH_GRACE_SECONDS", configured)
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))
        _rotation_instant_in_the_future(db, sid)

        assert _run(store.rotate(sid, "jti-0", "jti-9")).outcome == REUSE_DETECTED
        assert _run(store.is_active(sid)) is False, "strict single-use must still revoke the family"

    def test_the_zero_window_construction_lands_inside_the_window(self, monkeypatch):
        """Falsifying twin for the test above. Identical setup, positive window:
        if this did not come back GRACE_REPLAY, the future-dated rotation instant
        would not actually be inside any window and the strict test would be
        passing for the old, uninformative reason."""
        monkeypatch.setenv("JWT_REFRESH_GRACE_SECONDS", "5")
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))
        _rotation_instant_in_the_future(db, sid)

        assert _run(store.rotate(sid, "jti-0", "jti-9")).outcome == GRACE_REPLAY

    def test_missing_rotation_timestamp_fails_closed(self):
        """A record without a usable timestamp cannot be shown to be inside the
        window, so it is treated as outside it. Forgiving an unknown is the
        expensive direction here."""
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))
        _run(db.sessions.update_one({"session_id": sid}, {"$set": {"previous_jti_at": "not-a-timestamp"}}))

        assert _run(store.rotate(sid, "jti-0", "jti-9")).outcome == REUSE_DETECTED

    def test_grace_does_not_resurrect_a_revoked_family(self):
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))
        _run(store.revoke(sid))

        assert _run(store.rotate(sid, "jti-0", "jti-9")).outcome == REVOKED


# =========================================================================== #
# F — the same behaviour at the HTTP boundary                                  #
# =========================================================================== #
class TestConcurrentRefreshEndpoint:
    def test_two_tabs_refreshing_the_same_token_both_succeed(self, client, fake_db):
        """End to end: the second tab gets a 200 and a usable session, and the
        first tab's session is still alive afterwards. Before the grace window
        this was 401 + a revoked family + both tabs signed out."""
        _register(client, email="tabs@example.com")
        shared_refresh = client.cookies.get("refresh_token")

        first = client.post("/api/auth/refresh", cookies={"refresh_token": shared_refresh})
        assert first.status_code == 200
        second = client.post("/api/auth/refresh", cookies={"refresh_token": shared_refresh})
        assert second.status_code == 200

        # The family is intact: the token the jar now holds still refreshes.
        assert client.post("/api/auth/refresh").status_code == 200

    def test_the_second_tab_receives_a_token_that_actually_works(self, client, fake_db):
        """A 200 whose cookie is unusable would be worse than the 401 it
        replaced — the failure would arrive one refresh later, unexplained."""
        _register(client, email="tabs2@example.com")
        shared_refresh = client.cookies.get("refresh_token")

        client.post("/api/auth/refresh", cookies={"refresh_token": shared_refresh})
        second = client.post("/api/auth/refresh", cookies={"refresh_token": shared_refresh})
        handed_back = second.cookies.get("refresh_token")
        assert handed_back

        again = client.post("/api/auth/refresh", cookies={"refresh_token": handed_back})
        assert again.status_code == 200, (
            "the grace-replay response handed out a refresh token the store did " "not consider current"
        )

    def test_the_second_tab_does_not_extend_the_session(self, client, fake_db):
        """The store-level invariant, asserted where it actually matters: a real
        HTTP grace replay must leave the persisted family byte-identical. If the
        endpoint ever grew its own "touch the session on every refresh" write,
        the store-level test would still pass and this one would not."""
        _register(client, email="noextend@example.com")
        shared_refresh = client.cookies.get("refresh_token")

        assert client.post("/api/auth/refresh", cookies={"refresh_token": shared_refresh}).status_code == 200
        before = deepcopy(_run(fake_db.sessions.find_one({})))

        replay = client.post("/api/auth/refresh", cookies={"refresh_token": shared_refresh})

        assert replay.status_code == 200, "setup did not produce a grace replay"
        after = deepcopy(_run(fake_db.sessions.find_one({})))
        for field in LIFETIME_FIELDS:
            assert after[field] == before[field], f"the refresh endpoint moved {field!r} on a grace replay"
        assert after == before

    def test_a_stale_token_still_revokes_the_family_at_the_endpoint(self, client, fake_db, monkeypatch):
        monkeypatch.setenv("JWT_REFRESH_GRACE_SECONDS", "0")
        _register(client, email="thief@example.com")
        stolen = client.cookies.get("refresh_token")
        assert client.post("/api/auth/refresh").status_code == 200

        assert client.post("/api/auth/refresh", cookies={"refresh_token": stolen}).status_code == 401
        assert client.post("/api/auth/refresh").status_code == 401


# =========================================================================== #
# F — the seven behaviours that were already correct, pinned                    #
# =========================================================================== #
class TestSessionSemanticsRegression:
    def test_refresh_requires_the_cookie_not_a_body_or_header(self, client, fake_db):
        _register(client, email="cookieonly@example.com")
        token = client.cookies.get("refresh_token")
        client.cookies.clear()

        assert client.post("/api/auth/refresh").status_code == 401
        assert client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        assert client.post("/api/auth/refresh", json={"refresh_token": token}).status_code == 401

    def test_logout_kills_the_refresh_token_immediately(self, client, fake_db):
        _register(client, email="lo62@example.com")
        token = client.cookies.get("refresh_token")
        assert client.post("/api/auth/logout").status_code == 200

        assert client.post("/api/auth/refresh", cookies={"refresh_token": token}).status_code == 401

    def test_logout_is_csrf_exempt_so_a_dying_client_can_still_clear_cookies(self, client, fake_db):
        """D6.2-D. The SPA calls logout on a definitive expiry to get rid of an
        access cookie that may still be minutes from expiring. That call happens
        when the CSRF cookie is stale by definition, so the endpoint must not
        require it."""
        from security.csrf import _DEFAULT_EXEMPT_PATHS

        assert "/api/auth/logout" in _DEFAULT_EXEMPT_PATHS

    def test_logout_clears_both_auth_cookies(self, client, fake_db):
        _register(client, email="clear62@example.com")
        r = client.post("/api/auth/logout")
        cleared = " ".join(r.headers.get_list("set-cookie"))
        assert "access_token=" in cleared and "refresh_token=" in cleared

    def test_deleted_user_cannot_refresh(self, client, fake_db):
        r = _register(client, email="gone@example.com")
        user_id = r.json()["id"]
        from bson import ObjectId

        _run(fake_db.users.delete_one({"_id": ObjectId(user_id)}))

        assert client.post("/api/auth/refresh").status_code == 401

    def test_expired_family_refreshes_to_nothing(self):
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(
            db.sessions.update_one(
                {"session_id": sid}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}}
            )
        )

        assert _run(store.rotate(sid, "jti-0", "jti-1")).ok is False

    def test_rotation_slides_the_absolute_expiry_forward(self):
        """An actively used session must not be signed out mid-use."""
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(
            db.sessions.update_one(
                {"session_id": sid}, {"$set": {"expires_at": datetime.now(timezone.utc) + timedelta(seconds=30)}}
            )
        )

        _run(store.rotate(sid, "jti-0", "jti-1"))

        expires = _run(store.get(sid))["expires_at"]
        assert expires > datetime.now(timezone.utc) + timedelta(days=1)


# =========================================================================== #
# D — cookie / CORS / CSRF topology coherence                                  #
# =========================================================================== #
class TestCookieTopologyChecker:
    """The configuration is three files that only work as one system, and the
    failure mode of a mismatch is silent: the API answers, CORS matches, and
    every cookie-authenticated mutation 403s because the SPA is looking for a
    csrf_token the browser filed under a host it is not on."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for var in (
            "COOKIE_DOMAIN",
            "COOKIE_SAMESITE",
            "COOKIE_SECURE",
            "APP_ENV",
            "API_PUBLIC_ORIGIN",
            "CORS_ALLOWED_ORIGINS",
            "CORS_ORIGINS",
            "FRONTEND_URL",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_matched_single_host_deployment_is_clean(self, monkeypatch):
        monkeypatch.setenv("API_PUBLIC_ORIGIN", "https://app.example.com")
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
        assert cookie_policy.cookie_policy_warnings() == []

    def test_subdomain_split_with_a_covering_domain_is_clean(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("API_PUBLIC_ORIGIN", "https://api.example.com")
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
        monkeypatch.setenv("COOKIE_DOMAIN", ".example.com")
        assert cookie_policy.cookie_policy_warnings() == []

    def test_host_only_cookies_with_a_split_frontend_are_flagged(self, monkeypatch):
        """The real production trap. Everything looks configured; the SPA simply
        cannot read csrf_token, so every mutation 403s."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("API_PUBLIC_ORIGIN", "https://api.example.com")
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
        problems = cookie_policy.cookie_policy_warnings()
        assert any("COOKIE_DOMAIN is unset" in p for p in problems)
        assert any("csrf_token" in p for p in problems)

    def test_a_domain_that_does_not_cover_an_allowed_origin_is_flagged(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("API_PUBLIC_ORIGIN", "https://api.example.com")
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.other.com")
        monkeypatch.setenv("COOKIE_DOMAIN", ".example.com")
        problems = cookie_policy.cookie_policy_warnings()
        assert any("does not cover allowed origin host" in p for p in problems)

    def test_a_domain_that_does_not_cover_the_api_itself_is_flagged(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("API_PUBLIC_ORIGIN", "https://api.other.com")
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
        monkeypatch.setenv("COOKIE_DOMAIN", ".example.com")
        problems = cookie_policy.cookie_policy_warnings()
        assert any("this API's own host" in p for p in problems)

    def test_samesite_none_without_secure_is_flagged_as_degraded(self, monkeypatch):
        monkeypatch.setenv("API_PUBLIC_ORIGIN", "http://localhost:8000")
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:8000")
        monkeypatch.setenv("COOKIE_SAMESITE", "none")
        monkeypatch.setenv("COOKIE_SECURE", "false")
        problems = cookie_policy.cookie_policy_warnings()
        assert any("degraded to 'lax'" in p for p in problems)
        # And the degradation the warning describes is the one that happens.
        assert cookie_policy._resolved_flags()[1] == "lax"

    def test_a_cross_domain_split_on_lax_is_flagged(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("API_PUBLIC_ORIGIN", "https://api.stockassist.ai")
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://stockassist.vercel.app")
        problems = cookie_policy.cookie_policy_warnings()
        assert any("SameSite=" in p for p in problems)

    def test_an_unset_api_origin_says_the_check_was_partial(self, monkeypatch):
        """An empty warning list must never be readable as "verified" when the
        sharpest check could not run."""
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
        problems = cookie_policy.cookie_policy_warnings()
        assert any("partially checked" in p for p in problems)

    def test_domain_matching_follows_the_browser_rule(self):
        covers = cookie_policy._domain_covers
        assert covers(".example.com", "app.example.com") is True
        assert covers("example.com", "example.com") is True
        assert covers(".example.com", "deep.app.example.com") is True
        # A sibling, a parent and a suffix-lookalike are all NOT covered.
        assert covers(".app.example.com", "api.example.com") is False
        assert covers(".app.example.com", "example.com") is False
        assert covers(".example.com", "notexample.com") is False


# =========================================================================== #
# C / E — a revoked session's WebSocket must not keep streaming                #
# =========================================================================== #
class TestSocketTeardownOnRevocation:
    """A WebSocket authenticates exactly once, at the handshake, and then lives
    for as long as the tab stays open. Every other authentication path in the
    platform re-resolves the identity on each request, so revoking a session
    took effect within the access token's 15-minute life — except here, where it
    took effect when the user happened to close the browser. That made the
    socket the one place a logout, a password change, an administrator block and
    an account deletion all failed to reach, while it carried the private
    domains (portfolio, orders, broker events, notifications).
    """

    @pytest.fixture(autouse=True)
    def _clean_manager(self):
        from server import ws_manager

        for store in (
            ws_manager.active,
            ws_manager.user_connections,
            ws_manager.channels,
            ws_manager.session_connections,
        ):
            store.clear()
        yield
        for store in (
            ws_manager.active,
            ws_manager.user_connections,
            ws_manager.channels,
            ws_manager.session_connections,
        ):
            store.clear()

    @staticmethod
    def _auth(user_doc, session_id):
        from server import create_access_token

        return ["stockassist.auth", create_access_token(str(user_doc["_id"]), user_doc["email"], session_id)]

    def test_a_socket_is_tracked_under_its_session(self, client, test_user):
        from server import ws_manager

        with client.websocket_connect("/api/ws", subprotocols=self._auth(test_user, "sess-A")):
            assert "sess-A" in ws_manager.session_connections
            assert len(ws_manager.session_connections["sess-A"]) == 1

    def test_closing_a_session_closes_its_socket(self, client, test_user):
        from server import ws_manager

        with client.websocket_connect("/api/ws", subprotocols=self._auth(test_user, "sess-A")) as ws:
            assert _run(ws_manager.close_session("sess-A")) == 1
            _expect_closed(ws)  # the stream is over, not merely quiet

    def test_closing_another_session_leaves_this_one_alone(self, client, test_user):
        """The precision that makes this safe to call on a single-device logout:
        the user's other devices did not sign out and their sockets are still
        legitimately authorized."""
        from server import ws_manager

        with client.websocket_connect("/api/ws", subprotocols=self._auth(test_user, "sess-A")) as ws:
            assert _run(ws_manager.close_session("sess-B")) == 0
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

    def test_closing_a_user_closes_every_session(self, client, test_user):
        from server import ws_manager

        uid = str(test_user["_id"])
        with client.websocket_connect("/api/ws", subprotocols=self._auth(test_user, "sess-A")) as ws:
            assert _run(ws_manager.close_user(uid)) == 1
            _expect_closed(ws)

    def test_logout_closes_the_socket_for_that_session(self, client, fake_db):
        """End to end. Before this, signing out revoked the refresh family and
        left the private event stream running."""
        r = _register(client, email="wslogout@example.com")
        token = r.json()["token"]
        with client.websocket_connect("/api/ws", subprotocols=["stockassist.auth", token]) as ws:
            assert client.post("/api/auth/logout").status_code == 200
            _expect_closed(ws)

    def test_an_administrator_block_closes_the_socket(self, client, fake_db, test_user):
        """PH3.10 made `blocked` effective everywhere an identity is re-resolved.
        The socket never re-resolves, so this was the gap — and blocking is what
        an operator reaches for during an active incident."""
        from server import ws_manager

        uid = str(test_user["_id"])
        with client.websocket_connect("/api/ws", subprotocols=self._auth(test_user, "sess-A")) as ws:
            _run(fake_db.users.update_one({"_id": test_user["_id"]}, {"$set": {"blocked": True}}))
            assert _run(ws_manager.close_user(uid)) == 1
            _expect_closed(ws)

    def test_tracking_is_emptied_on_a_clean_disconnect(self, client, test_user):
        """A retention leak here would be the PH3.6 bug again, in a new dict."""
        from server import ws_manager

        with client.websocket_connect("/api/ws", subprotocols=self._auth(test_user, "sess-A")):
            pass
        assert "sess-A" not in ws_manager.session_connections

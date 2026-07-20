"""PH1.6 — JWT lifecycle & session security tests.

Covers the full acceptance matrix for the sprint:

* token issuance carries the hardened claim set (iat/jti/aud/iss/ver/sid);
* refresh rotates (new refresh token every use, old one dead);
* replay of a rotated refresh token is detected and revokes the whole family;
* expired / revoked / malformed / wrong-issuer / wrong-audience / wrong-type /
  wrong-version / bad-signature tokens are all rejected;
* logout revokes the current session; logout-all revokes every session;
* password_changed_at invalidates outstanding tokens;
* the existing register → login → me → refresh → logout lifecycle still works.

Hermetic: pure-crypto assertions run against ``security.jwt`` directly, the
``SessionStore`` runs against the in-memory ``FakeDB`` via ``asyncio.run`` (no
pytest-asyncio needed), and endpoint tests use the shared ``client``/``fake_db``
fixtures. No live server, Mongo, or Redis required.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from security import jwt as jwtmod
from security.sessions import (
    SessionStore, ROTATED, REUSE_DETECTED, REVOKED, EXPIRED, NOT_FOUND,
)
from tests._fakedb import FakeDB

REG_PASSWORD = "S3cure!Passw0rd"


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
def _mint(sub="user-1", *, ttype="access", ttl=900, aud=None, iss=None,
          ver=None, sid="sess-1", jti="jti-1", email="u@example.com", iat=None):
    """Encode a token with arbitrary claims against the app's real secret — used
    to forge the specific malformations each rejection test needs."""
    now = datetime.now(timezone.utc)
    claims = {
        "sub": sub,
        "type": ttype,
        "sid": sid,
        "jti": jti,
        "iat": iat or now,
        "exp": now + timedelta(seconds=ttl),
        "aud": aud if aud is not None else jwtmod.audience(),
        "iss": iss if iss is not None else jwtmod.issuer(),
        "ver": ver if ver is not None else jwtmod.TOKEN_VERSION,
    }
    if ttype == "access":
        claims["email"] = email
    return pyjwt.encode(claims, jwtmod.get_secret(), algorithm=jwtmod.JWT_ALGORITHM)


def _run(coro):
    """Drive one async coroutine to completion on a fresh loop (no
    pytest-asyncio dependency) — each call is self-contained."""
    return asyncio.run(coro)


def _register(client, email="jwt@example.com"):
    return client.post("/api/auth/register", json={
        "name": "JWT Tester", "email": email, "password": REG_PASSWORD,
    })


# --------------------------------------------------------------------------- #
# security.jwt — issuance carries the hardened claim set                         #
# --------------------------------------------------------------------------- #
class TestTokenClaims:
    def test_access_token_has_full_claim_set(self):
        tok = jwtmod.create_access_token("u1", "a@b.com", "sess-9")
        claims = jwtmod.decode_token(tok, expected_type="access")
        for key in ("sub", "email", "type", "sid", "jti", "iat", "exp", "aud", "iss", "ver"):
            assert key in claims, f"missing {key}"
        assert claims["sub"] == "u1"
        assert claims["sid"] == "sess-9"
        assert claims["type"] == "access"
        assert claims["ver"] == jwtmod.TOKEN_VERSION
        assert claims["aud"] == jwtmod.audience()
        assert claims["iss"] == jwtmod.issuer()

    def test_refresh_token_has_no_email_and_correct_type(self):
        tok = jwtmod.create_refresh_token("u1", "sess-9", "jti-xyz")
        claims = jwtmod.decode_token(tok, expected_type="refresh")
        assert claims["type"] == "refresh"
        assert claims["jti"] == "jti-xyz"
        assert "email" not in claims

    def test_each_access_token_gets_a_unique_jti(self):
        a = jwtmod.decode_token(jwtmod.create_access_token("u1", "a@b.com", "s"), expected_type="access")
        b = jwtmod.decode_token(jwtmod.create_access_token("u1", "a@b.com", "s"), expected_type="access")
        assert a["jti"] != b["jti"]

    def test_access_token_shortlived_by_default(self):
        claims = jwtmod.decode_token(jwtmod.create_access_token("u1", "a@b.com", "s"), expected_type="access")
        lifetime = claims["exp"] - claims["iat"]
        assert lifetime == jwtmod.DEFAULT_ACCESS_TTL_SECONDS == 15 * 60


# --------------------------------------------------------------------------- #
# security.jwt — verification rejects every malformation                        #
# --------------------------------------------------------------------------- #
class TestTokenRejection:
    def test_expired_rejected(self):
        with pytest.raises(jwtmod.TokenExpired):
            jwtmod.decode_token(_mint(ttl=-10), expected_type="access")

    def test_wrong_audience_rejected(self):
        with pytest.raises(jwtmod.TokenInvalid):
            jwtmod.decode_token(_mint(aud="some-other-app"), expected_type="access")

    def test_wrong_issuer_rejected(self):
        with pytest.raises(jwtmod.TokenInvalid):
            jwtmod.decode_token(_mint(iss="evil-issuer"), expected_type="access")

    def test_bad_signature_rejected(self):
        now = datetime.now(timezone.utc)
        forged = pyjwt.encode(
            {"sub": "u1", "type": "access", "sid": "s", "jti": "j",
             "iat": now, "exp": now + timedelta(minutes=5),
             "aud": jwtmod.audience(), "iss": jwtmod.issuer(), "ver": jwtmod.TOKEN_VERSION,
             "email": "e@x.com"},
            "not-the-real-secret", algorithm="HS256",
        )
        with pytest.raises(jwtmod.TokenInvalid):
            jwtmod.decode_token(forged, expected_type="access")

    def test_wrong_type_rejected(self):
        # A refresh token must not pass as an access token, and vice-versa.
        with pytest.raises(jwtmod.TokenInvalid):
            jwtmod.decode_token(_mint(ttype="refresh"), expected_type="access")
        with pytest.raises(jwtmod.TokenInvalid):
            jwtmod.decode_token(_mint(ttype="access"), expected_type="refresh")

    def test_stale_version_rejected(self):
        with pytest.raises(jwtmod.TokenInvalid):
            jwtmod.decode_token(_mint(ver=jwtmod.TOKEN_VERSION + 99), expected_type="access")

    def test_missing_required_claim_rejected(self):
        # A pre-PH1.6-shaped token (no jti/aud/iss/ver) must fail closed.
        now = datetime.now(timezone.utc)
        legacy = pyjwt.encode(
            {"sub": "u1", "type": "access", "exp": now + timedelta(hours=1)},
            jwtmod.get_secret(), algorithm="HS256",
        )
        with pytest.raises(jwtmod.TokenInvalid):
            jwtmod.decode_token(legacy, expected_type="access")

    def test_garbage_rejected(self):
        with pytest.raises(jwtmod.TokenInvalid):
            jwtmod.decode_token("not.a.jwt", expected_type="access")


class TestPasswordChangedAt:
    def test_token_issued_before_cutoff_is_stale(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        claims = jwtmod.decode_token(_mint(iat=past), expected_type="access")
        # Cutoff = now → a token minted an hour ago is stale.
        assert jwtmod.token_issued_before(claims, datetime.now(timezone.utc)) is True

    def test_token_issued_after_cutoff_is_fresh(self):
        claims = jwtmod.decode_token(_mint(), expected_type="access")
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        assert jwtmod.token_issued_before(claims, past) is False

    def test_no_cutoff_is_never_stale(self):
        claims = jwtmod.decode_token(_mint(), expected_type="access")
        assert jwtmod.token_issued_before(claims, None) is False

    def test_iso_string_cutoff_supported(self):
        claims = jwtmod.decode_token(_mint(), expected_type="access")
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        assert jwtmod.token_issued_before(claims, future) is True


# --------------------------------------------------------------------------- #
# security.sessions — rotation, reuse detection, revocation                     #
# --------------------------------------------------------------------------- #
class TestSessionStore:
    def test_create_persists_family(self):
        db = FakeDB()
        sid = _run(SessionStore(db).create("user-1", "jti-0", user_agent="UA", ip="1.2.3.4"))
        doc = _run(SessionStore(db).get(sid))
        assert doc["user_id"] == "user-1"
        assert doc["current_jti"] == "jti-0"
        assert doc["revoked"] is False
        assert doc["user_agent"] == "UA" and doc["ip"] == "1.2.3.4"

    def test_rotate_with_current_jti_succeeds(self):
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        res = _run(store.rotate(sid, "jti-0", "jti-1"))
        assert res.outcome == ROTATED and res.ok
        doc = _run(store.get(sid))
        assert doc["current_jti"] == "jti-1"
        assert doc["refresh_count"] == 1

    def test_replay_of_rotated_token_revokes_family(self):
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        _run(store.rotate(sid, "jti-0", "jti-1"))          # legit rotation
        res = _run(store.rotate(sid, "jti-0", "jti-2"))    # replay the dead token
        assert res.outcome == REUSE_DETECTED
        # The whole family is now revoked — even the currently-valid token dies.
        follow = _run(store.rotate(sid, "jti-1", "jti-3"))
        assert follow.outcome == REVOKED
        assert _run(store.is_active(sid)) is False

    def test_rotate_unknown_session(self):
        db = FakeDB()
        assert _run(SessionStore(db).rotate("nope", "j", "j2")).outcome == NOT_FOUND

    def test_rotate_expired_session(self):
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        # Force the family past its absolute expiry.
        _run(db.sessions.update_one(
            {"session_id": sid},
            {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
        ))
        assert _run(store.rotate(sid, "jti-0", "jti-1")).outcome == EXPIRED

    def test_revoke_one(self):
        db = FakeDB()
        store = SessionStore(db)
        sid = _run(store.create("u", "jti-0"))
        assert _run(store.revoke(sid)) is True
        assert _run(store.is_active(sid)) is False
        # Idempotent — a second revoke reports nothing changed.
        assert _run(store.revoke(sid)) is False

    def test_revoke_all_for_user(self):
        db = FakeDB()
        store = SessionStore(db)
        s1 = _run(store.create("user-A", "j1"))
        s2 = _run(store.create("user-A", "j2"))
        s_other = _run(store.create("user-B", "j3"))
        count = _run(store.revoke_all_for_user("user-A"))
        assert count == 2
        assert _run(store.is_active(s1)) is False
        assert _run(store.is_active(s2)) is False
        assert _run(store.is_active(s_other)) is True   # other users untouched

    def test_list_for_user_active_only(self):
        db = FakeDB()
        store = SessionStore(db)
        s1 = _run(store.create("u", "j1"))
        s2 = _run(store.create("u", "j2"))
        _run(store.revoke(s2))
        active = _run(store.list_for_user("u", active_only=True))
        assert {s["session_id"] for s in active} == {s1}
        alls = _run(store.list_for_user("u", active_only=False))
        assert len(alls) == 2


# --------------------------------------------------------------------------- #
# Endpoint integration — the full lifecycle over HTTP                            #
# --------------------------------------------------------------------------- #
class TestAuthEndpoints:
    def test_login_issues_valid_short_lived_access_token(self, client, fake_db):
        _register(client, email="login@example.com")
        r = client.post("/api/auth/login",
                        json={"email": "login@example.com", "password": REG_PASSWORD})
        assert r.status_code == 200
        claims = jwtmod.decode_token(r.json()["token"], expected_type="access")
        assert claims["sub"]
        assert claims["exp"] - claims["iat"] == jwtmod.DEFAULT_ACCESS_TTL_SECONDS
        # A session/family was opened for this login and its id is the token sid.
        assert any(s["session_id"] == claims["sid"] for s in fake_db.sessions.docs)

    def test_refresh_rotates_refresh_token(self, client, fake_db):
        _register(client, email="rot@example.com")
        old_refresh = client.cookies.get("refresh_token")
        r = client.post("/api/auth/refresh")
        assert r.status_code == 200
        new_refresh = client.cookies.get("refresh_token")
        assert new_refresh and new_refresh != old_refresh

    def test_replayed_refresh_token_rejected_and_family_revoked(self, client, fake_db):
        _register(client, email="replay@example.com")
        old_refresh = client.cookies.get("refresh_token")
        # Legit rotation → jar now holds a new refresh token.
        assert client.post("/api/auth/refresh").status_code == 200
        # Replay the ORIGINAL (now rotated-out) refresh token.
        replay = client.post("/api/auth/refresh", cookies={"refresh_token": old_refresh})
        assert replay.status_code == 401
        # Reuse detection revoked the family — the current token is dead too.
        followup = client.post("/api/auth/refresh")
        assert followup.status_code == 401

    def test_logout_revokes_current_session(self, client, fake_db):
        _register(client, email="lo@example.com")
        good_refresh = client.cookies.get("refresh_token")
        assert client.post("/api/auth/logout").status_code == 200
        # The refresh token captured before logout can no longer be rotated.
        r = client.post("/api/auth/refresh", cookies={"refresh_token": good_refresh})
        assert r.status_code == 401
        assert all(s["revoked"] for s in fake_db.sessions.docs)

    def test_logout_all_revokes_every_session(self, client, fake_db):
        _register(client, email="all@example.com")
        token = client.post("/api/auth/login",
                            json={"email": "all@example.com", "password": REG_PASSWORD}).json()["token"]
        # Two live families now exist (register + login).
        assert sum(1 for s in fake_db.sessions.docs if not s["revoked"]) == 2
        r = client.post("/api/auth/logout-all", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["sessions_revoked"] == 2
        assert all(s["revoked"] for s in fake_db.sessions.docs)

    def test_password_changed_at_invalidates_access_token(self, client, fake_db):
        _register(client, email="pwd@example.com")
        token = client.post("/api/auth/login",
                            json={"email": "pwd@example.com", "password": REG_PASSWORD}).json()["token"]
        # Baseline: token works.
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        # Simulate a password change / forced global logout in the future.
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        for u in fake_db.users.docs:
            if u["email"] == "pwd@example.com":
                u["password_changed_at"] = future
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    def test_expired_access_token_rejected(self, client, fake_db, test_user):
        expired = _mint(sub=str(test_user["_id"]), ttl=-5)
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
        assert r.status_code == 401

    def test_wrong_audience_token_rejected_at_endpoint(self, client, fake_db, test_user):
        bad = _mint(sub=str(test_user["_id"]), aud="another-app")
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401

    def test_refresh_token_cannot_be_used_as_access(self, client, fake_db, test_user):
        refresh = _mint(sub=str(test_user["_id"]), ttype="refresh")
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {refresh}"})
        assert r.status_code == 401

    def test_full_lifecycle_still_works(self, client, fake_db):
        # Regression: register → me → refresh → logout, end to end.
        reg = _register(client, email="life@example.com")
        assert reg.status_code == 200
        token = reg.json()["token"]
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert client.post("/api/auth/refresh").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200

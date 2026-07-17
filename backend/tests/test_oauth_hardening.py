"""PH1.2 — Google OAuth production hardening tests.

Covers the hardened OAuth flow end to end, hermetically (in-process against the
FakeDB, no live Google and no Mongo):

  - `GET /api/auth/google/login-url` plants a CSRF state cookie and returns the
    Google authorization URL; fail-closed when unconfigured; redirect_uri allowlist.
  - `POST /api/auth/google/session` enforces the state (CSRF), verifies the
    id_token, rejects unverified emails, allowlists redirect_uri, and links or
    creates accounts safely without ever producing duplicates.

Google's network calls are substituted at the two module-level seams
`server._exchange_google_code` and `server._verify_google_id_token`.
"""
from urllib.parse import urlparse, parse_qs

import httpx
import pytest
from bson import ObjectId
from fastapi import HTTPException

import server
from services import cache


REDIRECT_URI = "http://localhost:3000/auth/google/callback"


def _verified_claims(email="newuser@example.com", sub="google-sub-1", name="New User"):
    return {
        "iss": "https://accounts.google.com",
        "aud": "test-client-id",
        "email": email,
        "email_verified": True,
        "sub": sub,
        "name": name,
        "picture": "https://example.com/p.png",
    }


def _patch_google(monkeypatch, claims, token_data=None):
    async def fake_exchange(code, redirect_uri, client_id, client_secret):
        return token_data if token_data is not None else {"id_token": "header.payload.sig"}

    monkeypatch.setattr(server, "_exchange_google_code", fake_exchange)
    monkeypatch.setattr(server, "_verify_google_id_token", lambda tok, cid: claims)


def _start_flow(client):
    """Call login-url, returning the CSRF state (cookie is stored on the client)."""
    r = client.get("/api/auth/google/login-url", params={"redirect_uri": REDIRECT_URI})
    assert r.status_code == 200
    return parse_qs(urlparse(r.json()["url"]).query)["state"][0]


@pytest.fixture(autouse=True)
def force_memory_state_store(monkeypatch):
    """Force the OAuth state store onto the in-memory cache path (no Redis) and
    start each test with an empty store, so state records never leak between
    tests and no live Redis is required."""
    monkeypatch.setenv("REDIS_URL", "")
    cache._memory.clear()
    yield
    cache._memory.clear()


@pytest.fixture
def google_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setenv("APP_ENV", "development")


# --------------------------------------------------------------------------- #
# login-url initiation
# --------------------------------------------------------------------------- #
class TestLoginUrl:
    def test_returns_url_and_sets_state_cookie(self, client, google_env):
        r = client.get("/api/auth/google/login-url", params={"redirect_uri": REDIRECT_URI})
        assert r.status_code == 200
        url = r.json()["url"]
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        q = parse_qs(urlparse(url).query)
        assert q["client_id"] == ["test-client-id"]
        assert q["response_type"] == ["code"]
        assert q["redirect_uri"] == [REDIRECT_URI]
        assert q["state"][0]  # non-empty
        assert "g_oauth_state" in r.headers.get("set-cookie", "")

    def test_state_is_random_per_call(self, client, google_env):
        s1 = _start_flow(client)
        s2 = _start_flow(client)
        assert s1 != s2

    def test_unconfigured_returns_401(self, client, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        r = client.get("/api/auth/google/login-url", params={"redirect_uri": REDIRECT_URI})
        assert r.status_code == 401
        assert "not configured" in r.json()["detail"]

    def test_rejects_unlisted_redirect_uri(self, client, google_env):
        r = client.get("/api/auth/google/login-url",
                       params={"redirect_uri": "https://evil.example.com/auth/google/callback"})
        assert r.status_code == 400


# --------------------------------------------------------------------------- #
# session — CSRF state enforcement
# --------------------------------------------------------------------------- #
class TestStateEnforcement:
    def test_missing_state_rejected(self, client, fake_db, google_env):
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "redirect_uri": REDIRECT_URI})
        assert r.status_code == 400
        assert "state" in r.json()["detail"].lower()
        assert len(fake_db.users.docs) == 0

    def test_state_without_cookie_rejected(self, client, fake_db, google_env):
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": "forged", "redirect_uri": REDIRECT_URI})
        assert r.status_code == 400
        assert "Invalid OAuth state" in r.json()["detail"]
        assert len(fake_db.users.docs) == 0

    def test_mismatched_state_rejected(self, client, fake_db, google_env, monkeypatch):
        _patch_google(monkeypatch, _verified_claims())
        _start_flow(client)  # sets a real cookie
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": "not-the-cookie", "redirect_uri": REDIRECT_URI})
        assert r.status_code == 400
        assert "Invalid OAuth state" in r.json()["detail"]
        assert len(fake_db.users.docs) == 0


# --------------------------------------------------------------------------- #
# session — identity verification
# --------------------------------------------------------------------------- #
class TestIdentityVerification:
    def test_unverified_email_rejected(self, client, fake_db, google_env, monkeypatch):
        claims = _verified_claims()
        claims["email_verified"] = False
        _patch_google(monkeypatch, claims)
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 401
        assert "not verified" in r.json()["detail"]
        assert len(fake_db.users.docs) == 0

    def test_invalid_id_token_rejected(self, client, fake_db, google_env, monkeypatch):
        def boom(tok, cid):
            raise ValueError("bad signature")
        monkeypatch.setattr(server, "_verify_google_id_token", boom)

        async def fake_exchange(*a, **k):
            return {"id_token": "x.y.z"}
        monkeypatch.setattr(server, "_exchange_google_code", fake_exchange)
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 401
        assert "Invalid Google identity token" in r.json()["detail"]
        assert len(fake_db.users.docs) == 0

    def test_bad_issuer_rejected(self, client, fake_db, google_env, monkeypatch):
        claims = _verified_claims()
        claims["iss"] = "https://accounts.evil.com"
        _patch_google(monkeypatch, claims)
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 401
        assert "issuer" in r.json()["detail"]
        assert len(fake_db.users.docs) == 0

    def test_missing_id_token_rejected(self, client, fake_db, google_env, monkeypatch):
        _patch_google(monkeypatch, _verified_claims(), token_data={"access_token": "at"})
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 401
        assert "identity token" in r.json()["detail"]
        assert len(fake_db.users.docs) == 0


# --------------------------------------------------------------------------- #
# session — redirect_uri allowlist and upstream failures
# --------------------------------------------------------------------------- #
class TestRedirectAndUpstream:
    def test_bad_redirect_uri_rejected(self, client, fake_db, google_env, monkeypatch):
        _patch_google(monkeypatch, _verified_claims())
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state,
                              "redirect_uri": "https://evil.example.com/auth/google/callback"})
        assert r.status_code == 400
        assert "redirect_uri" in r.json()["detail"]
        assert len(fake_db.users.docs) == 0

    def test_token_exchange_failure_rejected(self, client, fake_db, google_env, monkeypatch):
        async def fail(*a, **k):
            raise HTTPException(status_code=401, detail="Google token exchange failed")
        monkeypatch.setattr(server, "_exchange_google_code", fail)
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 401
        assert len(fake_db.users.docs) == 0

    def test_google_network_error_returns_502(self, client, fake_db, google_env, monkeypatch):
        async def down(*a, **k):
            raise httpx.RequestError("connection failed")
        monkeypatch.setattr(server, "_exchange_google_code", down)
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 502


# --------------------------------------------------------------------------- #
# session — account resolution / linking
# --------------------------------------------------------------------------- #
class TestAccountResolution:
    def test_new_google_user_created(self, client, fake_db, google_env, monkeypatch):
        _patch_google(monkeypatch, _verified_claims(email="brand@new.com", sub="sub-new"))
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == "brand@new.com"
        assert body["auth_provider"] == "google"
        assert "token" in body
        assert "access_token" in r.headers.get("set-cookie", "")
        assert len(fake_db.users.docs) == 1
        created = fake_db.users.docs[0]
        assert created["auth_provider"] == "google"
        assert created["google_sub"] == "sub-new"
        assert created["password_hash"] == ""

    def test_existing_password_user_is_linked_not_overwritten(self, client, fake_db, google_env, monkeypatch):
        # A pre-existing email/password account with the same (verified) email.
        client.post("/api/auth/register", json={
            "name": "Pwd User", "email": "shared@example.com", "password": "S3cure!Passw0rd",
        })
        assert len(fake_db.users.docs) == 1
        original = dict(fake_db.users.docs[0])

        _patch_google(monkeypatch, _verified_claims(email="shared@example.com", sub="sub-link"))
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 200
        assert len(fake_db.users.docs) == 1  # linked, not duplicated
        linked = fake_db.users.docs[0]
        assert linked["google_sub"] == "sub-link"
        # Password login must keep working: hash and provider preserved.
        assert linked["password_hash"] == original["password_hash"]
        assert linked["password_hash"] != ""

        # And email/password login still succeeds after linking.
        login = client.post("/api/auth/login", json={
            "email": "shared@example.com", "password": "S3cure!Passw0rd",
        })
        assert login.status_code == 200

    def test_repeated_google_login_creates_no_duplicate(self, client, fake_db, google_env, monkeypatch):
        _patch_google(monkeypatch, _verified_claims(email="repeat@example.com", sub="sub-r"))
        for _ in range(3):
            state = _start_flow(client)
            r = client.post("/api/auth/google/session",
                            json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
            assert r.status_code == 200
        assert sum(1 for u in fake_db.users.docs if u.get("email") == "repeat@example.com") == 1

    def test_existing_role_preserved_on_login(self, client, fake_db, google_env, monkeypatch):
        from bson import ObjectId
        fake_db.users.docs.append({
            "_id": ObjectId(), "email": "admin@example.com", "name": "Admin",
            "role": "admin", "auth_provider": "google", "capital": 250000,
        })
        _patch_google(monkeypatch, _verified_claims(email="admin@example.com", sub="sub-admin"))
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 200
        assert r.json()["role"] == "admin"
        assert r.json()["capital"] == 250000


# --------------------------------------------------------------------------- #
# session — state replay / expiry (server-side single-use record)
# --------------------------------------------------------------------------- #
class TestStateReplayAndExpiry:
    def test_state_is_single_use(self, client, fake_db, google_env, monkeypatch):
        _patch_google(monkeypatch, _verified_claims(email="once@example.com", sub="sub-once"))
        state = _start_flow(client)
        r1 = client.post("/api/auth/google/session",
                         json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r1.status_code == 200
        # Same browser resubmits: the cookie was burned on first use.
        r2 = client.post("/api/auth/google/session",
                         json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r2.status_code == 400
        assert sum(1 for u in fake_db.users.docs if u.get("email") == "once@example.com") == 1

    def test_consumed_record_blocks_forged_cookie_replay(self, client, fake_db, google_env, monkeypatch):
        # The server-side record defeats replay even if an attacker re-plants the
        # cookie: once consumed, the record is gone regardless of the cookie.
        _patch_google(monkeypatch, _verified_claims(email="rep@example.com", sub="sub-rep"))
        state = _start_flow(client)
        assert client.post("/api/auth/google/session",
                           json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI}).status_code == 200
        client.cookies.set("g_oauth_state", state, domain="testserver")
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 400
        assert "Invalid OAuth state" in r.json()["detail"]

    def test_expired_or_absent_record_rejected(self, client, fake_db, google_env):
        # Cookie matches the submitted state, but no server-side record exists
        # (expired via TTL or never issued) → rejected.
        client.cookies.set("g_oauth_state", "orphan-state-value", domain="testserver")
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": "orphan-state-value", "redirect_uri": REDIRECT_URI})
        assert r.status_code == 400
        assert "Invalid OAuth state" in r.json()["detail"]
        assert len(fake_db.users.docs) == 0


# --------------------------------------------------------------------------- #
# session — Google `sub` as primary identity
# --------------------------------------------------------------------------- #
class TestSubIdentity:
    def test_sub_match_ignores_email_change(self, client, fake_db, google_env, monkeypatch):
        uid = ObjectId()
        fake_db.users.docs.append({
            "_id": uid, "email": "old@example.com", "name": "Old Name",
            "role": "user", "auth_provider": "google", "google_sub": "sub-stable",
            "capital": 100000,
        })
        # Same sub, different (verified) email — the Google display email changed.
        _patch_google(monkeypatch, _verified_claims(email="new@example.com", sub="sub-stable", name="New Name"))
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 200
        assert r.json()["id"] == str(uid)          # same account
        assert len(fake_db.users.docs) == 1        # no duplicate
        assert fake_db.users.docs[0]["email"] == "old@example.com"  # email not mutated

    def test_sub_conflict_is_rejected(self, client, fake_db, google_env, monkeypatch):
        # An account already bound to a different Google sub must not be re-linked.
        fake_db.users.docs.append({
            "_id": ObjectId(), "email": "victim@example.com", "name": "Victim",
            "role": "user", "auth_provider": "google", "google_sub": "sub-A",
        })
        _patch_google(monkeypatch, _verified_claims(email="victim@example.com", sub="sub-B"))
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 401
        assert "cannot be linked" in r.json()["detail"]
        assert fake_db.users.docs[0]["google_sub"] == "sub-A"  # unchanged
        assert len(fake_db.users.docs) == 1


# --------------------------------------------------------------------------- #
# session — audience wiring + audit logging
# --------------------------------------------------------------------------- #
class TestAudienceWiring:
    def test_id_token_verified_with_client_id_audience(self, client, fake_db, google_env, monkeypatch):
        captured = {}

        def fake_verify(tok, cid):
            captured["audience"] = cid
            return _verified_claims(email="aud@example.com", sub="sub-aud")

        async def fake_exchange(*a, **k):
            return {"id_token": "x.y.z"}

        monkeypatch.setattr(server, "_verify_google_id_token", fake_verify)
        monkeypatch.setattr(server, "_exchange_google_code", fake_exchange)
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 200
        assert captured["audience"] == "test-client-id"


class TestAuditLogging:
    def test_success_is_audited_without_secrets(self, client, fake_db, google_env, monkeypatch):
        _patch_google(monkeypatch, _verified_claims(email="audit@example.com", sub="sub-audit"))
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "DISTINCTCODE123", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 200
        logs = fake_db.security_audit_logs.docs
        success = [l for l in logs if l["event"] == "oauth_login_success"]
        assert success and success[0]["email"] == "audit@example.com"
        assert success[0]["details"]["new_account"] is True
        # The authorization code and the state value must never be persisted.
        blob = str(logs)
        assert "DISTINCTCODE123" not in blob
        assert state not in blob

    def test_invalid_state_is_audited(self, client, fake_db, google_env):
        client.post("/api/auth/google/session",
                    json={"code": "abc", "state": "forged", "redirect_uri": REDIRECT_URI})
        logs = fake_db.security_audit_logs.docs
        assert any(l["event"] == "oauth_login_failure" and l["reason"] == "invalid_state" for l in logs)

    def test_unverified_email_is_audited(self, client, fake_db, google_env, monkeypatch):
        claims = _verified_claims(email="unv@example.com", sub="sub-unv")
        claims["email_verified"] = False
        _patch_google(monkeypatch, claims)
        state = _start_flow(client)
        client.post("/api/auth/google/session",
                    json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        logs = fake_db.security_audit_logs.docs
        assert any(l["reason"] == "unverified_email" and l["email"] == "unv@example.com" for l in logs)

    def test_linking_is_audited(self, client, fake_db, google_env, monkeypatch):
        client.post("/api/auth/register", json={
            "name": "P", "email": "linkaudit@example.com", "password": "S3cure!Passw0rd",
        })
        _patch_google(monkeypatch, _verified_claims(email="linkaudit@example.com", sub="sub-la"))
        state = _start_flow(client)
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 200
        success = [l for l in fake_db.security_audit_logs.docs if l["event"] == "oauth_login_success"]
        assert success and success[0]["details"]["linked"] is True

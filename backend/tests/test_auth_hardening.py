"""PH1.1 — Authentication Security Hardening tests.

Asserts the removed backdoors stay removed (B1 auto-login, B2 OAuth
demo/mock/legacy paths) and that the legitimate email/password lifecycle
(register → login → me → refresh → logout) still works. Hermetic: runs
in-process against the FakeDB, no live server or Mongo required.
"""
import pytest

import server
from server import app


class TestAutoLoginRemoved:
    def test_auto_login_route_absent_from_app(self):
        paths = {getattr(r, "path", "") for r in app.routes}
        assert not any("auto-login" in p or "auto_login" in p for p in paths)

    def test_auto_login_returns_404(self, client, fake_db):
        r = client.get("/api/auth/auto-login")
        assert r.status_code == 404

    def test_auto_login_sets_no_cookies(self, client, fake_db):
        r = client.get("/api/auth/auto-login")
        assert "access_token" not in r.headers.get("set-cookie", "")


class TestOAuthFailsClosed:
    @pytest.fixture(autouse=True)
    def _no_google_creds(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

    def test_mock_code_rejected(self, client, fake_db):
        r = client.post("/api/auth/google/session", json={"code": "mock-code-for-testing"})
        assert r.status_code == 401
        assert len(fake_db.users.docs) == 0

    def test_unconfigured_oauth_rejected(self, client, fake_db):
        r = client.post("/api/auth/google/session", json={"code": "any-real-looking-code"})
        assert r.status_code == 401
        assert "not configured" in r.json()["detail"]

    def test_legacy_session_id_rejected(self, client, fake_db):
        r = client.post("/api/auth/google/session", json={"session_id": "legacy-session-abc"})
        assert r.status_code == 400
        assert len(fake_db.users.docs) == 0

    def test_empty_body_rejected(self, client, fake_db):
        r = client.post("/api/auth/google/session", json={})
        assert r.status_code == 400

    def test_no_demo_user_ever_created(self, client, fake_db):
        for payload in ({"code": "mock-code-for-testing"}, {"session_id": "x"}, {}):
            client.post("/api/auth/google/session", json=payload)
        assert not any(
            u.get("email") == "demo-user@alphapartner.com" for u in fake_db.users.docs
        )


class TestLegitimateAuthStillWorks:
    def test_register_login_me_refresh_logout(self, client, fake_db):
        # Register
        r = client.post("/api/auth/register", json={
            "name": "Hardening Tester",
            "email": "hardening@example.com",
            "password": "S3cure!Passw0rd",
        })
        assert r.status_code == 200
        assert r.json()["email"] == "hardening@example.com"
        assert "token" in r.json()
        assert "access_token" in r.headers.get("set-cookie", "")

        # Login
        r = client.post("/api/auth/login", json={
            "email": "hardening@example.com", "password": "S3cure!Passw0rd",
        })
        assert r.status_code == 200
        token = r.json()["token"]

        # Me (bearer)
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["email"] == "hardening@example.com"
        assert "password_hash" not in r.json()

        # Refresh (cookie set by login)
        r = client.post("/api/auth/refresh")
        assert r.status_code == 200

        # Logout clears cookies
        r = client.post("/api/auth/logout")
        assert r.status_code == 200

    def test_wrong_password_rejected(self, client, fake_db):
        client.post("/api/auth/register", json={
            "name": "T", "email": "t2@example.com", "password": "RightPass1!",
        })
        r = client.post("/api/auth/login", json={"email": "t2@example.com", "password": "wrong"})
        assert r.status_code == 401

    def test_protected_route_requires_auth(self, client, fake_db):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

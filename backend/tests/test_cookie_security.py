"""PH1.3 — Authentication cookie security tests.

Verifies the centralized cookie policy (`security.cookies`) end to end and at
the unit level, hermetically (in-process against the FakeDB — no Mongo, no live
Google):

  * Login / register issue hardened session cookies (HttpOnly, SameSite, Path,
    Max-Age; Secure driven by env and forced in production).
  * Logout clears every authentication cookie.
  * Refresh re-issues the access cookie through the same hardened policy.
  * The Google OAuth state cookie shares the unified posture and is scoped to
    /api/auth, HttpOnly, and burned after use.
  * Session fixation: login/register always overwrite the session cookies with
    freshly minted tokens.

Set-Cookie flags are asserted against the raw response headers (not the cookie
jar), which is the authoritative wire representation and is independent of the
test client's http scheme.
"""
from urllib.parse import parse_qs, urlparse

import pytest

import server
from security import cookies as cookie_policy


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
def _set_cookie_headers(response):
    """All Set-Cookie header values on a response (httpx combines duplicates)."""
    return response.headers.get_list("set-cookie")


def _cookie_header(response, name):
    """The single Set-Cookie header for `name`, or None."""
    for h in _set_cookie_headers(response):
        if h.split("=", 1)[0].strip() == name:
            return h
    return None


def _attrs(cookie_header):
    """Parse a Set-Cookie header into {lowercased-attr: value|True}."""
    parts = [p.strip() for p in cookie_header.split(";")]
    attrs = {}
    for p in parts[1:]:  # skip the name=value pair
        if "=" in p:
            k, v = p.split("=", 1)
            attrs[k.strip().lower()] = v.strip()
        else:
            attrs[p.strip().lower()] = True
    return attrs


def _register(client, email="cookie_user@example.com", password="S3cure!Passw0rd"):
    return client.post("/api/auth/register",
                       json={"name": "Cookie User", "email": email, "password": password})


@pytest.fixture
def dev_env(monkeypatch):
    """Deterministic development cookie environment: not production, Secure off,
    SameSite default, no domain."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.delenv("COOKIE_SAMESITE", raising=False)
    monkeypatch.delenv("COOKIE_DOMAIN", raising=False)


# --------------------------------------------------------------------------- #
# Unit — policy resolution                                                      #
# --------------------------------------------------------------------------- #
class TestPolicyResolution:
    def test_secure_forced_true_in_production(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("COOKIE_SECURE", "false")  # must be ignored in prod
        assert cookie_policy.cookie_secure() is True

    def test_secure_defaults_false_in_dev(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.delenv("COOKIE_SECURE", raising=False)
        assert cookie_policy.cookie_secure() is False

    def test_secure_env_override_in_dev(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("COOKIE_SECURE", "true")
        assert cookie_policy.cookie_secure() is True

    def test_samesite_default_lax(self, monkeypatch):
        monkeypatch.delenv("COOKIE_SAMESITE", raising=False)
        assert cookie_policy.cookie_samesite() == "lax"

    def test_samesite_invalid_falls_back_to_lax(self, monkeypatch):
        monkeypatch.setenv("COOKIE_SAMESITE", "banana")
        assert cookie_policy.cookie_samesite() == "lax"

    def test_none_without_secure_degrades_to_lax(self, monkeypatch):
        # SameSite=None without Secure is dropped by browsers → degrade to Lax.
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("COOKIE_SECURE", "false")
        monkeypatch.setenv("COOKIE_SAMESITE", "none")
        secure, samesite = cookie_policy._resolved_flags()
        assert secure is False
        assert samesite == "lax"

    def test_none_with_secure_is_honored(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")  # forces Secure
        monkeypatch.setenv("COOKIE_SAMESITE", "none")
        secure, samesite = cookie_policy._resolved_flags()
        assert secure is True
        assert samesite == "none"

    def test_domain_unset_is_none(self, monkeypatch):
        monkeypatch.delenv("COOKIE_DOMAIN", raising=False)
        assert cookie_policy.cookie_domain() is None

    def test_domain_configured(self, monkeypatch):
        monkeypatch.setenv("COOKIE_DOMAIN", ".stockassist.ai")
        assert cookie_policy.cookie_domain() == ".stockassist.ai"


# --------------------------------------------------------------------------- #
# Login / register — hardened session cookies                                   #
# --------------------------------------------------------------------------- #
class TestSessionCookieFlags:
    def test_register_sets_both_hardened_cookies(self, client, fake_db, dev_env):
        r = _register(client)
        assert r.status_code == 200
        for name, max_age in (("access_token", "86400"), ("refresh_token", "604800")):
            header = _cookie_header(r, name)
            assert header is not None, f"{name} not set"
            attrs = _attrs(header)
            assert attrs.get("httponly") is True
            assert attrs.get("samesite") == "lax"
            assert attrs.get("path") == "/"
            assert attrs.get("max-age") == max_age
            assert "secure" not in attrs  # dev, COOKIE_SECURE=false

    def test_login_sets_both_hardened_cookies(self, client, fake_db, dev_env):
        _register(client, email="login@example.com")
        client.cookies.clear()
        r = client.post("/api/auth/login",
                        json={"email": "login@example.com", "password": "S3cure!Passw0rd"})
        assert r.status_code == 200
        for name in ("access_token", "refresh_token"):
            attrs = _attrs(_cookie_header(r, name))
            assert attrs.get("httponly") is True
            assert attrs.get("samesite") == "lax"
            assert attrs.get("path") == "/"

    def test_production_forces_secure(self, client, fake_db, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("COOKIE_SECURE", "false")  # ignored in prod
        r = _register(client, email="prod@example.com")
        assert r.status_code == 200
        for name in ("access_token", "refresh_token"):
            attrs = _attrs(_cookie_header(r, name))
            assert attrs.get("secure") is True
            assert attrs.get("httponly") is True

    def test_dev_secure_override(self, client, fake_db, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("COOKIE_SECURE", "true")
        r = _register(client, email="devsecure@example.com")
        for name in ("access_token", "refresh_token"):
            assert _attrs(_cookie_header(r, name)).get("secure") is True

    def test_configured_domain_applied(self, client, fake_db, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("COOKIE_SECURE", "false")
        monkeypatch.setenv("COOKIE_DOMAIN", ".stockassist.ai")
        r = _register(client, email="domain@example.com")
        attrs = _attrs(_cookie_header(r, "access_token"))
        assert attrs.get("domain") == ".stockassist.ai"


# --------------------------------------------------------------------------- #
# Logout — clears every authentication cookie                                   #
# --------------------------------------------------------------------------- #
class TestLogout:
    def test_logout_clears_both_cookies(self, client, fake_db, dev_env):
        _register(client, email="logout@example.com")
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        for name in ("access_token", "refresh_token"):
            header = _cookie_header(r, name)
            assert header is not None, f"{name} not cleared"
            attrs = _attrs(header)
            # Deletion is Max-Age=0 (and an expired Expires) with matching Path.
            assert attrs.get("max-age") == "0"
            assert attrs.get("path") == "/"

    def test_logout_delete_matches_set_path(self, client, fake_db, dev_env):
        # The delete Path must equal the set Path or the browser keeps the cookie.
        r_set = _register(client, email="pathmatch@example.com")
        r_clear = client.post("/api/auth/logout")
        for name in ("access_token", "refresh_token"):
            assert _attrs(_cookie_header(r_set, name)).get("path") == \
                   _attrs(_cookie_header(r_clear, name)).get("path")


# --------------------------------------------------------------------------- #
# Refresh — re-issues access cookie through the hardened policy                  #
# --------------------------------------------------------------------------- #
class TestRefresh:
    def test_refresh_reissues_hardened_access_cookie(self, client, fake_db, dev_env):
        _register(client, email="refresh@example.com")  # sets refresh_token in jar
        r = client.post("/api/auth/refresh")
        assert r.status_code == 200
        attrs = _attrs(_cookie_header(r, "access_token"))
        assert attrs.get("httponly") is True
        assert attrs.get("samesite") == "lax"
        assert attrs.get("path") == "/"
        assert attrs.get("max-age") == "86400"
        # Refresh must not rotate/re-set the refresh token here (PH1.6 owns that).
        assert _cookie_header(r, "refresh_token") is None

    def test_refresh_without_cookie_rejected(self, client, fake_db, dev_env):
        r = client.post("/api/auth/refresh")
        assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Session fixation — fresh cookies on every authentication                       #
# --------------------------------------------------------------------------- #
class TestSessionFixation:
    def test_login_overwrites_preexisting_cookie(self, client, fake_db, dev_env):
        _register(client, email="fixation@example.com")
        # Attacker-planted stale value in the browser.
        client.cookies.set("access_token", "attacker-fixed-value")
        r = client.post("/api/auth/login",
                        json={"email": "fixation@example.com", "password": "S3cure!Passw0rd"})
        assert r.status_code == 200
        header = _cookie_header(r, "access_token")
        assert header is not None
        # A fresh token is issued, overwriting the planted value at the same path.
        assert "attacker-fixed-value" not in header
        assert _attrs(header).get("path") == "/"


# --------------------------------------------------------------------------- #
# Google OAuth state cookie — unified posture + scope + burn-after-use           #
# --------------------------------------------------------------------------- #
REDIRECT_URI = "http://localhost:3000/auth/google/callback"


@pytest.fixture
def google_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("REDIS_URL", "")  # in-memory state store


class TestOAuthStateCookie:
    def test_state_cookie_hardened_and_scoped(self, client, google_env):
        r = client.get("/api/auth/google/login-url", params={"redirect_uri": REDIRECT_URI})
        assert r.status_code == 200
        header = _cookie_header(r, "g_oauth_state")
        assert header is not None
        attrs = _attrs(header)
        assert attrs.get("httponly") is True
        assert attrs.get("samesite") == "lax"      # never Strict — survives Google redirect
        assert attrs.get("path") == "/api/auth"    # scoped to the auth routes
        assert attrs.get("max-age") == "600"
        assert "secure" not in attrs               # dev

    def test_state_cookie_secure_in_production(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
        monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.setenv("APP_ENV", "production")
        r = client.get("/api/auth/google/login-url", params={"redirect_uri": REDIRECT_URI})
        assert r.status_code == 200
        assert _attrs(_cookie_header(r, "g_oauth_state")).get("secure") is True

    def test_state_cookie_never_strict(self, client, monkeypatch):
        # Even if an operator sets SameSite=Strict globally, the OAuth state
        # cookie must degrade to Lax so it survives the redirect back from Google.
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
        monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("COOKIE_SECURE", "false")
        monkeypatch.setenv("COOKIE_SAMESITE", "strict")
        r = client.get("/api/auth/google/login-url", params={"redirect_uri": REDIRECT_URI})
        assert _attrs(_cookie_header(r, "g_oauth_state")).get("samesite") == "lax"

    def test_state_cookie_burned_after_exchange(self, client, fake_db, google_env, monkeypatch):
        # A successful session exchange must clear the state cookie (Max-Age=0).
        claims = {
            "iss": "https://accounts.google.com", "aud": "test-client-id",
            "email": "burn@example.com", "email_verified": True, "sub": "sub-burn",
            "name": "Burn", "picture": "",
        }

        async def fake_exchange(*a, **k):
            return {"id_token": "x.y.z"}

        monkeypatch.setattr(server, "_exchange_google_code", fake_exchange)
        monkeypatch.setattr(server, "_verify_google_id_token", lambda tok, cid: claims)

        r0 = client.get("/api/auth/google/login-url", params={"redirect_uri": REDIRECT_URI})
        state = parse_qs(urlparse(r0.json()["url"]).query)["state"][0]
        r = client.post("/api/auth/google/session",
                        json={"code": "abc", "state": state, "redirect_uri": REDIRECT_URI})
        assert r.status_code == 200
        # Session cookies issued...
        assert _cookie_header(r, "access_token") is not None
        # ...and the state cookie cleared.
        cleared = _cookie_header(r, "g_oauth_state")
        assert cleared is not None
        attrs = _attrs(cleared)
        assert attrs.get("max-age") == "0"
        assert attrs.get("path") == "/api/auth"

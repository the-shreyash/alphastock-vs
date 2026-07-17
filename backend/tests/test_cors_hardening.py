"""PH1.4 — CORS hardening tests.

Verifies the centralized CORS policy (`security.cors`) at two levels,
hermetically (no network, no Mongo):

  * Unit — origin allowlist resolution: the canonical `CORS_ALLOWED_ORIGINS`
    variable, backward-compatible legacy inputs, trailing-slash normalization,
    de-duplication, development defaults, production fail-closed behavior, and
    the guarantee that a wildcard `*` can never enter the allowlist.
  * Integration — the assembled Starlette `CORSMiddleware` on a throwaway app,
    asserting real preflight/simple-request wire behavior: allowed origins are
    reflected with credentials, unknown origins are rejected, and methods and
    request headers are enforced rather than blanket-reflected.

The middleware resolves its allowlist from the environment at attach time, so
each integration test builds a fresh app via `apply_cors` under a controlled
environment — the same "assert the real wire representation" approach used by
the PH1.3 cookie tests.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from security import cors as cors_policy
from security.cors import (
    apply_cors,
    allowed_origins,
    cors_kwargs,
    CORS_ALLOWED_ORIGINS_ENV,
    LEGACY_CORS_ORIGINS_ENV,
    LEGACY_FRONTEND_URL_ENV,
    DEFAULT_DEV_ORIGINS,
    ALLOWED_METHODS,
    ALLOWED_HEADERS,
    EXPOSE_HEADERS,
)

ORIGIN_ENV_VARS = (
    CORS_ALLOWED_ORIGINS_ENV,
    LEGACY_CORS_ORIGINS_ENV,
    LEGACY_FRONTEND_URL_ENV,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
@pytest.fixture
def clean_env(monkeypatch):
    """Start every test from a known-empty origin/environment baseline so a
    developer's ambient env cannot leak into assertions."""
    for var in ORIGIN_ENV_VARS + ("APP_ENV",):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _build_client(monkeypatch, origins=None, app_env=None):
    """A fresh app with the hardened CORS middleware attached under a controlled
    environment.

    The allowlist is resolved when `apply_cors` runs, so env must be set first.
    """
    for var in ORIGIN_ENV_VARS + ("APP_ENV",):
        monkeypatch.delenv(var, raising=False)
    if app_env is not None:
        monkeypatch.setenv("APP_ENV", app_env)
    if origins is not None:
        monkeypatch.setenv(CORS_ALLOWED_ORIGINS_ENV, origins)

    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.post("/api/ping")
    def ping_post():
        return {"ok": True}

    apply_cors(app)
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Unit — origin allowlist resolution                                            #
# --------------------------------------------------------------------------- #
class TestAllowedOrigins:
    def test_canonical_variable_parsed(self, clean_env):
        clean_env.setenv(
            CORS_ALLOWED_ORIGINS_ENV,
            "https://app.stockassist.ai,https://www.stockassist.ai",
        )
        assert allowed_origins() == [
            "https://app.stockassist.ai",
            "https://www.stockassist.ai",
        ]

    def test_trailing_slash_normalized(self, clean_env):
        # Origin headers never carry a trailing slash, so it must be stripped or
        # the allowlist entry would never match.
        clean_env.setenv(CORS_ALLOWED_ORIGINS_ENV, "https://app.stockassist.ai/")
        assert allowed_origins() == ["https://app.stockassist.ai"]

    def test_whitespace_trimmed_and_empties_dropped(self, clean_env):
        clean_env.setenv(
            CORS_ALLOWED_ORIGINS_ENV,
            "  https://a.example.com , , https://b.example.com ",
        )
        assert allowed_origins() == ["https://a.example.com", "https://b.example.com"]

    def test_wildcard_stripped_from_canonical(self, clean_env):
        # In production a stripped wildcard leaves an empty (fail-closed) list;
        # no dev defaults are assumed there.
        clean_env.setenv("APP_ENV", "production")
        clean_env.setenv(CORS_ALLOWED_ORIGINS_ENV, "*")
        assert allowed_origins() == []

    def test_wildcard_stripped_but_valid_origins_kept(self, clean_env):
        clean_env.setenv(CORS_ALLOWED_ORIGINS_ENV, "*,https://app.stockassist.ai")
        assert allowed_origins() == ["https://app.stockassist.ai"]

    def test_wildcard_stripped_from_legacy(self, clean_env):
        clean_env.setenv(LEGACY_CORS_ORIGINS_ENV, "*")
        clean_env.setenv("APP_ENV", "production")
        assert allowed_origins() == []

    def test_legacy_cors_origins_honored(self, clean_env):
        clean_env.setenv(LEGACY_CORS_ORIGINS_ENV, "https://legacy.example.com")
        assert allowed_origins() == ["https://legacy.example.com"]

    def test_legacy_frontend_url_honored(self, clean_env):
        clean_env.setenv(LEGACY_FRONTEND_URL_ENV, "https://frontend.example.com")
        assert allowed_origins() == ["https://frontend.example.com"]

    def test_sources_merged_and_deduplicated(self, clean_env):
        clean_env.setenv(CORS_ALLOWED_ORIGINS_ENV, "https://a.example.com")
        clean_env.setenv(LEGACY_CORS_ORIGINS_ENV, "https://a.example.com,https://b.example.com")
        clean_env.setenv(LEGACY_FRONTEND_URL_ENV, "https://a.example.com")
        # First-seen order preserved, no duplicates.
        assert allowed_origins() == ["https://a.example.com", "https://b.example.com"]

    def test_dev_defaults_when_unconfigured(self, clean_env):
        clean_env.setenv("APP_ENV", "development")
        assert allowed_origins() == list(DEFAULT_DEV_ORIGINS)

    def test_dev_defaults_absent_when_configured(self, clean_env):
        clean_env.setenv("APP_ENV", "development")
        clean_env.setenv(CORS_ALLOWED_ORIGINS_ENV, "https://only.example.com")
        assert allowed_origins() == ["https://only.example.com"]

    def test_production_fails_closed_when_unconfigured(self, clean_env):
        # No dev defaults assumed in production — empty allowlist rejects all.
        clean_env.setenv("APP_ENV", "production")
        assert allowed_origins() == []

    def test_production_uses_configured_origins(self, clean_env):
        clean_env.setenv("APP_ENV", "production")
        clean_env.setenv(CORS_ALLOWED_ORIGINS_ENV, "https://app.stockassist.ai")
        assert allowed_origins() == ["https://app.stockassist.ai"]


# --------------------------------------------------------------------------- #
# Unit — assembled middleware kwargs                                            #
# --------------------------------------------------------------------------- #
class TestCorsKwargs:
    def test_never_contains_wildcard(self, clean_env):
        clean_env.setenv(CORS_ALLOWED_ORIGINS_ENV, "*")
        assert "*" not in cors_kwargs()["allow_origins"]

    def test_credentials_enabled(self, clean_env):
        assert cors_kwargs()["allow_credentials"] is True

    def test_methods_restricted_not_wildcard(self, clean_env):
        methods = cors_kwargs()["allow_methods"]
        assert "*" not in methods
        assert methods == ALLOWED_METHODS
        assert "OPTIONS" in methods  # preflight

    def test_headers_restricted_not_wildcard(self, clean_env):
        headers = cors_kwargs()["allow_headers"]
        assert "*" not in headers
        assert headers == ALLOWED_HEADERS

    def test_no_response_headers_exposed(self, clean_env):
        assert cors_kwargs()["expose_headers"] == EXPOSE_HEADERS == []

    def test_credentials_never_paired_with_wildcard_origin(self, clean_env):
        # The core invariant: credentials + wildcard is forbidden by the Fetch
        # standard. Even asked for a wildcard, the assembled config is safe.
        clean_env.setenv(CORS_ALLOWED_ORIGINS_ENV, "*")
        kwargs = cors_kwargs()
        assert kwargs["allow_credentials"] is True
        assert "*" not in kwargs["allow_origins"]


# --------------------------------------------------------------------------- #
# Integration — real preflight / simple-request wire behavior                   #
# --------------------------------------------------------------------------- #
class TestPreflight:
    def test_allowed_origin_preflight_succeeds(self, monkeypatch):
        client = _build_client(monkeypatch, origins="https://app.stockassist.ai")
        resp = client.options(
            "/api/ping",
            headers={
                "Origin": "https://app.stockassist.ai",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "https://app.stockassist.ai"
        assert resp.headers["access-control-allow-credentials"] == "true"

    def test_unknown_origin_preflight_rejected(self, monkeypatch):
        client = _build_client(monkeypatch, origins="https://app.stockassist.ai")
        resp = client.options(
            "/api/ping",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        # Starlette returns 400 "Disallowed CORS origin" and no ACAO header.
        assert resp.status_code == 400
        assert "access-control-allow-origin" not in resp.headers

    def test_disallowed_method_preflight_rejected(self, monkeypatch):
        client = _build_client(monkeypatch, origins="https://app.stockassist.ai")
        resp = client.options(
            "/api/ping",
            headers={
                "Origin": "https://app.stockassist.ai",
                "Access-Control-Request-Method": "TRACE",  # not in ALLOWED_METHODS
            },
        )
        assert resp.status_code == 400

    def test_disallowed_request_header_preflight_rejected(self, monkeypatch):
        client = _build_client(monkeypatch, origins="https://app.stockassist.ai")
        resp = client.options(
            "/api/ping",
            headers={
                "Origin": "https://app.stockassist.ai",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Evil-Header",
            },
        )
        assert resp.status_code == 400

    def test_allowed_request_header_preflight_succeeds(self, monkeypatch):
        client = _build_client(monkeypatch, origins="https://app.stockassist.ai")
        resp = client.options(
            "/api/ping",
            headers={
                "Origin": "https://app.stockassist.ai",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "https://app.stockassist.ai"


class TestSimpleRequest:
    def test_allowed_origin_gets_cors_headers(self, monkeypatch):
        client = _build_client(monkeypatch, origins="https://app.stockassist.ai")
        resp = client.get("/api/ping", headers={"Origin": "https://app.stockassist.ai"})
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "https://app.stockassist.ai"
        assert resp.headers["access-control-allow-credentials"] == "true"

    def test_unknown_origin_gets_no_cors_headers(self, monkeypatch):
        # The request still executes server-side, but without ACAO the browser
        # blocks the response — credentials never leak to an untrusted origin.
        client = _build_client(monkeypatch, origins="https://app.stockassist.ai")
        resp = client.get("/api/ping", headers={"Origin": "https://evil.example.com"})
        assert resp.status_code == 200
        assert "access-control-allow-origin" not in resp.headers

    def test_wildcard_origin_header_never_returned(self, monkeypatch):
        client = _build_client(monkeypatch, origins="https://app.stockassist.ai")
        resp = client.get("/api/ping", headers={"Origin": "https://app.stockassist.ai"})
        assert resp.headers.get("access-control-allow-origin") != "*"


class TestLocalDevelopment:
    def test_localhost_works_out_of_the_box(self, monkeypatch):
        # No origin env configured, development → dev defaults apply so the
        # local frontend keeps working with zero configuration.
        client = _build_client(monkeypatch, app_env="development")
        resp = client.get("/api/ping", headers={"Origin": "http://localhost:3000"})
        assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert resp.headers["access-control-allow-credentials"] == "true"

    def test_vite_dev_origin_allowed(self, monkeypatch):
        client = _build_client(monkeypatch, app_env="development")
        resp = client.get("/api/ping", headers={"Origin": "http://localhost:5173"})
        assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"

    def test_production_unconfigured_rejects_localhost(self, monkeypatch):
        client = _build_client(monkeypatch, app_env="production")
        resp = client.get("/api/ping", headers={"Origin": "http://localhost:3000"})
        assert "access-control-allow-origin" not in resp.headers

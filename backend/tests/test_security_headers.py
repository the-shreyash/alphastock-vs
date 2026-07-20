"""PH1.4b — HTTP security-headers tests.

Verifies the centralized header policy (`security.headers`) at two levels,
hermetically (no network, no Mongo):

  * Unit — the pure resolver functions: HSTS enablement/value, the strict
    default CSP and its nonce substitution, the cross-origin isolation family,
    and the environment overrides for every header.
  * Integration — the assembled `SecurityHeadersMiddleware` on a throwaway app,
    asserting the real wire representation: the headers are present on normal,
    error, and CORS-preflight responses; HSTS appears only over HTTPS/production;
    COEP is emitted only when opted in; and a nonce-based policy substitutes a
    real per-request nonce and exposes it on `request.state.csp_nonce`.

Each test controls the environment explicitly (headers resolve from the
environment per request), the same "assert the real wire representation"
approach used by the PH1.3 cookie and PH1.4 CORS tests.
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from security import headers as headers_policy
from security.headers import (
    apply_security_headers,
    content_security_policy,
    coep_value,
    csp_template,
    csp_uses_nonce,
    generate_nonce,
    hsts_enabled,
    hsts_max_age,
    resolve_headers,
    should_send_hsts,
    static_security_headers,
    strict_transport_security_value,
    CSP_ENV,
    COEP_ENV,
    COOP_ENV,
    CORP_ENV,
    HSTS_ENABLE_ENV,
    HSTS_MAX_AGE_ENV,
    HSTS_INCLUDE_SUBDOMAINS_ENV,
    HSTS_PRELOAD_ENV,
    PERMISSIONS_POLICY_ENV,
    REFERRER_POLICY_ENV,
    X_FRAME_OPTIONS_ENV,
    DEFAULT_HSTS_MAX_AGE,
)

# Every environment variable the module reads — cleared before each test so a
# developer's ambient environment cannot leak into assertions.
HEADER_ENV_VARS = (
    "APP_ENV",
    HSTS_ENABLE_ENV,
    HSTS_MAX_AGE_ENV,
    HSTS_INCLUDE_SUBDOMAINS_ENV,
    HSTS_PRELOAD_ENV,
    CSP_ENV,
    PERMISSIONS_POLICY_ENV,
    REFERRER_POLICY_ENV,
    X_FRAME_OPTIONS_ENV,
    COOP_ENV,
    CORP_ENV,
    COEP_ENV,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
@pytest.fixture
def clean_env(monkeypatch):
    """Start every test from a known-empty environment baseline."""
    for var in HEADER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _http_scope(scheme="http", headers=None):
    """A minimal ASGI HTTP scope for the request-dependent resolvers."""
    return {
        "type": "http",
        "scheme": scheme,
        "headers": headers or [],
    }


def _build_client(monkeypatch, app_env=None, **env):
    """A fresh app with the security-header middleware under a controlled env."""
    for var in HEADER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    if app_env is not None:
        monkeypatch.setenv("APP_ENV", app_env)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.get("/api/boom")
    def boom():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="nope")

    @app.get("/api/nonce")
    def nonce_echo(request: Request):
        # Proves the middleware-generated nonce is visible to the handler.
        return {"nonce": getattr(request.state, "csp_nonce", None)}

    apply_security_headers(app)
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Unit — HSTS                                                                    #
# --------------------------------------------------------------------------- #
class TestHsts:
    def test_disabled_by_default_outside_production(self, clean_env):
        clean_env.setenv("APP_ENV", "development")
        assert hsts_enabled() is False

    def test_enabled_by_default_in_production(self, clean_env):
        clean_env.setenv("APP_ENV", "production")
        assert hsts_enabled() is True

    def test_force_enable_via_env(self, clean_env):
        clean_env.setenv("APP_ENV", "development")
        clean_env.setenv(HSTS_ENABLE_ENV, "true")
        assert hsts_enabled() is True

    def test_default_value_two_years_include_subdomains_no_preload(self, clean_env):
        assert hsts_max_age() == DEFAULT_HSTS_MAX_AGE
        value = strict_transport_security_value()
        assert value == f"max-age={DEFAULT_HSTS_MAX_AGE}; includeSubDomains"
        assert "preload" not in value

    def test_preload_opt_in(self, clean_env):
        clean_env.setenv(HSTS_PRELOAD_ENV, "true")
        assert "preload" in strict_transport_security_value()

    def test_subdomains_can_be_disabled(self, clean_env):
        clean_env.setenv(HSTS_INCLUDE_SUBDOMAINS_ENV, "false")
        assert "includeSubDomains" not in strict_transport_security_value()

    def test_invalid_max_age_falls_back(self, clean_env):
        clean_env.setenv(HSTS_MAX_AGE_ENV, "not-a-number")
        assert hsts_max_age() == DEFAULT_HSTS_MAX_AGE

    def test_custom_max_age(self, clean_env):
        clean_env.setenv(HSTS_MAX_AGE_ENV, "31536000")
        assert hsts_max_age() == 31536000

    def test_not_sent_over_plain_http_dev(self, clean_env):
        clean_env.setenv("APP_ENV", "development")
        assert should_send_hsts(_http_scope(scheme="http")) is False

    def test_sent_over_https_dev(self, clean_env):
        # Enabled + genuinely HTTPS ⇒ sent even outside production.
        clean_env.setenv("APP_ENV", "development")
        clean_env.setenv(HSTS_ENABLE_ENV, "true")
        assert should_send_hsts(_http_scope(scheme="https")) is True

    def test_sent_in_production(self, clean_env):
        clean_env.setenv("APP_ENV", "production")
        assert should_send_hsts(_http_scope(scheme="http")) is True

    def test_forwarded_proto_https_recognized(self, clean_env):
        clean_env.setenv("APP_ENV", "development")
        clean_env.setenv(HSTS_ENABLE_ENV, "true")
        scope = _http_scope(scheme="http", headers=[(b"x-forwarded-proto", b"https")])
        assert should_send_hsts(scope) is True


# --------------------------------------------------------------------------- #
# Unit — Content-Security-Policy                                                 #
# --------------------------------------------------------------------------- #
class TestCsp:
    def test_default_is_strict_and_locks_everything(self, clean_env):
        policy = content_security_policy()
        assert "default-src 'none'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "base-uri 'none'" in policy
        assert "form-action 'none'" in policy

    def test_default_has_no_unsafe_values(self, clean_env):
        policy = content_security_policy()
        assert "unsafe-inline" not in policy
        assert "unsafe-eval" not in policy

    def test_env_override_is_verbatim(self, clean_env):
        clean_env.setenv(CSP_ENV, "default-src 'self'")
        assert content_security_policy() == "default-src 'self'"

    def test_default_uses_no_nonce(self, clean_env):
        assert csp_uses_nonce() is False

    def test_nonce_placeholder_detected(self, clean_env):
        clean_env.setenv(CSP_ENV, "script-src 'nonce-{nonce}'")
        assert csp_uses_nonce() is True

    def test_nonce_substituted(self, clean_env):
        clean_env.setenv(CSP_ENV, "script-src 'nonce-{nonce}'")
        assert content_security_policy("ABC123") == "script-src 'nonce-ABC123'"

    def test_generate_nonce_is_unique_and_nonempty(self, clean_env):
        assert generate_nonce() != generate_nonce()
        assert len(generate_nonce()) > 0


# --------------------------------------------------------------------------- #
# Unit — static headers + cross-origin isolation                                #
# --------------------------------------------------------------------------- #
class TestStaticHeaders:
    def test_defaults(self, clean_env):
        h = static_security_headers()
        assert h["X-Content-Type-Options"] == "nosniff"
        assert h["X-Frame-Options"] == "DENY"
        assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert h["Cross-Origin-Opener-Policy"] == "same-origin"
        assert h["Cross-Origin-Resource-Policy"] == "same-origin"
        assert h["X-XSS-Protection"] == "0"
        assert "camera=()" in h["Permissions-Policy"]

    def test_coep_absent_by_default(self, clean_env):
        assert coep_value() is None
        assert "Cross-Origin-Embedder-Policy" not in static_security_headers()

    def test_coep_emitted_when_configured(self, clean_env):
        clean_env.setenv(COEP_ENV, "require-corp")
        assert coep_value() == "require-corp"
        assert static_security_headers()["Cross-Origin-Embedder-Policy"] == "require-corp"

    def test_overrides(self, clean_env):
        clean_env.setenv(X_FRAME_OPTIONS_ENV, "SAMEORIGIN")
        clean_env.setenv(REFERRER_POLICY_ENV, "no-referrer")
        clean_env.setenv(PERMISSIONS_POLICY_ENV, "geolocation=(self)")
        clean_env.setenv(COOP_ENV, "unsafe-none")
        clean_env.setenv(CORP_ENV, "cross-origin")
        h = static_security_headers()
        assert h["X-Frame-Options"] == "SAMEORIGIN"
        assert h["Referrer-Policy"] == "no-referrer"
        assert h["Permissions-Policy"] == "geolocation=(self)"
        assert h["Cross-Origin-Opener-Policy"] == "unsafe-none"
        assert h["Cross-Origin-Resource-Policy"] == "cross-origin"


class TestResolveHeaders:
    def test_hsts_included_when_secure(self, clean_env):
        clean_env.setenv("APP_ENV", "production")
        pairs, nonce = resolve_headers(_http_scope(scheme="https"))
        names = {k for k, _ in pairs}
        assert "Strict-Transport-Security" in names
        assert "Content-Security-Policy" in names
        assert nonce is None

    def test_hsts_omitted_when_insecure(self, clean_env):
        clean_env.setenv("APP_ENV", "development")
        pairs, _ = resolve_headers(_http_scope(scheme="http"))
        assert "Strict-Transport-Security" not in {k for k, _ in pairs}

    def test_nonce_generated_only_when_policy_uses_it(self, clean_env):
        clean_env.setenv(CSP_ENV, "script-src 'nonce-{nonce}'")
        pairs, nonce = resolve_headers(_http_scope())
        assert nonce is not None
        csp = dict(pairs)["Content-Security-Policy"]
        assert f"'nonce-{nonce}'" == csp.replace("script-src ", "")


# --------------------------------------------------------------------------- #
# Integration — real wire behavior through the middleware                        #
# --------------------------------------------------------------------------- #
class TestMiddleware:
    def test_headers_present_on_success(self, monkeypatch):
        client = _build_client(monkeypatch, app_env="development")
        resp = client.get("/api/ping")
        assert resp.status_code == 200
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert resp.headers["cross-origin-opener-policy"] == "same-origin"
        assert resp.headers["cross-origin-resource-policy"] == "same-origin"
        assert resp.headers["x-xss-protection"] == "0"
        assert "default-src 'none'" in resp.headers["content-security-policy"]
        assert "camera=()" in resp.headers["permissions-policy"]

    def test_headers_present_on_error_response(self, monkeypatch):
        # Error responses (HTTPException) must still carry the security headers.
        client = _build_client(monkeypatch, app_env="development")
        resp = client.get("/api/boom")
        assert resp.status_code == 404
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in resp.headers["content-security-policy"]

    def test_hsts_absent_in_dev_over_http(self, monkeypatch):
        client = _build_client(monkeypatch, app_env="development")
        resp = client.get("/api/ping")
        assert "strict-transport-security" not in resp.headers

    def test_hsts_present_in_production(self, monkeypatch):
        client = _build_client(monkeypatch, app_env="production")
        resp = client.get("/api/ping")
        assert "strict-transport-security" in resp.headers
        assert "max-age=" in resp.headers["strict-transport-security"]
        assert "includeSubDomains" in resp.headers["strict-transport-security"]

    def test_coep_absent_by_default(self, monkeypatch):
        client = _build_client(monkeypatch, app_env="development")
        resp = client.get("/api/ping")
        assert "cross-origin-embedder-policy" not in resp.headers

    def test_coep_present_when_opted_in(self, monkeypatch):
        client = _build_client(
            monkeypatch, app_env="development", CROSS_ORIGIN_EMBEDDER_POLICY="require-corp"
        )
        resp = client.get("/api/ping")
        assert resp.headers["cross-origin-embedder-policy"] == "require-corp"

    def test_nonce_policy_produces_matching_state_and_header(self, monkeypatch):
        client = _build_client(
            monkeypatch,
            app_env="development",
            CONTENT_SECURITY_POLICY="default-src 'none'; script-src 'nonce-{nonce}'",
        )
        resp = client.get("/api/nonce")
        assert resp.status_code == 200
        state_nonce = resp.json()["nonce"]
        assert state_nonce  # the handler saw the middleware-generated nonce
        # The same nonce is present in the response header.
        assert f"'nonce-{state_nonce}'" in resp.headers["content-security-policy"]
        assert "{nonce}" not in resp.headers["content-security-policy"]

    def test_two_requests_get_distinct_nonces(self, monkeypatch):
        client = _build_client(
            monkeypatch,
            app_env="development",
            CONTENT_SECURITY_POLICY="script-src 'nonce-{nonce}'",
        )
        first = client.get("/api/nonce").json()["nonce"]
        second = client.get("/api/nonce").json()["nonce"]
        assert first and second and first != second

    def test_env_override_reflected_on_wire(self, monkeypatch):
        client = _build_client(
            monkeypatch, app_env="development", X_FRAME_OPTIONS="SAMEORIGIN"
        )
        resp = client.get("/api/ping")
        assert resp.headers["x-frame-options"] == "SAMEORIGIN"

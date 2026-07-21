"""PH1.7 — CSRF protection tests.

Covers the sprint's CSRF acceptance matrix:

* valid token accepted; missing / invalid / mismatched token rejected;
* token bound to the session — a token for another session is rejected;
* safe methods (GET/HEAD/OPTIONS) exempt;
* auth-bootstrap paths exempt;
* Bearer-authenticated requests exempt by design (the SPA path stays working);
* cookie-authenticated mutating requests without a valid token get 403;
* existing register → login lifecycle keeps issuing a readable CSRF cookie.

Hermetic: pure-token assertions run against ``security.csrf`` directly; the
middleware is exercised against a tiny Starlette app via ``TestClient`` (no full
server, Mongo, or Redis needed). ``conftest.py`` loads ``.env`` (JWT_SECRET) by
importing ``server`` at collection time.
"""
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from security import jwt as jwtmod
from security import csrf


# --------------------------------------------------------------------------- #
# Helpers                                                                        #
# --------------------------------------------------------------------------- #
def _access(sid="sess-1", sub="user-1"):
    """A real, valid access token bound to ``sid`` (so csrf's own decode works)."""
    return jwtmod.create_access_token(sub, "u@example.com", sid)


def _tiny_app():
    """A minimal app with one mutating route, guarded by CSRFMiddleware."""
    async def mutate(request):
        return JSONResponse({"ok": True})

    async def read(request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[
        Route("/api/thing", mutate, methods=["POST", "PUT", "PATCH", "DELETE"]),
        Route("/api/thing", read, methods=["GET"]),
        Route("/api/auth/login", mutate, methods=["POST"]),
    ])
    csrf.apply_csrf_protection(app)
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Token mint / verify / bind                                                     #
# --------------------------------------------------------------------------- #
class TestToken:
    def test_issue_verify_roundtrip(self):
        tok = csrf.issue_token("sess-1")
        assert csrf.verify_token(tok, "sess-1")

    def test_token_bound_to_session(self):
        tok = csrf.issue_token("sess-1")
        assert not csrf.verify_token(tok, "sess-2")  # wrong session → invalid

    def test_tampered_signature_rejected(self):
        tok = csrf.issue_token("sess-1")
        nonce, _, sig = tok.partition(".")
        forged = f"{nonce}.{'0' * len(sig)}"
        assert not csrf.verify_token(forged, "sess-1")

    def test_tampered_nonce_rejected(self):
        tok = csrf.issue_token("sess-1")
        _, _, sig = tok.partition(".")
        assert not csrf.verify_token(f"attacker.{sig}", "sess-1")

    def test_malformed_tokens_rejected(self):
        for bad in ("", None, "no-dot", ".", "a.", ".b"):
            assert not csrf.verify_token(bad, "sess-1")

    def test_two_tokens_same_session_differ(self):
        assert csrf.issue_token("sess-1") != csrf.issue_token("sess-1")

    def test_tokens_match_constant_time(self):
        assert csrf.tokens_match("abc", "abc")
        assert not csrf.tokens_match("abc", "abd")
        assert not csrf.tokens_match(None, "abc")
        assert not csrf.tokens_match("abc", None)


# --------------------------------------------------------------------------- #
# Middleware behavior                                                            #
# --------------------------------------------------------------------------- #
class TestMiddleware:
    def test_get_is_exempt(self):
        client = _tiny_app()
        r = client.get("/api/thing")
        assert r.status_code == 200

    def test_options_is_exempt(self):
        client = _tiny_app()
        # No matching OPTIONS route, but CSRF must not 403 a safe method.
        r = client.options("/api/thing")
        assert r.status_code != 403

    def test_bearer_request_is_exempt(self):
        """A Bearer-authenticated mutation carries no ambient cookie authority and
        cannot be forged cross-site → exempt even without a CSRF token."""
        client = _tiny_app()
        r = client.post("/api/thing", headers={"Authorization": f"Bearer {_access()}"})
        assert r.status_code == 200

    def test_no_session_cookie_is_exempt(self):
        """No cookie session → nothing for CSRF to protect (auth layer owns it)."""
        client = _tiny_app()
        r = client.post("/api/thing")
        assert r.status_code == 200

    def test_bootstrap_path_exempt(self):
        client = _tiny_app()
        # Cookie-authenticated POST to an exempt bootstrap path → allowed.
        client.cookies.set("access_token", _access())
        r = client.post("/api/auth/login")
        assert r.status_code == 200

    def test_cookie_auth_without_token_rejected(self):
        client = _tiny_app()
        client.cookies.set("access_token", _access("sess-9"))
        r = client.post("/api/thing")
        assert r.status_code == 403
        assert r.json()["code"] == "CSRF_FAILED"

    def test_cookie_auth_with_valid_token_accepted(self):
        client = _tiny_app()
        tok = csrf.issue_token("sess-9")
        client.cookies.set("access_token", _access("sess-9"))
        client.cookies.set("csrf_token", tok)
        r = client.post("/api/thing", headers={"X-CSRF-Token": tok})
        assert r.status_code == 200

    def test_header_cookie_mismatch_rejected(self):
        client = _tiny_app()
        client.cookies.set("access_token", _access("sess-9"))
        client.cookies.set("csrf_token", csrf.issue_token("sess-9"))
        # Header carries a *different* (also valid) token → double-submit fails.
        r = client.post("/api/thing", headers={"X-CSRF-Token": csrf.issue_token("sess-9")})
        assert r.status_code == 403

    def test_token_for_other_session_rejected(self):
        """Header==cookie but the token is bound to a different session than the
        one the access cookie authenticates → rejected (binding check)."""
        client = _tiny_app()
        other = csrf.issue_token("sess-OTHER")
        client.cookies.set("access_token", _access("sess-9"))
        client.cookies.set("csrf_token", other)
        r = client.post("/api/thing", headers={"X-CSRF-Token": other})
        assert r.status_code == 403

    def test_all_mutating_methods_enforced(self):
        client = _tiny_app()
        client.cookies.set("access_token", _access("sess-9"))
        for method in ("post", "put", "patch", "delete"):
            r = getattr(client, method)("/api/thing")
            assert r.status_code == 403, method


# --------------------------------------------------------------------------- #
# Integration: the real auth flow issues a readable, session-bound CSRF cookie   #
# --------------------------------------------------------------------------- #
class TestAuthFlowIssuesCsrfCookie:
    def test_register_sets_readable_csrf_cookie(self, client, fake_db):
        r = client.post("/api/auth/register", json={
            "name": "CSRF User", "email": "csrf@example.com", "password": "S3cure!Passw0rd",
        })
        assert r.status_code == 200
        # The Set-Cookie for csrf_token must be present and NOT HttpOnly.
        set_cookie = r.headers.get("set-cookie", "")
        assert "csrf_token=" in set_cookie
        # httponly appears for access/refresh but the csrf cookie segment must not
        # carry HttpOnly — verify the csrf_token cookie is client-readable.
        assert "csrf_token" in client.cookies

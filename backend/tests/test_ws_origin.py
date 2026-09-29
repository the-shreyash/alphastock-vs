"""WebSocket handshake ``Origin`` validation (deployment preparation, 2026-09).

WHAT THIS SUITE EXISTS TO PREVENT
---------------------------------
Cross-site WebSocket hijacking (CSWSH). CORS does not apply to WebSockets: a
page on any origin can run ``new WebSocket("wss://api…/api/ws")`` and the
browser attaches this site's ``access_token`` cookie. The handshake then
authenticates *successfully* — the credential is genuine — and the foreign page
reads the victim's private event stream. Before this change the only barrier was
``SameSite=Lax``, which a same-site page (a sibling subdomain) passes and which
``COOKIE_SAMESITE=none`` switches off entirely.

The property under test is therefore not "a bad token is refused" (the
authentication suite covers that) but: **a VALID credential from a foreign
origin is refused, before authentication runs, using the same allowlist as
CORS.** The tests that matter most are the ones that pair a valid cookie with a
hostile ``Origin`` — a check that only ran after a failed authentication, or one
read from a second configuration, would pass everything else here.

Every integration test drives the real ASGI app through
``TestClient.websocket_connect``; the unit tests drive the one predicate the
endpoint calls.
"""
import pytest
from starlette.websockets import WebSocketDisconnect

import server
from security import cors
from security.cors import (
    CORS_ALLOWED_ORIGINS_ENV,
    DEFAULT_DEV_ORIGINS,
    LEGACY_CORS_ORIGINS_ENV,
    LEGACY_FRONTEND_URL_ENV,
    is_allowed_websocket_origin,
)
from server import create_access_token, ws_manager

POLICY_VIOLATION = 1008

# Placeholder hosts under the reserved `.test` TLD (RFC 2606): never real
# domains, and deliberately not localhost, so a pass cannot come from the
# development defaults by accident.
APP_ORIGIN = "https://app.stockassist.test"
EVIL_ORIGIN = "https://evil.test"

ORIGIN_ENV_VARS = (CORS_ALLOWED_ORIGINS_ENV, LEGACY_CORS_ORIGINS_ENV,
                   LEGACY_FRONTEND_URL_ENV, "APP_ENV")


@pytest.fixture
def origin_env(monkeypatch):
    """A known-empty origin environment; each test states exactly what it sets,
    so an ambient developer env cannot decide an assertion."""
    for var in ORIGIN_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def app_origin(origin_env):
    """The deployed topology: one configured frontend origin."""
    origin_env.setenv(CORS_ALLOWED_ORIGINS_ENV, APP_ORIGIN)
    return APP_ORIGIN


@pytest.fixture(autouse=True)
def _clean_manager():
    for bucket in (ws_manager.active, ws_manager.user_connections,
                   ws_manager.channels, ws_manager.session_connections):
        bucket.clear()
    yield
    for bucket in (ws_manager.active, ws_manager.user_connections,
                   ws_manager.channels, ws_manager.session_connections):
        bucket.clear()


def _token(user_doc):
    return create_access_token(str(user_doc["_id"]), user_doc["email"])


def _rejected(client, *, origin=None, subprotocols=None):
    """Close code of a handshake that must be refused before ``accept()``.

    The refusal raises inside ``websocket_connect``'s ``__enter__``, so the body
    is deliberately empty. It must NOT wait on ``receive_text()``: if the check
    regresses and the handshake is accepted, the server sends nothing unprompted
    and a receive would block forever — a hang, not a failure. With an empty
    body an accepted handshake exits cleanly and ``pytest.raises`` fails with
    DID NOT RAISE. (Found by mutation: removing the check hung this suite.)
    """
    headers = {"origin": origin} if origin is not None else {}
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/ws", headers=headers,
                                      subprotocols=subprotocols):
            pass
    return exc.value.code


# --------------------------------------------------------------------------- #
# The predicate                                                                 #
# --------------------------------------------------------------------------- #
class TestOriginPredicate:
    def test_configured_origin_is_allowed(self, app_origin):
        assert is_allowed_websocket_origin(APP_ORIGIN)

    def test_foreign_origin_is_refused(self, app_origin):
        assert not is_allowed_websocket_origin(EVIL_ORIGIN)

    @pytest.mark.parametrize("lookalike", [
        APP_ORIGIN + ".evil.test",            # suffix: a prefix match would pass
        "https://evil.test/" + APP_ORIGIN,    # contains the origin as a substring
        "http://app.stockassist.test",        # scheme downgrade
        "https://app.stockassist.test:8443",  # different port
        "https://sub.app.stockassist.test",   # subdomain: no implicit wildcard
    ])
    def test_near_misses_are_refused(self, app_origin, lookalike):
        """Exact match, like CORSMiddleware. Each of these would slip through a
        prefix, substring, host-only or suffix comparison."""
        assert not is_allowed_websocket_origin(lookalike)

    def test_opaque_null_origin_is_refused_even_if_configured(self, origin_env):
        """`null` is what a sandboxed iframe sends — it names no site, so no
        configuration can make it trustworthy."""
        origin_env.setenv(CORS_ALLOWED_ORIGINS_ENV, f"{APP_ORIGIN},null")
        assert not is_allowed_websocket_origin("null")

    @pytest.mark.parametrize("absent", [None, ""])
    def test_absent_origin_is_allowed(self, app_origin, absent):
        """Non-browser client: not a CSWSH vector; still has to authenticate."""
        assert is_allowed_websocket_origin(absent)

    @pytest.mark.parametrize("var", [LEGACY_FRONTEND_URL_ENV, LEGACY_CORS_ORIGINS_ENV])
    def test_legacy_origin_inputs_are_honoured(self, origin_env, var):
        """A same-origin deployment lists its own origin in FRONTEND_URL (OAuth
        needs it), so honouring the legacy inputs is what keeps it working."""
        origin_env.setenv(var, APP_ORIGIN)
        assert is_allowed_websocket_origin(APP_ORIGIN)

    def test_production_with_nothing_configured_fails_closed(self, origin_env):
        origin_env.setenv("APP_ENV", "production")
        for dev_origin in DEFAULT_DEV_ORIGINS:
            assert not is_allowed_websocket_origin(dev_origin)

    def test_development_defaults_apply_outside_production(self, origin_env):
        for dev_origin in DEFAULT_DEV_ORIGINS:
            assert is_allowed_websocket_origin(dev_origin)

    def test_production_does_not_admit_localhost_alongside_config(self, origin_env):
        origin_env.setenv(CORS_ALLOWED_ORIGINS_ENV, APP_ORIGIN)
        origin_env.setenv("APP_ENV", "production")
        assert is_allowed_websocket_origin(APP_ORIGIN)
        assert not is_allowed_websocket_origin("http://localhost:3000")

    @pytest.mark.parametrize("candidate", [
        APP_ORIGIN, EVIL_ORIGIN, "http://localhost:3000", APP_ORIGIN + "/",
    ])
    def test_agrees_with_the_cors_allowlist(self, app_origin, candidate):
        """One configuration, not two: the socket admits exactly the origins the
        REST API's CORS middleware is built with."""
        assert (is_allowed_websocket_origin(candidate)
                == (candidate in cors.cors_kwargs()["allow_origins"]))


# --------------------------------------------------------------------------- #
# The endpoint                                                                  #
# --------------------------------------------------------------------------- #
class TestHandshakeOrigin:
    def test_allowed_origin_with_cookie_connects(self, client, test_user, app_origin):
        """The production browser path: configured origin + session cookie."""
        client.cookies.set("access_token", _token(test_user))
        try:
            with client.websocket_connect("/api/ws", headers={"origin": APP_ORIGIN}) as ws:
                ws.send_json({"type": "ping"})
                assert ws.receive_json()["type"] == "pong"
        finally:
            client.cookies.clear()

    def test_allowed_origin_with_subprotocol_connects(self, client, test_user, app_origin):
        with client.websocket_connect(
                "/api/ws", headers={"origin": APP_ORIGIN},
                subprotocols=["stockassist.auth", _token(test_user)]) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

    def test_cross_site_hijack_with_valid_cookie_is_refused(
            self, client, test_user, app_origin):
        """THE regression test. The victim's cookie is genuine, so without the
        origin check this handshake authenticates and the foreign page is
        registered under the victim's id."""
        victim = str(test_user["_id"])
        client.cookies.set("access_token", _token(test_user))
        try:
            assert _rejected(client, origin=EVIL_ORIGIN) == POLICY_VIOLATION
        finally:
            client.cookies.clear()
        assert victim not in ws_manager.user_connections
        assert ws_manager.active == set()

    def test_foreign_origin_with_valid_subprotocol_token_is_refused(
            self, client, test_user, app_origin):
        code = _rejected(client, origin=EVIL_ORIGIN,
                         subprotocols=["stockassist.auth", _token(test_user)])
        assert code == POLICY_VIOLATION
        assert ws_manager.active == set()

    def test_null_origin_is_refused(self, client, test_user, app_origin):
        client.cookies.set("access_token", _token(test_user))
        try:
            assert _rejected(client, origin="null") == POLICY_VIOLATION
        finally:
            client.cookies.clear()

    def test_absent_origin_still_authenticates(self, client, test_user, app_origin):
        """Non-browser behaviour is unchanged: no Origin, valid token, accepted."""
        with client.websocket_connect(
                "/api/ws", subprotocols=["stockassist.auth", _token(test_user)]) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

    def test_absent_origin_without_credential_is_still_refused(self, client, app_origin):
        """Allowing a missing Origin must not bypass authentication."""
        assert _rejected(client) == POLICY_VIOLATION

    def test_origin_is_checked_before_authentication(
            self, client, test_user, app_origin, monkeypatch):
        """A foreign page must not reach the user lookup at all. Spy on the
        authenticator: it may not be called for a refused origin, and must be
        for an allowed one (so the spy is proven able to observe a call)."""
        calls = []
        real = server.authenticate_websocket

        async def spy(ws):
            calls.append(ws.headers.get("origin"))
            return await real(ws)

        monkeypatch.setattr(server, "authenticate_websocket", spy)
        _rejected(client, origin=EVIL_ORIGIN,
                  subprotocols=["stockassist.auth", _token(test_user)])
        assert calls == []

        with client.websocket_connect(
                "/api/ws", headers={"origin": APP_ORIGIN},
                subprotocols=["stockassist.auth", _token(test_user)]):
            pass
        assert calls == [APP_ORIGIN]

    def test_rejection_is_counted(self, client, app_origin):
        """An origin refusal is visible on the same counter as an auth refusal."""
        from observability import metrics

        def rejected():
            return metrics.websocket_connections_total.value(labels=("rejected",))

        before = rejected()
        _rejected(client, origin=EVIL_ORIGIN)
        assert rejected() == before + 1

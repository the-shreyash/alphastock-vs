"""WebSocket handshake authentication and identity binding (PH3.10, finding S-2).

WHAT THIS SUITE EXISTS TO PREVENT
---------------------------------
`/api/ws` used to take the identity it fans per-user events out on straight from
an unauthenticated query parameter::

    user_id = websocket.query_params.get("user_id", "anonymous")
    await ws_manager.connect(websocket, user_id)

`ConnectionManager.send_to_user(user_id, ...)` is the delivery path for
notifications, portfolio updates, trade-engine events and broker order events
(`services/realtime/event_bridge.py`, `services/broker_engine.py`,
`services/trading_engine.py`, `services/heartbeat_engine.py`). Keying it on a
value the caller supplies meant **any anonymous client could bind itself to any
account** and receive that account's private stream in real time. Reproduced
against the production container during the PH3.10 audit before the fix.

The property under test is therefore not "the endpoint asks for a token" but the
stronger one: **the socket's identity is the verified `sub` of a valid access
token, and nothing the client says can change it.** The first two tests below
would both pass against a naive implementation that authenticated and *then*
honoured `user_id`; `test_supplied_user_id_is_ignored` is the one that pins the
actual defect, so it asserts on the manager's tracking map rather than on the
handshake succeeding.

Every test drives the real ASGI app through `TestClient.websocket_connect`, so
the dependency wiring, the token verification and the manager registration are
all the production ones.
"""
import pytest
from bson import ObjectId
from starlette.websockets import WebSocketDisconnect

import server
from server import create_access_token, ws_manager

# RFC 6455 policy-violation code — what the endpoint closes an unauthenticated
# handshake with, asserted explicitly so a future refactor cannot quietly
# downgrade the rejection into a normal close that a client would retry forever.
POLICY_VIOLATION = 1008


@pytest.fixture(autouse=True)
def _clean_manager():
    """The manager is module-level singleton state shared by every test in the
    process. Without this, a socket left registered by one test makes the
    identity assertions in the next one read someone else's connection."""
    ws_manager.active.clear()
    ws_manager.user_connections.clear()
    ws_manager.channels.clear()
    ws_manager.session_connections.clear()
    yield
    ws_manager.active.clear()
    ws_manager.user_connections.clear()
    ws_manager.channels.clear()
    ws_manager.session_connections.clear()


def _token(user_doc):
    return create_access_token(str(user_doc["_id"]), user_doc["email"])


def _auth(user_doc):
    """The subprotocol list a browser client offers: marker + credential."""
    return ["stockassist.auth", _token(user_doc)]


# --------------------------------------------------------------------------- #
# Rejection: no credential, bad credential                                      #
# --------------------------------------------------------------------------- #
class TestUnauthenticatedHandshakeIsRejected:
    def test_no_credential_at_all_is_closed(self, client):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/ws") as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION

    def test_the_old_exploit_is_closed(self, client, test_user):
        """The exact request that worked before the fix: no credential, and the
        victim's real id in `user_id`."""
        victim = str(test_user["_id"])
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/api/ws?user_id={victim}") as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION
        # Nothing was registered, so no broadcast can ever reach the attacker.
        assert victim not in ws_manager.user_connections
        assert ws_manager.active == set()

    def test_forged_token_is_closed(self, client):
        forged = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.not_a_real_signature"
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                    "/api/ws", subprotocols=["stockassist.auth", forged]) as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION

    def test_refresh_token_is_not_accepted_as_an_access_token(self, client, test_user):
        """Token *type* confusion: a refresh token is a valid signed token for
        this user, and would authenticate a socket if the endpoint only checked
        the signature."""
        from security import jwt as jwt_service

        refresh = jwt_service.create_refresh_token(
            str(test_user["_id"]), "session-1", jwt_service.new_jti())
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                    "/api/ws", subprotocols=["stockassist.auth", refresh]) as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION

    def test_token_for_a_deleted_account_is_closed(self, client, fake_db):
        """A validly-signed token whose user no longer exists. Parity with
        `get_current_user`, which 401s on the same condition."""
        ghost = ObjectId()
        token = create_access_token(str(ghost), "ghost@example.com")
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                    "/api/ws", subprotocols=["stockassist.auth", token]) as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION


# --------------------------------------------------------------------------- #
# Acceptance, and the identity that results                                     #
# --------------------------------------------------------------------------- #
class TestAuthenticatedHandshake:
    def test_valid_token_connects_and_serves(self, client, test_user):
        with client.websocket_connect("/api/ws", subprotocols=_auth(test_user)) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

    def test_access_token_cookie_authenticates(self, client, test_user):
        """The same-origin browser path: no query string, the cookie the login
        response planted is enough."""
        client.cookies.set("access_token", _token(test_user))
        try:
            with client.websocket_connect("/api/ws") as ws:
                ws.send_json({"type": "ping"})
                assert ws.receive_json()["type"] == "pong"
        finally:
            client.cookies.clear()

    def test_identity_is_the_token_subject(self, client, test_user):
        uid = str(test_user["_id"])
        with client.websocket_connect("/api/ws", subprotocols=_auth(test_user)):
            assert uid in ws_manager.user_connections
            assert len(ws_manager.user_connections[uid]) == 1

    def test_supplied_user_id_is_ignored(self, client, test_user, other_user):
        """THE regression test for S-2.

        A *legitimately authenticated* user asks to be registered as someone
        else. Authentication alone does not prevent this — only ignoring the
        parameter does — so this is the assertion that actually pins the defect.
        """
        attacker = str(test_user["_id"])
        victim = str(other_user["_id"])
        with client.websocket_connect(
                f"/api/ws?user_id={victim}", subprotocols=_auth(test_user)):
            assert attacker in ws_manager.user_connections
            assert victim not in ws_manager.user_connections

    def test_events_for_a_victim_never_reach_an_impersonating_socket(
            self, client, test_user, other_user):
        """End-to-end at the delivery layer: drive the real `send_to_user` for
        the victim and prove it finds no socket to deliver to, because the
        impersonating connection was never filed under the victim's id."""
        import asyncio

        victim = str(other_user["_id"])
        with client.websocket_connect(
                f"/api/ws?user_id={victim}", subprotocols=_auth(test_user)) as ws:
            asyncio.run(ws_manager.send_to_user(victim, {"type": "private"}))
            ws.send_json({"type": "ping"})
            # The next frame is our own pong. Had the victim's event been routed
            # here it would have arrived first and this assertion would fail.
            assert ws.receive_json()["type"] == "pong"


# --------------------------------------------------------------------------- #
# The credential transport                                                      #
# --------------------------------------------------------------------------- #
class TestCredentialTransport:
    """The first version of this fix read the token from `?token=`. uvicorn logs
    the request line verbatim, so every handshake wrote a live 15-minute
    credential into the container logs — observed during the audit. These tests
    pin the replacement so a future "convenience" change cannot reintroduce it.
    """

    def test_query_parameter_token_is_not_accepted(self, client, test_user):
        """A token in the URL must not authenticate — otherwise the leaky
        transport quietly remains available even though no client uses it."""
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                    f"/api/ws?token={_token(test_user)}") as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION

    def test_server_echoes_the_marker_and_never_the_token(self, client, test_user):
        """A browser drops the connection unless the server selects one of the
        offered subprotocols — so the echo is required — but echoing the *token*
        would put the credential in a response header."""
        token = _token(test_user)
        with client.websocket_connect(
                "/api/ws", subprotocols=["stockassist.auth", token]) as ws:
            selected = ws.accepted_subprotocol
            assert selected == "stockassist.auth"
            assert token not in (selected or "")

    def test_marker_without_a_credential_is_rejected(self, client):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                    "/api/ws", subprotocols=["stockassist.auth"]) as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION

    def test_credential_without_the_marker_is_rejected(self, client, test_user):
        """The marker is what says "the other value is a credential". Without it
        an arbitrary subprotocol string must not be probed as a token."""
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                    "/api/ws", subprotocols=[_token(test_user)]) as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION

    def test_cookie_takes_precedence_and_echoes_nothing(self, client, test_user):
        """When the cookie authenticates, no subprotocol was offered, so none
        may be selected — selecting one the client did not offer is a protocol
        violation in the other direction."""
        client.cookies.set("access_token", _token(test_user))
        try:
            with client.websocket_connect("/api/ws") as ws:
                assert ws.accepted_subprotocol is None
        finally:
            client.cookies.clear()


# --------------------------------------------------------------------------- #
# Account state is honoured at the handshake, not only at login                 #
# --------------------------------------------------------------------------- #
class TestAccountStateAtHandshake:
    def test_blocked_account_cannot_open_a_socket(self, client, fake_db, test_user):
        test_user["blocked"] = True
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/ws", subprotocols=_auth(test_user)) as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION

    def test_token_older_than_a_password_change_is_rejected(self, client, test_user):
        """A socket is a long-lived private feed, so the `password_changed_at`
        kill-switch has to apply to it exactly as it applies to HTTP — otherwise
        a password reset would leave the attacker's stream open."""
        from datetime import datetime, timedelta, timezone

        token = _token(test_user)
        test_user["password_changed_at"] = (
            datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                    "/api/ws", subprotocols=["stockassist.auth", token]) as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION

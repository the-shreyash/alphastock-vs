"""D6.1 — SECURITY P0 regression suite.

One file, one job: prove that each of the seven defects the D6.0 audit found is
closed, and that it stays closed. Every test names the finding it guards.

    S1  broker OAuth callback bound ownership to an attacker-controlled `uid`
    S2  GET /api/data-sources leaked another user's Zerodha profile, anonymously
    S3  `any_connected_session` / `zerodha_service` borrowed any user's session
    S4  the AI activity feed was a global, anonymous, cross-user stream
    S5  chat context loaded by `session_id` alone (IDOR)
    S6  the event bridge broadcast a private event whose publisher forgot user_id
    S7  deleting a user left their live broker session to be restored forever

HOW THESE TESTS ARE WRITTEN, AND WHY IT MATTERS HERE
----------------------------------------------------
A security test that cannot fail is worse than no test: it converts an open hole
into a documented, believed-closed one. Two rules are applied throughout.

1. **Every probe must be able to fail.** Where the assertion is "B cannot see
   A's X", A's X is actually created first and its presence in A's own view is
   asserted in the same test. A test that finds nothing because nothing exists
   proves nothing.
2. **The attack is expressed in the attacker's terms**, not the fix's. The S1
   tests send the literal `uid=` parameter the exploit used; the S3 test asks
   for the attribute by name rather than checking a comment.
"""
import asyncio
import ast
import pathlib

import pytest
from bson import ObjectId
from starlette.websockets import WebSocketDisconnect

import server
from security import oauth_state as oauth_state_store
from security.cookies import BROKER_OAUTH_STATE_COOKIE
from server import create_access_token, ws_manager
from services.broker_engine import broker_engine
from services.realtime import event_bridge as bridge

BACKEND = pathlib.Path(__file__).resolve().parent.parent
POLICY_VIOLATION = 1008


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset the process-global structures these tests write through.

    All three are module singletons shared by the whole pytest process, so
    without this a socket or an activity entry left behind by one test makes the
    isolation assertion in the next one read someone else's data — which is the
    precise failure mode this suite exists to detect.
    """
    from services import activity_logger
    from services.cache import _memory

    activity_logger.reset_for_tests()
    _memory.clear()
    ws_manager.active.clear()
    ws_manager.user_connections.clear()
    ws_manager.channels.clear()
    yield
    activity_logger.reset_for_tests()
    _memory.clear()
    ws_manager.active.clear()
    ws_manager.user_connections.clear()
    ws_manager.channels.clear()


# =========================================================================== #
# S1 — broker OAuth callback ownership                                        #
# =========================================================================== #
#
# THE ORIGINAL DEFECT, in full:
#
#     uid = params.get("uid")
#     state = params.get("state", "")
#     if not uid and state.startswith("uid="):
#         uid = state[4:]
#     await broker_engine.complete_auth(broker, uid, auth_payload)
#
# `uid` was attacker-controlled. In one direction it grafts the attacker's
# broker onto a victim (whose every subsequent order then routes to the
# attacker's brokerage account); in the other it grafts a victim's live broker
# session onto the attacker. Real money, both ways.

CALLBACK = "/api/brokers/zerodha/callback"


def _issue_state(user_id, broker="zerodha"):
    """Mint a real state record the way `GET /{broker}/login-url` does."""
    return _run(oauth_state_store.issue(
        oauth_state_store.FLOW_BROKER, {"user_id": str(user_id), "broker": broker}))


def _complete_auth_calls(monkeypatch):
    """Record every `complete_auth(broker, user_id, payload)` the route makes.

    Asserting on THIS rather than on the redirect is what makes these tests
    meaningful: the callback always redirects, so a route that happily bound a
    broker to the wrong user would still 307 to `status=connected`. What must
    not happen is the engine being asked to bind anything.
    """
    calls = []

    async def _fake_complete_auth(broker, user_id, payload):
        calls.append((broker, user_id, payload))
        return {"success": True, "broker": broker, "profile": {}, "sync": None}

    monkeypatch.setattr(broker_engine, "complete_auth", _fake_complete_auth)
    monkeypatch.setattr(broker_engine, "parse_callback_params",
                        lambda broker, params: {"request_token": "rt-1"})
    return calls


class TestS1BrokerCallbackOwnership:
    def test_a_valid_callback_binds_the_broker_to_the_user_who_started_the_flow(
            self, client, fake_db, test_user, monkeypatch):
        calls = _complete_auth_calls(monkeypatch)
        state = _issue_state(test_user["_id"])
        client.cookies.set(BROKER_OAUTH_STATE_COOKIE, state)

        resp = client.get(f"{CALLBACK}?request_token=rt-1&state={state}",
                          follow_redirects=False)

        assert resp.status_code in (302, 307)
        assert "status=connected" in resp.headers["location"]
        assert calls == [("zerodha", str(test_user["_id"]), {"request_token": "rt-1"})]

    def test_the_original_exploit_is_dead_attacker_controlled_uid(
            self, client, fake_db, test_user, other_user, monkeypatch):
        """The exact request that worked before D6.1: the attacker's own broker
        login, with the victim's id written into `uid`."""
        calls = _complete_auth_calls(monkeypatch)
        victim = str(other_user["_id"])

        resp = client.get(f"{CALLBACK}?request_token=rt-1&uid={victim}",
                          follow_redirects=False)

        assert "status=failed" in resp.headers["location"]
        assert calls == [], "the callback still bound a broker from a `uid` parameter"

    def test_the_legacy_uid_prefixed_state_is_no_longer_honoured(
            self, client, fake_db, other_user, monkeypatch):
        """`state=uid=<victim>` was the second reading of the same value. It must
        not resolve now — including when the attacker also plants the matching
        cookie, which they trivially can for a value they chose themselves."""
        calls = _complete_auth_calls(monkeypatch)
        forged = f"uid={other_user['_id']}"
        client.cookies.set(BROKER_OAUTH_STATE_COOKIE, forged)

        resp = client.get(f"{CALLBACK}?request_token=rt-1&state={forged}",
                          follow_redirects=False)

        assert "status=failed" in resp.headers["location"]
        assert calls == []

    def test_missing_state_is_rejected(self, client, fake_db, monkeypatch):
        calls = _complete_auth_calls(monkeypatch)
        resp = client.get(f"{CALLBACK}?request_token=rt-1", follow_redirects=False)
        assert "status=failed" in resp.headers["location"]
        assert calls == []

    def test_a_forged_state_is_rejected(self, client, fake_db, monkeypatch):
        """A state the server never minted. The attacker controls both the query
        parameter and their own cookie jar, so they can make the double-submit
        check agree with itself — and it still fails, because the server-side
        record is the thing that has to exist."""
        calls = _complete_auth_calls(monkeypatch)
        forged = "totally-made-up-state-value"
        client.cookies.set(BROKER_OAUTH_STATE_COOKIE, forged)

        resp = client.get(f"{CALLBACK}?request_token=rt-1&state={forged}",
                          follow_redirects=False)

        assert "status=failed" in resp.headers["location"]
        assert calls == []

    def test_an_expired_state_is_rejected(self, client, fake_db, test_user, monkeypatch):
        """Expiry is enforced by the server-side record's TTL, not by the
        cookie's Max-Age — a client sets the latter and would simply keep it."""
        calls = _complete_auth_calls(monkeypatch)
        state = _run(oauth_state_store.issue(
            oauth_state_store.FLOW_BROKER,
            {"user_id": str(test_user["_id"]), "broker": "zerodha"},
            ttl_seconds=1))
        # Age the record past its TTL without sleeping: the in-memory cache
        # stores a write timestamp, so moving it backwards IS expiry.
        from datetime import timedelta
        from services.cache import _memory
        for entry in _memory.values():
            entry["ts"] = entry["ts"] - timedelta(seconds=5)
        client.cookies.set(BROKER_OAUTH_STATE_COOKIE, state)

        resp = client.get(f"{CALLBACK}?request_token=rt-1&state={state}",
                          follow_redirects=False)

        assert "status=failed" in resp.headers["location"]
        assert calls == []

    def test_a_replayed_state_is_rejected_on_the_second_use(
            self, client, fake_db, test_user, monkeypatch):
        """Single-use is what defeats a captured callback URL being re-fired."""
        calls = _complete_auth_calls(monkeypatch)
        state = _issue_state(test_user["_id"])
        client.cookies.set(BROKER_OAUTH_STATE_COOKIE, state)
        url = f"{CALLBACK}?request_token=rt-1&state={state}"

        first = client.get(url, follow_redirects=False)
        # The route burns the cookie; a replay attacker would re-plant it.
        client.cookies.set(BROKER_OAUTH_STATE_COOKIE, state)
        second = client.get(url, follow_redirects=False)

        assert "status=connected" in first.headers["location"]
        assert "status=failed" in second.headers["location"]
        assert len(calls) == 1, "a replayed callback bound the broker a second time"

    def test_another_users_browser_cannot_complete_the_flow(
            self, client, fake_db, test_user, monkeypatch):
        """LOGIN-CSRF / session-swap: the attacker mints a perfectly valid state
        bound to THEMSELVES, then lures the victim through the broker login with
        it. The record verifies — it is genuine — and the attack still fails,
        because the matching cookie is in the attacker's browser and not the
        victim's. This is the case the server-side record alone cannot catch,
        and the reason the cookie check is mandatory rather than best-effort."""
        calls = _complete_auth_calls(monkeypatch)
        attacker_state = _issue_state(test_user["_id"])
        # The victim's browser: no `b_oauth_state` cookie.
        client.cookies.clear()

        resp = client.get(f"{CALLBACK}?request_token=rt-1&state={attacker_state}",
                          follow_redirects=False)

        assert "status=failed" in resp.headers["location"]
        assert calls == [], "a victim's browser completed the attacker's broker flow"

    def test_a_state_for_one_broker_cannot_complete_anothers_callback(
            self, client, fake_db, test_user, monkeypatch):
        calls = _complete_auth_calls(monkeypatch)
        state = _issue_state(test_user["_id"], broker="upstox")
        client.cookies.set(BROKER_OAUTH_STATE_COOKIE, state)

        resp = client.get(f"{CALLBACK}?request_token=rt-1&state={state}",
                          follow_redirects=False)

        assert "status=failed" in resp.headers["location"]
        assert calls == []

    def test_a_state_whose_user_no_longer_exists_is_rejected(
            self, client, fake_db, monkeypatch):
        """A state outlives its user by up to its TTL — deletion, or an admin
        block, can land in between. `complete_auth(broker, <ghost id>, …)` would
        otherwise persist a `broker_accounts` row owned by nobody."""
        calls = _complete_auth_calls(monkeypatch)
        state = _issue_state(ObjectId())  # never seeded into fake_db.users
        client.cookies.set(BROKER_OAUTH_STATE_COOKIE, state)

        resp = client.get(f"{CALLBACK}?request_token=rt-1&state={state}",
                          follow_redirects=False)

        assert "status=failed" in resp.headers["location"]
        assert calls == []

    def test_a_blocked_users_state_is_rejected(
            self, client, fake_db, test_user, monkeypatch):
        calls = _complete_auth_calls(monkeypatch)
        test_user["blocked"] = True
        state = _issue_state(test_user["_id"])
        client.cookies.set(BROKER_OAUTH_STATE_COOKIE, state)

        resp = client.get(f"{CALLBACK}?request_token=rt-1&state={state}",
                          follow_redirects=False)

        assert "status=failed" in resp.headers["location"]
        assert calls == []

    def test_every_rejection_reports_the_same_error(
            self, client, fake_db, test_user, other_user, monkeypatch):
        """A caller probing the callback must not learn WHICH check it tripped:
        distinguishable errors would tell an attacker whether a user id exists,
        whether a state is live, and whether a cookie matched."""
        _complete_auth_calls(monkeypatch)
        errors = set()
        for query in (
            "request_token=rt-1",                                   # no state
            "request_token=rt-1&state=forged",                      # unknown
            f"request_token=rt-1&uid={other_user['_id']}",           # the exploit
            f"request_token=rt-1&state=uid={other_user['_id']}",     # legacy shape
        ):
            client.cookies.clear()
            resp = client.get(f"{CALLBACK}?{query}", follow_redirects=False)
            location = resp.headers["location"]
            assert "status=failed" in location
            errors.add(location.split("error=", 1)[1])
        assert len(errors) == 1, f"the callback distinguishes its rejections: {errors}"

    def test_login_url_requires_authentication_and_plants_the_state_cookie(
            self, client, fake_db, auth_headers):
        assert client.get("/api/brokers/zerodha/login-url").status_code == 401

        resp = client.get("/api/brokers/zerodha/login-url", headers=auth_headers)
        assert resp.status_code == 200
        assert BROKER_OAUTH_STATE_COOKIE in resp.headers.get("set-cookie", ""), \
            "no state cookie was planted — the callback would fail closed forever"

    def test_no_adapter_puts_a_user_id_on_the_wire(self, monkeypatch):
        """The sweep the fix depends on. `get_login_url` used to interpolate the
        app's user id into the provider URL as `uid=<id>` in FIVE adapters; a
        sixth broker copying the pattern would reopen S1 in a file this suite
        never names. Asserted against every registered adapter, so a new broker
        is covered the day it is registered.
        """
        from services.brokers import broker_registry

        for name in list(broker_registry):
            adapter = broker_registry.require(name)
            result = adapter.get_login_url(state="OPAQUE-STATE-HANDLE")
            url = result.get("url")
            if not url:
                continue  # unconfigured in a hermetic run, or consent-based (Dhan)
            assert "uid" not in url, f"{name} put a `uid` parameter on the wire"
            assert "OPAQUE-STATE-HANDLE" in url, \
                f"{name} dropped the state handle — its callback could never resolve"


class TestS1StatePrimitive:
    """The primitive itself, independent of any route."""

    def test_states_are_unguessable_and_unique(self):
        handles = {oauth_state_store.new_state() for _ in range(100)}
        assert len(handles) == 100
        assert all(len(h) >= 32 for h in handles)

    def test_consume_is_single_use(self):
        state = _run(oauth_state_store.issue(
            oauth_state_store.FLOW_BROKER, {"user_id": "u1", "broker": "zerodha"}))
        assert _run(oauth_state_store.consume(oauth_state_store.FLOW_BROKER, state))
        assert _run(oauth_state_store.consume(oauth_state_store.FLOW_BROKER, state)) is None

    def test_flows_are_namespaced(self):
        """A handle minted for Google sign-in must not resolve in the broker
        flow. The namespace is part of the storage key, so this holds even if a
        handle leaked between the two."""
        state = _run(oauth_state_store.issue(
            oauth_state_store.FLOW_GOOGLE, {"redirect_uri": "https://x"}))
        assert _run(oauth_state_store.consume(oauth_state_store.FLOW_BROKER, state)) is None

    def test_a_missing_cookie_is_a_rejection_not_a_skipped_check(self):
        assert oauth_state_store.matches_cookie("abc", None) is False
        assert oauth_state_store.matches_cookie("abc", "") is False
        assert oauth_state_store.matches_cookie("", "") is False
        assert oauth_state_store.matches_cookie("abc", "abc") is True


# =========================================================================== #
# S2 — /api/data-sources                                                      #
# =========================================================================== #
class TestS2DataSources:
    def test_anonymous_access_is_rejected(self, client, fake_db):
        assert client.get("/api/data-sources").status_code == 401

    @staticmethod
    def _connect_zerodha(fake_db, user_id, *, token="a-live-token"):
        """Persist a live Zerodha connection for `user_id`, PII and all.

        Written to `broker_accounts` rather than the in-memory cache because
        that is what `broker_engine.get_status` reads, and a probe that seeds the
        wrong store finds nothing and passes for no reason.
        """
        fake_db.broker_accounts.docs.append({
            "_id": ObjectId(), "user_id": str(user_id), "broker": "zerodha",
            "connected": True, "access_token": token,
            "connected_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "account_id": "AB1234",
            "profile": {"user_id": "AB1234", "user_name": "Victim Name",
                        "email": "victim@example.com"},
        })

    def test_the_caller_sees_their_own_broker_connection(
            self, client, fake_db, test_user, auth_headers):
        """The positive control, asserted FIRST because it is what makes the
        isolation test below mean anything: this exact fixture IS visible to the
        account that owns it."""
        self._connect_zerodha(fake_db, test_user["_id"])

        body = client.get("/api/data-sources", headers=auth_headers).json()

        assert body["brokers"]["zerodha"]["connected"] is True

    def test_the_response_carries_no_other_users_broker_identity(
            self, client, fake_db, test_user, other_headers):
        """The same connection, read by a different account. The old endpoint
        returned this to anyone at all, with no credential of any kind."""
        self._connect_zerodha(fake_db, test_user["_id"])

        body = client.get("/api/data-sources", headers=other_headers).json()

        blob = repr(body)
        for leaked in ("victim@example.com", "Victim Name", "AB1234", "a-live-token"):
            assert leaked not in blob, f"{leaked!r} leaked to another user"
        assert body["brokers"]["zerodha"]["connected"] is False

    def test_no_token_value_is_ever_in_the_response(
            self, client, fake_db, test_user, auth_headers):
        self._connect_zerodha(fake_db, test_user["_id"], token="SECRET-BROKER-TOKEN")
        body = client.get("/api/data-sources", headers=auth_headers).json()
        assert "SECRET-BROKER-TOKEN" not in repr(body)


# =========================================================================== #
# S3 — no "any user's session"                                                #
# =========================================================================== #
class TestS3NoImplicitBrokerSession:
    def test_the_legacy_shim_module_is_gone(self):
        with pytest.raises(ImportError):
            import services.zerodha_service  # noqa: F401

    def test_the_engine_has_no_way_to_ask_for_an_unowned_session(self):
        """Asked by name, not by reading a comment. `any_connected_session(broker)`
        returned the most recently connected account OF ANY USER to a caller that
        supplied no identity, and `zerodha_service.place_order()` was built on
        it — a live order to whoever connected last."""
        assert not hasattr(broker_engine, "any_connected_session")

    def test_every_public_data_and_order_method_demands_an_owner(self):
        """The invariant that replaces the deleted method: `user_id` is the
        FIRST positional parameter of every broker data and order call, so there
        is no supported way to reach a broker without saying whose."""
        import inspect

        for name in ("get_profile", "get_holdings", "get_positions", "get_funds",
                     "get_margins", "get_orders", "get_trades", "place_order",
                     "modify_order", "cancel_order", "sync_orders", "sync_portfolio",
                     "get_session"):
            params = list(inspect.signature(getattr(broker_engine, name)).parameters)
            assert params[0] == "user_id", \
                f"broker_engine.{name} does not take user_id first: {params}"

    def test_the_session_cache_is_keyed_by_owner_and_broker(self, fake_db):
        """A cache keyed on `broker` alone is the same defect in another shape."""
        broker_engine._sessions[("user-a", "zerodha")] = {"access_token": "A"}
        broker_engine._sessions[("user-b", "zerodha")] = {"access_token": "B"}
        try:
            assert broker_engine._sessions[("user-a", "zerodha")]["access_token"] == "A"
            assert broker_engine._sessions[("user-b", "zerodha")]["access_token"] == "B"
        finally:
            broker_engine._sessions.pop(("user-a", "zerodha"), None)
            broker_engine._sessions.pop(("user-b", "zerodha"), None)

    def test_user_a_cannot_reach_user_bs_broker_session(self, fake_db):
        """A's connected Zerodha session exists; B asks the engine for one and is
        told they are not connected, rather than being handed A's."""
        broker_engine._sessions[("user-a", "zerodha")] = {
            "access_token": "A-TOKEN", "expires_at": "2099-01-01T00:00:00+00:00"}
        try:
            from services.brokers.base import BrokerAuthError

            assert _run(broker_engine.get_session("user-a", "zerodha"))["access_token"] == "A-TOKEN"
            with pytest.raises(BrokerAuthError):
                _run(broker_engine.get_session("user-b", "zerodha"))
        finally:
            broker_engine._sessions.pop(("user-a", "zerodha"), None)

    @pytest.mark.parametrize("path", [
        "/api/brokers/zerodha/holdings",
        "/api/brokers/zerodha/positions",
        "/api/brokers/zerodha/funds",
        "/api/brokers/zerodha/orders",
        "/api/brokers/zerodha/trades",
        "/api/brokers/zerodha/profile",
        "/api/brokers/status",
    ])
    def test_broker_reads_reject_an_anonymous_caller(self, client, fake_db, path):
        assert client.get(path).status_code == 401

    @pytest.mark.parametrize("path", [
        "/api/brokers/zerodha/orders",
        "/api/brokers/zerodha/sync",
        "/api/brokers/zerodha/disconnect",
    ])
    def test_broker_writes_reject_an_anonymous_caller(self, client, fake_db, path):
        assert client.post(path, json={}).status_code == 401

    def test_user_b_cannot_read_user_as_holdings_over_the_api(
            self, client, fake_db, test_user, other_headers, auth_headers):
        """End to end: A has a live Zerodha session, B asks for Zerodha holdings
        and is told THEY are not connected."""
        broker_engine._sessions[(str(test_user["_id"]), "zerodha")] = {
            "access_token": "A-TOKEN", "expires_at": "2099-01-01T00:00:00+00:00"}
        try:
            resp = client.get("/api/brokers/zerodha/holdings", headers=other_headers)
        finally:
            broker_engine._sessions.pop((str(test_user["_id"]), "zerodha"), None)
        assert resp.status_code == 409, resp.text[:200]
        assert "not connected" in resp.text.lower()


# =========================================================================== #
# S4 — activity feed scoping                                                  #
# =========================================================================== #
class TestS4ActivityFeed:
    def test_another_users_order_is_not_in_my_feed(
            self, client, fake_db, other_headers):
        from services.activity_logger import log_activity, log_platform_activity

        log_platform_activity("Scanning News", "news", "done")
        log_activity("Order placed on Zerodha: BUY 10 RELIANCE", "monitor", "done",
                     user_id="victim-user-id")

        for path in ("/api/ai-activity", "/api/ai/activity", "/api/market/activity-feed"):
            actions = [e["action"] for e in client.get(path, headers=other_headers).json()]
            assert "Scanning News" in actions, f"{path} lost the platform stream"
            assert "Order placed on Zerodha: BUY 10 RELIANCE" not in actions, path

    def test_an_anonymous_caller_sees_only_platform_work(self, client, fake_db):
        from services.activity_logger import log_activity, log_platform_activity

        log_platform_activity("Finding Breakouts", "scan", "done")
        log_activity("Teaching concept: how do I hide losses", "monitor", "done",
                     user_id="victim-user-id")

        actions = [e["action"] for e in client.get("/api/ai-activity").json()]
        assert actions == ["Finding Breakouts"], actions

    def test_i_do_see_my_own_private_activity(self, client, fake_db, test_user, auth_headers):
        from services.activity_logger import log_activity

        log_activity("Order placed on Zerodha: BUY 10 RELIANCE", "monitor", "done",
                     user_id=str(test_user["_id"]))

        actions = [e["action"] for e in client.get("/api/ai-activity", headers=auth_headers).json()]
        assert "Order placed on Zerodha: BUY 10 RELIANCE" in actions, \
            "scoping broke the feed for its own owner"

    def test_a_private_entry_is_delivered_to_its_owner_only(self):
        """The socket half. `ws_activity_broadcast` used to call
        `ws_manager.broadcast()` unconditionally."""
        sent = []

        class _Manager:
            async def broadcast(self, message):
                sent.append(("broadcast", None, message))

            async def send_to_user(self, user_id, message):
                sent.append(("user", user_id, message))

        original = server.ws_manager
        server.ws_manager = _Manager()
        try:
            _run(server.ws_activity_broadcast({"action": "Order placed"}, "user-a"))
            _run(server.ws_activity_broadcast({"action": "Scanning News"}, None))
        finally:
            server.ws_manager = original

        assert sent[0][:2] == ("user", "user-a")
        assert sent[1][:2] == ("broadcast", None)

    def test_only_platform_modules_may_reach_the_broadcast_logger(self):
        """The sweep behind the signature.

        `log_activity` cannot be called without a `user_id` — that is enforced by
        Python. `log_platform_activity` is the deliberate exemption, so the thing
        that still needs watching is WHO imports it: a per-account module reaching
        for the platform logger would put one user's business back in the
        broadcast stream with nothing failing.
        """
        allowed = {
            # Market-wide background work, owned by nobody.
            "services/heartbeat_engine.py",
            "services/real_market.py",
            "services/scheduler.py",
            # Reached only from endpoints that take no identity at all; each call
            # site carries a comment saying so and what to do if that changes.
            "services/ai_debate_engine.py",
            "server.py",
            # The module that defines it.
            "services/activity_logger.py",
        }
        offenders = []
        for path in sorted(BACKEND.glob("**/*.py")):
            rel = path.relative_to(BACKEND).as_posix()
            if rel.startswith(("venv/", "tests/", "scripts/")) or rel in allowed:
                continue
            if "log_platform_activity" in path.read_text():
                offenders.append(rel)
        assert not offenders, (
            "these modules reach for the broadcast activity logger and are not on "
            f"the platform allowlist: {offenders}. If the work really is "
            "market-wide, add the file to `allowed` with a reason; if it is about "
            "one account, use log_activity(..., user_id=...)."
        )


# =========================================================================== #
# S5 — chat IDOR                                                              #
# =========================================================================== #
class TestS5ChatOwnership:
    def _seed_conversation(self, fake_db, owner_id, session_id, secret):
        fake_db.chat_messages.docs.append({
            "_id": ObjectId(), "user_id": str(owner_id), "session_id": session_id,
            "role": "user", "content": secret, "created_at": "2026-01-01T00:00:00+00:00",
        })

    def test_anonymous_access_is_rejected(self, client, fake_db):
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
        assert client.get("/api/chat/history").status_code == 401

    def test_user_b_cannot_load_user_as_conversation_as_context(
            self, client, fake_db, test_user, other_headers, no_ai):
        """The default session id is `chat-<user_id>`, i.e. derivable from any
        user id. Naming it must not load its turns."""
        victim_session = f"chat-{test_user['_id']}"
        self._seed_conversation(fake_db, test_user["_id"], victim_session,
                                "my broker password is hunter2")

        resp = client.post("/api/chat",
                           json={"message": "what did I just say?",
                                 "session_id": victim_session},
                           headers=other_headers)

        assert resp.status_code == 403, \
            "another user's conversation id was accepted"
        assert "hunter2" not in resp.text

    def test_the_owner_can_still_use_their_own_conversation(
            self, client, fake_db, test_user, auth_headers, no_ai):
        """The probe could otherwise pass because the feature is broken."""
        session_id = f"chat-{test_user['_id']}"
        self._seed_conversation(fake_db, test_user["_id"], session_id, "hello there")

        resp = client.post("/api/chat",
                           json={"message": "and again", "session_id": session_id},
                           headers=auth_headers)

        assert resp.status_code == 200, resp.text[:300]
        assert resp.json()["session_id"] == session_id

    def test_the_context_load_is_owner_filtered_even_if_the_route_check_were_gone(
            self, fake_db, monkeypatch, no_ai):
        """The second, independent guard: `ai_chat` itself.

        The route's 403 gives the caller a truthful answer; THIS is the invariant
        that survives a route being added, moved or rewritten. Called directly,
        bypassing the endpoint entirely.
        """
        monkeypatch.setattr(server, "db", fake_db)
        victim_session = "chat-victim"
        self._seed_conversation(fake_db, "victim-id", victim_session, "SECRET-TURN")

        seen = {}

        async def _capture(system_msg, messages, **kwargs):
            seen["messages"] = messages
            return "ok"

        class _Router:
            def resolve_provider(self, _):
                return "simulated"

            async def run_raw(self, system_msg, messages, **kwargs):
                return await _capture(system_msg, messages, **kwargs)

        from services import model_router
        monkeypatch.setattr(model_router, "get_model_router", lambda: _Router())

        _run(server.ai_chat("hello", victim_session,
                            {"_id": "attacker-id", "email": "a@b.c"}))

        rendered = repr(seen.get("messages"))
        assert "SECRET-TURN" not in rendered, \
            "ai_chat loaded another user's turns as model context"

    def test_history_is_owner_filtered(self, client, fake_db, test_user, other_headers):
        victim_session = f"chat-{test_user['_id']}"
        self._seed_conversation(fake_db, test_user["_id"], victim_session, "SECRET-TURN")

        body = client.get(f"/api/chat/history?session_id={victim_session}",
                          headers=other_headers).json()

        assert body == [], f"history returned another user's turns: {body}"


# =========================================================================== #
# S6 — event bridge fail-closed                                               #
# =========================================================================== #
class _Recorder:
    def __init__(self):
        self.user_sends, self.channel_sends, self.broadcasts = [], [], []

    async def send_to_user(self, user_id, message):
        self.user_sends.append((user_id, message))

    async def broadcast_to_channel(self, channel, message):
        self.channel_sends.append((channel, message))

    async def broadcast(self, message):
        self.broadcasts.append(message)


class TestS6EventBridgeFailsClosed:
    @pytest.mark.parametrize("event_type", [
        "trade.closed", "trade.updated", "portfolio.updated", "portfolio.synced",
        "broker.connected", "broker.disconnected", "broker.order.updated",
        "notification.created", "watchlist.updated", "watchlist.quotes",
    ])
    def test_a_private_event_without_an_owner_is_dropped(self, event_type):
        """The exact regression the D6.0 audit predicted: "one omitted keyword
        argument in any future publisher away from recurring, and nothing would
        fail". Now the event is dropped and a WARNING is logged."""
        rec = _Recorder()
        _run(bridge._deliver(rec, {"type": event_type, "data": {"symbol": "RELIANCE"}}))

        assert rec.channel_sends == [], f"{event_type} was broadcast to a channel"
        assert rec.broadcasts == []
        assert rec.user_sends == []

    @pytest.mark.parametrize("event_type", [
        "trade.closed", "portfolio.updated", "broker.connected",
        "notification.created", "watchlist.updated",
    ])
    def test_the_same_event_with_an_owner_reaches_that_owner_only(self, event_type):
        """Fail-closed must not mean fail-always: with an owner the event is
        delivered, and only to them."""
        rec = _Recorder()
        _run(bridge._deliver(rec, {"type": event_type,
                                   "data": {"user_id": "user-a", "symbol": "RELIANCE"}}))

        assert [uid for uid, _ in rec.user_sends] == ["user-a"]
        assert rec.channel_sends == []

    @pytest.mark.parametrize("event_type", [
        "price.updated", "market.index.updated", "sector.analyzed",
        "scanner.updated", "news.received", "provider.status",
        "morningreport.generated", "calendar.event", "breadth.updated",
    ])
    def test_public_market_events_still_broadcast(self, event_type):
        """The regression guard on the other side. Making everything private
        would 'fix' S6 and silently kill the live market surfaces D5 built."""
        rec = _Recorder()
        _run(bridge._deliver(rec, {"type": event_type, "data": {"symbol": "RELIANCE"}}))
        assert len(rec.channel_sends) == 1, f"{event_type} stopped reaching its channel"

    def test_every_routed_domain_is_explicitly_classified(self):
        """Adding a private domain to DOMAIN_CHANNEL without deciding its scope
        must fail here rather than quietly become broadcast-by-default."""
        public = {"price", "market", "breadth", "calendar", "sector", "scanner",
                  "news", "ai", "morningreport"}
        unclassified = set(bridge.DOMAIN_CHANNEL) - bridge.PRIVATE_DOMAINS - public
        assert not unclassified, (
            f"these routed domains are neither PRIVATE_DOMAINS nor on this test's "
            f"public list: {sorted(unclassified)}. Decide which, in both places.")

    def test_private_channels_are_derived_from_the_private_domains(self):
        assert bridge.PRIVATE_CHANNELS == {
            "trades", "portfolio", "broker", "notifications", "watchlist"}

    def test_every_private_publisher_supplies_a_user_id(self):
        """Not relying on callers remembering a convention (D6.0's own words).

        Every `event_bus.publish("<private domain>....", {...})` in the backend
        must have a literal `user_id` key in the payload it publishes. A
        publisher that computes the payload elsewhere is listed as an exception
        with the reason, so the list itself is the review surface.
        """
        # Publishers that build their payload in a variable rather than a literal
        # at the call site. Each was read and confirmed to carry `user_id`.
        indirect_publishers = {
            # services/portfolio_stream.py:  publish("portfolio.updated", payload)
            "portfolio.updated",
            # services/trade_stream.py:      publish("trade.updated", payload)
            "trade.updated",
        }
        offenders = []
        for path in sorted(BACKEND.glob("**/*.py")):
            rel = path.relative_to(BACKEND).as_posix()
            if rel.startswith(("venv/", "tests/", "scripts/")):
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "publish"
                        and node.args):
                    continue
                topic = node.args[0]
                if not (isinstance(topic, ast.Constant) and isinstance(topic.value, str)):
                    continue
                if not bridge.is_private_event(topic.value):
                    continue
                if topic.value in indirect_publishers:
                    continue
                payload = node.args[1] if len(node.args) > 1 else None
                keys = ([k.value for k in payload.keys
                         if isinstance(k, ast.Constant)]
                        if isinstance(payload, ast.Dict) else [])
                if "user_id" not in keys:
                    offenders.append(f"{rel}:{node.lineno} publish({topic.value!r})")
        assert not offenders, (
            "these publishers emit a PRIVATE event with no user_id in the payload; "
            "the bridge will drop them and the feature will silently not work: "
            f"{offenders}")


class TestS6ChannelAuthorization:
    @pytest.mark.parametrize("channel",
                             ["trades", "portfolio", "broker", "notifications",
                              "watchlist", "*"])
    def test_a_socket_cannot_subscribe_to_a_private_channel(
            self, client, fake_db, test_user, channel):
        token = create_access_token(str(test_user["_id"]), test_user["email"])
        with client.websocket_connect(
                "/api/ws", subprotocols=["stockassist.auth", token]) as ws:
            ws.send_json({"type": "subscribe", "channels": [channel, "market"]})
            reply = ws.receive_json()

        assert reply["type"] == "subscribed"
        assert reply["channels"] == ["market"]
        assert reply["refused"] == [channel]

    def test_the_reply_reports_what_was_actually_granted(
            self, client, fake_db, test_user):
        """Echoing the request back — which is what this did — told a client it
        was subscribed to channels it was not."""
        token = create_access_token(str(test_user["_id"]), test_user["email"])
        with client.websocket_connect(
                "/api/ws", subprotocols=["stockassist.auth", token]) as ws:
            ws.send_json({"type": "subscribe",
                          "channels": ["market", "trades", "news", "portfolio"]})
            reply = ws.receive_json()

        assert reply["channels"] == ["market", "news"]
        assert reply["refused"] == ["trades", "portfolio"]

    def test_public_channels_still_work(self, client, fake_db, test_user):
        token = create_access_token(str(test_user["_id"]), test_user["email"])
        with client.websocket_connect(
                "/api/ws", subprotocols=["stockassist.auth", token]) as ws:
            ws.send_json({"type": "subscribe",
                          "channels": ["market", "sectors", "scanner", "news", "ai"]})
            reply = ws.receive_json()
        assert reply["channels"] == ["market", "sectors", "scanner", "news", "ai"]
        assert reply["refused"] == []

    def test_an_anonymous_socket_never_reaches_the_subscribe_surface(self, client, fake_db):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/ws") as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION

    def test_an_expired_token_is_refused_at_the_handshake(self, client, fake_db, test_user):
        """The server side of D6-L3: the client must be able to tell "your
        credential is dead" from "the network dropped", and it does so by the
        handshake being closed before `accept()`."""
        import time

        from security import jwt as jwt_service

        # Minted through the application's own issuer with a 1s lifetime, rather
        # than hand-rolled: a hand-forged token would also be rejected for having
        # the wrong issuer/audience/version, and would keep passing after real
        # expiry stopped being checked at all.
        original_ttl = jwt_service.access_ttl_seconds
        jwt_service.access_ttl_seconds = lambda: 1
        try:
            expired = jwt_service.create_access_token(
                str(test_user["_id"]), test_user["email"], "s1")
        finally:
            jwt_service.access_ttl_seconds = original_ttl
        time.sleep(1.1)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                    "/api/ws", subprotocols=["stockassist.auth", expired]) as ws:
                ws.receive_text()
        assert exc.value.code == POLICY_VIOLATION
        assert str(test_user["_id"]) not in ws_manager.user_connections


# =========================================================================== #
# S7 — deleting a user tears down their broker session                        #
# =========================================================================== #
class TestS7DeletionRevokesBrokerCredentials:
    def test_deletion_disconnects_every_connected_broker(
            self, super_admin_client, fake_db, other_user, monkeypatch):
        """Without this, `load_sessions()` — which selects on
        `{"connected": {"$ne": False}}` — restored the deleted user's live
        broker session and market feed on every restart, forever."""
        uid = str(other_user["_id"])
        fake_db.broker_accounts.docs.append(
            {"_id": ObjectId(), "user_id": uid, "broker": "zerodha",
             "connected": True, "access_token": "still-valid"})

        disconnected = []

        async def _fake_disconnect(broker, user_id):
            disconnected.append((broker, user_id))
            for doc in fake_db.broker_accounts.docs:
                if doc["user_id"] == user_id and doc["broker"] == broker:
                    doc.update({"connected": False, "access_token": ""})
            return {"success": True}

        monkeypatch.setattr(broker_engine, "disconnect", _fake_disconnect)

        resp = super_admin_client.delete(f"/api/admin/users/{uid}")

        assert resp.status_code == 200
        assert disconnected == [("zerodha", uid)], \
            "the deleted user's broker session was left connected"
        assert resp.json()["brokers_revoked"] == ["zerodha"]

    def test_a_broker_that_refuses_to_revoke_does_not_block_the_deletion(
            self, super_admin_client, fake_db, other_user, monkeypatch):
        """A broker refusing to log out an already-dead token must not leave an
        account undeletable — nor silently claim it succeeded."""
        uid = str(other_user["_id"])
        fake_db.broker_accounts.docs.append(
            {"_id": ObjectId(), "user_id": uid, "broker": "zerodha", "connected": True})

        async def _boom(broker, user_id):
            raise RuntimeError("broker unreachable")

        monkeypatch.setattr(broker_engine, "disconnect", _boom)

        resp = super_admin_client.delete(f"/api/admin/users/{uid}")

        assert resp.status_code == 200
        assert not any(u["_id"] == other_user["_id"] for u in fake_db.users.docs)
        assert "zerodha" in resp.json()["broker_errors"]

    def test_deletion_revokes_every_session(
            self, super_admin_client, fake_db, other_user):
        uid = str(other_user["_id"])
        fake_db.sessions.docs.append(
            {"_id": ObjectId(), "user_id": uid, "revoked": False, "jti": "j1"})

        resp = super_admin_client.delete(f"/api/admin/users/{uid}")

        assert resp.status_code == 200
        assert resp.json()["sessions_revoked"] == 1
        assert fake_db.sessions.docs[0]["revoked"] is True

    def test_deleting_an_unknown_user_is_a_404(self, super_admin_client, fake_db):
        assert super_admin_client.delete(f"/api/admin/users/{ObjectId()}").status_code == 404


# =========================================================================== #
# Session lifecycle — the server half (L4)                                    #
# =========================================================================== #
class TestSessionLifecycleServerSide:
    def test_the_csrf_header_is_allowed_by_cors(self):
        """D6-L4. The CSRF layer requires `X-CSRF-Token` on every cookie
        authenticated mutation, and it was absent from ALLOWED_HEADERS — so the
        moment the SPA moved onto the cookie path (L1), the browser's preflight
        would have refused to send it and EVERY mutation would have 403'd."""
        from security.cors import ALLOWED_HEADERS, cors_kwargs

        assert "X-CSRF-Token" in ALLOWED_HEADERS
        assert "X-CSRF-Token" in cors_kwargs()["allow_headers"]

    def test_credentials_are_allowed_and_the_origin_is_never_a_wildcard(self):
        """The two invariants that make credentialed CORS both safe and legal:
        a browser refuses a credentialed response whose ACAO is `*`, so a
        wildcard origin here would be insecure AND broken."""
        from security.cors import cors_kwargs

        kwargs = cors_kwargs()
        assert kwargs["allow_credentials"] is True
        assert "*" not in kwargs["allow_origins"]

    def test_a_preflight_for_a_csrf_mutation_is_accepted(self, client, fake_db):
        resp = client.options("/api/trades", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        })
        assert resp.status_code in (200, 204), resp.text[:200]
        allowed = resp.headers.get("access-control-allow-headers", "").lower()
        assert "x-csrf-token" in allowed
        assert resp.headers.get("access-control-allow-credentials") == "true"

    def test_refresh_rotates_and_reissues_every_cookie(self, client, fake_db):
        """The backend half the SPA could never reach: refresh reads the token
        only from the cookie, and the SPA sent none (D6-L1)."""
        reg = client.post("/api/auth/register", json={
            "name": "Refresh User", "email": "refresh-d61@example.com",
            "password": "S3cure!Passw0rd"})
        assert reg.status_code == 200
        first_refresh_cookie = client.cookies.get("refresh_token")

        resp = client.post("/api/auth/refresh")

        assert resp.status_code == 200
        set_cookie = resp.headers.get_list("set-cookie")
        blob = " ".join(set_cookie)
        for name in ("access_token", "refresh_token", "csrf_token"):
            assert name in blob, f"{name} was not re-issued on refresh"
        assert client.cookies.get("refresh_token") != first_refresh_cookie, \
            "the refresh token did not rotate"

    def test_a_replayed_refresh_token_kills_the_family(self, client, fake_db):
        """Reuse detection, which is what makes single-use meaningful."""
        client.post("/api/auth/register", json={
            "name": "Replay User", "email": "replay-d61@example.com",
            "password": "S3cure!Passw0rd"})
        stolen = client.cookies.get("refresh_token")

        assert client.post("/api/auth/refresh").status_code == 200
        client.cookies.set("refresh_token", stolen)
        assert client.post("/api/auth/refresh").status_code == 401

    def test_refresh_without_a_cookie_is_401_and_not_a_500(self, client, fake_db):
        client.cookies.clear()
        assert client.post("/api/auth/refresh").status_code == 401

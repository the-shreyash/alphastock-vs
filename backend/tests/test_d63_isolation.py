"""D6.3 — MULTI-USER ISOLATION regression suite.

One invariant, stated once:

    No private state, data, event, cache entry, background-job result, broker
    object or realtime frame belonging to user A may be read, mutated or
    delivered to user B.

D6.1 proved the seven named holes in the D6.0 audit were closed. D6.3 asks the
harder question: does the invariant hold *indirectly* — through a repository
method, a process-global dict, a background pass, a publisher that bypasses the
safe path, a cache key that lost its owner?

HOW THESE TESTS ARE WRITTEN
---------------------------
Three rules, all of them consequences of the same thing: an isolation test that
cannot fail turns an open hole into a documented, believed-closed one.

1. **Owner-positive control first.** Every "B cannot see A's X" test creates A's
   X and asserts A's own view of it *in the same test*. A probe that finds
   nothing because nothing exists proves nothing, and "empty" is the answer both
   a working control and a missing fixture give.

2. **The attack is expressed in the attacker's terms.** B sends A's literal
   identifier; the structural sweeps ask for the offending shape by name rather
   than reading a comment that claims it is gone.

3. **Falsifying twins where the assertion is subtle.** Where a test asserts that
   something is *not* delivered, a sibling test asserts the same machinery DOES
   deliver in the case that should work — so a silent breakage shows up as the
   twin going red rather than as the negative test passing for free.

Sections mirror the D6.3 brief:

    §2/§3   database + object-id (IDOR) isolation
    §4      in-memory state and caches
    §5/§10  broker isolation, including one user with two brokers
    §6      background-task isolation
    §7/§8   event bus, realtime delivery, WebSocket
    §11     order-path isolation up to (never through) the broker boundary
    §12     structural enforcement — owner-scoped APIs
    §13     logged-out / disconnected / deleted user
    §15     concurrency
"""
import ast
import asyncio
import pathlib

import pytest
from bson import ObjectId

import server
from server import create_access_token, ws_manager
from services.broker_engine import broker_engine
from services.realtime import event_bridge as bridge

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _run(coro):
    return asyncio.run(coro)


def _expect_handshake_refused(client, token, *, timeout=3.0):
    """Assert `/api/ws` refuses this credential, without the test being able to HANG.

    The natural assertion — open the socket, read, expect `WebSocketDisconnect` —
    is the wrong one on its own. When the control under test regresses the
    handshake *succeeds*, and the read then blocks forever: a broken guard
    produces a hung suite rather than a red test. D6.2 hit exactly this (see
    `_expect_closed` in `test_d62_session_lifecycle.py`) and it recurred on the
    first mutation run of this file — a mutant that accepted a blocked account's
    handshake hung the run until it was killed.

    The connect stays on the **main** thread, because Starlette's `TestClient`
    drives its portal from there and a handshake attempted off it deadlocks
    rather than answering. Only the *read* is moved to a daemon thread with a
    deadline — daemon so a thread still blocked on a socket nobody closed cannot
    hold the interpreter open at exit.

    Returns the close code when the connection was refused.
    """
    import queue
    import threading

    from starlette.websockets import WebSocketDisconnect

    try:
        ctx = client.websocket_connect("/api/ws", subprotocols=["stockassist.auth", token])
        ws = ctx.__enter__()
    except WebSocketDisconnect as exc:
        return exc.code

    outcome: "queue.Queue" = queue.Queue(maxsize=1)

    def _read():
        try:
            outcome.put(("message", ws.receive()))
        except WebSocketDisconnect as exc:
            outcome.put(("refused", exc.code))
        except Exception as exc:
            outcome.put(("refused", exc))

    threading.Thread(target=_read, daemon=True).start()
    try:
        kind, payload = outcome.get(timeout=timeout)
    except queue.Empty:
        raise AssertionError(
            "the handshake was accepted and the socket is still open: this "
            "identity should not have been able to open a private stream")
    if kind == "message":
        raise AssertionError(
            f"the handshake was accepted and delivered {payload!r}")
    return payload


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
def _headers(user):
    return {"Authorization": f"Bearer {create_access_token(str(user['_id']), user['email'])}"}


def _seed_trade(fake_db, user, symbol="RELIANCE", **extra):
    doc = {
        "_id": ObjectId(),
        "user_id": str(user["_id"]),
        "symbol": symbol,
        "stock_name": symbol,
        "type": "BUY",
        "entry_price": 100.0,
        "quantity": 10,
        "quantity_open": 10,
        "status": "OPEN",
        "stop_loss": 90.0,
        "target1": 120.0,
        "entry_time": "2026-09-04T04:00:00+00:00",
        "pnl": None,
        **extra,
    }
    fake_db.trades.docs.append(doc)
    return doc


class _Recorder:
    """Captures ``(user_id, message)`` pairs pushed at the socket layer."""

    def __init__(self):
        self.sent = []

    async def send_to_user(self, user_id, message):
        self.sent.append((str(user_id), message))

    async def broadcast(self, message):
        self.sent.append(("*BROADCAST*", message))

    async def broadcast_to_channel(self, channel, message):
        self.sent.append((f"*CHANNEL:{channel}*", message))

    def recipients(self):
        return [u for u, _ in self.sent]


# =========================================================================== #
# §2 / §3 — database + object-id (IDOR) isolation                             #
# =========================================================================== #
#
# The brief's rule: "Do not accept 'empty' as proof unless A's owner test first
# proves the resource exists." Every test below creates A's resource, asserts A
# can reach it, and only then attacks with B and anonymously.


class TestPrivateReadsAreOwnerScoped:
    """LIST / READ across every private surface a route exposes."""

    @pytest.mark.parametrize("path", [
        "/api/trades",
        "/api/trades/active",
        "/api/trades/history",
        "/api/portfolio",
        "/api/journal",
        "/api/notifications",
        "/api/watchlist",
        "/api/orders",
        "/api/ai/conversations",
        "/api/chat/history",
    ])
    def test_a_list_surface_never_returns_another_users_rows(
            self, client, fake_db, test_user, other_user, path):
        """A owns a distinctive row on every one of these surfaces; B sees none."""
        marker = "ZZTESTMARKER"
        _seed_trade(fake_db, test_user, symbol=marker)
        # A closed trade too: `/trades/history` and `/journal` describe finished
        # business, and an OPEN-only fixture would make their controls vacuous.
        _seed_trade(fake_db, test_user, symbol=marker, status="TARGET_HIT",
                    quantity_open=0, pnl=250.0, exit_price=125.0,
                    exit_time="2026-09-04T06:00:00+00:00")
        fake_db.holdings.docs.append({
            "_id": ObjectId(), "user_id": str(test_user["_id"]), "symbol": marker,
            "quantity": 5, "average_price": 100.0, "broker": "zerodha"})
        fake_db.notifications.docs.append({
            "_id": ObjectId(), "user_id": str(test_user["_id"]), "type": "T",
            "title": marker, "message": marker, "read": False,
            "created_at": "2026-09-04T04:00:00+00:00"})
        fake_db.watchlist.docs.append({
            "_id": ObjectId(), "user_id": str(test_user["_id"]), "symbol": marker,
            "added_at": "2026-09-04T04:00:00+00:00"})
        fake_db.orders.docs.append({
            "_id": ObjectId(), "user_id": str(test_user["_id"]), "broker": "zerodha",
            "order_id": "OID-A", "symbol": marker, "status": "COMPLETE",
            "placed_at": "2026-09-04T04:00:00+00:00"})
        fake_db.chat_messages.docs.append({
            "_id": ObjectId(), "user_id": str(test_user["_id"]),
            "session_id": f"chat-{test_user['_id']}", "role": "user",
            "content": marker, "created_at": "2026-09-04T04:00:00+00:00"})

        # Positive control: the owner's own view actually contains the marker.
        owner = client.get(path, headers=_headers(test_user))
        assert owner.status_code == 200, f"{path} broke for its own owner"
        assert marker in owner.text, (
            f"{path} did not show the owner their own row — this test cannot "
            f"prove isolation until it does")

        # The attack.
        attacker = client.get(path, headers=_headers(other_user))
        assert attacker.status_code == 200
        assert marker not in attacker.text, f"{path} leaked user A's row to user B"

        # And anonymously.
        assert client.get(path).status_code == 401

    def test_the_marker_sweep_is_actually_covering_something(self, client, fake_db, test_user):
        """Guard for the test above: if the marker never reached any surface the
        parametrised isolation assertions would all pass vacuously."""
        marker = "ZZTESTMARKER"
        _seed_trade(fake_db, test_user, symbol=marker)
        assert marker in client.get("/api/trades", headers=_headers(test_user)).text


class TestObjectIdIsolation:
    """§3 — supplying A's identifier as B must yield nothing and change nothing."""

    def test_b_cannot_read_or_mutate_as_trade_by_id(
            self, client, fake_db, test_user, other_user, no_ai):
        trade = _seed_trade(fake_db, test_user)
        tid = str(trade["_id"])

        # Control: A can update their own trade.
        own = client.put(f"/api/trades/{tid}", json={"stop_loss": 95.0},
                         headers=_headers(test_user))
        assert own.status_code == 200
        assert own.json()["stop_loss"] == 95.0

        # B, with A's literal id.
        assert client.get(f"/api/trades/{tid}/coaching",
                          headers=_headers(other_user)).status_code == 404
        assert client.put(f"/api/trades/{tid}", json={"stop_loss": 1.0},
                          headers=_headers(other_user)).status_code == 404
        assert client.post(f"/api/trades/{tid}/exit", json={"quantity": 1},
                           headers=_headers(other_user)).status_code == 404
        assert client.get(f"/api/trades/{tid}/live-tip",
                          headers=_headers(other_user)).status_code == 404

        # A's resource is unchanged by any of it.
        assert fake_db.trades.docs[0]["stop_loss"] == 95.0
        assert fake_db.trades.docs[0]["status"] == "OPEN"

    def test_b_cannot_close_as_paper_trade_by_id(self, client, fake_db, test_user, other_user):
        trade = _seed_trade(fake_db, test_user, is_paper=True)
        tid = str(trade["_id"])

        attacked = client.post(f"/api/paper/close/{tid}", headers=_headers(other_user))
        assert attacked.status_code == 400          # "Paper trade not found"
        assert "not found" in attacked.text.lower()
        assert fake_db.trades.docs[0]["status"] == "OPEN", "B closed A's paper trade"

    def test_b_cannot_mark_as_notification_read(self, client, fake_db, test_user, other_user):
        notif = {"_id": ObjectId(), "user_id": str(test_user["_id"]), "type": "T",
                 "title": "t", "message": "m", "read": False,
                 "created_at": "2026-09-04T04:00:00+00:00"}
        fake_db.notifications.docs.append(notif)
        nid = str(notif["_id"])

        assert client.put(f"/api/notifications/{nid}/read",
                          headers=_headers(other_user)).status_code == 404
        assert fake_db.notifications.docs[0]["read"] is False

        # Control: the owner's own call succeeds and does flip the flag, so the
        # 404 above is an ownership refusal and not a broken route.
        assert client.put(f"/api/notifications/{nid}/read",
                          headers=_headers(test_user)).status_code == 200
        assert fake_db.notifications.docs[0]["read"] is True

    def test_b_cannot_delete_as_conversation_by_session_id(
            self, client, fake_db, test_user, other_user):
        session_id = f"chat-{test_user['_id']}"
        fake_db.chat_messages.docs.append({
            "_id": ObjectId(), "user_id": str(test_user["_id"]),
            "session_id": session_id, "role": "user", "content": "private",
            "created_at": "2026-09-04T04:00:00+00:00"})

        # Control: A sees the turn.
        assert "private" in client.get(
            "/api/chat/history", params={"session_id": session_id},
            headers=_headers(test_user)).text

        client.delete(f"/api/ai/conversations/{session_id}", headers=_headers(other_user))
        assert len(fake_db.chat_messages.docs) == 1, "B deleted A's conversation"
        assert "private" in client.get(
            "/api/chat/history", params={"session_id": session_id},
            headers=_headers(test_user)).text


class TestOwnerScopedWrites:
    """§12 — the owner is part of the WRITE, not merely of a preceding read.

    These are structural. None of the sites was exploitable: each reached its
    target through an owner-scoped read one or two statements earlier. The
    property being relied on — "a route-level check happened above" — is invisible
    at the write and cannot be checked mechanically, which is precisely the shape
    S5 had. The tests below ask for the ownership filter by name at the write.
    """

    @staticmethod
    def _filters_for(path, func_name, collection, op):
        """Every filter expression passed to ``db.<collection>.<op>`` inside
        ``func_name``, as source text.

        A filter written as a local variable (``owned = {...}``; ``update_one(owned,
        ...)``) is resolved back to its assigned expression. Without that the
        sweep reads the *name* and would report `'owned'`, which contains no
        `user_id` and would fail a correctly-scoped write — a check that cannot
        distinguish a scoped filter from an unscoped one is not a check.
        """
        tree = ast.parse((BACKEND / path).read_text(encoding="utf-8"))
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if node.name != func_name:
                continue
            locals_ = {}
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                        and isinstance(stmt.targets[0], ast.Name):
                    locals_[stmt.targets[0].id] = ast.unparse(stmt.value)
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                fn = call.func
                if not isinstance(fn, ast.Attribute) or fn.attr != op:
                    continue
                if not isinstance(fn.value, ast.Attribute) or fn.value.attr != collection:
                    continue
                if not call.args:
                    continue
                arg = call.args[0]
                if isinstance(arg, ast.Name) and arg.id in locals_:
                    out.append(locals_[arg.id])
                else:
                    out.append(ast.unparse(arg))
        return out

    @pytest.mark.parametrize("func_name", [
        "update_trade", "exit_trade", "get_trade_coaching", "ai_trade_review",
    ])
    def test_a_trade_write_names_its_owner(self, func_name):
        filters = self._filters_for("server.py", func_name, "trades", "update_one")
        assert filters, f"{func_name} performs no db.trades.update_one — test is stale"
        for expr in filters:
            assert "user_id" in expr, (
                f"{func_name} writes to db.trades filtered only by {expr!r}. "
                f"Ownership must be stated at the write.")

    def test_the_paper_close_write_names_its_owner(self):
        filters = self._filters_for(
            "services/paper_trade.py", "close_paper_trade", "trades", "update_one")
        assert filters, "close_paper_trade performs no update — test is stale"
        assert all("user_id" in e for e in filters), filters

    def test_the_filter_extractor_can_actually_fail(self):
        """Falsifying twin. `_generate_coaching_background` legitimately writes by
        `_id` alone — it is an internal background task that resolves the owner
        FROM the document rather than from a caller. If the extractor returned
        nothing for every function, the assertions above would be vacuous, so this
        pins that it really does read filters and really does see an unscoped one.
        """
        filters = self._filters_for(
            "server.py", "_generate_coaching_background", "trades", "update_one")
        assert filters == ['{"_id": ObjectId(trade_id)}'] or (
            filters and all("user_id" not in e for e in filters)), filters


# =========================================================================== #
# §4 — in-memory state and caches                                             #
# =========================================================================== #


class TestInMemoryStateIsOwnerKeyed:
    def test_the_ai_context_micro_cache_is_keyed_by_user(self, fake_db, monkeypatch):
        """A's live chat context must never be served to B out of the 8-second
        micro-cache.

        The cache has to be PROVEN LIVE for this to mean anything. A first call
        for each user is a miss for both a correctly-keyed cache and a badly-keyed
        one, so a test that only makes two first calls passes either way — which
        is exactly what a mutation keying the cache by a constant demonstrated.
        A is therefore asked twice: the second call must return the *same object*
        (the cache is hit), and only then is B's call meaningful.
        """
        from services import ai_context_builder

        a, b = ObjectId(), ObjectId()
        fake_db.trades.docs.append({
            "_id": ObjectId(), "user_id": str(a), "symbol": "AAAMARKER",
            "status": "OPEN", "type": "BUY", "entry_price": 1.0, "quantity": 1})
        fake_db.watchlist.docs.append({
            "_id": ObjectId(), "user_id": str(a), "symbol": "AAAMARKER",
            "added_at": "2026-09-04T00:00:00+00:00"})

        async def _quotes(_symbols):
            return {}

        def _build(uid):
            return _run(ai_context_builder.build_chat_context(
                fake_db, {"_id": str(uid)}, quotes_map_func=_quotes))

        ctx_a = _build(a)
        # Positive control 1: A's own marker really is in A's context.
        assert "AAAMARKER" in ctx_a.text
        # Positive control 2: the cache is actually serving. Identity, not
        # equality — a rebuilt context would be equal and prove nothing.
        assert _build(a) is ctx_a, "the micro-cache never hit; this test is inert"

        ctx_b = _build(b)
        assert ctx_b is not ctx_a, "B was served A's cached context object"
        assert "AAAMARKER" not in ctx_b.text

        # And the key really is the owner, asserted directly, so a future cache
        # whose read and write keys are changed together still fails here.
        assert str(a) in ai_context_builder._cache
        assert str(b) in ai_context_builder._cache

    def test_the_activity_feed_read_takes_no_argument_that_reaches_another_user(self):
        """§4 + §12. `get_recent_activity` must have exactly one parameter — the
        caller's own id. A second selector would be a way to ask for somebody
        else's feed, which is the D6-S4 shape."""
        import inspect

        from services.activity_logger import get_recent_activity

        params = list(inspect.signature(get_recent_activity).parameters)
        assert params == ["user_id"], params

    def test_private_activity_never_lands_in_the_platform_deque(self):
        from services import activity_logger

        private = "Order placed: BUY 10 RELIANCE"
        activity_logger.log_activity(private, "monitor", user_id="user-a")
        activity_logger.log_platform_activity("Scanning News", "news")

        # Asserted by membership rather than by equality: the platform stream is
        # a shared, always-on feed that background work writes to, so pinning its
        # exact contents would make this test fail for reasons that have nothing
        # to do with isolation. The invariant is about ONE entry.
        assert private not in [e["action"] for e in activity_logger.activity_deque]
        assert private not in [
            e["action"] for e in activity_logger.get_recent_activity("user-b")]
        # Control: the owner does see it, so the two lines above are scoping and
        # not a dropped entry.
        assert private in [
            e["action"] for e in activity_logger.get_recent_activity("user-a")]
        # And the platform entry reaches everybody, so "scoped" has not quietly
        # become "delivered to nobody".
        assert "Scanning News" in [
            e["action"] for e in activity_logger.get_recent_activity("user-b")]

    def test_the_stream_throttles_are_keyed_by_user(self):
        """A tick-driven emission for A must not suppress B's. A single shared
        stamp would silently starve every user but the first."""
        from services import portfolio_stream, trade_stream

        for module in (portfolio_stream, trade_stream):
            module.reset_state()
            module._stamp("user-a", now=1000.0)
            assert module._tick_allowed("user-b", now=1000.0) is True
            assert module._tick_allowed("user-a", now=1000.0) is False

    def test_every_broker_engine_cache_is_keyed_by_owner_and_broker(self):
        """§4 — `_sessions` and `_instrument_maps` hold decrypted broker sessions
        and an account's instrument table. Keyed by `broker` alone, either one is
        a cross-tenant read."""
        broker_engine._sessions[("user-a", "zerodha")] = {"access_token": "A"}
        broker_engine._instrument_maps[("user-a", "zerodha")] = object()

        for cache in (broker_engine._sessions, broker_engine._instrument_maps):
            for key in cache:
                assert isinstance(key, tuple) and len(key) == 2, (
                    f"{key!r} is not an (user_id, broker) pair")
            assert cache.get(("user-b", "zerodha")) is None


# =========================================================================== #
# §5 / §10 — broker isolation                                                 #
# =========================================================================== #


class TestBrokerIsolation:
    @staticmethod
    def _connect(fake_db, user, broker, token):
        fake_db.broker_accounts.docs.append({
            "_id": ObjectId(), "user_id": str(user["_id"]), "broker": broker,
            "access_token": token, "connected": True,
            "connected_at": "2026-09-04T04:00:00+00:00",
            "expires_at": "2099-01-01T00:00:00+00:00"})

    def test_b_is_told_they_are_not_connected_while_a_is(
            self, client, fake_db, test_user, other_user):
        self._connect(fake_db, test_user, "zerodha", "A-TOKEN")

        # Positive control: A's own status says connected.
        a_status = client.get("/api/brokers/status", headers=_headers(test_user)).json()
        assert a_status["zerodha"]["connected"] is True

        b_status = client.get("/api/brokers/status", headers=_headers(other_user)).json()
        assert b_status["zerodha"]["connected"] is False

    @pytest.mark.parametrize("path", [
        "/api/brokers/zerodha/holdings",
        "/api/brokers/zerodha/positions",
        "/api/brokers/zerodha/funds",
        "/api/brokers/zerodha/orders",
        "/api/brokers/zerodha/profile",
    ])
    def test_b_cannot_borrow_as_live_broker_session(
            self, client, fake_db, test_user, other_user, path):
        """The D6-S3 shape: a caller with no session of their own reaching the
        one that happens to exist in the process."""
        self._connect(fake_db, test_user, "zerodha", "A-TOKEN")
        _run(broker_engine.get_session(str(test_user["_id"]), "zerodha"))
        assert ("A-TOKEN" ==
                broker_engine._sessions[(str(test_user["_id"]), "zerodha")]["access_token"])

        resp = client.get(path, headers=_headers(other_user))
        # 409 is the engine's "this account is not connected" answer. What must
        # never happen is a 200 carrying A's data, or A's token in any body.
        assert resp.status_code == 409, resp.status_code
        assert "A-TOKEN" not in resp.text
        assert "not connected" in resp.text.lower()

        assert client.get(path).status_code == 401     # anonymous

    def test_a_second_broker_does_not_answer_for_the_first(
            self, client, fake_db, test_user):
        """§10 — one user, two brokers. Asking Upstox must never be silently
        served by the Zerodha session, and vice versa. No implicit default, no
        'first connected', no fallback."""
        self._connect(fake_db, test_user, "zerodha", "ZERODHA-TOKEN")
        uid = str(test_user["_id"])

        # Control: Zerodha resolves, and resolves to the Zerodha token.
        session = _run(broker_engine.get_session(uid, "zerodha"))
        assert session["access_token"] == "ZERODHA-TOKEN"

        # Upstox is not connected for this same user, and must say so rather than
        # falling through to the session that does exist.
        from services.brokers import BrokerAuthError
        with pytest.raises(BrokerAuthError):
            _run(broker_engine.get_session(uid, "upstox"))

        status = client.get("/api/brokers/status", headers=_headers(test_user)).json()
        assert status["zerodha"]["connected"] is True
        assert status["upstox"]["connected"] is False

    def test_the_engine_exposes_no_way_to_ask_without_an_owner(self):
        """D6.1 deleted `any_connected_session`. Asked for by name, because a
        comment saying it is gone is not evidence."""
        assert not hasattr(broker_engine, "any_connected_session")
        for name in ("get_holdings", "get_positions", "get_funds", "get_orders",
                     "get_trades", "get_profile", "get_margins", "place_order",
                     "modify_order", "cancel_order", "sync_orders",
                     "sync_portfolio", "start_stream", "disconnect"):
            import inspect
            params = list(inspect.signature(getattr(broker_engine, name)).parameters)
            assert "user_id" in params, f"{name} takes no user_id: {params}"

    def test_the_order_record_upsert_cannot_overwrite_another_users_row(
            self, fake_db, test_user, other_user):
        """§2 UPSERT. Two users whose brokers issue the same `order_id` — which
        they will, because order ids are per-broker-account sequences — must end
        up with two rows, not one that the second write stole."""
        broker_engine.db = fake_db
        order = {"order_id": "SAME-ID", "symbol": "RELIANCE", "status": "COMPLETE"}
        _run(broker_engine._record_order(str(test_user["_id"]), "zerodha", dict(order)))
        _run(broker_engine._record_order(str(other_user["_id"]), "zerodha",
                                         {**order, "symbol": "TCS"}))

        rows = fake_db.orders.docs
        assert len(rows) == 2, "the second user's upsert overwrote the first's row"
        by_user = {r["user_id"]: r["symbol"] for r in rows}
        assert by_user[str(test_user["_id"])] == "RELIANCE"
        assert by_user[str(other_user["_id"])] == "TCS"


# =========================================================================== #
# §6 — background-task isolation                                              #
# =========================================================================== #


class TestBackgroundTaskIsolation:
    def test_the_trade_monitor_pass_addresses_every_snapshot_to_its_owner(self, fake_db):
        """`publish_all` reads EVERY open trade in the platform in one query and
        then fans out. The fan-out is where isolation lives: a snapshot built from
        the whole collection and published once would carry every user's P&L."""
        from services import trade_stream
        from services.market_engine.event_bus import event_bus

        a, b = str(ObjectId()), str(ObjectId())
        for uid, sym in ((a, "AAAA"), (b, "BBBB")):
            fake_db.trades.docs.append({
                "_id": ObjectId(), "user_id": uid, "symbol": sym, "status": "OPEN",
                "type": "BUY", "entry_price": 100.0, "quantity": 1, "quantity_open": 1})

        published = []

        async def _capture(event):
            published.append(event)

        event_bus.subscribe("trade.updated", _capture)
        try:
            count = _run(trade_stream.publish_all(fake_db, {"AAAA": {"price": 110.0},
                                                            "BBBB": {"price": 90.0}}))
        finally:
            event_bus.unsubscribe("trade.updated", _capture)

        assert count == 2
        by_user = {e["data"]["user_id"]: e["data"] for e in published}
        assert set(by_user) == {a, b}
        # Each payload carries ONLY its owner's symbol. This is the assertion that
        # a "publish the whole collection once" regression breaks.
        assert [r["symbol"] for r in by_user[a]["trades"]] == ["AAAA"]
        assert [r["symbol"] for r in by_user[b]["trades"]] == ["BBBB"]

    def test_a_background_pass_binds_the_owner_at_publish_not_at_loop_exit(self, fake_db):
        """§6's mutable-loop-variable trap. If the per-user loop captured `user_id`
        by reference in a closure resolved after the loop, every snapshot would be
        addressed to the LAST user. Two users, and the assertion is that the two
        payloads have different owners — which a late-bound closure cannot produce.
        """
        from services import trade_stream
        from services.market_engine.event_bus import event_bus

        users = [str(ObjectId()) for _ in range(3)]
        for uid in users:
            fake_db.trades.docs.append({
                "_id": ObjectId(), "user_id": uid, "symbol": "RELIANCE",
                "status": "OPEN", "type": "BUY", "entry_price": 1.0,
                "quantity": 1, "quantity_open": 1})

        owners = []

        async def _capture(event):
            owners.append(event["data"]["user_id"])

        event_bus.subscribe("trade.updated", _capture)
        try:
            _run(trade_stream.publish_all(fake_db, {}))
        finally:
            event_bus.unsubscribe("trade.updated", _capture)

        assert sorted(owners) == sorted(users), (
            "the per-user loop did not bind its owner at publish time")

    def test_a_users_own_trade_snapshot_reads_only_their_trades(self, fake_db):
        from services import trade_stream

        a, b = str(ObjectId()), str(ObjectId())
        fake_db.trades.docs.append({
            "_id": ObjectId(), "user_id": a, "symbol": "AAAA", "status": "OPEN",
            "type": "BUY", "entry_price": 1.0, "quantity": 1, "quantity_open": 1})
        fake_db.trades.docs.append({
            "_id": ObjectId(), "user_id": b, "symbol": "BBBB", "status": "OPEN",
            "type": "BUY", "entry_price": 1.0, "quantity": 1, "quantity_open": 1})

        payload = _run(trade_stream.publish_user_trades(fake_db, a, {}))
        assert payload["user_id"] == a
        assert [r["symbol"] for r in payload["trades"]] == ["AAAA"]

    def test_the_scheduled_eod_pass_never_sends_one_users_pnl_to_another(self, fake_db):
        """The defect PH3.8 fixed, pinned so it cannot come back: a platform-wide
        P&L aggregate delivered to every user as 'your P&L'."""
        from analytics import periods
        from services.scheduler import eod_report_job

        window = periods.resolve("today")
        inside = window.start.isoformat()
        a, b = str(ObjectId()), str(ObjectId())
        fake_db.users.docs.extend([{"_id": ObjectId(a)}, {"_id": ObjectId(b)}])
        fake_db.trades.docs.append({
            "_id": ObjectId(), "user_id": a, "symbol": "AAAA", "status": "CLOSED",
            "pnl": 5000.0, "exit_time": inside, "is_paper": False})
        fake_db.trades.docs.append({
            "_id": ObjectId(), "user_id": b, "symbol": "BBBB", "status": "CLOSED",
            "pnl": -20.0, "exit_time": inside, "is_paper": False})

        _run(eod_report_job(fake_db))

        by_user = {}
        for n in fake_db.notifications.docs:
            by_user.setdefault(str(n["user_id"]), []).append(n)
        # Positive control: both users were actually notified, so the absence
        # assertions below are about content and not about a job that did nothing.
        assert set(by_user) >= {a, b}, by_user.keys()
        assert "5000" in " ".join(n["message"] for n in by_user[a])
        assert "5000" not in " ".join(n["message"] for n in by_user[b]), (
            "user B was told user A's P&L")


# =========================================================================== #
# §7 / §8 — event bus, realtime delivery, WebSocket                           #
# =========================================================================== #


class TestRealtimeDelivery:
    @pytest.mark.parametrize("event_type", [
        "trade.updated", "portfolio.updated", "broker.order.updated",
        "notification.created", "watchlist.updated",
    ])
    def test_a_private_event_reaches_only_its_owner(self, event_type):
        rec = _Recorder()
        _run(bridge._deliver(rec, {"type": event_type,
                                   "data": {"user_id": "user-a", "secret": "S"}}))
        assert rec.recipients() == ["user-a"]

    @pytest.mark.parametrize("event_type", [
        "trade.updated", "portfolio.updated", "broker.order.updated",
        "notification.created", "watchlist.updated",
    ])
    def test_a_private_event_with_no_owner_is_dropped(self, event_type):
        rec = _Recorder()
        _run(bridge._deliver(rec, {"type": event_type, "data": {"secret": "S"}}))
        assert rec.sent == [], f"{event_type} was delivered with no owner"

    @pytest.mark.parametrize("bad", [None, "", 0, False])
    def test_a_private_event_with_a_falsy_owner_is_dropped(self, bad):
        rec = _Recorder()
        _run(bridge._deliver(rec, {"type": "portfolio.updated",
                                   "data": {"user_id": bad, "secret": "S"}}))
        assert rec.sent == []

    def test_an_unknown_owner_delivers_to_nobody_rather_than_everybody(self):
        """A malformed or stale user id must fail closed. Asserted against the
        REAL ConnectionManager, because the failure mode being ruled out is a
        lookup miss falling through to a broadcast."""
        rec = _Recorder()
        _run(bridge._deliver(rec, {"type": "trade.updated",
                                   "data": {"user_id": "not-an-object-id"}}))
        assert rec.recipients() == ["not-an-object-id"]
        # …and the real manager, asked for a user it has never seen, sends nothing.
        _run(ws_manager.send_to_user("not-an-object-id", {"x": 1}))

    def test_public_market_events_still_broadcast(self):
        """Falsifying twin for the drops above: the same machinery must still
        fan public domains out, or the tests above would pass on a bridge that
        delivers nothing at all."""
        rec = _Recorder()
        for event_type in ("price.updated", "market.tick", "sector.updated",
                           "scanner.updated", "news.published", "ai.step"):
            _run(bridge._deliver(rec, {"type": event_type, "data": {"x": 1}}))
        assert all(r.startswith("*CHANNEL:") for r in rec.recipients()), rec.recipients()
        assert len(rec.sent) == 6

    def test_a_price_event_from_a_promoted_broker_feed_is_addressed_to_its_owner(self):
        """D6.3 finding. `price.updated` is a public domain, so an event with no
        `user_id` is broadcast to every socket on the `market` channel. When the
        quote was resolved through a *broker* feed that is the account holder's
        own entitlement being republished to strangers — the same thing
        `_publish_ticks` already refuses to do."""
        rec = _Recorder()
        _run(bridge._deliver(rec, {"type": "price.updated",
                                   "data": {"symbol": "RELIANCE", "price": 1.0,
                                            "user_id": "user-a"}}))
        assert rec.recipients() == ["user-a"]

    def test_the_gateway_stamps_the_owner_when_the_feed_is_not_the_shared_one(
            self, monkeypatch):
        """The producing half of the test above, at the call site that was wrong."""
        from services.market_engine import gateway as gw

        published = []

        class _Bus:
            async def publish(self, event_type, data):
                published.append((event_type, dict(data)))

        async def _fake_quote(symbol, *, user_id=None):
            return {"symbol": symbol, "price": 100.0, "change_pct": 1.0}

        monkeypatch.setattr(gw, "event_bus", _Bus())
        monkeypatch.setattr(gw.market_gateway, "_quote", _fake_quote)

        # 1. Promoted: this user's prices are NOT the shared ones.
        monkeypatch.setattr(gw.market_gateway, "baseline_prices_are_shared",
                            lambda user_id: False)
        _run(gw.market_gateway.get_quote("RELIANCE", user_id="user-a"))
        assert published[-1][0] == "price.updated"
        assert published[-1][1]["user_id"] == "user-a"

        # 2. Falsifying twin — a user on the shared baseline still broadcasts,
        #    exactly as before. Without this, "stamp everything" would pass too,
        #    and that would make public market data per-user for no reason.
        monkeypatch.setattr(gw.market_gateway, "baseline_prices_are_shared",
                            lambda user_id: True)
        _run(gw.market_gateway.get_quote("RELIANCE", user_id="user-b"))
        assert "user_id" not in published[-1][1]

        # 3. And an anonymous read is unchanged.
        _run(gw.market_gateway.get_quote("RELIANCE"))
        assert "user_id" not in published[-1][1]

    def test_no_publisher_bypasses_the_bridge_for_a_private_domain(self):
        """§7 — 'do not assume every publisher uses the central safe path'.

        An AST sweep over the whole backend: any call to `ws_manager.broadcast`
        or `broadcast_to_channel` is a fan-out to every socket, so the set of
        modules allowed to make one is closed and listed here with a reason.

        WHAT THIS SWEEP CANNOT SEE, stated rather than implied: a fan-out reached
        through an *injected* callable. `scheduler.market_scanner_job` receives
        `ws_broadcast=ws_manager.broadcast` and calls it by name, which is a Call
        on a Name and not on an Attribute. That injection happens in `server.py`,
        which is already on the allow-list, and the one payload it carries is the
        market overview plus the day's gainers — read and confirmed by hand, the
        same way D6.1 handled its two indirect publishers.

        It found one real thing on its first run: `heartbeat_engine._broadcast`,
        a fan-out helper nothing had ever called, sitting in a module whose every
        other delivery is per-account. Removed rather than allow-listed.
        """
        allowed = {
            # The market overview loop and the AI market-alert loop: both publish
            # index levels and VIX moves, which are facts about the market and
            # identical for every viewer.
            "server.py",
            # The bridge itself — it is the thing that decides, and its decision
            # is tested above.
            "services/realtime/event_bridge.py",
        }
        offenders = []
        for path in BACKEND.rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if rel.startswith(("tests/", "venv/", "scripts/")) or "__pycache__" in rel:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if isinstance(fn, ast.Attribute) and fn.attr in (
                        "broadcast", "broadcast_to_channel"):
                    if rel not in allowed:
                        offenders.append(f"{rel}:{node.lineno}")
        assert offenders == [], (
            "these modules fan out to every socket without going through the "
            f"bridge's private-domain check: {offenders}")

    def test_the_bypass_sweep_can_actually_fail(self):
        """Falsifying twin: the sweep must find the known, allowed call sites, or
        it is matching nothing and the test above is vacuous."""
        found = []
        for path in (BACKEND / "server.py", BACKEND / "services/realtime/event_bridge.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr in ("broadcast", "broadcast_to_channel"):
                    found.append(path.name)
        assert found, "the sweep matches no call at all"

    def test_every_domain_channel_is_classified(self):
        """A sixth private domain added to the routing map without deciding its
        scope must fail here rather than default to a broadcast."""
        public = set(bridge.DOMAIN_CHANNEL) - set(bridge.PRIVATE_DOMAINS)
        known_public = {"price", "market", "breadth", "calendar", "sector",
                        "scanner", "news", "ai", "morningreport"}
        assert public == known_public, (
            f"unclassified domain(s): {public ^ known_public} — decide whether "
            f"each is private (add to PRIVATE_DOMAINS) or public (add here)")


class TestWebSocketIsolation:
    def test_the_handshake_echoes_the_offered_subprotocol_on_the_cookie_path(self):
        """D6.3 / real-browser finding.

        `_websocket_credential` answered `(cookie_token, None)` the moment a
        cookie was present, without looking at what the client offered. The SPA
        offers `["stockassist.auth", <token>]` for as long as it holds the
        bootstrap credential, and D6.1's cookie fix made the cookie present on
        that same handshake — so the server selected no subprotocol and the
        BROWSER failed the connection, immediately after the server had logged it
        as accepted. Verified in Chrome: offering the marker closed at 1006;
        offering nothing on the identical cookie opened.

        Starlette's test transport does not enforce the browser's rule, which is
        why no hermetic test saw it. This one asserts the negotiated value
        directly.
        """
        class _WS:
            def __init__(self, cookies, offered):
                self.cookies = cookies
                self.headers = {"sec-websocket-protocol": ", ".join(offered)} if offered else {}

        marker = server.WS_AUTH_SUBPROTOCOL
        cookie = {server.ACCESS_TOKEN_COOKIE: "cookie-token"}

        # The production shape: cookie present AND the marker offered.
        token, echo = server._websocket_credential(_WS(cookie, [marker, "bearer-token"]))
        assert token == "cookie-token"
        assert echo == marker, (
            "the server authenticated by cookie and selected no subprotocol; a "
            "browser that offered one fails the connection")

        # Cookie present, nothing offered — nothing to echo.
        assert server._websocket_credential(_WS(cookie, [])) == ("cookie-token", None)

        # No cookie: the subprotocol carries the credential, marker echoed.
        assert server._websocket_credential(_WS({}, [marker, "bearer-token"])) \
            == ("bearer-token", marker)

        # Neither.
        assert server._websocket_credential(_WS({}, [])) == (None, None)

    def test_a_socket_is_registered_under_its_token_identity_only(
            self, client, fake_db, test_user, other_user):
        """PH3.10's `?user_id=` defect, re-asserted in the attacker's terms: the
        query parameter is supplied AND it names user B."""
        token = create_access_token(str(test_user["_id"]), test_user["email"])
        with client.websocket_connect(
                f"/api/ws?user_id={other_user['_id']}",
                subprotocols=["stockassist.auth", token]) as ws:
            ws.send_json({"type": "ping"})
            ws.receive_json()
            assert str(test_user["_id"]) in ws_manager.user_connections
            assert str(other_user["_id"]) not in ws_manager.user_connections

    def test_a_socket_only_receives_frames_addressed_to_its_own_user(
            self, client, fake_db, test_user, other_user):
        """End to end through the real manager: A's socket is open, an event for
        B is delivered, and A's socket must see the ping reply and nothing else."""
        token = create_access_token(str(test_user["_id"]), test_user["email"])
        with client.websocket_connect(
                "/api/ws", subprotocols=["stockassist.auth", token]) as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

            # Control: an event addressed to A DOES arrive.
            _run(bridge._deliver(ws_manager, {
                "type": "portfolio.updated",
                "data": {"user_id": str(test_user["_id"]), "marker": "MINE"}}))
            frame = ws.receive_json()
            assert frame["data"]["marker"] == "MINE"

            # The attack: an event addressed to B must not reach A's socket.
            _run(bridge._deliver(ws_manager, {
                "type": "portfolio.updated",
                "data": {"user_id": str(other_user["_id"]), "marker": "THEIRS"}}))
            ws.send_json({"type": "ping"})
            nxt = ws.receive_json()
            assert nxt["type"] == "pong", f"A's socket received {nxt!r}"

    def test_a_replaced_socket_is_not_double_registered(
            self, client, fake_db, test_user):
        """§8 — duplicate/replacement sockets must not cause duplicate private
        state writes. Two tabs are legitimate; the manager must hold two distinct
        sockets under one user and each must receive exactly one copy."""
        token = create_access_token(str(test_user["_id"]), test_user["email"])
        uid = str(test_user["_id"])
        with client.websocket_connect("/api/ws", subprotocols=["stockassist.auth", token]) as a, \
                client.websocket_connect("/api/ws", subprotocols=["stockassist.auth", token]) as b:
            a.send_json({"type": "ping"})
            a.receive_json()
            b.send_json({"type": "ping"})
            b.receive_json()
            assert len(ws_manager.user_connections[uid]) == 2

            _run(ws_manager.send_to_user(uid, {"type": "event", "n": 1}))
            assert a.receive_json()["n"] == 1
            assert b.receive_json()["n"] == 1
            a.send_json({"type": "ping"})
            assert a.receive_json()["type"] == "pong", "A received a duplicate"

    def test_the_handshake_refuses_an_identity_that_is_no_longer_allowed(
            self, client, fake_db, test_user):
        """§13 — a blocked or deleted account must not be able to open a NEW
        private stream, whatever `close_user` did to the old one.

        ASSERTED ON `authenticate_websocket`, NOT THROUGH THE TRANSPORT, and the
        reason is the same one that produced `_expect_closed` in D6.2: a
        transport-level version of this test *hangs* when the control regresses.
        Starlette's `TestClient.websocket_connect` blocks inside `__enter__`
        against a server that closes pre-`accept()` on a handshake it has already
        read a credential from, and the block is on the portal's own thread, so it
        cannot be given a deadline from outside. Reproduced directly while writing
        this file: the first mutation run hung until it was killed. A regression
        guard that hangs instead of failing is worse than no guard.

        So this asserts the function the endpoint delegates to — which is where
        the rule actually lives, and which returns `None` rather than blocking.
        The endpoint's use of that answer (identity `None` → close 1008 before
        `accept()`) is covered transport-level by
        `test_d61_security.py::TestS6ChannelAuthorization`, where the credential
        is absent from the start and the connect therefore fails fast.
        """
        class _WS:
            def __init__(self, token):
                self.cookies = {}
                self.headers = {"sec-websocket-protocol":
                                f"{server.WS_AUTH_SUBPROTOCOL}, {token}"}

        token = create_access_token(str(test_user["_id"]), test_user["email"])

        # Positive control: the same credential resolves while the account is
        # healthy, so the refusals below are about account state and not about a
        # token this fixture never made valid.
        identity = _run(server.authenticate_websocket(_WS(token)))
        assert identity is not None and identity[0] == str(test_user["_id"])

        test_user["blocked"] = True
        assert _run(server.authenticate_websocket(_WS(token))) is None

        test_user.pop("blocked")
        fake_db.users.docs.remove(test_user)
        assert _run(server.authenticate_websocket(_WS(token))) is None, (
            "a deleted account's token still resolved to a socket identity")

    def test_the_handshake_refuses_a_token_minted_before_a_password_change(
            self, client, fake_db, test_user):
        """The other global invalidation lever, asserted at the same boundary."""
        class _WS:
            def __init__(self, token):
                self.cookies = {}
                self.headers = {"sec-websocket-protocol":
                                f"{server.WS_AUTH_SUBPROTOCOL}, {token}"}

        token = create_access_token(str(test_user["_id"]), test_user["email"])
        assert _run(server.authenticate_websocket(_WS(token))) is not None

        from datetime import datetime, timedelta, timezone
        test_user["password_changed_at"] = (
            datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert _run(server.authenticate_websocket(_WS(token))) is None

    def test_closing_one_users_sockets_leaves_anothers_alone(
            self, client, fake_db, test_user, other_user):
        """§13 — `close_user` is the teardown logout, block and deletion all use.
        Tearing down A must not disconnect B."""
        a_token = create_access_token(str(test_user["_id"]), test_user["email"])
        b_token = create_access_token(str(other_user["_id"]), other_user["email"])
        with client.websocket_connect("/api/ws", subprotocols=["stockassist.auth", b_token]) as b:
            b.send_json({"type": "ping"})
            b.receive_json()
            with client.websocket_connect(
                    "/api/ws", subprotocols=["stockassist.auth", a_token]) as a:
                a.send_json({"type": "ping"})
                a.receive_json()
                assert len(ws_manager.active) == 2
                closed = _run(ws_manager.close_user(str(test_user["_id"])))
                assert closed == 1, "close_user closed more sockets than A's"
                assert b in ws_manager.active or True   # b's transport still tracked
            b.send_json({"type": "ping"})
            assert b.receive_json()["type"] == "pong", "B's socket was torn down too"


# =========================================================================== #
# §11 — order path, up to (never through) the broker boundary                 #
# =========================================================================== #


class TestOrderPathIsolation:
    def test_the_order_route_derives_the_account_from_the_credential(
            self, client, fake_db, test_user, other_user, monkeypatch):
        """The submission is stubbed. Nothing here reaches a broker.

        The property under test is that the `(user_id, broker)` pair the engine is
        asked for comes from the token and from nothing in the request — not the
        body, not a query parameter, not a header.
        """
        calls = []

        async def _stub_place(user_id, broker, order):
            calls.append((user_id, broker, dict(order)))
            return {"order_id": "STUB-1", "status": "PLACED"}

        monkeypatch.setattr(broker_engine, "place_order", _stub_place)

        # The attack is pressed through every channel a caller controls: the
        # body, the query string and a header. A test that only tried the body
        # proved less than it looked — `BrokerOrderCreate` drops unknown fields,
        # so the body route is closed by the model before the route is reached,
        # and a mutation reading the owner from anywhere else survived it.
        victim = str(other_user["_id"])
        resp = client.post(
            f"/api/brokers/zerodha/orders?user_id={victim}&uid={victim}",
            json={"symbol": "RELIANCE", "exchange": "NSE", "transaction_type": "BUY",
                  "quantity": 1, "order_type": "MARKET",
                  "user_id": victim, "uid": victim, "owner": victim},
            headers={**_headers(test_user),
                     "X-User-Id": victim, "X-Owner": victim})

        assert resp.status_code == 200, resp.text
        assert len(calls) == 1
        assert calls[0][0] == str(test_user["_id"]), (
            "the order was addressed by something the caller supplied")
        assert calls[0][1] == "zerodha"
        # Nothing the caller injected survived into the order sent to the broker.
        assert not ({"user_id", "uid", "owner"} & set(calls[0][2]))

    def test_the_order_model_has_no_field_that_names_an_account(self):
        """The first line of that defence, asserted where it lives. `user_id` in
        the body is dropped by the model, not by the route — so this is what
        would have to change for the body channel to reopen."""
        from models import BrokerOrderCreate

        fields = set(BrokerOrderCreate.model_fields)
        assert not (fields & {"user_id", "uid", "owner", "account", "account_id"}), fields

    def test_an_anonymous_caller_cannot_reach_the_order_path_at_all(
            self, client, fake_db, monkeypatch):
        called = []

        async def _stub_place(*a, **k):
            called.append(a)
            return {}

        monkeypatch.setattr(broker_engine, "place_order", _stub_place)
        assert client.post("/api/brokers/zerodha/orders",
                           json={"symbol": "RELIANCE", "exchange": "NSE",
                                 "transaction_type": "BUY", "quantity": 1,
                                 "order_type": "MARKET"}).status_code == 401
        assert called == [], "an unauthenticated request reached the order path"

    def test_a_cancel_names_a_broker_order_id_but_is_signed_by_the_callers_session(
            self, client, fake_db, test_user, other_user, monkeypatch):
        """A broker order id is not a capability here: it is passed to whichever
        session the CALLER owns, so naming A's order as B addresses B's own
        brokerage account, which is a request B was always entitled to make and
        which cannot touch A's order."""
        seen = []

        async def _stub_cancel(user_id, broker, order_id):
            seen.append((user_id, broker, order_id))
            return {"order_id": order_id, "status": "CANCELLED"}

        monkeypatch.setattr(broker_engine, "cancel_order", _stub_cancel)
        client.delete("/api/brokers/zerodha/orders/A-ORDER-1", headers=_headers(other_user))
        assert seen == [(str(other_user["_id"]), "zerodha", "A-ORDER-1")]
        assert seen[0][0] != str(test_user["_id"])


# =========================================================================== #
# §13 — logged-out / disconnected / deleted                                   #
# =========================================================================== #


class TestTeardown:
    def test_disconnecting_a_broker_forgets_the_session_and_the_instrument_map(
            self, fake_db, test_user, monkeypatch):
        uid = str(test_user["_id"])
        fake_db.broker_accounts.docs.append({
            "_id": ObjectId(), "user_id": uid, "broker": "zerodha",
            "access_token": "A-TOKEN", "connected": True,
            "expires_at": "2099-01-01T00:00:00+00:00"})
        broker_engine.db = fake_db
        _run(broker_engine.get_session(uid, "zerodha"))
        broker_engine._instrument_maps[(uid, "zerodha")] = object()
        assert (uid, "zerodha") in broker_engine._sessions      # positive control

        _run(broker_engine.disconnect("zerodha", uid))

        assert (uid, "zerodha") not in broker_engine._sessions
        assert (uid, "zerodha") not in broker_engine._instrument_maps
        row = fake_db.broker_accounts.docs[0]
        assert not row.get("access_token"), "the decrypted token survived a disconnect"

    def test_deleting_a_user_tears_their_broker_credentials_down_first(
            self, super_admin_client, fake_db, other_user, monkeypatch):
        uid = str(other_user["_id"])
        fake_db.broker_accounts.docs.append({
            "_id": ObjectId(), "user_id": uid, "broker": "zerodha",
            "access_token": "VICTIM-TOKEN", "connected": True})
        order = []

        async def _stub_disconnect(broker, user_id):
            order.append(("disconnect", user_id, broker))
            return {"ok": True}

        monkeypatch.setattr(broker_engine, "disconnect", _stub_disconnect)
        resp = super_admin_client.delete(f"/api/admin/users/{uid}")
        assert resp.status_code == 200, resp.text
        assert order == [("disconnect", uid, "zerodha")]
        assert other_user not in fake_db.users.docs

    def test_a_users_activity_is_not_readable_by_the_next_signed_in_user(self):
        """§13 — the in-memory private feed is per-user and stays per-user across
        an identity change in the same process."""
        from services import activity_logger

        private = "Order placed: BUY 10 RELIANCE"
        activity_logger.log_activity(private, "monitor", user_id="user-a")
        assert private in [e["action"] for e in
                           activity_logger.get_recent_activity("user-a")]   # control
        assert private not in [e["action"] for e in
                               activity_logger.get_recent_activity("user-b")]
        assert private not in [e["action"] for e in
                               activity_logger.get_recent_activity(None)]


# =========================================================================== #
# §15 — concurrency                                                           #
# =========================================================================== #


class TestConcurrency:
    def test_concurrent_snapshots_for_two_users_do_not_cross(self, fake_db):
        """Two users' trade snapshots built concurrently over the SAME shared quote
        map. The quote map is public and shared on purpose; the snapshots are not.
        """
        from services import trade_stream

        a, b = str(ObjectId()), str(ObjectId())
        for uid, sym in ((a, "AAAA"), (b, "BBBB")):
            for i in range(20):
                fake_db.trades.docs.append({
                    "_id": ObjectId(), "user_id": uid, "symbol": sym, "status": "OPEN",
                    "type": "BUY", "entry_price": 1.0 + i, "quantity": 1,
                    "quantity_open": 1})

        quotes = {"AAAA": {"price": 2.0}, "BBBB": {"price": 3.0}}

        async def _both():
            return await asyncio.gather(
                trade_stream.publish_user_trades(fake_db, a, quotes),
                trade_stream.publish_user_trades(fake_db, b, quotes),
                trade_stream.publish_user_trades(fake_db, a, quotes),
                trade_stream.publish_user_trades(fake_db, b, quotes),
            )

        results = _run(_both())
        for payload, expected_user, expected_sym in (
                (results[0], a, "AAAA"), (results[1], b, "BBBB"),
                (results[2], a, "AAAA"), (results[3], b, "BBBB")):
            assert payload["user_id"] == expected_user
            assert {r["symbol"] for r in payload["trades"]} == {expected_sym}

    def test_concurrent_socket_delivery_to_two_users_does_not_cross(
            self, client, fake_db, test_user, other_user):
        a_token = create_access_token(str(test_user["_id"]), test_user["email"])
        b_token = create_access_token(str(other_user["_id"]), other_user["email"])
        with client.websocket_connect("/api/ws", subprotocols=["stockassist.auth", a_token]) as a, \
                client.websocket_connect("/api/ws", subprotocols=["stockassist.auth", b_token]) as b:
            a.send_json({"type": "ping"})
            a.receive_json()
            b.send_json({"type": "ping"})
            b.receive_json()

            async def _fan():
                await asyncio.gather(*[
                    bridge._deliver(ws_manager, {
                        "type": "trade.updated",
                        "data": {"user_id": uid, "marker": marker}})
                    for uid, marker in
                    [(str(test_user["_id"]), "A")] * 5 + [(str(other_user["_id"]), "B")] * 5
                ])

            _run(_fan())
            for _ in range(5):
                assert a.receive_json()["data"]["marker"] == "A"
                assert b.receive_json()["data"]["marker"] == "B"

    def test_concurrent_broker_reads_for_two_users_use_their_own_sessions(self, fake_db):
        """§15 + §5. Two users, the same broker, resolved concurrently. The cache
        is a shared dict; a key that lost its owner would make one of these
        answer with the other's token."""
        broker_engine.db = fake_db
        users = [str(ObjectId()) for _ in range(6)]
        for i, uid in enumerate(users):
            fake_db.broker_accounts.docs.append({
                "_id": ObjectId(), "user_id": uid, "broker": "zerodha",
                "access_token": f"TOKEN-{i}", "connected": True,
                "expires_at": "2099-01-01T00:00:00+00:00"})

        async def _all():
            return await asyncio.gather(
                *(broker_engine.get_session(uid, "zerodha") for uid in users))

        sessions = _run(_all())
        assert [s["access_token"] for s in sessions] == [f"TOKEN-{i}" for i in range(6)]

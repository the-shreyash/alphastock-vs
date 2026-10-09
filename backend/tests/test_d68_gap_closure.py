"""D6.8 gap closure — LIM-D6.8-3 (order routes vs `validate_trade`) and
LIM-D6.8-4 (the unauthenticated Zerodha postback).

THE TWO QUESTIONS
-----------------
1. "Can an irreversible broker order be reached without the checks that make it
   the caller's own order to place?"
2. "Can an anonymous caller change anything through `/api/zerodha/postback`?"

WHAT D6.8 RECORDED, AND WHAT TURNED OUT TO BE TRUE
--------------------------------------------------
LIM-D6.8-3 bundled two claims that needed separating:

* **"the direct routes skip `validate_trade`" — not a security finding.**
  `trading_engine.validate_trade(user, trade, trades_today, realized_today)` is
  a pure function of four values. It resolves no account, proves no ownership,
  consults no capability, reads no session and writes nothing. It is the user's
  own risk-discipline check over their own settings. Calling it is not a
  boundary and skipping it crosses none. §1 proves that from its signature and
  its behaviour rather than asserting it — including the positive control that
  a route which *does* call it still refuses the order the check rejects.
* **"`/zerodha/order` takes an unvalidated body" — a real defect, and wider
  than recorded.** Two routes read `await request.json()` and indexed it. §3
  and §4 pin the states that used to reach a live Kite account.

LIM-D6.8-4's premise was also partly wrong: the endpoint does NOT write
unboundedly *in count* — the platform-wide `PUBLIC_API` limiter caps anonymous
callers at 60 requests/minute per IP. It wrote unboundedly in *volume*: a 2 MB
body was stored verbatim, with no TTL and nothing that prunes. §5 is the attack
matrix; §6 pins the bound, the idempotency and the retention.

NO REAL BROKER IS CONTACTED. Every adapter order method is replaced by a spy for
the whole of §1–§4, and a spy that must not be called is asserted empty — never
merely "the status code was 4xx".
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from bson import ObjectId

import server
from models import BrokerOrderCreate, ZerodhaOrderCreate, ZerodhaQuickTradeCreate
from services import trading_engine
from services.broker_engine import broker_engine
from services.brokers import broker_registry
from services.brokers.accounts import BrokerAccountStatus
from tests._accounts import account_doc, fixture_account_id
from tests.conftest import _headers_for

_FUTURE = "2099-01-01T00:00:00+00:00"
_PAST = "2020-01-01T00:00:00+00:00"

#: A payload every order route accepts and every constraint permits.
_GOOD_ORDER = {
    "symbol": "RELIANCE",
    "exchange": "NSE",
    "transaction_type": "BUY",
    "quantity": 1,
    "order_type": "MARKET",
}
_GOOD_QUICK = {
    "symbol": "RELIANCE",
    "stock_name": "Reliance",
    "entry_price": 100.0,
    "quantity": 10,
    "stop_loss": 95.0,
    "target1": 110.0,
}


@pytest.fixture
def adapter_spy(monkeypatch):
    """Every registered adapter's irreversible order methods, replaced by a spy.

    The spy sits BELOW `BrokerGateway.require_capability` and below
    `BrokerEngine.get_session`, so an entry means the request reached the point
    where a real adapter would have spoken to a real broker. Same fixture shape
    as `test_d68_entitlements.adapter_spy`; it records the payload too, because
    this module needs to assert what would have been SENT and not only that
    something was.
    """
    calls = []
    for name in broker_registry.names():
        adapter = broker_registry.require(name)

        def _make(method, broker):
            async def _spy(session, *args, **kwargs):
                payload = args[0] if args else kwargs.get("order")
                calls.append(
                    {
                        "broker": broker,
                        "method": method,
                        "token": (session or {}).get("access_token"),
                        "payload": payload,
                    }
                )
                return {"order_id": f"SPY-{broker}-{len(calls)}", "status": "PENDING"}

            return _spy

        for method in ("place_order", "modify_order", "cancel_order"):
            monkeypatch.setattr(adapter, method, _make(method, name), raising=False)
    broker_engine._sessions.clear()
    return calls


@pytest.fixture
def accounts(fake_db, test_user, other_user):
    """A's and B's brokerage accounts.

    A holds a live, sole zerodha account (so the broker-name bridge resolves),
    a live upstox account, a REVOKED zerodha-less upstox account and an angelone
    account whose adapter cannot place orders. B holds one of each.
    """
    a, b = str(test_user["_id"]), str(other_user["_id"])

    def _add(owner, broker, suffix, ext, *, expires=_FUTURE, status=BrokerAccountStatus.CONNECTED):
        doc = account_doc(owner, broker, suffix=suffix, external_account_id=ext, status=status)
        fake_db.broker_accounts.docs.append(
            {
                "_id": ObjectId(),
                **doc,
                "access_token": f"TOKEN-{ext}",
                "expires_at": expires,
                "connected_at": "2026-09-13T03:00:00+00:00",
            }
        )
        return doc["broker_account_id"]

    return {
        "A_KITE": _add(a, "zerodha", "", "A-KITE"),
        "A_UPX": _add(a, "upstox", "1", "A-UPX"),
        "A_UPX_EXPIRED": _add(a, "upstox", "2", "A-UPX-OLD", expires=_PAST),
        "A_UPX_REVOKED": _add(a, "upstox", "3", "A-UPX-REV", status=BrokerAccountStatus.REVOKED),
        "A_ANGEL": _add(a, "angelone", "", "A-ANGEL"),
        "B_KITE": _add(b, "zerodha", "", "B-KITE"),
        "B_UPX": _add(b, "upstox", "", "B-UPX"),
        "UNKNOWN": fixture_account_id("nobody", "upstox", "ghost"),
    }


# =========================================================================== #
# §1 — `validate_trade` IS NOT AN AUTHORIZATION BOUNDARY (LIM-D6.8-3, part 1)  #
# =========================================================================== #
class TestValidateTradeIsNotASecurityBoundary:
    """The finding read "the direct routes skip `validate_trade`". These tests
    establish what skipping it can and cannot mean, so the verdict rests on the
    function's actual contract rather than on its name."""

    def test_it_takes_no_account_no_session_and_no_request(self):
        """Its whole input is (user, trade dict, two numbers).

        There is no `broker_account_id` parameter, no session, no db handle and
        no request — so it is not physically capable of resolving an account,
        proving ownership, reading a broker session or checking a capability.
        A function cannot enforce what it cannot see.
        """
        import inspect

        params = list(inspect.signature(trading_engine.validate_trade).parameters)
        assert params == ["user", "trade", "trades_today", "today_realized_pnl"]

    def test_it_writes_nothing_and_returns_only_a_verdict(self, fake_db, test_user):
        """No review artifact, no reservation, no token. Nothing downstream can
        later ask "was this order approved?" — which is the other reason calling
        it cannot be the boundary: it leaves no evidence to check."""
        before = {
            name: len(getattr(fake_db, name).docs)
            for name in ("trades", "orders", "broker_accounts", "activity", "notifications", "security_audit_logs")
        }
        result = trading_engine.validate_trade(test_user, dict(_GOOD_QUICK), 0, 0.0)
        assert set(result) == {"approved", "violations", "warnings", "metrics"}
        assert {name: len(getattr(fake_db, name).docs) for name in before} == before

    def test_its_verdict_is_decided_by_the_callers_own_settings(self, test_user):
        """It enforces the USER'S limits against the USER'S own trade. Change
        only `max_trades_per_day` on the user document and the same payload
        flips — which is the definition of a personal discipline check and not
        of an access-control decision."""
        permissive = {**test_user, "max_trades_per_day": 10}
        strict = {**test_user, "max_trades_per_day": 1}
        assert trading_engine.validate_trade(permissive, dict(_GOOD_QUICK), 5, 0.0)["approved"]
        assert not trading_engine.validate_trade(strict, dict(_GOOD_QUICK), 5, 0.0)["approved"]

    def test_the_relay_routes_carry_nothing_it_could_evaluate(self):
        """Why it is not simply bolted onto every order route.

        `validate_trade`'s violations are all relationships between entry, stop
        and targets. `BrokerOrderCreate` — the body of every relay route — has
        no `stop_loss`, no `target1` and no `entry_price`. Handed one, the
        function approves it vacuously, which would be worse than not calling
        it: a route that "runs the risk check" and can never fail it.
        """
        relay_fields = set(BrokerOrderCreate.model_fields)
        assert not relay_fields & {"stop_loss", "target1", "target2", "entry_price"}
        vacuous = trading_engine.validate_trade(
            {"capital": 100000}, BrokerOrderCreate(**_GOOD_ORDER).model_dump(), 0, 0.0
        )
        assert vacuous["approved"] is False or vacuous["violations"], (
            "a relay payload must not be silently approved — if this starts "
            "passing, the check has become vacuous rather than protective"
        )

    def test_positive_control_a_route_that_does_run_it_still_refuses(self, client, accounts, test_user, adapter_spy):
        """Guards the three tests above: they would be worthless if
        `validate_trade` had quietly stopped rejecting anything."""
        resp = client.post(
            "/api/trades",
            json={
                "symbol": "INFY",
                "stock_name": "Infosys",
                "type": "BUY",
                "entry_price": 100.0,
                "quantity": 50,
                "stop_loss": 150.0,
                "target1": 104.0,
                "broker_account_id": accounts["A_UPX"],
            },
            headers=_headers_for(test_user),
        )
        assert resp.status_code == 422
        assert "Stop loss must be below" in json.dumps(resp.json())
        assert adapter_spy == []


# =========================================================================== #
# §2 — EVERY IRREVERSIBLE ROUTE ENFORCES THE CHAIN ITSELF                      #
# =========================================================================== #
#: Every route handler in `server.py` that can reach
#: `broker_engine.place_order` / `modify_order` / `cancel_order`, mapped to the
#: owner-scoped resolver it uses. This is the list §2 walks, and the static test
#: below fails when a handler is added, removed or loses its resolver — so the
#: map cannot silently fall behind the code it describes.
IRREVERSIBLE_ORDER_HANDLERS = {
    "create_trade": "_account/_sole_account",
    "exit_trade": "_trade_broker_account",
    "broker_account_place_order": "_account",
    "broker_account_modify_order": "_account",
    "broker_account_cancel_order": "_account",
    "broker_place_order": "_sole_account",
    "broker_modify_order": "_sole_account",
    "broker_cancel_order": "_sole_account",
    "zerodha_order": "_sole_account",
    "zerodha_cancel": "_sole_account",
    "zerodha_quick_trade": "_sole_account",
    "zerodha_emergency_stop": "_sole_account",
}

_RESOLVERS = ("_account", "_sole_account", "_trade_broker_account")


def _handlers_reaching_the_broker() -> dict:
    """Route handlers in `server.py` whose own body calls an irreversible
    `broker_engine` order method, with the resolvers they call."""
    tree = ast.parse(Path(server.__file__).read_text())
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)]
        reaches = any(
            isinstance(c.func, ast.Attribute)
            and c.func.attr in ("place_order", "modify_order", "cancel_order")
            and isinstance(c.func.value, ast.Name)
            and c.func.value.id == "broker_engine"
            for c in calls
        )
        if not reaches:
            continue
        resolvers = {c.func.id for c in calls if isinstance(c.func, ast.Name) and c.func.id in _RESOLVERS}
        found[node.name] = resolvers
    return found


class TestEveryIrreversibleRouteResolvesAnOwnedAccount:
    def test_the_set_of_handlers_is_exactly_the_pinned_set(self):
        """A new order route that nobody thought to review shows up here."""
        assert set(_handlers_reaching_the_broker()) == set(IRREVERSIBLE_ORDER_HANDLERS)

    def test_each_one_resolves_through_an_owner_scoped_helper(self):
        """`_account`, `_sole_account` and `_trade_broker_account` are the only
        three ways to obtain a `BrokerAccountRef` in a request, and each of them
        scopes by `str(user["_id"])`. A handler that reaches the broker without
        calling one of them has taken an account from somewhere else."""
        for name, resolvers in _handlers_reaching_the_broker().items():
            assert resolvers & set(_RESOLVERS), f"{name} reaches a broker with no owner-scoped resolver"

    def test_each_one_requires_an_authenticated_identity(self):
        """The resolvers scope by the authenticated user, so the route must have
        one. Read off the live dependency tree, not the source."""
        from tests._routes import PUBLIC_ROUTES

        by_name = {r.endpoint.__name__: r for r in server.app.routes if getattr(r, "endpoint", None) is not None}
        for name in IRREVERSIBLE_ORDER_HANDLERS:
            route = by_name[name]
            for method in route.methods - {"HEAD", "OPTIONS"}:
                assert (
                    method,
                    route.path,
                ) not in PUBLIC_ROUTES, f"{method} {route.path} reaches a broker order with no identity"

    @pytest.mark.parametrize(
        "target,expected",
        [
            ("B_KITE", 404),
            ("B_UPX", 404),
            ("UNKNOWN", 404),
            ("A_UPX_EXPIRED", 409),
            ("A_UPX_REVOKED", 409),
            ("A_ANGEL", 502),
        ],
    )
    def test_account_addressed_placement_never_reaches_the_adapter(
        self, client, accounts, test_user, adapter_spy, target, expected
    ):
        """TEST C/D/E/F in one table: a foreign account, an unknown account, an
        expired session, a withdrawn account and an uncapable broker."""
        resp = client.post(
            f"/api/brokers/accounts/{accounts[target]}/orders", json=_GOOD_ORDER, headers=_headers_for(test_user)
        )
        assert resp.status_code == expected, resp.text
        assert adapter_spy == []

    def test_positive_control_the_owner_reaches_it_exactly_once(self, client, accounts, test_user, adapter_spy):
        resp = client.post(
            f"/api/brokers/accounts/{accounts['A_UPX']}/orders", json=_GOOD_ORDER, headers=_headers_for(test_user)
        )
        assert resp.status_code == 200, resp.text
        assert [(c["broker"], c["method"], c["token"]) for c in adapter_spy] == [
            ("upstox", "place_order", "TOKEN-A-UPX")
        ]

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("POST", "/api/zerodha/order", _GOOD_ORDER),
            ("POST", "/api/zerodha/quick-trade", _GOOD_QUICK),
            ("POST", "/api/zerodha/emergency-stop", None),
            ("DELETE", "/api/zerodha/order/ORD-1", None),
            ("POST", "/api/brokers/zerodha/orders", _GOOD_ORDER),
            ("PATCH", "/api/brokers/zerodha/orders/ORD-1", {"quantity": 2}),
            ("DELETE", "/api/brokers/zerodha/orders/ORD-1", None),
        ],
    )
    def test_no_identity_reaches_no_broker(self, client, fake_db, accounts, adapter_spy, method, path, body):
        """TEST G. No credential at all, on every legacy zerodha order route."""
        resp = client.request(method, path, json=body)
        assert resp.status_code == 401, resp.text
        assert adapter_spy == []

    #: The four legacy zerodha routes and the status each returns when the
    #: caller holds no account there. They disagree — 200-with-FAILED, 502, 500
    #: — because each swallows `BrokerAuthError` its own way rather than letting
    #: the app's handler map it to 409. That is LIM-D6.8-6's shape (a
    #: fail-closed refusal reported as something else) and is recorded here, not
    #: fixed: changing an order route's status code is a contract change, and
    #: the security property below holds at every one of them.
    LEGACY_NO_ACCOUNT_STATUS = [
        ("POST", "/api/zerodha/order", _GOOD_ORDER, 200),
        ("POST", "/api/zerodha/quick-trade", _GOOD_QUICK, 502),
        ("POST", "/api/zerodha/emergency-stop", None, 500),
        ("DELETE", "/api/zerodha/order/ORD-1", None, 200),
    ]

    @pytest.mark.parametrize("method,path,body,expected", LEGACY_NO_ACCOUNT_STATUS)
    def test_a_user_with_no_zerodha_account_borrows_nobodys(
        self, client, fake_db, accounts, other_user, adapter_spy, method, path, body, expected
    ):
        """B holds a live zerodha account and these routes are broker-NAMED, so
        the only thing between this request and B's account is the owner-scoped
        bridge. The caller here is a third user who holds none at all.

        Asserting the status alone would be weak — two of these answer 200. The
        load-bearing assertions are that no adapter was reached, no trade row was
        written and no order was recorded.
        """
        from tests.conftest import _seed_user

        third = _seed_user(fake_db, "third@example.com", "user", "Third")
        resp = client.request(method, path, json=body, headers=_headers_for(third))
        assert resp.status_code == expected, resp.text
        assert "not connected" in resp.text.lower()
        assert adapter_spy == [], "borrowed another user's broker account"
        assert fake_db.trades.docs == []
        assert fake_db.orders.docs == []

    def test_client_supplied_authority_cannot_redirect_a_legacy_order(
        self, client, accounts, test_user, other_user, adapter_spy
    ):
        """TEST H on the routes the finding named. `user_id`, `broker`,
        `broker_account_id`, `role`, `is_paper`, `entitlement` and `capability`
        in the body change nothing: the order lands in A's own zerodha account
        and carries A's own token."""
        resp = client.post(
            "/api/zerodha/order",
            json={
                **_GOOD_ORDER,
                "user_id": str(other_user["_id"]),
                "broker": "upstox",
                "broker_account_id": accounts["B_KITE"],
                "role": "super_admin",
                "admin": True,
                "is_paper": True,
                "entitlement": "pro",
                "capabilities": ["PLACE_ORDER"],
            },
            headers=_headers_for(test_user),
        )
        assert resp.status_code == 200, resp.text
        assert [(c["broker"], c["token"]) for c in adapter_spy] == [("zerodha", "TOKEN-A-KITE")]

    def test_a_forged_body_cannot_file_a_quick_trade_under_another_user(
        self, client, fake_db, accounts, test_user, other_user, adapter_spy
    ):
        resp = client.post(
            "/api/zerodha/quick-trade",
            json={**_GOOD_QUICK, "user_id": str(other_user["_id"]), "is_paper": True, "role": "admin"},
            headers=_headers_for(test_user),
        )
        assert resp.status_code == 200, resp.text
        assert len(fake_db.trades.docs) == 1
        row = fake_db.trades.docs[0]
        assert str(row["user_id"]) == str(test_user["_id"])
        assert row.get("is_paper") in (None, False), "a live broker order was filed as a paper trade"


# =========================================================================== #
# §3 — THE LEGACY BODIES ARE VALIDATED BEFORE THE BROKER (G-1)                 #
# =========================================================================== #
#: Every payload that used to reach a live Kite account through
#: `POST /api/zerodha/order` and be answered 200. Each is refused by
#: `/api/brokers/accounts/{id}/orders` and now by this route too — the test
#: asserts BOTH, so the two contracts cannot drift apart again.
REJECTED_ORDER_BODIES = [
    ("negative quantity", {"symbol": "RELIANCE", "quantity": -50}),
    ("zero quantity", {"symbol": "RELIANCE", "quantity": 0}),
    ("quantity over the cap", {"symbol": "RELIANCE", "quantity": 1_000_000_000}),
    ("fractional quantity", {"symbol": "RELIANCE", "quantity": 1.5}),
    ("non-numeric quantity", {"symbol": "RELIANCE", "quantity": "abc"}),
    ("unknown side", {"symbol": "RELIANCE", "quantity": 1, "transaction_type": "STEAL"}),
    ("unknown order type", {"symbol": "RELIANCE", "quantity": 1, "order_type": "WHATEVER"}),
    ("negative price", {"symbol": "RELIANCE", "quantity": 1, "price": -999}),
    ("missing symbol", {"quantity": 1}),
    ("missing quantity", {"symbol": "RELIANCE"}),
    ("empty body", {}),
    ("symbol is an object", {"symbol": {"$ne": None}, "quantity": 1}),
    ("oversized symbol", {"symbol": "A" * 5000, "quantity": 1}),
]


class TestTheLegacyZerodhaOrderBodyIsValidated:
    @pytest.mark.parametrize("label,body", REJECTED_ORDER_BODIES, ids=[b[0] for b in REJECTED_ORDER_BODIES])
    def test_it_is_refused_before_any_account_is_resolved(self, client, accounts, test_user, adapter_spy, label, body):
        resp = client.post("/api/zerodha/order", json=body, headers=_headers_for(test_user))
        assert resp.status_code == 422, f"{label}: {resp.status_code} {resp.text}"
        assert adapter_spy == [], f"{label}: reached the adapter"

    @pytest.mark.parametrize("label,body", REJECTED_ORDER_BODIES, ids=[b[0] for b in REJECTED_ORDER_BODIES])
    def test_the_account_addressed_route_refuses_the_same_payload(
        self, client, accounts, test_user, adapter_spy, label, body
    ):
        """The drift check. If a bound is ever loosened on one of the two
        routes, one of these two parametrizations goes red."""
        resp = client.post(
            f"/api/brokers/accounts/{accounts['A_KITE']}/orders", json=body, headers=_headers_for(test_user)
        )
        assert resp.status_code == 422, f"{label}: {resp.status_code}"
        assert adapter_spy == []

    def test_a_missing_field_is_a_422_and_not_a_500(self, client, accounts, test_user, adapter_spy):
        """It used to raise `KeyError` out of the handler. A 500 on an order
        route is not merely untidy: it is indistinguishable from "the broker
        broke", which is the one failure a caller is expected to retry."""
        resp = client.post("/api/zerodha/order", json={"symbol": "RELIANCE"}, headers=_headers_for(test_user))
        assert resp.status_code == 422
        assert adapter_spy == []

    def test_the_defaults_a_valid_request_relies_on_did_not_move(self, client, accounts, test_user, adapter_spy):
        """A validation fix must not change what a VALID order does. An omitted
        `order_type` still means LIMIT here (`BrokerOrderCreate` would have made
        it MARKET) and an omitted `product` still means MIS."""
        resp = client.post(
            "/api/zerodha/order",
            json={"symbol": "RELIANCE", "quantity": 7, "price": 100.0},
            headers=_headers_for(test_user),
        )
        assert resp.status_code == 200, resp.text
        assert len(adapter_spy) == 1
        sent = adapter_spy[0]["payload"]
        assert sent["order_type"] == "LIMIT"
        assert sent["product"] == "MIS"
        assert sent["exchange"] == "NSE"
        assert sent["transaction_type"] == "BUY"
        assert sent["quantity"] == 7 and sent["price"] == 100.0

    #: `json.dumps` emits these, `json.loads` accepts them, and Starlette parses
    #: request bodies with `json.loads`. They cannot be sent through `json=`,
    #: which is exactly why they went unnoticed: every test that tried used the
    #: client's serializer and got a `ValueError` in the test process instead of
    #: a request.
    NON_FINITE_ORDER_BODIES = [
        ("infinite price", b'{"symbol":"RELIANCE","quantity":1,"price":Infinity}'),
        ("negative infinite price", b'{"symbol":"RELIANCE","quantity":1,"price":-Infinity}'),
        ("NaN price", b'{"symbol":"RELIANCE","quantity":1,"price":NaN}'),
        ("infinite trigger price", b'{"symbol":"RELIANCE","quantity":1,"trigger_price":Infinity}'),
        ("infinite quantity", b'{"symbol":"RELIANCE","quantity":Infinity}'),
    ]

    @pytest.mark.parametrize("label,raw", NON_FINITE_ORDER_BODIES, ids=[b[0] for b in NON_FINITE_ORDER_BODIES])
    @pytest.mark.parametrize(
        "route",
        [
            "/api/zerodha/order",
            "ACCOUNT",  # resolved to the account-addressed route below
        ],
    )
    def test_a_non_finite_price_never_reaches_the_broker(
        self, client, accounts, test_user, adapter_spy, route, label, raw
    ):
        """G-7, and the one finding that was live on a route D6.8 had already
        certified. `Field(ge=0)` ADMITS `Infinity` — `inf >= 0` is True — so
        `{"price": Infinity}` passed validation everywhere and `price: inf` was
        handed to the adapter. `NaN` was refused, but only because `nan >= 0` is
        False: an accident of comparison rather than a bound. Both routes are
        asserted because the constraint lives on the shared model, and a fix
        applied to one of them would be the drift this module exists to stop.
        """
        path = f"/api/brokers/accounts/{accounts['A_KITE']}/orders" if route == "ACCOUNT" else route
        resp = client.post(path, content=raw, headers={**_headers_for(test_user), "content-type": "application/json"})
        assert resp.status_code == 422, f"{label}: {resp.status_code} {resp.text}"
        assert adapter_spy == [], f"{label}: a non-finite order reached the adapter"

    def test_a_non_finite_price_never_reaches_a_modify(self, client, accounts, test_user, adapter_spy):
        """Modifying a live order is as irreversible as placing one."""
        resp = client.patch(
            f"/api/brokers/accounts/{accounts['A_KITE']}/orders/ORD-1",
            content=b'{"price":Infinity}',
            headers={**_headers_for(test_user), "content-type": "application/json"},
        )
        assert resp.status_code == 422, resp.text
        assert adapter_spy == []

    def test_a_non_finite_target_is_not_written_to_the_journal(self, client, fake_db, accounts, test_user, adapter_spy):
        """`TradeCreate.target2` was a bare `Optional[float]` — no bound at all.
        The row was WRITTEN with `target2: inf` and the request then failed
        serializing its own response, so the caller saw a 500 and the journal
        kept an infinite target that every downstream aggregate would inherit.
        """
        resp = client.post(
            "/api/trades",
            content=(
                b'{"symbol":"INFY","stock_name":"Infosys","type":"BUY",'
                b'"entry_price":100.0,"quantity":10,"stop_loss":95.0,'
                b'"target1":110.0,"target2":Infinity}'
            ),
            headers={**_headers_for(test_user), "content-type": "application/json"},
        )
        assert resp.status_code == 422, resp.text
        assert fake_db.trades.docs == [], "an infinite target was persisted"
        assert adapter_spy == []

    def test_the_model_inherits_rather_than_restates_the_order_contract(self):
        """The reason the two routes cannot drift: one set of constraints."""
        assert issubclass(ZerodhaOrderCreate, BrokerOrderCreate)
        inherited = set(BrokerOrderCreate.model_fields) - set(ZerodhaOrderCreate.__annotations__)
        assert {"symbol", "quantity", "transaction_type", "price"} <= inherited


# =========================================================================== #
# §4 — QUICK-TRADE: BOUNDS AND THE RISK MANAGER (G-2)                          #
# =========================================================================== #
REJECTED_QUICK_TRADES = [
    ("negative quantity", {"quantity": -5}, 422),
    ("zero quantity", {"quantity": 0}, 422),
    ("quantity over the cap", {"quantity": 1_000_000_000}, 422),
    ("negative entry price", {"entry_price": -100.0}, 422),
    ("zero entry price", {"entry_price": 0.0}, 422),
    ("negative stop", {"stop_loss": -1.0}, 422),
    ("oversized symbol", {"symbol": "A" * 5000}, 422),
    # Reaches the Risk Manager rather than the schema: the numbers are each
    # individually legal, their RELATIONSHIP is not.
    ("stop above entry on a BUY", {"stop_loss": 150.0}, 422),
    ("target below entry on a BUY", {"target1": 90.0}, 422),
]


class TestQuickTradeCannotPlaceAnUnboundedOrLossMakingOrder:
    @pytest.mark.parametrize("label,patch,expected", REJECTED_QUICK_TRADES, ids=[q[0] for q in REJECTED_QUICK_TRADES])
    def test_it_is_refused_before_the_broker_and_writes_no_trade(
        self, client, fake_db, accounts, test_user, adapter_spy, label, patch, expected
    ):
        resp = client.post("/api/zerodha/quick-trade", json={**_GOOD_QUICK, **patch}, headers=_headers_for(test_user))
        assert resp.status_code == expected, f"{label}: {resp.text}"
        assert adapter_spy == [], f"{label}: reached the adapter"
        assert fake_db.trades.docs == [], f"{label}: wrote a trade row"
        assert fake_db.notifications.docs == [], f"{label}: notified"

    def test_the_daily_trade_limit_stops_the_one_click_path(self, client, fake_db, accounts, test_user, adapter_spy):
        """The reason the Risk Manager belongs here and not only on the trade
        form: this is the FASTEST path to an order in the product, and it is the
        one a user clicks when they are chasing a loss.

        Driven entirely through the app — the first order is placed by this
        route and counted by the same query the gate reads, so the test cannot
        be wrong about the shape of a row it did not write itself.
        """
        for doc in fake_db.users.docs:
            if str(doc["_id"]) == str(test_user["_id"]):
                doc["max_trades_per_day"] = 1

        first = client.post("/api/zerodha/quick-trade", json=_GOOD_QUICK, headers=_headers_for(test_user))
        assert first.status_code == 200, first.text
        assert len(adapter_spy) == 1, "positive control: the first order goes through"

        second = client.post(
            "/api/zerodha/quick-trade", json={**_GOOD_QUICK, "symbol": "TCS"}, headers=_headers_for(test_user)
        )
        assert second.status_code == 422, second.text
        assert "Daily trade limit" in json.dumps(second.json())
        assert len(adapter_spy) == 1, "the refused order still reached the broker"
        assert len(fake_db.trades.docs) == 1
        assert all(t["symbol"] != "TCS" for t in fake_db.trades.docs)

    def test_positive_control_a_sound_quick_trade_still_places_and_records(
        self, client, fake_db, accounts, test_user, adapter_spy
    ):
        """Guards every test above: they would all pass on a route that refused
        everything."""
        resp = client.post("/api/zerodha/quick-trade", json=_GOOD_QUICK, headers=_headers_for(test_user))
        assert resp.status_code == 200, resp.text
        assert [(c["broker"], c["method"]) for c in adapter_spy] == [("zerodha", "place_order")]
        assert adapter_spy[0]["payload"]["quantity"] == 10
        assert len(fake_db.trades.docs) == 1
        assert fake_db.trades.docs[0]["status"] == "OPEN"

    def test_the_model_shares_the_trade_entry_vocabulary(self):
        """`TradeCreate`, `PaperTradeCreate` and this model are the three
        surfaces that write a `db.trades` row. PH3.12R/B-1 exists because two of
        them drifted; this asserts the third is spelled in the same aliases."""
        from models import PaperTradeCreate, TradeCreate

        for field in ("quantity", "entry_price", "stop_loss", "target1"):
            annotations = {
                m.model_fields[field].annotation for m in (TradeCreate, PaperTradeCreate, ZerodhaQuickTradeCreate)
            }
            metadata = {
                str(m.model_fields[field].metadata) for m in (TradeCreate, PaperTradeCreate, ZerodhaQuickTradeCreate)
            }
            assert (
                len(annotations) == 1 and len(metadata) == 1
            ), f"{field} is constrained differently across the three entry models"


# =========================================================================== #
# §5 — THE POSTBACK ATTACK MATRIX (LIM-D6.8-4)                                 #
# =========================================================================== #
#: Collections the postback must never touch. `zerodha_postbacks` is the one it
#: may, and is excluded on purpose.
_UNTOUCHED = (
    "orders",
    "trades",
    "broker_accounts",
    "users",
    "notifications",
    "activity",
    "sessions",
    "security_audit_logs",
    "chat_messages",
)


def _snapshot(fake_db):
    return {name: [json.dumps(d, default=str) for d in getattr(fake_db, name).docs] for name in _UNTOUCHED}


POSTBACK_ATTACKS = [
    (
        "A valid-looking anonymous payload",
        {"order_id": "250101000000001", "status": "COMPLETE", "tradingsymbol": "RELIANCE"},
    ),
    ("C arbitrary user_id", {"order_id": "X", "user_id": "victim", "status": "COMPLETE"}),
    ("D arbitrary broker_account_id", {"order_id": "X", "broker_account_id": "bacc_ffffffffffffffffffffffffffffffff"}),
    ("E arbitrary order_id", {"order_id": "NOT-A-REAL-ORDER", "status": "COMPLETE"}),
    ("G nonexistent order", {"order_id": "999999999999999", "status": "COMPLETE"}),
    ("I unexpected extra fields", {"order_id": "X", "role": "admin", "is_paper": False, "capital": 1e9}),
    ("J missing required fields", {}),
    ("K forged status", {"order_id": "X", "status": "COMPLETE", "filled_quantity": 999999}),
    ("L forged transition", {"order_id": "X", "status": "COMPLETE", "previous_status": "CANCELLED"}),
]


class TestThePostbackChangesNothing:
    @pytest.mark.parametrize("label,body", POSTBACK_ATTACKS, ids=[a[0] for a in POSTBACK_ATTACKS])
    def test_no_collection_but_the_recorder_moves(self, client, fake_db, accounts, test_user, adapter_spy, label, body):
        """The strong form of "nothing reads it": seed a full world first — two
        users, five brokerage accounts, a trade — then assert every other
        collection is byte-identical afterwards, and that no adapter was called.
        A status-code assertion alone would pass on an endpoint that quietly
        rewrote an order."""
        client.post(
            "/api/trades",
            json={
                "symbol": "INFY",
                "stock_name": "Infosys",
                "type": "BUY",
                "entry_price": 100.0,
                "quantity": 10,
                "stop_loss": 95.0,
                "target1": 110.0,
            },
            headers=_headers_for(test_user),
        )
        before = _snapshot(fake_db)
        spy_before = list(adapter_spy)

        resp = client.post("/api/zerodha/postback", json=body)

        assert resp.status_code == 200
        assert _snapshot(fake_db) == before, f"{label}: mutated platform state"
        assert adapter_spy == spy_before, f"{label}: called a broker"

    @pytest.mark.parametrize("label,body", POSTBACK_ATTACKS, ids=[a[0] for a in POSTBACK_ATTACKS])
    def test_the_response_is_identical_for_every_payload(self, client, fake_db, label, body):
        """TEST H / information disclosure: a caller cannot tell a real order id
        from a fabricated one, or another user's from its own. The response is
        the same two bytes of status every time."""
        resp = client.post("/api/zerodha/postback", json=body)
        assert resp.json() == {"status": "ok"}
        assert set(resp.headers) >= {"content-type"}

    @pytest.mark.parametrize(
        "label,raw",
        [
            ("B malformed JSON", b"<<<not json at all>>>"),
            ("array body", b"[1, 2, 3]"),
            ("scalar body", b"42"),
            ("null body", b"null"),
            ("string body", b'"COMPLETE"'),
        ],
    )
    def test_a_non_object_body_is_refused_and_leaves_no_row(self, client, fake_db, label, raw):
        """G-4. A JSON array used to be INSERTED and then answered
        `{"status": "error"}` — the row was written before `body.get(...)` raised
        on it, so the endpoint's answer and its effect disagreed."""
        resp = client.post("/api/zerodha/postback", content=raw, headers={"content-type": "application/json"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "error"}
        assert fake_db.zerodha_postbacks.docs == [], f"{label}: recorded a non-object"

    #: The only operations any production module may perform on
    #: `db.zerodha_postbacks`: the writer and its two indexes.
    _PERMITTED_POSTBACK_OPS = frozenset({"update_one", "create_index"})

    def test_nothing_in_the_platform_reads_the_collection(self):
        """The load-bearing fact behind "LOW severity". `zerodha_postbacks` is
        written in exactly one place and read in none — so a forged record has
        no consumer to mislead. This fails the moment someone writes one, which
        is precisely when the checksum gate (LIM-D6.8-4) must be closed first.

        Matched on the AST rather than line-by-line: `await db.zerodha_postbacks
        .update_one(...)` wraps across lines, so a textual scan either misses
        the attribute or reports the writer as a reader.
        """
        root = Path(server.__file__).parent
        offenders = []
        for path in sorted(root.rglob("*.py")):
            if {"venv", "tests", "__pycache__"} & set(path.parts):
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                # `<anything>.zerodha_postbacks` — the collection handle itself.
                if not (isinstance(node, ast.Attribute) and node.attr == "zerodha_postbacks"):
                    continue
                parent = next((p for p in ast.walk(tree) if isinstance(p, ast.Attribute) and p.value is node), None)
                op = parent.attr if parent is not None else "<bare reference>"
                if op not in self._PERMITTED_POSTBACK_OPS:
                    offenders.append(f"{path.relative_to(root)}:{node.lineno} .{op}")
        assert offenders == [], (
            "db.zerodha_postbacks acquired an operation beyond its writer and "
            "its indexes; an unauthenticated webhook now has a consumer, and "
            f"the Kite checksum (LIM-D6.8-4) must be verified first: {offenders}"
        )


# =========================================================================== #
# §6 — THE POSTBACK IS BOUNDED, IDEMPOTENT AND EXPIRING (G-3, G-5, G-6)        #
# =========================================================================== #
class TestThePostbackIsBounded:
    def test_an_oversized_body_is_refused_before_it_is_stored(self, client, fake_db):
        """G-3, the only reachable harm this endpoint had. A 2 MB body was
        stored verbatim; MongoDB takes documents up to 16 MB; there was no TTL
        and nothing pruned. At the anonymous rate limit that is roughly a
        gigabyte a minute of database growth per source address."""
        body = json.dumps({"order_id": "X", "junk": "A" * (2 * 1024 * 1024)})
        resp = client.post("/api/zerodha/postback", content=body.encode(), headers={"content-type": "application/json"})
        assert resp.status_code == 413
        assert fake_db.zerodha_postbacks.docs == []

    def test_a_body_at_the_limit_is_still_accepted(self, client, fake_db):
        """The bound must not be so tight that a real postback is dropped."""
        padding = server.ZERODHA_POSTBACK_MAX_BYTES - 200
        body = json.dumps({"order_id": "X", "junk": "A" * padding})
        assert len(body) <= server.ZERODHA_POSTBACK_MAX_BYTES
        resp = client.post("/api/zerodha/postback", content=body.encode(), headers={"content-type": "application/json"})
        assert resp.status_code == 200 and resp.json() == {"status": "ok"}
        assert len(fake_db.zerodha_postbacks.docs) == 1

    def test_a_body_with_no_declared_length_is_still_bounded(self, client, fake_db):
        """The two size checks are layered, and a test that cannot tell them
        apart proves neither.

        A chunked request carries NO `Content-Length`, so the cheap header
        pre-check cannot fire and only the measurement of the bytes actually
        read can refuse it. Without this the header check alone satisfied every
        size assertion, and deleting the body check changed nothing any test
        could see.
        """
        oversized = json.dumps({"order_id": "X", "junk": "A" * (200 * 1024)}).encode()

        def chunks():
            yield oversized

        resp = client.post("/api/zerodha/postback", content=chunks(), headers={"content-type": "application/json"})
        assert resp.request.headers.get("content-length") is None
        assert resp.request.headers.get("transfer-encoding") == "chunked"
        assert resp.status_code == 413
        assert fake_db.zerodha_postbacks.docs == []

    def test_a_declared_oversize_is_refused_without_reading_the_body(self, client, fake_db):
        """And the other half: the header check's whole value is that a 99 MB
        upload is never buffered into the worker's memory just to be measured
        and thrown away. The generator below records whether it was consumed —
        so this fails if the refusal moves to after the read, which a status
        code alone could never show.
        """
        consumed = []

        def chunks():
            consumed.append("body was read")
            yield b'{"order_id":"X"}'

        resp = client.post(
            "/api/zerodha/postback",
            content=chunks(),
            headers={"content-type": "application/json", "content-length": str(99 * 1024 * 1024)},
        )
        assert resp.status_code == 413
        assert consumed == [], "an oversized body was buffered before being refused"
        assert fake_db.zerodha_postbacks.docs == []

    def test_a_realistic_kite_postback_fits_with_room_to_spare(self, client, fake_db):
        kite_like = {
            "user_id": "AB1234",
            "unfilled_quantity": 0,
            "app_id": 1234,
            "checksum": "a" * 64,
            "placed_by": "AB1234",
            "order_id": "250101000000001",
            "exchange_order_id": "1000000000000000",
            "parent_order_id": None,
            "status": "COMPLETE",
            "status_message": None,
            "status_message_raw": None,
            "order_timestamp": "2026-01-01 09:20:00",
            "exchange_update_timestamp": "2026-01-01 09:20:00",
            "exchange_timestamp": "2026-01-01 09:20:00",
            "variety": "regular",
            "exchange": "NSE",
            "tradingsymbol": "RELIANCE",
            "instrument_token": 738561,
            "order_type": "MARKET",
            "transaction_type": "BUY",
            "validity": "DAY",
            "product": "CNC",
            "quantity": 1,
            "disclosed_quantity": 0,
            "price": 0,
            "trigger_price": 0,
            "average_price": 2890.5,
            "filled_quantity": 1,
            "market_protection": 0,
            "meta": {},
            "tag": None,
            "guid": "x" * 20,
        }
        raw = json.dumps(kite_like).encode()
        assert len(raw) < server.ZERODHA_POSTBACK_MAX_BYTES / 10
        resp = client.post("/api/zerodha/postback", content=raw, headers={"content-type": "application/json"})
        assert resp.status_code == 200
        assert fake_db.zerodha_postbacks.docs[0]["data"]["order_id"] == "250101000000001"

    def test_duplicate_delivery_collapses_to_one_row(self, client, fake_db):
        """TEST F / G-5. Kite retries; an attacker replays. Both are identical
        bytes and both produce exactly one record."""
        body = {"order_id": "250101000000001", "status": "COMPLETE"}
        for _ in range(20):
            assert client.post("/api/zerodha/postback", json=body).status_code == 200
        assert len(fake_db.zerodha_postbacks.docs) == 1

    def test_a_forged_variant_cannot_overwrite_a_genuine_record(self, client, fake_db):
        """Why the dedupe key is a digest of the body and not `order_id`.

        Keyed on `order_id`, this second request would have REPLACED the first
        record's status — inert while nothing reads the collection, and a
        planted lie for the first consumer that does.
        """
        genuine = {"order_id": "250101000000001", "status": "REJECTED"}
        forged = {"order_id": "250101000000001", "status": "COMPLETE"}
        client.post("/api/zerodha/postback", json=genuine)
        client.post("/api/zerodha/postback", json=forged)
        stored = [d["data"]["status"] for d in fake_db.zerodha_postbacks.docs]
        assert "REJECTED" in stored, "a forged replay overwrote the genuine record"
        assert len(fake_db.zerodha_postbacks.docs) == 2

    def test_every_stored_row_carries_an_expiry(self, client, fake_db):
        """G-6. The TTL index reaps on this field; a row written without one
        would live forever and the index would never notice."""
        client.post("/api/zerodha/postback", json={"order_id": "X"})
        row = fake_db.zerodha_postbacks.docs[0]
        assert "expires_at" in row
        assert isinstance(row["expires_at"], server.datetime), "a TTL index only reaps a BSON Date, never an ISO string"
        assert row["expires_at"] > server.datetime.now(server.timezone.utc)

    def test_the_ttl_and_uniqueness_indexes_are_actually_created(self):
        """The expiry field is inert without the index that reads it, and the
        idempotency is only advisory without the unique constraint behind it."""
        source = Path(server.__file__).read_text()
        assert 'db.zerodha_postbacks.create_index("expires_at", expireAfterSeconds=0)' in source
        assert 'db.zerodha_postbacks.create_index("body_sha256", unique=True)' in source

    def test_the_endpoint_is_rate_limited_like_any_other_anonymous_route(self, client, fake_db):
        """TEST N. Not a new control — the platform-wide `PUBLIC_API` limiter
        already covers it, which is why LIM-D6.8-4's "writes unboundedly" was
        true of volume and not of count. Pinned so an exemption added later is
        a visible decision."""
        from security.rate_limit import _MIDDLEWARE_EXEMPT_PATHS

        assert "/api/zerodha/postback" not in _MIDDLEWARE_EXEMPT_PATHS
        codes = {client.post("/api/zerodha/postback", json={"order_id": str(i)}).status_code for i in range(120)}
        assert 429 in codes, "an anonymous flood was never throttled"

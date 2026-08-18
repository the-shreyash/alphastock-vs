"""B-1 regression: `POST /api/paper/trade` must reject hostile input WITHOUT
mutating anything (PH3.12R).

THE DEFECT THIS SUITE EXISTS TO KEEP CLOSED
-------------------------------------------
PH3.12 sent `{"quantity": -1000}` to `/api/paper/trade` and got **200**. The
service computed `total_cost = entry_price * quantity = -1,000,000`, and
`update_paper_balance(user_id, -total_cost)` therefore *added* a million rupees
to the caller's paper account. One request moved a balance from ₹86,840 to
₹10,86,840; the inflation was unbounded and repeatable. The identical payload
sent to `/api/trades` was rejected with 422, because the real-trade model had
the bounds and the paper model — declared inline in `server.py`, 5,000 lines
from its sibling — had none.

Nothing here is about authorization. B-1 crossed no user boundary and touched no
real money. What it corrupted was the paper P&L, the trade journal and the
per-user performance analytics: the numbers PH3.9 spent a whole sprint making
truthful. A user could fabricate their own track record.

WHY THE ASSERTIONS ARE ABOUT *STATE*, NOT ABOUT STATUS CODES
------------------------------------------------------------
A 422 that arrives after the balance has already been written is not a fix, and
a suite that only reads `response.status_code` cannot tell the two apart. So
every rejection test in `TestRejectedRequestsMutateNothing` snapshots the whole
account — balance, trade documents, open positions, realised and unrealised P&L
— sends the hostile request, and asserts the snapshot is byte-identical
afterwards. That assertion would have failed against the pre-fix code for the
right reason.

WHY THE EXPLOIT TEST PINS THE FIELD NAME
----------------------------------------
`PaperTradeCreate` now also forbids unknown keys, so a *badly written* probe can
get its 422 from a stray field and pass without ever exercising the quantity
bound — the same "the probe could not have failed" flaw that made PH3.11 certify
B-2 closed on a route that never existed. `test_ph312_exploit_*` therefore sends
a payload that is valid in every respect except `quantity`, and asserts the
error is reported against `quantity` specifically.
"""
import copy

import pytest
from unittest.mock import AsyncMock, patch

TRADE_URL = "/api/paper/trade"
BALANCE_URL = "/api/paper/balance"
PNL_URL = "/api/paper/pnl"
TRADES_URL = "/api/paper/trades"

#: A payload that is correct in every field. Every hostile case below is this
#: dict with exactly ONE key replaced, so a rejection can only be attributed to
#: the field under test.
VALID_TRADE = {
    "symbol": "INFY",
    "stock_name": "Infosys",
    "quantity": 10,
    "entry_price": 1000.0,
    "type": "BUY",
    "stop_loss": 900.0,
    "target1": 1200.0,
    "target2": 1300.0,
    "setup_type": "MOMENTUM",
    "notes": "regression",
}


def _payload(**overrides):
    body = copy.deepcopy(VALID_TRADE)
    body.update(overrides)
    return body


def _quote(price=1050.0):
    """Patch the single live-market call the paper account makes when marking
    open positions. Hermetic tests have no network; without this the P&L route
    counts every open position as `marks_unavailable` and the unrealised figure
    is structurally 0, which would make the "P&L unchanged" assertions pass for
    a reason that has nothing to do with validation."""
    return patch("services.real_market.fetch_real_stock_quote",
                 new_callable=AsyncMock, return_value={"price": price})


def _account_snapshot(client, headers):
    """Everything a paper trade is capable of mutating, in one comparable blob."""
    with _quote():
        balance = client.get(BALANCE_URL, headers=headers).json()
        pnl = client.get(PNL_URL, headers=headers).json()
        trades = client.get(TRADES_URL, headers=headers).json()
    return {"balance": balance, "pnl": pnl, "trades": trades}


# --------------------------------------------------------------------------- #
# The exploit, reproduced exactly                                               #
# --------------------------------------------------------------------------- #
class TestPH312Exploit:
    """The literal PH3.12 finding: `quantity: -1000` credits the account."""

    def test_ph312_exploit_negative_quantity_is_rejected_on_the_quantity_field(
        self, client, auth_headers, fake_db
    ):
        # ACT
        response = client.post(TRADE_URL, json=_payload(quantity=-1000),
                               headers=auth_headers)

        # ASSERT — 422, and specifically because of `quantity`. Pinning the
        # field is what stops this test passing on an unrelated rejection.
        assert response.status_code == 422, (
            f"quantity=-1000 must be rejected, got {response.status_code}: "
            f"{response.text[:300]}"
        )
        offending_fields = {
            error["loc"][-1] for error in response.json()["detail"]
        }
        assert "quantity" in offending_fields, (
            f"rejected, but not for the quantity bound: {response.json()['detail']}"
        )

    def test_ph312_exploit_leaves_the_balance_untouched(
        self, client, auth_headers, fake_db
    ):
        # ARRANGE
        before = client.get(BALANCE_URL, headers=auth_headers).json()
        assert before["balance"] == 100000.0

        # ACT — the exact shape PH3.12 used, repeated: the original defect was
        # unbounded *and* repeatable, so one call is not a sufficient probe.
        for _ in range(3):
            client.post(TRADE_URL, json=_payload(quantity=-1000),
                        headers=auth_headers)

        # ASSERT — not merely "not inflated": identical.
        after = client.get(BALANCE_URL, headers=auth_headers).json()
        assert after["balance"] == before["balance"] == 100000.0
        assert after["pnl"] == 0.0

    def test_ph312_exploit_creates_no_trade(self, client, auth_headers, fake_db):
        client.post(TRADE_URL, json=_payload(quantity=-1000), headers=auth_headers)

        assert fake_db.trades.docs == [], (
            "a rejected request wrote a trade document into the journal"
        )


# --------------------------------------------------------------------------- #
# The full constraint matrix                                                    #
# --------------------------------------------------------------------------- #
#: `(case_id, overrides, offending_field)` — every constraint the canonical
#: `TradeCreate` enforces, plus the symbol/extras bounds paper trading needs
#: because its `symbol` reaches the journal, the activity log and the analytics
#: group-by keys. The offending field is asserted so no case can pass by
#: accident.
_REJECTED_CASES = [
    # quantity
    ("quantity_negative_exploit", {"quantity": -1000}, "quantity"),
    ("quantity_negative_one", {"quantity": -1}, "quantity"),
    ("quantity_zero", {"quantity": 0}, "quantity"),
    ("quantity_above_ceiling", {"quantity": 100001}, "quantity"),
    ("quantity_not_an_integer", {"quantity": "ten"}, "quantity"),
    # entry price
    ("entry_price_negative", {"entry_price": -100.0}, "entry_price"),
    ("entry_price_zero", {"entry_price": 0.0}, "entry_price"),
    ("entry_price_not_a_number", {"entry_price": "free"}, "entry_price"),
    # stop loss / targets
    ("stop_loss_negative", {"stop_loss": -1.0}, "stop_loss"),
    ("stop_loss_zero", {"stop_loss": 0.0}, "stop_loss"),
    ("target1_negative", {"target1": -1200.0}, "target1"),
    ("target1_zero", {"target1": 0.0}, "target1"),
    ("target2_negative", {"target2": -5.0}, "target2"),
    # side
    ("side_nonsense", {"type": "NONSENSE"}, "type"),
    ("side_lowercase", {"type": "buy"}, "type"),
    ("side_empty", {"type": ""}, "type"),
    ("side_sql_ish", {"type": "BUY' OR 1=1--"}, "type"),
    # symbol
    ("symbol_empty", {"symbol": ""}, "symbol"),
    ("symbol_whitespace", {"symbol": "   "}, "symbol"),
    ("symbol_html_payload", {"symbol": "<script>alert(1)</script>"}, "symbol"),
    ("symbol_path_traversal", {"symbol": "../../etc/passwd"}, "symbol"),
    ("symbol_mongo_operator", {"symbol": {"$ne": None}}, "symbol"),
    ("symbol_newline_suffix", {"symbol": "INFY\nEVIL"}, "symbol"),
    ("symbol_overlong", {"symbol": "A" * 33}, "symbol"),
    # unknown keys — a paper payload must not be able to name live-execution
    # fields, even though they would have been ignored.
    ("extra_field_broker", {"broker": "zerodha"}, "broker"),
    ("extra_field_is_paper", {"is_paper": False}, "is_paper"),
    # bounded free text
    ("notes_overlong", {"notes": "x" * 2001}, "notes"),
    ("stock_name_overlong", {"stock_name": "x" * 121}, "stock_name"),
]

#: JSON's non-standard numeric literals. Python's `json.loads` — which is what
#: Starlette parses bodies with — accepts `Infinity` and `NaN`, and a plain
#: `gt=0` float ADMITS `Infinity` (`inf > 0` is True). Sent as raw text because
#: no JSON *encoder* will emit them on request.
_NON_FINITE_BODIES = [
    ("entry_price_infinity", '"entry_price": Infinity', "entry_price"),
    ("entry_price_negative_infinity", '"entry_price": -Infinity', "entry_price"),
    ("entry_price_nan", '"entry_price": NaN', "entry_price"),
    ("stop_loss_infinity", '"stop_loss": Infinity', "stop_loss"),
]


class TestHostileInputRejected:

    @pytest.mark.parametrize(
        "overrides,field",
        [pytest.param(o, f, id=i) for i, o, f in _REJECTED_CASES],
    )
    def test_rejected_with_422(self, client, auth_headers, fake_db, overrides, field):
        response = client.post(TRADE_URL, json=_payload(**overrides),
                               headers=auth_headers)

        # 422, never 200 and never 500: the caller sent something invalid, and
        # a bad request reported as a server fault flat-lines an error budget
        # and tells the client to retry something that can never succeed.
        assert response.status_code == 422, (
            f"{overrides} → {response.status_code} {response.text[:200]}"
        )
        offending = {error["loc"][-1] for error in response.json()["detail"]}
        assert field in offending, (
            f"rejected, but {field} was not the reason: {response.json()['detail']}"
        )

    @pytest.mark.parametrize(
        "fragment,field",
        [pytest.param(fr, f, id=i) for i, fr, f in _NON_FINITE_BODIES],
    )
    def test_non_finite_numbers_rejected(self, client, auth_headers, fake_db,
                                         fragment, field):
        # ARRANGE — hand-built body: `Infinity`/`NaN` are not valid JSON, so
        # they have to be smuggled in as text exactly as an attacker would.
        body = (
            '{"symbol": "INFY", "stock_name": "Infosys", "quantity": 10, '
            '"entry_price": 1000.0, "type": "BUY", "stop_loss": 900.0, '
            '"target1": 1200.0, ' + fragment + "}"
        )

        # ACT
        response = client.post(
            TRADE_URL, content=body,
            headers={**auth_headers, "Content-Type": "application/json"},
        )

        # ASSERT
        assert response.status_code == 422, (
            f"{fragment} → {response.status_code} {response.text[:200]}"
        )
        offending = {error["loc"][-1] for error in response.json()["detail"]}
        assert field in offending
        assert fake_db.trades.docs == []

    def test_malformed_json_body_is_not_a_500(self, client, auth_headers, fake_db):
        response = client.post(
            TRADE_URL, content='{"symbol": "INFY", "quantity":',
            headers={**auth_headers, "Content-Type": "application/json"},
        )

        assert response.status_code < 500, f"got {response.status_code}"
        assert fake_db.trades.docs == []

    def test_missing_required_fields_is_a_422(self, client, auth_headers, fake_db):
        response = client.post(TRADE_URL, json={"symbol": "INFY"},
                               headers=auth_headers)

        assert response.status_code == 422
        assert fake_db.trades.docs == []

    def test_rejection_does_not_echo_the_submitted_value(self, client, auth_headers,
                                                         fake_db):
        """PH1.5's sanitized 422 handler must still apply to this route."""
        response = client.post(TRADE_URL, json=_payload(quantity=-1000),
                               headers=auth_headers)

        assert response.status_code == 422
        assert "input" not in response.text
        assert "-1000" not in response.text


# --------------------------------------------------------------------------- #
# The half that actually matters: nothing moved                                 #
# --------------------------------------------------------------------------- #
class TestRejectedRequestsMutateNothing:
    """A rejection that arrives after the write is not a fix."""

    @pytest.mark.parametrize(
        "overrides",
        [pytest.param(o, id=i) for i, o, _ in _REJECTED_CASES],
    )
    def test_no_state_changes_on_a_fresh_account(self, client, auth_headers,
                                                 fake_db, overrides):
        before = _account_snapshot(client, auth_headers)

        client.post(TRADE_URL, json=_payload(**overrides), headers=auth_headers)

        assert _account_snapshot(client, auth_headers) == before
        assert fake_db.trades.docs == []

    @pytest.mark.parametrize(
        "overrides",
        [pytest.param(o, id=i) for i, o, _ in _REJECTED_CASES],
    )
    def test_no_state_changes_on_an_account_holding_a_position(
        self, client, auth_headers, fake_db, overrides
    ):
        """The stronger form. A fresh account has a balance of exactly the
        default and no positions, so several mutations (a P&L shift, a position
        edit) have nothing to move. This one opens a real position first, so
        balance, open positions, unrealised P&L and the journal all carry
        non-trivial values that a defect could disturb."""
        # ARRANGE — one legitimate trade, then snapshot the loaded account.
        with _quote():
            opened = client.post(TRADE_URL, json=_payload(), headers=auth_headers)
        assert opened.status_code == 200
        before = _account_snapshot(client, auth_headers)
        assert before["balance"]["balance"] == 90000.0     # 100000 - 10 × 1000
        assert len(before["trades"]) == 1
        assert before["pnl"]["open_trades"] == 1
        assert before["pnl"]["unrealized_pnl"] != 0.0

        # ACT
        response = client.post(TRADE_URL, json=_payload(**overrides),
                               headers=auth_headers)
        assert response.status_code == 422

        # ASSERT — balance, positions, journal and P&L all untouched.
        after = _account_snapshot(client, auth_headers)
        assert after["balance"] == before["balance"]
        assert after["pnl"] == before["pnl"]
        assert after["trades"] == before["trades"]
        assert len(fake_db.trades.docs) == 1

    def test_a_rejected_request_cannot_credit_the_account(self, client, auth_headers,
                                                          fake_db):
        """The invariant B-1 violated, stated directly: no rejected request may
        ever leave the paper balance ABOVE where it started."""
        start = client.get(BALANCE_URL, headers=auth_headers).json()["balance"]

        for _, overrides, _field in _REJECTED_CASES:
            client.post(TRADE_URL, json=_payload(**overrides), headers=auth_headers)
            now = client.get(BALANCE_URL, headers=auth_headers).json()["balance"]
            assert now <= start, f"{overrides} credited the account: {start} → {now}"
        assert client.get(BALANCE_URL,
                          headers=auth_headers).json()["balance"] == start


# --------------------------------------------------------------------------- #
# Valid trades still work                                                       #
# --------------------------------------------------------------------------- #
class TestValidTradesUnaffected:
    """The other half of a validation fix: legitimate input must be untouched.
    These cases mirror what `frontend/src/pages/PaperTrading.jsx` actually
    submits, so a constraint too tight for the real UI fails here."""

    def test_valid_buy_succeeds_and_debits_the_balance(self, client, auth_headers,
                                                       fake_db):
        with _quote():
            response = client.post(TRADE_URL, json=_payload(), headers=auth_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["symbol"] == "INFY"
        assert body["quantity"] == 10
        assert body["is_paper"] is True
        assert body["status"] == "OPEN"
        assert client.get(BALANCE_URL,
                          headers=auth_headers).json()["balance"] == 90000.0

    def test_valid_sell_succeeds(self, client, auth_headers, fake_db):
        with _quote():
            response = client.post(TRADE_URL, json=_payload(type="SELL"),
                                   headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["type"] == "SELL"

    @pytest.mark.parametrize("symbol", [
        "RELIANCE",             # the common case
        "reliance",             # lowercase — the service upper-cases on the way in
        "M&M",                  # a real NSE listing with an ampersand
        "BAJAJ-AUTO",           # a real NSE listing with a hyphen
        "NIFTY50",              # digits
        "A",                    # single character
        "A" * 32,               # exactly at the length ceiling
    ])
    def test_real_world_symbols_are_accepted(self, client, auth_headers, fake_db,
                                             symbol):
        with _quote():
            response = client.post(TRADE_URL, json=_payload(symbol=symbol),
                                   headers=auth_headers)

        assert response.status_code == 200, f"{symbol!r} → {response.text[:200]}"
        assert response.json()["symbol"] == symbol.upper()

    def test_omitted_optional_fields_still_default(self, client, auth_headers,
                                                   fake_db):
        """`stock_name`, `target2`, `setup_type` and `notes` are optional and
        must stay optional — `target2: 0` in particular is the caller's
        documented "no second target" sentinel, not an invalid price."""
        minimal = {"symbol": "TCS", "quantity": 5, "entry_price": 100.0,
                   "stop_loss": 90.0, "target1": 120.0}

        with _quote():
            response = client.post(TRADE_URL, json=minimal, headers=auth_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stock_name"] == "TCS"      # falls back to the symbol
        assert body["target2"] == 120.0         # `target2 or target1`
        assert body["setup_type"] == "MOMENTUM"

    def test_explicit_zero_target2_is_accepted(self, client, auth_headers, fake_db):
        with _quote():
            response = client.post(TRADE_URL, json=_payload(target2=0.0),
                                   headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["target2"] == 1200.0     # falls back to target1

    @pytest.mark.parametrize("setup_type", [
        "RSI_BREAKOUT", "VWAP_CROSS", "BULLISH_ENGULFING", "BEARISH_ENGULFING",
        "EMA_CROSSOVER", "MACD_SIGNAL", "SUPPORT_BOUNCE", "RESISTANCE_BREAK",
        "TRIANGLE_BREAKOUT", "GAP_UP_PLAY", "MOMENTUM",
    ])
    def test_every_setup_type_the_ui_offers_is_accepted(self, client, auth_headers,
                                                        fake_db, setup_type):
        """The exact option list in `PaperTrading.jsx`. If a constraint added
        here ever rejects one of them, the dropdown is broken in production."""
        with _quote():
            response = client.post(TRADE_URL, json=_payload(setup_type=setup_type),
                                   headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["setup_type"] == setup_type

    def test_quantity_at_the_ceiling_is_accepted(self, client, auth_headers,
                                                 fake_db):
        """100,000 is the boundary `TradeCreate` has always allowed — the fix
        must reject 100,001 without also rejecting the last legal value."""
        with _quote():
            response = client.post(
                TRADE_URL,
                json=_payload(quantity=100000, entry_price=0.5, stop_loss=0.4,
                              target1=0.6, target2=0.7),
                headers=auth_headers,
            )

        assert response.status_code == 200, response.text
        assert response.json()["quantity"] == 100000

    def test_insufficient_capital_is_still_a_400_not_a_422(self, client,
                                                           auth_headers, fake_db):
        """A well-formed order the account cannot afford is a business
        rejection, not a malformed request. The two must stay distinguishable —
        the UI shows the `detail` string for one and a field error for the
        other."""
        with _quote():
            response = client.post(TRADE_URL,
                                   json=_payload(quantity=1000, entry_price=1000.0),
                                   headers=auth_headers)

        assert response.status_code == 400
        assert "Insufficient paper capital" in response.json()["detail"]
        assert fake_db.trades.docs == []


# --------------------------------------------------------------------------- #
# Defence in depth: the service validates its own arguments                     #
# --------------------------------------------------------------------------- #
class TestServiceLayerGuard:
    """`execute_paper_trade` is importable, and B-1 was caused precisely by
    trusting that someone upstream had checked. A future scheduler, backfill or
    AI action calling it directly must not be able to reopen the hole."""

    def test_direct_service_call_with_negative_quantity_raises(self):
        import asyncio

        from bson import ObjectId

        from services.paper_trade import execute_paper_trade, get_paper_balance
        from tests._fakedb import FakeDB

        async def run():
            db = FakeDB()
            user_id = str(ObjectId())
            db.users.docs.append({"_id": ObjectId(user_id), "name": "Direct Caller"})

            with pytest.raises(ValueError, match="quantity"):
                await execute_paper_trade(
                    user_id=user_id, symbol="INFY", stock_name="Infosys",
                    quantity=-1000, entry_price=1000.0, trade_type="BUY",
                    stop_loss=900.0, target1=1200.0, target2=0.0,
                    setup_type="MOMENTUM", notes="", db=db,
                )

            assert (await get_paper_balance(user_id, db))["balance"] == 100000.0
            assert db.trades.docs == []

        asyncio.run(run())

    @pytest.mark.parametrize("overrides", [
        pytest.param({"quantity": 0}, id="quantity_zero"),
        pytest.param({"entry_price": -1.0}, id="entry_price_negative"),
        pytest.param({"trade_type": "NONSENSE"}, id="side_invalid"),
        pytest.param({"symbol": "<script>"}, id="symbol_malformed"),
        pytest.param({"entry_price": float("inf")}, id="entry_price_infinite"),
    ])
    def test_direct_service_call_rejects_the_same_inputs_as_the_route(self,
                                                                     overrides):
        import asyncio

        from bson import ObjectId

        from services.paper_trade import execute_paper_trade
        from tests._fakedb import FakeDB

        async def run():
            db = FakeDB()
            user_id = str(ObjectId())
            db.users.docs.append({"_id": ObjectId(user_id), "name": "Direct Caller"})
            args = dict(user_id=user_id, symbol="INFY", stock_name="Infosys",
                        quantity=10, entry_price=1000.0, trade_type="BUY",
                        stop_loss=900.0, target1=1200.0, target2=0.0,
                        setup_type="MOMENTUM", notes="", db=db)
            args.update(overrides)

            with pytest.raises(ValueError):
                await execute_paper_trade(**args)

            assert db.trades.docs == []
            assert db.users.docs[0].get("paper_capital") is None

        asyncio.run(run())


# --------------------------------------------------------------------------- #
# The models can no longer drift apart                                          #
# --------------------------------------------------------------------------- #
class TestCanonicalConstraintsAreShared:
    """B-1's root cause was two independent declarations of one contract. These
    assert the declarations are now the SAME objects, which is the only form of
    the guarantee that a future edit cannot quietly undo."""

    @pytest.mark.parametrize("field", ["quantity", "entry_price", "stop_loss",
                                       "target1", "type"])
    def test_paper_and_real_trade_models_share_field_metadata(self, field):
        from models import PaperTradeCreate, TradeCreate

        paper = PaperTradeCreate.model_fields[field]
        real = TradeCreate.model_fields[field]

        assert paper.metadata == real.metadata, (
            f"{field} has drifted: paper={paper.metadata} real={real.metadata}"
        )
        assert paper.annotation == real.annotation

    def test_the_shared_aliases_are_the_ones_actually_in_use(self):
        """A guard against the aliases being defined and then not used — the
        shape a "fix" takes when someone re-inlines a constraint later."""
        from models import PaperTradeCreate, TradeQuantity

        assert PaperTradeCreate.model_fields["quantity"].metadata == \
            TradeQuantity.__metadata__[0].metadata

    def test_every_paper_endpoint_still_requires_authentication(self, client):
        """B-1 was not an authorization defect and this fix must not become
        one: the route is still behind `get_current_user`."""
        for url, method in ((TRADE_URL, "post"), (BALANCE_URL, "get"),
                            (PNL_URL, "get"), (TRADES_URL, "get")):
            response = getattr(client, method)(url, json=VALID_TRADE) \
                if method == "post" else getattr(client, method)(url)
            assert response.status_code == 401, f"{url} answered {response.status_code}"

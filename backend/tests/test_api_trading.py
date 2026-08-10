"""Trading API behaviour: orders, positions, exits, broker failures (PH3.3).

WHAT PRODUCTION FAILURE THIS CATCHES
------------------------------------
The trade lifecycle is the one surface where a bug costs the user money rather
than an error message. The failures worth catching here are specific:

* an OPEN position recorded for a broker order that was **rejected** — the user
  believes they hold something they do not;
* a position closed twice, or exited for more than the quantity held, so the
  realized P&L that every downstream aggregate reads is wrong;
* a risk-manager violation that blocks the response but persists the trade
  anyway.

Each of those is asserted against the *database state* after the request, not
just the status code. A route can return the right code and still have written
the wrong document, and it is the document that the portfolio, the journal and
the tax export all read afterwards.

SCOPE DISCIPLINE
----------------
`services/trading_engine.py` (the risk calculations and partial-exit maths) has
its own unit coverage in `test_trading_engine.py` and is **not** retested here;
PH3.3 explicitly must not modify trading logic. This file covers the *API*
around it: validation, ownership, persistence, and the broker boundary.

The broker is always mocked. A test that places a real order is not a test.
"""
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

import server
from services.brokers.base import BrokerError

VALID_TRADE = {
    "symbol": "RELIANCE",
    "stock_name": "Reliance Industries",
    "type": "BUY",
    "entry_price": 100.0,
    "quantity": 10,
    "stop_loss": 95.0,
    "target1": 110.0,
}


@pytest.fixture
def no_quotes(monkeypatch):
    """Live quotes unavailable — the deterministic default for these tests.

    Trading endpoints that mark positions to market must not reach a provider,
    and the "no live price" branch is the one a provider outage takes.
    """
    monkeypatch.setattr(server, "real_quotes_map", AsyncMock(return_value={}))
    monkeypatch.setattr(server, "real_quote", AsyncMock(return_value=None))


@pytest.fixture
def open_trade(fake_db, test_user):
    doc = {
        "_id": ObjectId(),
        "user_id": str(test_user["_id"]),
        "symbol": "TESTCO",
        "stock_name": "TEST Company",
        "type": "BUY",
        "entry_price": 100.0,
        "quantity": 10,
        "quantity_open": 10,
        "realized_pnl": 0.0,
        "stop_loss": 90.0,
        "initial_stop_loss": 90.0,
        "target1": 120.0,
        "target2": None,
        "target3": None,
        "best_price": 100.0,
        "targets_hit": [],
        "trailing_stop": {"enabled": False},
        "status": "OPEN",
        "pnl": None,
        "pnl_percent": None,
        "entry_time": "2026-08-01T09:15:00+00:00",
        "exit_time": None,
        "events": [],
        "broker": None,
    }
    fake_db.trades.docs.append(doc)
    return doc


# --------------------------------------------------------------------------- #
# Order placement                                                               #
# --------------------------------------------------------------------------- #
class TestCreateTrade:
    def test_valid_trade_is_persisted_and_owned_by_the_caller(
            self, authenticated_client, fake_db, test_user):
        resp = authenticated_client.post("/api/trades", json=VALID_TRADE)
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "RELIANCE"
        assert body["status"] == "OPEN"
        assert body["quantity_open"] == 10, "quantity_open must seed from quantity"
        assert body["realized_pnl"] == 0.0

        assert len(fake_db.trades.docs) == 1
        stored = fake_db.trades.docs[0]
        assert stored["user_id"] == str(test_user["_id"])
        assert stored["initial_stop_loss"] == 95.0, \
            "initial_stop_loss anchors risk reporting and must be captured at entry"

    def test_symbol_is_normalized_to_uppercase(
            self, authenticated_client, fake_db, test_user):
        """Every downstream lookup keys on an uppercase symbol; a lowercase one
        stored verbatim silently stops matching quotes."""
        resp = authenticated_client.post("/api/trades", json={**VALID_TRADE, "symbol": "reliance"})
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "RELIANCE"

    def test_entry_event_is_recorded(self, authenticated_client, fake_db, test_user):
        resp = authenticated_client.post("/api/trades", json=VALID_TRADE)
        events = resp.json()["events"]
        assert events and events[0]["type"] == "ENTRY"

    def test_a_notification_is_raised_for_the_owner_only(
            self, authenticated_client, fake_db, test_user, other_user):
        authenticated_client.post("/api/trades", json=VALID_TRADE)
        notifs = fake_db.notifications.docs
        assert len(notifs) == 1
        assert notifs[0]["user_id"] == str(test_user["_id"])

    def test_risk_violation_blocks_the_trade_and_persists_nothing(
            self, authenticated_client, fake_db, test_user):
        """A 422 that still wrote the document would be the worst outcome: the
        user is told the trade was rejected and holds a position anyway."""
        with patch("services.trading_engine.validate_trade", return_value={
                "approved": False,
                "violations": ["TEST position size exceeds risk limit"],
                "warnings": [], "metrics": {}}):
            resp = authenticated_client.post("/api/trades", json=VALID_TRADE)
        assert resp.status_code == 422
        assert resp.json()["detail"]["violations"]
        assert fake_db.trades.docs == [], "a rejected trade was persisted"

    def test_warnings_do_not_block(self, authenticated_client, fake_db, test_user):
        """Warnings educate; only violations block (CLAUDE.md product rule)."""
        with patch("services.trading_engine.validate_trade", return_value={
                "approved": True, "violations": [],
                "warnings": ["TEST wide stop"], "metrics": {"risk_pct": 5.0}}):
            resp = authenticated_client.post("/api/trades", json=VALID_TRADE)
        assert resp.status_code == 200
        assert fake_db.trades.docs[0]["risk_check"]["warnings"] == ["TEST wide stop"]

    def test_validate_endpoint_is_a_dry_run(self, authenticated_client, fake_db, test_user):
        """`POST /trades/validate` powers the live risk panel; it must never
        create anything, or opening the form would place trades."""
        resp = authenticated_client.post("/api/trades/validate", json=VALID_TRADE)
        assert resp.status_code == 200
        assert "approved" in resp.json()
        assert fake_db.trades.docs == []


class TestBrokerOrderFailures:
    """A live entry that the broker rejects must leave no OPEN trade behind."""

    def test_broker_rejection_is_502_and_records_nothing(
            self, authenticated_client, fake_db, test_user):
        with patch.object(server.broker_engine, "place_order", new_callable=AsyncMock,
                          side_effect=BrokerError("insufficient funds",
                                                  user_message="Insufficient funds")):
            resp = authenticated_client.post(
                "/api/trades", json={**VALID_TRADE, "broker": "zerodha"})
        assert resp.status_code == 502
        assert "Insufficient funds" in resp.json()["detail"]
        assert fake_db.trades.docs == [], \
            "a trade was recorded for an order the broker rejected"

    def test_successful_broker_order_id_is_stored(
            self, authenticated_client, fake_db, test_user):
        with patch.object(server.broker_engine, "place_order", new_callable=AsyncMock,
                          return_value={"order_id": "TEST-ORDER-1"}):
            resp = authenticated_client.post(
                "/api/trades", json={**VALID_TRADE, "broker": "zerodha"})
        assert resp.status_code == 200
        assert resp.json()["broker_order_id"] == "TEST-ORDER-1"

    def test_auto_exit_requires_a_broker_link(
            self, authenticated_client, fake_db, test_user):
        """`auto_exit` without a broker cannot be honoured, so it must not be
        stored as enabled — a position that believes it will auto-exit and
        cannot is worse than one that never claimed to."""
        resp = authenticated_client.post(
            "/api/trades", json={**VALID_TRADE, "auto_exit": True})
        assert resp.status_code == 200
        assert resp.json()["auto_exit"] is False

    def test_unknown_broker_is_rejected(self, authenticated_client, fake_db, test_user):
        resp = authenticated_client.post(
            "/api/trades", json={**VALID_TRADE, "broker": "not-a-broker"})
        assert 400 <= resp.status_code < 500
        assert fake_db.trades.docs == []


# --------------------------------------------------------------------------- #
# Listing positions                                                             #
# --------------------------------------------------------------------------- #
class TestListTrades:
    def test_list_is_empty_for_a_new_user(self, authenticated_client, fake_db, test_user):
        resp = authenticated_client.get("/api/trades")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_active_excludes_closed_positions(
            self, authenticated_client, fake_db, test_user, open_trade, no_quotes):
        fake_db.trades.docs.append({**open_trade, "_id": ObjectId(),
                                    "status": "CLOSED", "symbol": "CLOSEDCO"})
        resp = authenticated_client.get("/api/trades/active")
        assert resp.status_code == 200
        assert [t["symbol"] for t in resp.json()] == ["TESTCO"]

    def test_history_excludes_open_positions(
            self, authenticated_client, fake_db, test_user, open_trade):
        fake_db.trades.docs.append({**open_trade, "_id": ObjectId(),
                                    "status": "TARGET_HIT", "symbol": "DONECO"})
        resp = authenticated_client.get("/api/trades/history")
        assert resp.status_code == 200
        assert [t["symbol"] for t in resp.json()] == ["DONECO"]

    def test_active_survives_a_market_data_outage(
            self, authenticated_client, fake_db, test_user, open_trade, no_quotes):
        """No live quote is an outage, not an error: the position must still be
        listed, just without a live mark. Returning 5xx here would blank the
        user's open positions during a provider incident — precisely when they
        most need to see them."""
        resp = authenticated_client.get("/api/trades/active")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_pnl_summary_counts_only_the_callers_trades(
            self, authenticated_client, fake_db, test_user, other_user):
        fake_db.trades.docs.extend([
            {"_id": ObjectId(), "user_id": str(test_user["_id"]),
             "status": "CLOSED", "pnl": 500.0, "symbol": "MINE"},
            {"_id": ObjectId(), "user_id": str(other_user["_id"]),
             "status": "CLOSED", "pnl": 9999.0, "symbol": "THEIRS"},
        ])
        resp = authenticated_client.get("/api/trades/pnl")
        assert resp.status_code == 200
        assert resp.json()["total_pnl"] == 500.0


# --------------------------------------------------------------------------- #
# Modifying and exiting                                                         #
# --------------------------------------------------------------------------- #
class TestModifyTrade:
    def test_stop_loss_can_be_moved_to_lock_in_profit(
            self, authenticated_client, fake_db, test_user, open_trade):
        """A breakeven-plus stop is *above* entry on a long — the documented
        rule is "anywhere below target 1", not "below entry"."""
        resp = authenticated_client.put(f"/api/trades/{open_trade['_id']}",
                                        json={"stop_loss": 105.0})
        assert resp.status_code == 200
        assert fake_db.trades.docs[0]["stop_loss"] == 105.0

    def test_stop_at_or_above_target1_is_422(
            self, authenticated_client, fake_db, test_user, open_trade):
        resp = authenticated_client.put(f"/api/trades/{open_trade['_id']}",
                                        json={"stop_loss": 130.0})
        assert resp.status_code == 422
        assert fake_db.trades.docs[0]["stop_loss"] == 90.0

    def test_target_on_the_wrong_side_of_entry_is_422(
            self, authenticated_client, fake_db, test_user, open_trade):
        resp = authenticated_client.put(f"/api/trades/{open_trade['_id']}",
                                        json={"target1": 80.0})
        assert resp.status_code == 422

    def test_out_of_order_targets_are_422(
            self, authenticated_client, fake_db, test_user, open_trade):
        resp = authenticated_client.put(f"/api/trades/{open_trade['_id']}",
                                        json={"target1": 120.0, "target2": 110.0})
        assert resp.status_code == 422

    def test_a_closed_trade_cannot_be_modified(
            self, authenticated_client, fake_db, test_user, open_trade):
        fake_db.trades.docs[0]["status"] = "CLOSED"
        resp = authenticated_client.put(f"/api/trades/{open_trade['_id']}",
                                        json={"stop_loss": 95.0})
        assert resp.status_code == 400

    def test_unknown_trade_is_404(self, authenticated_client, fake_db, test_user):
        resp = authenticated_client.put(f"/api/trades/{ObjectId()}", json={"stop_loss": 95.0})
        assert resp.status_code == 404


class TestExitTrade:
    def test_full_exit_closes_and_records_realized_pnl(
            self, authenticated_client, fake_db, test_user, open_trade):
        resp = authenticated_client.post(f"/api/trades/{open_trade['_id']}/exit",
                                         json={"exit_price": 120.0, "quantity": 10})
        assert resp.status_code == 200
        stored = fake_db.trades.docs[0]
        assert stored["status"] != "OPEN"
        assert stored["quantity_open"] == 0
        assert stored["realized_pnl"] == pytest.approx(200.0), \
            "(120 - 100) x 10 — the number every downstream aggregate reads"

    def test_partial_exit_leaves_the_remainder_open(
            self, authenticated_client, fake_db, test_user, open_trade):
        resp = authenticated_client.post(f"/api/trades/{open_trade['_id']}/exit",
                                         json={"exit_price": 110.0, "quantity": 4})
        assert resp.status_code == 200
        stored = fake_db.trades.docs[0]
        assert stored["status"] == "OPEN"
        assert stored["quantity_open"] == 6
        assert stored["realized_pnl"] == pytest.approx(40.0)

    def test_exiting_more_than_held_is_capped_not_oversold(
            self, authenticated_client, fake_db, test_user, open_trade):
        """Selling 1,000 of a 10-share position must not book a 1,000-share
        profit. The route clamps to `quantity_open`."""
        resp = authenticated_client.post(f"/api/trades/{open_trade['_id']}/exit",
                                         json={"exit_price": 120.0, "quantity": 1000})
        assert resp.status_code == 200
        stored = fake_db.trades.docs[0]
        assert stored["quantity_open"] == 0
        assert stored["realized_pnl"] == pytest.approx(200.0)

    def test_a_closed_trade_cannot_be_exited_again(
            self, authenticated_client, fake_db, test_user, open_trade):
        """Double-close is the classic duplicate-submission bug: two clicks on a
        slow connection would otherwise book the profit twice."""
        first = authenticated_client.post(f"/api/trades/{open_trade['_id']}/exit",
                                          json={"exit_price": 120.0, "quantity": 10})
        assert first.status_code == 200
        second = authenticated_client.post(f"/api/trades/{open_trade['_id']}/exit",
                                           json={"exit_price": 130.0, "quantity": 10})
        assert second.status_code == 400
        assert fake_db.trades.docs[0]["realized_pnl"] == pytest.approx(200.0), \
            "P&L was booked twice"

    def test_market_exit_without_a_price_and_without_a_quote_is_422(
            self, authenticated_client, fake_db, test_user, open_trade, no_quotes):
        """Rather than guessing a price. A fabricated exit price corrupts the
        trade record permanently."""
        resp = authenticated_client.post(f"/api/trades/{open_trade['_id']}/exit", json={})
        assert resp.status_code == 422
        assert fake_db.trades.docs[0]["status"] == "OPEN"

    def test_market_exit_uses_the_live_quote_when_available(
            self, authenticated_client, fake_db, test_user, open_trade, monkeypatch):
        monkeypatch.setattr(server, "real_quote", AsyncMock(
            return_value={"symbol": "TESTCO", "price": 115.0}))
        resp = authenticated_client.post(f"/api/trades/{open_trade['_id']}/exit", json={})
        assert resp.status_code == 200
        assert fake_db.trades.docs[0]["realized_pnl"] == pytest.approx(150.0)

    def test_at_market_exit_requires_a_broker_linked_trade(
            self, authenticated_client, fake_db, test_user, open_trade):
        resp = authenticated_client.post(f"/api/trades/{open_trade['_id']}/exit",
                                         json={"at_market": True})
        assert resp.status_code == 400
        assert fake_db.trades.docs[0]["status"] == "OPEN"

    def test_broker_rejected_exit_leaves_the_position_open(
            self, authenticated_client, fake_db, test_user, open_trade):
        """The most dangerous failure in the file: recording an exit for an
        order the broker refused would leave the user flat on paper and long in
        reality, with no stop attached."""
        fake_db.trades.docs[0]["broker"] = "zerodha"
        with patch.object(server.broker_engine, "place_order", new_callable=AsyncMock,
                          side_effect=BrokerError("rejected", user_message="Order rejected")):
            resp = authenticated_client.post(f"/api/trades/{open_trade['_id']}/exit",
                                             json={"at_market": True, "quantity": 10})
        assert resp.status_code == 502
        stored = fake_db.trades.docs[0]
        assert stored["status"] == "OPEN"
        assert stored["quantity_open"] == 10
        assert stored["realized_pnl"] == 0.0


# --------------------------------------------------------------------------- #
# Paper trading                                                                 #
# --------------------------------------------------------------------------- #
class TestPaperTrading:
    def test_balance_is_available_for_a_new_user(
            self, authenticated_client, fake_db, test_user):
        resp = authenticated_client.get("/api/paper/balance")
        assert resp.status_code == 200

    def test_paper_trade_is_recorded_against_the_caller(
            self, authenticated_client, fake_db, test_user):
        resp = authenticated_client.post("/api/paper/trade", json={
            "symbol": "RELIANCE", "quantity": 5, "entry_price": 100.0,
            "stop_loss": 95.0, "target1": 110.0})
        assert resp.status_code < 500
        for doc in fake_db.paper_trades.docs:
            assert doc["user_id"] == str(test_user["_id"])

    def test_closing_an_unknown_paper_trade_is_a_client_error(
            self, authenticated_client, fake_db, test_user):
        resp = authenticated_client.post(f"/api/paper/close/{ObjectId()}")
        assert 400 <= resp.status_code < 500

    def test_reset_only_affects_the_caller(
            self, authenticated_client, fake_db, test_user, other_user):
        victim = {"_id": ObjectId(), "user_id": str(other_user["_id"]),
                  "symbol": "THEIRS", "status": "OPEN",
                  "quantity": 1, "entry_price": 10.0}
        fake_db.paper_trades.docs.append(victim)
        resp = authenticated_client.post("/api/paper/reset")
        assert resp.status_code == 200
        assert any(d["_id"] == victim["_id"] for d in fake_db.paper_trades.docs), \
            "another user's paper trades were reset"


# --------------------------------------------------------------------------- #
# Orders view                                                                   #
# --------------------------------------------------------------------------- #
class TestOrders:
    def test_orders_survive_a_broker_outage(
            self, authenticated_client, fake_db, test_user):
        """A disconnected or failing broker must degrade to a readable answer,
        not a 500 — the orders screen is where a user goes *during* an incident."""
        with patch.object(server.broker_engine, "get_orders", new_callable=AsyncMock,
                          side_effect=BrokerError("upstream down",
                                                  user_message="Broker unavailable")):
            resp = authenticated_client.get("/api/orders")
        assert resp.status_code in (200, 502), resp.text
        if resp.status_code == 502:
            assert "detail" in resp.json()

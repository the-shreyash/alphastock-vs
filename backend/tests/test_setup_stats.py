"""Tests for services/trade_journal.py's get_setup_success_rates() and
GET /api/journal/setup-stats, plus setup_type acceptance on trade creation.

NOTE (testability gap — not fixed here, see final report): TradeCreate
accepts a `setup_type` field and POST /api/trades returns 200 for it, but
server.py's create_trade() never copies data.setup_type into the inserted
trade_doc — it is silently dropped. Only paper trades (services/paper_trade.py
execute_paper_trade) actually persist setup_type today. This means
get_setup_success_rates() can never see non-demo grouped data from trades
created via the normal (non-paper) POST /api/trades flow. The test below
therefore only asserts that the request is accepted (200), not that
setup_type round-trips — asserting persistence would fail against current
behavior.
"""
import asyncio

from bson import ObjectId

from tests._fakedb import FakeDB
from services.trade_journal import get_setup_success_rates


def test_setup_stats_returns_dict():
    async def run():
        # ARRANGE — no trades at all for this user
        db = FakeDB()
        user_id = str(ObjectId())

        # ACT
        result = await get_setup_success_rates(db, user_id)

        # ASSERT
        assert isinstance(result, dict)
        assert "setups" in result and isinstance(result["setups"], list)

    asyncio.run(run())


def test_win_rate_calculation_correct():
    async def run():
        # ARRANGE — 5 closed trades under the same setup_type: 3 wins, 2 losses
        db = FakeDB()
        user_id = str(ObjectId())
        for pnl in (500, 300, 200, -100, -150):
            db.trades.docs.append({
                "_id": ObjectId(), "user_id": user_id, "status": "CLOSED",
                "setup_type": "RSI_BREAKOUT", "pnl": pnl, "pnl_percent": pnl / 100,
            })

        # ACT
        result = await get_setup_success_rates(db, user_id)

        # ASSERT
        assert result["is_demo"] is False
        setup = next(s for s in result["setups"] if s["setup_type"] == "RSI_BREAKOUT")
        assert setup["total_trades"] == 5
        assert setup["winning_trades"] == 3
        assert setup["win_rate"] == 60.0

    asyncio.run(run())


def test_setup_stats_returns_demo_when_no_trades():
    async def run():
        # ARRANGE
        db = FakeDB()
        user_id = str(ObjectId())

        # ACT
        result = await get_setup_success_rates(db, user_id)

        # ASSERT
        assert result["is_demo"] is True
        assert len(result["setups"]) > 0

    asyncio.run(run())


def test_setup_type_accepted_in_trade_creation(client, fake_db, auth_headers):
    # ARRANGE
    payload = {
        "symbol": "RELIANCE", "stock_name": "Reliance", "type": "BUY",
        "entry_price": 2500.0, "quantity": 1, "stop_loss": 2450.0, "target1": 2600.0,
        "setup_type": "RSI_BREAKOUT",
    }

    # ACT
    response = client.post("/api/trades", json=payload, headers=auth_headers)

    # ASSERT — the field is accepted by the request model without a 422
    assert response.status_code == 200, response.text
    assert response.json()["symbol"] == "RELIANCE"


def test_setup_stats_endpoint_returns_200(client, fake_db, auth_headers):
    # ACT
    response = client.get("/api/journal/setup-stats", headers=auth_headers)

    # ASSERT
    assert response.status_code == 200, response.text
    assert "setups" in response.json()

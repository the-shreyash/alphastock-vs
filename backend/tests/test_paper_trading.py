"""Tests for services/paper_trade.py (Paper Trading — virtual trades on a
1,00,000 starting capital that must never touch the real broker).

paper_trade.py's functions take `db` as an explicit parameter rather than
reading a module-level global, so these tests construct a local FakeDB
(tests/_fakedb.py) directly and call the async service functions with
`asyncio.run(...)` — matching the async-test pattern already used for
WebSocket checks in tests/test_phase2.py (no pytest-asyncio plugin is
installed in this project, so a bare `async def test_...` would silently
no-op rather than fail).
"""
import asyncio
from unittest.mock import AsyncMock, patch

from bson import ObjectId

from tests._fakedb import FakeDB
from services.paper_trade import (
    DEFAULT_CAPITAL,
    get_paper_balance,
    execute_paper_trade,
    close_paper_trade,
    reset_paper_capital,
)


def _new_user_id(db):
    uid = str(ObjectId())
    db.users.docs.append({"_id": ObjectId(uid), "name": "Paper Trader"})
    return uid


def test_get_paper_balance_returns_100000_default():
    async def run():
        # ARRANGE
        db = FakeDB()
        user_id = _new_user_id(db)

        # ACT
        result = await get_paper_balance(user_id, db)

        # ASSERT
        assert result["balance"] == 100000.0
        assert result["starting"] == DEFAULT_CAPITAL

    asyncio.run(run())


def test_execute_paper_trade_creates_trade_with_is_paper_true():
    async def run():
        # ARRANGE
        db = FakeDB()
        user_id = _new_user_id(db)

        # ACT
        trade = await execute_paper_trade(
            user_id=user_id, symbol="reliance", stock_name="Reliance",
            quantity=5, entry_price=100.0, trade_type="BUY",
            stop_loss=95.0, target1=110.0, target2=120.0,
            setup_type="MOMENTUM", notes="test", db=db,
        )

        # ASSERT
        assert trade["is_paper"] is True
        assert trade["symbol"] == "RELIANCE"
        assert trade["status"] == "OPEN"
        stored = await db.trades.find_one({"_id": ObjectId(trade["_id"])})
        assert stored["is_paper"] is True

    asyncio.run(run())


def test_execute_paper_trade_deducts_from_balance():
    async def run():
        # ARRANGE
        db = FakeDB()
        user_id = _new_user_id(db)
        before = await get_paper_balance(user_id, db)
        assert before["balance"] == 100000.0

        # ACT — BUY 10 shares @ 100 = 1000 total cost
        await execute_paper_trade(
            user_id=user_id, symbol="TCS", stock_name="TCS",
            quantity=10, entry_price=100.0, trade_type="BUY",
            stop_loss=95.0, target1=110.0, target2=0.0,
            setup_type="MOMENTUM", notes="", db=db,
        )

        # ASSERT
        after = await get_paper_balance(user_id, db)
        assert after["balance"] == 99000.0

    asyncio.run(run())


def test_paper_trade_never_places_a_live_broker_order():
    """A paper trade must never reach a real brokerage account.

    D6.1 / S3. This used to patch `services.zerodha_service.place_order`, a
    module that has been deleted — it was the "find any connected session"
    single-broker shim whose `place_order()` would have sent a live order to
    whichever user happened to have connected most recently. Patching
    `broker_engine.place_order` is both the surviving path and the stronger
    assertion: it covers EVERY broker, not just Zerodha.
    """
    async def run():
        # ARRANGE
        db = FakeDB()
        user_id = _new_user_id(db)

        with patch("services.broker_engine.broker_engine.place_order", new_callable=AsyncMock) as mock_place, \
             patch("services.real_market.fetch_real_stock_quote", new_callable=AsyncMock, return_value={"price": 105.0}):
            # ACT
            trade = await execute_paper_trade(
                user_id=user_id, symbol="INFY", stock_name="Infosys",
                quantity=2, entry_price=100.0, trade_type="BUY",
                stop_loss=95.0, target1=110.0, target2=0.0,
                setup_type="MOMENTUM", notes="", db=db,
            )
            await close_paper_trade(trade["_id"], user_id, db)

            # ASSERT
            mock_place.assert_not_called()

    asyncio.run(run())


def test_reset_paper_capital_restores_100000():
    async def run():
        # ARRANGE
        db = FakeDB()
        user_id = _new_user_id(db)
        with patch("services.real_market.fetch_real_stock_quote", new_callable=AsyncMock, return_value={"price": 100.0}):
            await execute_paper_trade(
                user_id=user_id, symbol="WIPRO", stock_name="Wipro",
                quantity=100, entry_price=100.0, trade_type="BUY",
                stop_loss=90.0, target1=120.0, target2=0.0,
                setup_type="MOMENTUM", notes="", db=db,
            )
        drained = await get_paper_balance(user_id, db)
        assert drained["balance"] == 90000.0

        # ACT
        await reset_paper_capital(user_id, db)

        # ASSERT
        restored = await get_paper_balance(user_id, db)
        assert restored["balance"] == 100000.0
        open_trades = [t for t in db.trades.docs if t.get("user_id") == user_id and t.get("status") == "OPEN"]
        assert open_trades == [], "reset must close any still-open paper trades"

    asyncio.run(run())


def test_close_paper_trade_calculates_pnl():
    async def run():
        # ARRANGE
        db = FakeDB()
        user_id = _new_user_id(db)
        with patch("services.real_market.fetch_real_stock_quote", new_callable=AsyncMock, return_value={"price": 100.0}):
            trade = await execute_paper_trade(
                user_id=user_id, symbol="HDFCBANK", stock_name="HDFC Bank",
                quantity=10, entry_price=100.0, trade_type="BUY",
                stop_loss=90.0, target1=130.0, target2=0.0,
                setup_type="MOMENTUM", notes="", db=db,
            )

        # ACT — price rises to 110 by the time the trade is closed
        with patch("services.real_market.fetch_real_stock_quote", new_callable=AsyncMock, return_value={"price": 110.0}):
            result = await close_paper_trade(trade["_id"], user_id, db)

        # ASSERT
        assert result["pnl"] == 100.0  # (110 - 100) * 10
        assert result["pnl_pct"] == 10.0
        assert result["status"] == "CLOSED"

    asyncio.run(run())

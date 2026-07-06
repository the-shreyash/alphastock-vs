"""Tests for AI Trade Coaching: services/trade_journal.py's
generate_trade_coaching(), GET /api/trades/{trade_id}/coaching, and
GET /api/trades/coaching/summary.

The `no_ai` fixture forces claude_configured()/gemini_configured() to False
so the coaching routes take their deterministic template path (ai_func=None)
instead of attempting a real Claude/Gemini call — matching testing.md rule 5.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from bson import ObjectId

import services.trade_journal as trade_journal
from services.trade_journal import generate_trade_coaching


def _closed_trade_doc(**overrides):
    doc = {
        "_id": ObjectId(), "symbol": "RELIANCE", "type": "BUY",
        "entry_price": 2500.0, "exit_price": 2575.0, "stop_loss": 2450.0,
        "target1": 2600.0, "pnl": 750.0, "pnl_percent": 3.0,
        "status": "TARGET_HIT", "setup_type": "MOMENTUM", "is_paper": False,
    }
    doc.update(overrides)
    return doc


# ---------- generate_trade_coaching() — service level ----------
def test_generate_trade_coaching_returns_coaching_object():
    async def run():
        trade = _closed_trade_doc()
        result = await generate_trade_coaching(trade, ai_func=None)
        assert isinstance(result, dict)
        for field in ("grade", "lesson_title", "what_went_right", "what_went_wrong", "next_time", "coaching_text"):
            assert field in result

    asyncio.run(run())


def test_generate_trade_coaching_grade_is_valid():
    async def run():
        trade = _closed_trade_doc()
        result = await generate_trade_coaching(trade, ai_func=None)
        assert result["grade"] in ("A", "B", "C", "D")

    asyncio.run(run())


# ---------- GET /api/trades/{trade_id}/coaching ----------
def test_coaching_endpoint_returns_coaching_object(client, fake_db, test_user, auth_headers, no_ai):
    # ARRANGE
    trade = _closed_trade_doc(user_id=str(test_user["_id"]))
    fake_db.trades.docs.append(trade)

    # ACT
    response = client.get(f"/api/trades/{trade['_id']}/coaching", headers=auth_headers)

    # ASSERT
    assert response.status_code == 200, response.text
    data = response.json()
    assert "grade" in data
    assert "coaching_text" in data


def test_coaching_has_valid_grade(client, fake_db, test_user, auth_headers, no_ai):
    # ARRANGE
    trade = _closed_trade_doc(user_id=str(test_user["_id"]))
    fake_db.trades.docs.append(trade)

    # ACT
    response = client.get(f"/api/trades/{trade['_id']}/coaching", headers=auth_headers)

    # ASSERT
    assert response.json()["grade"] in ("A", "B", "C", "D")


def test_coaching_cached_on_second_call(client, fake_db, test_user, auth_headers, no_ai):
    # ARRANGE
    trade = _closed_trade_doc(user_id=str(test_user["_id"]))
    fake_db.trades.docs.append(trade)
    wrapped = AsyncMock(wraps=generate_trade_coaching)

    # ACT
    with patch.object(trade_journal, "generate_trade_coaching", wrapped):
        first = client.get(f"/api/trades/{trade['_id']}/coaching", headers=auth_headers)
        second = client.get(f"/api/trades/{trade['_id']}/coaching", headers=auth_headers)

    # ASSERT
    assert first.status_code == 200 and second.status_code == 200
    assert wrapped.call_count == 1, "second call should return the cached coaching without regenerating"
    assert first.json() == second.json()


def test_coaching_rejected_for_open_trade(client, fake_db, test_user, auth_headers, no_ai):
    # ARRANGE
    trade = _closed_trade_doc(user_id=str(test_user["_id"]), status="OPEN", exit_price=None, pnl=None, pnl_percent=None)
    fake_db.trades.docs.append(trade)

    # ACT
    response = client.get(f"/api/trades/{trade['_id']}/coaching", headers=auth_headers)

    # ASSERT
    assert response.status_code == 400


def test_coaching_summary_returns_list(client, fake_db, test_user, auth_headers, no_ai):
    # ARRANGE — two closed trades that already have cached coaching
    for i in range(2):
        trade = _closed_trade_doc(user_id=str(test_user["_id"]), exit_time=f"2026-01-0{i+1}T00:00:00")
        trade["coaching"] = {
            "grade": "B", "lesson_title": "t", "coaching_text": "c",
            "what_went_right": "r", "what_went_wrong": "w", "next_time": "n",
        }
        fake_db.trades.docs.append(trade)

    # ACT
    response = client.get("/api/trades/coaching/summary", headers=auth_headers)

    # ASSERT
    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    for item in data:
        assert "trade_id" in item and "symbol" in item and "grade" in item

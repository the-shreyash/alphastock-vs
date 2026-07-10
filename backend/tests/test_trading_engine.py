"""Sprint 9 — Trading Engine tests (services/trading_engine.py + routes).

Hermetic: FakeDB + TestClient via the shared conftest fixtures. Pure engine
math (risk validation, trailing stop, target evaluation, partial exits) is
tested directly; the /api/trades lifecycle endpoints are tested in-process.
No broker or market-data network calls — broker-linked paths are exercised
only up to their explicit local failure modes (e.g. at_market without a
linked broker → 400).
"""
from datetime import datetime, timezone

from bson import ObjectId

from services import trading_engine as te


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _user(**overrides):
    user = {"_id": "u1", "capital": 100000.0, "risk_level": "moderate",
            "max_daily_loss": 5000.0, "max_trades_per_day": 3}
    user.update(overrides)
    return user


def _trade(**overrides):
    trade = {"type": "BUY", "entry_price": 100.0, "quantity": 10,
             "stop_loss": 95.0, "target1": 110.0, "target2": None, "target3": None}
    trade.update(overrides)
    return trade


# ─── Risk Manager: validate_trade ────────────────────────────────────────────

def test_valid_long_trade_is_approved_with_metrics():
    result = te.validate_trade(_user(), _trade(), trades_today=0, today_realized_pnl=0)
    assert result["approved"] is True
    assert result["violations"] == []
    m = result["metrics"]
    assert m["risk_amount"] == 50.0          # (100-95) × 10
    assert m["reward_amount"] == 100.0       # (110-100) × 10
    assert m["risk_reward"] == 2.0
    assert m["position_value"] == 1000.0
    # moderate → 2% of 1,00,000 = 2000 budget / 5 per-share risk = 400
    assert m["suggested_quantity"] == 400


def test_sl_above_entry_blocks_long():
    result = te.validate_trade(_user(), _trade(stop_loss=105), 0, 0)
    assert result["approved"] is False
    assert any("Stop loss must be below" in v for v in result["violations"])


def test_sl_below_entry_blocks_short():
    result = te.validate_trade(
        _user(), _trade(type="SELL", stop_loss=95, target1=90), 0, 0)
    assert result["approved"] is False
    assert any("above" in v for v in result["violations"])


def test_target_on_wrong_side_blocks():
    result = te.validate_trade(_user(), _trade(target1=99), 0, 0)
    assert result["approved"] is False
    assert any("Target 1" in v for v in result["violations"])


def test_targets_out_of_order_block():
    result = te.validate_trade(_user(), _trade(target1=110, target2=105), 0, 0)
    assert result["approved"] is False
    assert any("Target 2" in v and "beyond" in v for v in result["violations"])


def test_daily_trade_limit_blocks():
    result = te.validate_trade(_user(max_trades_per_day=2), _trade(), trades_today=2,
                               today_realized_pnl=0)
    assert result["approved"] is False
    assert any("Daily trade limit" in v for v in result["violations"])


def test_daily_loss_limit_blocks():
    result = te.validate_trade(_user(max_daily_loss=5000), _trade(), 0,
                               today_realized_pnl=-5000)
    assert result["approved"] is False
    assert any("Daily loss limit" in v for v in result["violations"])


def test_risk_beyond_remaining_loss_budget_blocks():
    # 4,000 already lost → ₹1,000 budget left; trade risks (100-80)×100 = 2,000.
    result = te.validate_trade(
        _user(max_daily_loss=5000),
        _trade(stop_loss=80, quantity=100), 0, today_realized_pnl=-4000)
    assert result["approved"] is False
    assert any("loss budget" in v for v in result["violations"])


def test_oversized_risk_and_poor_rr_warn_but_do_not_block():
    # Risk = (100-90)×300 = 3,000 = 3% of capital (> 2% moderate guideline);
    # reward at T1 = 5 per share → R:R 0.5.
    result = te.validate_trade(
        _user(), _trade(stop_loss=90, target1=105, quantity=300), 0, 0)
    assert result["approved"] is True
    assert any("guideline" in w for w in result["warnings"])
    assert any("Risk:reward" in w for w in result["warnings"])


# ─── Trailing stop ───────────────────────────────────────────────────────────

def test_trailing_stop_ratchets_up_for_long_percent():
    trade = _trade(trailing_stop={"enabled": True, "type": "percent", "value": 2},
                   best_price=100.0)
    changes = te.update_trailing_stop(trade, 110.0)
    assert changes["best_price"] == 110.0
    assert changes["stop_loss"] == 107.8      # 110 × (1 − 0.02)


def test_trailing_stop_never_loosens():
    trade = _trade(stop_loss=107.8, best_price=110.0,
                   trailing_stop={"enabled": True, "type": "percent", "value": 2})
    assert te.update_trailing_stop(trade, 105.0) == {}   # pullback: no change


def test_trailing_stop_points_for_short():
    trade = _trade(type="SELL", entry_price=100, stop_loss=105, target1=90,
                   best_price=100.0,
                   trailing_stop={"enabled": True, "type": "points", "value": 3})
    changes = te.update_trailing_stop(trade, 94.0)
    assert changes["best_price"] == 94.0
    assert changes["stop_loss"] == 97.0       # 94 + 3, tighter than 105


def test_disabled_trailing_still_tracks_best_price():
    trade = _trade(best_price=100.0)
    changes = te.update_trailing_stop(trade, 108.0)
    assert changes == {"best_price": 108.0}


# ─── Target / SL evaluation ──────────────────────────────────────────────────

def test_sl_hit_takes_priority():
    trade = _trade(status="OPEN", quantity_open=10)
    actions = te.evaluate_trade(trade, 94.0)
    assert actions == [{"action": "SL_HIT", "quantity": 10}]


def test_single_target_hit_books_everything_once():
    trade = _trade(status="OPEN", quantity_open=10, targets_hit=[])
    actions = te.evaluate_trade(trade, 111.0)
    assert actions == [{"action": "TARGET_HIT", "level": 1,
                        "target_price": 110.0, "quantity": 10}]
    # Already-hit targets never fire again.
    trade["targets_hit"] = [{"level": 1}]
    assert te.evaluate_trade(trade, 112.0) == []


def test_multi_target_split_and_final_target_closes_remainder():
    trade = _trade(status="OPEN", quantity=9, quantity_open=9,
                   target1=110, target2=120, target3=130, targets_hit=[])
    # T1 alone: books a third of the original quantity.
    assert te.evaluate_trade(trade, 111.0) == [
        {"action": "TARGET_HIT", "level": 1, "target_price": 110.0, "quantity": 3}]
    # A gap through every target books 3 + 3 then closes the remainder.
    actions = te.evaluate_trade(trade, 131.0)
    assert [(a["level"], a["quantity"]) for a in actions] == [(1, 3), (2, 3), (3, 3)]


def test_partial_exit_keeps_trade_open_and_books_pnl():
    trade = _trade(status="OPEN", quantity_open=10, realized_pnl=0)
    update = te.apply_partial_exit(trade, 110.0, 4, "TARGET_HIT")
    assert update["quantity_open"] == 6
    assert update["realized_pnl"] == 40.0
    assert "status" not in update


def test_final_exit_closes_with_combined_pnl():
    trade = _trade(status="OPEN", quantity_open=6, realized_pnl=40.0)
    update = te.apply_partial_exit(trade, 112.0, 6, "TARGET_HIT")
    assert update["status"] == "TARGET_HIT"
    assert update["pnl"] == 112.0            # 40 + (112-100)×6
    assert update["exit_price"] == 112.0
    assert update["pnl_percent"] == 11.2     # 112 / (100×10)


def test_short_partial_exit_pnl():
    trade = _trade(type="SELL", entry_price=100, stop_loss=105, target1=90,
                   status="OPEN", quantity_open=10, realized_pnl=0)
    update = te.apply_partial_exit(trade, 92.0, 10, "TARGET_HIT")
    assert update["pnl"] == 80.0             # (100-92)×10


# ─── Endpoints ───────────────────────────────────────────────────────────────

def _trade_payload(**overrides):
    payload = {"symbol": "TCS", "stock_name": "TCS Ltd", "type": "BUY",
               "entry_price": 100, "quantity": 10, "stop_loss": 95,
               "target1": 110}
    payload.update(overrides)
    return payload


def test_validate_endpoint_returns_risk_check(client, fake_db, test_user, auth_headers):
    resp = client.post("/api/trades/validate", json=_trade_payload(), headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved"] is True
    assert body["metrics"]["risk_reward"] == 2.0


def test_create_trade_blocked_on_violation(client, fake_db, test_user, auth_headers):
    resp = client.post("/api/trades", json=_trade_payload(stop_loss=105),
                       headers=auth_headers)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["violations"]
    assert len(fake_db.trades.docs) == 0     # nothing recorded


def test_create_trade_blocked_when_daily_limit_reached(client, fake_db, test_user, auth_headers):
    test_user["max_trades_per_day"] = 1
    fake_db.trades.docs.append({
        "_id": ObjectId(), "user_id": str(test_user["_id"]), "symbol": "INFY",
        "status": "OPEN", "entry_time": _now_iso(), "entry_price": 1, "quantity": 1,
        "stop_loss": 0.5, "target1": 2,
    })
    resp = client.post("/api/trades", json=_trade_payload(), headers=auth_headers)
    assert resp.status_code == 422
    assert any("Daily trade limit" in v for v in resp.json()["detail"]["violations"])


def test_create_trade_seeds_engine_fields(client, fake_db, test_user, auth_headers):
    payload = _trade_payload(
        target2=120, target3=130,
        trailing_stop={"enabled": True, "type": "percent", "value": 2})
    resp = client.post("/api/trades", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["quantity_open"] == 10
    assert body["initial_stop_loss"] == 95
    assert body["trailing_stop"]["enabled"] is True
    assert body["targets_hit"] == []
    assert body["events"][0]["type"] == "ENTRY"
    assert body["auto_exit"] is False        # no broker → consent flag ignored
    assert body["broker"] is None


def test_modify_stop_loss_and_targets(client, fake_db, test_user, auth_headers):
    trade_id = ObjectId()
    fake_db.trades.docs.append({
        "_id": trade_id, "user_id": str(test_user["_id"]), "symbol": "TCS",
        "type": "BUY", "status": "OPEN", "entry_price": 100.0, "quantity": 10,
        "quantity_open": 10, "stop_loss": 95.0, "target1": 110.0, "events": [],
    })
    resp = client.put(f"/api/trades/{trade_id}",
                      json={"stop_loss": 101, "target2": 118},
                      headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["stop_loss"] == 101          # breakeven+ stop is allowed
    assert body["target2"] == 118
    assert body["events"][-1]["type"] == "MODIFIED"


def test_modify_rejects_sl_beyond_target(client, fake_db, test_user, auth_headers):
    trade_id = ObjectId()
    fake_db.trades.docs.append({
        "_id": trade_id, "user_id": str(test_user["_id"]), "symbol": "TCS",
        "type": "BUY", "status": "OPEN", "entry_price": 100.0, "quantity": 10,
        "stop_loss": 95.0, "target1": 110.0,
    })
    resp = client.put(f"/api/trades/{trade_id}", json={"stop_loss": 115},
                      headers=auth_headers)
    assert resp.status_code == 422


def test_partial_exit_endpoint(client, fake_db, test_user, auth_headers, no_ai):
    trade_id = ObjectId()
    fake_db.trades.docs.append({
        "_id": trade_id, "user_id": str(test_user["_id"]), "symbol": "TCS",
        "type": "BUY", "status": "OPEN", "entry_price": 100.0, "quantity": 10,
        "quantity_open": 10, "realized_pnl": 0, "stop_loss": 95.0, "target1": 110.0,
        "events": [],
    })
    resp = client.post(f"/api/trades/{trade_id}/exit",
                       json={"exit_price": 108, "quantity": 4}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "OPEN"
    assert body["quantity_open"] == 6
    assert body["realized_pnl"] == 32.0
    # Then close the rest at target.
    resp = client.post(f"/api/trades/{trade_id}/exit",
                       json={"exit_price": 111}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "TARGET_HIT"
    assert body["pnl"] == 98.0               # 32 + (111-100)×6


def test_exit_at_market_requires_broker_link(client, fake_db, test_user, auth_headers):
    trade_id = ObjectId()
    fake_db.trades.docs.append({
        "_id": trade_id, "user_id": str(test_user["_id"]), "symbol": "TCS",
        "type": "BUY", "status": "OPEN", "entry_price": 100.0, "quantity": 10,
        "stop_loss": 95.0, "target1": 110.0,
    })
    resp = client.post(f"/api/trades/{trade_id}/exit",
                       json={"at_market": True}, headers=auth_headers)
    assert resp.status_code == 400


def test_risk_summary_endpoint(client, fake_db, test_user, auth_headers):
    test_user["max_daily_loss"] = 5000
    test_user["max_trades_per_day"] = 3
    uid = str(test_user["_id"])
    today_iso = _now_iso()
    fake_db.trades.docs.extend([
        {"_id": ObjectId(), "user_id": uid, "symbol": "TCS", "type": "BUY",
         "status": "OPEN", "entry_price": 100.0, "quantity": 10, "quantity_open": 10,
         "stop_loss": 95.0, "target1": 110.0, "entry_time": today_iso},
        {"_id": ObjectId(), "user_id": uid, "symbol": "INFY", "type": "BUY",
         "status": "SL_HIT", "entry_price": 50.0, "quantity": 10, "stop_loss": 45.0,
         "target1": 60.0, "pnl": -1000.0, "entry_time": today_iso,
         "exit_time": today_iso},
    ])
    resp = client.get("/api/trades/risk/summary", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["trades_today"] == 2
    assert body["realized_pnl_today"] == -1000.0
    assert body["loss_budget_remaining"] == 4000.0
    assert body["loss_budget_used_pct"] == 20.0
    assert body["open_risk"] == 50.0         # (100-95)×10
    assert body["trading_halted"] is False


def test_unified_orders_endpoint(client, fake_db, test_user, auth_headers):
    uid = str(test_user["_id"])
    fake_db.orders.docs.extend([
        {"_id": ObjectId(), "user_id": uid, "broker": "zerodha", "order_id": "A1",
         "symbol": "TCS", "status": "FILLED", "placed_at": "2026-07-10T10:00:00"},
        {"_id": ObjectId(), "user_id": uid, "broker": "upstox", "order_id": "B2",
         "symbol": "INFY", "status": "OPEN", "placed_at": "2026-07-10T11:00:00"},
        {"_id": ObjectId(), "user_id": "someone-else", "broker": "zerodha",
         "order_id": "C3", "symbol": "SBIN", "status": "FILLED",
         "placed_at": "2026-07-10T09:00:00"},
    ])
    resp = client.get("/api/orders", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2                # only this user's orders
    assert body["orders"][0]["order_id"] == "B2"   # newest first
    # Broker filter narrows further.
    resp = client.get("/api/orders?broker=zerodha", headers=auth_headers)
    assert resp.json()["count"] == 1


# ─── Trading platform selection (Sprint 9.1 — no default broker) ────────────

def test_settings_persists_preferred_broker(client, fake_db, test_user, auth_headers):
    resp = client.put("/api/settings", json={"preferred_broker": "zerodha"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["preferred_broker"] == "zerodha"
    # "" clears the choice — back to "no platform selected".
    resp = client.put("/api/settings", json={"preferred_broker": ""}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["preferred_broker"] is None


def test_settings_rejects_unsupported_broker(client, fake_db, test_user, auth_headers):
    resp = client.put("/api/settings", json={"preferred_broker": "robinhood"}, headers=auth_headers)
    assert resp.status_code == 422


def test_quick_trade_requires_platform_choice(client, fake_db, test_user, auth_headers):
    """No default broker: quick trade is refused until the user picks one."""
    resp = client.post("/api/trades/quick", json={
        "symbol": "TCS", "entry_price": 100, "quantity": 1,
        "stop_loss": 95, "target1": 110,
    }, headers=auth_headers)
    assert resp.status_code == 400
    assert "Trading Platform" in resp.json()["detail"]
    assert len(fake_db.trades.docs) == 0


def test_quick_trade_requires_connected_platform(client, fake_db, test_user, auth_headers, monkeypatch):
    import server
    test_user["preferred_broker"] = "zerodha"

    class _StubEngine:
        async def get_status(self, user_id):
            return {"zerodha": {"connected": False, "display_name": "Zerodha"}}

    monkeypatch.setattr(server, "broker_engine", _StubEngine())
    resp = client.post("/api/trades/quick", json={
        "symbol": "TCS", "entry_price": 100, "quantity": 1,
        "stop_loss": 95, "target1": 110,
    }, headers=auth_headers)
    assert resp.status_code == 400
    assert "not connected" in resp.json()["detail"]
    assert len(fake_db.trades.docs) == 0


def test_quick_trade_places_order_on_chosen_platform(client, fake_db, test_user, auth_headers, monkeypatch):
    import server
    test_user["preferred_broker"] = "upstox"
    placed = {}

    class _StubEngine:
        async def get_status(self, user_id):
            return {"upstox": {"connected": True, "display_name": "Upstox"}}

        async def place_order(self, user_id, broker, order):
            placed.update({"broker": broker, **order})
            return {"order_id": "UPX-1", "status": "PENDING"}

    monkeypatch.setattr(server, "broker_engine", _StubEngine())
    resp = client.post("/api/trades/quick", json={
        "symbol": "TCS", "stock_name": "TCS Ltd", "entry_price": 100,
        "quantity": 2, "stop_loss": 95, "target1": 110, "confidence": 84,
    }, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["broker"] == "upstox"          # the user's chosen platform
    assert body["order_id"] == "UPX-1"
    assert placed["broker"] == "upstox"
    assert placed["transaction_type"] == "BUY" and placed["quantity"] == 2
    trade = fake_db.trades.docs[0]
    assert trade["broker"] == "upstox"
    assert trade["broker_order_id"] == "UPX-1"
    assert trade["ai_confidence"] == 84


# ─── Monitor cycle (run_cycle) ───────────────────────────────────────────────

def test_run_cycle_trails_and_books_targets():
    import asyncio
    from tests._fakedb import FakeDB

    async def run():
        db = FakeDB()
        trade_id = ObjectId()
        db.trades.docs.append({
            "_id": trade_id, "user_id": "u1", "symbol": "TCS", "type": "BUY",
            "status": "OPEN", "entry_price": 100.0, "quantity": 10,
            "quantity_open": 10, "realized_pnl": 0, "stop_loss": 95.0,
            "target1": 110.0, "target2": 120.0, "best_price": 100.0,
            "targets_hit": [], "events": [],
            "trailing_stop": {"enabled": True, "type": "percent", "value": 5},
        })
        stats = await te.run_cycle(db, {"TCS": {"price": 112.0}})
        assert stats == {"checked": 1, "trailed": 1, "targets_hit": 1,
                         "sl_exits": 0, "auto_orders": 0}
        doc = db.trades.docs[0]
        assert doc["stop_loss"] == 106.4                  # 112 × 0.95 > 95
        assert doc["best_price"] == 112.0
        assert doc["targets_hit"][0]["level"] == 1
        assert doc["status"] == "OPEN"                    # alert-only: no auto exit
        assert any(e["type"] == "TARGET_HIT" for e in doc["events"])
        assert any(e["type"] == "TRAILING_SL" for e in doc["events"])
        assert len(db.notifications.docs) == 1            # target alert sent

        # Second cycle at the same price: nothing new fires (dedup via state).
        stats = await te.run_cycle(db, {"TCS": {"price": 112.0}})
        assert stats["targets_hit"] == 0 and stats["trailed"] == 0
        assert len(db.notifications.docs) == 1

    asyncio.run(run())


def test_run_cycle_skips_paper_and_unquoted_trades():
    import asyncio
    from tests._fakedb import FakeDB

    async def run():
        db = FakeDB()
        db.trades.docs.extend([
            {"_id": ObjectId(), "user_id": "u1", "symbol": "TCS", "type": "BUY",
             "status": "OPEN", "entry_price": 100.0, "quantity": 10,
             "stop_loss": 95.0, "target1": 110.0, "is_paper": True},
            {"_id": ObjectId(), "user_id": "u1", "symbol": "NOQUOTE", "type": "BUY",
             "status": "OPEN", "entry_price": 100.0, "quantity": 10,
             "stop_loss": 95.0, "target1": 110.0},
        ])
        stats = await te.run_cycle(db, {"TCS": {"price": 120.0}})
        assert stats["checked"] == 0

    asyncio.run(run())

"""Sprint 7 — Broker Integration tests (hermetic, no real broker calls).

Covers:
  • Token encryption at rest (Fernet roundtrip + legacy plaintext migration)
  • Adapter interface: login URLs, session expiry rules, response normalization
  • BrokerEngine: encrypted storage, session freshness, order audit logging
  • Kite ticker binary frame parsing
  • Unified /api/brokers route registration + auth guards

All broker HTTP traffic is mocked at BrokerAdapter._request — the single
chokepoint every adapter call goes through — so no test ever reaches a real
broker API (compliance: never hit official APIs from CI).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

from tests._fakedb import FakeDB

from services.brokers.base import IST, BrokerAuthError, BrokerError, normalize_status
from services.brokers.crypto import decrypt_token, encrypt_token, is_encrypted
from services.brokers.registry import broker_registry
# D4.2 moved Kite's binary framing out of the shared transport and into the
# adapter that owns the protocol. Same parser, same expectations, new home.
from services.brokers.zerodha import parse_kite_binary
from services.brokers.upstox import UpstoxAdapter
from services.brokers.zerodha import ZerodhaAdapter
from services.broker_engine import BrokerEngine


# ---------------------------------------------------------------- crypto

def test_token_encrypt_decrypt_roundtrip():
    token = "abc123-secret-access-token"
    stored = encrypt_token(token)
    assert stored != token
    assert is_encrypted(stored)
    assert decrypt_token(stored) == token


def test_decrypt_passes_through_legacy_plaintext():
    # Pre-Sprint-7 records stored raw tokens; they must keep working.
    assert decrypt_token("legacy-plaintext-token") == "legacy-plaintext-token"
    assert not is_encrypted("legacy-plaintext-token")


def test_encrypt_empty_token_is_empty():
    assert encrypt_token("") == ""
    assert decrypt_token("") == ""


# ---------------------------------------------------------------- status normalization

def test_normalize_status_maps_broker_specific_values():
    assert normalize_status("COMPLETE") == "FILLED"
    assert normalize_status("open") == "OPEN"
    assert normalize_status("TRIGGER PENDING") == "PENDING"
    assert normalize_status("CANCELLED") == "CANCELLED"
    assert normalize_status("REJECTED") == "REJECTED"


# ---------------------------------------------------------------- login URLs

def test_zerodha_login_url_carries_uid(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "testkey")
    monkeypatch.setenv("KITE_API_SECRET", "testsecret")
    result = ZerodhaAdapter().get_login_url(user_id="user42")
    assert result["configured"] is True
    assert "api_key=testkey" in result["url"]
    assert "uid%3Duser42" in result["url"]  # redirect_params is URL-encoded


def test_zerodha_login_url_unconfigured(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "")
    result = ZerodhaAdapter().get_login_url()
    assert result["configured"] is False
    assert result["url"] is None


def test_upstox_login_url_carries_state(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "upx-key")
    monkeypatch.setenv("UPSTOX_API_SECRET", "upx-secret")
    monkeypatch.setenv("UPSTOX_REDIRECT_URL", "https://app.example.com/api/brokers/upstox/callback")
    result = UpstoxAdapter().get_login_url(user_id="user42")
    assert result["configured"] is True
    assert "client_id=upx-key" in result["url"]
    assert "state=uid%3Duser42" in result["url"]


def test_upstox_login_url_unconfigured(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "")
    monkeypatch.setenv("UPSTOX_API_SECRET", "")
    monkeypatch.setenv("UPSTOX_REDIRECT_URL", "")
    result = UpstoxAdapter().get_login_url()
    assert result["configured"] is False


# ---------------------------------------------------------------- session expiry rules

def test_zerodha_session_expires_at_6am_ist_next_day():
    # Connected 10:00 IST → expires 06:00 IST the NEXT day.
    connected = datetime(2026, 7, 9, 10, 0, tzinfo=IST)
    expiry = ZerodhaAdapter().session_expiry(connected).astimezone(IST)
    assert (expiry.day, expiry.hour, expiry.minute) == (10, 6, 0)

    # Connected 05:00 IST (pre-dawn) → expires 06:00 IST the SAME day.
    connected = datetime(2026, 7, 9, 5, 0, tzinfo=IST)
    expiry = ZerodhaAdapter().session_expiry(connected).astimezone(IST)
    assert (expiry.day, expiry.hour) == (9, 6)


def test_upstox_session_expires_at_330am_ist():
    connected = datetime(2026, 7, 9, 10, 0, tzinfo=IST)
    expiry = UpstoxAdapter().session_expiry(connected).astimezone(IST)
    assert (expiry.day, expiry.hour, expiry.minute) == (10, 3, 30)


def test_session_is_fresh_checks_expiry():
    adapter = ZerodhaAdapter()
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert adapter.session_is_fresh({"expires_at": future}) is True
    assert adapter.session_is_fresh({"expires_at": past}) is False
    assert adapter.session_is_fresh({}) is False


# ---------------------------------------------------------------- adapter normalization

def test_zerodha_holdings_normalized(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    adapter = ZerodhaAdapter()
    kite_payload = {"status": "success", "data": [{
        "tradingsymbol": "INFY", "exchange": "NSE", "quantity": 8, "t1_quantity": 2,
        "average_price": 1500.0, "last_price": 1600.0, "pnl": 1000.0,
        "product": "CNC", "isin": "INE009A01021", "instrument_token": 408065,
    }]}
    with patch.object(ZerodhaAdapter, "_request", new_callable=AsyncMock, return_value=kite_payload):
        holdings = asyncio.run(adapter.get_holdings({"access_token": "tok"}))
    h = holdings[0]
    assert h["symbol"] == "INFY"
    assert h["quantity"] == 10                    # quantity + t1_quantity
    assert h["invested_value"] == 15000.0
    assert h["market_value"] == 16000.0
    assert h["pnl"] == 1000.0
    assert h["instrument_token"] == 408065


def test_zerodha_token_exception_raises_auth_error(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    adapter = ZerodhaAdapter()
    err_payload = {"status": "error", "error_type": "TokenException", "message": "Token is invalid"}
    with patch.object(ZerodhaAdapter, "_request", new_callable=AsyncMock, return_value=err_payload):
        with pytest.raises(BrokerAuthError):
            asyncio.run(adapter.get_holdings({"access_token": "stale"}))


def test_zerodha_requires_connection_before_calls():
    with pytest.raises(BrokerAuthError):
        asyncio.run(ZerodhaAdapter().get_holdings({}))  # no access_token


class _FakeResponse:
    """Minimal httpx-like response for exercising _request's status handling."""
    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def _run_request(adapter, response):
    """Drive adapter._request against a canned response (httpx patched out)."""
    class _FakeClient:
        async def __aenter__(self_): return self_
        async def __aexit__(self_, *a): return False
        async def request(self_, *a, **k): return response
    with patch("services.brokers.base.httpx.AsyncClient", return_value=_FakeClient()):
        return asyncio.run(adapter._request("POST", "https://api/orders/regular"))


def test_403_with_broker_message_surfaces_real_reason(monkeypatch):
    # A permission/order-window rejection must NOT be mislabeled "session expired".
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    adapter = ZerodhaAdapter()
    resp = _FakeResponse(403, {"status": "error", "error_type": "PermissionException",
                               "message": "Order placement is blocked outside market hours"})
    with pytest.raises(BrokerError) as exc:
        _run_request(adapter, resp)
    assert not isinstance(exc.value, BrokerAuthError)
    assert exc.value.user_message == "Order placement is blocked outside market hours"


def test_401_still_maps_to_auth_error(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    adapter = ZerodhaAdapter()
    resp = _FakeResponse(401, {"status": "error", "error_type": "TokenException",
                               "message": "Invalid token"})
    with pytest.raises(BrokerAuthError):
        _run_request(adapter, resp)


def test_403_token_exception_maps_to_auth_error(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    adapter = ZerodhaAdapter()
    resp = _FakeResponse(403, {"status": "error", "error_type": "TokenException",
                               "message": "Token is invalid"})
    with pytest.raises(BrokerAuthError):
        _run_request(adapter, resp)


def test_upstox_order_normalized_status():
    order = UpstoxAdapter._normalize_order({
        "order_id": "240709000001", "trading_symbol": "TCS", "exchange": "NSE",
        "transaction_type": "BUY", "order_type": "LIMIT", "product": "D",
        "quantity": 5, "filled_quantity": 5, "pending_quantity": 0,
        "price": 4000.0, "average_price": 3999.5, "status": "complete",
    })
    assert order["status"] == "FILLED"
    assert order["broker"] == "upstox"
    assert order["symbol"] == "TCS"


def test_upstox_place_order_resolves_instrument_from_holdings(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "k")
    monkeypatch.setenv("UPSTOX_API_SECRET", "s")
    monkeypatch.setenv("UPSTOX_REDIRECT_URL", "https://x/cb")
    adapter = UpstoxAdapter()
    calls = []

    async def fake_request(self, method, url, headers=None, data=None, json_body=None, timeout=12.0):
        calls.append((method, url, json_body))
        if "short-term-positions" in url:
            return {"status": "success", "data": []}
        if "long-term-holdings" in url:
            return {"status": "success", "data": [{
                "trading_symbol": "INFY", "exchange": "NSE", "quantity": 10,
                "average_price": 1500.0, "last_price": 1600.0,
                "instrument_token": "NSE_EQ|INE009A01021",
            }]}
        if "/order/place" in url:
            return {"status": "success", "data": {"order_id": "UPX123"}}
        raise AssertionError(f"unexpected url {url}")

    with patch.object(UpstoxAdapter, "_request", fake_request):
        result = asyncio.run(adapter.place_order(
            {"access_token": "tok"},
            {"symbol": "INFY", "transaction_type": "SELL", "quantity": 2, "order_type": "MARKET"}))
    assert result["order_id"] == "UPX123"
    placed = [c for c in calls if "/order/place" in c[1]][0]
    assert placed[2]["instrument_token"] == "NSE_EQ|INE009A01021"
    assert placed[2]["transaction_type"] == "SELL"


# ---------------------------------------------------------------- kite binary parsing

def test_parse_kite_binary_ltp_packet():
    import struct
    # 1 packet, length 8: token=408065, ltp=160050 paise (₹1600.50)
    frame = struct.pack(">H", 1) + struct.pack(">H", 8) + struct.pack(">ii", 408065, 160050)
    ticks = parse_kite_binary(frame)
    assert ticks == [{"instrument_token": 408065, "last_price": 1600.50}]


def test_parse_kite_binary_heartbeat_and_garbage():
    assert parse_kite_binary(b"\x00") == []       # heartbeat
    assert parse_kite_binary(b"") == []
    assert parse_kite_binary(b"\x00\x02\xff") == []  # truncated frame


# ---------------------------------------------------------------- broker engine

def _engine_with_fakedb():
    engine = BrokerEngine()
    engine.configure(FakeDB())
    return engine


def _fresh_session(adapter, **extra):
    now = datetime.now(timezone.utc)
    return {
        "access_token": "live-token", "refresh_token": "", "public_token": "",
        "expires_at": adapter.session_expiry(now).isoformat(),
        "connected_at": now.isoformat(),
        "account_id": "AB1234", "profile": {"user_id": "AB1234"},
        **extra,
    }


def test_engine_stores_tokens_encrypted(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    engine = _engine_with_fakedb()
    uid = str(ObjectId())
    session = _fresh_session(engine.adapter("zerodha"))

    asyncio.run(engine._save_account(uid, "zerodha", session))

    stored = engine.db.broker_accounts.docs[0]
    assert stored["access_token"] != "live-token"
    assert is_encrypted(stored["access_token"])
    # And the engine can read it back decrypted.
    loaded = asyncio.run(engine._load_account(uid, "zerodha"))
    assert loaded["access_token"] == "live-token"


def test_engine_migrates_legacy_plaintext_tokens(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    engine = _engine_with_fakedb()
    uid = str(ObjectId())
    # A pre-Sprint-7 record: plaintext token, no expires_at.
    engine.db.broker_accounts.docs.append({
        "user_id": uid, "broker": "zerodha",
        "access_token": "legacy-plain", "public_token": "",
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "profile": {"user_id": "AB1234"},
    })

    loaded = asyncio.run(engine._load_account(uid, "zerodha"))

    assert loaded["access_token"] == "legacy-plain"
    stored = engine.db.broker_accounts.docs[0]
    assert is_encrypted(stored["access_token"])          # migrated at rest
    assert stored.get("expires_at")                       # expiry derived


def test_engine_get_session_raises_when_expired(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    engine = _engine_with_fakedb()
    uid = str(ObjectId())
    expired = _fresh_session(engine.adapter("zerodha"))
    expired["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    asyncio.run(engine._save_account(uid, "zerodha", expired))
    engine._sessions.clear()  # force a DB load

    with pytest.raises(BrokerAuthError):
        asyncio.run(engine.get_session(uid, "zerodha"))


def test_engine_get_session_raises_when_never_connected():
    engine = _engine_with_fakedb()
    with pytest.raises(BrokerAuthError):
        asyncio.run(engine.get_session(str(ObjectId()), "upstox"))


def test_engine_place_order_writes_audit_log(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    engine = _engine_with_fakedb()
    uid = str(ObjectId())
    asyncio.run(engine._save_account(uid, "zerodha", _fresh_session(engine.adapter("zerodha"))))

    ok_payload = {"status": "success", "data": {"order_id": "Z9001"}}
    with patch.object(ZerodhaAdapter, "_request", new_callable=AsyncMock, return_value=ok_payload):
        result = asyncio.run(engine.place_order(uid, "zerodha", {
            "symbol": "INFY", "transaction_type": "BUY", "quantity": 1,
            "order_type": "MARKET", "product": "CNC",
        }))

    assert result["order_id"] == "Z9001"
    # Order recorded for tracking
    assert any(o.get("order_id") == "Z9001" for o in engine.db.orders.docs)
    # Audit entry written, with no token material anywhere in it
    audits = [a for a in engine.db.audit_logs.docs if a["action"] == "broker.order.placed"]
    assert len(audits) == 1
    assert "live-token" not in str(audits[0])


def test_engine_status_reports_all_brokers(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    monkeypatch.setenv("UPSTOX_API_KEY", "")
    monkeypatch.setenv("UPSTOX_API_SECRET", "")
    monkeypatch.setenv("UPSTOX_REDIRECT_URL", "")
    engine = _engine_with_fakedb()
    uid = str(ObjectId())
    asyncio.run(engine._save_account(uid, "zerodha", _fresh_session(engine.adapter("zerodha"))))

    status = asyncio.run(engine.get_status(uid))

    # Every registered broker, read from the registry rather than listed here:
    # a status surface that omits a broker is the defect, and a literal set
    # would have to be edited by each new adapter — which is the friction the
    # framework exists to remove (D4.9 added the third).
    assert set(status.keys()) == set(broker_registry.names())
    assert {"zerodha", "upstox"} <= set(status.keys())
    assert status["zerodha"]["connected"] is True
    assert status["zerodha"]["mode"] == "live"
    assert status["upstox"]["connected"] is False
    assert status["upstox"]["configured"] is False


def test_engine_disconnect_clears_tokens(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    engine = _engine_with_fakedb()
    uid = str(ObjectId())
    asyncio.run(engine._save_account(uid, "zerodha", _fresh_session(engine.adapter("zerodha"))))

    with patch.object(ZerodhaAdapter, "_request", new_callable=AsyncMock,
                      return_value={"status": "success", "data": {}}):
        result = asyncio.run(engine.disconnect("zerodha", uid))

    assert result["success"] is True
    stored = engine.db.broker_accounts.docs[0]
    assert stored["access_token"] == ""
    assert stored["connected"] is False
    assert (uid, "zerodha") not in engine._sessions


# ---------------------------------------------------------------- API routes

def test_unified_broker_routes_registered(client):
    from server import app
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    for expected in (
        "/api/brokers", "/api/brokers/status",
        "/api/brokers/{broker}/login-url", "/api/brokers/{broker}/session",
        "/api/brokers/{broker}/callback", "/api/brokers/{broker}/disconnect",
        "/api/brokers/{broker}/sync", "/api/brokers/{broker}/holdings",
        "/api/brokers/{broker}/positions", "/api/brokers/{broker}/orders",
        "/api/brokers/{broker}/orders/{order_id}", "/api/brokers/{broker}/trades",
        "/api/brokers/{broker}/funds", "/api/brokers/{broker}/margins",
    ):
        assert expected in paths, f"missing route {expected}"


def test_broker_routes_require_auth(client):
    assert client.get("/api/brokers").status_code in (401, 403)
    assert client.post("/api/brokers/zerodha/sync").status_code in (401, 403)


def test_unknown_broker_returns_404(client, fake_db, auth_headers, monkeypatch):
    import server
    from services.broker_engine import broker_engine as live_engine
    monkeypatch.setattr(live_engine, "db", fake_db)
    resp = client.get("/api/brokers/robinhood/login-url", headers=auth_headers)
    assert resp.status_code == 404


def test_broker_status_endpoint_shape(client, fake_db, auth_headers, monkeypatch):
    from services.broker_engine import broker_engine as live_engine
    monkeypatch.setattr(live_engine, "db", fake_db)
    resp = client.get("/api/brokers/status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "zerodha" in body and "upstox" in body
    for broker in body.values():
        for key in ("configured", "connected", "mode", "message", "display_name"):
            assert key in broker


def test_holdings_endpoint_returns_409_when_not_connected(client, fake_db, auth_headers, monkeypatch):
    """BROKER_AUTH must NOT be a 401 (the frontend would treat it as an app
    session failure); it surfaces as 409 with a reconnect message."""
    from services.broker_engine import broker_engine as live_engine
    monkeypatch.setattr(live_engine, "db", fake_db)
    live_engine._sessions.clear()
    resp = client.get("/api/brokers/upstox/holdings", headers=auth_headers)
    assert resp.status_code == 409
    assert resp.json()["code"] == "BROKER_AUTH"


def test_place_order_validates_body(client, fake_db, auth_headers, monkeypatch):
    from services.broker_engine import broker_engine as live_engine
    monkeypatch.setattr(live_engine, "db", fake_db)
    # negative quantity → pydantic validation error, broker never called
    resp = client.post("/api/brokers/zerodha/orders", headers=auth_headers, json={
        "symbol": "INFY", "quantity": -5, "transaction_type": "BUY",
    })
    assert resp.status_code == 422

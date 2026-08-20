"""Phase 2 backend tests: Data sources, Zerodha (mock), Alpha Vantage fallback (live/intraday), Google OAuth session, WebSocket."""
import json
import asyncio
import pytest
import requests
import websockets

from tests._live import API, BASE_URL, WS_URL, admin_login  # noqa: F401


@pytest.fixture(scope="session")
def admin_token():
    return admin_login(requests)["token"]


@pytest.fixture(scope="session")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


# ---------- Data Sources ----------
class TestDataSources:
    def test_data_sources_status(self):
        r = requests.get(f"{API}/data-sources", timeout=20)
        assert r.status_code == 200
        d = r.json()
        # Validate structure of all 3 integrations
        assert "alpha_vantage" in d and "zerodha" in d and "ai" in d
        assert "configured" in d["alpha_vantage"] and "mode" in d["alpha_vantage"]
        assert d["alpha_vantage"]["mode"] in ("live", "simulated")
        assert isinstance(d["zerodha"], dict)
        assert "configured" in d["ai"]


# ---------- Zerodha ----------
class TestZerodha:
    def test_zerodha_status(self, auth_headers):
        r = requests.get(f"{API}/zerodha/status", headers=auth_headers, timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert "configured" in d
        # mode/connected fields per implementation
        assert any(k in d for k in ("mode", "connected", "status"))

    def test_zerodha_status_requires_auth(self):
        r = requests.get(f"{API}/zerodha/status", timeout=15)
        assert r.status_code == 401

    def test_zerodha_login_url(self, auth_headers):
        r = requests.get(f"{API}/zerodha/login-url", headers=auth_headers, timeout=20)
        assert r.status_code == 200
        d = r.json()
        # Should have either login_url, url, or message
        assert any(k in d for k in ("login_url", "url", "message", "mode"))

    def test_zerodha_holdings_simulated(self, auth_headers):
        r = requests.get(f"{API}/zerodha/holdings", headers=auth_headers, timeout=20)
        assert r.status_code == 200
        d = r.json()
        # Should return list or dict containing holdings
        assert isinstance(d, (list, dict))

    def test_zerodha_positions_simulated(self, auth_headers):
        r = requests.get(f"{API}/zerodha/positions", headers=auth_headers, timeout=20)
        assert r.status_code == 200
        assert isinstance(r.json(), (list, dict))

    def test_zerodha_place_order_mock(self, auth_headers):
        payload = {
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "quantity": 1,
            "price": 1000.0,
            "order_type": "LIMIT",
        }
        r = requests.post(f"{API}/zerodha/order", json=payload, headers=auth_headers, timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        # Mock should at least return some order info
        assert isinstance(d, dict)
        # Common keys for a simulated order
        assert any(k in d for k in ("order_id", "status", "success", "mode", "message"))


# ---------- Alpha Vantage / Live Stock ----------
class TestStockLiveAndIntraday:
    def test_stock_live_declares_a_freshness_tier_not_a_provider(self):
        """DD-1: the public contract carries `source_tier`, never a provider
        name. The old assertion allowed `"simulated"`, which PH3.9 removed —
        so it had been asserting a value the product can no longer produce."""
        r = requests.get(f"{API}/stocks/RELIANCE/live", timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("source_tier") in ("delayed", "streaming"), \
            f"expected a freshness tier, got {d.get('source_tier')}"
        assert "source" not in d, "provider identity leaked into the public contract"
        assert "price" in d or "symbol" in d

    def test_stock_intraday_declares_a_freshness_tier_not_a_provider(self):
        r = requests.get(f"{API}/stocks/RELIANCE/intraday", timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("source_tier") in ("delayed", "streaming")
        assert "source" not in d, "provider identity leaked into the public contract"
        assert "data" in d


# ---------- Google OAuth Session Exchange ----------
class TestGoogleOAuth:
    def test_google_session_missing_session_id(self):
        r = requests.post(f"{API}/auth/google/session", json={}, timeout=20)
        assert r.status_code == 400

    def test_google_session_invalid_session_id(self):
        # Random invalid session_id should be rejected using JWT authentication -> 401 (or 502 if upstream errors)
        r = requests.post(f"{API}/auth/google/session", json={"session_id": "invalid-xyz-nonexistent"}, timeout=30)
        assert r.status_code in (401, 502, 400), f"unexpected: {r.status_code} {r.text}"


# ---------- WebSocket ----------
class TestWebSocket:
    def test_websocket_connect_and_ping(self):
        async def run():
            try:
                async with websockets.connect(WS_URL, open_timeout=15, close_timeout=5) as ws:
                    await ws.send(json.dumps({"type": "ping"}))
                    resp = await asyncio.wait_for(ws.recv(), timeout=10)
                    data = json.loads(resp)
                    assert data.get("type") in ("pong", "market_update"), f"unexpected: {data}"
                    return True
            except Exception as e:
                pytest.fail(f"WebSocket failed: {e}")
        asyncio.run(run())

    def test_websocket_subscribe_prices(self):
        async def run():
            async with websockets.connect(WS_URL, open_timeout=15, close_timeout=5) as ws:
                await ws.send(json.dumps({"type": "subscribe_prices", "symbols": ["RELIANCE"]}))
                # We should receive at least one message back (price_tick or market_update)
                resp = await asyncio.wait_for(ws.recv(), timeout=15)
                data = json.loads(resp)
                assert data.get("type") in ("price_tick", "market_update"), f"unexpected: {data}"
        asyncio.run(run())

"""AlphaPartner backend API tests - auth, market, trades, portfolio, chat, SIP, settings, notifications."""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8000").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@alphapartner.com"
ADMIN_PASSWORD = "admin123"


@pytest.fixture(scope="session")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    if r.status_code == 429:
        pytest.skip("Rate limited; rerun in 15 min")
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    data = r.json()
    assert "token" in data and data["email"] == ADMIN_EMAIL
    return data["token"]


@pytest.fixture(scope="session")
def admin_client(admin_token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"})
    return s


# ---------- Health ----------
def test_api_root():
    r = requests.get(f"{API}", timeout=15)
    assert r.status_code == 200
    assert r.json().get("status") == "running"


# ---------- Auth ----------
class TestAuth:
    def test_register_new_user_and_login(self):
        email = f"test_{uuid.uuid4().hex[:8]}@example.com"
        r = requests.post(f"{API}/auth/register", json={"name": "Test User", "email": email, "password": "TestPass123!"}, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["email"] == email
        assert "token" in data and len(data["token"]) > 20
        assert data["role"] == "user"

        # Duplicate email
        r2 = requests.post(f"{API}/auth/register", json={"name": "Dup", "email": email, "password": "x"}, timeout=20)
        assert r2.status_code == 400

        # Login with new user
        r3 = requests.post(f"{API}/auth/login", json={"email": email, "password": "TestPass123!"}, timeout=20)
        assert r3.status_code == 200
        token = r3.json()["token"]

        # /me
        r4 = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=20)
        assert r4.status_code == 200
        assert r4.json()["email"] == email

    def test_login_invalid(self):
        r = requests.post(f"{API}/auth/login", json={"email": "nope@example.com", "password": "wrong"}, timeout=20)
        assert r.status_code in (401, 429)

    def test_me_requires_auth(self):
        r = requests.get(f"{API}/auth/me", timeout=15)
        assert r.status_code == 401


# ---------- Market ----------
class TestMarket:
    @pytest.mark.parametrize("path", ["overview", "gainers", "losers", "sectors", "global", "commodities", "fii-dii", "activity-feed"])
    def test_market_endpoints(self, path):
        r = requests.get(f"{API}/market/{path}", timeout=20)
        assert r.status_code == 200, f"{path}: {r.status_code}"
        body = r.json()
        assert body is not None

    def test_overview_shape(self):
        r = requests.get(f"{API}/market/overview", timeout=20)
        d = r.json()
        for k in ["nifty", "bank_nifty", "india_vix", "market_sentiment"]:
            assert k in d, f"missing {k}"
        assert "value" in d["nifty"]


# ---------- Stocks ----------
class TestStocks:
    def test_universe(self):
        r = requests.get(f"{API}/stocks/universe", timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list) and len(r.json()) > 0

    def test_search(self):
        r = requests.get(f"{API}/stocks/search", params={"q": "rel"}, timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_stock_detail_and_chart(self):
        uni = requests.get(f"{API}/stocks/universe", timeout=15).json()
        sym = uni[0]["symbol"] if uni and isinstance(uni[0], dict) else "RELIANCE"
        r = requests.get(f"{API}/stocks/{sym}", timeout=15)
        assert r.status_code == 200
        assert r.json()["symbol"] == sym
        r2 = requests.get(f"{API}/stocks/{sym}/chart", timeout=15)
        assert r2.status_code == 200

    def test_stock_not_found(self):
        r = requests.get(f"{API}/stocks/NOSUCHSTOCK", timeout=15)
        assert r.status_code == 404


# ---------- Analysis ----------
class TestAnalysis:
    def test_top_picks(self):
        r = requests.get(f"{API}/analysis/top-picks", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "picks" in d and len(d["picks"]) >= 1


# ---------- Trades ----------
class TestTrades:
    def test_trade_lifecycle(self, admin_client):
        # Create
        payload = {
            "symbol": "RELIANCE", "stock_name": "Reliance", "type": "BUY",
            "entry_price": 1000, "quantity": 5, "stop_loss": 990, "target1": 1020, "target2": 1050,
            "notes": "TEST_trade"
        }
        r = admin_client.post(f"{API}/trades", json=payload, timeout=20)
        assert r.status_code == 200, r.text
        trade = r.json()
        assert trade["symbol"] == "RELIANCE"
        assert trade["status"] == "OPEN"
        trade_id = trade["_id"]

        # List
        r2 = admin_client.get(f"{API}/trades", timeout=20)
        assert r2.status_code == 200
        assert any(t["_id"] == trade_id for t in r2.json())

        # Active
        r3 = admin_client.get(f"{API}/trades/active", timeout=20)
        assert r3.status_code == 200
        active = r3.json()
        match = [t for t in active if t["_id"] == trade_id]
        assert match and "current_price" in match[0]

        # PnL summary
        r4 = admin_client.get(f"{API}/trades/pnl", timeout=20)
        assert r4.status_code == 200
        for k in ["total_pnl", "today_pnl", "total_trades", "open_trades", "win_rate"]:
            assert k in r4.json()

        # Update -> close
        r5 = admin_client.put(f"{API}/trades/{trade_id}", json={"exit_price": 1025}, timeout=20)
        assert r5.status_code == 200
        updated = r5.json()
        assert updated["status"] in ("TARGET_HIT", "CLOSED")
        assert updated["pnl"] is not None

    def test_trades_requires_auth(self):
        r = requests.get(f"{API}/trades", timeout=15)
        assert r.status_code == 401


# ---------- Portfolio ----------
class TestPortfolio:
    def test_portfolio_and_summary(self, admin_client):
        # Add an open trade to ensure data
        admin_client.post(f"{API}/trades", json={
            "symbol": "TCS", "stock_name": "TCS", "type": "BUY",
            "entry_price": 3500, "quantity": 2, "stop_loss": 3400, "target1": 3600
        }, timeout=20)
        r = admin_client.get(f"{API}/portfolio", timeout=20)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        r2 = admin_client.get(f"{API}/portfolio/summary", timeout=20)
        assert r2.status_code == 200
        for k in ["total_invested", "current_value", "total_pnl", "holdings_count", "capital"]:
            assert k in r2.json()


# ---------- Notifications ----------
class TestNotifications:
    def test_notifications_list(self, admin_client):
        r = admin_client.get(f"{API}/notifications", timeout=20)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_mark_all_read(self, admin_client):
        r = admin_client.put(f"{API}/notifications/read-all", timeout=20)
        assert r.status_code == 200


# ---------- SIP ----------
class TestSIP:
    def test_calculator(self):
        r = requests.get(f"{API}/sip/calculator", params={"amount": 5000, "years": 10, "rate": 12}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["total_invested"] == 5000 * 12 * 10
        assert d["future_value"] > d["total_invested"]


# ---------- Settings ----------
class TestSettings:
    def test_get_and_update_settings(self, admin_client):
        r = admin_client.get(f"{API}/settings", timeout=20)
        assert r.status_code == 200
        r2 = admin_client.put(f"{API}/settings", json={"risk_level": "aggressive", "capital": 500000}, timeout=20)
        assert r2.status_code == 200
        assert r2.json()["risk_level"] == "aggressive"
        assert r2.json()["capital"] == 500000


# ---------- Chat (AI) ----------
class TestChat:
    def test_chat_message(self, admin_client):
        r = admin_client.post(f"{API}/chat", json={"message": "What is RSI in one short sentence?"}, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "response" in d and isinstance(d["response"], str) and len(d["response"]) > 0

    def test_chat_history(self, admin_client):
        r = admin_client.get(f"{API}/chat/history", timeout=20)
        assert r.status_code == 200

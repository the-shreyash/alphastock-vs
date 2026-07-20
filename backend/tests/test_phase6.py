"""Phase 6 backend tests: Deep Zerodha integration + Stock autocomplete."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8000").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@alphapartner.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    # Credential login to obtain JWT cookies + bearer
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    token = r.json().get("token")
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


# ---- Zerodha account / funds / profile / orders ----
class TestZerodhaAccount:
    def test_account_full_overview(self, session):
        r = session.get(f"{BASE_URL}/api/zerodha/account", timeout=15)
        assert r.status_code == 200
        d = r.json()
        for key in ("profile", "funds", "holdings", "positions", "status"):
            assert key in d, f"missing {key}"
        # Holdings substructure
        assert "holdings" in d["holdings"]
        assert isinstance(d["holdings"]["holdings"], list)
        if d["status"].get("connected"):
            assert len(d["holdings"]["holdings"]) >= 0
        else:
            assert len(d["holdings"]["holdings"]) == 0
            assert d["holdings"].get("error") == "Zerodha not connected"
        # Funds equity
        assert "equity" in d["funds"]
        assert "available_margin" in d["funds"]["equity"]

    def test_funds(self, session):
        r = session.get(f"{BASE_URL}/api/zerodha/funds", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "equity" in d
        assert isinstance(d["equity"].get("available_margin"), (int, float))

    def test_profile(self, session):
        r = session.get(f"{BASE_URL}/api/zerodha/profile", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "user_name" in d or "user_id" in d

    def test_orders(self, session):
        r = session.get(f"{BASE_URL}/api/zerodha/orders", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "orders" in d
        assert isinstance(d["orders"], list)

    def test_holdings(self, session):
        r = session.get(f"{BASE_URL}/api/zerodha/holdings", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "holdings" in d
        if d["holdings"]:
            h = d["holdings"][0]
            for f in ("tradingsymbol", "quantity", "average_price", "last_price"):
                assert f in h


# ---- Quick trade ----
class TestQuickTrade:
    def test_quick_trade_places_order_and_creates_trade(self, session):
        payload = {
            "symbol": "RELIANCE",
            "stock_name": "Reliance Industries",
            "entry_price": 2890.0,
            "quantity": 1,
            "stop_loss": 2850.0,
            "target1": 2950.0,
            "target2": 3000.0,
            "confidence": 85,
        }
        r = session.post(f"{BASE_URL}/api/zerodha/quick-trade", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "trade" in d and "order" in d
        assert d["trade"]["symbol"] == "RELIANCE"
        assert d["trade"]["quantity"] == 1
        assert d["trade"]["status"] == "OPEN"
        if d["order"].get("status") == "FAILED":
            assert d["order"].get("order_id") is None
            assert "not connected" in d["order"].get("message", "").lower()
        else:
            assert d["order"].get("order_id")
        assert "_id" in d["trade"]
        trade_id = d["trade"]["_id"]

        # Verify trade persisted
        r2 = session.get(f"{BASE_URL}/api/trades", timeout=10)
        assert r2.status_code == 200
        ids = [t["_id"] for t in r2.json()]
        assert trade_id in ids

    def test_quick_trade_missing_field(self, session):
        r = session.post(f"{BASE_URL}/api/zerodha/quick-trade", json={"symbol": "TCS"}, timeout=10)
        assert r.status_code in (400, 422, 500)


# ---- Stock search/autocomplete ----
class TestStockSearch:
    def test_search_reliance(self, session):
        r = session.get(f"{BASE_URL}/api/stocks/search", params={"q": "REL"}, timeout=10)
        assert r.status_code == 200
        results = r.json()
        assert isinstance(results, list)
        assert len(results) >= 1
        syms = [s.get("symbol") for s in results]
        assert any("RELIANCE" in s for s in syms)

    def test_search_empty(self, session):
        r = session.get(f"{BASE_URL}/api/stocks/search", params={"q": ""}, timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---- Credential login still healthy ----
class TestAdminLogin:
    def test_admin_login_returns_token(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d.get("token")
        assert d.get("email") == ADMIN_EMAIL


# ---- Existing surfaces still healthy (regression) ----
class TestRegression:
    def test_top_picks(self, session):
        r = session.get(f"{BASE_URL}/api/analysis/top-picks", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "picks" in d
        assert len(d["picks"]) >= 1

    def test_news(self, session):
        r = session.get(f"{BASE_URL}/api/news", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "articles" in d

    def test_journal_stats(self, session):
        r = session.get(f"{BASE_URL}/api/journal/stats", timeout=15)
        assert r.status_code == 200

    def test_zerodha_status(self, session):
        r = session.get(f"{BASE_URL}/api/zerodha/status", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "configured" in d

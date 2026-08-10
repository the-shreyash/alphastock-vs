"""Live-deployment smoke suite — auth, market, trades, portfolio, chat, SIP,
settings, notifications, driven over HTTP against a RUNNING backend.

Renamed from `test_backend.py` in PH3.1. The old name suggested "the backend
tests", which is how it came to be run by default — and, with no server on the
machine, how the default `pytest` invocation came to report 47 failures and 51
errors that meant nothing. The `_live` suffix states the requirement in the one
place nobody can miss it.

WHAT STAYED HERE, AND WHY
-------------------------
These tests answer a question a hermetic test cannot: *is the deployed stack
actually working end to end* — real FastAPI process, real MongoDB, real Yahoo
Finance, real Anthropic. That makes this a deployment/smoke suite, and it earns
its place as one.

The **response-shape contracts** it used to be the only home for were converted
to hermetic `TestClient` tests in `tests/test_api_contract.py` (PH3.1 §7
Option A), so they now run on every push instead of never. What remains here is
the round-trip that genuinely needs the stack: registration writing to a real
database, a trade lifecycle persisting across requests, and the AI chat route
producing a real completion.

HOW TO RUN
----------
    export REACT_APP_BACKEND_URL=http://localhost:8000
    export TEST_ADMIN_EMAIL=... TEST_ADMIN_PASSWORD=...
    pytest -m integration tests/test_backend_live.py

Credentials come from the environment and have no defaults — see `tests/_live.py`.
"""
import uuid
import pytest
import requests

from tests._live import ADMIN_EMAIL, API, BASE_URL, admin_login  # noqa: F401


@pytest.fixture(scope="session")
def admin_token():
    data = admin_login(requests)
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
        # Password must satisfy the PH1.5 policy or the 422 fires before the
        # duplicate-email 400 check.
        r2 = requests.post(f"{API}/auth/register", json={"name": "Dup", "email": email, "password": "Dupl!cate92Xy"}, timeout=20)
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


# Notifications and the SIP calculator used to be asserted here. Both moved to
# tests/test_api_contract.py (hermetic): the SIP calculator is pure arithmetic
# with no deployment dimension at all, and the notification list/read-all
# behaviour is ownership filtering, which the hermetic version checks properly
# (it seeds a second user's notification and asserts it is neither returned nor
# mutated — something this suite could not do without polluting a real
# database). Nothing was dropped; both are now checked on every push.


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

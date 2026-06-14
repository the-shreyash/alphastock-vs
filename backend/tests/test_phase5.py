"""Phase 5 backend tests: auto-login, news, journal, full-report, zerodha status."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8000").rstrip("/")


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def auth_client(client):
    # Use auto-login (no creds) — Phase 5 feature
    r = client.get(f"{BASE_URL}/api/auth/auto-login", timeout=30)
    assert r.status_code == 200, f"auto-login failed: {r.status_code} {r.text}"
    data = r.json()
    assert "token" in data and data["email"]
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {data['token']}",
    })
    return s


# ---------- Auto-login ----------
class TestAutoLogin:
    def test_auto_login_returns_admin(self, client):
        r = client.get(f"{BASE_URL}/api/auth/auto-login", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["email"] == os.environ.get("ADMIN_EMAIL", "admin@alphapartner.com")
        assert d["token"]
        assert d["role"] in ("admin", "user")

    def test_auto_login_sets_cookie(self, client):
        r = client.get(f"{BASE_URL}/api/auth/auto-login", timeout=30)
        assert r.status_code == 200
        # Cookie should be set on response
        cookies = r.headers.get("set-cookie", "")
        assert "access_token" in cookies


# ---------- News RSS ----------
class TestNews:
    def test_news_list(self, client):
        r = client.get(f"{BASE_URL}/api/news", timeout=45)
        assert r.status_code == 200
        d = r.json()
        assert "articles" in d
        assert isinstance(d["articles"], list)
        assert d["count"] == len(d["articles"])
        # Validate real RSS articles came through
        assert d["count"] > 0, "No RSS articles returned"
        first = d["articles"][0]
        for f in ("title", "link", "source"):
            assert f in first
        # Sources should include known feeds
        sources = {a["source"] for a in d["articles"]}
        known = {"MoneyControl", "ET Markets", "LiveMint", "MoneyControl Markets", "ET Stocks"}
        assert sources & known, f"None of known RSS sources present, got: {sources}"

    def test_news_for_stock(self, client):
        r = client.get(f"{BASE_URL}/api/news/stock/RELIANCE?name=Reliance", timeout=45)
        assert r.status_code == 200
        d = r.json()
        assert d["symbol"] == "RELIANCE"
        assert isinstance(d["articles"], list)


# ---------- Trade Journal ----------
class TestJournal:
    def test_journal_requires_auth(self):
        # Fresh session with no cookies/headers
        fresh = requests.Session()
        r = fresh.get(f"{BASE_URL}/api/journal", timeout=15)
        assert r.status_code == 401

    def test_journal_list(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/journal", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d, list)
        # Each entry should have core fields if present
        if d:
            for k in ("id", "symbol", "entry_price", "status"):
                assert k in d[0]

    def test_journal_stats(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/journal/stats", timeout=15)
        assert r.status_code == 200
        d = r.json()
        for k in ("recent", "all_time", "open_positions", "period"):
            assert k in d
        for k in ("total", "wins", "losses", "win_rate", "total_pnl"):
            assert k in d["recent"]
            assert k in d["all_time"]


# ---------- Full AI report ----------
class TestFullReport:
    def test_full_report_scoring(self, client):
        r = client.post(
            f"{BASE_URL}/api/analysis/full-report",
            json={"symbol": "RELIANCE", "name": "Reliance Industries"},
            timeout=120,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["symbol"] == "RELIANCE"
        assert "confidence_score" in d
        assert 0 <= d["confidence_score"] <= 100
        assert "scoring_breakdown" in d and isinstance(d["scoring_breakdown"], list)
        assert len(d["scoring_breakdown"]) >= 5
        for item in d["scoring_breakdown"]:
            assert "factor" in item and "score" in item and "max" in item and "reason" in item
        # AI debate fields
        for f in ("claude_analysis", "gemini_analysis", "final_verdict"):
            assert f in d
        # Related news
        assert "related_news" in d
        assert isinstance(d["related_news"], list)

    def test_full_report_invalid_symbol(self, client):
        r = client.post(f"{BASE_URL}/api/analysis/full-report", json={"symbol": "ZZZNOTREAL"}, timeout=15)
        assert r.status_code == 404


# ---------- Zerodha status ----------
class TestZerodha:
    def test_status_configured(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/zerodha/status", timeout=15)
        assert r.status_code == 200
        d = r.json()
        # Phase 5: keys configured -> expect configured True
        assert d.get("configured") is True, f"Zerodha should be configured: {d}"

    def test_data_sources_includes_zerodha(self, client):
        r = client.get(f"{BASE_URL}/api/data-sources", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "zerodha" in d
        assert d["zerodha"].get("configured") is True


# ---------- Dashboard composite ----------
class TestDashboardComposite:
    def test_market_overview(self, client):
        r = client.get(f"{BASE_URL}/api/market/overview", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "nifty" in d and "bank_nifty" in d

    def test_activity_feed(self, client):
        r = client.get(f"{BASE_URL}/api/market/activity-feed", timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_portfolio_health(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/monitor/health", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "health_score" in d

"""Phase 4 backend tests: portfolio monitor, WhatsApp, Zerodha urls/callback, data-sources."""
import os
# pyrefly: ignore [missing-import]
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    from pathlib import Path
    env_path = Path("/app/frontend/.env") if Path("/app/frontend/.env").exists() else Path(__file__).parent.parent.parent / "frontend" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break

ADMIN_EMAIL = "admin@alphapartner.com"
ADMIN_PASSWORD = "admin123"


@pytest.fixture(scope="session")
def auth_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    s.headers.update({"Authorization": f"Bearer {data['token']}"})
    return s


# ---------- Monitor ----------
class TestMonitor:
    def test_health_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/monitor/health")
        assert r.status_code == 401

    def test_health_returns_score(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/monitor/health")
        assert r.status_code == 200
        data = r.json()
        assert "health_score" in data
        assert "alerts" in data
        assert isinstance(data["alerts"], list)
        assert isinstance(data["health_score"], (int, float))
        assert 0 <= data["health_score"] <= 100
        assert "summary" in data

    def test_run_monitor(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/monitor/run")
        assert r.status_code == 200
        data = r.json()
        assert "alerts" in data
        assert "health_score" in data

    def test_alerts_list(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/monitor/alerts")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------- WhatsApp ----------
class TestWhatsApp:
    def test_status(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/whatsapp/status")
        assert r.status_code == 200
        data = r.json()
        assert "configured" in data
        assert "mode" in data
        assert data["mode"] in ("live", "simulated")

    def test_test_send_simulated(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/whatsapp/test")
        assert r.status_code == 200
        data = r.json()
        assert data.get("success") is True
        assert data.get("source") in ("simulated", "twilio")

    def test_configure_saves_phone(self, auth_session):
        r = auth_session.post(f"{BASE_URL}/api/whatsapp/configure",
                              json={"phone_number": "+919999999999"})
        assert r.status_code == 200
        assert r.json().get("phone") == "+919999999999"


# ---------- Zerodha ----------
class TestZerodha:
    def test_urls_public(self):
        r = requests.get(f"{BASE_URL}/api/zerodha/urls")
        assert r.status_code == 200
        data = r.json()
        assert "redirect_url" in data
        assert "postback_url" in data
        assert "instructions" in data

    def test_callback_cancelled_redirect(self):
        r = requests.get(f"{BASE_URL}/api/zerodha/callback",
                         allow_redirects=False)
        assert r.status_code in (302, 307)
        assert "zerodha=cancelled" in r.headers.get("location", "")

    def test_postback_accepts_json(self):
        r = requests.post(f"{BASE_URL}/api/zerodha/postback",
                          json={"order_id": "TEST_PHASE4_ORDER", "status": "COMPLETE"})
        assert r.status_code == 200
        assert r.json().get("status") in ("ok", "error")


# ---------- Data Sources ----------
class TestDataSources:
    def test_data_sources_includes_whatsapp(self):
        r = requests.get(f"{BASE_URL}/api/data-sources")
        assert r.status_code == 200
        data = r.json()
        assert "whatsapp" in data
        assert "alpha_vantage" in data
        assert "zerodha" in data
        assert "ai" in data
        assert "configured" in data["whatsapp"]
        assert "mode" in data["whatsapp"]


# ---------- Stock detail (chart consumer) ----------
class TestStockEndpoints:
    def test_reliance_detail(self):
        r = requests.get(f"{BASE_URL}/api/stocks/RELIANCE")
        assert r.status_code == 200
        d = r.json()
        assert d.get("symbol") == "RELIANCE"
        assert "price" in d

    def test_reliance_intraday(self):
        r = requests.get(f"{BASE_URL}/api/stocks/RELIANCE/intraday")
        assert r.status_code == 200
        d = r.json()
        assert "data" in d
        assert isinstance(d["data"], list)
        assert len(d["data"]) > 0

    def test_reliance_live(self):
        r = requests.get(f"{BASE_URL}/api/stocks/RELIANCE/live")
        assert r.status_code == 200
        d = r.json()
        assert "price" in d
        assert "source" in d

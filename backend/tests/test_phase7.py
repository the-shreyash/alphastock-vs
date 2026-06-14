"""Phase 7 backend tests: Real market data (Yahoo Finance) + WhatsApp Twilio LIVE."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.get(f"{BASE_URL}/api/auth/auto-login", timeout=20)
    assert r.status_code == 200, f"auto-login failed: {r.status_code} {r.text}"
    token = r.json().get("token")
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


# ---- Real Market Data (Yahoo Finance) ----
class TestRealMarketOverview:
    def test_overview_returns_real_yahoo_data(self, session):
        r = session.get(f"{BASE_URL}/api/market/overview", timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert d.get("source") == "yahoo_finance", f"expected source=yahoo_finance, got {d.get('source')}"
        for idx_key in ("nifty", "bank_nifty", "sensex"):
            assert idx_key in d, f"missing {idx_key}"
            assert "value" in d[idx_key]
            assert "change" in d[idx_key]
            assert "change_pct" in d[idx_key]

    def test_nifty_value_in_realistic_range(self, session):
        r = session.get(f"{BASE_URL}/api/market/overview", timeout=20)
        d = r.json()
        nifty = d["nifty"]["value"]
        # Nifty has been between ~15000 and ~30000 in last 3 years; loose bounds
        assert 15000 < nifty < 35000, f"Nifty value {nifty} out of realistic range"

    def test_banknifty_value_in_realistic_range(self, session):
        r = session.get(f"{BASE_URL}/api/market/overview", timeout=20)
        d = r.json()
        bn = d["bank_nifty"]["value"]
        assert 35000 < bn < 70000, f"BankNifty value {bn} out of realistic range"

    def test_sensex_value_in_realistic_range(self, session):
        r = session.get(f"{BASE_URL}/api/market/overview", timeout=20)
        d = r.json()
        sx = d["sensex"]["value"]
        assert 50000 < sx < 100000, f"Sensex value {sx} out of realistic range"

    def test_market_status_present(self, session):
        r = session.get(f"{BASE_URL}/api/market/overview", timeout=20)
        d = r.json()
        assert d.get("market_status") in ("OPEN", "CLOSED")


# ---- Real Stock Quote ----
class TestRealStockQuote:
    def test_reliance_real_price(self, session):
        r = session.get(f"{BASE_URL}/api/stocks/RELIANCE", timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert d.get("source") == "yahoo_finance", f"expected yahoo_finance source, got {d.get('source')}"
        assert d["symbol"] == "RELIANCE"
        price = d.get("price", 0)
        # Reliance has been between INR 1000-3500 historically; loose
        assert 500 < price < 5000, f"RELIANCE price {price} out of realistic range"
        assert "rsi" in d
        assert "vwap" in d
        assert d.get("currency") == "INR"

    def test_tcs_real_price(self, session):
        r = session.get(f"{BASE_URL}/api/stocks/TCS", timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert d.get("source") == "yahoo_finance"
        assert d["symbol"] == "TCS"
        price = d.get("price", 0)
        assert 1000 < price < 6000, f"TCS price {price} out of realistic range"


# ---- WhatsApp Twilio LIVE ----
class TestWhatsAppLive:
    def test_status_configured_live(self, session):
        r = session.get(f"{BASE_URL}/api/whatsapp/status", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d.get("configured") is True, f"expected configured=true, got {d}"
        assert d.get("mode") == "live", f"expected mode=live, got {d.get('mode')}"
        assert d.get("has_sid") is True
        assert d.get("has_token") is True
        assert d.get("has_from") is True
        assert d.get("has_to") is True

    def test_send_test_message_via_twilio(self, session):
        r = session.post(f"{BASE_URL}/api/whatsapp/test", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d.get("success") is True, f"WhatsApp test failed: {d}"
        assert d.get("source") == "twilio", f"expected source=twilio, got {d.get('source')}"
        assert "sid" in d and d["sid"].startswith("SM"), f"expected Twilio SID starting SM, got {d.get('sid')}"


# ---- Regression on prior features ----
class TestRegression:
    def test_auto_login_still_works(self, session):
        r = session.get(f"{BASE_URL}/api/auth/auto-login", timeout=10)
        assert r.status_code == 200
        assert r.json().get("token")

    def test_top_picks(self, session):
        r = session.get(f"{BASE_URL}/api/analysis/top-picks", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert "picks" in d
        assert len(d["picks"]) >= 1

    def test_news_feed(self, session):
        r = session.get(f"{BASE_URL}/api/news", timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert "articles" in d and isinstance(d["articles"], list)

    def test_trades_endpoint(self, session):
        r = session.get(f"{BASE_URL}/api/trades", timeout=10)
        assert r.status_code == 200

    def test_journal_endpoint(self, session):
        r = session.get(f"{BASE_URL}/api/journal", timeout=10)
        assert r.status_code == 200

    def test_zerodha_status(self, session):
        r = session.get(f"{BASE_URL}/api/zerodha/status", timeout=10)
        assert r.status_code == 200

    def test_zerodha_account(self, session):
        r = session.get(f"{BASE_URL}/api/zerodha/account", timeout=15)
        assert r.status_code == 200

    def test_stock_search(self, session):
        r = session.get(f"{BASE_URL}/api/stocks/search?q=REL", timeout=10)
        assert r.status_code == 200
        d = r.json()
        # accept list or dict-with-results
        results = d if isinstance(d, list) else d.get("results", d.get("stocks", []))
        assert any("RELIANCE" in (s.get("symbol", "") if isinstance(s, dict) else str(s)) for s in results)

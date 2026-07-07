"""Tests for the AI Investment Advisor (server.py: build_advisor_recommendations
and POST /api/advisor/recommend).

Runs fully in-process using the fake_db/auth_headers/no_ai fixtures from
conftest.py. `services.real_market.fetch_all_universe_quotes`,
`fetch_real_stock_quote`, `detect_chart_patterns` and `fetch_real_sectors`
(Yahoo Finance) are patched so no network call is made, and the AI-configured
checks are forced False (no_ai) so the deterministic narrative path is used
instead of a real Claude/Gemini call. This mirrors test_morning_report.py.
"""
from unittest.mock import AsyncMock, patch

# Two real-shaped universe quotes (as fetch_all_universe_quotes returns them)
UNIVERSE = [
    {"symbol": "RELIANCE", "name": "Reliance Industries", "sector": "Oil & Gas",
     "price": 2890.0, "change_pct": 1.8, "volume": 5_000_000, "volume_ratio": 1.6},
    {"symbol": "TCS", "name": "Tata Consultancy Services", "sector": "IT",
     "price": 3680.0, "change_pct": 1.2, "volume": 3_000_000, "volume_ratio": 1.3},
    {"symbol": "HDFCBANK", "name": "HDFC Bank", "sector": "Banking",
     "price": 1740.0, "change_pct": 0.9, "volume": 4_000_000, "volume_ratio": 1.2},
    {"symbol": "INFY", "name": "Infosys", "sector": "IT",
     "price": 1520.0, "change_pct": 0.6, "volume": 2_500_000, "volume_ratio": 1.1},
]

FULL_QUOTES = {
    "RELIANCE": {"symbol": "RELIANCE", "name": "Reliance Industries", "sector": "Oil & Gas",
                 "price": 2890.0, "change_pct": 1.8, "rsi": 58.0, "macd": 2.1,
                 "macd_signal": 1.0, "volume_ratio": 1.6},
    "TCS": {"symbol": "TCS", "name": "Tata Consultancy Services", "sector": "IT",
            "price": 3680.0, "change_pct": 1.2, "rsi": 62.0, "macd": 3.0,
            "macd_signal": 2.0, "volume_ratio": 1.3},
    "HDFCBANK": {"symbol": "HDFCBANK", "name": "HDFC Bank", "sector": "Banking",
                 "price": 1740.0, "change_pct": 0.9, "rsi": 55.0, "macd": 1.2,
                 "macd_signal": 0.5, "volume_ratio": 1.2},
    "INFY": {"symbol": "INFY", "name": "Infosys", "sector": "IT",
             "price": 1520.0, "change_pct": 0.6, "rsi": 51.0, "macd": 0.4,
             "macd_signal": 0.2, "volume_ratio": 1.1},
}

PATTERNS = {"patterns": [{"pattern": "Bullish Engulfing", "signal": "bullish", "confidence": 0.85}],
            "summary": "Bullish", "bias": "Bullish"}

SECTORS = [
    {"sector": "IT", "change_pct": 1.4},
    {"sector": "Oil & Gas", "change_pct": 0.9},
    {"sector": "Banking", "change_pct": 0.3},
]

REQUIRED_FIELDS = [
    "symbol", "name", "sector", "confidence", "risk", "expected_return_pct",
    "holding_period", "entry_zone", "stop_loss", "targets", "technical_reasons",
    "fundamental_reasons", "news_impact", "sector_strength", "ai_summary",
]


def _patches():
    async def _quote(sym):
        return FULL_QUOTES.get(sym.upper())

    async def _patterns(sym):
        return PATTERNS

    return (
        patch("services.real_market.fetch_all_universe_quotes",
              new_callable=AsyncMock, return_value=UNIVERSE),
        patch("services.real_market.fetch_real_stock_quote", side_effect=_quote),
        patch("services.real_market.detect_chart_patterns", side_effect=_patterns),
        patch("services.real_market.fetch_real_sectors",
              new_callable=AsyncMock, return_value=SECTORS),
    )


def test_advisor_requires_auth(client, fake_db):
    # ACT — no auth header
    response = client.post("/api/advisor/recommend", json={"horizon": "swing"})
    # ASSERT
    assert response.status_code == 401, response.text


def test_advisor_returns_recommendations(client, fake_db, auth_headers, no_ai):
    # ARRANGE
    p1, p2, p3, p4 = _patches()
    # ACT
    with p1, p2, p3, p4:
        response = client.post("/api/advisor/recommend",
                               json={"horizon": "swing"}, headers=auth_headers)
    # ASSERT
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["horizon"] == "swing"
    recs = body["recommendations"]
    assert 3 <= len(recs) <= 6
    for r in recs:
        for field in REQUIRED_FIELDS:
            assert field in r, f"missing '{field}' in recommendation: {r}"


def test_advisor_uses_real_prices_and_levels(client, fake_db, auth_headers, no_ai):
    # ARRANGE
    p1, p2, p3, p4 = _patches()
    # ACT
    with p1, p2, p3, p4:
        response = client.post("/api/advisor/recommend",
                               json={"horizon": "swing"}, headers=auth_headers)
    # ASSERT — real symbols and price-derived levels
    recs = {r["symbol"]: r for r in response.json()["recommendations"]}
    assert set(recs).issubset({"RELIANCE", "TCS", "HDFCBANK", "INFY"})
    rel = recs.get("RELIANCE")
    if rel:
        assert rel["price"] == 2890.0
        # entry zone straddles the live price, SL below it, targets above it
        assert rel["entry_zone"]["low"] < rel["price"] < rel["entry_zone"]["high"]
        assert rel["stop_loss"] < rel["price"]
        assert all(t > rel["price"] for t in rel["targets"])


def test_advisor_intraday_tighter_than_long(client, fake_db, auth_headers, no_ai):
    # ARRANGE
    p1, p2, p3, p4 = _patches()
    # ACT — same universe, two horizons
    with p1, p2, p3, p4:
        intr = client.post("/api/advisor/recommend",
                           json={"horizon": "intraday"}, headers=auth_headers).json()
        lng = client.post("/api/advisor/recommend",
                          json={"horizon": "long"}, headers=auth_headers).json()
    # ASSERT — intraday stop is tighter (closer to price) than long term
    def sl_gap(payload):
        r = next((x for x in payload["recommendations"] if x["symbol"] == "RELIANCE"), None)
        return (r["price"] - r["stop_loss"]) / r["price"] if r else None
    gi, gl = sl_gap(intr), sl_gap(lng)
    assert gi is not None and gl is not None
    assert gi < gl, f"intraday SL gap {gi} should be tighter than long {gl}"
    assert intr["recommendations"][0]["holding_period"] != lng["recommendations"][0]["holding_period"]


def test_advisor_sector_filter(client, fake_db, auth_headers, no_ai):
    # ARRANGE
    p1, p2, p3, p4 = _patches()
    # ACT — restrict to IT (TCS + INFY qualify: 2 names, filter relaxes below 3)
    with p1, p2, p3, p4:
        response = client.post("/api/advisor/recommend",
                               json={"horizon": "medium", "sectors": ["IT"]},
                               headers=auth_headers)
    # ASSERT — still returns a structured, non-empty result (filter relaxes when
    # fewer than 3 candidates match, never crashing)
    assert response.status_code == 200, response.text
    assert len(response.json()["recommendations"]) >= 1


def test_advisor_invalid_horizon_defaults_to_swing(client, fake_db, auth_headers, no_ai):
    # ARRANGE
    p1, p2, p3, p4 = _patches()
    # ACT
    with p1, p2, p3, p4:
        response = client.post("/api/advisor/recommend",
                               json={"horizon": "banana"}, headers=auth_headers)
    # ASSERT
    assert response.status_code == 200, response.text
    assert response.json()["horizon"] == "swing"


def test_advisor_no_ai_uses_deterministic_narrative(client, fake_db, auth_headers, no_ai):
    # ARRANGE
    p1, p2, p3, p4 = _patches()
    # ACT
    with p1, p2, p3, p4:
        response = client.post("/api/advisor/recommend",
                               json={"horizon": "short"}, headers=auth_headers)
    # ASSERT — ai_powered False, but every rec still carries a real narrative
    body = response.json()
    assert body["ai_powered"] is False
    for r in body["recommendations"]:
        assert len(r["ai_summary"]) > 0
        assert len(r["news_impact"]) > 0

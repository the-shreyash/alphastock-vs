"""Tests for GET /api/analysis/reports/morning (server.py: morning_report_full).

Uses the fake_db/auth_headers/no_ai fixtures from conftest.py to run fully
in-process: `services.real_market.fetch_real_*` (Yahoo Finance) are patched
so no network call is made, and claude_configured/gemini_configured are
forced False so the deterministic ai_briefing fallback is used instead of a
real Claude/Gemini call.
"""
from unittest.mock import AsyncMock, patch

REAL_OVERVIEW = {
    "nifty": {"value": 24500.0, "change_pct": 0.8},
    "bank_nifty": {"value": 52000.0, "change_pct": 0.5},
    "sensex": {"value": 80500.0, "change_pct": 0.3},
}
TOP_PICKS = {"picks": [{"symbol": "RELIANCE", "name": "Reliance", "confidence": 82}]}
SECTORS = [{"sector": "IT", "change_pct": 1.2}, {"sector": "Banking", "change_pct": 0.4}]


def _patches():
    return (
        patch("services.real_market.fetch_real_market_overview", new_callable=AsyncMock, return_value=REAL_OVERVIEW),
        patch("services.real_market.fetch_real_top_picks", new_callable=AsyncMock, return_value=TOP_PICKS),
        patch("services.real_market.fetch_real_sectors", new_callable=AsyncMock, return_value=SECTORS),
    )


def test_morning_report_returns_report_object(client, fake_db, auth_headers, no_ai):
    # ARRANGE
    p1, p2, p3 = _patches()

    # ACT
    with p1, p2, p3:
        response = client.get("/api/analysis/reports/morning", headers=auth_headers)

    # ASSERT
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), dict)


def test_morning_report_has_required_fields(client, fake_db, auth_headers, no_ai):
    # ARRANGE
    p1, p2, p3 = _patches()

    # ACT
    with p1, p2, p3:
        response = client.get("/api/analysis/reports/morning", headers=auth_headers)

    # ASSERT
    data = response.json()
    required = [
        "date", "market_mood", "nifty", "banknifty", "sensex",
        "ai_briefing", "top_picks", "key_risks", "global_cues",
        "fii_dii", "generated_at",
    ]
    for field in required:
        assert field in data, f"missing '{field}' in morning report: {data}"


def test_morning_report_mood_is_valid_value(client, fake_db, auth_headers, no_ai):
    # ARRANGE
    p1, p2, p3 = _patches()

    # ACT
    with p1, p2, p3:
        response = client.get("/api/analysis/reports/morning", headers=auth_headers)

    # ASSERT
    assert response.json()["market_mood"] in ("Bullish", "Bearish", "Neutral", "Cautious")


def test_morning_report_cached_on_second_call(client, fake_db, auth_headers, no_ai):
    # ARRANGE
    p1, p2, p3 = _patches()

    # ACT — first call generates and caches the report in db.reports
    with p1 as m_overview, p2 as m_picks, p3 as m_sectors:
        first = client.get("/api/analysis/reports/morning", headers=auth_headers)
        assert first.status_code == 200, first.text

        # Second call within the same cached "today" should short-circuit
        # to the cached doc without recomputing real-market data.
        second = client.get("/api/analysis/reports/morning", headers=auth_headers)
        assert second.status_code == 200, second.text

        assert m_overview.call_count == 1, "real-market overview should not be refetched on cache hit"
        assert m_picks.call_count == 1, "top picks should not be regenerated on cache hit"
        assert m_sectors.call_count == 1, "sectors should not be refetched on cache hit"

    # ASSERT — identical payload returned both times
    assert first.json()["generated_at"] == second.json()["generated_at"]
    assert first.json() == second.json()

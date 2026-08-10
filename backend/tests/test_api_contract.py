"""Hermetic API contract tests — converted from the live-server suite (PH3.1).

WHY THIS FILE EXISTS
--------------------
`test_backend_live.py` (formerly `test_backend.py`) asserted the response shape
of the market, stock, analysis, portfolio, notification and SIP endpoints by
issuing `requests` calls against a running deployment. That made it a *good
deployment smoke test* and a *useless CI signal*: with no server on the machine
every one of those tests errored with `ConnectionError`, so the contract they
described was never actually checked on any pull request.

This file is the conversion (PH3.1 §7, Option A). Same contracts, asserted
in-process through `TestClient` with market data stubbed, so the assertions run
on every push. The live file keeps the versions of these checks that genuinely
validate a *deployment* — real Yahoo Finance data, real Mongo, real AI — which
is a different question and cannot be answered hermetically.

WHAT IS AND IS NOT ASSERTED HERE
--------------------------------
These tests pin the **API contract**: status codes, response keys, types, and
the documented behaviour when live market data is unavailable. They do not
assert that the numbers are *correct market data* — that is what the stub
replaces, and pretending otherwise would be the "stub agrees with the bug"
failure mode. Where an endpoint has a documented degraded mode
(`available: False`), both branches are covered, because the degraded branch is
the one production will actually hit during a provider outage and the one no
live test can reach on demand.

Auth endpoints are deliberately absent: `/api/auth/{register,login,me}` already
have far deeper hermetic coverage in the PH1 security suites
(test_auth_hardening, test_jwt_sessions, test_password_policy, test_cookie_security,
test_csrf, test_rate_limit, test_recovery). Re-asserting the happy path here
would be duplicate logic, not extra safety.
"""
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

import server

# --------------------------------------------------------------------------- #
# Deterministic market-data doubles                                             #
# --------------------------------------------------------------------------- #
# Values are obviously synthetic (round numbers, TEST_ prefixes where a string
# is free) so that a number appearing in a failure message is immediately
# identifiable as fixture data rather than something that leaked from a real
# provider.

TEST_OVERVIEW = {
    "nifty": {"value": 22000.0, "change": 110.0, "change_pct": 0.5},
    "bank_nifty": {"value": 47000.0, "change": -85.0, "change_pct": -0.18},
    "sensex": {"value": 72000.0, "change": 320.0, "change_pct": 0.45},
    "india_vix": {"value": 13.5, "change": -0.4, "change_pct": -2.87},
    "market_sentiment": "neutral",
}

TEST_QUOTE = {
    "symbol": "RELIANCE",
    "name": "Reliance Industries",
    "price": 2500.0,
    "change": 25.0,
    "change_pct": 1.0,
}

TEST_PICKS = [
    {"symbol": "TCS", "name": "TCS", "score": 82, "reason": "TEST pick"},
]


@pytest.fixture
def stub_market(monkeypatch):
    """Replace the two market-data entry points `server` calls directly.

    Patched on the `server` module rather than on `services.real_market`
    because `server.py` binds `real_quote`/`real_overview` at import time — a
    patch applied to the service module would not be seen by the route.
    """
    monkeypatch.setattr(server, "real_overview", AsyncMock(return_value=dict(TEST_OVERVIEW)))
    monkeypatch.setattr(server, "real_quote", AsyncMock(return_value=dict(TEST_QUOTE)))


@pytest.fixture
def market_unavailable(monkeypatch):
    """Both market-data entry points report an outage (return falsy)."""
    monkeypatch.setattr(server, "real_overview", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "real_quote", AsyncMock(return_value=None))


# --------------------------------------------------------------------------- #
# Health                                                                        #
# --------------------------------------------------------------------------- #
def test_api_root_reports_running(client):
    resp = client.get("/api")
    assert resp.status_code == 200
    assert resp.json().get("status") == "running"


# --------------------------------------------------------------------------- #
# Market                                                                        #
# --------------------------------------------------------------------------- #
class TestMarketOverview:
    def test_overview_shape(self, client, stub_market):
        resp = client.get("/api/market/overview")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("nifty", "bank_nifty", "india_vix", "market_sentiment"):
            assert key in body, f"missing {key}"
        assert "value" in body["nifty"]
        assert body["available"] is True

    def test_overview_reports_unavailable_rather_than_fabricating(self, client, market_unavailable):
        """The documented degraded contract (ADR-021): no synthetic numbers.

        This is the branch a provider outage actually takes in production and
        the one a live test can never trigger on demand — which is exactly why
        it belongs in the hermetic suite.
        """
        resp = client.get("/api/market/overview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert "nifty" not in body
        assert body["note"]


# --------------------------------------------------------------------------- #
# Stocks                                                                        #
# --------------------------------------------------------------------------- #
class TestStocks:
    def test_universe_is_a_non_empty_list_of_symbols(self, client):
        resp = client.get("/api/stocks/universe")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list) and body
        assert all("symbol" in row for row in body)

    def test_search_falls_back_to_static_metadata_offline(self, client):
        """`search_yahoo_stocks` returning None means "live search unreachable";
        the route must then answer from static metadata, not error."""
        with patch("services.real_market.search_yahoo_stocks",
                   new_callable=AsyncMock, return_value=None):
            resp = client.get("/api/stocks/search", params={"q": "rel"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_stock_detail_returns_quote(self, client, stub_market):
        resp = client.get("/api/stocks/RELIANCE")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "RELIANCE"

    def test_unknown_symbol_is_404(self, client, market_unavailable):
        resp = client.get("/api/stocks/NOSUCHSTOCK")
        assert resp.status_code == 404

    def test_known_symbol_with_no_live_data_is_503_not_404(self, client, market_unavailable):
        """A real symbol whose quote is unavailable is an outage (503), not a
        missing resource (404). Conflating the two would tell the frontend to
        render "stock not found" during a provider incident."""
        resp = client.get("/api/stocks/RELIANCE")
        assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# Analysis                                                                      #
# --------------------------------------------------------------------------- #
class TestTopPicks:
    def test_top_picks_served_from_cache(self, client, fake_db):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fake_db.market_analysis.docs.append({"date": today, "top_picks": TEST_PICKS})
        resp = client.get("/api/analysis/top-picks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["picks"] == TEST_PICKS

    def test_top_picks_reports_unavailable_when_live_fetch_is_empty(self, client, fake_db):
        with patch("services.real_market.fetch_real_top_picks",
                   new_callable=AsyncMock, return_value={"picks": []}):
            resp = client.get("/api/analysis/top-picks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["picks"] == []
        # Nothing may be persisted from an empty live fetch, or the next
        # request would serve the emptiness back as a cached "result".
        assert fake_db.market_analysis.docs == []


# --------------------------------------------------------------------------- #
# Portfolio                                                                     #
# --------------------------------------------------------------------------- #
class TestPortfolio:
    def test_portfolio_requires_auth(self, client, fake_db):
        assert client.get("/api/portfolio").status_code == 401

    def test_summary_shape_and_capital_passthrough(self, client, fake_db, test_user, auth_headers):
        with patch("services.portfolio_engine.build_holdings",
                   new_callable=AsyncMock, return_value=[]):
            resp = client.get("/api/portfolio/summary", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        for key in ("total_invested", "current_value", "total_pnl",
                    "holdings_count", "capital", "realized_pnl", "sources"):
            assert key in body, f"missing {key}"
        assert body["holdings_count"] == 0
        assert body["capital"] == test_user["capital"]

    def test_summary_realized_pnl_sums_closed_trades(self, client, fake_db, test_user, auth_headers):
        uid = str(test_user["_id"])
        fake_db.trades.docs.extend([
            {"_id": ObjectId(), "user_id": uid, "status": "TARGET_HIT", "pnl": 250.0},
            {"_id": ObjectId(), "user_id": uid, "status": "CLOSED", "pnl": -50.0},
            # Still open: must NOT contribute to realized P&L.
            {"_id": ObjectId(), "user_id": uid, "status": "OPEN", "pnl": 999.0},
        ])
        with patch("services.portfolio_engine.build_holdings",
                   new_callable=AsyncMock, return_value=[]):
            resp = client.get("/api/portfolio/summary", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["realized_pnl"] == 200.0


# --------------------------------------------------------------------------- #
# Notifications                                                                 #
# --------------------------------------------------------------------------- #
class TestNotifications:
    def test_list_requires_auth(self, client, fake_db):
        assert client.get("/api/notifications").status_code == 401

    def test_list_returns_only_the_callers_notifications(
            self, client, fake_db, test_user, auth_headers):
        uid = str(test_user["_id"])
        fake_db.notifications.docs.extend([
            {"_id": ObjectId(), "user_id": uid, "title": "TEST mine", "read": False},
            {"_id": ObjectId(), "user_id": "someone-else", "title": "TEST theirs", "read": False},
        ])
        resp = client.get("/api/notifications", headers=auth_headers)
        assert resp.status_code == 200
        titles = [n["title"] for n in resp.json()]
        assert titles == ["TEST mine"]

    def test_unread_count_excludes_read(self, client, fake_db, test_user, auth_headers):
        uid = str(test_user["_id"])
        fake_db.notifications.docs.extend([
            {"_id": ObjectId(), "user_id": uid, "read": False},
            {"_id": ObjectId(), "user_id": uid, "read": True},
        ])
        resp = client.get("/api/notifications/unread-count", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_read_all_marks_only_the_callers_notifications(
            self, client, fake_db, test_user, auth_headers):
        uid = str(test_user["_id"])
        fake_db.notifications.docs.extend([
            {"_id": ObjectId(), "user_id": uid, "read": False},
            {"_id": ObjectId(), "user_id": "someone-else", "read": False},
        ])
        assert client.put("/api/notifications/read-all", headers=auth_headers).status_code == 200
        mine = [n for n in fake_db.notifications.docs if n["user_id"] == uid]
        theirs = [n for n in fake_db.notifications.docs if n["user_id"] != uid]
        assert all(n["read"] for n in mine)
        assert not any(n["read"] for n in theirs)


# --------------------------------------------------------------------------- #
# SIP calculator                                                                #
# --------------------------------------------------------------------------- #
class TestSIPCalculator:
    def test_totals_and_growth(self, client):
        resp = client.get("/api/sip/calculator",
                          params={"amount": 5000, "years": 10, "rate": 12})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_invested"] == 5000 * 12 * 10
        assert body["future_value"] > body["total_invested"]
        assert body["wealth_gained"] == round(body["future_value"] - body["total_invested"], 2)

    def test_zero_rate_is_plain_accumulation(self, client):
        """The `r > 0` branch guard: at 0% the annuity formula divides by zero,
        so the route has a separate path. Unreachable from the UI, reachable
        from the API, and never previously covered."""
        resp = client.get("/api/sip/calculator",
                          params={"amount": 1000, "years": 2, "rate": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert body["future_value"] == 1000 * 24
        assert body["wealth_gained"] == 0

"""Live-server tests converted to hermetic API tests (PH3.3 §17).

WHY THIS FILE EXISTS
--------------------
`test_phase2/4/5/6.py` drive a running deployment over HTTP. That makes them
useful deployment smoke tests and useless CI signal: with no server on the
machine they skip, so the contracts they describe are checked on no pull
request. PH3.1 converted the market/portfolio half of the largest live suite
into `test_api_contract.py`; this file continues that work for the assertions
PH3.3's audit found to need nothing but the application object.

WHAT WAS MIGRATED, AND THE RULE USED
------------------------------------
A live test is migratable when the thing it asserts is a property of the
*application* — a status code, a redirect target, a response shape, an
authorization rule, a validation rule. It is **not** migratable when the thing
it asserts is a property of the *deployment* — that Yahoo Finance really
answered, that a real Zerodha account really holds these positions, that a
WebSocket survives a real network hop, that Twilio really delivered a message.
Converting the second kind would replace a true statement about production with
a tautology about a mock.

Converted here:

* Zerodha configuration URLs, the cancelled-callback redirect, and postback
  acceptance (`test_phase2`, `test_phase4`).
* Google OAuth session-exchange input validation (`test_phase2`).
* The data-sources status contract (`test_phase2`, `test_phase4`, `test_phase5`).
* Trade journal listing, stats and ownership (`test_phase5`, `test_phase6`).
* Portfolio monitor health and alerts (`test_phase4`).
* Quick-trade and search input validation (`test_phase6`).
* Full-report rejection of an unknown symbol (`test_phase5`).

Deliberately left live, with the question only a deployment can answer:

| Live test | What it really asserts |
|---|---|
| `test_stock_live_declares_a_freshness_tier_not_a_provider` | A provider actually responded (`source_tier` is set, and no provider name leaked) |
| `TestZerodhaAccount` (phase6) | A real broker session returns real funds/holdings |
| `TestWebSocket` (phase2) | A socket survives a real network hop and proxy |
| `TestWhatsAppLive` (phase7) | Twilio actually delivered a billable message |
| `test_full_report_scoring` | Scoring over genuine market data, not fixtures |

The `requires auth` cases in every one of those files are not migrated because
they are already covered, far more completely, by the mechanical 401 sweep in
`test_api_authz.py` — which checks all 126 authenticated routes rather than the
handful these files happened to name.
"""
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

import server


# --------------------------------------------------------------------------- #
# Zerodha configuration surface (from test_phase2 / test_phase4)                #
# --------------------------------------------------------------------------- #
class TestZerodhaConfigurationEndpoints:
    def test_urls_are_public_and_shaped(self, client, fake_db):
        """Operators read this page while configuring the Kite app, before any
        account exists — so it must answer without credentials."""
        resp = client.get("/api/zerodha/urls")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("redirect_url", "postback_url", "instructions"):
            assert key in body

    def test_urls_degrade_when_unconfigured_rather_than_inventing_one(
            self, client, fake_db, monkeypatch):
        """`KITE_REDIRECT_URL` is blank in the test environment, as it is on a
        fresh deployment. An empty string is the honest answer; a fabricated
        default would be silently wrong in the Kite dashboard."""
        monkeypatch.delenv("KITE_REDIRECT_URL", raising=False)
        body = client.get("/api/zerodha/urls").json()
        assert body["redirect_url"] == ""
        assert body["postback_url"] == ""

    def test_cancelled_callback_redirects_rather_than_erroring(self, client, fake_db):
        """The user pressed "cancel" in Kite. That is a normal outcome, and they
        must land back in Settings — an error page here looks like a broken
        integration for what was a deliberate choice."""
        resp = client.get("/api/zerodha/callback", params={"status": "cancelled"},
                          follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "zerodha=cancelled" in resp.headers["location"]

    def test_callback_without_a_request_token_does_not_error(self, client, fake_db):
        resp = client.get("/api/zerodha/callback", follow_redirects=False)
        assert resp.status_code in (302, 307)

    def test_postback_accepts_a_broker_webhook(self, client, fake_db):
        """Zerodha retries a postback it cannot deliver, so a non-2xx here means
        duplicate order events later."""
        resp = client.post("/api/zerodha/postback",
                           json={"order_id": "TEST-1", "status": "COMPLETE"})
        assert resp.status_code == 200
        assert fake_db.zerodha_postbacks.docs, "the postback was not recorded"

    def test_postback_survives_a_malformed_body(self, client, fake_db):
        """A broker's webhook payload is not under our control and must never be
        able to 500 the endpoint — the broker would simply retry it forever."""
        resp = client.post("/api/zerodha/postback", content=b"{not json",
                           headers={"Content-Type": "application/json"})
        assert resp.status_code < 500


# --------------------------------------------------------------------------- #
# Google OAuth input validation (from test_phase2)                              #
# --------------------------------------------------------------------------- #
class TestGoogleSessionValidation:
    @pytest.mark.parametrize("payload,missing", [
        ({}, "code"),
        ({"state": "TEST-state"}, "code"),
        ({"code": "TEST-code"}, "state"),
    ])
    def test_missing_parameters_are_rejected_before_any_token_exchange(
            self, client, fake_db, payload, missing):
        """Rejected locally, without calling Google. Forwarding an incomplete
        request would turn a client bug into an upstream error and a slower one."""
        resp = client.post("/api/auth/google/session", json=payload)
        assert resp.status_code == 400
        assert missing in resp.json()["detail"]

    def test_an_invalid_state_is_rejected(self, client, fake_db):
        """The `state` parameter is the OAuth CSRF defence; accepting an
        unrecognised one would accept a login the user never initiated."""
        resp = client.post("/api/auth/google/session", json={
            "code": "TEST-code", "state": "TEST-not-a-real-state",
            "redirect_uri": "http://localhost:3000/auth/callback"})
        assert 400 <= resp.status_code < 500
        assert fake_db.users.docs == [], "a session was created from an invalid state"


# --------------------------------------------------------------------------- #
# Data sources (from test_phase2 / 4 / 5)                                       #
# --------------------------------------------------------------------------- #
class TestDataSources:
    def test_status_lists_every_integration(self, client, fake_db):
        resp = client.get("/api/data-sources")
        assert resp.status_code == 200
        body = resp.json()
        for source in ("alpha_vantage", "zerodha", "ai", "whatsapp"):
            assert source in body, f"{source} missing from the data-source status"

    @pytest.mark.parametrize("smtp_port", ["", "  ", "587"])
    def test_a_blank_smtp_port_does_not_break_the_status_page(
            self, client, fake_db, monkeypatch, smtp_port):
        """PH3.3 defect D-11. `SMTP_PORT` was read with
        `os.environ.get("SMTP_PORT", "587")`, whose default applies only when
        the key is *absent*. A declared-but-empty value — what every deployment
        scaffolded from `.env.example` has before SMTP is configured — reached
        `int("")` and raised ValueError, 500ing this endpoint and breaking all
        outbound email including password reset.
        """
        monkeypatch.setenv("SMTP_PORT", smtp_port)
        resp = client.get("/api/data-sources")
        assert resp.status_code == 200, resp.text[:200]

    def test_email_status_defaults_the_port_when_blank(self, monkeypatch):
        from services.email_service import get_status
        monkeypatch.setenv("SMTP_PORT", "")
        assert get_status()["configured"] is False

    def test_every_integration_reports_unconfigured_in_a_hermetic_run(
            self, client, fake_db):
        """The counterpart to the network guard: `_testenv.py` blanks every
        third-party credential, so anything reporting `configured: true` here
        means a real key reached the suite."""
        body = client.get("/api/data-sources").json()
        assert body["ai"]["configured"] is False, \
            "an AI provider is configured during a hermetic run — a real key leaked in"
        assert body["alpha_vantage"]["configured"] is False
        assert body["alpha_vantage"]["mode"] == "yahoo_finance"


# --------------------------------------------------------------------------- #
# Trade journal (from test_phase5 / test_phase6)                                #
# --------------------------------------------------------------------------- #
class TestJournal:
    @pytest.fixture
    def closed_trade(self, fake_db, test_user):
        doc = {
            "_id": ObjectId(), "user_id": str(test_user["_id"]),
            "symbol": "TESTCO", "type": "BUY", "entry_price": 100.0,
            "exit_price": 110.0, "quantity": 10, "quantity_open": 0,
            "pnl": 100.0, "realized_pnl": 100.0, "pnl_percent": 10.0,
            "status": "TARGET_HIT", "setup_type": "MOMENTUM",
            "entry_time": "2026-08-01T09:15:00+00:00",
            "exit_time": "2026-08-01T14:30:00+00:00",
            "notes": "TEST journal entry",
        }
        fake_db.trades.docs.append(doc)
        return doc

    def test_journal_lists_the_callers_closed_trades(
            self, authenticated_client, fake_db, test_user, closed_trade):
        resp = authenticated_client.get("/api/journal")
        assert resp.status_code == 200

    def test_journal_excludes_other_users_trades(
            self, authenticated_client, fake_db, test_user, other_user, closed_trade):
        fake_db.trades.docs.append({**closed_trade, "_id": ObjectId(),
                                    "user_id": str(other_user["_id"]),
                                    "symbol": "THEIRS"})
        resp = authenticated_client.get("/api/journal")
        assert resp.status_code == 200
        assert "THEIRS" not in str(resp.json())

    def test_stats_render_with_no_trades(self, authenticated_client, fake_db, test_user):
        """A new user opens the journal before trading; an empty history must
        not divide by zero computing a win rate."""
        resp = authenticated_client.get("/api/journal/stats")
        assert resp.status_code == 200

    def test_stats_reflect_a_winning_trade(
            self, authenticated_client, fake_db, test_user, closed_trade):
        resp = authenticated_client.get("/api/journal/stats")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_setup_stats_render_empty(self, authenticated_client, fake_db, test_user):
        assert authenticated_client.get("/api/journal/setup-stats").status_code == 200


# --------------------------------------------------------------------------- #
# Portfolio monitor (from test_phase4)                                          #
# --------------------------------------------------------------------------- #
class TestPortfolioMonitor:
    def test_health_returns_a_score_shape(self, authenticated_client, fake_db, test_user):
        with patch("services.portfolio_monitor.analyze_portfolio_health",
                   new_callable=AsyncMock,
                   return_value={"health_score": 72, "at_risk": 0,
                                 "total_unrealized_pnl": 0.0, "issues": []}):
            resp = authenticated_client.get("/api/monitor/health")
        assert resp.status_code == 200
        assert resp.json()["health_score"] == 72

    def test_health_renders_for_an_empty_portfolio(
            self, authenticated_client, fake_db, test_user, monkeypatch):
        monkeypatch.setattr(server, "real_quotes_map", AsyncMock(return_value={}))
        resp = authenticated_client.get("/api/monitor/health")
        assert resp.status_code == 200

    def test_alerts_are_scoped_to_the_caller(
            self, authenticated_client, fake_db, test_user, other_user):
        fake_db.notifications.docs.extend([
            {"_id": ObjectId(), "user_id": str(test_user["_id"]),
             "title": "TEST mine", "type": "RISK_ALERT", "read": False,
             "created_at": "2026-08-01T00:00:00+00:00"},
            {"_id": ObjectId(), "user_id": str(other_user["_id"]),
             "title": "TEST theirs", "type": "RISK_ALERT", "read": False,
             "created_at": "2026-08-01T00:00:00+00:00"},
        ])
        resp = authenticated_client.get("/api/monitor/alerts")
        assert resp.status_code == 200
        assert "theirs" not in str(resp.json())


# --------------------------------------------------------------------------- #
# Input validation lifted from the live suites                                  #
# --------------------------------------------------------------------------- #
class TestValidationFromLiveSuites:
    def test_quick_trade_rejects_a_missing_field(
            self, authenticated_client, fake_db, test_user):
        """`test_phase6::test_quick_trade_missing_field`, which needed a running
        server and a real broker session to assert a 422."""
        resp = authenticated_client.post("/api/zerodha/quick-trade", json={})
        assert 400 <= resp.status_code < 500

    def test_stock_search_with_an_empty_query(self, client, fake_db):
        """`test_phase6::test_search_empty`."""
        with patch("services.real_market.search_yahoo_stocks",
                   new_callable=AsyncMock, return_value=None):
            resp = client.get("/api/stocks/search", params={"q": ""})
        assert resp.status_code < 500

    def test_full_report_rejects_an_unknown_symbol(self, client, fake_db, monkeypatch):
        """`test_phase5::test_full_report_invalid_symbol`."""
        monkeypatch.setattr(server, "real_quote", AsyncMock(return_value=None))
        resp = client.post("/api/analysis/full-report",
                           json={"symbol": "NOSUCHSTOCK"})
        assert 400 <= resp.status_code < 500


# --------------------------------------------------------------------------- #
# The classification itself                                                     #
# --------------------------------------------------------------------------- #
def test_live_suites_are_still_classified_and_deselected():
    """The live suites must keep skipping cleanly rather than failing in CI.

    PH3.3 migrated assertions *out* of those files but deliberately did not
    delete them. This guards the classification that keeps the remainder out of
    the default run — if a filename is renamed without updating
    `conftest._LIVE_SERVER_SUITES`, that suite silently rejoins CI, finds no
    server, and fails for a reason that looks nothing like the cause.
    """
    from pathlib import Path

    from tests.conftest import _LIVE_SERVER_SUITES

    tests_dir = Path(__file__).parent
    for filename in _LIVE_SERVER_SUITES:
        assert (tests_dir / filename).exists(), (
            f"{filename} is classified as a live suite but no longer exists; "
            f"update conftest._LIVE_SERVER_SUITES."
        )

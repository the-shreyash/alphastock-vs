"""Tests for the n8n automation webhook routes in server.py
(POST /api/webhooks/morning-scan, /evening-summary, /weekly-review,
/news-digest), gated by a shared-secret `X-Webhook-Key` header checked
against the WEBHOOK_API_KEY env var (server.py's verify_webhook_key —
fails closed if the env var is unset).

Each route's heavy lifting (services.scheduler.morning_analysis_job /
eod_report_job, services.trade_journal.generate_weekly_review,
services.news_service.fetch_news) is patched out so no AI provider,
yfinance, or RSS network call is made. WEBHOOK_API_KEY is set/unset per
test via monkeypatch.setenv/delenv so tests never leak env state to each
other.
"""
from unittest.mock import AsyncMock, patch

WEBHOOK_KEY = "test-webhook-secret-123"


def test_morning_scan_webhook_returns_ok(client, fake_db, monkeypatch):
    # ARRANGE
    monkeypatch.setenv("WEBHOOK_API_KEY", WEBHOOK_KEY)

    # ACT
    with patch("services.scheduler.morning_analysis_job", new_callable=AsyncMock) as mock_job:
        response = client.post(
            "/api/webhooks/morning-scan", headers={"X-Webhook-Key": WEBHOOK_KEY}
        )

    # ASSERT
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"
    mock_job.assert_called_once()


def test_evening_summary_webhook_returns_ok(client, fake_db, monkeypatch):
    # ARRANGE
    monkeypatch.setenv("WEBHOOK_API_KEY", WEBHOOK_KEY)

    # ACT
    with patch("services.scheduler.eod_report_job", new_callable=AsyncMock) as mock_job:
        response = client.post(
            "/api/webhooks/evening-summary", headers={"X-Webhook-Key": WEBHOOK_KEY}
        )

    # ASSERT
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"
    mock_job.assert_called_once()


def test_weekly_review_webhook_returns_ok(client, fake_db, test_user, monkeypatch):
    # ARRANGE
    monkeypatch.setenv("WEBHOOK_API_KEY", WEBHOOK_KEY)

    # ACT
    with patch(
        "services.trade_journal.generate_weekly_review",
        new_callable=AsyncMock,
        return_value={"review": "Solid week overall."},
    ) as mock_review:
        response = client.post(
            "/api/webhooks/weekly-review", headers={"X-Webhook-Key": WEBHOOK_KEY}
        )

    # ASSERT
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "success"
    assert data["users_reviewed"] >= 1
    mock_review.assert_called()


def test_news_digest_webhook_returns_ok(client, fake_db, monkeypatch):
    # ARRANGE
    monkeypatch.setenv("WEBHOOK_API_KEY", WEBHOOK_KEY)

    # ACT
    with patch(
        "services.news_service.fetch_news",
        new_callable=AsyncMock,
        return_value=[{"title": "Market rallies"}, {"title": "RBI holds rates"}],
    ) as mock_fetch:
        response = client.post(
            "/api/webhooks/news-digest", headers={"X-Webhook-Key": WEBHOOK_KEY}
        )

    # ASSERT
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "success"
    assert data["articles"] == 2
    mock_fetch.assert_called_once()


def test_webhook_without_api_key_returns_403(client, monkeypatch):
    # ARRANGE — WEBHOOK_API_KEY unset: verify_webhook_key fails closed
    monkeypatch.delenv("WEBHOOK_API_KEY", raising=False)

    endpoints = [
        "/api/webhooks/morning-scan",
        "/api/webhooks/evening-summary",
        "/api/webhooks/weekly-review",
        "/api/webhooks/news-digest",
    ]
    for path in endpoints:
        # ACT — no header at all
        r_no_header = client.post(path)
        # ASSERT
        assert r_no_header.status_code == 403, f"{path} (no header): {r_no_header.text}"

    # ACT — wrong header, key still unset server-side
    r_wrong_key = client.post("/api/webhooks/morning-scan", headers={"X-Webhook-Key": "wrong-key"})
    # ASSERT
    assert r_wrong_key.status_code == 403

    # ARRANGE — key configured server-side, but caller sends the wrong value
    monkeypatch.setenv("WEBHOOK_API_KEY", WEBHOOK_KEY)
    # ACT
    r_mismatched = client.post("/api/webhooks/morning-scan", headers={"X-Webhook-Key": "not-the-real-key"})
    # ASSERT
    assert r_mismatched.status_code == 403

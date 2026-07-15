"""Sprint R7 — AI Live Activity tests (hermetic, no network/Redis/Mongo).

Locks the ai.run.* event contract for the instrumented pipelines:

  • Morning report (server.generate_morning_report): ordered, truthful step
    events — started → running/done pairs in label order → completed.
  • run_id passthrough: the client-supplied correlation id is echoed on every
    event of the run (HTTP query param → AIRun).
  • Cache hit: a cached report returns before the run starts — zero ai.* events
    (REALTIME_SYSTEM.md: never fake AI progress).
  • Failure honesty: a failing fetch marks its step `warning` and the run
    completes `warning`.
  • Scheduler job (morning_analysis_job): broadcast run — user_id is None on
    every event so the bridge fans out on the `ai` channel.

Events are captured by subscribing "ai.*" on the process-global event bus;
handlers run inline during publish, so ordering assertions are exact.
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.market_engine.event_bus import event_bus

REAL_OVERVIEW = {
    "nifty": {"value": 24500.0, "change_pct": 0.8},
    "bank_nifty": {"value": 52000.0, "change_pct": 0.5},
    "sensex": {"value": 80500.0, "change_pct": 0.3},
}
TOP_PICKS = {"picks": [{"symbol": "RELIANCE", "name": "Reliance", "confidence": 82}]}
SECTORS = [{"sector": "IT", "change_pct": 1.2}]
NEWS_SENTIMENT = {"available": True, "score": 62, "label": "Bullish", "articles_analyzed": 40}


def _run(coro):
    return asyncio.run(coro)


def _patches(**overrides):
    """Patches for every external fetch the morning-report pipeline performs."""
    targets = {
        "services.real_market.fetch_real_market_overview": REAL_OVERVIEW,
        "services.real_market.fetch_real_top_picks": TOP_PICKS,
        "services.real_market.fetch_real_sectors": SECTORS,
        "services.real_market.fetch_real_fii_dii": {"fii": {"net": -1200}, "dii": {"net": 900}},
        "services.news_service.get_market_sentiment": NEWS_SENTIMENT,
    }
    targets.update(overrides)
    return [
        patch(target, new_callable=AsyncMock, **(
            {"side_effect": value} if isinstance(value, Exception) else {"return_value": value}))
        for target, value in targets.items()
    ]


class _Spy:
    """Captures every ai.* event published on the process-global bus."""

    def __init__(self):
        self.events = []

    async def __call__(self, evt):
        self.events.append(evt)

    def __enter__(self):
        event_bus.subscribe("ai.*", self)
        return self

    def __exit__(self, *exc):
        event_bus.unsubscribe("ai.*", self)
        return False


def _steps_of(events, status=None):
    steps = [e for e in events if e["type"] == "ai.step"]
    if status:
        steps = [s for s in steps if s["data"]["status"] == status]
    return steps


# ---------------------------------------------------------------------------
# Morning report — ordered, truthful step events
# ---------------------------------------------------------------------------

def test_morning_report_emits_ordered_run_events(fake_db, no_ai):
    import server

    async def run():
        with _Spy() as spy:
            patches = _patches()
            for p in patches:
                p.start()
            try:
                report = await server.generate_morning_report(user_id="u1", run_id="r1")
            finally:
                for p in patches:
                    p.stop()
        return report, spy.events

    report, events = _run(run())

    assert report["available"] is True
    assert events[0]["type"] == "ai.run.started"
    started = events[0]["data"]
    assert started["run_id"] == "r1"
    assert started["user_id"] == "u1"
    assert started["steps"] == server.MORNING_REPORT_STEPS

    # Every step runs then finishes, in plan order, with matching indices.
    running = _steps_of(events, "running")
    done = _steps_of(events, "done")
    assert [s["data"]["label"] for s in running] == server.MORNING_REPORT_STEPS
    assert [s["data"]["label"] for s in done] == server.MORNING_REPORT_STEPS
    assert [s["data"]["index"] for s in running] == list(range(len(server.MORNING_REPORT_STEPS)))

    assert events[-1]["type"] == "ai.run.completed"
    assert events[-1]["data"]["status"] == "done"
    # Correlation ids ride on every event of the run.
    assert all(e["data"]["run_id"] == "r1" and e["data"]["user_id"] == "u1" for e in events)
    # The real news work landed in the report.
    assert report["news_sentiment"] == NEWS_SENTIMENT


def test_run_id_passthrough_via_http(client, fake_db, auth_headers, no_ai):
    patches = _patches()
    with _Spy() as spy:
        for p in patches:
            p.start()
        try:
            response = client.get(
                "/api/analysis/reports/morning?run_id=abc-123", headers=auth_headers)
        finally:
            for p in patches:
                p.stop()

    assert response.status_code == 200, response.text
    started = [e for e in spy.events if e["type"] == "ai.run.started"]
    assert len(started) == 1
    assert started[0]["data"]["run_id"] == "abc-123"
    completed = [e for e in spy.events if e["type"] == "ai.run.completed"]
    assert completed and completed[0]["data"]["run_id"] == "abc-123"


def test_cached_report_emits_no_run_events(client, fake_db, auth_headers, no_ai):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fake_db.reports.docs.append({
        "date": today, "type": "morning", "available": True,
        "ai_briefing": "cached", "generated_at": "t",
    })

    with _Spy() as spy:
        response = client.get(
            "/api/analysis/reports/morning?run_id=zzz", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["ai_briefing"] == "cached"
    assert spy.events == [], "a cache hit must not fake an AI run"


def test_failed_fetch_marks_step_and_run_warning(fake_db, no_ai):
    import server

    async def run():
        with _Spy() as spy:
            patches = _patches(**{
                "services.real_market.fetch_real_top_picks": RuntimeError("NSE feed down"),
            })
            for p in patches:
                p.start()
            try:
                with pytest.raises(RuntimeError):
                    await server.generate_morning_report(user_id="u1", run_id="r2")
            finally:
                for p in patches:
                    p.stop()
        return spy.events

    events = _run(run())

    warnings = _steps_of(events, "warning")
    assert len(warnings) == 1
    assert warnings[0]["data"]["label"] == "Scanning NSE"
    assert events[-1]["type"] == "ai.run.completed"
    assert events[-1]["data"]["status"] == "warning"


def test_unavailable_overview_completes_run_with_warning(fake_db, no_ai):
    import server

    async def run():
        with _Spy() as spy:
            patches = _patches(**{
                "services.real_market.fetch_real_market_overview": None,
            })
            for p in patches:
                p.start()
            try:
                report = await server.generate_morning_report(user_id="u1")
            finally:
                for p in patches:
                    p.stop()
        return report, spy.events

    report, events = _run(run())

    assert report["available"] is False
    assert events[-1]["type"] == "ai.run.completed"
    assert events[-1]["data"]["status"] == "warning"


# ---------------------------------------------------------------------------
# Scheduler job — broadcast run (user_id None → `ai` channel fan-out)
# ---------------------------------------------------------------------------

def test_scheduler_morning_job_broadcasts_run():
    from tests._fakedb import FakeDB
    from services.scheduler import morning_analysis_job

    async def run():
        db = FakeDB()
        db.trades.distinct = AsyncMock(return_value=[])
        with _Spy() as spy:
            patches = _patches()
            for p in patches:
                p.start()
            try:
                await morning_analysis_job(db, AsyncMock(return_value="report text"))
            finally:
                for p in patches:
                    p.stop()
        return db, spy.events

    db, events = _run(run())

    assert events[0]["type"] == "ai.run.started"
    assert events[-1]["type"] == "ai.run.completed"
    assert events[-1]["data"]["status"] == "done"
    # Broadcast semantics: no user_id anywhere → bridge fans out on `ai` channel.
    assert all(e["data"]["user_id"] is None for e in events)
    # The job's real work still happened (picks snapshot persisted).
    assert len(db.market_analysis.docs) == 1

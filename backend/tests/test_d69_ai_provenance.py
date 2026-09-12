"""D6.9 — AI artifact provenance and freshness (hermetic: no network, Redis, Mongo).

The defect this suite exists to make impossible to reintroduce, measured on a
deployment whose ANTHROPIC_API_KEY had no billing credit:

    workspace header       "AI ready"
    Morning Report page    "AI Market Briefing"
    the briefing text      "AI services are currently offline or unavailable.
                            Please check that ANTHROPIC_API_KEY and
                            GOOGLE_GEMINI_KEY are configured in your backend
                            .env file …"

— persisted into `db.reports` and served to every user for the rest of the day.
Three separate failures with one root cause: nothing recorded whether a model
had actually answered, so every surface inferred it, and every surface inferred
it wrong.

What is locked here:

  * Generation status is recorded, and success is never inferred from a
    scheduler having run or a key being present.
  * Provider and model are written ONLY when a model answered.
  * Freshness derives from `completed_at` and from nothing else.
  * A stale report stays available; a failed one never becomes an artifact.
  * Current AI health never overwrites historical generation status.
  * Legacy documents get "unknown", never a fabricated timestamp.
  * Nothing secret and no raw provider error leaves the API.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services import ai_provenance
from services.ai_provenance import AIOutcome, Freshness, GenerationStatus
from services.ai_provider import AIResponse

# Reuse Sprint 10's fixtures verbatim — the report's inputs are not this
# sprint's subject, and a second set of doubles would drift from the first.
from tests.test_sprint10_morning_report import (  # noqa: F401
    _generate, _isolate_gift_nifty, _patches, _run,
)

ARTIFACT = ai_provenance.ARTIFACT_MORNING_REPORT


def _record(**over):
    """A completed provenance record, unless a test says otherwise."""
    prov = ai_provenance.begin(ARTIFACT, "morning:2026-09-11", analysis_version="1.0.0")
    ai_provenance.completed(prov)
    prov.update(over)
    return prov


def _aged(seconds):
    """A record whose generation succeeded `seconds` ago."""
    prov = _record()
    prov["completed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    return prov


# --------------------------------------------------------------------------- #
# 1-4. The provenance model                                                    #
# --------------------------------------------------------------------------- #

def test_a_new_record_is_generating_and_has_no_completion_instant():
    prov = ai_provenance.begin(ARTIFACT, "morning:2026-09-11", analysis_version="1.0.0")

    assert prov["status"] == GenerationStatus.GENERATING.value
    assert prov["generated_at"], "the attempt's start must be recorded"
    assert prov["completed_at"] is None, "nothing has succeeded yet"


def test_only_a_real_model_call_writes_a_provider_and_model():
    prov = ai_provenance.begin(ARTIFACT, "a", analysis_version="1.0.0")
    ai_provenance.ai_succeeded(prov, provider="claude", model="claude-3-haiku-20240307",
                               prompt_key="morning_report", prompt_version="1.0.0")

    assert prov["ai"]["provider"] == "claude"
    assert prov["ai"]["model"] == "claude-3-haiku-20240307"
    assert prov["ai"]["prompt_version"] == "1.0.0"


def test_a_failed_model_call_names_no_provider_and_no_model():
    """The provider that was *attempted* is not the provider that answered.

    Recording it would read, to every consumer, as an attribution.
    """
    prov = ai_provenance.begin(ARTIFACT, "a", analysis_version="1.0.0")
    ai_provenance.ai_did_not_run(prov, outcome=AIOutcome.PROVIDER_ERROR,
                                 prompt_key="morning_report", prompt_version="1.0.0")

    assert prov["ai"]["outcome"] == AIOutcome.PROVIDER_ERROR.value
    assert prov["ai"]["provider"] is None
    assert prov["ai"]["model"] is None


def test_market_data_provenance_is_a_tier_and_never_a_provider_name():
    """MARKET_DATA_ARCHITECTURE.md Developer Rule 4 applies to artifacts too."""
    prov = ai_provenance.begin(ARTIFACT, "a", analysis_version="1.0.0")
    ai_provenance.market_data(prov, source_tier="delayed", observed_at="2026-09-11T03:00:00+00:00")

    assert prov["market_data"]["source_tier"] == "delayed"
    assert set(prov["market_data"]) == {"source_tier", "observed_at"}


# --------------------------------------------------------------------------- #
# 5-6. Failure produces no artifact                                            #
# --------------------------------------------------------------------------- #

def test_a_failure_has_no_completion_instant_and_so_no_age():
    prov = ai_provenance.begin(ARTIFACT, "a", analysis_version="1.0.0")
    ai_provenance.failed(prov, code="generation_error", message="Report generation failed.")

    assert prov["status"] == GenerationStatus.FAILED.value
    assert prov["completed_at"] is None
    assert ai_provenance.age_seconds(prov) is None, "a failure must never acquire an age"
    assert ai_provenance.freshness(prov) is Freshness.UNAVAILABLE


def test_a_failed_generation_is_never_ai_generated():
    prov = ai_provenance.begin(ARTIFACT, "a", analysis_version="1.0.0")
    # The model answered, and the generation then failed anyway. There is no
    # published artifact to attribute the model's work to.
    ai_provenance.ai_succeeded(prov, provider="claude", model="m")
    ai_provenance.failed(prov, code="generation_error", message="failed")

    assert ai_provenance.is_ai_generated(prov) is False


def test_a_completed_artifact_whose_model_failed_is_not_ai_generated():
    prov = ai_provenance.begin(ARTIFACT, "a", analysis_version="1.0.0")
    ai_provenance.ai_did_not_run(prov, outcome=AIOutcome.PROVIDER_ERROR)
    ai_provenance.completed(prov)

    assert prov["status"] == GenerationStatus.COMPLETED.value
    assert ai_provenance.is_ai_generated(prov) is False, (
        "a completed report carrying the deterministic briefing is not an AI artifact"
    )


# --------------------------------------------------------------------------- #
# 8-11. Freshness derives from completed_at                                    #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("age_s,expected", [
    (60, Freshness.FRESH),
    (ai_provenance.FRESH_MAX_SECONDS - 1, Freshness.FRESH),
    (ai_provenance.FRESH_MAX_SECONDS + 1, Freshness.STALE),
    (ai_provenance.STALE_MAX_SECONDS - 1, Freshness.STALE),
    (ai_provenance.STALE_MAX_SECONDS + 1, Freshness.VERY_STALE),
    (30 * 3600, Freshness.VERY_STALE),
])
def test_freshness_bands(age_s, expected):
    assert ai_provenance.freshness(_aged(age_s)) is expected


def test_freshness_ignores_the_start_of_generation():
    """`generated_at` is when the attempt began; freshness is about success.

    A generation that began 20 hours ago and succeeded a minute ago is fresh.
    Reading the wrong field would report it as a previous session's briefing.
    """
    prov = _aged(60)
    prov["generated_at"] = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()

    assert ai_provenance.freshness(prov) is Freshness.FRESH


def test_freshness_is_measured_from_the_record_not_from_the_read():
    """Two reads of one record, one hour apart, give different answers.

    This is the property that makes a persisted freshness value wrong, and the
    reason `describe()` derives it rather than storing it.
    """
    prov = _aged(ai_provenance.FRESH_MAX_SECONDS - 60)
    later = datetime.now(timezone.utc) + timedelta(hours=1)

    assert ai_provenance.freshness(prov) is Freshness.FRESH
    assert ai_provenance.freshness(prov, now=later) is Freshness.STALE


# --------------------------------------------------------------------------- #
# The legacy-data rule                                                          #
# --------------------------------------------------------------------------- #

def test_a_legacy_record_gets_unknown_and_no_invented_timestamp():
    view = ai_provenance.describe(None)

    assert view["known"] is False
    assert view["status"] == "unknown"
    assert view["freshness"] == Freshness.UNKNOWN.value
    assert view["completed_at"] is None
    assert view["generated_at"] is None
    assert view["age_seconds"] is None
    assert view["ai"]["provider"] is None
    assert view["ai"]["model"] is None
    assert view["is_ai_generated"] is False


def test_missing_provenance_is_unknown_and_not_a_failure():
    """The distinction the whole legacy rule rests on.

    An absent record cannot testify that generation did not succeed — only that
    nobody wrote down whether it did. Collapsing UNKNOWN into UNAVAILABLE would
    report every pre-D6.9 report as a failed generation.
    """
    assert ai_provenance.freshness(None) is Freshness.UNKNOWN
    assert ai_provenance.freshness(_record(status="failed")) is Freshness.UNAVAILABLE


def test_an_unparseable_or_naive_completion_instant_is_unknown_not_guessed():
    """A naive datetime read as UTC is a guess, and a guess is a fabrication."""
    for bad in ("not-a-date", "", None, datetime(2026, 9, 11, 8, 30)):
        prov = _record(completed_at=bad)
        assert ai_provenance.age_seconds(prov) is None, bad
        assert ai_provenance.freshness(prov) is Freshness.UNKNOWN, bad


def test_legacy_report_is_never_backfilled_from_generated_at():
    """`generated_at` on a pre-D6.9 document is not evidence of AI completion.

    It was written at the top of a code path that could still fail, so promoting
    it into `completed_at` would manufacture a successful generation that may
    never have happened.
    """
    from services.morning_report import _with_provenance

    legacy = {"date": "2026-01-02", "type": "morning", "available": True,
              "generated_at": "2026-01-02T03:00:00+00:00", "ai_briefing": "text"}

    view = _with_provenance(legacy)["provenance"]

    assert view["known"] is False
    assert view["completed_at"] is None
    assert view["freshness"] == Freshness.UNKNOWN.value


# --------------------------------------------------------------------------- #
# Morning Report lifecycle                                                     #
# --------------------------------------------------------------------------- #

def test_a_generated_report_persists_provenance(fake_db, no_ai):
    report = _generate(fake_db)

    stored = _run(fake_db.reports.find_one({"date": report["date"], "type": "morning"}))
    prov = stored["provenance"]

    assert prov["status"] == GenerationStatus.COMPLETED.value
    assert prov["artifact_type"] == ARTIFACT
    assert prov["completed_at"], "a successful generation must stamp its completion"
    assert prov["analysis_version"]


def test_market_data_observation_instant_is_persisted(fake_db, no_ai):
    report = _generate(fake_db)
    stored = _run(fake_db.reports.find_one({"date": report["date"], "type": "morning"}))

    observed = stored["provenance"]["market_data"]["observed_at"]
    assert observed, "the snapshot instant the numbers came from must be recorded"
    # It is the session's own observation instant, not re-derived at save time.
    assert observed == stored["session"]["observed_at"]


def test_no_ai_configured_yields_a_deterministic_briefing_not_an_ai_one(fake_db, no_ai):
    report = _generate(fake_db)

    assert report["briefing_source"] == "deterministic"
    assert report["provenance"]["ai"]["outcome"] == AIOutcome.NOT_CONFIGURED.value
    assert report["provenance"]["is_ai_generated"] is False
    assert report["provenance"]["ai"]["provider"] is None


def test_a_successful_model_call_is_attributed(fake_db, monkeypatch):
    import server

    monkeypatch.setattr(server, "claude_configured", lambda: True)
    monkeypatch.setattr(server, "gemini_configured", lambda: False)

    class _Engine:
        async def simple_chat_result(self, system, context, **_k):
            return AIResponse(content="A real briefing.", provider="claude",
                              model="claude-3-haiku-20240307")

    monkeypatch.setattr(server, "get_debate_engine", lambda: _Engine())

    report = _generate(fake_db)

    assert report["ai_briefing"] == "A real briefing."
    assert report["briefing_source"] == "ai"
    assert report["provenance"]["is_ai_generated"] is True
    assert report["provenance"]["ai"]["provider"] == "claude"
    assert report["provenance"]["ai"]["model"] == "claude-3-haiku-20240307"
    assert report["provenance"]["ai"]["prompt_key"] == "morning_report"
    assert report["provenance"]["ai"]["prompt_version"]


def test_the_simulated_fallback_is_never_published_as_an_ai_briefing(fake_db, monkeypatch):
    """THE DEFECT, REPRODUCED.

    A configured provider that fails sends `simple_chat` all the way to
    `SimulatedProvider`, whose content is a backend-configuration message. The
    report must publish the grounded briefing instead, and must not call it AI.
    """
    import server
    from services.ai_debate_engine import AIDebateEngine

    monkeypatch.setattr(server, "claude_configured", lambda: True)
    monkeypatch.setattr(server, "gemini_configured", lambda: True)

    engine = AIDebateEngine()

    async def _always_fails(messages, **_k):
        return AIResponse(content="Claude API key is invalid.", provider="claude",
                          model="m", error="authentication_error")

    monkeypatch.setattr(engine.claude, "complete", _always_fails)
    monkeypatch.setattr(engine.gemini, "complete", _always_fails)
    monkeypatch.setattr(engine.claude.__class__, "is_configured", property(lambda _s: True))
    monkeypatch.setattr(engine.gemini.__class__, "is_configured", property(lambda _s: True))
    monkeypatch.setattr(server, "get_debate_engine", lambda: engine)

    report = _generate(fake_db)

    briefing = report["ai_briefing"]
    assert "ANTHROPIC_API_KEY" not in briefing
    assert "GOOGLE_GEMINI_KEY" not in briefing
    assert ".env" not in briefing
    assert "currently offline or unavailable" not in briefing
    # It is the grounded briefing, which restates numbers the report collected.
    assert "24,500" in briefing
    assert report["briefing_source"] == "deterministic"
    assert report["provenance"]["is_ai_generated"] is False
    assert report["provenance"]["ai"]["outcome"] == AIOutcome.PROVIDER_ERROR.value


def test_unreachable_market_data_persists_an_unavailable_record(fake_db, no_ai):
    from services.market_engine import market_gateway

    with patch.object(market_gateway, "get_indices", new_callable=AsyncMock, return_value={}):
        report = _generate(fake_db)

    assert report["available"] is False
    prov = report["provenance"]
    assert prov["status"] == GenerationStatus.UNAVAILABLE.value
    assert prov["completed_at"] is None
    assert prov["freshness"] == Freshness.UNAVAILABLE.value

    stored = _run(fake_db.reports.find_one({"date": report["date"], "type": "morning"}))
    assert stored["provenance"]["status"] == GenerationStatus.UNAVAILABLE.value


def test_a_failed_generation_does_not_become_todays_cached_report(fake_db, no_ai):
    """A transient 08:30 outage must not pin "unavailable" for the whole day.

    The persisted failure is evidence about the last attempt, not a report, so
    the next read retries — exactly as it did before failures were recorded.
    """
    from services.market_engine import market_gateway

    with patch.object(market_gateway, "get_indices", new_callable=AsyncMock, return_value={}):
        first = _generate(fake_db)
    assert first["available"] is False

    second = _generate(fake_db)

    assert second["available"] is True, "the failure record must not be served as a cache hit"
    assert second["provenance"]["status"] == GenerationStatus.COMPLETED.value


def test_top_picks_are_stamped_deterministic_and_share_the_reports_timestamp(fake_db, no_ai):
    report = _generate(fake_db)

    assert report["top_picks_source"] == "deterministic_technical_scan"
    # The picks carry no timestamp of their own: their age IS the report's.
    assert report["provenance"]["completed_at"]


def test_the_scanner_no_longer_fabricates_a_win_rate_or_static_risks():
    """`historical_success` was `65 + confidence/3`, rendered as "Win: 87%".

    No backtest produced it. Asserted at the source so the field cannot return
    through the scanner, whatever any UI does.
    """
    import inspect

    from services import real_market

    source = inspect.getsource(real_market.fetch_real_top_picks)
    # The scored-row literal must not carry them. Comments explaining the
    # removal are expected, so the assertion is on the *key* syntax.
    assert '"historical_success":' not in source
    assert '"risk_factors":' not in source
    assert "Consolidating near recent highs" not in source.split("# D6.9")[-1].split('"reasons"')[-1]


# --------------------------------------------------------------------------- #
# Current AI health vs historical generation status                            #
# --------------------------------------------------------------------------- #

def test_ai_going_offline_does_not_change_a_stored_reports_status(fake_db, monkeypatch):
    """SCENARIO A. Generated at 08:30 with AI up; read at 14:00 with AI down."""
    import server

    monkeypatch.setattr(server, "claude_configured", lambda: True)
    monkeypatch.setattr(server, "gemini_configured", lambda: False)

    class _Engine:
        async def simple_chat_result(self, system, context, **_k):
            return AIResponse(content="Morning briefing.", provider="claude", model="m")

    monkeypatch.setattr(server, "get_debate_engine", lambda: _Engine())
    generated = _generate(fake_db)
    assert generated["provenance"]["is_ai_generated"] is True

    # AI goes offline. The report is read again from cache.
    monkeypatch.setattr(server, "claude_configured", lambda: False)
    monkeypatch.setattr(server, "gemini_configured", lambda: False)

    later = _generate(fake_db)

    assert later["available"] is True, "an existing report stays visible when AI goes down"
    assert later["provenance"]["is_ai_generated"] is True, (
        "current provider health must never rewrite what happened at generation time"
    )
    assert later["provenance"]["ai"]["provider"] == "claude"
    assert later["provenance"]["completed_at"] == generated["provenance"]["completed_at"]


def test_configured_but_never_called_is_not_reported_as_degraded():
    from services import ai_health

    ai_health.reset()
    status = ai_health.provider_status("claude", configured=True)

    assert status["configured"] is True
    assert status["verified"] is False, "no call has been made, so nothing is verified"
    assert status["degraded"] is False, "knowing nothing is not evidence of an outage"


def test_a_failing_provider_is_degraded_and_ai_status_stops_saying_online():
    """THE 'AI READY' LIE, REPRODUCED.

    `online` used to be `bool(api_key)`, so this case rendered "AI ready".
    """
    from services import ai_health
    from services.model_router import get_model_router

    ai_health.reset()
    ai_health.record_failure("claude", "configuration")

    with patch("services.ai_debate_engine.AIDebateEngine.get_status", return_value={
        "claude": {"configured": True, "model": "claude-3-haiku-20240307"},
        "gemini": {"configured": False, "model": "gemini-2.5-flash"},
        "debate_ready": True, "full_debate": False,
    }):
        status = get_model_router().status()

    assert status["debate_ready"] is True, "the capability is unchanged — a key is present"
    assert status["online"] is False, "but nothing is answering, so the platform is not online"
    assert status["health"]["claude"]["degraded"] is True
    ai_health.reset()


def test_a_later_success_clears_a_degraded_provider():
    from services import ai_health

    ai_health.reset()
    ai_health.record_failure("claude", "rate_limit")
    ai_health.record_success("claude", "claude-3-haiku-20240307")

    status = ai_health.provider_status("claude", configured=True)
    assert status["degraded"] is False
    assert status["verified"] is True
    assert status["last_success_model"] == "claude-3-haiku-20240307"
    ai_health.reset()


def test_health_never_records_a_raw_provider_error_string():
    """Provider errors carry request ids, account identifiers and echoed prompts."""
    from services import ai_health

    from observability import errors

    ai_health.reset()
    secret = "invalid x-api-key sk-ant-SECRET-123 for request req_0xdeadbeef"
    ai_health.record_failure("claude", ai_health.classify(secret))

    status = ai_health.provider_status("claude", configured=True)
    assert "SECRET" not in str(status)
    assert "req_0xdeadbeef" not in str(status)
    assert "sk-ant" not in str(status)
    # An unrecognised error degrades to the generic class. That is the designed
    # worst case and it is still safe: the label comes from the closed
    # vocabulary, so no substring of the provider's text can ever reach it.
    assert status["last_error_class"] in errors.ERROR_CLASSES, status["last_error_class"]
    assert status["last_error_class"] == errors.AI_PROVIDER

    # A recognisable error still classifies, so the fallback above is a
    # fallback and not the only outcome — without this, the assertion above
    # would pass even if `classify` returned the generic label unconditionally.
    ai_health.record_failure("claude", ai_health.classify("authentication failed: invalid api key"))
    assert ai_health.provider_status("claude", configured=True)["last_error_class"] == errors.CONFIGURATION
    ai_health.reset()


def test_raw_provider_text_handed_straight_to_record_failure_is_refused():
    """The contract is enforced, not documented.

    A mutation campaign found that every test reached `record_failure` through
    `classify()`, so nothing covered the obvious mistake — writing
    `record_failure(provider, resp.error)` — and a docstring was the only thing
    standing between a provider's error text and a field `/api/ai/status`
    serves.
    """
    from observability import errors
    from services import ai_health

    ai_health.reset()
    ai_health.record_failure("claude", "x-api-key sk-ant-SECRET-123 rejected for req_0xdeadbeef")

    status = ai_health.provider_status("claude", configured=True)
    assert "SECRET" not in str(status)
    assert "req_0xdeadbeef" not in str(status)
    assert status["last_error_class"] == errors.AI_PROVIDER
    assert status["last_error_class"] in errors.ERROR_CLASSES
    ai_health.reset()


def test_ai_status_advertises_the_model_it_actually_calls():
    """The endpoint hardcoded `claude-3-5-sonnet-20241022` while every call in
    the process went to `claude-3-haiku-20240307`."""
    from services.claude_provider import CLAUDE_DEFAULT_MODEL
    from services.model_router import get_model_router

    status = get_model_router().status()

    assert status["providers"]["claude"]["model"] == CLAUDE_DEFAULT_MODEL


# --------------------------------------------------------------------------- #
# API surface                                                                  #
# --------------------------------------------------------------------------- #

def test_the_api_exposes_provenance_without_leaking_anything(client, fake_db, auth_headers, no_ai):
    patches = _patches()
    for p in patches:
        p.start()
    try:
        response = client.get("/api/analysis/reports/morning", headers=auth_headers)
    finally:
        for p in patches:
            p.stop()

    assert response.status_code == 200
    prov = response.json()["provenance"]

    for field in ("status", "generated_at", "completed_at", "ai", "market_data",
                  "freshness", "age_seconds", "is_ai_generated", "freshness_policy"):
        assert field in prov, f"missing '{field}'"

    body = response.text.lower()
    for forbidden in ("anthropic_api_key", "google_gemini_key", "sk-ant", "traceback",
                      "mongodb://", "api_key"):
        assert forbidden not in body, f"leaked '{forbidden}'"
    # The prompt template itself is never a client-facing value — only its key
    # and version (PROMPT.md → Forbidden Behaviors).
    assert "you are a morning market analyst" not in body


def test_the_compact_endpoint_never_publishes_a_failure_as_a_report(client, monkeypatch):
    """It assigned `f"Morning report generation failed: {e}"` to the report body
    and returned `available: True` — a failure served as a successful report,
    carrying the raw exception text."""
    import server

    monkeypatch.setattr(server, "claude_configured", lambda: True)
    monkeypatch.setattr(server, "gemini_configured", lambda: False)

    class _Engine:
        async def simple_chat_result(self, *_a, **_k):
            raise RuntimeError("connection to mongodb://user:pw@host failed (req_0xdead)")

    monkeypatch.setattr(server, "get_debate_engine", lambda: _Engine())

    with patch("services.real_market.fetch_real_top_picks", new_callable=AsyncMock,
               return_value={"picks": []}), \
         patch("services.real_market.fetch_real_sectors", new_callable=AsyncMock, return_value=[]), \
         patch.object(server, "real_overview", new_callable=AsyncMock, return_value={
             "nifty": {"value": 24500.0, "change_pct": 0.5},
             "bank_nifty": {"value": 52000.0, "change_pct": -0.2},
             "market_status": "CLOSED", "market_sentiment": 55, "india_vix": 14.0,
         }):
        response = client.get("/api/analysis/morning-report")

    assert response.status_code == 200
    body = response.json()
    assert body["briefing_source"] == "deterministic"
    assert body["ai_provider"] is None and body["ai_model"] is None
    assert "mongodb://" not in response.text
    assert "req_0xdead" not in response.text
    assert "generation failed" not in body["report"].lower()


def test_the_ready_signal_carries_the_generation_outcome(fake_db, no_ai):
    """A listener refetching on the 8:30 signal must land in the right state.

    The event used to say only "available", which a failed run also satisfies
    on a day with a stale cached document.
    """
    from services import morning_report as mr

    published = []

    class _Bus:
        async def publish(self, event_type, data):
            published.append((event_type, data))

    with patch("services.market_engine.event_bus.event_bus", _Bus()), \
         patch.object(mr, "notify_users", new_callable=AsyncMock, return_value=0):
        patches = _patches()
        for p in patches:
            p.start()
        try:
            _run(mr.generate_and_notify(fake_db))
        finally:
            for p in patches:
                p.stop()

    assert published, "the ready-signal must still be published"
    _, data = published[0]
    assert data["status"] == GenerationStatus.COMPLETED.value
    assert data["completed_at"]
    assert data["is_ai_generated"] is False, "no provider was configured in this run"


def test_a_failed_regeneration_does_not_leave_a_partial_artifact(fake_db, no_ai):
    """SCENARIO E — generation fails after a successful report already exists.

    Persistence was `update_one({"$set": ...})`, which merges: the successful
    run's sections and its `available: true` survived beside the failed run's
    `status: "failed"`, so the stored document read as a complete report whose
    own provenance said it had not been produced — and it was servable.
    """
    from services.market_engine import market_gateway

    good = _generate(fake_db)
    assert good["available"] is True
    assert good["top_picks"], "the successful run must have written sections"

    # A forced regeneration — what the 8:30 scheduler does — that cannot reach
    # market data. `force` is required: an unforced read would serve the cached
    # successful report and never attempt a second generation at all.
    from services.morning_report import get_morning_report

    with patch.object(market_gateway, "get_indices", new_callable=AsyncMock, return_value={}):
        patches = _patches()
        for p in patches:
            p.start()
        try:
            _run(get_morning_report(fake_db, user=None, force=True))
        finally:
            for p in patches:
                p.stop()

    stored = _run(fake_db.reports.find_one({"date": good["date"], "type": "morning"}))

    assert stored["provenance"]["status"] == GenerationStatus.UNAVAILABLE.value
    assert stored["available"] is False
    for section in ("top_picks", "ai_briefing", "key_risks", "nifty", "briefing_source"):
        assert section not in stored, (
            f"'{section}' survived from the previous successful run into a failed document"
        )

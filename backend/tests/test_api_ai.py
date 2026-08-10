"""AI API behaviour with every provider mocked (PH3.3).

WHAT PRODUCTION FAILURE THIS CATCHES
------------------------------------
The AI is the product's core, and it is also the least reliable dependency in
the stack: LLM providers time out, rate-limit, return empty completions, return
prose where JSON was requested, and occasionally return something that parses
into the wrong shape entirely. Every one of those must degrade into an honest
answer, because the alternative — a 500 — takes down the feature users came for.

Two failure modes matter more than the status code, and both are asserted here:

* **Fabrication.** An unavailable provider must produce "AI is unavailable", not
  a confident-sounding answer assembled from nothing. A trading assistant that
  invents analysis when its model is down is worse than one that says nothing.
* **Persistence of garbage.** A failed or empty completion must not be written
  into the conversation history or into AI Memory, where it would silently
  poison every later prompt that reads them back as context.

NO TEST IN THIS FILE MAY REACH A REAL PROVIDER
----------------------------------------------
Three independent guarantees, defence in depth: `tests/_testenv.py` blanks
`ANTHROPIC_API_KEY`/`GOOGLE_GEMINI_KEY` so every `*_configured()` check reads
False; each test patches the provider boundary explicitly; and
`tests/_netguard.py` blocks the socket if anything slips past both. No API key
appears anywhere in this file.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

import server


class _ProviderDown(Exception):
    """Stand-in for a provider SDK's own error type."""


#: The ways an LLM call fails in production.
#:
#: These are injected at `AIDebateEngine.simple_chat` — the single boundary
#: every AI feature funnels through on its way to a provider SDK. Patching there
#: rather than at the route means the Model Router, the Prompt Library and each
#: route's own result handling all run for real; only the network call is
#: replaced. Mocking the router instead would have skipped exactly the code most
#: likely to mishandle a bad completion.
#: Only *reachable* results appear here, for the same reason the market suite
#: carries no `side_effect=TimeoutError` cases. A provider SDK exception cannot
#: reach `simple_chat`: `ClaudeProvider.complete` / `GeminiProvider.complete`
#: catch it and return an `AIResponse` carrying `error`, and `gemini_analyze`
#: catches it and returns an explanatory string. A route therefore only ever
#: observes a *completion* — possibly empty, possibly nonsense, never a raise.
#:
#: Injecting a raise at `simple_chat` would test a state production cannot
#: enter, and "fixing" the routes to survive it would mean writing exception
#: handlers for exceptions that never arrive. The containment itself is asserted
#: in `TestFailureContainmentLivesInTheProviderLayer`, at the boundary that
#: provides it — so removing that `try/except` fails a test where it broke.
AI_FAILURES = {
    "empty_string": {"return_value": ""},
    "none": {"return_value": None},
    "whitespace_only": {"return_value": "   \n  "},
    "quota_message": {"return_value": "[Gemini quota reached] free tier limit"},
    "provider_error_text": {"return_value": "Gemini analysis unavailable: upstream 503"},
    "malformed_json": {"return_value": "{not: valid json at all"},
}
AI_FAILURE_IDS = list(AI_FAILURES)

CONTROLLED = {200, 400, 401, 403, 404, 422, 429, 503}


def assert_controlled(resp, context):
    assert resp.status_code in CONTROLLED, (
        f"{context} answered {resp.status_code}; an AI provider failure must be "
        f"a controlled response, never an unhandled 500."
    )


@pytest.fixture
def ai_call(monkeypatch):
    """Install a stubbed provider call and hand the mock back to the test.

    PATCH THE SINGLETON INSTANCE, NOT THE CLASS
    -------------------------------------------
    The obvious form — `monkeypatch.setattr(AIDebateEngine, "simple_chat", ...)`
    — passes in isolation and fails in a full-suite run, which is the worst
    possible failure mode and took a while to explain.

    `test_ai_workspace.py` patches the *instance*:
    `monkeypatch.setattr(ai_debate_engine._engine, "simple_chat", _fake)`.
    Because `simple_chat` lives on the class, monkeypatch records the "original"
    as the bound method it read through the instance, and restores it by
    `setattr`-ing that bound method **onto the instance**. Teardown therefore
    leaves a permanent instance attribute shadowing the class attribute — the
    value is correct, so nothing looks wrong, but from that point on any patch
    applied to the class is invisible to `_engine`.

    So this fixture patches the same instance every other AI test patches.
    `ModelRouter` holds that one object (`self._engine = get_debate_engine()`,
    resolved once at import), so it is the single point every AI feature
    actually routes through.
    """
    from services import ai_debate_engine

    def install(**kwargs):
        mock = AsyncMock(**kwargs)
        monkeypatch.setattr(ai_debate_engine._engine, "simple_chat", mock)
        return mock
    return install


@pytest.fixture
def ai_offline(monkeypatch):
    """No AI provider is configured — the state a missing/rotated key produces."""
    monkeypatch.setattr(server, "claude_configured", lambda: False)
    monkeypatch.setattr(server, "gemini_configured", lambda: False)


# --------------------------------------------------------------------------- #
# Provider isolation                                                            #
# --------------------------------------------------------------------------- #
class TestProviderIsolation:
    def test_no_ai_provider_is_configured_in_the_test_environment(self):
        """The precondition every other test in this file rests on.

        If a developer's real key ever leaked into the suite, these tests would
        start making billable network calls and asserting against live model
        output. `_testenv.py` blanks the keys; this fails loudly if that stops
        being true.
        """
        assert server.claude_configured() is False
        assert server.gemini_configured() is False

    def test_ai_status_is_public_and_honest_when_offline(self, client):
        """The workspace renders a status pill pre-login, so this is
        deliberately unauthenticated — and it must report offline, not pretend."""
        resp = client.get("/api/ai/status")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_prompt_library_never_exposes_raw_prompt_text(self, client):
        """PROMPT.md forbidden behaviour: the templates are proprietary and are
        also an injection surface if published."""
        resp = client.get("/api/ai/prompts")
        assert resp.status_code == 200
        body = resp.json()
        assert "version" in body and "prompts" in body
        for prompt in body["prompts"]:
            assert "template" not in prompt, "raw prompt text was exposed"
            assert "system" not in prompt


# --------------------------------------------------------------------------- #
# Chat                                                                          #
# --------------------------------------------------------------------------- #
class TestChat:
    @pytest.mark.parametrize("failure", AI_FAILURE_IDS)
    def test_provider_failure_never_becomes_a_500(
            self, authenticated_client, fake_db, test_user, ai_call, failure):
        ai_call(**AI_FAILURES[failure])
        resp = authenticated_client.post("/api/chat", json={"message": "TEST question"})
        assert_controlled(resp, f"/api/chat with a provider that {failure}")

    def test_successful_chat_is_persisted_as_a_turn_pair(
            self, authenticated_client, fake_db, test_user, ai_call):
        ai_call(return_value="TEST answer")
        resp = authenticated_client.post("/api/chat", json={"message": "TEST question"})
        assert resp.status_code == 200
        assert "TEST answer" in str(resp.json()["response"])
        roles = [m["role"] for m in fake_db.chat_messages.docs]
        assert roles == ["user", "assistant"], \
            "conversation history must record both halves or context is lost"
        assert all(m["user_id"] == str(test_user["_id"])
                   for m in fake_db.chat_messages.docs)

    def test_history_is_scoped_to_the_caller(
            self, authenticated_client, fake_db, test_user, other_user):
        fake_db.chat_messages.docs.extend([
            {"_id": ObjectId(), "user_id": str(test_user["_id"]), "session_id": "s1",
             "role": "user", "content": "TEST mine", "created_at": "2026-08-01T00:00:00"},
            {"_id": ObjectId(), "user_id": str(other_user["_id"]), "session_id": "s1",
             "role": "user", "content": "TEST theirs", "created_at": "2026-08-01T00:00:00"},
        ])
        resp = authenticated_client.get("/api/chat/history")
        assert resp.status_code == 200
        body = resp.json()
        messages = body if isinstance(body, list) else body.get("messages", [])
        assert not any("theirs" in str(m.get("content", "")) for m in messages), \
            "another user's conversation was returned"

    @pytest.mark.parametrize("message", ["", "   ", "x" * 100_000])
    def test_degenerate_messages_are_handled(
            self, authenticated_client, fake_db, test_user, ai_call, message):
        ai_call(return_value="TEST answer")
        resp = authenticated_client.post("/api/chat", json={"message": message})
        assert_controlled(resp, f"/api/chat with message of length {len(message)}")

    def test_missing_message_field_is_422(self, authenticated_client, fake_db, test_user):
        resp = authenticated_client.post("/api/chat", json={})
        assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Portfolio review / reflection                                                 #
# --------------------------------------------------------------------------- #
class TestPortfolioReview:
    @pytest.mark.parametrize("failure", AI_FAILURE_IDS)
    def test_provider_failure_never_becomes_a_500(
            self, authenticated_client, fake_db, test_user, ai_call, failure):
        ai_call(**AI_FAILURES[failure])
        with patch("services.portfolio_monitor.analyze_portfolio_health",
                   new_callable=AsyncMock, return_value={"health_score": 70, "at_risk": 0,
                                                         "total_unrealized_pnl": 0}):
            resp = authenticated_client.post("/api/ai/portfolio-review", json={})
        assert_controlled(resp, f"/api/ai/portfolio-review with a provider that {failure}")


class TestReflection:
    def test_no_closed_trades_is_an_honest_answer_not_an_ai_call(
            self, authenticated_client, fake_db, test_user, ai_call):
        """With nothing to reflect on, the route must short-circuit. Calling the
        model anyway would bill for a prompt containing no data and invite a
        hallucinated "lesson" about trades that do not exist."""
        provider = ai_call(return_value="TEST should never be called")
        resp = authenticated_client.post("/api/ai/reflect")
        assert resp.status_code == 200
        assert resp.json()["lessons_added"] == 0
        provider.assert_not_called()

    @pytest.mark.parametrize("failure", AI_FAILURE_IDS)
    def test_provider_failure_never_becomes_a_500(
            self, authenticated_client, fake_db, test_user, ai_call, failure):
        fake_db.trades.docs.append({
            "_id": ObjectId(), "user_id": str(test_user["_id"]), "symbol": "TESTCO",
            "status": "CLOSED", "pnl": 100.0, "entry_price": 100.0, "exit_price": 110.0,
            "quantity": 10, "type": "BUY", "exit_time": "2026-08-01T00:00:00+00:00",
        })
        ai_call(**AI_FAILURES[failure])
        resp = authenticated_client.post("/api/ai/reflect")
        assert_controlled(resp, f"/api/ai/reflect with a provider that {failure}")

    @pytest.mark.parametrize("failure", ["empty_string", "none", "whitespace_only"])
    def test_an_empty_completion_adds_no_lesson_to_memory(
            self, authenticated_client, fake_db, test_user, ai_call, failure):
        """AI Memory is read back into later prompts. An empty or whitespace
        "lesson" persisted here would be fed to the model forever as if it were
        a real insight the user had earned."""
        fake_db.trades.docs.append({
            "_id": ObjectId(), "user_id": str(test_user["_id"]), "symbol": "TESTCO",
            "status": "CLOSED", "pnl": 100.0, "entry_price": 100.0, "exit_price": 110.0,
            "quantity": 10, "type": "BUY", "exit_time": "2026-08-01T00:00:00+00:00",
        })
        ai_call(**AI_FAILURES[failure])
        resp = authenticated_client.post("/api/ai/reflect")
        assert_controlled(resp, "/api/ai/reflect")
        assert resp.json()["lessons_added"] == 0, \
            "an empty completion was parsed into a lesson"
        assert fake_db.ai_memory.docs == [], \
            "an empty completion was written into AI Memory"


# --------------------------------------------------------------------------- #
# AI memory                                                                     #
# --------------------------------------------------------------------------- #
class TestAIMemory:
    def test_memory_is_per_user(
            self, authenticated_client, fake_db, test_user, other_user):
        fake_db.ai_memory.docs.append(
            {"_id": ObjectId(), "user_id": str(other_user["_id"]),
             "notes": "TEST someone else's private notes"})
        resp = authenticated_client.get("/api/ai/memory")
        assert resp.status_code == 200
        assert "someone else" not in str(resp.json()), \
            "another user's AI memory leaked into this response"

    def test_memory_update_writes_only_the_callers_record(
            self, authenticated_client, fake_db, test_user, other_user):
        fake_db.ai_memory.docs.append(
            {"_id": ObjectId(), "user_id": str(other_user["_id"]), "notes": "TEST theirs"})
        resp = authenticated_client.put("/api/ai/memory", json={"notes": "TEST mine"})
        assert resp.status_code == 200
        victim = next(d for d in fake_db.ai_memory.docs
                      if d["user_id"] == str(other_user["_id"]))
        assert victim["notes"] == "TEST theirs"


# --------------------------------------------------------------------------- #
# Conversations                                                                 #
# --------------------------------------------------------------------------- #
class TestConversations:
    def test_list_is_scoped_to_the_caller(
            self, authenticated_client, fake_db, test_user, other_user):
        fake_db.chat_messages.docs.extend([
            {"_id": ObjectId(), "user_id": str(test_user["_id"]), "session_id": "mine",
             "role": "user", "content": "TEST mine", "created_at": "2026-08-01T00:00:00"},
            {"_id": ObjectId(), "user_id": str(other_user["_id"]), "session_id": "theirs",
             "role": "user", "content": "TEST theirs", "created_at": "2026-08-01T00:00:00"},
        ])
        resp = authenticated_client.get("/api/ai/conversations")
        assert resp.status_code == 200
        assert "theirs" not in str(resp.json())

    def test_deleting_a_conversation_cannot_reach_another_users_session(
            self, authenticated_client, fake_db, test_user, other_user):
        fake_db.chat_messages.docs.append(
            {"_id": ObjectId(), "user_id": str(other_user["_id"]), "session_id": "theirs",
             "role": "user", "content": "TEST theirs", "created_at": "2026-08-01T00:00:00"})
        resp = authenticated_client.delete("/api/ai/conversations/theirs")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0, "another user's conversation was deleted"
        assert len(fake_db.chat_messages.docs) == 1


# --------------------------------------------------------------------------- #
# Gemini direct                                                                 #
# --------------------------------------------------------------------------- #
class TestGeminiDirect:
    @pytest.mark.parametrize("failure", AI_FAILURE_IDS)
    def test_provider_failure_never_becomes_a_500(
            self, authenticated_client, fake_db, test_user, failure):
        with patch("services.gemini_direct.gemini_analyze",
                   new_callable=AsyncMock, **AI_FAILURES[failure]):
            resp = authenticated_client.post("/api/gemini/chat",
                                             json={"message": "TEST question"})
        assert_controlled(resp, f"/api/gemini/chat with a provider that {failure}")

    def test_unavailable_provider_says_so_rather_than_inventing_analysis(
            self, authenticated_client, fake_db, test_user):
        with patch("services.gemini_direct.gemini_analyze",
                   new_callable=AsyncMock, return_value=None):
            resp = authenticated_client.post("/api/gemini/chat",
                                             json={"message": "Should I buy RELIANCE?"})
        assert resp.status_code == 200
        assert resp.json()["response"] == "Gemini unavailable", \
            "an offline model must not produce trading advice"


# --------------------------------------------------------------------------- #
# The debate engine's own fallback                                              #
# --------------------------------------------------------------------------- #
class TestDebateEngineFallback:
    def test_a_failing_debate_returns_a_structured_unavailable_result(self):
        """`ai_dual_debate` is the one place that swallows provider errors and
        synthesises a reply. It must keep the response *shape* — callers index
        into these keys — while making the unavailability explicit."""
        async def exercise():
            with patch.object(server, "get_debate_engine") as engine:
                engine.return_value.debate = AsyncMock(
                    side_effect=_ProviderDown("both providers down"))
                return await server.ai_dual_debate("TEST prompt")

        result = asyncio.run(exercise())
        assert result["providers_active"] == []
        assert result["rounds_completed"] == 0
        for key in ("claude_analysis", "gemini_analysis", "final_verdict"):
            assert key in result, f"callers index {key}; the shape must survive failure"
        verdict = result["final_verdict"].lower()
        assert "unable" in verdict or "unavailable" in verdict, \
            f"the failure must be stated plainly, not implied: {verdict!r}"


# --------------------------------------------------------------------------- #
# Where AI failure containment actually lives                                   #
# --------------------------------------------------------------------------- #
class TestFailureContainmentLivesInTheProviderLayer:
    """The guarantee every route above depends on, asserted at its source.

    No AI route catches provider exceptions, and none needs to — each provider
    adapter converts an SDK failure into a *value*: `complete()` returns an
    `AIResponse` carrying `error`, and `gemini_analyze` returns a human-readable
    string. That conversion is load-bearing for the whole AI surface and is a
    single `except Exception` per adapter. If a refactor narrowed it, every AI
    endpoint would start 500ing on the next provider incident, and no
    route-level test would have noticed beforehand.
    """

    def test_claude_sdk_failure_becomes_an_error_response_not_an_exception(self):
        from services.ai_provider import AIMessage
        from services.claude_provider import ClaudeProvider

        async def exercise():
            with patch("services.claude_provider._get_key", return_value="TEST-key"), \
                 patch("anthropic.AsyncAnthropic", side_effect=_ProviderDown("SDK exploded")):
                return await ClaudeProvider().complete(
                    [AIMessage(role="user", content="TEST")])

        response = asyncio.run(exercise())
        assert response.error, "an SDK failure must surface as AIResponse.error"
        assert not response.success

    def test_an_unconfigured_provider_says_so_rather_than_raising(self):
        from services.ai_provider import AIMessage
        from services.claude_provider import ClaudeProvider

        async def exercise():
            with patch("services.claude_provider._get_key", return_value=None):
                return await ClaudeProvider().complete(
                    [AIMessage(role="user", content="TEST")])

        response = asyncio.run(exercise())
        assert response.error == "missing_api_key"
        assert not response.success

    def test_gemini_direct_failure_becomes_a_string_not_an_exception(self):
        from services import gemini_direct

        async def exercise():
            with patch.object(gemini_direct, "_get_key", return_value="TEST-key"), \
                 patch("google.genai.Client", side_effect=_ProviderDown("SDK exploded")):
                return await gemini_direct.gemini_analyze("TEST prompt")

        result = asyncio.run(exercise())
        assert isinstance(result, str)
        assert "unavailable" in result.lower()

    def test_gemini_quota_exhaustion_is_explained_not_swallowed(self):
        """A 429 is the single most likely Gemini failure on a free tier, and
        the user needs to be told it is a quota problem rather than shown a
        generic error they cannot act on."""
        from services import gemini_direct

        async def exercise():
            with patch.object(gemini_direct, "_get_key", return_value="TEST-key"), \
                 patch("google.genai.Client",
                       side_effect=_ProviderDown("429 RESOURCE_EXHAUSTED")):
                return await gemini_direct.gemini_analyze("TEST prompt")

        result = asyncio.run(exercise())
        assert "quota" in result.lower()

    def test_simple_chat_falls_back_when_the_preferred_provider_fails(self):
        """Claude failing must hand off to Gemini rather than surfacing an
        error — the multi-provider architecture exists precisely for this."""
        from services.ai_debate_engine import AIDebateEngine
        from services.ai_provider import AIResponse

        engine = AIDebateEngine()
        failed = AIResponse(content="", provider="claude", model="m", error="boom")
        ok = AIResponse(content="TEST fallback answer", provider="gemini", model="m")

        async def exercise():
            with patch.object(type(engine.claude), "is_configured", True), \
                 patch.object(type(engine.gemini), "is_configured", True), \
                 patch.object(engine.claude, "complete", new_callable=AsyncMock,
                              return_value=failed), \
                 patch.object(engine.gemini, "complete", new_callable=AsyncMock,
                              return_value=ok):
                return await engine.simple_chat("TEST system", "TEST message")

        assert asyncio.run(exercise()) == "TEST fallback answer"

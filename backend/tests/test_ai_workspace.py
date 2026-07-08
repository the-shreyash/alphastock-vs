"""Hermetic tests for the AI Workspace (Sprint 6).

Covers the /api/ai router (status, prompts, memory, conversations, learning,
trade review, portfolio review, reflection, activity) plus the Prompt Library,
Model Router and AI Memory services.

AI network calls are stubbed at the debate-engine singleton so tests never hit
Claude/Gemini and stay fast + deterministic even though real keys are present
in .env. Follows the same in-process FakeDB approach as the other feature tests
(see tests/conftest.py).
"""
import pytest
from bson import ObjectId

import server
from services import ai_debate_engine
from services.prompt_library import get_prompt, list_prompts, PROMPTS
from services.ai_memory import build_memory_context


@pytest.fixture
def stub_ai(monkeypatch):
    """Replace the debate engine's simple_chat with a canned async response so
    Model-Router-backed endpoints never make a network call."""
    async def _fake(system_prompt, user_message, prefer="auto", max_tokens=800):
        return f"[stub:{prefer}] analysis based on provided data."
    monkeypatch.setattr(ai_debate_engine._engine, "simple_chat", _fake)
    return _fake


# ── Prompt Library ──────────────────────────────────────────────────────
def test_prompt_library_renders_with_master_envelope():
    text = get_prompt("ai_chat", memory="Name: Jo")
    assert "StockAssist AI (SAI)" in text  # master envelope present
    assert "personal investment assistant" in text.lower()
    assert "Name: Jo" in text  # placeholder merged


def test_prompt_library_tolerates_missing_placeholders():
    # Should not raise even though {memory} isn't supplied.
    text = get_prompt("ai_chat")
    assert "personal investment assistant" in text.lower()


def test_prompt_library_lists_all_prompts():
    items = list_prompts()
    keys = {i["key"] for i in items}
    assert {"ai_chat", "trade_review", "learning_mentor", "portfolio_manager"} <= keys
    assert all("template" not in i for i in items)  # never leaks raw templates


# ── Memory helper ────────────────────────────────────────────────────────
def test_build_memory_context_empty_returns_blank():
    assert build_memory_context({}, {"preferred_sectors": [], "favorite_companies": []}) == ""


def test_build_memory_context_includes_known_fields():
    ctx = build_memory_context(
        {"name": "Asha", "capital": 500000},
        {"risk_preference": "aggressive", "preferred_sectors": ["IT"], "goals": "wealth"},
    )
    assert "Asha" in ctx and "aggressive" in ctx and "IT" in ctx and "wealth" in ctx


# ── /api/ai/status + /prompts + /activity (public) ───────────────────────
def test_ai_status_reports_routing_and_providers(client, fake_db):
    r = client.get("/api/ai/status")
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body and "claude" in body["providers"]
    assert "routing" in body and len(body["routing"]) >= 4
    assert "policy" in body


def test_ai_prompts_endpoint(client, fake_db):
    r = client.get("/api/ai/prompts")
    assert r.status_code == 200
    body = r.json()
    assert body["version"]
    assert any(p["key"] == "ai_chat" for p in body["prompts"])


def test_ai_activity_endpoint(client, fake_db):
    r = client.get("/api/ai/activity")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── Memory endpoints ─────────────────────────────────────────────────────
def test_get_and_update_memory(client, fake_db, auth_headers):
    r = client.get("/api/ai/memory", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["experience_level"] == "beginner"  # default

    r = client.put(
        "/api/ai/memory",
        headers=auth_headers,
        json={"risk_preference": "aggressive", "preferred_sectors": ["Banking", "IT"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["risk_preference"] == "aggressive"
    assert body["preferred_sectors"] == ["Banking", "IT"]

    # Persisted across requests
    r = client.get("/api/ai/memory", headers=auth_headers)
    assert r.json()["risk_preference"] == "aggressive"


def test_memory_requires_auth(client, fake_db):
    assert client.get("/api/ai/memory").status_code in (401, 403)


# ── Conversations ────────────────────────────────────────────────────────
def test_conversation_lifecycle(client, fake_db, test_user, auth_headers):
    uid = str(test_user["_id"])
    # Seed two sessions worth of messages
    for sid, text in [("s1", "hello one"), ("s1", "reply one"), ("s2", "hello two")]:
        fake_db.chat_messages.docs.append({
            "_id": ObjectId(), "user_id": uid, "session_id": sid,
            "role": "user", "content": text, "created_at": "2026-01-01T00:00:0" + str(len(fake_db.chat_messages.docs)),
        })

    r = client.get("/api/ai/conversations", headers=auth_headers)
    assert r.status_code == 200
    convos = r.json()
    assert {c["session_id"] for c in convos} == {"s1", "s2"}

    # New conversation mints an id
    r = client.post("/api/ai/conversations", headers=auth_headers)
    assert r.json()["session_id"].startswith(f"chat-{uid}-")

    # Delete s1 removes its messages only
    r = client.delete("/api/ai/conversations/s1", headers=auth_headers)
    assert r.status_code == 200 and r.json()["deleted"] == 2
    remaining = {d["session_id"] for d in fake_db.chat_messages.docs}
    assert remaining == {"s2"}


# ── Learning ─────────────────────────────────────────────────────────────
def test_learn_endpoint(client, fake_db, auth_headers, stub_ai):
    r = client.post("/api/ai/learn", headers=auth_headers, json={"topic": "RSI", "level": "beginner"})
    assert r.status_code == 200
    body = r.json()
    assert body["topic"] == "RSI"
    assert body["prompt_key"] == "learning_mentor"
    assert "stub" in body["content"]


# ── Trade Review ─────────────────────────────────────────────────────────
def test_trade_review_by_id(client, fake_db, test_user, auth_headers, stub_ai):
    tid = ObjectId()
    fake_db.trades.docs.append({
        "_id": tid, "user_id": str(test_user["_id"]), "symbol": "TCS",
        "stock_name": "Tata Consultancy", "type": "BUY", "entry_price": 3500,
        "exit_price": 3700, "quantity": 10, "stop_loss": 3400, "target1": 3800,
        "status": "CLOSED", "pnl": 2000, "pnl_percent": 5.7,
    })
    r = client.post("/api/ai/trade-review", headers=auth_headers, json={"trade_id": str(tid)})
    assert r.status_code == 200
    assert r.json()["cached"] is False
    assert "stub" in r.json()["content"]

    # Second call returns cached review (no regeneration)
    r2 = client.post("/api/ai/trade-review", headers=auth_headers, json={"trade_id": str(tid)})
    assert r2.json()["cached"] is True


def test_trade_review_rejects_open_trade(client, fake_db, test_user, auth_headers, stub_ai):
    tid = ObjectId()
    fake_db.trades.docs.append({
        "_id": tid, "user_id": str(test_user["_id"]), "symbol": "INFY",
        "stock_name": "Infosys", "status": "OPEN", "entry_price": 1500,
        "quantity": 5, "stop_loss": 1450, "target1": 1600,
    })
    r = client.post("/api/ai/trade-review", headers=auth_headers, json={"trade_id": str(tid)})
    assert r.status_code == 400


def test_trade_review_requires_input(client, fake_db, auth_headers, stub_ai):
    r = client.post("/api/ai/trade-review", headers=auth_headers, json={})
    assert r.status_code == 400


# ── Portfolio review ─────────────────────────────────────────────────────
def test_portfolio_review_empty(client, fake_db, auth_headers, stub_ai):
    r = client.post("/api/ai/portfolio-review", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["empty"] is True and body["holdings_count"] == 0


# ── Reflection ───────────────────────────────────────────────────────────
def test_reflect_no_trades(client, fake_db, auth_headers, stub_ai):
    r = client.post("/api/ai/reflect", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["lessons_added"] == 0


def test_reflect_parses_and_stores_lessons(client, fake_db, test_user, auth_headers, monkeypatch):
    # Stub returns a bulleted list so the endpoint parses + persists lessons.
    async def _bullets(system_prompt, user_message, prefer="auto", max_tokens=800):
        return "Key lessons:\n- Respect your stop loss\n- Avoid revenge trading\n- Size positions consistently"
    monkeypatch.setattr(ai_debate_engine._engine, "simple_chat", _bullets)

    fake_db.trades.docs.append({
        "_id": ObjectId(), "user_id": str(test_user["_id"]), "symbol": "HDFCBANK",
        "stock_name": "HDFC Bank", "status": "CLOSED", "entry_price": 1600,
        "exit_price": 1550, "quantity": 10, "stop_loss": 1580, "target1": 1700,
        "pnl": -500, "pnl_percent": -3.1, "exit_time": "2026-01-02T00:00:00",
    })
    r = client.post("/api/ai/reflect", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["lessons_added"] == 3

    # Lessons are persisted into AI memory and surface on GET /api/ai/memory
    mem = client.get("/api/ai/memory", headers=auth_headers).json()
    stored = [l["lesson"] for l in mem.get("lessons", [])]
    assert "Respect your stop loss" in stored

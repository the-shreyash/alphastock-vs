"""
AI Memory service for StockAssist AI (SAI).

Implements the persistent memory layer from `.claude/AI_AGENT_SYSTEM.md` →
"AI Memory". Two kinds of memory are handled here:

  * User Memory       — durable facts about the user (risk preference, goals,
                        preferred sectors, favourite companies, experience level,
                        and lessons the reflection engine has stored). One
                        document per user in the `ai_user_memory` collection.

  * Conversation Memory — recent chat turns, reused from the existing
                        `chat_messages` collection, condensed into a short
                        context string that is injected into the AI Chat prompt.

The service intentionally uses only `$set` semantics (read-modify-write for
lists) so it behaves identically against real Motor and the hermetic FakeDB
used in tests. All functions take an explicit `db` handle to stay testable and
decoupled from the FastAPI app, matching the convention used by
`services/trade_journal.py` and `services/portfolio_monitor.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# Editable memory fields the user (or reflection engine) may set.
EDITABLE_FIELDS = (
    "risk_preference",
    "goals",
    "preferred_sectors",
    "favorite_companies",
    "experience_level",
    "preferred_language",
    "notes",
)

_DEFAULT_MEMORY = {
    "risk_preference": None,
    "goals": None,
    "preferred_sectors": [],
    "favorite_companies": [],
    "experience_level": "beginner",
    "preferred_language": "English",
    "notes": None,
    "lessons": [],  # durable takeaways written by the reflection engine
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_user_memory(db, user_id: str) -> dict:
    """Return the user's AI memory document, seeded with defaults if absent."""
    doc = await db.ai_user_memory.find_one({"user_id": user_id})
    memory = dict(_DEFAULT_MEMORY)
    if doc:
        for k in list(_DEFAULT_MEMORY.keys()):
            if k in doc and doc[k] is not None:
                memory[k] = doc[k]
        memory["updated_at"] = doc.get("updated_at")
    memory["user_id"] = user_id
    return memory


async def update_user_memory(db, user_id: str, patch: dict) -> dict:
    """Upsert editable memory fields for a user and return the merged memory.

    Unknown keys are ignored so the endpoint can safely forward a whole body.
    """
    updates = {k: v for k, v in (patch or {}).items() if k in EDITABLE_FIELDS}
    updates["updated_at"] = _now()
    await db.ai_user_memory.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, **updates}},
        upsert=True,
    )
    return await get_user_memory(db, user_id)


async def add_lesson(db, user_id: str, lesson: str, *, max_lessons: int = 20) -> dict:
    """Append a durable lesson (read-modify-write so it works on FakeDB too)."""
    lesson = (lesson or "").strip()
    if not lesson:
        return await get_user_memory(db, user_id)
    memory = await get_user_memory(db, user_id)
    lessons = list(memory.get("lessons") or [])
    lessons.append({"lesson": lesson, "at": _now()})
    lessons = lessons[-max_lessons:]  # keep only the most recent N
    await db.ai_user_memory.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "lessons": lessons, "updated_at": _now()}},
        upsert=True,
    )
    return await get_user_memory(db, user_id)


def build_memory_context(user: dict, memory: dict) -> str:
    """Render a compact 'what I remember about you' block for the chat prompt.

    Only includes fields that carry signal so short-context models are not
    diluted. Returns an empty string when nothing is known yet.
    """
    lines: list[str] = []
    name = (user or {}).get("name")
    if name:
        lines.append(f"- Name: {name}")
    capital = (user or {}).get("capital")
    if capital:
        lines.append(f"- Investable capital: ₹{capital:,.0f}")
    risk = memory.get("risk_preference") or (user or {}).get("risk_level")
    if risk:
        lines.append(f"- Risk preference: {risk}")
    if memory.get("experience_level"):
        lines.append(f"- Experience level: {memory['experience_level']}")
    if memory.get("goals"):
        lines.append(f"- Goals: {memory['goals']}")
    if memory.get("preferred_sectors"):
        lines.append(f"- Preferred sectors: {', '.join(memory['preferred_sectors'])}")
    if memory.get("favorite_companies"):
        lines.append(f"- Favourite companies: {', '.join(memory['favorite_companies'])}")
    if memory.get("notes"):
        lines.append(f"- Notes: {memory['notes']}")
    lessons = memory.get("lessons") or []
    if lessons:
        recent = "; ".join(l.get("lesson", "") for l in lessons[-3:])
        lines.append(f"- Lessons learned together: {recent}")

    if not lines:
        return ""
    return "What you remember about this user:\n" + "\n".join(lines)


async def list_conversations(db, user_id: str, limit: int = 30) -> list[dict]:
    """Return the user's chat sessions, newest first, with a preview + counts.

    Sessions are derived from the flat `chat_messages` collection so this works
    with the data the platform already writes — no migration required.
    """
    messages = await db.chat_messages.find({"user_id": user_id}).sort("created_at", 1).to_list(2000)
    sessions: dict[str, dict] = {}
    for m in messages:
        sid = m.get("session_id") or "default"
        s = sessions.setdefault(
            sid,
            {"session_id": sid, "message_count": 0, "title": None,
             "last_message": None, "created_at": m.get("created_at"),
             "updated_at": m.get("created_at")},
        )
        s["message_count"] += 1
        s["updated_at"] = m.get("created_at")
        content = (m.get("content") or "").strip()
        if s["title"] is None and m.get("role") == "user" and content:
            s["title"] = content[:60] + ("…" if len(content) > 60 else "")
        if content:
            s["last_message"] = content[:80]
    ordered = sorted(
        sessions.values(),
        key=lambda s: s.get("updated_at") or "",
        reverse=True,
    )
    for s in ordered:
        if not s["title"]:
            s["title"] = "New conversation"
    return ordered[:limit]


async def delete_conversation(db, user_id: str, session_id: str) -> int:
    """Delete every message in a session owned by the user. Returns count."""
    res = await db.chat_messages.delete_many(
        {"user_id": user_id, "session_id": session_id}
    )
    return getattr(res, "modified_count", 0)

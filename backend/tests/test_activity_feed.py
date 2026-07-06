"""Tests for the AI Activity Feed: services/activity_logger.py (log_activity,
get_recent_activity, the module-level `activity_deque`) and GET /api/ai-activity.

Fully hermetic and in-process: `GET /api/ai-activity` touches no DB and no
external service, so it runs straight through TestClient(app) with no fixtures.
`activity_deque` is module-level state private to this pytest process (the
live dev server, if any, runs in a separate process with its own copy), so
mutating it here cannot affect other test files or the running app.
"""
import uuid

from fastapi.testclient import TestClient

from server import app
from services.activity_logger import activity_deque, log_activity, get_recent_activity

client = TestClient(app)


# ---------- GET /api/ai-activity ----------
def test_get_activity_feed_returns_list():
    # ARRANGE — ensure there is at least one entry to return
    log_activity("Test scan action", "scan", "done")

    # ACT
    response = client.get("/api/ai-activity")

    # ASSERT
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_activity_feed_entries_have_required_fields():
    # ARRANGE
    log_activity("Test alert action", "alert", "warning")

    # ACT
    response = client.get("/api/ai-activity")

    # ASSERT
    data = response.json()
    assert len(data) > 0
    for entry in data:
        for field in ("time", "action", "category", "status"):
            assert field in entry, f"missing '{field}' in activity entry: {entry}"


# ---------- log_activity() / activity_deque ----------
def test_log_activity_appends_to_deque():
    # ARRANGE
    unique_action = f"Unique test action {uuid.uuid4().hex[:8]}"

    # ACT
    log_activity(unique_action, "monitor", "done")

    # ASSERT — the just-logged entry is the newest one in the deque
    assert activity_deque[-1]["action"] == unique_action
    assert activity_deque[-1]["category"] == "monitor"
    assert activity_deque[-1]["status"] == "done"
    assert activity_deque[-1]["time"]  # non-empty HH:MM:SS string


def test_activity_feed_max_50_entries():
    # ARRANGE — the deque itself is capped at maxlen=50 (get_recent_activity()
    # only ever surfaces the newest 20 of those 50 via the API).
    marker = uuid.uuid4().hex[:8]
    first_action = f"oldest-{marker}"
    last_action = f"newest-{marker}"

    # ACT — push well past the 50-item cap
    log_activity(first_action, "scan", "done")
    for i in range(1, 59):
        log_activity(f"filler-{marker}-{i}", "scan", "done")
    log_activity(last_action, "scan", "done")

    # ASSERT
    assert len(activity_deque) == 50, "activity_deque must never exceed maxlen=50"
    actions = [e["action"] for e in activity_deque]
    assert last_action in actions, "most recent entry must be retained"
    assert first_action not in actions, "oldest entries must be evicted once full"


def test_get_recent_activity_returns_newest_first_capped_at_20():
    # ARRANGE
    marker = uuid.uuid4().hex[:8]
    for i in range(25):
        log_activity(f"seq-{marker}-{i}", "rank", "done")

    # ACT
    recent = get_recent_activity()

    # ASSERT
    assert len(recent) <= 20
    assert recent[0]["action"] == f"seq-{marker}-24"

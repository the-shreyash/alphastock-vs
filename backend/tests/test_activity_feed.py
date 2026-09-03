"""Tests for the AI Activity Feed: services/activity_logger.py and the three
endpoints that serve it (`GET /api/ai-activity`, `GET /api/ai/activity`,
`GET /api/market/activity-feed`).

D6.1 / S4 rewrote this module. The feed used to be ONE process-global deque
holding both market-wide work and per-account business (orders, portfolio sizes,
AI questions), served unauthenticated and broadcast to every socket. It is now
two scopes with different delivery, so these tests changed shape: the platform
behaviour they always asserted is unchanged and still asserted here, and the
scoping is asserted alongside it. The cross-user isolation proofs live in
`test_d61_security.py`.

Fully hermetic and in-process: these endpoints touch no DB and no external
service. Module state is private to this pytest process, and `reset_for_tests()`
keeps each test independent of the ones before it.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from server import app
from services.activity_logger import (
    activity_deque,
    get_recent_activity,
    log_activity,
    log_platform_activity,
    reset_for_tests,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_feed():
    reset_for_tests()
    yield
    reset_for_tests()


# ---------- GET /api/ai-activity (platform stream, readable signed out) -------
def test_get_activity_feed_returns_list():
    # ARRANGE — ensure there is at least one entry to return
    log_platform_activity("Test scan action", "scan", "done")

    # ACT
    response = client.get("/api/ai-activity")

    # ASSERT
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_activity_feed_entries_have_required_fields():
    # ARRANGE
    log_platform_activity("Test alert action", "alert", "warning")

    # ACT
    response = client.get("/api/ai-activity")

    # ASSERT
    data = response.json()
    assert len(data) > 0
    for entry in data:
        for field in ("time", "action", "category", "status"):
            assert field in entry, f"missing '{field}' in activity entry: {entry}"


def test_the_feed_stays_readable_signed_out():
    """The platform stream is a legitimate signed-out surface (the landing page
    shows it). D6.1 closed the leak by scoping the CONTENT, not by 401-ing the
    endpoint, and this asserts that distinction held."""
    log_platform_activity("Scanning News", "news", "running")
    for path in ("/api/ai-activity", "/api/ai/activity", "/api/market/activity-feed"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} became authenticated-only"
        assert any(e["action"] == "Scanning News" for e in resp.json()), path


# ---------- log_platform_activity() / activity_deque -------------------------
def test_log_platform_activity_appends_to_deque():
    # ARRANGE
    unique_action = f"Unique test action {uuid.uuid4().hex[:8]}"

    # ACT
    log_platform_activity(unique_action, "monitor", "done")

    # ASSERT — the just-logged entry is the newest one in the deque
    assert activity_deque[-1]["action"] == unique_action
    assert activity_deque[-1]["category"] == "monitor"
    assert activity_deque[-1]["status"] == "done"
    assert activity_deque[-1]["time"]  # non-empty HH:MM:SS string


def test_the_internal_sort_key_never_reaches_a_reader():
    """The two scopes are merged on a monotonic write sequence rather than the
    "HH:MM:SS" `time` string, which wraps at midnight and would put yesterday's
    23:59 entries above today's 00:01 ones. It is a sort key, not part of the
    feed contract, so it is stripped on the way out."""
    log_platform_activity("Scanning News", "news", "done")
    log_activity("A's order", "monitor", "done", user_id="user-a")

    for entry in get_recent_activity("user-a"):
        assert set(entry) == {"time", "action", "category", "status"}, entry

    resp = client.get("/api/ai-activity")
    assert all(set(e) == {"time", "action", "category", "status"} for e in resp.json())


def test_the_merged_feed_is_ordered_by_write_order_not_by_clock_string():
    log_platform_activity("first", "news", "done")
    log_activity("second", "monitor", "done", user_id="user-a")
    log_platform_activity("third", "news", "done")

    assert [e["action"] for e in get_recent_activity("user-a")] == \
        ["third", "second", "first"]


def test_activity_feed_max_50_entries():
    # ARRANGE — the deque itself is capped at maxlen=50 (get_recent_activity()
    # only ever surfaces the newest 20 of those 50 via the API).
    marker = uuid.uuid4().hex[:8]
    first_action = f"oldest-{marker}"
    last_action = f"newest-{marker}"

    # ACT — push well past the 50-item cap
    log_platform_activity(first_action, "scan", "done")
    for i in range(1, 59):
        log_platform_activity(f"filler-{marker}-{i}", "scan", "done")
    log_platform_activity(last_action, "scan", "done")

    # ASSERT
    assert len(activity_deque) == 50, "activity_deque must never exceed maxlen=50"
    actions = [e["action"] for e in activity_deque]
    assert last_action in actions, "most recent entry must be retained"
    assert first_action not in actions, "oldest entries must be evicted once full"


def test_get_recent_activity_returns_newest_first_capped_at_20():
    # ARRANGE
    marker = uuid.uuid4().hex[:8]
    for i in range(25):
        log_platform_activity(f"seq-{marker}-{i}", "rank", "done")

    # ACT
    recent = get_recent_activity()

    # ASSERT
    assert len(recent) <= 20
    assert recent[0]["action"] == f"seq-{marker}-24"


# ---------- scoping (D6.1 / S4) ----------------------------------------------
def test_private_entries_never_enter_the_platform_deque():
    log_activity("Order placed on Zerodha: BUY 10 RELIANCE", "monitor", "done",
                 user_id="user-a")
    assert list(activity_deque) == [], \
        "a private entry reached the deque that is broadcast to every socket"


def test_a_reader_sees_the_platform_stream_plus_only_their_own():
    log_platform_activity("Scanning News", "news", "done")
    log_activity("A's order", "monitor", "done", user_id="user-a")
    log_activity("B's order", "monitor", "done", user_id="user-b")

    actions_a = [e["action"] for e in get_recent_activity("user-a")]
    actions_b = [e["action"] for e in get_recent_activity("user-b")]
    anonymous = [e["action"] for e in get_recent_activity(None)]

    assert "Scanning News" in actions_a and "Scanning News" in actions_b
    assert "A's order" in actions_a and "A's order" not in actions_b
    assert "B's order" in actions_b and "B's order" not in actions_a
    assert anonymous == ["Scanning News"], \
        f"an anonymous reader saw private entries: {anonymous}"


def test_the_private_logger_cannot_be_called_without_an_owner():
    """The enforcement is the signature, not a convention (D6.1 / S4). A caller
    that does not know whose activity this is cannot reach this function at
    all — which is a stronger guarantee than any sweep or code review."""
    with pytest.raises(TypeError):
        log_activity("Order placed", "monitor", "done")  # noqa:

"""Shared fixtures for the hermetic (in-process) backend test files.

These fixtures back the 8 new feature test files (activity feed, morning
report, paper trading, chart patterns, setup stats, trade coaching,
backtesting, webhooks). They do NOT touch or alter the existing
`requests`-based integration test files (test_backend.py, test_phase*.py),
which hit the live dev server directly and are untouched.

Why in-process + a fake DB instead of `requests` against the live server:
Motor (the async Mongo driver) binds its client to the event loop that was
running when it was constructed. FastAPI's synchronous TestClient runs each
request on a fresh loop via `asyncio.run`, so a second DB-backed request
raises `RuntimeError: Event loop is closed`. Swapping `server.db` for an
in-memory `FakeDB` (tests/_fakedb.py) sidesteps that entirely, keeps tests
hermetic (no real Mongo needed), and matches testing.md rule 5 (no real
external services required to pass).
"""
import sys
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402
from server import app, create_access_token  # noqa: E402
from tests._fakedb import FakeDB  # noqa: E402


# --------------------------------------------------------------------------- #
# Suite selection (PH2.4)                                                       #
# --------------------------------------------------------------------------- #
# The backend has two kinds of test file living side by side:
#
#   * hermetic     — in-process TestClient + FakeDB. No server, no database, no
#                    network. These are the ones CI runs on every push and PR.
#   * live-server  — the older `requests`/`websockets` suites, which drive a
#                    running backend at $REACT_APP_BACKEND_URL and, through it,
#                    a real MongoDB and real market-data providers.
#
# CI needs to select the first set MECHANICALLY. Doing that with `--ignore`
# flags in the workflow file would put the list of live-server suites in YAML,
# far away from the tests themselves, where the next person to add one would
# never find it — and their suite would then run in CI, hit no server, and fail
# for a reason that looks nothing like the cause.
#
# So the marker is applied HERE, from the one fact that actually distinguishes
# them, and CI just says `-m "not integration"`. Adding a new live-server suite
# means adding its filename to this tuple; nothing in .github/ changes.
#
# PH3.1 owns converting these suites to hermetic equivalents. Until then they
# remain runnable on demand (`pytest -m integration` against a live stack).
_LIVE_SERVER_SUITES = frozenset({
    "test_backend.py",
    "test_phase2.py",
    "test_phase4.py",
    "test_phase5.py",
    "test_phase6.py",
    "test_phase7.py",
})


def pytest_collection_modifyitems(items):
    """Mark every test in a live-server suite as ``integration``."""
    integration = pytest.mark.integration
    for item in items:
        if Path(str(item.fspath)).name in _LIVE_SERVER_SUITES:
            item.add_marker(integration)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_db(monkeypatch):
    """Swap the module-level `server.db` for an in-memory FakeDB."""
    fdb = FakeDB()
    monkeypatch.setattr(server, "db", fdb)
    return fdb


@pytest.fixture
def test_user(fake_db):
    """A user document pre-seeded into the fake DB, for auth + ownership checks."""
    user_doc = {
        "_id": ObjectId(),
        "name": "Test User",
        "email": "test_user@example.com",
        "capital": 100000.0,
        "risk_level": "moderate",
        "role": "user",
    }
    fake_db.users.docs.append(user_doc)
    return user_doc


@pytest.fixture
def auth_headers(test_user):
    """A real JWT (minted via the app's own create_access_token) for test_user."""
    token = create_access_token(str(test_user["_id"]), test_user["email"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def no_ai(monkeypatch):
    """Force the AI-configured checks to False so routes take their deterministic
    fallback path instead of attempting a real Claude/Gemini network call."""
    monkeypatch.setattr(server, "claude_configured", lambda: False)
    monkeypatch.setattr(server, "gemini_configured", lambda: False)

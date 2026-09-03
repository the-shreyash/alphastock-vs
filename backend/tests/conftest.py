"""Shared fixtures and suite-wide isolation for the backend test suite.

Two jobs, in this order:

1. **Isolation (PH3.1).** Before `server` is imported, install a deterministic
   test environment so no test ever sees the developer's real `backend/.env`
   (`tests/_testenv.py`). Then, per test, block outbound network for anything
   not explicitly classified as needing it (`tests/_netguard.py`).

2. **Fixtures.** An in-process `TestClient`, an in-memory Mongo double, and a
   deterministic test user + JWT.

Why in-process + a fake DB instead of `requests` against the live server:
Motor (the async Mongo driver) binds its client to the event loop that was
running when it was constructed. FastAPI's synchronous TestClient runs each
request on a fresh loop via `asyncio.run`, so a second DB-backed request
raises `RuntimeError: Event loop is closed`. Swapping `server.db` for an
in-memory `FakeDB` (tests/_fakedb.py) sidesteps that entirely, keeps tests
hermetic (no real Mongo needed), and matches the rule that no real external
service is required to pass.
"""
import sys
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _netguard, _testenv  # noqa: E402

# MUST run before `import server`: server.py reads configuration — and calls
# load_dotenv(override=True) — at module scope, so by the time the import
# statement below returns it is too late to influence what it read.
_testenv.apply()

import server  # noqa: E402
from server import app, create_access_token  # noqa: E402
from services.broker_engine import broker_engine  # noqa: E402
from tests._fakedb import FakeDB  # noqa: E402


# --------------------------------------------------------------------------- #
# Suite classification                                                          #
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
# So the markers are applied HERE, from the one fact that actually
# distinguishes them, and CI just says `-m "not integration"`. Adding a new
# live-server suite means adding its filename to this tuple; nothing in
# .github/ changes.
#
# PH3.1 kept `integration` on these files even though `live` is the more
# accurate name, because `-m "not integration"` is the selection every existing
# GitHub Actions workflow already uses. Both markers are applied; the
# deselection contract is unchanged, and `-m live` now also addresses them.
_LIVE_SERVER_SUITES = frozenset({
    "test_backend_live.py",
    "test_phase2.py",
    "test_phase4.py",
    "test_phase5.py",
    "test_phase6.py",
    "test_phase7.py",
})

#: The PH1 security-control suites. Marked `security` mechanically for the same
#: reason the live suites are marked mechanically: a hand-applied decorator on
#: ~450 tests would be missing from some of them within a sprint, and
#: `pytest -m security` would then quietly under-report the regression surface.
#: These are all hermetic and all run in the default selection — the marker
#: exists so the security gate can be run *alone*, not to exclude it.
_SECURITY_SUITES = frozenset({
    "test_audit.py",
    # D6.1 — the S1..S7 regression matrix (ownership, isolation, fail-closed).
    "test_d61_security.py",
    "test_auth_hardening.py",
    "test_cookie_security.py",
    "test_cors_hardening.py",
    "test_csrf.py",
    "test_identifiers.py",
    "test_jwt_sessions.py",
    "test_oauth_hardening.py",
    "test_password_policy.py",
    "test_rate_limit.py",
    "test_recovery.py",
    "test_roles.py",
    "test_secret_loading.py",
    "test_secrets.py",
    "test_security_headers.py",
})

#: Markers that mean "this test is allowed to touch the outside world".
#: A test carrying any of them does not get the outbound-network guard.
#:
#: `allow_network` is the single-test escape hatch. It is a *marker* rather
#: than an opt-out fixture on purpose: a marker shows up in `--collect-only`
#: and can be selected against (`pytest -m allow_network` lists every test
#: that claims the exemption), whereas an unused fixture is invisible.
_NON_HERMETIC_MARKERS = frozenset({"integration", "live", "e2e", "allow_network"})


def pytest_collection_modifyitems(items):
    """Apply the file-level markers: `integration`+`live`, and `security`."""
    for item in items:
        filename = Path(str(item.fspath)).name
        if filename in _LIVE_SERVER_SUITES:
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.live)
        if filename in _SECURITY_SUITES:
            item.add_marker(pytest.mark.security)


@pytest.fixture(scope="session")
def _live_backend_reachable():
    """Probe the configured deployment once per session. Cached."""
    import urllib.error
    import urllib.request

    from tests._live import API

    try:
        with urllib.request.urlopen(f"{API}", timeout=5) as resp:
            return 200 <= resp.status < 500
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(autouse=True)
def _require_live_server(request, _live_backend_reachable):
    """Skip `live` tests when there is no deployment to drive.

    WHY SKIP RATHER THAN FAIL
    -------------------------
    A live-server test with no live server is not a failing test — it is an
    unrun one, and reporting it as a failure is how a suite ends up with 43
    permanently-red results that everyone learns to scroll past. This is
    PH3.1 §6 classification C ("test requires external infrastructure"),
    applied honestly.

    It also makes marker selection composable. `-m` is single-valued, so
    `pytest -m "not slow"` REPLACES the `-m "not integration"` default in
    `pyproject.toml` and pulls the live suites back into the run. Before this
    fixture that produced 43 connection errors; now it produces skips with a
    readable reason.

    WHY IT CANNOT HIDE A BROKEN DEPLOYMENT
    --------------------------------------
    Set ``REQUIRE_LIVE_BACKEND=1`` and an unreachable deployment becomes a hard
    failure instead. The PH2.6 integration job MUST set it — otherwise a stack
    that failed to boot would skip its way to a green tick, which is a worse
    outcome than the red one this fixture is replacing.
    """
    import os

    if not request.node.get_closest_marker("live"):
        return
    if _live_backend_reachable:
        return

    from tests._live import BASE_URL

    message = f"No backend reachable at {BASE_URL} — live-server test not run."
    if os.environ.get("REQUIRE_LIVE_BACKEND") == "1":
        pytest.fail(f"{message} REQUIRE_LIVE_BACKEND=1 forbids skipping it.")
    pytest.skip(message)


@pytest.fixture(autouse=True)
def _hermetic_network(request, monkeypatch):
    """Block outbound network for every test not classified non-hermetic.

    Autouse and unconditional by design. An opt-in guard only guards the tests
    someone remembered to opt in, which is never the one that regresses. The
    escape hatch is a *marker* on the test, which is visible in the collection
    listing and in the CI selection — unlike a fixture that quietly isn't used.
    """
    if _NON_HERMETIC_MARKERS.intersection(m.name for m in request.node.iter_markers()):
        return
    _netguard.install(monkeypatch)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_db(monkeypatch):
    """Swap the module-level `server.db` for an in-memory FakeDB.

    PH3.3: `server.db` is not the only handle. `services.broker_engine` captures
    its own reference at startup (`broker_engine.db`), and patching only
    `server.db` left every `/api/brokers/*` and `/api/zerodha/*` route talking
    to the **real** Motor client. That did not fail loudly — it failed as
    `RuntimeError: Event loop is closed`, thrown from deep inside Motor on the
    second DB-backed request of the session, which reads like an async bug in
    the application and is really an unpatched dependency. Every broker route
    was unreachable from a hermetic test until this was found.

    Any future component that caches its own handle belongs in this list, and
    the symptom to recognise is that same RuntimeError.
    """
    fdb = FakeDB()
    monkeypatch.setattr(server, "db", fdb)
    monkeypatch.setattr(broker_engine, "db", fdb)
    return fdb


def _seed_user(fake_db, email, role, name):
    """Insert a synthetic user document and return it.

    Shared by every principal fixture below so the four roles differ in exactly
    one field (`role`) and nothing else — an authorization test that passes for
    the wrong reason (different capital, different name) is not possible.
    """
    doc = {
        "_id": ObjectId(),
        "name": name,
        "email": email,
        "capital": 100000.0,
        "risk_level": "moderate",
        "role": role,
    }
    fake_db.users.docs.append(doc)
    return doc


def _headers_for(user_doc):
    """A real JWT (minted via the app's own `create_access_token`) for a user.

    Minting through the application's own function rather than hand-rolling a
    token is deliberate: a test that forges its own JWT stops testing the
    issuer, and would keep passing after the issuer started emitting something
    the verifier rejects.
    """
    token = create_access_token(str(user_doc["_id"]), user_doc["email"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_user(fake_db):
    """A user document pre-seeded into the fake DB, for auth + ownership checks."""
    return _seed_user(fake_db, "test_user@example.com", "user", "Test User")


@pytest.fixture
def auth_headers(test_user):
    """A real JWT (minted via the app's own create_access_token) for test_user."""
    return _headers_for(test_user)


# --------------------------------------------------------------------------- #
# Additional principals (PH3.3)                                                 #
# --------------------------------------------------------------------------- #
# Authorization has four distinct answers in this system — anonymous, owner,
# admin, super_admin — and "another ordinary user" is the principal that proves
# *horizontal* isolation. Each is a first-class fixture so a test names the
# principal it is exercising rather than assembling one inline, where the role
# string is easy to get subtly wrong (e.g. "superadmin") and the test then
# passes by falling into the 403 branch it meant to escape.

@pytest.fixture
def other_user(fake_db):
    """A second ordinary user — the victim in horizontal-escalation tests."""
    return _seed_user(fake_db, "other_user@example.com", "user", "Other User")


@pytest.fixture
def other_headers(other_user):
    return _headers_for(other_user)


@pytest.fixture
def admin_user(fake_db):
    return _seed_user(fake_db, "test_admin@example.com", "admin", "Test Admin")


@pytest.fixture
def admin_headers(admin_user):
    return _headers_for(admin_user)


@pytest.fixture
def super_admin_user(fake_db):
    return _seed_user(fake_db, "test_super@example.com", "super_admin", "Test Super Admin")


@pytest.fixture
def super_admin_headers(super_admin_user):
    return _headers_for(super_admin_user)


@pytest.fixture
def authenticated_client(fake_db, auth_headers):
    """`TestClient` that sends `test_user`'s bearer token on every request.

    Saves threading `headers=` through every call in ownership-heavy suites.
    Where a test's *point* is the presence or absence of credentials, use the
    plain `client` fixture and pass headers explicitly — an implicit credential
    is exactly the thing those tests must not have.
    """
    return TestClient(app, headers=auth_headers)


@pytest.fixture
def admin_client(fake_db, admin_headers):
    return TestClient(app, headers=admin_headers)


@pytest.fixture
def super_admin_client(fake_db, super_admin_headers):
    return TestClient(app, headers=super_admin_headers)


@pytest.fixture
def no_ai(monkeypatch):
    """Force the AI-configured checks to False so routes take their deterministic
    fallback path instead of attempting a real Claude/Gemini network call."""
    monkeypatch.setattr(server, "claude_configured", lambda: False)
    monkeypatch.setattr(server, "gemini_configured", lambda: False)

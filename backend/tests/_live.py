"""Shared configuration for the live-server (`integration` / `live`) suites.

WHY THIS FILE EXISTS
--------------------
The six `requests`-based suites each rediscovered the same two facts — where
the deployment is, and how to authenticate as an administrator — and each got
them wrong in its own way:

* **Hardcoded credentials.** ``ADMIN_EMAIL = "admin@alphapartner.com"`` and
  ``ADMIN_PASSWORD = "admin123"`` were literals in five of the six files. A
  credential pair in version control is a credential pair in version control
  whether or not it currently works somewhere, and `admin123` against a real
  deployment is not a test, it is an incident. PH3.1 §12 requires them gone.
* **Developer-machine discovery.** `test_phase4.py` and `test_phase7.py`
  searched for `/app/frontend/.env` and then walked up the source tree looking
  for a frontend `.env` to scrape `REACT_APP_BACKEND_URL` out of. That makes
  the target of the test a property of the machine it runs on.

Now there is one source of truth, it is explicit, and a missing value produces
a **skip with a readable reason** rather than a `ConnectionError` traceback or,
worse, a silent run against the wrong host.

CONFIGURATION
-------------
``REACT_APP_BACKEND_URL``  base URL of the running deployment
                           (default ``http://localhost:8000`` — a developer
                           running the stack locally is the common case, and
                           localhost cannot be mistaken for production).
``TEST_ADMIN_EMAIL``       administrator login for the target deployment.
``TEST_ADMIN_PASSWORD``    its password.

Both credential variables are **required and have no default**. Tests that need
them call :func:`require_admin_credentials`, which skips when they are absent.

These suites are excluded from the default `pytest` run (`-m "not integration"`
in `pyproject.toml`). Run them with ``pytest -m integration`` — or ``-m live``
— against a stack you own.
"""
import os

import pytest

#: Base URL of the deployment under test. No filesystem discovery: the target
#: of an integration run must be something the operator stated, not something
#: the test inferred from whatever `.env` happened to be lying around.
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8000").rstrip("/")

#: Conventional `/api` prefix, precomputed because every suite wants it.
API = f"{BASE_URL}/api"

#: WebSocket origin derived from the same base URL.
WS_URL = BASE_URL.replace("https://", "wss://").replace("http://", "ws://") + "/api/ws"

ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "").strip()
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "").strip()

_MISSING_CREDS = (
    "Live-server admin tests need TEST_ADMIN_EMAIL and TEST_ADMIN_PASSWORD to be "
    "set for the deployment at "
)


def require_admin_credentials():
    """Skip the calling test unless admin credentials were supplied.

    ``REQUIRE_LIVE_BACKEND=1`` turns the skip into a failure — the same switch
    `conftest.py` uses for deployment reachability, and for the same reason: a
    CI integration job that skips 49 tests because nobody wired its secrets is
    a job reporting success for work it did not do.
    """
    if ADMIN_EMAIL and ADMIN_PASSWORD:
        return
    message = f"{_MISSING_CREDS}{BASE_URL}. See tests/_live.py."
    if os.environ.get("REQUIRE_LIVE_BACKEND") == "1":
        pytest.fail(f"{message} REQUIRE_LIVE_BACKEND=1 forbids skipping it.")
    pytest.skip(message)


def admin_login(session_or_requests, timeout=30):
    """Authenticate as the administrator; skip (never fail) on a rate limit.

    A 429 means the deployment's PH1 rate limiter is doing its job. Failing the
    suite for that would train people to ignore red integration runs.
    """
    require_admin_credentials()
    resp = session_or_requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=timeout,
    )
    if resp.status_code == 429:
        pytest.skip("Rate-limited by the deployment; rerun in ~15 minutes.")
    assert resp.status_code == 200, f"admin login failed: {resp.status_code} {resp.text}"
    return resp.json()

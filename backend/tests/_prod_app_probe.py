"""Boot the REAL application under `APP_ENV=production` and report what its
documentation routes answer (PH3.12R / B-2).

WHY A SUBPROCESS
----------------
`server.py` reads its configuration, validates its secrets and constructs
`app` at *import* time, and `tests/conftest.py` has already imported it as
`testing` before any test runs. `APP_ENV` therefore cannot be changed in-process
after the fact: `monkeypatch.setenv` moves the variable but not the application
object that was built from it, and re-importing the module inside a live pytest
process would rebuild half the backend's global state underneath every other
suite.

So the probe runs in a clean interpreter. What it buys is the only assertion
that actually answers the certification question: not "would a correctly
configured FastAPI hide its docs" but **"does the application this repository
ships hide them when it is booted as production."** PH3.11 answered the first
kind of question (against a path that did not exist) and certified B-2 closed
while it was open.

HOW IT STAYS HERMETIC
---------------------
It starts from `tests/_testenv.apply()` — the same synthetic environment the
rest of the suite uses, so no real credential is present — then overlays the
three values production validation insists on:

* `CORS_ALLOWED_ORIGINS` — required in production, and an obviously fake host.
* an AI provider key — a syntactically valid, self-evidently fake string;
  nothing calls it, `DISABLE_BACKGROUND_ENGINE=1` keeps the engine from
  starting, and no request in this probe reaches an AI route.
* `MONGO_URL` with credentials on **loopback** — production validation rejects
  an unauthenticated database. Loopback rather than a hostname is deliberate:
  a hostname would make the process do a DNS lookup, which is an outbound
  network call in a suite that forbids them.

Only `/docs`, `/redoc` and `/openapi.json` are requested. They are matched (or
in production, not matched) before any `/api`-scoped middleware, so nothing in
the probe touches the database, and it finishes in a few seconds without a
Mongo, a Redis or a network.

Run standalone with `python -m tests._prod_app_probe`; it prints a single JSON
object on stdout and nothing else that matters.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: The real, framework-default documentation routes. Written as literals, never
#: imported from `security.api_docs` — a probe that derives the path it requests
#: from the value under test agrees with any bug in that value, which is exactly
#: how `/api/docs` came to be certified.
PROBED_PATHS = ("/docs", "/redoc", "/openapi.json")

#: Overlaid on `tests/_testenv`'s synthetic environment to satisfy production
#: config validation. See the module docstring for why each one is shaped
#: this way.
PRODUCTION_OVERLAY = {
    "APP_ENV": "production",
    "CORS_ALLOWED_ORIGINS": "https://app.example.invalid",
    "ANTHROPIC_API_KEY": "sk-ant-probe-not-a-real-key-0000000000000000",
    "MONGO_URL": "mongodb://probe_user:probe_password@127.0.0.1:27017",
    "DISABLE_BACKGROUND_ENGINE": "1",
    # Quiet the boot: this process prints exactly one line that matters.
    "LOG_LEVEL": "CRITICAL",
}


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from tests import _testenv

    _testenv.apply()
    os.environ.update(PRODUCTION_OVERLAY)

    import logging

    import server
    from fastapi.testclient import TestClient

    logging.disable(logging.CRITICAL)

    client = TestClient(server.app)
    print("PROBE_RESULT " + json.dumps({
        "environment": os.environ["APP_ENV"],
        "docs_url": server.app.docs_url,
        "redoc_url": server.app.redoc_url,
        "openapi_url": server.app.openapi_url,
        # `app.openapi()` must keep working even with `openapi_url=None`: the
        # schema is unpublished, not ungenerable. Deployment tooling and the
        # route-inventory sweeps depend on it.
        "schema_generable": "/api/paper/trade" in server.app.openapi()["paths"],
        "statuses": {p: client.get(p).status_code for p in PROBED_PATHS},
        "bodies_mention_openapi": {
            p: "openapi" in client.get(p).text.lower() for p in PROBED_PATHS
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

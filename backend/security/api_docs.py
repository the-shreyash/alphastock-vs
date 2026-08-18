"""Environment-aware exposure policy for the interactive API documentation
(PH3.12R / B-2).

WHAT THIS CLOSES
----------------
`server.py` created its application as `FastAPI(title="AlphaPartner API")` —
every documentation URL left at its framework default. In production that
published, to anonymous callers:

    GET /docs          200  Swagger UI
    GET /redoc         200  ReDoc
    GET /openapi.json  200  121 KB — 188 paths, 23 admin routes, 26 schemas

No credential check was missing (all 23 admin paths still answered 401), and no
secret value appears in a generated schema. What leaked was the *map*: every
route, every parameter name, every request/response shape, handed to an
unauthenticated attacker as a machine-readable file. That contradicts a posture
this same codebase enforces everywhere else — the `Server` header is suppressed,
the CSP is `default-src 'none'`, unknown paths answer 404 — and PH3.11 had
already certified it closed.

WHY PH3.11 GOT IT WRONG, AND WHAT THIS MODULE DOES ABOUT IT
-----------------------------------------------------------
PH3.11 probed `/api/docs`, observed 404, and recorded the control as verified.
`/api/docs` was never a route this application served: the 404 was the generic
unknown-path handler answering a question nobody had asked. The probe could not
have failed, so it certified nothing.

The lesson is encoded here structurally rather than in a comment. The policy is
one pure function of the environment, so a test can assert the *production*
answer without a production deployment; and `server.app` is constructed from
that function's output, so the accompanying tests can assert that the running
application's real `docs_url` / `redoc_url` / `openapi_url` are the values this
module chose. A future edit that hardcodes them back turns those tests red.

WHY ALL THREE, NOT JUST SWAGGER
-------------------------------
Setting `docs_url=None` alone removes the human-facing page and leaves
`/openapi.json` serving the entire schema — the half that actually matters to an
attacker, since it is the machine-readable one. The three settings are returned
together by a single function precisely so they cannot be disabled apart.

POLICY
------
    development / testing / staging   docs, redoc and openapi.json available
    production                        all three unrouted (404)

The environment is read through `security.secrets.app_env()` — the one existing
primitive — so documentation exposure can never disagree with the cookie policy
or the diagnostics endpoint about which environment this is.

THE OVERRIDE ONLY EVER TIGHTENS
-------------------------------
`API_DOCS_ENABLED=false` suppresses the documentation outside production, for a
staging environment that wants a production-shaped surface. There is
deliberately **no** override in the other direction: production is forced off
regardless of what the environment says, mirroring `security.cookies.
cookie_secure()`, which forces `Secure` on in production for the same reason. An
enable-flag would mean one mistyped variable re-opens exactly the hole this
module exists to close — and it would be discovered the same way B-2 was.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

from security.secrets import PRODUCTION, app_env

#: FastAPI's own defaults, restated rather than imported, so that "what we serve
#: in development" is a decision this module owns and a test can name.
DEFAULT_DOCS_URL = "/docs"
DEFAULT_REDOC_URL = "/redoc"
DEFAULT_OPENAPI_URL = "/openapi.json"

#: Non-production opt-out. Absent or unparseable means "enabled".
DOCS_TOGGLE_VAR = "API_DOCS_ENABLED"

_FALSEY = frozenset({"0", "false", "no", "off"})


def docs_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the interactive documentation should be routed at all.

    `environ is None` means "read the process environment"; an empty mapping is
    a legitimate argument meaning "an environment in which nothing is set" —
    the same distinction `security.secrets.app_env` draws, and for the same
    reason (a caller asking what a bare configuration resolves to must not be
    answered with the host's real one).
    """
    env = os.environ if environ is None else environ

    if app_env(env) == PRODUCTION:
        return False

    raw = env.get(DOCS_TOGGLE_VAR)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSEY


def docs_kwargs(environ: Optional[Mapping[str, str]] = None) -> dict:
    """Keyword arguments for `FastAPI(...)` implementing the policy above.

    Returned as one dict, and consumed as `FastAPI(**docs_kwargs())`, so the
    three URLs are switched as a unit. `None` tells FastAPI not to register the
    route at all, which is why a disabled path answers 404 (the unknown-path
    response) rather than 403 — an attacker learns nothing from the difference
    between "no documentation here" and "no such endpoint".
    """
    if not docs_enabled(environ):
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {
        "docs_url": DEFAULT_DOCS_URL,
        "redoc_url": DEFAULT_REDOC_URL,
        "openapi_url": DEFAULT_OPENAPI_URL,
    }

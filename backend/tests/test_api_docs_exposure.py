"""B-2 regression: the interactive API documentation must not be reachable in
production (PH3.12R).

THE DEFECT
----------
`server.py` built its application as `FastAPI(title="AlphaPartner API")`, so
every documentation URL kept its framework default. Against the production
image PH3.12 measured, anonymously:

    GET /docs          200   Swagger UI
    GET /redoc         200   ReDoc
    GET /openapi.json  200   121 KB — 188 paths, 23 admin routes, 26 schemas

WHY PH3.11 CERTIFIED THIS CLOSED WHEN IT WAS OPEN
--------------------------------------------------
PH3.11 probed **`/api/docs`**, saw 404, and recorded the control as verified.
`/api/docs` was never a route this application served — the 404 came from the
generic unknown-path handler. The probe could not have failed, so it certified
nothing, and the real paths went unmeasured for two sprints.

That failure mode dictates the shape of this file:

* **The real paths, spelled out as literals.** `/docs`, `/redoc`,
  `/openapi.json` — never `/api/docs`, and never derived from the same constant
  the production code reads, because a test that computes the path it probes
  from the value under test agrees with any bug in that value.
* **Both environments are asserted, not just production.** A policy that
  disabled the documentation everywhere would satisfy a production-only test
  while breaking every developer, so `development` is pinned to 200 with the
  same literals.
* **The shipped application is booted as production and measured.** The
  environment-parametrised tests build their own `FastAPI(**docs_kwargs(env))`,
  which proves the *policy* routes correctly but says nothing about whether
  `server.app` uses it — and under the suite's own `APP_ENV=testing` a
  regressed `FastAPI(title=...)` would be indistinguishable from the fix,
  because both serve the docs there. So `TestTheShippedApplicationInProduction`
  boots the real `server` module in a clean interpreter with `APP_ENV=production`
  (`tests/_prod_app_probe.py`) and asserts the real routes answer 404. That is
  the assertion PH3.12 made by hand against a container, reproduced hermetically
  and permanently.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from security.api_docs import DOCS_TOGGLE_VAR, docs_enabled, docs_kwargs

#: The routes FastAPI actually serves. Written out here as literals on purpose —
#: see the module docstring. `/api/docs` is NOT one of them and never was.
DOCS_PATH = "/docs"
REDOC_PATH = "/redoc"
OPENAPI_PATH = "/openapi.json"
ALL_DOC_PATHS = (DOCS_PATH, REDOC_PATH, OPENAPI_PATH)

#: The path PH3.11 mistakenly probed. Kept as a named constant so the test that
#: proves it is worthless cannot be mistaken for a test of the real surface.
PH311_MISTAKEN_PATH = "/api/docs"

NON_PRODUCTION_ENVS = ("development", "testing", "staging")


def _app_for(env, **extra):
    """A FastAPI instance configured by the real policy for `env`."""
    environ = {"APP_ENV": env, **extra}
    app = FastAPI(title="policy probe", **docs_kwargs(environ))
    app.get("/api/health")(lambda: {"ok": True})
    return app


# --------------------------------------------------------------------------- #
# The policy function                                                           #
# --------------------------------------------------------------------------- #
class TestPolicy:

    def test_production_disables_all_three(self):
        assert docs_kwargs({"APP_ENV": "production"}) == {
            "docs_url": None, "redoc_url": None, "openapi_url": None,
        }

    @pytest.mark.parametrize("env", NON_PRODUCTION_ENVS)
    def test_non_production_enables_all_three(self, env):
        assert docs_kwargs({"APP_ENV": env}) == {
            "docs_url": DOCS_PATH,
            "redoc_url": REDOC_PATH,
            "openapi_url": OPENAPI_PATH,
        }

    def test_openapi_is_never_left_on_when_swagger_is_off(self):
        """The specific half-fix this policy is designed to make impossible:
        hiding the Swagger page while `/openapi.json` keeps serving the entire
        schema — the half that actually matters to an attacker, because it is
        the machine-readable one."""
        for env in ("production", *NON_PRODUCTION_ENVS):
            resolved = docs_kwargs({"APP_ENV": env})
            enabled = {key for key, value in resolved.items() if value is not None}
            assert enabled in ({}, set(), set(resolved)), (
                f"{env}: documentation partially enabled — {resolved}"
            )

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", ""])
    def test_no_environment_variable_can_re_enable_docs_in_production(self, value):
        """There is deliberately no enable-flag. One mistyped variable must not
        be able to reopen the hole this module exists to close — which is
        exactly how it would be rediscovered."""
        assert docs_enabled({"APP_ENV": "production", DOCS_TOGGLE_VAR: value}) is False
        assert docs_kwargs({"APP_ENV": "production", DOCS_TOGGLE_VAR: value}) == {
            "docs_url": None, "redoc_url": None, "openapi_url": None,
        }

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE"])
    def test_the_override_can_tighten_outside_production(self, value):
        assert docs_enabled({"APP_ENV": "staging", DOCS_TOGGLE_VAR: value}) is False

    def test_an_unknown_app_env_is_not_treated_as_production(self):
        """`app_env()` resolves an unrecognised value to `development`. Pinned
        so the failure direction is known rather than assumed: a typo'd
        `APP_ENV` gets documentation, not a silently-hardened surface that
        hides the misconfiguration."""
        assert docs_enabled({"APP_ENV": "prodction"}) is True

    def test_an_empty_environment_does_not_fall_back_to_the_host(self):
        """An empty mapping means "nothing is set", not "read os.environ" —
        the same distinction `security.secrets.app_env` draws."""
        assert docs_enabled({}) is True


# --------------------------------------------------------------------------- #
# The real routes, under each environment                                       #
# --------------------------------------------------------------------------- #
class TestProductionRoutes:
    """`APP_ENV=production` → /docs, /redoc and /openapi.json are unrouted."""

    @pytest.mark.parametrize("path", ALL_DOC_PATHS)
    def test_documentation_path_is_404(self, path):
        response = TestClient(_app_for("production")).get(path)

        assert response.status_code == 404, (
            f"{path} answered {response.status_code} in production"
        )

    def test_the_schema_is_not_served_under_any_of_them(self):
        client = TestClient(_app_for("production"))

        for path in ALL_DOC_PATHS:
            body = client.get(path).text
            assert "openapi" not in body.lower()
            assert "swagger" not in body.lower()

    def test_normal_routes_still_work(self):
        """Disabling documentation must not disturb the API itself."""
        assert TestClient(_app_for("production")).get("/api/health").status_code == 200

    def test_the_schema_is_still_generable_in_process(self):
        """`app.openapi()` keeps working with `openapi_url=None` — the schema is
        merely not *published*. Deployment tooling and the route-inventory
        sweeps call it directly, and they must not be broken by this control."""
        schema = _app_for("production").openapi()

        assert "paths" in schema and "/api/health" in schema["paths"]


class TestDevelopmentRoutes:
    """`APP_ENV=development` → all three are available, unchanged."""

    @pytest.mark.parametrize("path", ALL_DOC_PATHS)
    def test_documentation_path_is_200(self, path):
        response = TestClient(_app_for("development")).get(path)

        assert response.status_code == 200, (
            f"{path} answered {response.status_code} in development"
        )

    def test_openapi_json_is_a_real_schema(self):
        schema = TestClient(_app_for("development")).get(OPENAPI_PATH).json()

        assert schema["openapi"].startswith("3.")
        assert "/api/health" in schema["paths"]

    @pytest.mark.parametrize("env", NON_PRODUCTION_ENVS)
    def test_every_non_production_environment_serves_all_three(self, env):
        client = TestClient(_app_for(env))

        assert [client.get(p).status_code for p in ALL_DOC_PATHS] == [200, 200, 200]


class TestTheSwitchIsDeterministic:

    def test_flipping_only_app_env_flips_every_path(self):
        """One variable, three paths, both directions — asserted in a single
        test so the production and development claims cannot drift apart."""
        prod = TestClient(_app_for("production"))
        dev = TestClient(_app_for("development"))

        assert [prod.get(p).status_code for p in ALL_DOC_PATHS] == [404, 404, 404]
        assert [dev.get(p).status_code for p in ALL_DOC_PATHS] == [200, 200, 200]

    def test_the_ph311_path_proves_nothing(self):
        """`/api/docs` answers 404 in BOTH environments — it is not a route
        either way. This is the assertion that documents why PH3.11's evidence
        was empty, and it fails if anyone ever "fixes" B-2 by adding that path
        back and testing it instead of the real ones."""
        prod = TestClient(_app_for("production")).get(PH311_MISTAKEN_PATH)
        dev = TestClient(_app_for("development")).get(PH311_MISTAKEN_PATH)

        assert prod.status_code == 404
        assert dev.status_code == 404, (
            "/api/docs is not a documentation route; a test using it cannot "
            "distinguish a hardened deployment from an exposed one"
        )


# --------------------------------------------------------------------------- #
# The application that actually runs                                            #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def production_probe():
    """Boot `server` in a clean interpreter as production; return its report.

    Module-scoped: the subprocess pays a full application import (~0.7s), and
    every assertion below reads the same one report.
    """
    backend_dir = Path(__file__).resolve().parent.parent
    completed = subprocess.run(
        [sys.executable, "-m", "tests._prod_app_probe"],
        cwd=backend_dir, capture_output=True, text=True, timeout=180,
    )
    marker = "PROBE_RESULT "
    line = next(
        (ln for ln in completed.stdout.splitlines() if ln.startswith(marker)), None
    )
    assert line is not None, (
        "the production probe did not report.\n"
        f"exit={completed.returncode}\nstdout:\n{completed.stdout[-3000:]}\n"
        f"stderr:\n{completed.stderr[-3000:]}"
    )
    return json.loads(line[len(marker):])


class TestTheShippedApplicationInProduction:
    """The real `server` module, booted as production, measured on the real
    paths. Everything above this point tests the policy; this tests the
    application — and it is the only class here that would have caught B-2."""

    def test_it_really_booted_as_production(self, production_probe):
        """Guard against the probe passing because the subprocess quietly fell
        back to a development environment — which would make every assertion
        below vacuous in the same way `/api/docs` was."""
        assert production_probe["environment"] == "production"

    @pytest.mark.parametrize("path", ALL_DOC_PATHS)
    def test_documentation_route_is_404(self, production_probe, path):
        assert production_probe["statuses"][path] == 404, (
            f"{path} answered {production_probe['statuses'][path]} from the "
            f"shipped application booted as production"
        )

    def test_no_documentation_response_leaks_the_schema(self, production_probe):
        assert production_probe["bodies_mention_openapi"] == {
            path: False for path in ALL_DOC_PATHS
        }

    def test_all_three_urls_are_unset_on_the_application_object(self,
                                                               production_probe):
        assert production_probe["docs_url"] is None
        assert production_probe["redoc_url"] is None
        assert production_probe["openapi_url"] is None

    def test_the_schema_is_still_generable_in_process(self, production_probe):
        """Unpublished, not ungenerable: `app.openapi()` must keep working so
        deployment tooling and the route-inventory sweeps are unaffected."""
        assert production_probe["schema_generable"] is True


class TestTheRealApplication:
    """The same application under the suite's own environment (`testing`),
    where the documentation must remain available."""

    def test_the_app_is_configured_from_the_policy(self):
        import server

        expected = docs_kwargs()
        assert server.app.docs_url == expected["docs_url"]
        assert server.app.redoc_url == expected["redoc_url"]
        assert server.app.openapi_url == expected["openapi_url"]

    @pytest.mark.parametrize("path", ALL_DOC_PATHS)
    def test_documentation_is_available_under_the_test_environment(self, client,
                                                                   path):
        """The suite runs as `APP_ENV=testing` (`tests/_testenv.py`), which is
        non-production — so the REAL application must serve all three. This is
        the developer-experience half of the contract, measured on the real
        route table rather than a constructed one."""
        assert client.get(path).status_code == 200

    def test_the_real_schema_still_describes_the_real_api(self, client):
        schema = client.get(OPENAPI_PATH).json()

        assert "/api/paper/trade" in schema["paths"]

    def test_documentation_needs_no_credentials_outside_production(self, client):
        """No `Authorization` header. Gating docs behind auth is NOT the chosen
        policy — they are simply absent in production — and this pins that so
        the two approaches are not silently mixed."""
        assert client.get(DOCS_PATH).status_code == 200


# --------------------------------------------------------------------------- #
# Nothing else moved                                                            #
# --------------------------------------------------------------------------- #
class TestNoCollateralDamage:
    """B-2's fix touches the application constructor, which every route,
    middleware and dependency hangs off. These are the load-bearing behaviours
    that would break if it had been done wrong."""

    def test_health_endpoints_still_answer(self, client):
        assert client.get("/api/health/live").status_code == 200

    def test_authentication_still_rejects_anonymous_callers(self, client):
        assert client.get("/api/paper/balance").status_code == 401

    def test_authenticated_routes_still_work(self, client, auth_headers, fake_db):
        assert client.get("/api/paper/balance", headers=auth_headers).status_code == 200

    def test_admin_authorization_still_applies(self, client, auth_headers, fake_db):
        assert client.get("/api/admin/users",
                          headers=auth_headers).status_code == 403

    def test_security_headers_are_still_applied(self, client):
        headers = client.get("/api/health/live").headers

        assert "content-security-policy" in headers
        assert headers.get("x-content-type-options") == "nosniff"

    def test_unknown_paths_still_404(self, client):
        assert client.get("/api/definitely-not-a-route").status_code == 404

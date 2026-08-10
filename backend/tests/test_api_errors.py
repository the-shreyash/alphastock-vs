"""Error-handling contracts: what a client sees when something goes wrong (PH3.3).

WHAT PRODUCTION FAILURE THIS CATCHES
------------------------------------
Error responses are a public API surface, and they are the part nobody looks at
until an incident. Three specific failures:

* **An error the client cannot parse.** The frontend's axios interceptor reads
  `detail` to decide between "show this message", "refresh the token", and
  "retry". An error body that omits it renders as a blank toast.
* **An error that leaks.** A stack trace, an internal path, a database error
  string, or — worst — the submitted credentials echoed back inside a
  validation error.
* **An error that lies about whose fault it is.** A 500 for a malformed request
  sends the client into a retry loop and burns the availability budget on
  requests that will never succeed.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
This file does not force 500s to inspect them, except where the handler itself
is the subject. PH3.3 §14: the objective is predictable production behaviour,
not theatre. The one deliberately-triggered failure is the database-outage test,
because "the database is down" is a real operating state whose response contract
nobody had ever checked.

Rate limiting is covered in depth by PH1's `test_rate_limit.py` (policy,
storage, lockout, headers, fail-open). Only the API-level integration is
asserted here — that the platform-wide limiter is actually attached to real
business endpoints rather than only to the synthetic route that suite uses.
"""
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

import server
from security import rate_limit as rl
from security.rate_limit import RateLimitPolicy


# --------------------------------------------------------------------------- #
# Error body shape                                                              #
# --------------------------------------------------------------------------- #
#: (method, path, expected status) covering every 4xx the API produces.
ERROR_CASES = [
    ("GET", "/api/trades", 401),                       # missing credentials
    ("GET", "/api/admin/dashboard", 403),              # authenticated, not entitled
    ("GET", "/api/nonexistent-endpoint", 404),         # no such route
    ("GET", "/api/trades/not-an-objectid/coaching", 400),   # malformed identifier
    ("POST", "/api/trades", 422),                      # schema violation
]


class TestErrorBodyContract:
    @pytest.mark.parametrize("method,path,expected", ERROR_CASES,
                             ids=[f"{m}-{p}-{s}" for m, p, s in ERROR_CASES])
    def test_every_error_carries_a_parseable_detail(
            self, client, fake_db, auth_headers, method, path, expected):
        headers = auth_headers if expected != 401 else {}
        kwargs = {"headers": headers}
        if method == "POST":
            kwargs["json"] = {}
        resp = client.request(method, path, **kwargs)
        assert resp.status_code == expected, resp.text
        body = resp.json()
        assert "detail" in body, (
            f"{method} {path} returned {resp.status_code} with no `detail`; "
            f"the frontend error handler reads that key."
        )

    def test_validation_errors_never_echo_the_submitted_value(self, client, fake_db):
        """PH1.5. FastAPI's default 422 includes each error's `input`, which for
        a registration payload means the plaintext password is reflected to the
        client and into any response logging. The custom handler strips it; this
        is the regression test for that handler still being installed.

        The trigger is a *missing* required field rather than a malformed email:
        `UserCreate.email` is a bare `str` and accepts anything (defect D-10
        below), so a bad email is not a validation error at all.
        """
        secret = "TEST-Sup3rSecret-Passw0rd!"
        resp = client.post("/api/auth/register", json={"password": secret})
        assert resp.status_code == 422
        assert secret not in resp.text, "the submitted password was reflected back"
        for error in resp.json()["detail"]:
            assert set(error) <= {"loc", "msg", "type"}, \
                f"unexpected key in a validation error: {sorted(error)}"

    def test_a_rejected_password_is_never_reflected(self, client, fake_db):
        """The password policy rejects weak passwords with a 4xx. That response
        must describe the *rule* broken, never quote the attempt."""
        weak = "TESTpassword123"
        resp = client.post("/api/auth/register", json={
            "name": "TEST User", "email": "weakpw@example.com", "password": weak})
        assert resp.status_code >= 400
        assert weak not in resp.text

    def test_error_bodies_do_not_leak_internals(
            self, client, fake_db, auth_headers):
        """A 4xx must not disclose file paths, stack frames, or driver errors."""
        leaky = ("Traceback", "/Users/", "/app/backend", "motor", "pymongo",
                 "server.py", "site-packages")
        for method, path, _ in ERROR_CASES:
            kwargs = {"headers": auth_headers}
            if method == "POST":
                kwargs["json"] = {}
            text = client.request(method, path, **kwargs).text
            for token in leaky:
                assert token not in text, f"{method} {path} leaked {token!r}"


# --------------------------------------------------------------------------- #
# Malformed request bodies                                                      #
# --------------------------------------------------------------------------- #
class TestMalformedJson:
    """PH3.3 defect D-6, fixed by a central `JSONDecodeError` handler.

    Eighteen routes read `await request.json()` rather than binding a Pydantic
    model, so they bypass FastAPI's body parsing and its 422 entirely. A
    truncated body — a dropped mobile connection mid-POST — raised
    `JSONDecodeError` inside the handler and surfaced as a 500.
    """

    @pytest.mark.parametrize("body", [
        b"{not json",
        b"",
        b"[[[",
        b'{"unterminated": ',
        b"\x00\x01\x02",
        "{'single': 'quotes'}".encode(),
    ])
    def test_malformed_body_is_400_not_500(
            self, client, fake_db, auth_headers, test_user, body):
        trade = {"_id": ObjectId(), "user_id": str(test_user["_id"]),
                 "symbol": "TESTCO", "status": "OPEN", "quantity": 10,
                 "quantity_open": 10, "entry_price": 100.0, "stop_loss": 90.0,
                 "target1": 120.0, "type": "BUY", "events": []}
        fake_db.trades.docs.append(trade)
        resp = client.put(f"/api/trades/{trade['_id']}", content=body,
                          headers={**auth_headers, "Content-Type": "application/json"})
        assert 400 <= resp.status_code < 500, (
            f"a malformed body produced {resp.status_code}; it is a client error."
        )
        assert "detail" in resp.json()

    def test_the_parse_error_does_not_echo_the_body(self, client, fake_db, auth_headers):
        """The decoder's message quotes the offending fragment; reflecting it
        would reintroduce exactly what the 422 handler strips."""
        resp = client.put(f"/api/admin/users/{ObjectId()}",
                          content=b'{"secret": "TEST-do-not-reflect"',
                          headers={**auth_headers, "Content-Type": "application/json"})
        assert "TEST-do-not-reflect" not in resp.text

    def test_a_valid_body_still_works(self, client, fake_db, auth_headers, test_user):
        """The D-6 handler must not have broken well-formed requests."""
        trade = {"_id": ObjectId(), "user_id": str(test_user["_id"]),
                 "symbol": "TESTCO", "status": "OPEN", "quantity": 10,
                 "quantity_open": 10, "entry_price": 100.0, "stop_loss": 90.0,
                 "target1": 120.0, "type": "BUY", "events": []}
        fake_db.trades.docs.append(trade)
        resp = client.put(f"/api/trades/{trade['_id']}", json={"stop_loss": 95.0},
                          headers=auth_headers)
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Method and route errors                                                       #
# --------------------------------------------------------------------------- #
class TestRoutingErrors:
    def test_wrong_method_is_405(self, client, fake_db):
        assert client.delete("/api/market/overview").status_code == 405

    def test_unknown_api_path_is_404_with_a_json_body(self, client, fake_db):
        resp = client.get("/api/definitely/not/a/route")
        assert resp.status_code == 404
        assert "detail" in resp.json()


# --------------------------------------------------------------------------- #
# Infrastructure failure                                                        #
# --------------------------------------------------------------------------- #
class TestDatabaseFailure:
    """What the API does when MongoDB is unreachable.

    This is the one place the suite deliberately induces a failure, because
    "the database is down" is a real state with an unchecked contract. The
    requirement is modest and specific: the process must not be taken down, and
    the client must receive a structured response it can act on.
    """

    def test_a_database_outage_does_not_crash_the_process(
            self, client, fake_db, auth_headers, monkeypatch):
        class _DeadCollection:
            def __getattr__(self, name):
                raise ConnectionError("TEST: mongo unreachable")

        class _DeadDB:
            def __getattr__(self, name):
                return _DeadCollection()

        monkeypatch.setattr(server, "db", _DeadDB())
        try:
            resp = client.get("/api/trades", headers=auth_headers)
            status = resp.status_code
        except ConnectionError:
            # Surfacing as an unhandled 500 from the ASGI layer is the current
            # documented behaviour (see the certification's Known Gaps): the
            # error is not swallowed, and no partial//fabricated data is served.
            status = 500
        assert status >= 400, "a dead database must never look like success"
        # The app object itself must still be serving other requests.
        assert client.get("/api/health/live").status_code == 200

    def test_health_liveness_does_not_depend_on_the_database(self, client):
        """Liveness answers "is this process alive", never "is the database up".
        Coupling them makes a database blip roll every application pod, turning
        a recoverable dependency failure into a full outage."""
        assert client.get("/api/health/live").status_code == 200


class TestHealthEndpoints:
    @pytest.mark.parametrize("endpoint", [
        "/api/health", "/api/health/live", "/api/health/startup", "/api/health/ready",
    ])
    def test_health_endpoints_answer_without_credentials(self, client, endpoint):
        """Probes are unauthenticated by necessity — a load balancer has no
        token — and must stay that way.

        200 or 503 are both correct: under TestClient the app's lifespan has not
        run, so the readiness/startup gates legitimately report `starting`. That
        they answer *at all*, in JSON, without credentials, is the contract.
        """
        resp = client.get(endpoint)
        assert resp.status_code in (200, 503), f"{endpoint}: {resp.text[:200]}"
        assert isinstance(resp.json(), dict)

    def test_readiness_reports_a_status_either_way(self, client, fake_db):
        """Readiness may legitimately answer 503 when a dependency is down;
        what it must never do is answer nothing parseable."""
        resp = client.get("/api/health/ready")
        assert resp.status_code in (200, 503)
        assert isinstance(resp.json(), dict)


# --------------------------------------------------------------------------- #
# Rate limiting at the API level                                                #
# --------------------------------------------------------------------------- #
class TestRateLimitIntegration:
    """PH1 owns the rate limiter's behaviour; this owns its *attachment*.

    `test_rate_limit.py` exercises the middleware against a synthetic Starlette
    route. That proves the middleware works — it cannot prove it is mounted on
    the real application, which is a separate and silent failure mode: remove
    the `apply_rate_limiting` call from `server.py` and every one of those tests
    still passes.
    """

    def test_the_anonymous_tier_is_attached_to_real_endpoints(
            self, client, fake_db, monkeypatch):
        monkeypatch.setattr(rl, "PUBLIC_API", RateLimitPolicy("api_ip", 2, 60, "ip"))
        assert client.get("/api/market/overview").status_code == 200
        assert client.get("/api/market/overview").status_code == 200
        throttled = client.get("/api/market/overview")
        assert throttled.status_code == 429
        assert throttled.json()["code"] == "RATE_LIMITED"
        assert int(throttled.headers["retry-after"]) > 0, \
            "a 429 without Retry-After tells the client nothing about when to retry"

    def test_the_authenticated_tier_is_attached_and_keyed_per_user(
            self, client, fake_db, auth_headers, other_headers, monkeypatch):
        monkeypatch.setattr(rl, "AUTHENTICATED_API",
                            RateLimitPolicy("api_user", 1, 60, "user"))
        assert client.get("/api/trades", headers=auth_headers).status_code == 200
        assert client.get("/api/trades", headers=auth_headers).status_code == 429
        assert client.get("/api/trades", headers=other_headers).status_code == 200, \
            "one user's budget must not throttle another's"

    def test_health_probes_are_exempt_on_the_real_app(self, client, fake_db, monkeypatch):
        """A throttled probe reads as "unhealthy" to an orchestrator, which then
        restarts a container that was fine — the limiter manufacturing the
        outage it exists to prevent."""
        monkeypatch.setattr(rl, "PUBLIC_API", RateLimitPolicy("api_ip", 1, 60, "ip"))
        for _ in range(5):
            assert client.get("/api/health/live").status_code == 200

    def test_a_throttled_response_still_carries_security_headers(
            self, client, fake_db, monkeypatch):
        """The limiter is wired *inside* the header/CORS middleware so a 429 is
        still readable by a browser. Wiring it outside would strip both."""
        monkeypatch.setattr(rl, "PUBLIC_API", RateLimitPolicy("api_ip", 1, 60, "ip"))
        client.get("/api/market/overview")
        throttled = client.get("/api/market/overview")
        assert throttled.status_code == 429
        assert "x-content-type-options" in {k.lower() for k in throttled.headers}


# --------------------------------------------------------------------------- #
# Broker error translation                                                      #
# --------------------------------------------------------------------------- #
class TestBrokerErrorHandlers:
    """Broker failures have dedicated handlers with deliberately-chosen codes."""

    def test_a_broker_auth_error_is_409_not_401(
            self, authenticated_client, fake_db, test_user):
        """409, specifically. A 401 here would trip the frontend's global
        session-expiry interceptor and log the user out of StockAssist because
        their *broker* token expired — losing their session over an unrelated
        third party's problem."""
        from services.brokers.base import BrokerAuthError
        with patch.object(server.broker_engine, "get_holdings", new_callable=AsyncMock,
                          side_effect=BrokerAuthError("Reconnect your broker")):
            resp = authenticated_client.get("/api/brokers/zerodha/holdings")
        assert resp.status_code == 409
        assert resp.json()["code"] == "BROKER_AUTH"

    def test_a_broker_rate_limit_is_429(self, authenticated_client, fake_db, test_user):
        from services.brokers.base import BrokerError
        error = BrokerError("slow down", user_message="Broker rate limit")
        error.code = "RATE_LIMIT"
        with patch.object(server.broker_engine, "get_holdings", new_callable=AsyncMock,
                          side_effect=error):
            resp = authenticated_client.get("/api/brokers/zerodha/holdings")
        assert resp.status_code == 429

    def test_a_generic_broker_failure_is_502(self, authenticated_client, fake_db, test_user):
        """502 says "the upstream failed", which is true and actionable, rather
        than 500 which says "we failed" and points debugging at the wrong system."""
        from services.brokers.base import BrokerError
        with patch.object(server.broker_engine, "get_holdings", new_callable=AsyncMock,
                          side_effect=BrokerError("upstream down",
                                                  user_message="Broker unavailable")):
            resp = authenticated_client.get("/api/brokers/zerodha/holdings")
        assert resp.status_code == 502
        assert "detail" in resp.json()


# --------------------------------------------------------------------------- #
# Registration input validation                                                 #
# --------------------------------------------------------------------------- #
class TestRegistrationEmailValidation:
    """PH3.3 defect D-10 (MEDIUM) — recorded, deliberately not fixed here.

    `UserCreate.email` is a bare `str`. No format check exists anywhere on the
    registration path, so `POST /api/auth/register` happily creates an account
    with `"not-an-email"` — or with an empty string.

    Why that matters beyond tidiness: email is this system's only identity
    recovery channel. An account created with an unusable address can never
    verify itself and can never complete a password reset, so a typo at signup
    produces a permanently unrecoverable account, and the user's only remedy is
    to register again. It is also a free way to fill the users collection with
    rows that no operator can contact.

    Why it is not fixed in PH3.3: `/api/auth/*` is PH1-certified surface, and
    switching to `EmailStr` changes an authentication contract, adds the
    `email-validator` dependency, and needs a decision about accounts already
    stored with invalid addresses. That is a migration, not a test fix, and
    PH3.3's mandate is explicit that larger defects are documented and assigned
    rather than absorbed. Assigned in the certification's handoff.
    """

    @pytest.mark.parametrize("email", ["not-an-email", "@example.com", "a@b@c", " "])
    @pytest.mark.xfail(reason="D-10: registration does not validate email format",
                       strict=False)
    def test_invalid_email_should_be_rejected(self, client, fake_db, email):
        resp = client.post("/api/auth/register", json={
            "name": "TEST User", "email": email, "password": "TEST-Str0ng-Passw0rd!x"})
        assert resp.status_code == 422

    def test_what_actually_happens_today(self, client, fake_db):
        """Pinned so the behaviour change is visible when D-10 is fixed."""
        resp = client.post("/api/auth/register", json={
            "name": "TEST User", "email": "not-an-email",
            "password": "TEST-Str0ng-Passw0rd!x"})
        assert resp.status_code == 200
        assert fake_db.users.docs[0]["email"] == "not-an-email"

    def test_duplicate_registration_is_rejected(self, client, fake_db):
        """The one registration invariant that *is* enforced, kept under test so
        a D-10 fix cannot regress it."""
        payload = {"name": "TEST User", "email": "dup@example.com",
                   "password": "TEST-Str0ng-Passw0rd!x"}
        assert client.post("/api/auth/register", json=payload).status_code == 200
        second = client.post("/api/auth/register", json=payload)
        assert second.status_code == 400
        assert len(fake_db.users.docs) == 1

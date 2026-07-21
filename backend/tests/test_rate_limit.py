"""PH1.7 — rate limiting & abuse-protection tests.

Covers the sprint's acceptance matrix:

* login rate limit enforced (failures counted, success resets, lockout + expiry);
* registration rate limit enforced;
* refresh endpoint limit enforced;
* authenticated-API / public-API tier enforced by the middleware;
* Retry-After returned on every rejection;
* progressive (escalating) lockout;
* existing auth lifecycle keeps working.

Hermetic: the limiter/store run against the in-memory ``FakeDB`` via
``asyncio.run`` (no pytest-asyncio); the middleware runs against a tiny Starlette
app; endpoint tests use the shared ``client``/``fake_db`` fixtures. No live
server, Mongo, or Redis needed.
"""
import asyncio
import time

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from security import jwt as jwtmod
from security import rate_limit as rl
from security.rate_limit import (
    RateLimitPolicy, RateLimiter, MongoRateLimitStore,
)
from tests._fakedb import FakeDB

REG_PASSWORD = "S3cure!Passw0rd"


def _run(coro):
    return asyncio.run(coro)


def _store():
    return MongoRateLimitStore(FakeDB().rate_limits)


# --------------------------------------------------------------------------- #
# Store — fixed-window counter + lockout                                        #
# --------------------------------------------------------------------------- #
class TestStore:
    def test_hit_increments_within_window(self):
        store = _store()

        async def go():
            c1, _ = await store.hit("k", 60)
            c2, _ = await store.hit("k", 60)
            return c1, c2
        assert _run(go()) == (1, 2)

    def test_count_is_readonly(self):
        store = _store()

        async def go():
            await store.hit("k", 60)
            c_read, _ = await store.count("k", 60)
            c_read2, _ = await store.count("k", 60)
            return c_read, c_read2
        assert _run(go()) == (1, 1)  # reading never increments

    def test_block_expires(self):
        store = _store()

        async def go():
            await store.set_block("k", int(time.time()) - 1, trips=1)  # already past
            return await store.get_block("k")
        assert _run(go()) is None

    def test_active_block_returned_with_trips(self):
        store = _store()

        async def go():
            await store.set_block("k", int(time.time()) + 100, trips=3)
            return await store.get_block("k")
        until, trips = _run(go())
        assert trips == 3 and until > int(time.time())

    def test_clear_removes_everything(self):
        store = _store()

        async def go():
            await store.hit("k", 60)
            await store.set_block("k", int(time.time()) + 100, 1)
            await store.clear("k")
            return (await store.count("k", 60))[0], await store.get_block("k")
        count, block = _run(go())
        assert count == 0 and block is None


# --------------------------------------------------------------------------- #
# Limiter — decisions, lockout, escalation, reset                               #
# --------------------------------------------------------------------------- #
class TestLimiter:
    def test_check_allows_under_limit_then_blocks(self):
        pol = RateLimitPolicy("t", limit=2, window_seconds=60, scope="ip")
        lim = RateLimiter(_store())

        async def go():
            return [(await lim.check(pol, "id")).allowed for _ in range(4)]
        # limit=2: req1,req2 allowed; req3 (count 3 > 2) and req4 blocked.
        assert _run(go()) == [True, True, False, False]

    def test_peek_does_not_consume(self):
        pol = RateLimitPolicy("t", limit=1, window_seconds=60, scope="ip")
        lim = RateLimiter(_store())

        async def go():
            a = (await lim.peek(pol, "id")).allowed
            b = (await lim.peek(pol, "id")).allowed
            return a, b
        assert _run(go()) == (True, True)  # peeking never burns budget

    def test_record_failure_locks_at_limit(self):
        pol = RateLimitPolicy("login", limit=3, window_seconds=900, scope="ip:account")
        lim = RateLimiter(_store())

        async def go():
            results = [await lim.record_failure(pol, "id") for _ in range(3)]
            after = await lim.peek(pol, "id")
            return results, after
        results, after = _run(go())
        assert results[0].allowed and results[1].allowed          # first two under limit
        assert not results[2].allowed                              # third failure trips lock
        assert not after.allowed and after.retry_after > 0         # subsequent peek is locked

    def test_reset_clears_after_success(self):
        pol = RateLimitPolicy("login", limit=2, window_seconds=900, scope="ip:account")
        lim = RateLimiter(_store())

        async def go():
            await lim.record_failure(pol, "id")
            await lim.record_failure(pol, "id")   # locked now
            locked = (await lim.peek(pol, "id")).allowed
            await lim.reset(pol, "id")            # successful login
            freed = (await lim.peek(pol, "id")).allowed
            return locked, freed
        locked, freed = _run(go())
        assert locked is False and freed is True

    def test_retry_after_present_on_denial(self):
        pol = RateLimitPolicy("t", limit=1, window_seconds=120, scope="ip")
        lim = RateLimiter(_store())

        async def go():
            await lim.check(pol, "id")
            return await lim.check(pol, "id")
        denied = _run(go())
        assert not denied.allowed and denied.retry_after > 0 and denied.reset > int(time.time())

    def test_escalation_increases_block_across_successive_lockouts(self):
        # block_seconds=10, escalate → 10, 20, 40 ... on each *new* trip once the
        # prior lockout has expired. An active lockout is NOT re-escalated (a
        # client hammering during a lockout just keeps getting the same wait).
        pol = RateLimitPolicy("t", limit=0, window_seconds=3600, scope="ip",
                              block_seconds=10, escalate=True, max_block_seconds=1000)
        store = _store()
        lim = RateLimiter(store)

        async def go():
            durations = []
            for _ in range(3):
                d = (await lim.check(pol, "id")).retry_after   # limit=0 → always trips
                durations.append(d)
                # Simulate the lockout expiring while the trip history persists,
                # then the abuser returns for another trip.
                trips = await store.get_trips("t:id")
                await store.set_block("t:id", int(time.time()) - 1, trips,
                                      expires_epoch=int(time.time()) + 1000)
            return durations
        d1, d2, d3 = _run(go())
        assert d1 < d2 < d3           # 10 → 20 → 40, strictly escalating
        assert (d2, d3) == (2 * d1, 4 * d1)

    def test_active_lockout_not_re_escalated(self):
        pol = RateLimitPolicy("t", limit=0, window_seconds=3600, scope="ip",
                              block_seconds=10, escalate=True)
        lim = RateLimiter(_store())

        async def go():
            d1 = (await lim.check(pol, "id")).retry_after
            d2 = (await lim.check(pol, "id")).retry_after  # still locked → same wait
            return d1, d2
        d1, d2 = _run(go())
        assert abs(d1 - d2) <= 1  # unchanged (modulo the one-second tick)


# --------------------------------------------------------------------------- #
# Policies + helpers                                                            #
# --------------------------------------------------------------------------- #
class TestPolicies:
    def test_default_policies_match_spec(self):
        assert (rl.LOGIN.limit, rl.LOGIN.window_seconds) == (5, 900)
        assert (rl.REGISTER.limit, rl.REGISTER.window_seconds) == (5, 3600)
        assert (rl.REFRESH.limit, rl.REFRESH.window_seconds) == (20, 60)
        assert (rl.AUTHENTICATED_API.limit, rl.AUTHENTICATED_API.window_seconds) == (120, 60)
        assert (rl.PUBLIC_API.limit, rl.PUBLIC_API.window_seconds) == (60, 60)
        assert rl.LOGIN.escalate is True

    def test_env_override_parsed(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_LOGIN", "9/111")
        pol = rl._policy("login", 5, 900, "ip:account")
        assert (pol.limit, pol.window_seconds) == (9, 111)

    def test_env_override_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_LOGIN", "garbage")
        pol = rl._policy("login", 5, 900, "ip:account")
        assert (pol.limit, pol.window_seconds) == (5, 900)

    def test_client_ip_prefers_forwarded_for(self):
        class _Req:
            headers = {"x-forwarded-for": "1.2.3.4, 5.6.7.8"}
            client = None
        assert rl.client_ip(_Req()) == "1.2.3.4"


# --------------------------------------------------------------------------- #
# Middleware — platform-wide tiers                                              #
# --------------------------------------------------------------------------- #
def _mw_client(fake):
    async def ok(request):
        return JSONResponse({"ok": True})
    app = Starlette(routes=[
        Route("/api/x", ok, methods=["GET", "POST"]),
        Route("/api", ok, methods=["GET"]),
    ])
    rl.apply_rate_limiting(app, lambda: fake)
    return TestClient(app)


class TestMiddleware:
    def test_public_ip_tier_enforced(self, monkeypatch):
        monkeypatch.setattr(rl, "PUBLIC_API", RateLimitPolicy("api_ip", 2, 60, "ip"))
        client = _mw_client(FakeDB())
        assert client.get("/api/x").status_code == 200
        assert client.get("/api/x").status_code == 200
        r = client.get("/api/x")
        assert r.status_code == 429
        assert r.json()["code"] == "RATE_LIMITED"
        assert int(r.headers["retry-after"]) > 0

    def test_rate_headers_on_allowed(self, monkeypatch):
        monkeypatch.setattr(rl, "PUBLIC_API", RateLimitPolicy("api_ip", 5, 60, "ip"))
        client = _mw_client(FakeDB())
        r = client.get("/api/x")
        assert r.headers["x-ratelimit-limit"] == "5"
        assert int(r.headers["x-ratelimit-remaining"]) == 4

    def test_health_and_options_exempt(self, monkeypatch):
        monkeypatch.setattr(rl, "PUBLIC_API", RateLimitPolicy("api_ip", 1, 60, "ip"))
        client = _mw_client(FakeDB())
        for _ in range(5):
            assert client.get("/api").status_code == 200  # health never throttled

    def test_authenticated_tier_keyed_per_user(self, monkeypatch):
        monkeypatch.setattr(rl, "AUTHENTICATED_API", RateLimitPolicy("api_user", 1, 60, "user"))
        client = _mw_client(FakeDB())
        tok_a = jwtmod.create_access_token("userA", "a@x.com", "s1")
        tok_b = jwtmod.create_access_token("userB", "b@x.com", "s2")
        assert client.get("/api/x", headers={"Authorization": f"Bearer {tok_a}"}).status_code == 200
        # userA over budget, but userB is a different key → still allowed.
        assert client.get("/api/x", headers={"Authorization": f"Bearer {tok_a}"}).status_code == 429
        assert client.get("/api/x", headers={"Authorization": f"Bearer {tok_b}"}).status_code == 200

    def test_store_error_fails_open(self):
        class _BadDB:
            @property
            def rate_limits(self):
                raise RuntimeError("mongo down")
        async def ok(request):
            return JSONResponse({"ok": True})
        app = Starlette(routes=[Route("/api/x", ok)])
        rl.apply_rate_limiting(app, lambda: _BadDB())
        client = TestClient(app)
        # Storage failure must not take the API down.
        assert client.get("/api/x").status_code == 200


# --------------------------------------------------------------------------- #
# Endpoint integration — the real auth routes                                   #
# --------------------------------------------------------------------------- #
class TestLoginEndpoint:
    def _register(self, client, email="rl@example.com"):
        return client.post("/api/auth/register", json={
            "name": "RL User", "email": email, "password": REG_PASSWORD,
        })

    def test_login_lockout_after_five_failures(self, client, fake_db):
        self._register(client)
        for _ in range(5):
            bad = client.post("/api/auth/login", json={"email": "rl@example.com", "password": "wrong-Passw0rd!"})
            assert bad.status_code == 401
        blocked = client.post("/api/auth/login", json={"email": "rl@example.com", "password": "wrong-Passw0rd!"})
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) > 0

    def test_successful_login_resets_budget(self, client, fake_db):
        self._register(client)
        # Two failures, then a success clears the counter.
        for _ in range(2):
            client.post("/api/auth/login", json={"email": "rl@example.com", "password": "nope-Passw0rd!"})
        ok = client.post("/api/auth/login", json={"email": "rl@example.com", "password": REG_PASSWORD})
        assert ok.status_code == 200
        # Budget reset: three fresh failures are all 401 (not immediately locked).
        for _ in range(3):
            assert client.post("/api/auth/login", json={"email": "rl@example.com", "password": "x-Passw0rd!"}).status_code == 401

    def test_register_rate_limited(self, client, fake_db, monkeypatch):
        monkeypatch.setattr(rl, "REGISTER", RateLimitPolicy("register", 2, 3600, "ip"))
        assert self._register(client, "a@example.com").status_code == 200
        assert self._register(client, "b@example.com").status_code == 200
        blocked = self._register(client, "c@example.com")
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) > 0

    def test_refresh_rate_limited(self, client, fake_db, monkeypatch):
        monkeypatch.setattr(rl, "REFRESH", RateLimitPolicy("refresh", 3, 60, "session"))
        self._register(client)  # sets refresh_token cookie on the client
        allowed = [client.post("/api/auth/refresh").status_code for _ in range(3)]
        assert allowed == [200, 200, 200]
        blocked = client.post("/api/auth/refresh")
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) > 0

    def test_auth_lifecycle_still_works(self, client, fake_db):
        """Regression: register → login → refresh → logout unaffected by PH1.7."""
        assert self._register(client, "life@example.com").status_code == 200
        assert client.post("/api/auth/login", json={"email": "life@example.com", "password": REG_PASSWORD}).status_code == 200
        assert client.post("/api/auth/refresh").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200

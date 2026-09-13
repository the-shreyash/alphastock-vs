"""D6.8 — entitlements, capability authorization and access control.

THE QUESTION
------------
"Can a user obtain a capability simply by changing a request, broker name,
account id, role, URL, frontend state, or client payload?"

Every test here drives the real application (`server.app` through `TestClient`,
real JWTs from the app's own issuer, the real `broker_engine`, the real
`broker_registry`) and asserts on the thing that would change if the check were
removed: the database row, the adapter call, the response an attacker can
compare. A status code alone is never the only assertion where a side effect is
possible.

WHAT EXISTS, AND WHAT DOES NOT (see .claude/TASK.md, D6.8 §1–§2)
----------------------------------------------------------------
* Platform identity — JWT → `get_current_user`, re-read from the database on
  every request.
* Platform role — `users.role`; `require_admin` / super_admin checks.
* Broker account ownership — `BrokerAccountDirectory.resolve(user_id, id)`.
* Broker capability — `BrokerGateway.require_capability`.
* Product entitlement — **NOT IMPLEMENTED.** Plan tiers are values of
  `users.role` that no server-side code path consults; `plan_expires_at` is never
  read; `db.feature_flags` is never read; `db.payments` has no writer. This
  module does not test an entitlement system that does not exist. It pins that
  *nothing a client sends* can stand in for one.

SECTIONS (brief phase in brackets)
----------------------------------
  §1  Client-supplied authority is ignored            [4, 5, 19]
  §2  Admin identity and the admin-tier target  F-2   [6]
  §3  The public surface is pinned              F-3   [4, 16]
  §4  Paid model calls require identity         F-5   [5, 10]
  §5  A conversation is (user, label)           F-1   [10, 13]
  §6  Paper and live trades cannot cross        F-4   [11, 12]
  §7  Order authorization stops before the adapter    [7, 8, 12, 14]
  §8  Withdrawn broker accounts serve no session F-6  [8, 14]
  §9  Realtime private channels                       [9]
  §10 Indistinguishable refusals                      [13]

No real broker is contacted: every adapter order method is replaced by a spy
for the whole of §7/§8, and a spy that is never called is asserted as such.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

import server
from services.broker_engine import broker_engine
from services.brokers import broker_registry
from services.brokers.accounts import BrokerAccountStatus
from services.brokers.errors import BrokerAuthError
from tests._accounts import account_doc, account_ref, fixture_account_id
from tests._routes import PUBLIC_ROUTES
from tests.conftest import _headers_for, _seed_user


def _stored(fake_db, collection, oid):
    return next(d for d in getattr(fake_db, collection).docs if d["_id"] == oid)


# =========================================================================== #
# §1 — CLIENT-SUPPLIED AUTHORITY IS IGNORED                                    #
# =========================================================================== #
#: Every field name a client might try as a self-grant. None of them exists as a
#: server-side authority; the tests prove sending them changes nothing stored
#: and nothing decided.
_FORGED_AUTHORITY = {
    "role": "super_admin", "admin": True, "is_admin": True, "is_superuser": True,
    "plan": "elite", "plan_expires_at": None, "entitlement": True,
    "entitlements": ["live_trading", "ai_unlimited"], "capability": True,
    "capabilities": ["place_order"], "blocked": False, "user_id": "someone-else",
    "email_verified": True, "paper_capital": 99_999_999,
}


class TestClientSuppliedAuthorityIsIgnored:
    def test_registration_cannot_choose_a_role_or_plan(self, client, fake_db):
        resp = client.post("/api/auth/register", json={
            "name": "Mallory", "email": "mallory@example.com",
            "password": "Correct-Horse-Battery-9", **_FORGED_AUTHORITY})
        assert resp.status_code == 200, resp.text
        stored = next(u for u in fake_db.users.docs if u["email"] == "mallory@example.com")
        assert stored["role"] == "user"
        for field in ("plan", "admin", "is_admin", "entitlements", "capabilities",
                      "paper_capital", "plan_expires_at"):
            assert field not in stored, f"registration persisted client field {field!r}"
        me = client.get("/api/admin/dashboard", headers=_headers_for(stored))
        assert me.status_code == 403

    def test_settings_update_cannot_grant_a_role_plan_or_unblock(
            self, authenticated_client, fake_db, test_user):
        resp = authenticated_client.put("/api/settings", json={
            "name": "Still Me", **_FORGED_AUTHORITY})
        assert resp.status_code == 200, resp.text
        stored = _stored(fake_db, "users", test_user["_id"])
        assert stored["name"] == "Still Me", "positive control: the real field was not applied"
        assert stored["role"] == "user"
        for field in ("plan", "admin", "is_admin", "entitlements", "capabilities",
                      "paper_capital", "plan_expires_at", "email_verified"):
            assert field not in stored, f"settings persisted client field {field!r}"
        assert authenticated_client.get("/api/admin/dashboard").status_code == 403

    @pytest.mark.parametrize("how", ["query", "header", "body"])
    def test_an_admin_flag_in_the_request_does_not_open_the_console(
            self, client, fake_db, test_user, auth_headers, how):
        kwargs = {"headers": dict(auth_headers)}
        if how == "query":
            path = "/api/admin/users?admin=true&role=super_admin&is_admin=1"
        else:
            path = "/api/admin/users"
        if how == "header":
            kwargs["headers"].update({"X-Admin": "true", "X-User-Role": "super_admin",
                                      "X-Role": "admin", "X-Entitlement": "admin"})
        if how == "body":
            resp = client.request("PUT", f"/api/admin/users/{test_user['_id']}",
                                  json={"role": "super_admin", "admin": True}, **kwargs)
            assert resp.status_code == 403
            assert _stored(fake_db, "users", test_user["_id"])["role"] == "user"
            return
        assert client.get(path, **kwargs).status_code == 403

    @pytest.mark.parametrize("path_tail,body", [
        ("/block", {"role": "super_admin", "admin": True, "is_admin": True}),
        ("/grant-plan", {"plan": "elite", "role": "admin", "admin": True}),
    ])
    def test_an_admin_field_in_the_body_cannot_authorize_an_action_with_no_second_check(
            self, client, fake_db, test_user, other_user, auth_headers, path_tail, body):
        """The role-write test above is not enough on its own: that route also
        runs `validate_role_assignment`, which refuses a non-super_admin actor
        further down, so a `require_admin` that trusted the body would still
        answer 403 there. Block and grant-plan have no such second check, so
        they are where trusting a request field would actually take effect."""
        before = dict(other_user)
        resp = client.post(f"/api/admin/users/{other_user['_id']}{path_tail}",
                           json=body, headers=auth_headers)
        assert resp.status_code == 403
        assert _stored(fake_db, "users", other_user["_id"]) == before

    def test_role_is_read_from_the_database_on_every_request_not_from_the_token(
            self, client, fake_db, admin_user, admin_headers):
        """The same bearer token is admin, then not. If role were carried in the
        token or cached per session, demotion would not take effect until expiry."""
        assert client.get("/api/admin/dashboard", headers=admin_headers).status_code == 200
        _stored(fake_db, "users", admin_user["_id"])["role"] = "user"
        assert client.get("/api/admin/dashboard", headers=admin_headers).status_code == 403

    @pytest.mark.parametrize("role", ["", None, "ADMIN", "admin ", "Super_Admin",
                                      ["admin"], {"$in": ["admin"]}, 1])
    def test_a_malformed_stored_role_fails_closed(self, client, fake_db, role):
        """Phase 14: a malformed role never defaults to admin."""
        user = _seed_user(fake_db, "weird@example.com", "user", "Weird")
        user["role"] = role
        assert client.get("/api/admin/dashboard", headers=_headers_for(user)).status_code == 403

    def test_a_user_row_with_no_role_is_not_admin(self, client, fake_db):
        user = _seed_user(fake_db, "norole@example.com", "user", "No Role")
        del user["role"]
        assert client.get("/api/admin/dashboard", headers=_headers_for(user)).status_code == 403


# =========================================================================== #
# §2 — ADMIN IDENTITY AND THE ADMIN-TIER TARGET (F-2)                           #
# =========================================================================== #
def _admin_mutations(target_id):
    """Every admin endpoint that modifies another account, with a benign body."""
    return {
        "update_name": ("PUT", f"/api/admin/users/{target_id}", {"name": "renamed"}),
        "demote": ("PUT", f"/api/admin/users/{target_id}", {"role": "pro"}),
        "grant_plan": ("POST", f"/api/admin/users/{target_id}/grant-plan",
                       {"plan": "pro", "duration_days": 30}),
        "block": ("POST", f"/api/admin/users/{target_id}/block", None),
        "unblock": ("POST", f"/api/admin/users/{target_id}/unblock", None),
    }


_MUTATIONS = sorted(_admin_mutations("x"))


class TestAnAdminCannotModifyAnAdminTierAccount:
    """F-2. `validate_role_assignment` checked the role being written; nothing
    checked the account it was written to. A plain admin could demote, re-plan or
    block a super_admin."""

    @pytest.mark.parametrize("action", _MUTATIONS)
    @pytest.mark.parametrize("target_role", ["admin", "super_admin"])
    def test_a_plain_admin_is_refused_and_nothing_is_written(
            self, client, fake_db, admin_headers, action, target_role):
        target = _seed_user(fake_db, f"{target_role}-target@example.com", target_role, "Target")
        before = dict(target)
        method, path, body = _admin_mutations(target["_id"])[action]
        resp = client.request(method, path, json=body, headers=admin_headers)
        assert resp.status_code == 403, resp.text
        assert _stored(fake_db, "users", target["_id"]) == before, \
            f"{action} modified an {target_role} account despite the 403"
        assert not any(log.get("target") == str(target["_id"])
                       for log in fake_db.admin_audit_logs.docs), \
            "an audit record claims an action that was refused"

    @pytest.mark.parametrize("action", _MUTATIONS)
    @pytest.mark.parametrize("target_role", ["admin", "super_admin"])
    def test_a_super_admin_may(self, client, fake_db, super_admin_headers, action, target_role):
        """Positive control: the refusal above is about the actor, not a broken route."""
        target = _seed_user(fake_db, f"{target_role}-t2@example.com", target_role, "Target")
        method, path, body = _admin_mutations(target["_id"])[action]
        resp = client.request(method, path, json=body, headers=super_admin_headers)
        assert resp.status_code == 200, resp.text

    @pytest.mark.parametrize("action", _MUTATIONS)
    @pytest.mark.parametrize("target_role", ["user", "pro", "elite"])
    def test_a_plain_admin_may_still_administer_ordinary_accounts(
            self, client, fake_db, admin_headers, action, target_role):
        target = _seed_user(fake_db, f"{target_role}-t3@example.com", target_role, "Target")
        method, path, body = _admin_mutations(target["_id"])[action]
        resp = client.request(method, path, json=body, headers=admin_headers)
        assert resp.status_code == 200, resp.text

    @pytest.mark.parametrize("action", _MUTATIONS)
    def test_a_plain_admin_cannot_modify_themselves_either(
            self, client, fake_db, admin_user, admin_headers, action):
        """An admin is an admin-tier target. Self-service changes go through
        /api/settings, which cannot touch role or blocked."""
        before = dict(admin_user)
        method, path, body = _admin_mutations(admin_user["_id"])[action]
        assert client.request(method, path, json=body, headers=admin_headers).status_code == 403
        assert _stored(fake_db, "users", admin_user["_id"]) == before

    @pytest.mark.parametrize("action", _MUTATIONS)
    def test_a_missing_target_is_404_not_a_success(self, client, fake_db, admin_headers, action):
        """block/unblock/update used to write to a nonexistent id and report
        success — an audit trail of actions that happened to nobody."""
        method, path, body = _admin_mutations(ObjectId())[action]
        assert client.request(method, path, json=body, headers=admin_headers).status_code == 404
        assert fake_db.admin_audit_logs.docs == []

    def test_an_ordinary_user_learns_nothing_about_which_ids_exist(
            self, client, fake_db, other_user, auth_headers):
        """The admin dependency runs before target resolution, so a non-admin
        gets the same 403 for a real account and a fabricated one."""
        for action in _MUTATIONS:
            m, real, body = _admin_mutations(other_user["_id"])[action]
            _, fake, _ = _admin_mutations(ObjectId())[action]
            r1 = client.request(m, real, json=body, headers=auth_headers)
            r2 = client.request(m, fake, json=body, headers=auth_headers)
            assert (r1.status_code, r1.json()) == (r2.status_code, r2.json()) == \
                (403, {"detail": "Admin access required"})


class TestPlanGrantAllowlistIsTheRoleModule:
    def test_every_plan_role_is_grantable_and_nothing_else(self, client, fake_db, admin_headers):
        from security.roles import ADMIN_TIER_ROLES, PLAN_ROLES
        for plan in sorted(PLAN_ROLES):
            target = _seed_user(fake_db, f"{plan}@example.com", "user", plan)
            resp = client.post(f"/api/admin/users/{target['_id']}/grant-plan",
                               json={"plan": plan, "duration_days": 1}, headers=admin_headers)
            assert resp.status_code == 200, (plan, resp.text)
            assert _stored(fake_db, "users", target["_id"])["role"] == plan
        for forbidden in sorted(ADMIN_TIER_ROLES) + ["user", "root", "", "PRO"]:
            target = _seed_user(fake_db, f"f-{forbidden}@example.com", "user", "x")
            resp = client.post(f"/api/admin/users/{target['_id']}/grant-plan",
                               json={"plan": forbidden}, headers=admin_headers)
            assert resp.status_code == 400, forbidden
            assert _stored(fake_db, "users", target["_id"])["role"] == "user"

    @pytest.mark.parametrize("plan", [["pro"], {"$ne": "x"}, 1, None, True])
    def test_a_non_string_plan_is_rejected_before_any_write(self, client, fake_db, admin_headers, plan):
        target = _seed_user(fake_db, "ns@example.com", "user", "x")
        resp = client.post(f"/api/admin/users/{target['_id']}/grant-plan",
                           json={"plan": plan}, headers=admin_headers)
        assert resp.status_code == 400
        assert _stored(fake_db, "users", target["_id"])["role"] == "user"


# =========================================================================== #
# §3 — THE PUBLIC SURFACE IS PINNED (F-3)                                      #
# =========================================================================== #
#: Every route reachable with no credential, and why. `tests/_routes.py`
#: classifies a route as protected by finding `get_current_user` in its
#: dependency tree — so deleting that dependency reclassified the route as public
#: and deleted its 401 test with it. The sweep's own docstring claimed the
#: opposite. Pinning the complement is what makes that deletion fail.
#:
#: Adding a public route means adding it here with a reason. That is the review
#: step: a route is public because someone decided so, not because nobody
#: remembered the dependency.
PINNED_PUBLIC_ROUTES = frozenset({
    # Liveness / discovery / operational. Metrics and diagnostics are gated in
    # production inside the handler (`observability.routes.require_operational_access`).
    ("GET", "/api"), ("GET", "/api/health"), ("GET", "/api/health/live"),
    ("GET", "/api/health/ready"), ("GET", "/api/health/startup"),
    ("GET", "/api/metrics"), ("GET", "/api/diagnostics"), ("GET", "/api/diagnostics/redis"),
    ("POST", "/api/observability/client-errors"),
    # Authentication — must be reachable before a credential exists.
    ("POST", "/api/auth/register"), ("POST", "/api/auth/login"), ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/refresh"), ("POST", "/api/auth/forgot-password"),
    ("POST", "/api/auth/reset-password"), ("POST", "/api/auth/verify-email"),
    ("GET", "/api/auth/google/login-url"), ("POST", "/api/auth/google/session"),
    # Broker OAuth redirects — the browser arrives from the broker with no bearer
    # token; ownership is the server-side state record + cookie (D6.1 / S1).
    ("GET", "/api/brokers/{broker}/callback"), ("GET", "/api/zerodha/callback"),
    ("GET", "/api/zerodha/urls"),
    # Broker webhook — unauthenticated by protocol. Stores only; nothing reads
    # `zerodha_postbacks` (LIM-D6.8-4).
    ("POST", "/api/zerodha/postback"),
    # Scheduler webhooks — authenticated by `verify_webhook_key`, fail closed.
    ("POST", "/api/webhooks/morning-scan"), ("POST", "/api/webhooks/evening-summary"),
    ("POST", "/api/webhooks/weekly-review"), ("POST", "/api/webhooks/news-digest"),
    # Public market reference data (D5 — market data is a shared public good).
    # Some take `get_optional_user_id` to resolve a signed-in user's own feed.
    ("GET", "/api/market/activity-feed"), ("GET", "/api/market/calendar"),
    ("GET", "/api/market/commodities"), ("GET", "/api/market/engine/status"),
    ("GET", "/api/market/events"), ("GET", "/api/market/fii-dii"),
    ("GET", "/api/market/gainers"), ("GET", "/api/market/global"),
    ("GET", "/api/market/heatmap"), ("GET", "/api/market/losers"),
    ("GET", "/api/market/overview"), ("GET", "/api/market/ranking"),
    ("GET", "/api/market/scanner"), ("GET", "/api/market/scanner/presets"),
    ("GET", "/api/market/sector-analysis"), ("GET", "/api/market/sectors"),
    ("GET", "/api/stocks/search"), ("GET", "/api/stocks/universe"),
    ("GET", "/api/stocks/{symbol}"), ("GET", "/api/stocks/{symbol}/chart"),
    ("GET", "/api/stocks/{symbol}/financials"), ("GET", "/api/stocks/{symbol}/fundamentals"),
    ("GET", "/api/stocks/{symbol}/intraday"), ("GET", "/api/stocks/{symbol}/levels"),
    ("GET", "/api/stocks/{symbol}/live"), ("GET", "/api/stocks/{symbol}/patterns"),
    ("GET", "/api/stocks/{symbol}/peers"), ("GET", "/api/stocks/{symbol}/profile"),
    ("GET", "/api/stocks/{symbol}/risk"), ("GET", "/api/stocks/{symbol}/trade-setup"),
    ("GET", "/api/analysis/top-picks"),
    ("GET", "/api/news"), ("GET", "/api/news/refresh"), ("GET", "/api/news/sentiment"),
    ("GET", "/api/news/stock/{symbol}"),
    # Calculators over public data; backtest takes optional identity (D6.3).
    ("GET", "/api/sip/calculator"), ("GET", "/api/advisor/horizons"), ("POST", "/api/backtest"),
    # Status / metadata. None invokes a model (§4 sweep enforces it).
    ("GET", "/api/ai/status"), ("GET", "/api/gemini/status"), ("GET", "/api/ai/prompts"),
    # Platform-wide AI activity merged with the caller's own private entries via
    # optional identity (D6.1 / S4).
    ("GET", "/api/ai/activity"), ("GET", "/api/ai-activity"),
})


class TestThePublicSurfaceIsPinned:
    def test_the_derived_public_set_is_exactly_the_pinned_one(self):
        derived = frozenset(PUBLIC_ROUTES)
        newly_public = sorted(derived - PINNED_PUBLIC_ROUTES)
        no_longer_public = sorted(PINNED_PUBLIC_ROUTES - derived)
        assert not newly_public, (
            f"routes reachable with NO credential that nobody decided should be: "
            f"{newly_public}. Add Depends(get_current_user), or pin them here with a reason.")
        assert not no_longer_public, (
            f"pinned public routes that are now protected or gone: {no_longer_public}. "
            f"Remove them from PINNED_PUBLIC_ROUTES.")

    #: The details `get_current_user` raises. A public handler may legitimately
    #: answer 401 for its own reasons (`google/login-url` says "not configured"),
    #: so the credential dependency is identified by what it says, not the code.
    _AUTH_DETAILS = {"Not authenticated", "Token expired", "Invalid token", "User not found"}

    #: Pinned GETs that do not reach a market or news provider, so they can be
    #: exercised hermetically. The rest are covered by the set equality above.
    _PROVIDER_FREE = sorted(
        (m, p) for m, p in PINNED_PUBLIC_ROUTES
        if m == "GET" and not p.startswith(("/api/market/", "/api/news", "/api/stocks",
                                            "/api/analysis", "/api/sip", "/api/advisor")))

    @pytest.mark.parametrize("entry", _PROVIDER_FREE, ids=lambda e: e[1])
    def test_a_pinned_route_is_not_behind_the_credential_dependency(self, client, fake_db, entry):
        """A pin that names a route which demands a credential anyway is stale,
        and a stale pin is where a reclassified route would hide."""
        from tests._routes import sample_path
        resp = client.get(sample_path(entry[1]))
        detail = resp.json().get("detail") if resp.headers.get("content-type", "").startswith(
            "application/json") and isinstance(resp.json(), dict) else None
        assert not (resp.status_code == 401 and detail in self._AUTH_DETAILS), \
            f"pinned public route {entry[1]} demanded a credential"

    @pytest.mark.parametrize("path", sorted(p for m, p in PINNED_PUBLIC_ROUTES
                                            if p.startswith("/api/webhooks/")))
    def test_scheduler_webhooks_fail_closed_without_their_key(self, client, fake_db, monkeypatch, path):
        monkeypatch.delenv("WEBHOOK_API_KEY", raising=False)
        assert client.post(path, headers={"X-Webhook-Key": "anything"}).status_code == 403
        monkeypatch.setenv("WEBHOOK_API_KEY", "the-real-key")
        assert client.post(path, headers={"X-Webhook-Key": "not-the-key"}).status_code == 403
        assert client.post(path).status_code == 403


# =========================================================================== #
# §4 — PAID MODEL CALLS REQUIRE IDENTITY (F-5)                                 #
# =========================================================================== #
#: The five handlers F-5 moved behind authentication.
_MODEL_INVOKING_ROUTES = [
    ("GET", "/api/market/summary", None),
    ("POST", "/api/analysis/explain", {"symbol": "RELIANCE", "force": True}),
    ("GET", "/api/analysis/morning-report", None),
    ("POST", "/api/analysis/full-report", {"symbol": "RELIANCE"}),
    ("GET", "/api/gemini/market-pulse", None),
]

#: Names whose appearance in a handler's source means it can invoke a model.
_MODEL_CALL = re.compile(
    r"ai_dual_debate|ai_chat\(|simple_chat|ai_market_summary\(|get_debate_engine\(|"
    r"gemini_(?:analyze|market_pulse|realtime_analysis)|router\.run\(")

#: Public handlers that mention a model name without invoking one, or that are
#: authenticated by something other than a user. Each is a decision.
_PUBLIC_MODEL_EXEMPT = {
    ("GET", "/api/ai/status"): "reports router health; invokes nothing",
    ("GET", "/api/gemini/status"): "reports configuration; invokes nothing",
    ("POST", "/api/webhooks/morning-scan"): "verify_webhook_key",
    ("POST", "/api/webhooks/weekly-review"): "verify_webhook_key",
}


class TestPaidModelCallsRequireIdentity:
    @pytest.mark.parametrize("method,path,body", _MODEL_INVOKING_ROUTES,
                             ids=[p for _, p, _ in _MODEL_INVOKING_ROUTES])
    def test_anonymous_callers_are_refused_before_any_model_is_touched(
            self, client, fake_db, monkeypatch, method, path, body):
        touched = []

        def _trip(name):
            async def _spy(*a, **k):
                touched.append(name)
                return {}
            return _spy

        monkeypatch.setattr(server, "ai_dual_debate", _trip("ai_dual_debate"))
        monkeypatch.setattr(server, "ai_market_summary", _trip("ai_market_summary"))
        monkeypatch.setattr(server, "get_debate_engine",
                            lambda: (_ for _ in ()).throw(AssertionError("engine touched")))
        monkeypatch.setattr(server, "real_quote", _trip("real_quote"))
        monkeypatch.setattr(server, "real_overview", _trip("real_overview"))
        import services.gemini_direct as gd
        monkeypatch.setattr(gd, "gemini_market_pulse", _trip("gemini_market_pulse"))

        resp = client.request(method, path, json=body)
        assert resp.status_code == 401, resp.text
        assert touched == []

    def test_force_on_explain_is_honoured_for_a_signed_in_caller(
            self, client, fake_db, auth_headers, monkeypatch):
        """Positive control for the route that F-5 most constrains."""
        calls = []

        async def _debate(prompt, session):
            calls.append(session)
            return {"providers_active": []}

        monkeypatch.setattr(server, "ai_dual_debate", _debate)
        monkeypatch.setattr(server, "real_quote", AsyncMock(return_value={
            "price": 1.0, "rsi": 50, "volume_ratio": 1.0, "sector": "X", "macd": 0,
            "vwap": 1.0, "name": "Reliance"}))
        resp = client.post("/api/analysis/explain", json={"symbol": "RELIANCE", "force": True},
                           headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert calls == ["explain-RELIANCE"]

    def test_no_public_route_can_invoke_a_model(self):
        """The mechanical half: a sixth public model route fails here by name."""
        import inspect

        from fastapi.routing import APIRoute
        offenders = []
        public = set(PUBLIC_ROUTES)
        for route in server.app.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods - {"HEAD", "OPTIONS"}:
                key = (method, route.path)
                if key not in public or key in _PUBLIC_MODEL_EXEMPT:
                    continue
                if _MODEL_CALL.search(inspect.getsource(route.endpoint)):
                    offenders.append(key)
        assert offenders == [], f"public routes that invoke a model: {sorted(offenders)}"

    def test_the_sweep_would_have_caught_the_original_five(self):
        """The regex is the instrument; prove it matches the handlers F-5 fixed."""
        import inspect
        for fn in (server.market_summary, server.explain_stock, server.morning_report,
                   server.full_ai_report, server.gemini_market_pulse_endpoint):
            assert _MODEL_CALL.search(inspect.getsource(fn)), fn.__name__


# =========================================================================== #
# §5 — A CONVERSATION IS (USER, LABEL) (F-1)                                    #
# =========================================================================== #
@pytest.fixture
def quiet_chat(monkeypatch, no_ai):
    """Deterministic, network-free chat: empty live context, no provider."""
    import services.ai_context_builder as acb
    monkeypatch.setattr(acb, "build_chat_context",
                        AsyncMock(return_value=SimpleNamespace(text="")))


def _chat(client, headers, message, session_id=None):
    body = {"message": message}
    if session_id is not None:
        body["session_id"] = session_id
    return client.post("/api/chat", json=body, headers=headers)


class TestAConversationIsOwnedByItsUserNotItsLabel:
    def test_the_response_does_not_depend_on_another_users_use_of_a_label(
            self, client, fake_db, test_user, other_user, other_headers, quiet_chat):
        """Phase 13. The oracle was 403-vs-200. Now the used label and a fresh
        label are indistinguishable in status, shape, and stored effect."""
        used = f"chat-{test_user['_id']}"
        fake_db.chat_messages.docs.append({
            "_id": ObjectId(), "user_id": str(test_user["_id"]), "session_id": used,
            "role": "user", "content": "A-PRIVATE", "created_at": "2026-01-01T00:00:00+00:00"})
        fresh = f"chat-{ObjectId()}"

        r_used = _chat(client, other_headers, "probe", used)
        r_fresh = _chat(client, other_headers, "probe", fresh)

        assert r_used.status_code == r_fresh.status_code == 200
        assert set(r_used.json()) == set(r_fresh.json())
        assert r_used.json()["response"] == r_fresh.json()["response"]
        assert "A-PRIVATE" not in r_used.text
        b_rows = [d for d in fake_db.chat_messages.docs if d["user_id"] == str(other_user["_id"])]
        assert sorted(d["session_id"] for d in b_rows) == sorted([used, used, fresh, fresh])

    def test_squatting_a_victims_default_label_does_not_lock_them_out(
            self, client, fake_db, test_user, auth_headers, other_headers, quiet_chat):
        """Reproduced before F-1: B posted first to `chat-<A>`, and A's own
        default chat answered 403 permanently."""
        victim_default = f"chat-{test_user['_id']}"
        assert _chat(client, other_headers, "squat", victim_default).status_code == 200
        resp = _chat(client, auth_headers, "hello")
        assert resp.status_code == 200, resp.text
        assert resp.json()["session_id"] == victim_default
        history = client.get(f"/api/chat/history?session_id={victim_default}",
                             headers=auth_headers).json()
        assert [m["content"] for m in history if m["role"] == "user"] == ["hello"]

    def test_two_users_on_the_same_client_minted_label_stay_separate(
            self, client, fake_db, test_user, other_user, auth_headers, other_headers, quiet_chat):
        """The SPA mints `chat-<epoch ms>` with no user in it; two users in one
        millisecond share a label."""
        label = "chat-1757750400000"
        assert _chat(client, auth_headers, "A says", label).status_code == 200
        assert _chat(client, other_headers, "B says", label).status_code == 200
        a_hist = client.get(f"/api/chat/history?session_id={label}", headers=auth_headers).json()
        b_hist = client.get(f"/api/chat/history?session_id={label}", headers=other_headers).json()
        assert "B says" not in repr(a_hist) and "A says" not in repr(b_hist)
        a_list = client.get("/api/ai/conversations", headers=auth_headers).json()
        assert [c["title"] for c in a_list] == ["A says"]

    def test_deleting_a_shared_label_removes_only_the_callers_rows_and_counts_them(
            self, client, fake_db, auth_headers, other_headers, quiet_chat):
        label = "chat-shared"
        _chat(client, auth_headers, "A1", label)
        _chat(client, other_headers, "B1", label)
        resp = client.delete(f"/api/ai/conversations/{label}", headers=other_headers)
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2, "deleted_count was misread (modified_count)"
        remaining = [d["content"] for d in fake_db.chat_messages.docs]
        assert "B1" not in remaining and "A1" in remaining

    def test_the_fake_delete_result_has_the_drivers_shape(self, fake_db):
        """The double used to expose `modified_count` on deletes, which pymongo's
        DeleteResult does not — so the misread count passed here and failed live."""
        import asyncio

        from pymongo.results import DeleteResult
        res = asyncio.run(fake_db.chat_messages.delete_many({}))
        real = DeleteResult({"n": 0}, True)
        assert hasattr(res, "deleted_count") and hasattr(real, "deleted_count")
        assert hasattr(res, "modified_count") == hasattr(real, "modified_count") is False


# =========================================================================== #
# §6 — PAPER AND LIVE TRADES CANNOT CROSS (F-4)                                #
# =========================================================================== #
_TRADE = {"symbol": "INFY", "stock_name": "Infosys", "type": "BUY", "entry_price": 100.0,
          "quantity": 50, "stop_loss": 98.0, "target1": 104.0, "override_warnings": True}


@pytest.fixture
def adapter_spy(monkeypatch):
    """Replace every registered adapter's irreversible order methods with a spy.

    The spy sits BELOW `BrokerGateway.require_capability` and below
    `BrokerEngine.get_session`: an entry here means the request reached the
    point where a real adapter would have spoken to the broker.
    """
    calls = []
    for name in broker_registry.names():
        adapter = broker_registry.require(name)

        def _make(method, broker):
            async def _spy(session, *args, **kwargs):
                calls.append((broker, method, (session or {}).get("access_token")))
                return {"order_id": f"SPY-{broker}-{len(calls)}", "status": "PENDING"}
            return _spy

        for method in ("place_order", "modify_order", "cancel_order"):
            monkeypatch.setattr(adapter, method, _make(method, name), raising=False)
    broker_engine._sessions.clear()
    broker_engine._session_touched.clear()
    yield calls
    broker_engine._sessions.clear()
    broker_engine._session_touched.clear()


class TestPaperAndLiveTradesCannotCross:
    def test_the_trades_endpoint_refuses_is_paper_and_writes_nothing(
            self, authenticated_client, fake_db, test_user, adapter_spy):
        resp = authenticated_client.post("/api/trades", json={**_TRADE, "is_paper": True})
        assert resp.status_code == 422, resp.text
        assert fake_db.trades.docs == []
        assert "paper_capital" not in _stored(fake_db, "users", test_user["_id"])
        assert adapter_spy == []

    def test_the_reproduced_inflation_no_longer_reproduces(
            self, authenticated_client, fake_db, monkeypatch):
        """Before F-4: two zero-P&L round trips took paper capital 1,00,000 → 1,10,000."""
        from services import real_market
        monkeypatch.setattr(real_market, "fetch_real_stock_quote",
                            AsyncMock(return_value={"price": 100.0}))
        for _ in range(2):
            created = authenticated_client.post("/api/trades", json={**_TRADE, "is_paper": True})
            assert created.status_code == 422
        assert authenticated_client.get("/api/paper/balance").json()["balance"] == 100000.0

    def test_is_paper_with_a_live_account_never_reaches_the_adapter(
            self, client, fake_db, test_user, adapter_spy):
        uid = str(test_user["_id"])
        doc = account_doc(uid, "upstox", external_account_id="OWN")
        fake_db.broker_accounts.docs.append({**doc, "access_token": "TOKEN-OWN",
                                             "expires_at": "2099-01-01T00:00:00+00:00"})
        resp = client.post("/api/trades", json={
            **_TRADE, "is_paper": True, "auto_exit": True,
            "broker_account_id": doc["broker_account_id"]}, headers=_headers_for(test_user))
        assert resp.status_code == 422
        assert adapter_spy == [], "a real order was placed for a trade labelled paper"
        assert fake_db.trades.docs == []

    def test_validate_is_still_a_dry_run_that_accepts_the_field(self, authenticated_client, fake_db):
        """The load harness sends `is_paper` to /validate; it writes nothing."""
        resp = authenticated_client.post("/api/trades/validate", json={**_TRADE, "is_paper": True})
        assert resp.status_code == 200
        assert fake_db.trades.docs == []

    def test_a_live_manual_trade_is_still_accepted(self, authenticated_client, fake_db, adapter_spy):
        """Positive control: F-4 refuses the flag, not the endpoint."""
        resp = authenticated_client.post("/api/trades", json=_TRADE)
        assert resp.status_code == 200, resp.text
        assert fake_db.trades.docs[0]["is_paper"] is False
        assert adapter_spy == []

    def test_paper_trading_state_never_crosses_users(
            self, client, fake_db, test_user, other_user, auth_headers, other_headers, monkeypatch):
        """Phase 11: B cannot close, reset or read A's paper state; A's balance is
        untouched by B's operations; the refusal matches a nonexistent id."""
        from services import real_market
        monkeypatch.setattr(real_market, "fetch_real_stock_quote",
                            AsyncMock(return_value={"price": 120.0}))
        opened = client.post("/api/paper/trade", json={
            "symbol": "TCS", "quantity": 10, "entry_price": 100.0, "stop_loss": 95.0,
            "target1": 110.0}, headers=auth_headers)
        assert opened.status_code == 200, opened.text
        a_trade = opened.json()["_id"]
        a_balance = client.get("/api/paper/balance", headers=auth_headers).json()["balance"]

        foreign = client.post(f"/api/paper/close/{a_trade}", headers=other_headers)
        missing = client.post(f"/api/paper/close/{ObjectId()}", headers=other_headers)
        assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json())
        assert client.post("/api/paper/reset", headers=other_headers).status_code == 200

        a_row = next(t for t in fake_db.trades.docs if str(t["_id"]) == a_trade)
        assert a_row["status"] == "OPEN"
        assert client.get("/api/paper/balance", headers=auth_headers).json()["balance"] == a_balance
        assert all(t["symbol"] != "TCS" for t in
                   client.get("/api/paper/trades", headers=other_headers).json())
        closed = client.post(f"/api/paper/close/{a_trade}", headers=auth_headers)
        assert closed.status_code == 200, "positive control: the owner can close it"


# =========================================================================== #
# §7 — ORDER AUTHORIZATION STOPS BEFORE THE ADAPTER                            #
# =========================================================================== #
_ORDER = {"symbol": "RELIANCE", "exchange": "NSE", "transaction_type": "BUY",
          "quantity": 1, "order_type": "MARKET"}
_FUTURE = "2099-01-01T00:00:00+00:00"
_PAST = "2020-01-01T00:00:00+00:00"


@pytest.fixture
def world(fake_db, test_user, other_user):
    """A and B, the account shapes Phase 8 names.

    A: upstox #1 (live), upstox #2 (session expired), upstox #3 (REVOKED with a
       still-fresh token), zerodha (live, sole — so the broker-name bridge
       resolves), angelone (live, cannot place orders).
    B: upstox (live), zerodha (live).
    """
    a, b = str(test_user["_id"]), str(other_user["_id"])

    def _add(owner, broker, suffix, ext, *, expires=_FUTURE, status=BrokerAccountStatus.CONNECTED):
        doc = account_doc(owner, broker, suffix=suffix, external_account_id=ext, status=status)
        fake_db.broker_accounts.docs.append({
            "_id": ObjectId(), **doc, "access_token": f"TOKEN-{ext}", "expires_at": expires,
            "connected_at": "2026-09-13T03:00:00+00:00"})
        return doc["broker_account_id"]

    return {
        "A_UPX": _add(a, "upstox", "1", "A-UPX"),
        "A_UPX_EXPIRED": _add(a, "upstox", "2", "A-UPX-OLD", expires=_PAST),
        "A_UPX_REVOKED": _add(a, "upstox", "3", "A-UPX-REV", status=BrokerAccountStatus.REVOKED),
        "A_KITE": _add(a, "zerodha", "", "A-KITE"),
        "A_ANGEL": _add(a, "angelone", "", "A-ANGEL"),
        "B_UPX": _add(b, "upstox", "", "B-UPX"),
        "B_KITE": _add(b, "zerodha", "", "B-KITE"),
        "UNKNOWN": fixture_account_id("nobody", "upstox", "ghost"),
    }


class TestOrderAuthorizationStopsBeforeTheAdapter:
    def test_positive_control_the_owners_live_account_reaches_the_adapter_once(
            self, client, world, test_user, adapter_spy):
        resp = client.post(f"/api/brokers/accounts/{world['A_UPX']}/orders", json=_ORDER,
                           headers=_headers_for(test_user))
        assert resp.status_code == 200, resp.text
        assert adapter_spy == [("upstox", "place_order", "TOKEN-A-UPX")]

    @pytest.mark.parametrize("target,expected", [
        ("B_UPX", 404), ("B_KITE", 404), ("UNKNOWN", 404),
        # A capability refusal is a BrokerError, which the app maps to 502
        # (LIM-D6.8-6: a permanent refusal reported as a gateway failure).
        ("A_ANGEL", 502), ("A_UPX_EXPIRED", 409), ("A_UPX_REVOKED", 409),
    ])
    def test_account_addressed_placement_is_refused_before_the_adapter(
            self, client, world, test_user, adapter_spy, target, expected):
        resp = client.post(f"/api/brokers/accounts/{world[target]}/orders", json=_ORDER,
                           headers=_headers_for(test_user))
        assert resp.status_code == expected, resp.text
        assert adapter_spy == [], f"{target}: place_order_called"

    @pytest.mark.parametrize("target", ["B_UPX", "B_KITE", "UNKNOWN", "A_UPX_EXPIRED",
                                        "A_UPX_REVOKED"])
    @pytest.mark.parametrize("verb", ["PATCH", "DELETE"])
    def test_modify_and_cancel_are_refused_before_the_adapter(
            self, client, world, test_user, adapter_spy, target, verb):
        body = {"quantity": 2} if verb == "PATCH" else None
        resp = client.request(verb, f"/api/brokers/accounts/{world[target]}/orders/ORD-1",
                              json=body, headers=_headers_for(test_user))
        assert resp.status_code in (404, 409), resp.text
        assert adapter_spy == []

    def test_forged_authority_in_the_body_cannot_redirect_the_order(
            self, client, world, test_user, other_user, adapter_spy):
        """Phase 4: user_id, broker, broker_account_id, admin, capability and
        entitlement fields in the JSON do not change which account is used."""
        resp = client.post(f"/api/brokers/accounts/{world['A_UPX']}/orders", json={
            **_ORDER, **_FORGED_AUTHORITY, "user_id": str(other_user["_id"]),
            "broker": "zerodha", "broker_account_id": world["B_KITE"]},
            headers=_headers_for(test_user))
        assert resp.status_code == 200, resp.text
        assert adapter_spy == [("upstox", "place_order", "TOKEN-A-UPX")]

    @pytest.mark.parametrize("extra,expected", [
        ({"broker_account_id": "B_UPX"}, 404),
        ({"broker_account_id": "B_KITE", "user_id": "B"}, 404),
        # M14: an account id that does not resolve must not fall back to the
        # broker name, even though A holds exactly one zerodha account.
        ({"broker_account_id": "B_KITE", "broker": "zerodha"}, 404),
        ({"broker_account_id": "UNKNOWN", "broker": "zerodha"}, 404),
        ({"broker_account_id": "A_ANGEL"}, 502),
        # `create_trade` wraps every BrokerError, auth included, as 502
        # "Order was not placed".
        ({"broker_account_id": "A_UPX_EXPIRED"}, 502),
        ({"broker_account_id": "A_UPX_REVOKED"}, 502),
        # Ambiguous bridge: three upstox accounts, no id → refuse, never pick.
        ({"broker": "upstox"}, 409),
        # A named broker the caller holds no account at, though B does.
        ({"broker": "fyers"}, 409),
    ])
    def test_the_trade_form_path_is_refused_before_the_adapter(
            self, client, fake_db, world, test_user, other_user, adapter_spy, extra, expected):
        body = {**_TRADE}
        for key, value in extra.items():
            if key == "broker_account_id":
                value = world[value]
            if key == "user_id":
                value = str(other_user["_id"])
            body[key] = value
        resp = client.post("/api/trades", json=body, headers=_headers_for(test_user))
        assert resp.status_code == expected, resp.text
        assert adapter_spy == []
        assert fake_db.trades.docs == [], "a trade row was written for a refused order"

    def test_a_risk_violation_is_refused_before_the_adapter(self, client, world, test_user, adapter_spy):
        resp = client.post("/api/trades", json={**_TRADE, "stop_loss": 150.0,
                                                "broker_account_id": world["A_UPX"]},
                           headers=_headers_for(test_user))
        assert resp.status_code == 422
        assert adapter_spy == []

    def test_broker_name_route_for_a_broker_only_another_user_holds(
            self, client, fake_db, world, test_user, adapter_spy):
        """A holds no fyers account; nobody's is borrowed."""
        resp = client.post("/api/brokers/fyers/orders", json=_ORDER, headers=_headers_for(test_user))
        assert resp.status_code == 409
        assert adapter_spy == []

    def test_capability_on_one_account_does_not_transfer_to_a_sibling(
            self, client, world, test_user, adapter_spy):
        """Phase 8: A_KITE can place orders; A_ANGEL (same owner) cannot, and the
        refusal is not satisfied by falling through to the capable sibling."""
        resp = client.post(f"/api/brokers/accounts/{world['A_ANGEL']}/orders", json=_ORDER,
                           headers=_headers_for(test_user))
        assert resp.status_code == 502
        assert resp.json()["code"] == "BROKER_UNSUPPORTED"
        assert adapter_spy == []

    def test_the_owner_of_the_other_account_is_refused_symmetrically(
            self, client, world, other_user, adapter_spy):
        """B + A's account → denied, for every one of A's accounts."""
        for key in ("A_UPX", "A_KITE", "A_ANGEL", "A_UPX_EXPIRED", "A_UPX_REVOKED"):
            resp = client.post(f"/api/brokers/accounts/{world[key]}/orders", json=_ORDER,
                               headers=_headers_for(other_user))
            assert resp.status_code == 404, (key, resp.text)
        assert adapter_spy == []


# =========================================================================== #
# §8 — WITHDRAWN BROKER ACCOUNTS SERVE NO SESSION (F-6)                         #
# =========================================================================== #
def _run(coro):
    import asyncio
    return asyncio.run(coro)


class TestWithdrawnAccountsServeNoSession:
    @pytest.fixture(autouse=True)
    def _engine_db(self, fake_db):
        broker_engine._sessions.clear()
        broker_engine._session_touched.clear()
        yield
        broker_engine._sessions.clear()
        broker_engine._session_touched.clear()

    def _seed(self, fake_db, status, **extra):
        doc = account_doc("u-f6", "upstox", external_account_id="F6", status=status)
        row = {**doc, "access_token": "TOKEN-F6", "expires_at": _FUTURE, **extra}
        if status is None:
            row.pop("status")
        fake_db.broker_accounts.docs.append(row)
        return account_ref("u-f6", "upstox", external_account_id="F6",
                           status=status or BrokerAccountStatus.DISCONNECTED)

    @pytest.mark.parametrize("status", [BrokerAccountStatus.REVOKED, BrokerAccountStatus.DISCONNECTED])
    def test_a_withdrawn_state_is_refused_even_with_a_fresh_token(self, fake_db, status):
        ref = self._seed(fake_db, status)
        with pytest.raises(BrokerAuthError):
            _run(broker_engine.get_session(ref))
        assert ref.broker_account_id not in broker_engine._sessions

    def test_connected_with_a_fresh_token_is_served(self, fake_db):
        ref = self._seed(fake_db, BrokerAccountStatus.CONNECTED)
        assert _run(broker_engine.get_session(ref))["access_token"] == "TOKEN-F6"

    def test_a_legacy_row_with_no_status_is_judged_by_its_token(self, fake_db):
        """Explicit values only: ref_from_doc defaults a missing status to
        DISCONNECTED for display, and the guard must not read that default."""
        ref = self._seed(fake_db, None)
        assert _run(broker_engine.get_session(ref))["access_token"] == "TOKEN-F6"

    def test_reauth_required_is_left_to_the_broker(self, fake_db):
        """Recorded decision: calendar expiry already refuses an aged token, and
        a stream-side rejection must not cut REST access the broker still grants."""
        ref = self._seed(fake_db, BrokerAccountStatus.REAUTH_REQUIRED)
        assert _run(broker_engine.get_session(ref))["access_token"] == "TOKEN-F6"


# =========================================================================== #
# §9 — REALTIME PRIVATE CHANNELS                                               #
# =========================================================================== #
class TestRealtimePrivateChannels:
    _PRIVATE = ["trades", "portfolio", "broker", "notifications", "watchlist", "*"]

    def test_every_private_channel_and_the_wildcard_is_refused(self):
        manager = server.ConnectionManager()
        ws = object()
        accepted, refused = manager.subscribe(ws, self._PRIVATE + ["market"])
        assert accepted == ["market"]
        assert sorted(refused) == sorted(self._PRIVATE)
        assert manager.channels[ws] == {"market"}

    def test_the_private_set_is_derived_from_every_private_domain(self):
        from services.realtime.event_bridge import DOMAIN_CHANNEL, PRIVATE_CHANNELS, PRIVATE_DOMAINS
        assert PRIVATE_CHANNELS == {DOMAIN_CHANNEL[d] for d in PRIVATE_DOMAINS}
        assert set(self._PRIVATE) - {"*"} == set(PRIVATE_CHANNELS)

    def test_a_near_miss_channel_name_receives_no_private_event(self):
        """The subscribe list is a deny-list, so `Trades` is accepted — and must
        be inert: channel delivery is exact-match and private events never use
        channel delivery at all."""
        import asyncio

        from services.realtime import event_bridge
        sent = []

        class _WS:
            async def send_text(self, payload):
                sent.append(payload)

        manager = server.ConnectionManager()
        ws = _WS()
        manager.active.add(ws)
        accepted, _ = manager.subscribe(ws, ["Trades", "trades ", "trade", "portfolio.*"])
        assert len(accepted) == 4
        asyncio.run(event_bridge._deliver(manager, {
            "type": "trade.updated", "data": {"symbol": "X"}}))
        assert sent == [], "an ownerless private event reached a near-miss subscription"


# =========================================================================== #
# §10 — INDISTINGUISHABLE REFUSALS                                             #
# =========================================================================== #
class TestIndistinguishableRefusals:
    @pytest.mark.parametrize("suffix", ["", "/holdings", "/positions", "/funds", "/margins",
                                        "/orders", "/trades", "/profile"])
    def test_a_foreign_account_reads_exactly_like_a_nonexistent_one(
            self, client, world, test_user, adapter_spy, suffix):
        h = _headers_for(test_user)
        foreign = client.get(f"/api/brokers/accounts/{world['B_UPX']}{suffix}", headers=h)
        ghost = client.get(f"/api/brokers/accounts/{world['UNKNOWN']}{suffix}", headers=h)
        malformed = client.get(f"/api/brokers/accounts/upstox{suffix}", headers=h)
        assert (foreign.status_code, foreign.json()) == (ghost.status_code, ghost.json()) \
            == (malformed.status_code, malformed.json()) == (404, {"detail": "Broker account not found"})

    def test_the_account_list_never_names_another_users_account(self, client, world, test_user):
        body = client.get("/api/brokers/accounts", headers=_headers_for(test_user)).text
        for key in ("B_UPX", "B_KITE"):
            assert world[key] not in body
        assert "B-UPX" not in body and "B-KITE" not in body
        assert world["A_UPX"] in body, "positive control"

    def test_a_foreign_trade_reads_exactly_like_a_nonexistent_one(
            self, client, fake_db, test_user, other_user):
        oid = ObjectId()
        fake_db.trades.docs.append({"_id": oid, "user_id": str(other_user["_id"]), "symbol": "X",
                                    "status": "OPEN", "type": "BUY", "entry_price": 1.0,
                                    "quantity": 1, "stop_loss": 0.5, "target1": 2.0, "events": []})
        h = _headers_for(test_user)
        for method, tail, body in (("PUT", "", {"stop_loss": 0.6}),
                                   ("POST", "/exit", {"exit_price": 1.5, "quantity": 1}),
                                   ("GET", "/coaching", None)):
            r1 = client.request(method, f"/api/trades/{oid}{tail}", json=body, headers=h)
            r2 = client.request(method, f"/api/trades/{ObjectId()}{tail}", json=body, headers=h)
            assert (r1.status_code, r1.json()) == (r2.status_code, r2.json()), tail

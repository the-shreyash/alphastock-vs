"""Authentication and authorization coverage for the whole API surface (PH3.3).

WHAT PRODUCTION FAILURE THIS CATCHES
------------------------------------
The one that does not announce itself: an endpoint shipped without its
`Depends(get_current_user)` — or an admin endpoint shipped with
`get_current_user` where it needed `require_admin`. Nothing about such a route
looks wrong. It returns 200, its feature works, its own tests pass. It is simply
readable by anybody on the internet, and nobody finds out until someone else
does.

Three layers here, in increasing specificity:

1. **Mechanical sweeps** over the live route table (`tests/_routes.py`) — every
   authenticated route rejects anonymous callers; every admin route rejects
   ordinary users. These grow automatically with the API.
2. **Vertical escalation** — an `admin` cannot do the things reserved for a
   `super_admin`, including promoting themselves.
3. **Horizontal escalation** — an authenticated user cannot read or mutate
   another user's trades, notifications, watchlist or settings.

The PH1 security suites already cover *token* validity in depth (expiry,
signature, issuer, audience, type confusion, replay, revocation). This file
deliberately does not restate that; it covers the orthogonal question of whether
each *route* is wired to the checks PH1 built. §5 of the PH3.3 brief asks for
both, and duplicating the token matrix here would add lines without adding
safety.

A NOTE ON WHY THE SWEEPS ARE PARAMETRIZED
-----------------------------------------
One test per route rather than one loop over all of them, for two reasons. A
failure names the offending route in the pytest node id instead of stopping the
loop at the first one. And each parametrized case gets its own function-scoped
`fake_db`, hence its own rate-limit counter — a single test issuing 126
anonymous requests would trip the platform-wide 60/min anonymous limiter
partway through and start collecting 429s that have nothing to do with
authorization.
"""
import pytest
from bson import ObjectId

from tests._routes import (
    ADMIN_ROUTES,
    AUTHENTICATED_ROUTES,
    USER_PROTECTED_ROUTES,
    route_id,
    sample_path,
)


def _call(client, method, path, **kwargs):
    """Issue `method path`, sending an empty JSON body on mutating verbs.

    A body is supplied so a route with a Pydantic model does not answer 422
    before authentication is reached — that would make the sweep assert nothing.
    """
    if method in ("POST", "PUT", "PATCH"):
        kwargs.setdefault("json", {})
    return client.request(method, path, **kwargs)


# --------------------------------------------------------------------------- #
# 1. Anonymous access                                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entry", AUTHENTICATED_ROUTES, ids=route_id)
def test_authenticated_route_rejects_anonymous(client, fake_db, entry):
    """Every route behind a credential check answers 401 without one.

    401 exactly, not "any 4xx": a 403 would mean the request was authenticated
    and then refused, and a 404 would leak nothing but also tell us the
    dependency never ran. Only 401 proves the credential check itself fired.
    """
    method, path = entry
    resp = _call(client, method, sample_path(path))
    assert resp.status_code == 401, (
        f"{method} {path} answered {resp.status_code} to an anonymous caller; "
        f"expected 401. Is Depends(get_current_user) missing?"
    )
    assert "detail" in resp.json()


@pytest.mark.parametrize("entry", AUTHENTICATED_ROUTES, ids=route_id)
def test_authenticated_route_rejects_garbage_bearer_token(client, fake_db, entry):
    """A syntactically invalid bearer token is rejected everywhere, as 401.

    The failure this guards is a route that treats "a token was presented" as
    "the caller is authenticated" — which passes the anonymous sweep above and
    is still completely open.
    """
    method, path = entry
    resp = _call(client, method, sample_path(path),
                 headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 401, (
        f"{method} {path} answered {resp.status_code} to a forged token; expected 401."
    )


def test_the_sweep_is_actually_covering_something():
    """Guard against the sweeps silently emptying.

    If `_routes.py` ever fails to classify routes (a FastAPI upgrade changing
    the `dependant` shape, say), every parametrized test above would vanish and
    the suite would go green while asserting nothing. This test fails instead.
    """
    assert len(USER_PROTECTED_ROUTES) > 50, USER_PROTECTED_ROUTES
    assert len(ADMIN_ROUTES) > 20, ADMIN_ROUTES


# --------------------------------------------------------------------------- #
# 2. Vertical escalation — user → admin                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entry", ADMIN_ROUTES, ids=route_id)
def test_admin_route_rejects_ordinary_user(client, fake_db, auth_headers, entry):
    """403, not 401: the caller is authenticated, just not entitled."""
    method, path = entry
    resp = _call(client, method, sample_path(path), headers=auth_headers)
    assert resp.status_code == 403, (
        f"{method} {path} answered {resp.status_code} to a role='user' caller; "
        f"expected 403. Is Depends(require_admin) missing?"
    )


@pytest.mark.parametrize("role", ["free", "pro", "elite", "lifetime", "beta_tester"])
def test_paid_plan_roles_are_not_admin(client, fake_db, role):
    """A subscription tier is an entitlement, never a control-plane privilege.

    `users.role` carries both plan tiers and admin tiers in one field. That
    conflation is the standing risk: any code path that grants a plan is one
    typo away from granting `admin`. This pins the boundary from the other side.
    """
    from tests.conftest import _headers_for, _seed_user
    user = _seed_user(fake_db, f"{role}@example.com", role, f"{role} user")
    resp = client.get("/api/admin/dashboard", headers=_headers_for(user))
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# 3. Vertical escalation — admin → super_admin                                  #
# --------------------------------------------------------------------------- #
class TestSuperAdminBoundary:
    """Only a super_admin may delete accounts or mint admin-tier roles."""

    def test_admin_cannot_delete_a_user(self, admin_client, fake_db, other_user):
        resp = admin_client.delete(f"/api/admin/users/{other_user['_id']}")
        assert resp.status_code == 403
        assert any(u["_id"] == other_user["_id"] for u in fake_db.users.docs), \
            "the account was deleted despite the 403"

    def test_super_admin_can_delete_a_user(self, super_admin_client, fake_db, other_user):
        resp = super_admin_client.delete(f"/api/admin/users/{other_user['_id']}")
        assert resp.status_code == 200
        assert not any(u["_id"] == other_user["_id"] for u in fake_db.users.docs)

    @pytest.mark.parametrize("target_role", ["admin", "super_admin"])
    def test_admin_cannot_grant_an_admin_tier_role(
            self, admin_client, fake_db, other_user, target_role):
        resp = admin_client.put(f"/api/admin/users/{other_user['_id']}",
                                json={"role": target_role})
        assert resp.status_code == 403
        stored = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert stored["role"] == "user", "role was elevated despite the 403"

    def test_admin_cannot_promote_themselves(self, admin_client, fake_db, admin_user):
        """The self-elevation path: the one an attacker with a stolen admin
        session reaches for first."""
        resp = admin_client.put(f"/api/admin/users/{admin_user['_id']}",
                                json={"role": "super_admin"})
        assert resp.status_code == 403
        stored = next(u for u in fake_db.users.docs if u["_id"] == admin_user["_id"])
        assert stored["role"] == "admin"

    def test_super_admin_can_grant_an_admin_tier_role(
            self, super_admin_client, fake_db, other_user):
        resp = super_admin_client.put(f"/api/admin/users/{other_user['_id']}",
                                      json={"role": "admin"})
        assert resp.status_code == 200
        stored = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert stored["role"] == "admin"

    def test_admin_may_still_grant_a_plan_role(self, admin_client, fake_db, other_user):
        """Plan administration is ordinary account admin and must keep working —
        the escalation guard above must not have over-blocked it."""
        resp = admin_client.put(f"/api/admin/users/{other_user['_id']}",
                                json={"role": "pro"})
        assert resp.status_code == 200
        stored = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert stored["role"] == "pro"

    def test_grant_plan_endpoint_refuses_admin_tier_roles(
            self, admin_client, fake_db, other_user):
        """`grant-plan` has its own allowlist; `admin` must not be in it."""
        resp = admin_client.post(f"/api/admin/users/{other_user['_id']}/grant-plan",
                                 json={"plan": "admin"})
        assert resp.status_code == 400
        stored = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert stored["role"] == "user"


# --------------------------------------------------------------------------- #
# 4. Horizontal escalation — user A vs user B                                   #
# --------------------------------------------------------------------------- #
class TestTradeOwnership:
    """A trade is readable and mutable only by the user who owns it."""

    @pytest.fixture
    def victim_trade(self, fake_db, other_user):
        trade = {
            "_id": ObjectId(),
            "user_id": str(other_user["_id"]),
            "symbol": "TESTCO",
            "type": "BUY",
            "entry_price": 100.0,
            "quantity": 10,
            "quantity_open": 10,
            "realized_pnl": 0.0,
            "stop_loss": 90.0,
            "target1": 120.0,
            "status": "OPEN",
            "events": [],
        }
        fake_db.trades.docs.append(trade)
        return trade

    def test_list_excludes_other_users_trades(
            self, authenticated_client, fake_db, test_user, victim_trade):
        resp = authenticated_client.get("/api/trades")
        assert resp.status_code == 200
        assert resp.json() == [], "another user's trade appeared in the list"

    def test_cannot_modify_another_users_trade(
            self, authenticated_client, fake_db, test_user, victim_trade):
        resp = authenticated_client.put(f"/api/trades/{victim_trade['_id']}",
                                        json={"stop_loss": 95.0})
        assert resp.status_code == 404, \
            "404, not 403 — an owner check must not confirm the resource exists"
        stored = next(t for t in fake_db.trades.docs if t["_id"] == victim_trade["_id"])
        assert stored["stop_loss"] == 90.0, "the trade was modified across the ownership boundary"

    def test_cannot_exit_another_users_trade(
            self, authenticated_client, fake_db, test_user, victim_trade):
        resp = authenticated_client.post(f"/api/trades/{victim_trade['_id']}/exit",
                                         json={"exit_price": 130.0, "quantity": 10})
        assert resp.status_code == 404
        stored = next(t for t in fake_db.trades.docs if t["_id"] == victim_trade["_id"])
        assert stored["status"] == "OPEN", "the trade was closed across the ownership boundary"

    def test_cannot_read_another_users_trade_coaching(
            self, authenticated_client, fake_db, test_user, victim_trade):
        resp = authenticated_client.get(f"/api/trades/{victim_trade['_id']}/coaching")
        assert resp.status_code == 404


class TestNotificationOwnership:
    def test_cannot_mark_another_users_notification_read(
            self, authenticated_client, fake_db, test_user, other_user):
        notif = {"_id": ObjectId(), "user_id": str(other_user["_id"]),
                 "title": "TEST theirs", "read": False}
        fake_db.notifications.docs.append(notif)
        resp = authenticated_client.put(f"/api/notifications/{notif['_id']}/read")
        # The security property — the row is untouched — held even before PH3.3,
        # because `user_id` is part of the update filter. The status code did
        # not: the miss answered 200 "Marked as read" (defect D-2), making an
        # ownership rejection indistinguishable from a successful update.
        assert resp.status_code == 404
        stored = next(n for n in fake_db.notifications.docs if n["_id"] == notif["_id"])
        assert stored["read"] is False

    def test_marking_own_notification_read_still_succeeds(
            self, authenticated_client, fake_db, test_user):
        """The D-2 fix must not have made the happy path 404."""
        notif = {"_id": ObjectId(), "user_id": str(test_user["_id"]),
                 "title": "TEST mine", "read": False}
        fake_db.notifications.docs.append(notif)
        resp = authenticated_client.put(f"/api/notifications/{notif['_id']}/read")
        assert resp.status_code == 200
        assert fake_db.notifications.docs[0]["read"] is True

    def test_unknown_notification_id_is_404(self, authenticated_client, fake_db, test_user):
        resp = authenticated_client.put(f"/api/notifications/{ObjectId()}/read")
        assert resp.status_code == 404


class TestWatchlistOwnership:
    def test_list_is_scoped_to_the_caller(
            self, authenticated_client, fake_db, test_user, other_user, monkeypatch):
        import server
        from unittest.mock import AsyncMock
        monkeypatch.setattr(server, "real_quotes_map", AsyncMock(return_value={}))
        fake_db.watchlist.docs.extend([
            {"_id": ObjectId(), "user_id": str(test_user["_id"]), "symbol": "MINE"},
            {"_id": ObjectId(), "user_id": str(other_user["_id"]), "symbol": "THEIRS"},
        ])
        resp = authenticated_client.get("/api/watchlist")
        assert resp.status_code == 200
        assert [i["symbol"] for i in resp.json()] == ["MINE"]

    def test_delete_cannot_reach_another_users_entry(
            self, authenticated_client, fake_db, test_user, other_user):
        fake_db.watchlist.docs.append(
            {"_id": ObjectId(), "user_id": str(other_user["_id"]), "symbol": "THEIRS"})
        resp = authenticated_client.delete("/api/watchlist/THEIRS")
        assert resp.status_code == 404
        assert len(fake_db.watchlist.docs) == 1, "another user's watchlist row was deleted"


class TestPaperTradingOwnership:
    def test_paper_trades_are_scoped_to_the_caller(
            self, authenticated_client, fake_db, test_user, other_user):
        fake_db.paper_trades.docs.append(
            {"_id": ObjectId(), "user_id": str(other_user["_id"]), "symbol": "THEIRS",
             "status": "OPEN", "quantity": 1, "entry_price": 10.0})
        resp = authenticated_client.get("/api/paper/trades")
        assert resp.status_code == 200
        body = resp.json()
        rows = body if isinstance(body, list) else body.get("trades", [])
        assert not any(r.get("symbol") == "THEIRS" for r in rows)


class TestProfileIsolation:
    def test_me_returns_the_caller_and_never_a_password_hash(
            self, authenticated_client, test_user):
        resp = authenticated_client.get("/api/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == test_user["email"]
        assert "password_hash" not in body

    def test_settings_update_only_touches_the_caller(
            self, authenticated_client, fake_db, test_user, other_user):
        resp = authenticated_client.put("/api/settings", json={"capital": 555000.0})
        assert resp.status_code == 200
        victim = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert victim["capital"] == 100000.0, "another account's settings were modified"

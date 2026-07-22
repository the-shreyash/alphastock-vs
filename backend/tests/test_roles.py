"""Tests for security.roles (PH1.12 / finding F-1) — role taxonomy & the
least-privilege guard on role assignment — plus an end-to-end regression test
proving the privilege-escalation path in the admin user editor is closed.

The vulnerability (F-1): `PUT /api/admin/users/{id}` accepted `role` as an
unchecked passthrough field, so any admin could promote any account — including
themselves — to `admin` or `super_admin`. The fix routes every role write
through `validate_role_assignment`, which (a) allowlists the value and (b) lets
only a super_admin grant the admin-tier roles.
"""
import pytest
from bson import ObjectId
from fastapi import HTTPException

from security.roles import (
    ADMIN_TIER_ROLES,
    ASSIGNABLE_ROLES,
    PLAN_ROLES,
    is_admin_tier,
    validate_role_assignment,
)


# ----------------------------- unit: taxonomy -----------------------------

def test_admin_tier_is_admin_and_super_admin():
    assert ADMIN_TIER_ROLES == {"admin", "super_admin"}


def test_is_admin_tier():
    assert is_admin_tier("admin")
    assert is_admin_tier("super_admin")
    assert not is_admin_tier("pro")
    assert not is_admin_tier("user")
    assert not is_admin_tier("")


def test_assignable_roles_include_user_plans_and_admin_tier():
    assert "user" in ASSIGNABLE_ROLES
    assert PLAN_ROLES <= ASSIGNABLE_ROLES
    assert ADMIN_TIER_ROLES <= ASSIGNABLE_ROLES


def test_plan_roles_are_not_admin_tier():
    assert PLAN_ROLES.isdisjoint(ADMIN_TIER_ROLES)


# ------------------- unit: validate_role_assignment -----------------------

@pytest.mark.parametrize("role", sorted(PLAN_ROLES | {"user"}))
def test_admin_may_assign_any_non_admin_role(role):
    # A plain admin performs ordinary account administration.
    assert validate_role_assignment(role, "admin") == role


@pytest.mark.parametrize("role", sorted(ADMIN_TIER_ROLES))
def test_super_admin_may_assign_admin_tier_roles(role):
    assert validate_role_assignment(role, "super_admin") == role


@pytest.mark.parametrize("role", sorted(ADMIN_TIER_ROLES))
def test_admin_cannot_grant_admin_tier_roles(role):
    with pytest.raises(HTTPException) as exc:
        validate_role_assignment(role, "admin")
    assert exc.value.status_code == 403
    assert "super_admin" in exc.value.detail


@pytest.mark.parametrize("bad", ["", "root", "owner", "administrator", "PRO", "super-admin", "user; drop"])
def test_unknown_role_is_rejected_400(bad):
    with pytest.raises(HTTPException) as exc:
        validate_role_assignment(bad, "super_admin")
    assert exc.value.status_code == 400
    assert "Invalid role" in exc.value.detail


def test_non_admin_actor_still_blocked_from_admin_tier():
    # Defense in depth: even if the endpoint guard were bypassed, an empty/plain
    # actor role can never mint an admin-tier role.
    with pytest.raises(HTTPException) as exc:
        validate_role_assignment("super_admin", "")
    assert exc.value.status_code == 403


# --------------------- integration: admin user editor ---------------------

@pytest.fixture
def _admin(fake_db):
    doc = {"_id": ObjectId(), "name": "Adminy", "email": "admin@example.com", "role": "admin"}
    fake_db.users.docs.append(doc)
    return doc


@pytest.fixture
def _super_admin(fake_db):
    doc = {"_id": ObjectId(), "name": "Root", "email": "root@example.com", "role": "super_admin"}
    fake_db.users.docs.append(doc)
    return doc


@pytest.fixture
def _victim(fake_db):
    doc = {"_id": ObjectId(), "name": "Target", "email": "target@example.com", "role": "user"}
    fake_db.users.docs.append(doc)
    return doc


def _headers(user):
    from server import create_access_token
    token = create_access_token(str(user["_id"]), user["email"])
    return {"Authorization": f"Bearer {token}"}


def test_admin_cannot_escalate_a_user_to_admin(client, _admin, _victim, fake_db):
    resp = client.put(
        f"/api/admin/users/{_victim['_id']}",
        json={"role": "admin"},
        headers=_headers(_admin),
    )
    assert resp.status_code == 403
    # The victim's stored role is unchanged.
    stored = next(u for u in fake_db.users.docs if u["_id"] == _victim["_id"])
    assert stored["role"] == "user"


def test_admin_cannot_self_promote_to_super_admin(client, _admin, fake_db):
    resp = client.put(
        f"/api/admin/users/{_admin['_id']}",
        json={"role": "super_admin"},
        headers=_headers(_admin),
    )
    assert resp.status_code == 403
    stored = next(u for u in fake_db.users.docs if u["_id"] == _admin["_id"])
    assert stored["role"] == "admin"


def test_super_admin_can_grant_admin_role(client, _super_admin, _victim, fake_db):
    resp = client.put(
        f"/api/admin/users/{_victim['_id']}",
        json={"role": "admin"},
        headers=_headers(_super_admin),
    )
    assert resp.status_code == 200
    stored = next(u for u in fake_db.users.docs if u["_id"] == _victim["_id"])
    assert stored["role"] == "admin"


def test_admin_can_still_grant_plan_roles(client, _admin, _victim, fake_db):
    resp = client.put(
        f"/api/admin/users/{_victim['_id']}",
        json={"role": "pro"},
        headers=_headers(_admin),
    )
    assert resp.status_code == 200
    stored = next(u for u in fake_db.users.docs if u["_id"] == _victim["_id"])
    assert stored["role"] == "pro"


def test_unknown_role_rejected_by_endpoint(client, _super_admin, _victim):
    resp = client.put(
        f"/api/admin/users/{_victim['_id']}",
        json={"role": "root"},
        headers=_headers(_super_admin),
    )
    assert resp.status_code == 400


def test_malformed_user_id_returns_400_not_500(client, _admin):
    # F-2 regression: a malformed ObjectId in the path is a clean 400.
    resp = client.put(
        "/api/admin/users/not-a-valid-id",
        json={"name": "x"},
        headers=_headers(_admin),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid user id"

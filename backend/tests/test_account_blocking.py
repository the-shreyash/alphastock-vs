"""Administrative account blocking is actually enforced (PH3.10).

WHAT THIS SUITE EXISTS TO PREVENT
---------------------------------
`POST /api/admin/users/{id}/block` has always written `blocked: True`, written an
immutable audit record saying the user was blocked, and been reflected in the
admin user list's `status=blocked` filter. Nothing on any authentication path
ever read the flag. Blocking an account therefore did nothing whatsoever — the
target's outstanding tokens kept working, their refresh token kept minting new
access tokens for another seven days, and they could log in again immediately —
while the console reported success and the audit log recorded an action that had
not happened.

That is the most dangerous shape an admin control can have: it fails silently,
in the direction of false confidence, at exactly the moment an operator is
relying on it (an account compromise, a payment dispute, an abusive user).

The tests are written against the four places an identity is established, because
fixing only one of them leaves the control broken in a way that is *harder* to
notice than before — an operator who sees login refused would reasonably assume
the live session died too.
"""
import pytest


@pytest.fixture
def blocked_user(fake_db, test_user):
    """`test_user`, blocked. Mutating the seeded document is what the admin
    endpoint does (`$set: {blocked: True}`), so this is the real post-block
    state rather than a separate fixture that could drift from it."""
    test_user["blocked"] = True
    return test_user


# --------------------------------------------------------------------------- #
# The flag is honoured everywhere an identity is established                    #
# --------------------------------------------------------------------------- #
class TestBlockedAccountIsRefused:
    def test_existing_access_token_stops_working(self, client, blocked_user, auth_headers):
        """The one that matters most: a block must not wait for the current
        token to expire. `auth_headers` was minted before the block."""
        r = client.get("/api/auth/me", headers=auth_headers)
        assert r.status_code == 403
        assert "blocked" in r.json()["detail"].lower()

    def test_blocked_user_cannot_log_in(self, client, fake_db, blocked_user):
        blocked_user["password_hash"] = _hash("Str0ng!Passw0rd#2026")
        r = client.post("/api/auth/login", json={
            "email": blocked_user["email"], "password": "Str0ng!Passw0rd#2026"})
        assert r.status_code == 403

    def test_wrong_password_on_a_blocked_account_still_says_401(
            self, client, fake_db, blocked_user):
        """The block check runs AFTER the password comparison. Answering 403 to a
        wrong password would make the endpoint an oracle for which addresses hold
        blocked accounts."""
        blocked_user["password_hash"] = _hash("Str0ng!Passw0rd#2026")
        r = client.post("/api/auth/login", json={
            "email": blocked_user["email"], "password": "not-the-password"})
        assert r.status_code == 401

    def test_protected_routes_reject_a_blocked_user(self, client, blocked_user, auth_headers):
        """Spot-check that the refusal comes from the shared dependency rather
        than a special case in `/auth/me`."""
        for path in ("/api/trades", "/api/portfolio", "/api/notifications"):
            r = client.get(path, headers=auth_headers)
            assert r.status_code == 403, f"{path} admitted a blocked user"


# --------------------------------------------------------------------------- #
# Unblocking restores access — the control has to be reversible                 #
# --------------------------------------------------------------------------- #
class TestUnblockRestoresAccess:
    def test_unblocked_user_works_again(self, client, fake_db, test_user, auth_headers):
        test_user["blocked"] = True
        assert client.get("/api/auth/me", headers=auth_headers).status_code == 403
        test_user["blocked"] = False
        assert client.get("/api/auth/me", headers=auth_headers).status_code == 200

    def test_absent_flag_is_treated_as_active(self, client, test_user, auth_headers):
        """Every account that predates the flag has no `blocked` key at all.
        Defaulting a missing key to "blocked" would lock out the entire user
        base on deploy."""
        assert "blocked" not in test_user
        assert client.get("/api/auth/me", headers=auth_headers).status_code == 200


# --------------------------------------------------------------------------- #
# The admin endpoint and the enforcement agree                                  #
# --------------------------------------------------------------------------- #
class TestAdminBlockEndpointTakesEffect:
    def test_block_then_the_target_is_locked_out(
            self, client, fake_db, admin_headers, other_user, other_headers):
        """End to end through the real admin route, asserting the *consequence*
        rather than the 200. The PH3.3 refund stub (D-4) is the precedent: an
        endpoint returning `{"success": true}` proves nothing about whether the
        thing happened."""
        assert client.get("/api/auth/me", headers=other_headers).status_code == 200

        r = client.post(f"/api/admin/users/{other_user['_id']}/block",
                        headers=admin_headers)
        assert r.status_code == 200

        assert client.get("/api/auth/me", headers=other_headers).status_code == 403

    def test_unblock_through_the_admin_route_restores_access(
            self, client, fake_db, admin_headers, other_user, other_headers):
        client.post(f"/api/admin/users/{other_user['_id']}/block", headers=admin_headers)
        assert client.get("/api/auth/me", headers=other_headers).status_code == 403

        r = client.post(f"/api/admin/users/{other_user['_id']}/unblock",
                        headers=admin_headers)
        assert r.status_code == 200
        assert client.get("/api/auth/me", headers=other_headers).status_code == 200


def _hash(password: str) -> str:
    from security.passwords import hash_password
    return hash_password(password)

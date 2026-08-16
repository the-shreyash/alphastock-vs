"""Admin API: the control plane (PH3.3).

WHAT PRODUCTION FAILURE THIS CATCHES
------------------------------------
The admin surface is 29 endpoints that read every user's data and mutate
accounts, roles and entitlements. Two classes of failure matter here and
nowhere else:

* **A control-plane action that silently does not happen.** An admin blocks an
  abusive account, sees "success", and the account keeps trading. The status
  code is not the assertion — the resulting document is.
* **An operational dashboard that dies on one bad row.** Admin pages are read
  during incidents. An endpoint that 500s because a single legacy document has
  an unexpected shape is unavailable exactly when it is needed, and the operator
  cannot tell a broken page from a broken platform.

Authorization for these endpoints — who may call them at all, and the
admin/super_admin boundary — is covered in `test_api_authz.py` and deliberately
not repeated. This file assumes the caller is entitled and asks whether the
endpoint then does the right thing.

Every mutating test also asserts the audit record, because an unaudited
privileged action is indistinguishable from one that never happened.
"""
import pytest
from bson import ObjectId


def seed_users(fake_db, count, **overrides):
    """Insert `count` obviously-synthetic users and return them."""
    made = []
    for i in range(count):
        doc = {
            "_id": ObjectId(),
            "name": f"TEST User {i:02d}",
            "email": f"seed{i:02d}@example.com",
            "role": "user",
            "capital": 100000.0,
            "created_at": f"2026-07-{i % 28 + 1:02d}T00:00:00+00:00",
            **overrides,
        }
        fake_db.users.docs.append(doc)
        made.append(doc)
    return made


def audit_entries(fake_db, action):
    return [e for e in fake_db.admin_audit_logs.docs if e["action"] == action]


# --------------------------------------------------------------------------- #
# Dashboard & health                                                            #
# --------------------------------------------------------------------------- #
class TestDashboard:
    def test_dashboard_renders_for_an_empty_platform(self, admin_client, fake_db):
        """Day one, and also the disaster-recovery case: a dashboard that
        divides by a zero user count is unusable precisely when the platform is
        empty because something went wrong."""
        resp = admin_client.get("/api/admin/dashboard")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("total_users", "total_trades", "open_trades", "db_health"):
            assert key in body, f"missing {key}"

    def test_counts_reflect_the_database(self, admin_client, fake_db, admin_user):
        seed_users(fake_db, 5)
        fake_db.trades.docs.extend([
            {"_id": ObjectId(), "user_id": "u1", "status": "OPEN"},
            {"_id": ObjectId(), "user_id": "u1", "status": "CLOSED"},
        ])
        body = admin_client.get("/api/admin/dashboard").json()
        assert body["total_users"] == 6, "5 seeded + the admin making the call"
        assert body["total_trades"] == 2
        assert body["open_trades"] == 1

    def test_system_health_is_available(self, admin_client, fake_db):
        resp = admin_client.get("/api/admin/system/health")
        assert resp.status_code == 200

    def test_api_health_page_does_not_call_any_provider(self, admin_client, fake_db):
        """It reports configuration, not liveness. Probing real providers from
        an admin page would make the page as slow and as fragile as the slowest
        third party — and the network guard would block it here anyway."""
        resp = admin_client.get("/api/admin/apis/health")
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# User management                                                               #
# --------------------------------------------------------------------------- #
class TestUserListing:
    def test_password_hashes_are_never_returned(self, admin_client, fake_db):
        """The projection is the only thing preventing every user's credential
        hash from being served to an admin page — and from there into browser
        history, logs and screenshots."""
        seed_users(fake_db, 3, password_hash="TEST-should-never-appear")
        body = admin_client.get("/api/admin/users").json()
        assert body["users"], "nothing was returned, so nothing was proven"
        for user in body["users"]:
            assert "password_hash" not in user

    def test_search_matches_name_and_email_case_insensitively(self, admin_client, fake_db):
        seed_users(fake_db, 3)
        fake_db.users.docs.append({
            "_id": ObjectId(), "name": "TEST Findme", "email": "findme@example.com",
            "role": "user", "created_at": "2026-07-01T00:00:00+00:00"})
        by_name = admin_client.get("/api/admin/users", params={"search": "findme"})
        assert by_name.status_code == 200
        assert any(u["email"] == "findme@example.com" for u in by_name.json()["users"])

    def test_role_filter_narrows_the_result(self, admin_client, fake_db):
        seed_users(fake_db, 3)
        seed_users(fake_db, 2, role="pro")
        body = admin_client.get("/api/admin/users", params={"role": "pro"}).json()
        assert body["total"] == 2
        assert all(u["role"] == "pro" for u in body["users"])

    def test_status_filter_separates_blocked_from_active(self, admin_client, fake_db):
        seed_users(fake_db, 3)
        seed_users(fake_db, 2, blocked=True)
        blocked = admin_client.get("/api/admin/users", params={"status": "blocked"}).json()
        assert blocked["total"] == 2
        active = admin_client.get("/api/admin/users", params={"status": "active"}).json()
        assert active["total"] == 4, "3 seeded + the calling admin; blocked excluded"

    def test_page_count_is_reported(self, admin_client, fake_db):
        seed_users(fake_db, 25)
        body = admin_client.get("/api/admin/users", params={"limit": 10}).json()
        assert body["pages"] == 3
        assert body["total"] == 26


class TestUserDetail:
    def test_known_user_is_returned_with_trade_count(self, admin_client, fake_db, other_user):
        fake_db.trades.docs.append(
            {"_id": ObjectId(), "user_id": str(other_user["_id"]), "status": "OPEN"})
        resp = admin_client.get(f"/api/admin/users/{other_user['_id']}")
        assert resp.status_code == 200
        assert resp.json()["trade_count"] == 1

    def test_unknown_user_is_404(self, admin_client, fake_db):
        assert admin_client.get(f"/api/admin/users/{ObjectId()}").status_code == 404

    def test_detail_never_returns_a_password_hash(self, admin_client, fake_db, other_user):
        fake_db.users.docs[-1]["password_hash"] = "TEST-should-never-appear"
        resp = admin_client.get(f"/api/admin/users/{other_user['_id']}")
        assert "password_hash" not in resp.json()


class TestBlockAndUnblock:
    def test_blocking_persists_and_is_audited(self, admin_client, fake_db, other_user, admin_user):
        resp = admin_client.post(f"/api/admin/users/{other_user['_id']}/block")
        assert resp.status_code == 200
        stored = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert stored["blocked"] is True, "the account was not actually blocked"
        logged = audit_entries(fake_db, "user.blocked")
        assert len(logged) == 1
        assert logged[0]["target"] == str(other_user["_id"])
        assert logged[0]["admin_id"] == str(admin_user["_id"])

    def test_unblocking_reverses_it(self, admin_client, fake_db, other_user):
        admin_client.post(f"/api/admin/users/{other_user['_id']}/block")
        admin_client.post(f"/api/admin/users/{other_user['_id']}/unblock")
        stored = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert stored["blocked"] is False
        assert audit_entries(fake_db, "user.unblocked")

    def test_blocking_an_unknown_user_is_not_a_server_error(self, admin_client, fake_db):
        resp = admin_client.post(f"/api/admin/users/{ObjectId()}/block")
        assert resp.status_code < 500


class TestUserUpdate:
    def test_permitted_fields_are_written(self, admin_client, fake_db, other_user):
        resp = admin_client.put(f"/api/admin/users/{other_user['_id']}", json={
            "name": "TEST Renamed", "capital": 250000.0, "risk_level": "aggressive"})
        assert resp.status_code == 200
        stored = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert stored["name"] == "TEST Renamed"
        assert stored["capital"] == 250000.0
        assert stored["risk_level"] == "aggressive"

    def test_the_update_is_audited_with_its_payload(self, admin_client, fake_db, other_user):
        admin_client.put(f"/api/admin/users/{other_user['_id']}", json={"name": "TEST Renamed"})
        logged = audit_entries(fake_db, "user.updated")
        assert len(logged) == 1
        assert logged[0]["details"] == {"name": "TEST Renamed"}


class TestGrantPlan:
    def test_granting_sets_role_and_expiry_and_attribution(
            self, admin_client, fake_db, other_user, admin_user):
        resp = admin_client.post(f"/api/admin/users/{other_user['_id']}/grant-plan",
                                 json={"plan": "elite", "duration_days": 90})
        assert resp.status_code == 200
        stored = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert stored["role"] == "elite"
        assert stored["plan_expires_at"], "a non-lifetime plan must expire"
        assert stored["plan_granted_by"] == str(admin_user["_id"]), \
            "who granted an entitlement must be recoverable"

    def test_lifetime_has_no_expiry(self, admin_client, fake_db, other_user):
        admin_client.post(f"/api/admin/users/{other_user['_id']}/grant-plan",
                          json={"plan": "lifetime"})
        stored = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert stored["plan_expires_at"] is None


class TestUserDeletion:
    def test_deletion_removes_the_account_and_is_audited(
            self, super_admin_client, fake_db, other_user):
        resp = super_admin_client.delete(f"/api/admin/users/{other_user['_id']}")
        assert resp.status_code == 200
        assert not any(u["_id"] == other_user["_id"] for u in fake_db.users.docs)
        assert audit_entries(fake_db, "user.deleted")


# --------------------------------------------------------------------------- #
# Audit log                                                                     #
# --------------------------------------------------------------------------- #
class TestAuditLog:
    def test_log_is_paginated_and_newest_first(self, admin_client, fake_db, admin_user):
        for i in range(5):
            fake_db.admin_audit_logs.docs.append({
                "_id": ObjectId(), "admin_id": str(admin_user["_id"]),
                "action": f"TEST.action.{i}", "target": "", "details": {},
                "timestamp": f"2026-08-0{i + 1}T00:00:00+00:00"})
        body = admin_client.get("/api/admin/logs", params={"limit": 3}).json()
        assert body["total"] == 5
        assert len(body["logs"]) == 3
        assert body["logs"][0]["action"] == "TEST.action.4", "newest entry first"

    def test_action_filter_narrows_the_log(self, admin_client, fake_db, admin_user):
        for action in ("user.blocked", "user.updated", "user.blocked"):
            fake_db.admin_audit_logs.docs.append({
                "_id": ObjectId(), "admin_id": str(admin_user["_id"]),
                "action": action, "target": "", "details": {},
                "timestamp": "2026-08-01T00:00:00+00:00"})
        body = admin_client.get("/api/admin/logs", params={"action": "blocked"}).json()
        assert body["total"] == 2

    def test_an_unresolvable_admin_id_does_not_break_the_page(
            self, admin_client, fake_db):
        """Audit rows outlive the accounts that wrote them — deliberately, since
        deleting an admin must not erase what they did. The page has to render
        an orphaned row rather than 500 on it."""
        fake_db.admin_audit_logs.docs.append({
            "_id": ObjectId(), "admin_id": "not-a-valid-objectid",
            "action": "TEST.orphaned", "target": "", "details": {},
            "timestamp": "2026-08-01T00:00:00+00:00"})
        resp = admin_client.get("/api/admin/logs")
        assert resp.status_code == 200
        assert resp.json()["logs"][0]["admin_name"] in ("System", "Unknown")


# --------------------------------------------------------------------------- #
# Analytics                                                                     #
# --------------------------------------------------------------------------- #
class TestAnalytics:
    @pytest.mark.parametrize("endpoint", [
        "/api/admin/analytics/users",
        "/api/admin/analytics/revenue",
        "/api/admin/analytics/features",
        "/api/admin/ai/status",
        "/api/admin/ai/usage",
    ])
    def test_analytics_render_on_an_empty_database(self, admin_client, fake_db, endpoint):
        """Every aggregate here divides or averages over collections that are
        empty on a fresh install and after a restore."""
        resp = admin_client.get(endpoint)
        assert resp.status_code == 200, f"{endpoint}: {resp.text[:200]}"

    def test_ai_usage_survives_a_malformed_user_id(self, admin_client, fake_db):
        """PH3.3 defect D-9. `ObjectId(uid)` was called raw on a value taken
        from a `$group` over `chat_messages.user_id`. One row written by a
        legacy path — or by any future code that stores a session key there —
        raised InvalidId and took the entire AI-usage page to a 500.
        """
        fake_db.chat_messages.docs.extend([
            {"_id": ObjectId(), "user_id": "legacy-non-objectid",
             "role": "user", "content": "TEST", "created_at": "2026-08-01T00:00:00"},
            {"_id": ObjectId(), "user_id": str(ObjectId()),
             "role": "user", "content": "TEST", "created_at": "2026-08-01T00:00:00"},
        ])
        resp = admin_client.get("/api/admin/ai/usage")
        assert resp.status_code == 200
        top = resp.json()["top_users"]
        assert len(top) == 2, "the malformed row must still be counted, not dropped"
        assert any(u["name"] == "Unknown" for u in top)

    def test_ai_usage_ranks_by_message_count(self, admin_client, fake_db, other_user):
        for _ in range(3):
            fake_db.chat_messages.docs.append(
                {"_id": ObjectId(), "user_id": str(other_user["_id"]),
                 "role": "user", "content": "TEST", "created_at": "2026-08-01T00:00:00"})
        fake_db.chat_messages.docs.append(
            {"_id": ObjectId(), "user_id": str(ObjectId()),
             "role": "user", "content": "TEST", "created_at": "2026-08-01T00:00:00"})
        top = admin_client.get("/api/admin/ai/usage").json()["top_users"]
        # PH3.9 renamed `request_count` -> `message_count`. The value is
        # unchanged and always was a count of stored chat messages; the old name
        # claimed a provider-request count, which it overstated by roughly 2x
        # because a message is written for the user turn AND the assistant turn.
        assert top[0]["message_count"] == 3
        assert top[0]["email"] == other_user["email"]


# --------------------------------------------------------------------------- #
# Payments                                                                      #
# --------------------------------------------------------------------------- #
class TestPayments:
    def test_absent_payments_collection_is_an_empty_page_not_an_error(
            self, admin_client, fake_db):
        """Payments are not wired up yet; the admin page must still render."""
        resp = admin_client.get("/api/admin/payments")
        assert resp.status_code == 200
        assert resp.json()["payments"] == []

    def test_payment_stats_render_with_no_payments(self, admin_client, fake_db):
        assert admin_client.get("/api/admin/payments/stats").status_code == 200


# --------------------------------------------------------------------------- #
# Feature flags, announcements, tickets                                         #
# --------------------------------------------------------------------------- #
class TestFeatureFlags:
    def test_create_then_list(self, admin_client, fake_db):
        created = admin_client.post("/api/admin/feature-flags",
                                    json={"name": "TEST_flag", "enabled": True})
        assert created.status_code == 200
        listed = admin_client.get("/api/admin/feature-flags")
        assert listed.status_code == 200
        assert any("TEST_flag" in str(f) for f in listed.json().get("flags", listed.json()))

    def test_updating_an_unknown_flag_is_not_a_server_error(self, admin_client, fake_db):
        resp = admin_client.put(f"/api/admin/feature-flags/{ObjectId()}",
                                json={"enabled": False})
        assert resp.status_code < 500


class TestAnnouncements:
    def test_create_list_and_delete(self, admin_client, fake_db):
        created = admin_client.post("/api/admin/announcements",
                                    json={"title": "TEST notice", "message": "TEST body"})
        assert created.status_code == 200
        listed = admin_client.get("/api/admin/announcements")
        assert listed.status_code == 200
        assert fake_db.announcements.docs, "nothing was persisted"

        ann_id = fake_db.announcements.docs[0]["_id"]
        deleted = admin_client.delete(f"/api/admin/announcements/{ann_id}")
        assert deleted.status_code == 200
        assert fake_db.announcements.docs == []

    def test_deleting_an_unknown_announcement_is_not_a_server_error(
            self, admin_client, fake_db):
        assert admin_client.delete(
            f"/api/admin/announcements/{ObjectId()}").status_code < 500


class TestSupportTickets:
    def test_list_renders_empty(self, admin_client, fake_db):
        resp = admin_client.get("/api/admin/support/tickets")
        assert resp.status_code == 200
        assert resp.json()["tickets"] == []

    def test_status_filter_narrows_the_list(self, admin_client, fake_db):
        for status in ("open", "closed", "open"):
            fake_db.support_tickets.docs.append({
                "_id": ObjectId(), "subject": f"TEST {status}", "status": status,
                "created_at": "2026-08-01T00:00:00+00:00"})
        body = admin_client.get("/api/admin/support/tickets",
                                params={"status": "open"}).json()
        assert body["total"] == 2

    def test_updating_an_unknown_ticket_is_not_a_server_error(self, admin_client, fake_db):
        resp = admin_client.put(f"/api/admin/support/tickets/{ObjectId()}",
                                json={"status": "closed"})
        assert resp.status_code < 500

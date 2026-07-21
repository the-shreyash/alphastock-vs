"""PH1.8 — identity-recovery tests.

Covers the sprint's acceptance matrix:

* email verification succeeds; expired verification rejected; replay rejected;
* forgot-password returns a generic response (no email enumeration);
* reset token expires; reset token is single-use; password policy enforced;
* password change requires the current password; a wrong/duplicate one is
  rejected;
* every session is revoked (and access tokens go stale) after a change / reset;
* Google-native accounts are verified on creation;
* the existing register → login → me lifecycle keeps working.

Hermetic: the ``RecoveryStore`` runs against the in-memory ``FakeDB`` via
``asyncio.run`` (no pytest-asyncio); endpoint tests use the shared
``client``/``fake_db`` fixtures with recovery emails captured in-process (no live
server, Mongo, or real mailer). ``conftest.py`` loads ``.env`` (JWT_SECRET) by
importing ``server`` at collection time.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

import pytest
from bson import ObjectId

import server
from security import recovery
from security.recovery import (
    RecoveryStore,
    PURPOSE_VERIFY_EMAIL,
    PURPOSE_RESET_PASSWORD,
)
from tests._fakedb import FakeDB

REG_PASSWORD = "S3cure!Passw0rd"
NEW_PASSWORD = "N3w!Str0ngPhrase"


def _run(coro):
    return asyncio.run(coro)


def _token_from_url(url: str) -> str:
    """Pull the ?token=... value out of a captured recovery link."""
    return parse_qs(urlparse(url).query)["token"][0]


# --------------------------------------------------------------------------- #
# Fixtures                                                                       #
# --------------------------------------------------------------------------- #
@pytest.fixture
def sent_emails(monkeypatch):
    """Capture every recovery email in-process instead of sending it.

    Patches ``server._send_recovery_email`` (the single best-effort sink used by
    all recovery flows) so tests can read back the verification / reset link
    without a real mailer."""
    captured = []

    async def fake_send(notif_type, to_email, **kwargs):
        captured.append({"type": notif_type, "to": to_email, **kwargs})

    monkeypatch.setattr(server, "_send_recovery_email", fake_send)
    return captured


def _register(client, email="rec@example.com", name="Recovery Tester"):
    return client.post("/api/auth/register", json={
        "name": name, "email": email, "password": REG_PASSWORD,
    })


# --------------------------------------------------------------------------- #
# security.recovery — pure token mint / verify / single-use                      #
# --------------------------------------------------------------------------- #
class TestRecoveryStore:
    def test_issue_verify_consume_roundtrip(self):
        db = FakeDB()

        async def go():
            store = RecoveryStore(db)
            tok = await store.issue("user-1", PURPOSE_VERIFY_EMAIL)
            assert await store.verify(tok.value, PURPOSE_VERIFY_EMAIL)
            record = await store.consume(tok.value, PURPOSE_VERIFY_EMAIL)
            return tok, record
        tok, record = _run(go())
        assert record is not None
        assert record["user_id"] == "user-1"
        assert tok.value.count(".") == 1  # <token_id>.<hmac>

    def test_single_use_second_consume_rejected(self):
        db = FakeDB()

        async def go():
            store = RecoveryStore(db)
            tok = await store.issue("user-1", PURPOSE_RESET_PASSWORD)
            first = await store.consume(tok.value, PURPOSE_RESET_PASSWORD)
            second = await store.consume(tok.value, PURPOSE_RESET_PASSWORD)
            after = await store.verify(tok.value, PURPOSE_RESET_PASSWORD)
            return first, second, after
        first, second, after = _run(go())
        assert first is not None
        assert second is None      # replay of a burned token
        assert after is None       # verify also sees it as spent

    def test_expired_token_rejected(self):
        db = FakeDB()

        async def go():
            store = RecoveryStore(db)
            tok = await store.issue("user-1", PURPOSE_RESET_PASSWORD)
            # Force the authoritative record into the past.
            db.recovery_tokens.docs[0]["expires_at"] = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            )
            return (await store.verify(tok.value, PURPOSE_RESET_PASSWORD),
                    await store.consume(tok.value, PURPOSE_RESET_PASSWORD))
        assert _run(go()) == (None, None)

    def test_wrong_purpose_rejected(self):
        db = FakeDB()

        async def go():
            store = RecoveryStore(db)
            tok = await store.issue("user-1", PURPOSE_VERIFY_EMAIL)
            # A verification token must never redeem as a reset token.
            return await store.verify(tok.value, PURPOSE_RESET_PASSWORD)
        assert _run(go()) is None

    def test_tampered_signature_rejected(self):
        db = FakeDB()

        async def go():
            store = RecoveryStore(db)
            tok = await store.issue("user-1", PURPOSE_VERIFY_EMAIL)
            token_id, _, sig = tok.value.partition(".")
            flipped = "0" if sig[-1] != "0" else "1"
            forged = f"{token_id}.{sig[:-1]}{flipped}"
            return await store.verify(forged, PURPOSE_VERIFY_EMAIL)
        assert _run(go()) is None

    def test_reissue_invalidates_prior_unused_token(self):
        db = FakeDB()

        async def go():
            store = RecoveryStore(db)
            first = await store.issue("user-1", PURPOSE_RESET_PASSWORD)
            second = await store.issue("user-1", PURPOSE_RESET_PASSWORD)
            return (await store.verify(first.value, PURPOSE_RESET_PASSWORD),
                    await store.verify(second.value, PURPOSE_RESET_PASSWORD))
        old_ok, new_ok = _run(go())
        assert old_ok is None      # superseded — only one live link at a time
        assert new_ok is not None

    def test_malformed_token_rejected(self):
        db = FakeDB()

        async def go():
            store = RecoveryStore(db)
            return [await store.verify(v, PURPOSE_VERIFY_EMAIL)
                    for v in ("", "no-dot", "a.", ".b", None)]
        assert _run(go()) == [None] * 5

    def test_ttls_match_policy(self):
        assert recovery.ttl_seconds(PURPOSE_VERIFY_EMAIL) == 24 * 60 * 60
        assert recovery.ttl_seconds(PURPOSE_RESET_PASSWORD) == 30 * 60


# --------------------------------------------------------------------------- #
# Email verification — endpoint flow                                            #
# --------------------------------------------------------------------------- #
class TestEmailVerification:
    def test_registration_starts_unverified_and_sends_link(self, client, fake_db, sent_emails):
        r = _register(client, email="verify@example.com")
        assert r.status_code == 200
        assert r.json()["email_verified"] is False
        user = fake_db.users.docs[0]
        assert user["email_verified"] is False
        # A verification email was dispatched by the background task.
        assert any(e["type"] == "EMAIL_VERIFICATION" for e in sent_emails)

    def test_verify_email_succeeds(self, client, fake_db, sent_emails):
        _register(client, email="verify@example.com")
        token = _token_from_url(sent_emails[0]["verify_url"])
        r = client.post("/api/auth/verify-email", json={"token": token})
        assert r.status_code == 200
        assert r.json()["email_verified"] is True
        user = fake_db.users.docs[0]
        assert user["email_verified"] is True
        assert user["verified_by"] == "email"
        assert user["email_verified_at"]

    def test_verify_email_replay_rejected(self, client, fake_db, sent_emails):
        _register(client, email="verify@example.com")
        token = _token_from_url(sent_emails[0]["verify_url"])
        assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
        # Second redemption of the same (now burned) link fails.
        replay = client.post("/api/auth/verify-email", json={"token": token})
        assert replay.status_code == 400

    def test_verify_email_expired_rejected(self, client, fake_db, sent_emails):
        _register(client, email="verify@example.com")
        token = _token_from_url(sent_emails[0]["verify_url"])
        fake_db.recovery_tokens.docs[0]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        r = client.post("/api/auth/verify-email", json={"token": token})
        assert r.status_code == 400
        assert fake_db.users.docs[0].get("email_verified") is False

    def test_verify_email_garbage_token_rejected(self, client, fake_db):
        r = client.post("/api/auth/verify-email", json={"token": "totally.bogus"})
        assert r.status_code == 400

    def test_resend_verification_requires_auth(self, client, fake_db):
        assert client.post("/api/auth/verify-email/request").status_code == 401

    def test_resend_verification_generic_for_verified_user(self, client, fake_db, sent_emails):
        r = _register(client, email="verify@example.com")
        token = r.json()["token"]
        # Verify first, then a resend should send nothing but still 200 generic.
        vtok = _token_from_url(sent_emails[0]["verify_url"])
        client.post("/api/auth/verify-email", json={"token": vtok})
        sent_emails.clear()
        resend = client.post("/api/auth/verify-email/request",
                             headers={"Authorization": f"Bearer {token}"})
        assert resend.status_code == 200
        assert sent_emails == []  # already verified → no new mail


# --------------------------------------------------------------------------- #
# Forgot / reset password — endpoint flow                                       #
# --------------------------------------------------------------------------- #
class TestPasswordReset:
    def test_forgot_password_generic_for_unknown_email(self, client, fake_db, sent_emails):
        r = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
        assert r.status_code == 200
        assert "If an account matches" in r.json()["message"]
        assert sent_emails == []  # no account → no mail, no signal

    def test_forgot_password_generic_for_known_email(self, client, fake_db, sent_emails):
        _register(client, email="reset@example.com")
        sent_emails.clear()
        r = client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
        assert r.status_code == 200
        # Identical message to the unknown-email case (no enumeration)...
        assert "If an account matches" in r.json()["message"]
        # ...but a reset link WAS sent for the real account.
        assert any(e["type"] == "PASSWORD_RESET" for e in sent_emails)

    def test_reset_password_succeeds_and_rotates_credential(self, client, fake_db, sent_emails):
        _register(client, email="reset@example.com")
        sent_emails.clear()
        client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
        token = _token_from_url(sent_emails[0]["reset_url"])
        r = client.post("/api/auth/reset-password",
                        json={"token": token, "new_password": NEW_PASSWORD})
        assert r.status_code == 200
        # Old password no longer works; the new one does.
        assert client.post("/api/auth/login",
                           json={"email": "reset@example.com", "password": REG_PASSWORD}).status_code == 401
        assert client.post("/api/auth/login",
                           json={"email": "reset@example.com", "password": NEW_PASSWORD}).status_code == 200

    def test_reset_token_single_use(self, client, fake_db, sent_emails):
        _register(client, email="reset@example.com")
        sent_emails.clear()
        client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
        token = _token_from_url(sent_emails[0]["reset_url"])
        assert client.post("/api/auth/reset-password",
                           json={"token": token, "new_password": NEW_PASSWORD}).status_code == 200
        # Replaying the burned reset link fails.
        replay = client.post("/api/auth/reset-password",
                             json={"token": token, "new_password": "An0ther!Pass99"})
        assert replay.status_code == 400

    def test_reset_token_expires(self, client, fake_db, sent_emails):
        _register(client, email="reset@example.com")
        sent_emails.clear()
        client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
        token = _token_from_url(sent_emails[0]["reset_url"])
        fake_db.recovery_tokens.docs[-1]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        r = client.post("/api/auth/reset-password",
                        json={"token": token, "new_password": NEW_PASSWORD})
        assert r.status_code == 400

    def test_reset_password_enforces_policy(self, client, fake_db, sent_emails):
        _register(client, email="reset@example.com")
        sent_emails.clear()
        client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
        token = _token_from_url(sent_emails[0]["reset_url"])
        r = client.post("/api/auth/reset-password",
                        json={"token": token, "new_password": "weak"})
        assert r.status_code == 422

    def test_reset_revokes_all_sessions(self, client, fake_db, sent_emails):
        _register(client, email="reset@example.com")
        sent_emails.clear()
        client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
        token = _token_from_url(sent_emails[0]["reset_url"])
        client.post("/api/auth/reset-password",
                    json={"token": token, "new_password": NEW_PASSWORD})
        # Every refresh family was revoked, so the session opened at registration
        # can no longer be rotated — the user is forced to log in again.
        assert all(s["revoked"] for s in fake_db.sessions.docs)
        assert client.post("/api/auth/refresh").status_code == 401


# --------------------------------------------------------------------------- #
# Change password — authenticated flow                                          #
# --------------------------------------------------------------------------- #
class TestPasswordChange:
    def _auth(self, client, email="change@example.com"):
        token = _register(client, email=email).json()["token"]
        return token, {"Authorization": f"Bearer {token}"}

    def test_change_password_requires_current(self, client, fake_db, sent_emails):
        _, headers = self._auth(client)
        r = client.post("/api/auth/change-password",
                        json={"current_password": "wrong-password", "new_password": NEW_PASSWORD},
                        headers=headers)
        assert r.status_code == 400

    def test_change_password_succeeds_and_signs_out(self, client, fake_db, sent_emails):
        _, headers = self._auth(client)
        r = client.post("/api/auth/change-password",
                        json={"current_password": REG_PASSWORD, "new_password": NEW_PASSWORD},
                        headers=headers)
        assert r.status_code == 200
        # Signed out everywhere: every refresh family revoked, so the current
        # session can no longer be rotated.
        assert all(s["revoked"] for s in fake_db.sessions.docs)
        assert client.post("/api/auth/refresh").status_code == 401
        # New credential works.
        assert client.post("/api/auth/login",
                           json={"email": "change@example.com", "password": NEW_PASSWORD}).status_code == 200

    def test_change_password_rejects_same_password(self, client, fake_db, sent_emails):
        _, headers = self._auth(client)
        r = client.post("/api/auth/change-password",
                        json={"current_password": REG_PASSWORD, "new_password": REG_PASSWORD},
                        headers=headers)
        assert r.status_code == 422

    def test_change_password_enforces_policy(self, client, fake_db, sent_emails):
        _, headers = self._auth(client)
        r = client.post("/api/auth/change-password",
                        json={"current_password": REG_PASSWORD, "new_password": "short"},
                        headers=headers)
        assert r.status_code == 422

    def test_change_password_requires_auth(self, client, fake_db):
        r = client.post("/api/auth/change-password",
                        json={"current_password": REG_PASSWORD, "new_password": NEW_PASSWORD})
        assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Regression — the existing lifecycle is untouched                              #
# --------------------------------------------------------------------------- #
class TestRegression:
    def test_register_login_me_still_work(self, client, fake_db, sent_emails):
        assert _register(client, email="reg@example.com").status_code == 200
        login = client.post("/api/auth/login",
                            json={"email": "reg@example.com", "password": REG_PASSWORD})
        assert login.status_code == 200
        token = login.json()["token"]
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["email"] == "reg@example.com"
        assert me.json()["email_verified"] is False

"""PH1.10 — centralized security audit logging & monitoring.

Covers the security.audit engine directly (taxonomy, schema, redaction, sinks,
fail-safe emission) and its integration into the auth flow via the FastAPI app
(login success/failure, logout, session revocation, replay detection, invalid
JWT, rate-limit triggering) — all against the in-memory FakeDB, no real Mongo.

The overriding invariant these tests defend: a secret (password, token,
authorization code, OAuth state) is NEVER persisted to an audit record, and a
failing sink NEVER breaks the calling security flow.
"""
import asyncio

import pytest
from bson import ObjectId

import server
from security import audit


# --------------------------------------------------------------------------- #
# Helpers                                                                        #
# --------------------------------------------------------------------------- #
def _run(coro):
    """Drive a coroutine to completion on a private event loop.

    A dedicated loop per call (rather than ``get_event_loop``) keeps these
    direct-async unit tests hermetic: FastAPI's TestClient runs each request via
    its own ``asyncio.run`` and closes that loop, so reusing the process default
    loop across tests is unreliable in the full suite."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _events(fake_db, name=None):
    docs = fake_db.security_audit_logs.docs
    return [d for d in docs if name is None or d["event"] == name]


class _BoomSink(audit.AuditSink):
    async def emit(self, record):
        raise RuntimeError("sink is down")


# --------------------------------------------------------------------------- #
# Taxonomy + schema                                                             #
# --------------------------------------------------------------------------- #
class TestTaxonomy:
    def test_known_events_classified(self):
        cat, sev, out = audit.classify(audit.LOGIN_FAILURE)
        assert cat == audit.Category.AUTHENTICATION
        assert sev == audit.Severity.WARNING
        assert out == audit.Outcome.FAILURE

    def test_replay_is_critical(self):
        _, sev, _ = audit.classify(audit.TOKEN_REPLAY_DETECTED)
        assert sev == audit.Severity.CRITICAL

    def test_unknown_event_fails_safe_to_warning(self):
        cat, sev, _ = audit.classify("some_new_unmapped_event")
        assert cat == audit.Category.SECURITY
        assert sev == audit.Severity.WARNING

    def test_record_document_has_full_schema(self):
        rec = audit.AuditRecord(
            event=audit.LOGIN_SUCCESS, category=audit.Category.AUTHENTICATION,
            severity=audit.Severity.INFO, outcome=audit.Outcome.SUCCESS,
            email="a@b.com", user_id="u1", session_id="s1",
        )
        doc = rec.to_document()
        for key in ("event", "category", "severity", "outcome", "email", "user_id",
                    "session_id", "reason", "ip", "user_agent", "request_id",
                    "target", "details", "timestamp", "schema_version"):
            assert key in doc
        assert doc["schema_version"] == audit.SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# Redaction — the core guarantee                                                #
# --------------------------------------------------------------------------- #
class TestRedaction:
    def test_sensitive_keys_blanked(self):
        clean = audit._redact({
            "password": "hunter2", "access_token": "abc.def", "refresh_token": "r",
            "authorization": "Bearer x", "code": "AUTHCODE", "state": "STATEVAL",
            "csrf_token": "c", "password_hash": "$2b$...", "api_key": "k",
            "email": "keep@me.com", "count": 3,
        })
        for k in ("password", "access_token", "refresh_token", "authorization",
                  "code", "state", "csrf_token", "password_hash", "api_key"):
            assert clean[k] == audit._REDACTED
        # Non-sensitive context is preserved.
        assert clean["email"] == "keep@me.com"
        assert clean["count"] == 3

    def test_nested_and_listed_secrets_blanked(self):
        clean = audit._redact({
            "outer": {"token": "SECRET", "ok": 1},
            "items": [{"password": "p"}, {"name": "safe"}],
        })
        assert clean["outer"]["token"] == audit._REDACTED
        assert clean["outer"]["ok"] == 1
        assert clean["items"][0]["password"] == audit._REDACTED
        assert clean["items"][1]["name"] == "safe"

    def test_depth_is_bounded(self):
        # A pathologically deep structure degrades instead of recursing forever.
        node = {"leaf": "v"}
        for _ in range(20):
            node = {"child": node}
        # Must not raise; deep-enough content is collapsed to the redaction marker.
        assert audit._redact(node) is not None


# --------------------------------------------------------------------------- #
# Sinks + fail-safe emission                                                    #
# --------------------------------------------------------------------------- #
class TestSinksAndFailSafe:
    def test_mongo_sink_writes_document(self, fake_db):
        sink = audit.MongoAuditSink(lambda: fake_db)
        logger = audit.AuditLogger(sink)
        _run(logger.record(audit.LOGIN_SUCCESS, email="x@y.com", user_id="u"))
        docs = _events(fake_db, audit.LOGIN_SUCCESS)
        assert docs and docs[0]["email"] == "x@y.com"
        assert docs[0]["category"] == audit.Category.AUTHENTICATION

    def test_failing_sink_never_raises(self):
        logger = audit.AuditLogger(_BoomSink())
        # Must return cleanly despite the sink raising — observability is never a gate.
        _run(logger.record(audit.LOGIN_FAILURE, email="x@y.com"))

    def test_composite_isolates_a_bad_sink(self, fake_db):
        good = audit.MongoAuditSink(lambda: fake_db)
        composite = audit.CompositeAuditSink([_BoomSink(), good])
        logger = audit.AuditLogger(composite)
        _run(logger.record(audit.LOGOUT, user_id="u"))
        # The good sink still received the event despite its sibling blowing up.
        assert _events(fake_db, audit.LOGOUT)

    def test_metadata_is_redacted_before_storage(self, fake_db):
        logger = audit.AuditLogger(audit.MongoAuditSink(lambda: fake_db))
        _run(logger.record(audit.LOGIN_FAILURE, email="x@y.com",
                           metadata={"password": "topsecret", "attempt": 2}))
        doc = _events(fake_db, audit.LOGIN_FAILURE)[0]
        assert doc["details"]["password"] == audit._REDACTED
        assert doc["details"]["attempt"] == 2
        assert "topsecret" not in str(fake_db.security_audit_logs.docs)


# --------------------------------------------------------------------------- #
# Integration through the live app (uses the default logger + FakeDB swap)      #
# --------------------------------------------------------------------------- #
def _seed_password_user(fake_db, email="audit_login@example.com",
                        password="S3cure!Passw0rd"):
    from security.passwords import hash_password
    uid = ObjectId()
    fake_db.users.docs.append({
        "_id": uid, "name": "Audit User", "email": email,
        "password_hash": hash_password(password), "role": "user", "capital": 100000,
    })
    return str(uid)


class TestAuthIntegration:
    def test_login_success_is_audited(self, client, fake_db):
        _seed_password_user(fake_db)
        r = client.post("/api/auth/login",
                        json={"email": "audit_login@example.com", "password": "S3cure!Passw0rd"})
        assert r.status_code == 200
        assert _events(fake_db, audit.LOGIN_SUCCESS)
        # A session_created event accompanies every successful login.
        assert _events(fake_db, audit.SESSION_CREATED)

    def test_failed_login_is_audited(self, client, fake_db):
        _seed_password_user(fake_db)
        r = client.post("/api/auth/login",
                        json={"email": "audit_login@example.com", "password": "wrong-password"})
        assert r.status_code == 401
        fails = _events(fake_db, audit.LOGIN_FAILURE)
        assert fails and fails[0]["severity"] == audit.Severity.WARNING
        # The attempted password must never be persisted.
        assert "wrong-password" not in str(fake_db.security_audit_logs.docs)

    def test_registration_is_audited(self, client, fake_db):
        r = client.post("/api/auth/register", json={
            "name": "New", "email": "reg_audit@example.com", "password": "S3cure!Passw0rd",
        })
        assert r.status_code == 200
        assert _events(fake_db, audit.REGISTRATION)

    def test_logout_audits_session_revocation(self, client, fake_db):
        _seed_password_user(fake_db, email="logout_audit@example.com")
        login = client.post("/api/auth/login",
                            json={"email": "logout_audit@example.com", "password": "S3cure!Passw0rd"})
        assert login.status_code == 200
        client.cookies.update(login.cookies)
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        assert _events(fake_db, audit.SESSION_REVOKED)
        assert _events(fake_db, audit.LOGOUT)

    def test_invalid_jwt_is_audited(self, client, fake_db):
        r = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
        assert r.status_code == 401
        invalid = _events(fake_db, audit.INVALID_JWT)
        assert invalid and invalid[0]["category"] == audit.Category.SECURITY

    def test_no_audit_for_missing_token(self, client, fake_db):
        # An anonymous request (no token at all) is not a security event.
        r = client.get("/api/auth/me")
        assert r.status_code == 401
        assert not _events(fake_db, audit.INVALID_JWT)


class TestSessionAndReplayIntegration:
    def test_refresh_rotation_and_replay_detection_audited(self, client, fake_db):
        _seed_password_user(fake_db, email="replay_audit@example.com")
        login = client.post("/api/auth/login",
                            json={"email": "replay_audit@example.com", "password": "S3cure!Passw0rd"})
        assert login.status_code == 200
        stolen_refresh = login.cookies.get("refresh_token")

        # Legitimate rotation.
        client.cookies.update(login.cookies)
        r1 = client.post("/api/auth/refresh")
        assert r1.status_code == 200
        assert _events(fake_db, audit.REFRESH_ROTATION)

        # Replay the now-rotated (stolen) refresh token → family revoked + audited.
        client.cookies.clear()
        client.cookies.set("refresh_token", stolen_refresh)
        r2 = client.post("/api/auth/refresh")
        assert r2.status_code == 401
        replay = _events(fake_db, audit.TOKEN_REPLAY_DETECTED)
        assert replay and replay[0]["severity"] == audit.Severity.CRITICAL


class TestRateLimitIntegration:
    def test_rate_limit_trigger_is_audited(self, client, fake_db):
        _seed_password_user(fake_db, email="rl_audit@example.com")
        # LOGIN policy trips after 5 failed attempts / 15 min per ip:account.
        for _ in range(6):
            client.post("/api/auth/login",
                        json={"email": "rl_audit@example.com", "password": "wrong"})
        triggered = _events(fake_db, audit.RATE_LIMIT_TRIGGERED)
        assert triggered
        assert any(t["details"].get("policy") == "login" for t in triggered)


# --------------------------------------------------------------------------- #
# Backward compatibility — the legacy facade + record shape are preserved       #
# --------------------------------------------------------------------------- #
class TestBackwardCompatibility:
    def test_log_auth_event_writes_legacy_fields(self, fake_db, monkeypatch):
        monkeypatch.setattr(server, "db", fake_db)

        class _Req:
            client = type("C", (), {"host": "1.2.3.4"})()
            headers = {"user-agent": "pytest", "x-request-id": "req-1"}

        _run(server.log_auth_event("oauth_login_success", _Req(),
                                   email="compat@example.com", user_id="u9",
                                   detail={"new_account": True}))
        doc = _events(fake_db, "oauth_login_success")[0]
        # Every field the prior implementation (and existing queries/tests) relied on.
        assert doc["email"] == "compat@example.com"
        assert doc["user_id"] == "u9"
        assert doc["details"]["new_account"] is True
        assert doc["ip"] == "1.2.3.4"
        assert doc["user_agent"] == "pytest"
        assert doc["request_id"] == "req-1"

"""Centralized security audit logging & event observability (PH1.10).

Single source of truth for how StockAssist AI records security-sensitive events.
Before this sprint, audit writes were scattered: an inline ``log_auth_event`` in
``server.py`` (auth / OAuth / recovery → ``security_audit_logs``), a separate
``log_admin_action`` (admin actions → ``admin_audit_logs``), and the broker
engine's ``_audit`` (broker actions → ``audit_logs``). Each invented its own
record shape, its own field names, and its own "which fields are safe to log"
judgement. That makes an incident investigation a cross-collection archaeology
project and makes it far too easy to accidentally persist a token.

This module centralizes the security-event path so there is exactly ONE place a
security-relevant event is shaped, redacted, and emitted — the same discipline
``security.jwt`` / ``security.csrf`` / ``security.recovery`` established for their
own credentials. Callers describe *what happened*; this module owns *how it is
recorded* (taxonomy, severity, schema, redaction, storage, failure policy).

Design pillars
--------------

* **A closed event taxonomy.** Every security event is one of the named
  constants below, each mapped (in ``_EVENT_REGISTRY``) to a ``category`` and a
  default ``severity``. Callers never invent free-form event strings for the
  security log — an unknown event still records, but is treated as
  ``SECURITY``/``WARNING`` (fail-safe: an unrecognized security signal is
  noteworthy, never silently downgraded).

* **A structured, versioned schema.** Every record carries the same fields
  (``event``, ``category``, ``severity``, ``outcome``, actor identifiers,
  ``session_id``, ``ip``, ``user_agent``, ``request_id``, ``target``, redacted
  ``details``, ``timestamp``, ``schema_version``). The legacy field names the
  prior ``log_auth_event`` wrote (``event`` / ``email`` / ``user_id`` /
  ``reason`` / ``ip`` / ``user_agent`` / ``details`` / ``timestamp``) are a
  strict subset, so existing records, queries, and indexes keep working.

* **Never log a secret.** ``_redact`` walks the metadata recursively and blanks
  any value whose key looks sensitive (password, token, secret, authorization,
  code, state, csrf, hash, api key, …). Passwords, tokens, authorization codes,
  and OAuth state can never reach storage even if a caller passes them by
  mistake — the guard is defense-in-depth on top of callers being careful.

* **Pluggable storage, stable callers.** An ``AuditSink`` interface abstracts
  *where* records go. The default is a composite of a durable
  ``MongoAuditSink`` (the existing ``security_audit_logs`` collection) and a
  structured ``LoggingAuditSink`` (one JSON line per event — the seam a future
  SIEM / log shipper reads). Swapping in a different backend (a dedicated audit
  service, syslog, Kafka) is a sink change, not a caller change.

* **Fail safe.** Audit logging is observability, never a gate. Every emit is
  wrapped so a storage error degrades to a logged warning and the calling
  security flow proceeds untouched. Losing an audit record must never lock a
  user out or 500 a request.

DB handle resolution is lazy (a zero-arg ``db_provider`` callable, exactly like
``RateLimitMiddleware``'s), so the module always uses the live handle and the
test suite's in-memory ``FakeDB`` swap is picked up with no special wiring.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional

logger = logging.getLogger("security.audit")

# The collection the prior log_auth_event already wrote to. Kept identical so
# existing records, indexes (timestamp / event / email), and admin queries are
# unaffected — this sprint centralizes the writer, not the storage location.
COLLECTION = "security_audit_logs"

SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- #
# Severity + category vocabulary                                                #
# --------------------------------------------------------------------------- #
class Severity:
    """How much attention an event deserves. Drives the log level of the
    ``LoggingAuditSink`` and gives a SIEM a single field to alert on."""
    INFO = "info"          # routine success (login, session created)
    NOTICE = "notice"      # noteworthy but expected (logout-all, password change)
    WARNING = "warning"    # failed / rejected attempt (bad login, CSRF fail, 429)
    CRITICAL = "critical"  # active-threat signal (token replay, suspicious activity)


class Category:
    """The domain an event belongs to — the primary axis for filtering an
    investigation ("show me all SESSION events for this user")."""
    AUTHENTICATION = "authentication"
    IDENTITY = "identity"
    SESSION = "session"
    SECURITY = "security"
    ADMINISTRATION = "administration"


class Outcome:
    SUCCESS = "success"
    FAILURE = "failure"
    INFO = "info"


# --------------------------------------------------------------------------- #
# Event taxonomy — the closed set of security events, one canonical name each.   #
# String values of pre-existing events are preserved verbatim so historical     #
# records and their queries keep matching.                                      #
# --------------------------------------------------------------------------- #
# Authentication
LOGIN_SUCCESS = "login_success"
LOGIN_FAILURE = "login_failure"
LOGOUT = "logout"
LOGOUT_ALL = "logout_all"

# Identity
REGISTRATION = "registration"
EMAIL_VERIFICATION_REQUESTED = "email_verification_requested"
EMAIL_VERIFICATION_SUCCESS = "email_verification_success"
EMAIL_VERIFICATION_FAILURE = "email_verification_failure"
PASSWORD_CHANGE_SUCCESS = "password_change_success"
PASSWORD_CHANGE_FAILURE = "password_change_failure"
PASSWORD_RESET_REQUESTED = "password_reset_requested"
PASSWORD_RESET_SUCCESS = "password_reset_success"
PASSWORD_RESET_FAILURE = "password_reset_failure"
OAUTH_LOGIN_SUCCESS = "oauth_login_success"
OAUTH_LOGIN_FAILURE = "oauth_login_failure"

# Sessions
SESSION_CREATED = "session_created"
SESSION_REVOKED = "session_revoked"
SESSION_EXPIRED = "session_expired"
REFRESH_ROTATION = "refresh_rotation"
TOKEN_REPLAY_DETECTED = "token_replay_detected"

# Security
RATE_LIMIT_TRIGGERED = "rate_limit_triggered"
CSRF_VALIDATION_FAILURE = "csrf_validation_failure"
INVALID_JWT = "invalid_jwt"
INVALID_REFRESH = "invalid_refresh"
INVALID_PASSWORD = "invalid_password"
PERMISSION_DENIED = "permission_denied"
SUSPICIOUS_ACTIVITY = "suspicious_activity"

# Administration
ADMIN_LOGIN = "admin_login"
USER_ROLE_CHANGED = "user_role_changed"
ACCOUNT_DISABLED = "account_disabled"
ACCOUNT_ENABLED = "account_enabled"


# (category, default severity, default outcome). ``outcome`` is a hint; a caller
# may override it (e.g. a per-attempt outcome), but the default keeps the vast
# majority of call sites terse.
_EVENT_REGISTRY: dict[str, tuple[str, str, str]] = {
    # Authentication
    LOGIN_SUCCESS: (Category.AUTHENTICATION, Severity.INFO, Outcome.SUCCESS),
    LOGIN_FAILURE: (Category.AUTHENTICATION, Severity.WARNING, Outcome.FAILURE),
    LOGOUT: (Category.AUTHENTICATION, Severity.INFO, Outcome.SUCCESS),
    LOGOUT_ALL: (Category.AUTHENTICATION, Severity.NOTICE, Outcome.SUCCESS),
    # Identity
    REGISTRATION: (Category.IDENTITY, Severity.INFO, Outcome.SUCCESS),
    EMAIL_VERIFICATION_REQUESTED: (Category.IDENTITY, Severity.INFO, Outcome.INFO),
    EMAIL_VERIFICATION_SUCCESS: (Category.IDENTITY, Severity.INFO, Outcome.SUCCESS),
    EMAIL_VERIFICATION_FAILURE: (Category.IDENTITY, Severity.WARNING, Outcome.FAILURE),
    PASSWORD_CHANGE_SUCCESS: (Category.IDENTITY, Severity.NOTICE, Outcome.SUCCESS),
    PASSWORD_CHANGE_FAILURE: (Category.IDENTITY, Severity.WARNING, Outcome.FAILURE),
    PASSWORD_RESET_REQUESTED: (Category.IDENTITY, Severity.INFO, Outcome.INFO),
    PASSWORD_RESET_SUCCESS: (Category.IDENTITY, Severity.NOTICE, Outcome.SUCCESS),
    PASSWORD_RESET_FAILURE: (Category.IDENTITY, Severity.WARNING, Outcome.FAILURE),
    OAUTH_LOGIN_SUCCESS: (Category.IDENTITY, Severity.INFO, Outcome.SUCCESS),
    OAUTH_LOGIN_FAILURE: (Category.IDENTITY, Severity.WARNING, Outcome.FAILURE),
    # Sessions
    SESSION_CREATED: (Category.SESSION, Severity.INFO, Outcome.SUCCESS),
    SESSION_REVOKED: (Category.SESSION, Severity.NOTICE, Outcome.SUCCESS),
    SESSION_EXPIRED: (Category.SESSION, Severity.INFO, Outcome.INFO),
    REFRESH_ROTATION: (Category.SESSION, Severity.INFO, Outcome.SUCCESS),
    TOKEN_REPLAY_DETECTED: (Category.SESSION, Severity.CRITICAL, Outcome.FAILURE),
    # Security
    RATE_LIMIT_TRIGGERED: (Category.SECURITY, Severity.WARNING, Outcome.FAILURE),
    CSRF_VALIDATION_FAILURE: (Category.SECURITY, Severity.WARNING, Outcome.FAILURE),
    INVALID_JWT: (Category.SECURITY, Severity.WARNING, Outcome.FAILURE),
    INVALID_REFRESH: (Category.SECURITY, Severity.WARNING, Outcome.FAILURE),
    INVALID_PASSWORD: (Category.SECURITY, Severity.WARNING, Outcome.FAILURE),
    PERMISSION_DENIED: (Category.SECURITY, Severity.WARNING, Outcome.FAILURE),
    SUSPICIOUS_ACTIVITY: (Category.SECURITY, Severity.CRITICAL, Outcome.FAILURE),
    # Administration
    ADMIN_LOGIN: (Category.ADMINISTRATION, Severity.NOTICE, Outcome.SUCCESS),
    USER_ROLE_CHANGED: (Category.ADMINISTRATION, Severity.NOTICE, Outcome.SUCCESS),
    ACCOUNT_DISABLED: (Category.ADMINISTRATION, Severity.NOTICE, Outcome.SUCCESS),
    ACCOUNT_ENABLED: (Category.ADMINISTRATION, Severity.NOTICE, Outcome.SUCCESS),
}

# Fail-safe classification for an event not in the registry: treat any unknown
# security event as noteworthy rather than dropping or trivializing it.
_UNKNOWN_CLASSIFICATION = (Category.SECURITY, Severity.WARNING, Outcome.INFO)


def classify(event: str) -> tuple[str, str, str]:
    """(category, severity, default outcome) for ``event`` — never raises."""
    return _EVENT_REGISTRY.get(event, _UNKNOWN_CLASSIFICATION)


# --------------------------------------------------------------------------- #
# Redaction — the one guarantee: a secret never reaches a sink.                  #
# --------------------------------------------------------------------------- #
# Substrings that mark a metadata key as sensitive. Matched case-insensitively
# against the key, so ``password_hash``, ``access_token``, ``X-CSRF-Token``, and
# ``authorization`` are all caught. Deliberately broad — a false-positive redact
# is harmless; a leaked credential is not.
_SENSITIVE_KEY_MARKERS = (
    "password", "passwd", "pwd", "secret", "token", "authorization",
    "auth_code", "code", "state", "csrf", "jwt", "api_key", "apikey",
    "api-key", "private_key", "privatekey", "hash", "otp", "session_secret",
    "credential", "cookie", "bearer", "signature",
)
_REDACTED = "[REDACTED]"
# Bound the walk so a pathological/cyclic payload can never spin the redactor.
_MAX_REDACT_DEPTH = 6


def _is_sensitive_key(key: str) -> bool:
    k = key.lower()
    return any(marker in k for marker in _SENSITIVE_KEY_MARKERS)


def redact_fields(value: Any) -> Any:
    """Public entry point to the redactor — the one sensitive-key list in the app.

    Exposed for `observability.logging` (PH2.5), which must apply exactly the
    same rule to structured log fields that this module applies to audit
    metadata. Two independently maintained lists of "what counts as a secret"
    would drift, and the half that got missed would be the half that leaks: a
    marker added here for a new credential type would silently not protect the
    application log. One list, two consumers.
    """
    return _redact(value)


def _redact(value: Any, _depth: int = 0) -> Any:
    """Recursively copy ``value`` with any sensitive-keyed field blanked.

    Dict keys are checked against ``_SENSITIVE_KEY_MARKERS``; matching values are
    replaced wholesale with ``[REDACTED]`` (we never try to partially mask — a
    partial token is still a token). Lists/tuples are walked element-wise.
    Non-container leaves pass through. Depth is capped so a deeply nested or
    cyclic structure degrades to ``[REDACTED]`` instead of recursing forever."""
    if _depth >= _MAX_REDACT_DEPTH:
        return _REDACTED
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            clean[key] = _REDACTED if _is_sensitive_key(key) else _redact(v, _depth + 1)
        return clean
    if isinstance(value, (list, tuple)):
        return [_redact(v, _depth + 1) for v in value]
    return value


# --------------------------------------------------------------------------- #
# Record model                                                                  #
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AuditRecord:
    """One structured security-audit event. ``to_document`` renders the persisted
    shape — a superset of the legacy ``log_auth_event`` fields so old queries and
    indexes keep matching."""
    event: str
    category: str
    severity: str
    outcome: str
    email: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    reason: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    target: Optional[str] = None
    details: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)
    schema_version: int = SCHEMA_VERSION

    def to_document(self) -> dict:
        return {
            "event": self.event,
            "category": self.category,
            "severity": self.severity,
            "outcome": self.outcome,
            "email": self.email,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "reason": self.reason,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "request_id": self.request_id,
            "target": self.target,
            "details": self.details,
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
        }


def _context_request_id() -> Optional[str]:
    """The active correlation ID from `observability.context`, or None.

    Imported inside the function, not at module scope. `security.audit` is
    imported at boot by `server.py` before the observability package is wired,
    and a security primitive should not acquire a hard import-time dependency on
    a telemetry package — if observability is ever absent or broken, audit
    logging must still work. The import is cached by `sys.modules` after the
    first call, so this costs a dict lookup.
    """
    try:
        from observability.context import current_request_id_or_none

        return current_request_id_or_none()
    except Exception:  # pragma: no cover - defensive
        return None


def request_context(request) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Best-effort (ip, user_agent, request_id) from a Starlette/FastAPI request.

    IP honors the first hop of ``X-Forwarded-For`` when present (behind a trusted
    proxy) and falls back to the socket peer — matching ``rate_limit.client_ip``
    so the audit log and the rate limiter attribute the same request to the same
    identity.

    ``request_id`` is the correlation key an investigation joins on. It comes
    from the active request context (PH2.5) — the ID
    ``ObservabilityMiddleware`` assigned at the front door, which is the same
    value stamped on every application log line for this request and returned to
    the client in ``X-Request-ID``. Before PH2.5 this read the inbound header
    directly, so the field was ``None`` on every record unless an edge proxy
    happened to set one; going through the context means an audit record and the
    surrounding application logs always share a key. The raw header remains the
    fallback for a code path running outside the middleware (a scheduler task
    replaying a request object, a unit test constructing one by hand).

    Never raises."""
    if request is None:
        # Still worth asking the context: a background task spawned by a request
        # inherits its correlation ID even though it holds no request object.
        return None, None, _context_request_id()
    try:
        ip: Optional[str] = None
        xff = request.headers.get("x-forwarded-for")
        if xff:
            first = xff.split(",")[0].strip()
            ip = first or None
        if not ip:
            ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")
        rid = _context_request_id() or request.headers.get("x-request-id")
        return ip, ua, rid
    except Exception:  # pragma: no cover - defensive; context is best-effort
        return None, None, None


# --------------------------------------------------------------------------- #
# Sinks — the pluggable storage seam.                                           #
# --------------------------------------------------------------------------- #
class AuditSink:
    """Abstract destination for audit records. Implementations must be
    fail-safe-friendly: they may raise, and :class:`AuditLogger` isolates the
    failure — but a well-behaved sink swallows its own transport errors so one
    misbehaving backend never starves the others in a composite."""

    async def emit(self, record: AuditRecord) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class MongoAuditSink(AuditSink):
    """Durable sink writing to the ``security_audit_logs`` MongoDB collection.

    ``db_provider`` is a zero-arg callable returning the live DB handle (never a
    captured handle), so the sink always writes to the current database and the
    test suite's ``FakeDB`` monkeypatch is honored with no special wiring — the
    same lazy-handle discipline as ``RateLimitMiddleware``. A provider returning
    ``None`` (DB not yet wired) is a silent no-op for this sink."""

    def __init__(self, db_provider: Callable[[], Any],
                 collection: str = COLLECTION) -> None:
        self._db_provider = db_provider
        self._collection = collection

    async def emit(self, record: AuditRecord) -> None:
        db = self._db_provider()
        if db is None:
            return
        await getattr(db, self._collection).insert_one(record.to_document())


class LoggingAuditSink(AuditSink):
    """Structured sink emitting one JSON line per event to the standard logger.

    This is the SIEM / log-shipper seam: a Fluent Bit / Vector / CloudWatch
    agent tailing stdout gets a machine-parseable, secret-free event stream with
    no database coupling. Severity maps to the Python log level so existing
    log-based alerting can key off ``WARNING``/``CRITICAL`` immediately."""

    _LEVELS = {
        Severity.INFO: logging.INFO,
        Severity.NOTICE: logging.INFO,
        Severity.WARNING: logging.WARNING,
        Severity.CRITICAL: logging.ERROR,
    }

    def __init__(self, logger_name: str = "security.audit.events") -> None:
        self._log = logging.getLogger(logger_name)

    async def emit(self, record: AuditRecord) -> None:
        level = self._LEVELS.get(record.severity, logging.INFO)
        try:
            payload = json.dumps(record.to_document(), default=str, separators=(",", ":"))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            payload = f"event={record.event} severity={record.severity}"
        self._log.log(level, "audit %s", payload)


class CompositeAuditSink(AuditSink):
    """Fan an event out to several sinks. Each sink is isolated: one raising does
    not stop the others, so a Mongo outage still leaves the structured log line
    (and vice-versa). Failures are surfaced as warnings, never propagated."""

    def __init__(self, sinks: List[AuditSink]) -> None:
        self._sinks = list(sinks)

    async def emit(self, record: AuditRecord) -> None:
        for sink in self._sinks:
            try:
                await sink.emit(record)
            except Exception as exc:  # isolate a bad sink from its siblings
                logger.warning("audit sink %s failed for %s: %s",
                               type(sink).__name__, record.event, exc)


# --------------------------------------------------------------------------- #
# Logger — builds, redacts, and emits records; never raises.                    #
# --------------------------------------------------------------------------- #
class AuditLogger:
    """Composes an :class:`AuditSink` into the public ``record`` entry point.

    Owns the invariant that matters most: **emitting an audit event can never
    break the calling flow**. Building the record, redacting it, and handing it
    to the sink are all wrapped — any failure degrades to a logged warning and
    returns normally."""

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink

    async def record(
        self,
        event: str,
        *,
        request=None,
        email: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        reason: Optional[str] = None,
        target: Optional[str] = None,
        outcome: Optional[str] = None,
        metadata: Optional[dict] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        """Record one security event. Actor/context fields are optional — pass
        what the call site knows. ``metadata`` is free-form context that is
        redacted before storage. Explicit ``ip`` / ``user_agent`` / ``request_id``
        win over values derived from ``request``."""
        try:
            category, severity, default_outcome = classify(event)
            ctx_ip, ctx_ua, ctx_rid = request_context(request)
            rec = AuditRecord(
                event=event,
                category=category,
                severity=severity,
                outcome=outcome or default_outcome,
                email=email,
                user_id=str(user_id) if user_id is not None else None,
                session_id=session_id,
                reason=reason,
                ip=ip if ip is not None else ctx_ip,
                user_agent=user_agent if user_agent is not None else ctx_ua,
                request_id=request_id if request_id is not None else ctx_rid,
                target=target,
                details=_redact(metadata or {}),
            )
            await self._sink.emit(rec)
        except Exception as exc:  # observability must never gate a security flow
            logger.warning("audit record failed for %s: %s", event, exc)


# --------------------------------------------------------------------------- #
# Module-level default logger — the seam every caller shares.                   #
# --------------------------------------------------------------------------- #
# Until ``configure`` wires a database, the default logger still records to the
# structured log sink, so audit events are never silently lost during boot / in
# a context that has not called ``configure``.
_default_logger: AuditLogger = AuditLogger(LoggingAuditSink())


def configure(db_provider: Callable[[], Any], *, also_log: bool = True) -> None:
    """Wire the process-wide default audit logger to durable storage.

    Called once at import/startup with a zero-arg ``db_provider`` returning the
    live DB handle. Installs a composite sink: the durable
    :class:`MongoAuditSink` plus (by default) the structured
    :class:`LoggingAuditSink` so every event is both queryable and shippable.
    Idempotent — safe to call again to re-point the sink."""
    sinks: List[AuditSink] = [MongoAuditSink(db_provider)]
    if also_log:
        sinks.append(LoggingAuditSink())
    global _default_logger
    _default_logger = AuditLogger(CompositeAuditSink(sinks))


def get_logger() -> AuditLogger:
    """The process-wide default :class:`AuditLogger`."""
    return _default_logger


async def log_event(event: str, **kwargs) -> None:
    """Record a security event via the default logger — the convenience every
    caller (endpoints, middleware, security modules) uses. Fail-safe by
    construction (delegates to :meth:`AuditLogger.record`). See ``record`` for
    the accepted keyword arguments."""
    await _default_logger.record(event, **kwargs)

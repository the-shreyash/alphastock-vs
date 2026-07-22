"""StockAssist AI security package.

Home for cross-cutting, security-relevant primitives that must behave
identically everywhere they are used. Current tenants:

* `security.cookies` (PH1.3) — centralized authentication-cookie policy.
* `security.cors` (PH1.4) — centralized, environment-driven CORS policy.
* `security.headers` (PH1.4b) — centralized HTTP response security headers
  (HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy,
  Permissions-Policy, and the Cross-Origin isolation family). The only place
  security response headers are set.
* `security.passwords` (PH1.5) — centralized password policy + bcrypt
  primitives; the only place passwords are validated, hashed, or verified.
* `security.jwt` (PH1.6) — centralized JWT issuance/verification (claims,
  lifetimes, `iat`/`jti`/`aud`/`iss`/`ver`). Pure crypto: the only place a
  token is encoded or decoded.
* `security.sessions` (PH1.6) — DB-backed `SessionStore`: refresh-token
  families, rotation, reuse detection, and revocation (logout / logout-all).
  The stateful counterpart to `security.jwt`.
* `security.csrf` (PH1.7) — signed double-submit CSRF token bound to the
  session; the only place a CSRF token is minted, its cookie set/cleared, or a
  request validated. Enforced (via `CSRFMiddleware`) on cookie-authenticated,
  state-changing requests; Bearer-authenticated requests are CSRF-safe and
  exempt.
* `security.rate_limit` (PH1.7) — centralized rate limiting & abuse protection:
  named per-endpoint policies, a pluggable `RateLimitStore` (MongoDB now,
  Redis-ready), progressive lockout with `Retry-After`, and a platform-wide
  `RateLimitMiddleware`. The one limiter — the prior inline login lockout is
  folded in here.
* `security.recovery` (PH1.8) — centralized identity-recovery tokens: the only
  place a single-use email-verification or password-reset token is minted,
  verified, or burned. Signed handle (`<token_id>.<hmac>`, bound to purpose +
  user) backed by an authoritative `recovery_tokens` record that enforces
  expiry and atomic single-use (replay protection). The stateful counterpart to
  the recovery endpoints in `server.py`.
* `security.audit` (PH1.10) — centralized security audit logging & event
  observability: the closed event taxonomy (authentication / identity / session /
  security / administration), the structured, versioned record schema, recursive
  secret redaction (a token can never reach a sink), and a pluggable `AuditSink`
  interface (durable Mongo + structured/SIEM-ready logging, composed) behind a
  fail-safe `AuditLogger`. The one place a security-relevant event is shaped,
  redacted, and emitted — the prior scattered `log_auth_event` now delegates
  here. Every emit is best-effort: audit logging is observability, never a gate.
* `security.secrets` (PH1.9, extended in PH2.3) — centralized secret &
  configuration management: the authoritative `SECRET_REGISTRY` of every
  environment variable (category, sensitivity, which environments require it),
  boot-time `validate_config()` that fails closed on missing/weak critical
  secrets, and value-free reporting that never logs a secret. The one place the
  app's configuration surface is defined; drives `backend/.env.example` and
  `.claude/SECRETS.md`.
  PH2.3 added the **source** layer: `resolve_all()` applies one precedence order
  (`<NAME>_FILE` pointer → `$SECRETS_DIR/<name>` Docker/K8s mount → plaintext
  env) to every variable, and `load_secrets()` materializes the result into
  `os.environ` once at boot — which is why every existing `os.environ` consumer
  in this codebase reads file-backed secrets without a call-site change. Fails
  closed on an unreadable file or two competing sources; `reload_secrets()`
  re-reads for rotation and reports changes by fingerprint, never by value.
  See `docs/deployment/SECRETS.md`.
* `security.roles` (PH1.12) — centralized role taxonomy & assignment
  authorization (finding F-1): the `ASSIGNABLE_ROLES` allowlist and
  `validate_role_assignment`, which enforces least privilege on elevation
  (only a `super_admin` may grant the admin-tier roles). The one place a role
  written to `users.role` is validated.
* `security.identifiers` (PH1.12) — centralized ObjectId parsing (finding F-2):
  `parse_object_id`, the one place an untrusted identifier becomes a
  `bson.ObjectId`. Turns malformed ids into a clean 400 instead of an
  accidental 500. Use at every trust boundary (path/query/body); trusted ids
  (a verified JWT `sub`, an `_id` read back from Mongo) stay raw.

Subsequent hardening sprints add their own modules here per PRODUCTION_ROADMAP.md.
"""

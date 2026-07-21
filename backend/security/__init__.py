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
* `security.secrets` (PH1.9) — centralized secret & configuration management:
  the authoritative `SECRET_REGISTRY` of every environment variable (category,
  sensitivity, which environments require it), boot-time `validate_config()`
  that fails closed on missing/weak critical secrets, and value-free reporting
  that never logs a secret. The one place the app's configuration surface is
  defined; drives `backend/.env.example` and `.claude/SECRETS.md`.

Subsequent hardening sprints add their own modules here per PRODUCTION_ROADMAP.md.
"""

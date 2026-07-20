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

Subsequent hardening sprints add their own modules here (CSRF, rate limiting)
per PRODUCTION_ROADMAP.md.
"""

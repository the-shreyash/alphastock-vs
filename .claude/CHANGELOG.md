# StockAssist AI
## Changelog

This file records documentation-system versions and, from v1.0 launch onward, product release notes. Documentation versions apply to the `.claude/` documentation set as a whole.

---

# Sprint PH1.9 — Secrets & Supply Chain Security — 2026-07-22

**Production Hardening PH1.9 complete. Configuration is now centralized and
validated: a misconfigured or weakly-configured production refuses to boot, the
dependency set is fully pinned and continuously audited, and no hard-coded
secret remains in the repository. Zero frontend or business-logic change.**

This sprint delivers the roadmap's PH1.8 (Secrets & Environment Hardening)
content plus the supply-chain core of PH1.11, executed under the "PH1.9" label
(Identity Recovery had consumed the PH1.8 slot). Before it, the app read ~40 env
vars ad hoc with no boot-time validation (a missing `JWT_SECRET` only surfaced
at the first token operation), `docker-compose.yml` shipped a weak `JWT_SECRET`
fallback and a hard-coded n8n password, `.env.example` was git-ignored (so no
shareable template existed), and four dependencies floated on `>=` bounds.

Added

- `backend/security/secrets.py` — the single source of truth for the
  configuration surface. `SECRET_REGISTRY` declares every variable (category,
  `sensitive`, `required_in` environments, `min_length`, example). Boot-time
  `validate_config()` **fails closed**: it aggregates every problem into one
  value-free error and is called from `server.py` *before* the Mongo client or
  any router. Severity is environment-aware — the core trio (`MONGO_URL`,
  `DB_NAME`, `JWT_SECRET`) is fatal everywhere; production additionally makes
  fatal any missing required secret, a signing key < 32 chars, any
  placeholder/weak value, a half-configured OAuth or broker pair,
  `ENABLE_AUTO_LOGIN=true`, a weak `ADMIN_PASSWORD`, and the absence of any AI
  provider. No secret value is ever logged (`redact()`, presence-only summary).
  Reuses `security.cookies.is_production` so environment semantics never drift.
- `backend/.env.example` + `frontend/.env.example` — committed, placeholder-only
  templates. The backend template is **generated** from the registry by
  `backend/scripts/generate_env_example.py` (with a `--check` mode CI enforces),
  so code and template can never drift.
- `.github/workflows/security-audit.yml` — `pip-audit --strict` + `pip check`
  (backend), `npm audit` (frontend), `gitleaks` history scan + a tracked-`.env`
  guard, and the `.env.example` sync check. Runs on push/PR and weekly.
- `backend/scripts/audit_dependencies.py` — local `pip check` + `pip-audit`
  runner (degrades gracefully when pip-audit isn't installed).
- `.claude/SECRETS.md` — the secrets & supply-chain runbook: inventory,
  environment strategy, rotation policy (per secret class), dependency-update
  policy, accepted-advisory backlog, and leaked-credential incident response.
- `backend/tests/test_secrets.py` — 38 hermetic tests (env-aware validation,
  core-trio enforcement, cross-field invariants, no-secret-in-output,
  registry/example-sync integrity, accessors).

Changed

- `backend/server.py` — calls `security.secrets.validate_config()` immediately
  after `load_dotenv`; logs the presence-only summary + any warnings. This is
  the only server change (additive, non-breaking).
- `backend/requirements.txt` — now **fully exact-pinned**: locked the 4 floating
  `>=` bounds (aiohappyeyeballs, psutil, anthropic, litellm) and applied 7 in-pin
  security patches (aiohttp 3.13.5→3.14.1, cryptography 48.0.0→48.0.1, httplib2
  0.31.2→0.32.0, pillow 12.2.0→12.3.0, pyasn1 0.6.3→0.6.4, pymongo 4.5.0→4.6.3,
  python-multipart 0.0.29→0.0.31). Verified to co-resolve; starlette/litellm/
  ecdsa advisories deferred (framework-locked / AI-scope / unfixed) — see
  SECRETS.md §8.
- `docker-compose.yml` — removed the weak `JWT_SECRET` fallback (now required via
  `${JWT_SECRET:?…}`) and the hard-coded n8n password `alphapartner123` (now
  required `${N8N_BASIC_AUTH_PASSWORD:?…}`); added `APP_ENV`.
- `.gitignore` — negations (`!.env.example`, `!**/.env.example`) so example
  templates are committable while every real `.env` stays ignored.
- `backend/security/__init__.py` — documents the new `security.secrets` tenant.

Security notes

- **Fail-fast, fail-closed:** `APP_ENV=production` with any missing/weak required
  variable now exits before serving a single request, with a named-variable
  error listing all problems at once.
- **No secret in git history:** verified via `git log --all -S <value>` that no
  real provider key or `JWT_SECRET` was ever committed and no `.env` was ever
  tracked. One committed value existed — the n8n dev password `alphapartner123`
  in `docker-compose.yml` (5 commits) — now externalized; low severity
  (local-only editor basic-auth), documented in SECRETS.md §9.
- **Rotation reminder:** live provider keys currently in local `.env` files have
  existed in plaintext dev files and must be rotated before production launch
  (SECRETS.md §9).

Tests

- `backend/tests/test_secrets.py` — 38/38 passing. Full backend hermetic suite
  regression-checked (the pre-existing `requests`-based integration files and
  one pre-existing `test_trading_engine` failure are unrelated and unchanged by
  this sprint). Manual verification: prod-missing-secret aborts startup;
  valid-prod loads clean; the real dev `.env` boots.

---

# Sprint PH1.8 — Identity Recovery — 2026-07-22

**Production Hardening PH1.8 complete. The identity lifecycle is now
recoverable: users can verify their email, change their password, and reset a
forgotten one — each single-use, expiring, and safe against enumeration — with
zero frontend break and no change to the JWT/CSRF/rate-limit/OAuth layers.**

Before this sprint an account, once created, had no recovery path: no email
verification, no password change, no forgotten-password reset, and no way to
force-sign-out after a credential rotation. PH1.8 closes all four as a single
reusable `backend/security/recovery.py` module composed by new `/api/auth`
endpoints.

Added

- `backend/security/recovery.py` — the single source of truth for
  identity-recovery tokens. A **signed handle backed by an authoritative
  record**: the token handed to the user is `<token_id>.<HMAC(secret,
  "prefix|purpose|user_id|token_id")>` (unforgeable, bound to exactly one user +
  one purpose), while a `recovery_tokens` document carries `issued_at` /
  `expires_at` / `used_at` so **expiry and single-use are enforced
  authoritatively**. `consume()` burns a token with an atomic compare-and-set
  (`used_at: None → now`) — replay-safe. Issuing a fresh token invalidates the
  user's outstanding unused ones of that purpose (one live link at a time).
  - Purposes & lifetimes (env-overridable): **email verification 24h**
    (`RECOVERY_VERIFY_TTL_SECONDS`), **password reset 30 min**
    (`RECOVERY_RESET_TTL_SECONDS`).
  - HMAC key: `RECOVERY_SECRET` or the required `JWT_SECRET` (domain-separated by
    a versioned prefix — no weak default, fail-closed). Never logs a token.
- New `/api/auth` endpoints (all recovery logic centralized, never inline):
  - **`POST /verify-email`** — redeem a verification token (public; single-use).
  - **`POST /verify-email/request`** — resend the verification link
    (authenticated; generic response; no-op if already verified).
  - **`POST /forgot-password`** — start reset (public; **always** a generic
    response — no email enumeration; OAuth-only accounts silently skipped).
  - **`POST /reset-password`** — reset with a token + new password (public;
    enforces the PH1.5 policy; revokes every session; stamps
    `password_changed_at`).
  - **`POST /change-password`** — authenticated; requires the **current**
    password, rejects an unchanged one, enforces the PH1.5 policy, then revokes
    every session and stamps `password_changed_at` (signed out everywhere).
- `backend/tests/test_recovery.py` (28 tests) — hermetic: token
  mint/verify/consume, single-use/replay, expiry, purpose-binding, signature
  tamper, reissue invalidation; and the full endpoint matrix (verify success /
  expired / replay, forgot-password generic response, reset single-use / expiry /
  policy / session-revocation, change-password current-password /
  unchanged-password / policy / sign-out, and the untouched register→login→me
  lifecycle).

Changed

- **User model gains email-verification status:** `email_verified` (bool),
  `email_verified_at`, `verified_by` (`"email"` | `"google"`). New
  email/password registrations start **unverified** and are emailed a
  verification link (out-of-band via `BackgroundTasks`; a slow/failed mailer
  never delays sign-up). **Login is deliberately NOT blocked on this flag** —
  backward-compatible; it is the hook a future verified-only gate flips on.
- **Google OAuth accounts are verified on creation/link.** Google already
  asserts (and we already enforce) a verified email, so a Google-native or
  Google-linked account is marked `verified_by: "google"` with no separate
  verification email; pre-PH1.8 Google accounts self-heal the flag on next login.
- **`security.csrf`** default exempt paths extended with the three *public*
  recovery entrypoints (`/verify-email`, `/forgot-password`, `/reset-password`)
  — they carry their own single-use authorization or are anonymous, so they rely
  on no ambient cookie authority. The *authenticated* recovery actions
  (`change-password`, `verify-email/request`) stay CSRF-protected for cookie
  clients.
- **`services.email_service`** gains three branded templates:
  `EMAIL_VERIFICATION`, `PASSWORD_RESET`, `PASSWORD_CHANGED` (security
  confirmation).
- **Register response** gains the additive `email_verified` field so the SPA can
  surface a "verify your email" prompt; the rest of the contract is unchanged.
- Startup creates the `recovery_tokens` indexes (`token_id` unique,
  `(user_id, purpose)`, and a TTL index on `expires_at`).

Security properties

- **No enumeration:** forgot-password and verify-email/request return an
  identical generic message whether or not the account exists; recovery reads run
  through the existing rate limiter (`PASSWORD` policy, 5 / hour).
- **Single-use + replay-safe:** every recovery token is burned atomically on
  redemption; a replayed link is a generic 400.
- **Full session invalidation:** a reset OR change revokes every refresh family
  (`SessionStore.revoke_all_for_user`) and bumps `password_changed_at`, so
  outstanding access tokens also go stale on next use — the user re-logs in
  everywhere.

Out of scope (unchanged): JWT crypto, rate limiting, CSRF enforcement model,
OAuth login, cookie/header policy, trading engine, AI, frontend.

---

# Sprint PH1.7 — CSRF Protection & Rate Limiting — 2026-07-21

**Production Hardening PH1.7 complete. The two remaining abuse-surface gaps —
an unowned CSRF token layer and login-only rate limiting — are closed, without
any frontend change or public-API break.**

Before this sprint, cross-site state-changing requests were defended only by
`SameSite=Lax` (a real baseline, but no token layer), and rate limiting was a
single inline `db.login_attempts` lockout on `/api/auth/login`. PH1.7 adds a
signed, session-bound CSRF token layer and a centralized, progressive rate
limiter with a platform-wide flooding backstop — both as reusable
`backend/security/` modules.

Added

- `backend/security/csrf.py` — the single source of truth for CSRF. A **signed
  double-submit cookie bound to the session** (OWASP pattern): a non-HttpOnly
  `csrf_token` cookie carrying `<nonce>.<HMAC(secret, "prefix|sid|nonce")>`,
  echoed by the client in `X-CSRF-Token`.
  - **`CSRFMiddleware`** enforces on a request iff it is a mutating method, a
    non-exempt path, carries **no** `Authorization: Bearer` header, and is
    cookie-authenticated. Validation = header==cookie (double-submit) **and** the
    HMAC verifying against the cookie session's `sid` (binding). Failure → **403**
    (`code: CSRF_FAILED`), fail-closed.
  - **Bearer requests exempt by construction** — the SPA's `Authorization: Bearer`
    path cannot be forged cross-site and carries no ambient cookie authority, so
    enforcement targets exactly the cookie-only attack surface. This is what makes
    the rollout require **zero frontend changes**.
  - HMAC key: `CSRF_SECRET` or the required `JWT_SECRET` (domain-separated);
    cookie `Secure`/`SameSite`/`Domain` resolved through `security.cookies`.
- `backend/security/rate_limit.py` — the single limiter. Named per-endpoint
  policies, a pluggable `RateLimitStore` interface (shipped `MongoRateLimitStore`,
  Redis-ready), fixed-window counting, and **progressive lockout** with automatic
  expiry and `Retry-After`.
  - Policies (env-overridable via `RATE_LIMIT_<NAME>`): **login 5 / 15 min** per
    `ip:account` (failures only; escalating lockout), **register 5 / hour** per IP,
    **refresh 20 / min** per session, **password 5 / hour**, **authenticated API
    120 / min** per user, **public API 60 / min** per IP.
  - **`RateLimitMiddleware`** — platform-wide flooding backstop over all `/api`
    traffic (per-user when authenticated, per-IP otherwise); emits
    `X-RateLimit-*`; a storage error fails **open** (logged) so the throttle can
    never take the API down.
- `backend/tests/test_csrf.py` (18 tests) and `backend/tests/test_rate_limit.py`
  (26 tests) — hermetic: token mint/verify/bind, every middleware
  exempt/enforce/reject path, store/limiter semantics, lockout + escalation,
  `Retry-After`, and the real auth-endpoint integrations.

Changed

- `backend/server.py`:
  - `_issue_session` and `/refresh` now plant/re-mint the CSRF cookie;
    `logout`/`logout-all` clear it.
  - `login` replaces the inline `login_attempts` block with the centralized
    limiter (`peek` → `record_failure` → `reset`); `register` and `refresh` gain
    inline limits. Observable lockout behavior is preserved byte-for-byte.
  - Middleware wired: `apply_csrf_protection` + `apply_rate_limiting` registered
    **before** CORS/headers so a 403/429 still carries CORS + security headers
    (execution order: Security Headers → CORS → Rate Limiter → CSRF → route).
  - Startup drops the `login_attempts` index; adds `rate_limits` indexes
    (compound `(key, kind, window_start)` + TTL on `expires_at`).
- `backend/security/__init__.py` — tenant index lists `csrf` and `rate_limit`.
- `backend/tests/test_password_policy.py` — two tests that asserted the internal
  `login_attempts` collection now assert the new limiter's observable behavior
  (`rate_limits`); the login-compatibility guarantees are unchanged.

Migration

- **No data migration, no API break.** The `login_attempts` collection is simply
  no longer written (a Mongo TTL/manual drop can retire it). Existing Bearer-based
  clients are unaffected by CSRF (exempt); a future cookie-only client reads the
  `csrf_token` cookie and sets `X-CSRF-Token`. All limits are env-tunable.

Threat-model rows "Cross-site state-changing request via cookie auth (CSRF
proper)", "Credential stuffing / brute force", and "Endpoint flooding / token
abuse" move to ✅ Closed. Remaining PH1 work: PH1.8 (secrets/env validator),
PH1.9 (WebSocket authorization), PH1.10–PH1.12.

---

# Sprint PH1.6 — JWT Lifecycle & Session Security — 2026-07-20

**Production Hardening PH1.6 complete. The two highest-value open authentication
risks — long-lived access tokens (R-06) and refresh-token replay — are closed.**

Before this sprint, access tokens lived 24 hours, refresh tokens never rotated
or revoked, and logout only deleted cookies (the JWTs stayed cryptographically
valid until natural expiry). A stolen token was usable for a day; a captured
refresh token for a week, undetectably. PH1.6 centralizes all JWT logic, shortens
the access token to 15 minutes, rotates refresh tokens on every use with theft
detection, and adds a durable server-side revocation store — without changing any
public API contract.

Added

- `backend/security/jwt.py` — the single source of truth for JWT issuance and
  verification (pure crypto, no FastAPI/DB). The only place a token is encoded or
  decoded.
  - **Hardened claim set on every token:** `iat`, `jti` (unique id — the handle
    the session store rotates/revokes), `aud`, `iss`, `sid` (owning session), and
    `ver` (token schema version), alongside the existing `sub`/`email`/`type`/`exp`.
  - **Strict, fail-closed verification** (`decode_token`) — validates signature,
    `exp`, `aud`, `iss`, requires every claim, and checks `type`/`ver`. Raises
    typed, framework-neutral `TokenExpired`/`TokenInvalid` (never a raw `pyjwt`
    error), which the web layer maps to a generic 401.
  - **Configurable lifetimes** — `JWT_ACCESS_TTL_SECONDS` (default **900 / 15 min**)
    and `JWT_REFRESH_TTL_SECONDS` (default **604800 / 7 days**), plus `JWT_ISSUER` /
    `JWT_AUDIENCE`. `TOKEN_VERSION` is a pinned-in-code global kill-switch.
  - **`password_changed_at` support** (`token_issued_before`) — the anchor a future
    password change / forced-logout uses to invalidate every outstanding token by
    `iat`, for both access and refresh.
- `backend/security/sessions.py` — `SessionStore`, the DB-backed session (refresh-
  token family) store. One MongoDB `sessions` document per login/device.
  - **Rotation on every refresh** — the presented refresh token is single-use; a
    new `jti` becomes current and the old one is dead.
  - **Reuse detection** — replaying an already-rotated refresh token (its `jti` no
    longer current) is treated as theft and **revokes the entire family**, so both
    attacker and victim are forced to re-login (closes refresh-replay).
  - **Revocation** — `revoke` (single session / logout) and `revoke_all_for_user`
    (logout-all-devices); durable, TTL-reaped at `expires_at`.
  - **PH1.10 groundwork** — captures `user_agent`, `ip`, created/last-used
    timestamps, and exposes `list_for_user` for the future active-sessions screen.
- `backend/tests/test_jwt_sessions.py` — 34 hermetic tests: claim set, every
  rejection path (expired / wrong-aud / wrong-iss / bad-signature / wrong-type /
  stale-version / missing-claim / garbage), rotation, reuse→family-revoke, revoke,
  revoke-all, `password_changed_at`, and the full HTTP lifecycle.
- `POST /api/auth/logout-all` — authenticated "sign out of all devices" endpoint.

Changed

- `backend/server.py` — `create_access_token`/`get_current_user`/`refresh`/`logout`
  now delegate to `security.jwt` + `security.sessions`; the inline `pyjwt`
  encode/decode and the old 24h/7d helpers are gone. Login/register/OAuth open a
  session via a shared `_issue_session` helper (captures device/IP). Refresh
  rotates **both** cookies. Startup provisions `sessions` indexes (unique
  `session_id`, `user_id`, TTL on `expires_at`). `import jwt as pyjwt` removed.
- `backend/security/__init__.py` — tenant index lists `jwt` and `sessions`.
- `backend/tests/test_cookie_security.py` — the PH1.3 test that asserted refresh
  does *not* rotate the refresh cookie (explicitly deferred to PH1.6) now asserts
  the rotated refresh cookie carries the hardened flags.

Migration

- **Clean cutover.** Strict validation rejects pre-PH1.6 tokens (no `aud`/`ver`),
  so active users re-authenticate once via the normal 401 → login flow on deploy.
  No data migration. `cookies.py` (cookie `Max-Age`) is unchanged — the access
  cookie's 24h Max-Age harmlessly outlives the 15-min JWT (expired token →
  silent refresh); aligning them is a cosmetic PH1.3-owned follow-up.

Risk R-06 closed. Threat-model rows "Stolen long-lived access token" and "Refresh
token replay" move to ✅ Closed. Note: the roadmap's placeholder `tokens.py` was
realized as two cohesive modules (`jwt.py` pure crypto + `sessions.py` stateful
store) and refresh defaults to 7 days (env-tunable to the SECURITY.md 30-day
target); both deviations are recorded in PRODUCTION_ROADMAP.md PH1.6.

---

# Sprint PH1.4b — HTTP Security Headers — 2026-07-20

**Production Hardening PH1.4b complete. The "no security headers" gap (flagged in
the PH0 audit and de-scoped from the CORS-only PH1.4) is closed.**

Before this sprint the API emitted **no** security response headers — every
response could be framed, MIME-sniffed, and leaked referrers, with no transport
pinning and no content policy. PH1.4b adds the full defensive header set in one
centralized, environment-driven place, wired *after* CORS so even CORS
preflight and rejected-origin responses carry the headers. API contracts and
payloads are unchanged; only response headers are added.

Added

- `backend/security/headers.py` — the single source of truth for HTTP response
  security headers. No security header may be set anywhere else.
  - **Middleware** (`SecurityHeadersMiddleware`, `apply_security_headers`) — a
    pure-ASGI middleware (not `BaseHTTPMiddleware`) chosen so it never buffers
    the body (safe for streaming/SSE), touches only the `http` scope
    (WebSocket upgrades pass through), and **enforces** its values (overwriting
    any inner-handler value so the posture cannot be weakened downstream).
  - **Emitted on every response:** `X-Content-Type-Options: nosniff`,
    `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
    a locked-down `Permissions-Policy` (camera/mic/geolocation/USB/… disabled),
    `Cross-Origin-Opener-Policy: same-origin`,
    `Cross-Origin-Resource-Policy: same-origin`, `X-XSS-Protection: 0` (the
    deprecated, buggy legacy auditor neutralized), and a strict
    `Content-Security-Policy` (`default-src 'none'; base-uri 'none';
    form-action 'none'; frame-ancestors 'none'`) — **no `unsafe-inline` /
    `unsafe-eval` anywhere.**
  - **Conditional:** `Strict-Transport-Security`
    (`max-age=63072000; includeSubDomains`) is emitted **only** over HTTPS or in
    production (honors `X-Forwarded-Proto` behind a TLS-terminating proxy;
    `preload` opt-in) so a plain-HTTP dev origin never pins itself.
    `Cross-Origin-Embedder-Policy: require-corp` is implemented but **opt-in**
    (`CROSS_ORIGIN_EMBEDDER_POLICY`) — it protects the API's own JSON not at all
    yet would break same-origin HTML tooling (Swagger UI) pulling cross-origin
    subresources without CORP.
  - **Environment-driven & nonce-capable:** every header value is overridable
    via environment variable (`CONTENT_SECURITY_POLICY`, `PERMISSIONS_POLICY`,
    `REFERRER_POLICY`, `X_FRAME_OPTIONS`, `CROSS_ORIGIN_*`, and the `HSTS_*`
    family). A `{nonce}` placeholder in the CSP is replaced per request with a
    fresh `secrets.token_urlsafe(16)` nonce, also exposed on
    `request.state.csp_nonce` for a future HTML handler to stamp onto
    `<script nonce=…>` tags.
- `backend/tests/test_security_headers.py` — 35 hermetic tests (no network, no
  Mongo): HSTS enablement/value and HTTPS/production gating, the strict default
  CSP and its nonce substitution, the cross-origin isolation family, every
  environment override, and real wire behavior through the middleware on
  success, error, CORS-preflight, and nonce-based responses.

Changed

- `backend/server.py` — wires `apply_security_headers(app)` immediately after
  `apply_cors(app)` (so headers wrap CORS responses too). No other change.
- `backend/security/__init__.py` — records `security.headers` in the tenant index.

Notes

- **CORP is safe with the credentialed CORS frontend:** `Cross-Origin-Resource-Policy`
  only governs *no-cors* cross-origin loads, so the frontend's `mode: cors`
  requests (governed by `security.cors`) are unaffected while the API can no
  longer be embedded as an opaque subresource.
- **Swagger UI (`/docs`) and any HTML served from the API origin** will be
  restricted by the strict `default-src 'none'` CSP; a deployment that needs it
  relaxes `CONTENT_SECURITY_POLICY` (or, preferably, disables docs in
  production). No production JSON API endpoint is affected.

Verification

- `pytest backend/tests/test_security_headers.py backend/tests/test_cors_hardening.py
  backend/tests/test_cookie_security.py backend/tests/test_password_policy.py`
  → **128 passed** (33 new + 95 regression). Manual: real-app smoke check on
  `/api` in production mode confirmed all headers present (including HSTS) and
  the CORS 400 rejection still carries `X-Frame-Options`.

Scope note

- Out of scope and untouched per the sprint definition: JWT/refresh, cookies,
  OAuth, password policy, CSRF, rate limiting, email verification,
  infrastructure/Docker, logging, database, and the frontend. Deferred:
  request-scoped nonce propagation into rendered HTML templates (the header/state
  plumbing exists now; no HTML is rendered by the API yet).

---

# Sprint PH1.5 — Password Policy & Account Protection — 2026-07-19

**Production Hardening PH1.5 complete. Finding H10 (password half) closed; risk
R-05 partially mitigated (password-policy half — the rate-limiting half remains
PH1.7).**

Replaced the accept-anything password handling (`password: str`, no validator,
implicit bcrypt cost) with a production-grade, centralized password policy.
Enforcement is at the model layer, so weak passwords are rejected with 422
before they ever reach hashing. Existing users, login, and OAuth are unchanged:
the policy applies to **new** passwords only, and the register/login API
contracts (payloads, success shapes, generic 401, `ip:email` lockout) are
byte-for-byte preserved.

Added

- `backend/security/passwords.py` — the single source of truth for password
  policy, hashing, and verification. No password may be validated, hashed, or
  verified outside this module.
  - **Policy** (`validate_new_password`, returns every violated rule at once):
    12–64 characters (and ≤72 UTF-8 bytes — the bcrypt truncation boundary);
    uppercase + lowercase + number + special character required; rejects
    common passwords, email-/name-derived passwords, repeated-character
    passwords (<5 unique chars), and sequential runs (alphabet/digits/qwerty
    rows, forward or reversed). Leading/trailing whitespace is normalized away
    before validation *and* hashing.
  - **Hashing** — bcrypt with an explicit, pinned cost factor
    (`BCRYPT_ROUNDS = 12`); previously the cost was the silent library default.
  - **Verification** — constant-time (`bcrypt.checkpw`) and never raises:
    empty/malformed stored hashes return `False` after a dummy-hash comparison,
    which also **timing-equalizes** login failures (unknown email, OAuth-only
    account, and wrong password all cost one bcrypt comparison).
- `backend/security/data/common_passwords.txt` — bundled, curated common-password
  blocklist (~450 lowercase entries; padding-resistant matching strips trailing
  digits/punctuation, so `Monkey987654!!` still matches `monkey`). No new
  dependencies.
- `backend/tests/test_password_policy.py` — 40 hermetic tests: every policy rule
  (boundaries, character classes, common/sequential/repeated/identity-derived,
  whitespace, multibyte length), hashing primitives (explicit cost, round-trip,
  never-raises, delegation), register-endpoint enforcement (422 + no user
  created, clean error contract, unchanged success shape), and the sprint's
  compatibility guarantees (legacy weak-password login works, OAuth-native
  401-not-500, indistinguishable failures, lockout preserved/cleared).

Changed

- `backend/models.py` — `UserCreate` gained a `model_validator` that normalizes
  the password and enforces the policy (422 with actionable rule messages;
  cross-field checks against the user's own email/name). `UserLogin` is
  deliberately unvalidated so existing accounts keep working.
- `backend/server.py` — removed the inline `hash_password`/`verify_password`
  definitions (and the now-unused `bcrypt` import); both are imported from
  `security.passwords`. Login now always runs exactly one bcrypt comparison
  (timing-equalization) — this also fixed a real bug where password login
  against a Google-OAuth-native account (`password_hash: ""`) raised
  `ValueError` → 500 instead of the generic 401. Added a sanitizing
  `RequestValidationError` handler: 422 bodies now carry only `loc`/`msg`/`type`
  — FastAPI's default handler echoed the submitted input (including raw
  passwords) back in every validation error.
- `backend/scripts/seed_dev_admin.py` — hashes via `security.passwords`
  (consistent cost factor; still dev-only, still no policy on seeded creds).
- `backend/tests/_fakedb.py` — `update_one` now supports `$inc` (match and
  upsert), making the login-lockout counter hermetically testable for the
  first time.
- `frontend/src/pages/Register.jsx` — client-side minimum raised 6 → 12 to
  mirror the server policy; full rule feedback comes from the API's 422
  messages, which the existing `formatApiError` already renders.

Security outcome

- No weak password can enter the system through any current registration path;
  policy logic exists in exactly one module (no per-endpoint drift).
- bcrypt cost is an explicit, reviewed constant; verification can no longer
  500 on hostile or legacy data.
- Login failures are generic **and** timing-equalized; validation errors no
  longer reflect submitted values. Password-hash exposure re-verified: no
  endpoint returns `password_hash` (covered by tests).
- Brute-force lockout (5 attempts / 15 min per `ip:email`) preserved unchanged
  and now under test.

Not in scope (deferred, unchanged)

- Password change endpoint and password reset flow (reviewed: neither exists;
  no reset tokens are generated anywhere) — deferred with `EmailStr` and email
  verification to an unscheduled PH1.5b (SMTP provider decision OR-6 moves with
  them). Platform-wide rate limiting remains PH1.7; JWT lifetime/rotation and
  session revocation remain PH1.6. Audit-logging of password-login events
  remains a tracked gap (SECURITY_ARCHITECTURE.md §22, PH1.6/PH1.7 candidate).

---

# Sprint PH1.4 — CORS Hardening — 2026-07-18

**Production Hardening PH1.4 complete. Risk R-03 / finding B3 closed.**

Replaced the development-friendly, unsafe CORS configuration with a
production-safe, environment-driven, exact-match origin allowlist, and
centralized the whole policy into a single module. The prior configuration
defaulted to `Access-Control-Allow-Origin: *` **with `allow_credentials=True`**
— a combination the Fetch standard forbids (the browser refuses to expose a
credentialed response to a wildcard origin) and a security hole (any origin was
trusted with the session cookie). Frontend communication is unchanged.

Added

- `backend/security/cors.py` — the single source of truth for CORS. Resolves an
  exact-match origin allowlist from the environment and assembles the
  `CORSMiddleware` configuration. Exposes `allowed_origins()`, `cors_kwargs()`,
  and `apply_cors(app)`.
  - **Origins** — `CORS_ALLOWED_ORIGINS` is canonical (comma-separated, exact
    scheme+host+port, trailing slash normalized away). Legacy `CORS_ORIGINS`
    and `FRONTEND_URL` are still honored as inputs (backward compatible), merged
    and de-duplicated. A literal `*` is stripped from **every** source, so a
    wildcard can never enter the allowlist or pair with credentials.
  - **Development fallback** — when nothing is configured and `APP_ENV` is not
    `production`, the local dev origins `http://localhost:3000` and
    `http://localhost:5173` are assumed, so the app runs with zero config.
  - **Production fail-closed** — nothing is assumed in production; an
    unconfigured allowlist is empty and every cross-origin request is rejected.
  - **Credentials** allowed (cookie-based auth) — safe by construction because
    origins are always an exact list, never the wildcard.
  - **Methods** restricted to `GET, POST, PUT, PATCH, DELETE, OPTIONS`;
    **request headers** restricted to `Authorization, Content-Type, Accept,
    Origin, X-Requested-With`; **no response headers exposed**. Preflight cached
    for 10 minutes.
- `backend/tests/test_cors_hardening.py` — 30 hermetic tests: allowlist
  resolution (canonical var, legacy inputs, trailing-slash/whitespace
  normalization, merge+dedupe, dev defaults, production fail-closed, wildcard
  stripped from every source), assembled-kwargs invariants (never wildcard,
  credentials on, methods/headers restricted, nothing exposed), and real wire
  behavior on a live middleware (allowed-origin preflight + simple request
  reflect ACAO and `Allow-Credentials: true`; unknown origin gets no grant;
  disallowed method/header preflight rejected; localhost works out of the box;
  production rejects unconfigured localhost).
- Documented CORS env vars (`CORS_ALLOWED_ORIGINS`) in `backend/.env` and removed
  the unsafe `CORS_ORIGINS=*` line.

Changed — `backend/server.py`

- Removed the inline wildcard-defaulting `app.add_middleware(CORSMiddleware, …)`
  block (and the now-unused `CORSMiddleware` import); CORS is now wired in via
  `apply_cors(app)` from `security.cors`.

Security outcome

- No wildcard origin remains anywhere; credentials are only ever granted to
  approved, exact-match origins (R-03 / B3 closed).
- CORS configuration is centralized — no duplicated or drifting CORS logic.
- Local development continues to work unchanged; the frontend on `localhost:3000`
  is allowed with credentials.

Not in scope (deferred, unchanged)

- Security **headers** (HSTS, X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy, CSP) were de-scoped from this
  CORS-only sprint and are carried forward as PH1.4b. The Google OAuth
  redirect-URI allowlist (`_allowed_google_redirect_uris`, PH1.2 scope) is
  untouched and continues to derive from `FRONTEND_URL` / `CORS_ORIGINS`.

---

# Sprint PH1.3 — Cookie Security Hardening — 2026-07-18

**Production Hardening PH1.3 complete. Risk R-04 closed.**

Hardened every authentication-related cookie for production and centralized the
cookie policy into a single module. No change to API contracts; login, logout,
refresh and the Google OAuth flow behave identically for clients — the cookies
they receive are now consistently and safely configured. Email/password and
Google auth are functionally unchanged.

Added

- `backend/security/` package + `backend/security/cookies.py` — the single
  source of truth for issuing and clearing every auth cookie. Resolves the
  Secure/HttpOnly/SameSite/Path/Max-Age/Domain posture from the environment and
  exposes `set_auth_cookies`, `set_access_cookie`, `set_refresh_cookie`,
  `set_oauth_state_cookie`, `clear_auth_cookies`, `clear_oauth_state_cookie`.
  - **Secure** is env-driven (`COOKIE_SECURE`) and **forced `True` when
    `APP_ENV=production`** regardless of the override — a production build can
    never ship an insecure auth cookie (closes R-04). Local dev defaults to
    `False` so cookies work over plain-HTTP `localhost`.
  - **HttpOnly** always `True` on all three cookies (JS never reads them).
  - **SameSite** defaults to `Lax` (CSRF baseline); configurable via
    `COOKIE_SAMESITE` (`lax`/`strict`/`none`). `None` is auto-degraded to `Lax`
    when the cookie would not also be `Secure` (browsers drop `None` without
    `Secure`). The OAuth-state cookie is never `Strict` so it survives the
    top-level redirect back from Google.
  - **Path** — session cookies at `/` (single-shot logout, no duplicate-path
    cookies); OAuth-state cookie scoped to `/api/auth`.
  - **Domain** — optional `COOKIE_DOMAIN` for subdomain session sharing;
    host-only when unset.
  - **Clearing** mirrors the exact key + path + domain + security attributes so
    the browser reliably deletes the cookie.
- `backend/tests/test_cookie_security.py` — 24 hermetic tests: policy resolution
  (prod forces Secure; dev default/override; SameSite default/invalid/`none`
  degrade/honored; domain), login/register cookie flags, production Secure
  enforcement, dev Secure override, configured Domain, logout clears both
  cookies with matching path, refresh re-issues a hardened access cookie (and
  does **not** rotate refresh — PH1.6 owns that), session-fixation overwrite,
  and the OAuth-state cookie (hardened, scoped to `/api/auth`, never `Strict`,
  Secure in prod, burned after a successful exchange).
- Documented cookie env vars (`COOKIE_SECURE`, `COOKIE_SAMESITE`, `COOKIE_DOMAIN`)
  in `backend/.env`.

Changed — `backend/server.py`

- Removed the local `set_auth_cookies` helper (which hardcoded `secure=False`)
  and the inline `set_cookie`/`delete_cookie` literals at all four call sites;
  they now delegate to `security.cookies`:
  - `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/google/session`
    → `set_auth_cookies` (hardened flags).
  - `POST /api/auth/logout` → `clear_auth_cookies` (clears both cookies with
    attributes that match how they were set).
  - `POST /api/auth/refresh` → `set_access_cookie` (was a raw `secure=False`
    `set_cookie`).
  - `_set_oauth_state_cookie` / `_clear_oauth_state_cookie` → delegate to
    `set_oauth_state_cookie` / `clear_oauth_state_cookie`; the state cookie now
    shares the unified Secure/SameSite/Domain posture instead of a hardcoded
    `secure=False`.

Security outcome

- Every auth cookie (`access_token`, `refresh_token`, `g_oauth_state`) carries
  `HttpOnly; SameSite` always, and `Secure` in production — no token can be sent
  over plain HTTP in production (R-04 closed).
- Logout removes every authentication cookie; refresh remains functional;
  session fixation is mitigated (fresh tokens overwrite on every login/register/
  OAuth); OAuth state is burned after use.
- Cookie policy is centralized — no duplicated cookie logic remains.

Not in scope (deferred, unchanged)

- CSRF **token** middleware for cookie-authenticated state-changing routes
  (SameSite=Lax provides the cookie-layer CSRF baseline; token middleware is
  tracked as the next hardening item). Refresh-token **rotation** and JWT
  lifetime changes remain PH1.6. CORS/security headers remain PH1.4.

---

# Sprint PH1.2 — Google OAuth Production Hardening — 2026-07-17

**Production Hardening PH1.2 complete. Risk R-02 fully closed.**

Hardened the legitimate Google OAuth flow (PH1.1 had removed the backdoors; this
sprint makes the remaining real flow production-safe). All changes preserve
email/password register and login unchanged.

Added

- `GET /api/auth/google/login-url` (`backend/server.py`) — server-side flow
  initiation. Generates a cryptographically random OAuth `state`, stores a
  **single-use server-side state record** (via `services/cache.py`: Redis when
  `REDIS_URL` is set, bounded in-memory fallback otherwise) bound to the chosen
  `redirect_uri` with an authoritative 600s TTL, **and** binds the state to the
  browser via a short-lived httponly `g_oauth_state` cookie. Validates the
  requested `redirect_uri` against an allowlist and returns the Google
  authorization URL. Fail-closed (401) when `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
  are unset. The client no longer constructs the Google URL.
- Single-use state consumption on callback (fetch-and-delete) → **replay
  protection** and cross-process, TTL-authoritative expiry; the callback
  `redirect_uri` must equal the one bound at flow start.
- `log_auth_event()` + immutable `db.security_audit_logs` collection (indexed at
  startup) — records every OAuth outcome (`oauth_login_success` with
  new_account/linked flags; `oauth_login_failure` with a `reason`: invalid_state,
  replayed_or_expired_state, invalid_redirect_uri, unverified_email,
  invalid_id_token, bad_issuer, sub_conflict, missing_id_token,
  token_exchange_failed, google_unavailable). Logs ip/user-agent/outcome, never
  tokens, codes, or state values (SECURITY.md logging rule).
- `google_sub` persisted on the user document and used as the **primary external
  identity**: accounts resolve by `google_sub` first (stable across Google
  profile/email changes), then by verified email for safe linking. An email
  already bound to a different `google_sub` is rejected (`sub_conflict`).
- `frontend/src/services/googleAuth.js` — shared `startGoogleLogin()` used by
  the Login and Register pages; calls the backend for the URL (with credentials
  so the state cookie is stored) and redirects.
- `frontend/src/pages/AuthCallback.jsx` — a proper `/auth/google/callback` route
  (added in `App.js`); forwards `code` **and** `state` to the session exchange.
- `backend/tests/test_oauth_hardening.py` — 26 hermetic tests: state issuance/
  randomness, missing/forged/mismatched state, **single-use/replay rejection,
  expired-or-absent server-side record**, unverified-email rejection,
  invalid/absent id_token, bad issuer, **id_token verified with client_id as
  audience**, redirect_uri allowlist + binding, token-exchange failure, Google
  network error (502), new-user creation, safe linking of an existing password
  account (password login still works), no duplicate accounts, role/capital
  preserved, **`sub`-primary identity across an email change, `sub_conflict`
  rejection**, and **audit-log assertions** (success/linked/invalid_state/
  unverified_email; no code or state value ever persisted).

Changed — `POST /api/auth/google/session` (`backend/server.py`)

- **CSRF + replay protection:** now requires `state`, validates it (constant-time)
  against the `g_oauth_state` cookie (per-browser binding), then consumes the
  single-use server-side record (fetch-and-delete). Rejects missing/mismatched
  state and replayed/expired state with 400.
- **Identity verification:** verifies the OIDC `id_token` (signature via Google's
  public keys, audience = our client_id, expiry) using `google-auth`, checks the
  issuer, and derives identity from the verified token instead of the `/userinfo`
  endpoint. **Rejects unverified Google emails (`email_verified != true`) with 401** —
  they never create or link an account (account-takeover guard).
- **redirect_uri allowlisting:** removed the hardcoded
  `http://localhost:3000/...` fallback; the redirect_uri must be allowlisted.
- **`sub`-primary identity + safe linking:** resolves by `google_sub` first
  (stable identity), then by verified email to link an existing email/password
  account (stores `google_sub`; leaves `password_hash`/`auth_provider` intact)
  rather than silently taking it over. Email stays the unique key (no duplicates);
  an email already bound to a different `google_sub` is rejected.
- Removed the dead legacy `session_id=` hash short-circuit in `App.js` and
  `AuthContext.jsx` (leftover from the flow removed in PH1.1).

Notes

- Two PH1.1 regression assertions in `test_auth_hardening.py` were updated:
  with `state` now mandatory and checked first, a lone forged code is rejected
  with 400 (still fail-closed, still no user created); the "not configured" 401
  contract moved to `GET /api/auth/google/login-url`.
- The `g_oauth_state` cookie deliberately mirrors the existing auth cookies'
  `secure=False` posture; unifying the secure/SameSite flags across all cookies
  is PH1.3's scope, not this sprint's.

---

# Sprint PH1.1 — Authentication Backdoor Removal — 2026-07-17

**Production Hardening PH1.1 complete. Findings B1 and B2 closed; risks R-01 and R-02 closed.**

Removed

- `GET /api/auth/auto-login` endpoint and the `ENABLE_AUTO_LOGIN` switch (`backend/server.py`) — finding B1, risk R-01. Admin sessions can no longer be obtained without credentials.
- Google OAuth demo-user fallback, `mock-code-for-testing` path, and the legacy fail-open `session_id` exchange against `demobackend.emergentagent.com` (`backend/server.py`) — finding B2, risk R-02. `/api/auth/google/session` is now fail-closed: it accepts only a Google authorization code and returns 401 when `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are unset. The orphan `session_token` cookie and `user_sessions` write (never validated anywhere) are gone.
- Startup admin seeding: the server no longer creates an admin with default password `admin123`, no longer force-resets the admin password on every boot, and no longer writes plaintext credentials to `memory/test_credentials.md` (same finding class as B1; closed under PH1.1).
- Frontend callers of removed paths: `autoLogin` in `AuthContext`, the "Quick Demo Login (Dev Mode)" button on the login page, and the legacy `session_id` exchange in `AuthCallback`.

Added

- `backend/scripts/seed_dev_admin.py` — idempotent dev-only admin seeding; refuses to run when `APP_ENV=production`; never resets an existing password.
- `backend/tests/test_auth_hardening.py` — 11 hermetic tests asserting the backdoors stay removed (404 on auto-login, 401/400 on all OAuth fallback payloads, no demo user ever created) and that register → login → me → refresh → logout still works.

Changed

- Live-server test fixtures (`test_phase5/6/7.py`) authenticate via `POST /api/auth/login` with env-driven admin credentials instead of auto-login, matching `test_backend`/`test_phase2`/`test_phase4`.
- `/api/auth/google/session` response now reports the user's actual role instead of hardcoded `"user"`.

---

# Documentation v1.2 — 2026-07-17

**Feature freeze. Production Hardening program introduced.**

Added

- `PRODUCTION_HARDENING.md` — master hardening architecture document: engineering audit baseline, risk matrix (R-01…R-15), production readiness score (4.2/10), priority matrix, security/infrastructure/deployment/testing/performance/documentation/monitoring/recovery strategies, operational·launch·certification checklists, open risks report (OR-1…OR-8), engineering standards addendum, and the Definition of Production Ready.
- `PRODUCTION_ROADMAP.md` — 36-sprint implementation roadmap: PH1 Production Security Hardening, PH2 Production Infrastructure & DevOps, PH3 Production Quality Assurance (12 sprints each), with per-sprint objective, scope, deliverables, expected files, dependencies, acceptance criteria, validation steps, rollback plan, difficulty, time, and success metrics; implementation sequencing and dependency graph.
- `CHANGELOG.md` — this file.
- ADR-027 in `DECISIONS.md` — Feature Freeze & Production Hardening Program; acknowledges the as-built FastAPI + CRA stack pending PH3.10 reconciliation.

Changed

- `INDEX.md` — added Production Hardening document category, documentation-map entries, and a "Production Hardening (Current Phase)" reading guide; version 1.2.
- `ROADMAP.md` — Phase 1 and Phase 2 marked COMPLETE; Production Hardening Interlude (PH1–PH3) inserted as the current phase; product Phases 3–9 blocked until Production Certification; version 1.2.
- `TASKS.md` — Current Focus replaced with the feature freeze and PH1.1 as next sprint; full PH1–PH3 status tracker added; version 1.2.
- `DECISIONS.md` — ADR-027 added; version 1.2.

Baseline

- `PRODUCTION_READINESS_REPORT.md` (Sprint 12 audit, 2026-07-17): verdict NOT READY FOR PRODUCTION. Six critical blockers (auth backdoors ×2, CORS wildcard + credentials, insecure cookies, broken Docker packaging, no CI/CD), five high-priority findings, five medium-priority findings.

---

# Documentation v1.1 — 2026-07-16

- Introduced `MARKET_DATA_ARCHITECTURE.md`; provider-independent market data architecture (ADR-026): Market Gateway, Source Manager, provider adapters, priority and failover strategy.
- Separated Connected Broker experience from Premium AI features.
- All affected documentation synchronized.

---

# Documentation v1.0

- Initial documentation system.

---

# End of Changelog

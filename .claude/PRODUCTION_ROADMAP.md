# StockAssist AI
## Production Hardening Roadmap (PH1 – PH3)

Version: 1.2

Status: PH1 In Progress — PH1.1 complete (2026-07-17), awaiting review before PH1.2

Date: 2026-07-17

Companion Documents: PRODUCTION_HARDENING.md (strategy, risk, certification) · SECURITY_ARCHITECTURE.md (security engineering blueprint each PH1 sprint implements against)

---

# Purpose

This roadmap operationalizes the Production Hardening program defined in PRODUCTION_HARDENING.md. It introduces three new phases, each of 12 implementation sprints. **No new product features ship during these phases.** ROADMAP.md's product Phases 3–9 resume only after Production Certification.

Phase order and gating:

```
MVP COMPLETE (Phase 1 Sprints 1–12, Phase 2 R1–R9)
        │
        ▼
PH1  Production Security Hardening      ── blocks everything downstream
        │  (PH2/PH3 tracks may run in parallel where noted)
        ▼
PH2  Production Infrastructure & DevOps ── blocks deployment
        ▼
PH3  Production Quality Assurance       ── blocks certification
        ▼
PRODUCTION CERTIFICATION → v1.0 LAUNCH
```

Sprint sizing: one sprint = one focused unit of work, ½ day to 3 days each. Difficulty scale: Low / Medium / High.

---

# PH1 — Production Security Hardening

Goal: eliminate every finding in PRODUCTION_HARDENING.md §2 marked CRITICAL/HIGH in the security domain, and bring the system into SECURITY.md compliance.

Architecture reference: every PH1 sprint implements against **SECURITY_ARCHITECTURE.md**, the authoritative security engineering blueprint. Each sprint below implements or extends the specific section(s) noted, and — per PRODUCTION_HARDENING.md §15 — must update those section(s) in the same PR (see SECURITY_ARCHITECTURE.md §32, Future Production Hardening Plan, for the full mapping).

---

## PH1.1 — Authentication Backdoor Removal

- **Status:** ✅ COMPLETE (2026-07-17). B1 and B2 removed; startup admin seeding (default `admin123` password, boot-time password reset, plaintext `memory/test_credentials.md` write) also removed as the same finding class. Dev seeding moved to `backend/scripts/seed_dev_admin.py`; guarded by `backend/tests/test_auth_hardening.py` (11 hermetic tests).
- **Architecture reference:** SECURITY_ARCHITECTURE.md §3 (Threat Model), §5 (Authentication Architecture).
- **Objective:** Remove both authentication backdoors (B1, B2) so no unauthenticated caller can obtain any session.
- **Scope:** Delete `GET /api/auth/auto-login` (`backend/server.py:3860`) and the `ENABLE_AUTO_LOGIN` switch. Delete the OAuth mock-code path, the demo-user fallback, and the legacy `session_id` exchange against `demobackend.emergentagent.com` (`backend/server.py:2672`). Replace dev convenience with a `scripts/seed_dev_admin.py` script guarded on `APP_ENV != production`.
- **Deliverables:** Endpoints removed; dev seeding script; tests asserting 404/401 on removed paths.
- **Files Expected:** `backend/server.py`, `backend/scripts/seed_dev_admin.py`, `backend/tests/test_auth_hardening.py`.
- **Dependencies:** None. **This is sprint zero of the entire program.**
- **Acceptance Criteria:** `grep -ri "auto-login\|mock-code\|emergentagent"` over source returns nothing; no code path issues a session without verified credentials; full suite green.
- **Validation Steps:** Run new tests; manual curl of removed endpoints returns 404; login/refresh/logout still work end-to-end.
- **Rollback Plan:** None permitted for the removals (PRODUCTION_HARDENING.md §14). If a legit flow breaks, fix forward.
- **Estimated Difficulty:** Low. **Estimated Time:** 0.5 day.
- **Success Metrics:** Risk R-01/R-02 closed; readiness authn score 2.0 → 5.0.

## PH1.2 — Google OAuth Production Flow

- **Architecture reference:** SECURITY_ARCHITECTURE.md §13 (Google OAuth Architecture), §29 (OAuth Login Sequence).
- **Objective:** Make Google OAuth fail-closed and production-correct.
- **Scope:** Server-side authorization-code exchange with Google only; 401 when `GOOGLE_CLIENT_ID/SECRET` unset; state parameter (CSRF) on the OAuth flow; account-linking rules (existing email → link, new email → create) documented in USER_FLOWS.md.
- **Deliverables:** Hardened `/api/auth/google/session`; OAuth integration tests with mocked Google token endpoint (test-only mocks, per ADR-021).
- **Files Expected:** `backend/server.py` (or new `backend/routers/auth_oauth.py`), `backend/tests/test_oauth.py`, `.claude/USER_FLOWS.md`.
- **Dependencies:** PH1.1.
- **Acceptance Criteria:** Invalid/absent code → 401; unset credentials → 401 with clean error; valid code path covered by tests.
- **Validation Steps:** Test suite; manual flow against a Google test client on staging.
- **Rollback Plan:** Revert PR; email/password auth unaffected. Never re-enable fallbacks.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1 day.
- **Success Metrics:** OAuth logins succeed only with verified Google identities; zero fallback sessions in logs.

## PH1.3 — Cookie & Session Security

- **Architecture reference:** SECURITY_ARCHITECTURE.md §10 (Cookie Architecture), §18 (CSRF Protection Strategy — includes the tracked, unscheduled CSRF-token-layer gap).
- **Status:** ✅ **COMPLETE (2026-07-18).** Cookie policy centralized in `backend/security/cookies.py`; all auth cookies (`access_token`, `refresh_token`, `g_oauth_state`) carry `HttpOnly; SameSite` always and `Secure` forced in production; logout clears every cookie with matching attributes; refresh remains functional; session fixation mitigated; 24 hermetic tests in `backend/tests/test_cookie_security.py`. Risk R-04 / finding B4 closed. **Deferred to a follow-up:** CSRF **token** middleware (SameSite=Lax delivers the cookie-layer CSRF baseline now); the dedicated `backend/security/csrf.py` token layer is carried forward as the next security item. Refresh-token rotation stays in PH1.6.
- **Objective:** Auth cookies unusable over plain HTTP and resistant to CSRF.
- **Scope:** `secure=True` on all four `set_cookie` call sites (env-driven `COOKIE_SECURE`, forced true when `APP_ENV=production`); confirm `httponly` + `samesite` strategy; CSRF token middleware for state-changing cookie-authenticated routes; central cookie helper so policy lives in one place.
- **Deliverables:** Cookie helper module; CSRF protection; tests.
- **Files Expected:** `backend/security/cookies.py`, `backend/security/csrf.py`, `backend/server.py`, `backend/tests/test_cookie_security.py`.
- **Dependencies:** PH1.1.
- **Acceptance Criteria:** All auth cookies carry `Secure; HttpOnly; SameSite` in production config; state-changing requests without CSRF token → 403; frontend keeps working (axios/interceptor updated if needed).
- **Validation Steps:** Inspect Set-Cookie headers in staging over HTTPS; run frontend against hardened backend locally with `COOKIE_SECURE=false`.
- **Rollback Plan:** Env escape hatch valid only outside production; revert PR if frontend regression.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1 day.
- **Success Metrics:** Risk R-04 closed; no token ever sent over HTTP.

## PH1.4 — CORS Hardening

- **Architecture reference:** SECURITY_ARCHITECTURE.md §19 (CORS Strategy).
- **Status:** ✅ **COMPLETE (2026-07-18).** CORS policy centralized in `backend/security/cors.py`; the wildcard-with-credentials default is gone. Origins now resolve from an environment-driven, exact-match allowlist (`CORS_ALLOWED_ORIGINS`, canonical; legacy `CORS_ORIGINS`/`FRONTEND_URL` still honored). A literal `*` is stripped from every source, so a wildcard can never pair with credentials. Development falls back to `http://localhost:3000` / `http://localhost:5173`; production assumes nothing (empty allowlist → all cross-origin rejected, fail closed). Methods and request headers are restricted to what the API and frontend actually use (no `*`); no response headers are exposed. 30 hermetic tests in `backend/tests/test_cors_hardening.py`. Risk R-03 / finding B3 closed. **Security headers (HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, CSP) were de-scoped from this sprint and are carried forward** as PH1.4b below.
- **Objective:** Only trusted origins may make credentialed requests.
- **Scope:** Replace the wildcard default with an environment-driven exact-match origin allowlist; never allow `Access-Control-Allow-Origin: *` with credentials; restrict methods, request headers, and exposed response headers; centralize the policy in one module.
- **Deliverables:** `backend/security/cors.py`; `apply_cors(app)` wiring in `server.py`; `backend/tests/test_cors_hardening.py`.
- **Files Delivered:** `backend/security/cors.py`, `backend/server.py`, `backend/tests/test_cors_hardening.py`.
- **Dependencies:** PH1.1 (and coordinates with PH1.8 env validation).
- **Acceptance Criteria:** No wildcard origin remains; disallowed origin gets no CORS grant; credentials only for approved origins; local development still functions. ✅ Met.
- **Validation Steps:** curl / TestClient with foreign Origin header → no ACAO; allowed origin → reflected ACAO + `Allow-Credentials: true`.
- **Success Metrics:** Risk R-03 closed.

## PH1.4b — Security Headers (carried forward)

> **Status (2026-07-20): COMPLETE.** HTTP response security headers are
> centralized in `backend/security/headers.py` and wired via a single
> pure-ASGI `SecurityHeadersMiddleware` (`apply_security_headers(app)`),
> applied *after* CORS so even CORS preflight/rejection responses carry the
> headers. Emitted on every response: `X-Content-Type-Options: nosniff`,
> `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
> a locked-down `Permissions-Policy`, `Cross-Origin-Opener-Policy: same-origin`,
> `Cross-Origin-Resource-Policy: same-origin`, `X-XSS-Protection: 0` (legacy
> auditor neutralized), and a strict, nonce-capable `Content-Security-Policy`
> (`default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors
> 'none'` — no `unsafe-*` anywhere). `Strict-Transport-Security`
> (`max-age=63072000; includeSubDomains`) is emitted **only** over HTTPS /
> production. `Cross-Origin-Embedder-Policy: require-corp` is implemented but
> opt-in (`CROSS_ORIGIN_EMBEDDER_POLICY`) to avoid breaking same-origin HTML
> tooling. Every header is environment-overridable; the CSP supports a
> `{nonce}` placeholder resolved per request and exposed on
> `request.state.csp_nonce`. 35 hermetic tests in
> `backend/tests/test_security_headers.py`. CORP is safe alongside the
> credentialed CORS frontend because CORP only blocks *no-cors* loads.

- **Architecture reference:** SECURITY_ARCHITECTURE.md §20 (Security Headers Strategy), §27 (Security Middleware Pipeline).
- **Objective:** Browsers receive the full defensive header set.
- **Scope:** Security-header middleware: HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, and a CSP compatible with the CRA build. (Split out of PH1.4, which delivered CORS only.)
- **Deliverables:** Header middleware; tests asserting headers on responses.
- **Files Expected:** `backend/security/headers.py`, `backend/server.py`, `backend/tests/test_security_headers.py`.
- **Dependencies:** PH1.4.
- **Acceptance Criteria:** All headers present on API responses.
- **Validation Steps:** securityheaders.com scan against staging.
- **Rollback Plan:** Revert middleware PR; CSP can be report-only first if it breaks the frontend.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1 day.
- **Success Metrics:** A grade on header scan.

## PH1.5 — Password Policy, Input Validation & Email Verification

> **Status (2026-07-19): password portion COMPLETE; email portion split out to PH1.5b.**
> Delivered as "PH1.5 — Password Policy & Account Protection": centralized
> `backend/security/passwords.py` (policy + explicit-cost bcrypt + safe,
> timing-equalized verification), model-layer 422 enforcement on `UserCreate`,
> bundled common-password blocklist, sanitized validation errors, FakeDB `$inc`
> support, and 40 hermetic tests (`backend/tests/test_password_policy.py` — the
> test file named below was superseded by this per-module naming). The
> acceptance criterion "weak password → 422 with actionable message" passes;
> the frontend mirrors the 12-char minimum inline and renders the API's rule
> messages. `EmailStr`, strict-model hardening, email verification, the
> password-reset flow, and the SMTP decision (OR-6) carry forward to **PH1.5b —
> Email Validation & Verification** (tracked in TASK.md).

- **Architecture reference:** SECURITY_ARCHITECTURE.md §15 (Password Security), §16 (Email Verification), §17 (Password Reset — currently unimplemented; fold into this sprint's scope).
- **Objective:** Enforce SECURITY.md identity rules at the model layer.
- **Scope:** `EmailStr` on all email fields; password validator (≥12 chars, upper/lower/number/special); email-verification flow (token email, verified flag, resend endpoint) with SMTP provider decision (OR-6); strict Pydantic models (reject unknown fields, bounded lengths) on auth payloads. **Forward-password-reset flow** (`forgot-password`/`reset-password` endpoints) — identified during the SECURITY_ARCHITECTURE.md synchronization review as a gap with no prior owner; folded into this sprint since it shares the SMTP/token-email infrastructure being built here.
- **Deliverables:** Hardened models; verification endpoints + email templates; tests.
- **Files Expected:** `backend/models.py`, `backend/security/passwords.py`, `backend/services/email_service.py`, `backend/tests/test_registration_policy.py`.
- **Dependencies:** PH1.1; SMTP provider decision.
- **Acceptance Criteria:** Weak password → 422 with actionable message; invalid email rejected; unverified accounts restricted per USER_FLOWS.md decision; frontend registration form mirrors the policy with inline validation.
- **Validation Steps:** Unit tests; manual registration on staging including verification email receipt.
- **Rollback Plan:** Verification enforcement behind `REQUIRE_EMAIL_VERIFICATION` (default true in production); policy validation itself is not rolled back.
- **Estimated Difficulty:** Medium. **Estimated Time:** 2 days.
- **Success Metrics:** 100% of new accounts meet policy; verification completion rate measurable.

## PH1.5b — Email Verification & Account Recovery

> **Status: ✅ COMPLETE (2026-07-22).** Delivered as the **"PH1.8 — Identity
> Recovery"** sprint (the operator reused the PH1.8 label; the roadmap's separate
> PH1.8 — Secrets & Environment Hardening is unchanged and still pending). This
> closes the email-verification / password-reset content carried forward from
> PH1.5.

- **Architecture reference:** SECURITY_ARCHITECTURE.md §16 (Email Verification), §17 (Password Reset) — both rewritten from "target/does-not-exist" to "current".
- **Objective:** Make the identity lifecycle recoverable without weakening security.
- **Delivered:**
  - `backend/security/recovery.py` — the single source of truth for recovery tokens. Signed handle `<token_id>.<HMAC(secret, "prefix|purpose|user_id|token_id")>` bound to one user + one purpose, backed by an authoritative `recovery_tokens` record (`issued_at`/`expires_at`/`used_at`) enforcing expiry and **atomic single-use** (replay-safe); a fresh issue invalidates the user's prior unused token of that purpose. Secret: `RECOVERY_SECRET` else `JWT_SECRET` (domain-separated, no weak default). Lifetimes: verification 24h, reset 30 min (both env-overridable).
  - New `/api/auth` endpoints: `verify-email`, `verify-email/request`, `forgot-password`, `reset-password`, `change-password`.
  - User model: `email_verified` / `email_verified_at` / `verified_by`. New email/password accounts start unverified + emailed a link (out-of-band via `BackgroundTasks`); Google accounts verified on creation/link.
  - Enumeration-safe: forgot-password / resend return an identical generic response; rate-limited via the existing `PASSWORD` policy (5/hour). Reset **and** change revoke every session (`revoke_all_for_user`) and bump `password_changed_at` → full sign-out. Shared `_apply_password_change` primitive keeps reset and change identical on the security-critical steps.
  - `services/email_service.py` templates: `EMAIL_VERIFICATION`, `PASSWORD_RESET`, `PASSWORD_CHANGED`. `recovery_tokens` startup indexes (unique `token_id`, `(user_id,purpose)`, TTL on `expires_at`). CSRF default-exempt list extended with the three public recovery entrypoints.
- **Files touched:** `backend/security/recovery.py` (new), `backend/security/__init__.py`, `backend/security/csrf.py`, `backend/models.py`, `backend/services/email_service.py`, `backend/server.py`, `backend/tests/test_recovery.py` (new), `backend/tests/test_password_policy.py` (register-contract assertion updated for the additive `email_verified` field).
- **Tests:** 28 hermetic tests in `backend/tests/test_recovery.py` (token mint/verify/consume, single-use/replay, expiry, purpose-binding, signature tamper, reissue invalidation; verify success/expired/replay, forgot-password generic response, reset single-use/expiry/policy/session-revocation, change-password current-password/unchanged/policy/sign-out, register→login→me regression). Full hermetic security suite green.
- **Deviations from the original PH1.5b scope (recorded per the rollback/ADR discipline):**
  - **`EmailStr` tightening deferred.** Email remains `email: str`; switching to `EmailStr` is a candidate follow-up. Reason: it is orthogonal to recovery and would reject some already-registered addresses — kept out of a recovery-focused sprint to preserve backward compatibility.
  - **Login is NOT blocked on `email_verified`** (the `REQUIRE_EMAIL_VERIFICATION` gate is not enabled). Reason: hard enforcement would lock out every pre-PH1.8 account and is unsafe until a real SMTP provider is provisioned (OR-6). The flag + endpoints are all in place; enabling enforcement is a one-line future gate.
  - **SMTP provider still unprovisioned (OR-6 open).** Email send runs through the existing `email_service` which falls back to *simulated* mode when no SendGrid/SMTP is configured; the recovery flows are provider-agnostic and work the moment credentials are set.
- **Remaining follow-ups (technical debt):** `EmailStr` tightening; provision SendGrid/SMTP (OR-6); optional `REQUIRE_EMAIL_VERIFICATION` enforcement gate; frontend `verify-email` / `reset-password` pages consuming the emailed `?token=` links.

## PH1.6 — JWT Lifecycle & Refresh Rotation

- **Status:** ✅ **COMPLETE (2026-07-20).** Access token 15 min; refresh token rotation on every use with reuse detection (a replayed refresh revokes the whole family); durable server-side revocation store (MongoDB `sessions` collection); `password_changed_at` + token `ver` global kill-switches; logout revokes the current session and `POST /api/auth/logout-all` revokes every session; device/IP/timestamp capture as PH1.10 groundwork. Centralized in `backend/security/jwt.py` (pure token crypto) + `backend/security/sessions.py` (`SessionStore`). 34 hermetic tests in `backend/tests/test_jwt_sessions.py` (rotation, replay→family-revoke, expired/revoked/wrong-aud/wrong-iss/bad-sig/wrong-type/stale-ver rejection, logout, logout-all, `password_changed_at`). Risk R-06 / finding H11 closed.
- **Architecture reference:** SECURITY_ARCHITECTURE.md §9 (Session Architecture), §11 (JWT Lifecycle), §12 (Refresh Token Lifecycle), §30 (Session Refresh Sequence), §31 (Logout Sequence).
- **Objective:** Token lifetimes and rotation per SECURITY.md.
- **Scope:** Access token 15 min; refresh token with rotation on every use, reuse detection (revoke family on replay), and a durable server-side revocation store; sessions-listing groundwork (device/IP capture).
- **Deliverables:** Token service; session/revocation store; migration note for existing sessions; tests including replay attack. ✅ All delivered.
- **Deviations from the original plan (recorded per the rollback/ADR discipline):**
  - **Module shape:** the placeholder single `backend/security/tokens.py` was realized as **two cohesive modules** — `security/jwt.py` (pure, framework-agnostic token crypto) and `security/sessions.py` (the DB-backed stateful `SessionStore`). Splitting crypto from persistence keeps each single-responsibility and independently testable; matches the one-tenant-per-concern convention of the `security/` package.
  - **Revocation store backing:** **MongoDB, not Redis.** Rotation with reuse detection needs an *authoritative, durable* record; the `services.cache` (Redis/in-memory) layer is best-effort and evictable, which would silently drop reuse detection. Sessions live in Mongo beside their users and are TTL-reaped. (Rationale in SECURITY_ARCHITECTURE.md §9.)
  - **Refresh lifetime default:** shipped **7 days** (env `JWT_REFRESH_TTL_SECONDS`), aligned with the existing `refresh_token` cookie `Max-Age`; SECURITY.md's 30-day policy target is reached by config (`=2592000`) without a code change.
  - **Frontend interceptor & `test_token_rotation.py` filename:** interceptor verification is deferred to the frontend-realtime track (this sprint is backend-scoped; the existing 401→login/refresh interceptor already handles the new behavior); tests live in `test_jwt_sessions.py`.
- **Files touched:** `backend/security/jwt.py`, `backend/security/sessions.py`, `backend/security/__init__.py`, `backend/server.py`, `backend/tests/test_jwt_sessions.py`, `backend/tests/test_cookie_security.py`.
- **Dependencies:** PH1.3.
- **Acceptance Criteria:** ✅ Access token expires in 15 min; reused refresh token → 401 + family revoked; users are not visibly logged out during normal use (rotation is silent).
- **Validation Steps:** Hermetic time-independent tests (crafted expired/forged tokens instead of freezegun, which is not a project dependency); manual soak on staging for one session lifetime remains a pre-launch item.
- **Rollback Plan:** Lifetimes are env-configured; revert to previous values without code rollback if UX breaks, and record deviation as an ADR.
- **Estimated Difficulty:** High. **Estimated Time:** 2 days.
- **Success Metrics:** Risk R-06 closed; zero support reports of surprise logouts after one week on staging (to confirm on staging).

## PH1.7 — CSRF Protection & Rate Limiting — ✅ COMPLETE (2026-07-21)

- **Architecture reference:** SECURITY_ARCHITECTURE.md §18 (CSRF Protection Strategy) + §21 (Rate Limiting Strategy — fold the existing `login_attempts` lockout into the new limiter rather than running both in parallel).
- **Objective:** Production-grade CSRF protection for cookie-authenticated mutations, and centralized rate limiting / brute-force / flooding protection.
- **Scope (as delivered):** `security/csrf.py` (signed double-submit token bound to the session; `CSRFMiddleware`) + `security/rate_limit.py` (named per-endpoint policies, pluggable `RateLimitStore`, progressive lockout, platform-wide `RateLimitMiddleware`). Strict limits on `/api/auth/*`; 429 responses with `Retry-After`.
- **Deliverables:** ✅ CSRF middleware + token lifecycle; ✅ limiter middleware + policies + lockout; ✅ 44 hermetic tests.
- **Files Delivered:** `backend/security/csrf.py`, `backend/security/rate_limit.py`, `backend/server.py`, `backend/security/__init__.py`, `backend/tests/test_csrf.py`, `backend/tests/test_rate_limit.py`, `backend/tests/test_password_policy.py` (2 tests re-pointed to the new store).
- **Dependencies:** PH1.1; PH1.6 (token decode reuse). MongoDB (already in stack).
- **Acceptance Criteria:** ✅ Exceeding a tier budget → 429; ✅ 6th login attempt after 5 failures → 429 regardless of credentials; ✅ limits env-configurable (`RATE_LIMIT_<NAME>`); ✅ health endpoint exempt; ✅ CSRF: valid token accepted, missing/invalid/mismatched/wrong-session rejected (403), GET/bootstrap/Bearer exempt.
- **Validation Steps:** ✅ Automated burst/lockout tests + CSRF matrix (hermetic); full PH1 security regression green (245 tests). k6/staging run deferred to PH2 (no staging env yet).
- **Rollback Plan:** Middleware are independently revertable; per-policy env overrides tune limits without a code change; the rate-limit middleware fails **open** on storage error so it can never take the API down.
- **Estimated Difficulty:** Medium. **Actual:** ~1 sprint.
- **Success Metrics:** Risk R-05 closed; credential-stuffing/flooding blocked in tests.

**Deviations from the original plan (recorded per the roadmap rule):**
1. **CSRF folded into this sprint.** The originally-titled "Rate Limiting & Brute-Force Protection" sprint was executed as "CSRF Protection & Rate Limiting" — the previously-unowned CSRF token layer (SECURITY_ARCHITECTURE.md §18) now has a home here rather than remaining unscheduled.
2. **MongoDB store, not Redis.** Per the sprint directive ("use the current persistence approach unless there is already infrastructure for Redis"), the limiter is MongoDB-backed behind a `RateLimitStore` interface shaped exactly for a drop-in Redis (`INCR`/`EXPIRE`) implementation later — no caller changes required.
3. **Tiers.** Delivered the authenticated (120/min per user) vs public (60/min per IP) split plus the abuse-critical per-endpoint limits; the full role-tiered Guest/Free/Pro/Elite quotas from SECURITY.md remain future work.
4. **No single `RATE_LIMIT_ENABLED` flag.** Superseded by per-policy env overrides + fail-open-on-storage-error, which together give finer, safer operational control than a blunt global kill-switch.
5. **`test_rate_limiting.py` → `test_rate_limit.py`** to match the `test_<module>.py` convention (module is `rate_limit.py`).
6. **No frontend change.** Because CSRF exempts Bearer requests (the SPA's auth path), the friendly-retry messaging / frontend wiring originally imagined was unnecessary for a non-breaking rollout; it becomes relevant only if/when a cookie-only client is introduced.

## PH1.8 — Secrets & Environment Hardening

> **STATUS: ✅ COMPLETE (2026-07-22)** — delivered as the **"PH1.9 — Secrets &
> Supply Chain Security"** sprint (numbering shifted because Identity Recovery
> consumed the PH1.8 slot), combined with the supply-chain/dependency-auditing
> portion of PH1.11. Actuals vs. the plan below:
> - Validator lives in **`backend/security/secrets.py`** (not `backend/config.py`)
>   — follows the established one-module-per-sprint security-package convention;
>   the `SECRET_REGISTRY` is the typed registry the plan called for.
> - Boot-time `validate_config()` wired into `server.py` before the Mongo client:
>   fails closed, aggregates all problems, `JWT_SECRET` ≥ 32 + placeholder
>   rejection, cross-field prod invariants (AI provider present, OAuth/broker
>   both-or-neither, `ENABLE_AUTO_LOGIN` off, weak `ADMIN_PASSWORD` rejected).
> - Removed weak compose defaults (`change_this_in_production_min_32_chars` →
>   required `JWT_SECRET`; hard-coded n8n `alphapartner123` → required
>   `N8N_BASIC_AUTH_PASSWORD`).
> - `backend/.env.example` + `frontend/.env.example` (generated from the registry;
>   `.gitignore` updated to permit committed examples). Rotation runbook is a
>   dedicated **`.claude/SECRETS.md`** (not DEPLOYMENT.md).
> - Supply chain (folds in PH1.11's core): full exact-pinning of
>   `requirements.txt`, 7 in-pin CVE patches, and a `security-audit` CI workflow
>   (pip-audit + pip check + npm audit + gitleaks, push + weekly).
> - 38 hermetic tests in `backend/tests/test_secrets.py`. gitleaks-style history
>   check: no real provider secret in history; the one committed value
>   (`alphapartner123`) externalized — see SECRETS.md §9.

- **Architecture reference:** SECURITY_ARCHITECTURE.md §23 (Secret Management), §24 (Environment Security).
- **Objective:** No weak defaults anywhere; misconfigured production refuses to boot.
- **Scope:** Boot-time config validator (typed registry of every env var: required-in-prod flag, format checks — `JWT_SECRET` ≥ 32 chars and not the known placeholder, Mongo/Redis URLs, CORS origins); remove `change_this_in_production_min_32_chars` and hardcoded n8n password from compose defaults; `.env.example` for backend and frontend; secret-rotation runbook.
- **Deliverables:** `backend/config.py` validator; cleaned compose; `.env.example` files; runbook section in DEPLOYMENT.md.
- **Files Expected:** `backend/config.py`, `docker-compose.yml`, `backend/.env.example`, `frontend/.env.example`, `.claude/DEPLOYMENT.md`.
- **Dependencies:** PH1.4 (CORS validation folds in); coordinates with PH2.3.
- **Acceptance Criteria:** `APP_ENV=production` with any missing/weak required var → process exits non-zero with a named-variable error; no secret literals in repo (gitleaks scan clean).
- **Validation Steps:** Boot matrix test (each var removed in turn); gitleaks/trufflehog run.
- **Rollback Plan:** Validator failures list all problems at once (not first-failure) so ops can fix in one pass; revert PR only for validator bugs.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1 day.
- **Success Metrics:** Zero weak defaults in repo; misconfiguration caught at boot, never at request time.

## PH1.9 — Real-Time & WebSocket Security

- **Architecture reference:** SECURITY_ARCHITECTURE.md §4 (Trust Boundaries — extend the diagram with a Socket.IO lane), §32 (Future Production Hardening Plan — this sprint adds a new Real-Time Authorization Architecture section).
- **Objective:** Socket.IO surface as protected as REST.
- **Scope:** Authenticated connection handshake (cookie/token validation before join); per-room authorization (a user may only join their own portfolio/trade rooms; admin rooms require admin); per-connection message rate limits; idle disconnect; subscription validation against watchlist entitlements per REALTIME_SYSTEM.md.
- **Deliverables:** Socket auth middleware; room authorization map; tests with socket.io test client.
- **Files Expected:** `backend/realtime/socket_auth.py` (or equivalent module), `backend/server.py`, `backend/tests/test_socket_security.py`.
- **Dependencies:** PH1.6 (token validation reuse).
- **Acceptance Criteria:** Unauthenticated connect → rejected; cross-user room join → rejected + logged; message flood → throttled/disconnected.
- **Validation Steps:** Automated socket tests; manual multi-user session on staging verifying isolation.
- **Rollback Plan:** Revert PR; REST remains protected independently.
- **Estimated Difficulty:** High. **Estimated Time:** 2 days.
- **Success Metrics:** Risk R-15 closed; zero cross-user event leakage in tests.

## PH1.10 — Audit Logging & Security Monitoring

- **Status:** ✅ **COMPLETE (2026-07-22).** Centralized security-event logging in `backend/security/audit.py`: a closed event taxonomy across five categories (authentication / identity / session / security / administration) mapping every event to a `category` + default `severity` (info/notice/warning/critical, unknown → security/warning fail-safe); a versioned structured schema (`schema_version=1`: event, category, severity, outcome, email, user_id, session_id, reason, ip, user_agent, request_id, target, redacted details, timestamp); recursive secret redaction (a token/password/code/state/hash can never reach a sink); a pluggable `AuditSink` interface with a default composite of durable `MongoAuditSink` (`security_audit_logs`) + SIEM-ready `LoggingAuditSink`; and a fail-safe `AuditLogger` (emitting can never break a security flow). The prior `log_auth_event` is now a thin backward-compatible facade over it. Instrumented the auth surface (login ±, registration, session created/revoked, logout/logout-all, refresh rotation, token-replay vs. invalid-refresh, invalid-JWT), the CSRF middleware (`csrf_validation_failure`), and the rate limiter (`rate_limit_triggered` at the single `_trip` choke point). Centralized in `backend/security/audit.py`; 20 hermetic tests in `backend/tests/test_audit.py` (taxonomy, schema, redaction, sinks, fail-safe, live-app integration, backward compatibility). Documented in SECURITY_ARCHITECTURE.md §31b. This sprint took the PH1.10 slot; Admin Hardening & Session Management moves to **PH1.10b** below (as the Secrets sprint shifted PH1.8→PH1.9).
- **Delivered under the PH1.10 label per the sprint brief** (Audit Logging & Security Monitoring). Objective, scope, and acceptance below are recorded as completed.
- **Objective:** Centralized audit log; all security-sensitive events captured; foundation for future SIEM integration.
- **Acceptance Criteria (met):** Login success/failure logged; password reset logged; session revocation logged; rate limit logged; replay detection logged; invalid JWT logged; sensitive values never logged (asserted); regression tests pass.

## PH1.10b — Admin Hardening & Session Management

- **Architecture reference:** SECURITY_ARCHITECTURE.md §9 (Session Architecture — session listing/revocation), §14 (Future MFA Architecture — ADR-028), §8 (Permission System — a fine-grained permission system is a separate, unscheduled future item; do not conflate it with this sprint's admin-policy scope).
- **Objective:** Admin surface meets SECURITY.md admin requirements; users get session visibility.
- **Scope:** Active-sessions API (list/revoke one/revoke all) with device/IP/last-activity from PH1.6 groundwork; admin session shorter lifetime; admin action re-auth for destructive operations; MFA **design** (TOTP) recorded as an ADR with implementation scheduled pre-Closed-Beta (OR-4); audit-log review pass ensuring all admin mutations are logged.
- **Deliverables:** Sessions endpoints + minimal settings UI wiring; admin policy enforcement; MFA design ADR.
- **Files Expected:** `backend/server.py` (or `backend/routers/sessions.py`), `frontend/src/pages/Settings*` (sessions panel), `.claude/DECISIONS.md` (ADR-028 MFA design).
- **Dependencies:** PH1.6.
- **Acceptance Criteria:** User can see and revoke sessions; revoked session's refresh fails; every admin mutation appears in audit log (spot-check matrix).
- **Validation Steps:** Multi-device manual test; audit-log diff review.
- **Rollback Plan:** Sessions UI is additive; revert independently of backend policy.
- **Estimated Difficulty:** Medium. **Estimated Time:** 2 days.
- **Success Metrics:** 100% admin mutations audited; session revocation < 30 s to take effect.

## PH1.11 — Dependency & Vulnerability Scanning

> **STATUS: ✅ COMPLETE (2026-07-22)** — the core supply-chain deliverables landed
> in the PH1.9 sprint: `pip-audit` + `pip check` + `npm audit` + `gitleaks` in a
> `security-audit` GitHub Actions workflow (push + weekly), full exact-pinning of
> `backend/requirements.txt`, 7 in-pin CVE patches, and
> `scripts/audit_dependencies.py` for local runs. **Finished in PH1.12/F-3:**
> `.github/dependabot.yml` (weekly PRs for pip `/backend`, npm `/frontend`,
> github-actions; docker staged for PH2.1/2.2), the `requirements.txt` →
> `requirements-dev.txt` split (finding M14 — dev tools verified dev-only via
> `pip show … Required-by`), the triage-SLA policy (critical blocks release · high
> 7d · medium 30d · low 90d) in SECRETS.md §7 + TESTING.md, and a CI change to
> audit BOTH requirements files and run `pip check` on the runtime-only install.
> Deferred CVEs (starlette/litellm/ecdsa) remain tracked in SECRETS.md §8.
> Risk R-14 closed.

- **Architecture reference:** SECURITY_ARCHITECTURE.md §25 (Dependency Security).
- **Objective:** Supply chain continuously scanned.
- **Scope:** `pip-audit` (backend) and `npm audit --omit=dev` (frontend) wired into CI (lands with PH2.6 if CI not yet ready — script-first so it runs locally); Dependabot config for pip, npm, docker, github-actions ecosystems; triage policy (critical = block merge, high = 7-day SLA) documented; move `black`/`flake8` from `requirements.txt` to `requirements-dev.txt` (finding M14).
- **Deliverables:** Scan scripts; `.github/dependabot.yml`; split requirements files; triage policy in TESTING.md.
- **Files Expected:** `scripts/security_scan.sh`, `.github/dependabot.yml`, `backend/requirements.txt`, `backend/requirements-dev.txt`, `.claude/TESTING.md`.
- **Dependencies:** None hard; CI integration depends on PH2.5.
- **Acceptance Criteria:** Scans run clean or with documented accepted findings; runtime image contains no dev tooling.
- **Validation Steps:** Run scripts locally; verify Dependabot opens PRs on the repo.
- **Rollback Plan:** Scans are advisory until PH2.6 makes them gates; trivially revertible.
- **Estimated Difficulty:** Low. **Estimated Time:** 0.5 day.
- **Success Metrics:** Risk R-14 closed; zero unpatched criticals at any time.

## PH1.12 — Security Certification

> **STATUS: ✅ COMPLETE (2026-07-22)** — Phase 1 exit gate passed. Implemented the
> three PH1.11 verification residuals: **F-1** privilege escalation
> (`backend/security/roles.py` — role allowlist + least-privilege
> `validate_role_assignment`, wired into `admin_update_user`); **F-2** unhandled
> ObjectId parsing (`backend/security/identifiers.py` — `parse_object_id` returns
> a clean 400 at every untrusted id boundary); **F-3** supply-chain automation
> (see PH1.11). 48 new hermetic tests; security checklist executed (no debug/
> backdoors/hardcoded secrets; cookies/CORS/headers/CSRF/rate-limit/audit/config-
> validation confirmed). **Re-score: Authentication & Authorization 9.0, API &
> Transport Security 8.5** (both ≥ 8.0 gate). Report: `docs/security/PH1_CERTIFICATION.md`;
> sign-off in PRODUCTION_HARDENING.md §17. **Decision: PH1 security CERTIFIED;
> overall production deployment NO-GO pending PH2 + PH3.**

- **Architecture reference:** SECURITY_ARCHITECTURE.md §34 (Testing Strategy), and the document as a whole — it is the primary evidence artifact the pen-test checklist is executed against (§32).
- **Objective:** Independent verification that PH1 achieved its goal; formal sign-off.
- **Scope:** Execute the SECURITY.md penetration checklist against staging (authn, authz, rate limiting, XSS, CSRF, injection, session management, broker flow, WebSocket); OWASP Top 10 review; grep-evidence pack for removed backdoors; re-score security categories; write the Security Certification Report.
- **Deliverables:** `docs/security/PH1_CERTIFICATION.md` report; fixed findings or ADR-recorded acceptances; sign-off entry in PRODUCTION_HARDENING.md §17.
- **Files Expected:** `docs/security/PH1_CERTIFICATION.md`, `.claude/PRODUCTION_HARDENING.md`, `.claude/CHANGELOG.md`.
- **Dependencies:** PH1.1–PH1.11 complete; staging environment (PH2.12 or interim compose stack).
- **Acceptance Criteria:** No critical/high findings open; authn ≥ 8.0, API security ≥ 8.0 on re-score.
- **Validation Steps:** Checklist execution log attached to the report.
- **Rollback Plan:** N/A (assessment sprint). Failing items reopen their source sprint.
- **Estimated Difficulty:** Medium. **Estimated Time:** 2 days.
- **Success Metrics:** Security sign-off recorded; PH1 exit gate passed.

---

# PH2 — Production Infrastructure & DevOps

Goal: the platform can be built, deployed, observed, and recovered mechanically. Closes findings B5, B6, R-07, R-10, R-11.

---

## PH2.1 — Backend Production Dockerfile ✅ COMPLETE (2026-07-22)

- **Objective:** Reproducible backend image.
- **Scope:** Multi-stage `backend/Dockerfile`: builder (deps compile) → slim runtime; non-root user; `requirements.txt` only (dev deps excluded per PH1.11); uvicorn with explicit `--workers`, **no `--reload`**, no bind mounts; HEALTHCHECK instruction; `.dockerignore`.
- **Deliverables:** `backend/Dockerfile`, `backend/.dockerignore`; image builds and serves.
- **Files Expected:** `backend/Dockerfile`, `backend/.dockerignore`.
- **Dependencies:** PH1.8 (config validator makes the image safe to boot).
- **Acceptance Criteria:** `docker build` succeeds; container runs as non-root; image passes health check with valid env; image size within reason (< 400 MB target).
- **Validation Steps:** Build + run against local Mongo/Redis; hit `/api/monitor/health`; `docker inspect` user check.
- **Rollback Plan:** Additive file; nothing depends on it until PH2.3.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1 day.
- **Success Metrics:** Deterministic image build from clean checkout.
- **Delivered (2026-07-22):** `backend/Dockerfile` (two-stage, builder toolchain discarded), `backend/.dockerignore`, `backend/docker/entrypoint.sh`, `backend/docker/healthcheck.sh`, `production.env.example`, `docs/deployment/DOCKER.md`. Scope was extended beyond the plan with an **entrypoint** (startup validation delegated to `security/secrets.py`, a `pre-start.d/` hook directory for future migrations, `exec` signal handoff) and a **stdlib-only health probe** (so `curl` never enters the runtime image).
  - **Met:** build succeeds; runs as uid 10001 non-root and cannot write its own source; health check passes against live Mongo; no dev deps, no `.env`, no `pip`/`curl`/`wget`/`gcc` in the image; graceful SIGTERM → exit 0 in 1.2 s; healthy under `--read-only --cap-drop=ALL --security-opt no-new-privileges`.
  - **Missed:** image is **1.03 GB** vs the < 400 MB target. Every image-level lever was applied (multi-stage −300 MB, bundled test suites −66 MB, pip −16 MB; `strip` measured at 0 MB and `--no-compile` rejected on startup-cost grounds). The residual is the dependency set: `googleapiclient` (97 MB), `litellm` (55 MB), `boto3`/`botocore` (32 MB), `stripe` (24 MB), `s5cmd` (15 MB) are **not imported by any application code** — ≈220 MB. Closing the gap requires a `requirements.txt` prune, not a Dockerfile change. **Recommended follow-up sprint.** — **CLOSED (2026-07-24) by the PH2.8 "Production Configuration & Environment Optimization" sprint:** `requirements.txt` pruned 118 → 58 packages, a measured 377 MB (−66%) off the dependency footprint, projected image ~650 MB. See CHANGELOG.md and docs/infrastructure/CONFIGURATION.md §7–§8.
  - **Deviation:** validated against `/api` (public, rate-limit-exempt, dependency-free) rather than the planned `/api/monitor/health`, which requires authentication and hits Mongo — unsuitable for a liveness probe. Rationale in `docs/deployment/DOCKER.md` §8.
  - **Defect surfaced (not fixed — out of sprint scope):** `pytz` is imported by `services/market_engine/validator.py` but pinned in neither requirements file, so the Market Engine fails to initialize. Pre-existing; equally broken outside Docker. — **FIXED (2026-07-24) in the PH2.8 config sprint:** `pytz==2025.2` pinned in `requirements.txt`.
  - **Constraint:** `WEB_CONCURRENCY` must stay at 1 until PH2.8 (in-process scheduler + in-memory WebSocket registry are not multi-process safe). Enforced by a startup warning and documented.

## PH2.2 — Frontend Production Dockerfile

- **Objective:** Reproducible static frontend image.
- **Scope:** Multi-stage `frontend/Dockerfile`: node build (`npm ci && npm run build`) → nginx runtime; nginx config with gzip, long-cache hashed assets, no-cache `index.html`, SPA fallback, security headers mirroring PH1.4; build-arg for API URL.
- **Deliverables:** `frontend/Dockerfile`, `frontend/nginx.conf`, `frontend/.dockerignore`.
- **Files Expected:** as above.
- **Dependencies:** None (parallel with PH2.1).
- **Acceptance Criteria:** Image serves the app; deep-link routes resolve (SPA fallback); Lighthouse best-practices pass on served build.
- **Validation Steps:** Build, run, click through core pages against staging API.
- **Rollback Plan:** Additive; Vercel-style static hosting remains an alternative until PH2.12.
- **Estimated Difficulty:** Low. **Estimated Time:** 0.5 day.
- **Success Metrics:** Production-equivalent frontend runnable anywhere Docker runs.

## PH2.3 — Compose Split: Development vs Production

- **Objective:** One honest dev stack, one hardened prod stack.
- **Scope:** Rewrite `docker-compose.yml` (dev: bind mounts, reload, Mailhog, seeded Mongo) and add `docker-compose.prod.yml` (built images, env-file injection, **no default secrets** — compose fails if unset, per PH1.8; restart policies; resource limits; Mongo/Redis healthchecks; internal network isolation).
- **Deliverables:** Both compose files; `Makefile` or scripts for `dev up` / `prod up`.
- **Files Expected:** `docker-compose.yml`, `docker-compose.prod.yml`, `Makefile`.
- **Dependencies:** PH2.1, PH2.2, PH1.8.
- **Acceptance Criteria:** `docker compose -f docker-compose.prod.yml --env-file .env.production up` boots the full stack green from clean images; dev compose preserves hot-reload workflow.
- **Validation Steps:** Clean-machine boot test of both stacks; verify prod stack refuses to start without secrets.
- **Rollback Plan:** Keep prior compose in git history; dev workflow verified before merge.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1 day.
- **Success Metrics:** Finding B5 closed; clean-machine boot < 10 min.

## PH2.4 — Environment & Configuration Framework

- **Objective:** Three-environment strategy (dev/staging/prod) real and documented.
- **Scope:** Environment matrix (every var × every env) documented in DEPLOYMENT.md; `APP_ENV` respected consistently across backend, compose, and frontend build; staging env definition; config drift check script comparing `.env.example` against validator registry.
- **Deliverables:** Env matrix docs; drift-check script; staging env files (templates).
- **Files Expected:** `.claude/DEPLOYMENT.md`, `scripts/check_env_drift.py`, `.env.staging.example`.
- **Dependencies:** PH1.8, PH2.3.
- **Acceptance Criteria:** Every env var in code appears in the registry, the example files, and the docs — drift check enforces this; staging boots from its template.
- **Validation Steps:** Run drift check (add it to CI in PH2.6); boot staging profile.
- **Rollback Plan:** Documentation/script sprint — trivially revertible.
- **Estimated Difficulty:** Low. **Estimated Time:** 0.5 day.
- **Success Metrics:** Zero undocumented env vars; onboarding an env requires no code reading.

## PH2.5 — CI Pipeline Foundation

- **Objective:** Every PR mechanically verified.
- **Scope:** `.github/workflows/ci.yml`: backend job (`pip install`, lint via ruff/flake8, `pytest -m "not integration"` with Mongo/Redis service containers) + frontend job (`npm ci`, lint, `npm run build`); branch protection on `main` requiring green CI; PR template with the PRODUCTION_HARDENING.md §15 verification checklist.
- **Deliverables:** CI workflow; branch protection; PR template.
- **Files Expected:** `.github/workflows/ci.yml`, `.github/pull_request_template.md`.
- **Dependencies:** PH3.1 (hermetic suite) — coordinate: CI lands with integration tests excluded, PH3.1 makes the default suite green.
- **Acceptance Criteria:** Red tests block merge; pipeline < 10 min; runs on every PR and push to main.
- **Validation Steps:** Open a deliberately failing PR; verify block; verify green PR merges.
- **Rollback Plan:** Disable required check temporarily via repo settings if the pipeline itself breaks; never merge around a legitimately red build.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1 day.
- **Success Metrics:** Finding B6 half-closed; 100% of merges pipeline-verified from this sprint forward.

## PH2.6 — CI Extended: Docker, Security & Integration Stages

- **Objective:** Pipeline covers packaging, supply chain, and cross-service behavior.
- **Scope:** Docker build jobs for both images (cache-optimized); `pip-audit`/`npm audit` gates (PH1.11 policy); gitleaks secret scan; integration job that boots the prod compose stack and runs `pytest -m integration`; env drift check (PH2.4); frontend test job placeholder (activated by PH3.3).
- **Deliverables:** Extended workflow(s); documented triage policy for gate failures.
- **Files Expected:** `.github/workflows/ci.yml`, `.github/workflows/integration.yml`.
- **Dependencies:** PH2.1–2.3, PH2.5, PH1.11, PH3.1.
- **Acceptance Criteria:** All stages green on main; integration suite runs against real containers; critical vulnerability fails the build.
- **Validation Steps:** Seed a known-vulnerable dep on a branch → verify gate; run integration job.
- **Rollback Plan:** Individual jobs can be set non-blocking while repaired; document any such demotion in the PR.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1.5 days.
- **Success Metrics:** Finding B6 fully closed; escaped-defect classes (packaging, vuln, integration) each gated.

## PH2.7 — CD & Release Automation

- **Objective:** Tag-to-production is mechanical and reversible.
- **Scope:** Release workflow on semver tags: build → push images to registry (GHCR) → deploy staging automatically → manual approval gate → deploy production → post-deploy health verification → auto-rollback to previous tag on failed health; release-notes generation from CHANGELOG.md.
- **Deliverables:** `release.yml` workflow; deploy scripts for the chosen host (Railway/VM per DEPLOYMENT.md decision); rollback script.
- **Files Expected:** `.github/workflows/release.yml`, `scripts/deploy.sh`, `scripts/rollback.sh`.
- **Dependencies:** PH2.6; hosting decision (record as ADR if it deviates from ADR-012).
- **Acceptance Criteria:** One tag produces a verified staging deploy; production requires approval; rollback restores previous version in < 10 min (rehearsed).
- **Validation Steps:** Full dry-run: tag → staging → approve → prod (staging-as-prod rehearsal) → forced-failure rollback drill.
- **Rollback Plan:** Manual deploy path documented as fallback until two successful automated releases.
- **Estimated Difficulty:** High. **Estimated Time:** 2 days.
- **Success Metrics:** Deploy lead time < 30 min; rollback rehearsed and timed.

## PH2.8 — Database & Redis Production Configuration

> **Numbering note (2026-07-24):** the executed sprint labelled **PH2.8** was
> *"Production Configuration & Environment Optimization"* (config-source
> consolidation, environment profiles, the 118 → 58 dependency prune, image
> optimization, the `pytz` fix, and `docs/infrastructure/CONFIGURATION.md`) — the
> same as-commissioned-vs-roadmap drift recorded for PH2.2–PH2.7 in TASK.md. That
> work is **COMPLETE**; see CHANGELOG.md and TASK.md → PH2.8. The data-tier scope
> below is **displaced to PH2.8b** and remains outstanding. (Redis persistence was
> since decided under PH2.7: AOF-only for warm restart — see docs/infrastructure/REDIS.md.)

- **Objective:** Data tier secured and performant per SECURITY.md/DATABASE.md.
- **Scope:** Mongo: authenticated least-privilege app user, TLS connection string, index audit against DATABASE.md (create missing, document all), Atlas backup policy (or self-hosted mongodump schedule); Redis: `requirepass`, no public bind, `maxmemory` + eviction policy, persistence decision (RDB/AOF/none) recorded; connection-pool sizing documented.
- **Deliverables:** Hardened connection configs; index migration script; data-tier section in DEPLOYMENT.md.
- **Files Expected:** `backend/database.py` (or equivalent), `scripts/ensure_indexes.py`, `docker-compose.prod.yml`, `.claude/DEPLOYMENT.md`, `.claude/DATABASE.md`.
- **Dependencies:** PH2.3.
- **Acceptance Criteria:** Anonymous Mongo/Redis access impossible in prod stack; all documented indexes exist (script idempotent); slow-query threshold logged.
- **Validation Steps:** Connection attempt without creds fails; `ensure_indexes` run + explain-plan spot checks on hot queries.
- **Rollback Plan:** Index creation is online/idempotent; auth changes staged through staging first.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1.5 days.
- **Success Metrics:** Data-tier items of SECURITY.md checklist pass.

## PH2.9 — Structured Logging

- **Objective:** Every significant event observable, nothing sensitive leaked.
- **Scope:** JSON structured logging (level, ts, logger, request-id, user-id) via a logging module replacing ad-hoc prints; request-id middleware propagated to responses; redaction filter (tokens/passwords/keys); log level by env; auth/payment/trade/broker/admin events logged per SECURITY.md; deprecated `@app.on_event` → lifespan handler (finding M15) folded in here.
- **Deliverables:** Logging module; middleware; redaction tests; lifespan migration.
- **Files Expected:** `backend/observability/logging.py`, `backend/server.py`, `backend/tests/test_log_redaction.py`.
- **Dependencies:** PH2.3 (log driver config in compose).
- **Acceptance Criteria:** Logs parse as JSON; grep for known token pattern in logs under test → zero hits; request-id joins a request across log lines.
- **Validation Steps:** Redaction unit tests; manual log inspection on staging under load.
- **Rollback Plan:** Logging format switchable by env var; revert PR safe.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1.5 days.
- **Success Metrics:** MTTD for injected test error < 5 min using logs alone.
- **Delivered (2026-07-22) under the PH2.5 sprint label** — see TASK.md. `backend/observability/logging.py` + `context.py`; JSON records to stdout with level/ts/logger/request-id/service/environment/version; `ObservabilityMiddleware` generates or validates `X-Request-ID` and returns it on every response; redaction reuses `security.audit`'s marker list plus a free-text message scrubber; `LOG_LEVEL`/`LOG_FORMAT` by environment. Tests are in `backend/tests/test_observability.py` rather than the expected `test_log_redaction.py`, alongside the rest of the logging coverage.
  - **Not delivered from this scope:** the `@app.on_event` → lifespan migration (finding M15) was left in place. It is an unrelated refactor of the application's startup/shutdown contract, and doing it inside an observability sprint would have put a behavioural change to broker-session restore and scheduler teardown in a PR about telemetry. The lifecycle hooks PH2.5 added (`mark_started`/`mark_stopping`) sit inside the existing handlers and move unchanged when M15 is done.
  - **Extended (2026-07-22) under the "PH2.6 Production Logging Infrastructure" sprint label** — the *log management* half this scope never named, and the piece PH2.3's "log driver config in compose" dependency was pointing at. Structured application logging was NOT redesigned. Added: five-way stream separation by logger name (application / access / security / audit / error) so retention can differ per stream; size-triggered rotation to timestamped, gzipped segments; retention by both age and count, age applied first; every file handler behind a bounded `QueueListener` so disk I/O, rotation and compression never touch the event loop; `/var/log/stockassist` pre-created in the image owned by uid 10001 with a `backend_logs` named volume; Docker `json-file` bounded at 10 MB × 3 with `mode: non-blocking`, and the documented driver matrix for Loki/ELK/CloudWatch/Datadog/Splunk. File sinks are opt-in (`LOG_TO_FILES`) and never replace stdout. Redaction was verified rather than rebuilt — the acceptance criterion "grep for a known token pattern → zero hits" is now enforced on the FILE sink, where logs persist. Documentation: `docs/operations/LOGGING.md`. Tests: `backend/tests/test_log_infrastructure.py` (61). **One real defect found and fixed:** the request ID was silently `"-"` in every file record — the formatter read a `contextvars` value at format time, and file records are formatted on the listener thread, whose context is empty. The context is now snapshotted onto the record at enqueue time, on the calling thread. Log **shipping** remains PH2.10.

## PH2.10 — Monitoring, Metrics & Alerting

- **Objective:** Outages announce themselves.
- **Scope:** Split health endpoints `/health` (live), `/ready` (Mongo+Redis+provider checks), keeping `/api/monitor/health` as alias; error tracking (Sentry SDK or GlitchTip) for backend + frontend; metrics endpoint (request latency, error rate, Socket.IO connections, Redis pub/sub lag, market-event throughput, cron success); minimum alert set (health fail, error spike, auth-failure spike, backup failure) to a monitored channel; uptime check on public URL.
- **Deliverables:** Health endpoints; error tracking wired; metrics; alert rules; monitoring section in DEPLOYMENT.md.
- **Files Expected:** `backend/observability/metrics.py`, `backend/server.py`, `frontend/src/index.js` (error boundary reporting), `.claude/DEPLOYMENT.md`.
- **Dependencies:** PH2.9.
- **Acceptance Criteria:** Killing Redis flips `/ready` and fires an alert within 2 min; unhandled backend exception appears in error tracker with request context.
- **Validation Steps:** Chaos drill on staging (kill Redis, kill Mongo, throw test exception); confirm alert receipt.
- **Rollback Plan:** All additive; SDK removable by env flag.
- **Estimated Difficulty:** Medium. **Estimated Time:** 2 days.
- **Success Metrics:** Risk R-10 closed; alert-to-human latency < 5 min in drill.
- **Partially delivered (2026-07-22) under the PH2.5 sprint label** — the *observable* half. Health endpoints split three ways rather than two: `/api/health/live`, `/api/health/ready` (Mongo critical, Redis non-critical) and `/api/health/startup`; `/api/monitor/health` is untouched and is **not** an alias — it is an authenticated AI portfolio analysis that merely shares the word "health". `backend/observability/metrics.py` exposes request latency/error rate/traffic/in-flight, dependency health and uptime at `/api/metrics` in Prometheus exposition format, token-gated in production. Monitoring documentation went to `docs/operations/MONITORING.md` (the operations doc set) rather than `.claude/DEPLOYMENT.md`.
  - **Largely completed (2026-08-15) under the sprint label "PH3.7 — Monitoring & Observability"** — the *instrumentation* half, plus scope this item never named. Report: **`docs/architecture/OBSERVABILITY.md`**; `docs/operations/MONITORING.md` raised to v1.2. **Numbering note:** the brief called this PH3.7; this roadmap's PH3.7 is *Performance Benchmarking & Load Testing* (complete, separate item). **The sprint's finding was that PH2.5 had left instrumentation at the process boundary.** Every dependency the application talks to was uninstrumented: MongoDB had one bit (`dependency_up`) and no latency, failures or pool visibility; WebSockets had PH3.6's gauges but no *flow*, so 200 stable connections and a churn of 200 reconnects were indistinguishable; background tasks, market-data providers, broker APIs, news, AI providers and the event bus had nothing at all — so "which subsystem is failing?" was unanswerable. **Delivered:** a closed 13-class error taxonomy (`observability/errors.py`, matched by MRO name rather than `isinstance` so the module every subsystem depends on imports no client library); the keystone `subsystem_errors_total{subsystem,error_class}`; **driver-level MongoDB instrumentation** (`observability/mongo_monitor.py` — command latency/outcome and pool occupancy via pymongo's own listeners, so every one of several hundred call sites is covered by construction and reads only the command *name*, never the BSON document that carries emails, password hashes and broker tokens); WebSocket connection/disconnect-by-reason/fan-out/send-failure counters; background-task start/termination/lifetime; provider and AI request/latency/error families; event-bus throughput and handler failures; `auth_events_total` fed from `security.audit`'s single choke point; a **critical `configuration` readiness check**; and the frontend's first error boundary, global handlers and client-error ingest. **Closed-vocabulary label validation** (`instruments._bounded`) is now the fourth cardinality defence alongside route templates, the `<unmatched>` bucket and `METRICS_MAX_SERIES`. **This item's remaining scope is unchanged and is the load-bearing part:** Prometheus server + scrape config, Grafana dashboards, error tracking (Sentry/GlitchTip), **an alert delivery channel**, uptime check on the public URL, and cross-worker metric aggregation. The alert *set* is no longer a draft — OBSERVABILITY.md §9 specifies 6 critical and 22 warning conditions with thresholds, severities, expected responses and false-positive analysis — but **nothing evaluates or delivers them**, so detection remains manual and dominates RTO, the acceptance criterion "killing Redis flips `/ready` and fires an alert within 2 min" is still only half-executable (the flip is verified; the alert cannot fire), and **every threshold is an engineering estimate pending a staging baseline (PH2.12)**. Of the metrics this item listed by name: Socket.IO fan-out ✅, market-event throughput ✅ (`event_bus_events_total`), Redis pub-sub lag ❌ (reconnects and dispositions are counted; lag itself is not), cron success ✅ (`scheduler_job_runs_total{job,outcome}` + `scheduler_job_duration_seconds`, from an APScheduler event listener — and it captures `missed`, the failure only cron has, where the run is skipped past its misfire grace period so nothing inside the job body can ever report it).
  - **Dependencies:** PH2.9 (met).

## PH2.11 — Backup & Disaster Recovery

- **Objective:** Data loss bounded and recovery proven.
- **Scope:** Automated Mongo backups (daily/weekly/monthly per SECURITY.md), encrypted, stored off-host; restore runbook; **executed restore drill** with timing; RPO/RTO recorded in PRODUCTION_HARDENING.md §11 verified against reality; incident-response postmortem template; Redis persistence per PH2.8 decision.
- **Deliverables:** Backup automation; restore runbook + drill record; postmortem template.
- **Files Expected:** `scripts/backup_mongo.sh` (or Atlas policy doc), `docs/runbooks/RESTORE.md`, `docs/runbooks/POSTMORTEM_TEMPLATE.md`, `.claude/DEPLOYMENT.md`.
- **Dependencies:** PH2.8.
- **Acceptance Criteria:** Backup produced on schedule and encrypted; drill restores a full environment to working state within RTO; drill documented with timestamps.
- **Validation Steps:** The drill itself is the validation.
- **Rollback Plan:** N/A (additive capability).
- **Estimated Difficulty:** Medium. **Estimated Time:** 1.5 days.
- **Success Metrics:** Risk R-11 closed; restore drill ≤ 4 h RTO.
- **Status (2026-08-04): PARTIAL — the backup/restore half is delivered by the PH2.9 sprint** (sprint-track numbering; see `.claude/TASK.md`). Delivered: `scripts/backup/` (six files), encrypted AES-256 streamed `mongodump --archive --gzip`, grandfather-father-son retention (7/4/6), three verification levels including an executed restore drill, a restore path with verify-before-write and verify-after-write, an encrypted secret-material archive, an upload-storage path, and `docs/operations/BACKUP_AND_RESTORE.md`. **Drill executed and timed** — 205 000 docs / 26.3 MB: backup 2.06 s, restore 3.51 s, 13.2:1 compression, indexes and document contents verified identical; the real `alpha_stock_db` drilled 21/21 collections. RPO ≤ 24 h and RTO ≤ 4 h in PRODUCTION_HARDENING.md §11 are now measurement-backed for the database tier. Redis persistence decision recorded (not backed up — reconstructible cache; AOF is a warm-start optimisation). **Outstanding:** the off-host copy wired up (documented, not implemented), `docs/runbooks/POSTMORTEM_TEMPLATE.md`, the full-environment DR runbook, and backup-failure alerting (shared with PH2.10). The `RESTORE.md` runbook this item expected lives as `docs/operations/BACKUP_AND_RESTORE.md` §9, alongside the architecture it depends on.
- **Status (2026-08-05): the disaster-recovery half is delivered by the PH2.10 sprint** (sprint-track numbering). Delivered: `docs/operations/DISASTER_RECOVERY.md` (ten runbooks R1–R10, RPO/RTO decomposed phase by phase, seven named recovery assumptions, business-continuity assumptions, severity + escalation matrix, drill schedule, pre-disaster checklist, nine honest limitations), `docs/runbooks/POSTMORTEM_TEMPLATE.md` (the file this item explicitly expected), and two executable scripts — `scripts/dr/dr_verify.sh` (four-layer diagnosis and post-recovery verification; an empty restored database is a **failure**, manifest count comparison, running-build assertion) and `scripts/dr/deploy_rollback.sh` (deployment ledger + verified rollback with automatic revert). 41 hermetic tests. Measured against the live database: restore of 21 collections **4.48 s** with 21/21 matched, full verification **1.10 s**, config recovery **0.17 s** for 14 files. **Still outstanding for this roadmap item: the off-host copy actually wired up** — it is the one limitation that leaves a whole runbook (R7, complete server loss) unexecutable, and it is now recorded as such rather than as a footnote. Backup-failure alerting remains shared with roadmap PH2.10.

## PH2.12 — Infrastructure Certification & Staging Sign-off

- **Objective:** Staging is a production twin and has soaked clean.
- **Scope:** Stand up the durable staging environment from PH2 artifacts; 7-day soak with live-like traffic (scanner + market hours) and zero Sev-1; execute the DEPLOYMENT.md deployment checklist end-to-end; write the Infrastructure Certification Report; sign-off in PRODUCTION_HARDENING.md §17.
- **Deliverables:** Running staging; soak log; `docs/infra/PH2_CERTIFICATION.md`.
- **Files Expected:** `docs/infra/PH2_CERTIFICATION.md`, `.claude/PRODUCTION_HARDENING.md`, `.claude/CHANGELOG.md`.
- **Dependencies:** PH2.1–PH2.11 complete.
- **Acceptance Criteria:** All DEPLOYMENT.md checklist items ✓; soak week clean; packaging/CI-CD/observability categories re-score ≥ 8.0.
- **Validation Steps:** Checklist execution log; soak metrics attached.
- **Rollback Plan:** N/A (assessment sprint). Failures reopen source sprints.
- **Estimated Difficulty:** Medium. **Estimated Time:** 2 days active + 7-day soak (calendar).
- **Success Metrics:** Infrastructure sign-off recorded; PH2 exit gate passed.
- **Status (2026-08-09): ✅ COMPLETE — CONDITIONALLY CERTIFIED — infrastructure score 8.0/10.** Report at **`docs/infrastructure/PH2_CERTIFICATION.md`** (not `docs/infra/` — it lives with the existing `docs/infrastructure/` tree). **Two scope deviations, both deliberate:** (1) **the 7-day staging soak was NOT performed** — there is still no durable staging environment, so the soak and its metrics carry to PH3; (2) in its place the sprint ran a **full certification against a live local stack**, which was possible for the first time because this is the **first PH2 sprint with a working Docker daemon** (29.4.0). Every sprint from PH2.7 onward recorded "no Docker daemon in the sprint environment" as a limitation, and that is precisely where the defects were. **Found and fixed 1 Critical + 2 High**, all of which had passed every prior hermetic review: `deploy_rollback.sh` **silently recreated nothing and reported `rollback verified`** while the bad release kept serving (Compose ranks shell env above `.env`, and the script's own config loader exports `BACKEND_IMAGE_TAG` — so its atomic file rewrite was outranked by itself); the **BLOCKING flake8 CI gate has been red on every run since PH2.4** (`backend/.venv-ci` never matched an exclude list containing `venv`/`.venv`, because flake8 matches on basename); and `dr_verify.sh`'s running-build probe **could never pass** (parsed `app_version`/`vcs_ref`; the endpoint emits `build.version`/`build.revision`). **Re-score against the acceptance criterion "packaging/CI-CD/observability ≥ 8.0": Packaging 1.0 → 9.0 ✅, Observability 9.0 → 9.0 ✅, CI/CD 2.0 → 6.0 ❌** — CI/CD misses the bar because there is no CD at all (no registry, no deploy workflow) and the dependency-audit gate is failing on 6 runtime CVEs. **The PH2 exit gate is passed conditionally**, with six required-before-production actions in §24 of the report — of which the load-bearing three are the runtime dependency upgrades (`cryptography` 48.0.1, `aiohttp` 3.14.1), the **off-host backup copy** (still unwired, so R7/complete server loss remains unexecutable), and **alerting** (there is none; detection is manual, which dominates RTO).

---

# PH3 — Production Quality Assurance

Goal: quality is verified, honest, and enforced. Closes findings H7–H9, M12–M16 residuals, and certifies launch.

---

## PH3.1 — Backend Test Suite Repair & Hermeticity

- **Objective:** Default test run 100% green with no external services.
- **Scope:** Fix stale `test_run_cycle_trails_and_books_targets` (accept `closed_trades` key); mark the 5 live-server tests (`test_phase2/6/7`) `@pytest.mark.integration`; register markers in `pytest.ini`; default run excludes integration; integration suite adapted to run against the compose stack in CI (PH2.6).
- **Deliverables:** Green hermetic suite (347 collected, integration deselected by default).
- **Files Expected:** `backend/tests/test_trading_engine.py`, `backend/tests/test_phase*.py`, `backend/pytest.ini`.
- **Dependencies:** None — **can start immediately, in parallel with PH1.1.**
- **Acceptance Criteria:** `pytest` passes on a machine with no services running; `pytest -m integration` passes against the running stack.
- **Validation Steps:** Run both modes locally and in CI.
- **Rollback Plan:** Trivial revert; no runtime impact.
- **Estimated Difficulty:** Low. **Estimated Time:** 0.5 day.
- **Success Metrics:** Finding H8 closed; CI signal trustworthy.
- **Status (2026-08-09): ✅ COMPLETE — CERTIFIED.** Report at **`docs/testing/PH3.1_TEST_CERTIFICATION.md`**; developer reference at `docs/testing/TEST_ARCHITECTURE.md`. **Default `pytest`: 1,035 passed, 0 failed, 0 errors, ~2m20s** (was 1,016 passed / **47 failed / 51 errors** / 176s) — verified in a fully scrubbed environment (`env -i`, no `.env`, no exported secrets), which is the CI-compatibility proof. **Scope was larger than planned, in one direction that mattered:** the sprint's premise was that the hermetic suite was already hermetic and only the live suites needed marking. It was not. Socket instrumentation found **three tests in the default suite opening live TLS connections on every run** — `api.anthropic.com`, Google Generative Language, Yahoo Finance — **authenticated with the developer's real production API keys**, because `server.py` calls `load_dotenv(backend/.env, override=True)` at import and `conftest.py` imports `server`. They passed either way: the call sites catch broadly, so a live call and a mocked one look identical in the output. Now closed three independent ways (`tests/_testenv.py` deterministic env + `PYTHON_DOTENV_DISABLED`, `tests/_netguard.py` socket guard, blank credentials) and **measured at zero**. **Two real implementation defects found and fixed** in `backend/security/secrets.py`: `app_env()` and `get()` used `(environ or os.environ)`, so an **explicitly empty environment mapping silently resolved to the host's live configuration** — wrong in the dangerous direction for a security-config reader, and invisible until the test process stopped inheriting a `.env`. The chartered stale assertion `test_run_cycle_trails_and_books_targets` was **already repaired** by a prior sprint (verified against `services/trading_engine.py:346`; exact-equality assertion intact, not weakened). **Deliverable counts differ from the plan** — 1,130 collected, not 347 (the estimate predates PH1/PH2 adding ~780 tests) — and `pytest.ini` does not exist; markers went into the existing `backend/pyproject.toml`. **Also delivered beyond scope:** 19 new hermetic API-contract tests converted from the live suite (mutation-checked), the hardcoded `admin@alphapartner.com`/`admin123` pair removed from five files, filesystem-scraping of the deployment URL removed from two, an explicit `ALLOW_LIVE_WHATSAPP_SEND=1` gate on the one test that sends a real billable message, and the **coverage baseline: 59.2% of application statements** (`security/` 94.8%, `observability/` 95.8%, `server.py` 51.9%, `services/` 42.4%). `pytest -m integration` **skips cleanly** without a deployment rather than failing — and `REQUIRE_LIVE_BACKEND=1` turns those skips into failures, which **the PH2.6 integration job must set** or a stack that failed to boot will skip its way to green.

## PH3.2 — Mock Data Eradication (ADR-021 Compliance)

- **Objective:** No fabricated data in any production code path.
- **Scope:** Admin revenue analytics (`server.py:4357`) computed from the `payments` collection or an honest empty state; `revenue_today` placeholder (`:4059`) replaced with real aggregation; hardcoded feature-usage percentages (`:4374`) replaced with real event counts or empty state with "insufficient data" messaging; repo-wide sweep for other fabricated values; frontend admin components render the empty states per CLAUDE.md Error Handling rules.
- **Deliverables:** Real aggregations + empty states; sweep report; tests for both data-present and data-absent cases.
- **Files Expected:** `backend/server.py`, `frontend/src/components/admin/*`, `frontend/src/pages/admin/*`, `backend/tests/test_admin_analytics.py`.
- **Dependencies:** None — parallel-safe.
- **Acceptance Criteria:** Zero fabricated numbers renderable in admin UI; empty DB shows honest empty states, not zeros-styled-as-data.
- **Validation Steps:** Tests against empty and seeded DBs; manual admin console review.
- **Rollback Plan:** Revert PR (returns to violating state — only acceptable pre-launch).
- **Estimated Difficulty:** Medium. **Estimated Time:** 1 day.
- **Success Metrics:** Findings H7/R-09 closed; ADR-021 grep-clean.
- **Status (2026-08-16): ✅ COMPLETE — both halves.** Delivered under two sprint labels: the audit as **"PH3.8 — Analytics & Data Integrity"**, the removal as **"PH3.9 — Mock Removal & Production Data Integrity"**. Report: **`docs/architecture/ANALYTICS.md`** (§11 is the removal record). **Numbering note:** the briefs called these PH3.8/PH3.9; this roadmap's PH3.8 is *Accessibility & Responsive Audit* and PH3.9 is *End-to-End Critical Journeys*, both untouched. Same brief-label drift as PH3.2–PH3.7.
- **Delivered (removal half) — there are no MOCK metrics left in the product: 17 → 0.** Totals moved 4 REAL / 26 DERIVED / 17 MOCK / 5 UNAVAILABLE → **4 / 32 / 0 / 17**; six became real numbers, eleven became explicit UNAVAILABLE, and two tests hold both facts in place (`test_no_metric_is_classified_mock`, `test_every_ph38_mock_records_what_ph39_did_to_it`). **The governing rule — never replace mock data with fake realistic data — meant departing from the PH3.8 inventory in three places, each of which would otherwise have swapped a fabricated number for a *systematically wrong* one** (worse, because a wrong number that came from a real query is far harder to spot). **MAU:** the prescribed 30-day query over `db.sessions` cannot be answered — a TTL index deletes each session one refresh lifetime after last use (7 days by default), so it returns a 7-day count under a 30-day label, undercounting more the longer ago a user churned; `active_users` checks the window against the retention horizon and refuses it, and the refusal self-corrects if the TTL is raised. **API health:** "rewire" could not apply to the row list, which named *vendors* with individual latencies while the Market Gateway deliberately hides which upstream served a request (`MARKET_DATA_ARCHITECTURE.md`) — only `market_data` and `news` are instrumented, the rest report `not_measured` rather than a green badge, and **the Razorpay row was deleted outright**: `status: "configured"` with a 300ms latency for an integration that exists nowhere in the codebase. **`ai_requests_today`:** rewiring it to `ai_requests_total` would trade a durable database count for an in-process counter that resets on every deploy and covers one worker of N — it was **renamed** to `chat_messages_today`, which is what it always counted (both turns, so ~2× the provider calls). **Revenue is gated on whether a payment integration exists, not on whether `db.payments` is empty** — gating on emptiness is how the first stray document flips revenue back to "available" and reports it as fact, which is PH3.8's own finding in a new implementation; the gate is one named predicate and the aggregation behind it is written and tested now, including that created/pending/authorized are intents rather than revenue. **The most dangerous removal was `_synthetic_backtest`**, not an admin number: its win count was `randint(10, 16)` of 20, so the win rate was always 50–80% and a losing strategy could not be represented, and it passed through the *same* `_compute_metrics` as the real path so an invented Sharpe ratio and drawdown rendered in the same UI cards — reached on **any** yfinance failure, so a network blip produced flattering fabricated performance. Deleted; 503. **D-4 fixed** (carried from PH3.5): the refund endpoint returned success for any string while writing `payment.refunded` to the immutable audit log — now 501 and **no audit record**, the audit half being the worse one. **Five further defects fixed** (ANALYTICS.md §13.1), including `api_health`/`server_health` as literals that read "healthy" during a total outage and seven feature rows reporting `usage_count: 0` for features nothing measures. **The frontend was where the sprint could have been silently undone:** `{stats?.mrr || 0}` turns `null` into `₹0` in the same typeface as a measured figure with no test failing, so every admin metric routes through one `MetricValue` component and the tests assert **the absence of `₹0`** rather than the presence of an em-dash — the converse asserted too, since a real `0` is a measurement. **One index** (`sessions {last_used_at, user_id}`, pinned in `HOT_QUERIES`), with query costs counted in tests: DAU is 1 query flat in session count, growth 2, every revenue metric **0** because the gate short-circuits before the database. **Tests: +109 backend (2,425 → 2,534 green), +20 frontend (375 → 395 green); PH1 security 452 unchanged; production build clean; static mock scan reviewed.** **Remaining unavailable:** everything revenue-shaped is one payment integration (MRR/ARR additionally need subscription records); retention, MAU and feature adoption need a durable activity stream and **none is back-fillable**; AI cost needs token accounting; profit factor needs per-fill broker charges.
- **Delivered — and the scope discipline is the headline.** The brief was explicit that this sprint must not silently replace mock analytics with invented calculations, and the temptation was real: several fabricated numbers have a plausible formula sitting next to them. **Seventeen mock metrics were left in production with unchanged values.** What changed is that every one now declares itself in its API response (`provenance`, `status`, `note`, `mock_metrics`) and renders behind a visible "Simulated" marker — so the flag is now the only thing standing between an operator and a set of invented business metrics, and tests hold it in place until the removal sprint lands. **The inventory is code, not a table that drifts:** `backend/analytics/registry.py` classifies every metric REAL / DERIVED / MOCK / UNAVAILABLE with source, calculation, window, consumer and — for mocks — the production source that would replace it, and `tests/test_analytics.py` asserts every endpoint it names still exists on the live route table and every mock carries a replacement plan. **Totals: 4 REAL, 26 DERIVED, 17 MOCK, 5 UNAVAILABLE.** The rule applied throughout, and the one that reclassified most entries: **an endpoint existing is not evidence a metric is real** — each was traced collection → service → route → component → pixels, and where that trace ended at a literal, a formula over a proxy, or a collection nothing writes to, the entry says so.
- **Delivered — the structural finding.** **`db.payments` has no writer anywhere in the codebase.** The platform has no payment integration; three admin endpoints read the collection and `ensure_indexes` indexes it, and nothing has ever written to it. Every revenue figure is therefore fabricated: MRR/ARR infer revenue from **role counts × hardcoded ₹499/₹999**, and roles are assigned by an admin through `grant-plan` with no payment involved, so every comped, internal and beta account is counted as paying. `revenue_today` is **`count(all payment documents) × ₹499`** — not a sum, not date-filtered — and reads ₹0 only *because the collection is empty*, so the first record to land reports ₹499 of "today's revenue" whatever it was for. The 30-day revenue chart is `2500 + i×150 + (500 if i % 7 == 0)` with **no database access of any kind**, rendered by Recharts with no visual distinction from a real series. `refunds: 0` is additionally contradicted by the product: PH3.5's D-4 found the refund endpoint is a stub returning success while writing a `payment.refunded` audit record for a refund that never happened.
- **Delivered — ten defects, each reproduced before being fixed.** **F-2 (P0): `eod_report_job` crashed on every single run.** It iterated every trade and called `t.get("exit_time", "").startswith(today)`; an open trade stores `exit_time: None` *explicitly*, so `.get` returns `None` and `None.startswith` raised `AttributeError` — swallowed by a broad `except` into one log line. **No end-of-day report was ever written and no user was ever notified**, for as long as any position was open. Reproduced with a single open trade. **F-2b (P0): the P&L it would have reported was everybody's** — the job summed the closed trades of the entire platform and sent that one figure to every user as "Today's P&L" (measured: users at +₹1,000 and −₹400 both told "+₹600"), a wrong personal number *and* a cross-tenant disclosure of other users' aggregate trading performance. **F-1 (P1): paper trades contaminated every real-money statistic** — a ₹9,000 virtual gain beside a ₹500 real loss reported as **+₹8,500 at a 50% win rate**; `build_intelligence` excluded paper trades from holdings and included them in realised P&L **in the same function**, so one payload carried two definitions of "my portfolio". **F-6 (P1): partial exits were invisible to the daily loss limit** — after booking ₹500 at target 1, `realized_pnl_today` read **₹0**, because every realised-P&L metric keys off `pnl` and `pnl` is written only at full close. That is a risk control, not a display bug. **F-10 (P1): equity-curve ranges sliced snapshot count, not calendar days** — `snaps[-30:]` on a monthly cadence returned **thirty months** labelled "1M". **F-8 (P1): portfolio return counts deposits as performance** — depositing ₹1L into a ₹1L portfolio reported **+100% return and a +100% "best day"**. **F-11 (P1): the synthetic backtest was non-deterministic** — seeded from `hash(str)`, which Python salts with `PYTHONHASHSEED`, so identical input returned **80% / 60% / 80% win rates across three consecutive processes**; and `randint(10, 16)` of 20 trades means a losing strategy cannot be represented. Also **F-5** (`to_list(500)` with **no sort**, silently truncating all-time P&L, win rate and setup statistics to an arbitrary 500 rows — the cap was never a performance guard), **F-3** (breakeven scored as a loss, compounded by `reset_paper_capital` force-closing positions at exactly ₹0), **F-4** (every window a UTC day on an IST exchange, so the daily trade counter and loss budget reset at 05:30 IST), **F-7** (the Dashboard labelled lifetime unrealised P&L as **"Today's P/L"**), **F-9** (an unfetchable quote contributing ₹0 of unrealised paper P&L, indistinguishable from an unmoved position) and **F-18** (nine admin dashboard cards each carrying a hardcoded **"+12% vs last month"** in the gain colour, beside user counts, MRR, open tickets and broker links alike — deleted rather than flagged, because there was never anything behind it).
- **Delivered — `backend/analytics/`, and what it deliberately is not.** Five modules: `periods` (the one documented timezone strategy — **storage UTC, boundaries IST**, half-open `[start, end)` windows; a fixed `+05:30` offset rather than a `ZoneInfo` lookup that can fail on a host without tzdata), `contract` (the metric envelope, with construction-time invariants that make "unavailable" **unrepresentable as zero** and force a MOCK provenance to carry a MOCK status), `registry` (the inventory), `queries` (**filters only, no arithmetic**), `quality` (source-data validation that reports and never repairs, never silently excludes). **The package computes no business metrics of its own** — `services.portfolio_engine` and `services.trading_engine` remain the single source of truth for the math. What moved is the *scoping*, which is what had drifted: whether a paper trade counts, what "closed" means (`!= "OPEN"` vs `== "CLOSED"`, where the second silently drops every trade that exited at a target or a stop), and which day is "today" were being decided independently at six call sites.
- **Delivered — performance.** Three indexes, each justified against a query in the request path. **`portfolio_snapshots {user_id, date}` unique** — the collection had **no index of any kind since Sprint 8 created it**, so every Portfolio page load scanned every user's history and the nightly 16:05 IST snapshot job scanned the whole collection once per user, which is O(users²) work in an unattended job; the uniqueness also makes one-snapshot-per-user-per-day the database's rule rather than the upsert's private assumption. **`users {created_at}`** and **`chat_messages {created_at}`** — the admin dashboard's signup and AI-request counts were `$regex` prefix matches on unindexed string fields (full scans on every page load, growing with total signups forever), and no existing compound index can serve a query that constrains neither `user_id` nor `session_id`. All three pinned in `tests/test_perf_regression.py::HOT_QUERIES`. Unbounded work removed from six paths, including `eod_report_job`'s `find({}).to_list(1000)` over the entire platform's trades. **No caching was added and Redis was deliberately not used** — correctness semantics were the thing under repair, and a TTL over a metric whose window semantics had just changed would freeze the old answer and make the next defect much harder to see.
- **Delivered — tests.** **122 backend + 11 frontend added; backend 2,303 → 2,425 passed / 0 failed / 6 xfailed in ~2m38s; frontend 364 → 375 passed across 21 suites in ~6s.** Every pre-existing test passed **unchanged** — the contract is additive by construction, adding an `analytics` block beside the flat keys rather than replacing them. Production build green; blocking lint gate clean; the new package is flake8-clean at the full standard and net advisory findings went **down** by two. **One test-harness defect found in this sprint's own suite and worth carrying forward:** 23 tests passed in isolation and failed in the full run with `RuntimeError: There is no current event loop`, because a suite earlier in the alphabet closes the main-thread loop — `asyncio.get_event_loop()` was the culprit, not the application, and the helper now uses a private loop and restores the previous policy state so it neither inherits nor exports the problem.
- **Remaining half (the removal sprint).** The 17-item priority-ordered specification is `docs/architecture/ANALYTICS.md` §11 and `analytics.registry.ph39_inventory()`. **Sequencing note worth acting on: four items need no new data at all** — admin API health, AI provider latency/failures, Redis/scheduler status and AI request counts. PH3.7 already ships real health probes and real `provider_requests_total` / `ai_requests_total` / `ai_request_errors_total` families; those four are **wiring, not instrumentation**, and they are the highest value-per-hour work in the list. Today the admin API-health page reports `overall_status: "healthy"` during a total provider outage and `failures: 0` beside a live failure counter — an operator watching the console cannot see an outage the platform is already measuring. Everything revenue-shaped is blocked on a payment integration and should be **one** change, not five.

## PH3.3 — Frontend Test Foundation & Smoke Suite

- **Status (2026-08-10): ✅ COMPLETE — delivered under the sprint label "PH3.2 — Frontend Testing & UI Regression Foundation".** Report at **`docs/testing/PH3.2_FRONTEND_TEST_CERTIFICATION.md`**. **Numbering note:** the sprint brief numbered this work PH3.2; this roadmap numbers PH3.2 as *Mock Data Eradication*, which remains **NOT STARTED and untouched**. The certification document keeps the brief's label. Read "PH3.2" in `docs/testing/` as this roadmap's PH3.3.
- **Delivered:** **313 tests / 17 suites, green in ~8s** — well past the ≥15 smoke-test bar. Jest 27 + React Testing Library 16 through `craco test` (the runner already inside `react-scripts`); **Vitest was rejected** because it would run tests through esbuild while production ships through webpack/CRA. Covers authentication (login, register, logout, session restore, expiry, Google OAuth callback), routing and guards driven off the *real* route table, admin access control, dashboard shell, paper-trading order entry, AI workspace, watchlist, notifications, admin dashboard and the realtime store — each critical screen in all four states (loading / success / empty / error). **Scope also reached into PH3.4 and PH3.8:** `services/api.js` interceptors at **100%**, `tradeService.js` at 94.3%, and an accessibility baseline (accessible names, label association, `role="alert"`) on the critical forms. **Coverage baseline: 33.6% overall statements, 77.0% on critical paths** — overall is low by design because ~30 unscoped feature pages count against it. **Five frontend defects found and fixed** (dead error-message fallback that discarded every client-thrown message; missing `role="alert"` on auth error banners; paper-trading load failures rendering as an *empty account* rather than an error; form labels not programmatically associated; unlabelled icon-only buttons). **One pre-existing build defect found and NOT fixed** (out of scope, documented): `yarn build` fails at `[eslint] Failed to load config "react-app"` because `eslint@^9` in devDependencies displaces the `eslint@^8` that `react-scripts` requires — **reproduced on pristine pre-sprint dependencies**, so it predates this work. The app itself compiles cleanly (`DISABLE_ESLINT_PLUGIN=true yarn build` succeeds). **CI gate not yet activated** — the PH2.6 workflow placeholder still needs wiring.
- **Objective:** Frontend regressions detectable by machine.
- **Scope:** Jest + React Testing Library configured through craco; test utilities (providers wrapper, router, mocked services); smoke tests: login/register/logout flows, Dashboard renders with mocked data, protected-route redirect, Sidebar navigation; CI job activation (placeholder from PH2.6).
- **Deliverables:** Test infrastructure; ≥ 15 smoke tests; CI gate.
- **Files Expected:** `frontend/craco.config.js`, `frontend/src/setupTests.js`, `frontend/src/test-utils.jsx`, `frontend/src/**/__tests__/*.test.jsx`, `.github/workflows/ci.yml`.
- **Dependencies:** PH2.5 for the gate; test-writing itself has none.
- **Acceptance Criteria:** `npm test` green locally and in CI; breaking the login form breaks the build.
- **Validation Steps:** Mutation spot-check (intentionally break a flow → red).
- **Rollback Plan:** Additive; gate can be temporarily non-blocking if flaky, with an issue opened.
- **Estimated Difficulty:** Medium. **Estimated Time:** 2 days.
- **Success Metrics:** Finding H9 closed at smoke level.

## PH3.4 — Frontend Service & Hook Coverage

- **Objective:** The service layer (the frontend's business logic) properly tested.
- **Scope:** Unit tests for API services (including `adminService.js`), auth context/hooks, Socket.IO event-handling hooks (reconnect, event batching from R9), React Query cache behavior; coverage reporting wired; ratcheting coverage threshold (start at achieved level, never decreases).
- **Deliverables:** Service/hook test suites; coverage gate.
- **Files Expected:** `frontend/src/services/__tests__/*`, `frontend/src/hooks/__tests__/*`, `frontend/package.json` (coverage config).
- **Dependencies:** PH3.3.
- **Acceptance Criteria:** All service modules have tests covering success/error/retry; coverage threshold enforced in CI.
- **Validation Steps:** Coverage report review; CI gate check.
- **Rollback Plan:** Threshold adjustable; tests additive.
- **Estimated Difficulty:** Medium. **Estimated Time:** 2 days.
- **Success Metrics:** Service-layer coverage ≥ 70%, ratcheting toward TESTING.md's 80%.

## PH3.5 — API Contract & Error-State Testing

- **Status (2026-08-10): ✅ COMPLETE — CERTIFIED, delivered under the sprint label "PH3.3 — Backend Tests & API Coverage".** Report at **`docs/testing/PH3.3_BACKEND_TEST_CERTIFICATION.md`**. **Numbering note:** the sprint brief numbered this work PH3.3; this roadmap numbers PH3.3 as *Frontend Test Foundation* (already delivered under the brief label "PH3.2"). Read "PH3.3" in `docs/testing/` as this roadmap's PH3.5. Roadmap PH3.2 (*Mock Data Eradication*) remains **NOT STARTED and untouched**.
- **Delivered:** **1,115 new tests across 8 suites; backend total 1,035 → 2,150** (2,144 passed + 6 xfailed), green in ~2m46s with no server, database, credentials or network. **201 API routes inventoried** from the application object. The acceptance criterion "authz matrix 100% pass" is met *mechanically*: `tests/_routes.py` classifies every route by its **resolved dependency graph** (not its URL), and three suites parametrize over that — 126 authenticated routes × anonymous-rejection and forged-token rejection, 29 admin routes × non-admin rejection, plus horizontal-escalation coverage on every user-owned collection and the full admin→super_admin boundary (each asserting the **stored document**, not just the status code). A route added later is covered automatically; guard tests fail if the derived lists ever empty. Error-envelope criterion met: every 4xx carries a parseable `detail`, and no response leaks `Traceback`/`/Users/`/`motor`/`pymongo`/`server.py`/`site-packages`. **Eight genuine defects found; six fixed, two documented and assigned** — **D-11 (HIGH)** a blank `SMTP_PORT` reached `int("")`, 500ing `GET /api/data-sources` and **breaking all outbound email including password reset** on any install scaffolded from `.env.example`; **D-1 (MED)** `page=0` → negative Mongo `skip` → 500 and `limit=0` → ZeroDivisionError on all four paginated admin endpoints (fixed declaratively with `Query` bounds, which also closes an unbounded full-collection scan); **D-3 (MED)** `duration_days` → `timedelta` TypeError/OverflowError; **D-6 (MED)** 18 routes reading `await request.json()` 500ed on a malformed body (fixed with one central `JSONDecodeError` handler rather than 18 try/excepts); **D-9 (LOW)** raw `ObjectId()` on aggregated `user_id`; **D-2 (LOW)** notification read-marking reported success across an ownership boundary. **Deferred with owners: D-4 (HIGH)** — `POST /api/admin/payments/{id}/refund` is a stub returning `success: true` for any string while writing a `payment.refunded` audit record for a refund that never happened → **PH3.9** *(fixed there on 2026-08-16: 501, and no audit record)*; **D-10 (MED)** — registration performs no email-format validation, so a signup typo creates a permanently unrecoverable account → **next auth-touching sprint**. Both are pinned as `xfail` tests that will XPASS when fixed. **Two pre-existing test-isolation defects found and fixed:** `broker_engine` holds its own Mongo handle that `fake_db` never patched (all 33 broker routes were hitting the real Motor client during "hermetic" tests, surfacing as `RuntimeError: Event loop is closed`), and a `monkeypatch` in `test_ai_workspace.py` permanently shadowed a class attribute so later class-level patches were silently ignored. **Method note worth carrying forward:** three suites initially "found" a HIGH provider-timeout defect that did not exist — containment lives at the transport boundary, so a timeout reaches a route as `None`, never as an exception. Those tests were rewritten rather than the application, and each guarantee is now asserted **at the layer that provides it**. **Coverage: 59.2% → 65.0%; `server.py` 51.9% → 67.2%** (+15.3 across 2,914 statements). PH1 security (452) and PH3.2 frontend (313) regressions both unchanged and green. **No trading logic, API shape, rate limiter or security test was modified.**
- **Objective:** Every endpoint honors the CLAUDE.md error-handling contract.
- **Scope:** Backend: parametrized tests asserting consistent error envelope (shape, no stack traces, no internal paths) across 4xx/5xx; auth matrix tests (each protected route × unauthenticated/wrong-role); frontend: loading/empty/error/retry states verified for key pages; document the error envelope in API_REFERENCE.md.
- **Deliverables:** Contract test suites both sides; API_REFERENCE.md error section.
- **Files Expected:** `backend/tests/test_error_contract.py`, `backend/tests/test_authz_matrix.py`, `frontend/src/**/__tests__/*states.test.jsx`, `.claude/API_REFERENCE.md`.
- **Dependencies:** PH3.1, PH3.3.
- **Acceptance Criteria:** No endpoint leaks stack traces/internal errors; authz matrix 100% pass; key pages prove all four UI states.
- **Validation Steps:** Suite runs; manual spot check of raw 500 responses on staging.
- **Rollback Plan:** Additive tests.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1.5 days.
- **Success Metrics:** SECURITY.md error-handling items pass; zero information-leak findings in PH1.12 re-check.

## PH3.6 — Backend Decomposition (server.py → Routers)

- **Objective:** Retire the 4,823-line monolith without behavior change.
- **Scope:** Extract FastAPI routers by domain — auth, admin, portfolio, trading, market, ai, realtime, monitor — plus `security/`, `observability/` modules already begun in PH1/PH2; `server.py` becomes app factory + router registration; **no logic changes** — pure moves, verified by the (now trustworthy) test suite; update SYSTEM_ARCHITECTURE.md structure section.
- **Deliverables:** Router package; slim `server.py` (< 300 lines); unchanged API surface.
- **Files Expected:** `backend/routers/*.py`, `backend/server.py`, `.claude/SYSTEM_ARCHITECTURE.md`.
- **Dependencies:** PH3.1 (safety net), PH3.5 (contract tests lock the surface). Do **after** PH1 to avoid conflicting with security edits.
- **Acceptance Criteria:** Full suite (hermetic + integration) green; OpenAPI schema diff empty (routes/models identical); no import cycles.
- **Validation Steps:** OpenAPI JSON diff before/after; integration run; smoke on staging.
- **Rollback Plan:** Single revertible PR series per router; keep each extraction independently green.
- **Estimated Difficulty:** High. **Estimated Time:** 3 days.
- **Success Metrics:** Finding M13/R-12 closed; new-endpoint pattern documented.

## PH3.7 — Performance Benchmarking & Load Testing

- **Status (2026-08-14): ✅ COMPLETE — BOTH HALVES CERTIFIED.** Benchmarking delivered under the sprint label "PH3.4 — Performance Engineering & Optimization" (**`docs/performance/PH3.4_PERFORMANCE_CERTIFICATION.md`**); load testing delivered under the sprint label "PH3.5 — Load Testing & Capacity Validation" (**`docs/performance/PH3.5_LOAD_TEST_CERTIFICATION.md`**). Read both "PH3.4" and "PH3.5" in `docs/performance/` as this one roadmap item; the tracker's own PH3.4 (*Frontend Service & Hook Coverage*) and PH3.5 (*API Contract & Error-State Testing*) are separate line items. **Numbering note:** the sprint brief numbered this work PH3.4; this roadmap numbers PH3.4 as *Frontend Service & Hook Coverage* (still NOT STARTED, untouched). Read "PH3.4" in `docs/performance/` as the benchmarking half of this roadmap item. **The load-testing half was subsequently delivered as the brief's PH3.5** (see the separate Delivered bullet below); of the scope listed here, **k6 profiles and real-time fan-out under burst are done**, while **Lighthouse CI and the CI bundle budget are still owed** and are carried forward with the monitoring work. **At the time of the benchmarking half, the acceptance criterion "API p95 < 500 ms on staging" was NOT met because it was not measurable:** there is no staging deployment, so no p95, no LCP and no concurrency figure was produced. Six metrics are marked explicitly *unavailable* in the certification (§2) rather than estimated — Redis timing (no Redis on the measurement host), LCP/real-user metrics, production p50/p95/p99 under load, Socket.IO fan-out at scale, and AI provider latency (no key is configured in any measurement environment, by design).
- **Delivered:** **The application code was measured not to be the bottleneck** — no prioritised endpoint's own logic exceeds **11 ms** in steady state. Two other layers were, and neither was visible from the code. **(1) Four collections had no index of any kind** — `watchlist`, `holdings`, `orders`, `payments` — backing the most-visited pages: `GET /api/watchlist` examined **2,000 documents to return 5**, every `/api/portfolio*` route **4,800 to return 12**, and the cost scaled with *total signups* rather than the caller's own data. The sharpest case was the AI chat path: the continuity lookup filters on `session_id` **alone**, which the existing `{user_id, session_id}` index cannot serve (a compound index is only usable from its leading field), so **every message sent to the AI scanned all of `chat_messages`** — 12,000 examined to return 10. Seven more hot queries produced blocking in-memory `SORT` stages, which MongoDB **aborts past 100 MB**. **12 indexes across 6 collections** fixed all of it (400×–2,000× fewer documents examined; 6 of 7 sorts now index-served; the unread-notification badge became a covered `COUNT_SCAN` touching **zero** documents). `ensure_indexes()` was **extracted from the 160-line `startup()` handler** so the index set can finally be asserted at all — the in-memory test double has no query planner, so an unindexed collection passed all 2,144 existing tests identically to a perfectly indexed one. **(2) Every provider call opened a new TLS connection:** `fetch_yahoo_quote` runs once per symbol under `asyncio.gather`, each call building its own `httpx.AsyncClient` — measured **803.8 ms → 236.2 ms (3.40×)** through the application's own `real_quotes_map` after introducing loop- and timeout-keyed pooled clients (`services/http_client.py`). Layer attribution showed **>90% of quote-enriched endpoint latency was provider transport**. Also: `/api/admin/logs` **N+1 removed** (31 → 7 queries; 201 → 7 at a page of 200) and the admin dashboard's **11 independent counts gathered** (11 serial RTT → 1). **Equally load-bearing: the places nothing changed.** No frontend optimization was warranted — route splitting already complete, all **13** polling timers already disconnected-only fallbacks (verified by measuring **zero** requests across 70 s with the socket live), no duplicate request per mount. Redis needed nothing (Sprint R9's `MGET` batching was already the best available change). No blocking operation in any request path. `recharts`' transitive `@reduxjs/toolkit` looked like an easy 280 KiB win and is not removable; `framer-motion` in the entry chunk is **correct**. **Two findings deliberately deferred with measurements and owners:** the rate limiter's `update_one`-then-`find_one` could become one atomic `find_one_and_update`, removing a query from **every request on all 201 routes** while also closing a documented non-atomic race — but it is PH1-certified security surface (→ next security-touching sprint); and whether `fetch_yahoo_quote` needs 3 months of history for a 14-period RSI is an indicator-accuracy question, not a performance one. **38 regression tests, none asserting wall-clock time** — query counts asserted *identical at 3 rows and 33* (the N+1 signature, which a constant cannot be updated to satisfy), index coverage recorded by running `ensure_indexes()` against a stub rather than parsing source, payload bounds, gather structure, per-request floor, plus a counter-test proving Watchlist *does* poll when disconnected (without which "no polling while connected" would pass if every timer had been deleted). **Two of the sprint's own measurements were wrong before they were right and are documented as method notes:** a corpus typo (`target_1` for `target1`) manufactured a `KeyError` that looked like a HIGH defect, and a frontend test drove a store field no selector reads *and* set it before the provider overwrites it, manufacturing a polling defect that does not exist. **Neither was reported.** **No regression:** backend 2,144 → **2,176 passed**, PH1 security **452 unchanged**, frontend 313 → **319 passed**, production build green, bundle byte-identical, no API contract / trading logic / AI decision logic / prompt / model selection changed.
- **Delivered (load-testing half, 2026-08-14, sprint label "PH3.5"):** **Neither the application code nor MongoDB is the constraint.** From 5 to 100 concurrent virtual users the system served **zero 5xx, zero timeouts, 100% of functional checks**, with a median that did not move — **10.9 ms at 5 users, 8.3 ms at 100** — and Mongo never queued an operation. PH3.4's measured 4–5 query floor held at **3.9–4.6 across a 65× load range**; six of PH3.4's seven claims were confirmed under concurrency and **the seventh was corrected**. **Three P1 findings, all of which needed concurrency to appear. (L-1)** `REDIS_MAX_CONNECTIONS` defaults to **24**, below the application's own fan-out width — a watchlist request does one `cache_get` per symbol — and **redis-py's pool raises rather than queues when exhausted**, with five failures opening a **process-wide** circuit breaker that drops *the entire cache* to the in-process fallback for 10 s. During those 10 s every quote misses and goes upstream: the worst possible moment to add provider load. p95 goes 21 ms at 100 rps → 187 ms at 150 → 515 ms at 200 → **10,485 ms at 250**. **The identical sweep with `REDIS_MAX_CONNECTIONS=200` removes it entirely** (11.1 ms at 250 rps, 29.1 ms at 400, **zero** Redis failures at every rate), lifting sustained read throughput **~217 → ~410 rps, 1.9×, with no code change** — and the ceiling that remains is honest: **100.0% of one CPU core**, which is what a single-process Python event loop should be bound by. **(L-2)** `ConnectionManager.broadcast()` iterates `self.active` directly while awaiting `ws.send_text` inside the loop; at 200 sockets with 14,057 open/close cycles it raised `RuntimeError: Set changed size during iteration`, **silently dropping a market broadcast to every client past the mutation point** and skipping the event-bus publish after it. One line; the sibling `broadcast_to_channel` already iterates a copy. **(L-3)** `verify_password` (bcrypt cost 12, **234 ms**) runs **synchronously on the event loop**, pinning login at **~4/s at any concurrency** — and the proof is not the login numbers but `/refresh` and `/logout`, which do no bcrypt and have a 3–4 ms floor yet reach **1,670 ms / 1,430 ms** medians at 25 users because they are queued behind it. **This corrects PH3.4 §13's "no synchronous blocking operation in an async request path" — a single-request measurement cannot see a queue.** **What held:** provider failure fully contained (market mock at 30% errors / 10% timeouts / +800 ms, AI mock at 6 s + 20% 429 → **zero 5xx and zero timeouts in every phase**, and AI degradation did not contaminate the rest — `api` p95 30.5 ms while `ai` p95 sat at the injected 6,152 ms); rate limiting exact at its boundary (120 served, then 429 with `Retry-After` on 100% of rejections, **0 of 39** bystanders affected); the 60 s quote cache collapsed **7,044 quote-enriched requests into 583 upstream fetches (91.7%)** with **no thundering herd at TTL expiry**, answering PH3.4's flagged "most likely load finding" in the negative; 150 sockets held 75 s with zero errors and a 2 ms ping→pong p95. **Capacity, stated with its constraint:** safe sustained read throughput **~100 rps** on the shipped Redis pool, **~300 rps** with `REDIS_MAX_CONNECTIONS≈100`, hard ceiling ~410 rps CPU-bound on one worker — and **login separately pinned at ~4/s per worker**, the number to plan a launch spike around. **Explicitly not a claim that the product supports *N* users:** converting rps to users needs a per-user request rate only production telemetry can supply. **One application change and it is inert by default** — `yahoo_origin()` reads `MARKET_DATA_YAHOO_BASE` at call time and returns byte-identical URLs when unset; all seven Yahoo call sites route through it; the AI side needed no change at all (the SDK reads `ANTHROPIC_BASE_URL`). A harness monkeypatch was rejected because it would exercise a code path production does not have. **12 tests pin both halves — inert by default *and* actually effective when set**, since a working provider and a working mock produce the same green result. **Verification did not trust either mechanism:** every outbound TCP connection during a run was enumerated and all were loopback. **Nothing in the findings table was fixed** — changing code mid-sprint invalidates every measurement taken before it. Also found: **L-6, there is no Redis-backed rate-limit store, only Mongo** (this roadmap and PH3.4 §21.5 both imply otherwise), **S-1** `X-Forwarded-For` honoured with no trusted-proxy check so the anonymous tier is bypassable, and **L-4** multi-worker scaling untested. **No security control was weakened to obtain any number:** rate limiting stayed on even for the saturation search where disabling it would have raised the ceiling, no XFF spoofing was used even though it would have worked, bcrypt cost 12 was not lowered. **Two of the sprint's own results were wrong before they were right:** a 4.4% "error rate" that was the risk engine correctly refusing over-drawn paper orders, and 83 CSRF failures that were the harness reusing a token the server had **correctly rotated** on refresh — neither reported as a defect. **No load test was added to PR CI, deliberately:** a latency threshold on a shared runner goes red when the runner is busy and green on a fast runner that just regressed. **No regression:** backend 2,176 → **2,188 passed**, PH1 security **452 unchanged**, frontend **319 passed**, build green, bundle unchanged within noise.
- **Objective:** Targets verified with numbers, not vibes.
- **Scope:** k6 (or Locust) profiles: auth, dashboard aggregate, market quotes, scanner, Socket.IO fan-out under simulated market burst; Lighthouse CI for frontend (dashboard < 2 s target); bundle-size budget check; baseline report; fix only egregious regressions (> 2× target) — tuning beyond that is post-launch.
- **Deliverables:** Load scripts; Lighthouse CI config; baseline report; budgets in CI (advisory → blocking post-launch).
- **Files Expected:** `tests/load/*.js`, `.github/workflows/perf.yml`, `docs/perf/BASELINE_2026-07.md`.
- **Dependencies:** PH2.12 (staging), PH3.6 recommended first (stable module layout).
- **Acceptance Criteria:** Baselines recorded for every profile; API p95 < 500 ms and dashboard < 2 s on staging, or gaps ticketed with owner.
- **Validation Steps:** Repeat runs within 10% variance (methodology sanity).
- **Rollback Plan:** N/A (measurement).
- **Estimated Difficulty:** Medium. **Estimated Time:** 2 days.
- **Success Metrics:** Performance category re-score with evidence; capacity number (concurrent users at SLA) known.

## PH3.7b — Memory & Resource Stability

> **A new roadmap item, not a re-numbering.** This roadmap never carried a
> memory/resource sprint. It exists because PH3.7's load-testing half explicitly
> handed one forward (`docs/performance/PH3.5_LOAD_TEST_CERTIFICATION.md` §25),
> and it was commissioned under the sprint label **"PH3.6 — Memory & Resource
> Stability"**. This roadmap's own **PH3.6 is *Backend Decomposition (server.py →
> Routers)*, which remains NOT STARTED and was not touched.**

- **Status (2026-08-15): ✅ COMPLETE — PASS WITH CONDITIONS.** Report: **`docs/performance/PH3_MEMORY_STABILITY.md`**.
- **Objective:** Determine whether the platform can run continuously without memory growth, resource leaks, connection leaks, background-task leaks, unbounded caches or duplicated listeners — and whether usage *returns toward baseline* after activity stops, rather than merely staying under a number once.
- **Scope:** Backend and frontend resource lifecycle: WebSocket tier, background loops, scheduler, event bus, every cache, MongoDB/Redis/HTTP connection management, broker streams, React timers/listeners/observers/animations, and the Zustand store. Explicitly **not** in scope: trading logic, AI decision logic, prompts, model selection, API contracts, the design system, or this roadmap's PH3.6.
- **Delivered — the premise had to be rejected first.** PH3.7's load half advised starting from *"no leak is visible at these durations"*. That was correct about its own data and wrong as a conclusion: **RSS is the wrong instrument for the leaks this application actually has.** Both P0 findings are dicts that gain a few hundred bytes per event — less than the noise between two idle RSS samples — so PH3.7's flat memory curve was accurate *and structurally incapable* of showing either. **A leak is a shape, a count that only ever rises, not a size.** Counting entries instead of bytes found in the first hour what >150,000 requests of throughput testing could not.
- **Delivered — findings.** **M-1 (P0):** `ConnectionManager.user_connections` retained a dict key per connection forever, in both the clean path (`disconnect`) and the dropped-connection path (`_reap`) — and the key is `websocket.query_params["user_id"]`, which **nothing authenticates** (S-2, tracked to PH1.9), so an anonymous caller can mint one per connection. Measured: **1,000 clean cycles → 1,000 empty sets; 500 dirty disconnects → 500**; only a process restart ever emptied it. **M-2 (P0):** `ai_context_builder._cache` checked its 8-second TTL on read and evicted nothing, retaining a multi-KB `ChatContext` — rendered markdown *plus* the structured sections behind it — per user for the process's life. Measured: **5,000 users, every entry 999 s stale, 5,000 live entries**, none reachable again. **M-3 (P1)** confirms and fixes PH3.7's **L-2**: `broadcast()` iterated the live socket set across an `await`, reproduced as `RuntimeError: Set changed size during iteration` — and the exception is the *lucky* outcome; the unlucky one is every socket past the mutation point silently missing the message and the event-bus publish after the loop never running. `send_to_user()` had the same bug. **M-4 (P2):** four perpetual loops (`market_broadcast_loop`, `ai_monitoring_loop`, both heartbeat loops) were started with bare `asyncio.create_task` and the result discarded — no strong reference (asyncio keeps only a weak one) and, more consequentially, **no cancellation path**, so `shutdown()` closed the scheduler, broker streams, Redis, the HTTP pool and the Mongo client while all four kept running against them; both heartbeat loops read Mongo, so every clean stop emitted a burst of connection errors indistinguishable in the logs from a crash. **M-8:** MongoDB `maxIdleTimeMS` was unset — pooled connections are never reaped when idle, a pool that only ratchets up — closing the gap left by PH2.8's data-tier scope being displaced to PH2.8b. Also **M-5** (two unbounded per-user throttle maps), **M-6** (`BrokerStreamManager` retaining a finished stream *and the expired broker access token inside it*), **M-7** (`start_event_bridge` registering the catch-all `"*"` handler unconditionally — a second call would double every event forever), and frontend **F-1/F-2/F-3**: `tradeLive.byId` merged onto the previous map although every producer publishes the user's *complete* open set, so a closed trade was retained forever **and displayed as open**; unbounded multi-KB AI trade reviews; and an `aiRuns` cap that never evicts an `active` run, so a socket dropping mid-run defeats the cap permanently.
- **Delivered — what was checked and found correct, which is part of the result.** `infrastructure/redis_client.py` and `redis_pubsub.py` have **no defect** and are recorded as the reference the rest of the backend should look like (one client construction site, a breaker that re-tests rather than latches, Pub/Sub on a dedicated connection, backoff with jitter, exactly one subscriber per channel, individually guarded teardown). Every Mongo cursor uses an explicit `to_list(N)` — no `to_list(None)` anywhere. Metric cardinality is route-templated with a series ceiling and an overflow series. The log queue is bounded with a drop counter. And the **entire frontend timer / listener / observer / GSAP surface is clean**: 13 `setInterval` sites, 6 `addEventListener` sites, one `ResizeObserver`, every GSAP context — all with matching cleanup — plus a `RealtimeProvider` that assigns socket handlers as properties rather than adding listeners, so a reconnect **cannot** accumulate them.
- **Delivered — instruments and observability.** `backend/infrastructure/tasks.py` (new) supervises every perpetual loop: strong reference released on completion, one task per name with a refused coroutine **closed** rather than leaked, bounded 5 s cancellation, crashed tasks logged with tracebacks. Shutdown now cancels producers **before** their dependencies. **Six new gauges** (`websocket_tracked_users`, `websocket_connections`, `websocket_channel_subscriptions`, `background_tasks_running`, `event_bus_subscribers`, `app_cache_entries{cache=…}`) make the bounds observable — both P0 leaks grew for a whole process lifetime without appearing on any dashboard. **The alert worth writing first** is `websocket_tracked_users` holding a floor above zero while `websocket_connections` is at zero: M-1's exact signature. Two re-runnable tools: `backend/scripts/resource_probe.py` (in-process; exits non-zero on a retained structure or a cache over its ceiling) and `scripts/load/soak.sh` (samples `/api/metrics` every 30 s for the **whole** run plus an idle settle window — a before/after pair, which is all PH3.7's harness took, cannot distinguish "grew and came back" from "never grew").
- **Delivered — verification discipline.** **The caches sitting exactly at their ceilings in the baseline is the result, not a warning:** each was driven at 3–10× its bound, and landing *at* it rather than one entry past is the only evidence the eviction path executes — a constant in the source proves nothing, which is the same class of claim as PH2.12's stub that agreed with a bug. **Every regression test was verified to fail on the old code:** run against the pre-sprint tree, **18 of 26 failed**; the 8 that passed are the 6 covering the new task registry (no old behaviour to fail against) and 2 deliberate counter-tests asserting *preserved* behaviour, without which deleting `_stamp`'s body entirely would satisfy every ceiling assertion perfectly. **One of the sprint's own measurements was wrong before it was right and is documented:** the first soak reported samples on schedule for six minutes while k6 never ran, because `pid="$(start_sampler …)"` blocks until the backgrounded subshell closes stdout — it was measuring an idle server.
- **Deliberately not fixed, with reasons.** **`socketTimeoutMS` remains unset:** no read timeout means a query against a wedged primary holds its request and connection forever, but choosing the number requires the slowest legitimate query on production hardware, and one picked from a laptop starts aborting real work under load — wired to `MONGO_SOCKET_TIMEOUT_MS` and carried as an open risk. **PH3.7's L-1 (Redis pool size), L-3 (bcrypt on the event loop), L-5, L-6 and S-1 keep their existing owners** — changing a security control or a deployment default inside a memory sprint is how a sprint stops being reviewable. The event-bus log's per-publish list slice and two GSAP tweens that are not killed on unmount are both bounded and correct; optimising them would be speculative.
- **No regression:** backend **2,188 → 2,216 passed** (6 xfail unchanged), PH1 security **452 unchanged**, frontend **319 → 324 passed**, production build green. No trading logic, AI decision logic, prompt, model selection, API contract or design-system change.
- **Conditions (all environmental, none a defect):** `MONGO_SOCKET_TIMEOUT_MS` to be baselined in staging; multi-worker resource behaviour unmeasured, so the documented resource budget is **per worker** and must be multiplied; multi-day continuous operation unmeasured; Mongo TTL reaping of `sessions`/`rate_limits` under sustained write rate unmeasured; frontend bounds asserted structurally rather than heap-profiled.

## PH3.8 — Accessibility & Responsive Audit

- **Objective:** TESTING.md device/a11y matrix actually executed.
- **Scope:** Automated: jest-axe on key pages + Lighthouse a11y ≥ 90; manual: keyboard-only pass of auth/dashboard/trade flows, screen-reader spot check, color-contrast check in both themes (UI_GUIDELINES.md consistency rule); responsive matrix: desktop/tablet/mobile on dashboard, charts, admin, settings; fix critical findings, ticket the rest.
- **Deliverables:** a11y test suite; audit report with findings ledger; critical fixes.
- **Files Expected:** `frontend/src/**/__tests__/a11y.test.jsx`, `docs/qa/A11Y_RESPONSIVE_AUDIT.md`, targeted component fixes.
- **Dependencies:** PH3.3.
- **Acceptance Criteria:** Zero critical a11y violations on key pages; no horizontal-scroll/overlap breakage at 375px/768px/1440px on audited pages.
- **Validation Steps:** jest-axe in CI; manual matrix executed and recorded.
- **Rollback Plan:** Fixes are scoped CSS/markup changes, individually revertible.
- **Estimated Difficulty:** Medium. **Estimated Time:** 2 days.
- **Success Metrics:** Accessibility partial-pass → pass in the readiness scorecard.

## PH3.9 — End-to-End Critical Journeys

- **Objective:** The flows that make or lose users verified browser-to-database.
- **Scope:** Playwright against the prod compose stack: register (with email verification via Mailhog) → login → dashboard live data → add to watchlist → paper trade lifecycle → trade monitor → logout; admin journey: login → user management → audit log; failure journeys: wrong password, expired session refresh, rate-limit 429 UX; wire as CI nightly + pre-release gate.
- **Deliverables:** Playwright suite; CI job; flake policy (retry once, quarantine list).
- **Files Expected:** `e2e/playwright.config.ts`, `e2e/tests/*.spec.ts`, `.github/workflows/e2e.yml`.
- **Dependencies:** PH2.3 (stack), PH1.5 (verification flow), PH3.3.
- **Acceptance Criteria:** All journeys green against the prod stack 3 consecutive runs; suite < 15 min.
- **Validation Steps:** Triple run locally + CI.
- **Rollback Plan:** Additive; nightly (not per-PR) if runtime too heavy.
- **Estimated Difficulty:** High. **Estimated Time:** 3 days.
- **Success Metrics:** Zero manual-only critical paths at launch.

## PH3.10 — Documentation Synchronization

- **Status (2026-08-17): ⚠️ NOT this sprint — label drift, see below.** The sprint executed under the label "PH3.10" was **"PH3.10 — Final Production Audit"**, a full 35-category production-readiness audit, not this documentation-synchronization sprint. Report: **`docs/production/PH3.10_FINAL_PRODUCTION_AUDIT.md`**. Same brief-label drift already recorded for PH3.2–PH3.9. **This documentation-synchronization scope remains OPEN and unscheduled**, and should be re-numbered or folded into PH3.12. Note that the audit *did* correct one high-consequence documentation defect in passing (replica-scaling guidance across four files, §21 of that report), but the DEPLOYMENT.md / INDEX.md / ADR reconciliation below is untouched.
- **Audit outcome (for planning):** **GO TO PH3.11** on seven conditions. Matrix: 24 PASS · 8 PASS WITH CONDITIONS · 3 BLOCKED · 0 FAIL. **Two P0s found and fixed:** (1) `/api/ws` had **no authentication** — it bound per-user event delivery to an unauthenticated `user_id` query parameter, so any anonymous caller could read any account's private realtime stream (notifications, portfolio, trade-engine, broker orders); reproduced live against the production container, fixed, re-verified live, 17 regression tests confirmed non-vacuous against pre-fix code. Carried since PH1.9 as "S-2"; PH1 scoped WebSocket authz out and PH3.3's 201-route sweep could not see it because it iterates `APIRoute` and a WebSocket is a `WebSocketRoute`. (2) **The frontend had not built since 2026-08-03** (commit `930432d` added an ESLint config extending `react-app` without the `eslint-config-react-app` dependency) — no deployable artifact existed for fourteen days. **Root cause shared by both: no CI job had ever built or tested the frontend**, so 395 passing tests and a broken build both reported green; `frontend-ci.yml` added. **Three P1s also fixed:** administrative `blocked` was written and audited but never read on any auth path (blocking a user did nothing); a `react-router` open-redirect advisory in the shipped bundle; and deployment guidance in four files telling operators to "scale with replicas", which would run a second scheduler and place **duplicate real broker exit orders**. **Standing constraint: exactly one backend process** (no scheduler leader election). Backend **2,534 → 2,559** tests, frontend **395** unchanged, build green, container boots healthy in 0.68 s and shuts down clean. Still BLOCKED (unbuilt, not broken): email delivery, off-host backup, host-loss DR.
- **Objective:** Documentation describes the system that exists (finding M16/R-13).
- **Scope:** Rewrite DEPLOYMENT.md technology sections to FastAPI + Python / CRA-craco JS reality (or record an ADR-027-referenced migration intent — the doc must state which); reconcile ADR-002 with an addendum; fix INDEX.md file-name drift (`TASKS.md`→`TASK.md`, `PRODUCT_REQUIREMENTS.md`→`PRODUCT_REQUIREMENT.md`, `PROMPTS.md`→`PROMPT.md`); bring `CHANGELOG.md` (created in doc v1.2) fully up to date; verify every env var, endpoint list, and diagram against code; update API_REFERENCE.md gaps found during PH3.5; produce the Documentation Synchronization Report.
- **Deliverables:** Corrected docs; `CHANGELOG.md`; sync report.
- **Files Expected:** `.claude/DEPLOYMENT.md`, `.claude/INDEX.md`, `.claude/DECISIONS.md`, `.claude/CHANGELOG.md`, `.claude/API_REFERENCE.md`, `docs/qa/DOC_SYNC_REPORT.md`.
- **Dependencies:** After PH1/PH2 (documents their outcomes); before PH3.12.
- **Acceptance Criteria:** A new engineer following DEPLOYMENT.md alone can build and boot the prod stack; zero dangling cross-references in `.claude/` (link check).
- **Validation Steps:** Fresh-eyes walkthrough (someone other than the author follows the docs); scripted reference check.
- **Rollback Plan:** Docs-only; revert freely.
- **Estimated Difficulty:** Medium. **Estimated Time:** 2 days.
- **Success Metrics:** Documentation accuracy score 5.0 → ≥ 9.0.

## PH3.11 — Regression & Release Test Protocol

- **Objective:** Repeatable pre-release verification, written down.
- **Scope:** Release Test Protocol document: which automated suites gate (hermetic, integration, frontend, E2E, a11y), the manual checklist (device matrix, empty/error states, both themes), entry/exit criteria, sign-off template; execute the full protocol once against staging as a rehearsal; fold protocol into TESTING.md.
- **Deliverables:** Protocol doc; rehearsal execution record.
- **Files Expected:** `docs/qa/RELEASE_TEST_PROTOCOL.md`, `.claude/TESTING.md`.
- **Dependencies:** PH3.1–PH3.9.
- **Acceptance Criteria:** Protocol executed end-to-end in ≤ 1 day; every step has an owner and a pass/fail record.
- **Validation Steps:** The rehearsal is the validation.
- **Rollback Plan:** N/A (process artifact).
- **Estimated Difficulty:** Low. **Estimated Time:** 1 day.
- **Success Metrics:** Release verification no longer depends on memory.
- **Status (2026-08-17): ✅ COMPLETE — READY FOR PH3.12 CERTIFICATION.** Reports: **`docs/production/PH3.11_RELEASE_CANDIDATE_REPORT.md`** (regression pass, 30 sections) and **`docs/production/PH3.11_REMEDIATION_REPORT.md`** (blocker closure, 10 sections). **Deliverable `docs/qa/RELEASE_TEST_PROTOCOL.md` now exists**, written from the protocol that was actually executed — the documentation drift noted below is resolved. **Scope note:** the brief executed was a *regression and release-candidate verification* sprint rather than the protocol-document sprint planned above — the same brief-label drift recorded for PH3.2–PH3.10 — so the protocol document was written from the rehearsal rather than the rehearsal from the document. Nothing in it is aspirational; every step has been run at least once, and steps that could not be executed in this environment say so.
- **Remediation pass (2026-08-17): blocker B-1 CLOSED by fixing, not suppressing.** **All 6 Python advisories fixed** — `aiohttp` 3.14.1 → **3.14.3**, `cryptography` 48.0.1 → **50.0.0**. The two-major cryptography jump was taken on evidence: no dependent caps it (every constraint is a lower bound), the application's entire surface is one `from cryptography.fernet import Fernet` import, and **a Fernet token encrypted under 48.0.0 decrypts correctly under 50.0.0** — verified directly, because existing broker tokens in a production database must remain readable. **7 of 18 npm advisories fixed** by patch-level `overrides` (brace-expansion 1.1.18, fast-uri 3.1.5, js-yaml 4.3.1, nanoid 3.3.18, underscore 1.13.8) plus the direct `postcss` devDependency 8.4.49 → 8.5.26: **18 high → 11**. `resolutions` is declared alongside `overrides` because `package.json` names yarn as its `packageManager` while CI uses npm, and **yarn 1 ignores `overrides` entirely** — a fix that silently fails to apply under the declared tool would be worse than none. **8 dead suppressions deleted** (`litellm` ×7, `ecdsa` ×1 — packages already removed from `requirements.txt`, suppressing nothing while the CI summary still listed them as pending).
- **A third defect surfaced during remediation, previously unrecorded:** the dev-requirements audit step ran with **no suppressions** over a file whose line 17 is `-r requirements.txt`, so it re-audited the entire runtime set and failed on the very advisories the runtime step deliberately accepted. Its comment asserted "dev tooling never reaches production" — false for a file that transitively includes every runtime package. **That job could never have passed since the suppression policy was written.**
- **New enforcement:** `.github/dependency-triage.yml` (machine-readable register, both ecosystems: package, advisory, severity, reason, reachability, re-runnable evidence, mitigation, owner, expiry) enforced by `.github/scripts/dependency_audit.py`. Untriaged finding → fail; expired entry → fail with **no grace period**; **entry matching nothing → fail**, which is the check that would have caught the litellm/ecdsa rot; auditor unusable → **exit 2**, deliberately distinct from both pass and policy failure because "the check could not be performed" must never read as "the check passed". A `not-reachable` entry missing its `evidence` field is rejected outright, so the stronger claim cannot be made without proof.
- **The 2026-08-22 expiry was re-argued, not extended.** The blanket "pinned by fastapi" justification covering all 7 starlette advisories split **5/2** on re-triage: five are **structurally unreachable** (no form or multipart parsing anywhere, no `HTTPEndpoint`, no `StaticFiles`, and PYSEC-2026-2281 is a Windows-only defect on a Linux image) → **2027-02-15**; two (PYSEC-2026-161/248) got a ***shorter* leash than before — 2026-11-15** — because the app reads only `request.url.path` and never the reconstructed absolute URL, making them unreachable by convention rather than by control. The npm build-chain entries expire **2026-11-15**, tied to the CRA migration decision.
- **The gate is itself tested — 8 negative tests, every mutation reverted and the revert verified:** expired entry fails · the expiry date itself still passes · the day after fails · the 30-day warning fires without failing · a deleted entry raises `UNTRIAGED` · a bogus entry raises `STALE` · downgrading `aiohttp` raises 3 × `UNTRIAGED` · removing an npm override raises `UNTRIAGED`.
- **Full protocol re-run after remediation, every check reproducing baseline:** 2,559 backend (0 failed, 4 xfailed), 452 security, 395 frontend / 22 suites, build exit 0 with 48 bundles / 14 MB, routes **97/29/75 = 201** unchanged, analytics **0 MOCK** of 53, trading-engine mutation check fails-then-reverts-clean, WebSocket P0 matrix fully closed, Redis loss → API still serving, Mongo loss → readiness 503 with liveness 200 and no leakage, **0 restarts**, shutdown **2 s exit 0**, **0** secrets in logs, image rebuilt `--no-cache --pull` (425 MB) with the compiled cryptography wheel and Fernet verified **inside the container**. One incidental confirmation: the first live boot **failed closed** on a 20-character `METRICS_TOKEN` against a 32-character minimum — the config gate working, not a regression. **No application code was changed in either pass.**
- **RC baseline:** commit `32437e8` on `main`, **working tree clean before and after — no code was changed**, because **zero code regressions were found**. Python 3.11.15 (host) / 3.11-slim (image), Node v23.11.0, Docker 29.4.0, Compose v5.1.1, `mongo:7.0`, `redis:7.2-alpine`.
- **Regression result — identical to PH3.10 on every axis:** **2,559 backend passed / 0 failed / 4 xfailed / 95 deselected** (2,658 collected, 187.63s), **452** security-marked, **395 frontend / 22 suites**, production build exit 0 with the same **48 bundles / 14 MB**, and **97 protected / 29 admin / 75 public = 201** routes by the same dependency-graph classifier. Nothing skipped, disabled, weakened or newly xfailed. Every delta explained: Python 3.11.16→3.11.15 is the *host* interpreter (the image is unchanged); `server.py` 6,954→6,998 because PH3.10 measured mid-sprint and its own commit added +163 lines; Mongo 20/62→19/61 collections/indexes because `ensure_indexes()` declares **42** across **18** collections and 42 + 19 implicit `_id_` = 61 — **the declared set is identical**, the extra collection was created lazily by PH3.10's own traffic.
- **Verified live, against a from-scratch `--no-cache --pull` production image** (424 MB, non-root uid 10001, pip absent, no `.env` baked, no `--reload`) booting in **0.534 s** with `APP_ENV=production` against authenticated Mongo and Redis. **PH3.10's P0 holds under re-attack:** anonymous, spoofed `?user_id=<victim>`, query-string token and forged-subprotocol handshakes all **403**; only cookie or `Sec-WebSocket-Protocol: stockassist.auth,<token>` connects; a valid token plus spoofed `user_id` binds to the **token's** subject. Two users on concurrent sockets saw **zero** foreign identifiers. Session security total — rotation, replay → 401 + **family revoked**, logout-all revoking 6 sessions with all three refreshes 401 **including the caller's**. Live: JWT exactly **900 s**, cookies HttpOnly+Secure, CSRF 403 on missing *and* forged, no ACAO for a disallowed origin, all 6 security headers, `401×5 → 429×3`, OpenAPI **404**, and **zero** configured secrets in container logs measured against the real values in use.
- **Fault injection — controlled degradation every time, 0 process restarts:** Redis loss keeps the API serving with readiness reporting `redis: fail, critical:false`; Mongo loss flips readiness to **503** (`critical:true`) while `/health/live` stays 200 and the body is a bare `Internal Server Error` with no leakage; both recover automatically. `docker stop --timeout 30` → **1 s, exit 0**, readiness draining *first*, all **4** background tasks stopped, Redis pub/sub, Redis client, HTTP pools and the Mongo client closed. Churn of **60 sockets** returned every gauge to zero.
- **The chartered stale test was proven, not assumed.** `test_run_cycle_trails_and_books_targets` keeps an **exact-equality** assertion (the repair added `closed_trades` to the *expectation*, it did not relax the comparison), is backed by consequence checks (trailed SL 106.4, target booked, status still OPEN, both events, dedup on the second cycle), and is **mutation-checked**: injecting a spurious key into `run_cycle`'s return contract makes it FAIL. Mutation reverted; `git diff` clean.
- **🚫 BLOCKER B-1 (regression pass — ✅ CLOSED in the remediation pass above) — the `dependency-audit` CI workflow is red on both jobs, and no prior sprint ever ran it.** PH3.10 reported CI/CD "PASS WITH CONDITIONS" after adding `frontend-ci` but never executed the supply-chain gate. Backend `pip-audit` with CI's 15 `--ignore-vuln` suppressions **exits 1** on 6 advisories against pinned **runtime** deps — `cryptography` 48.0.1 (PYSEC-2026-3552/3553/3554) and `aiohttp` 3.14.1 (PYSEC-2026-3545/3546/3547) — all published after the suppression list was written. Frontend `npm audit --audit-level=high` **exits 1** on **18 high advisories with no triage mechanism at all**: the Python gate has a documented allowlist with a mechanical expiry, the npm gate was never given one, so it fails unconditionally with nothing an engineer can act on. **Reachability analysed rather than assumed** — `cryptography` is used only for Fernet (no `pkcs7`, no `x509.verification`, no `PolicyBuilder` anywhere), `aiohttp` is **client-only** (ruling out the server-side smuggling advisory), and **zero** of the 18 npm packages reach the shipped bundle (grep against `build/static/js/`; the `svgo` hits are minifier variable names). `npm audit --omit=dev` does not filter them only because `react-scripts` sits in `dependencies`, which is CRA's own layout. **So the product is not known to be vulnerable and a required gate is still red — two different claims, kept apart.** **Also: `SUPPRESSION_REVIEW_BY` expires 2026-08-22, five days out.**
- **Deliberately not fixed in-sprint.** Bumping `cryptography` across a major version or migrating off `react-scripts` is architectural work during a freeze, which the core rule forbids; suppressing the advisories to turn the gate green is the "mark a failure as passed" outcome the brief prohibits, and would retire an accepted-risk register days before it is due to be re-argued. **Remediation for approval: R-1** bump `aiohttp` 3.14.1 → 3.14.3 (patch-level, in-pin — `SECRETS.md` §8 records this exact class of fix being applied before); **R-2** evaluate `cryptography` 48 → 49/50 in a dedicated sprint with regression coverage; **R-3** give the npm gate the Python gate's triage mechanism, or schedule the CRA migration; **R-4** re-argue or extend the suppression expiry.
- **Five P3 observations, all pre-existing:** plain-HTTP `FRONTEND_URL` accepted in production (mitigated in practice — production forces `Secure` cookies, so a plaintext origin breaks the session rather than downgrading it silently); uvicorn's access log records caller-supplied query strings verbatim, so a client that sends `?token=` logs a live credential (the platform's own client never does — PH3.10 moved it to the subprotocol); a Redis **client-pool** exhaustion transient during the boot burst (server `maxclients` 10,000 with 7 connected) that self-heals in ~7 ms; Mongo outage surfacing as 500 rather than the semantically correct 503; and `source: yahoo_finance` disclosed in quote payloads, which `MARKET_DATA_ARCHITECTURE.md` forbids in *error* surfaces (§611) and is silent on for success payloads.
- **Method note.** Two apparent findings were artifacts of the probes, not the product: a logout-all test confounded by an unsaved rotated cookie (its 401 was replay detection) *and* a missing CSRF header, and a fail-closed matrix aimed at `validate_config()` when wildcard CORS is stripped in `cors.py`, `COOKIE_SECURE` is forced in `cookies.py`, and **debug mode has no code path to reject** — there is no `--reload`, no `app.debug`, no debug flag anywhere. Both looked like security defects. Re-running before reporting is what separated them from the real blocker.
- **Scorecard: 24 PASS · 5 PASS WITH CONDITIONS · 3 BLOCKED · 1 FAIL** (33 categories). All eight PH3.10 conditions (C-1 SMTP, C-2 dedicated secrets, C-3 `MONGO_SOCKET_TIMEOUT_MS`, C-4 alerting, C-5 off-host backup, C-6 single process, C-7 same-origin, C-8 one lockfile) remain open. **Limitations stated rather than assumed:** no staging environment (the smoke test ran locally in production mode and is *not* called production verification), no SMTP/OAuth/payment provider so those live paths are hermetic-only, no multi-day soak, no load run this sprint, no device matrix. **Post-remediation scorecard: 25 PASS · 5 PASS WITH CONDITIONS · 3 BLOCKED · 0 FAIL.** R-1, R-2, R-3 and R-4 all landed, `dependency-audit` is green, and every stop condition in the brief is met. The three BLOCKED categories (email verification, backups, disaster recovery) are unbuilt operational capabilities rather than defects — deployment prerequisites, not certification blockers. **Two register deadlines are now real calendar commitments: 2026-11-15 and 2027-02-15.**

## PH3.12 — Production Certification & Launch Readiness

- **Status (2026-08-17): ⛔ BLOCKED — NO-GO, PRODUCTION CERTIFICATION BLOCKED.** Report: **`docs/production/PH3.12_PRODUCTION_CERTIFICATION_NOGO_ARCHIVE.md`** (30 sections). **Deliverable naming note:** written as `docs/production/PH3.12_PRODUCTION_CERTIFICATION.md` rather than the `docs/qa/PH3_CERTIFICATION.md` planned below, keeping it alongside the PH3.10/PH3.11 production reports it continues; the go/no-go is recorded in that report's §30 rather than in a separate ADR. **No application code was changed** (tracked diff `b2f4921d…b32725`, identical before and after). **Every baseline reproduced:** 2,559 backend / 452 security / 395 frontend / 48 bundles / 201 routes (97-29-75) / 0 MOCK of 53 / 61 indexes; from-scratch image 425 MB booting in 0.242 s and shutting down in 2 s exit 0. **The PH3.11 dependency gate was re-proven rather than accepted** — exit 0, plus 7 negative tests confirming it fails on expiry, on an untriaged finding, on a stale entry, and returns a distinct **exit 2** when the auditor cannot run; register restored byte-identical after every mutation. **Live security holds:** WebSocket P0 matrix fully closed, refresh rotation + replay + family revocation, logout-all total, CSRF and rate limiting enforced, 0 secrets in 442 log lines, controlled degradation under Redis and Mongo loss with 0 restarts, no resource leak across 60 sockets. **Two blockers found, both controls no prior sprint actually probed.** **B-1:** `PaperTradeCreate` (`server.py:5125`) has **no input validation** — `quantity: -1000` is credited, moving a paper balance ₹86,840 → ₹1,086,840 in one request; negative prices and arbitrary trade types also accepted, while the canonical `TradeCreate` (`models.py:124`) enforces all of it and returns 422. Bounded to paper trading and the acting user's own data — no real money, no broker order, no authz crossed — but it falsifies paper P&L, the trade journal and per-user analytics. **B-2:** `/docs`, `/redoc` and `/openapi.json` return **200 anonymously in production** (188 paths, 23 admin routes, 26 schemas); **PH3.11 §9 certified this as 404**, evidence that was never true because it was probed at `/api/docs`. Authorization is fully intact and no secrets are exposed. **Neither was fixed** — the brief forbids silently repairing a newly-found blocker mid-certification. **Also: L-1 — the release candidate is an uncommitted working tree**, so it cannot be checked out, tagged or reproduced, and DR assumption A6 does not currently hold. **Scorecard: 15 PASS · 4 PASS WITH CONDITIONS · 2 BLOCKED · 3 NOT OPERATIONALLY VERIFIED** (payments, backup/DR, rollback — unbuilt operational capabilities, not defects). No composite score was issued: the ≥9.0 target below assumes a scoring rubric this certification deliberately replaced with per-category PASS / CONDITIONAL / BLOCKED / NOT OPERATIONALLY VERIFIED, because averaging hides a blocked category. **Path to GO:** fix B-1 and B-2, add regression tests that fail without each fix, commit and tag the candidate (L-1), then re-run §7, §10 and §29 only — the rest of the evidence is tied to a recorded tree hash verified unchanged.
- **Remediation status (2026-08-18): ✅ PH3.12R COMPLETE — BLOCKERS CLOSED, READY FOR A FRESH CERTIFICATION RERUN.** Report: **`docs/production/PH3.12_PRODUCTION_CERTIFICATION_NOGO_ARCHIVE.md`**, § *PH3.12R Blocker Remediation Addendum*. **The NO-GO above still stands** — this sprint fixed what blocked certification; only a rerun can lift it. **B-1 closed.** Root cause was **duplication, not a missing bound**: the trade-entry contract was written down twice — `TradeCreate` in `models.py` with every constraint, `PaperTradeCreate` inline in `server.py` ~5,000 lines away with none — and nothing linked them. The contract now exists **once**, as shared constrained types, with `PaperTradeCreate` moved into `models.py` directly beneath `TradeCreate`; validation is model-layer, so malformed input answers **422 before any balance, position, trade or P&L write is reachable**, and `execute_paper_trade` re-validates against the *same model* for non-HTTP callers. A **second latent instance of the same class** was found and closed: `json.loads` accepts `Infinity`, and a plain `gt=0` float admits it, so `entry_price: Infinity` passed every bound on the **real** trade endpoint too (`allow_inf_nan=False`). **B-2 closed** by new `backend/security/api_docs.py`: production → `/docs`, `/redoc`, `/openapi.json` all **404**, every other environment → **200**, switched **together from one function** so the partial fix (Swagger hidden, schema still served) is structurally impossible, with **no variable able to enable docs in production**. **L-1 resolved** — the RC is a commit, not a working tree. **184 regression tests added, all falsifiable and measured against pre-fix code:** B-1 **94 failed / 38 passed** pre-fix; B-2 **5 failed** on the pre-fix line and **7 failed** on the half-fix. The decisive B-2 class boots the **real `server` module in a clean interpreter as `APP_ENV=production`**, because under the suite's own `testing` environment a regressed constructor is *indistinguishable from the fix*. **Battery green:** 2,743 backend (2,559 baseline + 184) / 452 security unchanged / 395 frontend / build exit 0 / dependency audit exit 0; route inventory **193 dev → 189 production**, removing exactly the four documentation routes and nothing else, **188 OpenAPI paths** matching §7. **Remaining: none from B-1, B-2 or L-1.** The eight PH3.10 conditions (C-1…C-8) and the three **NOT OPERATIONALLY VERIFIED** categories — payments, backup/off-host DR, rollback — are unchanged and untouched; they are unbuilt operational capabilities rather than defects, and they are why this sprint does not convert §30 into a GO.
- **Certification RERUN status (2026-08-18): ✅ COMPLETE — GO (CONDITIONAL).** Report: **`docs/production/PH3.12_PRODUCTION_CERTIFICATION.md`** (30 sections, rewritten). Independent re-certification of committed RC **`a4ee79f`** on `main` with a **clean working tree**, against a from-scratch `--no-cache --pull` image (`stockassist-rc:ph312-cert`, 425 MB, `sha256:42d12ddf…abf8ce`). **No application code changed.** Baselines reproduced exactly: backend **2,743**, security **452**, frontend **395**, build exit 0 (62 warnings = PH3.10 baseline), dependency gate exit 0, routes **97/29/75 = 201** HTTP + 1 WS, analytics **0 MOCK** of 53. **B-1, B-2 and L-1 all independently CLOSED with falsifiable probes** — B-1: 15/15 hostile payloads → 422 with the paper balance byte-for-byte unchanged, re-verified with an open position, valid BUY/SELL/close arithmetic exact; B-2: `/docs` `/redoc` `/openapi.json` → 404 with **0 documentation routes registered**, proven falsifiable because the *same image* at `APP_ENV=development` serves all three 200 with 188 paths; L-1: **0 content mismatches** across 117 image files vs committed blobs. PH1 — 17 controls re-probed live, none regressed. WebSocket — 4/4 attacks rejected, 2/2 legitimate connections. Infra — Redis loss degrades with 0 restarts, Mongo loss holds liveness 200 / readiness 503, both auto-recover. Dependency gate proven to bite 3 ways (expiry, stale entry, missing auditor → distinct exit 2). Secrets — no leaks in image, 0 secrets in live logs, production config **fails closed** (3 refused starts). Backup+restore drill executed: **19 collections matched exactly**. **Two NEW findings, neither security nor financial:** **C-1** — untracked git-ignored `backend/test-results/junit.xml` baked into the image (working-dir build **117** files vs clean `git archive` build **116**), so the image is not a pure function of the commit; **C-2** — exit **137** on shutdown after a Redis outage (4/4; baseline 0; `-t 60` → 0), an orchestrator-signalling defect, not a data-integrity one. **Decision: source at `a4ee79f` certified production ready; the image must be rebuilt after the one-line C-1 fix before deploy.** Payments (**not implemented**), Backup/DR (**no off-host target**) and Rollback (**no deployment ledger**) recorded as **NOT OPERATIONALLY VERIFIED** deployment prerequisites. No blocker repaired during certification; nothing deployed; PH3.13 not started.
- **Conditional remediation status (2026-08-18): ✅ PH3.12C COMPLETE — C-1 CLOSED, C-2 WITHDRAWN.** Report: **`docs/production/PH3.12_PRODUCTION_CERTIFICATION.md`** §31. **C-1** closed in `backend/.dockerignore` by excluding test *outputs* as well as inputs; established empirically first that a `.dockerignore` pattern is **root-anchored** (a bare `test-results/` still let `sub/test-results/junit.xml` through on Docker 29.4.0), so every rule ships both a bare and a `**/` form. Guard `backend/tests/test_build_context.py` (**44 tests**, hermetic) **proven able to fail — 26 failed against the pre-fix file**. Proof with the offending artifacts left on disk: working-dir build **116** files in `/app` vs clean `git archive` build **116**, **identical**, 0 mismatches, 0 image-only files (was 117). Image `stockassist-rc:ph312-c1fix`, `sha256:cdfcd0b3…a9af03`. **C-2 WITHDRAWN — the finding was wrong and no code was changed:** a 20-line control container with no application code reproduces exit **137 in 6/6** under bare `docker stop` and exit **0 in 3/3** under `docker stop -t 10/30/60`, i.e. this host's `docker stop` SIGKILLs at ~1.3s without an explicit `--timeout`; the app's teardown is 1.5–2.3s and straddles that window, and every 137 in the rerun came from a bare stop while every 0 came from an explicit `-t` — Redis was never the variable. Direct SIGTERM gives **exit 0 in 3/3** with complete ordered teardown and **0** asyncio leak warnings. Post-remediation: backend **2,787** (= 2,743 + 44 new), security **452**, targeted regression **379**, and B-1/B-2 re-verified live on the remediated image. Files changed: `backend/.dockerignore`, `backend/tests/test_build_context.py` — **no application module, dependency, workflow or config touched**. **Remaining condition: the remediation is UNCOMMITTED** — commit both files and rebuild from that commit before deploy. PH3.13 not started.
- **Closure-pass status (2026-08-19): ⛔ PH3.12F BLOCKED — C-1 CLOSED, C-2 WITHDRAWN, NEW BLOCKER C-3 OPEN.** Report: **`docs/production/PH3.12_PRODUCTION_CERTIFICATION.md`** §32. Release SHA **`6b53b3bcf99c400a0f623d5f4d280ffe87c47776`**, image **`sha256:9de7b850d09bc81ce1d61f49ba9682bed1850e2b25df9fcdcdf8310eb6bb2cc4`**, built `--no-cache --pull` from a clean `git archive` export. Against that image: **0 image-only files, 0 content mismatches** vs the commit, no test input or result artifact under `/app`, all production modules present. Backend **2,787**, security **452**, frontend **395** + build exit 0 (48 bundles / 14 MB), dependency gate **exit 0** and still falsifiable, route inventory **201 HTTP + 1 WS / 97-29-75 / 0 doc routes**, B-1 **15/15 → 422** with the balance byte-identical, B-2 **404 in production and 200 with 188 paths from the same image in development**. **C-3:** `__pycache__/` and `*.py[cod]` are root-anchored, so a working-directory build ships host bytecode carrying the developer's absolute paths in `co_filename` (228 files vs 223) — and the C-1 guard cannot see it, because it models Docker's matcher with `fnmatch`, whose `*` crosses `/`. Reported, not repaired. The §25 verdict is **not** upgraded to an unconditional GO while C-3 is open. Payments, backup/DR and rollback remain NOT OPERATIONALLY VERIFIED prerequisites. Nothing deployed; PH3.13 not started.
- **Objective:** Final gate: re-run the full production readiness audit and certify v1.0.
- **Scope:** Re-execute the Sprint 12 audit methodology end-to-end; re-score every category (target ≥ 9.0 composite, no category < 8.0); execute PRODUCTION_HARDENING.md §16 Launch Checklist and §17 certification matrix; write the Production Launch Readiness Report; go/no-go decision recorded in DECISIONS.md.
- **Deliverables:** `docs/qa/PH3_CERTIFICATION.md` (Launch Readiness Report); signed §17 matrix; go/no-go ADR.
- **Files Expected:** `docs/qa/PH3_CERTIFICATION.md`, `.claude/PRODUCTION_HARDENING.md`, `.claude/DECISIONS.md`, `.claude/CHANGELOG.md`.
- **Dependencies:** Everything. PH1.12 and PH2.12 sign-offs recorded.
- **Acceptance Criteria:** All §13 program acceptance criteria evidenced; composite ≥ 9.0; zero critical open risks without documented acceptance.
- **Validation Steps:** Independent reviewer walks the evidence pack.
- **Rollback Plan:** No-go reopens the failing sprint; launch date moves — quality does not.
- **Estimated Difficulty:** Medium. **Estimated Time:** 2 days.
- **Success Metrics:** StockAssist AI v1.0 certified for public launch.

---

# Implementation Sequencing

## Must complete before anything else (Week 1 critical path)

1. **PH1.1** — backdoor removal (sprint zero; nothing else matters while these exist)
2. **PH3.1** — test suite repair (parallel with PH1.1; restores trustworthy signal)
3. **PH1.3 + PH1.4** — cookies, CORS, headers
4. **PH2.5** — CI foundation (locks all fixes in place)

## Parallel tracks (after critical path start)

| Track | Sprints | Can run alongside |
|---|---|---|
| Security | PH1.2, PH1.5–PH1.9 (in order) | Infrastructure & Quality tracks |
| Infrastructure | PH2.1 → PH2.2 → PH2.3 → PH2.4 → PH2.6 → PH2.7 → PH2.8 → PH2.9 → PH2.10 → PH2.11 | Security track |
| Quality | PH3.2, PH3.3 → PH3.4, PH3.5 | Both other tracks |

## Serialization constraints

- PH1.6 → PH1.9, PH1.10 (token service reuse)
- PH1.8 → PH2.1, PH2.3 (config validator before images/compose)
- PH2.1–2.3 → PH2.6 → PH2.7 → PH2.12
- PH3.1 + PH3.5 → PH3.6 (tests lock the surface before decomposition); PH3.6 after PH1 completes (merge-conflict avoidance)
- PH2.12 → PH3.7 (staging needed for load tests)
- PH1.12, PH2.12 → PH3.12

## Blocks production (launch gates)

PH1.1–PH1.8, PH1.12 · PH2.1–PH2.8, PH2.12 · PH3.1–PH3.3, PH3.5, PH3.10, PH3.11, PH3.12

## Does not block production (complete before 10k-user milestone)

PH1.9 (strongly recommended pre-launch; hard gate for Closed Beta), PH1.10 MFA implementation, PH3.4 full coverage, PH3.6, PH3.7 beyond baseline, PH3.8 non-critical findings, PH3.9 full breadth (smoke journeys ARE a gate)

## Optional / Deferred (post-launch backlog)

Worker-tier extraction (OR-3) · Kubernetes / multi-region (DEPLOYMENT.md future) · Sentry SaaS vs self-hosted finalization · GraphQL (ADR-018 future) · licensed exchange feeds (ADR-026 tier 2) · Market Data Architecture implementation Phases 1–6 (TASK.md — resumes after certification)

## Dependency Graph

```
PH1.1 ──┬─▶ PH1.2 ─────────────────────────────┐
        ├─▶ PH1.3 ─▶ PH1.6 ─┬─▶ PH1.9 ─────────┤
        ├─▶ PH1.4 ──┐       └─▶ PH1.10 ────────┤
        ├─▶ PH1.5 ──┤                          ├─▶ PH1.12 ──┐
        └─▶ PH1.7 ──┤                          │            │
                    └─▶ PH1.8 ─▶ PH2.1 ─┐      │            │
PH1.11 ────────────────────────┐        │      │            │
              PH2.2 ───────────┼─▶ PH2.3 ─▶ PH2.4          │
                               │        │                   │
PH3.1 ─▶ PH2.5 ─▶ PH2.6 ◀──────┴────────┘                   │
                    │                                        │
                    ▼                                        ▼
                 PH2.7 ─▶ PH2.8 ─▶ PH2.9 ─▶ PH2.10 ─▶ PH2.11 ─▶ PH2.12
                                                                 │
PH3.2 (independent)                                              │
PH3.3 ─▶ PH3.4                                                   ▼
PH3.1 + PH3.5 ─▶ PH3.6          PH2.12 ─▶ PH3.7        PH3.8, PH3.9
                                                                 │
                 PH3.10 ─▶ PH3.11 ─▶ PH3.12 ◀────────────────────┘
```

## Estimated Program Duration

- Active engineering: ~48 working days single-threaded; **~5–6 calendar weeks with the three tracks parallelized** (plus the 7-day PH2.12 soak overlapping PH3 work).
- Hard launch blockers alone (critical path): ~2 weeks.

---

# Relationship to the Product Roadmap

ROADMAP.md Phases 1–9 remain the product roadmap. PH1–PH3 are inserted as a mandatory hardening interlude between the completed MVP (Phase 1 + Phase 2 releases) and any further product phase. ROADMAP.md v1.2 references this document; this document owns all sprint-level hardening detail.

---

# Version History

| Version | Date | Change |
|---|---|---|
| 1.2 | 2026-07-17 | Initial roadmap: PH1/PH2/PH3, 36 sprints, sequencing and dependency graph. Baseline: Sprint 12 Production Readiness Report. |

---

# End of Production Hardening Roadmap

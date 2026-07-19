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

## PH1.6 — JWT Lifecycle & Refresh Rotation

- **Architecture reference:** SECURITY_ARCHITECTURE.md §9 (Session Architecture), §11 (JWT Lifecycle), §12 (Refresh Token Lifecycle), §30 (Session Refresh Sequence).
- **Objective:** Token lifetimes and rotation per SECURITY.md.
- **Scope:** Access token 15 min; refresh token 30 days with rotation on every use, reuse detection (revoke family on replay), and a server-side revocation store (Redis); silent-refresh behavior verified in the frontend interceptor; sessions listing groundwork (device/IP capture).
- **Deliverables:** Token service; revocation store; migration note for existing sessions; tests including replay attack.
- **Files Expected:** `backend/security/tokens.py`, `backend/server.py`, `frontend/src/services/api.js` (interceptor), `backend/tests/test_token_rotation.py`.
- **Dependencies:** PH1.3.
- **Acceptance Criteria:** Access token expires in 15 min; reused refresh token → 401 + family revoked; users are not visibly logged out during normal use.
- **Validation Steps:** Time-travel tests (freezegun); manual soak on staging for one session lifetime.
- **Rollback Plan:** Lifetimes are env-configured; revert to previous values without code rollback if UX breaks, and record deviation as an ADR.
- **Estimated Difficulty:** High. **Estimated Time:** 2 days.
- **Success Metrics:** Risk R-06 closed; zero support reports of surprise logouts after one week on staging.

## PH1.7 — Rate Limiting & Brute-Force Protection

- **Architecture reference:** SECURITY_ARCHITECTURE.md §21 (Rate Limiting Strategy — fold the existing `login_attempts` lockout into the new limiter rather than running both in parallel).
- **Objective:** Tiered rate limiting per SECURITY.md (Guest 30/min → Elite 600/min) with strict auth-endpoint limits.
- **Scope:** ASGI rate limiter (slowapi or equivalent) backed by Redis; per-plan tiers resolved from the authenticated user; strict limits on `/api/auth/*` (e.g., 5 login attempts/min/IP + per-account lockout with backoff); 429 responses with `Retry-After`; frontend surfaces friendly retry messaging.
- **Deliverables:** Limiter middleware; tier configuration; lockout logic; tests.
- **Files Expected:** `backend/security/rate_limit.py`, `backend/server.py`, `backend/tests/test_rate_limiting.py`.
- **Dependencies:** PH1.1; Redis available (already in stack).
- **Acceptance Criteria:** Exceeding tier budget → 429; 6th login attempt in a minute → 429 regardless of credentials; limits configurable per env; health endpoints exempt.
- **Validation Steps:** Automated burst tests; k6 mini-run against staging.
- **Rollback Plan:** Limiter behind `RATE_LIMIT_ENABLED` (default true in production); disable only outside production.
- **Estimated Difficulty:** Medium. **Estimated Time:** 1.5 days.
- **Success Metrics:** Risk R-05 closed; credential-stuffing simulation blocked in staging test.

## PH1.8 — Secrets & Environment Hardening

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

## PH1.10 — Admin Hardening & Session Management

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

## PH2.1 — Backend Production Dockerfile

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

## PH3.3 — Frontend Test Foundation & Smoke Suite

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

## PH3.12 — Production Certification & Launch Readiness

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

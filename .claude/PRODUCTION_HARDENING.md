# StockAssist AI
## Production Hardening — Master Architecture Document

Version: 1.2

Status: PH1 In Progress — PH1.1 complete (2026-07-17): findings B1/B2 closed, risks R-01/R-02 closed; startup admin seeding (default password + plaintext credentials file) also removed under PH1.1. PH1.2 complete (2026-07-17): Google OAuth hardened — CSRF `state`, id_token verification, `email_verified` gate, redirect_uri allowlist, safe account linking; risk R-02 fully closed. PH1.3 complete (2026-07-18): authentication cookies production-hardened and centralized in `backend/security/cookies.py` — `Secure` forced in production, `HttpOnly`+`SameSite` on all cookies, matched-attribute clearing, unified OAuth-state cookie posture; finding B4 closed, risk R-04 closed. PH1.4 complete (2026-07-18): CORS production-hardened and centralized in `backend/security/cors.py` — wildcard-with-credentials default removed, environment-driven exact-match origin allowlist (`CORS_ALLOWED_ORIGINS`), restricted methods/headers, fail-closed in production; finding B3 closed, risk R-03 closed. Security headers de-scoped to PH1.4b. PH1.5 complete (2026-07-19): production password policy centralized in `backend/security/passwords.py` — model-layer 422 enforcement (12–64 chars, character classes, common/sequential/repeated/identity-derived rejection), explicit bcrypt cost 12, never-raising timing-equalized verification (fixed OAuth-account login 500), sanitized validation errors; finding H10 password-half closed, risk R-05 partially mitigated. Email scope (EmailStr/verification/reset, OR-6) split out to PH1.5b. PH1.5b/Identity Recovery complete (2026-07-22): email verification + password reset + password change centralized in `backend/security/recovery.py` — single-use signed recovery tokens (`<token_id>.<HMAC>` + authoritative `recovery_tokens` record, atomic burn), five new `/api/auth` recovery endpoints, `email_verified`/`email_verified_at`/`verified_by` on the user model, enumeration-safe generic responses, and full session invalidation (`revoke_all_for_user` + `password_changed_at`) on any credential rotation; finding H10 email-verification half closed (only `EmailStr` tightening remains); OR-6 (real SMTP provider) still open — flows run in provider-agnostic simulated mode until credentials are set; login is not yet gated on verification (backward-compatible). PH1.4b complete (2026-07-20): HTTP response security headers centralized in `backend/security/headers.py` and applied via a single pure-ASGI `SecurityHeadersMiddleware` — HSTS (HTTPS/production only), a strict nonce-capable CSP (`default-src 'none'`, no `unsafe-*`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, a locked-down `Permissions-Policy`, `Cross-Origin-Opener-Policy`/`Cross-Origin-Resource-Policy` (COEP opt-in), and the deprecated `X-XSS-Protection` neutralized; every value environment-overridable; 35 hermetic tests; "no security headers" gap closed. PH1.9 Secrets & Supply Chain complete (2026-07-22): configuration centralized in `backend/security/secrets.py` (authoritative `SECRET_REGISTRY` + boot-time fail-closed `validate_config`, run before the Mongo client; value-free logging), weak compose defaults removed (`JWT_SECRET` placeholder + hard-coded n8n password now required env vars), `requirements.txt` fully exact-pinned with 7 in-pin CVE patches, CI supply-chain workflow added (`pip-audit`/`pip check`/`npm audit`/`gitleaks`), committed `.env.example` templates (backend generated from the registry) + `.claude/SECRETS.md` rotation/incident runbook; 38 hermetic tests; finding B5 secret-fallback half + "no boot-time validation" gap closed (delivered the roadmap's PH1.8 plus the supply-chain core of PH1.11). PH1.10 Audit Logging & Security Monitoring complete (2026-07-22): centralized security-event logging in `backend/security/audit.py` — a closed event taxonomy across five categories (authentication/identity/session/security/administration) with per-event `category`+`severity` (info/notice/warning/critical, unknown → fail-safe security/warning), a versioned structured record schema (event, category, severity, outcome, actor/session/ip/user_agent/request_id/target, redacted details, timestamp), recursive secret redaction (a token/password/code/state/hash can never reach a sink), a pluggable `AuditSink` interface with a default composite of durable `MongoAuditSink` (`security_audit_logs`) + SIEM-ready `LoggingAuditSink`, and a fail-safe `AuditLogger` (emitting can never break a security flow); the prior scattered `log_auth_event` is now a thin backward-compatible facade over it, and the auth surface (login ±/registration/session created·revoked/logout·logout-all/refresh rotation/token-replay vs. invalid-refresh/invalid-JWT), the CSRF middleware (`csrf_validation_failure`), and the rate limiter (`rate_limit_triggered`) are instrumented; 20 hermetic tests (`backend/tests/test_audit.py`); "no centralized audit log / no structured security event model / limited security visibility" gap closed (took the PH1.10 slot — Admin Hardening & Session Management moves to PH1.10b). PH1.11 Dependency & Vulnerability Scanning complete (2026-07-22): finished in PH1.12/F-3 — `.github/dependabot.yml` (pip/npm/github-actions; docker staged), `requirements-dev.txt` split (M14), triage-SLA in SECRETS.md §7 + TESTING.md, CI audits both requirements files; risk R-14 closed. PH1.12 Security Certification complete (2026-07-22) — **PHASE 1 EXIT GATE PASSED**: F-1 privilege escalation closed (`backend/security/roles.py` — role allowlist + least-privilege assignment, wired into `admin_update_user`), F-2 unhandled ObjectId parsing closed (`backend/security/identifiers.py` — `parse_object_id` → clean 400 at every untrusted id boundary), F-3 supply-chain automation closed (see PH1.11); 48 new hermetic tests; security checklist executed; re-score Authentication & Authorization 2.0→9.0 and API & Transport Security 3.0→8.5 (both clear the ≥8.0 gate); `docs/security/PH1_CERTIFICATION.md` published; §17 Security row signed off. **Phase 1 (Production Security Hardening) is CERTIFIED COMPLETE; overall production deployment remains NO-GO pending PH2 (Infrastructure & DevOps) and PH3 (Quality Assurance).**

Date: 2026-07-17

Owner: Engineering (CTO)

Companion Documents: PRODUCTION_ROADMAP.md (sprint-level plan for PH1–PH3) · SECURITY_ARCHITECTURE.md (security engineering blueprint, authoritative for PH1 architecture detail)

Baseline Input: PRODUCTION_READINESS_REPORT.md (Sprint 12 audit, 2026-07-17)

---

# 1. Executive Summary

StockAssist AI has completed its MVP feature set: Phase 1 (Sprints 1–12) and Phase 2 (Releases R1–R9). The platform now enters a **feature freeze** and a dedicated **Production Hardening program** consisting of three phases:

- **PH1 — Production Security Hardening** (12 sprints)
- **PH2 — Production Infrastructure & DevOps** (12 sprints)
- **PH3 — Production Quality Assurance** (12 sprints)

The Sprint 12 Production Readiness Audit returned a verdict of **NOT READY FOR PRODUCTION**. The application core is healthy — 341 passing backend tests, clean production frontend build, sound secrets hygiene, bcrypt hashing, RBAC-guarded admin routes with immutable audit logging — but the system carries **two critical authentication backdoors**, wildcard CORS with credentialed requests, insecure cookies, broken Docker packaging, zero CI/CD, no rate limiting, no frontend tests, and fabricated data in the admin console.

None of these findings require product redesign. All are hardening work. The estimated effort to clear the hard launch blockers alone is 3–5 focused engineering days; the full three-phase program brings the platform to a certifiable Version 1.0.

**No new product features may be merged until PH1 and PH2 exit criteria are met.** This is the single most important governance rule of this program.

---

# 2. Current System Status

## Actual Deployed Stack (as-built, verified 2026-07-17)

| Layer | Documented (DEPLOYMENT.md / ADRs) | Actual |
|---|---|---|
| Backend | Node.js + Express + TypeScript | **Python + FastAPI** (`backend/server.py`, 4,823 lines) |
| Frontend | React + TypeScript + Vite | **React + JavaScript + CRA/craco** |
| Database | MongoDB Atlas | MongoDB (Motor async driver) — consistent |
| Cache / Events | Redis + BullMQ | Redis Pub/Sub + Socket.IO — BullMQ does not exist (Python stack) |
| Jobs | BullMQ workers | In-process cron loops inside the API server |

This stack divergence is itself a hardening finding: the deployment handbook describes a system that does not exist. Reconciliation is scheduled in PH3.10 (Documentation Synchronization) and recorded as ADR-027 in DECISIONS.md.

## What Is in Good Shape

- 341 passing backend tests (trading engine, portfolio, real-time streams, morning report, webhooks, broker integration)
- Frontend production build compiles cleanly with route-level code splitting
- No `.env` files committed; no hardcoded API keys; `JWT_SECRET` strictly from env
- bcrypt password hashing; httponly cookies; `require_admin` RBAC; immutable admin audit log
- `/api/monitor/health` endpoint; Mongo healthcheck in compose
- Zero TODO/FIXME markers in application source
- Event-driven real-time architecture (Redis Pub/Sub → Socket.IO) per REALTIME_SYSTEM.md
- Provider-independent market data architecture (ADR-026)

## What Blocks Production

Verified in code on branch `sprint-r3-frontend-realtime`:

| # | Finding | Location | Severity |
|---|---|---|---|
| B1 | Admin auto-login backdoor, **enabled by default** (`ENABLE_AUTO_LOGIN` defaults to `"true"`) | `backend/server.py:3860` | CRITICAL |
| B2 | Google OAuth demo-user bypass + legacy third-party session exchange (`demobackend.emergentagent.com`) that fails open | `backend/server.py:2672` | CRITICAL |
| B3 | ~~CORS `allow_origins` defaults to `*` with `allow_credentials=True`~~ **CLOSED (PH1.4)** — centralized in `backend/security/cors.py`; environment-driven exact-match allowlist (`CORS_ALLOWED_ORIGINS`), wildcard stripped from every source, methods/headers restricted | `backend/security/cors.py` | ~~CRITICAL~~ CLOSED |
| B4 | ~~Auth cookies set with `secure=False` (all four call sites)~~ **CLOSED (PH1.3)** — centralized in `backend/security/cookies.py`; `Secure` forced when `APP_ENV=production`, `HttpOnly`+`SameSite` on all cookies | `backend/security/cookies.py` | ~~CRITICAL~~ CLOSED |
| B5 | `docker-compose.yml` references `backend/Dockerfile` and `frontend/Dockerfile` — **neither exists**; dev-mode uvicorn `--reload` + bind mount; weak secret fallbacks in compose | `docker-compose.yml` | CRITICAL |
| B6 | No CI/CD — `.github/workflows/` does not exist | repo root | CRITICAL |
| H7 | Fabricated admin analytics (revenue series, `revenue_today = total_payments * 499`, hardcoded feature usage) — violates ADR-021 | `backend/server.py:4357, 4059, 4374` | HIGH |
| H8 | 6 failing backend tests: 1 stale assertion, 5 non-hermetic live-server integration tests | `backend/tests/` | HIGH |
| H9 | Zero frontend tests | `frontend/src/` | HIGH |
| H10 | No password policy, `email: str` not `EmailStr`, no email verification — password policy ✅ closed PH1.5 (2026-07-19, `backend/security/passwords.py`); email verification + account recovery ✅ closed PH1.5b/Identity Recovery (2026-07-22, `backend/security/recovery.py` — verification/reset/change endpoints, single-use signed tokens, `email_verified` flag, full session invalidation on credential rotation); **only `EmailStr` tightening remains outstanding** | `backend/models.py:32` | HIGH (near-closed) |
| H11 | ~~JWT lifetimes diverge from SECURITY.md (24 h access vs 15 min spec; no refresh rotation/revocation)~~ **CLOSED** (PH1.6, 2026-07-20): 15-min access, refresh rotation + reuse detection, durable revocation store; centralized in `backend/security/jwt.py` + `backend/security/sessions.py` | `backend/server.py` | HIGH |
| M12 | No rate limiting anywhere | global | MEDIUM |
| M13 | `server.py` is a 4,823-line monolith | `backend/server.py` | MEDIUM |
| M14 | Dev tooling (`black`, `flake8`) pinned in runtime `requirements.txt` | `backend/requirements.txt` | MEDIUM |
| M15 | Deprecated `@app.on_event("shutdown")` instead of lifespan handler | `backend/server.py:4817` | MEDIUM |
| M16 | Documentation/code stack mismatch (see table above); file-name drift (`TASK.md` vs `TASKS.md`, `PRODUCT_REQUIREMENT.md` vs `PRODUCT_REQUIREMENTS.md`, `PROMPT.md` vs `PROMPTS.md`, no `CHANGELOG.md`) | `.claude/` | MEDIUM |

---

# 3. Risk Assessment

## Risk Matrix

| ID | Risk | Likelihood | Impact | Exposure | Owner Phase |
|---|---|---|---|---|---|
| R-01 | Anyone on the internet obtains an admin session via `GET /api/auth/auto-login` | Certain (default-on) | Catastrophic | **CRITICAL** | PH1.1 |
| R-02 | ~~Arbitrary login as demo user via OAuth mock-code / fail-open session exchange~~ **CLOSED** (PH1.1 removed the bypasses; PH1.2 added state/CSRF, id_token verification, `email_verified` gate, redirect_uri allowlist, safe linking) | — | — | **CLOSED** | PH1.1–1.2 |
| R-03 | ~~Credentialed CSRF-style requests from any origin (wildcard CORS + cookies)~~ **CLOSED** (PH1.4: environment-driven exact-match origin allowlist, wildcard never paired with credentials, centralized in `backend/security/cors.py`) | — | — | **CLOSED** | PH1.4 |
| R-04 | ~~Auth tokens transmitted over plain HTTP (`secure=False`)~~ **CLOSED** (PH1.3: `Secure` forced in production, `HttpOnly`+`SameSite` everywhere, centralized cookie policy) | — | — | **CLOSED** | PH1.3 |
| R-05 | ~~Credential stuffing / brute force succeeds (no rate limiting, no password policy)~~ **CLOSED** (PH1.5: production password policy + timing-equalized login; PH1.7 (2026-07-21): centralized limiter in `backend/security/rate_limit.py` — login 5/15min per `ip:account` with progressive lockout, platform-wide per-user/per-IP tiers, `Retry-After`; plus a signed session-bound CSRF token layer in `backend/security/csrf.py`) | — | — | **CLOSED** | PH1.5 ✅, PH1.7 ✅ |
| R-06 | ~~Stolen access token valid for 24 h; refresh tokens never rotate or revoke~~ **CLOSED** (PH1.6: 15-min access token; refresh rotation with reuse-detection that revokes the family on replay; durable server-side revocation store; `password_changed_at` + token `ver` kill-switches) | — | — | **CLOSED** | PH1.6 |
| R-07 | Deployment impossible or hand-rolled (broken Docker, no CI/CD) → unreproducible prod, config drift | ~~Certain today~~ **Low** | High | **MEDIUM** (was HIGH) | PH2.1–2.7 — *PH2.12 executed a fresh-environment deployment end to end against a live daemon (build → healthy in 8 s → smoke tests → shutdown → restart → recovery), so the image and compose topology are reproducible and verified. Residual risk is entirely the **CD half**: deployment is still a human running compose, with no registry and no pipeline — so a rollback depends on the previous image surviving on the host. PH2.7b* |
| R-07b | **A rollback silently does nothing and reports success** | ~~Certain~~ **Very Low** | Catastrophic | **LOW** (new, then closed) | *Found by PH2.12 against a live stack and fixed the same sprint: `deploy_rollback.sh` recreated nothing while printing `rollback verified`, because Compose ranks shell environment above `.env` and the script's own config loader exported the tag it was rolling away from. Now passes the tag to compose explicitly and **asserts the running build** before declaring success. Two regression tests, both proven to fail without the fix. Recorded here rather than deleted because it is the sharpest example in the programme of a control that existed, was tested, and did not work* |
| R-08 | Regression ships unnoticed (no frontend tests, non-hermetic backend suite, no pipeline gate) | High | High | **HIGH** | PH3.1–3.5 |
| R-09 | Fabricated admin revenue data drives a real business decision | Medium | High | **HIGH** | PH3.2 |
| R-10 | Outage with no monitoring/alerting → prolonged silent downtime | Medium | High | **MEDIUM** | PH2.9–2.10 — *the **monitoring** half is closed as of 2026-08-15 (PH3.7 sprint label): every subsystem now emits health, error-class and latency signals, `subsystem_errors_total{subsystem,error_class}` answers "which part is failing" directly, and a 22+6-condition alert catalogue with thresholds and false-positive analysis exists in `docs/architecture/OBSERVABILITY.md` §9. **The risk does not move, because nothing evaluates or delivers those alerts** — no Prometheus, no Alertmanager, no channel, no uptime check. Detection is still a human noticing, which is exactly the term §11's RTO decomposition shows to be dominant. This is now a pure infrastructure task with no application work left in it*|
| R-11 | Data loss with no tested backup/restore path | Low | Catastrophic | **LOW** (was MEDIUM) | PH2.11 — *backup, restore, verification and an **executed, timed** drill delivered 2026-08-04; residual risk is that backups are still single-host (off-host copy documented, not wired) and failure is not yet alerted* |
| R-12 | `server.py` monolith slows every future fix and raises defect rate | Certain | Medium | **MEDIUM** | PH3.6 |
| R-13 | New engineer follows DEPLOYMENT.md and builds for the wrong stack | Medium | Medium | **MEDIUM** | PH3.10 |
| R-14 | Vulnerable dependency ships (no scanning) | Medium | Medium | **MEDIUM** | PH1.11 |
| R-15 | Socket.IO subscriptions insufficiently authorized / unthrottled | Medium | Medium | **MEDIUM** | PH1.9 |

## Production Readiness Score

Scoring: each category graded 0–10 against its authoritative document. Weighted composite.

Two columns: the 2026-07-17 baseline, and the post-PH1 re-score (2026-07-22).
Categories in PH1 scope moved; Phase 2 / Phase 3 categories are unchanged and
remain the launch blockers.

Three columns: the 2026-07-17 baseline, the post-PH1 re-score (2026-07-22), and
the post-PH2 re-score (2026-08-09, PH2.12 certification). Post-PH2 figures are
backed by execution against a live stack, not by review — see
`docs/infrastructure/PH2_CERTIFICATION.md`.

| Category | Baseline | Post-PH1 | Post-PH2 | Basis (post-PH2) |
|---|---|---|---|---|
| Application functionality | 8.5 | 8.5 | 8.5 | Unchanged — PH2 touched no product code |
| Authentication & authorization | 2.0 | **9.0** | **9.0** | No regression under containerisation (PH2.12 §17) |
| API & transport security | 3.0 | **8.5** | **9.0** | All PH1 headers verified live; `server` header suppressed; operational endpoints token-gated; proxy headers trusted only from the immediate peer |
| Secrets & configuration | 6.0 | **8.5** | **9.0** | Fail-closed rejected 5/6 bad configs at boot; gitleaks clean over full history; **zero leakage** of four real secrets across stdout + file sinks |
| Packaging & deployability | 1.0 | 1.0 | **9.0** | **423 MB** non-root image (was 1.03 GB), immutable source tree, no pip, no baked secrets; compose lifecycle verified end to end incl. full `down`/`up` persistence. −1: no frontend production image |
| CI/CD | 0.0 | **2.0** | **6.0** | Five workflows with real gates — but the blocking lint gate was **red on every run since PH2.4** (fixed in PH2.12), the dependency gate is **still failing** on 6 runtime CVEs, and there is **no CD, no registry** — PH2.7b (blocks launch) |
| Testing | 5.0 | 5.5 | 5.5 | 1014 hermetic pass, but PH2.12 showed two suites **agreeing with bugs** via stubs that never met the real contract; still zero frontend tests — PH3.1/3.2/3.3 |
| Observability | 3.5 | **9.0** | **9.0** | Instrumentation confirmed excellent live (health split correct under Mongo/Redis failure; 20+ metric families). **PH3.7 (2026-08-15) closed the remaining instrumentation gap** — dependencies were uninstrumented past the process boundary (Mongo had one bit; tasks, providers, AI, cron and the browser had nothing) — adding driver-level Mongo metrics, an error taxonomy, subsystem attribution, a critical configuration readiness check and the frontend's first error boundary. **Alerting still does not exist** — every signal is present, the alert set is now fully specified, and nothing watches it |
| Data integrity | 5.5 | 5.5 | **7.5** | **Destructive restore drilled**: 3 collections dropped → full recovery, 16 matched; backup `docker` transport verified. **PH3.8 (2026-08-16) audited the analytics/data model end to end** — every metric classified REAL/DERIVED/MOCK/UNAVAILABLE in code (4/26/17/5), one documented timezone strategy replacing per-call-site UTC-day arithmetic, and **ten defects fixed** including two P0s: the end-of-day job **crashed on every run** (swallowed by a broad `except`, so no report was ever written), and the P&L it would have sent was the **platform-wide sum broadcast to every user as their own** — a cross-tenant disclosure. Paper trades no longer contaminate real-money P&L; partial exits are now visible to the daily loss limit. −2.5: no off-host copy, no PITR; **17 mock metrics remain in production** — flagged in every payload and marked in the UI, but not yet removed, and the blocker is structural (`db.payments` has no writer; the platform has no payment integration) |
| Documentation accuracy | 5.0 | 6.5 | 7.5 | Operations docs proved usable by following them; deployment-stack mismatch closed. −2.5: no single deploy-from-nothing runbook; undocumented `.env` coupling in the backup scripts — PH3.10 |

## **Overall Production Readiness Score: 4.2 → ~6.4 → ~8.0 / 10** (post-PH2, 2026-08-09)

Definition of launchable: **≥ 9.0 composite with no category below 8.0** (see §22).

PH1 lifted every security category over the bar. PH2 lifted Packaging from 1.0 to
9.0 — the single largest movement in the programme — and put Data integrity on
measured evidence for the first time. **The composite is now gated by three
categories below 8.0: CI/CD (6.0), Testing (5.5) and Data integrity (7.0), plus
Documentation (7.5).**

Note that Observability holds at 9.0 while the certification records "no alerting
whatsoever". That is not an inconsistency being papered over: the *observable*
surface genuinely is 9.0-grade, and alerting is scored under CI/CD-adjacent
operational tooling rather than instrumentation. It is called out here because it
is the highest-value remaining infrastructure item — per PH2.10's RTO
decomposition, detection dominates recovery time, so the measured sub-15-second
mechanical RTO is theoretical until something notices the failure.

## Priority Matrix

| | Blocks Launch | Does Not Block Launch |
|---|---|---|
| **Do first (serial)** | PH1.1 backdoor removal · PH1.3 cookies · PH1.4 CORS/headers · PH2.1–2.3 Docker | — |
| **Do early (parallelizable)** | PH2.5 CI foundation · PH1.5 validation · PH1.7 rate limiting · PH3.1 test repair · PH3.2 mock-data removal | PH3.3 frontend test foundation |
| **Do before certification** | PH1.6 JWT lifecycle · PH1.8 secrets · PH2.8–2.11 data/observability/DR · PH3.5 error-state tests · PH3.9 E2E | PH3.6 monolith split · PH3.7 load testing |
| **Deferred / optional** | — | MFA implementation (design only in PH1.10) · Kubernetes · multi-region · Sentry SaaS choice |

---

# 4. Security Strategy

Authoritative references: SECURITY.md (operational policy) and
**SECURITY_ARCHITECTURE.md** (engineering blueprint — module design, threat
model, trust boundaries, sequence diagrams, and the authoritative
implemented-vs-planned status for every control referenced below). This
section defines the hardening posture; sprint detail lives in
PRODUCTION_ROADMAP.md (PH1).

## Principles for this program

1. **Fail closed.** Any auth path that cannot verify identity returns 401. No demo fallbacks, no third-party session exchanges, no simulation modes reachable in production builds.
2. **Secure by default.** Every security-relevant env var must default to the safe value. `ENABLE_AUTO_LOGIN` class of switches must default off AND be hard-gated on `APP_ENV != production`.
3. **Defense in depth.** Rate limiter → validation → authn → authz → audit, on every route including WebSocket events.
4. **Spec compliance.** SECURITY.md is the contract: 12-char password policy with complexity, 15-min access tokens, 30-day rotating refresh tokens, tiered rate limits (Guest 30/min → Elite 600/min), full security-header set, CSRF protection for cookie-based auth.

## Workstreams

- **Auth surface purge (PH1.1–1.2):** delete auto-login endpoint and OAuth simulation/legacy paths entirely — removal, not flags. Google OAuth becomes fail-closed with server-side code exchange only.
- **Transport & browser security (PH1.3–1.4):** `secure=True` cookies (env-driven for local dev), `SameSite` strategy, CSRF tokens for state-changing cookie-auth routes, explicit `CORS_ORIGINS` allowlist required at boot in production, HSTS/CSP/X-Frame-Options/X-Content-Type-Options/Referrer-Policy/Permissions-Policy middleware.
- **Identity hygiene (PH1.5–1.6):** Pydantic `EmailStr`, password policy validator, email-verification flow decision, JWT lifetimes to spec, refresh rotation with reuse detection and revocation list.
- **Abuse resistance (PH1.7 ✅, PH1.9):** ✅ PH1.7 — centralized limiter (`backend/security/rate_limit.py`, MongoDB-backed behind a Redis-ready `RateLimitStore` interface) with per-user/per-IP tiers + strict per-endpoint limits on `/api/auth/*` and `Retry-After`, plus a signed session-bound CSRF token layer (`backend/security/csrf.py`); PH1.9 — Socket.IO connection auth, per-event authorization, message rate limits, idle disconnect.
- **Supply chain & certification (PH1.9 ✅, PH1.11–1.12):** ✅ PH1.9 — centralized secret registry + boot-time fail-closed validation (`backend/security/secrets.py`), secret-rotation runbook (`.claude/SECRETS.md`), `pip-audit`/`npm audit`/`gitleaks` in CI (`.github/workflows/security-audit.yml`), full dependency pinning + 7 CVE patches, weak compose defaults removed; remaining — Dependabot, `requirements-dev` split, OWASP Top 10 review, internal penetration checklist, security sign-off gate (PH1.11–1.12).

---

# 5. Infrastructure Strategy

Authoritative reference: DEPLOYMENT.md (to be corrected to the FastAPI stack in PH3.10).

- **Containerization first.** Two production Dockerfiles (multi-stage): backend (python slim, non-root user, no `--reload`, no bind mounts, uvicorn with explicit workers) and frontend (node build stage → nginx static stage with gzip and cache headers).
- **Compose split.** `docker-compose.yml` (dev: bind mounts, reload, Mailhog) and `docker-compose.prod.yml` (immutable images, env-file injection, no secret fallbacks — compose must fail if secrets are absent).
- **Configuration contract.** A single documented `.env.example` per service; boot-time validation module that hard-fails on missing/weak production config (`JWT_SECRET` length, `CORS_ORIGINS` presence, `APP_ENV`).
- **Data tier.** MongoDB: authenticated users, TLS, indexes verified, Atlas backup policy. Redis: `requirepass`, no public bind, eviction policy documented, persistence decision recorded.
- **Environments.** dev → staging → production, each with isolated DB/Redis/keys per DEPLOYMENT.md. Staging must run the production images.

---

# 6. Deployment Strategy

- **CI (PH2.5–2.6):** GitHub Actions. Stage 1 (every PR): backend `pytest -m "not integration"`, `ruff`/`flake8`, frontend `npm run build` + lint. Stage 2: docker image builds, `pip-audit`/`npm audit`, integration job that boots the compose stack and runs `pytest -m integration`.
- **CD (PH2.7):** tag-driven releases; image push to registry; staging auto-deploy; production deploy behind manual approval; post-deploy health verification (`/health`, `/ready`, `/live`); automatic rollback to previous image tag on failed health check.
- **Branch policy:** `main` protected — merges require green pipeline. No direct pushes.
- **Release versioning:** semver tags (`v1.0.0-rc1` during hardening); every release reversible per DEPLOYMENT.md rollback flow.

---

# 7. Testing Strategy

Authoritative reference: TESTING.md.

- **Hermetic default suite (PH3.1):** default `pytest` run must pass with no services running. Live-server tests marked `@pytest.mark.integration` and run only in the CI integration job. Fix the stale `test_run_cycle_trails_and_books_targets` assertion (`closed_trades` key).
- **Frontend testing (PH3.3–3.4):** Jest + React Testing Library via craco. Priority order: auth flows → Dashboard render → service layer (API + Socket.IO event handling) → admin components. Smoke coverage is a launch gate; 80% coverage per TESTING.md is the post-launch target.
- **Contract & error-state tests (PH3.5):** every API service tested for loading/success/empty/error/retry per CLAUDE.md Error Handling rules.
- **E2E (PH3.9):** Playwright against the compose stack: register → login → dashboard → watchlist → paper trade → logout; admin login → user management → audit log.
- **Regression protocol (PH3.11):** release test checklist (desktop/tablet/mobile, loading/empty/error states, a11y pass) executed on staging before every production deploy.

---

# 8. Performance Strategy

Targets (ROADMAP.md / DEPLOYMENT.md): dashboard < 2 s, API < 500 ms, market updates real-time, 99.9% availability.

- **Measure before tuning.** PH3.7 establishes baselines: k6/Locust API load profile, Lighthouse CI for frontend, Socket.IO fan-out benchmark under simulated market bursts.
- Existing R9 optimizations (event batching, selective rendering, virtualization, code splitting, Redis batching) are the foundation — hardening validates them under load rather than adding new optimization work.
- Performance budgets enforced in CI (Lighthouse thresholds, bundle-size check) once baselines exist.

## 8.1 Resource budget (PH3.7b, 2026-08-15)

**Per uvicorn worker.** Multi-worker behaviour is unmeasured, and each worker
holds an independent copy of every in-process cache, so these numbers multiply
rather than divide. Full derivation:
`docs/performance/PH3_MEMORY_STABILITY.md` §19.

| Resource | Budget | Basis |
|---|---|---|
| Backend RSS (idle → sustained load) | 45–80 MB → ≤ 150 MB | measured |
| Backend CPU | 100% of **one** core is one worker's ceiling | measured (PH3.7) |
| Mongo pool | `maxPoolSize` 100; idle reaped after **60 s** | configured PH3.7b |
| Redis pool | `REDIS_MAX_CONNECTIONS`; **the shipped default of 24 is too small** | PH3.7 L-1, owner PH3.7/deployment |
| Redis Pub/Sub connections | exactly **1** per subscribed channel | enforced by registry |
| Outbound HTTP sockets | ≤ **20** per (loop, timeout) pool | `services/http_client.py` |
| Supervised background tasks | exactly **4** in a healthy process | `background_tasks_running` |
| Event-bus subscribers | exactly **1** in a healthy process | `event_bus_subscribers` |
| AI chat-context cache | ≤ **512** entries | `_CACHE_MAX_ENTRIES` |
| Market cache fallback | ≤ **1,024** keys | `_MEMORY_MAX_KEYS` |
| Portfolio / trade throttle maps | ≤ **4,096** each | `_MAX_TRACKED_USERS` |
| Event-bus log | ≤ **500** events | `_max_log_size` |
| Concurrent WebSocket connections | **TO BE BASELINED IN STAGING** | 150 held cleanly; no ceiling found |

**The alert to write first** is `websocket_tracked_users` holding a floor above
zero while `websocket_connections` sits at zero. That is the exact signature of
the P0 leak PH3.7b fixed, and before that sprint no dashboard in the system
could have shown it.

**Every perpetual loop must be started through
`backend/infrastructure/tasks.py`, never with a bare `asyncio.create_task`.**
A discarded task has no strong reference (asyncio keeps only a weak one) and,
more importantly, no cancellation path — which is how four loops came to keep
running against a Mongo client that `shutdown()` was in the middle of closing.
Shutdown cancels producers **before** the resources they use.

---

# 9. Documentation Strategy

- PRODUCTION_HARDENING.md (this file) and PRODUCTION_ROADMAP.md are permanent `.claude/` documents, versioned with the doc system (now v1.2).
- PH3.10 reconciles DEPLOYMENT.md (and any ADR references) with the actual FastAPI + CRA stack, resolves file-name drift (`TASKS.md`→`TASK.md`, etc.), and introduces a dedicated `CHANGELOG.md`.
- Every hardening sprint that changes operational behavior must update its authoritative document in the same PR (INDEX.md rule: documentation synchronized with codebase).
- Documentation version increments on every phase completion (v1.3 after PH1, v1.4 after PH2, v1.5 = launch docs after PH3).

---

# 10. Monitoring Strategy

- **Health:** extend `/api/monitor/health` into `/health` (liveness), `/ready` (DB + Redis + provider reachability), `/live` per DEPLOYMENT.md. **✅ Delivered (PH2.5, extended PH3.7)** — `/api/health/live`, `/api/health/ready`, `/api/health/startup`. Readiness checks MongoDB (critical), Redis (non-critical — the cache falls back in-process) and, since PH3.7, **configuration (critical)**. Liveness deliberately touches nothing: a liveness probe wired to a dependency turns a database blip into a fleet-wide restart. Note `/api/monitor/health` was **not** extended and is not an alias — it is an authenticated AI *portfolio* analysis that merely shares the word.
- **Logging:** structured JSON logs (level, timestamp, request id, user id — never tokens/passwords/keys) replacing ad-hoc prints; log retention decision documented. **✅ Delivered (PH2.5/PH2.6)** — `observability/logging.py` + `context.py`, five separated streams, size-triggered rotation with gzip, retention by age and count, redaction reusing `security.audit`'s marker list, and every file handler behind a bounded `QueueListener` so disk I/O never touches the event loop. See `docs/operations/LOGGING.md`.
- **Error tracking:** Sentry (or self-hosted GlitchTip) for unhandled exceptions, worker failures, broker/payment/AI failures. **⚠️ Partial.** No third-party service. Unhandled backend exceptions are logged with tracebacks and counted as `http_request_errors_total{kind="exception"}`; worker failures as `background_task_terminations_total{outcome="failed"}`; broker/AI failures as `provider_errors_total` / `ai_request_errors_total`, each with an error class. **Frontend runtime errors reach `frontend_errors_total` plus one structured log line (PH3.7)** — previously they reached nothing at all. What is missing is *grouping and deduplication across releases*, which is what a tracker actually buys.
- **Metrics:** request latency, error rate, Socket.IO connection count, Redis pub/sub lag, market-event throughput, cron/job success. Prometheus-format endpoint acceptable initially. **✅ Delivered, with one gap.** Prometheus text exposition at `/api/metrics`, token-gated in production. Latency ✅, error rate ✅, Socket.IO connections ✅ (gauges + connect/disconnect/fan-out counters), market-event throughput ✅ (`event_bus_events_total`), cron success ✅ (`scheduler_job_runs_total`, including `missed` — the failure only cron has). **Redis pub/sub *lag* ❌** — reconnects and message dispositions are counted; lag itself is not.
- **Alerting:** minimum viable — health-check failure, error-rate spike, auth-failure spike (security signal), backup failure. Delivered to a monitored channel. **❌ NOT DELIVERED — the highest-value remaining infrastructure item.** The alert *set* is fully specified: `docs/architecture/OBSERVABILITY.md` §9 defines 6 critical and 22 warning conditions, each with a threshold, severity, expected response and false-positive analysis, covering all four named here and more. **Nothing evaluates or delivers them.** There is no Prometheus server, no Alertmanager, no monitored channel and no uptime check, so detection remains a human noticing — the term §11 identifies as dominating RTO. Every threshold is also an engineering estimate rather than a measurement, because no staging baseline exists to derive one from (PH2.12).

---

# 11. Recovery Strategy

- **RPO:** ≤ 24 h at launch (daily Mongo backups), target ≤ 1 h post-launch (point-in-time recovery / Atlas). **✅ Implemented 2026-08-04** — nightly full backup; the ≤ 1 h target still requires converting mongod to a single-node replica set for `--oplog`.
- **RTO:** ≤ 4 h at launch. **✅ Measurement-backed 2026-08-04** — the mechanical restore is *seconds* at current data volume (205 000 docs / 26.3 MB restored in 3.51 s, indexes and document contents verified identical). The four-hour budget is dominated by human time: detection, decision, host provisioning, off-host fetch, configuration recovery and post-restore validation. Re-measure at production data volume — see `docs/operations/BACKUP_AND_RESTORE.md` §8.1.
- **Backups:** daily/weekly/monthly Mongo per SECURITY.md; encrypted; restore drill executed and documented in PH2.11 — an untested backup does not count. **✅ Delivered 2026-08-04** by `scripts/backup/` — AES-256, grandfather-father-son 7/4/6, three verification levels with the structural level running automatically after every backup, and a drill that restores into a scratch database and compares per-collection counts against a baseline captured at dump time. Secret material has its own mandatory-encryption archive. Redis is deliberately **not** backed up (reconstructible cache; PH2.7's AOF is a warm-start optimisation). Full detail and the eight known limitations: `docs/operations/BACKUP_AND_RESTORE.md`.
- **Rollback:** every deploy keeps the previous image tag warm; DB migrations must be backward-compatible one version (expand/contract pattern). **✅ Delivered 2026-08-05** by `scripts/dr/deploy_rollback.sh` — an append-only deployment ledger under `$BACKUP_ROOT` (so it survives the host it describes), a precondition that refuses to touch anything unless the target image is present on the host, an atomic `BACKEND_IMAGE_TAG` rewrite, backend-only recreation (`--no-deps`), and an **automatic revert** when the rolled-back build fails verification. The expand/contract requirement is unchanged and unautomatable: the script warns and requires the tag to be typed, because rolling an image back over a migrated database is not a rollback. Registry-independent rollback remains blocked on PH2.7b — see `docs/operations/DISASTER_RECOVERY.md` §12 L5.
- **Incident response:** Detect → Alert → Investigate → Contain → Recover → Review → Document (SECURITY.md); postmortem template added in PH2.11. **✅ Delivered 2026-08-05** — `docs/runbooks/POSTMORTEM_TEMPLATE.md` (blameless, with an explicit "where luck was involved" section and a rule that a postmortem is unfinished until it changes a runbook, a check, an alert or a drill) and `docs/operations/DISASTER_RECOVERY.md`: ten runbooks (R1–R10) covering failed deployment, container failure, Redis loss, MongoDB corruption, a failed rollback, storage/volume loss, complete server loss, configuration corruption / accidental secret rotation, suspected compromise, and a silently failing backup job — each with diagnosis, recovery, what to do when the recovery fails, and a *command* for verification. Severity model and escalation matrix in §5; the eight disaster scenarios in `BACKUP_AND_RESTORE.md` §13 remain the data-tier view of the same ground.
- **Verification:** **✅ Added 2026-08-05** — `scripts/dr/dr_verify.sh` is both the diagnosis tool and the post-recovery gate, deliberately the same tool. Four layers (host → containers → data → application), every check run with dependent checks reporting SKIP rather than a misleading second failure, an **empty restored database treated as a failure**, per-collection counts compared against the backup manifest's baseline, and the running build asserted against the expected version so a rollback that silently did not take effect cannot pass. Measured 1.10 s for a full run.
- **Recovery objectives are now decomposed, not just stated:** `DISASTER_RECOVERY.md` §4.2 budgets the four-hour RTO phase by phase and shows the mechanical work is **under five minutes** — detection (manual until alerting lands) is the dominant term, which makes roadmap PH2.10's alerting worth more to the RTO than any further optimisation of the restore path. Seven named assumptions (§4.3) and the business-continuity assumptions (§4.4) are recorded so a plan that has quietly stopped being true is detectable at drill time.

---

# 12. Operational Checklist (steady-state, post-launch)

Daily: review error tracker; check backup success; check health dashboard.
Weekly: dependency-scan triage; auth-failure/rate-limit anomaly review; staging deploy of `main`.
Monthly: restore drill (rotating collection sample); secret-rotation review; access review (admin accounts); performance baseline re-run.
Per release: full §7 regression protocol; release notes; post-deploy health verification.

---

# 13. Acceptance Criteria (program-level)

The Production Hardening program is complete when ALL of the following are true:

1. Zero authentication paths that bypass credential verification exist in the codebase (verified by code search + integration tests asserting 401/404 on removed endpoints).
2. All SECURITY.md checklist items pass or have a documented, ADR-recorded deviation.
3. `docker compose -f docker-compose.prod.yml up` boots the full stack from clean images and passes health checks.
4. CI pipeline blocks merge on: lint, type/format check, hermetic backend suite, frontend tests, frontend build, dependency audit.
5. Default backend test suite: 100% pass, hermetic. Integration suite: 100% pass in CI.
6. Frontend smoke tests exist and pass for auth, dashboard, and the service layer.
7. No mock/fabricated data reachable in any production code path (ADR-021 compliant).
8. Structured logging, error tracking, and minimum alerting live in staging and production.
9. A backup has been restored successfully in a drill within the last 30 days before launch.
10. Documentation describes the actual system (stack, endpoints, env vars) — verified by the PH3.10 sync review.
11. Production Readiness Score re-scored ≥ 9.0 with no category < 8.0.

---

# 14. Rollback Strategy (program-level)

Hardening changes are behavior-narrowing and therefore low-risk to roll back, with rules:

- Every PH sprint lands as an independently revertible PR (or small PR series) — no cross-sprint entanglement.
- Security removals (PH1.1–1.2) are **never rolled back**; if they break a legitimate flow, the flow is fixed forward. The demo/dev convenience they provided is re-created only as dev-environment seeding scripts, never as endpoints.
- Config-tightening sprints (CORS, cookies, rate limits) ship with env-var escape hatches that work **only when `APP_ENV != production`**.
- Infrastructure sprints keep the previous deployment method working until the new one passes staging certification.
- Each sprint's specific rollback plan is defined in PRODUCTION_ROADMAP.md.

---

# 15. Verification Checklist (per sprint, mandatory)

- [ ] Acceptance criteria in PRODUCTION_ROADMAP.md all pass
- [ ] Full hermetic test suite green locally and in CI
- [ ] New behavior covered by at least one automated test
- [ ] Security-relevant change reviewed against SECURITY.md
- [ ] Authoritative documentation updated in the same PR
- [ ] TASK.md status updated
- [ ] No new mock data, no new TODO markers, no secrets in diff
- [ ] Change is revertible as a unit

---

# 16. Launch Checklist

Executed once, immediately before public availability:

- [ ] All §13 acceptance criteria verified and evidenced
- [ ] Penetration checklist (SECURITY.md) executed against staging; criticals fixed
- [ ] `ENABLE_AUTO_LOGIN`, mock OAuth codes, demo backends: confirmed absent from codebase (grep evidence attached)
- [ ] Production env vars set and validated by boot-time checker
- [ ] DNS, TLS, HSTS live; HTTP→HTTPS redirect verified
- [ ] Monitoring dashboards and alerts confirmed firing (test alert)
- [ ] Backup schedule confirmed; latest restore drill ≤ 30 days old
- [ ] On-call/incident contact defined; rollback rehearsed on staging
- [ ] Legal/compliance: privacy policy, market-data display terms, broker API policy review
- [ ] CHANGELOG.md and release notes published

---

# 17. Production Certification Checklist

Sign-off matrix — each row requires named sign-off and date:

| Area | Evidence Required | Gate | Sign-off |
|---|---|---|---|
| Security | PH1.12 certification report; pen-checklist results | Blocks launch | ✅ **CERTIFIED — Principal Release & Security Engineer, 2026-07-22** (`docs/security/PH1_CERTIFICATION.md`; authn 9.0 / API sec 8.5; F-1/F-2/F-3 closed) |
| Infrastructure | PH2.12 report; staging soak (7 days, no Sev-1) | Blocks launch | ⚠️ **CONDITIONALLY CERTIFIED — Principal Platform Engineer, 2026-08-09** (`docs/infrastructure/PH2_CERTIFICATION.md`; infrastructure 8.0/10; 1 Critical + 2 High found and fixed in-sprint against a live stack). **The 7-day staging soak was NOT performed — no staging environment exists — so this row is not fully satisfied and carries to PH3.** Certification was executed against a live local stack instead. Six required actions before production in §24; the blocking three are runtime dependency CVEs, the unwired off-host backup copy, and the total absence of alerting |
| Quality | PH3.12 report; regression protocol run | Blocks launch | ⛔ Pending PH3 |
| Data | Restore drill record; ADR-021 compliance grep | Blocks launch | ⛔ Pending PH2.8/PH3.2 |
| Documentation | PH3.10 sync report | Blocks launch | ⛔ Pending PH3.10 |
| Performance | Baseline report vs targets | Advisory at launch; blocks 10k-user milestone | ⛔ Pending PH3.7 |

> **Phase 1 (Security) is CERTIFIED COMPLETE (2026-07-22).** The remaining rows
> are Phase 2 / Phase 3 gates; the overall launch decision stays **NO-GO** until
> all blocking rows are signed off and the composite readiness score reaches ≥ 9.0
> with no category < 8.0 (§22).

---

# 18. Future Maintenance Strategy

- **Feature freeze ends** only after Production Certification. Post-launch, every feature PR must pass the same CI gates — the hardening bar never lowers.
- Quarterly: re-run the production readiness audit as a scored exercise; track the composite score in CHANGELOG.md.
- Dependency updates: Dependabot auto-PRs, weekly triage.
- `server.py` decomposition (PH3.6) establishes the router-per-domain pattern; all new endpoints must land in routers, shrinking the monolith monotonically.
- Security review required on every PR touching auth, payments, broker, or admin (ADR-022).

---

# 19. Engineering Standards (hardening addendum)

In addition to CODING_STANDARDS.md:

1. No endpoint ships without authn/authz declaration, input validation, rate-limit class, and an error-state contract.
2. Every env var must be registered in the boot-time config validator with type, default policy, and prod-required flag.
3. "Dev convenience" code (seeding, fake providers) lives under a dev-only module that is import-guarded on `APP_ENV` — never inline in request handlers.
4. Backend: new code in routers/services, never appended to `server.py`.
5. Tests accompany the change in the same PR. A fix without a test that would have caught it is incomplete.

---

# 20. Implementation Sequencing Summary

Full dependency graph in PRODUCTION_ROADMAP.md §Dependency Graph. Summary:

- **Serial critical path:** PH1.1 → PH1.3 → PH1.4 → PH2.1–2.3 (Docker) → PH2.5 (CI) → PH2.7 (CD) → staging soak → certification.
- **Parallel track A (security):** PH1.5–1.9 after PH1.1; independent of infrastructure.
- **Parallel track B (quality):** PH3.1–3.3 can start immediately (test repair needs nothing); PH3.2 (mock data) independent.
- **Blocks production:** PH1.1–1.8, PH2.1–2.8, PH3.1–3.3, PH3.5, PH3.10, all three certification sprints.
- **Does not block production (do before 10k users):** PH1.10 MFA implementation, PH3.6 monolith split, PH3.7 load testing beyond baseline, PH3.9 full E2E breadth.
- **Deferred:** Kubernetes, multi-region, GraphQL, vector DB (Pending Decisions in DECISIONS.md).

---

# 21. Open Risks Report

Risks that remain open **after** the program completes, requiring ongoing ownership:

| ID | Open Risk | Mitigation Posture |
|---|---|---|
| OR-1 | Yahoo Finance free-tier data is unlicensed for redistribution at commercial scale | Business/legal review before Commercial Launch milestone; licensed feed on roadmap (ADR-026 tier 2) |
| OR-2 | Single-provider AI dependency cost/availability | Model abstraction layer (ADR-010 future work) |
| OR-3 | In-process cron jobs die with the API server; no independent worker tier yet | Acceptable at launch scale; worker extraction scheduled post-launch (DEPLOYMENT.md background workers) |
| OR-4 | MFA for admin accounts designed (PH1.10) but not enforced at launch | Enforce before Closed Beta ends; interim: strong passwords + audit-log review |
| OR-5 | 80% frontend coverage target not reached at launch (smoke-level only) | PH3.4 continues post-launch; coverage gate ratchets up in CI |
| OR-6 | Email verification depends on production SMTP provider selection | Decision required during PH1.5b (email scope was split out of PH1.5, which shipped password-policy-only); risk if deferred |
| OR-7 | Broker API policy changes (Zerodha/Upstox) | Quarterly policy review; adapter isolation limits blast radius |
| OR-8 | Regulatory exposure of AI-generated trading guidance | Disclaimers audit at launch checklist; legal review before Commercial Launch |

---

# 22. Definition of Production Ready

StockAssist AI is production ready when, and only when:

> A new engineer can clone the repository, read the documentation, build both production images, deploy the stack to a clean environment with validated configuration, and serve real users — with every authentication path verifying real credentials, every request rate-limited and validated, every error observable, every byte of displayed data real, every deploy reversible, every backup restorable, and every one of these properties enforced automatically by the pipeline rather than by memory.

Quantitatively: §13 acceptance criteria all pass, composite readiness score ≥ 9.0, no category < 8.0, and the three phase-certification sign-offs (§17) are recorded.

---

# Version History

| Version | Date | Change |
|---|---|---|
| 1.2 | 2026-07-17 | Initial document. Created from the Sprint 12 Production Readiness Report as the baseline for the PH1–PH3 Production Hardening program. |

---

# End of Production Hardening Document

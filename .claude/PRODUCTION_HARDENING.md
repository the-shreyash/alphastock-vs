# StockAssist AI
## Production Hardening — Master Architecture Document

Version: 1.2

Status: Approved Baseline — Awaiting PH1 Implementation Approval

Date: 2026-07-17

Owner: Engineering (CTO)

Companion Document: PRODUCTION_ROADMAP.md (sprint-level plan for PH1–PH3)

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
| B3 | CORS `allow_origins` defaults to `*` with `allow_credentials=True` | `backend/server.py:4668` | CRITICAL |
| B4 | Auth cookies set with `secure=False` (all four call sites) | `backend/server.py:193` | CRITICAL |
| B5 | `docker-compose.yml` references `backend/Dockerfile` and `frontend/Dockerfile` — **neither exists**; dev-mode uvicorn `--reload` + bind mount; weak secret fallbacks in compose | `docker-compose.yml` | CRITICAL |
| B6 | No CI/CD — `.github/workflows/` does not exist | repo root | CRITICAL |
| H7 | Fabricated admin analytics (revenue series, `revenue_today = total_payments * 499`, hardcoded feature usage) — violates ADR-021 | `backend/server.py:4357, 4059, 4374` | HIGH |
| H8 | 6 failing backend tests: 1 stale assertion, 5 non-hermetic live-server integration tests | `backend/tests/` | HIGH |
| H9 | Zero frontend tests | `frontend/src/` | HIGH |
| H10 | No password policy, `email: str` not `EmailStr`, no email verification | `backend/models.py:32` | HIGH |
| H11 | JWT lifetimes diverge from SECURITY.md (24 h access vs 15 min spec; no refresh rotation/revocation) | `backend/server.py` | HIGH |
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
| R-02 | Arbitrary login as demo user via OAuth mock-code / fail-open session exchange | High | Catastrophic | **CRITICAL** | PH1.1–1.2 |
| R-03 | Credentialed CSRF-style requests from any origin (wildcard CORS + cookies) | High | Severe | **CRITICAL** | PH1.4 |
| R-04 | Auth tokens transmitted over plain HTTP (`secure=False`) | Medium | Severe | **HIGH** | PH1.3 |
| R-05 | Credential stuffing / brute force succeeds (no rate limiting, no password policy) | High | Severe | **HIGH** | PH1.5, PH1.7 |
| R-06 | Stolen access token valid for 24 h; refresh tokens never rotate or revoke | Medium | High | **HIGH** | PH1.6 |
| R-07 | Deployment impossible or hand-rolled (broken Docker, no CI/CD) → unreproducible prod, config drift | Certain today | High | **HIGH** | PH2.1–2.7 |
| R-08 | Regression ships unnoticed (no frontend tests, non-hermetic backend suite, no pipeline gate) | High | High | **HIGH** | PH3.1–3.5 |
| R-09 | Fabricated admin revenue data drives a real business decision | Medium | High | **HIGH** | PH3.2 |
| R-10 | Outage with no monitoring/alerting → prolonged silent downtime | Medium | High | **MEDIUM** | PH2.9–2.10 |
| R-11 | Data loss with no tested backup/restore path | Low | Catastrophic | **MEDIUM** | PH2.11 |
| R-12 | `server.py` monolith slows every future fix and raises defect rate | Certain | Medium | **MEDIUM** | PH3.6 |
| R-13 | New engineer follows DEPLOYMENT.md and builds for the wrong stack | Medium | Medium | **MEDIUM** | PH3.10 |
| R-14 | Vulnerable dependency ships (no scanning) | Medium | Medium | **MEDIUM** | PH1.11 |
| R-15 | Socket.IO subscriptions insufficiently authorized / unthrottled | Medium | Medium | **MEDIUM** | PH1.9 |

## Production Readiness Score

Scoring: each category graded 0–10 against its authoritative document. Weighted composite.

| Category | Score | Basis |
|---|---|---|
| Application functionality | 8.5 | Feature-complete MVP, deep backend test coverage |
| Authentication & authorization | 2.0 | Two active backdoors; good fundamentals underneath |
| API & transport security | 3.0 | CORS wildcard, insecure cookies, no rate limiting, no security headers |
| Secrets & configuration | 6.0 | Good repo hygiene; weak compose fallbacks; no boot-time validation |
| Packaging & deployability | 1.0 | Docker broken, no Dockerfiles |
| CI/CD | 0.0 | Does not exist |
| Testing | 5.0 | Strong backend suite (341), but 6 failures, non-hermetic, zero frontend tests |
| Observability | 3.5 | Health endpoint only; no structured logging, metrics, or error tracking |
| Data integrity | 5.5 | Mock data in admin analytics violates ADR-021 |
| Documentation accuracy | 5.0 | Comprehensive but describes the wrong backend stack |

## **Overall Production Readiness Score: 4.2 / 10**

Definition of launchable: **≥ 9.0 composite with no category below 8.0** (see §22).

## Priority Matrix

| | Blocks Launch | Does Not Block Launch |
|---|---|---|
| **Do first (serial)** | PH1.1 backdoor removal · PH1.3 cookies · PH1.4 CORS/headers · PH2.1–2.3 Docker | — |
| **Do early (parallelizable)** | PH2.5 CI foundation · PH1.5 validation · PH1.7 rate limiting · PH3.1 test repair · PH3.2 mock-data removal | PH3.3 frontend test foundation |
| **Do before certification** | PH1.6 JWT lifecycle · PH1.8 secrets · PH2.8–2.11 data/observability/DR · PH3.5 error-state tests · PH3.9 E2E | PH3.6 monolith split · PH3.7 load testing |
| **Deferred / optional** | — | MFA implementation (design only in PH1.10) · Kubernetes · multi-region · Sentry SaaS choice |

---

# 4. Security Strategy

Authoritative reference: SECURITY.md. This section defines the hardening posture; sprint detail lives in PRODUCTION_ROADMAP.md (PH1).

## Principles for this program

1. **Fail closed.** Any auth path that cannot verify identity returns 401. No demo fallbacks, no third-party session exchanges, no simulation modes reachable in production builds.
2. **Secure by default.** Every security-relevant env var must default to the safe value. `ENABLE_AUTO_LOGIN` class of switches must default off AND be hard-gated on `APP_ENV != production`.
3. **Defense in depth.** Rate limiter → validation → authn → authz → audit, on every route including WebSocket events.
4. **Spec compliance.** SECURITY.md is the contract: 12-char password policy with complexity, 15-min access tokens, 30-day rotating refresh tokens, tiered rate limits (Guest 30/min → Elite 600/min), full security-header set, CSRF protection for cookie-based auth.

## Workstreams

- **Auth surface purge (PH1.1–1.2):** delete auto-login endpoint and OAuth simulation/legacy paths entirely — removal, not flags. Google OAuth becomes fail-closed with server-side code exchange only.
- **Transport & browser security (PH1.3–1.4):** `secure=True` cookies (env-driven for local dev), `SameSite` strategy, CSRF tokens for state-changing cookie-auth routes, explicit `CORS_ORIGINS` allowlist required at boot in production, HSTS/CSP/X-Frame-Options/X-Content-Type-Options/Referrer-Policy/Permissions-Policy middleware.
- **Identity hygiene (PH1.5–1.6):** Pydantic `EmailStr`, password policy validator, email-verification flow decision, JWT lifetimes to spec, refresh rotation with reuse detection and revocation list.
- **Abuse resistance (PH1.7, PH1.9):** slowapi (or equivalent ASGI limiter) with per-tier budgets from SECURITY.md, strict low limits on `/api/auth/*`; Socket.IO connection auth, per-event authorization, message rate limits, idle disconnect.
- **Supply chain & certification (PH1.8, PH1.11–1.12):** boot-time env validation, secret rotation runbook, `pip-audit`/`npm audit` in CI, Dependabot, OWASP Top 10 review, internal penetration checklist, security sign-off gate.

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

---

# 9. Documentation Strategy

- PRODUCTION_HARDENING.md (this file) and PRODUCTION_ROADMAP.md are permanent `.claude/` documents, versioned with the doc system (now v1.2).
- PH3.10 reconciles DEPLOYMENT.md (and any ADR references) with the actual FastAPI + CRA stack, resolves file-name drift (`TASKS.md`→`TASK.md`, etc.), and introduces a dedicated `CHANGELOG.md`.
- Every hardening sprint that changes operational behavior must update its authoritative document in the same PR (INDEX.md rule: documentation synchronized with codebase).
- Documentation version increments on every phase completion (v1.3 after PH1, v1.4 after PH2, v1.5 = launch docs after PH3).

---

# 10. Monitoring Strategy

- **Health:** extend `/api/monitor/health` into `/health` (liveness), `/ready` (DB + Redis + provider reachability), `/live` per DEPLOYMENT.md.
- **Logging:** structured JSON logs (level, timestamp, request id, user id — never tokens/passwords/keys) replacing ad-hoc prints; log retention decision documented.
- **Error tracking:** Sentry (or self-hosted GlitchTip) for unhandled exceptions, worker failures, broker/payment/AI failures.
- **Metrics:** request latency, error rate, Socket.IO connection count, Redis pub/sub lag, market-event throughput, cron/job success. Prometheus-format endpoint acceptable initially.
- **Alerting:** minimum viable — health-check failure, error-rate spike, auth-failure spike (security signal), backup failure. Delivered to a monitored channel.

---

# 11. Recovery Strategy

- **RPO:** ≤ 24 h at launch (daily Mongo backups), target ≤ 1 h post-launch (point-in-time recovery / Atlas).
- **RTO:** ≤ 4 h at launch.
- **Backups:** daily/weekly/monthly Mongo per SECURITY.md; encrypted; restore drill executed and documented in PH2.11 — an untested backup does not count.
- **Rollback:** every deploy keeps the previous image tag warm; DB migrations must be backward-compatible one version (expand/contract pattern).
- **Incident response:** Detect → Alert → Investigate → Contain → Recover → Review → Document (SECURITY.md); postmortem template added in PH2.11.

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

| Area | Evidence Required | Gate |
|---|---|---|
| Security | PH1.12 certification report; pen-checklist results | Blocks launch |
| Infrastructure | PH2.12 report; staging soak (7 days, no Sev-1) | Blocks launch |
| Quality | PH3.12 report; regression protocol run | Blocks launch |
| Data | Restore drill record; ADR-021 compliance grep | Blocks launch |
| Documentation | PH3.10 sync report | Blocks launch |
| Performance | Baseline report vs targets | Advisory at launch; blocks 10k-user milestone |

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
| OR-6 | Email verification depends on production SMTP provider selection | Decision required during PH1.5; risk if deferred |
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

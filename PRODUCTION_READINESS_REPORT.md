# StockAssist AI — Production Readiness Report

> **⚠️ LIVING DOCUMENT.** The most recent update is **PH3.7b — Memory & Resource
> Stability** (2026-08-15), immediately below; the **PH1.12 security update**
> (2026-07-22) follows it and remains the standing release decision. The original
> **Sprint 12 baseline audit** (2026-07-17,
> verdict *NOT READY*) is preserved unchanged from the divider marked
> *"Sprint 12 Baseline Audit"* onward, as the historical record that seeded the
> Production Hardening program. Do not delete it.

---

# PH3.7b Update — Memory & Resource Stability (2026-08-15)

**Companion report:** `docs/performance/PH3_MEMORY_STABILITY.md` ·
**Sprint label:** "PH3.6 — Memory & Resource Stability"

**Status: PASS WITH CONDITIONS.** The platform's long-running resource behaviour
is now measured rather than assumed, and two P0 leaks that no previous sprint
could have seen were found and fixed.

**The finding that matters for release planning is methodological.** PH3.7's
load testing reported flat memory across more than 150,000 requests, and that
report was accurate. It was also structurally incapable of showing either defect
this sprint found, because both are dictionaries that gain a few hundred bytes
per event — less than the noise between two idle RSS samples. **A leak is a
shape, a count that only ever rises, not a size.** Any future readiness claim
about memory that rests on an RSS graph should be read with that in mind.

**Fixed before launch:**

* **A remotely-triggerable unbounded map.** The WebSocket manager kept one
  dictionary key per connection forever, and the key comes from an
  **unauthenticated** query parameter — so an anonymous caller could grow it at
  will, and only a process restart ever emptied it. Measured at 1,000 retained
  keys per 1,000 connect/disconnect cycles.
* **An unbounded per-user AI cache** holding multi-KB rendered context objects
  for every user who had ever sent a chat message, with a TTL that was consulted
  on read and enforced nowhere.
* **A market broadcast that could be silently dropped** to every client past a
  concurrent disconnect (PH3.7's L-2, now confirmed, reproduced and closed).
* **Four background loops with no shutdown path at all**, which kept running
  against the database, cache and HTTP pools while the shutdown handler was
  closing them — making every clean stop look like a crash in the logs.
* **A MongoDB pool that only ratcheted upward**, because idle connections were
  never reaped.

**Operationally, the important change is that the bounds are now visible.** Six
new metrics expose the counts these leaks grew in; before this sprint no
dashboard in the system could have shown either one. The first alert to
configure is `websocket_tracked_users` holding a floor above zero while
`websocket_connections` is at zero.

**Conditions on this status — all environmental, none an outstanding defect:**

1. `MONGO_SOCKET_TIMEOUT_MS` is deliberately unset and **must be baselined in
   staging**. Without a read timeout, a query against a wedged primary holds its
   request and connection indefinitely. A number was not invented here because
   one chosen without production data would abort legitimate work under load.
2. **Multi-worker resource behaviour is unmeasured.** Every figure in the
   resource budget is *per worker*, and each worker holds an independent copy of
   every in-process cache, so the budget multiplies.
3. **Multi-day continuous operation is unmeasured.** The soak is tens of
   minutes.
4. **Mongo TTL reaping of `sessions` and `rate_limits` under sustained write
   rate is unmeasured.** Both collections grow with every request; the TTL
   indexes exist and were verified present, but whether the reaper keeps up is a
   database-side question this sprint did not answer.
5. **Frontend bounds are asserted by tests, not heap-profiled** on a real
   all-day session.

**No regression:** backend 2,188 → **2,216** tests passing, PH1 security **452
unchanged**, frontend 319 → **324**, production build green. No trading logic,
AI decision logic, prompt, model selection or API contract was changed.

---

# PH1.12 Update — End of Phase 1 (Security)

**Date:** 2026-07-22 · **Phase:** PH1 — Production Security Hardening ·
**Companion report:** `docs/security/PH1_CERTIFICATION.md`

## Release Decision

> **Phase 1 (Security): ✅ CERTIFIED COMPLETE.**
> **Overall production deployment: ⛔ NO-GO (conditional).**

Phase 1 achieved its security objective. Every baseline critical/high **security**
finding is closed, and the PH1.11 residuals (F-1, F-2, F-3) are fixed this sprint.
Security re-score: **Authentication & Authorization 9.0**, **API & Transport
Security 8.5** — both clear the ≥ 8.0 exit gate.

Deployment is **still blocked**, but by **infrastructure and QA**, not security:
Phase 2 (Docker, CI/CD, production compose, DB/observability/DR) and Phase 3
(frontend tests, hermetic integration tests) are **not started**. The composite
readiness score is not yet at the ≥ 9.0 / no-category-< 8.0 launch bar.

## Status of the original Sprint-12 blockers

| # | Baseline blocker (2026-07-17) | Owning sprint | Status |
|---|-------------------------------|---------------|--------|
| 1 | Admin auto-login backdoor | PH1.1 | ✅ Removed |
| 2 | Google OAuth demo-user bypass | PH1.1/PH1.2 | ✅ Removed & hardened |
| 3 | CORS wildcard with credentials | PH1.4 | ✅ Fixed (`security/cors.py`) |
| 4 | Auth cookies not `Secure` | PH1.3 | ✅ Fixed (forced in prod) |
| 5 | Docker packaging broken | **PH2.1–2.3** | ⛔ **Open** — Dockerfiles/prod compose not yet written |
| 6 | No CI/CD | **PH2.5–2.7** | 🟡 Partial — `security-audit.yml` exists; no build/test/deploy pipeline |
| 7 | Mock data in admin analytics | **PH3.2** | ⛔ Open |
| 8 | Backend test failures / non-hermetic | **PH3.1** | 🟡 1 pre-existing engine test + legacy live-server tests remain |
| 9 | No frontend tests | **PH3.3** | ⛔ Open |
| 10 | Password policy + email verification | PH1.5/PH1.5b | ✅ Done (`EmailStr` tightening pending) |
| 11 | JWT lifetimes / rotation | PH1.6 | ✅ Done |
| — | Rate limiting | PH1.7 | ✅ Done |
| — | Dev tooling in runtime requirements | PH1.11 | ✅ Fixed (`requirements-dev.txt`) |

**Remaining release blockers are all Phase 2 / Phase 3** (items 5, 6, 7, 9).

## Final Architecture (security surface)

All cross-cutting security concerns are centralized in one audited package,
`backend/security/`, each module the single place its concern is enforced:

`cookies` · `cors` · `headers` · `passwords` · `recovery` · `jwt` · `sessions` ·
`csrf` · `rate_limit` · `secrets` · `audit` · **`roles` (F-1)** ·
**`identifiers` (F-2)**. Boot-time `secrets.validate_config()` fails closed
before the Mongo client or any router is constructed. `server.py` wires these in;
no security posture is set anywhere else. Authoritative design:
`SECURITY_ARCHITECTURE.md`.

## Operational Prerequisites (before any deployment — mostly Phase 2)

1. **Secrets provisioned** (Phase-aware, from `SECRET_REGISTRY`): `JWT_SECRET`
   (≥ required strength), `MONGO_URL`, `DB_NAME`, `APP_ENV=production`,
   `CORS_ALLOWED_ORIGINS`, and any enabled provider keys. Boot fails closed if a
   critical secret is missing/weak.
2. **`APP_ENV=production`** — forces `Secure` cookies and enables HSTS.
3. **TLS termination** in front of the app (HSTS assumes HTTPS).
4. **Email provider** (SMTP/SendGrid) for recovery flows — currently simulated
   (OR-6).
5. **Production Dockerfiles + compose** — *does not exist yet* (PH2.1–2.3).
6. **CI/CD gates** — build/test/deploy pipeline — *does not exist yet* (PH2.5–2.7).
7. **Managed MongoDB with backups + Redis** for production scale (PH2.8).

## Deployment Checklist (target — gated on Phase 2)

- [ ] Production Dockerfiles built (no `--reload`, no bind mounts, non-root) — **PH2.1/2.2**
- [ ] Production compose / orchestration separate from dev — **PH2.3**
- [ ] All secrets set in the deployment environment; `validate_config()` passes at boot
- [ ] `APP_ENV=production`; verify `Secure` cookies + HSTS on a live response
- [ ] TLS certificate valid; HTTP→HTTPS redirect in place
- [ ] `CORS_ALLOWED_ORIGINS` set to the real frontend origin(s); no `*`
- [ ] CI green: full backend suite, `pip-audit` (both requirements), `npm audit`, gitleaks
- [ ] DB migrations/indexes applied; managed backups enabled — **PH2.8**
- [ ] Health check (`/api/monitor/health`) wired to the orchestrator
- [ ] Smoke test: register → verify → login → refresh → logout; one admin action audited
- [ ] Rollback plan rehearsed (below)

## Rollback Checklist

- [ ] Identify last-known-good image tag / release
- [ ] Redeploy previous image (orchestrator rollback) — **PH2.7 CD provides one-command rollback**
- [ ] If a schema/index change shipped, apply its documented reverse step
- [ ] Rotate `JWT_SECRET` **only if** the incident involves token/secret exposure
      (this invalidates all sessions — intended for that case)
- [ ] Confirm health check green + smoke test on the rolled-back version
- [ ] Record incident + root cause; open a fix-forward ticket

## Backup Strategy (target — implemented in PH2.8, not yet in place)

- **MongoDB:** managed automated daily snapshots + point-in-time recovery;
  retention ≥ 30 days; periodic restore drills. Application data (users, trades,
  audit logs) is the system of record.
- **Audit logs** (`security_audit_logs`): retained per policy; append-only in
  practice; never contain secrets (recursive redaction at the sink).
- **Secrets:** stored in the platform secret manager, **not** in backups; rotate
  per `SECRETS.md §6`.
- **Config templates:** `.env.example` files are generated from the registry and
  version-controlled (no real values).

## Recovery Strategy (target — PH2.11 DR)

- **RPO/RTO** to be set with the managed-DB tier in PH2.8/2.11; interim target
  RPO ≤ 24 h (daily snapshot), RTO ≤ 4 h (restore + redeploy).
- **Restore drill:** restore latest snapshot to a staging DB, boot the app
  against it, run the smoke test — must be exercised before launch.
- **Credential-leak recovery:** `SECRETS.md §9` runbook (contain → assess → purge
  history → rotate). `JWT_SECRET` rotation invalidates all outstanding tokens by
  design.

## Recommendation

Close **Phase 1**. Begin **Phase 2 — Production Infrastructure & DevOps** at
PH2.1 (Backend Production Dockerfile). Re-evaluate the composite readiness score
at the end of Phase 2 and again at PH3.12 (final production certification).

---

# Sprint 12 Baseline Audit (historical — 2026-07-17)

Sprint: 12 — Production Readiness
Date: 2026-07-17
Branch: `sprint-r3-frontend-realtime`
Scope: Full audit against DEPLOYMENT.md, TESTING.md, SECURITY.md, INDEX.md checklists.

---

## Verdict

**NOT READY FOR PRODUCTION.**

The application core is in good shape — 341 backend tests pass, the frontend production
build succeeds with route-level code splitting, no `.env` files are committed, passwords
are bcrypt-hashed, admin routes are RBAC-guarded with audit logging, and a health
endpoint exists. However, there are **two critical authentication backdoors**, broken
Docker packaging, no CI/CD pipeline, and mock data in admin analytics. These are hard
blockers.

---

## Scorecard

| Checklist Item | Status | Notes |
|---|---|---|
| No mock data | ❌ FAIL | Admin analytics revenue/features are fabricated |
| No TODOs | ✅ PASS | Zero TODO/FIXME/HACK markers in source |
| Tests pass | ⚠️ PARTIAL | 341/347 backend pass; 6 failures (see below); **no frontend tests exist** |
| Performance targets | ⚠️ PARTIAL | Code splitting in place; no load-time/API-latency measurement done |
| Security review | ❌ FAIL | Critical auth backdoors, CORS misconfig, no rate limiting |
| Responsive | ⚠️ UNVERIFIED | Tailwind responsive classes used; no device-matrix testing performed |
| Accessible | ⚠️ PARTIAL | eslint-plugin-jsx-a11y configured; no automated a11y test run |
| Docker ready | ❌ FAIL | `docker-compose.yml` references `backend/Dockerfile` and `frontend/Dockerfile` — **neither exists** |
| CI/CD ready | ❌ FAIL | No `.github/workflows/` — no pipeline at all |
| Deployment ready | ❌ FAIL | Blocked by all of the above |

---

## Critical Blockers (must fix before any deployment)

### 1. Admin auto-login backdoor — enabled by default
`backend/server.py:3857` — `GET /api/auth/auto-login` issues **admin** access and
refresh tokens to any unauthenticated caller. The kill switch defaults to ON:

```python
if not os.environ.get("ENABLE_AUTO_LOGIN", "true").lower() in ("true", "1", "yes"):
```

Unless every environment explicitly sets `ENABLE_AUTO_LOGIN=false`, anyone on the
internet becomes admin with one GET request.
**Fix:** default to disabled, and additionally gate on a non-production environment
check (`NODE_ENV`/`APP_ENV != production`). Remove before launch.

### 2. Google OAuth demo-user bypass
`backend/server.py:2672` — `POST /api/auth/google/session`:
- Sending `code: "mock-code-for-testing"` (or any code when Google creds are unset)
  logs the caller in as `demo-user@alphapartner.com` — a real session in the real DB.
- The legacy `session_id` path calls a **third-party demo backend**
  (`demobackend.emergentagent.com`), and on *any* failure/timeout silently falls back
  to the demo user — so any random `session_id` yields a valid session.

**Fix:** delete the simulation fallbacks and the legacy third-party session exchange;
fail closed (401) when Google credentials are not configured.

### 3. CORS: wildcard origins with credentials
`backend/server.py:4668` — `allow_origins` defaults to `*` while
`allow_credentials=True`. Combined with cookie-based auth, any website can make
credentialed requests against the API.
**Fix:** require an explicit `CORS_ORIGINS` allowlist in production; no `*` default.

### 4. Auth cookies are not `secure`
`backend/server.py:193` — `set_auth_cookies` sets `secure=False` on both tokens.
Tokens will be sent over plain HTTP.
**Fix:** `secure=True` (env-driven for local dev), keep `httponly` + `samesite`.

### 5. Docker packaging is broken
`docker-compose.yml` builds `backend/Dockerfile` and `frontend/Dockerfile` — neither
file exists anywhere in the repo, so `docker compose up --build` fails. The backend
service also runs `uvicorn --reload` with a source bind-mount (dev mode) and the
compose file ships weak defaults: `JWT_SECRET: change_this_in_production_min_32_chars`
and a hardcoded n8n password (`alphapartner123`).
**Fix:** write both Dockerfiles (multi-stage frontend build → static server; backend
without `--reload` or bind mount), remove secret fallbacks, and split dev/prod compose files.

### 6. No CI/CD
No GitHub Actions workflows exist. DEPLOYMENT.md mandates lint → test → build →
security scan → deploy. Nothing is automated today.
**Fix:** add a minimal pipeline first (backend pytest + frontend build on PR), then
extend with lint/type-check/docker-build stages.

---

## High-Priority Issues

### 7. Mock data in admin analytics (violates Data Rules)
- `backend/server.py:4357` — `GET /api/admin/analytics/revenue` returns a fabricated
  30-day revenue series generated by a formula.
- `backend/server.py:4059` — admin overview `revenue_today = total_payments * 499`
  (placeholder) — commented as mock in code.
- `backend/server.py:4374` — feature analytics returns hardcoded usage percentages.

**Fix:** compute from the `payments` collection, or return honest empty states until
the payment system lands. Never render fabricated numbers in an admin console.

### 8. Backend test failures (6)
- `test_trading_engine.py::test_run_cycle_trails_and_books_targets` — **stale test**:
  `run_cycle` now returns a `closed_trades` key the assertion doesn't expect. Update
  the expected dict.
- `test_phase2/6/7` failures (5) — these are live-server integration tests hitting
  `http://localhost:8000` with real market data; they fail when the server isn't
  running. They should be marked/skipped (`pytest -m integration`) so the default
  suite is hermetic, and run separately in CI against a spun-up stack.

### 9. No frontend tests
Zero `*.test.*` files under `frontend/src`. TESTING.md requires component/page
coverage (80% minimum). At minimum, add smoke tests for auth flows, Dashboard render,
and the service layer before launch.

### 10. Missing password policy and email verification
`backend/models.py:32` — `UserCreate` accepts any string: no 12-char minimum, no
complexity rules (SECURITY.md mandates both), `email: str` instead of `EmailStr`.
Registration issues tokens immediately with no email verification.

### 11. JWT lifetimes diverge from SECURITY.md
Access token is 24 h (spec: 15 min); refresh is 7 days (spec: 30 days) with no
rotation or revocation on use. Acceptable to defer, but document the deviation or fix.

---

## Medium-Priority Issues

- **No rate limiting** anywhere (SECURITY.md requires a rate limiter layer). Auth
  endpoints especially need brute-force protection (e.g., `slowapi`).
- **`server.py` is a 4,823-line monolith** containing auth, admin, OAuth, AI, broker
  and analytics routes. Architecture docs require separation; split into routers as
  ongoing refactoring (not a launch blocker, but a scaling one).
- **Dev tooling in production requirements** — `backend/requirements.txt` pins
  `black`, `flake8` as runtime deps; move to a dev requirements file.
- **FastAPI deprecations** — `@app.on_event("shutdown")` (server.py:4817) should move
  to lifespan handlers before a future FastAPI upgrade breaks it.
- **Documentation/code mismatch** — DEPLOYMENT.md describes Node/Express + Vite +
  TypeScript; the actual stack is FastAPI (Python) + CRA/craco JavaScript. CLAUDE.md
  mandates TypeScript. The docs (or a documented decision) need to be reconciled —
  today the deployment handbook describes a different system than the one in the repo.

---

## What Is in Good Shape

- **Backend test depth:** 341 passing tests across trading engine, portfolio,
  real-time streams, morning report, webhooks, broker integration.
- **Frontend production build:** compiles cleanly, route-level code splitting active,
  largest chunks are reasonable.
- **Secrets hygiene:** no `.env` files committed; comprehensive `.gitignore` coverage;
  no hardcoded API keys found in source; `JWT_SECRET` read strictly from env at runtime.
- **Auth fundamentals:** bcrypt password hashing, httponly cookies, Bearer fallback,
  `require_admin` RBAC dependency, immutable admin audit log.
- **Operational endpoints:** `/api/monitor/health` exists; docker-compose has a Mongo
  healthcheck.
- **Code cleanliness:** zero TODO/FIXME markers in application source.

---

## Recommended Fix Order

1. Remove/disable both auth backdoors (items 1–2) — hours, not days.
2. Fix CORS + cookie `secure` flags (items 3–4).
3. Write the two Dockerfiles and a production compose file (item 5).
4. Add the minimal CI pipeline (item 6) so 1–5 stay fixed.
5. Replace admin analytics mock data with real aggregation or empty states (item 7).
6. Fix the stale trading-engine test; mark integration tests (item 8).
7. Add password policy + `EmailStr` validation (item 10).
8. Add rate limiting on auth endpoints.
9. Frontend smoke tests; then broader coverage.

Estimated effort to clear all launch blockers (1–7): roughly 3–5 focused engineering days.

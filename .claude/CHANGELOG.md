# StockAssist AI
## Changelog

This file records documentation-system versions and, from v1.0 launch onward, product release notes. Documentation versions apply to the `.claude/` documentation set as a whole.

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

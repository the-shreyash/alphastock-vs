# StockAssist AI — PH1 Security Certification Report

**Sprint:** PH1.12 — Security Certification (final Phase 1 sprint)
**Date:** 2026-07-22
**Certifying role:** Principal Release & Security Engineer
**Scope of certification:** Phase 1 — Production Security Hardening (PH1.1–PH1.12)
**Authoritative evidence base:** `.claude/SECURITY_ARCHITECTURE.md`, `.claude/SECURITY.md`,
`.claude/PRODUCTION_HARDENING.md`, `.claude/SECRETS.md`, and the backend test suite.

---

## 1. Executive Summary

Phase 1 set out to take a feature-complete MVP with **two active authentication
backdoors** and a security readiness of **2.0–3.0/10** to a defensible,
independently verifiable security posture. Across twelve sprints the program
centralized every security-relevant concern into a single audited package
(`backend/security/`), closed all baseline critical/high findings, and added
continuous supply-chain scanning.

This sprint (PH1.12) implemented the three residual hardening findings from the
PH1.11 verification (F-1 privilege escalation, F-2 unhandled identifier parsing,
F-3 supply-chain automation), executed the security verification checklist, and
re-scored the security categories.

**Result:** The **Phase 1 security objective is achieved.** No open
critical or high **security** findings remain. Authentication & Authorization
and API & Transport Security both re-score ≥ 8.0 — the PH1.12 exit gate.

**Important scope boundary:** This certifies the **security** phase (PH1). It is
**not** an authorization to deploy to production today. Actual deployment is
blocked by Phase 2 (Infrastructure & DevOps — no production Dockerfiles, no
CI/CD, no production compose) and Phase 3 (Quality Assurance — zero frontend
tests, non-hermetic integration tests). See §7 and the Production Readiness
Report for the release decision.

---

## 2. PH1 Sprint Inventory (what was delivered)

| Sprint | Title | Status | Module(s) / Artifact |
|--------|-------|--------|----------------------|
| PH1.1 | Authentication Backdoor Removal | ✅ Complete | server.py (auto-login + seed-admin removed) |
| PH1.2 | Google OAuth Production Hardening | ✅ Complete | server.py OAuth flow (state, id_token verify, allowlist) |
| PH1.3 | Cookie & Session Security | ✅ Complete | `security/cookies.py` |
| PH1.4 | CORS Hardening | ✅ Complete | `security/cors.py` |
| PH1.4b | Security Headers (HSTS/CSP/…) | ✅ Complete | `security/headers.py` |
| PH1.5 | Password Policy & Account Protection | ✅ Complete | `security/passwords.py` |
| PH1.5b | Email Verification & Account Recovery | ✅ Complete | `security/recovery.py` |
| PH1.6 | JWT Lifecycle & Refresh Rotation | ✅ Complete | `security/jwt.py`, `security/sessions.py` |
| PH1.7 | CSRF Protection & Rate Limiting | ✅ Complete | `security/csrf.py`, `security/rate_limit.py` |
| PH1.8/9 | Secrets & Supply-Chain Security | ✅ Complete | `security/secrets.py`, `security-audit.yml` |
| PH1.10 | Audit Logging & Security Monitoring | ✅ Complete | `security/audit.py` |
| PH1.11 | Dependency & Vulnerability Scanning | ✅ Complete | `security-audit.yml`, `dependabot.yml`, requirements split |
| PH1.12 | Security Certification (this report) | ✅ Complete | `security/roles.py`, `security/identifiers.py`, this report |

> Deferred within PH1 (non-blocking, tracked): **PH1.9 Real-Time/WebSocket
> Security** and **PH1.10b Admin Hardening & Session Management** — see §8.

---

## 3. PH1.12 Hardening Fixes (F-1, F-2, F-3)

### F-1 — Privilege escalation via the admin user editor *(High → Closed)*

- **Finding:** `PUT /api/admin/users/{id}` accepted `role` as an unchecked
  passthrough field. Any account with the `admin` role could promote any user —
  including themselves — to `admin` or `super_admin`.
- **Fix:** New `backend/security/roles.py` centralizes the role taxonomy
  (`ASSIGNABLE_ROLES` allowlist) and `validate_role_assignment(new_role,
  actor_role)`, which (a) rejects any role outside the allowlist with **400**,
  and (b) permits the admin-tier roles (`admin`, `super_admin`) **only** when the
  actor is a `super_admin` (**403** otherwise). Wired into `admin_update_user`.
- **Verification:** `tests/test_roles.py` — unit tests plus end-to-end
  regression proving an `admin` cannot escalate a user or self-promote (403,
  stored role unchanged), a `super_admin` can, and plan roles remain grantable by
  any admin.

### F-2 — Unhandled ObjectId parsing → 500s *(Medium → Closed)*

- **Finding:** 43 raw `ObjectId(...)` call sites; those parsing **untrusted**
  path/body identifiers raised `bson.errors.InvalidId` uncaught, surfacing as
  HTTP 500 (an implementation error leaking to the client) on any malformed id.
- **Fix:** New `backend/security/identifiers.py` provides `parse_object_id(value,
  resource)` — the single boundary where an untrusted id becomes an `ObjectId`,
  returning a clean **400** ("Invalid `<resource>` id", never echoing the input)
  otherwise. Also closes the surprising `ObjectId(None)` → *new random id*
  behavior. Applied at every trust boundary: the admin user/ticket/flag/
  announcement editors and the trade/notification/paper endpoints. Trusted ids
  (verified JWT `sub`, `_id` read back from Mongo) intentionally stay raw and are
  documented as such.
- **Verification:** `tests/test_identifiers.py` (valid/passthrough/malformed/
  non-string/no-echo) + endpoint regression (`malformed_user_id_returns_400`).

### F-3 — Supply-chain automation & runtime/dev dependency split *(Medium → Closed)*

- **Finding (from PH1.11 partial):** CI auditing, pinning, and CVE patches
  landed in PH1.9, but Dependabot, the `requirements-dev.txt` split (M14), and a
  documented triage SLA were outstanding.
- **Fix:**
  - `.github/dependabot.yml` — weekly PRs for `pip` (`/backend`), `npm`
    (`/frontend`), `github-actions`; docker staged for PH2.1/2.2.
  - `backend/requirements-dev.txt` — dev/CI tooling (`pytest`, `black`,
    `flake8`, `isort`, `mypy` + their exclusive transitive deps, each verified
    dev-only via `pip show … Required-by`) split out of the runtime set so the
    production image ships no dev tooling.
  - `security-audit.yml` — now audits **both** requirements files and runs
    `pip check` on the runtime-only install (which also proves the split).
  - Triage SLA (critical blocks release · high 7d · medium 30d · low 90d)
    documented in `SECRETS.md §7` and `TESTING.md`.
- **Verification:** `pip check` clean on the runtime set; YAML validated; local
  `pip-audit` unchanged (deferred CVEs still tracked in `SECRETS.md §8`).

---

## 4. Security Controls Verification

Executed against the codebase and the hermetic backend suite (evidence in
parentheses).

| Control | Status | Evidence |
|---------|--------|----------|
| **Auth backdoors removed** (auto-login, seed-admin) | ✅ | grep: no `auto-login`/`backdoor`/`bypass_auth`/`ENABLE_AUTO_LOGIN` in source |
| **No debug mode / reload in app code** | ✅ | grep: no `debug=True`/`reload=True` in `server.py`/`services` |
| **No hardcoded/test secrets** | ✅ | grep clean; compose defaults externalized & required (`JWT_SECRET`, n8n pw) |
| **Password policy + bcrypt** | ✅ | `security/passwords.py`; `test_password_policy.py` |
| **Role least-privilege (F-1)** | ✅ | `security/roles.py`; `test_roles.py` |
| **JWT lifecycle + refresh rotation + reuse detection** | ✅ | `security/jwt.py`, `sessions.py`; `test_jwt_sessions.py` |
| **Session revocation on credential change** | ✅ | `revoke_all_for_user` + `password_changed_at`; `test_recovery.py` |
| **Cookies: HttpOnly always, Secure forced in prod, SameSite** | ✅ | `security/cookies.py` (`is_production` override); `test_cookie_security.py` |
| **CORS: no wildcard-with-credentials, env allowlist, fail-closed** | ✅ | `security/cors.py`; `test_cors_hardening.py` |
| **Security headers: HSTS (prod), strict CSP, XFO/nosniff/Referrer/Permissions** | ✅ | `security/headers.py`; `test_security_headers.py` |
| **CSRF: signed double-submit, cookie-auth mutations** | ✅ | `security/csrf.py`; `test_csrf.py` |
| **Rate limiting: per-endpoint policies + progressive lockout** | ✅ | `security/rate_limit.py`; `test_rate_limit.py` |
| **Input validation on untrusted ids (F-2)** | ✅ | `security/identifiers.py`; `test_identifiers.py` |
| **Boot-time config validation, fail-closed** | ✅ | `security/secrets.py` `validate_config`; `test_secrets.py` |
| **Audit logging: taxonomy, redaction, fail-safe sinks** | ✅ | `security/audit.py`; `test_audit.py` |
| **Secrets never committed; `.env` git-ignored; only `.env.example` tracked** | ✅ | `.gitignore` (`!.env.example`); gitleaks + tracked-`.env` guard in CI |
| **Dependency scanning + Dependabot + triage SLA (F-3)** | ✅ | `security-audit.yml`, `dependabot.yml`, `SECRETS.md §7` |

---

## 5. OWASP Top 10 (2021) — Posture

| # | Category | Status | Notes |
|---|----------|--------|-------|
| A01 | Broken Access Control | ✅ Addressed | RBAC on admin routes; F-1 role least-privilege; ownership checks on user resources |
| A02 | Cryptographic Failures | ✅ Addressed | bcrypt(12) passwords; HS256 JWTs with rotation; Secure cookies in prod; HSTS |
| A03 | Injection | ✅ Addressed | Mongo via Motor (no string-built queries); F-2 id validation; Pydantic models |
| A04 | Insecure Design | 🟢 Reasonable | Centralized security package; threat model in SECURITY_ARCHITECTURE.md |
| A05 | Security Misconfiguration | ✅ Addressed | Fail-closed config validation; no debug; strict headers; hardened CORS |
| A06 | Vulnerable Components | ✅ Addressed | Full pinning; pip-audit/npm audit CI; Dependabot; deferred CVEs tracked (§8) |
| A07 | Identification & Auth Failures | ✅ Addressed | Backdoors removed; OAuth hardened; rate-limited login w/ lockout; recovery flow |
| A08 | Software & Data Integrity | 🟢 Reasonable | Signed tokens; lockfiles; gitleaks. CD signing is PH2. |
| A09 | Logging & Monitoring Failures | ✅ Addressed (PH1) | Centralized audit log w/ redaction. Metrics/alerting is PH2.10. |
| A10 | SSRF | 🟢 Low exposure | Outbound calls target fixed provider hosts; no user-supplied URL fetch on the auth surface |

No OWASP category is in a failing state for the PH1 security scope.

---

## 6. Test Summary

- **Hermetic backend suite: 626 passed, 1 failed, 0 errors.**
- The single failure — `test_trading_engine::test_run_cycle_trails_and_books_targets`
  — is a **pre-existing, documented** trading-engine math assertion unrelated to
  any security change (TASK.md; scheduled for PH3.1). It exercises `run_cycle`
  against a FakeDB and touches none of the PH1.12 code paths.
- **New in PH1.12: 48 tests** (`test_roles.py` + `test_identifiers.py`), all green.
- The full run additionally reports failures/errors from the legacy
  `requests`-based integration files (`test_backend.py`, `test_phase*.py`), which
  require a **live dev server** and are environmental (ConnectionError), not code
  regressions. Migrating these to hermetic in-process tests is PH3.1.

---

## 7. Security Re-Score

| Category | Baseline (2026-07-17) | Post-PH1 | Gate | Verdict |
|----------|----------------------:|---------:|:----:|:-------:|
| Authentication & Authorization | 2.0 | **9.0** | ≥ 8.0 | ✅ PASS |
| API & Transport Security | 3.0 | **8.5** | ≥ 8.0 | ✅ PASS |
| Secrets & Configuration | 6.0 | **8.5** | — | ✅ |
| Observability (security) | 3.5 | **7.0** | — | 🟡 (metrics = PH2.10) |

**PH1.12 acceptance criteria — "authn ≥ 8.0 and API security ≥ 8.0, no open
critical/high security findings" — are MET.**

Categories intentionally **out of PH1 scope** and still low (they gate the
overall release, not the security certification): Packaging & Deployability
(1.0), CI/CD (0.0), Testing composite (frontend 0). These are Phase 2 / Phase 3.

---

## 8. Known Limitations & Residual Risk (security scope)

| ID | Item | Severity | Disposition |
|----|------|----------|-------------|
| OR-6 | Email delivery runs in **simulated** mode until a real SMTP/SendGrid provider is provisioned | Medium | Accepted for PH1; recovery flow is provider-agnostic and ready. Provision before launch. |
| — | Deferred CVEs: `starlette` (pinned by FastAPI), `litellm` (AI dep), `ecdsa` (no fix; we use PyJWT HS256, not ECDSA) | Medium/Low | Accepted with documented remediation plan — `SECRETS.md §8` |
| — | `email: str` not yet `EmailStr` | Low | PH1.5b follow-up; cosmetic validation tightening |
| PH1.9(rt) | Real-Time / WebSocket connection & room authorization | Medium | Deferred within PH1; Socket.IO auth handshake — schedule in PH1 tail or PH2 |
| PH1.10b | Admin session management (force-logout, session list) | Medium | Deferred; RBAC + audit already in place |
| — | MFA | Low (design-only) | Post-launch roadmap |

None of the above is an open **critical or high** security finding. F-1/F-2/F-3
are closed.

---

## 9. Certification Decision

> **PHASE 1 (PRODUCTION SECURITY HARDENING): CERTIFIED COMPLETE.**
>
> The PH1 security objective is achieved. All baseline critical/high security
> findings and the PH1.11 residuals (F-1, F-2, F-3) are closed. The re-score
> meets the exit gate (authn 9.0, API security 8.5). Residual items are Medium or
> lower, tracked, and accepted.
>
> This is a **security** sign-off. It does **not** authorize production
> deployment: the release remains blocked by Phase 2 (Infrastructure & DevOps)
> and Phase 3 (Quality Assurance). See the Production Readiness Report.
>
> **Recommendation:** Close Phase 1. Proceed to **Phase 2 — Production
> Infrastructure & DevOps**, starting with PH2.1 (Backend Production Dockerfile).

**Signed:** Principal Release & Security Engineer — 2026-07-22
**Next review:** On completion of PH2 (infrastructure) and again at PH3.12 (final
production certification), when the composite readiness score is re-evaluated
against the ≥ 9.0 / no-category-< 8.0 launch definition.

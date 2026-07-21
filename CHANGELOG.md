# Changelog

All notable changes to the StockAssist AI project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Identity Recovery (PH1.8):** `backend/security/recovery.py` — the single source of truth for single-use, expiring email-verification and password-reset tokens. Each token is a signed handle `<token_id>.<HMAC>` bound to one user + one purpose, backed by an authoritative `recovery_tokens` record enforcing expiry and atomic single-use (replay-safe); a fresh issue invalidates the user's prior unused token of that purpose. Secret: `RECOVERY_SECRET` else `JWT_SECRET`. Lifetimes: verification 24h, reset 30 min (env-overridable).
- **Recovery endpoints (PH1.8):** `POST /api/auth/verify-email`, `/verify-email/request`, `/forgot-password`, `/reset-password`, `/change-password`. Public flows return an identical generic response (no email enumeration); a reset or change enforces the PH1.5 password policy, revokes every session, and bumps `password_changed_at` (full sign-out on every device).
- **Email verification status (PH1.8):** user model gains `email_verified` / `email_verified_at` / `verified_by`; new email/password accounts start unverified and are emailed a link (Google accounts are verified on creation/link). Three branded email templates (`EMAIL_VERIFICATION`, `PASSWORD_RESET`, `PASSWORD_CHANGED`) and `recovery_tokens` startup indexes (unique `token_id`, `(user_id,purpose)`, TTL on `expires_at`).
- **CSRF Protection (PH1.7):** `backend/security/csrf.py` — a signed double-submit CSRF token bound to the session, enforced via `CSRFMiddleware` on cookie-authenticated, state-changing requests (Bearer requests are exempt by construction, so no frontend change was required). Failures return `403`.
- **Centralized Rate Limiting (PH1.7):** `backend/security/rate_limit.py` — one limiter with named per-endpoint policies (login 5/15min, register 5/hour, refresh 20/min, authenticated API 120/min per user, public API 60/min per IP), a pluggable `RateLimitStore` (MongoDB now, Redis-ready), progressive lockout with automatic expiry, and a platform-wide `RateLimitMiddleware`. Every rejection carries `Retry-After`.
- **Tests:** `backend/tests/test_recovery.py` (28), `backend/tests/test_csrf.py` (25) and `backend/tests/test_rate_limit.py` (30) — hermetic coverage of the identity-recovery, CSRF and rate-limit matrices.

### Changed
- **Auth endpoints:** login/register/refresh now use the centralized limiter; login/register/OAuth and refresh issue the CSRF cookie; logout clears it. Register now returns an additive `email_verified` field and emails a verification link out-of-band; the three public recovery endpoints are CSRF-exempt (they carry their own single-use authorization). No breaking public-API contract change.
- **Middleware pipeline:** CSRF and rate-limit middleware wired inside CORS/security-headers so `403`/`429` responses remain browser-readable and consistently hardened.

### Removed
- **Inline login lockout:** the ad-hoc `db.login_attempts` mechanism was folded into the centralized limiter and removed (its startup index dropped; new `rate_limits` collection + TTL added).

---

## [1.2.0] - 2026-07-17

### Added
- **Developer Hardening Specifications:** Introduced `PRODUCTION_HARDENING.md` describing risk matrices (R-01 through R-15), launch readiness scorecards, and definitions of production readiness.
- **Hardening Roadmap:** Created `PRODUCTION_ROADMAP.md` covering the PH1 (Security), PH2 (DevOps), and PH3 (QA) sprints.
- **Dev-Only Admin Seed:** Added `backend/scripts/seed_dev_admin.py` to allow dev environment database seeding without resorting to static file overrides or production security risks.
- **Backdoor Verification Suite:** Added `backend/tests/test_auth_hardening.py` checking auto-login endpoints, Google OAuth invalid session configurations, and me/refresh endpoints.

### Changed
- **Roadmap Sequence:** Product Phase 3 through Phase 9 marked as blocked pending exit criteria completion of the Production Hardening tracks.
- **Fixture Authentication:** Test suites `test_phase5`, `test_phase6`, and `test_phase7` modified to authenticate strictly via authenticated credentials on `/api/auth/login` rather than relying on auto-login configurations.

### Removed
- **Authentication Backdoor:** Removed `/api/auth/auto-login` endpoints and the corresponding `ENABLE_AUTO_LOGIN` environment toggle.
- **OAuth Fallback Backdoors:** Deleted demo fallback hooks and invalid `session_id` tokens previously directing traffic to `demobackend.emergentagent.com`.
- **Boot Credentials Seeding:** Stopped the automated generation of files like `memory/test_credentials.md` and server reboot passwords.

---

## [1.1.0] - 2026-07-16

### Added
- **Multi-Source Market Support:** Introduced `MARKET_DATA_ARCHITECTURE.md` specifying provider-independent architectures.
- **Source Manager:** Built failover routing mechanisms supporting broker WebSockets, direct licensed feeds, Yahoo Finance, and cached snapshots.
- **Visual Freshness Indicators:** Front-end components updated to render warnings when data falls back to delayed tiers.

### Changed
- **Decoupled Paid Intelligence:** Restructured subscription logic so premium subscription fees apply exclusively to AI analytical tooling (Morning Reports, Debate Engine) rather than basic feed listings.

---

## [1.0.0] - 2026-05-26

### Added
- **Interactive AI Debate Engine:** Built dual debating between Claude and Gemini on specific stock targets.
- **Broker Connections:** Initial Zerodha Kite Connect integration allowing portfolio sync and one-click order placement.
- **Technical Indicator Scanning:** Scanner services tracking momentum, volume alerts, and risk levels.
- **Core Platform:** User authentication, portfolio tracking, trade monitoring, and SIP advisory pages.
- **Admin Dashboard:** Initial administrative analytics platform (currently using simulated revenue aggregates).

# Changelog

All notable changes to the StockAssist AI project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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

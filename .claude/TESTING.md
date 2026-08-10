# StockAssist AI
## Testing Documentation

Version: 1.1

Status: Active Development

Last updated: 2026-08-10 (PH3.3)

---

# How to read this document

This document is the testing **policy**: what must be tested, to what standard,
and what quality gates a release must pass. Much of it describes the intended
end state rather than what exists today — that is deliberate, and each such
section now says so.

The testing **mechanics** — how the backend suite is actually organized, which
command to run, what each marker means, how isolation is enforced — live in
`docs/testing/TEST_ARCHITECTURE.md`. When the two disagree about what exists,
TEST_ARCHITECTURE.md is describing reality and this document is describing the
target.

**Current state in one line (PH3.3, 2026-08-10):** the backend has 2,245 tests;
`pytest` runs 2,150 of them hermetically and green in ~2m46s with no server,
database, credentials or network; 95 live-server tests are classified and skip
cleanly without a deployment; the frontend has 313 tests across 17 suites
(PH3.2), green in ~8s.

---

# Purpose

This document defines the complete testing strategy for StockAssist AI.

Testing ensures that every feature, API, AI workflow, broker integration, payment flow, and user interaction works correctly before reaching production.

Quality is everyone's responsibility.

Testing is required before deployment.

---

# Testing Goals

Prevent Bugs

Protect Users

Ensure Reliability

Verify AI Responses

Validate Market Data

Secure Payments

Verify Broker Integrations

Maintain Performance

Support Continuous Deployment

---

# Testing Philosophy

Every feature must be tested before release.

Testing should be:

Automated

Repeatable

Reliable

Fast

Independent

Documented

---

# Testing Pyramid

                End-to-End Tests
                     ▲
               Integration Tests
                     ▲
                 Unit Tests

Most tests should be unit tests.

End-to-end tests should cover critical user journeys.

---

# Test Architecture (as built, PH3.1)

```
Developer
   │
   ├── pytest                          ← the default. 2,150 tests, ~2m46s
   │     │                               no server, no database, no network
   │     ├── Unit                      (services, engines, pure logic)
   │     ├── Security                  (452 tests — PH1 controls)
   │     └── Hermetic API / Integration (FastAPI TestClient + FakeDB)
   │           ├── authz + validation sweeps derived from the live route
   │           │   table (PH3.3) — 126 protected routes, 29 admin routes
   │           ├── provider-failure matrices (market, AI, broker)
   │           └── trading lifecycle asserted at the database level
   │
   ├── pytest -m integration           ← 95 tests, needs a running deployment
   │     ├── Mongo   (real, through the app)
   │     └── Redis   (real, through the app)
   │
   └── Live / E2E
         │
         └── Running deployment
               ├── real market data, real AI, real brokers
               └── E2E journeys: none yet (PH3.9)
```

CI executes the deterministic suite on every push and pull request. The
integration layer needs a booted stack, which PH2.6 owns provisioning; until
then it runs only on demand.

Mechanics, fixtures and the full command set:
`docs/testing/TEST_ARCHITECTURE.md`.

---

# Testing Levels

Unit Testing

Integration Testing

API Testing

End-to-End Testing

Performance Testing

Security Testing

Accessibility Testing

AI Validation Testing

Broker Testing

Regression Testing

User Acceptance Testing

---

# Unit Testing

Purpose

Test individual functions and components.

Examples

Utility Functions

React Components

Hooks

Services

Validators

Business Logic

Expected Coverage

Minimum 80%

Recommended 90%

---

# Frontend Testing

**Status: implemented (PH3.2, 2026-08-10).** 313 tests across 17 suites, green
in ~8s. Full detail: `docs/testing/PH3.2_FRONTEND_TEST_CERTIFICATION.md`.

## Framework

**Jest 27 + React Testing Library 16**, run through `craco test` — the runner
already shipping inside `react-scripts`. No second framework was introduced.

Vitest was the documented target above and was *not* adopted: it would have
meant a parallel build pipeline (esbuild) alongside the webpack/CRA one that
actually ships the app, so tests would run against a different transform than
production. Revisit only if the build itself migrates to Vite.

| Tool | Role |
|------|------|
| `jest` (via `react-scripts`) | runner, jsdom environment, coverage |
| `@testing-library/react` | render + query by accessible role/text |
| `@testing-library/user-event` | realistic user interaction |
| `@testing-library/jest-dom` | DOM/accessibility matchers |
| `axios-mock-adapter` | request interception at the transport boundary |

## Commands

Run from `frontend/`:

| Purpose | Command |
|---------|---------|
| Run once | `yarn test` |
| Watch (inner loop) | `yarn test:watch` |
| Coverage | `yarn test:coverage` |
| CI mode | `yarn test:ci` |
| One file | `yarn test --testPathPattern=Login` |

## Architecture

Two layers, both in-process; no browser and no running backend.

```
frontend/src/
├── setupTests.js              ← jsdom polyfills + network isolation
├── test-utils/
│   ├── index.js               ← renderWithProviders, renderAppAt, auth helpers
│   ├── apiMock.js             ← installApiMock, HTTP codes, pending()
│   └── fixtures.js            ← deterministic test data
├── **/__tests__/*.test.js(x)  ← unit + component tests, beside their subject
└── __tests__/                 ← cross-screen integration (routing, auth flow)
```

**Layer 1 — unit/component.** Formatters, the API client's interceptors, the
realtime store's reducers, and each critical screen rendered in isolation.

**Layer 2 — integration.** `routing.test.jsx` and `authFlow.integration.test.jsx`
drive the application's *real* route table (`AppRouter`, exported from App.js for
this purpose), so a guard removed from the route declaration fails the suite.

**Layer 3 — E2E.** Deliberately not started in PH3.2; see Known Gaps in the
certification document.

## Mocking strategy

Every network call goes through the single axios instance in
`src/services/api.js`. `axios-mock-adapter` replaces that instance's *adapter* —
the last step before a request leaves the process. Everything above it runs for
real: the bearer-token request interceptor, the 401 silent-refresh interceptor,
every service module and every component.

MSW was evaluated and rejected: CRA 5 / Jest 27 predate `package.json#exports`
resolution, and MSW v2 is exports-only ESM needing Web-streams polyfills under
jsdom. Adapter interception is the smaller tool for the same job here.

`onNoMatch: "throwException"` is the default, so an unstubbed request fails
loudly by name rather than hanging.

> **Trap.** `axios-mock-adapter` matches handlers **in registration order**.
> Register specific routes *before* `stubRemainingWith()`, or the catch-all
> answers them and the test passes for the wrong reason.

No test can reach a real service: `setupTests.js` points the axios base URL at a
fake host, replaces `fetch` with a rejecting stub, and substitutes an inert
`WebSocket` so `RealtimeProvider` never opens a socket. Live-data behaviour is
exercised by writing directly into the Zustand realtime store.

## What is covered

Authentication (login, registration, logout, session restore, expiry, Google
OAuth callback), routing and route guards including admin access control, the
dashboard shell, paper trading order entry, the AI workspace, the watchlist,
notifications, the admin dashboard, and the realtime store. Each critical screen
is asserted in all four states: loading, success, empty and error.

---

# Backend Testing

Framework

**pytest** (8.0+, currently 9.0.3). This section previously said "Vitest / Jest",
which was never true of this backend — it is Python/FastAPI. Corrected in PH3.1.

Authoritative mechanics: `docs/testing/TEST_ARCHITECTURE.md`.

Commands (from `backend/`, all verified working):

| Goal | Command |
|------|---------|
| Default hermetic suite | `pytest` |
| Fast inner loop | `pytest -m "not slow"` |
| Security regression | `pytest -m security` |
| Integration / live | `pytest -m integration` |
| Integration, CI-strict | `REQUIRE_LIVE_BACKEND=1 pytest -m integration` |
| Coverage | `pytest --cov` |

Markers: `integration`, `live`, `e2e`, `security`, `slow`, `requires_db`,
`requires_redis`, `allow_network` — registered in `backend/pyproject.toml`,
applied mechanically by `backend/tests/conftest.py`. `--strict-markers` turns a
typo into a collection error.

## API test architecture (PH3.3)

Nine hermetic API suites, 1,134 tests, all in-process via `TestClient` + `FakeDB`:

| Suite | Tests | Covers |
|---|---:|---|
| `test_api_authz.py` | 307 | Authentication + authorization over the **live route table** |
| `test_api_validation.py` | 552 | Malformed input over the **live route table** |
| `test_api_market_data.py` | 68 | Market-provider failure matrix + containment |
| `test_api_ai.py` | 48 | AI-provider failure matrix + containment |
| `test_api_admin.py` | 39 | Admin control plane, audit records, empty-DB rendering |
| `test_api_errors.py` | 38 | Error envelope, rate-limit attachment, DB failure |
| `test_api_trading.py` | 35 | Order lifecycle asserted at the database level |
| `test_api_migrated.py` | 28 | Converted from the live-server suites |
| `test_api_contract.py` | 19 | PH3.1 conversions |

**The route table is read, not written down.** `tests/_routes.py` inspects
`server.app.routes` at collection time and classifies each route by its resolved
**dependency graph** — "protected" iff `get_current_user` is in its dependency
tree, "admin" iff `require_admin` is. Three suites parametrize over that
classification, so a new endpoint gets authorization tests automatically and one
that ships without its dependency turns them red. Guard tests fail if the
derived lists ever empty, so the sweeps cannot silently vanish and report green.

One parametrized case per route, not one loop: a failure names the route, and
each case gets a fresh `fake_db` and therefore its own rate-limit counter (a
single test issuing 126 anonymous requests would trip the 60/min anonymous
limiter partway through).

**Assert each guarantee at the layer that provides it.** The routes contain no
error handling for provider failures, and correctly so — containment lives at the
transport boundary (`fetch_yahoo_quote` catches everything and returns `None`;
provider adapters convert SDK exceptions into `AIResponse.error`). Tests that
inject a *raise* at the top of the stack assert a state production cannot enter.
The failure matrices therefore use only reachable results, and the containment
itself is asserted directly at the `try/except` that provides it. Full rationale:
`docs/testing/PH3.3_BACKEND_TEST_CERTIFICATION.md` §10.1.

### Fixtures (PH3.3 additions)

`other_user`/`other_headers` (horizontal-escalation victim), `admin_user`/
`admin_headers`, `super_admin_user`/`super_admin_headers`,
`authenticated_client`, `admin_client`, `super_admin_client`. All four
principals come from one `_seed_user` helper so they differ in exactly one field
(`role`); tokens are minted by the app's own `create_access_token`.

> **Trap.** `fake_db` must patch **every** database handle.
> `services.broker_engine` keeps its own (`broker_engine.db`); before PH3.3 it
> was unpatched, so all 33 broker routes talked to the real Motor client during
> "hermetic" tests and failed as `RuntimeError: Event loop is closed` — which
> reads like an application async bug and is really an unpatched dependency.

> **Trap.** `monkeypatch.setattr(instance, "method", ...)` for a method defined
> on the *class* leaves a permanent instance attribute after teardown, shadowing
> the class attribute. Any later class-level patch of that object is then
> silently ignored. Patch the same object every other test in the area patches.

> **Trap.** `tests/_fakedb.py` raises `UnsupportedQuery` for any Mongo operator
> it does not model. It previously *ignored* them, which meant a filter meant to
> narrow a result set silently matched the whole collection. If you hit this,
> extend the double — do not assert around it.

Hermeticity is enforced, not assumed: `tests/_testenv.py` installs a fixed
synthetic environment (and disables `.env` loading) before `server` is
imported; `tests/_netguard.py` blocks non-loopback sockets for every test not
marked `integration`/`live`/`e2e`/`allow_network`; `tests/_fakedb.py` replaces
MongoDB. No test uses a real credential, a real database, or a real API.

Test

Controllers

Services

Middleware

Routes

Authentication

Authorization

Validation

Database Layer

Business Logic

---

# API Testing

Test Every Endpoint

Authentication

Authorization

Validation

Success Responses

Error Responses

Pagination

Filtering

Sorting

Rate Limiting

Performance

---

# Integration Testing

Purpose

Verify communication between services.

Examples

Frontend ↔ Backend

Backend ↔ MongoDB

Backend ↔ Redis

Backend ↔ AI

Backend ↔ Broker

Backend ↔ Payment

---

# End-to-End Testing

Framework

Playwright

Critical User Flows

User Registration

Login

Connect Broker

Search Stock

View Dashboard

Generate Morning Report

Chat with AI

Paper Trade

Backtest Strategy

Upgrade Subscription

Purchase Credits

Logout

Admin Login

---

# AI Testing

Validate

Response Quality

Response Time

Prompt Accuracy

Context Retention

Memory

Portfolio Analysis

Trade Suggestions

Morning Reports

AI Debate

AI Reflection

Verify

No hallucinated portfolio data

No invalid recommendations caused by missing data

Proper error handling

---

# Broker Integration Testing

Test

OAuth Flow

Portfolio Sync

Holdings Sync

Order Placement

Order Modification

Order Cancellation

Trade History

WebSocket

Token Refresh

Session Expiry

API Failure

Rate Limits

---

# Payment Testing

Test

Checkout

Webhook Verification

Subscription Activation

Credit Purchase

Invoice Generation

Refund Flow

Payment Failure

Renewals

Cancellation

---

# Market Engine Testing

Verify

Live Price Updates

Market Scanner

Ranking Engine

Sector Analysis

Morning Report Data

News Processing

Cache

WebSocket Streams

---

# Performance Testing

Targets

Dashboard

<2 Seconds

Search

<500ms

API

<500ms

Scanner

<10 Seconds

Morning Report

<60 Seconds

Portfolio Load

<2 Seconds

---

# Load Testing

Simulate

100 Users

500 Users

1000 Users

5000 Users

10000 Users

Measure

Response Time

CPU

Memory

Database

Redis

Worker Queue

WebSocket Stability

---

# Stress Testing

Verify behavior during

Traffic Spike

Market Open

Breaking News

Large AI Usage

Large Scanner Requests

Mass Notifications

---

# Security Testing

Authentication

Authorization

Rate Limiting

JWT

CSRF

XSS

Injection

Session Management

Secrets

Broker Tokens

Payment Security

OWASP Top 10

---

# Dependency Vulnerability Triage (PH1.11)

Supply-chain scanning is continuous: the `security-audit` GitHub Actions
workflow runs `pip-audit --strict` (runtime + dev requirements), `npm audit`,
`pip check`, and `gitleaks` on every push/PR and weekly; Dependabot
(`.github/dependabot.yml`) opens weekly update PRs for pip, npm, and
github-actions. Every advisory is triaged against this SLA (time from surfaced
to merged fix or recorded acceptance in SECRETS.md §8):

| Severity | SLA | Merge gate |
|----------|-----|------------|
| Critical | Immediate | **Blocks merge and release** — never ship a known critical |
| High | 7 days | Accepted-risk entry in SECRETS.md §8 if not fixed in time |
| Medium | 30 days | SECRETS.md §8 backlog |
| Low | 90 days | SECRETS.md §8 backlog |

Authoritative copy of the policy: SECRETS.md §7 (Dependency & supply-chain
policy). Dev/CI tooling lives in `requirements-dev.txt` and is never installed
into the production runtime image.

---

# Accessibility Testing

Verify

Keyboard Navigation

Focus States

ARIA Labels

Screen Reader

Contrast Ratio

Reduced Motion

Responsive Text

WCAG AA Compliance

---

# Mobile Testing

Test

Android

iOS

Tablets

Responsive Layout

Touch Gestures

Navigation

Charts

Forms

Performance

---

# Browser Testing

Support

Chrome

Edge

Firefox

Safari

Latest Stable Versions

---

# Database Testing

Verify

Indexes

Relationships

Validation

Soft Delete

Migration

Backup

Recovery

Performance

---

# Redis Testing

Verify

Caching

Expiration

Invalidation

Sessions

Rate Limits

Queue

---

# Notification Testing

Email

Browser Notifications

Push Notifications (Future)

Retry Logic

Delivery Status

---

# Regression Testing

Run before every release.

Ensure previous functionality still works.

Focus

Authentication

Dashboard

Portfolio

Trading

AI

Subscriptions

Admin Portal

---

# Smoke Testing

Verify

Application Starts

Database Connected

Redis Connected

API Healthy

Frontend Loads

Login Works

---

# User Acceptance Testing

Verify

Business Requirements

User Experience

Design Consistency

Performance

Accessibility

Documentation

---

# Test Data

Use

Dedicated Test Database

Sandbox Brokers

Sandbox Payment Gateway

Mock AI Responses

Synthetic Users

Never test on production user data.

## As implemented (PH3.1)

The backend hermetic suite needs no test database at all: `tests/_fakedb.py`
replaces MongoDB with a function-scoped in-memory double, so isolation and
cleanup are structural rather than procedural. `MONGO_URL`/`DB_NAME` still point
at an unmistakably-named test target (`stockassist_pytest`) so an accidental
connection cannot reach development data.

Every third-party credential is blanked by `tests/_testenv.py`, so no broker,
payment, AI or messaging provider is ever configured during a hermetic run —
and `tests/_netguard.py` blocks the socket if something tries anyway.

Fixture data is obviously synthetic and labelled: `@example.com` addresses,
`TEST`-prefixed titles and notes, round market numbers. PH3.1 removed the
hardcoded `admin@alphapartner.com` / `admin123` pair from five test files;
live-server credentials now come from `TEST_ADMIN_EMAIL` /
`TEST_ADMIN_PASSWORD` with no defaults.

One test in the repository has an irreversible outward-facing side effect —
`test_phase7.py::TestWhatsAppLive::test_send_test_message_via_twilio` sends a
real, billable WhatsApp message. It requires `ALLOW_LIVE_WHATSAPP_SEND=1` on
top of `-m integration` and skips otherwise.

---

# CI/CD Testing

## Target pipeline

Every Pull Request

↓

Lint

↓

Type Check

↓

Unit Tests

↓

Integration Tests

↓

API Tests

↓

Build

↓

Security Scan

↓

Deploy Staging

## Implemented today (PH2.4)

Five GitHub Actions workflows run on every push to `main`, every pull request,
and (for the three security workflows) weekly. Authoritative documentation:
`docs/deployment/GITHUB_ACTIONS.md`.

| Workflow | Verifies | Status |
|----------|----------|--------|
| `backend-ci` | Lint (correctness subset, blocking; full style advisory), static analysis (mypy on `backend/security`), compile + import + startup-validation, **1,035 hermetic tests** (PH3.1; was 695 at PH2.4) | Implemented |
| `docker-build` | hadolint; production image builds; image refuses to start unconfigured; production config validates; boots against real MongoDB + Redis; graceful SIGTERM | Implemented |
| `dependency-audit` | `pip-audit --strict` (runtime + dev), `npm audit --audit-level=high`, suppression-expiry ratchet | Implemented |
| `security-audit` | gitleaks over full history, no tracked `.env`, `.env.example` in sync with the secret registry | Implemented |
| `codeql` | Taint-tracking SAST for Python and JavaScript/TypeScript | Gated — requires a public repo or GitHub Advanced Security |

Test selection is mechanical: `pytest -m "not integration"` — which is also the
default in `backend/pyproject.toml` since PH3.1, so a bare `pytest` on a laptop
selects exactly what CI selects. The `integration` marker is applied
automatically to the live-server suites by `backend/tests/conftest.py` — never
by a flag in a workflow file.

The test job no longer sets test environment variables either. PH3.1 moved that
to `backend/tests/_testenv.py`, which overwrites rather than defaults, so CI and
a developer machine provably run the same configuration.

Not yet implemented, with owners:

- Integration tests against a booted stack — PH2.6. **Must set
  `REQUIRE_LIVE_BACKEND=1`**, or a stack that failed to boot will skip its way
  to a green tick.
- Frontend build / lint / test job — PH3.3
- Coverage measurement in CI — PH3.11. Tooling itself is done: `pytest-cov`
  is pinned and configured (PH3.1), `pytest --cov` works; what is missing is a
  job, a trend, and a threshold.
- Branch protection requiring these checks — PH2.5
- Deploy Staging — PH2.7 (CD; no workflow in this repository deploys anything)

---

# Coverage Goals

These are long-term targets, not current state.

## Measured baseline (PH3.1, 2026-08-09)

`pytest --cov` over the default hermetic suite, application code only
(statements; branch coverage is not measured yet):

| Area | Coverage | Target |
|------|---------:|-------:|
| `security/` (PH1 modules) | **94.8%** | 100% |
| `observability/` (PH2.5) | **95.8%** | 90% |
| `infrastructure/` (PH2.7) | **82.4%** | 90% |
| `services/trading_engine.py` | **82.0%** | 95% |
| `services/brokers/` | 56.9% | 90% |
| `server.py` (API surface) | 51.9% | 90% |
| `services/market_engine/` | 46.5% | 90% |
| `services/` (other) | 42.4% | 90% |
| **Backend total** | **59.2%** | 90% |

## Measured baseline (PH3.2, 2026-08-10) — frontend

`yarn test:coverage`, application code only, excluding the vendored
`components/ui/` shadcn primitives.

| Metric | Overall | Critical paths |
|--------|--------:|---------------:|
| Statements | **33.6%** | **77.0%** |
| Branches | 19.5% | — |
| Functions | 28.6% | — |
| Lines | 34.4% | — |

Overall is low by design: it counts ~30 untested feature pages (Portfolio,
TradeMonitor, StockDetail, Markets, News, Settings, ten admin pages) that PH3.2
did not scope. The number that matters is the second column.

| Critical path | Stmts |
|---------------|------:|
| `services/api.js` (interceptors) | **100%** |
| `services/googleAuth.js` | **100%** |
| `utils/formatters.js` | **100%** |
| `pages/Login.jsx` | **100%** |
| `pages/AIAssistant.jsx` | **100%** |
| `context/AuthContext.jsx` | 97.6% |
| `pages/PaperTrading.jsx` | 96.1% |
| `pages/AuthCallback.jsx` | 95.8% |
| `services/tradeService.js` | 94.3% |
| `components/notifications/NotificationPanel.jsx` | 94.1% |
| `utils/apiError.js` | 92.0% |
| `pages/Watchlist.jsx` | 89.1% |
| `pages/admin/AdminDashboard.jsx` | 84.0% |
| `components/admin/AdminRoute.jsx` | 75.0% |
| `hooks/useAIWorkspace.js` | 75.6% |
| `pages/Dashboard.jsx` | 66.4% |
| `store/realtimeStore.js` | 60.6% |

No `fail_under` threshold is enforced on either suite. PH3.11 sets one from
trend data rather than inventing a number alongside the first measurement.

Full detail and methodology: `docs/testing/TEST_ARCHITECTURE.md` §8 (backend)
and `docs/testing/PH3.2_FRONTEND_TEST_CERTIFICATION.md` (frontend).

## Targets

Frontend

90%

Backend

90%

Business Logic

95%

Critical Services

100%

---

# Bug Severity

Critical

System unusable

High

Major feature broken

Medium

Feature partially works

Low

Minor UI issue

Trivial

Cosmetic issue

---

# Release Quality Gates

Before production verify

✓ Unit Tests Passed

✓ Integration Tests Passed

✓ API Tests Passed

✓ E2E Tests Passed

✓ Security Scan Passed

✓ Performance Targets Met

✓ Accessibility Verified

✓ Documentation Updated

✓ Manual QA Approved

✓ Product Owner Approval

---

# Monitoring After Release

Monitor

Error Rate

Crash Rate

API Failures

Broker Failures

Payment Failures

AI Errors

Latency

User Feedback

Rollback if required.

---

# Future Enhancements

Visual Regression Testing

AI Evaluation Framework

Synthetic Monitoring

Chaos Engineering

Contract Testing

Mutation Testing

Cross-Region Testing

Enterprise QA Dashboard

---

# Long-Term Vision

Testing should become an automated quality assurance system that continuously validates every layer of StockAssist AI.

Every deployment should be backed by automated tests, performance benchmarks, security checks, and user experience validation, ensuring confidence in every release while enabling rapid development.

---

# End of Testing Documentation
# StockAssist AI
## Testing Documentation

Version: 1.2

Status: Active Development

Last updated: 2026-08-15 (PH3.7 — observability testing)

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

**Current state in one line (PH3.7, 2026-08-15):** the backend has 2,398 tests;
`pytest` runs 2,303 of them hermetically and green in ~2m50s with no server,
database, credentials or network; 95 live-server tests are classified and skip
cleanly without a deployment; the frontend has 364 tests across 20 suites, green
in ~11s. PH1 security (`pytest -m security`) holds at **452**, unchanged since
PH1.12.

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

## Performance measurement, as built (PH3.4)

Full report: `docs/performance/PH3.4_PERFORMANCE_CERTIFICATION.md`.

**None of the targets above has been verified against a deployment.** They remain
targets. What PH3.4 built is the instrumentation to measure the things that
*determine* them, in three contexts that must not be confused:

| Context | Tool | Measures | Cannot measure |
|---|---|---|---|
| Hermetic, in-process | `scripts/perf_api_profile.py --offline` | Application code + serialization; query count; documents read; payload bytes | Any real latency |
| Real MongoDB | `scripts/perf_db_benchmark.py` | Query **plans** via `explain`, `docsExamined/nReturned`, in-memory sort stages — before and after `ensure_indexes()` | Concurrency, contention |
| Real provider | same profiler without `--offline` | Actual market-data transport latency | Anything reproducible |

Run them:

```bash
cd backend
python scripts/perf_api_profile.py --offline     # application cost only
python scripts/perf_api_profile.py               # + real provider latency
python scripts/perf_db_benchmark.py              # query plans, before/after
```

`perf_db_benchmark.py` seeds an isolated scratch database, **refuses to run if its
name resolves to the configured `DB_NAME`**, and drops it on exit. It never reads
or writes application data.

### Why performance regression tests assert counts, not durations

`assert elapsed < 0.05` measures the CI runner. It goes red when the runner is busy
and stays green on a fast laptop that has just regressed by forty queries — failing
for the wrong reasons and passing for the only reason that matters. Such a test is
marked `skip` within two sprints and takes its coverage with it.

The 38 PH3.4 regression tests therefore assert only exactly-reproducible
quantities:

* **`tests/test_perf_regression.py` (32).** Query count **identical at 3 rows and
  33** — the N+1 *signature*, which unlike a pinned constant cannot be satisfied by
  updating the number; index coverage for every hot filter+sort; payload bounds;
  `asyncio.gather` structure; the per-request query floor; health probes touching
  zero collections.
* **`frontend/src/__tests__/requestEfficiency.test.jsx` (6).** No duplicate request
  per mount; no re-fetch on re-render (the unstable-dependency detector); **zero**
  requests over 70 s while the socket is live — plus a counter-test proving
  Watchlist *does* poll when disconnected, without which the previous assertion
  would pass just as happily if every timer had been deleted.

**`TestIndexCoverage` is the assertion the in-memory double cannot make.** `FakeDB`
has no query planner, so a collection with no index behaves identically under test
to a perfectly indexed one — four unindexed collections passed all 2,144 pre-PH3.4
tests. That class records what `server.ensure_indexes()` declares by running it
against a stub `db` (rather than parsing source, which would keep passing if the
call moved somewhere that never runs) and checks each hot query shape against it.
It also carries a floor assertion so the mechanism cannot silently empty and
report green.

`tests/_perf.py` is the shared instrument: a `count_queries()` context manager and
a `measure()` helper that reports **cold and warm timings separately** — several
handlers import their service module inside the function body, and conflating that
once-per-process import with steady-state latency is how PH3.4 nearly attributed
288 ms to an endpoint that runs in 11 ms.

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

## Load testing, as built (PH3.5)

Full report: `docs/performance/PH3.5_LOAD_TEST_CERTIFICATION.md`.

**The user counts above are still aspirations, and the honest number is lower than
any of them.** PH3.5 measured **100 concurrent synthetic users** against a
single-worker local stack; 500 and beyond were not attempted, because a figure
produced by one uvicorn process on a shared laptop would not transfer to a
deployment that does not exist yet. What *was* established is a capacity envelope
with a named binding constraint at each level, which is the thing a scaling
decision actually needs.

**Tool: k6 v2.2.0**, one framework, chosen over Locust because the load driver
should not itself be a Python process competing with the system under test for the
GIL and the CPU that is the measured ceiling.

```bash
scripts/load/load-test.sh smoke        # ~40 s, 5 VUs — "can the stack serve traffic at all"
scripts/load/load-test.sh baseline     # 20 VUs
scripts/load/load-test.sh moderate     # 50 VUs
scripts/load/load-test.sh high         # 75 VUs
scripts/load/load-test.sh stress       # 100 VUs
scripts/load/load-test.sh saturation   # arrival-rate ceiling search
scripts/load/load-test.sh auth         # login throughput
scripts/load/load-test.sh ratelimit    # 429 boundary, rejection shape, bystander isolation
scripts/load/load-test.sh websocket    # hold and churn modes
scripts/load/load-test.sh failure      # provider fault injection, 6 phases
```

The runner brings up the whole environment, runs a **preflight that refuses to
start against the configured `DB_NAME`**, snapshots server-side metrics before and
after each run, and writes every artefact to `scripts/load/results/<timestamp>-<shape>/`.

### Two rules this harness exists to enforce

**No third party receives load.** The brief forbids it and it would in any case
measure the provider's throttle rather than StockAssist's capacity. Market data is
redirected with `MARKET_DATA_YAHOO_BASE` (`services/real_market.py::yahoo_origin()`,
read at call time, **inert when unset**), AI with the SDK's own
`ANTHROPIC_BASE_URL` — no application change needed for the second. Both are backed
by local stdlib mocks with a `/__control` endpoint for latency / error / timeout /
429 injection. **Verification does not rely on either mechanism being correct:**
every outbound TCP connection from the backend was enumerated during a run and all
of them were loopback.

**`tests/test_load_harness.py` (12 tests) pins both halves of that contract** — that
the override is inert by default, *including* that an empty or whitespace-only value
is treated as unset, and that it actually takes effect when set. The second half
matters as much as the first: a working provider and a working mock produce the same
green result, so a silently-broken redirect would send the next load test at Yahoo
and nothing would report it. Same failure shape PH3.1 found in three "hermetic"
tests that were reaching the live internet and passing either way.

### What load found that single-request measurement could not

| ID | Finding | Owner |
|---|---|---|
| **L-1** | `REDIS_MAX_CONNECTIONS=24` is below the app's own fan-out width; redis-py's pool **raises rather than queues** when exhausted, and 5 failures open a **process-wide** circuit breaker for 10 s — so a burst becomes a total cache outage, at the worst moment. p95 at 250 rps: **10,485 ms**. Setting the pool to 200: **11.1 ms, zero failures**, ~217 → ~410 rps sustained, no code change. | PH3.7 (config) |
| **L-2** | `ConnectionManager.broadcast` iterates a mutating set; under socket churn it raised `RuntimeError: Set changed size during iteration`, **silently dropping a market broadcast to every client past the mutation point**. The sibling `broadcast_to_channel` already iterates a copy. | Next real-time sprint |
| **L-3** | `verify_password` (bcrypt cost 12, 234 ms) runs **synchronously on the event loop**. Login is pinned at **~4/s** at any concurrency — and `/refresh` and `/logout`, which do no bcrypt, degrade in lockstep because they are queued behind it. This **corrects** PH3.4's "no blocking operation in an async request path". | Next auth sprint |

All three needed concurrency to appear, and none of them was fixed in PH3.5 —
changing application code mid-sprint invalidates every measurement taken before the
change.

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

## Stress and failure testing, as built (PH3.5)

**Two different questions, two different instruments — and using the wrong one is
the common mistake.** `scenarios.js` models realistic users with think-time between
page visits, so throughput there is governed by the think-time: adding virtual users
buys linear throughput and the system never approaches its limit. It answers "does
plausible traffic hold up" (it did: flat 8.3 ms median from 5 to 100 VUs) and says
nothing about the ceiling.

`saturation.js` removes think-time and drives a **fixed arrival rate**. That is the
correct instrument for "where does it break", because with `ramping-arrival-rate`
k6 keeps offering the requested rate when the system slows down, so the queue
becomes visible — whereas with `ramping-vus` a system that slows down simply
receives less traffic and hides its own saturation.

`load-test.sh failure` covers the degradation half: the market mock injected at 30%
errors, 10% timeouts and +800 ms latency, and the AI mock at 6 s plus 20%
rate-limiting. **Zero 5xx and zero timeouts in every phase**, and AI degradation did
not contaminate the rest of the API — `api` p95 stayed at 30.5 ms while `ai` p95 sat
at the injected 6,152 ms.

Market-open and breaking-news bursts remain untested as *named scenarios*; the
traffic shapes that would produce them (burst arrival, socket fan-out under churn)
are covered by `saturation.js` and `websocket.js -e WS_CHURN=1`.

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

## Measured under concurrency (PH3.5)

Redis was **entirely unmeasured** before PH3.5 — PH3.4 marked it explicitly
unavailable because the measurement host had no Redis running. With one running, it
turned out to be **the system's first bottleneck** (L-1 above), and the mechanism is
worth knowing before reading any Redis number in this repository:

* **Caching works, and works well.** The 60 s quote cache collapsed **7,044
  quote-enriched requests into 583 upstream fetches (91.7%)**, with **no thundering
  herd at TTL expiry** at any tested level — answering PH3.4's flagged "most likely
  load-test finding" in the negative. It remains theoretical at higher fan-out and
  under multiple workers, where each worker holds an independent cache.
* **The client pool is the problem, not the server.** Redis itself sat at 25 of
  10,000 available client slots. The exhaustion is entirely in redis-py's pool
  sizing on the application side.
* **"Rate Limits" above describes something that does not exist.** There is **no
  Redis-backed rate-limit store** — only `MongoRateLimitStore`. PH3.4 §21.5 and the
  roadmap both imply otherwise. Recorded as **L-6**, owned by the next
  security-touching sprint.

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

## Observability testing, as built (PH3.7)

Counts as of 2026-08-15: **backend 2,303 passed / 6 xfailed** (was 2,216),
**frontend 364 passed / 20 suites** (was 324 / 18).

| Suite | Tests | Covers |
|---|---:|---|
| `backend/tests/test_observability.py` (PH2.5) | 148 | Health probes, metrics registry, structured logging, request correlation |
| `backend/tests/test_log_infrastructure.py` (PH2.6) | 61 | Streams, rotation, retention, file-sink redaction |
| `backend/tests/test_observability_subsystems.py` (**new**) | **87** | Error classification, subsystem/auth/Mongo/WebSocket/task/scheduler/provider/AI/event-bus instrumentation, configuration readiness, client-error ingest, cardinality, redaction |
| `frontend/src/services/__tests__/telemetry.test.js` (**new**) | **27** | Reporting, route normalisation, chunk detection, caps, and what is never collected |
| `frontend/src/components/__tests__/ErrorBoundary.test.jsx` (**new**) | **13** | Containment, reporting, production message withholding, one-shot chunk reload |

### Three things these tests assert that ordinary coverage does not

**Cardinality, directly.** A label that becomes unbounded fails nothing locally
— it takes out the monitoring backend weeks later, in production, under load.
`TestCardinality` asserts the bound instead of hoping: a **denylist** of
forbidden label names checked against every registered metric (a denylist, not
an allowlist, because the failure mode is someone *adding* a label nobody
reviewed), plus a ceiling test driving 550 distinct label sets and asserting
they collapse to `MAX_SERIES_PER_METRIC + 1` with the overflow series present
and `metrics_series_dropped_total` non-zero.

**Redaction as an absence claim.** Two sweeps drive secret-shaped strings —
API keys, JWTs, connection URIs with passwords, emails, ObjectIds — through
every path that accepts free text (AI provider error strings, exception
messages, MongoDB failure documents, browser reports) and assert the strings
are **absent from the rendered exposition document**. A third proves the closed
vocabularies actually *refuse* rather than merely log, because a validator that
warns and then records the value anyway passes every other test in the file.
"We were careful" is not checkable; "this string does not appear" is.

**That instrumentation does not change control flow.** Every tracker test
asserts propagation as well as the counter — including `caught.value is
original`, by identity, because a tracker that wraps or replaces an exception
destroys the caller's ability to handle it. A tracker that silently swallowed a
provider failure would turn a visible incident into a wrong answer, and would
otherwise look like a passing test.

Failure behaviour is additionally exercised outside pytest by a **61-check
failure-injection drill** run against the real `server.app` (Mongo killed,
configuration invalidated, hostile ingest payloads, scanner probes), and
overhead by `backend/scripts/observability_overhead.py`, which is committed so
the numbers in `docs/architecture/OBSERVABILITY.md` §10 can be re-derived rather
than trusted.

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

## Analytics testing, as built (PH3.8)

Counts as of 2026-08-16: **backend 2,429 passed / 6 xfailed** (was 2,303),
**frontend 375 passed / 21 suites** (was 364 / 20).

| Suite | Tests | Covers |
|---|---:|---|
| `backend/tests/test_analytics.py` (**new**) | **122** | IST window resolution and the 05:30 boundary, the metric contract's construction-time invariants, the inventory's structural integrity, trade-scoping filters, ten reproduced financial defects, data-quality checks, empty/single/multi datasets, and analytics authorization |
| `frontend/src/pages/__tests__/AdminAnalytics.test.jsx` (**new**) | **11** | That fabricated metrics render marked, that genuinely derived ones do not, that the marker degrades safely, and that no growth figure is invented in the frontend |
| `backend/tests/test_perf_regression.py` (extended) | +4 rows | The four analytics query shapes are index-covered |

### What these assert that ordinary coverage does not

**That the mocks are still labelled.** PH3.8 deliberately did not remove
seventeen fabricated metrics, so the marker is the only thing between an
operator and a set of invented business numbers. `TestAdminAnalyticsContract`
and the frontend suite hold it in place. If a future change strips the
`mock_metrics` array or the "Simulated" badge, the suite goes red — which is
the point of flagging rather than deleting.

**That the inventory cannot drift.** `TestRegistry` asserts every endpoint named
in `analytics/registry.py` exists on the **live route table**, that every entry
is structurally complete, and that every MOCK entry names the production source
that would replace it. A markdown table would have been accurate on the day it
was written and wrong thereafter.

**Wrong values by name, not just right ones.** Where the old behaviour was a
specific wrong number, the test asserts the right one *and* names the wrong one
in a comment — "pre-PH3.8 this reported +8,500 at a 50% win rate". A regression
is then legible in the failure output rather than requiring archaeology.

### One harness defect worth carrying forward

23 of these tests passed in isolation and failed in the full run with
`RuntimeError: There is no current event loop`, because a suite earlier in the
alphabet closes the main-thread loop. The cause was `asyncio.get_event_loop()`
in the suite's own coroutine-driver helper, **not the application** — and it
presented exactly like an application defect. The helper now creates a private
loop and restores the previous policy state, so it neither inherits the problem
nor exports it to whatever runs next. Bare `asyncio.run()` would have done the
latter: it leaves the policy's current loop set to `None`.

---

# End of Testing Documentation
# StockAssist AI — Backend Test Architecture

**Owner:** PH3.1 — Test Infrastructure & Test Stabilization
**Last updated:** 2026-08-09
**Applies to:** `backend/tests/` (1,130 tests across 48 files)
**Frontend testing:** none yet — PH3.3 owns it. This document is backend-only.

This is the developer-facing reference for how the backend suite is organized,
what each category guarantees, and which command to run. `.claude/TESTING.md`
holds the project-wide testing *policy*; this document holds the *mechanics*.

---

## 1. The shape of it

```
Developer
   │
   ├── pytest                          ← the default. 1,035 tests, ~2m20s
   │     │                               no server, no database, no network
   │     ├── Hermetic unit             (services, engines, pure logic)
   │     ├── Hermetic API contract     (FastAPI TestClient + FakeDB)
   │     ├── Security regression       (452 tests, PH1 controls)
   │     └── Infrastructure logic      (Redis/logging/backup/DR config paths)
   │
   ├── pytest -m integration           ← 95 tests, needs a running deployment
   │     │                               (skips cleanly when there isn't one)
   │     ├── MongoDB    (real, via the app)
   │     ├── Redis      (real, via the app)
   │     └── Market data / AI providers (real)
   │
   └── e2e                             ← no tests yet. PH3.9 owns the journeys.
```

CI executes the deterministic suite on every push and pull request. The
integration layer is not wired to CI yet — PH2.6 owns provisioning a booted
stack for it. See §10.

---

## 2. Hermetic is the default, and it is enforced

A hermetic test must not depend on a running localhost server, external
network, production API, developer machine state, or manually seeded data.
Before PH3.1 that was an aspiration with nothing behind it. Three mechanisms
now enforce it, all in `backend/tests/`:

| Mechanism | File | What it prevents |
|---|---|---|
| Deterministic environment | `_testenv.py` | The suite reading the developer's real `backend/.env` |
| Outbound-network guard | `_netguard.py` | A test silently reaching a real provider |
| In-memory Mongo double | `_fakedb.py` | A test touching a real database |

### 2.1 Deterministic environment

`server.py` calls `load_dotenv(ROOT_DIR / '.env', override=True)` at import
time, and `conftest.py` imports `server`. Every "hermetic" test therefore ran
against whatever secrets the developer happened to have configured.

`tests/_testenv.py::apply()` runs **before** that import and:

* sets `PYTHON_DOTENV_DISABLED=1` — python-dotenv's own supported kill switch,
  so `load_dotenv` becomes a no-op instead of something to monkeypatch;
* **overwrites** (never `setdefault`s) every configuration variable the backend
  reads, so an exported shell variable cannot leak past it;
* sets `APP_ENV=testing` — a first-class environment in `security/secrets.py`,
  deliberately distinct from `development`;
* blanks every third-party credential, so every `*_configured()` check reads
  "not configured" and routes take their offline fallback;
* removes `REDIS_URL` and the cookie/CORS overrides, because *absent* and
  *empty* are different states to those readers.

Individual tests override freely with `monkeypatch.setenv` — that is how the
security suites exercise production-shaped configuration.

**Consequence for CI:** the workflow no longer sets test environment variables.
There is one source of truth and both CI and a laptop read it.

### 2.2 Outbound-network guard

`tests/_netguard.py` replaces `socket.socket.connect`/`connect_ex` for the
duration of each hermetic test and raises `NetworkAccessBlocked` for any
non-loopback address. It is installed by an **autouse** fixture, and skipped
only for tests carrying `integration`, `live`, `e2e`, or `allow_network`.

It patches at the socket layer rather than per-client on purpose: the escapes
PH3.1 found came through three different HTTP clients (`aiohttp`, `httpx`,
`requests` via `yfinance`), and a per-client allowlist goes stale the moment
someone adds a fourth.

`NetworkAccessBlocked` subclasses `OSError` so application code that already
handles connection failures takes its normal offline path — the test then fails
on a wrong assertion, which is diagnosable, rather than on an exotic exception
surfacing from inside a library.

Loopback stays open: `TestClient` needs no socket, but `socketpair` and
asyncio's self-pipe do, and blocking those breaks the event loop.

### 2.3 Database isolation

There is no test MongoDB, because no hermetic test needs one. The `fake_db`
fixture swaps the module-level `server.db` for `tests/_fakedb.py::FakeDB`, an
in-memory async double implementing the subset of the Motor API the routes use.

This was originally a workaround for Motor binding its client to the event loop
that constructed it (TestClient runs each request on a fresh loop via
`asyncio.run`, so the second DB-backed request raises
`RuntimeError: Event loop is closed`). It is also, conveniently, complete
isolation: there is no connection string a test could get wrong.

Belt and braces for the case where something does try to connect: `_testenv`
points `MONGO_URL` at `mongodb://127.0.0.1:27017` and `DB_NAME` at
`stockassist_pytest`, so an accidental connection lands somewhere harmless and
obviously wrong rather than in the development database.

**Cleanup between tests** is structural, not procedural: `fake_db` is
function-scoped, so each test gets a new empty `FakeDB`. There is no shared
state to reset and no cleanup step to forget.

### 2.4 Redis isolation

The Redis suites (`test_redis_infrastructure.py`, 50 tests) exercise
`RedisSettings` parsing, client construction, the pub/sub registry and the
degradation paths — none of which require a server. They drive configuration
through `monkeypatch.setenv("REDIS_URL", ...)` with synthetic URLs and assert
on the resulting objects.

No `fakeredis`, no container, no real Redis. `_testenv` removes `REDIS_URL`
from the base environment so a developer with a local Redis running does not
get a different suite from CI. `requires_redis` is registered as a marker for
the future PH2.6 integration job; no test carries it today.

### 2.5 External API isolation

Default automated tests do not depend on external APIs. Three layers, in order
of how much you should rely on them:

1. **Credentials are blank** (`_testenv`) — the provider is not configured, so
   the route takes its documented offline branch. This is the layer that
   produces *correct* behaviour.
2. **Explicit mocks** — `unittest.mock.patch` / `AsyncMock` at the service
   boundary (`services.real_market.*`, `services.portfolio_engine.*`). This is
   the layer that produces *deterministic data*.
3. **The network guard** — the backstop that turns a missed mock into a loud
   failure instead of a silent live call.

Real external integration tests exist only behind `integration`/`live`. One of
them —`test_phase7.py::TestWhatsAppLive::test_send_test_message_via_twilio` —
sends a real, billable WhatsApp message, and requires a *second* explicit
opt-in, `ALLOW_LIVE_WHATSAPP_SEND=1`, because it is the only test in the
repository with an irreversible outward-facing side effect.

---

## 3. Markers

Registered in `backend/pyproject.toml`; `--strict-markers` makes a typo a
collection error rather than a silently-ignored decorator.

| Marker | Meaning | Applied |
|---|---|---|
| `integration` | Needs a live backend at `$REACT_APP_BACKEND_URL` and, transitively, real Mongo/Redis/providers | Automatically, by file (`conftest.py::_LIVE_SERVER_SUITES`) |
| `live` | Drives a running deployment over HTTP/WebSocket rather than TestClient | Automatically, alongside `integration` |
| `e2e` | A complete user journey across the running stack | Nothing yet — PH3.9 |
| `security` | A PH1 security-control regression test | Automatically, by file (`conftest.py::_SECURITY_SUITES`) |
| `slow` | Takes more than ~5 s | By hand — currently `test_backup_restore.py::TestRetention` |
| `requires_db` | Needs a real MongoDB | Nothing yet — reserved for PH2.6 |
| `requires_redis` | Needs a real Redis | Nothing yet — reserved for PH2.6 |
| `allow_network` | Deliberately exempt from the network guard | Nothing yet |

**Markers that matter are applied mechanically, from a filename list in
`conftest.py`, not by hand.** A hand-applied decorator on 452 security tests
would be missing from some of them within a sprint, and `pytest -m security`
would then quietly under-report the regression surface. Adding a new
live-server suite means adding its filename to one tuple; nothing in
`.github/` changes.

**There is deliberately no `unit` marker.** It would have to be hand-applied to
~1,000 existing tests to mean anything, and `-m unit` finding a fraction of
them and reporting green is worse than not having it. Hermetic is the default
and is defined by the *absence* of the markers above — which needs no
annotation and cannot drift.

---

## 4. Commands

All commands run from `backend/`. Every one of these has been executed and the
stated result observed (2026-08-09).

| Goal | Command | Result |
|---|---|---|
| **Default suite** | `pytest` | 1,035 passed, 95 deselected, ~2m20s |
| Fast inner loop | `pytest -m "not slow"` | 1,029 passed, 95 skipped, ~1m40s |
| Security regression | `pytest -m security` | 452 passed, ~34s |
| Slow tests only | `pytest -m slow` | 6 tests |
| Integration / live | `pytest -m integration` | 95 tests; skips with a reason when no deployment is reachable |
| Integration, CI-strict | `REQUIRE_LIVE_BACKEND=1 pytest -m integration` | Fails instead of skipping |
| Coverage | `pytest --cov` | 59.2% of application statements |
| Single file | `pytest tests/test_api_contract.py` | 19 passed, 0.14s |

Live-server runs additionally need:

```bash
export REACT_APP_BACKEND_URL=http://localhost:8000
export TEST_ADMIN_EMAIL=...        # no default, deliberately
export TEST_ADMIN_PASSWORD=...     # no default, deliberately
```

### 4.1 A trap worth knowing

`-m` is single-valued. `pyproject.toml` puts `-m "not integration"` in
`addopts`, and an explicit `-m` on the command line **replaces** it rather than
combining. So `pytest -m "not slow"` would pull the live suites back into the
run.

Rather than document that as a rule people must remember, PH3.1 made it
harmless: `conftest.py::_require_live_server` probes the configured deployment
once per session and **skips** `live` tests when nothing answers. An unrun test
reports as unrun. Set `REQUIRE_LIVE_BACKEND=1` — as the PH2.6 integration job
must — and the skip becomes a failure, so a stack that failed to boot cannot
skip its way to a green tick.

---

## 5. Fixtures

Defined in `backend/tests/conftest.py`, available to every test:

| Fixture | Scope | Provides |
|---|---|---|
| `client` | function | `TestClient(app)` — in-process, no socket |
| `fake_db` | function | Fresh `FakeDB` patched over `server.db` |
| `test_user` | function | A seeded user document (`test_user@example.com`, ₹100,000 capital, role `user`) |
| `auth_headers` | function | A real JWT for `test_user`, minted by the app's own `create_access_token` |
| `no_ai` | function | Forces `claude_configured()`/`gemini_configured()` to `False` |
| `_hermetic_network` | function, autouse | Installs the network guard |
| `_require_live_server` | function, autouse | Skips `live` tests with no deployment |
| `_live_backend_reachable` | session | Cached reachability probe |

**Test data conventions.** Fixture data is obviously synthetic and labelled:
users are `@example.com`, notification titles and trade notes are prefixed
`TEST`, market values are round numbers (₹22,000 Nifty, ₹2,500 quote). A number
in a failure message should be immediately identifiable as fixture data rather
than something that leaked from a real provider. There are no real credentials,
production identifiers, or personal data anywhere in `backend/tests/`.

---

## 6. The live-server suites

Six files, 95 tests, all marked `integration` + `live` automatically:

| File | Tests | What only a deployment can answer |
|---|---|---|
| `test_backend_live.py` | 24 | Registration writing to real Mongo; trade lifecycle persisting across requests; real AI chat completion |
| `test_phase7.py` | 17 | Real Yahoo Finance data (`source == "yahoo_finance"`); live Twilio |
| `test_phase4.py` | 14 | Portfolio monitor, Zerodha callback/postback against the real app |
| `test_phase6.py` | 14 | Deep Zerodha integration; stock autocomplete |
| `test_phase5.py` | 13 | News, journal, full-report scoring against real data |
| `test_phase2.py` | 13 | Data-source status; WebSocket over a real network connection |

Shared configuration lives in `tests/_live.py`: one `BASE_URL`, one
`admin_login()`, credentials from the environment with no defaults.

Before PH3.1 five of these files hardcoded `admin@alphapartner.com` /
`admin123`, and two of them located the deployment by scraping a frontend
`.env` off the filesystem (`/app/frontend/.env`, then walking up the source
tree). Both are gone.

---

## 7. What was converted, and what was not

PH3.1 converted the API-contract half of the largest live suite into
`tests/test_api_contract.py` (19 hermetic tests): market overview, stock
detail/search/universe, top picks, portfolio summary, notifications, and the
SIP calculator — including the documented **degraded** branches
(`available: false`, 503-vs-404) that a live test cannot trigger on demand and
that production will actually hit during a provider outage.

What stayed live is what genuinely needs a deployment (§6). Nothing was
deleted to make a number look better; `test_phase8.py` was removed because it
was a zero-byte file.

Auth endpoints were deliberately **not** converted: `/api/auth/*` already has
far deeper hermetic coverage in the PH1 security suites, and re-asserting the
happy path would be duplicate logic, not extra safety.

---

## 8. Coverage

Measured with `pytest-cov` (pinned in `requirements-dev.txt`, never installed
into the runtime image). Configuration in `pyproject.toml` under
`[tool.coverage.*]`.

Baseline, 2026-08-09, default hermetic suite:

| Area | Statements | Missing | Coverage |
|---|---:|---:|---:|
| `security/` (PH1 modules) | 1,567 | 81 | **94.8%** |
| `observability/` (PH2.5) | 1,173 | 49 | **95.8%** |
| `infrastructure/` (PH2.7 Redis) | 539 | 95 | **82.4%** |
| `services/trading_engine.py` | 251 | 45 | **82.0%** |
| `services/brokers/` | 559 | 241 | 56.9% |
| `server.py` (API surface) | 2,897 | 1,393 | 51.9% |
| `services/market_engine/` | 922 | 493 | 46.5% |
| `services/` (other) | 5,091 | 2,933 | 42.4% |
| **Total** | **12,988** | **5,295** | **59.2%** |

Notes on reading this honestly:

* **Statements only.** Branch coverage is not measured; adding `--cov-branch`
  would change every number downward and PH3.1 chose one baseline over two.
* **Application code only.** Including `tests/` in the denominator gives 72%,
  which is the more flattering and less meaningful figure — test files are
  ~100% covered by construction.
* **Coverage is not in `addopts`.** Instrumentation costs ~25% wall-clock, and
  the inner-loop command has to stay fast or people stop running it.
* **There is no `fail_under`.** A threshold invented in the same commit as the
  first measurement is a number pulled from the air, and the first person it
  blocks will simply lower it. PH3.11 owns setting one from trend data.

The shape is the expected one: code written *with* tests (PH1 security, PH2
observability) is in the 80–96% band; code written before this discipline
(`services/`, `server.py`) is in the 40–52% band. `server.py` at 51.9% across
2,897 statements is the single largest gap and is what PH3.5 (API contract
tests) and PH3.6 (router decomposition) exist to address.

---

## 9. Known limitations

1. **No frontend tests at all.** PH3.3.
2. **No CI job runs the integration suite.** PH2.6 owns provisioning a booted
   stack. Until then the 95 live tests run only when someone runs them.
3. **No branch coverage, no coverage gate, no coverage job in CI.** §8.
4. **`FakeDB` is not MongoDB.** It implements the operator subset the routes
   use. A query using an operator it does not model will behave differently
   under test than in production. This is the standing risk of the in-memory
   double and the reason the integration layer must not be abandoned.
5. **`test_backup_restore.py::TestRetention` sleeps 1.05 s per artifact** (~43 s
   of the suite). Not a hidden race — the pruner sorts by whole-second mtime,
   so the fixture genuinely has to space artifacts out. Fixing it means giving
   the pruner an injectable clock; PH3.11.
6. **`services/` at 42.4%** is the largest untested surface in the backend.

---

## 10. CI

The `backend-ci` workflow's `test` job runs `pytest -m "not integration"` with
no test-environment variables of its own (`_testenv.py` owns them) and uploads
JUnit XML. Verified 2026-08-09 by running the suite in a scrubbed environment
(`env -i`, no `.env`, no exported secrets): **1,035 passed**.

The remaining CI gaps — integration job, frontend job, coverage job, branch
protection — are tracked in `.claude/TESTING.md` with their owning sprints.

---

## Related documents

* `.claude/TESTING.md` — project-wide testing policy and strategy
* `docs/testing/PH3.1_TEST_CERTIFICATION.md` — the PH3.1 certification record
* `docs/deployment/GITHUB_ACTIONS.md` — CI workflow reference
* `.claude/PRODUCTION_ROADMAP.md` — PH3 sprint definitions

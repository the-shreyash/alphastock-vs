# PH3.3 — Backend Test & API Coverage Certification

**Sprint:** PH3.3 — Backend Tests & API Coverage
**Phase:** PH3 — Production Hardening & Quality Assurance
**Date:** 2026-08-10
**Decision:** ✅ **CERTIFIED**

> **Numbering.** The sprint brief labelled this work **PH3.3**. This
> repository's `PRODUCTION_ROADMAP.md` numbers PH3.3 as *Frontend Test
> Foundation* (delivered under the brief label "PH3.2") and numbers this work
> **PH3.5 — API Contract & Error-State Testing**. This document keeps the
> brief's label; the roadmap carries the cross-reference. Nothing was
> renumbered unilaterally. Roadmap PH3.2 (*Mock Data Eradication*) remains
> NOT STARTED and untouched.

---

## 1. Executive Summary

The backend had 1,035 hermetic tests, and they were good tests — but they were
concentrated in the security modules (PH1) and the infrastructure modules (PH2).
The **API surface itself** was the gap: `server.py` sat at 51.9% statement
coverage across 2,897 statements, and of 201 routes, the authorization posture
of most of them had never been asserted by anything.

PH3.3 adds **1,115 tests across 8 suites**, taking the backend from 1,035 to
**2,150 hermetic tests (2,144 passed + 6 xfailed), green in ~2m46s** with no server, no database, no
credentials and no network.

The central deliverable is not a count. It is that **three of the new suites
derive their test cases from the live route table** rather than from a
hand-written list. Every authenticated route is checked for anonymous rejection;
every admin route for non-admin rejection; every identifier-shaped path
parameter for malformed-input handling. A route added next sprint is
automatically covered, and a route that ships without its dependency turns those
suites red without anyone remembering to write a test.

**Eight genuine defects were found. Six were fixed; two were documented and
assigned** rather than absorbed into a testing sprint (§21).

Two of the fixed defects are the kind that only a test finds:

* **D-11** — a blank `SMTP_PORT` (exactly what a deployment scaffolded from
  `.env.example` has before SMTP is configured) raised `ValueError` inside
  `int("")`, which 500ed `GET /api/data-sources` **and broke every outbound
  email — including password-reset and email-verification delivery**.
* **D-1** — `page=0` on any paginated admin endpoint produced a negative Mongo
  `skip`, which the driver rejects; `limit=0` divided by zero. Both surfaced as
  500s from a plain query string.

**One pre-existing test-isolation defect was found and fixed** (§17): a
monkeypatch in `test_ai_workspace.py` permanently shadowed a class attribute,
so any later class-level patch of the AI engine was silently ignored. It had
been latent for as long as that test existed; it only became visible when a new
test patched the same object.

| Measure | Result |
|---------|--------|
| New tests | **1,115** across 8 suites |
| Total backend tests | **2,144 passed**, 0 failed, 6 xfailed, 95 deselected |
| Runtime | ~2m46s with coverage; ~2m35s without |
| Backend statement coverage | 59.2% → **65.0%** |
| `server.py` coverage | 51.9% → **67.2%** |
| API routes inventoried | **201** |
| Routes under mechanical authz coverage | **126** (97 user + 29 admin) |
| Defects found | **8** (6 fixed, 2 documented + assigned) |
| PH1 security regression | **452 passed** — unchanged |
| PH3.1 regression | **1,035 passed** — unchanged |
| PH3.2 frontend regression | **313 passed / 17 suites** — unchanged |

---

## 2. Existing Test Audit

The starting inventory, 48 files / 1,130 tests, classified before anything was
changed.

| Category | Files | Tests | Notes |
|---|---:|---:|---|
| Security (PH1) | 15 | 452 | Marked `security` mechanically by `conftest.py` |
| Live-server | 6 | 95 | Marked `integration`+`live`; skip without a deployment |
| Service / engine unit | 20 | ~400 | Trading engine, portfolio, scanner, backtest, journal |
| Infrastructure (PH2) | 6 | ~170 | Redis, logging, backup, disaster recovery, observability |
| Hermetic API contract | 1 | 19 | `test_api_contract.py` — PH3.1's conversion |

**Findings from the audit, before writing anything:**

1. **The API surface was the hole.** 19 hermetic API tests for 201 routes. The
   security suites cover `/api/auth/*` deeply, but the other ~190 routes had
   almost no route-level assertions.
2. **No authorization matrix existed.** Not one test asserted "this route
   rejects anonymous callers" as a general property. Individual live tests
   asserted it for perhaps six routes.
3. **No test could reach the broker routes.** `services.broker_engine` holds its
   own Mongo handle, which the `fake_db` fixture did not patch — see §17.
4. **The in-memory Mongo double silently widened queries.** `_match` ignored any
   operator it did not implement, so a filter containing `$regex`, `$in` or
   `$or` matched *everything*. Any test asserting a count over such a filter
   would have agreed with a wrong answer.
5. **Nothing was stale or flaky at the file level.** No test was deleted. The
   only test *modified* was one whose isolation leak is described in §17.

**Nothing from the existing suite was rewritten.** PH3.3 is additive, apart from
the fixture and double improvements needed to make the untested surface
reachable.

---

## 3. Test Architecture

The three-layer model from the brief, as built:

```
pytest                                  ← default. 2,150 tests, ~2m35s
  │                                       no server, no DB, no network
  ├── Layer 1 — Unit
  │     services, engines, validators, security modules, pure logic
  │
  ├── Layer 2 — Hermetic API / integration
  │     FastAPI TestClient + FakeDB + patched service boundaries
  │     ├── test_api_authz.py        (307)  authn/authz over the live route table
  │     ├── test_api_validation.py   (552)  malformed input over the live route table
  │     ├── test_api_market_data.py   (68)  provider-failure matrix
  │     ├── test_api_ai.py            (48)  AI provider-failure matrix
  │     ├── test_api_admin.py         (39)  control plane
  │     ├── test_api_errors.py        (38)  error envelope, rate limit, infra failure
  │     ├── test_api_trading.py       (35)  order lifecycle + broker boundary
  │     ├── test_api_migrated.py      (28)  converted from the live suites
  │     └── test_api_contract.py      (19)  PH3.1
  │
  └── Layer 3 — External / live            ← `-m integration`, 95 tests
        needs a deployment; skips cleanly without one
```

Layer 3 is unchanged and still deselected by default (`-m "not integration"` in
`pyproject.toml`).

### 3.1 The derived-test-case principle

`tests/_routes.py` reads `server.app.routes` at collection time and classifies
each route by its **resolved dependency graph** — not by its URL. A route is
"protected" iff `get_current_user` appears in its dependency tree, and "admin"
iff `require_admin` does.

Three suites parametrize over that classification. The consequence:

* Adding an endpoint adds its authorization tests automatically.
* Shipping an endpoint without its dependency turns a test red, named for the
  offending route in the pytest node id.
* No list in `tests/` has to be maintained to keep pace with the API.

Each parametrized case is **one test**, not one loop over all routes. That gives
a named failure per route, and — less obviously but more importantly — a fresh
function-scoped `fake_db` per case, so each request gets its own rate-limit
counter. A single test issuing 126 anonymous requests would trip the
platform-wide 60/min anonymous limiter partway through and start collecting 429s
that have nothing to do with authorization.

Both mechanical suites carry a guard test (`test_the_sweep_is_actually_covering_something`)
that fails if the derived list ever empties — otherwise a FastAPI upgrade
changing the `dependant` shape would delete hundreds of tests and report green.

---

## 4. API Route Inventory

**201 routes**, enumerated from the application object, grouped by domain, with
the authorization each actually enforces:

| Domain | Routes | Admin | User | Public |
|---|---:|---:|---:|---:|
| Admin portal | 29 | 29 | 0 | 0 |
| Brokers | 17 | 0 | 16 | 1 |
| Market | 17 | 0 | 0 | 17 |
| Zerodha | 16 | 0 | 13 | 3 |
| Stocks | 14 | 0 | 0 | 14 |
| Trades | 13 | 0 | 13 | 0 |
| Auth | 13 | 0 | 3 | 10 |
| AI workspace | 12 | 0 | 9 | 3 |
| Paper trading | 6 | 0 | 6 | 0 |
| Analysis | 5 | 0 | 2 | 3 |
| Portfolio | 5 | 0 | 5 | 0 |
| Webhooks | 5 | 0 | 1 | 4 (key-gated) |
| Gemini | 4 | 0 | 2 | 2 |
| Health | 4 | 0 | 0 | 4 |
| News | 4 | 0 | 0 | 4 |
| Notifications | 4 | 0 | 4 | 0 |
| Journal | 4 | 0 | 4 | 0 |
| Monitor / WhatsApp / Email | 9 | 0 | 9 | 0 |
| Watchlist | 3 | 0 | 3 | 0 |
| Settings / SIP / Advisor | 6 | 0 | 5 | 1 |
| Chat | 2 | 0 | 2 | 0 |
| Diagnostics / metrics / misc | 9 | 0 | 0 | 9 |
| **Total** | **201** | **29** | **97** | **75** |

The 75 public routes were reviewed individually. All are legitimately
unauthenticated: market and stock data (the product is readable pre-login),
health and metrics probes (a load balancer has no token; `observability/routes.py`
gates metrics/diagnostics with their own token in production), auth entry
points, OAuth callbacks, and the five webhook routes — which are **not** open:
`verify_webhook_key` fails closed, denying every call when `WEBHOOK_API_KEY` is
unset.

`GET /api/ai/activity` and `GET /api/ai-activity` were checked specifically
because their names suggest per-user data. They return the *system-wide*
heartbeat trace (`services.activity_logger`), not user history, so they are
correctly public.

---

## 5. Authentication Coverage

`test_api_authz.py` — 307 tests.

| Check | Routes | Result |
|---|---:|---|
| Anonymous → 401 | 126 | all pass |
| Forged bearer token → 401 | 126 | all pass |

401 is asserted **exactly**, not "any 4xx". A 403 would mean the request was
authenticated and then refused; a 404 would mean the dependency never ran. Only
401 proves the credential check itself fired.

The second sweep exists because a route that treats "a token was presented" as
"the caller is authenticated" passes the first sweep and is still completely
open.

**Token-level authentication is deliberately not re-tested here.** PH1's suites
(`test_jwt_sessions`, `test_auth_hardening`, `test_cookie_security`, `test_csrf`,
`test_recovery`, `test_password_policy`) already cover expiry, signature,
issuer, audience, token-type confusion, refresh replay, revocation and session
invalidation across 452 tests. Restating that matrix would have added lines
without adding safety. PH3.3 covers the orthogonal question: whether each
*route* is wired to the checks PH1 built.

Registration, login and duplicate-account handling are asserted in
`test_api_errors.py` where they intersect with the error contract.

---

## 6. Authorization Coverage

### Vertical — user → admin

All 29 admin routes answer **403** to an authenticated `role="user"` caller.
Separately, the five plan tiers (`free`, `pro`, `elite`, `lifetime`,
`beta_tester`) are each asserted to be non-admin. That second test exists
because `users.role` carries both entitlement tiers and control-plane roles in
one field — a conflation that puts every plan-granting code path one typo away
from granting `admin`.

### Vertical — admin → super_admin

| Action | admin | super_admin |
|---|---|---|
| Delete a user | 403, account intact | 200, account removed |
| Grant `admin` / `super_admin` | 403, role unchanged | 200 |
| Self-promote to `super_admin` | 403, role unchanged | — |
| Grant a plan role (`pro`) | 200 | 200 |
| `grant-plan` with `plan="admin"` | 400 | 400 |

Each assertion checks the **stored document**, not just the status code. A 403
that still wrote the role would be the worst possible outcome and is exactly
what a status-code-only test would miss.

### Horizontal — user A vs user B

Verified across every user-owned resource: trades (list, modify, exit,
coaching), notifications, watchlist (list, delete), paper trades (list, reset),
chat history, AI conversations, AI memory, and settings.

Two properties are asserted for each: the response (**404**, not 403 — an
ownership check must not confirm that someone else's resource exists) and the
**victim's document afterwards**, unchanged.

---

## 7. Request Validation Coverage

`test_api_validation.py` — 552 tests.

### Identifier sweep

13 malformed identifier values × every route with an identifier-shaped path
parameter, in two tiers:

* **Universal invariant** (all such routes): never 5xx.
* **Strict** (routes whose parameter really is an ObjectId): must answer 4xx.

Values include `not-an-objectid`, `123`, `null`, `undefined`, 100 zeroes,
`../../etc/passwd`, `%00`, `$ne`, `{"$gt":""}`, a 23-character hex string, and a
24-character non-hex string. The NoSQL-operator strings are included because a
handler that reaches the database with one unparsed is a different and worse bug
than one that merely 500s.

**Two path parameters are excluded from the strict tier, by name and with the
reason recorded in the source:** `session_id` (a chat session key, free-form by
design) and `order_id` (a *broker-side* reference whose format belongs to
Zerodha/Upstox). They remain in the 5xx sweep. Excluding them by an anonymous
predicate would have let a genuinely unguarded route be added to the exclusion
later to make a red test green.

The empty string is deliberately absent from the value list: it collapses the
URL to the collection path, so it tests routing rather than validation.

### Other validation surfaces

| Surface | Cases | Finding |
|---|---:|---|
| Pagination (`page`, `limit`) on 4 admin endpoints | 20 | **D-1** — fixed |
| `grant-plan` `duration_days` | 10 | **D-3** — fixed |
| Admin user update (mass assignment) | 8 | No defect; allowlist holds |
| Trade creation (missing / wrong type / non-positive) | 22 | No defect |
| Paper trade payloads | 4 | No defect |
| SIP calculator query params | 5 | **D-5** — fixed |
| Stock search, scanner, symbol params | 17 | No defect |
| Malformed JSON bodies | 6 | **D-6** — fixed |

The mass-assignment test is worth calling out: `PUT /api/admin/users/{id}` is
asserted to drop `password_hash`, `_id`, `blocked` and any other unlisted key.
An admin editor that accepted arbitrary fields would let an admin overwrite a
credential directly.

---

## 8. Database Coverage

Exercised through the routes rather than against a repository layer, because
this codebase has no repository layer — `server.py` calls Motor directly.

| Operation | Where covered |
|---|---|
| Create | Trade creation, watchlist add, announcements, feature flags, chat turns |
| Read | Every listing endpoint; projection correctness (`password_hash` excluded) |
| Update | Trade modify/exit, notification read, settings, admin user update, block/unblock |
| Delete | Watchlist removal, announcements, user deletion, conversation deletion |
| Duplicate prevention | Registration (duplicate email → 400), watchlist add (idempotent per user+symbol) |
| Ownership enforcement | §6 horizontal tests — every user-owned collection |
| Missing documents | 404 paths on trades, notifications, users, announcements, tickets |
| Invalid identifiers | §7 identifier sweep |
| Pagination | Page boundaries, page-count arithmetic, non-overlapping pages, beyond-the-end |
| Sorting | Audit log newest-first; trade lists by entry/exit time |

**No test can reach a real database.** `tests/_fakedb.py` replaces
`server.db` and (new in PH3.3) `broker_engine.db` with a function-scoped
in-memory double. Cleanup is structural — each test gets a fresh instance —
rather than procedural, so there is no teardown step to forget. `MONGO_URL`
additionally points at `stockassist_pytest`, so even an accidental connection
lands somewhere unmistakably wrong rather than in development data.

### 8.1 The double was strengthened, deliberately

`FakeDB` gained `aggregate`, `list_collection_names`, `command`, cursor
`skip`/`limit`, projections, `deleted_count`, and the `$or`/`$in`/`$nin`/
`$regex`/`$lt`/`$gt` operators — all because a route under test needed them.

The important change is the one that removes a foot-gun rather than adding a
feature: **an unsupported operator now raises `UnsupportedQuery` instead of
matching everything.** Previously, a condition dict containing an unmodelled
operator fell through every branch and the document matched, so a filter meant
to *narrow* a result set silently *widened* it to the whole collection. That is
the "stub agrees with the bug" failure mode, and it would have made any count
assertion over such a filter agree with the wrong answer.

`skip(-n)` and `limit(-n)` also raise, matching MongoDB, which is what made
**D-1** visible instead of silently passing.

**The double remains a partial implementation, on purpose** — see §22.

---

## 9. Trading API Coverage

`test_api_trading.py` — 35 tests. Every assertion checks the **stored document**
as well as the response, because a route can return the right code and still
have written the wrong thing, and it is the document that the portfolio, the
journal and the tax export read afterwards.

| Scenario | Asserted outcome |
|---|---|
| Valid order | Persisted, owned by caller, `quantity_open` seeded, `initial_stop_loss` captured |
| Risk violation | 422 **and nothing persisted** |
| Risk warning | 200, trade created, warnings recorded (warnings educate, violations block) |
| `POST /trades/validate` | Dry run — creates nothing |
| Broker rejects entry | 502 **and no OPEN trade recorded** |
| Broker accepts entry | `broker_order_id` stored |
| `auto_exit` without a broker | Stored as `False` |
| Unknown broker | 4xx, nothing persisted |
| Symbol case | Normalized to uppercase |
| Full exit | Closed, `quantity_open == 0`, realized P&L correct |
| Partial exit | Remainder stays OPEN, partial P&L booked |
| Exit quantity > held | **Clamped**, not oversold |
| Double close | Second attempt 400, **P&L not booked twice** |
| Market exit, no quote | 422 rather than guessing a price |
| Broker rejects exit | 502 **and position stays OPEN with quantity intact** |
| Modify: stop above entry | Allowed (breakeven-plus stops are the documented rule) |
| Modify: stop ≥ target 1 | 422, unchanged |
| Modify: target wrong side / out of order | 422 |
| Modify a closed trade | 400 |

Three of these are the ones that would cost a user money: a trade recorded for a
rejected broker order (user believes they hold something they do not), an exit
recorded for a rejected broker order (user is flat on paper and long in reality,
with no stop attached), and a double-booked close.

**No trading logic was modified.** `services/trading_engine.py` has its own unit
coverage in `test_trading_engine.py`; this suite covers the API around it —
validation, ownership, persistence, and the broker boundary. The broker is
always mocked.

---

## 10. Market Data Coverage

`test_api_market_data.py` — 68 tests. Failure matrix across market overview,
stock detail, gainers/losers/sectors/global/commodities/FII-DII, the scanner,
news, sentiment and search.

Two contracts are asserted, not one:

1. **No provider failure becomes a 500.** The allowed set is
   `{200, 400, 404, 422, 429, 503}` — **503 is explicitly correct**, not a
   failure. "The upstream is down, try later" is the documented answer for a
   known symbol with no live quote, and a blanket `< 500` assertion would have
   failed the endpoint for doing the right thing.
2. **No provider failure produces invented numbers.** ADR-021. The degraded
   overview payload must carry `available: false`, must *not* carry a `nifty`
   value, and must explain itself.

`/api/stocks/{symbol}` is asserted to distinguish **404 (no such symbol)** from
**503 (real symbol, no live data)**. Conflating them would tell the frontend
"stock not found" during a provider incident, sending users to search for a
stock that exists.

### 10.1 A test that was wrong, and what it taught

The first version of this suite patched `real_overview` / `fetch_real_gainers`
to **raise** `asyncio.TimeoutError`, and 20 tests failed with the exception
propagating to a 500.

That looked like a serious HIGH finding. It was not, and reporting it would have
been wrong. Failure containment in this system lives one layer down:
`fetch_yahoo_quote` wraps its `httpx` call in `except Exception -> return None`,
and `fetch_all_universe_quotes` gathers with `return_exceptions=True` and skips
the failures. **A real provider timeout reaches a route as `None` or `[]`, never
as an exception.** The test was asserting a state production cannot enter, and
"fixing" the routes to satisfy it would have added exception handlers for
exceptions that never arrive.

The suite was rewritten to inject failure where it actually originates — at the
`httpx` boundary — and the containment itself is now asserted directly, in
`TestFailureContainmentLivesInTheServiceLayer`. Those tests fail at the exact
`try/except` that broke, rather than in twenty route tests that would only
report a symptom.

The same reasoning applies to `fetch_news`, which contractually returns a list
on every path including total feed failure; `TestNewsServiceContract` pins that,
because `GET /api/news` computes `len(articles)` with no guard and is correct
only because of it.

This pattern — assert the guarantee at the layer that provides it — is the main
methodological result of the sprint.

---

## 11. AI API Coverage

`test_api_ai.py` — 48 tests. Chat, portfolio review, reflection, AI memory,
conversations, Gemini direct, and the debate engine's fallback.

**No test reaches a real provider**, guaranteed three independent ways:
`_testenv.py` blanks `ANTHROPIC_API_KEY` / `GOOGLE_GEMINI_KEY` so every
`*_configured()` check reads False; each test patches the provider boundary
explicitly; `_netguard.py` blocks the socket if anything slips past both. A
dedicated test asserts the first of these, so a developer's real key leaking
into the suite fails loudly instead of quietly making billable calls.

Failure injection happens at `AIDebateEngine.simple_chat` — the single boundary
every AI feature funnels through — so the Model Router, the Prompt Library and
each route's own result handling all run for real.

Beyond status codes, two behavioural properties:

* **No fabrication.** An unavailable Gemini answers `"Gemini unavailable"`, not
  invented trading advice. The debate-engine fallback keeps its response
  *shape* (callers index those keys) while stating the failure plainly.
* **No garbage persisted.** An empty or whitespace-only completion adds **zero**
  lessons to AI Memory. Memory is read back into later prompts, so a
  whitespace "lesson" persisted once would be fed to the model forever as if it
  were an insight the user had earned.
* **No pointless calls.** With no closed trades, `/api/ai/reflect` short-circuits
  and the provider mock asserts `not_called` — calling the model with no data
  would bill for a prompt containing nothing and invite a hallucinated lesson
  about trades that do not exist.

`/api/ai/prompts` is asserted never to expose raw prompt text (PROMPT.md
forbidden behaviour — the templates are proprietary and an injection surface).

Containment is again asserted at its source: `ClaudeProvider.complete` converts
an SDK exception into `AIResponse(error=...)`, `gemini_analyze` converts one into
an explanatory string (with quota exhaustion explained specifically, since a 429
is the most likely free-tier failure), and `simple_chat` falls back from Claude
to Gemini.

---

## 12. Admin API Coverage

`test_api_admin.py` — 39 tests. Authorization is covered in §6; this suite asks
whether an entitled caller then gets the right behaviour.

* **Dashboard and analytics render on an empty database** — the day-one case and
  the post-restore case. Five aggregate endpoints are swept for this, because
  each divides or averages over collections that are empty on a fresh install.
* **Every mutating action asserts its audit record** — actor, target, and
  payload. An unaudited privileged action is indistinguishable from one that
  never happened.
* **Block/unblock, update, grant-plan and delete assert the resulting document**,
  not the status code.
* **`password_hash` is asserted absent** from both the list and detail
  responses. The projection is the only thing preventing every user's credential
  hash from reaching an admin page, and from there browser history and logs.
* **Search, role filter, status filter, page count and page boundaries** are
  each asserted — these are what D-1 broke.
* **An orphaned audit row renders.** Audit entries outlive the accounts that
  wrote them, deliberately, so the page must show an unresolvable `admin_id` as
  "System" rather than 500 on it.
* **`/api/admin/ai/usage` survives a malformed `user_id`** — this is D-9.

---

## 13. Error Handling Coverage

`test_api_errors.py` — 38 tests.

| Status | Covered by |
|---|---|
| 400 | Malformed identifier; malformed JSON body; invalid plan; missing OAuth params |
| 401 | The 126-route anonymous sweep |
| 403 | The 29-route non-admin sweep; super-admin boundary; webhook key |
| 404 | Unknown route; unknown trade / user / notification / announcement |
| 405 | Wrong method on a real path |
| 409 | Broker auth error |
| 422 | Schema violations; risk violations; unavailable exit price |
| 429 | Rate limiter (§14) |
| 502 | Broker upstream failure |
| 503 | Market data unavailable; readiness gate |

Three contracts hold across all of them:

1. **Every error carries a parseable `detail`.** The frontend's axios
   interceptor reads that key to choose between showing a message, refreshing
   the token, and retrying; an error body without it renders as a blank toast.
2. **No error leaks internals.** Asserted absent: `Traceback`, `/Users/`,
   `/app/backend`, `motor`, `pymongo`, `server.py`, `site-packages`.
3. **No error reflects submitted values.** The PH1.5 sanitizing 422 handler is
   regression-tested, and the new D-6 handler is asserted not to echo the
   offending body fragment — the decoder's own message quotes it.

**Broker error codes are asserted specifically**, because the choices are
deliberate and non-obvious: a broker *auth* failure is **409, not 401**. A 401
would trip the frontend's global session-expiry interceptor and log the user out
of StockAssist because their *broker* token expired — losing their session over
an unrelated third party's problem. A generic broker failure is **502**, which
says "the upstream failed" and points debugging at the right system.

**Database failure** is the one place the suite deliberately induces a failure,
because "MongoDB is unreachable" is a real operating state whose response
contract had never been checked. The assertion is modest and honest: the process
must not be taken down, no partial or fabricated data may be served, and
`/api/health/live` must keep answering. Liveness is separately asserted **not**
to depend on the database — coupling them makes a database blip roll every
application pod, turning a recoverable dependency failure into a full outage.

---

## 14. Rate-Limit Coverage

PH1's `test_rate_limit.py` (26 tests) covers the limiter's *behaviour* — policy
arithmetic, storage, lockout, `Retry-After`, headers, fail-open. **None of that
was changed, weakened, or duplicated.**

PH3.3 adds only what that suite structurally cannot prove. It exercises the
middleware against a synthetic Starlette route, which proves the middleware
works but **not that it is mounted on the real application**. Remove the
`apply_rate_limiting(app, ...)` call from `server.py` and every one of those 26
tests still passes.

Four new tests close that gap on the real app:

* The anonymous tier throttles `/api/market/overview` and returns
  `code: "RATE_LIMITED"` with a positive `Retry-After`.
* The authenticated tier throttles `/api/trades` and is keyed per user — one
  user's exhausted budget does not throttle another's.
* Health probes stay exempt. A throttled probe reads as "unhealthy" to an
  orchestrator, which then restarts a container that was fine — the limiter
  manufacturing the outage it exists to prevent.
* A 429 still carries security headers, proving the limiter is wired *inside*
  the header/CORS middleware. Wired outside, a browser could not read the 429.

---

## 15. Security Regression

```
pytest -m security   →  452 passed, 1,793 deselected, 33s
```

Unchanged from PH3.1. No security test was modified, skipped, or weakened.

| Area | Status |
|---|---|
| OAuth | green |
| Cookies | green |
| CORS | green |
| JWT | green |
| Sessions | green |
| Password policy | green |
| CSRF | green |
| Rate limiting | green |
| RBAC | green |
| Identifiers | green |
| Security headers | green |
| Audit logging | green |

**Security coverage increased.** The authorization sweeps are new security
surface: 126 routes × 2 authentication checks and 29 routes × 1 authorization
check that no test previously asserted, plus horizontal-escalation coverage on
every user-owned collection. `security/` module coverage is unchanged at 94.8%;
what changed is the coverage of *the routes that consume those modules*.

---

## 16. Live-Test Migration

PH3.1 converted the market/portfolio half of the largest live suite. PH3.3
continues that with `test_api_migrated.py` (28 tests).

**The rule used:** a live test is migratable when it asserts a property of the
*application* — a status code, a redirect target, a response shape, an
authorization rule, a validation rule. It is **not** migratable when it asserts a
property of the *deployment*. Converting the second kind replaces a true
statement about production with a tautology about a mock.

**Migrated:**

| From | What |
|---|---|
| `test_phase2`, `test_phase4` | Zerodha config URLs, cancelled-callback redirect, postback acceptance and malformed-payload tolerance |
| `test_phase2` | Google OAuth session-exchange validation (missing code/state, invalid state) |
| `test_phase2/4/5` | Data-sources status contract |
| `test_phase5`, `test_phase6` | Trade journal listing, stats, setup-stats, ownership |
| `test_phase4` | Portfolio monitor health and alert scoping |
| `test_phase6` | Quick-trade and search validation |
| `test_phase5` | Full-report rejection of an unknown symbol |

**Deliberately left live**, with the question only a deployment answers:

| Live test | What it really asserts |
|---|---|
| `test_stock_live_has_source` | Yahoo Finance actually responded (`source == "yahoo_finance"`) |
| `TestZerodhaAccount` (phase6) | A real broker session returns real funds/holdings |
| `TestWebSocket` (phase2) | A socket survives a real network hop and proxy |
| `TestWhatsAppLive` (phase7) | Twilio actually delivered a billable message |
| `test_full_report_scoring` | Scoring over genuine market data |

The `requires auth` cases in those files were **not** migrated because the
mechanical 401 sweep already covers all 126 authenticated routes rather than the
handful those files happened to name.

**No live file was deleted**, and a test asserts that every filename in
`conftest._LIVE_SERVER_SUITES` still exists — if one is renamed without updating
that tuple, the suite silently rejoins CI, finds no server, and fails for a
reason that looks nothing like the cause.

**The default CI suite requires no `uvicorn`.** It never did since PH3.1; PH3.3
did not regress it and added 1,115 tests that also do not.

---

## 17. Fixture Architecture

Added to `backend/tests/conftest.py`:

| Fixture | Provides |
|---|---|
| `other_user` / `other_headers` | A second ordinary user — the victim in horizontal-escalation tests |
| `admin_user` / `admin_headers` | `role="admin"` |
| `super_admin_user` / `super_admin_headers` | `role="super_admin"` |
| `authenticated_client` | `TestClient` pre-loaded with `test_user`'s bearer token |
| `admin_client` / `super_admin_client` | Same, for the privileged roles |

All four principals are built by one `_seed_user` helper, so they differ in
**exactly one field** (`role`). An authorization test that passes for the wrong
reason — different capital, different name — is not constructible.

Tokens are minted by the application's own `create_access_token`. A test that
forges its own JWT stops testing the issuer and would keep passing after the
issuer started emitting something the verifier rejects.

`authenticated_client` is used where credentials are incidental; the plain
`client` fixture with explicit headers is used where the *presence or absence*
of credentials is the point — an implicit credential is exactly what those tests
must not have.

### 17.1 Two isolation defects found in the existing fixtures

**`fake_db` did not patch every database handle.** `services.broker_engine`
captures its own reference (`broker_engine.db`), so all 33 broker and Zerodha
routes were talking to the **real Motor client** during "hermetic" tests. This
did not fail loudly — it failed as `RuntimeError: Event loop is closed` thrown
from inside Motor on the second DB-backed request of the session, which reads
like an async bug in the application and is really an unpatched dependency.
Every broker route was unreachable from a hermetic test until this was found.
`fake_db` now patches both handles, and the symptom is documented in the
fixture so the next occurrence is recognisable.

**A monkeypatch in `test_ai_workspace.py` leaked permanently.** It does
`monkeypatch.setattr(ai_debate_engine._engine, "simple_chat", _fake)` — patching
an *instance* attribute for a method that lives on the *class*. Monkeypatch
records the "original" as the bound method it read through the instance and
restores it by setting that bound method **onto the instance**. Teardown
therefore leaves a permanent instance attribute shadowing the class attribute.
The value is correct, so nothing looks wrong — but from that point on, any patch
applied to the class is invisible to `_engine`.

This had been latent for as long as that test existed. It surfaced as a new
PH3.3 test that passed in isolation and failed in the full suite — the worst
failure mode there is. The fix was to patch the same singleton instance every
other AI test patches; order-independence is verified in both directions. The
trap is documented in the fixture docstring.

---

## 18. External-Service Mocking

| Service | Default-suite treatment |
|---|---|
| Market data (Yahoo) | Credentials blank; `httpx` boundary patched; network guard |
| Broker APIs (Zerodha, Upstox) | `broker_engine` methods patched; `broker_engine.db` now a double |
| AI (Claude, Gemini) | Keys blank; `simple_chat` / provider `complete` patched |
| News (RSS) | `_parse_feed` / `fetch_news` patched |
| Email (SMTP, SendGrid) | Credentials blank; `get_status` reports unconfigured |
| WhatsApp / SMS (Twilio) | Credentials blank |
| Payments | Not implemented (see D-4) |
| Redis | `REDIS_URL` removed from the base environment |
| MongoDB | `FakeDB` in-memory double |

**No default test uses a real credential, a real database, or a real network
call.** Three tests assert this rather than assume it: the AI provider-isolation
test, the data-sources hermeticity test (any integration reporting
`configured: true` means a real key reached the suite), and the market
hermeticity test (`available: false` with no patch in place proves the network
guard held).

External integration tests remain behind `-m integration`, documented in §16 and
in `docs/testing/TEST_ARCHITECTURE.md` §4.

---

## 19. Coverage Results

`pytest --cov`, default hermetic suite, application code only, statements.

| Area | Stmts | Missing | PH3.1 | PH3.3 | Δ |
|---|---:|---:|---:|---:|---:|
| `observability/` | 1,092 | 42 | 95.8% | **96.2%** | +0.4 |
| `security/` | 1,536 | 80 | 94.8% | **94.8%** | — |
| `services/portfolio_engine.py` | 241 | 17 | — | **93.0%** | — |
| `infrastructure/` | 537 | 95 | 82.4% | **82.3%** | — |
| `services/trading_engine.py` | 251 | 45 | 82.0% | **82.1%** | — |
| **`server.py` (API surface)** | 2,914 | 957 | **51.9%** | **67.2%** | **+15.3** |
| `services/brokers/` | 559 | 241 | 56.9% | **56.9%** | — |
| `services/` (other) | 4,840 | 2,596 | 42.4% | **46.4%** | +4.0 |
| `services/market_engine/` | 868 | 485 | 46.5% | **44.1%** | −2.4 |
| **Backend total** | **13,005** | **4,550** | **59.2%** | **65.0%** | **+5.8** |

`server.py` is where the sprint aimed and where it landed: **+15.3 points across
2,914 statements**, roughly 450 previously-unexecuted statements now covered.

`services/market_engine/` shows −2.4 despite no test being removed. That is a
denominator effect, not a regression: the module grew between the two
measurements. The absolute number of covered statements did not fall.

**Domain coverage**, read from the same run:

| Domain | Assessment |
|---|---|
| Authentication | Strong — PH1 depth + PH3.3 route wiring |
| Authorization | **Complete** at route level (126/126, 29/29) |
| Security | Strong (94.8%), unchanged |
| Trading | Strong — engine 82%, API lifecycle fully asserted |
| Portfolio | Strong — engine 93% |
| Market | Moderate — `real_market.py` 34%; failure paths covered, the many per-provider parsers are not |
| AI | Moderate — routes and failure handling covered; individual prompt pipelines are not |
| Admin | Strong — all 29 routes exercised |
| Notifications | Moderate |
| Analytics | Moderate — endpoints render; the arithmetic is not verified against fixtures |

**Honest reading of these numbers**, unchanged in method from PH3.1: statements
only (branch coverage still not measured); application code only (including
`tests/` would give a flattering ~72%); coverage is not in `addopts` because
instrumentation costs ~25% wall-clock and the inner loop must stay fast; and
**there is still no `fail_under`** — a threshold invented alongside a
measurement is a number pulled from the air. PH3.11 owns setting one from trend
data.

Coverage was not inflated with meaningless tests. The 1,115 new tests are
concentrated in two mechanical sweeps that each assert a single sharp property
across many routes; they raise `server.py` coverage as a side effect of
asserting authorization and validation, not as a goal.

---

## 20. Defects Found

Eight genuine defects. Every one was found by a test, and for each the question
"is the test wrong or is the application wrong?" was answered before anything
was changed — three times the answer was "the test", and those tests were
rewritten rather than the application (§10.1, §7, §13).

| ID | Severity | Defect | Status |
|---|---|---|---|
| **D-11** | **HIGH** | Blank `SMTP_PORT` → `int("")` → ValueError. 500s `GET /api/data-sources` and **breaks all outbound email**, including password-reset and email-verification delivery, on any deployment that declares the variable without a value — i.e. every install scaffolded from `.env.example`. | ✅ Fixed |
| **D-4** | **HIGH** | `POST /api/admin/payments/{id}/refund` is a stub: reads no payment, calls no provider, returns `{"success": true, "message": "Refund initiated"}` for any string. The admin UI tells an operator the customer was refunded when nobody was, and the immutable audit log records `payment.refunded` for a refund that never happened. | ⚠️ Documented — **assigned to PH3.9** |
| **D-1** | MEDIUM | `page=0` → negative Mongo `skip` (driver rejects) → 500; `limit=0` → ZeroDivisionError in the page-count expression. All 4 paginated admin endpoints. Also unbounded `limit` allowed a full collection scan. | ✅ Fixed |
| **D-3** | MEDIUM | `grant-plan` `duration_days` went from an untyped JSON body straight into `timedelta(days=...)` — TypeError for a string/null/list, OverflowError for a huge int. Both 500. | ✅ Fixed |
| **D-6** | MEDIUM | 18 routes read `await request.json()` without a Pydantic model, bypassing FastAPI's body parsing; a malformed or truncated body raised `JSONDecodeError` → 500. A dropped mobile connection mid-POST produced a server error, and any authenticated caller had a trivial 500 generator. | ✅ Fixed |
| **D-10** | MEDIUM | `UserCreate.email` is a bare `str` with no format validation anywhere on the registration path. An account created with `"not-an-email"` can never verify and can never complete a password reset — a signup typo produces a permanently unrecoverable account. | ⚠️ Documented — **assigned** (see §21) |
| **D-9** | LOW | `GET /api/admin/ai/usage` called `ObjectId(uid)` raw on a value from a `$group` over `chat_messages.user_id`; one malformed row took the whole page to a 500. | ✅ Fixed |
| **D-2** | LOW | `PUT /api/notifications/{id}/read` answered 200 "Marked as read" for a notification belonging to someone else. The write was always safe (`user_id` is in the filter), but reporting success made an ownership rejection indistinguishable from a real update. | ✅ Fixed |

Plus one **test-infrastructure defect** (§17.1): the `test_ai_workspace.py`
monkeypatch leak, fixed.

---

## 21. Defects Fixed

Six, each with the fix chosen to be the smallest change that removes the class
of problem rather than the instance.

**D-11** — `os.environ.get("SMTP_PORT", "").strip() or "587"`. The default was
always meant to express "not configured"; it just never handled
present-but-empty, which is what a declared-and-unset variable looks like.

**D-1** — Declarative FastAPI `Query` bounds (`PageParam`, `LimitParam`,
`LogLimitParam`) rather than defensive clamping in four handlers. An
out-of-range value becomes a 422 naming the parameter — clamping would silently
serve page 1 to a client that asked for page 0 and hide its bug. The bound also
appears in the OpenAPI schema, and there is nothing for a fifth paginated
endpoint to forget to copy. `le=100` additionally closes the unbounded-scan
vector.

**D-3** — Explicit type and range check (1–3650 days) before `timedelta`. The
route reads raw JSON rather than a model, so the coercion has to be explicit.

**D-6** — One central `@app.exception_handler(json.JSONDecodeError)` returning
400, rather than 18 try/except blocks. A per-route fix is 18 chances to forget,
and the 19th route added next sprint would not be covered. Registered against
the precise type, not `ValueError`, so genuine 500s stay visible. The parse
position and snippet are deliberately not echoed — that would reflect fragments
of the submitted body, which is what the PH1.5 handler exists to prevent.

**D-9** — Narrow `try/except (InvalidId, TypeError)` resolving to "Unknown".
The malformed row is still *counted*, just not resolved — dropping it would
silently understate usage.

**D-2** — Check `matched_count` and answer 404, matching every sibling endpoint
(trades, watchlist). Verified safe against the frontend first:
`NotificationPanel.jsx` calls this with `.catch(() => {})` and only ever with
IDs from its own rendered list.

### Deliberately not fixed

**D-4 (refund stub)** — fixing it means implementing refunds against a payment
provider. That is PH3.9 (Mock Removal), not a change to make quietly inside a
testing sprint. Recorded as two `xfail` tests asserting the *correct* behaviour,
so they report XPASS the moment PH3.9 lands and demand promotion to real
assertions. A third test pins the audit behaviour that exists today so it is not
lost in the rewrite.

**D-10 (email validation)** — `/api/auth/*` is PH1-certified surface. Switching
to `EmailStr` changes an authentication contract, adds the `email-validator`
dependency, and requires a decision about accounts already stored with invalid
addresses. That is a migration, not a test fix. Recorded as four `xfail` tests
plus one test pinning today's behaviour so the change is visible when it lands.

Both follow the brief's §23 rule: document larger defects and assign them; do
not silently expand scope.

---

## 22. Known Gaps

1. **`FakeDB` is not MongoDB.** It implements the operator subset the routes
   use. This is now *safer* than it was — an unmodelled operator raises instead
   of matching everything (§8.1) — but a query whose semantics differ subtly
   between the double and the real driver will still behave differently under
   test. This is the standing cost of an in-memory double and the reason the
   integration layer must not be abandoned.
2. **No integration job in CI.** Owned by PH2.6. The 95 live tests run only when
   someone runs them. **That job must set `REQUIRE_LIVE_BACKEND=1`**, or a stack
   that failed to boot will skip its way to a green tick.
3. **No branch coverage, no coverage gate, no coverage job.** PH3.11.
4. **`services/real_market.py` at 34%.** The failure paths are covered; the many
   per-provider response parsers are not. Largest remaining backend gap.
5. **`services/stock_details.py` (28%), `scheduler.py` (15%), `trade_stream.py`
   (22%), `zerodha_service.py` (20%)** — background workers and streaming paths,
   which need either a scheduler harness or the integration layer.
6. **Analytics arithmetic is not verified.** The admin analytics endpoints are
   asserted to render on empty and populated databases; the correctness of the
   revenue and feature-usage numbers is not asserted against fixtures. Some of
   those numbers are acknowledged placeholders in the source
   (`revenue_today = total_payments * 499`), which PH3.9 owns.
7. **WebSocket routes are untested hermetically.** `/api/ws` remains live-only.
8. **No E2E journeys.** PH3.9.
9. **`test_backup_restore.py::TestRetention` still sleeps ~43s** (PH3.1
   finding, unchanged — the pruner needs an injectable clock). PH3.11.

---

## 23. CI Verification

The default command is unchanged and needs nothing new:

```bash
cd backend && pytest        # -m "not integration" comes from pyproject.toml
```

| Requirement | Status |
|---|---|
| No manual `uvicorn` | ✅ Never started by any default test |
| No production database | ✅ `FakeDB`; `MONGO_URL` → `stockassist_pytest` |
| No production Redis | ✅ `REDIS_URL` removed from the base environment |
| No real credentials | ✅ Blanked by `_testenv.py`; asserted by three tests |
| No real broker accounts | ✅ `broker_engine` patched; its DB handle now doubled |
| No real AI keys | ✅ Asserted by `test_no_ai_provider_is_configured_in_the_test_environment` |
| GitHub Actions compatible | ✅ Same command, same selection, same environment source |

`backend-ci`'s test job requires **no changes** — it already runs
`pytest -m "not integration"` and sets no test environment variables of its own
(`_testenv.py` owns them). The new suites are picked up automatically.

Runtime grew from ~2m20s to ~2m35s for **more than double** the test count,
because the new suites are in-process and share no expensive setup.

---

## 24. Technical Debt

Introduced by this sprint, and honestly labelled:

1. **`tests/_routes.py` couples the suite to FastAPI internals.** It reads
   `route.dependant`, which is not a public API. A FastAPI major upgrade could
   change its shape. Mitigated by the guard tests that fail if the derived lists
   empty — the failure mode is a loud red, not a silent green.
2. **`FakeDB` is growing into a Mongo reimplementation.** It is now ~330 lines.
   The `UnsupportedQuery` policy keeps it honest, but if it keeps growing, the
   right answer is `mongomock` or a containerized Mongo in the integration layer
   rather than more hand-written operators.
3. **Six `xfail` tests** (D-4, D-10). These are documentation, not coverage, and
   must be resolved by their owning sprints rather than accumulating.
4. **Dependency-graph classification assumes dependency *names*.** `_routes.py`
   matches `get_current_user` and `require_admin` by `__name__`. Renaming either
   function without updating `_routes.py` would silently reclassify routes as
   public. The guard tests catch a total collapse, not a partial one.

Pre-existing debt this sprint documented but did not take on: `server.py` is
5,700 lines and 201 routes in one module (PH3.6 owns decomposition — the new
contract tests are exactly the safety net that work needs); no repository layer,
so routes call Motor directly and database tests must go through HTTP.

---

## 25. PH3.4 Handoff

**PH3.3 is complete. PH3.4 was not started.**

Carried forward, with owners:

| Item | Owner | Note |
|---|---|---|
| **D-4 — implement or remove the refund stub** | **PH3.9** | Two `xfail` tests will XPASS when done. An admin-visible action that lies is worse than a missing feature. |
| **D-10 — registration email validation** | **Next auth-touching sprint** | Needs an `EmailStr` decision *and* a migration plan for accounts already stored with invalid addresses. Four `xfail` tests waiting. |
| Integration job with `REQUIRE_LIVE_BACKEND=1` | PH2.6 | Without the flag, a dead stack skips to green. |
| Coverage job, branch coverage, `fail_under` | PH3.11 | Set the threshold from trend data, not from this measurement. |
| `real_market.py` parser coverage (34%) | PH3.4 / PH3.9 | Largest remaining backend gap. |
| WebSocket hermetic coverage | PH3.9 | `/api/ws` is live-only today. |
| E2E journeys | PH3.9 | None exist. |
| `server.py` decomposition | PH3.6 | The 1,115 new contract tests are the safety net that makes it survivable. |
| Analytics arithmetic verification | PH3.9 | Placeholder revenue maths still in the source. |

**What PH3.4 inherits:** a backend where every route's authorization posture is
asserted mechanically, every identifier-shaped parameter is fuzzed for
controlled failure, the trading lifecycle is pinned at the database level, and
every external dependency's failure containment is asserted at the layer that
provides it — with a documented method for telling a real defect from a test
that invented an unreachable scenario.

---

## 26. Verification Record

Every command below was executed and the stated result observed on 2026-08-10.

| Command | Result |
|---|---|
| `pytest` (backend default) | **2,144 passed, 6 xfailed, 95 deselected, 166s** |
| `pytest -m security` | **452 passed**, 1,793 deselected, 33s |
| `pytest --cov` | **65.0%** total (13,005 stmts, 4,550 missing); `server.py` **67.2%** |
| `pytest tests/test_api_authz.py` | 307 passed, 0.5s |
| `pytest tests/test_api_validation.py` | 552 passed, 2 xfailed |
| `pytest tests/test_api_trading.py` | 35 passed |
| `pytest tests/test_api_market_data.py` | 68 passed |
| `pytest tests/test_api_ai.py` | 48 passed |
| `pytest tests/test_api_admin.py` | 39 passed |
| `pytest tests/test_api_errors.py` | 34 passed, 4 xfailed |
| `pytest tests/test_api_migrated.py` | 28 passed |
| Order-independence check (both directions) | 66 passed each way |
| `yarn test:ci` (frontend, PH3.2) | **313 passed, 17 suites**, 21s |

Baseline before the sprint, re-measured rather than quoted: **1,035 passed, 95
deselected, 139s.**

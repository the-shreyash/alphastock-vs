# PH3.5 — Load Testing & Capacity Validation Certification

**Sprint:** PH3.5 — Load Testing & Capacity Validation
**Phase:** PH3 — Production Hardening & Quality Assurance
**Date:** 2026-08-14
**Decision:** ✅ **CERTIFIED**

> **Numbering.** The sprint brief labels this work **PH3.5**. This repository's
> `PRODUCTION_ROADMAP.md` numbers PH3.5 as *API Contract & Error-State Testing*
> (already delivered under the brief label "PH3.3") and numbers this work as the
> **load-testing half of PH3.7 — Performance Benchmarking & Load Testing**, whose
> benchmarking half was delivered as the brief's PH3.4. This document keeps the
> brief's label, matching the precedent set by the PH3.2, PH3.3 and PH3.4
> certifications. The roadmap carries the cross-reference. Nothing was renumbered
> unilaterally.

---

## 1. Executive Summary

**The application code is not the constraint, and neither is MongoDB.** At every
tested concurrency from 5 to 100 virtual users the system served **zero 5xx
responses, zero timeouts, and passed 100% of functional checks**, with a median
latency that did not move: **10.9 ms at 5 users, 8.3 ms at 100**. MongoDB never
queued a single operation.

Two things *are* the constraint, and both were invisible until concurrency was
applied.

**The Redis client connection pool is sized below the application's own fan-out
width, and the failure it produces is a cascade rather than a slowdown.**
`REDIS_MAX_CONNECTIONS` defaults to **24**. A single watchlist request fans out a
quote lookup per symbol, each performing its own `cache_get`; several such
requests in flight, plus the background market loop, exceed 24 concurrent
commands. redis-py's pool does not queue when exhausted — it raises
`ConnectionError: Too many connections` immediately. Five consecutive failures
trip the circuit breaker, which degrades **the entire cache** to the in-process
fallback for 10 seconds; during those 10 seconds every quote misses and goes
upstream, which is the worst possible moment to add provider load. The effect
scales sharply and non-linearly:

| Offered rps | Redis failures | Circuit opens | p95 | p99 |
|---:|---:|---:|---:|---:|
| 100 | 100 (0.5%) | 8 | **21 ms** | 146 ms |
| 150 | 1,138 (3.7%) | 96 | 187 ms | 1,414 ms |
| 200 | 3,854 (9.9%) | 313 | 515 ms | 2,298 ms |
| 250 | 11,224 (31.7%) | 979 | **10,485 ms** | 11,655 ms |

**Raising one environment variable removes it completely.** Re-running the same
sweep with `REDIS_MAX_CONNECTIONS=200`:

| Offered rps | p95 (pool 24) | p95 (pool 200) | Redis failures (200) |
|---:|---:|---:|---:|
| 150 | 187 ms | **16.6 ms** | **0** |
| 200 | 515 ms | **42.4 ms** | **0** |
| 250 | 10,485 ms | **11.1 ms** | **0** |
| 300 | 11,888 ms | **28.5 ms** | **0** |
| 400 | — | **29.1 ms** | **0** |

Sustained read throughput rises from **~217 rps to ~410 rps — 1.9× — with no code
change**, and the ceiling that remains is honest: at 400 rps the uvicorn worker
is measured at **100.0% of one CPU core**, sustained. It is CPU-bound, as a
single-process Python event loop should be.

**The second constraint is that `verify_password` runs synchronously on the event
loop.** `security/passwords.py` pins bcrypt at cost 12 — correct, and not
something to trade away — measured at **234 ms per verification**. Because the
call is not offloaded, login throughput is **pinned at ~4 logins/second no matter
how many users are waiting**:

| Concurrent users | Logins/sec | Login median | **`/refresh` median** | **`/logout` median** |
|---:|---:|---:|---:|---:|
| 5 | 4.02 | 468 ms | 241 ms | 239 ms |
| 10 | 3.98 | 751 ms | 536 ms | 491 ms |
| 25 | **4.06** | 1,680 ms | **1,670 ms** | **1,430 ms** |

The two right-hand columns are the proof. `/refresh` and `/logout` perform **no
bcrypt at all** — their floor is 3–4 ms — yet they degrade in lockstep with
login. They are queued behind it on the same event loop. Throughput of 4.06/s is
1/234 ms to three significant figures: perfectly serialised, using one of the
host's eight cores.

**Three defects were found that only concurrency could reveal:**

* **L-1 (P1)** — the Redis pool cascade above.
* **L-2 (P1)** — `ConnectionManager.broadcast()` raised `RuntimeError: Set changed
  size during iteration` under connection churn, **silently dropping a market
  broadcast to every client after the mutation point** and skipping the event-bus
  publish that follows it. Reproduced at 200 concurrent sockets with 14,057
  open/close cycles. One line fixes it; the sibling method
  `broadcast_to_channel` already does it correctly.
* **L-3 (P1)** — bcrypt on the event loop, above.

**Everything else held up, and several things held up better than expected.**
Provider failure is contained completely: with the market provider injected at
30% errors, 10% timeouts, or 800 ms of added latency, and with the AI provider at
6 s plus 20% rate-limiting, the system produced **zero 5xx and zero timeouts in
every phase**, and AI degradation did not contaminate the rest of the API
(`api p95` stayed at 30.5 ms while `ai p95` sat at the injected 6,152 ms). Rate
limiting was exact at its boundary — 120 served, then 429 with `Retry-After` on
100% of rejections — and a throttled identity never affected a bystander
(**0 of 39** bystander requests blocked). The 60-second quote cache collapsed
**7,044 quote-enriched requests into 583 upstream fetches (91.7%)** with no
thundering herd at TTL expiry, answering PH3.4 §21.3 directly. 150 concurrent
WebSocket connections held for 75 s with **zero errors, zero early closes, and a
2 ms ping→pong p95**.

**No external provider received any load.** Verified structurally (every outbound
socket from the backend was loopback: MongoDB, Redis, and the two local mocks) and
by construction (an origin override for market data, the SDK's own
`ANTHROPIC_BASE_URL` for AI).

**No functional or security regression.** Backend 2,176 → **2,188 passed** (+12,
this sprint's tests), PH1 security **452 passed, unchanged**, frontend **319
passed**, production build green, and a production-shaped import confirms the
provider override is **inert by default**.

**Two of this sprint's own results were wrong before they were right**, and both
are recorded in §22 because the error is instructive in each case: a 4.4% "error
rate" that was the risk engine correctly refusing over-drawn paper orders, and 83
consecutive CSRF failures that were the harness reusing a token the server had
correctly rotated. Neither was reported as a defect.

---

## 2. Test Environment

Load tests ran against a **dedicated non-production stack**. No production
credential, database, broker, payment account, or provider key was used at any
point.

| Component | Value |
|---|---|
| Host | Apple Silicon (Darwin 25.5.0), 8 cores, 8 GB RAM, local workstation |
| Backend | `uvicorn server:app`, **1 worker**, Python 3.11.15 (`backend/venv`) |
| `APP_ENV` | **`staging`** — see below |
| MongoDB | 7.x, local `127.0.0.1:27017`, database **`stockassist_loadtest`** |
| Redis | **7.2.14** in Docker, `maxmemory 256mb`, `allkeys-lru`, persistence off |
| Market data | **local mock** (`scripts/load/mocks/market_provider.py`) on `:9020` |
| AI provider | **local mock** (`scripts/load/mocks/ai_provider.py`) on `:9030` |
| Load driver | **k6 v2.2.0** (go1.26.5, darwin/arm64), same host |
| Frontend | not exercised — this sprint measures the API and real-time tiers |

### 2.1 Why `APP_ENV=staging` and not `development`

`security/secrets.py` classifies `development` and `testing` as
`LENIENT_ENVIRONMENTS`, which relaxes configuration gates. A capacity number
measured with relaxed security configuration would not describe the deployment it
is supposed to predict. `staging` runs every gate that is not keyed specifically
on `== production`. The runner's preflight **refuses to start** unless the target
reports `staging`.

### 2.2 The load driver shares a host with the system under test

Stated plainly because it bounds every number here. k6 and the backend compete
for the same 8 cores. At the saturation ceiling the backend used one core fully
and k6 used more; there was headroom, but the two are not isolated. Figures at or
near the ceiling should be read as **lower bounds** on what dedicated hardware
would produce.

### 2.3 Explicitly not measured, and why

Per the brief's requirement to mark rather than estimate:

| Metric | Status | Why |
|---|---|---|
| Multi-worker / multi-instance scaling | **NOT MEASURED** | One uvicorn worker was tested. §20 extrapolates *structurally* and labels it as such. |
| Real network RTT to MongoDB / Redis | **NOT REPRESENTATIVE** | Both are loopback. Multiply the per-request query floor (§12) by real RTT for a deployment estimate. |
| Frontend paint metrics / Lighthouse | **NOT MEASURED — out of scope** | Still owed by roadmap PH3.7; carried forward (§25). |
| Real AI provider latency | **NOT MEASURED, deliberately** | Brief §15 forbids it. The mock's latency is a *chosen input*, not a measurement, and is never reported as one. |
| Real market-data provider latency | **NOT MEASURED, deliberately** | Brief §14. PH3.4 §12 measured it (236 ms per 10-symbol batch, pooled) and that number stands. |
| CPU as a continuous series | **SAMPLED, not continuous** | `ps` at 2 s intervals during one 400 rps run (§18). `/api/metrics` exposes RSS and FDs but no CPU gauge — noted as a PH3.7 gap. |
| Network utilisation | **NOT MEASURED** | Loopback only; the number would describe nothing. |

---

## 3. Infrastructure Configuration

Every value that governs a limit observed in this report.

| Setting | Value | Source |
|---|---|---|
| uvicorn workers | **1** | run command |
| `REDIS_MAX_CONNECTIONS` | **24** (default) | `infrastructure/redis_client.py` |
| `REDIS_CIRCUIT_FAILURE_THRESHOLD` | 5 | same |
| `REDIS_CIRCUIT_RESET_SECONDS` | 10.0 | same |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | 2.0 | same |
| `REDIS_STATS_INTERVAL_SECONDS` | 10 (default 30) | load env — more samples per run |
| Outbound HTTP pool | `max_connections=20`, `max_keepalive=10` | `services/http_client.py` (PH3.4 O-2) |
| Provider timeouts | 8 s / 10 s / 12 s by call site | `services/real_market.py` |
| Quote cache TTL | 60 s | `real_market.CACHE_TTL` |
| `AUTHENTICATED_API` | **120 req / 60 s per user** | `security/rate_limit.py` |
| `PUBLIC_API` | **60 req / 60 s per IP** | same |
| `LOGIN` | 5 failures / 15 min per ip:account, escalating | same |
| `REFRESH` | 20 / 60 s per session | same |
| bcrypt cost | **12** | `security/passwords.py` |
| Rate-limit store | **MongoDB** | see §9.3 |
| `DISABLE_BACKGROUND_ENGINE` | **0 — background work left ON** | load env |
| `LOG_LEVEL` | `WARNING` | load env |

**The background workers were deliberately left running.** The heartbeat engine,
the 10-second market broadcast loop, the AI monitoring loop and the APScheduler
cron jobs all generate work on a timer. Switching them off would have produced a
cleaner graph describing a deployment that does not exist — and PH3.4 §21.4
specifically asked whether they compete with request traffic for the same pools.
They do; that is part of what §12 and §13 measure.

### 3.1 One environment change was required to make the load environment work

`backend/server.py` line 6 is `load_dotenv(ROOT_DIR / '.env', override=True)`. It
runs at *import* time and `override=True` means a developer's `backend/.env` wins
over anything exported into the environment. The first attempt to boot the load
stack therefore ran against **the development database and the developer's real
Anthropic API key**, while the process environment showed the load values. Every
login returned 401 and it read as a seeding bug.

The load environment now sets **`PYTHON_DOTENV_DISABLED=1`** — python-dotenv's own
supported kill switch, the same mechanism and the same reason as
`backend/tests/_testenv.py` (PH3.1). The runner's preflight additionally
**authenticates a seeded account before every run**, because that is the only
check that can prove which database the server is actually attached to.

---

## 4. Load-Test Tool

**k6 v2.2.0.** No load-testing framework existed in the repository; one was
chosen, and only one was installed.

**Why k6 over Locust or Artillery.** The deciding factor was not the language.
It was that k6 supports `constant-arrival-rate` and `ramping-arrival-rate`
executors as first-class citizens. Arrival rate is the correct instrument for a
saturation search: with a VU-count executor, a system that slows down simply
*receives less traffic*, so it never reveals its ceiling — the test silently
becomes easier exactly when it should become harder. §17's entire result depends
on holding offered load constant while the system struggles. k6 also runs as a
single static binary with no runtime dependency on a package index, which matters
for a workflow that must still run during an incident.

```
brew install k6          # macOS
k6 version               # v2.2.0 (commit/devel, go1.26.5, darwin/arm64)
```

Scenario sources live in `scripts/load/k6/`. There is **no remote import** in any
of them — a load test that cannot start because a CDN is down is a load test
nobody runs — so the k6 summary helpers are written inline rather than pulled
from jslib.

---

## 5. Traffic Model

### 5.1 The mix, and the assumption behind it

| Scenario | Share | Contents |
|---|---:|---|
| **B — Authenticated user** | 45% | `/auth/me`, watchlist, portfolio summary, notifications, unread count, active trades |
| **C — Active trader** | 30% | market overview, stock detail, watchlist, portfolio, risk validate, **paper** order, paper P&L |
| **D — AI user** | 10% | AI status, AI activity, market context, `POST /api/chat` |
| **A — Anonymous** | 10% | liveness, readiness, public stock universe |
| **E — Admin** | 5% | admin dashboard, users, audit logs, user analytics |

**ASSUMPTION, stated rather than buried.** StockAssist has no production telemetry
yet, so this mix is derived from the product's own structure — which screens
exist, which is the default post-login route, which are gated behind a plan or a
role — **not from observed traffic**. It should be replaced with measured
proportions once PH3.7 monitoring is live. Every capacity statement in §20 is
conditional on this mix.

### 5.2 Each iteration is a page visit, not a request

A user does not call `GET /api/watchlist` in a loop. They open a screen that fans
out to five or six endpoints, then read it for several seconds. Each flow
therefore issues a realistic fan-out and then sleeps 3–10 s. This is what makes
the resulting throughput mean something about capacity — and it is also why the
mixed-traffic ladder cannot find the ceiling on its own (§17).

### 5.3 Anonymous traffic is deliberately small

`PUBLIC_API` is 60 requests/minute **per client IP**, and every virtual user in
this test shares one source address. Driving anonymous traffic hard from a single
host would measure the rate limiter and would starve the other scenarios of the
same budget. Rate-limit behaviour is validated deliberately and separately (§9).

**No `X-Forwarded-For` spoofing was used to widen that budget.** It would have
worked — `client_ip()` honours the first XFF hop with no trusted-proxy check —
and it would have been exactly the "disabling a security control to improve a
benchmark number" the brief forbids. The observation is recorded as a security
finding instead (§19, S-1).

### 5.4 Trading is paper-only

Scenario C submits through `POST /api/paper/trade`, which is simulated end to end
and touches no broker. **No real order was placed at any point.** `POST /api/trades`
with a `broker` field — the path that reaches a live broker — was never exercised.

### 5.5 Fixtures

`backend/scripts/seed_load_fixtures.py`, fixed seed `20260814`:

| Collection | Rows | Per user |
|---|---:|---:|
| users | 251 | — (250 users + 1 admin) |
| trades | 6,250 | 25 |
| chat_messages | 5,000 | 20 |
| notifications | 5,000 | 20 |
| watchlist | 3,000 | 12 |
| holdings | 2,000 | 8 |
| orders | 1,250 | 5 |

Per-user volumes match the PH3.4 §3.1 profiling corpus exactly, so the PH3.4 →
PH3.5 comparison in §21 is like-for-like. A different corpus would make every
latency delta unattributable — it could be the concurrency, or it could be that
there is more data to read.

All 250 accounts share one password, hashed **once** and reused. bcrypt embeds its
salt, so one hash verifies for all of them through the application's real
`verify_password`; nothing about the login path is weakened, and each login still
pays a full cost-12 verification. Hashing 250 times individually would have cost
57 seconds of setup for no benefit.

---

## 6. Test Scenarios

| Command | Script | Purpose |
|---|---|---|
| `load-test.sh smoke\|baseline\|moderate\|high\|stress` | `scenarios.js` | Mixed traffic, A–E, at five sizes |
| `load-test.sh saturation` | `saturation.js` | Read-path throughput ceiling (arrival-rate) |
| `load-test.sh auth` | `auth.js` | Login / refresh / logout / logout-all throughput |
| `load-test.sh ratelimit` | `ratelimit.js` | Boundary, rejection shape, bystander isolation |
| `load-test.sh websocket` | `websocket.js` | Real-time connections, hold and churn |
| `load-test.sh failure` | six phases | Controlled provider failure injection |

`smoke` through `stress` are **one script at five sizes**, not five scripts. Five
files would drift and the comparison between them would quietly stop meaning
anything.

---

## 7. Concurrency Levels

| Stage | Peak VUs | Ramp | Hold | Cool-down |
|---|---:|---|---|---|
| smoke | 5 | 5 s | 30 s | 5 s |
| baseline | 10 | 15 s | 60 s | 10 s |
| moderate | 25 | 20 s | 90 s | 10 s |
| high | 50 | 30 s | 120 s | 15 s |
| stress | 100 | 40 s | 120 s | 20 s |

Saturation search offered **50 → 600 requests/second**; WebSocket load ran at
**50, 150 and 200** concurrent connections.

**These are the levels tested, not a capacity claim.** §20 reports what was
actually sustained.

---

## 8. Ramp-Up Strategy

Every stage ramps, holds, and drains. Slamming peak concurrency into a cold
process measures connection-pool construction and lazy imports — PH3.4 §3.3
nearly attributed 288 ms of one-time import cost to an endpoint that runs in
11 ms — not steady-state behaviour. Percentiles are taken across the whole run
including ramp, which is conservative: it can only make the reported tail worse.

For the saturation sweep the ramp finds *that* there is a knee; **flat
constant-rate holds** then establish steady-state latency at each rate, because a
ramp's percentiles blend every step it passed through on the way up.

---

## 9. Acceptance Thresholds

Declared in `scripts/load/k6/lib/config.js` **before any result was seen**, so the
bar cannot have been fitted to the measurement.

| Class | p95 | p99 | Rationale |
|---|---|---|---|
| `kind:api` — ordinary DB-backed reads | **< 500 ms** | < 1,000 ms | The brief's default; generous for work PH3.4 measured at ≤11 ms of application code |
| `kind:quote` — provider-enriched reads | < 1,500 ms | < 3,000 ms | Dominated by provider transport (PH3.4 §7: >90% of total), a property of the provider |
| `kind:ai` — AI-provider calls | < 5,000 ms | — | The mock answers in 900 ms by configuration; the interesting quantity is the excess |
| `kind:auth` — login | < 2,000 ms | — | See below |
| 5xx rate | **< 1%** | | Gating |
| Timeout rate | **< 1%** | | Gating |
| 429 rate | recorded, **not gated** | | A 429 is the rate limiter working; §9.2 explains every one observed |
| Functional checks | **> 99%** | | Gating |

**Login gets the loosest bar deliberately, and not because logins may be slow.**
bcrypt at cost 12 is a *security control*. A sub-500 ms threshold on login would
be a threshold on the cost factor, and the brief (§24) forbids trading a security
control for a benchmark number. The figure is reported and interpreted (§11)
rather than gated.

### 9.1 Results against the thresholds

| Stage | api p95 | ai p95 | login p95 | 5xx | timeouts | checks | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| smoke (5) | 32.1 ms | 1,377 ms | 953 ms | 0% | 0% | 100% | ✅ |
| baseline (10) | 44.3 ms | 1,986 ms | 981 ms | 0% | 0% | 100% | ✅ |
| moderate (25) | 27.4 ms | 1,904 ms | 1,027 ms | 0% | 0% | 100% | ✅ |
| high (50) | 29.3 ms | 1,133 ms | 998 ms | 0% | 0% | 100% | ✅ |
| stress (100) | 45.1 ms | 1,900 ms | 1,070 ms | 0% | 0% | 100% | ✅ |

**All thresholds met at every tested concurrency.**

### 9.2 The 429s, explained rather than excused

The only stage with any 429 is `stress`, at **0.44%** — 57 responses out of
12,974. All 57 came from the rate-limit middleware, which rejects before route
resolution. Their cause is the per-user `AUTHENTICATED_API` tier of 120 req/min:
at 100 VUs the trader flow's shortest think-time occasionally puts a single
synthetic user over budget within a window. This is the limiter working as
designed on a synthetic traffic pattern, not a capacity signal.

*(A small observability consequence is worth recording: because the middleware
returns before the router runs, all 57 are labelled `route="<unmatched>"` in
`http_requests_total`, so a 429 cannot be attributed to an endpoint from metrics
alone. Filed as O-1 in §19.)*

### 9.3 A configuration finding, not a threshold one

PH3.4 §21.5 asked PH3.5 to "verify the Redis-backed path is in use" for rate
limiting. **There is no Redis-backed rate-limit store.** `security/rate_limit.py`
defines the `RateLimitStore` interface and exactly one implementation,
`MongoRateLimitStore`; `get_limiter()` always constructs it. The module docstring
describes a Redis store as a future possibility, which is accurate, but the
roadmap item reads as though the path exists. It does not. Recorded as **L-6**
(§19) so the expectation and the code stop disagreeing.

---

## 10. API Results — mixed traffic

Client-side, k6, whole run including ramp.

| Stage | VUs | Requests | rps | p50 | p90 | p95 | p99 | max | 5xx | 429 | 4xx | checks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| smoke | 5 | 200 | 4.20 | 10.9 | 29.7 | 464.6 | 1,019.3 | 1,415.8 | 0% | 0% | 0% | 100% |
| baseline | 10 | 670 | 7.24 | 11.3 | 29.9 | 55.8 | 979.7 | 2,191.4 | 0% | 0% | 0% | 100% |
| moderate | 25 | 2,345 | 18.33 | 9.0 | 22.6 | 33.4 | 981.2 | 2,487.7 | 0% | 0% | 0% | 100% |
| high | 50 | 6,320 | 36.50 | 9.0 | 22.4 | 36.0 | 927.5 | 2,513.8 | 0% | 0% | 0.05% | 100% |
| stress | 100 | 12,974 | 69.68 | 8.3 | 28.9 | 66.4 | 954.3 | 2,563.2 | 0% | 0.44% | 0.10% | 100% |

**Reading this table.**

*The median does not move.* 10.9 ms at 5 VUs, 8.3 ms at 100. Twentyfold
concurrency, no change. The application is not close to saturation anywhere in
this ladder.

*The p99 is pinned near 950–1,020 ms at every single stage.* That is not
degradation — degradation would grow with load. It is a **fixed cost** appearing
in the tail: the AI mock's 900 ms and bcrypt's 234 ms. The mixed p95 at `smoke`
(464.6 ms) is higher than at `moderate` (33.4 ms) for the same reason and is a
small-sample artefact — with only 200 requests, the run's handful of logins and
chat calls land above the 95th percentile; by 2,345 requests they no longer do.
This is why `api p95` is reported separately from the blended figure.

*The 4xx are correct application behaviour.* 13 of them, all
`400 Insufficient paper capital` — see §22.1.

### 10.1 Server-side latency by route (stress, 100 VUs)

From the application's own `http_request_duration_seconds` histogram — an
independent view of the same run. **Interpolated from cumulative bucket edges, so
less precise than k6's percentile over raw samples**, and reported alongside
rather than instead of it.

| Route | n | p50 | p95 | p99 |
|---|---:|---:|---:|---:|
| `POST /api/chat` | 121 | 860.1 | 2,365.4 | 3,991.7 |
| `POST /api/auth/login` | 90 | 424.5 | 1,362.5 | 2,272.5 |
| `GET /api/admin/dashboard` | 79 | 28.1 | 315.6 | 605.0 |
| `GET /api/portfolio/summary` | 976 | 13.6 | 49.6 | 257.5 |
| `GET /api/watchlist` | 1,775 | 13.8 | 48.1 | 216.8 |
| `GET /api/trades/active` | 976 | 9.0 | 46.5 | 281.0 |
| `GET /api/portfolio` | 799 | 11.8 | 44.9 | 200.1 |
| `GET /api/market/overview` | 920 | 5.9 | 34.7 | 241.4 |
| `POST /api/paper/trade` | 799 | 7.1 | 26.1 | 250.4 |
| `POST /api/trades/validate` | 799 | 5.7 | 21.7 | 48.2 |
| `GET /api/notifications` | 976 | 5.6 | 21.6 | 48.6 |

The client-side and server-side views agree closely, which is itself a result:
**there is no significant queueing outside the application** at this load. Where
they would diverge is the interesting case, and it does not arise until the
saturation ceiling (§17).

---

## 11. Authentication Results

`scripts/load/k6/auth.js`. Valid credentials only, one account per VU, no
brute-force pattern, no lockout triggered at any point — the `LOGIN` policy counts
failures only, and there were none (`sa_login_fail = 0` in all runs).

| Concurrent VUs | Logins/sec | min | median | p95 | max |
|---:|---:|---:|---:|---:|---:|
| 5 | **4.02** | 233.6 ms | 468.4 ms | 715.7 ms | 949.1 ms |
| 10 | **3.98** | 239.2 ms | 751.0 ms | 1,210 ms | 1,660 ms |
| 25 | **4.06** | 241.0 ms | 1,680 ms | 2,710 ms | 3,380 ms |

Session lifecycle in the same runs:

| Operation | 5 VUs median | 10 VUs median | 25 VUs median | floor (min) |
|---|---:|---:|---:|---:|
| `POST /api/auth/refresh` | 241.1 ms | 536.0 ms | **1,670 ms** | 3.2 ms |
| `POST /api/auth/logout` | 238.6 ms | 490.9 ms | **1,430 ms** | 2.6 ms |
| `POST /api/auth/logout-all` | 241.1 ms | 491.7 ms | **1,230 ms** | 3.7 ms |

### 11.1 The finding (L-3, P1)

**Login throughput does not increase with concurrency.** 4.02, 3.98, 4.06 per
second at 5, 10 and 25 concurrent users. The reciprocal of the bcrypt cost
measured directly on this host — 234 ms — is 4.27/s. Login is perfectly
serialised.

**The proof is in the other three rows.** `refresh`, `logout` and `logout-all`
perform no password hashing whatsoever; their floor is 2.6–3.7 ms. Yet their
median rises 241 → 536 → 1,670 ms in lockstep with login. They are not slow;
**they are queued behind bcrypt on the same event loop.**

`server.py::login` calls `verify_password(...)` synchronously inside an `async def`
handler. A synchronous 234 ms CPU-bound call in an async handler blocks the entire
worker's event loop for its whole duration — every other in-flight request on that
worker stalls. It also cannot use more than one core, on an 8-core host.

**This is not a criticism of bcrypt cost 12, which is correct and must not be
lowered.** The fix is to move the call off the loop
(`await asyncio.to_thread(verify_password, ...)` or a bounded executor), which
preserves the cost factor exactly while letting verifications run in parallel and
freeing the loop for everything else. Registration (`hash_password`) has the same
shape.

**This corrects PH3.4 §13**, which audited blocking operations and concluded that
"no synchronous blocking operation was found in an async request path." That
conclusion was reached from hermetic in-process profiling where `/api/auth/*`
measured 0.5 ms warm — the double returns instantly and no real hash is verified.
The audit was sound; the instrument could not see this one. It took real
credentials under concurrency.

---

## 12. Database Results

**MongoDB was never the bottleneck at any tested level, and never queued.**

| Stage | Requests | Queries | Updates | Inserts | Queries/request | Connections | Queue (r/w) |
|---|---:|---:|---:|---:|---:|---:|---:|
| smoke | 200 | 918 | 198 | — | 4.6 | 18 | 0 / 0 |
| baseline | 670 | 2,869 | 679 | 12 | 4.3 | 18 | 0 / 0 |
| moderate | 2,345 | 9,294 | 2,405 | 132 | 4.0 | 18 | 0 / 0 |
| high | 6,320 | 25,336 | 6,472 | 640 | 4.0 | 18 | 0 / 0 |
| stress | 12,974 | 50,702 | 13,244 | 1,319 | 3.9 | 18 | **0 / 0** |
| saturation @400 rps | 18,001 | 75,808 | 18,031 | 600 | 4.2 | 28 | **0 / 0** |

**Queries per request is flat at ~4.0 across a 65× range of load.** That is the
N+1 signature's absence, confirmed dynamically at scale rather than only by the
static assertions PH3.4's `TestQueryCountDoesNotScaleWithData` makes. It also
matches PH3.4 §3.1's measured per-request floor of 4–5 queries exactly.

**The `updates` column is the rate limiter.** One `update_one($inc)` per request,
on `rate_limits` — plus a `find_one` to read the result back, and a `find_one` for
the block check. Three of the ~4 operations on every authenticated request are the
throttle, not the feature. This is the most-executed database work in the system,
exactly as PH3.4 §19.3 predicted, and PH3.4's deferred **O-7**
(`find_one_and_update(returnDocument=AFTER)`) would remove one of the three. At
stress that is **~13,000 round trips saved per 13,000 requests.**

**Connections stayed at 18** (28 under saturation) against Mongo's 51,182
available. No connection pressure, no `rejected`, no
`queuedForEstablishment`. `globalLock.currentQueue` was **0 readers / 0 writers in
every single snapshot** across every run. `currentOp` filtered to the load database
returned **no operation running longer than 1 second** at any point.

No slow query was found. PH3.4's O-1 (12 indexes across 6 collections) is doing
its job: the queries that were COLLSCANs before that sprint would have been the
first thing to fall over here, and instead the database is the quietest tier in
the system.

---

## 13. Redis Results

**Redis is the first bottleneck, and it is a configuration bottleneck rather than
a capacity one.** The Redis *server* was never stressed; the *client pool* was.

### 13.1 Under mixed traffic (default pool of 24)

| Stage | Commands | Failures | Failure rate | Circuit opens |
|---|---:|---:|---:|---:|
| smoke (5 VU) | 1,054 | 15 | 1.4% | 3 |
| baseline (10) | 3,386 | 16 | 0.5% | 1 |
| moderate (25) | 13,007 | 129 | 1.0% | 12 |
| high (50) | 35,818 | 342 | 1.0% | 28 |
| stress (100) | 71,963 | 1,251 | 1.7% | **129** |

Every failure is `ConnectionError: Too many connections` — redis-py's message for
a **client-side pool exhaustion**, not a server refusal. Confirmed against the
server: `connected_clients` peaked at 25 (24 pooled + 1 Pub/Sub subscriber, which
holds its own connection outside the command pool) against `maxclients 10000`,
with `rejected_connections` at 0 and no evictions.

### 13.2 Under the saturation sweep — the mechanism, isolated

| Offered rps | Commands | Failures | Failure rate | Circuit opens | p95 | p99 |
|---:|---:|---:|---:|---:|---:|---:|
| 50 | 10,926 | 93 | 0.9% | 11 | 146 ms | 459 ms |
| 100 | 21,553 | 100 | 0.5% | 8 | **21 ms** | 146 ms |
| 150 | 30,715 | 1,138 | 3.7% | 96 | 187 ms | 1,414 ms |
| 200 | 38,986 | 3,854 | 9.9% | 313 | 515 ms | 2,298 ms |
| 250 | 35,383 | 11,224 | **31.7%** | 979 | **10,485 ms** | 11,655 ms |
| 300 | 35,251 | 10,816 | **30.7%** | 967 | 11,888 ms | 12,364 ms |

### 13.3 Why it cascades instead of merely slowing down

Three design choices compose into a cliff:

1. **The pool does not queue.** `aioredis.from_url(..., max_connections=24)`
   builds a non-blocking `ConnectionPool`, which raises immediately when
   exhausted. A `BlockingConnectionPool` would make a burst wait a few
   milliseconds; this one makes it fail.
2. **The retry cannot help.** `retry_on_error=[ConnectionError]` retries twice
   with backoff — but the pool is still full, so both retries fail too, and the
   request has now spent its backoff budget on a certainty.
3. **The breaker is global.** Five consecutive failures open the circuit, and the
   circuit is per-process, not per-key. For 10 seconds **every cache read in the
   process** returns from the bounded in-process dict instead of Redis — so every
   quote misses and goes upstream, at the exact moment the system is busiest.

The pool is sized at 24 while the application's own fan-out is per-symbol: a
12-symbol watchlist issues 12 concurrent `cache_get` calls, and the outbound HTTP
pool (`max_connections=20`, PH3.4 O-2) is separately sized to allow 20 concurrent
provider calls. Nothing coordinates the two, and the background heartbeat and
10-second market loop draw from the same 24.

### 13.4 The controlled experiment

Same sweep, same build, same fixtures, `REDIS_MAX_CONNECTIONS=200`:

| Offered rps | Delivered | p50 | p95 | p99 | Redis failures | Circuit opens |
|---:|---:|---:|---:|---:|---:|---:|
| 150 | 150.0 | 7.9 ms | **16.6 ms** | 40.0 ms | **0** | **0** |
| 200 | 200.0 | 7.8 ms | **42.4 ms** | 193.3 ms | **0** | **0** |
| 250 | 250.0 | 6.3 ms | **11.1 ms** | 23.0 ms | **0** | **0** |
| 300 | 300.0 | 6.9 ms | **28.5 ms** | 166.5 ms | **0** | **0** |
| 400 | 400.0 | 6.0 ms | **29.1 ms** | 145.7 ms | **0** | **0** |
| 500 | 407.3 *(4,171 dropped)* | 59.7 ms | 568.6 ms | 13,242 ms | 3,056 | 61 |
| 600 | 409.0 *(8,593 dropped)* | 207.9 ms | 1,153 ms | 24,682 ms | 2,698 | 67 |

**At 250 rps, p95 falls from 10,485 ms to 11.1 ms — a 944× improvement from one
environment variable.** Peak observed pool occupancy was ~78 of 200, so 200 has
headroom and **~100 would be adequately sized**; 200 was chosen to make the
experiment unambiguous rather than as a recommendation.

The Redis failures that reappear at 500–600 rps are a *symptom* of the saturated
worker, not the cause: the pool reads fully returned (`available: 200`) at
snapshot time, and the worker is CPU-pinned (§18).

**No Redis change was made in this sprint.** All headline results in §10 are
reported at the **shipped default of 24**. The experiment is evidence for the
owning sprint, not a change smuggled in under a measurement.

---

## 14. WebSocket Results

> **A correction to the brief.** The brief refers to Socket.IO throughout.
> **StockAssist does not use Socket.IO.** The transport is a *native FastAPI
> WebSocket* at `/api/ws`, with an in-process `ConnectionManager` (`server.py`)
> doing fan-out and Redis Pub/Sub bridging events across processes
> (`services/realtime/event_bridge.py`). This matters for results as well as
> wording: there is no room abstraction, no polling fallback, and no
> acknowledgement protocol, so the failure modes are different ones.

Events are the application's **own** server-driven pushes — the 10-second market
broadcast loop and the heartbeat engine. No synthetic events were manufactured;
doing so would have measured the generator.

| Test | Sockets | Errors | Closed early | Events received | Subscribe ack | Held full | ping→pong p95 | connect p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 concurrent, 75 s hold | 100 | **0** | **0** | 2,112 | 100% | 100% | 2.0 ms | 9.6 ms |
| 150 concurrent, 75 s hold | 300 | **0** | **0** | 6,496 | 100% | 100% | 2.0 ms | 6.4 ms |
| 200 concurrent, 1.5 s churn | **14,057** | **0** | **0** | 8,602 | 100% | 100% | 7.0 ms | 18.2 ms |

**Steady-state real-time behaviour is excellent.** No unexpected disconnects, no
errors, sub-10 ms round trips at 150 held connections, and RSS moved 61.4 → 47.9 MB
across the 150-connection run (i.e. not at all — noise). The socket path put
almost no pressure on Redis (415–450 commands per run).

### 14.1 L-2 — a broadcast is silently dropped under churn (P1)

The churn scenario exists because of a specific hypothesis.
`ConnectionManager.broadcast()` and `send_to_user()` iterate `self.active` /
`self.user_connections` **directly** while awaiting `ws.send_text()` inside the
loop, whereas the sibling `broadcast_to_channel()` iterates a `list(...)` copy. In
CPython, mutating a set during iteration raises `RuntimeError`, and every `await`
in that loop is a point where another task can run `connect()` or `disconnect()`.

Under 200 concurrent sockets with 14,057 open/close cycles in 90 seconds, it
fired:

```
{"level":"ERROR","logger":"root",
 "message":"Broadcast error: Set changed size during iteration"}
```

**Once** in that window. The consequences are worse than the frequency suggests:

* The broadcast **aborts mid-fan-out**. Every socket after the mutation point does
  not receive that market update. The clients are still connected and see nothing
  wrong; they simply get stale data until the next tick.
* The `except` that catches it is the one wrapping the **whole** loop body
  (`server.py:3150`), so the `event_bus.publish("market.index.updated", ...)`
  calls that follow the broadcast are skipped too — meaning the **Redis
  cross-process bridge also misses that tick**, and other instances' clients miss
  it as well.
* It is logged at ERROR and otherwise invisible. No metric increments.

**Remediation is one word,** and the codebase already contains the correct
pattern three lines away: iterate `list(self.active)` in `broadcast`, and
`list(conns)` in `send_to_user`. Not applied here (§19 explains why), and handed
off with the reproduction command.

### 14.2 Not measured

**Per-event delivery latency.** The payloads carry no server-side send timestamp,
so deriving one from the receive clock would be measuring this script rather than
the system. Recorded as a gap for PH3.7 — a `sent_at` field on the event envelope
would make it measurable.

---

## 15. AI Results

All AI traffic went to `scripts/load/mocks/ai_provider.py` via the Anthropic
SDK's own `ANTHROPIC_BASE_URL` — no application change, and no request reached a
real provider.

**The mock's latency is a chosen input, not a measurement, and is never reported
as one.** The question this sprint can answer is not "how fast is the model" but
"what happens to the process while N requests are parked waiting on one".

| Stage | `/api/chat` calls | Mock max concurrent | ai p95 (client) | api p95 (same run) |
|---|---:|---:|---:|---:|
| baseline (10 VU) | 5 | 1 | 1,986 ms | 44.3 ms |
| moderate (25) | 26 | 2 | 1,904 ms | 27.4 ms |
| high (50) | 58 | 3 | 1,133 ms | 29.3 ms |
| stress (100) | 121 | **4** | 1,900 ms | **45.1 ms** |

**AI requests do not pile up, and AI latency does not contaminate the rest of the
API.** At 100 concurrent users the provider mock saw at most **4** simultaneous
in-flight calls, and `api p95` on the same run stayed at 45 ms while AI calls were
sitting at ~1.9 s. The await is genuinely non-blocking; a slow provider costs the
requests that are waiting on it and nothing else.

Server-side, `POST /api/chat` measured p50 860 ms against the mock's 900 ms
configured latency — the application's own contribution to that route is
**single-digit milliseconds**, which is consistent with PH3.4's finding that the
AI-chat continuity query is now index-served (O-1 fixed a 12,000-document
COLLSCAN on that exact path).

Degraded-provider behaviour is covered in §17, phase 4.

---

## 16. Trading Results

Paper trading only. **No real order was placed and no broker was contacted.**

| Endpoint | Stage | n | p50 | p95 | p99 |
|---|---|---:|---:|---:|---:|
| `POST /api/trades/validate` | stress | 799 | 5.7 ms | 21.7 ms | 48.2 ms |
| `POST /api/paper/trade` | stress | 799 | 7.1 ms | 26.1 ms | 250.4 ms |

The write path is the **fastest** surface in the product under load — faster than
most reads, because it touches one collection and enriches nothing. Across the
stress run the system executed **1,319 inserts and 13,244 updates** with zero
write conflicts, zero lock queueing, and no failed order.

The risk engine held under concurrency: 13 orders were correctly rejected with
`400 Insufficient paper capital` when a synthetic account exhausted its
₹100,000 balance. That was initially mistaken for an error-rate finding — see
§22.1.

---

## 17. Failure Testing

Six phases, `baseline` traffic (10 VUs) each, run back to back on the same
process so every number has something to be compared against from the same
minute. Faults are injected into the mocks' control endpoints, never into
infrastructure.

| Phase | Injected | p50 | p95 | p99 | max | api p95 | ai p95 | **5xx** | **timeouts** | checks |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | none (baseline) | 9.6 | 30.6 | 971.0 | 1,535 | 26.3 | 1,386 | **0** | **0** | 100% |
| 1 | market +800 ms | 10.2 | **819.5** | 1,754 | 2,446 | 38.4 | 1,832 | **0** | **0** | 100% |
| 2 | market 30% HTTP 503 | 9.9 | **40.4** | 974.9 | 3,068 | 32.6 | 2,552 | **0** | **0** | 100% |
| 3 | market 10% timeout (30 s) | 11.0 | **45.9** | 1,003 | 8,056 | 35.4 | 4,197 | **0** | **0** | 100% |
| 4 | AI 6 s + 20% HTTP 429 | 10.5 | **38.4** | 996.6 | 6,157 | 30.5 | **6,152** | **0** | **0** | 100% |
| 5 | recovery (none) | 10.9 | 38.6 | 974.0 | 2,325 | 31.1 | 1,993 | **0** | **0** | 100% |

**Zero 5xx and zero client timeouts in every phase.** PH3.3's finding that
"containment lives at the transport boundary, so a provider timeout reaches a
route as `None`, never as an exception" is confirmed under concurrent load.

Four things in that table are worth reading carefully:

**Slowness is more expensive than failure.** Phase 1 (800 ms added latency) pushed
p95 to 819 ms; phase 2 (30% hard failures) left it at 40 ms. A 503 returns
immediately and the route takes its fallback; a slow response occupies the
request for its full duration. Any future provider-health logic should treat
latency, not error rate, as the primary signal.

**The 8-second timeout is doing exactly its job.** Phase 3 injected 30-second
stalls; the observed maximum was 8,056 ms — `fetch_yahoo_quote`'s `timeout=8`
plus overhead. The stall is bounded precisely where the code says it is, and p95
barely moved (45.9 ms) because only the affected requests paid.

**AI degradation is isolated.** Phase 4 held the AI provider at 6 s and rejected
20% with 429s. `ai p95` landed at 6,152 ms — the injected value, with ~150 ms of
application overhead and **no amplification** — while `api p95` on the same run
stayed at **30.5 ms**. A degraded AI provider does not degrade the product.

**Recovery is complete and immediate.** Phase 5 returns to phase 0 within noise,
with no residual circuit-breaker state or connection-pool damage.

---

## 18. Resource Utilisation

| Stage | RSS before → after | Open FDs | Mongo conns | Redis pool max in use |
|---|---|---:|---:|---:|
| smoke | 50 → 48 MB | 43 → 53 | 18 | ≤24 |
| baseline | 55 → 48 MB | 53 → 59 | 18 | ≤24 |
| moderate | 45 → 33 MB | 59 → 65 | 18 | ≤24 |
| high | 50 → 36 MB | 65 → 62 | 18 | ≤24 |
| stress | 60 → 66 MB | 46 → 64 | 18 | ≤24 |
| saturation @400 rps (pool 200) | 55 → 87 MB | 180 → 183 | 28 | ~78 |

**Memory is flat.** RSS oscillates between roughly 33 MB and 110 MB across every
run — it goes *down* as often as up — which is Python's allocator and GC, not a
trend. Across a session that served well over 150,000 requests, no monotonic
growth appeared. **File descriptors track concurrent connections and return
afterwards**, with no ratchet.

This is a clean input to PH3.6 rather than a problem for it: there is **no
evidence of a memory or descriptor leak** at any tested load, and PH3.6 should
start from that baseline rather than hunting one. Sustained-soak behaviour (hours,
not minutes) remains unmeasured and is PH3.6's to establish (§25).

### 18.1 CPU

Sampled with `ps` at 2-second intervals during a sustained 400 rps run
(`REDIS_MAX_CONNECTIONS=200`):

```
0.2  90.9  100.0 100.0 100.0 100.0 100.0 100.0 100.2 100.0
100.0 100.0 100.1 100.0 98.3  99.3  99.2  100.0 100.0 99.4     (%CPU)
```

**Pinned at 100.0% of one core for the entire hold**, on an 8-core host, with RSS
steady at 60–68 MB. The saturation ceiling is CPU-bound on a single Python
process — precisely what a one-worker uvicorn deployment should be bound by, and
the reason §20's extrapolation is about worker count.

**`/api/metrics` exposes RSS and open FDs but no CPU gauge**, so CPU is the one
resource this sprint could not read from the application's own instrumentation.
Filed as O-2 (§19) for PH3.7.

---

## 19. Bottleneck Analysis

| ID | Finding | Class | Priority | Owner |
|---|---|---|---|---|
| **L-1** | Redis client pool (24) below the application's fan-out width; non-blocking pool + global circuit breaker turn a burst into a 10 s cache outage | Infrastructure / config | **P1** | **PH3.7** (config) — see below |
| **L-2** | `ConnectionManager.broadcast` / `send_to_user` iterate a mutating set; a broadcast is silently dropped mid-fan-out under churn, taking the event-bus publish with it | Application (correctness) | **P1** | **Next real-time-touching sprint** |
| **L-3** | `verify_password` (bcrypt cost 12, 234 ms) runs synchronously on the event loop; login pinned at ~4/s and every other request queues behind it | Application (concurrency) | **P1** | **Next auth-touching sprint** |
| **L-4** | Single uvicorn worker saturates one core at ~410 rps; no horizontal capacity measured | Infrastructure | **P2** | **PH3.7 / deployment** |
| **L-5** | Rate limiter costs 3 MongoDB operations on every request; PH3.4's O-7 removes one | Database | **P2** | PH3.4 §20.2 (unchanged owner) |
| **L-6** | No Redis-backed rate-limit store exists; only `MongoRateLimitStore`. PH3.4 §21.5 and the roadmap imply otherwise | Documentation / architecture | **P2** | Next security-touching sprint |
| **O-1** | Middleware 429s are labelled `route="<unmatched>"`, so a throttled request cannot be attributed to an endpoint from metrics | Observability | **P3** | **PH3.7** |
| **O-2** | No CPU gauge in `/api/metrics`; RSS and FDs are exposed but CPU is not | Observability | **P3** | **PH3.7** |
| **S-1** | `client_ip()` honours the first `X-Forwarded-For` hop with no trusted-proxy check, so an anonymous client can bypass the per-IP tier with a spoofed header | Security | **P2** | Next security-touching sprint |
| **S-2** | `/api/ws` accepts `user_id` as an unauthenticated query parameter — any client can receive another user's per-user pushes | Security | **P1** | **PH1.9** — *already known* |

### Notes on ownership

**S-2 is not a new finding.** `SECURITY_ARCHITECTURE.md` §32 already lists
WebSocket security as "❌ Not started", owned by PH1.9. It is repeated here only
because §14 exercised that endpoint at 200 concurrent connections and a reader of
this document should not conclude the socket tier was security-tested.

**L-1's remediation is a configuration change, and that is why it is P1 rather
than P0.** Setting `REDIS_MAX_CONNECTIONS≈100` in the deployment configuration
removes the observed cliff entirely (§13.4) with no code change and no rollback
risk. The deeper questions — whether the pool should be a
`BlockingConnectionPool` so a burst queues instead of failing, and whether the
circuit breaker should be per-operation rather than per-process — are design
decisions for the owning sprint, not for a measurement sprint to make.

**Nothing in this table was fixed during PH3.5.** Changing application code
mid-sprint would have invalidated every measurement taken before the change, and
the brief (§19) is explicit that findings which belong to a later sprint are to be
documented and handed off. Each entry carries the evidence and, where it is
short, the exact remediation.

---

## 20. Capacity Findings

### 20.1 What was sustained

> **Under the tested environment and workload model, the system sustained
> 100 concurrent synthetic users at 69.7 requests/second with a p50 of 8.3 ms,
> an api-class p95 of 45.1 ms, 0.00% 5xx, 0.00% timeouts and 100% of functional
> checks passing — on a single uvicorn worker.**

Every stage from 5 to 100 VUs met every acceptance threshold. **No upper bound on
concurrency was found by the mixed-traffic ladder**, because that model is
governed by its own think-time: at 100 VUs the worker was far from saturated.

### 20.2 The read-path ceiling

Found with a constant-arrival-rate search on the authenticated read fan-out
(no think time, no login, no AI, no writes):

| Configuration | Highest rate meeting p95 < 500 ms | Hard ceiling | Binding constraint |
|---|---:|---:|---|
| **Shipped default** (`REDIS_MAX_CONNECTIONS=24`) | **~150 rps** | ~217 rps | Redis client pool exhaustion → circuit cascade |
| `REDIS_MAX_CONNECTIONS=200` | **~400 rps** | ~410 rps | **CPU: one core at 100%** |

At 400 rps with the larger pool: p50 6.0 ms, p95 29.1 ms, p99 145.7 ms, 0 errors,
0 Redis failures, CPU 100.0% of one core, RSS 60–68 MB.

### 20.3 Safe operating capacity

Recommended for the tested topology (single worker, this workload model), with a
deliberate margin below the knee:

| | Default Redis pool | With `REDIS_MAX_CONNECTIONS≈100` |
|---|---:|---:|
| **Safe sustained read throughput** | **~100 rps** | **~300 rps** |
| Observed p95 at that rate | 21 ms | 28.5 ms |
| Observed p99 at that rate | 146 ms | 166 ms |
| Redis failures at that rate | 100 (0.5%) | **0** |

**Login is capacity-limited separately and much more tightly: ~4 logins/second
per worker**, regardless of concurrency (§11). This is the number to plan around
for a launch spike, a marketing event, or any mass re-authentication — it does not
improve by adding users, only by adding workers or by fixing L-3.

### 20.4 What this does *not* say

This is **not** a claim that StockAssist supports *N* users. Converting requests
per second into users requires a request-rate-per-user figure that only production
telemetry can supply, and the traffic model here is derived from the product's
structure rather than observed behaviour (§5.1). The arithmetic is also
environment-specific: one worker on a shared laptop, loopback datastores, mocked
providers.

What can be said structurally, and is labelled as extrapolation rather than
measurement: the ceiling is one CPU core on one Python process, and the tiers
behind it (MongoDB at 0 queue depth, Redis at 25 of 10,000 clients) were nowhere
near their limits — so **worker count is the first scaling lever**, and it is
untested. §25 hands that to PH3.7.

---

## 21. PH3.4 Comparison

**PH3.4's measurements are unchanged and no historical baseline was edited.**
PH3.4 measured single-request cost; PH3.5 measures behaviour under concurrency.
The comparison is therefore about whether PH3.4's conclusions *held*.

| PH3.4 claim | PH3.5 evidence | Verdict |
|---|---|---|
| Application code is not the bottleneck (≤11 ms per endpoint) | p50 flat at 8.3–11.3 ms from 5 to 100 VUs | ✅ **Held** |
| Per-request floor is 4–5 queries | Measured **3.9–4.6 queries/request** across a 65× load range | ✅ **Confirmed at scale** |
| O-1's 12 indexes eliminated every hot COLLSCAN | Mongo queue depth **0/0** in every snapshot; no slow op >1 s; 50,702 queries at stress with no degradation | ✅ **Held** |
| O-3 removed the `/api/admin/logs` N+1 | Query count flat with load; admin p95 316 ms at 100 VUs on a page of 25 | ✅ **Held** |
| O-4 parallelised the admin dashboard fan-out | `/api/admin/dashboard` p50 28.1 ms under stress | ✅ **Held** |
| >90% of quote-endpoint latency is provider transport | Failure phase 1 (+800 ms provider) moved p95 30.6 → 819.5 ms — near 1:1 pass-through | ✅ **Confirmed under load** |
| **§13: "no synchronous blocking operation in an async request path"** | **bcrypt at 234 ms on the event loop; login pinned at 4/s; refresh/logout queue behind it** | ❌ **Corrected — L-3** |

### 21.1 PH3.4's handoff questions, answered

PH3.4 §21 posed nine questions. Seven are answered here:

1. **Outbound pool ceiling (`max_connections=20`) as the market-path throughput limit.** **Not reached.** The 60 s cache collapsed 7,044 quote-enriched requests into 583 upstream fetches (§13.5 below), so the outbound pool was never the constraint. The *Redis* pool was, and PH3.4 did not anticipate it.
2. **Provider rate limits unknown.** **Still unknown, deliberately.** Probing them is load-testing a third party (brief §14).
3. **60 s quote cache hit rate; thundering herd at TTL expiry.** **Answered.** 583 upstream fetches for 7,044 quote-enriched requests at 100 VUs = **0.083 fetches per request, a 91.7% collapse**. **No thundering herd was observed** at any tested level — no latency spike aligned with TTL boundaries. The absence of single-flight coalescing that PH3.4 flagged as "the most likely load-test finding" did **not** materialise at ≤100 VUs; it remains a theoretical risk at higher fan-out and is worth re-testing at multi-worker scale, where each worker holds an independent cache.
4. **Background-worker N+1s scaling with user count.** **Not reproduced at this scale.** With 250 seeded users and background work enabled, Mongo queries per request stayed flat at ~4.0. At 10,000 users this is still a real concern; 250 does not exercise it.
5. **Rate limiter as the per-request hot spot; is the Redis path in use?** **Answered, and the premise was wrong.** The limiter is confirmed as the dominant per-request database cost (3 of ~4 operations, §12), and **there is no Redis-backed store to be in use** (§9.3, L-6). O-7 becomes more valuable, exactly as PH3.4 predicted.
6. **Redis entirely unmeasured.** **Now measured** — and it is the system's first bottleneck (§13).
7. **Socket fan-out under burst.** **Measured** (§14), and it surfaced L-2.
8. **Frontend paint metrics / CI bundle budget.** **Still not done** — out of scope here, carried to PH3.7.
9. **`chat_messages` write cost of three indexes.** **Not isolated.** Chat volume in this model (121 calls at stress) is too low to separate index-write cost from noise. Still open.

### 21.2 Bundle

Re-measured, not quoted: **172.8 KiB gzip initial load** (main.js 161,144 B +
main.css 15,780 B), 558.7 KiB total JS across 48 chunks. PH3.4 recorded 172.7 KiB
/ 557.7 KiB. **No frontend source was modified in this sprint**; the ~0.1 KiB
difference is build-to-build noise.

---

## 22. Failures / Regressions

**No functional or security regression.** Full validation record in §27.

Two results were wrong before they were right. Both are recorded because the
method matters more than either number, and because reporting either as a defect
would have been wrong — the same discipline PH3.4 §3.3 applied.

### 22.1 A 4.4% "error rate" that was the risk engine working

The first baseline run reported a 4.37% 4xx rate: 31 of 41 `POST /api/paper/trade`
calls returning 400. That looks like a write-path failure under concurrency.

It was not. Each synthetic account starts with ₹100,000 of paper capital
(`services/paper_trade.py::DEFAULT_CAPITAL`) and the flow never sold anything, so a
5-lot order at ₹2,500 exhausted the balance after eight iterations and every
subsequent order was correctly rejected with *"Insufficient paper capital"*.

Before filing anything, the PH3.3 §10.1 rule was applied — *is the test wrong or
is the application wrong?* — and the answer was the test. Verified by hand: six
consecutive orders against a fresh account all returned 200. The flow now uses
quantity 1 and calls `POST /api/paper/reset` when it hits the limit, which is what
a real user does and which exercises one more real endpoint.

### 22.2 83 consecutive CSRF failures that were correct token rotation

The first authentication run showed `logout-all` failing **83 times out of 83**,
with `logout` (no CSRF required on that path for cookie clients in this flow)
passing. A 100% failure rate on one endpoint reads like a defect.

It was the harness. `POST /api/auth/refresh` calls `set_csrf_cookie` again
(`server.py`), minting a **fresh random double-submit value** bound to the same
session — so the token captured at login is stale the moment a refresh happens,
and `tokens_match(header, cookie)` correctly rejects it with a 403. Re-minting on
rotation is exactly what a double-submit implementation should do, and a browser
reads the cookie fresh on every request; the script did not. Verified by hand
against a live session (`{"message":"Signed out of all devices","sessions_revoked":21}`)
before anything was filed. The script now re-reads the cookie.

### 22.3 Two harness bugs in the instrumentation itself

Recorded briefly because both would have produced quietly wrong reports:

* The metrics probe read the `/api/metrics?format=json` **envelope** as the
  registry, found no series, and reported `requests=0` for a run that served 222.
  It now unwraps explicitly and **refuses to emit a delta** when the after-snapshot
  contains no series, rather than printing a clean zero.
* The saturation summary included `setup()`'s token-minting logins in its
  percentiles. At 40 rps those 200 bcrypt calls were ~10% of all samples and *were*
  the p95 — producing a suspiciously stable "p95 ≈ 240 ms" at 40, 60, 80 and 100
  rps that was simply bcrypt's latency showing through. Setup is now tagged and
  excluded.

---

## 23. Known Limitations

1. **Single uvicorn worker.** The most important untested dimension. Multi-worker and multi-instance scaling is extrapolated structurally in §20.4 and measured nowhere.
2. **Load driver shares a host with the system under test.** Figures near the ceiling are lower bounds (§2.2).
3. **Loopback datastores.** Real network RTT to MongoDB and Redis would add to every one of the ~4 queries per request. Multiply accordingly.
4. **The traffic mix is derived, not observed** (§5.1). Every capacity statement is conditional on it.
5. **Provider latency is a chosen input.** The market mock answers instantly and the AI mock in 900 ms by configuration. Real provider behaviour is PH3.4 §12's measurement, not this sprint's.
6. **250 synthetic users.** Enough to give every VU its own rate-limit budget; nowhere near enough to exercise the background-worker N+1s that scale with total user count (§21.1.4).
7. **Runs are minutes, not hours.** Adequate for throughput and latency; inadequate for slow leaks, fragmentation, or connection ratchets. That is PH3.6's question and this sprint's data (§18) is its starting point, not its answer.
8. **No frontend load.** No browser, no paint metrics, no Lighthouse.
9. **CPU sampled, not continuous** (§2.3).
10. **WebSocket event-delivery latency not measurable** without a server-side send timestamp (§14.2).
11. **`fetch_real_fii_dii` (nseindia.com) is the one provider call not redirected.** It is reached only via `ai_market_summary`, which runs on an 08:30 weekday cron; no test in this sprint touched it, confirmed by the loopback-only connection check (§26). It is a session-cookie scrape and would need its own handling if a future test exercises that path.

---

## 24. Reproducibility Instructions

Everything below is committed. A future engineer needs no artefact from this run.

### 24.1 Prerequisites

```bash
brew install k6            # v2.2.0 used here
# Docker (Redis), a local MongoDB, and backend/venv
```

### 24.2 Bring up the environment

```bash
cd <repo root>
scripts/load/load-test.sh up        # Redis container, both mocks, seeds 250 users
```

### 24.3 Start the backend against the load environment

```bash
set -a; . scripts/load/env/loadtest.env; set +a
cd backend && source venv/bin/activate
uvicorn server:app --host 127.0.0.1 --port 8000 --workers 1 --log-level warning
```

⚠ **`PYTHON_DOTENV_DISABLED=1` must be in the environment before uvicorn starts.**
It is set by `loadtest.env`; §3.1 explains what happens without it. The runner's
preflight authenticates a seeded account precisely to catch this.

### 24.4 Run

```bash
scripts/load/load-test.sh preflight                       # verify, run nothing
scripts/load/load-test.sh smoke                           # 5 VUs
scripts/load/load-test.sh baseline                        # 10
scripts/load/load-test.sh moderate                        # 25
scripts/load/load-test.sh high                            # 50
scripts/load/load-test.sh stress                          # 100
scripts/load/load-test.sh saturation                      # ramp to 400 rps
scripts/load/load-test.sh saturation -e SAT_RATE=200 -e SAT_DURATION=45s
scripts/load/load-test.sh auth -e AUTH_VUS=25
scripts/load/load-test.sh ratelimit
scripts/load/load-test.sh websocket -e WS_CONNECTIONS=150 -e WS_HOLD=75
scripts/load/load-test.sh websocket -e WS_CONNECTIONS=200 -e WS_CHURN=1   # reproduces L-2
scripts/load/load-test.sh failure                         # six-phase injection
scripts/load/load-test.sh down
```

The Redis experiment of §13.4 is `REDIS_MAX_CONNECTIONS=200` in the backend's
environment before starting uvicorn; nothing else changes.

### 24.5 Artefacts

Each run writes `scripts/load/results/<UTC timestamp>-<label>/`:

| File | Contents |
|---|---|
| `k6.log` | Full k6 output including the threshold verdicts |
| `k6-summary.json` | Every k6 metric, machine-readable |
| `before.json` / `after.json` | `/api/metrics`, `/api/diagnostics`, `/api/diagnostics/redis`, Mongo `serverStatus`, both mock control endpoints |
| `delta.json` | Computed deltas: rps, 5xx, RSS, FDs, Mongo opcounters, Redis commands/failures/circuit opens, provider call counts, per-route server-side latency |

### 24.6 Version pinning for exact reproduction

| Item | Version |
|---|---|
| Repository commit | `7907e14` + this sprint |
| k6 | v2.2.0 (go1.26.5, darwin/arm64) |
| Python | 3.11.15 |
| Redis image | `redis:7.2-alpine` (7.2.14) |
| MongoDB | local 7.x |
| Fixture seed | `20260814`, 250 users |
| Scenario version | `scripts/load/k6/` as committed with this document |

---

## 25. PH3.6 Handoff

**PH3.5 is complete. PH3.6 was not started.** No memory optimisation, no
monitoring work, no mock removal, no code fix for any finding in §19.

### 25.1 What PH3.6 inherits

* **A reusable load harness** — `scripts/load/`, one command per run shape, with a preflight that refuses to run against the wrong database and artefacts written automatically.
* **A resource baseline, and it is clean.** RSS oscillated 33–110 MB with no monotonic trend across >150,000 requests; FDs tracked connections and returned. **PH3.6 should start from "no leak is visible at these durations" rather than hunting one** — and should focus on what minutes cannot show.
* **A saturation profile** (§20) so PH3.6 knows which load level is worth soaking at: ~300 rps with an adequate Redis pool, ~100 rps without.
* **A fault-injection facility** (both mocks' `/__control`) for testing behaviour under sustained degradation, not just brief degradation.

### 25.2 Specifically for PH3.6 — memory / resource stability

1. **Run a soak.** Everything here is minutes. Hours at ~150 rps is the test that separates allocator noise from a leak. The harness supports it: `-e SAT_RATE=150 -e SAT_DURATION=3600s`.
2. **Soak the WebSocket tier separately.** 150 held connections were flat over 75 s, but `ConnectionManager` holds three per-socket dictionaries and `_reap` is the only path that cleans them. Whether a socket that dies without a clean close is ever reaped is a question only a long run with induced disconnects answers — and L-2 shows that path is already fragile under churn.
3. **Watch the in-process cache fallback.** When the Redis circuit opens, `services/cache.py` degrades to a dict bounded at `_MEMORY_MAX_KEYS = 1024`. At the shipped pool size that happened **129 times in one 3-minute run** (§13.1). The bound means it cannot leak — but a soak should confirm the eviction path actually runs rather than trusting the constant.
4. **The `sessions` and `rate_limits` collections grow with every request.** Both rely on TTL indexes. A soak is the only way to see whether reaping keeps up with write rate.
5. **L-1 changes what to soak.** If the Redis pool is resized first, PH3.6 soaks a system at 300 rps; if not, it soaks one that spends part of its life on the in-process fallback. Establish which before starting.

### 25.3 Routed elsewhere, not to PH3.6

| Finding | Owner |
|---|---|
| **L-1** Redis pool sizing / pool semantics | **PH3.7** (config + deployment defaults) |
| **L-2** broadcast set mutation | Next real-time-touching sprint — one line, worth doing early |
| **L-3** bcrypt on the event loop | Next auth-touching sprint |
| **L-4** multi-worker scaling | PH3.7 / deployment |
| **L-5** rate-limiter round trips (PH3.4 O-7) | Next security-touching sprint (owner unchanged) |
| **L-6** no Redis rate-limit store | Next security-touching sprint |
| **O-1** 429s unattributable to a route | **PH3.7** |
| **O-2** no CPU gauge in `/api/metrics` | **PH3.7** |
| **S-1** XFF trusted without a proxy check | Next security-touching sprint |
| **S-2** unauthenticated WebSocket `user_id` | **PH1.9** (already tracked) |
| Frontend paint metrics, CI bundle budget | PH3.7 (unchanged from PH3.4 §21.8) |
| PH3.3's D-4 (refund stub) and D-10 (email validation) | PH3.9 / next auth sprint — untouched, still `xfail` |

---

## 26. External Provider Protection

The brief forbids sending load to third parties (§14, §15). Two mechanisms, and
one verification that does not rely on either being correct.

**Market data.** `services/real_market.py::yahoo_origin()` reads
`MARKET_DATA_YAHOO_BASE` at call time and, when unset, returns
`https://<host>.finance.yahoo.com` — byte-identical to the pre-existing URLs. All
seven Yahoo call sites across `real_market.py` and `stock_details.py` route
through it. A monkeypatch from the harness was rejected as the alternative because
it would exercise a code path that does not exist in production, making the
measurement non-transferable.

**AI.** No application change: the `anthropic` SDK reads `ANTHROPIC_BASE_URL`
itself (verified against anthropic 0.116.0). The load environment sets it
alongside an obviously-synthetic key.

**Verification, independent of both.** During a run, every established outbound
TCP connection from the backend process was enumerated:

```
28 → 127.0.0.1:27017   (MongoDB)
25 → 127.0.0.1:6379    (Redis: 24 pooled + 1 Pub/Sub)
10 → 127.0.0.1:9020    (market mock)
 1 → 127.0.0.1:9030    (AI mock)
```

**Zero non-loopback connections.** The backend log contains zero occurrences of
`nseindia`, `finance.yahoo` or `api.anthropic`. The one un-redirected provider
call (§23.11) is on an 08:30 weekday cron that never fired during these runs.

Twelve regression tests (`backend/tests/test_load_harness.py`) pin both halves of
the contract: that the override is **inert by default** — including that an empty
or whitespace-only value is treated as unset, which would otherwise build a
request to the application's own host and 404 quietly into the existing broad
`except` — and that it **actually takes effect** when set, since a working
provider and a working mock produce the same green result.

---

## 27. Validation Record

Every command executed on 2026-08-14.

| Command | Result |
|---|---|
| `pytest` (backend default) | **2,188 passed**, 6 xfailed, 95 deselected, 168 s |
| `pytest -m security` (PH1) | **452 passed**, 1,837 deselected, 32 s — **unchanged** |
| `pytest tests/test_perf_regression.py` (PH3.4) | **32 passed** |
| `pytest tests/test_load_harness.py` (new) | **12 passed** |
| `yarn test:ci` (frontend, PH3.2) | **319 passed / 18 suites**, 19 s |
| `yarn build` (production) | **green** (`DISABLE_ESLINT_PLUGIN=true` — pre-existing PH3.2 defect) |
| Bundle after build | 172.8 KiB gzip initial — unchanged within noise |
| Backend import, production-shaped env | **green** — 204 routes, `ensure_indexes` present, **provider override inert** |
| `load-test.sh smoke / baseline / moderate / high / stress` | all thresholds met, 0 5xx |
| `load-test.sh saturation` (7 rates × 2 pool configs) | ceiling established |
| `load-test.sh auth` (5 / 10 / 25 VUs) | 0 login failures |
| `load-test.sh ratelimit` | **201/201 checks**, 0 5xx |
| `load-test.sh websocket` (50 / 150 hold, 200 churn) | 0 errors; L-2 reproduced |
| `load-test.sh failure` (6 phases) | 0 5xx, 0 timeouts in every phase |

**Baselines re-measured rather than quoted:** backend 2,176 and frontend 319 were
both re-run at the start of this sprint.

### 27.1 Re-validation, 2026-08-15

The suite-level checks were re-run the following day, when the `.claude`
documentation set was updated to match this certification. Nothing in the
application or the harness changed between the two runs.

| Command | Result |
|---|---|
| `pytest` (backend default) | **2,188 passed**, 6 xfailed, 95 deselected, 187 s — unchanged |
| `pytest -m security` (PH1) | **452 passed**, 1,837 deselected, 32 s — unchanged |
| `pytest tests/test_load_harness.py tests/test_perf_regression.py` | **44 passed** (12 + 32) |
| `yarn test:ci` (frontend) | **319 passed / 18 suites**, 20 s — unchanged |
| `DISABLE_ESLINT_PLUGIN=true yarn build` | **green** |
| Backend import, production-shaped env | **green** — override inert, `ensure_indexes` present |

Two figures came back different from the table above, and both are corrected here
rather than left to stand:

* **Route count is 205, not 204.** Measured as `len(server.app.routes)`: 200
  `APIRoute` + 1 `WebSocketRoute` + 4 framework routes (`/openapi.json`, `/docs`,
  `/docs/oauth2-redirect`, `/redoc`). 205 is also what PH3.4 recorded. The 204 above
  was an off-by-one in transcription, not a route that disappeared.
* **Bundle is 173.3 KiB gzip initial** — `main.5f4e9719.js` 161,596 B +
  `main.3d282de1.css` 15,897 B — against the 172.8 KiB recorded in §21.2, a **+569 B
  (+0.3%)** difference. **The frontend tree is git-clean and unchanged**, and two
  consecutive rebuilds produced byte-identical output, so the build is deterministic
  on this host and the difference originates outside the tracked sources — most
  plausibly a `node_modules` resolution difference between the two measurements.
  It is recorded as an open discrepancy rather than absorbed into "noise": 569 B is
  small, but "no frontend source changed" and "the bundle changed" cannot both be
  fully explained yet, and a bundle budget (owed to PH3.7) will need that explained
  before it can be set.

### 27.2 Security regression

**No security control was modified, weakened, bypassed, or disabled to obtain any
number in this document.**

* Rate limiting stayed **on** for every run — including the saturation search, where turning it off would have raised the ceiling. §9.2 explains every 429 observed rather than eliminating them.
* No `X-Forwarded-For` spoofing was used to widen the anonymous budget, even though it would have worked (§5.3). It is reported as finding S-1 instead.
* CSRF stayed enforced; the cookie-authenticated flows send a freshly-read token, and the one endpoint that appeared to fail was the harness's error (§22.2).
* Authentication used valid credentials only. No brute-force pattern was generated and no account was locked out at any point.
* bcrypt cost 12 was **not** lowered, and login was deliberately given the loosest threshold rather than the cost factor being treated as tunable (§9).
* PH1's 452 security tests are unchanged and green.
* No production credential, database, broker, payment account, or provider key was used. The one accidental exposure — a first boot that inherited `backend/.env` — was found, closed with `PYTHON_DOTENV_DISABLED=1`, and the stray rows it wrote to the development database were removed and verified gone.

---

## 28. Files Changed

**Application code** — one change, and it is inert by default.

| File | Change |
|---|---|
| `backend/services/real_market.py` | **New** `yahoo_origin()` / `yahoo_origin_overridden()`; 4 Yahoo call sites route through it. Default output byte-identical to before. |
| `backend/services/stock_details.py` | 3 Yahoo call sites + the `fc.yahoo.com` cookie bootstrap route through `yahoo_origin()` |

**Tests**

| File | Change |
|---|---|
| `backend/tests/test_load_harness.py` | **New.** 12 tests pinning the override's default-inertness and its effect |

**Load-test harness** (new, non-application)

| File | Purpose |
|---|---|
| `scripts/load/load-test.sh` | Runner: environment lifecycle, preflight, all run shapes, metric capture |
| `scripts/load/env/loadtest.env` | The load environment, committed for reproducibility |
| `scripts/load/mocks/market_provider.py` | Yahoo-shaped mock with latency/error/timeout injection (stdlib only) |
| `scripts/load/mocks/ai_provider.py` | Anthropic-shaped mock with latency/error/429/timeout injection (stdlib only) |
| `scripts/load/k6/lib/config.js` | Target, fixtures, custom metrics, thresholds |
| `scripts/load/k6/lib/flows.js` | Scenarios A–E as user flows |
| `scripts/load/k6/scenarios.js` | Mixed traffic at five stages |
| `scripts/load/k6/saturation.js` | Arrival-rate ceiling search (ramp + flat modes) |
| `scripts/load/k6/auth.js` | Authentication throughput |
| `scripts/load/k6/ratelimit.js` | Rate-limit boundary, rejection shape, bystander isolation |
| `scripts/load/k6/websocket.js` | Real-time load, hold and churn modes |
| `scripts/load/.gitignore` | Excludes `results/`, `.run/`, generated `fixtures.json` |
| `.gitignore` (root) | **Correction, 2026-08-15.** The repository-wide `*.env` rule silently swallowed `scripts/load/env/loadtest.env` — so `git add scripts/load` skipped it and the harness would have arrived **unrunnable** for anyone cloning, with §24's reproducibility instructions pointing at a file that was never committed. One negation rule, next to the existing `!.env.example` exceptions and carrying the same justification: every value in that file is synthetic and self-labelling (`loadtest-mock-key-not-a-real-credential`), it is never sourced by the application, and reproducibility is the entire reason it exists. |
| `backend/scripts/seed_load_fixtures.py` | Synthetic fixtures + manifest, with three refusal guards |
| `backend/scripts/load_metrics_probe.py` | Server-side snapshot / delta / summary |

**No API contract, response shape, trading logic, AI decision logic, prompt, model
selection, index, or security control was changed. No frontend source was
modified.**

---

## 29. Success Criteria

| Criterion | Status |
|---|---|
| Dedicated non-production load environment identified | ✅ §2, §3 |
| Load-testing framework selected | ✅ §4 — k6 v2.2.0, one framework |
| Realistic traffic model established | ✅ §5, with the derivation stated as an assumption |
| Smoke load test created | ✅ §7, §10 |
| Baseline load test created | ✅ §7, §10 |
| Higher-load test created | ✅ moderate / high / stress / saturation |
| API throughput measured | ✅ §10, §20 |
| p50 / p95 / p99 measured | ✅ §10, client- and server-side |
| Error rate / 5xx measured | ✅ §10 — 0.00% at every level |
| Rate-limiting behaviour validated | ✅ §9 — 201/201 checks, bystander isolation proven |
| MongoDB behaviour measured | ✅ §12 — 0 queue depth throughout |
| Redis behaviour measured | ✅ §13 — the system's first bottleneck, with a controlled experiment |
| WebSocket behaviour measured | ✅ §14 — and it surfaced L-2 |
| AI behaviour tested using mocks | ✅ §15 |
| Trading tested using paper/simulation | ✅ §16 — no real order placed |
| External providers protected | ✅ §26 — verified structurally, zero non-loopback connections |
| Resource utilisation recorded | ✅ §18 — RSS, FDs, CPU, connections |
| Bottlenecks classified | ✅ §19 — P1/P2/P3 with owners |
| Capacity findings documented | ✅ §20, with an explicit statement of what it does not claim |
| PH3.4 baseline comparison completed | ✅ §21 — six claims held, one corrected |
| Tests are repeatable | ✅ §24 — committed environment, fixed seed, pinned versions |
| Heavy load tests not forced into PR CI | ✅ §30 |
| Security remains enabled | ✅ §27.2 |
| Existing tests remain green | ✅ §27 |
| Certification document created | ✅ this document |
| `.claude` documentation updated | ✅ 2026-08-15 — `TESTING.md` (load / stress / Redis sections rewritten as-built), `CHANGELOG.md` (sprint entry), `PRODUCTION_ROADMAP.md` + `TASK.md` (**PH3.7 now COMPLETE — both halves**), `docs/performance/README.md` |
| PH3.6 handoff documented | ✅ §25 |

---

## 30. CI Decision

**Load testing stays an explicit workflow. No load test was added to PR CI, and
that is a decision rather than an omission.**

The reasoning: every run in this document takes 1.5–5 minutes and needs Redis, a
seeded MongoDB, two mock processes and a warmed backend. On a shared CI runner
the numbers would be dominated by whatever else the runner is doing — the same
argument PH3.4 made for why its 38 regression tests assert counts rather than
durations. A latency threshold in PR CI would go red when the runner is busy and
green on a fast runner that had just regressed, which is precisely how a check
gets marked `continue-on-error` and stops meaning anything.

What *is* appropriate, and is handed to PH3.7 with the monitoring work:

* **A scheduled (nightly or weekly) `baseline` run** against a persistent staging deployment, with results retained so a trend is visible. Trends survive noisy runners; single thresholds do not.
* **A manual `workflow_dispatch`** for the heavier stages, run deliberately before a release.
* **`scripts/load/load-test.sh smoke` as an optional pre-release gate**, not a PR gate — it is 40 seconds and would catch a stack that cannot serve traffic at all.

The load-relevant properties that *can* be asserted deterministically on every PR
already are: PH3.4's `tests/test_perf_regression.py` (32 tests — query counts,
index coverage, payload bounds, gather structure) and this sprint's
`tests/test_load_harness.py` (12 tests). Both run in well under a second and
neither measures wall-clock time.

---

**PH3.6 was not started. Stopped here, per the brief's §29.**

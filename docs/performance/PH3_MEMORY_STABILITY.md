# PH3.6 — Memory & Resource Stability

**Sprint:** PH3.6 — Memory & Resource Stability
**Phase:** PH3 — Production Hardening & Verification
**Date:** 2026-08-15
**Status:** **PASS WITH CONDITIONS** (see §20)

> **Numbering note.** This sprint was commissioned as "PH3.6 — Memory &
> Resource Stability". `.claude/PRODUCTION_ROADMAP.md`'s own PH3.6 is *Backend
> Decomposition (server.py → Routers)*, which is **untouched and still
> NOT_STARTED**. The same brief-label-vs-tracker drift is recorded for PH3.4 and
> PH3.5 in `.claude/TASK.md`; read "PH3.6" in `docs/performance/` as this
> document. The predecessor that handed this work over is
> `PH3.5_LOAD_TEST_CERTIFICATION.md` §25.

---

## 1. Scope

PH3.5 measured the system for **minutes** and found no leak. It said so
explicitly and handed forward the reason that was not an answer:

> *"Runs are minutes, not hours. Adequate for throughput and latency;
> inadequate for slow leaks, fragmentation, or connection ratchets. That is
> PH3.6's question and this sprint's data is its starting point, not its
> answer."* — PH3.5 §21.7

**In scope:** memory retention, resource lifecycle, connection management,
background-task lifecycle, cache growth, listener registration, reconnect and
failure behaviour, and long-running process stability, across backend and
frontend.

**Explicitly out of scope and not touched:** trading strategy logic, AI decision
logic, prompts, model selection, API contracts, the design system, PH3.7, and
the roadmap's own PH3.6 (backend decomposition).

### 1.1 The premise this sprint had to reject

PH3.5's handoff advised starting from *"no leak is visible at these durations"*
rather than hunting one. That advice was correct about its own data and wrong as
a conclusion, and the reason is the single most important sentence in this
document:

**RSS is the wrong instrument for the leaks this application actually has.**

Both confirmed leaks below are Python dicts that gain one entry per event and
never lose it. An entry costs a few hundred bytes. Ten thousand of them — a
meaningful fraction of a day's traffic — weigh less than the noise between two
idle RSS samples on an idle process. PH3.5's RSS series was accurate, honest,
and structurally incapable of showing either defect. **A leak is a shape, not a
size: a count that only ever rises.** This sprint therefore counted entries
rather than bytes, and found in the first hour what 150,000 requests of
throughput testing could not.

---

## 2. Architecture Reviewed

Read at HEAD, not from prior sprint reports.

| Area | Files |
|---|---|
| Application lifecycle | `backend/server.py` (`startup`/`shutdown`, 6,100+ lines) |
| WebSocket tier | `server.ConnectionManager`, `/api/ws`, `services/realtime/event_bridge.py` |
| Background loops | `market_broadcast_loop`, `ai_monitoring_loop`, `services/heartbeat_engine.py` (2 loops) |
| Scheduled jobs | `services/scheduler.py` (APScheduler, 6 cron jobs) |
| Event bus | `services/market_engine/event_bus.py` |
| Caches | `services/cache.py`, `services/ai_context_builder.py`, `services/market_engine/scanner_worker.py`, `services/news_service.py` |
| Per-user state | `services/portfolio_stream.py`, `services/trade_stream.py`, `services/broker_engine.py` |
| Redis | `infrastructure/redis_client.py`, `infrastructure/redis_pubsub.py` |
| MongoDB | `server.py` client construction, `ensure_indexes()` |
| Outbound HTTP | `services/http_client.py` |
| Broker streams | `services/brokers/stream.py`, `services/broker_engine.py` |
| Observability | `observability/metrics.py`, `observability/log_streams.py`, `observability/middleware.py` |
| Frontend realtime | `context/RealtimeProvider.jsx`, `store/realtimeStore.js`, `hooks/useWebSocket.js` |
| Frontend lifecycle | 166 JS/JSX files swept for timers, listeners, observers, animations |

---

## 3. Resource Inventory

Every long-lived resource the process holds, with its owner and its cleanup path
**as found**.

| Resource | Owner | Created | Cleanup path (before PH3.6) |
|---|---|---|---|
| MongoDB client | `server.client` | import time | `client.close()` in `shutdown` ✅ |
| Mongo connection pool | pymongo | on demand | **never reaped when idle** ❌ (§5) |
| Redis pooled client | `infrastructure/redis_client` | lazily | `close()` in `shutdown` ✅ |
| Redis Pub/Sub subscriber | `infrastructure/redis_pubsub` | startup | `stop_all()` in `shutdown` ✅ |
| Redis stats sampler task | `redis_client` | startup | `stop_stats_sampler()` via `close()` ✅ |
| Outbound HTTP pools | `services/http_client` | startup | `close_pool()` in `shutdown` ✅ |
| APScheduler | `services/scheduler` | startup | `scheduler.shutdown()` ✅ |
| Broker WebSocket streams | `BrokerStreamManager` | per account | `stop_all()` in `shutdown` ✅ / **leaked on token expiry** ❌ |
| `market_broadcast_loop` | bare `create_task` | startup | **none** ❌ |
| `ai_monitoring_loop` | bare `create_task` | startup | **none** ❌ |
| Heartbeat loop | bare `create_task` | startup | **none** ❌ |
| Price-stream loop | bare `create_task` | startup | **none** ❌ |
| Client WebSockets | `ConnectionManager.active` | per connection | `disconnect` / `_reap` ✅ |
| Per-socket channel sets | `ConnectionManager.channels` | per connection | `disconnect` / `_reap` ✅ |
| **Per-user socket map** | `ConnectionManager.user_connections` | per connection | **key never removed** ❌ |
| **AI chat-context cache** | `ai_context_builder._cache` | per chat message | **never evicted** ❌ |
| Market/news cache fallback | `services/cache._memory` | per miss | bounded 1024 + sweep ✅ |
| Portfolio emit throttle | `portfolio_stream._last_emit` | per user | **never pruned** ❌ |
| Trade emit throttle | `trade_stream._last_emit` | per user | **never pruned** ❌ |
| Scanner novelty state | `scanner_worker._recent_hits` | per hit | pruned by cooldown ✅ |
| Breaking-news dedupe | `news_service._recent_breaking` | per headline | pruned by cooldown ✅ |
| Event-bus handlers | `EventBus._handlers` | startup | registered unconditionally ⚠️ |
| Event-bus log | `EventBus._event_log` | per publish | bounded 500 ✅ |
| Metric series | `observability/metrics` | per label set | `MAX_SERIES_PER_METRIC` + overflow ✅ |
| Log queue | `observability/log_streams` | per record | bounded queue + drop counter ✅ |
| Thread pools | `real_market` (5), `news_service` (3) | import time | module-level, fixed ✅ |
| Broker sessions | `broker_engine._sessions` | per account | popped on disconnect/expiry ✅ |

---

## 4. Memory Leak Audit

Patterns searched for, and what each search found.

| Pattern | Result |
|---|---|
| Global mutable collections | 24 module-level containers enumerated; 5 unbounded (see §16) |
| Dicts growing indefinitely | **2 confirmed** (`user_connections`, `ai_context._cache`) + 2 slow (`_last_emit` ×2) |
| Caches without TTL | **1 confirmed** — `ai_context_builder._cache` checked TTL on read only |
| Unbounded queues | none — log queue bounded, event log bounded, no work queues |
| `create_task` without lifecycle | **4 loops**, all bare, none cancellable |
| Handlers registered repeatedly | `start_event_bridge` unconditional (1 caller, latent) |
| WebSocket objects retained | sockets released correctly; **user keys were not** |
| Mongo cursors | every `.find()` uses an explicit `to_list(N)`; no `to_list(None)`; `async for` cursors fully consumed |
| Redis Pub/Sub subscriptions | deduped by channel, explicit `stop()`, clean unsubscribe ✅ |
| HTTP clients per call | fixed in PH3.4; pools keyed by (loop, timeout), bounded ✅ |
| File handles | log handlers behind `QueueListener`, rotated; no ad-hoc file I/O on request paths |
| Logging buffers | `MAX_QUEUE_SIZE` bounded with a drop counter ✅ |
| Metric label cardinality | route **templates**, never raw paths; `MAX_SERIES_PER_METRIC` ceiling with an overflow series ✅ |
| In-memory session state | sessions/rate limits/audit live in Mongo with TTL indexes, not in process ✅ |
| Per-user state without expiry | **4 found** (`user_connections`, `ai_context._cache`, 2× `_last_emit`) |

---

## 5. Database Resource Audit

**As found:** `AsyncIOMotorClient(mongo_url)` — every pool bound and every
timeout left at the driver's default, none of them written down anywhere.
PH2.8's *"connection-pool sizing documented"* was displaced to PH2.8b and never
executed, so the deployed configuration existed only as library defaults nobody
had read.

| Setting | pymongo default | Verdict |
|---|---|---|
| `maxPoolSize` | 100 | **Adequate.** PH3.5 measured 18 connections at steady state, 28 at 400 rps. Not the problem. |
| `minPoolSize` | 0 | Fine. |
| `serverSelectionTimeoutMS` | 30,000 | Bounded. Lowering it is a deployment decision, not a leak fix. |
| `connectTimeoutMS` | 20,000 | Bounded. |
| `maxIdleTimeMS` | **None** | **DEFECT.** A pooled connection is never closed for being idle: a spike that opens 60 connections leaves 60 open until the process restarts. The pool is a ratchet. |
| `socketTimeoutMS` | **None** | **RISK, NOT FIXED.** No read timeout at all — a query against a wedged primary blocks its request forever, holding a connection and a worker slot. Deliberately left unset; see §18. |

**Changed:** `maxIdleTimeMS=60000`, and every other value made explicit and
env-overridable at its existing default. One behavioural change, and it is pure
resource reclamation.

**Not changed:** `socketTimeoutMS`. Choosing a value requires knowing the
slowest legitimate query on production hardware; a number picked from a laptop
would start aborting real work under load. Wired to `MONGO_SOCKET_TIMEOUT_MS`
so staging can baseline it, and carried as an open risk.

Other checks: one client for the whole process (scripts construct their own,
correctly); no per-request client construction anywhere; `client.close()` on
shutdown; background jobs share the one client.

---

## 6. Redis Audit

Reviewed `infrastructure/redis_client.py` (763 lines) and
`infrastructure/redis_pubsub.py` (483 lines) in full. **No defect found.** This
is the best-managed resource in the backend and is recorded here as the
reference the rest should look like:

* one client construction site process-wide (`_build_client`);
* pooled, with a circuit breaker that **re-tests and closes** rather than a
  one-way latch;
* Pub/Sub on a **dedicated** connection, so a subscription cannot consume a pool
  slot for the process's life;
* reconnect with exponential backoff **and jitter**, the ladder reset on a
  successful subscribe;
* **exactly one subscriber per channel**, enforced by a registry;
* teardown individually guarded per call, so a failing teardown cannot kill the
  reconnect loop;
* shutdown ordered subscribers-then-pool, and the ordering is explained in the
  code.

`REDIS_MAX_CONNECTIONS` defaulting to 24 (PH3.5's L-1) is **not** re-litigated
here — it is a sizing/deployment question owned by PH3.7. Per PH3.5 §25.2 item
5, the soak in §14 was run at `REDIS_MAX_CONNECTIONS=200` so it measured a
system on Redis rather than one spending part of its life on the in-process
fallback. That is recorded as a condition of the result, not as a fix.

Cache TTLs: every `cache_set` call site passes an explicit TTL; the in-memory
fallback is bounded at 1,024 keys with sweep-then-evict. Unbounded growth is not
reachable.

---

## 7. WebSocket Audit

The most important section, because the platform is real-time and because both
of this tier's defects were remotely triggerable.

**Lifecycle as found:** `connect` adds to three maps; `disconnect` removes from
two of them; `_reap` removes from two of them.

**Finding M-1 — the third map never shrank.** `disconnect` did
`self.user_connections[user_id].discard(ws)` and stopped. The empty `set()` and
its key stayed. `_reap` had the same gap. The key is
`websocket.query_params.get("user_id", "anonymous")` — **nothing authenticates
it** (S-2, tracked to PH1.9) — so an anonymous caller can mint a fresh key on
every connection, and a legitimate reconnect storm does it accidentally.

Measured before the fix: 1,000 clean connect/disconnect cycles left **1,000**
empty sets; 500 sockets reaped after dying without a clean close left **500**.
Neither returned. Growth per key is small; growth per key is also unbounded, and
the only thing that ever emptied the map was a process restart.

**Finding M-3 — `broadcast` iterated the live set** (PH3.5's L-2, routed here).
`await ws.send_text(...)` suspends; any socket disconnecting during that suspend
mutates `self.active`. Reproduced: `RuntimeError: Set changed size during
iteration`. The exception is the *lucky* outcome — it is loud. The unlucky one
is what it implies: every socket after the mutation point silently misses the
message, and the event-bus publish after the call never happens.
`broadcast_to_channel` already snapshotted; `broadcast` and `send_to_user` did
not.

**Reconnect behaviour:** no duplicate listeners are possible on the backend —
subscriptions are per-socket sets keyed by the socket object, so a reconnect
produces a new socket with a fresh set and the old one is dropped. Verified by
churn (§14.2).

---

## 8. Background Task Audit

Every task-creating call site in the backend:

| Site | Kind | Lifecycle as found |
|---|---|---|
| `server.startup` → `market_broadcast_loop` | perpetual | bare `create_task`, **no reference, no cancellation** ❌ |
| `server.startup` → `ai_monitoring_loop` | perpetual | bare `create_task`, **no reference, no cancellation** ❌ |
| `heartbeat_engine.start_engine` → `_heartbeat_loop` | perpetual | bare `create_task`, **no reference, no cancellation** ❌ |
| `heartbeat_engine.start_engine` → `_price_stream_loop` | perpetual | bare `create_task`, **no reference, no cancellation** ❌ |
| `redis_client.start_stats_sampler` | perpetual | reference held, `stop_stats_sampler()` ✅ |
| `redis_pubsub.PubSubSubscriber.start` | perpetual | reference held, `stop()` with bounded wait ✅ |
| `brokers/stream.BrokerStream.start` | per account | reference held, `stop()` ✅ |
| `scheduler.trade_monitor_job` → `generate_close_intelligence` | one-shot | fire-and-forget, bounded by closed-trade count ⚠️ |
| `activity_logger.log_activity` → callback | one-shot | fire-and-forget, bounded by log rate ⚠️ |
| `redis_client._schedule_pool_reset` → `_drop` | one-shot | fire-and-forget, bounded by breaker opens ⚠️ |
| `BackgroundTasks.add_task` (5 routes) | per request | FastAPI-owned, runs after response ✅ |

**Finding M-4.** The four perpetual loops carried two distinct defects:

1. **No reference.** `asyncio` holds only a *weak* reference to a running task;
   the documentation says to keep one. A loop collected mid-execution leaves no
   exception and no log line — the dashboard just goes quiet.
2. **No shutdown path, which is the expensive one.** `shutdown()` closed the
   scheduler, broker streams, Redis, the HTTP pool and finally the Mongo client
   while all four loops kept running against them. Both heartbeat loops read
   Mongo (`_collect_prices` calls `distinct` on `watchlist` and `trades`), so a
   loop waking after `client.close()` does I/O against a closed client. Every
   clean stop emitted a burst of connection errors indistinguishable in the logs
   from a crash.

No task-per-request creation was found; no task survives logout or WebSocket
disconnect.

---

## 9. Cache Audit

| Cache | Storage | Key | TTL | Max size | Eviction | Verdict |
|---|---|---|---|---|---|---|
| Market/news/quotes | Redis, else `_memory` | namespaced string | explicit per call (30–60 s) | 1,024 (fallback only) | sweep expired, then oldest | ✅ bounded |
| **AI chat context** | process dict | **user id** | 8 s, **read-side only** | **none** | **none** | ❌ **M-2** |
| Scanner novelty | process dict | (kind, symbol) | 30 min | universe-bounded | prune on each call | ✅ |
| Breaking news | process dict | headline[:80] | 120 min | headline-bounded | prune on each call | ✅ |
| Portfolio emit throttle | process dict | **user id** | 3 s semantic | **none** | **none** | ❌ **M-5** |
| Trade emit throttle | process dict | **user id** | 3 s semantic | **none** | **none** | ❌ **M-5** |
| Event-bus log | process list | — | — | 500 | slice on publish | ✅ |
| Activity feed | `deque` | — | — | `maxlen=50` | automatic | ✅ |
| Broker sessions | process dict | (user, broker) | broker session | user-bounded | popped on disconnect | ✅ |
| Yahoo crumb session | process dict | — | timestamped | 1 entry | overwritten | ✅ |
| Metric series | process dict | label tuple | — | `MAX_SERIES_PER_METRIC` | overflow series | ✅ |
| Frontend price ticks | Zustand | symbol | — | universe-bounded | — | ✅ |
| **Frontend live trades** | Zustand | trade id | — | **none** | **none** | ❌ **F-1** |
| **Frontend trade reviews** | Zustand | trade id | — | **none** | **none** | ❌ **F-2** |
| **Frontend AI runs** | Zustand | run id | — | soft 6, **defeatable** | never evicts `active` | ❌ **F-3** |
| Frontend alerts/orders/events | Zustand | — | — | 20–50 | slice | ✅ |

**Finding M-2 is the largest by volume.** `ai_context_builder._cache` maps a
user id to a `ChatContext` — the rendered markdown block of live market,
portfolio and news text, **plus** the structured `sections` it was rendered
from, several KB per entry. The 8-second TTL was consulted on read and enforced
nowhere. Measured: 5,000 users, every entry 999 seconds stale, **5,000 live
entries retained**. Nothing could ever read any of them again.

---

## 10. Frontend Lifecycle Audit

Swept all 166 JS/JSX source files.

**Clean, and verified individually:**

* **Timers.** 13 `setInterval` sites and every `setTimeout` chain — all in a
  `useEffect` with a matching `clearInterval`/`clearTimeout` in the cleanup.
* **DOM listeners.** 6 `addEventListener` sites (keydown, resize ×2, mousedown
  ×2, storage) — all with a matching `removeEventListener`.
* **Observers.** The one `ResizeObserver` (`TradingChart`) calls `disconnect()`
  and `chart.remove()` on unmount.
* **GSAP.** `Landing.jsx` scopes its ScrollTriggers in a `gsap.context` and
  `ctx.revert()`s them; `AnimatedNumber` calls `tween.kill()`.
* **The socket.** `RealtimeProvider` owns exactly one, keyed on `userId`, with
  every timer cleared and the socket closed in cleanup; handlers are assigned as
  `onopen`/`onmessage`/`onclose`/`onerror` properties rather than added as
  listeners, so a reconnect **cannot** accumulate handlers. Backoff is
  exponential with jitter and resets on a clean open; a `closedByUs` flag stops
  the reconnect loop on unmount.
* **Bounded event lists.** `tradeUpdates` (50), `engineEvents` (20), `alerts`
  (50), `marketAlerts` (50), `brokerOrders` (50), `scanner` (50), `news` (50).

**Three maps were not bounded** — F-1, F-2, F-3 in §16. All three grow only
within a single session, which for this product means *a trading day with the tab
open*, which is exactly the duration that matters.

**Reported, not fixed:** `usePriceFlash` and `useCardEntrance` do not kill their
tweens on unmount, so GSAP holds a detached element for up to 600 ms. That is a
transient, not a leak, and changing it is speculative optimisation.

---

## 11. Profiling Method

Three instruments, answering three different questions. Mixing them up is the
main way this kind of work goes wrong.

| Instrument | Question it answers | Where |
|---|---|---|
| `backend/scripts/resource_probe.py` | Do the in-process structures return to baseline? | new, this sprint |
| `backend/tests/test_resource_lifecycle.py` | Does each fix hold, mechanically, forever? | new, this sprint (28 tests) |
| `scripts/load/soak.sh` | Do they stay flat over **tens of minutes under real load**? | new, this sprint |

`resource_probe.py` drives the application's own objects in-process (FakeDB, no
network) through seven scenarios — clean disconnect cycles, dirty disconnects,
broadcast-under-churn, chat-context writes, throttle stamps, bus publishes, task
spawn/cancel — and reports every tracked count at eight checkpoints plus a settle
phase. It exits non-zero if any lifecycle structure fails to return to baseline
or any cache exceeds its own ceiling, so it is a check and not only a report.

`soak.sh` samples `/api/metrics` every 30 s **for the whole run** and writes one
CSV row per sample. This is the difference from PH3.5's harness, which snapshots
before and after: a before/after pair cannot distinguish "grew and came back"
from "never grew", and the series is the only form in which a ratchet is visible.
It deliberately uses `curl` + `awk` rather than PH3.5's Python probe, because
that probe opens its own Mongo and Redis clients and a sampler that adds a
connection every 30 seconds would contribute to the number it is measuring.

**What was NOT used, and why.** `tracemalloc` was not enabled in the soak: it
allocates per-frame bookkeeping proportional to allocation rate, which perturbs
exactly the measurement it is taken for. It was not needed — the leaking objects
were located by reading the code and confirmed by counting entries, which is
cheaper, exact, and does not disturb the process.

---

## 12. Baseline Measurements

Hermetic, in-process (`resource_probe.py`), 5,000 cycles per scenario. This is
application code with no database, network or Redis in the path.

```
phase                           act   chan  users  aictx  cache   pthr   tthr   subs  btask  atask  bstrm  rssMB    fds    thr
------------------------------------------------------------------------------------------------------------------------------
T0 baseline                       0      0      0      0      0      0      0      0      0      1      0   74.7      6      1
T1 ws connect/disconnect          0      0      0      0      0      0      0      0      0      1      0   74.7      6      1
T2 ws dirty disconnects           0      0      0      0      0      0      0      0      0      1      0   74.7      6      1
T3 broadcast under churn          0      0      0      0      0      0      0      0      0      1      0   74.7      6      1
T4 ai chat context                0      0      0    512      0      0      0      0      0      1      0   74.8      6      1
T5 portfolio/trade throttle       0      0      0    512      0   4096   4096      0      0      1      0   74.5      6      1
T6 event bus publish              0      0      0    512      0   4096   4096      0      0      1      0   73.5      6      1
T7 task spawn/cancel              0      0      0    512      0   4096   4096      0      0      1      0   73.5      6      1
T8 settled                        0      0      0    512      0   4096   4096      0      0      1      0   73.5      6      1

LIFECYCLE — must return to baseline once activity stops
  OK   all 6 structures returned to baseline after 5000 cycles

CACHES — must stay under their own ceiling (peak across all phases)
  OK   ai_context_entries: peak 512 / ceiling 512
  OK   cache_memory_keys: peak 0 / ceiling 1024
  OK   portfolio_throttle_users: peak 4096 / ceiling 4096
  OK   trade_throttle_users: peak 4096 / ceiling 4096
  OK   event_bus_log: peak 500 / ceiling 500
```

**The caches sitting exactly at their ceilings is the result, not a warning.**
Each was driven with 3–10× its bound; landing *at* the bound and not one entry
past it is the only evidence that the eviction path actually executes. A constant
in the source proves nothing — PH2.12's certification recorded the failure mode
where a stub agreed with a bug, and "the ceiling is declared" is the same class
of claim.

**The same scenarios before the fixes:**

| Scenario | Before | After |
|---|---:|---:|
| 1,000 clean connect/disconnect cycles → `user_connections` keys | **1,000** | **0** |
| 500 dirty disconnects (reap path) → `user_connections` keys | **500** | **0** |
| Broadcast with concurrent churn | **`RuntimeError: Set changed size during iteration`** | delivers to every socket |
| 5,000 chat users, all entries 999 s stale → cache entries | **5,000** | **512** (ceiling) |

---

## 13. Test Results

| Suite | Before | After | Delta |
|---|---:|---:|---|
| Backend hermetic (`pytest tests/`) | 2,188 passed, 6 xfailed | **2,216 passed, 6 xfailed** | **+28**, none failing |
| PH1 security subset | 452 | **452** | unchanged |
| Frontend (`yarn test`) | 319 passed, 18 suites | **324 passed, 18 suites** | **+5**, none failing |
| Production frontend build | green | **green** | unchanged |
| `resource_probe.py` | n/a | **exit 0** | new |

**New failures: none. Pre-existing failures: none.** The 6 `xfail`s are D-4
(refund stub → PH3.9) and D-10 (email validation → next auth sprint), unchanged
and untouched.

**Environmental note, recorded because it briefly looked like a regression.**
The first post-fix full run reported one failure:
`test_perf_regression.py::test_admin_dashboard_counts_are_gathered`. It passed
in isolation. The cause was this sprint's own method, not the code: that test
uses `inspect.getsource` + `ast.parse` on `server.admin_dashboard`, and
`server.py` was being edited **while the background run was in progress**, so
`inspect` read the current file at line offsets cached at import. The suite was
re-run clean with no concurrent edits. **This was not reported as a finding**, in
the same spirit as PH3.4 §3.3 and PH3.5's two self-corrections.

### 13.1 The tests fail on the old code — verified, not assumed

A regression test that passes before the fix protects nothing. Every test in
`test_resource_lifecycle.py` was run against the pre-PH3.6 tree (`git stash` of
the eight changed files, new files retained): **18 of 26 failed.** The 8 that
passed are the 6 covering `infrastructure/tasks.py` (a new file, so there was no
old behaviour to fail against) and the 2 *counter-tests* that assert preserved
behaviour — `test_throttling_still_works_after_bounding`, which is supposed to
pass both before and after, because without it deleting `_stamp`'s body entirely
would satisfy every ceiling assertion perfectly.

---

## 14. Long-Run Results

### 14.1 HTTP soak — 150 rps sustained

**Environment:** the PH3.5 harness stack (`APP_ENV=staging`, `stockassist_loadtest`
database, both providers mocked onto loopback), one uvicorn worker,
`REDIS_MAX_CONNECTIONS=200` per PH3.5 §25.2 item 5, `DISABLE_BACKGROUND_ENGINE=0`
so all four perpetual loops ran throughout.

**Run:** `scripts/load/soak.sh http 1800 150` —
`scripts/load/results/20260815T071727Z-soak-http/`.
**270,000 requests delivered at exactly 150.00 rps for 30m00s**, 64 samples at
30-second intervals, then a 60-second idle settle window.

| k6 | Value |
|---|---:|
| Load requests | **270,000** |
| Delivered rate | **150.00 rps** (offered 150) |
| p50 / p95 | **3.83 ms / 18.16 ms** |
| max | 1,297.23 ms |
| **5xx rate** | **0.000%** |
| **Timeout rate** | **0.000%** |
| Functional checks | 99.61% |
| 429 rate | 51.000% — see §14.1.1 |

**Every count structure was flat for thirty minutes. Not "roughly flat" —
identical.**

| Series | first | peak | last | delta |
|---|---:|---:|---:|---:|
| `websocket_connections` | 0 | 0 | 0 | **+0** |
| `websocket_tracked_users` | 0 | 0 | 0 | **+0** |
| `websocket_channel_subscriptions` | 0 | 0 | 0 | **+0** |
| `background_tasks_running` | 4 | 4 | 4 | **+0** |
| `event_bus_subscribers` | 1 | 1 | 1 | **+0** |
| `app_cache_entries{ai_chat_context}` | 0 | 0 | 0 | **+0** |
| `app_cache_entries{market_memory_fallback}` | 0 | 0 | 0 | **+0** |
| `app_cache_entries{portfolio_throttle}` | 63 | 63 | 63 | **+0** |
| `app_cache_entries{trade_throttle}` | 56 | 56 | 56 | **+0** |
| `process_open_fds` | 58 | 404 | 168 | see below |
| `process_resident_memory_bytes` | 42.7 MB | 87.1 MB | 49.4 MB | see below |

**File descriptors are the cleanest result in this sprint**, because they show
the full shape the objective asks for rather than a single number:

```
58 67 311 308 399 404 398 403 398 397 315 319 352 352 334 336 333 337 337 322
321 323 323 317 341 341 328 320 339 339 329 329 319 325 324 323 327 329 329 327
329 329 329 331 326 326 326 330 324 323 317 323 323 323 325 314 314 312 317 327
327 320 | 169 168
```

Baseline **58** → ramp to a **404** peak → a **flat plateau of 312–352 for
twenty-eight minutes with no upward drift** → and the moment load stops, **169**.
That plateau is the evidence. A ratchet would climb across it; this does not.

**Memory oscillates; it does not trend.** RSS across the 64 samples, in MB:

```
40 38 79 78 49 79 80 46 80 80 46 46 52 46 84 81 48 48 53 76 86 81 86 86 82 47
86 81 87 84 60 47 43 51 41 52 41 81 41 41 43 47 45 52 54 50 42 51 43 47 46 46
44 47 52 41 51 81 55 44 86 50 38 49
```

min **38.2**, max **87.1**, mean **58.9**. It sawtooths between a ~40 MB floor and
a ~85 MB ceiling for the entire run and **reaches its global minimum at sample
63, near the end**. The tool's own `first → last` delta reads **+9.2 MB**, and
that number should be ignored: **first-versus-last on an oscillating series is
not a trend, it is two arbitrary points on a sawtooth.** Recorded here as a
limitation of the summariser rather than quietly omitted — the min/max/mean and
the series itself are what carry the conclusion, which matches PH3.5 §18's
independent finding that RSS "goes down as often as up".

**Mongo idle reaping (M-8) verified live.** Immediately after the run the process
held **3** connections to 27017, against the **18 at steady state** PH3.5
measured with `maxIdleTimeMS` unset. That is the fix doing exactly what it was
added to do, observed rather than inferred.

#### 14.1.1 What the 429 rate means, and why it is not a defect

**51% of offered requests were rejected by the rate limiter**, so roughly 132,000
requests reached handlers and roughly 138,000 were refused at the limiter. This
is the traffic model, not a fault: the harness drives a fixed pool of 250 seeded
users at 150 rps, which concentrates far more requests per user than a real
population would, and the per-user limits are doing precisely what PH1.7 built
them to do. Zero 5xx and zero timeouts across all 270,000.

It is stated plainly because it **qualifies the soak's coverage**, and a report
that quoted "270,000 requests" without it would be overselling: the limiter path
(which writes to `rate_limits` in Mongo) got a heavier workout than the handler
path did. **Two consequences worth carrying forward.** First, the
`sessions`/`rate_limits` TTL-reaping question (§18.4) is now *more* interesting,
not less — this run wrote a great many limiter documents. Second, the AI
chat-context cache (M-2) shows **0 entries throughout**, because this scenario
does not exercise `/api/chat`: **M-2's fix is proven by the probe (§12) and by
the unit tests, and was not exercised by this soak.** Saying so is the point of
recording it.

### 14.2 WebSocket churn soak

**Run:** `WS_CONNECTIONS=100 scripts/load/soak.sh ws 600` —
`scripts/load/results/20260815T075516Z-soak-ws/`. Churn mode: each socket
connects, authenticates, subscribes to a real channel set, receives events, and
drops after ~2 s, continuously, for ten and a half minutes.

This is the **repeated connection test** (brief §12) run at a scale the
hermetic probe cannot reach, and it is the run that matters most for M-1 and
M-3 — both of which are churn defects that steady-state connections never
expose.

| k6 | Value |
|---|---:|
| **Sockets opened / closed** | **30,755** |
| **Closed early** | **0** |
| **Socket errors** | **0** |
| Domain events received | 25,096 |
| Subscribe ack rate | **100.00%** |
| Held full duration | 100.00% |
| ping → pong p95 | 4.00 ms |
| Connect p95 | 10.67 ms |

| Series | first | peak | last | delta |
|---|---:|---:|---:|---:|
| `websocket_connections` | 0 | 100 | **0** | **+0** |
| `websocket_tracked_users` | 0 | 100 | **0** | **+0** |
| `websocket_channel_subscriptions` | 0 | 100 | **0** | **+0** |
| `background_tasks_running` | 4 | 4 | 4 | **+0** |
| `event_bus_subscribers` | 1 | 1 | 1 | **+0** |
| `app_cache_entries` (all four) | — | — | — | **+0** |
| `process_open_fds` | 161 | 263 | **160** | **−1** |
| `process_resident_memory_bytes` | 60.5 MB | 104.1 MB | **57.5 MB** | **−2.9 MB** |

**30,755 connect/disconnect cycles, and every tracked structure returned exactly
to baseline** — with file descriptors one *below* where they started and RSS
2.9 MB *lower* than at the start.

**The three WebSocket maps moved in lockstep throughout**, sampled repeatedly
mid-run at 23/23/23 and 100/100/100. That equality is the invariant M-1 broke:
`websocket_tracked_users` is supposed to be bounded by `websocket_connections`,
and before the fix it was a cumulative count that only rose.

**M-3 is confirmed fixed under the exact condition that exposed it.** PH3.5
raised `RuntimeError: Set changed size during iteration` at 200 sockets over
14,057 churn cycles. This run did **30,755** cycles with the market broadcast
loop and the heartbeat price stream both running throughout: **zero socket
errors, zero closed early, 25,096 events delivered.**

**One honest limit on what this run proves.** The harness draws `user_id` from a
pool of 250 seeded accounts, so ids repeat. Even with the M-1 bug present,
`websocket_tracked_users` would have plateaued near 250 here rather than growing
without bound. **This run demonstrates that the invariant holds and that the maps
return to zero; it does not demonstrate the unbounded case.** That one needs a
distinct id per connection — the realistic adversarial shape, since the id is
unauthenticated — and it is covered by `resource_probe.py` (§12) and by
`test_repeated_cycles_leave_nothing_behind`, both of which measured 1,000
retained keys before the fix and 0 after.

---

## 15. Failure / Recovery Results

The question here is not whether the system survives a failure — PH3.5 answered
that. It is whether **failure, retry and recovery leave resources where they
started**, or whether each cycle ratchets something upward. A retry storm that
opens a connection per attempt, a reconnect that registers a second listener, or
a supervisor that respawns a dead loop are all invisible while the dependency is
healthy.

### 15.1 Redis: full outage and recovery

`docker stop stockassist-loadtest-redis` → 80 s down → `docker start`, sampled
throughout with the backend serving.

| Moment | `ready` | Circuit | Pool | Pub/Sub subscriber | `background_tasks_running` | `event_bus_subscribers` | FDs |
|---|---|---|---|---|---:|---:|---:|
| Before | 200 | closed | `in_use 0, available 138` | `running, connected` | 4 | 1 | 161 |
| Outage +8 s … +48 s | **200** | open | **released** | `running: true, connected: false` | **4** | **1** | **24** |
| Recovery +10 s | 200 | **closed** | `available 22` | reconnecting | 4 | 1 | — |
| Recovery +20 s … +50 s | 200 | closed | `available 22` | **`connected: true`, `reconnects_total: 1`** | **4** | **1** | 42 |

**Five things this establishes, and the last is the one the brief asks for
explicitly.**

1. **Readiness stayed 200 for the entire outage.** Redis is registered as a
   non-critical health check, so losing it degrades the cache rather than the
   service — which is the trade `services/cache.py`'s docstring argues for, now
   observed rather than asserted.
2. **The pool was released, not stranded.** File descriptors fell from **161 to
   24** and Redis sockets to **zero** when the breaker opened —
   `_schedule_pool_reset` genuinely drops every pooled connection. Worth noting
   against M-12: the Redis pool's high-water mark is permanent *while healthy*,
   but a failure releases it.
3. **The subscriber never died.** `running: true` throughout, with
   `last_error` recording the refused connection — the exact failure mode
   `infrastructure/redis_pubsub.py` was written to prevent, confirmed against a
   real outage rather than a mock.
4. **Recovery was bounded and quiet:** circuit closed within 10 s, and the
   subscriber came back with **`reconnects_total: 1`** — one reconnect, not a
   storm. The pool rebuilt to **22** connections on demand rather than jumping
   back to its old 139 high-water mark.
5. **No duplicate listeners after reconnect.** `event_bus_subscribers` held at
   **1** and the Pub/Sub registry reported exactly **one** channel with **one**
   subscriber, before, during and after. This is brief §6's explicit
   requirement — a reconnect must not produce listener 1, listener 2, listener
   3 — and it is the property M-7 hardened and `redis_pubsub`'s channel registry
   already guaranteed.

`background_tasks_running` held at **4** across the whole cycle: no loop died and
none was respawned.

**The sampled series over the same window** (`soak.sh sample 300 redis-outage`):

| Series | first | peak | last | delta |
|---|---:|---:|---:|---:|
| `process_open_fds` | 163 | 163 | **63** | **−100** |
| `process_resident_memory_bytes` | 59.8 MB | 71.0 MB | 60.2 MB | +0.3 MB |
| `background_tasks_running` | 4 | 4 | 4 | **+0** |
| `event_bus_subscribers` | 1 | 1 | 1 | **+0** |
| `app_cache_entries{market_memory_fallback}` | 0 | **68** | 59 | +59 |
| `app_cache_entries{portfolio_throttle}` | 63 | 63 | 63 | **+0** |
| `app_cache_entries{trade_throttle}` | 56 | 56 | 56 | **+0** |

**The in-process cache fallback engaged and stayed bounded.** It went from 0 to
**68** entries while Redis was gone — the degraded mode `services/cache.py`
documents, doing exactly what it says — against a ceiling of 1,024. **It did not
reach the ceiling, so this run did not exercise the eviction path**, only the
engagement path. PH3.5 §25.2 item 3 asked for confirmation that the eviction
actually runs rather than trusting the constant; that confirmation comes from
`resource_probe.py` and the unit tests (§12), not from here, and the distinction
is recorded rather than blurred.

### 15.2 Provider degradation and recovery

`scripts/load/load-test.sh failure` — six phases, ~90 s each, sampled
continuously by `soak.sh sample 900 provider-failure`.

| Phase | Injected | 5xx | Timeouts | 429 | Checks | `api` p95 | `ai` p95 |
|---|---|---:|---:|---:|---:|---:|---:|
| 0 | clean baseline | 0.000% | 0.000% | 0.000% | **100%** | 34.72 ms | 1,326 ms |
| 1 | market +800 ms | 0.000% | 0.000% | 0.000% | **100%** | 54.95 ms | 1,636 ms |
| 2 | market 30% 503 | 0.000% | 0.000% | 0.000% | **100%** | 32.48 ms | 1,000 ms |
| 3 | market 10% timeout (30 s) | 0.000% | 0.000% | 0.000% | **100%** | 44.23 ms | 2,110 ms |
| 4 | **AI 6 s + 20% 429** | 0.000% | 0.000% | 0.000% | **100%** | **29.17 ms** | **15,363 ms** |
| 5 | recovery | 0.000% | 0.000% | 0.000% | **100%** | 36.71 ms | 1,006 ms |

**Zero 5xx, zero timeouts and 100% of functional checks in every phase**,
including one where the AI provider was effectively unusable.

**Phase 4 is the one to read twice.** `ai` p95 reached **15,363 ms** while `api`
p95 on the same run was **29.17 ms — the lowest of all six phases.** A
catastrophically degraded AI provider did not cost the rest of the product a
millisecond, which is what proper isolation looks like and is not something to
take on trust.

**Resource behaviour across the whole nine-minute fault window** — the part this
sprint actually owns, since retry and timeout paths are where connections and
tasks leak:

| Series | first | peak | last | delta |
|---|---:|---:|---:|---:|
| `background_tasks_running` | 4 | 4 | 4 | **+0** |
| `event_bus_subscribers` | 1 | 1 | 1 | **+0** |
| `websocket_*` (all three) | 0 | 0 | 0 | **+0** |
| `app_cache_entries{ai_chat_context}` | 0 | **4** | 4 | +4 |
| `app_cache_entries{market_memory_fallback}` | 59 | 59 | 59 | **+0** |
| `app_cache_entries{portfolio_throttle}` | 63 | 63 | 63 | **+0** |
| `app_cache_entries{trade_throttle}` | 56 | 59 | 59 | +3 |
| `process_open_fds` | 55 | 103 | 76 | +21 |
| `process_resident_memory_bytes` | 57.2 MB | 111.2 MB | **51.2 MB** | **−6.0 MB** |

**Sustained provider failure, 30-second timeouts and 20% rate limiting produced
no task growth, no listener duplication and no connection storm.** RSS ended
6 MB *below* where it started. The two caches that moved (`ai_chat_context` 0→4,
`trade_throttle` +3) moved because the AI phase exercised paths the HTTP soak
does not — four entries against a 512 ceiling.

### 15.3 Clean shutdown (M-4)

The defect M-4 fixed is only observable at SIGTERM, so it was tested there
directly: start the backend against the load environment at `LOG_LEVEL=INFO`,
confirm the four loops register, send SIGTERM, read the sequence.

Startup registers all four by name:

```
Background task started: market-broadcast-loop
Background task started: ai-monitoring-loop
Background task started: ai-heartbeat-loop
Background task started: ai-price-stream-loop
```

And SIGTERM produces exactly the designed order:

```
16:51:21.861  INFO  server                       Shutdown initiated — readiness now reports draining
16:51:21.862  INFO  services.heartbeat_engine    AI heartbeat engine stopped
16:51:21.863  INFO  infrastructure.tasks         Cancelled 2 background task(s)
16:51:22.336  INFO  infrastructure.redis_pubsub  Redis pub/sub subscriber stopped on 'sa:events'
16:51:22.343  INFO  infrastructure.redis_client  Redis client closed
16:51:22.345  INFO  services.http_client         Closed 1 pooled HTTP client(s).
              INFO  uvicorn                      Application shutdown complete.
```

**Readiness fails first, then the four producers are cancelled, and only then are
the resources they use torn down** — the inversion of startup, which is the whole
point of the change. Total elapsed **484 ms**, far inside both the registry's 5 s
grace and the compose file's 30 s `stop_grace_period`.

**Not one WARNING or ERROR line was emitted.** That absence is the result. Before
this fix the loops survived into steps 4–6 and then did I/O against a closed
Mongo client, each raising into its own `except Exception` handler — so a clean
stop emitted a burst of connection errors that reads, in a log aggregator,
exactly like a crash. A separate SIGTERM at `LOG_LEVEL=WARNING` produced **zero
output at all**, which is the same result stated the other way round.

---

## 16. Findings

Severity uses the brief's scale: P0 continuous growth, P1 reconnect leaks,
P2 background-task leaks, P3 cache growth, P4 minor lifecycle.

| ID | Severity | Finding | Confirmed by | Status |
|---|---|---|---|---|
| **M-1** | **P0** | `ConnectionManager.user_connections` never removes a user's key when their last socket goes. Key is an unauthenticated query parameter, so growth is remotely triggerable at will. Both the clean path (`disconnect`) and the dropped-connection path (`_reap`) had the gap. | 1,000 cycles → 1,000 retained keys; 500 dirty → 500 | **FIXED** |
| **M-2** | **P0** | `ai_context_builder._cache` retains a multi-KB `ChatContext` per user forever; the 8 s TTL is checked on read and enforced nowhere. | 5,000 users, all 999 s stale → 5,000 live entries | **FIXED** |
| **M-3** | **P1** | `broadcast()` and `send_to_user()` iterate a live container across an `await`, so a concurrent disconnect raises and drops the broadcast to every socket past that point. (PH3.5's L-2, routed here.) | `RuntimeError: Set changed size during iteration` reproduced | **FIXED** |
| **M-4** | **P2** | Four perpetual loops started with bare `asyncio.create_task`: no strong reference (GC hazard) and no cancellation path, so all four ran on against a Mongo client `shutdown()` was closing. | code + shutdown ordering | **FIXED** |
| **M-5** | **P3** | `portfolio_stream._last_emit` and `trade_stream._last_emit` retain one float per user forever. Small per entry, monotonic with cumulative signups. | code; ceiling exercised at 3× | **FIXED** |
| **M-6** | **P4** | `BrokerStreamManager` retains a finished `BrokerStream` — including the expired broker **access token** in its `session` — after `_AuthExpired`, and reports it in `status()`. | code | **FIXED** |
| **M-7** | **P4** | `start_event_bridge` registers the catch-all `"*"` bus handler unconditionally; a second call doubles delivery of every event forever. Latent (one caller today). | code | **FIXED** |
| **M-8** | **P4** | Mongo `maxIdleTimeMS` unset: pooled connections are never reaped when idle, so the pool only ratchets up. | pymongo semantics | **FIXED** |
| **M-9** | **RISK** | Mongo `socketTimeoutMS` unset: no read timeout, so a query against a wedged primary holds its request and connection indefinitely. | pymongo semantics | **NOT FIXED** — §18 |
| **M-12** | **P4 / RISK** | **The Redis connection pool never reaps idle connections, so its high-water mark is permanent.** Four minutes after all load stopped the process still held **139 sockets to Redis** with `in_use: 0`. Bounded by `max_connections`, so not unbounded growth — but it is exactly the connection *ratchet* M-8 fixed on the Mongo side, and it **changes the trade-off in PH3.5's L-1**: raising `REDIS_MAX_CONNECTIONS` from 24 to 200 to cure the cascade also raises the permanent per-worker footprint from ≤24 to ≤200. | measured (139 held; app diagnostics report `available: 138, in_use: 0, max: 200`) **and verified at the source**: `redis/asyncio/connection.py` `ConnectionPool.release()` appends to `_available_connections` with no idle timeout, and the pool constructor accepts no reaping parameter | **NOT FIXED** — routed to PH3.7 with L-1 (§17.1, §18) |
| **M-13** | **INFO** | Even at `REDIS_MAX_CONNECTIONS=200`, the pool was momentarily **exhausted during the soak's ramp**: 6 `ConnectionError: Too many connections` (redis-py's own pool error, `asyncio/connection.py:1062`), enough to open the circuit breaker **once**, after which it closed and stayed closed for the remaining 30 minutes. | app diagnostics: `circuit_opens_total: 1`, `connection_errors_total: 6` | **REPORTED** — input for PH3.7's L-1 sizing |
| **F-1** | **P3** | `tradeLive.byId` merges each snapshot onto the previous map, but every producer publishes the user's **complete** open set — so a closed trade is retained forever *and* displayed as open. | code + producer contract | **FIXED** |
| **F-2** | **P3** | `tradeReviews` keyed by trade id, unbounded; each value is a multi-KB AI review. | code | **FIXED** |
| **F-3** | **P3** | `aiRuns` cap never evicts an `active` run and `break`s when all are active. A socket dropping mid-run leaves a run active forever, so enough dropped runs defeat the cap entirely. | code | **FIXED** |
| **M-10** | **INFO** | `EventBus._event_log` rebuilds a 500-element list on every publish past the bound. Bounded and correct; allocation churn only. | code | **NOT FIXED** (speculative) |
| **M-11** | **INFO** | `usePriceFlash` / `useCardEntrance` do not kill tweens on unmount; GSAP holds a detached element ≤600 ms. | code | **NOT FIXED** (transient) |

### 16.1 What was checked and found correct

Recorded because "we looked and it was fine" is a result:

`infrastructure/redis_client.py` and `redis_pubsub.py` (no defect — §6);
`services/http_client.py` pools; `services/cache.py` bounds and sweep;
`scanner_worker` and `news_service` cooldown pruning; metric label cardinality
and its overflow series; the bounded log queue; every Mongo cursor
(`to_list(N)` everywhere, no `to_list(None)`); APScheduler shutdown; broker
session eviction; the entire frontend timer/listener/observer/GSAP surface
(§10); and `RealtimeProvider`'s reconnect path, which cannot accumulate handlers
by construction.

---

## 17. Fixes

| File | Change |
|---|---|
| `backend/server.py` | M-1: drop the user key when the last socket goes, in both `disconnect` and `_reap`. M-3: iterate snapshots in `broadcast` and `send_to_user`. M-4: spawn the two application loops through the task registry; cancel them (and the heartbeat engine) **first** in `shutdown`, before the resources they use. M-8: explicit, env-overridable Mongo client options. New `_collect_resource_gauges` collector. |
| `backend/infrastructure/tasks.py` | **New.** Supervised task registry: strong reference for a task's life, released on completion; one task per name with the refused coroutine **closed**; bounded `cancel_all()`; a crashed task logged with its traceback rather than swallowed. |
| `backend/infrastructure/__init__.py` | Export `tasks`. |
| `backend/services/ai_context_builder.py` | M-2: `_cache_store` / `_prune_cache` / `cache_stats`, bounded at 512 with sweep-expired-then-evict-oldest — deliberately the same idiom as `services/cache.py`. |
| `backend/services/heartbeat_engine.py` | M-4: both loops spawned through the registry under named constants; new `stop_engine()` returning the engine to a startable state. |
| `backend/services/portfolio_stream.py`, `trade_stream.py` | M-5: bounded at 4,096 with a 300 s staleness horizon; `throttle_stats()`. |
| `backend/services/brokers/stream.py` | M-6: `discard()` — pops the registry entry without awaiting, because the caller runs inside the stream's own task. |
| `backend/services/broker_engine.py` | M-6: call `discard` on token expiry. |
| `backend/services/realtime/event_bridge.py` | M-7: register the `"*"` handler at most once; `reset_for_tests()`. |
| `backend/observability/metrics.py` | Six new gauges for the bounded structures (§19). |
| `frontend/src/store/realtimeStore.js` | F-1: rebuild `tradeLive.byId` from the incoming snapshot, preserving object identity for unchanged rows. F-2: bound `tradeReviews` to 25. F-3: hard ceiling of 50 on `aiRunOrder`. |
| `backend/scripts/resource_probe.py` | **New.** The in-process instrument (§11). |
| `scripts/load/soak.sh` | **New.** The sustained-soak runner with continuous sampling (§11). |
| `backend/tests/test_resource_lifecycle.py` | **New.** 28 regression tests (§13.1). |
| `frontend/src/store/__tests__/realtimeStore.test.js` | 5 new tests for F-1/F-2/F-3. |

### 17.1 Deliberately not fixed

* **M-9 `socketTimeoutMS`** — §18. A number picked without production data
  aborts real work.
* **M-10 event-log slicing** — bounded and correct. The brief forbids optimising
  code because it looks inefficient, and a `deque` swap would move an O(500) copy
  from publish-time to read-time, not remove it.
* **M-11 GSAP tweens** — a 600 ms transient is not a leak.
* **PH3.5's L-1 (Redis pool size), L-3 (bcrypt on the event loop), L-5, L-6,
  S-1** — owners unchanged (PH3.7 / next security sprint). Changing a security
  control or a deployment default inside a memory sprint is how a sprint stops
  being reviewable.

---

## 18. Remaining Risks

| # | Risk | Why it is still open |
|---|---|---|
| 1 | **`MONGO_SOCKET_TIMEOUT_MS` unset (M-9)** — a wedged primary holds requests and connections indefinitely. | Requires the slowest legitimate production query. Wired as an env var; **TO BE BASELINED IN STAGING**. |
| 2 | **Multi-worker resource behaviour unmeasured.** Every number here is one uvicorn worker. Each worker holds an independent copy of every in-process cache, so the ceilings in §19 are **per worker**, and the budget multiplies. **M-12 makes this sharper than it sounds:** the Redis pool's high-water mark is permanent, so N workers sized at 200 hold up to 200N Redis sockets indefinitely, and Redis's own `maxclients` is the ceiling they all share. | Inherited from PH3.5's L-4; owner PH3.7. |
| 2b | **Redis pool connections are never reaped when idle (M-12).** 139 sockets held with `in_use: 0`. Not fixed here on purpose: redis-py exposes no idle-reaping option, so the only in-process fix is a custom pool subclass — a rewrite of working infrastructure, which this sprint's brief forbids and which is the wrong change to make inside a memory sprint. The deployment-side lever (Redis's own `timeout` directive, which closes idle clients and lets redis-py reconnect on demand) belongs with the sizing decision. | Owner **PH3.7**, together with L-1. |
| 3 | **Multi-day continuous operation unmeasured.** The soak is tens of minutes. Fragmentation and slow allocator drift live above that horizon. | Environment; needs staging. |
| 4 | **`sessions` and `rate_limits` collection growth relies on Mongo TTL reaping.** Both grow with every request. The TTL indexes exist and were verified present; whether the reaper keeps up at sustained write rate is a **database-side** question this sprint did not measure. | Needs a staging soak with `db.serverStatus()` sampling. Carried from PH3.5 §25.2 item 4. |
| 5 | **The in-process cache fallback path is exercised only when Redis is down.** The soak ran with a healthy Redis (pool 200), so `_memory`'s eviction ran zero times in it — it is proven by `resource_probe.py` and the unit tests, not by the soak. | Deliberate: PH3.5 §25.2 item 5 requires establishing which system is soaked. |
| 6 | **Frontend memory measured structurally, not empirically.** The bounds are asserted by tests; no heap profile of a real all-day session was taken. | No browser profiling harness exists; PH3.7 owns frontend paint/RUM metrics. |

---

## 19. Resource Budget

Per uvicorn worker. Values marked **measured** come from §12/§14; values marked
**TO BE BASELINED IN STAGING** are deliberately not invented.

| Resource | Budget | Basis |
|---|---|---|
| Backend RSS, idle | 45–80 MB | measured (this sprint + PH3.5 §18) |
| Backend RSS, sustained load | ≤ 150 MB | measured; PH3.5 saw 33–110 MB across every shape |
| Backend CPU | 100% of **one** core is the ceiling of one worker | PH3.5 §18.1 |
| Open file descriptors | tracks concurrent connections; must return | measured |
| Mongo pool | `maxPoolSize` **100**, idle reaped after **60 s** | configured this sprint |
| Redis pool | `REDIS_MAX_CONNECTIONS` — **24 shipped default is too small**; 200 used here. **Budget the pool size as a permanent per-worker socket cost, not a peak** (M-12): idle connections are never reaped, so whatever the pool reaches, it holds. Measured: **139 sockets still open with `in_use: 0`** four minutes after load stopped | PH3.5 L-1 + PH3.6 M-12, owner PH3.7 |
| Redis Pub/Sub connections | exactly **1** per subscribed channel | enforced by registry |
| Outbound HTTP sockets | ≤ **20** per (loop, timeout) pool | `services/http_client.py` |
| Supervised background tasks | exactly **4** in a healthy process | `background_tasks_running` |
| Event-bus subscribers | exactly **1** in a healthy process | `event_bus_subscribers` |
| `websocket_tracked_users` | **≤ `websocket_connections`**, and must fall with it | the M-1 alert |
| AI chat-context cache | ≤ **512** entries | `_CACHE_MAX_ENTRIES` |
| Market cache fallback | ≤ **1,024** keys | `_MEMORY_MAX_KEYS` |
| Portfolio / trade throttle maps | ≤ **4,096** each | `_MAX_TRACKED_USERS` |
| Event-bus log | ≤ **500** events | `_max_log_size` |
| Concurrent WebSocket connections | **TO BE BASELINED IN STAGING** | 150 held cleanly (PH3.5); no ceiling found |
| Max sustained rps | ~300 with an adequate Redis pool; ~410 CPU-bound | PH3.5 §20 |

**The alert worth writing first** is `websocket_tracked_users` holding a floor
above zero while `websocket_connections` is at zero. That is M-1's exact
signature, and before this sprint no dashboard in the system could have shown it.

---

## 20. PH3.6 Exit Decision

| Gate | Status |
|---|---|
| No confirmed critical memory leaks | ✅ two found (M-1, M-2), both fixed and regression-tested |
| No confirmed critical resource leaks | ✅ M-3, M-4, M-6, M-8 fixed |
| WebSocket lifecycle is bounded | ✅ §7, §12, §14.2 |
| Redis connections are bounded | ✅ §6 (no defect found) |
| MongoDB connections are bounded | ✅ §5, idle reaping added |
| Background tasks have lifecycle management | ✅ `infrastructure/tasks.py` |
| Event listeners are cleaned up | ✅ backend §8/M-7, frontend §10 |
| Caches are bounded or TTL-controlled | ✅ §9, ceilings exercised not asserted |
| Reconnect behaviour is bounded | ✅ §7, §14.2 |
| Resource cleanup occurs on shutdown | ✅ verified at SIGTERM (§15.3): producers cancelled before their dependencies, 484 ms, **zero warnings or errors** |
| Repeated connection test is stable | ✅ §12 (5,000 cycles), §14.2 |
| Failure/recovery test is stable | ✅ §15 — Redis outage + recovery, six provider-degradation phases, and SIGTERM, all with zero task or listener growth |
| Relevant tests pass | ✅ §13 — backend 2,216, frontend 324, build green |
| Documentation updated | ✅ §21 |

### **PH3.6 STATUS: PASS WITH CONDITIONS**

Conditions, all of them environmental rather than defects:

1. **`MONGO_SOCKET_TIMEOUT_MS` must be baselined in staging** and set (§18.1).
2. **Multi-worker resource behaviour is unmeasured** — the §19 budget is *per
   worker* and must be multiplied (§18.2).
3. **Multi-day operation is unmeasured** — the soak is tens of minutes, not days
   (§18.3).
4. **Mongo TTL reaping of `sessions`/`rate_limits` under sustained write rate is
   unmeasured** (§18.4).
5. **Frontend bounds are asserted structurally, not heap-profiled** (§18.6).
6. **The Redis pool never reaps idle connections (M-12)** — 139 sockets held with
   `in_use: 0`. Bounded, released on failure, and not a defect this sprint should
   fix in code; but it means `REDIS_MAX_CONNECTIONS` must be budgeted as a
   **permanent per-worker socket cost**, and PH3.7 owns that decision alongside
   L-1 (§18.2b).

**PH3.7 is safe to begin.** Nothing here blocks it, and three of PH3.6's
findings hand it better inputs than it would otherwise have had: the §19 budget
gives its deployment work concrete per-worker numbers, the new gauges give its
monitoring work something real to alert on, and PH3.5's L-1 Redis pool sizing —
still open and still PH3.7's — now has a documented soak profile behind it.

---

## 20.1 Run artefacts

Every measurement in §14 and §15 is reproducible from a committed tool, and its
raw output is on disk under `scripts/load/results/`:

| Directory | Run |
|---|---|
| `20260815T071727Z-soak-http` | 30-minute HTTP soak, 150 rps (§14.1) |
| `20260815T074953Z-soak-post-soak-idle` | 4-minute idle recovery after the soak |
| `20260815T075516Z-soak-ws` | WebSocket churn soak, 30,755 cycles (§14.2) |
| `20260815T080729Z-soak-redis-outage` | Redis outage and recovery (§15.1) |
| `20260815T081016Z-soak-provider-failure` | resource sampling across all six fault phases |
| `20260815T081018Z-failure-0-baseline` … `-failure-5-recovery` | the six provider-degradation phases (§15.2) |

Each contains `samples.csv` (one row per 30 s), `k6.log`, `k6-summary.json` and,
for the soak runs, `verdict.txt`.

---

## 21. Documentation Updated

| Document | Change |
|---|---|
| `docs/performance/PH3_MEMORY_STABILITY.md` | this document |
| `docs/performance/README.md` | added this sprint and both new tools |
| `.claude/TASK.md` | PH3.6 entry |
| `.claude/CHANGELOG.md` | PH3.6 entry |
| `.claude/PRODUCTION_ROADMAP.md` | PH3.6 record + numbering note |
| `.claude/PRODUCTION_HARDENING.md` | resource budget + task lifecycle |
| `PRODUCTION_READINESS_REPORT.md` | memory/resource stability status |

No unrelated documentation was modified.

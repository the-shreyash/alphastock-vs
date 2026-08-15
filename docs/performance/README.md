# Performance

Measured performance work on StockAssist AI: baselines, query-plan analysis,
optimizations and their before/after evidence.

## Documents

| Document | Contents |
|---|---|
| [`PH3.4_PERFORMANCE_CERTIFICATION.md`](PH3.4_PERFORMANCE_CERTIFICATION.md) | The PH3.4 sprint record: measurement methodology, baseline, per-layer analysis (frontend, bundle, API, database, Redis, real-time, AI, external providers), the four implemented optimizations with before/after numbers, the regression tests, remaining bottlenecks, and the PH3.5 load-test handoff. |
| [`PH3.5_LOAD_TEST_CERTIFICATION.md`](PH3.5_LOAD_TEST_CERTIFICATION.md) | The PH3.5 sprint record: traffic model, the five concurrency stages, the arrival-rate saturation search, authentication throughput, database / Redis / WebSocket / AI / trading behaviour under load, provider fault injection, the capacity envelope and its binding constraint at each level, eleven findings with owners, and the PH3.6 handoff. |
| [`PH3_MEMORY_STABILITY.md`](PH3_MEMORY_STABILITY.md) | The PH3.6 sprint record: resource inventory, the memory-leak / database / Redis / WebSocket / background-task / cache / frontend-lifecycle audits, the in-process baseline, the sustained soak, failure-and-recovery behaviour, fourteen findings, the resource budget, and the exit decision. |

**They answer different questions, and the difference is the point.** PH3.4
measured the cost of *one* request and concluded the application code was not the
bottleneck. PH3.5 applied concurrency and found three P1 defects that a
single-request measurement is structurally unable to see — a connection-pool
cascade, a set mutated during a broadcast, and a 234 ms blocking call that queues
every other request behind it. One of them **corrects** a PH3.4 conclusion. PH3.6
asked what *survives* a request, and found two unbounded per-user maps that
neither earlier sprint could have seen — because **a leak is a shape, not a
size**. Both grow a few hundred bytes per event, which is less than the noise
between two idle RSS samples, so PH3.5's flat memory curve was accurate and
structurally incapable of showing them. Read PH3.4 for per-request cost, PH3.5
for behaviour under concurrency and capacity, PH3.6 for what the process is still
holding an hour later.

## The tools

Both live in `backend/scripts/` and are meant to be re-run, not read once.

```bash
cd backend

# Query plans against a real MongoDB, before and after ensure_indexes().
# Seeds an isolated scratch database and drops it; refuses to run if its name
# resolves to the configured DB_NAME.
python scripts/perf_db_benchmark.py
python scripts/perf_db_benchmark.py --json out.json

# API surface: queries per request, documents read, payload bytes, cold/warm.
python scripts/perf_api_profile.py --offline   # application cost only
python scripts/perf_api_profile.py             # + real provider latency
```

`tests/_perf.py` holds the shared instrument (query counting, cold/warm
measurement) used by both the scripts and `tests/test_perf_regression.py`.

### The load harness (PH3.5)

Lives in `scripts/load/`, driven by one runner. It brings the whole environment up
and down, preflights that it is **not** pointed at the configured `DB_NAME`,
snapshots server-side metrics before and after, and writes artefacts to
`scripts/load/results/<timestamp>-<shape>/`.

```bash
scripts/load/load-test.sh smoke        # ~40 s sanity run
scripts/load/load-test.sh stress       # 100 VUs, mixed traffic
scripts/load/load-test.sh saturation   # arrival-rate ceiling search
scripts/load/load-test.sh auth         # login throughput
scripts/load/load-test.sh ratelimit    # 429 boundary and bystander isolation
scripts/load/load-test.sh websocket    # hold and churn modes
scripts/load/load-test.sh failure      # provider fault injection
```

**No third party ever receives load.** Market data is redirected with
`MARKET_DATA_YAHOO_BASE` and AI with the SDK's own `ANTHROPIC_BASE_URL`, both onto
local stdlib mocks that inject latency, errors, timeouts and 429s on demand.
`backend/tests/test_load_harness.py` (12 tests) pins that the market override is
**inert when unset** *and* **effective when set** — the second matters as much as
the first, because a working provider and a working mock produce the same green
result, so a silently-broken redirect would send the next run at Yahoo and nothing
would report it.

### The resource instruments (PH3.6)

Two tools, because the leaks this application can have are invisible to the load
harness. A dict that gains one key per WebSocket connection and never drops it
costs a few hundred bytes; a hundred thousand connections later it is the largest
object in the process, and for the whole first hour it moves RSS less than
Python's allocator does between two idle samples.

```bash
cd backend

# In-process: drive the application's own objects through connect/disconnect
# cycles, chat-context writes, bus publishes and task spawn/cancel, then report
# every tracked count at each checkpoint plus a settle phase.
# Exits non-zero if a lifecycle structure fails to return to baseline or a cache
# exceeds its own ceiling, so it is a check and not only a report.
python scripts/resource_probe.py
python scripts/resource_probe.py --cycles 5000 --json out.json
```

```bash
# Under sustained load: samples /api/metrics every 30 s for the WHOLE run and
# writes one CSV row per sample, then a 60 s idle settle window.
scripts/load/soak.sh http 1800 150   # 150 rps for 30 minutes
scripts/load/soak.sh ws 900          # WebSocket connect/churn
scripts/load/soak.sh sample 300 idle # sample only, drive nothing
```

**Why `soak.sh` samples continuously and `load-test.sh` does not.**
`load-test.sh` snapshots server metrics before and after a run, which cannot
distinguish "grew and came back" from "never grew". A ratchet is only visible as
a *series*. The settle window matters for the same reason: the difference between
a working cache and a leak is entirely in what happens after the load stops.

## The one rule worth repeating

**Three measurement contexts, and they answer different questions.** The hermetic
suite runs against an in-memory dictionary, so it measures application code and
nothing else. A real MongoDB measures query *plans*. A real provider measures
transport. Reporting a number from one context as if it came from another is the
main way performance work goes wrong — PH3.4 §2 records which metrics were
genuinely unavailable in this environment rather than estimating them, and §3.3
records two of its own measurements that were wrong before they were right.

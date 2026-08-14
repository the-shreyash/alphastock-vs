# Performance

Measured performance work on StockAssist AI: baselines, query-plan analysis,
optimizations and their before/after evidence.

## Documents

| Document | Contents |
|---|---|
| [`PH3.4_PERFORMANCE_CERTIFICATION.md`](PH3.4_PERFORMANCE_CERTIFICATION.md) | The PH3.4 sprint record: measurement methodology, baseline, per-layer analysis (frontend, bundle, API, database, Redis, real-time, AI, external providers), the four implemented optimizations with before/after numbers, the regression tests, remaining bottlenecks, and the PH3.5 load-test handoff. |

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

## The one rule worth repeating

**Three measurement contexts, and they answer different questions.** The hermetic
suite runs against an in-memory dictionary, so it measures application code and
nothing else. A real MongoDB measures query *plans*. A real provider measures
transport. Reporting a number from one context as if it came from another is the
main way performance work goes wrong — PH3.4 §2 records which metrics were
genuinely unavailable in this environment rather than estimating them, and §3.3
records two of its own measurements that were wrong before they were right.

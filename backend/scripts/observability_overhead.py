#!/usr/bin/env python3
"""Measure what the observability instrumentation costs (PH3.7).

WHY THIS IS A COMMITTED SCRIPT AND NOT A ONE-OFF
------------------------------------------------
The claim "instrumentation is cheap" is the one an observability sprint is most
tempted to assert and least likely to check. It is also the claim most likely to
stop being true later: a metric family added next year, a label computed on the
hot path, a classifier that starts importing something. A number in a document
ages badly; a script that reproduces the number does not.

Every figure in `docs/architecture/OBSERVABILITY.md` §10 comes from this file.
Re-run it after adding an instrument, and update that table if anything moved.

WHAT THIS MEASURES, AND WHAT IT CANNOT
--------------------------------------
It measures the **per-call cost of each instrument in isolation**, single
threaded, with a warm cache. That is the right instrument for "did this change
make the counter path slower" and the wrong one for "what does this cost in
production": it cannot see lock contention between concurrent workers, which is
the only mechanism by which these numbers could become interesting. Real
concurrency needs a load test against a durable staging environment
(`scripts/load/`, roadmap PH2.12), and until that exists §10 says so.

Usage:
    python backend/scripts/observability_overhead.py [--calls N]
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

# Import the application package the same way the test suite does.
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Keep the process out of any real environment: this script imports the metrics
# registry, and a stray `.env` would only add noise to a measurement.
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("APP_ENV", "testing")

from observability import errors, instruments, metrics, mongo_monitor  # noqa: E402

#: Five repeats and a median, not a mean of one run: the first repeat routinely
#: lands 20-30% high on a laptop (CPU frequency scaling, cold branch
#: predictors), and a mean lets that single sample set the published number.
REPEATS = 5


def bench(label: str, fn, calls: int) -> float:
    per_repeat = max(1, calls // REPEATS)
    for _ in range(min(5_000, per_repeat)):  # warmup
        fn()
    samples = []
    for _ in range(REPEATS):
        started = time.perf_counter()
        for _ in range(per_repeat):
            fn()
        samples.append((time.perf_counter() - started) / per_repeat)
    median = statistics.median(samples)
    spread = (max(samples) - min(samples)) / median * 100 if median else 0.0
    print(f"  {label:<48} {median * 1e6:7.2f} µs   (spread {spread:4.0f}%)")
    return median


class _FakeCommandEvent:
    command_name = "find"
    duration_micros = 1500
    failure = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=100_000,
                        help="total calls per benchmark (default 100000)")
    args = parser.parse_args()
    n = args.calls

    counter = metrics.registry.counter("overhead_bench_counter", "bench", ("a",))
    gauge = metrics.registry.gauge("overhead_bench_gauge", "bench")
    histogram = metrics.registry.histogram("overhead_bench_histogram", "bench", ("a",))
    command_listener = mongo_monitor.CommandMetricsListener()
    pool_listener = mongo_monitor.PoolMetricsListener()

    def provider_call():
        with instruments.track_provider("market_data", "get_quote"):
            pass

    def ai_call():
        with instruments.track_ai("claude"):
            pass

    print("\nRegistry primitives")
    bench("Counter.inc (labelled)", lambda: counter.inc(labels=("x",)), n)
    bench("Gauge.set", lambda: gauge.set(1.0), n)
    bench("Histogram.observe (11 buckets)", lambda: histogram.observe(0.037, labels=("x",)), n)

    print("\nError classification")
    bench("classify_exception (RuntimeError)",
          lambda: errors.classify_exception(RuntimeError("x")), n)
    bench("classify_exception (unmapped, deep MRO)",
          lambda: errors.classify_exception(KeyboardInterrupt()), n)

    print("\nSubsystem instruments")
    bench("record_error (keystone)",
          lambda: instruments.record_error("database", errors.DATABASE), n)
    bench("record_exception (classify + record)",
          lambda: instruments.record_exception("database", RuntimeError("x")), n)
    bench("record_auth_event",
          lambda: instruments.record_auth_event("login_success", "success"), n)
    bench("record_ws_fanout (0 failures)",
          lambda: instruments.record_ws_fanout("broadcast", 0), n)
    bench("record_ws_fanout (3 failures)",
          lambda: instruments.record_ws_fanout("broadcast", 3), n)
    bench("record_mongo_command (ok)",
          lambda: instruments.record_mongo_command("find", 0.002, ok=True), n)
    bench("track_provider (context manager)", provider_call, n)
    bench("track_ai (context manager)", ai_call, n)

    print("\nMongoDB driver listeners (always on, once per command)")
    bench("CommandMetricsListener.succeeded",
          lambda: command_listener.succeeded(_FakeCommandEvent()), n)
    bench("PoolMetricsListener.connection_checked_out",
          lambda: pool_listener.connection_checked_out(None), n)

    print("\nScrape cost (paid by the scraper, never by a request)")
    for extra in (0, 200, 500):
        metrics.reset_for_tests()
        for i in range(extra):
            metrics.provider_requests_total.inc(labels=("market_data", f"op{i}", "ok"))
        started = time.perf_counter()
        for _ in range(20):
            document = metrics.registry.render_prometheus()
        elapsed_ms = (time.perf_counter() - started) / 20 * 1000
        print(f"  render_prometheus (+{extra:>3} series){'':<21}"
              f"{elapsed_ms:7.3f} ms   ({len(document):,} bytes)")

    print(
        "\nContext for the numbers above:\n"
        "  Mongo command   1-10 ms      HTTP request    5-15 ms\n"
        "  Provider call   0.1-5 s      AI call         1-30 s\n"
        "\nWebSocket fan-out is counted ONCE per broadcast, not per recipient,\n"
        "so its cost is constant in the number of connected users. See\n"
        "docs/architecture/OBSERVABILITY.md §4.3 and §10.\n"
        "\nSingle-threaded microbenchmarks: they cannot show lock contention\n"
        "under real concurrency. Use scripts/load/ against staging for that.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

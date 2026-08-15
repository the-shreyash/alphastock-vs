#!/usr/bin/env python3
"""Server-side resource snapshot for PH3.5 load runs.

WHY A SEPARATE PROBE
--------------------
k6 measures the client's view: how long a request took to come back. It cannot
see why. The brief (§7) also requires CPU, memory, MongoDB and Redis behaviour,
and every one of those lives on the server side.

Everything here is *read from instrumentation the application already has* —
`/api/metrics` (PH2.5), `/api/diagnostics/redis` (PH2.7), MongoDB's own
`serverStatus`. Nothing is estimated and nothing new is instrumented for the
occasion, which is what makes the numbers reproducible by anyone who runs the
same commands later.

TWO SNAPSHOTS, THEN A DELTA
---------------------------
Counters (`http_requests_total`, `redis_commands_total`, Mongo's `opcounters`)
are monotonic since process start. Reporting their absolute value after a run
would include every request the process served while warming up, seeding and
smoke-testing. So the runner takes a snapshot before and after, and `--delta`
subtracts them. Gauges (RSS, open FDs, pool occupancy) are reported as
before/after pairs instead, because a difference of two gauges is not a rate.

Histograms are reported as their cumulative buckets, from which the runner
derives server-side p50/p95/p99 — the server's own view of latency, independent
of k6's. When those two disagree the gap is queueing outside the application,
which is exactly the kind of finding a load test exists to produce.

USAGE
    python scripts/load_metrics_probe.py --out before.json
    python scripts/load_metrics_probe.py --out after.json
    python scripts/load_metrics_probe.py --delta before.json after.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

BASE_URL = os.environ.get("LOAD_BASE_URL", "http://127.0.0.1:8000")
METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")

#: Route templates whose server-side latency distribution is worth carrying into
#: the certification. Keeping this list short keeps the report readable; the raw
#: snapshot retains every series, so nothing is lost by not listing a route.
ROUTES_OF_INTEREST = (
    "/api/watchlist", "/api/portfolio", "/api/portfolio/summary",
    "/api/trades/active", "/api/notifications", "/api/settings",
    "/api/auth/login", "/api/chat", "/api/market/overview",
    "/api/admin/dashboard", "/api/paper/trade", "/api/trades/validate",
)


def _fetch(path: str, token_required: bool = True) -> Optional[dict]:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url)
    if token_required and METRICS_TOKEN:
        req.add_header("Authorization", f"Bearer {METRICS_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
        # Returned rather than raised: a probe that aborts the whole run because
        # one optional endpoint was unreachable is a probe that gets removed
        # from the runner. The absent key is itself the signal.
        return {"_error": f"{type(e).__name__}: {e}"}


def _mongo_status() -> Dict[str, Any]:
    """MongoDB `serverStatus`, reduced to the fields that matter under load.

    Uses pymongo directly rather than going through the application, because the
    question is what the *database* is doing, and asking the application would
    add the application's own behaviour to the answer.
    """
    try:
        from pymongo import MongoClient
    except ImportError:
        return {"_error": "pymongo not installed"}

    url = os.environ.get("MONGO_URL", "").strip()
    db_name = os.environ.get("DB_NAME", "").strip()
    if not url:
        return {"_error": "MONGO_URL unset"}

    try:
        client = MongoClient(url, serverSelectionTimeoutMS=3000)
        status = client.admin.command("serverStatus")
        out = {
            "connections": status.get("connections", {}),
            "opcounters": status.get("opcounters", {}),
            "network": {k: status.get("network", {}).get(k)
                        for k in ("bytesIn", "bytesOut", "numRequests")},
            "globalLock": {
                "currentQueue": status.get("globalLock", {}).get("currentQueue", {}),
                "activeClients": status.get("globalLock", {}).get("activeClients", {}),
            },
            "wiredTiger_concurrentTransactions":
                status.get("wiredTiger", {}).get("concurrentTransactions", {}),
            "uptime_seconds": status.get("uptime"),
        }
        if db_name:
            try:
                db_stats = client[db_name].command("dbStats")
                out["dbStats"] = {k: db_stats.get(k) for k in
                                  ("collections", "objects", "dataSize", "indexSize", "indexes")}
            except Exception as e:      # noqa: BLE001 - diagnostic best effort
                out["dbStats"] = {"_error": str(e)}
            # Slow operations currently running against the LOAD DATABASE.
            # Empty is the expected result; a non-empty list during a run is a
            # finding worth chasing.
            #
            # The namespace filter is essential rather than cosmetic. An
            # unfiltered `currentOp` returns the server's own housekeeping —
            # `admin.$cmd` cursors, replication awaits, and this probe's own
            # command — several of which legitimately sit "running" for
            # seconds. Reporting those as slow application queries would cry
            # wolf on every single run, and a signal that is always red is a
            # signal nobody reads.
            try:
                current = client.admin.command(
                    "currentOp", {"secs_running": {"$gte": 1}, "active": True})
                out["slow_ops"] = [
                    {"op": o.get("op"), "ns": o.get("ns"),
                     "secs": o.get("secs_running"), "plan": o.get("planSummary")}
                    for o in current.get("inprog", [])
                    if str(o.get("ns", "")).startswith(f"{db_name}.")
                ]
            except Exception as e:      # noqa: BLE001
                out["slow_ops"] = [{"_error": str(e)}]
        client.close()
        return out
    except Exception as e:              # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def snapshot() -> Dict[str, Any]:
    return {
        "captured_at": time.time(),
        "captured_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "metrics": _fetch("/api/metrics?format=json"),
        "diagnostics": _fetch("/api/diagnostics"),
        "redis": _fetch("/api/diagnostics/redis"),
        "health": _fetch("/api/health", token_required=False),
        "mongo": _mongo_status(),
        "market_mock": _fetch_plain(os.environ.get("MARKET_MOCK_CONTROL",
                                                   "http://127.0.0.1:9020/__control")),
        "ai_mock": _fetch_plain(os.environ.get("AI_MOCK_CONTROL",
                                               "http://127.0.0.1:9030/__control")),
    }


def _fetch_plain(url: str) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:              # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


# --------------------------------------------------------------------------- #
# Metric-series helpers                                                         #
# --------------------------------------------------------------------------- #
def _registry(snapshot_metrics: Optional[dict]) -> dict:
    """The metric registry out of a `/api/metrics?format=json` document.

    The endpoint wraps the registry under a `metrics` key alongside `service`
    and `environment`. Unwrapping here rather than at every call site means a
    future change to that envelope is one edit, and — the reason this function
    exists at all — a probe that silently reads the *envelope* as the registry
    finds no series and reports a clean `requests=0` for a run that served
    hundreds. It did exactly that on PH3.5's first instrumented run.
    """
    if not isinstance(snapshot_metrics, dict):
        return {}
    inner = snapshot_metrics.get("metrics")
    return inner if isinstance(inner, dict) else snapshot_metrics


def _series(metrics: dict, name: str) -> list:
    node = (metrics or {}).get(name)
    return node.get("series", []) if isinstance(node, dict) else []


def _sum(metrics: dict, name: str, label_filter: Optional[dict] = None) -> float:
    total = 0.0
    for s in _series(metrics, name):
        if label_filter and any(s["labels"].get(k) != v for k, v in label_filter.items()):
            continue
        total += s["value"]
    return total


def _gauge(metrics: dict, name: str) -> Optional[float]:
    rows = _series(metrics, name)
    return rows[0]["value"] if rows else None


def _histogram_quantiles(metrics: dict, name: str, route: str) -> Dict[str, Optional[float]]:
    """Approximate quantiles from cumulative histogram buckets.

    This is bucket interpolation, so the result is bounded by the bucket edges
    and is NOT as precise as k6's client-side percentile over raw samples. It is
    reported alongside k6's figure rather than instead of it, and the
    certification says which is which — quoting an interpolated p99 as if it
    were exact is the kind of small dishonesty that makes a whole report
    untrustworthy.
    """
    buckets = []
    count = 0.0
    total = 0.0
    for s in _series(metrics, name):
        if s["labels"].get("route") != route:
            continue
        if s["sample"].endswith("_bucket"):
            le = s["labels"].get("le")
            buckets.append((float("inf") if le == "+Inf" else float(le), s["value"]))
        elif s["sample"].endswith("_count"):
            count += s["value"]
        elif s["sample"].endswith("_sum"):
            total += s["value"]

    if not buckets or count <= 0:
        return {"count": count or 0, "mean": None, "p50": None, "p95": None, "p99": None}

    buckets.sort()

    def q(p: float) -> Optional[float]:
        target = p * count
        prev_edge, prev_cum = 0.0, 0.0
        for edge, cum in buckets:
            if cum >= target:
                if edge == float("inf"):
                    return prev_edge          # the open bucket has no upper edge to report
                if cum == prev_cum:
                    return edge
                frac = (target - prev_cum) / (cum - prev_cum)
                return prev_edge + frac * (edge - prev_edge)
            prev_edge, prev_cum = edge, cum
        return None

    return {"count": count, "mean": total / count if count else None,
            "p50": q(0.50), "p95": q(0.95), "p99": q(0.99)}


def delta(before: dict, after: dict) -> Dict[str, Any]:
    bm = _registry(before.get("metrics"))
    am = _registry(after.get("metrics"))
    elapsed = max(0.001, after["captured_at"] - before["captured_at"])
    if not am:
        raise SystemExit(
            "the 'after' snapshot contains no metric series — /api/metrics was "
            "unreachable or token-rejected. Refusing to emit a delta that would "
            "read as a clean zero."
        )

    requests = _sum(am, "http_requests_total") - _sum(bm, "http_requests_total")
    errors_5xx = _sum(am, "http_request_errors_total", {"kind": "server"}) - \
        _sum(bm, "http_request_errors_total", {"kind": "server"})
    errors_4xx = _sum(am, "http_request_errors_total", {"kind": "client"}) - \
        _sum(bm, "http_request_errors_total", {"kind": "client"})
    exceptions = _sum(am, "http_request_errors_total", {"kind": "exception"}) - \
        _sum(bm, "http_request_errors_total", {"kind": "exception"})

    def mongo_delta(field: str) -> Optional[float]:
        b = ((before.get("mongo") or {}).get("opcounters") or {}).get(field)
        a = ((after.get("mongo") or {}).get("opcounters") or {}).get(field)
        return (a - b) if (isinstance(a, (int, float)) and isinstance(b, (int, float))) else None

    def redis_delta(field: str) -> Optional[float]:
        b = ((before.get("redis") or {}).get("connection") or {}).get(field)
        a = ((after.get("redis") or {}).get("connection") or {}).get(field)
        return (a - b) if (isinstance(a, (int, float)) and isinstance(b, (int, float))) else None

    out: Dict[str, Any] = {
        "elapsed_seconds": round(elapsed, 2),
        "http": {
            "requests": requests,
            "requests_per_second": round(requests / elapsed, 2),
            "errors_5xx": errors_5xx,
            "errors_4xx": errors_4xx,
            "exceptions": exceptions,
            "error_rate_5xx": round(errors_5xx / requests, 6) if requests else None,
            "in_flight_after": _gauge(am, "http_requests_in_flight"),
        },
        "process": {
            "rss_bytes_before": _gauge(bm, "process_resident_memory_bytes"),
            "rss_bytes_after": _gauge(am, "process_resident_memory_bytes"),
            "open_fds_before": _gauge(bm, "process_open_fds"),
            "open_fds_after": _gauge(am, "process_open_fds"),
            "uptime_seconds_after": _gauge(am, "app_uptime_seconds"),
        },
        "mongo": {
            "query": mongo_delta("query"),
            "insert": mongo_delta("insert"),
            "update": mongo_delta("update"),
            "delete": mongo_delta("delete"),
            "command": mongo_delta("command"),
            "connections_before": (before.get("mongo") or {}).get("connections"),
            "connections_after": (after.get("mongo") or {}).get("connections"),
            "queue_after": ((after.get("mongo") or {}).get("globalLock") or {}).get("currentQueue"),
            "slow_ops_after": (after.get("mongo") or {}).get("slow_ops"),
        },
        "redis": {
            "commands": redis_delta("commands_total"),
            "failures": redis_delta("failures_total"),
            "connection_errors": redis_delta("connection_errors_total"),
            "circuit_opens": redis_delta("circuit_opens_total"),
            "circuit_state_after": ((after.get("redis") or {}).get("connection") or {}).get("circuit_state"),
            "pool_after": ((after.get("redis") or {}).get("connection") or {}).get("pool"),
            "server_memory_used_bytes_after": _gauge(am, "redis_server_memory_used_bytes"),
            "server_connected_clients_after": _gauge(am, "redis_server_connected_clients"),
            "server_evicted_keys_after": _gauge(am, "redis_server_evicted_keys_total"),
            "pubsub_reconnects": _sum(am, "redis_pubsub_reconnects_total") -
                                 _sum(bm, "redis_pubsub_reconnects_total"),
        },
        "providers": {
            "market_mock_requests": _mock_delta(before, after, "market_mock", "requests"),
            "ai_mock_requests": _mock_delta(before, after, "ai_mock", "requests"),
            "ai_mock_max_concurrent": (after.get("ai_mock") or {}).get("max_concurrent"),
        },
        "server_side_latency": {},
    }

    for route in ROUTES_OF_INTEREST:
        q = _histogram_quantiles(am, "http_request_duration_seconds", route)
        qb = _histogram_quantiles(bm, "http_request_duration_seconds", route)
        # Cumulative histograms mean these quantiles cover the process's whole
        # life, not just the run. Reported with the request delta beside them so
        # a reader can see how much of the distribution the run contributed.
        if q["count"]:
            out["server_side_latency"][route] = {
                "requests_in_window": q["count"] - (qb["count"] or 0),
                "p50_ms": round(q["p50"] * 1000, 2) if q["p50"] is not None else None,
                "p95_ms": round(q["p95"] * 1000, 2) if q["p95"] is not None else None,
                "p99_ms": round(q["p99"] * 1000, 2) if q["p99"] is not None else None,
                "note": "cumulative since process start; interpolated from buckets",
            }

    return out


def _mock_delta(before: dict, after: dict, key: str, field: str):
    b = (before.get(key) or {}).get(field)
    a = (after.get(key) or {}).get(field)
    return (a - b) if (isinstance(a, (int, float)) and isinstance(b, (int, float))) else None


def _fmt(value, scale: float = 1.0, unit: str = "", digits: int = 0) -> str:
    """Render a possibly-absent number. `n/a` rather than `0` when a probe could
    not read a value — the two mean very different things during triage."""
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{value / scale:.{digits}f}{unit}"
    return str(value)


def render_delta(d: Dict[str, Any]) -> str:
    """A human-readable delta for the runner's console output.

    Lives here rather than as a shell one-liner in `load-test.sh` because the
    shell version has to escape every quote inside an f-string, which is both
    unreadable and — as PH3.5 found on its first run — a syntax error on Python
    3.11.
    """
    h, p, m, r, v = d["http"], d["process"], d["mongo"], d["redis"], d["providers"]
    lines = [
        f"  requests={_fmt(h['requests'])} rps={_fmt(h['requests_per_second'], digits=2)} "
        f"5xx={_fmt(h['errors_5xx'])} 4xx={_fmt(h['errors_4xx'])} "
        f"exceptions={_fmt(h['exceptions'])}",
        f"  rss {_fmt(p['rss_bytes_before'], 1e6, ' MB', 1)} -> "
        f"{_fmt(p['rss_bytes_after'], 1e6, ' MB', 1)}   "
        f"fds {_fmt(p['open_fds_before'])} -> {_fmt(p['open_fds_after'])}",
        f"  mongo query={_fmt(m['query'])} update={_fmt(m['update'])} "
        f"insert={_fmt(m['insert'])} command={_fmt(m['command'])}",
        f"  mongo conns={m['connections_after']} queue={m['queue_after']}",
        f"  redis commands={_fmt(r['commands'])} failures={_fmt(r['failures'])} "
        f"circuit_opens={_fmt(r['circuit_opens'])} state={r['circuit_state_after']} "
        f"pool={r['pool_after']}",
        f"  provider calls: market={_fmt(v['market_mock_requests'])} "
        f"ai={_fmt(v['ai_mock_requests'])} ai_max_concurrent={_fmt(v['ai_mock_max_concurrent'])}",
    ]
    slow = m.get("slow_ops_after")
    if slow:
        lines.append(f"  MONGO SLOW OPS: {slow}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="PH3.5 server-side metric snapshot / delta.")
    ap.add_argument("--out", help="write a snapshot to this path")
    ap.add_argument("--delta", nargs=2, metavar=("BEFORE", "AFTER"),
                    help="print the delta between two snapshots")
    ap.add_argument("--summary", action="store_true",
                    help="with --delta, print the human-readable form instead of JSON")
    args = ap.parse_args()

    if args.delta:
        with open(args.delta[0]) as f:
            before = json.load(f)
        with open(args.delta[1]) as f:
            after = json.load(f)
        computed = delta(before, after)
        if args.summary:
            print(render_delta(computed))
        else:
            json.dump(computed, sys.stdout, indent=2)
            sys.stdout.write("\n")
        return 0

    snap = snapshot()
    if args.out:
        with open(args.out, "w") as f:
            json.dump(snap, f, indent=2)
        print(f"[probe] snapshot → {args.out}")
    else:
        json.dump(snap, sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

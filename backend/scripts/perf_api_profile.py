#!/usr/bin/env python3
"""Profile the API surface for query count, documents read and payload size (PH3.4).

WHAT THIS ANSWERS THAT `perf_db_benchmark.py` DOES NOT
------------------------------------------------------
The database benchmark measures one query at a time and asks whether an index
serves it. This script measures a whole **request** and asks how many queries it
issued at all. Those are different failures with different fixes: a perfectly
indexed query executed 101 times in a loop is fast in `explain` and slow in
production, and no amount of indexing repairs it.

It runs against the hermetic stack (`FakeDB`, blank credentials, network guard),
so the wall-clock column describes this machine and nothing else — see
`tests/_perf.py` for why that is stated rather than quietly reported. The
columns that do transfer to production are:

  q     — database round trips per request. Multiply by real RTT to get the
          latency this endpoint pays on any deployment.
  docs  — documents the handler caused the store to look at.
  bytes — response size, which the user pays for regardless of server speed.

USAGE
-----
    cd backend && python scripts/perf_api_profile.py
    cd backend && python scripts/perf_api_profile.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _netguard, _testenv  # noqa: E402

_testenv.apply()


from bson import ObjectId  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from server import app, create_access_token  # noqa: E402
from services.broker_engine import broker_engine  # noqa: E402
from tests._fakedb import FakeDB  # noqa: E402
from tests._perf import measure  # noqa: E402


class _ImmediateMonkeypatch:
    """The two-method subset of pytest's `monkeypatch` that `_netguard` uses.

    `_netguard.install` is written against the pytest fixture because that is
    where it is normally used; reimplementing its socket patching here would mean
    this script guarded the network slightly differently from the test suite, and
    the difference would be found the hard way. Nothing is undone — the process
    exits at the end of the run.
    """

    def setattr(self, target, name, value):  # noqa: A003 - mirrors monkeypatch
        setattr(target, name, value)

    def delattr(self, target, name, raising=True):  # noqa: A003
        try:
            delattr(target, name)
        except AttributeError:
            if raising:
                raise


#: Per-user corpus. Small on purpose: this script is looking for a query count
#: that scales with the data (the N+1 signature), and N+1 is visible at 25 rows.
#: A larger corpus would only make the wall-clock column — the one that already
#: does not transfer — look worse.
TRADES = 25
NOTIFS = 20
WATCHLIST = 12
HOLDINGS = 8
AUDIT_LOGS = 30


def build_corpus():
    """A fake DB seeded with one user who has used the product."""
    fdb = FakeDB()
    uid = ObjectId()
    user = {
        "_id": uid, "name": "Perf User", "email": "perf@example.invalid",
        "capital": 100000.0, "risk_level": "moderate", "role": "super_admin",
    }
    fdb.users.docs.append(user)
    suid = str(uid)

    for i in range(TRADES):
        closed = i % 3 != 0
        fdb.trades.docs.append({
            "_id": ObjectId(), "user_id": suid, "symbol": "RELIANCE",
            "direction": "LONG", "quantity": 10, "quantity_open": 0 if closed else 10,
            "entry_price": 1000.0 + i, "status": "CLOSED" if closed else "OPEN",
            "entry_time": f"2026-0{1 + i % 8}-01T09:15:00+00:00",
            "exit_time": f"2026-0{1 + i % 8}-02T15:15:00+00:00" if closed else None,
            "pnl": 100.0 * (1 if i % 2 else -1) if closed else None,
            # `target1`, not `target_1`. The application's field name has no
            # underscore (models.py:131). An early version of this corpus used the
            # underscored form and produced a KeyError from
            # /api/portfolio/intelligence that looked exactly like an application
            # defect and was purely a seeding mistake — recorded here so the next
            # reader does not re-file it.
            "setup": "breakout", "stop_loss": 950.0, "target1": 1100.0,
            "target2": 1200.0, "target3": 1300.0,
        })
    for i in range(NOTIFS):
        fdb.notifications.docs.append({
            "_id": ObjectId(), "user_id": suid, "title": f"Alert {i}",
            "message": "Body.", "read": i % 4 != 0,
            "created_at": f"2026-08-{1 + i % 28:02d}T10:00:00+00:00",
        })
    for i in range(WATCHLIST):
        fdb.watchlist.docs.append({
            "_id": ObjectId(), "user_id": suid, "symbol": f"SYM{i}",
            "added_at": f"2026-07-{1 + i:02d}T10:00:00+00:00",
        })
    for i in range(HOLDINGS):
        fdb.holdings.docs.append({
            "_id": ObjectId(), "user_id": suid, "broker": "zerodha",
            "symbol": f"HLD{i}", "quantity": 10, "avg_price": 500.0 + i,
        })
    for i in range(AUDIT_LOGS):
        fdb.admin_audit_logs.docs.append({
            "_id": ObjectId(), "admin_id": suid, "action": "user.updated",
            "timestamp": f"2026-08-{1 + i % 28:02d}T10:00:00+00:00",
            "details": {"note": "corpus"},
        })
    for i in range(20):
        fdb.chat_messages.docs.append({
            "_id": ObjectId(), "user_id": suid, "session_id": "s-1",
            "role": "user" if i % 2 else "assistant", "content": "Hi.",
            "created_at": f"2026-08-{1 + i:02d}T10:00:00+00:00",
        })
    return fdb, user


#: (method, path). Chosen to cover the routes the brief's §7 prioritises and that
#: a real session actually hits: the dashboard's fan-out, the portfolio bundle,
#: the trade lists, the notification badge, the watchlist, and the admin pages
#: whose N+1s this script exists to find.
ENDPOINTS: List[tuple] = [
    ("GET", "/api/auth/me"),
    ("GET", "/api/trades"),
    ("GET", "/api/trades/active"),
    ("GET", "/api/trades/history"),
    ("GET", "/api/trades/pnl"),
    ("GET", "/api/trades/risk/summary"),
    ("GET", "/api/trades/coaching/summary"),
    ("GET", "/api/portfolio"),
    ("GET", "/api/portfolio/summary"),
    ("GET", "/api/portfolio/intelligence"),
    ("GET", "/api/portfolio/performance"),
    ("GET", "/api/notifications"),
    ("GET", "/api/notifications/unread-count"),
    ("GET", "/api/watchlist"),
    ("GET", "/api/journal/trades"),
    ("GET", "/api/journal/stats"),
    ("GET", "/api/settings"),
    ("GET", "/api/chat/history"),
    ("GET", "/api/ai/memory"),
    ("GET", "/api/ai/conversations"),
    ("GET", "/api/paper/account"),
    ("GET", "/api/paper/trades"),
    ("GET", "/api/brokers"),
    ("GET", "/api/orders"),
    ("GET", "/api/admin/dashboard"),
    ("GET", "/api/admin/users?page=1&limit=25"),
    ("GET", "/api/admin/logs?page=1&limit=25"),
    ("GET", "/api/admin/ai/usage"),
    ("GET", "/api/admin/analytics/revenue"),
    ("GET", "/api/admin/analytics/users"),
    ("GET", "/api/admin/support/tickets?page=1&limit=25"),
    ("GET", "/api/health/live"),
    ("GET", "/api/health/ready"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    ap.add_argument(
        "--offline", action="store_true",
        help="install the outbound-network guard, so market-data providers are "
             "unreachable and the timings describe application code alone",
    )
    args = ap.parse_args()

    # Attribution, and the whole reason this flag exists.
    #
    # Several endpoints enrich their response with live quotes. Run WITHOUT the
    # guard and their wall clock is dominated by Yahoo Finance — a real and
    # important number (PH3.4 §12) that says nothing about this codebase. Run
    # WITH the guard and the same endpoints report the cost of the application's
    # own work. Neither figure alone answers "which layer is slow?"; the
    # difference between the two runs does, which is what the brief's §7 asks for
    # before optimising anything.
    if args.offline:
        _netguard.install(_ImmediateMonkeypatch())

    fdb, user = build_corpus()
    server.db = fdb
    broker_engine.db = fdb

    token = create_access_token(str(user["_id"]), user["email"])
    headers = {"Authorization": f"Bearer {token}"}

    rows: List[Dict[str, Any]] = []
    mode = "OFFLINE (network guarded — application cost only)" if args.offline \
        else "ONLINE (real market-data providers reachable)"
    print(f"\nMode: {mode}\n")
    print(f"{'method':6} {'path':44} {'code':>4} {'q':>4} {'docs':>6} "
          f"{'bytes':>7} {'cold':>12} {'warm':>11}")
    print("-" * 108)

    with TestClient(app) as client:
        for method, path in ENDPOINTS:
            try:
                m = measure(client, method, path, headers=headers)
            except Exception as exc:  # a route the double cannot serve
                print(f"{method:6} {path:52}  --  ERROR {type(exc).__name__}: {exc}")
                rows.append({"method": method, "path": path, "error": repr(exc)})
                continue
            print(m.row())
            rows.append({
                "method": m.method, "path": m.path, "status": m.status,
                "queries": m.queries, "documents_examined": m.documents_examined,
                "response_bytes": m.response_bytes,
                "cold_ms": round(m.cold_seconds * 1000, 2),
                "warm_ms": round(m.warm_seconds * 1000, 2),
                "by_collection": m.log.by_collection, "by_op": m.log.by_op,
            })

    ok = [r for r in rows if r.get("queries") is not None]
    if ok:
        print("\nHighest query counts:")
        for r in sorted(ok, key=lambda r: -r["queries"])[:8]:
            print(f"  {r['queries']:4} queries  {r['path']:48} {r['by_collection']}")
        print("\nSlowest (warm, steady state):")
        for r in sorted(ok, key=lambda r: -r["warm_ms"])[:8]:
            print(f"  {r['warm_ms']:8.1f} ms warm ({r['cold_ms']:8.1f} cold)  {r['path']}")
        print("\nLargest payloads:")
        for r in sorted(ok, key=lambda r: -r["response_bytes"])[:8]:
            print(f"  {r['response_bytes']:7} bytes  {r['path']}")

    if args.json:
        args.json.write_text(json.dumps(
            {"mode": "offline" if args.offline else "online", "rows": rows},
            indent=2, default=str))
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

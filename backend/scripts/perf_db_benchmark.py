#!/usr/bin/env python3
"""Measure the real cost of the backend's hot query shapes (PH3.4).

WHY THIS SCRIPT EXISTS
----------------------
The hermetic test suite runs against an in-memory dictionary. That is the right
double for correctness — but every query costs the same there, so it cannot tell
a query served by an index from one that reads the entire collection. Those two
are indistinguishable at the 4 documents a test seeds and are the difference
between a page that loads and a page that times out at production volume.

MongoDB will answer the question directly, and exactly, via `explain`. This
script asks it: it seeds a realistic corpus into a scratch database, runs the
**actual filter and sort shapes taken from `server.py` and `services/`**, and
records the query plan MongoDB chose along with how many documents it had to
examine to return how many.

`docsExamined / nReturned` is the number that matters. At 1.0 the index is doing
the work. At 40,000 / 25 the database is reading the whole collection and
throwing away 99.9% of it, and that ratio grows linearly with every user who
signs up — which is why this cannot be deferred to a load test: PH3.5 would
measure the symptom under traffic, and the cause is visible right here with one
user's worth of requests.

METHODOLOGY
-----------
* Two passes over the identical corpus: **before** any application index exists,
  then **after** `server.py`'s `ensure_indexes()` has run. The delta is the
  measurement; a single pass would only prove that indexes exist.
* Wall-clock is reported as the **minimum** of N runs. Every sample is the true
  cost plus non-negative interference from whatever else is on this machine, so
  the minimum is the least-contaminated estimate. Plans and document counts are
  exact and do not need the trick.
* The corpus is deliberately modest (see `SCALE`). The point is not to find the
  volume at which the system breaks — that is PH3.5's job — but to expose the
  *plan*, which a COLLSCAN reveals at any size.

SAFETY
------
Writes only to `PERF_DB_NAME`, refuses to run if that resolves to the
application's configured `DB_NAME`, and drops the scratch database on the way
out. It never reads or writes application data.

USAGE
-----
    cd backend && python scripts/perf_db_benchmark.py            # before/after
    cd backend && python scripts/perf_db_benchmark.py --json out.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

#: Scratch database. Unmistakably named so that finding it on a server tells the
#: finder what it is and that it is safe to drop.
PERF_DB_NAME = "stockassist_perf_ph34"

#: Corpus size. Chosen to be (a) large enough that a collection scan is clearly
#: distinguishable from an index seek in both the plan and the wall clock, and
#: (b) small enough to seed in a few seconds so the script stays runnable in a
#: sprint loop. Per-user counts are what a moderately active account looks like.
SCALE = {
    "users": 400,
    "trades_per_user": 60,
    "notifications_per_user": 40,
    "watchlist_per_user": 25,
    "holdings_per_user": 12,
    "chat_messages_per_user": 30,
    "audit_logs": 20_000,
}

REPEATS = 5

#: The index set that existed on the collections below **before PH3.4**, copied
#: from `server.py`'s startup handler at commit 528b77e. This is the honest
#: baseline: "before" must mean what production actually has today, not "no
#: indexes at all", or every measured improvement is inflated by the indexes the
#: application already had. Collections absent from this mapping had no index
#: (beyond the implicit `_id_`) at all — which is itself the finding for
#: `watchlist`, `holdings`, `orders` and `payments`.
PRE_PH34_INDEXES: Dict[str, List[Any]] = {
    "users": [("email", {"unique": True})],
    "trades": [("user_id", {})],
    "notifications": [("user_id", {})],
    "chat_messages": [([("user_id", 1), ("session_id", 1)], {})],
    "admin_audit_logs": [("timestamp", {}), ("admin_id", {}), ("action", {})],
}

_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL",
    "ITC", "LT", "AXISBANK", "KOTAKBANK", "MARUTI", "SUNPHARMA", "TITAN",
    "WIPRO", "TATAMOTORS", "ADANIENT", "BAJFINANCE", "HCLTECH", "NESTLEIND",
]


# --------------------------------------------------------------------------- #
# The query shapes under measurement                                            #
# --------------------------------------------------------------------------- #
# Each entry is a real query from the application, cited to its source line so a
# reader can confirm the shape was not invented for a flattering result. `sort`
# and `limit` are included because a sort the index cannot serve is its own cost
# (Mongo materializes and sorts in memory, and aborts past 100 MB) and is
# invisible if you only look at the filter.
QUERIES: List[Dict[str, Any]] = [
    {
        "id": "trades.list",
        "source": "server.py:2043  GET /api/trades",
        "collection": "trades",
        "filter": {"user_id": "$USER"},
        "sort": [("entry_time", -1)],
        "limit": 100,
    },
    {
        "id": "trades.active",
        "source": "server.py:2054  GET /api/trades/active",
        "collection": "trades",
        "filter": {"user_id": "$USER", "status": "OPEN"},
        "sort": [("entry_time", -1)],
        "limit": 50,
    },
    {
        "id": "trades.history",
        "source": "server.py:2068  GET /api/trades/history",
        "collection": "trades",
        "filter": {"user_id": "$USER", "status": {"$ne": "OPEN"}},
        "sort": [("exit_time", -1)],
        "limit": 100,
    },
    {
        "id": "trades.pnl",
        "source": "server.py:2075  GET /api/trades/pnl",
        "collection": "trades",
        "filter": {"user_id": "$USER"},
        "sort": None,
        "limit": 500,
    },
    {
        "id": "notifications.list",
        "source": "server.py:2510  GET /api/notifications",
        "collection": "notifications",
        "filter": {"user_id": "$USER"},
        "sort": [("created_at", -1)],
        "limit": 50,
    },
    {
        "id": "notifications.unread_count",
        "source": "server.py:2506  GET /api/notifications/unread-count",
        "collection": "notifications",
        "filter": {"user_id": "$USER", "read": False},
        "sort": None,
        "limit": 0,
        "count_only": True,
    },
    {
        "id": "watchlist.list",
        "source": "server.py:4485  GET /api/watchlist",
        "collection": "watchlist",
        "filter": {"user_id": "$USER"},
        "sort": [("added_at", -1)],
        "limit": 100,
    },
    {
        "id": "watchlist.exists",
        "source": "server.py:4512  POST /api/watchlist (duplicate check)",
        "collection": "watchlist",
        "filter": {"user_id": "$USER", "symbol": "RELIANCE"},
        "sort": None,
        "limit": 1,
    },
    {
        "id": "holdings.by_user",
        "source": "services/portfolio_engine.py:58  every /api/portfolio* route",
        "collection": "holdings",
        "filter": {"user_id": "$USER"},
        "sort": None,
        "limit": 500,
    },
    {
        "id": "holdings.by_user_broker",
        "source": "services/portfolio_stream.py:198, trade_stream.py:193",
        "collection": "holdings",
        "filter": {"user_id": "$USER", "broker": "zerodha"},
        "sort": None,
        "limit": 500,
    },
    {
        "id": "orders.by_user",
        "source": "server.py:3769  GET /api/orders",
        "collection": "orders",
        "filter": {"user_id": "$USER"},
        "sort": [("placed_at", -1)],
        "limit": 200,
    },
    {
        "id": "admin_audit_logs.page",
        "source": "server.py:5234  GET /api/admin/logs",
        "collection": "admin_audit_logs",
        "filter": {},
        "sort": [("timestamp", -1)],
        "limit": 50,
    },
    {
        "id": "chat.history_session",
        "source": "server.py:2570  GET /api/chat/history?session_id=...",
        "collection": "chat_messages",
        "filter": {"user_id": "$USER", "session_id": "$SESSION"},
        "sort": [("created_at", 1)],
        "limit": 100,
    },
    {
        "id": "chat.history_all",
        "source": "server.py:2570  GET /api/chat/history (no session_id)",
        "collection": "chat_messages",
        "filter": {"user_id": "$USER"},
        "sort": [("created_at", 1)],
        "limit": 100,
    },
    {
        # The hot one. This runs inside POST /api/chat, on every message a user
        # sends to the AI, and it filters on `session_id` ALONE. The pre-PH3.4
        # index was `{user_id, session_id}`, whose prefix is `user_id` — a
        # compound index cannot serve a query that does not constrain its leading
        # field, so this was a full collection scan on the AI chat path.
        "id": "chat.session_turns",
        "source": "server.py:488  POST /api/chat (conversation continuity)",
        "collection": "chat_messages",
        "filter": {"session_id": "$SESSION"},
        "sort": [("created_at", -1)],
        "limit": 10,
    },
]


# --------------------------------------------------------------------------- #
# Seeding                                                                       #
# --------------------------------------------------------------------------- #
async def seed(db) -> Dict[str, Any]:
    """Insert the corpus. Returns the identifiers the queries are run against."""
    rng = random.Random(20260814)  # fixed seed: the corpus must be reproducible
    now = datetime.now(timezone.utc)

    user_ids = [ObjectId() for _ in range(SCALE["users"])]
    await db.users.insert_many([
        {
            "_id": uid,
            "name": f"Perf User {i}",
            "email": f"perf{i}@example.invalid",
            "capital": 100_000.0,
            "risk_level": "moderate",
            "role": "user",
            "created_at": (now - timedelta(days=rng.randint(0, 700))).isoformat(),
        }
        for i, uid in enumerate(user_ids)
    ])

    def iso(days_ago: float) -> str:
        return (now - timedelta(days=days_ago)).isoformat()

    trades, notifs, watch, holds, chats = [], [], [], [], []
    # One session per user, not one shared session. A shared session_id would
    # make the `{session_id}` query return every user's messages, which would
    # both overstate the pre-index cost and understate the post-index win — the
    # corpus has to have the selectivity the real data has.
    def session_for(suid: str) -> str:
        return f"chat-{suid}"

    for uid in user_ids:
        suid = str(uid)
        for j in range(SCALE["trades_per_user"]):
            closed = j % 3 != 0
            trades.append({
                "user_id": suid,
                "symbol": rng.choice(_SYMBOLS),
                "direction": rng.choice(["LONG", "SHORT"]),
                "quantity": rng.randint(1, 200),
                "quantity_open": 0 if closed else rng.randint(1, 200),
                "entry_price": round(rng.uniform(100, 4000), 2),
                "status": "CLOSED" if closed else "OPEN",
                "entry_time": iso(rng.uniform(0, 700)),
                "exit_time": iso(rng.uniform(0, 700)) if closed else None,
                "pnl": round(rng.uniform(-5000, 8000), 2) if closed else None,
            })
        for j in range(SCALE["notifications_per_user"]):
            notifs.append({
                "user_id": suid,
                "title": f"Alert {j}",
                "message": "Perf corpus notification body.",
                "read": j % 4 != 0,
                "created_at": iso(rng.uniform(0, 200)),
            })
        for sym in rng.sample(_SYMBOLS, SCALE["watchlist_per_user"] % len(_SYMBOLS) or 5):
            watch.append({"user_id": suid, "symbol": sym, "added_at": iso(rng.uniform(0, 300))})
        for sym in rng.sample(_SYMBOLS, SCALE["holdings_per_user"]):
            holds.append({
                "user_id": suid, "broker": rng.choice(["zerodha", "upstox"]),
                "symbol": sym, "quantity": rng.randint(1, 500),
                "avg_price": round(rng.uniform(100, 4000), 2),
            })
        for j in range(SCALE["chat_messages_per_user"]):
            chats.append({
                "user_id": suid, "session_id": session_for(suid), "role": "user" if j % 2 else "assistant",
                "content": "Perf corpus chat turn.", "created_at": iso(rng.uniform(0, 100)),
            })

    async def bulk(col, docs):
        for i in range(0, len(docs), 5000):
            await col.insert_many(docs[i:i + 5000])

    await bulk(db.trades, trades)
    await bulk(db.notifications, notifs)
    await bulk(db.watchlist, watch)
    await bulk(db.holdings, holds)
    await bulk(db.chat_messages, chats)
    await bulk(db.orders, [
        {"user_id": str(rng.choice(user_ids)), "broker": "zerodha", "symbol": rng.choice(_SYMBOLS),
         "status": "COMPLETE", "placed_at": iso(rng.uniform(0, 300))}
        for _ in range(SCALE["users"] * 5)
    ])
    await bulk(db.admin_audit_logs, [
        {"admin_id": str(rng.choice(user_ids)), "action": rng.choice(
            ["user.blocked", "user.updated", "plan.granted", "flag.toggled"]),
         "timestamp": iso(rng.uniform(0, 400)), "details": {"note": "perf corpus"}}
        for _ in range(SCALE["audit_logs"])
    ])

    counts = {}
    for name in ("users", "trades", "notifications", "watchlist", "holdings",
                 "orders", "chat_messages", "admin_audit_logs"):
        counts[name] = await db[name].count_documents({})
    first = str(user_ids[0])
    return {"user_id": first, "session_id": session_for(first), "counts": counts}


# --------------------------------------------------------------------------- #
# Measurement                                                                   #
# --------------------------------------------------------------------------- #
def _resolve(spec, ctx):
    if isinstance(spec, dict):
        return {k: _resolve(v, ctx) for k, v in spec.items()}
    if spec == "$USER":
        return ctx["user_id"]
    if spec == "$SESSION":
        return ctx["session_id"]
    return spec


def _plan_summary(explain: dict) -> Dict[str, Any]:
    """Pull the three facts that matter out of explain's nested output.

    `winningPlan` is a tree; the leaf stage is what touched the data. Walking to
    the leaf rather than reading the top stage matters because the top is almost
    always `LIMIT` or `SORT`, which says nothing about whether an index was used.
    """
    stats = explain.get("executionStats", {})
    plan = stats.get("executionStages") or explain.get("queryPlanner", {}).get("winningPlan", {})

    stages: List[str] = []
    node = plan
    while isinstance(node, dict):
        stage = node.get("stage")
        if stage:
            stages.append(stage)
        node = node.get("inputStage") or (node.get("inputStages") or [None])[0]

    leaf = stages[-1] if stages else "?"
    return {
        "stages": stages,
        "leaf_stage": leaf,
        "index_used": leaf == "IXSCAN",
        "in_memory_sort": "SORT" in stages,
        "docs_examined": stats.get("totalDocsExamined"),
        "keys_examined": stats.get("totalKeysExamined"),
        "returned": stats.get("nReturned"),
        "execution_ms": stats.get("executionTimeMillis"),
    }


async def measure(db, ctx) -> List[Dict[str, Any]]:
    results = []
    for q in QUERIES:
        col = db[q["collection"]]
        flt = _resolve(q["filter"], ctx)

        if q.get("count_only"):
            cursor_explain = await db.command({
                "explain": {"count": q["collection"], "query": flt},
                "verbosity": "executionStats",
            })
        else:
            cursor = col.find(flt)
            if q.get("sort"):
                cursor = cursor.sort(q["sort"])
            if q.get("limit"):
                cursor = cursor.limit(q["limit"])
            cursor_explain = await cursor.explain()

        plan = _plan_summary(cursor_explain)

        best = None
        for _ in range(REPEATS):
            start = time.perf_counter()
            if q.get("count_only"):
                await col.count_documents(flt)
            else:
                cursor = col.find(flt)
                if q.get("sort"):
                    cursor = cursor.sort(q["sort"])
                if q.get("limit"):
                    cursor = cursor.limit(q["limit"])
                await cursor.to_list(q.get("limit") or 500)
            elapsed = (time.perf_counter() - start) * 1000
            best = elapsed if best is None else min(best, elapsed)

        results.append({
            "id": q["id"], "source": q["source"], "collection": q["collection"],
            "wall_ms": round(best, 2), **plan,
        })
    return results


def render(label: str, rows: List[Dict[str, Any]]) -> str:
    out = [f"\n{'=' * 118}", f"{label}", "=" * 118,
           f"{'query':30} {'leaf stage':12} {'examined':>9} {'returned':>9} "
           f"{'ratio':>8} {'memsort':>8} {'mongo ms':>9} {'wall ms':>8}"]
    for r in rows:
        ex, ret = r["docs_examined"], r["returned"]
        ratio = f"{ex / ret:.1f}x" if ex is not None and ret else ("-" if ex is None else "inf")
        out.append(
            f"{r['id']:30} {r['leaf_stage']:12} {str(ex):>9} {str(ret):>9} "
            f"{ratio:>8} {('YES' if r['in_memory_sort'] else '-'):>8} "
            f"{str(r['execution_ms']):>9} {r['wall_ms']:>8.2f}"
        )
    return "\n".join(out)


def render_delta(before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> str:
    """The before/after table the certification quotes.

    Reports documents examined rather than milliseconds as the headline, because
    documents examined is the quantity that scales with the corpus while the
    milliseconds on this machine do not describe any deployment.
    """
    by_id = {r["id"]: r for r in before}
    out = [f"\n{'=' * 118}", "DELTA — pre-PH3.4 index set vs PH3.4 index set", "=" * 118,
           f"{'query':30} {'plan before':>14} {'plan after':>14} "
           f"{'docs before':>12} {'docs after':>11} {'reduction':>10} {'memsort':>16}"]
    for a in after:
        b = by_id[a["id"]]
        db_, da = b["docs_examined"], a["docs_examined"]
        if db_ and da:
            red = f"{db_ / da:.1f}x" if da else "-"
        elif db_ == da:
            red = "none"
        else:
            red = "-"
        sort = ("fixed" if b["in_memory_sort"] and not a["in_memory_sort"]
                else "still in-memory" if a["in_memory_sort"] else "n/a")
        out.append(
            f"{a['id']:30} {b['leaf_stage']:>14} {a['leaf_stage']:>14} "
            f"{str(db_):>12} {str(da):>11} {red:>10} {sort:>16}"
        )
    return "\n".join(out)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, help="write the full result set here")
    ap.add_argument("--keep", action="store_true", help="do not drop the scratch database")
    args = ap.parse_args()

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    app_db_name = os.environ.get("DB_NAME", "")
    if PERF_DB_NAME == app_db_name:
        print(f"REFUSING: scratch name {PERF_DB_NAME!r} equals DB_NAME.", file=sys.stderr)
        return 2

    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=3000)
    try:
        await client.admin.command("ping")
    except Exception as exc:
        print(f"No MongoDB at {mongo_url}: {exc}", file=sys.stderr)
        return 3

    db = client[PERF_DB_NAME]
    try:
        await client.drop_database(PERF_DB_NAME)
        print(f"Seeding {PERF_DB_NAME} ...")
        t0 = time.perf_counter()
        ctx = await seed(db)
        print(f"Seeded in {time.perf_counter() - t0:.1f}s: "
              + ", ".join(f"{k}={v}" for k, v in ctx["counts"].items()))

        unindexed = await measure(db, ctx)
        print(render("REFERENCE — no indexes at all (not the baseline; shows what an "
                     "index is worth here)", unindexed))

        for collection, specs in PRE_PH34_INDEXES.items():
            for keys, opts in specs:
                await db[collection].create_index(keys, **opts)
        before = await measure(db, ctx)
        print(render("BEFORE — the pre-PH3.4 index set (what production has today)", before))

        # Apply exactly what the application applies, by calling the application's
        # own routine. Re-declaring the index list here would let this script
        # measure indexes production does not have.
        #
        # `_testenv.apply()` first, for the same reason `tests/conftest.py` does:
        # importing `server` runs `validate_config()` at module scope, which
        # aborts on a missing JWT_SECRET. Reusing the test suite's synthetic
        # environment is better than inventing a second one here — this script
        # then reads configuration identically to every test, and cannot pick up
        # the developer's real credentials while doing it.
        from tests import _testenv
        _testenv.apply()
        import server
        real_db = server.db
        server.db = db
        try:
            await server.ensure_indexes()
        finally:
            server.db = real_db
        idx = {c: await db[c].index_information() for c in sorted(ctx["counts"])}
        print("\nIndexes now present (from server.ensure_indexes()):")
        for c, info in idx.items():
            names = [n for n in info if n != "_id_"]
            print(f"  {c:22} {names if names else '— none —'}")

        after = await measure(db, ctx)
        print(render("AFTER — server.ensure_indexes() applied (PH3.4)", after))

        print(render_delta(before, after))

        if args.json:
            args.json.write_text(json.dumps(
                {"scale": SCALE, "counts": ctx["counts"], "indexes": {
                    c: [n for n in i if n != "_id_"] for c, i in idx.items()},
                 "unindexed": unindexed, "before": before, "after": after}, indent=2, default=str))
            print(f"\nWrote {args.json}")
        return 0
    finally:
        if not args.keep:
            await client.drop_database(PERF_DB_NAME)
            print(f"Dropped {PERF_DB_NAME}.")
        client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

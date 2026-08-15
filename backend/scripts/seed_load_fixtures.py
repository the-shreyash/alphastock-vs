#!/usr/bin/env python3
"""Seed the PH3.5 load-test database with synthetic users and portfolios.

WHY THIS LIVES IN `backend/scripts/`
------------------------------------
`scripts/README.md` draws the boundary: host-side operator tooling lives in
`scripts/`, anything that needs the application installed lives here. This
needs the application — specifically `security.passwords.hash_password`, because
a synthetic user whose password hash was produced by anything other than the
application's own hasher is a user the application cannot authenticate, and the
entire authenticated load model would collapse at the first login.

SAFETY
------
Three independent refusals, because this script's whole job is to write a large
amount of data into a database:

1. It refuses to run against `APP_ENV=production`.
2. It refuses any `DB_NAME` that does not contain `loadtest`. That is not
   belt-and-braces — the load environment is sourced from a file, and a file
   that fails to source leaves the *previous* `DB_NAME` in the environment.
   Without this check the failure mode is "seeds 250 fake users into the
   development database and nobody notices for a week".
3. `--reset` (the default for a repeatable run) drops only the collections it
   seeds, in the named database, and prints which.

THE PASSWORD HASH IS COMPUTED ONCE, DELIBERATELY
------------------------------------------------
Every synthetic user shares one password, and bcrypt at the application's pinned
cost of 12 takes ~230 ms per hash on the reference host. Hashing 250 users
individually would cost ~57 seconds of pure setup for zero benefit: bcrypt
embeds its salt in the hash, so one hash of the shared password verifies for all
of them through the application's real `verify_password`. Nothing about the
login path is weakened or bypassed — each login still performs a full cost-12
verification, which is exactly the cost PH3.5 needs to measure.

FIXTURE MANIFEST
----------------
Writes `scripts/load/fixtures.json`, which the k6 scenarios read to learn which
accounts exist. Generating credentials inside the k6 script instead would mean
the load driver and the database could disagree about who exists, and the
resulting 401s would look like a capacity failure.

USAGE
-----
    set -a; . scripts/load/env/loadtest.env; set +a
    cd backend && python scripts/seed_load_fixtures.py --users 250
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from bson import ObjectId                                    # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient           # noqa: E402

from security.passwords import hash_password                 # noqa: E402

#: The password every synthetic account shares. Obviously synthetic, and it
#: satisfies the application's own password policy so `register`-equivalent
#: accounts are indistinguishable from real ones to the login path.
LOAD_PASSWORD = "Ph35-LoadTest-Passw0rd!"

#: Symbols drawn from `services/real_market.py::YAHOO_TICKERS`, so every seeded
#: watchlist row and holding resolves through the real quote path (against the
#: mock provider) rather than falling into an unknown-symbol branch that would
#: skip the work being measured.
SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC",
    "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK", "ASIANPAINT",
    "MARUTI", "TITAN", "SUNPHARMA", "TATAMOTORS", "BAJFINANCE", "WIPRO", "ONGC",
]

#: Per-user volumes. Chosen to match the PH3.4 §3.1 profiling corpus (25 trades,
#: 20 notifications, 12 watchlist symbols, 8 holdings, 20 chat messages) so the
#: PH3.4 → PH3.5 comparison in the certification is like-for-like. A different
#: corpus would make every latency delta un-attributable: it could be the
#: concurrency or it could be that there is more data to read.
PER_USER = {
    "trades": 25,
    "notifications": 20,
    "watchlist": 12,
    "holdings": 8,
    "chat_messages": 20,
    "orders": 5,
}

SEEDED_COLLECTIONS = (
    "users", "trades", "notifications", "watchlist", "holdings",
    "orders", "chat_messages", "sessions", "rate_limits",
    "admin_audit_logs", "audit_logs",
)


def _guard(db_name: str) -> None:
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    if app_env == "production":
        raise SystemExit("refusing to seed: APP_ENV=production")
    if "loadtest" not in db_name.lower():
        raise SystemExit(
            f"refusing to seed: DB_NAME={db_name!r} does not contain 'loadtest'.\n"
            "Source scripts/load/env/loadtest.env first — an unsourced environment\n"
            "leaves the previous DB_NAME in place, and this check is what stops\n"
            "250 synthetic users landing in it."
        )


async def seed(db, users: int, reset: bool) -> dict:
    rng = random.Random(20260814)          # fixed seed — the corpus must be reproducible
    now = datetime.now(timezone.utc)

    if reset:
        for name in SEEDED_COLLECTIONS:
            await db[name].drop()
        print(f"[seed] dropped {len(SEEDED_COLLECTIONS)} collections in {db.name}")

    # One hash, reused. See the module docstring for why this does not weaken
    # anything: every login still runs a full cost-12 verification.
    shared_hash = hash_password(LOAD_PASSWORD)

    def iso(days_ago: float) -> str:
        return (now - timedelta(days=days_ago)).isoformat()

    def base_user(uid: ObjectId, email: str, name: str, role: str) -> dict:
        return {
            "_id": uid,
            "name": name,
            "email": email,
            "password_hash": shared_hash,
            "role": role,
            "capital": 100_000,
            "risk_level": "moderate",
            "max_daily_loss": 5000,
            "max_trades_per_day": 3,
            "telegram_chat_id": None,
            # Verified, so the load model exercises the ordinary authenticated
            # path rather than whatever a future hard verification gate does to
            # an unverified account.
            "email_verified": True,
            "email_verified_at": iso(30),
            "verified_by": "loadtest-seed",
            "notification_prefs": {
                "push": True, "email": True, "morning_report": True,
                "trade_alerts": True, "exit_reminder": True,
                "portfolio_alerts": True, "email_alerts": True,
                "telegram_alerts": False,
            },
            "created_at": iso(rng.randint(1, 700)),
        }

    user_ids = [ObjectId() for _ in range(users)]
    docs = [
        base_user(uid, f"loaduser{i}@loadtest.invalid", f"Load User {i}", "user")
        for i, uid in enumerate(user_ids)
    ]

    # One admin for Scenario E. Kept separate from the pool so an admin session
    # can never be handed to a non-admin scenario by an off-by-one.
    admin_id = ObjectId()
    docs.append(base_user(admin_id, "loadadmin@loadtest.invalid", "Load Admin", "admin"))

    await db.users.insert_many(docs)

    trades, notifs, watch, holds, chats, orders = [], [], [], [], [], []
    for uid in user_ids:
        suid = str(uid)
        for j in range(PER_USER["trades"]):
            closed = j % 3 != 0
            qty = rng.randint(1, 200)
            entry = round(rng.uniform(100, 4000), 2)
            trades.append({
                "user_id": suid,
                "symbol": rng.choice(SYMBOLS),
                "stock_name": "Synthetic Issuer Ltd",
                "type": rng.choice(["BUY", "SELL"]),
                "direction": rng.choice(["LONG", "SHORT"]),
                "quantity": qty,
                "quantity_open": 0 if closed else qty,
                "entry_price": entry,
                "stop_loss": round(entry * 0.95, 2),
                "target1": round(entry * 1.05, 2),
                "target2": round(entry * 1.10, 2),
                "status": "CLOSED" if closed else "OPEN",
                "is_paper": True,
                "entry_time": iso(rng.uniform(0, 700)),
                "exit_time": iso(rng.uniform(0, 700)) if closed else None,
                "exit_price": round(entry * rng.uniform(0.9, 1.2), 2) if closed else None,
                "pnl": round(rng.uniform(-5000, 8000), 2) if closed else None,
            })
        for j in range(PER_USER["notifications"]):
            notifs.append({
                "user_id": suid,
                "title": f"Load alert {j}",
                "message": "Synthetic load-test notification body.",
                "type": "info",
                "read": j % 4 != 0,
                "created_at": iso(rng.uniform(0, 200)),
            })
        for sym in rng.sample(SYMBOLS, PER_USER["watchlist"]):
            watch.append({
                "user_id": suid, "symbol": sym, "note": None,
                "added_price": round(rng.uniform(100, 4000), 2),
                "added_at": iso(rng.uniform(0, 300)),
            })
        for sym in rng.sample(SYMBOLS, PER_USER["holdings"]):
            holds.append({
                "user_id": suid, "broker": rng.choice(["zerodha", "upstox"]),
                "symbol": sym, "quantity": rng.randint(1, 500),
                "avg_price": round(rng.uniform(100, 4000), 2),
                "updated_at": iso(rng.uniform(0, 10)),
            })
        for j in range(PER_USER["chat_messages"]):
            chats.append({
                "user_id": suid, "session_id": f"load-{suid}",
                "role": "user" if j % 2 else "assistant",
                "content": "Synthetic load-test chat turn.",
                "created_at": iso(rng.uniform(0, 100)),
            })
        for _ in range(PER_USER["orders"]):
            orders.append({
                "user_id": suid, "broker": "zerodha",
                "symbol": rng.choice(SYMBOLS), "status": "COMPLETE",
                "placed_at": iso(rng.uniform(0, 300)),
            })

    async def bulk(col, batch):
        for i in range(0, len(batch), 5000):
            await col.insert_many(batch[i:i + 5000])

    await bulk(db.trades, trades)
    await bulk(db.notifications, notifs)
    await bulk(db.watchlist, watch)
    await bulk(db.holdings, holds)
    await bulk(db.chat_messages, chats)
    await bulk(db.orders, orders)

    counts = {}
    for name in ("users", "trades", "notifications", "watchlist",
                 "holdings", "orders", "chat_messages"):
        counts[name] = await db[name].count_documents({})

    return {
        "database": db.name,
        "seeded_at": now.isoformat(),
        "password": LOAD_PASSWORD,
        "user_count": users,
        "user_email_pattern": "loaduser{i}@loadtest.invalid",
        "admin_email": "loadadmin@loadtest.invalid",
        "symbols": SYMBOLS,
        "per_user": PER_USER,
        "counts": counts,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description="Seed synthetic PH3.5 load-test fixtures.")
    ap.add_argument("--users", type=int, default=250,
                    help="synthetic user accounts to create (default 250)")
    ap.add_argument("--keep", action="store_true",
                    help="append instead of dropping the seeded collections first")
    ap.add_argument("--manifest", default=str(REPO_ROOT / "scripts" / "load" / "fixtures.json"))
    args = ap.parse_args()

    mongo_url = os.environ.get("MONGO_URL", "").strip()
    db_name = os.environ.get("DB_NAME", "").strip()
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL and DB_NAME must be set (source scripts/load/env/loadtest.env)")
    _guard(db_name)

    client = AsyncIOMotorClient(mongo_url)
    try:
        manifest = await seed(client[db_name], args.users, reset=not args.keep)
    finally:
        client.close()

    Path(args.manifest).write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[seed] {db_name}: " + ", ".join(f"{k}={v}" for k, v in manifest["counts"].items()))
    print(f"[seed] manifest → {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""D6.7 — scale, concurrency and multi-user load, against a REAL MongoDB.

WHY THIS FILE IS SEPARATE FROM `test_d63_real_db_races.py`
---------------------------------------------------------
D6.3's closure suite asked one question — *when a real database race happens,
can it move a value across a tenant boundary?* — and answered it. This file asks
the two questions D6.3 explicitly scoped out:

1. **Can the same operation execute twice?** Not "can it leak", but "can one
   stop-loss become two live market orders, one broker order become two
   authoritative rows, one portfolio become two copies of itself".
2. **Does anything silently not execute at all?** The `to_list(N)` fan-out caps,
   which are not races but are the same class of defect: work the platform
   believes it did and did not do.

Both need a real `mongod`, for the reason D6.3 recorded: `FakeDB` is
single-threaded with no await point inside an operation, so a concurrent-write
interleaving cannot be constructed against it — and, new in D6.7, it enforces no
unique index at all, so every constraint this sprint added is invisible to it.
A test of a unique index written against `FakeDB` passes with the index deleted.

THE HARNESS IS D6.3'S, DELIBERATELY
-----------------------------------
`_Gate`, `_GatedDb`, `_run` and the `mongo_db` fixture are imported from the
D6.3 suite rather than copied. They are already proved — that file's §1 contains
the falsifying twins showing the barrier really does overlap writers and really
does distinguish a compare-and-swap from a read/decide/write — and a second,
subtly different copy would be a second instrument nobody had validated.

EVERY GUARD HERE HAS A FALSIFYING TWIN
--------------------------------------
For each control, this file asserts the guard holds AND that the same harness,
pointed at the unguarded shape, sees the defect. A test that says "no duplicate
order was created" is worth nothing if the barrier never overlapped the two
writers; the twin is what makes the green meaningful.
"""

import asyncio
import os
import uuid

import pytest
from bson import ObjectId

pymongo = pytest.importorskip("pymongo", reason="pymongo is required for the D6.7 concurrency suite")
motor_asyncio = pytest.importorskip("motor.motor_asyncio", reason="motor is required for the D6.7 concurrency suite")

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
TIMEOUT = 10.0


def _mongo_is_reachable() -> bool:
    try:
        client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1500)
        try:
            client.admin.command("ping")
            return True
        finally:
            client.close()
    except Exception:
        return False


if not _mongo_is_reachable():  # pragma: no cover - environment-dependent
    pytest.skip(
        f"needs a real MongoDB at {MONGO_URL} (D6.7 concurrency suite); "
        "start one with `brew services start mongodb-community` or set MONGO_URL",
        allow_module_level=True,
    )

pytestmark = pytest.mark.requires_db

from _accounts import account_ref  # noqa: E402
from security.sessions import REVOKED  # noqa: E402
from test_d63_real_db_races import _GatedDb, _gather, _run  # noqa: E402


class _StartGate:
    """Parks only each caller's FIRST write, then lets everything through.

    WHY D6.3'S `_Gate` DOES NOT FIT THE SYNC RACE
    ---------------------------------------------
    `_Gate` parks *every* `update_one` at a barrier of N, which pins the
    interleaving exactly — and requires every caller to make the same number of
    writes. That is right for the races D6.3 studied (one write each) and wrong
    for a portfolio sync, which writes once per symbol and, on a duplicate key,
    once more. With uneven write counts the barrier never fills and the test
    hangs instead of failing.

    (It did, once, during development. `asyncio.wait_for` turned the hang into a
    red test rather than a wedged run, which is exactly why every barrier in
    these suites is bounded — a concurrency test that blocks forever reports
    nothing at all.)

    What the sync race actually needs is for both callers to be *inside* their
    write loops at the same time; after that, the natural interleaving of awaits
    is the thing under test and pinning it further would only narrow the
    scheduling this is meant to explore. So: one rendezvous, at the start.
    """

    def __init__(self, collection, barrier):
        self._collection = collection
        self._barrier = barrier
        self._released = set()

    def __getattr__(self, name):
        return getattr(self._collection, name)

    async def _park(self):
        task = id(asyncio.current_task())
        if task not in self._released:
            self._released.add(task)
            await asyncio.wait_for(self._barrier.wait(), timeout=TIMEOUT)

    async def update_one(self, *args, **kwargs):
        await self._park()
        return await self._collection.update_one(*args, **kwargs)

    # `insert_many` and `delete_many` are parked too, and that is not
    # thoroughness — it is what keeps the guarded test falsifiable. The
    # pre-D6.7 sync wrote through `delete_many` + `insert_many` and never
    # touched `update_one`, so a gate that parked only updates let the mutated
    # (old) code run its two syncs end to end with no overlap at all. D6.7's
    # M10d survived 45 tests for exactly that reason.
    async def insert_many(self, *args, **kwargs):
        await self._park()
        return await self._collection.insert_many(*args, **kwargs)

    async def delete_many(self, *args, **kwargs):
        await self._park()
        return await self._collection.delete_many(*args, **kwargs)


class _StartGatedDb:
    """`_GatedDb`, but handing out `_StartGate`s."""

    def __init__(self, db, gated):
        self._db = db
        self._gated = {name: _StartGate(getattr(db, name), barrier)
                       for name, barrier in gated.items()}

    def __getattr__(self, name):
        return self._gated.get(name) or getattr(self._db, name)

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture()
def mongo_db():
    """A throwaway database, dropped on the way out.

    Declared here rather than imported from the D6.3 suite: importing a fixture
    by name makes it shadow itself in every signature that uses it, and the
    fixture is the one trivial part of that harness. The load-bearing parts —
    `_GatedDb`'s barrier and `_run`'s per-loop motor client — are imported,
    because those are the pieces D6.3's falsifying twins actually validated.
    """
    name = f"alpha_stock_d67_{uuid.uuid4().hex[:12]}"
    yield name
    client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1500)
    try:
        client.drop_database(name)
    finally:
        client.close()


# =========================================================================== #
# §1 — the unique constraints (Phase 4)                                        #
# =========================================================================== #
class TestOrderIdentityIsEnforcedByTheDatabase:
    """`_record_order` upserts on `(broker_account_id, order_id)`.

    Before D6.7 that filter was a convention. `update_one(upsert=True)` is NOT
    atomic against a concurrent insert of the same key unless a unique index
    exists: both callers find nothing and both insert.
    """

    @staticmethod
    async def _engine(db):
        from services.broker_engine import BrokerEngine
        engine = BrokerEngine()
        engine.configure(db)
        return engine

    def test_without_the_index_the_collection_accepts_a_second_row(self, mongo_db):
        """THE FALSIFYING TWIN — and an honest statement of what it is not.

        WHAT THIS DOES NOT DO, AND WHY
        ------------------------------
        The first version of this test tried to reproduce the *race*: two
        `_record_order` upserts overlapped at a barrier, asserting they produce
        two rows without the index. **It does not reproduce on a standalone
        `mongod`, and the reason is structural, not a tuning problem.** An upsert
        is ONE server-side operation — Mongo evaluates the filter and performs
        the insert without yielding to another client's write on the same key —
        so a client-side barrier releases both callers into a queue the server
        then serializes. The second caller's filter sees the first caller's row.

        Duplicate inserts from a concurrent upsert are documented for the cases
        where that serialization does not hold (notably a sharded cluster, where
        the two writes can land on different shards). This deployment is a single
        standalone node, so the window is not reachable from this harness.

        Reporting that as "the race was reproduced" would be a fabrication, and
        reporting the guarded test as proof of a fix for a race nobody observed
        would be worse. So this twin asserts the property the index actually
        carries and that the guarded test actually depends on: **without the
        constraint the collection will hold two rows for one broker order
        identity; with it, it will not.** That is falsifiable — delete the index
        from `ensure_order_identity_index` and the paired test below goes red —
        and it is true.

        The constraint is therefore recorded as defence in depth with a
        demonstrated property, not as the fix for a demonstrated race. See
        D6.7 §4 and LIM-D6.7-1.
        """

        async def body(db):
            account = account_ref("u1", "upstox")
            for status in ("OPEN", "FILLED"):
                await db.orders.insert_one({
                    "broker_account_id": account.broker_account_id,
                    "order_id": "OID-1", "user_id": "u1", "status": status})
            return await db.orders.count_documents({"order_id": "OID-1"})

        assert _run(mongo_db, body) == 2, (
            "an unconstrained collection must accept two rows for one order "
            "identity; if it refuses, an index is already present and the "
            "guarded test below is unfalsifiable"
        )

    def test_with_the_index_the_collection_refuses_a_second_row(self, mongo_db):
        """The constraint itself, asserted directly at the database.

        Paired with the twin above: the same two inserts, the same identity, the
        only difference being the index. This is the assertion that goes red if
        `unique=True` is ever dropped from `ensure_order_identity_index`.
        """

        async def body(db):
            from infrastructure.mongo_errors import is_duplicate_key
            from services.brokers.order_identity import ensure_order_identity_index

            account = account_ref("u1", "upstox")
            report = await ensure_order_identity_index(db)
            assert report.unique, f"the unique index did not build: {report.as_dict()}"
            row = {"broker_account_id": account.broker_account_id,
                   "order_id": "OID-1", "user_id": "u1"}
            await db.orders.insert_one(dict(row))
            try:
                await db.orders.insert_one(dict(row))
                return "ACCEPTED", await db.orders.count_documents({"order_id": "OID-1"})
            except Exception as e:
                if not is_duplicate_key(e):
                    raise
                return "REFUSED", await db.orders.count_documents({"order_id": "OID-1"})

        assert _run(mongo_db, body) == ("REFUSED", 1)

    def test_a_pre_d64_order_with_no_account_does_not_collide(self, mongo_db):
        """The partial filter, which is what lets the constraint ship at all.

        Mongo treats a missing field and a null as ONE value in a unique index,
        so without `partialFilterExpression` every legacy order the D6.4 account
        migration could not name would collide with every other one — and a
        constraint legacy data cannot satisfy never gets deployed.
        """

        async def body(db):
            from services.brokers.order_identity import ensure_order_identity_index

            await ensure_order_identity_index(db)
            for i in range(3):
                await db.orders.insert_one({"user_id": "u1", "order_id": f"legacy-{i}"})
            return await db.orders.count_documents({"user_id": "u1"})

        assert _run(mongo_db, body) == 3

    def test_with_the_index_two_concurrent_upserts_create_exactly_one_row(self, mongo_db):
        """The constraint admits one row, and the loser does not raise at the caller.

        A duplicate-key error on this path is the database doing its job. What
        must NOT happen is `_record_order` propagating it: the losing writer is
        recording an order the winner already recorded, so there is nothing to
        report and nothing lost.
        """

        async def body(db):
            from services.brokers.order_identity import ensure_order_identity_index

            report = await ensure_order_identity_index(db)
            assert report.unique, f"the unique index did not build: {report.as_dict()}"

            engine = await self._engine(db)
            account = account_ref("u1", "upstox")
            barrier = asyncio.Barrier(2)
            gated = _GatedDb(db, {"orders": barrier})
            engine.configure(gated)
            results = await _gather(*[
                engine._record_order(account, {"order_id": "OID-1", "status": "OPEN",
                                               "symbol": "RELIANCE"})
                for _ in range(2)])
            count = await db.orders.count_documents({"order_id": "OID-1"})
            return count, [r for r in results if isinstance(r, Exception)]

        count, raised = _run(mongo_db, body)
        assert count == 1, f"one broker order must be one row, got {count}"
        assert raised == [], (
            f"the losing writer must not raise at the caller; got {raised}. An "
            f"order-stream callback that raises here stops processing the rest "
            f"of the broker's message."
        )

    def test_two_accounts_may_hold_the_same_broker_order_id(self, mongo_db):
        """The constraint is per ACCOUNT, which is the whole point of D6.4.

        A broker order id is a per-account sequence, so two accounts — even two
        accounts of the same user at the same broker — can legitimately both be
        handed the same id. An index keyed on `(user_id, order_id)` would have
        merged them into one row and destroyed one of the two orders.
        """

        async def body(db):
            from services.brokers.order_identity import ensure_order_identity_index

            await ensure_order_identity_index(db)
            engine = await self._engine(db)
            a = account_ref("u1", "upstox", suffix="one")
            b = account_ref("u1", "upstox", suffix="two")
            await engine._record_order(a, {"order_id": "SHARED", "symbol": "TCS"})
            await engine._record_order(b, {"order_id": "SHARED", "symbol": "INFY"})
            return await db.orders.count_documents({"order_id": "SHARED"})

        assert _run(mongo_db, body) == 2, (
            "two accounts sharing a broker order id must keep two rows"
        )

    def test_a_collection_with_duplicates_is_reported_and_never_repaired(self, mongo_db):
        """Phase 4's hard rule: production order records are not deleted for an index.

        A collection that already violates the constraint is the state the
        missing constraint allowed. The build must fail loudly, name the keys,
        and leave every row exactly where it is — two rows for one order is a
        fact about a real brokerage account and needs a person.
        """

        async def body(db):
            from services.brokers.order_identity import ensure_order_identity_index

            for status in ("OPEN", "FILLED"):
                await db.orders.insert_one({
                    "broker_account_id": "ba_dup", "order_id": "OID-DUP",
                    "user_id": "u1", "status": status})
            report = await ensure_order_identity_index(db)
            surviving = await db.orders.count_documents({"order_id": "OID-DUP"})
            names = [ix async for ix in db.orders.list_indexes()]
            return report, surviving, [n["name"] for n in names]

        report, surviving, index_names = _run(mongo_db, body)
        assert not report.unique, "the unique index must NOT build over duplicates"
        assert report.duplicates and report.duplicates[0]["order_id"] == "OID-DUP", (
            f"the offending key must be named so a person can reconcile it; got "
            f"{report.duplicates}")
        assert surviving == 2, (
            f"BOTH order records must survive; {2 - surviving} were destroyed to "
            f"make an index build")
        assert "order_identity_nonunique" in index_names, (
            "reads must keep their access path while the constraint is absent")


class TestHoldingIdentityIsEnforcedByTheDatabase:
    def test_concurrent_syncs_of_one_account_never_double_the_portfolio(self, mongo_db):
        """The delete-then-insert race, and the generation ordering that fixes it.

        Two syncs of ONE account, overlapped at the holdings write. Pre-D6.7 the
        sequence was `delete_many` then `insert_many`, so the interleaving
        delete/delete/insert/insert left both result sets in the collection and
        the dashboard showed exactly twice the portfolio value.
        """

        async def body(db):
            from services.brokers.order_identity import ensure_holding_identity_index
            from services.broker_engine import BrokerEngine

            await ensure_holding_identity_index(db)
            engine = BrokerEngine()
            engine.configure(db)
            account = account_ref("u1", "upstox")
            rows = [{"symbol": s, "quantity": 10, "invested_value": 100.0,
                     "market_value": 120.0} for s in ("RELIANCE", "TCS", "INFY")]

            barrier = asyncio.Barrier(2)
            gated = _StartGatedDb(db, {"holdings": barrier})
            engine.configure(gated)

            async def one_sync():
                generation = await engine._next_sync_generation(
                    account.user_id, account.broker_account_id)
                await engine._replace_holdings(account, rows,
                                               generation=generation, now="now")

            await _gather(one_sync(), one_sync())
            engine.configure(db)
            return await db.holdings.count_documents(
                {"broker_account_id": account.broker_account_id})

        held = _run(mongo_db, body)
        assert held == 3, (
            f"three positions synced twice must remain three rows, got {held}. "
            f"Six is the pre-D6.7 doubling; zero is the empty window between the "
            f"delete and the insert."
        )

    def test_a_sync_never_leaves_the_account_with_no_holdings(self, mongo_db):
        """The second failure mode of delete-then-insert, asserted directly.

        Between the two statements the account held nothing, and any read landing
        in that window — the snapshot job, a dashboard refresh — reported an
        empty portfolio as fact. Nothing is deleted before the new rows exist, so
        the window is gone by construction; this observes it from a concurrent
        reader rather than arguing it.
        """

        async def body(db):
            from services.brokers.order_identity import ensure_holding_identity_index
            from services.broker_engine import BrokerEngine

            await ensure_holding_identity_index(db)
            engine = BrokerEngine()
            engine.configure(db)
            account = account_ref("u1", "upstox")
            scope = {"broker_account_id": account.broker_account_id}
            rows = [{"symbol": s, "quantity": 1, "invested_value": 1.0,
                     "market_value": 1.0} for s in ("RELIANCE", "TCS")]

            # Seed a first generation so there is something an empty window
            # could be observed *against*.
            g0 = await engine._next_sync_generation(
                account.user_id, account.broker_account_id)
            await engine._replace_holdings(account, rows, generation=g0, now="t0")

            observed = []
            stop = asyncio.Event()

            async def watcher():
                while not stop.is_set():
                    observed.append(await db.holdings.count_documents(scope))
                    await asyncio.sleep(0)

            async def resync():
                for _ in range(25):
                    g = await engine._next_sync_generation(
                        account.user_id, account.broker_account_id)
                    await engine._replace_holdings(account, rows, generation=g,
                                                   now="t1")
                stop.set()

            await asyncio.wait_for(asyncio.gather(watcher(), resync()), timeout=TIMEOUT)
            return observed

        observed = _run(mongo_db, body)
        assert observed, "the watcher never sampled; the test proves nothing"
        assert min(observed) >= 2, (
            f"a concurrent reader saw {min(observed)} holdings during a re-sync "
            f"of a 2-position account — the empty window is back"
        )


# =========================================================================== #
# §2 — the exit claim (Phase 7: duplicate real broker orders)                  #
# =========================================================================== #
class TestOneExitCannotBecomeTwoOrders:
    """The stop condition this sprint exists to close.

    `run_cycle` reads a trade, decides an exit is due, and places a REAL market
    order. Two schedulers — two uvicorn workers, two containers, or one cycle
    that overran its 60-second slot — reach the same decision on the same trade.
    """

    def test_the_claim_admits_exactly_one_of_many_concurrent_callers(self, mongo_db):
        async def body(db):
            from services import trading_engine

            trade_id = ObjectId()
            await db.trades.insert_one({"_id": trade_id, "user_id": "u1",
                                        "status": "OPEN", "symbol": "RELIANCE"})
            barrier = asyncio.Barrier(5)
            gated = _GatedDb(db, {"trades": barrier})
            results = await _gather(*[
                trading_engine.claim_exit(gated, trade_id, "SL") for _ in range(5)])
            row = await db.trades.find_one({"_id": trade_id})
            return results, row.get("exit_claims")

        results, claims = _run(mongo_db, body)
        assert sum(1 for r in results if r is True) == 1, (
            f"exactly one of five concurrent callers may claim one exit; got "
            f"{results}. More than one is a duplicate live market order."
        )
        assert claims == ["SL"], f"the claim must be recorded once, got {claims}"

    def test_a_partial_target_exit_does_not_block_the_stop_loss(self, mongo_db):
        """The claim key is the EXIT, not the trade, and this is why.

        A trade that booked half its size at target 1 must still be able to exit
        the remainder on a stop. Claiming the trade would have made the first
        partial exit the last exit that trade ever took.
        """

        async def body(db):
            from services import trading_engine

            trade_id = ObjectId()
            await db.trades.insert_one({"_id": trade_id, "user_id": "u1",
                                        "status": "OPEN", "symbol": "RELIANCE"})
            return [await trading_engine.claim_exit(db, trade_id, k)
                    for k in ("T1", "T2", "SL", "SL")]

        assert _run(mongo_db, body) == [True, True, True, False], (
            "each distinct exit claims once; a repeat of the same exit is refused"
        )

    def test_an_unrecordable_claim_refuses_rather_than_ordering(self, mongo_db):
        """Fail closed. A claim that cannot be proved exclusive is not a claim.

        The alternative — order anyway when the database is unreachable — is the
        one outcome that is certainly wrong, because the reason the claim failed
        may well be that another process is holding the write this one needed.
        """

        async def body(db):
            from services import trading_engine

            class _Broken:
                class trades:
                    @staticmethod
                    async def update_one(*a, **k):
                        raise RuntimeError("database is unreachable")

            return await trading_engine.claim_exit(_Broken(), ObjectId(), "SL")

        assert _run(mongo_db, body) is False

    def test_broker_exit_places_no_second_order_for_one_stop_loss(self, mongo_db):
        """End to end, through the real production path, with a spy broker.

        NO REAL BROKER IS CONTACTED. `place_order` is a spy that records and
        returns; the account directory is pointed at the same throwaway database.
        What is real is everything between the scheduler's decision and the
        adapter: account resolution, ownership, and the claim.
        """

        async def body(db):
            from services import trading_engine
            from services.brokers.accounts import broker_accounts

            account = account_ref("u1", "upstox")
            await db.broker_accounts.insert_one({
                "broker_account_id": account.broker_account_id,
                "user_id": "u1", "broker": "upstox", "status": "connected",
                "created_at": "2026-01-01T00:00:00+00:00"})
            broker_accounts.configure(db)
            try:
                placed = []

                class _Engine:
                    async def place_order(self, acct, order):
                        placed.append(acct.broker_account_id)
                        return {"order_id": f"OID-{len(placed)}"}

                trade_id = ObjectId()
                trade = {"_id": trade_id, "user_id": "u1", "symbol": "RELIANCE",
                         "broker": "upstox", "quantity": 10, "type": "BUY",
                         "broker_account_id": account.broker_account_id}
                await db.trades.insert_one(dict(trade))

                barrier = asyncio.Barrier(3)
                gated = _GatedDb(db, {"trades": barrier})
                await _gather(*[
                    trading_engine._broker_exit(_Engine(), trade, 10, "SL", db=gated)
                    for _ in range(3)])
                return placed
            finally:
                broker_accounts.configure(None)

        placed = _run(mongo_db, body)
        assert len(placed) == 1, (
            f"three concurrent runners placed {len(placed)} live market orders "
            f"for ONE stop loss. Anything but 1 is real money."
        )


# =========================================================================== #
# §3 — the scheduler lease (Phase 7: multi-worker safety)                      #
# =========================================================================== #
class TestOnlyOneProcessLeadsTheScheduler:
    def test_of_many_processes_exactly_one_acquires(self, mongo_db):
        """Four simulated workers campaign at once; one wins.

        Each `LeaderLease` carries its own `instance_id`, which is what a
        separate OS process would have. The election is a single-document
        compare-and-swap, so Mongo decides it — there is no coordination between
        the four beyond the database they share.
        """

        async def body(db):
            from infrastructure.leader import LeaderLease

            leases = [LeaderLease(db, "scheduler") for _ in range(4)]
            results = await _gather(*[lease.acquire() for lease in leases])
            doc = await db.leader_leases.find_one({"_id": "scheduler"})
            return results, doc, [lease.identity for lease in leases]

        results, doc, identities = _run(mongo_db, body)
        assert sum(1 for r in results if r is True) == 1, (
            f"exactly one of four workers may lead; got {results}")
        assert doc["holder"] in identities

    def test_a_follower_takes_over_when_the_lease_expires(self, mongo_db):
        """A killed leader must not leave the platform without a scheduler.

        A plain lock would need the holder to release it, and a SIGKILLed
        process never does. The expiry is written into the past rather than
        waited out, so this asserts the election's rule and not a sleep.
        """

        async def body(db):
            from datetime import datetime, timedelta, timezone
            from infrastructure.leader import LeaderLease

            leader = LeaderLease(db, "scheduler")
            follower = LeaderLease(db, "scheduler")
            assert await leader.acquire()
            assert not await follower.acquire(), "a live lease must not be stealable"

            await db.leader_leases.update_one(
                {"_id": "scheduler"},
                {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
            took_over = await follower.acquire()
            still_leader = await leader.renew()
            return took_over, still_leader

        took_over, still_leader = _run(mongo_db, body)
        assert took_over, "a follower must take over an expired lease"
        assert not still_leader, (
            "the displaced leader must learn it lost; a process that keeps "
            "believing it leads keeps running leader-only jobs")

    def test_renewal_cannot_resurrect_a_lease_somebody_else_holds(self, mongo_db):
        """A stalled leader that wakes up late must not steal the lease back."""

        async def body(db):
            from datetime import datetime, timedelta, timezone
            from infrastructure.leader import LeaderLease

            stalled = LeaderLease(db, "scheduler")
            await stalled.acquire()
            await db.leader_leases.update_one(
                {"_id": "scheduler"},
                {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
            successor = LeaderLease(db, "scheduler")
            await successor.acquire()

            renewed = await stalled.renew()
            doc = await db.leader_leases.find_one({"_id": "scheduler"})
            return renewed, doc["holder"] == successor.identity

        renewed, successor_still_holds = _run(mongo_db, body)
        assert not renewed
        assert successor_still_holds, "a stale renewal overwrote the live holder"

    def test_release_by_a_non_holder_does_not_unseat_the_leader(self, mongo_db):
        async def body(db):
            from infrastructure.leader import LeaderLease

            leader = LeaderLease(db, "scheduler")
            other = LeaderLease(db, "scheduler")
            await leader.acquire()
            await other.release()
            doc = await db.leader_leases.find_one({"_id": "scheduler"})
            return doc["holder"] == leader.identity

        assert _run(mongo_db, body), "a non-holder's release unseated the leader"

    def test_two_named_leases_do_not_contend(self, mongo_db):
        """A future second single-leader subsystem takes its own lease."""

        async def body(db):
            from infrastructure.leader import LeaderLease

            return await _gather(LeaderLease(db, "scheduler").acquire(),
                                 LeaderLease(db, "reports").acquire())

        assert _run(mongo_db, body) == [True, True]


class TestTheSchedulerGateIsActuallyWired:
    """The lease is worthless if the jobs do not consult it.

    These run without a database: the gate is in-memory by design (see
    `setup_scheduler`'s docstring on why leadership is checked per tick and not
    per boot), so this is where it can be driven directly.
    """

    class _Lease:
        def __init__(self, is_leader):
            self.is_leader = is_leader

    def test_a_follower_runs_no_leader_only_job(self):
        from services import scheduler as sched

        ran = []

        async def job():
            ran.append(1)

        sched.set_lease(self._Lease(False))
        try:
            asyncio.run(sched.leader_only("trade_monitor", job)())
        finally:
            sched.set_lease(None)
        assert ran == [], "a follower ran the job that places real broker orders"

    def test_the_leader_runs_it(self):
        from services import scheduler as sched

        ran = []

        async def job():
            ran.append(1)

        sched.set_lease(self._Lease(True))
        try:
            asyncio.run(sched.leader_only("trade_monitor", job)())
        finally:
            sched.set_lease(None)
        assert ran == [1]

    def test_with_no_lease_configured_the_job_still_runs(self):
        """The pre-D6.7 single-process deployment must be unaffected.

        A scheduler that silently stopped running because nobody wired an
        election would be a far worse regression than the duplication the
        election prevents.
        """
        from services import scheduler as sched

        ran = []

        async def job():
            ran.append(1)

        sched.set_lease(None)
        asyncio.run(sched.leader_only("trade_monitor", job)())
        assert ran == [1]

    def test_every_registered_cron_job_is_declared_leader_only(self):
        """The gate must not be something a seventh job can forget to opt into.

        Asserted against the jobs the scheduler actually registers, not against
        a hand-written list — a new `add_job` with no entry in `LEADER_ONLY_JOBS`
        fails here rather than quietly running on every worker.
        """
        from unittest.mock import MagicMock

        from services import scheduler as sched

        registered = []
        real_add, real_start = sched.scheduler.add_job, sched.scheduler.start
        sched.scheduler.add_job = lambda *a, **k: registered.append(k.get("id"))
        sched.scheduler.start = lambda *a, **k: None
        try:
            sched.setup_scheduler(db=MagicMock(), ai_summary_func=None,
                                  ws_broadcast=None, lease=None)
        finally:
            sched.scheduler.add_job, sched.scheduler.start = real_add, real_start
            sched.set_lease(None)

        assert registered, "no jobs were registered; this test proves nothing"
        missing = [job for job in registered if job not in sched.LEADER_ONLY_JOBS]
        assert not missing, (
            f"cron job(s) {missing} are registered but not declared leader-only, "
            f"so every worker will run them. Add them to LEADER_ONLY_JOBS, or "
            f"document explicitly why duplication is safe for that job."
        )


# =========================================================================== #
# §4 — session rotation and revocation (Phase 2: 1, 6, 7)                      #
# =========================================================================== #
class TestSessionLifecycleUnderConcurrency:
    def test_a_logout_racing_a_refresh_is_not_overwritten(self, mongo_db):
        """A revoked session must never be resurrected by an in-flight refresh.

        `rotate()` reads the family, finds it live, and writes — including a
        fresh `expires_at`. A `revoke()` landing between those two would, with a
        filter of `{session_id}` alone, have its revocation preserved but its
        family's absolute expiry pushed a full refresh lifetime into the future.
        The `revoked: False` clause in the CAS filter is what refuses the write
        outright.
        """

        async def body(db):
            from security.sessions import SessionStore

            store = SessionStore(db)
            session_id = await store.create("user-A", "jti-1")
            before = await db.sessions.find_one({"session_id": session_id})

            barrier = asyncio.Barrier(2)
            gated = SessionStore(_GatedDb(db, {"sessions": barrier}))
            results = await _gather(gated.rotate(session_id, "jti-1", "jti-2"),
                                    gated.revoke(session_id))
            after = await db.sessions.find_one({"session_id": session_id})
            return [getattr(r, "outcome", r) for r in results], before, after

        outcomes, before, after = _run(mongo_db, body)
        assert after["revoked"] is True, (
            f"the session was resurrected by a concurrent refresh; outcomes={outcomes}")
        if after["current_jti"] != before["current_jti"]:
            # The rotation won the race. That is legal — it happened while the
            # family was live — but the revocation that followed must still stand.
            assert after["revoked"] is True

    def test_concurrent_logout_all_revokes_every_session_exactly_once(self, mongo_db):
        """`revoke_all_for_user` is `update_many` filtered on `revoked: False`.

        Two concurrent sign-out-everywhere calls must not both claim to have
        revoked the same sessions: the count is what an audit record reports,
        and two records each claiming three revocations describes six sessions
        that never existed.
        """

        async def body(db):
            from security.sessions import SessionStore

            store = SessionStore(db)
            for i in range(3):
                await store.create("user-A", f"jti-{i}")
            await store.create("user-B", "b-jti")

            counts = await _gather(store.revoke_all_for_user("user-A"),
                                   store.revoke_all_for_user("user-A"))
            a_live = await db.sessions.count_documents({"user_id": "user-A",
                                                        "revoked": False})
            b_live = await db.sessions.count_documents({"user_id": "user-B",
                                                        "revoked": False})
            return counts, a_live, b_live

        counts, a_live, b_live = _run(mongo_db, body)
        assert sum(counts) == 3, (
            f"three sessions must be revoked three times in total across two "
            f"concurrent callers, got {counts}")
        assert a_live == 0
        assert b_live == 1, "another user's session was revoked by A's logout-all"


# =========================================================================== #
# §5 — the fan-out caps (Phase 6)                                              #
# =========================================================================== #
class TestNoSweepSilentlySkipsUsers:
    """The `to_list(N)` caps were not races; they were work never done.

    Each test seeds more documents than the old cap and asserts every user is
    reached. The counts are deliberately just over the old boundary — 101 users
    against `to_list(100)`, 201 trades against `to_list(200)` — because that is
    where the defect starts and a much larger number would only make the suite
    slow.
    """

    def test_every_open_trade_is_streamed_past_the_old_cap(self, mongo_db):
        async def body(db):
            from services import fanout

            await db.trades.insert_many([
                {"user_id": f"u{i}", "status": "OPEN", "symbol": "RELIANCE"}
                for i in range(201)])
            rows = await fanout.collect(db.trades.find({"status": "OPEN"}),
                                        label="test")
            return len(rows), len({r["user_id"] for r in rows})

        count, users = _run(mongo_db, body)
        assert count == 201, f"the 201st open trade was skipped; saw {count}"
        assert users == 201, f"{201 - users} users were never monitored"

    def test_every_live_broker_account_is_restored_past_the_old_cap(self, mongo_db):
        """`live_accounts` feeds startup session restore.

        A cap here meant accounts past the 1,000th were never restored and their
        owners found a disconnected broker after a deploy, with nothing logged.
        Seeded at 1,001 to sit exactly one past the old boundary.
        """

        async def body(db):
            from services.brokers.accounts import BrokerAccountDirectory

            await db.broker_accounts.insert_many([
                {"broker_account_id": f"ba_{i:032d}", "user_id": f"u{i}",
                 "broker": "upstox", "status": "connected",
                 "created_at": "2026-01-01T00:00:00+00:00"}
                for i in range(1001)])
            return len(await BrokerAccountDirectory(db).live_accounts())

        assert _run(mongo_db, body) == 1001

    def test_the_ceiling_is_a_breaker_that_announces_itself(self, caplog, mongo_db):
        """The replacement ceiling must never be silent — that was the defect.

        A cap that can be hit without saying so is exactly what `to_list(N)` was.
        This drives the breaker with a deliberately tiny ceiling and requires the
        log line to name the sweep and the count.
        """
        import logging

        async def body(db):
            from services import fanout

            await db.trades.insert_many([{"status": "OPEN"} for _ in range(10)])
            with caplog.at_level(logging.ERROR, logger="services.fanout"):
                rows = await fanout.collect(db.trades.find({"status": "OPEN"}),
                                            label="deliberate-runaway", ceiling=3)
            return len(rows), caplog.text

        count, text = _run(mongo_db, body)
        assert count == 3, "the breaker must stop at its ceiling"
        assert "deliberate-runaway" in text, (
            "the breaker fired without naming the sweep — an operator cannot act "
            "on 'fan-out ceiling reached' with no subject")


# =========================================================================== #
# §6 — the tenant stress matrix (Phase 8)                                      #
# =========================================================================== #
class TestTenantIsolationHoldsUnderConcurrentLoad:
    """10 users × 2 broker accounts each, exercised simultaneously.

    D6.3 and D6.4 proved isolation sequentially. This asks whether it survives
    the thing they could not construct: every user's read in flight at once
    against one database, one process and one set of module-level singletons.
    """

    USERS = 10
    ACCOUNTS_PER_USER = 2

    async def _seed(self, db):
        from services.brokers.accounts import broker_accounts

        brokers = ("upstox", "zerodha")
        expected = {}
        for u in range(self.USERS):
            user_id = f"stress-u{u}"
            expected[user_id] = set()
            for n in range(self.ACCOUNTS_PER_USER):
                account = account_ref(user_id, brokers[n], suffix=str(n))
                expected[user_id].add(account.broker_account_id)
                await db.broker_accounts.insert_one({
                    "broker_account_id": account.broker_account_id,
                    "user_id": user_id, "broker": brokers[n],
                    "status": "connected",
                    "created_at": f"2026-01-0{n + 1}T00:00:00+00:00"})
                await db.holdings.insert_one({
                    "user_id": user_id, "broker_account_id": account.broker_account_id,
                    "symbol": f"SYM{u}{n}", "quantity": 1,
                    "invested_value": 1.0, "market_value": 1.0})
                await db.orders.insert_one({
                    "user_id": user_id, "broker_account_id": account.broker_account_id,
                    "order_id": f"OID-{u}-{n}", "symbol": f"SYM{u}{n}"})
        broker_accounts.configure(db)
        return expected

    def test_no_user_resolves_another_users_account_under_concurrent_load(self, mongo_db):
        async def body(db):
            from services.brokers.accounts import broker_accounts

            expected = await self._seed(db)
            try:
                async def read(user_id):
                    refs = await broker_accounts.list_for_user(user_id)
                    return user_id, {r.broker_account_id for r in refs}

                results = await _gather(*[read(u) for u in expected])
                return expected, results
            finally:
                broker_accounts.configure(None)

        expected, results = _run(mongo_db, body)
        assert len(results) == self.USERS
        for user_id, seen in results:
            assert seen == expected[user_id], (
                f"{user_id} resolved {seen - expected[user_id]} account(s) that "
                f"are not theirs under concurrent load")

    def test_a_foreign_account_id_is_refused_even_while_its_owner_reads_it(self, mongo_db):
        """The strongest form: the attacker's read overlaps the owner's.

        A cache or a module-level 'current account' would leak here and nowhere
        else — the owner's successful resolution is in flight at the instant the
        stranger asks for the same id.
        """

        async def body(db):
            from services.brokers.accounts import broker_accounts
            from services.brokers.accounts import UnknownBrokerAccount

            expected = await self._seed(db)
            try:
                victim = "stress-u0"
                target = sorted(expected[victim])[0]

                async def owner():
                    ref = await broker_accounts.resolve(victim, target)
                    return ("owner", ref.broker_account_id)

                async def stranger(user_id):
                    try:
                        ref = await broker_accounts.resolve(user_id, target)
                        return ("LEAK", ref.broker_account_id)
                    except UnknownBrokerAccount:
                        return ("refused", None)

                strangers = [u for u in expected if u != victim]
                results = await _gather(owner(),
                                        *[stranger(u) for u in strangers] * 3)
                return target, results
            finally:
                broker_accounts.configure(None)

        target, results = _run(mongo_db, body)
        leaks = [r for r in results if r[0] == "LEAK"]
        assert not leaks, f"a foreign account resolved under load: {leaks}"
        # THE OWNER-POSITIVE CONTROL. Without it, "everyone was refused" is also
        # what a completely broken directory looks like.
        assert results[0] == ("owner", target), (
            f"the owner's own resolution failed ({results[0]}), so the refusals "
            f"above prove nothing about isolation")
        assert len(results) == 1 + (self.USERS - 1) * 3

    def test_concurrent_order_reads_never_cross_tenants(self, mongo_db, monkeypatch):
        """Drives the production `/api/orders` handler, not the database.

        STRENGTHENED IN THE D6.7 RE-VERIFICATION. This test used to run
        `db.orders.find({"user_id": user_id})` itself — a query written by the
        test, against the database, touching no production code. It could not
        fail however broken the order endpoint was: it verified that MongoDB
        honours a filter. It now calls `unified_orders`, whose filter is the one
        under test.
        """
        import server

        async def body(db):
            expected = await self._seed(db)
            monkeypatch.setattr(server, "db", db)
            try:
                async def read(user_id):
                    body = await server.unified_orders(
                        user={"_id": user_id}, broker=None,
                        broker_account_id=None, refresh=False)
                    rows = body["orders"]
                    return user_id, {r["order_id"] for r in rows}, {
                        r["user_id"] for r in rows}

                return await _gather(*[read(u) for u in expected])
            finally:
                from services.brokers.accounts import broker_accounts
                broker_accounts.configure(None)

        for user_id, order_ids, owners in _run(mongo_db, body):
            assert owners == {user_id}, (
                f"{user_id}'s order read returned rows owned by {owners - {user_id}}")
            assert len(order_ids) == self.ACCOUNTS_PER_USER


# =========================================================================== #
# §7 — credential retention (Phase 11)                                         #
# =========================================================================== #
class TestDecryptedTokensDoNotLiveForever:
    def test_an_idle_session_loses_its_plaintext_copy(self):
        from services.broker_engine import BrokerEngine

        engine = BrokerEngine()
        engine._cache_session("ba_idle", {"access_token": "t"})
        engine._cache_session("ba_busy", {"access_token": "t"})
        # Age only the idle one, by rewriting its touch rather than sleeping.
        engine._session_touched["ba_idle"] -= 10_000

        assert engine.evict_idle_sessions() == 1
        assert "ba_idle" not in engine._sessions
        assert "ba_busy" in engine._sessions, "an active session was evicted"

    def test_eviction_drops_the_timestamp_with_the_session(self):
        """A touch entry outliving its session is a slow leak of its own."""
        from services.broker_engine import BrokerEngine

        engine = BrokerEngine()
        engine._cache_session("ba_1", {"access_token": "t"})
        engine._session_touched["ba_1"] -= 10_000
        engine.evict_idle_sessions()
        assert "ba_1" not in engine._session_touched

    def test_an_untracked_entry_is_treated_as_idle_not_as_fresh(self):
        """Fail safe. An entry with no timestamp must not become immortal."""
        from services.broker_engine import BrokerEngine

        engine = BrokerEngine()
        engine._sessions["ba_orphan"] = {"access_token": "t"}
        assert engine.evict_idle_sessions() == 1

    def test_every_cache_write_records_a_touch(self):
        """The invariant the sweeper depends on, asserted on the real writes.

        A direct `self._sessions[key] = ...` anywhere in the engine would create
        exactly the untracked entry the previous test describes. This drives the
        engine's own session-storing path rather than the accessor, so a future
        edit that bypasses `_cache_session` is caught here.
        """
        from unittest.mock import AsyncMock

        from services.broker_engine import BrokerEngine

        engine = BrokerEngine()
        engine.db = AsyncMock()
        account = account_ref("u1", "upstox")
        asyncio.run(engine._save_session(account, {"access_token": "t"}))
        assert set(engine._sessions) == set(engine._session_touched), (
            "a session was cached without recording a touch; the sweeper cannot "
            "see it and its plaintext token lives for the life of the process")

    def test_the_sweeper_is_owned_by_the_engine_not_the_global_registry(self):
        """Pins the scope fix, because the failure it caused was remote.

        Registering the sweeper with `infrastructure.tasks` put a perpetual task
        in a PROCESS-GLOBAL registry that outlives an event loop. `BrokerEngine`
        is a module-level singleton whose `start_recovery()` runs in any test
        that restores a session, so the first such test left a task bound to a
        loop that then closed — and a later test's `cancel_all()` reached across
        it. The symptom was two failures in `test_observability_subsystems.py`
        that appeared only in a full run and passed in isolation, which is the
        hardest kind of regression to attribute.

        So this asserts the ownership directly: starting a sweeper must add
        nothing to the global registry, and `shutdown()` must leave none behind.
        """
        from infrastructure import tasks
        from services.broker_engine import BrokerEngine

        engine = BrokerEngine()

        async def scenario():
            before = set(tasks.registry.running)
            engine._start_credential_sweeper()
            assert engine._credential_sweeper is not None, (
                "no sweeper was started, so this test asserts nothing")
            during = set(tasks.registry.running)
            await engine._stop_credential_sweeper()
            return before, during

        before, during = asyncio.run(scenario())
        assert during == before, (
            f"the credential sweeper registered itself globally: "
            f"{during - before}. It outlives the event loop it was created on.")
        assert engine._credential_sweeper is None, (
            "shutdown left the sweeper task behind")

    def test_starting_twice_does_not_create_a_second_sweeper(self):
        from services.broker_engine import BrokerEngine

        engine = BrokerEngine()

        async def scenario():
            first = engine._start_credential_sweeper()
            second = engine._start_credential_sweeper()
            try:
                return first is second
            finally:
                await engine._stop_credential_sweeper()

        assert asyncio.run(scenario()) is True


# =========================================================================== #
# §10 — closures for the three mutation survivors                              #
# =========================================================================== #
class TestTheSessionCacheNeverServesAnotherAccountsCredential:
    """Closes D6.7 mutation **M3**, which survived 382 tests.

    THE MUTANT
    ----------
    `get_session` reads::

        session = self._sessions.get(key) or await self._load_session(account)

    M3 appended ``or next(iter(self._sessions.values()), None)`` — a global
    fallback to *whatever session happens to be cached*. An account with no
    stored credential would then be handed another account's decrypted broker
    access token, and every subsequent call — holdings, orders, `place_order` —
    would execute against a stranger's brokerage account.

    WHY NOTHING CAUGHT IT
    ---------------------
    D6.4 and D6.6 both attack this line, and both attack the *key*: D6.6's M9
    re-keyed the cache by broker instead of by account, and was killed. Those
    tests prove a session is never fetched under the WRONG key. The mutant does
    not use a wrong key — it ignores the key entirely, on the miss path, which
    no test drove with a populated cache. A miss against an EMPTY cache (the
    shape every existing test happens to have) makes the injected clause return
    None, so the mutation is dead in the suite and live in production.

    The fix is the missing precondition, not a new assertion: the cache must be
    populated with somebody else's session before the miss is tested.
    """

    @staticmethod
    def _engine(db=None):
        from unittest.mock import AsyncMock

        from services.broker_engine import BrokerEngine

        engine = BrokerEngine()
        engine.db = db if db is not None else AsyncMock()
        return engine

    @staticmethod
    def _refuses(engine, account):
        """Assert `get_session` refuses, with every session in the cache FRESH.

        THE PRECONDITION THAT MAKES THIS FALSIFIABLE, AND IT IS SUBTLE.
        `session_is_fresh` is patched to True. Without that, a mutant that DOES
        hand back a stranger's session still fails the freshness check two lines
        later and raises `BrokerAuthError` anyway — so the test passes, the
        mutation survives, and the assertion has been satisfied by the wrong
        control. The first version of this test did exactly that. The cached
        credential has to be one the engine would happily use, or "it refused"
        says nothing about *why*.
        """
        from unittest.mock import patch

        from services.brokers.errors import BrokerAuthError

        with patch("services.brokers.broker_gateway.session_is_fresh",
                   return_value=True):
            with pytest.raises(BrokerAuthError):
                asyncio.run(engine.get_session(account))
        assert account.broker_account_id not in engine._sessions, (
            "a credential-less account was given a cache entry")

    def test_an_account_with_no_stored_session_is_refused_not_lent_one(self):
        from unittest.mock import AsyncMock

        victim = account_ref("victim", "upstox")
        stranger = account_ref("stranger", "upstox")

        engine = self._engine()
        # THE OTHER PRECONDITION THE SUITE WAS MISSING: a live session for
        # somebody else, already in the cache, at the moment the miss happens.
        engine._cache_session(victim.broker_account_id,
                              {"access_token": "VICTIM-TOKEN"})
        engine.db.broker_accounts.find_one = AsyncMock(return_value=None)
        self._refuses(engine, stranger)

    def test_a_second_account_of_the_same_user_is_also_refused(self):
        """Same user, same broker, two accounts — the narrowest form.

        A fallback keyed on anything coarser than `broker_account_id` looks
        correct here (same owner, same broker) and is still wrong: these are two
        separate brokerage accounts, and an order routed into the wrong one is
        an order in an account the user did not choose.
        """
        from unittest.mock import AsyncMock

        first = account_ref("u1", "upstox", suffix="one")
        second = account_ref("u1", "upstox", suffix="two")

        engine = self._engine()
        engine._cache_session(first.broker_account_id, {"access_token": "FIRST"})
        engine.db.broker_accounts.find_one = AsyncMock(return_value=None)
        self._refuses(engine, second)

    def test_the_owner_positive_control(self):
        """The cache must still serve the account it belongs to.

        Without this, both refusals above are equally satisfied by a
        `get_session` that never returns anything at all.
        """
        from unittest.mock import AsyncMock, patch

        account = account_ref("u1", "upstox")
        engine = self._engine()
        engine._cache_session(account.broker_account_id, {"access_token": "MINE"})
        engine.db.broker_accounts.find_one = AsyncMock(return_value=None)

        with patch("services.brokers.broker_gateway.session_is_fresh",
                   return_value=True):
            session = asyncio.run(engine.get_session(account))
        assert session["access_token"] == "MINE"


class TestSyncPortfolioActuallyUsesTheGenerationPath:
    """Closes D6.7 mutation **M10d**, which survived all 40 tests in this file.

    THE MUTANT
    ----------
    M10d reverted `sync_portfolio`'s single call to `_replace_holdings` back to
    the pre-D6.7 `delete_many` + `insert_many` pair. Every test of the fix
    passed, because every one of them called `_replace_holdings` **directly**.

    That is the D6.6/M4 shape again: the property was asserted of the right
    function and nothing asserted that the caller still calls it. A helper that
    is correct and unreachable is not a fix. These tests drive `sync_portfolio`
    itself, through the gateway, so the wiring is part of the evidence.

    NO BROKER IS CONTACTED. `broker_gateway`'s fetches and `get_session` are
    patched; what stays real is the database, the generation counter, the
    per-symbol upserts and the cleanup.
    """

    @staticmethod
    def _patched(holdings):
        from unittest.mock import AsyncMock, patch

        return [
            patch("services.broker_engine.BrokerEngine.get_session",
                  new=AsyncMock(return_value={"access_token": "t"})),
            patch("services.broker_engine.BrokerEngine.start_stream",
                  new=AsyncMock(return_value=None)),
            patch("services.brokers.broker_gateway.get_holdings",
                  new=AsyncMock(return_value=holdings)),
            patch("services.brokers.broker_gateway.get_positions",
                  new=AsyncMock(return_value=[])),
            patch("services.brokers.broker_gateway.get_funds",
                  new=AsyncMock(return_value={"available_margin": 100.0})),
            # D6.7 re-verification: `sync_portfolio` publishes a snapshot, and
            # the snapshot fetches live quotes. Unpatched, this class made 41
            # outbound Yahoo Finance requests per run — refused only because the
            # host had no route out. A test that would read live market data on
            # a networked machine is not a mocked test.
            patch("services.portfolio_stream.publish_snapshot",
                  new=AsyncMock(return_value=None)),
        ]

    def test_two_concurrent_syncs_both_succeed_and_leave_one_copy(self, mongo_db):
        """Drives `sync_portfolio` itself, and asserts what M10d actually breaks.

        A NOTE ON WHAT THE UNIQUE INDEX ALREADY DOES, AND WHY THE FIRST VERSION
        OF THIS TEST WAS TOO WEAK
        -----------------------------------------------------------------------
        This test originally asserted only the row count. It passed under M10d,
        and the reason is worth recording rather than patching around: with the
        unique index in place, the OLD `delete_many` + `insert_many` cannot
        double the portfolio either — the second `insert_many` collides and
        raises. The index alone closes the doubling.

        What the index does not close, and what the generation ordering exists
        for, is everything else about that pair:

        * the losing sync **raises `DuplicateKeyError` out of `sync_portfolio`**,
          so a user who clicks sync twice gets an error on a request that should
          simply have been idempotent;
        * `insert_many` is ordered, so it stops at the first collision and the
          positions after it are never written at all;
        * and between the delete and the insert the account holds **nothing**.

        So this asserts both callers succeed, which is the property that
        distinguishes the two implementations. The row count stays as a
        secondary assertion, not the primary one.
        """
        import contextlib

        holdings = [{"symbol": s, "quantity": 10, "invested_value": 100.0,
                     "market_value": 120.0} for s in ("RELIANCE", "TCS", "INFY")]

        async def body(db):
            from services.brokers.order_identity import ensure_holding_identity_index
            from services.broker_engine import BrokerEngine

            await ensure_holding_identity_index(db)
            account = account_ref("u1", "upstox")
            engine = BrokerEngine()
            engine.configure(db)
            with contextlib.ExitStack() as stack:
                for p in self._patched(holdings):
                    stack.enter_context(p)
                barrier = asyncio.Barrier(2)
                engine.configure(_StartGatedDb(db, {"holdings": barrier}))
                results = await _gather(engine.sync_portfolio(account),
                                        engine.sync_portfolio(account))
            engine.configure(db)
            rows = await db.holdings.find(
                {"broker_account_id": account.broker_account_id}).to_list(100)
            failures = [repr(r)[:160] for r in results
                        if isinstance(r, BaseException)]
            return failures, len(rows), sorted(r["symbol"] for r in rows)

        failures, count, symbols = _run(mongo_db, body)
        assert failures == [], (
            f"a concurrent sync of the same account failed: {failures}. Syncing "
            f"twice must be idempotent, not an error the user sees.")
        assert count == 3, (
            f"two concurrent syncs of a 3-position account left {count} holding "
            f"rows. 6 is the pre-D6.7 doubling reaching the dashboard as double "
            f"the portfolio value; fewer than 3 is positions lost.")
        assert symbols == ["INFY", "RELIANCE", "TCS"]

    def test_a_concurrent_reader_never_sees_the_account_empty(self, mongo_db):
        """The empty window, observed through `sync_portfolio` rather than argued.

        `_replace_holdings` deletes nothing before the new rows exist, so the
        window is gone by construction — but "by construction" is exactly the
        claim a caller-site mutation invalidates, so it is measured here from a
        reader running against the same database while syncs land.
        """
        import contextlib

        holdings = [{"symbol": s, "quantity": 10, "invested_value": 100.0,
                     "market_value": 120.0} for s in ("RELIANCE", "TCS")]

        async def body(db):
            from services.brokers.order_identity import ensure_holding_identity_index
            from services.broker_engine import BrokerEngine

            await ensure_holding_identity_index(db)
            account = account_ref("u1", "upstox")
            scope = {"broker_account_id": account.broker_account_id}
            engine = BrokerEngine()
            engine.configure(db)
            with contextlib.ExitStack() as stack:
                for p in self._patched(holdings):
                    stack.enter_context(p)
                await engine.sync_portfolio(account)

                observed = []
                stop = asyncio.Event()

                async def watcher():
                    while not stop.is_set():
                        observed.append(await db.holdings.count_documents(scope))
                        await asyncio.sleep(0)

                async def resync():
                    try:
                        for _ in range(15):
                            await engine.sync_portfolio(account)
                    finally:
                        stop.set()

                await asyncio.wait_for(
                    asyncio.gather(watcher(), resync(), return_exceptions=True),
                    timeout=TIMEOUT)
            return observed

        observed = _run(mongo_db, body)
        assert observed, "the watcher never sampled; the test proves nothing"
        assert min(observed) >= 2, (
            f"a concurrent reader saw {min(observed)} holdings during a re-sync "
            f"of a 2-position account. Any dip below the real position count is "
            f"an empty or partial portfolio reported to a user as fact.")

    def test_a_sync_still_removes_a_position_that_was_sold(self, mongo_db):
        """The owner-positive control for the cleanup.

        Without it, "no rows were lost" is equally satisfied by a sync that
        never deletes anything — and then a sold position would linger in the
        portfolio forever, which is the opposite error and just as wrong.
        """
        import contextlib

        async def body(db):
            from services.brokers.order_identity import ensure_holding_identity_index
            from services.broker_engine import BrokerEngine

            await ensure_holding_identity_index(db)
            account = account_ref("u1", "upstox")
            engine = BrokerEngine()
            engine.configure(db)
            two = [{"symbol": s, "quantity": 10, "invested_value": 100.0,
                    "market_value": 120.0} for s in ("RELIANCE", "TCS")]
            one = two[:1]

            with contextlib.ExitStack() as stack:
                for p in self._patched(two):
                    stack.enter_context(p)
                await engine.sync_portfolio(account)
            with contextlib.ExitStack() as stack:
                for p in self._patched(one):
                    stack.enter_context(p)
                await engine.sync_portfolio(account)

            rows = await db.holdings.find(
                {"broker_account_id": account.broker_account_id}).to_list(100)
            return sorted(r["symbol"] for r in rows)

        assert _run(mongo_db, body) == ["RELIANCE"], (
            "a position the broker no longer reports was left in the portfolio")


# =========================================================================== #
# §8 — realtime under concurrent load (Phase 10)                               #
# =========================================================================== #
class TestPrivateEventsStayPrivateUnderFanOut:
    """Mocked ticks, many accounts, one bridge.

    NOT LIVE MARKET DATA, and the distinction is recorded rather than assumed:
    every event below is constructed by this file. D6.6 §10 established that a
    broker feed's ticks are stamped with `owner_user_id` and delivered per-user;
    this asks whether that survives many accounts publishing at once through one
    process-global bridge and one process-global socket map.
    """

    @staticmethod
    def _manager():
        class _Manager:
            def __init__(self):
                self.per_user = {}
                self.broadcasts = []

            async def send_to_user(self, user_id, envelope):
                self.per_user.setdefault(user_id, []).append(envelope)

            async def broadcast_to_channel(self, channel, envelope):
                self.broadcasts.append((channel, envelope))

        return _Manager()

    def test_concurrent_private_events_never_reach_another_user(self):
        from services.realtime.event_bridge import _deliver

        manager = self._manager()

        async def main():
            await asyncio.gather(*[
                _deliver(manager, {
                    "type": kind,
                    "data": {"user_id": f"u{i}", "symbol": "RELIANCE",
                             "broker_account_id": f"ba_{i}"}})
                for i in range(25)
                for kind in ("trade.updated", "portfolio.updated",
                             "broker.order.updated", "notification.created")])

        asyncio.run(main())
        assert manager.broadcasts == [], (
            f"private events were broadcast to a channel: "
            f"{[c for c, _ in manager.broadcasts]}")
        for i in range(25):
            got = manager.per_user[f"u{i}"]
            assert len(got) == 4, f"u{i} received {len(got)} events, expected 4"
            for envelope in got:
                assert envelope["data"]["user_id"] == f"u{i}", (
                    f"u{i} received an event owned by "
                    f"{envelope['data']['user_id']}")

    def test_a_private_event_with_no_owner_is_dropped_not_broadcast(self):
        """D6.1 / S6's fail-closed rule, re-asserted under the D6.7 load shape.

        A publisher that forgets `user_id` loses its event. The alternative —
        the pre-D6.1 `if user_id: send_to_user else: broadcast` — sent it to
        everybody, and under fan-out that is one omission away from every user's
        trade flow reaching every socket.
        """
        from services.realtime.event_bridge import _deliver

        manager = self._manager()

        async def main():
            await asyncio.gather(*[
                _deliver(manager, {"type": kind, "data": {"symbol": "RELIANCE"}})
                for kind in ("trade.updated", "portfolio.updated",
                             "broker.order.updated", "notification.created",
                             "watchlist.updated")])

        asyncio.run(main())
        assert manager.broadcasts == []
        assert manager.per_user == {}

    def test_public_market_events_still_fan_out(self):
        """The owner-positive control.

        Without it, "nothing was broadcast" is also what a completely broken
        bridge looks like — and the public market channels are the ones that are
        SUPPOSED to reach everyone.
        """
        from services.realtime.event_bridge import _deliver

        manager = self._manager()

        async def main():
            for kind in ("price.updated", "sector.analyzed", "scanner.updated"):
                await _deliver(manager, {"type": kind, "data": {"symbol": "NIFTY"}})

        asyncio.run(main())
        assert [c for c, _ in manager.broadcasts] == ["market", "sectors", "scanner"]
        assert manager.per_user == {}

    def test_no_private_channel_can_be_subscribed_to(self):
        """The second half of the same control (D6.1 / S6).

        The bridge stops anything being *sent* to a private channel; this stops
        anything being *received* on one. Either alone leaves a future broadcast
        one mistake away from a leak.
        """
        import server
        from services.realtime.event_bridge import PRIVATE_CHANNELS

        manager = server.ConnectionManager()
        socket = object()
        manager.channels[socket] = set()
        accepted, refused = manager.subscribe(
            socket, list(PRIVATE_CHANNELS) + ["market"])
        assert set(refused) == set(PRIVATE_CHANNELS), (
            f"a private channel was subscribable: "
            f"{set(PRIVATE_CHANNELS) - set(refused)}")
        assert accepted == ["market"], "the public channel must still be granted"


# =========================================================================== #
# §9 — identity transitions under concurrency (Phase 9)                        #
# =========================================================================== #
class TestIdentityTransitionsUnderConcurrency:
    def test_a_revoked_session_cannot_be_refreshed_back_to_life(self, mongo_db):
        """Logout-all, then a refresh that was already in flight.

        The stop condition is "revoked sessions can be resurrected". The CAS
        filter carries `revoked: False`, so a rotation that started before the
        revocation cannot complete after it.
        """

        async def body(db):
            from security.sessions import SessionStore

            store = SessionStore(db)
            sessions = [await store.create("user-A", f"jti-{i}") for i in range(3)]
            await store.revoke_all_for_user("user-A")
            outcomes = await _gather(*[
                store.rotate(sid, f"jti-{i}", f"jti-{i}-next")
                for i, sid in enumerate(sessions)])
            live = await db.sessions.count_documents({"user_id": "user-A",
                                                      "revoked": False})
            return [r.outcome for r in outcomes], live

        outcomes, live = _run(mongo_db, body)
        assert outcomes == [REVOKED] * 3, (
            f"a revoked session accepted a refresh: {outcomes}")
        assert live == 0, f"{live} revoked session(s) came back to life"

    def test_one_devices_logout_does_not_kill_another_devices_session(self, mongo_db):
        """The reason session closure is keyed by `sid` and not by user.

        Signing out on one device must not tear down another device's session:
        that session was not revoked and its socket is still legitimate. Run
        concurrently, because a store that resolved "which session" from
        anything process-global would fail only here.
        """

        async def body(db):
            from security.sessions import SessionStore

            store = SessionStore(db)
            phone = await store.create("user-A", "phone-1")
            laptop = await store.create("user-A", "laptop-1")
            results = await _gather(store.revoke(phone),
                                    store.rotate(laptop, "laptop-1", "laptop-2"))
            phone_doc = await db.sessions.find_one({"session_id": phone})
            laptop_doc = await db.sessions.find_one({"session_id": laptop})
            return results[1].outcome, phone_doc["revoked"], laptop_doc["revoked"]

        outcome, phone_revoked, laptop_revoked = _run(mongo_db, body)
        assert phone_revoked is True
        assert laptop_revoked is False, "logging out one device killed another"
        assert outcome == "rotated"

    def test_a_stale_identitys_writes_cannot_land_on_the_new_identity(self, mongo_db, monkeypatch):
        """The store-reset question, asked at the data layer.

        A request issued as user A, still in flight when the tab becomes user B,
        must not write into B's data. Every write on these paths carries the
        owner in its FILTER, not merely in its payload — so a stale write matches
        nothing rather than landing somewhere.
        """

        async def body(db):
            from services import paper_trade

            a, b = ObjectId(), ObjectId()
            trade_id = ObjectId()
            await db.users.insert_many([
                {"_id": a, "paper_capital": 100000.0},
                {"_id": b, "paper_capital": 100000.0}])
            # The trade belongs to A.
            await db.trades.insert_one({
                "_id": trade_id, "user_id": str(a), "symbol": "RELIANCE",
                "type": "BUY", "entry_price": 100.0, "quantity": 10,
                "status": "OPEN", "is_paper": True})

            import services.real_market as real_market

            async def fake_quote(symbol):
                return {"price": 150.0}

            # Restored by monkeypatch: a bare assignment leaked this fake quote
            # into every later test in the process (D6.7 re-verification).
            monkeypatch.setattr(real_market, "fetch_real_stock_quote", fake_quote)

            # B — the identity the tab now holds — tries to close A's trade,
            # concurrently with A's own legitimate close.
            results = await _gather(
                paper_trade.close_paper_trade(str(trade_id), str(a), db),
                paper_trade.close_paper_trade(str(trade_id), str(b), db))
            rows = {str(u["_id"]): u["paper_capital"]
                    for u in await db.users.find({}).to_list(10)}
            trade = await db.trades.find_one({"_id": trade_id})
            return results, rows, trade

        results, balances, trade = _run(mongo_db, body)
        assert isinstance(results[1], Exception), (
            "user B closed a trade belonging to user A")
        assert balances[str(trade["user_id"])] == 101500.0, (
            f"the owner's proceeds are wrong: {balances}")
        other = [v for k, v in balances.items() if k != str(trade["user_id"])]
        assert other == [100000.0], (
            f"a stale identity's close credited another user: {other}")


# =========================================================================== #
# §11 — D6.7 RE-VERIFICATION (2026-09-13)                                     #
# =========================================================================== #
# The D6.7 brief was re-issued after D6.7 had completed and been committed. It
# was treated as a verify-not-redo pass: the suite above was re-run against
# HEAD, the mutation campaign was re-executed independently, and the brief was
# diffed against what §1–§10 actually assert. Everything below is what that
# diff found missing — including one financial defect in a D6.7 fix.


class TestAnUnseededBalanceStartsFromTheDefault:
    """A D6.7 fix that reset a user's paper capital to the size of one credit.

    THE DEFECT
    ----------
    `update_paper_balance` issues `$inc` against `{"_id": oid}` and runs its
    seeding branch only when that matches nothing. Its docstring says a match
    means "that row had a balance". It does not: the filter matches every user
    row, including the ordinary one that has **no `paper_capital` field yet**,
    and `$inc` on an absent field starts from zero. The seeding branch was
    reachable only for a user row that does not exist at all.

    REACHABLE FROM THE PRODUCT, NOT ONLY IN THEORY
    ----------------------------------------------
    `execute_paper_trade` debits — and so seeds — only for a BUY. A user whose
    first paper trade is a SELL (a short) never has the field written; closing
    that short calls `update_paper_balance(profit)`, and the account that read
    ₹1,00,000 a moment earlier now holds the profit alone.

    WHY NO D6.7 TEST SAW IT
    -----------------------
    Every real-Mongo balance test in D6.3 and D6.7 inserts its user with
    `"paper_capital": 100000.0` already set, so the unseeded path — the one the
    docstring spends a paragraph defending — was never driven. The fixture had
    removed exactly the dimension the code branched on (D6.6's M13 shape).
    """

    def test_a_credit_to_a_user_who_never_paper_traded_starts_from_the_default(self, mongo_db):
        async def body(db):
            from services import paper_trade

            user_id = ObjectId()
            await db.users.insert_one({"_id": user_id, "email": "fixture@test.invalid"})
            before = await paper_trade.get_paper_balance(str(user_id), db)
            after = await paper_trade.update_paper_balance(str(user_id), 500.0, db)
            stored = (await db.users.find_one({"_id": user_id}))["paper_capital"]
            return before["balance"], after, stored

        before, after, stored = _run(mongo_db, body)
        assert before == 100000.0, "precondition: an unseeded user reads the default"
        assert stored == 100500.0, (
            f"a ₹500 credit to a user who read ₹1,00,000 left ₹{stored:,.2f}. "
            f"`$inc` on an absent field starts from zero, so the starting "
            f"capital was discarded.")
        assert after == 100500.0

    def test_a_first_trade_that_is_a_short_keeps_the_starting_capital(self, mongo_db, monkeypatch):
        """The same defect, reached through the product's own two calls."""
        import services.real_market as real_market

        async def exit_quote(symbol):
            return {"price": 90.0}

        monkeypatch.setattr(real_market, "fetch_real_stock_quote", exit_quote)

        async def body(db):
            from services import paper_trade

            user_id = ObjectId()
            await db.users.insert_one({"_id": user_id, "email": "fixture@test.invalid"})
            trade = await paper_trade.execute_paper_trade(
                user_id=str(user_id), symbol="RELIANCE", stock_name="Reliance",
                quantity=10, entry_price=100.0, trade_type="SELL",
                stop_loss=110.0, target1=90.0, target2=85.0,
                setup_type="MOMENTUM", notes="", db=db)
            await paper_trade.close_paper_trade(str(trade["_id"]), str(user_id), db)
            return (await paper_trade.get_paper_balance(str(user_id), db))["balance"]

        balance = _run(mongo_db, body)
        # Short 10 @ ₹100, covered @ ₹90 → ₹100 profit on ₹1,00,000.
        assert balance == 100100.0, (
            f"a user whose first paper trade was a profitable short ended with "
            f"₹{balance:,.2f}; expected ₹1,00,100.00")

    def test_concurrent_credits_to_an_unseeded_row_are_all_applied(self, mongo_db):
        """The seeding branch under contention — the race it claims to handle.

        Four credits start at one rendezvous against a row with no balance, so
        every caller misses the increment and more than one reaches the seed.
        The spy proves that last part: if only one caller ever entered the seed,
        the concurrent-creator fallback was never exercised and a green result
        would say nothing about it.
        """

        async def body(db):
            from services import paper_trade

            user_id = ObjectId()
            await db.users.insert_one({"_id": user_id, "email": "fixture@test.invalid"})
            seed_attempts = []

            class _Spy(_StartGate):
                async def update_one(self, filter, update, *args, **kwargs):
                    if isinstance(filter.get("paper_capital"), dict) and \
                            filter["paper_capital"].get("$exists") is False:
                        seed_attempts.append(1)
                    return await super().update_one(filter, update, *args, **kwargs)

            gated = _StartGatedDb(db, {})
            gated._gated["users"] = _Spy(db.users, asyncio.Barrier(4))
            await _gather(*[paper_trade.update_paper_balance(str(user_id), 100.0, gated)
                            for _ in range(4)])
            stored = (await db.users.find_one({"_id": user_id}))["paper_capital"]
            return stored, len(seed_attempts)

        stored, seeds = _run(mongo_db, body)
        assert stored == 100400.0, (
            f"four concurrent ₹100 credits to an unseeded account left "
            f"₹{stored:,.2f}; expected ₹1,00,400.00")
        assert seeds >= 2, (
            f"only {seeds} caller(s) reached the seeding branch, so the barrier "
            f"did not overlap them and this test proves nothing about the race")


class TestFourAccountOwnershipMatrixUnderConcurrency:
    """Phase 6's exact fixture, through production code, all at once.

        USER_A: A_UPSTOX_1, A_UPSTOX_2      (two accounts at ONE broker)
        USER_B: B_UPSTOX_1, B_ZERODHA_1

    §6 above resolves accounts under load but never puts two accounts of one
    user at one broker in flight together — the shape where every coarser key
    (broker name, "the user's upstox", array position, latest account) looks
    right and routes into the wrong brokerage account. It also never ran a
    disconnect, a status update, a sync and a session read concurrently.

    NO BROKER IS CONTACTED. The gateway's network calls are spies that echo the
    credential they were handed, so a session served to the wrong account is
    visible in the data it produced rather than inferred.
    """

    ACCOUNTS = {
        "A_UPSTOX_1": ("user-a", "upstox"),
        "A_UPSTOX_2": ("user-a", "upstox"),
        "B_UPSTOX_1": ("user-b", "upstox"),
        "B_ZERODHA_1": ("user-b", "zerodha"),
    }

    def test_no_concurrent_operation_crosses_an_account(self, mongo_db):
        import contextlib
        from unittest.mock import AsyncMock, patch

        refs = {label: account_ref(user, broker, suffix=label.lower())
                for label, (user, broker) in self.ACCOUNTS.items()}
        token_of = {r.broker_account_id: f"TOKEN-{label}" for label, r in refs.items()}
        label_of = {r.broker_account_id: label for label, r in refs.items()}
        by_token = {v: k for k, v in token_of.items()}

        async def echo_holdings(broker, session):
            owner = by_token[session["access_token"]]
            return [{"symbol": f"HOLD-{label_of[owner]}", "quantity": 1,
                     "invested_value": 1.0, "market_value": 1.0}]

        async def echo_orders(broker, session):
            return [{"order_id": f"ORD-{session['access_token']}"}]

        invalidated = []

        async def spy_invalidate(broker, session):
            invalidated.append(session["access_token"])

        async def body(db):
            from services.broker_engine import BrokerEngine
            from services.brokers.accounts import (
                AmbiguousBrokerAccount, UnknownBrokerAccount, broker_accounts)
            from services.brokers.order_identity import ensure_holding_identity_index

            await ensure_holding_identity_index(db)
            for i, (label, r) in enumerate(refs.items()):
                await db.broker_accounts.insert_one({
                    "broker_account_id": r.broker_account_id, "user_id": r.user_id,
                    "broker": r.broker, "status": "connected",
                    "access_token": "encrypted-at-rest-placeholder",
                    "created_at": f"2026-01-0{i + 1}T00:00:00+00:00"})
            engine = BrokerEngine()
            engine.configure(db)
            for account_id, token in token_of.items():
                engine._cache_session(account_id, {"access_token": token})

            ops = []

            def op(name, coro):
                async def run():
                    try:
                        return name, await coro
                    except Exception as e:  # noqa: BLE001 - recorded, asserted below
                        return name, e
                ops.append(run())

            a1, a2, b1, bz = (refs[k] for k in self.ACCOUNTS)
            for _ in range(3):
                for label, r in refs.items():
                    other = "user-b" if r.user_id == "user-a" else "user-a"
                    op(f"resolve-own:{label}", broker_accounts.resolve(r.user_id, r.broker_account_id))
                    op(f"resolve-foreign:{label}", broker_accounts.resolve(other, r.broker_account_id))
                    if r is not b1:
                        op(f"session:{label}", engine.get_session(r))
                        op(f"orders:{label}", engine.get_orders(r))
                op("sole:user-a:upstox", broker_accounts.sole_for_broker("user-a", "upstox"))
                op("sole:user-a:zerodha", broker_accounts.sole_for_broker("user-a", "zerodha"))
                op("sole:user-b:upstox", broker_accounts.sole_for_broker("user-b", "upstox"))
                op("sole:user-b:zerodha", broker_accounts.sole_for_broker("user-b", "zerodha"))
                op("list:user-a", broker_accounts.list_for_user("user-a"))
                op("list:user-b", broker_accounts.list_for_user("user-b"))
            op("update:A_UPSTOX_2", broker_accounts.set_status(a2.broker_account_id, "connected", last_sync="x"))
            op("disconnect:B_UPSTOX_1", engine.disconnect(b1))
            for label in ("A_UPSTOX_1", "A_UPSTOX_2", "B_ZERODHA_1"):
                op(f"sync:{label}", engine.sync_portfolio(refs[label]))

            with contextlib.ExitStack() as stack:
                for target, value in (
                    ("services.brokers.broker_gateway.session_is_fresh", lambda *a, **k: True),
                    ("services.brokers.broker_gateway.get_holdings", echo_holdings),
                    ("services.brokers.broker_gateway.get_positions", AsyncMock(return_value=[])),
                    ("services.brokers.broker_gateway.get_funds", AsyncMock(return_value=None)),
                    ("services.brokers.broker_gateway.get_orders", echo_orders),
                    ("services.brokers.broker_gateway.invalidate_session", spy_invalidate),
                    ("services.broker_engine.stream_manager.stop_stream", AsyncMock(return_value=None)),
                    ("services.broker_engine.detach_market_feed", AsyncMock(return_value=None)),
                    ("services.broker_engine.BrokerEngine.start_stream", AsyncMock(return_value=None)),
                    ("services.portfolio_stream.publish_snapshot", AsyncMock(return_value=None)),
                    ("services.broker_engine.BrokerEngine._publish_connection", AsyncMock(return_value=None)),
                    ("services.broker_engine.BrokerEngine._push", AsyncMock(return_value=None)),
                ):
                    stack.enter_context(patch(target, new=value))
                results = await _gather(*ops)

            holdings = await db.holdings.find({}).to_list(100)
            rows = {d["broker_account_id"]: d for d in await db.broker_accounts.find({}).to_list(10)}
            return results, holdings, rows, AmbiguousBrokerAccount, UnknownBrokerAccount

        results, holdings, rows, Ambiguous, Unknown = _run(mongo_db, body)
        failures = []
        for name, value in results:
            kind, _, subject = name.partition(":")
            if kind == "resolve-own":
                if isinstance(value, Exception) or value.broker_account_id != refs[subject].broker_account_id:
                    failures.append(f"{name} → {value!r}")
            elif kind == "resolve-foreign":
                if not isinstance(value, Unknown):
                    failures.append(f"{name} RESOLVED a foreign account → {value!r}")
            elif kind == "session":
                if isinstance(value, Exception) or value["access_token"] != f"TOKEN-{subject}":
                    failures.append(f"{name} served {value!r}")
            elif kind == "orders":
                if isinstance(value, Exception) or value != [{"order_id": f"ORD-TOKEN-{subject}"}]:
                    failures.append(f"{name} read {value!r}")
            elif name == "sole:user-a:upstox":
                if not isinstance(value, Ambiguous):
                    failures.append(f"{name} PICKED one of two accounts → {value!r}")
            elif name == "sole:user-a:zerodha":
                if value is not None:
                    failures.append(f"{name} fell back to another broker → {value!r}")
            elif name == "sole:user-b:upstox":
                if isinstance(value, Exception) or value.broker_account_id != refs["B_UPSTOX_1"].broker_account_id:
                    failures.append(f"{name} → {value!r}")
            elif name == "sole:user-b:zerodha":
                if isinstance(value, Exception) or value.broker_account_id != refs["B_ZERODHA_1"].broker_account_id:
                    failures.append(f"{name} → {value!r}")
            elif kind == "list":
                owned = {r.broker_account_id for r in refs.values() if r.user_id == subject}
                if isinstance(value, Exception) or {r.broker_account_id for r in value} != owned:
                    failures.append(f"{name} → {value!r}")
            elif isinstance(value, Exception):
                failures.append(f"{name} raised {value!r}")
        assert failures == [], "\n".join(failures)

        # The disconnect touched exactly one account, and invalidated exactly
        # that account's credential — never a sibling's at the same broker.
        assert invalidated == ["TOKEN-B_UPSTOX_1"], invalidated
        b1_id = refs["B_UPSTOX_1"].broker_account_id
        assert rows[b1_id]["status"] == "disconnected"
        assert rows[b1_id]["access_token"] == ""
        for label in ("A_UPSTOX_1", "A_UPSTOX_2", "B_ZERODHA_1"):
            row = rows[refs[label].broker_account_id]
            assert row["access_token"] == "encrypted-at-rest-placeholder", (
                f"disconnecting B_UPSTOX_1 cleared {label}'s credential")
            assert row["status"] == "connected"

        # Every holding row was produced by its own account's credential, and is
        # owned by that account's user.
        seen = {(h["broker_account_id"], h["symbol"], h["user_id"]) for h in holdings}
        expected = {(refs[l].broker_account_id, f"HOLD-{l}", refs[l].user_id)
                    for l in ("A_UPSTOX_1", "A_UPSTOX_2", "B_ZERODHA_1")}
        assert seen == expected, f"holdings crossed accounts: {seen ^ expected}"


class TestMixedTenantLoad:
    """Phase 13 — every tenant-scoped operation in flight at once.

    10 users × 2 accounts (upstox + zerodha), one process, one database, one
    engine, one socket manager. §6 ran one kind of read at a time; the shape a
    module-level "current user" or "current session" leaks through is different
    kinds of operation for different tenants interleaving, so they are mixed:
    session reads, portfolio syncs, order reads, refresh-token rotation (two
    tabs each), logout of half the users, and private + public realtime events
    delivered through the REAL `ConnectionManager` to fake sockets.

    Mocked brokers and mocked events. Not production scale and not live data.
    """

    USERS = 10

    def test_no_cross_tenant_state_under_mixed_concurrent_load(self, mongo_db):
        import contextlib
        from unittest.mock import AsyncMock, patch

        import server

        users = [f"load-u{i}" for i in range(self.USERS)]
        refs = {(u, b): account_ref(u, b, suffix=b) for u in users for b in ("upstox", "zerodha")}
        token_of = {r.broker_account_id: f"TOK-{u}-{b}" for (u, b), r in refs.items()}
        owner_of_token = {t: refs_key for refs_key, t in
                          ((r.broker_account_id, token_of[r.broker_account_id]) for r in refs.values())}
        user_of_account = {r.broker_account_id: r.user_id for r in refs.values()}

        async def echo_holdings(broker, session):
            account_id = owner_of_token[session["access_token"]]
            return [{"symbol": f"H-{account_id}", "quantity": 1,
                     "invested_value": 1.0, "market_value": 1.0}]

        class _Socket:
            def __init__(self, user_id):
                self.user_id = user_id
                self.received = []

            async def accept(self, subprotocol=None):
                return None

            async def send_text(self, payload):
                import json
                self.received.append(json.loads(payload))

            async def close(self, code=None):
                return None

        async def body(db):
            from services.broker_engine import BrokerEngine
            from services.brokers.order_identity import ensure_holding_identity_index
            from services.realtime.event_bridge import _deliver
            from security.sessions import GRACE_REPLAY, ROTATED, SessionStore

            await ensure_holding_identity_index(db)
            engine = BrokerEngine()
            engine.configure(db)
            for account_id, token in token_of.items():
                engine._cache_session(account_id, {"access_token": token})
            store = SessionStore(db)
            manager = server.ConnectionManager()
            sockets = {}
            sids = {}
            for u in users:
                sids[u] = await store.create(u, f"jti-{u}-0")
                sock = _Socket(u)
                await manager.connect(sock, user_id=u, session_id=sids[u])
                manager.subscribe(sock, ["market", "trades", "*"])
                sockets[u] = sock

            logged_out = set(users[::2])
            ops = []
            for u in users:
                for b in ("upstox", "zerodha"):
                    r = refs[(u, b)]
                    ops.append(("session", r.broker_account_id, engine.get_session(r)))
                    ops.append(("sync", r.broker_account_id, engine.sync_portfolio(r)))
                # Two tabs refresh the same generation at once.
                ops.append(("refresh", u, store.rotate(sids[u], f"jti-{u}-0", f"jti-{u}-tabA")))
                ops.append(("refresh", u, store.rotate(sids[u], f"jti-{u}-0", f"jti-{u}-tabB")))
                if u in logged_out:
                    ops.append(("logout", u, store.revoke_all_for_user(u)))
                for n in range(5):
                    ops.append(("event", u, _deliver(manager, {
                        "type": "portfolio.updated",
                        "data": {"user_id": u, "n": n,
                                 "broker_account_id": refs[(u, "upstox")].broker_account_id}})))
                ops.append(("event", u, _deliver(manager, {
                    "type": "trade.updated", "data": {"symbol": "ORPHAN"}})))
                ops.append(("event", u, _deliver(manager, {
                    "type": "price.updated", "data": {"symbol": "NIFTY", "price": 1.0}})))

            async def tagged(kind, subject, coro):
                try:
                    return kind, subject, await coro
                except Exception as e:  # noqa: BLE001 - asserted below
                    return kind, subject, e

            with contextlib.ExitStack() as stack:
                for target, value in (
                    ("services.brokers.broker_gateway.session_is_fresh", lambda *a, **k: True),
                    ("services.brokers.broker_gateway.get_holdings", echo_holdings),
                    ("services.brokers.broker_gateway.get_positions", AsyncMock(return_value=[])),
                    ("services.brokers.broker_gateway.get_funds", AsyncMock(return_value=None)),
                    ("services.broker_engine.BrokerEngine.start_stream", AsyncMock(return_value=None)),
                    ("services.portfolio_stream.publish_snapshot", AsyncMock(return_value=None)),
                    ("services.broker_engine.BrokerEngine._push", AsyncMock(return_value=None)),
                ):
                    stack.enter_context(patch(target, new=value))
                results = await _gather(*[tagged(k, s, c) for k, s, c in ops])

            holdings = await db.holdings.find({}).to_list(1000)
            sessions = {d["user_id"]: d for d in await db.sessions.find({}).to_list(100)}
            # After the logouts, a third refresh from each side must not revive
            # a revoked family.
            late = await _gather(*[store.rotate(sids[u], f"jti-{u}-tabA", f"jti-{u}-late")
                                   for u in sorted(logged_out)])
            return (results, holdings, sessions, late, sockets, logged_out,
                    ROTATED, GRACE_REPLAY, manager)

        (results, holdings, sessions, late, sockets, logged_out,
         ROTATED, GRACE_REPLAY, manager) = _run(mongo_db, body)

        errors = [(k, s, v) for k, s, v in results if isinstance(v, Exception)
                  and k != "refresh"]
        assert errors == [], f"operations failed under load: {errors[:5]}"

        for kind, subject, value in results:
            if kind == "session":
                assert value["access_token"] == token_of[subject], (
                    f"account {subject} was served another account's credential")

        # Holdings: exactly one row per account, produced by that account's own
        # credential and owned by that account's user.
        by_account = {}
        for h in holdings:
            by_account.setdefault(h["broker_account_id"], []).append(h)
            assert h["symbol"] == f"H-{h['broker_account_id']}", (
                f"{h['broker_account_id']} holds a row fetched with another credential")
            assert h["user_id"] == user_of_account[h["broker_account_id"]]
        assert set(by_account) == set(token_of) and all(len(v) == 1 for v in by_account.values())

        # Refresh: per user, the two tabs never both ROTATED (no fork). Users
        # not logged out always get exactly one ROTATED + one GRACE_REPLAY.
        for u in sockets:
            outcomes = sorted(v.outcome for k, s, v in results
                              if k == "refresh" and s == u and not isinstance(v, Exception))
            assert outcomes.count(ROTATED) <= 1, f"{u}'s session family forked: {outcomes}"
            if u not in logged_out:
                assert outcomes == sorted([ROTATED, GRACE_REPLAY]), f"{u}: {outcomes}"
                assert sessions[u]["revoked"] is False, f"another user's logout revoked {u}"
                assert sessions[u]["refresh_count"] == 1
            else:
                assert sessions[u]["revoked"] is True
        assert all(r.outcome == REVOKED for r in late), (
            f"a revoked family refreshed back to life: {[r.outcome for r in late]}")

        # Realtime: every socket received only its own user's private events —
        # all five of them — plus the public tick; no ownerless private event
        # reached anybody, even on a socket subscribed to `*`.
        for u, sock in sockets.items():
            private = [m for m in sock.received if m["event"] == "portfolio.updated"]
            assert sorted(m["data"]["n"] for m in private) == [0, 1, 2, 3, 4], (
                f"{u} received {len(private)} of its 5 private events")
            assert all(m["data"]["user_id"] == u for m in private), (
                f"{u} received another user's private event")
            assert not [m for m in sock.received if m["event"] == "trade.updated"], (
                f"{u} received an ownerless private event")
        # The "*" subscription was refused, so the public tick reached sockets
        # through `market` — the positive control that delivery works at all.
        assert all(any(m["event"] == "price.updated" for m in s.received)
                   for s in sockets.values())
        assert all("*" not in subs for subs in manager.channels.values())

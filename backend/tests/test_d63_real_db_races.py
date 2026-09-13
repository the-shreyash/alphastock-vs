"""D6.3 (closure) — concurrency isolation against a REAL MongoDB.

WHY THIS FILE EXISTS
--------------------
D6.3 shipped with an explicit hole, recorded as **LIM-D6.3-2**:

    "A real database race was not reproduced. `FakeDB` is single-threaded with
    no await point inside an operation, so concurrent-write interleavings
    cannot be constructed against it. The §15 tests exercise concurrent
    *application* paths over shared state, which is real, and say nothing about
    Mongo-level atomicity. Rather than write a test that cannot fail, this is
    recorded."

That was the right call at the time and the wrong state to leave the invariant
in: "no cross-tenant race was found" and "no cross-tenant race was looked for
with an instrument that could see one" are very different claims, and the D6.3
report could only make the second. LIM-D6.2-6 (the `SessionStore.rotate()`
read/decide/write TOCTOU) was parked for the same reason — "not constructible
against `FakeDB`".

This file closes that gap. It drives the **real production code paths** against
a **real `mongod`**, with the interleaving forced deterministically, and asks
the only question D6.3 owns:

    When a real database race happens, can it move a value across a tenant
    boundary?

THE ANSWER, AND WHY IT IS NOT "NO RACES EXIST"
----------------------------------------------
Three real races reproduce here. **None of them crosses a tenant boundary.**
All three are single-owner integrity defects, and they are recorded as such
rather than quietly passed over, because a suite that reports "isolation holds"
while stepping around three reproducible races is the kind of green that D6.3
exists to distrust:

  * `SessionStore.rotate()`  — LIM-D6.2-6, reproduced (§2). Frozen by the brief;
    proven tenant-contained, not fixed.
  * `update_paper_balance()` — read/modify/write with `$set`: a lost update (§1).
  * `close_paper_trade()`    — status TOCTOU: one trade closes N times (§4).

The last two are asserted in their *correct* form under `xfail(strict=True)`,
so they are red-by-design today and turn the suite red **again** the moment
someone fixes them without removing the marker. They are open defects with a
reproduction, not documentation.

HOW THE INTERLEAVING IS FORCED
------------------------------
`_Gate` wraps a real motor collection and parks every `update_one` at an
`asyncio.Barrier` until N callers have arrived, then releases them together.
The database, the documents, the filters and the update semantics are all real;
only the *scheduling* is pinned. That matters because the alternative —
`asyncio.gather` and hope — is exactly the flaky test this repo keeps refusing
to write: §5's probe of the double-close showed 3/3 closes succeeding on one
scheduling and 1/3 on another, purely from where `await` happened to land.

Every barrier is bounded by `asyncio.wait_for`. This suite must **fail** when a
control regresses, never hang: D6.2 and D6.3 were each bitten once by a test
that blocked forever instead of going red, and a barrier is the single easiest
way to reintroduce that.

THE FALSIFYING TWINS COME FIRST
-------------------------------
`TestTheHarnessCanSeeARace` is not decoration. Every "the race did not cross
tenants" assertion below is worthless if the harness cannot observe a race at
all — that is the precise trap LIM-D6.3-2 refused to walk into. §1 therefore
proves, against the same `mongod` and the same gate, that this instrument
*does* detect a lost update and *does* distinguish a compare-and-swap from a
read/decide/write. If §1 goes green-by-accident the rest of the file is void,
so it is written to fail loudly instead.
"""

import asyncio
import os
import uuid

import pytest
from bson import ObjectId

pymongo = pytest.importorskip("pymongo", reason="pymongo is required for the real-DB race suite")
motor_asyncio = pytest.importorskip("motor.motor_asyncio", reason="motor is required for the real-DB race suite")

from security.sessions import (  # noqa: E402
    GRACE_REPLAY, REUSE_DETECTED, ROTATED, SessionStore,
)
from _accounts import account_ref  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")

#: Barrier/settle budget. Generous enough for a loaded laptop, short enough that
#: a regression is a red test in seconds rather than a wedged CI job.
TIMEOUT = 10.0


def _mongo_is_reachable() -> bool:
    """True when a real `mongod` answers a ping at ``MONGO_URL``.

    Checked once, at import, with a short server-selection timeout. Without a
    live server every test in this file would fail for an environmental reason
    and drown the signal it exists to produce, so the module skips instead.
    """
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
        f"needs a real MongoDB at {MONGO_URL} (LIM-D6.3-2 closure suite); "
        "start one with `brew services start mongodb-community` or set MONGO_URL",
        allow_module_level=True,
    )

pytestmark = pytest.mark.requires_db


# --------------------------------------------------------------------------- #
# Harness                                                                      #
# --------------------------------------------------------------------------- #
class _Gate:
    """Parks `update_one` at a barrier so a read/decide/write interleaves for real.

    Wraps a live motor collection and forwards everything else untouched, so the
    code under test is talking to real Mongo through its real handle. Only the
    write is delayed, and only until ``parties`` callers have reached it — which
    is what turns "both readers saw the old value" from a scheduling accident
    into a property of the test.

    ``asyncio.Barrier`` (3.11+) rather than an ``Event`` plus ``sleep``: a sleep
    encodes a guess about how long a round trip takes, and this suite is meant
    to be run on machines that are busier than the one it was written on.
    """

    def __init__(self, collection, barrier):
        self._collection = collection
        self._barrier = barrier

    def __getattr__(self, name):
        return getattr(self._collection, name)

    async def update_one(self, *args, **kwargs):
        if self._barrier is not None:
            await asyncio.wait_for(self._barrier.wait(), timeout=TIMEOUT)
        return await self._collection.update_one(*args, **kwargs)


class _GatedDb:
    """A database handle whose named collections hand out gated writes.

    Collections not named in ``gated`` are returned as-is, so a path that writes
    to two collections can have exactly one of them pinned.
    """

    def __init__(self, db, gated):
        self._db = db
        self._gated = gated

    def __getattr__(self, name):
        collection = getattr(self._db, name)
        if name in self._gated:
            return _Gate(collection, self._gated[name])
        return collection

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture()
def mongo_db():
    """A throwaway database, dropped on the way out.

    Named per-test with a uuid so a crashed run cannot poison the next one, and
    so this suite can never be pointed at `alpha_stock_db` by an inherited
    environment variable. The client is deliberately NOT created here: motor
    binds a client to the running loop, and every test below drives its own
    `asyncio.run`. The fixture yields the name; the test opens and closes its
    own client inside its own loop.
    """
    name = f"alpha_stock_d63_race_{uuid.uuid4().hex[:12]}"
    yield name
    client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1500)
    try:
        client.drop_database(name)
    finally:
        client.close()


def _run(db_name, body):
    """Run one async test body with a real motor client bound to this loop."""

    async def _main():
        client = motor_asyncio.AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=2000)
        try:
            return await body(client[db_name])
        finally:
            client.close()

    return asyncio.run(_main())


async def _gather(*coros):
    """`asyncio.gather` with a deadline, so a wedged barrier is a failure."""
    return await asyncio.wait_for(asyncio.gather(*coros, return_exceptions=True), timeout=TIMEOUT)


# =========================================================================== #
# §1 — the harness itself                                                     #
# =========================================================================== #
class TestTheHarnessCanSeeARace:
    """Falsifying twins for the whole file.

    If these two do not behave as stated, every "no cross-tenant leak" result
    below is unfalsifiable and must not be believed.
    """

    def test_a_read_modify_write_really_does_lose_an_update(self, mongo_db):
        """The instrument detects a lost update.

        REWRITTEN IN D6.7, AND WHY IT NO LONGER DRIVES PRODUCTION CODE
        -------------------------------------------------------------
        This check used to run `update_paper_balance` — which really did read
        `paper_capital`, add in Python and `$set` the absolute result — and
        assert that four concurrent ₹100 credits collapsed into one. Its own
        failure message named the day this would stop working: *"If this is
        100400.0 the write is now atomic and this harness check ... needs
        updating."* D6.7 made it atomic (`$inc`), so that day is today.

        The twin is still needed and is now written the same way the CAS twin
        beside it always was: as **a property of the database and the gate**,
        exercised through a read/modify/write defined right here. That is
        strictly better than what it replaced. Pinning the harness's credibility
        to a defect in production code means the instrument stops being
        falsifiable the moment the defect is fixed — and worse, it creates an
        incentive to leave the defect in place so the suite stays green.

        `FakeDB` cannot express any of this: it is single-threaded with no await
        point inside an operation, which is the whole reason this file exists.
        """

        async def body(db):
            user_id = ObjectId()
            await db.users.insert_one({"_id": user_id, "paper_capital": 100000.0})

            barrier = asyncio.Barrier(4)
            gated = _GatedDb(db, {"users": barrier})

            async def lossy_credit(amount):
                """The pre-D6.7 shape of `update_paper_balance`, verbatim."""
                row = await gated.users.find_one({"_id": user_id})
                current = row.get("paper_capital", 100000.0)
                await gated.users.update_one(
                    {"_id": user_id},
                    {"$set": {"paper_capital": round(current + amount, 2)}},
                )

            await _gather(*[lossy_credit(100.0) for _ in range(4)])

            row = await db.users.find_one({"_id": user_id})
            return row["paper_capital"]

        balance = _run(mongo_db, body)
        assert balance == 100100.0, (
            f"expected the lost-update race to collapse four +100 credits into one "
            f"(100100.0), got {balance}. If this is 100400.0 the barrier is not "
            f"overlapping the readers and every race result in this file is "
            f"meaningless."
        )

    def test_the_production_credit_path_no_longer_loses_an_update(self, mongo_db):
        """The same gate, the same four callers, against real production code.

        Paired deliberately with the twin above: identical barrier, identical
        database, identical four-caller shape — the only difference is that this
        one calls `update_paper_balance`. One goes red when the *instrument*
        breaks; this one goes red when the *fix* is reverted. Neither can stand
        in for the other, and reading them together is what makes "the race is
        closed" a measured claim rather than an absent failure.
        """
        import services.paper_trade as paper_trade

        async def body(db):
            user_id = ObjectId()
            await db.users.insert_one({"_id": user_id, "paper_capital": 100000.0})
            barrier = asyncio.Barrier(4)
            gated = _GatedDb(db, {"users": barrier})
            await _gather(*[
                paper_trade.update_paper_balance(str(user_id), 100.0, gated)
                for _ in range(4)
            ])
            row = await db.users.find_one({"_id": user_id})
            return row["paper_capital"]

        assert _run(mongo_db, body) == 100400.0

    def test_a_compare_and_swap_admits_exactly_one_writer(self, mongo_db):
        """The instrument distinguishes a CAS from a read/decide/write.

        Same barrier, same database, same two-writer shape as the `rotate()`
        reproduction in §2 — but the write states the value it expected to
        find. Mongo matches it for exactly one writer and the other sees
        `modified_count == 0`.

        Without this test, §2's "both writers won" would be indistinguishable
        from "the barrier never actually overlapped them", which is the failure
        mode that makes a concurrency suite worthless.

        It is also the shape LIM-D6.2-6's eventual fix takes. It is written here
        as a *property of the database*, not applied to `SessionStore` — D6.2 is
        frozen and the brief forbids redesigning `rotate()`.
        """

        async def body(db):
            doc_id = ObjectId()
            await db.sessions.insert_one({"_id": doc_id, "current_jti": "jti-1", "revoked": False})

            barrier = asyncio.Barrier(2)

            async def cas(new_jti):
                found = await db.sessions.find_one({"_id": doc_id})
                if found["current_jti"] != "jti-1":
                    return "rejected-at-read"
                await asyncio.wait_for(barrier.wait(), timeout=TIMEOUT)
                result = await db.sessions.update_one(
                    {"_id": doc_id, "current_jti": "jti-1", "revoked": False},
                    {"$set": {"current_jti": new_jti}},
                )
                return "won" if result.modified_count else "lost"

            return await _gather(cas("jti-2A"), cas("jti-2B"))

        outcomes = _run(mongo_db, body)
        assert sorted(outcomes) == ["lost", "won"], (
            f"a compare-and-swap must admit exactly one of two racing writers; got {outcomes}. "
            f"If both won, the barrier is not overlapping them and every race result in this "
            f"file is meaningless."
        )


# =========================================================================== #
# §2 — LIM-D6.2-6: reproduced by D6.3, CLOSED by D6.7                          #
# =========================================================================== #
class TestSessionRotationRaceIsTenantContained:
    """The `rotate()` TOCTOU, against a database that can actually exhibit it.

    D6.2 reasoned that the race fails closed and D6.3 reasoned that it leaks
    nothing across tenants. Neither could demonstrate it; these tests did — and
    what they demonstrated was worse than "untidy". Both racing refreshes were
    accepted, so the family held two live tokens, and the client that lost held
    one that was neither current nor the single retired generation. Its next
    refresh was therefore indistinguishable from replay and **revoked the whole
    family**: a user signed out of every device because two tabs refreshed in
    the same instant.

    D6.7 made the write a compare-and-swap (`security/sessions.py`), so the
    tests below now assert the closed behaviour. The class name is kept: tenant
    containment was and remains the property D6.3 owned here, and it is still
    asserted — a race that is gone cannot cross a boundary, but the bystander
    control is what proves the *fix* did not start touching other families
    either.
    """

    @staticmethod
    async def _race(db, session_id, presented, parties=("X", "Y")):
        barrier = asyncio.Barrier(len(parties))
        gated = SessionStore(_GatedDb(db, {"sessions": barrier}))
        return await _gather(*[gated.rotate(session_id, presented, f"{presented}-next-{suffix}") for suffix in parties])

    def test_exactly_one_of_two_concurrent_rotations_is_accepted(self, mongo_db):
        """LIM-D6.2-6, closed. One `ROTATED`, one `GRACE_REPLAY`, one live token.

        The write now restates `current_jti`, so Mongo admits exactly one of the
        two racing rotations under the document lock. The loser is not invented a
        new answer for: the winner has just set `previous_jti` to the very token
        the loser presented, so it lands in D6.2's existing two-tab grace path
        and is handed the winner's live token. That vocabulary was built for this
        shape; the race was simply reaching it by a route that revoked instead.

        `refresh_count == 1` is the load-bearing half of the assertion. It is the
        one field that counts *logical* rotations, so a fix that merely made both
        writers agree on a final `current_jti` — without making one of them lose
        — would still show 2 here.
        """

        async def body(db):
            store = SessionStore(db)
            session_id = await store.create("user-A", "jti-1")
            results = await self._race(db, session_id, "jti-1")
            doc = await db.sessions.find_one({"session_id": session_id})
            return [r.outcome for r in results], doc, [r.issued_jti for r in results]

        outcomes, doc, issued = _run(mongo_db, body)
        assert sorted(outcomes) == [GRACE_REPLAY, ROTATED], (
            f"a compare-and-swap must admit exactly one of two racing rotations; got "
            f"{outcomes}. Two ROTATED is LIM-D6.2-6 reopened."
        )
        assert doc["refresh_count"] == 1, (
            f"one logical rotation must be counted once, got {doc['refresh_count']}"
        )
        assert doc["current_jti"] in ("jti-1-next-X", "jti-1-next-Y")
        assert doc["revoked"] is False, "the race itself must not revoke the family"
        assert set(issued) == {doc["current_jti"]}, (
            f"both clients must be handed the ONE live token; got {issued} against a "
            f"stored current_jti of {doc['current_jti']}. A client sent away with a "
            f"token the family does not hold is the old defect wearing a new outcome."
        )

    def test_a_token_the_family_never_issued_still_fails_closed_after_a_race(self, mongo_db):
        """Reuse detection is not softened by the fix. Renamed in D6.7.

        WHAT THIS TEST USED TO BE, AND WHY THE NAME HAD TO CHANGE
        --------------------------------------------------------
        It was `test_the_client_that_lost_the_race_fails_closed`, and it was the
        "fails closed" half of the D6.2 verdict: under the TOCTOU, the losing
        client really was handed a live-looking token the family did not hold, so
        its next refresh revoked the family. That was the defect's cost, stated
        honestly.

        After D6.7 there is no such client — the loser gets `GRACE_REPLAY` and
        the winner's real token — so the jti this test constructs is now simply
        one the family never issued. The assertion still holds and is still worth
        holding, but it is a different claim, and leaving the old name on it
        would have left the suite reporting a scenario that can no longer occur.

        What it proves now: making `rotate()` atomic did **not** buy the fix by
        widening the grace window. A genuinely unknown token presented against a
        live family is still theft, and still kills the family.
        """

        async def body(db):
            store = SessionStore(db)
            session_id = await store.create("user-A", "jti-1")
            await self._race(db, session_id, "jti-1")

            doc = await db.sessions.find_one({"session_id": session_id})
            # The jti the losing rotation *would* have installed pre-D6.7. The
            # family never issued it, which is exactly what makes it the right
            # probe for reuse detection.
            unissued = ("jti-1-next-Y" if doc["current_jti"] == "jti-1-next-X"
                        else "jti-1-next-X")
            result = await store.rotate(session_id, unissued, "jti-3")
            after = await db.sessions.find_one({"session_id": session_id})
            return result.outcome, after

        outcome, after = _run(mongo_db, body)
        assert outcome == REUSE_DETECTED, f"the losing token must be refused, got {outcome}"
        assert after["revoked"] is True
        assert after["revoked_reason"] == "refresh_reuse_detected"

    def test_the_race_and_its_fallout_never_touch_another_users_session(self, mongo_db):
        """**The D6.3 verdict.** The blast radius is one family.

        Bystander B's session document is captured field-for-field before A's
        race and compared after both the race *and* the family-revoking refresh
        that follows it. `session_id` is a 24-byte `secrets.token_urlsafe`, and
        every write in the path filters on it, so there is no key under which A's
        rotation could address B's row — but the assertion is on the stored
        document rather than on that argument.
        """

        async def body(db):
            store = SessionStore(db)
            a_session = await store.create("user-A", "jti-1")
            b_session = await store.create("user-B", "b-jti-1")

            before = await db.sessions.find_one({"session_id": b_session})
            await self._race(db, a_session, "jti-1")

            doc = await db.sessions.find_one({"session_id": a_session})
            unissued = ("jti-1-next-Y" if doc["current_jti"] == "jti-1-next-X"
                        else "jti-1-next-X")
            await store.rotate(a_session, unissued, "jti-3")

            after = await db.sessions.find_one({"session_id": b_session})
            b_still_works = await store.rotate(b_session, "b-jti-1", "b-jti-2")
            return before, after, b_still_works.outcome

        before, after, b_outcome = _run(mongo_db, body)
        assert before == after, (
            "user B's session document changed while user A's refresh family raced and was "
            f"revoked.\nbefore: {before}\nafter:  {after}"
        )
        assert after["revoked"] is False, "B's family was revoked by A's reuse detection"
        assert b_outcome == ROTATED, (
            f"B could no longer refresh after A's race, got {b_outcome} — the owner-positive "
            f"control: B's session must still be usable, or 'unchanged' proves nothing."
        )


# =========================================================================== #
# §3 — concurrent writes across tenants                                        #
# =========================================================================== #
class TestConcurrentWritesDoNotCrossTenants:
    def test_two_users_upserting_the_same_broker_order_id_get_their_own_row(self, mongo_db):
        """§2 UPSERT, for real. Order ids are per-account sequences and DO collide.

        The hermetic twin of this test runs against `FakeDB`, where an upsert is a
        list scan and a filter is a dict comparison. Here the filter is executed by
        Mongo, concurrently, on one collection with a real index — and the two
        writes must still land in two documents owned by their own users.
        """
        from services.broker_engine import broker_engine

        async def body(db):
            broker_engine.db = db
            a_id, b_id = str(ObjectId()), str(ObjectId())
            order = {"order_id": "SAME-ID", "status": "COMPLETE"}

            barrier = asyncio.Barrier(2)
            broker_engine.db = _GatedDb(db, {"orders": barrier})
            await _gather(
                broker_engine._record_order(account_ref(a_id, "zerodha"), {**order, "symbol": "RELIANCE"}),
                broker_engine._record_order(account_ref(b_id, "zerodha"), {**order, "symbol": "TCS"}),
            )
            broker_engine.db = db
            rows = await db.orders.find({}).to_list(10)
            return a_id, b_id, rows

        a_id, b_id, rows = _run(mongo_db, body)
        assert len(rows) == 2, f"the second user's upsert overwrote the first's row: {rows}"
        by_user = {r["user_id"]: r["symbol"] for r in rows}
        assert by_user[a_id] == "RELIANCE"
        assert by_user[b_id] == "TCS"

    def test_a_concurrent_cross_user_close_matches_nothing_and_leaves_the_trade_open(self, mongo_db, monkeypatch):
        """§3 IDOR under concurrency. B fires A's trade id three times, in parallel.

        The owner-positive control runs in the same test: after B's attack, A
        closes the trade themselves and it works. Without that, "still OPEN" is
        also what a broken fixture produces.
        """
        import services.paper_trade as paper_trade
        import services.real_market as real_market

        async def body(db):
            async def fake_quote(symbol):
                return {"price": 150.0}

            # Restored by monkeypatch: a bare assignment leaked this fake quote
            # into every later test in the process (D6.7 re-verification).
            monkeypatch.setattr(real_market, "fetch_real_stock_quote", fake_quote)

            a_id, b_id = str(ObjectId()), str(ObjectId())
            trade_id = ObjectId()
            await db.users.insert_one({"_id": ObjectId(a_id), "paper_capital": 100000.0})
            await db.trades.insert_one(
                {
                    "_id": trade_id,
                    "user_id": a_id,
                    "symbol": "RELIANCE",
                    "type": "BUY",
                    "entry_price": 100.0,
                    "quantity": 10,
                    "status": "OPEN",
                    "is_paper": True,
                }
            )

            attacks = await _gather(*[paper_trade.close_paper_trade(str(trade_id), b_id, db) for _ in range(3)])
            during = await db.trades.find_one({"_id": trade_id})

            owner = await paper_trade.close_paper_trade(str(trade_id), a_id, db)
            after = await db.trades.find_one({"_id": trade_id})
            return attacks, during, owner, after

        attacks, during, owner, after = _run(mongo_db, body)
        assert all(
            isinstance(a, ValueError) for a in attacks
        ), f"a concurrent cross-user close was not refused: {attacks}"
        assert during["status"] == "OPEN", "B's concurrent attack mutated A's trade"
        assert during["user_id"] == after["user_id"], "the row changed owner"
        assert owner["pnl"] == 500.0, "owner-positive control: A must be able to close their own trade"
        assert after["status"] == "CLOSED"

    def test_concurrent_balance_writes_never_move_another_users_balance(self, mongo_db):
        """§15. A's credits and B's debits, interleaved on one collection.

        `update_paper_balance` loses updates *within* one owner (§1, §4) — that is
        a known, reproduced defect. What must not happen, and does not, is a
        credit landing on the wrong `_id`: A never goes down and B never goes up,
        however the reads and writes interleave.
        """
        import services.paper_trade as paper_trade

        async def body(db):
            a_id, b_id = ObjectId(), ObjectId()
            await db.users.insert_many(
                [
                    {"_id": a_id, "paper_capital": 100000.0},
                    {"_id": b_id, "paper_capital": 100000.0},
                ]
            )
            barrier = asyncio.Barrier(8)
            gated = _GatedDb(db, {"users": barrier})
            await _gather(
                *[paper_trade.update_paper_balance(str(a_id), 50.0, gated) for _ in range(4)],
                *[paper_trade.update_paper_balance(str(b_id), -777.0, gated) for _ in range(4)],
            )
            a_row = await db.users.find_one({"_id": a_id})
            b_row = await db.users.find_one({"_id": b_id})
            return a_row["paper_capital"], b_row["paper_capital"]

        a_balance, b_balance = _run(mongo_db, body)
        assert a_balance > 100000.0, f"A was debited by B's concurrent write: {a_balance}"
        assert b_balance < 100000.0, f"B was credited by A's concurrent write: {b_balance}"
        assert a_balance % 50 == 0, f"A's balance carries B's 777 debit: {a_balance}"


# =========================================================================== #
# §4 — single-owner integrity races found on the way                           #
# =========================================================================== #
class TestSingleOwnerIntegrityRaces:
    """CLOSED IN D6.7. Kept as the regression, with the history intact.

    D6.3 recorded two reproducible single-owner races here under
    `xfail(strict=True)` — red-by-design, so that `strict` would turn the
    unexpected pass into a failure and the fix could not land without deleting
    the marker and the limitation together. That is exactly what happened: D6.7
    made `update_paper_balance` a `$inc` and `close_paper_trade` a status-
    conditional compare-and-swap, both tests began to pass, `strict` caught it,
    and the markers are gone.

    The tests themselves are unchanged and stay — a race that was reproduced
    once will be reintroduced by the next well-meaning refactor of either write,
    and this is the only instrument in the repository that can see it.

    Neither ever crossed the tenant boundary, which is why D6.3 was right to
    scope them out and record them rather than fix them mid-sprint. Both belong
    to the owner's own account.
    """

    def test_concurrent_credits_should_all_be_applied(self, mongo_db):
        import services.paper_trade as paper_trade

        async def body(db):
            user_id = ObjectId()
            await db.users.insert_one({"_id": user_id, "paper_capital": 100000.0})
            barrier = asyncio.Barrier(4)
            gated = _GatedDb(db, {"users": barrier})
            await _gather(*[paper_trade.update_paper_balance(str(user_id), 100.0, gated) for _ in range(4)])
            row = await db.users.find_one({"_id": user_id})
            return row["paper_capital"]

        assert _run(mongo_db, body) == 100400.0

    def test_a_paper_trade_should_only_close_once(self, mongo_db, monkeypatch):
        """The write must be conditional on the status the decision was made against.

        Note the interaction, which is why the two markers had to clear
        together: the double credit was *masked* by the lost update above.
        Fixing `update_paper_balance` to `$inc` on its own would have turned a
        hidden double-close into a real, visible over-credit — the balance would
        have been repaired into paying out three times for one position.
        """
        import services.paper_trade as paper_trade
        import services.real_market as real_market

        async def body(db):
            async def fake_quote(symbol):
                return {"price": 150.0}

            # Restored by monkeypatch: a bare assignment leaked this fake quote
            # into every later test in the process (D6.7 re-verification).
            monkeypatch.setattr(real_market, "fetch_real_stock_quote", fake_quote)

            user_id = ObjectId()
            trade_id = ObjectId()
            await db.users.insert_one({"_id": user_id, "paper_capital": 100000.0})
            await db.trades.insert_one(
                {
                    "_id": trade_id,
                    "user_id": str(user_id),
                    "symbol": "RELIANCE",
                    "type": "BUY",
                    "entry_price": 100.0,
                    "quantity": 10,
                    "status": "OPEN",
                    "is_paper": True,
                }
            )
            barrier = asyncio.Barrier(3)
            gated = _GatedDb(db, {"trades": barrier})
            results = await _gather(
                *[paper_trade.close_paper_trade(str(trade_id), str(user_id), gated) for _ in range(3)]
            )
            return sum(1 for r in results if not isinstance(r, Exception))

        assert _run(mongo_db, body) == 1

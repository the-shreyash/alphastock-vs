"""Graceful shutdown must actually release the scheduler lease.

Found during deployment preparation by stopping a production-mode container:
`server.shutdown()` logged "Releasing the scheduler lease failed: module
'infrastructure.tasks' has no attribute 'cancel'". `LeaderLease.stop()` called a
module-level wrapper that did not exist, so the release never ran and a rolling
deploy's successor waited out the full lease TTL (45 s + up to one 15 s
campaign tick) with no scheduler — no `trade_monitor` stop-loss/target checks
during market hours, on every deploy.

Nothing caught it because no test ever called `stop()`: the D6.7 lease tests
exercise acquire / renew / release directly, and they only run against a live
MongoDB. This file is hermetic so it always runs.
"""
import asyncio

from infrastructure import tasks
from infrastructure.leader import COLLECTION, LeaderLease


class _Result:
    def __init__(self, modified):
        self.modified_count = modified


class _Leases:
    """The two operations `release()` performs, recorded — nothing more."""

    def __init__(self):
        self.updates = []

    async def update_one(self, flt, update):
        self.updates.append((flt, update))
        return _Result(1)


class _Db(dict):
    def __init__(self):
        super().__init__({COLLECTION: _Leases()})


def test_stop_cancels_renewal_and_releases_the_lease():
    db = _Db()

    async def scenario():
        lease = LeaderLease(db, "scheduler", identity="proc-a")
        lease._is_leader = True          # as after a won acquire()
        lease.start()
        renew_task = lease._renew_task
        await asyncio.sleep(0)           # let the loop start
        await lease.stop()               # raised AttributeError before the fix
        return lease, renew_task

    try:
        lease, renew_task = asyncio.run(scenario())
    finally:
        tasks.registry.reset_for_tests()

    assert renew_task is not None and renew_task.cancelled(), (
        "stop() must cancel the renewal loop, or a shut-down process keeps "
        "renewing a lease it no longer intends to use")
    assert lease.is_leader is False
    flt, update = db[COLLECTION].updates[-1]
    assert flt == {"_id": "scheduler", "holder": "proc-a"}, (
        "release must be conditional on ownership")
    assert update["$set"]["holder"] is None, "the lease was not released"


def test_module_level_cancel_targets_one_named_task():
    async def scenario():
        async def forever():
            await asyncio.Event().wait()

        keep = tasks.spawn("keep-me", forever())
        drop = tasks.spawn("drop-me", forever())
        existed = await tasks.cancel("drop-me")
        missing = await tasks.cancel("never-spawned")
        state = (drop.cancelled(), keep.done())
        await tasks.cancel_all()
        return existed, missing, state

    try:
        existed, missing, (drop_cancelled, keep_done) = asyncio.run(scenario())
    finally:
        tasks.registry.reset_for_tests()

    assert existed is True and missing is False
    assert drop_cancelled and not keep_done

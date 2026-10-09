"""Single-leader election over MongoDB (D6.7 / A2).

THE DEFECT THIS MODULE EXISTS TO CLOSE
--------------------------------------
`server.py`'s startup handler calls `services.scheduler.setup_scheduler()`
unconditionally. Every uvicorn worker is a separate OS process with its own
event loop, so N workers register N copies of the six cron jobs — and one of
those jobs, `trade_monitor`, reaches `trading_engine.run_cycle`, which calls
`broker_engine.place_order` on a stop-loss or target hit. **Two schedulers means
two market exit orders for one position, in a live brokerage account, with real
money.**

Before D6.7 the only thing standing between that and production was a log line
in `backend/docker/entrypoint.sh`. It was inadequate in two specific ways, and
the second is the one that matters:

* it fires on `WEB_CONCURRENCY > 1` only, so scaling by *replicas* — two
  containers, each correctly running one worker — produces the identical
  duplicate-order defect and emits no warning at all;
* it lives in the Docker entrypoint, so `uvicorn server:app --workers 4` on a
  host, in CI, or in a developer's terminal never prints it.

A warning is also the wrong shape for this. The operator cannot act on it at the
moment it matters (04:00 on a Tuesday, when a stop is hit), and "the deployment
is one process by decree" is a decree no code enforces.

WHY A LEASE, AND WHY IN MONGO
-----------------------------
The requirement is exactly one holder at a time across processes that share
nothing but a database. That is a lock, and the smallest correct lock this
platform can build is the one it already has durable storage for.

**Mongo, not Redis.** Redis is optional on this deployment (`REDIS_URL` may be
unset; `services/cache.py` degrades to no-ops) and the scheduler must not become
the one subsystem that silently stops electing a leader when the cache is down.
Mongo is not optional — no leader could run a job without it anyway — so making
the lock depend on it adds no new failure mode. It also means a lock holder and
the work it guards fail together, which is the property that matters: a leader
that cannot reach the database cannot place an order either.

**A lease, not a lock.** A plain lock needs the holder to release it; a process
that is SIGKILLed never does, and the platform would have no scheduler until an
operator cleared a row by hand. A lease expires on its own. The cost is that
correctness now depends on a clock, which is why the renewal interval is a small
fraction of the TTL (see `RENEW_EVERY_SECONDS`) — a leader must renew several
times inside one lease, so a single slow renewal cannot orphan it.

**`find_one_and_update` with an upsert, not find-then-write.** The whole point
is a compare-and-swap: the filter states the condition the caller believes holds
(the lease is unheld, expired, or already mine), and the server applies it
atomically to the one document. `find_one` followed by `update_one` is the
read-modify-write pattern D6.7 exists to remove, and writing the lock that way
would make the lock itself the race.

WHAT THIS DOES NOT PROMISE
--------------------------
**It is not a fencing token.** Between a leader losing its lease (a long GC
pause, a partitioned network) and noticing, a new leader can be elected while
the old one still believes it holds the lock. A lease bounds that window; it
does not eliminate it. Distributed locks built on a TTL cannot, and claiming
otherwise is how the second exit order gets placed anyway.

That residual window is why this module is **not** the only guard on the order
path. `services/trading_engine.py` claims each trade's exit with a per-trade
compare-and-swap before it places anything, so a duplicate exit is impossible
even with two processes that both believe they lead. The lease keeps duplicate
*work* (and duplicate notifications, reports and snapshots) from happening at
all; the CAS keeps duplicate *orders* from happening even when the lease is
wrong. Neither substitutes for the other.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from infrastructure.mongo_errors import is_duplicate_key

logger = logging.getLogger(__name__)

#: Collection holding one document per named lease.
COLLECTION = "leader_leases"

#: How long a claim is good for without renewal. Long enough to survive an event
#: loop blocked by a slow cycle, short enough that a killed leader's work resumes
#: within one lease rather than at the next deploy.
LEASE_TTL_SECONDS = 45

#: Renewal cadence. Deliberately a third of the TTL: a leader gets three chances
#: to renew inside one lease, so one slow round trip never costs it leadership.
RENEW_EVERY_SECONDS = 15

#: The default lease name. Named rather than implicit so a future second
#: single-leader subsystem takes its own lease instead of contending for this one.
SCHEDULER_LEASE = "scheduler"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def instance_id() -> str:
    """A human-readable, unique-per-process identity for the lease holder.

    Host and pid make an orphaned lease diagnosable from the database alone
    ("which box is holding it?"); the random suffix keeps two processes that
    share a pid namespace — two containers from one image — distinguishable.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class LeaderLease:
    """A renewable, expiring claim on one named responsibility.

    Instantiate per process and call :meth:`acquire`; if it returns True this
    process is the leader and must call :meth:`start_renewing` to stay so.
    """

    def __init__(self, db, name: str = SCHEDULER_LEASE, *,
                 identity: Optional[str] = None,
                 ttl_seconds: int = LEASE_TTL_SECONDS):
        self._db = db
        self._name = name
        self._identity = identity or instance_id()
        self._ttl = int(ttl_seconds)
        self._is_leader = False
        self._renew_task: Optional[asyncio.Task] = None

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_leader(self) -> bool:
        """Whether this process currently believes it holds the lease.

        "Believes" is the honest word and is not hedging: see the module
        docstring on fencing. A caller that must not act twice needs its own
        idempotence, not this flag.
        """
        return self._is_leader

    # -- election ----------------------------------------------------------- #
    async def acquire(self) -> bool:
        """Claim the lease if it is free, expired, or already ours.

        One atomic compare-and-swap. The filter is the entire safety argument:
        the write lands only on a document that is unheld, whose lease has
        already expired, or that this same process already holds. Any other
        state means somebody else leads, the filter matches nothing, and the
        upsert would collide with the unique `_id` — which is caught and
        reported as "not the leader" rather than raised.
        """
        if self._db is None:
            return False
        now = _now()
        expires = now + timedelta(seconds=self._ttl)
        try:
            await self._db[COLLECTION].find_one_and_update(
                {
                    "_id": self._name,
                    "$or": [
                        {"holder": None},
                        {"expires_at": {"$lte": now}},
                        {"holder": self._identity},
                    ],
                },
                {"$set": {"holder": self._identity, "expires_at": expires,
                          "acquired_at": now.isoformat()},
                 "$inc": {"term": 1}},
                upsert=True,
            )
        except Exception as e:
            # A DuplicateKeyError here is the *expected* outcome of losing the
            # election: the filter did not match (somebody else holds a live
            # lease), so the upsert tried to insert a second `_id` and Mongo
            # refused. Anything else — an unreachable database — must also fail
            # closed, because a process that cannot verify it leads must not act
            # as though it does.
            if not is_duplicate_key(e):
                logger.warning("Leader election for %r failed: %s", self._name, e)
            self._is_leader = False
            return False
        self._is_leader = True
        return True

    async def renew(self) -> bool:
        """Extend our own lease. Returns False if we no longer hold it.

        Conditioned on `holder == identity`, so a process that lost the lease
        while it was stalled cannot extend somebody else's claim back to itself.
        """
        if self._db is None:
            return False
        result = await self._db[COLLECTION].update_one(
            {"_id": self._name, "holder": self._identity},
            {"$set": {"expires_at": _now() + timedelta(seconds=self._ttl)}},
        )
        held = bool(getattr(result, "matched_count", 0))
        if not held and self._is_leader:
            logger.error(
                "Lost the %r lease: the lock is no longer held by %s. Leader-only "
                "work stops in this process.", self._name, self._identity)
        self._is_leader = held
        return held

    async def release(self) -> bool:
        """Give up the lease so another process can take it immediately.

        Conditional on ownership, so a late release from a process that already
        lost the lease cannot unseat the current leader. Best-effort by design:
        a lease nobody releases simply expires.
        """
        self._is_leader = False
        if self._db is None:
            return False
        try:
            result = await self._db[COLLECTION].update_one(
                {"_id": self._name, "holder": self._identity},
                {"$set": {"holder": None, "expires_at": _now()}},
            )
            return bool(getattr(result, "modified_count", 0))
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Releasing the %r lease failed: %s", self._name, e)
            return False

    # -- maintenance loop ---------------------------------------------------- #
    def start(self) -> None:
        """Run the election continuously in the background.

        ONE LOOP, TWO JOBS, AND WHY IT IS NOT TWO LOOPS
        -----------------------------------------------
        A leader renews; a follower campaigns. Both happen on the same cadence
        and are mutually exclusive, so they are one loop with a branch. The
        alternative — elect once at startup and stop — has a failure mode that
        is worse than the one this module was written for: when the leader dies,
        the survivors have already decided they are followers and the platform
        runs with **no** scheduler at all until somebody notices. A follower that
        keeps campaigning takes over within one lease.

        Registered through `infrastructure.tasks` for the reason that module
        exists: `asyncio` holds only a weak reference to a bare task, and a
        leader whose renewal loop was garbage-collected keeps believing it leads
        while its lease quietly expires underneath it.
        """
        from infrastructure import tasks

        async def _loop():
            while True:
                await asyncio.sleep(RENEW_EVERY_SECONDS)
                try:
                    if self._is_leader:
                        await self.renew()
                    elif await self.acquire():
                        logger.info(
                            "Acquired the %r lease (%s) — this process is now "
                            "the leader and will run leader-only work.",
                            self._name, self._identity)
                except Exception as e:
                    # A failed round trip is not yet a lost lease — the TTL still
                    # has time on it — so log and try again on the next tick.
                    # Never re-raise: this loop going down is how a leader stops
                    # renewing without noticing.
                    logger.warning("Lease maintenance for %r errored: %s",
                                   self._name, e)

        self._renew_task = tasks.spawn(f"leader-{self._name}", _loop())

    async def stop(self) -> None:
        """Stop renewing and release. Safe to call when not the leader."""
        from infrastructure import tasks

        await tasks.cancel(f"leader-{self._name}")
        self._renew_task = None
        await self.release()

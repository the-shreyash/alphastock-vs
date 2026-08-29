"""Provider recovery policy — how a withdrawn feed is allowed back (D5.6).

WHY THIS IS A MODULE AND NOT A RETRY FLAG
------------------------------------------
D5.5 gave the platform its first genuinely *terminal* feed condition: a broker
that refuses an account's entitlement ends that channel and does not reconnect,
because reconnecting cannot make an unlicensed account licensed. That is right,
and it is also a one-way door — LIM-D5.5-3. If the entitlement is granted later
while the session stays valid, nothing in the platform can discover it.

The same shape had already been recorded once, in ADR-029::

    provider withdrawn
          ↓
    never called / never attached
          ↓
    no evidence
          ↓
    no recovery

so this is the second caller for work that has been owed since D2, and the point
of this module is that it answers both as one question rather than adding a
second special case beside `NOT_ENTITLED`.

RE-PROBE IS NOT RECONNECT, AND THE DIFFERENCE IS THE WHOLE DESIGN
------------------------------------------------------------------
`reliability.py` paces reconnects. It asks::

    is the same feed reachable again?

and its unit is a socket, its clock is seconds, and it is driven by link
transitions the transport already reports. This module asks a different
question::

    has a feed previously judged unusable become eligible again?

whose unit is a *provider-level condition* — an entitlement, a licence, an
account's plan — which changes on human timescales, not socket timescales. The
two ladders are therefore separate objects with separate constants, and nothing
here is ever consulted by the reconnect loop. Reusing D5.1's ladder would have
meant re-probing an entitlement every two seconds because that is how fast a
socket should come back, which is the churn D5.5 exists to stop.

What the two *do* share is `reconnect_pause` — equal-jitter pacing. That is
arithmetic for decorrelating a herd, not connection semantics, and a second copy
of it would be a second thing to get wrong the day a broker-side outage returns
every refused account's re-probe in the same instant.

THE PROBE IS ONE ORDINARY ATTACH, AND THAT IS DELIBERATE
---------------------------------------------------------
There is no `check_entitlement()` on the adapter contract and there will not be
one. A control-plane probe would have to be a broker-specific REST call, would
widen what every adapter and every test double implements (the change D4.7,
D4.10 and D5.5 each refused for the same reason), and — worst — would answer a
question that is not the one being asked: a control-plane "yes" is not evidence
that a feed can serve a price. MARKET_DATA_ARCHITECTURE.md has exactly one
definition of a usable feed, and it is a valid canonical tick on the current
link.

So a re-probe is **one ordinary attach attempt through the existing lifecycle**.
Its outcome is read off the callbacks that already exist:

  * the broker refuses again  → `NOT_ENTITLED` → the candidate stays, ladder up
  * the socket opens and ticks → the candidate is discharged by *evidence*
  * the token is dead          → `AUTH_EXPIRED` → the class becomes SESSION and
    the candidate stops being re-probeable at all

and the recovered feed then earns READY, serves its probation window and is
ranked on delivery latency exactly as a feed attached for the first time does,
because it *is* a feed attached for the first time — `attach_market_feed` builds
a new `StreamingTickProvider`, so there is no state left to inherit. Recovery
creates no eligibility; it only creates an opportunity to earn some.

BROKER-NEUTRALITY
-----------------
This module imports nothing from `services.` beyond `reliability`, names no
broker, and receives no broker vocabulary. Its whole input is a
`(user, broker, channel)` key, a :class:`RecoveryClass`, and two injected
predicates. `broker` here is an opaque account-scoping token — the same role it
plays in `BrokerStreamManager`'s registry key — and is never compared to a
literal. The Market Engine imports none of this, and does not learn that
anything was re-probed.

WHAT THIS DELIBERATELY DOES NOT DO
-----------------------------------
* **It does not re-probe a dead session.** `RecoveryClass.SESSION` exists so
  auth expiry is *recorded* and *excluded* rather than merely absent, and the
  service asks a session predicate again before every attempt. Two independent
  guards, because retrying invalid credentials on a schedule is the one failure
  mode here that a broker can respond to by locking an account.
* **It does not re-probe transport blips or stale feeds.** Both already recover
  on their own — D5.1's ladder and D5.3's fresh-evidence predicate respectively
  — and a candidate for a condition that heals itself is a second recovery
  mechanism racing the first.
* **It does not reach the market layer.** No readiness, no probation, no
  ranking, no registry. It decides *when an attach may be attempted* and nothing
  after that.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.brokers.reliability import reconnect_pause

logger = logging.getLogger(__name__)

#: How long to wait before the first re-probe of a provider-level condition,
#: in seconds.
#:
#: WHERE FIVE MINUTES COMES FROM, HONESTLY
#: Unlike `STABLE_CONNECTION_SECONDS` and `PROBATION_WINDOW_SECONDS`, this is not
#: a number MARKET_DATA_ARCHITECTURE.md had already published, and reaching for
#: one of those because it is also a duration would be a false economy dressed as
#: consistency (the mistake ADR-044 named when it declined to reuse a health
#: threshold for a latency window).
#:
#: The justification is what the condition *is*. An entitlement changes when a
#: person changes it: a user upgrades a plan on their broker's website, or an
#: operations team enables a data API on an account. Nothing observable to this
#: process happens at the moment it changes, so the only question is how long a
#: user should wait to notice — and five minutes is short enough that a user who
#: has just fixed their subscription sees their tier flip while still looking at
#: the screen, and long enough that it costs the broker one connection attempt
#: per account per five minutes in the worst case, against a reconnect ladder
#: whose *ceiling* is one per minute.
STILL_UNAVAILABLE_BASE_DELAY = 300.0

#: The ceiling of the re-probe ladder, in seconds. One hour.
#:
#: An account whose entitlement has been refused twelve times in a row is not
#: about to be granted one in the next thirty seconds, and the cost of being
#: wrong is bounded in the direction that matters: the user still recovers
#: immediately by reconnecting the broker, which is the lifecycle event that
#: clears the ladder outright.
STILL_UNAVAILABLE_MAX_DELAY = 3600.0

#: How often the background sweeper wakes to ask whether anything is due.
#:
#: This is a *tick*, not a probe interval: a sweep with nothing due performs no
#: I/O of any kind, touches no broker, and reads two dictionaries. The pacing
#: that matters is the ladder above; this only bounds how late a due candidate
#: can be.
REPROBE_SWEEP_INTERVAL = 60.0


class RecoveryClass(str, Enum):
    """How a withdrawn feed is allowed to come back.

    A closed set of five, and the reason it is five rather than one "retry" flag
    is that collapsing them is precisely the defect D5.5 found in the previous
    generation of this code: two conditions with different blast radii sharing
    one response, where the response is wrong for one of them and no message
    text can fix it.

    Every member answers one question — *what has to happen before an attach is
    worth attempting again?* — and only one member answers it with "time".
    """

    #: A transport blip. The reconnect ladder already owns it (D5.1) and it is
    #: never registered here: a candidate for a condition that heals itself is a
    #: second recovery mechanism racing the first.
    TRANSPORT = "transport"

    #: A feed whose link is still open but whose data went stale (D5.3).
    #: Recovers on the next accepted canonical tick, push-driven, with no
    #: control plane involved at all. Never registered here for the same reason
    #: as TRANSPORT.
    EVIDENCE = "evidence"

    #: A provider-level condition that may change without anything in this
    #: process being told — an entitlement, a licence, an account's data plan.
    #: **The only re-probeable class.** Recovers by a paced attach attempt.
    REPROBE = "reprobe"

    #: The credentials are gone. Recovery requires a *new valid session* through
    #: the existing re-authentication lifecycle, and never a retry: an automatic
    #: probe with invalid credentials is a login attempt on a schedule, which is
    #: how an account gets locked rather than how a feed comes back.
    SESSION = "session"

    #: The broker no longer declares this channel, or no transport serves its
    #: protocol. A deployment fact, not a runtime one; it comes back when the
    #: configuration does.
    CONFIGURATION = "configuration"


#: The classes a re-probe may act on. A frozenset of one, written as a set
#: because the question "may this class be re-probed?" must have exactly one
#: place to be answered — a membership test that a sixth class joins or does
#: not, rather than an `is REPROBE` comparison repeated at each call site.
REPROBEABLE_CLASSES = frozenset({RecoveryClass.REPROBE})

#: Classes that describe a condition which recovers with no help from this
#: module. Recorded so that "this was considered and deliberately not
#: registered" is a fact in the code rather than an omission.
SELF_RECOVERING_CLASSES = frozenset({RecoveryClass.TRANSPORT, RecoveryClass.EVIDENCE})


class ReprobeOutcome(str, Enum):
    """What one re-probe request did. Broker-neutral by construction.

    No member names a broker, a wire code, a protocol or a credential, and none
    can: this enum is produced from the register's own bookkeeping and from two
    boolean predicates. It is the *only* thing the caller learns, which is what
    stops a broker's vocabulary travelling out of the adapter behind a recovery
    result.
    """

    #: No withdrawal is recorded for this key — nothing to recover.
    NOT_REGISTERED = "not_registered"
    #: Recorded, but its class is not one a re-probe can act on.
    NOT_REPROBEABLE = "not_reprobeable"
    #: Re-probeable, but its ladder says the next attempt is not due yet.
    TOO_SOON = "too_soon"
    #: Due, but the account has no valid session to attach with.
    SESSION_UNAVAILABLE = "session_unavailable"
    #: Due, but a stream is already running for this channel — somebody else
    #: (a user reconnect, a session restore) got there first.
    ALREADY_ATTACHED = "already_attached"
    #: An attach was attempted. **Not** a statement that the feed recovered:
    #: whether it did is decided by evidence arriving afterwards, never here.
    ATTEMPTED = "attempted"
    #: An attach was attempted and raised. The ladder still climbed.
    ATTEMPT_FAILED = "attempt_failed"


RecoveryKey = Tuple[str, str, str]


@dataclass
class RecoveryCandidate:
    """One withdrawn (user, broker, channel) and what it is waiting for.

    Per key, which is the same granularity `BrokerStreamManager` keys its
    registry on and the same granularity `ConnectionStability` is instantiated
    at. That is not a coincidence and it is not a convention: it is the unit a
    withdrawal actually happens to. Two users on one broker hold two candidates
    that no code path can confuse, and one user's refused market feed says
    nothing about the same user's order channel.
    """

    user_id: str
    broker: str
    channel: str
    recovery_class: RecoveryClass
    #: Attempts made against this key since the ladder was last cleared by a
    #: lifecycle event. Survives a discharge on purpose — see
    #: :meth:`RecoveryRegister.record_withdrawal`.
    attempts: int = 0
    #: Monotonic instant the next attempt becomes due. `None` for a class that
    #: is never re-probed, so "not due" and "never due" are different values
    #: rather than one sentinel doing two jobs.
    next_attempt_at: Optional[float] = None

    @property
    def key(self) -> RecoveryKey:
        return (self.user_id, self.broker, self.channel)

    @property
    def is_reprobeable(self) -> bool:
        return self.recovery_class in REPROBEABLE_CLASSES

    def describe(self, *, now: float) -> Dict[str, Any]:
        """Diagnostics only. Carries no session, no credential and no reason text.

        The broker name is present for the same reason it is present on a
        provider name: this reaches an operator's diagnostics surface and the
        logs, never a consumer payload (Developer Rule 4).
        """
        return {
            "user_id": self.user_id,
            "broker": self.broker,
            "channel": self.channel,
            "recovery_class": self.recovery_class.value,
            "reprobeable": self.is_reprobeable,
            "attempts": self.attempts,
            "due_in_seconds": (
                None if self.next_attempt_at is None
                else max(0.0, self.next_attempt_at - now)
            ),
        }


class RecoveryRegister:
    """Which feeds have been withdrawn, why, and when each may be tried again.

    Holds two maps, and the reason there are two is the sharpest rule in this
    module:

    * `_candidates` — the withdrawals that are currently outstanding. A
      discharge removes an entry here.
    * `_history` — how many attempts a key has cost, cleared **only** by
      :meth:`forget`, which is called from deliberate lifecycle events (the user
      reconnected the broker, the user disconnected it).

    Without the second map the ladder would reset every time a broker accepted a
    socket and then refused the entitlement on it — the exact accept-then-refuse
    shape that produced DB-5's reconnect storm one layer down, reappearing here
    on a five-minute period instead of a 1.5-second one. Keeping the count
    outside the candidate is what makes an apparent success unable to buy a
    fresh ladder.
    """

    def __init__(
        self,
        *,
        base_delay: float = STILL_UNAVAILABLE_BASE_DELAY,
        max_delay: float = STILL_UNAVAILABLE_MAX_DELAY,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[float], float] = reconnect_pause,
    ) -> None:
        self._base_delay = float(base_delay)
        self._max_delay = float(max_delay)
        #: Monotonic, never wall-clock, for the reason every other duration in
        #: D5 is: an NTP step that moved the clock backwards would make every
        #: pending re-probe due at once, which is the storm this paces against.
        self._clock = clock
        self._jitter = jitter
        self._candidates: Dict[RecoveryKey, RecoveryCandidate] = {}
        self._history: Dict[RecoveryKey, int] = {}

    # ── Recording ────────────────────────────────────────

    def record_withdrawal(
        self,
        user_id: Any,
        broker: str,
        channel: str,
        recovery_class: RecoveryClass,
    ) -> Optional[RecoveryCandidate]:
        """Record that this feed was withdrawn, and say when it may be tried.

        Returns the candidate, or `None` for a class this module deliberately
        does not track — a transport blip and a stale feed both recover without
        it, and registering them would be a second mechanism racing the one that
        already works.

        A key that is already registered has its class **replaced**, not merged.
        That matters in one direction specifically: a feed refused on
        entitlement grounds whose token then expires must stop being
        re-probeable, and the strictly-stronger condition is the later one.

        The ladder is read from `_history`, so a key that has been withdrawn,
        discharged and withdrawn again pays the *next* rung rather than the
        first one.
        """
        if recovery_class in SELF_RECOVERING_CLASSES:
            return None
        key = (str(user_id), broker, channel)
        attempts = self._history.get(key, 0)
        candidate = RecoveryCandidate(
            user_id=key[0],
            broker=broker,
            channel=channel,
            recovery_class=recovery_class,
            attempts=attempts,
            next_attempt_at=(
                self._clock() + self._pause(attempts)
                if recovery_class in REPROBEABLE_CLASSES
                else None
            ),
        )
        self._candidates[key] = candidate
        logger.info(
            "Recovery: %s %s feed for user %s withdrawn (%s), attempts=%d, "
            "next attempt %s",
            broker, channel, key[0], recovery_class.value, attempts,
            "never — not re-probeable" if candidate.next_attempt_at is None
            else f"in {candidate.next_attempt_at - self._clock():.0f}s",
        )
        return candidate

    def reclassify(
        self,
        user_id: Any,
        broker: str,
        recovery_class: RecoveryClass,
        channel: str = None,
    ) -> int:
        """Move every outstanding withdrawal for this account to `recovery_class`.

        The one caller is session expiry, which is a fact about the *account*
        and not about the channel that happened to report it: a dead token
        cannot be re-probed on any channel, so an entitlement candidate on a
        second channel must stop being re-probeable at the same instant. Reuses
        :meth:`record_withdrawal` rather than mutating in place, so the ladder is
        carried across by the one piece of code that knows how.
        """
        changed = 0
        for key in self._keys(user_id, broker, channel):
            if self._candidates[key].recovery_class is recovery_class:
                continue
            self.record_withdrawal(key[0], key[1], key[2], recovery_class)
            changed += 1
        return changed

    def discharge(self, user_id: Any, broker: str, channel: str = None) -> int:
        """The feed produced evidence — drop the outstanding withdrawal.

        Keeps the attempt history, deliberately. See the class docstring: an
        attach that got a socket and then a refusal must not buy a fresh ladder,
        and only a lifecycle event may clear one.

        `channel=None` discharges every channel of the account, which is what
        the evidence-bearing caller has: a canonical tick is a fact about the
        account's market feed, and the caller that receives one is not told
        which socket carried it.
        """
        removed = 0
        for key in self._keys(user_id, broker, channel):
            if self._candidates.pop(key, None) is not None:
                removed += 1
        if removed:
            logger.info(
                "Recovery: %s feed for user %s recovered on evidence "
                "(%d outstanding withdrawal(s) discharged)",
                broker, str(user_id), removed,
            )
        return removed

    def forget(self, user_id: Any, broker: str, channel: str = None) -> int:
        """Clear the withdrawal **and the ladder** for this account.

        The only thing that resets pacing, and its callers are the two
        deliberate lifecycle events: the user connected the broker (a new
        session supersedes everything known about the old one) and the user
        disconnected it (there is nothing left to recover).
        """
        cleared = 0
        for key in self._keys(user_id, broker, channel):
            cleared += int(self._candidates.pop(key, None) is not None)
            self._history.pop(key, None)
        return cleared

    def note_attempt(self, candidate: RecoveryCandidate) -> RecoveryCandidate:
        """Charge one attempt against `candidate` and climb its ladder.

        Called *before* the attach, never after, and that ordering is load
        bearing: an attach that hangs, raises, or succeeds and is then refused
        must all cost the same rung. Charging afterwards would leave a key whose
        attach reliably throws re-probing at the base delay forever.
        """
        key = candidate.key
        candidate.attempts = self._history.get(key, 0) + 1
        self._history[key] = candidate.attempts
        candidate.next_attempt_at = self._clock() + self._pause(candidate.attempts)
        return candidate

    # ── Reading ──────────────────────────────────────────

    def get(self, user_id: Any, broker: str, channel: str) -> Optional[RecoveryCandidate]:
        return self._candidates.get((str(user_id), broker, channel))

    def is_due(self, candidate: RecoveryCandidate) -> bool:
        """Whether `candidate`'s ladder says the next attempt may happen now.

        The single place the ladder is compared against the clock, so
        :meth:`due` and a one-off :meth:`RecoveryService.reprobe` cannot answer
        differently — and so the register's clock stays private, which is what
        makes the pacing testable by injection rather than by sleeping.
        """
        return (
            candidate.is_reprobeable
            and candidate.next_attempt_at is not None
            and candidate.next_attempt_at <= self._clock()
        )

    def due(self, *, limit: Optional[int] = None) -> List[RecoveryCandidate]:
        """Re-probeable candidates whose next attempt is due, oldest first.

        Ordered by `next_attempt_at` so a candidate that has been waiting
        longest is served first when a sweep is capped — otherwise a dictionary
        ordering decides, and a large enough register could starve a key
        indefinitely.
        """
        ready = sorted(
            (
                candidate
                for candidate in self._candidates.values()
                if self.is_due(candidate)
            ),
            key=lambda candidate: candidate.next_attempt_at,
        )
        return ready if limit is None else ready[:limit]

    def candidates(self) -> List[RecoveryCandidate]:
        return list(self._candidates.values())

    def describe(self) -> List[Dict[str, Any]]:
        """Diagnostics: every outstanding withdrawal. Admin surfaces only."""
        now = self._clock()
        return [candidate.describe(now=now) for candidate in self._candidates.values()]

    def clear(self) -> None:
        """Drop everything. Startup and tests only."""
        self._candidates.clear()
        self._history.clear()

    # ── Internals ────────────────────────────────────────

    def _keys(self, user_id: Any, broker: str, channel: str = None) -> List[RecoveryKey]:
        owner = str(user_id)
        return [
            key
            for key in list(self._candidates)
            if key[0] == owner and key[1] == broker and (channel is None or key[2] == channel)
        ]

    def _pause(self, attempts: int) -> float:
        """The ladder rung for a key that has already made `attempts` attempts.

        Derived from the count rather than carried as mutable state, so a
        candidate rebuilt from history lands on the same rung the one it
        replaced would have — which is what makes the withdraw/discharge/
        withdraw path pace correctly without a second copy of the ladder.
        """
        delay = min(self._base_delay * (2.0 ** max(0, attempts)), self._max_delay)
        return self._jitter(delay)


#: Process-wide register, matching `stream_manager` / `provider_registry`.
recovery_register = RecoveryRegister()


class RecoveryService:
    """Drives re-probes: decides nothing, sequences everything.

    Holds no broker knowledge and no engine import. What it can do is supplied
    as three callables, which is what keeps `services.brokers.recovery` free of
    a cycle back into `broker_engine` — and, more usefully, what makes every
    branch below assertable without a database, a socket or an adapter.

    :param attach: ``await attach(user_id, broker, channel)`` — perform one
        ordinary attach of that channel. The engine passes its existing
        `start_stream`, scoped to the one channel; there is no probe-only path
        and there must not be one (see the module docstring).
    :param has_session: ``has_session(user_id, broker) -> bool`` — whether a
        valid session exists to attach with.
    :param is_attached: ``is_attached(user_id, broker, channel) -> bool`` —
        whether a stream is already running for that channel.
    """

    def __init__(
        self,
        register: Optional[RecoveryRegister] = None,
        *,
        attach,
        has_session,
        is_attached,
        interval: float = REPROBE_SWEEP_INTERVAL,
        max_per_sweep: Optional[int] = None,
    ) -> None:
        self._register = register if register is not None else recovery_register
        self._attach = attach
        self._has_session = has_session
        self._is_attached = is_attached
        self._interval = float(interval)
        #: A cap on how many attaches one sweep may start, so a register that
        #: has grown large cannot turn a single wake-up into a burst against one
        #: broker. `None` means uncapped, which is the right default while the
        #: ladder already spreads attempts across at least five minutes each.
        self._max_per_sweep = max_per_sweep
        self._task: Optional[asyncio.Task] = None

    @property
    def register(self) -> RecoveryRegister:
        return self._register

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ── One probe ────────────────────────────────────────

    async def reprobe(self, user_id: Any, broker: str, channel: str) -> ReprobeOutcome:
        """Attempt one re-probe of one channel. Never raises.

        The order of the guards is the policy, so it is worth reading as one
        sentence: a re-probe happens only for a **recorded** withdrawal, of a
        **re-probeable** class, whose ladder says it is **due**, for an account
        that still has a **valid session**, and whose channel is **not already
        attached**.

        The session guard is asked here even though `RecoveryClass.SESSION` is
        already excluded above it, and the redundancy is deliberate rather than
        defensive: the two guards catch different facts. The class excludes a
        feed whose *token expired*; this excludes a feed whose entitlement was
        refused and whose session went away **afterwards** — the user
        disconnected the broker, or another channel reported the token dead —
        which no classification made at withdrawal time can know about.
        """
        candidate = self._register.get(user_id, broker, channel)
        if candidate is None:
            return ReprobeOutcome.NOT_REGISTERED
        if not candidate.is_reprobeable:
            return ReprobeOutcome.NOT_REPROBEABLE
        if not self._register.is_due(candidate):
            return ReprobeOutcome.TOO_SOON
        return await self._attempt(candidate)

    async def _attempt(self, candidate: RecoveryCandidate) -> ReprobeOutcome:
        """Run the session/attachment guards and, if they pass, attach once."""
        if not self._has_session(candidate.user_id, candidate.broker):
            # Not an attempt: nothing was asked of the broker, so nothing is
            # charged. A candidate with no session simply waits, and the
            # lifecycle event that restores the session clears it outright.
            return ReprobeOutcome.SESSION_UNAVAILABLE
        if self._is_attached(candidate.user_id, candidate.broker, candidate.channel):
            self._register.discharge(candidate.user_id, candidate.broker, candidate.channel)
            return ReprobeOutcome.ALREADY_ATTACHED

        self._register.note_attempt(candidate)
        logger.info(
            "Recovery: re-probing the %s %s feed for user %s (attempt %d) — "
            "one ordinary attach; readiness and probation are still to be earned",
            candidate.broker, candidate.channel, candidate.user_id, candidate.attempts,
        )
        try:
            await self._attach(candidate.user_id, candidate.broker, candidate.channel)
        except Exception as e:
            # Swallowed on purpose. A re-probe is speculative by definition and
            # runs on a background task: an adapter raising must not kill the
            # sweeper and must not surface anywhere a user can see it. The
            # ladder has already climbed, so a broker that reliably throws backs
            # off exactly as one that reliably refuses does.
            logger.warning(
                "Recovery: re-probe of the %s %s feed for user %s failed: %s",
                candidate.broker, candidate.channel, candidate.user_id, e,
            )
            return ReprobeOutcome.ATTEMPT_FAILED
        return ReprobeOutcome.ATTEMPTED

    # ── The sweep ────────────────────────────────────────

    async def sweep_once(self) -> Dict[str, int]:
        """Attempt every due candidate once. Returns outcome counts.

        The whole of the periodic behaviour, factored out of the loop so it is
        testable without a clock, a sleep or a task — the property D5.2 insisted
        on for probation and for the same reason: a policy only observable by
        waiting is a policy nobody tests.

        A sweep with nothing due performs no I/O, reaches no broker and touches
        no adapter.
        """
        counts: Dict[str, int] = {}
        for candidate in self._register.due(limit=self._max_per_sweep):
            outcome = await self._attempt(candidate)
            counts[outcome.value] = counts.get(outcome.value, 0) + 1
        return counts

    def start(self) -> Optional[asyncio.Task]:
        """Begin sweeping in the background. Idempotent."""
        if self.running:
            return self._task
        self._task = asyncio.create_task(self._run(), name="provider-recovery-sweeper")
        return self._task

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("Recovery sweep failed: %s", e)

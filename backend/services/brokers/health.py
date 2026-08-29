"""Broker health — is this broker's API working, for everyone?

BROKER_INTEGRATION.md's Health Monitoring section asks for API availability,
authentication success and error rates surfaced in the Admin Portal. Before D3
the only health signal was `BrokerAdapter.health_check`, a live authenticated
call answering "is *this user's* session alive right now" — useful, and a
different question entirely. It needs a user's token, costs a network round
trip, and says nothing about the broker at any moment other than the one it ran.

WHY THIS MIRRORS `ProviderHealth` INSTEAD OF INVENTING A SECOND MODEL
---------------------------------------------------------------------
`services/market_engine/providers/base.py` already solved this shape for market
data providers: four states, counter-based rather than time-windowed, two
thresholds so one blip does not demote anything. Reusing the *design* keeps one
mental model for "is an external dependency healthy" across the platform, and
reusing the thresholds keeps the two subsystems from drifting into different
definitions of "degraded".

It is a separate class rather than an import because the two track different
things and must be free to diverge: a market provider's health is about data
freshness and drives *selection*, while a broker's health is about API
availability and drives *reporting* — brokers are chosen by the user, never by a
scoring function. Sharing a class would eventually mean a change made for one
subsystem silently changing behaviour in the other.

WHY AN AUTH FAILURE IS NOT A HEALTH FAILURE
--------------------------------------------
This is the load-bearing rule of this module. A `BrokerAuthError` means one
user's token expired — Kite invalidates every token daily at 06:00 IST, so at
06:01 every connected user's next call raises it. Counting those against broker
health would drive Zerodha to DOWN every single morning while its API was
perfectly available, and an admin dashboard that cries outage daily is a
dashboard nobody reads. Auth failures are counted separately, where a *rising*
auth-failure rate is genuinely interesting, and left out of the state machine.

D5.8 — WHY THIS IS THE FIRST THING DB-1 MOVED
----------------------------------------------
A broker's API is one remote system. Every worker's calls to it observe the same
outage, so N workers holding N independent counters is not N opinions about N
things — it is N partial views of one thing, and the Admin Portal was reporting
whichever worker happened to answer. The counters below are therefore mirrored
from a shared record (`infrastructure/health_state.py`) whenever one is
configured, and computed locally exactly as before when one is not.

Nothing about the *policy* changed: the thresholds, the four states, the
auth-failure exclusion and the public method names are the ones D3 shipped. What
changed is who owns the number.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from infrastructure import health_state
from infrastructure.health_state import broker_key, shared_health_store

logger = logging.getLogger(__name__)

#: Consecutive failures before a broker is considered degraded, then down.
#: Matches the market-provider thresholds so "degraded" means the same number of
#: consecutive failures everywhere in the platform.
DEGRADED_AFTER_FAILURES = 3
DOWN_AFTER_FAILURES = 8


class BrokerConnectionState(str, Enum):
    """Health of a broker's API as observed by this deployment.

    UNKNOWN   registered but never called — no evidence either way
    UP        answering normally
    DEGRADED  failing intermittently
    DOWN      failing consistently

    UNKNOWN is the honest initial value for the same reason it is in the market
    provider model: a broker registered one millisecond ago and one that has
    served ten thousand clean calls must not report identically on a surface
    whose only job is to tell an operator what is working. Unlike the market
    side, UNKNOWN has no ranking consequence here — brokers are selected by the
    user, not by health — so it is purely diagnostic.
    """

    UNKNOWN = "unknown"
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class BrokerHealth:
    """Rolling health of one broker's API.

    Broker-level, not user-level: every user's calls to Zerodha observe the same
    Kite API, so their outcomes are evidence about the same thing. Per-user
    session state is a different concept and lives on
    :class:`~services.brokers.contracts.BrokerConnection`.
    """

    broker: str = ""
    state: BrokerConnectionState = BrokerConnectionState.UNKNOWN
    consecutive_failures: int = 0
    total_calls: int = 0
    total_errors: int = 0
    #: Auth failures, counted but excluded from the state machine — see module
    #: docstring. A climbing count with a flat error count is the signature of a
    #: token-expiry wave, not an outage.
    total_auth_failures: int = 0
    last_success_at: Optional[str] = None
    last_error_at: Optional[str] = None
    last_error_code: Optional[str] = None

    def record_success(self) -> Optional[BrokerConnectionState]:
        """Record a successful call. Returns the new state if it changed."""
        self.total_calls += 1
        self.consecutive_failures = 0
        self.last_success_at = _now_iso()
        return self._transition(BrokerConnectionState.UP)

    def record_auth_failure(self) -> None:
        """Record a per-user session failure. Never changes the state."""
        self.total_calls += 1
        self.total_auth_failures += 1
        self.last_error_at = _now_iso()
        self.last_error_code = "BROKER_AUTH"

    def record_failure(self, code: str = None) -> Optional[BrokerConnectionState]:
        """Record a failed call. Returns the new state if it changed."""
        self.total_calls += 1
        self.total_errors += 1
        self.consecutive_failures += 1
        self.last_error_at = _now_iso()
        self.last_error_code = code
        if self.consecutive_failures >= DOWN_AFTER_FAILURES:
            return self._transition(BrokerConnectionState.DOWN)
        if self.consecutive_failures >= DEGRADED_AFTER_FAILURES:
            return self._transition(BrokerConnectionState.DEGRADED)
        return None

    def reset(self) -> None:
        """Drop all health state. Startup and tests only."""
        self.state = BrokerConnectionState.UNKNOWN
        self.consecutive_failures = 0
        self.total_calls = 0
        self.total_errors = 0
        self.total_auth_failures = 0
        self.last_success_at = None
        self.last_error_at = None
        self.last_error_code = None

    def apply_shared(self, shared: Any) -> Optional[BrokerConnectionState]:
        """Adopt the shared record as authoritative (D5.8). Returns a changed state.

        Overwrites rather than merges, for the reason
        :meth:`MarketDataProvider.apply_shared_health` does: a worker that missed
        a mutation converges on the next one instead of carrying a private drift
        forward forever. `error_label` is the store's generic name for the one
        short diagnostic string each subsystem keeps — here, the broker error
        code.
        """
        previous = self.state
        try:
            state = BrokerConnectionState(shared.state)
        except ValueError:  # pragma: no cover - defensive
            state = BrokerConnectionState.UNKNOWN
        self.state = state
        self.consecutive_failures = int(shared.consecutive_failures)
        self.total_calls = int(shared.total_calls)
        self.total_errors = int(shared.total_errors)
        self.total_auth_failures = int(shared.total_auth_failures)
        self.last_success_at = shared.last_success_at
        self.last_error_at = shared.last_error_at
        self.last_error_code = shared.error_label
        return state if state != previous else None

    def _transition(self, state: BrokerConnectionState) -> Optional[BrokerConnectionState]:
        if self.state == state:
            return None
        previous = self.state
        self.state = state
        logger.info(
            "Broker %s health %s -> %s (consecutive_failures=%d)",
            self.broker,
            previous.value,
            state.value,
            self.consecutive_failures,
        )
        return state

    def as_dict(self) -> Dict[str, Any]:
        return {
            "broker": self.broker,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "total_calls": self.total_calls,
            "total_errors": self.total_errors,
            "total_auth_failures": self.total_auth_failures,
            "last_success_at": self.last_success_at,
            "last_error_at": self.last_error_at,
            "last_error_code": self.last_error_code,
        }


# --------------------------------------------------------------------------- #
# The distributed path (D5.8 / DB-1)                                            #
# --------------------------------------------------------------------------- #
#
# Free functions rather than methods, because `BrokerHealth` is a dataclass the
# adapter owns and awaiting inside it would make every read of a plain counter
# look like I/O. Each one does the shared mutation, mirrors the authoritative
# answer back onto `health`, and falls back to the local arithmetic — the same
# code that has always been there — when Redis does not answer.


async def record_success_shared(health: BrokerHealth) -> Optional[BrokerConnectionState]:
    """Record a successful call against the shared record."""
    ok, record = await shared_health_store.record(
        broker_key(health.broker),
        health_state.SUCCESS,
        stamp=_now_iso(),
        degraded_after=DEGRADED_AFTER_FAILURES,
        down_after=DOWN_AFTER_FAILURES,
    )
    if not ok or record is None:
        return health.record_success()
    return health.apply_shared(record)


async def record_failure_shared(
    health: BrokerHealth, code: Optional[str] = None
) -> Optional[BrokerConnectionState]:
    """Record a failed call against the shared record.

    The consecutive-failure counter is incremented inside Redis, which is what
    makes `DOWN_AFTER_FAILURES` mean eight failures observed by the deployment
    rather than eight observed by one worker.
    """
    ok, record = await shared_health_store.record(
        broker_key(health.broker),
        health_state.FAILURE,
        stamp=_now_iso(),
        degraded_after=DEGRADED_AFTER_FAILURES,
        down_after=DOWN_AFTER_FAILURES,
        label=code,
    )
    if not ok or record is None:
        return health.record_failure(code)
    return health.apply_shared(record)


async def refresh_shared(*healths: BrokerHealth) -> bool:
    """Adopt the shared record for each of `healths`. True when Redis answered.

    The read half of DB-1 for brokers, and the reason the Admin Portal stops
    reporting whichever worker happened to serve the request: an operator asking
    "is this broker up?" gets the deployment's answer rather than one replica's
    partial view of it.

    Batched into one round trip, because the diagnostics surface asks about every
    broker at once and one round trip per broker would make an admin page's cost
    grow with the broker list.
    """
    subjects = [h for h in healths if h.broker]
    if not subjects:
        return False
    ok, records = await shared_health_store.read_many(
        [broker_key(h.broker) for h in subjects]
    )
    if not ok:
        return False
    for health in subjects:
        record = records.get(broker_key(health.broker))
        if record is not None:
            health.apply_shared(record)
    return True


async def record_auth_failure_shared(health: BrokerHealth) -> None:
    """Record a per-user session failure against the shared record.

    Shared for the *count* and excluded from the state machine, exactly as
    locally. Sharing it is what makes the signal readable at all: a token-expiry
    wave at 06:00 IST is visible as a climbing auth count against a flat error
    count, and split across N workers it was neither.
    """
    ok, record = await shared_health_store.record(
        broker_key(health.broker),
        health_state.AUTH,
        stamp=_now_iso(),
        degraded_after=DEGRADED_AFTER_FAILURES,
        down_after=DOWN_AFTER_FAILURES,
        label="BROKER_AUTH",
    )
    if not ok or record is None:
        health.record_auth_failure()
        return
    health.apply_shared(record)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

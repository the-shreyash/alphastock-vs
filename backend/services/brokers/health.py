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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

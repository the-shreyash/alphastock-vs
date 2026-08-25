"""Stream reliability policy — how a reconnecting transport paces itself (D5.1).

WHY THIS IS A MODULE AND NOT THREE MORE LINES IN `stream.py`
-------------------------------------------------------------
`stream.py` is the transport: it opens a socket, sends the frames the adapter
describes, hands frames back to the codec and reconnects. *How fast* it should
reconnect, and *what evidence* a connection has to produce before the platform
believes it, are a different kind of question — policy rather than mechanism —
and D5 adds several of them (probation, stale-feed demotion, failure
classification). Keeping them beside the run loop is how a run loop ends up with
a policy engine embedded in it, which is what D3 spent a sprint removing from
this same file.

Everything here is broker-neutral by construction. This module has no imports
from any adapter, names no broker, and receives no broker identity: its whole
input is *when a link came up* and *when it went down*. That is deliberate —
MARKET_DATA_ARCHITECTURE.md's Developer Rules put reliability decisions on
provider state, link state and timestamps, never on who the counterparty is.

THE DEFECT THIS CLOSES: DB-5
-----------------------------
Before D5.1 the run loop reset its backoff after any connection that
*completed*::

    await runner(self)
    delay = RECONNECT_BASE_DELAY   # "clean close -> quick reconnect"

The comment says "clean close", but the code cannot tell a clean close from any
other kind: a socket the broker accepted and closed one frame later reaches that
line exactly as a socket that ran all session does. So a broker saying "stop
doing this" — one of the five current protocols has a disconnect code that means
exactly that, and it exposed the defect rather than causing it — produced::

    connect -> accepted -> closed -> backoff reset -> reconnect ~1.5s later
            -> accepted -> closed -> ...   forever

A reconnect storm against a broker whose own documentation warns that continuing
may get the user blocked. Named as debt DB-5 in D4.11 rather than fixed there,
because the fix *is* flap suppression and flap suppression is D5's.

The correction is one word: the backoff resets after a connection that
**lasted**, not after a connection that *happened*.

THE MODEL — THREE OUTCOMES, ONE LADDER
---------------------------------------
Every reconnect attempt ends in exactly one of these, and the ladder responds
differently to each:

  =========================  ==========================================
  outcome                    effect on the reconnect ladder
  =========================  ==========================================
  STABLE                     reset to the base delay, clear the streak
  SHORT_LIVED                keep the ladder, count one flap
  NEVER_ESTABLISHED          keep the ladder
  =========================  ==========================================

and the ladder itself doubles on every attempt regardless, capped, jittered.
So a feed that runs for an hour and drops reconnects in ~1–2s exactly as it did
before D5.1 — the healthy case is unchanged, which is the point — while a feed
that keeps dying young climbs 2 → 4 → 8 → 16 → 32 → 60 and stays there.

Note what is *not* here: no global delay increase. Raising
`RECONNECT_BASE_DELAY` would have suppressed the storm and simultaneously made
every genuine blip cost every healthy user a slower recovery, which trades a
rare pathology for a constant tax. The ladder distinguishes instead of taxing.

WHERE THE 30 SECONDS COMES FROM
--------------------------------
`STABLE_CONNECTION_SECONDS` is not a number invented here.
MARKET_DATA_ARCHITECTURE.md §"Tracks probation" already fixes the platform's
definition of a provider that has proved itself:

    "a provider that just recovered must deliver clean data for a probation
    window (e.g. 30 seconds of valid messages) before it is eligible to become
    primary again — this prevents flapping"

A connection that dies before that window has, by the platform's own published
definition, never got far enough to be trusted — so it has no claim on a reset
backoff either. Using the same constant for both keeps one meaning of "stable"
across the transport and the provider layer instead of two that drift. D5's
probation slice will consume this same constant rather than declare a second.

WHAT THIS DELIBERATELY DOES NOT DO (D5.2+)
-------------------------------------------
* **It never gives up.** A feed that flaps a hundred times keeps retrying, at
  the ceiling. "This will never work, stop" is a *classification* judgement —
  entitlement failure, permanent misconfiguration — and belongs with D5's
  failure-classification slice, where the generic event model can express it.
  Escalating to the ceiling is the honest thing to do until then, and
  `consecutive_short_connections` is exposed so that slice has the evidence.
* **It does not touch readiness.** Whether a flapping feed may be the *primary*
  quote source is the provider layer's question, answered by
  `StreamingTickProvider`'s readiness gate, and D5's probation slice sharpens it
  there. A transport that reached into provider state would be the failover
  logic living in two places.
"""

from __future__ import annotations

import random
import time
from enum import Enum
from typing import Callable, Optional

#: Reconnect ladder, in seconds. Unchanged from D4 — D5.1 changes *when* the
#: ladder resets, not how far it climbs.
RECONNECT_BASE_DELAY = 2
RECONNECT_MAX_DELAY = 60

#: How long a connection must survive before it counts as stable, in seconds.
#: Derived from MARKET_DATA_ARCHITECTURE.md's probation window — see the module
#: docstring for why the two are deliberately the same number.
STABLE_CONNECTION_SECONDS = 30.0


def reconnect_pause(delay: float) -> float:
    """Equal-jitter backoff: half the current ceiling, plus a random half.

    The ceiling still doubles deterministically; only the *sleep* is randomized.

    Without this every stream reconnects in lockstep, and the reason is
    structural rather than unlucky: one broker-side blip disconnects every
    connected user's socket in the same instant, so an unjittered schedule has
    all of them retry in the same instant too — then again 2s later, 4s later,
    8s later, as a synchronized herd that grows with the user count and hits the
    broker hardest at exactly the moment it is least able to answer.

    Equal jitter rather than full jitter (`uniform(0, delay)`) because full
    jitter can roll a near-zero pause, which turns a still-down broker into a
    tight retry loop for whichever stream got the low number. Keeping a floor at
    half the ceiling preserves the backoff's purpose while still decorrelating
    the herd.
    """
    half = delay / 2.0
    return half + random.uniform(0.0, half)


class ConnectionOutcome(str, Enum):
    """How one connection attempt ended, as the reconnect ladder sees it.

    A closed set of three, and the set is the whole classification: the ladder
    needs to know whether the attempt earned a reset, cost a flap, or neither.
    Richer failure classification — transient vs entitlement vs protocol — is a
    different question asked at a different layer (D5's failure-classification
    slice) and is deliberately not smuggled in here, where it would arrive with
    no way to observe the difference: this module sees timestamps, not frames.
    """

    #: The link stayed up for at least `STABLE_CONNECTION_SECONDS`.
    STABLE = "stable"
    #: The link came up and died before proving itself. One flap.
    SHORT_LIVED = "short_lived"
    #: The attempt never reached link-up at all — a refused handshake, a socket
    #: closed before the subscribe frames were away, a transport that raised.
    NEVER_ESTABLISHED = "never_established"


class ConnectionStability:
    """The reconnect ladder for one connection, and the flap detector that feeds it.

    One instance per :class:`~services.brokers.stream.BrokerStream` — that is,
    per (user, broker, channel). Per-connection rather than per-broker on
    purpose, and it is the property Rule 6 of the D5 brief asks for: two users
    on the same broker hold two ladders, so one user's flapping session cannot
    slow another user's reconnect, and cannot be slowed by it. A shared ladder
    would also be wrong on its own terms — the thing being measured is whether
    *this socket* survives, and two sockets fail for different reasons.

    Driven entirely by link transitions, which the transport already reports
    exactly once each (`BrokerStream._notify_link` is change-gated). Nothing
    polls, nothing schedules: `link_up`/`link_down` are called at the instants
    the transport already knew about, and the only clock read is a duration.
    """

    def __init__(
        self,
        *,
        base_delay: float = RECONNECT_BASE_DELAY,
        max_delay: float = RECONNECT_MAX_DELAY,
        stable_after: float = STABLE_CONNECTION_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[float], float] = reconnect_pause,
    ) -> None:
        self._base_delay = float(base_delay)
        self._max_delay = float(max_delay)
        self._stable_after = float(stable_after)
        #: Monotonic, never wall-clock: a duration measured against a clock that
        #: an NTP step can move backwards would classify a flap as stable, and
        #: the whole point of the class is that classification.
        self._clock = clock
        self._jitter = jitter
        self._delay = float(base_delay)
        self._up_since: Optional[float] = None
        self._consecutive_short = 0

    # ── Link transitions ─────────────────────────────────

    def link_up(self) -> None:
        """The transport reports this attempt's link established.

        "Established" is the transport's existing definition — the socket is
        open *and* the subscribe frames are away — not the socket opening. A
        broker that accepts a connection and closes it before anything was asked
        of it never reaches here, and is classified `NEVER_ESTABLISHED` rather
        than as a flap, which is the truthful reading: nothing was established
        to flap.
        """
        self._up_since = self._clock()

    def link_down(self) -> ConnectionOutcome:
        """The transport reports this attempt's link lost. Classify it.

        Resets the ladder only for a connection that lasted. This one line is
        DB-5's fix; everything else in this module exists to make it legible.
        """
        if self._up_since is None:
            return ConnectionOutcome.NEVER_ESTABLISHED
        lasted = self._clock() - self._up_since
        self._up_since = None
        if lasted >= self._stable_after:
            self._delay = self._base_delay
            self._consecutive_short = 0
            return ConnectionOutcome.STABLE
        self._consecutive_short += 1
        return ConnectionOutcome.SHORT_LIVED

    # ── The ladder ───────────────────────────────────────

    def next_pause(self) -> float:
        """How long to wait before the next attempt, and climb one rung.

        Climbing on *every* attempt rather than only on failures is what makes
        an unreachable broker back off, and is unchanged from D4. What D5.1
        changed is that a stable connection has already put the ladder back on
        its bottom rung by the time this is called, so the healthy path still
        reconnects in ~1–2 seconds.
        """
        pause = self._jitter(self._delay)
        self._delay = min(self._delay * 2.0, self._max_delay)
        return pause

    # ── Diagnostics ──────────────────────────────────────

    @property
    def delay(self) -> float:
        """The current ladder rung, in seconds. Diagnostics and tests only."""
        return self._delay

    @property
    def consecutive_short_connections(self) -> int:
        """Connections in a row that came up and died before proving stable.

        Zero on any deployment behaving normally. A climbing value is the
        signature the D5 brief calls a flapping feed, and it is exposed rather
        than merely logged so that D5's failure-classification slice has
        something to decide on without re-deriving it from log lines.
        """
        return self._consecutive_short

    @property
    def is_flapping(self) -> bool:
        """Whether the last connection died before proving stable.

        A single short connection is enough to be true here, which is
        intentional: this reports a *condition*, and the response to it is
        already graduated by the ladder rather than by a threshold on this flag.
        """
        return self._consecutive_short > 0

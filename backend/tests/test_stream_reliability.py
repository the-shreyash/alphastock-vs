"""Sprint D5 — production-grade streaming reliability tests (hermetic).

D5 hardens the five-broker streaming architecture D4 built. This module pins the
first slice, **D5.1 — reconnect flap suppression**, and is where the later D5
slices (probation, stale-feed demotion, failure classification) will extend.

WHAT D5.1 IS AND WHY EACH TEST HERE EXISTS
-------------------------------------------
The defect is DB-5, named in D4.11 and deliberately left for D5. The stream run
loop reset its reconnect ladder after any connection that *completed*::

    await runner(self)
    delay = RECONNECT_BASE_DELAY   # "clean close -> quick reconnect"

The comment claims a clean close; the code cannot tell one. A socket a broker
accepts and closes one frame later reaches that assignment exactly as a socket
that ran all session does, so a broker-side "stop doing this" produced::

    connect -> accepted -> closed -> reset -> reconnect ~1.5s later -> ...

forever, against a broker whose own documentation warns that continuing may get
the user blocked. Every individual line of that storm looks like a routine
reconnect, which is why it survived four broker integrations.

The fix is one condition — the ladder resets after a connection that **lasted**
— and the tests below are written so that removing that condition turns them
red. Two of them exist *only* for that reason:

* `test_a_broker_that_accepts_and_immediately_closes_does_not_reconnect_forever_at_the_base_delay`
  drives the real `BrokerStream._run` through a transport that mimics DB-5's
  wire behaviour and asserts the sleeps climb. Restoring the old
  ``delay = RECONNECT_BASE_DELAY`` makes it fail.
* `test_a_stable_connection_resets_the_ladder_and_a_short_one_does_not` is its
  falsifying twin: a mechanism that suppressed flapping by simply never
  resetting would pass the first test and fail this one.

The pair is the point. Neither alone distinguishes "flap suppression" from
"slower reconnects for everybody", which the D5 brief rules out explicitly.

**No test here sleeps, opens a socket or reaches a broker API.** The stability
model takes its clock as a constructor argument and the run-loop tests patch
`asyncio.sleep`, so a 30-second stability threshold and a 60-second ceiling are
exercised in microseconds.
"""

import asyncio
import logging
import pathlib
import re
from unittest.mock import patch

import pytest

from services.brokers.reliability import (
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_DELAY,
    STABLE_CONNECTION_SECONDS,
    ConnectionOutcome,
    ConnectionStability,
)
from services.brokers.stream import BrokerStream

BACKEND = pathlib.Path(__file__).resolve().parent.parent

#: The five brokers whose adapters exist today. Used only by the sweep that
#: forbids their names in reliability code — the point of listing them is that
#: none of them may appear in the module under test.
BROKER_NAMES = ("zerodha", "kite", "upstox", "angelone", "angel one", "smartapi", "fyers", "dhan")


def run(coro):
    """Drive one coroutine on a fresh event loop.

    Matches `test_broker_streaming.run` deliberately: `asyncio.run` rather than
    `get_event_loop().run_until_complete`, because the latter passes in
    isolation and fails in a full-suite run once an earlier test has left the
    thread with no current loop.
    """
    return asyncio.run(coro)


class FakeClock:
    """A monotonic clock the test advances by hand.

    The stability model's whole job is comparing a duration against a threshold,
    so the tests have to control the duration. Sleeping 30 real seconds to prove
    a 30-second threshold would make this suite unrunnable; patching the module
    global would make it test a different constant than production uses. The
    model therefore takes its clock as an argument and production takes the
    default.
    """

    def __init__(self, now: float = 1000.0) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def stability(clock: FakeClock) -> ConnectionStability:
    """A stability model on `clock`, with production's constants and no jitter.

    Jitter is removed so the ladder's *rungs* are assertable; that jitter is
    applied at all is pinned separately in `test_broker_streaming.py`, which is
    where it has been since D4.1.
    """
    return ConnectionStability(clock=clock, jitter=lambda delay: delay)


# ==================================================================
# The model — classification
# ==================================================================


def test_a_connection_that_lasts_the_stability_window_is_stable():
    clock = FakeClock()
    model = stability(clock)

    model.link_up()
    clock.advance(STABLE_CONNECTION_SECONDS)

    assert model.link_down() is ConnectionOutcome.STABLE
    assert model.consecutive_short_connections == 0
    assert model.is_flapping is False


def test_a_connection_that_dies_before_the_window_is_a_flap():
    """One second short of the threshold is a flap, not a rounding error.

    The boundary is asserted on the *failing* side as well as the passing side
    because a mechanism written with `>` where `>=` belongs, or with the
    comparison inverted, still passes a test that only ever checks the happy
    case — and a comparison inverted here reinstates DB-5 exactly.
    """
    clock = FakeClock()
    model = stability(clock)

    model.link_up()
    clock.advance(STABLE_CONNECTION_SECONDS - 1)

    assert model.link_down() is ConnectionOutcome.SHORT_LIVED
    assert model.consecutive_short_connections == 1
    assert model.is_flapping is True


def test_an_attempt_that_never_came_up_is_not_counted_as_a_flap():
    """A refused handshake is a failure, but nothing was established to flap.

    It still escalates the ladder — see the ladder tests — so the distinction
    costs nothing operationally. It matters because `consecutive_short_connections`
    is the evidence D5's failure-classification slice will read, and conflating
    "the broker will not talk to us" with "the broker keeps hanging up on us"
    would hand that slice a number that cannot tell them apart.
    """
    clock = FakeClock()
    model = stability(clock)

    assert model.link_down() is ConnectionOutcome.NEVER_ESTABLISHED
    assert model.consecutive_short_connections == 0


# ==================================================================
# The model — the ladder
# ==================================================================


def test_a_stable_connection_resets_the_ladder_and_a_short_one_does_not():
    """The falsifying twin of the reconnect-storm test.

    A "fix" that suppressed flapping by never resetting the ladder would pass
    every storm test in this file and be wrong: it would make one broker-side
    blip cost every healthy user a minute of delayed recovery, which the D5
    brief rules out ("do NOT simply increase the reconnect delay globally").
    This test fails against that implementation and against the pre-D5.1 one,
    which is what makes the pair meaningful.
    """
    clock = FakeClock()
    model = stability(clock)

    # Climb the ladder on flaps.
    for _ in range(4):
        model.link_up()
        clock.advance(1)
        assert model.link_down() is ConnectionOutcome.SHORT_LIVED
        model.next_pause()
    assert model.delay > RECONNECT_BASE_DELAY, "repeated short connections did not escalate"

    # One connection that lasts puts it back on the bottom rung.
    model.link_up()
    clock.advance(STABLE_CONNECTION_SECONDS + 1)
    assert model.link_down() is ConnectionOutcome.STABLE
    assert model.delay == RECONNECT_BASE_DELAY
    assert model.next_pause() == RECONNECT_BASE_DELAY
    assert model.consecutive_short_connections == 0


def test_repeated_short_connections_escalate_to_the_ceiling_and_stay_there():
    clock = FakeClock()
    model = stability(clock)
    pauses = []

    for _ in range(12):
        model.link_up()
        clock.advance(0.1)
        model.link_down()
        pauses.append(model.next_pause())

    assert pauses[0] == RECONNECT_BASE_DELAY
    assert pauses == sorted(pauses), "the ladder must never step backwards while flapping"
    assert pauses[-1] == RECONNECT_MAX_DELAY
    assert max(pauses) <= RECONNECT_MAX_DELAY
    assert model.consecutive_short_connections == 12


def test_a_refused_handshake_still_backs_off():
    """Pre-D5.1 behaviour for the never-established case, preserved.

    The exception path never reset the ladder before D5.1 either. Pinned so that
    a future simplification of `link_down` cannot accidentally give an
    unreachable broker a tight retry loop.
    """
    clock = FakeClock()
    model = stability(clock)

    pauses = [(model.link_down(), model.next_pause())[1] for _ in range(5)]

    assert pauses == [2, 4, 8, 16, 32]


# ==================================================================
# The run loop — DB-5 itself
# ==================================================================


def _flapping_stream(broker: str = "zerodha", user_id: str = "u1"):
    """A stream whose transport comes up and immediately loses the connection.

    Mimics DB-5's wire behaviour exactly: the broker *accepts* the socket, the
    subscribe frames go out (which is when the transport reports link-up), and
    the connection then ends by returning normally — the "clean close" the old
    comment trusted.
    """
    stream = BrokerStream(user_id=user_id, broker=broker, session={"access_token": "t"})

    async def _accept_then_close(self):
        await self._notify_link(True)
        await self._notify_link(False, "stream ended")

    return stream, _accept_then_close


def _drive(stream, runner, rounds: int):
    """Run `rounds` reconnect attempts and return the sleeps the loop asked for."""
    slept = []

    async def _fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= rounds:
            stream._stopped = True

    # `resolve_transport` rather than a `PROTOCOL_RUNNERS` entry: the key would
    # be a wire-protocol name, which is broker knowledge, and these tests
    # deliberately run against several brokers' channels to prove the mechanism
    # does not know which one it is pacing.
    with (
        patch("services.brokers.stream.resolve_transport", return_value=runner),
        patch("services.brokers.stream.asyncio.sleep", new=_fake_sleep),
    ):
        run(stream._run())
    return slept


def test_a_broker_that_accepts_and_immediately_closes_does_not_reconnect_forever_at_the_base_delay():
    """DB-5. The whole point of D5.1.

    Restoring the deleted ``delay = RECONNECT_BASE_DELAY`` after the transport
    call turns this red: every sleep collapses back onto the base delay and the
    stream hammers the broker roughly every 1.5 seconds indefinitely.

    Asserted on the *trend* rather than on exact values because the production
    path keeps its jitter — the sleeps are jittered, so the assertion that has
    to hold is that the ceiling they are drawn from climbs.
    """
    stream, runner = _flapping_stream()

    slept = _drive(stream, runner, rounds=6)

    assert len(slept) == 6
    assert slept[-1] > slept[0], "the reconnect delay did not grow across repeated short connections"
    assert slept[-1] >= RECONNECT_BASE_DELAY * 4, (
        "six accepted-then-closed connections still reconnect at nearly the base delay — DB-5 is back"
    )
    assert stream._stability.consecutive_short_connections == 6


def test_a_long_lived_connection_still_reconnects_quickly():
    """The healthy path must not pay for the pathological one.

    A feed that streamed all session and dropped is the common case, and D5.1
    must leave it exactly where D4 had it: reconnecting within the base delay.
    """
    stream = BrokerStream(user_id="u1", broker="zerodha", session={"access_token": "t"})
    clock = FakeClock()
    stream._stability = ConnectionStability(clock=clock)

    async def _long_lived(self):
        await self._notify_link(True)
        clock.advance(STABLE_CONNECTION_SECONDS * 10)
        await self._notify_link(False, "stream ended")

    slept = _drive(stream, _long_lived, rounds=3)

    assert len(slept) == 3
    for pause in slept:
        assert RECONNECT_BASE_DELAY / 2.0 <= pause <= RECONNECT_BASE_DELAY, (
            "a stable connection did not reset the reconnect ladder"
        )
    assert stream._stability.consecutive_short_connections == 0


def test_a_flap_is_reported_in_the_log_without_a_credential():
    """The storm must be visible, and visible without leaking the session.

    Two assertions in one test on purpose: the log line only exists to make the
    pattern findable, and a log line that carried the access token would be a
    SECURITY.md breach introduced by a diagnostic. Captured at DEBUG so nothing
    the module emits at any level escapes the check.
    """
    stream, runner = _flapping_stream()
    stream.session = {"access_token": "live_looking_token_9f3a7c21", "api_key": "ak_live_51H8xQ2"}
    stream.credentials = {"api_key": "ak_live_51H8xQ2"}

    with patch("services.brokers.stream.logger") as log:
        _drive(stream, runner, rounds=3)

    warnings = " ".join(str(call) for call in log.warning.call_args_list)
    assert "flap" in warnings.lower(), "a flapping connection was suppressed silently"
    for secret in ("live_looking_token_9f3a7c21", "ak_live_51H8xQ2"):
        assert secret not in warnings


def test_a_flap_is_reported_through_real_logging_at_debug_level(caplog):
    """The same property, through the real logging stack rather than a mock.

    A mocked logger proves what the module *asked* to log; this proves what a
    deployment running at DEBUG would actually write, which is where a
    credential would surface if one leaked through a formatter argument.
    """
    stream, runner = _flapping_stream()
    stream.session = {"access_token": "live_looking_token_9f3a7c21"}

    with caplog.at_level(logging.DEBUG, logger="services.brokers.stream"):
        _drive(stream, runner, rounds=3)

    text = caplog.text
    assert "flap" in text.lower()
    assert "live_looking_token_9f3a7c21" not in text


# ==================================================================
# User isolation (D5 brief, Rule 6)
# ==================================================================


def test_one_users_flapping_broker_does_not_pace_another_users_reconnects():
    """Two users on the *same* broker hold two independent ladders.

    A stability model keyed on the broker instead of the connection would pass
    every other test in this file and fail here — and the failure in production
    is a user whose feed is perfectly healthy reconnecting a minute late because
    somebody else's Dhan session is being rejected.
    """
    flapping, flapping_runner = _flapping_stream(broker="dhan", user_id="user-a")
    healthy = BrokerStream(user_id="user-b", broker="dhan", session={"access_token": "t"})
    clock = FakeClock()
    healthy._stability = ConnectionStability(clock=clock)

    async def _long_lived(self):
        await self._notify_link(True)
        clock.advance(STABLE_CONNECTION_SECONDS * 10)
        await self._notify_link(False, "stream ended")

    flapping_sleeps = _drive(flapping, flapping_runner, rounds=6)
    healthy_sleeps = _drive(healthy, _long_lived, rounds=3)

    assert flapping_sleeps[-1] > RECONNECT_BASE_DELAY
    assert healthy._stability.consecutive_short_connections == 0
    for pause in healthy_sleeps:
        assert pause <= RECONNECT_BASE_DELAY, "user B's ladder was moved by user A's flapping session"


def test_two_channels_of_one_account_back_off_independently():
    """A broker's order socket flapping must not slow its market feed.

    D4.7 made a broker's connections fail independently; D5.1 must not quietly
    recouple them through a shared ladder. The market feed is the one that
    matters here — a slower reconnect on it is a longer stretch of delayed
    prices for a user whose ticks channel never had a problem.
    """
    orders, orders_runner = _flapping_stream(broker="upstox", user_id="u1")
    orders.channel = "orders"
    ticks = BrokerStream(user_id="u1", broker="upstox", session={"access_token": "t"}, channel="ticks")

    _drive(orders, orders_runner, rounds=5)

    assert orders._stability.consecutive_short_connections == 5
    assert ticks._stability.consecutive_short_connections == 0
    assert ticks._stability.delay == RECONNECT_BASE_DELAY


# ==================================================================
# The architectural rule D5 must not break
# ==================================================================


def test_the_reliability_module_names_no_broker():
    """Rule 1 of the D5 brief, enforced rather than reviewed.

    Reliability decisions may use link state, timestamps and durations. They may
    not use broker identity — the moment one does, every subsequent broker needs
    a branch here and D3's whole achievement unwinds. Checked against executable
    code *and* prose: this module's docstrings explain DB-5, which is a real
    story about a real protocol, so the sweep would be worthless if a broker
    name in a comment satisfied it. It is asserted on the source with strings
    and comments left in, and the module is written to pass that stricter bar.
    """
    source = (BACKEND / "services" / "brokers" / "reliability.py").read_text().lower()

    for name in BROKER_NAMES:
        assert not re.search(rf"\b{re.escape(name)}\b", source), (
            f"reliability policy names the broker {name!r} — see Rule 1 of the D5 brief"
        )


def test_the_reliability_module_imports_no_adapter():
    """It may not reach a broker even without naming one.

    A name sweep alone is satisfiable by importing `services.brokers.dhan` as
    `mod` and branching on a constant read from it. The import graph is the
    property that actually holds.
    """
    source = (BACKEND / "services" / "brokers" / "reliability.py").read_text()

    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, flags=re.MULTILINE)
    for module in imports:
        assert not module.startswith("services."), (
            f"reliability policy imports {module} — it must depend on nothing but the stdlib"
        )


@pytest.mark.parametrize("attribute", ["broker", "user_id", "channel", "session", "credentials"])
def test_the_stability_model_is_never_told_who_it_is_pacing(attribute):
    """The strongest form of Rule 1: it cannot branch on identity it never receives.

    `ConnectionStability` is constructed with tuning constants and two callables
    and nothing else. If a later slice needs broker identity to make a
    reliability decision, that is a design conversation, not a parameter — and
    this test is where it has to happen.
    """
    model = ConnectionStability()
    assert not hasattr(model, attribute)

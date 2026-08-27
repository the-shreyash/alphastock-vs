"""Sprint D5.5 — entitlement-failure classification and safe recovery (hermetic).

WHAT THIS FILE PINS
-------------------
Until D5.5 the transport had exactly two answers to a broker that stopped
serving: end the account's **session** (`AUTH_EXPIRED`), or keep reconnecting
(everything else). An account that is *not entitled* to a feed fits neither, and
the platform already had one in production terms — Dhan's disconnect code 806,
"Data APIs not subscribed", which ADR-040 knowingly approximated as an expired
session and recorded as a limitation.

The rules D5.5 establishes, stated so they can be falsified:

  * A broker's **explicit** refusal decodes to the broker-neutral
    `StreamEventKind.NOT_ENTITLED`. Nothing else does: not silence, not a
    timeout, not a socket that opens and closes, not an accepted subscription
    that yields no data, not a malformed frame. Absence of evidence is not a
    refusal.
  * `NOT_ENTITLED` is **terminal for one channel of one user's stream** and for
    nothing else. The transport stops that channel and does not reconnect;
    coming back requires a deliberate lifecycle event.
  * The **session survives**. This is the whole reason the kind exists: the
    token is valid, so REST portfolio, funds, order placement and the order
    stream keep working, and `AUTH_EXPIRED`'s session teardown would destroy
    them on the strength of a statement the broker did not make.
  * The affected account's **market feed stops being resolvable immediately** —
    unregistered, so no READY, STABLE or primary state can keep it selected —
    and the baseline serves the very next resolution.
  * Everything else is untouched: the other channels of that broker, every other
    broker, every other user, and the guest/baseline context.

WHY SO MUCH OF THIS FILE IS ABOUT WHAT AN ENTITLEMENT FAILURE MAY *NOT* DO
---------------------------------------------------------------------------
A terminal classification is the most dangerous kind of state a feed can enter,
because every way of being wrong about it is silent and permanent: inferred from
silence it stops a working feed forever; scoped too widely it takes down a
session, a second channel or a second user; scoped to the wrong layer it puts a
broker's vocabulary into generic code. So the tests below spend more effort
proving the boundaries than proving the happy path.

No test opens a socket, sleeps on a reconnect ladder, or reaches a broker API.
LIVE VALIDATION WAS NOT PERFORMED.
"""

import ast
import asyncio
import contextlib
import json
import pathlib
import re
import struct
import time
from unittest.mock import AsyncMock, patch

import pytest

from services.broker_engine import BrokerEngine
from services.brokers.reliability import ConnectionStability
from services.brokers.stream import (
    BrokerStream,
    _AuthExpired,
    _NotEntitled,
    _terminal_refusal,
    stream_manager,
)
from services.brokers.streaming import (
    DEFAULT_STREAM_CHANNEL,
    EVENT_CAPABILITY,
    BrokerStreamEvent,
    StreamEventKind,
)
from services.market_engine.providers import (
    Capability,
    ProviderRegistry,
    ResolutionContext,
    StreamingTickProvider,
    YahooPollingAdapter,
)
from services.market_engine.source_manager import SourceManager
from tests._fakedb import FakeDB
from tests.test_broker_framework import _strip_comments_and_strings as _strip_source

# The D4/D5 seam helpers, reused rather than re-implemented: a second copy of
# "attach a feed the way the engine attaches one" would be a second thing to
# keep true.
from tests.test_broker_streaming import (
    NovaAdapter,
    _attach,
    _clean_provider_registry,
    _FakeSocket,
    nova_registered,
    run,
)
from tests.test_provider_probation import FakeClock, _tick

BACKEND = pathlib.Path(__file__).resolve().parent.parent

#: Dhan's documented "Data APIs not subscribed" disconnect reason — the one
#: real-broker entitlement signal the platform has, and the reason this sprint
#: exists. Written as a literal here rather than imported so the test states the
#: wire value independently of the table it is checking.
DHAN_NOT_SUBSCRIBED = 806


# ==================================================================
# Fixtures: a fictional broker that refuses entitlement, and a driver
# for the *reconnecting* loop rather than one pass over it
# ==================================================================


class EntitlementNovaAdapter(NovaAdapter):
    """Nova, plus the two ways a broker can refuse an entitlement.

    A fictional broker on purpose (brief requirement 18): if the mechanism only
    works for the broker that motivated it, it is not a mechanism. Nova shares
    no wire format with Dhan — text frames, symbol identity, string prices — and
    reaches the same broker-neutral outcome through the same generic transport
    with no core change of any kind.
    """

    #: A frame the broker sends on an open socket, Dhan's shape of refusal.
    DENY_FRAME = "DENIED market data is not enabled for this account"
    #: A handshake the broker refuses outright, Angel One's / Fyers' shape.
    DENY_HANDSHAKE = "403 Forbidden: market data subscription required"

    def decode_stream_frame(self, frame):
        if isinstance(frame, (bytes, bytearray)):
            frame = frame.decode("utf-8", errors="ignore")
        if isinstance(frame, str) and frame.startswith("DENIED"):
            return BrokerStreamEvent.not_entitled(frame[len("DENIED "):])
        return super().decode_stream_frame(frame)

    def stream_connect_error(self, error: BaseException):
        text = str(error)
        if "403" in text and "subscription" in text:
            # The adapter interprets its own broker's vocabulary and returns a
            # broker-neutral classification. Nothing generic reads "403".
            return BrokerStreamEvent.not_entitled("this account is not subscribed to the market feed")
        if "401" in text:
            return "the session is no longer accepted"
        return None


@contextlib.contextmanager
def entitlement_nova():
    with nova_registered(EntitlementNovaAdapter()) as adapter:
        yield adapter


class _StreamRun:
    """What one run of the reconnecting loop did."""

    def __init__(self):
        self.connects = 0
        self.ticks = []
        self.expired = []
        self.not_entitled = []
        self.links = []


def drive_loop(
    adapter,
    frames,
    *,
    max_connects=6,
    user_id="user-1",
    channel=None,
    instruments=("RELIANCE",),
    connect_error=None,
):
    """Run `BrokerStream._run` — the loop that reconnects — to completion.

    `drive_stream` in the D4 suite runs a single transport pass, which cannot
    see the property this sprint is about: whether the loop comes back. This
    driver counts connections and stops the loop after `max_connects` by
    cancelling, so "reconnects forever" and "stopped after one" are
    distinguishable outcomes rather than a hang.

    The reconnect pause is patched to zero. The *ladder* is D5.1's and is tested
    there; what matters here is how many attempts happen, not how far apart.
    """
    result = _StreamRun()
    script = list(frames)

    async def fake_connect(self, endpoint):
        result.connects += 1
        if result.connects > max_connects:
            # Not an assertion: raising here is how a test that would otherwise
            # spin forever terminates, and `_run` re-raises CancelledError
            # untouched, which is the behaviour being relied on.
            raise asyncio.CancelledError()
        if connect_error is not None:
            raise connect_error
        return _FakeSocket(script)

    async def on_tick(uid, broker, batch):
        result.ticks.append((uid, broker, batch))

    async def on_expired(uid, broker, channel_name):
        result.expired.append((uid, broker, channel_name))

    async def on_not_entitled(uid, broker, channel_name):
        result.not_entitled.append((uid, broker, channel_name))

    async def on_link_state(uid, broker, up, reason, channel_name):
        result.links.append((uid, broker, up, channel_name))

    if channel is None:
        declared = adapter.stream_channels()
        channel = declared[0].name if declared else DEFAULT_STREAM_CHANNEL

    stream = BrokerStream(
        user_id,
        adapter.name,
        {"access_token": "live-token"},
        credentials={"api_key": "nova-key"},
        instrument_tokens=list(instruments or []),
        on_tick=on_tick,
        on_expired=on_expired,
        on_not_entitled=on_not_entitled,
        on_link_state=on_link_state,
        channel=channel,
    )

    async def scenario():
        with patch.object(BrokerStream, "_connect", fake_connect), \
                patch.object(ConnectionStability, "next_pause", lambda self: 0):
            with contextlib.suppress(asyncio.CancelledError):
                await stream._run()

    run(scenario())
    return result, stream


def _price_frame(price=2650.0, symbol="RELIANCE"):
    return json.dumps({"kind": "price", "rows": [{"scrip": symbol, "rate": str(price)}]})


def _dhan_disconnect_frame(code):
    """A DhanHQ feed-disconnect packet carrying `code`.

    Packed here from the documented layout rather than imported from the
    adapter's own constants, so the test states the wire independently of the
    code that reads it.
    """
    return struct.pack("<BHBIH", 50, 10, 0, 0, code)


# ==================================================================
# 1. An explicit refusal becomes a broker-neutral NOT_ENTITLED
# ==================================================================


def test_a_dhan_806_frame_decodes_to_a_broker_neutral_entitlement_refusal():
    """The real signal that motivated the sprint, at the boundary that classifies it.

    806 is "Data APIs not subscribed". Before D5.5 it decoded to AUTH_EXPIRED
    with an honest message and a dishonest state — see the file docstring and
    ADR-045.
    """
    from services.brokers.dhan import DhanAdapter

    event = DhanAdapter().decode_stream_frame(_dhan_disconnect_frame(DHAN_NOT_SUBSCRIBED))
    assert event.kind is StreamEventKind.NOT_ENTITLED
    assert event.message, "the refusal carried no reason"
    assert str(DHAN_NOT_SUBSCRIBED) not in event.message, "the raw wire code leaked to a user-facing message"


def test_a_fictional_broker_reaches_the_same_classification_from_a_different_wire():
    """Requirement 18: the mechanism is not Dhan's.

    Nova's refusal is a text frame with no code in it at all, and it produces
    the identical broker-neutral event through the identical generic transport.
    """
    adapter = EntitlementNovaAdapter()
    event = adapter.decode_stream_frame(EntitlementNovaAdapter.DENY_FRAME)
    assert event.kind is StreamEventKind.NOT_ENTITLED
    assert event.message == "market data is not enabled for this account"


def test_the_refusal_reaches_the_transport_as_a_lifecycle_outcome_not_as_data():
    with entitlement_nova() as adapter:
        result, _stream = drive_loop(adapter, [_price_frame(), adapter.DENY_FRAME, _price_frame(9999.0)])

    assert result.not_entitled == [("user-1", "nova", DEFAULT_STREAM_CHANNEL)]
    assert result.expired == [], "an entitlement refusal took the session-expiry path"
    # The tick before the refusal is delivered; nothing after it is.
    assert [t["last_price"] for _u, _b, batch in result.ticks for t in batch] == [2650.0]


def test_a_refused_handshake_is_classified_by_the_adapter_and_acted_on_generically():
    """The second route in: a broker that refuses at the HTTP upgrade (D5.5).

    `connect_error` may now answer with a terminal event rather than only with a
    reason string, so a broker whose 403 means "not licensed" is no longer
    forced to describe it as an expired session.
    """
    with entitlement_nova() as adapter:
        result, _stream = drive_loop(
            adapter, [], connect_error=RuntimeError(adapter.DENY_HANDSHAKE))

    assert result.not_entitled == [("user-1", "nova", DEFAULT_STREAM_CHANNEL)]
    assert result.expired == []
    assert result.connects == 1, "a refused handshake was retried"


# ==================================================================
# 2 / 19 / 20. Terminal means terminal — no retry, no churn, and coming
# back needs a deliberate lifecycle event
# ==================================================================


def test_an_entitlement_refusal_stops_the_stream_instead_of_reconnecting():
    """Requirement 2. Retrying cannot make an unlicensed account licensed."""
    with entitlement_nova() as adapter:
        result, stream = drive_loop(adapter, [adapter.DENY_FRAME], max_connects=25)

    assert result.connects == 1, f"the transport reconnected {result.connects} times into a refusal"
    assert not stream.running


def test_an_ordinary_error_frame_still_reconnects_which_is_what_makes_the_refusal_meaningful():
    """The control for the test above, and the reason ERROR cannot express this.

    `ERROR` deliberately leaves the connection alone, so a broker that closes
    the socket after sending one drives the ladder indefinitely — paced by
    D5.1's flap suppression, never stopped by it. That is the exact behaviour a
    refused entitlement must NOT have, and it is why the closed kind set could
    not represent this without a new member (ADR-045).
    """
    with nova_registered() as adapter:
        result, _stream = drive_loop(adapter, ["PING"], max_connects=5)

    assert result.connects > 1, "the control case did not reconnect, so the comparison proves nothing"


def test_repeated_identical_refusals_cannot_produce_reconnect_churn():
    """Requirement 19. Ten scripted refusals still cost exactly one connection."""
    with entitlement_nova() as adapter:
        result, _stream = drive_loop(adapter, [adapter.DENY_FRAME] * 10, max_connects=25)

    assert result.connects == 1
    assert result.not_entitled == [("user-1", "nova", DEFAULT_STREAM_CHANNEL)]


def test_coming_back_requires_a_deliberate_reattachment_rather_than_the_loops_own_schedule():
    """Requirement 20.

    The stream that was refused is finished. A new one exists only because
    something *decided* to start it — which is what `BrokerStreamManager`
    does on connect, on session restore, and nowhere else.
    """
    with entitlement_nova() as adapter:
        result, refused = drive_loop(adapter, [adapter.DENY_FRAME], max_connects=25)
        assert result.connects == 1 and not refused.running

        async def restart():
            stream = await stream_manager.start_stream(
                "user-1", adapter.name, {"access_token": "live-token"},
                credentials={"api_key": "k"}, instrument_tokens=["RELIANCE"],
            )
            await stream_manager.stop_stream("user-1", adapter.name)
            return stream

        restarted = run(restart())

    assert restarted is not refused, "the refused stream was resurrected rather than replaced"


# ==================================================================
# 3 / 4. The other lifecycle outcomes stay distinguishable
# ==================================================================


def test_an_expired_session_remains_a_distinct_outcome_with_a_distinct_callback():
    """Requirement 3. AUTH_EXPIRED and NOT_ENTITLED are not collapsed."""
    with entitlement_nova() as adapter:
        result, _stream = drive_loop(adapter, ["EXPIRED"], max_connects=25)

    assert result.expired == [("user-1", "nova", DEFAULT_STREAM_CHANNEL)]
    assert result.not_entitled == [], "an expired session was reported as an entitlement failure"


def test_the_two_terminal_exceptions_are_unrelated_types():
    """Neither may be caught by the other's handler — the session teardown is
    the difference, and a subclass relationship would silently restore it."""
    assert not issubclass(_NotEntitled, _AuthExpired)
    assert not issubclass(_AuthExpired, _NotEntitled)


def test_an_ordinary_disconnect_is_neither_and_still_reconnects():
    """Requirement 4. A socket that simply ends is routine weather."""
    with entitlement_nova() as adapter:
        result, _stream = drive_loop(adapter, [_price_frame()], max_connects=4)

    assert result.expired == [] and result.not_entitled == []
    assert result.connects > 1, "an ordinary disconnect stopped reconnecting"
    assert (("user-1", "nova", True, DEFAULT_STREAM_CHANNEL) in result.links
            and ("user-1", "nova", False, DEFAULT_STREAM_CHANNEL) in result.links)


# ==================================================================
# 5 / 6. Entitlement is never inferred — the sprint's sharpest rule
# ==================================================================


def test_silence_is_not_an_entitlement_failure():
    """Requirement 5. A socket that opens, subscribes and delivers nothing.

    "Do not infer entitlement from silence" is the brief's rule and it is the
    one an implementation is most tempted to break, because a feed that never
    ticks looks exactly like a feed that is not allowed to.
    """
    with entitlement_nova() as adapter:
        result, _stream = drive_loop(adapter, [], max_connects=4)

    assert result.not_entitled == [], "silence was read as a refusal"
    assert result.connects > 1, "a silent feed stopped reconnecting"


def test_a_socket_that_opens_and_immediately_closes_is_not_an_entitlement_failure():
    """socket open != entitlement, and neither does losing it prove the opposite."""
    with entitlement_nova() as adapter:
        result, _stream = drive_loop(adapter, ["PING"], max_connects=4)

    assert result.not_entitled == []
    assert result.connects > 1


def test_a_timeout_is_not_an_entitlement_failure():
    """A handshake that times out is a broker outage until an adapter says
    otherwise; `connect_error` returns None for it and the ladder runs."""
    with entitlement_nova() as adapter:
        result, _stream = drive_loop(
            adapter, [], max_connects=4, connect_error=asyncio.TimeoutError("timed out"))

    assert result.not_entitled == [] and result.expired == []
    assert result.connects > 1


def test_a_malformed_frame_is_not_an_entitlement_failure():
    """Requirement 6. Codec containment (D4.2) is unchanged by this sprint.

    A frame the codec cannot read is dropped and the connection is left alone —
    it is emphatically not evidence about what the account is licensed for.
    """
    with entitlement_nova() as adapter:
        result, _stream = drive_loop(
            adapter, ["{not json at all", b"\x00\x01\x02", _price_frame()], max_connects=2)

    assert result.not_entitled == []
    assert [t["last_price"] for _u, _b, batch in result.ticks for t in batch] == [2650.0, 2650.0]


def test_a_codec_that_raises_does_not_produce_an_entitlement_failure():
    class Exploding(EntitlementNovaAdapter):
        def decode_stream_frame(self, frame):
            raise RuntimeError("codec defect")

    with nova_registered(Exploding()) as adapter:
        result, _stream = drive_loop(adapter, ["anything"], max_connects=3)

    assert result.not_entitled == []
    assert result.connects > 1


def test_an_unclassified_handshake_failure_is_not_terminal():
    assert _terminal_refusal(None) is None
    assert _terminal_refusal("") is None


def test_a_non_terminal_event_from_connect_error_is_refused_rather_than_guessed_at():
    """A codec defect must not become a lifecycle decision.

    Reading a TICKS event out of a failed handshake as "keep retrying" would be
    correct by accident; reading it as terminal would stop a working feed. It is
    logged and left to the ordinary backoff.
    """
    assert _terminal_refusal(BrokerStreamEvent.ignore()) is None
    assert _terminal_refusal(BrokerStreamEvent.error("boom")) is None
    assert isinstance(_terminal_refusal("dead token"), _AuthExpired)
    assert isinstance(_terminal_refusal(BrokerStreamEvent.auth_expired("x")), _AuthExpired)
    assert isinstance(_terminal_refusal(BrokerStreamEvent.not_entitled("x")), _NotEntitled)


def test_the_refusal_is_ungated_by_capability():
    """A broker that mis-declares what it serves must not thereby lose the
    ability to say "stop" — the same reasoning AUTH_EXPIRED is ungated by."""
    assert StreamEventKind.NOT_ENTITLED not in EVENT_CAPABILITY
    assert StreamEventKind.AUTH_EXPIRED not in EVENT_CAPABILITY


def test_a_refusal_carries_no_ticks_and_no_order():
    with pytest.raises(Exception):
        BrokerStreamEvent(kind=StreamEventKind.NOT_ENTITLED, order={"order_id": "1"})


# ==================================================================
# 7–10. What it does to the market side: ineligible, immediately,
# whatever the feed had earned
# ==================================================================


def _engine():
    engine = BrokerEngine()
    engine.configure(FakeDB())
    return engine


def _market_fixture(users=("u1",), broker="nova", symbols=("RELIANCE",), probation=0.0):
    """The real registry holding the baseline plus one feed per user.

    Built through `attach_market_feed`, the seam the engine actually uses, so
    these tests exercise a provider constructed where the platform constructs
    one. The probation window defaults to zero because these tests are about
    what an entitlement refusal does to a *promoted* feed, and would otherwise
    spend every fixture re-proving D5.2's window; the tests that are about the
    probation interaction set it back.
    """
    from services.brokers.market_feed import feed_provider_name, set_market_feed_link
    from services.market_engine.providers import provider_registry
    from services.market_engine.providers import streaming as streaming_module

    baseline = YahooPollingAdapter()
    provider_registry.register(baseline)
    run(baseline.connect())
    feeds = {}
    with patch.object(streaming_module, "PROBATION_WINDOW_SECONDS", probation):
        for user in users:
            run(_attach(user, broker, list(symbols)))
            run(set_market_feed_link(user, broker, up=True))
            feeds[user] = provider_registry.get(feed_provider_name(user, broker))
    return SourceManager(provider_registry), baseline, feeds


def _quote(manager, user_id, symbol="RELIANCE"):
    return manager.resolve(
        Capability.QUOTES, context=ResolutionContext(user_id=user_id, symbol=symbol))


def test_an_entitlement_failure_takes_the_feed_out_of_quote_eligibility():
    """Requirements 7 and 8, in one pair of resolutions."""
    from services.brokers.market_feed import publish_market_ticks

    with entitlement_nova(), _clean_provider_registry() as registry:
        registry.clear()
        manager, baseline, feeds = _market_fixture()
        run(publish_market_ticks("u1", "nova", [_tick()]))
        assert feeds["u1"].is_ready

        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))

        assert _quote(manager, "u1") is baseline, "the refused feed still served the quote"
        assert manager.status(user_id="u1")["state"] == "available", "the baseline was not resolvable"


@pytest.mark.parametrize("hold_to", ["ready", "stable"])
def test_a_feed_that_had_earned_readiness_or_stability_cannot_stay_selected(hold_to):
    """Requirements 9 and 10.

    Both are asserted the same way and that is the point: the feed is
    *unregistered*, so there is no state — READY, STABLE, primary — in which it
    can remain a candidate. A demotion that merely lowered its rank would leave
    it selectable the moment nothing steadier existed.
    """
    from services.brokers.market_feed import publish_market_ticks
    from services.market_engine.providers import streaming as streaming_module

    window = 0.05
    with entitlement_nova(), _clean_provider_registry() as registry, \
            patch.object(streaming_module, "PROBATION_WINDOW_SECONDS", window):
        registry.clear()
        manager, baseline, feeds = _market_fixture(probation=window)
        feed = feeds["u1"]

        run(publish_market_ticks("u1", "nova", [_tick()]))
        if hold_to == "stable":
            time.sleep(window * 1.5)
            run(publish_market_ticks("u1", "nova", [_tick()]))
            assert feed.is_stable
            assert _quote(manager, "u1") is feed, "the fixture never promoted the feed"
        else:
            assert feed.is_ready and feed.is_on_probation

        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))

        assert _quote(manager, "u1") is baseline
        assert feed not in manager.failover_chain(
            Capability.QUOTES, context=ResolutionContext(user_id="u1", symbol="RELIANCE"))


def test_the_refused_feed_can_no_longer_deliver_into_the_gateway():
    """Unregistering is not only a resolution change: a live socket that keeps
    pushing must stop being able to reach the engine (the D4.4 sink contract)."""
    from services.brokers.market_feed import publish_market_ticks

    with entitlement_nova(), _clean_provider_registry() as registry:
        registry.clear()
        _manager, _baseline, _feeds = _market_fixture()
        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))
        assert run(publish_market_ticks("u1", "nova", [_tick()])) == 0


# ==================================================================
# 11–13. Scope: one user, one feed, one channel
# ==================================================================


def test_a_second_user_of_the_same_broker_is_unaffected():
    """Requirement 11 — the isolation that is easiest to lose and hardest to see.

    Two users on ONE broker, resolved through the registry rather than through
    the object the attach returned, because that is how every consumer reaches a
    feed and it is the only arrangement in which a broker-scoped mistake is
    visible.
    """
    from services.brokers.market_feed import publish_market_ticks

    with entitlement_nova(), _clean_provider_registry() as registry:
        registry.clear()
        manager, baseline, feeds = _market_fixture(users=("u1", "u2"))
        for user in ("u1", "u2"):
            run(publish_market_ticks(user, "nova", [_tick()]))

        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))

        assert _quote(manager, "u1") is baseline
        assert _quote(manager, "u2") is feeds["u2"], "one user's refusal demoted another user's feed"
        assert feeds["u2"].is_ready


def test_another_broker_of_the_same_user_is_unaffected():
    """Requirement 12."""
    from services.brokers.market_feed import publish_market_ticks

    class Orion(EntitlementNovaAdapter):
        name = "orion"
        display_name = "Orion Markets"

    with entitlement_nova(), nova_registered(Orion()), _clean_provider_registry() as registry:
        registry.clear()
        manager, baseline, nova_feeds = _market_fixture(broker="nova")
        _m2, _b2, orion_feeds = _market_fixture(broker="orion")
        run(publish_market_ticks("u1", "orion", [_tick()]))

        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))

        assert orion_feeds["u1"].is_ready, "an unrelated provider lost its readiness"
        assert _quote(manager, "u1") is orion_feeds["u1"], "the surviving feed did not serve"
        assert baseline is not None


def test_the_guest_and_baseline_contexts_are_unchanged():
    """Requirement 13. A per-user refusal may not touch the platform floor."""
    with entitlement_nova(), _clean_provider_registry() as registry:
        registry.clear()
        manager, baseline, _feeds = _market_fixture()

        before = manager.status()
        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))

        assert manager.status() == before
        assert _quote(manager, None) is baseline
        assert baseline.is_connected, "the baseline was disconnected by another user's refusal"


def test_a_refusal_on_a_non_tick_channel_does_not_demote_the_market_feed():
    """Failure-scope rule: an entitlement is about a capability, not a login.

    A broker whose *order* channel is refused says nothing about its market
    feed, and detaching one because the other was refused would drop a feed that
    is delivering prices perfectly well. Uses the same channel gate
    `_on_stream_link_state` uses, so there is one answer to "which connection
    carries the ticks".
    """
    from services.brokers.base import AdapterStreamChannel
    from services.brokers.market_feed import publish_market_ticks

    class TwoChannel(EntitlementNovaAdapter):
        def stream_channels(self):
            return (
                AdapterStreamChannel(self, name="ticks", delivers=frozenset({StreamEventKind.TICKS})),
                AdapterStreamChannel(self, name="orders", delivers=frozenset({StreamEventKind.ORDER})),
            )

    with nova_registered(TwoChannel()), _clean_provider_registry() as registry:
        registry.clear()
        manager, _baseline, feeds = _market_fixture()
        run(publish_market_ticks("u1", "nova", [_tick()]))

        run(_engine()._on_stream_not_entitled("u1", "nova", "orders"))

        assert _quote(manager, "u1") is feeds["u1"], "an order channel's refusal demoted the market feed"


def test_a_refusal_on_the_tick_channel_of_a_multi_channel_broker_does_demote_it():
    """The other half of the gate — otherwise the test above passes vacuously."""
    from services.brokers.base import AdapterStreamChannel
    from services.brokers.market_feed import publish_market_ticks

    class TwoChannel(EntitlementNovaAdapter):
        def stream_channels(self):
            return (
                AdapterStreamChannel(self, name="ticks", delivers=frozenset({StreamEventKind.TICKS})),
                AdapterStreamChannel(self, name="orders", delivers=frozenset({StreamEventKind.ORDER})),
            )

    with nova_registered(TwoChannel()), _clean_provider_registry() as registry:
        registry.clear()
        manager, baseline, _feeds = _market_fixture()
        run(publish_market_ticks("u1", "nova", [_tick()]))

        run(_engine()._on_stream_not_entitled("u1", "nova", "ticks"))

        assert _quote(manager, "u1") is baseline


# ==================================================================
# The session survives — the whole reason the kind exists
# ==================================================================


def test_an_entitlement_failure_leaves_the_accounts_session_intact():
    """The distinction ADR-045 is for, asserted where it actually bites.

    `_on_stream_expired` drops the cached session, stops every channel of the
    broker and tells the user their login expired. None of that may happen here:
    the token is valid, and the account can still fetch its portfolio, place
    orders and receive order updates.
    """
    engine = _engine()
    engine._sessions[("u1", "nova")] = {"access_token": "still-good"}

    with entitlement_nova(), _clean_provider_registry() as registry:
        registry.clear()
        _market_fixture()
        with patch.object(BrokerEngine, "_push", new=AsyncMock()) as pushed:
            run(engine._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))

    assert engine._sessions.get(("u1", "nova")) == {"access_token": "still-good"}, \
        "an entitlement refusal dropped a valid session"
    assert not any("session_expired" in json.dumps(call.args, default=str)
                   for call in pushed.await_args_list), \
        "the user was told their session expired when it had not"


def test_an_expired_session_still_does_tear_the_account_down():
    """The control: the session path is unchanged by this sprint."""
    engine = _engine()
    engine._sessions[("u1", "nova")] = {"access_token": "dead"}

    with entitlement_nova(), _clean_provider_registry() as registry:
        registry.clear()
        _market_fixture()
        with patch.object(BrokerEngine, "_push", new=AsyncMock()):
            run(engine._on_stream_expired("u1", "nova", DEFAULT_STREAM_CHANNEL))

    assert ("u1", "nova") not in engine._sessions


def test_a_refusal_does_not_stop_the_accounts_other_channels():
    """`_on_stream_expired` stops every channel of the broker because the token
    is the account's. An entitlement is not, so the others keep running."""
    from services.brokers.base import AdapterStreamChannel

    class TwoChannel(EntitlementNovaAdapter):
        def stream_channels(self):
            return (
                AdapterStreamChannel(self, name="ticks", delivers=frozenset({StreamEventKind.TICKS})),
                AdapterStreamChannel(self, name="orders", delivers=frozenset({StreamEventKind.ORDER})),
            )

    async def scenario():
        for channel in ("ticks", "orders"):
            await stream_manager.start_stream(
                "u1", "nova", {"access_token": "t"}, credentials={},
                instrument_tokens=["RELIANCE"], channel=channel)

    with nova_registered(TwoChannel()), _clean_provider_registry() as registry:
        registry.clear()
        _market_fixture()
        run(scenario())
        try:
            run(_engine()._on_stream_not_entitled("u1", "nova", "ticks"))
            live = {row["channel"] for row in stream_manager.status()
                    if row["user_id"] == "u1" and row["broker"] == "nova"}
            assert live == {"orders"}, f"the surviving channels were {live or 'none'}"
        finally:
            run(stream_manager.stop_stream("u1", "nova"))


# ==================================================================
# 14 / 15. What a consumer is allowed to see
# ==================================================================


def test_the_status_change_is_published_to_the_owner_alone():
    """Requirement 14. The unregistration already publishes a user-scoped
    `provider.status`; this sprint adds no surface and redesigns no payload."""
    from services.market_engine.event_bus import event_bus

    published = []

    async def spy(event):
        published.append(event)

    with entitlement_nova(), _clean_provider_registry() as registry:
        registry.clear()
        _market_fixture(users=("u1", "u2"))
        event_bus.subscribe("provider.status", spy)
        try:
            run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))
        finally:
            event_bus.unsubscribe("provider.status", spy)

    scoped = [e for e in published if e.get("data", e).get("user_id")]
    assert scoped, "the owner was never told their tier moved"
    assert {e.get("data", e)["user_id"] for e in scoped} == {"u1"}, \
        "another user was told about a refusal that was not theirs"


def test_no_broker_vocabulary_or_credential_reaches_a_consumer_surface():
    """Requirement 15. The consumer payload is exactly what it was before D5.5."""
    with entitlement_nova(), _clean_provider_registry() as registry:
        registry.clear()
        manager, _baseline, _feeds = _market_fixture()
        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))

        status = manager.status(user_id="u1")

    assert set(status) == {"state", "tier", "reason", "capabilities"}
    blob = json.dumps(status).lower()
    for forbidden in ("nova", "dhan", "806", "not_subscribed", "live-token", "access_token", "api_key"):
        assert forbidden not in blob, f"{forbidden!r} reached a consumer surface"


def test_the_transport_logs_the_refusal_without_a_credential():
    """SECURITY.md, on the one new log line this sprint adds.

    Run at DEBUG through the real logging stack with live-looking credentials,
    because a handler that formats lazily can leak a value a unit assertion on
    the format string would never see.
    """
    secret = "eyJhbGciOiJIUzI1NiJ9.live-access-token-value"

    class Leaky(EntitlementNovaAdapter):
        def decode_stream_frame(self, frame):
            if frame == "DENY":
                # The broker's own error payload, credential and all — exactly
                # what an adapter must not pass on, and what the message field
                # would carry if an adapter did.
                return BrokerStreamEvent.not_entitled("not subscribed")
            return super().decode_stream_frame(frame)

        def stream_endpoint(self, session, credentials=None):
            endpoint = super().stream_endpoint(session, credentials)
            return type(endpoint)(url=f"wss://feed.nova.example/v1/stream?token={secret}")

    import logging

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = Capture()
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        with nova_registered(Leaky()) as adapter:
            drive_loop(adapter, ["DENY"], max_connects=3)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)

    blob = "\n".join(records)
    assert "not entitled" in blob.lower(), "the refusal was not logged at all"
    assert secret not in blob, "a live-looking credential was written to the log"
    assert "token=" not in blob, "a credential-bearing query string was logged"


# ==================================================================
# 16 / 17. The layering rules the whole D3–D5 sequence rests on
# ==================================================================


#: Every module the classification passes through that is owned by no broker.
GENERIC_STREAM_MODULES = (
    "services/brokers/stream.py",
    "services/brokers/streaming.py",
    "services/brokers/market_feed.py",
    "services/brokers/reliability.py",
    "services/broker_engine.py",
    "services/market_engine/providers/streaming.py",
    "services/market_engine/providers/base.py",
    "services/market_engine/source_manager.py",
)

#: The code D5.5 actually wrote, addressed by name so a *comment-inclusive*
#: sweep can run over it. The broad modules above carry historical docstrings
#: that legitimately narrate which broker forced which abstraction (D4.2's
#: parser, D4.7's channels, D4.10's connection scope), so a comment-inclusive
#: sweep over whole files would have to exempt them wholesale — which is how a
#: sweep stops meaning anything. Scoped to the new regions it stays strict, and
#: it already earned that: it caught a broker name in this sprint's own
#: `StreamEventKind` docstring, which was rewritten rather than exempted.
D55_REGIONS = (
    ("services/brokers/streaming.py", "StreamEventKind"),
    ("services/brokers/streaming.py", "not_entitled"),
    ("services/brokers/stream.py", "_NotEntitled"),
    ("services/brokers/stream.py", "_terminal_refusal"),
    ("services/brokers/stream.py", "_run"),
    ("services/brokers/stream.py", "_dispatch"),
    ("services/broker_engine.py", "_on_stream_not_entitled"),
)

BROKER_NAMES = ("zerodha", "kite", "upstox", "angelone", "angel one", "smartapi",
                "fyers", "dhan", "groww", "indmoney")


def _named_source(relative, symbol):
    """The full source of one named class or function, comments included."""
    source = (BACKEND / relative).read_text()
    for node in ast.walk(ast.parse(source)):
        if getattr(node, "name", None) == symbol:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{symbol} no longer exists in {relative}")


def test_the_entitlement_path_names_no_broker_even_in_a_comment():
    """Requirement 16, run the strict way D5.1 established.

    Comments and strings are left IN. A comment naming a broker in the generic
    layer is a design statement even when it is inert, and the sweep that reads
    only executable code cannot see the moment someone writes
    `# the fifth broker sends 806 here`.
    """
    offenders = []
    for relative, symbol in D55_REGIONS:
        body = _named_source(relative, symbol).lower()
        for name in BROKER_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", body):
                offenders.append(f"{relative}:{symbol} -> {name}")
    assert not offenders, f"a broker is named in the entitlement path: {offenders}"


def test_no_generic_module_branches_on_a_provider_or_broker_identity():
    """Requirement 16's stronger form: not just no name, no *identity comparison*.

    A module that read `if self.broker == some_literal` would pass a name sweep
    and still be the branch D3 removed. Run over executable code across every
    generic module the classification passes through.
    """
    pattern = re.compile(r"(broker|provider|name)\s*(==|!=)\s*['\"]")
    offenders = []
    for relative in GENERIC_STREAM_MODULES:
        source = _strip_source((BACKEND / relative).read_text())
        for line in source.splitlines():
            if pattern.search(line):
                offenders.append(f"{relative}: {line.strip()}")
    assert not offenders, f"a broker-identity branch reached generic code: {offenders}"


def test_no_broker_name_appears_in_the_executable_code_of_any_generic_module():
    """The D3/D4 sweep, unchanged, over the modules this sprint touched."""
    offenders = []
    for relative in GENERIC_STREAM_MODULES:
        source = _strip_source((BACKEND / relative).read_text()).lower()
        for name in BROKER_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", source):
                offenders.append(f"{relative}: {name}")
    assert not offenders, f"a broker is named in generic executable code: {offenders}"


def test_every_shipped_broker_still_uses_the_one_generic_transport():
    """Requirement 17. Five brokers, one transport, unchanged by this sprint."""
    from services.brokers import register_default_brokers
    from services.brokers.registry import broker_registry
    from services.brokers.stream import PROTOCOL_RUNNERS, resolve_transport

    register_default_brokers()
    streaming = [name for name in ("zerodha", "upstox", "angelone", "fyers", "dhan")
                 if name in broker_registry]
    assert len(streaming) == 5, f"expected five streaming brokers, found {streaming}"
    assert PROTOCOL_RUNNERS == {}, "a broker acquired a transport of its own"
    for name in streaming:
        adapter = broker_registry.require(name)
        for channel in adapter.stream_channels():
            assert resolve_transport(channel) is BrokerStream._run_websocket, name


def test_the_market_engine_still_cannot_see_the_broker_layer():
    """The classification stops at the engine seam: nothing in the Market Engine
    learns that a *broker* refused anything, only that a provider went away."""
    for relative in ("services/market_engine/providers/streaming.py",
                     "services/market_engine/source_manager.py",
                     "services/market_engine/gateway.py"):
        source = (BACKEND / relative).read_text()
        assert "services.brokers" not in source, relative
        assert "StreamEventKind" not in source, f"{relative} reads a broker event kind"


# ==================================================================
# Regression: D5.2–D5.4 are untouched
# ==================================================================


def _feed(user_id="u1", clock=None, probation=None, symbols=("RELIANCE",)):
    from services.market_engine.providers import PROBATION_WINDOW_SECONDS

    clock = clock or FakeClock()
    feed = StreamingTickProvider(
        f"feed:{user_id}",
        owner_user_id=user_id,
        probation_seconds=PROBATION_WINDOW_SECONDS if probation is None else probation,
        clock=clock,
    )
    run(feed.connect())
    if symbols:
        run(feed.subscribe(symbols))
    return feed, clock


def test_probation_still_requires_valid_canonical_evidence():
    """D5.2: time alone proves nothing, and neither does a socket."""
    from services.market_engine.providers import PROBATION_WINDOW_SECONDS

    feed, clock = _feed()
    run(feed.on_raw([_tick()]))
    assert feed.is_ready and feed.is_on_probation

    clock.advance(PROBATION_WINDOW_SECONDS * 10)
    assert feed.is_on_probation, "silence promoted a feed out of probation"
    run(feed.on_raw([_tick()]))
    assert feed.is_stable


def test_a_reconnect_still_resets_probation_and_evidence():
    """D5.2/D5.3: evidence belongs to the link that produced it."""
    from services.market_engine.providers import PROBATION_WINDOW_SECONDS

    feed, clock = _feed()
    run(feed.on_raw([_tick()]))
    clock.advance(PROBATION_WINDOW_SECONDS)
    run(feed.on_raw([_tick()]))
    assert feed.is_stable

    run(feed.mark_link_down("dropped"))
    run(feed.mark_link_up())
    assert not feed.is_ready and feed.is_on_probation
    assert feed.delivery_latency is None


def test_a_stale_feed_still_demotes_lazily_and_never_outranks_a_fresh_baseline():
    """D5.3, both halves — no timer, and the baseline wins while the link is up."""
    from services.market_engine.providers import DEFAULT_TICK_MAX_AGE_SECONDS

    registry = ProviderRegistry()
    baseline = YahooPollingAdapter()
    registry.register(baseline)
    run(baseline.connect())
    feed, clock = _feed(probation=0.0)
    registry.register(feed)
    manager = SourceManager(registry)

    run(feed.on_raw([_tick()]))
    assert _quote(manager, "u1") is feed

    clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)
    assert feed.is_link_up, "the link was torn down, so staleness is not what is being tested"
    assert _quote(manager, "u1") is baseline


def test_latency_remains_a_tie_break_and_yahoo_never_acquires_one():
    """D5.4: the third sort element, and the polled baseline stays unscored."""
    from services.market_engine.providers import LATENCY_WINDOW_SAMPLES

    baseline = YahooPollingAdapter()
    run(baseline.connect())
    assert baseline.delivery_latency is None

    fast, fast_clock = _feed("u1", probation=0.0)
    for _ in range(LATENCY_WINDOW_SAMPLES + 1):
        fast_clock.advance(0.05)
        run(fast.on_raw([_tick()]))
    slow, slow_clock = _feed("u2", probation=0.0)
    for _ in range(LATENCY_WINDOW_SAMPLES + 1):
        slow_clock.advance(5.0)
        run(slow.on_raw([_tick()]))

    assert fast.delivery_latency < slow.delivery_latency
    # A ranking term only: neither feed's latency made it eligible for the other
    # user, which is the property a filter-shaped implementation would break.
    assert not slow.is_eligible_for(ResolutionContext(user_id="u1", symbol="RELIANCE"))
    assert baseline.delivery_latency is None, "the polled baseline acquired a finite latency"

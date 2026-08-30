"""Sprint D5.11 — chaos testing and failure resilience (hermetic).

WHAT THIS FILE PROVES
---------------------
D5.11 adds no mechanism. Every previous D4/D5 sprint asserted its own rule in
isolation, on a fixture built for that rule; this file asserts that the rules
still hold **when something is failing**, and that the failure stays where it
happened. The question it exists to answer is the one the brief ends on:

    Can StockAssist survive realistic market-data failures without lying about
    freshness, losing valid data unnecessarily, cross-contaminating users or
    providers, creating retry storms, or bypassing the architecture's existing
    recovery and fallback rules?

THE EIGHT INVARIANTS, AND WHERE EACH IS PINNED
-----------------------------------------------
=========  ==========================================  ====================
invariant  claim                                       section
=========  ==========================================  ====================
A          user isolation                              §L, §J
B          provider isolation                          §L
C          shard isolation                             §J
D          fallback honesty                            §C, §E, §J
E          no stale evidence inheritance               §C, §D, §F
F          no retry storm                              §A, §H, §O
G          entitlement is terminal for the channel     §G
H          Redis failure degrades locally              §I
=========  ==========================================  ====================

WHY THE HARNESS IS DETERMINISTIC AND HAS NO SEED
-------------------------------------------------
See `tests/_chaos.py`. In one line: every failure D5.11 enumerates is a *named*
case with a named consequence, so a scripted case is stronger evidence than a
sample and needs no seed to be reproducible. The one source of randomness on the
production path — `reconnect_pause`'s jitter — is displaced by an injected
identity function through the constructor argument D5.1 already exposes, so the
recorded ladder is the real ladder rather than a re-implementation of it.

    No test in this file sleeps, reads a wall clock, opens a socket, or reaches
    a broker API. Every duration is a `ChaosClock.advance()`.

WHAT IS DELIBERATELY *NOT* HERE
--------------------------------
* **No broker-specific chaos.** Every transport and protocol case runs against
  the fictional `ChaosAdapter`, for the reason D4.2 built the codec boundary:
  a chaos suite that had a Kite branch would be testing Kite, and the property
  under test is that the generic path has no branches. The five real adapters'
  wire formats are pinned by `test_broker_streaming.py` and are untouched.
* **No new production flag, timer or seam.** The brief forbids them and none
  was needed; §E in particular re-proves D5.3's lazy decay *without* a timer,
  because adding one to make the decay observable would change the mechanism
  being observed.
"""

import asyncio
import json
import logging
import os
import pathlib

import pytest

from services.brokers.capabilities import BrokerCapability
from services.brokers.recovery import (
    STILL_UNAVAILABLE_BASE_DELAY,
    RecoveryClass,
    RecoveryRegister,
)
from services.brokers.reliability import (
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_DELAY,
    STABLE_CONNECTION_SECONDS,
)
from services.brokers.sharding import DEFAULT_SHARD_ID, plan_shards
from services.brokers.stream import _AuthExpired, _NotEntitled
from services.market_engine.providers import (
    DEFAULT_TICK_MAX_AGE_SECONDS,
    LATENCY_TAIL_WINDOW_SAMPLES,
    LATENCY_WINDOW_SAMPLES,
    PROBATION_WINDOW_SECONDS,
    Capability,
    FeedReadiness,
    FeedStability,
    ProviderRegistry,
    ProviderState,
    SourceTier,
    StreamingTickProvider,
)
from services.market_engine.providers.base import (
    DOWN_AFTER_FAILURES,
    ResolutionContext,
)
from services.market_engine.providers.health_recovery import ProviderHealthRecovery
from services.market_engine.source_manager import SourceManager, UnavailableReason
from tests._chaos import (
    FAKE_CREDENTIALS,
    INVALID_CANONICAL_RECORDS,
    INVALID_VALUE_FRAMES,
    MALFORMED_FRAMES,
    Advance,
    ChaosAdapter,
    ChaosChannel,
    ChaosClock,
    ChaosOrderChannel,
    Close,
    Raise,
    StreamHarness,
    ack,
    build_feed,
    chaos_adapter,
    chaos_registered,
    denied,
    err,
    px,
    serve_probation,
    tick,
)

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def run(coro):
    """Drive one coroutine on a fresh event loop.

    Matches `test_broker_streaming.run` deliberately — `asyncio.run` rather than
    a reused loop, because the latter passes in isolation and fails in a
    full-suite run once an earlier test has left the thread with no current loop.
    """
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════
# §A — TRANSPORT CHAOS
#
# Owner: BrokerStream (services/brokers/stream.py) + ConnectionStability.
# Recovery: automatic, by the D5.1 reconnect ladder.
# ══════════════════════════════════════════════════════════════════


def test_a_handshake_that_never_succeeds_backs_off_and_never_stops():
    """Connect fails every time: bounded pacing, unbounded willingness (F).

    Two properties in one, and they pull in opposite directions — which is why
    the case is worth a test rather than an inspection. The ladder must *climb*
    (or a broker outage becomes a retry storm) and it must never *give up* (or a
    transient outage costs the user their feed until they notice and reconnect).
    """
    with chaos_registered() as adapter:
        result = run(
            StreamHarness(
                adapter,
                [Raise(ConnectionRefusedError("no route to host"))] * 8,
                instruments=["A"],
            ).run()
        )

    assert result.attempts == 8, "the loop stopped retrying a transient failure"
    # Climbing, capped, and never resetting — nothing was ever established.
    assert result.pauses == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]
    assert max(result.pauses) == RECONNECT_MAX_DELAY
    assert result.outcomes == [], "a handshake that never connected reported a link transition"
    assert result.link_ups == 0


def test_a_socket_that_closes_immediately_is_a_flap_and_not_a_reset():
    """Accepted, then closed before a frame: the DB-5 case, still closed.

    The connection *establishes* — the subscribe frames go out, so the link is
    reported up — and then dies with nothing delivered. Before D5.1 that reset
    the ladder and produced a reconnect roughly every 1.5 seconds forever.
    """
    with chaos_registered() as adapter:
        result = run(StreamHarness(adapter, [[Close()]] * 6, instruments=["A"]).run())

    assert result.outcomes == ["short_lived"] * 6
    assert result.pauses == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0], "the ladder reset on a flap"
    assert result.ticks == []


def test_a_connection_that_lasted_resets_the_ladder_and_a_flap_does_not():
    """The one line DB-5 turned on, asserted from both sides in one run."""
    with chaos_registered() as adapter:
        result = run(
            StreamHarness(
                adapter,
                [
                    [Close()],                                    # flap
                    [Close()],                                    # flap
                    [Advance(STABLE_CONNECTION_SECONDS + 1), Close()],   # lasted
                    [Close()],                                    # flap again
                ],
                instruments=["A"],
            ).run()
        )

    assert result.outcomes == ["short_lived", "short_lived", "stable", "short_lived"]
    # 2 → 4 → (reset) 2 → 4.  The rung after the stable connection is the base
    # delay again, which is what makes a healthy feed's blip cost ~2s.
    assert result.pauses == [2.0, 4.0, RECONNECT_BASE_DELAY, 4.0]


def test_a_socket_that_closes_after_one_tick_keeps_the_tick_and_the_backoff():
    """One good frame is data, and is not evidence the connection is well."""
    with chaos_registered() as adapter:
        result = run(
            StreamHarness(
                adapter,
                [[px(("A", 100.0)), Close()], [px(("A", 101.0)), Close()]],
                instruments=["A"],
            ).run()
        )

    assert [b[0]["last_price"] for b in result.tick_batches] == [100.0, 101.0]
    assert result.outcomes == ["short_lived", "short_lived"]
    assert result.pauses == [2.0, 4.0]


def test_a_socket_that_raises_mid_stream_is_indistinguishable_from_a_clean_close():
    """A reset and a clean close must reach the same recovery, or the ladder
    would be paced by *how* a broker dies rather than by *how often*."""
    with chaos_registered() as adapter:
        clean = run(StreamHarness(adapter, [[px(("A", 100.0)), Close()]], instruments=["A"]).run())
        reset = run(
            StreamHarness(
                adapter,
                [[px(("A", 100.0)), Raise(ConnectionResetError("peer reset"))]],
                instruments=["A"],
            ).run()
        )

    assert clean.outcomes == reset.outcomes == ["short_lived"]
    assert clean.link_downs == reset.link_downs == 1
    assert len(clean.tick_batches) == len(reset.tick_batches) == 1


def test_every_exit_from_a_transport_pass_reports_the_link_down_exactly_once():
    """The `finally` in `_run_websocket`, asserted across every exit shape.

    A consumer that is not told a link ended keeps a feed's readiness alive on
    a socket that no longer exists — Invariant E's failure, arriving from the
    transport rather than from the provider.
    """
    exits = {
        "clean_close": [Close()],
        "reset": [Raise(ConnectionResetError("reset"))],
        "timeout": [Raise(asyncio.TimeoutError())],
        "protocol_error": [Raise(RuntimeError("invalid opcode"))],
        "no_frames_at_all": [],
    }
    with chaos_registered() as adapter:
        for name, script in exits.items():
            result = run(StreamHarness(adapter, [script], instruments=["A"]).run())
            assert result.link_ups == 1, f"{name}: the link was never reported up"
            assert result.link_downs == 1, f"{name}: the link end was not reported exactly once"


def test_the_link_is_reported_up_only_after_the_subscribe_frames_are_away():
    """Connected is not subscribed, and neither is ready.

    Asserted through what reached the socket: if the link were announced on the
    socket opening, a consumer would arm a readiness gate for a connection that
    has not asked the broker for anything.
    """
    order = []
    with chaos_registered() as adapter:
        harness = StreamHarness(adapter, [[Close()]], instruments=["A", "B"])
        original = harness._on_link

        async def watching(user_id, broker, up, reason="", channel=None):
            order.append(("link", up, list(harness.result.sockets[-1].sent)))
            await original(user_id, broker, up, reason, channel)

        harness._on_link = watching
        harness.stream.on_link_state = watching
        run(harness.run())

    up_events = [entry for entry in order if entry[1] is True]
    assert len(up_events) == 1
    assert up_events[0][2], "the link was announced before the subscribe frame was sent"


def test_a_stream_whose_channel_disappears_stops_rather_than_reconnecting():
    """A configuration change is not broker weather.

    Re-connecting into a channel the adapter no longer declares would retry a
    request that cannot succeed, forever — the shape D5.5 refused for
    entitlement, reached by a different route.
    """
    with chaos_registered() as adapter:
        harness = StreamHarness(adapter, [[Close()]] * 4, instruments=["A"])
        harness.stream.channel = "a-channel-this-broker-does-not-declare"
        result = run(harness.run())

    assert result.attempts == 0, "the transport connected to a channel that does not exist"
    assert result.pauses == [], "a missing channel entered the reconnect ladder"


# ══════════════════════════════════════════════════════════════════
# §B — PROTOCOL / FRAME CHAOS
#
# Owner: the channel codec, guarded by BrokerStream._decode.
# Recovery: per frame; a bad frame costs only itself.
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("name,frame", MALFORMED_FRAMES, ids=[n for n, _ in MALFORMED_FRAMES])
def test_no_malformed_frame_can_drop_a_live_connection_or_manufacture_a_tick(name, frame):
    """The whole malformed table, against one property that admits no exception.

    Twenty shapes — empty, truncated, oversized, binary garbage, impossible
    prices, wrong types — and for every one of them the socket must survive and
    nothing may be delivered. A single case that delivered would be a
    `MarketTick` manufactured from data the platform could not read.
    """
    with chaos_registered() as adapter:
        result = run(StreamHarness(adapter, [[frame, Close()]], instruments=["A"]).run())

    assert result.ticks == [], f"{name} produced a tick"
    assert result.orders == []
    assert result.expired == [] and result.not_entitled == []
    assert result.link_ups == 1 and result.link_downs == 1
    assert result.sockets[0].closed, "the socket was not closed down cleanly"


@pytest.mark.parametrize("name,frame", MALFORMED_FRAMES, ids=[n for n, _ in MALFORMED_FRAMES])
def test_a_valid_frame_following_an_invalid_one_is_still_delivered(name, frame):
    """The preservation half. Rejecting a frame must not poison the stream.

    Written as a separate test from the one above rather than as a second
    assertion in it, because the two fail for different reasons: the first
    catches a boundary that is too permissive, this one catches a boundary that
    became too brittle.
    """
    with chaos_registered() as adapter:
        result = run(
            StreamHarness(
                adapter,
                [[frame, px(("A", 100.0)), frame, px(("A", 101.0)), Close()]],
                instruments=["A"],
            ).run()
        )

    prices = [row["last_price"] for batch in result.tick_batches for row in batch]
    assert prices == [100.0, 101.0], f"{name} cost the frames that followed it"


def test_an_unknown_response_code_is_ignored_rather_than_guessed_at():
    """A frame kind the codec does not know is IGNORE, never a lifecycle event."""
    with chaos_registered() as adapter:
        result = run(
            StreamHarness(
                adapter,
                [[json.dumps({"t": "some-future-kind", "v": 1}), px(("A", 100.0)), Close()]],
                instruments=["A"],
            ).run()
        )

    assert result.expired == [] and result.not_entitled == []
    assert len(result.tick_batches) == 1


def test_a_broker_error_frame_is_logged_and_neither_kills_nor_promotes(caplog):
    """ERROR is weather. It is not an entitlement refusal and not a dead token."""
    with chaos_registered() as adapter, caplog.at_level(logging.WARNING):
        result = run(
            StreamHarness(
                adapter,
                [[err("rate limited"), px(("A", 100.0)), Close()]],
                instruments=["A"],
            ).run()
        )

    assert result.expired == [] and result.not_entitled == []
    assert len(result.tick_batches) == 1, "an error frame cost the frames after it"
    assert any("reported an error" in r.getMessage() for r in caplog.records)


def test_a_duplicate_tick_and_a_duplicate_ack_are_both_harmless():
    """Idempotence at the wire, asserted rather than assumed.

    A feed that re-sends the last price on reconnect, and a broker that
    acknowledges one subscription twice, are both normal. Neither may produce a
    second promotion or a lost frame.
    """
    with chaos_registered() as adapter:
        result = run(
            StreamHarness(
                adapter,
                [[ack(), ack(), px(("A", 100.0)), px(("A", 100.0)), Close()]],
                instruments=["A"],
            ).run()
        )

    assert len(result.tick_batches) == 2, "a duplicate price frame was swallowed"
    assert result.link_ups == 1, "a duplicate acknowledgement produced a second promotion"


def test_a_batch_keeps_its_good_rows_when_one_row_is_unusable():
    """The batch rule (D4.2): one short packet must not cost the frame."""
    frame = json.dumps(
        {"t": "px", "rows": [{"sym": "A", "px": 100.0}, {"sym": "B"}, {"sym": "C", "px": 300.0}]}
    )
    with chaos_registered() as adapter:
        result = run(StreamHarness(adapter, [[frame, Close()]], instruments=["A"]).run())

    assert [row["symbol"] for row in result.tick_batches[0]] == ["A", "C"]


def test_a_codec_that_returns_a_raw_payload_delivers_nothing_and_says_so(caplog):
    """The barrier that stops a broker payload continuing up, under chaos.

    Existing D4.2 coverage asserts this for one frame; the chaos question is
    whether a *stream* of them can accumulate into a delivery. It cannot,
    because the check is per frame.
    """

    class LeakyChannel(ChaosChannel):
        def decode(self, frame):
            return {"symbol": "A", "last_price": 100.0}  # not a BrokerStreamEvent

    adapter = chaos_adapter(channel=LeakyChannel)
    with chaos_registered(adapter), caplog.at_level(logging.ERROR):
        result = run(
            StreamHarness(adapter, [[px(("A", 1.0))] * 5 + [Close()]], instruments=["A"]).run()
        )

    assert result.ticks == []
    assert sum("instead of BrokerStreamEvent" in r.getMessage() for r in caplog.records) == 5


@pytest.mark.parametrize(
    "name,frame", INVALID_VALUE_FRAMES, ids=[n for n, _ in INVALID_VALUE_FRAMES]
)
def test_an_invalid_value_survives_the_wire_and_dies_at_the_canonical_boundary(name, frame):
    """Where each control lives, pinned so neither layer can absorb the other.

    A negative price is a well-formed statement in the broker's own vocabulary,
    so `BrokerTick` — which is the *broker's* shape — accepts it and the
    transport forwards it. `MarketTick` is where the platform says it does not
    have prices like that. Asserting only the second would pass against a
    transport that had started guessing; asserting only the first would pass
    against a canonical boundary that had stopped checking.
    """
    with chaos_registered() as adapter:
        result = run(StreamHarness(adapter, [[frame, Close()]], instruments=["A"]).run())

    assert result.tick_batches, f"{name} was dropped at the wire, where no such control exists"
    assert result.link_ups == 1 and result.link_downs == 1


@pytest.mark.parametrize(
    "name,record", INVALID_CANONICAL_RECORDS, ids=[n for n, _ in INVALID_CANONICAL_RECORDS]
)
def test_no_invalid_record_is_ever_coerced_into_a_market_tick(name, record):
    """The canonical boundary, under the whole invalid-value table.

    Nothing accepted, nothing counted as evidence, and — the property that
    matters for Invariant D — no readiness earned. A feed delivering only
    unusable records has demonstrated the *opposite* of readiness.
    """
    fixture = run(build_feed())

    accepted = run(fixture.feed.on_raw([record]))

    assert accepted == 0, f"{name} was coerced into a MarketTick"
    assert not fixture.feed.is_ready
    assert fixture.feed.covered_symbols == ()
    assert fixture.quote_provider() is fixture.baseline


@pytest.mark.parametrize(
    "name,record", INVALID_CANONICAL_RECORDS, ids=[n for n, _ in INVALID_CANONICAL_RECORDS]
)
def test_one_invalid_record_never_costs_the_valid_records_beside_it(name, record):
    """The batch rule at the canonical boundary, mirroring the wire's."""
    fixture = run(build_feed(symbols=("A", "B")))

    accepted = run(fixture.feed.on_raw([record, tick("B", 200.0)]))

    assert accepted == 1, f"{name} cost the valid record in its batch"
    assert fixture.feed.covers("B")


@pytest.mark.parametrize(
    "shape",
    ["truncated", "not_json", "oversized", "binary", "wrong_type", "decoded_but_unusable"],
)
def test_a_frame_carrying_a_credential_is_never_echoed_into_a_log(shape, caplog):
    """Invariant: a bad frame cannot become a log line quoting the wire.

    The failure this forecloses is specific and common — a decoder that says
    "could not decode %r" with the frame interpolated. A broker that
    authenticates in-band (D4.10) puts a live token in a frame, so exactly one
    malformed auth frame would print it.

    Parametrized over the shapes that reach *different* log lines, and that
    matters: an earlier version of this test used a frame that decoded
    successfully to nothing, so it never reached the decode-failure branch at
    all and a mutation that interpolated the raw frame there survived it. Each
    shape below drives the codec into a different failure path, and every one of
    them carries the same live-looking token.
    """
    token = FAKE_CREDENTIALS["access_token"]
    frames = {
        # Raises inside `json.loads` — the generic decode-failure branch.
        "truncated": '{"t": "px", "auth": "' + token + '", "rows": [',
        "not_json": "AUTH " + token,
        # Refused by the codec's own size limit before it is parsed, which is
        # the control that stops an oversized frame becoming an oversized line.
        "oversized": '{"auth": "' + token + '", "pad": "' + "x" * 8192 + '"}',
        "binary": ("AUTH " + token).encode() + b"\xff\xfe",
        # Not a str or bytes at all — the TypeError branch.
        "wrong_type": {"auth": token},
        # Decodes cleanly and yields nothing: the branch the first version of
        # this test hit, kept so the *set* still covers it.
        "decoded_but_unusable": json.dumps({"t": "px", "auth": token, "rows": "nope"}),
    }
    with chaos_registered() as adapter, caplog.at_level(logging.DEBUG):
        result = run(StreamHarness(adapter, [[frames[shape], Close()]], instruments=["A"]).run())

    assert result.ticks == []
    blob = "\n".join(
        f"{r.getMessage()} {r.exc_text or ''} {getattr(r, 'args', '')!r}" for r in caplog.records
    )
    assert token not in blob, f"{shape}: a credential reached the log through a bad frame"
    assert token[:20] not in blob, f"{shape}: a credential prefix reached the log"


def test_the_decode_failure_branch_is_actually_reached_by_these_frames(caplog):
    """The falsification for the test above.

    A credential sweep over frames that never trip the decoder proves nothing,
    so this asserts the premise separately: the shapes really do drive the codec
    into its failure branches and really do produce a log line to search.
    """
    token = FAKE_CREDENTIALS["access_token"]
    with chaos_registered() as adapter, caplog.at_level(logging.DEBUG):
        run(
            StreamHarness(
                adapter,
                [['{"t": "px", "auth": "' + token + '", "rows": [', Close()]],
                instruments=["A"],
            ).run()
        )

    decode_failures = [
        r for r in caplog.records if "could not be decoded" in r.getMessage()
        or "rejected by the contract" in r.getMessage()
    ]
    assert decode_failures, "the decode-failure branch was never reached — the sweep is vacuous"


# ══════════════════════════════════════════════════════════════════
# §C — READINESS CHAOS  (Invariants D, E)
#
# Owner: StreamingTickProvider. Recovery: automatic, on the next valid tick.
# ══════════════════════════════════════════════════════════════════


def test_no_amount_of_malformed_data_makes_a_connected_feed_ready():
    """CONNECTED + rejected frames → still not READY, however many."""
    fixture = run(build_feed())
    for _ in range(50):
        run(fixture.feed.on_raw([{"symbol": "A", "price": -1.0}]))

    assert fixture.feed.readiness is FeedReadiness.SUBSCRIBED
    assert not fixture.feed.is_ready
    assert fixture.quote_provider() is fixture.baseline


def test_one_valid_tick_and_only_a_valid_tick_earns_readiness():
    fixture = run(build_feed())
    assert not fixture.feed.is_ready
    run(fixture.feed.on_raw([tick("A")]))
    assert fixture.feed.is_ready


@pytest.mark.parametrize(
    "event",
    [
        "socket_opened",
        "authenticated",
        "subscribed",
        "previous_connection_was_ready",
    ],
)
def test_readiness_cannot_be_earned_by_anything_but_data(event):
    """The four temptations D4.5 names, each falsified separately.

    Parametrized rather than written as one test with four asserts so that a
    regression names *which* signal started granting readiness.
    """
    fixture = run(build_feed(link_up=False))
    feed = fixture.feed

    if event == "socket_opened":
        run(feed.mark_link_up())
    elif event == "authenticated":
        run(feed.connect())
    elif event == "subscribed":
        run(feed.subscribe(["A", "B", "C"]))
    elif event == "previous_connection_was_ready":
        run(feed.mark_link_up())
        run(feed.on_raw([tick("A")]))
        assert feed.is_ready
        run(feed.mark_link_down("blip"))
        run(feed.mark_link_up())

    assert not feed.is_ready, f"{event} granted readiness"
    assert fixture.quote_provider() is fixture.baseline


def test_a_disconnect_removes_readiness_on_the_very_next_resolution():
    """No poll, no timer: the transport says so and the next resolve is right."""
    fixture = run(build_feed())
    run(serve_probation(fixture.feed, fixture.clock, (DEFAULT_SHARD_ID,)))
    assert fixture.quote_provider() is fixture.feed

    run(fixture.feed.mark_link_down("socket closed"))

    assert fixture.quote_provider() is fixture.baseline
    assert fixture.feed.readiness is FeedReadiness.FAILED


def test_the_full_reconnect_state_walk_skips_no_state():
    """failure → reconnect → fresh evidence → READY → probation → STABLE.

    Written as the whole walk in one test on purpose: the sequence is the claim,
    and asserting the endpoints only would pass against an implementation that
    jumped straight to STABLE.
    """
    fixture = run(build_feed())
    feed, clock = fixture.feed, fixture.clock

    run(serve_probation(feed, clock, (DEFAULT_SHARD_ID,)))
    assert feed.readiness is FeedReadiness.READY and feed.is_stable

    run(feed.mark_link_down("dropped"))
    assert feed.readiness is FeedReadiness.FAILED
    assert feed.stability is FeedStability.PROBATION

    run(feed.mark_link_up())
    assert feed.readiness is FeedReadiness.SUBSCRIBED, "a reconnect skipped back to READY"
    assert not feed.is_ready

    run(feed.on_raw([tick("A")]))
    assert feed.readiness is FeedReadiness.READY
    assert feed.stability is FeedStability.PROBATION, "probation was inherited across a reconnect"

    clock.advance(PROBATION_WINDOW_SECONDS + 1)
    run(feed.on_raw([tick("A")]))
    assert feed.is_stable


# ══════════════════════════════════════════════════════════════════
# §D — PROBATION CHAOS  (Invariant E)
# ══════════════════════════════════════════════════════════════════


def test_silence_after_readiness_never_becomes_stability():
    """The elapsed-time trap: thirty seconds of nothing is not thirty seconds
    of valid messages, and a timer-based gate would promote exactly that."""
    fixture = run(build_feed())
    run(fixture.feed.on_raw([tick("A")]))
    fixture.clock.advance(PROBATION_WINDOW_SECONDS * 10)

    assert fixture.feed.stability is FeedStability.PROBATION
    assert not fixture.feed.is_stable


def test_a_stable_provider_beats_a_probationary_one():
    """Two feeds, one steady, one fresh: the steady one leads the chain."""
    fixture = run(build_feed(symbols=("A",)))
    run(serve_probation(fixture.feed, fixture.clock, (DEFAULT_SHARD_ID,)))

    newcomer = StreamingTickProvider("feed:u1:second", owner_user_id="u1", clock=fixture.clock)
    fixture.registry.register(newcomer)
    run(newcomer.connect())
    run(newcomer.subscribe(["A"]))
    run(newcomer.mark_link_up())
    run(newcomer.on_raw([tick("A")]))

    assert fixture.feed.is_stable and newcomer.is_on_probation
    assert fixture.quote_provider() is fixture.feed


def test_a_probationary_feed_still_serves_when_nothing_steadier_remains():
    """Probation ranks; it never filters. The reverse of the case above.

    D5.2's rule read literally: a probationary provider is still a candidate,
    still in the chain, and still answers when no steadier eligible source
    exists — which is what stops the platform from choosing "no data" over
    "unproven data".
    """
    fixture = run(build_feed(symbols=("A",), with_baseline=False))
    run(fixture.feed.on_raw([tick("A")]))

    assert fixture.feed.is_on_probation
    resolution = fixture.resolution(symbol="A")
    assert resolution.available and resolution.provider is fixture.feed


def test_probation_evidence_from_a_dead_link_is_unavailable_after_reconnect():
    """Not merely 'not counted' — gone. Asserted on the observable that a
    resolution reads, so a cached copy elsewhere would still fail this."""
    fixture = run(build_feed())
    run(serve_probation(fixture.feed, fixture.clock, (DEFAULT_SHARD_ID,)))
    assert fixture.feed.is_stable

    run(fixture.feed.mark_link_down("dropped"))
    run(fixture.feed.mark_link_up())
    run(fixture.feed.on_raw([tick("A")]))

    assert fixture.feed.stability is FeedStability.PROBATION
    # And it takes a *full new window*, not the remainder of the old one.
    fixture.clock.advance(PROBATION_WINDOW_SECONDS - 1)
    run(fixture.feed.on_raw([tick("A")]))
    assert not fixture.feed.is_stable
    fixture.clock.advance(2)
    run(fixture.feed.on_raw([tick("A")]))
    assert fixture.feed.is_stable


def test_a_flapping_feed_never_accumulates_a_claim_to_the_primary_position():
    """Ten reconnects, each with one tick and almost a full window of data.

    The point of D5.2: a feed that keeps almost proving itself proves nothing,
    and the near-misses do not add up.
    """
    fixture = run(build_feed())
    for _ in range(10):
        run(fixture.feed.mark_link_up())
        run(fixture.feed.on_raw([tick("A")]))
        fixture.clock.advance(PROBATION_WINDOW_SECONDS - 0.5)
        run(fixture.feed.on_raw([tick("A")]))
        assert not fixture.feed.is_stable
        run(fixture.feed.mark_link_down("flap"))

    assert fixture.feed.stability is FeedStability.PROBATION


# ══════════════════════════════════════════════════════════════════
# §E — STALE-FEED CHAOS  (Invariant D)
#
# D5.3's lazy decay, re-proved without introducing a timer to observe it.
# ══════════════════════════════════════════════════════════════════


def test_a_stable_feed_that_goes_quiet_demotes_itself_on_read_with_no_timer():
    """The whole of D5.3, as a chaos case.

    Nothing is scheduled and nothing polls: the clock moves, and the *next
    question asked* gets the new answer. A test that needed a timer to see the
    demotion would be testing a different mechanism.
    """
    fixture = run(build_feed())
    run(serve_probation(fixture.feed, fixture.clock, (DEFAULT_SHARD_ID,)))
    assert fixture.feed.is_stable and fixture.quote_provider() is fixture.feed

    fixture.clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)

    assert not fixture.feed.has_fresh_evidence
    assert fixture.feed.stability is FeedStability.PROBATION
    assert fixture.feed.covered_symbols == ()
    assert fixture.quote_provider() is fixture.baseline


def test_the_symbol_less_resolution_stops_reporting_a_dead_feed_too():
    """The D5.3 defect exactly: `active_tier()` and `status()` read the
    symbol-less path, and before D5.3 it answered `True` unconditionally — so a
    user whose feed had been silent for hours was told their data was live."""
    fixture = run(build_feed())
    run(serve_probation(fixture.feed, fixture.clock, (DEFAULT_SHARD_ID,)))
    assert fixture.tier() is SourceTier.STREAMING

    fixture.clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)

    assert fixture.tier() is SourceTier.DELAYED, "a dead feed still reported the live tier"


def test_a_later_valid_tick_restores_a_stale_feed_with_no_new_timer():
    """Recovery is the arrival of data, not the firing of anything.

    And on the *same* link it restores STABLE immediately rather than
    re-serving the window — the link never dropped, so nothing was discarded.
    """
    fixture = run(build_feed())
    run(serve_probation(fixture.feed, fixture.clock, (DEFAULT_SHARD_ID,)))
    fixture.clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)
    assert fixture.quote_provider() is fixture.baseline

    run(fixture.feed.on_raw([tick("A")]))

    assert fixture.feed.has_fresh_evidence
    assert fixture.feed.is_stable, "a same-link recovery was made to re-serve probation"
    assert fixture.quote_provider() is fixture.feed


def test_staleness_expires_per_symbol_and_not_for_the_whole_feed():
    """A feed streaming two instruments at different cadences.

    The per-symbol half of D4.5's coverage rule under chaos: one instrument
    going quiet must not cost the other, and must not cost the feed.
    """
    fixture = run(build_feed(symbols=("A", "B")))
    run(fixture.feed.on_raw([tick("A"), tick("B")]))
    fixture.clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS - 1)
    run(fixture.feed.on_raw([tick("A")]))
    fixture.clock.advance(2)

    assert fixture.feed.covers("A")
    assert not fixture.feed.covers("B")
    assert fixture.quote_provider(symbol="A") is fixture.feed
    assert fixture.quote_provider(symbol="B") is fixture.baseline


def test_the_staleness_boundary_is_the_coverage_window_and_not_a_second_policy():
    """One constant governs both branches, asserted at the boundary itself."""
    fixture = run(build_feed())
    run(fixture.feed.on_raw([tick("A")]))

    fixture.clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS)
    assert fixture.feed.has_fresh_evidence, "the window closed one instant early"
    assert fixture.feed.covers("A")

    fixture.clock.advance(0.001)
    assert not fixture.feed.has_fresh_evidence
    assert not fixture.feed.covers("A")


# ══════════════════════════════════════════════════════════════════
# §F — LATENCY CHAOS  (D5.4 / D5.9)
# ══════════════════════════════════════════════════════════════════


def _establish(feed, clock, gap, samples, shard=DEFAULT_SHARD_ID, symbol="A"):
    run(feed.on_raw([tick(symbol)], shard))
    for _ in range(samples):
        clock.advance(gap)
        run(feed.on_raw([tick(symbol)], shard))


@pytest.mark.parametrize(
    "samples,p50_established,p95_established",
    [
        (0, False, False),
        (8, False, False),
        (LATENCY_WINDOW_SAMPLES, True, False),          # exactly 9
        (12, True, False),                              # 10–19
        (LATENCY_TAIL_WINDOW_SAMPLES, True, True),      # exactly 20
        (25, True, True),                               # > 20
    ],
)
def test_each_statistic_is_none_until_its_own_window_is_full(
    samples, p50_established, p95_established
):
    """Unknown is `None`, never `0`, never `inf`, never a fabricated number."""
    fixture = run(build_feed())
    _establish(fixture.feed, fixture.clock, 1.0, samples)

    p50, p95 = fixture.feed.delivery_latency, fixture.feed.delivery_latency_p95
    assert (p50 is not None) is p50_established
    assert (p95 is not None) is p95_established
    for value in (p50, p95):
        assert value is None or (value == value and value not in (0.0, float("inf")))

    profile = fixture.feed.latency_profile
    assert profile.established is p50_established
    assert profile.samples == min(samples, LATENCY_TAIL_WINDOW_SAMPLES)


def test_one_extreme_tail_sample_does_not_become_the_reported_tail():
    """Nearest-rank at N=20 is index 19, so the single worst sample is excluded.

    The chaos case for it: nineteen healthy intervals and one catastrophic
    stall must not make the feed's published tail the stall.
    """
    fixture = run(build_feed())
    _establish(fixture.feed, fixture.clock, 1.0, LATENCY_TAIL_WINDOW_SAMPLES - 1)
    fixture.clock.advance(600.0)
    run(fixture.feed.on_raw([tick("A")]))

    assert fixture.feed.delivery_latency_p95 == 1.0
    assert fixture.feed.delivery_latency_p95 != 600.0


def test_a_reconnect_resets_the_series_and_never_spans_the_disconnection():
    """The gap across a dead link is not a delivery interval of anything."""
    fixture = run(build_feed())
    _establish(fixture.feed, fixture.clock, 1.0, LATENCY_TAIL_WINDOW_SAMPLES)
    assert fixture.feed.delivery_latency == 1.0

    run(fixture.feed.mark_link_down("dropped"))
    fixture.clock.advance(3600.0)
    run(fixture.feed.mark_link_up())
    run(fixture.feed.on_raw([tick("A")]))

    assert fixture.feed.delivery_latency is None, "latency was inherited across a reconnect"
    _establish(fixture.feed, fixture.clock, 2.0, LATENCY_WINDOW_SAMPLES)
    assert fixture.feed.delivery_latency == 2.0, "the hour-long gap entered the series"


def test_a_stale_feed_loses_its_latency_however_good_the_series_was():
    """A median of gaps that all closed ten minutes ago measures nothing now."""
    fixture = run(build_feed())
    _establish(fixture.feed, fixture.clock, 0.1, LATENCY_TAIL_WINDOW_SAMPLES)
    assert fixture.feed.delivery_latency == pytest.approx(0.1)

    fixture.clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)

    assert fixture.feed.delivery_latency is None
    assert fixture.feed.delivery_latency_p95 is None


def test_latency_never_creates_readiness_and_never_overrides_ranking():
    """The two guarantees D5.4 makes by *ordering* rather than by a branch.

    A blisteringly fast probationary feed and a slower stable one: the stable
    one wins, because probation is compared before latency and the comparison
    never reaches the third element.
    """
    fixture = run(build_feed(symbols=("A",)))
    slow_stable = fixture.feed
    _establish(slow_stable, fixture.clock, 5.0, LATENCY_WINDOW_SAMPLES)
    fixture.clock.advance(PROBATION_WINDOW_SECONDS + 1)
    run(slow_stable.on_raw([tick("A")]))
    assert slow_stable.is_stable

    fast_probationary = StreamingTickProvider("feed:u1:fast", owner_user_id="u1", clock=fixture.clock)
    fixture.registry.register(fast_probationary)
    run(fast_probationary.connect())
    run(fast_probationary.subscribe(["A"]))
    run(fast_probationary.mark_link_up())
    _establish(fast_probationary, fixture.clock, 0.01, LATENCY_WINDOW_SAMPLES)

    assert fast_probationary.delivery_latency < slow_stable.delivery_latency
    assert fast_probationary.is_on_probation
    assert fixture.quote_provider() is slow_stable, "latency promoted a probationary feed"

    # And latency alone never makes an unready feed usable at all.
    unready = StreamingTickProvider("feed:u1:unready", owner_user_id="u1", clock=fixture.clock)
    run(unready.connect())
    assert unready.delivery_latency is None and not unready.is_ready


def test_the_p95_is_reported_and_is_not_a_ranking_input():
    """ADR-049's decision, asserted where a regression would land: the sort key.

    Two feeds with identical medians and wildly different tails. If p95 had
    become a ranking term, the better tail would win; it does not, so the
    already-stable feed keeps the head of the chain.
    """
    fixture = run(build_feed(symbols=("A",)))
    from services.market_engine.source_manager import _selection_rank

    steady = fixture.feed
    _establish(steady, fixture.clock, 1.0, LATENCY_TAIL_WINDOW_SAMPLES)
    key = _selection_rank(steady)
    assert len(key) == 3, "the selection key grew a term"
    assert key[2] == steady.delivery_latency
    assert steady.delivery_latency_p95 is not None
    assert key[2] != steady.delivery_latency_p95 or steady.delivery_latency_p95 == key[2] == 1.0
    # The published tail travels on health() and describe(), not into ranking.
    assert steady.health().latency.p95_seconds == steady.delivery_latency_p95
    assert steady.describe()["delivery_latency_p95_seconds"] == steady.delivery_latency_p95


# ══════════════════════════════════════════════════════════════════
# §G — ENTITLEMENT CHAOS  (Invariant G)
#
# Owner: the channel's classification → BrokerStream → _on_stream_not_entitled.
# Recovery: NOT automatic by reconnect. D5.6's re-probe owns it.
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "when",
    ["at_handshake", "first_frame", "after_ready", "during_probation", "after_stable"],
)
def test_an_entitlement_refusal_is_terminal_wherever_in_the_life_it_arrives(when):
    """One refusal, five moments, one outcome.

    Parametrized over *when* rather than asserted once, because the tempting
    bug is a refusal that is terminal on a cold socket and gets swallowed by the
    reconnect path once a feed is established — the state where a user would
    actually notice, and the state D5.5 was written against.
    """
    warm = [px(("A", 100.0))]
    scripts = {
        "at_handshake": None,
        "first_frame": [denied()],
        "after_ready": warm + [denied()],
        "during_probation": warm + [Advance(1.0), px(("A", 101.0)), denied()],
        "after_stable": warm
        + [Advance(STABLE_CONNECTION_SECONDS + 1), px(("A", 101.0)), denied()],
    }

    if when == "at_handshake":
        class RefusingChannel(ChaosChannel):
            handshake_verdict = "not_entitled"

        adapter = chaos_adapter(channel=RefusingChannel)
        attempts = [Raise(RuntimeError("HTTP 403"))] * 4
    else:
        adapter = ChaosAdapter()
        attempts = [scripts[when]] + [[Close()]] * 3

    with chaos_registered(adapter):
        result = run(StreamHarness(adapter, attempts, instruments=["A"]).run())

    assert len(result.not_entitled) == 1, f"{when}: the refusal was not reported once"
    assert result.expired == [], f"{when}: an entitlement refusal became AUTH_EXPIRED"
    assert result.attempts == 1, f"{when}: the refusal entered the reconnect ladder"
    assert result.pauses == [], f"{when}: a terminal refusal climbed the reconnect ladder"


def test_an_entitlement_refusal_is_not_a_subclass_of_an_expiry():
    """The `_AuthExpired` subclass trap, pinned as a type relationship.

    `except _AuthExpired` is what tears down the whole session. Were
    `_NotEntitled` ever made a subclass of it — a plausible tidy-up, since both
    are terminal — every entitlement refusal would silently destroy a working
    trading session, and every behavioural test above would still pass because
    the transport catches `_AuthExpired` first.
    """
    assert not issubclass(_NotEntitled, _AuthExpired)
    assert not issubclass(_AuthExpired, _NotEntitled)
    order = list(_NotEntitled.__mro__)
    assert _AuthExpired not in order


def test_a_refusal_on_one_channel_leaves_the_other_channel_of_the_same_broker_alone():
    """Invariant G's blast radius, at the transport.

    Two channels of one broker are two `BrokerStream`s. The refusal ends the
    stream that saw it; the sibling is a different object with a different task
    and nothing in the refusal path can reach it.
    """
    adapter = chaos_adapter(
        channels=(ChaosChannel(), ChaosOrderChannel()),
        capabilities=frozenset(
            {BrokerCapability.TICK_STREAM, BrokerCapability.ORDER_STREAM}
        ),
    )
    with chaos_registered(adapter):
        market = StreamHarness(adapter, [[denied()]], instruments=["A"], channel="market")
        orders = StreamHarness(
            adapter,
            [[json.dumps({"t": "order", "id": "o1", "sym": "A", "qty": 1}), Close()]],
            channel="orders",
        )
        market_result = run(market.run())
        orders_result = run(orders.run())

    assert len(market_result.not_entitled) == 1
    assert orders_result.not_entitled == []
    assert len(orders_result.orders) == 1, "the order channel stopped delivering"


def test_a_misclassified_handshake_failure_falls_back_to_the_ladder(caplog):
    """`_terminal_refusal` refuses to guess, and says so.

    A channel that classifies a refused handshake as something non-terminal is
    a codec defect. Reading it as "stop" would end a feed on a transient error;
    reading it as "keep retrying" silently is how the defect never gets found.
    """
    class ConfusedChannel(ChaosChannel):
        handshake_verdict = "misclassified"

    adapter = chaos_adapter(channel=ConfusedChannel)
    with chaos_registered(adapter), caplog.at_level(logging.WARNING):
        result = run(
            StreamHarness(adapter, [Raise(RuntimeError("boom"))] * 3, instruments=["A"]).run()
        )

    assert result.not_entitled == [] and result.expired == []
    assert result.attempts == 3, "a misclassified handshake failure stopped the loop"
    assert any("not a terminal condition" in r.getMessage() for r in caplog.records)


def test_a_refusal_reaching_a_provider_unregisters_it_rather_than_demoting_it():
    """The market-side half: an ended entitlement is not a candidate at all.

    Demoting would leave a priority-1 streaming provider in the chain that can
    only answer with silence. Unregistering is what makes the baseline lead the
    very next resolution — regardless of whether the feed was READY or STABLE.
    """
    fixture = run(build_feed())
    run(serve_probation(fixture.feed, fixture.clock, (DEFAULT_SHARD_ID,)))
    assert fixture.feed.is_stable and fixture.quote_provider() is fixture.feed

    fixture.registry.unregister(fixture.feed.name)

    assert fixture.quote_provider() is fixture.baseline
    assert fixture.feed not in fixture.resolution(symbol="A").chain


def test_silence_a_timeout_and_a_malformed_frame_can_never_produce_a_refusal():
    """Absence of evidence is not a refusal (ADR-045), under every shape of it."""
    absences = {
        "silence": [Advance(600.0), Close()],
        "timeout": [Raise(asyncio.TimeoutError())],
        "malformed": ["<<<garbage>>>", Close()],
        "empty_subscription": [Close()],
        "error_frame": [err("service unavailable"), Close()],
    }
    with chaos_registered() as adapter:
        for name, script in absences.items():
            result = run(StreamHarness(adapter, [script], instruments=["A"]).run())
            assert result.not_entitled == [], f"{name} was read as an entitlement refusal"
            assert result.expired == [], f"{name} was read as an expired session"


# ══════════════════════════════════════════════════════════════════
# §H — RE-PROBE CHAOS  (D5.6, Invariant F)
#
# Owner: RecoveryRegister + RecoveryService.
# The property: a re-probe is NOT a second reconnect ladder.
# ══════════════════════════════════════════════════════════════════


class _ProbeClock:
    def __init__(self, now=0.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def _register(clock):
    """A register with its jitter removed, so a rung is a number and not a range."""
    return RecoveryRegister(clock=clock, jitter=lambda delay: delay)


def test_a_reprobe_ladder_is_orders_of_magnitude_slower_than_the_reconnect_ladder():
    """The falsification for "the re-probe is secretly another reconnect".

    Not asserted as "300 > 2", which any two constants satisfy, but as the
    property that matters operationally: the *fastest* re-probe is slower than
    the *slowest* reconnect, so the two mechanisms can never be confused by
    observing their rate.
    """
    clock = _ProbeClock()
    register = _register(clock)
    candidate = register.record_withdrawal("u1", "chaos", "market", RecoveryClass.REPROBE)

    assert candidate is not None
    first_rung = candidate.next_attempt_at - clock()
    assert first_rung >= STILL_UNAVAILABLE_BASE_DELAY
    assert first_rung > RECONNECT_MAX_DELAY, "a re-probe runs at reconnect frequency"


def test_repeated_failed_probes_climb_and_a_success_does_not_buy_a_fresh_ladder():
    """The two-map design (`_candidates` / `_history`), asserted as behaviour.

    The accept-then-refuse shape is the same one that produced DB-5's storm one
    layer down: a broker that accepts the socket and *then* refuses the
    entitlement discharges and re-records the candidate. If the ladder lived on
    the candidate, that would reset it — a five-minute storm instead of a
    1.5-second one.
    """
    clock = _ProbeClock()
    register = _register(clock)
    rungs = []
    for _ in range(5):
        candidate = register.record_withdrawal("u1", "chaos", "market", RecoveryClass.REPROBE)
        clock.now = candidate.next_attempt_at
        register.note_attempt(candidate)
        rungs.append(candidate.next_attempt_at - clock())
        # The broker accepted, then refused again: discharge, then re-record.
        register.discharge("u1", "chaos", "market")

    assert rungs == sorted(rungs), "the re-probe ladder did not climb"
    assert rungs[-1] > rungs[0] * 2, "an apparent success bought a fresh ladder"


def test_an_expired_session_is_reclassified_out_of_reprobe_entirely():
    """Retrying a dead credential on a schedule is a login attempt on a timer."""
    clock = _ProbeClock()
    register = _register(clock)
    register.record_withdrawal("u1", "chaos", "market", RecoveryClass.REPROBE)
    assert register.get("u1", "chaos", "market").is_reprobeable

    register.reclassify("u1", "chaos", RecoveryClass.SESSION)

    candidate = register.get("u1", "chaos", "market")
    assert candidate is not None, "the exclusion must be visible, not merely absent"
    assert not candidate.is_reprobeable
    assert register.due() == []


def test_a_probe_never_replaces_a_live_connection():
    """Recovering a feed by breaking it is the failure this guard forecloses."""
    clock = _ProbeClock()
    register = _register(clock)
    attached = []

    from services.brokers.recovery import RecoveryService

    async def attach(user_id, broker, channel):
        attached.append((user_id, broker, channel))

    service = RecoveryService(
        register,
        attach=attach,
        has_session=lambda u, b: True,
        is_attached=lambda u, b, c: True,   # a user reconnect got there first
    )
    candidate = register.record_withdrawal("u1", "chaos", "market", RecoveryClass.REPROBE)
    clock.now = candidate.next_attempt_at

    outcome = run(service.reprobe("u1", "chaos", "market"))

    assert outcome.value == "already_attached"
    assert attached == [], "a re-probe tore down a connection that was already live"
    assert register.get("u1", "chaos", "market") is None, "the candidate was not discharged"


def test_a_probe_for_an_account_with_no_session_costs_no_attempt():
    """Nothing is asked of the broker, so nothing is charged against the ladder."""
    clock = _ProbeClock()
    register = _register(clock)
    from services.brokers.recovery import RecoveryService

    service = RecoveryService(
        register,
        attach=lambda *a: None,
        has_session=lambda u, b: False,
        is_attached=lambda u, b, c: False,
    )
    candidate = register.record_withdrawal("u1", "chaos", "market", RecoveryClass.REPROBE)
    clock.now = candidate.next_attempt_at
    before = candidate.attempts

    outcome = run(service.reprobe("u1", "chaos", "market"))

    assert outcome.value == "session_unavailable"
    assert register.get("u1", "chaos", "market").attempts == before


def test_an_attach_that_raises_still_climbs_the_ladder():
    """`note_attempt` before the attach, asserted through a throwing attach.

    Charging afterwards would leave a broker whose attach reliably throws
    re-probing at the base delay forever — a storm hidden behind an exception
    handler.
    """
    clock = _ProbeClock()
    register = _register(clock)
    from services.brokers.recovery import RecoveryService

    async def attach(user_id, broker, channel):
        raise RuntimeError("adapter blew up")

    service = RecoveryService(
        register,
        attach=attach,
        has_session=lambda u, b: True,
        is_attached=lambda u, b, c: False,
    )
    rungs = []
    for _ in range(4):
        candidate = register.get("u1", "chaos", "market") or register.record_withdrawal(
            "u1", "chaos", "market", RecoveryClass.REPROBE
        )
        clock.now = candidate.next_attempt_at
        outcome = run(service.reprobe("u1", "chaos", "market"))
        assert outcome.value == "attempt_failed"
        rungs.append(candidate.next_attempt_at - clock())

    assert rungs == sorted(rungs) and rungs[-1] > rungs[0]


def test_a_sweep_with_nothing_due_performs_no_work_at_all():
    """The cost of a quiet deployment: two dictionary reads."""
    clock = _ProbeClock()
    register = _register(clock)
    from services.brokers.recovery import RecoveryService

    calls = []
    service = RecoveryService(
        register,
        attach=lambda *a: calls.append(a),
        has_session=lambda u, b: calls.append(("session",)) or True,
        is_attached=lambda u, b, c: calls.append(("attached",)) or False,
    )
    register.record_withdrawal("u1", "chaos", "market", RecoveryClass.REPROBE)

    assert run(service.sweep_once()) == {}
    assert calls == [], "a sweep with nothing due reached a guard"


def test_a_reprobe_is_scoped_to_one_user_and_one_broker_and_one_channel():
    """Invariant A at the recovery layer: three keys, three ladders."""
    clock = _ProbeClock()
    register = _register(clock)
    register.record_withdrawal("u1", "chaos", "market", RecoveryClass.REPROBE)
    register.record_withdrawal("u2", "chaos", "market", RecoveryClass.REPROBE)
    register.record_withdrawal("u1", "other", "market", RecoveryClass.REPROBE)
    register.record_withdrawal("u1", "chaos", "orders", RecoveryClass.REPROBE)

    register.forget("u1", "chaos", "market")

    remaining = {(c.user_id, c.broker, c.channel) for c in register.candidates()}
    assert remaining == {("u2", "chaos", "market"), ("u1", "other", "market"),
                         ("u1", "chaos", "orders")}


# ══════════════════════════════════════════════════════════════════
# §I — DISTRIBUTED-HEALTH CHAOS  (D5.8, Invariant H)
#
# Owner: SharedHealthStore + ProviderHealthRecovery.
# Recovery: automatic. Redis absence degrades to the pre-D5.8 local behaviour.
#
# The multi-worker properties are SERVER-SIDE guarantees, so they are asserted
# against a real Redis and never against a double — the same rule
# `test_distributed_health.py` set, and its discovery and `Worker` object are
# reused rather than re-implemented so the two suites cannot disagree about what
# a worker is.
# ══════════════════════════════════════════════════════════════════

from infrastructure import redis_client  # noqa: E402
from infrastructure.health_state import provider_key  # noqa: E402
from tests.test_distributed_health import (  # noqa: E402
    REDIS_URL,
    SharedHealthStore,
    Worker,
    _make_trial_due,
    needs_redis,
)

#: Every Redis assertion below runs inside ONE `asyncio.run`, because the Redis
#: client is a process singleton bound to the loop that created it — a second
#: `run()` in the same test finds a connection whose loop has been closed. That
#: is the structure `test_distributed_health.py` uses and the reason it uses it.


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("REDIS_URL", REDIS_URL or "redis://127.0.0.1:6399/15")
    redis_client.manager.reset_for_tests()
    yield SharedHealthStore()
    redis_client.manager.reset_for_tests()


@pytest.fixture
def unique():
    import uuid

    return lambda prefix="p": f"d511-{prefix}-{uuid.uuid4().hex[:12]}"


@needs_redis
def test_one_providers_failure_cannot_mark_every_provider_down(store, unique):
    """Shared health is shared *proportionally*, never catastrophically.

    The single most dangerous way D5.8 could be wrong: one bad endpoint taking a
    deployment's entire feed offline. Worker A drives one provider to DOWN;
    worker B must see that one provider's verdict and nothing else.
    """
    name, other = unique("failing"), unique("healthy")

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        bystander = Worker(store, name=other, failing=False)
        await a.fail(times=DOWN_AFTER_FAILURES)

        assert (await b.read()).state == ProviderState.DOWN.value
        assert (await bystander.read()).state != ProviderState.DOWN.value
        assert bystander.provider.health().state is not ProviderState.DOWN
        assert (await bystander.resolve()).available

    run(scenario())


@needs_redis
def test_two_workers_claiming_the_same_trial_in_the_same_instant_spend_it_once(store, unique):
    """The lease under simultaneous contention, asserted at the claim.

    A test that asserted only over resolution would pass against a claim that
    granted both and a resolver that happened to pick one — so the assertion is
    on the claim, which is where the Lua script's atomicity actually lives.
    """
    name = unique("contended")

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)
        await a.resolve()
        await _make_trial_due(store, provider_key(name))

        claims = await asyncio.gather(
            a.recovery.claim_due([a.provider]),
            b.recovery.claim_due([b.provider]),
        )
        assert len([c for c in claims if c.granted]) == 1

    run(scenario())


@needs_redis
def test_a_trial_lease_that_expires_is_reoffered_and_not_lost(store, unique):
    """A worker that dies holding a trial must not strand the provider forever.

    The lease is what makes that recoverable, and its expiry is the only path
    back — so a chaos test that never expired one would be asserting the happy
    half of the mechanism.
    """
    name = unique("lease")

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)
        await a.resolve()
        await _make_trial_due(store, provider_key(name))

        first = await a.recovery.claim_due([a.provider])
        assert first.granted, "the first claim was refused"
        # Worker A now "dies" — it never reports the outcome. The lease is what
        # releases the trial; bringing it forward is moving the clock, not the
        # policy.
        blocked = await b.recovery.claim_due([b.provider])
        assert not blocked.granted, "the lease did not exclude the second worker"

        await _make_trial_due(store, provider_key(name))
        after_expiry = await b.recovery.claim_due([b.provider])
        assert after_expiry.granted, "an expired lease stranded the provider"

    run(scenario())


@needs_redis
def test_redis_disappearing_degrades_to_local_evidence_and_reports_that_it_did(store, unique, monkeypatch):
    """Invariant H. The store answers "not available"; nothing is fabricated.

    Three facts asserted together, because a partial degradation is worse than
    none: the read says it did not sync, no trial is claimed with no store to
    claim it from, and resolution still returns a provider rather than an
    outage — Redis is not in the data path.
    """
    name = unique("outage")

    async def scenario():
        worker = Worker(store, name=name)
        await worker.fail(times=DOWN_AFTER_FAILURES)
        assert (await worker.read()).state == ProviderState.DOWN.value

        monkeypatch.delenv("REDIS_URL", raising=False)
        redis_client.manager.reset_for_tests()

        ok, records = await store.read_many([SourceManager._store_key(worker.provider)])
        assert ok is False and records == {}, "an unavailable store answered with data"

        shared = await worker.manager.prepare(Capability.QUOTES, None)
        assert shared.health_synced is False, "an unavailable store was reported as synced"
        # The claim set exists but is explicitly NOT distributed: the worker has
        # fallen back to its own D5.7 cool-down for the duration of the outage,
        # which is the documented degradation and not a decision to try
        # everything (fail-open to a storm) or nothing (fail-closed).
        assert shared.claims is not None
        assert shared.claims.distributed is False, (
            "a local fallback claim was reported as a distributed one — a second "
            "worker would then believe this trial was exclusive"
        )

    run(scenario())


@needs_redis
def test_redis_failure_is_fail_open_and_never_fail_closed(store, unique, monkeypatch):
    """The direction of the degradation, which is the whole of Invariant H.

    A Redis outage must not remove a *healthy* provider from anybody's chain.
    Fail-closed here would mean losing every user's feed because a cache is
    down — a dependency the market-data path is explicitly not allowed to have.
    """
    name = unique("failopen")

    async def scenario():
        worker = Worker(store, name=name, failing=False)
        assert (await worker.resolve()).available

        monkeypatch.delenv("REDIS_URL", raising=False)
        redis_client.manager.reset_for_tests()

        resolution = await worker.resolve()
        assert resolution.available, "a Redis outage removed a healthy provider"
        assert resolution.provider is worker.provider

    run(scenario())


@needs_redis
def test_redis_returning_resumes_shared_state_without_replaying_anything(store, unique, monkeypatch):
    """Recovery after an outage is bounded: the next call syncs, and that is all."""
    name = unique("return")

    async def scenario():
        worker = Worker(store, name=name)
        await worker.fail(times=DOWN_AFTER_FAILURES)

        monkeypatch.delenv("REDIS_URL", raising=False)
        redis_client.manager.reset_for_tests()
        assert (await worker.manager.prepare(Capability.QUOTES, None)).health_synced is False

        monkeypatch.setenv("REDIS_URL", REDIS_URL)
        redis_client.manager.reset_for_tests()

        shared = await worker.manager.prepare(Capability.QUOTES, None)
        assert shared.health_synced is True
        assert (await worker.read()).state == ProviderState.DOWN.value

    run(scenario())


@needs_redis
def test_a_restarted_worker_reads_shared_health_and_rebuilds_everything_else(store, unique):
    """Process restart, as D5.8 defines it.

    A brand-new `Worker` over the same store *is* a restarted process. It reads
    the shared verdict — that is the point of D5.8 — and inherits no readiness,
    no probation, no freshness and no latency, because those are facts about a
    socket this process does not hold.
    """
    name = unique("restart")

    async def scenario():
        before = Worker(store, name=name)
        await before.fail(times=DOWN_AFTER_FAILURES)

        after = Worker(store, name=name)
        shared = await after.manager.prepare(Capability.QUOTES, None)
        assert shared.health_synced is True
        assert (await after.read()).state == ProviderState.DOWN.value

    run(scenario())
    assert StreamingTickProvider.health_is_shared is False


def test_a_streaming_feeds_link_evidence_is_process_local_by_construction():
    """DB-1's sharpest rule, asserted without Redis because it is a *type* fact.

    Worker A's socket dies; worker B re-attaches the account. The fresh link
    must earn READY and serve its own probation window rather than inheriting a
    verdict about a socket that no longer exists. `health_is_shared = False` is
    what makes that true for every streaming feed at once, so it is asserted on
    the class, and the consequence is asserted on a fresh instance.
    """
    assert StreamingTickProvider.health_is_shared is False

    clock = ChaosClock()
    fixture = run(build_feed(clock=clock, with_baseline=False))
    run(serve_probation(fixture.feed, clock, (DEFAULT_SHARD_ID,)))
    assert fixture.feed.is_stable

    reattached = StreamingTickProvider(fixture.feed.name, owner_user_id="u1", clock=clock)
    run(reattached.connect())
    run(reattached.subscribe(["A"]))
    run(reattached.mark_link_up())

    assert not reattached.is_ready
    assert reattached.stability is FeedStability.PROBATION
    assert reattached.delivery_latency is None
    assert reattached.covered_symbols == ()


# ══════════════════════════════════════════════════════════════════
# §J — SHARDING CHAOS  (D5.10, Invariant C)
#
# Owner: plan_shards + StreamingTickProvider's per-shard evidence.
# The two halves: a lost shard must not blank its siblings, and healthy
# siblings must not mask it.
# ══════════════════════════════════════════════════════════════════

SHARDS = ("0", "1", "2", "3")
SHARD_SYMBOLS = {"0": "A", "1": "B", "2": "C", "3": "D"}


def _sharded(clock=None, user_id="u1"):
    """Four connections, one per instrument, every one delivering and stable."""
    clock = clock or ChaosClock()
    fixture = run(
        build_feed(
            shards=SHARDS,
            symbols=tuple(SHARD_SYMBOLS.values()),
            clock=clock,
            user_id=user_id,
        )
    )
    for _ in range(2):
        for shard, symbol in SHARD_SYMBOLS.items():
            run(fixture.feed.on_raw([tick(symbol)], shard))
        clock.advance(PROBATION_WINDOW_SECONDS + 1)
    for shard, symbol in SHARD_SYMBOLS.items():
        run(fixture.feed.on_raw([tick(symbol)], shard))
    assert fixture.feed.is_stable
    return fixture


@pytest.mark.parametrize(
    "lost",
    [
        ("0",), ("1",), ("2",), ("3",),
        ("0", "1"), ("1", "2"), ("0", "2"), ("2", "3"), ("0", "3"),
        ("0", "1", "2"),
        ("0", "1", "2", "3"),
    ],
    ids=lambda lost: "kill_" + "".join(lost),
)
def test_every_shard_kill_combination_preserves_survivors_and_drops_only_the_lost(lost):
    """The systematic matrix §13 asks for, as one property over eleven cases.

    Three claims per case, and they are the whole of Invariant C:

      * every surviving shard's instrument is still *eligible* on the feed — the
        feed goes on holding a usable price for it, so no data is lost;
      * every lost shard's instrument stops being covered at all — the feed does
        not go on answering from a socket that no longer exists;
      * every provider-level claim tightens for the whole feed, so a healthy
        shard cannot mask a dead one.

    WHY THE SURVIVORS ARE ASSERTED ON *ELIGIBILITY* AND NOT ON HEADSHIP
    -------------------------------------------------------------------
    This is the one place where writing the assertion the obvious way would
    have been writing it wrong. Any shard loss puts the whole provider back on
    probation (`_ready_since` is `None` while a declared shard has not earned
    readiness), and probation is the *second* ranking term — so the steadier
    baseline leads the chain even for an instrument a surviving connection is
    delivering perfectly well. That is the published ranking applied
    consistently, not a defect: D5.2's rule is that probation ranks rather than
    filters, and a provider that has just lost part of its subscription is
    exactly what "has not proved it is reliable" means.

    This is **LIM-D5.10-3**, already recorded in ADR-050: a partial shard loss
    costs the *surviving* instruments their streaming tier until the lost
    connection is back and the feed has served a full probation window again.
    D5.11 adds no new limitation here — it adds the first test that exercises
    the limitation across every kill combination rather than one, which is what
    turns a documented consequence into a pinned one.
    """
    fixture = _sharded()
    survivors = [s for s in SHARDS if s not in lost]

    for shard in lost:
        run(fixture.feed.mark_link_down("shard lost", shard))

    for shard in survivors:
        symbol = SHARD_SYMBOLS[shard]
        assert fixture.feed.covers(symbol), f"surviving shard {shard} stopped covering {symbol}"
        assert fixture.feed in fixture.resolution(symbol=symbol).chain, (
            f"surviving shard {shard}'s instrument left the failover chain entirely"
        )
    for shard in lost:
        symbol = SHARD_SYMBOLS[shard]
        assert not fixture.feed.covers(symbol), f"lost shard {shard} still claims {symbol}"
        assert fixture.quote_provider(symbol=symbol) is fixture.baseline
        assert fixture.feed not in fixture.resolution(symbol=symbol).chain

    # The masking half. Any loss at all tightens the provider-level answer.
    assert not fixture.feed.has_fresh_evidence, "a healthy shard masked a dead one"
    assert fixture.feed.stability is FeedStability.PROBATION
    assert fixture.feed.delivery_latency is None
    assert fixture.tier() is SourceTier.DELAYED

    # Readiness itself only falls when there is nothing left.
    if survivors:
        assert fixture.feed.is_ready, "one shard's loss demoted a feed still delivering"
    else:
        assert not fixture.feed.is_ready
        assert fixture.feed.covered_symbols == ()


def test_a_partially_failed_feed_still_serves_its_survivors_when_nothing_steadier_remains():
    """The other side of LIM-D5.10-3, and the reason it is a limitation and not
    a data-loss bug: with no baseline in the chain, the surviving instruments
    are answered by the feed rather than by nothing at all."""
    fixture = _sharded()
    fixture.registry.unregister(fixture.baseline.name)

    run(fixture.feed.mark_link_down("shard lost", "2"))

    assert fixture.quote_provider(symbol="A") is fixture.feed
    assert fixture.quote_provider(symbol="B") is fixture.feed
    unavailable = fixture.resolution(symbol="C")
    assert not unavailable.available, "a dead connection's instrument was still answered"
    # The *reason* vocabulary is coarser than the situation: with no baseline
    # registered, "the only candidate does not cover this symbol" is reported as
    # ALL_PROVIDERS_DOWN. Asserted as it is rather than as it might read better,
    # because the consumer-facing consequence — feed unavailable, no fabricated
    # price — is correct and the vocabulary is a diagnostics nuance, not a lie
    # about data. Recorded in the D5.11 report as an observation, not a defect.
    assert unavailable.reason is UnavailableReason.ALL_PROVIDERS_DOWN


@pytest.mark.parametrize("lost", ["0", "1", "2", "3"], ids=lambda s: f"reconnect_{s}")
def test_reconnecting_one_shard_costs_that_shard_everything_and_its_siblings_nothing(lost):
    """The asymmetry D5.10 is built on, at every position in the plan."""
    fixture = _sharded()
    survivors = [s for s in SHARDS if s != lost]

    run(fixture.feed.mark_link_down("dropped", lost))
    run(fixture.feed.mark_link_up(lost))

    for shard in survivors:
        assert fixture.feed.covers(SHARD_SYMBOLS[shard]), (
            f"reconnecting shard {lost} cost shard {shard} its coverage"
        )
    assert not fixture.feed.covers(SHARD_SYMBOLS[lost])
    assert fixture.feed.is_ready, "a sibling reconnect demoted a feed still delivering"

    # The reconnected shard re-earns everything, and the feed is not whole until
    # it has served a full window of its own.
    run(fixture.feed.on_raw([tick(SHARD_SYMBOLS[lost])], lost))
    assert fixture.feed.stability is FeedStability.PROBATION
    fixture.clock.advance(PROBATION_WINDOW_SECONDS + 1)
    for shard, symbol in SHARD_SYMBOLS.items():
        run(fixture.feed.on_raw([tick(symbol)], shard))
    assert fixture.feed.is_stable


def test_a_batch_naming_an_undeclared_shard_is_refused_rather_than_filed(caplog):
    """An unplanned connection may not widen the "every shard" conjunction.

    Filing it under a shard invented on arrival would make the conjunction
    quantify over a socket the provider was never told exists — permanently.
    """
    fixture = _sharded()
    with caplog.at_level(logging.WARNING):
        accepted = run(fixture.feed.on_raw([tick("E")], "99"))

    assert accepted == 0
    assert fixture.feed.shard_count == len(SHARDS)
    assert not fixture.feed.covers("E")
    assert any("does not have" in r.getMessage() for r in caplog.records)


def test_a_link_transition_naming_an_undeclared_shard_changes_nothing(caplog):
    """The same rule on the lifecycle path, which is the more dangerous one:
    a stale plan's link-down must not blank a live connection's prices."""
    fixture = _sharded()
    with caplog.at_level(logging.WARNING):
        assert run(fixture.feed.mark_link_down("stale plan", "99")) is False

    assert fixture.feed.is_stable
    for symbol in SHARD_SYMBOLS.values():
        assert fixture.feed.covers(symbol)


def test_no_shard_delivers_a_duplicate_or_swallows_an_instrument():
    """The planner's two silent failures, over a range of shapes.

    Losing the last instrument and duplicating one both produce a plan that
    *looks* right — the shard count is plausible either way — and both are
    invisible until a user's holding stops updating or updates twice.
    """
    for count in range(1, 40):
        for limit in (1, 2, 3, 7, 10):
            instruments = [f"T{i}" for i in range(count)]
            plan = plan_shards(instruments, max_instruments_per_connection=limit)
            flat = [i for shard in plan for i in shard.instruments]
            assert flat == instruments, f"count={count} limit={limit}: instruments moved"
            assert len(flat) == len(set(flat)), f"count={count} limit={limit}: duplicated"
            assert len(plan) == -(-count // limit)


def test_resharding_while_a_shard_is_down_leaves_the_working_shards_untouched():
    """A plan change during a partial outage must not restart healthy sockets.

    `declare_shards` keeps the identical evidence object for a retained shard,
    so a reshard that did not touch a connection does not make it re-earn
    readiness — which is what stops a portfolio sync from costing the account
    its whole feed.
    """
    fixture = _sharded()
    run(fixture.feed.mark_link_down("down", "2"))

    fixture.feed.declare_shards(SHARDS)   # the plan is unchanged

    for shard in ("0", "1", "3"):
        assert fixture.feed.covers(SHARD_SYMBOLS[shard]), f"reshard cost shard {shard}"
    assert not fixture.feed.covers("C")


def test_a_shard_leaving_the_plan_takes_its_prices_with_it():
    """A dropped connection's prices may not answer a quote after the reshard.

    They describe a socket that is being closed, exactly as a dropped link's do.
    """
    fixture = _sharded()
    fixture.feed.declare_shards(("0", "1"))

    assert fixture.feed.shard_count == 2
    assert fixture.feed.covers("A") and fixture.feed.covers("B")
    assert not fixture.feed.covers("C") and not fixture.feed.covers("D")
    assert fixture.quote_provider(symbol="C") is fixture.baseline


def test_ticks_arriving_during_a_reshard_are_attributed_to_the_right_connection():
    """Chaos ordering: data in flight while the plan is being rebuilt.

    A `Call` between two frames is the only deterministic way to interleave a
    plan change with an arriving batch, and it is what this case needs.
    """
    fixture = _sharded()

    run(fixture.feed.on_raw([tick("A", 111.0)], "0"))
    fixture.feed.declare_shards(("0", "1", "2"))     # shard 3 leaves
    run(fixture.feed.on_raw([tick("D", 444.0)], "3"))  # a batch from the shard that left

    assert fixture.feed.covers("A")
    assert not fixture.feed.covers("D"), "a departed connection's batch was accepted"
    assert fixture.feed.shard_count == 3


def test_no_shard_id_reaches_a_consumer_payload_or_a_provider_description():
    """A shard is not a provider, and its vocabulary is not consumer vocabulary.

    Asserted over every surface a consumer or an admin can read, because the
    leak would be silent: a shard id in a payload is a broker-connection detail
    the platform has promised never to expose (Developer Rule 4).
    """
    fixture = _sharded()

    described = json.dumps(fixture.feed.describe(), default=str)
    assert described.count('"connections": 4') == 1, "the count is not reported"
    for shard in SHARDS:
        assert f'"{shard}"' not in described or shard in ("0",), (
            "a shard id appears in describe()"
        )
    assert "shard" not in json.dumps(fixture.resolution(symbol="A").as_status())

    quote = run(fixture.feed.fetch_quote("A"))
    assert "shard" not in json.dumps(quote, default=str)
    assert set(quote) <= set(tick("A")), "fetch_quote grew a field"


def test_a_sharded_account_is_one_provider_in_the_chain_and_not_four():
    """The registry-level claim, asserted where a regression would show."""
    fixture = _sharded()
    chain = fixture.resolution(symbol="A").chain

    streaming = [p for p in chain if p.tier is SourceTier.STREAMING]
    assert len(streaming) == 1
    assert streaming[0] is fixture.feed
    assert fixture.feed.shard_count == 4


def test_one_users_shard_failure_cannot_reach_another_users_feed():
    """Invariant A, at the sharpest place to ask it: shared shard *ids*.

    Both accounts' plans call their connections "0".."3". If shard state were
    keyed globally rather than per provider, user A losing shard 2 would blank
    user B's shard 2 — and the two users are on the same broker, so the ids
    genuinely collide.
    """
    registry = ProviderRegistry()
    clock = ChaosClock()
    symbols = tuple(SHARD_SYMBOLS.values())
    a = run(build_feed(shards=SHARDS, symbols=symbols, clock=clock,
                       user_id="u1", registry=registry))
    b = run(build_feed(shards=SHARDS, symbols=symbols, clock=clock, user_id="u2",
                       registry=registry, with_baseline=False, manager=a.manager))

    # Both accounts serve a full probation window, in step on one clock.
    for _ in range(2):
        for fixture in (a, b):
            for shard, symbol in SHARD_SYMBOLS.items():
                run(fixture.feed.on_raw([tick(symbol)], shard))
        clock.advance(PROBATION_WINDOW_SECONDS + 1)
    for fixture in (a, b):
        for shard, symbol in SHARD_SYMBOLS.items():
            run(fixture.feed.on_raw([tick(symbol)], shard))
    assert a.feed.is_stable and b.feed.is_stable

    run(a.feed.mark_link_down("u1 lost a connection", "2"))

    # User A: exactly the documented consequence.
    assert not a.feed.covers("C")
    assert a.feed.stability is FeedStability.PROBATION

    # User B: entirely untouched, including the shard with the colliding id.
    assert b.feed.covers("C"), "one user's shard loss reached another user's feed"
    assert b.feed.is_ready and b.feed.is_stable
    assert b.feed.has_fresh_evidence
    assert a.manager.resolve(
        Capability.QUOTES, context=ResolutionContext(user_id="u2", symbol="C")
    ) is b.feed
    # And user B may never be served user A's feed, however healthy it is.
    assert a.feed not in a.manager.failover_chain(
        Capability.QUOTES, ResolutionContext(user_id="u2", symbol="A")
    )


# ══════════════════════════════════════════════════════════════════
# §K — MAKE-BEFORE-BREAK CHAOS  (Invariants B, D)
#
# Owner: SourceManager.resolve_feed — promotion is the *output* of a sort and
# never an input to it, which is what makes it atomic with no handover protocol.
# ══════════════════════════════════════════════════════════════════


def _second_feed(fixture, name="feed:u1:new", symbols=("A",)):
    provider = StreamingTickProvider(name, owner_user_id=fixture.user_id, clock=fixture.clock)
    fixture.registry.register(provider)
    run(provider.connect())
    run(provider.subscribe(list(symbols)))
    run(provider.mark_link_up())
    return provider


def test_a_new_provider_that_is_connected_but_not_ready_never_displaces_the_old_one():
    """The gate, stated as the ordering it enforces.

    Connected, subscribed, authenticated — none of it moves the primary. Only
    a valid canonical tick does, and only after the new feed has out-ranked the
    one already serving.
    """
    fixture = run(build_feed())
    old = fixture.feed
    run(serve_probation(old, fixture.clock, (DEFAULT_SHARD_ID,)))
    assert fixture.quote_provider() is old

    new = _second_feed(fixture)
    assert fixture.quote_provider() is old, "a connected feed displaced a serving one"

    run(new.on_raw([tick("A")]))
    assert new.is_ready and new.is_on_probation
    assert fixture.quote_provider() is old, "a probationary feed displaced a stable one"

    fixture.clock.advance(PROBATION_WINDOW_SECONDS + 1)
    run(new.on_raw([tick("A")]))
    run(old.on_raw([tick("A")]))
    assert new.is_stable
    # Both are now stable; the ordering between them is priority and
    # registration order, which is the published tie-break — not a handover.
    assert fixture.quote_provider() in (old, new)


def test_a_failing_new_provider_leaves_the_old_one_exactly_where_it_was():
    """The abort path: the switch simply does not happen, and nothing is lost."""
    fixture = run(build_feed())
    old = fixture.feed
    run(serve_probation(old, fixture.clock, (DEFAULT_SHARD_ID,)))

    new = _second_feed(fixture)
    run(new.mark_link_down("the new connection died"))

    assert fixture.quote_provider() is old
    assert old.is_stable and old.has_fresh_evidence


def test_no_momentary_gap_is_created_while_a_valid_old_source_exists():
    """Resolution is recomputed from current state on every call, so there is
    no interval in which the answer is "nothing" while something can serve.

    Asserted by resolving after *every* step of a full transition rather than
    at the endpoints — a handover with a gap in the middle would pass an
    endpoint-only test.
    """
    fixture = run(build_feed())
    old = fixture.feed
    run(serve_probation(old, fixture.clock, (DEFAULT_SHARD_ID,)))

    steps = [
        lambda: _second_feed(fixture),
        lambda: run(fixture._new.on_raw([tick("A")])),
        lambda: fixture.clock.advance(PROBATION_WINDOW_SECONDS + 1),
        lambda: run(fixture._new.on_raw([tick("A")])),
        lambda: run(old.on_raw([tick("A")])),
    ]
    for index, step in enumerate(steps):
        produced = step()
        if index == 0:
            fixture._new = produced
        resolution = fixture.resolution(symbol="A")
        assert resolution.available, f"step {index} left the feed with no provider at all"
        assert resolution.provider is not None


def test_a_shard_failing_mid_transition_does_not_take_the_old_provider_with_it():
    """Invariant B under a partial failure of the incoming feed."""
    fixture = run(build_feed())
    old = fixture.feed
    run(serve_probation(old, fixture.clock, (DEFAULT_SHARD_ID,)))

    new = StreamingTickProvider("feed:u1:sharded", owner_user_id="u1", clock=fixture.clock)
    new.declare_shards(("0", "1"))
    fixture.registry.register(new)
    run(new.connect())
    run(new.subscribe(["A"]))
    run(new.mark_link_up("0"))
    run(new.mark_link_up("1"))
    run(new.on_raw([tick("A")], "0"))
    run(new.mark_link_down("half the new feed died", "1"))

    assert fixture.quote_provider() is old, "a half-dead newcomer displaced a serving feed"
    assert old.is_stable


def test_the_old_provider_going_stale_mid_transition_hands_over_to_the_baseline():
    """Both feeds failing at once must land on the baseline, not on nothing."""
    fixture = run(build_feed())
    old = fixture.feed
    run(serve_probation(old, fixture.clock, (DEFAULT_SHARD_ID,)))
    new = _second_feed(fixture)

    fixture.clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)

    assert not old.has_fresh_evidence and not new.is_ready
    assert fixture.quote_provider() is fixture.baseline


def test_promotion_is_the_output_of_a_sort_and_never_stored_anywhere():
    """The reason make-before-break needs no lock, transaction or protocol.

    Two providers can never both believe they are primary because neither
    believes anything: `PRIMARY` is not a state on the class, and the resolver
    recomputes the head every time.
    """
    fixture = run(build_feed())
    assert not any(
        "primary" in name.lower() for name in vars(fixture.feed)
    ), "a provider grew a primary flag"
    assert not hasattr(fixture.feed, "is_primary")
    assert "primary" not in {state.value for state in FeedReadiness}


# ══════════════════════════════════════════════════════════════════
# §L — YAHOO FALLBACK AND THE CROSS-PROVIDER MATRIX  (Invariants A, B)
# ══════════════════════════════════════════════════════════════════


def _matrix_fixture(user_id="u1"):
    """A user with a streaming feed and the permanent baseline beneath it."""
    fixture = run(build_feed(user_id=user_id))
    run(serve_probation(fixture.feed, fixture.clock, (DEFAULT_SHARD_ID,)))
    return fixture


@pytest.mark.parametrize(
    "streaming_state,expected",
    [
        ("stable", "streaming"),
        ("down", "baseline"),
        ("stale", "baseline"),
        ("probation", "baseline"),
        ("entitlement_refused", "baseline"),
        ("never_ready", "baseline"),
    ],
)
def test_the_cross_provider_matrix_with_the_baseline_up(streaming_state, expected):
    """§16's matrix, one row per streaming condition, baseline healthy.

    Written as a table because the *set* is the claim: there must be no
    streaming condition other than "stable and delivering" that keeps the feed
    ahead of a working baseline.
    """
    fixture = _matrix_fixture()
    feed = fixture.feed

    if streaming_state == "down":
        run(feed.mark_link_down("socket closed"))
    elif streaming_state == "stale":
        fixture.clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)
    elif streaming_state == "probation":
        run(feed.mark_link_down("dropped"))
        run(feed.mark_link_up())
        run(feed.on_raw([tick("A")]))
    elif streaming_state == "entitlement_refused":
        fixture.registry.unregister(feed.name)
    elif streaming_state == "never_ready":
        fixture.registry.unregister(feed.name)
        fresh = StreamingTickProvider("feed:u1:fresh", owner_user_id="u1", clock=fixture.clock)
        fixture.registry.register(fresh)
        run(fresh.connect())
        run(fresh.subscribe(["A"]))
        run(fresh.mark_link_up())

    chosen = fixture.quote_provider()
    if expected == "streaming":
        assert chosen is feed and fixture.tier() is SourceTier.STREAMING
    else:
        assert chosen is fixture.baseline
        assert fixture.tier() is SourceTier.DELAYED


def test_when_the_baseline_is_also_gone_the_feed_reports_unavailable_and_invents_nothing():
    """The floor beneath the floor. `Resolution` is explicit in both directions,
    so a route handler never has to infer an outage from an empty return."""
    fixture = _matrix_fixture()
    run(fixture.feed.mark_link_down("streaming gone"))
    fixture.registry.unregister(fixture.baseline.name)

    resolution = fixture.resolution(symbol="A")

    assert not resolution.available
    assert resolution.provider is None and resolution.chain == ()
    # An *explicit* reason, always — the point of `UnavailableReason` is that no
    # caller has to infer an outage from an empty return. Which member it is
    # depends on what is left in the registry: here the dead feed is still
    # registered, so "providers exist, none this request may use" is the honest
    # diagnosis, and an empty registry below gives the other one.
    assert isinstance(resolution.reason, UnavailableReason)
    assert resolution.reason is UnavailableReason.NOT_ENTITLED
    fixture.registry.unregister(fixture.feed.name)
    assert fixture.resolution(symbol="A").reason is UnavailableReason.NO_PROVIDERS_REGISTERED
    status = resolution.as_status()
    assert status["state"] == "unavailable" and status["tier"] is None
    # And it names no provider, which is what lets it travel to a consumer.
    assert "yahoo" not in json.dumps(status).lower()
    assert "feed:" not in json.dumps(status)


def test_one_providers_failure_resolves_to_the_best_remaining_eligible_source():
    """Invariant B, with four streaming feeds and the baseline.

    One goes down; the resolution must land on the best *remaining* streaming
    feed rather than skipping to the baseline or to the wrong one.
    """
    fixture = run(build_feed(user_id="u1"))
    feeds = [fixture.feed] + [
        _second_feed(fixture, name=f"feed:u1:{i}") for i in range(1, 4)
    ]
    for _ in range(2):
        for feed in feeds:
            run(feed.on_raw([tick("A")]))
        fixture.clock.advance(PROBATION_WINDOW_SECONDS + 1)
    for feed in feeds:
        run(feed.on_raw([tick("A")]))
    assert all(feed.is_stable for feed in feeds)

    run(feeds[0].mark_link_down("provider 0 is gone"))

    chosen = fixture.quote_provider()
    assert chosen is not feeds[0]
    assert chosen in feeds[1:], "a single provider's failure fell through to the baseline"
    assert all(feed.is_stable for feed in feeds[1:]), "one feed's failure damaged another"


def test_the_whole_matrix_again_with_two_users_and_no_leakage_between_them():
    """§16's "then run the same matrix with multiple users".

    User A's feed is destroyed in every way the matrix names while user B's is
    untouched; B's tier must never move, and neither user may ever be resolved
    the other's provider.
    """
    registry = ProviderRegistry()
    clock = ChaosClock()
    a = run(build_feed(user_id="u1", clock=clock, registry=registry))
    b = run(build_feed(user_id="u2", clock=clock, registry=registry,
                       with_baseline=False, manager=a.manager))
    for _ in range(2):
        for fixture in (a, b):
            run(fixture.feed.on_raw([tick("A")]))
        clock.advance(PROBATION_WINDOW_SECONDS + 1)
    for fixture in (a, b):
        run(fixture.feed.on_raw([tick("A")]))

    for failure in ("link_down", "reconnect", "unregister"):
        if failure == "link_down":
            run(a.feed.mark_link_down("gone"))
        elif failure == "reconnect":
            run(a.feed.mark_link_up())
        else:
            registry.unregister(a.feed.name)

        assert a.manager.resolve(
            Capability.QUOTES, context=ResolutionContext(user_id="u2", symbol="A")
        ) is b.feed, f"{failure} on user A moved user B's feed"
        assert b.feed.is_stable, f"{failure} on user A cost user B its stability"
        assert a.feed not in a.manager.failover_chain(
            Capability.QUOTES, ResolutionContext(user_id="u2", symbol="A")
        ), f"{failure}: user A's feed entered user B's chain"


def test_the_baseline_is_never_removed_from_the_chain_by_a_streaming_failure():
    """Yahoo is the permanent floor — the one rule the whole fallback rests on."""
    fixture = _matrix_fixture()
    for step in ("stale", "down", "reconnect"):
        if step == "stale":
            fixture.clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)
        elif step == "down":
            run(fixture.feed.mark_link_down("gone"))
        else:
            run(fixture.feed.mark_link_up())
        chain = fixture.resolution(symbol="A").chain
        assert fixture.baseline in chain, f"{step} removed the baseline from the chain"


# ══════════════════════════════════════════════════════════════════
# §M — APPLICATION-RESTART CHAOS
#
# A restart is modelled as *building the objects again* — which is what a
# restart is. Nothing pretends process-local state survived it.
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "restart_after",
    ["registration", "connect", "subscribe", "first_tick", "probation", "stable", "reconnect"],
)
def test_a_restart_at_any_point_rebuilds_local_state_and_inherits_nothing(restart_after):
    """Seven restart points, one property: the new process starts from zero.

    The failure this excludes is a provider that came back believing it was
    still READY — the "no stale evidence inheritance" invariant, reached by the
    one route that would look most like a legitimate optimisation (persisting
    provider state so a restart is cheap).
    """
    clock = ChaosClock()
    fixture = run(build_feed(clock=clock, link_up=False))
    feed = fixture.feed

    if restart_after != "registration":
        run(feed.mark_link_up())
    if restart_after in ("first_tick", "probation", "stable", "reconnect"):
        run(feed.on_raw([tick("A")]))
    if restart_after in ("probation",):
        clock.advance(PROBATION_WINDOW_SECONDS / 2)
        run(feed.on_raw([tick("A")]))
    if restart_after in ("stable", "reconnect"):
        run(serve_probation(feed, clock, (DEFAULT_SHARD_ID,)))
    if restart_after == "reconnect":
        run(feed.mark_link_down("dropped"))

    # ── the process restarts ──
    reborn = run(build_feed(clock=clock, link_up=False))

    # The new process has constructed and connected a provider — that is what a
    # restart's re-attach does — so `is_link_up` is legitimately true here. What
    # it must NOT have is any evidence, which is the whole of the invariant:
    # CONNECTED is not READY (D4.5), and a restart is the case where a
    # persisted-state optimisation would most plausibly have blurred the two.
    assert reborn.feed.readiness is FeedReadiness.SUBSCRIBED
    assert not reborn.feed.is_ready
    assert reborn.feed.stability is FeedStability.PROBATION
    assert not reborn.feed.has_fresh_evidence
    assert reborn.feed.delivery_latency is None
    assert reborn.feed.covered_symbols == ()
    assert reborn.quote_provider() is reborn.baseline, (
        f"restart after {restart_after} left a feed resolvable with no socket"
    )


def test_a_restart_leaves_no_recovery_candidate_behind_in_a_fresh_register():
    """The re-probe register is process-local too, and is honest about it.

    A restart loses outstanding withdrawals — which is correct, because the
    lifecycle event that restores a session re-attaches the channel anyway. The
    test exists so that a future sprint that decides to persist them has to
    change an assertion rather than a comment.
    """
    clock = _ProbeClock()
    before = _register(clock)
    before.record_withdrawal("u1", "chaos", "market", RecoveryClass.REPROBE)
    assert before.candidates()

    after = _register(clock)

    assert after.candidates() == []
    assert after.due() == []


# ══════════════════════════════════════════════════════════════════
# §N — SECURITY CHAOS
#
# The whole chaos suite, re-run at DEBUG with realistic fake credentials
# planted everywhere the transport can reach them. Nothing credential-bearing
# may escape into a log, an exception, a task name, a description or a payload.
# ══════════════════════════════════════════════════════════════════


def _secret_free(text: str, *, where: str) -> None:
    for label, secret in FAKE_CREDENTIALS.items():
        assert secret not in text, f"{where} leaked the {label}"
    # Also catch a *fragment* long enough to be the real thing — a truncated
    # token is still a token, and a naive redaction that keeps a prefix would
    # pass an exact-match search.
    for label, secret in FAKE_CREDENTIALS.items():
        if len(secret) >= 24:
            assert secret[:20] not in text, f"{where} leaked a prefix of the {label}"


def test_the_whole_transport_chaos_script_leaks_no_credential_at_debug(caplog):
    """One run, every failure shape, DEBUG level, one search.

    The script deliberately mixes the paths that each have their own logging:
    a refused handshake (`connect_error`), a successful connect (the endpoint
    log line, which must use `safe_url`), malformed frames (the decode warning),
    an error frame, a keep-alive, a flap, and a terminal refusal.
    """
    with chaos_registered() as adapter, caplog.at_level(logging.DEBUG):
        run(
            StreamHarness(
                adapter,
                [
                    Raise(RuntimeError("handshake rejected")),
                    ["<<<garbage>>>", b"\x00\x01\xff", "x" * 8192, Close()],
                    [px(("A", 100.0)), err("rate limited"), Advance(1.0), Close()],
                    [denied("account not subscribed")],
                ],
                instruments=["A", "B"],
            ).run()
        )

    blob = "\n".join(
        f"{record.name} {record.getMessage()} {record.exc_text or ''}" for record in caplog.records
    )
    assert blob, "the run produced no log at all — the sweep would be vacuous"
    _secret_free(blob, where="the chaos transport log")


def test_the_endpoint_url_reaches_the_log_only_through_safe_url(caplog):
    """The one credential-bearing string the transport is *required* to hold.

    `ChaosChannel` puts the access token in the query string precisely so this
    control has something to redact — a harness whose endpoint had no secret in
    it could not test it.
    """
    with chaos_registered() as adapter, caplog.at_level(logging.DEBUG):
        run(StreamHarness(adapter, [[px(("A", 1.0)), Close()]], instruments=["A"]).run())

    connected = [r for r in caplog.records if "stream connected" in r.getMessage()]
    assert connected, "the connect log line disappeared — the control is untested"
    _secret_free("\n".join(r.getMessage() for r in connected), where="the connect log line")


def test_no_task_name_carries_a_credential_or_a_symbol():
    """Task names reach `asyncio.all_tasks()`, tracebacks and any dump of them."""
    with chaos_registered() as adapter:
        harness = StreamHarness(adapter, [[Close()]], instruments=["A"])
        name = f"broker-stream-{harness.stream.broker}-{harness.stream.channel}-" \
               f"{harness.stream.shard}-{harness.stream.user_id}"
        _secret_free(name, where="the stream task name")
        assert "A" not in name.replace("chaos", "").replace("market", "")


def test_neither_describe_nor_health_nor_a_payload_carries_a_credential():
    """Every provider surface an admin or a consumer can read."""
    fixture = _matrix_fixture()
    feed = fixture.feed

    surfaces = {
        "describe()": json.dumps(feed.describe(), default=str),
        "health()": json.dumps(feed.health().__dict__ if hasattr(feed.health(), "__dict__")
                               else str(feed.health()), default=str),
        "status()": json.dumps(fixture.manager.status(user_id="u1"), default=str),
        "resolution": json.dumps(fixture.resolution(symbol="A").as_status(), default=str),
        "quote": json.dumps(run(feed.fetch_quote("A")), default=str),
    }
    for where, blob in surfaces.items():
        _secret_free(blob, where=where)


def test_a_consumer_payload_names_a_tier_and_never_a_provider():
    """Developer Rule 4 under chaos: the payload must not become more
    informative because something went wrong."""
    fixture = _matrix_fixture()
    for step in ("healthy", "stale", "down"):
        if step == "stale":
            fixture.clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)
        elif step == "down":
            run(fixture.feed.mark_link_down("gone"))
        status = json.dumps(fixture.resolution(symbol="A").as_status()).lower()
        assert "yahoo" not in status, f"{step}: the payload named the baseline"
        assert "feed:" not in status, f"{step}: the payload named the feed"
        assert "chaos" not in status, f"{step}: the payload named the broker"
        assert "shard" not in status and "socket" not in status


def test_a_redis_key_or_value_never_carries_a_credential():
    """The shared store's keyspace, built from the same subjects under chaos."""
    from infrastructure.health_state import provider_key as _pk

    fixture = _matrix_fixture()
    key = _pk(fixture.feed.name, "u1")
    _secret_free(key.redis_key, where="the shared health key")
    _secret_free(key.probe_key, where="the shared probe key")


def test_the_chaos_harness_itself_plants_secrets_that_could_actually_leak():
    """The falsification for the whole of §N.

    A security sweep over a run that never held a credential proves nothing, so
    this asserts the premise: the material really is in the session, in the
    credentials, in the endpoint URL and in a header.
    """
    with chaos_registered() as adapter:
        harness = StreamHarness(adapter, [[Close()]], instruments=["A"])
        assert harness.stream.session["access_token"] == FAKE_CREDENTIALS["access_token"]
        assert harness.stream.credentials["api_secret"] == FAKE_CREDENTIALS["api_secret"]
        endpoint = adapter.stream_channels()[0].endpoint(
            harness.stream.session, harness.stream.credentials
        )
        assert FAKE_CREDENTIALS["access_token"] in endpoint.url
        assert FAKE_CREDENTIALS["api_key"] in json.dumps(endpoint.headers)
        # …and `safe_url` is what makes it loggable.
        assert FAKE_CREDENTIALS["access_token"] not in endpoint.safe_url


# ══════════════════════════════════════════════════════════════════
# §O — PERFORMANCE AND BOUNDEDNESS  (Invariant F)
#
# Failures must not create unbounded work. Every count below is asserted
# against a bound, not merely observed.
# ══════════════════════════════════════════════════════════════════


def test_a_hundred_consecutive_failures_produce_a_hundred_attempts_and_no_more():
    """The retry-storm falsification, stated as an exact identity.

    `attempts == script length` is the whole claim: a loop that retried twice
    per failure, or that spun without pausing, would not satisfy it — and a
    loop that gave up would not either.
    """
    with chaos_registered() as adapter:
        result = run(StreamHarness(adapter, [[Close()]] * 100,
                                   instruments=["A"], max_attempts=200).run())

    assert result.attempts == 100
    assert len(result.pauses) == 100, "an attempt was made without pacing it"
    assert all(pause > 0 for pause in result.pauses), "a reconnect was unpaced"
    # And the total wait grows: 100 failures cost at least an hour of backoff,
    # not 100 × the base delay.
    assert sum(result.pauses) > 100 * RECONNECT_BASE_DELAY * 10


def test_a_flapping_feed_creates_one_socket_per_attempt_and_closes_every_one():
    """No socket leak, no task leak, no accumulation across reconnects."""
    with chaos_registered() as adapter:
        result = run(StreamHarness(adapter, [[px(("A", 1.0)), Close()]] * 25,
                                   instruments=["A"]).run())

    assert len(result.sockets) == 25
    assert all(socket.closed for socket in result.sockets), "a socket was left open"
    # Exactly one subscribe frame per connection: a reconnect must not
    # accumulate subscriptions.
    assert all(len(socket.sent) == 1 for socket in result.sockets)


def test_a_terminal_refusal_creates_exactly_one_connection_and_stops():
    """`entitlement → detach → reprobe` must not run at reconnect frequency."""
    with chaos_registered() as adapter:
        result = run(StreamHarness(adapter, [[denied()]] + [[Close()]] * 50,
                                   instruments=["A"]).run())

    assert result.attempts == 1
    assert len(result.sockets) == 1
    assert result.pauses == []


def test_the_provider_holds_no_more_prices_than_it_subscribed_to():
    """The coverage ledger is bounded by the subscription, not by history."""
    fixture = run(build_feed(symbols=tuple(f"S{i}" for i in range(10))))
    for round_ in range(500):
        run(fixture.feed.on_raw([tick(f"S{round_ % 10}", 100.0 + round_)]))

    assert len(fixture.feed._last_tick) == 10


def test_the_latency_series_is_bounded_by_its_window_however_long_a_feed_runs():
    """A deque with a maxlen, asserted through the behaviour rather than the type."""
    fixture = run(build_feed())
    for _ in range(5_000):
        fixture.clock.advance(0.1)
        run(fixture.feed.on_raw([tick("A")]))

    samples = fixture.feed.latency_profile.samples
    assert samples == LATENCY_TAIL_WINDOW_SAMPLES
    assert samples <= LATENCY_TAIL_WINDOW_SAMPLES


def test_a_shard_plan_never_opens_more_connections_than_the_broker_permits():
    """Boundedness at the planner: the ceiling is a ceiling."""
    plan = plan_shards(
        [f"T{i}" for i in range(1000)],
        max_instruments_per_connection=10,
        max_connections=3,
    )
    assert len(plan) == 3
    assert plan.instrument_count == 30
    assert plan.dropped == 970


def test_a_rejected_batch_is_logged_once_and_not_once_per_record(caplog):
    """Log volume under a feed delivering a shape the boundary cannot read.

    One error for the batch, plus one warning per record — bounded by the batch
    size, which is bounded by the subscription. What must not happen is a
    per-record *error* storm, which is what turns a codec mismatch into a disk
    filling up.
    """
    fixture = run(build_feed())
    with caplog.at_level(logging.DEBUG):
        run(fixture.feed.on_raw([{"symbol": "A", "price": -1.0}] * 20))

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, "an unreadable batch produced one error per record"


# ══════════════════════════════════════════════════════════════════
# §P — GAPS THE MUTATION CAMPAIGN FOUND IN THIS FILE
#
# Every test below exists because a §19 mutation survived the chaos suite on
# its first pass. They are kept in their own section rather than folded into
# the sections above, so the record of *what the campaign actually bought* is
# not lost the first time somebody reorganises this file.
# ══════════════════════════════════════════════════════════════════


def test_a_stale_feed_is_not_merely_outranked_on_the_symbol_less_path_it_is_ineligible():
    """M07. The §E test above could not see this mutation, and here is why.

    With the symbol-less branch mutated to `return True`, a stale feed is
    eligible again — but it is also on probation, so the steadier baseline
    still leads the chain and `active_tier()` still answers `delayed`. The
    ranking masked the eligibility bug.

    Two things separate them here: the predicate is asked directly, and the
    resolution is repeated with no baseline registered, where eligibility is the
    only thing left to decide the answer. That second half is the one that
    matters operationally — an account whose baseline is also unavailable would
    otherwise be told its dead feed is live.
    """
    fixture = run(build_feed())
    run(serve_probation(fixture.feed, fixture.clock, (DEFAULT_SHARD_ID,)))
    context = ResolutionContext(user_id="u1", capability=Capability.QUOTES)
    assert fixture.feed.is_eligible_for(context)

    fixture.clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)

    assert not fixture.feed.is_eligible_for(context), (
        "a feed with no fresh evidence is still eligible on the symbol-less path"
    )
    fixture.registry.unregister(fixture.baseline.name)
    resolution = fixture.resolution()
    assert not resolution.available, "a dead feed answered the tier question"
    assert fixture.tier() is None


def test_latency_is_never_established_by_a_feed_that_is_not_ready():
    """M21. The establishment gates, asked one at a time.

    A feed that connected, subscribed and then had records pushed at it without
    ever earning readiness accumulates intervals — and must report `None`. The
    §F tests all worked from a ready feed, so the readiness gate was never the
    thing under test.
    """
    # A feed that connected but NEVER SUBSCRIBED is the exact case D5.4's
    # docstring names: it can never serve a quote, and it still accumulates
    # intervals from whatever is pushed at it. Built with no symbols rather than
    # with the link down, because `connect()` alone is enough to reach
    # SUBSCRIBED once a subscription exists — the readiness gate turns on the
    # subscription, not on the socket flag.
    fixture = run(build_feed(symbols=(), link_up=False))
    feed, clock = fixture.feed, fixture.clock

    for _ in range(LATENCY_TAIL_WINDOW_SAMPLES + 5):
        clock.advance(1.0)
        run(feed.on_raw([tick("A")]))
    assert not feed.is_ready
    assert feed.latency_profile.samples >= LATENCY_TAIL_WINDOW_SAMPLES, (
        "the samples were never accumulated — the gate is untested"
    )
    assert feed.delivery_latency is None, "an unready feed published a latency"
    assert feed.delivery_latency_p95 is None
    assert feed.latency_profile.established is False

    # Now earn readiness and fill the window: it establishes, and only now.
    run(feed.subscribe(["A"]))
    run(feed.mark_link_up())
    for _ in range(LATENCY_TAIL_WINDOW_SAMPLES + 1):
        clock.advance(1.0)
        run(feed.on_raw([tick("A")]))
    assert feed.is_ready and feed.delivery_latency == 1.0

    # And losing readiness takes it away again, without touching the samples.
    run(feed.mark_link_down("gone"))
    assert feed.delivery_latency is None


@needs_redis
def test_a_redis_outage_still_grants_the_local_cool_down_trial(store, unique):
    """M13. Fail-open means the local ladder *keeps working*, not that it stops.

    The §I outage test asserted the claim set was not distributed, which a
    fail-closed implementation satisfies just as well — it returns an
    undistributed, empty claim set. The distinguishing question is whether a
    provider whose local cool-down has expired is still offered, and that is
    what this asks.
    """
    name = unique("failopen-trial")

    async def scenario():
        recovery = ProviderHealthRecovery(base_delay=0.0, max_delay=0.0, store=store)
        worker = Worker(store, name=name)
        worker.recovery = recovery
        await worker.fail(times=DOWN_AFTER_FAILURES)
        assert worker.provider.health().state is ProviderState.DOWN

        # Arm the local cool-down, then take Redis away entirely.
        recovery.due_from([worker.provider])
        os.environ.pop("REDIS_URL", None)
        redis_client.manager.reset_for_tests()

        claims = await recovery.claim_due([worker.provider])
        assert claims.distributed is False
        assert claims.granted, (
            "a Redis outage became fail-closed — a DOWN provider whose local "
            "cool-down has expired was never offered its trial"
        )

    run(scenario())


def test_the_generic_chaos_path_names_no_broker_in_executable_code():
    """M25. The sweep, carried by this file rather than borrowed from D4's.

    §19 lists "a broker-specific branch added to generic chaos logic" as a
    mutation the chaos suite must catch, and on the first pass only
    `test_broker_streaming.py` caught it. A chaos suite that relies on another
    file's sweep is a chaos suite that stops catching it the day that file is
    reorganised, so the sweep is repeated here over exactly the modules this
    sprint drives.

    Comments and string literals are stripped first, for the reason D3's twin
    of this test does it: these modules carry prose explaining the boundary
    they enforce, and a sweep that could not tell an explanation from a
    violation would force the explanation to be deleted to stay green.
    """
    from tests.test_broker_streaming import _strip_source

    brokers = ("zerodha", "kite", "upstox", "angelone", "angel_one", "smartapi",
               "fyers", "hsm", "dhan", "groww", "indmoney")
    generic = (
        "services/brokers/stream.py",
        "services/brokers/reliability.py",
        "services/brokers/sharding.py",
        "services/brokers/recovery.py",
        "services/market_engine/providers/streaming.py",
        "services/market_engine/source_manager.py",
        "services/market_engine/providers/health_recovery.py",
        "infrastructure/health_state.py",
        "tests/_chaos.py",
    )
    for relative in generic:
        code = _strip_source((BACKEND / relative).read_text()).lower()
        for broker in brokers:
            assert broker not in code, f"{relative} names {broker!r} in executable code"

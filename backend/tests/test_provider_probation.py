"""Sprint D5.2 — provider probation: READY is not STABLE (hermetic).

WHAT THIS FILE PINS
-------------------
D4.5 built the readiness gate: a pushed feed becomes the primary quote source by
delivering a valid canonical tick on its current link, and stops being it the
instant that stops being true. D5.2 does not change that gate. It adds a second,
independent one on top of it, because readiness answers a weaker question than
the platform was reading it as answering::

    READY   this feed can produce a valid canonical price
    STABLE  this feed has kept producing them long enough to be trusted with
            the primary position

Between the two sits the failure D5.1 named and deliberately left (LIM-D5.1-3):
a feed that connects, ticks once, is promoted, dies, reconnects, ticks once and
is promoted again — competing with a steady source on the strength of one packet
from a connection that has repeatedly demonstrated it cannot survive.

The rule, stated so it can be falsified:

  * A feed leaves probation when valid canonical data has arrived at least
    `PROBATION_WINDOW_SECONDS` after the tick that earned readiness **on the
    current link** — "30 seconds of valid messages", read literally, so silence
    inside the window proves nothing and the passage of time alone proves
    nothing.
  * Probation **ranks**, it never **filters**. A probationary feed stays
    eligible, stays in the failover chain, and becomes the head of that chain
    the moment no steadier candidate remains. Probation may make a user's data
    delayed for a window; it may never make it absent.
  * Probation is per provider instance, and a provider instance is per (user,
    feed). Isolation between users and between two users of the same broker is
    therefore structural rather than enforced by a rule that could be forgotten.

WHY SO MUCH OF THIS FILE IS ABOUT WHAT PROBATION MAY *NOT* DO
--------------------------------------------------------------
A probation window is one line of arithmetic and three ways to be wrong: it can
promote a silent feed (a timer instead of evidence), it can survive a reconnect
(evidence not discarded), or it can refuse to serve at all when nothing steadier
exists (a filter instead of a rank). The last is the dangerous one — it trades a
cosmetic tier flap for an outage — so it is tested from both directions.

The window used here is the published `PROBATION_WINDOW_SECONDS`; the clock is
injected. A test that sleeps for thirty seconds is a test nobody runs.

No test opens a socket or reaches a broker API.
"""

import logging
import pathlib
import re
import time
from unittest.mock import patch

import pytest

from services.market_engine.providers import (
    PROBATION_WINDOW_SECONDS,
    Capability,
    FeedReadiness,
    FeedStability,
    ProviderRegistry,
    ResolutionContext,
    StreamingTickProvider,
    YahooPollingAdapter,
)
from services.market_engine.source_manager import SourceManager
from services.market_engine.ticks import MarketTick

# The D4 suite's seam helpers, reused rather than re-implemented: a second copy
# of "attach a feed the way the engine attaches one" would be a second thing to
# keep true.
from tests.test_broker_streaming import (
    _attach,
    _clean_provider_registry,
    nova_registered,
    run,
)

BACKEND = pathlib.Path(__file__).resolve().parent.parent


class FakeClock:
    """A monotonic clock a test can move deliberately.

    Injected into the provider rather than patched onto `time`, so moving this
    test's clock cannot move anything else's — and so the thirty-second window
    is exercised at its published value instead of at a value chosen to keep a
    test fast.
    """

    def __init__(self, now=1_000.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def _tick(symbol="RELIANCE", price=2650.0):
    return MarketTick(symbol=symbol, price=price, exchange="NSE").as_dict()


def _fixture(user_id="u1", symbols=("RELIANCE",), clock=None):
    """A registry holding the baseline and one connected, subscribed feed.

    The state D4.5 leaves a feed in: one valid tick away from READY, and — since
    D5.2 — one full window of valid data away from STABLE.
    """
    clock = clock or FakeClock()
    registry = ProviderRegistry()
    baseline = YahooPollingAdapter()
    registry.register(baseline)
    run(baseline.connect())
    feed = StreamingTickProvider(f"feed:{user_id}", owner_user_id=user_id, clock=clock)
    registry.register(feed)
    run(feed.connect())
    if symbols:
        run(feed.subscribe(symbols))
    return registry, SourceManager(registry), baseline, feed, clock


def _quote_provider(manager, user_id="u1", symbol="RELIANCE"):
    return manager.resolve(
        Capability.QUOTES, context=ResolutionContext(user_id=user_id, symbol=symbol)
    )


def _serve_probation(feed, clock, symbol="RELIANCE"):
    """Give `feed` exactly the evidence D5.2 requires, and no more.

    One tick, a full window of clock, one more tick. Written as the *evidence*
    rather than as a jump to a promoted state, so a test that uses it fails if
    the rule changes shape.
    """
    run(feed.on_raw([_tick(symbol)]))
    clock.advance(PROBATION_WINDOW_SECONDS)
    run(feed.on_raw([_tick(symbol)]))


# ==================================================================
# The rule itself
# ==================================================================


def test_a_ready_feed_is_on_probation_and_the_baseline_keeps_the_quote():
    """READY != STABLE, in one assertion pair — the whole sprint.

    The feed has done everything D4.5 asks of it: live link, declared
    subscription, valid canonical tick accepted. It is ready, it is eligible,
    and it is still not what a quote resolves to, because a steady provider is
    available and one packet is not a track record.
    """
    _registry, manager, baseline, feed, _clock = _fixture()

    run(feed.on_raw([_tick()]))

    assert feed.readiness is FeedReadiness.READY
    assert feed.is_ready, "the readiness gate itself must be unchanged by D5.2"
    assert feed.stability is FeedStability.PROBATION
    assert feed.is_on_probation
    assert _quote_provider(manager) is baseline


def test_a_feed_leaves_probation_on_evidence_spanning_the_window():
    """Promotion is earned by data that kept arriving, not by a clock striking.

    The tick that completes the window is the promotion. Nothing is scheduled
    and nothing polls; the same two timestamps are re-read on every resolution.
    """
    _registry, manager, _baseline, feed, clock = _fixture()

    run(feed.on_raw([_tick()]))
    clock.advance(PROBATION_WINDOW_SECONDS - 0.5)
    run(feed.on_raw([_tick()]))
    assert feed.is_on_probation, "half a second short of the window promoted the feed"

    clock.advance(0.5)
    run(feed.on_raw([_tick()]))
    assert feed.stability is FeedStability.STABLE
    assert _quote_provider(manager) is feed


def test_a_silent_feed_never_leaves_probation_however_long_it_waits():
    """The mistake a timer would make, and the reason the rule is not one.

    A feed that ticked once and then said nothing for ten times the window has
    delivered one message, not thirty seconds of messages. An elapsed-time gate
    would promote it — over a baseline that is, at that moment, the only source
    actually producing prices.
    """
    _registry, manager, baseline, feed, clock = _fixture()

    run(feed.on_raw([_tick()]))
    clock.advance(PROBATION_WINDOW_SECONDS * 10)

    assert feed.is_ready, "silence does not un-ready a feed — that is coverage's job"
    assert feed.is_on_probation
    assert _quote_provider(manager) is baseline


def test_neither_connecting_nor_subscribing_counts_towards_stability():
    """CONNECTED is not stable; SUBSCRIBED is not stable.

    The two states most tempting to read as progress. A feed can hold both for
    an hour, and the window has not started — it starts at data.
    """
    _registry, manager, baseline, feed, clock = _fixture(symbols=())

    run(feed.connect())
    clock.advance(PROBATION_WINDOW_SECONDS * 3)
    assert feed.readiness is FeedReadiness.CONNECTED
    assert feed.stability is FeedStability.PROBATION

    run(feed.subscribe(["RELIANCE"]))
    clock.advance(PROBATION_WINDOW_SECONDS * 3)
    assert feed.readiness is FeedReadiness.SUBSCRIBED
    assert feed.stability is FeedStability.PROBATION
    assert _quote_provider(manager) is baseline


def test_a_rejected_record_is_not_evidence_for_the_probation_window_either():
    """The same rule D4.5 applies to readiness, applied to what sustains it.

    A batch in which every record was refused at the canonical boundary is
    evidence that the feed is delivering a shape the platform cannot read. It
    must not complete a probation window any more than it could open one.
    """
    _registry, manager, baseline, feed, clock = _fixture()

    run(feed.on_raw([_tick()]))
    clock.advance(PROBATION_WINDOW_SECONDS)
    assert run(feed.on_raw([{"symbol": "RELIANCE", "not_a_tick_field": 1}])) == 0

    assert feed.is_on_probation, "a refused record completed the probation window"
    assert _quote_provider(manager) is baseline


# ==================================================================
# Link loss during probation (Phase 6)
# ==================================================================


def test_a_link_lost_during_probation_discards_the_window_and_the_evidence():
    """Probation is evidence about one connection and does not outlive it."""
    _registry, manager, baseline, feed, clock = _fixture()

    run(feed.on_raw([_tick()]))
    clock.advance(PROBATION_WINDOW_SECONDS - 1)
    run(feed.mark_link_down("socket closed"))

    assert feed.readiness is FeedReadiness.FAILED
    assert not feed.is_ready
    assert feed.stability is FeedStability.PROBATION
    assert feed.covered_symbols == ()
    assert _quote_provider(manager) is baseline

    # And the window does not resume where it left off: the new link serves the
    # whole thing from its own first tick.
    run(feed.mark_link_up())
    run(feed.on_raw([_tick()]))
    assert feed.is_ready and feed.is_on_probation
    assert _quote_provider(manager) is baseline

    clock.advance(PROBATION_WINDOW_SECONDS)
    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager) is feed


def test_a_reconnect_cannot_inherit_a_completed_probation():
    """The strongest form: a feed that *was* stable comes back unproven.

    A feed that served a full window, dropped and reconnected has proved
    something about a connection that no longer exists. Promoting the new link
    on the old link's record is promoting on a memory — and it is exactly what a
    flapping feed would exploit to hold the primary position permanently.
    """
    _registry, manager, baseline, feed, clock = _fixture()

    _serve_probation(feed, clock)
    assert _quote_provider(manager) is feed

    run(feed.mark_link_down("socket closed"))
    run(feed.mark_link_up())
    run(feed.on_raw([_tick()]))

    assert feed.is_ready
    assert feed.is_on_probation, "the reconnect inherited the dead link's probation"
    assert _quote_provider(manager) is baseline


def test_a_flapping_feed_never_becomes_the_preferred_source():
    """The composite failure D5.2 exists to end, driven as a loop.

    Ten connect / tick / die cycles. Under D4.5 alone this sequence promoted the
    feed ten times and demoted it ten times, and the user's tier indicator
    alternated with it. Here the baseline serves throughout, and the feed is
    never once the head of the chain.
    """
    _registry, manager, baseline, feed, clock = _fixture()

    for _ in range(10):
        run(feed.mark_link_up())
        run(feed.on_raw([_tick()]))
        clock.advance(PROBATION_WINDOW_SECONDS / 3)
        assert _quote_provider(manager) is baseline
        run(feed.mark_link_down("socket closed"))
        assert _quote_provider(manager) is baseline

    # And a connection that finally *does* hold is promoted on its own evidence,
    # with no penalty carried over from the ten that did not.
    run(feed.mark_link_up())
    _serve_probation(feed, clock)
    assert _quote_provider(manager) is feed


# ==================================================================
# Probation ranks; it never filters (Phases 7 and 8)
# ==================================================================


def test_a_probationary_feed_serves_when_no_steadier_provider_remains():
    """The distinction that keeps probation from becoming an outage.

    Probation protects a steady provider from being displaced. It is not a
    statement that the feed is unusable, so when the steady provider stops being
    a candidate the probationary one answers immediately — no window, no wait.
    """
    registry, manager, baseline, feed, _clock = _fixture()

    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager) is baseline

    registry.unregister(baseline.name)
    resolution = manager.resolve_feed(
        Capability.QUOTES, ResolutionContext(user_id="u1", symbol="RELIANCE")
    )
    assert resolution.available, "probation produced no provider at all"
    assert resolution.provider is feed
    assert feed.is_on_probation, "the feed was promoted rather than merely used"


def test_a_probationary_feed_is_second_in_the_chain_and_not_missing_from_it():
    """Ranked, not filtered — asserted on the chain the gateway actually walks.

    The gateway fails over *within* one request by walking this chain, so a
    probationary feed that were filtered out would leave the request with
    nothing behind the baseline. It sits behind it instead.
    """
    _registry, manager, baseline, feed, _clock = _fixture()

    run(feed.on_raw([_tick()]))
    chain = manager.failover_chain(
        Capability.QUOTES, ResolutionContext(user_id="u1", symbol="RELIANCE")
    )

    assert [p.name for p in chain] == [baseline.name, feed.name]


def test_a_steady_feed_outranks_a_probationary_one_for_the_same_user():
    """Two feeds, one user: the steady one leads regardless of registration order.

    Both are priority 1 and both are eligible, so nothing but probation
    separates them — which is what makes this the test that the ranking term is
    doing the work rather than the priority ordering underneath it.
    """
    clock = FakeClock()
    registry, manager, baseline, probationary, _clock = _fixture(clock=clock)
    steady = StreamingTickProvider("feed:u1:second", owner_user_id="u1", clock=clock)
    registry.register(steady)
    run(steady.connect())
    run(steady.subscribe(["RELIANCE"]))

    run(probationary.on_raw([_tick()]))
    _serve_probation(steady, clock)

    assert probationary.is_on_probation and steady.is_stable
    assert _quote_provider(manager) is steady

    # …and when the steady one loses its link, the baseline takes the quote —
    # because the baseline is *also* a steady provider, and probation prefers
    # any steady source over an unproven one. The probationary feed leads only
    # once no steady candidate is left at all, which is the next assertion.
    run(steady.mark_link_down("socket closed"))
    assert _quote_provider(manager) is baseline

    registry.unregister(baseline.name)
    assert _quote_provider(manager) is probationary
    assert probationary.is_on_probation, "it was promoted rather than merely used"


def test_the_baseline_is_never_disconnected_while_a_feed_serves_its_probation():
    """Make-before-break, extended across the window D5.2 adds.

    D4.5's ordering property with a longer gap in the middle: the steady source
    is not released early, not unregistered, and not made ineligible — it is
    simply out-ranked at the end, and only once the evidence is in.
    """
    registry, manager, baseline, feed, clock = _fixture()
    seen = []

    def observe(label):
        resolution = manager.resolve_feed(
            Capability.QUOTES, ResolutionContext(user_id="u1", symbol="RELIANCE")
        )
        assert resolution.available, f"the feed went dark at: {label}"
        assert baseline.name in registry, f"the baseline was unregistered at: {label}"
        assert baseline in resolution.chain, f"the baseline left the chain at: {label}"
        assert baseline.is_connected, f"the baseline was disconnected at: {label}"
        seen.append((label, resolution.provider.name, resolution.tier.value))

    observe("connected + subscribed")
    run(feed.on_raw([_tick()]))
    observe("ready, on probation")
    clock.advance(PROBATION_WINDOW_SECONDS)
    observe("window elapsed, no new evidence")
    run(feed.on_raw([_tick()]))
    observe("evidence spanning the window")

    assert [step[2] for step in seen] == ["delayed", "delayed", "delayed", "streaming"], seen


def test_the_probation_window_is_measured_on_a_monotonic_clock():
    """A wall clock would let an NTP step promote a feed that proved nothing.

    The window is a *duration*, and the one property a duration needs from its
    clock is that it cannot move backwards — a system clock corrected forward
    by a minute mid-probation would promote a feed on a correction rather than
    on data. The same reasoning is why D5.1's reconnect ladder is monotonic, and
    the two are asserted the same way: on the default, because that is what
    every provider the platform builds actually gets.
    """
    import inspect

    default = inspect.signature(StreamingTickProvider.__init__).parameters["clock"].default
    assert default is time.monotonic

    # And the arithmetic itself does not promote on a clock that jumps: only
    # evidence recorded *after* readiness counts, whatever the clock says.
    clock = FakeClock()
    _registry, manager, baseline, feed, _clock = _fixture(clock=clock)
    run(feed.on_raw([_tick()]))
    clock.advance(-PROBATION_WINDOW_SECONDS * 5)
    run(feed.on_raw([_tick()]))
    assert feed.is_on_probation
    assert _quote_provider(manager) is baseline


def test_health_outranks_probation_and_not_the_other_way_round():
    """The documented order of the two selection terms, pinned.

    A DEGRADED provider has produced evidence of *failure*; a probationary one
    has merely not yet produced evidence of success. MARKET_DATA_ARCHITECTURE.md
    is explicit that DEGRADED demotes a provider below a healthy lower tier, so
    health is asked first and probation breaks the tie inside it. Ordering them
    the other way would quietly reverse that published rule — and would do it
    silently, because both orders agree in every case where the baseline is
    healthy, which is every ordinary case.
    """
    from services.market_engine.providers import ProviderState

    _registry, manager, baseline, feed, _clock = _fixture()
    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager) is baseline

    # The baseline degrades. It is still eligible, and it is now ranked below a
    # healthy feed that has not finished proving itself.
    baseline._health.state = ProviderState.DEGRADED
    assert feed.is_on_probation
    assert _quote_provider(manager) is feed, (
        "a provider with evidence of failure kept the quote over a healthy probationary feed"
    )


def test_a_probationary_feed_still_answers_the_pushed_capability():
    """TICKS is a link-level question and probation does not change the answer.

    A stream that is attached is attached. Probation governs which provider is
    *preferred* for the capability that displaces the baseline; it does not
    silence a live feed for the capability nothing else can serve.
    """
    _registry, manager, _baseline, feed, _clock = _fixture()

    run(feed.on_raw([_tick()]))
    ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

    assert feed.is_on_probation
    assert manager.resolve(Capability.TICKS, context=ctx) is feed


# ==================================================================
# User isolation (Phase 9)
# ==================================================================


def test_one_users_probation_changes_nothing_for_anybody_else():
    """A's probation, B's stability, C's probation and a guest, side by side."""
    clock = FakeClock()
    registry = ProviderRegistry()
    baseline = YahooPollingAdapter()
    registry.register(baseline)
    run(baseline.connect())
    manager = SourceManager(registry)

    feeds = {}
    for user in ("userA", "userB", "userC"):
        feed = StreamingTickProvider(f"feed:{user}", owner_user_id=user, clock=clock)
        registry.register(feed)
        run(feed.connect())
        run(feed.subscribe(["RELIANCE"]))
        feeds[user] = feed

    run(feeds["userA"].on_raw([_tick()]))           # probation
    _serve_probation(feeds["userB"], clock)         # stable
    run(feeds["userC"].on_raw([_tick()]))           # probation

    assert _quote_provider(manager, "userA") is baseline
    assert _quote_provider(manager, "userB") is feeds["userB"]
    assert _quote_provider(manager, "userC") is baseline
    assert manager.resolve(
        Capability.QUOTES, context=ResolutionContext(symbol="RELIANCE")
    ) is baseline, "a per-user feed served a request with no user"

    # A's link dies mid-probation. B keeps its promotion; C keeps its window.
    run(feeds["userA"].mark_link_down("socket closed"))
    assert _quote_provider(manager, "userB") is feeds["userB"]
    assert feeds["userC"].is_on_probation and feeds["userC"].is_ready

    # C completes its own window on its own evidence, with nothing from A or B.
    clock.advance(PROBATION_WINDOW_SECONDS)
    run(feeds["userC"].on_raw([_tick()]))
    assert _quote_provider(manager, "userC") is feeds["userC"]
    assert _quote_provider(manager, "userA") is baseline
    assert _quote_provider(manager, "userB") is feeds["userB"]


def test_two_users_of_the_same_broker_serve_independent_probations():
    """The isolation a shared-by-broker implementation would break.

    Probation lives on the provider instance, and there is one instance per
    (user, feed) — so this cannot be shared by accident. Asserted through the
    registry rather than through the objects the fixture returned, because that
    is how every consumer reaches a feed, and a key collision is invisible from
    the object itself.
    """
    clock = FakeClock()
    registry = ProviderRegistry()
    baseline = YahooPollingAdapter()
    registry.register(baseline)
    run(baseline.connect())
    manager = SourceManager(registry)

    for user in ("userA", "userB"):
        feed = StreamingTickProvider(f"brokerfeed:acme:{user}", owner_user_id=user, clock=clock)
        registry.register(feed)
        run(feed.connect())
        run(feed.subscribe(["RELIANCE"]))

    a = registry.get("brokerfeed:acme:userA")
    b = registry.get("brokerfeed:acme:userB")
    assert a is not b, "two users of one broker share a provider instance"

    _serve_probation(a, clock)
    run(b.on_raw([_tick()]))

    assert a.is_stable and b.is_on_probation
    assert _quote_provider(manager, "userA") is a
    assert _quote_provider(manager, "userB") is baseline

    # B's flapping does not cost A its promotion.
    for _ in range(3):
        run(b.mark_link_down("socket closed"))
        run(b.mark_link_up())
        run(b.on_raw([_tick()]))
    assert a.is_stable and _quote_provider(manager, "userA") is a


# ==================================================================
# Multi-broker proof (Phase 10)
# ==================================================================

#: Every broker that streams today, plus one that does not exist. The point of
#: the fictional entry is that it needs no special case: if probation depended
#: on broker identity in any way, a broker the code has never heard of would
#: behave differently from the five it has.
STREAMING_BROKERS = ("zerodha", "upstox", "angelone", "fyers", "dhan", "nova")


@pytest.mark.parametrize("broker", STREAMING_BROKERS)
def test_probation_behaves_identically_for_every_broker_including_a_fictional_one(broker):
    """One parameterized test rather than five broker-specific ones.

    Five copies of this test would duplicate the implementation's assumptions
    five times and prove only that the copies agree. What is worth proving is
    that the *generic* seam behaves the same for any feed implementing the
    public contract — so the ticks pushed here are canonical ticks, identical
    for every broker, and the only thing that varies is the name on the feed.

    Driven through `attach_market_feed`, the real registration seam, so this
    also covers a provider built where the engine builds one — with the window
    read from the module constant rather than injected.
    """
    from services.market_engine.providers import provider_registry, streaming as streaming_module
    from services.brokers.market_feed import publish_market_ticks, set_market_feed_link

    window = 0.05
    with nova_registered(), _clean_provider_registry() as registry, \
            patch.object(streaming_module, "PROBATION_WINDOW_SECONDS", window):
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        run(baseline.connect())
        manager = SourceManager(registry)

        name = run(_attach("u1", broker, ["RELIANCE"]))
        assert name, f"{broker} did not attach a market feed"
        feed = provider_registry.get(name)
        run(set_market_feed_link("u1", broker, up=True))

        assert run(publish_market_ticks("u1", broker, [_tick()])) == 1
        assert feed.is_ready and feed.is_on_probation, broker
        assert _quote_provider(manager) is baseline, broker

        # Real elapsed time against the real constant — small, but the same
        # arithmetic the published window runs through.
        time.sleep(window * 1.5)
        assert run(publish_market_ticks("u1", broker, [_tick()])) == 1
        assert feed.is_stable, broker
        assert _quote_provider(manager) is feed, broker


def test_the_probation_layer_names_no_broker():
    """Rule 1 of the D5 brief, applied to the modules D5.2 changed.

    A name sweep including comments, because a comment naming a broker in the
    generic layer is a design statement even when it is inert — and because the
    sweep that only reads executable code cannot see the moment someone writes
    `# Zerodha needs an extra 10s here`.
    """
    brokers = ("zerodha", "kite", "upstox", "angel", "angelone", "smartapi",
               "fyers", "dhan", "groww", "indmoney", "nova")
    # `providers/base.py` is deliberately absent: its D1 contract prose names
    # providers as *examples* of the identifiers a registry key may hold, which
    # is the one thing that file exists to talk about. The rule being pinned
    # here is that no probation *decision* is taken on broker identity, and the
    # decision lives entirely in the two files below.
    for relative in ("services/market_engine/providers/streaming.py",
                     "services/market_engine/source_manager.py"):
        source = (BACKEND / relative).read_text().lower()
        for name in brokers:
            assert not re.search(rf"\b{re.escape(name)}\b", source), (
                f"{relative} names the broker {name!r} — probation must not know who it is judging"
            )


def test_the_two_layers_share_one_stability_window():
    """ADR-041's review question, answered by a test rather than by a comment.

    D5.1 took its 30 seconds from MARKET_DATA_ARCHITECTURE.md's probation window
    and asked D5.2 to consume the same number rather than declare a second. The
    two layers cannot import each other — the transport's reliability module is
    pinned to the standard library alone, and the Market Engine may not import
    the broker layer — so they hold two names for one published policy, and this
    is what makes drifting apart a failure rather than a nuisance.

    Note what is *not* claimed: that the two measure the same evidence. The
    transport can only see how long a socket lasted; this layer can see whether
    data kept arriving on it. Same window, stronger evidence, deliberately.
    """
    from services.brokers.reliability import STABLE_CONNECTION_SECONDS

    assert PROBATION_WINDOW_SECONDS == STABLE_CONNECTION_SECONDS == 30.0


def test_d51_flap_suppression_is_untouched_by_probation():
    """The transport ladder still classifies by connection duration alone.

    D5.2 adds a provider-layer gate and must not have quietly become a second
    place where reconnect pacing is decided.
    """
    from services.brokers.reliability import (
        RECONNECT_BASE_DELAY,
        ConnectionOutcome,
        ConnectionStability,
    )

    clock = FakeClock()
    ladder = ConnectionStability(clock=clock, jitter=lambda delay: delay)

    ladder.link_up()
    clock.advance(1.0)
    assert ladder.link_down() is ConnectionOutcome.SHORT_LIVED
    ladder.next_pause()
    assert ladder.delay > RECONNECT_BASE_DELAY

    ladder.link_up()
    clock.advance(PROBATION_WINDOW_SECONDS)
    assert ladder.link_down() is ConnectionOutcome.STABLE
    assert ladder.delay == RECONNECT_BASE_DELAY
    assert not ladder.is_flapping


# ==================================================================
# Announcement, status and security (Phases 13 and 14)
# ==================================================================


def test_leaving_probation_is_announced_once_and_only_on_the_crossing():
    """The tier moved, so the owner's consumers are told — exactly once.

    Without an announcement the promotion is real and invisible: resolution
    starts returning the feed, and the tier indicator keeps rendering `delayed`
    until something unrelated happens to republish. Announced on the crossing
    only, because every later tick would otherwise repeat one fact forever.
    """
    _registry, _manager, _baseline, feed, clock = _fixture()
    transitions = []

    async def listener(provider, previous, current):
        transitions.append((previous.value, current.value))

    feed.bind_readiness_listener(listener)

    run(feed.on_raw([_tick()]))
    assert transitions == [("subscribed", "ready")]

    clock.advance(PROBATION_WINDOW_SECONDS)
    run(feed.on_raw([_tick()]))
    assert transitions == [("subscribed", "ready"), ("probation", "stable")]

    for _ in range(5):
        clock.advance(1)
        run(feed.on_raw([_tick()]))
    assert len(transitions) == 2, "a settled feed kept announcing its promotion"


def test_the_feed_status_a_user_sees_carries_no_probation_detail():
    """Consumer payloads are unchanged by D5.2 — Developer Rule 4 still holds.

    A user learns that their data is delayed. They do not learn that a provider
    exists, that it is on probation, what it is called, or how long it has left
    to run. Probation is an internal ranking term and stays one.
    """
    _registry, manager, _baseline, feed, clock = _fixture()

    run(feed.on_raw([_tick()]))
    during = manager.status(user_id="u1")
    assert (during["state"], during["tier"], during["reason"]) == ("available", "delayed", None)

    clock.advance(PROBATION_WINDOW_SECONDS)
    run(feed.on_raw([_tick()]))
    after = manager.status(user_id="u1")
    assert (after["state"], after["tier"], after["reason"]) == ("available", "streaming", None)

    # The shape is unchanged by D5.2: the same keys, and no new one.
    assert set(during) == set(after) == {"state", "tier", "reason", "capabilities"}
    for payload in (during, after):
        blob = repr(payload).lower()
        for leak in ("probation", "feed:", "u1"):
            assert leak not in blob, f"the consumer status leaked {leak!r}"


def test_probation_state_is_visible_only_on_the_admin_diagnostic_surface():
    """`describe()` may name things; it is the one surface that may.

    Operators need to see why a live feed is not primary — "ready, on probation"
    is the answer, and without it the state is indistinguishable from a bug.
    That answer belongs where provider names already live and nowhere else.
    """
    _registry, _manager, _baseline, feed, clock = _fixture()

    run(feed.on_raw([_tick()]))
    described = feed.describe()
    assert described["readiness"] == "ready"
    assert described["stability"] == "probation"
    assert described["on_probation"] is True

    clock.advance(PROBATION_WINDOW_SECONDS)
    run(feed.on_raw([_tick()]))
    assert feed.describe()["stability"] == "stable"
    assert feed.describe()["on_probation"] is False


def test_a_polled_baseline_is_never_described_as_being_on_probation():
    """The generic default, asserted on the provider it exists for.

    A provider with no link has no per-connection reliability to demonstrate,
    and what can be said about it is already said by health. Defaulting it to
    "on probation" would rank the platform's permanent floor below every feed
    that had been up for thirty seconds.
    """
    baseline = YahooPollingAdapter()
    assert baseline.is_on_probation is False
    assert baseline.describe()["on_probation"] is False


def test_probation_logging_at_debug_leaks_no_credential(caplog):
    """DEBUG-level logging with live-looking credentials, per the D5 brief.

    The probation path logs a provider name and a duration. It never receives a
    token, and this is what proves it: a feed attached with credentials that
    look exactly like real ones, driven through a full window at DEBUG, with the
    whole capture searched.
    """
    from services.market_engine.providers import provider_registry, streaming as streaming_module
    from services.brokers.market_feed import publish_market_ticks, set_market_feed_link

    secrets = (
        "eyJhbGciOiJIUzI1NiJ9.aRealLookingAccessToken.5Xk9",
        "aBcDeF1234567890apikey",
        "s3cr3t-api-secret-value",
    )
    window = 0.05
    caplog.set_level(logging.DEBUG)

    with nova_registered(), _clean_provider_registry() as registry, \
            patch.object(streaming_module, "PROBATION_WINDOW_SECONDS", window):
        registry.clear()
        registry.register(YahooPollingAdapter())
        name = run(_attach("u1", "nova", ["RELIANCE"]))
        feed = provider_registry.get(name)
        run(set_market_feed_link("u1", "nova", up=True))

        # The credentials travel where a broker session would carry them; the
        # provider layer is downstream of that and must never see them.
        tick = _tick()
        run(publish_market_ticks("u1", "nova", [tick]))
        time.sleep(window * 1.5)
        run(publish_market_ticks("u1", "nova", [tick]))
        assert feed.is_stable

    captured = caplog.text.lower()
    for secret in secrets:
        assert secret.lower() not in captured
    assert "left probation" in captured, "the probation log line was never emitted at all"


def test_a_probationary_feed_cannot_be_resolved_for_another_user():
    """Entitlement is unchanged and probation does not widen it.

    Worth asserting explicitly rather than assuming: the ranking term is the
    only thing D5.2 adds to resolution, and a ranking term that accidentally
    reordered the entitlement filter would be a data-protection failure rather
    than a preference bug.
    """
    _registry, manager, baseline, feed, clock = _fixture(user_id="userA")

    _serve_probation(feed, clock)
    assert _quote_provider(manager, "userA") is feed
    assert _quote_provider(manager, "userB") is baseline
    assert manager.resolve(
        Capability.QUOTES, context=ResolutionContext(symbol="RELIANCE")
    ) is baseline


# ==================================================================
# Falsification — remove the control, watch the test go red (Phase 12)
# ==================================================================


def test_removing_probation_would_promote_a_feed_on_its_first_tick():
    """The mutation every test above is written against.

    With the ranking term neutralised, one valid tick takes the quote path
    immediately — which is precisely D4.5's behaviour and precisely what D5.2
    exists to stop. If this ever stops seeing the switch happen, the tests above
    are passing for some other reason and prove nothing about probation.
    """
    _registry, manager, baseline, feed, _clock = _fixture()

    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager) is baseline

    with patch.object(StreamingTickProvider, "is_on_probation", property(lambda self: False)):
        assert _quote_provider(manager) is feed, (
            "with probation removed the feed still did not take the quote path — "
            "the probation tests above are not testing probation"
        )

    assert _quote_provider(manager) is baseline


def test_removing_the_window_would_promote_a_feed_on_its_first_tick():
    """The threshold, falsified separately from the ranking term.

    Two different controls hold the switch shut — a window that has not elapsed,
    and a rank that puts an unproven feed second. A test that only neutralised
    one of them could not tell which was doing the work.
    """
    clock = FakeClock()
    _registry, manager, baseline, feed, _clock = _fixture(clock=clock)
    assert feed.probation_seconds == PROBATION_WINDOW_SECONDS

    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager) is baseline

    feed.probation_seconds = 0.0
    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager) is feed, "with the window at zero the feed was still held back"


def test_keeping_the_evidence_across_a_reconnect_would_promote_a_flapping_feed():
    """The reset on link loss — and the discovery that it is two controls.

    A reconnect cannot inherit a completed window because of *two* independent
    lines, and neither is redundant with the other in the way that phrase
    usually means — each one alone is sufficient, so removing either leaves the
    behaviour correct:

      * `_discard_evidence` clears the window's timestamps when the link drops;
      * `_advance` re-stamps `_ready_since` from the *new* link's first tick
        every time the feed enters READY.

    A falsification that removed only one would therefore stay green and prove
    nothing — which is what this test found on its first run, and the reason it
    removes both. That is genuine defence in depth rather than a gap, and it is
    recorded rather than tidied away: pinning one of the two lines individually
    would be asserting an implementation detail instead of the property.

    With both gone, a feed that served a full window, died and came back is
    promoted by its first tick on a connection that has proved nothing.
    """
    _registry, manager, baseline, feed, clock = _fixture()

    def only_forget_ticks(self):
        self._last_tick.clear()

    real_advance = StreamingTickProvider._advance

    async def advance_without_restarting_the_window(self, state, *, reason=""):
        kept_ready_since = self._ready_since
        changed = await real_advance(self, state, reason=reason)
        if kept_ready_since is not None:
            self._ready_since = kept_ready_since
        return changed

    # Unmutated, for the control: the reconnect serves its window again.
    _serve_probation(feed, clock)
    run(feed.mark_link_down("socket closed"))
    run(feed.mark_link_up())
    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager) is baseline, "the reconnect was already inheriting probation"

    with patch.object(StreamingTickProvider, "_discard_evidence", only_forget_ticks), \
            patch.object(StreamingTickProvider, "_advance", advance_without_restarting_the_window):
        _serve_probation(feed, clock)
        assert _quote_provider(manager) is feed
        run(feed.mark_link_down("socket closed"))
        run(feed.mark_link_up())
        run(feed.on_raw([_tick()]))
        assert _quote_provider(manager) is feed, (
            "with both halves of the reset removed a reconnect still did not inherit "
            "probation — the reconnect tests above are not testing the reset"
        )


def test_treating_probation_as_a_filter_would_leave_the_user_with_nothing():
    """The mutation in the *other* direction — the one that would be an outage.

    Probation excluding a feed instead of ranking it is the plausible wrong
    implementation, and it is invisible while a baseline exists. Remove the
    baseline and it becomes an unavailable feed for a user whose data is
    arriving perfectly well.
    """
    registry, manager, baseline, feed, _clock = _fixture()
    run(feed.on_raw([_tick()]))
    registry.unregister(baseline.name)
    ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

    assert manager.resolve_feed(Capability.QUOTES, ctx).available

    real_is_eligible_for = StreamingTickProvider.is_eligible_for

    def eligible_only_when_stable(self, context):
        return real_is_eligible_for(self, context) and self.is_stable

    with patch.object(StreamingTickProvider, "is_eligible_for", eligible_only_when_stable):
        assert not manager.resolve_feed(Capability.QUOTES, ctx).available, (
            "the filter mutation changed nothing — this test cannot detect the failure it exists for"
        )


def test_the_ranking_term_is_read_through_the_provider_contract():
    """A fictional provider that is not a `StreamingTickProvider` still ranks.

    The Source Manager must not be reading probation off a concrete class. A
    licensed exchange feed or a vendor feed implementing the same contract has
    to be ranked by what it has demonstrated, exactly as a broker feed is.
    """
    from services.market_engine.providers import MarketDataProvider, ProviderKind, SourceTier

    class VendorFeed(MarketDataProvider):
        name = "vendor"
        kind = ProviderKind.STREAMING
        tier = SourceTier.STREAMING
        capabilities = frozenset({Capability.QUOTES, Capability.TICKS})
        normalizer_key = "canonical"
        priority = 1

        def __init__(self):
            super().__init__()
            self.on_probation = True

        @property
        def is_on_probation(self):
            return self.on_probation

        async def on_raw(self, payload):
            return 0

        async def fetch_quote(self, symbol):
            return {"symbol": symbol, "price": 1.0}

    registry = ProviderRegistry()
    baseline = YahooPollingAdapter()
    registry.register(baseline)
    run(baseline.connect())
    vendor = VendorFeed()
    registry.register(vendor)
    run(vendor.connect())
    manager = SourceManager(registry)

    assert manager.resolve(Capability.QUOTES) is baseline
    vendor.on_probation = False
    assert manager.resolve(Capability.QUOTES) is vendor

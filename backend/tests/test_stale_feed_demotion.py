"""Sprint D5.3 — provider stability decay and stale-feed demotion (hermetic).

WHAT THIS FILE PINS
-------------------
D4.5 built the readiness gate; D5.2 added the probation window on top of it and
left one question written down and unanswered: *stability does not decay*. The
D5.3 audit answered it from the code, and the answer was that two things were
wrong, both caused by the coverage window being asked in only one of the two
places that needed it::

    is_eligible_for(context carrying a symbol)  →  covers(symbol)  →  window ✓
    is_eligible_for(context carrying no symbol) →  return True     →  none    ✗

1. The symbol-less branch is what `active_tier()`, `status()` and the gateway's
   `source_tier()` resolve through — the user's tier indicator and the AI's
   freshness context. A feed whose socket stayed up but whose data stopped was
   filtered out of every real quote and still won *that* resolution, so the
   platform reported `tier: streaming` to a user who had not received a live
   price in hours.

2. :attr:`StreamingTickProvider.stability` compared two past instants with no
   upper bound, so the same dead feed still reported STABLE, still reported
   `is_on_probation == False`, and still outranked a feed that was genuinely
   delivering data.

The rule D5.3 adds, stated so it can be falsified:

  * A feed has **fresh evidence** when an accepted canonical tick arrived within
    `tick_max_age_seconds` — the coverage window, reused, not a second constant.
  * A feed without fresh evidence is **not STABLE**, whatever it once proved,
    and is therefore ranked with the unproven feeds.
  * A feed without fresh evidence is **not eligible** for the quote capability,
    with or without a named instrument.
  * Evidence resuming on the *same* link restores stability immediately. A
    reconnect is the other case and D5.2's `_discard_evidence` already owns it.

WHAT THIS FILE IS MOSTLY ABOUT NOT DOING
-----------------------------------------
Every failure mode of a decay rule is a way of confusing "this link is alive"
with "this feed is delivering". So the tests below spend more effort proving
what may *not* count as evidence — an open socket, a reconnect, a rejected
record, elapsed time — than proving what does, and several assert that D5.3
changed nothing: probation still ranks rather than filters, Yahoo is still there
at every instant, and no broker is named anywhere in the decision.

The clock is injected and monotonic. No test sleeps, opens a socket, or reaches
a broker API.
"""

import pathlib
import re
import time
from unittest.mock import patch

import pytest

from services.market_engine.providers import (
    DEFAULT_TICK_MAX_AGE_SECONDS,
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

# The D4/D5.2 seam helpers, reused rather than re-implemented.
from tests.test_broker_streaming import (
    _attach,
    _clean_provider_registry,
    nova_registered,
    run,
)
from tests.test_provider_probation import FakeClock, _tick

BACKEND = pathlib.Path(__file__).resolve().parent.parent

#: Comfortably past the coverage window, so "stale" in these tests is never a
#: borderline value that a rounding change could flip.
WELL_PAST_THE_WINDOW = DEFAULT_TICK_MAX_AGE_SECONDS * 10


def _fixture(user_id="u1", symbols=("RELIANCE",), clock=None):
    """A registry holding the baseline and one connected, subscribed feed."""
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


def _make_stable(feed, clock, symbol="RELIANCE"):
    """Give `feed` exactly the evidence D5.2 requires to leave probation."""
    run(feed.on_raw([_tick(symbol)]))
    clock.advance(PROBATION_WINDOW_SECONDS)
    run(feed.on_raw([_tick(symbol)]))
    assert feed.is_stable, "fixture did not reach the state the test is about"


def _quote_ctx(user_id="u1", symbol="RELIANCE"):
    return ResolutionContext(user_id=user_id, symbol=symbol).for_capability(
        Capability.QUOTES
    )


def _tier_ctx(user_id="u1"):
    """The context the tier indicator resolves through: capability, no symbol."""
    return ResolutionContext.for_user(user_id).for_capability(Capability.QUOTES)


def _quote_provider(manager, user_id="u1", symbol="RELIANCE"):
    return manager.resolve(
        Capability.QUOTES, context=ResolutionContext(user_id=user_id, symbol=symbol)
    )


# ==================================================================
# The rule itself
# ==================================================================


def test_a_stable_feed_that_keeps_ticking_stays_stable_and_keeps_the_quote():
    """The control case. Decay must cost a working feed nothing.

    Written as repeated evidence across many multiples of the coverage window
    rather than as one assertion, because a decay rule that accidentally
    measured from `_ready_since` instead of from the last tick would pass a
    single check and fail this one.
    """
    _registry, manager, _baseline, feed, clock = _fixture()
    _make_stable(feed, clock)

    for _ in range(10):
        clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS * 0.5)
        run(feed.on_raw([_tick()]))
        assert feed.stability is FeedStability.STABLE
        assert not feed.is_on_probation
        assert _quote_provider(manager) is feed
        assert manager.active_tier(Capability.QUOTES, user_id="u1").value == "streaming"


def test_a_stable_feed_goes_stale_once_the_coverage_window_passes_in_silence():
    """The sprint, in one test: STABLE is not permanent.

    The feed is not disconnected, not failed, and still READY — its socket is
    fine. What changed is only that no canonical tick arrived, which is the one
    thing D5.3 counts.
    """
    _registry, manager, baseline, feed, clock = _fixture()
    _make_stable(feed, clock)

    clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)

    assert feed.readiness is FeedReadiness.READY, "the link was not the thing that changed"
    assert feed.is_link_up
    assert not feed.has_fresh_evidence
    assert feed.stability is FeedStability.PROBATION
    assert feed.is_on_probation
    assert _quote_provider(manager) is baseline
    assert manager.active_tier(Capability.QUOTES, user_id="u1").value == "delayed"


def test_the_tier_a_user_sees_stops_saying_streaming_when_the_feed_goes_quiet():
    """The user-visible half of the defect, pinned on the surface it reached.

    `status()` is what the frontend indicator and the AI's freshness context
    read. Before D5.3 this asserted `streaming` forever: the symbol-less
    resolution had no freshness term at all, so a dead feed kept winning it
    while being filtered out of every actual quote.
    """
    _registry, manager, _baseline, feed, clock = _fixture()
    _make_stable(feed, clock)
    assert manager.status(user_id="u1")["tier"] == "streaming"

    clock.advance(WELL_PAST_THE_WINDOW)

    status = manager.status(user_id="u1")
    assert status["tier"] == "delayed", "a silent feed still claimed to be live"
    assert status["state"] == "available", "demotion must not become an outage"
    assert "quotes" in status["capabilities"]


def test_a_stale_feed_cannot_outrank_a_feed_that_is_actually_delivering():
    """The desired property of the brief, stated against two competing feeds.

    The stale one served a full probation window and the fresh one has served
    none of it, so before D5.3 the stale feed won on the probation term — the
    platform preferring an hour-old memory to data arriving now.
    """
    registry, manager, _baseline, stale, clock = _fixture()
    _make_stable(stale, clock)

    fresh = StreamingTickProvider("feed2:u1", owner_user_id="u1", clock=clock)
    registry.register(fresh)
    run(fresh.connect())
    run(fresh.subscribe(["RELIANCE"]))

    clock.advance(WELL_PAST_THE_WINDOW)
    run(fresh.on_raw([_tick()]))

    assert not stale.has_fresh_evidence and fresh.has_fresh_evidence
    for ctx in (_quote_ctx(), _tier_ctx()):
        chain = [p.name for p in manager.failover_chain(Capability.QUOTES, ctx)]
        assert stale.name not in chain, f"a stale feed survived resolution: {chain}"
        assert fresh.name in chain, f"the live feed was dropped: {chain}"


def test_evidence_resuming_on_the_same_link_restores_stability_immediately():
    """A quiet instrument must not be punished for being illiquid.

    The link never dropped, so nothing was discarded and the window this feed
    proved is still the window of the connection it is still on. Requiring the
    window to be re-served here would mean a stock that trades every few minutes
    could never be stable — a demotion for illiquidity rather than for
    unreliability. The reconnect case is the other one, and is tested below.
    """
    _registry, manager, baseline, feed, clock = _fixture()
    _make_stable(feed, clock)

    clock.advance(WELL_PAST_THE_WINDOW)
    assert feed.is_on_probation and _quote_provider(manager) is baseline

    run(feed.on_raw([_tick()]))

    assert feed.stability is FeedStability.STABLE
    assert _quote_provider(manager) is feed


def test_silence_alone_never_creates_stability():
    """D5.2's rule, re-asserted because D5.3 touches the same expression.

    A feed that ticks once and then waits must not become stable by waiting, and
    it must not become stable by waiting *past the coverage window* either —
    which is the new way to get this wrong, since that is the point at which
    D5.3 starts consulting a second timestamp.
    """
    _registry, manager, baseline, feed, clock = _fixture()
    run(feed.on_raw([_tick()]))
    assert feed.is_ready and feed.is_on_probation

    for elapsed in (PROBATION_WINDOW_SECONDS * 2, WELL_PAST_THE_WINDOW):
        clock.advance(elapsed)
        assert feed.stability is FeedStability.PROBATION, elapsed
        assert not feed.is_stable, elapsed
        assert _quote_provider(manager) is baseline, elapsed


def test_a_connected_socket_is_never_fresh_evidence():
    """Rule 6 of the brief: socket-open time is not market-data evidence.

    Every transport event the platform has — connect, subscribe, link-up,
    link-up again — is applied here in turn, and none of them may move the
    freshness predicate. Only `on_raw` does.
    """
    _registry, _manager, _baseline, feed, clock = _fixture(symbols=())

    assert not feed.has_fresh_evidence
    run(feed.connect())
    assert not feed.has_fresh_evidence, "connecting counted as data"
    run(feed.subscribe(["RELIANCE"]))
    assert not feed.has_fresh_evidence, "subscribing counted as data"
    run(feed.mark_link_up())
    assert not feed.has_fresh_evidence, "a link-up counted as data"
    clock.advance(1.0)
    run(feed.mark_link_up())
    assert not feed.has_fresh_evidence, "a second link-up counted as data"
    assert feed.stability is FeedStability.PROBATION

    run(feed.on_raw([_tick()]))
    assert feed.has_fresh_evidence, "an accepted canonical tick was not evidence"


def test_a_rejected_record_does_not_refresh_a_stale_feed():
    """Staleness is refreshed by *valid* canonical data or not at all.

    A feed delivering a shape this boundary does not recognise has demonstrated
    the opposite of freshness, so counting the arrival would keep a broken feed
    permanently fresh — the failure mode that looks healthiest from outside.
    """
    _registry, manager, baseline, feed, clock = _fixture()
    _make_stable(feed, clock)
    clock.advance(WELL_PAST_THE_WINDOW)

    assert run(feed.on_raw([{"symbol": "RELIANCE", "last_traded_price": 2650.0}])) == 0

    assert not feed.has_fresh_evidence
    assert feed.is_on_probation
    assert _quote_provider(manager) is baseline


def test_a_reconnect_discards_stability_and_the_evidence_behind_it():
    """D5.2's per-link reset, re-pinned because D5.3 reads the same timestamps.

    The danger D5.3 introduces is a decay rule written so that a reconnect looks
    like "fresh again" — `_last_evidence_at` cleared to None must read as stale,
    never as unknown-and-therefore-fine.
    """
    _registry, manager, baseline, feed, clock = _fixture()
    _make_stable(feed, clock)
    assert _quote_provider(manager) is feed

    run(feed.mark_link_down("socket closed"))
    assert not feed.has_fresh_evidence
    assert feed.stability is FeedStability.PROBATION
    assert feed.covered_symbols == ()

    run(feed.mark_link_up())
    assert not feed.has_fresh_evidence, "a reconnect was treated as fresh evidence"
    assert feed.readiness is FeedReadiness.SUBSCRIBED
    assert _quote_provider(manager) is baseline

    # And the window is genuinely re-served, not inherited.
    run(feed.on_raw([_tick()]))
    assert feed.is_ready and feed.is_on_probation
    clock.advance(PROBATION_WINDOW_SECONDS)
    run(feed.on_raw([_tick()]))
    assert feed.is_stable


def test_staleness_is_measured_on_a_monotonic_injected_clock():
    """Rule: every duration here is a duration, never a wall-clock instant.

    Two halves. The provider's default clock must be `time.monotonic`, so a
    feed built at runtime cannot be demoted — or kept alive — by an NTP step.
    And the freshness predicate must read that clock on every call rather than
    caching a value, which is what makes decay lazy instead of scheduled.
    """
    assert StreamingTickProvider("probe")._clock is time.monotonic

    _registry, _manager, _baseline, feed, clock = _fixture()
    _make_stable(feed, clock)

    reads = []
    original = clock.now

    def counting_clock():
        reads.append(original)
        return clock.now

    feed._clock = counting_clock
    assert feed.has_fresh_evidence
    assert reads, "freshness was answered without consulting the clock"

    # Read again after moving only the clock: a cached answer would still say
    # fresh, which is how decay would silently become a no-op.
    reads.clear()
    clock.advance(WELL_PAST_THE_WINDOW)
    assert not feed.has_fresh_evidence
    assert reads, "the second read did not consult the clock — the value is cached"


# ==================================================================
# What D5.3 must NOT have changed
# ==================================================================


def test_yahoo_remains_available_at_every_instant_of_the_decay():
    """Rule 12. The baseline is the floor and demotion may never remove it.

    Sampled across the whole lifecycle — before readiness, on probation, stable,
    stale, and recovered — because a fallback that exists at the start and end
    of a transition but not in the middle is not a fallback.
    """
    _registry, manager, baseline, feed, clock = _fixture()

    def baseline_is_there(stage):
        chain = manager.failover_chain(Capability.QUOTES, _quote_ctx())
        assert baseline in chain, f"the baseline vanished at {stage}: {chain}"
        assert baseline.is_connected, f"the baseline was disconnected at {stage}"
        assert manager.resolve(Capability.QUOTES) is not None, stage

    baseline_is_there("registered")
    run(feed.on_raw([_tick()]))
    baseline_is_there("probation")
    clock.advance(PROBATION_WINDOW_SECONDS)
    run(feed.on_raw([_tick()]))
    baseline_is_there("stable")
    clock.advance(WELL_PAST_THE_WINDOW)
    baseline_is_there("stale")
    run(feed.on_raw([_tick()]))
    baseline_is_there("recovered")


def test_make_before_break_survives_the_decay_and_the_recovery():
    """The baseline is never released, and the feed is never unregistered.

    Decay is a resolution outcome, so demotion must be expressible without
    touching either provider's registration or connection. If a stale feed had
    to be disconnected to stop being preferred, the recovery would be a
    reconnect and the whole D5.2 window would be re-served for a link that never
    died.
    """
    registry, manager, baseline, feed, clock = _fixture()
    _make_stable(feed, clock)
    registered = sorted(p.name for p in registry.all())

    clock.advance(WELL_PAST_THE_WINDOW)
    assert _quote_provider(manager) is baseline

    assert sorted(p.name for p in registry.all()) == registered
    assert feed.is_connected, "a stale feed was disconnected to demote it"
    assert feed.readiness is FeedReadiness.READY, "decay walked the readiness gate back"
    assert baseline.is_connected


def test_staleness_ranks_and_never_filters_the_last_provider_out():
    """Rule 9, applied to the new term: demotion may never produce an outage.

    With the baseline gone, a stale feed is the only thing left. It must still
    answer for the pushed capability its link genuinely serves, and the resolver
    must still return *something* rather than reporting the user unavailable.
    """
    registry, manager, baseline, feed, clock = _fixture()
    _make_stable(feed, clock)
    registry.unregister(baseline.name)
    clock.advance(WELL_PAST_THE_WINDOW)

    assert feed.is_on_probation
    # TICKS is a link-level capability: the stream really is attached, and D5.3
    # deliberately did not touch that branch — transport state answers transport
    # questions.
    ticks = manager.resolve_feed(
        Capability.TICKS, ResolutionContext(user_id="u1")
    )
    assert ticks.provider is feed, "a stale feed stopped being an attached stream"
    assert ticks.available


def test_probation_is_still_a_ranking_term_and_not_an_eligibility_filter():
    """D5.2's central invariant, re-proved after D5.3 changed what feeds it.

    Two equally-fresh feeds where one is stable and one is not: the stable one
    leads and the probationary one is *present behind it*, not missing.
    """
    registry, manager, baseline, stable, clock = _fixture()
    _make_stable(stable, clock)

    young = StreamingTickProvider("feed2:u1", owner_user_id="u1", clock=clock)
    registry.register(young)
    run(young.connect())
    run(young.subscribe(["RELIANCE"]))
    run(young.on_raw([_tick()]))

    chain = manager.failover_chain(Capability.QUOTES, _quote_ctx())
    assert chain[0] is stable
    assert young in chain, "probation removed a live feed from the chain"
    assert baseline in chain


# ==================================================================
# Isolation
# ==================================================================


def test_one_users_feed_going_stale_changes_nothing_for_anybody_else():
    """Rule 10. Staleness is per provider instance, and instances are per user."""
    clock = FakeClock()
    registry, manager, baseline, feed_a, _ = _fixture("userA", clock=clock)
    feed_b = StreamingTickProvider("feed:userB", owner_user_id="userB", clock=clock)
    registry.register(feed_b)
    run(feed_b.connect())
    run(feed_b.subscribe(["RELIANCE"]))

    _make_stable(feed_a, clock)
    _make_stable(feed_b, clock)

    # Only A goes quiet. B keeps ticking through the same wall of time.
    for _ in range(4):
        clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS * 0.5)
        run(feed_b.on_raw([_tick()]))

    assert feed_a.is_on_probation and not feed_b.is_on_probation
    assert _quote_provider(manager, "userA") is baseline
    assert _quote_provider(manager, "userB") is feed_b
    assert manager.status(user_id="userA")["tier"] == "delayed"
    assert manager.status(user_id="userB")["tier"] == "streaming"


def test_two_users_of_the_same_broker_go_stale_independently():
    """Rule 14, at its sharpest: same broker, same symbol, same clock.

    Nothing about staleness is keyed on the feed's *kind*, so two accounts on one
    broker are two provider instances with two independent evidence timestamps.
    A global — or per-broker — stale flag would fail this and nothing else.
    """
    clock = FakeClock()
    registry = ProviderRegistry()
    baseline = YahooPollingAdapter()
    registry.register(baseline)
    run(baseline.connect())
    manager = SourceManager(registry)

    feeds = {}
    for user in ("userA", "userB"):
        feed = StreamingTickProvider(f"zerodha:{user}", owner_user_id=user, clock=clock)
        registry.register(feed)
        run(feed.connect())
        run(feed.subscribe(["RELIANCE"]))
        _make_stable(feed, clock)
        feeds[user] = feed

    clock.advance(WELL_PAST_THE_WINDOW)
    run(feeds["userB"].on_raw([_tick()]))

    assert feeds["userA"].is_on_probation
    assert not feeds["userB"].is_on_probation
    assert _quote_provider(manager, "userA") is baseline
    assert _quote_provider(manager, "userB") is feeds["userB"]


def test_a_guest_baseline_is_untouched_by_any_feed_going_stale():
    """Rule 12/10 together: an unauthenticated visitor has no feed to lose.

    A guest resolves through the global context, which no owned feed is ever
    entitled to — so a stale feed must be neither a demotion nor a promotion for
    them, before or after decay.
    """
    _registry, manager, baseline, feed, clock = _fixture()
    assert manager.resolve(Capability.QUOTES) is baseline

    _make_stable(feed, clock)
    assert manager.resolve(Capability.QUOTES) is baseline, "a guest was served a user's feed"

    clock.advance(WELL_PAST_THE_WINDOW)
    assert manager.resolve(Capability.QUOTES) is baseline
    assert manager.status()["tier"] == "delayed"
    assert manager.status()["state"] == "available"


def test_a_stale_feed_is_still_refused_to_another_user():
    """Entitlement is the outermost filter and decay must not reorder it.

    Asserted in both directions: the wrong user is refused whether the feed is
    fresh or stale, so nothing about the new predicate can widen the scope.
    """
    _registry, _manager, _baseline, feed, clock = _fixture("userA")
    _make_stable(feed, clock)
    intruder = ResolutionContext(user_id="userB", symbol="RELIANCE").for_capability(
        Capability.QUOTES
    )
    assert not feed.is_eligible_for(intruder)

    clock.advance(WELL_PAST_THE_WINDOW)
    assert not feed.is_eligible_for(intruder)


# ==================================================================
# Multi-broker proof
# ==================================================================

STREAMING_BROKERS = ("zerodha", "upstox", "angelone", "fyers", "dhan", "nova")


@pytest.mark.parametrize("broker", STREAMING_BROKERS)
def test_stale_demotion_behaves_identically_for_every_broker_including_a_fictional_one(broker):
    """One parameterized test, driven through the real registration seam.

    The fictional broker is the load-bearing parameter: if decay consulted broker
    identity anywhere, a broker the code has never heard of would behave
    differently from the five it has. Nothing here varies but the name.
    """
    from services.brokers.market_feed import publish_market_ticks, set_market_feed_link
    from services.market_engine.providers import provider_registry

    with nova_registered(), _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        run(baseline.connect())
        manager = SourceManager(registry)

        name = run(_attach("u1", broker, ["RELIANCE"]))
        assert name, f"{broker} did not attach a market feed"
        feed = provider_registry.get(name)
        run(set_market_feed_link("u1", broker, up=True))

        # Drive this feed's own clock rather than sleeping: the seam builds the
        # provider, so the window is the published one and only the clock is
        # ours.
        clock = FakeClock(now=feed._clock())
        feed._clock = clock
        feed.probation_seconds = 0.0

        assert run(publish_market_ticks("u1", broker, [_tick()])) == 1
        assert feed.is_stable, broker
        assert _quote_provider(manager) is feed, broker

        clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)

        assert not feed.has_fresh_evidence, broker
        assert feed.is_on_probation, broker
        assert _quote_provider(manager) is baseline, broker
        assert manager.status(user_id="u1")["tier"] == "delayed", broker

        assert run(publish_market_ticks("u1", broker, [_tick()])) == 1
        assert _quote_provider(manager) is feed, broker


def test_the_stale_feed_layer_names_no_broker():
    """Rule 15, swept over the files D5.3 could have changed.

    Comment-inclusive, because a comment saying `# this broker needs 300s` in
    the generic layer is a design statement even while it is inert — and it is
    the form the executable sweep cannot see.
    """
    brokers = ("zerodha", "kite", "upstox", "angel", "angelone", "smartapi",
               "fyers", "dhan", "groww", "indmoney", "nova")
    for relative in ("services/market_engine/providers/streaming.py",
                     "services/market_engine/source_manager.py"):
        source = (BACKEND / relative).read_text().lower()
        for name in brokers:
            assert not re.search(rf"\b{re.escape(name)}\b", source), (
                f"{relative} names the broker {name!r} — staleness must not know "
                "whose feed it is demoting"
            )


def test_staleness_reuses_the_coverage_window_rather_than_defining_a_second_one():
    """ADR-043's central claim, pinned so a later 'tunable' cannot quietly split it.

    One staleness policy, asked per-instrument where an instrument was named and
    per-feed where none was. If someone adds a separate stale-feed constant, the
    two windows will drift and this goes red the moment they do.
    """
    feed = StreamingTickProvider("probe", tick_max_age_seconds=7.0, clock=FakeClock())
    clock = feed._clock
    run(feed.connect())
    run(feed.subscribe(["RELIANCE"]))
    run(feed.on_raw([_tick()]))

    clock.advance(6.9)
    assert feed.has_fresh_evidence and feed.covers("RELIANCE")
    clock.advance(0.2)
    assert not feed.has_fresh_evidence and not feed.covers("RELIANCE")


def test_transport_flap_history_is_not_consulted_by_provider_stability():
    """ADR-043 question D/E: the two layers stay separate.

    The Market Engine cannot import the broker layer, so `consecutive_short_
    connections` is not reachable from here by construction — but the property
    worth pinning is the stronger one: this provider's stability is a function of
    its tick evidence and nothing else. A feed whose link has flapped a hundred
    times and is now delivering is stable; a feed whose link has never flapped
    and is delivering nothing is not.
    """
    _registry, _manager, _baseline, feed, clock = _fixture()

    for _ in range(100):
        run(feed.mark_link_down("flap"))
        run(feed.mark_link_up())
    _make_stable(feed, clock)
    assert feed.is_stable, "flap history leaked into provider stability"

    never_flapped, _m, _b, quiet, quiet_clock = _fixture("userQ")
    _make_stable(quiet, quiet_clock)
    quiet_clock.advance(WELL_PAST_THE_WINDOW)
    assert quiet.is_on_probation, "an unflapped but silent link was called stable"


def test_the_admin_surface_reports_decay_and_the_consumer_surface_does_not():
    """Provenance stays where MARKET_DATA_ARCHITECTURE.md puts it.

    Diagnostics may say which feed decayed; `status()` may say only that the
    tier moved. A stale-feed indicator that leaked a provider name to the
    frontend would breach Developer Rule 4 while fixing a freshness bug.
    """
    _registry, manager, _baseline, feed, clock = _fixture()
    _make_stable(feed, clock)
    clock.advance(WELL_PAST_THE_WINDOW)

    assert feed.describe()["stability"] == "probation"
    assert feed.describe()["covered_symbols"] == 0

    status = manager.status(user_id="u1")
    blob = repr(status).lower()
    assert "feed:u1" not in blob and "stale" not in blob
    assert set(status) == {"state", "tier", "reason", "capabilities"}


# ==================================================================
# Falsification — each mutation below must turn a test above RED
# ==================================================================


def test_removing_stale_demotion_would_keep_a_dead_feed_preferred():
    """Mutation: `has_fresh_evidence` always True — i.e. D5.3 reverted."""
    _registry, manager, baseline, feed, clock = _fixture()
    _make_stable(feed, clock)
    clock.advance(WELL_PAST_THE_WINDOW)
    assert _quote_provider(manager) is baseline
    assert manager.status(user_id="u1")["tier"] == "delayed"

    with patch.object(
        StreamingTickProvider, "has_fresh_evidence", property(lambda self: True)
    ):
        assert feed.is_stable, "the mutation did not take"
        assert manager.status(user_id="u1")["tier"] == "streaming", (
            "with demotion removed the tier stayed correct — the control is not "
            "the thing producing it"
        )


def test_making_stale_coverage_permanent_would_strand_a_recovered_feed():
    """Mutation: staleness latches instead of being recomputed.

    The opposite failure, and the reason decay is derived rather than stored: a
    stored flag nothing clears turns a two-minute silence into a permanent
    demotion, and the user never returns to live data at all.
    """
    _registry, manager, _baseline, feed, clock = _fixture()
    _make_stable(feed, clock)
    clock.advance(WELL_PAST_THE_WINDOW)
    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager) is feed, "recovery is not automatic"

    with patch.object(
        StreamingTickProvider, "has_fresh_evidence", property(lambda self: False)
    ):
        assert _quote_provider(manager) is not feed


def test_using_wall_clock_time_would_be_visible_to_an_ntp_step():
    """Mutation: `_clock` is `time.time` rather than `time.monotonic`.

    A feed built on the wall clock is demoted or resurrected by a clock step it
    has no relationship to. Pinned as the default rather than by simulating a
    step, because the default is the thing a runtime feed actually gets.
    """
    assert StreamingTickProvider("probe")._clock is time.monotonic
    assert StreamingTickProvider("probe")._clock is not time.time


def test_making_a_socket_connection_count_as_evidence_would_promote_a_dead_feed():
    """Mutation: `_last_evidence_at` stamped on link-up instead of on a tick."""
    _registry, manager, baseline, feed, clock = _fixture()
    _make_stable(feed, clock)
    clock.advance(WELL_PAST_THE_WINDOW)

    run(feed.mark_link_up())
    assert not feed.has_fresh_evidence, "a reconnect refreshed the feed"
    assert _quote_provider(manager) is baseline

    # The mutation, applied by hand: stamp the clock the way a link-up would.
    feed._last_evidence_at = clock.now
    assert feed.has_fresh_evidence, "the mutation did not take"
    assert feed.readiness is not FeedReadiness.READY, (
        "socket-derived evidence alone reached READY — the readiness gate is not "
        "defending this on its own"
    )


def test_making_staleness_global_would_demote_every_users_feed_at_once():
    """Mutation: one shared stale flag rather than per-instance timestamps.

    Expressed as the state a global flag would produce, since the real
    implementation has no global to mutate — which is itself the point.
    """
    clock = FakeClock()
    registry, manager, _baseline, feed_a, _ = _fixture("userA", clock=clock)
    feed_b = StreamingTickProvider("feed:userB", owner_user_id="userB", clock=clock)
    registry.register(feed_b)
    run(feed_b.connect())
    run(feed_b.subscribe(["RELIANCE"]))
    _make_stable(feed_a, clock)
    _make_stable(feed_b, clock)

    clock.advance(WELL_PAST_THE_WINDOW)
    run(feed_b.on_raw([_tick()]))

    assert feed_a.is_on_probation and not feed_b.is_on_probation
    assert feed_a._last_evidence_at is not feed_b._last_evidence_at
    assert _quote_provider(manager, "userB") is feed_b


def test_breaking_yahoo_fallback_would_leave_a_stale_feed_users_with_nothing():
    """Mutation: the baseline released when a feed is promoted.

    The reason make-before-break and decay have to coexist: if promotion had
    ever unregistered the baseline, this demotion would resolve to nothing at
    all and a two-minute silence would become an outage.
    """
    registry, manager, baseline, feed, clock = _fixture()
    _make_stable(feed, clock)
    clock.advance(WELL_PAST_THE_WINDOW)
    assert manager.resolve_feed(Capability.QUOTES, _quote_ctx()).available

    registry.unregister(baseline.name)
    assert not manager.resolve_feed(Capability.QUOTES, _quote_ctx()).available, (
        "the resolution survived losing the baseline — this test is not "
        "measuring the fallback"
    )

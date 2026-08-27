"""Sprint D5.4 — provider delivery-latency scoring and selection (hermetic).

WHAT THIS FILE PINS
-------------------
The D5.4 audit found that the latency MARKET_DATA_ARCHITECTURE.md §7 describes —
`now - exchange_timestamp` — **cannot be measured by this platform at all.** The
document's own clause says "where the provider supplies an exchange timestamp",
and none does: `MarketTick` has no field for one, three of the five brokers put
none on the wire in the mode the platform subscribes, and the two that do use
different units on an unsynchronised exchange clock.

So D5.4 measures the one thing that *is* exact — how long a feed makes a consumer
wait between usable prices — and names it that. The rules, stated so they can be
falsified:

  * **delivery latency** = the median of the last `LATENCY_WINDOW_SAMPLES`
    intervals between accepted canonical batches, on the provider's own
    monotonic clock.
  * It is **established** only when the window is full *and* the feed has fresh
    evidence. Otherwise it is `None`, which is not zero and not an estimate.
  * `None` sorts **last within its own (health, probation) group** — never first,
    because the polled baseline can never establish a latency and "unknown wins"
    would have promoted it over every streaming feed.
  * It is the **third** element of the sort key, so it can never promote a
    probationary or a stale feed past a proven one, and it can never make a
    provider eligible or ready.
  * A **reconnect** discards it, with the ticks and the probation timestamps.

WHAT THIS FILE IS MOSTLY ABOUT NOT DOING
-----------------------------------------
A latency term is a ranking refinement bolted onto a chain of gates that already
work, so almost every way it can be wrong is a way of reaching past its own
place in the order. The tests below therefore spend more effort proving that
latency does *not* create eligibility, does *not* create readiness, does *not*
outvote probation or staleness, does *not* move Yahoo and does *not* leak
between users than they do proving that a faster feed wins.

The clock is injected and monotonic. No test sleeps, opens a socket, or reaches
a broker API. No real latency is measured and none is claimed.
"""

import math
import pathlib
import re
import statistics
import time

import pytest

from services.market_engine.providers import (
    DEFAULT_TICK_MAX_AGE_SECONDS,
    LATENCY_WINDOW_SAMPLES,
    PROBATION_WINDOW_SECONDS,
    Capability,
    FeedStability,
    MarketDataProvider,
    ProviderRegistry,
    ResolutionContext,
    StreamingTickProvider,
    YahooPollingAdapter,
)
from services.market_engine.source_manager import (
    LATENCY_RANK_UNKNOWN,
    SourceManager,
    _selection_rank,
)

# The D4/D5.2/D5.3 seam helpers, reused rather than re-implemented.
from tests.test_broker_streaming import (
    _attach,
    _clean_provider_registry,
    nova_registered,
    run,
)
from tests.test_provider_probation import FakeClock, _tick

BACKEND = pathlib.Path(__file__).resolve().parent.parent

#: A delivery cadence a real feed would be proud of, and one it would not.
FAST = 0.05
SLOW = 5.0


def _feed(user_id="u1", name=None, symbols=("RELIANCE",), clock=None, probation=0.0):
    """A connected, subscribed feed whose clock the test drives.

    `probation` defaults to zero because these tests are about the *third*
    ranking element and would otherwise spend every fixture re-proving the
    second. The tests that are specifically about the probation interaction set
    it back to the published window.
    """
    clock = clock or FakeClock()
    feed = StreamingTickProvider(
        name or f"feed:{user_id}",
        owner_user_id=user_id,
        probation_seconds=probation,
        clock=clock,
    )
    run(feed.connect())
    if symbols:
        run(feed.subscribe(symbols))
    return feed, clock


def _deliver(feed, clock, interval, count=1, symbol="RELIANCE"):
    """Advance `clock` by `interval` and push a batch, `count` times.

    Each call records exactly one delivery interval — which is the property
    `test_one_batch_records_one_interval_however_many_instruments_it_carries`
    exists to keep true.
    """
    for _ in range(count):
        clock.advance(interval)
        assert run(feed.on_raw([_tick(symbol)])) == 1


def _establish(feed, clock, interval, symbol="RELIANCE"):
    """Give `feed` a full, homogeneous latency window at `interval`."""
    # One extra batch: the first has no predecessor and so records no interval.
    _deliver(feed, clock, interval, count=LATENCY_WINDOW_SAMPLES + 1, symbol=symbol)
    assert feed.delivery_latency == pytest.approx(interval), (
        "fixture did not reach the state the test is about"
    )


def _registry_with(*providers, baseline=True):
    registry = ProviderRegistry()
    base = None
    if baseline:
        base = YahooPollingAdapter()
        registry.register(base)
        run(base.connect())
    for provider in providers:
        registry.register(provider)
    return registry, SourceManager(registry), base


def _chain(manager, user_id="u1", symbol="RELIANCE"):
    return manager.failover_chain(
        Capability.QUOTES,
        context=ResolutionContext(user_id=user_id, symbol=symbol),
    )


def _quote_provider(manager, user_id="u1", symbol="RELIANCE"):
    return manager.resolve(
        Capability.QUOTES, context=ResolutionContext(user_id=user_id, symbol=symbol)
    )


# ==================================================================
# 1-3. No evidence, first observation, warm-up
# ==================================================================


def test_a_feed_with_no_evidence_at_all_has_no_latency():
    """Brief case 1. Not zero, not an estimate — absent."""
    feed, _clock = _feed()
    assert feed.delivery_latency is None
    assert _selection_rank(feed)[2] == LATENCY_RANK_UNKNOWN


def test_the_first_delivery_records_no_interval_because_it_has_nothing_to_measure():
    """Brief case 2.

    The single most tempting bug in the whole sprint is measuring the first
    batch against `_ready_since`, or against zero, and calling the result a
    sample. There is no interval until a *second* delivery, and this is also
    what makes a reconnect discard cleanly rather than record the gap that
    spanned it.
    """
    feed, clock = _feed()
    run(feed.on_raw([_tick()]))
    assert len(feed._delivery_intervals) == 0
    assert feed.delivery_latency is None

    clock.advance(FAST)
    run(feed.on_raw([_tick()]))
    assert list(feed._delivery_intervals) == [pytest.approx(FAST)]


def test_latency_is_not_established_until_the_window_is_full():
    """Brief case 3, and Rule 8 — one lucky tick may not become a score.

    Asserted at every intermediate count rather than only at the boundary,
    because an off-by-one that established the score one sample early would
    satisfy a single check at the end.
    """
    feed, clock = _feed()
    run(feed.on_raw([_tick()]))

    for observed in range(1, LATENCY_WINDOW_SAMPLES):
        clock.advance(FAST)
        run(feed.on_raw([_tick()]))
        assert len(feed._delivery_intervals) == observed
        assert feed.delivery_latency is None, (
            f"latency was established on {observed} samples, before the window was full"
        )

    clock.advance(FAST)
    run(feed.on_raw([_tick()]))
    assert feed.delivery_latency == pytest.approx(FAST)


def test_a_single_fast_sample_never_becomes_the_score():
    """Rule 8, stated the way the brief states it.

    One extremely fast delivery on an otherwise slow feed must not produce a
    fast score — nor any score at all until the window fills, and then only the
    median, which the outlier cannot reach.
    """
    feed, clock = _feed()
    _deliver(feed, clock, SLOW, count=5)
    _deliver(feed, clock, 0.001, count=1)
    assert feed.delivery_latency is None

    _deliver(feed, clock, SLOW, count=5)
    assert feed.delivery_latency == pytest.approx(SLOW)


# ==================================================================
# 4-6. Established scores and ranking between equals
# ==================================================================


def test_a_stable_low_latency_feed_reports_its_cadence():
    """Brief case 4."""
    feed, clock = _feed()
    _establish(feed, clock, FAST)
    assert feed.delivery_latency == pytest.approx(FAST)
    assert feed.is_stable


def test_a_stable_high_latency_feed_reports_its_cadence_and_is_still_stable():
    """Brief case 5, and the point of the whole design.

    A slow feed is not a broken feed. It stays READY, stays STABLE, stays
    eligible and stays in the chain; the only thing latency does is decide which
    of two such feeds leads. Latency never makes a provider unavailable.
    """
    feed, clock = _feed()
    _establish(feed, clock, SLOW)
    assert feed.delivery_latency == pytest.approx(SLOW)
    assert feed.is_stable
    assert not feed.is_on_probation

    _registry, manager, _baseline = _registry_with(feed)
    assert _quote_provider(manager) is feed


def test_the_faster_of_two_otherwise_equivalent_feeds_leads_the_chain():
    """Brief case 6 — the property D5.4 exists to deliver.

    Both feeds are the same user's, both healthy, both ready, both fresh, both
    out of probation, both covering the instrument. Every ranking element above
    latency ties, so latency is the only thing that can be deciding this.
    """
    slow, slow_clock = _feed(name="feed:slow")
    fast, fast_clock = _feed(name="feed:fast")
    _establish(slow, slow_clock, SLOW)
    _establish(fast, fast_clock, FAST)

    # Registered slow-first, so a chain that merely preserved registration
    # order would fail this.
    _registry, manager, baseline = _registry_with(slow, fast)
    chain = _chain(manager)

    assert chain[0] is fast
    assert chain[1] is slow
    assert baseline in chain, "the baseline is still the floor beneath both"
    assert _quote_provider(manager) is fast


def test_ranking_follows_the_measurement_rather_than_registration_order():
    """The same assertion with the roles swapped, which is the control.

    Without this, a test that registered the fast feed second and asserted it
    won would pass equally well against an implementation that simply preferred
    the most recently registered provider.
    """
    for fast_first in (True, False):
        slow, slow_clock = _feed(name="feed:slow")
        fast, fast_clock = _feed(name="feed:fast")
        _establish(slow, slow_clock, SLOW)
        _establish(fast, fast_clock, FAST)
        order = (fast, slow) if fast_first else (slow, fast)
        _registry, manager, _baseline = _registry_with(*order)
        assert _chain(manager)[0] is fast, f"fast_first={fast_first}"


def test_a_single_feed_user_is_unaffected_by_the_latency_term():
    """The term must be inert when there is nothing to compare.

    A slow-but-sole feed keeps the quote and keeps the streaming tier: latency
    is a tie-break, and there is no tie.
    """
    feed, clock = _feed()
    _establish(feed, clock, SLOW * 10)
    _registry, manager, _baseline = _registry_with(feed)

    assert _quote_provider(manager) is feed
    assert manager.active_tier(Capability.QUOTES, user_id="u1").value == "streaming"
    assert manager.status(user_id="u1")["state"] == "available"


# ==================================================================
# 7-10. Latency may not reach past its place in the order
# ==================================================================


def test_latency_cannot_override_stale_feed_demotion():
    """Brief case 7, and the ADR-043 review question answered in the ranking.

    The stale feed's latency is *better* than the live one's, so an
    implementation that consulted latency before freshness would keep it first.
    """
    stale, stale_clock = _feed(name="feed:stale")
    live, live_clock = _feed(name="feed:live")
    _establish(stale, stale_clock, FAST)
    _establish(live, live_clock, SLOW)

    _registry, manager, baseline = _registry_with(stale, live)
    assert _chain(manager)[0] is stale, "fixture did not start in the state under test"

    stale_clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS * 10)

    assert stale.is_on_probation, "D5.3 demotion did not fire"
    assert _quote_provider(manager) is live
    assert baseline in _chain(manager)


def test_a_stale_feeds_historical_latency_is_not_reported_at_all():
    """The second, independent guard — and it is honesty, not redundancy.

    Ranking already demotes a stale feed via probation, so this could not change
    a selection on its own. It exists because a median assembled from gaps that
    all closed ten minutes ago is not a current measurement of anything, and
    `describe()` showing one to an operator would be a false reading.
    """
    feed, clock = _feed()
    _establish(feed, clock, FAST)

    clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)

    assert not feed.has_fresh_evidence
    assert feed.delivery_latency is None
    assert feed.describe()["delivery_latency_seconds"] is None
    assert _selection_rank(feed)[2] == LATENCY_RANK_UNKNOWN


def test_latency_cannot_promote_a_probationary_feed_over_a_proven_one():
    """Brief case 8, at the published probation window.

    The probationary feed is the faster of the two by two orders of magnitude.
    If latency were consulted before probation — or instead of it — it would
    lead. It must not.
    """
    proven, proven_clock = _feed(name="feed:proven", probation=PROBATION_WINDOW_SECONDS)
    young, young_clock = _feed(name="feed:young", probation=PROBATION_WINDOW_SECONDS)

    _deliver(proven, proven_clock, SLOW, count=1)
    proven_clock.advance(PROBATION_WINDOW_SECONDS)
    _deliver(proven, proven_clock, SLOW, count=LATENCY_WINDOW_SAMPLES + 1)
    assert proven.is_stable and proven.delivery_latency == pytest.approx(SLOW)

    _establish(young, young_clock, FAST)
    assert young.is_on_probation, "the young feed must still be unproven"
    assert young.delivery_latency == pytest.approx(FAST)

    _registry, manager, _baseline = _registry_with(young, proven)
    chain = _chain(manager)
    assert chain[0] is proven
    assert chain.index(young) > chain.index(proven)


def test_probation_outranks_latency_in_the_sort_key_itself():
    """The structural form of the same claim, read off the key.

    Asserted on the tuple rather than only through a resolution, because this is
    the property that makes the behaviour true *by construction* — there is no
    branch anywhere that says "check probation first", only the position of the
    element.
    """
    proven, proven_clock = _feed(name="feed:proven")
    young, _young_clock = _feed(name="feed:young")
    _establish(proven, proven_clock, SLOW)
    young._readiness = proven._readiness  # same readiness; only probation differs
    assert young.is_on_probation and not proven.is_on_probation

    assert _selection_rank(proven)[:2] < _selection_rank(young)[:2]
    assert _selection_rank(proven) < _selection_rank(young)


def test_latency_creates_no_eligibility():
    """Brief case 9.

    A feed with an excellent established latency that does not cover the
    instrument, is not entitled to the user, or has no link is still filtered
    out — latency operates on the survivors of those gates and can never add to
    them.
    """
    feed, clock = _feed(symbols=("RELIANCE",))
    _establish(feed, clock, FAST)
    assert feed.delivery_latency == pytest.approx(FAST)

    # Covers RELIANCE, not TCS.
    assert not feed.is_eligible_for(
        ResolutionContext(user_id="u1", symbol="TCS").for_capability(Capability.QUOTES)
    )
    # Another user's request, at the same excellent latency.
    assert not feed.is_eligible_for(
        ResolutionContext(user_id="u2", symbol="RELIANCE").for_capability(Capability.QUOTES)
    )
    # Link down: the evidence, and the latency with it, are discarded.
    run(feed.mark_link_down("gone"))
    assert feed.delivery_latency is None
    assert not feed.is_eligible_for(
        ResolutionContext(user_id="u1", symbol="RELIANCE").for_capability(Capability.QUOTES)
    )


def test_latency_creates_no_readiness():
    """Brief case 10.

    Readiness is one valid canonical tick on a live, subscribed link — D4.5,
    unchanged. Recording delivery intervals on a feed that never subscribed
    moves nothing.
    """
    feed, clock = _feed(symbols=None)
    assert not feed.is_ready

    for _ in range(LATENCY_WINDOW_SAMPLES + 5):
        clock.advance(FAST)
        run(feed.on_raw([_tick()]))

    assert not feed.is_ready, "delivery intervals must not open the readiness gate"
    assert feed.delivery_latency is None, "an unready feed reports no latency"
    assert feed.stability is FeedStability.PROBATION


def test_an_unready_feed_carries_no_latency_into_the_link_level_comparison():
    """The consequence of the readiness gate, found by writing case 10.

    A feed that connected but never subscribed can never serve a quote — but it
    *is* a candidate for the link-level TICKS capability, where readiness is not
    required. Without the readiness gate on `delivery_latency` it would carry a
    finite sort key into that comparison and could lead it on the strength of
    intervals measured on data the platform would never use.
    """
    unready, unready_clock = _feed(name="feed:unready", symbols=None)
    ready, ready_clock = _feed(name="feed:ready")
    for _ in range(LATENCY_WINDOW_SAMPLES + 1):
        unready_clock.advance(FAST / 10)
        run(unready.on_raw([_tick()]))
    _establish(ready, ready_clock, SLOW)

    assert unready.delivery_latency is None
    assert _selection_rank(unready)[2] == LATENCY_RANK_UNKNOWN
    assert _selection_rank(ready)[2] == pytest.approx(SLOW)

    registry, manager, _baseline = _registry_with(unready, ready)
    ticks_chain = manager.failover_chain(
        Capability.TICKS, context=ResolutionContext.for_user("u1")
    )
    assert ticks_chain[0] is ready, (
        "an unready feed led the link-level chain on a latency it should not have"
    )
    assert unready in ticks_chain, "and it is still a candidate — ranked, not filtered"


def test_latency_ranks_and_never_filters():
    """A slow feed is still a candidate, and is still the answer when it is the
    only one left. ADR-042's rule about probation, applied to the third term:
    a filter would trade a ranking blemish for an outage.
    """
    slow, slow_clock = _feed(name="feed:slow")
    fast, fast_clock = _feed(name="feed:fast")
    _establish(slow, slow_clock, SLOW * 100)
    _establish(fast, fast_clock, FAST)

    registry, manager, _baseline = _registry_with(slow, fast)
    assert slow in _chain(manager), "the slow feed was filtered out rather than ranked"

    registry.unregister(fast.name)
    assert _quote_provider(manager) is slow
    assert manager.active_tier(Capability.QUOTES, user_id="u1").value == "streaming"


# ==================================================================
# 11-14. Reconnect, recovery, ageing, outliers
# ==================================================================


def test_latency_does_not_survive_a_reconnect():
    """Brief case 11, and it is justified rather than assumed.

    Intervals measured on a link that no longer exists describe a connection the
    platform cannot ask anything of — the same argument D4.5 made for coverage
    and D5.2 for probation. It also disposes of a defect that would otherwise
    need its own guard, asserted below: the gap *spanning* the disconnection
    would be one enormous fictitious sample.
    """
    feed, clock = _feed()
    _establish(feed, clock, FAST)

    run(feed.mark_link_down("socket closed"))
    assert feed.delivery_latency is None
    assert len(feed._delivery_intervals) == 0

    clock.advance(600.0)  # a long outage
    run(feed.mark_link_up())
    _deliver(feed, clock, FAST, count=1)

    assert list(feed._delivery_intervals) == [], (
        "the first batch after a reconnect recorded the outage as a delivery interval"
    )

    _deliver(feed, clock, FAST, count=LATENCY_WINDOW_SAMPLES)
    assert feed.delivery_latency == pytest.approx(FAST), (
        "the new link's score is contaminated by the old link's outage"
    )


def test_a_disconnect_also_clears_the_window():
    """The other link-loss path. Both must clear, or one quietly covers for the
    other and removing either would leave the reset untestable."""
    feed, clock = _feed()
    _establish(feed, clock, FAST)
    run(feed.disconnect())
    assert len(feed._delivery_intervals) == 0
    assert feed.delivery_latency is None


def test_a_feed_recovers_its_score_after_a_slow_period():
    """Brief case 12. A bad ten minutes must not be a permanent sentence."""
    feed, clock = _feed()
    _establish(feed, clock, SLOW)
    assert feed.delivery_latency == pytest.approx(SLOW)

    _deliver(feed, clock, FAST, count=LATENCY_WINDOW_SAMPLES)
    assert feed.delivery_latency == pytest.approx(FAST), (
        "the window did not refill — old samples are not being evicted"
    )


def test_old_latency_cannot_dominate_indefinitely():
    """Brief case 13, in the direction that actually matters.

    A feed that *was* fast and is now slow must lose its advantage. Asserted
    sample by sample so the eviction is shown to be gradual and complete rather
    than assumed from the endpoints — and the majority crossing is where the
    median moves, which is the outlier tolerance working in reverse.
    """
    feed, clock = _feed()
    _establish(feed, clock, FAST)

    majority = LATENCY_WINDOW_SAMPLES // 2 + 1
    _deliver(feed, clock, SLOW, count=majority)
    assert feed.delivery_latency == pytest.approx(SLOW), (
        "a majority of slow samples did not move the median"
    )

    _deliver(feed, clock, SLOW, count=LATENCY_WINDOW_SAMPLES)
    assert list(feed._delivery_intervals) == [pytest.approx(SLOW)] * LATENCY_WINDOW_SAMPLES
    assert len(feed._delivery_intervals) == LATENCY_WINDOW_SAMPLES, (
        "the window is not bounded — old samples accumulate forever"
    )


def test_the_window_tolerates_a_minority_of_outliers():
    """Brief case 14, and the arithmetic reason `LATENCY_WINDOW_SAMPLES` is 9.

    A median of N tolerates floor((N-1)/2) outliers before the statistic itself
    becomes one. Asserted at exactly that count, and then at one more, so the
    test pins the tolerance rather than merely observing that some outliers are
    survivable.
    """
    tolerated = (LATENCY_WINDOW_SAMPLES - 1) // 2
    feed, clock = _feed()

    _deliver(feed, clock, FAST, count=1)
    _deliver(feed, clock, FAST, count=LATENCY_WINDOW_SAMPLES - tolerated)
    _deliver(feed, clock, 600.0, count=tolerated)

    assert feed.delivery_latency == pytest.approx(FAST), (
        f"{tolerated} outliers moved a median of {LATENCY_WINDOW_SAMPLES}"
    )

    _deliver(feed, clock, 600.0, count=1)
    assert feed.delivery_latency > FAST, (
        "a majority of outliers must move the median — otherwise the statistic "
        "is not measuring anything"
    )


def test_one_outlier_is_not_a_permanent_demotion():
    """The same rule at the ranking level, which is where it is felt.

    A feed that hiccups once and then delivers normally must not lose the
    primary position to a feed that is genuinely slower.
    """
    hiccup, hiccup_clock = _feed(name="feed:hiccup")
    steady, steady_clock = _feed(name="feed:steady")
    _establish(hiccup, hiccup_clock, FAST)
    _establish(steady, steady_clock, SLOW)

    _deliver(hiccup, hiccup_clock, 300.0, count=1)
    _deliver(hiccup, hiccup_clock, FAST, count=1)

    _registry, manager, _baseline = _registry_with(steady, hiccup)
    assert _chain(manager)[0] is hiccup


def test_one_batch_records_one_interval_however_many_instruments_it_carries():
    """A wide subscription is not a fast feed.

    Every tick in a batch is stamped with the same arrival instant, so counting
    per tick would record seven intervals of zero for one frame carrying eight
    instruments — and would score a feed as instantaneous for being subscribed
    to a lot of things.
    """
    symbols = ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ITC", "SBIN", "WIPRO", "AXISBANK")
    feed, clock = _feed(symbols=symbols)

    run(feed.on_raw([_tick(s) for s in symbols]))
    for _ in range(3):
        clock.advance(FAST)
        run(feed.on_raw([_tick(s) for s in symbols]))

    assert len(feed._delivery_intervals) == 3
    assert list(feed._delivery_intervals) == [pytest.approx(FAST)] * 3


def test_a_batch_that_is_entirely_rejected_records_no_interval():
    """Evidence means *accepted* canonical data, here as everywhere else.

    A feed delivering a shape this boundary does not recognise has demonstrated
    the opposite of a good delivery cadence; recording the attempt would let a
    broken feed score itself fast by failing quickly.
    """
    feed, clock = _feed()
    _deliver(feed, clock, FAST, count=2)
    before = list(feed._delivery_intervals)

    clock.advance(FAST)
    assert run(feed.on_raw([{"symbol": "RELIANCE", "not_a_tick_field": 1}])) == 0

    assert list(feed._delivery_intervals) == before


# ==================================================================
# 15-18. Isolation
# ==================================================================


def test_latency_state_is_per_provider_instance():
    """Brief case 15. Two feeds of one user measure themselves, not each other."""
    a, a_clock = _feed(name="feed:a")
    b, b_clock = _feed(name="feed:b")
    _establish(a, a_clock, FAST)

    assert a.delivery_latency == pytest.approx(FAST)
    assert b.delivery_latency is None, "a second feed inherited the first one's window"
    assert a._delivery_intervals is not b._delivery_intervals

    _establish(b, b_clock, SLOW)
    assert a.delivery_latency == pytest.approx(FAST), "b's samples reached a"
    assert b.delivery_latency == pytest.approx(SLOW)


def test_latency_state_does_not_cross_users():
    """Brief case 16, and Rule 11.

    Structural rather than enforced: the deque is an instance attribute and
    `feed_provider_name(user, broker)` gives one instance per pair, so sharing
    would require a module-level accumulator and none exists. Asserted anyway,
    because "there is nothing to leak through" is exactly the claim a later
    refactor would break silently.
    """
    a, a_clock = _feed(user_id="userA", name="feed:userA")
    b, b_clock = _feed(user_id="userB", name="feed:userB")
    _establish(a, a_clock, FAST)
    _deliver(b, b_clock, SLOW, count=2)

    assert b.delivery_latency is None, "userB's feed was established by userA's data"

    _registry, manager, _baseline = _registry_with(a, b)
    # userB cannot even see userA's feed, at any latency.
    assert a not in _chain(manager, user_id="userB")
    assert b not in _chain(manager, user_id="userA")


def test_two_users_on_one_broker_score_independently():
    """Brief case 17 — the case a per-broker accumulator would break.

    One user's slow session must not make another user's fast session of the
    same broker look slow, or vice versa. This is the mutation "share latency
    state across users" made observable.
    """
    fast_user, fast_clock = _feed(user_id="userA", name="market:acme:userA")
    slow_user, slow_clock = _feed(user_id="userB", name="market:acme:userB")
    _establish(fast_user, fast_clock, FAST)
    _establish(slow_user, slow_clock, SLOW)

    assert fast_user.delivery_latency == pytest.approx(FAST)
    assert slow_user.delivery_latency == pytest.approx(SLOW)

    _registry, manager, baseline = _registry_with(fast_user, slow_user)
    assert _quote_provider(manager, user_id="userA") is fast_user
    assert _quote_provider(manager, user_id="userB") is slow_user
    assert _quote_provider(manager, user_id="guest") is baseline


def test_a_guest_gets_the_baseline_and_no_latency_information():
    """Brief case 18. A user with no feed sees the delayed tier, as before."""
    feed, clock = _feed(user_id="u1")
    _establish(feed, clock, FAST)
    _registry, manager, baseline = _registry_with(feed)

    assert _quote_provider(manager, user_id="guest") is baseline
    assert manager.active_tier(Capability.QUOTES, user_id="guest").value == "delayed"
    assert manager.status(user_id="guest")["tier"] == "delayed"


# ==================================================================
# 19. Yahoo interaction — the near-miss the ADR records
# ==================================================================


def test_the_baseline_can_never_establish_a_delivery_latency():
    """The fact the whole unknown-ranks-last decision turns on.

    Yahoo is polled: the gateway decides when to ask it, so any interval
    measured would be the platform's own poll schedule read back rather than a
    property of the provider. The generic contract says `None` and the baseline
    inherits it.
    """
    baseline = YahooPollingAdapter()
    run(baseline.connect())
    assert baseline.delivery_latency is None
    assert MarketDataProvider.delivery_latency.fget(baseline) is None
    assert _selection_rank(baseline)[2] == LATENCY_RANK_UNKNOWN


def test_latency_never_promotes_the_baseline_over_a_streaming_feed():
    """The failure ranking unknown latency *first* would have caused.

    Yahoo and a stable streaming feed share a health rank and a probation rank,
    so the latency element decides between them. Had `None` sorted best, the
    permanent fallback would have displaced every live feed in the platform and
    D4.5 would have been silently undone. Asserted for an established feed and
    for one that has not established anything, because the second is the case
    where both keys are unknown and the stable sort must preserve priority.
    """
    for establish in (True, False):
        feed, clock = _feed()
        if establish:
            _establish(feed, clock, SLOW * 100)
        else:
            _deliver(feed, clock, SLOW, count=1)
        _registry, manager, baseline = _registry_with(feed)

        chain = _chain(manager)
        assert chain[0] is feed, f"establish={establish}: the baseline displaced the feed"
        assert baseline in chain
        assert manager.active_tier(Capability.QUOTES, user_id="u1").value == "streaming"


def test_the_baseline_remains_the_floor_at_every_point_of_the_latency_lifecycle():
    """Rule 14. Yahoo is available, connected and resolvable throughout."""
    feed, clock = _feed()
    registry, manager, baseline = _registry_with(feed)

    def _floor(stage):
        assert registry.get(baseline.name) is baseline, stage
        assert baseline.is_connected, stage
        assert baseline in _chain(manager), stage
        assert _quote_provider(manager, user_id="guest") is baseline, stage

    _floor("registered, no evidence")
    _deliver(feed, clock, FAST, count=1)
    _floor("first tick")
    _establish(feed, clock, FAST)
    _floor("latency established")
    clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS * 10)
    _floor("gone stale")
    assert _quote_provider(manager) is baseline, "the stale feed still holds the quote"
    _deliver(feed, clock, FAST, count=1)
    _floor("recovered")


# ==================================================================
# 20-21. All five real brokers, plus a fictional one
# ==================================================================

STREAMING_BROKERS = ("zerodha", "upstox", "angelone", "fyers", "dhan", "nova")


@pytest.mark.parametrize("broker", STREAMING_BROKERS)
def test_latency_behaves_identically_for_every_broker_including_a_fictional_one(broker):
    """Brief cases 20 and 21, through the real registration seam.

    The fictional broker is the load-bearing parameter: if latency consulted
    broker identity anywhere, a broker the code has never heard of would behave
    differently from the five it has. Nothing here varies but the name — and the
    provider is built by the seam, so the window is the published one and only
    the clock is the test's.
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

        clock = FakeClock(now=feed._clock())
        feed._clock = clock
        feed.probation_seconds = 0.0

        assert run(publish_market_ticks("u1", broker, [_tick()])) == 1
        assert feed.delivery_latency is None, broker

        for _ in range(LATENCY_WINDOW_SAMPLES):
            clock.advance(FAST)
            assert run(publish_market_ticks("u1", broker, [_tick()])) == 1

        assert feed.delivery_latency == pytest.approx(FAST), broker
        assert _quote_provider(manager) is feed, broker

        # And it resets on this broker's reconnect exactly as on every other's.
        run(set_market_feed_link("u1", broker, up=False, reason="drop"))
        assert feed.delivery_latency is None, broker
        assert _quote_provider(manager) is baseline, broker


def test_the_latency_layer_names_no_broker():
    """Rule 23 and Rule 4, swept over the files D5.4 changed.

    Comment-inclusive, because a comment saying `# this broker batches, allow
    500ms` in the generic layer is a design statement even while it is inert,
    and it is the form an identifier sweep cannot see.
    """
    brokers = ("zerodha", "kite", "upstox", "angel", "angelone", "smartapi",
               "fyers", "dhan", "groww", "indmoney", "nova")

    def _sweep(label, source):
        for name in brokers:
            assert not re.search(rf"\b{re.escape(name)}\b", source.lower()), (
                f"{label} names the broker {name!r} — latency must not know "
                "whose feed it is scoring"
            )

    for relative in ("services/market_engine/providers/streaming.py",
                     "services/market_engine/source_manager.py"):
        _sweep(relative, (BACKEND / relative).read_text())

    # `base.py` is swept over the D5.4 property alone rather than whole-file.
    # It is the adapter *contract*, and its module docstring has named example
    # adapters since D1 — a whole-file sweep here would either fail on prose
    # that predates this sprint or force that prose to be rewritten to keep a
    # D5.4 test green, which is a test dictating unrelated documentation. The
    # property is the surface D5.4 added and the only one it can breach.
    import inspect

    from services.market_engine.providers.base import MarketDataProvider

    _sweep("base.MarketDataProvider.delivery_latency",
           inspect.getsource(MarketDataProvider.delivery_latency.fget))


def test_the_market_engine_still_imports_no_broker_module():
    """Rule J: no transport seam was created, so this stays true."""
    for relative in ("services/market_engine/providers/streaming.py",
                     "services/market_engine/providers/base.py",
                     "services/market_engine/source_manager.py"):
        source = (BACKEND / relative).read_text()
        assert not re.search(r"^\s*(from|import)\s+services\.brokers", source, re.M), (
            f"{relative} imports the broker layer"
        )


# ==================================================================
# 22. Timing semantics
# ==================================================================


def test_the_clock_is_monotonic_by_default():
    """Rule: a duration measured on a clock an NTP step can move backwards would
    rank a provider on an artefact of time synchronisation."""
    assert StreamingTickProvider("probe")._clock is time.monotonic


def test_a_backwards_clock_never_produces_a_fast_sample():
    """The wall-clock mutation, made observable.

    Monotonic timing makes this unreachable in production, which is exactly why
    it is worth asserting: if the clock is ever swapped for one that can step
    backwards, a negative interval would be the *best possible* score and would
    hand the primary position to whichever feed happened to be measured across
    the step.
    """
    feed, clock = _feed()
    _deliver(feed, clock, FAST, count=3)
    before = list(feed._delivery_intervals)

    clock.advance(-60.0)
    run(feed.on_raw([_tick()]))

    assert list(feed._delivery_intervals) == before, "a negative interval was recorded"
    assert all(sample >= 0 for sample in feed._delivery_intervals)


def test_the_score_is_derived_on_read_and_never_cached():
    """No stored score, for D4.5's reason: a stored copy of something derivable
    disagrees with it exactly when it matters."""
    feed, clock = _feed()
    _establish(feed, clock, FAST)
    assert feed.delivery_latency == pytest.approx(FAST)

    clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)
    assert feed.delivery_latency is None, "the score was cached across the staleness edge"

    clock.advance(-(DEFAULT_TICK_MAX_AGE_SECONDS + 1))
    assert feed.delivery_latency == pytest.approx(FAST)


def test_the_score_is_the_median_and_not_the_mean():
    """The statistic itself, pinned. A mean of the same window differs, and an
    implementation that quietly switched would still pass every test whose
    samples are homogeneous — so this one deliberately is not."""
    feed, clock = _feed()
    intervals = [0.1, 0.1, 0.1, 0.1, 0.2, 0.3, 0.4, 5.0, 9.0]
    assert len(intervals) == LATENCY_WINDOW_SAMPLES
    run(feed.on_raw([_tick()]))
    for interval in intervals:
        _deliver(feed, clock, interval, count=1)

    assert feed.delivery_latency == pytest.approx(statistics.median(intervals))
    assert feed.delivery_latency != pytest.approx(statistics.mean(intervals))


def test_the_window_is_bounded_and_never_grows():
    """Rule: bounded state per feed. A deque without a maxlen would be an
    unbounded per-user accumulator on a stream that runs all day."""
    feed, clock = _feed()
    for _ in range(LATENCY_WINDOW_SAMPLES * 50):
        _deliver(feed, clock, FAST, count=1)
    assert feed._delivery_intervals.maxlen == LATENCY_WINDOW_SAMPLES
    assert len(feed._delivery_intervals) == LATENCY_WINDOW_SAMPLES


# ==================================================================
# 24. Security, isolation and leakage
# ==================================================================


def test_latency_reaches_no_consumer_payload():
    """Rule 24 and Developer Rule 4.

    `status()` is the shape that travels to the frontend and into the AI's
    freshness context. It carries a tier and no provider identity, and D5.4 adds
    nothing to it — a latency number there would be a provider-shaped fact on a
    consumer surface, and would differ per user in a way a shared cache would
    leak.
    """
    feed, clock = _feed()
    _establish(feed, clock, FAST)
    _registry, manager, _baseline = _registry_with(feed)

    for status in (manager.status(user_id="u1"), manager.status(user_id="guest")):
        blob = repr(status).lower()
        assert "latency" not in blob
        assert "delivery" not in blob
        assert feed.name.lower() not in blob
        assert set(status) == {"state", "tier", "reason", "capabilities"}


def test_the_resolution_status_shape_is_unchanged():
    """The other consumer-facing summary. D5.4 must not have widened it."""
    feed, clock = _feed()
    _establish(feed, clock, FAST)
    _registry, manager, _baseline = _registry_with(feed)
    resolution = manager.resolve_feed(
        Capability.QUOTES, ResolutionContext(user_id="u1", symbol="RELIANCE")
    )
    assert set(resolution.as_status()) == {"state", "tier", "reason"}
    assert "latency" not in repr(resolution.as_status()).lower()


def test_latency_is_visible_only_on_the_diagnostics_surface():
    """Where it *is* intentionally exposed, and in what form.

    `describe()` is the admin/diagnostics surface where provider names already
    live (D5.3's precedent). The unestablished case is `null`, never `0` — which
    would read as instantaneous delivery — and never the sort key's infinity,
    which is not JSON and would break a serializer the day an admin page fetched
    it.
    """
    import json

    feed, clock = _feed()
    assert feed.describe()["delivery_latency_seconds"] is None
    assert json.loads(json.dumps(feed.describe()))["delivery_latency_seconds"] is None

    _establish(feed, clock, FAST)
    described = feed.describe()
    assert described["delivery_latency_seconds"] == pytest.approx(FAST)
    assert not math.isinf(described["delivery_latency_seconds"])
    assert json.loads(json.dumps(described))["delivery_latency_seconds"] == pytest.approx(FAST)


def test_no_credential_or_wire_data_can_reach_the_latency_path():
    """The measurement's entire input, stated as a test.

    Two floats from one monotonic clock and the count of accepted records. It
    receives no payload, no token, no broker identity and no wire bytes, so
    there is nothing for it to leak — and a future change that fed it any of
    those would have to add a parameter this signature does not have.
    """
    import inspect

    signature = inspect.signature(StreamingTickProvider._record_delivery_interval)
    assert list(signature.parameters) == ["self", "arrived_at"]
    # `from __future__ import annotations` makes these strings, so compare as one.
    assert signature.parameters["arrived_at"].annotation in (float, "float")


def test_the_real_logging_stack_emits_no_latency_or_credential_detail(caplog):
    """Verified through the real logging stack rather than by reading the source.

    The only line D5.4 can emit is the backwards-clock warning, which names the
    provider (a log, where names are permitted) and no number, no payload and no
    credential. A full lifecycle is driven with logging captured at DEBUG.
    """
    import logging

    feed, clock = _feed()
    with caplog.at_level(logging.DEBUG):
        _establish(feed, clock, FAST)
        run(feed.mark_link_down("drop"))
        run(feed.mark_link_up())
        _establish(feed, clock, SLOW)
        clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS * 10)
        assert feed.delivery_latency is None

    emitted = "\n".join(record.getMessage() for record in caplog.records).lower()
    assert emitted, "nothing was logged — the assertions below would be vacuous"
    for forbidden in ("token", "access_token", "password", "secret", "authorization",
                      "api_key", "latency", "delivery interval"):
        assert forbidden not in emitted, f"the logging stack emitted {forbidden!r}"


def test_the_backwards_clock_warning_carries_the_name_and_nothing_else(caplog):
    """The one line D5.4 adds, checked for what it does and does not say."""
    import logging

    feed, clock = _feed()
    _deliver(feed, clock, FAST, count=2)
    with caplog.at_level(logging.WARNING):
        clock.advance(-60.0)
        run(feed.on_raw([_tick(price=1234.5)]))

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("clock move backwards" in w for w in warnings)
    assert not any("1234.5" in w for w in warnings), "a price reached the log line"

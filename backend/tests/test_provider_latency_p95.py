"""Sprint D5.9 — p95 delivery latency and latency inside `health()` (hermetic).

WHAT THIS FILE PINS
-------------------
D5.4 measured delivery latency and reported a median. ADR-044 recorded
LIM-D5.4-3 — no p95, and no latency in `health()` — and gave a reason for the
first half that this sprint had to test rather than inherit: *"p95 needs a sample
larger than any warm-up worth waiting through."*

The D5.9 audit found the reason is arithmetic and the conclusion was one step
short. With the nearest-rank method the p95 of N samples is the
`ceil(0.95 * N)`-th smallest, and that equals N for every N up to 19 — so a p95
of nine samples **is the maximum**, which is exactly the one-outlier sensitivity
`LATENCY_WINDOW_SAMPLES = 9` was chosen to avoid. `ceil(0.95 * N) < N` first
holds at **N = 20**. So the sample does have to grow, 20 is the smallest size at
which a p95 is a different statistic from a maximum, and that — not a judgement
call — is where `LATENCY_TAIL_WINDOW_SAMPLES` comes from.

The rules D5.9 adds, stated so they can be falsified:

  * **p95 delivery latency** = the `ceil(0.95 * 20)`-th = 19th smallest of the
    last `LATENCY_TAIL_WINDOW_SAMPLES` intervals between accepted canonical
    batches, on the provider's own monotonic clock. Nearest-rank: an observed
    interval, never an interpolation.
  * **One series, two windows.** The median reads the newest
    `LATENCY_WINDOW_SAMPLES` of the *same* deque. There is no second series, no
    second clock, no second recording site and no second reset.
  * **The D5.4 definition is unchanged.** `delivery_latency` is still the median
    of the last 9 intervals, still establishes at 9, and still ranks third.
  * **Each statistic's window is its own warm-up**, so a feed legitimately has a
    median and no p95 between the 9th and 20th interval.
  * **`health()` carries the cadence** as a `LatencyProfile` — established /
    p50 / p95 / sample count — with unknown as `None`, never `0` and never the
    `math.inf` sort key.
  * **p95 is reported, never ranked on.** `_selection_rank` is untouched.
  * **Latency stays local.** Only a pushed feed has a cadence, and a pushed
    feed's health is never shared (D5.8), so no latency figure can reach Redis.

WHAT THIS FILE IS MOSTLY ABOUT NOT DOING
-----------------------------------------
Like D5.4's, most of the effort here goes on proving the new number does *not*
reach past its own place: it does not enter the sort key, does not create
eligibility or readiness, does not survive a reconnect, does not outvote
probation or staleness, does not reach a consumer payload, does not reach the
shared health store, and does not become visible across users.

The clock is injected and monotonic. No test sleeps, opens a socket, or reaches
a broker API. No real latency is measured and none is claimed.
"""

import ast
import inspect
import math
import pathlib
import statistics

import pytest

from services.market_engine.providers import (
    DEFAULT_TICK_MAX_AGE_SECONDS,
    LATENCY_TAIL_PERCENTILE,
    LATENCY_TAIL_WINDOW_SAMPLES,
    LATENCY_WINDOW_SAMPLES,
    PROBATION_WINDOW_SECONDS,
    Capability,
    LatencyProfile,
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
from tests.test_broker_streaming import _clean_provider_registry, run  # noqa: F401
from services.market_engine.providers.streaming import _ShardEvidence
from tests.test_provider_latency import FAST, SLOW, _deliver, _feed, _intervals
from tests.test_provider_probation import FakeClock, _tick

BACKEND = pathlib.Path(__file__).resolve().parent.parent

#: The nearest-rank index the whole sprint turns on: 1-indexed into the sorted
#: window. Written out rather than imported so a mutation to the production
#: arithmetic cannot also move the expectation.
P95_RANK = 19


def _code_only(relative_path):
    """`relative_path`'s source with comments and docstrings removed.

    A structural sweep has to run over what the module *does*, not what it says
    about itself: every one of these modules documents the boundary it is being
    swept for, so a raw-text sweep is satisfied only by deleting the
    documentation. String constants that are not docstrings — the store's Lua
    scripts, dictionary keys — stay in scope, because those are code.
    """
    source = (BACKEND / relative_path).read_text()
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))

    class _Strip(ast.NodeTransformer):
        def visit_Constant(self, node):  # noqa: N802
            if id(node) in docstrings:
                return ast.Constant(value="")
            return node

    stripped = _Strip().visit(tree)
    # ast.unparse drops comments outright, which is the other half of the job.
    return ast.unparse(ast.fix_missing_locations(stripped))


def _establish_tail(feed, clock, interval, symbol="RELIANCE"):
    """Give `feed` a full, homogeneous *tail* window at `interval`."""
    # One extra batch: the first has no predecessor and so records no interval.
    _deliver(feed, clock, interval, count=LATENCY_TAIL_WINDOW_SAMPLES + 1, symbol=symbol)
    assert feed.delivery_latency_p95 == pytest.approx(interval), "fixture did not reach the state the test is about"


def _fill(feed, clock, intervals):
    """Deliver `intervals` in order onto a feed that has an opening batch."""
    run(feed.on_raw([_tick()]))
    for interval in intervals:
        _deliver(feed, clock, interval, count=1)


# ==================================================================
# 1. The sample size, and why it is twenty
# ==================================================================


def test_nine_samples_cannot_carry_a_p95_and_twenty_is_the_smallest_that_can():
    """The arithmetic that justifies the whole constant, asserted directly.

    If this ever fails, `LATENCY_TAIL_WINDOW_SAMPLES` has stopped being derived
    and has become a number somebody picked — which is the thing ADR-044 spent a
    paragraph refusing to do and ADR-049 claims not to have done.
    """
    degenerate = [n for n in range(1, 40) if math.ceil(LATENCY_TAIL_PERCENTILE * n) >= n]
    assert LATENCY_WINDOW_SAMPLES in degenerate, "a p95 over the median's window would be the maximum"
    assert LATENCY_TAIL_WINDOW_SAMPLES not in degenerate
    assert (
        LATENCY_TAIL_WINDOW_SAMPLES == max(degenerate) + 1
    ), "the tail window is no longer the smallest non-degenerate sample size"
    assert math.ceil(LATENCY_TAIL_PERCENTILE * LATENCY_TAIL_WINDOW_SAMPLES) == P95_RANK


def test_the_p95_is_nearest_rank_and_not_an_interpolation():
    """The percentile method itself, pinned on a window chosen so that every
    common alternative gives a different answer.

    Nearest-rank returns the 19th smallest of the 20. Linear interpolation
    between neighbours (numpy's default, and `statistics.quantiles`' inclusive
    method) lands between the 19th and the 20th and so returns neither. An
    implementation that quietly switched would still pass every test whose
    samples are homogeneous, so this window deliberately is not.
    """
    intervals = [0.10 * i for i in range(1, LATENCY_TAIL_WINDOW_SAMPLES)] + [90.0]
    assert len(intervals) == LATENCY_TAIL_WINDOW_SAMPLES
    feed, clock = _feed()
    _fill(feed, clock, intervals)

    expected = sorted(intervals)[P95_RANK - 1]
    assert feed.delivery_latency_p95 == pytest.approx(expected)
    # It is an interval this feed was actually observed to deliver.
    assert feed.delivery_latency_p95 in [pytest.approx(i) for i in intervals]
    # And it is not the maximum, which is the entire point of a 20-wide window.
    assert feed.delivery_latency_p95 != pytest.approx(max(intervals))
    # Nor either interpolating alternative.
    interpolated = statistics.quantiles(intervals, n=100, method="inclusive")[94]
    assert feed.delivery_latency_p95 != pytest.approx(interpolated)


def test_the_rank_is_rounded_up_which_only_a_second_window_can_show():
    """The `ceil` in the nearest-rank index, pinned where it is observable.

    Found by falsification: at the published window the rounding mode is
    **inert**, because `0.95 * 20` is exactly 19.0 and `floor` and `ceil` agree.
    A mutation swapping one for the other therefore changes nothing a test of
    the shipped configuration could see — which is not the same as the choice
    being arbitrary. `ceil` is what makes nearest-rank *nearest-rank* for a
    general window, and it is what
    `test_nine_samples_cannot_carry_a_p95_and_twenty_is_the_smallest_that_can`
    relies on when it derives the constant. So it is pinned at a window where
    the two differ — nine, where `ceil(8.55) = 9` and `floor(8.55) = 8` — by
    asking the shared helper directly for the statistic over that window.
    """
    intervals = [0.10 * i for i in range(1, LATENCY_TAIL_WINDOW_SAMPLES + 1)]
    feed, clock = _feed()
    _fill(feed, clock, intervals)

    newest_nine = intervals[-LATENCY_WINDOW_SAMPLES:]
    over_nine = feed._percentile_over(LATENCY_WINDOW_SAMPLES, statistic="p95")

    assert over_nine == pytest.approx(sorted(newest_nine)[math.ceil(0.95 * 9) - 1])
    assert over_nine != pytest.approx(
        sorted(newest_nine)[math.floor(0.95 * 9) - 1]
    ), "the nearest-rank index is rounding down"


def test_one_catastrophic_gap_cannot_become_the_reported_tail():
    """Why excluding exactly one sample is worth the wider window.

    A broker's midday hiccup is one enormous interval. It moves the maximum by
    definition; it must not move the *tail statistic*, or the p95 is a
    worst-case alarm wearing a percentile's name.
    """
    feed, clock = _feed()
    _fill(feed, clock, [FAST] * (LATENCY_TAIL_WINDOW_SAMPLES - 1) + [600.0])
    assert feed.delivery_latency_p95 == pytest.approx(FAST)
    assert feed.delivery_latency == pytest.approx(FAST)


def test_a_second_bad_gap_does_move_the_tail_because_it_is_no_longer_an_outlier():
    """The falsifying twin of the test above.

    An implementation that simply dropped the maximum and reported the rest, or
    that clamped outliers, would pass the previous test and fail this one. Two
    slow deliveries in twenty is a tail, and the statistic must say so.
    """
    feed, clock = _feed()
    _fill(feed, clock, [FAST] * (LATENCY_TAIL_WINDOW_SAMPLES - 2) + [SLOW, 600.0])
    assert feed.delivery_latency_p95 == pytest.approx(SLOW)


# ==================================================================
# 2. Establishment: two windows, one series
# ==================================================================


def test_the_p95_is_not_established_until_its_own_wider_window_is_full():
    """Asserted at every intermediate count, not only at the boundary, so an
    off-by-one that established it one sample early cannot survive."""
    feed, clock = _feed()
    run(feed.on_raw([_tick()]))

    for observed in range(1, LATENCY_TAIL_WINDOW_SAMPLES):
        clock.advance(FAST)
        run(feed.on_raw([_tick()]))
        assert len(_intervals(feed)) == observed
        assert (
            feed.delivery_latency_p95 is None
        ), f"a p95 was established on {observed} samples, before its window was full"

    clock.advance(FAST)
    run(feed.on_raw([_tick()]))
    assert feed.delivery_latency_p95 == pytest.approx(FAST)


def test_a_feed_legitimately_has_a_median_and_no_p95():
    """The state that exists *because* the two windows differ, held explicitly
    so nobody later 'fixes' it by collapsing the thresholds into one.

    Between the 9th and the 20th interval the selection metric is established
    and the tail is not, and `health()` has to be able to say that.
    """
    feed, clock = _feed()
    run(feed.on_raw([_tick()]))
    for _ in range(LATENCY_WINDOW_SAMPLES):
        _deliver(feed, clock, FAST, count=1)

    assert feed.delivery_latency == pytest.approx(FAST)
    assert feed.delivery_latency_p95 is None

    profile = feed.health().latency
    assert profile.established is True
    assert profile.p50_seconds == pytest.approx(FAST)
    assert profile.p95_seconds is None
    assert profile.samples == LATENCY_WINDOW_SAMPLES


def test_the_median_still_reads_only_the_newest_nine_of_the_wider_window():
    """The D5.4 definition, unchanged by D5.9 and pinned against the widening.

    This is the regression the sprint most plausibly introduces: widening the
    deque to twenty and leaving `statistics.median` pointed at the whole of it
    would silently redefine the platform's selection metric. The window here is
    built so the two answers differ.
    """
    old, new = [90.0] * (LATENCY_TAIL_WINDOW_SAMPLES - LATENCY_WINDOW_SAMPLES), [FAST] * LATENCY_WINDOW_SAMPLES
    feed, clock = _feed()
    _fill(feed, clock, old + new)

    assert feed.delivery_latency == pytest.approx(statistics.median(new))
    assert feed.delivery_latency != pytest.approx(statistics.median(old + new))
    # And the older samples are still retained — for the tail, which reads them.
    assert len(_intervals(feed)) == LATENCY_TAIL_WINDOW_SAMPLES
    assert feed.delivery_latency_p95 == pytest.approx(sorted(old + new)[P95_RANK - 1])


def test_there_is_exactly_one_interval_series_and_one_recording_site():
    """Rule 6 — no second latency clock, and rule: no second measurement system.

    Structural rather than behavioural: a second deque or a second `append`
    would be the way this sprint goes wrong without any single assertion above
    changing.
    """
    #: D5.10 moved the series onto `_ShardEvidence`, one per broker connection,
    #: so the structural rule is asserted across both classes: still one buffer
    #: type, still one `append`, still one `clear`. A second deque or a second
    #: recording site remains the way this goes wrong with no assertion above
    #: changing.
    source = inspect.getsource(StreamingTickProvider) + inspect.getsource(_ShardEvidence)
    assert source.count("deque(") == 1, "a second interval buffer appeared"
    assert source.count(".intervals.append") == 1, "delivery intervals are recorded in more than one place"
    assert source.count(".intervals.clear()") == 1, "delivery intervals are cleared in more than one place"


def test_the_p95_is_taken_on_the_same_monotonic_clock_and_reads_no_wall_time():
    """Rule 6, from the other side. The injected clock is the only time source;
    a p95 that reached for `time.time()` would be unfalsifiable in these tests
    and wrong in production."""
    feed, clock = _feed()
    _establish_tail(feed, clock, FAST)
    assert feed.delivery_latency_p95 == pytest.approx(FAST)

    module = pathlib.Path("services/market_engine/providers/streaming.py").read_text()
    assert "time.time()" not in module, "a wall clock entered the streaming provider"


# ==================================================================
# 3. Lapse: reconnect, readiness, freshness
# ==================================================================


def test_a_reconnect_discards_the_p95_with_everything_else():
    """D5.4's reconnect rule, extended to the wider window rather than given an
    exemption by it. Intervals measured on a link that no longer exists describe
    a connection the platform cannot ask anything of."""
    feed, clock = _feed()
    _establish_tail(feed, clock, FAST)
    assert feed.delivery_latency_p95 == pytest.approx(FAST)

    run(feed.disconnect())
    run(feed.connect())
    run(feed.subscribe(("RELIANCE",)))

    assert _intervals(feed) == pytest.approx([])
    assert feed.delivery_latency_p95 is None
    assert feed.delivery_latency is None
    assert feed.health().latency == LatencyProfile()


def test_the_gap_spanning_a_disconnection_is_never_recorded_as_a_sample():
    """The tail statistic makes this defect far more damaging than D5.4's median
    did — one fictitious 3600-second sample is exactly the kind of value a p95
    surfaces and a median hides — so it is re-pinned here rather than assumed."""
    feed, clock = _feed()
    _establish_tail(feed, clock, FAST)

    run(feed.disconnect())
    clock.advance(3600.0)
    run(feed.connect())
    run(feed.subscribe(("RELIANCE",)))
    _establish_tail(feed, clock, FAST)

    assert max(_intervals(feed)) == pytest.approx(FAST)
    assert feed.delivery_latency_p95 == pytest.approx(FAST)


def test_a_stale_feed_reports_no_p95():
    """The same third gate the median has, for the same reason: a tail assembled
    from gaps that all closed ten minutes ago is not a current measurement."""
    feed, clock = _feed()
    _establish_tail(feed, clock, FAST)

    clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)
    assert feed.delivery_latency_p95 is None
    assert feed.delivery_latency is None
    assert feed.health().latency.established is False
    # The samples are retained, not destroyed — staleness suspends the statistic
    # and a fresh delivery restores it. Only a reconnect discards.
    assert len(_intervals(feed)) == LATENCY_TAIL_WINDOW_SAMPLES


def test_an_unready_feed_reports_no_p95():
    """A feed that connected but never delivered cannot serve a quote, so a
    statistic over it measures nothing the platform would act on."""
    feed, _clock = _feed(symbols=())
    assert feed.is_ready is False
    assert feed.delivery_latency_p95 is None
    assert feed.health().latency == LatencyProfile()


# ==================================================================
# 4. health(): the contract change
# ==================================================================


def test_health_carries_the_full_three_state_cadence():
    """The D5.9 health contract, in one assertion per state it must distinguish."""
    feed, clock = _feed()

    cold = feed.health().latency
    assert (cold.established, cold.p50_seconds, cold.p95_seconds, cold.samples) == (
        False,
        None,
        None,
        0,
    )

    _establish_tail(feed, clock, FAST)
    warm = feed.health().latency
    assert warm.established is True
    assert warm.p50_seconds == pytest.approx(FAST)
    assert warm.p95_seconds == pytest.approx(FAST)
    assert warm.samples == LATENCY_TAIL_WINDOW_SAMPLES


def test_unknown_latency_is_none_and_never_zero_or_infinity():
    """Rule: do not encode unknown as 0, and do not expose the ranking sentinel.

    Zero would read as instantaneous delivery — the best possible feed — which
    is the inversion D5.4's `LATENCY_RANK_UNKNOWN` exists to prevent inside the
    sort key. Infinity is not JSON and belongs only to that comparison.
    """
    feed, _clock = _feed()
    payload = feed.health().as_dict()["latency"]

    assert payload == {
        "established": False,
        "p50_seconds": None,
        "p95_seconds": None,
        "samples": 0,
    }
    # The two *durations* are the ones that must never be 0 when unknown.
    # `samples` is a count and 0 is its truthful cold value.
    for key in ("p50_seconds", "p95_seconds"):
        assert payload[key] is None
        assert payload[key] != 0
        assert payload[key] != LATENCY_RANK_UNKNOWN
    assert payload["samples"] == 0
    assert "inf" not in repr(payload).lower()


def test_health_is_recomputed_on_every_read_and_never_caches_a_lapsed_cadence():
    """Why the profile is derived rather than stored.

    A stored block would keep reporting a cadence after the feed went stale,
    because nothing calls the provider to tell it so — a pushed feed makes no
    calls, which is ADR-044's original reason for keeping latency off the
    counters in the first place.
    """
    feed, clock = _feed()
    _establish_tail(feed, clock, FAST)
    assert feed.health().latency.established is True

    clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)
    assert feed.health().latency.established is False, "a lapsed cadence was cached"

    clock.advance(-(DEFAULT_TICK_MAX_AGE_SECONDS + 1))
    assert feed.health().latency.established is True


def test_a_polled_provider_has_an_empty_cadence_rather_than_a_fast_one():
    """The baseline is not fast and is not unknown-that-might-be-fast: it has no
    delivery event to time at all, which is the same statement `is_on_probation`
    makes with `False`."""
    yahoo = YahooPollingAdapter()
    profile = yahoo.health().latency
    assert profile == LatencyProfile()
    assert profile.p50_seconds is None and profile.p95_seconds is None
    assert yahoo.health().as_dict()["latency"]["samples"] == 0


def test_health_names_no_provider_broker_or_credential_and_no_raw_instant():
    """Rule: `health()` leaks no identity and no wire-level data.

    `describe()` is the surface that may carry a provider name; `health()` is
    not, and the cadence block must not have quietly made it one. A monotonic
    reading would be both an internal and — since these are process-uptime
    figures — a fingerprint.
    """
    feed, clock = _feed(user_id="u-secret", name="feed:zerodha:u-secret")
    _establish_tail(feed, clock, FAST)
    blob = repr(feed.health().as_dict()).lower()

    for forbidden in (
        "zerodha",
        "upstox",
        "angel",
        "fyers",
        "dhan",
        "u-secret",
        "token",
        "access_token",
        "api_key",
        "secret",
        "session",
        "feed:",
    ):
        assert forbidden not in blob, f"health() leaked {forbidden!r}"

    latency = feed.health().as_dict()["latency"]
    assert set(latency) == {"established", "p50_seconds", "p95_seconds", "samples"}
    # Every number is a duration or a count, and both are small. A monotonic
    # instant is process uptime and is orders of magnitude larger.
    assert latency["samples"] == LATENCY_TAIL_WINDOW_SAMPLES
    assert latency["p50_seconds"] == pytest.approx(FAST)
    assert latency["p95_seconds"] == pytest.approx(FAST)


def test_no_introspection_surface_exposes_a_raw_monotonic_instant():
    """Rule: no raw monotonic timestamps exposed — on `health()` *or* `describe()`.

    Found by falsification: the leak test above sweeps `health()`, and a
    monotonic instant added to `describe()` slipped past it. `describe()` is an
    admin surface and may carry a provider name, but a monotonic reading is
    process uptime — it is not a time anyone can interpret, it correlates
    workers, and it is the internal the cadence is derived *from*. `samples` is
    a count and durations are small; a monotonic instant on this machine is
    orders of magnitude larger, which is what makes this checkable.
    """
    # A clock started at a realistic process uptime, so a leaked instant is
    # unmistakably larger than any duration or count the payloads legitimately
    # carry. `FakeClock`'s 1_000.0 default sits too near the threshold below for
    # the distinction to be convincing.
    feed, clock = _feed(clock=FakeClock(1_000_000.0))
    _establish_tail(feed, clock, FAST)
    assert feed._last_evidence_at > 1_000_000.0

    for surface in (feed.describe(), feed.health().as_dict()):
        for key, value in surface.items():
            if isinstance(value, dict):
                values = list(value.values())
            else:
                values = [value]
            for item in values:
                if isinstance(item, bool) or not isinstance(item, (int, float)):
                    continue
                assert abs(item) < 1_000.0, (
                    f"{key} carries {item!r}, which is too large to be a duration "
                    f"or a count and looks like a raw clock reading"
                )


def test_status_and_describe_stay_the_separate_contracts_they_were():
    """Rule: do not casually merge `status()`, `health()` and `describe()`."""
    feed, clock = _feed()
    _establish_tail(feed, clock, FAST)
    registry = ProviderRegistry()
    registry.register(feed)
    manager = SourceManager(registry)

    status = manager.resolve_feed(
        Capability.QUOTES, context=ResolutionContext(user_id="u1", symbol="RELIANCE")
    ).as_status()
    assert set(status) == {"state", "tier", "reason"}, "the consumer payload grew a field"

    described = feed.describe()
    assert described["name"] == feed.name, "describe() is still the named surface"
    assert described["delivery_latency_p95_seconds"] == pytest.approx(FAST)
    assert described["health"]["latency"]["p95_seconds"] == pytest.approx(FAST)


# ==================================================================
# 5. Ranking: p95 is reported and never ranked on
# ==================================================================


def test_the_sort_key_is_still_three_elements_and_still_ends_at_the_median():
    """ADR-049's central scope decision, pinned structurally.

    Adding p95 as a fourth element would be the easy expansion, and nothing in
    the Phase-5 specification asks for it: §7's `f(..., p95_latency)` is a
    continuous score ADR-044 rejected wholesale in favour of this sort key.
    """
    feed, clock = _feed()
    # Deliberately a window in which the two statistics differ: the newest nine
    # are mostly fast so the median is FAST, while three slow tails put the 19th
    # of twenty at SLOW. A sort key that had switched to the p95 would show it.
    _fill(
        feed,
        clock,
        [FAST] * (LATENCY_TAIL_WINDOW_SAMPLES - 3) + [SLOW, SLOW, 600.0],
    )
    assert feed.delivery_latency == pytest.approx(FAST)
    assert feed.delivery_latency_p95 == pytest.approx(SLOW)

    rank = _selection_rank(feed)
    assert len(rank) == 3
    assert rank[2] == pytest.approx(feed.delivery_latency)
    assert rank[2] != pytest.approx(feed.delivery_latency_p95)

    source = inspect.getsource(_selection_rank)
    assert "p95" not in source, "the tail statistic entered the sort key"


def test_a_dreadful_p95_does_not_demote_a_feed_with_a_good_median():
    """The behavioural half of the rule above: a feed whose tail is awful but
    whose typical delivery is fast still wins on the metric selection uses."""
    steady, steady_clock = _feed(user_id="u1", name="steady")
    spiky, spiky_clock = _feed(user_id="u1", name="spiky")
    _fill(steady, steady_clock, [SLOW] * LATENCY_TAIL_WINDOW_SAMPLES)
    _fill(spiky, spiky_clock, [FAST] * (LATENCY_TAIL_WINDOW_SAMPLES - 2) + [600.0, 600.0])

    assert spiky.delivery_latency_p95 > steady.delivery_latency_p95
    assert sorted([steady, spiky], key=_selection_rank)[0] is spiky


def test_an_excellent_p95_cannot_lift_a_probationary_or_stale_feed():
    """Rules 10-12 and the D5.4 ordering guarantee, re-asserted against the new
    number: latency is the *third* element, so a provider that loses on
    probation loses before any cadence is compared."""
    proven, proven_clock = _feed(user_id="u1", name="proven", probation=0.0)
    young, young_clock = _feed(user_id="u1", name="young", probation=PROBATION_WINDOW_SECONDS)
    _fill(proven, proven_clock, [SLOW] * LATENCY_TAIL_WINDOW_SAMPLES)
    _fill(young, young_clock, [FAST] * LATENCY_TAIL_WINDOW_SAMPLES)

    assert young.is_on_probation is True
    assert young.delivery_latency_p95 < proven.delivery_latency_p95
    assert sorted([young, proven], key=_selection_rank)[0] is proven


def test_an_unknown_p95_never_beats_an_established_one_anywhere_it_is_compared():
    """The D5.4 Yahoo mistake, checked against the new field.

    The baseline can never establish either statistic. If unknown ranked best,
    it would displace every streaming feed — which is what `LATENCY_RANK_UNKNOWN`
    being infinity prevents, and D5.9 must not have opened a second door to it.
    """
    feed, clock = _feed()
    _establish_tail(feed, clock, SLOW)
    yahoo = YahooPollingAdapter()
    run(yahoo.connect())

    assert yahoo.health().latency.p95_seconds is None
    assert _selection_rank(yahoo)[2] == LATENCY_RANK_UNKNOWN
    assert _selection_rank(feed)[2] < _selection_rank(yahoo)[2]


# ==================================================================
# 6. D5.8 boundary, and isolation
# ==================================================================


def test_a_pushed_feeds_health_is_not_shared_so_no_cadence_can_reach_redis():
    """The D5.8 boundary, and the structural reason D5.9 needed no store change.

    Latency exists only on providers that are pushed into, and exactly those
    providers declare their health per-socket and unshareable. The two facts are
    the same fact, and together they mean there is no code path along which a
    latency figure could be written to or read from the shared record.
    """
    feed, clock = _feed()
    _establish_tail(feed, clock, FAST)
    assert feed.health_is_shared is False
    assert feed.health().latency.established is True

    assert YahooPollingAdapter().health_is_shared is True
    assert YahooPollingAdapter().health().latency == LatencyProfile()

    # Belt and braces: the shared store names the fields it carries, one by one,
    # and no cadence field is among them.
    #
    # Swept over *code* rather than raw text. `health_state.py`'s docstring
    # argues at length about why delivery-latency samples stay local, so a bare
    # substring sweep fails on the very prose that documents the boundary — and
    # a sweep that passed only because that prose was deleted would be worse
    # than no sweep. Comments and docstrings are stripped; the Lua scripts are
    # ordinary string constants and are deliberately still in scope.
    store = _code_only("infrastructure/health_state.py")
    for forbidden in ("latency", "p50", "p95", "delivery_interval"):
        assert forbidden not in store, f"the shared health store learned about {forbidden!r}"


def test_apply_shared_health_cannot_overwrite_a_locally_measured_cadence():
    """The mirror half. `apply_shared_health` adopts named counters; a cadence is
    local evidence and must survive a shared read untouched — otherwise a worker
    that read Redis would blank the latency of a socket it is holding.

    HOW THIS IS ACTUALLY GUARANTEED, recorded because falsification showed it.
    A mutation that makes `apply_shared_health` explicitly blank the profile
    leaves this test **green**, and that is not a gap: the profile is derived on
    every `health()` read rather than stored, so there is no durable field for a
    shared read to corrupt. The recomputation is the guarantee, and the mutation
    that removes *it* — caching the profile after the first read — is caught by
    `test_health_is_recomputed_on_every_read_and_never_caches_a_lapsed_cadence`.
    This test pins the observable property; that one pins the mechanism. Kept as
    defence in depth against a future change that stores the profile.
    """

    class _Shared:
        state = "degraded"
        consecutive_failures = 4
        total_calls = 10
        total_errors = 4
        total_empty = 0
        last_success_at = None
        last_error_at = None
        error_label = "Timeout"

    feed, clock = _feed()
    _establish_tail(feed, clock, FAST)
    before = feed.health().latency

    feed.apply_shared_health(_Shared())

    assert feed.health().latency == before
    assert feed.health().latency.p95_seconds == pytest.approx(FAST)


def test_reset_health_does_not_pretend_to_clear_a_cadence_it_does_not_own():
    """`reset_health` drops the counters. The intervals live in the provider that
    measured them and are dropped by `_discard_evidence`, so a fresh
    `ProviderHealth` must not be able to report a stale cadence either."""
    feed, clock = _feed()
    _establish_tail(feed, clock, FAST)
    feed.reset_health()

    assert feed.health().latency.p95_seconds == pytest.approx(
        FAST
    ), "resetting counters silently destroyed live link evidence"
    run(feed.disconnect())
    assert feed.health().latency == LatencyProfile()


def test_one_users_cadence_is_invisible_to_another_through_the_registry():
    """Per-user isolation, resolved through the registry — the arrangement
    ADR-040 found is the only one in which a scoping mistake is visible."""
    mine, my_clock = _feed(user_id="u1", name="feed:u1")
    theirs, their_clock = _feed(user_id="u2", name="feed:u2")
    _establish_tail(mine, my_clock, FAST)
    _establish_tail(theirs, their_clock, SLOW)

    registry = ProviderRegistry()
    for provider in (mine, theirs):
        registry.register(provider)
    manager = SourceManager(registry)

    chain = manager.failover_chain(
        Capability.QUOTES,
        context=ResolutionContext(user_id="u1", symbol="RELIANCE"),
    )
    assert theirs not in chain
    assert mine.health().latency.p95_seconds == pytest.approx(FAST)
    assert theirs.health().latency.p95_seconds == pytest.approx(SLOW)


def test_the_cadence_reaches_no_consumer_payload():
    """Developer Rule 4. `status()` travels to the frontend and into the AI's
    freshness context; it carries a tier and no provider-shaped fact, and D5.9
    adds nothing to it."""
    feed, clock = _feed()
    _establish_tail(feed, clock, FAST)
    registry = ProviderRegistry()
    registry.register(feed)

    status = (
        SourceManager(registry)
        .resolve_feed(Capability.QUOTES, context=ResolutionContext(user_id="u1", symbol="RELIANCE"))
        .as_status()
    )

    blob = repr(status).lower()
    for forbidden in ("latency", "p50", "p95", "sample", str(FAST)):
        assert forbidden not in blob


def test_resolve_feed_is_still_synchronous():
    """D5.8's contract pin, re-asserted because D5.9 touched the health path it
    runs on. A cadence that needed an await would have made a five-module
    contract awaitable for a diagnostic figure."""
    source = pathlib.Path("services/market_engine/source_manager.py").read_text()
    tree = ast.parse(source)
    found = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "resolve_feed"]
    assert not found, "resolve_feed became a coroutine"

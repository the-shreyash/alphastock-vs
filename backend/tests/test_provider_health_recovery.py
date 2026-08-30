"""Sprint D5.7 — health recovery: the ADR-029 deadlock (hermetic).

WHAT THIS FILE PINS
-------------------
ADR-029 recorded a cycle and deferred it to Phase 5; D5.6 classified it and
deliberately left it without a caller (LIM-D5.6-1)::

    health reaches DOWN → excluded from candidates_for → never selected
                        → never called → health never improves → DOWN forever

D5.7 supplies the caller, and the shape of the answer is the whole sprint:
MARKET_DATA_ARCHITECTURE.md's Resolution procedure, step 2, has always said
"filter out candidates whose health state is `down` **or that are inside a
failure cool-down**". D1 implemented the first half unconditionally and the
second half not at all, which is how a cool-down became a permanent exclusion.

The rules, stated so they can be falsified:

  * **A DOWN provider is excluded while its cool-down runs**, exactly as before.
  * **Once the cool-down has run it is re-admitted for one trial**, at the
    *tail* of the failover chain and nowhere else — `HEALTH_RANK` puts DOWN in
    the worst band and health is the first element of the selection key, so no
    probation state and no latency can lift it past a healthy or a probationary
    provider.
  * **Re-admission is not recovery.** Health is untouched by being re-admitted
    and stays DOWN until a real call succeeds. An *empty* success does not
    count, because it does not reset the failure streak either.
  * **The ladder is charged by evidence, not by the offer.** A provider offered
    at the tail and never reached costs nothing and stays offered; a provider
    that is reached and fails climbs one rung.
  * **Everything else is still asked.** Entitlement, capability, readiness and
    per-symbol coverage are evaluated for a re-admitted provider exactly as for
    any candidate, through the same single eligibility pass.
  * **Nothing that was unregistered can come back this way.** An entitlement
    refusal (D5.5) and an expired session both *unregister* the feed, so it is
    not a provider this mechanism can see. Their way back is D5.6's re-probe or
    a new session.

WHY SO MANY OF THESE TESTS ASSERT THAT SOMETHING STAYED EXCLUDED
-----------------------------------------------------------------
The failure mode this sprint can introduce is worse than the one it closes. The
DOWN filter exists so that a broken provider costs nothing; a re-admission that
is unpaced, that ranks anywhere but last, or that treats DOWN as UP turns a
silent exclusion into a timeout on every request — or, worse, puts a provider
known to be failing back in front of a working one. So the boundaries get more
tests than the recovery does.

No test sleeps on a cool-down, opens a socket, or reaches a broker API.
LIVE VALIDATION WAS NOT PERFORMED.
"""

import ast
import contextlib
import logging
import pathlib
import re
from unittest.mock import AsyncMock, patch

import pytest

from services.market_engine import gateway as gateway_module
from services.market_engine.gateway import MarketGateway
from services.market_engine.providers import (
    HEALTH_PROBE_BASE_DELAY,
    HEALTH_PROBE_MAX_DELAY,
    Capability,
    MarketDataProvider,
    ProviderHealthRecovery,
    ProviderKind,
    ProviderRegistry,
    ProviderState,
    ResolutionContext,
    SourceTier,
    StreamingTickProvider,
    YahooPollingAdapter,
)
from services.market_engine.providers.base import DOWN_AFTER_FAILURES
from services.market_engine.event_bus import event_bus
from services.market_engine.source_manager import (
    FEED_AVAILABLE,
    FEED_UNAVAILABLE,
    HEALTH_RANK,
    PROVIDER_STATUS_TOPIC,
    SourceManager,
    UnavailableReason,
)
from tests.test_market_gateway import RAW_YAHOO_QUOTE
from tests.test_broker_streaming import (
    _attach,
    _clean_provider_registry,
    nova_registered,
    run,
)
from tests.test_provider_probation import FakeClock, _tick

BACKEND = pathlib.Path(__file__).resolve().parent.parent

#: The generic modules D5.7 wrote or changed. Every sweep below reads exactly
#: this list, so a module added to the mechanism later is either added here or
#: is visibly missing.
D57_MODULES = (
    "services/market_engine/providers/health_recovery.py",
    "services/market_engine/providers/registry.py",
    "services/market_engine/source_manager.py",
    "services/market_engine/gateway.py",
)

#: Every broker that streams today, plus one that does not exist. The fictional
#: entry is the point: if health recovery depended on broker identity in any
#: way, a broker the code has never heard of would behave differently.
STREAMING_BROKERS = ("zerodha", "upstox", "angelone", "fyers", "dhan", "nova")


# ==================================================================
# Fixtures
# ==================================================================


class FlakyPollingProvider(MarketDataProvider):
    """A baseline-shaped polled provider whose failures a test can switch off.

    Polled on purpose. The deadlock is specific to a provider whose only
    evidence comes from being *called*: a pushed feed records a success from
    `_ingest_ticks` whenever a batch is accepted, so evidence reaches it without
    selection and the cycle never closes (ADR-044's property). The only polled
    provider the platform ships is the permanent baseline, which is why the
    untreated case was a total outage rather than a corner case.
    """

    kind = ProviderKind.POLLING
    tier = SourceTier.DELAYED
    normalizer_key = "yahoo"
    priority = 3
    capabilities = frozenset({Capability.QUOTES})

    def __init__(self, name="flaky_baseline", *, failing=True, owner_user_id=None):
        super().__init__()
        self.name = name
        self.owner_user_id = owner_user_id
        self.failing = failing
        self.calls = 0

    async def fetch_quote(self, symbol):
        self.calls += 1
        if self.failing:
            raise RuntimeError("upstream 503")
        return dict(RAW_YAHOO_QUOTE)


class HealthyPollingProvider(MarketDataProvider):
    """A steady polled provider — the thing a re-admitted one must never
    outrank."""

    kind = ProviderKind.POLLING
    tier = SourceTier.DELAYED
    normalizer_key = "yahoo"
    priority = 2
    capabilities = frozenset({Capability.QUOTES})

    def __init__(self, name="steady"):
        super().__init__()
        self.name = name
        self.calls = 0

    async def fetch_quote(self, symbol):
        self.calls += 1
        return dict(RAW_YAHOO_QUOTE)


@contextlib.contextmanager
def wired(*providers):
    """A real gateway over a private registry whose cool-down clock is a test's.

    The gateway is the real one and the bookkeeping path is the real one — every
    `record_failure` and `record_success` below travels the route a market
    request travels. Only the clock is injected, because a test that waits sixty
    seconds is a test nobody runs.
    """
    clock = FakeClock()
    registry = ProviderRegistry()
    manager = SourceManager(registry, health_recovery=ProviderHealthRecovery(clock=clock))
    gateway = MarketGateway()
    for provider in providers:
        registry.register(provider)
    with patch.object(gateway_module, "source_manager", manager):
        yield gateway, manager, registry, clock


def _quotes(manager, **ctx):
    return manager.resolve_feed(Capability.QUOTES, ResolutionContext(**ctx))


def _drive_to_down(gateway, provider, symbol="RELIANCE"):
    """Fail a provider out of the candidate list through the real gateway."""
    for _ in range(DOWN_AFTER_FAILURES):
        run(gateway.get_quote(symbol))
    assert provider.health().state is ProviderState.DOWN
    return provider


def _strip_comments(source):
    """Executable code only — string literals and comments removed."""
    tree = ast.parse(source)
    spans = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            spans.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    lines = []
    for number, line in enumerate(source.splitlines(), start=1):
        if number in spans:
            continue
        lines.append(re.sub(r"#.*$", "", line))
    return "\n".join(lines)


# ==================================================================
# 1. The transition, and the exclusion that used to be permanent
# ==================================================================


def test_a_provider_transitions_to_down_through_the_real_request_path():
    """Requirement 1. Eight consecutive failed calls, counted by the gateway."""
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, _manager, _registry, _clock):
        for index in range(1, DOWN_AFTER_FAILURES + 1):
            run(gateway.get_quote("RELIANCE"))
            expected = (
                ProviderState.DOWN if index >= DOWN_AFTER_FAILURES
                else ProviderState.DEGRADED if index >= 3
                else ProviderState.UNKNOWN
            )
            assert flaky.health().state is expected, index


def test_a_down_provider_is_excluded_from_normal_selection():
    """Requirement 2. Unchanged from D2, and unchanged by D5.7: inside the
    cool-down the provider is not a candidate and the feed is unavailable."""
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, _clock):
        _drive_to_down(gateway, flaky)

        resolution = _quotes(manager)
        assert not resolution.available
        assert resolution.reason is UnavailableReason.ALL_PROVIDERS_DOWN
        assert flaky not in resolution.chain


def test_a_down_provider_costs_no_further_calls_inside_its_cool_down():
    """The property the unconditional filter was protecting, kept.

    Re-admitting on every request would turn a silent exclusion into a timeout
    on each one, which is worse than the deadlock it fixes.
    """
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, _manager, _registry, _clock):
        _drive_to_down(gateway, flaky)
        calls_at_exclusion = flaky.calls

        for _ in range(20):
            run(gateway.get_quote("RELIANCE"))

        assert flaky.calls == calls_at_exclusion


# ==================================================================
# 2. The recovery path itself
# ==================================================================


def test_a_down_provider_is_re_admitted_once_its_cool_down_has_run():
    """Requirement 3. The deadlock, broken — by the resolution path that was
    already running, not by a schedule."""
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        assert not _quotes(manager).available

        clock.advance(HEALTH_PROBE_BASE_DELAY)

        resolution = _quotes(manager)
        assert resolution.available
        assert resolution.chain == (flaky,)


def test_re_admission_is_not_recovery_and_leaves_health_untouched():
    """Requirement: recovery must not mean "DOWN is treated as UP".

    Being offered a trial is not evidence of anything. Resolving a hundred
    times must not move a single counter.
    """
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        clock.advance(HEALTH_PROBE_BASE_DELAY)
        before = dict(flaky.health().as_dict())

        for _ in range(100):
            _quotes(manager)

        assert flaky.health().state is ProviderState.DOWN
        assert flaky.health().as_dict() == before


def test_a_successful_recovery_restores_health_and_clears_the_cool_down():
    """Requirement 4. One real success — the same evidence every provider has
    always needed — and nothing less."""
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        clock.advance(HEALTH_PROBE_BASE_DELAY)
        flaky.failing = False

        assert run(gateway.get_quote("RELIANCE")) is not None

        assert flaky.health().state is ProviderState.UP
        assert flaky.health().consecutive_failures == 0
        assert manager.health_recovery.probe_for(flaky) is None
        assert _quotes(manager).chain == (flaky,)


def test_a_failed_recovery_re_excludes_the_provider_and_doubles_the_wait():
    """Requirement 5. The trial is charged, and the next one costs longer."""
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        clock.advance(HEALTH_PROBE_BASE_DELAY)
        calls_before = flaky.calls

        run(gateway.get_quote("RELIANCE"))
        assert flaky.calls == calls_before + 1, "the trial was offered but never taken"

        assert not _quotes(manager).available, "a failed trial must re-exclude"
        clock.advance(HEALTH_PROBE_BASE_DELAY)
        assert not _quotes(manager).available, "the ladder did not climb"
        clock.advance(HEALTH_PROBE_BASE_DELAY)
        assert _quotes(manager).available


def test_the_cool_down_is_charged_by_evidence_and_never_by_the_offer():
    """A provider offered at the tail and never reached costs nothing.

    This is what makes the pacing exact rather than approximate: the ladder
    counts calls that actually happened, so a re-admission that a healthier
    provider made unnecessary neither costs the broken provider a rung nor
    consumes its trial.
    """
    steady, flaky = HealthyPollingProvider(), FlakyPollingProvider()
    with wired(steady, flaky) as (gateway, manager, _registry, clock):
        for _ in range(DOWN_AFTER_FAILURES):
            manager.record_failure(flaky, RuntimeError("upstream 503"))
        clock.advance(HEALTH_PROBE_BASE_DELAY)

        for _ in range(5):
            run(gateway.get_quote("RELIANCE"))

        assert flaky.calls == 0, "the steady provider answered; nothing reached the tail"
        assert manager.health_recovery.probe_for(flaky).attempts == 1
        assert _quotes(manager).chain == (steady, flaky), "still offered, still last"


def test_repeated_down_recovery_down_cycles_never_deadlock():
    """Requirement 6 and 7. Three full cycles, and the third recovers as the
    first did — no state accumulates that can wedge it shut."""
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        for cycle in range(3):
            flaky.failing = True
            while flaky.health().state is not ProviderState.DOWN:
                run(gateway.get_quote("RELIANCE"))
            assert not _quotes(manager).available, cycle

            # A first trial, taken and failed — so this cycle exercises a
            # re-admission that did *not* work as well as one that did. A
            # mechanism that only ever offers the first trial would pass a test
            # that recovered immediately and deadlock here.
            clock.advance(HEALTH_PROBE_BASE_DELAY)
            calls = flaky.calls
            run(gateway.get_quote("RELIANCE"))
            assert flaky.calls == calls + 1, cycle
            assert not _quotes(manager).available, cycle

            clock.advance(HEALTH_PROBE_MAX_DELAY)
            assert _quotes(manager).available, ("no second trial", cycle)
            flaky.failing = False
            assert run(gateway.get_quote("RELIANCE")) is not None, cycle
            assert flaky.health().state is ProviderState.UP, cycle
            assert manager.health_recovery.probe_for(flaky) is None, cycle


def test_the_ladder_is_bounded_and_never_stops_offering_a_trial():
    """Requirement: bounded pacing, and a bound that is a ceiling rather than a
    give-up. Nothing else in the platform will ever notice on its own that a
    polled baseline came back."""
    flaky = FlakyPollingProvider()
    with wired(flaky) as (_gateway, manager, _registry, clock):
        for _ in range(60):
            manager.record_failure(flaky, RuntimeError("upstream 503"))

        probe = manager.health_recovery.probe_for(flaky)
        assert probe.next_probe_at - clock.now == pytest.approx(HEALTH_PROBE_MAX_DELAY)
        clock.advance(HEALTH_PROBE_MAX_DELAY)
        assert _quotes(manager).available, "the ceiling must remain a ceiling"


# ==================================================================
# 3. Where a re-admitted provider is allowed to sit
# ==================================================================


def test_a_re_admitted_provider_never_outranks_a_healthy_one():
    """Requirement 10 of the brief, and the reason it is true by construction:
    health is the first element of the selection key and DOWN is its worst
    band."""
    steady, flaky = HealthyPollingProvider(), FlakyPollingProvider()
    with wired(steady, flaky) as (_gateway, manager, _registry, clock):
        for _ in range(DOWN_AFTER_FAILURES):
            manager.record_failure(flaky, RuntimeError("upstream 503"))
        clock.advance(HEALTH_PROBE_MAX_DELAY)

        chain = _quotes(manager).chain
        assert chain[0] is steady
        assert chain[-1] is flaky
        assert HEALTH_RANK[ProviderState.DOWN] > max(
            HEALTH_RANK[state] for state in ProviderState if state is not ProviderState.DOWN
        )


def test_a_re_admitted_provider_never_outranks_a_probationary_one():
    """Requirement: recovery does not bypass probation.

    A probationary feed has merely not yet proved itself; a DOWN one has proved
    the opposite. The first ranking term settles it before probation is read.
    """
    flaky = FlakyPollingProvider()
    clock, feed_clock = FakeClock(), FakeClock()
    registry = ProviderRegistry()
    manager = SourceManager(registry, health_recovery=ProviderHealthRecovery(clock=clock))
    feed = StreamingTickProvider("feed:u1", owner_user_id="u1", clock=feed_clock)
    registry.register(flaky)
    registry.register(feed)
    run(feed.connect())
    run(feed.subscribe(["RELIANCE"]))
    run(feed.on_raw([_tick()]))
    assert feed.is_ready and feed.is_on_probation

    for _ in range(DOWN_AFTER_FAILURES):
        manager.record_failure(flaky, RuntimeError("upstream 503"))
    clock.advance(HEALTH_PROBE_MAX_DELAY)

    chain = _quotes(manager, user_id="u1", symbol="RELIANCE").chain
    assert chain[0] is feed, "a probationary feed still beats a re-admitted one"
    assert chain[-1] is flaky


def test_health_recovery_restores_health_and_not_stability():
    """The other half of the same rule: recovering health gives a feed nothing
    it had not earned. A recovered provider that is on probation is still on
    probation, and still ranks behind a steady peer."""
    steady = HealthyPollingProvider()
    clock, feed_clock = FakeClock(), FakeClock()
    registry = ProviderRegistry()
    manager = SourceManager(registry, health_recovery=ProviderHealthRecovery(clock=clock))
    feed = StreamingTickProvider("feed:u1", owner_user_id="u1", clock=feed_clock)
    registry.register(steady)
    registry.register(feed)
    run(feed.connect())
    run(feed.subscribe(["RELIANCE"]))
    run(feed.on_raw([_tick()]))

    for _ in range(DOWN_AFTER_FAILURES):
        manager.record_failure(feed, RuntimeError("ws closed"))
    clock.advance(HEALTH_PROBE_BASE_DELAY)
    manager.record_success(feed)

    assert feed.health().state is ProviderState.UP
    assert feed.is_on_probation, "health recovery must not confer stability"
    chain = _quotes(manager, user_id="u1", symbol="RELIANCE").chain
    assert chain[0] is steady


def test_recovery_does_not_bypass_readiness():
    """Requirement: a re-admitted feed that cannot serve is still not eligible.

    Readiness and coverage are asked through the same single eligibility pass
    that `candidates_for` uses, so there is no route by which a probe reaches a
    provider a normal candidate could not.
    """
    clock, feed_clock = FakeClock(), FakeClock()
    registry = ProviderRegistry()
    manager = SourceManager(registry, health_recovery=ProviderHealthRecovery(clock=clock))
    feed = StreamingTickProvider("feed:u1", owner_user_id="u1", clock=feed_clock)
    registry.register(feed)
    run(feed.connect())
    run(feed.subscribe(["RELIANCE"]))
    run(feed.on_raw([_tick()]))

    for _ in range(DOWN_AFTER_FAILURES):
        manager.record_failure(feed, RuntimeError("ws closed"))
    run(feed.mark_link_down("socket closed"))
    clock.advance(HEALTH_PROBE_MAX_DELAY)

    assert not feed.is_ready
    resolution = _quotes(manager, user_id="u1", symbol="RELIANCE")
    assert feed not in resolution.chain
    assert registry.down_candidates_for(
        Capability.QUOTES, ResolutionContext(user_id="u1", symbol="RELIANCE")) == []

    # ...and the moment readiness returns, the trial is available again.
    run(feed.mark_link_up())
    run(feed.on_raw([_tick()]))
    assert feed in _quotes(manager, user_id="u1", symbol="RELIANCE").chain


def test_recovery_does_not_bypass_per_symbol_coverage():
    """The same pass, asked about an instrument the feed does not carry."""
    clock, feed_clock = FakeClock(), FakeClock()
    registry = ProviderRegistry()
    manager = SourceManager(registry, health_recovery=ProviderHealthRecovery(clock=clock))
    feed = StreamingTickProvider("feed:u1", owner_user_id="u1", clock=feed_clock)
    registry.register(feed)
    run(feed.connect())
    run(feed.subscribe(["RELIANCE"]))
    run(feed.on_raw([_tick()]))
    for _ in range(DOWN_AFTER_FAILURES):
        manager.record_failure(feed, RuntimeError("ws closed"))
    clock.advance(HEALTH_PROBE_MAX_DELAY)

    assert feed in _quotes(manager, user_id="u1", symbol="RELIANCE").chain
    assert feed not in _quotes(manager, user_id="u1", symbol="AAPL").chain


def test_stale_evidence_cannot_restore_health():
    """Requirement: an empty success is not evidence.

    A provider answering 200-with-no-data for every symbol is exactly the
    silently-empty feed `record_success(empty=True)` was written to keep
    visible. It must not clear the failure streak and it must not clear the
    cool-down.
    """
    flaky = FlakyPollingProvider()
    with wired(flaky) as (_gateway, manager, _registry, clock):
        for _ in range(DOWN_AFTER_FAILURES):
            manager.record_failure(flaky, RuntimeError("upstream 503"))
        clock.advance(HEALTH_PROBE_BASE_DELAY)

        manager.record_success(flaky, empty=True)

        assert flaky.health().state is ProviderState.DOWN
        assert manager.health_recovery.probe_for(flaky) is not None


def test_an_empty_answer_from_the_gateway_leaves_the_provider_down():
    """The same rule through the real request path rather than the seam."""
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        clock.advance(HEALTH_PROBE_BASE_DELAY)

        with patch.object(FlakyPollingProvider, "fetch_quote", return_value=None):
            run(gateway.get_quote("RELIANCE"))

        assert flaky.health().state is ProviderState.DOWN
        assert manager.health_recovery.probe_for(flaky) is not None


# ==================================================================
# 4. Isolation
# ==================================================================


def test_a_cool_down_is_not_shared_between_providers():
    """Two providers that went down at different instants wait different
    lengths. A register keyed by anything shared would release both at once."""
    early, late = FlakyPollingProvider("early"), FlakyPollingProvider("late")
    with wired(early, late) as (_gateway, manager, _registry, clock):
        for _ in range(DOWN_AFTER_FAILURES):
            manager.record_failure(early, RuntimeError("upstream 503"))
        clock.advance(HEALTH_PROBE_BASE_DELAY * 0.75)
        for _ in range(DOWN_AFTER_FAILURES):
            manager.record_failure(late, RuntimeError("upstream 503"))
        clock.advance(HEALTH_PROBE_BASE_DELAY * 0.5)

        chain = _quotes(manager).chain
        assert early in chain, "the earlier provider's cool-down has run"
        assert late not in chain, "the later provider's has not"


def test_a_cool_down_is_not_shared_between_users():
    """Per-user isolation is a property of the key, not of a naming convention.

    Two feeds that carry the *same* provider name for different owners are
    given deliberately here: today's naming makes that unreachable, and the
    isolation must not depend on that staying true.
    """
    recovery = ProviderHealthRecovery(clock=FakeClock())
    a = FlakyPollingProvider("brokerfeed:nova", owner_user_id="userA")
    b = FlakyPollingProvider("brokerfeed:nova", owner_user_id="userB")

    recovery.note_probe_failed(a)

    assert recovery.probe_for(a) is not None
    assert recovery.probe_for(b) is None, "user B inherited user A's cool-down"
    assert recovery.due_from([b]) == [], "and was armed on read, not released"


def test_one_users_down_feed_is_never_a_trial_for_another_user():
    """Entitlement is asked of a re-admitted provider exactly as of a candidate,
    so a per-user feed is invisible to everyone else on both paths."""
    clock = FakeClock()
    registry = ProviderRegistry()
    manager = SourceManager(registry, health_recovery=ProviderHealthRecovery(clock=clock))
    baseline = YahooPollingAdapter()
    registry.register(baseline)
    run(baseline.connect())
    feeds = {}
    feed_clock = FakeClock()
    for user in ("userA", "userB"):
        feed = StreamingTickProvider(f"feed:{user}", owner_user_id=user, clock=feed_clock)
        registry.register(feed)
        run(feed.connect())
        run(feed.subscribe(["RELIANCE"]))
        run(feed.on_raw([_tick()]))
        feeds[user] = feed

    for _ in range(DOWN_AFTER_FAILURES):
        manager.record_failure(feeds["userA"], RuntimeError("ws closed"))
    clock.advance(HEALTH_PROBE_MAX_DELAY)

    a_chain = _quotes(manager, user_id="userA", symbol="RELIANCE").chain
    b_chain = _quotes(manager, user_id="userB", symbol="RELIANCE").chain
    assert feeds["userA"] in a_chain and feeds["userA"] is a_chain[-1]
    assert feeds["userA"] not in b_chain
    # User B's feed is untouched: still a candidate, still healthy, and holding
    # nothing but the probation D5.2 gives every young feed. (It sits behind the
    # baseline for that reason and no other, which is why the assertion is about
    # membership and health rather than about position.)
    assert feeds["userB"] in b_chain, "user B's feed left the chain"
    assert feeds["userB"].health().state is ProviderState.UNKNOWN
    assert manager.health_recovery.probe_for(feeds["userB"]) is None


# ==================================================================
# 5. Yahoo
# ==================================================================


def test_yahoo_stays_registered_connected_and_serving_through_a_whole_cycle():
    """Requirement 9. Recovery never disconnects or suppresses the baseline —
    including when the baseline is the provider being recovered."""
    clock = FakeClock()
    registry = ProviderRegistry()
    manager = SourceManager(registry, health_recovery=ProviderHealthRecovery(clock=clock))
    baseline = YahooPollingAdapter()
    registry.register(baseline)
    run(baseline.connect())
    feed = StreamingTickProvider("feed:u1", owner_user_id="u1", clock=FakeClock())
    registry.register(feed)
    run(feed.connect())
    run(feed.subscribe(["RELIANCE"]))
    run(feed.on_raw([_tick()]))

    for stage in ("down", "cooling", "re-admitted", "recovered"):
        if stage == "down":
            for _ in range(DOWN_AFTER_FAILURES):
                manager.record_failure(feed, RuntimeError("ws closed"))
        elif stage == "re-admitted":
            clock.advance(HEALTH_PROBE_MAX_DELAY)
        elif stage == "recovered":
            manager.record_success(feed)

        assert baseline.name in registry, stage
        assert baseline.is_connected, stage
        assert baseline.health().state is not ProviderState.DOWN, stage
        assert baseline in _quotes(manager, user_id="u1", symbol="RELIANCE").chain, stage


def test_the_baseline_is_the_provider_the_deadlock_actually_stranded():
    """The case LIM-D5.6-1 names: the only polled provider, down, alone.

    Before D5.7 this was a total feed outage that survived until a process
    restart. It is the reason the mechanism exists, so it is asserted end to end
    through the real gateway and the real adapter class.
    """
    with wired() as (gateway, manager, registry, clock):
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        run(baseline.connect())
        with patch.object(YahooPollingAdapter, "fetch_quote",
                          side_effect=RuntimeError("upstream 503")):
            for _ in range(DOWN_AFTER_FAILURES):
                run(gateway.get_quote("RELIANCE"))
            assert manager.status()["state"] == "unavailable"

            clock.advance(HEALTH_PROBE_BASE_DELAY)
            assert manager.status()["state"] == "available"

        # The outage ends. Nothing tells the platform so; the next request does.
        with patch.object(YahooPollingAdapter, "fetch_quote",
                          new_callable=AsyncMock, return_value=dict(RAW_YAHOO_QUOTE)):
            assert run(gateway.get_quote("RELIANCE")) is not None
        assert baseline.health().state is ProviderState.UP
        assert manager.active_tier(Capability.QUOTES) is SourceTier.DELAYED


# ==================================================================
# 6. What health recovery may not reach
# ==================================================================


def test_an_entitlement_refused_feed_is_not_a_health_recovery_candidate():
    """Requirement: an entitlement refusal recovers through D5.6, never here.

    D5.5 *unregisters* the feed rather than demoting it, so it is not a provider
    this mechanism can see. Driven through the engine callback the transport
    actually invokes.
    """
    from services.broker_engine import BrokerEngine
    from services.brokers.market_feed import feed_provider_name
    from services.brokers.streaming import DEFAULT_STREAM_CHANNEL
    from tests._fakedb import FakeDB
    from tests.test_provider_recovery import clean_register

    engine = BrokerEngine()
    engine.configure(FakeDB())
    with nova_registered(), _clean_provider_registry() as registry, clean_register():
        run(_attach("u1", "nova", ["RELIANCE"]))
        feed = registry.get(feed_provider_name("u1", "nova"))
        assert feed is not None
        manager = SourceManager(registry, health_recovery=ProviderHealthRecovery())

        run(engine._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))

        assert registry.get(feed_provider_name("u1", "nova")) is None
        assert registry.down_candidates_for(
            Capability.QUOTES, ResolutionContext(user_id="u1", symbol="RELIANCE")) == []
        assert manager.health_recovery.probe_for(feed) is None


def test_an_expired_session_is_not_treated_as_ordinary_health_recovery():
    """Requirement: auth expiry needs a new session, never a re-admission."""
    from services.broker_engine import BrokerEngine
    from services.brokers.market_feed import feed_provider_name
    from services.brokers.streaming import DEFAULT_STREAM_CHANNEL
    from tests._fakedb import FakeDB
    from tests.test_provider_recovery import clean_register

    engine = BrokerEngine()
    engine.configure(FakeDB())
    with nova_registered(), _clean_provider_registry() as registry, clean_register():
        run(_attach("u1", "nova", ["RELIANCE"]))
        feed = registry.get(feed_provider_name("u1", "nova"))
        manager = SourceManager(registry, health_recovery=ProviderHealthRecovery())

        run(engine._on_stream_expired("u1", "nova", DEFAULT_STREAM_CHANNEL))

        assert registry.get(feed_provider_name("u1", "nova")) is None
        assert manager.health_recovery.probe_for(feed) is None


def test_unregistering_a_feed_drops_its_cool_down():
    """A re-attached feed is a new instance with fresh UNKNOWN health, so a
    surviving entry would be inert — but a map that grows one entry per feed
    that has ever been down for the life of the process is the trap
    `forget_user_status` already avoids."""
    clock = FakeClock()
    registry = ProviderRegistry()
    manager = SourceManager(registry, health_recovery=ProviderHealthRecovery(clock=clock))
    gateway = MarketGateway()
    feed = StreamingTickProvider("feed:u1", owner_user_id="u1", clock=clock)
    with patch.object(gateway_module, "source_manager", manager), \
            patch.object(gateway_module, "provider_registry", registry):
        run(gateway.register_streaming_provider(feed))
        for _ in range(DOWN_AFTER_FAILURES):
            manager.record_failure(feed, RuntimeError("ws closed"))
        assert manager.health_recovery.probe_for(feed) is not None

        run(gateway.unregister_streaming_provider("feed:u1"))

        assert manager.health_recovery.probe_for(feed) is None
        assert manager.health_recovery.describe() == []


def test_a_reconnected_feed_starts_from_no_cool_down_at_all():
    """Reconnect semantics: a fresh instance inherits nothing."""
    clock = FakeClock()
    recovery = ProviderHealthRecovery(clock=clock)
    first = StreamingTickProvider("feed:u1", owner_user_id="u1", clock=clock)
    recovery.note_probe_failed(first)
    recovery.note_probe_failed(first)
    assert recovery.probe_for(first).attempts == 2

    recovery.forget(first)
    second = StreamingTickProvider("feed:u1", owner_user_id="u1", clock=clock)
    assert recovery.probe_for(second) is None
    assert second.health().state is ProviderState.UNKNOWN


# ==================================================================
# 7. Broker neutrality and the D5.6 boundary
# ==================================================================


@pytest.mark.parametrize("broker", STREAMING_BROKERS)
def test_health_recovery_behaves_identically_for_every_broker(broker):
    """Requirement: all five shipped brokers plus a fictional one, through the
    real attach seam, with the only difference being the name on the feed."""
    from services.brokers.market_feed import publish_market_ticks, set_market_feed_link
    from services.market_engine.providers import provider_registry

    from services.market_engine.providers import streaming as streaming_module

    clock = FakeClock()
    with nova_registered(), _clean_provider_registry() as registry, \
            patch.object(streaming_module, "PROBATION_WINDOW_SECONDS", 0.0):
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        run(baseline.connect())
        manager = SourceManager(registry, health_recovery=ProviderHealthRecovery(clock=clock))

        name = run(_attach("u1", broker, ["RELIANCE"]))
        assert name, f"{broker} did not attach a market feed"
        feed = provider_registry.get(name)
        run(set_market_feed_link("u1", broker, up=True))
        assert run(publish_market_ticks("u1", broker, [_tick()])) == 1

        for _ in range(DOWN_AFTER_FAILURES):
            manager.record_failure(feed, RuntimeError("ws closed"))
        context = ResolutionContext(user_id="u1", symbol="RELIANCE")
        assert feed not in manager.resolve_feed(Capability.QUOTES, context).chain, broker
        assert baseline is manager.resolve(Capability.QUOTES, context=context), broker

        clock.advance(HEALTH_PROBE_BASE_DELAY)
        chain = manager.resolve_feed(Capability.QUOTES, context).chain
        assert chain[-1] is feed, broker
        assert chain[0] is baseline, broker

        manager.record_success(feed)
        assert manager.resolve(Capability.QUOTES, context=context) is feed, broker


#: The one broker-naming line in the modules this sprint touched, quoted so the
#: sweep below is an exemption of a known D1 sentence rather than a hole. It is
#: `registry.py`'s module docstring restating Developer Rule 9 ("adding Zerodha,
#: Upstox, or a licensed NSE feed must mean one adapter…"), it predates D5.7, and
#: rewriting it would be the unrelated refactoring the brief forbids.
PRE_EXISTING_BROKER_NAMING = (
    "per developer rule 9 in market_data_architecture.md, adding zerodha, upstox, or a"
)


def test_the_health_recovery_layer_names_no_broker():
    """Rule 1 of the D5 brief. Comment-inclusive, because a comment naming a
    broker in the generic layer is a design statement even when it is inert."""
    for relative in D57_MODULES:
        source = (BACKEND / relative).read_text().lower()
        exempted = source.count(PRE_EXISTING_BROKER_NAMING)
        assert exempted <= 1, f"{relative} grew a second copy of the exempted line"
        source = source.replace(PRE_EXISTING_BROKER_NAMING, "")
        for broker in ("zerodha", "kite", "upstox", "angel", "smartapi",
                       "fyers", "dhan", "nova", "groww", "indmoney"):
            assert broker not in source, f"{relative} names {broker}"


def test_health_recovery_contains_no_identity_branch():
    """A generic mechanism has nothing to branch on. An executable-code sweep
    for a comparison against any provider or broker identity."""
    source = _strip_comments(
        (BACKEND / "services/market_engine/providers/health_recovery.py").read_text())
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left, *node.comparators]:
            assert not (isinstance(operand, ast.Constant)
                        and isinstance(operand.value, str)
                        and operand.value), \
                f"health recovery compares against a name: {ast.unparse(node)}"


def test_the_two_recovery_mechanisms_share_no_constant():
    """Requirement 4 of the brief: D5.6's REPROBE and D5.7's cool-down must not
    collapse into one schedule.

    Different layers, different units, different questions — and, pinned here,
    different numbers, so a future edit that makes one equal the other has to
    decide to do so rather than drift into it.
    """
    from services.brokers.recovery import (
        REPROBE_SWEEP_INTERVAL,
        STILL_UNAVAILABLE_BASE_DELAY,
        STILL_UNAVAILABLE_MAX_DELAY,
    )

    # The ordering pin, in the form ADR-046 used for reconnect vs re-probe: the
    # *slowest* health cool-down is still faster than the *fastest* re-probe. A
    # DOWN provider is a machine-timescale condition this process can observe
    # ending; an unresolved entitlement is a human-timescale one it cannot.
    assert HEALTH_PROBE_MAX_DELAY < STILL_UNAVAILABLE_BASE_DELAY
    assert HEALTH_PROBE_BASE_DELAY < HEALTH_PROBE_MAX_DELAY

    # And no rung is shared, so the two ladders cannot collapse into one by an
    # edit that only looks like a tidy-up.
    assert {HEALTH_PROBE_BASE_DELAY, HEALTH_PROBE_MAX_DELAY}.isdisjoint(
        {STILL_UNAVAILABLE_BASE_DELAY, STILL_UNAVAILABLE_MAX_DELAY})

    # `REPROBE_SWEEP_INTERVAL` is deliberately NOT in that set, and the reason is
    # reported rather than hidden: it is numerically equal to
    # HEALTH_PROBE_BASE_DELAY today. It is a sweeper wake-up that performs no
    # I/O, not a delay, so the coincidence paces nothing and links nothing —
    # asserted here so the claim is checked rather than asserted in a comment.
    assert REPROBE_SWEEP_INTERVAL < STILL_UNAVAILABLE_BASE_DELAY


def test_health_recovery_introduces_no_task_timer_or_sweep():
    """Requirement 7 of the brief: no timer unless the audit proves one is
    needed, and it did not — the resolution path already runs on every
    request."""
    source = _strip_comments(
        (BACKEND / "services/market_engine/providers/health_recovery.py").read_text())
    for banned in ("asyncio", "create_task", "sleep", "Thread", "threading"):
        assert banned not in source, f"health recovery grew a schedule ({banned})"


# ==================================================================
# 8. Consumer surfaces and logs
# ==================================================================


def test_the_consumer_status_shape_is_unchanged_by_recovery():
    """Requirement: `provider.status` grows no recovery vocabulary.

    Asserted by exact key set at every stage of a cycle, so a field added for
    an operator's benefit cannot reach a consumer payload unnoticed.
    """
    flaky = FlakyPollingProvider()
    expected = {"state", "tier", "reason", "capabilities"}
    with wired(flaky) as (gateway, manager, _registry, clock):
        assert set(manager.status()) == expected
        _drive_to_down(gateway, flaky)
        assert set(manager.status()) == expected
        clock.advance(HEALTH_PROBE_BASE_DELAY)
        assert set(manager.status()) == expected
        flaky.failing = False
        run(gateway.get_quote("RELIANCE"))
        assert set(manager.status()) == expected


def test_recovery_state_is_visible_only_on_the_admin_diagnostics_surface():
    """Provider names and cool-downs already have exactly one legitimate home."""
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, _clock):
        _drive_to_down(gateway, flaky)

        diagnostics = manager.diagnostics()
        assert diagnostics["health_recovery"], "an operator cannot see the exclusion"
        entry = diagnostics["health_recovery"][0]
        assert entry["provider"] == "flaky_baseline"
        assert entry["due_in_seconds"] > 0
        assert "flaky_baseline" not in str(manager.status())


def test_no_credentials_or_broker_vocabulary_reach_the_logs_at_debug():
    """Requirement: the real logging stack, at DEBUG, with live-looking secrets.

    The cool-down is driven by a broker feed whose credentials are present in
    this process, so the question is not whether the module formats them — it is
    whether anything on the path it runs writes one.
    """
    from services.brokers.market_feed import publish_market_ticks, set_market_feed_link
    from services.market_engine.providers import provider_registry

    secrets = ("eyJhbGciOiJIUzI1NiJ9.live-access-token.sIgNaTuRe",
               "nova-api-key-9f3c2a", "s3cr3t-refresh-token")
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = _Capture()
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        clock = FakeClock()
        with nova_registered(), _clean_provider_registry() as registry:
            registry.clear()
            baseline = YahooPollingAdapter()
            registry.register(baseline)
            run(baseline.connect())
            manager = SourceManager(
                registry, health_recovery=ProviderHealthRecovery(clock=clock))
            name = run(_attach("u1", "nova", ["RELIANCE"]))
            feed = provider_registry.get(name)
            feed._credentials = {"access_token": secrets[0], "api_key": secrets[1],
                                 "refresh_token": secrets[2]}
            run(set_market_feed_link("u1", "nova", up=True))
            run(publish_market_ticks("u1", "nova", [_tick()]))

            for _ in range(DOWN_AFTER_FAILURES):
                manager.record_failure(feed, RuntimeError(f"handshake failed: {secrets[0]}"))
            clock.advance(HEALTH_PROBE_BASE_DELAY)
            manager.resolve_feed(
                Capability.QUOTES, ResolutionContext(user_id="u1", symbol="RELIANCE"))
            manager.record_failure(feed, RuntimeError("still refused"))
            manager.record_success(feed)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)

    assert records, "the logging stack captured nothing — the test proves nothing"
    ours = [line for line in records if "health_recovery" in line or "source_manager" in line]
    assert ours, "the mechanism logged nothing at all"
    for secret in secrets:
        assert secret not in "\n".join(ours), "a credential reached the recovery logs"
    assert "handshake failed" not in "\n".join(ours), \
        "a provider's error text was replayed into the recovery log"


def test_the_re_admission_decision_is_logged_for_an_operator():
    """The exclusion was invisible before D5.7; an operator must be able to see
    that a provider is waiting rather than gone."""
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture()
    logger = logging.getLogger("services.market_engine.providers.health_recovery")
    logger.addHandler(handler)
    flaky = FlakyPollingProvider()
    try:
        with wired(flaky) as (gateway, _manager, _registry, clock):
            _drive_to_down(gateway, flaky)
            excluded = list(records)
            clock.advance(HEALTH_PROBE_BASE_DELAY)
            run(gateway.get_quote("RELIANCE"))
    finally:
        logger.removeHandler(handler)

    assert any("flaky_baseline" in message and "down" in message for message in excluded), \
        "an operator cannot see that the provider was excluded, or for how long"
    assert any("stays down after probe" in message for message in records), \
        "an operator cannot see that a trial was made and failed"


# ==================================================================
# 9. D5.12 — what the consumer sees while a trial is merely offered
# ==================================================================
#
# The D5.12 audit re-verified this whole mechanism and found the deadlock
# closed, so it added no mechanism. It did find one consequence of D5.7 that no
# document recorded and no test pinned, and these two tests pin it exactly as it
# behaves today so that changing it later has to be a decision:
#
# `status()` resolves through the same path a request does, and a re-admitted
# provider *is* a resolvable candidate. So during a sustained total outage the
# consumer-facing feed state flips to `available` the moment a cool-down
# expires — before anything has answered — and back to `unavailable` once the
# trial is spent and fails. It is a blink, not a lie about data (no price is
# fabricated and no tier is invented), and it is the honest report of "there is
# a provider to try". It is recorded as LIM-D5.12-1 because the alternative —
# having `status()` ignore probes — makes the consumer surface disagree with
# the resolution path, which is a worse property to hold. See ADR-052.


def test_the_feed_reports_available_while_a_trial_is_only_offered():
    """LIM-D5.12-1, first half: the flip is real and is not a data claim.

    Nothing has recovered at this point — health is still DOWN and the provider
    has answered nothing. What `available` means here is "a provider will be
    tried", which is what the resolution path itself means by it.
    """
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        assert manager.status()["state"] == FEED_UNAVAILABLE
        assert manager.status()["reason"] == UnavailableReason.ALL_PROVIDERS_DOWN.value

        clock.advance(HEALTH_PROBE_BASE_DELAY)
        during_trial = manager.status()
        assert during_trial["state"] == FEED_AVAILABLE
        assert during_trial["reason"] is None
        # ...and none of that is a claim that the provider recovered.
        assert flaky.health().state is ProviderState.DOWN
        assert flaky.calls == DOWN_AFTER_FAILURES, "nothing was called for the status"
        # The payload still carries no recovery vocabulary and no provider name.
        assert set(during_trial) == {"state", "tier", "reason", "capabilities"}
        assert "flaky_baseline" not in str(during_trial)


def test_a_failed_trial_publishes_an_available_then_unavailable_pair():
    """LIM-D5.12-1, second half: the blink is observable on the consumer bus.

    Only when something publishes status inside the window between the cool-down
    expiring and the trial being spent — the `/market/status` route, a broker
    connect, an unregister. Pinned so that a sprint which owns the consumer
    surface (the one that also owes LIM-D5.5-2) can see exactly what it is
    changing, and so that the pair can never grow into a repeating flap.
    """
    seen = []

    async def spy(event):
        seen.append(event["data"]["state"])

    event_bus.subscribe(PROVIDER_STATUS_TOPIC, spy)
    flaky = FlakyPollingProvider()
    try:
        with wired(flaky) as (gateway, manager, _registry, clock):
            _drive_to_down(gateway, flaky)
            seen.clear()

            clock.advance(HEALTH_PROBE_BASE_DELAY)
            run(manager.publish_status())
            run(gateway.get_quote("RELIANCE"))
            run(manager.publish_status())
            assert seen == [FEED_AVAILABLE, FEED_UNAVAILABLE]

            # Bounded, and that is the property that matters: the second
            # cool-down is twice as long, so the blink cannot become a flap.
            seen.clear()
            clock.advance(HEALTH_PROBE_BASE_DELAY)
            run(manager.publish_status())
            assert seen == [], "the ladder did not climb; the blink repeated"
    finally:
        event_bus.unsubscribe(PROVIDER_STATUS_TOPIC, spy)

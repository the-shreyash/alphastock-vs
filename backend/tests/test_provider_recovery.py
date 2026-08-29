"""Sprint D5.6 — generalized provider re-probe and safe recovery (hermetic).

WHAT THIS FILE PINS
-------------------
D5.5 made an entitlement refusal terminal, which was right, and left it a
one-way door, which was recorded as LIM-D5.5-3. ADR-029 had already recorded the
same shape for a demoted provider two sprints earlier::

    provider withdrawn → nothing attaches it → no evidence → no recovery

D5.6 answers both as one question. The rules it establishes, stated so they can
be falsified:

  * **Recovery is classified, never generic.** Five classes, and the reason
    there are five is that collapsing them is the exact defect D5.5 found one
    layer down. Only `REPROBE` is ever retried on a schedule; `TRANSPORT` and
    `EVIDENCE` already heal themselves and are refused registration outright;
    `SESSION` and `CONFIGURATION` are recorded and never re-probed.
  * **A re-probe is one ordinary attach.** There is no control-plane probe and
    no new adapter method. A recovered feed travels the identical route a first
    attachment does, so it must still earn READY, must still serve its probation
    window, and inherits neither readiness, nor stability, nor latency evidence
    from the connection that was refused.
  * **It is paced by its own ladder.** Five minutes, doubling, capped at an
    hour, jittered — a *provider-level* schedule, never the reconnect ladder,
    which is measured in seconds because it is about a socket.
  * **A dead session is never re-probed**, and it is excluded twice by two
    guards that catch different facts.
  * **Recovery is per (user, broker, channel)**, structurally, and Yahoo is
    untouched throughout.

WHY SO MANY OF THESE TESTS ASSERT THAT NOTHING HAPPENED
--------------------------------------------------------
The failure mode this sprint can introduce is worse than the one it fixes. A
withdrawal that never recovers costs one user one tier; a re-probe that fires
too often, on a dead credential, across users, or into a healthy connection is a
storm against a broker that has already said stop — the thing D5.1 and D5.5
between them spent two sprints preventing. So the boundaries get more tests than
the happy path.

No test opens a socket, sleeps on a ladder, or reaches a broker API.
LIVE VALIDATION WAS NOT PERFORMED.
"""

import ast
import contextlib
import pathlib
import re
from unittest.mock import AsyncMock, patch

import pytest

from services.broker_engine import BrokerEngine
from services.brokers.recovery import (
    REPROBEABLE_CLASSES,
    SELF_RECOVERING_CLASSES,
    STILL_UNAVAILABLE_BASE_DELAY,
    STILL_UNAVAILABLE_MAX_DELAY,
    RecoveryClass,
    RecoveryRegister,
    RecoveryService,
    ReprobeOutcome,
    recovery_register,
)
from services.brokers.reliability import RECONNECT_BASE_DELAY, RECONNECT_MAX_DELAY
from services.brokers.stream import stream_manager
from services.brokers.streaming import DEFAULT_STREAM_CHANNEL, StreamEventKind
from services.market_engine.providers import (
    Capability,
    ResolutionContext,
    YahooPollingAdapter,
)
from services.market_engine.source_manager import SourceManager
from tests._fakedb import FakeDB
from tests.test_broker_framework import _strip_comments_and_strings as _strip_source
from tests.test_broker_streaming import (
    NovaAdapter,
    _attach,
    _clean_provider_registry,
    nova_registered,
    run,
)
from tests.test_provider_entitlement import EntitlementNovaAdapter, entitlement_nova
from tests.test_provider_probation import FakeClock, _tick

BACKEND = pathlib.Path(__file__).resolve().parent.parent
TICKS = DEFAULT_STREAM_CHANNEL


# ==================================================================
# Fixtures
# ==================================================================


@contextlib.contextmanager
def clean_register():
    """Run against the process-wide register, restoring it afterwards.

    The register is a module-level singleton for the same reason `stream_manager`
    and `provider_registry` are, and a leaked candidate would be re-probed by
    whatever ran next.
    """
    saved_candidates = dict(recovery_register._candidates)
    saved_history = dict(recovery_register._history)
    recovery_register.clear()
    try:
        yield recovery_register
    finally:
        recovery_register._candidates.clear()
        recovery_register._candidates.update(saved_candidates)
        recovery_register._history.clear()
        recovery_register._history.update(saved_history)


def _engine():
    engine = BrokerEngine()
    engine.configure(FakeDB())
    return engine


def _register(clock=None, jitter=lambda delay: delay):
    """A register whose clock and jitter a test controls.

    Jitter defaults to the identity so the ladder's *rungs* are assertable
    exactly. `reconnect_pause`'s equal-jitter behaviour is D5.1's and is tested
    there; what matters here is that a re-probe ladder exists, climbs, and caps.
    """
    return RecoveryRegister(clock=clock or FakeClock(), jitter=jitter)


class _Attacher:
    """Records the attaches a service asked for, without performing one."""

    def __init__(self, *, raises=None):
        self.calls = []
        self._raises = raises

    async def __call__(self, user_id, broker, channel):
        self.calls.append((user_id, broker, channel))
        if self._raises is not None:
            raise self._raises


def _service(register, attacher=None, *, session=True, attached=False, **kwargs):
    return RecoveryService(
        register,
        attach=attacher if attacher is not None else _Attacher(),
        has_session=(session if callable(session) else (lambda u, b: session)),
        is_attached=(attached if callable(attached) else (lambda u, b, c: attached)),
        **kwargs,
    )


def _market_fixture(users=("u1",), broker="nova", symbols=("RELIANCE",), probation=0.0):
    """The real registry holding the baseline plus one feed per user.

    Deliberately the same construction the D5.5 suite uses — through
    `attach_market_feed`, the seam the engine actually uses — so the tests below
    exercise a provider built where the platform builds one.
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


# ==================================================================
# 1–2. A refusal stops the feed, and the withdrawal is recorded
# ==================================================================


def test_a_refusal_still_stops_the_feed_and_now_records_a_recovery_candidate():
    """Requirements 1 and 2. D5.5's behaviour is unchanged; a record is added.

    The refusal must stay exactly as terminal as it was — the transport does not
    reconnect and nothing here restarts it. What D5.6 adds is that the
    withdrawal stops being invisible.
    """
    with entitlement_nova(), clean_register() as register, _clean_provider_registry() as registry:
        registry.clear()
        manager, baseline, _feeds = _market_fixture()
        run(_engine()._on_stream_not_entitled("u1", "nova", TICKS))

        assert _quote(manager, "u1") is baseline, "the refused feed still served the quote"

        candidate = register.get("u1", "nova", TICKS)
        assert candidate is not None, "the withdrawal was not recorded — nothing can recover it"
        assert candidate.recovery_class is RecoveryClass.REPROBE
        assert candidate.is_reprobeable


def test_a_refusal_does_not_itself_attach_anything():
    """The record is a record. Recording must not be a retry in disguise."""
    with entitlement_nova(), clean_register() as register, _clean_provider_registry() as registry:
        registry.clear()
        _market_fixture()
        attacher = _Attacher()
        service = _service(register, attacher)
        run(_engine()._on_stream_not_entitled("u1", "nova", TICKS))

        assert attacher.calls == []
        # ...and it is not due either: the ladder starts at the base delay.
        assert run(service.reprobe("u1", "nova", TICKS)) is ReprobeOutcome.TOO_SOON
        assert attacher.calls == []


# ==================================================================
# 3–4. Still refused: no active provider, and the ladder climbs
# ==================================================================


def test_a_re_probe_while_still_refused_does_not_recreate_an_active_provider():
    """Requirement 3, asserted through the real registry.

    The attach happens; the broker refuses again; `_on_stream_not_entitled` runs
    again. What must be true afterwards is that the account has no resolvable
    feed — a re-probe may create an *attempt*, never a provider that serves.
    """
    clock = FakeClock()
    with entitlement_nova(), clean_register(), _clean_provider_registry() as registry:
        registry.clear()
        register = _register(clock)
        engine = _engine()
        manager, baseline, _feeds = _market_fixture()

        async def attach_that_is_refused(user_id, broker, channel):
            # What the transport does when the broker refuses the re-probed
            # connection: exactly the D5.5 path, unchanged.
            await engine._on_stream_not_entitled(user_id, broker, channel)

        service = _service(register, attach_that_is_refused)
        register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
        clock.advance(STILL_UNAVAILABLE_BASE_DELAY + 1)

        assert run(service.reprobe("u1", "nova", TICKS)) is ReprobeOutcome.ATTEMPTED
        assert _quote(manager, "u1") is baseline, "a refused re-probe produced a serving feed"
        assert register.get("u1", "nova", TICKS).is_reprobeable, "the candidate was lost"


def test_the_re_probe_ladder_climbs_and_is_capped():
    """Requirement 4. Bounded pacing, and pacing that is its own.

    Rung n is `base * 2**n`, capped. Asserted at the published constants rather
    than at a value chosen to keep the test fast, which is why the clock is
    injected.
    """
    clock = FakeClock()
    register = _register(clock)
    service = _service(register)
    register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)

    expected = []
    for _ in range(12):
        candidate = register.get("u1", "nova", TICKS)
        wait = candidate.next_attempt_at - clock.now
        expected.append(wait)
        clock.advance(wait)
        assert run(service.reprobe("u1", "nova", TICKS)) is ReprobeOutcome.ATTEMPTED

    assert expected[0] == STILL_UNAVAILABLE_BASE_DELAY
    assert expected[1] == STILL_UNAVAILABLE_BASE_DELAY * 2
    assert expected[2] == STILL_UNAVAILABLE_BASE_DELAY * 4
    assert expected == sorted(expected), f"the ladder did not climb monotonically: {expected}"
    assert expected[-1] == STILL_UNAVAILABLE_MAX_DELAY, "the ladder is unbounded"
    assert max(expected) <= STILL_UNAVAILABLE_MAX_DELAY


def test_an_undue_candidate_is_never_attempted_however_often_it_is_asked():
    """The pacing is a gate, not a hint: asking a hundred times attaches zero
    times, which is the difference between a schedule and a spin loop."""
    clock = FakeClock()
    register = _register(clock)
    attacher = _Attacher()
    service = _service(register, attacher)
    register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)

    for _ in range(100):
        assert run(service.reprobe("u1", "nova", TICKS)) is ReprobeOutcome.TOO_SOON
    assert attacher.calls == []


def test_the_re_probe_ladder_is_not_the_reconnect_ladder():
    """The core architectural rule of ADR-046, asserted as a number.

    Reconnect asks "is the same socket reachable"; re-probe asks "has a
    provider-level condition changed". Sharing a ladder would mean re-asking an
    entitlement every two seconds because that is how fast a socket should come
    back — the churn D5.5 exists to stop.
    """
    assert STILL_UNAVAILABLE_BASE_DELAY > RECONNECT_MAX_DELAY, (
        "the slowest reconnect is still faster than the fastest re-probe — "
        "the two schedules have collapsed into one"
    )
    assert STILL_UNAVAILABLE_BASE_DELAY != RECONNECT_BASE_DELAY
    assert STILL_UNAVAILABLE_MAX_DELAY > RECONNECT_MAX_DELAY


def test_an_attempt_that_raises_still_costs_a_rung():
    """A broker that reliably throws must back off exactly as one that reliably
    refuses. Charging the ladder after the attach would leave it at the base
    delay forever."""
    clock = FakeClock()
    register = _register(clock)
    service = _service(register, _Attacher(raises=RuntimeError("socket refused")))
    register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
    clock.advance(STILL_UNAVAILABLE_BASE_DELAY)

    assert run(service.reprobe("u1", "nova", TICKS)) is ReprobeOutcome.ATTEMPT_FAILED
    assert register.get("u1", "nova", TICKS).attempts == 1
    assert register.get("u1", "nova", TICKS).next_attempt_at - clock.now == \
        STILL_UNAVAILABLE_BASE_DELAY * 2


def test_an_apparent_success_cannot_buy_a_fresh_ladder():
    """The accept-then-refuse shape, which is DB-5 one layer up.

    A broker that accepts a socket, delivers a tick, and refuses the entitlement
    a moment later would — if the discharge cleared the attempt count — reset the
    ladder to five minutes on every cycle, forever. The register keeps the count
    outside the candidate precisely so it cannot.
    """
    clock = FakeClock()
    register = _register(clock)
    service = _service(register)
    register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
    clock.advance(STILL_UNAVAILABLE_BASE_DELAY)
    run(service.reprobe("u1", "nova", TICKS))

    register.discharge("u1", "nova")
    assert register.get("u1", "nova", TICKS) is None

    register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
    assert register.get("u1", "nova", TICKS).next_attempt_at - clock.now == \
        STILL_UNAVAILABLE_BASE_DELAY * 2, "the ladder reset on an apparent success"


# ==================================================================
# 5–7, 24. Entitlement restored: a real feed, earned the real way
# ==================================================================


def test_entitlement_restoration_lets_the_stream_be_recreated():
    """Requirement 5, through the engine's own attach path.

    The re-probe calls `start_stream` scoped to one channel; the broker no
    longer refuses; the account has a stream again.
    """
    with entitlement_nova(), clean_register(), _clean_provider_registry() as registry:
        registry.clear()
        clock = FakeClock()
        register = _register(clock)
        engine = _engine()
        engine._sessions[("u1", "nova")] = {"access_token": "still-good"}
        service = _service(register, engine._reattach_channel)
        register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
        clock.advance(STILL_UNAVAILABLE_BASE_DELAY)

        with patch.object(stream_manager, "start_stream", new=AsyncMock()) as started, \
                patch.object(engine, "get_session",
                             new=AsyncMock(return_value={"access_token": "still-good"})):
            assert run(service.reprobe("u1", "nova", TICKS)) is ReprobeOutcome.ATTEMPTED

        assert started.await_count == 1, "the re-probe opened no stream"
        assert started.await_args.kwargs["channel"] == TICKS


def test_a_recovered_feed_must_still_earn_readiness_and_probation():
    """Requirements 6, 7 and 24 together — the heart of ADR-046.

    A successful probe is not readiness. The recovered provider is registered,
    linked and subscribed and STILL does not serve the quote: only a valid
    canonical tick does that, and only a full probation window makes it stable.
    """
    from services.brokers.market_feed import (
        feed_provider_name,
        publish_market_ticks,
        set_market_feed_link,
    )
    from services.market_engine.providers import provider_registry
    from services.market_engine.providers import streaming as streaming_module

    with entitlement_nova(), clean_register(), _clean_provider_registry() as registry:
        registry.clear()
        manager, baseline, _feeds = _market_fixture(probation=streaming_module.PROBATION_WINDOW_SECONDS)
        run(_engine()._on_stream_not_entitled("u1", "nova", TICKS))
        assert _quote(manager, "u1") is baseline

        # The re-probe's attach, replayed through the same seam the engine uses.
        clock = FakeClock()
        with patch.object(streaming_module, "PROBATION_WINDOW_SECONDS",
                          streaming_module.PROBATION_WINDOW_SECONDS):
            run(_attach("u1", "nova", ["RELIANCE"]))
        feed = provider_registry.get(feed_provider_name("u1", "nova"))
        feed._clock = clock
        run(set_market_feed_link("u1", "nova", up=True))

        assert not feed.is_ready, "attaching made the feed ready"
        assert _quote(manager, "u1") is baseline, "a re-attached feed served before it was ready"

        run(publish_market_ticks("u1", "nova", [_tick()]))
        assert feed.is_ready, "a valid canonical tick did not earn readiness"
        assert feed.is_on_probation, "a recovered feed skipped probation"

        clock.advance(streaming_module.PROBATION_WINDOW_SECONDS + 1)
        run(publish_market_ticks("u1", "nova", [_tick(price=2651.0)]))
        assert feed.is_stable, "the recovered feed never left probation"
        assert _quote(manager, "u1") is feed, "a stable recovered feed was not selected"


@pytest.mark.parametrize("inherited", ["readiness", "stability", "latency"])
def test_a_recovered_feed_inherits_nothing_from_the_refused_one(inherited):
    """Requirements 9 and 10.

    Structural rather than enforced: `attach_market_feed` constructs a *new*
    `StreamingTickProvider`, so there is no object left holding the old link's
    readiness, probation window or delivery intervals. The test asserts the
    property rather than the mechanism, so a future implementation that reuses
    the instance has to keep it true.
    """
    from services.brokers.market_feed import (
        feed_provider_name,
        publish_market_ticks,
        set_market_feed_link,
    )
    from services.market_engine.providers import provider_registry

    with entitlement_nova(), clean_register(), _clean_provider_registry() as registry:
        registry.clear()
        clock = FakeClock()
        _manager, _baseline, feeds = _market_fixture()
        refused = feeds["u1"]
        refused._clock = clock
        # Give the refused feed everything it could possibly hand on: readiness,
        # a completed probation window and a full latency sample window.
        for i in range(12):
            run(publish_market_ticks("u1", "nova", [_tick(price=2650.0 + i)]))
            clock.advance(5.0)
        assert refused.is_ready and refused.is_stable
        assert refused.delivery_latency is not None

        run(_engine()._on_stream_not_entitled("u1", "nova", TICKS))
        run(_attach("u1", "nova", ["RELIANCE"]))
        run(set_market_feed_link("u1", "nova", up=True))
        recovered = provider_registry.get(feed_provider_name("u1", "nova"))

        assert recovered is not refused, "the refused provider object came back"
        if inherited == "readiness":
            assert not recovered.is_ready
        elif inherited == "stability":
            assert not recovered.is_stable
        else:
            assert recovered.delivery_latency is None


def test_yahoo_remains_available_at_every_step_of_a_recovery():
    """Requirement 8 and rule 14 — make-before-break.

    Recovery is additive. The baseline is never released to make room for a
    probe, so there is no instant at which the user has no feed.
    """
    from services.brokers.market_feed import publish_market_ticks, set_market_feed_link

    with entitlement_nova(), clean_register(), _clean_provider_registry() as registry:
        registry.clear()
        manager, baseline, _feeds = _market_fixture()
        seen = []

        def observe():
            seen.append((manager.status(user_id="u1")["state"],
                         baseline in manager.failover_chain(
                             Capability.QUOTES,
                             ResolutionContext(user_id="u1", symbol="RELIANCE"))))

        observe()
        run(_engine()._on_stream_not_entitled("u1", "nova", TICKS))
        observe()
        run(_attach("u1", "nova", ["RELIANCE"]))
        observe()
        run(set_market_feed_link("u1", "nova", up=True))
        observe()
        run(publish_market_ticks("u1", "nova", [_tick()]))
        observe()

        assert all(state == "available" for state, _ in seen), \
            f"the feed went unavailable during recovery: {seen}"
        assert all(present for _, present in seen), \
            f"the baseline left the chain during recovery: {seen}"


# ==================================================================
# 11–12. Authentication: never automatic
# ==================================================================


def test_an_expired_session_is_recorded_as_unrecoverable_by_re_probe():
    """Requirement 11, first guard: classification.

    Recorded rather than merely absent, so the exclusion is a fact a test can
    read and a mutation can break.
    """
    with entitlement_nova(), clean_register() as register, _clean_provider_registry() as registry:
        registry.clear()
        _market_fixture()
        engine = _engine()
        engine._sessions[("u1", "nova")] = {"access_token": "dead"}
        with patch.object(BrokerEngine, "_push", new=AsyncMock()):
            run(engine._on_stream_expired("u1", "nova", TICKS))

        candidate = register.get("u1", "nova", TICKS)
        assert candidate is not None, "the withdrawal was not recorded at all"
        assert candidate.recovery_class is RecoveryClass.SESSION
        assert not candidate.is_reprobeable
        assert candidate.next_attempt_at is None, "a dead session was given a retry schedule"
        assert register.due() == [], "an expired session entered the re-probe queue"


def test_an_expiry_downgrades_an_entitlement_candidate_on_every_channel():
    """The account-level fact beats the channel-level one.

    A feed refused on entitlement grounds whose token then dies — reported on a
    *different* channel — must stop being re-probeable at that instant. Without
    this the register would keep re-attaching with a credential the broker has
    already rejected.
    """
    with entitlement_nova(), clean_register() as register, _clean_provider_registry() as registry:
        registry.clear()
        _market_fixture()
        engine = _engine()
        engine._sessions[("u1", "nova")] = {"access_token": "dead"}
        register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)

        with patch.object(BrokerEngine, "_push", new=AsyncMock()):
            run(engine._on_stream_expired("u1", "nova", "orders"))

        assert register.get("u1", "nova", TICKS).recovery_class is RecoveryClass.SESSION
        assert register.due() == []


def test_a_due_candidate_whose_session_vanished_is_never_attempted():
    """Requirement 11, second guard: session availability at attempt time.

    Distinct from the classification guard and catching a fact it cannot know:
    the entitlement was refused *first*, and the session went away *afterwards*
    — the user disconnected the broker, or another channel reported the token
    dead. No classification made at withdrawal time can see that.
    """
    clock = FakeClock()
    register = _register(clock)
    attacher = _Attacher()
    service = _service(register, attacher, session=False)
    register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
    clock.advance(STILL_UNAVAILABLE_BASE_DELAY)

    assert run(service.reprobe("u1", "nova", TICKS)) is ReprobeOutcome.SESSION_UNAVAILABLE
    assert attacher.calls == [], "a re-probe attached with no session"
    assert register.get("u1", "nova", TICKS).attempts == 0, \
        "a blocked probe charged the ladder — a reconnecting user would be paced for nothing"


def test_the_engines_session_predicate_follows_the_real_session_cache():
    """The second guard is only worth anything if it reads the real thing.

    Every path that invalidates a session pops this map, which is what makes the
    predicate correct rather than merely present.
    """
    engine = _engine()
    assert engine._has_live_session("u1", "nova") is False
    engine._sessions[("u1", "nova")] = {"access_token": "t"}
    assert engine._has_live_session("u1", "nova") is True
    engine._sessions.pop(("u1", "nova"))
    assert engine._has_live_session("u1", "nova") is False


def test_a_new_valid_session_clears_the_withdrawal_and_the_ladder():
    """Requirement 12. Re-authentication is the authoritative recovery path, and
    it is the only thing that resets pacing."""
    with clean_register() as register:
        register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.SESSION)
        register.record_withdrawal("u1", "nova", "orders", RecoveryClass.REPROBE)

        engine = _engine()
        with nova_registered(NovaAdapter()), \
                patch.object(engine, "_save_account", new=AsyncMock()), \
                patch.object(engine, "sync_portfolio", new=AsyncMock()), \
                patch.object(engine, "_push", new=AsyncMock()), \
                patch("services.brokers.gateway.broker_gateway.exchange_token",
                      new=AsyncMock(return_value={"access_token": "fresh"})):
            run(engine.complete_auth("nova", "u1", {"request_token": "x"}))

        assert register.candidates() == [], "reconnecting the broker left a withdrawal behind"
        assert register._history == {}, "reconnecting the broker left the ladder climbed"


def test_disconnecting_the_broker_leaves_no_recovery_state_behind():
    """A user who removed the account must not have it re-probed."""
    with clean_register() as register:
        register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
        engine = _engine()
        with nova_registered(NovaAdapter()), \
                patch.object(stream_manager, "stop_stream", new=AsyncMock()), \
                patch.object(engine, "_push", new=AsyncMock()):
            run(engine.disconnect("nova", "u1"))

        assert register.candidates() == []
        assert register._history == {}


# ==================================================================
# 13–14. Transient failures stay D5.1's
# ==================================================================


def test_a_transport_class_withdrawal_is_refused_registration_outright():
    """Requirement 13. A blip is the reconnect ladder's business.

    Refused rather than registered-and-skipped, so there is no state a later
    change could accidentally make re-probeable.
    """
    register = _register()
    assert register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.TRANSPORT) is None
    assert register.candidates() == []
    assert RecoveryClass.TRANSPORT not in REPROBEABLE_CLASSES
    assert RecoveryClass.TRANSPORT in SELF_RECOVERING_CLASSES


def test_an_ordinary_link_loss_creates_no_recovery_candidate():
    """The engine's link-state path is untouched by this sprint.

    A dropped socket demotes the provider and the transport reconnects. If that
    produced a candidate, every blip on every feed would enter a five-minute
    ladder and D5.1's sub-second recovery would be shadowed by a second
    mechanism.
    """
    with entitlement_nova(), clean_register() as register, _clean_provider_registry() as registry:
        registry.clear()
        _manager, _baseline, feeds = _market_fixture()
        run(_engine()._on_stream_link_state("u1", "nova", False, "socket closed", TICKS))

        assert not feeds["u1"].is_ready, "the link loss did not demote the feed"
        assert register.candidates() == [], "a transport blip entered the re-probe register"


def test_the_reconnect_ladder_is_untouched_by_recovery():
    """Requirement 14. Nothing in the transport reads the recovery register, and
    nothing in the recovery module reads a connection lifetime."""
    transport = _strip_source((BACKEND / "services/brokers/stream.py").read_text())
    assert "recovery" not in transport.lower(), \
        "the reconnect ladder learned about re-probe — D5.1 pacing is no longer transport-only"

    recovery = _strip_source((BACKEND / "services/brokers/recovery.py").read_text())
    for borrowed in ("STABLE_CONNECTION_SECONDS", "ConnectionStability", "RECONNECT_BASE_DELAY"):
        assert borrowed not in recovery, \
            f"re-probe pacing borrowed connection-lifetime semantics ({borrowed})"


# ==================================================================
# 15–16. Stale feeds recover by themselves
# ==================================================================


def test_a_stale_open_feed_recovers_on_a_fresh_tick_with_no_control_plane_probe():
    """Requirements 15 and 16, pinned rather than re-implemented.

    D5.3 already made this work: the link never dropped, so nothing was
    discarded, and a fresh accepted tick restores fresh evidence and stability
    on the spot. Adding re-probe machinery here would be a second recovery
    mechanism racing a push-driven one that is already correct — so the test
    asserts both the recovery *and* the absence of the machinery.
    """
    from services.brokers.market_feed import publish_market_ticks
    from services.market_engine.providers.streaming import DEFAULT_TICK_MAX_AGE_SECONDS

    with entitlement_nova(), clean_register() as register, _clean_provider_registry() as registry:
        registry.clear()
        clock = FakeClock()
        manager, baseline, feeds = _market_fixture()
        feed = feeds["u1"]
        feed._clock = clock

        run(publish_market_ticks("u1", "nova", [_tick()]))
        clock.advance(1.0)
        run(publish_market_ticks("u1", "nova", [_tick(price=2651.0)]))
        assert feed.is_ready and feed.has_fresh_evidence

        clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)
        assert not feed.has_fresh_evidence, "the feed did not go stale"
        assert _quote(manager, "u1") is baseline, "a stale feed still served the quote"
        assert register.candidates() == [], "going stale created a recovery candidate"

        run(publish_market_ticks("u1", "nova", [_tick(price=2652.0)]))
        assert feed.has_fresh_evidence, "a fresh tick did not restore the feed"
        assert _quote(manager, "u1") is feed, "the naturally recovered feed was not re-selected"
        assert register.candidates() == [], "natural recovery went through the control plane"


def test_an_evidence_class_withdrawal_is_refused_registration_outright():
    register = _register()
    assert register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.EVIDENCE) is None
    assert RecoveryClass.EVIDENCE not in REPROBEABLE_CLASSES


def test_market_data_arriving_discharges_an_outstanding_candidate():
    """The push-driven discharge, taken at the engine's tick boundary.

    Evidence rather than a socket: a connection that opened proves nothing, and
    a candidate cleared on link-up would be re-created a frame later by a broker
    that accepts and then refuses.
    """
    with entitlement_nova(), clean_register() as register, _clean_provider_registry() as registry:
        registry.clear()
        _market_fixture()
        register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
        engine = _engine()

        run(engine._on_stream_tick("u1", "nova", []))
        assert register.get("u1", "nova", TICKS) is not None, \
            "an empty batch discharged a withdrawal"

        run(engine._on_stream_tick("u1", "nova", [{"scrip": "RELIANCE", "rate": "2650.0"}]))
        assert register.get("u1", "nova", TICKS) is None, "market data did not discharge the candidate"


# ==================================================================
# 17–20. Isolation
# ==================================================================


def test_two_users_of_the_same_broker_recover_independently():
    """Requirement 17, through the real registry and the real register."""
    with entitlement_nova(), clean_register() as register, _clean_provider_registry() as registry:
        registry.clear()
        manager, _baseline, feeds = _market_fixture(users=("u1", "u2"))
        from services.brokers.market_feed import publish_market_ticks
        run(publish_market_ticks("u2", "nova", [_tick()]))

        run(_engine()._on_stream_not_entitled("u1", "nova", TICKS))

        assert register.get("u1", "nova", TICKS) is not None
        assert register.get("u2", "nova", TICKS) is None, "user B was withdrawn by user A's refusal"
        assert feeds["u2"].is_ready, "user B's feed lost readiness"
        assert _quote(manager, "u2") is feeds["u2"], "user B stopped being served"

        clock = FakeClock()
        paced = _register(clock)
        attacher = _Attacher()
        service = _service(paced, attacher)
        paced.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
        clock.advance(STILL_UNAVAILABLE_BASE_DELAY)
        run(service.sweep_once())

        assert attacher.calls == [("u1", "nova", TICKS)], \
            f"a re-probe for user A touched somebody else: {attacher.calls}"


def test_different_brokers_of_the_same_user_recover_independently():
    """Requirement 18. The key is (user, broker, channel), so this is
    structural — but the structure is what a mutation would remove."""
    clock = FakeClock()
    register = _register(clock)
    attacher = _Attacher()
    service = _service(register, attacher)
    register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
    register.record_withdrawal("u1", "orion", TICKS, RecoveryClass.SESSION)

    clock.advance(STILL_UNAVAILABLE_BASE_DELAY)
    run(service.sweep_once())

    assert attacher.calls == [("u1", "nova", TICKS)], \
        "recovery crossed a broker boundary"
    assert register.get("u1", "orion", TICKS).recovery_class is RecoveryClass.SESSION


def test_two_channels_of_one_account_recover_independently():
    """A refusal on the market feed says nothing about the order socket."""
    clock = FakeClock()
    register = _register(clock)
    register.record_withdrawal("u1", "nova", "ticks", RecoveryClass.REPROBE)
    clock.advance(STILL_UNAVAILABLE_BASE_DELAY)
    run(_service(register).reprobe("u1", "nova", "ticks"))

    register.record_withdrawal("u1", "nova", "orders", RecoveryClass.REPROBE)
    assert register.get("u1", "nova", "orders").next_attempt_at - clock.now == \
        STILL_UNAVAILABLE_BASE_DELAY, "one channel's ladder paced another's"


def test_the_guest_and_baseline_contexts_are_unchanged_by_a_recovery():
    """Requirement 19. A user with no broker sees byte-identical status before
    and after somebody else's whole recovery cycle."""
    from services.brokers.market_feed import publish_market_ticks, set_market_feed_link
    from services.market_engine.providers import GLOBAL_CONTEXT

    with entitlement_nova(), clean_register(), _clean_provider_registry() as registry:
        registry.clear()
        manager, _baseline, _feeds = _market_fixture()
        before = (manager.status(), manager.status(user_id="guest"))

        run(_engine()._on_stream_not_entitled("u1", "nova", TICKS))
        run(_attach("u1", "nova", ["RELIANCE"]))
        run(set_market_feed_link("u1", "nova", up=True))
        run(publish_market_ticks("u1", "nova", [_tick()]))

        assert (manager.status(), manager.status(user_id="guest")) == before
        assert manager.resolve_feed(Capability.QUOTES, GLOBAL_CONTEXT).available


def test_a_sweep_starts_no_attach_for_an_account_already_attached():
    """Requirement 20's other half: recovery may never break a live connection.

    `start_stream` stops a channel before replacing it, so an unguarded sweep
    would tear down the feed a user reconnect had already restored — recovering
    a feed by killing it.
    """
    clock = FakeClock()
    register = _register(clock)
    attacher = _Attacher()
    service = _service(register, attacher, attached=True)
    register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
    clock.advance(STILL_UNAVAILABLE_BASE_DELAY)

    assert run(service.reprobe("u1", "nova", TICKS)) is ReprobeOutcome.ALREADY_ATTACHED
    assert attacher.calls == []
    assert register.get("u1", "nova", TICKS) is None, \
        "a channel somebody else restored kept an outstanding withdrawal"


def test_the_engines_attachment_predicate_reads_the_real_stream_registry():
    engine = _engine()
    assert engine._channel_is_attached("u1", "nova", TICKS) is False

    async def scenario():
        with nova_registered(NovaAdapter()):
            await stream_manager.start_stream(
                "u1", "nova", {"access_token": "t"}, credentials={},
                instrument_tokens=["RELIANCE"], channel=TICKS)
            attached = engine._channel_is_attached("u1", "nova", TICKS)
            other = engine._channel_is_attached("u2", "nova", TICKS)
            await stream_manager.stop_stream("u1", "nova")
            return attached, other

    attached, other = run(scenario())
    assert attached is True
    assert other is False, "the attachment predicate is not user-scoped"


def test_a_recovery_re_probe_opens_only_the_channel_it_is_recovering():
    """The channel filter, which is what stops a market-feed re-probe blipping a
    healthy order socket."""
    from services.brokers.base import AdapterStreamChannel

    class TwoChannel(EntitlementNovaAdapter):
        def stream_channels(self):
            return (
                AdapterStreamChannel(self, name="ticks", delivers=frozenset({StreamEventKind.TICKS})),
                AdapterStreamChannel(self, name="orders", delivers=frozenset({StreamEventKind.ORDER})),
            )

    engine = _engine()
    engine._sessions[("u1", "nova")] = {"access_token": "t"}
    with nova_registered(TwoChannel()), _clean_provider_registry() as registry:
        registry.clear()
        with patch.object(stream_manager, "start_stream", new=AsyncMock()) as started, \
                patch.object(engine, "get_session", new=AsyncMock(return_value={"access_token": "t"})):
            run(engine._reattach_channel("u1", "nova", "orders"))

    assert [call.kwargs["channel"] for call in started.await_args_list] == ["orders"]


def test_a_non_tick_channel_re_probe_does_not_replace_a_live_market_feed():
    """The subtler half of the same rule.

    Re-registering the account's provider would build a *new*
    `StreamingTickProvider` and throw away a live feed's readiness, probation
    and latency evidence — to re-ask a question about a different socket.
    """
    from services.brokers.base import AdapterStreamChannel
    from services.brokers.market_feed import feed_provider_name, publish_market_ticks
    from services.market_engine.providers import provider_registry

    class TwoChannel(EntitlementNovaAdapter):
        def stream_channels(self):
            return (
                AdapterStreamChannel(self, name="ticks", delivers=frozenset({StreamEventKind.TICKS})),
                AdapterStreamChannel(self, name="orders", delivers=frozenset({StreamEventKind.ORDER})),
            )

    engine = _engine()
    engine._sessions[("u1", "nova")] = {"access_token": "t"}
    with nova_registered(TwoChannel()), clean_register(), _clean_provider_registry() as registry:
        registry.clear()
        _market_fixture()
        run(publish_market_ticks("u1", "nova", [_tick()]))
        live = provider_registry.get(feed_provider_name("u1", "nova"))
        assert live.is_ready

        with patch.object(stream_manager, "start_stream", new=AsyncMock()), \
                patch.object(engine, "get_session", new=AsyncMock(return_value={"access_token": "t"})):
            run(engine._reattach_channel("u1", "nova", "orders"))

        assert provider_registry.get(feed_provider_name("u1", "nova")) is live, \
            "an order-channel re-probe replaced the live market feed"
        assert live.is_ready, "an order-channel re-probe discarded the market feed's readiness"


def test_recovery_state_never_reaches_a_consumer_surface():
    """Requirement 20. `provider.status` is unchanged in shape and carries no
    recovery vocabulary, no broker identity and no attempt count."""
    with entitlement_nova(), clean_register(), _clean_provider_registry() as registry:
        registry.clear()
        manager, _baseline, _feeds = _market_fixture()
        run(_engine()._on_stream_not_entitled("u1", "nova", TICKS))

        status = manager.status(user_id="u1")
        assert set(status) == {"state", "tier", "reason", "capabilities"}
        blob = repr(status).lower()
        for leak in ("nova", "reprobe", "recovery", "entitle", "attempt", "brokerfeed", "token"):
            assert leak not in blob, f"{leak!r} reached a consumer status payload"


# ==================================================================
# 21–23. Architecture
# ==================================================================


BROKER_NAMES = ("zerodha", "kite", "upstox", "angelone", "angel one", "smartapi",
                "fyers", "dhan", "groww", "indmoney")

#: Every module the recovery path passes through that must stay broker-neutral.
GENERIC_RECOVERY_MODULES = (
    "services/brokers/recovery.py",
    "services/brokers/reliability.py",
    "services/brokers/stream.py",
    "services/brokers/market_feed.py",
)


def test_no_broker_name_appears_anywhere_in_the_recovery_module():
    """Requirement 21, run the strict D5.1 way: comments and strings left IN.

    A comment naming a broker in generic recovery code is a design statement
    even when it is inert.
    """
    body = (BACKEND / "services/brokers/recovery.py").read_text().lower()
    offenders = [name for name in BROKER_NAMES if re.search(rf"\b{re.escape(name)}\b", body)]
    assert not offenders, f"a broker is named in the recovery module: {offenders}"


def test_no_broker_name_appears_in_the_executable_code_of_the_recovery_path():
    offenders = []
    for relative in GENERIC_RECOVERY_MODULES:
        source = _strip_source((BACKEND / relative).read_text()).lower()
        for name in BROKER_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", source):
                offenders.append(f"{relative}: {name}")
    assert not offenders, f"a broker is named in generic executable code: {offenders}"


def test_no_recovery_code_branches_on_a_broker_or_provider_identity():
    """Requirement 21's stronger form. `if broker == "x"` passes a name sweep."""
    pattern = re.compile(r"(broker|provider|channel|name)\s*(==|!=)\s*['\"]")
    offenders = []
    for relative in GENERIC_RECOVERY_MODULES + ("services/broker_engine.py",):
        source = _strip_source((BACKEND / relative).read_text())
        for line in source.splitlines():
            if pattern.search(line):
                offenders.append(f"{relative}: {line.strip()}")
    assert not offenders, f"an identity branch reached recovery code: {offenders}"


def test_the_recovery_module_imports_nothing_but_the_standard_library_and_one_policy_module():
    """The neutrality that makes it reusable: no adapter, no engine, no registry,
    no market layer, and therefore no cycle."""
    tree = ast.parse((BACKEND / "services/brokers/recovery.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    project = {name for name in imported if name.startswith("services")}
    assert project == {"services.brokers.reliability"}, \
        f"the recovery module reached beyond its layer: {sorted(project)}"


def test_a_fictional_broker_uses_the_recovery_contract_with_no_core_change():
    """Requirement 22. If the mechanism only works for the broker that motivated
    it, it is not a mechanism.

    Nova shares no wire format, no error vocabulary and no code with any shipped
    adapter, and reaches the identical recovery candidate through the identical
    generic path.
    """
    with entitlement_nova(), clean_register() as register, _clean_provider_registry() as registry:
        registry.clear()
        _market_fixture()
        run(_engine()._on_stream_not_entitled("u1", "nova", TICKS))

        candidate = register.get("u1", "nova", TICKS)
        assert candidate.recovery_class is RecoveryClass.REPROBE
        assert candidate.describe(now=0.0)["reprobeable"] is True


#: D5.6's re-probe vocabulary, named symbol by symbol.
#:
#: Until D5.7 this requirement was expressed as `"recovery" not in source`, which
#: was a proxy: the property being defended is that the Market Engine never
#: reaches for the *broker layer's* re-probe machinery, and no market-engine
#: module had any recovery concept of its own to collide with the word. D5.7
#: gives it one — a provider-health failure cool-down that lives entirely in the
#: Market Engine and shares no code, no constant and no schedule with this
#: module (ADR-047) — so the proxy is replaced by the symbols it stood for.
#: Narrower in wording and no weaker in effect: the structural ban on importing
#: `services.brokers` is untouched, and every name a market-engine module would
#: have to use to consult D5.6 is listed here.
REPROBE_VOCABULARY = (
    "reprobe",
    "recoveryclass",
    "recoveryregister",
    "recoveryservice",
    "recoverycandidate",
    "recovery_register",
    "recovery_service",
    "record_withdrawal",
    "still_unavailable",
)


def test_the_market_engine_imports_no_recovery_implementation():
    """Requirement 23. The Market Engine learns that a provider went away and
    came back; it never learns that anything was re-probed."""
    for relative in ("services/market_engine/providers/streaming.py",
                     "services/market_engine/providers/registry.py",
                     "services/market_engine/providers/health_recovery.py",
                     "services/market_engine/source_manager.py",
                     "services/market_engine/gateway.py"):
        source = _strip_source((BACKEND / relative).read_text()).lower()
        assert "services.brokers" not in source, f"{relative} imports the broker layer"
        for symbol in REPROBE_VOCABULARY:
            assert symbol not in source, f"{relative} learned about re-probe ({symbol})"


def test_the_reprobe_implementation_learns_nothing_about_provider_health():
    """The other direction of the same boundary (D5.7).

    D5.6 paces a broker *stream* re-attach; D5.7 paces a market-data *provider*
    re-admission. They are separate mechanisms for separate units, and the way
    they stay separate is that neither can name the other's vocabulary."""
    source = _strip_source((BACKEND / "services/brokers/recovery.py").read_text()).lower()
    for symbol in ("providerstate", "providerhealth", "health_probe",
                   "record_failure", "record_success", "candidates_for",
                   "source_manager", "market_engine"):
        assert symbol not in source, f"the re-probe ladder learned about provider health ({symbol})"


def test_every_recovery_class_has_exactly_one_documented_behaviour():
    """Requirement: the five classes are a classification, not a retry flag with
    extra names. Each is either self-recovering, re-probeable, or neither —
    never two, and never unassigned."""
    for member in RecoveryClass:
        buckets = sum([member in SELF_RECOVERING_CLASSES, member in REPROBEABLE_CLASSES])
        assert buckets <= 1, f"{member.value} is in two recovery buckets"
    assert REPROBEABLE_CLASSES == {RecoveryClass.REPROBE}, \
        "a second class became re-probeable without a decision record"
    assert RecoveryClass.SESSION not in REPROBEABLE_CLASSES
    assert RecoveryClass.CONFIGURATION not in REPROBEABLE_CLASSES


@pytest.mark.parametrize("recovery_class", [RecoveryClass.SESSION, RecoveryClass.CONFIGURATION])
def test_a_non_reprobeable_class_is_never_attempted(recovery_class):
    clock = FakeClock()
    register = _register(clock)
    attacher = _Attacher()
    service = _service(register, attacher)
    register.record_withdrawal("u1", "nova", TICKS, recovery_class)
    clock.advance(STILL_UNAVAILABLE_MAX_DELAY * 10)

    assert run(service.reprobe("u1", "nova", TICKS)) is ReprobeOutcome.NOT_REPROBEABLE
    assert run(service.sweep_once()) == {}
    assert attacher.calls == []


def test_the_outcome_vocabulary_carries_no_broker_or_credential_vocabulary():
    """§6: the re-probe result the caller sees is broker-neutral by construction."""
    for outcome in ReprobeOutcome:
        blob = outcome.value.lower()
        assert not any(name in blob for name in BROKER_NAMES)
        for forbidden in ("token", "secret", "key", "403", "401", "code"):
            assert forbidden not in blob, f"{outcome.value} leaks {forbidden!r}"


def test_an_unregistered_key_is_never_attached():
    service = _service(_register(), attacher := _Attacher())
    assert run(service.reprobe("nobody", "nova", TICKS)) is ReprobeOutcome.NOT_REGISTERED
    assert attacher.calls == []


def test_an_empty_sweep_touches_nothing():
    """The cost of the background timer on a healthy deployment: two dictionary
    reads and no I/O."""
    attacher = _Attacher()
    assert run(_service(_register(), attacher).sweep_once()) == {}
    assert attacher.calls == []


def test_the_sweeper_is_startable_stoppable_and_idempotent():
    async def scenario():
        service = _service(_register(), interval=0.01)
        first = service.start()
        assert service.start() is first, "a second start added a second sweeper"
        assert service.running
        await service.stop()
        assert not service.running
        await service.stop()  # idempotent

    run(scenario())


def test_the_sweep_is_capped_so_one_wake_up_cannot_burst():
    clock = FakeClock()
    register = _register(clock)
    attacher = _Attacher()
    service = _service(register, attacher, max_per_sweep=2)
    for i in range(5):
        register.record_withdrawal(f"u{i}", "nova", TICKS, RecoveryClass.REPROBE)
    clock.advance(STILL_UNAVAILABLE_BASE_DELAY)

    run(service.sweep_once())
    assert len(attacher.calls) == 2, f"the cap did not hold: {attacher.calls}"


# ==================================================================
# 21 (brief). Security: DEBUG logging with live-looking credentials
# ==================================================================


#: Credential material shaped like the real thing — the point of the check is
#: that a `repr()` or an f-string somewhere in the recovery path would carry
#: exactly this, and a placeholder like "x" would not be recognisable in the
#: output even if it were.
FAKE_CREDENTIALS = {
    "access_token": "fake_access_token_for_test_only",
    "refresh_token": "fake_refresh_token_for_test_only",
    "feed_token": "fake_feed_token_for_test_only",
    "api_key": "fake_api_key_for_test_only",
    "api_secret": "fake_api_secret_for_test_only",
}


def test_the_whole_recovery_path_logs_no_credential_at_debug(caplog):
    """Brief requirement 21, run through the real logging stack.

    Every recovery log line is asserted to carry a broker name, a channel name,
    a user id and counters, and nothing else. Broker identity in a *log* is
    permitted by the current audit policy (a provider name already reaches the
    logs); a token, a key, a secret or a credential-bearing URL is not, on any
    surface (SECURITY.md).
    """
    import logging

    with entitlement_nova(), clean_register() as register, _clean_provider_registry() as registry:
        registry.clear()
        _market_fixture()
        engine = _engine()
        engine._sessions[("u1", "nova")] = dict(FAKE_CREDENTIALS)

        with caplog.at_level(logging.DEBUG):
            # The full cycle: refusal → record → paced re-probe → attach →
            # refusal again → session expiry → reclassification.
            run(engine._on_stream_not_entitled("u1", "nova", TICKS))
            clock = FakeClock()
            paced = _register(clock)
            paced.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
            clock.advance(STILL_UNAVAILABLE_BASE_DELAY)
            service = RecoveryService(
                paced,
                attach=engine._reattach_channel,
                has_session=engine._has_live_session,
                is_attached=engine._channel_is_attached,
            )
            with patch.object(engine, "get_session",
                              new=AsyncMock(return_value=dict(FAKE_CREDENTIALS))), \
                    patch.object(stream_manager, "start_stream", new=AsyncMock()):
                run(service.sweep_once())
            with patch.object(BrokerEngine, "_push", new=AsyncMock()):
                run(engine._on_stream_expired("u1", "nova", TICKS))

        blob = caplog.text
        assert "Recovery:" in blob, "the recovery path logged nothing — the check proves nothing"
        for field, value in FAKE_CREDENTIALS.items():
            assert value not in blob, f"{field} reached the logs"
            # Also catch a truncated or prefixed rendering of the same secret.
            assert value[:12] not in blob, f"a prefix of {field} reached the logs"
        for forbidden in ("Bearer ", "authorization", "?token=", "access_token"):
            assert forbidden.lower() not in blob.lower(), f"{forbidden!r} reached the logs"
        assert register.get("u1", "nova", TICKS).recovery_class is RecoveryClass.SESSION


def test_a_candidates_diagnostics_row_carries_no_credential_and_no_reason_text():
    """`describe()` is the admin surface. It may name a broker; it may not carry
    anything a session does."""
    register = _register()
    register.record_withdrawal("u1", "nova", TICKS, RecoveryClass.REPROBE)
    rows = register.describe()

    assert len(rows) == 1
    assert set(rows[0]) == {
        "user_id", "broker", "channel", "recovery_class",
        "reprobeable", "attempts", "due_in_seconds",
    }
    blob = repr(rows).lower()
    for forbidden in ("token", "secret", "key", "session", "password", "url"):
        assert forbidden not in blob, f"{forbidden!r} reached the diagnostics row"

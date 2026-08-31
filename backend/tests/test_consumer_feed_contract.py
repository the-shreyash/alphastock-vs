"""Sprint D5.13 — the consumer feed contract (hermetic).

WHAT THIS FILE PINS
-------------------
Two limitations reached D5.13 open, and the audit found them to be the same
defect on the same surface: `SourceManager.status()` — the one payload a
frontend, the AI context and the `/market/status` route are allowed to see.

    LIM-D5.12-1  During a sustained total outage the feed reports `available`
                 at every cool-down expiry, before anything has answered, and
                 `unavailable` again once the trial is spent. It also reports
                 `tier: delayed` — a freshness claim about a provider whose
                 health is DOWN and which has returned nothing.

    LIM-D5.5-2   After an entitlement refusal the user's tier drops from
                 `streaming` to `delayed` with `reason: null`. The explanation
                 exists only in an audit row.

The single root cause: `Resolution.as_status()` answered the *resolution*
question — "is there a provider I will try?" — on a surface whose consumers
ask the *delivery* question — "is my feed serving me usable data?". For every
provider that has passed health, readiness, freshness and coverage those two
questions have the same answer, which is why the collapse survived twelve
sprints. They come apart in exactly one place: a provider re-admitted at DOWN
for one D5.7 trial. And a *transition* reason has no field at all, because the
provider that explains it has already been unregistered by the time anyone
could ask.

THE CONTRACT THIS FILE ASSERTS
------------------------------
  * **`available` means a provider that has demonstrated it can serve is
    selected.** Not "a provider will be tried".
  * **`recovering` is a third state**, and it is a refinement of *not
    available*: something will be tried and nothing has answered yet. A
    consumer branching `state == "available"` takes the degraded branch, which
    is the safe direction.
  * **`recovering` carries `tier: null`.** A tier is a freshness claim and
    there is no data to make it about.
  * **Only DOWN health produces `recovering`.** UNKNOWN must not — the Yahoo
    baseline is UNKNOWN at startup and has never been called, and reporting the
    whole platform as recovering at boot would be a worse lie than the one this
    sprint removes. DEGRADED must not — a degraded provider is still serving.
  * **A transition reason rides the `provider.status` *event*, never
    `status()`.** The reason belongs to the change, not to the steady state: a
    consumer that reconnects an hour later and reads `status()` must not be
    handed a stale explanation.
  * **The reason vocabulary is the platform's, not a broker's.** No broker
    name, no wire code, no transport error text, no credential.

Resolution itself is deliberately unchanged. The probe is still offered, still
ranked last and still spent by the request that reaches it — a status surface
that suppressed the probe would suppress the recovery, which is the failure
mode strictly worse than the one being fixed.

No test sleeps on a cool-down, opens a socket, or reaches a broker API.
LIVE VALIDATION WAS NOT PERFORMED.
"""

import json
import logging

import pytest

from services.market_engine.event_bus import event_bus
from services.market_engine.providers import (
    HEALTH_PROBE_BASE_DELAY,
    Capability,
    ProviderState,
    ResolutionContext,
)
from services.market_engine.providers.base import (
    DEGRADED_AFTER_FAILURES,
    DOWN_AFTER_FAILURES,
)
from services.market_engine.source_manager import (
    FEED_AVAILABLE,
    FEED_RECOVERING,
    FEED_UNAVAILABLE,
    PROVIDER_STATUS_TOPIC,
    FeedChangeReason,
    UnavailableReason,
)

from tests.test_broker_streaming import _clean_provider_registry, run
from tests.test_provider_health_recovery import (
    FlakyPollingProvider,
    HealthyPollingProvider,
    _drive_to_down,
    wired,
)
from tests.test_provider_probation import _tick


STATUS_KEYS = {"state", "tier", "reason", "capabilities"}


class _StatusSpy:
    """Every `provider.status` payload published inside the block."""

    def __init__(self):
        self.events = []

    def __enter__(self):
        async def handler(event):
            self.events.append(event["data"])
        self._handler = handler
        event_bus.subscribe(PROVIDER_STATUS_TOPIC, handler)
        return self

    def __exit__(self, *exc):
        event_bus.unsubscribe(PROVIDER_STATUS_TOPIC, self._handler)
        return False

    def for_user(self, user_id):
        return [e for e in self.events if e.get("user_id") == str(user_id)]


# ==================================================================
# A. LIM-D5.12-1 — a trial that is merely offered is not a live feed
# ==================================================================


def test_a_pending_trial_is_reported_as_recovering_and_not_as_available():
    """A. The headline. Nothing has answered, so nothing may say `available`.

    Health is DOWN and the provider's call count has not moved: the only thing
    that changed between the two reads is that a cool-down elapsed. That is not
    evidence, and the state must not read as though it were.
    """
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        assert manager.status()["state"] == FEED_UNAVAILABLE

        calls_before = flaky.calls
        clock.advance(HEALTH_PROBE_BASE_DELAY)
        during = manager.status()

        assert during["state"] == FEED_RECOVERING, \
            "a trial that has answered nothing is being reported as a live feed"
        assert during["state"] != FEED_AVAILABLE
        assert flaky.health().state is ProviderState.DOWN
        assert flaky.calls == calls_before, "the status read called the provider"


def test_a_pending_trial_claims_no_freshness_tier():
    """A. `tier` is a claim about data. There is no data.

    The pre-D5.13 payload read `tier: "delayed"` here, which the tier indicator
    and the AI's freshness context both consume as "you are on the delayed
    feed" — a statement about a provider that had returned nothing for eight
    consecutive calls.
    """
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        clock.advance(HEALTH_PROBE_BASE_DELAY)

        during = manager.status()
        assert during["tier"] is None, \
            f"a freshness tier {during['tier']!r} was claimed for a feed that is not delivering"
        assert during["capabilities"] == [], \
            "a capability was advertised as served by a provider that has answered nothing"


def test_the_outage_publishes_recovering_and_never_available():
    """A. The blink on the consumer bus is no longer an `available` blink.

    D5.12 pinned this pair as `available` → `unavailable`. The pair still
    exists — the surface still tracks resolution — but neither half now claims
    a usable feed.
    """
    flaky = FlakyPollingProvider()
    with _StatusSpy() as spy, wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        spy.events.clear()

        clock.advance(HEALTH_PROBE_BASE_DELAY)
        run(manager.publish_status())
        run(gateway.get_quote("RELIANCE"))
        run(manager.publish_status())

        states = [e["state"] for e in spy.events]
        assert states == [FEED_RECOVERING, FEED_UNAVAILABLE], f"published {states}"
        assert FEED_AVAILABLE not in states, \
            "a total outage told consumers the feed was available"


def test_a_successful_trial_returns_the_feed_to_available():
    """A. `recovering` is a transient state and not a trap.

    The guard must clear on the same evidence every other health claim clears
    on — one real call that succeeded — or it becomes a second, permanent
    exclusion of exactly the kind D5.7 existed to remove.
    """
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        clock.advance(HEALTH_PROBE_BASE_DELAY)
        assert manager.status()["state"] == FEED_RECOVERING

        flaky.failing = False
        run(gateway.get_quote("RELIANCE"))

        after = manager.status()
        assert after["state"] == FEED_AVAILABLE, "a recovered feed stayed reported as recovering"
        assert after["tier"] == "delayed"
        assert after["capabilities"] == ["quotes"]


def test_the_status_surface_does_not_suppress_the_trial_itself():
    """A. The fix is a projection and changes no resolution.

    The failure mode strictly worse than LIM-D5.12-1 is a status surface that
    removes the probe to look tidy: the trial is how a DOWN provider recovers
    at all, so suppressing it restores the ADR-029 deadlock D5.7 closed. The
    probe must still be offered, still be selected and still be charged.
    """
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        clock.advance(HEALTH_PROBE_BASE_DELAY)

        resolution = manager.resolve_feed(Capability.QUOTES)
        assert resolution.available, "the probe was removed from resolution"
        assert resolution.provider is flaky
        assert [p.name for p in resolution.chain] == [flaky.name]

        calls_before = flaky.calls
        run(gateway.get_quote("RELIANCE"))
        assert flaky.calls == calls_before + 1, "the trial was never spent"


def test_a_probe_behind_a_healthy_provider_does_not_make_the_feed_recovering():
    """A. The state follows the *selected* provider, not the presence of a probe.

    A re-admitted provider sits at the tail of the chain. While something
    healthy leads it, the feed is being served perfectly well and reporting
    `recovering` would be the mirror-image lie — an outage announced during
    normal operation.
    """
    flaky = FlakyPollingProvider()
    steady = HealthyPollingProvider()
    with wired(steady, flaky) as (_gateway, manager, _registry, clock):
        # Through the manager's own bookkeeping, because the gateway only ever
        # calls the chain head and `steady` is answering every request.
        for _ in range(DOWN_AFTER_FAILURES):
            manager.record_failure(flaky, RuntimeError("upstream 503"))
        assert flaky.health().state is ProviderState.DOWN

        clock.advance(HEALTH_PROBE_BASE_DELAY)
        chain = manager.resolve_feed(Capability.QUOTES).chain
        assert chain[0] is steady and flaky in chain, "the fixture did not offer the probe"

        status = manager.status()
        assert status["state"] == FEED_AVAILABLE, \
            "a served feed was reported as recovering because a probe sat behind it"
        assert status["tier"] == "delayed"


def test_a_provider_that_has_never_been_called_is_available_not_recovering():
    """A. The guard is DOWN, and it must not be `not UP`.

    The Yahoo baseline is UNKNOWN at process start and stays UNKNOWN until the
    first request. A guard written as "has it proved itself?" would report the
    entire platform as recovering at every boot — a worse and far more visible
    falsehood than the one this sprint removes. UNKNOWN has always been a
    resolvable, ordinary candidate (`HEALTH_RANK` ties it with UP) and stays one.
    """
    fresh = FlakyPollingProvider(failing=False)
    with wired(fresh) as (_gateway, manager, _registry, _clock):
        assert fresh.health().state is ProviderState.UNKNOWN
        assert fresh.calls == 0

        status = manager.status()
        assert status["state"] == FEED_AVAILABLE, \
            "an unproven-but-not-failed provider was reported as recovering at startup"
        assert status["tier"] == "delayed"
        assert status["capabilities"] == ["quotes"]


def test_a_degraded_provider_is_available_not_recovering():
    """A. DEGRADED is a provider that is still serving, and stays `available`.

    Only DOWN is exclusion. Widening the guard to DEGRADED would report an
    outage every time a provider had a single bad call, and would make the
    consumer surface disagree with a resolution that is still handing that
    provider every request.
    """
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, _clock):
        for _ in range(DEGRADED_AFTER_FAILURES):
            run(gateway.get_quote("RELIANCE"))
        assert flaky.health().state is ProviderState.DEGRADED

        status = manager.status()
        assert status["state"] == FEED_AVAILABLE
        assert status["tier"] == "delayed"


def test_the_recovering_payload_carries_no_provider_identity_or_new_key():
    """A. The third state is a new *value*, not a new shape.

    `status()` is the payload Developer Rule 4 governs. A state added to it may
    not smuggle a provider name, a health enum or a cool-down in beside it.
    """
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        clock.advance(HEALTH_PROBE_BASE_DELAY)
        during = manager.status()

    assert set(during) == STATUS_KEYS, f"the payload grew {set(during) - STATUS_KEYS}"
    blob = json.dumps(during).lower()
    for forbidden in ("flaky", "baseline", "yahoo", "probe", "cool", "down", "health"):
        assert forbidden not in blob, f"{forbidden!r} reached the consumer payload"


# ==================================================================
# B. LIM-D5.5-2 — a tier that moved, and why
# ==================================================================


def _entitlement_fixture(users=("u1",)):
    """The real registry with the baseline and one READY streaming feed per user.

    Built through `attach_market_feed` and driven to READY with a real tick, so
    the feed is genuinely serving `streaming` before it is refused — which is
    the state LIM-D5.5-2 is about. A feed that never got promoted loses nothing
    visible except the `ticks` capability.
    """
    from tests.test_provider_entitlement import _market_fixture
    from services.brokers.market_feed import publish_market_ticks

    manager, baseline, feeds = _market_fixture(users=users)
    for user in users:
        run(publish_market_ticks(user, "nova", [_tick()]))
    return manager, baseline, feeds


def test_an_entitlement_refusal_explains_the_tier_it_moved():
    """B. The headline. `streaming` → `delayed` now says why.

    Before D5.13 this event carried `state: available, tier: delayed,
    reason: null` and the user learned only that something had changed. The
    explanation existed, but only in an audit row no consumer can read.
    """
    from tests.test_provider_entitlement import (
        DEFAULT_STREAM_CHANNEL, _engine, entitlement_nova,
    )

    with entitlement_nova(), _clean_provider_registry() as registry, _StatusSpy() as spy:
        registry.clear()
        manager, _baseline, _feeds = _entitlement_fixture()
        assert manager.status(user_id="u1")["tier"] == "streaming", "the fixture never promoted"
        spy.events.clear()

        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))

        scoped = spy.for_user("u1")
        assert scoped, "the owner was never told their tier moved"
        assert scoped[-1]["tier"] == "delayed"
        assert scoped[-1]["change_reason"] == FeedChangeReason.ENTITLEMENT_REFUSED.value, \
            "the user was told their tier moved and not why"
        assert scoped[-1]["previous_tier"] == "streaming"


def test_the_explained_event_is_not_beaten_to_the_bus_by_the_teardown():
    """B. The defect the audit found while wiring the reason through.

    `unregister_streaming_provider` disconnects the provider before it
    publishes. Disconnecting drives readiness backwards, which fired
    `_on_provider_readiness` and published the user-scoped demotion *from
    inside the teardown* — unexplained. Change gating then suppressed the real,
    explained publish as a repeat, so the reason never reached the bus at all
    and the sprint's whole point was silently a no-op.

    The assertion is deliberately about the *pair*: exactly one user-scoped
    event, carrying both the tier it came from and the reason it moved. Two
    events — one with `previous_tier` and one with `change_reason` — would be a
    worse contract than the one this sprint set out to fix.
    """
    from tests.test_provider_entitlement import (
        DEFAULT_STREAM_CHANNEL, _engine, entitlement_nova,
    )

    with entitlement_nova(), _clean_provider_registry() as registry, _StatusSpy() as spy:
        registry.clear()
        _entitlement_fixture()
        spy.events.clear()
        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))
        scoped = spy.for_user("u1")

    assert len(scoped) == 1, \
        f"the owner got {len(scoped)} events for one demotion: {scoped}"
    assert scoped[0]["previous_tier"] == "streaming"
    assert scoped[0]["tier"] == "delayed"
    assert scoped[0]["change_reason"] == FeedChangeReason.ENTITLEMENT_REFUSED.value


def test_the_explanation_rides_the_event_and_never_the_steady_state():
    """B. A reason belongs to a transition, not to a feed.

    `status()` answers "what is serving me now". A consumer that reconnects an
    hour after a refusal and reads it must get the plain baseline answer, not
    an explanation of something that has stopped being news. Keeping the reason
    off `status()` is also what keeps it out of the change-gating comparison,
    so an unchanged feed still publishes nothing.
    """
    from tests.test_provider_entitlement import (
        DEFAULT_STREAM_CHANNEL, _engine, entitlement_nova,
    )

    with entitlement_nova(), _clean_provider_registry() as registry:
        registry.clear()
        manager, _baseline, _feeds = _entitlement_fixture()
        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))

        after = manager.status(user_id="u1")

    assert set(after) == STATUS_KEYS, \
        f"a transition reason leaked into the steady state: {set(after) - STATUS_KEYS}"


def test_the_refusal_reason_is_broker_neutral():
    """B. The vocabulary is the platform's, not the broker's.

    `entitlement_refused` is a statement about the *feed*. Widening it to carry
    what the broker actually said — a wire code, an error string, the broker's
    name — would put broker vocabulary on the one surface Developer Rule 4
    exists to keep free of it, and would make the field un-renderable for any
    broker whose codes nobody has read.
    """
    from tests.test_provider_entitlement import (
        DEFAULT_STREAM_CHANNEL, _engine, entitlement_nova,
    )

    with entitlement_nova(), _clean_provider_registry() as registry, _StatusSpy() as spy:
        registry.clear()
        _entitlement_fixture()
        spy.events.clear()
        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))
        scoped = spy.for_user("u1")

    assert scoped
    blob = json.dumps(scoped).lower()
    for forbidden in ("nova", "dhan", "zerodha", "upstox", "806", "not_subscribed",
                      "access_token", "api_key", "wss://", "token", "feed:"):
        assert forbidden not in blob, f"{forbidden!r} reached a consumer surface"


def test_a_refusal_is_explained_to_its_owner_and_to_nobody_else():
    """B. One user's explanation is not another user's event.

    A broker refusal is a statement about one account. A second user of the
    same broker holds a different feed this path cannot reach, and must be told
    nothing — neither a tier change nor a reason for one.
    """
    from tests.test_provider_entitlement import (
        DEFAULT_STREAM_CHANNEL, _engine, entitlement_nova,
    )

    with entitlement_nova(), _clean_provider_registry() as registry, _StatusSpy() as spy:
        registry.clear()
        _entitlement_fixture(users=("u1", "u2"))
        spy.events.clear()
        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))
        explained = {e["user_id"] for e in spy.events
                     if e.get("change_reason") and e.get("user_id")}
        others = [e for e in spy.events if e.get("user_id") == "u2"]

    assert explained == {"u1"}, f"the refusal was explained to {explained}"
    assert not others, "a user was told about a refusal that was not theirs"


def test_a_platform_wide_status_never_carries_a_users_transition_reason():
    """B. The reason is user-scoped, like the transition it explains.

    Unregistering a feed also republishes the *platform* view, because the
    registry changed. That view is broadcast to every consumer, and one
    account's entitlement is not news about the platform's feed.
    """
    from tests.test_provider_entitlement import (
        DEFAULT_STREAM_CHANNEL, _engine, entitlement_nova,
    )

    with entitlement_nova(), _clean_provider_registry() as registry, _StatusSpy() as spy:
        registry.clear()
        _entitlement_fixture()
        spy.events.clear()
        run(_engine()._on_stream_not_entitled("u1", "nova", DEFAULT_STREAM_CHANNEL))
        platform = [e for e in spy.events if not e.get("user_id")]

    assert all(e.get("change_reason") is None for e in platform), \
        "a broadcast payload carried one account's entitlement refusal"


def test_an_expired_session_and_a_disconnect_carry_their_own_reasons():
    """B. Three detaches, three causes, one vocabulary.

    An entitlement refusal, an expired token and a user removing the account
    all unregister the feed and all move the tier. Collapsing them to one
    reason would tell a user whose token expired that their broker refused
    them, which is a different problem with a different fix.
    """
    assert {r.value for r in FeedChangeReason} == {
        "entitlement_refused", "session_expired", "feed_disconnected"}, \
        "the transition vocabulary changed without a decision"

    from tests.test_provider_entitlement import _engine, entitlement_nova

    with entitlement_nova(), _clean_provider_registry() as registry, _StatusSpy() as spy:
        registry.clear()
        _entitlement_fixture()
        spy.events.clear()
        run(_engine()._on_stream_expired("u1", "nova"))
        scoped = spy.for_user("u1")

    assert scoped, "an expired session told the owner nothing"
    assert scoped[-1]["change_reason"] == FeedChangeReason.SESSION_EXPIRED.value


def test_the_transition_vocabulary_is_closed_against_a_broker_string():
    """B. The neutrality is enforced, not merely observed.

    Every existing call site passes a :class:`FeedChangeReason` member, so a
    test that only reads what those call sites produce would stay green if the
    coercion in `publish_status` were deleted — it would prove the current
    callers are well-behaved and nothing about the contract. This asserts the
    guard itself: a broker's own vocabulary, handed to this surface by a future
    caller who has an error code in scope and a field that looks like it wants
    one, is refused rather than published.
    """
    flaky = FlakyPollingProvider(failing=False)
    with wired(flaky) as (_gateway, manager, _registry, _clock):
        with pytest.raises(ValueError):
            run(manager.publish_status(force=True,
                                       change_reason="nova_not_subscribed_806"))


def test_an_unexplained_status_change_still_publishes_without_a_reason():
    """B. The field is optional and its absence is not an error.

    Most tier movements — a promotion, a stale-feed demotion, a link drop —
    have no single cause worth naming, and inventing one for them would be
    fabricated provenance. Those keep publishing exactly the payload they
    published before D5.13, with `change_reason` absent rather than guessed.
    """
    flaky = FlakyPollingProvider(failing=False)
    with _StatusSpy() as spy, wired(flaky) as (_gateway, manager, _registry, _clock):
        run(manager.publish_status(force=True))

    assert spy.events
    assert spy.events[-1].get("change_reason") is None
    assert set(spy.events[-1]) == STATUS_KEYS | {"previous_tier"}, \
        "the unexplained payload shape changed"


# ==================================================================
# C. Invariants D5.2–D5.12 hold across the change
# ==================================================================


def test_a_healthy_yahoo_baseline_is_untouched_by_the_change():
    """C. Yahoo remains a valid fallback and reports exactly what it did.

    The baseline is the provider every one of these paths falls back to, and it
    is the one a `recovering` state written even slightly too wide would
    silently take out of service on the consumer surface.
    """
    from services.market_engine.providers import YahooPollingAdapter

    baseline = YahooPollingAdapter()
    with wired(baseline) as (_gateway, manager, _registry, _clock):
        run(baseline.connect())
        status = manager.status()

    assert status["state"] == FEED_AVAILABLE
    assert status["tier"] == "delayed"
    assert status["reason"] is None
    assert "quotes" in status["capabilities"]

    baseline.record_success()
    assert baseline.health().state is ProviderState.UP


def test_probation_still_ranks_and_never_filters_the_consumer_state():
    """C. D5.2's invariant. A probationary feed is serving, and says so.

    Probation orders candidates; it has never removed one. A `recovering` state
    keyed off anything but health would turn D5.2's ranking term into a filter
    on the consumer surface — the exact conversion ADR-039 forbade.
    """
    from tests.test_provider_probation import _fixture

    _registry, manager, _baseline, feed, _clock = _fixture()
    run(feed.on_raw([_tick()]))
    assert feed.is_on_probation, "the fixture did not leave the feed on probation"

    status = manager.status(user_id="u1")
    assert status["state"] == FEED_AVAILABLE
    # `delayed` is D5.2 working: probation is a ranking term, so the unproven
    # feed loses the QUOTES tie to the baseline that has served its window.
    assert status["tier"] == "delayed"
    # But it was ranked, not filtered — `ticks` is a capability only this feed
    # serves, and it is still advertised. A `recovering` state keyed off
    # anything but health would have removed it here, converting D5.2's ranking
    # term into a filter on the consumer surface.
    assert "ticks" in status["capabilities"], \
        "a probationary feed was filtered out of the consumer's capability list"
    assert feed in manager.resolve_feed(
        Capability.QUOTES, ResolutionContext(user_id="u1")).chain, \
        "a probationary feed was removed from the failover chain"


def test_an_unresolvable_feed_still_reports_its_diagnosis():
    """C. The unavailable path keeps every reason D2 gave it.

    `recovering` is inserted between `available` and `unavailable`; it does not
    replace either, and it must not swallow the four-way diagnosis an operator
    reads to tell "nothing registered" from "this user is entitled to nothing".
    """
    with wired() as (_gateway, manager, _registry, _clock):
        empty = manager.status()
    assert empty["state"] == FEED_UNAVAILABLE
    assert empty["reason"] == UnavailableReason.NO_PROVIDERS_REGISTERED.value

    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, _clock):
        _drive_to_down(gateway, flaky)
        down = manager.status()
    assert down["state"] == FEED_UNAVAILABLE
    assert down["reason"] == UnavailableReason.ALL_PROVIDERS_DOWN.value


def test_the_admin_diagnostics_surface_keeps_the_detail_the_consumer_does_not():
    """C. The two surfaces stay separate and stay in agreement.

    `diagnostics()` embeds `status()`, so an operator sees the same state the
    consumer does *plus* the provider names and cool-downs that explain it —
    which is where "why is my feed recovering?" is answerable and where it must
    stay answerable.
    """
    flaky = FlakyPollingProvider()
    with wired(flaky) as (gateway, manager, _registry, clock):
        _drive_to_down(gateway, flaky)
        clock.advance(HEALTH_PROBE_BASE_DELAY)
        diag = manager.diagnostics()

    assert diag["feed"]["state"] == FEED_RECOVERING
    assert diag["selected_for_quotes"] == "flaky_baseline", \
        "the admin surface lost the provider identity it is allowed to have"
    assert diag["health_recovery"], "the cool-down ladder vanished from diagnostics"


def test_the_whole_surface_survives_debug_logging_with_live_looking_credentials():
    """C / SECURITY.md. Exercise the real logging stack, not a format string.

    `publish_status` logs every transition at INFO. A handler that formats
    lazily can render a value no unit assertion on the message template would
    ever see, so this runs the real logger at DEBUG with a credential-shaped
    string planted in the provider's own identity.
    """
    secret = "eyJhbGciOiJIUzI1NiJ9.live-access-token-value"
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = Capture()
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        flaky = FlakyPollingProvider(name=f"feed:u1:nova?token={secret}")
        with _StatusSpy() as spy, wired(flaky) as (gateway, manager, _registry, clock):
            run(manager.publish_status(force=True))
            _drive_to_down(gateway, flaky)
            clock.advance(HEALTH_PROBE_BASE_DELAY)
            run(manager.publish_status())
            payloads = json.dumps(spy.events)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)

    assert secret not in payloads, "a credential reached the consumer payload"
    assert "feed:u1:nova" not in payloads, "provider identity reached the consumer payload"
    assert any(FEED_RECOVERING in line for line in records), \
        "the transition was never logged, so this proved nothing"
    assert not any(secret in line for line in records if "provider.status" in line.lower())


def test_the_source_manager_still_imports_no_broker_module():
    """C. The transition vocabulary lives on the consumer side of the wall.

    `FeedChangeReason` is named by the broker layer and defined by the Market
    Engine. Defining it the other way round — or importing the broker's own
    error taxonomy to build it — would be the import the platform has forbidden
    since D3, and would make the reason broker-specific by construction.
    """
    import ast
    import pathlib

    source = (pathlib.Path(__file__).resolve().parent.parent
              / "services/market_engine/source_manager.py").read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not any(name.startswith("services.brokers") or name == "services.broker_engine"
                   for name in imported), f"the Market Engine imports a broker module: {imported}"

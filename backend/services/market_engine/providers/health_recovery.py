"""Health recovery — how a provider excluded at DOWN is allowed back (D5.7).

THE DEADLOCK THIS CLOSES
------------------------
ADR-029 recorded it and D5.6 classified it without giving it a caller
(LIM-D5.6-1)::

    health reaches DOWN
          ↓
    excluded from candidates_for()
          ↓
    never selected, therefore never called
          ↓
    health improves only on a call
          ↓
    DOWN forever

For a pushed feed the cycle does not close — `_ingest_ticks` records a success
whenever a tick batch is accepted, so evidence arrives without selection, which
is the same structural property ADR-044 relied on for delivery latency. For a
**polled** provider it closes completely, and the only polled provider the
platform has is the permanent baseline. So the untreated case is not a corner
case: it is a total feed outage that survives until the process restarts.

WHY THIS IS NOT A NEW MECHANISM, AND NOT A TIMER
------------------------------------------------
MARKET_DATA_ARCHITECTURE.md's Resolution procedure, step 2, has always read:

    Filter out candidates whose health state is `down` **or that are inside a
    failure cool-down**.

D1 implemented the first half as an unconditional filter and the second half
not at all, which is exactly how a cool-down became a permanent exclusion. This
module supplies the missing half. It adds no schedule, no sweeper and no task:
the cool-down is *read* on the resolution path that already runs on every
request, and it is *charged* by the outcome of a call that actually happened.

That is what keeps it from competing with D5.6's `RecoveryClass.REPROBE`
(`services/brokers/recovery.py`), which is a different question asked at a
different layer about a different unit:

    D5.6 REPROBE    "has a provider-level condition — an entitlement, a licence
                     — changed?"  Unit: (user, broker, channel). Answered by
                     performing one ordinary *attach*. Needs a schedule, because
                     nothing in this process observes an entitlement changing.

    D5.7 cool-down  "is a provider that failed its way out of the candidate list
                     answering again?"  Unit: one registered provider. Answered
                     by *the next request that needs it*. Needs no schedule,
                     because the platform is already asking this provider's
                     question several times a minute — it had simply stopped
                     including it in the ask.

Neither module imports the other and neither can: the Market Engine may not
import the broker layer at all (pinned by
`test_the_market_engine_never_imports_a_broker_module`). A withdrawn *stream*
and a demoted *provider* stay separate mechanisms, which is what D5.6's
taxonomy was for.

WHAT A RE-ADMITTED PROVIDER GETS, AND WHAT IT DOES NOT
-------------------------------------------------------
It gets one thing: a place at the **tail** of the failover chain. It does not
get its health back, it is not treated as UP, and nothing about it is assumed.

  * Health is unchanged by re-admission. `DOWN` stays `DOWN` until a real
    `record_success` from a real call — the same evidence every other provider
    needs — so "re-admitted" and "recovered" are different facts.
  * It ranks last, by construction rather than by a special case: `HEALTH_RANK`
    gives DOWN the worst band and health is the *first* element of the selection
    key, so no probation state and no latency can lift a re-admitted provider
    past a healthy or a probationary one.
  * It still passes every other filter. Entitlement, capability, readiness and
    per-symbol coverage are asked exactly as they are for any candidate — a
    re-admitted feed that is not READY is still not eligible, and a feed
    belonging to another user is still invisible.
  * It costs at most one call per cool-down, because the ladder is charged by a
    failed call and not by the offer. A provider that is offered and never
    reached — because something healthier answered — costs nothing at all.

WHAT IT CANNOT REACH
--------------------
A feed whose *entitlement* was refused (D5.5) or whose *session* expired is
**unregistered**, not demoted — `detach_market_feed` removes it from the
registry entirely. It is therefore not a provider this module can see, let
alone re-admit, and its way back is D5.6's re-probe or a new session. That
separation is structural rather than a rule written here.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

from infrastructure.health_state import (
    SharedHealthStore,
    provider_key,
    shared_health_store,
)
from services.market_engine.providers.base import MarketDataProvider

logger = logging.getLogger(__name__)


#: How long a provider stays excluded after entering DOWN, in seconds.
#:
#: A new number, and said so rather than dressed up as a reused one. Nothing the
#: platform already publishes measures this: D5.1/D5.2's 30 seconds is *one
#: window measured two ways* on a socket and its data, D5.3's 120 seconds is how
#: old a price may be, and D5.6's 300 seconds is how long to wait before asking a
#: human-timescale question again. Borrowing any of them would be the mistake
#: ADR-044 named when it declined to reuse a health threshold as a latency
#: window: two policies pinned together by nothing but a shared constant.
#:
#: The justification is what DOWN *is*. It is `DOWN_AFTER_FAILURES` (8)
#: consecutive failed calls — a rate limit, a 5xx spell, a DNS wobble — and for
#: the baseline it means the user is currently seeing no live data at all. So the
#: cost of waiting is at its maximum and the cost of being wrong is one HTTP
#: call. Sixty seconds is short enough that a transient provider outage clears
#: within about one probe of a user noticing, and long enough that a hard outage
#: costs one call per minute per provider instead of one call per request —
#: which is the property the unconditional DOWN filter was protecting, and the
#: only thing it was protecting.
HEALTH_PROBE_BASE_DELAY = 60.0

#: The ceiling of the cool-down ladder, in seconds. Four minutes.
#:
#: Chosen so that **the slowest health cool-down is still faster than the
#: fastest D5.6 re-probe** (300s), which is the same form of pin ADR-046 used to
#: keep the reconnect and re-probe ladders from collapsing into one — and it is
#: derived from what the two mechanisms measure rather than picked to satisfy
#: the inequality. A DOWN provider is a machine-timescale condition: this
#: process's own calls are failing and this process will see them succeed. An
#: unresolved re-probe is a human-timescale condition: somebody has to change an
#: entitlement before the answer can change. A schedule for the first that ran
#: slower than the schedule for the second would have the ordering backwards.
#:
#: Bounded in the direction that matters: it is a ceiling and never a give-up,
#: because nothing else in the platform will ever notice on its own that a
#: polled baseline came back.
HEALTH_PROBE_MAX_DELAY = 240.0


#: (owner scope, provider name). The owner is part of the key rather than
#: implied by the name, so a per-user feed's cool-down can never be read or
#: charged on behalf of another user even if two accounts' feeds were ever
#: minted with the same provider name. Per-user isolation is then a property of
#: the key, not of a naming convention that a future sprint could change.
ProbeKey = Tuple[str, str]


@dataclass(frozen=True)
class ProbeClaims:
    """Which DOWN providers this worker holds a trial for right now (D5.8).

    The result of one atomic claim round, passed into :meth:`due_from` so that
    the *decision* is made once, in an awaitable place, and the sync resolution
    path only reads it. That split is what let DB-1 land without changing
    `resolve_feed`'s signature or making the resolution path async.

    `distributed` records whether the shared store actually answered. It is not
    decoration: it is the difference between "no worker may try this provider"
    and "Redis is away and this worker fell back to its own cool-down", and the
    diagnostics surface has to be able to tell an operator which one happened.
    """

    granted: FrozenSet[ProbeKey] = frozenset()
    distributed: bool = False


@dataclass
class HealthProbe:
    """One DOWN provider's cool-down: how many probes it has cost, and when the
    next one is allowed."""

    user_scope: str
    provider_name: str
    #: Failed probes charged against this provider since it was last healthy.
    attempts: int = 0
    #: Monotonic instant at which re-admission becomes allowed.
    next_probe_at: float = 0.0

    @property
    def key(self) -> ProbeKey:
        return (self.user_scope, self.provider_name)

    def describe(self, *, now: float) -> Dict[str, Any]:
        """Diagnostics only. Carries the provider name, so this reaches an
        operator's surface and the logs and never a consumer payload
        (Developer Rule 4)."""
        return {
            "provider": self.provider_name,
            "scope": "global" if not self.user_scope else "user",
            "attempts": self.attempts,
            "due_in_seconds": max(0.0, self.next_probe_at - now),
        }


class ProviderHealthRecovery:
    """The failure cool-down that step 2 of the Resolution procedure names.

    Owned by the Source Manager, consulted on the resolution path, and charged
    on the health-bookkeeping path. It holds no provider references — only keys
    — so a provider that is unregistered and garbage-collected leaves a few
    bytes behind rather than a live object.
    """

    def __init__(
        self,
        *,
        base_delay: float = HEALTH_PROBE_BASE_DELAY,
        max_delay: float = HEALTH_PROBE_MAX_DELAY,
        clock: Callable[[], float] = time.monotonic,
        store: Optional[SharedHealthStore] = None,
    ) -> None:
        self._base_delay = float(base_delay)
        self._max_delay = float(max_delay)
        #: The shared cool-down (D5.8). Injected so a test can supply a store
        #: pointed at its own Redis, and so a deployment with no Redis keeps the
        #: process-local behaviour D5.7 shipped without a branch anywhere else.
        self._store = store if store is not None else shared_health_store
        #: Monotonic and injectable, never wall-clock: a cool-down is a
        #: duration, and a clock an NTP step can move backwards would re-admit a
        #: provider that has served no cool-down at all.
        self._clock = clock
        self._probes: Dict[ProbeKey, HealthProbe] = {}

    # ── The resolution path reads this ───────────────────

    def due_from(
        self,
        providers: Iterable[MarketDataProvider],
        *,
        claims: Optional[ProbeClaims] = None,
    ) -> List[MarketDataProvider]:
        """Which of these DOWN providers may be tried again now, in input order.

        `providers` are the ones that passed every filter *except* health — the
        registry decides that, this decides only whether the cool-down has run.

        A provider seen here for the first time is **armed and refused**: it gets
        a cool-down starting now and is not returned. That is what makes the
        mechanism independent of who wrote the DOWN state. Health can be driven
        by the gateway, by a test, or by any future caller; the cool-down is a
        property of the provider *being* DOWN, and reading it is what starts it,
        so there is no path by which a provider becomes DOWN without one.

        `claims` is D5.8's shared answer to the same question, produced by
        :meth:`claim_due` on the awaitable path just above this one. When it is
        supplied this method makes no decision at all — it filters — which is
        the property that stops the two from disagreeing. Without it the
        behaviour is exactly D5.7's, which is what a single-process deployment
        and every existing caller still get.
        """
        if claims is not None:
            granted = claims.granted
            return [p for p in providers if self._key(p) in granted]

        now = self._clock()
        due: List[MarketDataProvider] = []
        for provider in providers:
            probe = self._probes.get(self._key(provider))
            if probe is None:
                self._arm(provider, now=now)
                continue
            if now >= probe.next_probe_at:
                due.append(provider)
        return due

    async def claim_due(self, providers: Iterable[MarketDataProvider]) -> ProbeClaims:
        """Take one exclusive trial per DOWN provider that is due (D5.8).

        The load-bearing method of DB-1. D5.7 promised "at most one trial per
        cool-down" and delivered it per *worker*; with N workers a genuinely
        down provider was retried N times per cool-down. Here the decision is one
        atomic Redis script per provider, so exactly one worker is told `claimed`
        and every other is told the trial is already taken.

        WHAT IS CLAIMED IS THE OFFER, NOT THE LADDER
        --------------------------------------------
        D5.7's rule that the ladder is charged by evidence and never by the offer
        is preserved exactly: a claim takes a short lease
        (`TRIAL_LEASE_SECONDS`) on the *right to offer* the provider and does not
        touch `attempts` or the next-probe instant. A worker that is granted the
        trial and never reaches the provider — because something healthier
        answered first — has still cost nothing, and the lease simply lapses. A
        worker that does reach it and fails charges the ladder through
        :meth:`note_probe_failed_shared`, which is the only thing that climbs it.

        Providers that opt out of shared health (`health_is_shared = False` — a
        live socket, see `StreamingTickProvider`) are decided locally in the same
        pass, so a mixed candidate list produces one claim set and there is never
        a second place where "may this be tried" is answered.

        Falls back to the local cool-down for the whole shared set when Redis
        does not answer. That is a deliberate return to D5.7's semantics for the
        duration of the outage — see REDIS UNAVAILABLE in
        `infrastructure/health_state.py` — and never a decision to try
        everything or to try nothing.
        """
        candidates = list(providers)
        if not candidates:
            return ProbeClaims()

        local = [p for p in candidates if not p.health_is_shared]
        shared = [p for p in candidates if p.health_is_shared]
        granted = {self._key(p) for p in self.due_from(local)}

        distributed = False
        if shared:
            ok, claims = await self._store.claim_trials(
                [self._store_key(p) for p in shared],
                base_delay=self._base_delay,
                max_delay=self._max_delay,
            )
            if ok:
                distributed = True
                by_store_key = {self._store_key(p): p for p in shared}
                for store_key, claim in claims.items():
                    provider = by_store_key.get(store_key)
                    if provider is not None and claim.granted:
                        granted.add(self._key(provider))
            else:
                granted |= {self._key(p) for p in self.due_from(shared)}

        return ProbeClaims(granted=frozenset(granted), distributed=distributed)

    # ── The health-bookkeeping path charges this ─────────

    def note_probe_failed(self, provider: MarketDataProvider) -> HealthProbe:
        """Charge one failed call against a provider that is (still) DOWN.

        Called from `SourceManager.record_failure` once the provider's own
        counters have been updated, so "is it DOWN" is read from the provider
        rather than inferred from the exception. Every failure while DOWN climbs
        the ladder — the eighth consecutive failure that *creates* the DOWN
        state and a failed probe afterwards are the same event to this module,
        which is why the first cool-down and the first re-probe delay are one
        number rather than two.
        """
        now = self._clock()
        probe = self._probes.get(self._key(provider))
        if probe is None:
            probe = self._arm(provider, now=now)
            return probe
        probe.attempts += 1
        probe.next_probe_at = now + self._delay(probe.attempts)
        logger.info(
            "Provider %s stays down after probe %d — next re-admission in %.0fs",
            provider.name, probe.attempts, probe.next_probe_at - now,
        )
        return probe

    async def note_probe_failed_shared(self, provider: MarketDataProvider) -> None:
        """Charge one failed probe against the shared ladder (D5.8).

        The distributed twin of :meth:`note_probe_failed`, and the only thing
        that climbs the shared ladder — a claim never does, because D5.7 charges
        by evidence and not by the offer. The same write releases the trial
        lease, so the next cool-down's trial is available the moment it is due
        rather than a lease later.

        Falls back to the local ladder when the store does not answer, so a
        Redis outage during a probe still paces this worker.
        """
        if provider.health_is_shared:
            ok, _ = await self._store.note_trial_failed(
                self._store_key(provider),
                base_delay=self._base_delay,
                max_delay=self._max_delay,
            )
            if ok:
                return
        self.note_probe_failed(provider)

    async def note_recovered_shared(self, provider: MarketDataProvider) -> bool:
        """Drop the shared cool-down because the provider answered (D5.8).

        Clears the local entry too, unconditionally. A worker that recovered a
        provider must not keep a stale local cool-down that would exclude it
        again the moment Redis went away.
        """
        cleared = self.note_recovered(provider)
        if provider.health_is_shared:
            ok, removed = await self._store.clear_trial(self._store_key(provider))
            cleared = cleared or bool(ok and removed)
        return cleared

    async def forget_shared(self, provider: MarketDataProvider) -> bool:
        """Drop this provider's records everywhere. Unregistration only.

        Both the shared cool-down and the shared health record go, because an
        unregistered provider's counters are evidence about a subject that no
        longer exists — and a per-user feed for a closed account would otherwise
        hold a key until its TTL, in every worker's view, for an hour.
        """
        cleared = self.forget(provider)
        if provider.health_is_shared:
            ok, removed = await self._store.forget(self._store_key(provider))
            cleared = cleared or bool(ok and removed)
        return cleared

    def note_recovered(self, provider: MarketDataProvider) -> bool:
        """Drop a provider's cool-down because it answered. True when one went.

        Called from `SourceManager.record_success`, and deliberately guarded by
        the provider's own state there rather than by the fact a call returned:
        an *empty* success does not clear the failure streak (a provider
        answering 200-with-no-data is not healthy), so it must not clear the
        cool-down either.
        """
        probe = self._probes.pop(self._key(provider), None)
        if probe is None:
            return False
        logger.info(
            "Provider %s recovered after %d failed probe(s) — cool-down cleared",
            provider.name, probe.attempts,
        )
        return True

    # ── Lifecycle and diagnostics ────────────────────────

    def forget(self, provider: MarketDataProvider) -> bool:
        """Drop a provider's cool-down without claiming it recovered.

        Called when a provider is unregistered. A re-registered feed is a new
        instance with fresh UNKNOWN health, so a surviving entry would be inert
        rather than wrong — this keeps the map from growing one entry per feed
        that has ever been down for the life of the process, which is the same
        unbounded-growth trap `forget_user_status` avoids.
        """
        return self._probes.pop(self._key(provider), None) is not None

    def probe_for(self, provider: MarketDataProvider) -> Optional[HealthProbe]:
        """This provider's cool-down, or None. Diagnostics and tests."""
        return self._probes.get(self._key(provider))

    def describe(self) -> List[Dict[str, Any]]:
        """Every outstanding cool-down. Admin diagnostics and logs only."""
        now = self._clock()
        return [probe.describe(now=now) for probe in self._probes.values()]

    def clear(self) -> None:
        """Drop every cool-down. Startup and tests only."""
        self._probes.clear()

    # ── Internals ────────────────────────────────────────

    def _arm(self, provider: MarketDataProvider, *, now: float) -> HealthProbe:
        probe = HealthProbe(
            user_scope=self._scope(provider),
            provider_name=provider.name,
            attempts=1,
            next_probe_at=now + self._delay(1),
        )
        self._probes[probe.key] = probe
        logger.info(
            "Provider %s is down — excluded for %.0fs before it may be tried again",
            provider.name, probe.next_probe_at - now,
        )
        return probe

    def _delay(self, attempts: int) -> float:
        """Base delay doubling per failed probe, capped.

        No jitter, and that is a decision rather than an omission. Jitter
        decorrelates a *herd* released by a shared schedule; there is no
        schedule here. Each worker arms its own cool-down at the instant its own
        eighth failure landed and consumes it on its own next request, so the
        probes are already spread by request arrival. Adding jitter would mean a
        third copy of D5.1's arithmetic, in a layer that may not import the one
        that holds it, to decorrelate something that is not correlated.
        """
        return min(self._base_delay * (2 ** max(0, attempts - 1)), self._max_delay)

    @staticmethod
    def _store_key(provider: MarketDataProvider):
        """This provider's key in the shared store (D5.8).

        Built from the same two facts as :meth:`_key` — owner scope and provider
        name — so the local and the shared cool-down are scoped identically and
        a fallback in either direction lands on the same subject.
        """
        return provider_key(provider.name, ProviderHealthRecovery._scope(provider))

    @staticmethod
    def _key(provider: MarketDataProvider) -> ProbeKey:
        return (ProviderHealthRecovery._scope(provider), provider.name)

    @staticmethod
    def _scope(provider: MarketDataProvider) -> str:
        owner = provider.owner_user_id
        return str(owner) if owner else ""

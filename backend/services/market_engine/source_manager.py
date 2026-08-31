"""Source Manager — decides which provider answers a market data request.

MARKET_DATA_ARCHITECTURE.md gives this service one job stated three ways: for
every user and every moment, know which provider is the right one; keep that
decision current as conditions change; and make sure nothing downstream can tell
which way it went. The Market Gateway *executes* what this service decides and
holds no priority logic of its own.

The resolution path, end to end:

    capability + context
        -> registry.candidates_for()      entitlement, capability, health
        -> health ranking                 UP/UNKNOWN before DEGRADED
        -> probation ranking              proved before merely usable (D5.2)
        -> Resolution(provider, chain)    selected + ordered failover chain
        -> Market Gateway

WHAT D2 ADDED TO D1'S FOUNDATION
---------------------------------
D1 built capability-based resolution over the registry, health-based exclusion,
and `provider.status` events. Four things it deliberately left as seams, which
D2 makes real because each one is load-bearing before a second provider exists:

  * **An explicit unavailable result.** D1's `resolve()` returned `None` with no
    reason, so "nobody is registered", "this user is entitled to nothing",
    "no provider serves order-book depth" and "every provider is in outage"
    reached the gateway as the same silence. They are four different incidents
    and an operator reading a log line needs to know which one happened.
    :class:`UnavailableReason` names them; :meth:`resolve_feed` returns one.

  * **A failover chain rather than a single pick.** D1 returned the winner
    alone, so a request that failed on the preferred provider returned nothing
    even when a healthy baseline was sitting one tier below — the feed only
    recovered after the health counter escalated across `DOWN_AFTER_FAILURES`
    consecutive requests. Every one of those requests served a user an empty
    dashboard for an outage the platform could already route around.
    :attr:`Resolution.chain` carries the ordered alternatives so the gateway can
    fail over *within* one request.

  * **A real request context.** D1 accepted `user_id` and ignored it. D2 honours
    it through :meth:`MarketDataProvider.is_eligible_for`, so a provider bound
    to one user (D3's broker adapters) cannot be resolved for anybody else.

  * **`UNKNOWN` health.** A provider registered a millisecond ago no longer
    reports the same state as one that has served ten thousand clean requests.

WHAT D3 ADDED
-------------
Responsibility 1 of this service in MARKET_DATA_ARCHITECTURE.md — "subscribes to
broker connection lifecycle events ... maintains a per-user registry: which
brokers are connected, authenticated, and streaming-capable right now" — was
unimplementable before D3 for a mundane reason: `broker.connected` and
`broker.disconnected` were documented in BROKER_INTEGRATION.md and never
published by anything. D3's Broker Gateway publishes them, and this service now
subscribes: :meth:`connected_brokers` answers who is connected, and
:meth:`streaming_brokers` answers which of those connections could carry a
market feed, read from the capabilities the event carries rather than by
importing a broker module.

What D3 deliberately does NOT do is register a broker as a *market data
provider*. MARKET_DATA_ARCHITECTURE.md's Category 2 upgrade is a streaming
feed — the broker WebSocket, make-before-break switching, tick normalization —
and D3's brief defers broker streaming to D4. Registering a provider now would
mean either a fabricated streaming tier (forbidden outright by CLAUDE.md's data
rules) or a REST-polled provider silently taking a connected user's quotes away
from the baseline without any of the switching machinery that makes that safe.
The registry this class now keeps is exactly the record D4 attaches that
registration to.

Still deferred, with the sprint that owns each: the streaming push surface,
per-user provider registration and make-before-break switching (D4); probation
windows, latency scoring and flap suppression (D5 / Phase 5 in
MARKET_DATA_ARCHITECTURE.md).

WHY RESOLUTION IS STILL NOT CACHED
-----------------------------------
Every resolution reads the registry and provider health afresh. A cache keyed by
(user, capability) would need invalidating on broker connect, broker disconnect,
token refresh, every health transition and every registry mutation — five
invalidation paths guarding a sorted traversal of a list that currently has one
element. MARKET_DATA_ARCHITECTURE.md calls for caching resolution *per user
session*; that becomes worth its invalidation surface in D3, when a per-user
session with a WebSocket attached actually exists to hang it on.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from infrastructure import health_state
from infrastructure.health_state import (
    SharedHealthStore,
    provider_key,
    shared_health_store,
)
from services.market_engine.event_bus import event_bus
from services.market_engine.providers import (
    DEGRADED_AFTER_FAILURES,
    DOWN_AFTER_FAILURES,
    GLOBAL_CONTEXT,
    Capability,
    MarketDataProvider,
    ProbeClaims,
    ProviderHealthRecovery,
    ProviderRegistry,
    ProviderState,
    ResolutionContext,
    SourceTier,
    provider_registry,
)

logger = logging.getLogger(__name__)

#: Feed state published to consumers. Mirrors the Source Manager state machine
#: in MARKET_DATA_ARCHITECTURE.md, collapsed to what a consumer can act on.
#:
#: D5.13 — WHY THERE ARE THREE OF THESE AND NOT TWO
#: Until D5.13 there were two, on the stated reasoning that a feed "is either
#: being served, or it is not". That is a true dichotomy and it was projected
#: from the wrong question. `as_status()` reported the *resolution* answer —
#: "is there a provider I will try?" — on a surface whose consumers ask the
#: *delivery* question — "is my feed serving me usable data?". For every
#: provider that has passed health, readiness, freshness and coverage the two
#: answers coincide, which is why the collapse survived twelve sprints.
#:
#: They come apart in exactly one place, and D5.7 created it: a provider
#: excluded at DOWN is re-admitted for one trial once its cool-down expires.
#: It is a genuine resolution candidate and a genuine non-answer, so the
#: two-value contract had to call it one of the two and called it `available`
#: — reporting a usable feed, and a freshness `tier` to go with it, about a
#: provider whose health was DOWN and which had returned nothing (LIM-D5.12-1).
#:
#: :data:`FEED_RECOVERING` is that third case named. It is a refinement of
#: *not available*, deliberately: a consumer branching `state == "available"`
#: now takes its degraded branch, which is the safe direction to be wrong in.
#: And it agrees with resolution rather than contradicting it — which is what
#: ADR-052 rejected reporting `unavailable` here for. There *is* a candidate;
#: it simply has not answered yet, and that is what this word says.
FEED_AVAILABLE = "available"
FEED_UNAVAILABLE = "unavailable"
FEED_RECOVERING = "recovering"

#: Topic on the existing Event Bus. Payload carries `tier`, `state`, an
#: unavailability `reason` and — when the caller knows one — a
#: :class:`FeedChangeReason` for the *transition*; never a provider name
#: (Developer Rule 4).
PROVIDER_STATUS_TOPIC = "provider.status"

#: Broker lifecycle topics published by the Broker Gateway (D3). Subscribed
#: rather than called, so the Market Engine never imports a broker module and
#: the broker layer never imports the Market Engine — the two subsystems meet
#: only on the Event Bus.
BROKER_CONNECTED_TOPIC = "broker.connected"
BROKER_DISCONNECTED_TOPIC = "broker.disconnected"

#: The broker capability that makes a connection a candidate market feed. A
#: string, not an import: comparing to `BrokerCapability.TICK_STREAM.value`
#: would mean the Market Engine importing the broker package, which is the
#: coupling the Event Bus boundary exists to avoid. The value is part of the
#: published event contract.
TICK_STREAM_CAPABILITY = "tick_stream"

#: Selection order among providers that survived filtering. Lower wins.
#:
#: UP and UNKNOWN share a rank on purpose. Ranking UNKNOWN below UP would
#: deadlock the priority algorithm: a newly registered priority-1 broker feed
#: leaves UNKNOWN only by being called, and is called only by being selected, so
#: it would sit behind a healthy priority-3 baseline forever and the platform's
#: headline feature would never engage. DEGRADED is different in kind — it is
#: evidence of failure, not absence of evidence — and MARKET_DATA_ARCHITECTURE.md
#: is explicit that it demotes a provider below a healthy lower tier.
HEALTH_RANK = {
    ProviderState.UP: 0,
    ProviderState.UNKNOWN: 0,
    ProviderState.DEGRADED: 1,
    # D5.7. A DOWN provider is normally filtered out before ranking ever sees
    # it; it reaches this table only when its failure cool-down has run and it
    # has been re-admitted for one trial. The band is worst, and because health
    # is the *first* element of the selection key that single fact is the whole
    # of "a re-probed provider can never outrank a healthy or a probationary
    # one" — there is no branch enforcing it, only the position of the element
    # and the value in this row.
    ProviderState.DOWN: 2,
}

#: Selection order among providers of equal health (D5.2). Lower wins.
#:
#: A provider that has proved itself on its current connection is preferred over
#: one that is merely usable — MARKET_DATA_ARCHITECTURE.md's probation window,
#: applied where every other selection rule already lives. `is_on_probation` is
#: a generic property of the provider contract that defaults to False, so this
#: term is inert for the polled baseline and for any provider without a link to
#: prove anything about.
#:
#: WHY IT RANKS *BELOW* HEALTH RATHER THAN ABOVE IT
#: A DEGRADED provider has produced evidence of failure; a probationary one has
#: merely not yet produced evidence of success. Ordering health first keeps
#: MARKET_DATA_ARCHITECTURE.md's published rule intact — "DEGRADED demotes a
#: provider below a healthy lower tier" — and makes probation what it is meant
#: to be: the tie-break that decides which of two equally healthy providers is
#: preferred, not a new way to be excluded.
#:
#: WHY IT IS A RANKING TERM AND NOT A FILTER
#: A filter would let probation produce "no provider at all" whenever the only
#: live feed happened to be young, which trades a cosmetic tier flap for an
#: outage. Ranked instead, a probationary feed sits second in the chain behind
#: the steady source and becomes the head the moment that source stops being a
#: candidate — so the system is never less able to serve data than it was
#: before probation existed.
PROBATION_RANK = {False: 0, True: 1}

#: The sort key a provider whose delivery latency is not established gets (D5.4).
#:
#: Infinity, so it ranks **last within its own (health, probation) group** and
#: nowhere else — it can never move a provider out of the band health and
#: probation put it in. It exists only inside this comparison: nothing
#: serialises it, and `describe()` reports the unestablished case as `None`.
#:
#: WHY LAST RATHER THAN FIRST, WHICH IS THE TEMPTING MISTAKE
#: "Missing evidence should not be penalised" argues for ranking unknown latency
#: *best*, by analogy with `HEALTH_RANK` tying UNKNOWN with UP. That analogy
#: breaks, and breaks expensively: the polled baseline can never establish a
#: delivery latency at all — it is not pushed into, so it has no delivery event
#: to time — so "unknown wins ties" would have promoted Yahoo above every
#: streaming feed sharing its health and probation rank, and silently undone
#: D4.5. Ranked last, the term leaves the baseline exactly where priority
#: already puts it and can never move it.
#:
#: Nor does this recreate ADR-029's UNKNOWN-health deadlock, and the difference
#: is structural rather than lucky. Health improves only by the provider being
#: *called*, so a provider that is never selected can never leave UNKNOWN. A
#: pushed feed accumulates delivery intervals whether or not it is the primary,
#: so evidence arrives without selection and there is no cycle to deadlock.
LATENCY_RANK_UNKNOWN = math.inf


class UnavailableReason(str, Enum):
    """Why no provider could be resolved.

    Diagnostic vocabulary, safe for consumer surfaces: it describes the *feed*,
    never the providers behind it, so it can travel on `provider.status` without
    breaching Developer Rule 4.
    """

    #: Nothing is registered at all — a startup ordering bug, essentially.
    NO_PROVIDERS_REGISTERED = "no_providers_registered"

    #: Providers exist, but none this request is entitled to.
    NOT_ENTITLED = "not_entitled"

    #: Entitled providers exist, none of them serves this capability. The normal
    #: answer for order-book depth until a broker adapter lands in D3.
    CAPABILITY_UNSUPPORTED = "capability_unsupported"

    #: Providers exist and could serve it, but every one of them is DOWN.
    ALL_PROVIDERS_DOWN = "all_providers_down"


class FeedChangeReason(str, Enum):
    """Why a consumer's feed *changed*, when a caller knows.

    D5.13, and the closing half of LIM-D5.5-2. Distinct from
    :class:`UnavailableReason` in the one way that matters: that enum is a
    property of the current resolution and can always be re-derived from the
    registry, while this one describes a *transition* whose cause is gone by
    the time anyone could ask. An entitlement refusal unregisters the feed, so
    a moment later there is no provider left to explain the tier that moved and
    nothing in the Source Manager remembers there ever was one.

    That is why this travels on the `provider.status` **event** and never on
    :meth:`SourceManager.status`. A reason belongs to the change; a consumer
    that reconnects an hour later and reads the steady state must be told what
    is serving it now, not handed an explanation of something that stopped
    being news. Keeping it off `status()` also keeps it out of the
    change-gating comparison, so an unchanged feed still publishes nothing.

    WHY THIS VOCABULARY IS THE PLATFORM'S AND NOT A BROKER'S
    --------------------------------------------------------
    Every value here is a statement about the *feed*: it names what the
    platform did to the user's provider, never what a broker said to cause it.
    That is what lets it sit on the surface Developer Rule 4 governs. The
    broker's own words — a wire code, an error string, the broker's name — stay
    where they already are, in the audit row and the admin diagnostics, because
    a field a consumer can only render for the brokers somebody has read the
    error tables of is not a consumer field.

    Three values because there are exactly three paths that unregister a live
    feed, and they are three different problems with three different fixes.
    Collapsing them would tell a user whose token expired that their broker
    refused them. Widening the enum needs the same bar ADR-042 set for
    `UnavailableReason`: a caller that genuinely knows the cause, and a cause a
    consumer can act on differently.
    """

    #: The broker refused this account the data the feed carries (D5.5).
    #: Terminal for that stream; the way back is a D5.6 re-probe.
    ENTITLEMENT_REFUSED = "entitlement_refused"

    #: The account's session or token expired, so the feed cannot deliver
    #: another tick. The way back is a new session.
    SESSION_EXPIRED = "session_expired"

    #: The user removed the broker account. Nothing is wrong and nothing is
    #: coming back until they reconnect it.
    FEED_DISCONNECTED = "feed_disconnected"


@dataclass(frozen=True)
class Resolution:
    """The outcome of one resolution — explicit in both directions.

    Either `provider` is set and `chain` lists it first followed by the ordered
    alternatives, or `provider` is None and `reason` says why. There is no third
    shape, and no caller has to infer an outage from an empty return value.
    """

    capability: Capability
    context: ResolutionContext
    provider: Optional[MarketDataProvider] = None
    chain: Tuple[MarketDataProvider, ...] = ()
    reason: Optional[UnavailableReason] = None

    @property
    def available(self) -> bool:
        return self.provider is not None

    @property
    def tier(self) -> Optional[SourceTier]:
        return self.provider.tier if self.provider else None

    @property
    def recovering(self) -> bool:
        """Whether the selected provider has yet to demonstrate it can serve.

        D5.13, and the closing half of LIM-D5.12-1. True exactly when
        resolution picked a provider whose health is DOWN — which, since
        `candidates_for` filters DOWN out unconditionally, can only be a
        provider D5.7 re-admitted for one trial after its cool-down ran.

        THE PREDICATE IS HEALTH, AND DELIBERATELY NOT "CAME FROM `probes`"
        ------------------------------------------------------------------
        Those two are equivalent today and are not the same statement. "It was
        offered as a probe" is a fact about the mechanism that admitted it; "its
        health is DOWN" is a fact about the provider, and it is the one the
        consumer's question is actually about — nothing has recently succeeded
        against this provider, whatever route it took into the chain. Reading
        the mechanism would also make this surface a second consumer of D5.7's
        internals, which is how two things that must agree start disagreeing.

        WHY ONLY `DOWN`
        ---------------
        `UNKNOWN` must not qualify. A provider registered a moment ago has
        never been called and is UNKNOWN until the first request — the Yahoo
        baseline is UNKNOWN at every process start — and `HEALTH_RANK` has tied
        UNKNOWN with UP since D2 precisely because "unproven" is not "failing".
        Reporting the platform as recovering at every boot would be a larger
        and far more visible falsehood than the one this property removes.

        `DEGRADED` must not qualify either. A degraded provider has had some
        failures and is still being handed every request; calling that an
        outage on the consumer surface would disagree with the resolution that
        is still selecting it, which is exactly the property ADR-052 declined
        to give up.

        This is a *projection* and changes no resolution: the probe is still
        offered, still ranked last and still spent by the request that reaches
        it. A status surface that suppressed it to look tidy would suppress the
        recovery too, restoring the ADR-029 deadlock D5.7 closed.
        """
        return (
            self.provider is not None
            and self.provider.health().state is ProviderState.DOWN
        )

    @property
    def state(self) -> str:
        """The consumer-facing feed state — the three-way answer.

        `recovering` is checked first because it is a refinement of the
        `available` branch: the provider is set, so `available` is true, and the
        question this surface is answering is the narrower one.
        """
        if self.recovering:
            return FEED_RECOVERING
        return FEED_AVAILABLE if self.available else FEED_UNAVAILABLE

    def as_status(self) -> Dict[str, Any]:
        """Consumer-safe summary. Contains no provider identity.

        `tier` is a claim about the freshness of data a consumer is receiving,
        so it is reported only in the state where data is actually being
        served. In `recovering` there is a provider with a tier attribute and
        no data to make the claim about, and stamping `delayed` there is
        precisely what told a user they were on the delayed feed while eight
        consecutive calls had returned nothing (LIM-D5.12-1).

        `reason` stays `None` in `recovering`, and that is a decision rather
        than an omission. :class:`UnavailableReason` means "why no provider
        could be resolved", and here one was; overloading it would make the
        field answer two different questions depending on a sibling field. The
        state is self-describing, and the diagnosis an operator wants —
        which providers, which cool-downs — is on `diagnostics()`, where
        provider detail is allowed to be.
        """
        state = self.state
        return {
            "state": state,
            "tier": self.tier.value if (self.tier and state == FEED_AVAILABLE) else None,
            "reason": self.reason.value if self.reason else None,
        }


def _selection_rank(provider: MarketDataProvider) -> Tuple[int, int, float]:
    """Where `provider` sits among the candidates that survived filtering.

    Health, then probation, then delivery latency — see :data:`PROBATION_RANK`
    and :data:`LATENCY_RANK_UNKNOWN`. Read through the provider contract rather
    than by type, so a licensed exchange feed, a vendor feed and a broker feed
    are ranked by what they have demonstrated and not by what they are.

    LATENCY IS LAST, AND THAT ORDERING IS THE WHOLE GUARANTEE (D5.4)
    Placing it third is what makes "latency can never promote an unproven or a
    stale feed" true by construction rather than by a special case: a
    probationary provider — which since D5.3 includes every provider whose data
    has gone stale — loses on the second element before the third is compared,
    so no median, however good, can lift it past a provider that is proven and
    delivering. There is no branch here that says so, because there is nothing
    to branch on.

    Equally, this is a *tie-break* and not a filter. It changes the order of
    candidates that have already survived entitlement, capability, health,
    readiness and coverage; it can never remove one, and it can never add one.
    """
    latency = provider.delivery_latency
    return (
        HEALTH_RANK[provider.health().state],
        PROBATION_RANK[bool(provider.is_on_probation)],
        LATENCY_RANK_UNKNOWN if latency is None else float(latency),
    )


@dataclass(frozen=True)
class SharedResolution:
    """What one worker learned from the shared store before resolving (D5.8).

    DB-1 has to make a decision — "may this worker spend this provider's
    recovery trial?" — that only Redis can answer, on a path
    (:meth:`SourceManager.resolve_feed`) that is synchronous and called from
    routes, diagnostics and the gateway alike. Making that path `async` would
    have changed a contract five modules deep for a question asked about a
    handful of DOWN providers.

    So the awaitable work is lifted into :meth:`SourceManager.prepare`, which
    runs once per gateway call, and its result travels as this value. Resolution
    stays synchronous, stays pure, and — the property that matters — makes no
    decision the shared store has not already made: `claims` is filtered, never
    re-derived.

    A caller that does not prepare gets exactly the D5.7 behaviour, which is
    what keeps every existing call site correct and what a single-process
    deployment runs.
    """

    context: Optional[ResolutionContext] = None
    claims: Optional[ProbeClaims] = None
    #: Whether the shared health records were actually read. False means Redis
    #: did not answer and every provider below is being judged on this worker's
    #: own evidence — a fact the diagnostics surface must be able to state.
    health_synced: bool = False


class SourceManager:
    """Resolves the active market-data provider for a capability and context."""

    def __init__(
        self,
        registry: Optional[ProviderRegistry] = None,
        *,
        health_recovery: Optional[ProviderHealthRecovery] = None,
        store: Optional[SharedHealthStore] = None,
    ) -> None:
        self._registry = registry if registry is not None else provider_registry
        #: D5.7's failure cool-down — the second half of step 2 of the
        #: Resolution procedure, which D1 implemented as an unconditional
        #: exclusion. Owned here rather than on the registry because deciding
        #: *whether* an excluded provider may be tried is a selection decision,
        #: and the registry filters without choosing.
        self._health_recovery = (
            health_recovery if health_recovery is not None else ProviderHealthRecovery()
        )
        #: D5.8's shared health record. Injected for the same reason the
        #: cool-down register is: a test supplies one pointed at its own Redis,
        #: and a deployment without Redis keeps the process-local behaviour
        #: D5.1–D5.7 shipped without a branch anywhere else.
        self._store = store if store is not None else shared_health_store
        self._last_status: Optional[Dict[str, Any]] = None
        #: user_id -> last published per-user status, for change gating (D4.5).
        self._last_status_by_user: Dict[str, Dict[str, Any]] = {}
        #: user_id -> {broker_name: (capabilities,)}. Populated from Event Bus
        #: broker lifecycle events; never written by anything that imports a
        #: broker module.
        self._connected_brokers: Dict[str, Dict[str, Tuple[str, ...]]] = {}
        self._broker_events_subscribed = False

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    @property
    def health_recovery(self) -> ProviderHealthRecovery:
        """The failure cool-down register (D5.7). Diagnostics and tests."""
        return self._health_recovery

    # ── Resolution ───────────────────────────────────────

    async def prepare(
        self,
        capability: Capability,
        context: Optional[ResolutionContext] = None,
        *,
        user_id: Optional[str] = None,
    ) -> SharedResolution:
        """Read the shared health state and claim any due trials (D5.8).

        The awaitable half of resolution, and the whole of DB-1's read path. Run
        once by the Market Gateway immediately before :meth:`resolve_feed`, it
        does two things and nothing else:

        1. **Refreshes every eligible provider's health from Redis**, so this
           worker ranks on the failures every worker has seen rather than only
           on its own. Overwriting, not merging — see
           :meth:`MarketDataProvider.apply_shared_health`.
        2. **Claims at most one recovery trial per DOWN provider**, atomically,
           so two workers resolving in the same instant cannot both spend it.

        Cost is bounded and flat: one round trip for the health read of the whole
        candidate set, plus one per *currently DOWN* provider. A deployment with
        nothing down pays exactly one Redis operation per gateway call, and a
        deployment with no Redis pays none.

        The eligible set is `candidates_for + down_candidates_for`, which is the
        registry's one eligibility pass split by health — so the set read here
        cannot disagree with the set resolved below it, even though the health
        it is keyed on is what this method is about to change.
        """
        ctx = self._context(context, user_id)
        eligible = (
            self._registry.candidates_for(capability, ctx)
            + self._registry.down_candidates_for(capability, ctx)
        )
        shareable = [p for p in eligible if p.health_is_shared]

        synced = False
        if shareable:
            ok, records = await self._store.read_many(
                [self._store_key(p) for p in shareable]
            )
            if ok:
                synced = True
                for provider in shareable:
                    record = records.get(self._store_key(provider))
                    if record is not None:
                        provider.apply_shared_health(record)

        # Re-partitioned *after* the refresh: a provider this worker thought was
        # healthy may have been driven DOWN by another worker one millisecond
        # ago, and it is the post-refresh health that decides whether it is a
        # candidate or a cool-down subject.
        claims = await self._health_recovery.claim_due(
            self._registry.down_candidates_for(capability, ctx)
        )
        return SharedResolution(context=ctx, claims=claims, health_synced=synced)

    def resolve_feed(
        self,
        capability: Capability,
        context: Optional[ResolutionContext] = None,
        *,
        user_id: Optional[str] = None,
        shared: Optional[SharedResolution] = None,
    ) -> Resolution:
        """Resolve `capability` into an explicit :class:`Resolution`.

        The primary entry point. Steps 1–5 of the Resolution procedure in
        MARKET_DATA_ARCHITECTURE.md in order: the registry builds the candidate
        list (entitlement, capability, health), this method ranks the survivors
        and picks the head, and the remainder becomes the failover chain.

        Never raises and never returns a half-answer: an unresolvable request is
        a runtime condition the gateway degrades through — last cached data,
        honest timestamps, one calm banner — not an exception for a route
        handler to leak as a 500.

        `user_id` is the D1-compatible shorthand for `ResolutionContext(user_id=…)`
        and is ignored when `context` is supplied.
        """
        ctx = self._context(context, user_id)
        candidates = self._registry.candidates_for(capability, ctx)

        # D5.7 — step 2's other half. A provider excluded at DOWN is re-admitted
        # for one trial once its failure cool-down has run, and for no other
        # reason. It is *appended*, never merged into the healthy set: the sort
        # below puts it last because DOWN is the worst health band, so this can
        # add a last resort to the chain and can never reorder what is above it.
        #
        # Nothing here treats the provider as recovered. Its health is untouched
        # by re-admission and stays DOWN until a real call succeeds, which is
        # the same evidence every other provider has always needed.
        # D5.8 — when the caller prepared, the claim was made once, atomically,
        # in `prepare`. This filters that answer and re-decides nothing, so a
        # trial another worker holds is never offered here.
        probes = self._health_recovery.due_from(
            self._registry.down_candidates_for(capability, ctx),
            claims=shared.claims if shared is not None else None,
        )

        if not candidates and not probes:
            return Resolution(
                capability=capability,
                context=ctx,
                reason=self._diagnose(capability, ctx),
            )

        # Stable sort: `candidates_for` already ordered by priority then
        # registration, so sorting on health and probation alone preserves the
        # priority ordering *within* each rank. That is the whole tie-break rule
        # from MARKET_DATA_ARCHITECTURE.md — better health first, then a
        # provider that has served its probation window, then priority, then
        # most-recently-registered — expressed as one stable sort instead of a
        # comparator nobody can read.
        #
        # Recomputed on every resolution, from state the providers hold now.
        # Nothing here is cached and no provider is marked as primary: which
        # provider leads is the *output* of this sort and never an input to it
        # (D4.5), which is what makes promotion and demotion atomic.
        chain = sorted(candidates + probes, key=_selection_rank)
        return Resolution(
            capability=capability,
            context=ctx,
            provider=chain[0],
            chain=tuple(chain),
        )

    def resolve(
        self,
        capability: Capability,
        *,
        user_id: Optional[str] = None,
        context: Optional[ResolutionContext] = None,
    ) -> Optional[MarketDataProvider]:
        """The provider that should serve `capability`, or None if there is none.

        The convenience form of :meth:`resolve_feed` for callers that want the
        winner and nothing else. Callers that must survive a provider failing
        mid-request want :meth:`resolve_feed` and its chain.
        """
        return self.resolve_feed(capability, context, user_id=user_id).provider

    def failover_chain(
        self,
        capability: Capability,
        context: Optional[ResolutionContext] = None,
        *,
        user_id: Optional[str] = None,
    ) -> List[MarketDataProvider]:
        """The ordered providers to try for `capability`: preferred first.

        The mechanism behind MARKET_DATA_ARCHITECTURE.md's failover diagram —
        preferred provider, unavailable, next eligible provider. D2 supplies the
        ordering; the gateway walks it. Policy on top of it (probation windows,
        latency scoring, flap suppression) is D5.
        """
        return list(self.resolve_feed(capability, context, user_id=user_id).chain)

    def active_tier(
        self,
        capability: Capability = Capability.QUOTES,
        *,
        user_id: Optional[str] = None,
        context: Optional[ResolutionContext] = None,
    ) -> Optional[SourceTier]:
        """Freshness tier currently serving `capability`, or None when nothing is."""
        return self.resolve_feed(capability, context, user_id=user_id).tier

    def _context(
        self,
        context: Optional[ResolutionContext],
        user_id: Optional[str],
    ) -> ResolutionContext:
        if context is not None:
            return context
        if user_id is not None:
            return ResolutionContext.for_user(user_id)
        return GLOBAL_CONTEXT

    def _diagnose(
        self,
        capability: Capability,
        context: ResolutionContext,
    ) -> UnavailableReason:
        """Name the reason an empty candidate list is empty.

        Re-walks the registry with the filters relaxed one at a time. Only runs
        on the unavailable path, where one extra traversal of a short list buys
        an operator the difference between "our startup didn't register Yahoo"
        and "this user's broker token expired" — two incidents with the same
        symptom and nothing else in common.
        """
        if len(self._registry) == 0:
            return UnavailableReason.NO_PROVIDERS_REGISTERED

        entitled = self._registry.entitled_for(context)
        if not entitled:
            return UnavailableReason.NOT_ENTITLED

        capable = [p for p in entitled if p.supports(capability)]
        if not capable:
            return UnavailableReason.CAPABILITY_UNSUPPORTED

        return UnavailableReason.ALL_PROVIDERS_DOWN

    # ── Status ───────────────────────────────────────────

    def status(
        self,
        *,
        user_id: Optional[str] = None,
        context: Optional[ResolutionContext] = None,
    ) -> Dict[str, Any]:
        """Consumer-facing feed status. Contains NO provider identity.

        This is the payload the frontend tier indicator and the AI context are
        allowed to see: is the feed being served, how fresh is it, and — when it
        is not — why.
        """
        ctx = self._context(context, user_id)
        quotes = self.resolve_feed(Capability.QUOTES, ctx)
        return {
            **quotes.as_status(),
            # D5.13 — `state`, not `available`. This list is read as "what can
            # I ask for right now", so a capability whose only provider is a
            # DOWN one awaiting its trial must not appear: advertising it here
            # while `state` said `recovering` would put the contradiction back
            # one field to the right.
            "capabilities": sorted(
                capability.value
                for capability in Capability
                if self.resolve_feed(capability, ctx).state == FEED_AVAILABLE
            ),
        }

    def diagnostics(
        self,
        *,
        context: Optional[ResolutionContext] = None,
    ) -> Dict[str, Any]:
        """Full provider detail INCLUDING names, for admin surfaces and logs.

        MARKET_DATA_ARCHITECTURE.md permits provider detail on a diagnostics
        surface and forbids it on live UI surfaces; keeping the two in separate
        methods is what makes that boundary reviewable — `status()` cannot
        accidentally grow a provider name.
        """
        ctx = context if context is not None else GLOBAL_CONTEXT
        quotes = self.resolve_feed(Capability.QUOTES, ctx)
        return {
            "providers": self._registry.describe(),
            "feed": self.status(context=ctx),
            # The one place a selected provider's name is legitimate.
            "selected_for_quotes": quotes.provider.name if quotes.provider else None,
            "failover_chain": [p.name for p in quotes.chain],
            # D5.7. Outstanding failure cool-downs, so an operator can tell
            # "excluded and waiting" from "excluded forever" — which is the
            # thing that was invisible while the exclusion was unconditional.
            # Admin-only, alongside the provider names already here; `status()`
            # is unchanged and carries none of this.
            "health_recovery": self._health_recovery.describe(),
        }

    async def publish_status(
        self,
        *,
        force: bool = False,
        user_id: Optional[str] = None,
        change_reason: Optional["FeedChangeReason"] = None,
    ) -> Optional[Dict[str, Any]]:
        """Publish `provider.status` when the feed state, tier or reason changed.

        Change-gated because this fires from the gateway's per-call health
        bookkeeping. Publishing unconditionally would put one event on the bus
        per market request, drowning the topic that a tier flip — the single
        thing a consumer cares about — needs to be visible on.

        WHY `user_id` EXISTS (D4.5)
        ---------------------------
        Feed resolution has been per-user since D2, but this method only ever
        published the *platform* view. That was harmless while every user
        resolved to the same baseline. It stops being harmless the moment a
        user's feed is promoted to a streaming provider nobody else can see: the
        platform view does not change, so the one user whose tier actually
        flipped is the one consumer never told — the tier indicator keeps
        reading "delayed" while the data behind it is live.

        A user-scoped publish carries `user_id` on the payload, which is the
        convention the event bridge already uses to deliver an event to exactly
        one user (the same one `market.tick` uses for an owned feed). So this
        adds no topic and no second mechanism: one more argument, and change
        gating kept per user so a promotion is announced once rather than on
        every tick that follows it.
        """
        current = self.status(user_id=user_id) if user_id else self.status()
        key = str(user_id) if user_id else None
        previous = self._last_status if key is None else self._last_status_by_user.get(key)
        if not force and current == previous:
            return None

        if key is None:
            self._last_status = current
        else:
            self._last_status_by_user[key] = current

        payload = {**current, "previous_tier": (previous or {}).get("tier")}
        if key is not None:
            payload["user_id"] = key
        # D5.13 — closes LIM-D5.5-2. Present only when the caller actually knows
        # why, and absent rather than guessed otherwise: most tier movements —
        # a promotion, a stale-feed demotion, a link drop — have no single cause
        # worth naming, and inventing one would be fabricated provenance on the
        # surface least able to afford it. The key is omitted entirely when
        # there is no reason, so every existing consumer sees the payload it
        # saw before this sprint.
        if change_reason is not None:
            payload["change_reason"] = FeedChangeReason(change_reason).value
        await event_bus.publish(PROVIDER_STATUS_TOPIC, payload)
        logger.info(
            "Market feed status%s: state=%s tier=%s reason=%s change=%s (was tier=%s)",
            f" for user {key}" if key else "",
            current["state"], current["tier"], current["reason"],
            payload.get("change_reason"),
            (previous or {}).get("tier"),
        )
        return current

    def forget_user_status(self, user_id: Optional[str]) -> None:
        """Drop the cached per-user status for `user_id`.

        Called when a user's feed is unregistered. Without it this map keeps one
        entry per user who has ever had a feed for the life of the process —
        the same unbounded-growth trap `record_broker_disconnected` avoids by
        dropping empty user entries.
        """
        if user_id:
            self._last_status_by_user.pop(str(user_id), None)

    # ── Broker connection tracking (D3) ──────────────────

    def subscribe_broker_events(self) -> None:
        """Listen for broker connect/disconnect on the Event Bus.

        Idempotent: the Event Bus appends handlers without de-duplicating, so a
        second subscription would double-handle every event, and startup paths
        in this codebase demonstrably run more than once (reload, test
        re-import, worker fork). Guarding here rather than asking every caller
        to remember is the same choice the provider registry makes about
        duplicate registration.
        """
        if self._broker_events_subscribed:
            return
        event_bus.subscribe(BROKER_CONNECTED_TOPIC, self._on_broker_connected)
        event_bus.subscribe(BROKER_DISCONNECTED_TOPIC, self._on_broker_disconnected)
        self._broker_events_subscribed = True
        logger.info("SourceManager subscribed to broker lifecycle events")

    async def _on_broker_connected(self, event: Dict[str, Any]) -> None:
        data = event.get("data") or {}
        self.record_broker_connected(
            data.get("user_id"), data.get("broker"), data.get("capabilities") or [])

    async def _on_broker_disconnected(self, event: Dict[str, Any]) -> None:
        data = event.get("data") or {}
        self.record_broker_disconnected(data.get("user_id"), data.get("broker"))

    def record_broker_connected(self, user_id: Optional[str], broker: Optional[str],
                                capabilities: Any = ()) -> None:
        """Record that `user_id` has `broker` connected, with `capabilities`.

        Separate from the event handler so the state transition is callable and
        assertable without constructing an event envelope — and so a future
        caller with the information in hand does not have to publish an event to
        itself.
        """
        if not user_id or not broker:
            logger.warning("Ignoring broker.connected with no user or broker: %r/%r",
                           user_id, broker)
            return
        self._connected_brokers.setdefault(str(user_id), {})[broker] = tuple(capabilities)
        logger.info("Source Manager: broker %s connected for user %s (capabilities=%s)",
                    broker, user_id, ",".join(capabilities) or "none")

    def record_broker_disconnected(self, user_id: Optional[str],
                                   broker: Optional[str]) -> None:
        """Record that `user_id` no longer has `broker` connected."""
        if not user_id or not broker:
            return
        brokers = self._connected_brokers.get(str(user_id))
        if not brokers:
            return
        brokers.pop(broker, None)
        if not brokers:
            # Drop the empty user entry rather than keeping it. This map is
            # per-process and unbounded otherwise: one residual key per user who
            # has ever connected a broker, for the life of the process.
            self._connected_brokers.pop(str(user_id), None)
        logger.info("Source Manager: broker %s disconnected for user %s", broker, user_id)

    def connected_brokers(self, user_id: Optional[str]) -> List[str]:
        """Brokers this user currently has connected, in connection order."""
        if not user_id:
            return []
        return list(self._connected_brokers.get(str(user_id), {}))

    def streaming_brokers(self, user_id: Optional[str]) -> List[str]:
        """Connected brokers whose feed could serve streaming market data.

        The question MARKET_DATA_ARCHITECTURE.md's priority algorithm asks first,
        answered from the capabilities carried on the lifecycle event rather than
        by importing a broker module.

        Note what this method is *not*, since D4.4: it is not how a feed becomes
        a provider. Registration is attached to the stream itself, on the side
        that owns it, where the live socket and the entitlement both are — this
        registry is a record of connections, and a record of a connection is not
        evidence that a socket is up. Kept because it answers the priority
        algorithm's first question without resolving anything, which diagnostics
        and the D4.5 switch both need.
        """
        if not user_id:
            return []
        return [
            broker
            for broker, capabilities in self._connected_brokers.get(str(user_id), {}).items()
            if TICK_STREAM_CAPABILITY in capabilities
        ]

    def has_broker_connected(self, user_id: Optional[str]) -> bool:
        return bool(self.connected_brokers(user_id))

    # ── Health bookkeeping (called by the gateway) ───────

    def record_success(self, provider: MarketDataProvider, *, empty: bool = False) -> bool:
        """Record a successful provider call. True when the state changed.

        Clearing the D5.7 cool-down is gated on the provider's *resulting state*
        rather than on the call having returned, and that is the whole of "stale
        evidence cannot restore health" on this path: an empty success does not
        reset the failure streak, so a provider answering 200-with-no-data stays
        DOWN and keeps its cool-down, exactly as it keeps its exclusion.
        """
        changed = provider.record_success(empty=empty) is not None
        if provider.health().state is not ProviderState.DOWN:
            self._health_recovery.note_recovered(provider)
        return changed

    def record_failure(self, provider: MarketDataProvider, exc: BaseException) -> bool:
        """Record a failed provider call. True when the state changed.

        A state change here is what makes failover *durable*: once a provider
        crosses into DOWN the registry stops offering it as a candidate at all,
        so subsequent requests do not pay for its timeout before falling through.
        Within a single request the gateway walks the chain immediately — it
        does not wait for the counter. Recovery is symmetric: one success resets
        the streak.

        KNOWN D2 LIMITATION — no self-recovery for a demoted provider.
        A demoted provider is last in the chain, the chain stops at the first
        provider that answers, and health only improves on a successful call. So
        a provider that blips past DEGRADED is never called again and cannot
        climb back on its own: it needs an external `record_success`, a process
        restart, or the periodic re-probe that MARKET_DATA_ARCHITECTURE.md
        assigns to Phase 5 (sprint D5) along with probation windows. D3's broker
        adapter is the natural first caller — a reconnected WebSocket knows it
        recovered without anyone polling it. Pinned by
        `test_a_demoted_provider_has_no_self_recovery_path_in_d2`, which is
        about DEGRADED and remains true: a DEGRADED provider is still a
        candidate, still in the chain, and recovers the moment the chain reaches
        it — it is unreached, not unreachable.

        CLOSED FOR DOWN IN D5.7. The DOWN half of the same paragraph was a real
        deadlock, because exclusion made the provider unreach*able*. Every
        failure recorded while a provider is DOWN now charges its failure
        cool-down, so the eighth consecutive failure that creates the state and
        a failed trial afterwards climb one ladder rather than two. Read from
        the provider's resulting state rather than from the exception: this
        module does not classify failures and must not start.
        """
        changed = provider.record_failure(exc) is not None
        if provider.health().state is ProviderState.DOWN:
            self._health_recovery.note_probe_failed(provider)
        return changed

    async def record_success_shared(
        self, provider: MarketDataProvider, *, empty: bool = False
    ) -> bool:
        """The distributed form of :meth:`record_success` (D5.8).

        Same semantics, one authority. The transition is computed inside Redis so
        that a success racing a failure from another worker produces one logical
        transition rather than two half-applied ones, and the authoritative
        counters are then mirrored onto this worker's provider object — so the
        very next synchronous `resolve_feed` ranks on them without a second read.

        The empty-success rule survives the move intact and is enforced in the
        one place it can be: the script counts an empty call and does **not**
        clear the streak, so a provider answering 200-with-no-data stays DOWN
        and keeps its cool-down in every worker, not just in this one.

        Falls back to the purely local path when Redis does not answer.
        """
        if not provider.health_is_shared:
            return self.record_success(provider, empty=empty)

        ok, record = await self._store.record(
            self._store_key(provider),
            health_state.EMPTY if empty else health_state.SUCCESS,
            stamp=_now_iso(),
            degraded_after=DEGRADED_AFTER_FAILURES,
            down_after=DOWN_AFTER_FAILURES,
        )
        if not ok or record is None:
            return self.record_success(provider, empty=empty)

        provider.apply_shared_health(record)
        if provider.health().state is not ProviderState.DOWN:
            await self._health_recovery.note_recovered_shared(provider)
        return record.changed

    async def record_failure_shared(
        self, provider: MarketDataProvider, exc: BaseException
    ) -> bool:
        """The distributed form of :meth:`record_failure` (D5.8).

        The counter that decides DOWN is incremented inside Redis, which is what
        makes "eight consecutive failures" mean eight failures *observed by the
        deployment* rather than eight observed by one worker — the difference
        between a broken provider being excluded after 8 calls and after 8×N.

        Charging the cool-down is still gated on the provider's resulting state
        rather than on the exception, exactly as D5.7 has it: this module does
        not classify failures and must not start.
        """
        if not provider.health_is_shared:
            return self.record_failure(provider, exc)

        ok, record = await self._store.record(
            self._store_key(provider),
            health_state.FAILURE,
            stamp=_now_iso(),
            degraded_after=DEGRADED_AFTER_FAILURES,
            down_after=DOWN_AFTER_FAILURES,
            label=type(exc).__name__,
        )
        if not ok or record is None:
            return self.record_failure(provider, exc)

        provider.apply_shared_health(record)
        if provider.health().state is ProviderState.DOWN:
            await self._health_recovery.note_probe_failed_shared(provider)
        return record.changed

    async def forget_health_recovery_shared(self, provider: MarketDataProvider) -> bool:
        """Drop a provider's cool-down and shared health because it is going away.

        The distributed form of :meth:`forget_health_recovery`. An unregistered
        provider's shared record is evidence about a subject that no longer
        exists, and leaving it would mean a re-registered feed of the same name
        inheriting a verdict about its predecessor.
        """
        return await self._health_recovery.forget_shared(provider)

    @staticmethod
    def _store_key(provider: MarketDataProvider):
        """This provider's key in the shared store.

        Owner scope *and* name, never the name alone: two users holding a feed
        from the same broker are two subjects, and one account's outage is not
        the other's.
        """
        return provider_key(provider.name, provider.owner_user_id)

    def forget_health_recovery(self, provider: MarketDataProvider) -> bool:
        """Drop a provider's failure cool-down because it is being unregistered.

        Not a claim that it recovered — see
        :meth:`ProviderHealthRecovery.forget`. Called from the gateway's
        unregister path for the same reason :meth:`forget_user_status` is: a map
        keyed by every provider that has ever been down would otherwise grow for
        the life of the process.
        """
        return self._health_recovery.forget(provider)

    def reset(self) -> None:
        """Drop cached status, broker tracking and every provider's health.
        Startup and tests only."""
        self._last_status = None
        self._last_status_by_user.clear()
        self._connected_brokers.clear()
        self._health_recovery.clear()
        for provider in self._registry.all():
            provider.reset_health()


def _now_iso() -> str:
    """The wall-clock stamp written onto a shared health record.

    Diagnostics only, and deliberately so. Every *decision* in D5 — cool-downs,
    probation, freshness, latency — is made from a monotonic clock or, since
    D5.8, from Redis's own; these two fields are the "when did this last work"
    an operator reads and nothing compares them.
    """
    return datetime.now(timezone.utc).isoformat()


#: Module-level singleton, matching `event_bus` / `market_gateway`.
source_manager = SourceManager()

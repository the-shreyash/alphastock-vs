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
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from services.market_engine.event_bus import event_bus
from services.market_engine.providers import (
    GLOBAL_CONTEXT,
    Capability,
    MarketDataProvider,
    ProviderRegistry,
    ProviderState,
    ResolutionContext,
    SourceTier,
    provider_registry,
)

logger = logging.getLogger(__name__)

#: Feed state published to consumers. Mirrors the Source Manager state machine
#: in MARKET_DATA_ARCHITECTURE.md, collapsed to what a consumer can act on: it
#: is either being served, or it is not.
FEED_AVAILABLE = "available"
FEED_UNAVAILABLE = "unavailable"

#: Topic on the existing Event Bus. Payload carries `tier`, `state` and an
#: unavailability `reason` — never a provider name (Developer Rule 4).
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

    def as_status(self) -> Dict[str, Any]:
        """Consumer-safe summary. Contains no provider identity."""
        return {
            "state": FEED_AVAILABLE if self.available else FEED_UNAVAILABLE,
            "tier": self.tier.value if self.tier else None,
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


class SourceManager:
    """Resolves the active market-data provider for a capability and context."""

    def __init__(self, registry: Optional[ProviderRegistry] = None) -> None:
        self._registry = registry if registry is not None else provider_registry
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

    # ── Resolution ───────────────────────────────────────

    def resolve_feed(
        self,
        capability: Capability,
        context: Optional[ResolutionContext] = None,
        *,
        user_id: Optional[str] = None,
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

        if not candidates:
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
        chain = sorted(candidates, key=_selection_rank)
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
            "capabilities": sorted(
                capability.value
                for capability in Capability
                if self.resolve_feed(capability, ctx).available
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
        }

    async def publish_status(
        self,
        *,
        force: bool = False,
        user_id: Optional[str] = None,
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
        await event_bus.publish(PROVIDER_STATUS_TOPIC, payload)
        logger.info(
            "Market feed status%s: state=%s tier=%s reason=%s (was tier=%s)",
            f" for user {key}" if key else "",
            current["state"], current["tier"], current["reason"],
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
        """Record a successful provider call. True when the state changed."""
        return provider.record_success(empty=empty) is not None

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
        `test_a_demoted_provider_has_no_self_recovery_path_in_d2`.
        """
        return provider.record_failure(exc) is not None

    def reset(self) -> None:
        """Drop cached status, broker tracking and every provider's health.
        Startup and tests only."""
        self._last_status = None
        self._last_status_by_user.clear()
        self._connected_brokers.clear()
        for provider in self._registry.all():
            provider.reset_health()


#: Module-level singleton, matching `event_bus` / `market_gateway`.
source_manager = SourceManager()

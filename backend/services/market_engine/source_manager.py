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

Still deferred, with the sprint that owns each: the streaming push surface and
per-user broker detection (D3); probation windows, latency scoring and flap
suppression (D5 / Phase 5 in MARKET_DATA_ARCHITECTURE.md).

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


class SourceManager:
    """Resolves the active market-data provider for a capability and context."""

    def __init__(self, registry: Optional[ProviderRegistry] = None) -> None:
        self._registry = registry if registry is not None else provider_registry
        self._last_status: Optional[Dict[str, Any]] = None

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
        # registration, so sorting on health alone preserves the priority
        # ordering *within* each health rank. That is the whole tie-break rule
        # from MARKET_DATA_ARCHITECTURE.md — better health first, then priority,
        # then most-recently-registered — expressed as one stable sort instead
        # of a comparator nobody can read.
        chain = sorted(candidates, key=lambda p: HEALTH_RANK[p.health().state])
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

    async def publish_status(self, *, force: bool = False) -> Optional[Dict[str, Any]]:
        """Publish `provider.status` when the feed state, tier or reason changed.

        Change-gated because this fires from the gateway's per-call health
        bookkeeping. Publishing unconditionally would put one event on the bus
        per market request, drowning the topic that a tier flip — the single
        thing a consumer cares about — needs to be visible on.
        """
        current = self.status()
        if not force and current == self._last_status:
            return None

        previous = self._last_status
        self._last_status = current
        await event_bus.publish(PROVIDER_STATUS_TOPIC, {
            **current,
            "previous_tier": (previous or {}).get("tier"),
        })
        logger.info(
            "Market feed status: state=%s tier=%s reason=%s (was tier=%s)",
            current["state"], current["tier"], current["reason"],
            (previous or {}).get("tier"),
        )
        return current

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
        """Drop cached status and every provider's health. Startup and tests only."""
        self._last_status = None
        for provider in self._registry.all():
            provider.reset_health()


#: Module-level singleton, matching `event_bus` / `market_gateway`.
source_manager = SourceManager()

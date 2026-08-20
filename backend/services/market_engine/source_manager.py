"""Source Manager — decides which provider answers a market data request.

MARKET_DATA_ARCHITECTURE.md gives this service one job stated three ways: for
every user and every moment, know which provider is the right one; keep that
decision current as conditions change; and make sure nothing downstream can tell
which way it went. The Market Gateway *executes* what this service decides and
holds no priority logic of its own.

WHAT D1 IMPLEMENTS AND WHAT IT DOES NOT
---------------------------------------
D1 ships one provider. A full intelligent failover system — per-user broker
detection, make-before-break switching, probation windows, latency scoring, flap
suppression — has nothing to arbitrate between and could not be exercised, let
alone tested honestly. Writing it now would mean shipping several hundred lines
whose first real execution is in D3.

So D1 implements the parts that are load-bearing today and are the seams D2/D3
extend:

  * capability-based resolution over the registry, priority-ordered
  * health-based exclusion of failed providers, with automatic recovery
  * `provider.status` events carrying tier only, never a provider name
  * the `user_id` parameter on every resolution entry point

Deferred, with the sprint that owns each: per-user broker resolution and
make-before-break switching (D3), probation windows / latency scoring / flap
suppression (Phase 5 in MARKET_DATA_ARCHITECTURE.md).

WHY `user_id` EXISTS NOW WHEN NOTHING USES IT
----------------------------------------------
Provider entitlement is per user — that is the cornerstone of Category 2 in
MARKET_DATA_ARCHITECTURE.md: a user with a connected broker streams, a guest
polls, and both are served by the same gateway at the same instant. Threading a
new argument through every resolution call site later is a wide, mechanical,
merge-conflict-heavy change; accepting and ignoring it now costs one parameter
and one docstring. It is documented as inert rather than silently accepted so no
caller mistakes it for working per-user selection.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from services.market_engine.event_bus import event_bus
from services.market_engine.providers import (
    Capability,
    MarketDataProvider,
    ProviderRegistry,
    ProviderState,
    SourceTier,
    provider_registry,
)

logger = logging.getLogger(__name__)

#: Feed state published to consumers. Mirrors the Source Manager state machine
#: in MARKET_DATA_ARCHITECTURE.md, collapsed to what a consumer can act on: it
#: is either being served, or it is not.
FEED_AVAILABLE = "available"
FEED_UNAVAILABLE = "unavailable"

#: Topic on the existing Event Bus. Payload carries `tier` and `state` — never
#: a provider name (Developer Rule 4).
PROVIDER_STATUS_TOPIC = "provider.status"


class SourceManager:
    """Resolves the active market-data provider for a capability.

    Stateless with respect to the decision itself: resolution reads the
    registry and provider health on every call rather than caching a chosen
    provider. With a single polled provider a cache would save one sorted list
    traversal over a one-element list while introducing an invalidation bug
    surface — the cache arrives in D3 alongside the per-user session state that
    actually makes it worth having.
    """

    def __init__(self, registry: Optional[ProviderRegistry] = None) -> None:
        self._registry = registry if registry is not None else provider_registry
        self._last_status: Optional[Dict[str, Any]] = None

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    # ── Resolution ───────────────────────────────────────

    def resolve(
        self,
        capability: Capability,
        *,
        user_id: Optional[str] = None,
    ) -> Optional[MarketDataProvider]:
        """The provider that should serve `capability`, or None if there is none.

        `user_id` is accepted and IGNORED in D1 — see the module docstring.
        Passing it changes nothing today and will change everything in D3.

        Returns None rather than raising: a missing provider is a runtime
        condition the gateway must degrade through (last cached data, honest
        timestamps, calm banner), not an exception for a route handler to leak.
        """
        candidates = self._registry.candidates_for(capability)
        if not candidates:
            return None

        # `candidates_for` already applies priority order and drops DOWN
        # providers. Prefer a fully healthy provider over a degraded one within
        # that order; fall back to the degraded one only when nothing healthy
        # can serve the capability, because degraded data beats no data.
        for provider in candidates:
            if provider.health().state is ProviderState.UP:
                return provider
        return candidates[0]

    def active_tier(
        self,
        capability: Capability = Capability.QUOTES,
        *,
        user_id: Optional[str] = None,
    ) -> Optional[SourceTier]:
        """Freshness tier currently serving `capability`, or None when nothing is."""
        provider = self.resolve(capability, user_id=user_id)
        return provider.tier if provider else None

    # ── Status ───────────────────────────────────────────

    def status(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Consumer-facing feed status. Contains NO provider identity.

        This is the payload the frontend tier indicator and the AI context are
        allowed to see: is the feed being served, and how fresh is it.
        """
        tier = self.active_tier(user_id=user_id)
        return {
            "state": FEED_AVAILABLE if tier else FEED_UNAVAILABLE,
            "tier": tier.value if tier else None,
            "capabilities": sorted(
                capability.value
                for capability in Capability
                if self.resolve(capability, user_id=user_id) is not None
            ),
        }

    def diagnostics(self) -> Dict[str, Any]:
        """Full provider detail INCLUDING names, for admin surfaces and logs.

        MARKET_DATA_ARCHITECTURE.md permits provider detail on a diagnostics
        surface and forbids it on live UI surfaces; keeping the two in separate
        methods is what makes that boundary reviewable — `status()` cannot
        accidentally grow a provider name.
        """
        return {
            "providers": self._registry.describe(),
            "feed": self.status(),
        }

    async def publish_status(self, *, force: bool = False) -> Optional[Dict[str, Any]]:
        """Publish `provider.status` when the feed state or tier has changed.

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
            "Market feed status: state=%s tier=%s (was tier=%s)",
            current["state"], current["tier"], (previous or {}).get("tier"),
        )
        return current

    # ── Health bookkeeping (called by the gateway) ───────

    def record_success(self, provider: MarketDataProvider, *, empty: bool = False) -> bool:
        """Record a successful provider call. True when the state changed."""
        return provider.record_success(empty=empty) is not None

    def record_failure(self, provider: MarketDataProvider, exc: BaseException) -> bool:
        """Record a failed provider call. True when the state changed.

        A state change here is what makes failover automatic: once a provider
        crosses into DOWN the registry stops offering it as a candidate, so the
        next request resolves to the tier below with no switching code
        involved. Recovery is symmetric — one success resets the streak.
        """
        return provider.record_failure(exc) is not None

    def reset(self) -> None:
        """Drop cached status and every provider's health. Startup and tests only."""
        self._last_status = None
        for provider in self._registry.all():
            provider.reset_health()


#: Module-level singleton, matching `event_bus` / `market_gateway`.
source_manager = SourceManager()

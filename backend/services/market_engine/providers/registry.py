"""Provider Registry — the single list of market-data providers the platform knows.

Registering a provider here is the *entire* integration surface for a new feed.
Per Developer Rule 9 in MARKET_DATA_ARCHITECTURE.md, adding Zerodha, Upstox, or a
licensed NSE feed must mean one adapter, one normalizer, and one entry here — and
nothing else in the codebase changes.

WHY A REGISTRY AND NOT A MODULE-LEVEL DICT OF IMPORTS
-----------------------------------------------------
Providers arrive with wildly different lifecycles. Yahoo is available the moment
the process starts. A broker feed only exists once a specific user has completed
an OAuth flow, and disappears when their token is revoked. A licensed feed
appears when a contract is signed and a flag is flipped. A static import map
cannot express any of that; a registry with runtime register/unregister can, and
the Source Manager can re-resolve the moment membership changes.

The registry stores and orders. It does NOT choose — choosing is the Source
Manager's job, and keeping the two apart is what stops provider-selection logic
from creeping into a data structure.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from services.market_engine.providers.base import (
    GLOBAL_CONTEXT,
    PUSH_CAPABILITIES,
    Capability,
    MarketDataProvider,
    ProviderContractError,
    ProviderKind,
    ProviderState,
    ResolutionContext,
    SourceTier,
)

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Ordered collection of registered market-data providers.

    Ordering is by :attr:`MarketDataProvider.priority` (lower wins), then by
    registration order for stability. Two providers in the same tier — a user
    with two brokers connected — therefore resolve deterministically instead of
    depending on dict iteration order.
    """

    def __init__(self) -> None:
        # Insertion-ordered; Python dicts guarantee it, and that guarantee is
        # load-bearing here for the same-priority tie-break.
        self._providers: Dict[str, MarketDataProvider] = {}

    # ── Membership ───────────────────────────────────────

    def register(self, provider: MarketDataProvider, *, replace: bool = False) -> None:
        """Add a provider.

        Re-registering the same name without `replace=True` is ignored rather
        than raising: startup paths can run more than once (reload, test module
        re-import, a worker forking after the parent registered), and turning
        that into a crash would make the failure mode of a harmless duplicate
        worse than the duplicate itself. It is logged so a genuine name
        collision is still visible.

        A provider whose declarations contradict each other is a different
        matter and raises :class:`ProviderContractError` — see
        :func:`validate_provider`. That is deliberately harsher than the
        duplicate-name path: a duplicate registration is a harmless repeat of
        something already correct, while a contradictory one is a provider that
        would go on to serve the wrong thing silently for as long as it stayed
        registered.
        """
        validate_provider(provider)
        existing = self._providers.get(provider.name)
        if existing is not None and not replace:
            logger.warning(
                "Provider %r already registered (%s) — ignoring duplicate registration",
                provider.name, type(existing).__name__,
            )
            return
        self._providers[provider.name] = provider
        logger.info(
            "Market data provider registered: %s (kind=%s, tier=%s, priority=%d, capabilities=%s)",
            provider.name, provider.kind.value, provider.tier.value, provider.priority,
            ",".join(sorted(c.value for c in provider.capabilities)) or "none",
        )

    def unregister(self, name: str) -> Optional[MarketDataProvider]:
        """Remove a provider by name. Returns it, or None if it was not present.

        Used when an entitlement ends — a user disconnects their broker, a
        licence lapses. The Source Manager re-resolves on the next request and
        the feed drops to the next priority tier with no other code involved.
        """
        provider = self._providers.pop(name, None)
        if provider is not None:
            logger.info("Market data provider unregistered: %s", name)
        return provider

    def clear(self) -> None:
        """Drop every provider. Startup re-initialisation and tests only."""
        self._providers.clear()

    def get(self, name: str) -> Optional[MarketDataProvider]:
        return self._providers.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._providers

    def __len__(self) -> int:
        return len(self._providers)

    # ── Ordered views ────────────────────────────────────

    def all(self) -> List[MarketDataProvider]:
        """Every registered provider in priority order."""
        return sorted(
            self._providers.values(),
            key=lambda p: (p.priority, _registration_index(self._providers, p)),
        )

    def candidates_for(
        self,
        capability: Capability,
        context: Optional[ResolutionContext] = None,
    ) -> List[MarketDataProvider]:
        """Providers able to serve `capability` for `context`, in priority
        order, excluding any that are currently DOWN.

        This is steps 1–3 of the Resolution procedure in
        MARKET_DATA_ARCHITECTURE.md: build the candidate list from providers
        whose entitlement applies to the request, drop the ones lacking the
        capability, drop the ones that are unhealthy. Step 4 — picking among
        the survivors, and ordering the rest as a failover chain — belongs to
        the Source Manager. The registry stores and filters; it does not
        choose, and keeping that line sharp is what stops selection policy from
        settling into a data structure.

        `context` defaults to the global context, so a caller with no user
        attached (a scheduled refresh, a scanner sweep) sees exactly the
        platform-wide providers.

        DEGRADED providers are deliberately kept: a provider failing
        intermittently still beats no data at all, and the tier below it may be
        materially worse. UNKNOWN providers are kept for a different reason —
        they have never been tried, and filtering them out would make being
        tried impossible. Only DOWN is disqualifying.
        """
        ctx = context if context is not None else GLOBAL_CONTEXT
        return [
            provider
            for provider in self.all()
            if provider.supports(capability)
            and provider.is_eligible_for(ctx)
            and provider.health().state is not ProviderState.DOWN
        ]

    def entitled_for(self, context: ResolutionContext) -> List[MarketDataProvider]:
        """Every provider whose entitlement applies to `context`, in priority
        order, regardless of capability or health.

        Used by diagnostics and by the Source Manager to tell "this user has no
        provider at all" apart from "this user's providers cannot serve *this*
        capability" — two situations with the same empty candidate list and
        completely different meanings to an operator reading a log line.
        """
        return [p for p in self.all() if p.is_eligible_for(context)]

    def describe(self) -> List[dict]:
        """Diagnostic snapshot of every provider, in priority order.

        Carries provider names — admin/diagnostics surfaces and logs only.
        """
        return [provider.describe() for provider in self.all()]


def validate_provider(provider: MarketDataProvider) -> None:
    """Check that a provider's declarations about itself are mutually consistent.

    Run at registration, raising :class:`ProviderContractError` on the first
    contradiction. The broker layer's :meth:`BrokerRegistry.validate` does the
    same job on its side of the platform and for the same reason: a capability
    set that nothing verifies is a comment.

    The four rules, and the failure each one prevents:

    * **A push capability requires `kind=STREAMING`.** TICKS and DEPTH cannot be
      served by a request/response call. A polling adapter declaring TICKS would
      be resolved for the tick capability and then answer it by polling — the
      "no polling disguised as streaming" failure, arriving through the
      capability set.

    * **`tier=STREAMING` requires `kind=STREAMING`.** The same failure arriving
      through the freshness label instead. `tier` is what the AI calibrates its
      language against and what the UI renders as "Live", so a poll loop wearing
      it makes the platform describe a 30-second-old number as live. Forbidden
      by CLAUDE.md's data rules; caught here rather than noticed in production.

    * **`kind=STREAMING` requires `on_raw` to be overridden.** A streaming
      provider is one whose data is pushed into it, so a provider with no push
      entry point holds a connection that can deliver nothing —
      indistinguishable in the logs from a quiet market, which is the exact
      failure shape D4.2 found one layer down in the broker codec. The rule is
      stated on `kind` rather than on the push capabilities because a streaming
      provider serving pushed *quotes* needs the same entry point as one serving
      ticks, and requiring a TICKS declaration from it would be requiring a
      capability it does not serve.

    Yahoo is unaffected by all three: it is POLLING/DELAYED and declares no push
    capability, which is exactly what the rules describe as consistent.
    """
    pushes = PUSH_CAPABILITIES & set(provider.capabilities)
    streaming = provider.kind is ProviderKind.STREAMING

    if pushes and not streaming:
        raise ProviderContractError(
            f"provider {provider.name!r} declares push capabilities "
            f"{sorted(c.value for c in pushes)} but kind={provider.kind.value!r} — "
            "a pushed capability cannot be served by polling"
        )
    if provider.tier is SourceTier.STREAMING and not streaming:
        raise ProviderContractError(
            f"provider {provider.name!r} declares tier={provider.tier.value!r} with "
            f"kind={provider.kind.value!r} — a polling provider may not claim the streaming tier"
        )
    if streaming and type(provider).on_raw is MarketDataProvider.on_raw:
        raise ProviderContractError(
            f"provider {provider.name!r} declares kind='streaming' without implementing "
            "on_raw() — it has no entry point for the data it is supposed to be pushed"
        )


def _registration_index(providers: Dict[str, MarketDataProvider],
                        provider: MarketDataProvider) -> int:
    """Position of `provider` in registration order — the same-priority tie-break."""
    for index, name in enumerate(providers):
        if name == provider.name:
            return index
    return len(providers)  # pragma: no cover - unreachable for a member


#: Module-level singleton, matching the `event_bus` / `market_gateway` pattern
#: used elsewhere in the Market Engine.
provider_registry = ProviderRegistry()

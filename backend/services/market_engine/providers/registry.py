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
    Capability,
    MarketDataProvider,
    ProviderState,
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
        """
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

    def candidates_for(self, capability: Capability) -> List[MarketDataProvider]:
        """Providers able to serve `capability`, in priority order, excluding
        any that are currently DOWN.

        This is step 1–3 of the Resolution procedure in
        MARKET_DATA_ARCHITECTURE.md: build the candidate list, filter out
        unhealthy providers, filter out providers lacking the capability. Step
        4 (pick the survivor) belongs to the Source Manager.

        DEGRADED providers are deliberately kept: a provider failing
        intermittently still beats no data at all, and the tier below it may be
        materially worse. Only DOWN is disqualifying.
        """
        return [
            provider
            for provider in self.all()
            if provider.supports(capability)
            and provider.health().state is not ProviderState.DOWN
        ]

    def describe(self) -> List[dict]:
        """Diagnostic snapshot of every provider, in priority order.

        Carries provider names — admin/diagnostics surfaces and logs only.
        """
        return [provider.describe() for provider in self.all()]


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

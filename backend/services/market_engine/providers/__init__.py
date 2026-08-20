"""Market data provider adapters.

One module per provider. An adapter is the only code permitted to speak a
provider's protocol, and provider names are permitted to appear here and in the
matching normalizer family in `normalizer.py` — nowhere else in the platform.

Adding a provider (MARKET_DATA_ARCHITECTURE.md, Developer Rule 9):

    1. New adapter module implementing `MarketDataProvider`.
    2. New normalizer family in `normalizer.py` for its raw payload shape.
    3. Register it — `provider_registry.register(MyAdapter())`.

Nothing else changes. If a step 4 appears, the design has been breached.

See `base.py` for the contract and why the streaming push surface is not part
of D1.
"""
from services.market_engine.providers.base import (
    GLOBAL_CONTEXT,
    Capability,
    CapabilityUnavailable,
    MarketDataProvider,
    ProviderHealth,
    ProviderKind,
    ProviderState,
    ResolutionContext,
    SourceTier,
)
from services.market_engine.providers.registry import ProviderRegistry, provider_registry
from services.market_engine.providers.yahoo import YahooPollingAdapter

__all__ = [
    "GLOBAL_CONTEXT",
    "Capability",
    "CapabilityUnavailable",
    "MarketDataProvider",
    "ProviderHealth",
    "ProviderKind",
    "ProviderRegistry",
    "ProviderState",
    "ResolutionContext",
    "SourceTier",
    "YahooPollingAdapter",
    "provider_registry",
]

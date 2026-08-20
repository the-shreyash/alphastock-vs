"""Market Engine — Central market intelligence system for StockAssist AI.

The Market Engine collects, normalizes, validates, processes, ranks, caches,
and distributes real-time market data throughout the platform.

Architecture:
    Provider Adapters -> Source Manager -> Market Gateway -> Normalizer
    -> Validator -> Cache -> Processing Engine -> Ranking Engine
    -> Scanner Engine -> Event Bus -> AI System -> Frontend

Provider independence (D1/D2): every provider sits behind the Provider Adapter
contract in `providers/`, the Source Manager resolves which one serves a request
— by capability, entitlement and health, returning an ordered failover chain —
and the Market Gateway is the only code permitted to call one. Everything above
that line consumes normalized events carrying a `source_tier` and no provider
identity at all. MARKET_DATA_ARCHITECTURE.md is authoritative for this boundary.

The Market Engine is NOT responsible for making investment decisions.
It provides reliable data to the AI system, which reasons over it.
"""
from services.market_engine.event_bus import event_bus
from services.market_engine.gateway import (
    MarketGateway,
    market_gateway,
    register_default_providers,
)
from services.market_engine.providers import (
    Capability,
    MarketDataProvider,
    ProviderKind,
    ProviderState,
    ResolutionContext,
    SourceTier,
    provider_registry,
)
from services.market_engine.source_manager import (
    Resolution,
    SourceManager,
    UnavailableReason,
    source_manager,
)

__all__ = [
    "Capability",
    "MarketDataProvider",
    "MarketGateway",
    "ProviderKind",
    "ProviderState",
    "Resolution",
    "ResolutionContext",
    "SourceManager",
    "SourceTier",
    "UnavailableReason",
    "event_bus",
    "market_gateway",
    "provider_registry",
    "register_default_providers",
    "source_manager",
]

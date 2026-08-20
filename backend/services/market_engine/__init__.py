"""Market Engine — Central market intelligence system for StockAssist AI.

The Market Engine collects, normalizes, validates, processes, ranks, caches,
and distributes real-time market data throughout the platform.

Architecture:
    Provider Adapters -> Market Gateway -> Source Manager -> Normalizer
    -> Validator -> Cache -> Processing Engine -> Ranking Engine
    -> Scanner Engine -> Event Bus -> AI System -> Frontend

Provider independence (D1): every provider sits behind the Provider Adapter
contract in `providers/`, the Source Manager decides which one serves a request,
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
    SourceTier,
    provider_registry,
)
from services.market_engine.source_manager import SourceManager, source_manager

__all__ = [
    "Capability",
    "MarketDataProvider",
    "MarketGateway",
    "ProviderKind",
    "ProviderState",
    "SourceManager",
    "SourceTier",
    "event_bus",
    "market_gateway",
    "provider_registry",
    "register_default_providers",
    "source_manager",
]

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
    PUSH_CAPABILITIES,
    MarketDataProvider,
    ProviderContractError,
    ProviderHealth,
    ProviderKind,
    ProviderState,
    ResolutionContext,
    SourceTier,
)
from services.market_engine.providers.registry import (
    ProviderRegistry,
    provider_registry,
    validate_provider,
)
from services.market_engine.providers.streaming import (
    DEFAULT_TICK_MAX_AGE_SECONDS,
    LATENCY_WINDOW_SAMPLES,
    PROBATION_WINDOW_SECONDS,
    STREAMING_FEED_PRIORITY,
    FeedReadiness,
    FeedStability,
    StreamingTickProvider,
)
from services.market_engine.providers.yahoo import YahooPollingAdapter

__all__ = [
    "DEFAULT_TICK_MAX_AGE_SECONDS",
    "GLOBAL_CONTEXT",
    "LATENCY_WINDOW_SAMPLES",
    "PROBATION_WINDOW_SECONDS",
    "PUSH_CAPABILITIES",
    "STREAMING_FEED_PRIORITY",
    "Capability",
    "CapabilityUnavailable",
    "FeedReadiness",
    "FeedStability",
    "MarketDataProvider",
    "ProviderContractError",
    "ProviderHealth",
    "ProviderKind",
    "ProviderRegistry",
    "ProviderState",
    "ResolutionContext",
    "SourceTier",
    "StreamingTickProvider",
    "YahooPollingAdapter",
    "provider_registry",
    "validate_provider",
]

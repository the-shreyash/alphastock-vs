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
    DEGRADED_AFTER_FAILURES,
    DOWN_AFTER_FAILURES,
    GLOBAL_CONTEXT,
    Capability,
    CapabilityUnavailable,
    PUSH_CAPABILITIES,
    MarketDataProvider,
    ProviderContractError,
    LatencyProfile,
    ProviderHealth,
    ProviderKind,
    ProviderState,
    ResolutionContext,
    SourceTier,
)
from services.market_engine.providers.health_recovery import (
    HEALTH_PROBE_BASE_DELAY,
    HEALTH_PROBE_MAX_DELAY,
    HealthProbe,
    ProbeClaims,
    ProviderHealthRecovery,
)
from services.market_engine.providers.registry import (
    ProviderRegistry,
    provider_registry,
    validate_provider,
)
from services.market_engine.providers.streaming import (
    DEFAULT_FEED_SHARD,
    DEFAULT_TICK_MAX_AGE_SECONDS,
    LATENCY_TAIL_PERCENTILE,
    LATENCY_TAIL_WINDOW_SAMPLES,
    LATENCY_WINDOW_SAMPLES,
    PROBATION_WINDOW_SECONDS,
    STREAMING_FEED_PRIORITY,
    FeedReadiness,
    FeedStability,
    StreamingTickProvider,
)
from services.market_engine.providers.yahoo import YahooPollingAdapter

__all__ = [
    "DEFAULT_FEED_SHARD",
    "DEFAULT_TICK_MAX_AGE_SECONDS",
    "HEALTH_PROBE_BASE_DELAY",
    "HEALTH_PROBE_MAX_DELAY",
    "GLOBAL_CONTEXT",
    "LATENCY_TAIL_PERCENTILE",
    "LATENCY_TAIL_WINDOW_SAMPLES",
    "LATENCY_WINDOW_SAMPLES",
    "PROBATION_WINDOW_SECONDS",
    "PUSH_CAPABILITIES",
    "STREAMING_FEED_PRIORITY",
    "Capability",
    "CapabilityUnavailable",
    "FeedReadiness",
    "FeedStability",
    "DEGRADED_AFTER_FAILURES",
    "DOWN_AFTER_FAILURES",
    "HealthProbe",
    "ProbeClaims",
    "MarketDataProvider",
    "ProviderContractError",
    "LatencyProfile",
    "ProviderHealth",
    "ProviderHealthRecovery",
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

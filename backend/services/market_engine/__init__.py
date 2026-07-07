"""Market Engine — Central market intelligence system for StockAssist AI.

The Market Engine collects, normalizes, validates, processes, ranks, caches,
and distributes real-time market data throughout the platform.

Architecture:
    External Providers -> Market Gateway -> Collectors -> Normalizer
    -> Validator -> Cache -> Processing Engine -> Ranking Engine
    -> Scanner Engine -> Event Bus -> AI System -> Frontend

The Market Engine is NOT responsible for making investment decisions.
It provides reliable data to the AI system, which reasons over it.
"""
from services.market_engine.event_bus import event_bus
from services.market_engine.gateway import MarketGateway, market_gateway

__all__ = ["event_bus", "MarketGateway", "market_gateway"]

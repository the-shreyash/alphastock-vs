"""Broker integration package.

Every broker implements the same BrokerAdapter interface (see base.py) so the
rest of the platform (Trading Engine, Portfolio Engine, AI, UI) never depends
on a specific broker. Adding a broker == adding one adapter module here.
"""
from services.brokers.base import BrokerAdapter, BrokerError, BrokerAuthError
from services.brokers.zerodha import ZerodhaAdapter
from services.brokers.upstox import UpstoxAdapter

SUPPORTED_BROKERS = {
    "zerodha": ZerodhaAdapter,
    "upstox": UpstoxAdapter,
}


def create_adapter(broker: str) -> BrokerAdapter:
    """Instantiate the adapter for `broker` (raises for unknown brokers)."""
    cls = SUPPORTED_BROKERS.get((broker or "").lower())
    if not cls:
        raise BrokerError(f"Unsupported broker: {broker}")
    return cls()

"""Broker Provider Framework.

    Application
        -> BrokerEngine        sessions, persistence, sync, audit, events
        -> BrokerGateway       capability enforcement, contracts, errors, health
        -> BrokerRegistry      the brokers this deployment knows
        -> Broker Adapter      the only code that speaks a broker's protocol
        -> Broker API

Adding a broker (the whole checklist):

    1. New adapter module here implementing `BrokerAdapter`.
    2. Declare its `capabilities` and its `credential_spec`.
    3. Register it below.

Nothing else changes. If a step 4 appears — a branch in the Trading Engine, a
new field on a route, a case in the frontend — the framework has been breached
and the breach is what gets fixed, not the symptom. `tests/test_broker_framework.py`
holds a fictional broker that proves the three steps are sufficient.

Module map:

    base.py          the adapter contract
    capabilities.py  what a broker can do, declared by the broker
    contracts.py     the canonical shapes core services see
    credentials.py   the authentication / configuration boundary
    errors.py        one error vocabulary for every broker
    health.py        broker API health (distinct from a user's session)
    registry.py      the broker list, with registration-time verification
    gateway.py       the single choke point every broker call passes through
    stream.py        realtime transport for the brokers that offer one
    crypto.py        token encryption at rest
"""
from services.brokers.angelone import AngelOneAdapter
from services.brokers.base import BrokerAdapter, normalize_status
from services.brokers.capabilities import (
    SYNC_CAPABILITIES,
    TRADING_CAPABILITIES,
    BrokerCapability,
)
from services.brokers.contracts import BrokerConnection, ORDER_STATUS
from services.brokers.credentials import BrokerCredentials, BrokerCredentialSpec
from services.brokers.errors import (
    BrokerAuthError,
    BrokerContractError,
    BrokerError,
    BrokerErrorCode,
    CapabilityUnsupported,
    UnknownBrokerError,
)
from services.brokers.gateway import BrokerGateway, broker_gateway
from services.brokers.health import BrokerConnectionState, BrokerHealth
from services.brokers.registry import (
    BrokerAdapterInvalid,
    BrokerRegistry,
    broker_registry,
)
from services.brokers.upstox import UpstoxAdapter
from services.brokers.zerodha import ZerodhaAdapter


def register_default_brokers() -> None:
    """Register every broker this deployment ships with.

    Idempotent — the registry ignores a duplicate name — so a re-entrant import
    or a test module reload cannot produce two adapters with divergent health
    counters. Registration is unconditional rather than gated on
    `is_configured()`: an unconfigured broker must still appear in the broker
    list so the UI can say "not available on this deployment" instead of
    silently omitting it, and `configured` is a field on that listing.
    """
    for adapter_cls in (ZerodhaAdapter, UpstoxAdapter, AngelOneAdapter):
        if adapter_cls.name not in broker_registry:
            broker_registry.register(adapter_cls())


register_default_brokers()


#: Broker name -> adapter class.
#:
#: DEPRECATED compatibility view over the registry, kept because `server.py`,
#: `broker_engine.py` and the existing tests iterate it. It is now derived from
#: the registry rather than being the source of truth, so it cannot drift from
#: what is actually registered. New code asks `broker_registry` or
#: `broker_gateway`, both of which can answer capability questions this cannot.
SUPPORTED_BROKERS = {
    adapter.name: type(adapter) for adapter in broker_registry.all()
}


def create_adapter(broker: str) -> BrokerAdapter:
    """DEPRECATED. The registered adapter for `broker`.

    Was a factory that built a fresh instance per call; now it returns the
    registered singleton, because health has to accumulate somewhere and a
    per-call instance forgets everything. Raises `UnknownBrokerError` — a
    `BrokerError` subclass — so the existing `except BrokerError` handlers keep
    catching it.
    """
    return broker_registry.require(broker)


__all__ = [
    "AngelOneAdapter",
    "BrokerAdapter",
    "BrokerAdapterInvalid",
    "BrokerAuthError",
    "BrokerCapability",
    "BrokerConnection",
    "BrokerConnectionState",
    "BrokerContractError",
    "BrokerCredentialSpec",
    "BrokerCredentials",
    "BrokerError",
    "BrokerErrorCode",
    "BrokerGateway",
    "BrokerHealth",
    "BrokerRegistry",
    "CapabilityUnsupported",
    "ORDER_STATUS",
    "SUPPORTED_BROKERS",
    "SYNC_CAPABILITIES",
    "TRADING_CAPABILITIES",
    "UnknownBrokerError",
    "UpstoxAdapter",
    "ZerodhaAdapter",
    "broker_gateway",
    "broker_registry",
    "create_adapter",
    "normalize_status",
    "register_default_brokers",
]

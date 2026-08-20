"""Broker Registry — the single list of brokers the platform knows.

Registering an adapter here is the *entire* integration surface for a new
broker. BROKER_INTEGRATION.md's long-term vision states the requirement
directly: "Adding a new broker should require only creating a new adapter while
keeping the Trading Engine, Portfolio Engine, AI System, and UI unchanged."

WHY A REGISTRY AND NOT THE DICT IT REPLACED
--------------------------------------------
Before D3 the broker set was a module-level literal:

    SUPPORTED_BROKERS = {"zerodha": ZerodhaAdapter, "upstox": UpstoxAdapter}

and `create_adapter()` built a fresh instance on demand. Three things that
cannot express:

  * **Health.** A new instance per call has no memory, so nothing could
    accumulate a broker's API error rate — which is why the Admin Portal
    monitoring in BROKER_INTEGRATION.md had no data source. The registry holds
    one long-lived adapter per broker and that instance owns its health.

  * **Capability queries.** "Which of the user's brokers can place an order?" is
    a question about the registry, and a dict of classes can only answer it by
    instantiating everything at the call site.

  * **Registration-time verification.** A dict accepts any class. The registry
    checks that every capability an adapter declares has a real implementation,
    so a broker that claims HOLDINGS without overriding `get_holdings` fails at
    import — the cheapest possible moment — instead of returning
    `CapabilityUnsupported` to a user mid-session.

The registry stores, filters and verifies. It does NOT decide which broker to
use: that is always the user's explicit choice, routed through the Broker
Gateway. This is the one structural difference from the market-data
`ProviderRegistry`, where a Source Manager picks on the user's behalf — a broker
is an account the user owns, and silently routing an order to a different one
than they chose would be an unforgivable behaviour in a trading platform.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from services.brokers.base import BrokerAdapter
from services.brokers.capabilities import (
    CAPABILITY_METHODS,
    IMPLEMENTABLE_CAPABILITIES,
    BrokerCapability,
)
from services.brokers.errors import UnknownBrokerError

logger = logging.getLogger(__name__)


class BrokerAdapterInvalid(RuntimeError):
    """An adapter cannot be registered as declared.

    A startup/programming error, deliberately not a `BrokerError`: it is never
    the result of a user action and must never be rendered to a user. It fails
    the process rather than degrading, because a broker that is registered but
    broken is worse than one that is absent — the absent broker is simply not
    offered, while the broken one is offered and then fails at the worst moment.
    """


class BrokerRegistry:
    """Ordered collection of registered broker adapters.

    Ordering is registration order, which is display order on the broker-picker
    UI. There is no priority concept here on purpose — see the module docstring.
    """

    def __init__(self) -> None:
        self._adapters: Dict[str, BrokerAdapter] = {}

    # ── Membership ───────────────────────────────────────

    def register(self, adapter: BrokerAdapter, *, replace: bool = False) -> BrokerAdapter:
        """Add an adapter after verifying it can do what it claims.

        Re-registering the same name without `replace=True` is ignored rather
        than raising, matching `ProviderRegistry`: startup paths can run more
        than once (reload, test re-import, a worker forking after the parent
        registered), and making a harmless duplicate fatal is a worse failure
        than the duplicate. Replacing is allowed explicitly so tests can install
        a fake adapter without reaching into private state.
        """
        self.validate(adapter)
        existing = self._adapters.get(adapter.name)
        if existing is not None and not replace:
            logger.warning(
                "Broker %r already registered (%s) — ignoring duplicate registration",
                adapter.name,
                type(existing).__name__,
            )
            return existing
        self._adapters[adapter.name] = adapter
        logger.info(
            "Broker adapter registered: %s (configured=%s, capabilities=%s)",
            adapter.name,
            adapter.is_configured(),
            ",".join(sorted(c.value for c in adapter.capabilities)) or "none",
        )
        return adapter

    @staticmethod
    def validate(adapter: BrokerAdapter) -> None:
        """Reject an adapter whose declaration and implementation disagree.

        This is what makes the capability model trustworthy rather than
        decorative. Three checks, each closing a way a capability set can lie:

        1. The adapter has a real name — an unnamed adapter would register under
           `"base"` and shadow nothing usefully.
        2. Every declared capability is a `BrokerCapability`, so a stray string
           cannot silently never match.
        3. Every declared capability that names a method resolves to a real
           implementation. "Real" means not a `@capability_stub` — inheriting a
           default that only raises means the adapter declares support it does
           not have, and the only way to find out without this check is for a
           user to hit it. Inheriting a default that genuinely works
           (`get_margins` delegating to `get_funds`) is reuse, and is allowed.
        """
        if not adapter.name or adapter.name == "base":
            raise BrokerAdapterInvalid(f"{type(adapter).__name__} must set a unique `name`")

        for capability in adapter.capabilities:
            if not isinstance(capability, BrokerCapability):
                raise BrokerAdapterInvalid(f"broker {adapter.name!r} declares non-capability {capability!r}")

        missing = []
        for capability in adapter.capabilities:
            if capability not in IMPLEMENTABLE_CAPABILITIES:
                continue  # streaming capabilities are declarative, not methods
            method = CAPABILITY_METHODS[capability]
            implementation = getattr(type(adapter), method, None)
            if implementation is None or getattr(implementation, "_capability_stub", False):
                missing.append(f"{capability.value} -> {method}()")
        if missing:
            raise BrokerAdapterInvalid(
                f"broker {adapter.name!r} declares capabilities it does not implement: " + ", ".join(sorted(missing))
            )

    def unregister(self, name: str) -> Optional[BrokerAdapter]:
        adapter = self._adapters.pop((name or "").lower(), None)
        if adapter is not None:
            logger.info("Broker adapter unregistered: %s", name)
        return adapter

    def clear(self) -> None:
        """Drop every adapter. Startup re-initialisation and tests only."""
        self._adapters.clear()

    # ── Lookup ───────────────────────────────────────────

    def get(self, name: str) -> Optional[BrokerAdapter]:
        return self._adapters.get((name or "").lower())

    def require(self, name: str) -> BrokerAdapter:
        """The adapter for `name`, or :class:`UnknownBrokerError`.

        The one lookup core code should use: it turns "broker not supported"
        into a normalized broker error with a user-safe message, instead of a
        `None` that each call site has to remember to check.
        """
        adapter = self.get(name)
        if adapter is None:
            raise UnknownBrokerError(name)
        return adapter

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.lower() in self._adapters

    def __iter__(self):
        return iter(self._adapters)

    def __len__(self) -> int:
        return len(self._adapters)

    def names(self) -> List[str]:
        return list(self._adapters)

    def all(self) -> List[BrokerAdapter]:
        return list(self._adapters.values())

    def capable_of(self, capability: BrokerCapability) -> List[BrokerAdapter]:
        """Every registered broker offering `capability`."""
        return [a for a in self._adapters.values() if a.supports(capability)]

    def configured(self) -> List[BrokerAdapter]:
        """Every broker this deployment holds credentials for."""
        return [a for a in self._adapters.values() if a.is_configured()]

    def describe(self) -> List[dict]:
        """Diagnostic snapshot of every broker, in registration order."""
        return [adapter.describe() for adapter in self._adapters.values()]


#: Module-level singleton, matching the `provider_registry` / `event_bus` /
#: `market_gateway` pattern used elsewhere.
broker_registry = BrokerRegistry()

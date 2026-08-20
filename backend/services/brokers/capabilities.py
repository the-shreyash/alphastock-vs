"""Broker Capability Model — what a broker can actually do, declared by the broker.

WHY THIS EXISTS
---------------
BROKER_INTEGRATION.md lists seventeen methods every broker "must support". That
list is aspirational rather than true: Kite Connect has no refresh grant, Upstox
does not expose a public market-tick feed on the portfolio stream, and brokers
added later will be missing pieces neither of them is. Before D3 the contract
made every method abstract, which forced each adapter to implement all of them —
so an adapter for a partial broker had exactly two options, both bad: raise from
a stub (a method that lies about existing) or return an empty list (a method that
lies about the data).

A capability set makes the third option available and makes it the only one: the
adapter *declares* what it serves, the Broker Gateway refuses anything else
before the adapter is ever called, and the core can ask
`gateway.supports(broker, capability)` instead of asking `if broker ==
"zerodha"`. That question — "can this broker do X?" — is the only thing core
services ever legitimately need to know about a broker, and it is answerable
without naming one.

Capabilities are also what keeps Developer Rule 9 of MARKET_DATA_ARCHITECTURE.md
true on the broker side: adding a broker is one adapter plus one registry entry.
A broker with no funds endpoint simply omits :attr:`BrokerCapability.FUNDS`, and
the funds route returns a clean "not supported by this broker" instead of a 500
from a stub nobody remembered to write.

WHY EACH CAPABILITY MAPS TO EXACTLY ONE METHOD
----------------------------------------------
:data:`CAPABILITY_METHODS` binds each capability to the adapter method that
serves it. The registry uses it at *registration* time to verify that every
declared capability is actually implemented — an adapter that claims HOLDINGS
without overriding `get_holdings` fails at startup rather than at 09:15 on a
Monday. Without that binding, a capability set is a comment.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict


class BrokerCapability(str, Enum):
    """One unit of broker functionality.

    Grouped by the section of BROKER_INTEGRATION.md that defines it. Values are
    stable strings because they appear in API responses (`/api/brokers`) and in
    audit logs, where a renamed enum member would silently change a contract.
    """

    # ── Account data ─────────────────────────────────────
    PROFILE = "profile"
    HOLDINGS = "holdings"
    POSITIONS = "positions"
    FUNDS = "funds"
    MARGINS = "margins"
    ORDERS = "orders"
    TRADES = "trades"

    # ── Order management ─────────────────────────────────
    PLACE_ORDER = "place_order"
    MODIFY_ORDER = "modify_order"
    CANCEL_ORDER = "cancel_order"

    # ── Session lifecycle ────────────────────────────────
    #: The broker issues refresh tokens. Indian retail APIs mostly do not, which
    #: is why this is a capability and not an assumption: without it the engine
    #: knows to prompt a reconnect instead of attempting a refresh that cannot
    #: succeed.
    SESSION_REFRESH = "session_refresh"
    #: The broker supports explicit logout / token invalidation.
    SESSION_INVALIDATE = "session_invalidate"

    # ── Realtime ─────────────────────────────────────────
    #: Live order-status updates over the broker's WebSocket.
    ORDER_STREAM = "order_stream"
    #: Live price ticks over the broker's WebSocket. Declaring this is what
    #: makes a broker a candidate market-data provider — see the D4 note in
    #: `services/market_engine/source_manager.py`. It is NOT implemented as a
    #: market feed in D3: the broker's ticks currently drive portfolio and trade
    #: P&L only, and routing them into the Market Gateway is D4's work.
    TICK_STREAM = "tick_stream"


#: Capability -> the adapter method that serves it.
#:
#: Read at registration time by :class:`BrokerRegistry` to reject an adapter
#: that declares a capability it has not implemented, and by
#: :class:`BrokerGateway` to dispatch generically without a per-capability
#: branch. Every capability must appear here; the registry asserts that.
CAPABILITY_METHODS: Dict[BrokerCapability, str] = {
    BrokerCapability.PROFILE: "get_profile",
    BrokerCapability.HOLDINGS: "get_holdings",
    BrokerCapability.POSITIONS: "get_positions",
    BrokerCapability.FUNDS: "get_funds",
    BrokerCapability.MARGINS: "get_margins",
    BrokerCapability.ORDERS: "get_orders",
    BrokerCapability.TRADES: "get_trades",
    BrokerCapability.PLACE_ORDER: "place_order",
    BrokerCapability.MODIFY_ORDER: "modify_order",
    BrokerCapability.CANCEL_ORDER: "cancel_order",
    BrokerCapability.SESSION_REFRESH: "refresh_session",
    BrokerCapability.SESSION_INVALIDATE: "invalidate_session",
    # Streaming capabilities name the adapter method the transport calls, so
    # they are verified at registration like every other capability. A broker
    # declaring ORDER_STREAM without a frame normalizer, or TICK_STREAM without
    # a way to say what to subscribe to, would open a connection that could only
    # deliver nothing.
    BrokerCapability.ORDER_STREAM: "normalize_stream_order",
    BrokerCapability.TICK_STREAM: "stream_instruments",
}

#: Capabilities that name a real adapter method and are therefore verifiable at
#: registration time.
IMPLEMENTABLE_CAPABILITIES = frozenset(capability for capability, method in CAPABILITY_METHODS.items() if method)

#: The account-data capabilities a portfolio sync reads. Kept here rather than
#: in the engine so that "what a sync needs" is expressed in capability
#: vocabulary instead of a hardcoded list of method calls.
SYNC_CAPABILITIES = frozenset(
    {
        BrokerCapability.HOLDINGS,
        BrokerCapability.POSITIONS,
        BrokerCapability.FUNDS,
    }
)

#: The full trading surface. A broker missing any of these is read-only, which
#: the UI and the Trading Engine may present honestly instead of failing a user
#: mid-order.
TRADING_CAPABILITIES = frozenset(
    {
        BrokerCapability.PLACE_ORDER,
        BrokerCapability.MODIFY_ORDER,
        BrokerCapability.CANCEL_ORDER,
    }
)

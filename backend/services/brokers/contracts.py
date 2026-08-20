"""Canonical broker data contracts — the shapes core StockAssist services see.

Before D3 these shapes existed only as a docstring at the top of `base.py`. Two
adapters happened to agree with it, which is not the same as it being enforced:
Zerodha's `get_funds` returned an extra `raw` key carrying Kite's own
`equity`/`commodity` structures verbatim, and nothing stopped a future adapter
from omitting `pnl_percent` or spelling `side` differently. A contract that lives
in a comment is a convention, and conventions do not survive the fourth adapter.

WHAT THESE DATACLASSES DO
-------------------------
Each one names the canonical fields for a broker concept and knows how to build
itself from an adapter payload. The Broker Gateway runs every adapter response
through them, which buys three things at one boundary:

  * **Shape guarantee.** Every broker's holdings have identical keys, so
    Portfolio Engine, Trading Engine, the AI context builder and the frontend
    can be written once.
  * **Leak containment.** Fields the canonical model does not name are dropped.
    That is how Kite's `raw` blob stops reaching core services — not by asking
    the adapter author to remember, but by the boundary refusing to carry it.
  * **Type coercion.** Brokers return numbers as strings, absent fields as null,
    and quantities as floats. Coercing once here is why no downstream service
    needs a `float(x or 0)` around a broker value.

WHY dataclasses AND dicts, NOT dataclasses ALONE
------------------------------------------------
The platform stores these straight into MongoDB, publishes them on the Event Bus
and returns them as JSON. Making core services carry dataclass instances would
mean converting at every one of those boundaries, and the win — attribute access
— is not worth a migration across `portfolio_engine`, `trade_stream`,
`portfolio_stream`, `server.py` and the collections already holding this shape.
So the dataclass is the *definition* and `as_dict()` is the currency. The
definition is what tests assert against and what a new adapter is checked
against; the dict is what flows.

WHY COERCION IS LENIENT AND VALIDATION IS NARROW
-------------------------------------------------
A missing optional field becomes its zero value; a mistyped number is coerced.
Only a genuinely unusable record raises :class:`BrokerContractError` — an order
with no `order_id` cannot be tracked, modified or cancelled, and passing it on
would put an untrackable row in the order book. Being strict about the rest
would mean a single unexpected null from a broker blanking a user's entire
portfolio screen, which is a worse failure than a slightly empty field.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from services.brokers.errors import BrokerContractError

#: Normalized order statuses (BROKER_INTEGRATION.md — Order Status).
ORDER_STATUS = frozenset(
    {
        "CREATED",
        "PENDING",
        "OPEN",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELLED",
        "REJECTED",
        "EXPIRED",
    }
)

#: Normalized position sides.
POSITION_SIDES = frozenset({"LONG", "SHORT", "FLAT"})


def _f(value: Any, default: float = 0.0) -> float:
    """Coerce a broker numeric to float. Brokers send these as int, float, str
    and null within the same response; every one of those means a number."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _s(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _r(value: Any, places: int = 2) -> float:
    return round(_f(value), places)


@dataclass(frozen=True)
class BrokerProfile:
    """The user's account identity at the broker."""

    account_id: Optional[str] = None
    user_name: Optional[str] = None
    email: Optional[str] = None
    broker: Optional[str] = None
    exchanges: List[str] = field(default_factory=list)
    products: List[str] = field(default_factory=list)

    @classmethod
    def from_broker(cls, payload: Dict[str, Any]) -> "BrokerProfile":
        payload = payload or {}
        return cls(
            account_id=_s(payload.get("account_id") or payload.get("user_id")),
            user_name=_s(payload.get("user_name")),
            email=_s(payload.get("email")),
            broker=_s(payload.get("broker")),
            exchanges=list(payload.get("exchanges") or []),
            products=list(payload.get("products") or []),
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerHolding:
    """One long-term holding in the user's demat account.

    `instrument_token` is the broker's own opaque instrument identifier. It is
    canonical rather than a leak because the realtime tick feed keys on it —
    `portfolio_stream.apply_broker_ticks` and `trade_stream.apply_broker_ticks`
    match ticks to holdings by this value — and it is never interpreted, only
    matched. Nothing downstream parses it or assumes a format.
    """

    symbol: Optional[str] = None
    exchange: Optional[str] = None
    quantity: int = 0
    average_price: float = 0.0
    last_price: float = 0.0
    market_value: float = 0.0
    invested_value: float = 0.0
    pnl: float = 0.0
    pnl_percent: float = 0.0
    product: Optional[str] = None
    isin: Optional[str] = None
    instrument_token: Optional[Any] = None
    company_name: Optional[str] = None

    @classmethod
    def from_broker(cls, payload: Dict[str, Any]) -> "BrokerHolding":
        payload = payload or {}
        return cls(
            symbol=_s(payload.get("symbol")),
            exchange=_s(payload.get("exchange")),
            quantity=_i(payload.get("quantity")),
            average_price=_f(payload.get("average_price")),
            last_price=_f(payload.get("last_price")),
            market_value=_r(payload.get("market_value")),
            invested_value=_r(payload.get("invested_value")),
            pnl=_r(payload.get("pnl")),
            pnl_percent=_r(payload.get("pnl_percent")),
            product=_s(payload.get("product")),
            isin=_s(payload.get("isin")),
            instrument_token=payload.get("instrument_token"),
            company_name=_s(payload.get("company_name")),
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerPosition:
    """One intraday/derivative position."""

    symbol: Optional[str] = None
    exchange: Optional[str] = None
    product: Optional[str] = None
    quantity: int = 0
    average_price: float = 0.0
    last_price: float = 0.0
    pnl: float = 0.0
    realised: float = 0.0
    unrealised: float = 0.0
    buy_quantity: int = 0
    sell_quantity: int = 0
    side: str = "FLAT"
    instrument_token: Optional[Any] = None

    @classmethod
    def from_broker(cls, payload: Dict[str, Any]) -> "BrokerPosition":
        payload = payload or {}
        quantity = _i(payload.get("quantity"))
        side = (_s(payload.get("side")) or "").upper()
        if side not in POSITION_SIDES:
            # Derive rather than reject: the sign of the quantity is the
            # definition of the side, and a broker that omits it is not a
            # broker whose position we should refuse to show.
            side = "LONG" if quantity > 0 else ("SHORT" if quantity < 0 else "FLAT")
        return cls(
            symbol=_s(payload.get("symbol")),
            exchange=_s(payload.get("exchange")),
            product=_s(payload.get("product")),
            quantity=quantity,
            average_price=_f(payload.get("average_price")),
            last_price=_f(payload.get("last_price")),
            pnl=_r(payload.get("pnl")),
            realised=_r(payload.get("realised")),
            unrealised=_r(payload.get("unrealised")),
            buy_quantity=_i(payload.get("buy_quantity")),
            sell_quantity=_i(payload.get("sell_quantity")),
            side=side,
            instrument_token=payload.get("instrument_token"),
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerOrder:
    """One order in the broker's order book.

    `broker` is carried deliberately: the unified order history in `db.orders`
    spans every connected broker, and a row there must know which account can
    modify or cancel it. This is account routing, not provenance leakage — the
    market-data rule that forbids naming a provider governs *market data*, and
    an order belongs to a specific brokerage account by nature.
    """

    order_id: Optional[str] = None
    symbol: Optional[str] = None
    exchange: Optional[str] = None
    transaction_type: Optional[str] = None
    order_type: Optional[str] = None
    product: Optional[str] = None
    quantity: int = 0
    filled_quantity: int = 0
    pending_quantity: int = 0
    price: float = 0.0
    trigger_price: float = 0.0
    average_price: float = 0.0
    status: str = "PENDING"
    status_message: Optional[str] = None
    placed_at: str = ""
    updated_at: str = ""
    tag: Optional[str] = None
    broker: Optional[str] = None

    @classmethod
    def from_broker(cls, payload: Dict[str, Any], broker: str = None) -> "BrokerOrder":
        payload = payload or {}
        order_id = _s(payload.get("order_id"))
        if not order_id:
            raise BrokerContractError("broker order is missing order_id", broker=broker, operation="order")
        status = (_s(payload.get("status")) or "PENDING").upper()
        if status not in ORDER_STATUS:
            raise BrokerContractError(
                f"broker order {order_id} has unmapped status {status!r}", broker=broker, operation="order"
            )
        return cls(
            order_id=order_id,
            symbol=_s(payload.get("symbol")),
            exchange=_s(payload.get("exchange")),
            transaction_type=_s(payload.get("transaction_type")),
            order_type=_s(payload.get("order_type")),
            product=_s(payload.get("product")),
            quantity=_i(payload.get("quantity")),
            filled_quantity=_i(payload.get("filled_quantity")),
            pending_quantity=_i(payload.get("pending_quantity")),
            price=_f(payload.get("price")),
            trigger_price=_f(payload.get("trigger_price")),
            average_price=_f(payload.get("average_price")),
            status=status,
            status_message=_s(payload.get("status_message")),
            placed_at=str(payload.get("placed_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            tag=_s(payload.get("tag")),
            broker=_s(payload.get("broker")) or broker,
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerOrderAck:
    """The broker's acknowledgement of a place/modify/cancel request.

    Deliberately NOT a :class:`BrokerOrder`. An acknowledgement carries only what
    the broker actually asserted — the id it assigned and the state it accepted
    the request into — and coercing it into a full order would manufacture a
    complete order record whose every other field is a zero the broker never
    sent. That is not a cosmetic difference: `BrokerEngine.place_order` persists
    ``{**request, **ack}``, so a full-order ack would overwrite the real
    quantity, price and symbol of the request with those zeros and write a
    hollow row into the unified order book.
    """

    order_id: Optional[str] = None
    status: str = "PENDING"
    broker: Optional[str] = None

    @classmethod
    def from_broker(
        cls, payload: Dict[str, Any], broker: str = None, default_status: str = "PENDING"
    ) -> "BrokerOrderAck":
        payload = payload or {}
        order_id = _s(payload.get("order_id"))
        if not order_id:
            raise BrokerContractError("broker did not return an order_id", broker=broker, operation="order_ack")
        status = (_s(payload.get("status")) or default_status).upper()
        if status not in ORDER_STATUS:
            raise BrokerContractError(
                f"broker acknowledged order {order_id} with unmapped status {status!r}",
                broker=broker,
                operation="order_ack",
            )
        return cls(order_id=order_id, status=status, broker=_s(payload.get("broker")) or broker)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerTrade:
    """One executed trade (fill)."""

    trade_id: Optional[str] = None
    order_id: Optional[str] = None
    symbol: Optional[str] = None
    exchange: Optional[str] = None
    transaction_type: Optional[str] = None
    quantity: int = 0
    price: float = 0.0
    product: Optional[str] = None
    executed_at: Optional[str] = None

    @classmethod
    def from_broker(cls, payload: Dict[str, Any]) -> "BrokerTrade":
        payload = payload or {}
        return cls(
            trade_id=_s(payload.get("trade_id")),
            order_id=_s(payload.get("order_id")),
            symbol=_s(payload.get("symbol")),
            exchange=_s(payload.get("exchange")),
            transaction_type=_s(payload.get("transaction_type")),
            quantity=_i(payload.get("quantity")),
            price=_f(payload.get("price")),
            product=_s(payload.get("product")),
            executed_at=_s(payload.get("executed_at")),
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerFunds:
    """Cash and margin for the account.

    Every broker-specific structure is dropped here. Kite returned its whole
    `equity`/`commodity` margin tree under a `raw` key and Upstox mirrored the
    habit; nothing in the platform read either, and any consumer that started to
    would have been reading a shape only one broker produces. If a field in
    those trees turns out to be needed, it becomes a canonical field on this
    contract that every adapter fills — which is the entire point.
    """

    available_margin: float = 0.0
    used_margin: float = 0.0
    opening_balance: float = 0.0
    payin: float = 0.0
    payout: float = 0.0
    collateral: float = 0.0
    total_balance: float = 0.0

    @classmethod
    def from_broker(cls, payload: Dict[str, Any]) -> "BrokerFunds":
        payload = payload or {}
        return cls(
            available_margin=_r(payload.get("available_margin")),
            used_margin=_r(payload.get("used_margin")),
            opening_balance=_r(payload.get("opening_balance")),
            payin=_r(payload.get("payin")),
            payout=_r(payload.get("payout")),
            collateral=_r(payload.get("collateral")),
            total_balance=_r(payload.get("total_balance")),
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerConnection:
    """The association between one StockAssist user and one broker account.

    The user -> connected broker foundation. Before D3 this shape was assembled
    inline inside `BrokerEngine.get_status`, mixing three unrelated things —
    whether the deployment has API keys, whether this user has a live session,
    and what to tell them about it — into one dict literal that no other code
    could construct or assert against.

    Naming it makes the association addressable: the Source Manager tracks
    per-user connected brokers against it (MARKET_DATA_ARCHITECTURE.md, Source
    Manager responsibility 1), the AI context can describe a user's brokerage
    setup without touching broker modules, and D4's per-user market feed has a
    record to attach a provider registration to.

    It deliberately holds NO tokens. Session material stays encrypted in
    `db.broker_accounts` and inside the engine's in-memory session cache; a
    contract that travels to routes, events and AI context must be safe to log.
    """

    user_id: Optional[str] = None
    broker: Optional[str] = None
    display_name: Optional[str] = None
    #: Deployment has credentials for this broker.
    configured: bool = False
    #: This user has a live, unexpired session.
    connected: bool = False
    #: This user had a session and it has expired — distinct from never having
    #: connected, and the difference is the whole message shown to the user.
    session_expired: bool = False
    account_id: Optional[str] = None
    connected_at: Optional[str] = None
    expires_at: Optional[str] = None
    last_sync: Optional[str] = None
    streaming: bool = False
    capabilities: List[str] = field(default_factory=list)

    @property
    def mode(self) -> str:
        """`live` | `ready` | `disconnected` — the coarse state the UI renders."""
        if self.connected:
            return "live"
        return "ready" if self.configured else "disconnected"

    def as_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "mode": self.mode}


def coerce_holdings(rows: Any, broker: str = None) -> List[Dict[str, Any]]:
    return [BrokerHolding.from_broker(row).as_dict() for row in (rows or [])]


def coerce_positions(rows: Any, broker: str = None) -> List[Dict[str, Any]]:
    return [BrokerPosition.from_broker(row).as_dict() for row in (rows or [])]


def coerce_orders(rows: Any, broker: str = None) -> List[Dict[str, Any]]:
    return [BrokerOrder.from_broker(row, broker).as_dict() for row in (rows or [])]


def coerce_order(row: Any, broker: str = None) -> Dict[str, Any]:
    return BrokerOrder.from_broker(row, broker).as_dict()


def coerce_order_ack(payload: Any, broker: str = None, default_status: str = "PENDING") -> Dict[str, Any]:
    return BrokerOrderAck.from_broker(payload, broker, default_status).as_dict()


def coerce_trades(rows: Any, broker: str = None) -> List[Dict[str, Any]]:
    return [BrokerTrade.from_broker(row).as_dict() for row in (rows or [])]


def coerce_funds(payload: Any, broker: str = None) -> Dict[str, Any]:
    return BrokerFunds.from_broker(payload).as_dict()


def coerce_profile(payload: Any, broker: str = None) -> Dict[str, Any]:
    return BrokerProfile.from_broker(payload).as_dict()

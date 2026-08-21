"""Canonical broker *streaming* contracts — the codec boundary (D4.2).

`contracts.py` defines the shapes a broker's REST responses are coerced into.
This module does the same job for the shapes that arrive over a broker's
WebSocket, and it exists for the same reason: before D4.2 the streaming path had
no contract at all.

WHAT WAS WRONG
--------------
`stream.py` — a module no broker owns — held the Kite binary frame parser, the
Kite ticker URL, the Kite subscribe frames, the Kite text-frame branch, and the
Upstox JSON envelope check. Dispatch was by protocol rather than by broker name
(D3's fix), but the *wire formats themselves* still lived in shared code, so
adding a broker still meant editing a shared module, and the shape that came out
the other end was whatever that broker's parser happened to build.

That last part is the leak. `parse_kite_binary` produced
``{"instrument_token": …, "last_price": …}`` and handed the list straight to
`BrokerEngine._on_stream_tick`, which forwarded it to `portfolio_stream`,
`trade_stream` and the user's app WebSocket. Every one of those consumers was
written against a dict that no contract guaranteed — the docstrings in
`portfolio_stream.apply_broker_ticks` and `trade_stream.apply_broker_ticks` then
stated "Ticks are ``[{instrument_token, last_price}]``" as fact, which was true
only because exactly one broker's parser happened to build that shape. (Both now
take a canonical ``MarketTick``: D4.3 moved instrument identity behind the same
kind of boundary this module gave the tick's shape.) A second streaming
broker whose parser emitted ``{"token": …, "ltp": …}`` would have type-checked,
imported, connected, and silently stopped every live P&L recompute for its
users, with no error anywhere.

WHAT THIS MODULE ESTABLISHES
----------------------------
Three canonical types and one rule:

  * :class:`BrokerStreamEndpoint` — where to connect and how to authenticate.
    Built by the adapter, because URL shape and auth style are protocol
    knowledge (Kite authenticates by query string, Upstox by bearer header, the
    next broker by something else).
  * :class:`BrokerTick` — the canonical price tick. The only tick shape any code
    above the adapter sees.
  * :class:`BrokerStreamEvent` — what a decoded frame *is*: ticks, an order
    update, a dead token, a broker-reported error, or nothing at all.

The rule: **an adapter's `decode_stream_frame` is the only code entitled to see
a raw broker frame, and the only thing it may return is one of these types.**
The transport in `stream.py` type-checks the return value, so an adapter that
tries to pass a raw payload through does not leak — it fails, loudly, at the
boundary. Unknown keys are dropped by coercion exactly as `contracts.py` drops
Kite's `raw` blob, so leak containment is a property of the boundary rather than
of every future adapter author remembering.

WHY A DECODER RATHER THAN A TRANSPORT PER BROKER
-------------------------------------------------
DB-3 proposed moving each *transport* into its owning adapter. Splitting frame
decoding from connection management is strictly better than that, because the
transport is the part that genuinely is the same everywhere: connect, send
subscribe frames, iterate messages, reconnect with jittered backoff, honour
capabilities, forward. Duplicating that per broker would duplicate the
reconnect, the auth-expiry handling and the capability checks — the exact code
where a per-broker copy diverges and one broker quietly stops reconnecting.

So the split is: **transport generic, codec broker-owned.** Adding a WebSocket
broker now adds *zero* lines to any shared module — `PROTOCOL_RUNNERS` remains
only for a broker whose protocol is not a WebSocket at all.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.brokers.contracts import BrokerOrder, _f, _i, _s
from services.brokers.errors import BrokerContractError


class StreamEventKind(str, Enum):
    """What a decoded broker frame turned out to be.

    Deliberately a closed set. A codec cannot invent a sixth kind, which is what
    lets the transport's dispatch be exhaustive and the capability check below
    be complete — a new kind would otherwise arrive with no capability gating
    anybody remembered to add.
    """

    #: One or more price ticks. Requires `TICK_STREAM`.
    TICKS = "ticks"
    #: One order-status update. Requires `ORDER_STREAM`.
    ORDER = "order"
    #: The broker says this session's token is dead. The transport stops the
    #: stream and notifies the engine rather than reconnecting into a rejection.
    AUTH_EXPIRED = "auth_expired"
    #: The broker reported an error that is not an auth failure. Logged, and the
    #: connection is left alone — a rejected subscription must not drop a socket
    #: that is still delivering other instruments.
    ERROR = "error"
    #: Heartbeat, keep-alive, an envelope for an update type we do not consume,
    #: or an unparseable frame. The overwhelmingly common case; explicitly named
    #: so "nothing to do" is a decision the codec states rather than a `None`
    #: every caller has to guess the meaning of.
    IGNORE = "ignore"


#: Which capability a decoded event requires before it may be delivered.
#:
#: Read by the Broker Gateway. A broker that decodes ticks without declaring
#: `TICK_STREAM` has them dropped — the capability model is the authority on
#: what a broker serves, and a codec is not allowed to widen it silently.
#: AUTH_EXPIRED, ERROR and IGNORE are connection-level facts rather than data,
#: so they are ungated: a dead token must be actionable on any stream.
EVENT_CAPABILITY: Dict[StreamEventKind, str] = {
    StreamEventKind.TICKS: "tick_stream",
    StreamEventKind.ORDER: "order_stream",
}


@dataclass(frozen=True)
class BrokerStreamEndpoint:
    """Where a broker's stream lives and how to authenticate to it.

    Built by the adapter inside `stream_endpoint()`. `stream.py` used to hold
    two module constants (`KITE_WS_URL`, `UPSTOX_WS_URL`) and two hand-rolled
    connection calls, one appending credentials to a query string and one
    setting a bearer header.
    """

    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    #: WebSocket subprotocols, for brokers that negotiate one (some protobuf
    #: feeds do). Empty for both current adapters.
    subprotocols: Tuple[str, ...] = ()
    ping_interval: Optional[float] = 20.0
    ping_timeout: Optional[float] = 20.0

    def __post_init__(self) -> None:
        url = (self.url or "").strip()
        if not url.startswith(("ws://", "wss://")):
            raise BrokerContractError(
                f"stream endpoint must be a WebSocket URL, got {self.safe_url!r}", operation="stream_endpoint"
            )

    @property
    def safe_url(self) -> str:
        """The URL with its query string removed — the ONLY form that may be logged.

        Not cosmetic, and not hypothetical. Kite's ticker authenticates by query
        string (`wss://ws.kite.trade?api_key=…&access_token=…`), so a log line
        carrying the raw endpoint writes a live broker access token into the
        application log. This is the same defect D3 found and fixed in
        `BrokerAdapter._request`, arriving by a second route: SECURITY.md forbids
        credentials in logs, and "connected to <url>" is the most natural log
        line anybody would write here.
        """
        return (self.url or "").split("?", 1)[0]


@dataclass(frozen=True)
class BrokerTick:
    """One canonical price tick from a broker's feed.

    WHY `instrument_token` IS TYPED `Any`
    -------------------------------------
    It is the broker's own opaque instrument identifier, exactly as
    :class:`~services.brokers.contracts.BrokerHolding` carries it: an int at
    Zerodha, a string like `"NSE_EQ|INE002A01018"` at Upstox. Narrowing it to
    `int` would encode one broker's choice into the contract, which is the
    specific mistake this module exists to prevent.

    Since D4.3 this value goes exactly one place: `InstrumentMap.resolve`
    (`services/brokers/instruments.py`), which turns it into a canonical symbol
    by matching it against the account's synced holdings and positions. Nothing
    above that boundary sees it — core services consume
    :class:`~services.market_engine.ticks.MarketTick`, which has no field for it.

    `symbol` is the other identification style, and it is why the two fields
    coexist: a broker whose feed names instruments by trading symbol populates
    this instead, and resolves through the same boundary with no token at all.
    A tick must carry one of the two; `from_broker` rejects a tick carrying
    neither, because an unidentifiable tick can be joined to nothing.
    """

    instrument_token: Any = None
    last_price: float = 0.0
    symbol: Optional[str] = None
    exchange: Optional[str] = None
    volume: int = 0
    #: Broker-supplied exchange timestamp, verbatim as a string. Not parsed
    #: here: brokers disagree on format and timezone, and a wrong parse is worse
    #: than an unparsed string.
    timestamp: Optional[str] = None

    @classmethod
    def from_broker(cls, payload: Dict[str, Any]) -> "BrokerTick":
        """Coerce one decoded tick, dropping every key the contract does not name.

        Raises :class:`BrokerContractError` for a tick that cannot be used:
        without an identity there is nothing to join it to, and without a price
        there is nothing to mark a position at. Both are codec defects rather
        than broker weather, so they are loud — the transport catches them per
        frame, so one bad tick cannot drop a live connection.
        """
        payload = payload or {}
        token = payload.get("instrument_token")
        symbol = _s(payload.get("symbol"))
        if token in (None, "") and not symbol:
            raise BrokerContractError("stream tick identifies no instrument", operation="tick")
        price = payload.get("last_price")
        if price in (None, ""):
            raise BrokerContractError("stream tick carries no price", operation="tick")
        return cls(
            instrument_token=token if token not in (None, "") else None,
            last_price=_f(price),
            symbol=symbol,
            exchange=_s(payload.get("exchange")),
            volume=_i(payload.get("volume")),
            timestamp=_s(payload.get("timestamp")),
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerStreamEvent:
    """The decoded meaning of exactly one frame from a broker's stream.

    Constructed through the classmethods below rather than directly, so every
    event is coerced through the canonical types on the way in. `__post_init__`
    then re-checks the invariants, which is what makes the type impossible to
    misuse from a codec that builds one by hand.
    """

    kind: StreamEventKind
    ticks: Tuple[BrokerTick, ...] = ()
    #: Canonical order dict (`BrokerOrder.as_dict()`), not the broker's frame.
    order: Optional[Dict[str, Any]] = None
    #: Human-readable detail for ERROR / AUTH_EXPIRED. Never contains credential
    #: material: it is the broker's own message text.
    message: str = ""

    def __post_init__(self) -> None:
        if self.kind is StreamEventKind.TICKS:
            if not self.ticks:
                raise BrokerContractError("a TICKS event carries no ticks", operation="stream_frame")
            for tick in self.ticks:
                if not isinstance(tick, BrokerTick):
                    raise BrokerContractError(
                        f"a TICKS event carries {type(tick).__name__} instead of BrokerTick", operation="stream_frame"
                    )
        elif self.ticks:
            raise BrokerContractError(f"a {self.kind.value} event must carry no ticks", operation="stream_frame")

        if self.kind is StreamEventKind.ORDER:
            if not isinstance(self.order, dict) or not self.order:
                raise BrokerContractError("an ORDER event carries no order", operation="stream_frame")
        elif self.order is not None:
            raise BrokerContractError(f"a {self.kind.value} event must carry no order", operation="stream_frame")

    # ── Constructors ─────────────────────────────────────

    @classmethod
    def tick_event(cls, ticks: Sequence[Dict[str, Any]]) -> "BrokerStreamEvent":
        """Ticks from raw decoded dicts. Unusable ticks are dropped, not raised.

        A frame is a batch: Kite packs up to hundreds of packets into one, and
        rejecting the whole batch because one packet was short would throw away
        good prices. A frame that yields *nothing* usable is IGNORE rather than
        an empty TICKS event, so the transport has one shape for "nothing to
        deliver" instead of two.
        """
        coerced: List[BrokerTick] = []
        for raw in ticks or ():
            if isinstance(raw, BrokerTick):
                coerced.append(raw)
                continue
            try:
                coerced.append(BrokerTick.from_broker(raw))
            except BrokerContractError:
                continue
        if not coerced:
            return cls.ignore()
        return cls(kind=StreamEventKind.TICKS, ticks=tuple(coerced))

    @classmethod
    def order_event(cls, order: Dict[str, Any], *, broker: str = None) -> "BrokerStreamEvent":
        """An order update, coerced through the same contract REST orders use.

        Streamed order frames used to reach `db.orders` and the user's app
        WebSocket as whatever `normalize_stream_order` returned, while the
        identical order fetched over REST went through `BrokerOrder`. Two paths
        to one collection with one of them unenforced is how the shapes drift.
        """
        return cls(kind=StreamEventKind.ORDER, order=BrokerOrder.from_broker(order, broker=broker).as_dict())

    @classmethod
    def auth_expired(cls, message: str = "") -> "BrokerStreamEvent":
        return cls(kind=StreamEventKind.AUTH_EXPIRED, message=str(message or ""))

    @classmethod
    def error(cls, message: str = "") -> "BrokerStreamEvent":
        return cls(kind=StreamEventKind.ERROR, message=str(message or ""))

    @classmethod
    def ignore(cls) -> "BrokerStreamEvent":
        return cls(kind=StreamEventKind.IGNORE)

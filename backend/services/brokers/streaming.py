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
broker adds *zero* lines to any shared module — `PROTOCOL_RUNNERS` remains only
for a broker whose protocol is not a WebSocket at all.

D4.7 kept that split and widened one thing it had quietly assumed: that a broker
has one connection. See :class:`BrokerStreamChannel` at the end of this module
for what the second streaming broker revealed and why the answer names no
broker.
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

    #: An APPLICATION-level keep-alive frame this feed requires, and how often
    #: to send it (D4.9). `None` — the default — means the broker needs none,
    #: which is both current adapters.
    #:
    #: WHY THIS IS NOT `ping_interval`
    #: --------------------------------
    #: `ping_interval` / `ping_timeout` configure the WebSocket *protocol's* own
    #: ping frames (opcode 0x9), which the library sends and the peer's library
    #: answers without either application seeing them. Some feeds do not count
    #: those as liveness at all and require a keep-alive **in the data channel**
    #: — Angel One's smart-stream requires the text frame `ping` every 30
    #: seconds and closes a connection that stops sending it, regardless of how
    #: many protocol pings crossed the wire.
    #:
    #: The failure that makes this worth a contract field rather than a broker's
    #: private background task: without it the socket connects, subscribes,
    #: delivers ticks for half a minute and is then closed by the broker — over
    #: and over, on the reconnect schedule. From the outside that is a flapping
    #: feed, not a missing keep-alive, and the account's market feed would spend
    #: its life re-earning readiness it keeps losing.
    #:
    #: What the frame *is* stays broker knowledge (text here, a JSON envelope at
    #: the next broker, a binary opcode at the one after); sending it on a timer
    #: and cancelling it with the connection is transport work, and there is one
    #: transport. See `stream.py`.
    heartbeat_frame: Optional[Any] = None
    heartbeat_interval: Optional[float] = None

    def __post_init__(self) -> None:
        url = (self.url or "").strip()
        if not url.startswith(("ws://", "wss://")):
            raise BrokerContractError(
                f"stream endpoint must be a WebSocket URL, got {self.safe_url!r}", operation="stream_endpoint"
            )
        if self.heartbeat_frame is not None:
            # Both halves or neither. A frame with no interval would never be
            # sent and an interval with no frame would send nothing — either way
            # the feed is silently missing the keep-alive it declared, which is
            # exactly the failure this field exists to prevent.
            if not isinstance(self.heartbeat_frame, (str, bytes, bytearray)):
                raise BrokerContractError(
                    f"stream heartbeat frame must be str or bytes, got {type(self.heartbeat_frame).__name__}",
                    operation="stream_endpoint",
                )
            if not self.heartbeat_interval or float(self.heartbeat_interval) <= 0:
                raise BrokerContractError(
                    "a stream heartbeat frame needs a positive interval", operation="stream_endpoint"
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


# ── Stream channels (D4.7) ──────────────────────────────────────────────

#: The channel name a broker whose realtime surface is one connection uses.
#:
#: Named rather than empty so that every stream in the platform — single- or
#: multi-channel — is addressed the same way: `(user, broker, channel)`. A
#: registry keyed on a value that is sometimes absent is a registry with two
#: key shapes, and the second one is always the one that gets missed.
DEFAULT_STREAM_CHANNEL = "default"


class BrokerStreamChannel:
    """One named connection within a broker's realtime surface.

    WHY THIS EXISTS — THE D4.7 FINDING
    -----------------------------------
    D4.2 split the streaming path into *transport generic, codec broker-owned*
    and that split still holds. What it also assumed, silently and without ever
    saying so, is that a broker's realtime surface is **one socket**: the
    adapter had one `stream_protocol`, one `stream_endpoint`, one
    `decode_stream_frame`, and `BrokerStreamManager` keyed its registry on
    `(user, broker)`.

    That assumption was Kite-shaped. Kite multiplexes binary ticks and JSON
    order updates onto a single ticker connection, so one adapter, one socket
    and one codec were indistinguishable from each other and nothing forced the
    question. The second streaming broker is where they come apart: Upstox
    serves order updates on its v2 portfolio stream and market ticks on an
    entirely separate v3 market-data feed, with different hosts, different
    encodings and different subscription models. Under the one-socket
    assumption, supporting both was impossible without the adapter opening a
    connection of its own — which would have duplicated the reconnect, the
    backoff, the link-state reporting and the auth-expiry handling inside a
    broker module, the precise duplication D4.2 exists to prevent.

    So the transport generalised instead, and it generalised *without naming a
    broker*: a channel is a name, a protocol and a codec, and how many of them a
    broker has is the broker's business. Kite declares one and is byte-for-byte
    unaffected; Upstox declares two.

    WHAT A CHANNEL OWNS AND WHAT IT DOES NOT
    -----------------------------------------
    A channel owns exactly what D4.2 made broker-owned — the endpoint, the
    subscribe frames, the frame codec, the connection-failure interpretation —
    and nothing else. Connection lifecycle, reconnect, backoff, link-state
    reporting, capability enforcement and readiness all stay in `stream.py`,
    once, for every channel of every broker. A channel that opened a socket, or
    retried one, would be a transport, and there is only one of those.

    :attr:`delivers` is the channel's own declaration of which event kinds it
    may produce. It is a *narrowing* of the broker's capabilities, never a
    widening: the Broker Gateway's capability gate still runs, and this is
    checked first. Upstox's order channel decoding a tick is a codec defect, and
    without this it would be indistinguishable from a working tick feed — the
    order channel would drive the account's market-data provider, and a socket
    with no market data on it would mark the user's portfolio.
    """

    #: Stable channel name, unique within one broker.
    name: str = DEFAULT_STREAM_CHANNEL

    #: Wire protocol, for `PROTOCOL_RUNNERS` dispatch. Per channel rather than
    #: per broker because a broker's two feeds need not speak the same protocol
    #: — Upstox's are a JSON WebSocket and a protobuf WebSocket.
    protocol: str = ""

    #: Which `StreamEventKind`s this channel may deliver.
    delivers: frozenset = frozenset()

    def endpoint(self, session: dict, credentials: Dict[str, str] = None) -> BrokerStreamEndpoint:
        """Where to connect for this channel, and how to authenticate."""
        raise NotImplementedError

    def subscribe_frames(self, instruments: Sequence[Any] = None) -> List[Any]:
        """Frames to send immediately after connecting, in order. May be none."""
        return []

    def connect_error(self, error: BaseException) -> Optional[str]:
        """Reason string when a failed *handshake* means the session is dead.

        `None` — the default — leaves the failure to the transport's ordinary
        backoff. See :meth:`services.brokers.base.BrokerAdapter.stream_connect_error`
        for the full reasoning; it is per channel because two feeds of one
        broker can refuse a dead token in two different ways.
        """
        return None

    def decode(self, frame: Any) -> BrokerStreamEvent:
        """Decode ONE raw frame into a canonical :class:`BrokerStreamEvent`."""
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<{type(self).__name__} name={self.name!r} protocol={self.protocol!r}>"

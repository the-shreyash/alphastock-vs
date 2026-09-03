"""Deterministic chaos harness for the market-data path (D5.11).

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
----------------------------------------------
D5.11 is a *proof* sprint: it asserts that the D4/D5 architecture fails locally,
honestly and recoverably, and it does so without adding a mechanism to
production code. So this module is entirely test-side, and everything in it is a
**driver for seams that already exist** rather than a new injection point:

  * `BrokerStream._connect` is already patched by `tests/test_broker_streaming.
    drive_stream`; the difference here is that the same patch answers a *script*
    of connection attempts instead of one socket, which is what makes a
    reconnect observable at all.
  * `ConnectionStability` already takes an injected `clock` and `jitter`
    (D5.1); `RecordingStability` supplies both, so the reconnect ladder is
    exact rather than random and the test never waits.
  * `StreamingTickProvider` already takes an injected monotonic `clock` (D4.5);
    `ChaosClock` is the same `FakeClock` the D5.2/D5.3/D5.4 suites use, lifted
    here so the transport and the provider can share one.

Nothing here reaches into a private attribute of production code to *create* a
condition. Every failure is produced the way the real one is produced: a socket
that closes, a handshake that raises, a frame the codec cannot read, a link
transition the transport reports.

WHY THERE IS NO RANDOMNESS
---------------------------
The brief permits a seeded generator and this harness does not use one. Every
failure class D5.11 enumerates is a *named* condition with a named consequence —
"the socket closed after one tick" is not a sample from a distribution, it is a
case — so a scripted case is both stronger evidence and reproducible without a
seed to report. `random` is used by production code (`reconnect_pause`) and is
displaced here by an injected identity jitter, so the one source of
nondeterminism on the path is removed rather than seeded.

    HARNESS DETERMINISM: no RNG, no wall clock, no sleeps.
    Every duration in a chaos test is a `ChaosClock.advance()` call.

WHY THE RECONNECT LOOP IS DRIVEN THROUGH `_run` AND NOT `_run_websocket`
------------------------------------------------------------------------
`drive_stream` (D4.2) drives one transport pass, which is the right tool for a
codec question and the wrong one for every question D5.11 asks: backoff, ladder
reset, terminal classification and retry-storm boundedness are all properties of
the loop *around* the pass. `StreamHarness` therefore drives `BrokerStream._run`
itself, and bounds it by exhausting the connection script — the loop's own
`if self._stopped: return` is what ends it, so the harness never has to reach
past the transport's own exit conditions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from unittest.mock import patch

from services.brokers.base import BrokerAdapter
from services.brokers.capabilities import BrokerCapability
from services.brokers.credentials import BrokerCredentialSpec
from services.brokers.registry import broker_registry
from services.brokers.reliability import ConnectionStability
from services.brokers.sharding import DEFAULT_SHARD_ID
from services.brokers.stream import BrokerStream
from services.brokers.streaming import (
    BrokerStreamChannel,
    BrokerStreamEndpoint,
    BrokerStreamEvent,
    StreamEventKind,
)

# ══════════════════════════════════════════════════════════════════
# Clock
# ══════════════════════════════════════════════════════════════════


class ChaosClock:
    """A monotonic clock the test moves deliberately.

    The same shape as the `FakeClock` in the D5.2/D5.3/D5.4 suites, and
    deliberately so: a chaos test that used a different clock abstraction from
    the sprint suites it is re-proving would be asserting something subtly
    different from what those suites assert.

    One clock is shared by the provider and by `ConnectionStability`, so
    "the connection lasted a full stable window" and "the feed served a full
    probation window" are the same thirty seconds rather than two independent
    fictions.
    """

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += float(seconds)
        return self.now


# ══════════════════════════════════════════════════════════════════
# Script vocabulary
# ══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Close:
    """The peer closed the socket at this point in the script.

    The frame iterator simply ends, which is what `async for message in ws`
    sees when a WebSocket closes cleanly. Distinct from :class:`Raise` because
    the transport's `finally` runs identically for both and the *classification*
    that follows must not depend on which one happened — a property D5.11
    asserts rather than assumes.
    """


@dataclass(frozen=True)
class Raise:
    """The socket raised mid-stream — a reset, a protocol error, a timeout."""

    error: BaseException = field(default_factory=lambda: ConnectionResetError("socket reset"))


@dataclass(frozen=True)
class Advance:
    """Move the chaos clock while the socket is open.

    How a connection is made to *last*. `ConnectionStability` classifies an
    attempt by how long the link was up on the clock it was given, so advancing
    past `STABLE_CONNECTION_SECONDS` here is the only honest way to produce a
    STABLE outcome — and advancing by less is the only honest way to produce a
    flap.
    """

    seconds: float


@dataclass(frozen=True)
class Call:
    """Run an arbitrary callable at a precise point in the frame stream.

    The seam for cross-object chaos: "kill shard B *while* shard A is
    delivering" is one of these between two frames, and it is the only way to
    order two independent objects' events deterministically without a sleep.
    """

    fn: Callable[[], Any]


class ChaosSocket:
    """A WebSocket double that plays a script and records what was sent.

    An extension of `tests/test_broker_streaming._FakeSocket` rather than a
    replacement: it still yields frames and still records `sent`, and it adds
    the four control items above so a script can close, raise, move the clock or
    run a side effect *between* two frames.
    """

    def __init__(self, script: Sequence[Any], *, clock: Optional[ChaosClock] = None) -> None:
        self._script = list(script)
        self._clock = clock
        self.sent: List[Any] = []
        self.closed = False
        #: How many frames actually reached the consumer. Distinct from the
        #: script length, because control items are not frames.
        self.delivered = 0

    async def send(self, frame: Any) -> None:
        if self.closed:
            raise ConnectionResetError("send on a closed socket")
        self.sent.append(frame)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        async def gen():
            for item in self._script:
                if isinstance(item, Close):
                    return
                if isinstance(item, Raise):
                    raise item.error
                if isinstance(item, Advance):
                    if self._clock is not None:
                        self._clock.advance(item.seconds)
                    continue
                if isinstance(item, Call):
                    result = item.fn()
                    if asyncio.iscoroutine(result):
                        await result
                    continue
                self.delivered += 1
                yield item

        return gen()


# ══════════════════════════════════════════════════════════════════
# The reconnect ladder, made exact
# ══════════════════════════════════════════════════════════════════


class RecordingStability(ConnectionStability):
    """`ConnectionStability` with its jitter removed and its ladder recorded.

    Both changes go through the constructor arguments D5.1 already exposes, so
    this is the production ladder — the same doubling, the same ceiling, the
    same reset rule — observed rather than re-implemented. `next_pause` records
    the true rung and then returns **zero** to the caller, which is what stops a
    chaos test from waiting sixty seconds to prove that it would have waited
    sixty seconds.

    `pauses` is the evidence for Invariant F: a mechanism that had accidentally
    become a hot loop would show a flat list of base delays here, and a
    mechanism that never retried would show an empty one.
    """

    def __init__(self, clock: ChaosClock, **kwargs: Any) -> None:
        super().__init__(clock=clock, jitter=lambda delay: delay, **kwargs)
        self.pauses: List[float] = []
        self.outcomes: List[str] = []

    def link_down(self):
        outcome = super().link_down()
        self.outcomes.append(outcome.value)
        return outcome

    def next_pause(self) -> float:
        self.pauses.append(super().next_pause())
        return 0.0


# ══════════════════════════════════════════════════════════════════
# A fictional broker, built only from the contract
# ══════════════════════════════════════════════════════════════════

#: Realistic-looking but entirely fabricated credential material. Used by the
#: security sweep (§18): every one of these strings is planted somewhere the
#: transport can reach, and none of them may appear in a log record, an
#: exception, a task name, a provider description or a consumer payload.
FAKE_CREDENTIALS: Dict[str, str] = {
    "api_key": "ck_live_9f3b1c7a4e2d8865",
    "api_secret": "sk_chaos_0a1b2c3d4e5f60718293a4b5c6d7e8f9",
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.chaos.payload-not-a-real-jwt",
    "feed_token": "ft_2f6d9c1e5b8a4703",
    "client_id": "CH4OS001",
    "partner_secret": "ps_74bd91ee0c3f4a2d",
    "password": "correct-horse-battery-staple",
}


class ChaosChannel(BrokerStreamChannel):
    """A tick channel whose every failure mode is reachable from a frame.

    The wire format is deliberately trivial — one JSON object per frame — so a
    chaos test injects a *condition* rather than a codec puzzle. What matters is
    that every branch a real codec has is present and reachable:
    ticks, an order, an ignorable keep-alive, a broker error frame, an expired
    session and an entitlement refusal.
    """

    name = "market"
    protocol = "chaos_feed"
    delivers = frozenset({StreamEventKind.TICKS})
    #: Overridden per test class where sharding is the subject.
    max_instruments_per_connection: Optional[int] = None
    max_connections: Optional[int] = None

    #: How a refused handshake is classified. `None` is the default every
    #: pre-D5.5 channel has: an ordinary backoff.
    handshake_verdict: Optional[str] = None

    #: The largest frame this codec will look at. A real codec has one because a
    #: real socket has one; a frame past it is refused without being parsed,
    #: which is what stops an oversized frame from becoming an oversized log
    #: line carrying whatever the peer chose to put in it.
    max_frame_bytes: int = 4096

    def endpoint(self, session: dict, credentials: Dict[str, str] = None) -> BrokerStreamEndpoint:
        creds = dict(credentials or {})
        token = (session or {}).get("access_token", "")
        return BrokerStreamEndpoint(
            # A credential in the query string, deliberately: `safe_url` is the
            # control that keeps it out of the log line, and a chaos harness
            # whose endpoint had no secret in it could not test that control.
            url=f"wss://feed.chaos.example/v1/stream?token={token}",
            headers={"Authorization": f"Bearer {creds.get('api_key', '')}"},
        )

    def subscribe_frames(self, instruments: Sequence[Any] = None) -> List[Any]:
        if not instruments:
            return []
        return [json.dumps({"op": "sub", "tokens": [str(i) for i in instruments]})]

    def connect_error(self, error: BaseException) -> Optional[Any]:
        verdict = self.handshake_verdict
        if verdict == "not_entitled":
            return BrokerStreamEvent.not_entitled("this account is not subscribed to the feed")
        if verdict == "auth_expired":
            return BrokerStreamEvent.auth_expired("session is no longer valid")
        if verdict == "misclassified":
            # A codec defect: a handshake failure classified as something that
            # is not terminal. `_terminal_refusal` must refuse to guess.
            return BrokerStreamEvent.error("something went wrong")
        return None

    def decode(self, frame: Any) -> BrokerStreamEvent:
        if isinstance(frame, (bytes, bytearray)):
            if len(frame) > self.max_frame_bytes:
                raise ValueError(f"frame of {len(frame)} bytes exceeds this feed's limit")
            frame = frame.decode("utf-8", errors="ignore")
        if not isinstance(frame, str):
            raise TypeError(f"frame is {type(frame).__name__}")
        if len(frame) > self.max_frame_bytes:
            raise ValueError(f"frame of {len(frame)} characters exceeds this feed's limit")
        if not frame.strip():
            return BrokerStreamEvent.ignore()
        if frame == "PING":
            return BrokerStreamEvent.ignore()
        data = json.loads(frame)  # a malformed frame raises, and must cost only itself
        if not isinstance(data, dict):
            raise TypeError("frame is not an object")
        kind = data.get("t")
        if kind == "expired":
            return BrokerStreamEvent.auth_expired(data.get("msg", "session expired"))
        if kind == "denied":
            return BrokerStreamEvent.not_entitled(data.get("msg", "not entitled"))
        if kind == "err":
            return BrokerStreamEvent.error(data.get("msg", "broker error"))
        if kind == "ack":
            return BrokerStreamEvent.ignore()
        if kind == "order":
            return BrokerStreamEvent.order_event(
                {
                    "order_id": data.get("id"),
                    "symbol": data.get("sym"),
                    "quantity": data.get("qty"),
                    "status": "PENDING",
                    "broker": "chaos",
                },
                broker="chaos",
            )
        if kind == "px":
            return BrokerStreamEvent.tick_event(
                [
                    # `.get`, not `[...]`: a real codec builds the dict and lets
                    # `BrokerTick.from_broker` judge it, which is what makes one
                    # short row cost only itself instead of costing the frame.
                    {"symbol": row.get("sym"), "last_price": row.get("px"),
                     "volume": row.get("vol")}
                    for row in data.get("rows", [])
                    if isinstance(row, dict)
                ]
            )
        return BrokerStreamEvent.ignore()


class ChaosOrderChannel(ChaosChannel):
    """A second connection of the same broker, carrying orders and not ticks.

    Present so every "one channel's failure must not touch the other" claim in
    the audit has something to be false against.
    """

    name = "orders"
    delivers = frozenset({StreamEventKind.ORDER})


class ChaosAdapter(BrokerAdapter):
    """A streaming broker that does not exist, built only from the contract.

    Modelled on `tests/test_broker_streaming.NovaAdapter` and kept separate from
    it deliberately: Nova's job is to prove a *non-Kite* broker can stream, and
    changing it to grow chaos affordances would change what 300 D4 tests assert.
    """

    name = "chaos"
    display_name = "Chaos Securities"
    capabilities = frozenset({BrokerCapability.TICK_STREAM})
    credential_spec = BrokerCredentialSpec(api_key_env="CHAOS_API_KEY")
    default_product = "DELIVERY"
    stream_protocol = "chaos_feed"

    #: The channel class this adapter declares. Overridden by subclasses that
    #: need a per-connection limit (sharding) or a handshake verdict.
    channel_class = ChaosChannel

    def get_login_url(self, state: str = None) -> dict:
        return {"url": "https://chaos.example/login", "configured": True}

    async def exchange_token(self, auth_payload: dict) -> dict:
        return {"access_token": FAKE_CREDENTIALS["access_token"]}

    def session_expiry(self, connected_at):
        from datetime import timedelta

        return connected_at + timedelta(hours=8)

    def stream_instruments(self, holdings: list = None, positions: list = None) -> list:
        rows = list(holdings or []) + list(positions or [])
        return sorted({str(r.get("symbol")).upper() for r in rows if r.get("symbol")})

    def normalize_stream_order(self, payload: dict) -> dict:
        return dict(payload)

    # The adapter-level codec pair the registry requires of any broker
    # declaring a realtime capability (D4.2). Delegated to the channel rather
    # than duplicated: this adapter declares its channels explicitly, so these
    # exist to satisfy the same startup validation every real broker is subject
    # to, and answering them from a second implementation would let the two
    # drift in exactly the way that validation exists to catch.
    def stream_endpoint(self, session: dict, credentials: dict = None) -> BrokerStreamEndpoint:
        return self.channel_class().endpoint(session, credentials)

    def decode_stream_frame(self, frame: Any) -> BrokerStreamEvent:
        return self.channel_class().decode(frame)

    def stream_subscribe_frames(self, instruments: list = None) -> list:
        return self.channel_class().subscribe_frames(instruments)

    def stream_channels(self):
        return (self.channel_class(),)


def chaos_adapter(
    *,
    channel: Optional[type] = None,
    channels: Optional[Sequence[BrokerStreamChannel]] = None,
    capabilities: Optional[frozenset] = None,
) -> ChaosAdapter:
    """Build a one-off chaos broker with the channel shape a test needs."""

    class _Adapter(ChaosAdapter):
        pass

    if channel is not None:
        _Adapter.channel_class = channel
    if capabilities is not None:
        _Adapter.capabilities = capabilities
    if channels is not None:
        _Adapter.stream_channels = lambda self: tuple(channels)  # type: ignore[assignment]
    return _Adapter()


@contextlib.contextmanager
def chaos_registered(adapter: Optional[ChaosAdapter] = None):
    """Register the chaos broker for the body of one test."""
    adapter = adapter or ChaosAdapter()
    broker_registry.register(adapter, replace=True)
    try:
        yield adapter
    finally:
        broker_registry.unregister(adapter.name)


# ══════════════════════════════════════════════════════════════════
# The transport driver
# ══════════════════════════════════════════════════════════════════


class _ScriptExhausted(Exception):
    """Internal: the connection script ran out, so the loop is asked to stop."""


@dataclass
class StreamRun:
    """Everything one driven `BrokerStream._run` produced."""

    ticks: List[Tuple[str, str, list]] = field(default_factory=list)
    orders: List[Tuple[str, str, dict]] = field(default_factory=list)
    expired: List[Tuple[str, str, Optional[str]]] = field(default_factory=list)
    not_entitled: List[Tuple[str, str, Optional[str]]] = field(default_factory=list)
    links: List[Tuple[bool, str, Optional[str]]] = field(default_factory=list)
    sockets: List[ChaosSocket] = field(default_factory=list)
    attempts: int = 0

    @property
    def pauses(self) -> List[float]:
        return list(self.stability.pauses)

    @property
    def outcomes(self) -> List[str]:
        return list(self.stability.outcomes)

    #: Set by the harness.
    stability: RecordingStability = None  # type: ignore[assignment]

    @property
    def tick_batches(self) -> List[list]:
        return [batch for _user, _broker, batch in self.ticks]

    @property
    def link_ups(self) -> int:
        return sum(1 for up, _reason, _channel in self.links if up)

    @property
    def link_downs(self) -> int:
        return sum(1 for up, _reason, _channel in self.links if not up)


class StreamHarness:
    """Drives one `BrokerStream` through a *script of connection attempts*.

    Each element of `attempts` is either

      * a list — the frame script for that connection's socket, or
      * a `Raise` — the handshake itself fails, which is the only way to test
        `connect_error` classification and a connect that never establishes.

    When the script is exhausted the harness stops the stream through the
    transport's own `_stopped` flag, so the loop exits by the route it exits by
    in production rather than by the test cancelling a task.
    """

    def __init__(
        self,
        adapter: ChaosAdapter,
        attempts: Sequence[Any],
        *,
        clock: Optional[ChaosClock] = None,
        instruments: Optional[Sequence[Any]] = None,
        session: Optional[dict] = None,
        credentials: Optional[dict] = None,
        channel: Optional[str] = None,
        shard: str = DEFAULT_SHARD_ID,
        user_id: str = "u1",
        max_attempts: int = 64,
    ) -> None:
        self.adapter = adapter
        self.clock = clock or ChaosClock()
        self._attempts = list(attempts)
        self.result = StreamRun()
        declared = adapter.stream_channels()
        channel_name = channel or (declared[0].name if declared else "default")
        self.stream = BrokerStream(
            user_id,
            adapter.name,
            dict(session if session is not None else {"access_token": FAKE_CREDENTIALS["access_token"]}),
            credentials=dict(credentials if credentials is not None else FAKE_CREDENTIALS),
            instrument_tokens=list(instruments or []),
            on_order_update=self._on_order,
            on_tick=self._on_tick,
            on_expired=self._on_expired,
            on_not_entitled=self._on_not_entitled,
            on_link_state=self._on_link,
            channel=channel_name,
            shard=shard,
        )
        self.stability = RecordingStability(self.clock)
        self.stream._stability = self.stability
        self.result.stability = self.stability
        #: A hard ceiling on loop iterations, independent of the script. It is
        #: the harness's own proof that a chaos case cannot hang the suite — and
        #: a case that reaches it is a *finding*, not a passing test.
        self._max_attempts = max_attempts

    # -- callbacks --------------------------------------------------------
    async def _on_tick(self, user_id, broker, batch):
        self.result.ticks.append((user_id, broker, batch))

    async def _on_order(self, user_id, broker, order):
        self.result.orders.append((user_id, broker, order))

    async def _on_expired(self, user_id, broker, channel=None):
        self.result.expired.append((user_id, broker, channel))

    async def _on_not_entitled(self, user_id, broker, channel=None):
        self.result.not_entitled.append((user_id, broker, channel))

    async def _on_link(self, user_id, broker, up, reason="", channel=None):
        self.result.links.append((bool(up), reason, channel))

    # -- driving ----------------------------------------------------------
    async def _connect(self, endpoint):
        if self.result.attempts >= len(self._attempts) or self.result.attempts >= self._max_attempts:
            self.stream._stopped = True
            raise _ScriptExhausted("the chaos connection script is finished")
        item = self._attempts[self.result.attempts]
        self.result.attempts += 1
        if isinstance(item, Raise):
            raise item.error
        socket = ChaosSocket(item, clock=self.clock)
        self.result.sockets.append(socket)
        return socket

    async def run(self) -> StreamRun:
        """Drive the full reconnect loop until the script is exhausted."""
        with patch.object(BrokerStream, "_connect", self._connect):
            await self.stream._run()
        return self.result

    async def run_once(self) -> StreamRun:
        """Drive exactly one transport pass, propagating its terminal exception.

        The narrower driver, for the cases where the *classification* is the
        subject and the loop's response to it is asserted separately.
        """
        with patch.object(BrokerStream, "_connect", self._connect):
            await self.stream._run_websocket()
        return self.result


# ══════════════════════════════════════════════════════════════════
# Frame vocabulary — chaos payloads
# ══════════════════════════════════════════════════════════════════


def px(*rows: Tuple[str, float]) -> str:
    """A well-formed price frame for the named (symbol, price) pairs."""
    return json.dumps({"t": "px", "rows": [{"sym": s, "px": p} for s, p in rows]})


def denied(message: str = "not entitled") -> str:
    return json.dumps({"t": "denied", "msg": message})


def expired(message: str = "session expired") -> str:
    return json.dumps({"t": "expired", "msg": message})


def err(message: str = "broker error") -> str:
    return json.dumps({"t": "err", "msg": message})


def ack() -> str:
    return json.dumps({"t": "ack"})


#: Frame shapes the **codec** cannot read at all (§5 of the brief).
#:
#: Every one of these must leave the connection alive and produce no
#: `BrokerTick`, because there is nothing in them a tick could be built from.
#: They are separated from `INVALID_VALUE_FRAMES` below deliberately: the two
#: are refused at *different boundaries*, and a table that merged them would
#: pass against an implementation that had collapsed the two boundaries into
#: one — which is exactly the change D4.3 exists to prevent.
MALFORMED_FRAMES: Tuple[Tuple[str, Any], ...] = (
    ("empty", ""),
    ("whitespace", "   "),
    ("truncated_json", '{"t": "px", "rows": [{"sym": "A"'),
    ("not_json", "<<<not json at all>>>"),
    ("json_array", "[1, 2, 3]"),
    ("json_scalar", "42"),
    ("unknown_kind", json.dumps({"t": "quantum"})),
    ("missing_rows", json.dumps({"t": "px"})),
    ("row_missing_price", json.dumps({"t": "px", "rows": [{"sym": "A"}]})),
    ("row_missing_symbol", json.dumps({"t": "px", "rows": [{"px": 1.0}]})),
    ("row_empty", json.dumps({"t": "px", "rows": [{}]})),
    ("rows_not_a_list", json.dumps({"t": "px", "rows": "A"})),
    ("binary_garbage", b"\x00\x01\x02\xff\xfe"),
    ("oversized", "x" * 8192),
    ("oversized_binary", b"\x00" * 8192),
    ("wrong_type", 12345),
)

#: Frame shapes the codec reads *successfully* and whose values the **canonical
#: boundary** must refuse (D4.3 / `MarketTick`).
#:
#: These are the more dangerous half, and the reason they are a separate table:
#: a `BrokerTick` carrying a negative price is a perfectly well-formed statement
#: in the broker's own vocabulary, and it is `MarketTick` that says the platform
#: does not have prices like that. A chaos suite that asserted "the transport
#: drops it" would be asserting a control that does not exist there, and would
#: pass while the real control — one layer up — was removed.
INVALID_VALUE_FRAMES: Tuple[Tuple[str, Any], ...] = (
    ("price_not_a_number", json.dumps({"t": "px", "rows": [{"sym": "A", "px": "abc"}]})),
    ("price_negative", json.dumps({"t": "px", "rows": [{"sym": "A", "px": -5.0}]})),
    ("price_zero", json.dumps({"t": "px", "rows": [{"sym": "A", "px": 0.0}]})),
    ("price_impossible", json.dumps({"t": "px", "rows": [{"sym": "A", "px": 1e15}]})),
    ("price_nan", '{"t": "px", "rows": [{"sym": "A", "px": NaN}]}'),
    ("volume_negative", json.dumps({"t": "px", "rows": [{"sym": "A", "px": 10.0, "vol": -1}]})),
)

#: The canonical records those frames become, as `StreamingTickProvider.on_raw`
#: would be handed them — the same values, at the boundary that judges them.
INVALID_CANONICAL_RECORDS: Tuple[Tuple[str, Any], ...] = (
    ("price_not_a_number", {"symbol": "A", "price": "abc", "exchange": "NSE"}),
    ("price_negative", {"symbol": "A", "price": -5.0, "exchange": "NSE"}),
    ("price_zero", {"symbol": "A", "price": 0.0, "exchange": "NSE"}),
    ("price_impossible", {"symbol": "A", "price": 1e15, "exchange": "NSE"}),
    ("price_nan", {"symbol": "A", "price": float("nan"), "exchange": "NSE"}),
    ("volume_negative", {"symbol": "A", "price": 10.0, "exchange": "NSE", "volume": -1}),
    ("no_symbol", {"symbol": "", "price": 10.0, "exchange": "NSE"}),
    ("not_a_mapping", ["A", 10.0]),
    ("non_canonical_field", {"symbol": "A", "price": 10.0, "instrument_token": 738561}),
    ("feed_shaped_payload", {"sym": "A", "px": 10.0}),
)


# ══════════════════════════════════════════════════════════════════
# Provider-level fixtures
# ══════════════════════════════════════════════════════════════════


@dataclass
class FeedFixture:
    """A registry holding the baseline and one streaming feed, on one clock.

    The same shape the D5.2/D5.3/D5.10 suites build by hand, assembled once here
    because every chaos section past the transport needs it — and because a
    chaos test that built its own would be free to build a *different* one.
    """

    registry: Any
    manager: Any
    baseline: Any
    feed: Any
    clock: ChaosClock
    user_id: str = "u1"

    def quote_provider(self, symbol: str = "A", user_id: Optional[str] = None):
        from services.market_engine.providers import Capability
        from services.market_engine.providers.base import ResolutionContext

        return self.manager.resolve(
            Capability.QUOTES,
            context=ResolutionContext(user_id=user_id or self.user_id, symbol=symbol),
        )

    def tier(self, symbol: Optional[str] = None, user_id: Optional[str] = None):
        from services.market_engine.providers import Capability
        from services.market_engine.providers.base import ResolutionContext

        return self.manager.active_tier(
            Capability.QUOTES,
            context=ResolutionContext(user_id=user_id or self.user_id, symbol=symbol),
        )

    def resolution(self, symbol: Optional[str] = None, user_id: Optional[str] = None):
        from services.market_engine.providers import Capability
        from services.market_engine.providers.base import ResolutionContext

        return self.manager.resolve_feed(
            Capability.QUOTES,
            ResolutionContext(user_id=user_id or self.user_id, symbol=symbol),
        )


def tick(symbol: str = "A", price: float = 100.0, exchange: str = "NSE") -> dict:
    from services.market_engine.ticks import MarketTick

    return MarketTick(symbol=symbol, price=price, exchange=exchange).as_dict()


async def build_feed(
    *,
    shards: Sequence[str] = (DEFAULT_SHARD_ID,),
    symbols: Sequence[str] = ("A",),
    user_id: str = "u1",
    clock: Optional[ChaosClock] = None,
    with_baseline: bool = True,
    link_up: bool = True,
    registry: Any = None,
    manager: Any = None,
) -> "FeedFixture":
    """A connected, subscribed feed beside the polled baseline.

    The state the transport leaves a freshly opened plan in: sockets open,
    subscribe frames away, **nothing delivered yet**. Deliberately not a READY
    feed — every chaos section that needs readiness earns it from a tick, which
    is the only thing D4.5 accepts as evidence.
    """
    from services.market_engine.providers import ProviderRegistry, StreamingTickProvider
    from services.market_engine.providers.yahoo import YahooPollingAdapter
    from services.market_engine.source_manager import SourceManager

    clock = clock or ChaosClock()
    registry = registry if registry is not None else ProviderRegistry()
    baseline = None
    if with_baseline:
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        await baseline.connect()
    feed = StreamingTickProvider(f"feed:{user_id}", owner_user_id=user_id, clock=clock)
    feed.declare_shards(shards)
    registry.register(feed)
    await feed.connect()
    if symbols:
        await feed.subscribe(symbols)
    if link_up:
        for shard in shards:
            await feed.mark_link_up(shard)
    return FeedFixture(
        registry,
        manager if manager is not None else SourceManager(registry),
        baseline,
        feed,
        clock,
        user_id,
    )


async def serve_probation(feed, clock: ChaosClock, shards: Sequence[str], symbol: str = "A") -> None:
    """Give a feed exactly the evidence D5.2 requires, on every connection.

    Written as the *evidence* rather than as a jump to a promoted state, and
    delivered on every shard in step because `_ready_since` is the newest
    connection's while `_last_evidence_at` is the oldest's — serving them one at
    a time leaves the window measured between two different connections and the
    feed never leaves probation. That aggregation is the D5.10 rule, and the
    helper respects it rather than working around it.
    """
    from services.market_engine.providers import PROBATION_WINDOW_SECONDS

    for _ in range(2):
        for shard in shards:
            await feed.on_raw([tick(symbol)], shard)
        clock.advance(PROBATION_WINDOW_SECONDS + 1)
    for shard in shards:
        await feed.on_raw([tick(symbol)], shard)

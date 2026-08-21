"""Realtime broker WebSocket transport — connection management, no wire formats.

Official broker feeds only — no simulated ticks.

Each connected broker account gets one background task with jittered
exponential-backoff reconnect. Decoded updates are forwarded through callbacks
supplied by the Broker Engine (order upsert + per-user app WebSocket push),
keeping this module transport-only.

WHAT D3 CHANGED HERE
--------------------
Dispatch became protocol-based. The run loop used to read

    if self.broker == "zerodha": ... elif self.broker == "upstox": ...

a chain that grows by one branch per broker in a module no broker owns.

WHAT D4.2 CHANGED HERE — AND WHY IT WENT FURTHER
-------------------------------------------------
D3 removed the broker *names* but left every broker's *wire format* here: Kite's
ticker URL, Kite's binary packet layout, Kite's two subscribe frames, Kite's
error-frame convention, and Upstox's JSON envelope all lived in this file. So
adding a streaming broker still meant editing shared code, and — worse — the
tick shape the platform consumed was whatever the parser in this module happened
to build. `portfolio_stream` and `trade_stream` both document their input as
``[{instrument_token, last_price}]``, which was true only because exactly one
broker's parser produced it. A second broker emitting ``{"token", "ltp"}`` would
have connected cleanly and silently stopped every live P&L recompute for its
users.

All of it moved behind :meth:`BrokerAdapter.decode_stream_frame`. What is left
here is the part that genuinely is identical for every broker:

  * open the connection the adapter describes,
  * send the frames the adapter says to send,
  * hand each raw frame to the adapter and take back a `BrokerStreamEvent`,
  * refuse any event the broker's capabilities do not cover,
  * forward canonical data, and reconnect with jittered backoff.

Two properties follow, and both are asserted rather than hoped for:

  * **No broker name or protocol detail appears in this file** — it is now
    inside the same core-module ban as `broker_engine.py` and the gateway.
  * **A raw broker payload cannot pass through.** The decoded value is
    type-checked; only canonical `BrokerTick` / `BrokerOrder` shapes continue up.

Adding a WebSocket broker therefore changed nothing here at all — until a
broker turned out to need two of them. See the D4.7 note below.

What this module forwards is still a `BrokerTick` — broker-identified. D4.3 put
the canonical boundary immediately above it, in `BrokerEngine._on_stream_tick`,
where the account's `InstrumentMap` turns that identifier into a canonical
symbol and the broker's handle stops. The transport is deliberately not the
place for that: mapping needs the account's synced portfolio, which is the
engine's to hold, and a transport that reached for it would be back to knowing
things about brokers.

Since D4.4 the canonical batch the engine produces also enters the Market
Gateway, through a `MarketDataProvider` the broker side registers per account
(`services/brokers/market_feed.py`). None of that is visible here: this module
still forwards a `BrokerTick` to one callback and knows nothing about providers,
capabilities or tiers.

WHAT D4.6 ADDED — AND WHY IT IS NOT A BROKER BRANCH
----------------------------------------------------
One `try` around the connect call. `decode_stream_frame` can only classify a
failure the broker reports *in a frame*, which presupposes a connection; a
broker that refuses a dead session during the WebSocket handshake produces no
frame at all, so the codec never sees it and this loop saw only "connect
raised". An expired token was therefore indistinguishable from a broker outage
and reconnected on the backoff schedule indefinitely, with the account's market
feed left registered and the user never asked to reconnect.

`BrokerAdapter.stream_connect_error` asks the adapter what its broker's
rejection meant. The adapter *classifies*; this module *acts*, by raising the
same `_AuthExpired` a frame-reported expiry raises, so one expiry path serves
both. The default answer is `None`, so no other adapter is affected and this
file still names no broker.

WHAT D4.7 CHANGED — THE ASSUMPTION KITE HID
--------------------------------------------
D4.2 claimed that "adding a WebSocket broker changes nothing here at all". That
was true of every broker whose realtime surface is one socket, and it stopped
being true at the second streaming broker, so it is corrected rather than
quietly left standing.

One assumption survived every previous sprint because Kite could not expose it:
that a broker has *one* connection. `BrokerStream` held one endpoint, one codec
and one protocol, and `BrokerStreamManager` keyed its registry on
`(user, broker)`. Upstox serves order updates on its v2 portfolio stream and
market ticks on a separate v3 feed — different host, different encoding,
different subscription model — and under the old key its second `start_stream`
would have silently *replaced* the first: one feed live, one feed gone, nothing
raised anywhere.

The generalisation is :class:`~services.brokers.streaming.BrokerStreamChannel`:
a name, a protocol and a codec. This module opens one connection per channel and
still cannot tell one broker from another — a channel is as opaque to it as an
adapter was. A broker that has never heard of channels gets exactly one, backed
by the same five adapter methods it always implemented, so Kite is unchanged
byte for byte.

Two things moved with it, both because a broker's connections fail
*independently*:

  * the link-state callback carries the channel, so a consumer can tell which
    connection came back — without it, a broker's order socket blinking would
    demote a market feed that is delivering prices perfectly well;
  * a decoded event is checked against the *channel's* declared `delivers`
    before the broker-level capability gate, because a broker that declares
    TICK_STREAM on one channel would otherwise let its other channel deliver
    ticks it has no prices on.

Still out of scope, deliberately (D5+): probation windows, latency scoring,
flap suppression, and sharding one subscription across several connections —
which is a different thing from a broker needing several connections, and is
still not done here.
"""

import asyncio
import logging
import random

from services.brokers.errors import BrokerContractError
from services.brokers.streaming import (
    DEFAULT_STREAM_CHANNEL,
    BrokerStreamEndpoint,
    BrokerStreamEvent,
    StreamEventKind,
)

logger = logging.getLogger(__name__)

RECONNECT_BASE_DELAY = 2  # seconds
RECONNECT_MAX_DELAY = 60  # seconds


def reconnect_pause(delay: float) -> float:
    """Equal-jitter backoff: half the current ceiling, plus a random half.

    The ceiling still doubles deterministically; only the *sleep* is randomized.

    Without this every stream reconnects in lockstep, and the reason is
    structural rather than unlucky: one broker-side blip disconnects every
    connected user's socket in the same instant, so an unjittered schedule has
    all of them retry in the same instant too — then again 2s later, 4s later,
    8s later, as a synchronized herd that grows with the user count and hits the
    broker hardest at exactly the moment it is least able to answer.

    Equal jitter rather than full jitter (`uniform(0, delay)`) because full
    jitter can roll a near-zero pause, which turns a still-down broker into a
    tight retry loop for whichever stream got the low number. Keeping a floor at
    half the ceiling preserves the backoff's purpose while still decorrelating
    the herd.
    """
    half = delay / 2.0
    return half + random.uniform(0.0, half)


class BrokerStream:
    """One live WebSocket connection for one (user, broker) account."""

    def __init__(
        self,
        user_id: str,
        broker: str,
        session: dict,
        credentials: dict = None,
        instrument_tokens: list = None,
        on_order_update=None,
        on_tick=None,
        on_expired=None,
        on_link_state=None,
        channel: str = DEFAULT_STREAM_CHANNEL,
    ):
        self.user_id = user_id
        self.broker = broker
        #: Which of this broker's realtime channels this connection serves
        #: (D4.7). A string rather than the channel object because the object is
        #: resolved from the registry at use time, exactly as `_adapter` is:
        #: a long-lived stream holding a codec instance would keep serving a
        #: replaced adapter's wire format after a re-registration.
        self.channel = (channel or DEFAULT_STREAM_CHANNEL).strip() or DEFAULT_STREAM_CHANNEL
        self.session = session
        #: Credential material supplied by the adapter through the gateway. A
        #: dict rather than a bare `api_key` because what a transport needs is
        #: the transport's business: one broker authenticates by query string,
        #: another by bearer header, and the next by something else again.
        self.credentials = dict(credentials or {})
        #: Opaque broker instrument identifiers, produced by the adapter's
        #: `stream_instruments` and understood only by the adapter that made
        #: them. This module counts them and passes them back; it never reads
        #: one.
        self.instrument_tokens = instrument_tokens or []
        self.on_order_update = on_order_update
        self.on_tick = on_tick
        self.on_expired = on_expired
        #: Called `(user_id, broker, up: bool, reason: str)` when this
        #: connection is established and when it is lost (D4.5).
        #:
        #: The transport is the only party that knows the instant a socket dies,
        #: so it is the only party that can make failover immediate. Without
        #: this the market side would have to *notice* the silence, and noticing
        #: silence means a timer — a poll loop in everything but name, which
        #: MARKET_DATA_ARCHITECTURE.md's streaming path exists to eliminate.
        #:
        #: It carries connection state and nothing else: no session, no
        #: credentials, no ticks. A reconnect is reported as a fresh `up`, which
        #: is what makes the consumer re-earn whatever it had concluded about
        #: the previous connection.
        self.on_link_state = on_link_state
        self._link_up = False
        self._stopped = False
        self._task = None

    # -- lifecycle -----------------------------------------------------------
    def start(self):
        self._task = asyncio.create_task(self._run(), name=f"broker-stream-{self.broker}-{self.user_id}")
        return self._task

    async def _notify_link(self, up: bool, reason: str = ""):
        """Report a connection state change once, best-effort.

        Change-gated: a transport that reconnects reports `up` again, and a
        consumer must see one transition per real transition rather than one per
        loop iteration. Failures are swallowed — a consumer's bookkeeping error
        must never drop a live market feed.
        """
        if up == self._link_up:
            return
        self._link_up = up
        if not self.on_link_state:
            return
        try:
            await self.on_link_state(self.user_id, self.broker, up, reason, self.channel)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("%s %s stream link-state callback failed: %s", self.broker, self.channel, e)

    async def stop(self):
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # -- main loop with reconnect ---------------------------------------------
    @property
    def _adapter(self):
        """The adapter owning this connection — the only broker-aware lookup."""
        from services.brokers.registry import broker_registry

        return broker_registry.require(self.broker)

    @property
    def _codec(self):
        """The channel object this connection speaks through, or None.

        Resolved by name from the adapter every time rather than held, for the
        same reason `_adapter` is: a stream that cached the codec would keep
        decoding with a replaced adapter's wire format after a re-registration.

        `None` means the broker no longer declares a channel by this name, which
        is a configuration change rather than a runtime error — the run loop
        stops instead of reconnecting into a channel that does not exist.
        """
        for channel in self._adapter.stream_channels() or ():
            if channel.name == self.channel:
                return channel
        return None

    async def _run(self):
        delay = RECONNECT_BASE_DELAY
        while not self._stopped:
            try:
                codec = self._codec
                if codec is None:
                    logger.warning("Broker %s declares no stream channel %r", self.broker, self.channel)
                    return
                runner = resolve_transport(codec)
                if runner is None:
                    logger.warning(
                        "No stream transport for protocol %r (broker %s channel %s)",
                        codec.protocol, self.broker, self.channel,
                    )
                    return
                try:
                    await runner(self)
                finally:
                    # Every exit from a transport run is a lost connection —
                    # clean close, broker error, cancellation, dead token. Put
                    # in `finally` rather than after each branch so a transport
                    # added later cannot leave the consumer believing a link is
                    # up that has in fact ended.
                    await self._notify_link(False, "stream ended")
                delay = RECONNECT_BASE_DELAY  # clean close → quick reconnect
            except asyncio.CancelledError:
                raise
            except _AuthExpired:
                logger.info(
                    "%s %s stream token expired for user %s; stopping stream.",
                    self.broker, self.channel, self.user_id,
                )
                if self.on_expired:
                    try:
                        await self.on_expired(self.user_id, self.broker, self.channel)
                    except Exception:
                        pass
                return
            except Exception as e:
                logger.warning("%s %s stream error for user %s: %s", self.broker, self.channel, self.user_id, e)
            if self._stopped:
                return
            await asyncio.sleep(reconnect_pause(delay))
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _connect(self, endpoint: BrokerStreamEndpoint):
        import websockets

        kwargs = dict(
            additional_headers=endpoint.headers or None,
            ping_interval=endpoint.ping_interval,
            ping_timeout=endpoint.ping_timeout,
        )
        if endpoint.subprotocols:
            kwargs["subprotocols"] = list(endpoint.subprotocols)
        try:
            return await websockets.connect(endpoint.url, **kwargs)
        except TypeError:
            # websockets < 14 uses extra_headers
            kwargs["extra_headers"] = kwargs.pop("additional_headers")
            return await websockets.connect(endpoint.url, **kwargs)

    # -- the generic transport -------------------------------------------------
    async def _run_websocket(self):
        """Connect, subscribe, and pump frames through the adapter's codec.

        The whole transport. Every broker-specific answer it needs — the URL,
        the auth material, the subscribe frames, the meaning of a frame — comes
        from the adapter, so this body does not change when a broker is added.
        """
        codec = self._codec
        endpoint = codec.endpoint(self.session, self.credentials)
        try:
            ws = await self._connect(endpoint)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A broker that rejects a dead session during the handshake never
            # sends the error *frame* `decode_stream_frame` would classify, so
            # the codec cannot see this failure at all. Ask the adapter what its
            # broker's rejection means; a `None` answer — the default — leaves
            # the exception to the normal backoff, unchanged. See
            # `BrokerAdapter.stream_connect_error`.
            expired = codec.connect_error(exc)
            if expired:
                raise _AuthExpired(expired) from exc
            raise
        # `safe_url`, never `url`: a broker that authenticates by query string
        # puts a live access token in it, and SECURITY.md forbids credentials in
        # logs. See BrokerStreamEndpoint.safe_url.
        logger.info(
            "%s %s stream connected for user %s (%s)",
            self.broker, self.channel, self.user_id, endpoint.safe_url,
        )
        try:
            for frame in codec.subscribe_frames(self.instrument_tokens) or ():
                await ws.send(frame)
            # Announced after the subscribe frames are away, not on the socket
            # opening: an open socket nobody has asked anything of delivers
            # nothing, and reporting the link up before then would hand the
            # consumer the "connected therefore ready" conflation that D4.5's
            # readiness gate exists to refuse. It is still only a *link* signal —
            # the consumer treats it as evidence of nothing more.
            await self._notify_link(True)
            async for message in ws:
                await self._dispatch(self._decode(message))
        finally:
            await ws.close()

    def _decode(self, message) -> BrokerStreamEvent:
        """Run one raw frame through the adapter's codec, defensively.

        Three failure modes, each of which must leave a live connection alone:

        * the codec raises — one malformed frame must never drop a socket that
          is otherwise delivering good prices;
        * the codec returns something that is not a `BrokerStreamEvent` — this
          is the barrier that stops a raw broker payload from continuing up. A
          codec that returns its own dict does not leak; it produces nothing and
          says so in the log;
        * the codec returns an event carrying an unusable record, which
          `BrokerStreamEvent` itself refuses to construct.

        Logged at warning rather than debug: a codec that decodes nothing looks
        exactly like a quiet market from the outside, and that is precisely the
        failure this whole boundary exists to make visible.
        """
        codec = self._codec
        try:
            event = codec.decode(message)
        except BrokerContractError as e:
            logger.warning("%s %s stream frame rejected by the contract: %s", self.broker, self.channel, e)
            return BrokerStreamEvent.ignore()
        except Exception as e:
            logger.warning("%s %s stream frame could not be decoded: %s", self.broker, self.channel, e)
            return BrokerStreamEvent.ignore()
        if not isinstance(event, BrokerStreamEvent):
            logger.error(
                "%s %s codec returned %s instead of BrokerStreamEvent — frame dropped",
                self.broker,
                self.channel,
                type(event).__name__,
            )
            return BrokerStreamEvent.ignore()
        return event

    async def _dispatch(self, event: BrokerStreamEvent):
        """Deliver one decoded event, after the capability gate has allowed it."""
        if event.kind is StreamEventKind.IGNORE:
            return

        if event.kind is StreamEventKind.AUTH_EXPIRED:
            raise _AuthExpired(event.message or "broker reported an expired session")

        if event.kind is StreamEventKind.ERROR:
            logger.warning("%s stream reported an error: %s", self.broker, event.message)
            return

        codec = self._codec
        if codec is not None and event.kind not in codec.delivers:
            # The channel decoded something it does not carry (D4.7). Dropped
            # ahead of the broker-level gate because the broker may legitimately
            # declare the capability on a *different* channel, so the capability
            # check would let this through: a broker whose order channel emitted
            # a tick would drive that account's market-data provider from a
            # socket carrying no market data, and mark a live portfolio with it.
            logger.warning(
                "%s channel %s decoded a %s event it does not carry — dropped",
                self.broker,
                self.channel,
                event.kind.value,
            )
            return

        from services.brokers.gateway import broker_gateway

        if not broker_gateway.stream_event_allowed(self.broker, event.kind):
            # The adapter decoded something its own capability set does not
            # cover. Dropped rather than delivered: the capability model is the
            # authority on what a broker serves, and a codec may not widen it.
            logger.warning(
                "%s decoded a %s event without declaring the capability — dropped", self.broker, event.kind.value
            )
            return

        if event.kind is StreamEventKind.TICKS and self.on_tick:
            # `as_dict()` rather than the dataclass: `contracts.py` explains why
            # dicts are the currency at this boundary — these go straight into
            # MongoDB, onto the Event Bus and out as JSON. The dataclass is the
            # definition; the dict is what flows.
            await self.on_tick(self.user_id, self.broker, [tick.as_dict() for tick in event.ticks])
        elif event.kind is StreamEventKind.ORDER and self.on_order_update:
            await self.on_order_update(self.user_id, self.broker, event.order)


#: Wire protocol -> transport coroutine, for protocols the generic WebSocket
#: transport cannot serve.
#:
#: Empty by design. Every broker on a WebSocket — which is every broker the
#: platform supports and every one on the roadmap — is served by
#: `_run_websocket`, because after D4.2 the only per-broker differences are the
#: endpoint, the subscribe frames and the codec, and all three come from the
#: adapter. An entry belongs here only for a protocol that is not a WebSocket at
#: all (a long-poll feed, a raw TCP session), where connection management itself
#: differs rather than the payloads.
#:
#: Kept as a lookup rather than deleted so that such a broker adds an entry
#: instead of reintroducing a branch — the property D3 established and this
#: table preserves.
PROTOCOL_RUNNERS = {}


def resolve_transport(source):
    """The transport coroutine for a stream channel, or None if it has no protocol.

    Protocol-specific override first, generic WebSocket transport otherwise.
    A channel with no protocol gets None and never connects — checked here
    rather than at the call site so "does this stream" has one answer.

    Accepts a :class:`~services.brokers.streaming.BrokerStreamChannel` or, for
    the "does this broker stream at all" question, an adapter — whose answer is
    its default channel's protocol. Both are asked by name (`protocol` /
    `stream_protocol`) and neither is a broker: this function still cannot tell
    Kite from Upstox.
    """
    protocol = (getattr(source, "protocol", None) or getattr(source, "stream_protocol", "") or "").strip()
    if not protocol:
        return None
    return PROTOCOL_RUNNERS.get(protocol, BrokerStream._run_websocket)


class _AuthExpired(Exception):
    """Raised inside a stream loop when the broker reports a dead token."""


class BrokerStreamManager:
    """Owns every live broker stream: start/stop/replace per (user, broker, channel).

    Keyed on the channel as well as the account since D4.7. The key used to be
    `(user, broker)`, which was not a simplification but an assumption — that a
    broker's realtime surface is one socket — and it held only because Kite
    multiplexes ticks and order updates onto one connection. A broker that needs
    two would have had its second `start_stream` silently *replace* the first,
    leaving one feed live, one feed gone, and nothing raised.
    """

    def __init__(self):
        self._streams: dict = {}  # (user_id, broker, channel) -> BrokerStream

    async def start_stream(
        self,
        user_id: str,
        broker: str,
        session: dict,
        credentials: dict = None,
        instrument_tokens: list = None,
        on_order_update=None,
        on_tick=None,
        on_expired=None,
        on_link_state=None,
        channel: str = DEFAULT_STREAM_CHANNEL,
    ):
        channel = (channel or DEFAULT_STREAM_CHANNEL).strip() or DEFAULT_STREAM_CHANNEL
        await self.stop_stream(user_id, broker, channel)
        stream = BrokerStream(
            user_id,
            broker,
            session,
            credentials=credentials,
            instrument_tokens=instrument_tokens,
            on_order_update=on_order_update,
            on_tick=on_tick,
            on_expired=on_expired,
            on_link_state=on_link_state,
            channel=channel,
        )
        self._streams[(user_id, broker, channel)] = stream
        stream.start()
        return stream

    def _keys(self, user_id: str, broker: str, channel: str = None) -> list:
        """Every registry key for an account, or just the one channel's.

        `channel=None` means "every channel of this account", which is what the
        account-level callers want — disconnect, shutdown, session expiry. They
        pass no channel and must not have to enumerate a broker's channels
        themselves; asking a broker how many sockets it has is exactly the
        knowledge this module does not hold.
        """
        return [
            key
            for key in list(self._streams)
            if key[0] == user_id and key[1] == broker and (channel is None or key[2] == channel)
        ]

    async def stop_stream(self, user_id: str, broker: str, channel: str = None):
        """Stop one channel, or every channel of an account when `channel` is None."""
        for key in self._keys(user_id, broker, channel):
            stream = self._streams.pop(key, None)
            if stream:
                await stream.stop()

    def discard(self, user_id: str, broker: str, channel: str = None) -> bool:
        """Forget a stream that has already ended on its own (PH3.6).

        Deliberately NOT `stop_stream`. The one caller is the broker's
        token-expiry callback, which runs *inside* the stream's own task just
        before that task returns — and `stop()` would `cancel()` and then
        `await` the very task doing the calling, which is a task awaiting
        itself.

        Without this the registry kept the finished `BrokerStream` forever: one
        stale entry per (user, broker) whose token expired, each still holding
        its `session` dict — that is, an expired broker **access token** — and
        its callbacks, and each still listed by `status()` as a stream that
        exists but is not running.

        Scoped to the calling channel since D4.7. Discarding an account's other
        channels here would drop live streams from the registry without
        stopping them, leaking exactly the task `stop_stream` exists to cancel.
        """
        discarded = False
        for key in self._keys(user_id, broker, channel):
            discarded = self._streams.pop(key, None) is not None or discarded
        return discarded

    async def stop_all(self):
        for key in list(self._streams):
            await self.stop_stream(*key)

    def status(self) -> list:
        return [
            {
                "user_id": user_id,
                "broker": broker,
                "channel": channel,
                "running": stream.running,
                "subscribed_instruments": len(stream.instrument_tokens),
            }
            for (user_id, broker, channel), stream in self._streams.items()
        ]


stream_manager = BrokerStreamManager()

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

WHAT D4.9 ADDED — A KEEP-ALIVE THE PROTOCOL'S OWN PING IS NOT
--------------------------------------------------------------
One optional timer, declared on the endpoint rather than requested by a broker.
`ping_interval` here has always configured the WebSocket protocol's own ping
frames, which the library exchanges with the peer's library and neither
application sees. The third streaming broker does not count those: Angel One's
feed requires a keep-alive **in the data channel** and closes a connection that
stops sending one, so a socket would connect, subscribe, deliver ticks for half
a minute and be closed — repeatedly, on the reconnect schedule, looking from
outside like a flapping feed rather than a missing frame.

The split is the same one this module has made since D4.2: *what* the frame is
is the adapter's (`BrokerStreamEndpoint.heartbeat_frame`), and sending it on a
timer and cancelling it with the connection is the transport's. A broker that
ran its own timer would own a task whose lifetime must match a connection it
does not hold — and a task leaked per reconnect is forever on a flapping feed.
Both current adapters declare no heartbeat and are unaffected.

WHAT D4.10 ADDED — A CONNECTION SCOPE THE CODEC NEVER HAD
----------------------------------------------------------
Three lines: ask the channel for its view of *this* connection before the first
frame is sent, use it for the subscribe frames and the decode, drop it when the
socket ends. `BrokerStreamChannel.open()` returns `self` by default, so the
three brokers that came before are byte-for-byte unaffected and this module
still cannot tell one from another.

What forced it is a feed whose frames are not independently decodable. The
fourth streaming broker sends one *snapshot* per instrument — the identity and
the price scale — and then updates that carry a small numeric handle and the
changed values alone, so a steady-state frame means nothing without what earlier
frames on the same socket established. A channel object is a registry singleton
shared by every user of the broker, so that state could not live there: one
account's reconnect renumbers another account's instruments, and the failure is
a price attributed to the wrong company's holding, with nothing raised anywhere.

The same scope answers a second thing the contract could not express — a feed
that authenticates with a frame on the data channel rather than in the
handshake, so the first thing it sends is a credential `subscribe_frames()` had
no argument to reach.

Widening `subscribe_frames` / `decode` instead was rejected for the reason D4.7
rejected widening the adapter methods: it changes what every existing channel
and every test double implements, so an unmigrated broker fails on a live socket
rather than at import. A broker that has never heard of connection scope *is* a
stateless broker, which is what it always was.

WHAT D5.1 CHANGED — WHEN THE BACKOFF IS ALLOWED TO RESET
---------------------------------------------------------
One condition, and it closes DB-5. This loop used to reset its reconnect delay
after any connection that *completed*, with a comment reading "clean close →
quick reconnect" — but the code could not see a clean close. A socket a broker
accepted and closed one frame later reached that line exactly as a socket that
had streamed all session, so a broker saying "stop doing this" produced a
reconnect roughly every 1.5 seconds, indefinitely, against a broker whose own
documentation warns that continuing may get the user blocked.

The ladder and the flap detector that now gates it live in `reliability.py`;
what changed here is that the reset is driven by `_notify_link` — the one place
that already knows a link transition happened exactly once — instead of by the
transport having returned. A connection that lasted resets the ladder, and every
other outcome leaves it climbing. The healthy path is byte-for-byte unchanged in
behaviour: a long-lived feed that drops still reconnects in ~1–2 seconds.

WHAT D5.5 ADDED — THE SECOND TERMINAL CONDITION
------------------------------------------------
Until D5.5 this loop had exactly two shapes of answer to a broker that stopped
serving: stop the account's session (`AUTH_EXPIRED`) or keep reconnecting
(everything else). An account that is *not entitled* to a feed fits neither. Its
token is valid — REST portfolio, funds, order placement and the order stream all
keep working — so tearing the session down destroys working functionality and
tells the user something untrue; and reconnecting cannot make an unlicensed
account licensed, so the alternative is the churn D5.1 paces and never stops.

`_NotEntitled` is the third exit, and it is deliberately the *narrowest* one:

  * it ends **this channel** and no other. An entitlement is a statement about a
    capability, not about a login, so a broker refusing its market feed must not
    take down the same account's order socket;
  * it does **not** reconnect. Coming back requires a deliberate lifecycle event
    — `start_stream` after the user reconnects, a session restore at startup —
    never this loop's own schedule;
  * it says nothing about any other user. One `BrokerStream` is one
    `(user, broker, channel)`, so the scope is structural rather than a rule
    anybody has to enforce.

Both routes into it are the adapter's classification and neither is an
inference: a `NOT_ENTITLED` event decoded from a frame, or a `connect_error`
that returns one for a handshake the broker refused on entitlement grounds.
Silence, a timeout, an empty subscription and a malformed frame are all *absence
of evidence* and none of them can produce it. See ADR-045.

Still out of scope, deliberately: latency p95, instrument sharding across
several connections — which is a different thing from a broker needing several
connections, and is still not done here — and chaos testing.
"""

import asyncio
import contextlib
import logging
from typing import Optional

from services.brokers.errors import BrokerContractError
from services.brokers.reliability import (  # noqa: F401  (re-exported: see below)
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_DELAY,
    STABLE_CONNECTION_SECONDS,
    ConnectionOutcome,
    ConnectionStability,
    reconnect_pause,
)
from services.brokers.streaming import (
    DEFAULT_STREAM_CHANNEL,
    BrokerStreamEndpoint,
    BrokerStreamEvent,
    StreamEventKind,
)

logger = logging.getLogger(__name__)

# The reconnect ladder and its jitter moved to `reliability.py` in D5.1, where
# the flap detector that now drives them lives — they are reconnect *policy*,
# and splitting policy from the run loop is the reason that module exists. They
# are re-exported here unchanged because this is the name every caller and every
# test has always imported them under, and renaming a constant is not a fix for
# anything.


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
        on_not_entitled=None,
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
        #: Called `(user_id, broker, channel)` when the broker explicitly says
        #: this account may not consume what THIS channel carries (D5.5).
        #:
        #: A separate callback from `on_expired` rather than a reason argument on
        #: it, because the two have different blast radii and the difference is
        #: the whole point: an expired token finishes the account's session, an
        #: entitlement refusal finishes one feed and leaves the session — and
        #: every other channel, and every other user — working. One callback with
        #: a flag would have put that distinction in the hands of every consumer
        #: to remember; two callbacks put it in the type.
        self.on_not_entitled = on_not_entitled
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
        #: The channel's per-CONNECTION view, for the connection currently open
        #: (D4.10). `None` between connections, and set fresh on every one — a
        #: broker whose codec accumulates state across frames must start each
        #: socket from nothing, because the state a dead socket left behind
        #: describes instruments the new one has not been told about yet. Every
        #: channel that has no such state returns itself here, so for three of
        #: the four brokers this attribute simply holds the channel.
        self._connection = None
        #: This connection's reconnect ladder and flap detector (D5.1). One per
        #: stream — that is, per (user, broker, channel) — so one user's
        #: flapping session cannot pace another user's reconnects, and a
        #: broker's order socket blinking cannot slow its market feed.
        self._stability = ConnectionStability()

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
        # The reconnect ladder is driven from here rather than from the run loop
        # because this is the one place that already knows a transition happened
        # exactly once (D5.1). A transport added to `PROTOCOL_RUNNERS` later
        # therefore gets flap suppression by reporting its link state, which it
        # must do anyway, instead of by remembering to opt in.
        if up:
            self._stability.link_up()
        else:
            self._note_connection_ended(self._stability.link_down())
        if not self.on_link_state:
            return
        try:
            await self.on_link_state(self.user_id, self.broker, up, reason, self.channel)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("%s %s stream link-state callback failed: %s", self.broker, self.channel, e)

    def _note_connection_ended(self, outcome: ConnectionOutcome):
        """Say out loud when a connection died before it proved stable (D5.1).

        Logged at warning because this is the failure that reads as nothing at
        all: before D5.1 the loop reset its backoff after any connection that
        completed, so a broker accepting and immediately closing a socket
        produced a reconnect roughly every 1.5 seconds forever, and every
        individual line of that storm looked like a routine reconnect. Naming
        the streak is what makes the pattern visible in the logs rather than
        only in the broker's rate limiter.

        Carries no credential, no session and no frame — a broker name, a
        channel name, a user id and two counters (SECURITY.md).
        """
        if outcome is not ConnectionOutcome.SHORT_LIVED:
            return
        logger.warning(
            "%s %s stream for user %s closed after less than %.0fs — "
            "treating it as a flap (%d in a row); next reconnect backs off further",
            self.broker,
            self.channel,
            self.user_id,
            STABLE_CONNECTION_SECONDS,
            self._stability.consecutive_short_connections,
        )

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
            except asyncio.CancelledError:
                raise
            except _AuthExpired:
                await self._finish_expired()
                return
            except _NotEntitled as refusal:
                # Terminal for THIS channel and for nothing else (D5.5). The
                # `return` is the substance of the sprint: every other exit from
                # this loop falls through to `next_pause()` and reconnects, and
                # reconnecting into an explicit "this account is not subscribed"
                # cannot make the account subscribed — it can only produce the
                # churn D5.1 paces but never stops, against a broker that has
                # just said to stop. Coming back requires a deliberate lifecycle
                # event (`start_stream` after a reconnect, a session restore),
                # never this loop's own schedule.
                await self._finish_not_entitled(str(refusal))
                return
            except Exception as e:
                logger.warning("%s %s stream error for user %s: %s", self.broker, self.channel, self.user_id, e)
            if self._stopped:
                return
            # The ladder was reset by `_notify_link` if — and only if — the
            # connection that just ended had lasted (D5.1, DB-5). It used to be
            # reset unconditionally right here, on the strength of the transport
            # having *returned*, which a socket the broker accepted and closed
            # one frame later does exactly as readily as a socket that ran all
            # session.
            await asyncio.sleep(self._stability.next_pause())

    async def _finish_expired(self):
        """Report a dead token and stop. The account's session is finished."""
        logger.info(
            "%s %s stream token expired for user %s; stopping stream.",
            self.broker, self.channel, self.user_id,
        )
        if self.on_expired:
            try:
                await self.on_expired(self.user_id, self.broker, self.channel)
            except Exception:
                pass

    async def _finish_not_entitled(self, reason: str):
        """Report a refused entitlement and stop THIS channel (D5.5).

        A sibling of :meth:`_finish_expired` rather than a branch inside it,
        because the two say different things to different consumers and the run
        loop must not be the place that decides which. The reason is the
        broker's own message text and carries no credential; the log line adds a
        broker name, a channel name and a user id, exactly as the expiry path
        does (SECURITY.md).

        A consumer that raises is swallowed for the same reason it is on the
        expiry path: the channel is already finished, and a bookkeeping error
        must not turn a clean stop into an unhandled task exception.
        """
        logger.warning(
            "%s %s stream is not entitled for user %s: %s — stopping this channel; "
            "the session and every other channel are unaffected.",
            self.broker, self.channel, self.user_id, reason,
        )
        if self.on_not_entitled:
            try:
                await self.on_not_entitled(self.user_id, self.broker, self.channel)
            except Exception:
                pass

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
            refusal = _terminal_refusal(codec.connect_error(exc))
            if refusal is not None:
                raise refusal from exc
            raise
        # `safe_url`, never `url`: a broker that authenticates by query string
        # puts a live access token in it, and SECURITY.md forbids credentials in
        # logs. See BrokerStreamEndpoint.safe_url.
        logger.info(
            "%s %s stream connected for user %s (%s)",
            self.broker, self.channel, self.user_id, endpoint.safe_url,
        )
        heartbeat = self._start_heartbeat(ws, endpoint)
        # The channel's view of THIS connection (D4.10). Established before a
        # single frame is sent or received, so the frames that open the
        # conversation and the frames that answer it are handled by one object
        # with one lifetime — the socket's. Cleared in the `finally` below, so a
        # reconnect cannot decode against the dead connection's state.
        self._connection = codec.open(self.session, self.credentials) or codec
        try:
            for frame in self._connection.subscribe_frames(self.instrument_tokens) or ():
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
            self._connection = None
            if heartbeat is not None:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await heartbeat
            await ws.close()

    def _start_heartbeat(self, ws, endpoint: BrokerStreamEndpoint):
        """Send the feed's application-level keep-alive until the socket ends.

        `None` when the endpoint declares none, which is every feed whose
        liveness the WebSocket protocol's own pings already satisfy.

        Lives here rather than in an adapter for the reason every other part of
        this loop does: a broker that started its own timer would own a task
        whose lifetime has to match a connection it does not hold, and getting
        that wrong leaks a task per reconnect — forever, on a flapping feed. The
        frame's *content* is the adapter's (see `BrokerStreamEndpoint`); the
        timer and its cancellation are the transport's.

        A send failure is not raised: the socket is already ending, and the
        iterator in the caller is where that is observed and reconnected from.
        """
        frame = endpoint.heartbeat_frame
        interval = endpoint.heartbeat_interval
        if frame is None or not interval:
            return None

        async def beat():
            while True:
                await asyncio.sleep(interval)
                try:
                    await ws.send(frame)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.debug(
                        "%s %s stream keep-alive could not be sent: %s", self.broker, self.channel, e
                    )
                    return

        return asyncio.create_task(
            beat(), name=f"broker-stream-keepalive-{self.broker}-{self.channel}-{self.user_id}"
        )

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
        # The connection's own view when one is open (D4.10), the channel
        # otherwise — the latter for a direct caller outside a transport pass.
        # For a stateless channel these are the same object, because `open()`
        # returned `self`.
        codec = self._connection if self._connection is not None else self._codec
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

        if event.kind is StreamEventKind.NOT_ENTITLED:
            # Ungated by capability, exactly as AUTH_EXPIRED is: a refusal must
            # be actionable on any stream, and a broker that mis-declared what it
            # serves must not thereby lose the ability to say "stop". Raised
            # rather than returned so it unwinds through the same `finally` that
            # closes the socket and reports the link down.
            raise _NotEntitled(event.message or "broker reported this account is not entitled")

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


class _NotEntitled(Exception):
    """Raised inside a stream loop when the broker refuses this account the feed.

    Distinct from :class:`_AuthExpired` and deliberately not a subclass of it:
    `except _AuthExpired` is what tears down the whole session, and an
    entitlement refusal must never take that path. See ADR-045.
    """


def _terminal_refusal(classification) -> Optional[Exception]:
    """Turn a channel's `connect_error` answer into the exception to raise, if any.

    Accepts the three answers `BrokerStreamChannel.connect_error` may give and
    normalises them here, once, so the run loop has one shape to react to and
    a channel's return type is not a thing the transport has to know about:

    * `None`/empty        → `None`; ordinary backoff, unchanged since D4.6.
    * a reason string     → auth expiry, unchanged since D4.6.
    * a terminal event    → whatever that event classifies it as (D5.5).

    A non-terminal event (TICKS, ORDER, ERROR, IGNORE) is refused rather than
    guessed at: a handshake that produced ticks is a codec defect, and silently
    reading it as "keep retrying" would hide it. The failure falls through to the
    ordinary backoff and is logged, which is what an unclassified handshake
    failure has always done.
    """
    if classification is None:
        return None
    if isinstance(classification, BrokerStreamEvent):
        if classification.kind is StreamEventKind.NOT_ENTITLED:
            return _NotEntitled(classification.message or "broker refused this account the feed")
        if classification.kind is StreamEventKind.AUTH_EXPIRED:
            return _AuthExpired(classification.message or "broker reported an expired session")
        logger.warning(
            "A stream channel classified a failed handshake as %s, which is not a "
            "terminal condition — falling back to the reconnect schedule",
            classification.kind.value,
        )
        return None
    text = str(classification).strip()
    return _AuthExpired(text) if text else None


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
        on_not_entitled=None,
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
            on_not_entitled=on_not_entitled,
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

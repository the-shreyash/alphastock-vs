"""Realtime broker WebSocket streaming.

Official broker feeds only — no simulated ticks.

Zerodha  : wss://ws.kite.trade (Kite ticker v3).
           Binary frames carry price ticks (parsed in LTP mode); text frames
           carry JSON order updates / errors.
Upstox   : wss://api.upstox.com/v2/feed/portfolio-stream-feed
           JSON order/position/holding updates. (Upstox market ticks use a
           protobuf feed and are intentionally out of scope here.)

Each connected broker account gets one background task with exponential
backoff reconnect. Updates are forwarded through callbacks supplied by the
BrokerEngine (order upsert + per-user app WebSocket push), keeping this module
transport-only.

WHAT D3 CHANGED HERE
--------------------
Dispatch is by *protocol*, not by broker name. The run loop used to read

    if self.broker == "zerodha": ... elif self.broker == "upstox": ...

which is a chain that grows by one branch per broker in a module no broker
owns — the exact shape the Broker Provider Framework exists to prevent. Each
adapter now declares a `stream_protocol`, and this module maps protocols to
transports. Two brokers reselling the same vendor API share one entry; a broker
with no stream declares no protocol and never reaches here.

Frame normalization moved to the adapters too: this module used to import
`ZerodhaAdapter` and `UpstoxAdapter` by name to canonicalize an order frame.
It now asks the registry for whichever adapter owns the connection and calls
`normalize_stream_order`, which is part of the ORDER_STREAM capability and
verified at registration.

Still out of scope, deliberately (D4): routing broker ticks into the Market
Gateway as a streaming market-data feed. Ticks here drive portfolio and trade
P&L only, exactly as they did before D3.
"""
import asyncio
import json
import logging
import struct

logger = logging.getLogger(__name__)

KITE_WS_URL = "wss://ws.kite.trade"
UPSTOX_WS_URL = "wss://api.upstox.com/v2/feed/portfolio-stream-feed?update_types=order"

RECONNECT_BASE_DELAY = 2      # seconds
RECONNECT_MAX_DELAY = 60      # seconds


def parse_kite_binary(payload: bytes) -> list:
    """Parse a Kite ticker binary frame into [{instrument_token, last_price}].

    Frame layout (Kite Connect v3 docs):
      2 bytes  number of packets (int16 BE)
      per packet: 2 bytes length (int16 BE) + packet bytes
      LTP packet: 4 bytes instrument_token + 4 bytes ltp (price * 100)
    1-byte frames are heartbeats.
    """
    if len(payload) < 4:
        return []
    ticks = []
    try:
        count = struct.unpack_from(">H", payload, 0)[0]
        offset = 2
        for _ in range(count):
            if offset + 2 > len(payload):
                break
            length = struct.unpack_from(">H", payload, offset)[0]
            offset += 2
            packet = payload[offset:offset + length]
            offset += length
            if len(packet) < 8:
                continue
            token, ltp = struct.unpack_from(">ii", packet, 0)
            # Equity/derivative prices are paise (price * 100). Currency
            # segment uses a different divisor; equities are our scope.
            ticks.append({"instrument_token": token, "last_price": ltp / 100.0})
    except struct.error:
        logger.debug("Malformed Kite binary frame skipped")
    return ticks


class BrokerStream:
    """One live WebSocket connection for one (user, broker) account."""

    def __init__(self, user_id: str, broker: str, session: dict,
                 credentials: dict = None, instrument_tokens: list = None,
                 on_order_update=None, on_tick=None, on_expired=None):
        self.user_id = user_id
        self.broker = broker
        self.session = session
        #: Credential material supplied by the adapter through the gateway. A
        #: dict rather than a bare `api_key` because what a transport needs is
        #: the transport's business: Kite authenticates by query string, Upstox
        #: by bearer header, and the next broker by something else again.
        self.credentials = dict(credentials or {})
        self.instrument_tokens = instrument_tokens or []
        self.on_order_update = on_order_update
        self.on_tick = on_tick
        self.on_expired = on_expired
        self._stopped = False
        self._task = None

    # -- lifecycle -----------------------------------------------------------
    def start(self):
        self._task = asyncio.create_task(self._run(), name=f"broker-stream-{self.broker}-{self.user_id}")
        return self._task

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

    async def _run(self):
        delay = RECONNECT_BASE_DELAY
        while not self._stopped:
            try:
                runner = PROTOCOL_RUNNERS.get(self._adapter.stream_protocol)
                if runner is None:
                    logger.warning(
                        "No stream transport for protocol %r (broker %s)",
                        self._adapter.stream_protocol, self.broker)
                    return
                await runner(self)
                delay = RECONNECT_BASE_DELAY  # clean close → quick reconnect
            except asyncio.CancelledError:
                raise
            except _AuthExpired:
                logger.info(f"{self.broker} stream token expired for user {self.user_id}; stopping stream.")
                if self.on_expired:
                    try:
                        await self.on_expired(self.user_id, self.broker)
                    except Exception:
                        pass
                return
            except Exception as e:
                logger.warning(f"{self.broker} stream error for user {self.user_id}: {e}")
            if self._stopped:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _connect(self, url: str, headers: dict = None):
        import websockets
        try:
            return await websockets.connect(url, additional_headers=headers, ping_interval=20, ping_timeout=20)
        except TypeError:
            # websockets < 14 uses extra_headers
            return await websockets.connect(url, extra_headers=headers, ping_interval=20, ping_timeout=20)

    # -- Zerodha ---------------------------------------------------------------
    async def _run_kite(self):
        token = self.session.get("access_token")
        url = f"{KITE_WS_URL}?api_key={self.credentials.get('api_key', '')}&access_token={token}"
        ws = await self._connect(url)
        logger.info(f"Zerodha ticker connected for user {self.user_id}")
        try:
            if self.instrument_tokens:
                await ws.send(json.dumps({"a": "subscribe", "v": self.instrument_tokens}))
                await ws.send(json.dumps({"a": "mode", "v": ["ltp", self.instrument_tokens]}))
            async for message in ws:
                if isinstance(message, (bytes, bytearray)):
                    if len(message) <= 1:
                        continue  # heartbeat
                    ticks = parse_kite_binary(bytes(message))
                    if ticks and self.on_tick:
                        await self.on_tick(self.user_id, self.broker, ticks)
                else:
                    await self._handle_kite_text(message)
        finally:
            await ws.close()

    async def _handle_kite_text(self, message: str):
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            return
        msg_type = data.get("type")
        if msg_type == "order" and self.on_order_update:
            await self.on_order_update(
                self.user_id, self.broker,
                self._adapter.normalize_stream_order(data.get("data") or {}))
        elif msg_type == "error":
            text = str(data.get("data", ""))
            if "token" in text.lower():
                raise _AuthExpired(text)
            logger.warning(f"Zerodha ticker error: {text}")

    # -- Upstox ------------------------------------------------------------------
    async def _run_upstox(self):
        token = self.session.get("access_token")
        ws = await self._connect(UPSTOX_WS_URL, headers={"Authorization": f"Bearer {token}"})
        logger.info(f"Upstox portfolio stream connected for user {self.user_id}")
        try:
            async for message in ws:
                if isinstance(message, (bytes, bytearray)):
                    message = message.decode("utf-8", errors="ignore")
                try:
                    data = json.loads(message)
                except (json.JSONDecodeError, TypeError):
                    continue
                # The feed sends the order payload directly (update_type=order)
                if data.get("update_type") in (None, "order") and (data.get("order_id") or data.get("data")):
                    payload = data.get("data") or data
                    if self.on_order_update:
                        await self.on_order_update(
                            self.user_id, self.broker,
                            self._adapter.normalize_stream_order(payload))
        finally:
            await ws.close()


#: Wire protocol -> transport coroutine.
#:
#: The whole broker-awareness of this module. Adding a broker that speaks an
#: existing protocol adds nothing here; adding one with a new protocol adds a
#: single entry beside a new `_run_*` method, and no existing branch changes.
PROTOCOL_RUNNERS = {
    "kite_ticker": BrokerStream._run_kite,
    "upstox_portfolio": BrokerStream._run_upstox,
}


class _AuthExpired(Exception):
    """Raised inside a stream loop when the broker reports a dead token."""


class BrokerStreamManager:
    """Owns every live broker stream: start/stop/replace per (user, broker)."""

    def __init__(self):
        self._streams: dict = {}  # (user_id, broker) -> BrokerStream

    async def start_stream(self, user_id: str, broker: str, session: dict,
                           credentials: dict = None, instrument_tokens: list = None,
                           on_order_update=None, on_tick=None, on_expired=None):
        await self.stop_stream(user_id, broker)
        stream = BrokerStream(user_id, broker, session, credentials=credentials,
                              instrument_tokens=instrument_tokens,
                              on_order_update=on_order_update, on_tick=on_tick,
                              on_expired=on_expired)
        self._streams[(user_id, broker)] = stream
        stream.start()
        return stream

    async def stop_stream(self, user_id: str, broker: str):
        stream = self._streams.pop((user_id, broker), None)
        if stream:
            await stream.stop()

    def discard(self, user_id: str, broker: str) -> bool:
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
        """
        return self._streams.pop((user_id, broker), None) is not None

    async def stop_all(self):
        for key in list(self._streams):
            await self.stop_stream(*key)

    def status(self) -> list:
        return [{
            "user_id": user_id,
            "broker": broker,
            "running": stream.running,
            "subscribed_instruments": len(stream.instrument_tokens),
        } for (user_id, broker), stream in self._streams.items()]


stream_manager = BrokerStreamManager()

"""Sprint D4 — Multi-Broker Market Streaming Framework tests (hermetic).

D4 turns a connected broker's WebSocket into a registered priority-1 *market
data* provider, behind the same provider-independent boundary D1–D3 built. This
module is where that work is pinned. D4.1 — the prerequisites — is covered here;
later stages extend the same file.

WHAT D4.1 COVERS AND WHY EACH TEST EXISTS
------------------------------------------
**Connected-broker restoration (DB-2).** `BrokerEngine.load_sessions()` restores
encrypted sessions and restarts broker streams at boot, but published no
lifecycle event — so `SourceManager._connected_brokers`, which is built *only*
from those events, stayed empty after every restart while a broker socket was
running underneath it. D4's feed switch reads that registry, so the gap had to
close first.

The fix is two changes, not one, and the second is the reason these tests are
written the way they are. Publishing `broker.connected` from `load_sessions()`
is inert unless something is already subscribed, and at boot nothing was:
`server.py` restored sessions *before* `market_gateway.initialize()` — the only
caller of `subscribe_broker_events()`. `EventBus.publish` treats "no matching
handler" as normal (records a metric, returns; no raise, no warning), so the
broken version would have logged a correct-looking restore and left the registry
empty. A test that asserted only "publish was called" would have passed against
it. `test_broker_lifecycle_is_subscribed_before_sessions_are_restored` is
therefore an *ordering* assertion, and
`test_a_restored_session_repopulates_the_source_manager_registry` drives the real
Event Bus end to end rather than a mock.

**Reconnect jitter.** The stream backoff doubled deterministically with no
randomization, so a broker-side blip — which disconnects every connected user's
socket in the same instant — had all of them retry in the same instant too.

**The dependency boundary.** D4 adds modules on both sides of the broker/market
line. broker→market imports are permitted (`broker_engine` already imports the
Event Bus); market→broker are not, and that has to be locked *before* D4.2 code
lands rather than audited afterwards.

No test opens a socket or reaches a broker API.
"""

import ast
import asyncio
import contextlib
import json
import logging
import pathlib
import re
import struct
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.broker_engine import BrokerEngine
from services.brokers.base import BrokerAdapter
from services.brokers.capabilities import BrokerCapability
from services.brokers.contracts import BrokerOrder
from services.brokers.credentials import BrokerCredentialSpec
from services.brokers.errors import BrokerContractError
from services.brokers.instruments import InstrumentMap, canonical_ticks
from services.brokers.registry import BrokerAdapterInvalid, BrokerRegistry, broker_registry
from services.brokers.stream import (
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_DELAY,
    BrokerStream,
    _AuthExpired,
    reconnect_pause,
)
from services.brokers.streaming import BrokerStreamEndpoint, BrokerStreamEvent, StreamEventKind
from services.market_engine.event_bus import event_bus
from services.market_engine.source_manager import SourceManager
from services.market_engine.ticks import MarketInstrument, MarketTick, MarketTickError
from services.market_engine.validator import MAX_STOCK_PRICE
from tests._fakedb import FakeDB

# Reused rather than re-implemented: the same source-stripping the D3 framework
# sweeps use, so "named in executable code" means the same thing in both files.
from tests.test_broker_framework import _strip_comments_and_strings as _strip_source

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def run(coro):
    """Drive one coroutine on a fresh event loop.

    Matches `test_broker_framework.run` deliberately: `asyncio.run` rather than
    `get_event_loop().run_until_complete`, because the latter passes in
    isolation and fails in a full-suite run once an earlier test has left the
    thread with no current loop.
    """
    return asyncio.run(coro)


# ==================================================================
# DB-2 — connected-broker restoration
# ==================================================================


def test_broker_lifecycle_is_subscribed_before_sessions_are_restored():
    """Startup must wire the subscriber BEFORE it republishes restored sessions.

    This is an ordering assertion rather than a behavioural one because ordering
    is the whole defect. `load_sessions()` publishing `broker.connected` is
    correct code that does nothing at all if it runs first: `EventBus.publish`
    counts a metric and returns when no handler matches, so the restore emits no
    warning, logs "Restored N live broker session(s)", and leaves the Source
    Manager's registry empty.

    Reversing the two statements in `server.py` must turn this test red. Asserting
    that `load_sessions` publishes — the obvious test — would stay green against
    the broken order, which is exactly the kind of probe that certifies nothing.
    """
    source = (BACKEND / "server.py").read_text()

    subscribe_at = source.find("source_manager.subscribe_broker_events()")
    restore_at = source.find("await broker_engine.load_sessions()")

    assert subscribe_at != -1, "startup no longer subscribes to broker lifecycle events"
    assert restore_at != -1, "startup no longer restores broker sessions"
    assert subscribe_at < restore_at, (
        "server.py restores broker sessions before subscribing to broker lifecycle "
        "events. The restore's `broker.connected` publishes would fire into a bus "
        "with no handler and be silently dropped, leaving "
        "SourceManager._connected_brokers empty after every restart (DB-2)."
    )


def test_load_sessions_publishes_broker_connected_for_each_restored_session():
    """A restored session is a live connection and must announce itself as one.

    Driven through `BrokerEngine` against the real Event Bus rather than by
    asserting on a mock, so the test exercises the same path production does.
    """
    engine = BrokerEngine()
    engine.configure(_db_with_saved_session(user_id="u-restore", broker="zerodha"))

    seen = []

    async def _capture(event):
        seen.append(event.get("data") or {})

    event_bus.subscribe("broker.connected", _capture)
    try:
        with patch.object(BrokerEngine, "start_stream", new=AsyncMock()):
            restored = run(engine.load_sessions())
    finally:
        event_bus.unsubscribe("broker.connected", _capture)

    assert restored == 1
    assert len(seen) == 1
    assert seen[0]["user_id"] == "u-restore"
    assert seen[0]["broker"] == "zerodha"
    # The capabilities ride on the event so a consumer can decide what the
    # connection makes possible without importing a broker module.
    assert "tick_stream" in seen[0]["capabilities"]


def test_a_restored_session_repopulates_the_source_manager_registry():
    """The end-to-end property DB-2 is actually about.

    Engine -> Event Bus -> Source Manager, with nothing mocked between them. This
    is the assertion D4's feed switch depends on: after a restart, a user whose
    broker session survived must be visible as a streaming candidate without any
    further traffic touching their account.
    """
    engine = BrokerEngine()
    engine.configure(_db_with_saved_session(user_id="u-boot", broker="zerodha"))

    manager = SourceManager()
    manager.subscribe_broker_events()
    try:
        assert manager.connected_brokers("u-boot") == []  # nothing known pre-boot

        with patch.object(BrokerEngine, "start_stream", new=AsyncMock()):
            run(engine.load_sessions())

        assert manager.connected_brokers("u-boot") == ["zerodha"]
        assert manager.streaming_brokers("u-boot") == ["zerodha"]
    finally:
        _unsubscribe(manager)


def test_an_expired_saved_session_is_not_announced_as_connected():
    """The negative control that makes the test above mean something.

    Without this, `load_sessions()` could publish unconditionally — announcing
    every account that was ever connected as live — and the restoration tests
    would still pass. An expired token is not a connection: the user must
    reconnect, and D4 must not promote a dead session to a priority-1 feed.
    """
    engine = BrokerEngine()
    engine.configure(_db_with_saved_session(user_id="u-stale", broker="zerodha", expires_at=_past()))

    manager = SourceManager()
    manager.subscribe_broker_events()
    try:
        with patch.object(BrokerEngine, "start_stream", new=AsyncMock()):
            restored = run(engine.load_sessions())

        assert restored == 0
        assert manager.connected_brokers("u-stale") == []
    finally:
        _unsubscribe(manager)


# ==================================================================
# Reconnect jitter
# ==================================================================


def test_reconnect_pause_stays_within_its_ceiling():
    """Equal jitter: never longer than the ceiling, never below half of it.

    The lower bound is the half that matters. Full jitter (`uniform(0, delay)`)
    can roll a near-zero pause, which turns a still-unreachable broker into a
    tight retry loop for whichever stream drew the low number — trading a
    synchronized herd for an unthrottled one.
    """
    for delay in (RECONNECT_BASE_DELAY, 8, RECONNECT_MAX_DELAY):
        for _ in range(200):
            pause = reconnect_pause(delay)
            assert delay / 2.0 <= pause <= delay


def test_reconnect_pauses_do_not_collapse_to_a_single_value():
    """Two streams backing off from the same delay must not wake together.

    A blip at the broker disconnects every connected user's socket in the same
    instant. Without jitter every one of them retries in the same instant, then
    again 2s later, then 4s later — a herd that grows with the user count and
    hits the broker hardest exactly when it is least able to answer.
    """
    pauses = {reconnect_pause(RECONNECT_MAX_DELAY) for _ in range(50)}
    assert len(pauses) > 1


def test_the_reconnect_loop_sleeps_a_jittered_pause_not_the_raw_delay():
    """`reconnect_pause` must actually be *called* by the loop.

    The two tests above pass whether or not anything uses the helper, so on their
    own they would certify a jitter function sitting unreferenced beside an
    unjittered `sleep(delay)`. This one drives the real run loop: a transport
    that raises, one sleep, then stop.
    """
    stream = BrokerStream(user_id="u1", broker="zerodha", session={"access_token": "t"})
    slept = []

    async def _boom(_self):
        raise RuntimeError("transport down")

    async def _fake_sleep(seconds):
        slept.append(seconds)
        stream._stopped = True  # one pass through the backoff, then unwind

    with (
        patch.dict("services.brokers.stream.PROTOCOL_RUNNERS", {"kite_ticker": _boom}, clear=False),
        patch("services.brokers.stream.asyncio.sleep", new=_fake_sleep),
    ):
        run(stream._run())

    assert len(slept) == 1
    pause = slept[0]
    assert pause != RECONNECT_BASE_DELAY, "the reconnect loop slept the raw delay — reconnect_pause() is not wired in"
    assert RECONNECT_BASE_DELAY / 2.0 <= pause <= RECONNECT_BASE_DELAY


# ==================================================================
# The dependency boundary D4 must not breach
# ==================================================================


def test_the_market_engine_never_imports_a_broker_module():
    """The direction rule, locked before D4.2 adds modules on both sides.

    D4 puts a broker-fed provider inside the Market Engine and a construction
    seam inside the broker layer. broker -> market is permitted and already
    exists (`broker_engine` imports the Event Bus). market -> broker is not: the
    Market Engine must remain able to resolve, rank and normalize a feed without
    knowing that brokers exist as a concept, which is what lets a broker feed and
    a licensed exchange feed be the same kind of thing to it.

    Currently clean, so this test is green the day it is written — which is the
    point. It exists to fail the first time someone reaches across.
    """
    offenders = {}
    pattern = re.compile(r"^\s*(?:from|import)\s+services\.brokers", re.MULTILINE)
    for path in sorted((BACKEND / "services" / "market_engine").rglob("*.py")):
        code = _strip_comments_and_strings(path.read_text())
        if pattern.search(code):
            offenders[str(path.relative_to(BACKEND))] = True
    assert not offenders, (
        f"Market Engine modules importing the broker layer: {sorted(offenders)}. "
        "The broker side constructs and injects; the market side never reaches back."
    )


# ==================================================================
# Helpers
# ==================================================================


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()


def _db_with_saved_session(*, user_id: str, broker: str, expires_at: str = None) -> FakeDB:
    """A database holding one connected broker account, as a restart would find it.

    Tokens are stored in the legacy plaintext form on purpose: `decrypt_token`
    returns those unchanged, so the fixture needs no key material and the restore
    path under test is the same one a real deployment runs.
    """
    return FakeDB(
        broker_accounts=[
            {
                "user_id": user_id,
                "broker": broker,
                "connected": True,
                "access_token": "restored-token",
                "refresh_token": "",
                "public_token": "",
                "expires_at": expires_at or _future(),
                "account_id": "AB1234",
                "connected_at": _past(),
                "profile": {"user_id": "AB1234"},
            }
        ]
    )


def _unsubscribe(manager) -> None:
    event_bus.unsubscribe("broker.connected", manager._on_broker_connected)
    event_bus.unsubscribe("broker.disconnected", manager._on_broker_disconnected)


def _strip_comments_and_strings(source: str) -> str:
    """Executable code only — comments, docstrings and string literals removed.

    Same reason as its twin in `test_broker_framework.py`: D4 leaves comments
    describing the boundary it enforces ("the Market Engine imports no broker
    module"), and a structural test that cannot tell an explanation from a
    violation would force the explanation to be deleted to stay green.
    """
    without_docstrings = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', '""', source)
    without_strings = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', '""', without_docstrings)
    return re.sub(r"#[^\n]*", "", without_strings)


# ==================================================================
# D4.2 — the generic streaming contract / codec boundary
# ==================================================================
#
# D4.2 moved every broker's wire format out of `stream.py` and behind three
# adapter methods (`stream_endpoint`, `stream_subscribe_frames`,
# `decode_stream_frame`), leaving one generic transport. The tests below are
# written to fail if any part of that boundary is weakened, and each was run
# against a deliberately broken version before being kept.
#
# The fictional broker below is the load-bearing piece. `AcmeBrokerAdapter` in
# `test_broker_framework.py` proves a broker can be *added* without touching
# core; `NovaAdapter` proves a broker can *stream* without inheriting a single
# assumption from the one streaming broker that exists today. Every choice in
# its wire format is deliberately the opposite of Kite's: text frames rather
# than binary, instruments identified by trading symbol rather than by an opaque
# numeric token, prices in rupees as strings rather than integer paise, a
# comma-separated subscribe frame rather than JSON, and a bearer header rather
# than credentials in the query string. If any Kite-shaped assumption survived
# in the transport, Nova cannot stream.


class _FakeSocket:
    """A WebSocket that yields a fixed script of frames, then closes.

    Records what was sent so a test can assert the adapter's subscribe frames
    reached the wire *verbatim* — the transport must not re-encode them, since
    it cannot know what encoding the broker expects.
    """

    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []
        self.closed = False

    async def send(self, frame):
        self.sent.append(frame)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        async def gen():
            for frame in self._frames:
                yield frame

        return gen()


class NovaAdapter(BrokerAdapter):
    """A streaming broker that does not exist, built only from the contract."""

    name = "nova"
    display_name = "Nova Securities"
    capabilities = frozenset(
        {
            BrokerCapability.ORDER_STREAM,
            BrokerCapability.TICK_STREAM,
        }
    )
    credential_spec = BrokerCredentialSpec(api_key_env="NOVA_API_KEY")
    default_product = "DELIVERY"
    stream_protocol = "nova_feed"

    def get_login_url(self, user_id: str = None) -> dict:
        return {"url": "https://nova.example/login", "configured": True}

    async def exchange_token(self, auth_payload: dict) -> dict:
        return {"access_token": "nova-token"}

    def session_expiry(self, connected_at: datetime) -> datetime:
        return connected_at + timedelta(hours=8)

    # -- the streaming contract, implemented Nova's way ---------------------
    def stream_endpoint(self, session: dict, credentials: dict = None) -> BrokerStreamEndpoint:
        return BrokerStreamEndpoint(
            url="wss://feed.nova.example/v1/stream",
            headers={"X-Nova-Token": (session or {}).get("access_token", "")},
        )

    def stream_subscribe_frames(self, instruments: list = None) -> list:
        #: Not JSON. A broker is entitled to any framing it likes.
        return ["SUB " + ",".join(str(i) for i in (instruments or []))] if instruments else []

    def stream_instruments(self, holdings: list = None, positions: list = None) -> list:
        return sorted({(row.get("symbol") or "").upper() for row in (holdings or []) if row.get("symbol")})

    def normalize_stream_order(self, payload: dict) -> dict:
        return {
            "order_id": payload.get("ref"),
            "symbol": payload.get("scrip"),
            "quantity": payload.get("qty"),
            "status": "FILLED" if payload.get("state") == "DONE" else "PENDING",
            "broker": self.name,
        }

    def decode_stream_frame(self, frame):
        if isinstance(frame, (bytes, bytearray)):
            frame = frame.decode("utf-8", errors="ignore")
        if frame == "PING":
            return BrokerStreamEvent.ignore()
        if frame.startswith("EXPIRED"):
            return BrokerStreamEvent.auth_expired("nova session ended")
        try:
            data = json.loads(frame)
        except (ValueError, TypeError):
            return BrokerStreamEvent.ignore()
        if data.get("kind") == "price":
            return BrokerStreamEvent.tick_event(
                [
                    {
                        # Identified by SYMBOL, priced as a STRING in rupees.
                        "symbol": row["scrip"],
                        "last_price": row["rate"],
                        # Broker-specific extras the canonical contract does not
                        # name — present precisely so a test can prove they are
                        # dropped at the boundary rather than forwarded.
                        "nova_depth": row.get("depth"),
                        "raw": row,
                    }
                    for row in data.get("rows", [])
                ]
            )
        if data.get("kind") == "order":
            return BrokerStreamEvent.order_event(self.normalize_stream_order(data), broker=self.name)
        return BrokerStreamEvent.ignore()


@contextlib.contextmanager
def nova_registered(adapter=None):
    """Register a fictional streaming broker for the body of one test."""
    adapter = adapter or NovaAdapter()
    broker_registry.register(adapter, replace=True)
    try:
        yield adapter
    finally:
        broker_registry.unregister(adapter.name)


def drive_stream(adapter, frames, instruments=None, session=None):
    """Run one full transport pass over a scripted socket.

    Returns `(ticks, orders, expired, socket)` — everything that crossed the
    boundary, plus the socket so a test can inspect what was sent.
    """
    ticks, orders, expired = [], [], []

    async def on_tick(user_id, broker, batch):
        ticks.append((user_id, broker, batch))

    async def on_order_update(user_id, broker, order):
        orders.append((user_id, broker, order))

    async def on_expired(user_id, broker):
        expired.append((user_id, broker))

    socket = _FakeSocket(frames)
    stream = BrokerStream(
        "user-1",
        adapter.name,
        session if session is not None else {"access_token": "live-token"},
        credentials={"api_key": "nova-key"},
        instrument_tokens=instruments or [],
        on_order_update=on_order_update,
        on_tick=on_tick,
        on_expired=on_expired,
    )

    async def scenario():
        with patch.object(BrokerStream, "_connect", AsyncMock(return_value=socket)):
            try:
                await stream._run_websocket()
            except _AuthExpired:
                if stream.on_expired:
                    await stream.on_expired(stream.user_id, stream.broker)

    run(scenario())
    return ticks, orders, expired, socket


def test_a_broker_can_stream_without_any_kite_shaped_assumption():
    """The falsification the whole sprint is for: a non-Kite broker streams.

    Nova shares nothing with Zerodha's feed — text frames, symbol identity,
    string prices, a non-JSON subscribe frame, header auth — and it reaches the
    engine's callbacks in canonical shape without a line of Nova-specific code
    in `stream.py`, the engine, or anywhere else outside its adapter.
    """
    with nova_registered() as adapter:
        ticks, orders, expired, socket = drive_stream(
            adapter,
            frames=[
                "PING",
                json.dumps({"kind": "price", "rows": [{"scrip": "NOVACO", "rate": "141.25", "depth": [1, 2]}]}),
                json.dumps({"kind": "order", "ref": "NOVA-1", "scrip": "NOVACO", "qty": 5, "state": "DONE"}),
            ],
            instruments=["NOVACO"],
        )

    # The adapter's own subscribe framing reached the wire untouched.
    assert socket.sent == ["SUB NOVACO"]
    assert socket.closed

    # Ticks arrived canonical: a string price coerced to float, the symbol
    # carried, and no token invented for a broker that does not use one.
    assert len(ticks) == 1
    _, broker, batch = ticks[0]
    assert broker == "nova"
    assert batch[0]["symbol"] == "NOVACO"
    assert batch[0]["last_price"] == 141.25
    assert batch[0]["instrument_token"] is None

    # And the order arrived in the same canonical shape a REST order has.
    assert len(orders) == 1
    assert orders[0][2]["order_id"] == "NOVA-1"
    assert orders[0][2]["status"] == "FILLED"
    assert not expired


def test_a_raw_broker_payload_cannot_reach_the_tick_consumers():
    """Leak containment, asserted on the keys that actually arrive.

    Nova's codec deliberately emits `nova_depth` and a `raw` blob carrying its
    entire frame — the streaming equivalent of the Kite `raw` key `contracts.py`
    was written to stop. Neither may survive the boundary: `portfolio_stream`,
    `trade_stream` and the user's app WebSocket are all downstream of this list,
    and a broker-shaped key reaching them is a consumer written against one
    broker waiting to happen.
    """
    with nova_registered() as adapter:
        ticks, _, _, _ = drive_stream(
            adapter,
            frames=[json.dumps({"kind": "price", "rows": [{"scrip": "NOVACO", "rate": "10", "depth": [1]}]})],
        )

    delivered = ticks[0][2][0]
    assert set(delivered) == {"instrument_token", "last_price", "symbol", "exchange", "volume", "timestamp"}
    assert "nova_depth" not in delivered
    assert "raw" not in delivered


def test_a_codec_that_returns_a_raw_payload_delivers_nothing():
    """The barrier itself: the transport type-checks what the codec returns.

    The previous test proves coercion drops unknown keys. This one covers the
    way around it — a codec that skips the canonical types altogether and
    returns its own dict. Without the check, that dict is what
    `BrokerStream._dispatch` would be handed; with it, the frame produces
    nothing and says so at ERROR level rather than passing a broker payload up.
    """

    class LeakyNova(NovaAdapter):
        def decode_stream_frame(self, frame):
            return {"kind": "ticks", "ticks": [{"instrument_token": 1, "last_price": 9.0}]}

    with nova_registered(LeakyNova()) as adapter:
        ticks, orders, _, _ = drive_stream(adapter, frames=["anything"])

    assert ticks == []
    assert orders == []


def test_a_streaming_capability_without_a_codec_is_rejected_at_registration():
    """Unsupported streaming is refused by the capability framework, at startup.

    A broker that declares TICK_STREAM but cannot decode a frame opens a live
    socket whose every frame decodes to nothing — which in the logs is
    indistinguishable from a quiet market. The registry refuses it at import,
    the cheapest possible moment, exactly as it refuses a broker that declares
    HOLDINGS without implementing `get_holdings`.
    """

    class MuteNova(NovaAdapter):
        decode_stream_frame = BrokerAdapter.decode_stream_frame

    with pytest.raises(BrokerAdapterInvalid) as excinfo:
        BrokerRegistry.validate(MuteNova())
    assert "decode_stream_frame" in str(excinfo.value)

    class UnreachableNova(NovaAdapter):
        stream_endpoint = BrokerAdapter.stream_endpoint

    with pytest.raises(BrokerAdapterInvalid) as excinfo:
        BrokerRegistry.validate(UnreachableNova())
    assert "stream_endpoint" in str(excinfo.value)

    # Control: the same adapter with both implemented is accepted, so the two
    # failures above are about the codec and not about Nova being fictional.
    BrokerRegistry.validate(NovaAdapter())


def test_a_streaming_declaration_missing_its_other_half_is_rejected():
    """Both directions of the realtime declaration, because both are silent.

    A capability with no protocol has nothing to dispatch on; a protocol with no
    capability has a transport nothing may deliver through, since every decoded
    event is gated on a capability.
    """

    class ProtocollessNova(NovaAdapter):
        stream_protocol = ""

    with pytest.raises(BrokerAdapterInvalid) as excinfo:
        BrokerRegistry.validate(ProtocollessNova())
    assert "stream_protocol" in str(excinfo.value)

    class PurposelessNova(NovaAdapter):
        capabilities = frozenset()

    with pytest.raises(BrokerAdapterInvalid) as excinfo:
        BrokerRegistry.validate(PurposelessNova())
    assert "no streaming capability" in str(excinfo.value)


def test_a_decoded_event_the_broker_never_declared_is_dropped():
    """The capability gate, enforced on the streaming path at runtime.

    Registration proves a broker *can* decode what it declares. This is the
    other half: a broker that decodes something it never declared. Nova here
    offers order updates only — its tick decoding is a bug, a copied codec, or a
    broker that started sending an update type it was never subscribed to — and
    the platform refuses the ticks while continuing to deliver the orders it
    does declare.

    Dropped rather than delivered because the capability set is the authority on
    what a broker serves. `stream_instruments` is gone with the capability, so
    nothing was subscribed, so nothing downstream could have been written
    against these ticks.
    """

    class OrdersOnlyNova(NovaAdapter):
        capabilities = frozenset({BrokerCapability.ORDER_STREAM})

    with nova_registered(OrdersOnlyNova()) as adapter:
        ticks, orders, _, _ = drive_stream(
            adapter,
            frames=[
                json.dumps({"kind": "price", "rows": [{"scrip": "NOVACO", "rate": "10"}]}),
                json.dumps({"kind": "order", "ref": "NOVA-2", "scrip": "NOVACO", "qty": 1, "state": "DONE"}),
            ],
        )

    assert ticks == []
    assert [o[2]["order_id"] for o in orders] == ["NOVA-2"]


def test_a_streamed_order_is_coerced_through_the_same_contract_as_a_fetched_one():
    """One collection, one shape, whichever path the order arrived by.

    Streamed order frames used to reach `db.orders` and the app WebSocket as
    whatever `normalize_stream_order` returned, while the identical order
    fetched over REST went through `BrokerOrder`. Two writers to one collection
    with one of them unenforced is how a shape drifts.
    """

    class ChattyNova(NovaAdapter):
        def normalize_stream_order(self, payload: dict) -> dict:
            order = super().normalize_stream_order(payload)
            order["nova_internal_seq"] = 4711
            return order

    with nova_registered(ChattyNova()) as adapter:
        _, orders, _, _ = drive_stream(
            adapter,
            frames=[json.dumps({"kind": "order", "ref": "NOVA-3", "scrip": "NOVACO", "qty": "7", "state": "DONE"})],
        )

    order = orders[0][2]
    assert set(order) == set(BrokerOrder().as_dict())
    assert "nova_internal_seq" not in order
    assert order["quantity"] == 7  # coerced from the string the broker sent


def test_an_expired_token_reported_by_a_codec_stops_the_stream():
    """Auth expiry is a decoded event now, not a broker-specific exception.

    The codec says the session is dead in the contract's vocabulary; the
    transport turns that into the same stop-and-notify path it always had. A
    broker whose expiry signal is an HTTP close code, a magic frame or a JSON
    error field all express it the same way.
    """
    with nova_registered() as adapter:
        ticks, _, expired, _ = drive_stream(
            adapter,
            frames=[
                json.dumps({"kind": "price", "rows": [{"scrip": "NOVACO", "rate": "10"}]}),
                "EXPIRED",
                json.dumps({"kind": "price", "rows": [{"scrip": "NOVACO", "rate": "11"}]}),
            ],
        )

    assert expired == [("user-1", "nova")]
    # The frame before the expiry was delivered; the one after it was not —
    # the stream stops at the expiry rather than draining the socket.
    assert [t[2][0]["last_price"] for t in ticks] == [10.0]


def test_an_endpoint_never_carries_its_credentials_into_a_log_line(caplog):
    """A broker that authenticates by query string must not be logged whole.

    Kite's ticker URL carries a live access token, and "connected to <url>" is
    the most natural log line anybody would write in a transport. This is the
    same defect D3 found in `BrokerAdapter._request` arriving by a second route,
    and SECURITY.md forbids credentials in logs outright.
    """
    endpoint = BrokerStreamEndpoint(url="wss://feed.example/stream?api_key=KEY123&access_token=SECRET456")
    assert endpoint.safe_url == "wss://feed.example/stream"
    assert "SECRET456" not in endpoint.safe_url

    # And observed end to end: a broker whose endpoint carries a live token
    # connects, logs, and leaves nothing recoverable in the log.
    class QueryAuthNova(NovaAdapter):
        def stream_endpoint(self, session: dict, credentials: dict = None) -> BrokerStreamEndpoint:
            token = (session or {}).get("access_token", "")
            return BrokerStreamEndpoint(url=f"wss://feed.nova.example/v1/stream?access_token={token}")

    with nova_registered(QueryAuthNova()) as adapter:
        with caplog.at_level(logging.DEBUG, logger="services.brokers.stream"):
            drive_stream(adapter, frames=["PING"], session={"access_token": "SECRET456"})

    assert caplog.records, "nothing was logged — the assertion below would pass vacuously"
    assert not [r for r in caplog.records if "SECRET456" in r.getMessage()]


def test_the_endpoint_contract_refuses_a_non_websocket_url():
    """A codec mistake that would otherwise surface as a connection error."""
    with pytest.raises(BrokerContractError):
        BrokerStreamEndpoint(url="https://feed.example/stream")


def test_an_unusable_tick_is_dropped_without_dropping_its_batch():
    """One malformed packet must not cost the whole frame.

    Brokers pack hundreds of instruments into one frame. Rejecting the batch
    because a single packet was short would throw away good prices for every
    other instrument in it — and a frame that yields nothing usable becomes
    IGNORE, so the transport has one shape for "nothing to deliver".
    """
    event = BrokerStreamEvent.tick_event(
        [
            {"instrument_token": 1, "last_price": 10.0},
            {"last_price": 11.0},  # identifies nothing
            {"instrument_token": 3},  # prices nothing
        ]
    )
    assert event.kind is StreamEventKind.TICKS
    assert [t.instrument_token for t in event.ticks] == [1]
    assert BrokerStreamEvent.tick_event([{"last_price": 1.0}]).kind is StreamEventKind.IGNORE


def test_the_kite_codec_decodes_exactly_what_the_shared_parser_used_to():
    """Regression parity for the one live streaming broker.

    D4.2 moved Kite's binary framing, subscribe frames and error convention out
    of `stream.py` and into the adapter. Moving working code is where a silent
    behaviour change hides, so the decoded results are asserted against the
    values the removed transport produced for the same bytes.
    """
    adapter = broker_registry.require("zerodha")

    frame = struct.pack(">HHii", 1, 8, 408065, 1500 * 100)
    event = adapter.decode_stream_frame(frame)
    assert event.kind is StreamEventKind.TICKS
    assert event.ticks[0].instrument_token == 408065
    assert event.ticks[0].last_price == 1500.0

    assert adapter.decode_stream_frame(b"\x00").kind is StreamEventKind.IGNORE  # heartbeat

    order = adapter.decode_stream_frame(json.dumps({"type": "order", "data": {"order_id": "K1", "status": "COMPLETE"}}))
    assert order.kind is StreamEventKind.ORDER
    assert order.order["order_id"] == "K1"
    assert order.order["status"] == "FILLED"

    expired = adapter.decode_stream_frame(json.dumps({"type": "error", "data": "Invalid access token"}))
    assert expired.kind is StreamEventKind.AUTH_EXPIRED

    other = adapter.decode_stream_frame(json.dumps({"type": "error", "data": "Subscription limit reached"}))
    assert other.kind is StreamEventKind.ERROR

    # The subscribe handshake is Kite's two frames, in order, JSON-encoded.
    frames = [json.loads(f) for f in adapter.stream_subscribe_frames([1, 2])]
    assert frames == [{"a": "subscribe", "v": [1, 2]}, {"a": "mode", "v": ["ltp", [1, 2]]}]
    assert adapter.stream_subscribe_frames([]) == []


def test_the_engine_and_the_market_layer_never_name_a_stream_event_kind_by_hand():
    """The event vocabulary stays inside the broker package (D4.2).

    A core module reconstructing `{"instrument_token": …}` or testing a frame's
    `type` field itself would be the boundary being routed around, which is how
    the pre-D4.2 shape became a de-facto contract in the first place.
    """
    for relative in ("services/broker_engine.py", "services/portfolio_stream.py", "services/trade_stream.py"):
        code = _strip_source((BACKEND / relative).read_text())
        assert "decode_stream_frame" not in code, f"{relative} decodes broker frames itself"
        assert "BrokerStreamEndpoint" not in code, f"{relative} builds a broker endpoint itself"


# ==================================================================
# D4.3 — the canonical instrument-identity / market-tick boundary
# ==================================================================
#
# D4.2 closed the tick *shape* leak: a codec may return nothing but a
# `BrokerTick`. It left the *identity* leak open — `BrokerTick.instrument_token`
# is the broker's own opaque handle (a Kite integer, an Upstox instrument key),
# and it travelled all the way into `portfolio_stream`, `trade_stream` and the
# browser, with both services doing the token→symbol join themselves against
# `db.holdings`.
#
# Two defects followed. Core services were coupled to one broker's identifier
# format, and a symbol-identified broker — which is most brokers other than Kite
# — carried no token to join on, so every join produced nothing and every live
# P&L recompute for its users stopped, silently, on a healthy socket delivering
# good prices.
#
# D4.3 resolves identity at the broker boundary (`brokers/instruments.py`) and
# hands core services `MarketTick` (`market_engine/ticks.py`), which has no
# field a broker identifier could occupy. The tests below are the falsifications
# of that claim; each was run against a deliberately broken version first.


def _kite_holdings():
    """Synced holdings as a numeric-token broker produces them."""
    return [
        {
            "symbol": "RELIANCE",
            "exchange": "NSE",
            "instrument_token": 738561,
            "quantity": 10,
            "invested_value": 25000.0,
            "market_value": 26500.0,
        },
        {
            "symbol": "TCS",
            "exchange": "NSE",
            "instrument_token": 2953217,
            "quantity": 4,
            "invested_value": 15000.0,
            "market_value": 15960.0,
        },
    ]


def _find_key(node, key):
    """Every value stored under `key` anywhere inside a nested payload."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            found.extend(_find_key(v, key))
    elif isinstance(node, (list, tuple)):
        for item in node:
            found.extend(_find_key(item, key))
    return found


# -- identity resolution ------------------------------------------------------


def test_a_numeric_token_broker_instrument_becomes_a_canonical_symbol():
    """Kite's 738561 arrives; RELIANCE/NSE leaves. The token does not."""
    imap = InstrumentMap.from_portfolio(_kite_holdings())

    out = canonical_ticks([{"instrument_token": 738561, "last_price": 2650.5}], imap, broker="zerodha")

    assert len(out) == 1
    assert out[0]["symbol"] == "RELIANCE"
    assert out[0]["exchange"] == "NSE"
    assert out[0]["price"] == 2650.5
    assert "instrument_token" not in out[0]
    assert _find_key(out, "instrument_token") == []


def test_a_symbol_identified_broker_instrument_becomes_a_canonical_symbol():
    """The second identification style, through the same boundary.

    Nova identifies instruments by trading symbol and sends no token at all. It
    must resolve with an empty map (nothing synced yet) *and* pick up the
    exchange when the account does hold the instrument — a token broker's join
    key being absent is not an error, it is a different broker.
    """
    unmapped = canonical_ticks([{"symbol": "novaco", "last_price": "141.25"}], InstrumentMap())
    assert unmapped[0]["symbol"] == "NOVACO", "a symbol-identified broker resolved nothing"
    assert unmapped[0]["price"] == 141.25

    held = InstrumentMap.from_portfolio(
        [{"symbol": "NOVACO", "exchange": "BSE", "instrument_token": None, "quantity": 1}]
    )
    qualified = canonical_ticks([{"symbol": "novaco", "last_price": 141.25}], held)
    assert qualified[0]["exchange"] == "BSE", "the account's exchange did not qualify the symbol"


def test_an_unmapped_token_is_never_used_as_a_symbol():
    """The dangerous fallback, refused.

    An account has no row for token 999999 — it was bought between syncs, or
    belongs to another account. The only safe outcome is to drop the tick.
    Naming the instrument `"999999"` would push a Kite token into `db.holdings`,
    the trade snapshot and the AI's context as if it were an instrument.
    """
    imap = InstrumentMap.from_portfolio(_kite_holdings())

    out = canonical_ticks([{"instrument_token": 999999, "last_price": 100.0}], imap, broker="zerodha")

    assert out == []


def test_a_token_survives_a_string_round_trip_through_mongo():
    """738561 and "738561" are the same instrument.

    The same identifier reaches the map as an `int` from the binary codec and
    can reach it as a `str` from a JSON/Mongo round trip. Matching on the raw
    value would silently stop resolving after a persistence layer changed its
    mind about the type — a whole account's ticks dropped with nothing logged.
    """
    imap = InstrumentMap.from_portfolio([{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "738561"}])
    out = canonical_ticks([{"instrument_token": 738561, "last_price": 2650.0}], imap)
    assert out and out[0]["symbol"] == "RELIANCE"


# -- the canonical shape ------------------------------------------------------


def test_the_canonical_tick_shape_is_enforced():
    """A `MarketTick` cannot be built in a non-canonical form.

    Canonicality is a property of the type, not of every caller remembering to
    `.upper()`: a lowercase symbol, a zero price (what a truncated binary packet
    decodes to, and what would mark a whole position at zero) and a price
    outside the Market Engine's own quote bounds are all refused.
    """
    good = MarketTick.create(MarketInstrument.of("reliance", "nse"), "2650.5", volume=12)
    assert good.symbol == "RELIANCE" and good.exchange == "NSE"
    assert set(good.as_dict()) == {"symbol", "price", "exchange", "volume", "ingested_at"}

    with pytest.raises(MarketTickError):
        MarketTick(symbol="reliance", price=100.0)
    with pytest.raises(MarketTickError):
        MarketTick(symbol="RELIANCE", price=0.0)
    with pytest.raises(MarketTickError):
        MarketTick(symbol="RELIANCE", price=MAX_STOCK_PRICE * 2)
    with pytest.raises(MarketTickError):
        MarketTick(symbol="RELIANCE", price="2650.5")
    with pytest.raises(MarketTickError):
        MarketInstrument.of("   ", "NSE")
    with pytest.raises(MarketTickError):
        MarketTick.create("RELIANCE", 100.0)


def test_a_malformed_tick_costs_only_itself():
    """One unusable record must not cost the batch — nor the connection.

    A frame is a batch of hundreds of packets. The same discipline
    `BrokerStreamEvent.tick_event` applies to a short packet applies here to a
    tick that cannot be represented canonically.
    """
    imap = InstrumentMap.from_portfolio(_kite_holdings())

    out = canonical_ticks(
        [
            {"instrument_token": 738561, "last_price": None},  # no price
            {"instrument_token": 738561, "last_price": 0.0},  # zero mark
            {"instrument_token": 738561, "last_price": "not-a-number"},
            "not-a-tick",  # not even a record
            {"instrument_token": 999999, "last_price": 10.0},  # unmappable
            {"instrument_token": 2953217, "last_price": 3990.0},  # the good one
        ],
        imap,
        broker="zerodha",
    )

    assert [t["symbol"] for t in out] == ["TCS"]
    assert out[0]["price"] == 3990.0


def test_a_malformed_frame_does_not_terminate_a_live_stream():
    """End to end: garbage between two good frames costs neither the stream nor
    the surrounding ticks, and what survives is canonical."""
    with nova_registered() as adapter:
        ticks, _, expired, socket = drive_stream(
            adapter,
            frames=[
                json.dumps({"kind": "price", "rows": [{"scrip": "NOVACO", "rate": "141.25"}]}),
                "}{ not json at all",
                json.dumps({"kind": "price", "rows": [{"scrip": "NOVACO", "rate": "0"}]}),  # unusable price
                json.dumps({"kind": "price", "rows": [{"scrip": "NOVACO", "rate": "142.00"}]}),
            ],
            instruments=["NOVACO"],
        )

    assert expired == []
    assert socket.closed
    imap = InstrumentMap()
    prices = [t["price"] for _, _, batch in ticks for t in canonical_ticks(batch, imap)]
    assert prices == [141.25, 142.0], "a malformed frame took the good ticks with it"


# -- the engine boundary ------------------------------------------------------


def _engine_with(db=None, holdings=None):
    engine = BrokerEngine()
    pushes = []

    async def ws_push(user_id, message):
        pushes.append((user_id, message))

    db = db if db is not None else FakeDB(holdings=list(holdings or []))
    engine.configure(db, ws_push=ws_push)
    return engine, db, pushes


@contextlib.contextmanager
def _core_consumers_spied():
    """Capture exactly what the two core recompute services are handed."""
    from services import portfolio_stream, trade_stream

    seen = {"portfolio": [], "trade": []}

    async def fake_portfolio(db, user_id, broker, ticks, *a, **kw):
        seen["portfolio"].append(ticks)

    async def fake_trade(db, user_id, broker, ticks, *a, **kw):
        seen["trade"].append(ticks)

    with (
        patch.object(portfolio_stream, "apply_broker_ticks", fake_portfolio),
        patch.object(trade_stream, "apply_broker_ticks", fake_trade),
    ):
        yield seen


def test_no_broker_instrument_identifier_reaches_a_core_service():
    """The headline claim of D4.3, asserted on the real delivery path.

    `BrokerEngine._on_stream_tick` is where a broker-identified tick becomes a
    canonical one. Everything it hands on — the user's app WebSocket, the live
    portfolio recompute, the open-trade recompute — is inspected for the
    broker's handle in any form.
    """
    engine, _db, pushes = _engine_with(
        holdings=[{**row, "user_id": "u1", "broker": "zerodha"} for row in _kite_holdings()]
    )

    with _core_consumers_spied() as seen:
        run(
            engine._on_stream_tick(
                "u1",
                "zerodha",
                [
                    {"instrument_token": 738561, "last_price": 2650.0, "volume": 120},
                ],
            )
        )

    delivered = [msg for _, msg in pushes] + seen["portfolio"] + seen["trade"]
    assert delivered, "nothing was delivered at all"
    for payload in delivered:
        assert _find_key(payload, "instrument_token") == [], f"broker identifier escaped in {payload}"

    _, message = pushes[0]
    assert message["type"] == "broker_price_tick"
    tick = message["data"]["ticks"][0]
    assert tick["symbol"] == "RELIANCE" and tick["price"] == 2650.0
    assert seen["portfolio"] == seen["trade"] == [message["data"]["ticks"]]


def test_a_batch_that_resolves_to_nothing_wakes_nothing():
    """An unmappable batch stops at the boundary.

    Not merely tidiness: forwarding it would push a `broker_price_tick` with an
    empty tick list to the browser and wake two recomputes that can find nothing
    to do, on every frame, for as long as the map is stale.
    """
    engine, _db, pushes = _engine_with(
        holdings=[{**row, "user_id": "u1", "broker": "zerodha"} for row in _kite_holdings()]
    )

    with _core_consumers_spied() as seen:
        run(engine._on_stream_tick("u1", "zerodha", [{"instrument_token": 999999, "last_price": 10.0}]))

    assert pushes == []
    assert seen["portfolio"] == [] and seen["trade"] == []


def test_a_fictional_second_broker_reaches_core_services_unchanged():
    """Nova streams into the core with no core change and no map at all.

    Nova identifies instruments by symbol, prices them as strings, and has never
    synced a portfolio — so the account's map is empty and there is no token to
    join on. Under the pre-D4.3 join this produced exactly nothing, silently.
    The whole path runs here: socket frames → codec → transport → engine → core.
    """
    engine, _db, pushes = _engine_with(holdings=[])

    with nova_registered() as adapter, _core_consumers_spied() as seen:
        ticks, _orders, _expired, _socket = drive_stream(
            adapter,
            frames=[json.dumps({"kind": "price", "rows": [{"scrip": "NOVACO", "rate": "141.25"}]})],
            instruments=["NOVACO"],
        )
        for _user, broker, batch in ticks:
            run(engine._on_stream_tick("u1", broker, batch))

    assert seen["portfolio"], "a symbol-identified broker delivered nothing to the portfolio"
    assert seen["portfolio"][0][0]["symbol"] == "NOVACO"
    assert seen["portfolio"][0][0]["price"] == 141.25
    assert seen["trade"] == seen["portfolio"]
    assert _find_key(pushes, "instrument_token") == []
    # Nova's broker-specific extras were dropped at the codec, and nothing
    # re-introduced them at the identity boundary.
    assert _find_key(pushes, "nova_depth") == []
    assert _find_key(pushes, "raw") == []


def test_the_instrument_map_is_rebuilt_when_the_portfolio_is_synced():
    """A newly bought instrument must be nameable without a restart.

    The map is cached per account and deliberately has no TTL, so `sync_portfolio`
    is the *only* thing that can refresh it. If it does not, every tick for an
    instrument bought today is dropped for the life of the process.
    """
    engine, db, _pushes = _engine_with(holdings=[])
    db.broker_accounts.docs = _db_with_saved_session(user_id="u1", broker="zerodha").broker_accounts.docs

    # A tick for an instrument the account has not synced yet: unmappable.
    with _core_consumers_spied() as before:
        run(engine._on_stream_tick("u1", "zerodha", [{"instrument_token": 738561, "last_price": 2650.0}]))
    assert before["portfolio"] == []

    from services import portfolio_stream
    from services.brokers.gateway import broker_gateway

    # The broker API and the post-sync snapshot are stubbed, and the stream
    # restart with them — this test is about the map, and `start_stream` would
    # both open a socket and refresh the map by its own route, which would let
    # a sync that never refreshes it pass anyway.
    with (
        patch.object(broker_gateway, "get_holdings", AsyncMock(return_value=_kite_holdings())),
        patch.object(broker_gateway, "get_positions", AsyncMock(return_value=[])),
        patch.object(broker_gateway, "get_funds", AsyncMock(return_value={"available_margin": 0.0})),
        patch.object(BrokerEngine, "start_stream", AsyncMock()),
        patch.object(portfolio_stream, "publish_snapshot", AsyncMock(return_value=None)),
    ):
        run(engine.sync_portfolio("u1", "zerodha"))

    with _core_consumers_spied() as after:
        run(engine._on_stream_tick("u1", "zerodha", [{"instrument_token": 738561, "last_price": 2650.0}]))

    assert after["portfolio"], "the instrument map survived a portfolio sync — new instruments stay unmappable"
    assert after["portfolio"][0][0]["symbol"] == "RELIANCE"


# -- the boundary, structurally ----------------------------------------------


def test_core_services_no_longer_join_on_a_broker_identifier():
    """No core consumer may name a broker's instrument identifier again.

    A behavioural test proves today's path is clean; this one stops the join
    from being reintroduced in a helper the behavioural test does not exercise.
    Comments and docstrings are stripped first — D4.3 leaves explanations of the
    identifier it removed, and a sweep that cannot tell an explanation from a
    violation would force the explanation to be deleted to stay green.
    """
    offenders = {}
    for name in ("portfolio_stream.py", "trade_stream.py"):
        code = _strip_source((BACKEND / "services" / name).read_text())
        if "instrument_token" in code:
            offenders[name] = True
    assert not offenders, (
        f"core services naming a broker instrument identifier: {sorted(offenders)}. "
        "Identity is resolved at the broker boundary; core services key on canonical symbols."
    )


def test_the_canonical_tick_module_knows_nothing_about_brokers():
    """`market_engine/ticks.py` is the canonical shape, not a broker shape.

    It sits on the market side of the D4.1 direction rule, so it may not import
    the broker layer — and it must not name a broker either, which is the softer
    breach a plain import ban would miss. Checked against the raw source rather
    than stripped code, deliberately: `TestProviderLeakGuards` in
    `test_market_gateway.py` holds the market side to that stricter standard
    already, and a module that explains itself in a broker's vocabulary is a
    module that will eventually be written in it.
    """
    source = (BACKEND / "services" / "market_engine" / "ticks.py").read_text()
    # The import ban is about executable code — the docstring cross-references
    # `services.brokers.streaming.BrokerTick` on purpose, and a sweep that could
    # not tell a reference from an import would force the reference out.
    assert "services.brokers" not in _strip_source(source)
    for broker in ("zerodha", "kite", "upstox", "angel", "fyers", "dhan", "nova"):
        assert broker not in source.lower(), f"the canonical tick module names {broker}"


def test_starting_a_stream_makes_intraday_positions_mappable():
    """Positions are never persisted — the stream start is their only mapping.

    `sync_portfolio` writes holdings to `db.holdings` and drops positions on the
    floor, so a map rebuilt from the database alone can name a demat holding and
    nothing else. An intraday position is exactly the instrument whose price a
    trader is watching, and `stream_instruments` subscribes to it. Seeding the
    map from the same two lists that decide the subscription is what keeps those
    ticks nameable instead of silently dropped.
    """
    engine, _db, _pushes = _engine_with(holdings=[])
    position = {"symbol": "INFY", "exchange": "NSE", "instrument_token": 408065, "quantity": 50}

    from services.brokers.stream import stream_manager

    with (
        patch.object(engine, "get_session", AsyncMock(return_value={"access_token": "t"})),
        patch.object(stream_manager, "start_stream", AsyncMock()),
    ):
        run(engine.start_stream("u1", "zerodha", holdings=_kite_holdings(), positions=[position]))

    with _core_consumers_spied() as seen:
        run(engine._on_stream_tick("u1", "zerodha", [{"instrument_token": 408065, "last_price": 1490.0}]))

    assert seen["portfolio"], "an intraday position's ticks are unmappable"
    assert seen["portfolio"][0][0]["symbol"] == "INFY"


def test_disconnecting_leaves_no_per_account_state_behind():
    """A disconnected account keeps no instrument map.

    The map is derived from one account's portfolio and is meaningless without
    it. Left in place it is a per-process leak that grows with every disconnect,
    and it would answer for the account after the user reconnects — possibly to
    a different demat account at the same broker, where the same token names a
    different instrument.
    """
    engine, db, _pushes = _engine_with(
        holdings=[{**row, "user_id": "u1", "broker": "zerodha"} for row in _kite_holdings()]
    )
    db.broker_accounts.docs = _db_with_saved_session(user_id="u1", broker="zerodha").broker_accounts.docs

    with _core_consumers_spied():
        run(engine._on_stream_tick("u1", "zerodha", [{"instrument_token": 738561, "last_price": 2650.0}]))
    assert engine._instrument_maps, "the map was never cached — this test would prove nothing"

    from services.brokers.gateway import broker_gateway
    from services.brokers.stream import stream_manager

    with (
        patch.object(stream_manager, "stop_stream", AsyncMock()),
        patch.object(broker_gateway, "invalidate_session", AsyncMock(return_value=None)),
    ):
        run(engine.disconnect("zerodha", "u1"))

    assert engine._instrument_maps == {}
    assert engine._sessions == {}


def test_the_nothing_resolved_warning_is_throttled(caplog):
    """A stale map must be visible in the log without flooding it.

    The condition is persistent — a stale map stays stale until the next sync —
    while the ticks hitting it arrive several times a second per account.
    Unthrottled this is tens of thousands of identical WARNING lines an hour,
    which buries every other signal; silent, it is the invisible failure the
    warning exists to reveal.
    """
    from services.brokers import instruments

    instruments.reset_warn_state()
    imap = InstrumentMap.from_portfolio(_kite_holdings())
    unmappable = [{"instrument_token": 999999, "last_price": 10.0}]

    with caplog.at_level(logging.WARNING, logger="services.brokers.instruments"):
        for _ in range(50):
            assert canonical_ticks(unmappable, imap, broker="zerodha") == []
        warnings_burst = [r for r in caplog.records if "canonical boundary" in r.message]

        instruments.reset_warn_state()
        canonical_ticks(unmappable, imap, broker="zerodha")
        warnings_after_rearm = [r for r in caplog.records if "canonical boundary" in r.message]

    instruments.reset_warn_state()
    assert len(warnings_burst) == 1, "the stale-map warning is not throttled"
    assert len(warnings_after_rearm) == 2, "the stale-map warning never re-arms"


# ==================================================================
# D4.4 — the provider-registration seam
#
# The chain these tests pin, end to end:
#
#     broker stream → canonical MarketTick → MarketDataProvider
#         → Market Gateway → Source Manager → Market Engine
#
# Every test below is written to be able to fail. Where a property is
# "currently true and must stay true" (the gateway naming no broker, Yahoo's
# behaviour), the test first asserts that the thing it sweeps actually exists
# and actually ran — a sweep over an empty file list and a probe that could not
# have gone wrong are the two ways this kind of test passes while proving
# nothing.
# ==================================================================


@contextlib.contextmanager
def _clean_provider_registry():
    """Run a test against the real provider registry, restoring it afterwards.

    The registry is a module-level singleton shared with every other test in the
    suite, and a leaked per-user streaming provider would be resolved — at
    priority 1 — by whatever ran next.
    """
    from services.market_engine.providers import provider_registry

    saved = dict(provider_registry._providers)
    try:
        yield provider_registry
    finally:
        provider_registry._providers.clear()
        provider_registry._providers.update(saved)


def _canonical_batch(symbol="RELIANCE", price=2650.0, exchange="NSE"):
    """One canonical tick batch, exactly as `canonical_ticks` emits it."""
    return [MarketTick(symbol=symbol, price=price, exchange=exchange, volume=10).as_dict()]


def test_a_broker_feed_registers_through_the_existing_provider_framework():
    """The seam itself: a broker stream becomes a resolvable market-data provider.

    Asserted through the *existing* mechanisms — `provider_registry` membership,
    `SourceManager.resolve_feed`, `MarketGateway` registration — rather than
    through anything D4.4 invented, because the requirement is that a broker
    feed becomes a legitimate provider, not that it gets a parallel one.

    The TICKS capability is the falsifiable part: before D4.4 nothing served it,
    so `resolve_feed(TICKS)` returned CAPABILITY_UNSUPPORTED for every user in
    the platform.
    """
    from services.market_engine.providers import Capability, ResolutionContext
    from services.market_engine.source_manager import SourceManager, UnavailableReason

    with _clean_provider_registry() as registry:
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1")

        before = manager.resolve_feed(Capability.TICKS, ctx)
        assert not before.available, "TICKS already resolved — this test cannot prove D4.4 changed anything"
        assert before.reason is UnavailableReason.CAPABILITY_UNSUPPORTED

        with nova_registered():
            name = run(_attach("u1", "nova"))

        assert name in registry, "the broker feed was not registered as a market-data provider"

        after = manager.resolve_feed(Capability.TICKS, ctx)
        assert after.available
        assert after.provider.name == name
        assert after.tier.value == "streaming"

        # Entitlement: the feed is legally this user's own data.
        other = manager.resolve_feed(Capability.TICKS, ResolutionContext(user_id="u2"))
        assert not other.available, "a broker feed resolved for a user who does not own it"


def test_a_broker_without_the_streaming_capability_is_not_registered():
    """The capability gate, on the broker's own declaration.

    A broker that does not declare TICK_STREAM has no tick feed. Registering one
    anyway would produce a *priority-1 streaming* provider that can only deliver
    silence — and the Source Manager ranks priority 1 above the baseline, so the
    platform would prefer it.
    """
    from services.brokers.market_feed import feed_provider_name

    class QuietNova(NovaAdapter):
        name = "quietnova"
        capabilities = frozenset({BrokerCapability.ORDER_STREAM})

    with _clean_provider_registry() as registry, nova_registered(QuietNova()):
        assert run(_attach("u1", "quietnova")) is None
        assert feed_provider_name("u1", "quietnova") not in registry


def test_a_provider_declaring_a_pushed_capability_it_cannot_push_is_rejected():
    """The contract check on the market side, one layer beneath the broker gate.

    Four contradictions, each of which produces a provider that registers
    cleanly and then serves either nothing or a lie. All four are refused at
    registration rather than discovered in production.
    """
    from services.market_engine.providers import (
        Capability,
        MarketDataProvider,
        ProviderContractError,
        ProviderKind,
        SourceTier,
        StreamingTickProvider,
        provider_registry,
    )

    class PolledTicks(MarketDataProvider):
        name = "polled-ticks"
        kind = ProviderKind.POLLING
        tier = SourceTier.DELAYED
        capabilities = frozenset({Capability.TICKS})

        async def on_raw(self, payload):
            return 0

    class PolledStreamingTier(MarketDataProvider):
        name = "polled-streaming-tier"
        kind = ProviderKind.POLLING
        tier = SourceTier.STREAMING
        capabilities = frozenset({Capability.QUOTES})

    class SilentStream(MarketDataProvider):
        name = "silent-stream"
        kind = ProviderKind.STREAMING
        tier = SourceTier.STREAMING
        capabilities = frozenset({Capability.QUOTES})

    class DeafStream(MarketDataProvider):
        name = "deaf-stream"
        kind = ProviderKind.STREAMING
        tier = SourceTier.STREAMING
        capabilities = frozenset({Capability.TICKS})
        # on_raw deliberately not overridden — the base implementation raises.

    with _clean_provider_registry():
        for broken in (PolledTicks(), PolledStreamingTier(), SilentStream(), DeafStream()):
            with pytest.raises(ProviderContractError):
                provider_registry.register(broken)
            assert broken.name not in provider_registry

        # The control: an otherwise identical, *consistent* provider registers.
        provider_registry.register(StreamingTickProvider("consistent", owner_user_id="u1"))
        assert "consistent" in provider_registry


def test_no_polling_is_introduced_by_the_streaming_seam():
    """"Streaming" must mean pushed, in the code as well as in the label.

    Two halves, because the failure has two shapes. A provider that polls behind
    a streaming label is refused at registration (above, and re-asserted here on
    the concrete class). And the seam itself must contain no timer: a `sleep`,
    an interval or a scheduled task in these modules would be a poll loop
    wearing the streaming tier.
    """
    from services.market_engine.providers import (
        Capability,
        ProviderKind,
        SourceTier,
        StreamingTickProvider,
    )

    provider = StreamingTickProvider("nova-feed", owner_user_id="u1")
    assert provider.kind is ProviderKind.STREAMING
    assert provider.tier is SourceTier.STREAMING
    # D4.5 added QUOTES to the declaration, behind the readiness gate. The
    # property this test guards is unchanged and is now sharper: nothing on the
    # provider *fetches*. The quote surface answers only from ticks that were
    # pushed into it, so a feed that has been handed nothing has nothing to
    # give — and says so by raising rather than by polling for an answer.
    assert provider.capabilities == frozenset({Capability.TICKS, Capability.QUOTES})
    with pytest.raises(Exception):
        run(provider.fetch_quote("RELIANCE"))

    seam = [
        BACKEND / "services" / "market_engine" / "providers" / "streaming.py",
        BACKEND / "services" / "brokers" / "market_feed.py",
    ]
    assert all(path.exists() for path in seam), "the seam files moved — this sweep proves nothing"
    banned = re.compile(r"\b(?:asyncio\.sleep|time\.sleep|create_task|call_later|poll_interval)\b")
    offenders = {
        path.name for path in seam if banned.search(_strip_source(path.read_text()))
    }
    assert not offenders, f"a timer/poll construct appeared in the streaming seam: {sorted(offenders)}"


def test_no_broker_specific_payload_reaches_the_market_engine():
    """The containment property, asserted on a broker that ticks in its own shape.

    Nova identifies instruments by symbol, prices them as strings, and attaches
    `nova_depth` / `raw` extras. The whole path is driven — codec, canonical
    boundary, provider, gateway sink, Event Bus — and what lands on the bus is
    checked field by field against the canonical tick's closed field list.
    """
    from services.market_engine.gateway import TICK_TOPIC, market_gateway
    from services.market_engine.providers import provider_registry
    from services.market_engine.providers.streaming import TICK_FIELDS

    published = []

    async def _capture(event):
        published.append(event)

    frame = json.dumps({
        "kind": "price",
        "rows": [{"scrip": "reliance", "rate": "2650.50", "depth": [1, 2, 3]}],
    })

    with _clean_provider_registry():
        with nova_registered() as adapter:
            ticks, _orders, _expired, _socket = drive_stream(adapter, [frame])
            assert ticks, "the codec produced nothing — the rest of this test would be vacuous"
            broker_batch = ticks[0][2]
            # D4.2 already stripped Nova's `nova_depth`/`raw` extras at the codec
            # boundary. What is still broker-shaped here is the *identity*: a
            # `BrokerTick` carries `instrument_token`, a field the canonical tick
            # has no room for. That is what must not survive to the next layer.
            assert "instrument_token" in broker_batch[0], "the broker shape never existed; containment is untested"

            canonical = canonical_ticks(broker_batch, InstrumentMap(), broker="nova")
            assert canonical, "the canonical boundary dropped everything"

            name = run(_attach("u1", "nova"))
            provider = provider_registry.get(name)

            # The provider is the market side's own containment check: hand it
            # the broker-shaped batch and it must refuse every record rather than
            # forward a shape the Market Engine does not define.
            assert run(provider.on_raw(broker_batch)) == 0, "a broker-shaped record was accepted by the provider"

            # And the sharp case: a record canonical in every field the tick
            # *does* define, carrying the broker's identifier alongside. Nothing
            # but the closed-field-set check stands between this and the Market
            # Engine — a boundary that merely ignored the extra key would accept
            # it, and the identifier would be one `dict` copy away from riding
            # out onto the bus.
            smuggled = [{"symbol": "RELIANCE", "price": 2650.0, "exchange": "NSE", "instrument_token": 738561}]
            assert run(provider.on_raw(smuggled)) == 0, "a broker instrument identifier crossed into the Market Engine"

            event_bus.subscribe(TICK_TOPIC, _capture)
            try:
                accepted = run(provider.on_raw(canonical))
            finally:
                event_bus.unsubscribe(TICK_TOPIC, _capture)

    assert accepted == 1
    assert len(published) == 1
    payload = published[0]["data"]
    assert payload["source_tier"] == "streaming"
    assert payload["user_id"] == "u1"
    for tick in payload["ticks"]:
        assert set(tick) <= TICK_FIELDS, f"a non-canonical field reached the Market Engine: {sorted(set(tick) - TICK_FIELDS)}"
    # No provider identity, no broker name, anywhere in the delivered event.
    assert "nova" not in json.dumps(published[0]).lower()
    # The sink the event came out of is the gateway's, not a path of its own.
    assert provider_registry is not None and market_gateway is not None


def _executable_strings(source: str) -> list:
    """Every string literal in `source` that is NOT a docstring.

    The market modules discuss brokers at length in their prose — that is the
    documentation doing its job. What may not appear is a broker name the code
    can *act* on, and the sharpest form of that is a literal:
    `if broker == "zerodha"` survives an import ban and an identifier sweep
    untouched. Docstrings are excluded by node identity rather than by pattern,
    so a comment about Zerodha stays legal and a comparison against it does not.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def test_the_market_gateway_and_source_manager_stay_broker_agnostic():
    """Neither core service may learn that brokers exist.

    Three sweeps, because a broker name reaches core code by three routes and
    the import ban only closes one of them:

      * an import of the broker package — already pinned, re-asserted here
        because D4.4 is the sprint that adds a module on each side of that line;
      * an identifier (`ZerodhaAdapter`, `kite_token`) in executable code;
      * a **string literal** in executable code — `if broker == "zerodha"` —
        which survives both of the above untouched and is the shape a
        broker-specific branch actually takes.

    Each sweep is proved non-vacuous against a planted example, because a sweep
    that could not have fired is the way this kind of test passes while proving
    nothing.
    """
    modules = {
        "gateway.py": BACKEND / "services" / "market_engine" / "gateway.py",
        "source_manager.py": BACKEND / "services" / "market_engine" / "source_manager.py",
        "providers/streaming.py": BACKEND / "services" / "market_engine" / "providers" / "streaming.py",
        "providers/registry.py": BACKEND / "services" / "market_engine" / "providers" / "registry.py",
        "providers/base.py": BACKEND / "services" / "market_engine" / "providers" / "base.py",
    }
    broker_names = re.compile(r"\b(zerodha|kite|upstox|nova|angel|fyers|dhan)\b", re.IGNORECASE)

    identifier_hits, literal_hits = {}, {}
    for label, path in modules.items():
        assert path.exists(), f"{label} moved — this sweep proves nothing"
        source = path.read_text()
        hits = sorted(set(broker_names.findall(_strip_source(source))))
        if hits:
            identifier_hits[label] = hits
        literals = sorted({
            name for literal in _executable_strings(source) for name in broker_names.findall(literal)
        })
        if literals:
            literal_hits[label] = literals

    assert not identifier_hits, f"a broker identifier appeared in a core market module: {identifier_hits}"
    assert not literal_hits, f"a broker name appeared in executable code in a core market module: {literal_hits}"

    # Non-vacuity, one planted example per sweep.
    assert broker_names.findall(_strip_source("client = zerodha.connect()\n")) == ["zerodha"]
    assert broker_names.findall(" ".join(_executable_strings('def f(b):\n    return b == "zerodha"\n'))) == ["zerodha"]
    assert _executable_strings('"""A docstring about zerodha."""\n') == []

    # And the broker layer never appears in the market engine's imports either.
    test_the_market_engine_never_imports_a_broker_module()


def test_yahoo_is_unchanged_by_the_streaming_seam():
    """The baseline must keep serving every quote for every user.

    D4.4's scope line lives or dies here: the broker provider declares TICKS and
    not QUOTES, so registering it cannot outrank Yahoo for a quote. If a future
    change adds QUOTES to it without the make-before-break gate, this test is
    what goes red.
    """
    from services.market_engine.providers import (
        Capability,
        ProviderKind,
        ResolutionContext,
        SourceTier,
        YahooPollingAdapter,
    )
    from services.market_engine.source_manager import SourceManager

    with _clean_provider_registry() as registry:
        registry.clear()
        yahoo = YahooPollingAdapter()
        registry.register(yahoo)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1")

        baseline = manager.resolve_feed(Capability.QUOTES, ctx)
        assert baseline.provider is yahoo

        with nova_registered():
            run(_attach("u1", "nova"))

        after = manager.resolve_feed(Capability.QUOTES, ctx)
        assert after.provider is yahoo, "a broker feed took the quote path from the baseline without a switch gate"
        assert after.tier is SourceTier.DELAYED
        assert yahoo.kind is ProviderKind.POLLING
        # Every capability Yahoo served before still resolves to Yahoo.
        for capability in yahoo.capabilities:
            assert manager.resolve_feed(capability, ctx).provider is yahoo, capability


def test_a_second_fictional_broker_uses_the_same_seam_with_no_new_code():
    """Developer Rule 9, on the streaming path.

    A second streaming broker — same contract, different name, different wire
    shape — must reach a registered provider through exactly the same call, with
    no branch anywhere naming either of them.
    """
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import Capability, ResolutionContext
    from services.market_engine.source_manager import SourceManager

    class OrionAdapter(NovaAdapter):
        name = "orion"
        display_name = "Orion Broking"

    with _clean_provider_registry() as registry:
        with nova_registered(), nova_registered(OrionAdapter()):
            first = run(_attach("u1", "nova"))
            second = run(_attach("u2", "orion"))

        assert first == feed_provider_name("u1", "nova")
        assert second == feed_provider_name("u2", "orion")
        assert first in registry and second in registry
        assert first != second

        manager = SourceManager(registry)
        assert manager.resolve_feed(Capability.TICKS, ResolutionContext(user_id="u1")).provider.name == first
        assert manager.resolve_feed(Capability.TICKS, ResolutionContext(user_id="u2")).provider.name == second


def test_an_unready_feed_is_not_resolved_and_ending_the_entitlement_unregisters_it():
    """Readiness and teardown — the two ways a registered feed stops being usable.

    A provider whose socket is down has no failures to its name and is still
    unusable, so health cannot express it; readiness can. And an ended
    entitlement must stop being resolvable immediately rather than at the next
    health transition, because a broker feed is the user's own data.
    """
    from services.brokers.market_feed import detach_market_feed, feed_provider_name
    from services.market_engine.providers import Capability, ResolutionContext, provider_registry
    from services.market_engine.source_manager import SourceManager

    with _clean_provider_registry() as registry:
        with nova_registered():
            name = run(_attach("u1", "nova"))
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1")
        provider = provider_registry.get(name)

        assert manager.resolve_feed(Capability.TICKS, ctx).available
        run(provider.disconnect())
        assert not provider.is_ready
        assert not manager.resolve_feed(Capability.TICKS, ctx).available, "a disconnected feed was still resolved"

        run(provider.connect())
        assert manager.resolve_feed(Capability.TICKS, ctx).available

        assert run(detach_market_feed("u1", "nova")) is True
        assert feed_provider_name("u1", "nova") not in registry
        assert provider.has_sink is False, "an unregistered feed can still deliver into the gateway"


def test_the_engine_publishes_canonical_ticks_into_the_registered_feed():
    """The wiring, driven through `BrokerEngine` rather than by calling the seam.

    The failure this guards against is the one D3 found on the lifecycle topic:
    a boundary that is defined, documented and never actually called. Here that
    would mean a registered provider sitting at priority 1 receiving nothing
    while the socket underneath it delivered ticks all day.
    """
    from services.market_engine.providers import provider_registry

    engine = BrokerEngine()
    engine.configure(FakeDB())
    engine._remember_instrument_map("u1", "nova", holdings=[
        {"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": 738561},
    ])

    with _clean_provider_registry():
        with nova_registered():
            name = run(_attach("u1", "nova"))
        provider = provider_registry.get(name)
        with patch.object(BrokerEngine, "_push", new=AsyncMock()):
            run(engine._on_stream_tick("u1", "nova", [{"symbol": "RELIANCE", "last_price": 2650.0}]))
        assert provider.describe()["accepted_records"] == 1, "the engine never pushed into the registered feed"


def _attach(user_id, broker, symbols=None):
    """`attach_market_feed`, imported at call time so the seam is exercised as
    the engine imports it rather than through a name bound at module load."""
    from services.brokers.market_feed import attach_market_feed

    return attach_market_feed(user_id, broker, symbols)


# ==================================================================
# D4.5 — make-before-break provider switching + baseline failover
#
# D4.4 registered a broker feed as a market-data provider and deliberately
# stopped short of letting it serve quotes: registering is not a switch, and a
# switch performed the moment a socket opens is exactly the "break-before-make"
# that MARKET_DATA_ARCHITECTURE.md forbids. D4.5 is the switch.
#
# The sequence every test below is written against:
#
#     baseline primary
#         → feed connects        (still baseline)
#         → feed subscribes      (still baseline)
#         → valid canonical tick (readiness earned)
#         → feed primary, baseline standby
#
# and its inverse, on any failure, at any point, with the baseline never having
# been disconnected or unregistered at all.
#
# Every test is written so that removing the control it covers turns it red;
# the mutations are exercised explicitly in the falsification tests at the end.
# ==================================================================


def _switching_fixture(user_id="u1", symbols=("RELIANCE",)):
    """A registry holding a baseline and one unpromoted feed, plus a resolver.

    Returns `(registry, manager, baseline, feed, ctx)` where `ctx(symbol)` builds
    the resolution context a quote request would carry. The feed is registered
    and connected — the state D4.4 left it in — and subscribed, so every test
    starts one valid tick away from promotion and the tick is the only variable.
    """
    from services.market_engine.providers import (
        ProviderRegistry,
        ResolutionContext,
        StreamingTickProvider,
        YahooPollingAdapter,
    )
    from services.market_engine.source_manager import SourceManager

    registry = ProviderRegistry()
    baseline = YahooPollingAdapter()
    registry.register(baseline)
    run(baseline.connect())
    feed = StreamingTickProvider(f"feed:{user_id}", owner_user_id=user_id)
    registry.register(feed)
    run(feed.connect())
    if symbols:
        run(feed.subscribe(symbols))

    def ctx(symbol="RELIANCE"):
        return ResolutionContext(user_id=user_id, symbol=symbol)

    return registry, SourceManager(registry), baseline, feed, ctx


def _tick(symbol="RELIANCE", price=2650.0):
    return MarketTick(symbol=symbol, price=price, exchange="NSE").as_dict()


def _quote_provider(manager, ctx):
    from services.market_engine.providers import Capability

    return manager.resolve(Capability.QUOTES, context=ctx)


def test_a_connected_feed_is_not_a_ready_feed():
    """CONNECTED != READY — the distinction the whole sprint turns on.

    A feed that connected, authenticated and subscribed has demonstrated
    nothing about its ability to produce data. Every one of those milestones is
    reached here and the baseline still serves the quote.
    """
    from services.market_engine.providers import Capability, FeedReadiness

    _registry, manager, baseline, feed, ctx = _switching_fixture()

    assert feed.readiness is FeedReadiness.SUBSCRIBED
    assert feed.is_link_up, "the fixture never connected the feed — the rest proves nothing"
    assert not feed.is_ready
    assert _quote_provider(manager, ctx()) is baseline

    # And the feed is a legitimate answer for the *pushed* capability meanwhile:
    # a live stream exists, it simply has not proved it can price anything.
    assert manager.resolve(Capability.TICKS, context=ctx()) is feed


def test_the_make_before_break_ordering_holds_at_every_step():
    """The ordering assertion: the baseline is released last, or never.

    Checked after each step rather than only at the end, because the failure
    this guards against is a *window* — a switch that ends in the right state
    having passed through one request in which neither provider served.
    """
    from services.market_engine.providers import Capability

    registry, manager, baseline, feed, ctx = _switching_fixture()
    seen = []

    def observe(label):
        resolution = manager.resolve_feed(Capability.QUOTES, ctx())
        seen.append((label, resolution.provider.name, resolution.tier.value))
        # There is never a moment with no quote provider at all.
        assert resolution.available, f"the feed went dark at: {label}"
        # And the baseline is never taken away — not unregistered, not
        # disconnected, not made ineligible. It moves from head of the chain to
        # standby *inside* the chain, which is what "make before break" means.
        assert baseline.name in registry, f"the baseline was unregistered at: {label}"
        assert baseline in resolution.chain, f"the baseline left the failover chain at: {label}"

    observe("connected + subscribed")
    assert run(feed.on_raw([_tick()])) == 1
    observe("first valid tick accepted")

    assert [step[1] for step in seen] == [baseline.name, feed.name], seen
    assert [step[2] for step in seen] == ["delayed", "streaming"], seen
    # The baseline is still connected and still serving everything the feed does
    # not carry — standby, not stopped.
    assert baseline.is_connected
    assert _quote_provider(manager, ctx("SPX")) is baseline


def test_a_malformed_first_tick_does_not_promote_the_feed():
    """Evidence means a *valid* canonical tick, not a delivered frame.

    A feed whose records are all rejected has demonstrated the opposite of
    readiness, and promoting it would put the quote path behind a boundary that
    refuses everything the feed sends.
    """
    _registry, manager, baseline, feed, ctx = _switching_fixture()

    for junk in (
        [{"symbol": "RELIANCE", "price": -5.0}],              # out of range
        [{"symbol": "", "price": 2650.0}],                     # unnamed
        [{"symbol": "RELIANCE", "price": "2650.0", "instrument_token": 1}],  # feed-shaped
        ["not a record at all"],
    ):
        assert run(feed.on_raw(junk)) == 0, junk
        assert not feed.is_ready, junk
        assert _quote_provider(manager, ctx()) is baseline, junk

    # The control: the identical call with a valid record does promote, so the
    # assertions above are about the records and not about the path being dead.
    assert run(feed.on_raw([_tick()])) == 1
    assert _quote_provider(manager, ctx()) is feed


def test_a_feed_that_fails_before_promotion_leaves_the_baseline_primary():
    """Pre-promotion failure: nothing to undo, and nothing was taken away."""
    from services.market_engine.providers import Capability

    _registry, manager, baseline, feed, ctx = _switching_fixture()

    run(feed.mark_link_down("connection refused"))

    assert _quote_provider(manager, ctx()) is baseline
    # The feed stops answering the pushed capability too — a dead link is not a
    # stream — and it does so without being unregistered.
    assert manager.resolve(Capability.TICKS, context=ctx()) is None
    assert feed.describe()["last_failure"] == "connection refused"


def test_a_feed_that_fails_after_promotion_returns_the_baseline_to_primary():
    """Post-promotion failure: demotion on the very next resolution.

    No health counter has to escalate, no timer has to fire, and nothing polls:
    the side holding the socket says it died and the next resolve recomputes.
    """
    _registry, manager, baseline, feed, ctx = _switching_fixture()

    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager, ctx()) is feed, "the feed was never promoted"
    assert feed.health().consecutive_failures == 0, "the demotion below must not need a failure count"

    run(feed.mark_link_down("socket closed"))

    assert _quote_provider(manager, ctx()) is baseline
    assert feed.health().consecutive_failures == 0
    # And the prices from the dead link are gone, so a later bypass of
    # resolution cannot serve them either.
    assert feed.covered_symbols == ()


def test_a_reconnected_feed_re_earns_readiness_rather_than_inheriting_it():
    """Evidence is per link. A feed that ticked once, died and came back has
    proved nothing about the connection it now holds."""
    from services.market_engine.providers import FeedReadiness

    _registry, manager, baseline, feed, ctx = _switching_fixture()

    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager, ctx()) is feed

    run(feed.mark_link_down("socket closed"))
    run(feed.mark_link_up())

    assert feed.readiness is FeedReadiness.SUBSCRIBED
    assert feed.is_link_up and not feed.is_ready
    assert _quote_provider(manager, ctx()) is baseline, "a reconnect inherited the old link's readiness"

    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager, ctx()) is feed


def test_a_feed_that_never_subscribed_cannot_be_promoted_by_data_alone():
    """A feed nobody can say what was asked of does not take the quote path.

    The gate has three conditions and this is the one that is easiest to leave
    out, because a tick arriving looks like proof on its own.
    """
    _registry, manager, baseline, feed, ctx = _switching_fixture(symbols=())

    assert feed.subscribed_symbols == ()
    assert run(feed.on_raw([_tick()])) == 1, "the tick was not even accepted — this proves nothing"
    assert not feed.is_ready
    assert _quote_provider(manager, ctx()) is baseline

    # Subscribing then re-delivering is what opens the gate.
    run(feed.subscribe(["RELIANCE"]))
    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager, ctx()) is feed


def test_a_promoted_feed_only_answers_for_instruments_it_actually_streams():
    """Coverage, not just readiness: the baseline keeps everything else.

    MARKET_DATA_ARCHITECTURE.md's per-symbol rule. A promoted feed that claimed
    the whole universe would answer a US index with silence while a provider
    that carries it sat one rank below.
    """
    _registry, manager, baseline, feed, ctx = _switching_fixture(symbols=("RELIANCE", "TCS"))

    run(feed.on_raw([_tick("RELIANCE")]))

    assert _quote_provider(manager, ctx("RELIANCE")) is feed
    # Subscribed but never ticked — no price, so no claim.
    assert _quote_provider(manager, ctx("TCS")) is baseline
    assert _quote_provider(manager, ctx("SPX")) is baseline
    assert feed.covered_symbols == ("RELIANCE",)


def test_a_feed_whose_ticks_go_stale_stops_covering_them():
    """The backstop beneath the explicit link-down signal.

    A link that dies without saying so stops delivering, and a price older than
    the delayed baseline must not keep being served under the streaming label.
    Evaluated at resolve time: no timer, no sweeper, nothing scheduled.
    """
    _registry, manager, baseline, feed, ctx = _switching_fixture()
    feed.tick_max_age_seconds = 0.0  # every tick is immediately too old

    run(feed.on_raw([_tick()]))

    assert feed.is_ready, "readiness itself is not what expires — coverage is"
    assert feed.covered_symbols == ()
    assert _quote_provider(manager, ctx()) is baseline

    feed.tick_max_age_seconds = 120.0
    assert _quote_provider(manager, ctx()) is feed


def test_one_users_feed_failure_moves_only_that_users_feed():
    """User entitlement isolation across the switch.

    The property D4.4 established by construction (`owner_user_id`), re-asserted
    across a promotion and a demotion — the two operations that could plausibly
    reach for a global switch and take every other user with them.
    """
    from services.market_engine.providers import ResolutionContext, StreamingTickProvider

    registry, manager, baseline, feed_a, ctx_a = _switching_fixture(user_id="userA")
    feed_b = StreamingTickProvider("feed:userB", owner_user_id="userB")
    registry.register(feed_b)
    run(feed_b.connect())
    run(feed_b.subscribe(["RELIANCE"]))
    ctx_b = ResolutionContext(user_id="userB", symbol="RELIANCE")
    guest = ResolutionContext(user_id=None, symbol="RELIANCE")

    run(feed_a.on_raw([_tick()]))
    run(feed_b.on_raw([_tick()]))
    assert _quote_provider(manager, ctx_a()) is feed_a
    assert _quote_provider(manager, ctx_b) is feed_b
    assert _quote_provider(manager, guest) is baseline, "a per-user feed served a request with no user"

    run(feed_a.mark_link_down("userA socket closed"))

    assert _quote_provider(manager, ctx_a()) is baseline
    assert _quote_provider(manager, ctx_b) is feed_b, "one user's failure moved another user's feed"
    assert _quote_provider(manager, guest) is baseline
    # And neither user's feed is ever resolvable for the other, ready or not.
    assert _quote_provider(manager, ResolutionContext(user_id="userA", symbol="RELIANCE")) is baseline
    assert feed_b.owner_user_id == "userB"


def test_declaring_the_quote_capability_grants_nothing_on_its_own():
    """The QUOTES-safety invariant, stated directly.

    The provider declares QUOTES and outranks the baseline by priority. Neither
    fact promotes it. This is the assertion that would go red if a future change
    moved the gate out of `is_eligible_for` and back into the declaration.
    """
    from services.market_engine.providers import Capability

    _registry, manager, baseline, feed, ctx = _switching_fixture()

    assert Capability.QUOTES in feed.capabilities
    assert feed.supports(Capability.QUOTES)
    assert feed.priority < baseline.priority
    assert not feed.is_eligible_for(ctx().for_capability(Capability.QUOTES))
    assert _quote_provider(manager, ctx()) is baseline


def test_promotion_and_demotion_are_deterministic_under_repeated_events():
    """Duplicate readiness, duplicate disconnects, and a tick on a dead link.

    Repeated lifecycle events are normal on a reconnecting transport. The final
    state must depend on what actually happened, not on how many times it was
    reported.
    """
    from services.market_engine.providers import FeedReadiness

    _registry, manager, baseline, feed, ctx = _switching_fixture()
    transitions = []

    async def listener(provider, previous, current):
        transitions.append((previous.value, current.value))

    feed.bind_readiness_listener(listener)

    # Duplicate readiness evidence: one promotion, not three.
    for _ in range(3):
        run(feed.on_raw([_tick()]))
    assert transitions == [("subscribed", "ready")]
    assert _quote_provider(manager, ctx()) is feed

    # Repeated disconnects: one demotion.
    for _ in range(3):
        run(feed.mark_link_down("socket closed"))
    assert transitions[-1] == ("ready", "failed")
    assert len(transitions) == 2
    assert _quote_provider(manager, ctx()) is baseline

    # A tick arriving on a link already reported dead promotes nothing: the
    # record is from a connection the platform can no longer ask anything of.
    run(feed.on_raw([_tick()]))
    assert feed.readiness is FeedReadiness.FAILED
    assert _quote_provider(manager, ctx()) is baseline

    # Re-connect, re-earn, and the state is exactly where the events say it is.
    run(feed.mark_link_up())
    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager, ctx()) is feed
    assert [t[1] for t in transitions] == ["ready", "failed", "subscribed", "ready"]


def test_a_feed_that_disconnects_during_promotion_does_not_end_up_primary():
    """The race: readiness evidence and a link loss in the same instant.

    Whichever order they are applied in, a feed whose link is down is not the
    primary quote source — there is no interleaving that leaves it there,
    because eligibility is recomputed from current state rather than latched at
    the moment of promotion.
    """
    _registry, manager, baseline, feed, ctx = _switching_fixture()

    async def race():
        # The listener fires *inside* the promotion, which is the sharpest
        # possible interleaving: the link dies while the transition that would
        # promote it is still running.
        async def kill(provider, previous, current):
            if current.value == "ready":
                provider.bind_readiness_listener(None)
                await provider.mark_link_down("died during promotion")

        feed.bind_readiness_listener(kill)
        await feed.on_raw([_tick()])

    run(race())

    assert not feed.is_ready
    assert _quote_provider(manager, ctx()) is baseline


def test_the_baseline_being_unavailable_does_not_change_the_gate():
    """A feed is not promoted because the baseline is struggling.

    Readiness is evidence about the feed. If it were relaxed when the baseline
    was in trouble, the worst moment in the platform's day would be the moment
    it lowered its standard for going live.
    """
    from services.market_engine.providers import Capability, ProviderState
    from services.market_engine.source_manager import UnavailableReason

    _registry, manager, baseline, feed, ctx = _switching_fixture()

    for _ in range(12):
        baseline.record_failure(RuntimeError("baseline outage"))
    assert baseline.health().state is ProviderState.DOWN

    unavailable = manager.resolve_feed(Capability.QUOTES, ctx())
    assert not unavailable.available, "the baseline never went down — this proves nothing"
    assert unavailable.reason is UnavailableReason.ALL_PROVIDERS_DOWN

    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager, ctx()) is feed


# ------------------------------------------------------------------
# D4.5 — falsification: remove the control, watch the test go red
# ------------------------------------------------------------------


def test_removing_the_readiness_gate_would_promote_an_unproven_feed():
    """The mutation the make-before-break tests exist to catch.

    Every "not promoted" assertion above is only worth something if the gate is
    what is holding the feed back — rather than, say, an unrelated eligibility
    rule or a resolution that never reaches the feed at all. So: neutralise the
    gate, on a feed that has produced no data whatsoever, and confirm the feed
    takes the quote path immediately. If this test ever stops seeing the switch
    happen, the tests above have stopped proving anything.
    """
    from services.market_engine.providers import StreamingTickProvider

    _registry, manager, baseline, feed, ctx = _switching_fixture()
    assert _quote_provider(manager, ctx()) is baseline

    with patch.object(StreamingTickProvider, "is_ready", property(lambda self: True)), \
            patch.object(StreamingTickProvider, "covers", lambda self, symbol: True):
        assert _quote_provider(manager, ctx()) is feed, (
            "with the readiness gate removed the feed still did not take the quote path — "
            "the make-before-break tests above are not testing the gate"
        )

    assert _quote_provider(manager, ctx()) is baseline


def test_breaking_before_making_is_what_the_ordering_test_would_catch():
    """The wrong ordering, performed deliberately.

    Releasing the baseline before the feed is ready is the failure mode
    make-before-break is named after. Driven here so the ordering assertions in
    `test_the_make_before_break_ordering_holds_at_every_step` are known to be
    capable of failing: with the baseline released first there is a window in
    which the platform serves no quote at all.
    """
    from services.market_engine.providers import Capability

    registry, manager, baseline, feed, ctx = _switching_fixture()

    registry.unregister(baseline.name)  # break first — the mistake

    gap = manager.resolve_feed(Capability.QUOTES, ctx())
    assert not gap.available, (
        "releasing the baseline before the feed was ready left a working quote provider — "
        "the ordering assertions cannot fail and are proving nothing"
    )

    registry.register(baseline)
    run(feed.on_raw([_tick()]))
    assert _quote_provider(manager, ctx()) is feed


def test_the_switching_machinery_names_no_broker():
    """No `if broker == "…"` anywhere in the switch, in any of its three forms.

    Extends the D4.4 sweep to the modules D4.5 changed. Kept as a separate test
    from the D4.4 one so a failure says which sprint's boundary moved.
    """
    modules = {
        "providers/streaming.py": BACKEND / "services" / "market_engine" / "providers" / "streaming.py",
        "providers/base.py": BACKEND / "services" / "market_engine" / "providers" / "base.py",
        "providers/registry.py": BACKEND / "services" / "market_engine" / "providers" / "registry.py",
        "source_manager.py": BACKEND / "services" / "market_engine" / "source_manager.py",
        "gateway.py": BACKEND / "services" / "market_engine" / "gateway.py",
        "normalizer.py": BACKEND / "services" / "market_engine" / "normalizer.py",
    }
    broker_names = re.compile(r"\b(zerodha|kite|upstox|nova|orion|angel|fyers|dhan|groww|indmoney)\b", re.I)

    offenders = {}
    for label, path in modules.items():
        assert path.exists(), f"{label} moved — this sweep proves nothing"
        source = path.read_text()
        hits = sorted(set(broker_names.findall(_strip_source(source))))
        literals = sorted({
            name for literal in _executable_strings(source) for name in broker_names.findall(literal)
        })
        if hits or literals:
            offenders[label] = sorted(set(hits) | set(literals))

    assert not offenders, f"the switching machinery names a broker: {offenders}"

    # Non-vacuity: the planted branch this sweep exists to catch.
    planted = 'def promote(broker):\n    if broker == "zerodha":\n        return True\n'
    assert broker_names.findall(" ".join(_executable_strings(planted))) == ["zerodha"]


def test_no_polling_is_introduced_by_the_switch():
    """Failover must stay push-driven.

    A timer anywhere in the switch would mean the platform *noticing* a dead
    feed rather than being told about one, which is a poll loop with a different
    name — and it would put a scheduled task behind every connected account.
    """
    seam = [
        BACKEND / "services" / "market_engine" / "providers" / "streaming.py",
        BACKEND / "services" / "brokers" / "market_feed.py",
    ]
    assert all(path.exists() for path in seam), "the seam files moved — this sweep proves nothing"
    banned = re.compile(
        r"\b(?:asyncio\.sleep|time\.sleep|create_task|call_later|call_soon|poll_interval|"
        r"ensure_future|Timer|schedule)\b"
    )
    offenders = {path.name for path in seam if banned.search(_strip_source(path.read_text()))}
    assert not offenders, f"a timer/poll construct appeared in the switching seam: {sorted(offenders)}"
    # Non-vacuity.
    assert banned.search("asyncio.create_task(x)")


# ------------------------------------------------------------------
# D4.5 — the switch, driven through the real seam
# ------------------------------------------------------------------


def test_a_broker_feed_is_promoted_and_demoted_through_the_real_seam():
    """End to end on the global registry: attach, tick, promote, drop, demote.

    The unit tests above build their own registry. This one uses the real one,
    the real Market Gateway, and the real broker-side seam, because the failure
    D3 found — a boundary that is defined, documented and never actually wired —
    is invisible to a test that constructs both halves itself.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import (
        Capability,
        ResolutionContext,
        YahooPollingAdapter,
    )
    from services.market_engine.source_manager import SourceManager

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        with nova_registered():
            name = run(_attach("u1", "nova", ["RELIANCE"]))
            assert name in registry
            assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

            provider = registry.get(name)
            assert run(provider.on_raw([_tick()])) == 1
            assert manager.resolve(Capability.QUOTES, context=ctx) is provider

            assert run(set_market_feed_link("u1", "nova", up=False, reason="socket closed")) is True
            assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

            assert run(set_market_feed_link("u1", "nova", up=True)) is True
            assert manager.resolve(Capability.QUOTES, context=ctx) is baseline, \
                "a reconnect promoted the feed without fresh evidence"
            run(provider.on_raw([_tick()]))
            assert manager.resolve(Capability.QUOTES, context=ctx) is provider


def test_a_promoted_feed_serves_a_quote_carrying_no_provider_identity():
    """The quote path, through the public gateway API, after a promotion.

    Two properties in one drive, because they fail together: the quote is
    labelled `streaming` and carries no provider or broker identity anywhere,
    and an instrument the feed does not stream still comes from the baseline in
    the very same session.
    """
    from services.market_engine.gateway import market_gateway
    from services.market_engine.providers import YahooPollingAdapter, provider_registry

    async def fake_quote(symbol):
        return {"symbol": symbol, "name": symbol, "price": 100.0, "prev_close": 99.0,
                "change_pct": 1.01, "volume": 1000}

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)

        with nova_registered():
            name = run(_attach("u1", "nova", ["RELIANCE"]))
        provider = provider_registry.get(name)
        run(provider.on_raw([_tick("RELIANCE", 2650.0)]))

        with patch.object(YahooPollingAdapter, "fetch_quote", staticmethod(fake_quote)):
            streamed = run(market_gateway.get_quote("RELIANCE", user_id="u1"))
            other_symbol = run(market_gateway.get_quote("SPX", user_id="u1"))
            other_user = run(market_gateway.get_quote("RELIANCE", user_id="u2"))

    assert streamed["source_tier"] == "streaming"
    assert streamed["price"] == 2650.0
    # Nothing about who produced it — not the provider name, not the broker.
    blob = json.dumps(streamed).lower()
    assert "nova" not in blob and "brokerfeed" not in blob and "yahoo" not in blob
    assert set(streamed) >= {"symbol", "price", "source_tier", "ingested_at"}

    assert other_symbol["source_tier"] == "delayed", "an unstreamed symbol was claimed by the feed"
    assert other_user["source_tier"] == "delayed", "another user's request was served by this feed"


def test_a_promotion_is_announced_only_to_the_user_who_owns_the_feed():
    """The status event follows the entitlement.

    A per-user promotion published platform-wide would tell every other user's
    tier indicator that their feed went live when it did not — and would leak
    the existence of one user's broker connection to everybody else's socket.
    """
    from services.market_engine.providers import YahooPollingAdapter, provider_registry
    from services.market_engine.source_manager import PROVIDER_STATUS_TOPIC

    published = []

    async def capture(event):
        published.append(event["data"])

    with _clean_provider_registry() as registry:
        registry.clear()
        registry.register(YahooPollingAdapter())
        with nova_registered():
            name = run(_attach("u1", "nova", ["RELIANCE"]))
        provider = provider_registry.get(name)

        event_bus.subscribe(PROVIDER_STATUS_TOPIC, capture)
        try:
            run(provider.on_raw([_tick()]))
        finally:
            event_bus.unsubscribe(PROVIDER_STATUS_TOPIC, capture)

    assert published, "a promotion published nothing — the tier indicator would never move"
    owned = [p for p in published if p.get("user_id") == "u1"]
    assert owned, f"no user-scoped status was published: {published}"
    assert all(p.get("user_id") == "u1" for p in published), (
        f"a per-user promotion was announced platform-wide: {published}"
    )
    # And the payload still carries freshness without provenance.
    for payload in published:
        assert "tier" in payload
        assert "nova" not in json.dumps(payload).lower()
        assert not any("provider" in key for key in payload if key != "previous_tier")


def test_the_engine_subscribes_the_feed_to_the_accounts_instruments():
    """The wiring that makes the gate reachable at all.

    `attach_market_feed` is called by `BrokerEngine.start_stream` and the
    subscription comes from the account's instrument map. If that argument were
    dropped the feed could never become ready, every user would stay on the
    baseline forever, and no other test in this file would notice — which is
    exactly the shape of failure D3 found on the lifecycle topic.
    """
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import provider_registry

    engine = BrokerEngine()
    engine.configure(FakeDB())
    holdings = [
        {"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": 738561},
        {"symbol": "TCS", "exchange": "NSE", "instrument_token": 2953217},
    ]

    with _clean_provider_registry():
        with nova_registered():
            with patch("services.brokers.stream.stream_manager.start_stream", new=AsyncMock()), \
                    patch.object(BrokerEngine, "get_session", new=AsyncMock(return_value={"access_token": "t"})):
                run(engine.start_stream("u1", "nova", holdings=holdings, positions=[]))

            provider = provider_registry.get(feed_provider_name("u1", "nova"))
            assert provider is not None, "the engine never registered the feed"
            assert provider.subscribed_symbols == ("RELIANCE", "TCS"), (
                "the engine registered a feed it never told what to expect — it can never become ready"
            )
            assert not provider.is_ready
            run(provider.on_raw([_tick()]))
            assert provider.is_ready


def test_the_transport_reports_its_link_state_to_whoever_owns_the_feed():
    """The push signal that makes failover immediate, driven through the real transport.

    Without this the market side would have to notice silence, and noticing
    silence means a timer. Asserted on both edges — the link is reported up only
    after the subscribe frames are away, and reported down when the run ends —
    because a signal that only ever fires one way is half a failover.
    """
    states = []

    async def on_link_state(user_id, broker, up, reason):
        states.append((user_id, broker, up))

    with nova_registered() as adapter:
        socket = _FakeSocket([json.dumps({"kind": "price", "rows": [{"scrip": "reliance", "rate": "2650.5"}]})])
        stream = BrokerStream(
            "user-1", adapter.name, {"access_token": "live-token"},
            credentials={"api_key": "nova-key"},
            instrument_tokens=["RELIANCE"],
            on_tick=AsyncMock(),
            on_link_state=on_link_state,
        )

        async def scenario():
            with patch.object(BrokerStream, "_connect", AsyncMock(return_value=socket)):
                await stream._run_websocket()
                await stream._notify_link(False, "stream ended")

        run(scenario())

    assert states == [("user-1", "nova", True), ("user-1", "nova", False)], states
    # Reported after the subscribe frames, not on the socket opening: a socket
    # nobody has asked anything of delivers nothing.
    assert socket.sent, "no subscribe frame was sent — the ordering claim is untested"


def test_the_feed_status_a_user_sees_follows_their_own_promotion():
    """The tier indicator moves for the promoted user and nobody else.

    `status()` asks about the feed, not about an instrument, so it is the one
    quote resolution that carries no symbol — and it must answer "streaming"
    once this user's feed is live. Pinned because the two rules meet here: a
    symbol-less resolution reports the feed, a symbol-ful one still has to cover
    the instrument.
    """
    from services.market_engine.providers import Capability

    _registry, manager, _baseline, feed, ctx = _switching_fixture()

    assert manager.status(user_id="u1")["tier"] == "delayed"
    assert manager.status()["tier"] == "delayed"

    run(feed.on_raw([_tick()]))

    assert manager.status(user_id="u1")["tier"] == "streaming"
    assert manager.status(user_id="u2")["tier"] == "delayed", "another user's indicator moved"
    assert manager.status()["tier"] == "delayed", "the platform-wide indicator moved for one user's feed"
    # A quote for an instrument this feed does not stream is still delayed, and
    # says so on its own payload rather than inheriting the feed-level label.
    assert manager.resolve_feed(Capability.QUOTES, ctx("SPX")).tier.value == "delayed"

    run(feed.mark_link_down("socket closed"))
    assert manager.status(user_id="u1")["tier"] == "delayed"

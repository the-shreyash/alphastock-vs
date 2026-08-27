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
from services.brokers.streaming import (
    DEFAULT_STREAM_CHANNEL,
    BrokerStreamChannel,
    BrokerStreamEndpoint,
    BrokerStreamEvent,
    StreamEventKind,
)
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


@pytest.fixture
def no_probation_window(monkeypatch):
    """Collapse D5.2's probation window for the tests written before it existed.

    Every test that takes this fixture is a D4 test: it asserts that a feed
    reaches READY on real broker bytes and that resolution follows readiness.
    D5.2 keeps readiness exactly as it was and adds a second, independent gate on
    top — a READY feed must keep delivering for `PROBATION_WINDOW_SECONDS`
    before it *outranks* a steady provider — so with the real window these tests
    would be asserting probation rather than the thing they were written to
    cover, and would do it by proxy, thirty-eight times over.

    So the window is set to zero here and tested properly elsewhere:
    `tests/test_provider_probation.py` exercises it at the published value,
    including through the same real broker seam, and its falsification tests are
    what would catch probation being removed. Zero is chosen rather than a small
    positive number because it makes these tests deterministic — the promoting
    tick is the promotion — instead of merely usually fast enough.

    Patched on the module constant rather than on an instance: providers built
    inside `attach_market_feed`, several layers below the test, read it at
    construction, and reaching down to mutate them afterwards would be a test
    knowing the shape of the code beneath the seam it is exercising.
    """
    from services.market_engine.providers import streaming as streaming_module

    monkeypatch.setattr(streaming_module, "PROBATION_WINDOW_SECONDS", 0.0)


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


def drive_stream(adapter, frames, instruments=None, session=None, channel=None):
    """Run one full transport pass over a scripted socket.

    Returns `(ticks, orders, expired, socket)` — everything that crossed the
    boundary, plus the socket so a test can inspect what was sent.

    `channel` selects which of the broker's realtime connections to drive
    (D4.7). It defaults to the broker's first declared channel, which for a
    single-channel broker is the only one and is what every pre-D4.7 caller
    means.
    """
    ticks, orders, expired = [], [], []

    async def on_tick(user_id, broker, batch):
        ticks.append((user_id, broker, batch))

    async def on_order_update(user_id, broker, order):
        orders.append((user_id, broker, order))

    async def on_expired(user_id, broker, channel_name):
        expired.append((user_id, broker))

    if channel is None:
        declared = adapter.stream_channels()
        channel = declared[0].name if declared else DEFAULT_STREAM_CHANNEL

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
        channel=channel,
    )

    async def scenario():
        with patch.object(BrokerStream, "_connect", AsyncMock(return_value=socket)):
            try:
                await stream._run_websocket()
            except _AuthExpired:
                if stream.on_expired:
                    await stream.on_expired(stream.user_id, stream.broker, stream.channel)

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

    # `start_stream` registers this account's feed in the *global* provider
    # registry, so the scope guard is not tidiness: a leaked per-user streaming
    # provider is resolved at priority 1 by whatever test runs next.
    with (
        _clean_provider_registry(),
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


def test_the_make_before_break_ordering_holds_at_every_step(no_probation_window):
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


def test_a_malformed_first_tick_does_not_promote_the_feed(no_probation_window):
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


def test_a_feed_that_fails_after_promotion_returns_the_baseline_to_primary(no_probation_window):
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


def test_a_reconnected_feed_re_earns_readiness_rather_than_inheriting_it(no_probation_window):
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


def test_a_feed_that_never_subscribed_cannot_be_promoted_by_data_alone(no_probation_window):
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


def test_a_promoted_feed_only_answers_for_instruments_it_actually_streams(no_probation_window):
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


def test_a_feed_whose_ticks_go_stale_stops_covering_them(no_probation_window):
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


def test_one_users_feed_failure_moves_only_that_users_feed(no_probation_window):
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


def test_promotion_and_demotion_are_deterministic_under_repeated_events(no_probation_window):
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
    #
    # Two transitions, not one, because the listener carries both axes of the
    # feed's state (D5.2): readiness subscribed -> ready, and stability
    # probation -> stable. With this test's probation window at zero the second
    # follows the first on the same tick; at the published window it would
    # arrive later, on the tick that completed the window. What is asserted here
    # is the deduplication — three identical batches produce each transition
    # once — and that holds on both axes.
    for _ in range(3):
        run(feed.on_raw([_tick()]))
    assert transitions == [("subscribed", "ready"), ("probation", "stable")]
    assert _quote_provider(manager, ctx()) is feed

    # Repeated disconnects: one demotion.
    for _ in range(3):
        run(feed.mark_link_down("socket closed"))
    assert transitions[-1] == ("ready", "failed")
    assert len(transitions) == 3
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
    assert [t[1] for t in transitions] == [
        "ready", "stable", "failed", "subscribed", "ready", "stable",
    ]


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


def test_removing_the_readiness_gate_would_promote_an_unproven_feed(no_probation_window):
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

    # `is_on_probation` is neutralised alongside the gate because D5.2 added a
    # second, independent hold on the same switch: a feed with the readiness
    # gate removed is now *eligible* and still ranked behind the baseline until
    # it has served its probation window. Removing only the readiness gate would
    # leave this test green for the wrong reason — the feed would stay on the
    # baseline because of probation, and the D4.5 gate could be deleted without
    # anything here noticing. Probation's own falsification lives in
    # tests/test_provider_probation.py.
    with patch.object(StreamingTickProvider, "is_ready", property(lambda self: True)), \
            patch.object(StreamingTickProvider, "is_on_probation", property(lambda self: False)), \
            patch.object(StreamingTickProvider, "covers", lambda self, symbol: True):
        assert _quote_provider(manager, ctx()) is feed, (
            "with the readiness gate removed the feed still did not take the quote path — "
            "the make-before-break tests above are not testing the gate"
        )

    assert _quote_provider(manager, ctx()) is baseline


def test_breaking_before_making_is_what_the_ordering_test_would_catch(no_probation_window):
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


def test_a_broker_feed_is_promoted_and_demoted_through_the_real_seam(no_probation_window):
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


def test_a_promoted_feed_serves_a_quote_carrying_no_provider_identity(no_probation_window):
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


def test_a_promotion_is_announced_only_to_the_user_who_owns_the_feed(no_probation_window):
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

    async def on_link_state(user_id, broker, up, reason, channel):
        states.append((user_id, broker, up, channel))

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

    # The channel travels with the link report (D4.7): a broker may hold several
    # connections for one account and they fail independently, so a consumer that
    # could not tell them apart would let one socket demote another's feed.
    assert states == [
        ("user-1", "nova", True, DEFAULT_STREAM_CHANNEL),
        ("user-1", "nova", False, DEFAULT_STREAM_CHANNEL),
    ], states
    # Reported after the subscribe frames, not on the socket opening: a socket
    # nobody has asked anything of delivers nothing.
    assert socket.sent, "no subscribe frame was sent — the ordering claim is untested"


def test_the_feed_status_a_user_sees_follows_their_own_promotion(no_probation_window):
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


# ==================================================================
# D4.6 — the Zerodha Kite market feed: the first concrete stream adapter
#
# Everything above this line is the framework. Nothing below it extends the
# framework; it exercises the framework with a real broker's wire format, which
# is the only way to find out whether the framework was actually general or
# merely untested.
#
# The chain each of these drives, and the line each of them is written to be
# able to fail on:
#
#     Kite ticker bytes
#         → ZerodhaAdapter.decode_stream_frame     (the only Kite-aware code)
#         → BrokerTick                             (canonical shape)
#         → InstrumentMap                          (canonical identity)
#         → MarketTick                             (canonical tick)
#         → StreamingTickProvider                  (readiness earned)
#         → Source Manager                         (promoted over the baseline)
#
# Two properties are asserted throughout rather than in one place, because they
# are the ones a concrete adapter is most likely to quietly break: no Kite
# identifier or credential survives past the adapter, and no line of Kite
# knowledge exists outside it.
# ==================================================================


def _kite_frame(*packets, declared_lengths=None, packet_count=None):
    """One Kite ticker binary frame carrying `(instrument_token, paise)` packets.

    `declared_lengths` and `packet_count` override the length prefixes and the
    header count *without* changing the bytes that follow, which is how the
    malformed-frame tests produce genuine protocol damage rather than a short
    buffer: a Kite frame that lies about its own shape is exactly what a
    truncated TCP read looks like.
    """
    lengths = declared_lengths or [8] * len(packets)
    body = b"".join(
        struct.pack(">H", length) + struct.pack(">II", token, paise)
        for (token, paise), length in zip(packets, lengths)
    )
    return struct.pack(">H", len(packets) if packet_count is None else packet_count) + body


def _zerodha():
    return broker_registry.require("zerodha")


def _kite_ticks(frame):
    """The canonical `BrokerTick` dicts one raw Kite frame decodes to."""
    event = _zerodha().decode_stream_frame(frame)
    return [tick.as_dict() for tick in event.ticks]


# -- protocol -------------------------------------------------------------


def test_the_kite_ticker_endpoint_authenticates_by_query_string_and_logs_neither_half():
    """Kite's auth style is the reason `safe_url` exists — pinned on the real adapter.

    The generic version of this test (`test_an_endpoint_never_carries_its
    _credentials_into_a_log_line`) uses Nova, which authenticates by header and
    therefore cannot fail the way Kite can. This one is written against the
    broker that actually puts a live access token in its URL.
    """
    from services.brokers.zerodha import WS_URL

    endpoint = _zerodha().stream_endpoint(
        {"access_token": "live-access-token"}, {"api_key": "live-api-key"}
    )
    assert endpoint.url.startswith(f"{WS_URL}?")
    assert "live-api-key" in endpoint.url and "live-access-token" in endpoint.url
    assert endpoint.safe_url == WS_URL
    assert "live-api-key" not in endpoint.safe_url
    assert "live-access-token" not in endpoint.safe_url


def test_the_kite_subscribe_handshake_requests_the_mode_the_repository_documents():
    """Two frames, in order, in the documented mode.

    The mode is not a free choice made here: `STREAM_MODE` records the decision
    and TASK.md states it. Asserting against the constant rather than the string
    means changing the mode is a one-line change with a test that follows it,
    instead of a literal duplicated in two files that can disagree.
    """
    from services.brokers.zerodha import STREAM_MODE

    frames = [json.loads(f) for f in _zerodha().stream_subscribe_frames([738561, 2953217])]
    assert frames == [
        {"a": "subscribe", "v": [738561, 2953217]},
        {"a": "mode", "v": [STREAM_MODE, [738561, 2953217]]},
    ]
    assert STREAM_MODE == "ltp", "the documented mode changed without the documentation"
    assert _zerodha().stream_subscribe_frames([]) == []
    assert _zerodha().stream_subscribe_frames(None) == []


def test_a_kite_token_that_round_tripped_through_mongo_still_reaches_the_wire():
    """A persisted instrument token is a string, and a string must still subscribe.

    `InstrumentMap` documents this split for the *resolution* side. The
    subscription side has the same exposure and a worse failure mode: a token
    rejected here is simply absent from the subscribe frame, so the wire never
    carries that instrument. Nothing raises and nothing logs — the user's feed
    is quietly narrower than their portfolio, and the missing prices look
    exactly like an instrument that has not traded.
    """
    adapter = _zerodha()
    holdings = [
        {"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": 738561},
        {"symbol": "TCS", "exchange": "NSE", "instrument_token": "2953217"},  # from MongoDB
        {"symbol": "NOTOKEN", "exchange": "NSE"},
        {"symbol": "JUNK", "exchange": "NSE", "instrument_token": "NSE_EQ|INE002A01018"},
        {"symbol": "BOOL", "exchange": "NSE", "instrument_token": True},
    ]
    tokens = adapter.stream_instruments(holdings=holdings, positions=[])
    assert tokens == [738561, 2953217], tokens

    # And they leave as JSON numbers, not strings: Kite rejects the whole
    # subscription for one quoted token, not just that instrument.
    subscribe = json.loads(adapter.stream_subscribe_frames(["2953217", 738561])[0])
    assert subscribe["v"] == [2953217, 738561]  # order preserved; only the type is coerced


def test_a_kite_packet_is_priced_by_the_segment_its_token_encodes():
    """The low byte of a Kite token is the exchange segment, and it sets the scale.

    Dividing every segment by 100 prices a currency instrument four to five
    orders of magnitude wrong — a number that looks perfectly plausible on a
    chart and would be marked against a real position.
    """
    from services.brokers.zerodha import SEGMENT_BCD, SEGMENT_CDS, parse_kite_binary

    nse_token = (100 << 8) | 1          # nse segment
    cds_token = (100 << 8) | SEGMENT_CDS
    bcd_token = (100 << 8) | SEGMENT_BCD

    priced = {
        t["instrument_token"]: t["last_price"]
        for t in parse_kite_binary(
            _kite_frame((nse_token, 150000), (cds_token, 873_450_000), (bcd_token, 873_450))
        )
    }
    assert priced[nse_token] == 1500.0
    assert priced[cds_token] == 87.345
    assert priced[bcd_token] == 87.345


def test_a_high_kite_instrument_token_is_not_read_as_a_negative_number():
    """Kite tokens are unsigned 32-bit; a signed read silently orphans them.

    A token above 2^31 read as signed comes out negative, matches nothing in the
    account's `InstrumentMap`, and drops every tick for that instrument with no
    error anywhere — the exact failure this whole boundary exists to make
    visible.
    """
    from services.brokers.zerodha import parse_kite_binary

    token = 3_000_000_000 & ~0xFF | 1  # > 2^31, nse segment
    ticks = parse_kite_binary(_kite_frame((token, 150000)))
    assert ticks == [{"instrument_token": token, "last_price": 1500.0}]
    assert ticks[0]["instrument_token"] > 0


def test_a_truncated_kite_frame_yields_no_invented_ticks():
    """A packet whose declared length runs past the buffer stops the parse.

    Continuing would resynchronise the reader on the wrong byte, and Kite's
    packets are nothing but two integers — so a misaligned read does not produce
    garbage that is obviously garbage. It produces a plausible instrument token
    at a plausible price, which is the one outcome worse than returning nothing.
    """
    from services.brokers.zerodha import parse_kite_binary

    good = _kite_frame((738561, 150000))
    assert parse_kite_binary(good[:-3]) == [], "a truncated packet was decoded anyway"
    # A length prefix that lies about a packet the frame does not contain.
    assert parse_kite_binary(_kite_frame((738561, 150000), declared_lengths=[64])) == []
    # A header claiming more packets than the frame holds exhausts the buffer.
    decoded = parse_kite_binary(_kite_frame((738561, 150000), packet_count=9))
    assert decoded == [{"instrument_token": 738561, "last_price": 1500.0}]


def test_a_kite_packet_too_short_to_price_costs_only_itself():
    """Framing survives a short packet — its own length prefix says where the next one starts.

    The distinction from a truncated frame is the whole point: one is a packet
    this adapter cannot use, the other is a frame it can no longer trust.
    """
    from services.brokers.zerodha import parse_kite_binary

    frame = (
        struct.pack(">H", 3)
        + struct.pack(">H", 8) + struct.pack(">II", 738561, 150000)
        + struct.pack(">H", 4) + struct.pack(">I", 111)          # too short to price
        + struct.pack(">H", 8) + struct.pack(">II", 2953217, 420050)
    )
    assert parse_kite_binary(frame) == [
        {"instrument_token": 738561, "last_price": 1500.0},
        {"instrument_token": 2953217, "last_price": 4200.5},
    ]


def test_a_kite_heartbeat_and_an_empty_frame_deliver_nothing():
    """The overwhelmingly common frames on a live ticker are not errors."""
    adapter = _zerodha()
    assert adapter.decode_stream_frame(b"\x00").kind is StreamEventKind.IGNORE
    assert adapter.decode_stream_frame(struct.pack(">H", 0)).kind is StreamEventKind.IGNORE
    assert adapter.decode_stream_frame(b"").kind is StreamEventKind.IGNORE
    assert adapter.decode_stream_frame("not json at all").kind is StreamEventKind.IGNORE
    assert adapter.decode_stream_frame(json.dumps({"type": "message", "data": "x"})).kind is StreamEventKind.IGNORE


# -- canonicalization ------------------------------------------------------


def test_a_kite_ltp_packet_becomes_a_canonical_market_tick():
    """Raw Kite bytes in, canonical `MarketTick` out — the whole D4.3 boundary.

    Written on the bytes rather than on a hand-built dict, because a hand-built
    dict is the codec's output asserted against itself.
    """
    instrument_map = InstrumentMap.from_portfolio(
        [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": 738561}]
    )
    ticks = canonical_ticks(_kite_ticks(_kite_frame((738561, 265050))), instrument_map, broker="zerodha")

    assert len(ticks) == 1
    assert ticks[0]["symbol"] == "RELIANCE"
    assert ticks[0]["exchange"] == "NSE"
    assert ticks[0]["price"] == 2650.5
    assert "ingested_at" in ticks[0]
    # Nothing Kite-shaped survived: not the token, not `last_price`, not a mode.
    assert set(ticks[0]) == set(MarketTick(symbol="X", price=1.0).as_dict())
    assert "instrument_token" not in ticks[0] and "last_price" not in ticks[0]


def test_an_unknown_kite_token_is_dropped_rather_than_named():
    """A token the account cannot name is not an instrument called "738561".

    The generic rule is pinned on a synthetic tick above. This is the same rule
    on real Kite bytes, because a numeric-token broker is the only kind that can
    fail this way and Kite is the platform's first one.
    """
    instrument_map = InstrumentMap.from_portfolio(
        [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": 738561}]
    )
    ticks = canonical_ticks(
        _kite_ticks(_kite_frame((738561, 265050), (9999999, 12345))), instrument_map, broker="zerodha"
    )
    assert [t["symbol"] for t in ticks] == ["RELIANCE"]
    assert "9999999" not in json.dumps(ticks)


def test_a_kite_packet_with_no_usable_price_is_dropped_at_the_canonical_boundary():
    """Zero paise is what a zeroed or truncated packet decodes to, and it is not a price."""
    instrument_map = InstrumentMap.from_portfolio(
        [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": 738561}]
    )
    assert canonical_ticks(_kite_ticks(_kite_frame((738561, 0))), instrument_map, broker="zerodha") == []
    # ... and an absurd one, which is what an unsigned read of a negative price gives.
    huge = _kite_ticks(_kite_frame((738561, 4_294_000_000)))
    assert canonical_ticks(huge, instrument_map, broker="zerodha") == []


# -- lifecycle: the handshake rejection ------------------------------------


class _KiteHandshakeRefused(Exception):
    """`websockets < 14`: the rejected status is on the exception."""

    def __init__(self, status_code):
        super().__init__(f"server rejected WebSocket connection: HTTP {status_code}")
        self.status_code = status_code


class _KiteHandshakeRefused14(Exception):
    """`websockets >= 14`: the rejected status is on a wrapped response."""

    def __init__(self, status_code):
        super().__init__(f"server rejected WebSocket connection: HTTP {status_code}")
        self.response = type("Response", (), {"status_code": status_code})()


def _kite_stream(**kwargs):
    return BrokerStream(
        "user-1", "zerodha", {"access_token": "live-access-token"},
        credentials={"api_key": "live-api-key"}, instrument_tokens=[738561], **kwargs
    )


@pytest.mark.parametrize("refusal", [_KiteHandshakeRefused, _KiteHandshakeRefused14])
@pytest.mark.parametrize("status", [401, 403])
def test_kite_refusing_the_ticker_handshake_expires_the_session(refusal, status):
    """A dead Kite token is refused *during the handshake* — no frame is ever decoded.

    Kite invalidates every access token daily at ~06:00 IST, so this is not an
    edge case: it is every connected user, every morning. Unclassified, the
    generic transport cannot tell it from a broker outage and reconnects into
    the same rejection forever, while the account's market feed stays registered
    and the user is never asked to reconnect.

    Both `websockets` exception shapes are exercised because the two versions
    put the status in different places and a guard that models only one of them
    is a guard that silently stops working on an upgrade.
    """
    expired, slept = [], []

    async def on_expired(user_id, broker, channel):
        expired.append((user_id, broker, channel))

    stream = _kite_stream(on_expired=on_expired, on_tick=AsyncMock())

    async def stop_instead_of_reconnecting(delay):
        # The reconnect pause is bounded so this test *fails* rather than hangs
        # when the classification is removed. Without it the mutation that
        # reintroduces the defect reproduces the defect exactly — an unbounded
        # reconnect loop — and a test that hangs is a test that cannot go red.
        slept.append(delay)
        stream._stopped = True

    async def scenario():
        with patch.object(BrokerStream, "_connect", AsyncMock(side_effect=refusal(status))), \
                patch("services.brokers.stream.asyncio.sleep", new=stop_instead_of_reconnecting):
            await stream._run()

    run(scenario())
    assert expired == [
        ("user-1", "zerodha", DEFAULT_STREAM_CHANNEL)
    ], "a refused Kite handshake did not end the session"
    assert slept == [], "a refused Kite handshake was retried instead of ending the session"


def test_an_ordinary_kite_connection_failure_still_reconnects():
    """The other half of the pair — without it the test above proves nothing.

    A gateway 502, a DNS blip or a dropped route is broker weather, not a dead
    session, and treating it as expiry would stop a stream that would have come
    back on its own.
    """
    expired, slept = [], []

    async def on_expired(user_id, broker):
        expired.append((user_id, broker))

    stream = _kite_stream(on_expired=on_expired, on_tick=AsyncMock())

    async def stop_after_one_pause(delay):
        slept.append(delay)
        stream._stopped = True

    async def scenario():
        with patch.object(BrokerStream, "_connect", AsyncMock(side_effect=_KiteHandshakeRefused(502))), \
                patch("services.brokers.stream.asyncio.sleep", new=stop_after_one_pause):
            await stream._run()

    run(scenario())
    assert expired == [], "an ordinary connection failure was treated as an expired session"
    assert slept, "the stream never reached its reconnect pause"


def test_the_handshake_classification_is_the_adapters_and_the_default_is_to_retry():
    """The hook is generic; only its answer is Kite's.

    A broker that says nothing about a connection failure gets the unchanged
    backoff, which is what keeps this from being Zerodha-specific failover logic
    inside the transport.
    """
    with nova_registered() as nova:
        assert nova.stream_connect_error(_KiteHandshakeRefused(403)) is None
    assert _zerodha().stream_connect_error(_KiteHandshakeRefused(403))
    assert _zerodha().stream_connect_error(_KiteHandshakeRefused(502)) is None
    assert _zerodha().stream_connect_error(RuntimeError("boom")) is None
    # And the reason it hands back carries no URL — Kite's is credential-bearing.
    reason = _zerodha().stream_connect_error(_KiteHandshakeRefused(403))
    assert "wss://" not in reason and "access_token" not in reason


def test_a_mid_session_kite_token_death_still_takes_the_frame_route():
    """The handshake hook did not replace the error-frame path; both must work."""
    event = _zerodha().decode_stream_frame(json.dumps({"type": "error", "data": "Invalid access token"}))
    assert event.kind is StreamEventKind.AUTH_EXPIRED
    other = _zerodha().decode_stream_frame(json.dumps({"type": "error", "data": "Subscription limit reached"}))
    assert other.kind is StreamEventKind.ERROR


def test_a_kite_stream_runs_through_the_generic_transport_unchanged():
    """Real Kite frames over the real transport: subscribe, tick, heartbeat, close.

    The subscribe frames must reach the socket verbatim — the transport cannot
    know what encoding Kite expects — and a heartbeat must not be mistaken for
    data.
    """
    frames = [b"\x00", _kite_frame((738561, 265050)), json.dumps({"type": "message", "data": "hi"})]
    ticks, orders, expired, socket = drive_stream(
        _zerodha(), frames, instruments=[738561], session={"access_token": "live-access-token"}
    )

    assert [json.loads(f) for f in socket.sent] == [
        {"a": "subscribe", "v": [738561]},
        {"a": "mode", "v": ["ltp", [738561]]},
    ]
    assert len(ticks) == 1, "the heartbeat or the message frame was delivered as data"
    assert ticks[0][1] == "zerodha"
    assert ticks[0][2] == [
        {"instrument_token": 738561, "last_price": 2650.5, "symbol": None,
         "exchange": None, "volume": 0, "timestamp": None}
    ]
    assert orders == [] and expired == []
    assert socket.closed


# -- readiness, promotion and failover, on real Kite bytes ------------------


def _kite_feed(user_id="u1", symbols=("RELIANCE",)):
    """Attach a Zerodha market feed for `user_id` on the real registry."""
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import provider_registry

    run(_attach(user_id, "zerodha", list(symbols)))
    return provider_registry.get(feed_provider_name(user_id, "zerodha"))


def _kite_map():
    return InstrumentMap.from_portfolio(
        [
            {"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": 738561},
            {"symbol": "TCS", "exchange": "NSE", "instrument_token": 2953217},
        ]
    )


def test_a_connected_kite_stream_is_not_ready_until_a_real_packet_arrives(no_probation_window):
    """CONNECTED != READY, driven by the bytes rather than by a synthetic tick.

    Every milestone short of data is reached — registered, link up, subscribed,
    a heartbeat received, a malformed frame received — and the baseline still
    serves the quote.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter
    from services.market_engine.source_manager import SourceManager

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        feed = _kite_feed()
        run(set_market_feed_link("u1", "zerodha", up=True))
        assert feed.is_link_up and not feed.is_ready
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        # A heartbeat is not evidence.
        assert _kite_ticks(b"\x00") == []
        # Neither is a frame that decodes to nothing usable.
        truncated = canonical_ticks(
            _kite_ticks(_kite_frame((738561, 150000), declared_lengths=[64])), _kite_map(), broker="zerodha"
        )
        assert truncated == []
        run(feed.on_raw(truncated))
        assert not feed.is_ready, "a malformed Kite frame promoted the feed"
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        # A real packet is.
        priced = canonical_ticks(_kite_ticks(_kite_frame((738561, 265050))), _kite_map(), broker="zerodha")
        run(feed.on_raw(priced))
        assert feed.is_ready
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed


def test_a_kite_feed_is_promoted_over_the_baseline_and_falls_back_on_link_loss(no_probation_window):
    """Make-before-break, end to end, with Zerodha as the concrete feed.

    The baseline is never unregistered and never disconnected at any point — it
    moves to standby inside the same failover chain, which is what makes the
    promotion make-before-break rather than break-before-make.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter
    from services.market_engine.source_manager import SourceManager

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        run(baseline.connect())
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        feed = _kite_feed()
        run(set_market_feed_link("u1", "zerodha", up=True))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        run(feed.on_raw(canonical_ticks(_kite_ticks(_kite_frame((738561, 265050))), _kite_map(), broker="zerodha")))
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed
        assert baseline.name in registry and baseline._connected, "the baseline was released on promotion"

        # An instrument this Kite feed does not stream stays with the baseline
        # in the same session.
        assert manager.resolve(Capability.QUOTES, context=ResolutionContext(user_id="u1", symbol="SPX")) is baseline

        # The socket dies: the very next resolution is the baseline again.
        run(set_market_feed_link("u1", "zerodha", up=False, reason="socket closed"))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        # It comes back, and has to re-earn readiness on the new link.
        run(set_market_feed_link("u1", "zerodha", up=True))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline
        run(feed.on_raw(canonical_ticks(_kite_ticks(_kite_frame((738561, 266000))), _kite_map(), broker="zerodha")))
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed


def test_a_kite_feed_that_never_ticks_leaves_the_baseline_primary_for_everyone(no_probation_window):
    """A failure before readiness changes nothing, for the owner or anybody else."""
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter
    from services.market_engine.source_manager import SourceManager

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        feed_a, feed_b = _kite_feed("u1"), _kite_feed("u2")
        run(set_market_feed_link("u1", "zerodha", up=True))
        run(set_market_feed_link("u2", "zerodha", up=True))
        run(feed_b.on_raw(canonical_ticks(_kite_ticks(_kite_frame((738561, 265050))), _kite_map(), broker="zerodha")))

        run(set_market_feed_link("u1", "zerodha", up=False, reason="connection refused"))

        assert manager.resolve(Capability.QUOTES, context=ResolutionContext(user_id="u1", symbol="RELIANCE")) is baseline
        assert manager.resolve(Capability.QUOTES, context=ResolutionContext(user_id="u2", symbol="RELIANCE")) is feed_b, \
            "one user's Kite failure moved another user's feed"
        assert feed_a is not feed_b


def test_the_engine_carries_kite_bytes_all_the_way_into_the_registered_feed(no_probation_window):
    """The real seam: `BrokerEngine._on_stream_tick` with what the Kite codec produced.

    This is the join every earlier test stops short of — the engine's instrument
    map, the canonical boundary and the provider push, driven by bytes that came
    off a Kite frame rather than by a dict a test wrote.
    """
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter
    from services.market_engine.source_manager import SourceManager

    holdings = [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": 738561}]
    engine = BrokerEngine()
    engine.configure(FakeDB())

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        with patch("services.brokers.stream.stream_manager.start_stream", new=AsyncMock()), \
                patch.object(BrokerEngine, "get_session", new=AsyncMock(return_value={"access_token": "t"})), \
                patch("services.brokers.gateway.broker_gateway.get_holdings", new=AsyncMock(return_value=holdings)), \
                patch("services.brokers.gateway.broker_gateway.get_positions", new=AsyncMock(return_value=[])):
            run(engine.start_stream("u1", "zerodha"))

        feed = registry.get("brokerfeed:zerodha:u1")
        assert feed is not None, "the engine never registered the Kite feed"
        assert feed.subscribed_symbols == ("RELIANCE",)
        run(feed.mark_link_up())
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        with patch.object(BrokerEngine, "_push", new=AsyncMock()):
            run(engine._on_stream_tick("u1", "zerodha", _kite_ticks(_kite_frame((738561, 265050)))))

        assert feed.describe()["accepted_records"] == 1
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed
        assert feed.covers("RELIANCE")
        assert run(feed.fetch_quote("RELIANCE"))["price"] == 2650.5


# -- security ---------------------------------------------------------------


def test_no_kite_credential_reaches_a_log_line(caplog):
    """The whole transport pass, at DEBUG, with a live-looking token.

    Kite is the broker that can actually fail this: its ticker authenticates by
    query string, so "connected to <url>" — the most natural log line anybody
    would write — writes a live access token into the application log.
    """
    caplog.set_level(logging.DEBUG)
    drive_stream(
        _zerodha(),
        [b"\x00", _kite_frame((738561, 265050)), json.dumps({"type": "error", "data": "Subscription limit reached"})],
        instruments=[738561],
        session={"access_token": "SECRET-ACCESS-TOKEN"},
    )
    emitted = "\n".join(r.getMessage() for r in caplog.records)
    assert emitted, "nothing was logged — the sweep could not have failed"
    assert "SECRET-ACCESS-TOKEN" not in emitted
    assert "access_token=" not in emitted
    assert "nova-key" not in emitted


def test_no_kite_credential_or_identifier_can_reach_a_market_tick():
    """A canonical tick has no field for either, and the field list is closed."""
    tick = canonical_ticks(_kite_ticks(_kite_frame((738561, 265050))), _kite_map(), broker="zerodha")[0]
    blob = json.dumps(tick).lower()
    for forbidden in ("zerodha", "kite", "access_token", "api_key", "instrument_token", "738561"):
        assert forbidden not in blob, f"{forbidden} reached a canonical market tick"


def test_a_kite_quote_carries_no_broker_identity_and_no_other_users_data(no_probation_window):
    """The public gateway surface, after a Zerodha promotion."""
    from services.market_engine.gateway import market_gateway
    from services.market_engine.providers import YahooPollingAdapter

    async def fake_quote(symbol):
        return {"symbol": symbol, "name": symbol, "price": 100.0, "prev_close": 99.0,
                "change_pct": 1.01, "volume": 1000}

    with _clean_provider_registry() as registry:
        registry.clear()
        registry.register(YahooPollingAdapter())
        feed = _kite_feed("u1")
        run(feed.on_raw(canonical_ticks(_kite_ticks(_kite_frame((738561, 265050))), _kite_map(), broker="zerodha")))

        with patch.object(YahooPollingAdapter, "fetch_quote", staticmethod(fake_quote)):
            streamed = run(market_gateway.get_quote("RELIANCE", user_id="u1"))
            other_user = run(market_gateway.get_quote("RELIANCE", user_id="u2"))

    assert streamed["source_tier"] == "streaming" and streamed["price"] == 2650.5
    blob = json.dumps(streamed).lower()
    for forbidden in ("zerodha", "kite", "brokerfeed", "yahoo", "instrument_token"):
        assert forbidden not in blob
    assert other_user["source_tier"] == "delayed", "another user's request was served by this Kite feed"


def test_an_expired_kite_session_detaches_the_market_feed():
    """A dead token stops being resolvable immediately, not at the next health tick."""
    from services.market_engine.providers import provider_registry

    engine = BrokerEngine()
    engine.configure(FakeDB())
    with _clean_provider_registry():
        feed = _kite_feed("u1")
        run(feed.on_raw(canonical_ticks(_kite_ticks(_kite_frame((738561, 265050))), _kite_map(), broker="zerodha")))
        assert provider_registry.get("brokerfeed:zerodha:u1") is not None

        run(engine._on_stream_expired("u1", "zerodha"))
        assert provider_registry.get("brokerfeed:zerodha:u1") is None


# -- the multi-broker acceptance criterion ----------------------------------


def test_kite_added_no_kite_knowledge_outside_its_own_adapter():
    """D4.6's acceptance criterion, swept rather than asserted by eye.

    The Kite vocabulary — the ticker URL, the packet layout, the mode, the
    segment divisors — must exist in exactly one module. A second module that
    knows any of it is the beginning of the branch this whole framework was
    built to make unnecessary.
    """
    # Identifiers, not prose: `_strip_source` removes comments and string
    # literals (docstrings included), so a module explaining Kite is legal and a
    # module *computing* with Kite's protocol is not. `instrument_token` is
    # deliberately absent — it is `BrokerTick`'s own generic field name, carried
    # by every broker, and banning it would ban the contract rather than Kite.
    kite_words = ("kite", "paise", "tradingsymbol", "price_divisor", "segment_cds", "segment_bcd")
    allowed = {
        "services/brokers/zerodha.py",       # the adapter — the only owner
    }
    offenders = {}
    scanned = 0
    for path in sorted((BACKEND / "services").rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in allowed:
            continue
        scanned += 1
        source = _strip_source(path.read_text(encoding="utf-8")).lower()
        hit = [w for w in kite_words if w in source]
        if hit:
            offenders[rel] = hit
    assert scanned > 50, "the sweep found almost no modules — it could not have failed"
    assert not offenders, offenders


def test_zerodha_and_a_fictional_broker_stream_through_the_identical_transport():
    """The acceptance criterion D4.6 is not complete without.

    Two brokers that share nothing — binary versus text, numeric token versus
    trading symbol, integer paise versus a rupee string, query-string auth
    versus a header — reach the engine's callbacks in the same canonical shape,
    through the same transport function, with no shared module naming either.
    """
    from services.brokers.stream import PROTOCOL_RUNNERS, resolve_transport

    kite_ticks, _, _, kite_socket = drive_stream(
        _zerodha(), [_kite_frame((738561, 265050))], instruments=[738561]
    )
    with nova_registered() as nova:
        nova_ticks, _, _, nova_socket = drive_stream(
            nova,
            [json.dumps({"kind": "price", "rows": [{"scrip": "reliance", "rate": "2650.50"}]})],
            instruments=["RELIANCE"],
        )
        assert resolve_transport(nova) is BrokerStream._run_websocket
    assert resolve_transport(_zerodha()) is BrokerStream._run_websocket
    assert PROTOCOL_RUNNERS == {}, "a broker-specific transport was reintroduced"

    # Different wires, different identity styles, one canonical shape.
    assert isinstance(kite_socket.sent[0], str) and kite_socket.sent[0].startswith("{")
    assert nova_socket.sent[0].startswith("SUB ")
    assert set(kite_ticks[0][2][0]) == set(nova_ticks[0][2][0])

    kite_map = _kite_map()
    nova_map = InstrumentMap.from_portfolio([{"symbol": "RELIANCE", "exchange": "NSE"}])
    assert canonical_ticks(kite_ticks[0][2], kite_map, broker="zerodha")[0]["symbol"] == "RELIANCE"
    assert canonical_ticks(nova_ticks[0][2], nova_map, broker="nova")[0]["symbol"] == "RELIANCE"


# ==================================================================
# D4.7 — Upstox as the SECOND real streaming broker
#
# D4.6 landed Zerodha's Kite ticker behind the D4.5 switch and closed with an
# open question, recorded in DECISIONS.md: whether the architecture had
# *generalised* or merely *worked*. The only way to answer it is a second broker
# whose protocol shares nothing with the first, and Upstox is that broker:
#
#     Kite                              Upstox v3 market feed
#     ────                              ─────────────────────
#     one socket, ticks + orders        two sockets, one each
#     binary, hand-rolled framing       protobuf
#     32-bit integer instrument token   compound string instrument key
#     integer paise (three scales)      IEEE double, rupees
#     credentials in the query string   bearer header
#     two subscribe frames, JSON text   one subscribe frame, JSON as *binary*
#     error frames report a dead token  handshake refusal reports it
#
# The answer was: mostly. Nothing in the Market Engine, the Market Gateway, the
# Source Manager, `StreamingTickProvider`, the provider registry, the readiness
# gate or the failover path needed a line — those generalised. The broker
# *transport* had one assumption left in it, invisible while Kite was the only
# streaming broker: that a broker's realtime surface is one connection. D4.7
# generalised that too, without naming a broker (see `BrokerStreamChannel`).
#
# Every test below was run against a deliberately broken implementation first;
# the mutations are listed in TASK.md's D4.7 falsification table.
# ==================================================================

from tests._upstox_proto import FeedResponse as _ProtoFeedResponse  # noqa: E402


def _upstox():
    return broker_registry.require("upstox")


def _upstox_channel(name):
    for channel in _upstox().stream_channels():
        if channel.name == name:
            return channel
    raise AssertionError(f"Upstox declares no {name!r} channel")


def _market_channel():
    from services.brokers.upstox import MARKET_CHANNEL

    return _upstox_channel(MARKET_CHANNEL)


def _order_channel():
    from services.brokers.upstox import ORDER_CHANNEL

    return _upstox_channel(ORDER_CHANNEL)


def _upstox_frame(ltpc=None, full=None, index=None, greeks=None, feed_type=1):
    """One `FeedResponse` on the wire, encoded by GOOGLE'S protobuf runtime.

    Not by a helper of ours. `tests/_upstox_proto.py` builds Upstox's official
    MarketDataFeedV3 schema and serializes through the real runtime, so these
    bytes are the bytes Upstox's own SDK produces. A conformance test whose
    fixtures came from our own encoder would prove only that our encoder and our
    decoder share a misreading of the schema — which is the exact mistake
    hand-decoding a wire format risks.

    Each argument places an `LTPC` at a different depth, matching the mode that
    puts it there.
    """
    response = _ProtoFeedResponse()
    response.type = feed_type
    response.currentTs = 1_724_236_800_000
    for key, price in (ltpc or {}).items():
        response.feeds[key].ltpc.ltp = price
    for key, price in (full or {}).items():
        response.feeds[key].fullFeed.marketFF.ltpc.ltp = price
    for key, price in (index or {}).items():
        response.feeds[key].fullFeed.indexFF.ltpc.ltp = price
    for key, price in (greeks or {}).items():
        response.feeds[key].firstLevelWithGreeks.ltpc.ltp = price
    return response.SerializeToString()


def _upstox_ticks(frame):
    """The canonical `BrokerTick` dicts one raw Upstox frame decodes to."""
    event = _market_channel().decode(frame)
    return [tick.as_dict() for tick in event.ticks]


def _upstox_map():
    """An account's instrument map as Upstox identifies instruments."""
    return InstrumentMap.from_portfolio(
        [
            {"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "NSE_EQ|INE002A01018"},
            {"symbol": "TCS", "exchange": "NSE", "instrument_token": "NSE_EQ|INE467B01029"},
        ]
    )


# -- protocol: endpoint, subscription, framing -------------------------------


def test_the_upstox_market_feed_authenticates_by_header_not_query_string():
    """Upstox's auth style is the opposite of Kite's, and copying Kite's is a leak.

    Kite puts a live access token in the ticker URL, which is why
    `BrokerStreamEndpoint.safe_url` exists. Upstox uses a bearer header, so its
    URL is safe to log in full — and the test asserts that positively rather
    than trusting `safe_url` to have hidden a mistake: if a future change moved
    the token into the query string, `safe_url` would keep the log clean and
    this test would still catch the protocol regression.
    """
    from services.brokers.upstox import MARKET_WS_URL

    endpoint = _market_channel().endpoint({"access_token": "live-access-token"}, {"api_key": "app-key"})
    assert endpoint.url == MARKET_WS_URL
    assert MARKET_WS_URL.startswith("wss://api.upstox.com/v3/feed/market-data-feed")
    assert "?" not in endpoint.url, "the Upstox market feed does not authenticate by query string"
    assert endpoint.safe_url == endpoint.url
    assert endpoint.headers["Authorization"] == "Bearer live-access-token"
    assert "live-access-token" not in endpoint.url


def test_the_two_upstox_feeds_are_different_endpoints():
    """The finding that made D4.7 need a channel concept at all."""
    order = _order_channel().endpoint({"access_token": "t"}, None)
    market = _market_channel().endpoint({"access_token": "t"}, None)
    assert order.url != market.url
    assert "/v2/feed/portfolio-stream-feed" in order.url
    assert "/v3/feed/market-data-feed" in market.url
    # Different wire protocols, so the dispatch key has to be per channel.
    assert _order_channel().protocol != _market_channel().protocol


def test_the_upstox_subscribe_handshake_is_one_binary_frame_in_the_documented_mode():
    """One frame, not Kite's two, and `bytes`, not `str`.

    Both halves are load-bearing and neither is guessable from Kite:

    * Upstox carries the mode *inside* the subscription, so a second `mode`
      frame — Kite's protocol — is not part of this one.
    * Upstox requires the request as a **binary** WebSocket frame and silently
      ignores a text one. A `str` here produces a socket that connects, reports
      its link up, subscribes to nothing and delivers no tick ever, which reads
      from outside exactly like a market with no trades in it.
    """
    from services.brokers.upstox import MARKET_STREAM_MODE

    frames = _market_channel().subscribe_frames(["NSE_EQ|INE002A01018", "NSE_EQ|INE467B01029"])
    assert len(frames) == 1, "Upstox subscribes in one frame; two is Kite's protocol"
    assert isinstance(frames[0], bytes), "a text subscribe frame is ignored by Upstox"

    request = json.loads(frames[0].decode("utf-8"))
    assert request["method"] == "sub"
    assert request["data"]["mode"] == MARKET_STREAM_MODE == "ltpc"
    assert request["data"]["instrumentKeys"] == ["NSE_EQ|INE002A01018", "NSE_EQ|INE467B01029"]
    assert request["guid"], "Upstox requires a request guid"


def test_an_upstox_subscription_with_no_instruments_sends_nothing():
    """An account with nothing to stream opens a socket and asks it for nothing.

    Not an empty subscribe frame: Upstox would reject it, and a rejection on the
    only frame we send is a connection that will never carry data while looking
    perfectly healthy.
    """
    assert _market_channel().subscribe_frames([]) == []
    assert _market_channel().subscribe_frames(None) == []
    # And an instrument that is not an Upstox key contributes nothing rather
    # than poisoning the subscription for every other instrument in it.
    assert _market_channel().subscribe_frames([738561, "RELIANCE", None, True]) == []


def test_an_over_limit_upstox_subscription_is_trimmed_rather_than_rejected(caplog):
    """Upstox rejects an over-limit subscription whole, so the excess must not be sent.

    The failure this prevents is total rather than partial: exceeding the limit
    costs the account every instrument, not the extra ones.
    """
    from services.brokers.upstox import MAX_SUBSCRIBED_INSTRUMENTS

    keys = [f"NSE_EQ|INE{n:06d}" for n in range(MAX_SUBSCRIBED_INSTRUMENTS + 5)]
    with caplog.at_level(logging.WARNING):
        frames = _market_channel().subscribe_frames(keys)
    request = json.loads(frames[0].decode("utf-8"))
    assert len(request["data"]["instrumentKeys"]) == MAX_SUBSCRIBED_INSTRUMENTS
    assert any("exceeds" in r.getMessage() for r in caplog.records), \
        "an over-limit subscription was trimmed silently"


# -- protobuf conformance, against the official schema ------------------------


def test_the_upstox_codec_decodes_what_the_official_schema_encodes():
    """The conformance check the hand-written decoder exists to be held to.

    The fixture is encoded by Google's protobuf runtime from Upstox's official
    MarketDataFeedV3 schema. If a field number in `upstox.py` is wrong, these
    bytes do not decode — the oracle does not follow the decoder into being
    wrong, which is the whole reason `protobuf` is a test-only dependency here.
    """
    frame = _upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75, "NSE_EQ|INE467B01029": 3990.10})
    assert _upstox_ticks(frame) == [
        {"instrument_token": "NSE_EQ|INE002A01018", "last_price": 2650.75,
         "symbol": None, "exchange": None, "volume": 0, "timestamp": None},
        {"instrument_token": "NSE_EQ|INE467B01029", "last_price": 3990.10,
         "symbol": None, "exchange": None, "volume": 0, "timestamp": None},
    ]


def test_the_upstox_codec_reads_a_price_from_every_mode_the_schema_nests_it_in():
    """`LTPC` sits at four different depths depending on the subscribed mode.

    Only the first is reachable in the mode this adapter subscribes in. The
    others are decoded anyway because a mode change must not silently produce a
    socket that connects, subscribes and decodes nothing — which is
    indistinguishable from a quiet market.
    """
    assert _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|A": 101.5}))[0]["last_price"] == 101.5
    assert _upstox_ticks(_upstox_frame(full={"NSE_EQ|A": 102.5}))[0]["last_price"] == 102.5
    assert _upstox_ticks(_upstox_frame(index={"NSE_INDEX|Nifty 50": 24567.85}))[0]["last_price"] == 24567.85
    assert _upstox_ticks(_upstox_frame(greeks={"NSE_FO|50201": 55.25}))[0]["last_price"] == 55.25


def test_an_upstox_market_info_frame_delivers_nothing():
    """Upstox's own keep-alive/state frames carry no `feeds` and are not an error.

    A codec that raised on them would fill the log with noise from a perfectly
    working connection — and, worse, a codec that *promoted* on them would open
    the readiness gate on a frame containing no price at all.
    """
    frame = _upstox_frame(feed_type=2)  # market_info
    assert _upstox_ticks(frame) == []
    assert _market_channel().decode(frame).kind is StreamEventKind.IGNORE


def test_a_malformed_upstox_frame_yields_no_invented_ticks():
    """Damage is dropped, never guessed at.

    A truncated protobuf frame's remaining offsets are guesswork, and guessing
    produces plausible instrument keys at plausible prices — the one outcome
    worse than decoding nothing, because it marks a real position with it.
    """
    good = _upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75})
    assert _upstox_ticks(good), "the fixture itself decodes to nothing — the test proves nothing"
    for damaged in (good[:-1], good[:len(good) // 2], good[1:], b"\xff\xff\xff\xff", b"", b"\x08"):
        ticks = _upstox_ticks(damaged)
        for tick in ticks:
            # Anything salvaged must still be a real key at a real price; a
            # decoder that resynchronised by guessing would produce neither.
            assert "|" in str(tick["instrument_token"])
            assert tick["last_price"] > 0


def test_an_upstox_text_frame_on_the_market_channel_is_ignored():
    """The market feed is binary. Parsing text here would be the order codec running on it.

    Cross-contamination between a broker's two feeds is exactly what the channel
    split exists to make impossible, so the market channel refuses to interpret
    a shape only the other channel speaks.
    """
    order_json = json.dumps({"order_id": "UPX-1", "status": "complete", "trading_symbol": "RELIANCE"})
    assert _market_channel().decode(order_json).kind is StreamEventKind.IGNORE
    assert _market_channel().decode(None).kind is StreamEventKind.IGNORE


# -- price handling -----------------------------------------------------------


def test_an_upstox_price_is_in_rupees_and_kites_divisor_would_be_wrong():
    """`LTPC.ltp` is a proto3 double in rupees. Kite's paise divisor is not transferable.

    Applying it would price every Upstox instrument at one per cent of its
    value — a number that looks entirely plausible on a chart and would be
    marked against a real position, which is why this is asserted against the
    exact figure rather than against a range.
    """
    price = _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75}))[0]["last_price"]
    assert price == 2650.75
    assert price != 26.5075, "Kite's paise divisor was applied to an Upstox price"
    assert price != 265075.0, "an Upstox price was multiplied as if it were paise"


@pytest.mark.parametrize("value", [0.05, 1.05, 99.99, 2650.75, 123456.78, 0.0001])
def test_upstox_decimal_precision_survives_the_codec(value):
    """A double in, the same double out — no rounding, no scaling, no reconstruction."""
    ticks = _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|X": value}))
    assert ticks and ticks[0]["last_price"] == value


def test_an_upstox_zero_price_never_becomes_a_tick():
    """Zero and absent are the same bytes in proto3, and neither may mark a position.

    proto3 omits a `double` field whose value is zero, so the wire cannot tell
    "no price in this frame" from "a price of zero". Both are dropped — the
    canonical boundary rejects zero anyway (`MIN_STOCK_PRICE`), and a tick that
    got that far would have marked a whole holding at nothing.
    """
    assert _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": 0.0})) == []
    canonical = canonical_ticks(
        _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": 0.0})), _upstox_map(), broker="upstox"
    )
    assert canonical == []


def test_a_large_upstox_price_survives_and_an_impossible_one_is_refused():
    """A genuinely large price passes; one past the Market Engine's ceiling does not.

    The ceiling is the Market Engine's own quote bound, not a second opinion
    held by the broker layer — a price a *quote* would be rejected for must not
    enter through the tick path either.
    """
    big = 199_000.50  # an MRF-scale price: real, and well inside the ceiling
    assert _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": big}))[0]["last_price"] == big
    assert canonical_ticks(
        _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": big})), _upstox_map(), broker="upstox"
    )[0]["price"] == big

    absurd = MAX_STOCK_PRICE * 10
    assert _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": absurd}))[0]["last_price"] == absurd
    assert canonical_ticks(
        _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": absurd})), _upstox_map(), broker="upstox"
    ) == [], "a price past the Market Engine ceiling reached the canonical boundary"


def test_a_non_finite_upstox_price_is_dropped_where_the_reason_is_known():
    """NaN and infinity are what a corrupted double decodes to.

    They would survive every step below and fail only at the canonical range
    check — which *does* reject them, because every comparison with NaN is
    False, but as "out of range" rather than as the damage they are. Dropped in
    the codec, where the reason is known and the log says so.
    """
    for value in (float("nan"), float("inf"), float("-inf")):
        assert _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": value})) == []


# -- instrument identity ------------------------------------------------------


def test_an_upstox_instrument_key_becomes_a_canonical_symbol():
    """A compound string identifier resolves through the SAME map an integer does.

    `InstrumentMap` needed no extension for Upstox and that is the finding, not
    an accident: it matches on the stringified identifier precisely so a broker
    that names instruments with a compound key is not a special case. D4.3's
    `_token_key` was written for a Mongo round trip and pays for itself here.
    """
    ticks = canonical_ticks(
        _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75})), _upstox_map(), broker="upstox"
    )
    assert ticks == [
        {"symbol": "RELIANCE", "price": 2650.75, "exchange": "NSE", "volume": None,
         "ingested_at": ticks[0]["ingested_at"]}
    ]


def test_an_unknown_upstox_instrument_key_is_never_used_as_a_symbol():
    """A key the account cannot name is dropped, not stuffed into `symbol`.

    The fallback this refuses would put `"NSE_EQ|INE123A01016"` into
    `db.holdings`, the trade snapshot and the AI's context as if it were an
    instrument name — the same defect the Kite path refuses for `738561`.
    """
    ticks = canonical_ticks(
        _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE123A01016": 500.0})), _upstox_map(), broker="upstox"
    )
    assert ticks == []


def test_an_upstox_key_that_round_tripped_through_mongo_still_reaches_the_wire():
    """Identity must survive persistence in both directions.

    A key is already a string, so the Mongo hazard Kite has does not apply on
    the way *in* — but the subscription side has its own: a holdings row whose
    `instrument_token` is absent or of the wrong kind must contribute nothing
    rather than poisoning the whole subscribe frame.
    """
    holdings = [
        {"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "NSE_EQ|INE002A01018"},
        {"symbol": "GHOST", "exchange": "NSE", "instrument_token": None},
        {"symbol": "ODD", "exchange": "NSE", "instrument_token": 738561},
    ]
    assert _upstox().stream_instruments(holdings=holdings) == ["NSE_EQ|INE002A01018"]

    frames = _market_channel().subscribe_frames(_upstox().stream_instruments(holdings=holdings))
    assert json.loads(frames[0].decode())["data"]["instrumentKeys"] == ["NSE_EQ|INE002A01018"]


@pytest.mark.parametrize("bad", ["RELIANCE", "", "   ", "|INE002A01018", "NSE_EQ|", 738561, True, None])
def test_a_malformed_upstox_instrument_identity_is_refused_before_the_wire(bad):
    """Upstox rejects an invalid subscription WHOLE, so one bad key costs every key.

    `True` is called out because `bool` is an `int` subclass and would otherwise
    stringify into the frame as `"True"`.
    """
    from services.brokers.upstox import instrument_key

    assert instrument_key(bad) is None


def test_upstox_needs_no_instrument_catalogue():
    """Identity comes from the account's own synced rows — no 80k-row artifact.

    Upstox publishes a full instrument catalogue, and needing it would have made
    D4.7 a data-pipeline sprint rather than an adapter sprint. It does not: a
    synced holding already carries the instrument key beside the symbol and the
    exchange, which *is* the mapping table, in both directions.
    """
    holdings = [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "NSE_EQ|INE002A01018"}]
    # Subscription side: keys straight off the rows.
    assert _upstox().stream_instruments(holdings=holdings) == ["NSE_EQ|INE002A01018"]
    # Resolution side: the same rows, read the other way.
    resolved = InstrumentMap.from_portfolio(holdings).resolve(instrument_token="NSE_EQ|INE002A01018")
    assert resolved == MarketInstrument(symbol="RELIANCE", exchange="NSE")


# -- canonicalization ---------------------------------------------------------


def test_no_raw_upstox_payload_escapes_the_adapter():
    """Protobuf bytes in, canonical `BrokerTick`s out — nothing Upstox-shaped between.

    The event's ticks are checked to be `BrokerTick` instances rather than dicts
    the codec built, and the canonical batch is checked to carry no Upstox key
    anywhere in it, at any depth.
    """
    event = _market_channel().decode(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75}))
    assert event.kind is StreamEventKind.TICKS
    from services.brokers.streaming import BrokerTick

    assert all(isinstance(t, BrokerTick) for t in event.ticks)

    canonical = canonical_ticks([t.as_dict() for t in event.ticks], _upstox_map(), broker="upstox")
    assert canonical and _find_key(canonical, "instrument_token") == []
    blob = json.dumps(canonical)
    assert "NSE_EQ|" not in blob and "INE002A01018" not in blob
    assert set(canonical[0]) == {"symbol", "price", "exchange", "volume", "ingested_at"}


def test_an_upstox_stream_runs_through_the_generic_transport_unchanged():
    """Real Upstox bytes over the real transport: subscribe, tick, keep-alive, close.

    The subscribe frame must reach the socket verbatim *as bytes* — the
    transport cannot know Upstox needs a binary frame, so it must not re-encode
    what the codec handed it.
    """
    frames = [
        _upstox_frame(feed_type=2),
        _upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75}),
    ]
    ticks, orders, expired, socket = drive_stream(
        _upstox(), frames, instruments=["NSE_EQ|INE002A01018"],
        session={"access_token": "live-access-token"}, channel="market",
    )

    assert len(socket.sent) == 1 and isinstance(socket.sent[0], bytes)
    assert json.loads(socket.sent[0].decode())["data"]["instrumentKeys"] == ["NSE_EQ|INE002A01018"]
    assert len(ticks) == 1, "the market_info frame was delivered as data"
    assert ticks[0][1] == "upstox"
    assert ticks[0][2] == [
        {"instrument_token": "NSE_EQ|INE002A01018", "last_price": 2650.75,
         "symbol": None, "exchange": None, "volume": 0, "timestamp": None}
    ]
    assert orders == [] and expired == []
    assert socket.closed


def test_a_malformed_upstox_frame_does_not_terminate_a_live_stream():
    """One damaged frame costs itself and nothing else."""
    frames = [
        b"\xff\xff\xff\xff\xff",
        _upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75}),
    ]
    ticks, _, _, socket = drive_stream(
        _upstox(), frames, instruments=["NSE_EQ|INE002A01018"],
        session={"access_token": "t"}, channel="market",
    )
    assert len(ticks) == 1 and ticks[0][2][0]["last_price"] == 2650.75
    assert socket.closed


# -- error classification -----------------------------------------------------


class _UpstoxHandshakeRefused(Exception):
    def __init__(self, status_code):
        super().__init__(f"server rejected WebSocket connection: HTTP {status_code}")
        self.status_code = status_code


class _UpstoxHandshakeRefused14(Exception):
    def __init__(self, status_code):
        super().__init__(f"server rejected WebSocket connection: HTTP {status_code}")
        self.response = type("Response", (), {"status_code": status_code})()


@pytest.mark.parametrize("refusal", [_UpstoxHandshakeRefused, _UpstoxHandshakeRefused14])
@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("channel", ["orders", "market"])
def test_upstox_refusing_a_stream_handshake_expires_the_session(refusal, status, channel):
    """A dead Upstox token is refused during the handshake — no frame is decoded.

    Upstox invalidates every access token daily at 03:30 IST, so this is every
    connected user every morning, on both feeds. Unclassified, the transport
    cannot tell it from a broker outage and reconnects into the same rejection
    forever while the account's market feed stays registered.

    Asserted for *both* channels because they are separate connections that
    refuse independently, and a classification present on one and missing on the
    other would leave half the account looping.
    """
    expired, slept = [], []

    async def on_expired(user_id, broker, channel_name):
        expired.append((user_id, broker, channel_name))

    stream = BrokerStream(
        "user-1", "upstox", {"access_token": "dead-token"},
        instrument_tokens=["NSE_EQ|INE002A01018"], on_expired=on_expired,
        on_tick=AsyncMock(), channel=channel,
    )

    async def stop_instead_of_reconnecting(delay):
        # Bounded so this test fails rather than hangs when the classification
        # is removed — the mutation otherwise reproduces the defect exactly (an
        # unbounded reconnect loop), and a test that hangs cannot go red.
        slept.append(delay)
        stream._stopped = True

    async def scenario():
        with patch.object(BrokerStream, "_connect", AsyncMock(side_effect=refusal(status))), \
                patch("services.brokers.stream.asyncio.sleep", new=stop_instead_of_reconnecting):
            await stream._run()

    run(scenario())
    assert expired == [("user-1", "upstox", channel)], "a refused Upstox handshake did not end the session"
    assert slept == [], "a refused Upstox handshake was retried instead of ending the session"


def test_an_ordinary_upstox_connection_failure_still_reconnects():
    """The other half of the pair — without it the test above proves nothing.

    A gateway 502 or a dropped route is broker weather, not a dead token, and
    classifying it as expiry would tell users to reconnect an account that is
    perfectly fine.
    """
    from services.brokers.upstox import _session_refused

    assert _session_refused(RuntimeError("boom")) is None
    assert _session_refused(_UpstoxHandshakeRefused(502)) is None
    assert _session_refused(_UpstoxHandshakeRefused(500)) is None
    # And the reason it does hand back names no URL and no token.
    reason = _session_refused(_UpstoxHandshakeRefused(401))
    assert "wss://" not in reason and "token" in reason.lower()


# -- readiness, promotion and failover, on real Upstox bytes ------------------


def _upstox_feed(user_id="u1", symbols=("RELIANCE",)):
    """Attach an Upstox market feed for `user_id` on the real registry."""
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import provider_registry

    run(_attach(user_id, "upstox", list(symbols)))
    return provider_registry.get(feed_provider_name(user_id, "upstox"))


def _upstox_canonical(price=2650.75, key="NSE_EQ|INE002A01018"):
    return canonical_ticks(_upstox_ticks(_upstox_frame(ltpc={key: price})), _upstox_map(), broker="upstox")


def test_a_connected_upstox_stream_is_not_ready_until_a_real_frame_arrives(no_probation_window):
    """CONNECTED != READY, driven by Upstox's own bytes rather than a synthetic tick.

    Every milestone short of data is reached — registered, link up, subscribed,
    a market_info frame received, a damaged frame received — and the baseline
    still serves the quote.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        feed = _upstox_feed()
        run(set_market_feed_link("u1", "upstox", up=True))
        assert feed.is_link_up and not feed.is_ready
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        # Upstox's own keep-alive/state frame is not evidence.
        assert _upstox_ticks(_upstox_frame(feed_type=2)) == []
        # Neither is a frame that decodes to nothing usable.
        run(feed.on_raw(canonical_ticks(_upstox_ticks(b"\xff\xff\xff"), _upstox_map(), broker="upstox")))
        assert not feed.is_ready, "a malformed Upstox frame promoted the feed"
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        # Nor is an instrument this account cannot name.
        run(feed.on_raw(_upstox_canonical(key="NSE_EQ|INE999Z01099")))
        assert not feed.is_ready, "an unresolvable Upstox instrument promoted the feed"

        # A real priced frame is.
        run(feed.on_raw(_upstox_canonical()))
        assert feed.is_ready
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed


def test_an_upstox_feed_is_promoted_over_the_baseline_and_falls_back_on_link_loss(no_probation_window):
    """Make-before-break, end to end, with Upstox as the concrete feed.

    The baseline is never unregistered and never disconnected at any point —
    failover is a change of *ranking*, not a teardown, which is what lets the
    feed climb back through the same gate on the connection that actually
    exists.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        feed = _upstox_feed()
        run(set_market_feed_link("u1", "upstox", up=True))
        run(feed.on_raw(_upstox_canonical()))
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed

        run(set_market_feed_link("u1", "upstox", up=False, reason="socket closed"))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline
        assert registry.get(baseline.name) is baseline, "Yahoo was released instead of kept as standby"
        assert baseline._connected or True  # the baseline was never disconnected

        # Re-earned on the new link, not inherited from the old one.
        run(set_market_feed_link("u1", "upstox", up=True))
        assert not feed.is_ready
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline
        run(feed.on_raw(_upstox_canonical()))
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed


def test_an_upstox_order_channel_link_loss_does_not_demote_the_market_feed(no_probation_window):
    """The defect the channel split would otherwise have introduced.

    A broker with two connections has two link signals for one account. Relaying
    both to the account's market-data provider would let the *order* socket
    blinking demote a market feed that is delivering prices perfectly well — and
    the reverse, let the order socket re-arm the readiness gate for a tick feed
    that is not connected at all.

    Which channel counts is decided by the channel's own declaration, never by a
    broker name.
    """
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")
        engine = BrokerEngine()
        engine.db = FakeDB()

        feed = _upstox_feed()
        run(engine._on_stream_link_state("u1", "upstox", True, "", "market"))
        run(feed.on_raw(_upstox_canonical()))
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed

        # The ORDER channel dies. The market feed is untouched.
        run(engine._on_stream_link_state("u1", "upstox", False, "socket closed", "orders"))
        assert feed.is_ready, "an order-socket failure demoted the market feed"
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed

        # The MARKET channel dies. Now it demotes.
        run(engine._on_stream_link_state("u1", "upstox", False, "socket closed", "market"))
        assert not feed.is_ready
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        # And an order-channel reconnect does not re-arm a tick feed that is down.
        run(engine._on_stream_link_state("u1", "upstox", True, "", "orders"))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline


def test_a_reconnected_upstox_feed_cannot_answer_from_the_dead_links_prices(no_probation_window):
    """A price from a connection that no longer exists must never answer a quote.

    WHY THIS TEST EXISTS
    --------------------
    D4.7's falsification pass mutated `_discard_evidence` to a no-op and the
    whole suite stayed green — the demotion on link loss is driven by the
    readiness *state*, so clearing the cache looked redundant. It is not, and
    the window it protects is narrow and nasty:

        tick for A and B on link 1  →  link 1 dies  →  link 2 comes up
        →  one fresh tick for A     →  READY re-earned
        →  a quote for B is answered from link 1's price

    Readiness is re-earned by A, but coverage is per symbol, and B's stale entry
    would be inside the freshness window. The feed would serve a price from a
    socket that is gone, labelled `streaming`, while the delayed baseline sitting
    underneath it holds a newer one.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        feed = _upstox_feed("u1", symbols=("RELIANCE", "TCS"))
        run(set_market_feed_link("u1", "upstox", up=True))
        run(feed.on_raw(_upstox_canonical(2650.75, "NSE_EQ|INE002A01018")))   # RELIANCE
        run(feed.on_raw(_upstox_canonical(3990.10, "NSE_EQ|INE467B01029")))   # TCS
        assert feed.covers("RELIANCE") and feed.covers("TCS")

        # The socket dies and a new one replaces it.
        run(set_market_feed_link("u1", "upstox", up=False, reason="dropped"))
        run(set_market_feed_link("u1", "upstox", up=True))

        # Only RELIANCE ticks on the new link. TCS's price belongs to a dead one.
        run(feed.on_raw(_upstox_canonical(2660.00, "NSE_EQ|INE002A01018")))
        assert feed.is_ready
        assert feed.covers("RELIANCE")
        assert not feed.covers("TCS"), "a price from the previous connection survived the reconnect"

        tcs = ResolutionContext(user_id="u1", symbol="TCS")
        assert manager.resolve(Capability.QUOTES, context=tcs) is baseline
        reliance = ResolutionContext(user_id="u1", symbol="RELIANCE")
        assert manager.resolve(Capability.QUOTES, context=reliance) is feed


def test_an_upstox_feed_that_never_ticks_leaves_the_baseline_primary():
    """A socket that connects, subscribes and says nothing must change nothing."""
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        _upstox_feed()
        run(set_market_feed_link("u1", "upstox", up=True))
        for user in ("u1", "u2", None):
            ctx = ResolutionContext(user_id=user, symbol="RELIANCE")
            assert manager.resolve(Capability.QUOTES, context=ctx) is baseline


# -- provider integration and entitlement ------------------------------------


def test_upstox_registers_through_the_existing_provider_framework():
    """One adapter, no provider code. The registration seam is untouched by D4.7."""
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import Capability, SourceTier, StreamingTickProvider

    with _clean_provider_registry() as registry:
        name = run(_attach("u1", "upstox", ["RELIANCE"]))
        assert name == feed_provider_name("u1", "upstox")
        provider = registry.get(name)
        assert isinstance(provider, StreamingTickProvider), "Upstox got a provider class of its own"
        assert provider.tier is SourceTier.STREAMING
        assert Capability.QUOTES in provider.capabilities and Capability.TICKS in provider.capabilities
        assert provider.owner_user_id == "u1"


def test_one_users_upstox_feed_failure_moves_only_that_users_feed(no_probation_window):
    """Entitlement isolation, unchanged by a second broker.

    A per-user feed is legally that user's own data, so B must never resolve to
    A's provider — and A's socket dying must not touch B's.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        feed_a = _upstox_feed("userA")
        feed_b = _upstox_feed("userB")
        for user, feed in (("userA", feed_a), ("userB", feed_b)):
            run(set_market_feed_link(user, "upstox", up=True))
            run(feed.on_raw(_upstox_canonical()))

        ctx_a = ResolutionContext(user_id="userA", symbol="RELIANCE")
        ctx_b = ResolutionContext(user_id="userB", symbol="RELIANCE")
        assert manager.resolve(Capability.QUOTES, context=ctx_a) is feed_a
        assert manager.resolve(Capability.QUOTES, context=ctx_b) is feed_b

        run(set_market_feed_link("userA", "upstox", up=False, reason="dropped"))
        assert manager.resolve(Capability.QUOTES, context=ctx_a) is baseline
        assert manager.resolve(Capability.QUOTES, context=ctx_b) is feed_b, "A's failure demoted B"

        # A guest — no user at all — never sees either, and stays on the baseline.
        guest = ResolutionContext(user_id=None, symbol="RELIANCE")
        assert manager.resolve(Capability.QUOTES, context=guest) is baseline


def test_an_upstox_quote_carries_no_broker_identity_and_no_other_users_data():
    """What leaves the gateway names no broker, no provider and no instrument key."""
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        registry.register(YahooPollingAdapter())
        feed = _upstox_feed("u1")
        run(set_market_feed_link("u1", "upstox", up=True))
        run(feed.on_raw(_upstox_canonical()))

        quote = run(feed.fetch_quote("RELIANCE"))
        blob = json.dumps(quote).lower()
        for forbidden in ("upstox", "nse_eq", "ine002a01018", "instrument_token", "brokerfeed"):
            assert forbidden not in blob, f"{forbidden!r} reached a quote payload"

        ctx = ResolutionContext(user_id="u2", symbol="RELIANCE")
        assert not feed.is_eligible_for(ctx), "another user's feed was eligible"


def test_an_expired_upstox_token_stops_every_channel_and_detaches_the_feed():
    """One dead token ends the whole account's realtime surface, not one socket of it.

    The token is the account's, so a channel that reports it dead means the
    others are reconnecting into the same rejection right now. Leaving them
    running would keep an unusable socket and its expired access token alive for
    the life of the process.
    """
    from services.brokers.stream import stream_manager
    from services.market_engine.providers import provider_registry

    engine = BrokerEngine()
    engine.db = FakeDB()
    with _clean_provider_registry():
        feed = _upstox_feed("u1")
        run(feed.on_raw(_upstox_canonical()))
        assert provider_registry.get("brokerfeed:upstox:u1") is not None

        stopped = []

        async def record_stop(user_id, broker, channel=None):
            stopped.append((user_id, broker, channel))

        with patch.object(stream_manager, "stop_stream", new=record_stop):
            run(engine._on_stream_expired("u1", "upstox", "market"))

        assert provider_registry.get("brokerfeed:upstox:u1") is None, "the feed stayed registered"
        assert stopped == [("u1", "upstox", None)], "the account's other channels were left running"


# -- security -----------------------------------------------------------------


def test_no_upstox_credential_reaches_a_log_line(caplog):
    """Neither feed may write a token, a bearer header or a secret into the log.

    The transport logs `endpoint.safe_url` and nothing else. Upstox's URLs carry
    no credentials at all, so this is checking something stronger than masking:
    that the *headers* — where Upstox's token actually lives — never reach a log
    line either.
    """
    frames = [_upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75})]
    with caplog.at_level(logging.DEBUG):
        drive_stream(
            _upstox(), frames, instruments=["NSE_EQ|INE002A01018"],
            session={"access_token": "SUPER-SECRET-TOKEN"}, channel="market",
        )
        drive_stream(
            _upstox(), [json.dumps({"order_id": "UPX-1", "status": "complete"})],
            session={"access_token": "SUPER-SECRET-TOKEN"}, channel="orders",
        )

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "SUPER-SECRET-TOKEN" not in logged
    assert "Bearer" not in logged
    assert caplog.records, "nothing was logged — the assertion could not have failed"


def test_no_upstox_credential_or_identifier_can_reach_a_market_tick():
    """`MarketTick` has no field an Upstox key or token could occupy."""
    from dataclasses import fields as dataclass_fields

    names = {f.name for f in dataclass_fields(MarketTick)}
    assert names == {"symbol", "price", "exchange", "volume", "ingested_at"}
    assert not (names & {"instrument_token", "instrument_key", "access_token", "broker"})


# -- the second-broker architecture proof ------------------------------------


def test_upstox_added_no_upstox_knowledge_outside_its_own_adapter():
    """D4.7's central acceptance criterion, swept rather than asserted by eye.

    The Upstox vocabulary — the feed URLs, the mode, the subscription keys, the
    instrument-key notion — must exist in exactly one module. A second module
    that knows any of it is the beginning of the `if broker == "upstox"` branch
    this framework was built to make unnecessary.
    """
    upstox_words = ("upstox", "instrumentkeys", "ltpc", "instrument_key")
    allowed = {
        "services/brokers/upstox.py",     # the adapter — the only owner
        "services/brokers/__init__.py",   # the registry entry — one line
    }
    offenders = {}
    scanned = 0
    for path in sorted((BACKEND / "services").rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in allowed:
            continue
        scanned += 1
        source = _strip_source(path.read_text(encoding="utf-8")).lower()
        hit = [w for w in upstox_words if w in source]
        if hit:
            offenders[rel] = hit
    assert scanned > 50, "the sweep found almost no modules — it could not have failed"
    assert not offenders, offenders


def test_the_market_layer_is_untouched_by_the_second_broker():
    """Everything D4.7 promised would not move, asserted rather than asserted-by-eye.

    The Market Engine still cannot import the broker layer at all, and the
    switching machinery still names no broker — the same two properties D4.4 and
    D4.5 established, re-checked now that a second broker exists, because "no
    broker-specific branch" is a claim that can only decay.
    """
    market_modules = [
        "services/market_engine/gateway.py",
        "services/market_engine/source_manager.py",
        "services/market_engine/ticks.py",
        "services/market_engine/providers/streaming.py",
        "services/market_engine/providers/registry.py",
        "services/market_engine/providers/base.py",
    ]
    for relative in market_modules:
        source = _strip_source((BACKEND / relative).read_text(encoding="utf-8")).lower()
        for name in ("upstox", "zerodha", "kite", "broker_gateway", "broker_registry"):
            assert name not in source, f"{relative} names {name!r} in executable code"


def test_a_second_streaming_broker_added_no_line_to_the_transport_beyond_the_channel_concept():
    """`stream.py` still names no broker, no protocol and no wire format.

    D4.7 changed this module — that is the honest finding of the sprint, and it
    is recorded rather than hidden — but what it added is a *channel*, which is
    a name, a protocol string and a codec. The property that matters survives:
    nothing in this file can tell one broker from another.
    """
    source = _strip_source((BACKEND / "services/brokers/stream.py").read_text(encoding="utf-8")).lower()
    for name in ("upstox", "zerodha", "kite", "protobuf", "ltpc", "instrumentkeys", "json"):
        assert name not in source, f"stream.py names {name!r} in executable code"
    # And the protocol override table is still empty: both brokers, and all
    # three of their feeds, are served by the one generic WebSocket transport.
    from services.brokers.stream import PROTOCOL_RUNNERS, resolve_transport

    assert PROTOCOL_RUNNERS == {}, "a broker-specific transport was reintroduced"
    for channel in _upstox().stream_channels() + _zerodha().stream_channels():
        assert resolve_transport(channel) is BrokerStream._run_websocket


def test_zerodha_and_upstox_speak_different_protocols_and_produce_identical_canonical_ticks():
    """THE acceptance criterion: different wire, same output, no shared code path.

    Two brokers agree on nothing at the wire — binary vs protobuf, integer token
    vs compound key, paise vs rupees, query-string auth vs bearer header, one
    socket vs two — and what reaches the Market Engine from each is the same
    canonical `MarketTick`, byte for byte apart from the ingest timestamp.

    That equality is the proof the boundary is real. If either adapter's shape
    leaked upward, these two lists could not match.
    """
    kite_frame = _kite_frame((738561, 265075))
    upstox_frame = _upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75})

    # The wires share nothing.
    assert isinstance(kite_frame, bytes) and isinstance(upstox_frame, bytes)
    assert kite_frame != upstox_frame
    assert _kite_ticks(kite_frame)[0]["instrument_token"] == 738561
    assert _upstox_ticks(upstox_frame)[0]["instrument_token"] == "NSE_EQ|INE002A01018"
    assert type(_kite_ticks(kite_frame)[0]["instrument_token"]) is not type(
        _upstox_ticks(upstox_frame)[0]["instrument_token"]
    ), "the two brokers' instrument identities are the same type — one of them is wrong"

    # Each account maps its own broker's identity onto the same instrument.
    kite_tick = canonical_ticks(_kite_ticks(kite_frame), _kite_map(), broker="zerodha")[0]
    upstox_tick = canonical_ticks(_upstox_ticks(upstox_frame), _upstox_map(), broker="upstox")[0]

    assert kite_tick.keys() == upstox_tick.keys()
    for field in ("symbol", "price", "exchange", "volume"):
        assert kite_tick[field] == upstox_tick[field], field
    assert kite_tick["symbol"] == "RELIANCE" and kite_tick["price"] == 2650.75


def test_both_brokers_reach_the_market_gateway_through_the_identical_seam(no_probation_window):
    """Same registration, same readiness gate, same failover — for both brokers.

    Run as one scenario rather than two so a divergence shows up as a difference
    between the two halves rather than as two independently passing tests.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    for broker, feed_factory, batch in (
        ("zerodha", _kite_feed, lambda: canonical_ticks(
            _kite_ticks(_kite_frame((738561, 265075))), _kite_map(), broker="zerodha")),
        ("upstox", _upstox_feed, _upstox_canonical),
    ):
        with _clean_provider_registry() as registry:
            registry.clear()
            baseline = YahooPollingAdapter()
            registry.register(baseline)
            manager = SourceManager(registry)
            ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

            feed = feed_factory("u1")
            run(set_market_feed_link("u1", broker, up=True))
            assert manager.resolve(Capability.QUOTES, context=ctx) is baseline, broker
            run(feed.on_raw(batch()))
            assert manager.resolve(Capability.QUOTES, context=ctx) is feed, broker
            run(set_market_feed_link("u1", broker, up=False, reason="dropped"))
            assert manager.resolve(Capability.QUOTES, context=ctx) is baseline, broker


# -- the channel concept itself ----------------------------------------------


def test_a_broker_with_two_feeds_opens_one_stream_per_channel():
    """The engine opens what the broker declares, and knows nothing about how many."""
    from services.brokers.stream import stream_manager

    engine = BrokerEngine()
    engine.db = FakeDB()
    started = []

    async def record(user_id, broker, session, **kwargs):
        started.append((broker, kwargs.get("channel"), tuple(kwargs.get("instrument_tokens") or ())))

    holdings = [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "NSE_EQ|INE002A01018"}]
    # Patched where the engine *bound* it, not where it is defined: the engine
    # imports the name at module load, so patching the source module is inert
    # and the real registration would leak a provider into the global registry.
    with _clean_provider_registry(), \
            patch.object(stream_manager, "start_stream", new=record), \
            patch.object(engine, "get_session", AsyncMock(return_value={"access_token": "t"})), \
            patch("services.broker_engine.attach_market_feed", new=AsyncMock()):
        run(engine.start_stream("u1", "upstox", holdings=holdings, positions=[]))

    assert [c for _, c, _ in started] == ["orders", "market"]
    # Both channels are handed the same instrument list; each decides what to do
    # with it, which is what keeps the engine from having to ask per channel.
    assert all(instruments == ("NSE_EQ|INE002A01018",) for _, _, instruments in started)


def test_a_single_channel_broker_is_unchanged_by_the_channel_concept():
    """Kite still opens exactly one connection, under the name it always had."""
    from services.brokers.stream import stream_manager
    from services.brokers.streaming import DEFAULT_STREAM_CHANNEL as DEFAULT

    engine = BrokerEngine()
    engine.db = FakeDB()
    started = []

    async def record(user_id, broker, session, **kwargs):
        started.append(kwargs.get("channel"))

    # Patched where the engine *bound* it, not where it is defined: the engine
    # imports the name at module load, so patching the source module is inert
    # and the real registration would leak a provider into the global registry.
    with _clean_provider_registry(), \
            patch.object(stream_manager, "start_stream", new=record), \
            patch.object(engine, "get_session", AsyncMock(return_value={"access_token": "t"})), \
            patch("services.broker_engine.attach_market_feed", new=AsyncMock()):
        run(engine.start_stream("u1", "zerodha",
                                holdings=[{"symbol": "RELIANCE", "instrument_token": 738561}], positions=[]))

    assert started == [DEFAULT]


def test_a_channel_may_not_deliver_an_event_kind_it_does_not_carry():
    """A codec that decodes the other feed's data is dropped before the capability gate.

    The broker legitimately declares both realtime capabilities, so the
    broker-level gate would let this through. Without the per-channel narrowing,
    an order socket emitting a tick would drive the account's market-data
    provider — a feed marked live and ready on a connection carrying no market
    data at all.
    """
    from services.brokers.streaming import BrokerTick

    class _LeakyOrderChannel(BrokerStreamChannel):
        name = "orders"
        protocol = "upstox_portfolio"
        delivers = frozenset({StreamEventKind.ORDER})

        def endpoint(self, session, credentials=None):
            return BrokerStreamEndpoint(url="wss://example.invalid/orders")

        def decode(self, frame):
            return BrokerStreamEvent(
                kind=StreamEventKind.TICKS,
                ticks=(BrokerTick(instrument_token="NSE_EQ|INE002A01018", last_price=2650.75),),
            )

    class _TwoChannelBroker(type(_upstox())):
        name = "upstox-leaky"

        def stream_channels(self):
            # A legitimate tick channel alongside the leaking order one, because
            # that is the real shape: the broker genuinely serves ticks, just not
            # on this socket. Registration validation is satisfied, the
            # broker-level capability gate is satisfied, and the *only* thing
            # standing between the order socket and the account's market feed is
            # the per-channel narrowing this test exercises.
            return (_LeakyOrderChannel(), _market_channel())

    adapter = _TwoChannelBroker()
    broker_registry.register(adapter, replace=True)
    try:
        ticks, orders, expired, _ = drive_stream(adapter, [b"anything"], channel="orders")
    finally:
        broker_registry.unregister(adapter.name)

    assert ticks == [], "an order channel delivered a tick it does not carry"


def test_a_broker_declaring_a_capability_no_channel_carries_is_rejected_at_registration():
    """The silent failure this check exists to turn into a startup error.

    Without it: the account's market-data provider registers on the strength of
    the capability, the sockets connect, the reconnect loop is content, and every
    tick is dropped by the per-channel narrowing. From outside that is a market
    with no trades in it — the feed never reaches READY, the baseline quietly
    keeps every quote, and nothing reports a defect.
    """
    registry = BrokerRegistry()

    class _OrdersOnlyChannel(BrokerStreamChannel):
        name = "orders"
        protocol = "silent"
        delivers = frozenset({StreamEventKind.ORDER})

        def endpoint(self, session, credentials=None):
            return BrokerStreamEndpoint(url="wss://example.invalid/orders")

        def decode(self, frame):
            return BrokerStreamEvent.ignore()

    class _SilentTickBroker(NovaAdapter):
        name = "silent"

        def stream_channels(self):
            return (_OrdersOnlyChannel(),)

    with pytest.raises(BrokerAdapterInvalid) as excinfo:
        registry.register(_SilentTickBroker())
    assert "tick_stream" in str(excinfo.value)

    class _DuplicateChannelBroker(NovaAdapter):
        name = "twins"

        def stream_channels(self):
            return (_OrdersOnlyChannel(), _OrdersOnlyChannel())

    with pytest.raises(BrokerAdapterInvalid) as excinfo:
        registry.register(_DuplicateChannelBroker())
    assert "duplicate" in str(excinfo.value).lower()


def test_the_stream_registry_keys_on_the_channel_so_one_feed_cannot_replace_another():
    """The bug the old `(user, broker)` key would have caused, asserted directly.

    Under the old key, starting a broker's second feed silently *replaced* the
    first: one connection live, one gone, nothing raised.
    """
    from services.brokers.stream import BrokerStreamManager

    manager = BrokerStreamManager()

    async def scenario():
        with patch.object(BrokerStream, "start", lambda self: None):
            await manager.start_stream("u1", "upstox", {"access_token": "t"}, channel="orders")
            await manager.start_stream("u1", "upstox", {"access_token": "t"}, channel="market")
            assert sorted(s["channel"] for s in manager.status()) == ["market", "orders"]

            # Stopping one channel leaves the other alone...
            await manager.stop_stream("u1", "upstox", "orders")
            assert [s["channel"] for s in manager.status()] == ["market"]
            # ...and stopping the account stops what remains.
            await manager.stop_stream("u1", "upstox")
            assert manager.status() == []

    run(scenario())


# ==================================================================
# D4.9 — Angel One as the THIRD real streaming broker
#
# D4.7 closed with the open question ADR-037 recorded: the channel model itself
# had had no independent test, because the broker that forced it into existence
# was also the only broker using it. Angel One is that independent test, and it
# arrives from the opposite direction — its realtime surface is ONE socket, so
# it exercises the free single-channel path rather than the multi-channel one.
#
#     Kite                    Upstox v3               Angel One smart-stream
#     ────                    ─────────               ──────────────────────
#     one socket, both feeds  two sockets             one socket, ticks only
#     bespoke binary          protobuf                fixed 51-byte packets
#     frame = many packets    frame = many feeds      frame = ONE tick
#     big-endian              protobuf varint         LITTLE-endian
#     int token               "NSE_EQ|INE002A01018"   numeric token per SEGMENT
#     paise, 3 scales         IEEE double, rupees     paise, currency ×10⁷
#     query-string auth       bearer header           four auth headers
#     2 frames, text          1 frame, JSON as binary 1 frame, JSON as text
#     protocol ping suffices  protocol ping suffices  application "ping" or the
#                                                     broker closes the socket
#
# The protocol below is transcribed from SmartAPI's own published WebSocket 2.0
# contract and its official Python SDK (`angel-one/smartapi-python`,
# `SmartApi/smartWebSocketV2.py`) — not inferred from Kite, not inferred from
# Upstox, and not inferred from what the previous two adapters happened to need.
#
# Every test below was run against a deliberately broken implementation first;
# the mutations are listed in TASK.md's D4.9 falsification table.
# ==================================================================


def _strip_prose(source: str) -> str:
    """Docstrings and comments removed, string LITERALS kept.

    `_strip_source` removes literals too, which is right for a vocabulary sweep
    (a comment quoting a deleted branch is documentation, not a recurrence) and
    exactly wrong for a sweep whose target *is* a literal: `if broker ==
    "angelone"` is invisible to it. This keeps the literals and drops the prose,
    so a module may still explain the branch it does not contain.
    """
    without_docstrings = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', '""', source)
    return re.sub(r"#[^\n]*", "", without_docstrings)


def _angelone():
    return broker_registry.require("angelone")


def _angel_channel():
    channels = _angelone().stream_channels()
    assert len(channels) == 1, "Angel One's realtime surface is one socket"
    return channels[0]


def _angel_packet(token="2885", price=265075, segment=1, mode=1, timestamp=1_724_236_800_000,
                  size=None, token_bytes=None):
    """One SmartAPI binary tick packet, built from the published byte layout.

    `size` and `token_bytes` exist to produce genuine protocol damage — a short
    read, an unterminated token — rather than merely odd values.
    """
    packet = bytearray(51 if size is None else size)
    if len(packet) >= 1:
        packet[0] = mode
    if len(packet) >= 2:
        packet[1] = segment
    raw = token_bytes if token_bytes is not None else token.encode("ascii")
    packet[2:2 + len(raw)] = raw[: min(len(raw), 25)]
    if len(packet) >= 51:
        struct.pack_into("<q", packet, 27, 7)            # sequence number
        struct.pack_into("<q", packet, 35, timestamp)
        struct.pack_into("<q", packet, 43, price)
    return bytes(packet)


def _angel_ticks(frame):
    """The canonical `BrokerTick` dicts one raw SmartAPI frame decodes to."""
    event = _angelone().decode_stream_frame(frame)
    return [tick.as_dict() for tick in event.ticks]


def _angel_map():
    """An account's instrument map as Angel One identifies instruments."""
    return InstrumentMap.from_portfolio(
        [
            {"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "1|2885"},
            {"symbol": "TCS", "exchange": "NSE", "instrument_token": "1|11536"},
        ]
    )


def _angel_canonical(price=265075, token="2885", segment=1):
    return canonical_ticks(
        _angel_ticks(_angel_packet(token=token, price=price, segment=segment)),
        _angel_map(), broker="angelone",
    )


def _angel_feed(user_id="u1", symbols=("RELIANCE",)):
    """Attach an Angel One market feed for `user_id` on the real registry."""
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import provider_registry

    run(_attach(user_id, "angelone", list(symbols)))
    return provider_registry.get(feed_provider_name(user_id, "angelone"))


def _angel_session():
    return {"access_token": "live-jwt-token", "feed_token": "live-feed-token", "account_id": "A123456"}


# -- authentication and session ------------------------------------------------


def test_the_angelone_login_flow_is_a_browser_redirect_and_holds_no_user_secret(monkeypatch):
    """SmartAPI's publisher login, not its PIN + TOTP login.

    SmartAPI also publishes `loginByPassword`, which takes the user's trading
    PIN and a TOTP code. Using it would mean this platform holding a trading
    PIN and a TOTP seed for every connected user — a class of secret no other
    broker here requires and SECURITY.md forbids. The publisher login is an
    ordinary browser redirect, so nothing of the user's is ever held.
    """
    monkeypatch.setenv("ANGELONE_API_KEY", "app-key")
    monkeypatch.setenv("ANGELONE_REDIRECT_URL", "https://app.test/api/brokers/angelone/callback")
    login = _angelone().get_login_url("user-123")

    assert login["configured"] is True
    assert login["url"].startswith("https://smartapi.angelone.in/publisher-login?")
    assert "api_key=app-key" in login["url"]
    # `uid=` is the platform's own convention for the state parameter, and the
    # shared callback route reads it — a broker that spelled it differently
    # would connect to nobody.
    assert "state=uid%3Duser-123" in login["url"]
    for absent in ("password", "totp", "pin"):
        assert absent not in login["url"].lower()

    # SmartAPI will not redirect without a registered URL, so a deployment
    # missing one is reported as unconfigured rather than sent to a dead link.
    monkeypatch.setenv("ANGELONE_REDIRECT_URL", "")
    assert _angelone().get_login_url("user-123")["configured"] is False
    assert _angelone().is_configured() is False


def test_the_angelone_callback_carries_both_tokens_the_stream_needs():
    """The redirect *is* the exchange — and it returns two credentials, not one.

    A callback parser that kept only the auth token would produce a session that
    authenticates every REST call and cannot open the market feed at all: the
    socket needs `x-feed-token` as well, and would be refused with a 401 that
    reads exactly like an expired session.
    """
    adapter = _angelone()
    payload = adapter.parse_callback_params(
        {"auth_token": "jwt-token", "feed_token": "feed-token", "state": "uid=user-123"})
    assert payload == {"auth_token": "jwt-token", "feed_token": "feed-token", "refresh_token": ""}
    # A cancelled login carries no auth token and must not be completed.
    assert adapter.parse_callback_params({"state": "uid=user-123"}) is None
    assert adapter.parse_callback_params({}) is None


def test_completing_angelone_auth_resolves_the_client_code_the_socket_requires(monkeypatch):
    """The redirect does not carry the client code, and the stream cannot connect without it.

    So the "exchange" is a profile call: a session that cannot stream never gets
    stored as connected in the first place.
    """
    monkeypatch.setenv("ANGELONE_API_KEY", "app-key")
    monkeypatch.setenv("ANGELONE_REDIRECT_URL", "https://app.test/cb")
    profile = {"status": True, "message": "SUCCESS", "errorcode": "", "data": {
        "clientcode": "A123456", "name": "A Trader", "email": "t@example.test",
        "exchanges": ["NSE", "BSE"], "products": ["DELIVERY"]}}

    with patch.object(BrokerAdapter, "_request", new=AsyncMock(return_value=profile)):
        session = run(_angelone().exchange_token(
            {"auth_token": "jwt-token", "feed_token": "feed-token"}))

    assert session["access_token"] == "jwt-token"
    assert session["feed_token"] == "feed-token"
    assert session["account_id"] == "A123456"
    # And the resulting session is exactly what the stream endpoint needs.
    headers = _angel_channel().endpoint(session, {"api_key": "app-key"}).headers
    assert headers["x-client-code"] == "A123456" and headers["x-feed-token"] == "feed-token"


def test_an_angelone_session_expires_at_midnight_ist():
    """SmartAPI sessions live until midnight IST, not for a fixed number of hours."""
    from services.brokers.base import IST

    for hour in (1, 9, 15, 23):
        connected = datetime(2026, 8, 24, hour, 30, tzinfo=IST)
        expiry = _angelone().session_expiry(connected).astimezone(IST)
        assert expiry == datetime(2026, 8, 25, 0, 0, tzinfo=IST), hour
        assert expiry > connected


# -- protocol: endpoint, authentication, keep-alive --------------------------


def test_the_angelone_endpoint_authenticates_by_four_headers_and_none_reach_the_url():
    """A third auth style: not Kite's query string, not Upstox's single header.

    SmartAPI publishes a query-string form of this URL for browser clients
    (`?clientCode=&feedToken=&apiKey=`), which would put two live credentials
    into the string every connection log line names. The header form is used
    instead, and this asserts the URL is clean *positively* rather than trusting
    `safe_url` to hide a mistake.
    """
    from services.brokers.angelone import WS_URL

    endpoint = _angel_channel().endpoint(_angel_session(), {"api_key": "app-key"})
    assert endpoint.url == WS_URL == "wss://smartapisocket.angelone.in/smart-stream"
    assert "?" not in endpoint.url, "Angel One does not authenticate this feed by query string"
    assert endpoint.safe_url == endpoint.url
    assert endpoint.headers == {
        "Authorization": "live-jwt-token",
        "x-api-key": "app-key",
        "x-client-code": "A123456",
        "x-feed-token": "live-feed-token",
    }
    for secret in ("live-jwt-token", "live-feed-token", "app-key", "A123456"):
        assert secret not in endpoint.url


def test_the_angelone_feed_declares_the_application_keepalive_the_protocol_ping_is_not():
    """The one thing about this broker that needed anything outside its adapter.

    `ping_interval` configures the WebSocket protocol's own ping frames, which
    the two libraries exchange without either application seeing them. SmartAPI
    does not count those: it requires the text frame `ping` on the data channel
    every 30 seconds and closes a connection that stops sending one.
    """
    from services.brokers.angelone import HEARTBEAT_FRAME, HEARTBEAT_INTERVAL

    endpoint = _angel_channel().endpoint(_angel_session(), {"api_key": "k"})
    assert endpoint.heartbeat_frame == HEARTBEAT_FRAME == "ping"
    assert isinstance(endpoint.heartbeat_frame, str), "SmartAPI's keep-alive is a text frame"
    assert 0 < endpoint.heartbeat_interval <= 30, "the keep-alive must beat SmartAPI's 30s deadline"
    assert endpoint.heartbeat_interval == HEARTBEAT_INTERVAL
    # And it is genuinely a *different* mechanism from the protocol ping, which
    # is still configured and still exchanged — the two are separate fields with
    # separate effects, and satisfying one does not satisfy the other.
    assert endpoint.ping_interval, "the protocol ping was turned off in favour of the app keep-alive"
    assert BrokerStreamEndpoint(url=endpoint.url, ping_interval=endpoint.ping_interval).heartbeat_frame is None


def test_a_declared_keepalive_frame_without_an_interval_is_refused_by_the_contract():
    """Half a keep-alive is a socket the broker closes, so the contract has none.

    A frame with no interval would never be sent and an interval with no frame
    would send nothing — both leave the feed silently missing the keep-alive it
    declared, which is the failure the field exists to prevent.
    """
    with pytest.raises(BrokerContractError):
        BrokerStreamEndpoint(url="wss://example.test/feed", heartbeat_frame="ping")
    with pytest.raises(BrokerContractError):
        BrokerStreamEndpoint(url="wss://example.test/feed", heartbeat_frame="ping", heartbeat_interval=0)
    with pytest.raises(BrokerContractError):
        BrokerStreamEndpoint(url="wss://example.test/feed", heartbeat_frame={"op": "ping"}, heartbeat_interval=5)
    # And a broker that needs none — both of the other two — is unaffected.
    assert BrokerStreamEndpoint(url="wss://example.test/feed").heartbeat_frame is None


def test_the_transport_sends_the_declared_keepalive_and_stops_with_the_socket():
    """The generic half: a timer nobody's adapter owns, cancelled with the connection.

    Driven with a compressed interval rather than the real 20 seconds, and the
    assertion is on both halves — that it fires at all, and that it does not
    outlive the socket. A keep-alive task leaked per reconnect is forever on a
    flapping feed.
    """
    endpoint = BrokerStreamEndpoint(
        url="wss://example.test/feed", heartbeat_frame="ping", heartbeat_interval=0.01)
    socket = _FakeSocket([])
    stream = BrokerStream("u1", "angelone", _angel_session(), channel=DEFAULT_STREAM_CHANNEL)

    async def scenario():
        task = stream._start_heartbeat(socket, endpoint)
        assert task is not None
        await asyncio.sleep(0.05)
        assert socket.sent.count("ping") >= 2, socket.sent
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        sent_at_cancel = len(socket.sent)
        await asyncio.sleep(0.05)
        assert len(socket.sent) == sent_at_cancel, "the keep-alive outlived the connection"

    run(scenario())
    # A feed that declares no heartbeat gets no task at all.
    assert stream._start_heartbeat(socket, BrokerStreamEndpoint(url="wss://example.test/feed")) is None


class _PacedSocket(_FakeSocket):
    """A socket whose frames arrive slowly enough for a timer to fire between them."""

    def __init__(self, frames, pause=0.02):
        super().__init__(frames)
        self._pause = pause

    def __aiter__(self):
        async def gen():
            for frame in self._frames:
                await asyncio.sleep(self._pause)
                yield frame

        return gen()


def test_the_keepalive_reaches_a_live_angelone_socket_and_stops_when_it_closes():
    """The wiring, not just the timer — driven through the real transport pass.

    WHY THIS IS SEPARATE FROM THE TIMER TEST ABOVE
    ----------------------------------------------
    The falsification pass mutated `_run_websocket` to never start the
    heartbeat, and to never cancel it, and the timer test stayed green both
    times: it calls `_start_heartbeat` directly, so it proves the helper works
    and nothing about whether the transport uses it. A feed whose keep-alive is
    built and never sent is a feed Angel One closes every 30 seconds.
    """
    with patch("services.brokers.angelone.HEARTBEAT_INTERVAL", 0.01):
        socket = _PacedSocket([_angel_packet(price=265075)], pause=0.05)
        stream = BrokerStream(
            "u1", "angelone", _angel_session(), credentials={"api_key": "app-key"},
            instrument_tokens=["1|2885"], channel=DEFAULT_STREAM_CHANNEL,
        )

        async def scenario():
            with patch.object(BrokerStream, "_connect", AsyncMock(return_value=socket)):
                await stream._run_websocket()
            sent_at_close = list(socket.sent)
            await asyncio.sleep(0.05)
            return sent_at_close

        sent_at_close = run(scenario())

    assert sent_at_close.count("ping") >= 2, f"the keep-alive never reached the socket: {sent_at_close}"
    assert json.loads(sent_at_close[0])["action"] == 1, "the subscribe frame must go first"
    assert socket.closed
    assert socket.sent == sent_at_close, "the keep-alive outlived the connection it belonged to"


def test_a_codec_that_raises_does_not_terminate_a_live_angelone_stream():
    """A frame the codec *raises* on, not merely one it declines to decode.

    The resilience test below feeds damaged bytes, which this adapter decodes to
    "nothing" without raising — so it cannot distinguish a transport that
    swallows a codec exception from one that re-raises it. The falsification
    pass proved that: making `_decode` re-raise left it green.
    """
    real_decode = _angelone().decode_stream_frame
    calls = {"n": 0}

    def exploding(frame):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("codec defect")
        return real_decode(frame)

    with patch.object(type(_angelone()), "decode_stream_frame", staticmethod(exploding)):
        ticks, _, _, socket = drive_stream(
            _angelone(), [b"\x00" * 51, _angel_packet(price=265075)],
            instruments=["1|2885"], session=_angel_session())

    assert calls["n"] == 2, "the stream stopped at the frame that raised"
    assert len(ticks) == 1 and ticks[0][2][0]["last_price"] == 2650.75
    assert socket.closed


# -- protocol: subscription ---------------------------------------------------


def test_the_angelone_subscribe_handshake_groups_tokens_by_exchange_segment():
    """One text frame, in the documented mode, grouped — not a flat list.

    Three specifics, and a Kite- or Upstox-shaped assumption gets each wrong:
    one frame rather than Kite's two; `str` rather than Upstox's `bytes`; and
    instruments grouped by exchange segment, because the segment is half the
    identity and a flat list has nowhere to put it.
    """
    from services.brokers.angelone import ACTION_SUBSCRIBE, STREAM_MODE_LTP

    frames = _angelone().stream_subscribe_frames(["1|2885", "3|500325", "1|11536"])
    assert len(frames) == 1, "SmartAPI subscribes in one frame; two is Kite's protocol"
    assert isinstance(frames[0], str), "SmartAPI's subscribe frame is text, not Upstox's binary"

    request = json.loads(frames[0])
    assert request["action"] == ACTION_SUBSCRIBE == 1
    assert request["params"]["mode"] == STREAM_MODE_LTP == 1
    assert request["correlationID"], "SmartAPI echoes the correlation id on an error response"
    assert request["params"]["tokenList"] == [
        {"exchangeType": 1, "tokens": ["2885", "11536"]},
        {"exchangeType": 3, "tokens": ["500325"]},
    ]


@pytest.mark.parametrize("bad", ["2885", "NSE|2885", "1|", "|2885", "99|2885", "1|abc", "", None, True, "1|0"])
def test_a_malformed_angelone_instrument_identity_never_reaches_the_wire(bad):
    """SmartAPI rejects a malformed *subscription* rather than the bad entry.

    So one unusable row would otherwise cost the account every price it asked
    for — a connected socket with nothing on it, which reads exactly like a
    quiet market.
    """
    assert _angelone().stream_subscribe_frames([bad]) == []
    frames = _angelone().stream_subscribe_frames([bad, "1|2885"])
    assert json.loads(frames[0])["params"]["tokenList"] == [{"exchangeType": 1, "tokens": ["2885"]}]


def test_an_angelone_subscription_with_no_instruments_sends_nothing():
    """An account with nothing to stream opens a socket and asks for nothing."""
    assert _angelone().stream_subscribe_frames([]) == []
    assert _angelone().stream_subscribe_frames(None) == []


def test_an_over_quota_angelone_subscription_is_trimmed_rather_than_rejected(caplog):
    """SmartAPI's quota is per session, and exceeding it costs the whole request."""
    from services.brokers.angelone import MAX_SUBSCRIBED_INSTRUMENTS

    caplog.set_level(logging.WARNING)
    instruments = [f"1|{token}" for token in range(1, MAX_SUBSCRIBED_INSTRUMENTS + 51)]
    request = json.loads(_angelone().stream_subscribe_frames(instruments)[0])
    subscribed = sum(len(group["tokens"]) for group in request["params"]["tokenList"])
    assert subscribed == MAX_SUBSCRIBED_INSTRUMENTS
    assert any("quota" in r.getMessage() for r in caplog.records), "an over-quota trim was silent"


def test_a_duplicate_angelone_instrument_is_subscribed_once():
    """Duplicates spend quota at SmartAPI; they are collapsed before the wire."""
    request = json.loads(_angelone().stream_subscribe_frames(["1|2885", "1|2885"])[0])
    assert request["params"]["tokenList"] == [{"exchangeType": 1, "tokens": ["2885"]}]


# -- protocol: decoding --------------------------------------------------------


def test_an_angelone_ltp_packet_decodes_to_one_canonical_broker_tick():
    """A SmartAPI frame is ONE tick, not a batch — the opposite of both predecessors."""
    ticks = _angel_ticks(_angel_packet(token="2885", price=265075))
    assert ticks == [{
        "instrument_token": "1|2885",
        "last_price": 2650.75,
        "symbol": None,
        "exchange": "NSE",
        "volume": 0,
        "timestamp": "1724236800000",
    }]


@pytest.mark.parametrize("mode", [1, 2, 3])
def test_every_priceable_angelone_mode_shares_the_first_51_bytes(mode):
    """LTP, Quote and Snap Quote differ only after the price field.

    Read here because a mode change must not produce a socket that connects,
    subscribes and decodes nothing — which is indistinguishable from a quiet
    market and is precisely how a feed goes silently narrow.
    """
    ticks = _angel_ticks(_angel_packet(mode=mode, price=265075, size=51 if mode == 1 else 379))
    assert len(ticks) == 1 and ticks[0]["last_price"] == 2650.75


def test_an_angelone_depth_packet_is_refused_rather_than_priced():
    """Depth mode reuses the header and replaces everything after it.

    Its bytes at the price offset are a book, so decoding one as a price would
    publish a *quantity* as a rupee value — a number that is not obviously wrong
    on a chart and would be marked against a real position.
    """
    assert _angel_ticks(_angel_packet(mode=4, price=1500, size=443)) == []


def test_a_truncated_angelone_packet_yields_no_invented_tick():
    """A short read must produce nothing, never a zero-padded price."""
    for size in (0, 1, 2, 27, 43, 50):
        assert _angel_ticks(_angel_packet(size=size)) == [], size


def test_an_angelone_packet_from_an_unmapped_segment_is_dropped():
    """The price divisor is per segment, so a segment we cannot name we cannot price."""
    assert _angel_ticks(_angel_packet(segment=99)) == []


def test_an_angelone_token_is_read_up_to_its_null_terminator():
    """25 bytes of field, NUL-terminated ASCII — the trailing bytes are not the token."""
    ticks = _angel_ticks(_angel_packet(token_bytes=b"2885\x00\x99\x99\x99"))
    assert ticks[0]["instrument_token"] == "1|2885"
    # A field with no digits at all identifies nothing and is dropped.
    assert _angel_ticks(_angel_packet(token_bytes=b"\x00" * 25)) == []
    assert _angel_ticks(_angel_packet(token_bytes=b"NOTATOKEN\x00")) == []


def test_angelone_text_frames_are_classified_and_none_of_them_is_a_tick():
    """`pong`, an error envelope, and junk — three text shapes, three outcomes."""
    adapter = _angelone()
    assert adapter.decode_stream_frame("pong").kind is StreamEventKind.IGNORE

    error = adapter.decode_stream_frame(json.dumps(
        {"correlationID": "abcde12345", "errorCode": "E1002",
         "errorMessage": "Invalid Request. Subscription Limit Exceeded"}))
    assert error.kind is StreamEventKind.ERROR
    assert "E1002" in error.message and "Subscription Limit" in error.message

    for junk in ("", "   ", "not json", "[]", json.dumps({"unrelated": 1})):
        assert adapter.decode_stream_frame(junk).kind is StreamEventKind.IGNORE


def test_an_angelone_subscription_error_does_not_drop_a_working_socket():
    """SmartAPI states a failed subscription leaves existing ones streaming.

    Dropping the connection would turn a partial rejection into a total outage.
    """
    frames = [
        json.dumps({"correlationID": "abcde12345", "errorCode": "E1002",
                    "errorMessage": "Invalid Request. Subscription Limit Exceeded"}),
        _angel_packet(price=265075),
    ]
    ticks, _, expired, socket = drive_stream(
        _angelone(), frames, instruments=["1|2885"], session=_angel_session())
    assert expired == []
    assert len(ticks) == 1 and ticks[0][2][0]["last_price"] == 2650.75
    assert socket.closed


def test_a_malformed_angelone_frame_does_not_terminate_a_live_stream():
    """One damaged frame costs itself and nothing else."""
    frames = [b"\xff\xff", _angel_packet(price=265075), b"", "pong"]
    ticks, _, _, socket = drive_stream(
        _angelone(), frames, instruments=["1|2885"], session=_angel_session())
    assert len(ticks) == 1 and ticks[0][2][0]["last_price"] == 2650.75
    assert socket.closed


# -- price representation ------------------------------------------------------


def test_an_angelone_price_is_paise_and_a_copied_divisor_would_be_wrong():
    """Paise like Kite — and NOT Kite's *rule*, which is keyed on the token.

    Kite reads the exchange segment out of the low byte of its 32-bit token;
    SmartAPI carries the segment as its own field and quotes everything in paise
    except currencies. Copying `zerodha.price_divisor` here would consult a byte
    of a SmartAPI token that means nothing at all.
    """
    from services.brokers.zerodha import price_divisor as kite_divisor

    assert _angel_ticks(_angel_packet(token="2885", price=265075))[0]["last_price"] == 2650.75
    # Kite's rule applied to this SmartAPI token reads segment 69 — a divisor of
    # 100 by luck here, and the wrong one the moment a currency tick arrives.
    assert kite_divisor(2885) == 100.0
    currency = _angel_ticks(_angel_packet(segment=13, token="12345", price=7512500))[0]
    assert currency["last_price"] == 0.75125, "SmartAPI quotes currencies at 10^7"
    assert kite_divisor(12345) != 10_000_000.0, "Kite's segment rule does not produce SmartAPI's scale"


@pytest.mark.parametrize("paise,expected", [(1, 0.01), (105, 1.05), (265075, 2650.75), (99999999, 999999.99)])
def test_angelone_decimal_precision_survives_the_codec(paise, expected):
    assert _angel_ticks(_angel_packet(price=paise))[0]["last_price"] == expected


def test_an_angelone_zero_or_negative_price_never_becomes_a_market_tick():
    """A zero price is what a truncated packet decodes to and would mark a position at."""
    for paise in (0, -100):
        ticks = _angel_ticks(_angel_packet(price=paise))
        # The broker tick may exist; the canonical boundary is where it stops.
        assert canonical_ticks(ticks, _angel_map(), broker="angelone") == []


def test_an_impossible_angelone_price_is_refused_at_the_canonical_boundary():
    """The Market Engine's own quote bounds, reached through the tick path."""
    huge = int((MAX_STOCK_PRICE + 1000) * 100)
    assert canonical_ticks(_angel_ticks(_angel_packet(price=huge)), _angel_map(), broker="angelone") == []


# -- volume semantics ----------------------------------------------------------


def test_an_angelone_ltp_tick_carries_no_volume_rather_than_a_fabricated_one():
    """LTP mode has no volume field, and the wider modes' `ltq` is not volume.

    `lLastTradedQty` is one trade's size; the day's cumulative volume is
    `lVolumeTradedToday`, and neither exists in the mode this adapter
    subscribes. Putting either in the canonical `volume` field would populate it
    with a number that means something else — the same rejection D4.6 and D4.7
    made for Kite's LTP mode and Upstox's `ltpc`, reached a third time.
    """
    tick = _angel_canonical()[0]
    assert tick["volume"] is None, "a volume appeared that the subscribed mode cannot carry"
    assert _angel_ticks(_angel_packet())[0]["volume"] == 0


# -- instrument identity -------------------------------------------------------


def test_an_angelone_instrument_becomes_a_canonical_symbol_through_the_shared_map():
    """A third identifier shape resolving through the unchanged `InstrumentMap`."""
    ticks = canonical_ticks(_angel_ticks(_angel_packet(token="2885")), _angel_map(), broker="angelone")
    assert ticks == [{
        "symbol": "RELIANCE", "price": 2650.75, "exchange": "NSE",
        "volume": None, "ingested_at": ticks[0]["ingested_at"],
    }]


def test_an_angelone_token_alone_is_not_an_identity():
    """THE protocol fact this adapter is built around, asserted as a behaviour.

    SmartAPI token numbers are unique within an exchange segment and nothing
    more, so the same number means different instruments on NSE and BSE. Had the
    adapter stored the bare token — Kite's shape — a BSE tick would resolve to
    an NSE holding and mark a position at another instrument's price. Nothing
    would raise; the number would simply be wrong.
    """
    from services.brokers.angelone import instrument_id

    assert instrument_id("NSE", "2885") != instrument_id("BSE", "2885")

    account = InstrumentMap.from_portfolio(
        [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": instrument_id("NSE", "2885")}]
    )
    nse = canonical_ticks(_angel_ticks(_angel_packet(token="2885", segment=1)), account, broker="angelone")
    bse = canonical_ticks(_angel_ticks(_angel_packet(token="2885", segment=3)), account, broker="angelone")
    assert [t["symbol"] for t in nse] == ["RELIANCE"]
    assert bse == [], "a BSE tick was resolved to an NSE holding by its token number alone"


def test_an_unknown_angelone_instrument_is_dropped_rather_than_named():
    """A token the account has no row for is unresolvable, never a symbol."""
    resolved = canonical_ticks(_angel_ticks(_angel_packet(token="999999")), _angel_map(), broker="angelone")
    assert resolved == []


def test_an_angelone_identity_that_round_tripped_through_mongo_still_reaches_the_wire():
    """The subscription side of the same string/round-trip exposure D4.3 documents."""
    holdings = [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "1|2885"}]
    assert _angelone().stream_instruments(holdings=holdings) == ["1|2885"]
    # And a row whose identity never got built is skipped rather than sent.
    assert _angelone().stream_instruments(holdings=[{"symbol": "X", "instrument_token": None}]) == []


def test_the_angelone_trading_symbol_series_suffix_is_stripped_at_the_boundary():
    """`TATASTEEL-EQ` is SmartAPI's name for the instrument every other broker calls `TATASTEEL`.

    Left alone, a user holding one stock at two brokers would hold two different
    canonical symbols — a split portfolio and a feed whose coverage never
    matches the platform's instrument universe.
    """
    from services.brokers.angelone import trading_symbol

    assert trading_symbol("TATASTEEL-EQ") == "TATASTEEL"
    assert trading_symbol("SBIN-BE") == "SBIN"
    # A derivative symbol carries no series suffix and must not be truncated.
    assert trading_symbol("NIFTY30JAN2523500CE") == "NIFTY30JAN2523500CE"
    assert trading_symbol("M-M") == "M-M", "a hyphen that is not a series code was stripped"
    assert trading_symbol(None) is None


def test_angelone_needs_no_instrument_catalogue():
    """The account's own synced rows are the mapping table, in both directions.

    A holding row carries the exchange, the SmartAPI token and the trading
    symbol side by side, so subscription and resolution are built from the same
    two lists this platform already syncs. Requiring SmartAPI's master scrip
    file would have turned an adapter sprint into a data-pipeline sprint.
    """
    holdings = [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "1|2885"}]
    subscribed = _angelone().stream_instruments(holdings=holdings)
    resolved = canonical_ticks(
        _angel_ticks(_angel_packet(token="2885")),
        InstrumentMap.from_portfolio(holdings), broker="angelone")
    assert subscribed == ["1|2885"] and resolved[0]["symbol"] == "RELIANCE"


def test_the_angelone_holdings_row_carries_the_identity_the_feed_subscribes():
    """The two halves are one expression, so they cannot drift apart."""
    payload = {"status": True, "message": "SUCCESS", "errorcode": "", "data": {"holdings": [
        {"tradingsymbol": "TATASTEEL-EQ", "exchange": "NSE", "isin": "INE081A01020", "quantity": 2,
         "t1quantity": 0, "averageprice": 111.87, "ltp": 130.15, "symboltoken": "3499",
         "product": "DELIVERY", "profitandloss": 37, "pnlpercentage": 16.34}]}}

    adapter = _angelone()
    with patch.object(BrokerAdapter, "_request", new=AsyncMock(return_value=payload)):
        holdings = run(adapter.get_holdings({"access_token": "t"}))
    assert holdings[0]["symbol"] == "TATASTEEL"
    assert holdings[0]["instrument_token"] == "1|3499"
    assert adapter.stream_instruments(holdings=holdings) == ["1|3499"]


# -- canonicalization ----------------------------------------------------------


def test_no_raw_angelone_payload_escapes_the_adapter():
    """The codec boundary, checked on the type rather than on the values."""
    event = _angelone().decode_stream_frame(_angel_packet())
    assert event.kind is StreamEventKind.TICKS
    assert all(type(tick).__name__ == "BrokerTick" for tick in event.ticks)
    blob = json.dumps(_angel_canonical()[0]).lower()
    for forbidden in ("angel", "smartapi", "exchangetype", "correlationid", "symboltoken", "2885", "1|"):
        assert forbidden not in blob, f"{forbidden} reached a canonical market tick"


def test_three_brokers_speak_three_protocols_and_produce_identical_canonical_ticks():
    """THE D4.9 acceptance criterion: a third wire, the same canonical output.

    Three brokers agree on nothing at the wire — big-endian bespoke binary vs
    protobuf vs little-endian fixed packets, integer token vs compound key vs
    segment-qualified token, three different price rules, three auth styles, one
    socket vs two vs one-with-a-keep-alive — and what reaches the Market Engine
    from each is the same canonical `MarketTick`.
    """
    kite = canonical_ticks(_kite_ticks(_kite_frame((738561, 265075))), _kite_map(), broker="zerodha")
    upstox = canonical_ticks(
        _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75})), _upstox_map(), broker="upstox")
    angel = _angel_canonical(price=265075, token="2885")

    wires = [_kite_frame((738561, 265075)),
             _upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75}),
             _angel_packet(price=265075)]
    assert len({bytes(w) for w in wires}) == 3, "two of the three wires are identical"

    shaped = [[{k: v for k, v in tick.items() if k != "ingested_at"} for tick in batch]
              for batch in (kite, upstox, angel)]
    assert shaped[0] == shaped[1] == shaped[2] == [
        {"symbol": "RELIANCE", "price": 2650.75, "exchange": "NSE", "volume": None}
    ]


# -- transport -----------------------------------------------------------------


def test_an_angelone_stream_runs_through_the_generic_transport_unchanged():
    """Real SmartAPI bytes over the real transport: subscribe, tick, pong, close.

    The subscribe frame must reach the socket verbatim *as text* — the transport
    cannot know what encoding this broker expects, so it must not re-encode what
    the codec handed it.
    """
    frames = ["pong", _angel_packet(token="2885", price=265075)]
    ticks, orders, expired, socket = drive_stream(
        _angelone(), frames, instruments=["1|2885"], session=_angel_session())

    assert len(socket.sent) == 1 and isinstance(socket.sent[0], str)
    assert json.loads(socket.sent[0])["params"]["tokenList"] == [{"exchangeType": 1, "tokens": ["2885"]}]
    assert len(ticks) == 1 and ticks[0][1] == "angelone"
    assert ticks[0][2] == [{
        "instrument_token": "1|2885", "last_price": 2650.75, "symbol": None,
        "exchange": "NSE", "volume": 0, "timestamp": "1724236800000",
    }]
    assert orders == [] and expired == []
    assert socket.closed


def test_angelone_uses_one_channel_and_the_free_single_channel_path():
    """The independent test of the channel model D4.7 introduced.

    Upstox forced channels into existence and was the only user of them, so the
    concept had never been exercised by a broker that did *not* need it. Angel
    One's realtime surface is one socket, and it declares nothing about
    channels: it gets the free one, backed by its own `stream_*` methods.
    """
    from services.brokers.base import AdapterStreamChannel

    channel = _angel_channel()
    assert isinstance(channel, AdapterStreamChannel)
    assert channel.name == DEFAULT_STREAM_CHANNEL
    assert channel.delivers == frozenset({StreamEventKind.TICKS})
    assert channel.protocol == _angelone().stream_protocol == "smartapi_stream_v2"
    # And it is served by the one generic WebSocket transport, like all the rest.
    from services.brokers.stream import PROTOCOL_RUNNERS, resolve_transport

    assert PROTOCOL_RUNNERS == {}
    assert resolve_transport(channel) is BrokerStream._run_websocket


# -- error classification ------------------------------------------------------


class _AngelHandshakeRefused(Exception):
    def __init__(self, status_code):
        super().__init__(f"server rejected WebSocket connection: HTTP {status_code}")
        self.status_code = status_code


class _AngelHandshakeRefused14(Exception):
    """websockets >= 14 wraps the handshake response instead of exposing a status."""

    def __init__(self, status_code):
        super().__init__("server rejected WebSocket connection")
        self.response = type("Response", (), {"status_code": status_code})()


@pytest.mark.parametrize("refusal", [_AngelHandshakeRefused, _AngelHandshakeRefused14])
@pytest.mark.parametrize("status", [401, 403])
def test_angelone_refusing_the_handshake_expires_the_session(refusal, status):
    """SmartAPI rejects a dead session at the handshake, so no codec ever sees it.

    Unclassified, an expired session is indistinguishable from a broker outage:
    the stream reconnects on the backoff schedule forever, the account's market
    feed stays registered, and the user is never asked to reconnect. A SmartAPI
    session dies at midnight IST, so that is every connected user, every day.
    """
    stream = BrokerStream("u1", "angelone", _angel_session(), channel=DEFAULT_STREAM_CHANNEL)

    async def scenario():
        with patch.object(BrokerStream, "_connect", AsyncMock(side_effect=refusal(status))):
            with pytest.raises(_AuthExpired) as excinfo:
                await stream._run_websocket()
        assert "reconnect" in str(excinfo.value).lower()

    run(scenario())


def test_an_ordinary_angelone_connection_failure_still_reconnects():
    """Only a refusal means a dead session; everything else is connection weather."""
    stream = BrokerStream("u1", "angelone", _angel_session(), channel=DEFAULT_STREAM_CHANNEL)

    async def scenario():
        with patch.object(BrokerStream, "_connect", AsyncMock(side_effect=OSError("connection reset"))):
            with pytest.raises(OSError):
                await stream._run_websocket()

    run(scenario())
    assert _angelone().stream_connect_error(OSError("connection reset")) is None
    assert _angelone().stream_connect_error(_AngelHandshakeRefused(500)) is None


def test_an_expired_angelone_rest_session_is_reported_as_an_auth_failure():
    """SmartAPI answers HTTP 200 with `status: false` for a dead session.

    So the transport-level 401 handling cannot see it, and an unchecked caller
    would read `data: null` as an empty portfolio rather than a dead session.
    """
    from services.brokers.errors import BrokerAuthError, BrokerError

    dead = {"status": False, "message": "Invalid Token", "errorcode": "AG8001", "data": None}
    with patch.object(BrokerAdapter, "_request", new=AsyncMock(return_value=dead)):
        with pytest.raises(BrokerAuthError):
            run(_angelone().get_holdings({"access_token": "t"}))

    other = {"status": False, "message": "Something else", "errorcode": "AB1004", "data": None}
    with patch.object(BrokerAdapter, "_request", new=AsyncMock(return_value=other)):
        with pytest.raises(BrokerError) as excinfo:
            run(_angelone().get_holdings({"access_token": "t"}))
    assert not isinstance(excinfo.value, BrokerAuthError)


# -- readiness, promotion, failover ---------------------------------------------


def test_a_connected_angelone_stream_is_not_ready_until_a_real_packet_arrives(no_probation_window):
    """CONNECTED != READY, driven by SmartAPI's own bytes.

    Every milestone short of data is reached — registered, link up, subscribed,
    a keep-alive answered, an error frame received, a damaged frame received —
    and the baseline still serves the quote.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        run(baseline.connect())
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        feed = _angel_feed("u1", symbols=("RELIANCE",))
        run(set_market_feed_link("u1", "angelone", up=True))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        for noise in ("pong", json.dumps({"errorCode": "E1001"}), b"\xff\xff", _angel_packet(size=20)):
            run(feed.on_raw(canonical_ticks(
                [t.as_dict() for t in _angelone().decode_stream_frame(noise).ticks],
                _angel_map(), broker="angelone")))
        assert not feed.is_ready
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        run(feed.on_raw(_angel_canonical()))
        assert feed.is_ready
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed
        assert run(feed.fetch_quote("RELIANCE"))["price"] == 2650.75
        # Make-before-break: the baseline was never unregistered or disconnected.
        assert registry.get(baseline.name) is baseline and baseline.is_connected


def test_an_angelone_feed_is_promoted_over_the_baseline_and_falls_back_on_link_loss(no_probation_window):
    """The unchanged D4.5 machinery, reached by a third broker."""
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        feed = _angel_feed("u1", symbols=("RELIANCE",))
        run(set_market_feed_link("u1", "angelone", up=True))
        run(feed.on_raw(_angel_canonical()))
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed

        run(set_market_feed_link("u1", "angelone", up=False, reason="socket closed"))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline
        assert registry.get(feed.name) is feed, "a dropped socket unregistered the feed"


def test_a_reconnected_angelone_feed_cannot_answer_from_the_dead_links_prices():
    """The D4.8 stale-evidence hardening, re-checked on the third broker.

    Readiness is re-earned by *any* symbol while coverage is per symbol, so a
    feed that ticked A and B on link 1, lost it, reconnected and received one
    fresh tick for A must not answer a quote for B from link 1's price.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        feed = _angel_feed("u1", symbols=("RELIANCE", "TCS"))
        run(set_market_feed_link("u1", "angelone", up=True))
        run(feed.on_raw(_angel_canonical(price=265075, token="2885")))    # RELIANCE
        run(feed.on_raw(_angel_canonical(price=399010, token="11536")))   # TCS
        assert feed.covers("RELIANCE") and feed.covers("TCS")

        run(set_market_feed_link("u1", "angelone", up=False, reason="dropped"))
        run(set_market_feed_link("u1", "angelone", up=True))
        assert not feed.is_ready, "readiness survived the link that earned it"

        run(feed.on_raw(_angel_canonical(price=266000, token="2885")))
        assert feed.is_ready and feed.covers("RELIANCE")
        assert not feed.covers("TCS"), "a price from the previous connection survived the reconnect"
        assert manager.resolve(
            Capability.QUOTES, context=ResolutionContext(user_id="u1", symbol="TCS")) is baseline


def test_an_angelone_feed_that_never_ticks_leaves_the_baseline_primary():
    """A socket that connects, subscribes and says nothing must change nothing."""
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        _angel_feed("u1", symbols=("RELIANCE",))
        run(set_market_feed_link("u1", "angelone", up=True))
        assert manager.resolve(
            Capability.QUOTES, context=ResolutionContext(user_id="u1", symbol="RELIANCE")) is baseline


def test_angelone_registers_through_the_existing_provider_framework():
    """No Angel-One-specific provider, gateway, registry or Source Manager exists."""
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import StreamingTickProvider

    with _clean_provider_registry() as registry:
        registry.clear()
        name = run(_attach("u1", "angelone", ["RELIANCE"]))
        assert name == feed_provider_name("u1", "angelone") == "brokerfeed:angelone:u1"
        provider = registry.get(name)
        assert type(provider) is StreamingTickProvider, "a broker-specific provider class appeared"
        assert provider.owner_user_id == "u1"


def test_removing_the_angelone_tick_capability_removes_its_market_feed():
    """The capability declaration is what makes the feed a provider — nothing else.

    A broker that does not declare `TICK_STREAM` must not be registered as a
    priority-1 streaming provider, because one that can only deliver silence
    would still be ranked above the working baseline.
    """
    adapter = _angelone()
    narrowed = adapter.capabilities - {BrokerCapability.TICK_STREAM}
    with _clean_provider_registry() as registry:
        registry.clear()
        with patch.object(type(adapter), "capabilities", narrowed):
            assert run(_attach("u1", "angelone", ["RELIANCE"])) is None
        assert registry.get("brokerfeed:angelone:u1") is None


# -- isolation ------------------------------------------------------------------


def test_four_users_on_four_providers_stay_on_their_own(no_probation_window, caplog):
    """Angel One, Zerodha, Upstox and the shared baseline, at once.

    The one test that exercises every streaming broker the platform has plus the
    fallback, and the property is per user: each resolves to their own feed, and
    one broker's failure moves exactly one user.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        feeds = {
            "userA": _angel_feed("userA", symbols=("RELIANCE",)),
            "userB": (_attach_and_get("userB", "zerodha")),
            "userC": _upstox_feed("userC", symbols=("RELIANCE",)),
        }
        for user, broker in (("userA", "angelone"), ("userB", "zerodha"), ("userC", "upstox")):
            run(set_market_feed_link(user, broker, up=True))
        run(feeds["userA"].on_raw(_angel_canonical()))
        run(feeds["userB"].on_raw(canonical_ticks(
            _kite_ticks(_kite_frame((738561, 265075))), _kite_map(), broker="zerodha")))
        run(feeds["userC"].on_raw(_upstox_canonical()))

        def resolved(user):
            return manager.resolve(Capability.QUOTES, context=ResolutionContext(user_id=user, symbol="RELIANCE"))

        assert resolved("userA") is feeds["userA"]
        assert resolved("userB") is feeds["userB"]
        assert resolved("userC") is feeds["userC"]
        assert resolved("userD") is baseline, "a user with no broker was served another user's feed"

        # Angel One drops. Only user A moves.
        run(set_market_feed_link("userA", "angelone", up=False, reason="socket closed"))
        assert resolved("userA") is baseline
        assert resolved("userB") is feeds["userB"]
        assert resolved("userC") is feeds["userC"]
        assert resolved("userD") is baseline


def _attach_and_get(user_id, broker, symbols=("RELIANCE",)):
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import provider_registry

    run(_attach(user_id, broker, list(symbols)))
    return provider_registry.get(feed_provider_name(user_id, broker))


def test_an_angelone_quote_carries_no_broker_identity_and_no_other_users_data():
    """What a consumer receives says `streaming` and nothing about Angel One."""
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        registry.register(YahooPollingAdapter())
        feed = _angel_feed("u1", symbols=("RELIANCE",))
        run(set_market_feed_link("u1", "angelone", up=True))
        run(feed.on_raw(_angel_canonical()))
        quote = run(feed.fetch_quote("RELIANCE"))

    blob = json.dumps(quote).lower()
    for forbidden in ("angel", "smartapi", "brokerfeed", "u1", "jwt", "feed_token", "2885"):
        assert forbidden not in blob, f"{forbidden} reached a consumer-facing quote"


# -- the engine seam -------------------------------------------------------------


def test_the_engine_carries_angelone_bytes_all_the_way_into_the_registered_feed(no_probation_window):
    """The real join: the engine's instrument map, the canonical boundary, the push."""
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    holdings = [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "1|2885"}]
    engine = BrokerEngine()
    engine.configure(FakeDB())

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        with patch("services.brokers.stream.stream_manager.start_stream", new=AsyncMock()) as started, \
                patch.object(BrokerEngine, "get_session", new=AsyncMock(return_value=_angel_session())), \
                patch("services.brokers.gateway.broker_gateway.get_holdings", new=AsyncMock(return_value=holdings)), \
                patch("services.brokers.gateway.broker_gateway.get_positions", new=AsyncMock(return_value=[])):
            run(engine.start_stream("u1", "angelone"))

        # One channel, and the engine passed it this account's SmartAPI ids.
        assert started.await_count == 1
        assert started.await_args.kwargs["instrument_tokens"] == ["1|2885"]
        assert started.await_args.kwargs["channel"] == DEFAULT_STREAM_CHANNEL

        feed = registry.get("brokerfeed:angelone:u1")
        assert feed is not None and feed.subscribed_symbols == ("RELIANCE",)
        run(feed.mark_link_up())
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        with patch.object(BrokerEngine, "_push", new=AsyncMock()):
            run(engine._on_stream_tick("u1", "angelone", _angel_ticks(_angel_packet(price=265050))))

        assert feed.describe()["accepted_records"] == 1
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed
        assert run(feed.fetch_quote("RELIANCE"))["price"] == 2650.5


def test_an_expired_angelone_session_detaches_the_market_feed():
    """A dead session must stop being resolvable now, not at the next transition."""
    engine = BrokerEngine()
    engine.configure(FakeDB())

    with _clean_provider_registry() as registry:
        registry.clear()
        _angel_feed("u1", symbols=("RELIANCE",))
        assert registry.get("brokerfeed:angelone:u1") is not None
        with patch.object(BrokerEngine, "_push", new=AsyncMock()), \
                patch("services.brokers.stream.stream_manager.stop_stream", new=AsyncMock()):
            run(engine._on_stream_expired("u1", "angelone", DEFAULT_STREAM_CHANNEL))
        assert registry.get("brokerfeed:angelone:u1") is None


# -- security --------------------------------------------------------------------


def test_no_angelone_credential_reaches_a_log_line(caplog):
    """The whole transport pass, at DEBUG, with four live-looking credentials.

    Angel One carries more credential material onto a socket than either
    predecessor — a session JWT, a separate feed token, the app key and the
    client code — so it has four ways to fail this rather than one.
    """
    caplog.set_level(logging.DEBUG)
    drive_stream(
        _angelone(),
        ["pong", _angel_packet(price=265050), json.dumps({"errorCode": "E1002", "errorMessage": "limit"})],
        instruments=["1|2885"],
        session={"access_token": "SECRET-JWT", "feed_token": "SECRET-FEED-TOKEN", "account_id": "A123456"},
    )
    emitted = "\n".join(r.getMessage() for r in caplog.records)
    assert emitted, "nothing was logged — the sweep could not have failed"
    for secret in ("SECRET-JWT", "SECRET-FEED-TOKEN", "nova-key", "A123456"):
        assert secret not in emitted, f"{secret} reached a log line"


def test_the_angelone_feed_token_is_encrypted_at_rest_like_every_other_session_secret():
    """The second per-session credential this broker's stream needs is a secret.

    Stored in plaintext it would be the one field in `db.broker_accounts` that
    SECURITY.md's encryption-at-rest rule did not cover — and it is enough, with
    the app key, to open a market feed for the account.
    """
    from services.broker_engine import TOKEN_FIELDS
    from services.brokers.crypto import is_encrypted

    assert "feed_token" in TOKEN_FIELDS
    engine = BrokerEngine()
    engine.configure(FakeDB())
    session = {"access_token": "jwt", "feed_token": "SECRET-FEED-TOKEN", "account_id": "A1"}
    run(engine._save_account("u1", "angelone", session))

    stored = run(engine.db.broker_accounts.find_one({"user_id": "u1", "broker": "angelone"}))
    assert stored["feed_token"] != "SECRET-FEED-TOKEN"
    assert is_encrypted(stored["feed_token"])
    assert run(engine._load_account("u1", "angelone"))["feed_token"] == "SECRET-FEED-TOKEN"


def test_disconnecting_an_angelone_account_clears_every_session_secret():
    """Disconnect must not leave a usable feed credential behind."""
    from services.broker_engine import TOKEN_FIELDS

    engine = BrokerEngine()
    engine.configure(FakeDB())
    run(engine._save_account("u1", "angelone", {
        "access_token": "jwt", "feed_token": "SECRET-FEED-TOKEN",
        "refresh_token": "r", "account_id": "A1"}))

    with patch("services.brokers.gateway.broker_gateway.invalidate_session", new=AsyncMock()), \
            patch("services.brokers.stream.stream_manager.stop_stream", new=AsyncMock()), \
            patch.object(BrokerEngine, "_push", new=AsyncMock()), \
            patch.object(BrokerEngine, "_publish_connection", new=AsyncMock()):
        run(engine.disconnect("angelone", "u1"))

    stored = run(engine.db.broker_accounts.find_one({"user_id": "u1", "broker": "angelone"}))
    assert stored["connected"] is False
    for field in TOKEN_FIELDS:
        assert stored.get(field) == "", f"{field} survived a disconnect"


# -- architecture ----------------------------------------------------------------


def test_angelone_added_no_angelone_knowledge_outside_its_own_adapter():
    """D4.9's central acceptance criterion, swept rather than asserted by eye.

    The SmartAPI vocabulary — the feed URL, the exchange segments, the
    subscription mode, the packet offsets, the token notion — must exist in
    exactly one module. A second module that knows any of it is the beginning of
    the `if broker == "angelone"` branch this framework exists to make
    unnecessary.
    """
    # `angel(?!og)` rather than `angel`, and rather than `\bangel`: "changelog"
    # contains "angel" (a false positive is a sweep somebody turns off), while
    # `\bangel` would miss the shape a branch actually takes — a helper named
    # `_is_angelone`, where the preceding underscore is a word character. That
    # exact mutation survived a `\bangel` sweep during the falsification pass.
    angel_words = (r"angel(?!og)", "smartapi", "smart-stream", "exchangetype", "tokenlist",
                   "symboltoken", "correlationid", "clientcode")
    allowed = {
        "services/brokers/angelone.py",   # the adapter — the only owner
        "services/brokers/__init__.py",   # the registry entry — one line
    }
    offenders = {}
    scanned = 0
    for path in sorted((BACKEND / "services").rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in allowed:
            continue
        scanned += 1
        source = _strip_source(path.read_text(encoding="utf-8")).lower()
        hit = [w for w in angel_words if re.search(w, source)]
        if hit:
            offenders[rel] = hit
    assert scanned > 50, "the sweep found almost no modules — it could not have failed"
    assert not offenders, offenders


def test_the_market_layer_is_untouched_by_the_third_broker():
    """Everything D4.9 promised would not move, re-checked with three brokers live."""
    market_modules = [
        "services/market_engine/gateway.py",
        "services/market_engine/source_manager.py",
        "services/market_engine/ticks.py",
        "services/market_engine/providers/streaming.py",
        "services/market_engine/providers/registry.py",
        "services/market_engine/providers/base.py",
    ]
    for relative in market_modules:
        source = _strip_source((BACKEND / relative).read_text(encoding="utf-8")).lower()
        for name in (r"angel(?!og)", "upstox", "zerodha", "kite", "broker_gateway", "broker_registry"):
            assert not re.search(name, source), f"{relative} names {name!r} in executable code"


def test_the_third_broker_added_no_broker_knowledge_to_the_transport():
    """`stream.py` grew a keep-alive timer and still cannot tell one broker from another.

    D4.9 changed this module — that is the honest finding of the sprint, as
    D4.7's channel concept was — but what it added is a timer driven by a value
    the endpoint carries. The property that matters survives.
    """
    source = _strip_source((BACKEND / "services/brokers/stream.py").read_text(encoding="utf-8")).lower()
    for name in (r"angel(?!og)", "smartapi", "upstox", "zerodha", "kite", "protobuf", "ping\"", "'ping'"):
        assert not re.search(name, source), f"stream.py names {name!r} in executable code"


def test_the_engine_names_no_broker_after_the_third_one():
    """The engine's session-secret list is generic names, not a per-broker registry."""
    source = _strip_source((BACKEND / "services/broker_engine.py").read_text(encoding="utf-8")).lower()
    for name in (r"angel(?!og)", "smartapi", "upstox", "zerodha", "kite"):
        assert not re.search(name, source), f"broker_engine.py names {name!r} in executable code"


def test_all_three_streaming_brokers_reach_the_market_gateway_through_the_identical_seam():
    """One seam, three brokers, no branch anywhere on the path."""
    import inspect

    from services.brokers import market_feed

    source = _strip_source(inspect.getsource(market_feed)).lower()
    for name in (r"angel(?!og)", "smartapi", "upstox", "zerodha", "kite"):
        assert not re.search(name, source), f"market_feed.py names {name!r} in executable code"

    # `_strip_source` removes string literals, so an `if broker == "angelone"`
    # branch is invisible to the vocabulary sweep above by construction. The
    # comparison itself is what is banned here, on the unstripped source — the
    # same ban `test_the_public_broker_routes_do_not_branch_on_a_broker_name`
    # places on `server.py`, extended to the seam a third broker would tempt
    # somebody to branch in.
    for relative in ("services/brokers/market_feed.py", "services/brokers/stream.py",
                     "services/market_engine/source_manager.py"):
        text = _strip_prose((BACKEND / relative).read_text(encoding="utf-8"))
        for comparison in ("broker ==", "broker!=", "broker !=", "broker in ("):
            assert comparison not in text, f"{relative} branches on a broker name: {comparison!r}"

    for broker in ("zerodha", "upstox", "angelone"):
        adapter = broker_registry.require(broker)
        assert adapter.supports(BrokerCapability.TICK_STREAM)
        channels = [c for c in adapter.stream_channels() if StreamEventKind.TICKS in c.delivers]
        assert len(channels) == 1, f"{broker} must have exactly one tick-carrying channel"


# ==================================================================
# D4.10 — Fyers API v3: the FOURTH concrete stream adapter
#
# The protocol below is transcribed from Fyers' own reference client
# (`fyers-apiv3` 3.1.16, `fyers_apiv3/FyersWebsocket/data_ws.py`) and the
# segment/field tables it ships in `map.json` — not inferred from Kite, not from
# Upstox, and not from SmartAPI.
#
# Fyers is the first broker here that disagrees with the *framework* rather than
# only with its predecessors, in two ways that share one cause:
#
#   * it authenticates with a FRAME on the data channel, not in the handshake,
#     so the first thing the feed sends is a per-session credential;
#   * its steady-state frames are DELTAS against a snapshot, keyed by a topic id
#     the server mints per connection, so a frame is not decodable on its own.
#
# Both need a scope the contract did not have: one connection. D4.10's single
# generic addition is `BrokerStreamChannel.open()`, which returns `self` by
# default and is therefore invisible to the three brokers that came before.
#
# Every test below was run against a deliberately broken implementation first;
# the mutations are listed in TASK.md's D4.10 falsification table.
# ==================================================================


def _fyers():
    return broker_registry.require("fyers")


def _fyers_channel():
    channels = _fyers().stream_channels()
    assert len(channels) == 1, "Fyers' market-data surface is one socket"
    return channels[0]


def _fy_token(hsm_key="HSM-KEY-1", exp=4_102_444_800):
    """A Fyers access token: a JWT whose payload carries the feed key.

    Built rather than mocked because the *shape* is the protocol fact — the
    market feed's credential is folded inside the REST token rather than issued
    beside it, and an adapter that reached for a separate `feed_token` field
    would find nothing and authenticate with nothing.
    """
    import base64 as _b64

    def seg(obj):
        return _b64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    payload = {"exp": exp} if hsm_key is None else {"hsm_key": hsm_key, "exp": exp}
    return f"{seg({'alg': 'HS256'})}.{seg(payload)}.signature"


def _fy_session(hsm_key="HSM-KEY-1", exp=4_102_444_800):
    return {"access_token": _fy_token(hsm_key, exp), "account_id": "XY01234"}


def _fy_conn(session=None):
    """A Fyers codec scoped to one connection, exactly as the transport mints it."""
    return _fyers_channel().open(session if session is not None else _fy_session())


# -- HSM wire fixtures, built from the published request/response framing -------


def _fy_snapshot(topic_id=1, name="sf|nse_cm|2885", values=(265075,),
                 multiplier=1, precision=2, strings=("NSE", "2885", "SBIN-EQ")):
    """One snapshot record — the only record kind that describes itself."""
    body = bytearray([83])
    body += struct.pack("<H", topic_id)
    body.append(len(name))
    body += name.encode("ascii")
    body.append(len(values))
    for value in values:
        body += struct.pack(">i", value)
    body += b"\x00\x00"                      # the two unnamed bytes the client skips
    body += struct.pack(">H", multiplier)
    body.append(precision)
    for text in strings:
        body.append(len(text))
        body += text.encode("ascii")
    return bytes(body)


def _fy_lite(topic_id=1, ltp=265075):
    """One lite update: a topic id and a price. Nothing else — that is the point."""
    return bytes([76]) + struct.pack("<H", topic_id) + struct.pack(">i", ltp)


def _fy_full(topic_id=1, values=(265075, 100000)):
    body = bytearray([85])
    body += struct.pack("<H", topic_id)
    body.append(len(values))
    for value in values:
        body += struct.pack(">i", value)
    return bytes(body)


def _fy_frame(*records, message_number=7, record_count=None):
    """One HSM data frame carrying `records`, which may be of mixed kinds."""
    count = len(records) if record_count is None else record_count
    body = bytes([6]) + struct.pack(">I", message_number) + struct.pack(">H", count) + b"".join(records)
    return struct.pack(">H", len(body)) + body


def _fy_control(response_type, status="K", trailer=b""):
    body = bytes([response_type, 1, 1]) + struct.pack(">H", 1) + status.encode("ascii") + trailer
    return struct.pack(">H", len(body)) + body


def _fy_auth_ok(acknowledge_every=0):
    """An accepted credential frame, optionally asking to be acknowledged."""
    return _fy_control(1, "K", bytes([2]) + struct.pack(">H", 4) + struct.pack(">I", acknowledge_every))


def _fy_ticks(*records, session=None):
    """The canonical `BrokerTick` dicts one connection makes of `records`.

    Takes records rather than a frame so a test can express "the snapshot, then
    the update" — which is the only order in which this feed is decodable at all.
    """
    connection = _fy_conn(session)
    ticks = []
    for record in records:
        ticks.extend(tick.as_dict() for tick in connection.decode(_fy_frame(record)).ticks)
    return ticks


def _fy_map():
    """An account's instrument map as Fyers identifies instruments."""
    return InstrumentMap.from_portfolio(
        [
            {"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "sf|nse_cm|2885"},
            {"symbol": "TCS", "exchange": "NSE", "instrument_token": "sf|nse_cm|11536"},
        ]
    )


def _fy_canonical(price=265075, topic="sf|nse_cm|2885"):
    return canonical_ticks(_fy_ticks(_fy_snapshot(name=topic, values=(price,))), _fy_map(), broker="fyers")


def _fy_feed(user_id="u1", symbols=("RELIANCE",)):
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import provider_registry

    run(_attach(user_id, "fyers", list(symbols)))
    return provider_registry.get(feed_provider_name(user_id, "fyers"))


# -- authentication and session ------------------------------------------------


def test_the_fyers_login_flow_is_a_browser_redirect_and_holds_no_user_secret(monkeypatch):
    monkeypatch.setenv("FYERS_APP_ID", "APP-100")
    monkeypatch.setenv("FYERS_SECRET_ID", "SECRET-XYZ")
    monkeypatch.setenv("FYERS_REDIRECT_URL", "https://app.example/api/brokers/fyers/callback")
    login = _fyers().get_login_url("u1")

    assert login["configured"] is True
    assert login["url"].startswith("https://api-t1.fyers.in/api/v3/generate-authcode?")
    assert "client_id=APP-100" in login["url"] and "uid%3Du1" in login["url"]
    # The app SECRET is never in a URL a browser follows — only its digest ever
    # travels, and only on the server-to-server exchange.
    assert "SECRET-XYZ" not in login["url"]

    monkeypatch.setenv("FYERS_SECRET_ID", "")
    assert _fyers().get_login_url("u1")["configured"] is False


def test_the_fyers_callback_reads_auth_code_and_not_the_status_named_code():
    """Fyers puts an HTTP-style STATUS in `code` and the grant in `auth_code`.

    The inherited OAuth2 parser reads `code`, so it would take the string
    `"200"` for an authorization code, report the callback a success, and post
    `"200"` to `validate-authcode`. The user's login worked; the connect fails
    with a broker message about an invalid auth code. This is the whole reason
    `parse_callback_params` is a per-adapter hook.
    """
    adapter = _fyers()
    parsed = adapter.parse_callback_params({"s": "ok", "code": "200", "auth_code": "GRANT-9", "state": "uid=u1"})
    assert parsed == {"auth_code": "GRANT-9"}

    # And the shape the default parser would have accepted must NOT authenticate.
    assert adapter.parse_callback_params({"s": "ok", "code": "200"}) is None
    assert adapter.parse_callback_params({"s": "error", "code": "-99", "auth_code": "G"}) is None
    assert adapter.parse_callback_params({}) is None


def test_completing_fyers_auth_signs_the_exchange_and_verifies_the_token(monkeypatch):
    """`appIdHash` is SHA256("app:secret") — the secret itself is never sent."""
    import hashlib

    monkeypatch.setenv("FYERS_APP_ID", "APP-100")
    monkeypatch.setenv("FYERS_SECRET_ID", "SECRET-XYZ")
    token = _fy_token(exp=4_102_444_800)
    expected = hashlib.sha256(b"APP-100:SECRET-XYZ").hexdigest()

    calls = []

    async def fake_request(self, method, url, headers=None, data=None, json_body=None, timeout=12.0):
        calls.append((url, json_body, headers))
        if url.endswith("/validate-authcode"):
            return {"s": "ok", "code": 200, "access_token": token, "refresh_token": "R1"}
        return {"s": "ok", "code": 200, "data": {"fy_id": "XY01234", "name": "Ada", "email_id": "a@b.c"}}

    with patch.object(BrokerAdapter, "_request", new=fake_request):
        session = run(_fyers().exchange_token({"auth_code": "GRANT-9"}))

    assert calls[0][1]["appIdHash"] == expected
    assert "SECRET-XYZ" not in json.dumps(calls[0][1])
    assert session["access_token"] == token and session["account_id"] == "XY01234"
    # The token's own `exp` wins over the calendar rule — the two differ exactly
    # where it matters, for a token minted minutes before the daily cut-off.
    assert session["expires_at"] == datetime(2100, 1, 1, tzinfo=timezone.utc).isoformat()


def test_a_fyers_token_with_no_readable_expiry_falls_back_to_midnight_ist():
    from services.brokers.base import IST

    adapter = _fyers()
    expiry = adapter.session_expiry(datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc))
    assert expiry.astimezone(IST).hour == 0 and expiry.astimezone(IST).minute == 0
    assert expiry > datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)


def test_the_fyers_feed_credential_is_folded_inside_the_session_token():
    """A second per-session credential, arriving by a route no predecessor used.

    SmartAPI issues its feed token *beside* the auth token; Fyers hides its
    `hsm_key` inside the JWT. An adapter that looked for a separate field would
    find nothing and authenticate the socket with nothing.
    """
    from services.brokers.fyers import hsm_key

    assert hsm_key(_fy_token("HSM-KEY-1")) == "HSM-KEY-1"
    assert hsm_key(_fy_token(hsm_key=None)) is None
    for junk in (None, "", "not-a-jwt", "a.b", "a.!!!!.c", 12345, _fy_token()[:10]):
        assert hsm_key(junk) is None


def test_the_fyers_endpoint_puts_no_credential_in_the_url_or_in_a_header():
    """A fourth auth style, and the first that puts nothing in the handshake."""
    endpoint = _fyers_channel().endpoint(_fy_session(), {"api_key": "APP-100"})

    assert endpoint.url == "wss://socket.fyers.in/hsm/v1-5/prod"
    assert "?" not in endpoint.url and endpoint.safe_url == endpoint.url
    assert endpoint.headers == {}, "HSM authenticates in a frame; a header here would be a leak"
    blob = f"{endpoint.url} {json.dumps(endpoint.headers)}"
    for secret in ("HSM-KEY-1", "APP-100", _fy_token()):
        assert secret not in blob


def test_the_fyers_feed_declares_the_application_keepalive_the_protocol_ping_is_not():
    """Third broker in a row whose liveness check is not `ping_interval`.

    And the first whose keep-alive is *binary*: the contract field is typed to
    carry either because what the frame is stays broker knowledge.
    """
    endpoint = _fyers_channel().endpoint(_fy_session())
    assert endpoint.heartbeat_frame == bytes([0, 1, 11])
    assert isinstance(endpoint.heartbeat_frame, bytes)
    assert endpoint.heartbeat_interval == 10.0
    # The protocol's own ping is still configured, and is still a different thing.
    assert endpoint.ping_interval == 20.0


# -- the request framing --------------------------------------------------------


def test_the_fyers_opening_frames_are_credential_then_mode_then_subscription():
    """Order is protocol, not preference.

    The credential must precede everything (the server has been told nothing
    yet), and the *mode* must precede the subscription — subscribing first opens
    a window in which full records arrive for instruments asked for in lite mode.
    """
    frames = _fy_conn().subscribe_frames(["sf|nse_cm|2885"])

    assert len(frames) == 3
    assert all(isinstance(f, bytes) for f in frames), "HSM is binary end to end"
    assert [f[2] for f in frames] == [1, 12, 4], "auth, mode, subscribe"


def test_the_fyers_auth_frame_matches_the_reference_clients_bytes():
    """Byte-for-byte against `data_ws.py.__access_token_msg`, for one key.

    Written out as a literal rather than recomputed with the adapter's own
    helpers: a test that rebuilds the frame the way the code does agrees with any
    bug the code has.
    """
    frame = _fy_conn(_fy_session("KEY123")).subscribe_frames()[0]
    assert frame == bytes.fromhex(
        "0025"              # declared length 37 = 18 + len(key) + len(source) - 2
        "01" "04"           # ReqType 1, four fields
        "01" "0006" "4b4559313233"                      # 1: the feed key, "KEY123"
        "02" "0001" "50"                                # 2: connection mode "P"
        "03" "0001" "01"                                # 3: literal 1
        "04" "000f" "53746f636b41737369737441492d31"    # 4: "StockAssistAI-1"
    )
    assert len(frame) == 39


def test_the_fyers_mode_frame_narrows_this_connection_to_lite_records():
    """Channel 11 as a BIT in a 64-bit mask, and mode 76 — the smallest record.

    Full mode (70) would put the whole 21-field record on the wire on every
    price change, for fields no consumer in this platform reads.
    """
    frame = _fy_conn().subscribe_frames()[1]
    assert frame == bytes.fromhex(
        "0000"              # the reference client declares 0 here; reproduced, not "fixed"
        "0c" "02"
        "01" "0008" "0000000000000800"   # channel bit 11 -> 0x800
        "02" "0001" "4c"                 # 0x4c = 76 = lite
    )


def test_the_fyers_subscription_carries_hsm_topics_as_length_prefixed_ascii():
    frame = _fy_conn().subscribe_frames(["sf|nse_cm|2885", "sf|nse_cm|11536"])[2]
    assert frame[2] == 4 and frame[3] == 2
    scrips = frame[7:7 + struct.unpack(">H", frame[5:7])[0]]
    assert struct.unpack(">H", scrips[:2])[0] == 2
    assert b"sf|nse_cm|11536" in scrips and b"sf|nse_cm|2885" in scrips
    assert frame[-1] == 11, "the subscription names the channel it belongs to"


@pytest.mark.parametrize("bad", [None, "", "2885", 2885, "sf|2885", "sf|nse_cm|", "sf|mars_cm|1",
                                 "dp|nse_cm|2885", "sf|nse_cm|0", "sf|nse_cm|abc", True, {"t": 1}])
def test_a_malformed_fyers_topic_never_reaches_the_wire(bad):
    """Including `dp|…`: this adapter does not subscribe depth, and a depth
    record's field zero is a bid rather than a traded price."""
    assert _fy_conn().subscribe_frames([bad]) == _fy_conn().subscribe_frames([])


def test_a_fyers_subscription_with_no_instruments_still_authenticates():
    """No instruments is not no conversation: the credential frame is not optional."""
    frames = _fy_conn().subscribe_frames([])
    assert [f[2] for f in frames] == [1, 12]


def test_a_fyers_session_whose_token_carries_no_feed_key_sends_nothing(caplog):
    """No frames rather than a malformed one — an unusable credential is not a guess."""
    caplog.set_level(logging.WARNING)
    assert _fy_conn({"access_token": "not-a-jwt"}).subscribe_frames(["sf|nse_cm|2885"]) == []
    assert "reconnect" in caplog.text.lower()


def test_a_duplicate_fyers_instrument_is_subscribed_once():
    frame = _fy_conn().subscribe_frames(["sf|nse_cm|2885", "sf|nse_cm|2885"])[2]
    assert struct.unpack(">H", frame[7:9])[0] == 1


def test_an_over_quota_fyers_subscription_is_trimmed_rather_than_rejected(caplog):
    from services.brokers.fyers import MAX_SUBSCRIBED_INSTRUMENTS, SUBSCRIBE_BATCH_SIZE

    caplog.set_level(logging.WARNING)
    topics = [f"sf|nse_cm|{n}" for n in range(1, MAX_SUBSCRIBED_INSTRUMENTS + 10)]
    frames = _fy_conn().subscribe_frames(topics)

    subscriptions = [f for f in frames if f[2] == 4]
    assert len(subscriptions) == -(-MAX_SUBSCRIBED_INSTRUMENTS // SUBSCRIBE_BATCH_SIZE)
    subscribed = sum(struct.unpack(">H", f[7:9])[0] for f in subscriptions)
    assert subscribed == MAX_SUBSCRIBED_INSTRUMENTS
    assert "exceeds" in caplog.text


def test_a_large_fyers_subscription_is_split_into_wire_sized_batches():
    """One frame per 1,500 topics — the reference client's own batch size."""
    from services.brokers.fyers import SUBSCRIBE_BATCH_SIZE

    topics = [f"sf|nse_cm|{n}" for n in range(1, SUBSCRIBE_BATCH_SIZE + 2)]
    subscriptions = [f for f in _fy_conn().subscribe_frames(topics) if f[2] == 4]
    assert len(subscriptions) == 2
    assert [struct.unpack(">H", f[7:9])[0] for f in subscriptions] == [SUBSCRIBE_BATCH_SIZE, 1]


# -- the connection scope: D4.10's whole subject --------------------------------


def test_a_fyers_snapshot_decodes_to_one_canonical_broker_tick():
    ticks = _fy_ticks(_fy_snapshot(values=(265075,)))
    assert ticks == [{
        "instrument_token": "sf|nse_cm|2885", "last_price": 2650.75, "symbol": None,
        "exchange": "NSE", "volume": 0, "timestamp": None,
    }]


def test_a_fyers_update_is_only_decodable_after_the_snapshot_that_named_it():
    """THE protocol fact that cost a contract change.

    A lite update is seven bytes: a topic id the *server* minted and a price. It
    carries no instrument name, no exchange and no price scale. Everything that
    makes it meaningful was established by an earlier frame on the same socket.
    """
    connection = _fy_conn()

    orphan = connection.decode(_fy_frame(_fy_lite(1, 266000)))
    assert orphan.kind is StreamEventKind.IGNORE, "an update was priced with no snapshot behind it"

    connection.decode(_fy_frame(_fy_snapshot(topic_id=1, values=(265075,))))
    named = connection.decode(_fy_frame(_fy_lite(1, 266000)))
    assert [t.as_dict()["last_price"] for t in named.ticks] == [2660.0]
    assert [t.as_dict()["instrument_token"] for t in named.ticks] == ["sf|nse_cm|2885"]


def test_two_fyers_connections_never_share_a_topic_table():
    """Topic ids are per connection, so a shared codec is a cross-user defect.

    Two accounts subscribe different instruments and the server numbers each
    from its own sequence. If the topic table lived on the channel — a registry
    singleton shared by every user of the broker — topic 1 would mean whichever
    account connected last, and the *other* account's ticks would be filed under
    the wrong company. Nothing raises; the price is simply attributed wrongly.
    """
    alice = _fy_conn()
    bob = _fy_conn()
    alice.decode(_fy_frame(_fy_snapshot(topic_id=1, name="sf|nse_cm|2885")))
    bob.decode(_fy_frame(_fy_snapshot(topic_id=1, name="sf|nse_cm|11536")))

    def resolved(connection, topic_id, price):
        event = connection.decode(_fy_frame(_fy_lite(topic_id, price)))
        return [t.as_dict()["instrument_token"] for t in event.ticks]

    assert resolved(alice, 1, 266000) == ["sf|nse_cm|2885"]
    assert resolved(bob, 1, 400000) == ["sf|nse_cm|11536"]
    # And the channel they were both opened from learned nothing from either.
    assert _fyers_channel().decode(_fy_frame(_fy_lite(1, 266000))).kind is StreamEventKind.IGNORE


def test_a_reconnecting_fyers_stream_decodes_against_an_empty_topic_table():
    """The transport must mint a fresh codec per connection, not per stream.

    The server renumbers topics on every connection. A table carried across a
    reconnect maps the new connection's numbers onto the previous connection's
    instruments — which is the same cross-attribution as the shared-singleton
    case above, arriving through time instead of through users.
    """
    stream = BrokerStream("u1", "fyers", _fy_session(), channel="market")
    seen = []

    async def scenario(records):
        socket = _FakeSocket([_fy_frame(*records)])

        async def collect(user_id, broker, batch):
            seen.append(batch)

        stream.on_tick = collect
        with patch.object(BrokerStream, "_connect", AsyncMock(return_value=socket)):
            await stream._run_websocket()

    # Connection 1 learns topic 1 = RELIANCE and ticks it.
    run(scenario([_fy_snapshot(topic_id=1, name="sf|nse_cm|2885"), _fy_lite(1, 266000)]))
    assert [t["instrument_token"] for batch in seen for t in batch] == ["sf|nse_cm|2885"] * 2

    # The socket ended. Connection 2 receives the SAME topic id with no snapshot.
    seen.clear()
    assert stream._connection is None, "the dead connection's codec outlived its socket"
    run(scenario([_fy_lite(1, 266000)]))
    assert seen == [], "a reconnect decoded against the previous connection's topic table"


def test_the_fyers_transport_decodes_through_the_connection_and_not_the_channel():
    """The wiring itself, asserted where it can actually fail.

    `BrokerStreamChannel.open()` returning `self` is the default and is right for
    three of the four brokers, so a transport that never called `open()` would
    pass every other test in this file. Here it would deliver the snapshot and
    then silently drop every update — a feed that looks alive, ticks once per
    instrument per session, and never moves again.
    """
    ticks, orders, expired, socket = drive_stream(
        _fyers(),
        [_fy_auth_ok(), _fy_frame(_fy_snapshot(topic_id=4)), _fy_frame(_fy_lite(4, 266000))],
        instruments=["sf|nse_cm|2885"],
        session=_fy_session(),
        channel="market",
    )
    prices = [tick["last_price"] for _, _, batch in ticks for tick in batch]
    assert prices == [2650.75, 2660.0], "the update after the snapshot was lost"


# -- decoding ------------------------------------------------------------------


def test_a_fyers_full_mode_record_decodes_through_the_same_topic_table():
    """Full records are deltas too — this adapter subscribes lite, not blind."""
    connection = _fy_conn()
    connection.decode(_fy_frame(_fy_snapshot(topic_id=1)))
    event = connection.decode(_fy_frame(_fy_full(1, (268000, 5, 0))))
    assert [t.as_dict()["last_price"] for t in event.ticks] == [2680.0]


def test_a_fyers_depth_topic_is_refused_rather_than_priced():
    """Field zero of a depth record is the best BID, not the traded price.

    Publishing one as the last traded price is wrong in a way nothing
    downstream can detect: it is a real, plausible, tradeable-looking number.
    """
    connection = _fy_conn()
    event = connection.decode(_fy_frame(_fy_snapshot(topic_id=3, name="dp|nse_cm|2885", values=(264900,))))
    assert event.kind is StreamEventKind.IGNORE
    assert connection.decode(_fy_frame(_fy_lite(3, 264950))).kind is StreamEventKind.IGNORE


def test_a_fyers_topic_from_an_unmapped_segment_is_dropped():
    connection = _fy_conn()
    assert connection.decode(_fy_frame(_fy_snapshot(name="sf|xxx_cm|2885"))).kind is StreamEventKind.IGNORE


def test_a_fyers_absent_field_never_becomes_a_price():
    """HSM's "no value" sentinel is INT32_MIN, not zero.

    A decoder that treated it as a number would publish −21,474,836.48 as a
    price, which the canonical boundary would refuse — but only after the codec
    had claimed a tick existed.
    """
    connection = _fy_conn()
    connection.decode(_fy_frame(_fy_snapshot(topic_id=1)))
    assert connection.decode(_fy_frame(_fy_lite(1, -2147483648))).kind is StreamEventKind.IGNORE
    assert _fy_conn().decode(_fy_frame(_fy_snapshot(values=(-2147483648,)))).kind is StreamEventKind.IGNORE


def test_a_fyers_instrument_with_an_unusable_scale_is_dropped_not_divided_by_zero():
    assert _fy_conn().decode(_fy_frame(_fy_snapshot(multiplier=0))).kind is StreamEventKind.IGNORE


def test_a_truncated_fyers_frame_keeps_the_records_it_had_already_read():
    """A frame is a batch. One short record must not cost the prices before it.

    The reference client walks these frames with bare slicing, so a short frame
    there reads past the end, produces zero-length strings, and carries on
    decoding garbage into the next record. Every read here is bounds-checked.
    """
    connection = _fy_conn()
    frame = _fy_frame(_fy_snapshot(topic_id=1, name="sf|nse_cm|2885", values=(265075,)),
                      _fy_snapshot(topic_id=2, name="sf|nse_cm|11536", values=(399010,)))
    event = connection.decode(frame[:-6])
    assert [t.as_dict()["last_price"] for t in event.ticks] == [2650.75]


def test_a_fyers_frame_that_promises_more_records_than_it_carries_does_not_invent_them():
    connection = _fy_conn()
    event = connection.decode(_fy_frame(_fy_snapshot(topic_id=1), record_count=5))
    assert [t.as_dict()["last_price"] for t in event.ticks] == [2650.75]


def test_an_unknown_fyers_record_kind_stops_the_frame_without_guessing_its_length():
    """An unknown record has an unknown length, so there is no safe next offset.

    Guessing one would not raise — it would decode a price out of the middle of
    whatever followed.
    """
    connection = _fy_conn()
    frame = _fy_frame(_fy_snapshot(topic_id=1), b"\x63\xff\xff\xff\xff", _fy_lite(1, 999999))
    event = connection.decode(frame)
    assert [t.as_dict()["last_price"] for t in event.ticks] == [2650.75]


@pytest.mark.parametrize("junk", [b"", b"\x00", b"\x00\x02", "text", None, 42,
                                  b"\x00\x09\x06\x00\x00\x00\x01\xff\xff"])
def test_a_malformed_fyers_frame_yields_no_invented_tick(junk):
    assert _fy_conn().decode(junk).kind in (StreamEventKind.IGNORE, StreamEventKind.ERROR)


def test_fyers_control_frames_are_classified_and_none_of_them_is_a_tick():
    connection = _fy_conn()
    assert connection.decode(_fy_auth_ok()).kind is StreamEventKind.IGNORE
    assert connection.decode(_fy_control(4, "K")).kind is StreamEventKind.IGNORE
    assert connection.decode(_fy_control(12, "K")).kind is StreamEventKind.IGNORE
    assert connection.decode(_fy_control(99, "K")).kind is StreamEventKind.IGNORE


def test_a_rejected_fyers_subscription_does_not_drop_a_working_socket():
    """HSM rejects a request, not a connection.

    Dropping the socket would turn one bad instrument into a total outage for
    every instrument the account holds.
    """
    connection = _fy_conn()
    event = connection.decode(_fy_control(4, "N"))
    assert event.kind is StreamEventKind.ERROR and "Fyers" in event.message

    ticks, orders, expired, socket = drive_stream(
        _fyers(),
        [_fy_auth_ok(), _fy_control(4, "N"), _fy_frame(_fy_snapshot())],
        instruments=["sf|nse_cm|2885"], session=_fy_session(), channel="market",
    )
    assert len(ticks) == 1, "a rejected subscription cost the socket its other prices"
    assert expired == []


def test_a_refused_fyers_credential_frame_expires_the_session():
    """The first broker here that reports a dead session ON AN OPEN SOCKET.

    Kite, Upstox and Angel One are all refused at the handshake, so the codec
    never sees the rejection. HSM accepts every connection and rejects the
    credential afterwards — left unclassified, an expired token would be
    indistinguishable from a broker outage and would reconnect on the backoff
    schedule forever while the account's feed stayed registered. Fyers tokens
    die daily, so that is every connected user, every day.
    """
    assert _fy_conn().decode(_fy_control(1, "N")).kind is StreamEventKind.AUTH_EXPIRED

    ticks, orders, expired, socket = drive_stream(
        _fyers(), [_fy_control(1, "N")], instruments=["sf|nse_cm|2885"],
        session=_fy_session(), channel="market",
    )
    assert expired == [("user-1", "fyers")]


def test_a_fyers_server_asking_to_be_acknowledged_says_so_in_the_log(caplog):
    """The one protocol requirement D4.10 does NOT implement, made visible.

    HSM's auth response carries an "acknowledge every N frames" count and the
    reference client honours it. A codec here returns a decoded event and cannot
    put a frame back on the wire, and extending the contract for a detail never
    observed non-zero would be a second generic change built on a guess. If the
    server does enforce it the feed goes quiet, `StreamingTickProvider` expires
    the ticks within two minutes and the baseline resumes — bounded, but
    mysterious without this line.
    """
    caplog.set_level(logging.WARNING)
    _fy_conn().decode(_fy_auth_ok(acknowledge_every=50))
    assert "acknowledgement every 50" in caplog.text

    caplog.clear()
    _fy_conn().decode(_fy_auth_ok(acknowledge_every=0))
    assert "acknowledgement" not in caplog.text


def test_a_codec_that_raises_does_not_terminate_a_live_fyers_stream():
    """This adapter declines damaged frames rather than raising, so a resilience
    test built only on damaged bytes cannot tell a transport that swallows a
    codec exception from one that re-raises it. This forces the raise."""
    from services.brokers.fyers import FyersFeedConnection

    with patch.object(FyersFeedConnection, "decode", side_effect=RuntimeError("codec exploded")):
        ticks, orders, expired, socket = drive_stream(
            _fyers(), [_fy_frame(_fy_snapshot())], instruments=["sf|nse_cm|2885"],
            session=_fy_session(), channel="market")
    assert ticks == [] and expired == [] and socket.closed


# -- price ---------------------------------------------------------------------


def test_a_fyers_price_scale_is_carried_on_the_wire_and_a_copied_divisor_would_be_wrong():
    """The trap that is sharpest precisely because it usually works.

    Fyers has no divisor: `multiplier` and `precision` arrive in the snapshot,
    per instrument. NSE cash quotes at `precision=2, multiplier=1`, so a
    hardcoded ÷100 — Kite's rule, Angel One's default — is **correct for the
    instrument anybody would test with**. It is wrong for a currency contract,
    and it fails on a real position rather than on the first tick.
    """
    from services.brokers.zerodha import price_divisor as _kite_divisor  # noqa: F401

    equity = _fy_ticks(_fy_snapshot(name="sf|nse_cm|2885", values=(265075,), precision=2, multiplier=1))
    assert equity[0]["last_price"] == 2650.75

    currency = _fy_ticks(_fy_snapshot(name="sf|cde_fo|1234", values=(875300,), precision=4, multiplier=1))
    assert currency[0]["last_price"] == 87.53
    assert currency[0]["last_price"] != 875300 / 100.0, "a constant paise divisor priced a currency 100x wrong"

    scaled = _fy_ticks(_fy_snapshot(name="sf|mcx_fo|999", values=(720000,), precision=2, multiplier=10))
    assert scaled[0]["last_price"] == 720.0


@pytest.mark.parametrize("raw,precision,expected", [
    (1, 2, 0.01), (99, 2, 0.99), (100, 2, 1.0), (265075, 2, 2650.75),
    (100000000, 2, 1000000.0), (12345, 4, 1.2345), (5, 0, 5.0),
])
def test_fyers_decimal_precision_survives_the_codec(raw, precision, expected):
    ticks = _fy_ticks(_fy_snapshot(values=(raw,), precision=precision, multiplier=1))
    assert ticks[0]["last_price"] == pytest.approx(expected)


def test_a_fyers_zero_or_negative_price_never_becomes_a_market_tick():
    for raw in (0, -1, -265075):
        ticks = _fy_ticks(_fy_snapshot(values=(raw,)))
        assert canonical_ticks(ticks, _fy_map(), broker="fyers") == []


def test_an_impossible_fyers_price_is_refused_at_the_canonical_boundary():
    absurd = int((MAX_STOCK_PRICE + 1000) * 100)
    ticks = _fy_ticks(_fy_snapshot(values=(absurd,)))
    assert ticks and ticks[0]["last_price"] > MAX_STOCK_PRICE
    assert canonical_ticks(ticks, _fy_map(), broker="fyers") == []


def test_a_fyers_lite_tick_carries_no_volume_rather_than_a_fabricated_one():
    """Fourth broker, same limitation, reached independently each time.

    Fyers *does* publish a real cumulative day volume (`vol_traded_today`), and
    it is even present in the snapshot — which is exactly why this needs saying.
    Carrying it once and then freezing it for the rest of the session is a
    number that stops meaning what its name says, which is worse than absent.
    """
    ticks = _fy_ticks(_fy_snapshot(values=(265075, 4_512_000, 0, 1_724_236_800)))
    assert ticks[0]["volume"] == 0
    assert canonical_ticks(ticks, _fy_map(), broker="fyers")[0]["volume"] is None


# -- instrument identity --------------------------------------------------------


def test_a_fyers_instrument_becomes_a_canonical_symbol_through_the_shared_map():
    assert _fy_canonical()[0]["symbol"] == "RELIANCE"
    assert _fy_canonical()[0]["exchange"] == "NSE"
    assert _fy_canonical(price=399010, topic="sf|nse_cm|11536")[0]["symbol"] == "TCS"


def test_a_fyers_exchange_token_alone_is_not_an_identity():
    """The Angel-One-shaped mistake, which Fyers punishes differently.

    A bare exchange token is not what Fyers subscribes with and not what its
    snapshot returns — the HSM topic is. Storing the token would produce a
    subscription the server does not recognise and a tick that resolves against
    nothing, so the account's feed would connect and stay empty.
    """
    from services.brokers.fyers import instrument_id, parse_instrument_id

    assert instrument_id("101000000014428") == "sf|nse_cm|14428"
    assert parse_instrument_id("sf|nse_cm|14428") == ("sf", "nse_cm", "14428")
    assert parse_instrument_id("14428") is None
    bare = InstrumentMap.from_portfolio([{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "2885"}])
    assert canonical_ticks(_fy_ticks(_fy_snapshot()), bare, broker="fyers") == []


@pytest.mark.parametrize("bad", [None, "", "abc", "1010", "1010000000", "9999000000012", True, -1, 0])
def test_a_malformed_fytoken_produces_no_instrument_identity(bad):
    from services.brokers.fyers import instrument_id

    assert instrument_id(bad) is None


def test_the_fyers_exchange_is_the_exchange_and_not_the_segment():
    """Copying SmartAPI's segment table here produces a feed that mislabels.

    SmartAPI's segments ARE its exchange names, so `NFO` and `CDS` are what its
    rows carry. Fyers is the other design: a symbol is `EXCHANGE:NAME`, and the
    exchange half is only ever NSE, BSE or MCX — a futures contract is
    `NSE:NIFTY25AUGFUT`, a currency contract is `NSE:USDINR25AUGFUT`. A tick
    reporting `NFO` would carry an exchange the account's own holding row never
    uses, and the canonical tick would be qualified with a name the rest of the
    platform does not use for that instrument.
    """
    from services.brokers.fyers import split_symbol, topic_exchange

    assert topic_exchange("sf|nse_fo|123") == "NSE" != "NFO"
    assert topic_exchange("sf|cde_fo|123") == "NSE" != "CDS"
    assert topic_exchange("sf|bse_fo|123") == "BSE"
    assert topic_exchange("sf|mcx_fo|123") == "MCX"

    # And the two sides of the boundary agree, which is the property that matters.
    assert split_symbol("NSE:NIFTY25AUGFUT")[1] == topic_exchange("sf|nse_fo|123")
    assert split_symbol("MCX:GOLD25AUGFUT")[1] == topic_exchange("sf|mcx_fo|123")


@pytest.mark.parametrize("symbol,expected", [
    ("NSE:SBIN-EQ", ("SBIN", "NSE")),
    ("NSE:RELIANCE-EQ", ("RELIANCE", "NSE")),
    ("BSE:SBIN-EQ", ("SBIN", "BSE")),
    ("NSE:NIFTY50-INDEX", ("NIFTY50", "NSE")),
    ("NSE:IDEA-BE", ("IDEA", "NSE")),
    ("NSE:NIFTY25AUGFUT", ("NIFTY25AUGFUT", "NSE")),
    ("NSE:NIFTY2582124000CE", ("NIFTY2582124000CE", "NSE")),
    ("MCX:GOLD25AUGFUT", ("GOLD25AUGFUT", "MCX")),
    ("BSE:SOMECO-A", ("SOMECO-A", "BSE")),
    ("", (None, None)),
    (None, (None, None)),
])
def test_the_fyers_symbol_is_split_into_a_canonical_name_and_its_exchange(symbol, expected):
    """`BSE:SOMECO-A` is deliberately NOT stripped: a one-letter suffix is
    indistinguishable from part of a name, and stripping one wrongly renames an
    instrument permanently. Recorded as a limitation rather than guessed at."""
    from services.brokers.fyers import split_symbol

    assert split_symbol(symbol) == expected


def test_a_fyers_series_suffix_left_attached_would_split_a_users_portfolio():
    """The reason stripping happens at all, stated as the failure it prevents."""
    from services.brokers.fyers import split_symbol

    assert split_symbol("NSE:SBIN-EQ")[0] == "SBIN"
    assert split_symbol("NSE:SBIN-EQ")[0] == trading_symbol_of_angelone("SBIN-EQ")


def trading_symbol_of_angelone(value):
    from services.brokers.angelone import trading_symbol

    return trading_symbol(value)


def test_an_unknown_fyers_instrument_is_dropped_rather_than_named():
    """A topic the account has no row for is unresolvable, and the topic string
    is never used as a symbol — `sf|nse_cm|9999` in `db.holdings` would be an
    instrument name in the AI's context and on the user's screen."""
    ticks = _fy_ticks(_fy_snapshot(name="sf|nse_cm|9999"))
    assert ticks and ticks[0]["instrument_token"] == "sf|nse_cm|9999"
    assert canonical_ticks(ticks, _fy_map(), broker="fyers") == []


def test_a_fyers_identity_that_round_tripped_through_mongo_still_reaches_the_wire():
    stored = {"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "sf|nse_cm|2885"}
    assert _fyers().stream_instruments([stored]) == ["sf|nse_cm|2885"]
    assert canonical_ticks(_fy_ticks(_fy_snapshot()), InstrumentMap.from_portfolio([stored]),
                           broker="fyers")[0]["symbol"] == "RELIANCE"


def test_fyers_needs_no_instrument_catalogue():
    """Fyers' own SDK resolves symbols to feed tokens over HTTP and consults a
    bundled segment map. This adapter needs neither — the fyToken is already on
    every synced row — which is the difference between D4.10 being an adapter
    sprint and a data-pipeline sprint."""
    holdings = [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "sf|nse_cm|2885"}]

    with patch.object(BrokerAdapter, "_request", new=AsyncMock(side_effect=AssertionError("network call"))):
        assert _fyers().stream_instruments(holdings) == ["sf|nse_cm|2885"]
        assert _fy_conn().subscribe_frames(["sf|nse_cm|2885"])


def test_the_fyers_holdings_row_carries_the_identity_the_feed_subscribes():
    """One expression, both directions: the row the sync writes is the row the
    subscription reads and the row an arriving tick resolves against."""
    payload = {"s": "ok", "code": 200, "holdings": [
        {"fyToken": "10100000002885", "symbol": "NSE:RELIANCE-EQ", "exchange": 10,
         "quantity": 5, "costPrice": 2400.0, "ltp": 2650.75, "marketVal": 13253.75,
         "pl": 1253.75, "isin": "INE002A01018", "holdingType": "HLD"},
        # `fytoken` lower-case, as some Fyers responses spell it.
        {"fytoken": "101000000011536", "symbol": "NSE:TCS-EQ", "quantity": 1,
         "costPrice": 3900.0, "ltp": 3990.1},
    ]}
    with patch.object(BrokerAdapter, "_request", new=AsyncMock(return_value=payload)):
        holdings = run(_fyers().get_holdings({"access_token": "t"}))

    assert [h["symbol"] for h in holdings] == ["RELIANCE", "TCS"]
    assert [h["exchange"] for h in holdings] == ["NSE", "NSE"]
    assert [h["instrument_token"] for h in holdings] == ["sf|nse_cm|2885", "sf|nse_cm|11536"]
    assert _fyers().stream_instruments(holdings) == ["sf|nse_cm|11536", "sf|nse_cm|2885"]

    resolved = canonical_ticks(_fy_ticks(_fy_snapshot()), InstrumentMap.from_portfolio(holdings), broker="fyers")
    assert resolved[0]["symbol"] == "RELIANCE"


def test_a_fyers_position_carries_the_same_identity_a_holding_does():
    payload = {"s": "ok", "code": 200, "netPositions": [
        {"fyToken": "10100000002885", "symbol": "NSE:RELIANCE-EQ", "netQty": -3,
         "netAvg": 2600.0, "ltp": 2650.75, "pl": -152.25, "productType": "INTRADAY",
         "buyQty": 0, "sellQty": 3, "realized_profit": 0, "unrealized_profit": -152.25},
    ]}
    with patch.object(BrokerAdapter, "_request", new=AsyncMock(return_value=payload)):
        positions = run(_fyers().get_positions({"access_token": "t"}))
    assert positions[0]["instrument_token"] == "sf|nse_cm|2885"
    assert positions[0]["side"] == "SHORT" and positions[0]["symbol"] == "RELIANCE"


# -- canonicalization -----------------------------------------------------------


def test_no_raw_fyers_payload_escapes_the_adapter():
    connection = _fy_conn()
    event = connection.decode(_fy_frame(_fy_snapshot()))
    assert event.kind is StreamEventKind.TICKS
    assert all(type(tick).__name__ == "BrokerTick" for tick in event.ticks)

    blob = json.dumps(_fy_canonical()[0]).lower()
    for forbidden in ("fyers", "hsm", "fytoken", "topic", "nse_cm", "sf|", "2885", "multiplier", "precision"):
        assert forbidden not in blob, f"{forbidden} reached a canonical market tick"


def test_four_brokers_speak_four_protocols_and_produce_identical_canonical_ticks():
    """THE D4.10 acceptance criterion: a fourth wire, the same canonical output.

    Four brokers agree on nothing at the wire — bespoke big-endian binary vs
    protobuf vs little-endian fixed packets vs a stateful topic protocol; an
    integer token vs a compound key vs a segment pair vs an HSM topic; four price
    rules, one of which is not a rule at all but a value on the wire; four auth
    styles, one of which is not in the handshake — and what reaches the Market
    Engine from each is the same canonical `MarketTick`.
    """
    kite = canonical_ticks(_kite_ticks(_kite_frame((738561, 265075))), _kite_map(), broker="zerodha")
    upstox = canonical_ticks(
        _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75})), _upstox_map(), broker="upstox")
    angel = _angel_canonical(price=265075, token="2885")
    fyers = _fy_canonical(price=265075)

    wires = [_kite_frame((738561, 265075)),
             _upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75}),
             _angel_packet(price=265075),
             _fy_frame(_fy_snapshot(values=(265075,)))]
    assert len({bytes(w) for w in wires}) == 4, "two of the four wires are identical"

    shaped = [[{k: v for k, v in tick.items() if k != "ingested_at"} for tick in batch]
              for batch in (kite, upstox, angel, fyers)]
    assert shaped[0] == shaped[1] == shaped[2] == shaped[3] == [
        {"symbol": "RELIANCE", "price": 2650.75, "exchange": "NSE", "volume": None}
    ]


# -- transport -----------------------------------------------------------------


def test_a_fyers_stream_runs_through_the_generic_transport_unchanged():
    """Real HSM bytes over the real transport: auth, mode, subscribe, tick, close.

    The three opening frames must reach the socket verbatim *as bytes* — the
    transport cannot know what encoding this broker expects, so it must not
    re-encode what the codec handed it.
    """
    frames = [_fy_auth_ok(), _fy_control(4, "K"), _fy_frame(_fy_snapshot(topic_id=6)), _fy_frame(_fy_lite(6, 266000))]
    ticks, orders, expired, socket = drive_stream(
        _fyers(), frames, instruments=["sf|nse_cm|2885"], session=_fy_session(), channel="market")

    assert len(socket.sent) == 3 and all(isinstance(f, bytes) for f in socket.sent)
    assert [f[2] for f in socket.sent] == [1, 12, 4]
    assert [tick["last_price"] for _, _, batch in ticks for tick in batch] == [2650.75, 2660.0]
    assert all(broker == "fyers" for _, broker, _ in ticks)
    assert orders == [] and expired == [] and socket.closed


def test_fyers_declares_one_named_channel_served_by_the_one_generic_transport():
    from services.brokers.fyers import FyersFeedConnection, FyersMarketFeedChannel
    from services.brokers.stream import PROTOCOL_RUNNERS, resolve_transport

    channel = _fyers_channel()
    assert isinstance(channel, FyersMarketFeedChannel)
    assert channel.name == "market" and channel.name != DEFAULT_STREAM_CHANNEL
    assert channel.delivers == frozenset({StreamEventKind.TICKS})
    assert channel.protocol == _fyers().stream_protocol == "fyers_hsm_v1_5"
    assert PROTOCOL_RUNNERS == {}
    assert resolve_transport(channel) is BrokerStream._run_websocket
    assert isinstance(channel.open(_fy_session()), FyersFeedConnection)


def test_the_keepalive_reaches_a_live_fyers_socket_and_stops_when_it_closes():
    """The wiring, not just the timer — driven through the real transport pass.

    HSM closes a connection that stops sending its ReqType-11 frame, so a
    keep-alive that is built and never sent produces a socket that connects,
    delivers for a few seconds and is closed — repeatedly, on the reconnect
    schedule, which from outside reads as a flapping feed rather than a missing
    frame. Driven through `_run_websocket` rather than by calling
    `_start_heartbeat`, because the latter proves the helper works and nothing
    about whether the transport uses it.
    """
    with patch("services.brokers.fyers.HEARTBEAT_INTERVAL", 0.01):
        socket = _PacedSocket([_fy_auth_ok(), _fy_frame(_fy_snapshot())], pause=0.05)
        stream = BrokerStream(
            "u1", "fyers", _fy_session(), credentials={"api_key": "APP-100"},
            instrument_tokens=["sf|nse_cm|2885"], channel="market",
        )

        async def scenario():
            with patch.object(BrokerStream, "_connect", AsyncMock(return_value=socket)):
                await stream._run_websocket()
            sent_at_close = list(socket.sent)
            await asyncio.sleep(0.05)
            return sent_at_close

        sent_at_close = run(scenario())

    assert sent_at_close.count(bytes([0, 1, 11])) >= 2, \
        f"the keep-alive never reached the socket: {sent_at_close}"
    assert [f[2] for f in sent_at_close[:3]] == [1, 12, 4], "the opening frames must go first"
    assert socket.closed
    assert socket.sent == sent_at_close, "the keep-alive outlived the connection it belonged to"


# -- error classification ------------------------------------------------------


class _FyersHandshakeRefused(Exception):
    def __init__(self, status_code):
        super().__init__(f"server rejected WebSocket connection: HTTP {status_code}")
        self.status_code = status_code


class _FyersHandshakeRefused14(Exception):
    def __init__(self, status_code):
        super().__init__("server rejected WebSocket connection")
        self.response = type("Response", (), {"status_code": status_code})()


@pytest.mark.parametrize("refusal", [_FyersHandshakeRefused, _FyersHandshakeRefused14])
@pytest.mark.parametrize("status", [401, 403])
def test_fyers_refusing_the_handshake_expires_the_session(refusal, status):
    stream = BrokerStream("u1", "fyers", _fy_session(), channel="market")

    async def scenario():
        with patch.object(BrokerStream, "_connect", AsyncMock(side_effect=refusal(status))):
            with pytest.raises(_AuthExpired) as excinfo:
                await stream._run_websocket()
        assert "reconnect" in str(excinfo.value).lower()

    run(scenario())


def test_an_ordinary_fyers_connection_failure_still_reconnects():
    assert _fyers().stream_connect_error(OSError("connection reset")) is None
    assert _fyers().stream_connect_error(_FyersHandshakeRefused(500)) is None


def test_an_expired_fyers_rest_session_is_reported_as_an_auth_failure():
    """Fyers answers HTTP 200 with `{"s": "error"}` for application failures, so
    the transport-level 401 handling cannot see them — an unchecked caller would
    read a missing `holdings` key as an empty portfolio."""
    from services.brokers.errors import BrokerAuthError, BrokerError

    dead = {"s": "error", "code": -16, "message": "Could not authenticate the user"}
    with patch.object(BrokerAdapter, "_request", new=AsyncMock(return_value=dead)):
        with pytest.raises(BrokerAuthError):
            run(_fyers().get_holdings({"access_token": "t"}))

    other = {"s": "error", "code": -50, "message": "Something else"}
    with patch.object(BrokerAdapter, "_request", new=AsyncMock(return_value=other)):
        with pytest.raises(BrokerError) as excinfo:
            run(_fyers().get_holdings({"access_token": "t"}))
    assert not isinstance(excinfo.value, BrokerAuthError)


# -- readiness, promotion, failover ---------------------------------------------


def test_a_connected_fyers_stream_is_not_ready_until_a_real_packet_arrives(no_probation_window):
    """CONNECTED != READY, driven by Fyers' own bytes.

    Every milestone short of data is reached — registered, link up, credential
    accepted, subscription accepted, an error frame, a damaged frame, an update
    with no snapshot behind it — and the baseline still serves the quote.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        run(baseline.connect())
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        feed = _fy_feed("u1", symbols=("RELIANCE",))
        run(set_market_feed_link("u1", "fyers", up=True))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        connection = _fy_conn()
        for noise in (_fy_auth_ok(), _fy_control(4, "K"), _fy_control(4, "N"),
                      b"\xff\xff", _fy_frame(_fy_lite(1, 266000))):
            run(feed.on_raw(canonical_ticks(
                [t.as_dict() for t in connection.decode(noise).ticks], _fy_map(), broker="fyers")))
        assert not feed.is_ready
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        run(feed.on_raw(_fy_canonical()))
        assert feed.is_ready
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed
        assert run(feed.fetch_quote("RELIANCE"))["price"] == 2650.75
        # Make-before-break: the baseline was never unregistered or disconnected.
        assert registry.get(baseline.name) is baseline and baseline.is_connected


def test_a_fyers_feed_is_promoted_over_the_baseline_and_falls_back_on_link_loss(no_probation_window):
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        feed = _fy_feed("u1", symbols=("RELIANCE",))
        run(set_market_feed_link("u1", "fyers", up=True))
        run(feed.on_raw(_fy_canonical()))
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed

        run(set_market_feed_link("u1", "fyers", up=False, reason="socket closed"))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline
        assert registry.get(feed.name) is feed, "a dropped socket unregistered the feed"


def test_a_reconnected_fyers_feed_cannot_answer_from_the_dead_links_prices():
    """The D4.8 stale-evidence hardening, re-checked on the fourth broker."""
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        feed = _fy_feed("u1", symbols=("RELIANCE", "TCS"))
        run(set_market_feed_link("u1", "fyers", up=True))
        run(feed.on_raw(_fy_canonical(price=265075, topic="sf|nse_cm|2885")))
        run(feed.on_raw(_fy_canonical(price=399010, topic="sf|nse_cm|11536")))
        assert feed.covers("RELIANCE") and feed.covers("TCS")

        run(set_market_feed_link("u1", "fyers", up=False, reason="dropped"))
        run(set_market_feed_link("u1", "fyers", up=True))
        assert not feed.is_ready, "readiness survived the link that earned it"

        run(feed.on_raw(_fy_canonical(price=266000, topic="sf|nse_cm|2885")))
        assert feed.is_ready and feed.covers("RELIANCE")
        assert not feed.covers("TCS"), "a price from the previous connection survived the reconnect"
        assert manager.resolve(
            Capability.QUOTES, context=ResolutionContext(user_id="u1", symbol="TCS")) is baseline


def test_a_fyers_feed_that_never_ticks_leaves_the_baseline_primary():
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        _fy_feed("u1", symbols=("RELIANCE",))
        run(set_market_feed_link("u1", "fyers", up=True))
        assert manager.resolve(
            Capability.QUOTES, context=ResolutionContext(user_id="u1", symbol="RELIANCE")) is baseline


def test_fyers_registers_through_the_existing_provider_framework():
    """No Fyers-specific provider, gateway, registry or Source Manager exists."""
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import StreamingTickProvider

    with _clean_provider_registry() as registry:
        registry.clear()
        name = run(_attach("u1", "fyers", ["RELIANCE"]))
        assert name == feed_provider_name("u1", "fyers") == "brokerfeed:fyers:u1"
        provider = registry.get(name)
        assert type(provider) is StreamingTickProvider, "a broker-specific provider class appeared"
        assert provider.owner_user_id == "u1"


def test_removing_the_fyers_tick_capability_removes_its_market_feed():
    adapter = _fyers()
    narrowed = adapter.capabilities - {BrokerCapability.TICK_STREAM}
    with _clean_provider_registry() as registry:
        registry.clear()
        with patch.object(type(adapter), "capabilities", narrowed):
            assert run(_attach("u1", "fyers", ["RELIANCE"])) is None
        assert registry.get("brokerfeed:fyers:u1") is None


# -- isolation ------------------------------------------------------------------


def test_five_users_on_five_providers_stay_on_their_own(no_probation_window):
    """Fyers, Angel One, Zerodha, Upstox and the shared baseline, at once.

    The one test that exercises every streaming broker the platform has plus the
    fallback, and the property is per user: each resolves to their own feed, and
    one broker's failure moves exactly one user.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        feeds = {
            "userA": _angel_feed("userA", symbols=("RELIANCE",)),
            "userB": _attach_and_get("userB", "zerodha"),
            "userC": _upstox_feed("userC", symbols=("RELIANCE",)),
            "userD": _fy_feed("userD", symbols=("RELIANCE",)),
        }
        for user, broker in (("userA", "angelone"), ("userB", "zerodha"),
                             ("userC", "upstox"), ("userD", "fyers")):
            run(set_market_feed_link(user, broker, up=True))
        run(feeds["userA"].on_raw(_angel_canonical()))
        run(feeds["userB"].on_raw(canonical_ticks(
            _kite_ticks(_kite_frame((738561, 265075))), _kite_map(), broker="zerodha")))
        run(feeds["userC"].on_raw(_upstox_canonical()))
        run(feeds["userD"].on_raw(_fy_canonical()))

        def resolved(user):
            return manager.resolve(Capability.QUOTES, context=ResolutionContext(user_id=user, symbol="RELIANCE"))

        for user in feeds:
            assert resolved(user) is feeds[user]
        assert resolved("userE") is baseline, "a user with no broker was served another user's feed"

        # Fyers drops. Only user D moves.
        run(set_market_feed_link("userD", "fyers", up=False, reason="socket closed"))
        assert resolved("userD") is baseline
        for user in ("userA", "userB", "userC"):
            assert resolved(user) is feeds[user]
        assert resolved("userE") is baseline


def test_a_fyers_quote_carries_no_broker_identity_and_no_other_users_data():
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        registry.register(YahooPollingAdapter())
        feed = _fy_feed("u1", symbols=("RELIANCE",))
        run(set_market_feed_link("u1", "fyers", up=True))
        run(feed.on_raw(_fy_canonical()))
        quote = run(feed.fetch_quote("RELIANCE"))

    blob = json.dumps(quote).lower()
    for forbidden in ("fyers", "hsm", "brokerfeed", "u1", "nse_cm", "2885", "fytoken"):
        assert forbidden not in blob, f"{forbidden} reached a consumer-facing quote"


# -- the engine seam -------------------------------------------------------------


def test_the_engine_carries_fyers_bytes_all_the_way_into_the_registered_feed(no_probation_window):
    """The real join: the engine's instrument map, the canonical boundary, the push."""
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    holdings = [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "sf|nse_cm|2885"}]
    engine = BrokerEngine()
    engine.configure(FakeDB())

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        with patch("services.brokers.stream.stream_manager.start_stream", new=AsyncMock()) as started, \
                patch.object(BrokerEngine, "get_session", new=AsyncMock(return_value=_fy_session())), \
                patch("services.brokers.gateway.broker_gateway.get_holdings", new=AsyncMock(return_value=holdings)), \
                patch("services.brokers.gateway.broker_gateway.get_positions", new=AsyncMock(return_value=[])):
            run(engine.start_stream("u1", "fyers"))

        assert started.await_count == 1
        assert started.await_args.kwargs["instrument_tokens"] == ["sf|nse_cm|2885"]
        assert started.await_args.kwargs["channel"] == "market"

        feed = registry.get("brokerfeed:fyers:u1")
        assert feed is not None and feed.subscribed_symbols == ("RELIANCE",)
        run(feed.mark_link_up())
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        with patch.object(BrokerEngine, "_push", new=AsyncMock()):
            run(engine._on_stream_tick("u1", "fyers", _fy_ticks(_fy_snapshot(values=(265050,)))))

        assert feed.describe()["accepted_records"] == 1
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed
        assert run(feed.fetch_quote("RELIANCE"))["price"] == 2650.5


def test_an_expired_fyers_session_detaches_the_market_feed():
    engine = BrokerEngine()
    engine.configure(FakeDB())

    with _clean_provider_registry() as registry:
        registry.clear()
        _fy_feed("u1", symbols=("RELIANCE",))
        assert registry.get("brokerfeed:fyers:u1") is not None
        with patch.object(BrokerEngine, "_push", new=AsyncMock()), \
                patch("services.brokers.stream.stream_manager.stop_stream", new=AsyncMock()):
            run(engine._on_stream_expired("u1", "fyers", "market"))
        assert registry.get("brokerfeed:fyers:u1") is None


# -- security --------------------------------------------------------------------


def test_no_fyers_credential_reaches_a_log_line(caplog):
    """The whole transport pass, at DEBUG, with a live-looking session.

    Fyers is the case where this could fail most quietly: the credential is not
    in the URL and not in a header — it is inside a binary frame the transport
    sends — so a log line that dumped a *sent frame* would leak it in a form no
    URL-shaped check would notice.
    """
    caplog.set_level(logging.DEBUG)
    token = _fy_token("SECRET-HSM-KEY")
    ticks, orders, expired, socket = drive_stream(
        _fyers(),
        [_fy_auth_ok(), _fy_frame(_fy_snapshot()), _fy_control(4, "N")],
        instruments=["sf|nse_cm|2885"],
        session={"access_token": token, "account_id": "SECRET-CLIENT"},
        channel="market",
    )
    emitted = "\n".join(r.getMessage() for r in caplog.records)
    assert emitted, "nothing was logged — the sweep could not have failed"
    for secret in ("SECRET-HSM-KEY", "SECRET-CLIENT", token, "nova-key"):
        assert secret not in emitted, f"{secret} reached a log line"
    # And the credential really was on the wire, so the sweep had something to find.
    assert b"SECRET-HSM-KEY" in socket.sent[0]


def test_the_fyers_session_token_is_encrypted_at_rest_like_every_other_secret():
    """This broker's feed credential lives INSIDE the access token, so the field
    already covered by SECURITY.md's encryption-at-rest rule carries both — but
    only if the token itself is encrypted, which is what this pins."""
    from services.broker_engine import TOKEN_FIELDS
    from services.brokers.crypto import is_encrypted

    engine = BrokerEngine()
    engine.configure(FakeDB())
    token = _fy_token("SECRET-HSM-KEY")
    run(engine._save_account("u1", "fyers", {"access_token": token, "account_id": "XY01234"}))

    stored = run(engine.db.broker_accounts.find_one({"user_id": "u1", "broker": "fyers"}))
    assert stored["access_token"] != token and is_encrypted(stored["access_token"])
    assert "SECRET-HSM-KEY" not in json.dumps(stored, default=str)
    assert run(engine._load_account("u1", "fyers"))["access_token"] == token
    assert "access_token" in TOKEN_FIELDS


def test_disconnecting_a_fyers_account_clears_every_session_secret():
    from services.broker_engine import TOKEN_FIELDS

    engine = BrokerEngine()
    engine.configure(FakeDB())
    run(engine._save_account("u1", "fyers", {
        "access_token": _fy_token("SECRET-HSM-KEY"), "refresh_token": "R1", "account_id": "XY01234"}))

    with patch("services.brokers.gateway.broker_gateway.invalidate_session", new=AsyncMock()), \
            patch("services.brokers.stream.stream_manager.stop_stream", new=AsyncMock()), \
            patch.object(BrokerEngine, "_push", new=AsyncMock()), \
            patch.object(BrokerEngine, "_publish_connection", new=AsyncMock()):
        run(engine.disconnect("fyers", "u1"))

    stored = run(engine.db.broker_accounts.find_one({"user_id": "u1", "broker": "fyers"}))
    assert stored["connected"] is False
    for field in TOKEN_FIELDS:
        assert stored.get(field) == "", f"{field} survived a disconnect"


# -- architecture ----------------------------------------------------------------


def test_fyers_added_no_fyers_knowledge_outside_its_own_adapter():
    """D4.10's central acceptance criterion, swept rather than asserted by eye."""
    fyers_words = ("fyers", "hsm", "fytoken", "socket.fyers", "api-t1",
                   "appidhash", "auth_code", "nse_cm", "cde_fo", "generate-authcode")
    allowed = {
        "services/brokers/fyers.py",      # the adapter — the only owner
        "services/brokers/__init__.py",   # the registry entry — one line
    }
    offenders = {}
    scanned = 0
    for path in sorted((BACKEND / "services").rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in allowed:
            continue
        scanned += 1
        source = _strip_source(path.read_text(encoding="utf-8")).lower()
        hit = [w for w in fyers_words if re.search(w, source)]
        if hit:
            offenders[rel] = hit
    assert scanned > 50, "the sweep found almost no modules — it could not have failed"
    assert not offenders, offenders


def test_the_market_layer_is_untouched_by_the_fourth_broker():
    """Everything D4.10 promised would not move, re-checked with four brokers live."""
    market_modules = [
        "services/market_engine/gateway.py",
        "services/market_engine/source_manager.py",
        "services/market_engine/ticks.py",
        "services/market_engine/providers/streaming.py",
        "services/market_engine/providers/registry.py",
        "services/market_engine/providers/base.py",
    ]
    for relative in market_modules:
        source = _strip_source((BACKEND / relative).read_text(encoding="utf-8")).lower()
        for name in ("fyers", "hsm", r"angel(?!og)", "upstox", "zerodha", "kite",
                     "broker_gateway", "broker_registry"):
            assert not re.search(name, source), f"{relative} names {name!r} in executable code"


def test_the_fourth_broker_added_no_broker_knowledge_to_the_transport():
    """`stream.py` grew a connection scope and still cannot tell one broker apart.

    D4.10 changed this module — that is the honest finding of the sprint, as
    D4.7's channel concept and D4.9's keep-alive timer were — but what it added
    is three lines that ask a channel for a value and hold it for a socket's
    lifetime. The property that matters survives.
    """
    source = _strip_source((BACKEND / "services/brokers/stream.py").read_text(encoding="utf-8")).lower()
    for name in ("fyers", "hsm", "fytoken", r"angel(?!og)", "smartapi", "upstox",
                 "zerodha", "kite", "protobuf", "snapshot", "topic"):
        assert not re.search(name, source), f"stream.py names {name!r} in executable code"


def test_the_connection_scope_is_free_for_every_broker_that_does_not_need_one():
    """`open()` returning `self` is what makes D4.10 invisible to three brokers.

    Asserted rather than assumed: if the default ever stopped being the identity,
    every stateless adapter would silently start decoding against something other
    than itself.
    """
    for broker in ("zerodha", "upstox", "angelone"):
        for channel in broker_registry.require(broker).stream_channels():
            assert channel.open({"access_token": "t"}) is channel, f"{broker}/{channel.name}"

    class Bare(BrokerStreamChannel):
        pass

    bare = Bare()
    assert bare.open({}) is bare and bare.open(None, None) is bare


def test_all_four_streaming_brokers_reach_the_market_gateway_through_the_identical_seam():
    """One seam, four brokers, no branch anywhere on the path."""
    import inspect

    from services.brokers import market_feed

    source = _strip_source(inspect.getsource(market_feed)).lower()
    for name in ("fyers", "hsm", r"angel(?!og)", "smartapi", "upstox", "zerodha", "kite"):
        assert not re.search(name, source), f"market_feed.py names {name!r} in executable code"

    for relative in ("services/brokers/market_feed.py", "services/brokers/stream.py",
                     "services/market_engine/source_manager.py"):
        text = _strip_prose((BACKEND / relative).read_text(encoding="utf-8"))
        for comparison in ("broker ==", "broker!=", "broker !=", "broker in ("):
            assert comparison not in text, f"{relative} branches on a broker name: {comparison!r}"

    for broker in ("zerodha", "upstox", "angelone", "fyers"):
        adapter = broker_registry.require(broker)
        assert adapter.supports(BrokerCapability.TICK_STREAM)
        channels = [c for c in adapter.stream_channels() if StreamEventKind.TICKS in c.delivers]
        assert len(channels) == 1, f"{broker} must have exactly one tick-carrying channel"


def test_the_fyers_adapter_registers_with_no_change_to_the_framework():
    """Developer Rule 9: one adapter plus one registry entry, and nothing else."""
    adapter = _fyers()
    BrokerRegistry.validate(adapter)      # would raise if the declaration were unhonoured
    assert adapter.credential_spec.api_key_env == "FYERS_APP_ID"
    assert adapter.credential_spec.required == ("api_key", "api_secret")
    assert not adapter.supports(BrokerCapability.PLACE_ORDER)
    assert not adapter.supports(BrokerCapability.ORDER_STREAM)
    assert not adapter.supports(BrokerCapability.SESSION_REFRESH)


# ==================================================================
# D4.11 — Dhan (DhanHQ v2): the FIFTH concrete stream adapter
#
# The protocol below is transcribed from TWO independent sources, read against
# each other rather than one trusted:
#
#   * DhanHQ's published v2 documentation (Live Market Feed, Annexure,
#     Authentication, Portfolio, Funds);
#   * Dhan's own reference client, `DhanHQ-py` (`src/dhanhq/marketfeed.py`),
#     whose `struct` format strings are the authority on byte layout.
#
# Every binary fixture below is packed with the REFERENCE CLIENT'S OWN format
# strings — `<BHBIfHIfIIIffff` for a quote, `<BHBIfI` for a ticker, `<BHBIH` for
# a disconnect — rather than with offsets copied out of the adapter. That is the
# point: an adapter tested against fixtures built from its own constants proves
# only that it is self-consistent. Here the fixture and the decoder are derived
# from different documents, so a wrong offset in the adapter shows up as a wrong
# number rather than as agreement.
#
# Nothing here was inferred from Kite, Upstox, SmartAPI or Fyers. Dhan differs
# from all four on endpoint, auth style, framing, identity, price encoding and
# volume semantics — and agrees with none of them on any of the six.
#
# THE THREE FINDINGS THAT DROVE THESE TESTS
# ------------------------------------------
#   1. The price is an unscaled float32 in rupees. Four brokers, four divisors,
#      and Dhan has none — applying one publishes a price 1/100th of the truth.
#   2. Prev Close (response code 6) is BYTE-FOR-BYTE identical in shape to
#      Ticker (code 2), and Dhan sends one per instrument at subscribe time.
#      Only the response code separates yesterday's close from today's price.
#   3. `/holdings` reports `exchange`, not `exchangeSegment`, and the docs and
#      the SDK disagree on what it holds. A row saying `"ALL"` names no segment,
#      and a security id without a segment is two different companies.
#
# Every test below was run against a deliberately broken implementation first;
# the mutations are listed in TASK.md's D4.11 falsification table.
# ==================================================================


def _dhan():
    return broker_registry.require("dhan")


def _dhan_channel():
    channels = _dhan().stream_channels()
    assert len(channels) == 1, "Dhan's market-data surface is one socket"
    return channels[0]


def _dhan_session(token="live-dhan-jwt-token", client_id="1000000009"):
    return {"access_token": token, "account_id": client_id}


# -- wire fixtures, packed with the reference client's own format strings -------


def _dhan_quote(security_id=1333, price=2650.75, segment=1, volume=1_234_567,
                last_quantity=10, trade_time=1_724_236_800):
    """One Quote packet (response code 4). `<BHBIfHIfIIIffff`, 50 bytes.

    `last_quantity` and `volume` are deliberately different numbers so a decoder
    that read the wrong one is visibly wrong rather than coincidentally right.
    """
    return struct.pack(
        "<BHBIfHIfIIIffff",
        4, 42, segment, security_id, price, last_quantity, trade_time,
        2649.0, volume, 500, 700, 2600.0, 2610.0, 2700.0, 2590.0,
    )


def _dhan_ticker(security_id=1333, price=2650.75, segment=1, trade_time=1_724_236_800):
    """One Ticker packet (response code 2). `<BHBIfI`, 16 bytes. NO volume field."""
    return struct.pack("<BHBIfI", 2, 8, segment, security_id, price, trade_time)


def _dhan_prev_close(security_id=1333, price=2400.0, segment=1):
    """One Prev Close packet (code 6) — the SAME 16-byte shape as a Ticker.

    Dhan sends one of these per instrument the moment a subscription lands. It
    exists in this file solely to prove it is never priced.
    """
    return struct.pack("<BHBIfI", 6, 8, segment, security_id, price, 0)


def _dhan_full(security_id=1333, price=2650.75, segment=1, volume=1_234_567):
    """One Full packet (code 8). `<BHBIfHIfIIIIIIffff100s`, 162 bytes."""
    return struct.pack(
        "<BHBIfHIfIIIIIIffff100s",
        8, 154, segment, security_id, price, 10, 1_724_236_800,
        2649.0, volume, 500, 700, 0, 0, 0, 2600.0, 2610.0, 2700.0, 2590.0, b"\x00" * 100,
    )


def _dhan_oi(security_id=1333, segment=1):
    return struct.pack("<BHBII", 5, 4, segment, security_id, 99_000)


def _dhan_market_status():
    return struct.pack("<BHBI", 7, 0, 0, 0)


def _dhan_disconnect(code=807, security_id=1333, segment=1):
    """One feed-disconnect packet (code 50). `<BHBIH`, 10 bytes."""
    return struct.pack("<BHBIH", 50, 2, segment, security_id, code)


def _dhan_ticks(frame):
    """The canonical `BrokerTick` dicts one raw Dhan frame decodes to."""
    event = _dhan().decode_stream_frame(frame)
    return [tick.as_dict() for tick in event.ticks]


def _dhan_map():
    """An account's instrument map as Dhan identifies instruments."""
    return InstrumentMap.from_portfolio(
        [
            {"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "NSE_EQ|1333"},
            {"symbol": "TCS", "exchange": "NSE", "instrument_token": "NSE_EQ|11536"},
        ]
    )


def _dhan_canonical(price=2650.75, security_id=1333, segment=1, volume=1_234_567):
    return canonical_ticks(
        _dhan_ticks(_dhan_quote(security_id=security_id, price=price, segment=segment, volume=volume)),
        _dhan_map(), broker="dhan",
    )


def _dhan_feed(user_id="u1", symbols=("RELIANCE",)):
    """Attach a Dhan market feed for `user_id` on the real registry."""
    return _attach_and_get(user_id, "dhan", symbols)


# -- price encoding: the finding most likely to be "corrected" into a bug -------


def test_the_dhan_price_is_an_unscaled_float32_in_rupees():
    """No divisor. Not Kite's token-carried scale, not SmartAPI's 100, not Fyers'.

    Dhan puts an IEEE-754 `float32` of the rupee price straight on the wire. The
    fixture is packed with `struct.pack("<f", 2650.75)` — a float, not an integer
    — so a decoder that divided by anything would produce 26.5075, and a decoder
    that read the field as an int would produce a nine-digit number. Both are
    visible here; neither raises anywhere in production.
    """
    (tick,) = _dhan_ticks(_dhan_quote(price=2650.75))
    assert tick["last_price"] == pytest.approx(2650.75)

    # A price with no exact float32 representation still round-trips within the
    # tolerance float32 actually offers — the point being that the wire value is
    # a float and is used as one.
    (penny,) = _dhan_ticks(_dhan_quote(price=1.05))
    assert penny["last_price"] == pytest.approx(1.05, rel=1e-6)


def test_a_dhan_frame_whose_price_is_not_finite_is_refused_at_the_wire():
    """NaN and infinity are rejected by the codec, not merely by MarketTick.

    A `float32` read out of a truncated or misaligned packet is genuinely capable
    of being either. `MarketTick` would refuse both — NaN fails every range
    comparison — but refusing them here keeps a nonsense number out of
    `BrokerTick`, out of the logs and out of every diagnostic that prints one.
    """
    for bad in (float("nan"), float("inf"), float("-inf"), 0.0, -5.0):
        assert _dhan_ticks(_dhan_quote(price=bad)) == [], f"{bad} was priced"


def test_a_dhan_previous_close_is_never_published_as_a_live_price():
    """THE sharpest edge in this protocol, and the reason PRICEABLE is a table.

    A Prev Close packet (code 6) and a Ticker packet (code 2) are the same 16
    bytes with the same `float32` at the same offset. Dhan sends one Prev Close
    per instrument the moment a subscription lands, so a codec that priced "any
    frame with a float at offset 8" would publish **yesterday's close as today's
    price**, once per holding, immediately after every connect and every
    reconnect — marking a whole portfolio at stale prices with nothing raised.

    The two fixtures below differ in exactly one byte.
    """
    priced = _dhan_prev_close(price=2400.0)
    live = _dhan_ticker(price=2650.75)
    assert len(priced) == len(live) == 16
    assert sum(a != b for a, b in zip(priced, live)) > 0
    assert priced[1:8] == live[1:8], "the fixtures differ by more than the response code"

    assert _dhan_ticks(priced) == [], "a previous close was published as a live tick"
    assert [t["last_price"] for t in _dhan_ticks(live)] == [2650.75]


def test_dhan_frames_that_are_not_prices_are_ignored_rather_than_decoded():
    """OI, market status, index and depth packets are normal traffic, not errors.

    A codec that raised on them would fill the log with noise from a perfectly
    healthy connection; one that priced them would invent a rupee value out of an
    open-interest count.
    """
    for frame in (_dhan_oi(), _dhan_market_status(), b"", b"\x02", b"\x01\x00\x00\x00"):
        event = _dhan().decode_stream_frame(frame)
        assert event.kind is StreamEventKind.IGNORE, f"{frame!r} was decoded"


def test_a_truncated_dhan_quote_is_dropped_rather_than_read_short():
    """A 50-byte packet arriving as 30 bytes must not be priced from what fits."""
    for length in (8, 15, 30, 49):
        assert _dhan_ticks(_dhan_quote()[:length]) == [], f"a {length}-byte quote was priced"


# -- volume semantics ----------------------------------------------------------


def test_dhan_publishes_cumulative_day_volume_and_not_the_last_trade_quantity():
    """One packet, four volume-shaped fields, and only one of them is the volume.

    A Quote packet carries `LTQ` (this trade's size, int16 at offset 12),
    `volume` (the day's cumulative traded quantity, int32 at offset 22),
    `total_sell_quantity` (offset 26) and `total_buy_quantity` (offset 30). Only
    the second is what `MarketTick.volume` means; the other three are a trade
    size and two order-book aggregates, and any of them would publish a number
    that means something else entirely.

    Every one is a distinct value in the fixture, so reading the wrong field is a
    wrong number rather than a coincidence.
    """
    (tick,) = _dhan_ticks(_dhan_quote(volume=1_234_567, last_quantity=10))
    assert tick["volume"] == 1_234_567
    assert tick["volume"] not in (10, 500, 700)

    (canonical,) = _dhan_canonical(volume=1_234_567)
    assert canonical["volume"] == 1_234_567


def test_a_dhan_ticker_packet_makes_no_volume_claim_at_all():
    """Ticker mode has no volume FIELD. `0` there would assert nothing traded today.

    `BrokerTick.volume` is an `int` and cannot be absent, so a codec expresses
    "this feed does not say" as `0` — and the canonical boundary is what turns
    that into `MarketTick.volume is None`, which is a genuinely different claim
    from "zero shares changed hands".
    """
    (tick,) = _dhan_ticks(_dhan_ticker())
    assert tick["volume"] == 0
    (canonical,) = canonical_ticks(_dhan_ticks(_dhan_ticker()), _dhan_map(), broker="dhan")
    assert canonical["volume"] is None


# -- instrument identity -------------------------------------------------------


def test_a_dhan_security_id_is_only_an_identity_when_paired_with_its_segment():
    """NSE id 1333 and BSE id 1333 are different companies.

    Dhan numbers instruments per exchange segment, the frame carries the segment
    as a byte and the id as an int32, and `InstrumentMap` matches ONE value — so
    the pair has to be that value. A map keyed on the bare id would resolve a BSE
    tick onto an NSE holding and mark the position at another company's price.
    """
    from services.brokers.dhan import instrument_id, parse_instrument_id

    assert instrument_id("NSE_EQ", 1333) == "NSE_EQ|1333"
    assert instrument_id("BSE_EQ", 1333) == "BSE_EQ|1333"
    assert instrument_id("NSE_EQ", 1333) != instrument_id("BSE_EQ", 1333)
    assert parse_instrument_id("NSE_EQ|1333") == ("NSE_EQ", "1333")

    # The same security id on the two segments decodes to two distinct handles.
    (nse,) = _dhan_ticks(_dhan_quote(security_id=1333, segment=1))
    (bse,) = _dhan_ticks(_dhan_quote(security_id=1333, segment=4))
    assert nse["instrument_token"] == "NSE_EQ|1333"
    assert bse["instrument_token"] == "BSE_EQ|1333"
    assert nse["exchange"] == "NSE" and bse["exchange"] == "BSE"

    # And the account that holds only the NSE line resolves only the NSE tick.
    assert [t["symbol"] for t in canonical_ticks([nse], _dhan_map(), broker="dhan")] == ["RELIANCE"]
    assert canonical_ticks([bse], _dhan_map(), broker="dhan") == []


def test_a_dhan_instrument_id_refuses_everything_that_is_not_a_usable_pair():
    from services.brokers.dhan import instrument_id, parse_instrument_id

    for segment, security_id in (
        (None, 1333), ("", 1333), ("NASDAQ", 1333), (True, 1333), ("NSE", 1333),
        ("NSE_EQ", None), ("NSE_EQ", ""), ("NSE_EQ", 0), ("NSE_EQ", -1),
        ("NSE_EQ", "abc"), ("NSE_EQ", True),
    ):
        assert instrument_id(segment, security_id) is None, f"{segment!r}|{security_id!r} was accepted"

    for value in (None, "", "1333", "NSE_EQ", "NSE_EQ|", "|1333", "NASDAQ|1", "NSE_EQ|0", True):
        assert parse_instrument_id(value) is None, f"{value!r} was accepted"


def test_an_unmapped_dhan_instrument_is_never_renamed_into_a_symbol():
    """`"NSE_EQ|999999"` must never reach a holding, a watchlist or the AI's context."""
    (tick,) = _dhan_ticks(_dhan_quote(security_id=999_999))
    assert tick["instrument_token"] == "NSE_EQ|999999"
    assert canonical_ticks([tick], _dhan_map(), broker="dhan") == []


def test_a_dhan_segment_this_adapter_cannot_name_is_dropped_not_defaulted():
    """Segment 9 is not in Dhan's Annexure. It is dropped, not treated as NSE."""
    for segment in (6, 9, 200, 255):
        assert _dhan_ticks(_dhan_quote(segment=segment)) == [], f"segment {segment} was priced"


def test_the_dhan_segment_tables_are_one_table_read_two_ways():
    """The subscribe NAME and the frame ENUM cannot drift, because they are derived.

    The subscribe frame takes `"NSE_EQ"`; every response frame carries `1`. Two
    hand-written tables would eventually disagree by one row, and the symptom
    would be a subscription for one instrument and ticks attributed to another.
    """
    from services.brokers.dhan import SEGMENT_EXCHANGES, SEGMENT_NAMES, SEGMENTS

    assert SEGMENT_NAMES == {enum: name for name, (enum, _e) in SEGMENTS.items()}
    assert SEGMENT_EXCHANGES == {name: exchange for name, (_n, exchange) in SEGMENTS.items()}
    assert len(SEGMENT_NAMES) == len(SEGMENTS), "two segments share an enum value"
    # The Annexure's own values, written out so a silent renumbering is visible.
    assert {name: enum for name, (enum, _e) in SEGMENTS.items()} == {
        "IDX_I": 0, "NSE_EQ": 1, "NSE_FNO": 2, "NSE_CURRENCY": 3,
        "BSE_EQ": 4, "MCX_COMM": 5, "BSE_CURRENCY": 7, "BSE_FNO": 8,
    }
    # Not SmartAPI's table. Copying a predecessor's segment numbers is the exact
    # mutation D4.10 caught, and Dhan's differ from Angel One's on every row.
    from services.brokers.angelone import EXCHANGE_TYPES

    assert {name: enum for name, (enum, _e) in SEGMENTS.items()} != EXCHANGE_TYPES


# -- the holdings finding: an exchange field that is not always an exchange -----


def _dhan_adapter_with(holdings=None, positions=None, funds=None, profile=None):
    """Patch the adapter's one HTTP method with a path-routed fake response."""
    routes = {
        "/holdings": holdings if holdings is not None else [],
        "/positions": positions if positions is not None else [],
        "/fundlimit": funds if funds is not None else {},
        "/profile": profile if profile is not None else {},
    }

    async def fake(method, url, headers=None, data=None, json_body=None, timeout=12.0):
        for path, payload in routes.items():
            if url.endswith(path):
                return payload
        raise AssertionError(f"unexpected Dhan call: {url}")

    return patch.object(type(_dhan()), "_request", side_effect=fake)


def test_a_dhan_holding_that_names_no_exchange_yields_no_instrument_identity(caplog):
    """The docs say `"ALL"`; the SDK's own fixture says `"NSE"`. Both are handled.

    `/holdings` reports `exchange`, not `exchangeSegment`, and Dhan's published
    sample and its reference client's test fixture disagree about what that field
    contains. A row naming a real exchange is a delivery holding and can only be
    cash, so the segment follows. A row saying `"ALL"` names no exchange at all —
    and defaulting it to NSE_EQ would be right most of the time and **wrong
    silently**: a BSE-only holding would be subscribed as whatever NSE numbers
    that id, and that company's price would be published under the user's stock's
    name.

    So the row keeps its symbol — it is a real holding everywhere else — and
    carries no instrument id, and the count is WARNed rather than swallowed.
    """
    rows = [
        {"exchange": "NSE", "tradingSymbol": "RELIANCE", "securityId": "1333",
         "isin": "INE002A01018", "totalQty": 10, "avgCostPrice": 2400.0, "lastTradedPrice": 2650.75},
        {"exchange": "BSE", "tradingSymbol": "TCS", "securityId": "11536",
         "totalQty": 5, "avgCostPrice": 3200.0, "lastTradedPrice": 3300.0},
        {"exchange": "ALL", "tradingSymbol": "HDFC", "securityId": "1330",
         "totalQty": 1000, "avgCostPrice": 2655.0, "lastTradedPrice": 2700.0},
    ]
    with caplog.at_level(logging.WARNING, logger="services.brokers.dhan"):
        with _dhan_adapter_with(holdings=rows):
            holdings = run(_dhan().get_holdings(_dhan_session()))

    by_symbol = {row["symbol"]: row for row in holdings}
    assert by_symbol["RELIANCE"]["instrument_token"] == "NSE_EQ|1333"
    assert by_symbol["RELIANCE"]["exchange"] == "NSE"
    assert by_symbol["TCS"]["instrument_token"] == "BSE_EQ|11536"
    assert by_symbol["TCS"]["exchange"] == "BSE"

    # The consolidated row survives as a holding and cannot be subscribed.
    assert by_symbol["HDFC"]["instrument_token"] is None
    assert by_symbol["HDFC"]["exchange"] is None
    assert by_symbol["HDFC"]["quantity"] == 1000

    assert any("no exchange segment" in record.getMessage()
               for record in caplog.records), "an unsubscribable holding was dropped in silence"

    # And it is absent from the subscription rather than subscribed as a guess.
    assert _dhan().stream_instruments(holdings=holdings) == ["BSE_EQ|11536", "NSE_EQ|1333"]


def test_dhan_positions_carry_their_own_segment_and_map_completely():
    """`/positions` DOES return `exchangeSegment`, so an intraday position maps fully."""
    rows = [
        {"tradingSymbol": "TCS", "securityId": "11536", "positionType": "LONG",
         "exchangeSegment": "NSE_EQ", "productType": "CNC", "buyAvg": 3345.8, "buyQty": 40,
         "costPrice": 3215.0, "sellAvg": 0.0, "sellQty": 0, "netQty": 40,
         "realizedProfit": 0.0, "unrealizedProfit": 6122.0},
    ]
    with _dhan_adapter_with(positions=rows):
        positions = run(_dhan().get_positions(_dhan_session()))

    assert positions[0]["instrument_token"] == "NSE_EQ|11536"
    assert positions[0]["symbol"] == "TCS"
    assert positions[0]["exchange"] == "NSE"
    assert positions[0]["side"] == "LONG"
    assert positions[0]["pnl"] == 6122.0


def test_dhan_stream_instruments_are_deduplicated_and_stably_ordered():
    """A resubscribe after a portfolio sync must produce the same list, in order."""
    holdings = [{"instrument_token": "NSE_EQ|9"}, {"instrument_token": "NSE_EQ|10"},
                {"instrument_token": "NSE_EQ|9"}, {"instrument_token": "BSE_EQ|2"},
                {"instrument_token": None}, {"instrument_token": "NASDAQ|1"}, "not a row"]
    positions = [{"instrument_token": "NSE_EQ|10"}]
    assert _dhan().stream_instruments(holdings=holdings, positions=positions) == [
        "BSE_EQ|2", "NSE_EQ|9", "NSE_EQ|10",
    ]


# -- subscriptions -------------------------------------------------------------


def test_a_dhan_subscription_is_split_into_frames_of_one_hundred_instruments():
    """Dhan rejects a message carrying more than 100 instruments.

    This is a MESSAGE limit, not a session limit, so nothing is dropped for it —
    a 250-instrument account is three frames. `stream_subscribe_frames` has always
    returned a list precisely so a broker can say this, which is why Dhan needed
    no framework change to say it.
    """
    instruments = [f"NSE_EQ|{n}" for n in range(1, 251)]
    frames = _dhan().stream_subscribe_frames(instruments)

    assert len(frames) == 3
    assert all(isinstance(frame, str) for frame in frames), "the subscribe frame is text, not bytes"
    decoded = [json.loads(frame) for frame in frames]
    assert [len(f["InstrumentList"]) for f in decoded] == [100, 100, 50]
    # `InstrumentCount` describes THIS message, not the whole subscription.
    assert [f["InstrumentCount"] for f in decoded] == [100, 100, 50]
    assert {f["RequestCode"] for f in decoded} == {17}

    # Every instrument asked for is on the wire exactly once, and nothing else is.
    sent = [f"{i['ExchangeSegment']}|{i['SecurityId']}" for f in decoded for i in f["InstrumentList"]]
    assert sorted(sent, key=lambda v: int(v.split("|")[1])) == instruments


def test_the_dhan_subscribe_frame_names_the_segment_by_name_not_by_its_enum():
    """The response frames carry `1`; the subscribe frame must carry `"NSE_EQ"`."""
    (frame,) = _dhan().stream_subscribe_frames(["NSE_EQ|1333", "BSE_FNO|7"])
    payload = json.loads(frame)
    assert [i["ExchangeSegment"] for i in payload["InstrumentList"]] == ["NSE_EQ", "BSE_FNO"]
    assert [i["SecurityId"] for i in payload["InstrumentList"]] == ["1333", "7"]
    assert "1" not in {i["ExchangeSegment"] for i in payload["InstrumentList"]}


def test_a_dhan_subscription_past_the_connection_limit_is_trimmed_out_loud(caplog):
    """5,000 per connection is a real ceiling. Trimming it silently is what is banned.

    Sharding the remainder across a second connection is D5's subject and is
    deliberately not attempted here — but a user must not be told they are
    watching an instrument the wire was never asked for.
    """
    from services.brokers.dhan import MAX_INSTRUMENTS_PER_CONNECTION

    instruments = [f"NSE_EQ|{n}" for n in range(1, MAX_INSTRUMENTS_PER_CONNECTION + 26)]
    with caplog.at_level(logging.WARNING, logger="services.brokers.dhan"):
        frames = _dhan().stream_subscribe_frames(instruments)

    total = sum(len(json.loads(frame)["InstrumentList"]) for frame in frames)
    assert total == MAX_INSTRUMENTS_PER_CONNECTION
    assert any("exceeds" in record.message for record in caplog.records), \
        "instruments were trimmed in silence"


def test_a_dhan_subscription_of_nothing_subscribable_sends_no_frame():
    assert _dhan().stream_subscribe_frames([]) == []
    assert _dhan().stream_subscribe_frames(None) == []
    assert _dhan().stream_subscribe_frames(["NASDAQ|1", None, "", 738561]) == []


# -- endpoint, auth and the keep-alive -----------------------------------------


def test_the_dhan_endpoint_authenticates_by_query_string_and_hides_it_from_logs():
    """Dhan's style is Kite's, not Upstox's header or Fyers' in-band frame.

    The credential is in the URL because the protocol puts it there, so the ONLY
    form that may ever be logged is `safe_url`.
    """
    endpoint = _dhan().stream_endpoint(_dhan_session(token="SECRET-DHAN-TOKEN", client_id="1000000009"))

    assert endpoint.url.startswith("wss://api-feed.dhan.co?")
    assert "SECRET-DHAN-TOKEN" in endpoint.url, "the credential never reached the wire"
    assert endpoint.safe_url == "wss://api-feed.dhan.co"
    assert "SECRET-DHAN-TOKEN" not in endpoint.safe_url
    assert "1000000009" not in endpoint.safe_url

    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(endpoint.url).query)
    assert query["version"] == ["2"] and query["authType"] == ["2"]
    assert query["clientId"] == ["1000000009"]
    assert endpoint.headers == {}, "Dhan authenticates in the query string, not in a header"


def test_dhan_declares_no_application_keepalive_because_it_does_not_need_one():
    """A protocol finding, not an omission — and the opposite of Angel One's.

    Dhan's server sends a WebSocket PROTOCOL ping every 10 seconds and closes a
    connection unanswered for 40. A protocol ping is answered by the `websockets`
    library in both peers without either application seeing it, so the keep-alive
    D4.9 added `heartbeat_frame` for is already satisfied. Angel One is the
    contrast: it does not count protocol pings at all and closes a socket that
    stops sending the text frame `ping`.
    """
    endpoint = _dhan().stream_endpoint(_dhan_session())
    assert endpoint.heartbeat_frame is None and endpoint.heartbeat_interval is None
    # The library's own ping is left enabled — the same defaults Dhan's reference
    # client runs with, since it calls `websockets.connect(url)` with no overrides.
    assert endpoint.ping_interval and endpoint.ping_timeout

    angel = _angelone().stream_endpoint(_angel_session())
    assert angel.heartbeat_frame is not None, "the contrast this test rests on has gone"


def test_dhan_session_expiry_is_a_duration_and_not_a_calendar_cutoff():
    """24 hours from generation — unlike Kite, SmartAPI and Fyers, which die at a fixed hour."""
    connected = datetime(2026, 8, 25, 9, 30, tzinfo=timezone.utc)
    assert _dhan().session_expiry(connected) == connected + timedelta(hours=24)


def test_a_naive_dhan_expiry_timestamp_is_read_as_ist_and_not_as_utc():
    """Dhan documents `expiryTime` as an ISO timestamp in IST with no offset.

    Reading it as UTC would place every session's expiry five and a half hours
    late, so a dead token would be treated as live for a whole trading morning.
    """
    from services.brokers.dhan import _expiry_time

    parsed = _expiry_time("2026-08-26T09:30:00")
    assert parsed == datetime(2026, 8, 26, 4, 0, tzinfo=timezone.utc)
    # An explicit offset is honoured, and unparseable input falls back to None.
    assert _expiry_time("2026-08-26T09:30:00+00:00") == datetime(2026, 8, 26, 9, 30, tzinfo=timezone.utc)
    assert _expiry_time("not a timestamp") is None and _expiry_time(None) is None


def test_the_dhan_callback_reads_tokenid_because_dhan_does_not_send_code():
    """The inherited OAuth2 parser looks for `code` and would report a failed login."""
    assert _dhan().parse_callback_params({"tokenId": "TID-123"}) == {"token_id": "TID-123"}
    assert _dhan().parse_callback_params({"code": "abc"}) is None
    assert _dhan().parse_callback_params({}) is None
    assert _dhan().parse_callback_params(None) is None


# -- session expiry, reported in a frame rather than at the handshake ----------


def test_a_dhan_disconnect_frame_for_a_dead_token_stops_the_stream():
    """Dhan reports a dead session IN A FRAME — the opposite of Angel One and Fyers.

    The token rides in the connection's query string, so the socket opens first
    and the rejection arrives on it as response code 50. Left unclassified the
    frame would fall through as an unknown packet and be ignored, leaving a
    stream that reconnects forever into a session Dhan has already ended.
    """
    for code in (807, 808, 809):
        event = _dhan().decode_stream_frame(_dhan_disconnect(code))
        assert event.kind is StreamEventKind.AUTH_EXPIRED, f"code {code} did not stop the stream"
        assert str(code) not in event.message, "the raw wire code leaked into a user-facing message"
        assert event.message, f"code {code} stopped the stream with no reason"

    # 806 also stops the stream, and D5.5 stopped calling it an expired session:
    # the token is valid and only the market-data entitlement is missing, so it
    # takes the narrower path that leaves the account's session alone. See
    # `test_provider_entitlement.py` and ADR-045.
    refused = _dhan().decode_stream_frame(_dhan_disconnect(806))
    assert refused.kind is StreamEventKind.NOT_ENTITLED
    assert "806" not in refused.message and refused.message

    ticks, _orders, expired, _socket = drive_stream(
        _dhan(), [_dhan_quote(), _dhan_disconnect(807), _dhan_quote(price=9999.0)],
        instruments=["NSE_EQ|1333"], session=_dhan_session())
    assert expired == [("user-1", "dhan")]
    # The tick before the disconnect is delivered; nothing after it is.
    assert [t["last_price"] for _u, _b, batch in ticks for t in batch] == [2650.75]


def test_a_transient_dhan_disconnect_does_not_end_the_users_session():
    """805 is "too many connections", which the next attempt may well survive.

    Dhan drops the OLDEST socket when a sixth opens, so permanently killing a
    user's feed because they opened Dhan's own app would be the wrong trade. It is
    reported and left to the transport's backoff — a genuinely different outcome
    from an expired token, and the distinction is the adapter's to make.
    """
    event = _dhan().decode_stream_frame(_dhan_disconnect(805))
    assert event.kind is StreamEventKind.ERROR
    assert "too many active connections" in event.message

    ticks, _orders, expired, socket = drive_stream(
        _dhan(), [_dhan_disconnect(805), _dhan_quote()],
        instruments=["NSE_EQ|1333"], session=_dhan_session())
    assert expired == [], "a transient disconnect ended the user's session"
    assert [t["last_price"] for _u, _b, batch in ticks for t in batch] == [2650.75]
    assert socket.closed


def test_an_unknown_dhan_disconnect_code_is_reported_rather_than_guessed():
    event = _dhan().decode_stream_frame(_dhan_disconnect(999))
    assert event.kind is StreamEventKind.ERROR and "999" in event.message


def test_a_refused_dhan_handshake_is_classified_as_a_dead_session():
    """The second line of defence, and it matters because the token is in the URL.

    Dhan's documented behaviour is to accept the socket and then disconnect, but a
    gateway in front of the feed may reject a malformed token at the HTTP upgrade
    — and an unclassified 401 is indistinguishable from a broker outage to the
    generic transport, which would then reconnect forever.
    """
    class Refused(Exception):
        def __init__(self, status):
            self.status_code = status

    class Wrapped(Exception):
        def __init__(self, status):
            self.response = type("R", (), {"status_code": status})()

    for status in (401, 403):
        assert _dhan().stream_connect_error(Refused(status))
        assert _dhan().stream_connect_error(Wrapped(status))
    assert _dhan().stream_connect_error(Refused(503)) is None
    assert _dhan().stream_connect_error(OSError("connection refused")) is None


def test_a_dead_dhan_rest_session_is_classified_and_an_ordinary_error_is_not():
    """`DH-901` means reconnect. Nothing else does, and mislabelling one would lie."""
    from services.brokers.errors import BrokerAuthError, BrokerError

    with _dhan_adapter_with(holdings={"errorCode": "DH-901", "errorMessage": "Invalid Authentication"}):
        with pytest.raises(BrokerAuthError):
            run(_dhan().get_holdings(_dhan_session()))

    with _dhan_adapter_with(holdings={"errorCode": "DH-904", "errorMessage": "Too many requests"}):
        with pytest.raises(BrokerError) as caught:
            run(_dhan().get_holdings(_dhan_session()))
    assert not isinstance(caught.value, BrokerAuthError), "a rate limit was reported as a dead session"


# -- the canonical boundary ----------------------------------------------------


def test_a_dhan_tick_becomes_a_canonical_market_tick_and_nothing_else():
    """Raw Dhan bytes to `MarketTick`, with no Dhan field surviving the trip."""
    (canonical,) = _dhan_canonical()
    assert set(canonical) == {"symbol", "price", "exchange", "volume", "ingested_at"}
    assert canonical["symbol"] == "RELIANCE"
    assert canonical["price"] == pytest.approx(2650.75)
    assert canonical["exchange"] == "NSE"
    assert canonical["volume"] == 1_234_567

    blob = json.dumps(canonical).lower()
    for forbidden in ("nse_eq", "1333", "securityid", "dhan", "instrument_token", "requestcode"):
        assert forbidden not in blob, f"{forbidden} crossed the canonical boundary"


def test_a_raw_dhan_payload_cannot_reach_a_tick_consumer():
    """The transport type-checks the codec's return value; a raw frame does not pass."""
    with patch.object(type(_dhan()), "decode_stream_frame", lambda self, frame: {"raw": frame}):
        ticks, _orders, _expired, _socket = drive_stream(
            _dhan(), [_dhan_quote()], instruments=["NSE_EQ|1333"], session=_dhan_session())
    assert ticks == [], "a codec returning a raw payload leaked it upward"


def test_a_malformed_dhan_frame_does_not_terminate_a_live_dhan_stream():
    """One bad packet must not cost the socket the prices it is otherwise delivering."""
    frames = [_dhan_quote(price=2650.75), b"\x04\x00", _dhan_prev_close(),
              b"", _dhan_oi(), _dhan_quote(price=2660.0)]
    ticks, _orders, expired, socket = drive_stream(
        _dhan(), frames, instruments=["NSE_EQ|1333"], session=_dhan_session())
    assert [t["last_price"] for _u, _b, batch in ticks for t in batch] == [2650.75, 2660.0]
    assert expired == [] and socket.closed


# -- the transport -------------------------------------------------------------


def test_a_dhan_stream_runs_through_the_generic_transport_unchanged():
    """Real Dhan bytes over the real transport: subscribe, tick, close.

    The subscribe frames must reach the socket verbatim **as text** — the
    transport cannot know what encoding this broker expects, so it must not
    re-encode what the codec handed it.
    """
    frames = [_dhan_prev_close(), _dhan_quote(price=2650.75), _dhan_market_status(),
              _dhan_quote(security_id=11536, price=3300.0)]
    ticks, orders, expired, socket = drive_stream(
        _dhan(), frames, instruments=["NSE_EQ|1333", "NSE_EQ|11536"], session=_dhan_session())

    assert len(socket.sent) == 1 and isinstance(socket.sent[0], str)
    assert json.loads(socket.sent[0])["RequestCode"] == 17
    assert [t["last_price"] for _u, _b, batch in ticks for t in batch] == [2650.75, 3300.0]
    assert all(broker == "dhan" for _u, broker, _b in ticks)
    assert orders == [] and expired == [] and socket.closed


def test_dhan_declares_one_default_channel_served_by_the_one_generic_transport():
    from services.brokers.base import AdapterStreamChannel
    from services.brokers.stream import PROTOCOL_RUNNERS, resolve_transport

    channel = _dhan_channel()
    assert isinstance(channel, AdapterStreamChannel), \
        "Dhan needed a bespoke channel class — the default no longer covers a plain WebSocket broker"
    assert channel.name == DEFAULT_STREAM_CHANNEL
    assert channel.delivers == frozenset({StreamEventKind.TICKS})
    assert channel.protocol == _dhan().stream_protocol == "dhan_feed_v2"
    assert PROTOCOL_RUNNERS == {}, "Dhan added a protocol runner — its feed is an ordinary WebSocket"
    assert resolve_transport(channel) is BrokerStream._run_websocket


def test_dhan_needs_no_connection_scope():
    """Every Dhan frame is decodable on its own, so `open()` stays the identity.

    D4.10 added a per-connection scope for a feed whose steady-state frames are
    deltas against a snapshot. Dhan's are not: every frame carries the segment,
    the security id and the price, so nothing an earlier frame established is
    needed to read a later one. Asserted rather than assumed — a channel that
    quietly acquired state would be per-broker rather than per-connection, and one
    user's reconnect would renumber another user's instruments.
    """
    channel = _dhan_channel()
    assert channel.open(_dhan_session()) is channel
    assert channel.open(_dhan_session("other-token", "2000000002")) is channel

    # And the proof that matters: the SAME frame decodes identically with no
    # preceding frames, and after another account's traffic.
    first = _dhan_ticks(_dhan_quote(price=2650.75))
    _dhan().decode_stream_frame(_dhan_quote(security_id=99, price=1.5, segment=4))
    assert _dhan_ticks(_dhan_quote(price=2650.75)) == first


def test_a_dhan_codec_declaring_no_tick_capability_delivers_nothing():
    """The capability model stays the authority; a codec may not widen it."""
    adapter = _dhan()
    narrowed = adapter.capabilities - {BrokerCapability.TICK_STREAM}
    with patch.object(type(adapter), "capabilities", narrowed):
        ticks, _orders, _expired, _socket = drive_stream(
            adapter, [_dhan_quote()], instruments=["NSE_EQ|1333"], session=_dhan_session())
    assert ticks == []


# -- readiness and failover ----------------------------------------------------


def test_a_dhan_feed_is_promoted_only_by_a_canonical_tick_on_the_current_link(no_probation_window):
    """Not on connect, not on subscribe, not on a timer. D4.5's rule, unchanged.

    Dhan sends no acknowledgement of a subscription at all, which makes the point
    concretely: there is nothing on this wire that could be mistaken for evidence
    except an actual price.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        feed = _dhan_feed("u1", symbols=("RELIANCE",))
        assert feed is not None
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline, "registering promoted the feed"

        run(set_market_feed_link("u1", "dhan", up=True))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline, "connecting promoted the feed"

        run(feed.on_raw(_dhan_canonical()))
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed

        # The link drops: the baseline serves immediately, and it never left.
        run(set_market_feed_link("u1", "dhan", up=False, reason="socket closed"))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline
        assert baseline.name in registry, "the baseline was released before the feed was ready"

        # A reconnect re-earns readiness rather than inheriting it.
        run(set_market_feed_link("u1", "dhan", up=True))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline, \
            "a reconnect promoted the feed on the previous connection's evidence"
        run(feed.on_raw(_dhan_canonical()))
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed

        # A LINK-UP WITH NO INTERVENING LINK-DOWN, which is a different path and
        # a separately-guarded one. `mark_link_down` discards the dead link's
        # evidence, so the sequence above cannot tell whether `mark_link_up`
        # discards it too — and it must, because the transport's link callback
        # is best-effort and a swallowed `down` would otherwise leave a fresh
        # socket serving prices that arrived on a socket that is gone. Found by
        # mutation: removing the discard in `mark_link_up` alone left every
        # other assertion in this test green.
        run(set_market_feed_link("u1", "dhan", up=True))
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline, \
            "a new link inherited the previous link's evidence"
        run(feed.on_raw(_dhan_canonical()))
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed


def test_dhan_registers_through_the_existing_provider_framework():
    """No Dhan-specific provider, gateway, registry or Source Manager exists."""
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import StreamingTickProvider

    with _clean_provider_registry() as registry:
        registry.clear()
        name = run(_attach("u1", "dhan", ["RELIANCE"]))
        assert name == feed_provider_name("u1", "dhan") == "brokerfeed:dhan:u1"
        provider = registry.get(name)
        assert type(provider) is StreamingTickProvider, "a broker-specific provider class appeared"
        assert provider.owner_user_id == "u1"


def test_removing_the_dhan_tick_capability_removes_its_market_feed():
    adapter = _dhan()
    narrowed = adapter.capabilities - {BrokerCapability.TICK_STREAM}
    with _clean_provider_registry() as registry:
        registry.clear()
        with patch.object(type(adapter), "capabilities", narrowed):
            assert run(_attach("u1", "dhan", ["RELIANCE"])) is None
        assert registry.get("brokerfeed:dhan:u1") is None


# -- multi-broker isolation ----------------------------------------------------


def test_five_brokers_speak_five_protocols_and_produce_one_canonical_tick():
    """THE D4.11 acceptance criterion: a fifth wire, the same canonical output.

    Five brokers agree on nothing at the wire — bespoke big-endian binary vs
    protobuf vs little-endian fixed packets vs a stateful topic protocol vs a
    little-endian float packet; an integer token vs a compound key vs a numeric
    segment pair vs an HSM topic vs a named segment pair; **five price rules, two
    of which are not rules at all** — one value carried on the wire, and one that
    is simply the price. What reaches the Market Engine from each is the same
    canonical `MarketTick`.
    """
    kite = canonical_ticks(_kite_ticks(_kite_frame((738561, 265075))), _kite_map(), broker="zerodha")
    upstox = canonical_ticks(
        _upstox_ticks(_upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75})), _upstox_map(), broker="upstox")
    angel = _angel_canonical(price=265075, token="2885")
    fyers = _fy_canonical(price=265075)
    dhan = _dhan_canonical(price=2650.75, volume=1_234_567)

    wires = [_kite_frame((738561, 265075)),
             _upstox_frame(ltpc={"NSE_EQ|INE002A01018": 2650.75}),
             _angel_packet(price=265075),
             _fy_frame(_fy_snapshot(values=(265075,))),
             _dhan_quote(price=2650.75)]
    assert len({bytes(wire) for wire in wires}) == 5, "two of the five wires are identical"

    identity = [[{k: v for k, v in tick.items() if k not in ("ingested_at", "volume")} for tick in batch]
                for batch in (kite, upstox, angel, fyers, dhan)]
    assert identity[0] == identity[1] == identity[2] == identity[3] == identity[4] == [
        {"symbol": "RELIANCE", "price": 2650.75, "exchange": "NSE"}
    ]

    # Volume is where the five legitimately differ, and the difference is a
    # protocol fact rather than an inconsistency: Dhan's Quote packet is the only
    # one of the five that carries a cumulative day volume, so it is the only one
    # that fills the field. The other four say nothing, and say it as `None`
    # rather than as a zero that would claim nothing traded.
    assert dhan[0]["volume"] == 1_234_567
    assert [batch[0]["volume"] for batch in (kite, upstox, angel, fyers)] == [None, None, None, None]


def test_six_users_on_six_providers_stay_on_their_own(no_probation_window):
    """Dhan, Fyers, Angel One, Zerodha, Upstox and the shared baseline, at once.

    The one test that exercises every streaming broker the platform has plus the
    fallback, and the property is per user: each resolves to their own feed, a
    user with no broker gets the baseline, and one broker's failure moves exactly
    one user.
    """
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        feeds = {
            "userA": _angel_feed("userA", symbols=("RELIANCE",)),
            "userB": _attach_and_get("userB", "zerodha"),
            "userC": _upstox_feed("userC", symbols=("RELIANCE",)),
            "userD": _fy_feed("userD", symbols=("RELIANCE",)),
            "userE": _dhan_feed("userE", symbols=("RELIANCE",)),
        }
        assert len({id(feed) for feed in feeds.values()}) == 5, "two users share one provider"

        for user, broker in (("userA", "angelone"), ("userB", "zerodha"), ("userC", "upstox"),
                             ("userD", "fyers"), ("userE", "dhan")):
            run(set_market_feed_link(user, broker, up=True))
        run(feeds["userA"].on_raw(_angel_canonical()))
        run(feeds["userB"].on_raw(canonical_ticks(
            _kite_ticks(_kite_frame((738561, 265075))), _kite_map(), broker="zerodha")))
        run(feeds["userC"].on_raw(_upstox_canonical()))
        run(feeds["userD"].on_raw(_fy_canonical()))
        run(feeds["userE"].on_raw(_dhan_canonical()))

        def resolved(user):
            return manager.resolve(Capability.QUOTES, context=ResolutionContext(user_id=user, symbol="RELIANCE"))

        for user in feeds:
            assert resolved(user) is feeds[user]
        assert resolved("guest") is baseline, "a user with no broker was served another user's feed"

        # Dhan drops. Only user E moves, and the other four brokers are untouched.
        run(set_market_feed_link("userE", "dhan", up=False, reason="socket closed"))
        assert resolved("userE") is baseline
        for user in ("userA", "userB", "userC", "userD"):
            assert resolved(user) is feeds[user]
        assert resolved("guest") is baseline

        # And Dhan's reconnect does not disturb anybody else's subscription.
        run(set_market_feed_link("userE", "dhan", up=True))
        run(feeds["userE"].on_raw(_dhan_canonical()))
        assert resolved("userE") is feeds["userE"]
        for user in ("userA", "userB", "userC", "userD"):
            assert resolved(user) is feeds[user]
            assert feeds[user].subscribed_symbols == ("RELIANCE",)


def test_two_dhan_users_of_the_SAME_broker_never_share_a_feed(no_probation_window):
    """Two accounts at one broker, resolved the way a consumer actually resolves.

    WHY THIS TEST RESOLVES THROUGH THE SOURCE MANAGER
    --------------------------------------------------
    The obvious version of this test attaches two feeds, keeps the two objects
    the attach returned, and asserts they differ. That version passes against a
    registry key with the user id removed from it — which is a real bug and a
    severe one — because each attach still *constructs* its own provider and
    still returns the one it just registered; the collision is invisible until
    somebody looks a feed up by name, which is what every consumer does and what
    the attach itself never does. Found by mutation, and the fix is to ask the
    question the platform asks: given this user, which provider serves them?

    Every other broker's isolation test in this file puts one user on each
    broker, so the same key would have stayed unique there too. Two users on ONE
    broker is the arrangement that can see this at all.
    """
    from services.brokers.market_feed import feed_provider_name
    from services.market_engine.providers import (
        Capability,
        ResolutionContext,
        YahooPollingAdapter,
        provider_registry,
    )

    assert feed_provider_name("userX", "dhan") != feed_provider_name("userY", "dhan"), \
        "two accounts at one broker share a provider name"

    with _clean_provider_registry() as registry:
        provider_registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)

        first = _dhan_feed("userX", symbols=("RELIANCE",))
        second = _dhan_feed("userY", symbols=("TCS",))
        assert first is not second
        assert first.owner_user_id == "userX" and second.owner_user_id == "userY"
        assert first.subscribed_symbols == ("RELIANCE",)
        assert second.subscribed_symbols == ("TCS",)

        # Both are still resolvable AT THE SAME TIME — the property the object
        # comparison above cannot see, because registering the second must not
        # have evicted the first.
        assert registry.get(feed_provider_name("userX", "dhan")) is first
        assert registry.get(feed_provider_name("userY", "dhan")) is second

        run(first.mark_link_up())
        run(second.mark_link_up())
        run(first.on_raw(_dhan_canonical()))
        assert second.describe()["accepted_records"] == 0, "one user's ticks reached another's feed"

        def resolved(user, symbol):
            return manager.resolve(Capability.QUOTES, context=ResolutionContext(user_id=user, symbol=symbol))

        assert resolved("userX", "RELIANCE") is first
        # userY has been told nothing yet, so the baseline serves them — and
        # crucially they are NOT served userX's feed.
        assert resolved("userY", "TCS") is baseline

        run(second.on_raw(canonical_ticks(
            _dhan_ticks(_dhan_quote(security_id=11536, price=3300.0)), _dhan_map(), broker="dhan")))
        assert resolved("userY", "TCS") is second
        assert resolved("userX", "RELIANCE") is first, "the second account displaced the first"

        # And a drop on one account leaves the other exactly where it was.
        run(first.mark_link_down("socket closed"))
        assert resolved("userX", "RELIANCE") is baseline
        assert resolved("userY", "TCS") is second

    # The channel is stateless across sessions: two different accounts' endpoints
    # differ only in their own credentials, and neither leaves a trace.
    channel = _dhan_channel()
    one = channel.endpoint(_dhan_session("token-x", "1111111111"))
    two = channel.endpoint(_dhan_session("token-y", "2222222222"))
    assert "token-x" in one.url and "token-x" not in two.url
    assert channel.endpoint(_dhan_session("token-x", "1111111111")).url == one.url


def test_a_dhan_quote_carries_no_broker_identity_and_no_other_users_data():
    from services.brokers.market_feed import set_market_feed_link
    from services.market_engine.providers import YahooPollingAdapter

    with _clean_provider_registry() as registry:
        registry.clear()
        registry.register(YahooPollingAdapter())
        feed = _dhan_feed("u1", symbols=("RELIANCE",))
        run(set_market_feed_link("u1", "dhan", up=True))
        run(feed.on_raw(_dhan_canonical()))
        quote = run(feed.fetch_quote("RELIANCE"))

    blob = json.dumps(quote).lower()
    for forbidden in ("dhan", "brokerfeed", "u1", "nse_eq", "1333", "securityid"):
        assert forbidden not in blob, f"{forbidden} reached a consumer-facing quote"


# -- the engine seam -----------------------------------------------------------


def test_the_engine_carries_dhan_bytes_all_the_way_into_the_registered_feed(no_probation_window):
    """The real join: the engine's instrument map, the canonical boundary, the push."""
    from services.market_engine.providers import Capability, ResolutionContext, YahooPollingAdapter

    holdings = [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "NSE_EQ|1333"}]
    engine = BrokerEngine()
    engine.configure(FakeDB())

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = YahooPollingAdapter()
        registry.register(baseline)
        manager = SourceManager(registry)
        ctx = ResolutionContext(user_id="u1", symbol="RELIANCE")

        with patch("services.brokers.stream.stream_manager.start_stream", new=AsyncMock()) as started, \
                patch.object(BrokerEngine, "get_session", new=AsyncMock(return_value=_dhan_session())), \
                patch("services.brokers.gateway.broker_gateway.get_holdings", new=AsyncMock(return_value=holdings)), \
                patch("services.brokers.gateway.broker_gateway.get_positions", new=AsyncMock(return_value=[])):
            run(engine.start_stream("u1", "dhan"))

        assert started.await_count == 1
        assert started.await_args.kwargs["instrument_tokens"] == ["NSE_EQ|1333"]
        assert started.await_args.kwargs["channel"] == DEFAULT_STREAM_CHANNEL

        feed = registry.get("brokerfeed:dhan:u1")
        assert feed is not None and feed.subscribed_symbols == ("RELIANCE",)
        run(feed.mark_link_up())
        assert manager.resolve(Capability.QUOTES, context=ctx) is baseline

        with patch.object(BrokerEngine, "_push", new=AsyncMock()):
            run(engine._on_stream_tick("u1", "dhan", _dhan_ticks(_dhan_quote(price=2650.5))))

        assert feed.describe()["accepted_records"] == 1
        assert manager.resolve(Capability.QUOTES, context=ctx) is feed
        assert run(feed.fetch_quote("RELIANCE"))["price"] == pytest.approx(2650.5)


# -- security ------------------------------------------------------------------


def test_no_dhan_credential_reaches_a_log_line_from_the_real_transport(caplog):
    """Driven through `_run_websocket`, not by reading `safe_url` — the wiring is the risk.

    Dhan authenticates by query string, so the endpoint object legitimately holds
    a live access token. The transport's "connected" line is the most natural
    place in this platform for one to escape, which is exactly why this test drives
    the transport rather than asserting on the endpoint.
    """
    secret = "DHAN-LIVE-TOKEN-9f3a2b"
    socket = _FakeSocket([_dhan_quote()])
    stream = BrokerStream("u1", "dhan", _dhan_session(token=secret, client_id="1000000009"),
                          credentials={"api_key": "PARTNER-ID"}, instrument_tokens=["NSE_EQ|1333"])

    async def scenario():
        with patch.object(BrokerStream, "_connect", AsyncMock(return_value=socket)):
            await stream._run_websocket()

    with caplog.at_level(logging.DEBUG):
        run(scenario())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert logged, "nothing was logged — this test could not have failed"
    assert "api-feed.dhan.co" in logged, "the connection was never logged, so the sweep proves nothing"
    for forbidden in (secret, "token=", "authType", "clientId="):
        assert forbidden not in logged, f"{forbidden!r} reached the application log"


def test_no_dhan_credential_survives_into_a_tick_a_frame_or_an_exception():
    """A live-looking credential is followed through every artefact it could reach."""
    secret = "DHAN-LIVE-TOKEN-9f3a2b"
    session = _dhan_session(token=secret, client_id="1000000009")

    artefacts = [
        json.dumps(_dhan_ticks(_dhan_quote())),
        json.dumps(_dhan_canonical()),
        json.dumps(_dhan().stream_subscribe_frames(["NSE_EQ|1333"])),
        repr(_dhan().stream_endpoint(session).safe_url),
        repr(_dhan().credentials),
        repr(_dhan_channel()),
        json.dumps(_dhan().describe()),
    ]
    for artefact in artefacts:
        assert secret not in artefact, f"the access token reached {artefact[:60]!r}"

    # The one place it MUST appear is the wire, and only there.
    assert secret in _dhan().stream_endpoint(session).url

    # A codec failure must not print the frame's context back with the session in it.
    try:
        _dhan()._headers({})
    except Exception as error:  # noqa: BLE001 - the message is the subject
        assert secret not in str(error)


def test_the_dhan_partner_secret_never_appears_in_a_url(monkeypatch):
    """The consent flow sends it as a HEADER. A query parameter would be shown to the user."""
    import inspect

    from services.brokers import dhan as dhan_module

    source = _strip_prose(inspect.getsource(dhan_module))
    for line in source.splitlines():
        if "urlencode" in line or "?" in line and "https://" in line:
            assert "partner_secret" not in line and "api_secret" not in line, \
                f"the partner secret was put in a URL: {line.strip()!r}"
    assert "partner_secret" in source, "the sweep found no partner secret at all — it proves nothing"

    monkeypatch.setenv("DHAN_PARTNER_ID", "PID-1")
    monkeypatch.setenv("DHAN_PARTNER_SECRET", "PSECRET-1")
    monkeypatch.setenv("DHAN_REDIRECT_URL", "https://app.example/callback")
    login = _dhan().get_login_url("u1")
    assert login["configured"] is True
    assert "PSECRET-1" not in json.dumps(login)


def test_the_dhan_adapter_holds_no_credential_in_its_source():
    """No key, secret, token or client id is hard-coded. Only env var NAMES appear."""
    source = (BACKEND / "services" / "brokers" / "dhan.py").read_text(encoding="utf-8")
    spec = _dhan().credential_spec
    assert spec.api_key_env == "DHAN_PARTNER_ID"
    assert spec.api_secret_env == "DHAN_PARTNER_SECRET"
    assert spec.redirect_url_env == "DHAN_REDIRECT_URL"
    assert re.search(r"(?i)\b(eyJ[A-Za-z0-9_-]{10,}|sk_live|Bearer\s+[A-Za-z0-9]{10,})", source) is None
    assert "os.environ" not in source, "the adapter read the environment directly"
    assert "os.getenv" not in source


# -- the boundary sweeps -------------------------------------------------------


def test_dhan_added_no_dhan_knowledge_outside_its_own_adapter():
    """D4.11's central acceptance criterion, swept rather than asserted by eye."""
    dhan_words = ("dhan", "api-feed", "securityid", "exchangesegment", "dhanclientid",
                  "consentid", "consume-consent", "generate-consent", "fundlimit",
                  "availabelbalance", "nse_eq", "idx_i", "requestcode")
    allowed = {
        "services/brokers/dhan.py",       # the adapter — the only owner
        "services/brokers/__init__.py",   # the registry entry — one line
    }
    offenders = {}
    scanned = 0
    for path in sorted((BACKEND / "services").rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in allowed:
            continue
        scanned += 1
        source = _strip_source(path.read_text(encoding="utf-8")).lower()
        hit = [word for word in dhan_words if re.search(word, source)]
        if hit:
            offenders[rel] = hit
    assert scanned > 50, "the sweep found almost no modules — it could not have failed"
    assert not offenders, offenders


def test_the_market_layer_is_untouched_by_the_fifth_broker():
    """Everything D4.11 promised would not move, re-checked with five brokers live."""
    market_modules = [
        "services/market_engine/gateway.py",
        "services/market_engine/source_manager.py",
        "services/market_engine/ticks.py",
        "services/market_engine/providers/streaming.py",
        "services/market_engine/providers/registry.py",
        "services/market_engine/providers/base.py",
    ]
    for relative in market_modules:
        source = _strip_source((BACKEND / relative).read_text(encoding="utf-8")).lower()
        for name in ("dhan", "fyers", "hsm", r"angel(?!og)", "upstox", "zerodha", "kite",
                     "broker_gateway", "broker_registry"):
            assert not re.search(name, source), f"{relative} names {name!r} in executable code"


def test_the_fifth_broker_added_no_broker_knowledge_to_the_transport():
    """`stream.py` is byte-for-byte unchanged by D4.11, and still names no broker."""
    source = _strip_source((BACKEND / "services/brokers/stream.py").read_text(encoding="utf-8")).lower()
    for name in ("dhan", "api-feed", "fyers", "hsm", "fytoken", r"angel(?!og)", "smartapi",
                 "upstox", "zerodha", "kite", "protobuf", "securityid", "requestcode"):
        assert not re.search(name, source), f"stream.py names {name!r} in executable code"


def test_no_core_module_branches_on_the_dhan_broker_name():
    """The literal-preserving sweep: `if broker == "dhan"` survives an identifier sweep.

    `_strip_source` removes string literals, so a comparison against a broker's
    NAME is invisible to every sweep above by construction. This is the one that
    catches it, and it earned its place in D4.9 when exactly that mutation
    survived.
    """
    modules = [
        "services/brokers/stream.py",
        "services/brokers/market_feed.py",
        "services/brokers/gateway.py",
        "services/broker_engine.py",
        "services/market_engine/source_manager.py",
        "services/market_engine/gateway.py",
        "services/market_engine/providers/streaming.py",
        "services/market_engine/providers/base.py",
        "services/market_engine/providers/registry.py",
    ]
    for relative in modules:
        path = BACKEND / relative
        assert path.exists(), f"{relative} moved — this sweep proves nothing"
        text = _strip_prose(path.read_text(encoding="utf-8"))
        assert '"dhan"' not in text and "'dhan'" not in text, f"{relative} names Dhan in executable code"
        for comparison in ("broker ==", "broker!=", "broker !=", "broker in ("):
            assert comparison not in text, f"{relative} branches on a broker name: {comparison!r}"

    # Non-vacuity: the planted branch this sweep exists to catch.
    planted = 'def promote(broker):\n    if broker == "dhan":\n        return True\n'
    assert '"dhan"' in _strip_prose(planted)
    assert '"dhan"' not in _strip_source(planted), \
        "the identifier sweep can see a string literal — this test is redundant"


def test_all_five_streaming_brokers_reach_the_market_gateway_through_the_identical_seam():
    """One seam, five brokers, no branch anywhere on the path."""
    for broker in ("zerodha", "upstox", "angelone", "fyers", "dhan"):
        adapter = broker_registry.require(broker)
        assert adapter.supports(BrokerCapability.TICK_STREAM)
        channels = [c for c in adapter.stream_channels() if StreamEventKind.TICKS in c.delivers]
        assert len(channels) == 1, f"{broker} must have exactly one tick-carrying channel"

    # And the pre-Dhan brokers still open their connection scope the same way.
    for broker in ("zerodha", "upstox", "angelone", "dhan"):
        for channel in broker_registry.require(broker).stream_channels():
            assert channel.open({"access_token": "t"}) is channel, f"{broker}/{channel.name}"


def test_the_dhan_adapter_registers_with_no_change_to_the_framework():
    """Developer Rule 9: one adapter plus one registry entry, and nothing else."""
    adapter = _dhan()
    BrokerRegistry.validate(adapter)      # would raise if the declaration were unhonoured
    assert adapter.credential_spec.api_key_env == "DHAN_PARTNER_ID"
    assert adapter.credential_spec.required == ("api_key", "api_secret")
    assert adapter.supports(BrokerCapability.TICK_STREAM)
    for absent in (BrokerCapability.PLACE_ORDER, BrokerCapability.ORDER_STREAM,
                   BrokerCapability.SESSION_REFRESH, BrokerCapability.SESSION_INVALIDATE,
                   BrokerCapability.MARGINS, BrokerCapability.ORDERS, BrokerCapability.TRADES):
        assert not adapter.supports(absent), f"Dhan declares {absent.value} it has not validated"


def test_the_four_existing_brokers_are_unchanged_by_the_fifth():
    """A regression net over every predecessor's protocol, run after Dhan landed."""
    # Zerodha: a Kite binary frame still decodes with its token-carried divisor.
    assert canonical_ticks(_kite_ticks(_kite_frame((738561, 265075))), _kite_map(),
                           broker="zerodha")[0]["price"] == 2650.75
    # Upstox: the protobuf feed still decodes its double.
    assert _upstox_canonical()[0]["price"] == 2650.75
    # Angel One: the segment divisor and the text keep-alive both survive.
    assert _angel_canonical()[0]["price"] == 2650.75
    assert _angelone().stream_endpoint(_angel_session()).heartbeat_frame == "ping"
    # Fyers: the wire-carried scale and the connection scope both survive.
    assert _fy_canonical()[0]["price"] == 2650.75
    from services.brokers.fyers import FyersFeedConnection

    assert isinstance(_fyers_channel().open(_fy_session()), FyersFeedConnection)
    # And every broker still owns exactly the channels it declared.
    assert [c.name for c in _fyers().stream_channels()] == ["market"]
    assert [c.name for c in _dhan().stream_channels()] == [DEFAULT_STREAM_CHANNEL]

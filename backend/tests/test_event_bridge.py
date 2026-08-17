"""Sprint R2 — Event Bus & Infrastructure tests (hermetic, no Redis/live market).

Covers the real-time backbone wired in Sprint R2:
  • EventBus "*" catch-all matching (forwards every event to the bridge)
  • ConnectionManager channel model: subscribe/unsubscribe, channel-scoped
    delivery, dead-socket + disconnect cleanup
  • event bridge routing: channel broadcast vs per-user (user_id) delivery,
    stable "event" envelope + channel resolution
  • cache Redis Pub/Sub graceful no-op when REDIS_URL is unset
  • create_notification: persists one doc AND publishes notification.created

Everything runs in-process with a FakeDB and fake WebSockets — no Redis, no
Mongo, no network.
"""
import asyncio

from tests._fakedb import FakeDB

from services.market_engine.event_bus import EventBus, event_bus
from services.realtime import event_bridge as bridge


def _run(coro):
    return asyncio.run(coro)


class FakeWS:
    """Minimal async WebSocket double recording everything sent to it."""

    def __init__(self, fail=False):
        self.sent = []
        self.raw_sent = []  # pre-serialized payloads (Sprint R9 fan-out path)
        self.fail = fail

    # Signature mirrors `starlette.websockets.WebSocket.accept` (PH3.10). The
    # selected subprotocol is recorded rather than dropped: it is how the auth
    # handshake echoes its marker back, and a double that silently ignored the
    # argument would keep passing after the real call stopped working.
    async def accept(self, subprotocol=None, headers=None):
        self.accepted_subprotocol = subprotocol
        return None

    async def send_json(self, message):
        if self.fail:
            raise RuntimeError("socket dead")
        self.sent.append(message)

    async def send_text(self, payload):
        # Fan-out paths serialize once and send text (Sprint R9). Decode back
        # to a dict so existing assertions keep working unchanged.
        import json
        if self.fail:
            raise RuntimeError("socket dead")
        self.raw_sent.append(payload)
        self.sent.append(json.loads(payload))


def _new_manager():
    """A fresh ConnectionManager (imported lazily so importing server, which
    also starts app wiring, only happens when this test module runs)."""
    from server import ConnectionManager
    return ConnectionManager()


# ---------------------------------------------------------------------------
# EventBus "*" catch-all
# ---------------------------------------------------------------------------

def test_wildcard_star_catches_every_event():
    async def run():
        bus = EventBus()
        seen = []

        async def handler(evt):
            seen.append(evt["type"])

        bus.subscribe("*", handler)
        await bus.publish("price.updated", {"symbol": "RELIANCE"})
        await bus.publish("scanner.updated", {"strategy": "breakout"})
        await bus.publish("anything.at.all", {})
        return seen

    seen = _run(run())
    assert seen == ["price.updated", "scanner.updated", "anything.at.all"]


def test_prefix_and_exact_matching_still_work():
    async def run():
        bus = EventBus()
        exact, prefix = [], []

        async def on_exact(evt):
            exact.append(evt["type"])

        async def on_prefix(evt):
            prefix.append(evt["type"])

        bus.subscribe("price.updated", on_exact)
        bus.subscribe("sector.*", on_prefix)
        await bus.publish("price.updated", {})
        await bus.publish("sector.analyzed", {})
        await bus.publish("news.received", {})  # matched by neither
        return exact, prefix

    exact, prefix = _run(run())
    assert exact == ["price.updated"]
    assert prefix == ["sector.analyzed"]


# ---------------------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------------------

def test_resolve_channel_mapping():
    assert bridge.resolve_channel("price.updated") == "market"
    assert bridge.resolve_channel("market.index.updated") == "market"
    assert bridge.resolve_channel("sector.analyzed") == "sectors"
    assert bridge.resolve_channel("scanner.updated") == "scanner"
    assert bridge.resolve_channel("scanner.breakout") == "scanner"
    assert bridge.resolve_channel("scanner.volume_spike") == "scanner"
    assert bridge.resolve_channel("scanner.momentum") == "scanner"
    assert bridge.resolve_channel("news.received") == "news"
    assert bridge.resolve_channel("notification.created") == "notifications"
    assert bridge.resolve_channel("portfolio.updated") == "portfolio"
    assert bridge.resolve_channel("portfolio.synced") == "portfolio"
    # Unknown domain falls through to the domain name itself.
    assert bridge.resolve_channel("custom.thing") == "custom"


# ---------------------------------------------------------------------------
# ConnectionManager channel model
# ---------------------------------------------------------------------------

def test_channel_broadcast_only_reaches_subscribers():
    async def run():
        mgr = _new_manager()
        a, b = FakeWS(), FakeWS()
        await mgr.connect(a, "u1")
        await mgr.connect(b, "u2")
        mgr.subscribe(a, ["market"])
        # b subscribes to nothing
        await mgr.broadcast_to_channel("market", {"type": "event", "channel": "market"})
        return a.sent, b.sent

    a_sent, b_sent = _run(run())
    assert len(a_sent) == 1
    assert b_sent == []


def test_wildcard_channel_subscriber_receives_all():
    async def run():
        mgr = _new_manager()
        w = FakeWS()
        await mgr.connect(w, "u1")
        mgr.subscribe(w, ["*"])
        await mgr.broadcast_to_channel("scanner", {"type": "event"})
        await mgr.broadcast_to_channel("news", {"type": "event"})
        return w.sent

    assert len(_run(run())) == 2


def test_unsubscribe_stops_delivery():
    async def run():
        mgr = _new_manager()
        w = FakeWS()
        await mgr.connect(w, "u1")
        mgr.subscribe(w, ["market"])
        mgr.unsubscribe(w, ["market"])
        await mgr.broadcast_to_channel("market", {"type": "event"})
        return w.sent

    assert _run(run()) == []


def test_disconnect_cleans_channels():
    async def run():
        mgr = _new_manager()
        w = FakeWS()
        await mgr.connect(w, "u1")
        mgr.subscribe(w, ["market"])
        mgr.disconnect(w, "u1")
        assert w not in mgr.channels
        assert w not in mgr.active
        # A post-disconnect channel broadcast reaches nobody and does not raise.
        await mgr.broadcast_to_channel("market", {"type": "event"})
        return True

    assert _run(run()) is True


def test_dead_socket_is_reaped_on_channel_broadcast():
    async def run():
        mgr = _new_manager()
        good, bad = FakeWS(), FakeWS(fail=True)
        await mgr.connect(good, "u1")
        await mgr.connect(bad, "u2")
        mgr.subscribe(good, ["market"])
        mgr.subscribe(bad, ["market"])
        await mgr.broadcast_to_channel("market", {"type": "event"})
        return mgr, good, bad

    mgr, good, bad = _run(run())
    assert bad not in mgr.active
    assert bad not in mgr.channels
    assert len(good.sent) == 1


# ---------------------------------------------------------------------------
# Bridge routing
# ---------------------------------------------------------------------------

def test_bridge_broadcasts_public_event_to_channel():
    async def run():
        mgr = _new_manager()
        w = FakeWS()
        await mgr.connect(w, "u1")
        mgr.subscribe(w, ["market"])
        handler = bridge.make_bridge(mgr)
        await handler({"type": "price.updated", "data": {"symbol": "RELIANCE"}, "timestamp": "t"})
        return w.sent

    sent = _run(run())
    assert len(sent) == 1
    env = sent[0]
    assert env["type"] == "event"
    assert env["event"] == "price.updated"
    assert env["channel"] == "market"
    assert env["data"] == {"symbol": "RELIANCE"}


def test_bridge_routes_user_scoped_event_to_owner_only():
    async def run():
        mgr = _new_manager()
        owner, other = FakeWS(), FakeWS()
        await mgr.connect(owner, "u1")
        await mgr.connect(other, "u2")
        # Neither subscribes to a channel — user routing must not depend on it.
        handler = bridge.make_bridge(mgr)
        await handler({
            "type": "notification.created",
            "data": {"user_id": "u1", "message": "hi"},
            "timestamp": "t",
        })
        return owner.sent, other.sent

    owner_sent, other_sent = _run(run())
    assert len(owner_sent) == 1
    assert owner_sent[0]["event"] == "notification.created"
    assert other_sent == []


# ---------------------------------------------------------------------------
# Redis Pub/Sub graceful no-op (no REDIS_URL)
# ---------------------------------------------------------------------------

def test_cache_publish_is_noop_without_redis(monkeypatch):
    from services import cache
    monkeypatch.delenv("REDIS_URL", raising=False)
    # Force a clean "no redis" state for this process.
    monkeypatch.setattr(cache, "_redis_client", None, raising=False)
    monkeypatch.setattr(cache, "_redis_failed", False, raising=False)

    async def run():
        published = await cache.cache_publish("sa:events", {"x": 1})
        task = await cache.start_pubsub_listener("sa:events", lambda p: None)
        return published, task

    published, task = _run(run())
    assert published is False
    assert task is None


# ---------------------------------------------------------------------------
# create_notification: persist + publish
# ---------------------------------------------------------------------------

def test_create_notification_persists_and_publishes():
    async def run():
        from services.notification_service import create_notification
        db = FakeDB()
        events = []

        async def spy(evt):
            events.append(evt)

        event_bus.subscribe("notification.created", spy)
        try:
            doc = await create_notification(
                db, "u1",
                type="MARKET_ALERT", title="Market Alert",
                message="Nifty surging", severity="warning",
                data={"nifty": 25000},
            )
        finally:
            event_bus.unsubscribe("notification.created", spy)
        return db, doc, events

    db, doc, events = _run(run())
    assert doc is not None
    assert len(db.notifications.docs) == 1
    assert len(events) == 1
    payload = events[0]["data"]
    assert payload["user_id"] == "u1"
    assert payload["type"] == "MARKET_ALERT"
    assert payload["nifty"] == 25000


def test_create_notification_dedupe_suppresses_repeat():
    async def run():
        from services.notification_service import create_notification
        db = FakeDB()
        first = await create_notification(
            db, "u1", type="MARKET_ALERT", title="t", message="m", dedupe_minutes=5,
        )
        second = await create_notification(
            db, "u1", type="MARKET_ALERT", title="t", message="m", dedupe_minutes=5,
        )
        return first, second, db

    first, second, db = _run(run())
    assert first is not None
    assert second is None
    assert len(db.notifications.docs) == 1

"""Event bus → WebSocket bridge (Sprint R2).

The market engine publishes domain events (``price.updated``, ``sector.updated``,
``scanner.updated``, ``notification.created`` …) onto the in-process
``event_bus``. Historically nothing subscribed to that bus, so the doc's core
flow — *Market Engine → Event → Socket → only the affected component* — was
never realized (gap G1 in REALTIME_MIGRATION_PLAN.md).

This module registers a single catch-all subscriber that:

1. Maps each event to a **socket channel** (market/sectors/scanner/news/…).
2. Wraps it in a stable ``event`` envelope and delivers it to the right sockets
   — per-user when the payload carries a ``user_id``, and, for the domains that
   are inherently private, **only** then (see ``PRIVATE_DOMAINS``).
3. Mirrors the event to Redis Pub/Sub so **other processes** deliver it to their
   own local sockets (gap G2). A per-process ``ORIGIN_ID`` guard prevents a
   process from re-delivering its own event, and remote delivery never
   re-publishes to the bus, so there is no feedback loop.

Everything degrades gracefully: with no ``REDIS_URL`` the Redis steps are
no-ops and delivery is purely in-process.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.cache import cache_publish, start_pubsub_listener
from services.market_engine.event_bus import event_bus

logger = logging.getLogger(__name__)

# Redis channel that carries every bridged event between processes.
REDIS_EVENT_CHANNEL = "sa:events"

# Unique per-process id used to drop our own echoes coming back over Redis.
ORIGIN_ID = uuid.uuid4().hex

#: Domains whose every event is about ONE account and can never be broadcast.
#:
#: D6.1 / S6 (was HIGH, structural). Delivery used to read:
#:
#:     if user_id:  send_to_user(...)
#:     else:        broadcast_to_channel(...)
#:
#: i.e. "private if the publisher remembered to say so". `trade`, `portfolio`,
#: `broker`, `notification` and `watchlist` are all routed here, all carry one
#: user's business, and all fell through to a broadcast channel the moment a
#: publisher omitted one keyword argument — with nothing failing and nothing
#: logged. D5.16's watchlist P0 was exactly that failure, already realised once.
#:
#: The rule is now the domain's, not the publisher's: an event in one of these
#: domains without a `user_id` is DROPPED. A publisher that forgets loses its
#: event — loudly, at WARNING — rather than sending it to everybody.
#:
#: WHY A DENY-LIST OF DOMAINS RATHER THAN AN ALLOW-LIST OF EVENTS.
#: `resolve_channel` deliberately falls through to the domain name for unlisted
#: domains so a new event family routes somewhere sensible without editing this
#: file. That fall-through is load-bearing for public market families
#: (`provider.status` relies on it) and would be a hazard for private ones — but
#: a private domain is not one a new event family arrives in by accident:
#: `trade`, `portfolio`, `broker`, `notification` and `watchlist` already exist
#: and are already named here. `tests/test_d61_security.py` asserts every
#: DOMAIN_CHANNEL entry is classified one way or the other, so adding a sixth
#: private domain to the map without deciding its scope fails the suite.
PRIVATE_DOMAINS: frozenset = frozenset({
    "trade", "portfolio", "broker", "notification", "watchlist",
})

# Map an event's leading domain segment to a socket channel. Unlisted domains
# fall through to the domain name itself, so new event families still route
# somewhere sensible without a code change here.
DOMAIN_CHANNEL: Dict[str, str] = {
    "price": "market",
    "market": "market",
    "breadth": "market",
    "calendar": "market",
    "sector": "sectors",
    "scanner": "scanner",
    "news": "news",
    "notification": "notifications",
    "portfolio": "portfolio",
    "trade": "trades",
    "watchlist": "watchlist",
    "ai": "ai",
    "broker": "broker",
    # The morning report is an AI product — its ready-signal rides the ai
    # channel every dashboard already subscribes to (Sprint R8).
    "morningreport": "ai",
}


#: Socket channels that carry private domains. `ConnectionManager.subscribe`
#: refuses these: after the fix above nothing is ever broadcast to them, so a
#: subscription grants nothing — but a channel nobody can subscribe to is also a
#: channel a future broadcast cannot leak through.
PRIVATE_CHANNELS: frozenset = frozenset(
    channel for domain, channel in DOMAIN_CHANNEL.items() if domain in PRIVATE_DOMAINS
)


def domain_of(event_type: str) -> str:
    """The leading domain segment of an event type (``trade.closed`` -> ``trade``)."""
    return (event_type or "").split(".", 1)[0]


def is_private_event(event_type: str) -> bool:
    """True when this event family may only ever be delivered to one user."""
    return domain_of(event_type) in PRIVATE_DOMAINS


def resolve_channel(event_type: str) -> str:
    """Return the socket channel for an event type (e.g. ``price.updated`` →
    ``market``, ``sector.analyzed`` → ``sectors``)."""
    domain = (event_type or "").split(".", 1)[0]
    return DOMAIN_CHANNEL.get(domain, domain or "market")


def _envelope(event: Dict[str, Any], channel: str) -> Dict[str, Any]:
    """Build the client-facing message for a bus event."""
    return {
        "type": "event",
        "event": event.get("type"),
        "channel": channel,
        "data": event.get("data", {}),
        "timestamp": event.get("timestamp")
        or datetime.now(timezone.utc).isoformat(),
    }


async def _deliver(ws_manager, event: Dict[str, Any]) -> None:
    """Deliver a single bus event to local sockets (no Redis, no bus).

    Shared by both the local bus subscriber and the remote Redis listener so
    delivery semantics stay identical regardless of where the event originated.
    """
    event_type = event.get("type") or ""
    data = event.get("data") or {}
    channel = resolve_channel(event_type)
    envelope = _envelope(event, channel)

    user_id = data.get("user_id")
    if user_id:
        # Per-user events (notifications, per-user portfolio/broker updates).
        await ws_manager.send_to_user(str(user_id), envelope)
        return
    if is_private_event(event_type):
        # FAIL CLOSED (D6.1 / S6). The publisher did not say whose event this
        # is, and the domain says it belongs to somebody. Broadcasting it is the
        # one outcome that is certainly wrong, so it is dropped.
        #
        # WARNING, not debug: this is a publisher bug and it is silent by nature
        # — the event simply does not arrive, which looks like a realtime
        # hiccup. This line is the only thing that will point at the real cause.
        logger.warning(
            "Dropped private event %r: no user_id in payload. A %s.* event must "
            "carry the owning user's id; see PRIVATE_DOMAINS in this module.",
            event_type, domain_of(event_type),
        )
        return
    await ws_manager.broadcast_to_channel(channel, envelope)


def make_bridge(ws_manager):
    """Return the catch-all bus handler bound to `ws_manager`.

    Delivers locally, then mirrors to Redis for cross-process fan-out.
    """
    async def _handler(event: Dict[str, Any]) -> None:
        await _deliver(ws_manager, event)
        # Mirror to other processes; no-op when Redis is not configured.
        await cache_publish(REDIS_EVENT_CHANNEL, {"origin": ORIGIN_ID, "event": event})

    return _handler


#: The catch-all bus subscriber, once registered. PH3.6 — the registration was
#: unconditional, so a second call to `start_event_bridge` added a SECOND "*"
#: handler and every bus event was delivered to every socket twice, forever.
#: Only startup calls this today, which is exactly why it was easy to miss and
#: why the guard belongs here rather than in the caller: `redis_pubsub` already
#: dedupes its half of this same wiring for the same reason (a retried boot, a
#: test that re-invokes wiring, a future hot-reload), and duplicate delivery is
#: much harder to notice than no delivery — the UI just updates twice.
_bridge_handler: Optional[Any] = None


def reset_for_tests() -> None:
    """Forget the registered bridge handler (test isolation)."""
    global _bridge_handler
    if _bridge_handler is not None:
        event_bus.unsubscribe("*", _bridge_handler)
    _bridge_handler = None


async def start_event_bridge(ws_manager) -> Optional[Any]:
    """Wire the bridge: subscribe to the bus and start the Redis listener.

    Idempotent in both halves: the bus subscription is registered at most once
    per process (see `_bridge_handler`), and `start_pubsub_listener` returns the
    existing subscriber for a channel rather than creating a second one.

    Returns the Redis listener task (or None when Redis is unavailable). Call
    once at application startup after the WebSocket manager exists.
    """
    global _bridge_handler
    if _bridge_handler is None:
        _bridge_handler = make_bridge(ws_manager)
        event_bus.subscribe("*", _bridge_handler)
        logger.info("Event bridge: subscribed to market event bus")
    else:
        logger.debug("Event bridge: bus subscription already registered; reusing")

    async def _on_remote(payload: Dict[str, Any]) -> None:
        # Skip our own events echoed back by Redis; deliver everyone else's.
        if payload.get("origin") == ORIGIN_ID:
            return
        event = payload.get("event")
        if isinstance(event, dict):
            await _deliver(ws_manager, event)

    task = await start_pubsub_listener(REDIS_EVENT_CHANNEL, _on_remote)
    if task is not None:
        logger.info("Event bridge: Redis cross-process fan-out active")
    return task

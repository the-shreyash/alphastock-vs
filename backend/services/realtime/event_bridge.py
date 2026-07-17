"""Event bus → WebSocket bridge (Sprint R2).

The market engine publishes domain events (``price.updated``, ``sector.updated``,
``scanner.updated``, ``notification.created`` …) onto the in-process
``event_bus``. Historically nothing subscribed to that bus, so the doc's core
flow — *Market Engine → Event → Socket → only the affected component* — was
never realized (gap G1 in REALTIME_MIGRATION_PLAN.md).

This module registers a single catch-all subscriber that:

1. Maps each event to a **socket channel** (market/sectors/scanner/news/…).
2. Wraps it in a stable ``event`` envelope and delivers it to the right sockets
   — per-user when the payload carries a ``user_id`` (e.g. notifications),
   otherwise to every socket subscribed to the event's channel.
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
    else:
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


async def start_event_bridge(ws_manager) -> Optional[Any]:
    """Wire the bridge: subscribe to the bus and start the Redis listener.

    Returns the Redis listener task (or None when Redis is unavailable). Call
    once at application startup after the WebSocket manager exists.
    """
    event_bus.subscribe("*", make_bridge(ws_manager))
    logger.info("Event bridge: subscribed to market event bus")

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

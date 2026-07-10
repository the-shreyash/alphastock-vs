"""Internal event bus for market engine events.

Provides a publish/subscribe pattern for decoupled communication between
market engine components. Events flow through this bus to notify the AI
system, WebSocket layer, and other consumers about market state changes.

Events:
    market.open / market.close       Market session transitions
    price.updated                    Stock price change
    sector.updated                   Sector performance recalculated
    news.received                    New article classified
    scanner.updated                  Scanner results refreshed
    opportunity.detected             Trading opportunity found
    market.alert                     Significant market movement
    calendar.event                   Economic calendar event
    breadth.updated                  Market breadth recalculated
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)

EventHandler = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


class EventBus:
    """Async in-process event bus with topic-based publish/subscribe."""

    def __init__(self) -> None:
        self._handlers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._event_log: List[Dict[str, Any]] = []
        self._max_log_size = 500

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for an event type. Supports wildcards via prefix
        matching: subscribing to ``market.*`` catches ``market.open``,
        ``market.close``, etc. Subscribing to ``*`` catches every event (used by
        the WebSocket bridge to forward all domains without enumerating prefixes)."""
        self._handlers[event_type].append(handler)
        logger.debug(f"EventBus: subscribed to '{event_type}'")

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove a previously registered handler."""
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Publish an event. All matching handlers are invoked concurrently.
        Failures in individual handlers are logged but do not propagate."""
        event = {
            "type": event_type,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Persist to in-memory log (bounded)
        self._event_log.append(event)
        if len(self._event_log) > self._max_log_size:
            self._event_log = self._event_log[-self._max_log_size:]

        # Collect matching handlers (exact match + wildcard prefix + global "*")
        matched: List[EventHandler] = []
        for pattern, handlers in self._handlers.items():
            if pattern == "*":
                matched.extend(handlers)
            elif pattern == event_type:
                matched.extend(handlers)
            elif pattern.endswith(".*") and event_type.startswith(pattern[:-1]):
                matched.extend(handlers)

        if not matched:
            return

        # Fire all handlers concurrently
        results = await asyncio.gather(
            *[self._safe_invoke(h, event) for h in matched],
            return_exceptions=True,
        )
        failures = sum(1 for r in results if isinstance(r, Exception))
        if failures:
            logger.warning(
                f"EventBus: {failures}/{len(matched)} handlers failed for '{event_type}'"
            )

    async def _safe_invoke(self, handler: EventHandler, event: Dict[str, Any]) -> None:
        """Invoke a handler with exception isolation."""
        try:
            await handler(event)
        except Exception as exc:
            logger.error(f"EventBus handler error: {exc}", exc_info=True)
            raise

    def recent_events(self, event_type: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent events, optionally filtered by type prefix."""
        events = self._event_log
        if event_type:
            events = [e for e in events if e["type"].startswith(event_type)]
        return events[-limit:]

    @property
    def subscriber_count(self) -> int:
        return sum(len(h) for h in self._handlers.values())

    @property
    def event_types(self) -> List[str]:
        return list(self._handlers.keys())


# Singleton instance shared across the application
event_bus = EventBus()

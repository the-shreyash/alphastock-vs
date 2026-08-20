"""Internal event bus for market engine events.

Provides a publish/subscribe pattern for decoupled communication between
market engine components. Events flow through this bus to notify the AI
system, WebSocket layer, and other consumers about market state changes.

Events:
    market.open / market.close       Market session transitions
    price.updated                    Stock price change
    provider.status                  Market feed state changed (D1). Payload:
                                     state ("available"|"unavailable"), tier
                                     ("streaming"|"delayed"|null),
                                     previous_tier, capabilities[]. Carries the
                                     freshness TIER only — never a provider
                                     name (MARKET_DATA_ARCHITECTURE.md,
                                     Developer Rule 4). Published by the Source
                                     Manager, change-gated.
    sector.updated                   Sector performance recalculated
    news.received                    New article classified
    scanner.updated                  Scanner results refreshed (data.source:
                                     "worker" = continuous sweep, "api" = REST)
    scanner.breakout                 NEW breakout hit (novelty-gated)
    scanner.volume_spike             NEW volume-spike hit (novelty-gated)
    scanner.momentum                 NEW momentum hit (novelty-gated)
    opportunity.detected             Trading opportunity found
    market.alert                     Significant market movement
    calendar.event                   Economic calendar event
    breadth.updated                  Market breadth recalculated
    portfolio.updated                Per-user live portfolio snapshot (data
                                     carries user_id; data.reason: "monitor" |
                                     "broker_tick" | "broker_sync")
    portfolio.synced                 Per-user broker portfolio sync completed
    trade.updated                    Per-user open-trades snapshot (data
                                     carries user_id + trades[]; data.reason:
                                     "monitor" | "broker_tick" | "engine")
    trade.trailing_stop              Per-user: trailing stop ratcheted
                                     (old_stop → new_stop, best_price)
    trade.target_hit                 Per-user: target level hit (level,
                                     quantity, auto, order_id when auto-exit)
    trade.sl_hit                     Per-user: stop loss breached
    trade.closed                     Per-user: trade fully closed (data.source:
                                     "engine" | "manual"; pnl, pnl_percent)
    trade.review.ready               Per-user: AI trade review generated for a
                                     closed trade (data.review)
    broker.order.updated             Per-user: live broker order status change
                                     (data.order — normalized order fields)
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

from observability import instruments

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
            # Counted before returning (PH3.7): an event with no listener is
            # still throughput, and "nobody is subscribed" is itself a defect
            # worth being able to see — publishes climbing while
            # `event_bus_handler_failures_total` and every downstream effect
            # stay at zero is what a lost subscription looks like.
            instruments.record_event_published(event_type)
            return

        # Fire all handlers concurrently
        results = await asyncio.gather(
            *[self._safe_invoke(h, event) for h in matched],
            return_exceptions=True,
        )
        failures = sum(1 for r in results if isinstance(r, Exception))
        # PH3.7. Each failure here is a domain action that silently did not
        # happen — a portfolio that never resynced, a notification never sent.
        # Before this it produced one WARNING line and no countable signal.
        instruments.record_event_published(event_type, failures)
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

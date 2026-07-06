"""AI activity logging service with WebSocket broadcasting support."""
import collections
import logging
import asyncio
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)

# In-memory deque holding the last 50 activities
activity_deque = collections.deque(maxlen=50)
_broadcast_callbacks = []


def register_broadcast_callback(cb: Callable):
    """Register callback to be triggered when a new activity is logged."""
    _broadcast_callbacks.append(cb)


def log_activity(action: str, category: str, status: str = "done"):
    """
    Log an AI activity and broadcast it to all registered handlers.
    Categories: 'scan', 'news', 'alert', 'rank', 'monitor'
    Statuses: 'running', 'done', 'warning'
    """
    entry = {
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "action": action,
        "category": category,
        "status": status
    }
    activity_deque.append(entry)

    # Trigger all callbacks
    for cb in _broadcast_callbacks:
        try:
            if asyncio.iscoroutinefunction(cb):
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(cb(entry))
                else:
                    asyncio.run(cb(entry))
            else:
                cb(entry)
        except Exception as e:
            logger.error(f"Activity broadcast callback error: {e}")


def get_recent_activity():
    """Return the last 20 entries as a list (newest first)."""
    return list(activity_deque)[::-1][:20]


# NOTE: The feed intentionally starts EMPTY. It is filled within seconds of
# startup by the AI heartbeat engine (services/heartbeat_engine.py), which logs
# a truthful running -> done/warning trace of real background work (live market
# fetches, news scans, trade monitoring). No fake pre-population.

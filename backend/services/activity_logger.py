"""AI activity logging service with WebSocket broadcasting support."""
import collections
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

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


# Pre-populate with realistic startup messages
def _prepopulate():
    now = datetime.now(timezone.utc)
    startup_events = [
        ("Checking US market overnight movement", "scan", "done", 15),
        ("Reading RBI and economic news", "news", "done", 12),
        ("Analyzing Bank Nifty structure", "scan", "done", 10),
        ("Scanning 50 NSE stocks for setups", "scan", "done", 8),
        ("Detecting unusual volume patterns", "scan", "done", 5),
        ("Ranking top 3 setups by confidence", "rank", "done", 3),
        ("Monitoring active trades", "monitor", "running", 1),
    ]
    for action, category, status, min_ago in startup_events:
        t_str = (now - timedelta(minutes=min_ago)).strftime("%H:%M:%S")
        activity_deque.append({
            "time": t_str,
            "action": action,
            "category": category,
            "status": status
        })


_prepopulate()

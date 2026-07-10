"""Notification helper (Sprint R2).

Single entry point for creating a user notification. It persists the document
to ``db.notifications`` (unchanged shape) **and** publishes a
``notification.created`` event on the market event bus so the WebSocket bridge
can push it to that user's live sockets in real time — replacing the previous
pattern where notifications were only ever polled.

Existing insert sites can migrate to this helper incrementally; nothing breaks
if some continue to insert directly (they simply won't get the live push).
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from services.market_engine.event_bus import event_bus

logger = logging.getLogger(__name__)


async def create_notification(
    db,
    user_id: str,
    *,
    type: str,
    title: str,
    message: str,
    severity: str = "info",
    data: Optional[Dict[str, Any]] = None,
    dedupe_minutes: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Persist a notification and publish ``notification.created``.

    When `dedupe_minutes` is set, a notification of the same `type` created for
    this user within the window suppresses the new one (returns None) — used to
    avoid spamming repeated market alerts.

    Returns the stored document, or None when suppressed by dedupe.
    """
    now = datetime.now(timezone.utc)

    if dedupe_minutes:
        cutoff = (now - timedelta(minutes=dedupe_minutes)).isoformat()
        existing = await db.notifications.find_one({
            "user_id": str(user_id),
            "type": type,
            "created_at": {"$gte": cutoff},
        })
        if existing:
            return None

    doc = {
        "user_id": str(user_id),
        "type": type,
        "title": title,
        "message": message,
        "severity": severity,
        "read": False,
        "created_at": now.isoformat(),
    }
    result = await db.notifications.insert_one(doc)
    doc["_id"] = str(result.inserted_id)

    # Publish for the real-time bridge. user_id in the payload routes it to the
    # owning user's sockets (per-user delivery), not a public broadcast.
    try:
        await event_bus.publish("notification.created", {
            "user_id": str(user_id),
            "notification_id": doc["_id"],
            "type": type,
            "title": title,
            "message": message,
            "severity": severity,
            **(data or {}),
        })
    except Exception as e:
        logger.warning(f"notification.created publish failed: {e}")

    return doc

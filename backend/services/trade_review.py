"""AI Trade Review pipeline (Sprint R6).

REALTIME_SYSTEM.md's trade monitor flow ends *Trade Closed → Journal Updated →
AI Trade Review Starts*. Pre-R6 the review existed only behind the manual
``POST /api/ai/trade-review`` endpoint; this module makes it start
automatically when a trade fully closes — engine auto-exit (scheduler) and
manual close (exit/update endpoints) both call
:func:`generate_close_intelligence` as a background task.

On success the cached review is persisted on the trade document (same
``ai_review`` shape the endpoint uses, so the endpoint serves it as a cache
hit) and the user is told twice, live: a ``trade.review.ready`` bus event
(per-user, ``trades`` channel — the Trade Monitor History tab renders it
inline) and a ``notification.created`` toast.

No fabrication: when no AI provider is configured, or the model call fails,
nothing is persisted or published.
"""
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


def _oid(trade_id):
    """ObjectId when possible; raw id otherwise (hermetic FakeDB tests)."""
    try:
        from bson import ObjectId
        return ObjectId(trade_id)
    except Exception:
        return trade_id


def summarize_trade_for_review(t: dict) -> str:
    """Build a factual, number-only summary of a trade for the review prompt.

    Feeds the AI ONLY real values from the trade document — never invented
    context — honouring the no-fabrication rule in PROMPT.md."""
    pnl = t.get("pnl")
    pnl_pct = t.get("pnl_percent")
    return (
        f"Symbol: {t.get('symbol')} ({t.get('stock_name', '')})\n"
        f"Direction: {t.get('type', 'BUY')}\n"
        f"Entry: ₹{t.get('entry_price')}  Exit: ₹{t.get('exit_price')}\n"
        f"Quantity: {t.get('quantity')}\n"
        f"Stop loss: ₹{t.get('stop_loss')}  Target: ₹{t.get('target1')}\n"
        f"Result: P&L ₹{pnl if pnl is not None else 'n/a'} "
        f"({pnl_pct if pnl_pct is not None else 'n/a'}%)\n"
        f"Setup: {t.get('setup_type') or 'unspecified'}\n"
        f"Status: {t.get('status', 'CLOSED')}\n"
        f"User notes: {t.get('notes') or 'none'}"
    )


def _ai_configured() -> bool:
    try:
        from services.claude_provider import is_configured as claude_configured
        from services.gemini_provider import is_configured as gemini_configured
        return claude_configured() or gemini_configured()
    except Exception:
        return False


async def _default_review(trade: dict) -> dict:
    """Generate the review via the Model Router (same task the manual
    ``/ai/trade-review`` endpoint runs)."""
    from services.model_router import get_model_router
    result = await get_model_router().run(
        "trade_review", summarize_trade_for_review(trade), max_tokens=600)
    return {"content": result["content"], "model_used": result["model_used"],
            "symbol": trade.get("symbol")}


async def generate_close_intelligence(
    db, trade_id: str,
    review_func: Optional[Callable[[dict], Awaitable[dict]]] = None,
) -> Optional[dict]:
    """Generate + persist the AI review for a just-closed trade, then announce
    it live. No-op when the trade is open, paper, already reviewed, or no AI
    provider is configured. Returns the review, or None when skipped/failed.

    ``review_func`` injects the model call for hermetic tests.
    """
    try:
        trade = await db.trades.find_one({"_id": _oid(trade_id)})
        if (not trade or trade.get("status") == "OPEN" or trade.get("is_paper")
                or trade.get("ai_review")):
            return None
        if review_func is None:
            if not _ai_configured():
                return None
            review_func = _default_review

        from services.activity_logger import log_activity
        # Private: names the user's own traded symbol (D6.1 / S4).
        log_activity(f"Reviewing closed {trade.get('symbol')} trade", "monitor", "running",
                     user_id=str(trade.get("user_id")))
        review = await review_func(trade)
        if not review or not review.get("content"):
            return None

        await db.trades.update_one({"_id": trade["_id"]}, {"$set": {"ai_review": review}})
        user_id = str(trade.get("user_id"))

        try:
            from services.market_engine.event_bus import event_bus
            await event_bus.publish("trade.review.ready", {
                "user_id": user_id,
                "trade_id": str(trade["_id"]),
                "symbol": trade.get("symbol"),
                "review": review,
            })
        except Exception as e:
            logger.warning(f"trade.review.ready publish failed: {e}")

        try:
            from services.notification_service import create_notification
            await create_notification(
                db, user_id,
                type="TRADE_REVIEW_READY",
                title=f"AI review ready: {trade.get('symbol')}",
                message=f"Your {trade.get('symbol')} trade has been reviewed — "
                        "open the Trade Monitor history to read the lessons.",
                severity="info",
                symbol=trade.get("symbol"),
                data={"trade_id": str(trade["_id"])},
            )
        except Exception as e:
            logger.warning(f"Trade review notification failed: {e}")

        log_activity(f"Trade review ready for {trade.get('symbol')}", "monitor", "done",
                     user_id=str(trade.get("user_id")))
        return review
    except Exception as e:
        logger.error(f"Trade review generation failed for {trade_id}: {e}")
        return None

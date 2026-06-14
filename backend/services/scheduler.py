"""Cron job scheduler for AlphaPartner trading platform."""
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")


async def morning_analysis_job(db, ai_func, market_func, pick_func):
    """8:30 AM weekdays: Scan stocks, generate picks, create morning report."""
    logger.info("Running morning analysis job...")
    try:
        from services.activity_logger import log_activity
        log_activity("Scanning NSE top gainers", "scan", "done")

        picks = pick_func(3)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await db.market_analysis.update_one(
            {"date": today},
            {"$set": {"top_picks": picks, "generated_at": datetime.now(timezone.utc).isoformat(), "type": "morning"}},
            upsert=True
        )

        report = await ai_func()
        await db.market_analysis.update_one(
            {"date": today},
            {"$set": {"morning_report": report, "overview_snapshot": market_func()}},
        )

        # Only notify users who have open trades or recently traded (personal relevance)
        users_with_activity = await db.trades.distinct("user_id")
        for uid in users_with_activity:
            user_prefs = await db.users.find_one({"_id": uid if not isinstance(uid, str) else uid}, {"notification_prefs": 1})
            prefs = (user_prefs or {}).get("notification_prefs", {})
            if prefs.get("trade_alerts", True):
                await db.notifications.insert_one({
                    "user_id": uid if isinstance(uid, str) else str(uid),
                    "type": "MORNING_REPORT",
                    "title": "Morning Analysis Ready",
                    "message": f"{len(picks)} AI picks generated. Top: {picks[0]['name']} ({picks[0]['confidence']}% confidence). Check AI Picks.",
                    "read": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })

                # Send email if user has email_alerts enabled
                if prefs.get("email_alerts", False):
                    try:
                        from services.email_service import send_notification as email_notify
                        user_email = (user_prefs or {}).get("email", "")
                        if user_email:
                            await email_notify(
                                "MORNING_REPORT", user_email,
                                content=f"{len(picks)} AI picks generated. Top pick: {picks[0]['name']} ({picks[0]['confidence']}% confidence)."
                            )
                    except Exception as e:
                        logger.error(f"Email notification failed for {uid}: {e}")

        logger.info(f"Morning analysis complete: {len(picks)} picks generated")
    except Exception as e:
        logger.error(f"Morning analysis job error: {e}")


async def market_scanner_job(db, ws_broadcast):
    """Every 5 min during market hours: Scan for breakouts."""
    logger.info("Running market scanner...")
    try:
        from market_data import get_market_overview, get_top_gainers
        overview = get_market_overview()
        gainers = get_top_gainers(3)

        # Broadcast market update via WebSocket
        if ws_broadcast:
            await ws_broadcast({
                "type": "market_update",
                "data": {"overview": overview, "hot_stocks": gainers},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        logger.info("Market scan complete")
    except Exception as e:
        logger.error(f"Market scanner error: {e}")


async def trade_monitor_job(db, ws_broadcast):
    """Every 60 sec during market hours: Monitor active trades with AI."""
    try:
        from market_data import get_stock_quote
        from services.portfolio_monitor import run_monitoring_cycle
        from services.whatsapp_service import send_whatsapp, is_configured as wa_configured

        wa_func = send_whatsapp if wa_configured() else None
        alert_count = await run_monitoring_cycle(db, get_stock_quote, wa_func)

        # Also broadcast updates via WebSocket
        active_trades = await db.trades.find({"status": "OPEN"}).to_list(100)
        for trade in active_trades:
            quote = get_stock_quote(trade["symbol"])
            if not quote:
                continue
            if ws_broadcast:
                await ws_broadcast({
                    "type": "trade_update",
                    "user_id": trade["user_id"],
                    "data": {
                        "trade_id": str(trade["_id"]),
                        "symbol": trade["symbol"],
                        "current_price": quote["price"],
                        "entry_price": trade["entry_price"],
                        "unrealized_pnl": round((quote["price"] - trade["entry_price"]) * trade["quantity"], 2),
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    except Exception as e:
        logger.error(f"Trade monitor error: {e}")


async def exit_reminder_job(db):
    """3:10 PM weekdays: Remind users who have open trades to close positions."""
    logger.info("Sending exit reminders...")
    try:
        active_trades = await db.trades.find({"status": "OPEN"}).to_list(100)
        user_trades = {}
        for t in active_trades:
            uid = t["user_id"]
            user_trades.setdefault(uid, []).append(t)

        for uid, trades in user_trades.items():
            # Only notify users with their own open trades
            symbols = ", ".join([t["symbol"] for t in trades[:3]])
            await db.notifications.insert_one({
                "user_id": uid,
                "type": "EXIT_REMINDER",
                "title": "Close Your Positions",
                "message": f"3:15 PM approaching! You have {len(trades)} open position(s): {symbols}. Close intraday trades now.",
                "read": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        logger.info(f"Exit reminders sent to {len(user_trades)} users with open trades")
    except Exception as e:
        logger.error(f"Exit reminder error: {e}")


async def eod_report_job(db):
    """4:00 PM weekdays: Generate end-of-day report."""
    logger.info("Generating EOD report...")
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        all_trades = await db.trades.find({}).to_list(1000)
        today_closed = [t for t in all_trades if t.get("exit_time", "").startswith(today)]

        total_pnl = sum(t.get("pnl", 0) for t in today_closed if t.get("pnl"))
        wins = len([t for t in today_closed if (t.get("pnl") or 0) > 0])
        losses = len([t for t in today_closed if (t.get("pnl") or 0) < 0])

        # Store EOD report
        await db.market_analysis.update_one(
            {"date": today},
            {"$set": {
                "eod_report": {
                    "total_pnl": round(total_pnl, 2),
                    "trades_closed": len(today_closed),
                    "wins": wins,
                    "losses": losses,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
            }},
            upsert=True
        )

        # Notify all users
        users = await db.users.find({}, {"_id": 1}).to_list(1000)
        for u in users:
            await db.notifications.insert_one({
                "user_id": str(u["_id"]),
                "type": "EOD_REPORT",
                "title": "End of Day Report",
                "message": f"Market closed. Today's P&L: INR {'+' if total_pnl >= 0 else ''}{total_pnl:.2f}. Trades: {wins}W / {losses}L.",
                "read": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

            # Send email EOD report if user has email_alerts
            user_full = await db.users.find_one({"_id": u["_id"]}, {"email": 1, "notification_prefs": 1})
            if user_full and (user_full.get("notification_prefs") or {}).get("email_alerts", False):
                try:
                    from services.email_service import send_notification as email_notify
                    await email_notify(
                        "EOD_REPORT", user_full.get("email", ""),
                        pnl=f"{total_pnl:.2f}", wins=wins, losses=losses
                    )
                except Exception as e:
                    logger.error(f"Email EOD report failed: {e}")

        logger.info(f"EOD report: PnL={total_pnl}, Closed={len(today_closed)}")
    except Exception as e:
        logger.error(f"EOD report error: {e}")


def setup_scheduler(db, ai_summary_func, market_overview_func, generate_picks_func, ws_broadcast=None):
    """Set up all cron jobs."""

    # Morning Analysis — 8:30 AM IST weekdays
    scheduler.add_job(
        morning_analysis_job,
        CronTrigger(hour=8, minute=30, day_of_week="mon-fri"),
        args=[db, ai_summary_func, market_overview_func, generate_picks_func],
        id="morning_analysis",
        replace_existing=True,
    )

    # Market Scanner — Every 5 min, 9:15 AM - 3:30 PM IST weekdays
    scheduler.add_job(
        market_scanner_job,
        CronTrigger(minute="*/5", hour="9-15", day_of_week="mon-fri"),
        args=[db, ws_broadcast],
        id="market_scanner",
        replace_existing=True,
    )

    # Trade Monitor — Every 60 sec during market hours
    scheduler.add_job(
        trade_monitor_job,
        CronTrigger(minute="*", hour="9-15", day_of_week="mon-fri"),
        args=[db, ws_broadcast],
        id="trade_monitor",
        replace_existing=True,
    )

    # Exit Reminder — 3:10 PM IST weekdays
    scheduler.add_job(
        exit_reminder_job,
        CronTrigger(hour=15, minute=10, day_of_week="mon-fri"),
        args=[db],
        id="exit_reminder",
        replace_existing=True,
    )

    # EOD Report — 4:00 PM IST weekdays
    scheduler.add_job(
        eod_report_job,
        CronTrigger(hour=16, minute=0, day_of_week="mon-fri"),
        args=[db],
        id="eod_report",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started with 5 cron jobs (IST timezone)")
    return scheduler

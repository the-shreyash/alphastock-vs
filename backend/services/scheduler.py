"""Cron job scheduler for AlphaPartner trading platform."""
import logging
import time
from datetime import datetime, timezone
from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
    EVENT_JOB_SUBMITTED,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from bson import ObjectId
from bson.errors import InvalidId

from analytics import periods, queries
from observability import instruments

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")


# --------------------------------------------------------------------------- #
# Observability (PH3.7)                                                         #
#                                                                               #
# WHY A SCHEDULER LISTENER AND NOT A DECORATOR ON EACH JOB                       #
#                                                                                #
# A decorator can only observe a job that RUNS. The failure mode unique to cron  #
# is a job that does not: APScheduler skips a run whose misfire grace period has #
# elapsed — because the event loop was blocked, or because the previous run of   #
# the same job is still going — and emits `EVENT_JOB_MISSED` instead of calling  #
# the function at all. `trade_monitor` fires every 60 seconds during market      #
# hours, so a missed run means live positions went unchecked, and nothing inside #
# the job body can ever report that. The scheduler's own event stream is the     #
# only place it exists.                                                          #
#                                                                                #
# Timing comes from pairing SUBMITTED with EXECUTED/ERROR. The pending map is    #
# bounded by the number of jobs currently in flight (six registered jobs, with   #
# `replace_existing=True` and no concurrency), and every terminal event pops its #
# entry — including the error path, which is where a naive implementation leaks. #
# --------------------------------------------------------------------------- #
_job_started_at: dict = {}


def _on_job_event(event) -> None:
    """Translate an APScheduler event into metrics. Never raises.

    APScheduler invokes listeners inline on its own thread; an exception here
    propagates into the scheduler's dispatch loop, so this is wrapped whole for
    the same reason every other instrument in this codebase is.
    """
    try:
        job_id = getattr(event, "job_id", None) or "unknown"
        code = getattr(event, "code", None)

        if code == EVENT_JOB_SUBMITTED:
            _job_started_at[job_id] = time.monotonic()
            return

        if code == EVENT_JOB_MISSED:
            # No submission happened, so there is nothing to pop and no duration
            # to report — the run did not occur.
            logger.warning(
                "Scheduled job missed its run window: %s", job_id,
                extra={"event": "scheduler_job_missed", "job": job_id},
            )
            instruments.record_scheduler_run(job_id, "missed")
            return

        started = _job_started_at.pop(job_id, None)
        duration = None if started is None else max(0.0, time.monotonic() - started)

        if code == EVENT_JOB_ERROR:
            logger.error(
                "Scheduled job raised: %s", job_id,
                exc_info=getattr(event, "exception", None),
                extra={"event": "scheduler_job_error", "job": job_id},
            )
            instruments.record_scheduler_run(job_id, "error", duration)
        elif code == EVENT_JOB_EXECUTED:
            instruments.record_scheduler_run(job_id, "executed", duration)
    except Exception:  # pragma: no cover - defensive; see docstring
        pass


async def morning_analysis_job(db, ai_func=None):
    """8:30 AM weekdays: generate the full morning report and notify subscribers.

    Sprint 10: the report itself — every section, the persistence, the
    ready-signal broadcast and the notification fan-out — is owned by
    services/morning_report.py, which the on-demand API route also calls. This
    job is now purely the schedule trigger, so a scheduled briefing and a
    user-requested one can never drift apart.

    The report streams a broadcast AIRun timeline (user_id=None → the `ai`
    channel reaches every connected dashboard), so users watch the morning
    pipeline run live instead of discovering the report after the fact.

    `ai_func` is accepted for backwards compatibility with existing callers and
    is no longer used; the briefing is generated from the centralized prompt
    library inside the report service.
    """
    logger.info("Running morning analysis job...")
    try:
        from services.morning_report import generate_and_notify

        report = await generate_and_notify(db)

        picks = report.get("top_picks") or []
        # Mirror the picks snapshot the AI Picks page reads.
        if picks:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            await db.market_analysis.update_one(
                {"date": today},
                {"$set": {
                    "top_picks": picks,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "type": "morning",
                }},
                upsert=True,
            )
        else:
            logger.warning("Morning analysis: live picks unavailable — skipping picks snapshot")

        logger.info(f"Morning analysis complete: {len(picks)} picks generated")
    except Exception as e:
        logger.error(f"Morning analysis job error: {e}")


async def market_scanner_job(db, ws_broadcast):
    """Every 5 min during market hours: Scan for breakouts using real data."""
    logger.info("Running market scanner...")
    try:
        from services.real_market import fetch_real_market_overview, fetch_real_gainers
        overview = await fetch_real_market_overview()
        gainers = await fetch_real_gainers(3)

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
    """Every 60 sec during market hours: Monitor active trades with real live prices."""
    try:
        from services.real_market import fetch_real_stock_quote
        from services.portfolio_monitor import run_monitoring_cycle
        from services.whatsapp_service import send_whatsapp, is_configured as wa_configured
        from services.activity_logger import log_platform_activity as log_activity

        # Use real stock quote function with async wrapper
        async def real_quote_func(symbol: str):
            return await fetch_real_stock_quote(symbol)

        # Sync wrapper for portfolio_monitor which expects sync function
        import asyncio
        quote_cache = {}

        async def prefetch_quotes(symbols):
            tasks = [fetch_real_stock_quote(s) for s in symbols]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for sym, res in zip(symbols, results):
                if not isinstance(res, Exception) and res:
                    quote_cache[sym] = res

        # Pre-fetch all quotes for open trades
        open_trades = await db.trades.find({"status": "OPEN"}).to_list(100)
        symbols_needed = list({t["symbol"] for t in open_trades})
        if symbols_needed:
            await prefetch_quotes(symbols_needed)
            log_activity(f"Live price check for {len(symbols_needed)} open positions", "monitor", "done")

        def sync_quote_func(symbol: str):
            return quote_cache.get(symbol)

        # Trading Engine pass (Sprint 9): trailing stops, multi-target partial
        # exits, SL detection + consented broker auto-exits. Runs BEFORE the
        # advisory portfolio monitor so alerts reflect post-engine state.
        stats = {}
        try:
            from services.trading_engine import run_cycle
            from services.broker_engine import broker_engine
            stats = await run_cycle(
                db, quote_cache, broker_engine=broker_engine,
                ws_push=broker_engine.ws_push)
            if stats.get("trailed") or stats.get("targets_hit") or stats.get("sl_exits"):
                log_activity(
                    f"Trading engine: {stats['trailed']} stop(s) trailed, "
                    f"{stats['targets_hit']} target(s) hit, {stats['sl_exits']} SL exit(s)",
                    "monitor", "done")
        except Exception as e:
            logger.error(f"Trading engine cycle error: {e}")

        wa_func = send_whatsapp if wa_configured() else None
        alert_count = await run_monitoring_cycle(db, sync_quote_func, wa_func)

        # Sprint R6: stream per-user `trade.updated` snapshots through the
        # event bus (bridge → per-user delivery on the `trades` channel).
        # Replaces the legacy `trade_update` BROADCAST, which sent every
        # user's open-trade P&L to every connected socket.
        try:
            from services import trade_stream
            await trade_stream.publish_all(db, quote_cache, reason="engine")
        except Exception as e:
            logger.error(f"Trade stream publish error: {e}")

        # Engine-closed trades → background AI trade review (the doc's flow:
        # Trade Closed → Journal Updated → AI Trade Review Starts).
        for closed in stats.get("closed_trades") or []:
            try:
                from services.trade_review import generate_close_intelligence
                asyncio.create_task(generate_close_intelligence(db, closed["trade_id"]))
            except Exception as e:
                logger.error(
                    f"Trade review scheduling failed for {closed.get('symbol')}: {e}")

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

        from services.notification_service import create_notification
        for uid, trades in user_trades.items():
            # Only notify users with their own open trades
            symbols = ", ".join([t["symbol"] for t in trades[:3]])
            await create_notification(
                db, uid,
                type="EXIT_REMINDER",
                title="Close Your Positions",
                message=f"3:15 PM approaching! You have {len(trades)} open position(s): {symbols}. Close intraday trades now.",
                severity="warning",
            )
        logger.info(f"Exit reminders sent to {len(user_trades)} users with open trades")
    except Exception as e:
        logger.error(f"Exit reminder error: {e}")


async def eod_report_job(db):
    """4:00 PM IST weekdays: end-of-day report, per user.

    REWRITTEN IN PH3.8. The previous implementation had two defects, and the
    second is the one that matters:

    1. **It crashed on every run.** `[t for t in all_trades if
       t.get("exit_time", "").startswith(today)]` ran over EVERY trade in the
       collection, and an open trade stores `exit_time: None` explicitly — so
       `.get(..., "")` returns `None`, not `""`, and `None.startswith` raises
       `AttributeError`. The outer `except` swallowed it as "EOD report error",
       so no report was ever written and no user was ever notified, for as long
       as any position was open. Reproduced with a single OPEN trade.

    2. **The P&L it reported was everybody's.** `total_pnl` summed the closed
       trades of the WHOLE PLATFORM, and that one figure was then sent to every
       user as "Today's P&L". A user with no trades was told the aggregate;
       every user was told a number derived from strangers' positions. Both a
       wrong personal number and a cross-tenant disclosure of trading
       performance.

    Now: each user's own closed trades, their own P&L, the IST session date, and
    real-money only (a virtual paper gain is not an end-of-day trading result).
    The platform aggregate is still computed — it is genuinely useful — but it
    is written to `market_analysis` for the admin surface and never sent to a
    user.
    """
    logger.info("Generating EOD report...")
    try:
        window = periods.resolve("today")
        session = periods.ist_date().isoformat()

        closed_today = await db.trades.find(
            queries.closed_in_window(window)).to_list(None)

        by_user = {}
        for trade in closed_today:
            by_user.setdefault(str(trade.get("user_id")), []).append(trade)

        def _tally(rows):
            pnls = [t.get("pnl") or 0 for t in rows]
            return (round(sum(pnls), 2),
                    len([p for p in pnls if p > 0]),
                    len([p for p in pnls if p < 0]))

        platform_pnl, platform_wins, platform_losses = _tally(closed_today)

        await db.market_analysis.update_one(
            {"date": session},
            {"$set": {
                "eod_report": {
                    "total_pnl": platform_pnl,
                    "trades_closed": len(closed_today),
                    "wins": platform_wins,
                    "losses": platform_losses,
                    "traders": len(by_user),
                    "scope": "platform",
                    "basis": "gross",
                    "session_date": session,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
            }},
            upsert=True
        )

        # Notify only the users who actually traded today, each with their own
        # figures. Users with no closed trades are no longer sent an EOD P&L —
        # there is nothing personal to report, and the platform's number is not
        # theirs to see.
        from services.notification_service import create_notification
        for user_id, rows in by_user.items():
            pnl, wins, losses = _tally(rows)
            try:
                await create_notification(
                    db, user_id,
                    type="EOD_REPORT",
                    title="End of Day Report",
                    message=(f"Market closed. Your P&L today: INR "
                             f"{'+' if pnl >= 0 else ''}{pnl:.2f} (gross). "
                             f"Trades: {wins}W / {losses}L."),
                )
            except Exception as e:
                logger.error(f"EOD notification failed for {user_id}: {e}")
                continue

            try:
                user_full = await db.users.find_one(
                    {"_id": ObjectId(user_id)}, {"email": 1, "notification_prefs": 1})
            except (InvalidId, TypeError):
                user_full = None
            if user_full and (user_full.get("notification_prefs") or {}).get("email_alerts", False):
                try:
                    from services.email_service import send_notification as email_notify
                    await email_notify(
                        "EOD_REPORT", user_full.get("email", ""),
                        pnl=f"{pnl:.2f}", wins=wins, losses=losses
                    )
                except Exception as e:
                    logger.error(f"Email EOD report failed: {e}")

        logger.info(f"EOD report: traders={len(by_user)}, closed={len(closed_today)}, "
                    f"platform PnL={platform_pnl}")
    except Exception as e:
        logger.error(f"EOD report error: {e}")


async def portfolio_snapshot_job(db):
    """4:05 PM IST weekdays: record each user's end-of-day portfolio equity
    snapshot (Sprint 8). The Performance equity curve is built forward from
    these real marks — never back-filled with synthetic history."""
    try:
        import asyncio
        from services import portfolio_engine
        from services.real_market import fetch_real_stock_quote

        async def quotes_map_func(symbols):
            uniq = list({(s or "").upper() for s in symbols if s})
            if not uniq:
                return {}
            results = await asyncio.gather(
                *[fetch_real_stock_quote(s) for s in uniq], return_exceptions=True)
            return {sym: (None if isinstance(r, Exception) else r)
                    for sym, r in zip(uniq, results)}

        broker_users = set(await db.holdings.distinct("user_id"))
        trade_users = set(await db.trades.distinct(
            "user_id", {"status": "OPEN", "is_paper": {"$ne": True}}))
        recorded = 0
        for uid in (broker_users | trade_users):
            snap = await portfolio_engine.record_snapshot(db, {"_id": uid}, quotes_map_func)
            if snap:
                recorded += 1
        logger.info(f"Portfolio snapshots recorded for {recorded} user(s)")
    except Exception as e:
        logger.error(f"Portfolio snapshot error: {e}")


def setup_scheduler(db, ai_summary_func, ws_broadcast=None):
    """Set up all cron jobs."""

    # Morning Analysis — 8:30 AM IST weekdays
    scheduler.add_job(
        morning_analysis_job,
        CronTrigger(hour=8, minute=30, day_of_week="mon-fri"),
        args=[db, ai_summary_func],
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

    # Portfolio Snapshot — 4:05 PM IST weekdays (after EOD marks settle)
    scheduler.add_job(
        portfolio_snapshot_job,
        CronTrigger(hour=16, minute=5, day_of_week="mon-fri"),
        args=[db],
        id="portfolio_snapshot",
        replace_existing=True,
    )

    # PH3.7. Attached before `start()` so the very first run is observed, and
    # registered here rather than at import so a module import cannot install a
    # duplicate listener (which would double every count).
    scheduler.add_listener(
        _on_job_event,
        EVENT_JOB_SUBMITTED | EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )

    scheduler.start()
    logger.info("Scheduler started with 6 cron jobs (IST timezone)")
    return scheduler

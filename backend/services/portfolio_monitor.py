"""AI-powered portfolio monitoring service."""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def analyze_portfolio_health(db, user_id, market_func, quote_func, ai_func=None):
    """Deep AI analysis of user's portfolio with proactive alerts."""
    trades = await db.trades.find({"user_id": user_id, "status": "OPEN"}).to_list(50)
    if not trades:
        return {"alerts": [], "summary": "No open positions to monitor.", "health_score": 100}

    alerts = []
    total_unrealized = 0
    at_risk_count = 0

    for trade in trades:
        quote = quote_func(trade["symbol"])
        if not quote:
            continue

        from services.activity_logger import log_activity
        log_activity(f"Monitoring open trade: {trade['symbol']}", "monitor", "running")

        current = quote["price"]
        entry = trade["entry_price"]
        sl = trade["stop_loss"]
        t1 = trade["target1"]
        qty = trade["quantity"]
        pnl = round((current - entry) * qty, 2)
        pnl_pct = round(((current - entry) / entry) * 100, 2)
        total_unrealized += pnl

        # Distance to SL and Target
        sl_distance_pct = round(((current - sl) / current) * 100, 2)
        t1_distance_pct = round(((t1 - current) / current) * 100, 2)

        # RSI check
        rsi = quote.get("rsi", 50)
        volume_ratio = quote.get("volume_ratio", 1.0)

        # Alert: Near stop loss (within 1.5%)
        if sl_distance_pct <= 1.5 and sl_distance_pct > 0:
            alerts.append({
                "type": "RISK_HIGH",
                "severity": "critical",
                "symbol": trade["symbol"],
                "message": f"{trade['symbol']} is {sl_distance_pct}% from stop loss. Current: INR {current}, SL: INR {sl}. Consider tightening SL or exiting.",
                "action": "Review immediately",
                "pnl": pnl,
            })
            at_risk_count += 1

        # Alert: SL breached
        elif current <= sl:
            alerts.append({
                "type": "STOP_LOSS_HIT",
                "severity": "critical",
                "symbol": trade["symbol"],
                "message": f"{trade['symbol']} has breached stop loss! Current: INR {current}, SL: INR {sl}. Exit to protect capital.",
                "action": "Exit now",
                "pnl": pnl,
            })
            at_risk_count += 1

        # Alert: Near target (within 2%)
        elif t1_distance_pct <= 2 and t1_distance_pct > 0:
            alerts.append({
                "type": "NEAR_TARGET",
                "severity": "positive",
                "symbol": trade["symbol"],
                "message": f"{trade['symbol']} is {t1_distance_pct}% from target! Current: INR {current}, Target: INR {t1}. Consider partial profit booking.",
                "action": "Book partial profits",
                "pnl": pnl,
            })

        # Alert: Target hit
        elif current >= t1:
            alerts.append({
                "type": "TARGET_HIT",
                "severity": "positive",
                "symbol": trade["symbol"],
                "message": f"{trade['symbol']} has hit target INR {t1}! Current: INR {current}. Unrealized profit: INR {pnl}.",
                "action": "Book profits",
                "pnl": pnl,
            })

        # Alert: RSI overbought
        if rsi > 75:
            alerts.append({
                "type": "RSI_OVERBOUGHT",
                "severity": "warning",
                "symbol": trade["symbol"],
                "message": f"{trade['symbol']} RSI at {rsi} (overbought). Momentum may reverse. Consider trailing stop loss.",
                "action": "Tighten SL",
                "pnl": pnl,
            })

        # Alert: Volume spike (could mean big move)
        if volume_ratio > 2.5:
            alerts.append({
                "type": "VOLUME_SPIKE",
                "severity": "info",
                "symbol": trade["symbol"],
                "message": f"{trade['symbol']} volume {volume_ratio}x above average. High activity — watch for breakout or breakdown.",
                "action": "Monitor closely",
                "pnl": pnl,
            })

        # Alert: Significant loss (> 3%)
        if pnl_pct < -3:
            alerts.append({
                "type": "SIGNIFICANT_LOSS",
                "severity": "warning",
                "symbol": trade["symbol"],
                "message": f"{trade['symbol']} down {abs(pnl_pct)}% (INR {abs(pnl)} loss). Review if thesis still holds.",
                "action": "Review position",
                "pnl": pnl,
            })

    # Health score (100 = perfect, 0 = all at risk)
    health_score = max(0, 100 - (at_risk_count * 25) - (len([a for a in alerts if a["severity"] == "warning"]) * 10))

    # Summary
    summary = f"Monitoring {len(trades)} open position(s). Unrealized P&L: INR {'+' if total_unrealized >= 0 else ''}{total_unrealized:.2f}."
    if at_risk_count:
        summary += f" {at_risk_count} position(s) at risk."
    if not alerts:
        summary += " All positions within safe parameters."

    return {
        "alerts": sorted(alerts, key=lambda a: {"critical": 0, "positive": 1, "warning": 2, "info": 3}.get(a["severity"], 4)),
        "summary": summary,
        "health_score": health_score,
        "total_unrealized_pnl": total_unrealized,
        "open_positions": len(trades),
        "at_risk": at_risk_count,
        "last_check": datetime.now(timezone.utc).isoformat(),
    }


async def run_monitoring_cycle(db, quote_func, whatsapp_func=None):
    """Run monitoring for all users with open trades and send alerts."""
    from bson import ObjectId
    users_with_trades = await db.trades.distinct("user_id", {"status": "OPEN"})
    total_alerts = 0

    for user_id in users_with_trades:
        user = await db.users.find_one({"_id": ObjectId(user_id) if isinstance(user_id, str) else user_id})
        if not user:
            continue

        result = await analyze_portfolio_health(db, user_id, None, quote_func)
        critical_alerts = [a for a in result["alerts"] if a["severity"] in ("critical", "positive")]

        prefs = user.get("notification_prefs", {})

        for alert in critical_alerts:
            # Save as notification
            await db.notifications.insert_one({
                "user_id": user_id,
                "type": alert["type"],
                "title": f"AI Alert: {alert['symbol']}",
                "message": alert["message"],
                "severity": alert["severity"],
                "read": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

            quote = quote_func(alert["symbol"])
            price_val = quote["price"] if quote else 0

            # 1. WhatsApp Alert
            if whatsapp_func and prefs.get("trade_alerts", True):
                try:
                    await whatsapp_func(alert["message"])
                except Exception as e:
                    logger.error(f"WhatsApp alert failed: {e}")

            # 2. Email Alert
            if (prefs.get("email_alerts", True) or prefs.get("email", True)) and user.get("email"):
                try:
                    from services.email_service import send_notification as send_email_notif
                    from services.email_service import is_configured as email_configured
                    if email_configured():
                        notif_type = "RISK_ALERT" if alert["severity"] == "critical" else "PROFIT_ALERT"
                        await send_email_notif(
                            notif_type,
                            user["email"],
                            symbol=alert["symbol"],
                            reason=alert["message"],
                            price=price_val,
                            action=alert.get("action", "Review position"),
                            profit=alert.get("pnl", 0)
                        )
                except Exception as e:
                    logger.error(f"Email alert failed: {e}")

            # 3. Telegram Alert
            if prefs.get("telegram_alerts", False) and user.get("telegram_chat_id"):
                try:
                    from services.telegram_service import send_notification as send_tg_notif
                    from services.telegram_service import is_configured as tg_configured
                    if tg_configured():
                        notif_type = "RISK_ALERT" if alert["severity"] == "critical" else "PROFIT_ALERT"
                        await send_tg_notif(
                            notif_type,
                            user["telegram_chat_id"],
                            symbol=alert["symbol"],
                            reason=alert["message"],
                            price=price_val,
                            action=alert.get("action", "Review position"),
                            profit=alert.get("pnl", 0)
                        )
                except Exception as e:
                    logger.error(f"Telegram alert failed: {e}")

            total_alerts += 1

    logger.info(f"Monitoring cycle: {len(users_with_trades)} users, {total_alerts} alerts generated")
    return total_alerts

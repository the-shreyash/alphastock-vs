"""Telegram Bot notification service with simulated fallback."""
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _get_config():
    return {
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "default_chat_id": os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
    }


def is_configured():
    config = _get_config()
    return bool(config["bot_token"])


def get_status():
    config = _get_config()
    configured = bool(config["bot_token"])
    return {
        "configured": configured,
        "mode": "live" if configured else "simulated",
        "has_token": configured,
        "has_chat_id": bool(config["default_chat_id"]),
        "message": "Telegram notifications active" if configured else "Add TELEGRAM_BOT_TOKEN to enable Telegram alerts",
    }


async def send_telegram(message: str, chat_id: str = None):
    """Send Telegram message via Bot API. Returns result dict."""
    from services.activity_logger import log_activity
    log_activity("Sending alert via Telegram/WhatsApp", "alert", "done")

    config = _get_config()

    if not config["bot_token"]:
        logger.info(f"[SIMULATED Telegram] {message[:100]}...")
        return {
            "success": True,
            "source": "simulated",
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    target = chat_id or config["default_chat_id"]
    if not target:
        return {"success": False, "source": "error", "message": "No chat ID configured"}

    try:
        import httpx
        url = f"https://api.telegram.org/bot{config['bot_token']}/sendMessage"
        
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": target,
                "text": message,
                "parse_mode": "HTML"
            })
            
            data = resp.json()
            if data.get("ok"):
                logger.info(f"Telegram sent successfully to {target}")
                return {
                    "success": True,
                    "source": "telegram",
                    "message_id": data["result"]["message_id"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            
            logger.error(f"Telegram API error: {data}")
            return {"success": False, "source": "telegram", "message": data.get("description", "Failed to send")}
            
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return {"success": False, "source": "error", "message": str(e)}


# Notification templates matching other channels
TEMPLATES = {
    "MORNING_REPORT": "🤖 <b>AlphaPartner Morning Report</b>\n\n{content}\n\nGood luck today!",
    "TRADE_ENTRY": "🤖 <b>Trade Executed</b>\n\n<b>{type}</b> {qty} {symbol} @ INR {price}\nSL: INR {sl} | Target: INR {target}\n\nAI monitoring started.",
    "PROFIT_ALERT": "🤖 <b>Profit Opportunity</b>\n\n{symbol} near target INR {target}\nCurrent: INR {price}\nUnrealized: INR {profit}\n\nConsider booking partial profit.",
    "RISK_ALERT": "🤖 <b>Risk Alert</b>\n\n{symbol}: {reason}\nCurrent: INR {price}\n\nReview your position immediately.",
    "STOP_LOSS": "🤖 <b>Stop Loss Hit</b>\n\n{symbol} hit SL at INR {sl}\nLoss: INR {loss}\n\nCapital protected.",
    "EXIT_REMINDER": "🤖 <b>Market Close Reminder</b>\n\n3:15 PM approaching!\n{count} position(s) still open. Close intraday trades.",
    "EOD_REPORT": "🤖 <b>End of Day Report</b>\n\nP&L: INR {pnl}\nTrades: {wins}W / {losses}L",
    "PORTFOLIO_ALERT": "🤖 <b>Portfolio Alert</b>\n\n{content}",
}


async def send_notification(notif_type: str, chat_id: str, **kwargs):
    """Send a templated Telegram notification."""
    template = TEMPLATES.get(notif_type, "{content}")
    try:
        message = template.format(**kwargs)
    except KeyError:
        message = f"<b>{notif_type}</b>\n\n{str(kwargs)}"

    return await send_telegram(message, chat_id=chat_id)

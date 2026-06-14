"""Twilio WhatsApp notification service with simulated fallback."""
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _get_credentials():
    return {
        "sid": os.environ.get("TWILIO_ACCOUNT_SID", "").strip(),
        "token": os.environ.get("TWILIO_AUTH_TOKEN", "").strip(),
        "from_number": os.environ.get("TWILIO_WHATSAPP_FROM", "").strip(),
        "to_number": os.environ.get("USER_WHATSAPP_TO", "").strip(),
    }


def is_configured():
    creds = _get_credentials()
    return all(creds.values())


def get_status():
    creds = _get_credentials()
    configured = all(creds.values())
    return {
        "configured": configured,
        "mode": "live" if configured else "simulated",
        "has_sid": bool(creds["sid"]),
        "has_token": bool(creds["token"]),
        "has_from": bool(creds["from_number"]),
        "has_to": bool(creds["to_number"]),
        "message": "WhatsApp notifications active" if configured else "Add Twilio credentials to enable WhatsApp alerts",
    }


async def send_whatsapp(message: str, to_number: str = None):
    """Send WhatsApp message via Twilio. Returns result dict."""
    from services.activity_logger import log_activity
    log_activity("Sending alert via Telegram/WhatsApp", "alert", "done")

    creds = _get_credentials()

    if not all([creds["sid"], creds["token"], creds["from_number"]]):
        logger.info(f"[SIMULATED WhatsApp] {message[:100]}...")
        return {
            "success": True,
            "source": "simulated",
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    target = to_number or creds["to_number"]
    if not target:
        return {"success": False, "source": "error", "message": "No recipient number configured"}

    try:
        if "testing" in creds["sid"].lower() or "test" in creds["token"].lower():
            class MockMessage:
                def __init__(self):
                    self.sid = "SMmocked12345678901234567890"
                    self.status = "queued"
            msg = MockMessage()
        else:
            from twilio.rest import Client
            client = Client(creds["sid"], creds["token"])

            from_whatsapp = creds["from_number"] if creds["from_number"].startswith("whatsapp:") else f"whatsapp:{creds['from_number']}"
            to_whatsapp = target if target.startswith("whatsapp:") else f"whatsapp:{target}"

            msg = client.messages.create(
                body=message,
                from_=from_whatsapp,
                to=to_whatsapp,
            )

        logger.info(f"WhatsApp sent: SID={msg.sid}")
        return {
            "success": True,
            "source": "twilio",
            "sid": msg.sid,
            "status": msg.status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"WhatsApp send error: {e}")
        return {"success": False, "source": "error", "message": str(e)}


# Pre-built notification templates
TEMPLATES = {
    "MORNING_REPORT": "AlphaPartner Morning Report\n\n{content}\n\nGood luck today!",
    "TRADE_ENTRY": "Trade Executed\n\n{type} {qty} {symbol} @ INR {price}\nSL: INR {sl} | Target: INR {target}\n\nAI monitoring started.",
    "PROFIT_ALERT": "Profit Opportunity\n\n{symbol} near target INR {target}\nCurrent: INR {price}\nUnrealized: INR {profit}\n\nConsider partial exit.",
    "RISK_ALERT": "Risk Alert\n\n{symbol}: {reason}\nCurrent: INR {price}\n\nReview your position.",
    "STOP_LOSS": "Stop Loss Hit\n\n{symbol} hit SL at INR {sl}\nLoss: INR {loss}\n\nCapital protected. Review trade journal.",
    "EXIT_REMINDER": "Market Close Reminder\n\n3:15 PM approaching!\n{count} position(s) still open.\n\nClose intraday trades now.",
    "EOD_REPORT": "End of Day Report\n\nToday's P&L: INR {pnl}\nTrades: {wins}W / {losses}L\n\nSee full report in app.",
    "PORTFOLIO_ALERT": "Portfolio Alert\n\n{content}\n\nCheck your portfolio for details.",
}


async def send_notification(notif_type: str, **kwargs):
    """Send a templated WhatsApp notification."""
    template = TEMPLATES.get(notif_type, "{content}")
    try:
        message = template.format(**kwargs)
    except KeyError:
        message = f"{notif_type}: {str(kwargs)}"

    return await send_whatsapp(f"AlphaPartner\n\n{message}")

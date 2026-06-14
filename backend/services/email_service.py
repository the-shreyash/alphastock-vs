"""Email notification service with SendGrid and SMTP fallback."""
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _get_sendgrid_key():
    return os.environ.get("SENDGRID_API_KEY", "").strip()


def _get_smtp_config():
    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", "").strip(),
        "from_email": os.environ.get("EMAIL_FROM", "alerts@alphapartner.ai").strip(),
        "from_name": os.environ.get("EMAIL_FROM_NAME", "AlphaPartner").strip(),
    }


def is_configured():
    """Check if any email provider is configured."""
    if _get_sendgrid_key():
        return True
    smtp = _get_smtp_config()
    return bool(smtp["host"] and smtp["user"] and smtp["password"])


def get_status():
    """Get email service configuration status."""
    has_sg = bool(_get_sendgrid_key())
    smtp = _get_smtp_config()
    has_smtp = bool(smtp["host"] and smtp["user"] and smtp["password"])
    configured = has_sg or has_smtp

    return {
        "configured": configured,
        "mode": "sendgrid" if has_sg else "smtp" if has_smtp else "simulated",
        "has_sendgrid": has_sg,
        "has_smtp": has_smtp,
        "from_email": smtp["from_email"],
        "message": "Email notifications active" if configured else "Add SendGrid API key or SMTP credentials to enable email alerts",
    }


# ─── Templates ───────────────────────────────────────────────────────

def _base_html(title: str, body: str) -> str:
    """Generate a styled HTML email wrapper matching AlphaPartner branding."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#09090B;font-family:'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;margin:0 auto;background:#18181B;border-radius:12px;overflow:hidden;margin-top:20px;margin-bottom:20px;">
    <!-- Header -->
    <tr>
      <td style="padding:24px 32px;border-bottom:1px solid #27272A;">
        <span style="font-size:18px;font-weight:600;color:#FAFAFA;letter-spacing:-0.02em;">AlphaPartner</span>
        <span style="font-size:11px;color:#818CF8;margin-left:8px;">AI Trading</span>
      </td>
    </tr>
    <!-- Title -->
    <tr>
      <td style="padding:24px 32px 8px;">
        <h1 style="margin:0;font-size:20px;font-weight:600;color:#FAFAFA;letter-spacing:-0.01em;">{title}</h1>
      </td>
    </tr>
    <!-- Body -->
    <tr>
      <td style="padding:8px 32px 32px;font-size:14px;line-height:1.7;color:#A1A1AA;">
        {body}
      </td>
    </tr>
    <!-- Footer -->
    <tr>
      <td style="padding:16px 32px;border-top:1px solid #27272A;font-size:11px;color:#52525B;">
        AlphaPartner AI · Powered by Claude Opus + Gemini Pro · This is not financial advice.
      </td>
    </tr>
  </table>
</body>
</html>"""


TEMPLATES = {
    "MORNING_REPORT": {
        "subject": "☀️ AlphaPartner Morning Report",
        "body": lambda **kw: _base_html("Morning Report", f"""
            <p>{kw.get('content', 'Your morning market analysis is ready.')}</p>
            <p style="margin-top:16px;">
                <a href="#" style="display:inline-block;padding:10px 20px;background:#6366F1;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;font-size:13px;">View Full Report</a>
            </p>
        """),
    },
    "TRADE_ENTRY": {
        "subject": "📈 Trade Executed: {symbol}",
        "body": lambda **kw: _base_html("Trade Executed", f"""
            <table width="100%" cellpadding="8" cellspacing="0" style="margin:12px 0;">
                <tr><td style="color:#52525B;font-size:12px;">Symbol</td><td style="color:#FAFAFA;font-weight:600;">{kw.get('symbol','')}</td></tr>
                <tr><td style="color:#52525B;font-size:12px;">Type</td><td style="color:#FAFAFA;">{kw.get('type','BUY')}</td></tr>
                <tr><td style="color:#52525B;font-size:12px;">Entry</td><td style="color:#FAFAFA;font-family:monospace;">INR {kw.get('price','')}</td></tr>
                <tr><td style="color:#52525B;font-size:12px;">Quantity</td><td style="color:#FAFAFA;">{kw.get('qty','')}</td></tr>
                <tr><td style="color:#52525B;font-size:12px;">Stop Loss</td><td style="color:#F43F5E;font-family:monospace;">INR {kw.get('sl','')}</td></tr>
                <tr><td style="color:#52525B;font-size:12px;">Target</td><td style="color:#10B981;font-family:monospace;">INR {kw.get('target','')}</td></tr>
            </table>
            <p style="font-size:12px;color:#818CF8;">AI monitoring started. You'll receive alerts on SL/target proximity.</p>
        """),
    },
    "RISK_ALERT": {
        "subject": "⚠️ Risk Alert: {symbol}",
        "body": lambda **kw: _base_html("Risk Alert", f"""
            <div style="padding:12px 16px;background:rgba(244,63,94,0.1);border-left:3px solid #F43F5E;border-radius:8px;margin:8px 0;">
                <p style="margin:0;color:#FAFAFA;font-weight:600;">{kw.get('symbol','')} — {kw.get('reason','Position at risk')}</p>
                <p style="margin:4px 0 0;color:#A1A1AA;font-size:13px;">Current: INR {kw.get('price','')} | Action: {kw.get('action','Review position')}</p>
            </div>
        """),
    },
    "PROFIT_ALERT": {
        "subject": "🎯 Profit Opportunity: {symbol}",
        "body": lambda **kw: _base_html("Profit Opportunity", f"""
            <div style="padding:12px 16px;background:rgba(16,185,129,0.1);border-left:3px solid #10B981;border-radius:8px;margin:8px 0;">
                <p style="margin:0;color:#FAFAFA;font-weight:600;">{kw.get('symbol','')} near target!</p>
                <p style="margin:4px 0 0;color:#A1A1AA;font-size:13px;">Current: INR {kw.get('price','')} | Unrealized: INR {kw.get('profit','')}</p>
            </div>
            <p>Consider booking partial profits.</p>
        """),
    },
    "EOD_REPORT": {
        "subject": "📊 End of Day Report — P&L: INR {pnl}",
        "body": lambda **kw: _base_html("End of Day Report", f"""
            <table width="100%" cellpadding="8" cellspacing="0" style="margin:12px 0;">
                <tr><td style="color:#52525B;font-size:12px;">Total P&amp;L</td>
                    <td style="color:{'#10B981' if float(kw.get('pnl',0)) >= 0 else '#F43F5E'};font-weight:700;font-family:monospace;font-size:18px;">
                        INR {'+'if float(kw.get('pnl',0))>=0 else ''}{kw.get('pnl',0)}</td></tr>
                <tr><td style="color:#52525B;font-size:12px;">Wins / Losses</td><td style="color:#FAFAFA;">{kw.get('wins',0)}W / {kw.get('losses',0)}L</td></tr>
            </table>
            <p>View your full trade journal in the app for detailed analysis.</p>
        """),
    },
    "EXIT_REMINDER": {
        "subject": "⏰ Market Close Reminder — {count} Open Position(s)",
        "body": lambda **kw: _base_html("Market Close Reminder", f"""
            <div style="padding:12px 16px;background:rgba(245,158,11,0.1);border-left:3px solid #F59E0B;border-radius:8px;margin:8px 0;">
                <p style="margin:0;color:#FAFAFA;font-weight:600;">3:15 PM approaching!</p>
                <p style="margin:4px 0 0;color:#A1A1AA;font-size:13px;">You have {kw.get('count',0)} open intraday position(s). Close them before market close.</p>
            </div>
        """),
    },
    "WEEKLY_REVIEW": {
        "subject": "📈 Your Weekly Trading Review",
        "body": lambda **kw: _base_html("Weekly Trading Review", f"""
            <p>{kw.get('content', 'Your AI weekly performance review is ready.')}</p>
            <p style="margin-top:16px;">
                <a href="#" style="display:inline-block;padding:10px 20px;background:#6366F1;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;font-size:13px;">View Full Review</a>
            </p>
        """),
    },
}


# ─── Send Functions ──────────────────────────────────────────────────

async def _send_via_sendgrid(to_email: str, subject: str, html_body: str):
    """Send email via SendGrid API."""
    import httpx

    key = _get_sendgrid_key()
    smtp = _get_smtp_config()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "personalizations": [{"to": [{"email": to_email}]}],
                    "from": {"email": smtp["from_email"], "name": smtp["from_name"]},
                    "subject": subject,
                    "content": [{"type": "text/html", "value": html_body}],
                },
            )

            if resp.status_code in (200, 201, 202):
                logger.info(f"Email sent via SendGrid to {to_email}: {subject}")
                return {"success": True, "source": "sendgrid", "timestamp": datetime.now(timezone.utc).isoformat()}
            else:
                logger.error(f"SendGrid error {resp.status_code}: {resp.text}")
                return {"success": False, "source": "sendgrid", "error": resp.text[:200]}
    except Exception as e:
        logger.error(f"SendGrid send error: {e}")
        return {"success": False, "source": "error", "error": str(e)}


async def _send_via_smtp(to_email: str, subject: str, html_body: str):
    """Send email via SMTP (blocking, run in executor)."""
    import asyncio
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_cfg = _get_smtp_config()

    def _send():
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{smtp_cfg['from_name']} <{smtp_cfg['from_email']}>"
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"]) as server:
            server.starttls()
            server.login(smtp_cfg["user"], smtp_cfg["password"])
            server.send_message(msg)

        return True

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send)
        logger.info(f"Email sent via SMTP to {to_email}: {subject}")
        return {"success": True, "source": "smtp", "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        logger.error(f"SMTP send error: {e}")
        return {"success": False, "source": "error", "error": str(e)}


async def send_email(to_email: str, subject: str, html_body: str):
    """Send email via best available provider. Simulates if none configured."""
    if _get_sendgrid_key():
        return await _send_via_sendgrid(to_email, subject, html_body)

    smtp = _get_smtp_config()
    if smtp["host"] and smtp["user"]:
        return await _send_via_smtp(to_email, subject, html_body)

    # Simulated mode
    logger.info(f"[SIMULATED Email] To: {to_email} | Subject: {subject}")
    return {
        "success": True,
        "source": "simulated",
        "to": to_email,
        "subject": subject,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def send_notification(notif_type: str, to_email: str, **kwargs):
    """Send a templated email notification."""
    template = TEMPLATES.get(notif_type)
    if not template:
        subject = f"AlphaPartner Alert: {notif_type}"
        body = _base_html(notif_type, f"<p>{str(kwargs)}</p>")
    else:
        try:
            subject = template["subject"].format(**kwargs)
        except KeyError:
            subject = template["subject"]
        try:
            body = template["body"](**kwargs)
        except Exception:
            body = _base_html(notif_type, f"<p>{str(kwargs)}</p>")

    return await send_email(to_email, subject, body)

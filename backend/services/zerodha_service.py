"""Zerodha Kite Connect service with mock fallback."""
import os
import logging
from urllib.parse import quote
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Kite access tokens are invalidated daily around 06:00 IST.
IST = timezone(timedelta(hours=5, minutes=30))


def _get_credentials():
    api_key = os.environ.get("KITE_API_KEY", "").strip()
    api_secret = os.environ.get("KITE_API_SECRET", "").strip()
    return api_key, api_secret


def is_configured():
    api_key, api_secret = _get_credentials()
    return bool(api_key and api_secret)


# In-memory cache of the active token; source of truth is db.broker_accounts.
_access_token = None
_connected_at = None
_profile = {}


def _session_is_fresh(connected_at_iso: str) -> bool:
    """A Kite token is valid until ~06:00 IST the following day."""
    try:
        connected = datetime.fromisoformat(connected_at_iso).astimezone(IST)
        now = datetime.now(IST)
        expiry = connected.replace(hour=6, minute=0, second=0, microsecond=0)
        if connected.hour >= 6:
            expiry += timedelta(days=1)
        return now < expiry
    except Exception:
        return False


def get_login_url(user_id: str = None):
    api_key, _ = _get_credentials()
    if not api_key:
        return {"url": None, "configured": False, "message": "Kite API key not configured. Add KITE_API_KEY to .env"}
    url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    if user_id:
        # Kite echoes redirect_params back onto the registered redirect URL,
        # letting the public callback identify which app user initiated login.
        url += f"&redirect_params={quote(f'uid={user_id}')}"
    return {"url": url, "configured": True}


async def generate_session(request_token: str, db=None, user_id: str = None):
    api_key, api_secret = _get_credentials()
    if not api_key or not api_secret:
        return {"success": False, "message": "Zerodha not configured"}

    try:
        # Real Kite Connect session generation
        import hashlib
        checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()

        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post("https://api.kite.trade/session/token", data={
                "api_key": api_key,
                "request_token": request_token,
                "checksum": checksum,
            })
            data = resp.json()
            if data.get("status") == "success":
                global _access_token, _connected_at, _profile
                _access_token = data["data"]["access_token"]
                _connected_at = datetime.now(timezone.utc).isoformat()
                _profile = {
                    "user_id": data["data"].get("user_id"),
                    "user_name": data["data"].get("user_name"),
                    "email": data["data"].get("email"),
                    "broker": data["data"].get("broker", "ZERODHA"),
                }
                if db is not None:
                    await db.broker_accounts.update_one(
                        {"user_id": user_id, "broker": "zerodha"},
                        {"$set": {
                            "user_id": user_id,
                            "broker": "zerodha",
                            "access_token": _access_token,
                            "public_token": data["data"].get("public_token"),
                            "profile": _profile,
                            "connected_at": _connected_at,
                        }},
                        upsert=True,
                    )
                return {"success": True, "access_token": _access_token, "user": data["data"]}
            return {"success": False, "message": data.get("message", "Session generation failed")}
    except Exception as e:
        logger.error(f"Zerodha session error: {e}")
        return {"success": False, "message": str(e)}


async def load_saved_session(db):
    """Hydrate the in-memory token from Mongo on startup; drop stale sessions."""
    global _access_token, _connected_at, _profile
    try:
        doc = await db.broker_accounts.find_one({"broker": "zerodha"}, sort=[("connected_at", -1)])
        if not doc or not doc.get("access_token"):
            return False
        if not _session_is_fresh(doc.get("connected_at", "")):
            logger.info("Saved Zerodha session has expired (daily 6 AM IST cutoff); reconnect required.")
            return False
        _access_token = doc["access_token"]
        _connected_at = doc.get("connected_at")
        _profile = doc.get("profile", {})
        logger.info("Restored Zerodha session from database.")
        return True
    except Exception as e:
        logger.error(f"Failed to load saved Zerodha session: {e}")
        return False


async def get_holdings():
    if not _access_token:
        return {"source": "zerodha", "holdings": [], "error": "Zerodha not connected"}

    try:
        api_key, _ = _get_credentials()
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.kite.trade/portfolio/holdings", headers={
                "X-Kite-Version": "3",
                "Authorization": f"token {api_key}:{_access_token}",
            })
            data = resp.json()
            if data.get("status") == "success":
                return {"source": "zerodha", "holdings": data["data"]}
            return {"source": "zerodha", "holdings": [], "error": data.get("message")}
    except Exception as e:
        logger.error(f"Zerodha holdings error: {e}")
        return {"source": "zerodha", "holdings": [], "error": str(e)}


async def get_positions():
    if not _access_token:
        return {"source": "zerodha", "net": [], "day": [], "error": "Zerodha not connected"}

    try:
        api_key, _ = _get_credentials()
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.kite.trade/portfolio/positions", headers={
                "X-Kite-Version": "3",
                "Authorization": f"token {api_key}:{_access_token}",
            })
            data = resp.json()
            if data.get("status") == "success":
                return {"source": "zerodha", **data["data"]}
            return {"source": "zerodha", "net": [], "day": [], "error": data.get("message")}
    except Exception as e:
        logger.error(f"Zerodha positions error: {e}")
        return {"source": "zerodha", "net": [], "day": [], "error": str(e)}


async def place_order(symbol: str, transaction_type: str, quantity: int, price: float, order_type: str = "LIMIT"):
    if not _access_token:
        return {
            "source": "zerodha",
            "order_id": None,
            "status": "FAILED",
            "message": "Zerodha not connected. Please connect your Zerodha account in Settings.",
        }

    try:
        api_key, _ = _get_credentials()
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post("https://api.kite.trade/orders/regular", headers={
                "X-Kite-Version": "3",
                "Authorization": f"token {api_key}:{_access_token}",
            }, data={
                "exchange": "NSE",
                "tradingsymbol": symbol,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "product": "MIS",
                "order_type": order_type,
                "price": price if order_type == "LIMIT" else 0,
                "tag": "AlphaPartner",
            })
            data = resp.json()
            if data.get("status") == "success":
                return {"source": "zerodha", "order_id": data["data"]["order_id"], "status": "PLACED"}
            return {"source": "zerodha", "order_id": None, "status": "FAILED", "message": data.get("message")}
    except Exception as e:
        logger.error(f"Zerodha order error: {e}")
        return {"source": "zerodha", "order_id": None, "status": "ERROR", "message": str(e)}


async def cancel_order(order_id: str):
    if not _access_token or order_id.startswith("MOCK_"):
        return {"source": "zerodha", "status": "FAILED", "message": "Zerodha not connected"}

    try:
        api_key, _ = _get_credentials()
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(f"https://api.kite.trade/orders/regular/{order_id}", headers={
                "X-Kite-Version": "3",
                "Authorization": f"token {api_key}:{_access_token}",
            })
            data = resp.json()
            return {"source": "zerodha", "status": data.get("status"), "message": data.get("message")}
    except Exception as e:
        return {"source": "zerodha", "status": "ERROR", "message": str(e)}


def get_status():
    """Get Zerodha connection status."""
    configured = is_configured()
    fresh = _access_token is not None and (_connected_at is None or _session_is_fresh(_connected_at))
    expired = _access_token is not None and not fresh
    if fresh:
        message = f"Connected to Zerodha ({_profile.get('user_id', '')})".strip()
    elif expired:
        message = "Zerodha session expired (Kite tokens reset daily at 6 AM IST). Please login again."
    elif configured:
        message = "API keys configured. Login required."
    else:
        message = "Add KITE_API_KEY and KITE_API_SECRET to enable live trading."
    return {
        "configured": configured,
        "connected": fresh,
        "session_expired": expired,
        "profile": _profile if fresh else {},
        "connected_at": _connected_at if fresh else None,
        "mode": "live" if fresh else ("ready" if configured else "disconnected"),
        "message": message,
    }


async def get_funds():
    """Get account funds/balance."""
    if not _access_token:
        return {
            "source": "zerodha",
            "equity": {
                "available_margin": 0.0,
                "used_margin": 0.0,
                "opening_balance": 0.0,
                "payin": 0,
                "payout": 0,
                "collateral": 0,
            },
            "commodity": {"available_margin": 0, "used_margin": 0},
            "error": "Zerodha not connected"
        }
    try:
        api_key, _ = _get_credentials()
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.kite.trade/user/margins", headers={
                "X-Kite-Version": "3",
                "Authorization": f"token {api_key}:{_access_token}",
            })
            data = resp.json()
            if data.get("status") == "success":
                return {"source": "zerodha", **data["data"]}
    except Exception as e:
        logger.error(f"Zerodha funds error: {e}")
    return {"source": "zerodha", "equity": {"available_margin": 0}, "commodity": {"available_margin": 0}, "error": "Failed to fetch funds"}


async def get_profile():
    """Get user profile from Zerodha."""
    if not _access_token:
        return {
            "source": "zerodha",
            "user_name": "Not Connected",
            "user_id": "",
            "email": "",
            "broker": "ZERODHA",
            "exchanges": ["NSE", "BSE", "NFO"],
            "error": "Zerodha not connected"
        }
    try:
        api_key, _ = _get_credentials()
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.kite.trade/user/profile", headers={
                "X-Kite-Version": "3",
                "Authorization": f"token {api_key}:{_access_token}",
            })
            data = resp.json()
            if data.get("status") == "success":
                return {"source": "zerodha", **data["data"]}
    except Exception as e:
        logger.error(f"Zerodha profile error: {e}")
    return {"source": "zerodha", "user_name": "Not Connected", "user_id": "", "error": "Failed to fetch profile"}


async def get_orders():
    """Get today's orders."""
    if not _access_token:
        return {
            "source": "zerodha",
            "orders": [],
            "error": "Zerodha not connected",
        }
    try:
        api_key, _ = _get_credentials()
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.kite.trade/orders", headers={
                "X-Kite-Version": "3",
                "Authorization": f"token {api_key}:{_access_token}",
            })
            data = resp.json()
            if data.get("status") == "success":
                return {"source": "zerodha", "orders": data["data"]}
    except Exception as e:
        logger.error(f"Zerodha orders error: {e}")
    return {"source": "zerodha", "orders": [], "error": "Failed to fetch orders"}

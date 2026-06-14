"""Zerodha Kite Connect service with mock fallback."""
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _get_credentials():
    api_key = os.environ.get("KITE_API_KEY", "").strip()
    api_secret = os.environ.get("KITE_API_SECRET", "").strip()
    return api_key, api_secret


def is_configured():
    api_key, api_secret = _get_credentials()
    return bool(api_key and api_secret)


# In-memory token store (in production, store in DB)
_access_token = None


def get_login_url():
    api_key, _ = _get_credentials()
    if not api_key:
        return {"url": None, "configured": False, "message": "Kite API key not configured. Add KITE_API_KEY to .env"}
    return {"url": f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}", "configured": True}


async def generate_session(request_token: str, db=None):
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
                global _access_token
                _access_token = data["data"]["access_token"]
                return {"success": True, "access_token": _access_token, "user": data["data"]}
            return {"success": False, "message": data.get("message", "Session generation failed")}
    except Exception as e:
        logger.error(f"Zerodha session error: {e}")
        return {"success": False, "message": str(e)}


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
    connected = _access_token is not None
    return {
        "configured": configured,
        "connected": connected,
        "mode": "live" if connected else ("ready" if configured else "disconnected"),
        "message": "Connected to Zerodha" if connected else ("API keys configured. Login required." if configured else "Add KITE_API_KEY and KITE_API_SECRET to enable live trading."),
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

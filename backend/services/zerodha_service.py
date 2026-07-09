"""DEPRECATED compatibility shim over the unified Broker Engine.

Sprint 7 moved all broker logic into services/broker_engine.py +
services/brokers/ (adapter pattern, per-user encrypted sessions, Upstox
support, realtime streams). This module keeps the old single-session
Zerodha function signatures alive for legacy callers and tests.

New code must use `services.broker_engine.broker_engine` directly.

Legacy behavior preserved: these functions operate on "the most recently
connected Zerodha account" because the old implementation kept one global
in-memory token.
"""
import logging

from services.broker_engine import broker_engine
from services.brokers.base import BrokerAuthError, BrokerError

logger = logging.getLogger(__name__)

BROKER = "zerodha"


def is_configured():
    return broker_engine.adapter(BROKER).is_configured()


def get_login_url(user_id: str = None):
    return broker_engine.get_login_url(BROKER, user_id)


async def generate_session(request_token: str, db=None, user_id: str = None):
    """Exchange a Kite request_token. `db` is ignored (engine owns storage)."""
    try:
        result = await broker_engine.complete_auth(BROKER, user_id, {"request_token": request_token})
        # NOTE: unlike the pre-Sprint-7 version, the access token is never
        # returned to callers (it must not reach the browser).
        return {"success": True, "user": result.get("profile", {})}
    except (BrokerError, Exception) as e:
        message = getattr(e, "user_message", str(e))
        logger.error(f"Zerodha session error: {message}")
        return {"success": False, "message": message}


async def load_saved_session(db=None):
    restored = await broker_engine.load_sessions()
    return restored > 0


async def _legacy_session():
    found = await broker_engine.any_connected_session(BROKER)
    return found if found else (None, None)


def get_status():
    """Sync status snapshot (legacy shape) built from the engine cache."""
    adapter = broker_engine.adapter(BROKER)
    configured = adapter.is_configured()
    fresh = None
    for (uid, broker), session in broker_engine._sessions.items():
        if broker == BROKER and adapter.session_is_fresh(session):
            fresh = session
            break
    if fresh:
        profile = fresh.get("profile") or {}
        return {
            "configured": configured, "connected": True, "session_expired": False,
            "profile": profile, "connected_at": fresh.get("connected_at"),
            "mode": "live",
            "message": f"Connected to Zerodha ({profile.get('user_id', '')})".strip(),
        }
    return {
        "configured": configured, "connected": False, "session_expired": False,
        "profile": {}, "connected_at": None,
        "mode": "ready" if configured else "disconnected",
        "message": ("API keys configured. Login required." if configured
                    else "Add KITE_API_KEY and KITE_API_SECRET to enable live trading."),
    }


async def get_holdings():
    user_id, session = await _legacy_session()
    if not session:
        return {"source": BROKER, "holdings": [], "error": "Zerodha not connected"}
    try:
        return {"source": BROKER, "holdings": await broker_engine.adapter(BROKER).get_holdings(session)}
    except BrokerError as e:
        return {"source": BROKER, "holdings": [], "error": e.user_message}


async def get_positions():
    user_id, session = await _legacy_session()
    if not session:
        return {"source": BROKER, "net": [], "day": [], "error": "Zerodha not connected"}
    try:
        positions = await broker_engine.adapter(BROKER).get_positions(session)
        return {"source": BROKER, "net": positions, "day": []}
    except BrokerError as e:
        return {"source": BROKER, "net": [], "day": [], "error": e.user_message}


async def place_order(symbol: str, transaction_type: str, quantity: int, price: float, order_type: str = "LIMIT"):
    user_id, session = await _legacy_session()
    if not session:
        return {"source": BROKER, "order_id": None, "status": "FAILED",
                "message": "Zerodha not connected. Please connect your Zerodha account in Settings."}
    try:
        result = await broker_engine.place_order(user_id, BROKER, {
            "symbol": symbol, "transaction_type": transaction_type,
            "quantity": quantity, "price": price, "order_type": order_type,
            "exchange": "NSE", "product": "MIS",
        })
        return {"source": BROKER, "order_id": result.get("order_id"), "status": "PLACED"}
    except BrokerError as e:
        return {"source": BROKER, "order_id": None, "status": "FAILED", "message": e.user_message}


async def cancel_order(order_id: str):
    user_id, session = await _legacy_session()
    if not session or (order_id or "").startswith("MOCK_"):
        return {"source": BROKER, "status": "FAILED", "message": "Zerodha not connected"}
    try:
        result = await broker_engine.cancel_order(user_id, BROKER, order_id)
        return {"source": BROKER, "status": "success", "order_id": result.get("order_id")}
    except BrokerError as e:
        return {"source": BROKER, "status": "ERROR", "message": e.user_message}


async def get_funds():
    user_id, session = await _legacy_session()
    if not session:
        return {"source": BROKER, "equity": {"available_margin": 0.0, "used_margin": 0.0},
                "commodity": {"available_margin": 0, "used_margin": 0},
                "error": "Zerodha not connected"}
    try:
        funds = await broker_engine.adapter(BROKER).get_funds(session)
        raw = funds.pop("raw", {})
        return {"source": BROKER, **funds, **raw}
    except BrokerError as e:
        return {"source": BROKER, "equity": {"available_margin": 0},
                "commodity": {"available_margin": 0}, "error": e.user_message}


async def get_profile():
    user_id, session = await _legacy_session()
    if not session:
        return {"source": BROKER, "user_name": "Not Connected", "user_id": "", "email": "",
                "broker": "ZERODHA", "exchanges": ["NSE", "BSE", "NFO"],
                "error": "Zerodha not connected"}
    try:
        profile = await broker_engine.adapter(BROKER).get_profile(session)
        return {"source": BROKER, **profile, "user_id": profile.get("account_id", "")}
    except BrokerError as e:
        return {"source": BROKER, "user_name": "Not Connected", "user_id": "", "error": e.user_message}


async def get_orders():
    user_id, session = await _legacy_session()
    if not session:
        return {"source": BROKER, "orders": [], "error": "Zerodha not connected"}
    try:
        return {"source": BROKER, "orders": await broker_engine.adapter(BROKER).get_orders(session)}
    except BrokerError as e:
        return {"source": BROKER, "orders": [], "error": e.user_message}

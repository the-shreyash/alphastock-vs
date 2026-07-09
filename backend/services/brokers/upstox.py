"""Upstox API v2 adapter.

Official API docs: https://upstox.com/developer/api-documentation
Auth model: OAuth2 authorization-code. The `state` parameter carries our app
user id so the public callback can map the session to the right user.
Access tokens expire daily at 03:30 IST; Upstox v2 has no refresh grant for
standard apps, so refresh_session() reports "reconnect required".

Note on instruments: Upstox order APIs address instruments by instrument key
("NSE_EQ|<ISIN>"), not by trading symbol. The adapter resolves symbols to
instrument keys from the user's own holdings/positions; callers may also pass
`instrument_token` explicitly.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from services.brokers.base import (
    IST, BrokerAdapter, BrokerAuthError, BrokerError, normalize_status,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.upstox.com/v2"
AUTH_URL = f"{BASE_URL}/login/authorization/dialog"


class UpstoxAdapter(BrokerAdapter):
    name = "upstox"
    display_name = "Upstox"

    def _credentials(self):
        return (
            os.environ.get("UPSTOX_API_KEY", "").strip(),
            os.environ.get("UPSTOX_API_SECRET", "").strip(),
            os.environ.get("UPSTOX_REDIRECT_URL", "").strip(),
        )

    def is_configured(self) -> bool:
        api_key, api_secret, redirect = self._credentials()
        return bool(api_key and api_secret and redirect)

    def _headers(self, session: dict) -> dict:
        token = (session or {}).get("access_token")
        if not token:
            raise BrokerAuthError("Upstox is not connected. Connect your account in Settings.")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def _upstox(self, method: str, path: str, session: dict = None,
                      data: dict = None, json_body: dict = None) -> dict:
        payload = await self._request(method, f"{BASE_URL}{path}",
                                      headers=self._headers(session) if session is not None else
                                      {"Accept": "application/json"},
                                      data=data, json_body=json_body)
        if payload.get("status") == "error" or "errors" in payload and payload.get("status") != "success":
            errors = payload.get("errors") or []
            first = errors[0] if errors else {}
            code = first.get("errorCode") or first.get("error_code") or ""
            message = first.get("message", "Upstox request failed")
            if str(code).upper() in ("UDAPI100050", "UDAPI100067") or "token" in message.lower():
                raise BrokerAuthError("Upstox session expired (tokens reset daily at 3:30 AM IST). Please reconnect.")
            raise BrokerError(f"Upstox error [{code}]: {message}", user_message=message)
        return payload.get("data") if payload.get("data") is not None else payload

    # -- authentication ----------------------------------------------------
    def get_login_url(self, user_id: str = None) -> dict:
        api_key, _, redirect = self._credentials()
        if not (api_key and redirect):
            return {"url": None, "configured": False,
                    "message": "Upstox not configured. Add UPSTOX_API_KEY, UPSTOX_API_SECRET and UPSTOX_REDIRECT_URL to .env"}
        params = {"response_type": "code", "client_id": api_key, "redirect_uri": redirect}
        if user_id:
            params["state"] = f"uid={user_id}"  # echoed back on the callback
        return {"url": f"{AUTH_URL}?{urlencode(params)}", "configured": True}

    async def exchange_token(self, auth_payload: dict) -> dict:
        api_key, api_secret, redirect = self._credentials()
        code = (auth_payload or {}).get("code")
        if not (api_key and api_secret and redirect):
            raise BrokerError("Upstox not configured")
        if not code:
            raise BrokerError("authorization code required")
        data = await self._upstox("POST", "/login/authorization/token", session=None, data={
            "code": code,
            "client_id": api_key,
            "client_secret": api_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        })
        now = datetime.now(timezone.utc)
        return {
            "access_token": data.get("access_token", ""),
            "refresh_token": "",  # not issued by Upstox v2 standard apps
            "expires_at": self.session_expiry(now).isoformat(),
            "account_id": data.get("user_id", ""),
            "profile": {
                "user_id": data.get("user_id"),
                "user_name": data.get("user_name"),
                "email": data.get("email"),
                "broker": data.get("broker", "UPSTOX"),
                "exchanges": data.get("exchanges", []),
            },
        }

    def session_expiry(self, connected_at: datetime) -> datetime:
        """Upstox tokens expire at 03:30 IST every day."""
        local = connected_at.astimezone(IST)
        expiry = local.replace(hour=3, minute=30, second=0, microsecond=0)
        if local >= expiry:
            expiry += timedelta(days=1)
        return expiry.astimezone(timezone.utc)

    async def invalidate_session(self, session: dict) -> None:
        try:
            await self._upstox("DELETE", "/logout", session)
        except Exception as e:
            logger.warning(f"Upstox logout failed (token may already be dead): {e}")

    # -- account data --------------------------------------------------------
    async def get_profile(self, session: dict) -> dict:
        data = await self._upstox("GET", "/user/profile", session)
        return {
            "account_id": data.get("user_id"),
            "user_name": data.get("user_name"),
            "email": data.get("email"),
            "broker": data.get("broker", "UPSTOX"),
            "exchanges": data.get("exchanges", []),
            "products": data.get("products", []),
        }

    async def get_holdings(self, session: dict) -> list:
        rows = await self._upstox("GET", "/portfolio/long-term-holdings", session)
        holdings = []
        for h in rows or []:
            qty = (h.get("quantity") or 0) + (h.get("t1_quantity") or 0)
            avg = h.get("average_price") or 0
            ltp = h.get("last_price") or 0
            invested = qty * avg
            value = qty * ltp
            holdings.append({
                "symbol": h.get("trading_symbol") or h.get("tradingsymbol"),
                "exchange": h.get("exchange"),
                "quantity": qty,
                "average_price": avg,
                "last_price": ltp,
                "market_value": round(value, 2),
                "invested_value": round(invested, 2),
                "pnl": round(h.get("pnl") if h.get("pnl") is not None else value - invested, 2),
                "pnl_percent": round(((value - invested) / invested * 100) if invested else 0, 2),
                "product": h.get("product"),
                "isin": h.get("isin"),
                "instrument_token": h.get("instrument_token"),
                "company_name": h.get("company_name"),
            })
        return holdings

    async def get_positions(self, session: dict) -> list:
        rows = await self._upstox("GET", "/portfolio/short-term-positions", session)
        positions = []
        for p in rows or []:
            qty = p.get("quantity") or 0
            positions.append({
                "symbol": p.get("trading_symbol") or p.get("tradingsymbol"),
                "exchange": p.get("exchange"),
                "product": p.get("product"),
                "quantity": qty,
                "average_price": p.get("average_price") or 0,
                "last_price": p.get("last_price") or 0,
                "pnl": round(p.get("pnl") or 0, 2),
                "realised": round(p.get("realised") or 0, 2),
                "unrealised": round(p.get("unrealised") or 0, 2),
                "buy_quantity": p.get("day_buy_quantity") or 0,
                "sell_quantity": p.get("day_sell_quantity") or 0,
                "side": "LONG" if qty > 0 else ("SHORT" if qty < 0 else "FLAT"),
                "instrument_token": p.get("instrument_token"),
            })
        return positions

    async def get_funds(self, session: dict) -> dict:
        data = await self._upstox("GET", "/user/get-funds-and-margin", session)
        equity = data.get("equity") or {}
        return {
            "available_margin": round(equity.get("available_margin", 0) or 0, 2),
            "used_margin": round(equity.get("used_margin", 0) or 0, 2),
            "opening_balance": round(equity.get("notional_cash", 0) or 0, 2),
            "payin": round(equity.get("payin_amount", 0) or 0, 2),
            "payout": 0.0,
            "collateral": round(equity.get("span_margin", 0) or 0, 2),
            "total_balance": round((equity.get("available_margin", 0) or 0) + (equity.get("used_margin", 0) or 0), 2),
            "raw": {"equity": equity, "commodity": data.get("commodity") or {}},
        }

    async def get_orders(self, session: dict) -> list:
        rows = await self._upstox("GET", "/order/retrieve-all", session)
        return [self._normalize_order(o) for o in rows or []]

    async def get_trades(self, session: dict) -> list:
        rows = await self._upstox("GET", "/order/trades/get-trades-for-day", session)
        return [{
            "trade_id": t.get("trade_id"),
            "order_id": t.get("order_id"),
            "symbol": t.get("trading_symbol") or t.get("tradingsymbol"),
            "exchange": t.get("exchange"),
            "transaction_type": t.get("transaction_type"),
            "quantity": t.get("quantity") or 0,
            "price": t.get("average_price") or 0,
            "product": t.get("product"),
            "executed_at": t.get("exchange_timestamp") or t.get("order_timestamp"),
        } for t in rows or []]

    @staticmethod
    def _normalize_order(o: dict) -> dict:
        return {
            "order_id": o.get("order_id"),
            "symbol": o.get("trading_symbol") or o.get("tradingsymbol"),
            "exchange": o.get("exchange"),
            "transaction_type": o.get("transaction_type"),
            "order_type": o.get("order_type"),
            "product": o.get("product"),
            "quantity": o.get("quantity") or 0,
            "filled_quantity": o.get("filled_quantity") or 0,
            "pending_quantity": o.get("pending_quantity") or 0,
            "price": o.get("price") or 0,
            "trigger_price": o.get("trigger_price") or 0,
            "average_price": o.get("average_price") or 0,
            "status": normalize_status(o.get("status")),
            "status_message": o.get("status_message"),
            "placed_at": str(o.get("order_timestamp") or ""),
            "updated_at": str(o.get("exchange_timestamp") or ""),
            "tag": o.get("tag"),
            "broker": "upstox",
        }

    # -- order management ------------------------------------------------------
    async def _resolve_instrument_token(self, session: dict, symbol: str) -> str:
        """Find the Upstox instrument key for a symbol from the user's own
        holdings/positions. Raises a clear error when unresolvable."""
        symbol_upper = (symbol or "").upper()
        for source in (self.get_positions, self.get_holdings):
            try:
                for row in await source(session):
                    if (row.get("symbol") or "").upper() == symbol_upper and row.get("instrument_token"):
                        return row["instrument_token"]
            except BrokerError:
                continue
        raise BrokerError(
            f"Could not resolve Upstox instrument key for {symbol}",
            user_message=f"Could not resolve the Upstox instrument for {symbol}. "
                         "It must exist in your holdings/positions, or pass instrument_token explicitly.",
        )

    async def place_order(self, session: dict, order: dict) -> dict:
        instrument = order.get("instrument_token") or await self._resolve_instrument_token(session, order.get("symbol"))
        order_type = order.get("order_type", "MARKET")
        payload = {
            "instrument_token": instrument,
            "quantity": int(order["quantity"]),
            "product": order.get("product", "D"),  # D=Delivery, I=Intraday
            "validity": order.get("validity", "DAY"),
            "price": float(order.get("price") or 0),
            "order_type": order_type,
            "transaction_type": order.get("transaction_type", "BUY"),
            "disclosed_quantity": int(order.get("disclosed_quantity") or 0),
            "trigger_price": float(order.get("trigger_price") or 0),
            "is_amo": bool(order.get("is_amo", False)),
            "tag": (order.get("tag") or "StockAssistAI")[:20],
        }
        data = await self._upstox("POST", "/order/place", session, json_body=payload)
        return {"order_id": data.get("order_id"), "status": "PENDING", "broker": "upstox"}

    async def modify_order(self, session: dict, order_id: str, changes: dict) -> dict:
        payload = {"order_id": order_id, "validity": changes.get("validity", "DAY")}
        for key in ("quantity", "price", "trigger_price", "order_type", "disclosed_quantity"):
            if changes.get(key) is not None:
                payload[key] = changes[key]
        if len(payload) <= 2:
            raise BrokerError("No changes supplied", user_message="Nothing to modify in this order.")
        data = await self._upstox("PUT", "/order/modify", session, json_body=payload)
        return {"order_id": data.get("order_id", order_id), "status": "PENDING", "broker": "upstox"}

    async def cancel_order(self, session: dict, order_id: str) -> dict:
        data = await self._upstox("DELETE", f"/order/cancel?order_id={order_id}", session)
        return {"order_id": data.get("order_id", order_id), "status": "CANCELLED", "broker": "upstox"}

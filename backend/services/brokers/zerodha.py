"""Zerodha Kite Connect v3 adapter.

Official API docs: https://kite.trade/docs/connect/v3/
Auth model: browser login -> request_token -> POST /session/token with
sha256(api_key + request_token + api_secret). Access tokens are invalidated
daily around 06:00 IST; Kite Connect has no refresh grant for retail apps,
so refresh_session() reports "reconnect required".
"""
import hashlib
import json
import logging
import struct
from datetime import datetime, timedelta, timezone
from typing import Any, List
from urllib.parse import quote

from services.brokers.base import (
    IST, BrokerAdapter, BrokerAuthError, BrokerError, normalize_status,
)
from services.brokers.capabilities import BrokerCapability
from services.brokers.credentials import BrokerCredentialSpec
from services.brokers.streaming import BrokerStreamEndpoint, BrokerStreamEvent

logger = logging.getLogger(__name__)

BASE_URL = "https://api.kite.trade"
LOGIN_URL = "https://kite.zerodha.com/connect/login"
#: Kite ticker v3. Moved here from `stream.py` in D4.2 — a broker's endpoint is
#: the broker's business, and a shared module holding it is what made adding a
#: streaming broker an edit to code no broker owns.
WS_URL = "wss://ws.kite.trade"

#: Kite quotes equity and derivative prices in paise (price * 100). The currency
#: segment uses a different divisor; equities are our scope.
PAISE = 100.0


def parse_kite_binary(payload: bytes) -> list:
    """Parse a Kite ticker binary frame into [{instrument_token, last_price}].

    Frame layout (Kite Connect v3 docs):
      2 bytes  number of packets (int16 BE)
      per packet: 2 bytes length (int16 BE) + packet bytes
      LTP packet: 4 bytes instrument_token + 4 bytes ltp (price * 100)
    1-byte frames are heartbeats.

    Lived in `stream.py` until D4.2. Nothing about this layout is generic — it
    is Kite's framing, byte for byte — and holding it in the shared transport
    meant the *shape it produced* became the platform's de-facto tick contract
    by accident. It is now one step inside the adapter, and its output is
    coerced through `BrokerTick` before anything else sees it.
    """
    if len(payload) < 4:
        return []
    ticks = []
    try:
        count = struct.unpack_from(">H", payload, 0)[0]
        offset = 2
        for _ in range(count):
            if offset + 2 > len(payload):
                break
            length = struct.unpack_from(">H", payload, offset)[0]
            offset += 2
            packet = payload[offset : offset + length]
            offset += length
            if len(packet) < 8:
                continue
            token, ltp = struct.unpack_from(">ii", packet, 0)
            ticks.append({"instrument_token": token, "last_price": ltp / PAISE})
    except struct.error:
        logger.debug("Malformed Kite binary frame skipped")
    return ticks


class ZerodhaAdapter(BrokerAdapter):
    """Zerodha Kite Connect — the framework's first concrete adapter.

    Nothing in this module is referenced by name anywhere outside it and the
    registry entry in `__init__.py`. That is the property D3 exists to establish
    and the one the framework tests assert: a second broker is added by writing
    a sibling of this file, and no core engine, route or frontend component
    changes.
    """

    name = "zerodha"
    display_name = "Zerodha"

    #: Kite Connect v3 covers the full account, order and realtime surface.
    #: SESSION_REFRESH is absent and that absence is the point: Kite issues
    #: daily tokens with no refresh grant for retail apps, so the engine reads
    #: this set and prompts a reconnect instead of attempting a refresh that
    #: cannot succeed. Before D3 that fact lived in a `return None` override
    #: that no caller could see without reading the method body.
    capabilities = frozenset({
        BrokerCapability.PROFILE,
        BrokerCapability.HOLDINGS,
        BrokerCapability.POSITIONS,
        BrokerCapability.FUNDS,
        BrokerCapability.MARGINS,
        BrokerCapability.ORDERS,
        BrokerCapability.TRADES,
        BrokerCapability.PLACE_ORDER,
        BrokerCapability.MODIFY_ORDER,
        BrokerCapability.CANCEL_ORDER,
        BrokerCapability.SESSION_INVALIDATE,
        BrokerCapability.ORDER_STREAM,
        BrokerCapability.TICK_STREAM,
    })

    credential_spec = BrokerCredentialSpec(
        api_key_env="KITE_API_KEY",
        api_secret_env="KITE_API_SECRET",
        redirect_url_env="KITE_REDIRECT_URL",
    )

    #: Kite's delivery product code.
    default_product = "CNC"

    #: Kite Connect ticker (binary ticks + JSON order frames).
    stream_protocol = "kite_ticker"

    def _credentials(self):
        """(api_key, api_secret) — read through the credentials boundary.

        Kept as a private tuple accessor because this module uses both values in
        four places; the values themselves come from `credential_spec`, so no
        environment variable name appears in this file.
        """
        creds = self.credentials
        return creds.api_key, creds.api_secret

    def _headers(self, session: dict) -> dict:
        api_key, _ = self._credentials()
        token = (session or {}).get("access_token")
        if not token:
            raise BrokerAuthError("Zerodha is not connected. Connect your account in Settings.")
        return {"X-Kite-Version": "3", "Authorization": f"token {api_key}:{token}"}

    async def _kite(self, method: str, path: str, session: dict = None, data: dict = None) -> dict:
        payload = await self._request(method, f"{BASE_URL}{path}",
                                      headers=self._headers(session) if session is not None else None,
                                      data=data)
        if payload.get("status") != "success":
            error_type = payload.get("error_type", "")
            message = payload.get("message", "Zerodha request failed")
            if error_type == "TokenException":
                raise BrokerAuthError("Zerodha session expired (tokens reset daily at 6 AM IST). Please reconnect.")
            raise BrokerError(f"Zerodha error [{error_type}]: {message}", user_message=message)
        return payload.get("data") or {}

    # -- authentication ----------------------------------------------------
    def get_login_url(self, user_id: str = None) -> dict:
        api_key, _ = self._credentials()
        if not api_key:
            return {"url": None, "configured": False,
                    "message": "Zerodha API key not configured. Add KITE_API_KEY to .env"}
        url = f"{LOGIN_URL}?v=3&api_key={api_key}"
        if user_id:
            # Kite echoes redirect_params back onto the registered redirect
            # URL, letting the public callback identify the app user.
            url += f"&redirect_params={quote(f'uid={user_id}')}"
        return {"url": url, "configured": True}

    async def exchange_token(self, auth_payload: dict) -> dict:
        api_key, api_secret = self._credentials()
        request_token = (auth_payload or {}).get("request_token")
        if not (api_key and api_secret):
            raise BrokerError("Zerodha not configured")
        if not request_token:
            raise BrokerError("request_token required")
        checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()
        data = await self._kite("POST", "/session/token", session=None, data={
            "api_key": api_key, "request_token": request_token, "checksum": checksum,
        })
        now = datetime.now(timezone.utc)
        return {
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", "") or "",
            "public_token": data.get("public_token", "") or "",
            "expires_at": self.session_expiry(now).isoformat(),
            "account_id": data.get("user_id", ""),
            "profile": {
                "user_id": data.get("user_id"),
                "user_name": data.get("user_name"),
                "email": data.get("email"),
                "broker": "ZERODHA",
                "exchanges": data.get("exchanges", []),
            },
        }

    def parse_callback_params(self, params: dict) -> dict:
        """Kite redirects with `?request_token=&status=success`, not `?code=`."""
        params = params or {}
        if params.get("status") != "success" or not params.get("request_token"):
            return None
        return {"request_token": params.get("request_token")}

    def session_expiry(self, connected_at: datetime) -> datetime:
        """Kite tokens die at ~06:00 IST the following morning."""
        local = connected_at.astimezone(IST)
        expiry = local.replace(hour=6, minute=0, second=0, microsecond=0)
        if local.hour >= 6:
            expiry += timedelta(days=1)
        return expiry.astimezone(timezone.utc)

    async def invalidate_session(self, session: dict) -> None:
        """Logout: DELETE /session/token invalidates the access token."""
        api_key, _ = self._credentials()
        token = (session or {}).get("access_token")
        if not token:
            return
        try:
            await self._request("DELETE",
                                f"{BASE_URL}/session/token?api_key={api_key}&access_token={token}",
                                headers={"X-Kite-Version": "3"})
        except Exception as e:
            logger.warning(f"Zerodha session invalidation failed (token may already be dead): {e}")

    # -- account data --------------------------------------------------------
    async def get_profile(self, session: dict) -> dict:
        data = await self._kite("GET", "/user/profile", session)
        return {
            "account_id": data.get("user_id"),
            "user_name": data.get("user_name"),
            "email": data.get("email"),
            "broker": "ZERODHA",
            "exchanges": data.get("exchanges", []),
            "products": data.get("products", []),
        }

    async def get_holdings(self, session: dict) -> list:
        rows = await self._kite("GET", "/portfolio/holdings", session)
        holdings = []
        for h in rows or []:
            qty = (h.get("quantity") or 0) + (h.get("t1_quantity") or 0)
            avg = h.get("average_price") or 0
            ltp = h.get("last_price") or 0
            invested = qty * avg
            value = qty * ltp
            holdings.append({
                "symbol": h.get("tradingsymbol"),
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
            })
        return holdings

    async def get_positions(self, session: dict) -> list:
        data = await self._kite("GET", "/portfolio/positions", session)
        positions = []
        for p in data.get("net", []) or []:
            qty = p.get("quantity") or 0
            positions.append({
                "symbol": p.get("tradingsymbol"),
                "exchange": p.get("exchange"),
                "product": p.get("product"),
                "quantity": qty,
                "average_price": p.get("average_price") or 0,
                "last_price": p.get("last_price") or 0,
                "pnl": round(p.get("pnl") or 0, 2),
                "realised": round(p.get("realised") or 0, 2),
                "unrealised": round(p.get("unrealised") or 0, 2),
                "buy_quantity": p.get("buy_quantity") or 0,
                "sell_quantity": p.get("sell_quantity") or 0,
                "side": "LONG" if qty > 0 else ("SHORT" if qty < 0 else "FLAT"),
                "instrument_token": p.get("instrument_token"),
            })
        return positions

    async def get_funds(self, session: dict) -> dict:
        data = await self._kite("GET", "/user/margins", session)
        equity = data.get("equity") or {}
        available = equity.get("available") or {}
        utilised = equity.get("utilised") or {}
        return {
            "available_margin": round(available.get("live_balance", available.get("cash", 0)) or 0, 2),
            "used_margin": round(utilised.get("debits", 0) or 0, 2),
            "opening_balance": round(available.get("opening_balance", 0) or 0, 2),
            "payin": round(available.get("intraday_payin", 0) or 0, 2),
            "payout": round(utilised.get("payout", 0) or 0, 2),
            "collateral": round(available.get("collateral", 0) or 0, 2),
            "total_balance": round(equity.get("net", 0) or 0, 2),
            "raw": {"equity": equity, "commodity": data.get("commodity") or {}},
        }

    async def get_orders(self, session: dict) -> list:
        rows = await self._kite("GET", "/orders", session)
        return [self._normalize_order(o) for o in rows or []]

    async def get_trades(self, session: dict) -> list:
        rows = await self._kite("GET", "/trades", session)
        return [{
            "trade_id": t.get("trade_id"),
            "order_id": t.get("order_id"),
            "symbol": t.get("tradingsymbol"),
            "exchange": t.get("exchange"),
            "transaction_type": t.get("transaction_type"),
            "quantity": t.get("quantity") or 0,
            "price": t.get("average_price") or 0,
            "product": t.get("product"),
            "executed_at": t.get("fill_timestamp") or t.get("exchange_timestamp"),
        } for t in rows or []]

    @staticmethod
    def _normalize_order(o: dict) -> dict:
        return {
            "order_id": o.get("order_id"),
            "symbol": o.get("tradingsymbol"),
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
            "updated_at": str(o.get("exchange_update_timestamp") or ""),
            "tag": o.get("tag"),
            "broker": "zerodha",
        }

    # -- realtime --------------------------------------------------------------
    def normalize_stream_order(self, payload: dict) -> dict:
        """Canonicalize a Kite ticker order frame (same shape as the REST book)."""
        return self._normalize_order(payload or {})

    def stream_instruments(self, holdings: list = None, positions: list = None) -> List[Any]:
        """Kite instrument tokens for every instrument in the user's portfolio.

        The Kite ticker subscribes by numeric `instrument_token`, not by symbol.
        This lived in `BrokerEngine.start_stream` behind `if broker ==
        "zerodha":` — a broker name inside the engine, guarding logic only this
        adapter can be correct about. Sorted and de-duplicated so a resubscribe
        after a portfolio sync produces a stable subscription list.
        """
        tokens = {
            row.get("instrument_token")
            for row in list(holdings or []) + list(positions or [])
            if isinstance(row.get("instrument_token"), int)
        }
        return sorted(tokens)

    # -- realtime: the Kite ticker codec (D4.2) --------------------------------
    def stream_endpoint(self, session: dict, credentials: dict = None) -> BrokerStreamEndpoint:
        """The Kite ticker socket for one user.

        Kite authenticates the ticker by query string, which is why
        `BrokerStreamEndpoint.safe_url` exists and why the transport logs
        nothing else: this URL carries a live access token.
        """
        credentials = credentials or self.stream_credentials()
        api_key = credentials.get("api_key") or ""
        token = (session or {}).get("access_token") or ""
        return BrokerStreamEndpoint(url=f"{WS_URL}?api_key={quote(str(api_key))}&access_token={quote(str(token))}")

    def stream_subscribe_frames(self, instruments: list = None) -> List[Any]:
        """Kite's two-frame subscription: subscribe, then set the mode.

        LTP mode deliberately — the tick feed drives portfolio and trade marks,
        which need a last price and nothing else. Full mode multiplies the
        bandwidth for depth nothing currently reads.
        """
        instruments = list(instruments or [])
        if not instruments:
            return []
        return [
            json.dumps({"a": "subscribe", "v": instruments}),
            json.dumps({"a": "mode", "v": ["ltp", instruments]}),
        ]

    def decode_stream_frame(self, frame: Any) -> BrokerStreamEvent:
        """Decode one Kite ticker frame: binary carries ticks, text carries JSON.

        Both arrive on the same socket, which is exactly why the contract
        decodes `bytes | str` in one method rather than assuming a broker's
        frames are all one type.
        """
        if isinstance(frame, (bytes, bytearray)):
            if len(frame) <= 1:
                return BrokerStreamEvent.ignore()  # heartbeat
            return BrokerStreamEvent.tick_event(parse_kite_binary(bytes(frame)))

        try:
            data = json.loads(frame)
        except (json.JSONDecodeError, TypeError, ValueError):
            return BrokerStreamEvent.ignore()
        if not isinstance(data, dict):
            return BrokerStreamEvent.ignore()

        message_type = data.get("type")
        if message_type == "order":
            return BrokerStreamEvent.order_event(
                self.normalize_stream_order(data.get("data") or {}), broker=self.name
            )
        if message_type == "error":
            text = str(data.get("data", ""))
            # Kite reports a dead access token as a plain error frame. Treated
            # as an auth failure so the transport stops instead of reconnecting
            # into the same rejection every two seconds until the ceiling.
            if "token" in text.lower():
                return BrokerStreamEvent.auth_expired(text)
            return BrokerStreamEvent.error(text)
        return BrokerStreamEvent.ignore()

    # -- order management ------------------------------------------------------
    async def place_order(self, session: dict, order: dict) -> dict:
        variety = (order.get("variety") or self.default_variety).lower()
        payload = {
            "exchange": order.get("exchange", "NSE"),
            "tradingsymbol": order["symbol"],
            "transaction_type": order.get("transaction_type", "BUY"),
            "quantity": int(order["quantity"]),
            "product": order.get("product", "CNC"),
            "order_type": order.get("order_type", "MARKET"),
            "validity": order.get("validity", "DAY"),
            "tag": (order.get("tag") or "StockAssistAI")[:20],
        }
        if payload["order_type"] in ("LIMIT", "SL"):
            payload["price"] = float(order.get("price") or 0)
        if payload["order_type"] in ("SL", "SL-M"):
            payload["trigger_price"] = float(order.get("trigger_price") or 0)
        data = await self._kite("POST", f"/orders/{variety}", session, data=payload)
        return {"order_id": data.get("order_id"), "status": "PENDING", "broker": "zerodha"}

    async def modify_order(self, session: dict, order_id: str, changes: dict) -> dict:
        variety = (changes.get("variety") or self.default_variety).lower()
        payload = {}
        for src, dst in (("quantity", "quantity"), ("price", "price"),
                         ("trigger_price", "trigger_price"), ("order_type", "order_type"),
                         ("validity", "validity")):
            if changes.get(src) is not None:
                payload[dst] = changes[src]
        if not payload:
            raise BrokerError("No changes supplied", user_message="Nothing to modify in this order.")
        data = await self._kite("PUT", f"/orders/{variety}/{order_id}", session, data=payload)
        return {"order_id": data.get("order_id", order_id), "status": "PENDING", "broker": "zerodha"}

    async def cancel_order(self, session: dict, order_id: str) -> dict:
        variety = self.default_variety
        data = await self._kite("DELETE", f"/orders/{variety}/{order_id}", session)
        return {"order_id": data.get("order_id", order_id), "status": "CANCELLED", "broker": "zerodha"}

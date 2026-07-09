"""Unified broker adapter interface (BROKER_INTEGRATION.md).

Every broker adapter implements this interface and returns the SAME
normalized shapes, so the Trading Engine / Portfolio Engine / AI / UI never
know which broker is connected.

Normalized shapes
-----------------
Holding:
    {symbol, exchange, quantity, average_price, last_price, market_value,
     invested_value, pnl, pnl_percent, product, isin, instrument_token}

Position:
    {symbol, exchange, product, quantity, average_price, last_price,
     pnl, realised, unrealised, buy_quantity, sell_quantity, side}

Order:
    {order_id, symbol, exchange, transaction_type, order_type, product,
     quantity, filled_quantity, pending_quantity, price, trigger_price,
     average_price, status, status_message, placed_at, updated_at, tag}

Trade:
    {trade_id, order_id, symbol, exchange, transaction_type, quantity,
     price, product, executed_at}

Funds:
    {available_margin, used_margin, opening_balance, payin, payout,
     collateral, total_balance}

All sessions carry: {access_token, refresh_token, expires_at, account_id,
profile} — tokens are encrypted by the engine before storage, never here.
"""
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Broker order APIs (Zerodha, Upstox) restrict order placement to whitelisted
# *IPv4* addresses. If the host has IPv6 connectivity, httpx will happily egress
# over IPv6 — and the broker rejects the order with "IP not allowed". Binding the
# outbound socket to an IPv4 local address forces IPv4 so the source IP matches
# the whitelisted static IP. Opt out with BROKER_FORCE_IPV4=false (e.g. IPv6-only
# hosts).
_FORCE_IPV4 = os.environ.get("BROKER_FORCE_IPV4", "true").strip().lower() in ("1", "true", "yes", "on")


def _broker_http_client(timeout: float) -> httpx.AsyncClient:
    """AsyncClient for broker HTTP calls, pinned to IPv4 egress when enabled."""
    if _FORCE_IPV4:
        return httpx.AsyncClient(timeout=timeout,
                                 transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"))
    return httpx.AsyncClient(timeout=timeout)

# Normalized order statuses (BROKER_INTEGRATION.md — Order Status)
ORDER_STATUS = {
    "CREATED", "PENDING", "OPEN", "PARTIALLY_FILLED", "FILLED",
    "CANCELLED", "REJECTED", "EXPIRED",
}


class BrokerError(Exception):
    """Base error for broker operations. `user_message` is safe for the UI."""

    def __init__(self, message: str, user_message: str = None, code: str = "BROKER_ERROR"):
        super().__init__(message)
        self.user_message = user_message or message
        self.code = code


class BrokerAuthError(BrokerError):
    """Session missing/expired — the user must reconnect the broker."""

    def __init__(self, message: str = "Broker session expired. Please reconnect."):
        super().__init__(message, user_message=message, code="BROKER_AUTH")


def normalize_status(raw: str) -> str:
    """Map broker-specific order statuses onto the unified set."""
    s = (raw or "").strip().upper().replace(" ", "_")
    mapping = {
        "COMPLETE": "FILLED",
        "COMPLETED": "FILLED",
        "TRIGGER_PENDING": "PENDING",
        "VALIDATION_PENDING": "PENDING",
        "PUT_ORDER_REQ_RECEIVED": "PENDING",
        "MODIFY_PENDING": "PENDING",
        "CANCEL_PENDING": "PENDING",
        "OPEN_PENDING": "PENDING",
        "AMO_REQ_RECEIVED": "PENDING",
        "AFTER_MARKET_ORDER_REQ_RECEIVED": "PENDING",
        "PARTIALLY_FILLED": "PARTIALLY_FILLED",
    }
    normalized = mapping.get(s, s)
    return normalized if normalized in ORDER_STATUS else ("OPEN" if "OPEN" in normalized else normalized or "PENDING")


class BrokerAdapter(ABC):
    """Abstract broker adapter. One instance per broker type (stateless —
    per-user session tokens are passed into every call by the BrokerEngine)."""

    name: str = "base"
    display_name: str = "Broker"

    # -- configuration -----------------------------------------------------
    @abstractmethod
    def is_configured(self) -> bool:
        """True when API key/secret are present in the environment."""

    # -- authentication ----------------------------------------------------
    @abstractmethod
    def get_login_url(self, user_id: str = None) -> dict:
        """OAuth login URL for the browser redirect flow.
        Returns {url, configured, message?}."""

    @abstractmethod
    async def exchange_token(self, auth_payload: dict) -> dict:
        """Exchange the OAuth callback payload (request_token / code) for a
        session: {access_token, refresh_token?, expires_at, account_id, profile}."""

    async def refresh_session(self, session: dict) -> Optional[dict]:
        """Refresh the access token where the broker supports it.

        Indian retail broker APIs (Kite Connect, Upstox v2) issue daily tokens
        without a refresh grant, so the default returns None — meaning "not
        refreshable, reconnect required". Adapters for brokers with refresh
        tokens override this.
        """
        return None

    @abstractmethod
    def session_expiry(self, connected_at: datetime) -> datetime:
        """When a session created at `connected_at` stops being valid."""

    def session_is_fresh(self, session: dict) -> bool:
        expires_at = session.get("expires_at")
        if not expires_at:
            return False
        try:
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            return datetime.now(timezone.utc) < expires_at.astimezone(timezone.utc)
        except Exception:
            return False

    # -- account data (all take the decrypted session dict) -----------------
    @abstractmethod
    async def get_profile(self, session: dict) -> dict: ...

    @abstractmethod
    async def get_holdings(self, session: dict) -> list: ...

    @abstractmethod
    async def get_positions(self, session: dict) -> list: ...

    @abstractmethod
    async def get_funds(self, session: dict) -> dict: ...

    async def get_margins(self, session: dict) -> dict:
        """Margin details. Defaults to the funds payload (same source for
        Kite/Upstox); adapters may override with richer data."""
        return await self.get_funds(session)

    @abstractmethod
    async def get_orders(self, session: dict) -> list: ...

    @abstractmethod
    async def get_trades(self, session: dict) -> list:
        """Executed trades for the day (trade history)."""

    # -- order management ---------------------------------------------------
    @abstractmethod
    async def place_order(self, session: dict, order: dict) -> dict:
        """Place an order. `order` uses normalized fields:
        {symbol, exchange, transaction_type, quantity, order_type, product,
         price?, trigger_price?, validity?, tag?}
        Returns {order_id, status}."""

    @abstractmethod
    async def modify_order(self, session: dict, order_id: str, changes: dict) -> dict: ...

    @abstractmethod
    async def cancel_order(self, session: dict, order_id: str) -> dict: ...

    # -- health --------------------------------------------------------------
    async def health_check(self, session: dict) -> dict:
        """Cheap authenticated call to verify the session is alive."""
        try:
            profile = await self.get_profile(session)
            return {"healthy": True, "profile": profile}
        except BrokerAuthError:
            return {"healthy": False, "reason": "auth"}
        except Exception as e:
            return {"healthy": False, "reason": str(e)}

    @staticmethod
    def _extract_broker_error(resp) -> tuple:
        """Pull a human-readable (message, error_type) out of a broker error
        response. Handles the Kite shape ({message, error_type}) and the Upstox
        shape ({errors: [{message, error_code}]}); falls back to raw text so we
        never silently discard the broker's actual reason."""
        try:
            body = resp.json()
        except Exception:
            return (resp.text or "").strip()[:300], ""
        if isinstance(body, dict):
            message = (body.get("message") or body.get("error_description")
                       or body.get("error") or "")
            error_type = body.get("error_type") or ""
            errors = body.get("errors")
            if not message and isinstance(errors, list) and errors:
                first = errors[0] if isinstance(errors[0], dict) else {}
                message = first.get("message", "") or str(errors[0])
                error_type = error_type or first.get("error_code", "")
            return message, error_type
        return str(body)[:300], ""

    # -- shared HTTP helper ---------------------------------------------------
    async def _request(self, method: str, url: str, headers: dict = None,
                       data: dict = None, json_body: dict = None, timeout: float = 12.0) -> dict:
        """One HTTP call with normalized error handling. Kept as a single
        method so tests can patch it and rate/retry policy stays centralized."""
        try:
            async with _broker_http_client(timeout) as client:
                resp = await client.request(method, url, headers=headers, data=data, json=json_body)
        except httpx.TimeoutException:
            raise BrokerError(f"{self.display_name} API timeout",
                              user_message=f"{self.display_name} did not respond in time. Please retry.")
        except httpx.HTTPError as e:
            raise BrokerError(f"{self.display_name} network error: {e}",
                              user_message=f"Could not reach {self.display_name}. Check your connection and retry.")
        if resp.status_code in (401, 403):
            detail, error_type = self._extract_broker_error(resp)
            logger.warning("%s %s %s -> %s [%s] %s", self.display_name, method, url,
                           resp.status_code, error_type or "-", detail or "-")
            # A dead token is the common case, but brokers also return 401/403
            # for permission and order-window rejections (e.g. an intraday order
            # after market close). Only tell the user to reconnect on a genuine
            # auth failure; otherwise surface the broker's real reason so it is
            # not misdiagnosed as an expired session.
            if error_type == "TokenException" or resp.status_code == 401 or not detail:
                raise BrokerAuthError(
                    f"{self.display_name} session expired or unauthorized. Please reconnect.")
            raise BrokerError(f"{self.display_name} rejected the request [{error_type}]: {detail}",
                              user_message=detail, code="BROKER_REJECTED")
        if resp.status_code == 429:
            raise BrokerError(f"{self.display_name} rate limit hit",
                              user_message=f"{self.display_name} rate limit reached. Please wait a moment.",
                              code="RATE_LIMIT")
        try:
            payload = resp.json()
        except Exception:
            raise BrokerError(f"{self.display_name} returned non-JSON response ({resp.status_code})",
                              user_message=f"{self.display_name} returned an unexpected response.")
        return payload

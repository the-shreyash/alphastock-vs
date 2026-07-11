"""Broker Engine — the unified brokerage layer (BROKER_INTEGRATION.md).

Single entry point for everything broker-related:

    UI / Trading Engine  →  BrokerEngine  →  BrokerAdapter (Zerodha / Upstox)

Responsibilities:
  • OAuth session lifecycle (exchange, encrypted storage, expiry, refresh,
    reconnect prompts) — tokens are Fernet-encrypted at rest, never logged.
  • Per-user portfolio sync into MongoDB (portfolios / holdings collections).
  • Order placement / modification / cancellation with audit logging.
  • Realtime broker WebSocket streams (order updates + price ticks) forwarded
    to the app's per-user WebSocket channel.
  • Legacy migration: plaintext tokens written before Sprint 7 are re-saved
    encrypted on first load.

No simulated trading: every call goes to the official broker API; when a
broker is not connected the engine raises BrokerAuthError and endpoints
surface an explicit "connect your broker" state.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from services.brokers import SUPPORTED_BROKERS, create_adapter
from services.brokers.base import BrokerAdapter, BrokerAuthError, BrokerError
from services.brokers.crypto import decrypt_token, encrypt_token, is_encrypted
from services.brokers.stream import stream_manager

logger = logging.getLogger(__name__)

TOKEN_FIELDS = ("access_token", "refresh_token", "public_token")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BrokerEngine:
    def __init__(self):
        self.db = None
        self.ws_push = None            # async (user_id, message) -> None
        self._adapters: dict[str, BrokerAdapter] = {}
        self._sessions: dict = {}      # (user_id, broker) -> decrypted session dict

    # -- wiring -----------------------------------------------------------------
    def configure(self, db, ws_push=None):
        self.db = db
        self.ws_push = ws_push

    def adapter(self, broker: str) -> BrokerAdapter:
        broker = (broker or "").lower()
        if broker not in self._adapters:
            self._adapters[broker] = create_adapter(broker)
        return self._adapters[broker]

    def list_brokers(self) -> list:
        return [{
            "name": name,
            "display_name": cls.display_name,
            "configured": self.adapter(name).is_configured(),
        } for name, cls in SUPPORTED_BROKERS.items()]

    # -- audit / events ------------------------------------------------------------
    async def _audit(self, user_id: str, action: str, details: dict = None):
        """Immutable audit trail (SECURITY.md). Never contains token values."""
        if self.db is None:
            return
        try:
            await self.db.audit_logs.insert_one({
                "user_id": user_id,
                "category": "broker",
                "action": action,
                "details": details or {},
                "created_at": _now_iso(),
            })
        except Exception as e:
            logger.error(f"Audit log write failed for {action}: {e}")

    def _activity(self, message: str):
        try:
            from services.activity_logger import log_activity
            log_activity(message, "monitor", "done")
        except Exception:
            pass

    async def _push(self, user_id: str, message: dict):
        if self.ws_push:
            try:
                await self.ws_push(user_id, message)
            except Exception:
                pass

    # -- session storage ------------------------------------------------------------
    def _encrypt_doc(self, session: dict) -> dict:
        doc = dict(session)
        for field in TOKEN_FIELDS:
            if field in doc:
                doc[field] = encrypt_token(doc.get(field) or "")
        return doc

    def _decrypt_doc(self, doc: dict) -> dict:
        session = dict(doc)
        for field in TOKEN_FIELDS:
            if field in session:
                session[field] = decrypt_token(session.get(field) or "")
        return session

    async def _save_account(self, user_id: str, broker: str, session: dict):
        """Upsert the broker account with encrypted tokens; refresh cache."""
        doc = self._encrypt_doc(session)
        doc.update({"user_id": user_id, "broker": broker, "connected": True})
        doc.setdefault("connected_at", _now_iso())
        doc["last_refresh"] = _now_iso()
        await self.db.broker_accounts.update_one(
            {"user_id": user_id, "broker": broker}, {"$set": doc}, upsert=True)
        self._sessions[(user_id, broker)] = dict(session)

    async def _load_account(self, user_id: str, broker: str) -> Optional[dict]:
        doc = await self.db.broker_accounts.find_one({"user_id": user_id, "broker": broker})
        if not doc:
            return None
        needs_migration = any(
            doc.get(f) and not is_encrypted(doc[f]) for f in TOKEN_FIELDS)
        session = self._decrypt_doc(doc)
        session.pop("_id", None)
        if needs_migration and session.get("access_token"):
            # Legacy plaintext token (pre-Sprint 7) — re-save encrypted.
            adapter = self.adapter(broker)
            if not session.get("expires_at") and session.get("connected_at"):
                try:
                    connected = datetime.fromisoformat(session["connected_at"])
                    session["expires_at"] = adapter.session_expiry(connected).isoformat()
                except Exception:
                    pass
            await self.db.broker_accounts.update_one(
                {"user_id": user_id, "broker": broker},
                {"$set": self._encrypt_doc({f: session.get(f, "") for f in TOKEN_FIELDS})
                 | ({"expires_at": session["expires_at"]} if session.get("expires_at") else {})})
            logger.info(f"Migrated plaintext {broker} tokens to encrypted storage for user {user_id}")
        return session

    async def get_session(self, user_id: str, broker: str) -> dict:
        """Return a live (fresh) decrypted session or raise BrokerAuthError.
        Attempts a token refresh first where the broker supports it."""
        key = (user_id, broker)
        session = self._sessions.get(key) or await self._load_account(user_id, broker)
        if not session or not session.get("access_token"):
            raise BrokerAuthError(f"{self.adapter(broker).display_name} is not connected. "
                                  "Connect your account in Settings.")
        adapter = self.adapter(broker)
        if adapter.session_is_fresh(session):
            self._sessions[key] = session
            return session
        refreshed = await adapter.refresh_session(session)
        if refreshed and refreshed.get("access_token"):
            await self._save_account(user_id, broker, {**session, **refreshed})
            await self._audit(user_id, "broker.token.refreshed", {"broker": broker})
            return self._sessions[key]
        self._sessions.pop(key, None)
        raise BrokerAuthError(f"{adapter.display_name} session expired. Please reconnect from Settings.")

    # -- authentication flows -----------------------------------------------------------
    def get_login_url(self, broker: str, user_id: str) -> dict:
        return self.adapter(broker).get_login_url(user_id=user_id)

    async def complete_auth(self, broker: str, user_id: str, auth_payload: dict) -> dict:
        """Exchange the OAuth callback payload, store the encrypted session,
        start the realtime stream and run an initial portfolio sync."""
        adapter = self.adapter(broker)
        session = await adapter.exchange_token(auth_payload)
        await self._save_account(user_id, broker, session)
        await self._audit(user_id, "broker.connected", {
            "broker": broker, "account_id": session.get("account_id")})
        self._activity(f"{adapter.display_name} account connected — live broker session active")
        await self._push(user_id, {"type": "broker_status", "data": {
            "broker": broker, "connected": True}})
        # Initial sync + stream are best-effort: connection succeeds even if
        # the first sync hits a transient broker error.
        sync_result = None
        try:
            sync_result = await self.sync_portfolio(user_id, broker)
        except Exception as e:
            logger.warning(f"Initial {broker} sync failed after connect: {e}")
            try:
                await self.start_stream(user_id, broker)
            except Exception as se:
                logger.warning(f"Stream start failed after connect: {se}")
        return {"success": True, "broker": broker,
                "profile": session.get("profile", {}), "sync": sync_result}

    async def disconnect(self, broker: str, user_id: str) -> dict:
        adapter = self.adapter(broker)
        await stream_manager.stop_stream(user_id, broker)
        try:
            session = self._sessions.get((user_id, broker)) or await self._load_account(user_id, broker)
            if session and session.get("access_token") and hasattr(adapter, "invalidate_session"):
                await adapter.invalidate_session(session)
        except Exception as e:
            logger.warning(f"{broker} token invalidation on disconnect failed: {e}")
        self._sessions.pop((user_id, broker), None)
        await self.db.broker_accounts.update_one(
            {"user_id": user_id, "broker": broker},
            {"$set": {"connected": False, "access_token": "", "refresh_token": "",
                      "public_token": "", "disconnected_at": _now_iso()}})
        await self._audit(user_id, "broker.disconnected", {"broker": broker})
        self._activity(f"{adapter.display_name} account disconnected")
        await self._push(user_id, {"type": "broker_status", "data": {
            "broker": broker, "connected": False}})
        return {"success": True, "broker": broker}

    async def get_status(self, user_id: str) -> dict:
        """Connection status for every supported broker for this user."""
        statuses = {}
        for broker in SUPPORTED_BROKERS:
            adapter = self.adapter(broker)
            configured = adapter.is_configured()
            doc = await self.db.broker_accounts.find_one(
                {"user_id": user_id, "broker": broker}) if self.db is not None else None
            session = self._decrypt_doc(doc) if doc else None
            has_token = bool(session and session.get("access_token"))
            fresh = bool(has_token and adapter.session_is_fresh(session))
            expired = has_token and not fresh
            if fresh:
                message = f"Connected to {adapter.display_name}"
                account = (session.get("profile") or {}).get("user_id") or session.get("account_id")
                if account:
                    message += f" ({account})"
            elif expired:
                message = f"{adapter.display_name} session expired. Please login again."
            elif configured:
                message = "API keys configured. Login required."
            else:
                message = f"{adapter.display_name} API keys not configured."
            statuses[broker] = {
                "broker": broker,
                "display_name": adapter.display_name,
                "configured": configured,
                "connected": fresh,
                "session_expired": expired,
                "profile": (session.get("profile") or {}) if fresh else {},
                "connected_at": session.get("connected_at") if fresh else None,
                "expires_at": session.get("expires_at") if fresh else None,
                "last_sync": (session or {}).get("last_sync"),
                "mode": "live" if fresh else ("ready" if configured else "disconnected"),
                "message": message,
                "streaming": any(s["running"] for s in stream_manager.status()
                                 if s["user_id"] == user_id and s["broker"] == broker),
            }
        return statuses

    # -- data access (unified across brokers) ---------------------------------------------
    async def get_profile(self, user_id: str, broker: str) -> dict:
        return await self.adapter(broker).get_profile(await self.get_session(user_id, broker))

    async def get_holdings(self, user_id: str, broker: str) -> list:
        return await self.adapter(broker).get_holdings(await self.get_session(user_id, broker))

    async def get_positions(self, user_id: str, broker: str) -> list:
        return await self.adapter(broker).get_positions(await self.get_session(user_id, broker))

    async def get_funds(self, user_id: str, broker: str) -> dict:
        return await self.adapter(broker).get_funds(await self.get_session(user_id, broker))

    async def get_margins(self, user_id: str, broker: str) -> dict:
        return await self.adapter(broker).get_margins(await self.get_session(user_id, broker))

    async def get_orders(self, user_id: str, broker: str) -> list:
        return await self.adapter(broker).get_orders(await self.get_session(user_id, broker))

    async def get_trades(self, user_id: str, broker: str) -> list:
        return await self.adapter(broker).get_trades(await self.get_session(user_id, broker))

    # -- portfolio sync ---------------------------------------------------------------------
    async def sync_portfolio(self, user_id: str, broker: str) -> dict:
        """Pull holdings/positions/funds from the broker, persist them and
        broadcast a portfolio.synced event. Restarts the realtime stream so
        tick subscriptions cover the current holdings."""
        session = await self.get_session(user_id, broker)
        adapter = self.adapter(broker)
        holdings = await adapter.get_holdings(session)
        positions = await adapter.get_positions(session)
        try:
            funds = await adapter.get_funds(session)
        except BrokerError as e:
            logger.warning(f"{broker} funds fetch failed during sync: {e}")
            funds = None

        now = _now_iso()
        invested = round(sum(h["invested_value"] for h in holdings), 2)
        current = round(sum(h["market_value"] for h in holdings), 2)
        await self.db.portfolios.update_one(
            {"user_id": user_id, "broker": broker},
            {"$set": {
                "user_id": user_id,
                "broker": broker,
                "total_value": current,
                "invested_amount": invested,
                "unrealized_pnl": round(current - invested, 2),
                "cash_balance": (funds or {}).get("available_margin"),
                "holdings_count": len(holdings),
                "positions_count": len(positions),
                "last_synced": now,
            }},
            upsert=True)
        await self.db.holdings.delete_many({"user_id": user_id, "broker": broker})
        if holdings:
            await self.db.holdings.insert_many([
                {**h, "user_id": user_id, "broker": broker, "updated_at": now}
                for h in holdings])
        await self.db.broker_accounts.update_one(
            {"user_id": user_id, "broker": broker}, {"$set": {"last_sync": now}})
        session["last_sync"] = now

        await self._audit(user_id, "broker.portfolio.synced", {
            "broker": broker, "holdings": len(holdings), "positions": len(positions)})
        self._activity(f"{adapter.display_name} portfolio synced — "
                       f"{len(holdings)} holdings, {len(positions)} positions")
        result = {
            "success": True, "broker": broker, "synced_at": now,
            "holdings": holdings, "positions": positions, "funds": funds,
            "summary": {"invested": invested, "current": current,
                        "pnl": round(current - invested, 2),
                        "holdings_count": len(holdings),
                        "positions_count": len(positions)},
        }
        await self._push(user_id, {"type": "portfolio_synced", "data": {
            "broker": broker, "summary": result["summary"], "synced_at": now}})
        # Sprint R5: publish the doc's `portfolio.synced` event (per-user via
        # the bridge) and follow with a fresh full snapshot so allocation/P&L
        # surfaces update the moment a sync lands. Best-effort.
        try:
            from services import portfolio_stream
            from services.market_engine.event_bus import event_bus
            await event_bus.publish("portfolio.synced", {
                "user_id": str(user_id), "broker": broker,
                "summary": result["summary"], "synced_at": now})
            await portfolio_stream.publish_snapshot(self.db, user_id, reason="broker_sync")
        except Exception as e:
            logger.warning(f"portfolio.synced publish after {broker} sync failed: {e}")
        try:
            await self.start_stream(user_id, broker, holdings=holdings, positions=positions)
        except Exception as e:
            logger.warning(f"Stream restart after {broker} sync failed: {e}")
        return result

    # -- orders ---------------------------------------------------------------------------------
    async def place_order(self, user_id: str, broker: str, order: dict) -> dict:
        session = await self.get_session(user_id, broker)
        result = await self.adapter(broker).place_order(session, order)
        await self._record_order(user_id, broker, {**order, **result})
        await self._audit(user_id, "broker.order.placed", {
            "broker": broker, "order_id": result.get("order_id"),
            "symbol": order.get("symbol"), "transaction_type": order.get("transaction_type"),
            "quantity": order.get("quantity"), "order_type": order.get("order_type")})
        self._activity(f"Order placed on {self.adapter(broker).display_name}: "
                       f"{order.get('transaction_type', 'BUY')} {order.get('quantity')} {order.get('symbol')}")
        return result

    async def modify_order(self, user_id: str, broker: str, order_id: str, changes: dict) -> dict:
        session = await self.get_session(user_id, broker)
        result = await self.adapter(broker).modify_order(session, order_id, changes)
        await self._audit(user_id, "broker.order.modified", {
            "broker": broker, "order_id": order_id, "changes": changes})
        return result

    async def cancel_order(self, user_id: str, broker: str, order_id: str) -> dict:
        session = await self.get_session(user_id, broker)
        result = await self.adapter(broker).cancel_order(session, order_id)
        await self._audit(user_id, "broker.order.cancelled", {
            "broker": broker, "order_id": order_id})
        return result

    async def sync_orders(self, user_id: str, broker: str) -> list:
        """Pull today's order book from the broker and persist every order into
        db.orders (the unified order-history store, also fed by placements and
        the realtime stream). Returns the fetched orders."""
        orders = await self.get_orders(user_id, broker)
        for order in orders:
            await self._record_order(user_id, broker, order)
        return orders

    async def _record_order(self, user_id: str, broker: str, order: dict):
        if not order.get("order_id"):
            return
        doc = {k: v for k, v in order.items() if k != "_id"}
        doc.update({"user_id": user_id, "broker": broker, "updated_at": _now_iso()})
        doc.setdefault("placed_at", _now_iso())
        await self.db.orders.update_one(
            {"user_id": user_id, "broker": broker, "order_id": order["order_id"]},
            {"$set": doc}, upsert=True)

    # -- realtime streaming -------------------------------------------------------------------------
    async def start_stream(self, user_id: str, broker: str,
                           holdings: list = None, positions: list = None):
        """Open the official broker WebSocket for this account. Zerodha also
        subscribes to LTP ticks for every instrument in the portfolio."""
        session = await self.get_session(user_id, broker)
        instrument_tokens = []
        if broker == "zerodha":
            if holdings is None:
                try:
                    holdings = await self.adapter(broker).get_holdings(session)
                except BrokerError:
                    holdings = []
            if positions is None:
                try:
                    positions = await self.adapter(broker).get_positions(session)
                except BrokerError:
                    positions = []
            tokens = {row.get("instrument_token")
                      for row in (holdings or []) + (positions or [])
                      if isinstance(row.get("instrument_token"), int)}
            instrument_tokens = sorted(tokens)
        import os
        await stream_manager.start_stream(
            user_id, broker, session,
            api_key=os.environ.get("KITE_API_KEY", "").strip(),
            instrument_tokens=instrument_tokens,
            on_order_update=self._on_stream_order,
            on_tick=self._on_stream_tick,
            on_expired=self._on_stream_expired,
        )

    async def _on_stream_order(self, user_id: str, broker: str, order: dict):
        """Order update from the broker's realtime feed."""
        try:
            await self._record_order(user_id, broker, order)
        except Exception as e:
            logger.error(f"Failed to persist streamed order update: {e}")
        await self._push(user_id, {"type": "broker_order_update", "data": order})
        status = order.get("status")
        if status in ("FILLED", "REJECTED", "CANCELLED") and self.db is not None:
            verb = {"FILLED": "executed", "REJECTED": "rejected", "CANCELLED": "cancelled"}[status]
            try:
                await self.db.notifications.insert_one({
                    "user_id": user_id,
                    "type": f"ORDER_{status}",
                    "title": f"Order {verb}",
                    "message": f"{order.get('transaction_type', '')} {order.get('quantity', '')} "
                               f"{order.get('symbol', '')} — {verb} on {self.adapter(broker).display_name}."
                               + (f" Reason: {order.get('status_message')}" if status == "REJECTED" and order.get("status_message") else ""),
                    "severity": "critical" if status == "REJECTED" else "info",
                    "read": False,
                    "created_at": _now_iso(),
                })
            except Exception:
                pass

    async def _on_stream_tick(self, user_id: str, broker: str, ticks: list):
        await self._push(user_id, {"type": "broker_price_tick", "data": {
            "broker": broker, "ticks": ticks}})
        # Sprint R5: broker ticks drive the live portfolio — recompute this
        # user's P&L/allocation server-side and stream `portfolio.updated`
        # (throttled inside; best-effort so a recompute error never breaks
        # the raw tick forward above).
        try:
            from services import portfolio_stream
            await portfolio_stream.apply_broker_ticks(self.db, user_id, broker, ticks)
        except Exception as e:
            logger.warning(f"Live portfolio recompute from {broker} ticks failed: {e}")

    async def _on_stream_expired(self, user_id: str, broker: str):
        self._sessions.pop((user_id, broker), None)
        await self._audit(user_id, "broker.session.expired", {"broker": broker})
        await self._push(user_id, {"type": "broker_status", "data": {
            "broker": broker, "connected": False, "session_expired": True}})

    # -- startup ---------------------------------------------------------------------------------------
    async def load_sessions(self):
        """Restore fresh sessions (and streams) for every connected account on
        startup; encrypt any legacy plaintext tokens found along the way."""
        if self.db is None:
            return 0
        restored = 0
        try:
            docs = await self.db.broker_accounts.find({"connected": {"$ne": False}}).to_list(1000)
            for doc in docs:
                user_id, broker = doc.get("user_id"), doc.get("broker")
                if not user_id or broker not in SUPPORTED_BROKERS:
                    continue
                try:
                    session = await self._load_account(user_id, broker)
                    if not session or not session.get("access_token"):
                        continue
                    if not self.adapter(broker).session_is_fresh(session):
                        logger.info(f"Saved {broker} session for user {user_id} has expired; reconnect required.")
                        continue
                    self._sessions[(user_id, broker)] = session
                    restored += 1
                    try:
                        await self.start_stream(user_id, broker)
                    except Exception as e:
                        logger.warning(f"Could not start {broker} stream for user {user_id}: {e}")
                except Exception as e:
                    logger.error(f"Failed restoring {broker} session for user {user_id}: {e}")
        except Exception as e:
            logger.error(f"Broker session restore failed: {e}")
        if restored:
            logger.info(f"Restored {restored} live broker session(s) from database.")
        return restored

    async def shutdown(self):
        await stream_manager.stop_all()

    # -- legacy compatibility (single-session zerodha_service shim) ------------------------------------------
    async def any_connected_session(self, broker: str) -> Optional[tuple]:
        """(user_id, session) of the most recently connected fresh account for
        `broker` — used only by the legacy zerodha_service module API."""
        if self.db is None:
            return None
        docs = await self.db.broker_accounts.find(
            {"broker": broker, "connected": {"$ne": False}}
        ).sort("connected_at", -1).to_list(1)
        doc = docs[0] if docs else None
        if not doc:
            return None
        user_id = doc.get("user_id")
        try:
            session = await self.get_session(user_id, broker)
            return user_id, session
        except BrokerAuthError:
            return None


broker_engine = BrokerEngine()

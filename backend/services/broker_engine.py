"""Broker Engine — the unified brokerage layer (BROKER_INTEGRATION.md).

Single entry point for everything broker-related:

    UI / Trading Engine  →  BrokerEngine  →  BrokerGateway  →  Broker Adapter

WHAT D3 CHANGED HERE
--------------------
The engine used to hold adapters itself and call them directly, which made it
the place broker-specific knowledge accumulated: it read `KITE_API_KEY` by name
to open a stream, it branched on `if broker == "zerodha"` to decide what to
subscribe to, and it assembled the per-user connection status inline. Every one
of those is a reason a new broker could not be added without editing this file.

It now calls the Broker Gateway, which enforces capabilities, coerces responses
into the canonical contracts, normalizes errors and records broker health. The
engine keeps the responsibilities that are genuinely its own and that the
gateway deliberately does not want — the database, encryption at rest, session
lifecycle, audit logging, portfolio persistence and event publication.

It also publishes `broker.connected` / `broker.disconnected` on the Event Bus.
Both topics were documented in BROKER_INTEGRATION.md and neither was ever
published, which is why MARKET_DATA_ARCHITECTURE.md's Source Manager
responsibility 1 — "subscribes to broker connection lifecycle events" — had
nothing to subscribe to.

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

from services.brokers import BrokerCapability, broker_gateway, broker_registry
from services.brokers.base import BrokerAdapter
from services.brokers.crypto import decrypt_token, encrypt_token, is_encrypted
from services.brokers.errors import BrokerAuthError, BrokerError
from services.brokers.stream import stream_manager

logger = logging.getLogger(__name__)

TOKEN_FIELDS = ("access_token", "refresh_token", "public_token")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def adapter_display(broker: str) -> str:
    """Human-readable broker name for activity and notification copy.

    A one-line lookup rather than `self.adapter(broker).display_name` scattered
    through the engine: the display name is the only adapter attribute this
    module legitimately needs, and routing it through a named function keeps the
    deprecated `adapter()` accessor out of the engine's own code paths.
    """
    adapter = broker_registry.get(broker)
    return adapter.display_name if adapter else (broker or "Broker")


class BrokerEngine:
    def __init__(self):
        self.db = None
        self.ws_push = None            # async (user_id, message) -> None
        self._sessions: dict = {}      # (user_id, broker) -> decrypted session dict

    # -- wiring -----------------------------------------------------------------
    def configure(self, db, ws_push=None):
        self.db = db
        self.ws_push = ws_push

    def adapter(self, broker: str) -> BrokerAdapter:
        """DEPRECATED — the registered adapter for `broker`.

        Kept for `zerodha_service.py` (the legacy single-session shim) and for
        tests that patch adapter methods directly. The engine itself no longer
        calls broker methods through it; it goes through `broker_gateway`, which
        is what guarantees capability enforcement, canonical shapes and error
        normalization. New code must not reach for an adapter.

        It used to build and cache its own adapter instances, which meant the
        engine's adapters and anything else's were different objects with
        different health counters. There is now exactly one instance per broker,
        owned by the registry.
        """
        return broker_registry.require(broker)

    def list_brokers(self) -> list:
        """Supported brokers with their capabilities and configuration state."""
        return broker_gateway.list_brokers()

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
            if not session.get("expires_at") and session.get("connected_at"):
                try:
                    connected = datetime.fromisoformat(session["connected_at"])
                    session["expires_at"] = broker_gateway.session_expiry(broker, connected).isoformat()
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
        adapter = broker_registry.require(broker)
        session = self._sessions.get(key) or await self._load_account(user_id, broker)
        if not session or not session.get("access_token"):
            raise BrokerAuthError(f"{adapter.display_name} is not connected. "
                                  "Connect your account in Settings.")
        if broker_gateway.session_is_fresh(broker, session):
            self._sessions[key] = session
            return session
        # The gateway answers None both for "this broker has no refresh grant"
        # and for "the refresh failed"; the engine's response is the same either
        # way, which is why it does not need to tell them apart.
        refreshed = await broker_gateway.refresh_session(broker, session)
        if refreshed and refreshed.get("access_token"):
            await self._save_account(user_id, broker, {**session, **refreshed})
            await self._audit(user_id, "broker.token.refreshed", {"broker": broker})
            return self._sessions[key]
        self._sessions.pop(key, None)
        raise BrokerAuthError(f"{adapter.display_name} session expired. Please reconnect from Settings.")

    # -- authentication flows -----------------------------------------------------------
    def parse_callback_params(self, broker: str, params: dict) -> Optional[dict]:
        """The `exchange_token` payload for a broker's OAuth redirect, or None
        when the user cancelled."""
        return broker_gateway.parse_callback_params(broker, params)

    def get_login_url(self, broker: str, user_id: str) -> dict:
        return broker_gateway.login_url(broker, user_id=user_id)

    async def complete_auth(self, broker: str, user_id: str, auth_payload: dict) -> dict:
        """Exchange the OAuth callback payload, store the encrypted session,
        start the realtime stream and run an initial portfolio sync."""
        adapter = broker_registry.require(broker)
        session = await broker_gateway.exchange_token(broker, auth_payload)
        await self._save_account(user_id, broker, session)
        await self._audit(user_id, "broker.connected", {
            "broker": broker, "account_id": session.get("account_id")})
        self._activity(f"{adapter.display_name} account connected — live broker session active")
        await self._push(user_id, {"type": "broker_status", "data": {
            "broker": broker, "connected": True}})
        await self._publish_connection(user_id, broker, connected=True)
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
        adapter = broker_registry.require(broker)
        await stream_manager.stop_stream(user_id, broker)
        session = self._sessions.get((user_id, broker)) or await self._load_account(user_id, broker)
        if session and session.get("access_token"):
            # Capability-gated and best-effort inside the gateway: a broker with
            # no logout endpoint is not an error, and a broker that refuses to
            # log out an already-dead token must not fail the user's disconnect.
            # This replaced a `hasattr(adapter, "invalidate_session")` probe —
            # a duck-typing check that could not distinguish "this broker cannot
            # revoke tokens" from "someone renamed the method".
            await broker_gateway.invalidate_session(broker, session)
        self._sessions.pop((user_id, broker), None)
        await self.db.broker_accounts.update_one(
            {"user_id": user_id, "broker": broker},
            {"$set": {"connected": False, "access_token": "", "refresh_token": "",
                      "public_token": "", "disconnected_at": _now_iso()}})
        await self._audit(user_id, "broker.disconnected", {"broker": broker})
        self._activity(f"{adapter.display_name} account disconnected")
        await self._push(user_id, {"type": "broker_status", "data": {
            "broker": broker, "connected": False}})
        await self._publish_connection(user_id, broker, connected=False)
        return {"success": True, "broker": broker}

    async def _publish_connection(self, user_id: str, broker: str, *, connected: bool):
        """Publish `broker.connected` / `broker.disconnected` on the Event Bus.

        BROKER_INTEGRATION.md lists both topics and nothing published either,
        which left MARKET_DATA_ARCHITECTURE.md's Source Manager responsibility 1
        ("subscribes to broker connection lifecycle events") with nothing to
        subscribe to. The Source Manager now consumes these to maintain its
        per-user connected-broker registry — the record D4's market-data feed
        switch attaches a provider registration to.

        The payload carries the broker's *capabilities*, not just its name, so a
        consumer can decide what a connection makes possible without importing a
        broker module: the Source Manager reads `tick_stream` to know whether
        this connection could ever become a streaming market feed.

        Best-effort. A market-data listener failing must never fail a user's
        broker connection — the connection is the thing that succeeded.
        """
        try:
            from services.market_engine.event_bus import event_bus
            adapter = broker_registry.require(broker)
            await event_bus.publish(
                "broker.connected" if connected else "broker.disconnected",
                {
                    "user_id": str(user_id),
                    "broker": broker,
                    "capabilities": sorted(c.value for c in adapter.capabilities),
                },
            )
        except Exception as e:
            logger.warning(f"broker lifecycle event publish failed for {broker}: {e}")

    async def get_status(self, user_id: str) -> dict:
        """Connection status for every registered broker for this user.

        The per-broker record is built by the gateway as a
        :class:`~services.brokers.contracts.BrokerConnection` — the canonical
        user -> broker association — and this method adds only what needs the
        database or the stream manager: the broker profile and whether a stream
        is live. It used to assemble the whole dict inline, which is why no
        other code could construct or assert against the shape.

        Iterates the registry rather than a hardcoded broker list, so a new
        broker appears here by being registered.
        """
        statuses = {}
        for broker in broker_registry.names():
            doc = await self.db.broker_accounts.find_one(
                {"user_id": user_id, "broker": broker}) if self.db is not None else None
            session = self._decrypt_doc(doc) if doc else None
            streaming = any(s["running"] for s in stream_manager.status()
                            if s["user_id"] == user_id and s["broker"] == broker)
            connection = broker_gateway.connection(
                user_id=user_id, broker=broker, session=session, streaming=streaming)
            statuses[broker] = {
                **connection.as_dict(),
                "profile": (session.get("profile") or {}) if connection.connected else {},
                "message": self._connection_message(connection),
            }
        return statuses

    @staticmethod
    def _connection_message(connection) -> str:
        """The user-facing sentence for a connection state.

        Presentation, kept out of the contract on purpose: `BrokerConnection`
        travels to events, logs and AI context, and a display string has no
        business in any of those. Four states, four sentences, no broker name
        hardcoded — `display_name` comes from the adapter.
        """
        name = connection.display_name or connection.broker
        if connection.connected:
            account = f" ({connection.account_id})" if connection.account_id else ""
            return f"Connected to {name}{account}"
        if connection.session_expired:
            return f"{name} session expired. Please login again."
        if connection.configured:
            return "API keys configured. Login required."
        return f"{name} API keys not configured."

    # -- data access (unified across brokers) ---------------------------------------------
    async def get_profile(self, user_id: str, broker: str) -> dict:
        return await broker_gateway.get_profile(broker, await self.get_session(user_id, broker))

    async def get_holdings(self, user_id: str, broker: str) -> list:
        return await broker_gateway.get_holdings(broker, await self.get_session(user_id, broker))

    async def get_positions(self, user_id: str, broker: str) -> list:
        return await broker_gateway.get_positions(broker, await self.get_session(user_id, broker))

    async def get_funds(self, user_id: str, broker: str) -> dict:
        return await broker_gateway.get_funds(broker, await self.get_session(user_id, broker))

    async def get_margins(self, user_id: str, broker: str) -> dict:
        return await broker_gateway.get_margins(broker, await self.get_session(user_id, broker))

    async def get_orders(self, user_id: str, broker: str) -> list:
        return await broker_gateway.get_orders(broker, await self.get_session(user_id, broker))

    async def get_trades(self, user_id: str, broker: str) -> list:
        return await broker_gateway.get_trades(broker, await self.get_session(user_id, broker))

    # -- portfolio sync ---------------------------------------------------------------------
    async def sync_portfolio(self, user_id: str, broker: str) -> dict:
        """Pull holdings/positions/funds from the broker, persist them and
        broadcast a portfolio.synced event. Restarts the realtime stream so
        tick subscriptions cover the current holdings."""
        session = await self.get_session(user_id, broker)
        adapter = broker_registry.require(broker)
        holdings = await broker_gateway.get_holdings(broker, session)
        positions = await broker_gateway.get_positions(broker, session)
        try:
            funds = await broker_gateway.get_funds(broker, session)
        except BrokerError as e:
            # Includes CapabilityUnsupported for a broker with no funds
            # endpoint: a portfolio without a cash balance is still a portfolio,
            # and refusing to sync one would make an optional capability
            # mandatory in practice.
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
        result = await broker_gateway.place_order(broker, session, order)
        await self._record_order(user_id, broker, {**order, **result})
        await self._audit(user_id, "broker.order.placed", {
            "broker": broker, "order_id": result.get("order_id"),
            "symbol": order.get("symbol"), "transaction_type": order.get("transaction_type"),
            "quantity": order.get("quantity"), "order_type": order.get("order_type")})
        self._activity(f"Order placed on {adapter_display(broker)}: "
                       f"{order.get('transaction_type', 'BUY')} {order.get('quantity')} {order.get('symbol')}")
        return result

    async def modify_order(self, user_id: str, broker: str, order_id: str, changes: dict) -> dict:
        session = await self.get_session(user_id, broker)
        result = await broker_gateway.modify_order(broker, session, order_id, changes)
        await self._audit(user_id, "broker.order.modified", {
            "broker": broker, "order_id": order_id, "changes": changes})
        return result

    async def cancel_order(self, user_id: str, broker: str, order_id: str) -> dict:
        session = await self.get_session(user_id, broker)
        result = await broker_gateway.cancel_order(broker, session, order_id)
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
        """Open the broker's official WebSocket for this account.

        Fully broker-agnostic as of D3. What used to be here was an `if broker
        == "zerodha":` block that fetched the portfolio, extracted Kite
        instrument tokens, and an `os.environ["KITE_API_KEY"]` read to
        authenticate the socket — a broker name and a broker's secret name,
        both inside the engine. Every broker-specific answer now comes from the
        adapter through the gateway:

          * whether this broker has a stream worth opening at all
          * which instruments (if any) its tick feed subscribes to
          * what credential material its transport needs

        A broker with neither an order stream nor a tick stream opens no
        connection, rather than opening one that will immediately fail.
        """
        streams = broker_gateway.stream_capabilities(broker)
        if not (streams["orders"] or streams["ticks"]):
            logger.debug("Broker %s offers no realtime stream — skipping", broker)
            return

        session = await self.get_session(user_id, broker)
        instrument_tokens = []
        if streams["ticks"]:
            if holdings is None:
                try:
                    holdings = await broker_gateway.get_holdings(broker, session)
                except BrokerError:
                    holdings = []
            if positions is None:
                try:
                    positions = await broker_gateway.get_positions(broker, session)
                except BrokerError:
                    positions = []
            instrument_tokens = broker_gateway.stream_instruments(
                broker, holdings=holdings, positions=positions)

        await stream_manager.start_stream(
            user_id, broker, session,
            credentials=broker_gateway.stream_credentials(broker),
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
        # Sprint R6: also publish on the event bus — the bridge delivers a
        # `broker.order.updated` envelope per-user on the `broker` channel
        # (the Orders tab patches rows live from it). The legacy push above
        # stays one sprint for compatibility.
        try:
            from services.market_engine.event_bus import event_bus
            await event_bus.publish("broker.order.updated", {
                "user_id": str(user_id), "broker": broker, "order": order})
        except Exception as e:
            logger.warning(f"broker.order.updated publish failed: {e}")
        status = order.get("status")
        if status in ("FILLED", "REJECTED", "CANCELLED") and self.db is not None:
            verb = {"FILLED": "executed", "REJECTED": "rejected", "CANCELLED": "cancelled"}[status]
            try:
                from services.notification_service import create_notification
                await create_notification(
                    self.db, user_id,
                    type=f"ORDER_{status}",
                    title=f"Order {verb}",
                    message=f"{order.get('transaction_type', '')} {order.get('quantity', '')} "
                            f"{order.get('symbol', '')} — {verb} on {adapter_display(broker)}."
                            + (f" Reason: {order.get('status_message')}" if status == "REJECTED" and order.get("status_message") else ""),
                    severity="critical" if status == "REJECTED" else "info",
                    symbol=order.get("symbol"),
                    data={"order_id": order.get("order_id"), "broker": broker},
                )
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
        # Sprint R6: the same ticks drive open-trade P&L — recompute this
        # user's trade snapshot and stream `trade.updated` (throttled inside;
        # best-effort, same contract as the portfolio recompute above).
        try:
            from services import trade_stream
            await trade_stream.apply_broker_ticks(self.db, user_id, broker, ticks)
        except Exception as e:
            logger.warning(f"Live trade recompute from {broker} ticks failed: {e}")

    async def _on_stream_expired(self, user_id: str, broker: str):
        self._sessions.pop((user_id, broker), None)
        # The stream task is about to return on its own; drop the registry entry
        # with it (PH3.6). Without this the manager retained a finished
        # BrokerStream — and the expired access token inside its `session` — for
        # the life of the process. `discard` rather than `stop_stream` because we
        # are running inside that task; see BrokerStreamManager.discard.
        stream_manager.discard(user_id, broker)
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
                if not user_id or broker not in broker_registry:
                    continue
                try:
                    session = await self._load_account(user_id, broker)
                    if not session or not session.get("access_token"):
                        continue
                    if not broker_gateway.session_is_fresh(broker, session):
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

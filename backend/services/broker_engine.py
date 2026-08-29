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
from typing import Optional, Sequence

from services.brokers import BrokerCapability, broker_gateway, broker_registry
from services.brokers.base import BrokerAdapter
from services.brokers.crypto import decrypt_token, encrypt_token, is_encrypted
from services.brokers.errors import BrokerAuthError, BrokerError
from services.brokers.instruments import InstrumentMap, canonical_ticks
from services.brokers.market_feed import (
    attach_market_feed,
    detach_market_feed,
    publish_market_ticks,
    set_market_feed_link,
)
from services.brokers.recovery import (
    RecoveryClass,
    RecoveryService,
    recovery_register,
)
from services.brokers.stream import stream_manager
from services.brokers.streaming import DEFAULT_STREAM_CHANNEL, StreamEventKind

logger = logging.getLogger(__name__)

#: Session fields that are SECRETS, and are therefore encrypted at rest and
#: cleared on disconnect.
#:
#: A list of generic session-credential names, not a per-broker registry: an
#: adapter's `exchange_token` decides which of them its broker issues, and a
#: broker that issues none of one simply never sets it. `feed_token` joined the
#: list in D4.9 because a broker whose market feed authenticates with a *second*
#: per-session credential — separate from the token its REST API takes — is an
#: ordinary shape rather than one broker's quirk, and a session credential this
#: engine stored in plaintext would be the one field in `db.broker_accounts`
#: that SECURITY.md's encryption-at-rest rule did not cover.
TOKEN_FIELDS = ("access_token", "refresh_token", "public_token", "feed_token")


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
        #: (user_id, broker) -> InstrumentMap. The account's broker-identifier →
        #: canonical-symbol table (D4.3), rebuilt whenever the account's portfolio
        #: is re-synced rather than expired on a timer: holdings only change
        #: through `sync_portfolio`, so invalidation there is exact, and a TTL
        #: would only add a window in which a correct map is thrown away and
        #: rebuilt from a narrower source.
        self._instrument_maps: dict = {}
        #: D5.6. The paced re-probe that gives a withdrawn feed a way back.
        #: Constructed here rather than at module scope so its three injected
        #: callables bind to *this* engine — the recovery module holds no engine
        #: import, which is what keeps `services.brokers.recovery` free of a
        #: cycle and every branch in it assertable without a database.
        self._recovery = RecoveryService(
            recovery_register,
            attach=self._reattach_channel,
            has_session=self._has_live_session,
            is_attached=self._channel_is_attached,
        )

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
        # D5.6. A new valid session supersedes everything known about the old
        # one, so this is one of exactly two places that clear the re-probe
        # *ladder* as well as the withdrawal (the other is `disconnect`). It is
        # also the mechanism behind ADR-046's auth rule: a feed withdrawn for a
        # dead token becomes attachable again here and nowhere else.
        recovery_register.forget(user_id, broker)
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
        self._forget_instrument_map(user_id, broker)
        # D5.6. The user removed the account; there is nothing left to recover,
        # and leaving a candidate behind would re-probe a broker the user has
        # deliberately detached.
        recovery_register.forget(user_id, broker)
        # The entitlement has ended, so the feed must stop being resolvable now
        # rather than at the next health transition — a broker feed is legally
        # this user's own data (MARKET_DATA_ARCHITECTURE.md, Category 2).
        await detach_market_feed(user_id, broker)
        await self.db.broker_accounts.update_one(
            {"user_id": user_id, "broker": broker},
            {"$set": {**{field: "" for field in TOKEN_FIELDS},
                      "connected": False, "disconnected_at": _now_iso()}})
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
        # D4.3: the instrument map is derived from exactly these rows, so a sync
        # is the moment — and the only moment — it can go stale. Rebuilt from
        # the fetched rows rather than dropped, so the very next tick resolves
        # against the new portfolio instead of triggering a re-read.
        self._remember_instrument_map(user_id, broker, holdings=holdings, positions=positions)
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
                           holdings: list = None, positions: list = None,
                           channels: "Optional[Sequence[str]]" = None):
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
        feed_symbols = ()
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
            # D4.3: the same two lists that decide what to subscribe to also
            # decide what an arriving tick can be named — seed the map from them
            # so the first tick after a reconnect is already resolvable, and so
            # intraday positions (never persisted) are mappable at all.
            instrument_map = self._remember_instrument_map(
                user_id, broker, holdings=holdings, positions=positions)
            feed_symbols = instrument_map.symbols

        # D4.7: one connection per channel the broker declares. A broker whose
        # realtime surface is one socket declares one channel and this loop runs
        # once, which is what it has always done; a broker that serves order
        # updates and market ticks on separate feeds gets both, from the same
        # transport, with no name of its own anywhere in this method.
        credentials = broker_gateway.stream_credentials(broker)
        #: Whether this call actually (re)opened the channel that carries market
        #: ticks (D5.6). Registering the feed below replaces whatever provider
        #: the account already had, so a channel-scoped re-probe of an *order*
        #: socket must not run it: that would discard a live tick feed's
        #: readiness, probation and latency evidence to re-ask a question about
        #: a different channel.
        started_tick_channel = False
        for channel in broker_gateway.stream_channels(broker):
            # D5.6: `channels=None` — every existing caller — opens every
            # channel, byte-identically to before. A re-probe passes the one
            # channel it is recovering, because `BrokerStreamManager.start_stream`
            # stops a channel before replacing it: an account-wide attach would
            # blip a perfectly healthy *order* socket in order to re-ask a
            # question about the market feed.
            if channels is not None and channel.name not in channels:
                continue
            started_tick_channel = started_tick_channel or self._channel_carries_ticks(
                broker, channel.name)
            await stream_manager.start_stream(
                user_id, broker, session,
                credentials=credentials,
                instrument_tokens=instrument_tokens,
                on_order_update=self._on_stream_order,
                on_tick=self._on_stream_tick,
                on_expired=self._on_stream_expired,
                on_not_entitled=self._on_stream_not_entitled,
                on_link_state=self._on_stream_link_state,
                channel=channel.name,
            )
        if streams["ticks"] and started_tick_channel:
            # D4.4: this account's tick stream becomes a registered market-data
            # provider. Best-effort on purpose — the stream itself is already
            # up and driving portfolio and trade P&L, and a provider-registry
            # problem must not take that away. The Market Engine simply does not
            # see the feed until the next stream start.
            #
            # D4.5: the account's canonical instrument universe goes with it.
            # Registration and connection are not evidence a feed can serve, so
            # the provider stays behind the readiness gate until a valid tick
            # arrives on it — the symbols are what it is allowed to become ready
            # *for*.
            try:
                await attach_market_feed(user_id, broker, feed_symbols)
            except Exception as e:
                logger.warning(f"Registering the {broker} market feed failed: {e}")

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

    # -- instrument identity (D4.3) ------------------------------------------
    async def _instrument_map(self, user_id: str, broker: str) -> InstrumentMap:
        """The account's broker-identifier → canonical-symbol table.

        Built from the rows this engine already syncs, so it costs no broker
        call: a canonical holding carries the broker's `instrument_token`, the
        trading symbol and the exchange side by side, which *is* the mapping.

        Cached per account and invalidated on sync/disconnect. An account with
        nothing synced yet gets an empty map, which resolves nothing by token
        and everything a symbol-identified broker sends by symbol — the correct
        answer for both, rather than a guess for either.
        """
        key = (str(user_id), broker)
        cached = self._instrument_maps.get(key)
        if cached is not None:
            return cached
        holdings = []
        if self.db is not None:
            try:
                holdings = await self.db.holdings.find(
                    {"user_id": user_id, "broker": broker}).to_list(1000)
            except Exception as e:
                logger.warning(f"Instrument map load failed for {broker}: {e}")
                holdings = []
        return self._remember_instrument_map(user_id, broker, holdings=holdings)

    def _remember_instrument_map(self, user_id: str, broker: str,
                                 holdings: list = None, positions: list = None) -> InstrumentMap:
        """Rebuild and cache the account's map from rows already in hand.

        `start_stream` and `sync_portfolio` both hold freshly fetched holdings
        *and* positions, and positions are not persisted — so seeding from them
        is the only way an intraday position's ticks are ever mappable. Rebuilt
        wholesale rather than mutated: a stream reading the map must never
        observe a half-updated table.
        """
        instrument_map = InstrumentMap.from_portfolio(holdings, positions)
        self._instrument_maps[(str(user_id), broker)] = instrument_map
        return instrument_map

    def _forget_instrument_map(self, user_id: str, broker: str) -> None:
        self._instrument_maps.pop((str(user_id), broker), None)

    async def _on_stream_tick(self, user_id: str, broker: str, ticks: list):
        """Broker ticks arrive here as `BrokerTick` dicts and leave canonical.

        This is the D4.3 boundary. Everything below it — the app WebSocket, the
        live portfolio recompute, the open-trade recompute — receives
        `MarketTick` dicts keyed by canonical symbol and never sees the broker's
        instrument identifier. A tick whose instrument this account cannot name
        is dropped inside `canonical_ticks`; a batch that yields nothing stops
        here rather than waking two recomputes that would find nothing to do.
        """
        # D5.6: market data arriving on this account's feed is the evidence
        # that discharges an outstanding recovery candidate, and it is taken
        # *before* canonical mapping deliberately. The question a re-probe asks
        # is whether the account may consume this feed at all, and a broker
        # frame carrying market data answers it — an account whose holdings this
        # process cannot yet name is entitled all the same. Readiness is a
        # different question, asked further down and answered only by a valid
        # canonical tick reaching the provider; recovery never shortcuts it.
        if ticks:
            recovery_register.discharge(user_id, broker)
        instrument_map = await self._instrument_map(user_id, broker)
        ticks = canonical_ticks(ticks, instrument_map, broker=broker)
        if not ticks:
            return
        # D4.4: the same canonical batch enters the Market Gateway through this
        # account's registered provider, which is what makes it *market* data
        # rather than portfolio input — the gateway stamps the tier, the Source
        # Manager learns the feed is live, and the Event Bus fans it out. No
        # conversion happens here: it is the identical list, and a second
        # conversion path is a second place for two shapes to drift.
        try:
            await publish_market_ticks(user_id, broker, ticks)
        except Exception as e:
            logger.warning(f"Market feed publish from {broker} ticks failed: {e}")
        await self._push(user_id, {"type": "broker_price_tick", "data": {
            "broker": broker, "ticks": ticks}})
        # Sprint R5: broker ticks drive the live portfolio — recompute this
        # user's P&L/allocation server-side and stream `portfolio.updated`
        # (throttled inside; best-effort so a recompute error never breaks
        # the tick forward above).
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

    async def _on_stream_link_state(self, user_id: str, broker: str, up: bool, reason: str = "",
                                    channel: str = None):
        """The broker transport's connection came up or went down (D4.5).

        Relayed to this account's market-data provider, which is where the
        make-before-break gate lives. Nothing is decided here: a lost link
        demotes the feed below the baseline on the very next resolution, and a
        restored one puts it back at the start of the gate to re-earn readiness
        from a fresh tick.

        ONLY THE TICK-CARRYING CHANNEL DRIVES THE FEED (D4.7)
        ------------------------------------------------------
        A broker may now hold several connections for one account, and they fail
        independently. Relaying every channel's link state to the market feed
        would let a broker's *order* socket demote a market feed that is
        delivering prices perfectly well — a promoted feed dropped to the
        delayed baseline because an unrelated connection blinked — and, worse,
        let that same order socket re-arm the readiness gate for a tick feed
        that is not connected at all.

        Which channel that is comes from the channel's own declaration, not from
        a broker name: whichever one says it delivers ticks. A single-channel
        broker's one channel carries them, so this is a no-op for it.

        Best-effort, like every other market-feed call on this path — the stream
        itself is driving live portfolio and trade P&L, and a provider
        bookkeeping error must never cost the user that.
        """
        if not self._channel_carries_ticks(broker, channel):
            return
        try:
            await set_market_feed_link(user_id, broker, up=up, reason=reason)
        except Exception as e:
            logger.warning(f"Market feed link update from {broker} failed: {e}")

    def _channel_carries_ticks(self, broker: str, channel: str = None) -> bool:
        """Whether `channel` is the one this broker's market ticks arrive on.

        `None` means the caller did not say — a stream started before channels
        existed, or a test double. Treated as "yes", which preserves the
        pre-D4.7 behaviour for anything that has not been told about channels
        and keeps the failure direction safe: an unknown channel that is in fact
        the tick feed still demotes on link loss.
        """
        if channel is None:
            return True
        try:
            for declared in broker_gateway.stream_channels(broker):
                if declared.name == channel:
                    return StreamEventKind.TICKS in declared.delivers
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Could not resolve {broker} stream channel {channel}: {e}")
            return True
        return False

    async def _on_stream_expired(self, user_id: str, broker: str, channel: str = None):
        """A broker reported this account's token dead on one of its channels.

        The token is the account's, not the channel's, so every channel of this
        broker is finished — the others are reconnecting into the same rejection
        right now. The one that reported it is `discard`ed (we are inside its
        task; see `BrokerStreamManager.discard`) and the rest are stopped
        properly, which cancels their tasks rather than merely forgetting them.
        """
        self._sessions.pop((user_id, broker), None)
        # D5.6. Recorded, and recorded as SESSION rather than merely left out:
        # an expired token must be *visibly* excluded from re-probe rather than
        # absent from the register, so the exclusion is a fact a test can read
        # and a mutation can break. Retrying a dead credential on a schedule is
        # a login attempt on a timer, which is how an account gets locked rather
        # than how a feed comes back. It also *replaces* any REPROBE candidate
        # this account already had: the strictly stronger condition is the later
        # one, so an entitlement re-probe stops the moment the token dies.
        recovery_register.reclassify(user_id, broker, RecoveryClass.SESSION)
        recovery_register.record_withdrawal(
            user_id, broker, channel or DEFAULT_STREAM_CHANNEL, RecoveryClass.SESSION)
        # A dead token means the feed cannot deliver another tick. Unregistering
        # it is what stops the Source Manager resolving a priority-1 streaming
        # provider that can only answer with silence; the baseline below it then
        # serves the TICKS capability's absence honestly.
        await detach_market_feed(user_id, broker)
        # The stream task is about to return on its own; drop the registry entry
        # with it (PH3.6). Without this the manager retained a finished
        # BrokerStream — and the expired access token inside its `session` — for
        # the life of the process. `discard` rather than `stop_stream` because we
        # are running inside that task; see BrokerStreamManager.discard.
        stream_manager.discard(user_id, broker, channel)
        # The remaining channels are separate tasks, so stopping them here is
        # safe — the calling channel has just been removed from the registry, so
        # this cannot await the task it is running inside.
        await stream_manager.stop_stream(user_id, broker)
        await self._audit(user_id, "broker.session.expired", {"broker": broker})
        await self._push(user_id, {"type": "broker_status", "data": {
            "broker": broker, "connected": False, "session_expired": True}})

    async def _on_stream_not_entitled(self, user_id: str, broker: str, channel: str = None):
        """A broker refused this account the data one of its channels carries (D5.5).

        WHY THIS IS NOT `_on_stream_expired` WITH A DIFFERENT MESSAGE
        --------------------------------------------------------------
        Everything that method does is wrong here. The session is **valid**: the
        account can still fetch its portfolio, place orders and receive order
        updates, so dropping the cached session, stopping every channel and
        telling the user their login expired would destroy working functionality
        on the strength of a statement the broker did not make. What the broker
        said is narrower — this account may not consume *this feed* — and the
        response is exactly as narrow.

        Three things happen, and the list is deliberately short:

        * **the account's market feed stops being resolvable**, when the refused
          channel is the one carrying ticks. `detach_market_feed` unregisters the
          provider, so the very next resolution ranks the baseline first again —
          and it does so regardless of whether the feed was READY, STABLE or
          primary, because an unregistered provider is not a candidate at all.
          There is no state in which a provider that has lost its entitlement
          stays selected;
        * **the finished stream leaves the registry.** `discard` rather than
          `stop_stream`, because this runs inside that stream's own task
          (`BrokerStreamManager.discard`), and leaving it behind would retain the
          account's session dict for the life of the process;
        * **it is recorded.** An audit row, and the user-scoped `provider.status`
          the unregistration already publishes through the Market Gateway.

        Everything else is left alone on purpose: the session cache, this
        broker's other channels, every other broker, every other user, and the
        guest baseline. A second user of the same broker is a different
        `BrokerStream` with a different provider, and nothing here can reach it.

        The channel gate is the same one `_on_stream_link_state` uses and is
        asked for the same reason: an entitlement refused on an *order* channel
        says nothing about the market feed, and detaching it would demote a feed
        that is delivering prices perfectly well.
        """
        if self._channel_carries_ticks(broker, channel):
            await detach_market_feed(user_id, broker)
        stream_manager.discard(user_id, broker, channel)
        # D5.6, and the whole of what this sprint adds to this method. The
        # refusal stays exactly as terminal as D5.5 made it — the loop does not
        # reconnect and nothing here restarts it — but the withdrawal is now
        # *recorded*, so a paced re-probe can later ask the one question a
        # reconnect could not: has this account's entitlement changed? See
        # ADR-046. The class is REPROBE because an entitlement is a
        # provider-level condition a person can grant without this process being
        # told; it is not a credential problem and not a transport problem.
        recovery_register.record_withdrawal(
            user_id, broker, channel or DEFAULT_STREAM_CHANNEL, RecoveryClass.REPROBE)
        await self._audit(user_id, "broker.feed.entitlement_denied", {"broker": broker})

    # -- provider recovery (D5.6) ----------------------------------------------------------------------
    def _has_live_session(self, user_id: str, broker: str) -> bool:
        """Whether this account has a session a re-probe could attach with.

        Reads the engine's own session cache and nothing else. It is deliberately
        not a *freshness* check and not a broker call: `get_session` would hit
        the database and possibly the broker on a background sweep, and the
        authority on whether a token still works is the attach attempt itself —
        which reports `AUTH_EXPIRED` through the path that already exists and
        reclassifies the candidate out of re-probe entirely.

        What this does guarantee is the rule ADR-046 rests on: every path that
        invalidates a session (`disconnect`, `_on_stream_expired`) pops this map
        first, so a candidate whose session went away after it was recorded
        cannot be attempted.
        """
        return bool(self._sessions.get((user_id, broker)))

    def _channel_is_attached(self, user_id: str, broker: str, channel: str) -> bool:
        """Whether a stream is already running for this exact channel.

        Asked so a re-probe never replaces a live connection. `start_stream`
        stops a channel before opening it, so an unguarded sweep would tear down
        a feed that a user reconnect or a session restore had already brought
        back — recovering a feed by breaking it.
        """
        return any(
            row["running"]
            for row in stream_manager.status()
            if row["user_id"] == user_id and row["broker"] == broker
            and row["channel"] == channel
        )

    async def _reattach_channel(self, user_id: str, broker: str, channel: str):
        """One ordinary attach of one channel — the whole of what a re-probe does.

        There is no probe-only path: this is `start_stream` scoped to a single
        channel, so a recovered feed travels the identical route a first
        attachment does — connect, subscribe, first valid canonical tick, READY,
        probation, stability, latency. A control-plane "yes" would have proved
        something the platform does not accept as evidence (ADR-046).
        """
        await self.start_stream(user_id, broker, channels=(channel,))

    def start_recovery(self):
        """Begin the bounded background re-probe sweep. Idempotent.

        Started from the same place session restore is, and it is the only timer
        this sprint adds. A sweep with an empty register performs no I/O: it
        reads two dictionaries and goes back to sleep, so a deployment where
        nothing has ever been refused pays a dictionary lookup a minute.
        """
        return self._recovery.start()

    async def stop_recovery(self):
        await self._recovery.stop()

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
                    # DB-2 (D4.1). A restored session IS a live broker
                    # connection and must produce the same lifecycle event a
                    # fresh connect does. The Source Manager's per-user
                    # connected-broker registry is built only from these events,
                    # so without this a restart left it empty while a broker
                    # socket was running underneath — and every restored user
                    # silently stayed on the baseline feed until some other
                    # traffic happened to exercise their session.
                    #
                    # Published before the stream starts, deliberately: the
                    # connection is a fact about the session, not about whether
                    # a socket opened. A broker with no stream at all still has
                    # a connection worth recording.
                    #
                    # Best-effort — `_publish_connection` contains its own
                    # try/except, because a market-data listener failing must
                    # never fail a session restore.
                    await self._publish_connection(user_id, broker, connected=True)
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
        # D5.6. Session restore is the natural place: it is the point at which
        # this process knows which accounts exist, and it already runs exactly
        # once per startup. Idempotent, so a second call adds no second sweeper.
        self.start_recovery()
        return restored

    async def shutdown(self):
        await self.stop_recovery()
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

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
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Sequence

from pymongo import ReturnDocument

from services.brokers import BrokerCapability, broker_gateway, broker_registry
from services.brokers.accounts import (
    BrokerAccountRef,
    BrokerAccountStatus,
    broker_accounts,
)
from services.brokers.base import BrokerAdapter
from services.brokers.crypto import decrypt_token, encrypt_token, is_encrypted
from services.brokers.errors import BrokerAuthError, BrokerError
from services.brokers.instruments import InstrumentMap, canonical_ticks
from infrastructure.mongo_errors import is_duplicate_key
from services.brokers.market_feed import (
    # D5.13 — the Market Engine's consumer-facing transition vocabulary,
    # re-exported by the seam that already owns this layer's contact with it.
    # Importing it from `market_engine.source_manager` here would give the
    # broker engine a second, direct dependency on the engine's internals for
    # no gain; `market_feed` is the one module allowed to know both sides.
    FeedChangeReason,
    attach_market_feed,
    detach_market_feed,
    publish_market_ticks,
    set_market_feed_link,
)
from services.brokers.feed_universe import (
    build_feed_universe, dashboard_symbols, index_instruments,
)
from services.brokers.recovery import (
    RecoveryClass,
    RecoveryService,
    recovery_register,
)
from services.brokers.sharding import DEFAULT_SHARD_ID, InstrumentShard, plan_shards
from services.brokers.stream import stream_manager
from services.brokers.streaming import DEFAULT_STREAM_CHANNEL, StreamEventKind

logger = logging.getLogger(__name__)


def _bind_account(handler, account: "BrokerAccountRef", shard: str):
    """A stream callback that already knows which account and connection it serves.

    The transport's callback contract is unchanged — it still calls back with
    `(user_id, broker, ...)`, which is all a socket knows about itself. What the
    *engine* needs is the `broker_account_id`, and D6.4 binds it here for exactly
    the reason D5.10 binds the shard here (see :func:`_bind_shard`): the engine is
    what opened the connection, so it knows which account it opened it for, and
    binding the answer costs one closure instead of a signature change through
    `stream.py`, every adapter transport and every test double.

    The bound `(user_id, broker)` pair the transport supplies is deliberately
    **discarded** rather than trusted: the account ref already carries both, it
    was resolved through the owner-scoped directory, and a transport that reported
    a different pair would be reporting an identity it has no authority over.
    """
    from functools import partial

    async def callback(_user_id, _broker, *args, **kwargs):
        return await handler(account, *args, shard=shard, **kwargs)

    # `partial` is not used for the account/shard binding itself (the wrapper has
    # to absorb the transport's two leading positionals), but keeping the name
    # here documents that this is the same binding technique D5.10 introduced.
    del partial
    return callback


def _bind_shard(handler, shard: str):
    """A stream callback that already knows which connection it belongs to (D5.10).

    WHY THE SHARD IS BOUND HERE RATHER THAN CARRIED BY THE TRANSPORT
    ------------------------------------------------------------------
    The obvious alternative is to widen the transport's callbacks —
    `on_tick(user, broker, ticks, shard)` — which is what D4.7 did for the
    channel. It is the wrong trade this time, for two reasons that point the
    same way:

    * the transport would have to *know about shards*. It currently counts a
      list of opaque identifiers and hands it back; a shard is subscription
      policy, and a transport that reported one would be a transport that knew
      how subscriptions are planned. Bound here, `stream.py` gains a dictionary
      key and a log label and nothing else — which is the D5.10 result rather
      than an accident of it.
    * every existing callback signature, in this engine and in every test
      double, would move. D4.7 and D4.10 both refused that trade for the same
      reason (`BrokerStreamChannel.open`, `AdapterStreamChannel`): a signature
      that moves under an unmigrated implementation fails on a live socket
      rather than at import.

    The engine is the right owner because the engine is what *built the plan*.
    It knows which shard it is opening at the moment it opens it, so binding the
    answer costs one partial application and no contract change anywhere.
    """
    from functools import partial

    return partial(handler, shard=shard)


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


#: How long a decrypted broker session may sit unused in `BrokerEngine._sessions`
#: before its plaintext copy is dropped (D6.7 / A5). Thirty minutes is longer
#: than any burst of user activity and far shorter than a broker session's
#: lifetime, so an active account is never evicted and an idle one does not keep
#: a plaintext credential resident for the rest of the trading day.
SESSION_CACHE_IDLE_SECONDS = 1800

#: How often the sweeper wakes. Well below the idle threshold, so an eligible
#: entry is dropped within a few minutes rather than up to a full threshold late.
CREDENTIAL_SWEEP_INTERVAL_SECONDS = 300


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
        self._db = None
        self.db = None
        self.ws_push = None            # async (user_id, message) -> None
        #: broker_account_id -> decrypted session dict.
        #:
        #: Keyed by the account and not by `(user_id, broker)` since D6.4. The
        #: pair was never an account identity: two Zerodha accounts belonging to
        #: one user collided on it, and the second connect silently took the
        #: first's cached credentials with it. A `broker_account_id` already
        #: implies its user and its broker, so this is one key where there were
        #: two, and there is no longer a key a second account can land on.
        self._sessions: dict = {}
        #: broker_account_id -> monotonic timestamp of the last touch (D6.7 / A5).
        #:
        #: WHY THE PLAINTEXT CACHE IS NOW BOUNDED IN TIME
        #: ----------------------------------------------
        #: `_sessions` holds broker access tokens **decrypted**. At rest they are
        #: Fernet-encrypted; in this dict they are not, and before D6.7 they were
        #: evicted only by an explicit disconnect, an observed expiry or a
        #: process restart. A long-lived process therefore accumulated the
        #: plaintext credential of every account that had ever been touched, for
        #: as long as it ran — including accounts whose token the broker had
        #: already killed at its daily cut-off, and whose owner had logged out
        #: hours earlier.
        #:
        #: This is a blast-radius control, not an authorization control. Nothing
        #: was reachable through the cache that was not reachable without it:
        #: `get_session` re-derives freshness on every call and refuses an
        #: expired session regardless of what is cached, so a stale entry could
        #: never be *used*. What it could do is sit in a heap dump, a core file
        #: or a debugger long after it had any business existing.
        #:
        #: The lifetime is deliberately independent of the broker's own session
        #: expiry. Those run to a daily cut-off — up to 24 hours away, per D6.6
        #: §6 — so keying eviction on them would keep an idle account's plaintext
        #: token resident for most of a day. This is an *idle* timeout: an
        #: account in active use re-touches on every call and is never evicted,
        #: and an account that goes quiet loses its plaintext copy while keeping
        #: its encrypted one, so the next call simply reloads and decrypts it.
        #: The only cost of an eviction is one `find_one`.
        self._session_touched: dict = {}
        #: broker_account_id -> InstrumentMap. The account's broker-identifier →
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
        #: The credential-cache sweeper's task (D6.7), held here for the reason
        #: `asyncio` documents: it keeps only a weak reference to a running task.
        self._credential_sweeper = None
        self._recovery = RecoveryService(
            recovery_register,
            attach=self._reattach_channel,
            has_session=self._has_live_session,
            is_attached=self._channel_is_attached,
        )

    # -- wiring -----------------------------------------------------------------
    #
    # `db` is a property, not a plain attribute, and the setter is the reason:
    # the account directory reads the same collection this engine writes, and it
    # must never be pointed at a different database than the engine. Assigning
    # `engine.db` — which `configure`, the app startup and every test that swaps
    # in a double all do — repoints both, so there is no way to end up with an
    # engine on one database and a directory on another.
    @property
    def db(self):
        return self._db

    @db.setter
    def db(self, value):
        self._db = value
        broker_accounts.configure(value)

    def configure(self, db, ws_push=None):
        self.db = db
        self.ws_push = ws_push

    # -- account identity (D6.4) -------------------------------------------------
    async def resolve_account(self, user_id, broker_account_id) -> BrokerAccountRef:
        """The owner-scoped account for a client-supplied id.

        Every request that names an account passes through here. Raises
        `UnknownBrokerAccount` for an id that does not exist and for one that
        belongs to somebody else — the same exception, because the caller must
        not be able to tell those apart.
        """
        return await broker_accounts.resolve(user_id, broker_account_id)

    async def list_accounts(self, user_id) -> list:
        """Every brokerage account this user has authorized."""
        return await broker_accounts.list_for_user(user_id)

    async def account_for_broker(self, user_id, broker) -> Optional[BrokerAccountRef]:
        """This user's single account at `broker`, or None.

        The compatibility bridge for the broker-addressed routes that predate
        D6.4 and for rows (trades, orders) that recorded a broker name and no
        account. It raises `AmbiguousBrokerAccount` rather than choosing when the
        user holds more than one: the forbidden semantics are "first connected",
        "last connected" and "any connected", and refusing is the only remaining
        honest answer.
        """
        return await broker_accounts.sole_for_broker(user_id, broker)

    def adapter(self, broker: str) -> BrokerAdapter:
        """DEPRECATED — the registered adapter for `broker`.

        Kept for tests that patch adapter methods directly (the legacy
        single-session `zerodha_service` shim that was its other consumer is
        deleted — D6.1 / S3). The engine itself no longer
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

    def _activity(self, user_id: str, message: str):
        """Append a PRIVATE activity entry owned by `user_id` (D6.1 / S4).

        Every message this method carries is about one account — an order that
        was placed, a portfolio that was synced, a broker that was connected.
        These were previously written to the process-global feed and broadcast
        to every socket, which is how "Order placed on Zerodha: BUY 10 RELIANCE"
        became readable by other users and by anonymous callers.
        """
        try:
            from services.activity_logger import log_activity
            log_activity(message, "monitor", "done", user_id=user_id)
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

    async def _save_session(self, account: BrokerAccountRef, session: dict):
        """Store this account's encrypted session; refresh the cache.

        Addressed by `broker_account_id`, so a second account at the same broker
        writes its own row instead of overwriting the first's. It never *creates*
        a row: the account is minted by `BrokerAccountDirectory.link` before any
        credential exists, which is what stops an OAuth exchange from bringing an
        account into being as a side effect of storing a token.
        """
        doc = self._encrypt_doc(session)
        doc.update({
            "user_id": account.user_id,
            "broker": account.broker,
            "broker_account_id": account.broker_account_id,
            "connected": True,
            "status": BrokerAccountStatus.CONNECTED,
        })
        doc.setdefault("connected_at", _now_iso())
        doc["last_refresh"] = _now_iso()
        doc["updated_at"] = _now_iso()
        await self.db.broker_accounts.update_one(
            {"broker_account_id": account.broker_account_id}, {"$set": doc}, upsert=True)
        self._cache_session(account.broker_account_id, dict(session))

    async def _load_session(self, account: BrokerAccountRef) -> Optional[dict]:
        doc = await self.db.broker_accounts.find_one(
            {"broker_account_id": account.broker_account_id})
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
                    session["expires_at"] = broker_gateway.session_expiry(
                        account.broker, connected).isoformat()
                except Exception:
                    pass
            await self.db.broker_accounts.update_one(
                {"broker_account_id": account.broker_account_id},
                {"$set": self._encrypt_doc({f: session.get(f, "") for f in TOKEN_FIELDS})
                 | ({"expires_at": session["expires_at"]} if session.get("expires_at") else {})})
            logger.info(f"Migrated plaintext {account.broker} tokens to encrypted "
                        f"storage for account {account.broker_account_id}")
        return session

    def _cache_session(self, broker_account_id: str, session: dict) -> None:
        """Put a decrypted session in the cache and mark it touched.

        Every write to `_sessions` goes through here so the touch map cannot
        drift from the cache it describes — an entry with no timestamp would be
        invisible to the sweeper and would live forever, which is the exact
        condition this pair exists to remove.
        """
        self._sessions[broker_account_id] = session
        self._session_touched[broker_account_id] = time.monotonic()

    def _forget_session(self, broker_account_id: str) -> None:
        """Drop a cached session and its timestamp together."""
        self._sessions.pop(broker_account_id, None)
        self._session_touched.pop(broker_account_id, None)

    def evict_idle_sessions(self, *, idle_seconds: int = SESSION_CACHE_IDLE_SECONDS) -> int:
        """Drop the plaintext copy of every session idle for `idle_seconds`.

        Returns how many were evicted. Safe to call at any time and from any
        path: an evicted account's encrypted row is untouched, so the next
        `get_session` reloads and decrypts it and the user notices nothing.

        An entry with no recorded touch is treated as idle rather than as fresh.
        That is the fail-safe direction — the cost of evicting something that is
        actually in use is one database read, and the cost of keeping something
        that should have gone is the retention this method exists to bound.
        """
        now = time.monotonic()
        stale = [key for key in list(self._sessions)
                 if now - self._session_touched.get(key, 0.0) > idle_seconds]
        for key in stale:
            self._forget_session(key)
        if stale:
            logger.info("Evicted %d idle broker session(s) from the in-memory "
                        "cache; their encrypted rows are untouched.", len(stale))
        return len(stale)

    async def get_session(self, account: BrokerAccountRef) -> dict:
        """Return a live (fresh) decrypted session for ONE account, or raise.

        Takes a resolved account rather than `(user_id, broker)` since D6.4. The
        argument is the authorization: a `BrokerAccountRef` can only be obtained
        from the owner-scoped directory, so there is no way to reach a session
        from a broker name and a hope.
        """
        key = account.broker_account_id
        broker = account.broker
        adapter = broker_registry.require(broker)
        session = self._sessions.get(key) or await self._load_session(account)
        if not session or not session.get("access_token"):
            raise BrokerAuthError(f"{adapter.display_name} is not connected. "
                                  "Connect your account in Settings.")
        if broker_gateway.session_is_fresh(broker, session):
            self._cache_session(key, session)
            return session
        # The gateway answers None both for "this broker has no refresh grant"
        # and for "the refresh failed"; the engine's response is the same either
        # way, which is why it does not need to tell them apart.
        refreshed = await broker_gateway.refresh_session(broker, session)
        if refreshed and refreshed.get("access_token"):
            await self._save_session(account, {**session, **refreshed})
            await self._audit(account.user_id, "broker.token.refreshed", {
                "broker": broker, "broker_account_id": key})
            return self._sessions[key]
        self._forget_session(key)
        await broker_accounts.set_status(key, BrokerAccountStatus.REAUTH_REQUIRED)
        raise BrokerAuthError(f"{adapter.display_name} session expired. Please reconnect from Settings.")

    # -- authentication flows -----------------------------------------------------------
    def parse_callback_params(self, broker: str, params: dict) -> Optional[dict]:
        """The `exchange_token` payload for a broker's OAuth redirect, or None
        when the user cancelled."""
        return broker_gateway.parse_callback_params(broker, params)

    def get_login_url(self, broker: str, state: str = None) -> dict:
        """The broker's browser login URL for a flow identified by ``state``.

        ``state`` is the opaque handle minted by ``security.oauth_state`` and
        held in a server-side record bound to the initiating user. The engine
        never puts a user id on the wire (D6.1 / S1).
        """
        return broker_gateway.login_url(broker, state=state)

    async def complete_auth(self, broker: str, user_id: str, auth_payload: dict) -> dict:
        """Exchange the OAuth callback payload and bind it to the right account.

        THE ORDER HERE IS THE WHOLE OF D6.4's LINKING RULE (§8)
        --------------------------------------------------------
        The token exchange comes FIRST, and the account is resolved from what the
        *broker* said in its authenticated response — never from a
        `broker_account_id` the client supplied, never from the OAuth state, and
        never from "this user's zerodha account". Only then are credentials
        stored, against the account that exchange identified.

        That ordering is what makes reconnect idempotent and a second account
        possible in the same method: the same external identity resolves to the
        same `broker_account_id` and updates it; a different one mints a new
        account beside it. Neither outcome required this method to choose.

        Fails closed on an unidentifiable account: a broker that returns no
        account identity leaves `external_account_id` null, and the directory
        will link at most ONE such account per broker rather than treating every
        anonymous login as a fresh account. Every adapter in this repository
        returns one.
        """
        adapter = broker_registry.require(broker)
        session = await broker_gateway.exchange_token(broker, auth_payload)
        account = await broker_accounts.link(
            user_id, broker, session.get("account_id"),
            display_name=(session.get("profile") or {}).get("user_name"),
            capabilities=sorted(c.value for c in adapter.capabilities),
        )
        await self._save_session(account, session)
        await self._audit(user_id, "broker.connected", {
            "broker": broker, "broker_account_id": account.broker_account_id,
            "account_id": session.get("account_id")})
        self._activity(user_id, f"{adapter.display_name} account connected — live broker session active")
        await self._push(user_id, {"type": "broker_status", "data": {
            "broker": broker, "broker_account_id": account.broker_account_id,
            "connected": True}})
        await self._publish_connection(account, connected=True)
        # D5.6. A new valid session supersedes everything known about the old
        # one, so this is one of exactly two places that clear the re-probe
        # *ladder* as well as the withdrawal (the other is `disconnect`). It is
        # also the mechanism behind ADR-046's auth rule: a feed withdrawn for a
        # dead token becomes attachable again here and nowhere else.
        recovery_register.forget(account.broker_account_id)
        # Initial sync + stream are best-effort: connection succeeds even if
        # the first sync hits a transient broker error.
        sync_result = None
        try:
            sync_result = await self.sync_portfolio(account)
        except Exception as e:
            logger.warning(f"Initial {broker} sync failed after connect: {e}")
            try:
                await self.start_stream(account)
            except Exception as se:
                logger.warning(f"Stream start failed after connect: {se}")
        return {"success": True, "broker": broker,
                "broker_account_id": account.broker_account_id,
                "account": account.public_dict(),
                "profile": session.get("profile", {}), "sync": sync_result}

    async def disconnect(self, account: BrokerAccountRef) -> dict:
        """Detach ONE brokerage account. Its identity and history survive.

        Disconnect clears credentials and stops every connection; it does not
        delete the `broker_account_id`, which is what lets the same external
        account reconnect onto the same id with its orders and holdings still
        attached (D6.4 / §16).
        """
        broker, user_id = account.broker, account.user_id
        adapter = broker_registry.require(broker)
        await stream_manager.stop_stream(account.broker_account_id)
        session = self._sessions.get(account.broker_account_id) or await self._load_session(account)
        if session and session.get("access_token"):
            # Capability-gated and best-effort inside the gateway: a broker with
            # no logout endpoint is not an error, and a broker that refuses to
            # log out an already-dead token must not fail the user's disconnect.
            # This replaced a `hasattr(adapter, "invalidate_session")` probe —
            # a duck-typing check that could not distinguish "this broker cannot
            # revoke tokens" from "someone renamed the method".
            await broker_gateway.invalidate_session(broker, session)
        self._forget_session(account.broker_account_id)
        self._forget_instrument_map(account)
        # D5.6. The user removed the account; there is nothing left to recover,
        # and leaving a candidate behind would re-probe a broker the user has
        # deliberately detached.
        recovery_register.forget(account.broker_account_id)
        # The entitlement has ended, so the feed must stop being resolvable now
        # rather than at the next health transition — a broker feed is legally
        # this user's own data (MARKET_DATA_ARCHITECTURE.md, Category 2).
        # D5.13 — the owner's tier is about to drop to the baseline; say why.
        # Nothing is wrong here and the reason says so: the user removed the
        # account, and the feed is not coming back until they reconnect it.
        await detach_market_feed(
            account, change_reason=FeedChangeReason.FEED_DISCONNECTED)
        await self.db.broker_accounts.update_one(
            {"broker_account_id": account.broker_account_id},
            {"$set": {**{field: "" for field in TOKEN_FIELDS},
                      "connected": False,
                      "status": BrokerAccountStatus.DISCONNECTED,
                      "updated_at": _now_iso(),
                      "disconnected_at": _now_iso()}})
        await self._audit(user_id, "broker.disconnected", {
            "broker": broker, "broker_account_id": account.broker_account_id})
        self._activity(user_id, f"{adapter.display_name} account disconnected")
        await self._push(user_id, {"type": "broker_status", "data": {
            "broker": broker, "broker_account_id": account.broker_account_id,
            "connected": False}})
        await self._publish_connection(account, connected=False)
        return {"success": True, "broker": broker,
                "broker_account_id": account.broker_account_id}

    async def _publish_connection(self, account: BrokerAccountRef, *, connected: bool):
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
            adapter = broker_registry.require(account.broker)
            await event_bus.publish(
                "broker.connected" if connected else "broker.disconnected",
                {
                    "user_id": account.user_id,
                    "broker": account.broker,
                    # D6.4 — the event names the account, not just the brand. A
                    # consumer that tracks "this user's connected brokers" was
                    # previously told the same thing twice when a user held two
                    # accounts at one broker, and told the broker had gone when
                    # only one of them disconnected.
                    "broker_account_id": account.broker_account_id,
                    "capabilities": sorted(c.value for c in adapter.capabilities),
                },
            )
        except Exception as e:
            logger.warning(f"broker lifecycle event publish failed for {account.broker}: {e}")

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
        accounts = await self.account_statuses(user_id)
        #: broker -> the accounts this user holds there, in directory order.
        by_broker: dict = {}
        for entry in accounts:
            by_broker.setdefault(entry["broker"], []).append(entry)

        statuses = {}
        for broker in broker_registry.names():
            here = by_broker.get(broker) or []
            if len(here) == 1:
                # The unambiguous case, which is every account this platform has
                # ever had and stays the common one. The per-broker view is
                # exactly the account's own status.
                statuses[broker] = dict(here[0])
                continue
            if not here:
                connection = broker_gateway.connection(
                    user_id=str(user_id), broker=broker, session=None, streaming=False)
                statuses[broker] = {
                    **connection.as_dict(), "profile": {},
                    "message": self._connection_message(connection),
                    "broker_account_id": None, "accounts": [],
                }
                continue
            # D6.4 — several accounts at one broker. The per-broker view CANNOT
            # answer "is your Zerodha connected" with one boolean any more, and
            # inventing one would be the "any connected" semantics this sprint
            # exists to remove. It reports the aggregate honestly and carries
            # every account, so a caller that needs a specific one has the ids to
            # ask with. `broker_account_id` is null precisely because there is no
            # single answer.
            connected = [a for a in here if a.get("connected")]
            statuses[broker] = {
                **{k: v for k, v in here[0].items()
                   if k not in ("broker_account_id", "profile", "message",
                                "connected", "session_expired", "streaming",
                                "account_id", "connected_at", "last_sync")},
                "connected": bool(connected),
                "streaming": any(a.get("streaming") for a in here),
                "session_expired": bool(here) and not connected,
                "broker_account_id": None,
                "ambiguous": True,
                "account_count": len(here),
                "accounts": here,
                "profile": {},
                "message": (f"{len(here)} accounts connected"
                            if connected else f"{len(here)} accounts"),
            }
        return statuses

    async def account_statuses(self, user_id) -> list:
        """One status record per authorized brokerage account (D6.4).

        The account-addressed replacement for `get_status`, which could only ever
        describe one account per broker. Every record names its
        `broker_account_id`, which is what a client passes back to address it.

        Carries no credential: the session is read to answer "is it live" and
        "when does it expire", and only the `BrokerConnection` contract — which
        holds no token by construction — leaves this method.
        """
        if self.db is None:
            return []
        refs = await broker_accounts.list_for_user(user_id)
        live = stream_manager.status()
        out = []
        for ref in refs:
            doc = await self.db.broker_accounts.find_one(
                {"broker_account_id": ref.broker_account_id})
            session = self._decrypt_doc(doc) if doc else None
            streaming = any(row["running"] for row in live
                            if row["broker_account_id"] == ref.broker_account_id)
            connection = broker_gateway.connection(
                user_id=ref.user_id, broker=ref.broker,
                session=session, streaming=streaming)
            out.append({
                **connection.as_dict(),
                "broker_account_id": ref.broker_account_id,
                "external_account_id": ref.external_account_id,
                "status": ref.status,
                "profile": (session.get("profile") or {}) if connection.connected else {},
                "message": self._connection_message(connection),
            })
        return out

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
    # Every one takes a resolved `BrokerAccountRef`. There is no overload that
    # takes a broker name: a caller who has only a name has not yet answered
    # "which account", and the only place that question may be answered is
    # `account_for_broker`, which refuses when it is ambiguous.
    async def get_profile(self, account: BrokerAccountRef) -> dict:
        return await broker_gateway.get_profile(account.broker, await self.get_session(account))

    async def get_holdings(self, account: BrokerAccountRef) -> list:
        return await broker_gateway.get_holdings(account.broker, await self.get_session(account))

    async def get_positions(self, account: BrokerAccountRef) -> list:
        return await broker_gateway.get_positions(account.broker, await self.get_session(account))

    async def get_funds(self, account: BrokerAccountRef) -> dict:
        return await broker_gateway.get_funds(account.broker, await self.get_session(account))

    async def get_margins(self, account: BrokerAccountRef) -> dict:
        return await broker_gateway.get_margins(account.broker, await self.get_session(account))

    async def get_orders(self, account: BrokerAccountRef) -> list:
        return await broker_gateway.get_orders(account.broker, await self.get_session(account))

    async def get_trades(self, account: BrokerAccountRef) -> list:
        return await broker_gateway.get_trades(account.broker, await self.get_session(account))

    # -- portfolio sync ---------------------------------------------------------------------
    async def _next_sync_generation(self, user_id: str, account_id: str) -> int:
        """The next strictly-increasing sync generation for ONE account.

        Issued by `$inc` on the account's portfolio row rather than read from a
        clock: two syncs starting in the same millisecond would draw the same
        timestamp, and a generation that is not strictly increasing makes the
        ordering in `_replace_holdings` meaningless. Mongo increments under the
        document lock, so N concurrent callers get N distinct, ordered values.
        """
        # `find_one_and_update`, NOT `$inc` followed by `find_one`. The first
        # draft of this method was the latter, and it was the same read-modify-
        # write defect it exists to prevent, one level down: two concurrent
        # callers increment to 1 and 2, then both read — and can both read 2.
        # Two syncs holding the same generation are unordered, and the ordering
        # is the entire fix. Found by
        # `test_concurrent_syncs_of_one_account_never_double_the_portfolio`.
        try:
            row = await self.db.portfolios.find_one_and_update(
                {"user_id": user_id, "broker_account_id": account_id},
                {"$inc": {"sync_generation": 1}},
                upsert=True, return_document=ReturnDocument.AFTER,
                projection={"sync_generation": 1})
            return int((row or {}).get("sync_generation") or 1)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Could not draw a sync generation for %s: %s",
                           account_id, e)
            return 1

    async def _replace_holdings(self, account: BrokerAccountRef, holdings: list,
                                *, generation: int, now: str) -> None:
        """Make `db.holdings` for ONE account match `holdings`, race-safely.

        WHAT THIS REPLACED, AND WHY IT WAS WRONG
        ----------------------------------------
        It was `delete_many({user_id, broker_account_id})` followed by
        `insert_many(...)`. Two statements, no atomicity between them, and two
        distinct failure modes for a user who syncs twice at once (two clicks,
        or a manual sync landing on top of the sync that follows a reconnect):

        * **delete, delete, insert, insert** leaves *both* result sets in the
          collection. Nothing removed the second write's rows, so the account
          holds every position twice and the portfolio value on the dashboard is
          exactly doubled.
        * between the delete and the insert the account has **no holdings at
          all**, and any read landing in that window — the portfolio snapshot
          job, a dashboard refresh, the tick re-mark path — sees an empty
          portfolio and reports it as fact.

        HOW THE GENERATION FIXES BOTH
        -----------------------------
        Nothing is deleted before the new rows exist, so the empty window is
        gone outright. Each row is upserted under `sync_generation <= ours`, so a
        sync can refresh a row it is newer than but can never **downgrade** one a
        newer sync already wrote; the cleanup then removes only rows *strictly
        older* than this generation, so a newer sync's rows always survive an
        older sync's cleanup. Whichever order the two interleave, the collection
        ends up holding each symbol exactly once, at the newest generation that
        wrote it.

        The unique index on `(broker_account_id, symbol)` is what makes the
        per-symbol upsert atomic against a concurrent insert of the same symbol
        — without it, two callers both find nothing and both insert, which is
        the duplication this method exists to prevent, merely relocated. A
        duplicate-key error here therefore means "a newer sync already owns this
        row", which is a correct outcome and is skipped rather than raised.
        """
        user_id, account_id = account.user_id, account.broker_account_id
        for h in holdings:
            symbol = h.get("symbol")
            if not symbol:
                continue
            doc = {**h, "user_id": user_id, "broker": account.broker,
                   "broker_account_id": account_id, "updated_at": now,
                   "sync_generation": generation}
            claim = {"broker_account_id": account_id, "symbol": symbol,
                     "sync_generation": {"$lte": generation}}
            try:
                await self.db.holdings.update_one(claim, {"$set": doc}, upsert=True)
            except Exception as e:
                if not is_duplicate_key(e):
                    raise
                # A CONCURRENT SYNC INSERTED THIS SYMBOL BETWEEN OUR FILTER AND
                # OUR INSERT — and the retry below is load-bearing, not defensive.
                #
                # The first version of this method treated a duplicate key as
                # "a newer sync owns this row" and skipped. That is wrong, and
                # the way it was wrong cost rows: two syncs racing on the SAME
                # symbol both find nothing, one inserts at generation N, the
                # other collides — and if the collider was the NEWER sync, it
                # never wrote its generation onto the row, so its own cleanup
                # below then deleted the row as stale. A three-position account
                # synced twice ended up holding one or two positions.
                # Reproduced by `test_concurrent_syncs_of_one_account_never_
                # double_the_portfolio`, which is why that test seeds three
                # symbols rather than one.
                #
                # The retry is an UPDATE, never an upsert: the row demonstrably
                # exists now. Its filter still carries the generation guard, so
                # a genuinely older sync matches nothing and correctly leaves a
                # newer sync's row alone — the skip's intended behaviour, applied
                # only where it is actually true.
                await self.db.holdings.update_one(claim, {"$set": doc})
        # Anything this account carried that an OLDER sync wrote and this one did
        # not refresh is a position that is no longer held. `$lt`, never `$ne`:
        # `$ne` would delete the rows of a concurrent NEWER sync and leave the
        # account holding whichever set finished its cleanup last.
        await self.db.holdings.delete_many(
            {"user_id": user_id, "broker_account_id": account_id,
             "sync_generation": {"$lt": generation}})
        # Pre-D6.7 rows carry no generation at all. They belong to this account
        # and predate this sync, so they are exactly what the cleanup means; they
        # are removed in their own statement because `$lt` does not match a
        # missing field.
        await self.db.holdings.delete_many(
            {"user_id": user_id, "broker_account_id": account_id,
             "sync_generation": {"$exists": False}})

    async def sync_portfolio(self, account: BrokerAccountRef) -> dict:
        """Pull holdings/positions/funds for ONE account, persist them and
        broadcast a portfolio.synced event. Restarts the realtime stream so
        tick subscriptions cover the current holdings.

        Every persisted row carries `broker_account_id` since D6.4. The old rows
        named the broker, which meant two accounts at one broker wrote into each
        other's holdings — and `delete_many({user_id, broker})` at the top of each
        sync deleted the other account's portfolio before writing its own.
        """
        session = await self.get_session(account)
        user_id, broker = account.user_id, account.broker
        account_id = account.broker_account_id
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
        # D6.7 — A MONOTONE GENERATION, TAKEN BEFORE ANY HOLDING IS WRITTEN.
        #
        # `$inc` on the account's own portfolio row, so the value is issued by
        # the server under the document lock and two concurrent syncs of one
        # account can never draw the same number. Everything below is ordered by
        # it; see `_replace_holdings` for why that ordering is the whole fix.
        generation = await self._next_sync_generation(user_id, account_id)
        await self.db.portfolios.update_one(
            {"user_id": user_id, "broker_account_id": account_id},
            {"$set": {
                "user_id": user_id,
                "broker": broker,
                "broker_account_id": account_id,
                "total_value": current,
                "invested_amount": invested,
                "unrealized_pnl": round(current - invested, 2),
                "cash_balance": (funds or {}).get("available_margin"),
                "holdings_count": len(holdings),
                "positions_count": len(positions),
                "last_synced": now,
            }},
            upsert=True)
        await self._replace_holdings(account, holdings, generation=generation,
                                     now=now)
        # D4.3: the instrument map is derived from exactly these rows, so a sync
        # is the moment — and the only moment — it can go stale. Rebuilt from
        # the fetched rows rather than dropped, so the very next tick resolves
        # against the new portfolio instead of triggering a re-read.
        self._remember_instrument_map(account, holdings=holdings, positions=positions)
        await self.db.broker_accounts.update_one(
            {"broker_account_id": account_id}, {"$set": {"last_sync": now}})
        session["last_sync"] = now

        await self._audit(user_id, "broker.portfolio.synced", {
            "broker": broker, "broker_account_id": account_id,
            "holdings": len(holdings), "positions": len(positions)})
        self._activity(user_id, f"{adapter.display_name} portfolio synced — "
                       f"{len(holdings)} holdings, {len(positions)} positions")
        result = {
            "success": True, "broker": broker,
            "broker_account_id": account_id, "synced_at": now,
            "holdings": holdings, "positions": positions, "funds": funds,
            "summary": {"invested": invested, "current": current,
                        "pnl": round(current - invested, 2),
                        "holdings_count": len(holdings),
                        "positions_count": len(positions)},
        }
        await self._push(user_id, {"type": "portfolio_synced", "data": {
            "broker": broker, "broker_account_id": account_id,
            "summary": result["summary"], "synced_at": now}})
        # Sprint R5: publish the doc's `portfolio.synced` event (per-user via
        # the bridge) and follow with a fresh full snapshot so allocation/P&L
        # surfaces update the moment a sync lands. Best-effort.
        try:
            from services import portfolio_stream
            from services.market_engine.event_bus import event_bus
            await event_bus.publish("portfolio.synced", {
                "user_id": user_id, "broker": broker,
                "broker_account_id": account_id,
                "summary": result["summary"], "synced_at": now})
            await portfolio_stream.publish_snapshot(self.db, user_id, reason="broker_sync")
        except Exception as e:
            logger.warning(f"portfolio.synced publish after {broker} sync failed: {e}")
        try:
            await self.start_stream(account, holdings=holdings, positions=positions)
        except Exception as e:
            logger.warning(f"Stream restart after {broker} sync failed: {e}")
        return result

    # -- orders ---------------------------------------------------------------------------------
    async def place_order(self, account: BrokerAccountRef, order: dict) -> dict:
        session = await self.get_session(account)
        result = await broker_gateway.place_order(account.broker, session, order)
        await self._record_order(account, {**order, **result})
        await self._audit(account.user_id, "broker.order.placed", {
            "broker": account.broker, "broker_account_id": account.broker_account_id,
            "order_id": result.get("order_id"),
            "symbol": order.get("symbol"), "transaction_type": order.get("transaction_type"),
            "quantity": order.get("quantity"), "order_type": order.get("order_type")})
        self._activity(account.user_id, f"Order placed on {adapter_display(account.broker)}: "
                       f"{order.get('transaction_type', 'BUY')} {order.get('quantity')} {order.get('symbol')}")
        return {**result, "broker_account_id": account.broker_account_id}

    async def modify_order(self, account: BrokerAccountRef, order_id: str, changes: dict) -> dict:
        session = await self.get_session(account)
        result = await broker_gateway.modify_order(account.broker, session, order_id, changes)
        await self._audit(account.user_id, "broker.order.modified", {
            "broker": account.broker, "broker_account_id": account.broker_account_id,
            "order_id": order_id, "changes": changes})
        return result

    async def cancel_order(self, account: BrokerAccountRef, order_id: str) -> dict:
        session = await self.get_session(account)
        result = await broker_gateway.cancel_order(account.broker, session, order_id)
        await self._audit(account.user_id, "broker.order.cancelled", {
            "broker": account.broker, "broker_account_id": account.broker_account_id,
            "order_id": order_id})
        return result

    async def sync_orders(self, account: BrokerAccountRef) -> list:
        """Pull today's order book from the broker and persist every order into
        db.orders (the unified order-history store, also fed by placements and
        the realtime stream). Returns the fetched orders."""
        orders = await self.get_orders(account)
        for order in orders:
            await self._record_order(account, order)
        return orders

    async def _record_order(self, account: BrokerAccountRef, order: dict):
        """Persist one order row, owned by a user AND bound to an account.

        The two identities on the row mean different things and D6.4 keeps both:
        `user_id` is the platform owner (who may read it), `broker_account_id` is
        the brokerage account it was actually placed in. Deduplication moved to
        `(broker_account_id, order_id)` — a broker order id is unique within an
        account, not within a user, so two accounts at one broker could
        legitimately produce the same id and the old key would have merged them
        into one row.
        """
        if not order.get("order_id"):
            return
        doc = {k: v for k, v in order.items() if k != "_id"}
        doc.update({"user_id": account.user_id, "broker": account.broker,
                    "broker_account_id": account.broker_account_id,
                    "updated_at": _now_iso()})
        doc.setdefault("placed_at", _now_iso())
        await self.db.orders.update_one(
            {"broker_account_id": account.broker_account_id,
             "order_id": order["order_id"]},
            {"$set": doc}, upsert=True)

    # -- realtime streaming -------------------------------------------------------------------------
    async def start_stream(self, account: BrokerAccountRef,
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
        broker = account.broker
        streams = broker_gateway.stream_capabilities(broker)
        if not (streams["orders"] or streams["ticks"]):
            logger.debug("Broker %s offers no realtime stream — skipping", broker)
            return

        session = await self.get_session(account)
        instrument_tokens: list = []
        feed_symbols: tuple = ()
        if streams["ticks"]:
            instrument_tokens, feed_symbols = await self._plan_tick_subscription(
                account, session, holdings=holdings, positions=positions)

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
        feed_shards: tuple = ()
        for channel in broker_gateway.stream_channels(broker):
            # D5.6: `channels=None` — every existing caller — opens every
            # channel, byte-identically to before. A re-probe passes the one
            # channel it is recovering, because `BrokerStreamManager.start_stream`
            # stops a channel before replacing it: an account-wide attach would
            # blip a perfectly healthy *order* socket in order to re-ask a
            # question about the market feed.
            if channels is not None and channel.name not in channels:
                continue
            carries_ticks = self._channel_carries_ticks(broker, channel.name)
            started_tick_channel = started_tick_channel or carries_ticks
            # D5.10: one logical subscription, as many connections as the
            # broker's own per-connection limit requires. A channel that
            # declares no limit — every channel written before D5.10, and every
            # broker whose cap is a session quota rather than a socket ceiling —
            # plans exactly one shard holding everything, which is byte for byte
            # what this loop did before. See `services/brokers/sharding.py`.
            plan = plan_shards(
                instrument_tokens,
                max_instruments_per_connection=getattr(
                    channel, "max_instruments_per_connection", None),
                max_connections=getattr(channel, "max_connections", None),
                broker=broker,
                channel=channel.name,
            )
            if carries_ticks:
                feed_shards = plan.ids or (DEFAULT_SHARD_ID,)
            # A channel with nothing to subscribe still opens one connection:
            # an order stream subscribes to no instruments and must not be
            # planned out of existence by an empty instrument list.
            shards = plan.shards or (
                InstrumentShard(id=DEFAULT_SHARD_ID, instruments=tuple(instrument_tokens)),
            )
            await self._reshard_channel(
                account, channel.name, shards,
                session=session, credentials=credentials)
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
                await attach_market_feed(account, feed_symbols, feed_shards)
            except Exception as e:
                logger.warning(f"Registering the {broker} market feed failed: {e}")

    async def _reshard_channel(self, account: BrokerAccountRef, channel: str,
                               shards: "Sequence[InstrumentShard]", *,
                               session: dict, credentials: dict):
        """Bring one channel's connections into line with its shard plan (D5.10).

        Three things happen, in this order, and the order is the whole of
        make-before-break at this layer:

        1. **a connection whose subscription has not changed is left alone.**
           Not stopped and restarted — untouched, still holding its socket, still
           delivering, still holding the readiness and probation window it has
           earned. `start_stream` has always stopped a stream before replacing
           it, so without this a portfolio sync that added one instrument would
           tear down every connection the account had and re-earn everything on
           all of them, and a re-probe of a broken shard would blip the working
           ones. Never leaving an instrument uncovered "merely because the
           planner is rebuilding" is exactly this step.
        2. **connections that are new or whose membership changed are opened**,
           replacing whatever held their shard id before.
        3. **connections the plan no longer has are stopped**, last, so the
           shrink half of a reshard never runs before the connections that are
           taking over their instruments exist.

        A shard is compared on what actually determines what a connection
        delivers: its instrument list, its session and its credentials. A
        connection that is not *running* is always rebuilt, whatever it holds —
        which is what makes this method the whole of D5.6's re-probe for a
        sharded channel: the broken connection is re-opened and its healthy
        siblings are not asked anything.
        """
        planned = {shard.id: shard for shard in shards}
        account_id = account.broker_account_id
        for shard in shards:
            if self._shard_is_current(account, channel, shard,
                                      session=session, credentials=credentials):
                continue
            await stream_manager.start_stream(
                account.user_id, account.broker, session,
                broker_account_id=account_id,
                credentials=credentials,
                instrument_tokens=list(shard.instruments),
                # D6.4 — every callback is bound to THIS account. The transport
                # still reports `(user_id, broker)`; the binding is what makes
                # the engine's own handlers account-addressed without a signature
                # change reaching `stream.py`. See `_bind_account`.
                on_order_update=_bind_account(self._on_stream_order, account, shard.id),
                on_tick=_bind_account(self._on_stream_tick, account, shard.id),
                on_expired=_bind_account(self._on_stream_expired, account, shard.id),
                on_not_entitled=_bind_account(self._on_stream_not_entitled, account, shard.id),
                on_link_state=_bind_account(self._on_stream_link_state, account, shard.id),
                channel=channel,
                shard=shard.id,
            )
        for row in stream_manager.status():
            if (row["broker_account_id"], row["channel"]) != (account_id, channel):
                continue
            if row["shard"] not in planned:
                await stream_manager.stop_stream(account_id, channel, row["shard"])

    def _shard_is_current(self, account: BrokerAccountRef, channel: str,
                          shard: "InstrumentShard", *, session: dict, credentials: dict) -> bool:
        """Whether this exact connection is already open and already correct.

        Compared on everything that decides what the connection delivers and
        nothing that does not: it must be running, and its instruments, session
        and credential material must be the ones the new plan calls for. A
        session or credential that moved is a connection that has to be reopened
        however unchanged its instruments are — the old socket is authenticated
        with material the account no longer uses.

        Deliberately conservative in one direction only: anything this cannot
        prove is unchanged is rebuilt, so the failure mode of a wrong answer
        here is the pre-D5.10 behaviour (a reconnect) rather than a stale
        subscription nobody notices.
        """
        stream = stream_manager.get(account.broker_account_id, channel, shard.id)
        if stream is None or not stream.running:
            return False
        return (
            list(stream.instrument_tokens) == list(shard.instruments)
            and stream.session == session
            and stream.credentials == dict(credentials or {})
        )

    async def _on_stream_order(self, account: BrokerAccountRef, order: dict,
                               *, shard: str = DEFAULT_SHARD_ID):
        """Order update from ONE account's realtime feed."""
        user_id, broker = account.user_id, account.broker
        try:
            await self._record_order(account, order)
        except Exception as e:
            logger.error(f"Failed to persist streamed order update: {e}")
        await self._push(user_id, {"type": "broker_order_update", "data": {
            **order, "broker_account_id": account.broker_account_id}})
        # Sprint R6: also publish on the event bus — the bridge delivers a
        # `broker.order.updated` envelope per-user on the `broker` channel
        # (the Orders tab patches rows live from it). The legacy push above
        # stays one sprint for compatibility.
        try:
            from services.market_engine.event_bus import event_bus
            await event_bus.publish("broker.order.updated", {
                "user_id": user_id, "broker": broker,
                "broker_account_id": account.broker_account_id, "order": order})
        except Exception as e:
            logger.warning(f"broker.order.updated publish failed: {e}")
        status = order.get("status")
        if status in ("FILLED", "REJECTED", "CANCELLED") and self.db is not None:
            verb = {"FILLED": "executed", "REJECTED": "rejected", "CANCELLED": "cancelled"}[status]
            try:
                from services.notification_service import create_notification
                # Built first so the notification call stays inside the line
                # budget; the payload names the account so a user with two
                # accounts at one broker can tell which one the order was in.
                event_data = {
                    "order_id": order.get("order_id"),
                    "broker": broker,
                    "broker_account_id": account.broker_account_id,
                }
                await create_notification(
                    self.db, user_id,
                    type=f"ORDER_{status}",
                    title=f"Order {verb}",
                    message=f"{order.get('transaction_type', '')} {order.get('quantity', '')} "
                            f"{order.get('symbol', '')} — {verb} on {adapter_display(broker)}."
                            + (f" Reason: {order.get('status_message')}" if status == "REJECTED" and order.get("status_message") else ""),
                    severity="critical" if status == "REJECTED" else "info",
                    symbol=order.get("symbol"),
                    data=event_data,
                )
            except Exception:
                pass

    # -- instrument identity (D4.3) ------------------------------------------
    async def _instrument_map(self, account: BrokerAccountRef) -> InstrumentMap:
        """The account's broker-identifier → canonical-symbol table.

        Built from the rows this engine already syncs, so it costs no broker
        call: a canonical holding carries the broker's `instrument_token`, the
        trading symbol and the exchange side by side, which *is* the mapping.

        Cached per account and invalidated on sync/disconnect. An account with
        nothing synced yet gets an empty map, which resolves nothing by token
        and everything a symbol-identified broker sends by symbol — the correct
        answer for both, rather than a guess for either.
        """
        key = account.broker_account_id
        cached = self._instrument_maps.get(key)
        if cached is not None:
            return cached
        holdings = []
        if self.db is not None:
            try:
                # Scoped to the ACCOUNT, not to `(user_id, broker)`: the rows of
                # a second Zerodha account carry different broker identifiers for
                # the same symbols, and folding them into one map would let one
                # account's tick be named from the other's instrument table.
                holdings = await self.db.holdings.find(
                    {"user_id": account.user_id,
                     "broker_account_id": key}).to_list(1000)
            except Exception as e:
                logger.warning(f"Instrument map load failed for {account.broker}: {e}")
                holdings = []
        return self._remember_instrument_map(account, holdings=holdings)

    async def _plan_tick_subscription(self, account: BrokerAccountRef, session: dict,
                                      *, holdings: list, positions: list) -> tuple:
        """What this account's tick feed subscribes to, and what it may name.

        Returns `(instrument_tokens, feed_symbols)` — the broker's own
        identifiers for the wire, and the canonical symbols the provider is
        granted coverage for. Extracted from `start_stream` when D5.15 gave the
        subscription a second source: the method was already at the complexity
        ceiling, and the two halves of the answer are one decision.

        THE TWO SOURCES, AND WHY BOTH EXIST
        -----------------------------------
        1. **the portfolio**, whose rows carry the broker's identifiers already
           (`stream_instruments`). This was the whole of the universe before
           D5.15, and for an account that holds nothing it is empty — a socket
           that opens, reports its link up and can never deliver a packet.
        2. **the catalogue**, which turns the rest of the account's universe —
           watchlist, dashboard — into this broker's identifiers through the
           adapter, the only layer entitled to know what one looks like.

        Every step of the second degrades to the first rather than failing: a
        broker with no catalogue resolves nothing, an unreachable instrument
        master resolves nothing, and an unresolvable symbol is omitted. In each
        case the account keeps exactly the portfolio-derived subscription it had
        before D5.15.

        D4.3 — the same lists that decide what to subscribe to also decide what
        an arriving tick can be *named*, so the map is rebuilt from both here.
        A subscription the map cannot read back is the same defect as no
        subscription, reached one step later and silently: `canonical_ticks`
        drops what it cannot name.
        """
        broker = account.broker
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
        catalogue = await self._resolve_feed_catalogue(
            account, session, holdings=holdings, positions=positions)
        for token in catalogue.values():
            if token not in instrument_tokens:
                instrument_tokens.append(token)
        instrument_map = self._remember_instrument_map(
            account, holdings=holdings, positions=positions, catalogue=catalogue)
        return instrument_tokens, instrument_map.symbols

    async def _feed_watchlist_symbols(self, user_id: str) -> list:
        """This user's watchlisted symbols, or [] when they cannot be read.

        Scoped to the one account on purpose. `db.watchlist.distinct("symbol")`
        with no filter returns every user's watchlist, and a feed consumed under
        one user's broker entitlement may not be aimed at another user's
        instruments.

        When this was written, both of the platform's price broadcast loops did
        exactly that. D5.15 fixed one of them; D5.16 fixed the other, which was
        also publishing the result to every socket — see
        `heartbeat_engine._watchlist_symbols`. There is now no unfiltered read
        of this collection anywhere.
        """
        if self.db is None:
            return []
        try:
            return await self.db.watchlist.distinct("symbol", {"user_id": str(user_id)})
        except Exception as e:
            logger.warning(f"Watchlist read for the {user_id} feed universe failed: {e}")
            return []

    async def _resolve_feed_catalogue(self, account: BrokerAccountRef, session: dict,
                                      *, holdings: list, positions: list) -> dict:
        """`{CANONICAL_SYMBOL: broker instrument id}` for the non-portfolio half
        of this account's feed universe (D5.15; exchange-aware in D5.16).

        The universe passed to the adapter is a sequence of `FeedInstrument` —
        symbol, exchange, segment — not bare symbols. This method is unchanged
        by that: it neither builds nor reads one, which is the property that let
        the contract widen beneath five adapters without the engine learning
        what an instrument identifier or an exchange means to any of them.

        Portfolio instruments are deliberately excluded from the *result* even
        though they are included in the universe passed to the adapter: their
        identifiers already came from the broker on the holding row itself, and
        a catalogue lookup is a weaker source than the account's own record.
        Passing them anyway is what lets an adapter answer for a held instrument
        whose row carried no identifier.

        Never raises. The catalogue widens coverage; it is not load-bearing for
        a feed that already has a portfolio to subscribe to.
        """
        universe = build_feed_universe(
            holdings=holdings,
            positions=positions,
            watchlist=await self._feed_watchlist_symbols(account.user_id),
            # D5.17 — the index strip is on every page for every account and is
            # four instruments. It enters the universe here, as the same kind of
            # value as everything else, which is the property that let a second
            # segment ship without this method learning what a segment is.
            indices=index_instruments(),
            dashboard=dashboard_symbols(),
        )
        if not universe:
            return {}
        try:
            return await broker_gateway.resolve_instruments(
                account.broker, universe, session)
        except Exception as e:
            logger.warning(f"Instrument catalogue lookup for {account.broker} failed: {e}")
            return {}

    def _remember_instrument_map(self, account: BrokerAccountRef,
                                 holdings: list = None, positions: list = None,
                                 catalogue: dict = None) -> InstrumentMap:
        """Rebuild and cache the account's map from rows already in hand.

        `start_stream` and `sync_portfolio` both hold freshly fetched holdings
        *and* positions, and positions are not persisted — so seeding from them
        is the only way an intraday position's ticks are ever mappable. Rebuilt
        wholesale rather than mutated: a stream reading the map must never
        observe a half-updated table.

        `catalogue` (D5.15) carries the instruments the feed was aimed at beyond
        the portfolio, so a tick for a watchlisted or dashboard symbol can be
        named. It is passed only by `start_stream`, which is the one caller that
        resolved one; a sync rebuilds from the portfolio alone and would
        otherwise drop the catalogue half of the map on the floor — which is why
        `start_stream` is called at the end of `sync_portfolio` and rebuilds it
        again with both halves.
        """
        instrument_map = InstrumentMap.from_portfolio(holdings, positions, catalogue)
        self._instrument_maps[account.broker_account_id] = instrument_map
        return instrument_map

    def _forget_instrument_map(self, account: BrokerAccountRef) -> None:
        self._instrument_maps.pop(account.broker_account_id, None)

    async def _on_stream_tick(self, account: BrokerAccountRef, ticks: list,
                              *, shard: str = DEFAULT_SHARD_ID):
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
        user_id, broker = account.user_id, account.broker
        if ticks:
            recovery_register.discharge(account.broker_account_id)
        instrument_map = await self._instrument_map(account)
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
            await publish_market_ticks(account, ticks, shard)
        except Exception as e:
            logger.warning(f"Market feed publish from {broker} ticks failed: {e}")
        await self._push(user_id, {"type": "broker_price_tick", "data": {
            "broker": broker, "broker_account_id": account.broker_account_id,
            "ticks": ticks}})
        # Sprint R5: broker ticks drive the live portfolio — recompute this
        # user's P&L/allocation server-side and stream `portfolio.updated`
        # (throttled inside; best-effort so a recompute error never breaks
        # the tick forward above).
        try:
            from services import portfolio_stream
            await portfolio_stream.apply_broker_ticks(
                self.db, user_id, broker, ticks,
                broker_account_id=account.broker_account_id)
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

    async def _on_stream_link_state(self, account: BrokerAccountRef, up: bool, reason: str = "",
                                    channel: str = None, *, shard: str = DEFAULT_SHARD_ID):
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
        if not self._channel_carries_ticks(account.broker, channel):
            return
        try:
            await set_market_feed_link(account, up=up, reason=reason, shard=shard)
        except Exception as e:
            logger.warning(f"Market feed link update from {account.broker} failed: {e}")

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

    async def _on_stream_expired(self, account: BrokerAccountRef, channel: str = None,
                                 *, shard: str = DEFAULT_SHARD_ID):
        """A broker reported this account's token dead on one of its channels.

        The token is the account's, not the channel's and not the connection's,
        so every channel and every shard of this broker is finished — the others
        are reconnecting into the same rejection right now. The one that
        reported it is `discard`ed (we are inside its task; see
        `BrokerStreamManager.discard`) and the rest are stopped properly, which
        cancels their tasks rather than merely forgetting them.

        D5.10 scopes the `discard` to the reporting *connection* as well as its
        channel, for the reason D4.7 scoped it to the channel: discarding a
        sibling shard here would drop a live stream from the registry without
        stopping it, leaking exactly the task the `stop_stream` below cancels.
        """
        user_id, broker = account.user_id, account.broker
        account_id = account.broker_account_id
        self._forget_session(account_id)
        # D5.6. Recorded, and recorded as SESSION rather than merely left out:
        # an expired token must be *visibly* excluded from re-probe rather than
        # absent from the register, so the exclusion is a fact a test can read
        # and a mutation can break. Retrying a dead credential on a schedule is
        # a login attempt on a timer, which is how an account gets locked rather
        # than how a feed comes back. It also *replaces* any REPROBE candidate
        # this account already had: the strictly stronger condition is the later
        # one, so an entitlement re-probe stops the moment the token dies.
        recovery_register.reclassify(account_id, RecoveryClass.SESSION)
        recovery_register.record_withdrawal(
            account_id, channel or DEFAULT_STREAM_CHANNEL, RecoveryClass.SESSION)
        # A dead token means the feed cannot deliver another tick. Unregistering
        # it is what stops the Source Manager resolving a priority-1 streaming
        # provider that can only answer with silence; the baseline below it then
        # serves the TICKS capability's absence honestly.
        # D5.13 — and it is a *different* reason from an entitlement refusal,
        # which is the whole point of the vocabulary having three values: this
        # user's way back is a new session, not a re-probe.
        await detach_market_feed(
            account, change_reason=FeedChangeReason.SESSION_EXPIRED)
        # The stream task is about to return on its own; drop the registry entry
        # with it (PH3.6). Without this the manager retained a finished
        # BrokerStream — and the expired access token inside its `session` — for
        # the life of the process. `discard` rather than `stop_stream` because we
        # are running inside that task; see BrokerStreamManager.discard.
        stream_manager.discard(account_id, channel, shard)
        # The remaining channels are separate tasks, so stopping them here is
        # safe — the calling channel has just been removed from the registry, so
        # this cannot await the task it is running inside.
        await stream_manager.stop_stream(account_id)
        # D6.4 — the account moves to REAUTH_REQUIRED rather than DISCONNECTED.
        # The user did not detach it and one login fixes it; a UI that cannot
        # tell those apart shows "connect your broker" to somebody whose account
        # is connected and whose token simply aged out overnight.
        await broker_accounts.set_status(account_id, BrokerAccountStatus.REAUTH_REQUIRED)
        await self._audit(user_id, "broker.session.expired", {
            "broker": broker, "broker_account_id": account_id})
        await self._push(user_id, {"type": "broker_status", "data": {
            "broker": broker, "broker_account_id": account_id,
            "connected": False, "session_expired": True}})

    async def _on_stream_not_entitled(self, account: BrokerAccountRef, channel: str = None,
                                      *, shard: str = DEFAULT_SHARD_ID):
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
        user_id, broker = account.user_id, account.broker
        account_id = account.broker_account_id
        if self._channel_carries_ticks(broker, channel):
            # D5.13 — closes the backend half of LIM-D5.5-2. Until now this
            # unregistration moved the owner's tier from `streaming` to
            # `delayed` with `reason: null`, and the explanation existed only in
            # the audit row two lines below, which no consumer can read.
            await detach_market_feed(
                account, change_reason=FeedChangeReason.ENTITLEMENT_REFUSED)
        # D5.10 — THE REFUSAL ENDS EVERY CONNECTION OF THIS CHANNEL, AND ONLY
        # THIS CHANNEL. An entitlement is a statement about a *capability*, and
        # every shard of one channel serves the same capability with the same
        # credential, so a refusal on one is a refusal on all: the transport
        # stops only the connection that saw it, and leaving the siblings up
        # would hold live sockets open against a broker that has just said to
        # stop, feeding a provider that has just been unregistered. The
        # reporting connection is `discard`ed because we are inside its task;
        # the rest are stopped properly, which cancels theirs — the same split
        # `_on_stream_expired` makes one scope out.
        stream_manager.discard(account_id, channel, shard)
        await stream_manager.stop_stream(account_id, channel)
        # D5.6, and the whole of what this sprint adds to this method. The
        # refusal stays exactly as terminal as D5.5 made it — the loop does not
        # reconnect and nothing here restarts it — but the withdrawal is now
        # *recorded*, so a paced re-probe can later ask the one question a
        # reconnect could not: has this account's entitlement changed? See
        # ADR-046. The class is REPROBE because an entitlement is a
        # provider-level condition a person can grant without this process being
        # told; it is not a credential problem and not a transport problem.
        recovery_register.record_withdrawal(
            account_id, channel or DEFAULT_STREAM_CHANNEL, RecoveryClass.REPROBE)
        await self._audit(user_id, "broker.feed.entitlement_denied", {
            "broker": broker, "broker_account_id": account_id})

    # -- provider recovery (D5.6) ----------------------------------------------------------------------
    def _has_live_session(self, broker_account_id: str) -> bool:
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
        return bool(self._sessions.get(broker_account_id))

    def _channel_is_attached(self, broker_account_id: str, channel: str) -> bool:
        """Whether a stream is already running for this exact channel.

        Asked so a re-probe never replaces a live connection. `start_stream`
        stops a channel before opening it, so an unguarded sweep would tear down
        a feed that a user reconnect or a session restore had already brought
        back — recovering a feed by breaking it.
        """
        return any(
            row["running"]
            for row in stream_manager.status()
            if row["broker_account_id"] == broker_account_id
            and row["channel"] == channel
        )

    async def _reattach_channel(self, broker_account_id: str, channel: str):
        """One ordinary attach of one channel — the whole of what a re-probe does.

        There is no probe-only path: this is `start_stream` scoped to a single
        channel, so a recovered feed travels the identical route a first
        attachment does — connect, subscribe, first valid canonical tick, READY,
        probation, stability, latency. A control-plane "yes" would have proved
        something the platform does not accept as evidence (ADR-046).
        """
        # `get_unscoped` rather than `resolve`: a background re-probe acts AS the
        # account and has no authenticated user to scope by. The id it holds came
        # from the register, which only ever received one from this engine.
        account = await broker_accounts.get_unscoped(broker_account_id)
        if account is None:
            logger.warning("Re-probe skipped: account %s no longer exists",
                           broker_account_id)
            return
        await self.start_stream(account, channels=(channel,))

    def start_recovery(self):
        """Begin the bounded background re-probe sweep. Idempotent.

        Started from the same place session restore is, and it is the only timer
        D5.6 added. A sweep with an empty register performs no I/O: it reads two
        dictionaries and goes back to sleep, so a deployment where nothing has
        ever been refused pays a dictionary lookup a minute.

        D6.7 attaches the credential-cache sweeper here for the same reasons and
        at the same point in the lifecycle — it is cheap, it is idempotent, and
        starting it anywhere else would mean a second place that has to remember
        session restore has happened.
        """
        self._start_credential_sweeper()
        return self._recovery.start()

    def _start_credential_sweeper(self):
        """Run `evict_idle_sessions` on a slow tick. Idempotent.

        THE TASK IS HELD ON THIS ENGINE, NOT IN THE GLOBAL TASK REGISTRY.
        -----------------------------------------------------------------
        `asyncio` keeps only a weak reference to a running task, so *some*
        strong reference is mandatory: a credential sweeper that was silently
        garbage-collected would restore exactly the unbounded retention it
        exists to bound, with nothing logged and no symptom until somebody read
        a heap dump. `self._credential_sweeper` is that reference, and
        `shutdown()` cancels it — the same lifecycle `self._recovery` has had
        since D5.6, for the same reasons.

        It was first registered with `infrastructure.tasks`, and that was wrong
        in a way worth recording rather than quietly correcting. That registry is
        **process-global and outlives an event loop**; this engine is a
        module-level singleton whose `start_recovery()` runs in every test that
        restores a session. The first such test left a perpetual task in the
        registry bound to a loop that then closed, and a later test's
        `cancel_all()` reached across the dead loop — surfacing as two failures
        in the *observability* suite that appeared only in a full run and passed
        in isolation. A supervisor was the right idea at the wrong scope: this
        engine already owns this task's lifetime, so the registry added a second,
        longer-lived owner for something that has one.
        """
        if self._credential_sweeper is not None and not self._credential_sweeper.done():
            return self._credential_sweeper

        async def _loop():
            while True:
                # A tick well below the idle threshold, so an entry is evicted
                # within a few minutes of becoming eligible rather than up to a
                # full threshold late. The sweep is a dictionary scan over at
                # most the number of connected accounts in this process; it
                # performs no I/O and touches no broker.
                await asyncio.sleep(CREDENTIAL_SWEEP_INTERVAL_SECONDS)
                try:
                    self.evict_idle_sessions()
                except Exception as e:  # pragma: no cover - defensive
                    logger.warning("Credential cache sweep failed: %s", e)

        try:
            self._credential_sweeper = asyncio.create_task(
                _loop(), name="broker-credential-sweeper")
        except RuntimeError:
            # No running loop (a synchronous caller). The cache is still bounded
            # by every explicit eviction path — disconnect, observed expiry, a
            # direct `evict_idle_sessions()` — only the timer is absent.
            self._credential_sweeper = None
        return self._credential_sweeper

    async def _stop_credential_sweeper(self) -> None:
        """Cancel the sweeper and wait for it. Safe when it was never started."""
        task, self._credential_sweeper = self._credential_sweeper, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def stop_recovery(self):
        await self._recovery.stop()

    # -- startup ---------------------------------------------------------------------------------------
    async def _mark_reauth_required(self, account: BrokerAccountRef, why: str) -> None:
        """Record that this account needs a user re-login, best-effort.

        Best-effort deliberately: startup restore must not abort because one
        account's status write failed. The account is already unusable — a
        failed status write leaves it exactly as unusable as it was, whereas an
        exception here would stop every *other* account from being restored.
        """
        try:
            await broker_accounts.set_status(
                account.broker_account_id, BrokerAccountStatus.REAUTH_REQUIRED)
            logger.info("Broker account %s (%s) marked REAUTH_REQUIRED at startup: %s",
                        account.broker_account_id, account.broker, why)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Could not mark %s REAUTH_REQUIRED: %s",
                           account.broker_account_id, e)

    async def load_sessions(self):
        """Restore fresh sessions (and streams) for every connected account on
        startup; encrypt any legacy plaintext tokens found along the way."""
        if self.db is None:
            return 0
        restored = 0
        try:
            # D6.4 — the account directory answers "which accounts are live",
            # not a `connected` flag on a row whose identity was a broker name.
            # The migration runs before this (see `server.py`), so every row that
            # reaches here has a `broker_account_id`; one that somehow does not
            # is skipped rather than restored under an invented identity.
            accounts = await broker_accounts.live_accounts()
            for account in accounts:
                broker = account.broker
                user_id = account.user_id
                if not user_id or broker not in broker_registry:
                    continue
                try:
                    session = await self._load_session(account)
                    # LIM-D6.5-3 (closed in D6.6). Both branches below leave a
                    # CONNECTED account with no usable session, and until D6.6
                    # neither wrote that fact down: `status` stayed "connected"
                    # while the credential behind it was months dead, so
                    # `account_statuses` answered `connected: false` and
                    # `status: "connected"` in the same record. Nothing routed
                    # on the stale value — every call path re-derives freshness
                    # and `get_session` refuses — but the account directory is
                    # what the UI, the reconnect prompt and any operator read,
                    # and a directory that claims a dead session is live is a
                    # directory that cannot be trusted to say when a re-login is
                    # owed.
                    #
                    # It is owed *always*, which is why this matters here and
                    # not merely cosmetically: no adapter on this platform
                    # declares SESSION_REFRESH, because Indian retail broker
                    # APIs issue daily tokens with no refresh grant. There is no
                    # unattended path back from an expired session, so
                    # REAUTH_REQUIRED is the entire recovery contract.
                    #
                    # Writing it here rather than leaving it to the first call
                    # makes startup agree with `get_session`, which has always
                    # set exactly this status on exactly this condition
                    # (see `get_session` above) — the two paths now reach the
                    # same state instead of differing by whoever got there
                    # first.
                    if not session or not session.get("access_token"):
                        await self._mark_reauth_required(
                            account, "no usable credential on a live account")
                        continue
                    if not broker_gateway.session_is_fresh(broker, session):
                        logger.info(f"Saved {broker} session for account "
                                    f"{account.broker_account_id} has expired; "
                                    f"reconnect required.")
                        await self._mark_reauth_required(account, "session expired")
                        continue
                    self._cache_session(account.broker_account_id, session)
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
                    await self._publish_connection(account, connected=True)
                    try:
                        await self.start_stream(account)
                    except Exception as e:
                        logger.warning(f"Could not start {broker} stream for "
                                       f"account {account.broker_account_id}: {e}")
                except Exception as e:
                    logger.error(f"Failed restoring {broker} session for "
                                 f"account {account.broker_account_id}: {e}")
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
        await self._stop_credential_sweeper()
        await stream_manager.stop_all()

    # -- ownership invariant (D6.1 / S3) ---------------------------------------------------------------
    #
    # `any_connected_session(broker)` used to live here. It answered "the most
    # recently connected fresh account for this broker — of ANY user" to a caller
    # that supplied no identity, and `services/zerodha_service.py` was built
    # entirely on top of it: `get_holdings()`, `get_positions()`, `get_funds()`,
    # `get_profile()`, `get_orders()`, `cancel_order()` and — worst —
    # `place_order()` all took no `user_id`. A live order would have gone to
    # whichever user happened to have connected most recently.
    #
    # Both the method and its only consumer are deleted. The invariant that
    # replaced them in D6.1: **every broker operation is addressed by an explicit
    # `(user_id, broker)` pair**.
    #
    # D6.4 TIGHTENED THAT INVARIANT, BECAUSE THE PAIR WAS NOT AN ACCOUNT.
    # ------------------------------------------------------------------
    # `(user_id, broker)` is a user and a *brand*. It answered "your Zerodha",
    # which is only an account identity while a user has at most one — a
    # condition enforced by a unique index rather than by anything true about
    # brokerage. Every broker operation is now addressed by a
    # `BrokerAccountRef`, which:
    #
    #   * can only be obtained from `BrokerAccountDirectory`, whose lookups are
    #     filtered by the owning `user_id` — so a reference to another user's
    #     account cannot be constructed, not merely cannot be used;
    #   * carries the `broker_account_id` that keys `self._sessions`, the stream
    #     registry, the recovery register, the market-feed provider name and
    #     every persisted order, holding and portfolio row;
    #   * is minted from the identity the *broker* returned at token exchange,
    #     never from a client-supplied value.
    #
    # `account_for_broker` is the single bridge from a broker name to an account,
    # it resolves only the unambiguous case, and it raises rather than choosing.
    # `tests/test_d61_security.py` asserts the absence of the old accessor;
    # `tests/test_d64_identity.py` asserts the absence of the selection semantics
    # that would bring it back.


broker_engine = BrokerEngine()

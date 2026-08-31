"""Broker Gateway — the single choke point through which all broker calls pass.

    Application  ->  BrokerEngine  ->  BrokerGateway  ->  BrokerRegistry
                                                       ->  Broker Adapter  ->  Broker API

Nothing above this layer may hold a `BrokerAdapter`. That is the broker-side
equivalent of MARKET_DATA_ARCHITECTURE.md's Developer Rule 2 ("never bypass the
Market Gateway"), and it exists for the same reason: a choke point is the only
place where a cross-cutting guarantee can be made once instead of at every call
site.

WHAT THE GATEWAY GUARANTEES
----------------------------
Four things, on every single broker call:

1. **Capability enforcement.** An unsupported capability is refused *before* the
   adapter is called, as a permanent, user-safe "this broker does not offer
   this" — never a timeout, never a 500, never a network round trip.

2. **Canonical shapes.** Every response is coerced through `contracts.py`, so
   broker-specific keys cannot reach core services even if an adapter emits
   them. This is what actually stopped Kite's `raw` margin tree at the boundary;
   asking adapter authors to be careful would not have.

3. **One error family.** Every exception raised beneath the gateway leaves it as
   a `BrokerError` with a code, a retry flag, a recovery hint and a message
   written for a person. An `httpx` error or a `KeyError` on an unexpected
   payload cannot surface as a stack trace to a user.

4. **Health bookkeeping.** Every outcome is recorded against the broker's API
   health, with the auth-failure exception documented in `health.py`: one
   user's expired token is not evidence that the broker is down.

WHY THE GATEWAY DOES NOT OWN SESSIONS
--------------------------------------
Sessions are per user, encrypted at rest, refreshed on a schedule and restored
at startup — all of which needs the database, and the database belongs to
`BrokerEngine`. The gateway takes a decrypted session as an argument and never
stores, logs or persists one. Splitting it this way keeps the gateway
synchronously testable with no database and keeps token handling in exactly one
module.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.brokers.base import BrokerAdapter
from services.brokers.capabilities import BrokerCapability
from services.brokers.contracts import (
    BrokerConnection,
    coerce_funds,
    coerce_holdings,
    coerce_order_ack,
    coerce_orders,
    coerce_positions,
    coerce_profile,
    coerce_trades,
)
from services.brokers import health as broker_health
from services.brokers.errors import (
    BrokerAuthError,
    BrokerError,
    BrokerErrorCode,
    CapabilityUnsupported,
    normalize_broker_error,
)
from services.brokers.registry import BrokerRegistry, broker_registry
from services.brokers.streaming import EVENT_CAPABILITY, StreamEventKind

logger = logging.getLogger(__name__)


class BrokerGateway:
    """Capability-enforcing, error-normalizing entry point to every broker."""

    def __init__(self, registry: Optional[BrokerRegistry] = None) -> None:
        self._registry = registry if registry is not None else broker_registry

    @property
    def registry(self) -> BrokerRegistry:
        return self._registry

    # ── Resolution ───────────────────────────────────────

    def resolve(self, broker: str) -> BrokerAdapter:
        """The adapter serving `broker`, or a normalized unknown-broker error."""
        return self._registry.require(broker)

    def supports(self, broker: str, capability: BrokerCapability) -> bool:
        """Whether `broker` offers `capability`. Never raises for an unknown
        broker — a broker that does not exist supports nothing, and callers
        asking this question are branching on a feature, not validating input."""
        adapter = self._registry.get(broker)
        return bool(adapter and adapter.supports(capability))

    def require_capability(self, broker: str, capability: BrokerCapability) -> BrokerAdapter:
        """The adapter, having verified it offers `capability`."""
        adapter = self.resolve(broker)
        if not adapter.supports(capability):
            raise CapabilityUnsupported(adapter.name, capability, adapter.display_name)
        return adapter

    def capabilities(self, broker: str) -> List[str]:
        adapter = self._registry.get(broker)
        return sorted(c.value for c in adapter.capabilities) if adapter else []

    def default_product(self, broker: str) -> str:
        """The order product to use when the caller did not choose one.

        Replaces `"CNC" if broker == "zerodha" else "D"` in `server.py`. The old
        expression was wrong in two ways at once: it named a broker inside a core
        route, and its `else` branch silently handed Upstox's product code to
        every broker added after it.
        """
        return self.resolve(broker).default_product

    def list_brokers(self) -> List[Dict[str, Any]]:
        """Every registered broker with its capabilities and configuration state."""
        return [
            {
                "name": adapter.name,
                "display_name": adapter.display_name,
                "configured": adapter.is_configured(),
                "capabilities": sorted(c.value for c in adapter.capabilities),
            }
            for adapter in self._registry.all()
        ]

    # ── Invocation ───────────────────────────────────────

    async def call(
        self,
        broker: str,
        capability: BrokerCapability,
        invoke: Callable[[BrokerAdapter], Any],
        *,
        coerce: Optional[Callable[[Any, str], Any]] = None,
        operation: Optional[str] = None,
    ) -> Any:
        """Run one adapter call through every gateway guarantee.

        The single implementation behind every typed method below. Keeping the
        guarantees in one function rather than repeating them per method is what
        makes "the gateway always normalizes errors" a fact rather than an
        aspiration — a new method gets the behaviour by construction, and cannot
        forget a step.
        """
        adapter = self.require_capability(broker, capability)
        label = operation or capability.value
        try:
            result = await invoke(adapter)
        except BrokerAuthError as exc:
            # Per-user session failure. Recorded, but deliberately not counted
            # against the broker's API health — see health.py.
            # D5.8 — recorded in the shared store when one is configured, so the
            # count an operator reads is the deployment's and not one worker's.
            await broker_health.record_auth_failure_shared(adapter.health)
            raise normalize_broker_error(exc, broker=adapter.name, operation=label, display_name=adapter.display_name)
        except BaseException as exc:
            error = normalize_broker_error(exc, broker=adapter.name, operation=label, display_name=adapter.display_name)
            if _counts_against_health(error):
                if await broker_health.record_failure_shared(adapter.health, error.code):
                    logger.warning("Broker %s health degraded to %s", adapter.name, adapter.health.state.value)
            raise error

        await broker_health.record_success_shared(adapter.health)
        return coerce(result, adapter.name) if coerce else result

    # ── Authentication / configuration ───────────────────
    #
    # Not routed through `call`: these run before a session exists, so there is
    # no capability to check and an auth failure here is the expected outcome of
    # a user cancelling a login rather than a health signal.

    def login_url(self, broker: str, user_id: str = None) -> Dict[str, Any]:
        adapter = self.resolve(broker)
        return adapter.get_login_url(user_id=user_id)

    def parse_callback_params(self, broker: str, params: Dict[str, str]) -> Optional[dict]:
        """The `exchange_token` payload for a broker's OAuth redirect, or None
        when the user cancelled. Keeps callback parsing out of the route."""
        return self.resolve(broker).parse_callback_params(params)

    async def exchange_token(self, broker: str, auth_payload: dict) -> Dict[str, Any]:
        """Exchange an OAuth callback payload for a broker session.

        The returned dict carries live token material. It goes straight to
        `BrokerEngine`, which encrypts it before storage; it is never logged,
        never returned to a route and never placed on the Event Bus.
        """
        adapter = self.resolve(broker)
        try:
            return await adapter.exchange_token(auth_payload)
        except BaseException as exc:
            raise normalize_broker_error(
                exc, broker=adapter.name, operation="exchange_token", display_name=adapter.display_name
            )

    async def refresh_session(self, broker: str, session: dict) -> Optional[dict]:
        """Refresh the access token, or None when this broker cannot.

        Returns None rather than raising for a broker without
        `SESSION_REFRESH`: "this broker issues daily tokens" is a normal
        property of Indian retail brokers, and the engine's correct response —
        prompt a reconnect — is the same as for a refresh that failed.
        """
        adapter = self.resolve(broker)
        if not adapter.supports(BrokerCapability.SESSION_REFRESH):
            return None
        try:
            return await adapter.refresh_session(session)
        except BrokerAuthError:
            return None
        except BaseException as exc:
            logger.warning("Broker %s session refresh failed: %s", adapter.name, exc)
            return None

    async def invalidate_session(self, broker: str, session: dict) -> bool:
        """Log the session out at the broker. True when the broker supports it.

        Best-effort by design: the token is being discarded either way, and a
        broker that rejects the logout of an already-dead token must not fail
        the user's disconnect.
        """
        adapter = self.resolve(broker)
        if not adapter.supports(BrokerCapability.SESSION_INVALIDATE):
            return False
        try:
            await adapter.invalidate_session(session)
            return True
        except BaseException as exc:
            logger.warning("Broker %s token invalidation failed (token may already be dead): %s", adapter.name, exc)
            return False

    def session_expiry(self, broker: str, connected_at):
        return self.resolve(broker).session_expiry(connected_at)

    def session_is_fresh(self, broker: str, session: dict) -> bool:
        return self.resolve(broker).session_is_fresh(session)

    # ── Account data ─────────────────────────────────────

    async def get_profile(self, broker: str, session: dict) -> Dict[str, Any]:
        return await self.call(
            broker, BrokerCapability.PROFILE, lambda a: a.get_profile(session), coerce=coerce_profile
        )

    async def get_holdings(self, broker: str, session: dict) -> List[Dict[str, Any]]:
        return await self.call(
            broker, BrokerCapability.HOLDINGS, lambda a: a.get_holdings(session), coerce=coerce_holdings
        )

    async def get_positions(self, broker: str, session: dict) -> List[Dict[str, Any]]:
        return await self.call(
            broker, BrokerCapability.POSITIONS, lambda a: a.get_positions(session), coerce=coerce_positions
        )

    async def get_funds(self, broker: str, session: dict) -> Dict[str, Any]:
        return await self.call(broker, BrokerCapability.FUNDS, lambda a: a.get_funds(session), coerce=coerce_funds)

    async def get_margins(self, broker: str, session: dict) -> Dict[str, Any]:
        return await self.call(broker, BrokerCapability.MARGINS, lambda a: a.get_margins(session), coerce=coerce_funds)

    async def get_orders(self, broker: str, session: dict) -> List[Dict[str, Any]]:
        return await self.call(broker, BrokerCapability.ORDERS, lambda a: a.get_orders(session), coerce=coerce_orders)

    async def get_trades(self, broker: str, session: dict) -> List[Dict[str, Any]]:
        return await self.call(broker, BrokerCapability.TRADES, lambda a: a.get_trades(session), coerce=coerce_trades)

    # ── Order management ─────────────────────────────────

    async def place_order(self, broker: str, session: dict, order: dict) -> Dict[str, Any]:
        """Place a live order. `order` uses canonical fields only.

        The product defaults to the *adapter's* product code rather than the
        caller's guess, which is what lets a route place an order without
        knowing which broker it is talking to.
        """
        adapter = self.require_capability(broker, BrokerCapability.PLACE_ORDER)
        payload = dict(order or {})
        payload.setdefault("product", adapter.default_product)
        payload.setdefault("variety", adapter.default_variety)
        return await self.call(
            broker,
            BrokerCapability.PLACE_ORDER,
            lambda a: a.place_order(session, payload),
            coerce=lambda r, b: coerce_order_ack(r, b, "PENDING"),
            operation="place_order",
        )

    async def modify_order(self, broker: str, session: dict, order_id: str, changes: dict) -> Dict[str, Any]:
        return await self.call(
            broker,
            BrokerCapability.MODIFY_ORDER,
            lambda a: a.modify_order(session, order_id, changes),
            coerce=lambda r, b: coerce_order_ack(r, b, "PENDING"),
            operation="modify_order",
        )

    async def cancel_order(self, broker: str, session: dict, order_id: str) -> Dict[str, Any]:
        return await self.call(
            broker,
            BrokerCapability.CANCEL_ORDER,
            lambda a: a.cancel_order(session, order_id),
            coerce=lambda r, b: coerce_order_ack(r, b, "CANCELLED"),
            operation="cancel_order",
        )

    # ── Realtime configuration ───────────────────────────

    def stream_capabilities(self, broker: str) -> Dict[str, bool]:
        """Which realtime feeds this broker offers.

        Read by `BrokerEngine.start_stream` to decide whether to open a
        connection at all, replacing the assumption that every broker has a
        stream worth opening.
        """
        adapter = self.resolve(broker)
        return {
            "orders": adapter.supports(BrokerCapability.ORDER_STREAM),
            "ticks": adapter.supports(BrokerCapability.TICK_STREAM),
        }

    def stream_channels(self, broker: str) -> Tuple[Any, ...]:
        """Every connection this broker's realtime surface needs (D4.7).

        Asked by `BrokerEngine.start_stream`, which opens one stream per
        channel. Routed through the gateway rather than read off the registry
        for the same reason every other adapter question is: the engine talks to
        one object, and "how many sockets does this broker need" is a broker
        question the engine must not answer for itself.
        """
        return tuple(self.resolve(broker).stream_channels() or ())

    def stream_credentials(self, broker: str) -> Dict[str, str]:
        return self.resolve(broker).stream_credentials()

    def stream_event_allowed(self, broker: str, kind: StreamEventKind) -> bool:
        """Whether a decoded stream event may be delivered for this broker (D4.2).

        The capability check the streaming path was missing. Every REST call
        passes a capability gate before the adapter is reached; a decoded frame
        had none, so a codec could deliver ticks for a broker that never
        declared TICK_STREAM and nothing would object. That is not hypothetical
        housekeeping — it is how an order-only feed ends up marking a user's
        portfolio from a payload nobody validated it could produce.

        Enforced here rather than in the transport because the gateway is the
        choke point where "may this broker do X" is answered for everything
        else, and answering it in two places is how the two answers diverge.

        Connection-level events (a dead token, a broker error, a heartbeat) are
        ungated: they are facts about the socket rather than data crossing into
        the platform, and a stream that cannot report its own expiry reconnects
        forever into a rejection.
        """
        required = EVENT_CAPABILITY.get(kind)
        if required is None:
            return True
        return self.resolve(broker).supports(BrokerCapability(required))

    async def resolve_instruments(
        self,
        broker: str,
        instruments: "Sequence[Any]",
        session: dict = None,
    ) -> Dict[str, Any]:
        """Canonical instruments -> this broker's instrument identifiers.

        `instruments` are `FeedInstrument` values — symbol, exchange, segment —
        since D5.16; the result stays keyed by canonical symbol, because that is
        what `InstrumentMap` matches an arriving tick against.

        Returns `{}` — not an error — for a broker that publishes no instrument
        catalogue, so a caller can always ask and never has to know which
        brokers can answer. That is the same shape `stream_instruments` uses for
        a broker with no tick feed, and for the same reason: the alternative is
        every call site asking two questions to get one answer, and the second
        question being the one a new broker makes someone forget.

        Best-effort by contract. A catalogue is an optimisation of *coverage* —
        it widens what a feed can be aimed at — and a broker whose master file
        is unreachable must degrade to the portfolio-derived subscription it
        had before D5.15, not fail the user's stream. The error is logged with
        the broker's name and nothing else; a catalogue lookup carries no
        credential and its failure is not a per-user auth event.
        """
        if not instruments:
            return {}
        if not self.supports(broker, BrokerCapability.INSTRUMENT_CATALOGUE):
            return {}
        try:
            resolved = await self.call(
                broker,
                BrokerCapability.INSTRUMENT_CATALOGUE,
                lambda a: a.resolve_instruments(instruments, session),
                operation="resolve_instruments",
            )
        except BrokerError as exc:
            logger.warning("%s instrument catalogue unavailable: %s", broker, exc)
            return {}
        return {str(k): v for k, v in (resolved or {}).items() if v is not None}

    def stream_instruments(self, broker: str, holdings: list = None, positions: list = None) -> List[Any]:
        """Instrument identifiers to subscribe on this broker's tick feed.

        Returns nothing for a broker without `TICK_STREAM`, so the caller never
        has to ask two questions to get one answer.
        """
        adapter = self.resolve(broker)
        if not adapter.supports(BrokerCapability.TICK_STREAM):
            return []
        return adapter.stream_instruments(holdings=holdings, positions=positions)

    # ── Health / diagnostics ─────────────────────────────

    def connection(
        self, *, user_id: str, broker: str, session: Optional[dict], streaming: bool = False
    ) -> BrokerConnection:
        """Build the canonical user -> broker association record.

        The one place the three orthogonal facts about a broker connection are
        combined: whether the deployment is configured for it, whether this user
        has a live session, and whether that user's session has expired. Before
        D3 this was assembled inline in `BrokerEngine.get_status`, where no other
        code could construct or assert against it.

        Takes a decrypted session and returns a record containing no token
        material, which is what makes the result safe to log, publish on the
        Event Bus, and place in AI context.
        """
        adapter = self.resolve(broker)
        has_token = bool(session and session.get("access_token"))
        fresh = bool(has_token and adapter.session_is_fresh(session))
        profile = (session or {}).get("profile") or {}
        return BrokerConnection(
            user_id=str(user_id) if user_id is not None else None,
            broker=adapter.name,
            display_name=adapter.display_name,
            configured=adapter.is_configured(),
            connected=fresh,
            session_expired=has_token and not fresh,
            account_id=profile.get("user_id") or (session or {}).get("account_id"),
            connected_at=(session or {}).get("connected_at") if fresh else None,
            expires_at=(session or {}).get("expires_at") if fresh else None,
            last_sync=(session or {}).get("last_sync"),
            streaming=streaming,
            capabilities=sorted(c.value for c in adapter.capabilities),
        )

    def health(self, broker: str) -> Dict[str, Any]:
        """This worker's view of a broker's API health.

        Kept synchronous and kept as the local read: it is what a log line, a
        metric callback or any non-awaitable caller can ask for. A surface that
        reports to an operator wants :meth:`health_shared`.
        """
        return self.resolve(broker).health.as_dict()

    async def health_shared(self, broker: str) -> Dict[str, Any]:
        """The deployment's view of a broker's API health (D5.8).

        DB-1's read path for the Admin Portal. Before this, an operator asking
        whether a broker was up got the answer from whichever worker happened to
        serve the request, and two refreshes could disagree — which is the
        symptom that made health-driven automation unsafe to build on.
        """
        adapter = self.resolve(broker)
        await broker_health.refresh_shared(adapter.health)
        return adapter.health.as_dict()

    def diagnostics(self) -> Dict[str, Any]:
        """Full broker detail for admin surfaces and logs."""
        return {"brokers": self._registry.describe()}

    async def diagnostics_shared(self) -> Dict[str, Any]:
        """:meth:`diagnostics` over the shared health state (D5.8).

        One round trip for every broker rather than one each, so an admin page's
        cost does not grow with the broker list.
        """
        await broker_health.refresh_shared(
            *(adapter.health for adapter in self._registry.all())
        )
        return self.diagnostics()

    def reset_health(self) -> None:
        """Drop every broker's health. Startup and tests only."""
        for adapter in self._registry.all():
            adapter.health.reset()


def _counts_against_health(error: BrokerError) -> bool:
    """Whether an error is evidence about the broker's API availability.

    A refused order, an unsupported capability, an unreadable payload and an
    invalid request all say something about the request — not about whether the
    broker is up. Counting them would let a user placing ten malformed orders
    mark Zerodha as DOWN for everybody.
    """
    return error.code not in {
        BrokerErrorCode.REJECTED.value,
        BrokerErrorCode.UNSUPPORTED.value,
        BrokerErrorCode.UNKNOWN_BROKER.value,
        BrokerErrorCode.INVALID_REQUEST.value,
        BrokerErrorCode.NOT_CONFIGURED.value,
        BrokerErrorCode.CONTRACT.value,
    }


#: Module-level singleton.
broker_gateway = BrokerGateway()

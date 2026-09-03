"""Unified broker adapter contract (BROKER_INTEGRATION.md).

Every broker adapter implements this contract and returns the SAME canonical
shapes, so the Trading Engine, Portfolio Engine, AI and UI never know which
broker is connected. The canonical shapes themselves are defined and enforced in
`contracts.py`; the capability model in `capabilities.py`; the error vocabulary
in `errors.py`. This module is the contract an adapter implements — the Broker
Gateway is what calls it, and nothing else may.

WHAT D3 CHANGED HERE
--------------------
Three things, each removing a way the framework was Zerodha-and-Upstox-shaped
rather than broker-shaped:

1. **The fetch surface is capability-gated, not abstract.** Every account-data
   and order method used to be `@abstractmethod`, so a partial broker could only
   be integrated by writing stub methods that lie — either raising from a method
   that claims to exist, or returning `[]` from one that claims to have looked.
   Now an adapter declares :attr:`capabilities` and overrides exactly those; the
   defaults raise :class:`CapabilityUnsupported`, and the Broker Gateway refuses
   an unsupported capability before the adapter is ever reached. The registry
   verifies at registration that every declared capability is implemented, so
   the failure mode of a mistake is a startup error, not a 09:15 outage.

2. **Credentials are declared, not read.** Adapters used to call `os.environ`
   directly, and so did `BrokerEngine`, which reached for `KITE_API_KEY` by name
   to build a stream URL. An adapter now declares a
   :class:`~services.brokers.credentials.BrokerCredentialSpec` and everything
   else asks the adapter, which is what lets the engine open a broker stream
   without knowing whose credentials it is passing.

3. **Broker-specific defaults live on the adapter.** `server.py` chose an order
   product with `"CNC" if broker == "zerodha" else "D"` — a broker name in a
   core route, and one that silently gives every future broker Upstox's product
   code. :attr:`default_product` moves that answer to the only place that knows
   it.

WHAT AN ADAPTER IS
------------------
The only module allowed to speak one broker's protocol. It authenticates,
calls the broker's API, and maps the response onto the canonical shapes. It does
not persist anything, does not touch the Event Bus or the database, does not
decide policy (retry counts, sync scheduling, risk checks), and never imports a
core engine. Everything above it is the Broker Gateway's job.
"""
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from services.brokers.capabilities import BrokerCapability
from services.brokers.contracts import ORDER_STATUS
from services.brokers.credentials import (
    BrokerCredentials,
    BrokerCredentialSpec,
    resolve_credentials,
)
from services.brokers.errors import (
    BrokerAuthError,
    BrokerError,
    BrokerErrorCode,
    CapabilityUnsupported,
)
from services.brokers.health import BrokerHealth
from services.brokers.streaming import (
    DEFAULT_STREAM_CHANNEL,
    BrokerStreamChannel,
    BrokerStreamEndpoint,
    BrokerStreamEvent,
    StreamEventKind,
)

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Broker order APIs (Zerodha, Upstox) restrict order placement to whitelisted
# *IPv4* addresses. If the host has IPv6 connectivity, httpx will happily egress
# over IPv6 — and the broker rejects the order with "IP not allowed". Binding the
# outbound socket to an IPv4 local address forces IPv4 so the source IP matches
# the whitelisted static IP. Opt out with BROKER_FORCE_IPV4=false (e.g. IPv6-only
# hosts).
_FORCE_IPV4_ENV = "BROKER_FORCE_IPV4"


def _force_ipv4() -> bool:
    """Read the IPv4 pin at call time.

    Was a module-level constant evaluated at import. That made the setting
    untestable without reloading the module, and — more importantly — meant a
    deployment that sets it after the process reads its environment (a secrets
    mount, a config reload) silently kept the import-time answer.
    """
    return os.environ.get(_FORCE_IPV4_ENV, "true").strip().lower() in ("1", "true", "yes", "on")


def _broker_http_client(timeout: float) -> httpx.AsyncClient:
    """AsyncClient for broker HTTP calls, pinned to IPv4 egress when enabled."""
    if _force_ipv4():
        return httpx.AsyncClient(timeout=timeout,
                                 transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"))
    return httpx.AsyncClient(timeout=timeout)


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


def capability_stub(fn):
    """Mark a default implementation as a stub that only raises.

    The registry uses this mark to tell two kinds of inherited method apart, a
    distinction that matters more than it first appears. `get_holdings`'s
    default raises `CapabilityUnsupported` — inheriting it while declaring
    HOLDINGS is a broken adapter. `get_margins`'s default returns the funds
    payload, which is genuinely correct for every broker whose margins and funds
    come from one endpoint — inheriting *that* while declaring MARGINS is not a
    defect, it is reuse.

    Identity-comparing against the base class cannot distinguish them; a mark
    can, and putting the mark on the stub itself means a future default that
    stops raising automatically stops being treated as unimplemented.
    """
    fn._capability_stub = True
    return fn


def _safe_url(url: str) -> str:
    """Strip the query string before a URL reaches a log line.

    Not cosmetic. Kite's logout endpoint takes the access token *in the query
    string* (`DELETE /session/token?api_key=…&access_token=…`), so the one
    pre-D3 place that logged a raw broker URL — the 401/403 branch below —
    would write a live broker access token into the application log the moment
    a logout was rejected. SECURITY.md forbids credentials in logs outright,
    and the path is not hypothetical: a token already dead at the broker is
    exactly the case that returns 403 there.
    """
    return (url or "").split("?", 1)[0]


#: Which realtime capability entitles a channel to deliver which event kind.
#:
#: The inverse of `streaming.EVENT_CAPABILITY`, and derived from it rather than
#: written out a second time: the two disagreeing would mean a channel that
#: declares it delivers ticks while the gateway drops every one of them, which
#: reads in the logs exactly like a quiet market.
CAPABILITY_EVENTS = {
    BrokerCapability.TICK_STREAM: StreamEventKind.TICKS,
    BrokerCapability.ORDER_STREAM: StreamEventKind.ORDER,
}


class AdapterStreamChannel(BrokerStreamChannel):
    """The channel a broker whose realtime surface is ONE connection gets for free.

    Every adapter written before D4.7 declared its stream as five methods on
    itself — `stream_endpoint`, `stream_subscribe_frames`, `stream_connect_error`,
    `decode_stream_frame` — with no notion that there might be two of anything.
    This channel *is* that adapter, wrapped: it delegates each call straight
    back, so the one-socket contract those adapters were written against is
    preserved exactly rather than reimplemented beside it.

    That matters more than the line count suggests. The alternative — adding a
    `channel` argument to the five adapter methods — would have changed the
    signature every existing adapter and every test double implements, so a
    broker that had not been updated would fail at the first frame rather than
    at import, and the compatibility break would be discovered on a live socket.
    Here, a broker that knows nothing about channels *is* a single-channel
    broker, which is what it always was.

    :attr:`delivers` is read off the adapter's own capability declaration, so
    the free channel can deliver exactly what the broker says it serves and the
    per-channel narrowing is a no-op for a single-channel broker.
    """

    def __init__(
        self,
        adapter: "BrokerAdapter",
        name: str = DEFAULT_STREAM_CHANNEL,
        delivers: Optional[frozenset] = None,
    ) -> None:
        self._adapter = adapter
        self.name = name
        self.protocol = (getattr(adapter, "stream_protocol", "") or "").strip()
        # Derived from the broker's capabilities when not stated, which is right
        # for a single-channel broker: its one channel carries everything the
        # broker serves. A multi-channel broker must narrow explicitly — an
        # order channel that inherited TICKS from the adapter would claim to
        # carry a market feed it has no prices on, and the account's provider
        # would take its link state for the tick feed's.
        self.delivers = (
            frozenset(delivers)
            if delivers is not None
            else frozenset(event for capability, event in CAPABILITY_EVENTS.items() if adapter.supports(capability))
        )
        # D5.10. Read off the adapter for the same reason `protocol` is: a
        # single-channel broker states its facts on itself, and its one free
        # channel is that adapter wrapped. Both default to `None` on
        # `BrokerAdapter`, so a broker that has never heard of sharding gets the
        # single connection it has always had.
        self.max_instruments_per_connection = getattr(
            adapter, "stream_max_instruments_per_connection", None)
        self.max_connections = getattr(adapter, "stream_max_connections", None)

    def endpoint(self, session: dict, credentials: Dict[str, str] = None) -> BrokerStreamEndpoint:
        return self._adapter.stream_endpoint(session, credentials)

    def subscribe_frames(self, instruments: List[Any] = None) -> List[Any]:
        return self._adapter.stream_subscribe_frames(instruments)

    def connect_error(self, error: BaseException) -> Optional[Any]:
        return self._adapter.stream_connect_error(error)

    def decode(self, frame: Any) -> BrokerStreamEvent:
        return self._adapter.decode_stream_frame(frame)


class BrokerAdapter(ABC):
    """Abstract broker adapter.

    One instance per broker type, held by the :class:`BrokerRegistry`. Adapters
    are stateless with respect to users: per-user session tokens are passed into
    every call by the Broker Gateway, never stored on the adapter. The single
    piece of adapter state is :attr:`health`, which is about the broker's API
    rather than about any user.
    """

    #: Stable broker identifier — "zerodha", "upstox", … Appears in the
    #: registry, in `db.broker_accounts`, in audit logs and on `/api/brokers`.
    name: str = "base"

    #: Human-readable name for user-facing messages.
    display_name: str = "Broker"

    #: What this broker actually offers. The Broker Gateway refuses anything not
    #: in this set before calling the adapter, and the registry verifies at
    #: registration that every member here has an implementation.
    capabilities: frozenset = frozenset()

    #: Which environment variables carry this broker's credentials. Declared,
    #: never read here — see `credentials.py`.
    credential_spec: BrokerCredentialSpec = BrokerCredentialSpec()

    #: Product code used when the caller does not specify one. Zerodha's
    #: delivery product is "CNC", Upstox's is "D"; a new broker's is whatever it
    #: is, and no core route should have to know.
    default_product: str = "CNC"

    #: Order variety used when the caller does not specify one.
    default_variety: str = "regular"

    #: Which realtime wire protocol this broker's stream speaks. Separate from
    #: :attr:`name` for the same reason the market-data contract separates
    #: `normalizer_key` from the provider name: two brokers reselling the same
    #: vendor API share a transport, and a broker with no stream declares none.
    #: `stream.py` dispatches on this instead of on the broker's name, which is
    #: what stops a `if broker == ...` chain from growing there per broker.
    stream_protocol: str = ""

    #: How many instruments ONE of this broker's tick connections may carry, and
    #: how many such connections one account may hold — or `None` for either
    #: when the broker documents none (D5.10).
    #:
    #: Stated on the adapter rather than only on a channel because a
    #: single-channel broker's one channel *is* this adapter wrapped
    #: (`AdapterStreamChannel`), so this is where its author already states
    #: `stream_protocol` and everything else about its socket. A multi-channel
    #: broker states them per channel, where they belong: two feeds of one
    #: broker need not share a ceiling.
    #:
    #: **Declare a per-connection limit only.** A per-session quota and a
    #: per-frame limit are different facts that sharding cannot raise — see
    #: :attr:`~services.brokers.streaming.BrokerStreamChannel.max_instruments_per_connection`
    #: for what each one does to a feed if they are confused.
    stream_max_instruments_per_connection: Optional[int] = None
    stream_max_connections: Optional[int] = None

    def __init__(self) -> None:
        self.health = BrokerHealth(broker=self.name)

    # -- capabilities -------------------------------------------------------
    def supports(self, capability: BrokerCapability) -> bool:
        """Whether this broker offers `capability`."""
        return capability in self.capabilities

    def _unsupported(self, capability: BrokerCapability) -> CapabilityUnsupported:
        return CapabilityUnsupported(self.name, capability, self.display_name)

    # -- configuration -----------------------------------------------------
    @property
    def credentials(self) -> BrokerCredentials:
        """This broker's credentials, read fresh from the configuration source."""
        return resolve_credentials(self.name, self.credential_spec)

    def is_configured(self) -> bool:
        """True when this deployment has the credentials this broker requires.

        One implementation for every broker, derived from the declared spec.
        Adapters used to each write their own, which is three lines of identical
        code per broker and one more place for a broker to disagree with itself
        about what "configured" means.
        """
        return self.credentials.is_complete(self.credential_spec)

    # -- authentication ----------------------------------------------------
    @abstractmethod
    def get_login_url(self, state: str = None) -> dict:
        """OAuth login URL for the browser redirect flow.
        Returns {url, configured, message?}.

        ``state`` is an **opaque, single-use handle** minted by
        ``security.oauth_state`` and echoed back by the broker on the redirect.
        It is not, and must never again become, the app's user id.

        D6.1 / S1. This parameter used to be ``user_id``, and every adapter
        wrote it into the provider's echoed parameter as the literal string
        ``uid=<mongo object id>``. The public callback then read that value back
        and believed it, so rewriting one query parameter re-pointed a live
        brokerage authorization at any account in the system. The value an
        adapter puts on the wire is now meaningless to anyone who does not hold
        the server-side record it names, and adapters no longer know — and no
        longer need to know — which user is connecting.

        Abstract rather than capability-gated: a broker StockAssist cannot
        authenticate against is not a broker it can integrate at all, so there
        is no meaningful adapter that omits this.
        """

    @abstractmethod
    async def exchange_token(self, auth_payload: dict) -> dict:
        """Exchange the OAuth callback payload (request_token / code) for a
        session: {access_token, refresh_token?, expires_at, account_id, profile}."""

    @capability_stub
    async def refresh_session(self, session: dict) -> Optional[dict]:
        """Refresh the access token where the broker supports it.

        Indian retail broker APIs (Kite Connect, Upstox v2) issue daily tokens
        without a refresh grant, so the default returns None — meaning "not
        refreshable, reconnect required". Adapters for brokers with refresh
        tokens override this and declare
        :attr:`BrokerCapability.SESSION_REFRESH`.
        """
        return None

    @capability_stub
    async def invalidate_session(self, session: dict) -> None:
        """Explicitly log the session out at the broker.

        Default is a no-op for brokers with no logout endpoint. Declaring
        :attr:`BrokerCapability.SESSION_INVALIDATE` is what tells the engine a
        disconnect can actually revoke the token rather than merely forgetting
        it locally — a meaningful security difference the engine should not have
        to discover with `hasattr`.
        """
        return None

    def parse_callback_params(self, params: Dict[str, str]) -> Optional[dict]:
        """Turn the broker's OAuth redirect query into an `exchange_token` payload.

        Returns None when the user cancelled or the broker reported an error —
        the caller redirects to a "cancelled" outcome rather than attempting an
        exchange that cannot succeed.

        The default implements the standard OAuth2 authorization-code shape
        (`?code=` on success, `?error=` on failure), which is what Upstox and
        most brokers send. Zerodha overrides it because Kite answers with
        `?request_token=&status=`, which is its own convention.

        This exists because `server.py`'s public callback route used to branch
        `if broker == "zerodha": ... else:  # upstox`, where the `else` silently
        assumed every future broker speaks Upstox's dialect. Callback parsing is
        protocol knowledge and belongs to the adapter that owns the protocol.
        """
        params = params or {}
        if params.get("error") or not params.get("code"):
            return None
        return {"code": params.get("code")}

    @abstractmethod
    def session_expiry(self, connected_at: datetime) -> datetime:
        """When a session created at `connected_at` stops being valid."""

    def session_is_fresh(self, session: dict) -> bool:
        expires_at = (session or {}).get("expires_at")
        if not expires_at:
            return False
        try:
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            return datetime.now(timezone.utc) < expires_at.astimezone(timezone.utc)
        except Exception:
            return False

    # -- account data (all take the decrypted session dict) -----------------
    #
    # Every default raises. An adapter that declares a capability and forgets to
    # implement it is caught at registration; an adapter reached for a
    # capability it never declared has bypassed the gateway, which is a
    # programming error and should read like one.

    @capability_stub
    async def get_profile(self, session: dict) -> dict:
        raise self._unsupported(BrokerCapability.PROFILE)

    @capability_stub
    async def get_holdings(self, session: dict) -> list:
        raise self._unsupported(BrokerCapability.HOLDINGS)

    @capability_stub
    async def get_positions(self, session: dict) -> list:
        raise self._unsupported(BrokerCapability.POSITIONS)

    @capability_stub
    async def get_funds(self, session: dict) -> dict:
        raise self._unsupported(BrokerCapability.FUNDS)

    async def get_margins(self, session: dict) -> dict:
        """Margin details. Defaults to the funds payload (same source for
        Kite/Upstox); adapters may override with richer data."""
        return await self.get_funds(session)

    @capability_stub
    async def get_orders(self, session: dict) -> list:
        raise self._unsupported(BrokerCapability.ORDERS)

    @capability_stub
    async def get_trades(self, session: dict) -> list:
        """Executed trades for the day (trade history)."""
        raise self._unsupported(BrokerCapability.TRADES)

    # -- order management ---------------------------------------------------
    @capability_stub
    async def place_order(self, session: dict, order: dict) -> dict:
        """Place an order. `order` uses canonical fields:
        {symbol, exchange, transaction_type, quantity, order_type, product,
         price?, trigger_price?, validity?, tag?}
        Returns {order_id, status}."""
        raise self._unsupported(BrokerCapability.PLACE_ORDER)

    @capability_stub
    async def modify_order(self, session: dict, order_id: str, changes: dict) -> dict:
        raise self._unsupported(BrokerCapability.MODIFY_ORDER)

    @capability_stub
    async def cancel_order(self, session: dict, order_id: str) -> dict:
        raise self._unsupported(BrokerCapability.CANCEL_ORDER)

    # -- realtime -----------------------------------------------------------
    def stream_credentials(self) -> Dict[str, str]:
        """Credential material the stream transport needs to open a connection.

        Kite's ticker authenticates by query string (`api_key` + `access_token`)
        while Upstox uses a bearer header, so the transport needs different
        material per broker. Returning it from the adapter is what lets
        `BrokerEngine.start_stream` open a stream for any broker without naming
        a single environment variable — it used to read `KITE_API_KEY` directly.
        """
        return {"api_key": self.credentials.api_key}

    @capability_stub
    def stream_instruments(self, holdings: list = None, positions: list = None) -> List[Any]:
        """Instrument identifiers to subscribe for price ticks.

        Default is empty: a broker whose feed carries only order updates
        subscribes to nothing. Zerodha overrides it to derive Kite instrument
        tokens from the portfolio. This replaced an `if broker == "zerodha"`
        branch inside the engine — the same logic, moved to the only module
        entitled to know what an instrument identifier looks like at this broker.
        """
        return []

    #: Build this broker's `{(EXCHANGE, SYMBOL): identifier}` index from raw
    #: master rows. Overridden by every adapter that declares
    #: `INSTRUMENT_CATALOGUE`; a plain function of its inputs, so the whole of
    #: an adapter's catalogue *meaning* is testable without a network.
    @staticmethod
    def build_catalogue_index(*row_groups) -> Dict[Tuple[str, str], Any]:
        """`{(EXCHANGE, SYMBOL): broker identifier}` from instrument-master rows."""
        return {}

    @capability_stub
    async def resolve_instruments(
        self, instruments: "Sequence[Any]", session: dict = None
    ) -> Dict[str, Any]:
        """Canonical instrument -> this broker's own instrument identifier.

        The adapter-side half of the instrument catalogue. `instruments` are
        :class:`~services.brokers.feed_universe.FeedInstrument` values — a
        canonical symbol, an exchange and a segment — and the returned
        identifiers are whatever this broker's feed subscribes by: an opaque
        integer, a compound string, an exchange-qualified key. The Market Engine
        never sees either side of the mapping; it asks for a *universe of
        instruments* and the broker layer answers with something only that
        broker can interpret.

        D5.16 WIDENED THE INPUT FROM A SYMBOL TO AN INSTRUMENT
        -------------------------------------------------------
        D5.15 took `Sequence[str]`. A bare symbol cannot say which listing it
        means, and `RELIANCE` is two instruments with two identifiers at every
        one of the five brokers (verified against their published masters). The
        single implementation was an NSE-only master, so a BSE request was
        answered with the NSE key — silently, and with the account then marked
        at the wrong listing's price. Widening the input is what makes that
        state unrepresentable rather than merely unlikely.

        WHY THIS IS A CAPABILITY AND NOT A REQUIRED METHOD
        ---------------------------------------------------
        Resolving a symbol needs an instrument master, a search endpoint, or a
        static table, and not every broker publishes one. A broker without it is
        not broken: it keeps the pre-D5.15 behaviour of covering exactly what
        the account holds, because holdings and positions carry their own
        identifiers. Declaring the capability is what says "this broker's feed
        can be aimed at an instrument the account does not own".

        Partial answers are correct and expected. A symbol this broker cannot
        name is **omitted** rather than mapped to a sentinel: an unmapped
        instrument must disappear from the subscription, not enter it as a key
        the wire will reject and take the whole subscribe frame down with it.

        Returns `{}` from here rather than raising, unlike most stubs, because
        the gateway already gates the call on the capability — see
        :meth:`BrokerGateway.resolve_instruments`, which never reaches an
        adapter that has not declared it.
        """
        return {}

    @capability_stub
    def normalize_stream_order(self, payload: dict) -> dict:
        """Map a streamed order-update frame onto the canonical order shape.

        Part of the ORDER_STREAM capability rather than a private helper, which
        is what let `stream.py` stop importing `ZerodhaAdapter` and
        `UpstoxAdapter` by name to normalize a frame. The transport now asks the
        registry for whichever adapter owns the connection and calls this.
        """
        raise self._unsupported(BrokerCapability.ORDER_STREAM)

    # -- realtime: the codec boundary (D4.2) ----------------------------------
    #
    # These three methods are the entire wire-format surface of a broker stream,
    # and they are the reason `stream.py` no longer contains one. Before D4.2 a
    # shared module held Kite's ticker URL, Kite's subscribe frames, Kite's
    # binary packet layout and Upstox's JSON envelope — so adding a streaming
    # broker meant editing code no broker owns, and the tick shape that came out
    # was whatever that broker's parser happened to build.
    #
    # The transport is now generic and these are broker-owned: connect where the
    # adapter says, send what the adapter says, and hand every frame to the
    # adapter to decode. What comes back is a `BrokerStreamEvent` built from
    # canonical types, which is what stops a raw broker payload from continuing
    # up into the engine.

    def stream_channels(self) -> Tuple[BrokerStreamChannel, ...]:
        """Every connection this broker's realtime surface needs (D4.7).

        The default is the one this adapter has always described: a single
        channel backed by :meth:`stream_endpoint`, :meth:`stream_subscribe_frames`,
        :meth:`stream_connect_error` and :meth:`decode_stream_frame`. A broker
        that has never heard of channels therefore keeps working unchanged, and
        Kite — whose ticker multiplexes binary ticks and JSON order updates onto
        one socket — needs no override at all.

        A broker overrides this when its realtime surface is genuinely more than
        one connection. Upstox is the case that forced the concept to exist: its
        order updates arrive on the v2 portfolio stream and its market ticks on
        a separate v3 feed with a different host, a different encoding and a
        different subscription model. Overriding here is how it says so; nothing
        in the transport, the Market Gateway, the Source Manager or the provider
        registry learns that a broker did.

        Returns `()` for a broker with no stream at all, which is what stops the
        engine opening a connection that could only fail.
        """
        if not (self.stream_protocol or "").strip():
            return ()
        return (AdapterStreamChannel(self),)

    @capability_stub
    def stream_endpoint(self, session: dict, credentials: dict = None) -> BrokerStreamEndpoint:
        """Where to connect for this user's stream, and how to authenticate.

        `credentials` is what :meth:`stream_credentials` returned, passed back
        rather than re-read so the transport holds the material and the adapter
        stays free of environment access.

        Auth style is protocol knowledge and differs per broker in a way no
        shared module can generalise: a query string here, a bearer header
        there, a negotiated subprotocol at the next one. Anything credential-
        bearing that ends up in the URL is protected by
        :attr:`BrokerStreamEndpoint.safe_url`, which is the only form the
        transport logs.
        """
        raise self._unsupported(BrokerCapability.TICK_STREAM)

    def stream_subscribe_frames(self, instruments: list = None) -> List[Any]:
        """Frames to send immediately after connecting, in order.

        Returns strings or bytes ready for the socket — the adapter decides both
        the encoding and how many frames it takes, because "subscribe" is not
        one shape across brokers: Kite sends a subscribe frame and then a
        separate mode frame, and a broker that subscribes by URL sends none.

        The default is no frames, which is correct for a feed that pushes the
        account's updates without being asked (every order-only stream).
        """
        return []

    def stream_connect_error(self, error: BaseException) -> Optional[Any]:
        """Whether a failed stream *connection* means this session cannot recover.

        Returns a human-readable reason when the session is dead, and `None` —
        the default — when the failure is ordinary connection weather the
        transport should retry through its normal backoff.

        Since D5.5 it may instead return a terminal
        :class:`~services.brokers.streaming.BrokerStreamEvent` when the broker's
        rejection distinguishes an expired session from a **refused
        entitlement**: `BrokerStreamEvent.not_entitled(reason)` stops this feed
        without touching the account's session, where a reason string still means
        the session is finished. A 403 is the case that makes the distinction
        worth having — it is "your token is no longer accepted" at one broker and
        "this account is not licensed for this data" at another, and only the
        adapter knows which. Returning a string remains correct and is what every
        adapter written before D5.5 does.

        WHY THIS IS A SEPARATE HOOK FROM `decode_stream_frame`
        -------------------------------------------------------
        `decode_stream_frame` can only classify a failure the broker reports *in
        a frame*, which means a connection that was established. Some brokers
        reject a dead session during the WebSocket handshake instead, so no
        frame is ever decoded and the transport sees only "connect raised". Left
        unclassified, an expired token is indistinguishable from a broker
        outage: the stream reconnects on the backoff schedule forever, the
        account's market feed stays registered, and the user is never told to
        reconnect.

        The interpretation is the adapter's because only the adapter knows what
        its broker's rejection looks like. What happens next stays generic — the
        transport raises its own terminal signal, and the matching path (stop the
        stream, detach the market feed, notify) runs unchanged. Adapters must not
        act on the error themselves.

        **Never infer either condition from silence.** A handshake that times
        out, a socket that opens and closes, a subscription that yields no data
        — none of them is a statement the broker made, and classifying one as
        terminal permanently stops a feed that may be working.
        """
        return None

    @capability_stub
    def decode_stream_frame(self, frame: Any) -> BrokerStreamEvent:
        """Decode ONE raw frame into a canonical :class:`BrokerStreamEvent`.

        The only code in the platform entitled to see a raw broker frame, and
        the reason for the whole module: whatever this returns is the most
        broker-shaped thing anything above the adapter will ever hold, and the
        transport type-checks it, so a payload cannot be passed through by
        accident.

        `frame` is `bytes` or `str` exactly as the socket produced it — brokers
        mix both on one connection (Kite carries ticks in binary and orders in
        JSON text), so splitting this into two methods would encode one broker's
        framing into the contract.

        Must not raise for a frame it does not understand: return
        :meth:`BrokerStreamEvent.ignore`. Heartbeats, keep-alives and update
        types the platform does not consume are the normal case, not an error,
        and a codec that raises on them fills the log with noise from a working
        connection.
        """
        raise self._unsupported(BrokerCapability.TICK_STREAM)

    # -- health --------------------------------------------------------------
    async def health_check(self, session: dict) -> dict:
        """Cheap authenticated call to verify one user's session is alive.

        Distinct from :attr:`health`, which is the broker's API availability
        across all users. This answers "is this token still good"; that answers
        "is the broker up".
        """
        if not self.supports(BrokerCapability.PROFILE):
            return {"healthy": False, "reason": "unsupported"}
        try:
            profile = await self.get_profile(session)
            return {"healthy": True, "profile": profile}
        except BrokerAuthError:
            return {"healthy": False, "reason": "auth"}
        except Exception as e:
            return {"healthy": False, "reason": str(e)}

    # -- introspection --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """Diagnostic snapshot of the broker itself — no user, no session."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "configured": self.is_configured(),
            "capabilities": sorted(c.value for c in self.capabilities),
            "default_product": self.default_product,
            "health": self.health.as_dict(),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r}>"

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
            # Coded TIMEOUT rather than the generic default so retry policy can
            # be derived from the code (errors.py) instead of re-decided at each
            # call site. A timeout is retryable; a rejection is not.
            raise BrokerError(f"{self.display_name} API timeout",
                              user_message=f"{self.display_name} did not respond in time. Please retry.",
                              code=BrokerErrorCode.TIMEOUT.value, broker=self.name)
        except httpx.HTTPError as e:
            raise BrokerError(f"{self.display_name} network error: {e}",
                              user_message=f"Could not reach {self.display_name}. Check your connection and retry.",
                              code=BrokerErrorCode.NETWORK.value, broker=self.name)
        if resp.status_code in (401, 403):
            detail, error_type = self._extract_broker_error(resp)
            logger.warning("%s %s %s -> %s [%s] %s", self.display_name, method, _safe_url(url),
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
                              user_message=detail, code=BrokerErrorCode.REJECTED.value,
                              broker=self.name)
        if resp.status_code == 429:
            raise BrokerError(f"{self.display_name} rate limit hit",
                              user_message=f"{self.display_name} rate limit reached. Please wait a moment.",
                              code=BrokerErrorCode.RATE_LIMIT.value, broker=self.name)
        try:
            payload = resp.json()
        except Exception:
            raise BrokerError(f"{self.display_name} returned non-JSON response ({resp.status_code})",
                              user_message=f"{self.display_name} returned an unexpected response.",
                              code=BrokerErrorCode.CONTRACT.value, broker=self.name)
        return payload

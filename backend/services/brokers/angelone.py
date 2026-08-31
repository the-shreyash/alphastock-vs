"""Angel One SmartAPI adapter — the THIRD streaming broker (D4.9).

Official docs: https://smartapi.angelone.in/docs (User, Portfolio, WebSocket 2.0)
Official SDK:  https://github.com/angel-one/smartapi-python (`SmartApi/smartWebSocketV2.py`)

Auth model: the public **publisher login** — the user is sent to SmartAPI's own
login page and comes back to this platform's registered redirect URL carrying an
`auth_token` and a `feed_token` as query parameters. The alternative SmartAPI
flow (`loginByPassword` with client code + PIN + TOTP) is deliberately NOT used:
it would require this platform to hold a user's trading PIN and TOTP secret,
which SECURITY.md forbids and which no OAuth-style broker on this platform
needs. A SmartAPI session stays valid until midnight IST unless the user logs
out.

WHAT IS DIFFERENT ABOUT THIS BROKER, AND WHY IT MATTERS
--------------------------------------------------------
D4.7 asked whether the streaming architecture had generalised or had merely
worked twice. Angel One is the third answer, and it disagrees with both of its
predecessors on every axis that has ever mattered here:

    Kite                     Upstox v3                Angel One smart-stream
    ────                     ─────────                ──────────────────────
    one socket, both feeds   two sockets              one socket, ticks only
    bespoke binary framing   protobuf                 fixed 51-byte packets
    frame = many packets     frame = many feeds       frame = ONE tick
    big-endian               protobuf varint          LITTLE-endian
    int token (32-bit)       "NSE_EQ|INE002A01018"    numeric token IN A STRING,
                                                     unique only per exchange
    paise, 3 segment scales  IEEE double, rupees      paise, currency ×10⁷
    query-string auth        bearer header            FOUR auth headers
    2 subscribe frames, text 1 frame, JSON as binary  1 frame, JSON as text
    protocol ping suffices   protocol ping suffices   application "ping" or the
                                                     broker closes the socket

Only the last of those needed anything outside this file, and what it needed is
a generic transport capability rather than an Angel One branch — see
`BrokerStreamEndpoint.heartbeat_frame` and `stream.py`. Everything else on the
list is decoded, priced and named below, and stops here.

WHAT LEAVES THIS MODULE
-----------------------
A :class:`~services.brokers.streaming.BrokerTick` carrying the account's own
exchange-qualified instrument id, which `InstrumentMap` turns into a canonical
symbol exactly as it does for Kite's integer and Upstox's compound key. No
SmartAPI vocabulary — no exchange type, no subscription mode, no packet offset,
no token — exists anywhere else in the platform.
"""
import json
import logging
import struct
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

from services.brokers.base import (
    IST, BrokerAdapter, BrokerAuthError, BrokerError, _broker_http_client,
)
from services.brokers.capabilities import BrokerCapability
from services.brokers.catalogue import (
    INDEX_SEGMENT,
    CatalogueCache,
    InstrumentCatalogue,
    canonical_index,
    normalize_exchange,
    resolve_from_index,
    series_rank,
)
from services.brokers.credentials import BrokerCredentialSpec
from services.brokers.errors import BrokerErrorCode
from services.brokers.streaming import BrokerStreamEndpoint, BrokerStreamEvent

logger = logging.getLogger(__name__)

BASE_URL = "https://apiconnect.angelone.in"
#: The public login page the user is redirected to. Not an API endpoint.
LOGIN_URL = "https://smartapi.angelone.in/publisher-login"

#: SmartAPI WebSocket 2.0 — the market feed.
#:
#: Nothing credential-bearing is in this URL: all four authentication values go
#: in headers (see :meth:`AngelOneAdapter.stream_endpoint`), so
#: `BrokerStreamEndpoint.safe_url` has nothing to strip — the opposite of Kite's
#: ticker, whose URL carries a live access token. SmartAPI *also* documents a
#: query-string form (`?clientCode=&feedToken=&apiKey=`) for browser clients
#: that cannot set headers; it is deliberately not used here, because it would
#: put two live credentials into a URL that every connection log line names.
WS_URL = "wss://smartapisocket.angelone.in/smart-stream"

# ── The subscription protocol (WebSocket 2.0 request contract) ──────────────

#: `action` values. Only SUBSCRIBE is sent: the platform re-subscribes by
#: reconnecting, and D5 owns incremental subscription changes.
ACTION_SUBSCRIBE = 1

#: The feed mode this adapter subscribes in.
#:
#: LTP (1) deliberately, and — as with Kite's `STREAM_MODE` and Upstox's
#: `MARKET_STREAM_MODE` — recorded here rather than inline because it is the one
#: protocol decision with a product consequence. It is NOT copied from either:
#: SmartAPI's modes are its own, and the reasoning was made against what each
#: one actually carries.
#:
#: The tick feed marks portfolio holdings and open trades and answers streamed
#: quotes; all three need a last traded price. Quote (2) adds day OHLC, average
#: price, cumulative volume and aggregate buy/sell quantity — 123 bytes per tick
#: against 51 — and Snap Quote (3) adds the best-five book, open interest,
#: circuit limits and 52-week range at 379 bytes. Depth (4) is 20-level book,
#: NSE only, on a separate 50-token quota.
#:
#: What it costs, stated plainly: an Angel-One-derived `MarketTick` carries no
#: volume, because an LTP packet has none. That is the same limitation Kite's
#: LTP mode and Upstox's `ltpc` mode have, reached independently for the third
#: time, and it is recorded in TASK.md rather than papered over.
STREAM_MODE_LTP = 1

#: SmartAPI exchange segments, both directions.
#:
#: The subscription addresses instruments by `(exchangeType, token)` and the
#: tick returns the same pair, so a token alone is NOT an identity: SmartAPI
#: token numbers are unique within an exchange segment and nothing more. That is
#: the single most important protocol fact in this file and the reason
#: :func:`instrument_id` exists.
#:
#: Segment ids are SmartAPI's own (`1 nse_cm, 2 nse_fo, 3 bse_cm, 4 bse_fo,
#: 5 mcx_fo, 7 ncx_fo, 13 cde_fo`). The names on the left are the exchange
#: strings SmartAPI's own REST responses use for the same segments, which is
#: what a synced holding or position row carries.
EXCHANGE_TYPES: Dict[str, int] = {
    "NSE": 1,
    "NFO": 2,
    "BSE": 3,
    "BFO": 4,
    "MCX": 5,
    "NCDEX": 7,
    "NCO": 7,
    "CDS": 13,
}
EXCHANGE_NAMES: Dict[int, str] = {1: "NSE", 2: "NFO", 3: "BSE", 4: "BFO", 5: "MCX", 7: "NCDEX", 13: "CDS"}

#: SmartAPI's public scrip master — every instrument on every exchange, served
#: unauthenticated. The same file Angel One's own SDK downloads.
INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)

#: How long a fetched master stays authoritative — one trading day's worth.
INSTRUMENT_MASTER_TTL_SECONDS = 6 * 60 * 60

#: The `instrumenttype` a SmartAPI cash-market row carries: the empty string.
#:
#: Derivatives carry `OPTSTK`/`FUTSTK`/`OPTIDX`…, indices carry `AMXIDX`, and
#: cash rows carry nothing at all. That is the master's own discriminator and it
#: is what keeps an index out of an equity catalogue — verified against the live
#: file on 2026-08-31, where `Nifty 50` is `AMXIDX` and `RELIANCE-EQ` is `""`.
INSTRUMENT_CASH_TYPE = ""

#: The `instrumenttype` SmartAPI gives an index row (D5.17).
#:
#: Its own discriminator, and a positive one rather than the absence the cash
#: rows are identified by — which is what lets the index branch be additive
#: without touching the equity filter. Verified against the live master on
#: 2026-08-31: `NIFTY` (99926000, NSE), `BANKNIFTY` (99926009, NSE),
#: `INDIA VIX` (99926017, NSE), `SENSEX` (99919000, BSE).
#:
#: The `name` column is read, not `symbol`: SmartAPI's `symbol` for the Nifty is
#: `"Nifty 50"` while its `name` is `"NIFTY"`, and the same row pair holds for
#: Bank Nifty. Both spellings are in `catalogue.INDEX_ALIASES` anyway — the
#: column choice is about reading the field the master means as the identity,
#: exactly as the equity branch reads `symbol` for its series suffix.
INSTRUMENT_INDEX_TYPE = "AMXIDX"

#: Token subscriptions one SmartAPI socket may hold.
#:
#: Documented as a per-session quota rather than a per-request one, and counted
#: per token *and mode*: subscribing one instrument in three modes spends three.
#: This adapter subscribes one mode, so the quota is instrument count. Enforced
#: here for the same reason Upstox's 5,000-key limit is — an over-quota request
#: costs the account instruments it asked for, and a deterministic prefix with a
#: warning is strictly better than a feed that is quietly narrower than the
#: portfolio. A retail holdings-and-positions universe is nowhere near this.
MAX_SUBSCRIBED_INSTRUMENTS = 1000

#: SmartAPI allows three concurrent sockets per client code. This adapter opens
#: ONE (see :meth:`AngelOneAdapter.stream_channels`), so a user connecting the
#: same account from their own tooling still has headroom. Recorded because it
#: is the constraint any future sharding work (D5) has to fit inside.
MAX_CONCURRENT_CONNECTIONS = 3

# ── The keep-alive ──────────────────────────────────────────────────────────

#: SmartAPI requires the *text* frame `ping` on the data channel every 30
#: seconds and answers `pong`. The WebSocket protocol's own ping frames do not
#: satisfy it — see `BrokerStreamEndpoint.heartbeat_frame` for why that
#: distinction needed a contract field rather than a background task in here.
HEARTBEAT_FRAME = "ping"
HEARTBEAT_RESPONSE = "pong"

#: Sent every 20s against a 30s requirement. The margin is deliberate: a timer
#: that fires exactly at the deadline arrives late under any scheduling delay,
#: GC pause or slow send, and the cost of being late is the broker closing a
#: working connection. The cost of being early is one small frame every twenty
#: seconds.
HEARTBEAT_INTERVAL = 20.0

# ── SmartAPI binary tick framing (WebSocket 2.0 response contract) ──────────
#
# One frame is ONE tick — not a batch. Kite packs hundreds of packets behind a
# count header and Upstox returns a map of feeds; SmartAPI publishes one packet
# per token-and-mode, so the batching machinery above this module (a TICKS event
# carries a tuple) is simply used with one element.
#
# Layout, little-endian, shared by the LTP / Quote / Snap Quote modes for the
# first 51 bytes — which is the whole reason reading only those is safe:
#
#     offset  size  field
#     0       1     subscription mode      (1 LTP, 2 Quote, 3 Snap Quote)
#     1       1     exchange type          (see EXCHANGE_TYPES)
#     2       25    token, NUL-terminated ASCII
#     27      8     sequence number        (int64)
#     35      8     exchange timestamp     (int64, epoch milliseconds)
#     43      8     last traded price      (int64, scaled — see PRICE_SCALES)
#
# LTP mode ends at 51 bytes; Quote continues to 123 and Snap Quote to 379. Depth
# mode (4) reuses the header but replaces everything from offset 43, so it is
# refused rather than read — this adapter never subscribes it, and decoding one
# as a price would invent a number out of a quantity.

MODE_OFFSET = 0
EXCHANGE_OFFSET = 1
TOKEN_OFFSET = 2
TOKEN_BYTES = 25
TIMESTAMP_OFFSET = 35
PRICE_OFFSET = 43
#: The smallest priceable packet: through the last-traded-price field.
LTP_PACKET_BYTES = 51

#: Modes whose first 51 bytes are the layout above. Depth (4) is excluded.
PRICEABLE_MODES = frozenset({1, 2, 3})

#: Paise. Equities, derivatives, commodities and indices.
DEFAULT_PRICE_SCALE = 100.0
#: "For currencies, the price values should be divided by 10000000.0 to obtain
#: four decimal places" — SmartAPI's WebSocket 2.0 payload contract. Applying
#: the default here would price a currency instrument five orders of magnitude
#: wrong: not obviously wrong on a chart, and marked against a real position.
#: This is the same *class* of trap as Kite's segment scales and a different
#: rule; copying that adapter's divisor table would be wrong in both directions.
#: The names here deliberately avoid Kite's (`price_divisor`), which answers a
#: different question from a different input — the sweep that keeps one broker's
#: vocabulary inside its own adapter is right to insist on the distinction.
PRICE_SCALES: Dict[int, float] = {13: 10_000_000.0}

#: SmartAPI error codes that mean "this session is finished, log in again",
#: taken from SmartAPI's own client behaviour rather than guessed.
DEAD_SESSION_CODES = frozenset({"AG8001", "AG8002", "AG8003", "AB8050"})

#: NSE/BSE cash-market *series* suffixes on a SmartAPI trading symbol.
#:
#: SmartAPI names an equity `"TATASTEEL-EQ"` where Kite names it `"TATASTEEL"`.
#: Left alone, a user holding one stock at two brokers would hold two different
#: canonical symbols — a split portfolio, a split watchlist, and a market feed
#: whose coverage never matches the platform's own instrument universe. The
#: suffix is therefore stripped at this boundary, which is the only place
#: entitled to know it is a series code and not part of the name.
#:
#: Only these documented cash series are stripped, and only when there is a name
#: left over. A derivative symbol (`"NIFTY30JAN2523500CE"`) carries no hyphen
#: and is untouched.
CASH_SERIES_SUFFIXES = frozenset({"EQ", "BE", "BZ", "BL", "SM", "ST", "IQ", "GB", "GS"})


def trading_symbol(value: Any) -> Optional[str]:
    """A SmartAPI trading symbol as the platform names instruments.

    `"TATASTEEL-EQ"` → `"TATASTEEL"`; `"NIFTY30JAN2523500CE"` → unchanged.
    """
    text = "" if value is None else str(value).strip().upper()
    if not text:
        return None
    head, sep, tail = text.rpartition("-")
    if sep and head and tail in CASH_SERIES_SUFFIXES:
        return head
    return text


def exchange_type(exchange: Any) -> Optional[int]:
    """SmartAPI's numeric segment for an exchange name, or None if not one."""
    if exchange is None or isinstance(exchange, bool):
        return None
    return EXCHANGE_TYPES.get(str(exchange).strip().upper())


def instrument_id(exchange: Any, token: Any) -> Optional[str]:
    """This account's handle for one SmartAPI instrument: `"<segment>|<token>"`.

    WHY A COMPOUND IDENTIFIER AND NOT THE TOKEN
    --------------------------------------------
    SmartAPI tokens are unique **within an exchange segment**, not across them:
    NSE token 2885 is Reliance and BSE token 2885 is something else entirely.
    The subscription says `(exchangeType, tokens)` and every tick returns the
    pair, so the pair is the identity — and `InstrumentMap` matches one value.
    Storing the bare token would let a BSE tick be resolved to an NSE holding,
    marking a position at another instrument's price. Nothing raises; the number
    is simply wrong.

    Both directions are built here, from the same function, so the value written
    onto a synced holding row and the value a decoded tick carries cannot drift
    apart: they are one expression.

    Returns None for anything that is not a usable pair. A rejected instrument is
    absent from the subscribe frame rather than corrupting it — SmartAPI rejects
    a malformed *subscription* rather than the offending entry, so a single bad
    row would otherwise cost the account every price it asked for.
    """
    segment = exchange_type(exchange)
    if segment is None:
        return None
    if token is None or isinstance(token, bool):
        return None
    text = str(token).strip()
    if not text or not text.isdigit() or int(text) <= 0:
        return None
    return f"{segment}|{int(text)}"


def parse_instrument_id(value: Any) -> Optional[Tuple[int, str]]:
    """`"1|2885"` → `(1, "2885")`, or None when it is not one of ours."""
    if value is None or isinstance(value, bool):
        return None
    segment, sep, token = str(value).strip().partition("|")
    if not sep or not segment.strip().isdigit() or not token.strip().isdigit():
        return None
    segment_id = int(segment)
    # Token 0 is rejected for the same reason `instrument_id` rejects it: it is
    # what an absent identifier coerces to, and SmartAPI numbers instruments
    # from 1. Accepting it would put a subscription for nothing on the wire.
    if segment_id not in EXCHANGE_NAMES or int(token) <= 0:
        return None
    return segment_id, str(int(token))


def segment_scale(segment: int) -> float:
    """The scale SmartAPI quotes this exchange segment at.

    Named for the segment rather than for the price because Kite has a function
    called `price_divisor` that answers a different question from a different
    input (its scale is encoded in the low byte of the instrument token, not
    carried as a field), and the sweep that keeps Kite's vocabulary inside
    Kite's adapter is right to flag the collision.
    """
    return PRICE_SCALES.get(segment, DEFAULT_PRICE_SCALE)


def decode_tick(payload: bytes) -> Optional[Dict[str, Any]]:
    """Decode ONE SmartAPI binary packet into a raw tick dict, or None.

    `None` — not an exception — for every frame that is not a priceable tick:
    a short frame, a control frame, an unsubscribed mode, an exchange segment
    this adapter does not map, an empty token. A feed's normal traffic includes
    all of those, and a codec that raised on them would fill the log with noise
    from a healthy connection.

    Returns the pre-canonical dict `BrokerTick.from_broker` coerces, never a
    tick object: building the canonical type is the caller's step, so this
    function stays a pure reading of the wire.
    """
    if len(payload) < LTP_PACKET_BYTES:
        return None
    mode = payload[MODE_OFFSET]
    if mode not in PRICEABLE_MODES:
        # Depth mode's bytes past the header are a book, not a price. Reading
        # them as one would publish a quantity as a rupee value.
        return None
    segment = payload[EXCHANGE_OFFSET]
    exchange = EXCHANGE_NAMES.get(segment)
    if exchange is None:
        # A segment this adapter cannot name is also one it cannot price, since
        # the divisor is per segment. Dropped rather than defaulted.
        return None

    raw_token = bytes(payload[TOKEN_OFFSET:TOKEN_OFFSET + TOKEN_BYTES])
    token = raw_token.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
    if not token.isdigit():
        return None

    (timestamp,) = struct.unpack_from("<q", payload, TIMESTAMP_OFFSET)
    (scaled_price,) = struct.unpack_from("<q", payload, PRICE_OFFSET)
    identity = instrument_id(exchange, token)
    if identity is None:
        return None
    return {
        "instrument_token": identity,
        "last_price": scaled_price / segment_scale(segment),
        "exchange": exchange,
        # LTP mode carries no volume, and `ltq`/`vtt` do not appear until the
        # wider modes. Left absent rather than filled with a number that means
        # something else — see STREAM_MODE_LTP.
        "volume": 0,
        "timestamp": str(timestamp) if timestamp else None,
    }


def _session_refused(error: BaseException) -> Optional[str]:
    """Reason string when SmartAPI refused a stream handshake for a dead session.

    SmartAPI rejects the WebSocket handshake with **HTTP 401** and an
    `x-error-message` header naming which of the four credentials was bad —
    before a frame is exchanged, so the codec never sees it. Left unclassified,
    the generic transport cannot tell it from a broker outage: it reconnects on
    the backoff schedule indefinitely, the account's market feed stays
    registered, and the user is never asked to reconnect. Since a SmartAPI
    session dies at midnight IST, that is every connected user, every day.

    403 is included for the same reason Upstox's classifier includes it: a
    withdrawn app authorisation is equally unrecoverable by reconnecting, and
    equally fixed by the user reconnecting the account.

    The broker's own `x-error-message` is deliberately NOT read into the message
    shown to the user: it names the failing credential, which is a detail about
    our request rather than an action they can take.
    """
    status = getattr(error, "status_code", None)
    if status is None:
        # websockets >= 14 wraps the handshake response instead.
        status = getattr(getattr(error, "response", None), "status_code", None)
    if status in (401, 403):
        return (
            f"Angel One refused the stream handshake (HTTP {status}) — the session is no longer "
            "valid. SmartAPI sessions end at midnight IST; please reconnect."
        )
    return None


class AngelOneAdapter(BrokerAdapter):
    """Angel One SmartAPI.

    Nothing in this module is referenced by name anywhere outside it and the
    registry entry in `__init__.py` — the property the framework's source sweeps
    assert for every broker and now assert for a third.
    """

    name = "angelone"
    display_name = "Angel One"

    #: The account surface plus the market feed. What is deliberately absent is
    #: as much a declaration as what is present:
    #:
    #: * **No order capabilities.** D4.9 is a market-data sprint, and SmartAPI's
    #:   order surface is unvalidated against a live account here. The
    #:   capability model exists exactly so a partial broker is *declared*
    #:   partial rather than integrated with stub methods that lie — the Broker
    #:   Gateway refuses an undeclared capability before the adapter is reached,
    #:   and the UI can say so. Adding them later is an adapter change and
    #:   nothing else.
    #: * **No ORDER_STREAM.** SmartAPI serves order updates on a *different*
    #:   socket (`smart-order-update`), which would be a second channel — the
    #:   D4.7 mechanism is ready for it, and it belongs with the order surface.
    #: * **No SESSION_REFRESH.** SmartAPI does publish a token-renewal endpoint,
    #:   but it consumes a refresh token, and the publisher-login redirect is
    #:   documented as returning only `auth_token` and `feed_token`. Declaring a
    #:   refresh this platform may not hold the input for would make the engine
    #:   attempt a renewal that cannot succeed instead of asking the user to
    #:   reconnect. Recorded as an open question for live validation.
    capabilities = frozenset({
        BrokerCapability.PROFILE,
        BrokerCapability.HOLDINGS,
        BrokerCapability.POSITIONS,
        BrokerCapability.FUNDS,
        BrokerCapability.MARGINS,
        BrokerCapability.SESSION_INVALIDATE,
        BrokerCapability.TICK_STREAM,
        BrokerCapability.INSTRUMENT_CATALOGUE,
    })

    credential_spec = BrokerCredentialSpec(
        api_key_env="ANGELONE_API_KEY",
        redirect_url_env="ANGELONE_REDIRECT_URL",
        #: No API secret: the publisher-login flow returns the session tokens on
        #: the redirect, so there is no server-side exchange to sign. Declaring
        #: a secret this broker does not use would make `is_configured()` false
        #: for a correctly configured deployment.
        required=("api_key", "redirect_url"),
    )

    #: SmartAPI's delivery product code.
    default_product = "DELIVERY"
    default_variety = "NORMAL"

    #: SmartAPI WebSocket 2.0 — binary ticks, JSON errors, text keep-alive.
    stream_protocol = "smartapi_stream_v2"

    #: NO per-connection instrument limit is declared, and that is the D5.10
    #: audit finding for this broker rather than an omission.
    #:
    #: SmartAPI's documented cap is a **per-session token quota** counted across
    #: the client code — :data:`MAX_SUBSCRIBED_INSTRUMENTS`, spent per token and
    #: mode — not a ceiling on what one socket may hold. Sharding cannot raise a
    #: quota. Declaring 1,000 here would open a second socket that the same quota
    #: refuses, spending one of this broker's three permitted concurrent
    #: connections to subscribe to nothing and turning today's honest warning
    #: into a feed that is dead rather than merely narrow.
    #:
    #: So the quota stays enforced where it always was, in
    #: :meth:`stream_subscribe_frames`, by trimming with a warning. `None` here
    #: means "no *shardable* limit known", which is the truthful answer, and the
    #: 1,000-token quota remains a recorded limitation (LIM-D5.10-2).
    #:
    #: The three-socket-per-client-code ceiling is likewise not declared: a
    #: ceiling with no per-connection limit beneath it can never be reached,
    #: because the planner produces exactly one shard.

    # -- HTTP ----------------------------------------------------------------
    def _headers(self, session: dict = None) -> dict:
        """SmartAPI's fixed header set, with this user's session token.

        Every SmartAPI call requires the client-context headers below, present
        and non-empty. The values sent for the three device fields are neutral
        constants rather than this server's real IP and MAC: they identify the
        *host*, not the user, so populating them truthfully would send our
        infrastructure's network identity to a third party on every request and
        buy nothing — SmartAPI does not authenticate on them.
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": self.credentials.api_key,
        }
        if session is not None:
            token = (session or {}).get("access_token")
            if not token:
                raise BrokerAuthError("Angel One is not connected. Connect your account in Settings.")
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _angel(self, method: str, path: str, session: dict = None, body: dict = None) -> Any:
        """One SmartAPI call, with its envelope unwrapped and its errors mapped.

        SmartAPI answers HTTP 200 with `{"status": false, "errorcode": …}` for
        application failures, so the transport-level handling in
        `BrokerAdapter._request` cannot see them — an unchecked caller would
        read `data: null` as an empty portfolio.
        """
        payload = await self._request(
            method, f"{BASE_URL}{path}", headers=self._headers(session), json_body=body
        )
        if not isinstance(payload, dict):
            raise BrokerError("Angel One returned an unexpected response",
                              user_message="Angel One returned an unexpected response.")
        if payload.get("status") is not True:
            code = str(payload.get("errorcode") or payload.get("errorCode") or "")
            message = payload.get("message") or "Angel One request failed"
            if code in DEAD_SESSION_CODES:
                raise BrokerAuthError(
                    "Angel One session expired (SmartAPI sessions end at midnight IST). Please reconnect."
                )
            raise BrokerError(f"Angel One error [{code}]: {message}", user_message=message)
        return payload.get("data")

    # -- authentication ------------------------------------------------------
    def get_login_url(self, user_id: str = None) -> dict:
        credentials = self.credentials
        if not credentials.api_key:
            return {"url": None, "configured": False,
                    "message": "Angel One API key not configured. Add ANGELONE_API_KEY to .env"}
        if not credentials.redirect_url:
            return {"url": None, "configured": False,
                    "message": "Angel One not configured. Add ANGELONE_API_KEY and "
                               "ANGELONE_REDIRECT_URL to .env"}
        params = {"api_key": credentials.api_key, "redirect_url": credentials.redirect_url}
        if user_id:
            # SmartAPI echoes `state` back on the redirect, which is how the
            # public callback maps the session to the right app user — the same
            # role Upstox's `state` plays and Kite's `redirect_params` plays.
            # The `uid=` prefix is the platform's own convention for that
            # parameter, not SmartAPI's, and the shared callback route reads it.
            params["state"] = f"uid={user_id}"
        return {"url": f"{LOGIN_URL}?{urlencode(params)}", "configured": True}

    def parse_callback_params(self, params: Dict[str, str]) -> Optional[dict]:
        """SmartAPI redirects with `?auth_token=&feed_token=`, not `?code=`.

        The feed token is carried through because the market feed authenticates
        with it *in addition to* the auth token — a broker whose stream needs a
        second credential is why the session contract has a place to put one.
        """
        params = params or {}
        auth_token = (params.get("auth_token") or "").strip()
        if not auth_token:
            return None
        return {
            "auth_token": auth_token,
            "feed_token": (params.get("feed_token") or "").strip(),
            "refresh_token": (params.get("refresh_token") or "").strip(),
        }

    async def exchange_token(self, auth_payload: dict) -> dict:
        """Turn the redirect's tokens into a session.

        There is no exchange call: SmartAPI's publisher login hands the tokens
        straight to the redirect. What this does instead is *verify* them and
        resolve the client code, which the stream needs as a header and which
        the redirect does not carry — so a session that cannot stream never gets
        stored as connected in the first place.
        """
        payload = auth_payload or {}
        auth_token = (payload.get("auth_token") or payload.get("access_token") or "").strip()
        feed_token = (payload.get("feed_token") or "").strip()
        if not auth_token:
            raise BrokerError("auth_token required")
        if not self.credentials.api_key:
            raise BrokerError("Angel One not configured")

        session = {"access_token": auth_token}
        profile = await self.get_profile(session)
        client_code = profile.get("account_id")
        if not client_code:
            raise BrokerError("Angel One did not return a client code",
                              user_message="Angel One did not return an account id. Please try connecting again.")
        now = datetime.now(timezone.utc)
        return {
            "access_token": auth_token,
            "feed_token": feed_token,
            "refresh_token": (payload.get("refresh_token") or "").strip(),
            "expires_at": self.session_expiry(now).isoformat(),
            "account_id": client_code,
            "profile": profile,
        }

    def session_expiry(self, connected_at: datetime) -> datetime:
        """SmartAPI sessions stay valid until midnight IST."""
        local = connected_at.astimezone(IST)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return midnight.astimezone(timezone.utc)

    async def invalidate_session(self, session: dict) -> None:
        """Logout: SmartAPI invalidates the session for a client code."""
        client_code = (session or {}).get("account_id")
        if not (session or {}).get("access_token") or not client_code:
            return
        try:
            await self._angel("POST", "/rest/secure/angelbroking/user/v1/logout",
                              session, {"clientcode": client_code})
        except Exception as e:
            logger.warning(f"Angel One session invalidation failed (session may already be dead): {e}")

    # -- account data --------------------------------------------------------
    async def get_profile(self, session: dict) -> dict:
        data = await self._angel("GET", "/rest/secure/angelbroking/user/v1/getProfile", session) or {}
        return {
            "account_id": data.get("clientcode"),
            "user_name": data.get("name"),
            "email": data.get("email"),
            "broker": "ANGELONE",
            "exchanges": data.get("exchanges") or [],
            "products": data.get("products") or [],
        }

    async def get_holdings(self, session: dict) -> list:
        data = await self._angel("GET", "/rest/secure/angelbroking/portfolio/v1/getAllHolding", session)
        # `getAllHolding` wraps the rows and adds a portfolio summary; the older
        # `getHolding` returns the rows bare. Both shapes are accepted so the
        # adapter does not break if a deployment's app is entitled to only one.
        rows = data.get("holdings") if isinstance(data, dict) else data
        holdings = []
        for h in rows or []:
            if not isinstance(h, dict):
                continue
            qty = (h.get("quantity") or 0) + (h.get("t1quantity") or 0)
            avg = h.get("averageprice") or 0
            ltp = h.get("ltp") or 0
            invested = qty * avg
            value = qty * ltp
            pnl = h.get("profitandloss")
            holdings.append({
                "symbol": trading_symbol(h.get("tradingsymbol")),
                "exchange": h.get("exchange"),
                "quantity": qty,
                "average_price": avg,
                "last_price": ltp,
                "market_value": round(value, 2),
                "invested_value": round(invested, 2),
                "pnl": round(pnl if pnl is not None else value - invested, 2),
                "pnl_percent": round(h.get("pnlpercentage") if h.get("pnlpercentage") is not None
                                     else ((value - invested) / invested * 100 if invested else 0), 2),
                "product": h.get("product"),
                "isin": h.get("isin"),
                "instrument_token": instrument_id(h.get("exchange"), h.get("symboltoken")),
            })
        return holdings

    async def get_positions(self, session: dict) -> list:
        rows = await self._angel("GET", "/rest/secure/angelbroking/order/v1/getPosition", session)
        positions = []
        for p in rows or []:
            if not isinstance(p, dict):
                continue
            qty = _int(p.get("netqty"))
            positions.append({
                "symbol": trading_symbol(p.get("tradingsymbol") or p.get("symbolname")),
                "exchange": p.get("exchange"),
                "product": p.get("producttype"),
                "quantity": qty,
                "average_price": _num(p.get("avgnetprice") or p.get("totalbuyavgprice")),
                "last_price": _num(p.get("ltp") or p.get("close")),
                "pnl": round(_num(p.get("pnl") or p.get("unrealised")), 2),
                "realised": round(_num(p.get("realised")), 2),
                "unrealised": round(_num(p.get("unrealised")), 2),
                "buy_quantity": _int(p.get("buyqty")),
                "sell_quantity": _int(p.get("sellqty")),
                "side": "LONG" if qty > 0 else ("SHORT" if qty < 0 else "FLAT"),
                "instrument_token": instrument_id(p.get("exchange"), p.get("symboltoken")),
            })
        return positions

    async def get_funds(self, session: dict) -> dict:
        data = await self._angel("GET", "/rest/secure/angelbroking/user/v1/getRMS", session) or {}
        return {
            "available_margin": round(_num(data.get("availablecash")), 2),
            "used_margin": round(_num(data.get("utiliseddebits")), 2),
            "opening_balance": round(_num(data.get("net")), 2),
            "payin": round(_num(data.get("availableintradaypayin")), 2),
            "payout": round(_num(data.get("utilisedpayout")), 2),
            "collateral": round(_num(data.get("collateral")), 2),
            "total_balance": round(_num(data.get("net")), 2),
        }

    # -- realtime: instruments -------------------------------------------------
    def stream_instruments(self, holdings: list = None, positions: list = None) -> List[Any]:
        """This account's SmartAPI instrument ids, from its own synced rows.

        The same two lists Kite's and Upstox's adapters read, producing a third
        kind of identifier — and, as with Upstox, nothing has to be fetched:
        `get_holdings` and `get_positions` already wrote the exchange-qualified
        id onto every row. **No instrument catalogue is involved**, which is why
        D4.9 needed no catalogue sprint; the cost is that the feed covers what
        the account holds and nothing else, which is exactly the scope D4.5
        subscribes and grants coverage for.

        Sorted and de-duplicated so a resubscribe after a portfolio sync
        produces a stable subscription list.
        """
        ids = set()
        for row in list(holdings or []) + list(positions or []):
            if not isinstance(row, dict):
                continue
            identity = row.get("instrument_token")
            if parse_instrument_id(identity) is not None:
                ids.add(str(identity))
        # Sorted on the parsed pair rather than the string, so 1|10 does not
        # precede 1|9 and two runs cannot produce different-looking lists.
        return sorted(ids, key=lambda value: parse_instrument_id(value))

    # -- instrument catalogue (D5.16) ------------------------------------------
    _catalogue_cache = CatalogueCache(INSTRUMENT_MASTER_TTL_SECONDS)

    @staticmethod
    def build_catalogue_index(*row_groups) -> Dict[Tuple[str, str], Any]:
        """`{(EXCHANGE, SYMBOL): "<segment>|<token>"}` from scrip-master rows.

        TWO EXCHANGES THAT SPELL A SERIES DIFFERENTLY
        ----------------------------------------------
        SmartAPI names an NSE equity `RELIANCE-EQ` and the *same company's* BSE
        listing plainly `RELIANCE` — verified in the live master, where they are
        tokens 2885 and 500325. So the series is recoverable from the symbol on
        NSE and simply absent on BSE, and the two halves are ranked differently
        for a reason that is the exchange's, not this adapter's:

        * **NSE** — the suffix is the series and is ranked by the shared policy.
          That is what keeps sovereign gold bonds (`-SG`), treasury bills
          (`-TB`), NCDs (`-N0`) and mutual-fund units (`-MF`) out of an equity
          catalogue: they are cash-market rows with `instrumenttype: ""` and
          would otherwise all be admitted.
        * **BSE** — there is no series to read, so every accepted row ranks
          equally and a symbol claimed twice is dropped rather than guessed.
          Verified: 12,897 BSE cash rows, 0 such collisions.

        The identifier is built by `instrument_id`, the same function that
        stamps a synced holding row and decodes a tick, so the catalogue's
        value and the wire's value are one expression and cannot drift.
        """
        catalogue = InstrumentCatalogue()
        for rows in row_groups:
            for row in rows or ():
                if not isinstance(row, dict):
                    continue
                instrument_type = row.get("instrumenttype") or ""
                if instrument_type == INSTRUMENT_INDEX_TYPE:
                    exchange = normalize_exchange(row.get("exch_seg"))
                    canonical = canonical_index(row.get("name"))
                    if exchange is None or canonical is None:
                        continue
                    catalogue.offer(
                        exchange, canonical,
                        instrument_id(exchange, row.get("token")),
                        rank=0, segment=INDEX_SEGMENT,
                    )
                    continue
                if instrument_type != INSTRUMENT_CASH_TYPE:
                    continue
                exchange = normalize_exchange(row.get("exch_seg"))
                if exchange is None:
                    continue
                raw = str(row.get("symbol") or "").strip().upper()
                symbol = trading_symbol(raw)
                if exchange == "NSE":
                    rank = series_rank(exchange, raw.rpartition("-")[2])
                    if rank is None:
                        continue
                else:
                    rank = 0
                catalogue.offer(
                    exchange, symbol,
                    instrument_id(exchange, row.get("token")),
                    rank=rank,
                )
        return catalogue.build()

    async def _download_catalogue(self) -> Dict[Tuple[str, str], Any]:
        """Fetch and index SmartAPI's scrip master."""
        try:
            async with _broker_http_client(60.0) as client:
                response = await client.get(INSTRUMENT_MASTER_URL)
                response.raise_for_status()
                rows = response.json()
        except Exception as exc:
            raise BrokerError(
                f"SmartAPI scrip master unavailable: {type(exc).__name__}",
                code=BrokerErrorCode.NETWORK,
                user_message="Live instrument data is temporarily unavailable.",
            ) from exc
        index = self.build_catalogue_index(rows if isinstance(rows, list) else [])
        logger.info("SmartAPI instrument catalogue loaded: %d cash equities", len(index))
        return index

    async def _instrument_catalogue(self) -> Dict[Tuple[str, str], Any]:
        return await type(self)._catalogue_cache.get(self._download_catalogue)

    async def resolve_instruments(self, instruments: Sequence[Any],
                                  session: dict = None) -> Dict[str, Any]:
        """Canonical instruments -> SmartAPI `"<segment>|<token>"` identifiers.

        `session` is accepted and unused: the master is public.
        """
        if not instruments:
            return {}
        return resolve_from_index(instruments, await self._instrument_catalogue())

    # -- realtime: the SmartAPI codec ------------------------------------------
    def stream_endpoint(self, session: dict, credentials: dict = None) -> BrokerStreamEndpoint:
        """The smart-stream socket, authenticated by FOUR headers.

        Neither of the two auth styles the platform already speaks: not Kite's
        query string and not Upstox's single bearer header. SmartAPI wants the
        session's JWT, the app's API key, the account's client code and a
        *separate* feed token, all as headers — which is why nothing
        credential-bearing is in the URL and `safe_url` has nothing to hide.

        `credentials` is what `stream_credentials()` returned, passed back
        rather than re-read, so the adapter stays free of environment access.

        The keep-alive is declared here rather than run here: SmartAPI closes a
        connection that stops sending the text frame `ping`, and the timer for
        it belongs to whoever owns the socket. See `stream.py`.
        """
        session = session or {}
        credentials = credentials or self.stream_credentials()
        return BrokerStreamEndpoint(
            url=WS_URL,
            headers={
                "Authorization": str(session.get("access_token") or ""),
                "x-api-key": str(credentials.get("api_key") or ""),
                "x-client-code": str(session.get("account_id") or ""),
                "x-feed-token": str(session.get("feed_token") or ""),
            },
            heartbeat_frame=HEARTBEAT_FRAME,
            heartbeat_interval=HEARTBEAT_INTERVAL,
        )

    def stream_subscribe_frames(self, instruments: list = None) -> List[Any]:
        """SmartAPI's single subscribe frame — JSON, sent as **text**.

        Three SmartAPI specifics, and a Kite- or Upstox-shaped assumption gets
        each of them wrong:

        * one frame, not Kite's two: the mode is inside the subscription;
        * `str`, not Upstox's `bytes` — the transport forwards exactly what this
          returns without re-encoding it (D4.2), so the choice is made here;
        * instruments are **grouped by exchange segment**, not listed flat. A
          flat list has nowhere to put the segment, and the segment is half the
          identity.
        """
        grouped: Dict[int, List[str]] = {}
        count = 0
        for value in instruments or []:
            parsed = parse_instrument_id(value)
            if parsed is None:
                continue
            segment, token = parsed
            if count >= MAX_SUBSCRIBED_INSTRUMENTS:
                continue
            bucket = grouped.setdefault(segment, [])
            if token not in bucket:
                bucket.append(token)
                count += 1
        if not grouped:
            return []
        total = sum(1 for value in instruments or [] if parse_instrument_id(value) is not None)
        if total > MAX_SUBSCRIBED_INSTRUMENTS:
            logger.warning(
                "Angel One market feed: %d instruments exceeds the %d-token session quota — "
                "subscribing to the first %d",
                total, MAX_SUBSCRIBED_INSTRUMENTS, MAX_SUBSCRIBED_INSTRUMENTS,
            )
        request = {
            # A correlation id is optional and echoed back on an error response,
            # which is the only way to tell which request a rejection is about.
            "correlationID": uuid.uuid4().hex[:10],
            "action": ACTION_SUBSCRIBE,
            "params": {
                "mode": STREAM_MODE_LTP,
                "tokenList": [
                    {"exchangeType": segment, "tokens": grouped[segment]} for segment in sorted(grouped)
                ],
            },
        }
        return [json.dumps(request)]

    def stream_connect_error(self, error: BaseException) -> Optional[str]:
        """Whether a refused handshake means this session is dead."""
        return _session_refused(error)

    def decode_stream_frame(self, frame: Any) -> BrokerStreamEvent:
        """Decode one smart-stream frame.

        The feed mixes both framings on one socket, so both are handled here and
        neither is guessed at:

        * **binary** is a tick packet — exactly one, not a batch;
        * **text** is the `pong` keep-alive answer or a JSON error envelope
          (`{"correlationID", "errorCode", "errorMessage"}`). An error is
          reported rather than raised: SmartAPI states that a failed
          subscription does not affect existing ones, so dropping a socket that
          is still delivering other instruments would turn a partial rejection
          into a total outage.

        Nothing here classifies an expired session, and that is not an omission:
        SmartAPI refuses a dead session at the *handshake*, where no frame
        exists — see :func:`_session_refused`.
        """
        if isinstance(frame, (bytes, bytearray)):
            tick = decode_tick(bytes(frame))
            if tick is None:
                return BrokerStreamEvent.ignore()
            return BrokerStreamEvent.tick_event([tick])

        if not isinstance(frame, str):
            return BrokerStreamEvent.ignore()
        text = frame.strip()
        if not text or text == HEARTBEAT_RESPONSE:
            return BrokerStreamEvent.ignore()
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return BrokerStreamEvent.ignore()
        if not isinstance(data, dict):
            return BrokerStreamEvent.ignore()
        code = data.get("errorCode") or data.get("errorcode")
        if code:
            return BrokerStreamEvent.error(f"[{code}] {data.get('errorMessage') or data.get('message') or ''}".strip())
        return BrokerStreamEvent.ignore()


def _num(value: Any) -> float:
    """SmartAPI returns most numbers as strings, and some as `""`."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0

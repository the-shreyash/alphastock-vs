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

TWO FEEDS, TWO CHANNELS (D4.7)
------------------------------
Upstox does not multiplex its realtime surface the way Kite does. Order updates
arrive on the v2 **portfolio stream** as JSON; market ticks arrive on the v3
**market-data feed**, a different host path, a different encoding (protobuf) and
a different subscription model. Both are declared through
:meth:`UpstoxAdapter.stream_channels` and opened by the same generic transport,
which learns nothing about Upstox in the process — see
:class:`~services.brokers.streaming.BrokerStreamChannel` for why the transport
grew the concept rather than this module growing a socket.

Everything Upstox-specific below terminates in this file. What leaves it is a
:class:`~services.brokers.streaming.BrokerTick` carrying the account's own
instrument key, which `InstrumentMap` turns into a canonical symbol exactly as
it does for Kite's integer token.
"""
import json
import logging
import math
import struct
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

from services.brokers.base import (
    IST, AdapterStreamChannel, BrokerAdapter, BrokerAuthError, BrokerError, normalize_status,
)
from services.brokers.capabilities import BrokerCapability
from services.brokers.credentials import BrokerCredentialSpec
from services.brokers.streaming import (
    BrokerStreamChannel,
    BrokerStreamEndpoint,
    BrokerStreamEvent,
    StreamEventKind,
)

logger = logging.getLogger(__name__)

#: Upstox v2 portfolio stream. Order updates only — market ticks arrive on the
#: separate v3 feed below. Moved here from `stream.py` in D4.2 with the rest of
#: this broker's wire knowledge.
WS_URL = "wss://api.upstox.com/v2/feed/portfolio-stream-feed?update_types=order"

#: Upstox v3 market-data feed (D4.7).
#:
#: Verified against Upstox's own Python SDK (`upstox_client/feeder/
#: market_data_feeder_v3.py`), not inferred from Kite and not inferred from the
#: portfolio stream. Two properties matter here and neither is shared with
#: Zerodha:
#:
#: * **Authentication is a bearer header, not a query string.** Nothing
#:   credential-bearing is in this URL, so `BrokerStreamEndpoint.safe_url` has
#:   nothing to strip — the opposite of Kite's ticker, whose URL carries a live
#:   access token. No second masking mechanism was needed for it.
#: * **Upstox answers the handshake with a 307 redirect** to a short-lived
#:   signed socket URL. `websockets` follows it, so there is no separate
#:   `/authorize` call to make; that REST endpoint exists and returns the same
#:   destination, and is deliberately not used — an extra authenticated request
#:   whose response would be a credential-bearing URL we would then have to keep
#:   out of every log line.
MARKET_WS_URL = "wss://api.upstox.com/v3/feed/market-data-feed"

BASE_URL = "https://api.upstox.com/v2"
AUTH_URL = f"{BASE_URL}/login/authorization/dialog"

#: Channel names for this broker's two realtime connections (D4.7).
ORDER_CHANNEL = "orders"
MARKET_CHANNEL = "market"

#: The market-feed mode this adapter subscribes in.
#:
#: `ltpc` deliberately, and — as with Kite's `STREAM_MODE` — the choice is
#: recorded here rather than inline because it is the one protocol decision with
#: a product consequence. It is NOT copied from Kite: Upstox's modes are its own
#: (`ltpc`, `option_greeks`, `full`, `full_d30`) and the reasoning was made
#: against what each of them actually carries.
#:
#: The tick feed marks portfolio holdings and open trades and answers streamed
#: quotes; all three need a last traded price. `full` adds five depth levels,
#: 1-minute/30-minute/daily candles, option greeks and open interest — a
#: multiple of the bandwidth for fields no consumer reads — and `full_d30` is an
#: Upstox Plus entitlement this platform does not require its users to hold.
#:
#: What it costs, stated plainly: an Upstox-derived `MarketTick` carries no
#: volume. `LTPC` has `ltq` — the *last traded* quantity — and that is one
#: trade's size, not the day's cumulative volume; mapping it to the canonical
#: `volume` field would put a number there that means something else entirely.
#: Cumulative volume (`vtt`) exists only in the `full` modes. This is the same
#: limitation Kite's LTP mode has, reached independently, and it is recorded in
#: TASK.md rather than papered over.
MARKET_STREAM_MODE = "ltpc"

#: Instrument keys one Upstox socket may carry in `ltpc` mode.
#:
#: Enforced here because Upstox rejects an over-limit *subscription request* as
#: a whole rather than trimming it, so exceeding it silently costs the account
#: every instrument rather than the excess ones. A retail holdings-and-positions
#: universe is nowhere near this; an account that somehow is gets a warning and
#: a deterministic prefix, which is strictly better than a feed with nothing on
#: it.
MAX_SUBSCRIBED_INSTRUMENTS = 5000


# ── Upstox v3 market-data feed: the protobuf codec ──────────────────────────
#
# The v3 feed is Protocol Buffers, and this decodes exactly the fields the
# canonical contract can hold. Field numbers are transcribed from Upstox's
# official schema (`MarketDataFeedV3.proto`, upstox/upstox-python):
#
#     FeedResponse { Type type = 1; map<string, Feed> feeds = 2;
#                    int64 currentTs = 3; MarketInfo marketInfo = 4; }
#     Feed         { oneof { LTPC ltpc = 1; FullFeed fullFeed = 2;
#                            FirstLevelWithGreeks firstLevelWithGreeks = 3; } }
#     FullFeed     { oneof { MarketFullFeed marketFF = 1; IndexFullFeed indexFF = 2; } }
#     LTPC         { double ltp = 1; int64 ltt = 2; int64 ltq = 3; double cp = 4; }
#
# WHY A DECODER RATHER THAN THE PROTOBUF RUNTIME
# ----------------------------------------------
# `protobuf` is not a dependency of this platform, and its absence is a decision
# rather than an oversight: PH2.8 removed it (with grpcio and the old Google AI
# SDK) from the production runtime after PH2.1 measured ~220 MB of image that no
# application module imports. Re-adding a C-extension runtime dependency, plus a
# generated `_pb2` build artifact that must be regenerated whenever the schema
# moves, to read one `double` out of a map is a poor trade.
#
# The proto3 wire format is small, published and stable, and what is read here
# is four field numbers deep. The risk in hand-decoding is getting the schema
# wrong, so the tests do not take this module's word for it: fixtures are
# encoded by Google's own protobuf runtime from the official schema and must
# decode identically through the code below (`tests/test_broker_streaming.py`,
# the Upstox protobuf conformance tests). That runtime is a **test-only**
# dependency — see requirements-dev.txt — so the oracle cannot pass by agreeing
# with the implementation it is checking.

WIRE_VARINT = 0
WIRE_64BIT = 1
WIRE_LENGTH = 2
WIRE_32BIT = 5

#: `FeedResponse.feeds`.
FEEDS_FIELD = 2
#: proto3 map entries are messages of {key = 1, value = 2}.
MAP_KEY_FIELD = 1
MAP_VALUE_FIELD = 2
#: `LTPC.ltp`.
LTP_FIELD = 1

#: Every place an `LTPC` message can sit inside a `Feed`, as field-number paths.
#:
#: Only the first is reachable in the mode this adapter subscribes in. The other
#: three are here because a mode change must not produce a socket that connects,
#: subscribes and decodes nothing — which is indistinguishable from a quiet
#: market and is precisely how a feed goes silently narrow.
LTPC_PATHS = (
    (1,),        # Feed.ltpc                                    — ltpc mode
    (2, 1, 1),   # Feed.fullFeed.marketFF.ltpc                  — full mode, tradable
    (2, 2, 1),   # Feed.fullFeed.indexFF.ltpc                   — full mode, index
    (3, 1),      # Feed.firstLevelWithGreeks.ltpc               — option_greeks mode
)


class UpstoxProtocolError(ValueError):
    """A market-feed frame that is not decodable proto3.

    Raised inside this module and caught at :meth:`UpstoxMarketFeedChannel.decode`,
    never propagated: a malformed frame must cost itself and nothing else, least
    of all a socket that is otherwise delivering good prices.
    """


def _read_varint(buf: bytes, pos: int) -> Tuple[int, int]:
    """One base-128 varint at `pos`; returns (value, next position)."""
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise UpstoxProtocolError("varint runs past the end of the frame")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise UpstoxProtocolError("varint is longer than 64 bits")


def _fields(buf: bytes):
    """Yield `(field_number, wire_type, value)` for one protobuf message.

    `value` is an `int` for varints and a `bytes` slice for everything else.
    Unknown fields are yielded rather than skipped silently — the caller filters
    by field number, which is what makes this forward-compatible with a schema
    that grows fields we do not read.

    A length that runs past the buffer stops the parse by raising rather than
    truncating: every subsequent offset in a damaged frame is guesswork, and
    guessing produces plausible instrument keys at plausible prices, which is
    the one outcome worse than decoding nothing. (The same reasoning as the Kite
    binary parser's truncation check, arrived at from the same failure mode.)
    """
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = _read_varint(buf, pos)
        number, wire = key >> 3, key & 0x07
        if number == 0:
            raise UpstoxProtocolError("field number 0 is not valid")
        if wire == WIRE_VARINT:
            value, pos = _read_varint(buf, pos)
        elif wire == WIRE_64BIT:
            if pos + 8 > end:
                raise UpstoxProtocolError("64-bit field runs past the end of the frame")
            value, pos = buf[pos:pos + 8], pos + 8
        elif wire == WIRE_LENGTH:
            length, pos = _read_varint(buf, pos)
            if pos + length > end:
                raise UpstoxProtocolError("length-delimited field runs past the end of the frame")
            value, pos = buf[pos:pos + length], pos + length
        elif wire == WIRE_32BIT:
            if pos + 4 > end:
                raise UpstoxProtocolError("32-bit field runs past the end of the frame")
            value, pos = buf[pos:pos + 4], pos + 4
        else:
            # Wire types 3 and 4 are proto2 groups, removed in proto3, and 6/7
            # are not assigned. Any of them means this is not the frame we think
            # it is, so the parse stops rather than resynchronising by guess.
            raise UpstoxProtocolError(f"unsupported protobuf wire type {wire}")
        yield number, wire, value


def _submessage(buf: bytes, *path: int) -> Optional[bytes]:
    """Follow a path of field numbers down through nested messages."""
    for number in path:
        found = None
        for field_number, wire, value in _fields(buf):
            if field_number == number and wire == WIRE_LENGTH:
                found = value
                break
        if found is None:
            return None
        buf = found
    return buf


def _last_price(feed: bytes) -> Optional[float]:
    """`LTPC.ltp` from a `Feed`, in rupees, or None when it carries no price.

    RUPEES, NOT PAISE — AND THE SCHEMA IS THE EVIDENCE
    ---------------------------------------------------
    `LTPC.ltp` is a proto3 `double`. Kite quotes in paise (and in two other
    scales for the currency segments) because its packets carry unsigned
    integers, which have to be scaled to express a fraction; a `double` does
    not. Applying Kite's divisor of 100 here would price every Upstox
    instrument at one per cent of its value — a number that looks entirely
    plausible on a chart and would be marked against a real position.

    `None` rather than `0.0` when the field is absent, and the distinction is
    load-bearing: proto3 omits a `double` whose value is zero, so "no price in
    this frame" and "a price of zero" are the same bytes. Neither may become a
    tick — `MIN_STOCK_PRICE` rejects zero at the canonical boundary for exactly
    this reason — so returning None drops it here instead of manufacturing a
    tick that marks a position at nothing.
    """
    for path in LTPC_PATHS:
        ltpc = _submessage(feed, *path)
        if ltpc is None:
            continue
        for number, wire, value in _fields(ltpc):
            if number == LTP_FIELD and wire == WIRE_64BIT:
                price = struct.unpack("<d", value)[0]
                # NaN and ±inf are what a corrupted double decodes to. They
                # would survive every arithmetic step below and fail only at the
                # canonical range check — which compares NaN with `<=` and gets
                # False, so it *is* caught, but as "out of range" rather than as
                # the damage it is. Dropped here, where the reason is known.
                return price if math.isfinite(price) else None
    return None


def decode_market_feed(payload: bytes) -> List[Dict[str, Any]]:
    """One `FeedResponse` frame → `[{instrument_token, last_price}]`.

    The instrument identifier is the map key — Upstox's own instrument key,
    `"NSE_EQ|INE002A01018"` — carried through verbatim as
    :attr:`BrokerTick.instrument_token`, which is typed `Any` precisely so a
    broker that identifies instruments by a compound string needs no special
    case. `InstrumentMap` matches it against the account's synced holdings and
    positions, whose `instrument_token` is the same key, and the platform never
    sees it again.

    Nothing raises. A frame is a batch of instruments and one undecodable entry
    must not cost the others their prices; a frame that yields nothing usable
    returns `[]`, which the caller turns into an IGNORE event. `market_info`
    frames and Upstox's periodic keep-alives carry no `feeds` and land here
    naturally as empty rather than as errors.
    """
    ticks: List[Dict[str, Any]] = []
    try:
        entries = [value for number, wire, value in _fields(payload)
                   if number == FEEDS_FIELD and wire == WIRE_LENGTH]
    except UpstoxProtocolError as exc:
        logger.debug("Upstox market frame skipped: %s", exc)
        return ticks

    for entry in entries:
        try:
            key = None
            feed = None
            for number, wire, value in _fields(entry):
                if number == MAP_KEY_FIELD and wire == WIRE_LENGTH:
                    key = value.decode("utf-8", errors="ignore").strip()
                elif number == MAP_VALUE_FIELD and wire == WIRE_LENGTH:
                    feed = value
            if not key or feed is None:
                continue
            price = _last_price(feed)
            if price is None:
                continue
            ticks.append({"instrument_token": key, "last_price": price})
        except (UpstoxProtocolError, struct.error) as exc:
            logger.debug("Upstox market feed entry skipped: %s", exc)
            continue
    return ticks


def instrument_key(value: Any) -> Optional[str]:
    """Coerce a stored instrument identifier into an Upstox instrument key, or None.

    Upstox identifies instruments by a compound string — `"NSE_EQ|INE002A01018"`,
    `"NSE_INDEX|Nifty 50"` — so unlike Kite's numeric token there is nothing to
    parse; what this rejects is a value that is not one. The separator is the
    check: a bare symbol or an integer would be accepted by the subscribe frame
    and then silently rejected by Upstox for the *whole* subscription, leaving
    the account with a connected socket and no instruments on it.

    `bool` is excluded explicitly for the same reason the Kite coercion excludes
    it — it is an `int` subclass and would stringify into the frame as "True".
    """
    if value is None or isinstance(value, bool):
        return None
    key = str(value).strip()
    if "|" not in key:
        return None
    segment, _, identifier = key.partition("|")
    if not segment.strip() or not identifier.strip():
        return None
    return key


class UpstoxMarketFeedChannel(BrokerStreamChannel):
    """The v3 market-data feed — Upstox's tick channel.

    A sibling of the portfolio stream rather than a mode of it: different host
    path, different encoding, different subscription model, and it fails
    independently. Everything below is Upstox wire knowledge and none of it
    leaves this class; what the transport gets back is a
    :class:`BrokerStreamEvent` built from canonical `BrokerTick`s.

    :attr:`delivers` is TICKS alone. That narrowing is what stops this channel
    ever being credited with an order update, and — the direction that actually
    bites — stops the *order* channel being mistaken for this one when the
    account's market-data provider is deciding whether its feed is live.
    """

    name = MARKET_CHANNEL
    #: A protocol of its own, not the adapter's. Two feeds of one broker need
    #: not speak the same wire format, and these two do not.
    protocol = "upstox_market_feed_v3"
    delivers = frozenset({StreamEventKind.TICKS})

    def endpoint(self, session: dict, credentials: Dict[str, str] = None) -> BrokerStreamEndpoint:
        """The v3 feed, authenticated by bearer header.

        `credentials` is unused: this feed authenticates with the user's own
        session token and needs none of the app-level material Kite's ticker
        puts in its query string. The parameter stays because it is the
        channel contract, and a channel that dropped it would be a channel the
        transport calls differently.
        """
        token = (session or {}).get("access_token") or ""
        return BrokerStreamEndpoint(
            url=MARKET_WS_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "*/*"},
        )

    def subscribe_frames(self, instruments: Sequence[Any] = None) -> List[Any]:
        """Upstox's single subscribe frame — JSON, sent as **binary**.

        Two Upstox specifics that a Kite-shaped assumption gets wrong:

        * one frame, not two. Kite subscribes and then narrows the mode with a
          second frame; Upstox carries the mode inside the subscription.
        * `bytes`, not `str`. Upstox requires the request as a binary frame, and
          a text frame is ignored — which produces a socket that connects,
          reports its link up, and never delivers a tick. Returning `bytes` is
          what makes the transport send a binary frame, since it forwards
          exactly what this returns without re-encoding it (D4.2).

        The keys are re-coerced here as well as in `stream_instruments` because
        this method is part of the channel's contract and the transport hands
        back whatever it was given at stream start.
        """
        keys = [k for k in (instrument_key(i) for i in (instruments or [])) if k is not None]
        if not keys:
            return []
        if len(keys) > MAX_SUBSCRIBED_INSTRUMENTS:
            logger.warning(
                "Upstox market feed: %d instruments exceeds the %d-key limit for %s mode — "
                "subscribing to the first %d",
                len(keys), MAX_SUBSCRIBED_INSTRUMENTS, MARKET_STREAM_MODE, MAX_SUBSCRIBED_INSTRUMENTS,
            )
            keys = keys[:MAX_SUBSCRIBED_INSTRUMENTS]
        request = {
            "guid": str(uuid.uuid4()),
            "method": "sub",
            "data": {"mode": MARKET_STREAM_MODE, "instrumentKeys": keys},
        }
        return [json.dumps(request).encode("utf-8")]

    def connect_error(self, error: BaseException) -> Optional[str]:
        """Whether a refused handshake means this session is dead."""
        return _session_refused(error)

    def decode(self, frame: Any) -> BrokerStreamEvent:
        """Decode one v3 market-feed frame.

        Binary only. A text frame on this socket is not a tick under any Upstox
        mode, so it is ignored rather than JSON-parsed — parsing it would be the
        portfolio stream's codec running on the market feed, which is the
        cross-contamination this channel split exists to make impossible.
        """
        if not isinstance(frame, (bytes, bytearray)):
            return BrokerStreamEvent.ignore()
        return BrokerStreamEvent.tick_event(decode_market_feed(bytes(frame)))


def _session_refused(error: BaseException) -> Optional[str]:
    """Reason string when Upstox refused a stream handshake for a dead token.

    Upstox invalidates every access token daily at 03:30 IST and then refuses
    both feeds' handshakes with **HTTP 401** — before a frame is exchanged, so
    neither codec ever sees it. Left unclassified the generic transport cannot
    tell it from a broker outage: it reconnects on the backoff schedule
    indefinitely, the account's market feed stays registered, and the user is
    never asked to reconnect. That is not an edge case; it is every connected
    Upstox user, every morning.

    Shared by both channels because it is one token and one rejection, and
    written as a function rather than duplicated so the two cannot drift into
    disagreeing about what a dead Upstox session looks like.

    403 is included alongside 401 because Upstox uses it for a token whose app
    authorisation has been withdrawn — also unrecoverable by reconnecting, and
    also fixed by the user reconnecting the account.
    """
    status = getattr(error, "status_code", None)
    if status is None:
        # websockets >= 14 wraps the handshake response instead.
        status = getattr(getattr(error, "response", None), "status_code", None)
    if status in (401, 403):
        return (
            f"Upstox refused the stream handshake (HTTP {status}) — the access token is no "
            "longer valid. Upstox tokens reset daily at 3:30 AM IST; please reconnect."
        )
    return None


class UpstoxAdapter(BrokerAdapter):
    name = "upstox"
    display_name = "Upstox"

    #: Upstox v2 covers the account and order surface; the realtime surface is
    #: two feeds and both are declared here (D4.7). TICK_STREAM used to be
    #: absent because the market feed is a separate protobuf endpoint this
    #: adapter did not speak — it speaks it now, on its own channel, and the
    #: capability is what makes the account's feed a registered market-data
    #: provider through the same broker-agnostic path Zerodha uses.
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
        api_key_env="UPSTOX_API_KEY",
        api_secret_env="UPSTOX_API_SECRET",
        redirect_url_env="UPSTOX_REDIRECT_URL",
        #: Upstox will not issue a token without a registered redirect URL, so
        #: it is required for this broker where it is optional for Zerodha.
        required=("api_key", "api_secret", "redirect_url"),
    )

    #: Upstox's delivery product code.
    default_product = "D"

    #: Upstox v2 portfolio stream (JSON order updates).
    stream_protocol = "upstox_portfolio"

    def _credentials(self):
        """(api_key, api_secret, redirect_url) — via the credentials boundary."""
        creds = self.credentials
        return creds.api_key, creds.api_secret, creds.redirect_url

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
                    "message": "Upstox not configured. Add UPSTOX_API_KEY, "
                               "UPSTOX_API_SECRET and UPSTOX_REDIRECT_URL to .env"}
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

    def normalize_stream_order(self, payload: dict) -> dict:
        """Canonicalize an Upstox portfolio-stream order frame."""
        return self._normalize_order(payload or {})

    # -- realtime: channels (D4.7) ---------------------------------------------
    def stream_channels(self) -> Tuple[BrokerStreamChannel, ...]:
        """Upstox's two realtime connections.

        The order channel is this adapter itself, wrapped — its endpoint,
        subscribe frames and codec are the `stream_*` / `decode_stream_frame`
        methods below, unchanged from D4.2 — narrowed to ORDER so it can never
        be credited with a tick it did not carry. The market channel is a
        separate codec for a separate feed.

        Naming both explicitly rather than leaving one on the default channel
        name is deliberate: the stream registry keys on `(user, broker,
        channel)` and appears in diagnostics, and "default" would say nothing
        about which of two feeds an entry is.
        """
        return (
            AdapterStreamChannel(self, name=ORDER_CHANNEL, delivers=frozenset({StreamEventKind.ORDER})),
            UpstoxMarketFeedChannel(),
        )

    def stream_instruments(self, holdings: list = None, positions: list = None) -> List[Any]:
        """Upstox instrument keys for every instrument in the user's portfolio.

        The same two lists Kite's adapter reads, producing an entirely different
        kind of identifier: a compound string the user's own holdings and
        positions already carry as `instrument_token`. Nothing has to be fetched
        and no instrument catalogue is involved — which is why D4.7 needed no
        catalogue sprint. The cost is that the feed covers what the account
        holds and nothing else, which is exactly the scope D4.5 subscribes and
        grants coverage for.

        Sorted and de-duplicated so a resubscribe after a portfolio sync
        produces a stable subscription list.
        """
        keys = set()
        for row in list(holdings or []) + list(positions or []):
            if not isinstance(row, dict):
                continue
            key = instrument_key(row.get("instrument_token"))
            if key is not None:
                keys.add(key)
        return sorted(keys)

    def stream_connect_error(self, error: BaseException) -> Optional[str]:
        """Whether a refused portfolio-stream handshake means this session is dead.

        Same classification as the market feed's — see :func:`_session_refused`.
        """
        return _session_refused(error)

    # -- realtime: the portfolio-stream codec (D4.2) ---------------------------
    def stream_endpoint(self, session: dict, credentials: dict = None) -> BrokerStreamEndpoint:
        """The Upstox portfolio socket, authenticated by bearer header.

        A different auth style from Zerodha's query string, on the same
        contract — which is the point of returning an endpoint object rather
        than a URL string.
        """
        token = (session or {}).get("access_token") or ""
        return BrokerStreamEndpoint(url=WS_URL, headers={"Authorization": f"Bearer {token}"})

    def decode_stream_frame(self, frame: Any) -> BrokerStreamEvent:
        """Decode one portfolio-stream frame.

        The feed sends the order payload directly when subscribed with
        `update_types=order`, but wraps it in `{"data": …}` on some update
        types, so both shapes are accepted. Anything else — position and
        holding updates the platform does not consume from this feed — is
        ignored rather than logged: they are a working connection, not an error.
        """
        if isinstance(frame, (bytes, bytearray)):
            frame = frame.decode("utf-8", errors="ignore")
        try:
            data = json.loads(frame)
        except (json.JSONDecodeError, TypeError, ValueError):
            return BrokerStreamEvent.ignore()
        if not isinstance(data, dict):
            return BrokerStreamEvent.ignore()
        if data.get("update_type") not in (None, "order"):
            return BrokerStreamEvent.ignore()
        payload = data.get("data") or data
        if not isinstance(payload, dict) or not payload.get("order_id"):
            return BrokerStreamEvent.ignore()
        return BrokerStreamEvent.order_event(self.normalize_stream_order(payload), broker=self.name)

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

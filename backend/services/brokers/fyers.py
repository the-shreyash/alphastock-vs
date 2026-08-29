"""Fyers API v3 adapter — the FOURTH streaming broker (D4.10).

Official API portal: https://myapi.fyers.in  (API v3)
Official SDK:        https://pypi.org/project/fyers-apiv3/  (`fyers_apiv3`, 3.1.16)
                     `fyers_apiv3/FyersWebsocket/data_ws.py` + its bundled `map.json`

The market-data protocol below is transcribed from that SDK — Fyers' own
reference implementation of its own wire format — and from the field/segment
tables it ships. It is not inferred from Kite, not from Upstox and not from
SmartAPI. Two of the decisions here (the price rule and the connection scope)
are places where reasoning by analogy with the previous three brokers produces
code that runs, connects, and is wrong.

Auth model: the ordinary OAuth2 authorization-code flow. The user is sent to
Fyers' hosted login, comes back to this platform's registered redirect URL with
an `auth_code`, and `validate-authcode` exchanges it for an access token signed
with `SHA256("<app_id>:<secret_id>")`. No user PIN, no TOTP seed, no password —
the same standard this platform holds every broker to.

WHAT IS DIFFERENT ABOUT THIS BROKER, AND WHY IT MATTERS
--------------------------------------------------------
D4.9 asked whether the streaming framework had generalised or had merely worked
three times. Fyers is the fourth answer, and it is the first broker to disagree
with the framework itself rather than only with its predecessors:

    Kite            Upstox v3        Angel One        Fyers HSM v1-5
    ────            ─────────        ─────────        ──────────────
    handshake auth  bearer header    four headers     a FRAME on the data channel
    frame = batch   frame = map      frame = 1 tick   frame = batch of MIXED records
    self-contained  self-contained   self-contained   SNAPSHOT then DELTAS by topic id
    fixed divisor   none (double)    segment table    scale CARRIED ON THE WIRE
    big-endian      protobuf         little-endian    big-endian ints, LE topic ids
    int token       "NSE_EQ|INE…"    "1|2885"         "sf|nse_cm|2885"
    protocol ping   protocol ping    text `ping`/30s  binary 00 01 0B / 10s

The last two rows of the middle block are the ones that cost a contract change.
An HSM feed sends one **snapshot** per instrument — carrying the topic name, the
price scale and a small numeric topic id — and then sends **updates** that carry
the topic id and the changed values and nothing else. A steady-state frame is
therefore not decodable on its own, and the state that decodes it belongs to one
socket: the server renumbers topics on every connection, and two accounts hold
different instruments behind the same numbers.

`BrokerStreamChannel` is a registry singleton shared by every user of a broker,
so that state could not live on it. `BrokerStreamChannel.open()` — D4.10's one
generic addition, returning `self` by default and therefore invisible to the
three brokers that came before — is what gives a codec a per-connection scope.
See `streaming.py` for the full reasoning and ADR-039.

WHAT LEAVES THIS MODULE
-----------------------
A :class:`~services.brokers.streaming.BrokerTick` carrying this account's own
HSM topic string, which `InstrumentMap` turns into a canonical symbol exactly as
it does for Kite's integer, Upstox's compound key and SmartAPI's segment pair.
No Fyers vocabulary — no topic id, no fyToken, no channel number, no request
type, no HSM segment — exists anywhere else in the platform.
"""

import base64
import binascii
import hashlib
import json
import logging
import struct
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

from services.brokers.base import IST, BrokerAdapter, BrokerAuthError, BrokerError
from services.brokers.capabilities import BrokerCapability
from services.brokers.credentials import BrokerCredentialSpec
from services.brokers.streaming import (
    BrokerStreamChannel,
    BrokerStreamEndpoint,
    BrokerStreamEvent,
    StreamEventKind,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api-t1.fyers.in/api/v3"

#: The HSM market-data socket.
#:
#: Nothing credential-bearing is in this URL, and nothing can be: HSM does not
#: authenticate in the handshake at all — not by query string as Kite's ticker
#: does, not by header as Upstox and SmartAPI do — so `BrokerStreamEndpoint.
#: safe_url` has nothing to strip. The credential goes in the first *frame*, and
#: the frame is binary, which is why no log line in the transport can carry it
#: even by accident.
WS_URL = "wss://socket.fyers.in/hsm/v1-5/prod"

#: This adapter's channel name. Fyers' realtime surface is one socket *for market
#: data*; its order updates live on an entirely separate service that D4.10 does
#: not implement (see :attr:`FyersAdapter.capabilities`). The channel is named
#: rather than left on the default so that the day the order socket is added,
#: the existing stream registry entries do not have to be renamed.
MARKET_CHANNEL = "market"

STREAM_PROTOCOL = "fyers_hsm_v1_5"

#: Identifies this client to Fyers in the auth frame. The SDK sends its own
#: version string here; sending a stable, honest identifier for this platform is
#: the same courtesy and makes a Fyers-side support question answerable.
CLIENT_SOURCE = "StockAssistAI-1"

# ── The HSM request protocol ────────────────────────────────────────────────
#
# Every request is `<uint16 length><uint8 ReqType><uint8 FieldCount>` followed by
# FieldCount fields of `<uint8 FieldId><uint16 FieldLength><payload>`, all
# big-endian. The reference implementation's *length* values are inconsistent
# across request types — see `_hsm_request` — and the server plainly does not
# read them; they are reproduced exactly rather than corrected, because a value
# a working client sends is evidence and a value we reasoned our way to is not.

REQ_AUTH = 1
REQ_ACK = 3
REQ_SUBSCRIBE = 4
REQ_UNSUBSCRIBE = 5
REQ_HEARTBEAT = 11
REQ_MODE = 12

#: The subscription channel number inside one socket. HSM multiplexes up to 30
#: logical channels onto a connection so a client can pause and resume groups of
#: instruments independently. This adapter uses exactly one — the framework has
#: no incremental-subscription caller (a portfolio sync restarts the stream), so
#: a second channel would be a mechanism nothing drives. 11 is the SDK's default.
CHANNEL_NUMBER = 11

#: The auth frame's "mode" field. `P` is what the reference client sends; the
#: alternative values are undocumented and untested against a live socket.
CONNECTION_MODE = "P"

#: The feed mode this adapter subscribes in — LITE.
#:
#: The one protocol decision with a product consequence, recorded here rather
#: than inline for the same reason Kite's `STREAM_MODE`, Upstox's
#: `MARKET_STREAM_MODE` and SmartAPI's `STREAM_MODE_LTP` are, and — as with all
#: three — decided against what *Fyers'* modes carry rather than copied.
#:
#: A lite update is `<topic id><int32>`: the last traded price and nothing else,
#: 7 bytes on the wire. Full mode (70) replaces it with the whole 21-field
#: record — day OHLC, cumulative volume, best bid/ask and their sizes, total
#: buy/sell quantity, average traded price, open interest, circuit limits and
#: the 52-week range — on every price change, for fields no consumer in this
#: platform reads.
#:
#: What it costs, stated plainly: a Fyers-derived `MarketTick` carries no volume.
#: Fyers *does* publish a genuine cumulative day volume (`vol_traded_today`), but
#: only in the snapshot and in full mode; a lite feed would carry it once and
#: then freeze it, which is worse than absent. See :func:`_snapshot_record`.
MODE_LITE = 76
MODE_FULL = 70

#: Instrument subscriptions one HSM connection may hold, and how many HSM tokens
#: fit in one subscribe frame. Both are the reference client's own limits.
#:
#: Enforced here for the reason Upstox's 5,000-key and SmartAPI's 1,000-token
#: limits are: an over-quota request costs the account instruments it asked for,
#: and a deterministic prefix with a warning is strictly better than a feed that
#: is quietly narrower than the portfolio. A retail holdings-and-positions
#: universe is nowhere near either number.
MAX_SUBSCRIBED_INSTRUMENTS = 5000
SUBSCRIBE_BATCH_SIZE = 1500

# ── The keep-alive ──────────────────────────────────────────────────────────

#: HSM requires a bare ReqType-11 frame on the data channel; the WebSocket
#: protocol's own ping frames do not satisfy it. This is the third streaming
#: broker in a row whose liveness check is *not* `ping_interval` — see
#: `BrokerStreamEndpoint.heartbeat_frame` for why that distinction needed a
#: contract field rather than a background task in here.
#:
#: `00 01 0B` — a two-byte length of 1 followed by the request type, with no
#: fields. Binary, where SmartAPI's is text: the contract field is typed to
#: carry either precisely because "what the frame is" is broker knowledge.
HEARTBEAT_FRAME = bytes([0, 1, REQ_HEARTBEAT])

#: Ten seconds, which is the reference client's own interval. Deliberately NOT
#: given the safety margin SmartAPI's keep-alive was given: there the published
#: *deadline* was 30s and 20s bought headroom against a scheduling delay, while
#: here 10s is the observed sending rate of a working client and no deadline is
#: published. Matching a known-good client is the defensible choice when the
#: budget is unknown; inventing a margin against an unknown deadline is not.
HEARTBEAT_INTERVAL = 10.0

# ── The HSM response protocol ───────────────────────────────────────────────

RESP_AUTH = 1
RESP_SUBSCRIBE = 4
RESP_UNSUBSCRIBE = 5
RESP_DATA = 6
RESP_RESUME = 7
RESP_PAUSE = 8
RESP_MODE = 12

#: Control responses all share one shape — `<len:2><type:1><fieldcount:1>
#: <fieldid:1><fieldlen:2><status:1>` — so the status byte is always at offset 7
#: and `"K"` always means accepted. The reference implementation reads the field
#: length with a different byte order in almost every one of these handlers
#: (`!H` here, native `H` there) and then ignores the value; this reads the
#: status byte directly and depends on none of it.
CONTROL_STATUS_OFFSET = 7
STATUS_OK = "K"

#: Record kinds inside a data frame. One frame carries a count and then that many
#: records, and they may be of MIXED kinds — a snapshot for an instrument that
#: has just been subscribed sits beside updates for instruments that were
#: subscribed a minute ago. Every record must therefore be *walked*, not just the
#: ones this adapter wants: skipping one by guessing its length desynchronises
#: every record after it in the same frame.
RECORD_SNAPSHOT = 83
RECORD_FULL = 85
RECORD_LITE = 76

#: HSM's "this field has no value in this frame" sentinel, in a signed 32-bit
#: field. Not zero — zero is a legitimate value for a quantity — so a decoder
#: that treated it as a number would publish −21,474,836.48 as a price.
NO_VALUE = -2147483648

#: Index of the last traded price within a record's positional field list.
#:
#: Zero for scrip (`sf`) topics and for index (`if`) topics alike, which is what
#: lets one decoder serve both. It is emphatically **not** zero for depth (`dp`)
#: topics, where field 0 is the best bid price — which is why
#: :data:`PRICEABLE_TOPICS` exists rather than the decoder simply reading field
#: zero of whatever arrives.
LTP_FIELD = 0

#: Topic prefixes whose field 0 is a last traded price. `dp` (market depth) is
#: excluded rather than unsupported-by-omission: this adapter never subscribes
#: depth, but a decoder that read a depth record's field 0 as a price would
#: publish a *bid* as the traded price, silently and plausibly.
PRICEABLE_TOPICS = frozenset({"sf", "if"})

#: HSM exchange segment → the exchange name Fyers itself uses in a symbol.
#:
#: THE SINGLE MOST IMPORTANT IDENTITY FACT IN THIS FILE, and the one where
#: copying SmartAPI's table produces a feed that resolves nothing.
#:
#: SmartAPI's segments *are* its exchange names, so `NFO` and `CDS` are what its
#: rows carry and what its ticks must say. Fyers is the other design: its
#: symbols are `EXCHANGE:NAME`, and the exchange half is only ever `NSE`, `BSE`
#: or `MCX` — a futures contract is `NSE:NIFTY25AUGFUT`, a currency contract is
#: `NSE:USDINR25AUGFUT`, a BSE currency contract is `BSE:USDINR25AUGFUT`. The
#: cash/derivative/currency distinction lives in the *symbol*, never in the
#: exchange.
#:
#: A synced holding's exchange is read off its symbol (see
#: :func:`split_symbol`), so a tick that reported `NFO` for `nse_fo` would carry
#: an exchange the account's own row never uses. Resolution is by token so it
#: would still find the instrument — and then `MarketInstrument` would qualify
#: the canonical tick with an exchange the rest of the platform does not use for
#: that stock. The mapping below is what keeps the two halves of the boundary
#: saying the same word.
#:
#: Segment codes are Fyers' own (`map.json: exch_seg_dict`), keyed by the first
#: four characters of a fyToken.
SEGMENT_CODES: Dict[str, str] = {
    "1010": "nse_cm",
    "1011": "nse_fo",
    "1012": "cde_fo",
    "1020": "nse_com",
    "1120": "mcx_fo",
    "1210": "bse_cm",
    "1211": "bse_fo",
    "1212": "bcs_fo",
}
SEGMENT_EXCHANGES: Dict[str, str] = {
    "nse_cm": "NSE",
    "nse_fo": "NSE",
    "cde_fo": "NSE",
    "nse_com": "NSE",
    "mcx_fo": "MCX",
    "bse_cm": "BSE",
    "bse_fo": "BSE",
    "bcs_fo": "BSE",
}

#: Where the exchange token starts inside a fyToken. A fyToken is
#: `<segment:4><reserved:6><exchange token:n>` — `"101000000014428"` is NSE cash,
#: exchange token `14428` — and the reference client slices it at exactly this
#: index. The token is carried through as the raw slice rather than as an `int`:
#: it is an opaque handle that goes back on the wire, and re-formatting a value
#: whose canonical form is the broker's is how a subscription silently asks for
#: an instrument that does not exist.
FYTOKEN_EXCHANGE_TOKEN_INDEX = 10

#: Cash-market *series* suffixes on a Fyers trading symbol.
#:
#: Fyers names an equity `"NSE:SBIN-EQ"` where Kite names it `"SBIN"`. Left
#: alone, a user holding one stock at two brokers would hold two different
#: canonical symbols — a split portfolio, a split watchlist, and a feed whose
#: coverage never matches the platform's own instrument universe. The suffix is
#: stripped at this boundary, which is the only place entitled to know it is a
#: series code rather than part of the name.
#:
#: Restricted to the documented NSE cash series plus `INDEX`. BSE's single-letter
#: group codes (`-A`, `-B`, `-X`, …) are deliberately NOT stripped: a one-letter
#: suffix is indistinguishable from a real part of a name, and stripping one
#: wrongly renames an instrument permanently. Recorded as a known limitation
#: rather than guessed at.
CASH_SERIES_SUFFIXES = frozenset({"EQ", "BE", "BZ", "BL", "SM", "ST", "IQ", "GB", "GS", "INDEX"})

#: Fyers application error codes that mean "this session is finished".
#:
#: `-16` is Fyers' "could not authenticate the user", which is what a dead or
#: malformed access token returns. Deliberately a *short* list: Fyers publishes
#: a large error-code space and inventing membership in it would make the engine
#: tell a user to reconnect over an unrelated rejection. Everything else reaches
#: the user as the broker's own message, and a genuinely expired token also
#: returns HTTP 401, which `BrokerAdapter._request` already classifies.
DEAD_SESSION_CODES = frozenset({-16})


def split_symbol(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """A Fyers symbol as `(canonical symbol, exchange)`.

    `"NSE:SBIN-EQ"` → `("SBIN", "NSE")`; `"NSE:NIFTY50-INDEX"` →
    `("NIFTY50", "NSE")`; `"NSE:NIFTY25AUGFUT"` → `("NIFTY25AUGFUT", "NSE")` —
    a derivative carries no hyphenated series and is untouched.

    Both halves come out of one function because they come out of one string:
    Fyers puts the exchange *in* the symbol, and a holding row's separate
    `exchange` field is a numeric segment code rather than a name. Deriving the
    exchange from the symbol is therefore not a convenience — it is the only
    place a name exists at all, and it is the same name the tick side derives
    from the topic's segment (see :data:`SEGMENT_EXCHANGES`).
    """
    text = "" if value is None else str(value).strip().upper()
    if not text:
        return None, None
    exchange, sep, name = text.partition(":")
    if not sep:
        exchange, name = None, text
    name = name.strip()
    if not name:
        return None, None
    head, hyphen, tail = name.rpartition("-")
    if hyphen and head and tail in CASH_SERIES_SUFFIXES:
        name = head
    return name or None, (exchange or None)


def instrument_id(fy_token: Any) -> Optional[str]:
    """This account's handle for one Fyers instrument: an HSM topic string.

    `"101000000014428"` → `"sf|nse_cm|14428"`.

    WHY THE HSM TOPIC AND NOT THE fyToken
    --------------------------------------
    Because the topic is what appears on *both* sides of the wire. It is the
    exact string the subscribe frame carries and the exact string the snapshot
    record returns, so subscription and resolution are one expression and cannot
    drift — the same property SmartAPI's `"1|2885"` has, reached through a
    different derivation. The fyToken is only ever an input to this function; it
    never reaches `InstrumentMap`, because a tick never carries one.

    Returns None for anything that is not a usable fyToken, which keeps a
    rejected instrument out of the subscribe frame rather than corrupting it.
    """
    if fy_token is None or isinstance(fy_token, bool):
        return None
    text = str(fy_token).strip()
    if not text.isdigit() or len(text) <= FYTOKEN_EXCHANGE_TOKEN_INDEX:
        return None
    segment = SEGMENT_CODES.get(text[:4])
    if segment is None:
        return None
    exchange_token = text[FYTOKEN_EXCHANGE_TOKEN_INDEX:]
    # Token 0 is rejected for the reason every adapter here rejects it: it is
    # what an absent identifier coerces to, and it would put a subscription for
    # nothing on the wire.
    if not exchange_token.isdigit() or int(exchange_token) <= 0:
        return None
    return f"sf|{segment}|{exchange_token}"


def parse_instrument_id(value: Any) -> Optional[Tuple[str, str, str]]:
    """`"sf|nse_cm|14428"` → `("sf", "nse_cm", "14428")`, or None if not one of ours."""
    if value is None or isinstance(value, bool):
        return None
    parts = str(value).strip().split("|")
    if len(parts) != 3:
        return None
    kind, segment, token = (part.strip() for part in parts)
    if kind not in PRICEABLE_TOPICS or segment not in SEGMENT_EXCHANGES:
        return None
    if not token.isdigit() or int(token) <= 0:
        return None
    return kind, segment, token


def topic_exchange(topic: Any) -> Optional[str]:
    """The exchange name a topic string belongs to, or None."""
    parsed = parse_instrument_id(topic)
    return SEGMENT_EXCHANGES.get(parsed[1]) if parsed else None


def hsm_key(access_token: Any) -> Optional[str]:
    """The market-feed credential carried inside a Fyers access token.

    A Fyers v3 access token is a JWT, and its payload carries an `hsm_key` claim
    that is what the HSM socket authenticates on — the REST access token itself
    is *not* accepted there. So this broker has a second per-session credential
    like SmartAPI's feed token, except that it arrives folded inside the first
    one rather than beside it, which is why nothing extra is stored: the field
    already encrypted at rest contains it.

    Returns None for a token that is not a readable JWT or carries no key,
    rather than raising — the caller's job is to open a socket, and a
    credential this platform cannot read is not an exception in the transport.

    The signature is deliberately not verified. This is not authentication: the
    token was issued to us by Fyers over TLS and is being handed straight back
    to Fyers, which is the only party that can and does verify it. Verifying it
    here would need Fyers' public key and would buy nothing.
    """
    if not access_token or not isinstance(access_token, str):
        return None
    parts = access_token.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        # `urlsafe_b64decode` needs the padding a JWT omits; three `=` is always
        # enough and never too many, since Python ignores surplus padding.
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "===").decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        return None
    if not isinstance(payload, dict):
        return None
    key = payload.get("hsm_key")
    return str(key) if key else None


def token_expiry(access_token: Any) -> Optional[datetime]:
    """The `exp` claim of a Fyers access token, as an aware UTC datetime.

    Preferred over the calendar rule in :meth:`FyersAdapter.session_expiry`
    because it is the broker's own answer rather than our model of its policy —
    and the two differ in the case that matters, a token issued minutes before
    the daily cut-off.
    """
    if not access_token or not isinstance(access_token, str):
        return None
    parts = access_token.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "===").decode("utf-8"))
        return datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
    except (ValueError, TypeError, KeyError, OverflowError, OSError, UnicodeDecodeError, binascii.Error):
        return None


# ── HSM request framing ─────────────────────────────────────────────────────


def _field(field_id: int, payload: bytes) -> bytes:
    """One `<id><uint16 length><payload>` request field."""
    return bytes([field_id]) + struct.pack(">H", len(payload)) + bytes(payload)


def _hsm_request(request_type: int, fields: Sequence[bytes], declared_length: int) -> bytes:
    """One HSM request frame.

    `declared_length` is passed in rather than computed, and that is the point.
    The reference client computes it three different ways: the auth frame
    declares its true payload length, the mode frame declares **0**, and the
    subscribe frame declares a number that includes the lengths of an access
    token and a source string neither of which is in the frame. A server that
    read the field could not accept all three, so it does not read it — and the
    values are reproduced exactly rather than "fixed", because the bytes a
    working client puts on the wire are evidence and the bytes we reason our way
    to are a hypothesis. Getting this wrong is not a crash; it is a socket that
    connects, authenticates and never delivers a price.
    """
    body = bytes([request_type, len(fields)]) + b"".join(fields)
    return struct.pack(">H", declared_length) + body


def auth_frame(key: str) -> bytes:
    """The credential frame — the first thing this feed sends.

    Unlike every other broker here, this IS the authentication: the handshake
    carries nothing, so a socket that never sends this is an open connection
    that is never told who it belongs to.
    """
    fields = (
        _field(1, key.encode("utf-8")),
        _field(2, CONNECTION_MODE.encode("utf-8")),
        _field(3, bytes([1])),
        _field(4, CLIENT_SOURCE.encode("utf-8")),
    )
    # 16 + the two variable payloads, which is the reference client's
    # `18 + len(key) + len(source) - 2`, written as what it actually measures.
    declared = 16 + len(key.encode("utf-8")) + len(CLIENT_SOURCE.encode("utf-8"))
    return _hsm_request(REQ_AUTH, fields, declared)


def mode_frame(mode: int = MODE_LITE, channel: int = CHANNEL_NUMBER) -> bytes:
    """Narrow this connection's channel to the smallest record the feed offers.

    Sent before the subscription, not after: the mode governs which record kind
    the server publishes for a channel, and subscribing first would open a
    window in which full records arrive for instruments this adapter asked for
    in lite mode.

    The channel is addressed as a **bit** in a 64-bit mask rather than as a
    number, because HSM's pause/resume operates on sets of channels.
    """
    fields = (
        _field(1, struct.pack(">Q", 1 << channel)),
        _field(2, bytes([mode])),
    )
    return _hsm_request(REQ_MODE, fields, 0)


def _scrip_list(topics: Sequence[str]) -> bytes:
    """`<uint16 count>` then each topic as `<uint8 length><ascii>`."""
    body = bytearray(struct.pack(">H", len(topics)))
    for topic in topics:
        raw = topic.encode("ascii")
        body.append(len(raw))
        body.extend(raw)
    return bytes(body)


def subscribe_frame(topics: Sequence[str], access_token: str = "", channel: int = CHANNEL_NUMBER) -> bytes:
    """One subscription frame for up to :data:`SUBSCRIBE_BATCH_SIZE` topics."""
    scrips = _scrip_list(topics)
    declared = 18 + len(scrips) + len(access_token or "") + len(CLIENT_SOURCE)
    fields = (_field(1, scrips), _field(2, bytes([channel])))
    return _hsm_request(REQ_SUBSCRIBE, fields, declared)


# ── HSM response decoding ───────────────────────────────────────────────────


class _Reader:
    """A bounds-checked cursor over one frame.

    Exists because an HSM data frame is a *sequence of variable-length records*
    and the reference client walks it with bare slicing: a short frame there
    reads past the end, silently produces empty strings and zero-length reads,
    and carries on decoding garbage into the record after it. Every read here
    either yields the bytes it promised or raises :class:`_Truncated`, so a
    damaged frame costs exactly the records that had not been read yet.
    """

    __slots__ = ("_data", "offset")

    def __init__(self, data: bytes, offset: int = 0) -> None:
        self._data = data
        self.offset = offset

    def _take(self, count: int) -> bytes:
        end = self.offset + count
        if count < 0 or end > len(self._data):
            raise _Truncated(f"needed {count} bytes at offset {self.offset}")
        chunk = self._data[self.offset : end]
        self.offset = end
        return chunk

    def u8(self) -> int:
        return self._take(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self._take(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self._take(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self._take(4))[0]

    def topic_id(self) -> int:
        """The server's numeric handle for a subscribed topic.

        Read little-endian, matching the reference client's native-order `"H"`.
        The value is opaque — it is minted by the server, used only as a
        dictionary key, and never compared to anything we compute — so the only
        property that matters is that the snapshot and the updates that follow
        it are read the same way, and they are.
        """
        return struct.unpack("<H", self._take(2))[0]

    def text(self, count: int) -> str:
        return self._take(count).decode("utf-8", errors="ignore")

    def prefixed_text(self) -> str:
        return self.text(self.u8())

    def skip(self, count: int) -> None:
        self._take(count)


class _Truncated(ValueError):
    """A frame ended in the middle of a record."""


class _Topic:
    """What a snapshot established about one instrument, for one connection."""

    __slots__ = ("name", "scale")

    def __init__(self, name: str, scale: float) -> None:
        self.name = name
        self.scale = scale


def _price(raw: int, scale: float) -> Optional[float]:
    """A raw HSM integer as rupees, or None when it is not a price.

    THE PRICE RULE, AND WHY NO PREDECESSOR'S DIVISOR WORKS
    -------------------------------------------------------
    Fyers does not have a divisor. It carries `multiplier` and `precision` **in
    the snapshot, per instrument**, and the price is `raw / (10**precision *
    multiplier)`. Kite reads its scale out of the low byte of its instrument
    token, SmartAPI keys a table on its segment field, Upstox needs none because
    it sends a `double`; all three are constants of the *broker*, and this one is
    a value on the wire.

    The trap is specific and sharp: NSE cash quotes arrive with `precision=2,
    multiplier=1`, so a hardcoded ÷100 is **correct for the instruments anybody
    would test with** and silently wrong for currency (precision 4) and for any
    instrument Fyers scales differently. A copied divisor here does not fail
    loudly on the first tick — it fails on the first currency position.
    """
    if raw == NO_VALUE or not scale:
        return None
    return raw / scale


def _snapshot_record(reader: _Reader) -> Tuple[int, _Topic, Optional[int]]:
    """Walk one snapshot record; return `(topic_id, topic, raw ltp)`.

    The record is self-describing and is the only kind that is: it names the
    topic, carries the whole positional field list, and — critically — carries
    the `multiplier` and `precision` that every *later* record for this topic
    will be scaled by and will not repeat.

    Walked in full even when the topic turns out to be one this adapter cannot
    price, because the next record in the frame starts where this one ends.
    """
    topic_id = reader.topic_id()
    name = reader.prefixed_text()
    field_count = reader.u8()
    values = [reader.i32() for _ in range(field_count)]
    # Two bytes the published field list does not account for and the reference
    # client skips without naming. Skipped identically: a field whose meaning is
    # unknown is not a field to guess at, and mis-sizing it would shift the
    # scale that every subsequent price depends on.
    reader.skip(2)
    multiplier = reader.u16()
    precision = reader.u8()
    # `exchange`, `exchange_token`, `symbol` — the broker's own strings. Read to
    # advance the cursor, deliberately not used: the topic name already carries
    # the identity, and taking the symbol from here would make an arriving tick
    # name an instrument the account may not hold, bypassing `InstrumentMap`.
    for _ in range(3):
        reader.prefixed_text()
    scale = (10.0**precision) * multiplier if multiplier else 0.0
    ltp = values[LTP_FIELD] if len(values) > LTP_FIELD else None
    return topic_id, _Topic(name, scale), ltp


def _full_record(reader: _Reader) -> Tuple[int, Optional[int]]:
    """Walk one full-mode update; return `(topic_id, raw ltp)`."""
    topic_id = reader.topic_id()
    field_count = reader.u8()
    values = [reader.i32() for _ in range(field_count)]
    return topic_id, (values[LTP_FIELD] if len(values) > LTP_FIELD else None)


def _lite_record(reader: _Reader) -> Tuple[int, int]:
    """Walk one lite-mode update; return `(topic_id, raw ltp)`.

    Seven bytes, and six of them are not the price. This is the record the whole
    connection scope exists for: there is no instrument name in it, no scale in
    it, and nothing in it that identifies anything outside this socket.
    """
    topic_id = reader.topic_id()
    return topic_id, reader.i32()


class FyersFeedConnection(BrokerStreamChannel):
    """The Fyers market-data codec, scoped to ONE socket.

    Holds the topic table a snapshot builds and every later record is read
    against, plus the session whose credential the opening frame carries. The
    lifetime is the connection's: the transport builds one before the first
    frame is sent and drops it when the socket ends, so a reconnect necessarily
    starts from an empty table.

    That is a correctness requirement rather than tidiness. The server mints
    topic ids per connection, so a table carried across a reconnect maps the new
    connection's numbers onto the *old* connection's instruments — and the
    result is not an error but a price filed under another company's name.

    A connection instance is never shared between users, which the previous
    three brokers' shared-singleton codecs could not have offered and did not
    need.
    """

    name = MARKET_CHANNEL
    protocol = STREAM_PROTOCOL
    delivers = frozenset({StreamEventKind.TICKS})

    def __init__(self, session: dict = None, credentials: Dict[str, str] = None) -> None:
        self._session = dict(session or {})
        self._credentials = dict(credentials or {})
        self._topics: Dict[int, _Topic] = {}

    # -- what this connection sends first ------------------------------------
    def subscribe_frames(self, instruments: Sequence[Any] = None) -> List[Any]:
        """Authenticate, narrow the mode, then subscribe — in that order.

        Three frames where Kite sends two and Upstox one, and the first of them
        is the reason this method needs a connection at all: HSM authenticates
        **in the data channel**, so the credential is part of the opening
        conversation rather than part of the handshake.

        `bytes`, not `str`: HSM is a binary protocol end to end, and the
        transport forwards exactly what this returns without re-encoding it
        (D4.2), so the choice is made here. A text frame would be discarded by
        the server, producing a socket that connects, reports its link up, and
        never delivers a price.

        An unreadable access token yields *no* frames rather than a malformed
        one. The socket then sits unauthenticated until the server closes it and
        the transport reconnects on its ordinary backoff — the same outcome any
        broker has for a credential this platform cannot use, and strictly
        better than putting a guess on the wire.
        """
        key = hsm_key(self._session.get("access_token"))
        if not key:
            logger.warning(
                "Fyers market feed: the session's access token carries no feed key — "
                "the socket cannot be authenticated. The account needs reconnecting."
            )
            return []

        topics = self._topics_to_subscribe(instruments)
        frames: List[Any] = [auth_frame(key), mode_frame(MODE_LITE)]
        for start in range(0, len(topics), SUBSCRIBE_BATCH_SIZE):
            frames.append(
                subscribe_frame(
                    topics[start : start + SUBSCRIBE_BATCH_SIZE],
                    access_token=str(self._session.get("access_token") or ""),
                )
            )
        return frames

    @staticmethod
    def _topics_to_subscribe(instruments: Sequence[Any] = None) -> List[str]:
        """The account's instruments as HSM topics: valid, de-duplicated, capped."""
        topics: List[str] = []
        seen = set()
        for value in instruments or ():
            topic = str(value).strip() if parse_instrument_id(value) else None
            if topic is None or topic in seen:
                continue
            seen.add(topic)
            topics.append(topic)
        if len(topics) > MAX_SUBSCRIBED_INSTRUMENTS:
            logger.warning(
                "Fyers market feed: %d instruments exceeds the %d-instrument connection "
                "limit — subscribing to the first %d",
                len(topics),
                MAX_SUBSCRIBED_INSTRUMENTS,
                MAX_SUBSCRIBED_INSTRUMENTS,
            )
            topics = topics[:MAX_SUBSCRIBED_INSTRUMENTS]
        # Sorted so a resubscribe after a portfolio sync produces a stable list.
        return sorted(topics)

    # -- what this connection makes of what arrives ---------------------------
    def decode(self, frame: Any) -> BrokerStreamEvent:
        """Decode one HSM frame.

        Binary only. HSM never sends text, so a text frame on this socket is not
        something to parse hopefully — it is something that should not be there.

        Nothing here raises for a frame it does not understand, and a truncated
        frame costs only the records after the damage: an HSM frame is a batch,
        and one short record must not throw away the prices that were read
        before it or drop a socket that is delivering them.
        """
        if not isinstance(frame, (bytes, bytearray)):
            return BrokerStreamEvent.ignore()
        data = bytes(frame)
        if len(data) < 3:
            return BrokerStreamEvent.ignore()

        response_type = data[2]
        if response_type == RESP_DATA:
            return BrokerStreamEvent.tick_event(self._data_frame(data))
        if response_type == RESP_AUTH:
            return self._auth_frame_result(data)
        if response_type in (RESP_SUBSCRIBE, RESP_UNSUBSCRIBE, RESP_MODE, RESP_RESUME, RESP_PAUSE):
            return self._control_result(data, response_type)
        return BrokerStreamEvent.ignore()

    @staticmethod
    def _status(data: bytes) -> Optional[str]:
        """The one-byte status of a control response, or None if it is not there."""
        if len(data) <= CONTROL_STATUS_OFFSET:
            return None
        return chr(data[CONTROL_STATUS_OFFSET])

    def _auth_frame_result(self, data: bytes) -> BrokerStreamEvent:
        """Classify the response to the credential frame.

        This is where a dead Fyers session is discovered, and it is the first
        broker here where that happens *in a frame on an established socket*
        rather than at the handshake: HSM accepts every connection and rejects
        the credential afterwards. Left unclassified, an expired token would be
        indistinguishable from a broker outage — the socket would connect, be
        refused, close, and reconnect on the backoff schedule indefinitely,
        while the account's market feed stayed registered and the user was never
        asked to reconnect. Fyers access tokens die daily, so that is every
        connected user, every day.
        """
        if self._status(data) == STATUS_OK:
            self._warn_if_acknowledgements_are_expected(data)
            return BrokerStreamEvent.ignore()
        return BrokerStreamEvent.auth_expired(
            "Fyers refused the market-feed credential — the session is no longer valid. "
            "Fyers access tokens expire daily; please reconnect."
        )

    @staticmethod
    def _warn_if_acknowledgements_are_expected(data: bytes) -> None:
        """Say so, once per connection, if the server asks to be acknowledged.

        HSM's auth response carries an "acknowledge every N data frames" count,
        and the reference client honours it by sending a ReqType-3 frame with
        the last message number. **This adapter does not**, because a codec here
        returns a decoded event and has no way to put a frame back on the wire —
        and adding one on a protocol detail that has never been observed
        non-zero would be a second contract extension built on a guess.

        If the count *is* non-zero and the server enforces it, the feed stops
        delivering after N frames with the socket still open. That is bounded
        rather than silent: `StreamingTickProvider` expires a tick after
        `DEFAULT_TICK_MAX_AGE_SECONDS`, so the account falls back to the delayed
        baseline within two minutes and stays correct. This log line is what
        turns "the Fyers feed went quiet" into a named cause, and it is the
        first item on this broker's live-validation list.
        """
        try:
            reader = _Reader(data, CONTROL_STATUS_OFFSET + 1)
            reader.u8()  # field id
            reader.u16()  # field length
            expected = reader.u32()
        except (_Truncated, struct.error):
            return
        if expected:
            logger.warning(
                "Fyers market feed: the server requested acknowledgement every %d frames, "
                "which this client does not send. If the feed goes quiet, this is why.",
                expected,
            )

    def _control_result(self, data: bytes, response_type: int) -> BrokerStreamEvent:
        """A subscribe / unsubscribe / mode / pause / resume acknowledgement.

        A rejection is reported as an ERROR event, never raised: HSM rejects a
        *request*, not a connection, so dropping a socket that is still
        delivering other instruments would turn a partial rejection into a total
        outage.
        """
        if self._status(data) == STATUS_OK:
            return BrokerStreamEvent.ignore()
        return BrokerStreamEvent.error(f"Fyers rejected a request (type {response_type})")

    def _data_frame(self, data: bytes) -> List[Dict[str, Any]]:
        """Walk one data frame's records and return the raw ticks it yielded.

        A frame is a batch of *mixed* record kinds — a snapshot for a newly
        subscribed instrument sits beside lite updates for instruments
        subscribed a minute ago — so every record is walked whether or not this
        adapter can price it. Skipping one by guessing its length would
        desynchronise every record after it, and the symptom of that is not an
        exception: it is prices decoded out of the middle of other records.
        """
        ticks: List[Dict[str, Any]] = []
        try:
            reader = _Reader(data, 3)
            reader.u32()  # message number, used only for acks
            record_count = reader.u16()
            for _ in range(record_count):
                kind = reader.u8()
                if kind == RECORD_SNAPSHOT:
                    topic_id, topic, raw = _snapshot_record(reader)
                    self._topics[topic_id] = topic
                elif kind == RECORD_FULL:
                    topic_id, raw = _full_record(reader)
                    topic = self._topics.get(topic_id)
                elif kind == RECORD_LITE:
                    topic_id, raw = _lite_record(reader)
                    topic = self._topics.get(topic_id)
                else:
                    # An unknown record kind has an unknown length, so there is
                    # no safe way to reach the next one. The records already read
                    # are kept; the rest of the frame is abandoned.
                    logger.debug("Fyers market feed: unknown record kind %s — rest of frame skipped", kind)
                    break
                tick = self._tick(topic, raw)
                if tick is not None:
                    ticks.append(tick)
        except (_Truncated, struct.error) as exc:
            logger.debug("Fyers market feed: frame ended mid-record (%s) — %d tick(s) kept", exc, len(ticks))
        return ticks

    @staticmethod
    def _tick(topic: Optional[_Topic], raw: Optional[int]) -> Optional[Dict[str, Any]]:
        """One raw tick dict, or None when this record is not a usable price.

        `None` — not an exception — for an update whose snapshot has not arrived,
        a topic kind whose field zero is not a traded price, an absent value, and
        an instrument whose scale is unusable. A feed's normal traffic contains
        the first of those every time an instrument is added, and a codec that
        raised on it would fill the log with noise from a healthy connection.

        Returns the pre-canonical dict `BrokerTick.from_broker` coerces, never a
        tick object: building the canonical type is the caller's step.
        """
        if topic is None or raw is None:
            return None
        parsed = parse_instrument_id(topic.name)
        if parsed is None:
            # A depth topic, or one from a segment this adapter cannot name —
            # and therefore cannot place on an exchange. Dropped rather than
            # defaulted: field zero of a depth record is a *bid*, and publishing
            # one as the traded price is wrong in a way nothing downstream can
            # detect.
            return None
        price = _price(raw, topic.scale)
        if price is None:
            return None
        return {
            "instrument_token": topic.name,
            "last_price": price,
            "exchange": SEGMENT_EXCHANGES.get(parsed[1]),
            # Lite mode carries no volume and no exchange timestamp. Fyers does
            # publish a true cumulative day volume, but only in the snapshot and
            # in full mode; carrying it once and then freezing it would be a
            # number that stops meaning what its name says. Fourth broker, same
            # limitation, reached independently each time.
            "volume": 0,
            "timestamp": None,
        }


class FyersMarketFeedChannel(BrokerStreamChannel):
    """Fyers' market-data connection — where to reach it and how to scope it.

    Declares HSM's per-connection instrument ceiling (D5.10), which the shard
    planner raises by opening another connection instead of trimming the
    account's portfolio. `SUBSCRIBE_BATCH_SIZE` is deliberately NOT that number:
    it is how many topics fit in one subscribe *frame*, a wire-framing fact this
    channel already handles on a single socket, and confusing the two would
    open four connections where one would do.

    Everything that survives a connection lives here (the URL, the keep-alive
    declaration, the handshake classification); everything that belongs to one
    connection lives in :class:`FyersFeedConnection`, which :meth:`open` mints.

    :attr:`delivers` is TICKS alone. Fyers serves order updates on a separate
    service this adapter does not implement, and the narrowing is what stops a
    future order channel ever being credited with a price it did not carry — or,
    the direction that actually bites, this channel being taken for that one when
    the account's market-data provider decides whether its feed is live.
    """

    name = MARKET_CHANNEL
    protocol = STREAM_PROTOCOL
    delivers = frozenset({StreamEventKind.TICKS})
    #: Instruments one HSM connection may hold. Per connection, so sharding
    #: raises it. No concurrent-connection ceiling is documented for this broker
    #: in the repository, so none is declared (LIM-D5.10-1).
    max_instruments_per_connection = MAX_SUBSCRIBED_INSTRUMENTS

    def endpoint(self, session: dict, credentials: Dict[str, str] = None) -> BrokerStreamEndpoint:
        """The HSM socket — no credential in the URL, and none in a header.

        A fourth authentication style, and the first that puts nothing at all in
        the handshake: not Kite's query string, not Upstox's bearer header, not
        SmartAPI's four headers. `safe_url` therefore has nothing to strip and
        the connection log line is the whole URL, which is the strongest form of
        the rule rather than an exception to it.

        `credentials` is unused: this feed authenticates with a key folded inside
        the user's own session token and needs none of the app-level material
        Kite's ticker puts in its query string. The parameter stays because it is
        the channel contract.

        The keep-alive is declared here rather than run here — HSM closes a
        connection that stops sending it, and the timer belongs to whoever owns
        the socket. See `stream.py`.
        """
        return BrokerStreamEndpoint(
            url=WS_URL,
            heartbeat_frame=HEARTBEAT_FRAME,
            heartbeat_interval=HEARTBEAT_INTERVAL,
        )

    def connect_error(self, error: BaseException) -> Optional[str]:
        """Whether a refused handshake means this session is dead.

        Present on the channel and not only on the adapter because the transport
        asks the *channel* — an adapter-level classifier alone would never be
        consulted for a broker that declares an explicit channel, and the failure
        of that omission is silent: an expired token reconnects on the backoff
        schedule forever while the account's feed stays registered.
        """
        return _session_refused(error)

    def open(self, session: dict, credentials: Dict[str, str] = None) -> FyersFeedConnection:
        """A codec scoped to the connection about to be opened (D4.10)."""
        return FyersFeedConnection(session, credentials)

    def subscribe_frames(self, instruments: Sequence[Any] = None) -> List[Any]:
        """What this feed sends with no session in hand: nothing.

        Not an oversight and not a stub. The opening frame is a credential, and
        this object — a registry singleton shared by every user of the broker —
        is precisely the scope that must not hold one. The transport calls
        :meth:`open` first and asks the result; this answer exists so that a
        caller who does not is left with an unauthenticated socket rather than
        with somebody else's key on the wire.
        """
        return []

    def decode(self, frame: Any) -> BrokerStreamEvent:
        """What one frame means with no connection behind it.

        A fresh connection that has seen nothing — which is exactly what this is
        — decodes the self-contained records (a snapshot, an auth result, a
        rejected request) and drops the updates, because an update for a topic
        no snapshot has introduced is not decodable and is dropped on a live
        connection too. One implementation, one set of semantics; the difference
        between this and a real connection is only what it has been told.
        """
        return FyersFeedConnection().decode(frame)


def _session_refused(error: BaseException) -> Optional[str]:
    """Reason string when Fyers refused the stream handshake outright.

    HSM authenticates in a frame, so the *expected* place a dead session is
    discovered is `FyersFeedConnection._auth_frame_result` and not here. This
    covers the other case — an edge or gateway in front of the socket rejecting
    the request before HSM sees it — for the same reason the other three
    adapters classify it: left unclassified, the transport cannot tell a dead
    token from a broker outage and reconnects into a rejection indefinitely.

    403 is included alongside 401 because a withdrawn app authorisation is
    equally unrecoverable by reconnecting and equally fixed by the user
    reconnecting the account.
    """
    status = getattr(error, "status_code", None)
    if status is None:
        # websockets >= 14 wraps the handshake response instead.
        status = getattr(getattr(error, "response", None), "status_code", None)
    if status in (401, 403):
        return (
            f"Fyers refused the stream handshake (HTTP {status}) — the session is no longer "
            "valid. Fyers access tokens expire daily; please reconnect."
        )
    return None


class FyersAdapter(BrokerAdapter):
    """Fyers API v3.

    Nothing in this module is referenced by name anywhere outside it and the
    registry entry in `__init__.py` — the property the framework's source sweeps
    assert for every broker and now assert for a fourth.
    """

    name = "fyers"
    display_name = "Fyers"

    #: The account surface plus the market feed. What is absent is as much a
    #: declaration as what is present:
    #:
    #: * **No order capabilities.** D4.10 is a market-data sprint and Fyers'
    #:   order surface is unvalidated against a live account here. The capability
    #:   model exists exactly so a partial broker is *declared* partial rather
    #:   than integrated with stub methods that lie — the Broker Gateway refuses
    #:   an undeclared capability before the adapter is reached, and the UI can
    #:   say so. Adding them later is an adapter change and nothing else.
    #: * **No ORDER_STREAM.** Fyers serves order updates on a *separate* socket
    #:   with a different protocol, which would be a second channel — the D4.7
    #:   mechanism is ready for it, and it belongs with the order surface.
    #: * **No SESSION_REFRESH.** Fyers issues a refresh token, but redeeming it
    #:   requires the user's trading **PIN**, which SECURITY.md forbids this
    #:   platform from holding. Declaring a refresh whose input we may not hold
    #:   would make the engine attempt a renewal that cannot succeed instead of
    #:   asking the user to reconnect.
    capabilities = frozenset(
        {
            BrokerCapability.PROFILE,
            BrokerCapability.HOLDINGS,
            BrokerCapability.POSITIONS,
            BrokerCapability.FUNDS,
            BrokerCapability.MARGINS,
            BrokerCapability.SESSION_INVALIDATE,
            BrokerCapability.TICK_STREAM,
        }
    )

    credential_spec = BrokerCredentialSpec(
        api_key_env="FYERS_APP_ID",
        api_secret_env="FYERS_SECRET_ID",
        redirect_url_env="FYERS_REDIRECT_URL",
        required=("api_key", "api_secret"),
    )

    #: Fyers' delivery product code.
    default_product = "CNC"
    default_variety = "regular"

    stream_protocol = STREAM_PROTOCOL

    # -- HTTP ----------------------------------------------------------------
    def _headers(self, session: dict = None) -> dict:
        """Fyers' header set.

        `Authorization` is `"<app id>:<access token>"` — the app identity and the
        user's token concatenated, which is neither a bearer token nor a signed
        header and is easy to get subtly wrong by analogy with either.
        """
        headers = {"Content-Type": "application/json", "Accept": "application/json", "version": "3"}
        if session is not None:
            token = (session or {}).get("access_token")
            if not token:
                raise BrokerAuthError("Fyers is not connected. Connect your account in Settings.")
            headers["Authorization"] = f"{self.credentials.api_key}:{token}"
        return headers

    async def _fyers(self, method: str, path: str, session: dict = None, body: dict = None) -> Any:
        """One Fyers call, with its envelope unwrapped and its errors mapped.

        Fyers answers HTTP 200 with `{"s": "error", "code": …, "message": …}` for
        application failures, so the transport-level handling in
        `BrokerAdapter._request` cannot see them — an unchecked caller would read
        a missing `holdings` key as an empty portfolio.
        """
        payload = await self._request(method, f"{BASE_URL}{path}", headers=self._headers(session), json_body=body)
        if not isinstance(payload, dict):
            raise BrokerError(
                "Fyers returned an unexpected response", user_message="Fyers returned an unexpected response."
            )
        if str(payload.get("s") or "").lower() == "error":
            code = payload.get("code")
            message = payload.get("message") or "Fyers request failed"
            if isinstance(code, int) and code in DEAD_SESSION_CODES:
                raise BrokerAuthError("Fyers session expired (Fyers access tokens expire daily). Please reconnect.")
            raise BrokerError(f"Fyers error [{code}]: {message}", user_message=message)
        return payload

    # -- authentication ------------------------------------------------------
    def get_login_url(self, user_id: str = None) -> dict:
        credentials = self.credentials
        if not credentials.api_key or not credentials.api_secret:
            return {
                "url": None,
                "configured": False,
                "message": "Fyers not configured. Add FYERS_APP_ID and FYERS_SECRET_ID to .env",
            }
        if not credentials.redirect_url:
            return {"url": None, "configured": False, "message": "Fyers not configured. Add FYERS_REDIRECT_URL to .env"}
        params = {
            "client_id": credentials.api_key,
            "redirect_uri": credentials.redirect_url,
            "response_type": "code",
        }
        if user_id:
            # Fyers echoes `state` back on the redirect, which is how the public
            # callback maps the session to the right app user — the same role it
            # plays for Upstox and Angel One. The `uid=` prefix is the platform's
            # own convention for that parameter, not Fyers'.
            params["state"] = f"uid={user_id}"
        return {"url": f"{BASE_URL}/generate-authcode?{urlencode(params)}", "configured": True}

    def parse_callback_params(self, params: Dict[str, str]) -> Optional[dict]:
        """Fyers redirects with `?s=ok&code=200&auth_code=…`, not `?code=…`.

        The override is not cosmetic and the default is not merely unhelpful
        here: Fyers puts an **HTTP-style status** in `code` and the actual
        authorization code in `auth_code`. The inherited OAuth2 parser would
        read `code="200"`, decide the callback succeeded, and post the string
        `"200"` to `validate-authcode` — a connect attempt that fails at the
        broker with a message about an invalid auth code, for a login that in
        fact worked.
        """
        params = params or {}
        auth_code = (params.get("auth_code") or "").strip()
        if not auth_code:
            return None
        if str(params.get("s") or "ok").lower() not in ("ok", "success", ""):
            return None
        return {"auth_code": auth_code}

    def _app_id_hash(self) -> str:
        """`SHA256("<app id>:<secret id>")` — how Fyers authenticates the exchange.

        The secret is never sent; its digest with the app id is. Computed here
        rather than stored so a rotated secret takes effect without a restart.
        """
        credentials = self.credentials
        return hashlib.sha256(f"{credentials.api_key}:{credentials.api_secret}".encode("utf-8")).hexdigest()

    async def exchange_token(self, auth_payload: dict) -> dict:
        """Exchange the redirect's `auth_code` for a session.

        The profile call afterwards is not decoration: it resolves the Fyers
        client id, and — more usefully — it is the cheapest proof that the token
        we are about to store actually works. A session that cannot be used is
        never stored as connected.
        """
        payload = auth_payload or {}
        auth_code = (payload.get("auth_code") or payload.get("code") or "").strip()
        if not auth_code:
            raise BrokerError("auth_code required")
        credentials = self.credentials
        if not credentials.api_key or not credentials.api_secret:
            raise BrokerError("Fyers not configured")

        data = await self._request(
            "POST",
            f"{BASE_URL}/validate-authcode",
            headers={"Content-Type": "application/json"},
            json_body={
                "grant_type": "authorization_code",
                "appIdHash": self._app_id_hash(),
                "code": auth_code,
            },
        )
        if not isinstance(data, dict) or not data.get("access_token"):
            message = (data or {}).get("message") if isinstance(data, dict) else None
            raise BrokerError(
                f"Fyers token exchange failed: {message or 'no access token returned'}",
                user_message=message or "Fyers did not return an access token. Please try again.",
            )

        access_token = str(data["access_token"])
        session = {"access_token": access_token}
        profile = await self.get_profile(session)
        now = datetime.now(timezone.utc)
        # The token's own `exp` when it has one, the calendar rule otherwise.
        # The two differ exactly where it matters — a token minted minutes before
        # the daily cut-off — and the broker's own answer wins.
        expires_at = token_expiry(access_token) or self.session_expiry(now)
        return {
            "access_token": access_token,
            "refresh_token": str(data.get("refresh_token") or ""),
            "expires_at": expires_at.isoformat(),
            "account_id": profile.get("account_id"),
            "profile": profile,
        }

    def session_expiry(self, connected_at: datetime) -> datetime:
        """Fyers access tokens stay valid until midnight IST.

        The fallback for a token whose `exp` cannot be read; :meth:`exchange_token`
        prefers the claim.
        """
        local = connected_at.astimezone(IST)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return midnight.astimezone(timezone.utc)

    async def invalidate_session(self, session: dict) -> None:
        """Logout: Fyers invalidates the access token."""
        if not (session or {}).get("access_token"):
            return
        try:
            await self._fyers("POST", "/logout", session, {})
        except Exception as e:
            logger.warning(f"Fyers session invalidation failed (session may already be dead): {e}")

    # -- account data --------------------------------------------------------
    async def get_profile(self, session: dict) -> dict:
        data = (await self._fyers("GET", "/profile", session)).get("data") or {}
        return {
            "account_id": data.get("fy_id"),
            "user_name": data.get("name") or data.get("display_name"),
            "email": data.get("email_id"),
            "broker": "FYERS",
            "exchanges": [],
            "products": [],
        }

    async def get_holdings(self, session: dict) -> list:
        payload = await self._fyers("GET", "/holdings", session)
        holdings = []
        for h in payload.get("holdings") or []:
            if not isinstance(h, dict):
                continue
            symbol, exchange = split_symbol(h.get("symbol"))
            qty = _int(h.get("quantity"))
            avg = _num(h.get("costPrice"))
            ltp = _num(h.get("ltp"))
            invested = qty * avg
            value = _num(h.get("marketVal")) or qty * ltp
            pnl = h.get("pl")
            holdings.append(
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "quantity": qty,
                    "average_price": avg,
                    "last_price": ltp,
                    "market_value": round(value, 2),
                    "invested_value": round(invested, 2),
                    "pnl": round(_num(pnl) if pnl is not None else value - invested, 2),
                    "pnl_percent": round(((value - invested) / invested * 100) if invested else 0, 2),
                    "product": h.get("holdingType"),
                    "isin": h.get("isin"),
                    "instrument_token": instrument_id(_fytoken(h)),
                }
            )
        return holdings

    async def get_positions(self, session: dict) -> list:
        payload = await self._fyers("GET", "/positions", session)
        positions = []
        for p in payload.get("netPositions") or []:
            if not isinstance(p, dict):
                continue
            symbol, exchange = split_symbol(p.get("symbol"))
            qty = _int(p.get("netQty"))
            positions.append(
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "product": p.get("productType"),
                    "quantity": qty,
                    "average_price": _num(p.get("netAvg") or p.get("avgPrice")),
                    "last_price": _num(p.get("ltp")),
                    "pnl": round(_num(p.get("pl")), 2),
                    "realised": round(_num(p.get("realized_profit")), 2),
                    "unrealised": round(_num(p.get("unrealized_profit")), 2),
                    "buy_quantity": _int(p.get("buyQty")),
                    "sell_quantity": _int(p.get("sellQty")),
                    "side": "LONG" if qty > 0 else ("SHORT" if qty < 0 else "FLAT"),
                    "instrument_token": instrument_id(_fytoken(p)),
                }
            )
        return positions

    async def get_funds(self, session: dict) -> dict:
        """Fyers reports funds as a *list of titled rows*, not a flat object.

        Matched on the row title rather than on its `id`, deliberately. The ids
        are stable in Fyers' own documentation but they are opaque integers, and
        a table of them written from an example response is a table nobody can
        check; the titles say what they are, and a title this platform does not
        recognise simply contributes nothing rather than landing in the wrong
        field.
        """
        payload = await self._fyers("GET", "/funds", session)
        rows = {}
        for row in payload.get("fund_limit") or []:
            if isinstance(row, dict) and row.get("title"):
                rows[str(row["title"]).strip().lower()] = _num(row.get("equityAmount"))

        def amount(*titles: str) -> float:
            for title in titles:
                if title in rows:
                    return rows[title]
            return 0.0

        total = amount("total balance")
        return {
            "available_margin": round(amount("available balance", "clear balance"), 2),
            "used_margin": round(amount("utilized amount", "utilised amount"), 2),
            "opening_balance": round(amount("clear balance", "opening balance"), 2),
            "payin": round(amount("fund transfer"), 2),
            "payout": round(amount("receivables"), 2),
            "collateral": round(amount("collaterals", "collateral"), 2),
            "total_balance": round(total, 2),
        }

    # -- realtime: instruments -------------------------------------------------
    def stream_instruments(self, holdings: list = None, positions: list = None) -> List[Any]:
        """This account's HSM topics, from its own synced rows.

        The same two lists every adapter here reads, producing a fourth kind of
        identifier — and, as with the previous two, nothing has to be fetched:
        `get_holdings` and `get_positions` already wrote the topic onto every
        row.

        **No instrument catalogue is involved**, and that is worth stating for
        this broker in particular: Fyers' own SDK resolves a symbol to a feed
        token by calling `data/symbol-token` over HTTP and consulting a bundled
        segment map. This adapter needs neither, because a fyToken is already on
        every holding and position row and the topic is derived from it locally —
        which is the difference between D4.10 being an adapter sprint and being a
        data-pipeline sprint.

        Sorted and de-duplicated so a resubscribe after a portfolio sync produces
        a stable subscription list.
        """
        topics = set()
        for row in list(holdings or []) + list(positions or []):
            if not isinstance(row, dict):
                continue
            topic = row.get("instrument_token")
            if parse_instrument_id(topic) is not None:
                topics.add(str(topic))
        return sorted(topics)

    # -- realtime: the channel --------------------------------------------------
    def stream_channels(self) -> Tuple[BrokerStreamChannel, ...]:
        """Fyers' one realtime connection.

        Named `market` rather than left on the default channel name even though
        there is only one: the stream registry keys on `(user, broker, channel)`
        and appears in diagnostics, and the day the order socket is added the
        existing entries should not have to be renamed to make room for it.
        """
        return (FyersMarketFeedChannel(),)

    def stream_endpoint(self, session: dict, credentials: dict = None) -> BrokerStreamEndpoint:
        """Where this broker's stream lives — the market channel's endpoint.

        Present because a broker declaring a realtime capability must be able to
        describe its stream at the adapter level, which the registry verifies at
        registration. Fyers has one stream and this is it; delegating rather than
        restating means the two cannot disagree about the URL or the keep-alive.
        """
        return FyersMarketFeedChannel().endpoint(session, credentials)

    def stream_connect_error(self, error: BaseException) -> Optional[str]:
        """Whether a refused handshake means this session is dead."""
        return _session_refused(error)

    def decode_stream_frame(self, frame: Any) -> BrokerStreamEvent:
        """What one Fyers frame means outside a connection.

        Delegates to the channel, which is the same connectionless answer
        described there: the self-contained records decode and the deltas do not.
        The transport never calls this — it decodes through the connection
        `BrokerStreamChannel.open()` gives it, which is strictly more capable —
        and the difference between the two is the whole subject of ADR-039.
        """
        return FyersMarketFeedChannel().decode(frame)


def _fytoken(row: Dict[str, Any]) -> Any:
    """The fyToken off a Fyers row, whichever way it spelled the key.

    Fyers' own responses use `fyToken` and `fytoken` in different places, and a
    row read back out of MongoDB may carry either. Accepting both is not
    defensive clutter: missing it means the row gets no `instrument_token`, the
    instrument is silently absent from the subscription, and the account's feed
    is quietly narrower than its portfolio.
    """
    for key in ("fyToken", "fytoken", "fy_token"):
        if row.get(key):
            return row[key]
    return None


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0

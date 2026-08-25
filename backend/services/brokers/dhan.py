"""Dhan (DhanHQ v2) — the FIFTH concrete streaming broker adapter (D4.11).

Everything Dhan-specific in this platform is in this file and in the one-line
registry entry in `__init__.py`. Nothing else changed to add it: no transport
change, no contract change, no Market Engine change, no provider change. That is
the property Developer Rule 9 of MARKET_DATA_ARCHITECTURE.md asks for, and the
fifth broker is the first one for which it held with **zero** generic edits —
D4.7 needed channels, D4.9 needed a keep-alive, D4.10 needed a connection scope,
and D4.11 needed nothing.

WHERE THIS SITS
---------------
::

    Dhan binary frame
          |  THIS MODULE (codec)              D4.2 boundary
    BrokerTick
          |  InstrumentMap                    D4.3 boundary
    MarketTick
          |  StreamingTickProvider            D4.4 / D4.5
    Market Gateway -> Source Manager -> Event Bus -> Market Engine

WHERE THE PROTOCOL FACTS COME FROM
-----------------------------------
Two independent sources, read against each other rather than one trusted:

  * the published DhanHQ v2 documentation (Live Market Feed, Annexure,
    Authentication, Portfolio, Funds);
  * Dhan's own reference client, `DhanHQ-py` (`src/dhanhq/marketfeed.py`), whose
    `struct` format strings are the authority on byte layout because they are
    what a working client actually reads.

Where the two disagree, the disagreement is recorded at the constant it affects
and the choice is justified there. Nothing below was inferred from Kite, Upstox,
SmartAPI or Fyers; the four differ from Dhan on every single one of endpoint,
auth style, framing, identity, price encoding and volume semantics.

THE THREE FACTS THAT MAKE DHAN DIFFERENT FROM ITS FOUR PREDECESSORS
--------------------------------------------------------------------
* **The price is not scaled.** It is an IEEE-754 `float32` in rupees, on the
  wire. Kite divides by a scale carried in the token's low byte, SmartAPI by a
  per-segment constant, Fyers by a multiplier the snapshot names, Upstox sends a
  double. Dhan sends a float and *there is no divisor at all* — applying one
  would publish a price a hundredth of the real one, and nothing would raise.
* **The identity is a pair the tick splits across two fields.** A `SecurityId`
  is unique only *within* an exchange segment, and the frame carries the segment
  as a byte and the id as an `int32`. The pair is the identity.
* **The subscribe frame is JSON text on a socket that answers in binary**, and
  it is capped at 100 instruments per message, so a subscription is a *list* of
  frames rather than one.

WHAT IS DELIBERATELY NOT HERE
------------------------------
Orders, trades, order placement and the order-update stream — D4.11 is a
market-data sprint and Dhan's order surface is unvalidated against a live
account. The capability model exists exactly so a partial broker is *declared*
partial instead of integrated with stub methods that lie.
"""

from __future__ import annotations

import json
import logging
import math
import struct
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from services.brokers.base import BrokerAdapter
from services.brokers.capabilities import BrokerCapability
from services.brokers.credentials import BrokerCredentialSpec
from services.brokers.errors import BrokerAuthError, BrokerError
from services.brokers.streaming import BrokerStreamEndpoint, BrokerStreamEvent

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# -- Hosts -------------------------------------------------------------------
#: The trading/data REST API. The version is in the path, not a header.
BASE_URL = "https://api.dhan.co/v2"

#: The login host. A *different* host from the API, and it is the partner flow
#: that lives here — see `DhanAdapter.credential_spec` for why the reference
#: SDK's app flow is not used.
AUTH_URL = "https://auth.dhan.co"

#: The live market feed. Authenticated by QUERY STRING, which is why
#: `BrokerStreamEndpoint.safe_url` exists and why no log line in this platform
#: may print `endpoint.url`.
WS_URL = "wss://api-feed.dhan.co"

#: DhanHQ access tokens are valid for 24 hours from generation. The
#: consume-consent response carries an explicit `expiryTime`, which is preferred
#: when present; this is the fallback for a response that omits it.
SESSION_HOURS = 24


# -- Exchange segments (Annexure) --------------------------------------------
#
# Dhan uses BOTH representations of a segment and they are not interchangeable:
#
#   * the SUBSCRIBE frame takes the segment's NAME  ("NSE_EQ"),
#   * every RESPONSE frame carries its ENUM as one byte (1).
#
# Both tables are therefore needed, and both are derived from the single literal
# pairing below so the value a subscription asks for and the value a decoded tick
# reports cannot drift apart.

#: Dhan segment name -> (enum value, the exchange name this platform uses).
#:
#: The right-hand exchange names are the platform's canonical vocabulary — the
#: same one holdings, quotes and the watchlist already key on — NOT another
#: broker's segment table copied across. SmartAPI numbers the same exchanges
#: completely differently, and Dhan's enum is its own.
SEGMENTS: Dict[str, Tuple[int, str]] = {
    "IDX_I": (0, "IDX"),
    "NSE_EQ": (1, "NSE"),
    "NSE_FNO": (2, "NFO"),
    "NSE_CURRENCY": (3, "CDS"),
    "BSE_EQ": (4, "BSE"),
    "MCX_COMM": (5, "MCX"),
    "BSE_CURRENCY": (7, "BCD"),
    "BSE_FNO": (8, "BFO"),
}

#: Response-frame segment byte -> Dhan segment name. Derived, never written twice.
SEGMENT_NAMES: Dict[int, str] = {enum: name for name, (enum, _exchange) in SEGMENTS.items()}

#: Dhan segment name -> the platform's exchange name. Derived likewise.
SEGMENT_EXCHANGES: Dict[str, str] = {name: exchange for name, (_enum, exchange) in SEGMENTS.items()}

#: A plain exchange name on a HOLDINGS row -> the cash segment it must mean.
#:
#: WHY THIS TABLE EXISTS AT ALL, AND WHY ONLY THE CASH SEGMENTS
#: ------------------------------------------------------------
#: `/holdings` does not return `exchangeSegment` the way `/positions` does; it
#: returns `exchange`, and **the documentation and the reference SDK disagree on
#: what that field holds**. The published sample shows `"exchange": "ALL"`; the
#: SDK's own response fixture (`tests/data/get-current-holdings.json`) shows
#: `"exchange": "NSE"`. Both shapes are handled, and neither is guessed at:
#:
#:   * a row naming a real exchange is a *delivery* holding, which can only be
#:     cash, so the segment follows from the exchange — this table;
#:   * a row saying `"ALL"` names no exchange at all. It is a consolidated view,
#:     and a `SecurityId` without a segment is not an identity: NSE id 1333 and
#:     BSE id 1333 are different companies. Such a row yields **no instrument
#:     id**, is not subscribed, and is counted and warned about rather than
#:     silently dropped (see :meth:`DhanAdapter.get_holdings`).
#:
#: Defaulting `"ALL"` to `NSE_EQ` was rejected deliberately. It would be right
#: most of the time and wrong *silently*: a BSE-only holding would be subscribed
#: as whatever NSE numbers that id, and that instrument's price would be
#: published under the user's stock's name. Nothing raises; the number is simply
#: another company's.
HOLDING_EXCHANGE_SEGMENTS: Dict[str, str] = {"NSE": "NSE_EQ", "BSE": "BSE_EQ"}


# -- The subscription protocol (Feed Request Codes, Annexure) -----------------

#: Subscribe to the **Quote** packet.
#:
#: WHY QUOTE AND NOT TICKER OR FULL
#: ---------------------------------
#: Three modes carry a price, and the choice is decided entirely by what the
#: canonical `MarketTick` can hold — symbol, exchange, price, volume:
#:
#:   * **Ticker (15)** is 16 bytes and carries the last price and nothing else.
#:     It has NO volume field, so every tick would reach the Market Engine with
#:     `volume=None` — a permanently half-empty canonical tick.
#:   * **Quote (17)** is 50 bytes and carries the last price plus the day's
#:     **cumulative traded volume**, which is exactly what `MarketTick.volume`
#:     means. It is the narrowest mode that fills the canonical contract.
#:   * **Full (21)** is 162 bytes and adds open interest and five levels of
#:     market depth — three times the bytes for fields the canonical tick has
#:     nowhere to put.
#:
#: So Quote: the smallest frame that leaves nothing canonical unfilled.
REQUEST_SUBSCRIBE_QUOTE = 17

#: Sent on a clean shutdown. Not used by this adapter — the transport owns the
#: socket's lifetime and closes it — but named so the constant is not mistaken
#: for a gap.
REQUEST_DISCONNECT = 12

#: Dhan rejects a subscribe message carrying more than 100 instruments.
#:
#: This is a *message* limit, not a session limit, so exceeding it is not a
#: reason to drop instruments — it is a reason to send more frames. The framework
#: has always allowed that: `stream_subscribe_frames` returns a list.
MAX_INSTRUMENTS_PER_FRAME = 100

#: Dhan accepts up to 5,000 instruments on one connection.
#:
#: THIS one is a genuine ceiling, and instruments beyond it are trimmed with a
#: WARNING naming the number rather than dropped in silence. Sharding a
#: subscription across several connections is D5's subject and is deliberately
#: not attempted here; a retail account's holdings and positions are three orders
#: of magnitude below this.
MAX_INSTRUMENTS_PER_CONNECTION = 5000

#: Dhan allows five concurrent feed connections per user and disconnects the
#: OLDEST with code 805 when a sixth opens. Recorded because it is the fact
#: behind :data:`DISCONNECT_TRANSIENT`; this adapter opens exactly one.
MAX_CONNECTIONS_PER_USER = 5


# -- The response protocol (Feed Response Codes, Annexure) --------------------
#
# Every response frame opens with the same 8-byte header, little-endian:
#
#     offset  size  field
#     0       1     feed response code
#     1       2     message length          (int16, deliberately NOT read)
#     3       1     exchange segment        (enum, see SEGMENTS)
#     4       4     security id             (int32)
#
# The reference client reads this as the prefix `<BHBI` of every one of its
# `struct.unpack` formats, which is what makes one header reader correct for all
# of them.
#
# `message_length` is deliberately not used to bound a read. The reference client
# never reads it either — it slices by the fixed size it expects — and trusting a
# length field to bound a read is how a codec turns a wrong number into a wrong
# price. The packet's own fixed size is the bound here.

HEADER_BYTES = 8
HEADER_FORMAT = "<BHBI"

#: Feed response codes, from the Annexure.
CODE_INDEX = 1
CODE_TICKER = 2
CODE_DEPTH = 3
CODE_QUOTE = 4
CODE_OI = 5
CODE_PREV_CLOSE = 6
CODE_MARKET_STATUS = 7
CODE_FULL = 8
CODE_DISCONNECT = 50

#: Response code -> (minimum frame size, offset of the day-volume int32 or None).
#:
#: The three priceable packets and ONLY the three priceable packets. All three
#: begin `<BHBIf`, so the last traded price is a `float32` at offset 8 in each —
#: that shared prefix is why one reader serves all three, and it is a fact read
#: off the reference client's format strings rather than assumed.
#:
#: WHAT IS *NOT* IN THIS TABLE IS THE POINT
#: -----------------------------------------
#: **Prev Close (6) is byte-for-byte indistinguishable from Ticker (2)** — both
#: are `<BHBIfI`, 16 bytes, with a `float32` at offset 8 — and Dhan sends one
#: unsolicited for every instrument at subscribe time. A codec that priced "any
#: 16-byte frame with a float at offset 8" would publish **yesterday's closing
#: price as a live tick**, once per instrument, immediately after every connect
#: and every reconnect. The response code is the only thing separating them,
#: which is why this table is keyed on it and why an unlisted code is never
#: priced.
#:
#: The volume offset is `None` for Ticker because Ticker has no volume field at
#: all — not zero, *absent*. Publishing `0` there would state that nothing traded
#: today, which is a different claim from "this feed does not say".
PRICEABLE: Dict[int, Tuple[int, Optional[int]]] = {
    CODE_TICKER: (16, None),
    CODE_QUOTE: (50, 22),
    CODE_FULL: (162, 22),
}

#: Where the last traded price sits in every priceable packet: `float32`, rupees.
PRICE_OFFSET = 8

#: Where the last-trade time (`int32`, epoch seconds) sits, per response code.
#: Ticker and Quote differ here because Quote inserts an `int16` traded quantity
#: ahead of it — one of the few places a single shared reader would be quietly
#: wrong rather than loudly wrong.
TRADE_TIME_OFFSETS: Dict[int, int] = {CODE_TICKER: 12, CODE_QUOTE: 14, CODE_FULL: 14}

#: Disconnect packet: the 8-byte header plus an `int16` reason code.
DISCONNECT_BYTES = 10
DISCONNECT_CODE_OFFSET = 8

#: Disconnect reasons that mean this session cannot stream, ever, until the user
#: acts. Reconnecting into one of these is exactly what a dead session must not
#: do: retry forever, stay registered as a healthy provider, and quietly burn CPU
#: and log lines.
#:
#: 806 ("Data APIs not subscribed") is in this set on purpose, and it is the one
#: judgement call here rather than a reading of the protocol. It is an
#: *entitlement* failure, not an authentication one — the token is fine, the
#: account simply is not licensed for the data feed — and the closed
#: `StreamEventKind` set offers exactly two outcomes, "stop" and "retry forever".
#: Retrying forever cannot make an unlicensed account licensed, so stopping is
#: the honest choice; the cost is that the user is asked to reconnect a session
#: that is technically still valid. The message carried through says what
#: actually happened rather than "token expired", so the log and the audit record
#: are not misleading. Recorded as a limitation in TASK.md.
DISCONNECT_FATAL: Dict[int, str] = {
    806: "Dhan disconnected the market feed: this account is not subscribed to Dhan's Data APIs.",
    807: "Dhan disconnected the market feed: the access token has expired. Please reconnect.",
    808: "Dhan disconnected the market feed: the client id or access token is invalid. Please reconnect.",
    809: "Dhan disconnected the market feed: Dhan could not authenticate the session. Please reconnect.",
}

#: Disconnect reasons the transport's ordinary backoff is the right answer to.
#:
#: 805 is "too many active connections". It is a *concurrency* condition rather
#: than an entitlement one — Dhan drops the OLDEST socket when a sixth opens, so
#: the very next attempt may well succeed — and permanently killing a user's feed
#: because they opened Dhan's own app would be the wrong trade. Reported as an
#: ERROR and left to the reconnect schedule.
DISCONNECT_TRANSIENT: Dict[int, str] = {
    805: "Dhan disconnected the market feed: too many active connections for this account.",
}

#: DhanHQ REST error codes that mean the session is finished.
#:
#: `DH-901` is Dhan's "Invalid Authentication". Deliberately a short list:
#: `DH-902` ("Invalid Access") is an entitlement rejection and `DH-903`..`DH-908`
#: are account, rate-limit, input, order, data and server errors — telling a user
#: to reconnect over any of those would misdiagnose the failure. Everything else
#: reaches the user as Dhan's own message.
DEAD_SESSION_CODES = frozenset({"DH-901"})


# -- Instrument identity -----------------------------------------------------


def instrument_id(segment: Any, security_id: Any) -> Optional[str]:
    """This account's handle for one Dhan instrument: ``"<SEGMENT>|<security id>"``.

    WHY A COMPOUND IDENTIFIER AND NOT THE SECURITY ID
    --------------------------------------------------
    A Dhan `SecurityId` is unique **within an exchange segment**, not across
    them. The subscribe frame names the pair, every response frame returns the
    pair, and `InstrumentMap` matches exactly one value — so the pair has to be
    that value. Storing the bare id would let a BSE tick resolve to an NSE
    holding and mark a position at another company's price, with nothing raised
    anywhere.

    WHY THE SEGMENT **NAME** AND NOT ITS ENUM
    ------------------------------------------
    Two reasons, both practical. The subscribe frame takes the name verbatim, so
    an id built from names needs no translation on the way out; and `/positions`
    already returns `exchangeSegment: "NSE_EQ"`, so a synced row and a wire
    subscription are the same string rather than two encodings of one fact.
    (Angel One's ids use its *numeric* segment because SmartAPI's subscription is
    numeric. Same principle, opposite answer, because the protocols differ —
    which is why neither was copied from the other.)

    Both directions are built from this one function, so the value written onto a
    synced row and the value a decoded tick carries cannot drift apart.

    Returns None for anything that is not a usable pair. A rejected instrument is
    absent from the subscribe frame rather than corrupting it.
    """
    if segment is None or isinstance(segment, bool):
        return None
    name = str(segment).strip().upper()
    if name not in SEGMENTS:
        return None
    if security_id is None or isinstance(security_id, bool):
        return None
    text = str(security_id).strip()
    if not text.isdigit() or int(text) <= 0:
        # Zero is what an absent identifier coerces to, and Dhan numbers
        # instruments from 1. Accepting it would put a subscription for nothing
        # on the wire.
        return None
    return f"{name}|{int(text)}"


def parse_instrument_id(value: Any) -> Optional[Tuple[str, str]]:
    """``"NSE_EQ|1333"`` -> ``("NSE_EQ", "1333")``, or None when it is not one of ours."""
    if value is None or isinstance(value, bool):
        return None
    segment, separator, security_id = str(value).strip().partition("|")
    if not separator:
        return None
    name = segment.strip().upper()
    text = security_id.strip()
    if name not in SEGMENTS or not text.isdigit() or int(text) <= 0:
        return None
    return name, str(int(text))


def holding_segment(exchange: Any) -> Optional[str]:
    """The exchange segment a `/holdings` row means, or None when it names none.

    ``"NSE"`` -> ``"NSE_EQ"``; ``"ALL"`` -> ``None``. See
    :data:`HOLDING_EXCHANGE_SEGMENTS` for why ``"ALL"`` is left unresolved rather
    than defaulted.
    """
    if exchange is None or isinstance(exchange, bool):
        return None
    name = str(exchange).strip().upper()
    if name in SEGMENTS:
        # A row that already names a segment outright needs no translation.
        return name
    return HOLDING_EXCHANGE_SEGMENTS.get(name)


def trading_symbol(value: Any) -> Optional[str]:
    """A Dhan trading symbol as the platform names instruments.

    Uppercased and trimmed, and **nothing else**. Dhan's published samples and
    its SDK fixtures both show bare names (``"TCS"``, ``"HDFC"``) with no series
    suffix, unlike SmartAPI's ``"TATASTEEL-EQ"`` and Fyers' ``"SBIN-EQ"``.
    Inventing a strip rule for a suffix this broker does not appear to send would
    risk renaming an instrument permanently — a hyphen this platform cannot prove
    is a series code is part of the name. Flagged for live validation rather than
    guessed at.
    """
    text = "" if value is None else str(value).strip().upper()
    return text or None


# -- The market-feed codec ---------------------------------------------------


def _price(value: float) -> Optional[float]:
    """A wire price, or None when the bytes did not decode to a usable number.

    Non-finite values are rejected **here**, at the wire, rather than left to the
    canonical boundary. A `float32` read out of a truncated or misaligned packet
    is genuinely capable of being NaN or +/-inf, and while `MarketTick` would
    refuse it (its range check fails every comparison against NaN), refusing it at
    the point of decode keeps a nonsense number out of `BrokerTick`, out of the
    logs, and out of every diagnostic that prints one.

    Zero is rejected for the same reason `MIN_STOCK_PRICE` rejects it: it is what
    a short read decodes to, and a zero would mark a whole position at nothing.
    """
    if not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def decode_frame(payload: bytes) -> Optional[Dict[str, Any]]:
    """Decode ONE Dhan binary frame into a raw tick dict, or None.

    `None` — never an exception — for every frame that is not a priceable tick: a
    short frame, an index or OI or market-status packet, a **previous-close**
    packet, a segment this adapter cannot name, an unusable price. A healthy Dhan
    connection carries all of those in its ordinary traffic, and a codec that
    raised on them would fill the log with noise from a working feed.

    Returns the pre-canonical dict `BrokerTick.from_broker` coerces, never a tick
    object: building the canonical type is the caller's step, so this stays a pure
    reading of the wire.

    Disconnect frames are NOT handled here — they are a connection-level fact
    rather than data. See :func:`decode_disconnect`.
    """
    if len(payload) < HEADER_BYTES:
        return None
    code, _length, segment, security_id = struct.unpack_from(HEADER_FORMAT, payload, 0)

    sizing = PRICEABLE.get(code)
    if sizing is None:
        # Includes prev-close, OI, index, depth and market-status packets. See
        # PRICEABLE for why "has a float at offset 8" is not the test.
        return None
    minimum, volume_offset = sizing
    if len(payload) < minimum:
        return None

    segment_name = SEGMENT_NAMES.get(segment)
    if segment_name is None:
        # A segment this adapter cannot name is one whose ids it cannot
        # disambiguate. Dropped rather than defaulted.
        return None
    identity = instrument_id(segment_name, security_id)
    if identity is None:
        return None

    (raw_price,) = struct.unpack_from("<f", payload, PRICE_OFFSET)
    price = _price(raw_price)
    if price is None:
        return None

    volume = 0
    if volume_offset is not None:
        (volume,) = struct.unpack_from("<I", payload, volume_offset)

    timestamp = None
    time_offset = TRADE_TIME_OFFSETS.get(code)
    if time_offset is not None:
        (epoch,) = struct.unpack_from("<i", payload, time_offset)
        # Verbatim, as a string, exactly as `BrokerTick.timestamp` requires:
        # brokers disagree on format and timezone and a wrong parse is worse than
        # an unparsed value.
        timestamp = str(epoch) if epoch > 0 else None

    return {
        "instrument_token": identity,
        # NO DIVISOR. Dhan quotes in rupees as a float32 — see the module
        # docstring. This is the one line in this file most likely to be
        # "corrected" by analogy with another broker, and doing so would publish
        # every price a hundredth of its real value with nothing raised.
        "last_price": price,
        "exchange": SEGMENT_EXCHANGES.get(segment_name),
        # Cumulative traded volume for the day, which is what MarketTick.volume
        # means. NOT `LTQ` (the last trade's size, an int16 at offset 12) and NOT
        # `total_buy_quantity` / `total_sell_quantity` (resting order-book depth,
        # at offsets 30 and 26) — three fields on this same packet that a
        # volume-shaped name makes easy to confuse, any of which would publish a
        # number that means something else entirely.
        "volume": int(volume),
        "timestamp": timestamp,
    }


def decode_disconnect(payload: bytes) -> Optional[BrokerStreamEvent]:
    """Classify a Dhan feed-disconnect frame, or None if it is not one.

    Dhan reports a dead session **in a frame** rather than by refusing the
    handshake, which is the opposite of Angel One and Fyers: the token rides in
    the connection's query string, so the socket opens first and the rejection
    arrives on it.
    """
    if len(payload) < DISCONNECT_BYTES or payload[0] != CODE_DISCONNECT:
        return None
    (reason,) = struct.unpack_from("<H", payload, DISCONNECT_CODE_OFFSET)
    fatal = DISCONNECT_FATAL.get(reason)
    if fatal:
        return BrokerStreamEvent.auth_expired(fatal)
    transient = DISCONNECT_TRANSIENT.get(reason)
    return BrokerStreamEvent.error(transient or f"Dhan disconnected the market feed (code {reason}).")


def _session_refused(error: BaseException) -> Optional[str]:
    """Reason string when Dhan refused a stream *handshake* for a dead session.

    Dhan's documented behaviour is to accept the socket and then send a
    disconnect frame, which :func:`decode_disconnect` handles — so this is a
    second line rather than the main one. It is implemented anyway because the
    credential is in the URL: a gateway in front of the feed is entitled to reject
    a malformed token at the HTTP upgrade, and an unclassified 401 is
    indistinguishable from a broker outage to the generic transport, which would
    then reconnect on the backoff schedule forever with the account's feed still
    registered.

    403 is included for the same reason Upstox's and Angel One's classifiers
    include it: a withdrawn authorisation is equally unrecoverable by reconnecting
    and equally fixed by the user reconnecting the account.
    """
    status = getattr(error, "status_code", None)
    if status is None:
        # websockets >= 14 wraps the handshake response instead.
        status = getattr(getattr(error, "response", None), "status_code", None)
    if status in (401, 403):
        return (
            f"Dhan refused the market-feed handshake (HTTP {status}) — the session is no longer "
            "valid. Dhan access tokens expire 24 hours after login; please reconnect."
        )
    return None


# -- The adapter -------------------------------------------------------------


class DhanAdapter(BrokerAdapter):
    """DhanHQ v2.

    Nothing in this module is referenced by name anywhere outside it and the
    registry entry in `__init__.py` — the property the framework's source sweeps
    assert for every broker and now assert for a fifth.
    """

    name = "dhan"
    display_name = "Dhan"

    #: The account surface plus the market feed. What is absent is as much a
    #: declaration as what is present:
    #:
    #: * **No order capabilities.** D4.11 is a market-data sprint and Dhan's
    #:   order surface is unvalidated against a live account here. The capability
    #:   model exists exactly so a partial broker is *declared* partial rather
    #:   than integrated with stub methods that lie — the Broker Gateway refuses
    #:   an undeclared capability before the adapter is reached, and the UI can
    #:   say so. Adding them later is an adapter change and nothing else.
    #: * **No ORDER_STREAM.** Dhan serves order updates on a *separate* socket
    #:   with its own JSON protocol, which would be a second channel — the D4.7
    #:   mechanism is ready for it, and it belongs with the order surface.
    #: * **No MARGINS.** Dhan's margin surface is a *calculator* that prices a
    #:   hypothetical order, not a report of the account's used and available
    #:   margin, which is what this capability means everywhere else. Declaring it
    #:   would make `/api/brokers` promise a number Dhan does not publish.
    #: * **No SESSION_REFRESH.** Dhan publishes a token-renewal endpoint, but its
    #:   behaviour on a partner-issued token is unverified here. Declaring a
    #:   refresh that may not succeed would make the engine attempt a renewal
    #:   instead of asking the user to reconnect. Recorded as an open question for
    #:   live validation.
    #: * **No SESSION_INVALIDATE.** Dhan publishes no logout or token-revocation
    #:   endpoint for the partner flow; a 24-hour token simply expires.
    capabilities = frozenset(
        {
            BrokerCapability.PROFILE,
            BrokerCapability.HOLDINGS,
            BrokerCapability.POSITIONS,
            BrokerCapability.FUNDS,
            BrokerCapability.TICK_STREAM,
        }
    )

    #: WHY `partner_id` / `partner_secret` AND NOT `app_id` / `app_secret`
    #: -------------------------------------------------------------------
    #: Dhan publishes two consent flows and the reference SDK uses the *other*
    #: one. `auth.py`'s `/app/generate-consent` requires the user's own
    #: `dhanClientId` as a query parameter **before** they log in — which a
    #: multi-tenant platform by definition does not have, since learning who the
    #: user is at Dhan is the entire purpose of the login. The `/partner/*` flow
    #: takes no client id, returns one on consume, and is the flow Dhan documents
    #: for exactly this case. The SDK's flow is a single-account developer
    #: convenience, not a disagreement about the protocol.
    credential_spec = BrokerCredentialSpec(
        api_key_env="DHAN_PARTNER_ID",
        api_secret_env="DHAN_PARTNER_SECRET",
        redirect_url_env="DHAN_REDIRECT_URL",
        required=("api_key", "api_secret"),
    )

    #: Dhan's delivery product code.
    default_product = "CNC"
    default_variety = "NORMAL"

    #: DhanHQ v2 live market feed — JSON subscribe frames, little-endian binary
    #: responses, query-string auth.
    stream_protocol = "dhan_feed_v2"

    # -- HTTP ----------------------------------------------------------------
    def _headers(self, session: dict = None) -> dict:
        """Dhan's header set.

        Two headers rather than one, and neither is a bearer token: the access
        token goes in `access-token` unprefixed, and the account's own id goes
        beside it in `client-id`. Taken from the reference client's
        `DhanHTTP.header`, which is what a working client actually sends.
        """
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if session is not None:
            token = (session or {}).get("access_token")
            if not token:
                raise BrokerAuthError("Dhan is not connected. Connect your account in Settings.")
            headers["access-token"] = str(token)
            headers["client-id"] = str((session or {}).get("account_id") or "")
        return headers

    async def _dhan(self, method: str, path: str, session: dict = None, body: dict = None) -> Any:
        """One Dhan call, with its error envelope mapped.

        Dhan answers an application failure with `{"errorType", "errorCode",
        "errorMessage"}` — sometimes under an HTTP error status that
        `BrokerAdapter._request` already classifies, and sometimes not. Checking
        here as well is what stops an unchecked caller reading an error envelope
        as an empty portfolio.
        """
        payload = await self._request(method, f"{BASE_URL}{path}", headers=self._headers(session), json_body=body)
        if isinstance(payload, dict):
            code = str(payload.get("errorCode") or "").strip().upper()
            if code:
                message = payload.get("errorMessage") or "Dhan request failed"
                if code in DEAD_SESSION_CODES:
                    raise BrokerAuthError(
                        "Dhan session expired (Dhan access tokens are valid for 24 hours). Please reconnect."
                    )
                raise BrokerError(f"Dhan error [{code}]: {message}", user_message=str(message))
        return payload

    # -- authentication ------------------------------------------------------
    def get_login_url(self, user_id: str = None) -> dict:
        """Dhan's login cannot be a bare URL, and this says so rather than inventing one.

        Every other broker here publishes a single URL a browser can be sent to,
        with the app's identity in the query string. Dhan's partner login is
        genuinely two steps: a server-to-server `generate-consent` call that mints
        a short-lived `consentId`, and only then a browser visit to
        `consent-login?consentId=...`. The first step needs the network, and this
        method is synchronous by contract.

        So it reports `configured` honestly and points at
        :meth:`generate_consent`, rather than returning a URL that would 404 or —
        worse — putting the partner secret in a query string to make one work.
        """
        credentials = self.credentials
        if not credentials.api_key or not credentials.api_secret:
            return {
                "url": None,
                "configured": False,
                "message": "Dhan not configured. Add DHAN_PARTNER_ID and DHAN_PARTNER_SECRET to .env",
            }
        if not credentials.redirect_url:
            return {"url": None, "configured": False, "message": "Dhan not configured. Add DHAN_REDIRECT_URL to .env"}
        return {
            "url": None,
            "configured": True,
            "requires_consent": True,
            "message": "Dhan login needs a consent id — request one to obtain the login URL.",
        }

    async def generate_consent(self, user_id: str = None) -> dict:
        """Step 1 and 2 of Dhan's partner consent flow: mint a consent id, return its login URL.

        The partner secret is sent as a **header** and never as a query parameter,
        so nothing credential-bearing appears in any URL this returns — which is
        what makes the returned URL safe to log, to redirect to, and to show a
        user. A `consentId` is a short-lived single-use session handle rather than
        a credential.
        """
        credentials = self.credentials
        if not credentials.api_key or not credentials.api_secret:
            raise BrokerError("Dhan not configured")
        data = await self._request(
            "POST",
            f"{AUTH_URL}/partner/generate-consent",
            headers={
                "partner_id": credentials.api_key,
                "partner_secret": credentials.api_secret,
                "Accept": "application/json",
            },
        )
        consent_id = str((data or {}).get("consentId") or "").strip() if isinstance(data, dict) else ""
        if not consent_id:
            raise BrokerError(
                "Dhan did not return a consent id",
                user_message="Dhan could not start the login. Please try again.",
            )
        return {
            "url": f"{AUTH_URL}/consent-login?{urlencode({'consentId': consent_id})}",
            "configured": True,
            "consent_id": consent_id,
        }

    def parse_callback_params(self, params: Dict[str, str]) -> Optional[dict]:
        """Dhan redirects with `?tokenId=...`, not `?code=...`.

        The inherited OAuth2 parser looks for `code`, finds nothing, and reports a
        failed login for one that in fact succeeded.
        """
        params = params or {}
        token_id = str(params.get("tokenId") or params.get("token_id") or "").strip()
        return {"token_id": token_id} if token_id else None

    async def exchange_token(self, auth_payload: dict) -> dict:
        """Step 3: exchange the redirect's `tokenId` for an access token.

        Everything the platform needs about the account comes back in this one
        response — `dhanClientId` in particular, which is required as a *header*
        on every later REST call and as a *query parameter* on the market feed. A
        session without it can authenticate and still not stream, so a response
        that omits it is refused rather than stored.
        """
        payload = auth_payload or {}
        token_id = str(payload.get("token_id") or payload.get("tokenId") or "").strip()
        if not token_id:
            raise BrokerError("tokenId required")
        credentials = self.credentials
        if not credentials.api_key or not credentials.api_secret:
            raise BrokerError("Dhan not configured")

        data = await self._request(
            "POST",
            f"{AUTH_URL}/partner/consume-consent?{urlencode({'tokenId': token_id})}",
            headers={
                "partner_id": credentials.api_key,
                "partner_secret": credentials.api_secret,
                "Accept": "application/json",
            },
        )
        if not isinstance(data, dict) or not data.get("accessToken"):
            message = (data or {}).get("errorMessage") if isinstance(data, dict) else None
            raise BrokerError(
                f"Dhan token exchange failed: {message or 'no access token returned'}",
                user_message=message or "Dhan did not return an access token. Please try again.",
            )

        access_token = str(data["accessToken"])
        client_id = str(data.get("dhanClientId") or "").strip()
        if not client_id:
            raise BrokerError(
                "Dhan returned no client id",
                user_message="Dhan did not identify the account. Please try connecting again.",
            )
        now = datetime.now(timezone.utc)
        expires_at = _expiry_time(data.get("expiryTime")) or self.session_expiry(now)
        return {
            "access_token": access_token,
            "expires_at": expires_at.isoformat(),
            "account_id": client_id,
            "profile": {
                "account_id": client_id,
                "user_name": data.get("dhanClientName"),
                "broker": "DHAN",
                "ucc": data.get("dhanClientUcc"),
                "power_of_attorney": bool(data.get("givenPowerOfAttorney")),
            },
        }

    def session_expiry(self, connected_at: datetime) -> datetime:
        """Dhan access tokens last 24 hours from generation.

        A duration rather than a calendar cut-off — unlike Kite, SmartAPI and
        Fyers, all of which die at a fixed hour. This is the fallback for a
        response with no `expiryTime`; :meth:`exchange_token` prefers Dhan's own
        answer.
        """
        return connected_at.astimezone(timezone.utc) + timedelta(hours=SESSION_HOURS)

    # -- account data --------------------------------------------------------
    async def get_profile(self, session: dict) -> dict:
        """Dhan's profile, with the connected account's own id as the fallback.

        A failure here is not treated as a dead account: the consent response
        already told us who this is, and `account_id` is the field the rest of the
        platform actually uses. `BrokerAuthError` is deliberately allowed to
        propagate — a genuinely dead token must not be papered over by a fallback.
        """
        account_id = str((session or {}).get("account_id") or "")
        try:
            data = await self._dhan("GET", "/profile", session)
        except BrokerAuthError:
            raise
        except BrokerError:
            data = None
        data = data if isinstance(data, dict) else {}
        return {
            "account_id": str(data.get("dhanClientId") or account_id),
            "user_name": data.get("dhanClientName") or data.get("clientName"),
            "email": data.get("email"),
            "broker": "DHAN",
            "exchanges": [],
            "products": [],
        }

    async def get_holdings(self, session: dict) -> list:
        """Dhan's delivery holdings, mapped onto the canonical holding shape.

        The instrument id is this sprint's sharp edge: `/holdings` reports
        `exchange`, not `exchangeSegment`, and the value may be a real exchange or
        the consolidated `"ALL"`. A row naming no exchange gets **no** instrument
        id — see :data:`HOLDING_EXCHANGE_SEGMENTS` — and is counted and warned
        about, never silently discarded and never defaulted onto a segment it
        might not be on. It still contributes its symbol, so the holding is a real
        holding everywhere else in the platform; what it cannot do is be
        subscribed for ticks.
        """
        payload = await self._dhan("GET", "/holdings", session)
        rows = payload if isinstance(payload, list) else (payload or {}).get("data") or []
        holdings = []
        unsegmented = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = trading_symbol(row.get("tradingSymbol"))
            if not symbol:
                continue
            segment = holding_segment(row.get("exchange"))
            if segment is None:
                unsegmented += 1
            quantity = _int(row.get("totalQty"))
            average = _num(row.get("avgCostPrice"))
            last_price = _num(row.get("lastTradedPrice"))
            invested = quantity * average
            value = quantity * last_price
            holdings.append(
                {
                    "symbol": symbol,
                    "exchange": SEGMENT_EXCHANGES.get(segment) if segment else None,
                    "quantity": quantity,
                    "average_price": average,
                    "last_price": last_price,
                    "market_value": round(value, 2),
                    "invested_value": round(invested, 2),
                    "pnl": round(value - invested, 2),
                    "pnl_percent": round(((value - invested) / invested * 100) if invested else 0, 2),
                    "product": self.default_product,
                    "isin": row.get("isin"),
                    "instrument_token": instrument_id(segment, row.get("securityId")),
                }
            )
        if unsegmented:
            logger.warning(
                "Dhan: %d of %d holdings report no exchange segment (Dhan's consolidated view) — "
                "those instruments cannot be subscribed for live ticks, because a security id "
                "without a segment identifies two different instruments",
                unsegmented,
                len(holdings),
            )
        return holdings

    async def get_positions(self, session: dict) -> list:
        """Dhan's open positions. These DO carry `exchangeSegment`, so they map fully."""
        payload = await self._dhan("GET", "/positions", session)
        rows = payload if isinstance(payload, list) else (payload or {}).get("data") or []
        positions = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = trading_symbol(row.get("tradingSymbol"))
            if not symbol:
                continue
            segment = str(row.get("exchangeSegment") or "").strip().upper() or None
            quantity = _int(row.get("netQty"))
            positions.append(
                {
                    "symbol": symbol,
                    "exchange": SEGMENT_EXCHANGES.get(segment) if segment else None,
                    "product": row.get("productType"),
                    "quantity": quantity,
                    "average_price": _num(row.get("buyAvg")) if quantity >= 0 else _num(row.get("sellAvg")),
                    "last_price": _num(row.get("costPrice")),
                    "pnl": round(_num(row.get("realizedProfit")) + _num(row.get("unrealizedProfit")), 2),
                    "realised": round(_num(row.get("realizedProfit")), 2),
                    "unrealised": round(_num(row.get("unrealizedProfit")), 2),
                    "buy_quantity": _int(row.get("buyQty")),
                    "sell_quantity": _int(row.get("sellQty")),
                    "side": _side(row.get("positionType"), quantity),
                    "instrument_token": instrument_id(segment, row.get("securityId")),
                }
            )
        return positions

    async def get_funds(self, session: dict) -> dict:
        """Dhan's fund limits.

        `availabelBalance` is spelled exactly that way in Dhan's published
        response and in its reference client. The correct spelling is accepted as
        well, so a future fix at Dhan does not silently zero every user's balance
        — but the misspelling is read first, because it is what the API actually
        sends today.
        """
        payload = await self._dhan("GET", "/fundlimit", session)
        data = payload if isinstance(payload, dict) else {}
        available = _num(data.get("availabelBalance")) or _num(data.get("availableBalance"))
        return {
            "available_balance": round(available, 2),
            "used_margin": round(_num(data.get("utilizedAmount")), 2),
            "opening_balance": round(_num(data.get("sodLimit")), 2),
            "payin": round(_num(data.get("receiveableAmount")), 2),
            "payout": round(_num(data.get("blockedPayoutAmount")), 2),
            "collateral": round(_num(data.get("collateralAmount")), 2),
            "total_balance": round(_num(data.get("withdrawableBalance")) or available, 2),
        }

    # -- realtime: instruments -----------------------------------------------
    def stream_instruments(self, holdings: list = None, positions: list = None) -> List[Any]:
        """This account's Dhan instrument ids, from its own synced rows.

        The same two lists every other adapter here reads, producing a fifth kind
        of identifier — and, as with Angel One and Fyers, nothing has to be
        fetched: `get_holdings` and `get_positions` already wrote the
        segment-qualified id onto every row they could. **No instrument catalogue
        is involved**, which is why D4.11 needed no catalogue sprint; the cost is
        that the feed covers what the account holds and nothing else, which is
        exactly the scope D4.5 subscribes and grants coverage for.

        Rows whose segment Dhan did not report carry no id and are absent here.
        That is the honest outcome of the `"ALL"` finding above rather than a
        silent drop: `get_holdings` has already warned, with a count.

        Sorted and de-duplicated so a resubscribe after a portfolio sync produces
        a stable subscription list.
        """
        identifiers = set()
        for row in list(holdings or []) + list(positions or []):
            if not isinstance(row, dict):
                continue
            identity = row.get("instrument_token")
            if parse_instrument_id(identity) is not None:
                identifiers.add(str(identity))

        def order(value: str) -> Tuple[str, int]:
            # Sorted on the parsed pair rather than the raw string, so NSE_EQ|10
            # does not precede NSE_EQ|9 and two runs cannot produce
            # different-looking lists for one portfolio.
            segment, security_id = parse_instrument_id(value)
            return segment, int(security_id)

        return sorted(identifiers, key=order)

    # -- realtime: the DhanHQ v2 codec ---------------------------------------
    def stream_endpoint(self, session: dict, credentials: dict = None) -> BrokerStreamEndpoint:
        """The live market feed, authenticated by QUERY STRING.

        Kite's style rather than Upstox's bearer header, SmartAPI's four headers
        or Fyers' in-band credential frame — which means this endpoint's `url`
        carries a live access token and **must never be logged**.
        `BrokerStreamEndpoint.safe_url` is what the transport prints; the rule is
        enforced there rather than merely remembered here.

        `authType=2` is a fixed protocol constant, not a credential, and
        `version=2` selects the v2 feed. Both are required — a connection missing
        either is refused.

        NO APPLICATION-LEVEL KEEP-ALIVE IS DECLARED, and that is a protocol
        finding rather than an omission. Dhan's server sends a **WebSocket
        protocol ping** every 10 seconds and closes a connection that has not
        answered within 40; a protocol ping is answered by the `websockets`
        library itself, in both peers, without either application seeing it. That
        is the exact opposite of Angel One, which does not count protocol pings at
        all and requires a text frame in the data channel — the case
        `heartbeat_frame` was added for in D4.9. Dhan needs a keep-alive and gets
        it for free. The `ping_interval` / `ping_timeout` defaults left in place
        here are the same values Dhan's own reference client runs with, since it
        calls `websockets.connect(url)` with no overrides.
        """
        session = session or {}
        query = urlencode(
            {
                "version": "2",
                "token": str(session.get("access_token") or ""),
                "clientId": str(session.get("account_id") or ""),
                "authType": "2",
            }
        )
        return BrokerStreamEndpoint(url=f"{WS_URL}?{query}")

    def stream_subscribe_frames(self, instruments: list = None) -> List[Any]:
        """Dhan's subscribe messages — JSON, sent as **text**, in batches of 100.

        Four Dhan specifics, and an assumption shaped by any previous broker gets
        each of them wrong:

        * **many frames, not one.** Dhan rejects a message carrying more than 100
          instruments, so a 250-instrument account is three frames. This is a
          message limit rather than a session limit, so nothing is dropped for it
          — `stream_subscribe_frames` has always returned a list precisely so a
          broker can say this.
        * **`str`, not `bytes`.** The transport forwards exactly what this returns
          without re-encoding it (D4.2), so the choice is made here.
        * **the segment is sent by NAME**, not as the enum the response frames
          carry. Both live in `SEGMENTS`, derived from one table.
        * **`InstrumentCount` is per frame**, not per subscription. Sending the
          total on each batch would describe a message that is not the one being
          sent.

        The 5,000-instrument connection ceiling IS enforced, and instruments
        beyond it are trimmed with a WARNING that names the number — not dropped
        in silence. Sharding the remainder across a second connection is D5's
        subject and is deliberately not attempted here.
        """
        parsed: List[Tuple[str, str]] = []
        for value in instruments or []:
            pair = parse_instrument_id(value)
            if pair is not None and pair not in parsed:
                parsed.append(pair)
        if not parsed:
            return []
        if len(parsed) > MAX_INSTRUMENTS_PER_CONNECTION:
            logger.warning(
                "Dhan market feed: %d instruments exceeds the %d-instrument connection limit — "
                "subscribing to the first %d",
                len(parsed),
                MAX_INSTRUMENTS_PER_CONNECTION,
                MAX_INSTRUMENTS_PER_CONNECTION,
            )
            parsed = parsed[:MAX_INSTRUMENTS_PER_CONNECTION]

        frames: List[Any] = []
        for start in range(0, len(parsed), MAX_INSTRUMENTS_PER_FRAME):
            batch = parsed[start : start + MAX_INSTRUMENTS_PER_FRAME]
            frames.append(
                json.dumps(
                    {
                        "RequestCode": REQUEST_SUBSCRIBE_QUOTE,
                        "InstrumentCount": len(batch),
                        "InstrumentList": [
                            {"ExchangeSegment": segment, "SecurityId": security_id} for segment, security_id in batch
                        ],
                    }
                )
            )
        return frames

    def stream_connect_error(self, error: BaseException) -> Optional[str]:
        """Whether a refused handshake means this session is dead."""
        return _session_refused(error)

    def decode_stream_frame(self, frame: Any) -> BrokerStreamEvent:
        """Decode one Dhan market-feed frame.

        The feed is **binary only** in the response direction — there is no text
        error envelope of the kind SmartAPI mixes onto its socket, and no
        acknowledgement of a subscription at all. Everything Dhan says, including
        "your token is dead", is a binary packet whose first byte is a response
        code.

        Order matters. A disconnect frame is checked first, because it is a
        connection-level fact the transport must act on rather than data, and
        because its 10 bytes would otherwise fall through the priceable table as
        an unrecognised code and be ignored — leaving a stream that reconnects
        forever into a session Dhan has already said is finished.
        """
        if not isinstance(frame, (bytes, bytearray)):
            # A text frame is not part of this protocol. Ignored rather than
            # logged as an error: an intermediary is entitled to send one, and a
            # codec that shouted about it would shout on a healthy connection.
            return BrokerStreamEvent.ignore()
        payload = bytes(frame)

        disconnected = decode_disconnect(payload)
        if disconnected is not None:
            return disconnected

        tick = decode_frame(payload)
        if tick is None:
            return BrokerStreamEvent.ignore()
        return BrokerStreamEvent.tick_event([tick])


# -- Coercion helpers --------------------------------------------------------


def _expiry_time(value: Any) -> Optional[datetime]:
    """Dhan's `expiryTime`, which is an ISO timestamp **in IST with no offset**.

    A naive timestamp is read as IST rather than UTC — that is what Dhan
    documents — and reading it as UTC would place every session's expiry five and
    a half hours late, so a dead token would be treated as live for the whole of a
    trading morning. A value that cannot be parsed returns None and the caller
    falls back to the 24-hour rule, which is never wrong by more than the drift
    between the two.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(timezone.utc)


def _side(position_type: Any, quantity: int) -> str:
    """Dhan states the side outright; the net quantity is the fallback."""
    text = str(position_type or "").strip().upper()
    if text in ("LONG", "SHORT"):
        return text
    if text == "CLOSED":
        return "FLAT"
    return "LONG" if quantity > 0 else ("SHORT" if quantity < 0 else "FLAT")


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


__all__ = ["DhanAdapter"]

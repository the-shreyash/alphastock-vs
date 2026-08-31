# StockAssist AI
## Broker Integration Documentation

Version: 1.2

Status: Framework Implemented (Sprint D3, 2026-08-20); streaming contract / codec boundary Implemented (Sprint D4.2, 2026-08-21, ADR-032); five concrete market feeds implemented — Zerodha (D4.6, ADR-036), Upstox (D4.7, ADR-037), Angel One (D4.9, ADR-038), Fyers (D4.10, ADR-039), Dhan (D4.11, ADR-040 — **the first that needed no framework change at all**). **All five deterministic-validated only; live validation not performed.**

---

# Purpose

This document defines how StockAssist AI integrates with stock brokers.

Broker integration enables users to:

• Connect brokerage accounts

• View live portfolio

• View holdings

• View positions

• Place orders

• Modify orders

• Cancel orders

• Track execution

• Receive real-time updates

• Automatically upgrade their market data feed to the broker's streaming WebSocket (see MARKET_DATA_ARCHITECTURE.md)

The platform never stores user credentials directly.

Only secure tokens and broker-approved authentication methods are used.

---

# Design Principles

The broker layer must be:

Provider Independent

Secure

Reliable

Scalable

Event Driven

Real-Time

Future Ready

Every broker should implement the same interface.

The Trading Engine should never know which broker is connected.

---

# Supported Brokers

## Phase 1

Zerodha Kite Connect — implemented, incl. market feed (D4.6)

Upstox API — implemented, incl. market feed (D4.7)

Angel One SmartAPI — market feed implemented (D4.9); trading surface outstanding

Fyers API v3 — market feed implemented (D4.10); trading surface outstanding

Dhan DhanHQ v2 — market feed implemented (D4.11); trading surface outstanding

---

## Phase 2

Groww (if public APIs become available)

Alice Blue

---

## Phase 3

Interactive Brokers

Alpaca

Binance (Crypto)

International Brokers

---

# High Level Architecture

Implemented as described in Sprint D3 (ADR-031).

```

User

↓

Trading Engine / Portfolio Engine / Routes / AI

↓

Broker Engine            sessions, encryption, persistence, sync, audit, events

↓

Broker Gateway           capability enforcement · canonical contracts
                         error normalization · health          ← the choke point

↓

Broker Registry          the brokers this deployment knows

↓

Broker Adapter           the only code that speaks a broker's protocol

↓

Broker API

↓

Canonical Broker Data

↓

Event Bus

↓

Portfolio Engine · Notification Engine · AI Engine · Source Manager

```

**Nothing above the Broker Gateway may hold a `BrokerAdapter.`** This is the broker-side equivalent of MARKET_DATA_ARCHITECTURE.md's "never bypass the Market Gateway", and it exists for the same reason: a choke point is the only place a cross-cutting guarantee can be made once instead of at every call site.

Code: `backend/services/brokers/` (framework + adapters), `backend/services/broker_engine.py` (engine).

---

# Broker Provider Framework (D3)

## Module map

| Module | Responsibility |
|---|---|
| `base.py` | The adapter contract |
| `capabilities.py` | What a broker can do, declared by the broker |
| `contracts.py` | The canonical shapes core services see |
| `credentials.py` | The authentication / configuration boundary |
| `errors.py` | One error vocabulary for every broker |
| `health.py` | Broker API health (distinct from a user's session) |
| `registry.py` | The broker list, with registration-time verification |
| `gateway.py` | The single choke point every broker call passes through |
| `streaming.py` | The canonical streaming contract — endpoint, tick, decoded event |
| `stream.py` | Realtime transport — connection management only, no wire formats |
| `crypto.py` | Token encryption at rest |

## Adding a broker — the whole checklist

1. New adapter module implementing `BrokerAdapter`.
2. Declare its `capabilities` and its `credential_spec`.
3. Register it in `services/brokers/__init__.py`.

Nothing else changes. If a step 4 appears — a branch in the Trading Engine, a new field on a route, a case in the frontend — the framework has been breached, and the breach is what gets fixed, not the symptom.

This is enforced, not merely asserted: `backend/tests/test_broker_framework.py` builds a fictional broker (`AcmeBrokerAdapter`, with its own product code and a deliberately partial capability set) from nothing but the public contract and exercises it end to end, and structural tests fail if any core module names a broker in executable code.

---

# Broker Capability Model

Every broker declares what it actually offers. The Broker Gateway refuses anything else **before** the adapter is called — a permanent, user-safe "this broker does not support this feature", never a timeout, never a 500, never a network round trip.

| Group | Capabilities |
|---|---|
| Account data | `profile` `holdings` `positions` `funds` `margins` `orders` `trades` |
| Order management | `place_order` `modify_order` `cancel_order` |
| Session lifecycle | `session_refresh` `session_invalidate` |
| Realtime | `order_stream` `tick_stream` |

**Do not assume every broker supports every capability.** BROKER_INTEGRATION.md's original interface list (below, retained as the target surface) is aspirational: Kite Connect has no refresh grant, neither broker can refresh a daily token, and brokers added later will be missing pieces neither of them is.

Declared capabilities are verified at registration. An adapter claiming `trades` without implementing `get_trades` fails at import — the cheapest possible moment — rather than returning an error to a user mid-session.

## As implemented

| Capability | Zerodha | Upstox |
|---|---|---|
| profile · holdings · positions · funds · margins · orders · trades | ✅ | ✅ |
| place_order · modify_order · cancel_order | ✅ | ✅ |
| session_invalidate | ✅ | ✅ |
| session_refresh | ❌ (daily tokens, no refresh grant) | ❌ (same) |
| order_stream | ✅ | ✅ |
| tick_stream | ✅ (same socket) | ✅ (separate v3 protobuf feed) |

The absences are the point. `session_refresh` being unset is what tells the engine to prompt a reconnect instead of attempting a refresh that cannot succeed, rather than attempting one that cannot.

## Stream channels (D4.7)

A capability says *what* a broker serves. A **channel** says *over which connection*, and the two are deliberately separate: every consumer above the transport asks "does this broker stream ticks" and gets one answer whichever topology the broker has.

| | Zerodha | Upstox |
|---|---|---|
| channels | 1 — `default` | 2 — `orders`, `market` |
| protocol(s) | `kite_ticker` | `upstox_portfolio`, `upstox_market_feed_v3` |
| ticks and orders | multiplexed on one socket | one feed each |

An adapter declares its channels through `stream_channels()`. **The default is one channel**, backed by the same `stream_endpoint` / `stream_subscribe_frames` / `stream_connect_error` / `decode_stream_frame` methods every adapter already implements — so a broker that has never heard of channels *is* a single-channel broker and needs no override. Override it only when a broker's realtime surface is genuinely more than one connection.

Each channel declares:

- `name` — unique within the broker; it is part of the stream registry key `(user, broker, channel)` and appears in diagnostics, so name it after what it carries rather than leaving it `default`;
- `protocol` — the transport dispatch key, per channel because a broker's two feeds need not speak the same wire format;
- `delivers` — which `StreamEventKind`s this channel may produce. **This is a narrowing of the broker's capabilities, never a widening.** A multi-channel broker must set it explicitly: an order channel that inherited TICKS from the adapter would claim to carry a market feed it has no prices on, and the account's market-data provider would take its link state for the tick feed's.

Verified at registration: every declared realtime capability must be carried by some channel, names must be unique, and every channel must declare a protocol. A broker that declares `tick_stream` no channel delivers would otherwise register a market-data provider, connect its sockets, and have every tick dropped by the narrowing — which from outside is indistinguishable from a market with no trades in it.

Connection lifecycle, reconnect, backoff, link-state reporting, capability enforcement and readiness all remain in `stream.py`, once, for every channel of every broker. **A channel that opened a socket, or retried one, would be a transport, and there is only one of those.**

---

# Target Broker Interface

The full surface a broker adapter may implement. An adapter implements the subset matching its declared capabilities; everything else raises `CapabilityUnsupported` and is refused at the gateway before it is reached.

get_login_url()          — required (authentication is not optional)

exchange_token()         — required

session_expiry()         — required

parse_callback_params()  — defaults to standard OAuth2; override for other dialects

refresh_session()

invalidate_session()

get_profile()

get_holdings()

get_positions()

get_funds()

get_margins()

get_orders()

get_trades()

place_order()

modify_order()

cancel_order()

stream_credentials()

stream_instruments()

normalize_stream_order()

stream_endpoint()

stream_subscribe_frames()

decode_stream_frame()

health_check()

---

# Canonical Broker Data

Every broker returns the SAME shapes. Defined and enforced in `contracts.py`; coerced at the gateway, so broker-specific keys cannot reach core services even if an adapter emits them.

| Contract | Fields |
|---|---|
| `BrokerProfile` | account_id, user_name, email, broker, exchanges, products |
| `BrokerHolding` | symbol, exchange, quantity, average_price, last_price, market_value, invested_value, pnl, pnl_percent, product, isin, instrument_token, company_name |
| `BrokerPosition` | symbol, exchange, product, quantity, average_price, last_price, pnl, realised, unrealised, buy_quantity, sell_quantity, side, instrument_token |
| `BrokerOrder` | order_id, symbol, exchange, transaction_type, order_type, product, quantity, filled_quantity, pending_quantity, price, trigger_price, average_price, status, status_message, placed_at, updated_at, tag, broker |
| `BrokerOrderAck` | order_id, status, broker |
| `BrokerTrade` | trade_id, order_id, symbol, exchange, transaction_type, quantity, price, product, executed_at |
| `BrokerFunds` | available_margin, used_margin, opening_balance, payin, payout, collateral, total_balance |
| `BrokerConnection` | user_id, broker, display_name, configured, connected, session_expired, account_id, connected_at, expires_at, last_sync, streaming, capabilities, mode |

Rules:

• **Coercion is lenient, validation is narrow.** A missing optional field becomes its zero value and a mistyped number is coerced; only a genuinely unusable record is rejected. An order with no `order_id` can never be modified, cancelled or reconciled, so it is refused rather than written into the order book — but a single unexpected null must never blank a user's whole portfolio screen.

• **Unnamed fields are dropped.** Kite returned its whole `equity`/`commodity` margin tree under a `raw` key; nothing read it, and any consumer that had started to would have been reading a shape only one broker produces. If a field in there turns out to be needed, it becomes a canonical field every adapter fills.

• **`instrument_token` is the account's mapping table, not a leak — and not an export.** It is the broker's opaque instrument identifier, carried beside `symbol` and `exchange` on the same row, which is precisely what lets `InstrumentMap` name an arriving tick with no extra fetch. It is matched, never parsed, and since D4.3 it stops at the broker boundary: no core service reads it and it appears on no canonical tick.

• **`BrokerOrderAck` is separate from `BrokerOrder` on purpose.** `place_order` persists `{**request, **ack}`; a full-order acknowledgement would overwrite the request's real quantity, price and symbol with its default zeros.

---

# Streaming Contract (Sprint D4.2, ADR-032)

A broker's WebSocket is described entirely by its adapter. `stream.py` holds connection management and nothing else — no endpoint, no framing, no broker name. Adding a WebSocket broker changes no shared code at all.

| Contract | Fields |
|---|---|
| `BrokerStreamEndpoint` | url, headers, subprotocols, ping_interval, ping_timeout, heartbeat_frame, heartbeat_interval (D4.9) |
| `BrokerTick` | instrument_token, last_price, symbol, exchange, volume, timestamp |
| `BrokerStreamEvent` | kind (`ticks` / `order` / `auth_expired` / `not_entitled` / `error` / `ignore`), ticks, order, message |

Four adapter methods carry the whole wire format:

| Method | Answers |
|---|---|
| `stream_endpoint(session, credentials)` | Where to connect, and how to authenticate — query string, bearer header, negotiated subprotocol |
| `stream_subscribe_frames(instruments)` | What to send on connect, in the broker's own encoding, verbatim |
| `decode_stream_frame(frame)` | What one raw frame *means*, as a `BrokerStreamEvent` |
| `stream_connect_error(error)` | Whether a failed *handshake* means this session is dead (D4.6). Default `None` — retry on the normal backoff |

Rules:

• **The codec is the only code that sees a raw frame.** Whatever `decode_stream_frame` returns is the most broker-shaped thing anything above the adapter will ever hold, and the transport type-checks it — an adapter returning its own dict produces nothing and logs an error rather than leaking a payload upward.

• **A capability gates every decoded event.** `TICKS` requires `tick_stream` and `ORDER` requires `order_stream`, enforced by the gateway. The registry additionally refuses at startup any broker that declares a realtime capability without a codec, or a `stream_protocol` with no realtime capability to use it — a broker that declares a stream it cannot decode holds a live socket that looks, in every log line, exactly like a quiet market.

• **Never log a stream URL.** Use `BrokerStreamEndpoint.safe_url`, which strips the query. Kite authenticates its ticker by query string, so the raw URL carries a live access token, and SECURITY.md forbids credentials in logs.

• **A frame is a batch.** One unusable tick is dropped; the rest of its frame is delivered. A frame that yields nothing usable decodes to `ignore`, so "nothing to deliver" has one shape.

• **A codec must not raise on a frame it does not understand.** Heartbeats, keep-alives and unconsumed update types are the normal case, and returning `ignore` for them is what keeps a working connection out of the log.

• **An application-level keep-alive is not the protocol's ping.** `ping_interval` configures the WebSocket protocol's own ping frames, which the two libraries exchange without either application seeing them. Some feeds do not count those and require a keep-alive **in the data channel** — Angel One closes a socket that stops sending the text frame `ping` every 30 seconds. A broker that needs one declares `heartbeat_frame` and `heartbeat_interval` on its endpoint (both, or neither: the contract refuses half of one); the transport sends it on a timer started after the subscribe frames and cancelled with the connection. **An adapter must not run its own timer** — it would own a task whose lifetime has to match a connection it does not hold, and a task leaked per reconnect is forever on a flapping feed. The failure this prevents is not loud: the socket connects, subscribes, delivers ticks for half a minute and is closed, over and over, which reads as a flapping feed rather than a missing frame.


• **A codec may need a scope, and the scope is one connection.** `BrokerStreamChannel.open(session, credentials)` returns the channel's view of the connection about to be opened; the transport uses it for that connection's `subscribe_frames` and `decode`, and drops it when the socket ends. **The default returns `self`**, so a broker whose frames are independently decodable and whose credential travels in the handshake never notices this exists — three of the four brokers do not. Two things need it, and Fyers needs both: a feed whose steady-state frames are *deltas* against an earlier snapshot (the decode state is invalidated by a reconnect and differs per user, so it cannot live on a channel singleton shared by every user of the broker), and a feed that authenticates with a **frame on the data channel** rather than in the handshake (so the first thing it sends is a per-session credential `subscribe_frames()` had no argument to reach). The failure the scope prevents is not loud in either case: a shared table means one account's reconnect renumbers another account's instruments and a price is filed under the wrong company's name, with nothing raised. `endpoint`, `connect_error` and `delivers` are deliberately **not** routed through it — they are properties of the channel, not of a connection.
• **A dead session may be refused before any frame exists.** `decode_stream_frame` can only classify a failure the broker reports *in a frame*, which means a connection that was established. Some brokers reject a stale token during the WebSocket handshake instead — Kite answers HTTP 403 — so no frame is ever decoded and the transport sees only "connect raised". `stream_connect_error` is where an adapter says what its broker's rejection meant; the transport then raises its own auth-expiry signal and the existing expiry path runs unchanged. **Adapters classify, they do not act** — a broker that implements failover of its own is reintroducing the branch this framework removed.

---

# Zerodha Kite — the first concrete stream adapter (Sprint D4.6, ADR-036)

Zerodha is the platform's first real streaming broker. **It is not the market-data architecture.** Everything above this section was built and proved against a broker that does not exist (`NovaAdapter` in the tests); D4.6 only puts a real wire format through it. No module outside `services/brokers/zerodha.py` learned that Kite exists, and `test_kite_added_no_kite_knowledge_outside_its_own_adapter` sweeps `services/` to keep that true.

**Protocol, as implemented:**

| Aspect | Kite Connect v3 | Where it lives |
|---|---|---|
| Endpoint | `wss://ws.kite.trade?api_key=…&access_token=…` | `stream_endpoint` |
| Auth | Query string (which is why `safe_url` exists) | `stream_endpoint` |
| Subscribe | `{"a":"subscribe","v":[tokens]}` then `{"a":"mode","v":["ltp",[tokens]]}` | `stream_subscribe_frames` |
| Instrument id | Unsigned 32-bit token; its **low byte is the exchange segment** | `instrument_token`, `price_divisor` |
| Binary frame | `[uint16 packet count]` then `[uint16 length][packet]…` | `parse_kite_binary` |
| Packet | LTP is 8 bytes: token + price. Quote (44) and full (184) open with the same 8 | `parse_kite_binary` |
| Price scale | Paise (÷100), except `cds` (÷10⁷) and `bcd` (÷10⁴) | `price_divisor` |
| Heartbeat | A 1-byte binary frame | `decode_stream_frame` |
| Order updates | JSON text `{"type":"order"}` on the same socket | `decode_stream_frame` |
| Errors | JSON text `{"type":"error"}`; a token error is auth expiry | `decode_stream_frame` |
| Dead token at connect | **HTTP 403 during the handshake — no frame at all** | `stream_connect_error` |

**Mode is LTP, deliberately.** The tick feed marks holdings and open trades and answers streamed quotes; all three need a last price and nothing else. The decision lives in one named constant (`STREAM_MODE`) rather than a literal. **Consequence: a Kite-derived `MarketTick` carries no volume**, because an LTP packet has none. The decoder reads only the first eight bytes of each packet, which every tradable mode fills identically, so widening the mode later is a subscribe-frame change rather than a decoder rewrite.

**Limitations, recorded rather than worked around:** no volume on a Kite tick; only holdings-and-positions instruments are streamed (a full Kite instrument dump is a catalog with its own storage and refresh semantics, and is a sprint of its own); no wire-level unsubscribe, because a portfolio sync restarts the stream and nothing else changes a subscription incrementally. **Kite's 3,000-instrument-per-connection cap is sharded as of D5.10** — a larger subscription opens as many ticker connections as it needs (ADR-050); no concurrent-connection ceiling is declared, because this repository documents none for Kite and D5.10 does not invent numbers (LIM-D5.10-1).

**Live validation has not been performed.** A Kite ticker connection needs a per-user `access_token`, obtainable only through an interactive browser login. Everything asserted about this adapter is deterministic validation against fixtures built from the Kite Connect v3 binary specification.

---

# Angel One SmartAPI — the third concrete stream adapter (Sprint D4.9, ADR-038)

Angel One is the **independent test of the channel model** D4.7 introduced. Upstox forced channels into existence and was the only broker using them; Angel One's realtime surface is one socket, so it takes the *free* single-channel path and declares nothing about channels at all. No module outside `services/brokers/angelone.py` learned that SmartAPI exists, and `test_angelone_added_no_angelone_knowledge_outside_its_own_adapter` sweeps `services/` to keep that true.

**Protocol, as implemented:**

| Aspect | SmartAPI WebSocket 2.0 | Where it lives |
|---|---|---|
| Endpoint | `wss://smartapisocket.angelone.in/smart-stream` | `stream_endpoint` |
| Auth | **Four headers**: `Authorization` (session JWT), `x-api-key`, `x-client-code`, `x-feed-token`. Nothing credential-bearing in the URL | `stream_endpoint` |
| Login | Publisher login (browser redirect returning `auth_token` + `feed_token`); **not** `loginByPassword`, which would need the user's PIN and TOTP | `get_login_url`, `parse_callback_params`, `exchange_token` |
| Subscribe | One JSON **text** frame, `{"correlationID","action":1,"params":{"mode","tokenList":[{"exchangeType","tokens"}]}}` — grouped by exchange segment | `stream_subscribe_frames` |
| Instrument id | Numeric token **unique only within an exchange segment**; the adapter's handle is `"<segment>|<token>"` on both sides of the boundary | `instrument_id`, `parse_instrument_id` |
| Binary frame | **One tick per frame**, little-endian, fixed offsets | `decode_tick` |
| Packet | LTP is 51 bytes: mode(1) + segment(1) + token(25, NUL-terminated) + sequence(8) + exchange ts(8) + price(8). Quote (123) and Snap Quote (379) open with the same 51 | `decode_tick` |
| Price scale | Paise (÷100), except currencies (÷10⁷). The scale is keyed on the **segment field**, not on the token — Kite's rule read here would consult a byte that means nothing | `segment_scale` |
| Keep-alive | **Client sends the text frame `ping` every 30s**; the server answers `pong` | `heartbeat_frame` on the endpoint, sent by `stream.py` |
| Errors | JSON text `{"correlationID","errorCode","errorMessage"}`; a rejected subscription leaves existing ones streaming, so it is reported, not fatal | `decode_stream_frame` |
| Dead session at connect | **HTTP 401 during the handshake** (403 for a withdrawn authorisation), with an `x-error-message` header | `stream_connect_error` |
| Session lifetime | Until **midnight IST** | `session_expiry` |

**Mode is LTP (1), deliberately** — Quote is 123 bytes and Snap Quote 379 for fields no consumer reads, and Depth is a 20-level book on its own quota. **Consequence: an Angel-One-derived `MarketTick` carries no volume.** The decoder reads only the first 51 bytes, which the three priceable modes fill identically, so widening the mode later is a subscribe-frame change rather than a decoder rewrite. Depth packets are refused rather than read: they reuse the header and replace everything after it, so decoding one at the price offset would publish a *quantity* as a rupee value.

**A token alone is not an identity.** SmartAPI numbers instruments per exchange segment, so NSE 2885 and BSE 2885 are different instruments. Storing the bare token — Kite's shape — would let a BSE tick resolve to an NSE holding and mark a position at another instrument's price, with nothing raised. The segment-qualified string is built by one function used in both directions, so the value written onto a synced row and the value a decoded tick carries cannot drift apart.

**Trading-symbol series suffixes are stripped at the boundary** (`TATASTEEL-EQ` → `TATASTEEL`). Left alone, one stock held at two brokers would be two canonical symbols — a split portfolio, and a feed whose coverage never matches the platform's instrument universe.

**Capabilities are deliberately partial:** profile, holdings, positions, funds, margins, session invalidation and `tick_stream`. No order capabilities (D4.9 is a market-data sprint and SmartAPI's order surface is unvalidated here), no `order_stream` (SmartAPI serves order updates on a *separate* socket, which would be a second channel), and no `session_refresh` (SmartAPI's renewal endpoint consumes a refresh token the publisher-login redirect is not documented to return — declaring it would make the engine attempt a renewal that cannot succeed instead of asking the user to reconnect). The capability model is what makes a partial broker *declared* partial rather than integrated with stub methods that lie.

**The engine's session-secret list grew one generic name.** Angel One's feed authenticates with a second per-session credential, so `feed_token` joined `TOKEN_FIELDS` — encrypted at rest, cleared on disconnect, alongside `access_token`, `refresh_token` and `public_token`. It is a generic session-credential name, not a per-broker registry entry: an adapter's `exchange_token` decides which of them its broker issues.

**Limitations, recorded rather than worked around:** no volume on an Angel One tick; no order/trading surface; no `session_refresh`; only holdings-and-positions instruments are streamed; **the 1,000-token quota is still enforced by trimming with a warning, and that is D5.10's finding for this broker rather than an omission (LIM-D5.10-2)**: the quota is documented per *session*, counted across the client code, so sharding cannot raise it — a second socket would spend one of SmartAPI's three permitted connections to subscribe to nothing. This adapter therefore declares no per-connection limit (ADR-050); no wire-level unsubscribe; series-suffix stripping covers the documented NSE/BSE cash series only.

**Live validation has not been performed.** An Angel One feed needs a per-user session obtainable only through an interactive SmartAPI browser login. Everything asserted about this adapter is deterministic validation against fixtures built from SmartAPI's published byte layout, plus 20 source mutations observed red. The outstanding smoke test is listed in TASK.md's D4.9 section — including **holding the connection past 60 seconds**, which is the only way to prove the keep-alive, and **confirming whether the redirect carries a `refresh_token`**, which decides `session_refresh`.

---

# Fyers API v3 — the fourth concrete stream adapter (Sprint D4.10, ADR-039)

Fyers is the first broker that disagreed with the **framework** rather than only with its predecessors. Kite, Upstox and Angel One each authenticate in the handshake and each send frames that can be decoded on their own; Fyers' HSM feed does neither. It is also the first adapter whose protocol was transcribed from the broker's own *reference client* (`fyers-apiv3` 3.1.16, `FyersWebsocket/data_ws.py` and its bundled `map.json`) rather than from a published byte table. No module outside `services/brokers/fyers.py` learned that Fyers exists, and `test_fyers_added_no_fyers_knowledge_outside_its_own_adapter` sweeps `services/` to keep that true.

**Protocol, as implemented:**

| Aspect | Fyers HSM v1-5 | Where it lives |
|---|---|---|
| Endpoint | `wss://socket.fyers.in/hsm/v1-5/prod` | `FyersMarketFeedChannel.endpoint` |
| Auth | **A binary frame on the data channel** (ReqType 1) carrying the `hsm_key` claim decoded out of the session JWT. Nothing in the URL, nothing in a header — `safe_url` has nothing to strip | `FyersFeedConnection.subscribe_frames`, `hsm_key` |
| Login | Standard OAuth2 authorization-code; the redirect carries `?s=ok&code=200&auth_code=…` — **`code` is an HTTP-style status, not the grant** | `get_login_url`, `parse_callback_params` |
| Token exchange | `POST /validate-authcode` with `appIdHash = SHA256("<app id>:<secret id>")`; the secret itself is never sent | `exchange_token`, `_app_id_hash` |
| Opening frames | **Three, in order**: credential → mode → subscription. All `bytes` | `FyersFeedConnection.subscribe_frames` |
| Subscribe | ReqType 4: `<uint16 count>` then each topic as `<uint8 len><ascii>`, batched 1,500 per frame | `subscribe_frame` |
| Instrument id | The HSM topic `"sf|<segment>|<exchange token>"`, derived from the `fyToken` already on every synced row (`fyToken[:4]` → segment, `fyToken[10:]` → token). The same string is subscribed and returned | `instrument_id`, `parse_instrument_id` |
| Data frame | A batch of **mixed** record kinds: snapshot (83), full update (85), lite update (76) | `FyersFeedConnection._data_frame` |
| Decodability | **A snapshot names the topic and carries the price scale; every later record is a delta keyed by a server-minted topic id.** An update is not decodable on its own | `BrokerStreamChannel.open()` → `FyersFeedConnection` |
| Price scale | **Carried on the wire, per instrument**: `raw / (10**precision * multiplier)`, both read from the snapshot. Fyers has no divisor | `_price`, `_snapshot_record` |
| Exchange | Derived from the segment, and it is the **exchange**, not the segment: `nse_cm`/`nse_fo`/`cde_fo` are all `NSE`, because a Fyers symbol is `EXCHANGE:NAME` and the exchange half is only ever NSE, BSE or MCX | `SEGMENT_EXCHANGES`, `split_symbol` |
| Keep-alive | **Client sends the binary frame `00 01 0B` every 10s** — the reference client's own interval | `heartbeat_frame` on the endpoint, sent by `stream.py` |
| Dead session | Reported **in a frame on an open socket**: the credential response's status byte is not `"K"`. A refused handshake (401/403) is classified too, for an edge in front of the socket | `_auth_frame_result`, `connect_error` |
| Session lifetime | The token's own `exp` claim; **midnight IST** as the fallback | `token_expiry`, `session_expiry` |

**Mode is LITE (76), deliberately** — a lite update is seven bytes, where full mode (70) puts the whole 21-field record on the wire on every price change for fields no consumer reads. **Consequence: a Fyers-derived `MarketTick` carries no volume.** Fyers *does* publish a genuine cumulative day volume, and it is even present in the snapshot — which is exactly why this needs saying: carrying it once and freezing it for the rest of the session is a number that stops meaning what its name says.

**The price scale is not a broker constant.** Kite reads its scale from the low byte of its instrument token, Angel One keys a table on its segment field, Upstox needs none because it sends a `double`. Fyers carries `multiplier` and `precision` in the snapshot, per instrument — and the trap is sharpest precisely because a copied ÷100 *works* for NSE cash (`precision=2, multiplier=1`). It fails on a currency contract (`precision=4`), which means it fails on a real position rather than on the first tick anybody tests with.

**Depth topics are refused rather than priced.** Field zero of a scrip or index record is the last traded price; field zero of a **depth** record is the best bid. Publishing one as the traded price is wrong in a way nothing downstream can detect, because it is a real, plausible, tradeable-looking number.

**A frame is walked, never sampled.** One data frame carries a count and then that many records of mixed kinds. Every record is walked whether or not the adapter can price it — skipping one by guessing its length desynchronises every record after it, and the symptom is not an exception but prices decoded out of the middle of other records. Reads are bounds-checked (`_Reader`), so a truncated frame costs only the records after the damage; the reference client slices bare and decodes garbage past the end.

**Instrument identity needs no catalogue — and this is the broker where that is least obvious.** Fyers' own SDK resolves symbols to feed tokens with an authenticated HTTP call to `data/symbol-token` plus a bundled segment map. This adapter needs neither: a `fyToken` is already on every synced holding and position row, and the HSM topic is derived from it locally. That is the difference between D4.10 being an adapter sprint and being a data-pipeline sprint.

**Capabilities are deliberately partial:** profile, holdings, positions, funds, margins, session invalidation and `tick_stream`. No order capabilities (D4.10 is a market-data sprint and Fyers' order surface is unvalidated here), no `order_stream` (Fyers serves order updates on a separate socket, which would be a second channel), and no `session_refresh` — **Fyers issues a refresh token but redeeming it requires the user's trading PIN**, which SECURITY.md forbids this platform from holding.

**Limitations, recorded rather than worked around:** no volume on a Fyers tick; no order/trading surface; no `session_refresh`; only holdings-and-positions instruments are streamed; **the 5,000-instrument connection limit is sharded as of D5.10** (ADR-050) — HSM's per-connection ceiling is raised by opening another connection; `SUBSCRIBE_BATCH_SIZE` (1,500 topics per subscribe frame) is wire framing on one socket and is deliberately not that number; no concurrent-connection ceiling is declared (LIM-D5.10-1); no wire-level unsubscribe, `change_mode`, or channel pause/resume; series-suffix stripping covers the documented NSE cash series plus `INDEX` — **BSE single-letter group codes (`-A`, `-B`, `-X`) are deliberately not stripped**, because a one-letter suffix is indistinguishable from part of a name and stripping one wrongly renames an instrument permanently. **And one protocol requirement is knowingly unimplemented:** HSM's credential response carries an "acknowledge every N frames" count that the reference client honours by sending a ReqType-3 frame; a codec here returns a decoded event and cannot put a frame back on the wire. If the server enforces it the feed goes quiet with the socket still open — bounded by `StreamingTickProvider`'s tick-freshness backstop, so the account falls back to the delayed baseline within two minutes — and the adapter logs a named warning when the count arrives non-zero.

**Live validation has not been performed.** A Fyers feed needs a per-user session obtainable only through an interactive browser login. Everything asserted about this adapter is deterministic validation against fixtures built from the reference client's own framing, plus 22 source mutations observed red. The outstanding smoke test is listed in TASK.md's D4.10 section — including **whether the acknowledgement count is ever non-zero**, which is the one open protocol question, and **holding the connection past 30 seconds**, which is the only way to prove the keep-alive.

---

# Dhan (DhanHQ v2) — the fifth concrete stream adapter (Sprint D4.11, ADR-040)

**Dhan is the first broker that required no generic framework change at all.** D4.7 needed channels, D4.9 needed a keep-alive frame, D4.10 needed a connection scope; D4.11 needed one adapter module and one registry line. `stream.py`, `streaming.py`, `instruments.py`, `market_feed.py` and `ticks.py` are byte-for-byte unchanged by this sprint. No module outside `services/brokers/dhan.py` learned that Dhan exists, and `test_dhan_added_no_dhan_knowledge_outside_its_own_adapter` sweeps `services/` for thirteen Dhan vocabulary terms to keep that true.

The protocol was read from **two independent sources set against each other**: DhanHQ's published v2 documentation and Dhan's own reference client `DhanHQ-py` (`src/dhanhq/marketfeed.py`), whose `struct` format strings are the authority on byte layout. Every binary test fixture is packed with the reference client's format strings rather than the adapter's constants — an adapter tested against fixtures built from its own offsets proves only that it is self-consistent.

| Aspect | DhanHQ v2 live market feed | Where it lives |
|---|---|---|
| Endpoint | `wss://api-feed.dhan.co` | `DhanAdapter.stream_endpoint` |
| Auth | **Query string** — `?version=2&token=…&clientId=…&authType=2`. Kite's style, so the URL carries a live token and only `safe_url` may ever be logged | `stream_endpoint`, `BrokerStreamEndpoint.safe_url` |
| Subscribe frame | **JSON text on a socket that answers in binary** — the only broker here that mixes directions. `{"RequestCode": 17, "InstrumentCount": n, "InstrumentList": [...]}` | `stream_subscribe_frames` |
| Batching | **100 instruments per message**, a *message* limit rather than a session limit — so a 250-instrument account is three frames and nothing is dropped for it | `MAX_INSTRUMENTS_PER_FRAME` |
| Mode | **Quote (17)** — the narrowest mode that leaves nothing canonical unfilled | `REQUEST_SUBSCRIBE_QUOTE` |
| Response header | 8 bytes, `<BHBI`: response code, message length, exchange segment enum, security id | `HEADER_FORMAT` |
| Priceable packets | **A table keyed on the RESPONSE CODE** — Ticker (2), Quote (4), Full (8). Not a size check; see below | `PRICEABLE` |
| Price | **IEEE `float32` in rupees at offset 8 — NO DIVISOR AT ALL** | `decode_frame` |
| Volume | **Cumulative day volume, `int32` at offset 22.** The first of five brokers to fill `MarketTick.volume` | `PRICEABLE` |
| Instrument identity | `"<SEGMENT NAME>\|<security id>"` — e.g. `"NSE_EQ\|1333"` | `instrument_id`, `parse_instrument_id` |
| Exchange naming | Segment is **not** the exchange: `NSE_EQ`→`NSE`, `NSE_FNO`→`NFO`, `NSE_CURRENCY`→`CDS`, `BSE_CURRENCY`→`BCD` | `SEGMENTS` |
| Keep-alive | **None declared.** The server sends a WebSocket *protocol* ping every 10s and the library pongs it | `stream_endpoint` |
| Dead session | **A frame on an open socket** (response code 50, reason 806/807/808/809). Handshake 401/403 classified too, as a second line | `decode_disconnect`, `stream_connect_error` |
| Session lifetime | The consume-consent response's `expiryTime` (**ISO, IST, no offset**); **24 hours from login** as the fallback | `_expiry_time`, `session_expiry` |
| Catalogue needed | **No** — every synced holding and position row already carries the segment-qualified id | `stream_instruments` |

**The price is used exactly as it arrives, and this is the line most likely to be "corrected" into a bug.** Kite reads its scale from the low byte of its instrument token, Angel One keys a table on its segment, Fyers carries `multiplier` and `precision` in the snapshot, Upstox sends a `double`. Dhan sends a `float32` of the rupee price and there is *no rule at all*. Applying any predecessor's divisor publishes every price at one hundredth of its true value, and nothing raises anywhere.

**Prev Close is byte-for-byte shaped like Ticker, and Dhan sends one per instrument at subscribe time.** Response code 6 and response code 2 are both 16 bytes, `<BHBIfI`, with a `float32` at offset 8. A codec that priced "any frame with a float at offset 8" would publish **yesterday's close as today's price, once per holding, immediately after every connect and every reconnect** — marking a whole portfolio at stale prices with nothing raised. The response code is the only thing that separates them, which is why `PRICEABLE` is keyed on it and why an unlisted code is never priced.

**Four volume-shaped fields on one packet, and only one is the volume.** `LTQ` (this trade's size, offset 12), `volume` (the day's cumulative traded quantity, offset 22), `total_sell_quantity` (26) and `total_buy_quantity` (30). Only the second is what `MarketTick.volume` means; the other three are a trade size and two order-book aggregates.

**Identity is the segment NAME, where Angel One's is the segment NUMBER — same principle, opposite encoding.** A Dhan security id is unique only within its segment (NSE 1333 and BSE 1333 are different companies), so the pair is the identity. The name form is used because Dhan's subscribe frame takes the name verbatim and `/positions` already returns `exchangeSegment: "NSE_EQ"`; copying Angel One's numeric encoding would have meant translating on every subscribe.

**`/holdings` reports `exchange`, not `exchangeSegment` — and the docs and the SDK disagree about what it holds.** The published sample shows `"ALL"`; the SDK's own fixture shows `"NSE"`. Both are handled. A row naming a real exchange is a delivery holding and can only be cash, so the segment follows. A row saying `"ALL"` names no exchange, and **defaulting it to `NSE_EQ` was rejected**: right most of the time and wrong *silently*, subscribing a BSE-only holding as whatever NSE numbers that id and publishing another company's price under the user's stock's name. Such a row keeps its symbol — it is a real holding everywhere else — carries no instrument id, and the count is WARNed rather than swallowed.

**The partner consent flow, not the app flow the reference SDK uses.** `/app/generate-consent` requires the user's `dhanClientId` **before they log in**, which a multi-tenant platform by definition does not have — learning who the user is at Dhan is the point of the login. `/partner/*` takes no client id and returns one on consume. The partner secret is a **request header** and appears in no URL, which matters because the consent login URL is shown to the user. Login is therefore genuinely two steps: `generate_consent()` mints a short-lived `consentId` and returns the browser URL; `get_login_url()` reports `requires_consent` rather than inventing a URL that would not work.

**No keep-alive is declared, and that is a finding rather than an omission.** Dhan's server pings *us* every 10 seconds and closes a connection unanswered for 40; a WebSocket protocol ping is answered by the library in both peers without either application seeing it. Angel One is the exact contrast — it does not count protocol pings at all and closes a socket that stops sending the text frame `ping`. The `ping_interval` / `ping_timeout` defaults are left in place because they are what Dhan's own reference client runs with.

**Capabilities are deliberately partial:** profile, holdings, positions, funds and `tick_stream`. No order capabilities (D4.11 is a market-data sprint and Dhan's order surface is unvalidated here), no `order_stream` (Dhan serves order updates on a separate socket, which would be a second channel), **no `margins`** (Dhan's margin surface is a *calculator* pricing a hypothetical order, not a report of used and available margin), no `session_refresh` (the renewal endpoint's behaviour on a partner-issued token is unverified) and no `session_invalidate` (Dhan publishes no logout for the partner flow).

**Session-expiry and entitlement classification.** Codes 807/808/809 stop the stream through the `AUTH_EXPIRED` path — the account's token is dead. Code **806** ("Data APIs not subscribed") takes the `NOT_ENTITLED` path added in **D5.5 (ADR-045)**: the token is valid and only the market-data entitlement is missing, so the account's *feed* stops while its session, its other channels and its trading surface keep working. Until D5.5 it was approximated as an expired session — an honest message on a dishonest state — and that approximation is now closed. Code **805** ("too many active connections") is deliberately neither: Dhan drops the oldest socket when a sixth opens, so the next attempt may succeed, and it is reported as `ERROR` and left to the reconnect ladder.

**Limitations, recorded rather than worked around:** a holding reporting `"ALL"` cannot be streamed (its symbol, quantity and P&L are unaffected); no order/trading surface; no `margins`, `session_refresh` or `session_invalidate`; only holdings-and-positions instruments are streamed; **the 5,000-instrument connection ceiling is sharded as of D5.10** (ADR-050), capped at Dhan's documented **five concurrent feed connections per user** — declared because Dhan does not refuse a sixth connection but disconnects the *oldest* with code 805, so an uncapped plan would destroy the connection it opened first; instruments beyond five connections' capacity are still trimmed with a warning naming the number; no wire-level unsubscribe; and **no series-suffix stripping** — both official samples and the SDK fixtures show bare symbols, and inventing a strip rule for a suffix this broker does not appear to send would risk renaming an instrument permanently.

**One broker-neutral debt was found and named rather than fixed: DB-5.** The transport resets its reconnect backoff after any connection that *completed*, so a socket a broker accepts and immediately closes reconnects roughly every 1.5s indefinitely — which Dhan's code 805 is simply the first protocol to expose, against a broker whose own documentation warns that further requests may get the user blocked. The fix is to reset the backoff only after a connection that lasted a minimum duration, which **is flap suppression**, which is D5's. ✅ **CLOSED in D5.1 (2026-08-25, ADR-041)** — see *Reconnect pacing* below.

## Reconnect pacing — what an adapter can rely on, and what it must not do (D5.1)

• **A connection that lasts 30 seconds resets the reconnect ladder; one that dies sooner does not.** `services/brokers/reliability.py` owns this and nothing else does. A broker that closes a socket promptly — rate limiting, maintenance, a duplicate session, an unsupported subscription, an unlicensed data feed — backs off 2 → 4 → 8 → 16 → 32 → 60 seconds and stays at the ceiling, instead of reconnecting every ~1.5 seconds forever. A feed that streamed all session and dropped still reconnects within the base delay, exactly as it did before D5.1.

• **"Established" means the socket is open *and* the subscribe frames are away** — the transport's existing link-up signal. A broker that accepts a connection and hangs up before anything was asked of it is classified as never established rather than as a flap. It backs off identically; the distinction exists so that a later slice can tell "the broker will not talk to us" from "the broker keeps hanging up on us".

• **An adapter must not implement its own reconnect, its own backoff, or its own flap detection.** Same rule, and the same reason, as the keep-alive above: pacing is a property of a connection the adapter does not own, and a second ladder somewhere else is a ladder that disagrees with this one. An adapter's whole contribution to reliability is classifying what its broker *said* — `decode_stream_frame` returning `AUTH_EXPIRED`, `NOT_ENTITLED` or `ERROR`, and `stream_connect_error` classifying a handshake rejection. The transport decides what to do about it.

• **Each connection has its own ladder.** One per (user, broker, channel), so two users on one broker never pace each other and a broker's order socket flapping never slows its market feed.

• **Reconnect pacing is not the same gate as provider promotion (D5.2/D5.3).** Both use a 30-second window and they measure different things: this ladder measures how long the *socket* lasted, while `StreamingTickProvider`'s probation window measures whether *data kept arriving* on it. A link that stays open silently for a minute resets the ladder and is still on probation as a market-data provider. An adapter needs to do nothing for either.

## Entitlement failure — the third lifecycle outcome (D5.5)

• **`NOT_ENTITLED` means "this account may not consume what this feed carries".** It is a statement about a *capability*, not about a login. The token stays valid, so REST portfolio, funds, order placement and the order stream go on working; what stops is this one feed.

• **What the transport does with it, for every broker identically:** stop **this channel** — not the account's other channels and not its session — and **do not reconnect**. Retrying cannot make an unlicensed account licensed; it can only produce the churn the reconnect ladder paces and never stops, against a broker that has just said to stop. Coming back requires a deliberate lifecycle event (the user reconnecting, a session restore), never the loop's own schedule.

• **What the engine does with it:** the account's market feed is *unregistered*, so the baseline serves the very next resolution. Unregistered rather than demoted on purpose — there is then no state (READY, STABLE, primary) in which a feed that has lost its entitlement can remain selected. Nothing else is touched: not the session, not the broker's other channels, not any other broker, not any other user, and not the guest/baseline floor.

• **What the user is told, and in whose words (D5.13, ADR-053).** The unregistration publishes a user-scoped `provider.status` carrying `change_reason: "entitlement_refused"` — the Market Engine's `FeedChangeReason` vocabulary, which describes the *feed* and is closed against anything else. Until D5.13 the user saw their tier drop from `streaming` to `delayed` with no explanation at all (LIM-D5.5-2); they now see why.

  **And since D5.14 (ADR-054) they actually see it.** `MarketFeedStatus` renders the explanation from that event: *"Your market-data connection needs attention — your account is not cleared for this data."* The frontend maps the three `FeedChangeReason` values through an allow-list in `src/lib/feedState.js`; a value outside the three renders **nothing at all** rather than raw text, so an adapter that ever leaked a wire code into this field would produce silence on the user's screen, not a broker error string. The three sentences name no broker.

  **An adapter contributes nothing to this and must not try.** The `reason` string an adapter passes to `BrokerStreamEvent.not_entitled(...)` is the broker's own words and goes to the audit row and the admin diagnostics — never to the consumer payload. A wire code, an error string or the broker's name on that surface is a Developer Rule 4 breach, and a field a consumer can only render for the brokers somebody has read the error tables of is not a consumer field. The three reasons exist because there are three *platform* paths that unregister a live feed (`entitlement_refused`, `session_expired`, `feed_disconnected`), not because there are three things a broker can say.

• **How an adapter says it.** `decode_stream_frame` returns `BrokerStreamEvent.not_entitled(reason)` for a refusal the broker sends **in a frame**; `stream_connect_error` may return the same event for one the broker sends at the **handshake** (a 403 that means "not licensed" rather than "token rejected"). Returning a reason *string* from `stream_connect_error` still means session expiry, so no adapter written before D5.5 changed.

• **NEVER infer it.** A socket that opens is not entitlement. A subscribe frame the broker accepted is not entitlement. A timeout is not entitlement. Silence is not entitlement. A malformed frame is not entitlement. Only an explicit refusal may produce it — an inferred one permanently stops a feed that may be working perfectly, and nothing in the system will ever contradict it.

• **`AUTH_EXPIRED` and `NOT_ENTITLED` must not be collapsed.** They share the property of being terminal and nothing else: one ends the account's session, the other ends one feed. An adapter that cannot tell them apart from what its broker sent should return `ERROR` and let the ladder run, rather than guess.

## Provider recovery — how a withdrawn feed comes back (D5.6, ADR-046)

D5.5 made an entitlement refusal terminal, which is correct, and left it a one-way door. D5.6 gives it a way back that is neither a retry nor a reconnect. **Nothing here is an adapter's business** — the whole of it is generic, and this section exists so an adapter author knows what *not* to build.

• **An adapter must not implement recovery, re-probing, entitlement polling or a "try again later" of any kind.** Same rule, and the same reason, as the reconnect and keep-alive rules above. An adapter's whole contribution is classifying what its broker *said*; when and whether to try again is a property of a connection and an account the adapter does not own. `services/brokers/recovery.py` names no broker and must keep naming none.

• **There is no `check_entitlement()` on the adapter contract, and there will not be one.** A re-probe is **one ordinary attach** of the withdrawn channel through the existing lifecycle, so an adapter needs no new method and no new capability. A control-plane "yes" would prove the wrong thing anyway: the platform's only definition of a usable feed is a valid canonical tick on the current link.

• **Five recovery classes, and only one of them is retried.** `TRANSPORT` (the reconnect ladder owns it) and `EVIDENCE` (the next accepted tick owns it) are refused registration outright, because both already heal themselves. `REPROBE` — what a `NOT_ENTITLED` refusal produces — is retried on a paced ladder. `SESSION` — what an `AUTH_EXPIRED` produces — is **never** retried: a new valid session through the ordinary re-authentication lifecycle is the only way back, and an automatic probe with a dead credential is a login attempt on a timer. `CONFIGURATION` covers a channel or protocol the deployment no longer serves.

• **The re-probe ladder is not the reconnect ladder.** 300 seconds, doubling, capped at 3600. Reconnect asks whether a socket is reachable and answers in seconds; a re-probe asks whether an account's entitlement changed, and entitlements change when a person changes them. The two must never be shared, and a test asserts the slowest reconnect is still faster than the fastest re-probe.

• **Only reconnecting or disconnecting the broker resets the ladder.** An attach that appears to succeed does not — otherwise a broker that accepts a socket and then refuses in a frame would reset the pacing on every cycle, which is DB-5's storm on a five-minute period.

• **A candidate is cleared by market data, not by a socket.** A connection that opened proves nothing about entitlement; a frame carrying market data proves it exactly.

• **A recovered feed earns everything again.** New provider instance, so READY must be re-earned from a valid canonical tick, the probation window must be served in full, and no readiness, stability or latency evidence carries over from the refused connection. Recovery creates the opportunity to earn eligibility; it never creates eligibility.

• **Scope is (user, broker, channel)**, the same key the stream registry uses. A market-feed re-probe opens only the market-feed channel — it does not blip the account's order socket — and an order-channel re-probe can never replace a live market feed.

**Live validation has not been performed for D5.6.** See ADR-046 for the outstanding smoke test.

**Provider *health* recovery is also not an adapter's business (D5.7/ADR-047, re-verified D5.12/ADR-052).** A separate mechanism from the one above, and an adapter contributes nothing to it either. When a market-data provider fails enough consecutive calls to be excluded as `down`, the Market Engine — not the broker layer — re-admits it for one trial once a failure cool-down has run, at the tail of the failover chain and with its health untouched until a real call succeeds. It is paced on its own ladder (60s doubling to 240s, deliberately faster than the re-probe ladder above and sharing no constant with it), claimed atomically so that only one worker spends a trial, and charged by evidence rather than by the offer. **`services/brokers/` contains none of it and must keep containing none of it**: `services/market_engine/providers/health_recovery.py` names no broker, has no identity branch, and the Market Engine cannot import the broker layer at all — all three are swept by tests. For a streaming feed the question rarely arises, because a pushed feed records a success whenever a tick batch is accepted, whether or not it was selected. **A new broker writes no recovery code of any kind.**

**Live validation has not been performed.** A Dhan feed needs a per-user access token obtainable only through an interactive browser consent login. Everything asserted about this adapter is deterministic validation against fixtures packed with the reference client's own `struct` formats, plus 27 source mutations of which 25 were observed red. The outstanding smoke test is listed in TASK.md's D4.11 section — including **whether a real `/holdings` row returns `"ALL"` or a real exchange**, which decides how much of the holdings limitation actually bites, and **holding the connection past 40 seconds**, which is the only way to prove the library's pong satisfies Dhan's ping.

---

# Canonical Instrument Identity (Sprint D4.3)

D4.2 stopped a broker's *wire format* at the adapter. D4.3 stops its *instrument identity* one layer above, in `BrokerEngine._on_stream_tick`:

```
broker wire frame → codec (adapter) → BrokerStreamEvent → BrokerTick
                                                              ↓  InstrumentMap
                                                          MarketTick → portfolio_stream / trade_stream / app WebSocket
```

| Contract | Where | Fields |
|---|---|---|
| `MarketInstrument` | `services/market_engine/ticks.py` | symbol (canonical, uppercase), exchange |
| `MarketTick` | `services/market_engine/ticks.py` | symbol, price, exchange, volume, ingested_at |
| `InstrumentMap` | `services/brokers/instruments.py` | broker identifier → `MarketInstrument`, per account |

Rules:

• **A broker instrument identifier may not cross into a core service.** `portfolio_stream` and `trade_stream` used to join `instrument_token` against `db.holdings` themselves. That coupled two core services to one broker's identifier format and gave a symbol-identified broker no join key at all — its users' live P&L stopped updating with nothing raised, logged or failed. Pinned by `test_no_broker_instrument_identifier_reaches_a_core_service` and a source sweep.

• **Both identification styles resolve through the same boundary.** A token broker's tick is looked up in the account's map; a symbol broker's tick is canonicalized directly and qualified with the account's exchange when it has one. Adding either kind of broker changes no core service.

• **An unmapped token is dropped, never used as a symbol.** A fallback would push a broker's numeric handle into `db.holdings`, the trade snapshot and the AI's context as if it were an instrument name.

• **The map is built from rows the platform already syncs.** A canonical holding/position carries `instrument_token`, `symbol` and `exchange` together, so mapping costs no broker call. It is cached per account and rebuilt by `sync_portfolio` and `start_stream`, dropped on disconnect — no TTL, because holdings only change through a sync.

• **A tick that cannot be represented canonically is dropped, not raised.** Same batch discipline as the codec: one unusable record must not cost the other 299 their prices, nor drop a live socket. A batch that resolves to nothing wakes no consumer.

• **Canonical ticks carry no broker name, no provider identity and no broker timestamp.** `ingested_at` (UTC, ours) replaces `BrokerTick.timestamp`, which is a verbatim broker string precisely because brokers disagree on format and timezone.

---

# Broker Error Model

Every exception raised beneath the gateway leaves it as a `BrokerError` with a code, a retry flag, a recovery hint and a message written for a person.

| Code | Meaning | Retryable | Recovery |
|---|---|---|---|
| `BROKER_AUTH` | Session missing / expired | no | reconnect_broker |
| `BROKER_REJECTED` | Broker understood and refused | no | review_order |
| `RATE_LIMIT` | Broker rate limit reached | yes | wait_and_retry |
| `BROKER_TIMEOUT` | No answer in time | yes | retry |
| `BROKER_NETWORK` | Could not reach the broker | yes | retry |
| `BROKER_UNSUPPORTED` | Capability not offered by this broker | no | use_supported_broker |
| `BROKER_NOT_CONFIGURED` | Deployment has no credentials | no | contact_support |
| `BROKER_UNKNOWN` | No such broker registered | no | choose_supported_broker |
| `BROKER_INVALID_REQUEST` | Bad request before it reached the broker | no | correct_request |
| `BROKER_CONTRACT` | Payload the canonical contract cannot represent | no | contact_support |
| `BROKER_ERROR` | Anything else | no | retry |

Only `user_message` may be rendered to a user; it never contains a stack trace, a URL, a token or a broker's internal error type.

---

# Broker Health

Two different questions, deliberately not conflated:

| Question | Answered by |
|---|---|
| Is this broker's API up, for everyone? | `BrokerHealth` — `unknown` → `up` → `degraded` → `down`, counter-based, thresholds matching the market-provider model |
| Is this user's session alive right now? | `BrokerConnection` (state) and `health_check()` (a live authenticated call) |

**An auth failure never counts against broker health.** Kite invalidates every access token daily at ~06:00 IST, so at 06:01 every connected user's next call raises `BrokerAuthError`. Counting those would drive Zerodha to `down` every single morning while its API was perfectly available, and a dashboard that cries outage daily is a dashboard nobody reads. Auth failures are counted separately, where a *rising* rate is a genuine signal.

A rejected order, an unsupported capability, a contract breach and an invalid request are likewise excluded: they are evidence about the request, not about the broker.

---

# Authentication and Configuration Boundary

Adapters **declare** which environment variables carry their credentials (`BrokerCredentialSpec`) and never read them. Everything that needs a credential asks the adapter.

This is what lets `BrokerEngine` open a broker's WebSocket without naming a single secret — before D3 it read `KITE_API_KEY` directly, which meant it could not open a stream for a broker it was not written to know about. It also means secrets are read through exactly one function, which is where a future move to a managed secret store (SECRETS.md) plugs in.

Values are read at call time and never cached, so credential rotation does not require a process restart.

| Broker | Variables | Required |
|---|---|---|
| Zerodha | `KITE_API_KEY`, `KITE_API_SECRET`, `KITE_REDIRECT_URL` | key + secret |
| Upstox | `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_REDIRECT_URL` | key + secret + redirect (Upstox will not issue a token without one) |
| Angel One | `ANGELONE_API_KEY`, `ANGELONE_REDIRECT_URL` | key + redirect. **No secret**: SmartAPI's publisher login returns the session tokens on the redirect, so there is no server-side exchange to sign — declaring a secret this broker does not use would report a correctly configured deployment as unconfigured. |
| Fyers | `FYERS_APP_ID`, `FYERS_SECRET_ID`, `FYERS_REDIRECT_URL` | app id + secret. The secret signs the `appIdHash` on the token exchange and is never transmitted itself. |

`is_configured()` is one implementation derived from the declared spec, rather than three lines re-written per adapter.

Preferred

OAuth

Fallback

Broker Approved Login

Never store:

Passwords

PIN

OTP

Security Questions

Only store encrypted access tokens and refresh tokens where supported.

---

# OAuth Callback Parsing

Each adapter parses its own redirect shape via `parse_callback_params()`. The default implements the standard OAuth2 authorization-code shape (`?code=` / `?error=`); Zerodha overrides it because Kite answers with `?request_token=&status=`.

The public callback route calls the adapter. It used to branch `if broker == "zerodha": … else:  # upstox`, where the `else` silently assumed every future broker speaks Upstox's dialect.

---

# Connection Flow

User

↓

Settings

↓

Broker Accounts

↓

Choose Broker

↓

Redirect to Broker

↓

User Authenticates

↓

Broker Returns Authorization

↓

Backend Exchanges Code

↓

Access Token

↓

Encrypted Storage

↓

Portfolio Sync

↓

Success

---

# Token Management

Store

Encrypted Access Token

Encrypted Refresh Token

Expiry Time

Broker ID

User ID

Last Refresh

Automatically refresh tokens when supported.

If refresh fails:

Prompt user to reconnect.

---

# Portfolio Synchronization

Synchronization includes:

Holdings

Positions

Orders

Trades

Funds

Margins

PnL

Broker Profile

Portfolio sync should run:

On login

Manual refresh

Scheduled refresh

Broker event

---

# Order Lifecycle

User Clicks Buy

↓

Trading Engine

↓

Risk Validation

↓

Broker Adapter

↓

Broker API

↓

Order Accepted

↓

Broker Response

↓

Database Update

↓

Portfolio Update

↓

Notification

↓

AI Monitoring Begins

---

# Supported Order Types

Market

Limit

Stop Loss

Stop Loss Market

Bracket (if broker supports)

Cover Order (if broker supports)

AMO

GTT (Future)

---

# Product Types

CNC

MIS

NRML

Broker-specific types should be mapped internally.

---

# Order Status

Created

Pending

Open

Partially Filled

Filled

Cancelled

Rejected

Expired

Every status change emits an event.

---

# Trade Synchronization

Sync

Executed Trades

Average Price

Charges

Broker Fees

Taxes

Execution Time

Order ID

Trade ID

---

# Funds & Margins

Retrieve

Available Balance

Used Margin

Available Margin

Collateral

Buying Power

Display live.

---

# Real-Time Streaming

Preferred

Broker WebSocket

Fallback

Polling

Stream

Price

Orders

Positions

Holdings

Margins

Trade Executions

---

# Market Data Upgrade

Connecting a broker does more than enable trading.

The moment a broker connection becomes active, the Source Manager automatically switches the user's market data source from Yahoo Finance to the broker's streaming WebSocket.

broker.connected

↓

Source Manager re-resolves the user's best provider

↓

Market Gateway opens the broker WebSocket (make-before-break)

↓

User's entire experience upgrades to live streaming:
prices, portfolio, orders, P&L, watchlist, scanner, AI context

The user does NOT need a StockAssist subscription for this. The broker already owns the user's market data entitlement — StockAssist simply consumes the feed on behalf of the authenticated user.

On broker disconnect, the Source Manager falls back to Yahoo Finance automatically. The frontend never notices the *switch* — no spinner, no remount, no toast, and no provider name anywhere.

**What it does notice, since D5.14 (ADR-054), is the resulting feed state**, and only in the platform's own vocabulary: the tier indicator moves from `Live` to `Delayed`, and if the user *removed* the broker account, `change_reason: "feed_disconnected"` renders as *"Your market-data account is no longer connected, so streaming data has stopped."* Which broker, which transport and which error remain invisible to the frontend — the projection copies only the contract's fields, so a broker name on the payload is not rendered, it is not even stored.

**How the switch is actually gated (D4.5).** The upgrade is deliberately *not* triggered by `broker.connected`, and not by the WebSocket opening either. The account's stream registers as a market-data provider (D4.4) and stays behind a readiness gate until a valid canonical tick has arrived on that link while it is subscribed. Only then is it eligible for the quote capability at all, and only for the instruments it actually streams. Yahoo is never disconnected — it moves to standby inside the same failover chain — so there is no instant at which the user has no provider.

**And readiness alone no longer wins the switch (D5.2).** A newly ready feed is on **probation**: it must keep delivering valid canonical data for 30 seconds on that link before it outranks a provider that is already serving steadily. So the full sequence is *connect → subscribe → first valid tick (READY) → 30 seconds of valid ticks (STABLE) → primary*, and a feed that flaps never gets past the third step. A dropped link discards the window; the reconnected feed serves a fresh one from its own first tick. Probation only decides who is *preferred*: a probationary feed still serves immediately if no steadier provider remains, so this can delay a tier upgrade and can never cause an outage. An adapter contributes nothing to any of this and cannot influence it — see ADR-042.

**And stability is not permanent (D5.3).** A feed that stops delivering loses the primary position even while its socket stays open. The full lifecycle is therefore *connect → subscribe → first valid tick (READY) → 30 seconds of valid ticks (STABLE) → primary → **no valid tick for 120 seconds → back to probation, baseline resumes***, with the tier returning to streaming on the next tick without re-serving the 30-second window (the link never dropped, so nothing was discarded). The 120 seconds is the same per-symbol coverage window a feed has always been held to; D5.3 asks it of the *feed* as well, because until then the tier-reporting path had no freshness term at all and a silently dead feed went on reporting the streaming tier indefinitely.

Two consequences for an adapter author, and both are "do nothing":

  * **A quiet market does not need a keep-alive tick.** Do not synthesize ticks to hold the tier. A fabricated price is a data-rules violation, and the honest outcome — falling back to the delayed baseline — is the one the platform wants.
  * **Transport flap history is deliberately not consulted here.** `consecutive_short_connections` paces reconnects (D5.1) and is invisible to provider stability. A feed that has flapped repeatedly and is now delivering is stable; a feed whose link has never flapped and is delivering nothing is not. Market-data evidence and transport liveness stay separate facts — see ADR-043.

The demotion path is the mirror image and is push-driven: `BrokerStream` reports its own transport connect/disconnect, the account's provider records it, and the next resolution ranks the baseline first again. Nothing polls, and no failure counter has to escalate first. An *ended entitlement* — disconnect, revoked token, expired session — is a different event with a different response: the provider is unregistered outright rather than merely demoted.

All of this lives on the generic `MarketDataProvider` / `StreamingTickProvider` contract and names no broker. Adding a streaming broker adds one adapter and nothing else.

Full design, priority algorithm, and failover rules: MARKET_DATA_ARCHITECTURE.md (authoritative). Decision records: ADR-035 (the switch), ADR-042 (probation), ADR-043 (stale-feed demotion).

---

# Broker Events

Published by the Broker Engine onto the Event Bus.

broker.connected      — `{user_id, broker, capabilities}`

broker.disconnected   — `{user_id, broker}`

portfolio.synced

holding.updated

order.created

order.updated

order.executed

order.cancelled

trade.completed

funds.updated

margin.updated

Every event enters the Event Bus.

**`broker.connected` / `broker.disconnected` carry the broker's capabilities, not just its name.** A consumer can then decide what a connection makes possible without importing a broker module — the Source Manager reads `tick_stream` to know whether a connection could ever become a streaming market feed (MARKET_DATA_ARCHITECTURE.md, Source Manager responsibility 1).

Both topics were documented here from version 1.0 and published by nothing until D3, which is why that Source Manager responsibility had been unimplementable.

---

# AI Integration

After synchronization the following AI agents are notified:

Portfolio Manager

Trade Monitor

Risk Manager

Market Analyst

Notification Agent

Morning Report Agent

This ensures AI always works with the latest broker data.

---

# Error Handling

Examples

Authentication Failed

Token Expired

Market Closed

Insufficient Funds

Invalid Quantity

Rejected Order

Broker Timeout

API Limit Reached

Network Failure

Every error should have:

User Message

Developer Log

Retry Strategy

Recovery Suggestion

---

# Retry Policy

Temporary Failure

↓

Retry

↓

Retry Again

↓

Queue

↓

Notify User

↓

Admin Alert

Never lose orders silently.

---

# Security

HTTPS Only

Encrypted Tokens

Role Validation

Audit Logs

Rate Limiting

Input Validation

Request Signing (where supported)

No credentials in logs

Sensitive values encrypted at rest.

---

# Audit Logging

Log

Broker Connected

Broker Disconnected

Portfolio Sync

Order Placement

Order Modification

Order Cancellation

Trade Execution

Token Refresh

Authentication Failure

---

# Rate Limiting

Respect broker API limits.

Implement

Queue

Throttle

Retry

Exponential Backoff

Circuit Breaker (future)

Never flood broker APIs.

---

# Health Monitoring

Monitor

API Availability

Latency

**What "latency" means for a broker's tick stream, and what it does not (D5.4, ADR-044).** A broker's *market feed* is scored on **delivery latency** — the median gap between accepted canonical tick batches, measured on the platform's own monotonic clock — and never on the broker's own timestamp. That is not a preference: three of the five adapters put no exchange timestamp on the wire in the mode this platform subscribes (Zerodha's LTP packet is token + price and nothing else; Fyers lite carries none; the Upstox LTPC decoder extracts price only), and the two that do disagree about units (Angel One epoch milliseconds, Dhan epoch seconds) on an exchange clock this platform has never synchronised against. `BrokerTick.timestamp` therefore stays what it has always been — a verbatim, unparsed string — and no adapter was changed for D5.4.

The scoring itself lives entirely in the Market Engine and reads nothing an adapter provides beyond the fact that a batch of canonical ticks was accepted. **A new broker gets latency scoring by declaring `TICK_STREAM` and pushing canonical ticks through the existing seam; it writes no latency code, exposes no timestamp, and needs no entry in any table.** A broker whose feed is genuinely slow will rank behind a faster one *of the same user* and is never excluded, never marked unhealthy, and never surfaced to the user by name.

**D5.9 (ADR-049) adds a p95 and puts both figures on `health()`, and still requires nothing of an adapter.** The p95 is the same accepted-batch interval series read over a wider window (the last 20 rather than the newest 9, by nearest rank); the median — and therefore ranking — is unchanged. A broker feed's `health()` now carries `established / p50_seconds / p95_seconds / samples`, derived on read, with unknown as `None`. It is per-socket and per-user, is never shared to Redis (ADR-048: a pushed feed's health is not shared), is discarded on reconnect with the rest of the link's evidence, and names no broker. **A new broker still writes no latency code and exposes no timestamp.**

Broker *API* latency — the round-trip time of an authenticated REST call, which is what the Admin Portal rows below mean — is a different measurement on a different subsystem and is still unimplemented. `BrokerHealth` remains counter-based (availability, auth-failure rate, error rate); neither D5.4 nor D5.9 touched it.

**Broker health is now the deployment's, not one worker's (D5.8, ADR-048).** A broker's API is one remote system, so every worker's calls to it are evidence about the same outage — and holding one counter per worker meant an Admin Portal row that reported whichever replica served the request, and a broker that needed eight consecutive failures *per worker* before any of them said `down`. Since D5.8 the counters behind `BrokerHealth` are a shared Redis record with atomic transitions, mirrored onto each worker's adapter instance.

What this means for a broker adapter: **nothing.** No adapter was changed, no method was added to the contract, and no adapter may reach the store. `BrokerGateway.call` records the outcome exactly where it always did; the difference is where the number lives. The two rules that governed the counters still govern them unchanged:

  * an **auth failure is still not a health failure** — it is counted separately and stays out of the state machine, and sharing the count is what finally makes a token-expiry wave legible (a climbing auth count against a flat error count) instead of splitting it across replicas;
  * the thresholds are still 3 and 8, and they still match the market-provider model.

Two reads exist and the difference matters at the surface: `broker_gateway.health(broker)` is the synchronous, this-worker view for logs and metric callbacks, and **`broker_gateway.health_shared(broker)` / `diagnostics_shared()` are what an operator surface must call** — they adopt the shared record before rendering, so two refreshes cannot disagree. Where Redis is not configured (the supported single-process deployment) both are the same answer and behave exactly as they did before D5.8.

Per-user session state is untouched and remains per user on `BrokerConnection`; nothing about a user's tokens, sessions or identity is written to the shared store, whose keys carry a broker name and nothing else.

Authentication Success

Order Success Rate

Sync Success Rate

WebSocket Health

Token Expiry

Display in Admin Portal.

---

# Admin Monitoring

Display

Connected Brokers

Active Sessions

Orders Today

Portfolio Syncs

Failed Syncs

API Errors

Latency

Daily Requests

Quota Usage

---

# Broker Permissions

Before enabling trading verify:

Broker Connected

Market Open

Valid Session

Funds Available

Risk Check Passed

User Authorized

---

# Compliance

The platform must always:

Respect broker terms of service

Respect API rate limits

Never bypass authentication

Never impersonate users

Require explicit user consent before placing live orders

Clearly distinguish between AI recommendations and user-authorized executions

---

# Future Broker Features

Multi-Broker Portfolio

Broker Comparison

Smart Order Routing

Cross-Broker Analytics

Broker Performance Dashboard

Unified Holdings

Unified P&L

Broker Migration

Institutional Brokers

International Brokers

---

# Broker Integration Checklist

Before production verify:

✓ Capabilities declared and verified at registration

✓ Registered in the Broker Registry

✓ Credentials declared, never read directly

✓ Responses normalize into the canonical contracts

✓ Errors normalize into the canonical codes

✓ OAuth callback parsing (default OAuth2, or an override)

✓ OAuth Authentication

✓ Secure Token Storage

✓ Portfolio Sync

✓ Holdings Sync

✓ Orders Sync

✓ Trade Sync

✓ WebSocket Connection

✓ Retry Logic

✓ Error Handling

✓ Audit Logging

✓ Security Review

✓ Performance Testing

✓ Documentation

---

# Long-Term Vision

The Broker Engine should become a unified brokerage layer.

The rest of StockAssist AI should never depend on a specific broker implementation.

Adding a new broker should require only creating a new adapter while keeping the Trading Engine, Portfolio Engine, AI System, and UI unchanged.

---

# End of Broker Integration Documentation


---

# Instrument Sharding Across Broker Connections (D5.10, ADR-050)

Every streaming broker caps how many instruments one connection may carry. Until D5.10 an over-cap subscription was trimmed to a deterministic prefix with a warning, leaving the account's feed quietly narrower than its portfolio. It is now **sharded**: the subscription is split into as many broker-valid batches as it needs, one connection per batch, and the account still registers **exactly one market-data provider**.

## What a channel declares

Two class attributes on `BrokerStreamChannel` (or, for a single-channel broker, on the adapter as `stream_max_instruments_per_connection` / `stream_max_connections`). Both default to `None`.

| Attribute | Meaning | `None` means |
|---|---|---|
| `max_instruments_per_connection` | How many instruments **one connection** may hold | *No shardable limit known* → exactly one connection. **Never "unlimited".** |
| `max_connections` | How many such connections **one account** may hold | No documented ceiling → the plan is uncapped |

**Declare a per-connection limit only.** Three different limits appear in these adapters and only one of them is raised by opening another socket:

* **per connection** — shardable. Another connection genuinely doubles capacity.
* **per session / per client code** — a quota counted across the account, not the socket. Sharding it opens a socket the same quota refuses. Declare `None` and keep trimming.
* **per frame** — how many instruments fit in one subscribe *message* on one socket. Wire framing; the codec already handles it by sending more frames.

## The declared limits, per broker

| Broker | Per connection | Concurrent ceiling | Notes |
|---|---|---|---|
| Zerodha (Kite ticker) | 3,000 | not documented here | Sharded as of D5.10 |
| Upstox (v3 market feed) | 5,000 (`ltpc` keys) | not documented here | Declared on the *market* channel; the order channel has no instrument subscription |
| Angel One (SmartAPI) | **none — quota, not a ceiling** | 3 sockets per client code | 1,000 tokens **per session**; still trimmed with a warning (LIM-D5.10-2) |
| Fyers (HSM) | 5,000 | not documented here | 1,500 topics per subscribe *frame* is framing, not sharding |
| Dhan (DhanHQ v2) | 5,000 | **5 per user** | Dhan disconnects the **oldest** connection past the ceiling, so the cap is load-bearing; 100 instruments per *message* is framing |

## What sharding does not change

Instrument identity, the codec, the canonical `MarketTick`, `InstrumentMap` resolution, the Market Gateway, the Source Manager, the provider registry, the fallback chain, readiness, probation, freshness, entitlement classification, the reconnect ladder, the re-probe register, and the distributed health boundary. A tick from shard 2 resolves to exactly the same canonical instrument as if it had arrived on shard 1.

## Live smoke test (NOT YET PERFORMED)

Requires an interactive session on an account whose holdings-and-positions universe genuinely exceeds one connection's limit — easiest on Zerodha (3,001+ instruments) or with a temporarily lowered `stream_max_instruments_per_connection`.

1. **Subscription larger than one connection.** Connect the account, sync the portfolio, and confirm the planner logs `N instruments sharded across M connections (limit L per connection)` with `M == ceil(N / L)`.
2. **Multiple live connections.** `stream_manager.status()` lists `M` rows for the tick channel, distinct `shard` values, all `running: True`; the broker's own session page shows `M` active feed connections.
3. **Ticks through more than one shard.** With DEBUG logging, confirm `… stream connected for user …` appears `M` times and that instruments from at least two different shards appear in the canonical batches reaching the provider.
4. **Canonical ticks correct.** Spot-check three instruments from three different shards against the broker's own web terminal: symbol, exchange and last price must match, and no instrument may be missing from `covered_symbols`.
5. **One shard can fail without destroying the others.** Kill one connection at the network level (drop the socket, not the session). Confirm: the other connections stay `running`; `describe()["covered_symbols"]` loses only that shard's instruments; the account's quotes for the surviving instruments still resolve to the feed **once the baseline is unavailable** (see LIM-D5.10-3 — while the baseline is up it is preferred, because the feed is on probation).
6. **Reconnect restores the shard.** Confirm the killed connection reconnects on D5.1's ladder, re-subscribes its own batch, and its instruments return to `covered_symbols`.
7. **Readiness and probation after reconnect.** Confirm `describe()["stability"]` is `probation` immediately after the reconnect and returns to `stable` only after a full window of valid data on **every** connection — not on the reconnected one alone.
8. **No duplicate or missing instruments.** The union of every shard's `subscribed_instruments` must equal the account's `stream_instruments(...)` output exactly, with no repeats.

Until this is run, **LIVE VALIDATION: NOT PERFORMED** stands for sharding, as it does for every stream adapter (ADR-036…040, ADR-050).


---

## The Instrument Catalogue capability (D5.15, ADR-055)

### `BrokerCapability.INSTRUMENT_CATALOGUE`

```python
async def resolve_instruments(
    self, symbols: Sequence[str], session: dict = None
) -> Dict[str, Any]:
    """canonical symbol -> this broker's own instrument identifier."""
```

Bound to `resolve_instruments` in `CAPABILITY_METHODS`, so the registry rejects an adapter that declares it without implementing it — the same guarantee every other capability gets.

**Why it is a capability and not a required method.** Resolving a symbol needs an instrument master, a search endpoint or a static table, and not every broker publishes one. A broker without it is not broken: it keeps the pre-D5.15 behaviour of covering exactly what the account holds, because holdings and positions carry their own identifiers. Declaring the capability is what says *"this broker's feed can be aimed at an instrument the account does not own."*

**Why it is separate from TICK_STREAM.** They are different facts and either can exist without the other. TICK_STREAM says a feed exists; INSTRUMENT_CATALOGUE says the feed can be pointed somewhere. Before D5.15 the platform had only the first, so an account with an empty demat opened a live socket that was structurally incapable of carrying a packet — which is what a real broker account did in production.

### What an implementation must guarantee

| Rule | Why |
|---|---|
| **Canonical in, broker-opaque out.** `symbols` are uppercase canonical symbols; the return values are whatever this broker's feed subscribes by. | The Market Engine and the broker engine name no instrument format. `services/broker_engine.py` is swept for every identifier string the five adapters use. |
| **A symbol you cannot name is OMITTED.** Never a sentinel, never `None` in the map, never a guess. | Brokers reject an over-limit or malformed subscribe request *as a whole*. One bad key costs the account every instrument, not just the one. |
| **Partial answers are correct.** | A watchlist may name an instrument this broker does not carry. That symbol falls back to the baseline for that symbol alone. |
| **Never raise for an unreachable catalogue.** Raise `BrokerError`; the gateway turns it into "no catalogue". | A catalogue widens *coverage*. It is not load-bearing for a feed that already has a portfolio, and a master-file outage must not cost the user their stream. |
| **`session` is part of the signature even when unused.** | A broker whose catalogue is an authenticated search endpoint needs it. A signature that varied per broker would put the difference back in the caller. |
| **Cache per process, not per user, when the source is public.** | An instrument master is a fact about the exchange, not about anybody's account. One download serves every account; guard it with a lock so a restart restoring N sessions does not fetch N times. |

### Per-broker status

| Broker | TICK_STREAM | INSTRUMENT_CATALOGUE | What is missing |
|---|---|---|---|
| **Upstox** | ✅ | ✅ | Nothing for NSE equity. `NSE_EQ` segment only — an F&O, BSE or index symbol resolves to nothing (LIM-D5.15-3). |
| **Zerodha** | ✅ | ✅ (D5.16) | Trading symbol → numeric Kite `instrument_token`, from `api.kite.trade/instruments`. |
| **Angel One** | ✅ | ✅ (D5.16) | Symbol → SmartAPI `"<segment>\|<token>"`, from `OpenAPIScripMaster.json`. |
| **Fyers** | ✅ | ✅ (D5.16) | Symbol → HSM topic `"sf\|<segment>\|<token>"`, from `public.fyers.in/sym_details/{NSE,BSE}_CM.csv`. |
| **Dhan** | ✅ | ✅ (D5.16) | Symbol → `"<segment>\|<securityId>"`, from `api-scrip-master.csv`. |

**LIM-D5.15-2 and LIM-D5.15-3 are CLOSED.** All five adapters declare `INSTRUMENT_CATALOGUE`, and the contract is exchange-aware — see the section below.

Every one of those four sources was confirmed reachable and unauthenticated by HTTP range request during D5.15 — **reachability only**. No schema was parsed, no mapping was written, and nothing is claimed about any of them beyond that the work is bounded and per-adapter. The seam, the universe assembly, the instrument map and the engine wiring are already shared and broker-neutral, so each remaining broker is one method (LIM-D5.15-2).

**The seam is argued, not proven.** One implementation cannot demonstrate a broker-neutral abstraction. Zerodha is the sharpest next test of it, because a numeric token is the identifier format least like the compound string the first implementation returns.


---

## The equity instrument catalogue (D5.16)

### What an adapter must implement

Two members, and the split between them is the point:

```python
@staticmethod
def build_catalogue_index(*row_groups) -> Dict[Tuple[str, str], Any]:
    """{(EXCHANGE, SYMBOL): broker identifier} from instrument-master rows."""

async def _download_catalogue(self) -> Dict[Tuple[str, str], Any]:
    """Fetch the master(s) and hand the rows to build_catalogue_index."""
```

`build_catalogue_index` is **pure**, and that is deliberate: it is the whole of what "this broker has a catalogue" *means*, and it is the whole of what a hermetic test can honestly cover. The download is I/O whose correctness depends on a file at a third party, and no fixture can assert anything true about that. `resolve_instruments` and the per-process cache are inherited — an adapter writes neither.

```python
_catalogue_cache = CatalogueCache(INSTRUMENT_MASTER_TTL_SECONDS)

async def _instrument_catalogue(self):
    return await type(self)._catalogue_cache.get(self._download_catalogue)

async def resolve_instruments(self, instruments, session=None):
    if not instruments:
        return {}
    return resolve_from_index(instruments, await self._instrument_catalogue())
```

### Rules

| Rule | Why |
|---|---|
| **Key on `(EXCHANGE, SYMBOL)`, never on the symbol alone.** | `RELIANCE` is two instruments with two identifiers at all five brokers. Verified 2026-08-31. |
| **Never take the session.** | Every one of the five masters is a public, unauthenticated asset. A catalogue that took a session would become a per-account call and a per-account download. |
| **Offer rows; do not resolve them.** | The winner is not knowable until every candidate for a key has been seen, and the masters disagree about ordering. `CashEquityCatalogue.offer()` then `.build()`. |
| **Pass `series=` when the master has one; `rank=0` when it does not.** | The shared policy ranks the series. A master with no series column (Kite) offers everything equally, which makes a duplicate key resolve to *dropped* — the correct answer for a master that cannot tell two rows apart. |
| **Build the identifier with the adapter's own `instrument_id`/`instrument_token`.** | The value the catalogue stores and the value the wire carries must be one expression. Two derivations are two things that can drift, and the symptom is a subscription that ticks into an unnameable void. |
| **Return `None` for an identifier you could not build.** | Three of the five brokers reject a malformed *subscription* rather than the offending entry, so one bad row would cost the account every price it asked for. |
| **Raise `BrokerError` with `type(exc).__name__`, never `exc`.** | An httpx error stringifies to the request it failed on, URL and all. `BrokerError` renders its developer message into logs. This is how D3's token-in-log-URL leak happened. |
| **Always supply `user_message`.** | `BrokerError.__init__` does `user_message or message`. An adapter that omits it does not get an empty user message — it gets the *developer* one, which is the string carrying the vendor detail. |

### The masters, as published (verified 2026-08-31)

| Broker | URL | Format | Equity discriminator | Series field | Keys built |
|---|---|---|---|---|---|
| Zerodha | `api.kite.trade/instruments` | CSV + header | `instrument_type == "EQ"` and `segment == exchange` | *none* | 22,993 |
| Upstox | `assets.upstox.com/.../{NSE,BSE}.json.gz` | gzip JSON ×2 | `segment in {NSE_EQ, BSE_EQ}` | `instrument_type` | 8,536 |
| Angel One | `margincalculator.angelbroking.com/.../OpenAPIScripMaster.json` | JSON array | `instrumenttype == ""` | NSE: symbol suffix; BSE: *none* | 16,406 |
| Fyers | `public.fyers.in/sym_details/{NSE,BSE}_CM.csv` | CSV, **headerless** | fyToken segment prefix | ticker suffix | 8,536 |
| Dhan | `images.dhan.co/api-data/api-scrip-master.csv` | CSV + header | `SEM_SEGMENT == "E"` and `SEM_INSTRUMENT_NAME == "EQUITY"` | `SEM_SERIES` | 8,557 |

Fyers' files are headerless, so its column positions are constants (`MASTER_FYTOKEN_COLUMN = 0`, `MASTER_TICKER_COLUMN = 9`, `MASTER_NAME_COLUMN = 13`). That is a genuine fragility, and the mitigation is that every value read through them is then put through the adapter's own validators rather than trusted — a column shift at Fyers produces an *empty* catalogue and a logged degradation, not a catalogue of wrong identifiers.

Fyers keys on the master's underlying-name column rather than on the ticker, because `trading_symbol` only strips suffixes it recognises as NSE cash series: canonicalising `BSE:RELIANCE-A` would yield `RELIANCE-A` and no BSE symbol would ever match a watchlist entry. The ticker is still read — for its series, which is what separates `NSE:CHOLAFIN-EQ` from `NSE:CHOLAFIN-D1` and keeps `BSE:ENERGY-INDEX` out entirely.

---

## The index catalogue (D5.17)

D5.16's table above is the **equity** half. D5.17 adds the index half, from the
same downloads: no adapter fetches a new file, and Fyers' index rows are inside
the very NSE_CM.csv / BSE_CM.csv it already reads.

Each adapter contributes only its broker's own **discriminator** and identifier
format. The canonical spellings live once, in
`services/brokers/catalogue.py::INDEX_ALIASES`, and a sweep
(`tests/test_d517_boundaries.py`) fails if an adapter carries a copy.

| Broker | Index discriminator | Identity column read | Identifier format |
|---|---|---|---|
| Zerodha | `segment == "INDICES"` (note: `instrument_type` is `"EQ"`, same as a share) | `tradingsymbol` | `int` instrument token |
| Upstox | `segment in {NSE_INDEX, BSE_INDEX}` | `trading_symbol` | `NSE_INDEX\|Nifty 50` |
| Angel One | `instrumenttype == "AMXIDX"` | `name` (not `symbol`) | `"<segment>\|<token>"` |
| Fyers | ticker suffix `-INDEX` | column 13, the underlying name | `if\|<segment>\|<token>` |
| Dhan | `SEM_SEGMENT == "I"` | `SEM_TRADING_SYMBOL` | `IDX_I\|<securityId>` |

### The two traps, per broker

**Zerodha.** An index row carries `instrument_type: "EQ"` — identical to an
ordinary share. Only the segment tells them apart, which is why the equity
branch tests `segment == exchange` and not the type.

**Angel One.** SmartAPI's `symbol` for the Nifty is `"Nifty 50"` while its
`name` is `"NIFTY"`. Both spellings are in the shared table, so either column
would resolve; `name` is read because it is the field the master means as the
identity, mirroring how the equity branch reads `symbol` for its series suffix.

**Dhan.** The master says `"I"`; the *subscription* says `"IDX_I"`, and it says
it for a BSE index as well as an NSE one — Dhan's index segment is not
per-exchange, unlike `NSE_EQ`/`BSE_EQ`. Routing a BSE index through
`HOLDING_EXCHANGE_SEGMENTS` would produce `BSE_EQ|51`, which is a real NSE
security id belonging to an unrelated company.

**Fyers.** The one place a wrong answer is invisible. A Fyers tick is identified
by the topic string the *server* returns on the snapshot record, not by what was
subscribed, and an index is an `if` topic rather than an `sf` one. The prefix is
**not derivable from the fyToken** — `NSE:NIFTY50-INDEX` sits in the `nse_cm`
segment exactly as `NSE:SBIN-EQ` does and its token begins with the same four
characters — so `instrument_id(fy_token, kind)` takes it from the caller, which
is the only layer that read the master row. Getting it wrong is not a subscribe
error: the map has no entry for the topic that arrives, every packet is dropped,
and the symptom is an index that never ticks on a healthy socket. **Unverified
against a live HSM connection (LIM-D5.17-3.)**

### Live resolution, verified 2026-08-31 through production code paths

`resolve_instruments(index_instruments())` against each broker's real published
master — **20/20**, every index at every broker, on the right exchange:

| | NIFTY | BANKNIFTY | SENSEX | INDIAVIX |
|---|---|---|---|---|
| Zerodha | `256265` | `260105` | `265` | `264969` |
| Upstox | `NSE_INDEX\|Nifty 50` | `NSE_INDEX\|Nifty Bank` | `BSE_INDEX\|SENSEX` | `NSE_INDEX\|India VIX` |
| Angel One | `1\|99926000` | `1\|99926009` | `3\|99919000` | `1\|99926017` |
| Fyers | `if\|nse_cm\|26000` | `if\|nse_cm\|26009` | `if\|bse_cm\|1` | `if\|nse_cm\|26017` |
| Dhan | `IDX_I\|13` | `IDX_I\|25` | `IDX_I\|51` | `IDX_I\|21` |

A full universe (30 dashboard equities + 4 indices + a watchlist symbol + a
BSE-held RELIANCE) resolved **35/35 at all five brokers** in the same run.

### What no broker carries

Gold, silver, crude and USD-INR have **no spot instrument at any Indian broker**.
The masters carry dated futures — `GOLD26OCTFUT`, `CRUDEOIL26SEPFUT`,
`USDINR26SEPFUT` — a different instrument with a rolling identity, behind an
MCX/CDS segment entitlement, at a price that is not the spot number the
dashboard shows. This is a fact about the venues, not a deferral: see ADR-056.

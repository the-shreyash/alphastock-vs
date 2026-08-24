# StockAssist AI
## Broker Integration Documentation

Version: 1.2

Status: Framework Implemented (Sprint D3, 2026-08-20); streaming contract / codec boundary Implemented (Sprint D4.2, 2026-08-21, ADR-032); three concrete market feeds implemented — Zerodha (D4.6, ADR-036), Upstox (D4.7, ADR-037), Angel One (D4.9, ADR-038). **All three deterministic-validated only; live validation not performed.**

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

---

## Phase 2

Groww (if public APIs become available)

Dhan

Fyers

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
| `BrokerStreamEvent` | kind (`ticks` / `order` / `auth_expired` / `error` / `ignore`), ticks, order, message |

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

**Limitations, recorded rather than worked around:** no volume on a Kite tick; only holdings-and-positions instruments are streamed (a full Kite instrument dump is a catalog with its own storage and refresh semantics, and is a sprint of its own); no wire-level unsubscribe, because a portfolio sync restarts the stream and nothing else changes a subscription incrementally; Kite's 3,000-instrument-per-connection cap is neither enforced nor sharded (D5 owns sharding).

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

**Limitations, recorded rather than worked around:** no volume on an Angel One tick; no order/trading surface; no `session_refresh`; only holdings-and-positions instruments are streamed; the 1,000-token session quota is enforced by trimming with a warning rather than by sharding (D5 owns sharding — SmartAPI allows three sockets per client code, which is the headroom any sharding must fit inside); no wire-level unsubscribe; series-suffix stripping covers the documented NSE/BSE cash series only.

**Live validation has not been performed.** An Angel One feed needs a per-user session obtainable only through an interactive SmartAPI browser login. Everything asserted about this adapter is deterministic validation against fixtures built from SmartAPI's published byte layout, plus 20 source mutations observed red. The outstanding smoke test is listed in TASK.md's D4.9 section — including **holding the connection past 60 seconds**, which is the only way to prove the keep-alive, and **confirming whether the redirect carries a `refresh_token`**, which decides `session_refresh`.

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

On broker disconnect, the Source Manager falls back to Yahoo Finance automatically. The frontend never notices the switch.

**How the switch is actually gated (D4.5).** The upgrade is deliberately *not* triggered by `broker.connected`, and not by the WebSocket opening either. The account's stream registers as a market-data provider (D4.4) and stays behind a readiness gate until a valid canonical tick has arrived on that link while it is subscribed. Only then does it outrank the baseline, and only for the instruments it actually streams. Yahoo is never disconnected — it moves to standby inside the same failover chain — so there is no instant at which the user has no provider.

The demotion path is the mirror image and is push-driven: `BrokerStream` reports its own transport connect/disconnect, the account's provider records it, and the next resolution ranks the baseline first again. Nothing polls, and no failure counter has to escalate first. An *ended entitlement* — disconnect, revoked token, expired session — is a different event with a different response: the provider is unregistered outright rather than merely demoted.

All of this lives on the generic `MarketDataProvider` / `StreamingTickProvider` contract and names no broker. Adding a streaming broker adds one adapter and nothing else.

Full design, priority algorithm, and failover rules: MARKET_DATA_ARCHITECTURE.md (authoritative). Decision record: ADR-035.

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
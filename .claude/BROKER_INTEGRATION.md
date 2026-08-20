# StockAssist AI
## Broker Integration Documentation

Version: 1.2

Status: Framework Implemented (Sprint D3, 2026-08-20) — broker market-data streaming pending (D4)

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

Zerodha Kite Connect

Upstox API

---

## Phase 2

Angel One SmartAPI

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
| `stream.py` | Realtime transport, dispatched by protocol |
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

**Do not assume every broker supports every capability.** BROKER_INTEGRATION.md's original interface list (below, retained as the target surface) is aspirational: Kite Connect has no refresh grant, Upstox exposes no market-tick feed on its portfolio stream, and brokers added later will be missing pieces neither of them is.

Declared capabilities are verified at registration. An adapter claiming `trades` without implementing `get_trades` fails at import — the cheapest possible moment — rather than returning an error to a user mid-session.

## As implemented

| Capability | Zerodha | Upstox |
|---|---|---|
| profile · holdings · positions · funds · margins · orders · trades | ✅ | ✅ |
| place_order · modify_order · cancel_order | ✅ | ✅ |
| session_invalidate | ✅ | ✅ |
| session_refresh | ❌ (daily tokens, no refresh grant) | ❌ (same) |
| order_stream | ✅ | ✅ |
| tick_stream | ✅ | ❌ (separate protobuf feed) |

The absences are the point. `session_refresh` being unset is what tells the engine to prompt a reconnect instead of attempting a refresh that cannot succeed; `tick_stream` being unset for Upstox is what makes its stream order-only, from the same broker-agnostic code path that gives Zerodha a tick-carrying one.

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

• **`instrument_token` is canonical, not a leak.** It is the broker's opaque instrument identifier, matched (never parsed) by the tick pipeline in `portfolio_stream` and `trade_stream`.

• **`BrokerOrderAck` is separate from `BrokerOrder` on purpose.** `place_order` persists `{**request, **ack}`; a full-order acknowledgement would overwrite the request's real quantity, price and symbol with its default zeros.

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

Full design, priority algorithm, and failover rules: MARKET_DATA_ARCHITECTURE.md (authoritative).

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
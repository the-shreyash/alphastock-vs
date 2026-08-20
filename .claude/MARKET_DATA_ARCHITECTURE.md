# StockAssist AI
## Market Data Architecture

Version: 1.1

Status: Approved Design — Phase 1 Implemented (Sprint D1, 2026-08-19); Phase 2 Implemented in backend (Sprint D2, 2026-08-20, frontend tier indicator outstanding); Phase 3 re-scoped to the Broker Provider Framework and Implemented (Sprint D3, 2026-08-20, ADR-031); Phases 4–6 pending

Priority: Critical

Related Documents:

• SYSTEM_ARCHITECTURE.md
• REALTIME_SYSTEM.md
• MARKET_ENGINE.md
• BROKER_INTEGRATION.md
• AI_AGENT_SYSTEM.md

---

# Purpose

This document defines the market data architecture of StockAssist AI.

It is the single source of truth for how market data enters the platform, how it is normalized, how providers are selected, how failover works, and how every subsystem consumes it.

Any engineer implementing, extending, or debugging market data must read this document first.

The architecture described here is designed to remain valid for at least five years. New providers, new markets, and new asset classes must fit into this design without modifying it.

---

# Background

During the Real-Time Infrastructure migration (Sprints R1–R5) we established that the platform itself is not slow.

The platform already contains:

• Event Bus (`backend/services/market_engine/event_bus.py`) with cross-process Redis Pub/Sub bridging
• Market Engine (`backend/services/market_engine/`) with gateway, normalizer, validator, scanner, ranking
• Redis for caching and cross-process eventing
• Socket.IO WebSocket layer pushing events to the frontend
• AI Context Builder (`backend/services/ai_context_builder.py`) feeding normalized market context to Claude
• Event-driven frontend (`frontend/src/store/realtimeStore.js`) with GSAP-animated updates

The frontend never polls. The backend pushes. The pipeline is event-driven end to end.

The real bottleneck is the **market data provider**.

Yahoo Finance supports only request/response polling with caching. It has no streaming interface. No amount of internal optimization can make a polled provider feel like a streamed one.

Professional platforms — Zerodha Kite, Upstox Pro, TradingView — feel instant because they receive **streaming market data** over persistent WebSocket connections from the exchange or broker.

Conclusion: StockAssist AI must never depend on a single provider. It must support multiple market data providers behind one unified **Market Gateway**, and it must automatically use the best provider available to each user.

---

# Design Goals

1. **Provider independence.** The frontend, the AI, the Market Engine, the Scanner, and the Portfolio system must never know where market data originated.

2. **One normalized event model.** Every provider — polled or streamed, free or licensed — produces the same canonical events.

3. **Zero business-logic churn.** Adding, removing, or switching providers must never require changes to business logic, AI prompts, or UI components.

4. **Best available data, automatically.** Each user receives the highest-quality feed they are entitled to, selected and switched without user action.

5. **Graceful degradation.** Loss of any provider degrades data freshness, never platform availability.

6. **Per-user provider resolution.** Provider selection is a per-user decision (a user with a connected broker streams; a guest polls), not a global one.

---

# Non-Goals

• This document does not design the broker order-execution path. Order placement, modification, and cancellation are covered in BROKER_INTEGRATION.md. This document covers market **data** only.

• This document does not select specific licensed exchange vendors. It defines the adapter contract any vendor must satisfy.

• This document does not define UI visuals. It defines the events the UI consumes.

---

# High Level Architecture

```
                     ┌──────────────────────────────────────────┐
                     │            PROVIDER ADAPTERS             │
                     │                                          │
   Zerodha WS ──────▶│ BrokerAdapter(zerodha)                   │
   Upstox WS ───────▶│ BrokerAdapter(upstox)                    │
   Angel One WS ────▶│ BrokerAdapter(angelone)                  │
   Fyers WS ────────▶│ BrokerAdapter(fyers)                     │
   Dhan WS ─────────▶│ BrokerAdapter(dhan)                      │
   NSE/BSE Feed ────▶│ ExchangeFeedAdapter (future)             │
   Yahoo Finance ───▶│ YahooPollingAdapter                      │
                     └────────────────┬─────────────────────────┘
                                      │ raw provider payloads
                                      ▼
                     ┌──────────────────────────────────────────┐
                     │             MARKET GATEWAY               │
                     │                                          │
                     │  Connection Management   Authentication  │
                     │  Normalization           Validation      │
                     │  Health Monitoring       Latency Metrics │
                     │  Reconnection            Rate Limiting   │
                     └────────────────┬─────────────────────────┘
                                      │ normalized market events
                                      ▼
                     ┌──────────────────────────────────────────┐
                     │             SOURCE MANAGER               │
                     │                                          │
                     │  Broker Detection      Provider Priority │
                     │  Automatic Switching   Failover          │
                     │  Provider Status       Latency Scoring   │
                     └────────────────┬─────────────────────────┘
                                      │ single logical feed
                                      ▼
                              Market Engine
                     (cache · scanner · ranking · sectors)
                                      │
                                      ▼
                                 Event Bus
                          (in-process + Redis bridge)
                        ┌─────────────┼──────────────┐
                        ▼             ▼              ▼
                 AI Context      Socket.IO       Portfolio /
                  Builder         Server        Trading Engine
                        │             │
                        ▼             ▼
                     Claude       Frontend
                                (realtimeStore)
```

Key invariant: everything below the Source Manager consumes **one logical feed of normalized events** and cannot tell which adapter produced it.

---

# Market Data Strategy — Three User Categories

The architecture is designed around three categories of users. Provider selection is resolved **per user**, at session level.

---

## Category 1 — Guest / Free User

**Profile:** No broker connected. May or may not have an account.

**Provider:** Yahoo Finance (polling adapter).

**What they can do:**

• Learning platform
• Paper trading
• Market analysis
• Watchlists
• Charts
• AI chat
• News
• Manual portfolio tracking

**Expected latency and behavior:**

| Aspect | Expectation |
|---|---|
| Quote freshness | 15–60 seconds (poll interval + Yahoo's own delay) |
| Delivery to frontend | Instant once ingested — the internal pipeline pushes; only ingestion polls |
| Intraday candles | Delayed; suitable for analysis, not for scalping |
| Depth / order book | Not available |
| Tick-level volume | Not available (interval snapshots only) |
| Rate limits | Enforced by adapter-level throttling and Redis caching |

**Documented limitation:** Yahoo Finance is suitable for education, paper trading, market monitoring, and AI-assisted analysis. It is **not** equivalent to exchange-grade streaming and must never be presented as real-time exchange data. The UI should label this data honestly (e.g. "delayed") without ever apologizing for it or exposing provider internals.

This is a deliberately good free tier: for a learner or paper trader, 15–60 second data with instant push delivery, animations, and full AI reasoning is a strong experience.

---

## Category 2 — Connected Broker User (Most Important)

**Profile:** User has connected any supported broker — Zerodha, Upstox, Angel One, Fyers, Dhan, or any broker added later.

**Provider:** The broker's streaming WebSocket, automatically.

The moment a broker connection becomes active, the Source Manager switches that user's feed from Yahoo Finance to the broker's WebSocket. No setting, no toggle, no page refresh.

**What upgrades instantly:**

• Live market prices (tick-level)
• Live portfolio valuation
• Live order status
• Live P&L
• Live watchlist
• Live scanner (for the user's subscribed symbols)
• Live AI context (the AI reasons over streaming data)
• Trade updates (stop-loss, targets, trailing stops evaluated on ticks)
• Market events

**Entitlement principle — the cornerstone of this category:**

The user does **NOT** need a StockAssist subscription to receive live streaming data.

The broker already owns the user's market data entitlement. Every broker account includes real-time market data as part of the brokerage relationship. StockAssist AI simply consumes the broker feed **on behalf of the authenticated user**, using the user's own broker session.

This means:

• StockAssist never pays for this data
• StockAssist never resells this data
• Each user's feed is legally theirs, scoped to their session
• Broker tokens are per-user, encrypted at rest, and never shared across users (see SECURITY.md and BROKER_INTEGRATION.md)

**Why this is one of the platform's biggest advantages:**

Competitors either charge for real-time data or serve everyone delayed data. StockAssist gives every broker-connected user professional-grade streaming for free, because the entitlement already exists. Connecting a broker becomes the single highest-value action a user can take — which is also the action that enables live trading, which is where the platform's intelligence features become most valuable.

---

## Category 3 — Premium Subscriber

**Premium must NOT sell market data.**

Market data is either free (Yahoo) or already owned by the user (broker feed). Selling it would be selling something users can get elsewhere for nothing — a commodity with zero moat and real licensing risk.

**Premium sells intelligence:**

• AI Portfolio Intelligence
• Morning Report
• Advanced Scanner strategies
• AI Coach
• Strategy Builder
• Backtesting
• Trade Journal analytics
• AI Trade Review
• Risk Engine
• Tax Assistant
• Multi-Agent AI Debate
• Portfolio Optimization
• Smart Alerts
• Automation
• Custom AI Models

**Why premium focuses on AI rather than raw market data:**

1. **Data is a commodity; reasoning is not.** Anyone can display a price. Only StockAssist explains what the price movement means for *this user's* portfolio, risk profile, and open trades.

2. **No entitlement conflicts.** StockAssist never needs to become a data vendor, never needs exchange redistribution licenses for its paid tier, and never competes with brokers on their own product.

3. **Aligned incentives.** The better the free data experience, the more market context the AI has, the more valuable premium intelligence becomes. Data quality improvements make the paid product better instead of cannibalizing it.

4. **Sustainable margins.** AI features scale with compute, which StockAssist controls; market data costs scale with licensing fees, which vendors control.

---

## Future Category — Enterprise Feeds

The architecture must support future enterprise-grade providers without any change to the Market Engine or frontend:

• Licensed NSE feed (direct exchange data)
• Licensed BSE feed
• Institutional market data vendors (e.g. Refinitiv-class feeds)
• Crypto exchanges (Binance, Coinbase, …)
• Forex providers
• US markets (NYSE, NASDAQ)

Each of these is **one new adapter** implementing the Provider Adapter contract (below). Nothing else in the system changes. New asset classes may extend the normalized event model with new event types, but existing event types and consumers are untouched.

---

# The Provider Adapter Contract

Every provider — current and future — is wrapped in an adapter that implements a single contract. This is the only place provider-specific code is allowed to exist.

**Location convention:** `backend/services/market_engine/providers/<provider_name>.py`

**Implemented in** `backend/services/market_engine/providers/base.py` **as of Sprint D1.** The table below is the target contract; see the Phase 1 note under "Implementation Phasing" for what D1 built and what it deliberately deferred.

**Contract (conceptual interface — not implementation code):**

| Member | Responsibility |
|---|---|
| `name` | Stable provider identifier (`"yahoo"`, `"zerodha"`, `"upstox"`, `"nse_licensed"`, …) |
| `kind` | `"streaming"` or `"polling"` |
| `capabilities` | Declared set: quotes, ticks, depth, ohlc, news, indices, corporate_actions, market_status |
| `connect(credentials)` | Establish session (WebSocket connect / verify API access). Idempotent. |
| `disconnect()` | Tear down cleanly. Idempotent. |
| `subscribe(symbols)` | Begin delivering data for symbols (streaming: WS subscribe; polling: add to poll set) |
| `unsubscribe(symbols)` | Stop delivering data for symbols |
| `health()` | Returns connection state, last message age, error counts, measured latency |
| `on_raw(payload)` | Emits raw provider payloads to the Market Gateway — adapters never normalize |

**Adapter rules:**

1. Adapters emit **raw** payloads. Normalization happens in the Market Gateway (single, testable place — the existing `normalizer.py` pattern extends per provider).
2. Adapters never touch the Event Bus, Redis, the Market Engine, or the database directly.
3. Adapters never contain business logic (no scanner rules, no P&L math, no alert conditions).
4. Adapters own their reconnection primitives (heartbeats, ping/pong per the provider's protocol) but the Market Gateway owns reconnection **policy** (backoff schedule, give-up threshold, failover trigger).
5. Polling adapters expose the same interface as streaming adapters. `subscribe()` on a polling adapter adds symbols to its poll loop; the rest of the system cannot distinguish the two.

---

# Market Gateway

The Market Gateway is the single choke point through which all market data enters the platform. It already exists in embryonic form (`backend/services/market_engine/gateway.py`); this design extends it from a Yahoo wrapper into a multi-provider gateway.

**Nothing may bypass the Market Gateway. Ever.**

## Responsibilities

**1. Provider Selection (delegated)**

The gateway asks the Source Manager which provider serves a given user/symbol context. It never decides on its own.

**2. Connection Management**

• Maintains the lifecycle of every adapter connection (connect, authenticate, subscribe, teardown)
• Multiplexes: one broker WebSocket per user session serves all of that user's subscriptions; one Yahoo poll loop serves all polled symbols platform-wide
• Enforces per-provider connection limits and per-provider subscription limits (e.g. brokers cap instruments per WS connection — the gateway shards subscriptions across connections when needed)

**3. Health Monitoring**

• Tracks per-connection: last message timestamp, message rate, error rate, reconnect count
• A streaming connection with no messages for longer than its expected heartbeat interval is marked **degraded**; past a second threshold, **down**
• Health state changes publish `provider.status` events

**4. Authentication**

• Retrieves per-user broker credentials/tokens via the existing broker credential service (BROKER_INTEGRATION.md)
• Handles token refresh and re-authentication transparently
• On auth failure (revoked token, expired session): marks provider unavailable **for that user**, triggers failover, and emits a user-facing notification through the notification service asking them to reconnect the broker — never an error in the market feed itself

**5. Normalization**

• Every raw payload passes through the normalizer before leaving the gateway
• One normalizer module per provider format, all producing the canonical event model (below)
• Unknown or malformed payloads are logged and dropped — they never propagate

**6. Validation**

• Normalized events pass through the validator (`validator.py` pattern): sanity ranges (price > 0, change_pct within circuit limits), timestamp plausibility, required-field presence
• Cross-provider anomaly check: if a new provider's first quotes for a symbol deviate wildly from the last cached value, flag and quarantine rather than publish

**7. Latency Monitoring**

• Stamps every event with `ingested_at`; computes `latency_ms` where the provider supplies an exchange timestamp
• Maintains rolling p50/p95 latency per provider, fed to the Source Manager's scoring

**8. Automatic Failover (execution)**

• The Source Manager decides *when* to fail over; the gateway executes it: tear down/deprioritize the failed connection, activate the fallback adapter, replay current subscriptions onto it

**9. Reconnection**

• Exponential backoff with jitter per connection (e.g. 1s → 2s → 4s → … capped at 60s)
• Failover to the fallback provider happens **immediately** on disconnect; reconnection to the preferred provider continues in the background
• When the preferred provider recovers and passes a health probation window, the Source Manager switches back

**10. Provider Priority (enforcement)**

• The gateway enforces whatever priority the Source Manager resolves; it holds no priority logic itself

## What the Market Gateway is NOT

• Not a cache (Redis + Market Engine own caching)
• Not a business-rules engine (Market Engine owns processing)
• Not a fan-out layer (Event Bus owns distribution)

---

# Source Manager

The Source Manager is a dedicated service that decides, for every user and every moment, **which provider is the right one** — and keeps that decision current as conditions change.

**Location convention:** `backend/services/market_engine/source_manager.py`

**Implemented in Sprints D1–D2.** D1 built capability-based resolution over the registry, health-based exclusion and `provider.status` publication. D2 completed the resolution semantics: an ordered failover chain instead of a single pick, an explicit `UnavailableReason` instead of a bare `None`, a `ResolutionContext` (user, symbol, exchange) honoured through `MarketDataProvider.is_eligible_for()`, and a fourth health state `unknown`. See ADR-029 for the reasoning and for the one limitation D2 leaves open (a demoted provider cannot recover on its own until Phase 5's re-probe).

## Responsibilities

**1. Detect connected brokers**

• Subscribes to broker connection lifecycle events (`broker.connected`, `broker.disconnected`, token refresh outcomes) from the broker service
• Maintains a per-user registry: which brokers are connected, authenticated, and streaming-capable right now

**2. Determine the best provider**

• Applies the Provider Priority algorithm (next section) per user
• Resolution is cached per user session and invalidated on any relevant event (broker connect/disconnect, provider health change)

**3. Automatically switch providers**

• On any change in the best-provider resolution, orchestrates the switch through the Market Gateway using make-before-break where possible: connect the new provider, confirm first valid data, then release the old one
• Switching is invisible to every downstream consumer

**4. Reconnect failed providers**

• Owns the retry policy the gateway executes
• Tracks probation: a provider that just recovered must deliver clean data for a probation window (e.g. 30 seconds of valid messages) before it is eligible to become primary again — this prevents flapping

**5. Measure latency**

• Consumes the gateway's per-provider latency and health metrics
• Maintains a health score per provider per user: `score = f(connection_state, message_freshness, error_rate, p95_latency)`

**6. Publish provider status**

• Publishes `provider.status` events on every state change: which logical tier is active (`streaming` / `delayed`), health, and transitions
• Status events are **tier-labeled, not provider-labeled**, when they reach the frontend — the UI may show "Live" or "Delayed", never "Zerodha WebSocket" or "Yahoo" (an optional settings/diagnostics page may show provider detail; live UI surfaces may not)

**7. Fall back automatically**

• On provider failure, immediately re-resolves best provider and instructs the gateway to switch
• Fallback is always downward through the priority list; recovery is always upward, gated by probation

## Source Manager State Machine (per user)

```
            ┌────────────────────────────────────────────┐
            │                                            │
            ▼                                            │
      RESOLVING ──────▶ ACTIVE(provider) ──failure──▶ FAILING_OVER
            ▲                  │                         │
            │                  │ better provider         │ fallback
            │                  │ available + probation   │ selected
            │                  │ passed                  │
            │                  ▼                         ▼
            └──────────  SWITCHING  ◀───────────  ACTIVE(fallback)
                                                         │
                                              no provider available
                                                         ▼
                                                    UNAVAILABLE
                                              (retry loop + user banner)
```

---

# Provider Priority Algorithm

Per user, per resolution:

```
Priority 1   Connected Broker WebSocket
             (user's own broker; streaming; tick-level)

Priority 2   Licensed Exchange Feed
             (platform-level entitlement; streaming; future)

Priority 3   Yahoo Finance
             (polling; delayed; always available baseline)
```

**Resolution procedure:**

1. Build the candidate list: every provider whose entitlement applies to this user (broker connected → its adapter is a candidate; licensed feed enabled platform-wide → candidate; Yahoo → always a candidate).
2. Filter out candidates whose health state is `down` or that are inside a failure cool-down.
3. Filter out candidates that lack the required capability for the request context (e.g. a symbol not covered by the broker feed falls through to the next provider **for that symbol**).
4. Pick the highest-priority survivor.
5. If two candidates share a priority tier (user has two brokers connected), pick the one with the better health score; break ties by most recently used (stability over churn).

**As implemented (D2).** Steps 1–3 are `ProviderRegistry.candidates_for(capability, context)`; steps 4–5 are `SourceManager.resolve_feed()`, which returns the winner *and the ordered remainder as a failover chain*. Health ranks in two bands rather than as a continuous score — full latency scoring is Phase 5:

| Band | States | Meaning |
|---|---|---|
| 0 | `up`, `unknown` | not known to be failing |
| 1 | `degraded` | demonstrated intermittent failure |
| — | `down` | filtered out at step 2 |

Ranking is a *stable* sort over the already priority-ordered candidate list, so priority order survives inside each band — which is exactly rules 4 and 5.

`unknown` sharing band 0 with `up` is load-bearing, not a rounding-off. A newly registered priority-1 broker feed leaves `unknown` only by being called and is called only by being selected; ranking it below `up` would pin it behind a healthy Yahoo permanently and this document's Category 2 would never engage. See ADR-029.

**Rules:**

• The user can never end up with *no* provider while Yahoo is reachable — Yahoo is the permanent floor.
• Priority is evaluated per symbol-universe, not just per user: a broker feed covering NSE equities does not disqualify Yahoo from serving a US index the broker doesn't carry. The Source Manager may run two providers side by side for disjoint symbol sets; the downstream feed remains one logical stream.
• A manually-set provider override (admin/diagnostics only) must expire; it never becomes permanent state.

---

# Normalization — The Universal Market Event Model

Every provider payload is converted into exactly one canonical event model before it leaves the Market Gateway. The Market Engine consumes **only** normalized events. This extends the existing canonical formats in `normalizer.py`.

## Envelope (every event)

| Field | Description |
|---|---|
| `event_type` | One of the types below |
| `event_id` | Unique id (dedup across failover replays) |
| `symbol` / `scope` | Instrument or scope (`NIFTY50`, `market`, `sector:IT`) |
| `exchange` | `NSE`, `BSE`, … |
| `timestamp` | Exchange/provider event time (UTC ISO-8601) |
| `ingested_at` | Gateway ingestion time |
| `source_tier` | `streaming` \| `delayed` — the **only** provenance downstream may see |
| `data` | Type-specific payload below |

`source_tier` deliberately replaces any provider name. Internal gateway logs and metrics keep the real provider id; events do not carry it downstream.

## Event Types

**`stock.update`** — canonical StockQuote: `symbol, name, price, open, high, low, close, prev_close, change, change_pct, volume, avg_volume, volume_ratio, market_cap, pe_ratio, week_52_high, week_52_low, day_range, sector, exchange, timestamp` (existing format, unchanged). Streaming providers additionally fill `last_trade_qty`, `bid`, `ask`, `depth` when capable; polled providers leave them null.

**`index.update`** — canonical IndexQuote: `name, value, change, change_pct, timestamp`.

**`ohlc.update`** — candle close/refresh: `symbol, interval (1m/5m/15m/1d/…), open, high, low, close, volume, is_final`.

**`volume.update`** — significant volume delta: `symbol, volume, avg_volume, volume_ratio, spike (bool)`.

**`trade.tick`** — individual trade print (streaming providers only): `symbol, price, quantity, side (if known), trade_time`.

**`market.status`** — session state: `exchange, status (pre_open/open/closed/halted), next_transition_at`.

**`corporate.action`** — `symbol, action_type (dividend/split/bonus/agm/results), ex_date, details`.

**`news.received`** — canonical NewsArticle: `title, summary, source, url, published, sentiment, sentiment_score, companies, sectors, importance` (existing format, unchanged).

## Normalization Rules

1. One normalizer function per (provider, event family). New provider = new normalizer module; no existing normalizer changes.
2. Missing optional fields are `null`, never invented. Downstream code must tolerate nulls for capability-dependent fields.
3. All prices are floats in the instrument's native currency; all timestamps UTC.
4. Symbols are normalized to StockAssist's internal symbol convention (uppercase, exchange-qualified where ambiguous). Symbol-mapping tables per provider live beside the provider's normalizer.
5. A normalizer either returns a fully valid canonical event or `None`. No partial events.

## Relationship to the Event Bus

The Market Engine consumes normalized gateway events, applies processing (caching, scanning, ranking, sector aggregation), and publishes the **existing** Event Bus topics (`price.updated`, `scanner.*`, `portfolio.updated`, `trade.*`, …). Existing topics and payloads do not change — this architecture upgrades what feeds them, not what they are. Streaming providers simply make them fire more often and with fresher data.

---

# Provider Switching — End-to-End Workflow

The canonical upgrade path, in full:

```
User opens StockAssist
        │
        ▼
Source Manager resolves providers for user
        │  (no broker connected)
        ▼
Yahoo Finance polling adapter serves the user
        │  quotes every 15–60s → normalized → Event Bus → Socket.IO → UI
        ▼
User connects Zerodha (OAuth flow via Broker Integration)
        │
        ▼
Broker service publishes broker.connected(user, zerodha)
        │
        ▼
Source Manager detects connection, re-resolves:
        │  Priority 1 (broker WS) now available
        ▼
Market Gateway opens Zerodha WebSocket with the user's token
        │  subscribes the user's active symbol set
        ▼
First valid ticks arrive and pass validation   ← make-before-break gate
        │
        ▼
Source Manager promotes broker feed to primary;
user's symbols are removed from the Yahoo poll set
        │
        ▼
Market Gateway normalizes Zerodha ticks
        │  → same stock.update / index.update / trade.tick events
        ▼
Market Engine receives the identical event format
        │  cache, scanner, portfolio, trading engine — no code path changes
        ▼
Event Bus → Socket.IO → realtimeStore → GSAP animations
        │
        ▼
Frontend never notices the switch.
Prices just start moving tick-by-tick instead of every 30 seconds.
The only visible change: the feed indicator flips from "Delayed" to "Live".
```

**Switching guarantees:**

• **Make-before-break:** the new provider must deliver valid data before the old one is released. If the new provider fails to produce data within a bounded window, the switch aborts and the current provider stays.
• **No gaps:** during the overlap window both providers may deliver; the gateway dedups by `event_id`/monotonic timestamp per symbol so the UI never sees a price move backward due to the overlap.
• **No user action:** switching is never behind a button, modal, or refresh.

The reverse path (user disconnects broker, or logs out of broker) follows the same workflow downward: `broker.disconnected` → re-resolve → symbols rejoin the Yahoo poll set → tier indicator flips to "Delayed".

---

# Failover and Recovery

Failover is automatic, layered, and silent.

```
Broker WebSocket disconnects (network blip, token expiry, broker outage)
        │
        ▼
Gateway marks connection down; Source Manager notified
        │
        ▼
IMMEDIATE fallback: user's symbols join the Yahoo poll set
        │  (feed continues at delayed tier — never stops)
        ▼
Background: gateway reconnects to broker with backoff + jitter
        │
        ├── reconnect succeeds → probation window → promote back to streaming
        │
        └── auth failure (not transient) → stop retrying;
            notify user to reconnect broker; remain on Yahoo


Yahoo also unavailable (rare)
        │
        ▼
Fall back to any other reachable provider in the registry
        │
        ▼
No provider available at all
        │
        ▼
Feed state → UNAVAILABLE
UI shows last cached data clearly timestamped, plus one calm banner:

    "Market feed temporarily unavailable."

Retry loop continues; first provider to recover restores the feed.
```

**Failover rules:**

1. **Never expose internal errors.** No stack traces, no provider names, no "WebSocket closed with code 1006" in any user-facing surface.
2. **The AI never pleads ignorance.** The AI must never say "I don't have live market data." Its context always contains the last known market state with its timestamp; it reasons over that and, when data is stale, frames it naturally ("as of 10:42 AM, RELIANCE was at ₹2,891…"). Staleness is metadata the AI uses, not an excuse it gives.
3. **Cached data is always shown**, always honestly timestamped. An empty screen is a bug; a clearly-stamped last-known state is the design.
4. **Flap suppression.** A provider that fails repeatedly within a window enters an extended cool-down so users don't experience tier oscillation.
5. **Failover is per user where entitlement is per user** (broker feeds) and global where entitlement is global (Yahoo, licensed feeds).

---

# AI Integration

The AI must never communicate with providers. Not directly, not through helper utilities, not "just this once" for a missing field.

```
AI request (chat, morning report, trade review, agent task)
        │
        ▼
AI Context Builder  (backend/services/ai_context_builder.py)
        │   pulls ONLY from Market Engine cache + Event Bus state
        ▼
Market Engine
        │   normalized quotes, sectors, breadth, scanner hits,
        │   news, portfolio state — all provider-agnostic
        ▼
Normalized Market Context
        │   includes source_tier + data timestamps as metadata
        ▼
Claude (via Model Router)
        │
        ▼
Response — reasoning over data whose origin it cannot know
```

**AI rules:**

• The AI never knows which provider generated the data. Context contains `source_tier` (`streaming`/`delayed`) and timestamps so the AI can calibrate its language ("live price" vs "as of 10:42 AM"), but never a provider name.
• The AI Context Builder is the only door between the AI system and market data. New AI features that need market data extend the Context Builder; they never import market services directly.
• When a broker-connected user's feed upgrades to streaming, the AI's context automatically becomes fresher with zero prompt or pipeline changes — the same context fields simply carry newer timestamps.

---

# Frontend Integration

React components never contain provider-specific logic. The words "yahoo", "zerodha", "kite", "upstox" must never appear in frontend market-data code.

**The frontend's entire market data world:**

1. Connect to Socket.IO.
2. Consume normalized events into `realtimeStore.js`.
3. Render and animate from store state.
4. Optionally render the feed-tier indicator from `provider.status` events (`Live` / `Delayed` / `Unavailable` — tier only, never provider).

**Frontend rules:**

• Provider switching is invisible. No loading spinner, no remount, no toast on switch. Data simply gets faster or slower.
• Components must tolerate capability-dependent nulls (`bid`/`ask`/`depth` present only on streaming tier) by omitting those UI elements, not by branching on provider.
• The delayed tier and the streaming tier use identical components, identical stores, and identical animations. Tier affects update frequency, nothing else.
• During `UNAVAILABLE`, components render last-known store state with the standard staleness treatment defined in DESIGN_SYSTEM.md, plus the single global banner.

---

# Business Advantage

Why this architecture is strategically superior:

**1. Free users get a genuinely good experience.**
Yahoo-fed, push-delivered, AI-explained market data is better than most free products offer. Free users become educated users; educated users connect brokers.

**2. Broker-connected users get professional real-time data at zero cost to StockAssist.**
The entitlement already exists in the brokerage relationship. StockAssist activates value the user already owns but their broker's own app under-delivers on. This single feature — "connect your broker, get a live AI trading terminal" — is the platform's sharpest acquisition hook.

**3. Premium sells what only StockAssist has: intelligence.**
No data licensing costs in the paid tier, no vendor dependency in the revenue line, no competing with brokers or exchanges on their own commodity. Margins scale with AI efficiency, which improves every year.

**4. The result is a sustainable SaaS model:**

| Tier | Data cost to StockAssist | User pays for | Moat |
|---|---|---|---|
| Free | ~zero (Yahoo, cached) | Nothing | Education + AI taste |
| Broker-connected | Zero (user's entitlement) | Nothing extra | Live terminal experience |
| Premium | Zero incremental | AI intelligence | Models, context, workflows |
| Enterprise (future) | Licensed feeds | Feeds + intelligence | Compliance-grade data + AI |

**5. Provider independence is risk management.**
No single vendor can raise prices, change terms, or shut down and take the platform with it. Every provider is one adapter deep.

---

# Developer Rules

These are permanent, non-negotiable rules. Violating any of them is grounds for rejecting a PR regardless of how well it otherwise works.

1. **Never hardcode provider logic** outside a provider adapter. Provider names appear in exactly two places: adapter modules and their normalizer/symbol-mapping modules.

2. **Never bypass the Market Gateway.** No service fetches from a provider directly — not for a quick script, not for a one-off endpoint, not for a "temporary" feature.

3. **Never bypass the Source Manager.** No code picks a provider by name. If you need data, you ask the gateway; the Source Manager decides who answers.

4. **Never let the frontend know the provider type.** Tier (`streaming`/`delayed`) is the maximum provenance the frontend receives.

5. **Never let the AI know the provider type.** Context carries tier and timestamps only.

6. **Always normalize events.** Raw provider payloads must not cross the gateway boundary. A new field a provider offers becomes useful only after it is added to the canonical model.

7. **Always publish normalized events** through the existing Event Bus topics. No side channels, no direct service-to-service data handoffs for market data.

8. **Always support provider switching.** Any new consumer of market data must be correct under a mid-session provider switch: no assumptions about update frequency, no assumptions about field availability beyond the canonical required set, idempotent handling of duplicate events.

9. **New provider = new adapter + new normalizer + registry entry. Nothing else.** If adding a provider requires touching the Market Engine, the Event Bus, the AI, or the frontend, the design has been violated — stop and fix the design breach, not the symptom.

10. **Test the failure paths.** Every adapter ships with tests for: disconnect mid-stream, auth expiry, malformed payloads, duplicate delivery, and failover hand-off. A provider integration without failure tests is incomplete.

---

# Implementation Phasing (Guidance)

This document defines the target architecture. Recommended build order for whoever implements it:

**Phase 1 — Formalize the contract.** ✅ **IMPLEMENTED — Sprint D1, 2026-08-19.** Extract the Provider Adapter interface; wrap the existing Yahoo path (`real_market.py` behind `gateway.py`) as `YahooPollingAdapter`. Behavior identical, structure ready.

As built:

```
backend/services/market_engine/
    providers/base.py        MarketDataProvider contract, Capability,
                             ProviderKind, SourceTier, ProviderHealth
    providers/registry.py    ProviderRegistry — priority-ordered, capability-
                             and health-filtered candidate resolution
    providers/yahoo.py       YahooPollingAdapter (priority 3, polling, delayed)
    source_manager.py        SourceManager — resolution, health, provider.status
    gateway.py               resolves by capability; normalizes with the
                             adapter's normalizer_key; stamps source_tier
```

Three deviations from this document, each recorded with its reasoning in **ADR-028** and each carrying a dated closure sprint:

1. **The adapter contract omits `subscribe`/`unsubscribe`/`on_raw`.** D1 ships one polling provider and no consumer able to receive pushed ticks; the push surface lands in Phase 3 with the adapter and consumer that need it. `ProviderKind` already separates the families.
2. **Adapters return the provider's raw payload, but `real_market.py` also computes derived analytics** (RSI/MACD/VWAP, breadth, sentiment, mover ranking) that are Market Engine business logic living in the provider module. Relocating them is Phase 2.
3. **`source_tier` is added; the legacy `source: "yahoo_finance"` field remains in REST payloads** for API compatibility. ✅ **CLOSED 2026-08-20 (DD-1/DD-2, ADR-030).** The provider name is gone from every market-data response; `source_tier` replaces it, read from the Source Manager rather than written as a literal. No open violation of Developer Rule 4 remains on the public contract.

Not every consumer reaches the gateway yet, though the public market routes now do (DD-1, 2026-08-20): `server.py` and five service modules still call the provider client directly on non-contract paths. That set is frozen in an enforced register (`KNOWN_GATEWAY_BYPASSES`, `backend/tests/test_market_gateway.py`) which may only shrink — a new bypass fails CI, and a stale entry fails once the module is migrated. The Market Engine itself and the AI Context Builder are fully migrated.

**Phase 2 — Source Manager.** ✅ **BACKEND IMPLEMENTED — Sprint D2, 2026-08-20.** Introduce the Source Manager with a single provider (Yahoo). Wire `provider.status` events and the frontend tier indicator.

As built (D2, on top of D1):

```
providers/base.py        ProviderState.UNKNOWN (4th state, initial);
                         ResolutionContext (user_id, symbol, exchange);
                         owner_user_id + is_eligible_for() entitlement filter
providers/registry.py    candidates_for(capability, context) — entitlement,
                         capability and health filtering; entitled_for(context)
source_manager.py        Resolution (provider + ordered failover chain + reason),
                         UnavailableReason, resolve_feed(), failover_chain(),
                         HEALTH_RANK
gateway.py               walks the failover chain inside one request; records an
                         explicit unavailable state; supplies the symbol to the
                         resolution context at every call site that has one
```

Still outstanding for Phase 2:

• **The frontend tier indicator.** The backend publishes everything it needs — `provider.status` carries `state`, `tier`, `reason` and `previous_tier`, and `MarketGateway.status["feed"]` exposes the same — but no component renders it yet.
• **One of the three D1 deviations.** The push surface still lands in Phase 3 (unchanged). Derived analytics still live in `real_market.py` (DD-3).

Closed alongside D2:

• **DD-5** — no live UI surface names a provider any more.
• **DD-1 / DD-2 (2026-08-20, ADR-030)** — the public market routes read through the gateway, the sector shape is reconciled (canonical `name` plus a deprecated `sector` alias), and `source: "yahoo_finance"` is replaced everywhere by `source_tier` sourced from the Source Manager. ADR-028's open approval item is closed. `Markets.jsx` already renders Live/Delayed from `source_tier`, which is most of the tier indicator's groundwork.

**Phase 3 — Broker Provider Framework.** ⚠️ **RE-SCOPED, then IMPLEMENTED — Sprint D3, 2026-08-20 (ADR-031).**

Phase 3 was specified here as "the Zerodha Kite WebSocket adapter". Inspection before D3 found that the broker layer underneath it was not yet a framework — a hardcoded broker dict rather than a registry, no capability model, no broker gateway, canonical shapes documented only in a docstring, and broker names branched on inside `server.py`, `broker_engine.py` and `stream.py`. Building the streaming feed first would have hung the headline feature on all of that. D3 built the framework; the streaming feed moved to Phase 4.

What D3 delivered that this document depends on:

```
services/brokers/
    capabilities.py  BrokerCapability + registration-time verification
    registry.py      BrokerRegistry — one long-lived adapter per broker
    gateway.py       BrokerGateway — the broker-side choke point
    contracts.py     canonical broker data, enforced at the boundary
    errors.py        one broker error vocabulary
    health.py        broker API health (auth failures excluded)
    credentials.py   the authentication / configuration boundary
```

**Source Manager responsibility 1 is now implementable and implemented.** This document has always specified that the Source Manager "subscribes to broker connection lifecycle events" and "maintains a per-user registry: which brokers are connected, authenticated, and streaming-capable right now". It could not, for a mundane reason: `broker.connected` and `broker.disconnected` were documented in BROKER_INTEGRATION.md and published by nothing. D3's Broker Gateway publishes both, carrying the broker's *capabilities*, and `SourceManager.connected_brokers()` / `streaming_brokers()` maintain the registry. The two subsystems meet only on the Event Bus: the Market Engine imports no broker module and the broker layer imports no Market Engine module.

**What D3 deliberately did NOT do** is register a broker as a market-data provider. Doing so would have meant either a fabricated `streaming` tier — forbidden by this document's normalization rules and by CLAUDE.md's data rules — or a REST-polled broker provider silently taking a connected user's quotes away from the Yahoo baseline with none of the make-before-break machinery that makes such a switch safe. Pinned by `test_d3_does_not_register_a_broker_as_a_market_data_provider`.

**Phase 4 — Broker market-data streaming, then remaining brokers.** The Zerodha Kite WebSocket adapter as a registered priority-1 market provider (`owner_user_id` set to the connected user), the streaming push surface on `MarketDataProvider`, tick normalization, per-user resolution, make-before-break switching, failover back to Yahoo — then Upstox, Angel One, Fyers, Dhan, each one adapter. This phase delivers the headline feature. The entitlement filter (`is_eligible_for`, D2) and the per-user connected-broker registry (D3) are already in place for it.

**Phase 5 — Hardening.** Latency scoring, flap suppression, probation windows, multi-connection sharding, chaos tests (kill connections in staging, verify silent failover).

**Phase 6 (future) — Enterprise feeds** as entitlements and licensing arrive.

Each phase is independently shippable and never regresses the previous tier's experience.

---

# Long-Term Vision

StockAssist AI is to become a **provider-independent AI trading platform**.

Market data providers will change over the platform's life: brokers will be added, APIs will be deprecated, licensed feeds will be signed, new asset classes will arrive. None of that may ever again require touching business logic, AI pipelines, or the frontend.

The Market Gateway and Source Manager exist so that the answer to "how do we add provider X?" is permanently:

**"Implement one adapter. Register it. Done."**

Every future engineer reading this document should hold one picture in mind: below the Source Manager, the platform sees a single, normalized, always-on market feed. Everything above that line is StockAssist AI. Everything below it is replaceable.

---

# Glossary

| Term | Meaning |
|---|---|
| Provider | Any external source of market data (broker WS, Yahoo, licensed feed) |
| Adapter | The only module allowed to speak a provider's protocol |
| Market Gateway | Single entry point: connections, auth, normalization, validation, health |
| Source Manager | Per-user provider resolution, switching, failover, status |
| Canonical event | A normalized market event per the universal event model |
| Source tier | `streaming` or `delayed` — the only provenance visible downstream |
| Make-before-break | New provider proven live before old provider released |
| Probation | Clean-data window a recovered provider must pass before re-promotion |
| Flap suppression | Cool-down preventing rapid tier oscillation |
| Entitlement | The legal/contractual right to consume a feed (user's broker account, platform license) |

---

*End of document. Changes to this architecture require an entry in DECISIONS.md.*

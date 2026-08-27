# StockAssist AI
## Master Tasks

Version: 1.2

Status: Feature Freeze — Production Hardening (PH1–PH3)

---

# Purpose

This document is the master implementation tracker for StockAssist AI.

It contains every planned task, feature, milestone, bug, technical debt item, and future enhancement.

This is the first document Claude should update whenever development changes.

Every completed feature should update this document.

---

# Task Status

Every task must have one status.

NOT_STARTED

PLANNING

DESIGNING

IN_PROGRESS

BLOCKED

TESTING

READY_FOR_REVIEW

COMPLETED

DEPRECATED

---

# Priority Levels

Critical

High

Medium

Low

Future

---

# Milestone 1
Foundation

Status

IN_PROGRESS

---

## Authentication

Priority

Critical

Tasks

- User Registration
- Login
- Logout
- JWT Authentication
- Refresh Tokens
- Password Reset
- Email Verification
- Session Management
- Role-Based Access

Status

IN_PROGRESS

---

## Landing Page

Tasks

- Hero Section
- Features
- Pricing
- Testimonials
- FAQ
- CTA
- Footer
- Responsive Design
- Animations

Status

COMPLETED

---

## Dashboard

Tasks

- [x] Dashboard Layout
- [x] Sidebar
- [x] Header
- [x] Cards — stat-card, glass-card, premium-card
- [x] Search — global stock search in Navbar + SearchBox component
- [x] Notifications — NotificationPanel + dashboard widget
- [x] Quick Actions — quick-action bar (New Trade, AI Analysis, Morning Report, Portfolio, Stock Picks, Market News)
- [x] Theme Switch — light/dark toggle in Navbar
- [x] Responsive Layout
- [x] Index Strip — Nifty, Bank Nifty, Sensex, India VIX with sparkline charts
- [x] Commodities Strip — Gold, Crude Oil, Silver, USD/INR live prices
- [x] Morning Report Card — AI morning briefing summary
- [x] Top AI Picks Card — top 3 AI-scored stock picks
- [x] Portfolio Summary Card — value, P/L, holdings count
- [x] Watchlist Widget — top 5 watchlist items with live quotes
- [x] Market News Widget — latest 5 headlines with sentiment
- [x] AI Activity Feed — live WebSocket activity stream
- [x] Notifications Widget — recent unread notifications
- [x] Recent Stocks — localStorage-tracked recently viewed stocks
- [x] Market Breadth — advances/declines/unchanged with breadth bar
- [x] Global Markets Widget — international index prices
- [x] Sector Performance — heatmap grid
- [x] AI Lessons Card — latest trade coaching grades
- [x] Market Status Badge — OPEN/CLOSED indicator
- [x] WebSocket Status — LIVE/OFFLINE badge

Status

COMPLETED

---

## UI System

Tasks

- Design System
- Glass Cards
- Typography
- Color Tokens
- Animations
- GSAP
- Framer Motion
- Responsive Layout

Status

IN_PROGRESS

---

# Milestone 2
Market Intelligence

Status

IN_PROGRESS

---

## Market Engine

Tasks

- Market Gateway
- Market Collectors
- [x] Data Normalization — live Yahoo Finance quotes/indices normalized in services.real_market
- [x] Validation — unavailable data surfaced explicitly (available:false), never simulated
- [x] Redis Cache — services/cache.py (Redis when REDIS_URL set, in-memory fallback)
- [x] Event Publishing — event_bus wired to sockets via the R2 bridge; Redis
  Pub/Sub fan-out added to services/cache.py (Sprint R2)
- [x] Live WebSocket — market_broadcast_loop streams real overview only
- [x] Market Health — India VIX (^INDIAVIX), breadth & sentiment derived from live quotes

---

## Market Dashboard

Tasks

- [x] Nifty — live (^NSEI)
- [x] Sensex — live (^BSESN)
- [x] Bank Nifty — live (^NSEBANK)
- [x] India VIX — live (^INDIAVIX), null when unavailable
- [x] Commodities — live (Yahoo futures/forex), available:false on failure
- [x] Global Markets — live (Yahoo global indices), available:false on failure
- [x] Heatmap — live gainers/losers from universe quotes
- [x] Sector Analysis — live sector averages (fetch_real_sectors)

---

## Stock Scanner

Tasks

- [x] Technical Scanner — real RSI/MACD/volume from Yahoo history
- [x] Volume Scanner — real volume ratio vs 20-day average
- [x] Breakout Scanner — real chart-pattern detection
- Swing Scanner
- Long-Term Scanner
- [x] Momentum Scanner — live day-change + volume shortlist (advisor)
- [x] AI Ranking — fetch_real_top_picks scores live technicals; unavailable when no live data
- [x] Live Scanner Feed — continuous worker + push events + animated cards (Sprint R4)

---

## News Engine

Tasks

- [x] News Collection — RSS aggregation (services.news_service)
- [x] Deduplication — title-based dedupe
- [x] Sentiment Analysis — deterministic keyword sentiment per article + /api/news/sentiment aggregate
- Company Mapping
- Sector Mapping
- [x] AI Summary — ai_market_summary on live data

---

# Milestone 3
Trading

Status

IN_PROGRESS

---

Tasks

- [x] Portfolio — Portfolio Intelligence (Sprint 8): server-side allocation,
  diversification, risk score, sector exposure, P&L, dividends, performance,
  suggestions over a broker-primary holdings merge
- [x] Holdings — unified broker + manual holdings (source-tagged)
- [x] Positions — Trade Monitor active positions (live P&L, engine timeline)
- [x] Orders — unified order history (GET /api/orders) + Orders tab with
  cancel/modify of pending broker orders (Sprint 9)
- [x] Trade Monitor — Trading Engine (Sprint 9): risk-gated buy/sell entry
  with optional live broker execution, multi-target (T1–T3) partial booking,
  trailing stop (percent/points, never loosens), server-side modify of
  SL/targets/trailing, partial + at-market exits, per-trade event timeline,
  consented auto-exit via broker (per-trade opt-in)
- [x] Risk Manager — pre-trade validation (SL/target sanity, max trades/day,
  daily loss budget, risk-per-trade guideline) + GET /api/trades/risk/summary
  dashboard strip (Sprint 9)
- [x] Trading Platform selection (Sprint 9.1) — users.preferred_broker chosen
  explicitly in Settings → Trading Platform (radio list of connected brokers +
  "track only"; NO default). Quick trades (POST /api/trades/quick) and the New
  Trade form default route through the chosen platform; hardcoded Zerodha
  quick-trade path retired from the UI
- [x] Journal — trade journal + stats + weekly AI review + setup success rates
- [x] Paper Trading
- [x] Backtesting
- Strategy Builder
- Risk Dashboard (dedicated page — summary strip shipped in Sprint 9)

---

# Milestone 4
Broker Integration

Status

COMPLETED (Sprint 7)

---

Tasks

- [x] Zerodha OAuth — Kite Connect login flow, per-user encrypted sessions
- [x] Upstox OAuth — authorization-code flow, state carries user id
- [x] Token Refresh — refresh_session() interface; daily-expiry brokers surface explicit reconnect (Zerodha ~6:00, Upstox ~3:30 IST)
- [x] Holdings Sync — normalized across brokers, persisted to db.holdings
- [x] Portfolio Sync — POST /api/brokers/{broker}/sync → db.portfolios + portfolio_synced WS event
- [x] Orders — place/modify/cancel via unified adapter interface, audit-logged
- [x] Positions — normalized (side, realised/unrealised P&L)
- [x] Margins & Funds — GET /api/brokers/{broker}/funds|margins
- [x] Trade History — GET /api/brokers/{broker}/trades (broker trade book)
- [x] WebSocket — Kite ticker (binary LTP + order updates), Upstox portfolio stream (orders), Upstox v3 market feed (protobuf ticks, D4.7) and Angel One SmartAPI smart-stream (binary LTP ticks, D4.9), auto-reconnect w/ backoff
- [x] Live Quotes — portfolio-instrument LTP ticks pushed per-user over app WS

---

# Milestone 5
AI Intelligence

Status

COMPLETED

Sprint 6 (AI Workspace) delivered the unified AI intelligence layer:
centralized Prompt Library, Model Router, AI Memory, and the /api/ai
namespace, wired into the AI Workspace frontend.

---

Tasks

- [x] AI Chat — Prompt-Library + Memory + Model Router (POST /api/chat)
- [x] AI Memory — user memory + lessons (GET/PUT /api/ai/memory, services/ai_memory.py)
- [x] Morning Report — page + heartbeat/scheduler (GET /api/analysis/morning-report)
- [x] Portfolio Review — Portfolio AI (POST /api/ai/portfolio-review)
- [x] Stock Advisor — AI Investment Advisor (POST /api/advisor/recommend)
- [x] SIP Advisor — (POST /api/sip/recommend)
- [x] Trade Review — Trading Coach (POST /api/ai/trade-review)
- [x] AI Debate — dual-AI debate engine (services/ai_debate_engine.py)
- [x] Reflection Engine — extracts + stores durable lessons (POST /api/ai/reflect)
- [x] Learning Mentor — teach any concept (POST /api/ai/learn)
- [x] Conversation History — sessions list/create/delete (/api/ai/conversations)
- [x] Model Router — task→model routing + status (services/model_router.py, GET /api/ai/status)
- [x] Prompt Library — centralized versioned prompts (services/prompt_library.py, GET /api/ai/prompts)
- [x] AI Activity Timeline — live background-work feed (GET /api/ai/activity)

---

# Milestone 6
Admin Portal

Status

NOT_STARTED

---

Tasks

- Dashboard
- User Management
- API Monitoring
- AI Monitoring
- Revenue Dashboard
- Logs
- Feature Flags
- Notifications
- Support
- Analytics

---

# Milestone 7
Business Platform

Status

NOT_STARTED

---

Tasks

- Subscription
- Payments
- Credit Packs
- Billing
- Coupons
- Referral System
- Enterprise Plans
- Revenue Analytics

---

# Milestone 8
Learning Platform

Status

FUTURE

---

Tasks

- Courses
- AI Mentor
- Quizzes
- Certificates
- Progress Tracking

---

# Milestone 9
Global Expansion

Status

FUTURE

---

Tasks

- US Stocks
- Forex
- Crypto
- ETF
- Mutual Funds
- International Brokers
- Multi Currency
- Multi Language

---

# Infrastructure

Tasks

Docker

CI/CD

Monitoring

Logging

Redis

MongoDB

Cloudflare

Railway

GitHub Actions

Health Checks

Backups

---

# Security

Tasks

JWT

RBAC

Encryption

Rate Limiting

MFA

Secrets

Audit Logs

OWASP

Security Testing

---

# Documentation

Tasks

Architecture

API

Database

Deployment

Testing

Roadmap

Coding Standards

Developer Guides

---

# Sprint 2 — Replace Mock Data

Status

COMPLETED

Objective

Replace every remaining mock dataset with real backend integration. Where live
data is unavailable, return an explicit "unavailable" state — never silently
fall back to random values.

Delivered

- Backend `market_data.py` reduced to factual reference metadata only
  (symbols, names, sectors). ALL random price/indicator/pick/chart/activity
  generators removed.
- `services/real_market.py` (Yahoo Finance / NSE) is the single live source:
  - Removed every simulated fallback (quotes, charts, top picks, global,
    commodities). Failures now return empty / `available:false`, never random.
  - Added live India VIX (`^INDIAVIX`), advance/decline breadth and market
    sentiment derived from live universe quotes.
  - Added live NSE/BSE stock search via Yahoo search API (`search_yahoo_stocks`),
    static metadata search only as offline fallback.
  - FII/DII already real (NSE); ultimate fallback now `available:false` with nulls.
- `services/cache.py` — shared cache: Redis when `REDIS_URL` is set, in-memory
  otherwise. Wired into real_market + news_service. `redis==5.0.8` added to
  requirements.
- `services/news_service.py` — deterministic keyword sentiment per article +
  `get_market_sentiment()` aggregate. New endpoint `GET /api/news/sentiment`.
- `server.py` — every user-facing path (overview, stock detail/live, explain,
  gemini analyze, top-picks, morning report, advisor, watchlist) returns
  explicit unavailable instead of simulated data. Removed `random.randint`
  news-score in full-report (now real news-mention sentiment). Fixed
  `source:"simulated"` mislabels. Scheduler/webhook signatures trimmed.
- Frontend (no UI redesign): News sentiment gauge now fetches
  `/news/sentiment`; Dashboard/Markets market-breadth read live
  `overview.advance_decline` (was hardcoded 1042/842/176) with unavailable
  states; Dashboard AI-activity placeholders removed (loading skeleton);
  StockPicks / MorningReport / InvestmentAdvisor / AIAssistant handle
  `available:false`; AIAssistant "Trade Ideas" now real top-picks (was
  hardcoded RELIANCE/TCS/HDFCBANK).

Verification

- Backend in-process suites: 49 passed (advisor, morning_report, webhooks,
  paper_trading, chart_patterns, activity_feed, backtesting, trade_coaching,
  setup_stats). Live smoke test confirmed real overview + search.
- Frontend: all changed JSX files parse; no stale identifiers.
- The HTTP-integration suites (test_backend/phase*) require a running server
  and are unaffected by these changes.

---

# Sprint 3 — Dashboard Completion

Status

COMPLETED

Objective

Complete the Dashboard with all planned widgets, live data integration,
improved loading states, animations, and performance. No UI redesign — extend
and improve the existing design language.

Delivered

- **Quick Actions Bar** — row of 6 quick-action buttons (New Trade, AI
  Analysis, Morning Report, Portfolio, Stock Picks, Market News) with
  navigation; responsive horizontal scroll on mobile.
- **Index Sparkline** — Nifty 50 stat-card now shows a mini intraday sparkline
  chart overlay (last 30 data points from `/stocks/^NSEI/chart?period=1D`).
- **Commodities & Forex Strip** — new 4-column strip showing Gold, Crude Oil,
  Silver, and USD/INR live prices from `/market/commodities`.
- **Watchlist Widget** — shows top 5 watchlist items with live quotes from
  `/watchlist`; empty state with "Add stocks" CTA.
- **Market News Widget** — latest 5 headlines from `/news` with sentiment
  indicators and source/time metadata.
- **Notifications Widget** — recent unread notifications from `/notifications`
  with severity color coding.
- **Recent Stocks** — localStorage-based recently viewed stocks (tracked from
  StockDetail page visits); horizontal scroll chip display.
- **Global Markets Widget** — international index prices from `/market/global`
  in a 2-column grid.
- **Market Status Badge** — MARKET OPEN / MARKET CLOSED indicator in header
  derived from `overview.market_status`.
- **Breadth Bar** — visual bar showing advance/decline ratio below the numeric
  breadth grid.
- **Performance** — all 11 API fetches fire in parallel on mount; core market
  data refreshes every 30s; greeting and action lists memoized; WebSocket
  fallback polling preserved.
- **Loading States** — improved skeleton loader includes quick-actions row and
  content grid placeholders.
- **Animations** — scroll-reveal staggering on every section; hover
  micro-interactions on cards and quick-action buttons.
- **StockDetail recent-stocks tracking** — viewing any stock detail page now
  writes the symbol to `localStorage(sa_recent_stocks)`, keeping the last 6
  entries for the Dashboard widget.

Verification

- Frontend production build passes (`craco build`).
- No new dependencies added.
- All existing JSX files unmodified except Dashboard.jsx and StockDetail.jsx.

---

# Sprint 7 — Broker Integration

Status

COMPLETED

Objective

Complete broker integration for Zerodha and Upstox using official broker APIs
only — OAuth, portfolio sync, orders, positions, margins, funds, trade
history, realtime WebSocket, token refresh/reconnect. No simulated trading.

Delivered

- **Broker adapter layer** (`backend/services/brokers/`) — provider-independent
  `BrokerAdapter` interface (base.py) with normalized Holding/Position/Order/
  Trade/Funds shapes and unified order statuses; `zerodha.py` (Kite Connect v3)
  and `upstox.py` (Upstox v2) adapters. Adding a broker = one new adapter.
- **Encrypted token storage** (`brokers/crypto.py`) — Fernet encryption at rest
  for access/refresh/public tokens (`BROKER_TOKEN_KEY` env or key derived from
  `JWT_SECRET`); legacy plaintext tokens auto-migrate to encrypted on first
  load. Tokens never returned to the browser or written to logs/audits.
- **Broker Engine** (`services/broker_engine.py`) — single entry point: per-user
  session lifecycle (exchange → store → expiry → refresh-or-reconnect),
  portfolio sync into `portfolios`/`holdings` collections, order
  place/modify/cancel with immutable `audit_logs` entries, per-broker status,
  startup session restore, WS push wiring.
- **Realtime streaming** (`brokers/stream.py`) — official feeds only: Kite
  ticker WebSocket (binary LTP tick parsing + JSON order updates) and Upstox
  portfolio-stream-feed (order updates); exponential-backoff reconnect;
  auth-expiry detection stops the stream and notifies the user. Updates
  forwarded to the app's per-user WebSocket (`broker_order_update`,
  `broker_price_tick`, `portfolio_synced`, `broker_status`) and persisted to
  `db.orders`; fills/rejections create notifications.
- **Unified API** (`/api/brokers`) — list/status, `{broker}/login-url`,
  `session`, public OAuth `callback` (Zerodha `request_token`+`uid`, Upstox
  `code`+`state`), `disconnect`, `sync`, `profile`, `holdings`, `positions`,
  `funds`, `margins`, `orders` (GET/POST/PATCH/DELETE), `trades`. Pydantic
  validation (`BrokerOrderCreate`/`BrokerOrderModify`). Broker auth failures
  return 409 `BROKER_AUTH` (not 401, which the frontend treats as app-session
  loss); broker upstream errors 502; rate limits 429.
- **Legacy compatibility** — all `/api/zerodha/*` routes now delegate to the
  Broker Engine per-user (previously one global in-memory token shared by all
  users); `services/zerodha_service.py` reduced to a deprecated shim. Quick
  Trade no longer creates an OPEN trade when the broker order fails (was:
  silent `[SIM]` fallback). Emergency Stop cancels orders + liquidates
  positions per-user through the engine. `/zerodha/funds` now also returns the
  `available/used/total` aliases the Portfolio page reads (fixed undefined
  fields).
- **Frontend** — `services/brokerService.js` (single broker API gateway);
  Settings "Broker Accounts" card lists every supported broker with
  status/stream badges, Connect/Reconnect, Sync Now, Disconnect;
  `BrokerCallback.jsx` handles both Zerodha (`request_token`) and Upstox
  (`code`) redirects. No UI redesign — existing design language extended.
- **Config** — `.env` gains `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`,
  `UPSTOX_REDIRECT_URL` (point at `/api/brokers/upstox/callback`) and optional
  `BROKER_TOKEN_KEY`.

Verification

- `tests/test_broker_integration.py`: 31 hermetic tests (crypto roundtrip +
  legacy migration, login URLs, session-expiry rules, response normalization,
  Kite binary tick parsing, engine storage/audit/status/disconnect, route
  registration, auth guards, validation). All broker HTTP mocked at
  `BrokerAdapter._request` — CI never touches real broker APIs.
- Full in-process backend suite: 116 passed.
- Frontend production build passes (`craco build`).

Notes / follow-ups

- Zerodha and Upstox retail tokens cannot be silently refreshed (daily expiry
  is a broker rule); the engine surfaces explicit reconnect states instead.
- Upstox market-data ticks use a protobuf feed on a *separate* WebSocket from
  the order stream. Both are supported as of D4.7 (`ltpc` mode); the adapter
  decodes the proto3 wire format itself rather than adding a protobuf runtime
  dependency. Zerodha ticks stream in LTP mode on its single ticker socket.
- Upstox order placement addresses instruments by instrument key; the adapter
  resolves symbols from the user's holdings/positions, or accepts
  `instrument_token` explicitly.

---

# Sprint 8 — Portfolio Intelligence

Status

COMPLETED

Objective

Turn the Portfolio surface into a production-grade, server-side Portfolio
Intelligence layer that is the single source of truth for holdings, allocation,
diversification, risk, P&L, dividends, performance and rebalancing — powered by
a broker-primary merge of real broker holdings and manual trades. No fabricated
data anywhere.

Delivered

- **Portfolio Engine** (`backend/services/portfolio_engine.py`) — the single
  source of truth. `build_holdings` performs a **broker-primary merge**: real
  broker holdings (`db.holdings`) are the portfolio, manual non-paper open
  trades (`db.trades`) are merged as a `source`-tagged layer, a symbol held in
  both keeps the broker row (no double-count), paper trades are excluded. Pure,
  unit-tested analytics: `compute_allocation` (by holding + sector),
  `compute_diversification` (HHI + effective-holdings + label),
  `compute_risk_score` (additive 0-100 with named, explainable factors),
  `compute_pnl` (realized + unrealized + best/worst), `compute_movers`,
  `build_suggestions` (concentration + AI-alert driven), `compute_dividends`
  (real trailing rates or explicit `available:false`), and the `build_intelligence`
  orchestrator.
- **Live dividend data** — `services/real_market.fetch_dividend_info` pulls real
  trailing annual dividend rate/yield from Yahoo `quoteSummary` (cached); missing
  data surfaces `available:false` and is never fabricated.
- **Performance / equity curve** — new `portfolio_snapshots` collection +
  `portfolio_snapshot_job` scheduled 4:05 PM IST (6th cron job). `get_performance`
  returns the equity curve + returns + best/worst day, or `available:false` until
  ≥2 real end-of-day snapshots exist (built forward, never back-filled).
- **API** — `GET /api/portfolio` and `/summary` now delegate to the engine
  (broker-inclusive, `source` + `day_change_pct` added). New:
  `GET /api/portfolio/intelligence`, `GET /api/portfolio/performance?range=`,
  `GET /api/portfolio/export` (CSV).
- **Frontend** (`frontend/src/pages/Portfolio.jsx`) — consumes the server bundle
  (drops all client-side math). The tab bar is now **functional**: Overview,
  Holdings (with Broker/Manual source badges), Performance (recharts equity curve
  + returns, real empty-state), Allocation (holding + sector), AI Review (wires
  `POST /api/ai/portfolio-review`), Transactions (`/api/trades/history`). Dividend
  section shows real income/yield or explicit unavailable; the Download button
  now exports CSV. No UI redesign — existing design language extended.

Verification

- `tests/test_portfolio_engine.py`: 18 hermetic tests (merge/de-dup, broker-mark
  fallback, allocation, diversification/HHI, P&L, movers, risk factors,
  suggestions, dividend real + unavailable paths, performance empty-state +
  returns, snapshot upsert, orchestrator bundle). Live market/dividend fetchers
  injected as stubs — CI never touches Yahoo.
- Full in-process backend suite: 232 passed (the 4 `test_phase*` failures are the
  pre-existing `requests`-based tests that require a running dev server + live
  Yahoo; unaffected by this sprint).
- Frontend production build passes (`craco build`); no warnings in Portfolio.jsx.

---

# Sprint R2 — Event Bus & Infrastructure

Status

COMPLETED

Authority

REALTIME_SYSTEM.md (Path A — evolve FastAPI native WebSocket to satisfy the
doc's intent, per the Sprint R1 audit in REALTIME_MIGRATION_PLAN.md)

Objective

Build the backend real-time backbone the R1 audit found missing: wire the
in-process market event bus to WebSocket clients, add a channel subscription
model, add Redis Pub/Sub cross-process fan-out, and land the near-free frontend
fix so previously-dropped messages render. All changes are additive — existing
loops and their frontend handlers are untouched.

Delivered

- **Event bus catch-all** (`services/market_engine/event_bus.py`) — `subscribe("*")`
  now matches every event, so one bridge can forward all domains. Exact and
  `prefix.*` matching unchanged.
- **Channel model** (`server.py` `ConnectionManager`) — per-connection channel
  sets + `subscribe`/`unsubscribe`/`broadcast_to_channel`, with a shared
  `_reap()` dead-socket cleanup across `active`/`channels`/`user_connections`.
  New WS verbs `{"type":"subscribe|unsubscribe","channels":[...]}` (ack'd);
  legacy `subscribe_prices`/`ping` preserved.
- **Event → socket bridge** (`services/realtime/event_bridge.py`, new) — a
  catch-all bus subscriber maps each event to a channel (`DOMAIN_CHANNEL`),
  wraps it in a stable `{"type":"event","event","channel","data","timestamp"}`
  envelope, and delivers per-user when the payload carries `user_id` else to the
  channel's subscribers. Mirrors events to Redis (`sa:events`) with a
  per-process `ORIGIN_ID` guard; a Redis listener re-delivers other processes'
  events locally (no bus re-publish → no loop). Wired at startup in `server.py`.
- **Redis Pub/Sub** (`services/cache.py`) — `cache_publish()` +
  `start_pubsub_listener()` reusing the existing lazy Redis client; both are
  graceful no-ops (publish → False, listener → None) when `REDIS_URL` is unset,
  so single-process/dev/tests are unaffected.
- **New event emissions** — `create_notification()` helper
  (`services/notification_service.py`, new) inserts + publishes
  `notification.created` (routes the `ai_monitoring_loop` market-alert path,
  with 5-min per-user dedupe); `market_broadcast_loop` additionally diffs each
  index and publishes `market.index.updated` per changed index (keeps the coarse
  `market_update`).
- **Frontend contract fixes (G6)** (`frontend/src/hooks/useWebSocket.js`) — the
  previously-dropped `ai_alert` + `broker_status`/`portfolio_synced`/
  `broker_order_update`/`broker_price_tick` are now handled and exposed as new
  state (`marketAlerts`, `brokerStatus`, `portfolioSynced`, `brokerOrders`,
  `brokerTicks`). Purely additive; existing consumers untouched. Broker ticks
  are kept separate from the symbol-keyed price store (token-keyed).

Deferred to Sprint R3 (frontend real-time client)

- Single app-level socket provider (G3), Zustand real-time store (G4), GSAP
  price animations (G7), connection-state machine + 30s heartbeat + backoff (G8),
  consuming the new `event` envelope, and retiring the polling timers (G9).

Verification

- `tests/test_event_bridge.py`: 13 hermetic tests (bus `*` matching, channel
  resolution, channel subscribe/unsubscribe/broadcast, dead-socket + disconnect
  cleanup, bridge public-vs-user routing + envelope, Redis no-op without
  `REDIS_URL`, `create_notification` persist+publish + dedupe). No Redis/Mongo/network.
- Full in-process backend suite: 186 passed (the `test_phase*`/`test_backend`
  `requests`-based suites require a running dev server — unchanged, unaffected).
- Frontend `craco build` passes; the hook parses (pre-existing TradeJournal
  eslint warning unrelated).

Notes / follow-ups

- The `event` envelope + channel names (`market`, `sectors`, `scanner`, `news`,
  `notifications`, `portfolio`, `trades`, `ai`, `broker`) are the contract R3
  consumes. Additional per-index/breadth/news emissions land with their feature
  sprints; other notification insert sites can adopt `create_notification`
  incrementally to gain the live push.

---

# Sprint R3 — Frontend Real-Time Client

Status

COMPLETED

Authority

REALTIME_SYSTEM.md (Path A — evolve FastAPI native WebSocket; continues the
Sprint R1 audit / R2 backbone in REALTIME_MIGRATION_PLAN.md)

Objective

Consume the R2 backbone from the frontend: collapse to one socket per user,
introduce a global real-time store, consume the new `event` envelope, add a
real connection state machine with heartbeat + backoff, and retire the polling
timers that now have a push path. GSAP price animations (G7) are intentionally
deferred.

Delivered

- **Zustand global store** (`frontend/src/store/realtimeStore.js`, new) — a
  single store fed by one socket. `applyLegacy(msg)` folds the pre-R2 flat
  message switch (`market_update`, `prices`, `trade_update`, `ai_alert`,
  `broker_*`, …); `applyEvent(envelope)` routes the R2 `{type:"event",…}`
  envelope by domain into slices (`market.index.updated`→price store,
  `notification.created`→unread badge + latest, `portfolio.updated`, scanner/
  news/sector buckets). Narrow selectors so a tick re-renders only the
  components reading that slice (G4).
- **Single socket provider** (`frontend/src/context/RealtimeProvider.jsx`, new)
  — owns the ONE `WebSocket(/api/ws?user_id=)`, mounted in `App.js` inside
  `AuthProvider`. No anonymous socket. On open it subscribes to the app's
  channels (`market`, `sectors`, `scanner`, `news`, `notifications`,
  `portfolio`, `trades`, `ai`, `broker`) and exposes an imperative `send`.
  **Connection state machine** connecting→live→reconnecting→offline (G8),
  **30s heartbeat** (`ping`/`pong`, reconnect on missed pong), **exponential
  backoff** with jitter (1s→30s cap, reset on clean open) (G3, G8).
- **`useWebSocket` → store shim** (`frontend/src/hooks/useWebSocket.js`) — no
  longer opens a socket; returns the same interface (plus `connectionStatus`)
  read from the store, so Dashboard/TradeMonitor/Watchlist work unchanged while
  the app uses exactly one socket (G3).
- **Connection pill** (`frontend/src/components/layout/ConnectionStatus.jsx`,
  new) — Live/Connecting/Reconnecting/Offline, rendered app-wide in `Navbar`
  (replaces the Dashboard-only LIVE/OFFLINE badge).
- **New backend emissions (real data only)** — the always-on heartbeat tasks
  already computed real results but never published; they now emit onto the bus
  (the R2 bridge forwards to channels): `news.received` (scan_news),
  `scanner.breakout` (find_breakouts), `scanner.volume` (check_volume),
  `sector.updated` (sector_rotation), `market.global.updated` (global_markets),
  `market.movers.updated` + `breadth.updated` (sentiment). `market_broadcast_loop`
  additionally publishes `market.engine.status` ~every 30s. All guarded; a
  publish failure never breaks the task.
- **De-poll (G9, fully resolved)** — every poll with a push path is gated on
  `!connected` (fallback only), initial fetch retained: Dashboard core 30s +
  activity 10s, TradeMonitor active-trades 15s, Watchlist 30s, Navbar
  unread-count (badge live via `notification.created`), ActivityTimeline 15s
  (streams `activity_feed`), Markets 30s (indices/sectors/global/movers now
  pushed), MarketEngineStatus 30s (`market.engine.status`), PortfolioMonitor 60s
  (event-triggered refetch on `portfolio_update`).
- **GSAP animations (G7)** — `hooks/usePriceFlash.js` (green flash + scale-up on
  rise, red flash + scale-down on fall) on the Dashboard index cards and
  Watchlist row prices; `components/ui/AnimatedNumber.jsx` (smooth count-up) on
  the Dashboard portfolio value + Today's P&L — the doc's animation spec.

Verification

- `frontend` `yarn add zustand` + `CI=false yarn build` (craco) passes clean;
  only pre-existing eslint warnings remain (RankingTable/Settings/TradeJournal,
  untouched). Bundle +2.6 kB gz total.
- Backend: `python3 -m py_compile services/heartbeat_engine.py server.py` OK
  (deps not installed in the dev session, so the pytest suite was not run here).
- Manual (needs a running backend+frontend): one `/api/ws` connection across
  pages in DevTools; pill Live→(kill backend)→Reconnecting→Offline→(restart)→
  Live; polls silent while live; index cards flash on tick; portfolio value
  counts up; notification badge increments on a `notification.created` push.

Notes / follow-ups

- The R2 `event` envelope + channel names are consumed verbatim from
  `services/realtime/event_bridge.py`; new emissions route via the existing
  `DOMAIN_CHANNEL` map with no bridge change.

---

# Sprint R4 — Scanner Live Migration

Status

COMPLETED

Authority

REALTIME_SYSTEM.md (Path A — continues the R1 audit / R2 backbone / R3 client
in REALTIME_MIGRATION_PLAN.md; closes the audit's §4.5 Scanner gap)

Objective

Convert the scanner from fetch-only to a continuous, push-driven surface:
a continuous scanner worker emitting live breakout / volume-spike / momentum
events, auto-refresh of the results table via events, and animated card
insertion per the doc's scanner-card spec.

Delivered

- **Scanner worker** (`backend/services/market_engine/scanner_worker.py`, new)
  — pure novelty/detection layer for the continuous scanner:
  `filter_novel()` keeps a per-(kind, symbol) 30-min cooldown so every
  published `scanner.*` hit is a NEW opportunity (continuous rescans no longer
  flood the feed with repeats); `detect_momentum()`/`momentum_pass()` compare
  each cycle's day-change against the previous cycle's snapshot, so
  `scanner.momentum` fires only for stocks ≥2% up that are new or still
  accelerating (≥0.3% since last cycle). Process-local state; `reset_state()`
  for tests.
- **Heartbeat scanner tasks** (`services/heartbeat_engine.py`) — the existing
  breakout/volume tasks now gate their publishes through `filter_novel`;
  `scanner.volume` **renamed to `scanner.volume_spike`** (doc-aligned; single
  producer, no name-specific consumer). New `task_scan_momentum` (150s)
  publishes `scanner.momentum`. New `task_scanner_sweep` (180s) re-runs the
  preset scanners (2 per tick, rotating through all 8; reuses the 30s-cached
  universe quotes) and emits ONE worker-tagged `scanner.updated` — the
  frontend's auto-refresh signal.
- **Loop-safe refresh contract** (`services/market_engine/scanner_engine.py`)
  — `scan()` gains `source="api"` / `publish=True`; the REST scan's
  `scanner.updated` now carries `source:"api"` and the frontend refetches ONLY
  on `source:"worker"`, so an API scan can never trigger a refetch loop.
  Scanner events documented in `event_bus.py`'s contract docstring.
- **Store** (`frontend/src/store/realtimeStore.js`) — scanner slice is now
  event-aware: hit events become feed entries `{id, kind, event, candidates,
  count, timestamp}` (cap 50) and bump `scannerRefreshedAt`; worker-origin
  `scanner.updated` bumps `scannerRefreshedAt` only. New selectors
  `selectScannerFeed`, `selectScannerRefreshedAt`.
- **Live feed UI** (`components/market/ScannerLiveFeed.jsx`, new) — pure
  push-driven hit feed (no fetching) beside the scanner on the Markets Scanner
  tab (320px rail, stacks on mobile): kind badges (Breakout / Volume Spike /
  Momentum), top-3 candidates with price/change/vol-ratio, relative time,
  live/offline dot, honest empty + offline states.
- **Card entrance animation** (`hooks/useCardEntrance.js`, new) — the doc's
  scanner-card spec (Slide Right → Fade → Glow → Settle) as a GSAP mount
  animation mirroring `usePriceFlash`; only newly inserted cards animate.
- **Auto-refresh via events** (`components/market/MarketScanner.jsx`) — the
  results table refetches silently (no spinner flicker) ~1.5s-debounced on
  `scannerRefreshedAt`, shows a "Live · updated hh:mm:ss" caption, and falls
  back to a 60s poll ONLY while the socket is down (R3 gating pattern);
  initial fetch retained.

Verification

- `tests/test_scanner_worker.py` (new): 11 hermetic tests — novelty cooldown
  (first-pass/repeat/expiry/pruning/kind-independence), momentum
  threshold+acceleration semantics, heartbeat task contracts via bus spies
  (momentum publishes once then dedupes; volume task emits
  `scanner.volume_spike` and never the old name; breakout dedupes; sweep emits
  exactly one worker-tagged `scanner.updated`), `scan(publish=False)` silent +
  `source:"api"` tagging. All fetchers monkeypatched — no network.
- `tests/test_event_bridge.py` extended: `scanner.breakout` /
  `scanner.volume_spike` / `scanner.momentum` → `scanner` channel mapping.
- Full hermetic backend suite: 197 passed (the `test_phase*`/`test_backend`
  `requests`-based suites require a running dev server — unchanged, unaffected).
- Frontend `CI=false yarn build` (craco) passes clean (+1.4 kB gz); only the
  pre-existing TradeJournal eslint warning remains.
- Manual (needs running backend+frontend): Markets → Scanner tab; hit cards
  slide in within ~2–3 min of heartbeat cycles; table refreshes silently after
  sweeps; preset-button REST scans do NOT trigger refetch (source gating);
  no `/market/scanner` polling while live; offline falls back to 60s poll.

Notes / follow-ups

- Hit events are broadcast on the `scanner` channel (public market data);
  per-user notification fan-out (`create_notification`) and AI auto-analysis
  of hits are the doc's next steps for the scanner flow — deferred.
- The dedupe cooldown is process-local; if the backend is ever scaled to
  multiple worker processes, only one process should run the heartbeat engine
  (already the deployment model) or the cooldown moves to Redis.

---

# Sprint R5 — Portfolio Live Migration

Status

COMPLETED

Authority

REALTIME_SYSTEM.md (Path A — continues R1 audit / R2 backbone / R3 client /
R4 scanner in REALTIME_MIGRATION_PLAN.md; closes the audit's §4.8 Portfolio gap)

Objective

Convert the portfolio from fetch-on-mount to a live, push-driven surface:
streaming P&L, live allocation updates, broker-tick-driven recomputes,
animated counters, live charts, and event-triggered AI portfolio refresh —
per the doc's *Broker WebSocket → Portfolio Service → Socket → PnL Updated →
Number Animation → Allocation Chart Updates* flow.

Delivered

- **Live portfolio stream** (`backend/services/portfolio_stream.py`, new) —
  server-side recompute layer: builds a per-user snapshot through
  `portfolio_engine` (never re-implements portfolio math) and publishes a
  per-user `portfolio.updated` bus event (`data.user_id` → the R2 bridge
  delivers only to that user's sockets, `portfolio` channel). Payload:
  `{pnl, allocation, holdings (light marks), open_positions, reason}` +
  legacy-compat flat `total_pnl`/`total_unrealized_pnl`. Light quotes map
  (cached 2d Yahoo + factual sector metadata) — marks, not indicators.
- **Heartbeat producer** (`services/heartbeat_engine.py`) —
  `task_monitor_portfolio` (90s) upgraded from "manual open trades only" to
  the FULL broker+manual portfolio for every user with positions, using one
  shared quote prefetch per cycle; emits `portfolio.updated`
  (`reason:"monitor"`) via the bus instead of the legacy `portfolio_update`
  send (the store maps the event into the same consumer state).
- **Broker streaming → live P&L** (`services/broker_engine.py`) — official
  broker ticks now drive the portfolio: `_on_stream_tick` maps
  `instrument_token → symbol` via the user's synced `db.holdings`, persists
  fresh `last_price`/`market_value` marks (REST reads stay live between Yahoo
  refreshes), and publishes a throttled (3s/user, process-local, tick prices
  supersede cached quotes) `reason:"broker_tick"` snapshot. `sync_portfolio`
  additionally publishes the doc's `portfolio.synced` event + a
  `reason:"broker_sync"` snapshot. All best-effort — a recompute error never
  breaks the raw tick forward. Events documented in `event_bus.py`.
- **Store** (`frontend/src/store/realtimeStore.js`) — `portfolio.updated` →
  new `portfolioLive` slice (full snapshot + `updatedAt`) while still feeding
  `portfolioUpdate` for legacy consumers (Dashboard card, PortfolioMonitor);
  `portfolio.synced` → `portfolioSynced`. New selectors `selectPortfolioLive`,
  `selectPortfolioSynced`.
- **Portfolio page live migration** (`pages/Portfolio.jsx`) — live snapshot
  overlays the intelligence bundle (marks only; analytics stay server-computed):
  value strip uses GSAP `AnimatedNumber` count-up + `usePriceFlash` green/red
  flash on total value and P&L, with a pulsing LIVE badge showing snapshot
  time; holdings rows extracted into `HoldingRow` (per-row price flash — only
  the affected row updates); allocation pie + sector bars read the live
  allocation; equity curve appends a streaming "Live" point so the chart moves
  intraday; **AI portfolio refresh**: silent intelligence refetch triggered by
  `portfolio.synced` (immediate, deduped) and `portfolio.updated` (≥60s
  apart) — event-triggered, never a timer; no polling added (page stays
  fetch-on-mount + events, manual refresh retained).
- **Dashboard** (`pages/Dashboard.jsx`) — portfolio summary card now also
  merges live `current_value`/`invested` from R5 snapshots (existing
  AnimatedNumber animates them).

Verification

- `tests/test_portfolio_stream.py` (new): 8 hermetic tests — snapshot payload
  shape + legacy-compat fields, per-user `portfolio.updated` publish,
  no-holdings silence, tick price override, tick mark persistence, tick
  throttle + `reset_state` re-arm, unmatched-token silence, heartbeat task
  contract (broker + manual users each get one `reason:"monitor"` event with
  shared-prefetch P&L). Bus spied in-process; quotes injected — no network.
- `tests/test_event_bridge.py` extended: `portfolio.updated` /
  `portfolio.synced` → `portfolio` channel mapping.
- Full hermetic backend suite: 205 passed (197 pre-R5 + 8 new; the
  `test_phase*`/`test_backend` `requests`-based suites still require a running
  dev server — unchanged).
- Frontend `CI=false yarn build` (craco) passes clean (+0.7 kB gz); only the
  pre-existing TradeJournal eslint warning remains.
- Manual (needs running backend+frontend): Portfolio page shows LIVE badge;
  value/P&L count up and flash on heartbeat cycles (~90s); with a connected
  Zerodha session ticks move rows/value within seconds (3s throttle);
  allocation pie/bars follow; Performance tab shows the moving "Live" point;
  broker sync refreshes the AI intelligence bundle silently.

Notes / follow-ups

- The tick throttle is process-local (same trade-off as scanner_worker's
  cooldown; single-heartbeat deployment model). Multi-process scale moves it
  to Redis.
- Realized P&L is intentionally absent from live snapshots — it changes only
  when a trade closes and stays owned by the REST intelligence bundle.
- Zerodha funds card on the Portfolio page is still fetch-on-mount; a
  `funds.updated` broker event is the natural next step.

---

# Sprint R7 — AI Live Activity

Status

COMPLETED

Authority

REALTIME_SYSTEM.md (§ "AI Thinking Process" / "AI Activity Timeline" — "Never
fake AI progress. Always display actual AI workflow.")

Objective

Convert the AI chat from a static "Thinking…" indicator into an event-driven,
per-request step timeline — the doc's *Collecting Market Data → … → Completed*
flow, but with truthful labels that map to the work the chat pipeline actually
performs. Steps stream live over the existing event bus → WebSocket bridge and
update only the assistant bubble (no page rerender, no polling).

Delivered

- **Reusable step emitter** (`backend/services/ai_activity.py`, new) — `AIRun`
  publishes a run-correlated, per-user timeline on the `ai` domain:
  `ai.run.started` (full step plan), `ai.step` (index + `running`/`done`/
  `warning`), `ai.run.completed`. Every event carries `user_id` so the R2
  bridge delivers only to that user's sockets, and a `run_id` so the client
  correlates a step burst to the request that fired it. `step()` is an async
  context manager (running on enter; done/warning on exit) and all emission is
  best-effort — a bus/socket failure can never break the AI reply.
- **Chat pipeline instrumented** (`server.py` → `ai_chat`) — genuine stages
  wrapped as steps: *Recalling your context* (AI memory load), *Reviewing our
  conversation* (session history), and a model step whose label follows the
  provider that actually serves — *Consulting Claude* / *Consulting Gemini* /
  *Composing your answer* — via `ModelRouter.resolve_provider()` (the existing
  history-less fallback is nested inside that step). `run_id` added to
  `ChatMessage` (`models.py`) and threaded through `chat_endpoint`.
- **Store** (`frontend/src/store/realtimeStore.js`) — new `aiRun` slice; the
  `ai` domain now branches: `ai.run.started`/`ai.step`/`ai.run.completed` drive
  `aiRun` (stale-run guard by `runId`), all other `ai.*` still feed the
  background `activityUpdates` feed. New selector `selectAIRun`.
- **Live UI** (`frontend/src/components/ai/AIStepTimeline.jsx`, new) — renders
  the correlated run's steps with per-status icons (pending/running-spinner/
  done-check/warning) and a framer-motion staggered entrance; falls back to the
  original three-dot pulse when no matching live run exists yet (first paint or
  socket offline). Wired into `pages/AIAssistant.jsx` (replaces the inline dots
  while `sending`); `hooks/useAIWorkspace.js` generates a `run_id` per send,
  posts it to `/chat`, and exposes `activeRunId`.

Verification

- Backend `python3 -c "ast.parse(...)"` clean on all changed modules; frontend
  babel (react-app preset) parses all four changed files clean.
- Manual (needs running backend+frontend): open AI Workspace → send a message →
  the assistant bubble shows the three stages advancing live (spinner → check)
  instead of static dots, then the answer replaces the timeline. With the socket
  offline the bubble falls back to the pulsing dots (no regression).

Notes / follow-ups

- Chat runs three truthful stages (memory/history/model). Multi-tool pipelines
  (Morning Report, Trade/Portfolio review) adopted the same `AIRun` emitter in
  Sprint R7 Phase 2 (below).
- `run_id` correlation is client-generated per send. Since Phase 2 the store
  keys runs by `runId`, so concurrent timelines coexist.

---

# Sprint R7 Phase 2 — AI Live Activity: Full-Surface Rollout

Status

COMPLETED (2026-07-16)

Authority

REALTIME_SYSTEM.md (§ "AI Thinking Process" / "AI Activity Timeline" / "Morning
Report Flow" — "Never fake AI progress. All steps visible.")

Objective

Extend the R7 `AIRun` live-step system beyond chat to every AI surface: the
Morning Report page shows the doc's *Collecting Market Data → Reading News →
Scanning NSE → … → Completed* pipeline live; Portfolio/Trade review panels show
their real stages; the 8:30 IST scheduler run broadcasts to every dashboard.

Delivered — backend

- **Morning report instrumented** (`server.py` → new `generate_morning_report`,
  extracted from `morning_report_full`) — six truthful steps
  (`MORNING_REPORT_STEPS`): *Collecting Market Data* (`real_overview`),
  *Reading News* (NEW real phase — `news_service.get_market_sentiment()`, cached
  RSS; result persisted as `report["news_sentiment"]` + a key-risks line; feed
  failure → step `warning`, report still generated), *Scanning NSE* (top picks),
  *Analyzing Sector Flows* (FII/DII + sectors + mood), *Generating Report*
  (AI briefing), *Saving Report* (Mongo). Endpoint accepts `?run_id=` for
  correlation. Cache hits return before the run starts — zero events. Overview
  unavailable → run completes `warning`.
- **Portfolio review** (`POST /api/ai/portfolio-review`) — optional
  `PortfolioReviewRequest{run_id}` body (backward compatible); 3-step run:
  *Reading your holdings → Scanning portfolio health → Consulting the AI*.
- **Trade review** (`POST /api/ai/trade-review`) — `run_id` on
  `TradeReviewRequest`; steps *Reviewing execution with AI* (+ *Saving review*
  only when a `trade_id` save actually happens); cached reviews emit nothing.
- **Scheduler broadcast** (`services/scheduler.py` → `morning_analysis_job`) —
  wrapped in a `user_id=None` AIRun (*Scanning NSE → Generating Morning Report →
  Notifying Traders*); the bridge broadcasts null-user events on the `ai`
  channel, so every connected dashboard watches the 8:30 pipeline live. The
  redundant `log_activity("Scanning NSE top gainers")` line was removed.

Delivered — frontend

- **Keyed run store** (`realtimeStore.js`) — single `aiRun` slot replaced by
  `aiRuns` map keyed by `runId` (+ `aiRunOrder`, pruning oldest *completed*
  runs beyond 6). Only the patched run's object identity changes. Broadcast
  runs' `ai.step` events also mirror into `activityUpdates` (legacy
  activity-feed shape) so the dashboard AI Activity timeline shows scheduler
  runs. New actions: `resolveAIRun(runId, status)` (REST-settle reconciliation:
  no step may stay stuck "running" after the request resolves/fails) and
  `clearAIRun(runId)`. `selectAIRun` replaced by factory `selectAIRunById`.
- **`AIPipelineProgress.jsx`** (new) — page-level pipeline card: progress bar,
  animated "X of N" counter, step rows (shared `StepIcon`), GSAP entrance,
  `fallback` prop (shown until `ai.run.started` arrives — covers cache hits)
  and a `staleMs` guard (active run silent >45s degrades to fallback).
- **Surfaces wired** — `MorningReport.jsx` sends `run_id` and replaces loading
  skeletons with `AIPipelineProgress`; `PortfolioReviewPanel.jsx` /
  `TradeReviewPanel.jsx` send `run_id` and replace skeleton bars with the
  compact `AIStepTimeline`; `useAIWorkspace.js` (chat) now resolves + clears
  its run on settle. All surfaces call `resolveAIRun`/`clearAIRun` in
  catch/finally.

Tests

- `backend/tests/test_ai_live_activity.py` (new, 6 tests, hermetic): ordered
  started→running/done→completed events with correct labels/indices; `run_id`
  passthrough over HTTP; cache hit emits zero events; failing fetch → step +
  run `warning`; unavailable overview → run `warning`; scheduler job events all
  carry `user_id: None`. Full hermetic suite: 210 passed (1 pre-existing
  `test_trading_engine.py` failure unrelated to R7, fails on clean tree too).

Follow-ups

- [ ] Migrate `heartbeat_engine` tasks off `activity_logger` onto AIRun
      broadcast runs (single AI-activity system; retire `activity_feed`).
- [ ] Extract `generate_morning_report` (and siblings) out of the `server.py`
      monolith into a service module once its `db`/provider globals are
      injectable.

---

# Sprint R7.5 — AI Context Engine & Real-Time Audit

Status

COMPLETED

Authority

REALTIME_SYSTEM.md (source of truth) + AI_AGENT_SYSTEM.md ("Agents never
guess"; the AI must reason from the Market Engine, never from model memory).

Objective

Audit the full real-time pipeline and fix the core AI defect: the chat
assistant answered like a general-purpose LLM ("I don't have access to live
market data") because the chat request injected only memory + conversation
history — never live platform data. Combined with the Master Prompt's
anti-fabrication rule, the model correctly but uselessly disclaimed live access.

Audit findings

- **Delivery layer is healthy** — event_bus → event_bridge → WebSocket is
  event-driven and <100ms; one socket/user, heartbeat, reconnect, GSAP flashes
  all work. `ai_activity`, `trade_stream`, `trade_review`, `AIStepTimeline` are
  fully wired end-to-end (not stubs).
- **Root cause of the AI bug** — `server.ai_chat()` fed only `{memory}` + last 10
  turns; the `ai_chat` prompt had no live-data slot. Fixed this sprint.
- **"Feels static" root cause** — the free Yahoo data SOURCE is polled on
  30–180s cycles (indices 10–15s, trades 60s, portfolio 90s, scanner 120–180s).
  This is a data-source limitation, not a pipeline bug; true sub-second ticks
  need a broker websocket feed. Documented; cadence tuning deferred (see below).

Delivered

- **AI Context Builder** (`backend/services/ai_context_builder.py`, new) —
  `build_chat_context(db, user, quotes_map_func)` assembles one compact, live,
  token-budgeted (~500 tok) markdown snapshot before every chat request:
  market snapshot (NIFTY/Bank Nifty/Sensex/VIX/breadth/sentiment/status),
  gainers/losers, sectors, global markets, portfolio + P&L + risk, open trades,
  watchlist, latest news + sentiment, broker status, user memory, recent
  platform AI activity. Composes existing services only (`real_market`,
  `portfolio_engine`, `news_service`, `ai_memory`, `activity_logger`) — no new
  data source. Fully best-effort (per-section isolation), concurrent under one
  4s budget, and per-user micro-cached (8s). `quotes_map_func` is injected
  (`server.real_quotes_map`) to avoid a circular import, matching
  `portfolio_engine`/`portfolio_stream`.
- **Prompt Builder update** (`services/prompt_library.py`, `ai_chat` → 1.1.0) —
  new `{live_context}` slot; the AI is told the context is live ground truth,
  must NEVER mention knowledge cutoff / training data / "an AI model" / inability
  to access live data, and — only when the market feed is truly unavailable —
  must reply exactly "The live market feed is temporarily unavailable. Please
  try again in a few moments."
- **Chat pipeline wired** (`server.ai_chat`) — builds context as the first
  timeline step ("Reading live market data") and renders it into the prompt;
  best-effort so an empty context still yields a valid reply. No API-contract or
  frontend change.

Verification

- Import/AST clean on all changed modules (project venv). `ai_chat` renders to
  v1.1.0 with the live_context slot and the exact fallback sentence.
- Async smoke test of `build_chat_context` with stub db + quotes: live Yahoo
  overview fetched, portfolio/open-trades/watchlist/broker/news sections
  rendered, ~475 tokens, micro-cache returns the same object, and a forced
  section failure (missing memory collection) degraded gracefully without
  breaking the build.

Notes / follow-ups (out of scope this sprint)

- Real-time cadence tuning (tighter heartbeat/broadcast for indices/watchlist/
  portfolio) — needs a broker websocket feed for true Zerodha/TradingView-grade
  sub-second ticks.
- Proactive AI-model-generated alerts (trade opportunities, exit signals) — the
  monitoring scaffolding already exists in `heartbeat_engine.py`.
- Chat response streaming (SSE/WebSocket).
- The other agent endpoints (advisor, portfolio-review) already inject their own
  live data; they can adopt `build_chat_context` for a unified snapshot later.

---

# Sprint R8 — Notifications & Watchlist Live Migration

Status

COMPLETED

Authority

REALTIME_SYSTEM.md (source of truth for all real-time behavior); closes
migration-plan gaps §4.6 (Breaking News), §4.10 (Watchlist), §4.11
(Notifications), §4.13 (Morning Report).

Objective

Make every alerting surface push-driven: notifications toast in live and the
badge increments without polling; the watchlist streams price + RSI + volume
ratio per row; breaking headlines interrupt with a live toast; the morning
report announces itself the moment the 8:30 pipeline finishes.

Delivered — Backend

- [x] Breaking-news pipeline: `news_service` now classifies every article's
  `importance`/`is_breaking` deterministically (`_BREAKING_TERMS`) and
  `filter_breaking_novel()` cooldown-gates headlines (2h, process-local,
  mirrors `scanner_worker.filter_novel`); heartbeat `task_scan_news` publishes
  `news.breaking {articles, count}` for novel breaking items only.
- [x] Notification unification: every remaining direct
  `db.notifications.insert_one` migrated to
  `notification_service.create_notification` so ALL alerts push
  `notification.created` live — morning report, exit reminder, EOD report
  (scheduler), portfolio-monitor AI alerts, TRADE_ENTRY (manual + Zerodha),
  EMERGENCY_STOP, monitor/run alerts, WEEKLY_REVIEW (server.py). The helper's
  single insert is now the only write path.
- [x] Watchlist stream: new heartbeat `task_watchlist_stream` (120s) enriches
  every watchlisted symbol (RSI, volume ratio via `fetch_real_stock_quote`)
  and broadcasts `watchlist.quotes`; watchlist add/remove REST endpoints
  publish per-user `watchlist.updated {action, symbol}` for cross-surface
  sync. Bridge maps `watchlist.*` → `watchlist` channel.
- [x] Morning report ready-signal: `morning_analysis_job` publishes broadcast
  `morningreport.generated {date, picks}` after the report is saved; bridge
  maps the domain onto the `ai` channel every dashboard already subscribes to.

Delivered — Frontend

- [x] Store: `news.breaking` (prepend + `breakingNews` slice),
  `watchlist.quotes` (folded into the shared `priceTicks` store),
  `watchlist.updated` (`watchlistEvent` slice), `morningreport.generated`
  (`morningReportReadyAt`), `decrementUnread`; new selectors (`selectNews`,
  `selectBreakingNews`, `selectLatestNotification`, `selectWatchlistEvent`,
  `selectMorningReportReadyAt`); provider subscribes to the `watchlist`
  channel.
- [x] `NotificationToast.jsx` (new, mounted in Layout): global toast host —
  notification.created and breaking-news pushes slide down (severity-styled,
  auto-dismiss, click-through to the owning surface, max 3 stacked).
- [x] NotificationPanel: live prepend while open; mark-read/mark-all-read now
  sync the store badge (`decrementUnread` / `markNotificationsRead`).
- [x] Watchlist page: rows patch price, change, RSI, volume ratio AND
  since-added P&L from the live tick store; add/remove in another tab syncs
  via `watchlist.updated`; 30s poll remains disconnected-only.
- [x] News page: streamed headlines merge in live (breaking first, deduped by
  title) with a LIVE badge; BREAKING article badge now driven by real data.
- [x] Dashboard: news, notifications and watchlist widgets all patch from the
  store (no new fetches while connected); morning-report card refetches on the
  ready-signal.
- [x] Morning Report page: auto-refreshes in place when
  `morningreport.generated` arrives.

Verification

- `tests/test_sprint_r8.py` (9 tests: importance classification, breaking
  novelty gate incl. cooldown expiry + untitled skip, bridge routing for
  watchlist/morningreport/news.breaking) — all pass alongside the existing
  event-bridge/scanner/portfolio/AI suites (210+ passing; only pre-existing
  live-server integration tests and one pre-existing trading-engine assertion
  fail, unrelated to R8).
- Frontend production build compiles clean (craco build).

---

# Sprint R9 — Performance Optimization

Status

COMPLETED

Authority

REALTIME_SYSTEM.md (§ "Performance Rules" — batch updates, virtualize long
lists, lazy load, memoize, only changed components rerender) + the
performance skill targets (fast initial load, minimal re-renders, efficient
Redis usage).

Objective

Land the doc's performance rules as concrete infrastructure: batch the event
firehose on the client, stop no-op re-renders at the store, window long
lists, split the bundle per route, and cut Redis round-trips + per-socket
serialization on the backend.

Delivered — Frontend

- [x] Event batching (`context/RealtimeProvider.jsx`) — inbound socket
  messages queue for a 40ms window (setTimeout, so batching survives
  background tabs) and apply as one burst via the store's new
  `applyMessages`; `pong` bypasses the batch. A heartbeat cycle that emits a
  dozen events now produces one coalesced store update.
- [x] Store coalescing + selective rendering (`store/realtimeStore.js`) —
  `applyMessages` folds every price-bearing message in a burst (`prices`,
  `price_tick`, `market.index.updated`, `watchlist.quotes`) into ONE
  `_mergePrices` write. `_mergePrices` now MERGES per symbol — fixing a real
  bug where the 15s price stream replaced tick objects and wiped the
  RSI/volume_ratio fields the 120s watchlist stream had added — preserves
  object identity on no-op ticks, and skips the write entirely when nothing
  moved. New factory selector `selectTickForSymbol`.
- [x] Memoization — `WatchlistRow` is `memo`ized and subscribes to its own
  symbol tick (a burst re-renders only rows whose symbols moved; the page no
  longer clones the whole list per tick); News page gains a memoized
  `ArticleCard` (stable title+published keys, was index-keyed), `useMemo`d
  filtering and source counts; Dashboard's index/watchlist tick-patch effects
  keep previous state identity on no-op ticks.
- [x] Virtualization (`hooks/useVirtualList.js`, new) — dependency-free list
  windowing (scroll-window + spacer paddings, row height corrected from the
  first rendered row). Watchlist windows itself beyond 60 rows; smaller lists
  keep the staggered entrance animation.
- [x] Lazy loading (`App.js`, `components/layout/Layout.jsx`) — all 21 routed
  pages converted to `React.lazy` route-level code splitting; outer Suspense
  in App.js for public pages, nested Suspense around Layout's `<Outlet/>` so
  in-app navigation suspends only the content region (sidebar/navbar/toast
  host never unmount). Main bundle drops to ~160 kB gz with page chunks
  (86/55/27 kB…) loading on demand.

Delivered — Backend (Redis optimization)

- [x] `services/cache.py` — `cache_get_many` (one MGET round-trip) and
  `cache_set_many` (one non-transactional pipeline); the in-memory fallback
  store is now bounded (1024 keys: expired-entry sweep first, oldest-written
  eviction only if still over) instead of growing forever.
- [x] `services/real_market.fetch_all_universe_quotes` — when the 30s bundle
  key misses, every per-symbol quote key is warmed in ONE `cache_get_many`
  call (~50 sequential Redis GETs → 1 MGET); only true misses fall through to
  the Yahoo fetch.
- [x] `server.py` `ConnectionManager` — every fan-out path (`broadcast`,
  `broadcast_to_channel`, `send_to_user`) serializes the message ONCE and
  sends text; previously `ws.send_json` re-ran `json.dumps` per socket.
  `send_to_user` also short-circuits when the user has no sockets.

Verification

- `tests/test_sprint_r9.py` (new, 7 hermetic tests): get_many/set_many
  roundtrip + expired/missing key omission + empty-input safety, memory bound
  under overflow, expired-sweep-before-eviction, universe warm-hit skips the
  HTTP fetch (metadata still applied), broadcast sends one identical
  pre-serialized payload to every subscribed socket + reaps dead sockets,
  datetime-safe serialization. `test_event_bridge.py`'s FakeWS extended with
  `send_text`.
- Full hermetic backend suite: 227 passed (the single `test_trading_engine`
  failure is pre-existing — fails on the clean tree too, noted since R7).
- Frontend `CI=false yarn build` (craco) compiles clean; only the 3
  pre-existing eslint warnings in untouched files remain. Bundle is now
  route-split (main ~160 kB gz + ~30 on-demand chunks).

Notes / follow-ups

- News list stays memoized-but-unvirtualized (100-article cap, variable card
  heights); revisit with `useVirtualList` if the cap ever grows.
- The batching window is client-side only; backend producers already batch
  (15s `prices` map, 120s `watchlist.quotes`). If per-tick broker feeds ever
  broadcast publicly, add server-side coalescing at the bridge.

---

# Sprint 10 — Morning Report

Status

COMPLETED

Authority

AI_AGENT_SYSTEM.md (§9 "Morning Report Agent"), MARKET_ENGINE.md (§ "Morning
Report Builder"), PROMPT.md (§ "Morning Report Prompt"), DATABASE.md,
MARKET_DATA_ARCHITECTURE.md (all market reads via the Market Gateway).

Objective

Complete the automated Morning Report: every market day before the open,
covering Global Markets, Gift Nifty, News, Economic Calendar, Scanner, Top
Picks, Risk Warnings and Portfolio Alerts, with notifications sent
automatically.

Starting state — the report existed (mood, indices, picks, key risks, AI
briefing, FII/DII) but was missing most of the doc's sections, and its
"Global Cues" line was a **hardcoded sentence** asserting the same claim
every morning regardless of what global markets did.

Architecture — two layers

The report is now split, because its halves have different identities and
lifetimes. The shared **market layer** (global markets, Gift Nifty, news,
calendar, scanner, picks, risks) costs tens of provider calls, so it is built
once per day and persisted to `db.reports`. The per-user **personal layer**
(portfolio alerts) is computed fresh on every request and never written into
the shared document — a correctness requirement, not an optimization: the
shared doc is cached by *date alone*, so any per-user field stored in it
would be served to the next user who asked. Merged at read time by
`get_morning_report()`.

Delivered — Backend

- [x] `services/morning_report.py` (new) — owns the whole pipeline; the 8:30
  job and the on-demand API route both call it, so a scheduled briefing and a
  user-requested one can never drift apart. Extracted ~130 lines of business
  logic out of `server.py` (routes now only wire).
- [x] Global Markets — real, via `market_gateway.get_global_markets()`.
  Replaces the fabricated "US futures and Asian markets influencing early
  Indian session…" string with a summary derived from the actual quotes
  ("Overnight global cues are broadly positive — 4 of 6 tracked indices closed
  higher. Hang Seng +2.80% led; Nikkei 225 -2.79% lagged."). `global_cues` is
  retained as a field for the Dashboard card and existing API consumers, now
  carrying that real text.
- [x] Gift Nifty — `services/market_engine/gift_nifty.py` (new): a collector
  behind the gateway with a priority-ordered adapter chain, normalization,
  validation and caching. No free feed carries Gift Nifty (probed Yahoo, NSE
  IX, Alpha Vantage; it is an NSE IX instrument needing a data subscription),
  so it ships reporting `available: false` with the reason the UI shows
  verbatim — never a derived guess. A licensed feed registers one adapter and
  every consumer picks it up unchanged.
- [x] News — top headlines (high-importance first, then newest) alongside the
  existing sentiment score.
- [x] Economic Calendar — today's events + nearest high-importance ones, via
  the previously-unused `economic_calendar.get_calendar()`.
- [x] Risk Warnings — now also fold in today's high-importance calendar events
  and an indicated Gift Nifty gap; every line still names its evidence.
- [x] Portfolio Alerts — cross-references holdings against this morning's
  market state (risk factors from the portfolio engine, holdings in weak
  sectors, headlines naming a stock the user owns, critical monitor flags).
  Every alert carries a `why`.
- [x] Prompt — the AI briefing now loads from `prompt_library` (`morning_report`)
  instead of a hardcoded string, per PROMPT.md ("never hardcode prompts").
- [x] Notifications — **two bugs fixed**: the job checked the `trade_alerts`
  preference instead of the `morning_report` preference `models.py` defines,
  and only swept `db.trades.distinct("user_id")` — so a user who subscribed to
  the morning report but had never placed a trade was silently never notified.
  Now honors `morning_report`, reaches every subscriber, dedupes (180 min), and
  sends the branded HTML email via `build_morning_report_email`.
- [x] Honest degradation — `services/ai_activity.py` gained `step.warn()`:
  a section that fails and degrades marks its step `warning` and the run
  completes `warning`, instead of reporting `done` for work that didn't
  succeed. A dead scanner now costs the picks section, not the whole briefing.

Delivered — Frontend

- [x] `components/morning/` (new) — `GiftNiftyCard`, `GlobalMarketsCard`,
  `NewsHeadlines`, `EconomicCalendarCard`, `PortfolioAlertsCard`, and a shared
  `SectionUnavailable` that renders the backend's own reason verbatim.
- [x] `pages/MorningReport.jsx` — composes the new sections; portfolio alerts
  lead (the payoff), then overnight (global + Gift Nifty + FII/DII), then news
  + calendar, then risks.
- [x] Unavailable states are honest end-to-end — the FII/DII card previously
  rendered `₹0 Cr` when the value was missing ("institutions were flat" is a
  materially different claim from "NSE hasn't published yet"); it and
  `IndexCard` now say so.

Verification

- `tests/test_sprint10_morning_report.py` (new, 20 hermetic tests): every
  section present; global summary reflects real quotes and degrades honestly;
  headlines rank high-importance first; risks grounded in the collected VIX /
  FII numbers; unavailable inputs reported not invented; Gift Nifty
  unavailable without a feed, uses a registered adapter, falls through a
  failing one, rejects a nonsense quote, and surfaces a gap as a risk;
  portfolio alerts connect market events to holdings, are severity-ordered,
  carry reasoning, degrade alone, and **never enter the shared cache**;
  notifications honor the preference and reach a never-traded user.
- `tests/test_ai_live_activity.py` updated to the service and the new
  contracts (a cache hit must still not fake *market-layer* progress; a failed
  section degrades honestly rather than failing the report).
- `tests/_fakedb.py` — `_Cursor.__aiter__` added so the double speaks the
  Motor cursor protocol the notification sweep uses (`async for` instead of
  loading every user into memory).
- Full hermetic backend suite: 247 passed, up from 227. The 48 failures are
  pre-existing and identical on a clean tree (verified by stashing) — they
  need a live server on :8000.
- Driven end-to-end against live providers: real global markets, headlines,
  calendar and picks populate; Gift Nifty reports unavailable; the personal
  layer does not leak into the shared document.
- Frontend `CI=false npx craco build` compiles clean; no new warnings.

Notes / follow-ups

- **Claude is 404ing platform-wide** (out of this sprint's scope, filed under
  Technical Debt): `services/claude_provider.py` pins
  `claude-3-haiku-20240307` (retired 2026-04-19) and
  `services/ai_debate_engine.py` pins `claude-3-5-sonnet-20241022` (retired
  2025-10-28). Every Claude call fails and silently falls back to Gemini —
  including this report, whose prompt prefers Claude.
- Gift Nifty stays unavailable until an NSE IX subscription or licensed vendor
  feed exists; the adapter seam is ready.
- The economic calendar is still the curated static generator (its own Phase 2
  note); the report consumes it through the gateway, so a live source is a
  drop-in.

---

# Technical Debt

Every technical debt item must contain

Description

Reason

Impact

Priority

Target Version

Owner

Status

---

## TD-001 — Claude models are retired; every Claude call 404s

Description

`services/claude_provider.py` pins `claude-3-haiku-20240307` for both
`CLAUDE_DEFAULT_MODEL` and `CLAUDE_FAST_MODEL`; `services/ai_debate_engine.py`
pins `claude-3-5-sonnet-20241022`. Both model IDs have passed their retirement
dates (2026-04-19 and 2025-10-28) and now return HTTP 404 `not_found_error`.

Reason

Model IDs were pinned at build time and never revisited. The failure is silent:
`get_debate_engine()` catches the error and falls through to Gemini, so the
platform stays up and no alarm fires.

Impact

Claude is effectively absent from the product despite being the documented
primary reasoning model (INDEX.md, AI_AGENT_SYSTEM.md → "Claude & Gemini
Collaboration"). Every prompt the library routes with `prefer="claude"` —
morning report, portfolio review, risk analysis, complex reports — is served by
Gemini instead. The AI Debate System cannot produce two viewpoints, because one
participant always errors. Observed live: `Claude provider error: Error code:
404 - model: claude-3-haiku-20240307`.

Fix

Repoint to current models — `claude-opus-4-8` for reasoning-heavy work,
`claude-haiku-4-5` where the fast model is wanted. Note the API surface has
moved on since these IDs: `budget_tokens` is removed in favor of
`thinking={"type": "adaptive"}`, sampling params (`temperature`/`top_p`/`top_k`)
are rejected on Opus 4.7+, and assistant-turn prefills 400. Audit the provider
call sites against those before switching, and add a startup health check so a
retired model surfaces loudly instead of silently degrading.

Priority

High — the platform's primary AI provider is entirely non-functional.

Target Version

1.2

Owner

Unassigned

Status

Open — found during Sprint 10 verification (2026-07-16), not fixed there
because it is platform-wide scope rather than Morning Report scope.

---

## TD-1: Backtest engine uses simulated trades

Description

`services/backtest_engine.py` generates simulated win/loss trades
(`random.seed`/`random.randint`) rather than replaying real historical OHLCV.

Reason

Historical backtesting over the full universe was out of scope for Sprint 2
(which targeted live market data surfaces).

Impact

Backtest results are illustrative, not real historical performance.

Priority

Medium

Target Version

Milestone 3 (Trading)

Owner

Unassigned

Status

OPEN

---

## TD-2: Paper trading is intentionally simulated

Description

Paper-trade fills use live quotes but positions are virtual by design.

Reason

Educational feature — virtual money, not a data-integrity issue.

Impact

None (expected behavior).

Priority

Low

Target Version

N/A

Owner

Unassigned

Status

ACCEPTED

---

# Bugs

Fields

Title

Description

Severity

Priority

Status

Assigned To

Target Release

---

# Feature Requests

Fields

Feature

Reason

Priority

Status

Estimated Version

---

# Release Checklist

Before every release

□ Documentation Updated

□ Tests Passing

□ Security Reviewed

□ Performance Verified

□ Accessibility Verified

□ Mobile Tested

□ AI Tested

□ Broker Tested

□ Payments Tested

□ Monitoring Enabled

□ Backup Verified

□ Deployment Successful

---

# Current Sprint

Sprint Goal

Build a production-ready AI-powered stock analysis platform using real market data and broker integrations.

Current Priorities

1. Complete frontend

2. Connect backend APIs

3. Replace mock data

4. Broker integration

5. AI integration

6. Testing

7. Deployment

---

# Current Focus

This section should always contain the next highest-priority work.

Current Objective

**FEATURE FREEZE — Production Hardening program (2026-07-17).**

The MVP is feature complete (Phase 1 Sprints 1–12; Phase 2 Releases R1–R9). The
Sprint 12 Production Readiness Audit returned NOT READY (score 4.2/10): two
critical authentication backdoors, wildcard CORS with credentials, insecure
cookies, broken Docker packaging, no CI/CD, no rate limiting, mock data in
admin analytics, no frontend tests. No new product features ship until
Production Certification.

PH1.1 (Authentication Backdoor Removal) is COMPLETE (2026-07-17): the
auto-login endpoint, the Google OAuth demo/mock/legacy fallbacks, and the
startup admin seeding (default password + plaintext credentials file) are
removed; dev admin creation now lives in `backend/scripts/seed_dev_admin.py`
(refuses to run in production); guarded by `backend/tests/test_auth_hardening.py`.

PH1.2 (Google OAuth Production Hardening) is COMPLETE (2026-07-17): the OAuth
flow now enforces a CSRF `state` (backend-issued httponly cookie double-submit
**plus a single-use server-side record for replay protection and authoritative
TTL expiry, via Redis/in-memory `services/cache.py`**), cryptographically
verifies the Google id_token (signature + issuer + audience) and requires
`email_verified`, allowlists and binds the redirect_uri (no hardcoded dev
fallback), uses the Google **`sub` as the primary identity** (verified email for
safe linking; `sub_conflict` rejected) without creating duplicates, and writes
**immutable OAuth security-audit events** (`security_audit_logs`). Guarded by 26
hermetic tests in `backend/tests/test_oauth_hardening.py`. Risk R-02 fully
closed. See CHANGELOG.md.

PH1.3 (Cookie & Session Security) is COMPLETE (2026-07-18): every authentication
cookie is production-hardened and centralized in `backend/security/cookies.py` —
`Secure` forced when `APP_ENV=production` (env-driven `COOKIE_SECURE` in dev),
`HttpOnly` + `SameSite` on all cookies, `Path`/`Domain`/`Max-Age` from one policy,
and clearing that matches the set attributes so logout reliably removes every
cookie. The Google OAuth-state cookie now shares this unified posture (never
`Strict`; burned after use). Session fixation is mitigated (login/register/OAuth
mint fresh tokens that overwrite in place). Guarded by 24 hermetic tests in
`backend/tests/test_cookie_security.py`. Finding B4 and risk R-04 closed. CSRF
**token** middleware and refresh-token rotation are intentionally deferred
(SameSite=Lax provides the cookie-layer CSRF baseline now; rotation is PH1.6).
See CHANGELOG.md.

PH1.4 (CORS Hardening) is COMPLETE (2026-07-18): the wildcard-with-credentials
CORS default is removed and the policy is centralized in
`backend/security/cors.py`. Origins resolve from an environment-driven,
exact-match allowlist (`CORS_ALLOWED_ORIGINS` canonical; legacy `CORS_ORIGINS`/
`FRONTEND_URL` still honored), with `*` stripped from every source so a wildcard
can never pair with credentials. Development falls back to `localhost:3000`/
`localhost:5173`; production assumes nothing (fail closed). Methods and request
headers are restricted; no response headers are exposed. `server.py` wires it in
via `apply_cors(app)`. Guarded by 30 hermetic tests in
`backend/tests/test_cors_hardening.py`. Finding B3 and risk R-03 closed.
Security **headers** (HSTS/CSP/etc.) were de-scoped from this CORS-only sprint
and are carried forward as PH1.4b. See CHANGELOG.md.

PH1.5 (Password Policy & Account Protection) is COMPLETE (2026-07-19): password
policy is centralized in `backend/security/passwords.py` — the only place
passwords are validated, hashed, or verified. New passwords must be 12–64 chars
(≤72 UTF-8 bytes) with upper/lower/number/special, and must not be common
(bundled blocklist), email-/name-derived, repeated-character, or sequential;
enforced at the model layer on `UserCreate` (422, actionable messages, input
never echoed — a sanitizing RequestValidationError handler strips FastAPI's
default input reflection). bcrypt cost is now explicit (12); `verify_password`
never raises (fixed a 500 on password login against Google-native accounts) and
timing-equalizes failures via a dummy-hash comparison, so login cannot reveal
whether an email exists. Existing users, API contracts, and the `ip:email`
lockout (5/15min, now hermetically testable via FakeDB `$inc`) are preserved.
Guarded by 40 hermetic tests in `backend/tests/test_password_policy.py`.
Finding H10 (password half) closed; R-05 partially mitigated (rate-limiting
half is PH1.7). Email scope (EmailStr, verification, password reset, SMTP
decision OR-6) was deliberately split out to PH1.5b. See CHANGELOG.md.

PH1.4b (Security Headers) is COMPLETE (2026-07-20): all HTTP response security
headers are centralized in `backend/security/headers.py` and applied by one
pure-ASGI `SecurityHeadersMiddleware` (`apply_security_headers(app)`), wired
*after* CORS so even CORS preflight/rejection responses carry the headers. Every
response gets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: strict-origin-when-cross-origin`, a locked-down
`Permissions-Policy`, `Cross-Origin-Opener-Policy`/`Cross-Origin-Resource-Policy:
same-origin`, `X-XSS-Protection: 0` (deprecated auditor neutralized), and a
strict, nonce-capable CSP (`default-src 'none'; base-uri 'none'; form-action
'none'; frame-ancestors 'none'` — no `unsafe-*`). `Strict-Transport-Security`
(`max-age=63072000; includeSubDomains`) is emitted only over HTTPS/production;
`Cross-Origin-Embedder-Policy: require-corp` is implemented but opt-in. Every
value is environment-overridable and the CSP supports a `{nonce}` placeholder
resolved per request (`request.state.csp_nonce`). Guarded by 35 hermetic tests
in `backend/tests/test_security_headers.py`. The "no security headers" gap is
closed. See CHANGELOG.md.

PH1.6 (JWT Lifecycle & Session Security) is COMPLETE (2026-07-20): all JWT logic
centralized in `backend/security/jwt.py` (15-min access, hardened `iat`/`jti`/
`aud`/`iss`/`ver`/`sid` claim set, strict fail-closed verification, configurable
lifetimes); refresh-token families / rotation / reuse-detection / revocation in
`backend/security/sessions.py` (`SessionStore`, MongoDB-backed). Refresh now
rotates both tokens; a replayed refresh token revokes the whole family. Logout
revokes the current session; new `POST /api/auth/logout-all` revokes all sessions.
`password_changed_at` + token `ver` are the global kill-switches. 34 hermetic
tests in `backend/tests/test_jwt_sessions.py`. Risk R-06 / finding H11 closed.
See CHANGELOG.md and PRODUCTION_ROADMAP.md PH1.6 (records the tokens.py→jwt.py+
sessions.py split, Mongo-vs-Redis store, and 7-day refresh default deviations).

PH1.7 (CSRF Protection & Rate Limiting) is COMPLETE (2026-07-21): CSRF centralized
in `backend/security/csrf.py` — a signed double-submit token bound to the session,
enforced by `CSRFMiddleware` on cookie-authenticated mutations, with Bearer
requests exempt by construction (so no frontend change was needed). Rate limiting
centralized in `backend/security/rate_limit.py` — one limiter, named per-endpoint
policies (login 5/15min per ip:account with progressive lockout, register 5/hour,
refresh 20/min, authenticated API 120/min per user, public API 60/min per IP), a
pluggable `RateLimitStore` (MongoDB now, Redis-ready), and a platform-wide
`RateLimitMiddleware`; every rejection carries `Retry-After`. The prior inline
`login_attempts` lockout was folded in and removed. 44 hermetic tests
(`test_csrf.py` 18, `test_rate_limit.py` 26). Threat-model rows for CSRF-proper,
credential-stuffing/brute-force, and endpoint-flooding move to ✅ Closed. See
CHANGELOG.md and SECURITY_ARCHITECTURE.md §18/§21.

PH1.8 — Identity Recovery is COMPLETE (2026-07-22). This delivered the roadmap's
**PH1.5b (Email Validation & Verification / password reset)** content — executed
and labeled as the "PH1.8 — Identity Recovery" sprint; the roadmap's separate
PH1.8 (Secrets & Environment Hardening) is unchanged and still pending. All
recovery-token logic is centralized in `backend/security/recovery.py` (signed
`<token_id>.<HMAC>` handle + authoritative `recovery_tokens` record with atomic
single-use). New `/api/auth` endpoints: `verify-email`, `verify-email/request`,
`forgot-password`, `reset-password`, `change-password`. The user model gains
`email_verified` / `email_verified_at` / `verified_by`; new email/password
accounts start unverified and are emailed a link (Google accounts are verified on
creation/link). Forgot-password and resend return an identical generic response
(no enumeration). A reset or change **revokes every session** and bumps
`password_changed_at`, forcing re-login everywhere. Login is deliberately NOT
blocked on `email_verified` (backward-compatible). 28 hermetic tests
(`test_recovery.py`). See CHANGELOG.md and SECURITY_ARCHITECTURE.md §16/§17.
Remaining follow-ups (technical debt): tighten `email: str` → `EmailStr`, and
provision a real SMTP/SendGrid provider (open risk OR-6 — currently simulated).

PH1.9 — Secrets & Supply Chain Security is COMPLETE (2026-07-22). This delivered
the roadmap's **PH1.8 (Secrets & Environment Hardening)** content plus the
supply-chain/dependency-auditing portion of the roadmap's **PH1.11**, executed
and labeled as the "PH1.9 — Secrets & Supply Chain Security" sprint. The
configuration surface is now centralized in `backend/security/secrets.py` (the
authoritative `SECRET_REGISTRY` of every env var — category, sensitivity,
required-in-environment), with a boot-time `validate_config()` that runs before
the Mongo client and **fails closed** on a missing/weak critical secret,
aggregating every problem into one value-free error (no secret is ever logged).
Weak/hard-coded compose defaults were removed (`JWT_SECRET` placeholder → required;
n8n `alphapartner123` → required `N8N_BASIC_AUTH_PASSWORD`). `requirements.txt`
is now fully exact-pinned (4 floating `>=` bounds locked; 7 in-pin CVE patches
applied — aiohttp/cryptography/httplib2/pillow/pyasn1/pymongo/python-multipart).
A `security-audit` GitHub Actions workflow runs `pip-audit`/`pip check`/`npm
audit`/`gitleaks` on every push + weekly. New: `backend/.env.example` +
`frontend/.env.example` (generated from the registry, kept in sync by
`scripts/generate_env_example.py`), `.claude/SECRETS.md` runbook, and
`scripts/audit_dependencies.py`. 38 hermetic tests (`test_secrets.py`). Chosen
deltas from the roadmap plan: module is `backend/security/secrets.py` (not
`backend/config.py`) to follow the established security-package convention, and
the rotation runbook is a dedicated `SECRETS.md` (not folded into DEPLOYMENT.md).
See CHANGELOG.md and SECURITY_ARCHITECTURE.md §23/§24.

PH1.10 — Audit Logging & Security Monitoring is COMPLETE (2026-07-22). Security-event
observability is now centralized in `backend/security/audit.py`: a **closed event
taxonomy** across five categories (authentication / identity / session / security /
administration) mapping every event to a `category` + default `severity`
(info / notice / warning / critical; an unknown event fails safe to
security/warning); a **versioned structured schema** (`schema_version=1`: event,
category, severity, outcome, email, user_id, session_id, reason, ip, user_agent,
request_id, target, redacted details, timestamp); **recursive secret redaction**
that blanks any sensitive-keyed value (password/token/secret/authorization/code/
state/csrf/hash/api_key/cookie/signature) before storage — a secret can never
reach a sink; a **pluggable `AuditSink`** interface with a default composite of
durable `MongoAuditSink` (`security_audit_logs`) + SIEM-ready `LoggingAuditSink`;
and a **fail-safe `AuditLogger`** — emitting can never break a security flow. The
prior scattered `log_auth_event` is now a thin backward-compatible facade over it
(historical record fields are a strict subset, so every existing caller/query/
index/test is unaffected). Instrumented the auth surface (login ± / registration /
session created·revoked / logout·logout-all / refresh rotation / token-replay vs.
invalid-refresh / tampered-vs-expired invalid-JWT), the CSRF middleware
(`csrf_validation_failure`), and the rate limiter (`rate_limit_triggered` at the
single `_trip` choke point). 20 hermetic tests (`backend/tests/test_audit.py`);
full backend suite green (578 passed, 1 pre-existing unrelated failure). This took
the PH1.10 slot per the sprint brief; Admin Hardening & Session Management moves to
PH1.10b. See CHANGELOG.md and SECURITY_ARCHITECTURE.md §31b.

PH1.11 — Dependency & Vulnerability Scanning is COMPLETE (2026-07-22). The core
supply-chain deliverables (pip-audit/pip check/npm audit/gitleaks CI, full
exact-pinning, 7 CVE patches, `scripts/audit_dependencies.py`) landed in PH1.9;
the PH1.12 sprint finished the remainder: `.github/dependabot.yml` (weekly PRs
for pip `/backend`, npm `/frontend`, github-actions; docker staged for PH2.1/2.2),
the `requirements.txt` → `requirements-dev.txt` split (finding M14 — dev tools
`pytest/black/flake8/isort/mypy` + their exclusively-dev transitive deps, each
verified dev-only via `pip show … Required-by`, moved out of the runtime set so
the prod image ships no tooling), the triage-SLA policy (critical blocks release ·
high 7d · medium 30d · low 90d) in SECRETS.md §7 and TESTING.md, and a CI change
to audit BOTH requirements files and run `pip check` on the runtime-only install
(which doubles as proof of the split). See CHANGELOG.md and SECURITY_ARCHITECTURE.md §25.

PH1.12 — Security Certification is COMPLETE (2026-07-22) — the Phase 1 exit gate.
Implemented the three PH1.11 verification residuals: **F-1** (privilege
escalation) — new `backend/security/roles.py` centralizes the role allowlist and
`validate_role_assignment`, wired into `admin_update_user` so a plain `admin` can
no longer grant admin-tier roles (only `super_admin` can) and unknown roles are
rejected; **F-2** (unhandled ObjectId parsing → 500s) — new
`backend/security/identifiers.py` `parse_object_id` is the single boundary that
turns an untrusted id into a clean 400, applied to every user-facing path/body id
(admin user/ticket/flag/announcement editors, trade/notification/paper endpoints);
**F-3** (supply-chain automation) — see PH1.11 above. 48 new hermetic tests
(`test_roles.py`, `test_identifiers.py`); full hermetic suite 626 passed / 1
pre-existing unrelated failure. Executed the security verification checklist
(no debug mode, no backdoors, no hardcoded secrets; cookies/CORS/headers/CSRF/
rate-limit/audit/config-validation all confirmed) and re-scored: **Authentication
& Authorization 2.0 → 9.0**, **API & Transport Security 3.0 → 8.5** (both clear
the ≥ 8.0 gate). Deliverables: `docs/security/PH1_CERTIFICATION.md` (full report),
PH1.12 update prepended to `PRODUCTION_READINESS_REPORT.md`, sign-off in
PRODUCTION_HARDENING.md §17. **Decision: Phase 1 security CERTIFIED COMPLETE;
overall production deployment remains NO-GO pending Phase 2 (infra/Docker/CI-CD)
and Phase 3 (QA/frontend tests).**

**PHASE 1 (PRODUCTION SECURITY HARDENING) IS COMPLETE.** Current phase:
**PH2 — Production Infrastructure & DevOps**. PH2.1 (Backend Production
Dockerfile), PH2.2 (Production Docker Compose), PH2.3 (Secrets Management) and
PH2.4 (Production GitHub Actions CI) are complete — the backend stack boots
healthy from a single command with segmented networks, named volumes, no
hardcoded credentials, credentials deliverable as file-mounted Docker secrets,
and every push and pull request now mechanically verified: 695 hermetic tests, a
correctness lint gate at zero findings, an application-import and
startup-validation check, a production image that is built *and started* in CI,
and supply-chain plus secret-hygiene gates.

Next, in priority order:
1. **PH2.5 branch protection** — the highest-leverage remaining item, and small.
   Every gate PH2.4 built is advisory until `main` requires it; a red pipeline
   can still be merged today.
2. **Dependency remediation** (surfaced by PH2.4): the `fastapi`/`starlette`
   upgrade and the `litellm` removal close 14 of 15 suppressed advisories and
   ~55 MB of image size. CI will fail on 2026-09-21 if this is not triaged.
3. **PH2.4b (Environment & Configuration Framework)** — roadmap PH2.4.
4. **PH2.2b (Frontend Production Dockerfile)** — outstanding and parallelizable.

Deferred-within-PH1 items to schedule in the PH1 tail or
alongside PH2: PH1.9 Real-Time/WebSocket Security (Socket.IO auth, R-15) and
PH1.10b Admin Hardening & Session Management. PH3.1 (Backend Test Suite Repair)
may run in parallel — note its `test_trading_engine` item was **closed by PH2.4**
(stale assertion fixed; the hermetic suite is now 695/695), leaving the legacy
live-server test migration, which also unblocks PH2.6's integration stage.

Authoritative documents: PRODUCTION_HARDENING.md and PRODUCTION_ROADMAP.md.
Task tracking below under "Production Hardening Program".

---

# Production Hardening Program (PH1–PH3)

Status: PH1 COMPLETE (2026-07-22) — PH1.1 + PH1.2 complete 2026-07-17; PH1.3 + PH1.4 complete 2026-07-18; PH1.5 complete 2026-07-19; PH1.4b + PH1.6 complete 2026-07-20; PH1.7 complete 2026-07-21; PH1.5b/Identity Recovery + PH1.9 Secrets & Supply Chain + PH1.10 Audit Logging + PH1.11 Dependency Scanning + PH1.12 Security Certification complete 2026-07-22; SI1.1 Repository Audit complete 2026-07-17. **Phase 1 security certified; transition to PH2 (Infrastructure & DevOps).** Deferred within PH1: PH1.9 Real-Time/WebSocket Security, PH1.10b Admin Hardening.

Priority: Critical — blocks all other work

Full sprint definitions (objective, scope, acceptance criteria, validation,
rollback, estimates) live in PRODUCTION_ROADMAP.md. Status tracker:

## PH1 — Production Security Hardening

- [x] PH1.1 Authentication Backdoor Removal — COMPLETE (2026-07-17) — Critical
- [x] PH1.2 Google OAuth Production Hardening — COMPLETE (2026-07-17) — Critical
- [x] PH1.3 Cookie & Session Security — COMPLETE (2026-07-18) — Critical
- [x] PH1.4 CORS Hardening — COMPLETE (2026-07-18) — Critical
- [x] PH1.4b Security Headers (HSTS/CSP/etc., split from PH1.4) — COMPLETE (2026-07-20) — Critical
- [x] PH1.5 Password Policy & Account Protection (password portion of the roadmap's PH1.5) — COMPLETE (2026-07-19) — High
- [x] PH1.5b Email Verification & Account Recovery (verification flow, password reset, password change — delivered as the "PH1.8 — Identity Recovery" sprint) — COMPLETE (2026-07-22) — High — *follow-ups: EmailStr tightening + real SMTP provider (OR-6)*
- [x] PH1.6 JWT Lifecycle & Refresh Rotation — COMPLETE (2026-07-20) — High
- [x] PH1.7 CSRF Protection & Rate Limiting — COMPLETE (2026-07-21) — High
- [x] PH1.8 Secrets & Environment Hardening (delivered as the "PH1.9 — Secrets & Supply Chain Security" sprint, combined with the supply-chain portion of PH1.11) — COMPLETE (2026-07-22) — High
- [ ] PH1.9 Real-Time & WebSocket Security — NOT_STARTED — High
- [x] PH1.10 Audit Logging & Security Monitoring (centralized `backend/security/audit.py` — taxonomy, redaction, pluggable sinks, fail-safe; took the PH1.10 slot per the sprint brief) — COMPLETE (2026-07-22) — High
- [ ] PH1.10b Admin Hardening & Session Management — NOT_STARTED — Medium
- [x] PH1.11 Dependency & Vulnerability Scanning — COMPLETE (2026-07-22) — Medium — *finished in PH1.12/F-3: `.github/dependabot.yml` (pip/npm/github-actions; docker staged), `requirements-dev.txt` split (M14), triage-SLA in SECRETS.md §7 + TESTING.md; CI audits both requirements files. Core (pip-audit/npm audit/gitleaks, pinning, 7 CVE patches) landed in PH1.9.*
- [x] PH1.12 Security Certification — COMPLETE (2026-07-22) — Critical (gate) — *PH1 security certified; F-1/F-2/F-3 fixed; re-score authn 9.0 / API 8.5. Overall release NO-GO pending PH2+PH3. See docs/security/PH1_CERTIFICATION.md*

## PH2 — Production Infrastructure & DevOps

- [x] PH2.1 Backend Production Dockerfile — COMPLETE (2026-07-22) — Critical — *Two-stage `backend/Dockerfile` (builder → slim runtime), non-root uid 10001, `docker/entrypoint.sh` (fail-closed config validation + pre-start hooks + `exec` signal handoff), stdlib-only `docker/healthcheck.sh`, `.dockerignore`, `production.env.example`. Verified: builds in 2m44s cold / 4.5s on a code change, boots healthy in 2.5s, graceful SIGTERM exit 0, runs under `--read-only --cap-drop=ALL`. Image 1.03 GB — misses the <400 MB target because ~220 MB of declared dependencies are never imported (see docs/deployment/DOCKER.md §10). Also surfaced: `pytz` missing from `requirements.txt`.*
- [x] PH2.2 Production Docker Compose — COMPLETE (2026-07-22) — Critical — *Re-sequenced: the sprint as commissioned assigned PH2.2 to compose orchestration and PH2.3 to secrets; PRODUCTION_ROADMAP.md v1.2 still lists PH2.2 as the frontend image (now outstanding, see below) and PH2.3 as the compose split. Delivered: `docker-compose.yml` (production-shaped base — backend/mongo/redis, `name: stockassist`, YAML-anchored restart + `no-new-privileges` + bounded logging, `stop_grace_period: 30s`, loopback port binding, backend healthcheck inherited from the image rather than restated), `docker-compose.override.yml` (dev overlay — Mongo Express, Redis Insight `--profile tools`, n8n `--profile automation`, loopback DB ports, `APP_ENV=development`), `docker/mongodb/init-app-user.js` (least-privilege app user; root password never reaches the backend), `compose.env.example` (two-file env split: infra credentials vs application secrets), `docs/deployment/DOCKER_COMPOSE.md`. Two networks: `edge` (bridge) + `data` (`internal: true`, no egress). Named volumes only. 16/16 verification checks pass; cold start to all-healthy 13–32s, warm 12–14s. Security: MongoDB auth enabled and unpublished (was anonymous on `0.0.0.0:27017`), Redis password-protected, n8n basic-auth found dead upstream since n8n 1.0 and replaced with documented controls. Known limitation L1: the Redis pub/sub listener stops after ~3s — pre-existing defect at `backend/services/cache.py:47`, no functional regression at 1 worker/1 replica, fix owned by PH2.8.*
- [ ] PH2.2b Frontend Production Dockerfile — NOT_STARTED — Critical — *`frontend/Dockerfile` has never existed in this repository; the pre-PH2.2 compose file declared a frontend service that could not build. Multi-stage node → nginx per PRODUCTION_ROADMAP.md.*
- [x] PH2.3 Secrets Management — COMPLETE (2026-07-22) — Critical — *Source-resolution layer added to `backend/security/secrets.py`: one precedence order (`<NAME>_FILE` pointer → `$SECRETS_DIR/<name>` Docker/Swarm/K8s mount → plaintext env) applied to every variable, materialized into `os.environ` once at boot by `load_secrets()` — which is why ~30 existing `os.environ` consumers gained file-backed secret support with zero call-site changes. Fails closed: an unreadable pointer never falls back to the plaintext variable, and two sources for one secret is a boot error. `reload_secrets()` re-reads for rotation, reports changes by fingerprint (never value), and drops a revoked secret from the environment. Delivered: `docker-compose.secrets.yml` (opt-in overlay; retracts base values with `""` not `~`, since `~` inherits from the invoking shell), `secrets/generate.sh` + `secrets/README.md` + deny-by-default `secrets/.gitignore`, `docs/deployment/SECRETS.md`. Validation extended to credential *shape*: Mongo credentials, Redis password, Fernet-key format (error in every env), provider key shapes, and low-entropy detection (`aaaa…` clears a length check but not an offline attack). `REQUIRE_FILE_SECRETS=true` promotes plaintext delivery from warning to boot error. 68 new hermetic tests; full hermetic suite 694 passed / 1 pre-existing unrelated failure. Closes PH2.2 limitation **L3**. Bug found and fixed in-sprint: a `_FILE` pointer aimed inside `$SECRETS_DIR` conflicted with itself, which would have broken the documented configuration on every deploy. **Does NOT close PH2.2 L2** (in-container hot reload) — out of this sprint's scope, and now sharper: a bind-mounted `.env` would be a competing source. Residual limitations L1–L7 in docs/deployment/SECRETS.md §8.*
- [x] PH2.4 Production GitHub Actions CI — COMPLETE (2026-07-22) — Critical — ***Re-sequenced, same drift as PH2.2/PH2.3:** the sprint as commissioned assigned PH2.4 to GitHub Actions CI; PRODUCTION_ROADMAP.md v1.2 still lists PH2.4 as the Environment & Configuration Framework (now outstanding, see PH2.4b below) and CI as PH2.5/PH2.6. This sprint delivers **all of roadmap PH2.5** plus the Docker, supply-chain and secret-scanning stages of **PH2.6** — but not PH2.6's integration-test-against-Compose stage or image vulnerability scanning, and not PH2.5's branch protection or PR template. Delivered: `.github/workflows/backend-ci.yml` (parallel quality/build/test behind one aggregate gate; `build` runs compileall → `import server` with **runtime deps only** → startup validation across all three environments *including a negative case*; `test` runs 695 hermetic tests with JUnit XML uploaded `if: always()`), `docker-build.yml` (hadolint + buildx with GHA layer cache `mode=max` + artifact assertions + three smoke tests: **A** refuses to start unconfigured, **B** production config validates with no secret values in the log, **C** boots against real MongoDB/Redis, serves `/api`, exits 0 on SIGTERM — nothing is pushed), `dependency-audit.yml` (pip-audit/npm audit moved out of security-audit.yml, plus a **suppression-expiry ratchet**), `codeql.yml` (skips cleanly on a private repo without Advanced Security rather than failing red), `.github/actions/setup-backend/` (composite action; caches the built venv keyed on the requirements hash, **no `restore-keys`** — a partial-match restore is worse than a miss), `backend/pyproject.toml` + `.flake8` + `.hadolint.yaml`, `docs/deployment/GITHUB_ACTIONS.md`. **Lint adoption is deliberately two-tier**: the correctness subset (`E9,F63,F7,F82,F811,F632`) is blocking repo-wide at **zero findings** and files *added* by a PR are blocking under the full standard, while `black` (116/119 files), `isort` (70) and full flake8 (462) are advisory with a documented exit path — landing a 116-file reformat inside the CI PR would be unreviewable, and a permanently red `main` is worse than no build. Fixed in-sprint: a stale exact-equality assertion in `test_trading_engine.py` (`run_cycle` gained a `closed_trades` key), which had left the suite at 694/1 since PH2.3 and would have made `main` red on day one. **L1: `docker-build.yml` has never been executed** — no Docker daemon on the development machine; verified by YAML parse, `bash -n` over every run block, and review against the PH2.1 contract. **L2 (significant finding): 15 dependency advisories are suppressed** — `starlette 0.37.2` ×7 (fixes exist, held by the `fastapi==0.110.1` pin; highest priority, it is the ASGI request path), `litellm 1.80.0` ×7 (not imported by any application code — remove, do not upgrade), `ecdsa 0.19.2` ×1 (no fix, not reachable: JWTs are HS256). Each now carries a written reachability argument and a review date of 2026-08-22 enforced by CI. **L6: branch protection is not configured**, so every gate is advisory until PH2.5 requires it. Full L1–L10 in docs/deployment/GITHUB_ACTIONS.md §13.*
- [ ] PH2.4b Environment & Configuration Framework — NOT_STARTED — High — *Roadmap PH2.4. Env matrix (every var × every env) in DEPLOYMENT.md, consistent `APP_ENV` handling, staging env templates, config drift check. Note: the drift check largely exists already — `scripts/generate_env_example.py --check`, wired into `security-audit.yml` `config-sync`.*
- [x] PH2.5 Production Monitoring & Observability — COMPLETE (2026-07-22) — Critical — ***Re-sequenced, same drift as PH2.2/PH2.3/PH2.4:** the sprint as commissioned assigned PH2.5 to monitoring & observability; PRODUCTION_ROADMAP.md v1.2 lists PH2.5 as CI branch protection (still outstanding, see PH2.5b below). This sprint delivers **all of roadmap PH2.9** (Structured Logging) plus the observability core of **roadmap PH2.10** — but not PH2.10's alerting, error tracking (Sentry/GlitchTip), Prometheus/Grafana servers or uptime check, which remain in PH2.10.* Delivered: new `backend/observability/` package on the one-module-per-concern shape PH1 established for `backend/security/` — `context.py` (contextvars request correlation; dependency-free so `security.audit` imports it without a cycle), `logging.py` (JSON/text formatters, `configure_logging()`, scrubbing, access log), `metrics.py` (dependency-free Counter/Gauge/Histogram + Prometheus text exposition, **no `prometheus_client` dependency**), `health.py` (probe registry + `starting→ready→stopping` lifecycle + parallel timed probes with a 2s result cache), `runtime.py`, `middleware.py`, `routes.py`. **Three distinct probes, not one**: `/api/health/live` performs zero I/O (a DB-coupled liveness probe turns a database blip into a fleet-wide restart storm), `/api/health/ready` is the only one touching Mongo/Redis and also fails while starting or draining, `/api/health/startup` protects a ~20-index boot from the liveness timer. `/api/metrics` exposes the four golden signals with **route templates as labels, never raw paths** (the cardinality rule) plus a hard series ceiling and a `metrics_series_dropped_total` self-check. `/api/diagnostics` reports build provenance — `APP_VERSION`/`VCS_REF`/`BUILD_DATE` promoted from Docker build args to runtime `ENV`, since a process cannot read its own image labels. `configure_logging()` moved to the **top** of `server.py`, replacing the `logging.basicConfig` at line 5392 that ran after ~40 modules had already logged at import time. Middleware registered **last** so it is outermost — rate-limit 429s and CORS rejections are counted and carry a request ID. Six operational paths added to the rate limiter's exempt set (a probe cadence that trips the limiter makes the limiter manufacture the outage it prevents); `X-Request-ID` added to CORS `EXPOSE_HEADERS` (previously empty — without it the SPA cannot read the ID it needs to show the user). **`security.audit`'s `request_id` field, shipped in PH1.10, was `None` on every record in practice** because nothing generated one; it now reads the context. 123 new hermetic tests (suite 695 → 818); full flake8 clean on every added file. Two defects found by exercising the real boot path and fixed in-sprint: the message scrubber corrupted ordinary prose (bare whitespace accepted as a key/value separator), and histogram bucket bounds rendered with fixed six-decimal padding. **L1: metrics are per-process** — with `WEB_CONCURRENCY > 1` a scraper reaches one worker at random (PH2.10). **L2: no WebSocket instrumentation** — a 40-minute connection would poison the latency histogram. **L3: no alerting and no log shipping** (PH2.10 / PH2.6). **L4: `/api/admin/system/health` and `/api/admin/apis/health` still return partly fabricated data** (hard-coded latencies) — admin-dashboard endpoints, out of scope, should be re-pointed at this module's real data. Full limitations in `docs/operations/MONITORING.md` §11.*
- [ ] PH2.5b CI: Branch Protection & PR Template — PARTIAL — Critical — *Roadmap PH2.5. Pipeline delivered by PH2.4. Outstanding: branch protection on `main` requiring `backend-ci`, `docker-build` and `dependency-audit`; PR template carrying the PRODUCTION_HARDENING.md §15 checklist. Still the highest-leverage remaining infrastructure item, and small.*
- [x] PH2.6 Production Logging Infrastructure — COMPLETE (2026-07-22) — High — ***Re-sequenced, same drift as PH2.2–PH2.5:** the sprint as commissioned assigned PH2.6 to log management; PRODUCTION_ROADMAP.md v1.2 lists PH2.6 as CI Extended (still outstanding, see PH2.6b below). This sprint completes the **log-management half of roadmap PH2.9** that its scope never named, and the compose log-driver configuration **PH2.3** listed as a dependency. Application logging was NOT redesigned — PH2.5's schema, formatters and access log are untouched.* Delivered: `backend/observability/log_streams.py` (five-way stream separation — application / access / security / audit / error — routed **by logger name**, so not one call site changed; every file handler behind a bounded `QueueListener`) and `log_rotation.py` (size-triggered rollover to timestamped gzipped segments, retention by age **and** count). **Streams exist so retention can differ**: storing "this admin changed that user's role" under the same rule as 26M `GET /api/health` lines forces a choice between paying to keep access logs for a year and deleting the audit trail after a week. **Ordering is load-bearing** — `security.audit.events` is matched before `security.*`, or the security stream swallows every audit record and `audit.log` stays permanently empty; `error.log` is deliberately a **view, not a partition**, since making it exclusive would strip the access log of exactly its 5xx lines. **Timestamped segments, not the stdlib's `.1 .2 .3`**: one rename instead of N, a name that never changes meaning, and a chronological glob (`logrotate`'s `dateext`). **Age is pruned before count**, because count-first can retain a segment age already expired and quietly break a legal retention commitment; the pruner only deletes files whose names it can prove it created, so an operator's `application.log.keepme` is invisible to it. Files are **opt-in** (`LOG_TO_FILES`) and additive — stdout stays unconditional, so `docker logs` and any attached collector are unaffected; an unwritable log directory degrades to stdout-only rather than failing a boot. Docker: `/var/log/stockassist` pre-created in the image owned by uid 10001 (a named volume mounted on a path absent from the image is created **root-owned**, and the non-root app then fails to write it), `backend_logs` volume mounted **unconditionally** so logs cannot silently land on the container's writable layer and vanish on redeploy, `json-file` bounded at 10 MB × 3 with **`mode: non-blocking`** (the default `blocking` lets a stalled log backend block `write()` and therefore the event loop), plus the documented driver matrix for Loki / ELK / CloudWatch / Datadog / Splunk. 8 new env vars registered in `security/secrets.py` (single source of truth → `.env.example`, CI drift-checked); all clamp-and-warn, because a logging misconfiguration must never stop a deployment. **Redaction was verified, not rebuilt** — roadmap PH2.9's "grep for a known token pattern → zero hits" criterion is now enforced on the **file** sink, where logs persist. 61 new hermetic tests (`test_log_infrastructure.py`; suite 818 → 879, full suite green). **One real defect found and fixed in-sprint: `request_id` was silently `"-"` in every file record** while stdout showed it correctly — the formatter reads a `contextvars` value at format time, and file records are formatted on the listener *thread*, whose context is empty. The context is now snapshotted onto the record at enqueue time, on the calling thread. A correlation field that is present, authoritative-looking and wrong is worse than an absent one. Measured: caller cost **5.90 µs/record with file sinks on vs 12.34 µs stdout-only** (the queue moves JSON formatting off the request path, so enabling files made the caller *faster*), ~31k records/sec sustained, rotation+gzip 9.2 ms/MB (~460 ms at the 50 MB default, paid entirely on the listener thread), 8.1:1 compression, ~560 MB steady-state for all five streams. **L1: per-container, not centralized** — shipping is PH2.10. **L2: `docker compose down -v` destroys the log volume**; one volume on one host is not a retention strategy for an audit trail. **L3: retention runs at rotation time, not on a timer** — a stream that stops receiving records keeps segments past `LOG_RETENTION_DAYS` until its next rotation (bounded by count, but not a wall-clock guarantee). **L4: multi-worker rotation races** if `WEB_CONCURRENCY > 1` (already required to be 1 until PH2.8). **L5: `audit.log` is a portable copy, not the record of authority** — MongoDB remains that. Full limitations in `docs/operations/LOGGING.md` §10.*
- [ ] PH2.6b CI Extended: Integration & Image Scanning — PARTIAL — High — *Roadmap PH2.6. Docker build, supply-chain gates and secret scanning delivered by PH2.4. Outstanding: integration job booting the prod Compose stack and running `pytest -m integration` (**95 tests** after PH3.1's conversions; **unblocked** — PH3.1 classified and stabilized them, and the job **must** set `REQUIRE_LIVE_BACKEND=1` or unreachable-deployment skips will report green), image vulnerability scanning (Trivy/Grype), frontend test job (PH3.3), coverage job (PH3.11 — tooling ready, `pytest --cov` works).*
- [x] PH2.7 Production Redis Infrastructure — COMPLETE (2026-07-23) — High — ***Re-sequenced, same drift as PH2.2–PH2.6:** the sprint as commissioned assigned PH2.7 to production Redis; PRODUCTION_ROADMAP.md v1.2 lists PH2.7 as CD & Release Automation (still outstanding, see PH2.7b below). No roadmap sprint covers Redis hardening at all — it was assumed complete at PH2.2, which is exactly why the defects below survived.* Delivered: new `backend/infrastructure/` package on the one-module-per-concern shape PH1 established for `security/` and PH2.5 for `observability/` — `redis_client.py` (single pooled client: pool, retry, **circuit breaker**, INFO sampler, diagnostics) and `redis_pubsub.py` (supervised subscriber: dedicated connection, reconnect with exponential backoff + jitter, one-subscriber-per-channel registry, graceful shutdown). `services/cache.py` keeps every signature and contract and becomes pure policy (JSON, TTLs, batching, the bounded in-memory fallback); `docker/redis/redis.conf` replaces the six inline compose flags with 37 documented directives shared by the base stack **and** the secrets overlay, ending a duplication that would have drifted on the first tuning change. **Two production defects fixed, both of which failed silently — the reason this sprint existed.** *(1)* `services/cache.py` latched `_redis_failed = True` on the **first** failure and never cleared it: one transient blip — a Redis restart during a deploy, a 2s partition, an AOF-rewrite pause — permanently demoted that process to its in-memory fallback for its entire lifetime. Nothing raised, no request failed, no alert fired; the process just stopped sharing cache state with its peers until someone restarted it for an unrelated reason. Replaced by a CLOSED→OPEN→HALF_OPEN breaker that re-tests the dependency and closes on recovery, with the readiness poll doubling as the half-open trial. *(2)* the Pub/Sub listener ended **permanently** on the first exception (`async for … in listen()` wrapped in a bare `except`), and Redis drops subscribers routinely by design — `client-output-buffer-limit pubsub` exists precisely to disconnect a slow consumer. HTTP kept working, the cache kept working, health checks kept passing (pinging Redis and *being subscribed to it* are different facts), and the only symptom was WebSocket clients on that one replica silently ceasing to receive cross-process events. To the user: "the market went quiet." To the operator: nothing at all. **Consequently `/api/diagnostics/redis` reports per-channel subscriber state**, which no ping can. Also: `observability/health.py`'s Redis probe no longer builds a throwaway client per poll (a TCP connect + AUTH + teardown several times a minute, per replica, forever) — it goes through the shared client, so it measures what the application actually experiences rather than what a fresh private connection would have seen. 10 Redis metric families added (`redis_circuit_state` is the one to alert on — a **leading** indicator, since the fallback keeps serving while it climbs); `redis_server_*` gauges are sampled by a background task on a fixed cadence, **never at scrape time**, following PH2.5's rule that a scraper must not be able to drive load onto a dependency. 8 new env vars registered in `security/secrets.py` (single source of truth → `.env.example`, CI drift-checked); all clamp-and-warn. **Server config decisions, each documented inline:** AOF-only persistence is a **warm-restart** optimization, not durability — without it every Redis restart makes every replica re-fetch the whole quote universe from rate-limited providers at once; `timeout 0` because a healthy subscriber is by definition idle and any idle timeout would disconnect exactly the working ones; `maxclients 512` because Redis's default 10000 is not a limit on a memory-capped container (client buffers are not counted against `maxmemory`); `lazyfree-lazy-*` because freeing a multi-megabyte universe snapshot blocks **every** client on a single-threaded server; `enable-protected-configs/debug/module no` to close the `CONFIG SET dir` + `SAVE` → arbitrary-file-write path the PH2.2 comments already named. 50 new hermetic tests (`test_redis_infrastructure.py`; suite 879 → **929**, all green, flake8 clean). Measured: `cache_get()` facade overhead **3.5 µs/op**, `cache_set()` **7.3 µs/op**, and an operation while the circuit is open **1.1 µs** against the 1.5 s connect timeout it replaces (~1.3M×) — the entire value of the breaker, since a dead dependency must not make the application slow. Reconnect ladder measured: a short blip recovers on attempt 1–3, ~1–4 s after the server accepts connections again. **L1: live-stack verification was NOT executed** — no Docker daemon on the development machine; the four manual checks (restart persistence, connection recovery, Pub/Sub reconnect, eviction) are scripted in `docs/infrastructure/REDIS.md` §8 and must be run before this reaches production. **L2: single node, no failover** — if Redis is lost every replica degrades to its in-process cache and cross-process realtime stops until it returns; acceptable only because nothing in Redis is a system of record, and the migration path (Sentinel → managed → Cluster) is §9. **L3: Pub/Sub remains at-most-once** — messages published while a subscriber is disconnected are gone; reconnecting restores the stream, not the gap. Fine for UI refresh signals, **wrong for anything where a missed message is a lost fact**, which needs Redis Streams, not a bigger buffer. **L4: `maxmemory-policy allkeys-lru` is only correct while everything in Redis is reconstructible** — the day something non-evictable is added, that data belongs in MongoDB instead. **L5: the breaker is per-process**, so with several replicas each discovers a Redis outage independently. Full limitations in `docs/infrastructure/REDIS.md`.*
- [ ] PH2.7b CD & Release Automation — NOT_STARTED — High — *Roadmap PH2.7, displaced by the sprint above. Outstanding: automated deploy on tag, environment promotion, rollback procedure, release notes generation. Depends on PH2.5b branch protection.*
- [x] PH2.8 Production Configuration & Environment Optimization — COMPLETE (2026-07-24) — High — ***Re-sequenced, same drift as PH2.2–PH2.7:** the sprint as commissioned assigned PH2.8 to configuration consolidation & runtime optimization; PRODUCTION_ROADMAP.md v1.2 lists PH2.8 as Database & Redis Production Configuration (still outstanding, see PH2.8b below). This sprint closes the two follow-ups PH2.1 explicitly deferred here — the `requirements.txt` prune and the `pytz` defect — and delivers the config-precedence/environment-profile documentation roadmap PH2.4 left open.* The configuration **architecture** was already centralized in `backend/security/secrets.py` (PH1.9/PH2.3 — one registry-driven loader + fail-closed validator), so this sprint consolidated and documented rather than rebuilt. Delivered: **(1) dependency prune, 118 → 58 packages** — `requirements.txt` was a raw `pip freeze` carrying ~220 MB of packages no application module imports; rebuilt from actual imports (direct set → dependency closure), documented in two sections with a `pip freeze` ban. Removed the abandoned `litellm`/`openai`/`tiktoken`/`huggingface` stack, the **old** `google-generativeai` SDK + `grpcio`/`protobuf`/`google-api-*` gRPC tail (app uses the new HTTP `google.genai`), `boto3`/`botocore`/`s3transfer`/`s5cmd`, `stripe`, `pandas`/`numpy` (zero app imports — the Dockerfile's "pandas/numpy-heavy" note was stale), `python-jose`/`passlib`, `python-multipart`, and their transitive tails; `watchfiles` moved to `requirements-dev.txt` (dev-reload only). **Proven safe offline, two ways:** the pruned set is closed under its own core requirements (nothing missing), and with all 62 removed modules blocked at import the entire runtime module graph still loads (nothing over-removed). **(2) `pytz` defect fixed** — imported by `services/market_engine/validator.py` but pinned in neither requirements file, so the Market Engine validator failed to initialize on any clean install (PH2.1's surfaced defect, now closed); pinned `pytz==2025.2`, plus `docstring_parser==0.18.0` (an unpinned core dep of `anthropic`). **(3) first-class `testing` environment profile** — `TESTING` joins `KNOWN_ENVIRONMENTS`, `LENIENT_ENVIRONMENTS` names the development-severity set; `APP_ENV=testing` is now a recognized, non-production, honestly-labelled env for CI instead of an "unknown APP_ENV" error. **(4) `docs/infrastructure/CONFIGURATION.md`** — sources & precedence, the four profiles, fail-closed validation, the dependency method + removal ledger, image-optimization results, migration + cloud-portability guidance. **Measured:** `site-packages` 569 MB → ~192 MB, **377 MB (−66%)** off the dependency footprint (measured from the resolved venv); projected runtime image 1.03 GB → ~650 MB. 5 new tests (`test_secrets.py`; core-trio parametrization now covers all four profiles); full non-integration suite **934 passed**, no application code changed. **L1: the end-to-end image size is projected, not built** — no Docker daemon in the sprint environment; the 377 MB is measured directly from the venv layer the image copies, and the CI Docker build (structurally unchanged) produces the exact number. **L2: `yfinance` remains optional/unpinned** — `backtest_engine.py` falls back to synthetic data; pinning it would reintroduce `pandas`+`numpy` and route market data outside the Market Gateway (out of scope). Full detail in `docs/infrastructure/CONFIGURATION.md`.*
- [ ] PH2.8b Database & Redis Production Configuration — NOT_STARTED — High — *Roadmap PH2.8, displaced by the sprint above. Mongo least-privilege app user + TLS connection string + index audit against DATABASE.md; Redis persistence decision (AOF chosen in PH2.7) and connection-pool sizing. Depends on PH2.3.*
- [x] PH2.9 Structured Logging — COMPLETE (2026-07-22) — High — *Delivered in full by the PH2.5 sprint above: `backend/observability/logging.py` (JSON records to stdout, request-ID correlation injected by the formatter so no call site changed, secret redaction sharing `security.audit`'s marker list, single-line access log). The roadmap expected `backend/tests/test_log_redaction.py`; redaction coverage lives in `backend/tests/test_observability.py::TestStructuredLogging` alongside the rest of the logging tests. **Extended by the PH2.6 sprint** with the log-management layer this scope never named — stream separation, rotation, retention, compression and Docker log-driver configuration (`docs/operations/LOGGING.md`), plus redaction verification on the file sink. Log **shipping** is still not part of this — that is PH2.10.*
- [ ] PH2.10 Monitoring, Metrics & Alerting — PARTIAL — High — *Health endpoint split, metrics endpoint and dependency probes delivered by PH2.5 above. Outstanding: Prometheus server + scrape config (point it at `/api/metrics`, bearer auth via `METRICS_TOKEN`), Grafana dashboards, error tracking (Sentry/GlitchTip) for backend + frontend, the minimum alert set, uptime check on the public URL, and cross-worker metric aggregation (PH2.5 L1). Draft alert rules are in `docs/operations/MONITORING.md` §12.*
- [x] PH2.9 Production Backup & Restore — COMPLETE (2026-08-04) — High — ***Re-sequenced, same drift as PH2.2–PH2.8:** the sprint as commissioned assigned PH2.9 to backup & restore; PRODUCTION_ROADMAP.md v1.2 lists PH2.9 as Structured Logging (already complete, see above) and places backup under PH2.11. This sprint delivers the BACKUP AND RESTORE half of roadmap PH2.11; disaster recovery — the postmortem template, the full-environment DR runbook and the off-host/cloud story — remains PH2.10 in the sprint track.* Delivered `scripts/backup/` on the one-module-per-concern shape PH1 established for `backend/security/`: `lib.sh` (config, encryption, checksums, manifests, retention, Mongo transport, destructive-action guard — bash 3.2 compatible, because macOS ships 3.2 and a script that only works on the production shell is one whose behaviour is first observed during an incident), `backup_mongo.sh`, `restore_mongo.sh`, `verify_backup.sh`, `backup_config.sh`, `backup_uploads.sh`. **`mongodump --archive --gzip` is streamed through AES-256 straight to disk**, so no plaintext copy of the database ever touches a filesystem; publication is `.partial` → checksum → rename → **checksum again** (`mv` across a filesystem is a copy, and a copy is where a full disk truncates silently). **Verification is three graduated levels, not one**: `checksum` (0.12 s, offline), `structural` (0.31 s, offline — decrypts and runs the whole payload through gzip's CRC, then confirms the mongodump archive magic; runs automatically after every backup) and `drill` (~5 s — restores into `<db>__drill_<ts>` via `--nsFrom`/`--nsTo`, compares per-collection counts against a baseline captured at dump time, drops the scratch db). The baseline is load-bearing: **`mongorestore` exits 0 on a restore that moved nothing**. Retention is grandfather-father-son, **count-based per tier — deliberately diverging from PH2.6's age-first log retention**, because logs carry a wall-clock legal commitment while backups carry a coverage commitment, and age-first would silently reduce seven restore points to five in a week the job failed twice; prune runs LAST and only on success. **Redis is deliberately not backed up** — everything in it is reconstructible cache or in-flight Pub/Sub, and PH2.7's AOF is a warm-start optimisation, not a backup; documented with a monthly no-TTL tripwire. `backup_config.sh` **mandates encryption with no development exemption** (100% credential material) and the documentation carries the recursive-dependency trap: the passphrase cannot live in the repo, the environment, the secret store, or on the host being backed up. **Live drill executed**: real `alpha_stock_db` (21 collections) → 21/21 matched; scale drill at 205 000 docs / 26.3 MB → backup **2.06 s**, artifact **1.99 MB (13.2:1)**, restore **3.51 s**, secondary index and sampled document contents identical. Wrong passphrase, corrupted artifact, corrupted-artifact-with-rewritten-manifest, and unattended restore into a populated database all correctly rejected. 39 hermetic tests (`test_backup_restore.py`), flake8 clean. **Three real defects found and fixed in-sprint by the tests: (1)** a failing `git status` exited 128 and killed the whole config backup under `set -e`, silently and with no output; **(2)** an empty dump was publishable whenever encryption was on, because `openssl enc` turns zero bytes into a ~32-byte file that satisfies the non-empty check — post-write structural verification is now load-bearing and a failed artifact is quarantined as `*.rejected` (outside every glob, so `--latest` cannot select it and retention does not count it, but still on disk as evidence); **(3)** the working directory was created inside `$( … )`, where bash fires the EXIT trap on subshell exit and deleted it before the caller could use it. Also fixed: a credential file could be left inside the mongo container because staging happened lazily inside a pipeline element. Documentation: `docs/operations/BACKUP_AND_RESTORE.md`. **L1: no point-in-time recovery** — a standalone mongod is per-collection consistent only; the fix is a single-node replica set + `--oplog`. **L2: the off-host copy is documented, not implemented.** **L3: AES-CBC is unauthenticated** (mitigated by object lock; the manifest records `encryption` per artifact so a future format does not orphan today's files). **L4: backup failure is not alerted** (PH2.10). **L5: the drill is cron, not CI** — CI has no MongoDB. **L6: `BACKUP_MODE=docker` is unverified** — no Docker daemon in the sprint environment; every measurement was taken in `direct` mode against a real MongoDB 8.0.13, and the transports differ only in how the tools are invoked. **L7: uploads have no data**, so the Docker-volume path is unexercised (host-path mode round-tripped end to end). **L8: `docker compose down -v` still destroys the local volumes.** Full limitations in `docs/operations/BACKUP_AND_RESTORE.md` §14.*
- [x] PH2.10 Disaster Recovery & Business Continuity — COMPLETE (2026-08-05) — High — ***Re-sequenced, same drift as PH2.2–PH2.9:** the sprint as commissioned assigned PH2.10 to disaster recovery; PRODUCTION_ROADMAP.md v1.2 lists PH2.10 as Monitoring/Metrics/Alerting (still PARTIAL, see above) and places disaster recovery under PH2.11. This sprint delivers the DISASTER RECOVERY half of roadmap PH2.11 — the half PH2.9 explicitly left open.* Delivered `scripts/dr/` on the one-module-per-concern shape PH1 established for `backend/security/`: `dr_verify.sh` (layered diagnosis **and** post-recovery verification) and `deploy_rollback.sh` (deployment ledger + verified rollback), both **sourcing `scripts/backup/lib.sh` rather than reimplementing** config loading, logging and the Mongo transport — a verifier that reached the database differently from the way the restore reaches it could report healthy against a database the restore never touched. Documentation: `docs/operations/DISASTER_RECOVERY.md` (ten runbooks R1–R10: failed deployment, container failure, Redis loss, MongoDB corruption, **a failed rollback**, storage/volume loss, complete server loss, configuration corruption / accidental secret rotation, suspected compromise, and a silently failing backup job — each with diagnosis, recovery, *what to do when the recovery fails*, and verification as a command) plus `docs/runbooks/POSTMORTEM_TEMPLATE.md`, and the two three-line stubs `docs/operations/runbooks.md` / `incident-response.md` finally given content. **The RTO is decomposed rather than asserted** (§4.2): the mechanical work is under five minutes and *detection* is the dominant term, which is the finding that makes roadmap PH2.10's alerting worth more to recovery time than any further optimisation of the restore path. Seven named recovery assumptions (§4.3) with a quarterly drill against each, because an assumption that has quietly stopped being true is the normal way a tested plan still fails. **Verification is four layers, not one** — host → containers → data → application — with three decisions that carry the design: every check runs and dependent checks report **SKIP rather than a second misleading failure** (a diagnostic must not stop early the way a test suite should; "containers up, Mongo fine, Redis unreachable" is a different incident from "nothing is running"); **an empty restored database is a FAILURE**, since a stack serving no data passes every other layer; and the **running build is asserted against the expected version**, because `docker compose up -d` with an unchanged tag is a silent no-op, so "I rolled back" and "the old code is running" are two different claims. Rollback closes the four facts a rollback needs and nothing currently answers (no registry/CD yet — PH2.7b): an append-only ledger under `$BACKUP_ROOT` **so it survives the host it describes**, a precondition that refuses to touch anything unless the target image is on the host, an atomic `.env` rewrite (a half-written `.env` breaks compose interpolation entirely — worse than either version), backend-only recreation (`--no-deps`), and an **automatic revert** when the rolled-back build also fails verification. It deliberately does **not** `git checkout` and does **not** reverse migrations; it asks the migration question out loud and requires the tag to be typed. 41 hermetic tests (`test_disaster_recovery.py`, docker/curl/mongosh stubbed; suite 934 → **975**), flake8 clean, PH2.9's 39 still green. **Measured against the live `alpha_stock_db`:** backup 1.63 s, **restore of 21 collections 4.48 s with 21/21 matched**, `dr_verify --level full` **1.10 s**, config archive 0.72 s, **config recovery 0.17 s for 14 files**, and compose interpolation of a rolled-back tag verified without a daemon. The manifest comparison was proven non-vacuous by inserting one document (detected: `MISMATCH admin_audit_logs expected=7 actual=8`) and removing it again. **L1: the off-host copy is still documented, not implemented — R7 (complete server loss) is unexecutable without it**, and it is now stated as such rather than as a footnote. **L2: R1–R3, R5–R7 are unexecuted end to end** (no Docker daemon in the sprint environment, as in PH2.7/PH2.9); the data and configuration paths ran for real. **L3: detection is manual.** **L4: RPO is bounded by backup frequency.** **L5: no registry, so rollback depends on the image surviving on the host.** **L6: the ledger records only what goes through the script.** **L7: single host, no failover — every recovery here is an outage.** Full limitations in `docs/operations/DISASTER_RECOVERY.md` §12.*
- [ ] PH2.11 Backup & Disaster Recovery — PARTIAL — High — *Backup, restore, verification, retention, encryption, secret recovery and the executed restore drill are delivered by the PH2.9 sprint above; the disaster-recovery half — runbooks, recovery objectives, rollback, verification tooling and `docs/runbooks/POSTMORTEM_TEMPLATE.md` — by the PH2.10 sprint above. **Outstanding for the roadmap item: the off-host/encrypted-remote copy actually wired up** (the one gap that leaves a whole runbook unexecutable) and backup-failure alerting (shared with PH2.10).*
- [x] PH2.12 Infrastructure Certification & Release Readiness — **COMPLETE (2026-08-09) — CONDITIONALLY CERTIFIED — infrastructure score 8.0/10** — Critical (PHASE 2 EXIT GATE) — *Report: `docs/infrastructure/PH2_CERTIFICATION.md` (25 sections, full matrix, evidence per row). **Scope note:** the roadmap framed this as "Certification & Staging Sign-off" with a 7-day soak; there is still no durable staging environment, so the soak was **not** performed and is carried to PH3. What was performed instead is a full certification against a **live local stack** — which was possible for the first time, because this is the first PH2 sprint with a working Docker daemon (29.4.0). Every sprint from PH2.7 onward recorded "no Docker daemon in the sprint environment", and that gap is exactly where the defects were hiding.* **Found and fixed one CRITICAL: `deploy_rollback.sh` did not roll back and reported that it had.** It rewrote `BACKEND_IMAGE_TAG` in `.env`, ran compose, recreated **nothing**, and printed `rollback verified` while the bad release continued serving — measured live: `.env`=`cert`, container=`v2-bad`, app=`2.13.0-badrelease`. Root cause: `bk_load_env_file` **exports every key it parses out of `.env`**, so the tag being rolled *away from* was already in the process environment, and **Compose ranks shell variables above the `.env` file** — the script's careful atomic rewrite was outranked by its own config loader. Verification passed because it checked *health*, and the version being rolled away from is healthy by definition; it was serving wrong behaviour, not failing a probe. Fixed by passing the tag on the compose invocation and by asserting the **running build** before declaring success (on mismatch: fail, ledger `FAILED rollback`, explicit "do not close the incident"). Post-fix: `Recreated`, 10 s, independently confirmed. **Two HIGH, both fixed: (1)** the BLOCKING flake8 correctness gate has been red on **every CI run since PH2.4** — CI builds its venv at `backend/.venv-ci`, `.flake8` excluded `venv`/`.venv`, and flake8 matches on **basename**, so CI linted its own site-packages where libraries legitimately trip F811; it passes locally only because the dev venv is named `venv`. **(2)** `dr_verify.sh`'s running-build probe parsed `"app_version"`/`"vcs_ref"` which `/api/diagnostics` has never emitted (they are nested `build.version`/`build.revision`), so it could only SKIP — or, with `--expect-version`, **FAIL a healthy correctly-deployed stack while blaming `DR_OPS_TOKEN`**. Both hermetic stubs encoded the same wrong shapes, so the suites agreed with the bugs. **Verified live:** image **423 MB** (was 1.03 GB; beats PH2.8's ~650 MB projection), non-root, `/app` unwritable, no pip, no secrets in layers or FS; healthy **8 s** from fresh volumes; graceful shutdown **exit 0** ×3; data survived a full `down`/`up`; `data` network `internal:true`, no published DB ports, both datastores reject unauthenticated access; fail-closed config rejected 5/6 bad configurations (incl. a placeholder API key, which caught the certifier's own stand-in credential); **zero leakage** grepping four *real* secrets across stdout **and** file sinks; liveness/readiness correctly split under Mongo and Redis failure with auto-recovery; metrics token-gated, 20+ families; **backup + destructive restore drilled in `docker` mode — closes PH2.9's L6** (3 collections dropped → full recovery, 16 matched, ~1 s); `dr_verify --level full` **12/12**; **1014 hermetic tests pass**; frontend build clean; **no PH1 security control regressed** (containerisation improved the posture). **Left open as required-before-production (§24 of the report):** 6 CVEs in *runtime* deps (`cryptography` 48.0.1, `aiohttp` 3.14.1 — a major crypto bump touches broker Fernet tokens and does not belong in a certification sprint), npm high-severity advisories, **the off-host backup copy (still unwired — R7 remains unexecutable)**, no CD/registry, no frontend production image, branch protection unverified, and **no alerting whatsoever** — detection is manual, which per PH2.10's own RTO decomposition dominates recovery time and makes the measured sub-15-second mechanical RTO theoretical. **4 files changed, all remediation:** `backend/.flake8`, `scripts/dr/dr_verify.sh`, `scripts/dr/deploy_rollback.sh`, `backend/tests/test_disaster_recovery.py` (DR suite 41 → **43**; both new tests proven non-vacuous by reverting the fix and re-running). Machine left as found — stack torn down, volumes removed, cert images deleted, developer `.env` restored **byte-identical** (sha256 verified).*

> **PH2 EXIT — engineering lesson, carried to PH3.1 as its charter.** The Critical
> and one of the Highs survived every prior review because their hermetic tests
> **stubbed a system boundary and then agreed with the implementation instead of
> the contract**: the docker stub returned a fixed running image (so a rollback
> and a no-op were indistinguishable), and the curl stub emitted a diagnostics
> payload shape the real endpoint has never produced. Both suites were green the
> whole time. When a probe and its test share an assumption, only the real system
> can settle it — which is why PH3.1 should add an integration job that boots the
> actual compose stack in CI, the capability this sprint had to exercise by hand.

## PH3 — Production Quality Assurance

- [x] PH3.1 Backend Test Suite Repair & Hermeticity — **COMPLETE (2026-08-09) — CERTIFIED** — Critical — *Report: `docs/testing/PH3.1_TEST_CERTIFICATION.md` (21 sections, full inventory); developer reference: `docs/testing/TEST_ARCHITECTURE.md`.* **Default `pytest`: 1,035 passed / 0 failed / 0 errors / ~2m20s** — was **1,016 passed / 47 failed / 51 errors / 176s**. Verified in a fully scrubbed environment (`env -i`, no `.env`, no exported secrets), which is the CI-compatibility proof rather than a claim about it. **The sprint's premise was wrong in the way that mattered.** The charter assumed the hermetic suite was hermetic and only the live suites needed marking. Socket instrumentation built for this sprint found **three tests in the default suite opening live TLS connections on every run** — `api.anthropic.com`, Google Generative Language, Yahoo Finance — **authenticated with the developer's real production API keys**, because `server.py` calls `load_dotenv(backend/.env, override=True)` at import time and `conftest.py` imports `server`. They passed either way: the call sites catch broadly (correctly — a provider outage must not take the API down), so a live call and a mocked one produce an identical green tick and no output distinguishes them. Closed three independent ways — `tests/_testenv.py` (fixed synthetic env, `PYTHON_DOTENV_DISABLED=1`, every third-party credential blanked, overwrite-not-default so an exported shell secret cannot leak past), `tests/_netguard.py` (autouse socket-level guard, patched at `socket.socket.connect` because the escapes came through three different HTTP clients), and blank credentials making every `*_configured()` read false — and **measured at zero offenders**. Runtime fell 202s → 139s as a side effect. **Two genuine implementation defects found and fixed**, both `backend/security/secrets.py`: `app_env()` and `get()` used `(environ or os.environ)`, so an **explicitly empty mapping silently resolved to the host's live configuration** — a caller asking "what does this resolve to with nothing set?" was answered with the host's real config, wrong in the dangerous direction for a security-config reader. It survived every prior review because the test that catches it was itself running in a process that had loaded the developer's `.env`; **the same "stub agrees with the bug" pattern PH2.12 recorded above, one level up** — a check and the thing it checks sharing an assumption. The chartered stale assertion `test_run_cycle_trails_and_books_targets` was **already repaired** by a prior sprint (verified against `services/trading_engine.py:346`: `closed_trades` is in the return contract, exact-equality assertion intact — not weakened). **Beyond scope, delivered:** 19 new hermetic API-contract tests (`tests/test_api_contract.py`, converted from the live suite, every assertion **mutation-checked** by inversion), covering the degraded branches (`available: false`, 503-vs-404) that no live test can trigger on demand; the hardcoded `admin@alphapartner.com`/`admin123` pair removed from **five** files and filesystem-scraping of `/app/frontend/.env` removed from two (`tests/_live.py` now owns live config, credentials from env with no defaults); `ALLOW_LIVE_WHATSAPP_SEND=1` now required for the one test that sends a **real billable WhatsApp message**; marker taxonomy registered (`integration`/`live`/`e2e`/`security`/`slow`/`requires_db`/`requires_redis`/`allow_network`), applied **mechanically** from filename lists so a hand-applied decorator cannot drift; `pytest -m security` = 452 tests in 34s; **coverage baseline 59.2% of application statements** (`security/` 94.8%, `observability/` 95.8%, `infrastructure/` 82.4%, `trading_engine` 82.0%, `server.py` 51.9%, `services/` 42.4%) — the honest figure, not the 72% that including test files in the denominator produces. **`pytest -m integration` now skips cleanly** (0.28s) without a deployment instead of failing for 3 minutes — **and `REQUIRE_LIVE_BACKEND=1` turns every such skip into a failure, which the PH2.6 integration job MUST set**, or a stack that failed to boot will skip its way to a green tick. `test_phase8.py` (0 bytes) deleted; `test_backend.py` → `test_backend_live.py`. `requirements.txt` untouched — `pytest-cov`/`coverage` are dev-only. **Carried, not fixed:** no CI integration job (PH2.6), no frontend tests (PH3.3), no branch coverage or coverage gate (PH3.11), and `FakeDB` remains an operator-subset double — the standing argument for not letting a green hermetic suite justify dropping the integration layer.
- [x] PH3.2 Mock Data Eradication (ADR-021) — **COMPLETE (2026-08-16)** — High — *Delivered in two halves: the audit under the sprint label "PH3.8 — Analytics & Data Integrity", the removal under "PH3.9 — Mock Removal & Production Data Integrity". Report: `docs/architecture/ANALYTICS.md` (§11 is the removal record).* **Numbering note:** the briefs called these PH3.8 and PH3.9; this tracker's PH3.8 is *Accessibility & Responsive Audit* and PH3.9 is *End-to-End Critical Journeys*, both untouched. Same brief-label drift as PH3.2–PH3.7. **There are no MOCK metrics left in the product: 17 → 0.** Totals moved 4 REAL / 26 DERIVED / 17 MOCK / 5 UNAVAILABLE → **4 / 32 / 0 / 17**. Six became real numbers (DAU, signup growth, external API health, AI provider latency and failures, Redis and scheduler status, the dashboard health badges) and eleven became explicit UNAVAILABLE. `test_no_metric_is_classified_mock` asserts the zero; `test_every_ph38_mock_records_what_ph39_did_to_it` asserts all seventeen record what happened to them, so neither the removal nor its record can drift. **The rule that governed it — never replace mock data with fake realistic data — meant doing less than the inventory asked in three places, and each would otherwise have swapped a fabricated number for a systematically wrong one.** **(1) MAU:** the inventory prescribed a 30-day distinct-user query over `db.sessions`; that collection has a TTL index deleting a session one refresh lifetime after last use (7 days by default), so the query returns a 7-day count under a 30-day label, undercounting more the longer ago a user churned. `analytics.sources.active_users` checks the window against the retention horizon and refuses it — self-correcting, since raising `JWT_REFRESH_TTL_SECONDS` past thirty days makes the same call answer. **(2) API health:** "rewire" could not apply to the row list, which named *vendors* with individual latencies while the Market Gateway deliberately hides which upstream served a request (`MARKET_DATA_ARCHITECTURE.md`) — only `market_data` and `news` have instrumentation call sites, the rest report `not_measured` rather than a green badge, and **the Razorpay row was deleted outright** (`status: "configured"` beside a 300ms latency for an integration that exists nowhere in the codebase). **(3) `ai_requests_today`:** rewiring it to the counter would trade a durable database count for an in-process counter that resets on every deploy and covers one worker of N; it was **renamed** to `chat_messages_today`, which is what it always counted. **Revenue is gated on whether a payment integration exists, not on whether `db.payments` is empty** — gating on emptiness is how the first stray document flips revenue back to "available" and reports it as fact, which is the same defect PH3.8 found in a new implementation. The gate is one named predicate; the aggregation behind it is written and tested now, including that created/pending/authorized are intents rather than revenue. **The most dangerous removal was not an admin number:** `_synthetic_backtest` drew its win count from `randint(10, 16)` of 20 — so the win rate was always 50–80% and a losing strategy could not be represented — then passed the result through the *same* `_compute_metrics` as the real path, so an invented Sharpe ratio and drawdown rendered in the same UI cards; it was reached on **any** yfinance failure, so a network blip produced flattering fabricated performance. Deleted; the endpoint answers 503. **D-4 fixed** (carried from PH3.5): the refund endpoint returned `{"success": true}` for any string while writing `payment.refunded` to the immutable audit log — now 501 and **no audit record**, because a log containing invented events is misleading rather than merely weak. **Five further defects fixed** (ANALYTICS.md §13.1). **The frontend was where this could have been silently undone:** one `{stats?.mrr || 0}` turns `null` into `₹0` in the same typeface as a measured figure with no test failing, so every admin metric routes through one `MetricValue` component and the tests assert **the absence of `₹0`** rather than the presence of an em-dash — with the converse asserted too, since a genuine `0` is a measurement. **One index added** (`sessions {last_used_at, user_id}`, pinned in `HOT_QUERIES`); query costs counted in tests rather than assumed — DAU is 1 query flat in session count, growth 2, every revenue metric **0** because the gate short-circuits before touching the database. **Tests: +109 backend (2,425 → 2,534 green), +20 frontend (375 → 395 green); PH1 security 452 unchanged; production build clean.** **Remaining unavailable, with owners:** everything revenue-shaped is one payment integration (MRR/ARR additionally need subscription records — a one-off capture is not recurring revenue); retention, MAU and feature adoption need a durable activity or event stream and **none is back-fillable**; AI cost needs token accounting; profit factor and net P&L need per-fill broker charges.
- [x] PH3.3 Frontend Test Foundation & Smoke Suite — **COMPLETE (2026-08-10)** — Critical — *Delivered under the sprint label "PH3.2 — Frontend Testing & UI Regression Foundation"; report: `docs/testing/PH3.2_FRONTEND_TEST_CERTIFICATION.md`.* **Numbering note:** the sprint brief called this PH3.2, but this tracker's PH3.2 is *Mock Data Eradication*, which remains NOT_STARTED and untouched — read "PH3.2" in `docs/testing/` as this line item. **313 tests / 17 suites, green in ~8s**, against a bar of ≥15 smoke tests. **Jest 27 + React Testing Library 16 through `craco test`** — the runner already inside `react-scripts`; no second framework introduced. **Vitest, the previously documented target, was rejected on evidence:** it runs tests through esbuild while this app ships through webpack/CRA, so the suite would validate a transform that never reaches production. **MSW was also rejected** — CRA 5 / Jest 27 predate `package.json#exports` resolution and MSW v2 is exports-only ESM needing Web-streams polyfills under jsdom; interception happens instead at the **axios adapter**, the app's real transport boundary, so the bearer-token and 401-silent-refresh interceptors run for real in every test. **Covered:** authentication (login, register, logout, session restore, expired session, Google OAuth callback incl. missing/rejected `state`), routing and guards **driven off the real route table** (`AppRouter` exported from App.js) so a deleted guard fails the suite, admin access control (non-admin and signed-out both bounced; `admin` and `super_admin` both admitted), dashboard shell, paper-trading order entry, AI workspace, watchlist, notifications, admin dashboard, and the realtime store's reducers — every critical screen asserted in **all four states: loading / success / empty / error**. **Coverage baseline: 33.6% overall statements / 77.0% critical-path**; `services/api.js` **100%**, `Login.jsx` **100%**, `AIAssistant.jsx` **100%**, `formatters.js` **100%**, `AuthContext.jsx` 97.6%, `PaperTrading.jsx` 96.1%, `tradeService.js` 94.3%. Overall is low **by design** — it counts ~30 feature pages this sprint did not scope (Portfolio, TradeMonitor, StockDetail, Markets, News, Settings, ten admin pages); inflating it with shallow render-smoke tests was explicitly declined. **Five frontend defects found and fixed:** (FE-001) `formatApiError(detail) || err.message` could never reach `err.message` because the left side always returned a non-empty string — every client-thrown message, including "Google sign-in is unavailable right now.", was silently replaced by a generic one; fixed by extracting the duplicated-and-drifted helper from Login/Register into `utils/apiError.js`, which also now distinguishes transport failures from application errors. (FE-002) auth error banners carried **colour only** — no `role="alert"` — so a screen-reader user got no signal that sign-in failed. (FE-003) **paper trading rendered a failed load as an empty account** — zero balance, "no open paper trades" — which a trader reads as *my positions are gone*, not *the server is down*; now an explicit error state with retry. (FE-004) form labels on Login, Register and the order ticket were **visually** present but not programmatically associated (no `htmlFor`/`id`). (FE-005) icon-only controls (chat send, watchlist remove, notification close) had **no accessible name**. **One pre-existing defect found and deliberately NOT fixed** (FE-007, out of sprint scope, documented for the owning sprint): **`yarn build` fails** at `[eslint] Failed to load config "react-app" to extend from` — `eslint@^9` in devDependencies displaces the `eslint@^8`/`eslint-config-react-app@^7` that `react-scripts` requires. **Attribution verified rather than assumed:** stashed to pristine pre-sprint `package.json`/`yarn.lock`, reinstalled from the lockfile, reproduced the identical failure. The application compiles cleanly — `DISABLE_ESLINT_PLUGIN=true yarn build` succeeds with all PH3.2 changes. **Regression: PH1 + PH3.1 backend suite re-run green — 1,035 passed, 95 deselected, 152s**, exactly the PH3.1 baseline. **Carried, not fixed:** CI frontend job still a placeholder (PH2.6 wiring), no E2E layer (PH3.9), no coverage gate (PH3.11), and the silent-load-failure pattern fixed in PaperTrading **still exists** in Dashboard, Watchlist, AdminDashboard and NotificationPanel (FE-006) — pinned by tests at current behaviour so the deferred fix has a starting point and cannot regress further.
- [ ] PH3.4 Frontend Service & Hook Coverage — NOT_STARTED — Medium
- [x] PH3.5 API Contract & Error-State Testing — **COMPLETE (2026-08-10) — CERTIFIED** — High — *Delivered under the sprint label "PH3.3 — Backend Tests & API Coverage"; report: `docs/testing/PH3.3_BACKEND_TEST_CERTIFICATION.md`.* **Status corrected 2026-08-14 during PH3.7:** this line read NOT_STARTED while `PRODUCTION_ROADMAP.md` already recorded the sprint as complete and certified — tracker drift from the PH3.3 numbering split, not an incomplete sprint. 1,115 new tests took the backend from 1,035 to 2,150; 201 routes inventoried; authz asserted **mechanically** off the live route table (126 authenticated × anonymous + forged-token rejection, 29 admin × non-admin rejection, horizontal escalation on every user-owned collection); eight defects found, six fixed, D-4 → PH3.9 *(fixed 2026-08-16)* and D-10 → next auth-touching sprint. See the roadmap entry for the full record.
- [ ] PH3.6 Backend Decomposition (server.py → Routers) — NOT_STARTED — Medium
- [x] PH3.7 Performance Benchmarking & Load Testing — **COMPLETE (2026-08-14) — BOTH HALVES CERTIFIED** — Medium — *Delivered under the sprint label "PH3.4 — Performance Engineering & Optimization"; report: `docs/performance/PH3.4_PERFORMANCE_CERTIFICATION.md`.* **Numbering note:** the brief called this PH3.4, but this tracker's PH3.4 is *Frontend Service & Hook Coverage*, which remains NOT_STARTED and untouched — read "PH3.4" in `docs/performance/` as this line item. **The application code was measured not to be the bottleneck** — no prioritised endpoint's own logic exceeds **11 ms** steady-state. Two other layers were, and neither was visible from the code. **(1) Four collections had no index of any kind** (`watchlist`, `holdings`, `orders`, `payments`), backing the most-visited pages: `GET /api/watchlist` examined **2,000 documents to return 5**; every `/api/portfolio*` route **4,800 to return 12** — and the cost scaled with *total signups*, not with the caller's own data, which is the shape that looks healthy in development indefinitely and then does not. Sharpest case: the AI-chat continuity lookup (`server.py:488`) filters on `session_id` **alone**, and a compound index is only usable from its leading field, so the existing `{user_id, session_id}` could not serve it — **every message a user sent to the AI scanned all of `chat_messages`**, 12,000 examined to return 10. Seven further hot queries filtered on an indexed field then sorted on an unindexed one, producing blocking in-memory `SORT` stages that MongoDB **aborts past 100 MB** — a user with a long trade history was heading for a hard failure, not a slow page. Fixed with **12 indexes across 6 collections**: 400×–2,000× fewer documents examined, 6 of 7 sorts now index-served, and the unread-notification badge became a covered `COUNT_SCAN` touching **zero** documents. `ensure_indexes()` was **extracted from the 160-line `startup()` handler** so the index set can be asserted at all — the in-memory double has no query planner, so an unindexed collection passed all 2,144 existing tests exactly as a perfectly indexed one did. **(2) Every provider call opened a new TLS connection:** `fetch_yahoo_quote` runs once per symbol under `asyncio.gather` and each call built its own `httpx.AsyncClient` — measured **803.8 ms → 236.2 ms (3.40×)** through the application's own `real_quotes_map` after introducing loop- and timeout-keyed pooled clients (`services/http_client.py`), which also **bounds** provider concurrency for the first time (`max_connections=20`; previously unlimited). Layer attribution: **>90% of quote-enriched endpoint latency was provider transport**, so tuning those handlers' Python would have moved a term never worth more than 7%. Also **`/api/admin/logs` N+1 removed** (31 → 7 queries; 201 → 7 at a 200-row page) and the admin dashboard's **11 independent counts gathered** (11 serial round trips → 1). **The places nothing changed are part of the result:** no frontend optimization was warranted — route splitting already complete, all **13** polling timers already disconnected-only fallbacks (verified by measuring **zero** requests over 70 s with the socket live), no duplicate request per mount; Redis needed nothing (Sprint R9's `MGET` batching was already the best change available); no blocking operation in any request path; `recharts`' transitive `@reduxjs/toolkit` looked like an easy 280 KiB win and is not removable; `framer-motion` in the entry chunk is **correct**. **Two findings deliberately deferred with measurements and owners:** the rate limiter's `update_one`-then-`find_one` could become one atomic `find_one_and_update`, removing a query from **every request on all 201 routes** while also closing a documented non-atomic race — but it is PH1-certified security surface, and a performance sprint is the wrong place to rush a limiter change (→ next security-touching sprint); and whether `fetch_yahoo_quote` needs 3 months of history for a 14-period RSI is an indicator-accuracy question, not a performance one. **38 regression tests, none asserting wall-clock time** (a timing assertion measures the CI runner): query counts asserted **identical at 3 rows and 33** — the N+1 *signature*, which unlike a pinned constant cannot be "fixed" by updating the number — index coverage recorded by running `ensure_indexes()` against a stub rather than parsing source, payload bounds, gather structure, per-request floor, and a counter-test proving Watchlist *does* poll when disconnected (without which "no polling while connected" would pass just as happily if every timer had been deleted). **Two of the sprint's own measurements were wrong before they were right, and are documented as method notes:** a corpus typo (`target_1` for `target1`) manufactured a `KeyError` that looked exactly like a HIGH defect on `/api/portfolio/intelligence`, and a frontend test drove a store field no selector reads *and* set it before `RealtimeProvider` overwrites it, manufacturing a polling defect that does not exist — **neither was reported as a finding.** **Acceptance criterion "API p95 < 500 ms on staging" is NOT met because it was not measurable:** no staging deployment exists, so no p95, LCP or concurrency figure was produced; **six metrics are marked explicitly *unavailable* rather than estimated** (Redis timing, LCP/RUM, production percentiles under load, Socket.IO fan-out at scale, AI provider latency, isolated serialization time). **No regression:** backend 2,144 → **2,176 passed**, PH1 security **452 unchanged**, frontend 313 → **319 passed**, production build green, bundle byte-identical, no API contract / trading logic / AI decision logic / prompt / model selection touched. **Load-testing half delivered the same day** under the sprint label "PH3.5 — Load Testing & Capacity Validation"; report: `docs/performance/PH3.5_LOAD_TEST_CERTIFICATION.md`. **Numbering note:** this tracker's PH3.5 is *API Contract & Error-State Testing* (complete, separate line above) — read "PH3.5" in `docs/performance/` as this line item. **Neither the application code nor MongoDB is the constraint under concurrency:** from 5 to 100 VUs, **zero 5xx, zero timeouts, 100% of functional checks**, median flat at **10.9 ms → 8.3 ms**, Mongo queue depth 0 throughout, and PH3.4's 4–5 query floor held at **3.9–4.6 across a 65× load range**. Six of PH3.4's seven claims confirmed; the seventh corrected. **Three P1 findings, none of which a single-request measurement could see. (L-1)** `REDIS_MAX_CONNECTIONS` defaults to **24** — below the app's own fan-out width, since a watchlist request performs one `cache_get` per symbol — and **redis-py's pool raises rather than queues when exhausted**; five failures open a **process-wide** circuit breaker that degrades *the whole cache* to the in-process fallback for 10 s, during which every quote misses and goes upstream. p95: 21 ms @100 rps → 187 @150 → 515 @200 → **10,485 @250**. The same sweep at `REDIS_MAX_CONNECTIONS=200`: **11.1 ms @250, 29.1 ms @400, zero Redis failures at every rate** — **~217 → ~410 rps sustained, 1.9×, no code change** — leaving an honest ceiling of **100.0% of one CPU core**. **(L-2)** `ConnectionManager.broadcast()` iterates `self.active` directly while awaiting `ws.send_text` inside the loop; at 200 sockets / 14,057 churn cycles it raised `RuntimeError: Set changed size during iteration`, **silently dropping a market broadcast to every client past the mutation point** and skipping the event-bus publish after it — one line, and the sibling `broadcast_to_channel` already does it correctly. **(L-3)** `verify_password` (bcrypt cost 12, **234 ms**) runs **synchronously on the event loop**: login pinned at **~4/s at any concurrency**, and the proof is `/refresh` and `/logout` — no bcrypt, 3–4 ms floor — reaching **1,670 ms / 1,430 ms** medians at 25 users because they queue behind it. **This corrects PH3.4 §13's "no synchronous blocking operation in an async request path".** **What held:** provider failure fully contained (30% errors / 10% timeouts / +800 ms on market, 6 s + 20% 429 on AI → **zero 5xx, zero timeouts in every phase**; `api` p95 stayed 30.5 ms while `ai` p95 sat at the injected 6,152 ms); rate limiting exact at its boundary (120 then 429 with `Retry-After` on 100% of rejections, **0 of 39** bystanders affected); the 60 s quote cache collapsed **7,044 quote-enriched requests into 583 upstream fetches (91.7%)** with **no thundering herd at TTL expiry** — **the single-flight gap flagged above as the most likely load finding did not materialise at ≤100 VUs** and remains theoretical at higher fan-out and under multiple workers, where each holds an independent cache; 150 sockets held 75 s with zero errors, 2 ms ping→pong p95. **Capacity with its constraint named:** ~100 rps safe on the shipped Redis pool, ~300 rps with `REDIS_MAX_CONNECTIONS≈100`, hard ceiling ~410 rps CPU-bound on one worker, and **login separately pinned at ~4/s per worker** — the figure to plan a launch spike or mass re-auth around. **Explicitly not a claim that the product supports *N* users.** **One application change, inert by default:** `yahoo_origin()` reads `MARKET_DATA_YAHOO_BASE` at call time and is byte-identical to the previous URLs when unset; all seven Yahoo call sites route through it; the AI path needed no change (the SDK reads `ANTHROPIC_BASE_URL`). A harness monkeypatch was rejected because it would exercise a code path production does not have, making the measurement non-transferable. **12 tests pin both halves — inert by default *and* actually effective when set**, because a working provider and a working mock produce the same green result; **verification did not trust either mechanism**, every outbound TCP connection during a run was enumerated and all were loopback. **Nothing found was fixed** — changing code mid-sprint invalidates every measurement taken before it. Also: **L-6 — there is no Redis-backed rate-limit store, only Mongo**, which this tracker and PH3.4 §21.5 both imply otherwise; **S-1** `X-Forwarded-For` trusted with no proxy check (anonymous tier bypassable); **L-4** multi-worker scaling untested. **No security control was weakened for any number:** rate limiting stayed on even for the saturation search where disabling it would have raised the ceiling, no XFF spoofing was used although it would have worked, bcrypt cost 12 was not lowered. One accidental exposure — a first boot inheriting `backend/.env` — was found, closed with `PYTHON_DOTENV_DISABLED=1`, and the rows it wrote removed and verified gone. **Two of the sprint's own results were wrong before they were right:** a 4.4% "error rate" that was the risk engine correctly refusing over-drawn paper orders, and 83 CSRF failures that were the harness reusing a token the server had **correctly rotated** on refresh — neither reported as a defect. **No load test was added to PR CI, deliberately** (a latency threshold on a shared runner goes red when the runner is busy and green on a fast runner that just regressed); a scheduled `baseline` against staging plus manual `workflow_dispatch` are handed on. **Still owed from this roadmap item's original scope:** **Lighthouse CI and the CI bundle budget** (frontend paint metrics were never in either half). **No regression:** backend 2,176 → **2,188 passed**, PH1 security **452 unchanged**, frontend **319 passed**, build green, bundle unchanged within noise.
- [x] PH3.7b Memory & Resource Stability — **COMPLETE (2026-08-15) — PASS WITH CONDITIONS** — High — *Delivered under the sprint label "PH3.6 — Memory & Resource Stability"; report: `docs/performance/PH3_MEMORY_STABILITY.md`.* **Numbering note:** this tracker's PH3.6 is *Backend Decomposition (server.py → Routers)*, which remains NOT_STARTED and untouched — this line is a NEW item, because the roadmap never carried a memory/resource sprint at all; it exists because PH3.7's load-testing half explicitly handed one forward (`PH3.5_LOAD_TEST_CERTIFICATION.md` §25). **PH3.5 advised starting from "no leak is visible at these durations"; that advice was correct about its own data and wrong as a conclusion.** RSS is the wrong instrument for the leaks this application has: both P0 findings are dicts that gain a few hundred bytes per event, which is less than the noise between two idle RSS samples, so PH3.5's flat memory curve was accurate *and structurally incapable* of showing either. **A leak is a shape — a count that only ever rises — not a size.** Counting entries instead of bytes found in the first hour what 150,000 requests of throughput testing could not. **M-1 (P0): `ConnectionManager.user_connections` retained a dict key per connection forever**, in both the clean path (`disconnect`) and the dropped-connection path (`_reap`) — and the key is `websocket.query_params["user_id"]`, which **nothing authenticates** (S-2 → PH1.9), so an anonymous caller can mint one per connection: **1,000 clean cycles left 1,000 empty sets, 500 dirty disconnects left 500**, and only a process restart ever emptied it. **M-2 (P0): `ai_context_builder._cache` checked its 8s TTL on read and evicted nothing**, retaining a multi-KB `ChatContext` (rendered markdown + the structured sections behind it) per user for the life of the process — **5,000 users, every entry 999s stale, 5,000 live entries**; now bounded at 512 with sweep-then-evict, deliberately the same idiom as `services/cache.py`. **M-3 (P1) confirms and fixes PH3.7's L-2** — `broadcast()` iterated the live set across an `await` (`RuntimeError: Set changed size during iteration` reproduced); the exception is the *lucky* outcome, the unlucky one is every socket past the mutation point silently missing the message and the following event-bus publish never happening. `send_to_user()` had it too. **M-4 (P2): four perpetual loops had no shutdown path at all** — bare `create_task` with the result discarded, so no strong reference (asyncio keeps only a weak one) and nothing to cancel; `shutdown()` was closing the scheduler, broker streams, Redis, the HTTP pool and the Mongo client **while all four kept running against them**, and both heartbeat loops read Mongo, so every clean stop emitted a burst of errors that reads like a crash. New `backend/infrastructure/tasks.py` supervises them: strong reference released on completion, one task per name (closing a refused coroutine rather than leaking the frame), bounded 5s cancellation, crashed tasks logged with tracebacks; shutdown now cancels producers **before** their dependencies. **M-8: MongoDB `maxIdleTimeMS` was unset** — pooled connections never reaped when idle, a pool that only ratchets up — now 60s, with every other option made explicit and env-overridable at its existing value, closing the gap PH2.8's displaced-to-PH2.8b "connection-pool sizing documented" left open. **`socketTimeoutMS` deliberately NOT set:** no read timeout means a wedged primary holds a request forever, but the number requires the slowest legitimate production query and one picked from a laptop would abort real work — wired to `MONGO_SOCKET_TIMEOUT_MS`, carried as an open risk. Also fixed: **M-5** two unbounded per-user throttle maps; **M-6** `BrokerStreamManager` retaining a finished stream *and the expired broker access token inside it*; **M-7** `start_event_bridge` registering the catch-all `"*"` handler unconditionally (a second call would double every event forever); frontend **F-1** `tradeLive.byId` merging onto the previous map although every producer publishes the *complete* open set — a closed trade was retained forever **and shown as open**; **F-2** unbounded multi-KB AI trade reviews; **F-3** an `aiRuns` cap that never evicts an `active` run, so a socket dropping mid-run defeats the cap permanently. **What was checked and found correct is part of the result:** `infrastructure/redis_client.py` and `redis_pubsub.py` have **no defect** and are recorded as the reference the rest should look like; every Mongo cursor uses an explicit `to_list(N)`; metric cardinality is route-templated with an overflow series; and the **entire frontend timer/listener/observer/GSAP surface is clean** — 13 `setInterval` sites, 6 `addEventListener` sites, one `ResizeObserver`, every GSAP context, all with matching cleanup, and a `RealtimeProvider` that cannot accumulate handlers by construction. **Six new gauges** (`websocket_tracked_users`, `websocket_connections`, `background_tasks_running`, `event_bus_subscribers`, `app_cache_entries{cache=…}`) make the bounds observable — both P0 leaks grew for a process's whole life without appearing on any dashboard; **the alert worth writing first** is `websocket_tracked_users` holding a floor above zero while `websocket_connections` is at zero, which is M-1's exact signature. **Two new instruments:** `backend/scripts/resource_probe.py` (in-process, exits non-zero on a retained structure or a cache over its ceiling) and `scripts/load/soak.sh` (samples `/api/metrics` every 30s for the **whole** run plus an idle settle window — a before/after pair cannot distinguish "grew and came back" from "never grew"). **The caches sitting exactly at their ceilings is the result, not a warning:** each was driven at 3–10× its bound, and landing *at* it is the only evidence the eviction path runs — the same class of claim as PH2.12's stub that agreed with a bug. **Every regression test was verified to fail on the old code: 18 of 26 failed** against the pre-sprint tree; the 8 that passed are the 6 covering the new task registry and 2 deliberate counter-tests asserting *preserved* behaviour, without which deleting `_stamp`'s body would satisfy every ceiling assertion. **One of the sprint's own measurements was wrong before it was right and is documented:** the first soak reported samples on schedule for six minutes while k6 never ran, because `pid="$(start_sampler …)"` blocks until the backgrounded subshell closes stdout — it was measuring an idle server. **No regression:** backend **2,188 → 2,216 passed** (6 xfail unchanged), PH1 security **452 unchanged**, frontend **319 → 324 passed**, production build green; no trading logic, AI decision logic, prompt, model selection, API contract or design-system change. **Five conditions, all environmental:** `MONGO_SOCKET_TIMEOUT_MS` to be baselined in staging; multi-worker behaviour unmeasured (the resource budget is **per worker**); multi-day operation unmeasured; Mongo TTL reaping of `sessions`/`rate_limits` under sustained write rate unmeasured; frontend bounds asserted structurally rather than heap-profiled.
- [x] PH2.10b Monitoring & Observability (subsystem instrumentation, alert catalogue) — **COMPLETE (2026-08-15) — CERTIFIED** — High — *Delivered under the sprint label "PH3.7 — Monitoring & Observability"; report: `docs/architecture/OBSERVABILITY.md`; operator manual updated to `docs/operations/MONITORING.md` v1.2.* **Numbering note:** this tracker's PH3.7 is *Performance Benchmarking & Load Testing* (complete, separate line above); this sprint delivers **the outstanding half of roadmap PH2.10 — Monitoring, Metrics & Alerting** plus scope PH2.10 never named, so it is filed here rather than under a new PH3 number. **The audit changed the shape of the sprint, and that is the headline.** A monitoring brief usually assumes there is no monitoring; here PH2.5 had already delivered structured JSON logging, request correlation, three health probes and the four golden signals, PH2.6 stream separation/rotation/retention, PH2.7 nine Redis families, and PH3.6 six resource gauges — **brief Steps 3, 4 and most of 5 were already satisfied, and rebuilding them would have been the worst available outcome.** What was actually missing is that **instrumentation stopped at the process boundary**: MongoDB had one bit (`dependency_up`) and no latency, no failures, no pool visibility; WebSockets had gauges but no *flow*, so 200 connections and a churn of 200 reconnects looked identical; **background tasks, market-data providers, broker APIs, news, AI providers and the event bus had nothing at all.** The operator question "which subsystem is failing?" had no answer anywhere in the system. **The keystone is one metric:** `subsystem_errors_total{subsystem,error_class}`, which every failure path now writes to, with both labels closed vocabularies (14 subsystems, 13 error classes) so the metric everything writes to is also one that cannot grow unbounded. **`backend/observability/errors.py` classifies by MRO name-matching, not `isinstance`** — the obvious implementation would make the module every subsystem depends on import `pymongo`, `redis`, `httpx` and `anthropic`, invert the layering, and fail at import wherever an optional client is absent; string matching needs no imports, cannot raise, and degrades to `internal`. **Two rules resolve the overlaps:** *subsystem wins over failure mode* (`ServerSelectionTimeoutError` is `database`, not `timeout` — "MongoDB is unreachable" routes to an owner, "something timed out" routes to nobody; `is_timeout()` is the retry escape hatch), and *cancellation is classified but never counted*, since a clean shutdown cancels every in-flight operation and counting those makes every deploy look like an incident. **MongoDB is instrumented through the driver, not the call sites** (`observability/mongo_monitor.py`, a `CommandListener` + `ConnectionPoolListener` on the client): there are several hundred `await db.<collection>.<op>()` calls, and any wrapping scheme covers what was written before the sprint and nothing after — the first query someone adds *during an incident* is exactly the invisible one. **It reads the command name and duration only**: `CommandStartedEvent.command` is the full BSON document, carrying a user's email in a login lookup, a bcrypt hash in a password update, a broker access token in a credential write; `CommandFailedEvent.failure` is reduced to its integer `code` through a fixed table because `errmsg` embeds the failing query and, on a connection fault, **the credentialed connection URI**. The pool listener earns its own place: **pool exhaustion is the MongoDB failure that produces no errors at all** — every command waits, every percentile rises together, nothing is logged — and `checked_out` vs `max` is the only direct evidence; PH3.6 fixed `maxIdleTimeMS` (M-8) and left the pool unmeasured. **One design decision turned on a measurement:** a fan-out to 500 sockets four times a second is 2,000 sends, and at the measured 0.52 µs per increment, counting per recipient is ~260 µs per broadcast **growing with every connected user**; fan-outs are therefore counted **once per fan-out** with failures added in one sized increment — **0.6 µs, constant in audience size**. **Three counters cover failures that leave every other panel green**, which is the category this sprint most justified itself on: `provider_requests_total{outcome="empty"}` (a provider answering 200 with no rows — zero error rate, yesterday's prices on screen), `ai_requests_total{provider="simulated"}` (every real model failed, the user got a plausible canned answer, and the **request succeeded**), and `frontend_errors_total` (a blank page, from a bundle request that returned 200 minutes earlier). **The AI providers needed an explicit failure report:** each catches broadly and returns `AIResponse(error=...)` rather than raising — correct, so an outage degrades the feature instead of failing the request — which means a tracker watching only for exceptions would have recorded **a total provider outage as 100% success**; `track_ai(...).failed(detail)` closes it, and the error *string* only picks a class, never reaching a label, because a provider message can carry a request id, an account identifier or an echoed prompt. **The frontend had no error boundary at all** — since React 16 an uncaught render error unmounts the whole tree, a white page with the cause only in a console the user will never open and no server-side trace of any kind. Added: two boundary levels (outer around the providers so a throw inside `AuthProvider` is caught; inner keyed by pathname in both layouts so a page crash keeps the shell and the user has a route out that is not the back button), global `unhandledrejection`/`error` handlers, and chunk-load detection with **exactly one** auto-reload per tab — a stale `index.html` is fixed by one reload, and a second is an infinite refresh against a failing origin from every affected browser at once. **In production the boundary shows no message and no stack**, because a React error message can quote component props, which here means positions, prices and account values. **Client telemetry is small and paranoid:** no Sentry, no replay, no analytics; reports reach this app's own `POST /api/observability/client-errors` and become a counter plus one log line with **no database write** (an unauthenticated endpoint that inserts a document is an unauthenticated write amplifier). It is unauthenticated *by necessity* — the failures most worth hearing about are the ones where the app could not start — so it is treated as hostile input: closed `kind` vocabulary, hard field caps, newline stripping against log-line forgery, and a CSRF exemption justified twice (it changes no state, and `sendBeacon` cannot set a header). The client sends **only the id-normalised pathname**, never `search` or `hash`, because a query string here carries OAuth codes, broker callback tokens and recovery tokens; it reads nothing from `localStorage`; and a **20-report session cap plus signature dedup runs before anything is sent**, because a render loop throws thousands of times a second and an error inside a reporting path is the classic self-inflicted DoS. **Two defects found in this sprint's own work, both by its own tests. (1)** PyMongo validates listeners with `isinstance`, not duck typing, so the first implementation's duck-typed listeners were rejected at client construction with a `TypeError` — which would have taken **the entire application down at import, in every environment, immediately**; caught on the first test run, and `test_the_listeners_satisfy_pymongos_type_check` now exists so it cannot regress. **(2)** A redaction sweep found four `record_*` helpers accepting outcome/reason/kind labels as unvalidated strings; every current call site passes a source literal so nothing leaked, but "every current call site" is not a property — frozen sets plus a `_bounded` helper that logs `instrumentation_defect` and folds to `<unknown>` makes it one. **Verification: backend 2,216 → 2,303 passed** (delta is exactly the 87 new tests, zero regressions, 6 xfail unchanged), **frontend 324 → 364 passed / 20 suites** (exactly the 40 new tests), production build green, flake8 clean on every changed file with all twelve pre-existing findings verified against `HEAD`. A **61-check failure-injection drill** against the real application: Mongo killed → readiness 503 while **liveness stays 200** and recovers when Mongo returns; configuration invalidated → 503 naming no secret, only a count; every new family exposed and well-formed; scanner probes collapsing to one `<unmatched>` bucket; and secret-shaped strings driven through every free-text path and asserted **absent** from the rendered exposition document. Redaction under **production posture verified separately** (11/11): `detail == "ServerSelectionTimeoutError"`, no password, no host, no scheme. **One drill check fails environmentally and is documented:** `TestClient` runs each request in a fresh event loop while Motor binds to its creating loop, so the second Mongo-touching request raises `Event loop is closed` — a pre-existing harness artifact (it also produces the rate-limiter tracebacks in the drill output), proved unrelated by re-running the identical recovery sequence inside one loop, where pass → fail → pass. **Overhead was measured, not asserted,** and `backend/scripts/observability_overhead.py` is committed so the figures can be re-derived: counter increment **0.49 µs**, classification **0.92 µs**, the always-on Mongo listener **2.02 µs per command** (~0.02–0.2% of a command that just did network I/O), `/api/metrics` render **0.76 ms**. **These are single-threaded microbenchmarks and are labelled as such** — they cannot show lock contention under concurrency, the only mechanism by which any of them could matter, and that needs the staging environment this project still lacks. **The honest gap: nothing watches any of it.** OBSERVABILITY.md §9 defines 6 critical and 22 warning conditions, each with threshold, severity, expected response and false-positive analysis — but there is **no Prometheus, no Alertmanager, no notification channel and no uptime check**, so detection remains manual and dominates RTO, and **every threshold is an engineering estimate** because no staging baseline exists to derive one from. Both carry to roadmap PH2.10/PH2.12 as required-before-production.
- [ ] PH3.8 Accessibility & Responsive Audit — NOT_STARTED — Medium
- [ ] PH3.9 End-to-End Critical Journeys — NOT_STARTED — High
- [ ] PH3.10 Documentation Synchronization — NOT_STARTED — High
- [x] PH3.11 Regression & Release Test Protocol — **COMPLETE (2026-08-17) — READY FOR PH3.12 CERTIFICATION** — High — *Reports: `docs/production/PH3.11_RELEASE_CANDIDATE_REPORT.md` (regression, 30 sections) and `docs/production/PH3.11_REMEDIATION_REPORT.md` (blocker B-1 closure, 10 sections); protocol: `docs/qa/RELEASE_TEST_PROTOCOL.md`.* **The sprint ran in two passes: regression found one blocker, remediation closed it by fixing rather than suppressing.** **Remediation outcome (2026-08-17):** all **6 Python advisories FIXED** — `aiohttp` 3.14.1 → **3.14.3** and `cryptography` 48.0.1 → **50.0.0**. The cryptography jump crosses two majors and was taken on evidence, not assumption: no dependent caps it (every constraint is a lower bound), the application's entire API surface is one `from cryptography.fernet import Fernet` import, and **a Fernet token encrypted under 48.0.0 decrypts correctly under 50.0.0** — verified directly, because existing broker tokens in a production database must stay readable. **7 of 18 npm advisories FIXED** via patch-level `overrides` (brace-expansion 1.1.18, fast-uri 3.1.5, js-yaml 4.3.1, nanoid 3.3.18, underscore 1.13.8) plus the direct `postcss` devDependency 8.4.49 → 8.5.26: **18 high → 11**. `resolutions` is declared alongside `overrides` because `package.json` names yarn as the packageManager while CI uses npm, and **yarn 1 ignores `overrides` entirely** — a fix that silently fails to apply under the declared tool would be worse than none. **8 dead suppressions deleted** — they named `litellm` (7) and `ecdsa` (1), packages already removed from `requirements.txt`, suppressing nothing while the CI summary still advertised them as pending. **A third, previously unrecorded defect was found during remediation:** the dev-requirements audit step ran with **no suppressions** over a file whose line 17 is `-r requirements.txt`, so it re-audited every runtime package and failed on the very advisories the runtime step deliberately accepted — **that job could never have passed since the day the suppression policy was written.** **New enforcement:** `.github/dependency-triage.yml` (machine-readable register covering both ecosystems: package, advisory, severity, reason, reachability, re-runnable evidence, mitigation, owner, expiry) enforced by `.github/scripts/dependency_audit.py`. Untriaged finding → fail; expired entry → fail with **no grace period**; **entry matching nothing → fail** (the check that would have caught the litellm/ecdsa rot); auditor unusable → **exit 2**, distinct from both pass and policy failure, because "the check could not be performed" must never read as "the check passed". **The old `SUPPRESSION_REVIEW_BY: 2026-08-22` was re-argued, not extended:** the blanket "pinned by fastapi" justification covering all 7 starlette advisories split 5/2 on re-triage — 5 are **structurally unreachable** (no form/multipart parsing anywhere, no `HTTPEndpoint`, no `StaticFiles`, and 2281 is a Windows-only defect on a Linux image) and move to 2027-02-15; **2 (PYSEC-2026-161/248) got a *shorter* leash than before — 2026-11-15** — because the app reads only `request.url.path` and never the reconstructed absolute URL, which makes them unreachable by convention rather than by control. **Gate proven by 8 negative tests**, each mutation reverted and the revert verified: expired entry fails, expiry date itself still passes, day-after fails, 30-day warning fires without failing, deleted entry → UNTRIAGED, bogus entry → STALE, downgrading `aiohttp` → 3 UNTRIAGED, removing an npm override → UNTRIAGED. **Full protocol re-run after every change, all reproducing baseline:** 2,559 backend / 0 failed / 4 xfailed, 452 security, 395 frontend / 22 suites, build exit 0 with 48 bundles / 14 MB, routes **97/29/75 = 201** unchanged, analytics **0 MOCK** of 53, trading-engine mutation check fails-then-reverts-clean, WebSocket P0 matrix fully closed (anonymous / spoofed `user_id` / query token / forged subprotocol all **403**; valid token + spoofed `user_id` binds to the token's subject), Redis loss → API still serving, Mongo loss → readiness **503** with liveness 200 and no leakage, **0 restarts**, shutdown **2 s exit 0**, **0** secrets in logs. Production image rebuilt `--no-cache --pull` (425 MB) with the compiled cryptography wheel and Fernet verified **inside the container**. One incidental confirmation: the first live boot **failed closed** because the supplied `METRICS_TOKEN` was 20 chars against a 32-char minimum — the config gate working, not a regression. **No application code was changed in either pass** — only dependency manifests, the CI workflow, new tooling and documentation. **Remaining: zero release blockers.** The eight PH3.10 conditions (C-1…C-8) stay open as deployment prerequisites, and two register deadlines are now real calendar commitments (2026-11-15, 2027-02-15). Recommend proceeding to PH3.12. *(Original BLOCKED record, preserved: `docs/production/PH3.11_RELEASE_CANDIDATE_REPORT.md` §26.)* — regression-pass detail follows: *(30 sections).* RC commit `32437e8` on `main`, working tree clean before and after.* **No code was changed** — the tree is byte-identical to the commit it started from, because **zero code regressions were found**. Every headline number reproduced the PH3.10 baseline exactly: **2,559 backend passed / 0 failed / 4 xfailed / 95 deselected** (2,658 collected), **395 frontend / 22 suites**, production build exit 0 with the same **48 bundles / 14 MB**, and the authorization surface classifying to precisely the same **97 protected / 29 admin / 75 public = 201** routes. `pytest -m security` 452 green. Verified against a **from-scratch `--no-cache --pull` production image** (424 MB, non-root uid 10001, no pip, no `.env` baked) running with `APP_ENV=production` against authenticated Mongo and Redis — not against prior reports. **PH3.10's P0 holds under live re-attack:** anonymous, spoofed `?user_id=<victim>`, query-string token and forged-subprotocol WebSocket handshakes all **403**; only cookie or `Sec-WebSocket-Protocol: stockassist.auth,<token>` connects, and a valid token plus a spoofed `user_id` binds to the *token's* subject. Session security is total — refresh rotates, replay returns 401 and **revokes the family**, and logout-all revoked 6 sessions with all three subsequent refreshes 401 **including the caller's**. Fault injection produced controlled degradation every time with **0 process restarts**: Redis loss keeps the API serving (readiness reports `redis: fail, critical:false`), Mongo loss flips readiness to **503** while liveness stays 200 and leaks nothing, and `docker stop` completes in **1 s, exit 0**, with all 4 background tasks stopped and every pool closed. Resource churn (60 sockets) returned every gauge to zero. Analytics read live from the registry: **4 REAL / 32 DERIVED / 0 MOCK / 17 UNAVAILABLE**. **The chartered stale test `test_run_cycle_trails_and_books_targets` was proven fixed rather than assumed** — exact-equality assertion intact, backed by consequence checks, and **mutation-checked**: injecting a spurious key into `run_cycle`'s return contract makes it fail (mutation reverted, `git diff` clean). **One release blocker, and it is not a regression: the repository's own `dependency-audit` CI workflow is red on both jobs, and no prior sprint ever ran it.** Backend `pip-audit` (with CI's 15 suppressions) exits 1 on **6 advisories against pinned runtime deps** — `cryptography` 48.0.1 ×3, `aiohttp` 3.14.1 ×3 — published after the suppression list was written; frontend `npm audit --audit-level=high` exits 1 on **18 high advisories with no triage mechanism at all** (the Python gate has a documented allowlist with mechanical expiry; the npm gate was never given one, so it fails unconditionally). **Reachability was analysed, not assumed, and changes the severity without changing the status:** `cryptography` is used only for Fernet — the codebase contains no `pkcs7`, no `x509.verification`, no `PolicyBuilder` — and `aiohttp` is **client-only**, ruling out the server-side smuggling advisory; of the npm 18, **zero** vulnerable packages reach the shipped bundle (verified by grep against `build/static/js/`; the `svgo` hits are minifier variable names). So the product is not known to be vulnerable — but a required gate is red, and those are different claims. **Deliberately not fixed here:** bumping `cryptography` across a major version or migrating off `react-scripts` is architectural work during a freeze, and suppressing the advisories to manufacture green is the "mark a failure as passed" outcome the brief forbids. **Also found: `SUPPRESSION_REVIEW_BY` expires 2026-08-22 — five days out.** Remediation for approval: **R-1** bump `aiohttp` 3.14.1 → 3.14.3 (patch-level, in-pin, precedent in `SECRETS.md` §8); **R-2** evaluate `cryptography` 48 → 49/50 in a dedicated sprint; **R-3** give the npm gate the Python gate's triage mechanism; **R-4** re-argue the suppression expiry. **Five P3 observations recorded, all pre-existing:** plain-HTTP `FRONTEND_URL` accepted in production (mitigated — cookies are forced `Secure`, so it breaks the session rather than downgrading silently); uvicorn's access log echoes caller-supplied query strings verbatim (the platform's own client never sends a token there); a Redis client-pool exhaustion transient at boot that self-heals in ~7 ms; Mongo outage surfacing as 500 rather than 503; and `source: yahoo_finance` disclosed in quote payloads. **Method note worth keeping: two of this sprint's apparent findings were artifacts of my own probes** — a logout-all test confounded by an unsaved rotated cookie, and a fail-closed matrix aimed at `validate_config()` when wildcard-CORS is stripped in `cors.py` and `COOKIE_SECURE` is forced in `cookies.py`. Both looked like security defects; re-running before reporting is what separated them from the real one. **Scorecard: 24 PASS · 5 PASS WITH CONDITIONS · 3 BLOCKED · 1 FAIL.** All eight PH3.10 conditions (C-1…C-8) remain open. Once R-1/R-3/R-4 land and `dependency-audit` is green, every other stop condition in the brief is already met.
- [x] PH3.12 Production Certification & Launch Readiness *(first pass)* — **SUPERSEDED (2026-08-17) — NO-GO; blockers closed by PH3.12R, re-certified 2026-08-18 (see PH3.12-RERUN below)** — Critical (final gate) — *Report: `docs/production/PH3.12_PRODUCTION_CERTIFICATION_NOGO_ARCHIVE.md` (30 sections; archived verbatim — the original filename now holds the 2026-08-18 rerun).* **No application code was changed** — the tracked diff hashes to `b2f4921d…b32725` at both the start and the end of the sprint. **Every headline baseline reproduced exactly:** 2,559 backend / 0 failed / 4 xfailed, 452 security, 395 frontend / 22 suites, build exit 0 with 48 bundles / 14 MB, routes **97/29/75 = 201**, analytics **0 MOCK** of 53 (`{real:4, derived:32, mock:0, unavailable:17}`), DB 19 collections / 61 indexes / 3 TTL. Verified against a **from-scratch `--no-cache --pull` image** (`stockassist-rc:ph312`, 425 MB, `sha256:f373296b…638b142`, uid 10001, pip absent, no `.env` baked, `--reload` present only in a comment forbidding it) — not against prior reports. **The PH3.11 dependency gate was re-proven, not accepted:** `dependency_audit.py --ecosystem all` → **exit 0** (7 python + 16 npm, all triaged), and the gate was shown to bite by **7 negative tests** — past-expiry fails, expiry date itself passes, warn window warns without failing, all-expired fails with 23 EXPIRED, deleted entry → UNTRIAGED, bogus entry → STALE, and **auditor-unavailable → exit 2** (observed accidentally before it was designed, which makes it better evidence). Register restored **byte-identical** (`6868a4e2…dcaa4cc`) after every mutation. **Live security posture strong:** WebSocket P0 matrix fully closed (anonymous / spoofed `?user_id` / query token / forged subprotocol all **403**; valid token + spoofed `user_id` binds to the token's subject); refresh rotates, replay → 401 and revokes the family; logout-all revoked **4** sessions including the caller's; CSRF 403 on missing *and* forged; rate limit `401×5 → 429×3`; JWT exactly 900 s with `iss`/`aud`; all three cookies `Secure`; **0** occurrences of all 7 configured secrets across 442 lines of live logs; Redis loss degrades without outage, Mongo loss keeps liveness 200 / readiness 503, both recover with **0 restarts**; 60 WebSocket connections accepted and fully released with every gauge back to 0; shutdown **2 s exit 0** with all 4 tasks stopped. **TWO NEW BLOCKERS FOUND — both are controls no prior sprint actually probed.** **B-1 — `PaperTradeCreate` (`server.py:5125`) performs no input validation at all.** `quantity: -1000` yields `total_cost: -1000000` which is **credited**, moving a paper balance from ₹86,840 to **₹1,086,840** in one request; negative `entry_price` and arbitrary `type` strings are also accepted. The canonical `TradeCreate` (`models.py:124`) enforces `gt=0` on quantity/entry_price/stop_loss/target1 and a `^(BUY|SELL)$` pattern, and correctly returns **422** for the identical payloads — the paper model, declared inline in `server.py` instead of beside it in `models.py`, was never given them. Scope verified and bounded: paper only, own data only (a second user's balance stayed exactly ₹100,000), no real money, no broker order, no authz crossed — but it falsifies paper P&L, the trade journal and per-user analytics, the numbers PH3.9 spent a sprint making truthful. **B-2 — `/docs`, `/redoc` and `/openapi.json` all return 200 anonymously under `APP_ENV=production`**, disclosing 188 paths, 23 admin routes and 26 schemas. **PH3.11 §9 certified this as 404** — that evidence was wrong, almost certainly probed at `/api/docs` (which *is* 404) rather than `/docs`, where FastAPI mounts them. Root cause is one line: `server.py:357` is `app = FastAPI(title="AlphaPartner API")` with no `docs_url=None` and no environment gating anywhere. No secrets in the schema and **authz fully intact** (all 23 admin paths return 401 anonymously), so intrinsic severity is moderate; it is a blocker because it was certified closed on evidence that was never true. **Neither blocker was fixed** — the brief forbids silently repairing a newly-found blocker mid-certification, and a fix applied during certification invalidates the artifact being certified. **Also found — L-1: the release candidate is an *uncommitted working tree*** (`32437e8` + unstaged PH3.11 remediation), so it cannot be checked out, tagged or reproduced, and DR assumption A6 (roll forward from a recorded commit) does not currently hold. **Method note carried forward:** one apparent finding was again an artifact of my own probe — three sessions showed unauthenticated *before* logout-all because eight earlier deliberate bad logins had tripped the rate limiter and the logins' 429s were never captured; re-run with a fresh identity and explicit status capture, revocation was total. **A control is only certified if the probe that tested it could have failed** — that is how B-1 and B-2 were found and how the false one was discarded. **Scorecard: 15 PASS · 4 PASS WITH CONDITIONS · 2 BLOCKED · 3 NOT OPERATIONALLY VERIFIED** (payments, backup/DR, rollback — unbuilt operational capabilities, not defects). Nothing was promoted to PASS without evidence. **Path to GO:** fix B-1 and B-2, add regression tests that fail without each fix, commit and tag the candidate (L-1), then re-run §7, §10 and §29 only.
- [x] PH3.12R Production Certification Blocker Remediation — **COMPLETE (2026-08-18) — READY FOR A FRESH PH3.12 CERTIFICATION RERUN** — Critical — *Report: `docs/production/PH3.12_PRODUCTION_CERTIFICATION_NOGO_ARCHIVE.md`, § PH3.12R Blocker Remediation Addendum (archived verbatim).* **PH3.12 certification is NOT passed — this sprint fixes what blocked it; the rerun decides the release.** Both blockers had been present for the whole of PH3 and both survived for the same reason — **the probe that should have caught them could not have failed** — so every fix ships with tests run against the pre-fix code and *observed to fail*, with counts recorded. **B-1 root cause was duplication, not a missing bound:** the trade-entry contract was written down twice — `TradeCreate` in `models.py` with every constraint, and `PaperTradeCreate` declared inline in `server.py` ~5,000 lines away with none — and nothing linked them, so bounds added to one were never added to the other; `total_cost = entry_price * quantity` then went negative and the BUY branch called `update_paper_balance(user_id, -total_cost)`, **crediting** ₹10,00,000. **Fixed** by making the contract exist **once**, as shared constrained types (`TradeSide`, `TradeQuantity`, `TradePrice`, `OptionalTradePrice`, `TradeSymbol`), with `PaperTradeCreate` moved into `models.py` directly beneath `TradeCreate` and rewritten in them — adjacency is part of the fix, because the divergence is now visible at a glance and cannot be reintroduced without deleting a shared type. Validation is model-layer, so it completes **before** the handler body runs and therefore before any balance, position, trade or P&L write is reachable: malformed input answers **422** and mutates nothing; `execute_paper_trade` re-validates against the *same model* (not a copy of its rules) for callers that never touch FastAPI. **A second, latent instance of the same class was found and closed:** Python's `json.loads` — which Starlette parses bodies with — accepts `Infinity`/`NaN`, and a plain `gt=0` float **admits `Infinity`** (`inf > 0` is True), so `entry_price: Infinity` passed every bound on the **real** trade endpoint too; verified empirically, closed by `allow_inf_nan=False`. **B-2 root cause:** `server.py:357` was `FastAPI(title="AlphaPartner API")` with no environment gating — and it survived because **PH3.11 §9 probed `/api/docs`**, a path this application never served, saw the generic unknown-path 404, and recorded the control as verified. **Fixed** by new `backend/security/api_docs.py` and `FastAPI(title=..., **api_docs.docs_kwargs())`: production → `/docs`, `/redoc`, `/openapi.json` all **404**; development/testing/staging → all **200**. Four decisions each against a specific failure mode — all three switch **together from one function** (disabling Swagger alone leaves the machine-readable schema served, the half that matters); `None` rather than a 403 guard so the routes are never registered and disclose nothing by difference; the environment read through `security.secrets.app_env()` so docs exposure cannot disagree with the cookie policy; and **no variable can enable docs in production** (`API_DOCS_ENABLED=false` only ever tightens, mirroring `cookies.cookie_secure()`), because an enable-flag means one typo reopens exactly this hole. `app.openapi()` still works — unpublished, not ungenerable. **Regression tests: 184 new.** `tests/test_paper_trade_validation.py` (**132**) — the literal `quantity=-1000` exploit rejected *on the quantity field* with the balance identical after three repeats and no document written; a 28-case hostile matrix (quantity, price, stop/target, side, seven malformed symbols, unknown keys, overlong text, plus `Infinity`/`-Infinity`/`NaN` sent as **raw text** because no JSON encoder emits them); **every one of those 28 re-run against an account holding an open position** so balance, positions, unrealised P&L and journal all carry non-trivial values, each asserting a full account snapshot is byte-identical afterwards; valid BUY/SELL, seven real symbol shapes (`M&M`, `BAJAJ-AUTO`, lowercase), all eleven UI setup types, quantity exactly at the 100,000 ceiling, insufficient-capital still 400 not 422; direct service-layer calls; and metadata-identity assertions proving the two models can no longer drift. `tests/test_api_docs_exposure.py` (**52**) + harness `tests/_prod_app_probe.py` — real paths as **literals**, never derived from the constant under test; the partial fix proven impossible; an explicit test that **`/api/docs` is 404 in both environments**, documenting why PH3.11's evidence was empty; and a class that boots the **real `server` module in a clean interpreter as `APP_ENV=production`** and measures the real routes — necessary because `server` builds `app` at import time, so under the suite's own `testing` environment a regressed constructor would be *indistinguishable from the fix*. **Falsifiability measured, not asserted:** B-1 pre-fix **94 failed / 38 passed** (the 38 are the valid-input cases, which is correct); B-2 pre-fix line **5 failed**, and the **half-fix** (Swagger hidden, `/openapi.json` still served) **7 failed**. **Full battery green:** **2,743 backend** / 0 failed / 4 xfailed (baseline 2,559 + 184), **452 security unchanged**, 138 paper, 52 API-docs, 17 WebSocket, 867 authz/route/validation sweeps, 210 health/readiness, 71 trading engine, **395 frontend** / 22 suites, build exit 0, dependency audit **exit 0**. **Route inventory: 193 development → 189 production**, removing exactly `/docs`, `/docs/oauth2-redirect`, `/openapi.json`, `/redoc` and nothing else; `/api` count unchanged; **188 OpenAPI paths**, matching PH3.12 §7. **L-1 resolved** — the RC is now a commit, not a working tree; image `stockassist-rc:ph312r` (425 MB) rebuilt `--no-cache --pull`, with the honest caveat that such builds are **not bit-reproducible** so the verified property is that the **application source inside the image matches the committed source**, not that two builds hash alike. **Scope deviations recorded, not buried:** `TradeCreate` also gained `allow_inf_nan=False` via the shared alias (a real `/api/trades` behaviour change — same defect class, same field, and `Infinity` is not valid JSON; leaving the real endpoint deliberately weaker than the paper one was judged the worse call), and `extra="forbid"` on `PaperTradeCreate` — **note for the rerun:** PH3.12's reproduction `curl` sends `"action":"BUY"`, an unknown key, and will now be rejected for *that* reason; use a payload valid in every field except `quantity`. JWT, refresh rotation, cookies, CORS, CSRF, rate limiting, OAuth, Redis, payments, analytics and the trading engine beyond the shared aliases are unchanged. **Remaining: none from B-1, B-2 or L-1.** Still open and untouched: the eight PH3.10 deployment conditions (C-1…C-8) and the three **NOT OPERATIONALLY VERIFIED** categories (payments, backup/off-host DR, rollback) — unbuilt operational capabilities, not defects, and the reason this sprint does not convert §30 into a GO. **Recommend a fresh PH3.12 certification rerun.**
- [x] PH3.12 Production Certification RERUN — **COMPLETE (2026-08-18) — GO (CONDITIONAL)** — Critical (final gate) — *Report: `docs/production/PH3.12_PRODUCTION_CERTIFICATION.md` (30 sections, rewritten).* Independent re-certification of committed RC `a4ee79f` on `main`, **clean working tree**, against a from-scratch `--no-cache --pull` image (`stockassist-rc:ph312-cert`, 425 MB, `sha256:42d12ddf…abf8ce`). **No application code changed**; tree verified clean at start and end. **Every baseline reproduced exactly:** backend **2,743** / 0 failed / 4 xfailed, security **452**, frontend **395** / 22 suites, production build exit 0 (62 ESLint warnings = PH3.10 baseline), dependency gate exit 0, routes **97/29/75 = 201** HTTP + 1 WS, analytics **0 MOCK** of 53. **All three prior blockers independently CLOSED with falsifiable probes.** B-1: 15/15 hostile paper-trade payloads (incl. `quantity=-1000`, `NaN`, `Infinity`, `1e309`, unknown fields, bad action/symbol) → **422** with the paper balance **byte-for-byte unchanged**, re-verified with an open position held; valid BUY/SELL/close arithmetic exact (100000→90000→103229, realized P&L ₹3,229 at a live ₹1,322.90 mark). B-2: `/docs` `/redoc` `/openapi.json` → **404** in production and **0 documentation routes registered** — and the probe was proven able to fail: the *same image* at `APP_ENV=development` serves all three **200** with 188 paths. L-1: RC is a real commit; **0 content mismatches** across 117 image files vs committed blobs. PH1: 17 controls re-probed **live** (cookies HttpOnly/Secure/SameSite, CORS origin rejection, password policy 422, JWT forgery 401, refresh **rotation + replay 401 + whole-family revocation**, CSRF missing/forged 403 vs valid 200, rate limit 429 on attempt #6 = configured `5/900`, full security-header set, no `Server` header) — **none regressed**. WebSocket matrix 4/4 rejections (anonymous, spoofed `user_id`, query-token, forged subprotocol) with 2/2 legitimate connections proving falsifiability. Infra: Redis loss degrades with **0 restarts**, Mongo loss → live 200 / ready 503 in 7s, both recover automatically (ready 200 in ≤5s), data intact. Dependency gate proven to bite **three** ways (expiry → exit 1, stale entry → exit 1, auditor missing → exit **2**). Secrets: gitleaks over image `/app` **no leaks**, **0** of 7 configured secrets in live logs, production config **fails closed** (startup refused 3× on weak/placeholder config). Backup+restore drill executed end-to-end: encrypted AES-256 artifact, **19 collections matched exactly**. **Two NEW findings, neither security/financial:** **C-1** — untracked git-ignored `backend/test-results/junit.xml` is baked into the image (`.dockerignore` lacks `test-results/`); proven by build comparison — working dir **117** files in `/app` vs clean `git archive` of the same commit **116**; image is not a pure function of the commit, and PH3.12R missed it by checking only `.py` files. **C-2** — container exits **137** on shutdown after a Redis outage (4/4; baseline exits 0; `-t 60` exits 0); teardown itself completes, impact is orchestrator signalling not data integrity. **Verdict GO (CONDITIONAL):** source at `a4ee79f` certified production-ready; the image built this sprint must be **rebuilt after the one-line C-1 fix** before deploy. Payments **NOT OPERATIONALLY VERIFIED** (no provider integration exists; refund returns 501 and writes no audit record), Backup/DR and Rollback **NOT OPERATIONALLY VERIFIED** (no off-host target, no deployment ledger). No blocker was repaired during certification; nothing deployed; PH3.13 not started.
- [x] PH3.12C Conditional Remediation (C-1 / C-2) — **COMPLETE (2026-08-18)** — High — *Report: `docs/production/PH3.12_PRODUCTION_CERTIFICATION.md` §31.* **C-1 CLOSED.** `backend/.dockerignore` now excludes test *outputs*, not just test *inputs*. Established empirically first (Docker 29.4.0 probe context): **a `.dockerignore` pattern is anchored to the build-context root** — a bare `test-results/` left `sub/test-results/junit.xml` still being copied in — so every rule ships **both** a bare and a `**/`-prefixed form (`**/foo` does not match a root-level `foo`, so neither form is redundant); the same depth-independent twins were added for the caches block (`.pytest_cache/`, `.coverage`, `htmlcov/`, `.tox/`, `.mypy_cache/`…), which had the identical gap. Guard: **`backend/tests/test_build_context.py`, 44 tests**, hermetic (parses `.dockerignore`, never invokes Docker) — and **proven able to fail**: **26 failed** against `HEAD`'s pre-fix file, 44 pass after, and it also asserts the exclusions do *not* over-reach onto `server.py`/`models.py`/`entrypoint.sh`. Proof, with `test-results/junit.xml` and `.coverage` deliberately left on disk so the check could not pass vacuously: working-dir build **116** files in `/app` vs clean `git archive` build **116**, `diff` **identical**, **0** content mismatches, **0** image-only files, no `junit*.xml`/`coverage.xml`/`report.xml` anywhere under `/app` (was 117 with the artifact). Image `stockassist-rc:ph312-c1fix`, 425 MB, `sha256:cdfcd0b3…a9af03`. **C-2 WITHDRAWN — the certification finding was wrong, no code changed.** A 20-line control container (textbook SIGTERM handler, 1.2s teardown, no Redis, no asyncio, no application code) reproduces the identical **exit 137 in 6/6** runs under bare `docker stop`, and **exit 0 in 3/3** under `docker stop -t 10`/`-t 30`/`-t 60` — i.e. on this host `docker stop` **without an explicit `--timeout` SIGKILLs at ~1.3s**, far short of the documented 10s grace. The app's teardown takes **1.5–2.3s**, straddling that window, and the certification happened to use bare `docker stop` for the flap runs and explicit `-t` for the baselines — so **every 137 came from a bare stop and every 0 from an explicit `-t`**; Redis was never the variable. Measured by sending SIGTERM directly: **exit 0 in 3/3** (1.94s / 1.47s / 2.28s), ordered teardown complete, **0** asyncio `Task was destroyed`/never-awaited warnings — so the fire-and-forget pool-reset task in `infrastructure/redis_client.py` is not leaking either. No lifecycle fix implemented, because the SIGKILL originates in the host's `docker stop` client, not the application; exit code **not** masked, no test weakened. Post-remediation: targeted regression **379 passed**, B-2 404/404/404 and B-1 422 with balance unchanged on the remediated image, valid BUY 200 → 90000.0. Files changed: `backend/.dockerignore`, `backend/tests/test_build_context.py` (new) — **no application module, dependency, workflow or config touched**. **Remaining condition: the remediation is UNCOMMITTED**, so L-1's clean-tree property does not currently hold; commit both files and rebuild from that commit before deploy. PH3.13 not started.
- [ ] PH3.12F Release Closure Pass — **BLOCKED (2026-08-19) — NEW BLOCKER C-3 OPEN** — Critical — *Report: `docs/production/PH3.12_PRODUCTION_CERTIFICATION.md` §32.* **Release SHA `6b53b3bcf99c400a0f623d5f4d280ffe87c47776`; image `sha256:9de7b850d09bc81ce1d61f49ba9682bed1850e2b25df9fcdcdf8310eb6bb2cc4`** (425 MB, built `--no-cache --pull` from a `git archive` export, stamping its own commit in `org.opencontainers.image.revision`). **C-1 CLOSED** — with the offending host artifacts deliberately left on disk, the image carries **0 files that are not in the commit and 0 content mismatches** across all 116 non-generated files, no test input or result artifact anywhere under `/app`, and every production module present; guard 44 passed. **C-2 WITHDRAWN** — a 20-line control container with no application code reproduces the identical exit 137 under a bare `docker stop` (6/6) and exits 0 under any explicit `-t` (3/3); the final image exits **0** on direct SIGTERM with complete ordered teardown and 0 under `docker stop -t 30`. No lifecycle code changed. **Checks re-run:** backend **2,787 passed** / 0 failed / 4 xfailed, security **452**, frontend **395** in 22 suites, production build exit 0 with 48 bundles / 14 MB, dependency gate **exit 0** and still falsifiable (`--today 2030-01-01` → exit 1 EXPIRED, register unmodified), route inventory re-introspected live at **201 HTTP + 1 WS, 97/29/75, 0 documentation routes**. **B-1:** 15 hostile payloads all **422**, balance byte-identical, replayed against an account holding an open position, valid BUY/close arithmetic exact. **B-2:** 404/404/404 in production and **200 with 188 paths from the same image** under `APP_ENV=development` — falsifiable both directions. **NEW BLOCKER C-3 (reported, NOT repaired):** `.dockerignore`'s `__pycache__/` and `*.py[cod]` are bare and therefore root-anchored, so nested `__pycache__` still enters the build context — a working-directory build yields **228 files vs 223**, including 104 `.pyc` whose code objects embed the developer's absolute home path in `co_filename`. The C-1 guard misses it because `_excluded` models Docker with `fnmatch` (whose `*` crosses `/`) rather than Go's `filepath.Match` (whose `*` does not) — unfalsifiable for the exact class it defends. **Audit trail:** the NO-GO + PH3.12R report is preserved verbatim at `PH3.12_PRODUCTION_CERTIFICATION_NOGO_ARCHIVE.md`. PH3.13 not started.

---

# Previous Focus (superseded 2026-07-17)

Sprint 9 (Trading Engine) is COMPLETE — `services/trading_engine.py` closes
the trade lifecycle on top of the Broker Engine: a pre-trade Risk Manager
enforcing the user's own limits (max trades/day, daily loss budget, SL/target
sanity, risk-per-trade guideline), multi-target (T1–T3) partial profit
booking, a trailing stop that ratchets toward the best price and never
loosens, server-side modify of SL/targets/trailing, partial and at-market
exits, a per-trade event timeline, and a unified cross-broker order history
(GET /api/orders). Live exit orders are placed ONLY with per-trade opt-in
consent (`auto_exit`) on broker-linked trades; everything else is alert-only.
The Trade Monitor page gained a BUY/SELL form with a live risk-check panel,
broker execution, an Orders tab (cancel/modify pending), and a Risk Manager
summary strip. The engine runs inside the existing 60s trade_monitor cron.

Next: admin portal and payments/subscriptions (Milestones 6+), then a
dedicated Risk Dashboard page and Strategy Builder.

---

# Market Data Architecture (Provider Independence)

Status: IN PROGRESS — Phase D (Market Data Evolution)

Priority: Critical

Design approved and documented in MARKET_DATA_ARCHITECTURE.md (2026-07-16); ADR-026 recorded in DECISIONS.md. D1 scope decisions recorded as ADR-028 (2026-08-19).

Implementation phases (per MARKET_DATA_ARCHITECTURE.md):

- [x] **D1 / Phase 1 — Market Gateway Foundation** — **COMPLETE (2026-08-19)** — Provider Adapter contract, Provider Registry, Source Manager foundation, Yahoo migration. Scope decisions in **ADR-028**.
- [x] **D2 / Phase 2 — Source Manager completion** — **BACKEND COMPLETE (2026-08-20)** — failover chain, `UnavailableReason`, `ResolutionContext`, `unknown` health. Scope decisions in **ADR-029**; public-contract reconciliation in **ADR-030**. Frontend reactive tier indicator outstanding (DD-7).
- [x] **D3 / Phase 3 — Broker Provider Framework** — **COMPLETE (2026-08-20)** — re-scoped from "Zerodha Kite WebSocket adapter"; scope decisions in **ADR-031**. See "D3 — What shipped" below.
- [ ] D4 / Phase 4 — **IN PROGRESS.** D4.1 (DB-2 startup ordering + reconnect jitter), D4.2 (broker streaming contract / codec boundary, ADR-032), D4.3 (canonical instrument identity / market tick, ADR-033), D4.4 (broker feed registered as a market-data provider + the `subscribe`/`on_raw` push surface, ADR-034), D4.5 (make-before-break switching, readiness gate, per-symbol coverage, push-driven baseline failover, ADR-035) D4.6 (the Zerodha Kite ticker as the first concrete stream adapter behind the switch, ADR-036) and D4.7 (the Upstox v3 market feed as the second, plus the generic multi-channel stream transport it revealed the need for, ADR-037) and D4.9 (Angel One SmartAPI as the third, plus the generic application-level keep-alive it revealed the need for, ADR-038) and D4.10 (Fyers API v3 as the fourth, plus the generic per-connection codec scope it revealed the need for, ADR-039) and D4.11 (Dhan DhanHQ v2 as the fifth — **the first broker that required no generic framework change at all**, ADR-040) complete — all five **deterministic validation only; live validation not performed**; D4.12+ outstanding — the remaining broker adapters (Groww, INDmoney), each one adapter and nothing else
- [ ] D5 / Phase 5 — Hardening: latency scoring, flap suppression, probation windows, multi-connection sharding, chaos tests — **D5.1 (reconnect flap suppression, closes DB-5) COMPLETE (2026-08-25, ADR-041)**; **D5.2 (provider probation — READY vs STABLE, closes LIM-D5.1-3) COMPLETE (2026-08-27, ADR-042)**; **D5.3 (provider stability decay & stale-feed demotion, closes LIM-D5.2-3) COMPLETE (2026-08-27, ADR-043)**; **D5.4 (provider delivery-latency scoring, closes LIM-D5.3-3) COMPLETE (2026-08-27, ADR-044)**; **D5.5 (entitlement-failure classification & safe recovery, closes the D4.11 code-806 approximation) COMPLETE (2026-08-27, ADR-045)**; D5.6+ (DB-1, instrument sharding, chaos tests) not started
- [ ] D6 / Phase 6 — Enterprise/licensed feeds (future)

## D1 — What shipped

**Created**

- `backend/services/market_engine/providers/base.py` — `MarketDataProvider` contract, `Capability`, `ProviderKind`, `SourceTier`, `ProviderState`, `ProviderHealth`, `CapabilityUnavailable`.
- `backend/services/market_engine/providers/registry.py` — `ProviderRegistry`, priority-ordered, capability- and health-filtered candidate lists.
- `backend/services/market_engine/providers/yahoo.py` — `YahooPollingAdapter` (priority 3, `kind=polling`, `tier=delayed`), wrapping the hardened `services/real_market.py` client.
- `backend/services/market_engine/source_manager.py` — `SourceManager`: capability resolution, health-based exclusion and recovery, `provider.status` publication (change-gated, tier-only), `status()` vs `diagnostics()` split.
- `backend/tests/test_market_gateway.py` — 44 tests.

**Modified**

- `gateway.py` — resolves providers by capability instead of importing the Yahoo client; selects the normalizer from the adapter's `normalizer_key`; records call outcomes against provider health; stamps `source_tier` + `ingested_at`; gained `search()`; `status` gained a tier-only `feed` block; `diagnostics` added.
- `normalizer.py` — no longer stamps `provider` on any normalized event (all four families).
- `ai_context_builder.py` — the five direct `real_market.*` calls now go through the gateway; `_render_sectors` reads either sector-name shape.
- `event_bus.py` — `provider.status` topic documented.
- `market_engine/__init__.py` — exports the new surface.

**Two silent defects found and closed**

1. Index normalization in the gateway had never run: the provider's index sub-dicts carry no `name`, `validate_index_quote` rejects a nameless index, so the raw payload passed through on every request. The gateway now supplies the name at the boundary and merges normalized fields *over* the raw dict so `available` survives.
2. Every normalized quote carried `provider: "yahoo"` — provider identity one attribute access away from the AI prompt and the frontend store.

**Validation**

Backend suite **2,964 passed / 15 failed** — the 15 are the pre-existing `test_entrypoint_log_level.py` failures (`python: command not found`; the Docker entrypoint tests need the container's PATH) and are unchanged from the pre-D1 baseline of 2,921 passed / 15 failed. flake8 clean on every touched file (one pre-existing warning removed). **Seven falsification probes run**: each guard was mutated and observed to fail — provider leak restored → 2 red; normalizer key hardcoded → 1 red; index-name fix reverted → 1 red; new bypass module added → 1 red; provider named in a non-adapter engine module → 1 red; gateway re-importing the provider client → 1 red; AI layer reverted to `real_market` → 1 red.

## D3 — What shipped

**Re-scoped before starting.** D3 was planned as the Zerodha Kite WebSocket market-data adapter. The broker layer underneath it was not yet a framework — a hardcoded broker dict rather than a registry, no capability model, no broker gateway, canonical shapes documented only in a docstring, and broker names branched on inside `server.py`, `broker_engine.py` and `stream.py`. Building the headline streaming feature on top of that would have meant unpicking all of it later with a live feature sitting on it. Framework first; streaming feed is D4. Full reasoning: **ADR-031**.

**Created** (all under `backend/services/brokers/`)

- `capabilities.py` — `BrokerCapability` (14 capabilities) + `CAPABILITY_METHODS` binding each to the adapter method that serves it, which is what makes the model verifiable rather than decorative.
- `registry.py` — `BrokerRegistry`, one long-lived adapter per broker, with registration-time validation. A `@capability_stub` mark distinguishes a base default that only raises (declaring it is a defect) from one that genuinely works (`get_margins` → `get_funds`), which identity comparison against the base class could not.
- `gateway.py` — `BrokerGateway`, the single choke point: capability enforcement before the adapter is reached, canonical coercion, error normalization, health bookkeeping.
- `contracts.py` — `BrokerProfile`, `BrokerHolding`, `BrokerPosition`, `BrokerOrder`, `BrokerOrderAck`, `BrokerTrade`, `BrokerFunds`, `BrokerConnection`.
- `errors.py` — `BrokerErrorCode` (11 codes, existing wire values preserved), `CapabilityUnsupported`, `UnknownBrokerError`, `BrokerContractError`, `normalize_broker_error`; retry and recovery derived from the code.
- `health.py` — `BrokerConnectionState` + `BrokerHealth`, with auth failures counted separately and excluded from the state machine.
- `credentials.py` — `BrokerCredentialSpec` / `BrokerCredentials` / `resolve_credentials`: the authentication and configuration boundary.
- `backend/tests/test_broker_framework.py` — 49 tests, including `AcmeBrokerAdapter`, a fictional partial broker that proves adding a broker touches no core module.

**Modified**

- `brokers/base.py` — capability-gated fetch surface replacing nine `@abstractmethod`s; `capabilities`, `credential_spec`, `default_product`, `default_variety`, `stream_protocol`; `parse_callback_params`, `stream_credentials`, `stream_instruments`, `normalize_stream_order`, `describe`.
- `brokers/zerodha.py`, `brokers/upstox.py` — capability declarations, credential specs, stream protocols; no `os.environ` remains in either.
- `brokers/stream.py` — dispatch by `stream_protocol` through `PROTOCOL_RUNNERS` instead of `if self.broker == "zerodha"`; frame normalization via the adapter instead of importing adapter classes by name; `credentials` dict replacing a Kite-specific `api_key`.
- `brokers/__init__.py` — registry wiring; `SUPPORTED_BROKERS` and `create_adapter` retained as deprecated views *derived from* the registry so they cannot drift.
- `broker_engine.py` — every broker call routed through the gateway; publishes `broker.connected` / `broker.disconnected`; `get_status` built from `BrokerConnection`; the `if broker == "zerodha"` stream branch and the `KITE_API_KEY` read are gone.
- `server.py` — `_require_broker` and `preferred_broker` validate against the registry; the two `"CNC" if broker == "zerodha" else "D"` product defaults removed; the OAuth callback's broker-name branch replaced by `parse_callback_params`.
- `market_engine/source_manager.py` — subscribes to broker lifecycle events; `connected_brokers()`, `streaming_brokers()`, `has_broker_connected()`.
- `market_engine/gateway.py` — subscribes the Source Manager to broker events at initialisation.

**Three broker-name leaks in core closed**

1. Order product default — the old `else` branch silently handed Upstox's product code to every broker added after it.
2. OAuth callback parsing — the old `else` assumed every future broker speaks Upstox's dialect.
3. Stream dispatch — a broker-name chain that grew by one branch per broker in a module no broker owns.

**Two defects found and closed on the way**

1. **A live broker access token could be written to the application log.** `_request` logged the full broker URL on 401/403, and Kite's logout endpoint carries the access token *in the query string* — so a rejected logout (exactly what an already-dead token produces) leaked it. URLs are now stripped of their query before logging. Violated SECURITY.md's no-credentials-in-logs rule.
2. **`BROKER_FORCE_IPV4` was evaluated once at import**, so a deployment setting it after the process read its environment silently kept the import-time answer. Read at call time now.

**Validation**

Backend suite **3,064 passed** (3,015 before D3 + 49 new), 4 xfailed, 0 failed. `test_entrypoint_log_level.py` remains at its documented pre-existing baseline of 15 failures (needs a Docker daemon) — unchanged by D3. flake8 clean repo-wide on the blocking selection and fully clean on all eight new files; `black` and `isort` clean on all eight (CI's blocking format check applies to added files). No secrets; no mock or simulated data introduced.

**Deliberately NOT done in D3** (each deferred to D4, each pinned by a test)

- No broker registered as a *market data* provider. That requires either a fabricated `streaming` tier (forbidden by CLAUDE.md's data rules) or a REST-polled provider silently displacing the Yahoo baseline without make-before-break. Pinned by `test_d3_does_not_register_a_broker_as_a_market_data_provider`.
- No broker WebSocket market feed, no `subscribe`/`on_raw` push surface on `MarketDataProvider`.
- No DD-6 provider recovery / re-probe.
- No Upstox, Angel One, Groww, INDmoney, Dhan or Fyers *new* adapters (Upstox was already present and was brought onto the framework).

## D4 — What has shipped so far

### D4.1 — prerequisites (2026-08-21)

- **DB-2 closed.** `BrokerEngine.load_sessions()` now publishes `broker.connected` for each restored session, and `server.py` subscribes the Source Manager *before* restoring them. The ordering is the whole fix: `EventBus.publish` treats "no matching handler" as normal, so publishing first would have logged a correct-looking restore and left the per-user registry empty. Pinned by an ordering assertion, not a "publish was called" assertion.
- **Reconnect jitter.** Equal-jitter backoff (`half + uniform(0, half)`) replaced a deterministic doubling that had every user's socket retry in the same instant after a single broker-side blip.
- **The dependency boundary locked.** market → broker imports are banned by test before D4.2 code landed, rather than audited after.

### D4.2 — the streaming contract / codec boundary (2026-08-21, ADR-032)

**Created**

- `backend/services/brokers/streaming.py` — `BrokerStreamEndpoint`, `BrokerTick`, `BrokerStreamEvent` / `StreamEventKind`, `EVENT_CAPABILITY`. The canonical streaming shapes, and the rule that a codec may return nothing else.

**Modified**

- `brokers/base.py` — `stream_endpoint()`, `stream_subscribe_frames()`, `decode_stream_frame()`: the entire wire-format surface of a broker stream, now adapter-owned.
- `brokers/stream.py` — one generic WebSocket transport replacing `_run_kite` / `_run_upstox`. Kite's URL, binary framing, subscribe frames and error convention, and Upstox's JSON envelope, all left the module. `PROTOCOL_RUNNERS` is now an empty override table with `resolve_transport()` in front of it.
- `brokers/zerodha.py`, `brokers/upstox.py` — each broker's codec, in the only module entitled to hold it. `parse_kite_binary` moved here from `stream.py`.
- `brokers/capabilities.py` — `STREAMING_CAPABILITIES`, `STREAM_TRANSPORT_METHODS`.
- `brokers/registry.py` — `_validate_streaming`: a realtime capability without a codec, and a `stream_protocol` without a realtime capability, are both rejected at registration.
- `brokers/gateway.py` — `stream_event_allowed()`: the capability gate the streaming path never had.

**The defect this closed.** The platform's tick contract was an accident. `parse_kite_binary` produced `{"instrument_token", "last_price"}` and that list went straight to `portfolio_stream.apply_broker_ticks`, `trade_stream.apply_broker_ticks` and the user's app WebSocket — both service docstrings state that shape as fact, and it was true only because exactly one broker's parser happened to build it. A second streaming broker emitting `{"token", "ltp"}` would have type-checked, imported, connected, and silently stopped every live P&L recompute for its users. Nothing would have raised, logged or failed.

**Two smaller ones closed with it.** Streamed order frames reached `db.orders` as whatever `normalize_stream_order` returned while the identical order fetched over REST went through `BrokerOrder` — two writers to one collection with one unenforced; streamed orders are now coerced through the same contract. And a stream URL had no safe log form, which matters because Kite puts a live access token in the ticker's query string.

**Validation.** Backend suite **3,093 passed**, 4 xfailed; the 15 `test_entrypoint_log_level.py` failures are the documented pre-existing Docker baseline, unchanged. flake8 clean across `services/brokers/` and the new tests (one pre-existing E501 in `upstox.py` fixed on the way); `black` and `isort` clean on the new files. **Nine falsification probes run**, each mutation observed red and reverted: codec type-check removed → 1 red; runtime capability gate removed → 1 red; registration streaming validation removed → 2 red; `BrokerTick` key-dropping removed → 1 red; streamed-order coercion bypassed → 1 red; raw URL logged → 1 red; `json` re-imported into `stream.py` → 1 red; Kite paise divisor changed → 2 red; subscribe frames re-encoded by the transport → 1 red.

**Deliberately NOT done in D4.2** — no broker registered as a market-data provider, no `subscribe`/`on_raw` on `MarketDataProvider`, no Zerodha *market-feed* provider, no new broker adapters. `BrokerTick.symbol` is carried but unconsumed; D4.3 is where a consumer for it arrives.

### D4.3 — canonical instrument identity / market tick (2026-08-21, ADR-033)

**Created**

- `backend/services/market_engine/ticks.py` — `MarketInstrument`, `MarketTick`, `MarketTickError`. The canonical tick and instrument identity, on the market side of the D4.1 direction rule, naming no broker.
- `backend/services/brokers/instruments.py` — `InstrumentMap`, `canonical_ticks()`. The boundary a broker instrument identifier does not cross, built from rows the platform already syncs.

**Modified**

- `broker_engine.py` — `_on_stream_tick` canonicalizes before delivering; `_instrument_map` / `_remember_instrument_map` / `_forget_instrument_map` own the per-account map (seeded by `start_stream`, rebuilt by `sync_portfolio`, dropped by `disconnect`).
- `portfolio_stream.py`, `trade_stream.py` — consume `MarketTick` dicts keyed by canonical symbol; neither names a broker instrument identifier any more.
- `brokers/contracts.py`, `brokers/streaming.py`, `brokers/stream.py` — docstrings corrected where they described the join that D4.3 removed.
- `frontend/src/store/realtimeStore.js` — `broker_price_tick` is symbol-keyed; comment updated (nothing consumed the token form, so no UI change).

**The defect this closed.** `BrokerTick.instrument_token` — a Kite integer, an Upstox instrument key — travelled into `portfolio_stream`, `trade_stream` and the browser, and both services did the token→symbol join themselves against `db.holdings`. Two core services were coupled to one broker's identifier format, the join was written twice, and a **symbol-identified broker had no join key at all**: every join produced nothing and every live P&L recompute for its users stopped, on a healthy socket delivering good prices, with nothing raised, logged or failed. Same class of defect as D4.2's, surviving one layer up.

**Two smaller ones closed with it.** A trade in a symbol the demat account does not hold could not be marked from ticks at all and waited for the 60s monitor; a batch that resolved to nothing still pushed an empty tick list to the browser and woke two recomputes on every frame.

**Validation.** Backend suite **3,109 passed**, 4 xfailed; the 15 `test_entrypoint_log_level.py` failures are the documented pre-existing Docker baseline, unchanged. flake8 clean on every changed file except two pre-existing findings in `broker_engine.py` (present at HEAD); `black` and `isort` clean on both new modules. **Eleven falsification probes run**, each mutation observed red and reverted: unmapped token used as a symbol → 4 red; canonical boundary removed from the engine → 4 red; `MarketTick` identity/price enforcement removed → 1 red; symbol resolution dropped → 3 red; token/str matching reverted to raw values → 1 red; a malformed tick aborting the batch → 1 red; sync no longer rebuilding the map → 1 red; `start_stream` no longer seeding it → 1 red; disconnect keeping it → 1 red; canonical output built by patching the broker payload → 3 red; the stale-map warning unthrottled → 1 red.

**Deliberately NOT done in D4.3** — no broker registered as a `MarketDataProvider`, no `subscribe`/`on_raw` push surface, no Zerodha live market feed, no make-before-break, no provider failover, no new broker adapters.

### D4.4 — the provider-registration seam (2026-08-21, ADR-034)

**The chain, complete.** `broker stream → canonical MarketTick → MarketDataProvider → Market Gateway → Source Manager → Market Engine`. D4.2 gave the tick its shape, D4.3 gave it a canonical identity; D4.4 is where it becomes *market* data instead of portfolio input.

**Created**

- `backend/services/market_engine/providers/streaming.py` — `StreamingTickProvider`, `STREAMING_FEED_PRIORITY`, `TICK_FIELDS`. A generic pushed-feed provider. Names no broker, no exchange and no vendor; a second streaming broker adds zero lines to it.
- `backend/services/brokers/market_feed.py` — `attach_market_feed` / `detach_market_feed` / `publish_market_ticks` / `feed_provider_name`. The construction seam on the broker side of the D4.1 direction rule.

**Modified**

- `providers/base.py` — the push surface: `subscribe` / `unsubscribe` / `subscribed_symbols` / `on_raw` / `bind_sink` / `_emit`, plus `is_ready` and `ProviderContractError` / `PUSH_CAPABILITIES`. Closes the D1 deferral recorded in ADR-028.
- `providers/registry.py` — `validate_provider()`, run at registration.
- `market_engine/gateway.py` — `register_streaming_provider` / `unregister_streaming_provider` / `_ingest_ticks`, and `TICK_TOPIC = "market.tick"`.
- `broker_engine.py` — `start_stream` attaches the feed, `_on_stream_tick` publishes the canonical batch into it, `disconnect` and `_on_stream_expired` detach it.
- `tests/test_market_gateway.py` — `FakeStreamingProvider` and `UserScopedProvider` gained a real `on_raw` (see below).

**The scope line, and why it is where it is.** The registered provider declares `TICKS` and **not** `QUOTES`. Declaring QUOTES would make a priority-1 provider outrank the baseline the instant it registered — which *is* the feed switch, performed without the make-before-break gate MARKET_DATA_ARCHITECTURE.md requires. `TICKS` has never been served by anything, so registering the feed takes nothing away from anybody and the baseline continues to answer every quote for every user. The switch is D4.5 and is separately testable. Pinned by `test_yahoo_is_unchanged_by_the_streaming_seam`, which goes red the day `QUOTES` is added without a gate.

**What the contract check found immediately.** `validate_provider` rejects three contradictions — a push capability without `kind=STREAMING`, `tier=STREAMING` without `kind=STREAMING`, and `kind=STREAMING` without an `on_raw`. Its first two catches were pre-existing test doubles: `FakeStreamingProvider` and `UserScopedProvider` both declared themselves streaming broker feeds with no way to be pushed into. They were corrected rather than exempted. The first draft of the rule was *stricter* — it required a push capability rather than an `on_raw` — and `UserScopedProvider` is what exposed that as wrong: a streaming provider serving pushed quotes is exactly the shape D4.5 produces, and requiring `TICKS` of it would require a capability it does not serve.

**Security.** The tick event carries `user_id` when the feed is owned by one, because the event bridge delivers a `user_id`-bearing payload to that user alone and broadcasts everything else to every socket on the market channel. Without it, data consumed under one user's broker entitlement would have fanned out to every connected user — a Category 2 entitlement breach, not a preference. Ending the entitlement (disconnect, expired token) unregisters the provider immediately rather than waiting for a health transition. Provider names carry the broker and the user id and reach only the registry, gateway logs and the admin diagnostics surface; `source_manager.status()`, every normalized event and every API response still carry a `source_tier` and no identity (Developer Rule 4).

**Validation.** Backend suite **3,119 passed**, 4 xfailed; the 15 `test_entrypoint_log_level.py` failures are the documented pre-existing Docker baseline, unchanged. **Twelve falsification tests added** to `test_broker_streaming.py`, and **five mutations run against them**, each observed red and reverted: the broker capability gate removed → 1 red; `validate_provider` removed from registration → 1 red; the closed-field-set check removed → 1 red; the engine no longer publishing into the feed → 1 red; the provider declaring `QUOTES` → 3 red. A sixth probe was run *before* the tests were finished: the broker-agnosticism sweep's own non-vacuity assertion failed, which is how the string-literal sweep (`if broker == "zerodha"` survives an identifier sweep untouched) was added.

**Deliberately NOT done in D4.4** — no make-before-break switch, no broker→baseline failover, no `QUOTES` on a broker provider, no Zerodha market-feed integration, no additional broker adapters.

### D4.5 — make-before-break switching + baseline failover (2026-08-21, ADR-035)

**The switch D4.4 stopped short of.** A connected account's feed can now become the primary QUOTES provider — but only after proving it can produce valid canonical data, only for the instruments it actually streams, and only for the user who owns it. The baseline is never disconnected; it moves to standby inside the same failover chain.

**Modified**

- `providers/streaming.py` — `FeedReadiness` (`REGISTERED → CONNECTING → CONNECTED → SUBSCRIBED → READY`, plus `FAILED` / `DISCONNECTED`), the readiness gate in `is_eligible_for`, per-symbol coverage with a 120s staleness bound, `mark_link_up` / `mark_link_down`, `bind_readiness_listener`, and `fetch_quote` answering from the last streamed tick. `capabilities` now includes `QUOTES`.
- `providers/base.py` — `ResolutionContext.capability` + `for_capability()`, so `is_eligible_for` can answer differently for a pushed capability than for the quote capability that displaces the baseline.
- `providers/registry.py` — `candidates_for` stamps the capability onto the context it filters with.
- `market_engine/normalizer.py` — the `canonical` family: one canonical tick → the platform's StockQuote shape.
- `market_engine/source_manager.py` — `publish_status(user_id=…)` publishes a user-scoped `provider.status`, change-gated per user; `forget_user_status`.
- `market_engine/gateway.py` — binds/unbinds the readiness listener alongside the tick sink; `_on_provider_readiness` announces a promotion or demotion to the owning user only.
- `brokers/instruments.py` — `InstrumentMap.symbols`, the account's canonical instrument universe.
- `brokers/market_feed.py` — `attach_market_feed(user_id, broker, symbols)` subscribes the provider; `set_market_feed_link` relays transport link state.
- `brokers/stream.py` — `on_link_state` callback, reported after the subscribe frames are away and on every exit from a transport run.
- `broker_engine.py` — passes the account's symbols to `attach_market_feed`, wires `_on_stream_link_state`.

**Why `PRIMARY` is not a provider state.** It is the head of one `resolve_feed` chain, recomputed every time from current readiness. Storing it would be a lagging copy of a derived fact, and would make it possible for two providers to believe they were primary for one quote stream — the state MARKET_DATA_ARCHITECTURE.md forbids. Because nothing stores it, promotion is atomic with no lock and no handover protocol, and make-before-break falls out for free.

**What "ready" means, and what it deliberately does not.** A record that survived coercion into a canonical `MarketTick`, on this link, while subscribed. Not the socket opening, not authentication, not a subscribe frame, and never elapsed time. Evidence is per link: a reconnect discards it and readiness is re-earned. A feed attached with no symbols can never become ready — the safe default.

**Two design decisions worth revisiting later.** (1) A symbol-less QUOTES resolution — which only `status()` / `active_tier()` / `diagnostics()` produce, since every fetch path supplies a symbol — reports the *feed*, so a promoted user's tier indicator reads `streaming` even though instruments the feed does not stream are still served (and individually labelled) by the baseline. (2) The stale-tick bound is 120s: longer than the baseline's own delay would mean serving something worse than the fallback under a `streaming` label.

**Known limitation — a tick-derived quote is thinner than a polled one.** A canonical tick carries no previous close, so the quote built from it has no `change` / `change_pct` and no OHLC. Stitching those from the baseline's last quote would present two readings at two timestamps as one and is fabrication. **No production caller passes `user_id` to `market_gateway.get_quote` today**, so nothing regresses now; wiring per-user quote routing into the REST surface is gated on the canonical tick growing those fields, which needs a real feed that populates them.

**Security.** Promotion and demotion move exactly one user's feed — verified against a second user and a no-user (guest) context across both transitions. `provider.status` for a per-user feed carries `user_id` and is delivered to that user alone, so one user's promotion neither moves another's indicator nor discloses that they have a broker connected. A dropped socket demotes but does not unregister; an *ended entitlement* still unregisters immediately. No broker name, provider name or credential material appears on any consumer surface.

**Validation.** Backend suite **3,138 passed**, 4 xfailed (baseline before D4.5: 3,114); the 15 `test_entrypoint_log_level.py` failures remain the documented pre-existing Docker baseline, unchanged. **24 tests added**, including the falsification pair that keeps the rest honest: `test_removing_the_readiness_gate_would_promote_an_unproven_feed` (gate neutralised → the feed takes the quote path immediately, so the "not promoted" assertions are known to be about the gate) and `test_breaking_before_making_is_what_the_ordering_test_would_catch` (baseline released first → a window with no quote provider at all, so the ordering assertions are known to be capable of failing).

**Deliberately NOT done in D4.5** — no Zerodha Kite market-feed adapter and no other broker adapters, no generalized provider re-probe, no probation windows / latency scoring / flap suppression (D5), no frontend tier-indicator work, no trading, portfolio or frontend changes.

### D4.6 — the Zerodha Kite market feed: the first concrete stream adapter (2026-08-21, ADR-036)

**What this sprint is, and what it deliberately is not.** D4.1–D4.5 built a generic streaming architecture and proved it end to end against a broker that does not exist (`NovaAdapter`). D4.6 puts a *real* broker's wire format through it. Zerodha is the platform's first concrete stream adapter; it is **not** the market-data architecture, and nothing outside `services/brokers/zerodha.py` learned that Kite exists. The pipeline it enters is unchanged from D4.5, end to end:

```
Kite ticker bytes → ZerodhaAdapter.decode_stream_frame → BrokerTick
    → InstrumentMap → MarketTick → StreamingTickProvider
    → Market Gateway → Source Manager → Event Bus → Market Engine
```

**Modified — three files, and only one of them is broker-aware**

- `brokers/zerodha.py` — the Kite protocol, corrected against Kite Connect v3 and given the one hook the handshake needs. `parse_kite_binary` rewritten (see below); `price_divisor` / `instrument_token` helpers; `STREAM_MODE` as a named decision; `stream_instruments` and `stream_subscribe_frames` coerce tokens; `stream_connect_error` classifies a refused handshake.
- `brokers/base.py` — `stream_connect_error(error) -> str | None` added to the adapter contract, default `None` (retry as normal). Generic; only the *answer* is a broker's.
- `brokers/stream.py` — one `try` around the connect call that asks the adapter what a connection failure meant. No broker name, no protocol detail; the file's core-module ban still holds.

Nothing changed in the Market Engine, the Market Gateway, the Source Manager, `StreamingTickProvider`, the provider registry, the canonical tick contract, the Portfolio Engine, the Trading Engine, or the frontend.

**Four protocol defects the audit found in code that already existed.** D4.2 moved Kite's framing into the adapter without re-deriving it against the spec. Tracing it did:

1. **Every segment was priced in paise.** Kite encodes the exchange segment in the *low byte of the instrument token* and quotes the currency segments at different scales (`cds` ÷ 10⁷, `bcd` ÷ 10⁴). A flat ÷100 prices a currency instrument four to five orders of magnitude wrong — a number that looks entirely plausible on a chart and would be marked against a real position.
2. **Tokens and prices were read signed (`>ii`).** Kite's are unsigned 32-bit. A token above 2³¹ comes back negative, matches nothing in the account's `InstrumentMap`, and drops every tick for that instrument — no exception, no log line, indistinguishable from an instrument that has not traded.
3. **A truncated frame was resynchronised rather than abandoned.** A packet whose declared length ran past the buffer left the reader misaligned, and a Kite packet is nothing but two integers — a misaligned read produces a plausible token at a plausible price, which is worse than producing nothing. It now stops at the damage. A packet that is merely *too short to price* is still skipped, because its own length prefix keeps the framing intact; the two cases are told apart deliberately.
4. **A persisted instrument token never reached the wire.** `stream_instruments` required `isinstance(token, int)`, but the same token is `"738561"` after a MongoDB round trip — the split `InstrumentMap` already documents on the *resolution* side. On the subscription side the failure is worse: the instrument is simply absent from the subscribe frame, so the wire never carries it and the user's feed is quietly narrower than their portfolio.

**The lifecycle defect: a dead Kite token reconnected forever.** Kite refuses a ticker handshake carrying a stale token with **HTTP 403, before any frame is exchanged** — so the `{"type": "error"}` frame the codec reads for a *mid-session* token death never arrives. Unclassified, the generic transport could not tell it from a broker outage: it reconnected on the backoff schedule indefinitely, the account's market feed stayed registered, and the user was never asked to reconnect. Kite invalidates every access token daily at ~06:00 IST, so that is every connected user, every morning. `stream_connect_error` is the fix and it is deliberately a *classification* hook, not a failover one — the adapter says what the failure meant, the transport raises its own `_AuthExpired`, and the existing expiry path (stop the stream, detach the market feed, notify) runs unchanged. Both `websockets` exception shapes are handled, because the two versions put the status in different places.

**Stream mode: LTP, as the repository already documented.** TASK.md and the adapter both stated LTP mode; D4.6 implements that and moves the decision into a named constant (`STREAM_MODE`) so it lives in one place with a test bound to it rather than to a duplicated literal. The tick feed marks holdings and open trades and answers streamed quotes — all three need a last price and nothing else. Quote mode multiplies every frame for OHLC and depth no consumer reads; full mode multiplies it again for a twenty-level book the platform has no surface for. **The cost, stated plainly: a Kite-derived `MarketTick` carries no volume**, because an LTP packet has none. The decoder reads only the first eight bytes of each packet — the token and last price, which every tradable mode puts there — so widening the mode later is a subscribe-frame change, not a decoder rewrite.

**Instrument scope is unchanged and deliberately not widened.** A Kite feed subscribes to the tokens on the account's synced holdings and positions, and `InstrumentMap` resolves the same set. A full Kite instrument dump (~80k rows/day) would be a catalog with its own storage, refresh schedule and staleness semantics — a sprint, not a line — and nothing in D4.6 needs it: a tick for an instrument the account does not hold has nothing to be joined to. Recorded as a limitation, not done silently.

**Known limitations**

- No volume on a Kite tick (LTP mode, above). The D4.5 limitation stands unchanged: a tick-derived quote still carries no `change` / `change_pct` / OHLC.
- Only holdings-and-positions instruments are streamed (above).
- **No wire-level unsubscribe.** Kite's `{"a": "unsubscribe"}` frame is not implemented, because the framework has no incremental-subscription caller: a portfolio sync restarts the stream, which resubscribes from the current holdings. Adding a frame nothing sends would be speculative. It is a one-line adapter addition the day an incremental caller exists.
- Kite's 3,000-instrument-per-connection cap is not enforced or sharded (D5 owns multi-connection sharding). A retail portfolio is nowhere near it.

**Security.** The ticker authenticates by query string, so its URL carries a live access token — the reason `BrokerStreamEndpoint.safe_url` exists, now pinned on the broker that can actually fail it rather than only on a header-authenticated fake. A full transport pass at DEBUG level with a live-looking token emits no credential material anywhere. No Kite identifier, credential or broker name reaches a `MarketTick`, a quote payload, or the frontend; one user's Kite feed is never resolved for another; an expired session detaches the feed immediately.

**Validation.** Backend suite **3,172 passed**, 4 xfailed (baseline before D4.6: 3,143); the 15 `test_entrypoint_log_level.py` failures remain the documented pre-existing Docker baseline, unchanged, and are the only failures. **26 test functions added / 29 cases** (the handshake-refusal test is parametrized across both `websockets` exception shapes × HTTP 401/403). flake8's blocking correctness subset is zero repo-wide, and all four changed files pass the *full* advisory standard. black/isort were deliberately not run: CI holds them blocking for **new** files only, and D4.6 adds none — reformatting the files it modified would bury the change. **15 source mutations** run and observed red in the right place, then reverted — including the four protocol defects re-introduced one at a time, the handshake classification neutralised, the readiness gate removed, the failover removed, the authenticated URL logged, a `broker == "zerodha"` branch planted in the Source Manager, and the InstrumentMap bypassed.

**LIVE VALIDATION WAS NOT PERFORMED.** `KITE_API_KEY` / `KITE_API_SECRET` are configured, but a Kite ticker connection needs a per-user `access_token`, which is only obtainable through an interactive browser login (`request_token` → `POST /session/token`). No connected Zerodha session exists in this environment. Everything above is deterministic validation against fixtures built from the Kite Connect v3 binary specification. A live smoke test — connect, subscribe, real tick, canonical tick, readiness, promotion over the baseline, disconnect, fallback — remains outstanding and must be run before this is called production-verified.

**Multi-broker acceptance.** All D4.1–D4.5 Nova tests remain green, and `test_zerodha_and_a_fictional_broker_stream_through_the_identical_transport` drives both through the same transport function in one test: binary versus text, numeric token versus trading symbol, integer paise versus a rupee string, query-string auth versus a header — one canonical shape out. `test_kite_added_no_kite_knowledge_outside_its_own_adapter` sweeps every module under `services/` for Kite's vocabulary in executable code and allows exactly one file.

**Deliberately NOT done in D4.6** — no Upstox, Angel One, Groww, INDmoney, Dhan or Fyers implementation; no D5 work (probation windows, latency scoring, flap suppression, generalized re-probe, connection sharding); no broker trading changes; no frontend changes.

### D4.7 — the Upstox market feed: the second concrete stream adapter (2026-08-21, ADR-037)

**What this sprint is for.** D4.6 closed with an open question, recorded in DECISIONS.md: whether D4.1–D4.6 had *generalised* or had merely *worked* for the one broker it was built against. Only a second broker can answer that, and Upstox shares essentially nothing with Kite at the wire:

| | Zerodha (Kite ticker) | Upstox (v3 market feed) |
|---|---|---|
| connections | **one**, ticks + orders multiplexed | **two**, one each |
| encoding | binary, bespoke framing | Protocol Buffers |
| instrument identity | 32-bit integer token | compound string key (`NSE_EQ|INE002A01018`) |
| price | integer paise, three segment scales | IEEE `double`, rupees |
| auth | credentials in the query string | bearer header |
| subscribe | two JSON **text** frames | one JSON **binary** frame |
| dead token | reported in an error frame *and* at the handshake | reported at the handshake |

**The answer: the market side generalised completely; the broker transport had one assumption left.** Nothing changed in the Market Engine, the Market Gateway, the Source Manager, `StreamingTickProvider`, the provider registry, the canonical tick contract, the readiness gate, the failover path, the Portfolio Engine, the Trading Engine or the frontend. `InstrumentMap` needed no extension either — it matches on the stringified identifier, so a compound key resolves through the same table an integer does.

What did not generalise was the assumption that **a broker's realtime surface is one socket**. Kite could not expose it; Upstox does. `BrokerStream` held one endpoint, one codec and one protocol, and `BrokerStreamManager` keyed on `(user, broker)` — so Upstox's second `start_stream` would have silently *replaced* the first: one feed live, one feed gone, nothing raised. This is reported rather than hidden, and the fix names no broker.

**The generic extension: stream channels.** `BrokerStreamChannel` is a name, a protocol and a codec. A broker declares one or more through `stream_channels()`; the default is a single channel backed by the five `stream_*` methods every adapter already implements, so **Zerodha is unchanged byte for byte** and a broker that has never heard of channels *is* a single-channel broker. Two things moved with it, both because a broker's connections fail independently:

- the link-state callback carries the channel, and `BrokerEngine` relays it to the account's market feed **only from the channel that declares it delivers ticks** — otherwise an order socket blinking would demote a market feed that is delivering prices perfectly well, and an order-socket reconnect would re-arm the readiness gate for a tick feed that is not connected;
- a decoded event is checked against the *channel's* `delivers` before the broker-level capability gate — a broker legitimately declaring TICK_STREAM on one channel would otherwise let its other channel deliver ticks it has no prices on.

`BrokerRegistry` gained the matching startup check: every declared realtime capability must be carried by some channel, channel names must be unique, and every channel must declare a protocol. Without it, a broker could declare TICK_STREAM that no channel delivers — the provider registers, the sockets connect, the reconnect loop is content, every tick is dropped by the narrowing, and from outside that is indistinguishable from a market with no trades in it.

**Protocol decisions, taken from Upstox's own SDK and not inferred from Kite**

- **Endpoint** `wss://api.upstox.com/v3/feed/market-data-feed`, bearer header, `Accept: */*`. Upstox answers the handshake with a 307 to a signed socket URL and `websockets` follows it, so the `/v3/feed/market-data-feed/authorize` REST step is deliberately not used — it would be an extra authenticated call whose response is a credential-bearing URL we would then have to keep out of every log line. **Nothing credential-bearing is in the URL**, the opposite of Kite's ticker, so no second masking mechanism was needed.
- **Subscribe** one frame, `{"guid", "method": "sub", "data": {"mode", "instrumentKeys"}}`, JSON encoded as **binary**. Both halves matter and neither is guessable from Kite: Upstox carries the mode inside the subscription (Kite needs a second `mode` frame), and Upstox *silently ignores a text frame* — a `str` here produces a socket that connects, reports its link up, subscribes to nothing and never ticks.
- **Mode `ltpc`**, decided against what Upstox's own modes carry rather than copied from Kite's LTP. `full` adds five depth levels, candles, greeks and open interest — a multiple of the bandwidth for fields no consumer reads — and `full_d30` is an Upstox Plus entitlement this platform must not require its users to hold.
- **Price in rupees.** `LTPC.ltp` is a proto3 `double`. Kite quotes in paise because its packets carry unsigned integers, which must be scaled to express a fraction; a double need not. Applying Kite's ÷100 would price every Upstox instrument at one per cent of its value — plausible on a chart, and marked against a real position.
- **Zero is absent.** proto3 omits a `double` whose value is zero, so the wire cannot distinguish "no price in this frame" from "a price of zero". Both are dropped in the codec; the canonical boundary rejects zero anyway (`MIN_STOCK_PRICE`), and a tick that got that far would mark a whole holding at nothing.
- **Session expiry** is reported at the handshake with HTTP 401 (403 for a withdrawn app authorisation), on **both** feeds. Upstox invalidates every token daily at 03:30 IST, so this is every connected user every morning — the same class of defect D4.6 found in Kite, present in the Upstox portfolio stream since before D4.7 and closed here. One classifier serves both channels so they cannot drift into disagreeing about what a dead Upstox session looks like.

**Protobuf without a protobuf dependency.** The v3 feed is Protocol Buffers, and `protobuf` is absent from `requirements.txt` by decision — PH2.8 removed it with grpcio after PH2.1 measured the image cost of packages no application module imports. Re-adding a C extension plus a generated `_pb2` build artifact to read one `double` out of a map is a poor trade, and `protoc` is not a build dependency here. The adapter carries a ~90-line proto3 reader instead, decoding only the fields the canonical contract can hold.

The risk in hand-decoding is getting the *schema* wrong, and a test written by the same hand as the decoder cannot catch that. So `tests/_upstox_proto.py` transcribes Upstox's **official** `MarketDataFeedV3.proto` into a `FileDescriptorProto` and serializes fixtures with **Google's** protobuf runtime: the bytes the tests feed the adapter are the bytes Upstox's own SDK produces. `protobuf` is pinned in **requirements-dev.txt only** — the production runtime gains nothing, and the oracle cannot pass by agreeing with the implementation it checks.

**Instrument identity needs no catalogue.** Upstox publishes a full instrument dump; requiring it would have made D4.7 a data-pipeline sprint. It does not: a synced holding or position already carries the Upstox instrument key beside the symbol and the exchange, which *is* the mapping table, in both directions — `stream_instruments` reads keys off it for the subscribe frame, and `InstrumentMap` reads it the other way to name an arriving tick. Same scope as Kite's: the account's own holdings and positions, and nothing else.

**Files changed**

- `brokers/upstox.py` — the market-feed channel, the proto3 codec, `stream_instruments`, `stream_channels`, `stream_connect_error`, TICK_STREAM declared. All Upstox wire knowledge, in the only module entitled to hold it.
- `brokers/streaming.py` — `BrokerStreamChannel` + `DEFAULT_STREAM_CHANNEL`. Generic; names no broker.
- `brokers/base.py` — `AdapterStreamChannel` (the free single channel) and `BrokerAdapter.stream_channels()`.
- `brokers/stream.py` — one connection per channel; channel-scoped codec, decode, dispatch narrowing and link reporting; registry keyed on `(user, broker, channel)`.
- `brokers/registry.py` — channel-coverage validation at registration.
- `brokers/gateway.py` — `stream_channels(broker)`, so the engine asks the gateway rather than the registry.
- `broker_engine.py` — starts one stream per declared channel; routes link state by channel; scopes expiry to the reporting channel and stops the account's remaining ones.
- `requirements-dev.txt` — `protobuf==5.29.6`, test oracle only.
- `tests/_upstox_proto.py` (new), `tests/test_broker_streaming.py`, `tests/test_broker_framework.py`.

**Known limitations**

- **No volume on an Upstox tick.** `LTPC` carries `ltq` — the *last traded* quantity, one trade's size — not the day's cumulative volume, which lives in the `full` modes as `vtt`. Mapping `ltq` to the canonical `volume` would put a number there that means something else, so it is left unset. Same limitation Kite's LTP mode has, reached independently.
- The D4.5 limitation stands: a tick-derived quote carries no `change` / `change_pct` / OHLC, and D4.7 deliberately does not solve it by stitching two providers together.
- Only holdings-and-positions instruments are streamed.
- **No wire-level unsubscribe or `change_mode`.** Upstox supports both; the framework has no incremental-subscription caller (a portfolio sync restarts the stream), and adding a frame nothing sends would be speculative.
- The `ltpc` 5,000-key limit is enforced by trimming with a warning rather than by sharding across connections (D5 owns sharding). A retail portfolio is nowhere near it.
- Upstox's 307 handshake redirect is followed by the `websockets` client; it is not independently exercised in tests, because the fixture socket is injected below the redirect.

**Validation.** Backend suite **3,238 passed**, 4 xfailed, 95 deselected (baseline before D4.7: **3,172** passed, 4 xfailed, 95 deselected); the 15 `test_entrypoint_log_level.py` failures remain the documented pre-existing Docker baseline, unchanged, and are the only failures. **49 test functions added, 2 rewritten** (the two that asserted Upstox has no tick feed — a fact D4.7 changes), for **+66 collected cases** (the handshake-refusal test is parametrized across both `websockets` exception shapes × HTTP 401/403 × both channels). flake8's blocking correctness subset is zero across every changed file; the one new file (`tests/_upstox_proto.py`) passes the *full* advisory standard, as the new-file ratchet requires, and no new advisory finding was introduced into any modified file. **12 source mutations** run, each observed red in the right place and reverted — a Kite assumption copied into the Upstox subscribe frame, Kite's paise divisor applied to a double, the instrument key used as a symbol, a raw payload returned instead of a `BrokerStreamEvent`, promotion on socket connect, TICK_STREAM undeclared, evidence never discarded on reconnect, the Yahoo baseline made ineligible, the auth headers logged, the ownership filter neutralised, a `broker == "upstox"` name planted in the Source Manager, and `InstrumentMap` bypassed.

**One mutation initially stayed green, and it found a real gap.** Neutralising `StreamingTickProvider._discard_evidence` changed nothing the suite could see, because demotion on link loss is driven by the readiness *state*. But the cache it clears is per symbol, and readiness is re-earned by *any* symbol: a feed that ticked A and B on link 1, lost it, reconnected and received one fresh tick for A would have answered a quote for **B from the dead link's price**, inside the freshness window, labelled `streaming`, while the delayed baseline underneath held something newer. `test_a_reconnected_upstox_feed_cannot_answer_from_the_dead_links_prices` closes it; the mutation is red now.

**LIVE VALIDATION WAS NOT PERFORMED.** An Upstox market-feed connection needs a per-user `access_token` obtainable only through an interactive browser OAuth login, and no connected Upstox session exists in this environment. Everything above is deterministic validation against fixtures encoded from Upstox's official schema by Google's protobuf runtime. A live smoke test — connect, subscribe, real tick, canonical tick, readiness, promotion over the baseline, disconnect, fallback — remains outstanding for **both** Zerodha (D4.6) and Upstox, and neither is production-verified until it is run.

**Second-broker acceptance.** `test_zerodha_and_upstox_speak_different_protocols_and_produce_identical_canonical_ticks` drives both brokers' real bytes through their real codecs and asserts the canonical `MarketTick`s are equal field for field — the equality *is* the proof the boundary holds, because a leak from either adapter would break it. `test_both_brokers_reach_the_market_gateway_through_the_identical_seam` runs the whole register → gate → promote → fail-over sequence for each in one test. `test_upstox_added_no_upstox_knowledge_outside_its_own_adapter` sweeps every module under `services/` for Upstox vocabulary in executable code and allows exactly two files: the adapter and its one-line registry entry.

**Deliberately NOT done in D4.7** — no Angel One, Groww, INDmoney, Dhan or Fyers; no D5 work (probation windows, latency scoring, flap suppression, generalized re-probe, connection sharding); no trading changes; no frontend changes; no expansion of the canonical tick contract.

### D4.9 — Angel One SmartAPI: the third concrete stream adapter (2026-08-24, ADR-038)

**The question this sprint answers.** ADR-037 closed with one: the multi-channel transport D4.7 introduced had never been exercised by a broker that did not need it, and "generalised" had been claimed on a sample of two. Angel One is the independent test, and it arrives from the *opposite* direction — its market feed is **one socket**, so it takes the free single-channel path rather than the multi-channel one.

| | Kite (D4.6) | Upstox v3 (D4.7) | Angel One (D4.9) |
|---|---|---|---|
| connections | one, ticks + orders multiplexed | two, one each | one, ticks only |
| encoding | binary, bespoke framing | Protocol Buffers | fixed 51-byte packets |
| a frame is | many packets | many feeds | **one tick** |
| byte order | big-endian | protobuf varint | **little-endian** |
| instrument identity | 32-bit integer token | compound string key | numeric token, **unique only per exchange segment** |
| price | integer paise, three segment scales | IEEE `double`, rupees | integer paise, currencies ×10⁷ |
| auth | credentials in the query string | one bearer header | **four headers** |
| subscribe | two JSON **text** frames | one JSON **binary** frame | one JSON **text** frame, grouped by segment |
| dead session | error frame *and* handshake | handshake (401/403) | handshake (401 + `x-error-message`) |
| keep-alive | protocol ping suffices | protocol ping suffices | **application `ping` every 30s or the broker closes the socket** |

**The answer: the market side generalised again, and one transport assumption was exposed for the second time.** Nothing changed in the Market Engine, the Market Gateway, the Source Manager, `StreamingTickProvider`, the provider registry, the canonical tick contract, the readiness gate, the failover path, the Portfolio Engine, the Trading Engine or the frontend. `InstrumentMap` needed no extension: the adapter builds a segment-qualified string on both sides of the boundary, so a third identity shape resolves through the same table.

What did not generalise is that **`ping_interval` is not a keep-alive**. It configures the WebSocket protocol's own ping frames, which the two libraries exchange without either application seeing them; Angel One does not count those and closes a connection that stops sending the *text* frame `ping` on the data channel. Left unsolved, the socket would connect, subscribe, deliver ticks for half a minute and be closed — repeatedly, on the reconnect schedule — which from outside reads as a flapping feed rather than a missing frame, and the account's market feed would spend its life re-earning readiness it keeps losing.

**The generic extension: `BrokerStreamEndpoint.heartbeat_frame` + `heartbeat_interval`.** Both default to `None`, so Zerodha and Upstox are unchanged. What the frame *is* stays broker knowledge (text here, a JSON envelope at the next broker); starting the timer and cancelling it with the connection is transport work, in `stream.py`, once. An adapter running its own timer was rejected for the reason ADR-032 rejected per-broker transports: it would own a task whose lifetime must match a connection it does not hold, and a task leaked per reconnect is forever on a flapping feed.

**A second generic change, in the engine.** `TOKEN_FIELDS` — the session fields encrypted at rest and cleared on disconnect — gained `feed_token`. Angel One's market feed authenticates with a *second* per-session credential, separate from the token its REST API takes; stored in plaintext it would have been the one field in `db.broker_accounts` outside SECURITY.md's encryption-at-rest rule, and it is enough (with the app key and client code) to open a feed for the account. The list is generic session-credential names, not a per-broker registry: an adapter's `exchange_token` decides which of them its broker issues.

**Protocol decisions, taken from SmartAPI's own WebSocket 2.0 contract and its published Python SDK — not inferred from Kite and not inferred from Upstox**

- **Endpoint** `wss://smartapisocket.angelone.in/smart-stream`, authenticated by `Authorization` (session JWT), `x-api-key`, `x-client-code` and `x-feed-token`. SmartAPI also documents a query-string form for browser clients (`?clientCode=&feedToken=&apiKey=`); it is deliberately not used, because it would put two live credentials into the string every connection log line names. **Nothing credential-bearing is in the URL.**
- **Auth model: the publisher login**, not `loginByPassword`. The latter takes the user's trading PIN and a TOTP code — a class of secret this platform must never hold. The redirect returns `auth_token` and `feed_token`; the client code the socket needs is not on the redirect, so `exchange_token` resolves it with a profile call, which means a session that cannot stream is never stored as connected.
- **Subscribe** one JSON **text** frame, `{"correlationID", "action": 1, "params": {"mode", "tokenList": [{"exchangeType", "tokens"}]}}`. Instruments are **grouped by exchange segment**, not listed flat — a flat list has nowhere to put the segment, and the segment is half the identity.
- **Mode 1 (LTP)**, decided against what SmartAPI's own modes carry. Quote (2) adds day OHLC, average price, cumulative volume and aggregate quantities at 123 bytes against 51; Snap Quote (3) adds the best-five book, open interest, circuit limits and the 52-week range at 379; Depth (4) is a 20-level book, NSE only, on its own 50-token quota.
- **Instrument identity is `(exchangeType, token)`, never the token alone.** SmartAPI token numbers are unique *within* an exchange segment, so NSE 2885 and BSE 2885 are different instruments. The adapter writes a segment-qualified `"1|2885"` onto every synced holding and position and rebuilds the same string from every decoded tick, so subscription and resolution are one expression. Had the bare token been stored — Kite's shape — a BSE tick would have resolved to an NSE holding and marked a position at another instrument's price, with nothing raised.
- **Price in paise, except currencies at ÷10 000 000.** The same *class* of trap as Kite's segment scales and a different rule: Kite reads its scale out of the low byte of a 32-bit token, while SmartAPI carries the segment as its own field. Copying `zerodha.price_divisor` here would consult a byte of a SmartAPI token that means nothing.
- **Depth packets are refused rather than priced.** Mode 4 reuses the 43-byte header and replaces everything after it, so decoding one at the price offset would publish a *quantity* as a rupee value.
- **Trading-symbol series suffixes are stripped at the boundary.** SmartAPI names an equity `TATASTEEL-EQ` where every other broker here names it `TATASTEEL`. Left alone, one stock held at two brokers would be two canonical symbols — a split portfolio, a split watchlist, and a feed whose coverage never matches the platform's instrument universe.
- **Session expiry** is reported at the handshake with HTTP 401 (403 for a withdrawn authorisation) and an `x-error-message` header naming the failing credential. SmartAPI sessions end at **midnight IST**, so this is every connected user, every day. The broker's header text is deliberately not shown to the user: it names which of our four credentials was bad, which is not an action they can take.

**Instrument identity needs no catalogue.** SmartAPI publishes a master scrip file; requiring it would have made D4.9 a data-pipeline sprint. It does not: a synced holding or position carries the SmartAPI token beside the symbol and the exchange, which *is* the mapping table in both directions. Same scope as Kite's and Upstox's — the account's own holdings and positions, and nothing else.

**Files changed**

- `brokers/angelone.py` (new) — the adapter: publisher-login auth, profile/holdings/positions/funds, the segment-qualified identity, the 51-byte codec, the subscribe frame, the handshake classification, the keep-alive declaration. All SmartAPI knowledge, in the only module entitled to hold it.
- `brokers/streaming.py` — `BrokerStreamEndpoint.heartbeat_frame` / `heartbeat_interval`, with both-or-neither validation. Generic; names no broker.
- `brokers/stream.py` — one keep-alive task per connection, started after the subscribe frames and cancelled in the same `finally` that closes the socket.
- `brokers/__init__.py` — the registry entry, one line.
- `broker_engine.py` — `feed_token` added to `TOKEN_FIELDS`; disconnect now clears every field in that list rather than three named ones.
- `security/secrets.py`, `backend/.env.example` (regenerated), `production.env.example`, `tests/_testenv.py` — `ANGELONE_API_KEY`, `ANGELONE_REDIRECT_URL`.
- `tests/test_broker_streaming.py`, `tests/test_broker_integration.py` (its broker-status assertion now reads the registry instead of a two-broker literal).

**Known limitations**

- **No volume on an Angel One tick.** LTP mode carries none, and the wider modes' `lLastTradedQty` is one trade's size rather than the day's cumulative `lVolumeTradedToday`. Putting either in the canonical `volume` would populate it with a number that means something else. Third broker, same limitation, reached independently each time.
- **No order capabilities and no order stream.** D4.9 is a market-data sprint; SmartAPI's order surface is unvalidated against a live account, and its order updates arrive on a *different* socket (`smart-order-update`) which would be a second channel. The capability model declares the broker partial rather than integrating it with stub methods that lie — the Broker Gateway refuses an undeclared capability before the adapter is reached.
- **No `SESSION_REFRESH`.** SmartAPI publishes a token-renewal endpoint, but it consumes a refresh token and the publisher-login redirect is documented as returning only `auth_token` and `feed_token`. Declaring a refresh whose input this platform may not hold would make the engine attempt a renewal that cannot succeed instead of asking the user to reconnect. **Open question for live validation.**
- The D4.5 limitation stands: a tick-derived quote carries no `change` / `change_pct` / OHLC.
- Only holdings-and-positions instruments are streamed. The 1,000-token session quota is enforced by trimming with a warning rather than by sharding across connections (D5 owns sharding; SmartAPI allows three sockets per client code, which is the headroom any sharding must fit inside). A retail portfolio is nowhere near the quota.
- **No wire-level unsubscribe.** SmartAPI supports `action: 0`; the framework has no incremental-subscription caller (a portfolio sync restarts the stream), and adding a frame nothing sends would be speculative.
- Series-suffix stripping covers the documented NSE/BSE cash series (`EQ`, `BE`, `BZ`, `BL`, `SM`, `ST`, `IQ`, `GB`, `GS`). A cash series outside that set would reach the platform with its suffix attached.

**Validation.** Backend suite **3,316 passed**, 4 xfailed, 95 deselected (baseline before D4.9: **3,238** passed, 4 xfailed, 95 deselected); the 15 `test_entrypoint_log_level.py` failures remain the documented pre-existing Docker baseline, unchanged, and are the only failures. **61 test functions added** for **+78 collected cases** (the handshake-refusal test is parametrized across both `websockets` exception shapes × HTTP 401/403). flake8's blocking correctness subset is zero across every changed file; the one new file (`services/brokers/angelone.py`) passes the *full* advisory standard, as the new-file ratchet requires, and no new advisory finding was introduced into any modified file. **20 source mutations** run, each observed red and reverted: Kite's price rule applied to Angel One, an Upstox-shaped identity, a raw dict returned instead of a `BrokerStreamEvent`, promotion on connect, promotion on subscribe, `_discard_evidence` neutralised, `InstrumentMap` bypassed in the engine, an unknown token used as a symbol, TICK_STREAM undeclared, the handshake classification removed, the feed token leaked into the URL, a `_is_angelone` branch planted in the market-feed seam, the ownership filter removed, eligibility taken from the link instead of the evidence, link loss made non-demoting, a codec exception made fatal to the stream, the `(user, broker, channel)` key collapsed, a fabricated volume, the keep-alive never started, and the keep-alive never cancelled.

**Four mutations initially stayed green, and each found a real test gap.**

1. **A planted `_is_angelone` helper survived the vocabulary sweep.** Two reasons at once: `_strip_source` removes string literals, so `broker == "angelone"` is invisible to it *by construction*, and the `\bangel` pattern used to avoid matching "changelog" also fails to match `_is_angelone`, where the preceding underscore is a word character. Both are fixed — the sweep now uses `angel(?!og)`, and a separate assertion bans broker-name *comparisons* on prose-stripped-but-literal-preserving source across the market-feed seam, the transport and the Source Manager.
2. **Making a codec exception fatal to the stream stayed green**, because this adapter *declines* damaged frames rather than raising on them, so the resilience test could not tell a transport that swallows a codec exception from one that re-raises it. A test that forces the codec to raise now covers it.
3 & 4. **Neither keep-alive mutation was caught**, because the timer test called `_start_heartbeat` directly and proved only that the helper works. A test driving the real transport pass now asserts the ping reaches a live socket and stops when it closes.

Two further mutations (promotion on connect, promotion on subscribe) are red in the generic D4.5 readiness suite rather than in the Angel One tests, and that is reported rather than papered over: the Angel One path always follows `connect()` with a link event, which resets the gate, so the broker-specific test cannot observe those two in isolation. The gate itself is what they break, and the gate's own tests catch them.

**LIVE VALIDATION WAS NOT PERFORMED.** An Angel One market-feed connection needs a per-user session obtainable only through an interactive SmartAPI browser login, and no connected Angel One session exists in this environment. Everything above is deterministic validation against fixtures built from SmartAPI's published byte layout, plus 20 source mutations observed red. **The outstanding live smoke test:** publisher login → callback carries `auth_token` + `feed_token` (**and confirm whether `refresh_token` is present**, which decides `SESSION_REFRESH`) → `getProfile` resolves the client code → socket connects with the four headers → subscribe → a real binary tick decodes → `BrokerTick` → `InstrumentMap` → `MarketTick` → readiness → promotion over Yahoo → **hold the connection past 60 seconds to prove the keep-alive** → disconnect → Yahoo fallback → reconnect → re-readiness → midnight-IST expiry classified at the handshake. A live smoke test now remains outstanding for **all three** streaming brokers, and none is production-verified until it is run.

**Third-broker acceptance.** `test_three_brokers_speak_three_protocols_and_produce_identical_canonical_ticks` drives all three brokers' real bytes through their real codecs and asserts the canonical `MarketTick`s are equal field for field. `test_four_users_on_four_providers_stay_on_their_own` runs Angel One, Zerodha, Upstox and the Yahoo baseline at once and pins that one broker's failure moves exactly one user. `test_angelone_added_no_angelone_knowledge_outside_its_own_adapter` sweeps every module under `services/` for SmartAPI vocabulary in executable code and allows exactly two files: the adapter and its one-line registry entry. **The adapter count went from two to three; the number of market-data architectures stayed at one.**

**Deliberately NOT done in D4.9** — no Fyers, Dhan, INDmoney or Groww; no Angel One trading, order stream or GTT; no D5 work (probation windows, latency scoring, flap suppression, generalized re-probe, connection sharding); no REST quote changes; no frontend changes; no expansion of the canonical tick contract.

### D4.10 — Fyers API v3: the fourth concrete stream adapter (2026-08-24, ADR-039)

**The question this sprint answers.** ADR-038 closed by reporting that the market side had generalised for a second consecutive broker and that the only thing which had not was in the transport again. D4.10 asks a fourth time and gets a different *kind* of answer: Fyers is the first broker that disagrees with the **framework** rather than only with its predecessors.

| | Kite (D4.6) | Upstox v3 (D4.7) | Angel One (D4.9) | Fyers (D4.10) |
|---|---|---|---|---|
| auth | query string | bearer header | four headers | **a FRAME on the data channel** |
| a frame is | many packets | many feeds | one tick | a batch of **mixed record kinds** |
| decodable alone? | yes | yes | yes | **no — snapshot, then deltas by topic id** |
| instrument identity | 32-bit integer | `"NSE_EQ|INE002A01018"` | `"1|2885"` (segment-scoped) | `"sf|nse_cm|2885"` (HSM topic) |
| price | integer paise, 3 segment scales | IEEE `double`, rupees | paise, currencies ×10⁷ | **`raw / (10**precision × multiplier)`, both ON THE WIRE** |
| exchange naming | — | — | segment **is** the exchange (`NFO`, `CDS`) | segment is **not** the exchange (all of `nse_*` → `NSE`) |
| keep-alive | protocol ping | protocol ping | text `ping` / 30s | **binary `00 01 0B` / 10s** |
| dead session | error frame + handshake | handshake (401/403) | handshake (401) | **a frame on an OPEN socket** |
| catalogue needed | no | no | no | no — though Fyers' own SDK uses one |

**The answer: the market side generalised for the third consecutive broker, and the streaming *contract* needed one method.** Nothing changed in the Market Engine, the Market Gateway, the Source Manager, `StreamingTickProvider`, the provider registry, the canonical `MarketTick`, the readiness gate, the failover path, `InstrumentMap`, the Portfolio Engine, the Trading Engine or the frontend.

**The generic extension: `BrokerStreamChannel.open(session, credentials)`.** It returns the channel's view of the connection about to be opened; the transport uses it for that connection's subscribe frames and decode, and drops it when the socket ends. **The default returns `self`**, so Zerodha, Upstox and Angel One are byte-for-byte unaffected and `stream.py` still names no broker.

Two things needed it and they are one problem — both are facts about *this socket and this user*, and both are invalidated by a reconnect:

  * **a credential in the opening frame.** `subscribe_frames(instruments)` takes instruments and nothing else, because every previous broker authenticates in the handshake. HSM's does not.
  * **a topic table.** A lite update is seven bytes: a server-minted topic id and a price. No name, no exchange, no scale — all of it established by an earlier frame on the same socket. A channel object is a registry singleton shared by every user of the broker, so a table held there would be shared across users *and* carried across reconnects. The failure is not an exception: one account's reconnect renumbers another account's instruments and a price is filed under the wrong company's name.

Widening `subscribe_frames` / `decode` instead was rejected for the reason D4.7 rejected widening the adapter methods: it changes what every channel and every test double implements, so an unmigrated broker fails on a live socket rather than at import.

**Protocol decisions, taken from Fyers' own reference client (`fyers-apiv3` 3.1.16, `FyersWebsocket/data_ws.py` + its bundled `map.json`) — the first adapter here sourced from a working client rather than a published byte table**

- **Endpoint** `wss://socket.fyers.in/hsm/v1-5/prod`. **Nothing credential-bearing is in the URL and nothing is in a header**, because HSM authenticates in a frame — `safe_url` has nothing to strip, which is the strongest form of the rule rather than an exception to it.
- **Auth model: standard OAuth2 authorization-code**, `appIdHash = SHA256("<app id>:<secret id>")`; the secret is never transmitted. The market feed's credential is the `hsm_key` claim decoded out of the session JWT — a second per-session credential like SmartAPI's feed token, except folded *inside* the first one, so nothing extra is stored and the field already encrypted at rest carries both.
- **The callback trap.** Fyers redirects with `?s=ok&code=200&auth_code=…`, where **`code` is an HTTP-style status and `auth_code` is the grant.** The inherited OAuth2 parser would read `code`, report success, and post `"200"` to `validate-authcode` — a connect that fails at the broker for a login that in fact worked.
- **Three opening frames, in order:** credential → mode → subscription, all `bytes`. The mode must precede the subscription, or full records arrive for instruments asked for in lite mode.
- **Mode is LITE (76)**, decided against what Fyers' own modes carry. Full mode (70) puts the whole 21-field record on the wire on every price change for fields no consumer reads.
- **Instrument identity is the HSM topic `"sf|<segment>|<exchange token>"`**, derived locally from the `fyToken` already on every synced row (`[:4]` → segment, `[10:]` → exchange token). It is the exact string the subscribe frame carries and the exact string the snapshot returns, so subscription and resolution are one expression and cannot drift.
- **The price scale is carried on the wire, per instrument.** `raw / (10**precision × multiplier)`. The trap is sharpest precisely because a copied ÷100 is **correct for NSE cash** and wrong for currency (`precision=4`) — it fails on a real position, not on the first tick anybody tests with.
- **The exchange is the exchange, not the segment.** All of `nse_cm`/`nse_fo`/`cde_fo`/`nse_com` are `NSE`, because a Fyers symbol is `EXCHANGE:NAME` and the exchange half is only ever NSE, BSE or MCX. Copying SmartAPI's table would make a tick report `NFO` for an instrument the account's own row calls `NSE`.
- **Depth topics are refused rather than priced.** Field zero of a scrip/index record is the last traded price; field zero of a **depth** record is the best bid.
- **Every record in a frame is walked, never sampled**, and every read is bounds-checked. The reference client slices bare and decodes past the end of a short frame.
- **Session expiry** is the token's own `exp` claim, with midnight IST as the fallback; a dead session is reported **in a frame on an open socket** (credential-response status ≠ `"K"`), which no predecessor does.

**Instrument identity needs no catalogue — and this is the broker where that is least obvious**, because Fyers' own SDK resolves symbols to feed tokens with an authenticated HTTP call plus a bundled segment map. It does not need to: the `fyToken` is already on every holding and position row.

**Files changed**

- `brokers/fyers.py` (new) — the adapter: OAuth2 auth, profile/holdings/positions/funds, the HSM topic identity, the request framing, the connection-scoped codec, the wire-carried price rule, the keep-alive declaration, the handshake and in-frame auth classification. All Fyers knowledge, in the only module entitled to hold it.
- `brokers/streaming.py` — `BrokerStreamChannel.open()`, defaulting to the identity. Generic; names no broker.
- `brokers/stream.py` — one connection-scoped codec per socket, established before the first frame and cleared in the same `finally` that closes the socket.
- `brokers/__init__.py` — the registry entry, one line.
- `security/secrets.py`, `backend/.env.example` (regenerated), `production.env.example`, `tests/_testenv.py` — `FYERS_APP_ID`, `FYERS_SECRET_ID`, `FYERS_REDIRECT_URL`.
- `tests/test_broker_streaming.py`.

**Known limitations**

- **No volume on a Fyers tick.** Fourth broker, same limitation — and the sharpest case, because Fyers publishes a *genuine* cumulative day volume and even sends it in the snapshot. Carrying it once and freezing it for the session is worse than absent.
- **One protocol requirement is knowingly unimplemented.** HSM's credential response carries an "acknowledge every N frames" count that the reference client honours with a ReqType-3 frame; a codec here returns a decoded event and cannot put a frame back on the wire, and extending the contract a second time on a detail never observed non-zero would be a guess. If the server enforces it, the feed goes quiet with the socket open — bounded by `StreamingTickProvider`'s tick-freshness backstop (the account falls back to the delayed baseline within two minutes) — and the adapter logs a named warning when the count arrives non-zero. **First item on the live-validation list.**
- **No order capabilities and no order stream.** D4.10 is a market-data sprint; Fyers serves order updates on a separate socket which would be a second channel.
- **No `SESSION_REFRESH`.** Fyers issues a refresh token, but redeeming it requires the user's trading **PIN**, which SECURITY.md forbids this platform from holding. Rejected rather than deferred.
- The D4.5 limitation stands: a tick-derived quote carries no `change` / `change_pct` / OHLC.
- Only holdings-and-positions instruments are streamed. The 5,000-instrument connection limit is enforced by trimming with a warning rather than by sharding (D5 owns sharding); subscriptions are batched 1,500 topics per frame, as the reference client does.
- **No wire-level unsubscribe, `change_mode`, or channel pause/resume.** HSM supports all three; the framework has no incremental-subscription caller.
- **Series-suffix stripping covers the documented NSE cash series plus `INDEX`.** BSE single-letter group codes (`-A`, `-B`, `-X`) are deliberately not stripped — a one-letter suffix is indistinguishable from part of a name, and stripping one wrongly renames an instrument permanently.
- The frame-length fields are reproduced exactly as the reference client computes them, inconsistencies included (true length for auth, `0` for mode, a number including strings not in the frame for subscribe). The server plainly does not read them; "fixing" them would be a hypothesis where a working client's bytes are evidence.

**Validation.** Backend suite **3,439 passed**, 4 xfailed, 95 deselected (baseline before D4.10: **3,315** passed, 4 xfailed, 95 deselected); the 15 `test_entrypoint_log_level.py` failures remain the documented pre-existing Docker baseline, unchanged, and are the only failures. The baseline run also showed a 16th failure — `test_api_errors.py::TestRateLimitIntegration::test_the_anonymous_tier_is_attached_to_real_endpoints` — which passes both in isolation and in the final full run; it is flaky under full-suite rate-limit state, unrelated to D4.10, and is called out rather than quietly absorbed into the numbers. **79 test functions added** for **+123 collected cases** (the handshake-refusal test is parametrized across both `websockets` exception shapes × HTTP 401/403). flake8's blocking correctness subset is zero across every changed file; the one new file (`services/brokers/fyers.py`) passes **black, isort and the full flake8 standard**, as the new-file ratchet requires, and no new advisory finding was introduced into any modified file.

**22 source mutations run, and all 22 were RED on the first pass** — each observed red at its intended test and reverted: Zerodha's fixed divisor for the wire-carried scale; an Upstox-shaped identity; a raw dict returned instead of a `BrokerStreamEvent`; promotion on connect; promotion on subscription acknowledgement; current-link readiness removed; stale evidence reused across a reconnect; `InstrumentMap` bypassed; an unknown topic used as a canonical symbol; TICK_STREAM undeclared; auth-expiry classification removed; the credential leaked into the URL; a `broker == "fyers"` branch planted in the transport; the ownership filter neutralised; Yahoo unregistered before Fyers earned readiness; a malformed frame made fatal to the connection; the keep-alive never wired; the subscription limit and batching removed; the canonical `MarketTick` bypassed; **the connection scope removed**; **the connection codec cached across reconnects**; and SmartAPI's segment names (`NFO`/`CDS`) copied into the Fyers exchange table.

**That every mutation was red first time is itself the finding**, and it is reported rather than claimed as virtue: the previous three sprints each had mutations that started green and exposed a real test gap. The difference here is that the tests were written against the two failure modes the connection scope exists to prevent — a *shared* topic table and a *reused* one — rather than against the happy path, so the two mutations most likely to slip through had dedicated tests before the mutation pass began. One mutation is worth naming: the planted `if self.broker == "fyers"` branch is invisible to the vocabulary sweep by construction (`_strip_source` removes string literals) and is caught only by the literal-preserving comparison ban D4.9 added after that exact mutation survived. It earned its place a second time.

**Two real defects were found during the sprint, both by tests rather than by review.** (1) `stream_connect_error` was implemented on the *adapter* only, so it was never consulted for a broker that declares an explicit channel — an expired token would have reconnected on the backoff schedule forever with the account's feed still registered. `FyersMarketFeedChannel.connect_error` closes it. (2) A test fixture's `fyToken` was 14 characters where a real one is 15, which the `[10:]` slice turned into the wrong exchange token — the fixture was wrong and the code was right, which is the correct way round and confirmed the slice rule.

**LIVE VALIDATION WAS NOT PERFORMED.** A Fyers market-feed connection needs a per-user access token obtainable only through an interactive browser OAuth login, and no connected Fyers session exists in this environment. Everything above is deterministic validation against fixtures built from the reference client's own framing, plus 22 source mutations observed red. **The outstanding live smoke test:** hosted login → callback carries `auth_code` (**and confirm `s`/`code` semantics on a real redirect**) → `validate-authcode` returns a JWT → **confirm the JWT carries `hsm_key`** → socket connects with no credential in the handshake → the credential frame is accepted (status `"K"`) → **confirm whether the acknowledgement count is non-zero**, which is the one open protocol question → subscribe in lite mode → **confirm a snapshot arrives before the lite updates**, since every later record depends on it → a real record decodes → `BrokerTick` → `InstrumentMap` → `MarketTick` → readiness → promotion over Yahoo → **hold the connection past 30 seconds to prove the keep-alive** → disconnect → Yahoo fallback → reconnect → **confirm topics are renumbered**, which is the premise of the connection scope → re-readiness → daily expiry classified in-frame. A live smoke test now remains outstanding for **all four** streaming brokers, and none is production-verified until it is run.

**Fourth-broker acceptance.** `test_four_brokers_speak_four_protocols_and_produce_identical_canonical_ticks` drives all four brokers' real bytes through their real codecs and asserts the canonical `MarketTick`s are equal field for field. `test_five_users_on_five_providers_stay_on_their_own` runs Fyers, Angel One, Zerodha, Upstox and the Yahoo baseline at once and pins that one broker's failure moves exactly one user. `test_fyers_added_no_fyers_knowledge_outside_its_own_adapter` sweeps every module under `services/` for Fyers vocabulary in executable code and allows exactly two files: the adapter and its one-line registry entry. `test_the_connection_scope_is_free_for_every_broker_that_does_not_need_one` asserts the new method's identity default for all three previous brokers. **The adapter count went from three to four; the number of market-data architectures stayed at one.**

**Deliberately NOT done in D4.10** — no Dhan, INDmoney or Groww; no Fyers trading, order stream or GTT; no D5 work (probation windows, latency scoring, flap suppression, generalized re-probe, connection sharding); no REST quote changes; no frontend changes; no expansion of the canonical tick contract.

### D4.11 — Dhan (DhanHQ v2): the fifth concrete stream adapter (2026-08-25, ADR-040)

**The question this sprint answers.** ADR-039 closed with a Review Date naming its own test: whether `BrokerStreamChannel.open()` had *generalised* or had merely served the broker that forced it. D4.11 is that test, and the answer is the one four sprints of work were aiming at — **Dhan needed nothing.** No transport change, no contract change, no new capability, no new event kind, no widened signature. One adapter module and one registry line. `stream.py`, `streaming.py`, `instruments.py`, `market_feed.py` and `ticks.py` are byte-for-byte unchanged by this sprint.

That is the first time in five brokers, and it is only evidence if the fifth broker was genuinely different. It was, on every axis the framework abstracts:

| | Kite (D4.6) | Upstox v3 (D4.7) | Angel One (D4.9) | Fyers (D4.10) | **Dhan (D4.11)** |
|---|---|---|---|---|---|
| auth | query string | bearer header | four headers | a FRAME on the data channel | **query string** |
| subscribe frame | binary | protobuf/JSON | JSON text | binary | **JSON text → a BINARY socket** |
| a frame is | many packets | many feeds | one tick | a batch of mixed records | **one tick** |
| decodable alone? | yes | yes | yes | no | **yes** |
| instrument identity | 32-bit integer | `"NSE_EQ\|INE002A01018"` | `"1\|2885"` (numeric segment) | `"sf\|nse_cm\|2885"` | **`"NSE_EQ\|1333"` (NAMED segment)** |
| price | integer paise, 3 scales | IEEE `double` | paise, currencies ×10⁷ | scale on the wire | **`float32` rupees — NO DIVISOR AT ALL** |
| volume | absent | absent | absent | absent | **cumulative day volume — the first** |
| keep-alive | protocol ping | protocol ping | text `ping`/30s | binary/10s | **none needed — server pings US** |
| dead session | error frame + handshake | handshake 401/403 | handshake 401 | frame on an open socket | **frame on an open socket (code 50)** |
| session expiry | fixed hour | fixed hour | midnight IST | midnight IST | **24h duration from login** |
| catalogue needed | no | no | no | no | **no** |

**Protocol sourcing.** Two independent sources, read *against each other* rather than one trusted: DhanHQ's published v2 documentation (Live Market Feed, Annexure, Authentication, Portfolio, Funds) and Dhan's own reference client `DhanHQ-py` (`src/dhanhq/marketfeed.py`), whose `struct` format strings are the authority on byte layout because they are what a working client actually reads. **Every binary test fixture is packed with the reference client's format strings**, not with the adapter's own constants — an adapter tested against fixtures built from its own offsets proves only that it is self-consistent.

**The two places the docs and the SDK disagree, and what each disagreement changed**

1. **`/holdings` `exchange`.** The published sample shows `"exchange": "ALL"`; the SDK's own response fixture shows `"exchange": "NSE"`. Both shapes are handled. A row naming a real exchange is a delivery holding and can only be cash, so the segment follows; a row saying `"ALL"` names no exchange, and a security id without a segment identifies two different companies. Defaulting `"ALL"` to `NSE_EQ` was **rejected**: right most of the time and wrong *silently*, publishing another company's price under the user's stock's name. Such a row keeps its symbol and carries no instrument id, and the count is WARNed. → LIM-D4.11-1.
2. **The login flow.** The SDK uses `/app/generate-consent`, which requires the user's `dhanClientId` **before they log in** — which a multi-tenant platform by definition does not have, since learning who the user is at Dhan is the point of the login. The `/partner/*` flow takes no client id and returns one on consume, and is what this adapter uses. The partner secret is a **request header** and appears in no URL, which matters because the consent login URL is shown to the user.

**The sharpest finding: Prev Close is shaped exactly like Ticker.** A Prev Close packet (response code 6) and a Ticker packet (code 2) are the same 16 bytes, `<BHBIfI`, with a `float32` at the same offset 8 — and Dhan sends one Prev Close **per instrument the moment a subscription lands**. A codec that priced "any frame with a float at offset 8" would publish **yesterday's close as today's price, once per holding, immediately after every connect and every reconnect**, marking a whole portfolio at stale prices with nothing raised anywhere. The response code is the only thing that separates them, so the priceable packets are a **table keyed on the response code** rather than a size check, and an unlisted code is never priced.

**Market-data mode: Quote (17), not Ticker (15) and not Full (21).** Decided entirely by what the canonical `MarketTick` can hold. Ticker (16 bytes) has no volume field, so every tick would be permanently half-empty. Full (162 bytes) adds open interest and five depth levels the canonical tick has nowhere to put, at three times the bytes. **Quote (50 bytes) is the narrowest mode that leaves nothing canonical unfilled** — and this is the first adapter in the sequence where the richer mode was chosen rather than rejected, because it is the first whose middle mode carries a field the contract already has.

**Volume semantics.** One Quote packet carries four volume-shaped fields: `LTQ` (this trade's size, offset 12), `volume` (the day's cumulative traded quantity, offset 22), `total_sell_quantity` (26) and `total_buy_quantity` (30). Only the second is what `MarketTick.volume` means. All four are distinct values in the fixtures, so reading the wrong one is a wrong number rather than a coincidence. **Dhan is the first of the five brokers to fill `MarketTick.volume` at all.**

**Session expiry, and the one judgement call.** Dhan reports a dead session **in a frame** (response code 50), the reverse of Angel One and Fyers, because the token rides in the query string so the socket opens first. Codes **807/808/809** stop the stream through the existing `AUTH_EXPIRED` path. Code **806** ("Data APIs not subscribed") is also treated as fatal, and that is the judgement call: it is an *entitlement* failure rather than an authentication one, but the closed `StreamEventKind` set offers exactly two outcomes — "stop" and "retry forever" — and retrying forever cannot make an unlicensed account licensed. The carried message names the entitlement rather than claiming an expired token. Code **805** ("too many active connections") is deliberately **not** fatal: Dhan drops the oldest socket when a sixth opens, so the next attempt may succeed, and killing a user's feed because they opened Dhan's own app would be the wrong trade. `stream_connect_error` is *also* implemented for handshake 401/403 as a second line.

**Files created**

- `backend/services/brokers/dhan.py` — the whole adapter, and the only module in the platform that knows Dhan exists.

**Files modified**

- `backend/services/brokers/__init__.py` — the registry entry (one import, one tuple member, one `__all__` line).
- `backend/security/secrets.py`, `backend/.env.example` (**regenerated** via `scripts/generate_env_example.py`, not hand-edited), `production.env.example` — `DHAN_PARTNER_ID`, `DHAN_PARTNER_SECRET`, `DHAN_REDIRECT_URL`.
- `backend/tests/test_broker_streaming.py`.
- `.claude/TASK.md`, `.claude/DECISIONS.md` (ADR-040), `.claude/BROKER_INTEGRATION.md`, `.claude/MARKET_DATA_ARCHITECTURE.md`.

**Known limitations**

- **LIM-D4.11-1 — a Dhan holding reporting `"ALL"` cannot be streamed.** Its symbol, quantity and P&L are unaffected; only the tick subscription is. The count is WARNed rather than swallowed, and how much this actually bites is **first on the live-validation list** — the SDK fixture suggests real responses may name the exchange. Resolving it properly means Dhan's security-master CSV, which is an instrument-catalogue sprint and was rejected as such for the fifth consecutive time.
- **Code 806 is reported to the user as a session that needs reconnecting**, which is technically wrong — the token is fine, the account is not licensed for the data feed. ✅ **CLOSED (D5.5, 2026-08-27, ADR-045).** The sixth `StreamEventKind` ADR-040 rejected was implemented once the approximation's cost was measured: it tore down a *valid* trading session and told the user their login had expired, which the message text cannot fix because the state is the lie. 806 now takes the `NOT_ENTITLED` path — the feed stops, the session, the other channels and the account's trading surface do not. Original text preserved: The message text says what actually happened; the *state* the engine moves to is the approximation.
- **DB-5 (broker-neutral) — a server-side "stop doing this" produces a tight reconnect loop.** ✅ **CLOSED (D5.1, 2026-08-25, ADR-041).** The ladder now resets only after a connection that *lasted* `STABLE_CONNECTION_SECONDS` (30s, taken from MARKET_DATA_ARCHITECTURE.md's probation window rather than invented), and every other outcome leaves it climbing to the 60s ceiling. Implemented as a generic `ConnectionStability` in the new `services/brokers/reliability.py`, driven from `BrokerStream._notify_link` — the one place that already knows a link transition happened exactly once — with one ladder per (user, broker, channel) so no user's flapping session paces another's. No adapter, no core service and no broker was touched. Original finding: The transport resets its backoff after any connection that completed, so a socket Dhan accepts and immediately closes with code 805 reconnects roughly every 1.5s indefinitely — against a broker whose documentation warns that further requests may get the user blocked. Real, affects all five brokers, and exposed rather than caused by Dhan. The fix is to reset the backoff only after a connection that lasted a minimum duration, which **is flap suppression**, which is D5's. Named rather than quietly fixed or quietly ignored.
- **No order capabilities and no order stream.** D4.11 is a market-data sprint; Dhan serves order updates on a separate socket, which would be a second channel.
- **No `MARGINS`.** Dhan's margin surface is a *calculator* pricing a hypothetical order, not a report of used and available margin, which is what the capability means everywhere else.
- **No `SESSION_REFRESH` and no `SESSION_INVALIDATE`.** Dhan publishes a token-renewal endpoint whose behaviour on a partner-issued token is unverified here, and no logout endpoint for the partner flow at all.
- **No series-suffix stripping.** Both official samples and the SDK fixtures show bare symbols; inventing a strip rule for a suffix this broker does not appear to send would risk renaming an instrument permanently. Flagged for live validation rather than guessed at.
- Only holdings-and-positions instruments are streamed. The 5,000-instrument connection ceiling is enforced by trimming **with a warning naming the number**, not by sharding (D5 owns sharding); subscriptions are batched at Dhan's documented 100 instruments per message.
- **No wire-level unsubscribe.** Dhan publishes codes 16/18/22/24; the framework has no incremental-subscription caller.
- The D4.5 limitation stands: a tick-derived quote carries no `change` / `change_pct` / OHLC.

**Validation.** Backend suite **3,494 passed**, 4 xfailed, 95 deselected (baseline before D4.11: **3,439** passed, 4 xfailed, 95 deselected). The 15 `test_entrypoint_log_level.py` failures remain the documented pre-existing Docker baseline, unchanged, and are the only failures in both runs. **55 test functions added**, all in `tests/test_broker_streaming.py` (421 collected in that file, up from 366). flake8's blocking correctness subset is **zero** across every changed file; the one new file (`services/brokers/dhan.py`) passes **black, isort and the full flake8 standard**, as the new-file ratchet requires. The two advisory `C901` findings in `security/secrets.py` are pre-existing, in functions this sprint did not touch, and verified unchanged against `HEAD`.

**An intermediate failure worth recording rather than hiding.** The first full run showed a **16th** failure — `test_secrets.py::test_example_file_is_in_sync_with_registry` — because `backend/.env.example` had been hand-edited. It is a *generated* file; the fix was to add the three specs to `security/secrets.py` and regenerate. The test was right and the edit was wrong, which is the correct way round.

**27 source mutations run; 25 went RED.** Applied one at a time, each observed at its intended test and reverted: price scaling introduced; prev-close made priceable; identity reduced to the bare security id; an unknown instrument renamed into a symbol; the codec bypassed with a raw payload returned; the truncated-frame guard removed; subscription batching removed; the connection-limit enforcement removed; auth-expiry classification removed; the disconnect frame never inspected; the transient 805 misclassified as fatal; the credential logged by the transport; a `broker == "dhan"` branch planted in the transport; Dhan vocabulary planted in the Source Manager; volume read from the last-trade quantity; a ticker packet given a volume it does not carry; the non-finite price guard removed; holdings `"ALL"` defaulted onto `NSE_EQ`; the subscribe frame sending the segment enum instead of its name; SmartAPI-style segment numbers copied into Dhan's table; an application keep-alive declared that Dhan does not need; a naive expiry timestamp read as UTC; the connection scope stopped being the identity; the provider name losing its per-user scope; and both readiness guards removed together.

**One mutation found a real test gap, and it is the finding of the sprint's test pass.** Removing the user id from the provider registry key left **every** isolation test in the file green — because every existing test puts *one* user on each broker, so the key stayed unique per broker and the collision was invisible. It is only visible with **two users on one broker**, resolved **through the registry** rather than through the object the attach returned; the attach never looks a feed up by name and every consumer does. `test_two_dhan_users_of_the_SAME_broker_never_share_a_feed` is the fix, and it now resolves both accounts through a real `SourceManager`. Fifth consecutive sprint in which a mutation found something review did not.

**One mutation stayed green for a good reason, reported rather than papered over.** Removing the evidence discard in `StreamingTickProvider.mark_link_up` changes nothing, because the same method also demotes a READY feed — and removing the demotion changes nothing either, because the discard covers it. Each line is individually redundant with the other; removing **both** is red. That is genuine defence in depth rather than a test gap, and manufacturing a test that pins one of the two lines would assert an implementation detail instead of the property.

**LIVE VALIDATION WAS NOT PERFORMED.** A Dhan market feed needs a per-user access token obtainable only through an interactive browser consent login, and no connected Dhan session exists in this environment. Everything above is deterministic validation against fixtures packed with the reference client's own `struct` formats, plus the mutation pass. **The outstanding live smoke test**, items unique to this broker in bold: partner consent → browser login → redirect carries `tokenId` → consume-consent returns `accessToken` **and `dhanClientId`, without which the feed cannot be opened at all** → socket connects with the token in the query string → **confirm no acknowledgement of the subscription arrives**, which this codec assumes → **confirm a Prev Close packet arrives per instrument at subscribe time**, which is the premise of the response-code table → a real Quote packet decodes → **confirm the float32 price needs no scaling against a known live price** → `BrokerTick` → `InstrumentMap` → `MarketTick` → readiness → promotion over Yahoo → **hold the connection past 40 seconds to prove the library's pong satisfies Dhan's ping** → **confirm whether a real `/holdings` row returns `"ALL"` or a real exchange**, which decides how much of LIM-D4.11-1 bites → **confirm whether trading symbols carry a series suffix** → disconnect → Yahoo fallback → reconnect → re-readiness → 24-hour expiry classified in a code-50 frame. A live smoke test now remains outstanding for **all five** streaming brokers, and none is production-verified until it is run.

**Fifth-broker acceptance.** `test_five_brokers_speak_five_protocols_and_produce_one_canonical_tick` drives all five brokers' real bytes through their real codecs and asserts the canonical identity fields are equal — and asserts separately that Dhan alone fills `volume`, because that difference is a protocol fact rather than an inconsistency. `test_six_users_on_six_providers_stay_on_their_own` runs Dhan, Fyers, Angel One, Zerodha, Upstox and the Yahoo baseline at once and pins that one broker's failure moves exactly one user, that a guest stays on the baseline, and that a Dhan reconnect mutates nobody else's subscription. `test_dhan_added_no_dhan_knowledge_outside_its_own_adapter` sweeps every module under `services/` for thirteen Dhan vocabulary terms and allows exactly two files. `test_the_four_existing_brokers_are_unchanged_by_the_fifth` re-runs every predecessor's price rule, keep-alive and connection scope after Dhan landed. **The adapter count went from four to five; the number of market-data architectures stayed at one.**

**Deliberately NOT done in D4.11** — no Groww or INDmoney; no Dhan trading, order stream, forever orders or super orders; no D5 work (probation windows, latency scoring, flap suppression including DB-5, generalized re-probe, connection sharding); no REST quote changes; no frontend changes; no expansion of the canonical tick contract; nothing committed and nothing pushed.

## D5.1 — Reconnect Flap Suppression (2026-08-25) — COMPLETE, uncommitted

**Status: implemented, tested, mutation-verified; LIVE VALIDATION NOT PERFORMED. Nothing committed, nothing pushed.**

**The defect (DB-5, named in D4.11).** `BrokerStream._run` reset its reconnect ladder the moment the transport coroutine returned, under a comment reading "clean close → quick reconnect". The code cannot see a clean close: a socket a broker accepted and closed one frame later reaches that assignment exactly as a socket that streamed all session. So a broker-side "stop doing this" produced connect → accept → close → reset → reconnect ~1.5s later, forever — a reconnect storm against a broker whose own documentation warns that continuing may get the user blocked. It survived four broker integrations because every individual line of the storm reads as a routine reconnect.

**The fix.** One condition — the ladder resets after a connection that **lasted** — expressed as a generic `ConnectionStability` model in a new module, `services/brokers/reliability.py`. Three outcomes: `STABLE` (link up ≥ 30s → reset the ladder, clear the flap streak), `SHORT_LIVED` (came up, died young → keep the ladder, count one flap), `NEVER_ESTABLISHED` (never reached link-up → keep the ladder, not counted as a flap). The ladder itself doubles, caps at 60s and is jittered exactly as it did in D4. A long-lived feed that drops still reconnects in ~1–2s; a feed that keeps dying young climbs to the ceiling and stays there.

**The 30 seconds is not invented.** It is MARKET_DATA_ARCHITECTURE.md's own probation window ("30 seconds of valid messages … this prevents flapping"). A connection that dies before the platform's published definition of "proved itself" has no claim on a reset backoff. One constant will serve both layers when D5.2 builds probation.

**Where it is driven from.** `BrokerStream._notify_link`, which is already change-gated and therefore reports one transition per real transition. A transport added to `PROTOCOL_RUNNERS` later inherits flap suppression by reporting link state, which it must do anyway, instead of by opting in. One model per `BrokerStream` — per (user, broker, channel) — so one user's flapping session cannot pace another user's reconnects and a broker's order socket cannot slow its market feed.

**Files.** New: `backend/services/brokers/reliability.py`, `backend/tests/test_stream_reliability.py`. Changed: `backend/services/brokers/stream.py` (82 insertions, 33 deletions, most of it documentation; the reconnect constants and `reconnect_pause` moved into `reliability.py` and are re-exported unchanged). **No adapter, no core service, no contract, no capability, no event kind and no frontend file was touched.**

**Falsification.** Ten mutations, **ten observed red**, each restored: DB-5's assignment reinstated; flapping "suppressed" by never resetting (the global-slowdown fix the brief forbids); the threshold comparison inverted; the ladder frozen; the ceiling removed; a never-established attempt counted as a flap; the flap warning deleted; the ladder made a per-broker singleton; the transport hook unwired; broker identity handed to the model. The broker-agnostic sweep runs against the source **with comments and strings left in** — stricter than the D3/D4 sweeps — and caught a broker name in this sprint's own docstring, which was rewritten rather than exempted.

**Regression.** Baseline before: 3494 passed, 15 failed (the documented `test_entrypoint_log_level.py` Docker failures), 4 xfailed, 95 deselected. After: 3513 passed, same 15 failures, 4 xfailed, 95 deselected — **19 added, zero new failures.** Blocking flake8 subset clean repo-wide; both new files clean under the full advisory config.

**Security.** `reliability.py` contains no credential-shaped identifier at all. The one new log line carries a broker name, a channel name, a user id and two counters — no session, no token, no frame. Pinned twice: once through a mocked logger and once through the real logging stack captured at DEBUG with live-looking fake credentials on the stream.

**Deliberately NOT done in D5.1** — no probation windows, no latency scoring, no stale-feed demotion, no failure classification beyond D4.6's auth expiry (so a broker-neutral representation of *entitlement* failure is still owed, and the code-806 approximation stands), no DB-1 health relocation, no instrument sharding, no give-up-after-N policy, no chaos tests, no REST quote changes, no frontend changes, no expansion of the canonical tick contract; nothing committed and nothing pushed.

**Limitations carried out of D5.1.**
- **LIM-D5.1-1 — a flap can only be produced by a broker that actually hangs up.** No live session exists in this environment, so the mechanism is verified deterministically and by mutation, never on a wire. Outstanding smoke test: hold a real session past 30s, drop it, confirm one reconnect at ~1–2s; then induce repeated immediate closes and confirm the interval climbs to the ceiling with the flap warning naming a rising streak.
- **LIM-D5.1-2 — the transport never gives up.** A permanently unlicensed or misconfigured session retries at the 60s ceiling indefinitely. Correct until failure classification can tell that apart from a broker having a bad ten minutes; `consecutive_short_connections` is exposed as the evidence that slice needs.
- **LIM-D5.1-3 — flapping does not yet influence provider selection.** A feed that flaps still re-earns readiness on each connection and can retake the primary position from a stable provider. That is D5.2's probation work, deliberately not smuggled into the transport. ✅ **CLOSED (D5.2, 2026-08-27, ADR-042).**


## D5.2 — Provider Probation & Stability (2026-08-27) — COMPLETE, uncommitted

**Status: implemented, tested, mutation-verified; LIVE VALIDATION NOT PERFORMED. Nothing committed, nothing pushed.**

**The defect (LIM-D5.1-3).** D4.5's readiness gate answers "can this feed produce a valid canonical price". The platform was reading the answer as "should this feed be preferred over one that already works". On a flapping link the two come apart: connect → one tick → READY → preferred → socket dies → baseline resumes → reconnect → one tick → preferred → … Every step is individually correct; the composite is a tier indicator alternating between live and delayed, with each promotion resting on a single packet from a connection that has repeatedly failed to survive.

**The model.** A second axis, orthogonal to readiness and derived rather than stored:

    READY / PROBATION   valid data is arriving; that is all that is known
    READY / STABLE      valid data has kept arriving across the whole window

A feed leaves probation when valid canonical data arrives at least `PROBATION_WINDOW_SECONDS` (30s) **after the tick that earned readiness on the current link**. That is MARKET_DATA_ARCHITECTURE.md's "30 seconds of valid messages" read literally: **silence inside the window proves nothing, and elapsed time alone proves nothing.** A feed that ticks once and goes quiet stays on probation forever — the plain-timer reading would promote it, over a baseline that is at that moment the only source actually producing prices.

**Probation ranks; it never filters.** `is_on_probation` is a term in the Source Manager's selection sort (health first, probation second), not an eligibility rule. A probationary feed stays eligible, stays in the failover chain, and becomes its head the moment no steadier candidate remains — so probation can delay a tier upgrade and can never produce "no provider". The filter implementation is the plausible wrong one and is invisible while a baseline exists; a falsification test mutates the code into exactly that shape and watches a live feed become unavailable.

**Scope.** Per provider instance, and there is one instance per (user, feed) — so per-user isolation and two-users-on-one-broker isolation are structural, not enforced by a rule. No new scoping code was written.

**One number, two layers.** `PROBATION_WINDOW_SECONDS` (provider) and D5.1's `STABLE_CONNECTION_SECONDS` (transport) are two names for one published policy, held apart only because the layers may not import each other, and pinned equal by a test. ADR-041 asked whether they are genuinely the same concept: the answer recorded in ADR-042 is **the same window measured on different evidence** — the transport can only see how long a socket lasted, the provider layer can see whether data kept arriving on it, so a silently open link is STABLE to the transport and still on probation here.

**Files.** New: `backend/tests/test_provider_probation.py`. Changed: `backend/services/market_engine/providers/streaming.py` (the model), `backend/services/market_engine/providers/base.py` (the generic `is_on_probation` default, `describe()`), `backend/services/market_engine/source_manager.py` (the ranking term), `backend/services/market_engine/gateway.py` (the listener now carries both axes — wording and docstring only), `backend/services/market_engine/providers/__init__.py` (exports), `backend/tests/test_broker_streaming.py` (a `no_probation_window` fixture on 38 pre-D5.2 tests, and two updated assertions). **No broker adapter, no transport, no codec, no contract, no capability, no event kind, no API and no frontend file was touched.**

**Falsification.** Fourteen source mutations, each restored. Twelve red on the first pass: the ranking term removed; the window set to zero; both halves of the link-loss reset removed together; link-up treated as stable; readiness alone treated as stable; probation turned into an eligibility filter; probation shared across provider instances; the rank inverted; the window measured against the clock instead of against arriving evidence; the promotion never announced; a broker name branched on inside the stability rule; the default clock changed from monotonic to wall time. **Two started green and are reported rather than papered over:** the two halves of the reset (`_discard_evidence` clearing the timestamps, and `_advance` re-stamping `_ready_since` from the new link's first tick) are each individually sufficient, so removing either alone changes nothing — the same defence-in-depth pattern D4.11 found in `mark_link_up`. **Two more were green until the suite was strengthened:** ranking probation above health (no test pinned the documented order — one was added), and sharing probation across instances (the first mutation was not faithful; a real one is red).

**Regression.** Baseline before: 3513 passed, 15 failed (the documented `test_entrypoint_log_level.py` Docker failures), 4 xfailed, 95 deselected. After: 3550 passed, same 15 failures, 4 xfailed, 95 deselected — **37 added, zero new failures.** Blocking flake8 subset clean repo-wide; every changed file clean under the full advisory config.

**Security.** Probation receives no credential and no broker identity: its whole input is two timestamps recorded by the provider that owns them. The one new log line carries a provider name and a duration. Consumer payloads are byte-identical to D4.5's — `provider.status` still carries tier, state and reason and no provider identity, asserted on both sides of a promotion. Probation is visible only on `describe()`, the admin/diagnostics surface where provider names already live. Pinned at DEBUG through the real logging stack with live-looking fake credentials attached to the feed, and by a test that a probationary feed cannot be resolved for another user.

**Deliberately NOT done in D5.2** — no latency scoring, no stale-feed demotion, no entitlement-failure classification, no Redis health (DB-1), no instrument sharding, no Groww, no INDmoney, no D5.3+; no change to the canonical tick, the broker contract, the REST quote path or the frontend; nothing committed and nothing pushed.

**Limitations carried out of D5.2.**
- **LIM-D5.2-1 — no live validation.** No broker session exists in this environment. The window is exercised deterministically with an injected clock at its published value, and through the real `attach_market_feed` seam against real elapsed time at a reduced window, for all five brokers plus a fictional one. Outstanding smoke test: hold a real feed past 30 seconds of live ticks and confirm the tier flips to streaming exactly once; drop the socket at 15 seconds and confirm the reconnected feed serves a fresh window rather than being promoted on its first tick; run two accounts on one broker and confirm one flapping session never moves the other's tier.
- **LIM-D5.2-2 — a promotion now costs up to 30 seconds of delayed data.** Intended, and stated plainly: a user connecting a broker mid-session stays on the baseline for a window longer than before D5.2. They see data throughout; what they no longer see is a live indicator about to become a lie.
- **LIM-D5.2-3 — stability does not decay.** ✅ **CLOSED (D5.3, 2026-08-27, ADR-043)** — and the sentence that follows turned out to be wrong on its central claim: the per-symbol backstop was *not* beneath the symbol-less resolution path, so a quiet feed was not bounded there at all. Original text preserved: Once a feed is stable it stays stable while it holds readiness on that link. A feed that goes quiet is still bounded by the existing per-symbol freshness backstop — it stops *covering* its instruments within `DEFAULT_TICK_MAX_AGE_SECONDS` and the baseline resumes — but it is not actively demoted, and the gap between the two windows (30s to prove, 120s to stop covering) is asymmetric on purpose: proving is cheap to demand, un-proving is a demotion and belongs with D5.3's stale-feed work.
- **LIM-D5.2-4 — 38 pre-D5.2 tests run with the window collapsed to zero.** They were written to assert that broker bytes reach a provider and that resolution follows readiness; at the real window each would have been asserting probation by proxy. The window is exercised at its published value in `tests/test_provider_probation.py`, including through the same real seam, and no assertion in those 38 tests was weakened or removed. The residual risk is that a probation bug reachable only through a full broker path would not be caught there — which is why the probation suite drives `attach_market_feed` for all five brokers rather than only synthetic providers.



## D5.3 — Provider Stability Decay & Stale-Feed Demotion (2026-08-27) — COMPLETE, uncommitted

**ADR-043.** Answers the question D5.2 wrote down and left: *should STABLE decay, or is coverage expiry beneath it sufficient?*

**The audit found the premise wrong, and that is the sprint's central finding.** Coverage expiry was not beneath it. `is_eligible_for` consulted the 120s window only when the resolution named an instrument; the symbol-less branch returned `True` on readiness alone. That branch is what `SourceManager.active_tier()`, `SourceManager.status()` and `MarketGateway.source_tier()` resolve through — the user's freshness indicator and the AI's freshness context. Reproduced against the real resolver, a feed silent for 10,000 seconds gave `covers() False`, `resolve(QUOTES, symbol) → yahoo`, `resolve(QUOTES, no symbol) → the dead feed`, `active_tier() STREAMING`, `stability STABLE`, `is_on_probation False`. So the platform served baseline prices while telling the user and the AI that the data was live — a data-honesty defect, not a ranking blemish. Second finding, same cause: `stability` compared two *past* instants with no upper bound, so a dead feed outranked a live one (`chain = [stale feed, yahoo, live feed]`).

**Audit answers.** **A.** Yes — demonstrated above. **B.** No: the backstop is sufficient per *instrument* and was never asked per *feed*. **C.** STABLE decays, but needs no decay state — it needs the coverage window it was already documented as sitting on, asked in both places. **D.** No: `consecutive_short_connections` lives in the broker layer the Market Engine may not import; carrying it across would be exactly the transport/evidence unification Rule 8 forbids, and it is unnecessary because D5.2's per-link evidence reset already makes a flapping feed re-serve probation from zero. The smallest broker-neutral path to carry it is *no path*. **E.** No — the predicate reads one timestamp written only by an accepted canonical tick.

**The change.** One derived predicate, `StreamingTickProvider.has_fresh_evidence` (an accepted canonical tick within `tick_max_age_seconds`), read in two new places: the stability rule and the symbol-less eligibility branch. Three logic lines. **One window, not two** — a second stale-feed constant would be two answers to one question, free to drift, and is pinned against. **No new state, no new constant, no new timer, no new registry**; decay is derived on read, so demotion needs no scheduler. **Recovery on the same link is immediate** rather than re-serving the probation window: the link never dropped, and requiring a re-proof would demote a feed for being illiquid rather than unreliable.

**Files.** New: `backend/tests/test_stale_feed_demotion.py` (34 tests). Changed: `backend/services/market_engine/providers/streaming.py` only. **`source_manager.py`, `gateway.py`, `base.py`, the registry, all five broker adapters, `stream.py`, `reliability.py`, `market_feed.py`, `instruments.py`, `ticks.py`, the canonical tick, the broker contract, the REST quote path and the frontend are unchanged.**

**Tests.** All sixteen areas of the brief: stable-with-ticks stays stable; stale after the window; stale cannot outrank healthy; Yahoo available at five points of the decay lifecycle; make-before-break intact (nothing unregistered, nothing disconnected, readiness gate not walked back); reconnect discards stability; silence creates no stability; socket connectivity creates no stability (connect / subscribe / link-up / link-up again, none moves the predicate); probation still ranks and never filters; per-user isolation; two users on one broker; guest baseline; all five real brokers plus fictional Nova through the real `attach_market_feed` seam; a comment-inclusive broker-name sweep; monotonic clock semantics including that the value is not cached.

**Falsification.** Thirteen source mutations, thirteen red: demotion removed; stale provider kept first; symbol-less branch reverted; staleness made permanent; wall-clock time; socket-as-evidence; stability preserved across reconnect; a genuinely global stale timestamp; per-user entitlement removed; a broker-specific stability branch; staleness as a filter; a truncated failover chain; a second looser stale window. **Two earlier attempts came back GREEN and are reported rather than hidden — both were malformed mutations, neither a test gap nor defence in depth:** one assigned a class attribute that `__init__` immediately shadowed with an instance attribute, and one multiplied the probation rank by a constant, which preserves sort order. Both were reformed and both are red.

**Regression.** 3584 passed (3550 baseline + 34 new), 15 failed — the documented pre-existing `test_entrypoint_log_level.py` Docker failures, unchanged — 4 xfailed, unchanged. No unrelated failures. flake8 clean on both changed/new files.

**Security.** The predicate's whole input is one monotonic timestamp recorded by the provider that owns it; it receives no credential, no payload and no broker identity. Entitlement is unchanged and asserted in both directions — a stale feed is refused to another user exactly as a fresh one is. Consumer payloads are unchanged: `status()` still carries `state`, `tier`, `reason`, `capabilities` and no provider identity, asserted on the stale path; decay is visible only on `describe()`, the admin/diagnostics surface where provider names already live.

**Deliberately NOT done in D5.3** — no D5.4 latency scoring, no instrument sharding, no chaos testing; no entitlement-failure classification; no DB-1 health relocation; no Groww or INDmoney; no change to `MarketTick`, the broker contract, the REST quote path or the frontend; nothing committed and nothing pushed.

**Limitations carried out of D5.3.**
- **LIM-D5.3-1 — decay is lazy, so it is not announced.** Leaving probation fires the feed-state listener from `on_raw`; decaying *into* probation happens on read with no event, so a consumer holding a rendered tier is not proactively told until the next status publish. Deliberate — the alternative is the per-feed timer the brief forbids — and it is the same lazy behaviour the per-symbol coverage backstop has had since D4.5. If it proves visible in practice, the fix is to evaluate staleness on the existing publish path, not to add a timer.
- **LIM-D5.3-2 — no live validation.** No interactive broker session exists in this environment. Outstanding smoke test: hold a real broker feed to stability, stop the instrument's ticks while leaving the socket up, and confirm the tier flips to `delayed` after the coverage window *with the socket still open* — the point being that the link is not the thing that changed; then confirm it returns to `streaming` on the next tick without re-serving the probation window.
- **LIM-D5.3-3 — freshness and latency can disagree, and D5.4 must reconcile them.** ✅ **CLOSED (D5.4, 2026-08-27, ADR-044) — by refuting its premise rather than by adding a precedence rule.** The limitation was written with *event-time* latency in mind, and the D5.4 audit found the platform cannot measure event-time latency at all. The latency it can measure — the interval between accepted canonical batches — is read off the *same series of arrival instants* freshness reads, so the two cannot contradict each other: freshness asks whether the current gap is inside the coverage window, latency asks what the typical completed gap is. A feed delivering every 90 seconds is fresh *and* slower than one delivering every 200ms, and both statements are true. Original text preserved: A feed whose ticks are consistently 90 seconds late is fresh by this predicate and bad by any latency measure. D5.4's scoring term must not end up contradicting this one about the same feed.



## D5.4 — Provider Delivery-Latency Scoring (2026-08-27) — COMPLETE, uncommitted

**ADR-044.** Answers ADR-043's review question and closes LIM-D5.3-3.

**The audit found the sprint's own specification unimplementable, and that is the central finding.** MARKET_DATA_ARCHITECTURE.md §7 asks for `latency_ms` "**where the provider supplies an exchange timestamp**", and the conditional is the operative half: **no provider supplies one.** `MarketTick` has five fields and none is an exchange instant — `ticks.py` states the reason and it is still good. Below the canonical line it is worse: Zerodha's LTP packet is `>II` (token, price) and carries no timestamp at all; Fyers lite says so in the adapter; the dependency-free Upstox decoder extracts price only, leaving `ltt` on the wire; Angel One gives epoch **milliseconds** and Dhan epoch **seconds**, both on an exchange clock whose offset from ours has never been measured; Yahoo is polled and has no arrival event. So `now − broker_timestamp` would have required widening `MarketTick`, editing five adapters and decoding two more wire fields, in order to difference two unsynchronised clocks in two units — for two sources out of six. **It was not implemented and not faked.** LIM-D5.4-1 records it as a prerequisite.

**Audit answers.** **A.** Delivery latency — the interval between accepted canonical batches, on the platform's own monotonic clock. **B.** Yes, entirely: `on_raw` already stamps `arrived_at` and stores it: no contract widened, no field added, no adapter opened. **C.** No — table above; no cross-broker number was invented. **D.** Median of a bounded 9-sample window: a median of N tolerates ⌊(N−1)/2⌋ outliers *by definition*, so "one outlier is not a permanent demotion" is arithmetic rather than a hoped-for behaviour; the window forgets by eviction rather than by a decay coefficient nobody can justify; odd, so the median is an observed interval. **E.** Latency ranks strictly *below* probation, so it can never promote an unproven feed — enforced by the position of the element, with no branch that says so. **F.** They cannot disagree: one series, two questions. **G.** `None`, which sorts last within its own group and is never zero. **H.** Twice: window eviction, and expiry with the feed's freshness. **I.** Structurally — the deque is an instance attribute and there is one instance per `(user, broker)`. **J.** No. Zero broker-layer changes and no seam created.

**The near-miss the ADR records.** Ranking unknown latency *best* — by analogy with `HEALTH_RANK` tying UNKNOWN with UP — looks like the generous choice and is a catastrophe: **Yahoo can never establish a delivery latency**, because it is polled and has no delivery event to time, so "unknown wins ties" would have promoted the permanent fallback above every streaming feed in its health/probation group and silently undone D4.5. Ranked last, the term leaves the baseline exactly where priority already puts it. And it does not recreate ADR-029's deadlock, for a structural reason: health improves only by the provider being *called*, whereas a pushed feed accumulates intervals whether or not it is the primary — evidence arrives without selection.

**A defect found by writing the tests, not by reading the code.** `test_latency_creates_no_readiness` failed on the first run: a feed that connected but never subscribed accumulated intervals and reported a cadence. Harmless for quotes (it is not an eligible candidate) and *not* harmless for the link-level TICKS capability, where readiness is not required — it would have carried a finite sort key into that comparison and could have led it on data the platform would never use. Fixed by gating `delivery_latency` on `is_ready`, the same gate `_fresh_tick` already applies, and pinned by `test_an_unready_feed_carries_no_latency_into_the_link_level_comparison`.

**Files.** New: `backend/tests/test_provider_latency.py` (51 tests). Changed: `backend/services/market_engine/providers/streaming.py` (the model), `backend/services/market_engine/providers/base.py` (the generic `delivery_latency` default and one `describe()` field), `backend/services/market_engine/source_manager.py` (the third sort element), `backend/services/market_engine/providers/__init__.py` (one export). **No broker adapter, no transport, no codec, no `MarketTick`, no broker contract, no capability, no event kind, no gateway, no registry, no REST route and no frontend file was touched.**

**Tests.** All twenty-four areas of the brief: no evidence / first observation / warm-up asserted at every intermediate count; established fast and slow feeds; the faster of two equivalents leading, asserted in both registration orders so a most-recently-registered implementation cannot pass; latency losing to stale-feed demotion when the stale feed is the *faster* one; latency losing to probation at the published window when the probationary feed is the faster one; latency creating no eligibility (wrong symbol, wrong user, link down) and no readiness; reset on both link-loss paths, with the outage explicitly proven not to be recorded as a sample; recovery; ageing asserted at the majority crossing; outlier tolerance pinned at exactly ⌊(N−1)/2⌋ and at one more; per-provider, per-user and two-users-one-broker isolation; guest baseline; Yahoo at five points of the lifecycle plus the two tests that hold the unknown-ranks-last decision; all five real brokers plus fictional Nova through the real `attach_market_feed` seam; a comment-inclusive broker-name sweep; monotonic semantics including a backwards clock; and the median pinned against the mean on a deliberately non-homogeneous window.

**Falsification.** Thirty mutations, thirty red — the sixteen the brief names plus fourteen more: per-tick instead of per-batch sampling, a zero-interval first sample, sampling rejected batches, the readiness gate removed, a shared module-level accumulator, `min` instead of median, the newest sample instead of the median, an unbounded window, the base default returning `0.0`, `describe()` reporting `0.0` for unestablished, the warm-up doubled, `disconnect` no longer clearing, latency leaked into the consumer status payload, and a broker-name branch in the scorer. **One earlier attempt was malformed and is reported rather than hidden:** M23 edited a docstring sentence rather than the return value, so it did not mutate behaviour and its anchor did not even match. It was reformed into `return 0.0` and is red.

**Regression.** 3635 passed (3584 baseline + 51 new), 15 failed — the documented pre-existing `test_entrypoint_log_level.py` Docker failures, unchanged — 4 xfailed, unchanged. No unrelated failures. flake8 clean on all changed and new files.

**Security.** The measurement's entire input is two floats from one monotonic clock; `_record_delivery_interval` takes one parameter and it is a `float`, so there is no path for a credential, a payload or a broker identity to reach it, and a change that fed it one would have to add a parameter. Verified through the real logging stack at DEBUG across a full lifecycle: no token, no secret, no price, no latency number. Consumer payloads are unchanged and asserted by shape — `status()` is exactly `{state, tier, reason, capabilities}` and `Resolution.as_status()` exactly `{state, tier, reason}`, both with no provider identity and no latency. Latency is visible only on `describe()`, the admin/diagnostics surface where provider names already live, as `null` when unestablished — never `0`, and never the sort key's infinity, which is not JSON and never leaves the comparison.

**Deliberately NOT done in D5.4** — no exchange-timestamp latency (LIM-D5.4-1), no p95 and no latency in `health()` (LIM-D5.4-3), no entitlement-failure classification, no DB-1 health relocation, no instrument sharding, no chaos testing, no Groww or INDmoney, no D5.5; no change to `MarketTick`, the broker contract, the REST quote path or the frontend; nothing committed and nothing pushed.

**Limitations carried out of D5.4.**
- **LIM-D5.4-1 — exchange-to-ingest latency is still not measured, and this is not it.** The platform reports how fast a feed *delivers*, not how stale each price was on arrival. A broker that batches 200ms of ticks and pushes them promptly is indistinguishable here from one that pushes each tick 200ms late. Closing it needs, in order: a decoded exchange timestamp on the brokers whose wire carries one, a field on `MarketTick` to carry it, and — the actual blocker — a defensible estimate of the offset between the exchange clock and ours, without which the subtraction is not a measurement. A prerequisite, recorded as one rather than approximated.
- **LIM-D5.4-2 — the delivery interval is a per-feed aggregate over a heterogeneous subscription.** A feed's median mixes every instrument it carries, so a feed on quiet instruments scores worse than one on busy instruments for a reason that is nothing to do with the feed. Fairer than it sounds — `attach_market_feed` subscribes all of a user's brokers to the *same* holdings-and-positions universe, so two feeds being compared usually carry the same instruments in the same market minute — but "usually" is not "always". The mitigation is that the term is a last-place tie-break behind health, probation and freshness, so the worst case is a suboptimal choice between two working feeds, never an outage. Becomes worth revisiting (possibly per-symbol) when two feeds on one account can be observed carrying genuinely different sets.
- **LIM-D5.4-3 — p50 only; no p95, and no latency in `health()`.** §7 asks for rolling p50/p95 and the adapter table named "measured latency" on `health()`. p95 of nine samples is "the largest one", which is an outlier detector rather than a score, and a sample large enough for a real p95 is a warm-up long enough to be a liability. `ProviderHealth` was left alone because health is counter-based evidence from past *calls* and a pushed feed makes none — folding a push-derived statistic into it would be the transport/evidence unification ADR-043 refused, one layer along. The adapter contract table has been corrected to say so rather than continuing to promise it.
- **LIM-D5.4-4 — no live validation.** No interactive broker session exists in this environment. **No real latency was measured and none is claimed.** Outstanding smoke test: connect two brokers on one account to the same instrument universe, hold both past the probation window, confirm both establish a median and the faster one leads the chain; then throttle or re-subscribe the leader to a quiet instrument and confirm the order changes only after the window refills — and, throughout, that the tier the user sees never leaves `streaming`, because a *ranking* term moved and nothing was demoted.


## D5.5 — Entitlement Failure Classification & Safe Recovery (2026-08-27) — COMPLETE, uncommitted

**ADR-045.** Closes the D4.11 code-806 approximation and the "richer failure classification" item D5.1–D5.4 each carried forward.

**The audit's finding is that the approximation was not cosmetic, and that is what justified widening a closed set ADR-040 had deliberately refused to widen.** `AUTH_EXPIRED` and an entitlement refusal have *different blast radii*: `_on_stream_expired` drops the cached session, stops **every** channel of the broker, audits `broker.session.expired` and pushes `session_expired: true`; an entitlement refusal leaves a **valid token** — REST portfolio, funds, order placement and the order stream all keep working. So the approximation destroyed a working trading session and told the user something untrue, and no message text can fix that because the *state* is the lie. The other direction is no better: `ERROR` deliberately leaves the connection alone, so a broker that closes the socket after sending one drives the reconnect ladder indefinitely — **paced** by D5.1 to the 60s ceiling, **never stopped** by it — with the account's provider still registered. The closed set genuinely could not express the semantics, which is the bar this sprint set itself before adding a member.

**What was added.** Exactly one `StreamEventKind` member (`NOT_ENTITLED`), one constructor (`BrokerStreamEvent.not_entitled`), one transport exception (`_NotEntitled`, deliberately **not** a subclass of `_AuthExpired`), one engine callback (`on_not_entitled` → `BrokerEngine._on_stream_not_entitled`), and one widened *return type* on `stream_connect_error` so a handshake refusal can be classified too. No new contract, no new capability, no new registry, no consumer-payload field, no `MarketTick` change, no timer, and no frontend file.

**The market side is byte-for-byte unchanged, and that is the sprint's strongest evidence for the D4.4/D4.5 decomposition.** `StreamingTickProvider`, the readiness gate, probation, freshness, the latency term, the Source Manager's sort key, the Market Gateway and the provider registry were not touched. An entitlement failure reaches the Market Engine as *a provider going away* — which it has known how to handle since D4.4 — through `detach_market_feed`, the path an ended entitlement has always taken. **Unregistration rather than demotion** is the deliberate choice: a demoted feed is still a candidate the moment nothing steadier remains, so there would be a state in which a feed that lost its entitlement serves quotes again. Unregistered, there is none.

**Scope.** Terminal for **one channel of one user's stream**. The engine detaches the market feed only when the refused channel is the tick-carrying one (the same `_channel_carries_ticks` gate D4.7 added for link state), so an order channel's refusal cannot demote a working market feed. Per-user isolation is structural — one `BrokerStream` per `(user, broker, channel)`, one provider per `(user, broker)` — and is asserted with **two users on one broker resolved through the registry**, the arrangement ADR-040 found is the only one in which a broker-scoped mistake is visible.

**Entitlement is never inferred**, and each way of inferring it has its own test: socket open, subscribe frame accepted, timeout, silence, malformed frame, codec exception. This is the sharpest rule in the sprint because the failure it prevents is silent and permanent.

**Files.** New: `backend/tests/test_provider_entitlement.py` (44 tests). Changed: `backend/services/brokers/streaming.py` (the kind + constructor), `backend/services/brokers/stream.py` (the terminal path, the handshake classifier, two extracted finishers), `backend/services/brokers/base.py` (the `stream_connect_error` contract), `backend/services/brokers/dhan.py` (the disconnect table split in two), `backend/services/broker_engine.py` (the handler + one wiring line), `backend/tests/test_broker_streaming.py` (the 806 assertion, updated deliberately). **No market-engine module, no `MarketTick`, no capability, no gateway, no registry, no REST route and no frontend file was touched.**

**Tests.** All twenty areas of the brief: classification from a frame and from a handshake; one connection and no more, with an ERROR control proving the comparison is not vacuous; ten identical refusals still costing one connection; a deliberate reattachment being the only way back; expiry and ordinary disconnect staying distinguishable; five separate ways of *not* inferring entitlement; the feed leaving quote eligibility and the baseline resolving immediately; a previously READY and a previously STABLE feed both unable to remain selected; a second user of the same broker, a second broker of the same user and the guest context all unaffected; the session, the other channels and the trading surface surviving; the owner alone being told; the consumer payload asserted by exact key set; a DEBUG pass through the real logging stack with a live-looking token in a query string; a comment-inclusive broker-name sweep over the regions this sprint wrote; an executable-code sweep and an identity-branch sweep over every generic module; all five shipped brokers still on the one transport; and a fictional sixth broker using the mechanism with no core change. Plus D5.2–D5.4 regression: probation still requires canonical evidence, reconnect still resets it, stale feeds still demote lazily and never outrank a fresh baseline, latency remains a tie-break, and Yahoo still cannot acquire a finite latency.

**Falsification.** Twenty mutations, twenty red — the fifteen the brief names plus five more: the collapse performed at the contract constructor rather than at the adapter, the handshake string answer ignored, `_NotEntitled` made a subclass of `_AuthExpired`, the refusal routed through the capability gate, the channel gate removed, and the finished stream left in the registry holding the account's session. **One earlier attempt is reported rather than hidden:** the first form of the "refusal affects Yahoo" mutation had an ambiguous anchor and never applied; it was reformed against a unique anchor and is red. **The sprint's own broker-name sweep caught a broker name in the `StreamEventKind` docstring this sprint wrote**, which was rewritten rather than exempted — the same outcome D5.1's sweep produced.

**Regression.** 3679 passed (3635 baseline + 44 new), 15 failed — the documented pre-existing `test_entrypoint_log_level.py` Docker failures, unchanged — 4 xfailed, unchanged. No unrelated failures. flake8 clean on every changed and new file (`_run`'s complexity was kept under the ceiling by extracting the two terminal finishers rather than by raising it). isort clean on the new file; `black --check` still reports the same pre-existing reformat on `stream.py`, `streaming.py` and `broker_engine.py` that it reports at HEAD — advisory in this repo, and nothing was mass-formatted.

**Security.** Verified end to end at DEBUG through the real logging stack, driving the real fifth-adapter codec with a live-looking JWT access token, a client id and a partner secret, and a credential-bearing query-string endpoint: 21 log lines, **no token, no client id, no secret and no query string** in any of them. The one new log line carries a broker name, a channel name, a user id and the broker's own message text. The audit row is `{"broker": <name>}` and nothing else. The consumer status after the refusal is exactly `{state, tier, reason, capabilities}` with `tier: delayed`, no provider identity, no broker vocabulary and no wire code. `user_id` scoping is preserved: the `provider.status` republish reaches the owner alone.

**LIVE VALIDATION: NOT PERFORMED.** No interactive broker session exists in this environment.

**Deliberately NOT done in D5.5** — no D5.6, no p95 latency, no DB-1 health relocation, no DB-5 or flap-suppression rework (the audit confirmed it is separable: an entitlement refusal is terminal by classification and never reaches the ladder), no generalized re-probe, no instrument sharding, no chaos testing, no Groww, no INDmoney, no live broker integration; no change to `MarketTick`, the market-engine layer, the REST quote path or the frontend; nothing committed and nothing pushed.

**Limitations carried out of D5.5.**
- **LIM-D5.5-1 — only one shipped adapter classifies an entitlement failure today.** The mechanism is generic and is exercised by a fictional broker through the real transport, but four of the five shipped brokers publish no entitlement-specific code, so their 401/403 handling remains session expiry — which is what their documentation says it is. A statement about those protocols, not a gap in the mechanism; it closes per broker as each is shown to distinguish the two.
- **LIM-D5.5-2 — the user is told their tier moved, not why.** The consumer surface is the existing user-scoped `provider.status`, which correctly reports the baseline as *available*, so a user whose feed was refused sees `delayed` with no explanation and the reason lives only in the audit row. Closing it needs a consumer-payload field and a frontend change, neither of which D5.5 has a mandate for.
- **LIM-D5.5-3 — a refused feed never retries, and nothing re-probes it.** Intended, and also a one-way door until a lifecycle event: an entitlement granted *while connected* does not bring the feed back until the user reconnects the broker or the process restores sessions. A generalized re-probe is Phase 5 work ADR-029 already owes for demoted providers; this is its second caller.
- **LIM-D5.5-4 — no live validation.** Outstanding smoke test: connect an account that genuinely lacks the data-API entitlement; confirm the socket opens and the refusal arrives in a frame; confirm exactly one connection is made; confirm the tier falls to `delayed` **while the same account still fetches its portfolio and places an order**; then grant the entitlement and confirm that reconnecting the broker — and only that — brings the feed back.

## D3 — Debts carried into D4

- **DB-1 — Broker health is process-local.** `BrokerHealth` lives on the registry's adapter instance, so a multi-worker deployment has one health view per worker and the Admin Portal sees whichever worker answers. Acceptable while health is diagnostic; it needs a shared store (Redis, as `infrastructure/` already provides) before it drives any automatic behaviour.
- **DB-2 — The per-user connected-broker registry is process-local and not restored at startup.** `SourceManager._connected_brokers` is populated only by live lifecycle events, so a restart forgets who is connected until each user's session is exercised. `BrokerEngine.load_sessions()` is the natural place to re-publish `broker.connected` for restored sessions; deferred because nothing consumes the registry yet. Closing this is a prerequisite for D4's feed switch.
- **DB-3 — Stream transports still live in `stream.py` rather than in the adapters.** ✅ **CLOSED (D4.2, 2026-08-21) — by a different mechanism than proposed.** Moving each *transport* into its adapter would have duplicated the reconnect loop, the auth-expiry path and the capability checks into every broker, which is exactly the code where copies diverge and one broker quietly stops reconnecting. The split that actually holds is **transport generic, codec broker-owned**: `stream_endpoint()`, `stream_subscribe_frames()` and `decode_stream_frame()` carry the whole wire format, and `stream.py` keeps only connection management. It now contains no broker name, no endpoint literal, no `struct` and no `json`, and adding a WebSocket broker changes nothing in it. `PROTOCOL_RUNNERS` survives as an empty override table for a protocol that is not a WebSocket at all. Full reasoning: **ADR-032**.
- **DB-4 — `/api/zerodha/*` legacy routes remain.** A deprecated public surface with its own URL prefix (not a framework leak); they delegate to the Broker Engine. Retire with a deprecation window.

## D1 — Debts carried into D2 (tracked, tested, not hidden)

**D2 status (2026-08-20).** DD-5 closed. DD-6 and DD-7 opened.

**DD-1/DD-2 reconciliation (2026-08-20, post-D2).** Both CLOSED for the public
market-data contract. The provider-shape reconciliation that blocked them is
done, the public REST contract no longer carries provider identity, and the
frontend branches that read it are migrated. Residue is tracked as DD-1a/b/c
below — none of it is a contract leak.

- **DD-1 — Gateway bypasses.** ✅ **CLOSED for the public market-data contract (2026-08-20).** `/api/market/overview`, `/gainers`, `/losers`, `/sectors`, `/global`, `/commodities`, `/fii-dii` and `/api/stocks/{symbol}/intraday` now read through `market_gateway`. The blocking shape reconciliation is resolved: `/api/market/sectors` emits the canonical `name` **and** keeps `sector` as a deprecated alias of the same value, so unmigrated consumers keep working while `Dashboard.jsx` and `Markets.jsx` move to `name`. The alias lives in the route, not in `normalizer.py`, so the canonical model stays clean; remove it once no consumer reads `sector`.

  Migrating the routes exposed a real defect and fixed it: `MarketGateway.get_sectors` iterated whatever the provider returned, so a provider answering with a dict where a list belongs was iterated into its *keys* and raised `AttributeError` — an unhandled 500 on a dashboard route from a merely-wrong shape. Malformed payloads are now logged and dropped, per MARKET_DATA_ARCHITECTURE.md.

  `KNOWN_GATEWAY_BYPASSES` still lists `server.py` and the service modules below, because each retains *other* direct provider calls. Those are Developer Rule 2 debt, not public-contract debt.
- **DD-2 — `source: "yahoo_finance"` in the public REST contract.** ✅ **CLOSED (2026-08-20).** Removed from every market-data response and replaced with `source_tier` (`"delayed"` / `"streaming"`), read from the Source Manager via `market_gateway.source_tier()` rather than written as a literal. `real_market.py` no longer stamps a provider name into its own payloads. `InvestmentAdvisor.jsx` and `Markets.jsx` now branch on `source_tier`.

  The field was retired rather than retained because retaining it was not actually backward compatible: a literal `"yahoo_finance"` would have kept reporting Yahoo the day a broker feed served the quote, so the field would not merely have leaked provenance — it would have reported the *wrong* provenance, and `InvestmentAdvisor.jsx` branched on exactly that value to label a live broker feed "Fallback data". Compatibility is preserved everywhere it is meaningful: every other field, every shape, every status code, and the sectors legacy key alias. This closes ADR-028's outstanding approval item. Guarded by `TestPublicContractCarriesNoProviderIdentity`, which sweeps every public market endpoint's response and carries a control proving the sweep can actually observe a leak.

- **DD-1a — Alpha Vantage is selected inside a route handler.** `/api/stocks/{symbol}/live` and `/intraday` branch on `av_configured()` before falling back to the gateway, which is caller-side provider selection (Developer Rule 3). The *observable* breach is closed — both branches return `source_tier` and no provider name — but the branch remains. Closing it properly means one `MarketDataProvider` adapter for Alpha Vantage plus a registry entry, which belongs with the sprint that adds it.

- **DD-1b — `/api/stocks/{symbol}/live` still reads `real_quote` directly.** Left on its own data path deliberately: the gateway normalizes to the canonical StockQuote, which drops `currency`, `market_state` and the `historical_*` series this endpoint returns, so routing it through `get_quote` would be a silent breaking change. It carries `source_tier` and no provider name. Reconciling the canonical model with this endpoint's richer shape is its own piece of work.

- **DD-1c — `backtest_engine` returns `data_source: "yfinance"`.** Deliberately unchanged. It is a historical-simulation provenance marker paired with `provenance: "derived"` and `mock_metrics: []` — the PH3.9 control that distinguishes a real backtest from the removed `data_source: "synthetic"` fallback. Backtesting is not the live feed the Market Gateway governs, and rewording the field would weaken an anti-fabrication control to satisfy a rule aimed at a different surface.
- **DD-3 — Derived analytics inside the provider module.** RSI/MACD/VWAP, market breadth, sentiment scoring and gainer/loser ranking live in `real_market.py`. They are Market Engine business logic; relocating them is D2.
- **DD-4 — Unadapted providers.** FII/DII (NSE India public API), news (RSS), Gift Nifty (its own adapter chain) and the economic calendar are collectors the gateway calls directly. D2 folds them into the registry.
- **DD-5 — Frontend provider labels.** ✅ **CLOSED (D2, 2026-08-20).** `FinancialStatements.jsx` and `FundamentalsPanel.jsx` now read "live market data". No live UI surface names a provider. `Settings.jsx` and `AdminAPIs.jsx` still do, which MARKET_DATA_ARCHITECTURE.md permits for settings/diagnostics surfaces; `Landing.jsx` names Yahoo Finance as a marketing integration credit, which is a copy decision rather than an architecture breach but will read oddly once broker streaming ships.

- **DD-6 — A demoted provider cannot recover on its own.** It is last in the failover chain, the chain stops at the first provider that answers, and health only improves on a successful call — so a provider that blips past `DEGRADED` stays on the lower tier until an external `record_success`, a process restart, or Phase 5's periodic re-probe. Deliberate: probation windows and re-probing are Phase 5 (D5) in MARKET_DATA_ARCHITECTURE.md and need a clock source and background sweeper D2 has no other use for. D3's broker adapter is the natural first caller. Pinned by `test_a_demoted_provider_has_no_self_recovery_path_in_d2` so D5 has a red-to-green target.

- **DD-7 — Frontend tier indicator.** Partially delivered. `Markets.jsx` renders a Live / Delayed badge from the REST `source_tier` as a by-product of the DD-1 reconciliation (it replaced a badge that branched on `source === "yahoo_finance"`). What remains is the *reactive* indicator: no component subscribes to `provider.status` on the Event Bus, so a mid-session tier flip or an `UNAVAILABLE` state is not reflected until the next fetch, and no other surface shows the tier at all. The backend publishes everything needed — `state`, `tier`, `reason`, `previous_tier` — and `MarketGateway.status["feed"]` exposes the same. Deferred as a frontend feature (out of the D2 brief's scope); it becomes user-visible value when D3 gives the tier something to flip between.

---

# Future Ideas

AI Copilot

Voice Commands

Options Analysis

Market Replay

Portfolio Simulator

Strategy Marketplace

Community

Mobile Apps

Desktop App

International Markets

Enterprise Edition

---

# Definition of Done

A task is complete only when:

✓ Feature Implemented

✓ Backend Complete

✓ Frontend Complete

✓ Database Updated

✓ APIs Working

✓ Tests Passing

✓ Documentation Updated

✓ Security Reviewed

✓ Responsive

✓ Accessible

✓ Production Ready

---

# End of Master Tasks
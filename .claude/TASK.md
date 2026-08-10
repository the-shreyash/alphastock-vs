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
- [x] WebSocket — Kite ticker (binary LTP + order updates) & Upstox portfolio stream, auto-reconnect w/ backoff
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
- Upstox market-data ticks use a protobuf feed — out of scope; order updates
  stream via the JSON portfolio feed. Zerodha ticks stream in LTP mode.
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
- [ ] PH3.2 Mock Data Eradication (ADR-021) — NOT_STARTED — High (parallel-safe now)
- [x] PH3.3 Frontend Test Foundation & Smoke Suite — **COMPLETE (2026-08-10)** — Critical — *Delivered under the sprint label "PH3.2 — Frontend Testing & UI Regression Foundation"; report: `docs/testing/PH3.2_FRONTEND_TEST_CERTIFICATION.md`.* **Numbering note:** the sprint brief called this PH3.2, but this tracker's PH3.2 is *Mock Data Eradication*, which remains NOT_STARTED and untouched — read "PH3.2" in `docs/testing/` as this line item. **313 tests / 17 suites, green in ~8s**, against a bar of ≥15 smoke tests. **Jest 27 + React Testing Library 16 through `craco test`** — the runner already inside `react-scripts`; no second framework introduced. **Vitest, the previously documented target, was rejected on evidence:** it runs tests through esbuild while this app ships through webpack/CRA, so the suite would validate a transform that never reaches production. **MSW was also rejected** — CRA 5 / Jest 27 predate `package.json#exports` resolution and MSW v2 is exports-only ESM needing Web-streams polyfills under jsdom; interception happens instead at the **axios adapter**, the app's real transport boundary, so the bearer-token and 401-silent-refresh interceptors run for real in every test. **Covered:** authentication (login, register, logout, session restore, expired session, Google OAuth callback incl. missing/rejected `state`), routing and guards **driven off the real route table** (`AppRouter` exported from App.js) so a deleted guard fails the suite, admin access control (non-admin and signed-out both bounced; `admin` and `super_admin` both admitted), dashboard shell, paper-trading order entry, AI workspace, watchlist, notifications, admin dashboard, and the realtime store's reducers — every critical screen asserted in **all four states: loading / success / empty / error**. **Coverage baseline: 33.6% overall statements / 77.0% critical-path**; `services/api.js` **100%**, `Login.jsx` **100%**, `AIAssistant.jsx` **100%**, `formatters.js` **100%**, `AuthContext.jsx` 97.6%, `PaperTrading.jsx` 96.1%, `tradeService.js` 94.3%. Overall is low **by design** — it counts ~30 feature pages this sprint did not scope (Portfolio, TradeMonitor, StockDetail, Markets, News, Settings, ten admin pages); inflating it with shallow render-smoke tests was explicitly declined. **Five frontend defects found and fixed:** (FE-001) `formatApiError(detail) || err.message` could never reach `err.message` because the left side always returned a non-empty string — every client-thrown message, including "Google sign-in is unavailable right now.", was silently replaced by a generic one; fixed by extracting the duplicated-and-drifted helper from Login/Register into `utils/apiError.js`, which also now distinguishes transport failures from application errors. (FE-002) auth error banners carried **colour only** — no `role="alert"` — so a screen-reader user got no signal that sign-in failed. (FE-003) **paper trading rendered a failed load as an empty account** — zero balance, "no open paper trades" — which a trader reads as *my positions are gone*, not *the server is down*; now an explicit error state with retry. (FE-004) form labels on Login, Register and the order ticket were **visually** present but not programmatically associated (no `htmlFor`/`id`). (FE-005) icon-only controls (chat send, watchlist remove, notification close) had **no accessible name**. **One pre-existing defect found and deliberately NOT fixed** (FE-007, out of sprint scope, documented for the owning sprint): **`yarn build` fails** at `[eslint] Failed to load config "react-app" to extend from` — `eslint@^9` in devDependencies displaces the `eslint@^8`/`eslint-config-react-app@^7` that `react-scripts` requires. **Attribution verified rather than assumed:** stashed to pristine pre-sprint `package.json`/`yarn.lock`, reinstalled from the lockfile, reproduced the identical failure. The application compiles cleanly — `DISABLE_ESLINT_PLUGIN=true yarn build` succeeds with all PH3.2 changes. **Regression: PH1 + PH3.1 backend suite re-run green — 1,035 passed, 95 deselected, 152s**, exactly the PH3.1 baseline. **Carried, not fixed:** CI frontend job still a placeholder (PH2.6 wiring), no E2E layer (PH3.9), no coverage gate (PH3.11), and the silent-load-failure pattern fixed in PaperTrading **still exists** in Dashboard, Watchlist, AdminDashboard and NotificationPanel (FE-006) — pinned by tests at current behaviour so the deferred fix has a starting point and cannot regress further.
- [ ] PH3.4 Frontend Service & Hook Coverage — NOT_STARTED — Medium
- [ ] PH3.5 API Contract & Error-State Testing — NOT_STARTED — High
- [ ] PH3.6 Backend Decomposition (server.py → Routers) — NOT_STARTED — Medium
- [ ] PH3.7 Performance Benchmarking & Load Testing — NOT_STARTED — Medium
- [ ] PH3.8 Accessibility & Responsive Audit — NOT_STARTED — Medium
- [ ] PH3.9 End-to-End Critical Journeys — NOT_STARTED — High
- [ ] PH3.10 Documentation Synchronization — NOT_STARTED — High
- [ ] PH3.11 Regression & Release Test Protocol — NOT_STARTED — High
- [ ] PH3.12 Production Certification & Launch Readiness — NOT_STARTED — Critical (final gate)

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

Status: PLANNING

Priority: Critical

Design approved and documented in MARKET_DATA_ARCHITECTURE.md (2026-07-16); ADR-026 recorded in DECISIONS.md. Documentation system synchronized.

Implementation phases (per MARKET_DATA_ARCHITECTURE.md):

- [ ] Phase 1 — Formalize Provider Adapter contract; wrap Yahoo path as YahooPollingAdapter
- [ ] Phase 2 — Source Manager + provider.status events + frontend tier indicator (Live/Delayed)
- [ ] Phase 3 — Zerodha Kite WebSocket adapter; per-user switching; failover to Yahoo
- [ ] Phase 4 — Remaining broker adapters (Upstox, Angel One, Fyers, Dhan)
- [ ] Phase 5 — Hardening: latency scoring, flap suppression, probation, chaos tests
- [ ] Phase 6 — Enterprise/licensed feeds (future)

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
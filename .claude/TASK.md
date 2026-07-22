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
Dockerfile), PH2.2 (Production Docker Compose) and PH2.3 (Secrets Management) are
complete — the backend stack boots healthy from a single command with segmented
networks, named volumes, no hardcoded credentials, and credentials deliverable as
file-mounted Docker secrets rather than plaintext environment variables. Next:
**PH2.4 (Environment & Configuration Framework)**, with **PH2.2b (Frontend
Production Dockerfile)** outstanding and parallelizable.
Deferred-within-PH1 items to schedule in the PH1 tail or
alongside PH2: PH1.9 Real-Time/WebSocket Security (Socket.IO auth, R-15) and
PH1.10b Admin Hardening & Session Management. PH3.1 (Backend Test Suite Repair —
the pre-existing `test_trading_engine::test_run_cycle_trails_and_books_targets`
failure + legacy live-server test migration) may run in parallel.

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
- [ ] PH2.4 Environment & Configuration Framework — NOT_STARTED — High
- [ ] PH2.5 CI Pipeline Foundation — NOT_STARTED — Critical
- [ ] PH2.6 CI Extended: Docker, Security & Integration — NOT_STARTED — High
- [ ] PH2.7 CD & Release Automation — NOT_STARTED — High
- [ ] PH2.8 Database & Redis Production Configuration — NOT_STARTED — High
- [ ] PH2.9 Structured Logging — NOT_STARTED — High
- [ ] PH2.10 Monitoring, Metrics & Alerting — NOT_STARTED — High
- [ ] PH2.11 Backup & Disaster Recovery — NOT_STARTED — High
- [ ] PH2.12 Infrastructure Certification & Staging Sign-off — NOT_STARTED — Critical (gate)

## PH3 — Production Quality Assurance

- [ ] PH3.1 Backend Test Suite Repair & Hermeticity — NOT_STARTED — Critical (parallel-safe now)
- [ ] PH3.2 Mock Data Eradication (ADR-021) — NOT_STARTED — High (parallel-safe now)
- [ ] PH3.3 Frontend Test Foundation & Smoke Suite — NOT_STARTED — Critical
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
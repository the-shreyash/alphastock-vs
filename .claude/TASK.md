# StockAssist AI
## Master Tasks

Version: 1.0

Status: Active Development

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
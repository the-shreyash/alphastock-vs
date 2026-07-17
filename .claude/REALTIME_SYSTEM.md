# StockAssist AI
# Real-Time System Architecture

Version: 1.1

Status: Active Development

Priority: Critical

---

# Vision

StockAssist AI is not a website that occasionally fetches stock prices.

It is a **real-time AI-powered market operating system**.

The platform must feel alive from the moment the user opens it.

Users should never need to refresh the page.

Prices should move.

Charts should animate.

AI should think continuously.

Portfolio values should change instantly.

Scanner results should appear automatically.

News should stream in.

Notifications should arrive immediately.

The platform should behave similarly to professional platforms such as:

• Zerodha Kite
• Upstox Pro
• TradingView
• Bloomberg Terminal
• ThinkOrSwim

---

# Philosophy

The frontend should never ask:

"Has anything changed?"

Instead the backend should continuously tell the frontend:

"Something changed."

This is Event Driven Architecture.

---

# Core Principle

Never Poll.

Always Push.

Old Way

Browser

↓

fetch()

↓

fetch()

↓

fetch()

↓

fetch()

↓

Refresh UI

Professional Way

Market

↓

Backend

↓

Redis

↓

Socket.IO

↓

Frontend

↓

Animate Changes

---

# High Level Architecture

        Market Data Providers
   (Broker WebSockets · Licensed Feeds
      · Yahoo Finance · News APIs)
                  │
                  ▼
           Market Gateway
     (Provider Adapters · Auth · Health)
                  │
                  ▼
           Source Manager
   (Provider Priority · Switching · Failover)
                  │
                  ▼
         Data Normalization
                  │
                  ▼
          Validation Layer
                  │
                  ▼
             Redis Cache
                  │
                  ▼
           Market Engine
                  │
        ┌─────────┼─────────┐
        │         │         │
        ▼         ▼         ▼
   AI Agents   Scanner   Portfolio
        │         │         │
        └─────────┼─────────┘
                  ▼
           Event Bus
                  │
                  ▼
            Socket.IO Server
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
 Dashboard  Mobile App  Admin Portal

---

# Market Engine

The Market Engine is always running.

It should never stop while markets are open.

The Market Engine never talks to providers directly. All real-time data originates from the Market Gateway, which normalizes every provider (broker WebSocket, licensed feed, Yahoo Finance) into one universal event model. See MARKET_DATA_ARCHITECTURE.md.

Responsibilities

Consume normalized market events from the Market Gateway

Validate prices

Cache updates

Publish events

Maintain health

Trigger AI

Generate alerts

The Market Engine is the heartbeat of the platform.

---

# Market Data Flow

Suppose NIFTY changes.

Active Provider (broker WebSocket, licensed feed, or Yahoo —
selected automatically by the Source Manager)

↓

Market Gateway

↓

Normalize Data

↓

Validate

↓

Redis Cache Updated

↓

Market Engine Detects Change

↓

Publish Event

market.index.updated

↓

Socket.IO

↓

Dashboard

↓

Only NIFTY Card Updates

↓

Green Animation

↓

Mini Chart Updates

↓

User Sees Live Change

The page never refreshes.

Only the affected component updates.

---

# Event Driven Architecture

Everything in StockAssist AI is event based.

Every change creates an event.

Examples

market.index.updated

market.stock.updated

portfolio.updated

scanner.breakout

scanner.volume_spike

scanner.momentum

trade.created

trade.executed

trade.closed

trade.pnl.updated

watchlist.updated

news.breaking

notification.created

ai.started

ai.completed

broker.connected

broker.disconnected

market.open

market.close

heartbeat

Every module subscribes only to events it needs.

---

# Redis Pub/Sub

Redis is the communication layer.

Market Engine

↓

Redis

↓

Socket.IO

↓

Frontend

Redis is never accessed directly by the frontend.

Redis provides

Caching

Pub/Sub

Temporary storage

Fast lookups

Rate limiting

Job queues

---

# Socket.IO

Only one Socket.IO connection per user.

Never create multiple socket connections.

Socket Events

market

portfolio

scanner

trade

watchlist

news

ai

notification

heartbeat

Each page subscribes only to required channels.

---

# Transport Layer

The platform uses a persistent real-time connection between the backend and frontend. The preferred implementation is FastAPI native WebSockets. Socket.IO is an acceptable alternative when its additional protocol features are required. The event-driven architecture, Redis Pub/Sub, and connection lifecycle are the primary requirements—not a specific transport library.

# Live Dashboard

The dashboard should always feel alive.

Live Components

NIFTY

Sensex

Bank Nifty

India VIX

Portfolio

Watchlist

News

AI Activity

Trade Monitor

Notifications

Market Breadth

Sector Performance

Each widget updates independently.

Never rerender the whole page.

---

# Live Scanner

The scanner never waits for refresh.

Workflow

Scanner Worker

↓

Scans NSE

↓

Finds Breakout

↓

Publish Event

scanner.breakout

↓

Socket.IO

↓

Scanner Card Appears

↓

GSAP Animation

↓

AI Starts Analysis

↓

Notification Sent

↓

Watchlist Updated

---

# Live Portfolio

Broker WebSocket

↓

Portfolio Service

↓

Redis

↓

Socket.IO

↓

Portfolio Card

↓

PnL Updated

↓

Number Animation

↓

Green / Red Flash

↓

Allocation Chart Updates

---

# Live Trade Monitor

Trade Created

↓

Waiting

↓

Entry Hit

↓

Position Open

↓

PnL Streams Live

↓

Target 1

↓

Trailing Stop

↓

Target 2

↓

Exit

↓

Trade Closed

↓

Journal Updated

↓

AI Trade Review Starts

---

# Live Watchlist

Every stock inside the watchlist updates independently.

Price

Volume

Change %

Signal

AI Rating

Recommendation

Support

Resistance

No refresh required.

## Implementation (Sprint R8)

Two streams feed watchlist rows through the shared price store:

price stream (15s)   { SYMBOL: { price, change_pct } }        — broadcast "prices"

watchlist.quotes (120s) { quotes: { SYMBOL: { price, change_pct,
                          rsi, volume_ratio } } }              — watchlist channel

Rows patch every streamed field (including recomputed since-added P&L) from
`priceTicks`; the REST refetch survives only as a disconnected fallback.

watchlist.updated { user_id, action: added|removed, symbol } — published by
the add/remove REST endpoints, delivered per-user, syncs every open surface
(Watchlist page, Dashboard widget, other tabs) without a poll.

---

# Live News

News Worker

↓

Collects News

↓

AI Categorization

↓

Sentiment Analysis

↓

Relevant Stock Mapping

↓

Publish Event

news.breaking

↓

News Card Appears

↓

Notification

## Implementation (Sprint R8)

`news_service` tags every article with deterministic `sentiment`,
`importance` ("high" | "normal") and `is_breaking` (keyword classifier —
crashes, RBI/rate decisions, record highs, SEBI actions, M&A, …). The
heartbeat news scan publishes:

news.received { articles, count }   — latest headlines, replaces the live list

news.breaking { articles, count }   — novelty-gated (2h cooldown per headline
                                      via filter_breaking_novel); every event
                                      is genuinely new

The frontend merges both into the News page and Dashboard widget live;
`news.breaking` additionally fires the global toast (NotificationToast).

Per-user alerts follow the same push contract: ALL notification writes go
through `notification_service.create_notification`, which persists the
document AND publishes `notification.created` — toast slides in, the navbar
badge increments, and the panel prepends, with zero polling. The morning
pipeline also broadcasts `morningreport.generated` (ai channel) so report
surfaces refetch the moment the 8:30 job finishes.

---

# AI Activity Timeline

Never fake AI progress.

Always display actual AI workflow.

08:45

Wake AI

✓

08:45

Collect Global Markets

✓

08:46

Read News

✓

08:47

Scan NSE

✓

08:48

Analyze Portfolio

✓

08:49

Generate Recommendations

✓

08:50

Morning Report Ready

✓

This timeline updates live.

---

# AI Thinking Process

Instead of displaying

"Thinking..."

Display

Collecting Market Data

↓

Reading News

↓

Checking Portfolio

↓

Running Scanner

↓

Finding Opportunities

↓

Comparing Indicators

↓

Evaluating Risk

↓

Generating Recommendation

↓

Completed

Users should understand how AI reached its conclusion.

## Implementation (Sprint R7)

The live step timeline is emitted by `backend/services/ai_activity.py`
(`AIRun`) on the `ai` domain and delivered over the event bridge:

ai.run.started   { user_id, run_id, session_id, steps:[label…], total, started_at }

ai.step          { user_id, run_id, session_id, index, total, label,
                   status: running | done | warning }

ai.run.completed { user_id, run_id, session_id, status, duration_ms }

Producers (each step wraps the real work it names — never fake progress):

• AI Chat (`ai_chat`) — memory / history / model stages

• Morning Report (`generate_morning_report`) — Collecting Market Data →
  Reading News → Scanning NSE → Analyzing Sector Flows → Generating Report →
  Saving Report. Cache hits emit nothing.

• Portfolio Review / Trade Review endpoints — their real 1–3 stages.

• Scheduler morning job — a `user_id: null` run, broadcast on the `ai`
  channel to every connected dashboard.

Correlation: the client generates a `run_id` per request and sends it with the
API call; events matching that id drive the UI for that request only.

Frontend: `realtimeStore.js` keeps runs in an `aiRuns` map keyed by `run_id`
(concurrent surfaces never clobber each other; completed runs are pruned).
`AIStepTimeline` (compact, chat/panels) and `AIPipelineProgress` (page-level,
progress bar + stages) render the run; both fall back to a neutral loading
state until `ai.run.started` arrives. Reconciliation rules: when the REST call
settles, the consumer calls `resolveAIRun` (no step may stay "running" after
lost WS frames) then `clearAIRun`; an active run silent beyond ~45s degrades
to the fallback. Broadcast runs also mirror into the AI Activity feed.

---

# Morning Report Flow

08:30

AI Starts

↓

Global Markets

↓

Gift Nifty

↓

Economic Calendar

↓

Corporate Actions

↓

News

↓

Sector Analysis

↓

Scanner

↓

Top Picks

↓

Portfolio Review

↓

Generate Report

↓

Notify User

All steps visible.

---

# Frontend Data Flow

Never

fetch()

fetch()

fetch()

fetch()

Instead

Socket.IO

↓

Global Store

↓

Affected Component

↓

GSAP Animation

↓

Done

Only changed components rerender.

---

# Animations

Price Up

Green Flash

Scale 1.05

Return

Price Down

Red Flash

Scale 0.95

Return

Scanner Card

Slide Right

Fade

Glow

Settle

Notification

Slide Down

Fade

Dismiss

Portfolio

Smooth Counter

Chart Animation

PnL Glow

AI Timeline

Progress Animation

Step Completion

Loading Pulse

Everything should feel premium.

---

# Connection Management

Display connection status.

States

Live

Connecting

Reconnecting

Offline

Disconnected

Users should always know market connection status.

---

# Heartbeat

Every 30 seconds

Client

↓

Server

↓

Heartbeat

↓

Connection Validated

If heartbeat fails

Reconnect automatically.

---

# Error Recovery

If the active market data provider fails

↓

Source Manager falls back automatically
(Broker WebSocket → Licensed Feed → Yahoo Finance)

↓

If no provider is available, show last cached data with
"Market feed temporarily unavailable."

(Full failover design: MARKET_DATA_ARCHITECTURE.md)

If Socket disconnects

↓

Reconnect

If Redis fails

↓

Graceful Recovery

If Broker disconnects

↓

Reconnect

Notify User

No page crashes.

---

# Performance Rules

Never rerender entire dashboard.

Update only changed components.

Batch updates.

Use Redis.

Use WebSockets.

Virtualize long lists.

Lazy load charts.

Memoize expensive calculations.

One socket connection.

No unnecessary polling.

## Implementation (Sprint R9)

Event batching — `RealtimeProvider` queues inbound socket messages for a 40ms
window and hands the burst to `realtimeStore.applyMessages`, which coalesces
every price-bearing message (`prices`, `market.index.updated`,
`watchlist.quotes`) into ONE `priceTicks` write; other events apply in arrival
order. `pong` bypasses the batch (connection liveness is immediate).

Selective rendering — `_mergePrices` MERGES per symbol (the 15s price stream
no longer wipes the RSI/volume fields the 120s watchlist stream added),
preserves tick object identity on no-op updates, and skips the store write
when nothing moved. `selectTickForSymbol(symbol)` lets a memoized row
subscribe to its own symbol only — Watchlist rows re-render individually.

Virtualization — `hooks/useVirtualList.js` (dependency-free windowing with
measured row height); the Watchlist windows itself beyond 60 rows.

Lazy loading — every routed page is `React.lazy` (route-level code splitting);
`App.js` holds the outer Suspense, `Layout` a nested one so navigation swaps
only the content region, never the shell.

Memoization — memoized `WatchlistRow` / News `ArticleCard`; News filtering and
source counts are `useMemo`d; Dashboard tick-patch effects keep previous state
identity on no-op ticks.

Redis optimization — `cache_get_many` (MGET) / `cache_set_many` (pipeline) in
`services/cache.py`; the in-memory fallback is bounded (expired sweep + oldest
eviction at 1024 keys); `fetch_all_universe_quotes` warms every per-symbol
quote key in one MGET; `ConnectionManager` serializes each fan-out message
once (`send_text`) instead of `json.dumps` per socket.

---

# Developer Rules

Never poll every second.

Never update the whole page.

Never block UI.

Always publish events.

Always animate updates.

Always use Redis Pub/Sub.

Always use Socket.IO.

Always update only affected components.

---

# Sprint 9.5
Real-Time Infrastructure

Objective

Transform StockAssist AI from a static dashboard into a living trading platform.

Deliverables

Market Event Bus

Redis Pub/Sub

Socket.IO Gateway

Live Dashboard

Live Portfolio

Live Scanner

Live Watchlist

Live Trade Monitor

Live News

Live AI Timeline

Live Notifications

Auto Reconnect

Heartbeat

Connection Status

GSAP Price Animations

Performance Optimization

No Polling Architecture

---

# Definition of Done

The Real-Time System is complete when:

✓ Prices update automatically

✓ Charts animate without refresh

✓ Scanner updates live

✓ Portfolio updates instantly

✓ AI timeline shows actual work

✓ Notifications arrive immediately

✓ Trade monitor streams live

✓ Watchlist updates automatically

✓ Connection status is visible

✓ Auto reconnect works

✓ No unnecessary polling exists

✓ Users feel the platform is alive

---

# Long-Term Vision

The real-time system is the heartbeat of StockAssist AI.

Every market movement, broker update, AI decision, portfolio change, and user notification should flow through a unified event-driven architecture.

The platform should never feel static. It should feel like a professional financial operating system that is continuously observing, analyzing, and reacting to the market in real time.

This document is the source of truth for all real-time behavior in StockAssist AI.

---

# End of REALTIME_SYSTEM.md

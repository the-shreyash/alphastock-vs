# StockAssist AI
## Real-Time Migration Plan

Version: 1.0

Status: Audit (No code changes)

Authority: REALTIME_SYSTEM.md

Sprint: R1 — Real-Time Audit

---

# 1. Overview & Method

This document audits every live feature of StockAssist AI against the target
architecture defined in `REALTIME_SYSTEM.md`, and lays out the migration path.

REALTIME_SYSTEM.md is the definitive authority. Its mandate: an event-driven,
**push-only** platform — Redis Pub/Sub + Socket.IO, **one socket per user**,
channel subscriptions, market changes flowing `event → socket → only the affected
component → animation`, granular connection states, a 30s heartbeat, auto
reconnect, and **zero polling**.

**Important stack reality:** REALTIME_SYSTEM.md and SYSTEM_ARCHITECTURE.md describe
a Node/Express/**Socket.IO** backend. The actual backend is **Python FastAPI with
native (Starlette) WebSocket**. The real-time *foundation* exists, but key pieces
are unwired. Because the doc's transport (Socket.IO) does not match the
implementation (native WS), this plan documents **two target paths**:

- **Path A — Pragmatic evolution (primary):** evolve the existing FastAPI native-WS
  stack to satisfy the *intent* of REALTIME_SYSTEM.md.
- **Path B — Literal doc compliance (alternative):** adopt `python-socketio` +
  Redis manager to match the doc's Socket.IO wording verbatim.

Method: three parallel code audits (backend infra, frontend consumption,
per-feature data flow) cross-checked against direct reads of `server.py`,
`services/*`, and `frontend/src/*`. Every finding below cites `file:line`.

---

# 2. Executive Summary

The platform is **~40% push / 60% poll**. Market data, portfolio, trades, and
watchlist prices genuinely stream; **scanner, news, AI activity, and notification
lists are polled**. The deeper problem is architectural: the in-process **event
bus has no WebSocket subscriber**, **Redis is cache-only (no Pub/Sub)**, the
frontend opens **a separate socket per page**, there is **no global real-time
store**, **no GSAP price animation**, and **two whole classes of already-pushed
messages are silently dropped by the client** (`ai_alert`, all `broker_*`).

### Readiness Scorecard

| Feature | Transport today | Live w/o refresh | Status |
|---|---|---|---|
| Dashboard indices (Nifty/Sensex/BankNifty/VIX) | WS `market_update` 10s + poll 30s | Partial (full-overview replace) | ⚠️ |
| Commodities / Global markets | Fetch on mount + poll 30s | No | ❌ |
| Sector performance | Poll 30s | No | ❌ |
| Market breadth | Inside overview push/poll | Partial | ⚠️ |
| Scanner (breakout/volume/momentum) | Fetch only | No | ❌ |
| News (breaking) | Fetch + poll | No | ❌ |
| AI Activity Timeline | `activity_feed` pushed **but polled** 15s | Partial/redundant | ⚠️ |
| Portfolio (value/P&L/allocation) | WS `portfolio_update` + poll | Partial | ⚠️ |
| Trade Monitor (P&L/targets/SL) | WS `trade_update`+`trade_engine_event`, poll 15s | Yes | ✅ |
| Watchlist (price/change) | WS `prices` + poll 30s | Partial | ⚠️ |
| Notifications | `ai_alert` **dropped**; list polled 30s | No | ❌ |
| Broker stream (ticks/orders) | Pushed **but all dropped** by client | No | ❌ |
| Morning Report | Fetch on mount | No | ❌ |
| Connection status / heartbeat | `LIVE/OFFLINE` only; no heartbeat loop | Partial | ⚠️ |

---

# 2.5 Sprint R2 Status — Event Bus & Infrastructure (DONE)

Sprint R2 closed the backbone gaps below on **Path A** (native WebSocket). All
changes are additive; existing loops/handlers are untouched.

- **G1 — RESOLVED.** `services/realtime/event_bridge.py` registers a catch-all
  (`event_bus.subscribe("*")`) subscriber that maps each event to a socket
  channel and delivers a stable `event` envelope. Wired at startup in `server.py`.
- **G2 — RESOLVED (single- and multi-process).** `services/cache.py` gains
  `cache_publish()` + `start_pubsub_listener()`; the bridge fans events across
  processes via Redis channel `sa:events` with a per-process `ORIGIN_ID` guard.
  Graceful no-op when `REDIS_URL` is unset.
- **G5 — RESOLVED.** `ConnectionManager` tracks per-connection channel sets with
  `subscribe`/`unsubscribe`/`broadcast_to_channel`; WS verbs `subscribe`/
  `unsubscribe` added.
- **G6 — RESOLVED.** `useWebSocket.js` now handles `ai_alert` + the four
  `broker_*` types and exposes them as new state.
- **New emissions:** `notification.created` (via `services/notification_service.py`,
  routed through the `ai_monitoring_loop` alert path) and `market.index.updated`
  (per-index diff in `market_broadcast_loop`).

**New client contract — the `event` envelope:**
`{"type":"event","event":"<domain.action>","channel":"<name>","data":{...},"timestamp":"..."}`.
Channels: `market` (price/index/breadth/calendar), `sectors`, `scanner`, `news`,
`notifications`, `portfolio`, `trades`, `ai`, `broker`. Clients subscribe with
`{"type":"subscribe","channels":[...]}`. Legacy flat message types
(`market_update`, `prices`, `trade_update`, …) remain for backward compatibility
until R3 migrates consumers.

**Still open (deferred to R3+):** G3 (single socket provider), G4 (global store),
G7 (GSAP), G8 (connection states + heartbeat + backoff), G9 (retire polling).

---

# 2.6 Sprint R3 Status — Frontend Real-Time Client (DONE, full scope)

Sprint R3 consumed the R2 backbone from the client on **Path A**, added the
real-data backend emissions the last pollers needed, and landed the GSAP
animation layer — closing every remaining gap.

- **G3 — RESOLVED.** `context/RealtimeProvider.jsx` owns the ONE
  `WebSocket(/api/ws?user_id=)` for the app (mounted in `App.js` inside
  `AuthProvider`). `hooks/useWebSocket.js` is now a store-selector shim (no
  socket), so the three former per-page sockets collapse to one.
- **G4 — RESOLVED.** `store/realtimeStore.js` (Zustand) is the global store; the
  provider writes, components read via narrow selectors. Consumes the R2 `event`
  envelope (`applyEvent`) **and** the legacy flat types (`applyLegacy`).
- **G8 — RESOLVED.** Connection state machine (connecting→live→reconnecting→
  offline) surfaced by `components/layout/ConnectionStatus.jsx` in the Navbar;
  30s heartbeat (`ping`/`pong`, reconnect on missed pong); exponential backoff
  with jitter (1s→30s, reset on clean open).
- **G9 — RESOLVED.** Every poll with a push path is gated on `!connected`
  (fallback only): Dashboard core/activity, TradeMonitor active-trades,
  Watchlist, Navbar unread-count (live via `notification.created`),
  ActivityTimeline (streams `activity_feed`), Markets (indices/sectors/global/
  movers pushed), MarketEngineStatus (`market.engine.status`), PortfolioMonitor
  (event-triggered refetch on `portfolio_update`).
- **G7 — RESOLVED.** `hooks/usePriceFlash.js` (green/red flash + scale) on the
  Dashboard index cards and Watchlist row prices; `components/ui/AnimatedNumber.jsx`
  (count-up) on the Dashboard portfolio value + P&L.
- **New backend emissions (real data):** `news.received`, `scanner.breakout`,
  `scanner.volume`, `sector.updated`, `market.global.updated`,
  `market.movers.updated`, `breadth.updated` (heartbeat tasks), and
  `market.engine.status` (`market_broadcast_loop`). These are what let the last
  three pollers drop to fallback-only.

**All nine cross-cutting gaps (G1–G9) are now closed** across R2 (backbone) and
R3 (frontend client + emissions). Remaining work is per-feature polish, not
architectural.

---

# 2.7 Sprint R4 Status — Scanner Live Migration (DONE)

Sprint R4 closed §4.5 (Scanner) on **Path A**, converting the scanner from
fetch-only to a continuous push-driven surface.

- **Continuous worker:** heartbeat tasks are the worker. Existing breakout/
  volume scans now gate publishes through
  `services/market_engine/scanner_worker.py` (new) — a per-(kind, symbol)
  30-min novelty cooldown so every `scanner.*` hit event is a NEW opportunity.
  New `task_scan_momentum` (150s, `scanner.momentum`: ≥2% day-change, new or
  accelerating ≥0.3% vs the previous cycle) and `task_scanner_sweep` (180s,
  rotates 2 of the 8 presets per tick over the 30s-cached universe).
- **Event names (doc-aligned):** `scanner.volume` → **`scanner.volume_spike`**;
  final set: `scanner.breakout`, `scanner.volume_spike`, `scanner.momentum`
  (hits) + `scanner.updated` (refresh signal).
- **Loop guard:** `scanner_engine.scan()` gains `source`/`publish`; only
  worker-tagged `scanner.updated` (`source:"worker"`) triggers a frontend
  refetch — the REST scan's own event (`source:"api"`) is ignored, preventing
  a fetch→event→fetch loop (it also has two other producers: ranking_engine).
- **Frontend:** store scanner slice is event-aware (hit feed entries +
  `scannerRefreshedAt`); `components/market/ScannerLiveFeed.jsx` (new) renders
  the push-only hit feed beside `MarketScanner` on the Markets Scanner tab;
  `hooks/useCardEntrance.js` (new) plays the doc's card entrance (Slide Right →
  Fade → Glow → Settle, GSAP) on newly inserted cards only; `MarketScanner`
  auto-refetches silently (1.5s debounce) on `scannerRefreshedAt` with a 60s
  poll only while disconnected (R3 gating).
- **Tests:** `tests/test_scanner_worker.py` (11 hermetic tests: cooldown,
  momentum semantics, task contracts via bus spies, publish gating);
  bridge-mapping test extended for the new event names.

---

# 3. Cross-Cutting Infrastructure Gaps

These affect every feature and should be fixed before per-feature polish.

### G1 — Event bus is not wired to the socket (Critical)
`services/market_engine/event_bus.py` is a working async pub/sub with a bounded
500-event log. Publishers emit `price.updated` (gateway.py:90), `sector.updated`
(gateway.py:174), `scanner.updated` (scanner_engine.py:250, ranking_engine.py:375),
`sector.analyzed` (sector_engine.py:170), `calendar.event`
(economic_calendar.py:254), `market.gateway.ready` (gateway.py:49). **No handler
subscribes to forward these to WebSocket clients** — the only reader is the polled
REST endpoint `/api/market/events` (server.py:975). The doc's core flow
("Market Engine Detects Change → Publish Event → Socket.IO → only affected
component") is therefore **not realized**.

### G2 — Redis is cache-only, no Pub/Sub (High)
`services/cache.py` uses GET/SET/DEL with in-memory fallback. There is **no
PUBLISH/SUBSCRIBE** in the codebase. All background loops run in a single Uvicorn
process; a restart drops every stream, and horizontal scale-out would duplicate
work with no cross-instance fan-out.

### G3 — Multiple sockets per user (High)
`hooks/useWebSocket.js` opens a fresh native WebSocket every time it mounts, and
it mounts independently in Dashboard.jsx:708, TradeMonitor.jsx:68, and
Watchlist.jsx:157. Violates "Only one Socket.IO connection per user."

### G4 — No global real-time store (High)
No Zustand/Redux/React Query. Each page holds its own `useState`; a price tick in
Dashboard never reaches TradeMonitor or Watchlist. The doc's
`Socket → Global Store → Affected Component` pipeline is absent.

### G5 — No channel/topic subscription model (Medium)
Server broadcasts either to *all* clients or to a single user; the only client
subscribe verb is `subscribe_prices` (server.py:2392). Pages cannot subscribe to
just the channels they need (market/portfolio/scanner/news/…).

### G6 — Dropped message types (Critical, quick win)
- `ai_monitoring_loop` emits `type:"ai_alert"` (server.py:2479, 2499) but the hook
  only handles `"alert"` (useWebSocket.js:49) → **market alerts never reach the UI**.
- Broker engine pushes `broker_status`, `portfolio_synced`, `broker_order_update`,
  `broker_price_tick` (broker_engine.py:176/327/419/439) → **none are handled by
  the hook** → live broker data is discarded client-side.

### G7 — No GSAP price animation (Medium)
`gsap` is installed but used only for Landing scroll (`pages/Landing.jsx`). No
green/red flash, no scale pulse, no number count-up on any price card. The doc's
animation spec is entirely unimplemented.

### G8 — Partial connection states, no heartbeat loop (Medium)
Client exposes a single `connected` boolean; UI shows only `LIVE/OFFLINE`
(Dashboard header). No `Connecting`/`Reconnecting` states. Reconnect is a fixed
5s timeout with **no exponential backoff** (useWebSocket.js:60). `sendPing` exists
but **nothing calls it on an interval** — the doc's 30s heartbeat is missing.

### G9 — Polling is primary, not fallback (High)
Ten timers poll on a schedule regardless of socket health (see §4 / §6). The doc's
first principle — "Never Poll. Always Push." — is inverted in practice.

---

# 4. Feature-by-Feature Audit

Each feature lists **Current → Target → Gaps → Required changes → Effort**.
Effort is T-shirt sized (S ≈ ≤1d, M ≈ 2–3d, L ≈ 4–6d, XL ≈ >1wk) for Path A.

### 4.1 Dashboard Indices (Nifty / Sensex / Bank Nifty / India VIX)
- **Current:** `market_broadcast_loop` broadcasts the **entire** overview as
  `market_update` every 10s to all clients (server.py:2412-2430); Dashboard also
  polls `/market/overview` every 30s (Dashboard.jsx:877) and sets `marketData`
  wholesale. Whole-widget state replace on every tick.
- **Target:** Per-index event (`market.index.updated`) → only the changed index
  card updates → green/red flash + mini-chart nudge; no refresh.
- **Gaps:** full-overview replace (not granular); dual push+poll; no animation
  (G1, G4, G7, G9).
- **Required changes:** publish per-index deltas through the event bus; subscribe
  bus→socket on a `market` channel; store indices in the global store keyed by
  symbol; drop the 30s poll; add GSAP flash on value change.
- **Effort:** M

### 4.2 Commodities & Global Markets
- **Current:** fetched on mount and re-fetched by the Dashboard 30s core poll /
  Markets 30s poll (Markets.jsx:200, which uses **no WS at all**).
- **Target:** streamed on the `market` channel; cards update independently.
- **Gaps:** pure poll; Markets page has zero socket usage (G3, G9).
- **Required changes:** include commodities/global in the price-stream payload on
  the `market` channel; subscribe Markets page to the shared socket; delete polls.
- **Effort:** M

### 4.3 Sector Performance
- **Current:** `/market/sectors` polled 30s. `sector_engine` already publishes
  `sector.analyzed` / `sector.updated` to the (unwired) event bus.
- **Target:** `sector.updated` event → sector heatmap cell updates live.
- **Gaps:** event bus not forwarded (G1); poll-driven.
- **Required changes:** bus→socket bridge on a `sectors`/`market` channel; store
  sectors globally; remove poll.
- **Effort:** S (once G1 bridge exists)

### 4.4 Market Breadth
- **Current:** derived server-side inside overview (advance/decline), delivered via
  the same 10s push / 30s poll as indices.
- **Target:** `breadth.updated` event → breadth bar animates.
- **Gaps:** rides the coarse overview replace; no dedicated event (G1).
- **Required changes:** publish `breadth.updated`; bind breadth bar to it.
- **Effort:** S

### 4.5 Scanner (Breakout / Volume / Momentum / Technical) — RESOLVED (Sprint R4, see §2.7)
- **Current:** **fetch-only** via `/api/market/scanner`,
  `/api/market/ranking` (server.py:890-947). `scanner_engine`/`ranking_engine`
  publish `scanner.updated` to the event bus, but nothing streams it. No
  `scanner.breakout`-style push exists.
- **Target:** scanner worker finds a hit → `scanner.breakout` event → card slides
  in with GSAP → AI analysis starts → notification.
- **Gaps:** no push path; no scanner channel; no card animation (G1, G5, G7).
- **Required changes:** run the scanner continuously (heartbeat task already scans
  breakouts/volume — surface its results as events); bridge `scanner.*` → socket
  `scanner` channel; build an incremental scanner feed component with slide-in
  animation; subscribe consumers.
- **Effort:** L

### 4.6 News (Breaking)
- **Current:** `/api/news`, `/api/news/sentiment` fetched on mount and by the 30s
  poll. `news_service` does dedupe + sentiment but publishes no event.
- **Target:** news worker → `news.breaking` event → news card streams in +
  notification.
- **Gaps:** no `news.received`/`news.breaking` push; no news channel (G1, G5, G9).
- **Required changes:** emit `news.received` when the heartbeat news scan finds new
  items; bridge to socket `news` channel; append-on-push in the news widget;
  remove poll.
- **Effort:** M

### 4.7 AI Activity Timeline
- **Current:** `activity_logger` already broadcasts `activity_feed` over the socket
  (server.py:2369-2377) **and** `components/ai/ActivityTimeline.jsx` **polls**
  `/ai/activity` every 15s (:35). Redundant: the same data is both pushed and
  polled; Dashboard consumes the push (`activityUpdates`) while the timeline
  component ignores it.
- **Target:** single live feed driven purely by `ai.*` / `activity_feed` events.
- **Gaps:** redundant poll; component not wired to the socket (G4, G9).
- **Required changes:** feed `ActivityTimeline` from the global store's
  `activity_feed` stream; delete its 15s poll.
- **Effort:** S

### 4.8 Portfolio (Value / P&L / Allocation)
- **Current:** `portfolio_update` pushed by heartbeat + on broker sync
  (heartbeat_engine.py:351, broker_engine `portfolio_synced`); Dashboard/Portfolio
  also fetch/poll (`/portfolio/summary`, PortfolioMonitor 60s). Allocation/risk are
  fetch-on-mount only.
- **Target:** broker tick → portfolio recompute → `portfolio.updated` → value
  counter animates, allocation chart re-renders; no refresh.
- **Gaps:** `portfolio_synced`/`broker_*` dropped by client (G6); no number
  animation (G7); Portfolio page has no socket; poll remains (G3, G9).
- **Required changes:** handle `portfolio_synced` + `broker_price_tick` in the
  hook; recompute portfolio on tick server-side and emit `portfolio.updated`;
  subscribe Portfolio page to the shared socket; count-up animation on value/P&L;
  drop the 60s health poll to event-driven.
- **Effort:** L

### 4.9 Trade Monitor (P&L / Targets / Stop-Loss) — best-in-class today
- **Current:** `trade_update` (live price/P&L) and `trade_engine_event` (SL
  trailed, target hit, exit) pushed from the trading engine + trade_monitor cron
  (trading_engine.py:410, scheduler.py:158); TradeMonitor consumes both
  (TradeMonitor.jsx:68) but still polls active trades every 15s (:133) and tips
  every 5m (:167).
- **Target:** fully event-driven lifecycle; poll only as a disconnected fallback.
- **Gaps:** always-on 15s poll even when connected; no P&L flash (G7, G9).
- **Required changes:** gate the poll on `!connected`; number/flash animation on
  P&L; move tips to an `ai.*` event.
- **Effort:** S

### 4.10 Watchlist (Price / Volume / Change)
- **Current:** `prices` batch pushed every 15s by heartbeat; Watchlist patches
  rows from `priceTicks` (Watchlist.jsx:157,179) **and** re-fetches `/watchlist`
  every 30s (:171) for RSI / since-added.
- **Target:** per-row live price + signal update; no full-list poll.
- **Gaps:** separate socket per page (G3); 30s poll; no per-row flash (G7).
- **Required changes:** shared socket + store; stream RSI/signal so the 30s poll
  can be removed (or gate on `!connected`); row flash animation.
- **Effort:** M

### 4.11 Notifications
- **Current:** market alerts written to `db.notifications` and broadcast as
  `ai_alert` (server.py:2466-2484) — **but the client only handles `alert`, so the
  toast never fires (G6)**. Navbar polls `/notifications/unread-count` every 30s
  (Navbar.jsx:17); the list is fetched, never pushed incrementally.
- **Target:** `notification.created` → toast slides in + badge increments live.
- **Gaps:** dropped `ai_alert`; polled badge; no incremental list push (G5, G6, G9).
- **Required changes:** rename to a shared `notification`/`alert` contract and
  handle it; push `notification.created` with unread delta; badge from the store;
  remove the 30s count poll.
- **Effort:** M

### 4.12 Broker Stream (Kite ticker / Upstox)
- **Current:** `brokers/stream.py` parses Kite binary ticks + Upstox order feed and
  the broker engine pushes `broker_status`, `portfolio_synced`,
  `broker_order_update`, `broker_price_tick` per user (broker_engine.py). **The
  frontend hook handles none of these** → all broker realtime is dropped (G6).
- **Target:** broker ticks drive portfolio/trade/order UI live.
- **Gaps:** four unhandled message types; no consumer components (G4, G6).
- **Required changes:** handle the four `broker_*` types in the hook/store; route
  `broker_order_update` into the Orders/Trade UI, `broker_price_tick` into the
  price store, `broker_status` into a broker badge.
- **Effort:** M

### 4.13 Morning Report
- **Current:** generated by 8:30 cron + heartbeat task; page fetches
  `/api/analysis/morning-report` on mount only.
- **Target:** `morningreport.generated` event → card appears + notification when
  ready.
- **Gaps:** no ready-event push (G1, G9).
- **Required changes:** emit `morningreport.generated`; push a notification; update
  the card via the store.
- **Effort:** S

### 4.14 Connection Status & Heartbeat (cross-page)
- **Current:** `connected` boolean; `LIVE/OFFLINE` badge on Dashboard only; 5s
  fixed reconnect; no heartbeat loop.
- **Target:** `Live / Connecting / Reconnecting / Offline`; 30s heartbeat;
  exponential backoff.
- **Gaps:** G8.
- **Required changes:** add a state machine to the shared socket provider; drive a
  global status pill; start a 30s ping; backoff on reconnect.
- **Effort:** M

---

# 5. Two Target Paths

### Path A — Pragmatic Evolution (Recommended primary)
Keep FastAPI native WebSocket. Deliver the doc's *intent* by:
1. **One socket per user:** lift `useWebSocket` into an app-level provider/context;
   pages consume shared state (fixes G3).
2. **Channels:** add `subscribe`/`unsubscribe` verbs + per-connection channel sets
   server-side; broadcast by channel (fixes G5).
3. **Event bus → socket bridge:** register a subscriber on the existing
   `event_bus` that maps `domain.action` events onto socket channels — the bus
   becomes the real backbone (fixes G1).
4. **Global store:** introduce Zustand slices (market, prices, portfolio, trades,
   watchlist, activity, notifications); the socket writes, components read (fixes
   G4).
5. **Kill polling:** remove/gate all 10 timers as each surface goes push (fixes G9).
6. **Animations + connection UX:** GSAP flash/count-up on tick; Live/Connecting/
   Reconnecting/Offline + 30s heartbeat + backoff (fixes G7, G8).
7. **Redis Pub/Sub (scale only):** add a thin `publish/subscribe` in `cache.py`
   used to fan events across processes; single-process still works without it
   (fixes G2 for horizontal scale).
8. **Message-contract fixes:** handle `ai_alert` and the four `broker_*` types
   (fixes G6 — do this first, it is nearly free).

### Path B — Literal Doc Compliance (Alternative)
Replace native WS with **`python-socketio`** (ASGI) mounted on FastAPI, using
rooms/namespaces per the doc, with the **Redis manager** for multi-process
fan-out; rewrite the frontend on **`socket.io-client`**. Achieves verbatim
"Socket.IO + rooms" fidelity but discards the working native-WS layer and touches
every WS producer/consumer.

### Comparison

| Dimension | Path A (pragmatic) | Path B (Socket.IO) |
|---|---|---|
| Effort | Medium | High |
| Risk | Low (incremental) | High (transport swap) |
| Reuse of working code | High | Low |
| Doc fidelity (wording) | Intent-level | Verbatim |
| Scalability endpoint | Redis Pub/Sub added surgically | Redis manager native |
| Reconnect/rooms built-in | Hand-rolled | Library-provided |

Recommendation: **Path A**, adopting Path B only if a hard Socket.IO/rooms
requirement emerges. The two share ~80% of the work (store, channels, animations,
killing polling, event-bus bridge); only the transport layer differs.

---

# 6. Consolidated Gap Register

| ID | Gap | Severity | Doc ref | Key files | Path-A fix | Path-B fix |
|---|---|---|---|---|---|---|
| G1 | Event bus not forwarded to socket | Critical | Event Driven Arch. | event_bus.py; gateway.py:90/174; server.py:975 | bus→socket subscriber | bus→emit to rooms |
| G2 | No Redis Pub/Sub (cache only) | High | Redis Pub/Sub | services/cache.py | add publish/subscribe fan-out | socketio Redis manager |
| G3 | Multiple sockets per user | High | Socket.IO | useWebSocket.js; Dashboard/TradeMonitor/Watchlist | app-level provider | single socketio client |
| G4 | No global real-time store | High | Frontend Data Flow | all pages | Zustand slices | Zustand slices |
| G5 | No channel subscription | Medium | Socket Events | server.py:2381-2408 | subscribe verbs + channel broadcast | rooms/namespaces |
| G6 | Dropped `ai_alert` + `broker_*` | Critical | Notifications/Broker | server.py:2479/2499; broker_engine.py:176/327/419/439; useWebSocket.js:49 | handle message types | handle events |
| G7 | No GSAP price animation | Medium | Animations | StatCard/Watchlist rows | GSAP flash/count-up | same |
| G8 | Partial states, no heartbeat loop | Medium | Connection Mgmt/Heartbeat | useWebSocket.js:60,86 | state machine + 30s ping + backoff | socketio ping + states |
| G9 | Polling primary, not fallback | High | Core Principle | 10 timers (see §4) | delete/gate timers | delete/gate timers |

---

# 7. Effort Summary & Suggested Sequencing

Phased so each phase is shippable and de-risks the next. Ranges are Path-A rough
order-of-magnitude, not commitments.

- **Phase 1 — Contract fixes & one socket (S–M, ~2–4d):** G6 (handle `ai_alert`
  + `broker_*`), G3 (app-level socket provider). Immediate visible wins
  (alerts/broker data start showing) with minimal risk.
- **Phase 2 — Event bus → socket + channels (M–L, ~4–6d):** G1, G5. Bridge the
  bus to channel broadcasts; begin retiring the coarse `market_update` in favor of
  granular events. Kill the first polls (indices, sectors, activity).
- **Phase 3 — Global store & de-poll (M–L, ~4–6d):** G4, G9. Zustand slices;
  migrate every page off timers to store reads. Removes the remaining 10 timers.
- **Phase 4 — Animation & connection UX (M, ~3d):** G7, G8. GSAP flash/count-up;
  Live/Connecting/Reconnecting/Offline + heartbeat + backoff.
- **Phase 5 — Redis Pub/Sub fan-out (M, ~3d):** G2. Enables multi-process scale;
  optional until horizontal scaling is needed.
- **Phase 6 (optional) — Socket.IO transport (Path B) (L–XL):** only if verbatim
  doc compliance is mandated.

Per-feature roll-up (Path A): S×6, M×6, L×3 → the bulk of effort concentrates in
Scanner (4.5), Portfolio (4.8), and the store/de-poll phase.

---

# 8. Appendix

### 8.1 WebSocket message-type map (current)

| Type | Producer (file:line) | Interval/trigger | Client-handled? |
|---|---|---|---|
| `market_update` | server.py:2424; scheduler.py (scanner job) | 10s / 5m | Yes |
| `prices` | heartbeat_engine price stream | 15s | Yes |
| `price_tick` | server.py:2400 | on `subscribe_prices` | Yes |
| `pong` | server.py:2403 | on client `ping` | (implicit) |
| `trade_update` | scheduler.py:158; heartbeat_engine.py:267 | 60s / task | Yes |
| `trade_engine_event` | trading_engine.py:410 | on SL/target/exit | Yes |
| `portfolio_update` | heartbeat_engine.py:351 | task | Yes |
| `activity_feed` | server.py:2369 | per task | Yes |
| `ai_alert` | server.py:2479, 2499 | 30s on move | **No (dropped)** |
| `broker_status` | broker_engine.py:176/208/445 | on state change | **No (dropped)** |
| `portfolio_synced` | broker_engine.py:327 | on sync | **No (dropped)** |
| `broker_order_update` | broker_engine.py:419 | on order change | **No (dropped)** |
| `broker_price_tick` | broker_engine.py:439 | on broker tick | **No (dropped)** |

Client handler: `frontend/src/hooks/useWebSocket.js:31-55`.

### 8.2 Polling inventory (10 timers to retire)

| # | File:line | Interval | Endpoint | Notes |
|---|---|---|---|---|
| 1 | Dashboard.jsx:877 | 30s | overview/sectors/ai-activity | core poll |
| 2 | Dashboard.jsx:775 | 10s | /ai-activity | fallback when disconnected |
| 3 | TradeMonitor.jsx:133 | 15s | active trades | always on |
| 4 | TradeMonitor.jsx:167 | 5m | coaching tips | always on |
| 5 | Watchlist.jsx:171 | 30s | /watchlist | RSI/since-added |
| 6 | Markets.jsx:200 | 30s | 6 market endpoints | **no WS** |
| 7 | Navbar.jsx:17 | 30s | /notifications/unread-count | badge |
| 8 | PortfolioMonitor.jsx:19 | 60s | /monitor/health | portfolio health |
| 9 | ActivityTimeline.jsx:35 | 15s | /ai/activity | **already pushed** |
| 10 | MarketEngineStatus.jsx:16 | 30s | /market/engine/status | engine badge |

### 8.3 Key files

Backend: `server.py` (WS endpoint 2381-2408, ConnectionManager 2326-2361,
market loop 2412, AI loop 2433); `services/scheduler.py` (6 cron jobs);
`services/heartbeat_engine.py` (task tick 12s, price stream 15s);
`services/cache.py` (Redis cache, no pub/sub);
`services/market_engine/event_bus.py` (unwired bus);
`services/broker_engine.py`, `services/brokers/stream.py`;
`services/trading_engine.py`.

Frontend: `hooks/useWebSocket.js`; pages `Dashboard.jsx`, `TradeMonitor.jsx`,
`Watchlist.jsx`, `Markets.jsx`, `Portfolio.jsx`, `News.jsx`;
`components/layout/Navbar.jsx`, `components/dashboard/PortfolioMonitor.jsx`,
`components/ai/ActivityTimeline.jsx`, `components/market/MarketEngineStatus.jsx`.

---

# End of Real-Time Migration Plan

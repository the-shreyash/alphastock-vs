/**
 * Global real-time store (Sprint R3).
 *
 * A single Zustand store fed by one WebSocket (owned by RealtimeProvider). The
 * doc's data flow — `Socket → Global Store → Affected Component` — is realized
 * here: the provider writes, components read via selectors, so a price tick
 * re-renders only the components subscribed to that symbol/slice (never the
 * whole page).
 *
 * Two ingestion paths coexist during the R2→R3 migration:
 *   - `applyEvent(envelope)`  — the R2 `{type:"event", event, channel, data}`
 *     envelope carried by the event-bus → socket bridge.
 *   - `applyLegacy(msg)`      — the pre-R2 flat message types
 *     (`market_update`, `prices`, `trade_update`, `ai_alert`, `broker_*`, …),
 *     kept working until every producer is migrated to the envelope.
 *
 * Selectors live at the bottom so consumers subscribe to the narrowest slice.
 */
import { create } from "zustand";

const MAX_TRADE_UPDATES = 50;
const MAX_ENGINE_EVENTS = 20;
const MAX_ALERTS = 50;
const MAX_BROKER_ORDERS = 50;
const MAX_AI_RUNS = 6;
const MAX_TRADE_REVIEWS = 25;
// Hard ceiling for `aiRunOrder` (PH3.6). MAX_AI_RUNS is a *soft* cap: its
// eviction loop refuses to evict an active run and `break`s when every run is
// active, which is correct while runs finish. They do not always finish — a run
// is marked inactive by `ai.run.completed` or by `resolveAIRun`, and a socket
// that drops mid-run delivers neither, so the run stays `active: true` forever
// and permanently occupies a slot. Enough dropped runs in one long session and
// the soft cap stops capping anything at all. This ceiling is what the map is
// actually bounded by; reaching it means runs are being abandoned, so the
// oldest is stale regardless of what its `active` flag still claims.
const MAX_AI_RUNS_HARD = 50;

const initialState = {
  // Connection state machine: offline | connecting | live | reconnecting
  connection: { status: "offline", lastPongAt: null },
  // Imperative sender set by the provider on open (null while disconnected).
  send: null,

  // Market
  marketData: null, // coarse overview (legacy `market_update`)
  priceTicks: {}, // { [SYMBOL]: { price, change_pct, ... } }
  sectors: [],
  breadth: null,
  globalMarkets: [], // market.global.updated
  movers: null, // { gainers:[], losers:[] } from market.movers.updated
  engineStatus: null, // market.engine.status

  // Portfolio / trades
  portfolioUpdate: null,
  // Full live snapshot from `portfolio.updated` (Sprint R5):
  // { pnl, allocation, holdings, open_positions, reason, updatedAt }
  portfolioLive: null,
  tradeUpdates: [],
  engineEvents: [],
  // Live open-trade rows from `trade.updated` (Sprint R6), keyed by trade_id.
  // Rows keep their previous object identity when unchanged so memoized
  // per-trade components skip re-render.
  tradeLive: { byId: {}, reason: null, updatedAt: null },
  // AI reviews streamed by `trade.review.ready`, keyed by trade_id.
  tradeReviews: {},

  // AI
  activityUpdates: null,
  // Live "thinking" timelines (Sprint R7), keyed by runId so concurrent runs
  // (chat + morning report + review panels) never clobber each other. Shape:
  //   { [runId]: { runId, userId, sessionId, steps:[{ label, status }],
  //                active, status, startedAt, updatedAt } }
  // userId === null marks a broadcast run (e.g. the 8:30 scheduler pipeline).
  aiRuns: {},
  aiRunOrder: [], // insertion order, used to prune old completed runs

  // Notifications / alerts
  alerts: [], // legacy `alert`
  marketAlerts: [], // `ai_alert` + `notification.created` market alerts
  unreadCount: 0,
  latestNotification: null,

  // Broker (token-keyed ticks kept separate from the symbol-keyed price store)
  brokerStatus: null,
  portfolioSynced: null,
  brokerOrders: [],
  brokerTicks: null,

  // Scanner (Sprint R4): live hit feed + worker-driven refresh signal.
  scanner: [], // [{ id, kind, event, candidates, count, timestamp }, ...] newest first
  scannerRefreshedAt: null, // bumped only by worker-origin scanner events
  // News (Sprint R8): latest streamed headlines + the last breaking batch.
  news: [],
  breakingNews: null, // { articles, timestamp } — toast host consumes this
  // Watchlist (Sprint R8): per-user add/remove signal for cross-surface sync.
  watchlistEvent: null, // { action: "added"|"removed", symbol, updatedAt }
  // Morning report ready-signal (Sprint R8) — pages refetch when this bumps.
  morningReportReadyAt: null,
};

// Index symbol aliases used when folding index events into the price store.
const INDEX_SYMBOL = { nifty: "NIFTY", bank_nifty: "BANKNIFTY", sensex: "SENSEX" };

// Granular trade lifecycle events (Sprint R6) routed into `engineEvents`.
const TRADE_LIFECYCLE_EVENTS = new Set([
  "trade.trailing_stop", "trade.target_hit", "trade.sl_hit", "trade.closed",
]);

// Cheap value-equality for small live trade rows (~12 primitive fields plus
// targets_hit levels) — keeps object identity stable across no-op snapshots.
const sameRow = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// True when every field the incoming tick carries already matches the stored
// tick — the merge can then keep the previous object identity so memoized
// per-symbol subscribers skip re-render (Sprint R9 selective rendering).
const tickUnchanged = (prev, tick) => {
  if (!prev) return false;
  for (const k in tick) {
    if (tick[k] !== prev[k]) return false;
  }
  return true;
};

/**
 * Extract the price map a message carries, or null when it isn't a price
 * message. Used by the batched ingest path (Sprint R9) to coalesce every
 * price-bearing message in a burst — the 15s `prices` broadcast, per-index
 * `market.index.updated` events, `watchlist.quotes` — into ONE store write.
 */
const priceMapFromMessage = (msg) => {
  if (!msg) return null;
  if (msg.type === "prices") return msg.data || null;
  if (msg.type === "price_tick") {
    return msg.data?.symbol ? { [msg.data.symbol]: msg.data } : null;
  }
  if (msg.type !== "event") return null;
  const data = msg.data || {};
  if (msg.event === "market.index.updated") {
    const sym = data.symbol || INDEX_SYMBOL[data.key] || data.key;
    const mapped = INDEX_SYMBOL[sym] || sym;
    if (!mapped || data.value == null) return {};
    return { [String(mapped).toUpperCase()]: { price: data.value, change_pct: data.change_pct } };
  }
  if (msg.event === "watchlist.quotes") return data.quotes || null;
  return null;
};

export const useRealtimeStore = create((set, get) => ({
  ...initialState,

  setConnection: (status, extra = {}) =>
    set((s) => ({ connection: { ...s.connection, status, ...extra } })),

  setSend: (send) => set({ send }),

  seedUnreadCount: (count) => set({ unreadCount: Number(count) || 0 }),

  markNotificationsRead: () => set({ unreadCount: 0 }),

  /** Single notification marked read in the panel — keep the badge in sync. */
  decrementUnread: () =>
    set((s) => ({ unreadCount: Math.max(0, s.unreadCount - 1) })),

  /**
   * Merge a batch of live prices: { SYMBOL: { price, change_pct }, ... }.
   *
   * Sprint R9 semantics:
   *   - MERGE per symbol (never replace): the 15s price stream carries only
   *     { price, change_pct } and must not wipe the RSI / volume_ratio fields
   *     the 120s watchlist.quotes stream added to the same symbol.
   *   - Preserve object identity when a tick carries no new values, and skip
   *     the store write entirely when nothing changed, so subscribers only
   *     re-render on real movement.
   */
  _mergePrices: (map) =>
    set((s) => {
      if (!map) return {};
      let changed = false;
      const next = { ...s.priceTicks };
      for (const [sym, tick] of Object.entries(map)) {
        if (!tick) continue;
        const prev = next[sym];
        if (tickUnchanged(prev, tick)) continue;
        next[sym] = prev ? { ...prev, ...tick } : tick;
        changed = true;
      }
      return changed ? { priceTicks: next } : {};
    }),

  /**
   * Batched ingest (Sprint R9 event batching). The provider queues inbound
   * socket messages for a ~40ms window and hands the burst here: price-bearing
   * messages are coalesced into one `_mergePrices` write, everything else is
   * routed in arrival order. One heartbeat burst therefore produces one
   * priceTicks update instead of one per message.
   */
  applyMessages: (msgs) => {
    if (!Array.isArray(msgs) || msgs.length === 0) return;
    const prices = {};
    let havePrices = false;
    for (const msg of msgs) {
      const map = priceMapFromMessage(msg);
      if (map) {
        for (const [sym, tick] of Object.entries(map)) {
          // Later ticks in the burst win field-by-field.
          prices[sym] = prices[sym] ? { ...prices[sym], ...tick } : tick;
          havePrices = true;
        }
        continue;
      }
      if (msg?.type === "event") get().applyEvent(msg);
      else get().applyLegacy(msg);
    }
    if (havePrices) get()._mergePrices(prices);
  },

  /**
   * Handle a legacy flat message. This is the pre-R2 `useWebSocket` switch,
   * moved verbatim into the store so a single socket feeds every page.
   */
  applyLegacy: (msg) => {
    if (!msg || !msg.type) return;
    const { type, data } = msg;
    switch (type) {
      case "market_update":
        set({ marketData: data });
        break;
      case "price_tick":
        if (data?.symbol) get()._mergePrices({ [data.symbol]: data });
        break;
      case "prices":
        // Batched live prices keyed by symbol.
        if (data) get()._mergePrices(data);
        break;
      case "trade_update":
        set((s) => ({ tradeUpdates: [data, ...s.tradeUpdates].slice(0, MAX_TRADE_UPDATES) }));
        break;
      case "trade_engine_event":
        set((s) => ({ engineEvents: [data, ...s.engineEvents].slice(0, MAX_ENGINE_EVENTS) }));
        break;
      case "portfolio_update":
        set({ portfolioUpdate: data });
        break;
      case "alert":
        set((s) => ({ alerts: [data, ...s.alerts].slice(0, MAX_ALERTS) }));
        break;
      case "activity_feed":
        set({ activityUpdates: data });
        break;
      case "ai_alert":
        // Proactive market alert (Nifty big move / key-level cross).
        set((s) => ({
          marketAlerts: [
            { message: msg.message, severity: msg.severity, ...(data || {}), timestamp: msg.timestamp },
            ...s.marketAlerts,
          ].slice(0, MAX_ALERTS),
        }));
        break;
      case "broker_status":
        set({ brokerStatus: data });
        break;
      case "portfolio_synced":
        set({ portfolioSynced: data });
        break;
      case "broker_order_update":
        set((s) => ({ brokerOrders: [data, ...s.brokerOrders].slice(0, MAX_BROKER_ORDERS) }));
        break;
      case "broker_price_tick":
        // Raw broker feed: { broker, ticks: [...] } keyed by instrument token.
        set({ brokerTicks: data });
        break;
      default:
        break;
    }
  },

  /**
   * Handle the R2 `event` envelope: { type:"event", event, channel, data }.
   * Routes by the event's domain.action into the matching slice.
   */
  applyEvent: (envelope) => {
    if (!envelope) return;
    const event = envelope.event || "";
    const data = envelope.data || {};
    const domain = event.split(".", 1)[0];

    switch (domain) {
      case "price": // price.updated
      case "market": {
        if (event === "market.index.updated") {
          // Fold index deltas into the symbol-keyed price store.
          const sym = data.symbol || INDEX_SYMBOL[data.key] || data.key;
          const mapped = INDEX_SYMBOL[sym] || sym;
          if (mapped && data.value != null) {
            get()._mergePrices({
              [String(mapped).toUpperCase()]: { price: data.value, change_pct: data.change_pct },
            });
          }
        } else if (event === "market.global.updated") {
          set({ globalMarkets: Array.isArray(data.markets) ? data.markets : [] });
        } else if (event === "market.movers.updated") {
          set({ movers: { gainers: data.gainers || [], losers: data.losers || [] } });
        } else if (event === "market.engine.status") {
          set({ engineStatus: data });
        } else if (data.symbol && data.price != null) {
          get()._mergePrices({ [String(data.symbol).toUpperCase()]: data });
        }
        break;
      }
      case "breadth":
        set({ breadth: data });
        break;
      case "sector":
        set({ sectors: Array.isArray(data.sectors) ? data.sectors : data });
        break;
      case "scanner": {
        if (event === "scanner.updated") {
          // Results-refresh signal. Only worker-origin sweeps count: the REST
          // scan also emits scanner.updated, and refetching on our own fetch's
          // event would loop (fetch → event → fetch).
          if (data.source === "worker") {
            set({ scannerRefreshedAt: envelope.timestamp || Date.now() });
          }
        } else {
          // Hit events (scanner.breakout / volume_spike / momentum) — feed
          // entries are novelty-gated server-side, so each one is a NEW hit.
          set((s) => ({
            scanner: [
              {
                id: `${event}-${envelope.timestamp || Date.now()}`,
                kind: data.kind || event.split(".")[1],
                event,
                candidates: Array.isArray(data.candidates) ? data.candidates : [],
                count: data.count || 0,
                timestamp: envelope.timestamp,
              },
              ...s.scanner,
            ].slice(0, MAX_ALERTS),
            // A fresh hit is itself proof the market moved — refresh the table.
            scannerRefreshedAt: envelope.timestamp || Date.now(),
          }));
        }
        break;
      }
      case "news": {
        if (event === "news.breaking") {
          // Novelty-gated server-side, so every article here is genuinely new
          // — prepend to the live list and surface the batch for the toast.
          const incoming = Array.isArray(data.articles) ? data.articles : [];
          set((s) => {
            const seen = new Set(incoming.map((a) => (a.title || "").toLowerCase()));
            return {
              news: [...incoming, ...s.news.filter((a) => !seen.has((a.title || "").toLowerCase()))].slice(0, MAX_ALERTS),
              breakingNews: { articles: incoming, timestamp: envelope.timestamp || Date.now() },
            };
          });
        } else {
          // news.received carries { articles, count } — keep the latest list.
          set({ news: Array.isArray(data.articles) ? data.articles : (Array.isArray(data) ? data : []) });
        }
        break;
      }
      case "watchlist": {
        if (event === "watchlist.quotes") {
          // Broadcast enriched quotes (price + RSI + volume ratio) for every
          // watchlisted symbol — folded into the shared price store so any
          // row already subscribed to its symbol re-renders with the new tick.
          if (data.quotes) get()._mergePrices(data.quotes);
        } else if (event === "watchlist.updated") {
          // Per-user add/remove — lets every open surface for this user
          // (page, dashboard widget, second tab) sync without a poll.
          set({
            watchlistEvent: {
              action: data.action,
              symbol: data.symbol,
              updatedAt: envelope.timestamp || Date.now(),
            },
          });
        }
        break;
      }
      case "morningreport":
        // morningreport.generated — the 8:30 pipeline finished; consumers
        // refetch the report when this timestamp bumps.
        set({ morningReportReadyAt: envelope.timestamp || Date.now() });
        break;
      case "notification": {
        // notification.created — per-user push. Increment the unread badge and
        // surface the latest so a toast can fire live.
        set((s) => ({
          unreadCount: s.unreadCount + 1,
          latestNotification: { ...data, timestamp: envelope.timestamp },
          marketAlerts:
            data.severity === "critical" || data.severity === "warning"
              ? [{ message: data.message, severity: data.severity, ...data, timestamp: envelope.timestamp }, ...s.marketAlerts].slice(0, MAX_ALERTS)
              : s.marketAlerts,
        }));
        break;
      }
      case "portfolio": {
        if (event === "portfolio.synced") {
          // Broker sync completed — refetch trigger for portfolio surfaces.
          set({ portfolioSynced: { ...data, timestamp: envelope.timestamp } });
        } else {
          // portfolio.updated — live snapshot (P&L + allocation + marks).
          // portfolioUpdate keeps the legacy consumer contract (Dashboard).
          set({
            portfolioUpdate: data,
            portfolioLive: { ...data, updatedAt: envelope.timestamp || Date.now() },
          });
        }
        break;
      }
      case "trade": {
        if (event === "trade.updated") {
          // Per-user open-trades snapshot (Sprint R6). Merge rows by
          // trade_id, reusing the previous row object when unchanged so
          // memoized per-trade components skip re-render.
          set((s) => {
            // Built from the incoming rows ONLY, not merged onto the previous
            // map (PH3.6). Every producer of `trade.updated` publishes the
            // user's complete set of OPEN trades — `services/trade_stream.py`
            // states it and all three call sites query `status: "OPEN"` — so a
            // trade absent from this snapshot is a trade that closed. Merging
            // kept it forever: the map grew by one entry per trade the user ever
            // closed in the session, and the stale row it retained described a
            // closed position as though it were still open.
            const prevById = s.tradeLive.byId;
            const byId = {};
            (Array.isArray(data.trades) ? data.trades : []).forEach((row) => {
              if (!row || !row.trade_id) return;
              const prev = prevById[row.trade_id];
              // Object identity is preserved for unchanged rows so memoized
              // per-trade components still skip re-render.
              byId[row.trade_id] = prev && sameRow(prev, row) ? prev : row;
            });
            return {
              tradeLive: {
                byId,
                reason: data.reason || null,
                updatedAt: envelope.timestamp || Date.now(),
              },
            };
          });
        } else if (event === "trade.review.ready") {
          set((s) => {
            // Bounded (PH3.6). An AI trade review is a multi-KB text object and
            // this map was keyed by trade_id with nothing ever removing an
            // entry, so a trader who closes positions all day accumulates one
            // per close for as long as the tab stays open. Insertion order is
            // reliable here because the keys are ObjectId hex strings, never
            // integer-like, so V8 keeps them in insertion order.
            const tradeReviews = { ...s.tradeReviews, [data.trade_id]: data.review };
            const ids = Object.keys(tradeReviews);
            for (const id of ids.slice(0, Math.max(0, ids.length - MAX_TRADE_REVIEWS))) {
              delete tradeReviews[id];
            }
            return { tradeReviews };
          });
        } else if (TRADE_LIFECYCLE_EVENTS.has(event)) {
          // Trailing ratchet / target hit / SL hit / close — the Trade
          // Monitor patches rows in place from these (refetch only on close).
          set((s) => ({
            engineEvents: [{ event, ...data }, ...s.engineEvents].slice(0, MAX_ENGINE_EVENTS),
          }));
        } else {
          set((s) => ({ tradeUpdates: [data, ...s.tradeUpdates].slice(0, MAX_TRADE_UPDATES) }));
        }
        break;
      }
      case "ai": {
        // Live "thinking" timelines (Sprint R7) — replaces the static
        // "Thinking…" with the stages the AI is actually running. Runs are
        // keyed by runId so concurrent surfaces (chat, morning report, review
        // panels, scheduler broadcasts) each track their own run; only the
        // patched run's object identity changes, so unrelated subscribers
        // skip re-render.
        if (event === "ai.run.started") {
          set((s) => {
            const run = {
              runId: data.run_id,
              userId: data.user_id ?? null,
              sessionId: data.session_id,
              steps: (Array.isArray(data.steps) ? data.steps : []).map((label) => ({
                label,
                status: "pending",
              })),
              active: true,
              status: null,
              startedAt: data.started_at || envelope.timestamp || Date.now(),
              updatedAt: envelope.timestamp || Date.now(),
            };
            const aiRuns = { ...s.aiRuns, [run.runId]: run };
            let aiRunOrder = s.aiRunOrder.includes(run.runId)
              ? s.aiRunOrder
              : [...s.aiRunOrder, run.runId];
            // Prune oldest COMPLETED runs beyond the cap (never evict active).
            while (aiRunOrder.length > MAX_AI_RUNS) {
              const evictable = aiRunOrder.find((id) => aiRuns[id] && !aiRuns[id].active);
              if (!evictable) break;
              delete aiRuns[evictable];
              aiRunOrder = aiRunOrder.filter((id) => id !== evictable);
            }
            // Hard ceiling: evict the oldest even if it still claims to be
            // active. See MAX_AI_RUNS_HARD — an "active" run this old is an
            // abandoned one, and honouring the flag forever is what turned the
            // soft cap above into no cap at all.
            while (aiRunOrder.length > MAX_AI_RUNS_HARD) {
              const [oldest, ...rest] = aiRunOrder;
              delete aiRuns[oldest];
              aiRunOrder = rest;
            }
            return { aiRuns, aiRunOrder };
          });
        } else if (event === "ai.step") {
          set((s) => {
            const run = s.aiRuns[data.run_id];
            const patch = {};
            if (run) {
              const steps = run.steps.slice();
              if (data.index >= 0 && data.index < steps.length) {
                steps[data.index] = { ...steps[data.index], status: data.status };
              }
              patch.aiRuns = {
                ...s.aiRuns,
                [data.run_id]: { ...run, steps, updatedAt: envelope.timestamp || Date.now() },
              };
            }
            // Broadcast runs (no user_id — e.g. the 8:30 scheduler pipeline)
            // also feed the dashboard's background activity timeline, matching
            // the legacy activity_feed entry shape (UTC HH:MM:SS, like
            // services/activity_logger.py).
            if (data.user_id == null) {
              const ts = envelope.timestamp ? new Date(envelope.timestamp) : new Date();
              patch.activityUpdates = {
                action: data.label,
                category: "monitor",
                status: data.status,
                time: ts.toISOString().slice(11, 19),
              };
            }
            return patch;
          });
        } else if (event === "ai.run.completed") {
          set((s) => {
            const run = s.aiRuns[data.run_id];
            if (!run) return {};
            return {
              aiRuns: {
                ...s.aiRuns,
                [data.run_id]: {
                  ...run,
                  active: false,
                  status: data.status || "done",
                  updatedAt: envelope.timestamp || Date.now(),
                },
              },
            };
          });
        } else {
          // Any other ai.* event feeds the background activity timeline.
          set({ activityUpdates: data });
        }
        break;
      }
      case "broker": {
        if (event === "broker.order.updated") {
          // Live order status (Sprint R6) — upsert by order_id so a fill
          // updates the existing row instead of duplicating it.
          set((s) => {
            const order = { ...(data.order || {}), broker: data.broker };
            if (!order.order_id) return {};
            const idx = s.brokerOrders.findIndex((o) => o.order_id === order.order_id);
            const brokerOrders = idx >= 0
              ? s.brokerOrders.map((o, i) => (i === idx ? { ...o, ...order } : o))
              : [order, ...s.brokerOrders].slice(0, MAX_BROKER_ORDERS);
            return { brokerOrders };
          });
        } else {
          // Generic broker event fallthrough (status/connection changes).
          set({ brokerStatus: data });
        }
        break;
      }
      default:
        break;
    }
  },

  /**
   * Force-settle a live AI run (Sprint R7 reconciliation). Called when the
   * REST request that started the run resolves or fails: if WebSocket events
   * were lost mid-run (disconnect, missed frames), no step may stay stuck on
   * "running" and the run must stop animating.
   */
  resolveAIRun: (runId, status = "done") =>
    set((s) => {
      const run = runId ? s.aiRuns[runId] : null;
      if (!run || (!run.active && !run.steps.some((st) => st.status === "running"))) return {};
      return {
        aiRuns: {
          ...s.aiRuns,
          [runId]: {
            ...run,
            active: false,
            status: run.status || status,
            steps: run.steps.map((st) =>
              st.status === "running" ? { ...st, status } : st
            ),
            updatedAt: Date.now(),
          },
        },
      };
    }),

  /** Drop a finished AI run from the map once its consumer unmounts. */
  clearAIRun: (runId) =>
    set((s) => {
      if (!runId || !s.aiRuns[runId]) return {};
      const aiRuns = { ...s.aiRuns };
      delete aiRuns[runId];
      return { aiRuns, aiRunOrder: s.aiRunOrder.filter((id) => id !== runId) };
    }),

  reset: () => set({ ...initialState }),
}));

// ---- Selectors (subscribe to the narrowest slice) ----
export const selectConnected = (s) => s.connection.status === "live";
export const selectConnectionStatus = (s) => s.connection.status;
export const selectMarketData = (s) => s.marketData;
export const selectPriceTicks = (s) => s.priceTicks;
// Factory selector (Sprint R9): one symbol's tick only. Combined with
// `_mergePrices` identity preservation, a memoized row subscribing through
// this re-renders ONLY when its own symbol actually moves.
export const selectTickForSymbol = (symbol) => (s) => (symbol ? s.priceTicks[symbol] || null : null);
export const selectActivityUpdates = (s) => s.activityUpdates;
// Factory selector: subscribe to one run only (stable per runId, null-safe).
export const selectAIRunById = (runId) => (s) => (runId ? s.aiRuns[runId] || null : null);
export const selectPortfolioUpdate = (s) => s.portfolioUpdate;
export const selectPortfolioLive = (s) => s.portfolioLive;
export const selectPortfolioSynced = (s) => s.portfolioSynced;
export const selectTradeUpdates = (s) => s.tradeUpdates;
export const selectEngineEvents = (s) => s.engineEvents;
export const selectTradeLive = (s) => s.tradeLive;
export const selectTradeReviews = (s) => s.tradeReviews;
export const selectBrokerOrders = (s) => s.brokerOrders;
export const selectUnreadCount = (s) => s.unreadCount;
export const selectSectors = (s) => s.sectors;
export const selectGlobalMarkets = (s) => s.globalMarkets;
export const selectMovers = (s) => s.movers;
export const selectBreadth = (s) => s.breadth;
export const selectEngineStatus = (s) => s.engineStatus;
export const selectScannerFeed = (s) => s.scanner;
export const selectScannerRefreshedAt = (s) => s.scannerRefreshedAt;
export const selectNews = (s) => s.news;
export const selectBreakingNews = (s) => s.breakingNews;
export const selectLatestNotification = (s) => s.latestNotification;
export const selectWatchlistEvent = (s) => s.watchlistEvent;
export const selectMorningReportReadyAt = (s) => s.morningReportReadyAt;

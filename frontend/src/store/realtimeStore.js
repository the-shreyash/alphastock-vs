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
  // Live per-request "thinking" timeline (Sprint R7). Populated by ai.run.* /
  // ai.step events; correlated to a chat request by runId. Shape:
  //   { runId, sessionId, steps:[{ label, status }], active, startedAt, updatedAt }
  aiRun: null,

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
  // Bucket stored for future UI (no consumer yet); keeps the contract complete.
  news: [],
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

export const useRealtimeStore = create((set, get) => ({
  ...initialState,

  setConnection: (status, extra = {}) =>
    set((s) => ({ connection: { ...s.connection, status, ...extra } })),

  setSend: (send) => set({ send }),

  seedUnreadCount: (count) => set({ unreadCount: Number(count) || 0 }),

  markNotificationsRead: () => set({ unreadCount: 0 }),

  /** Merge a batch of live prices: { SYMBOL: { price, change_pct }, ... } */
  _mergePrices: (map) =>
    set((s) => ({ priceTicks: { ...s.priceTicks, ...map } })),

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
      case "news":
        // news.received carries { articles, count } — keep the latest list.
        set({ news: Array.isArray(data.articles) ? data.articles : (Array.isArray(data) ? data : []) });
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
            const byId = { ...s.tradeLive.byId };
            (Array.isArray(data.trades) ? data.trades : []).forEach((row) => {
              if (!row || !row.trade_id) return;
              const prev = byId[row.trade_id];
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
          set((s) => ({
            tradeReviews: { ...s.tradeReviews, [data.trade_id]: data.review },
          }));
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
        // Per-request "thinking" timeline (Sprint R7) — replaces the static
        // "Thinking…" with the live stages the AI is actually running. Kept in
        // its own slice so it never clobbers the background activity feed.
        if (event === "ai.run.started") {
          set({
            aiRun: {
              runId: data.run_id,
              sessionId: data.session_id,
              steps: (Array.isArray(data.steps) ? data.steps : []).map((label) => ({
                label,
                status: "pending",
              })),
              active: true,
              startedAt: data.started_at || envelope.timestamp || Date.now(),
              updatedAt: envelope.timestamp || Date.now(),
            },
          });
        } else if (event === "ai.step") {
          set((s) => {
            // Ignore steps for a stale/unknown run to avoid cross-talk.
            if (!s.aiRun || s.aiRun.runId !== data.run_id) return {};
            const steps = s.aiRun.steps.slice();
            if (data.index >= 0 && data.index < steps.length) {
              steps[data.index] = { ...steps[data.index], status: data.status };
            }
            return { aiRun: { ...s.aiRun, steps, updatedAt: envelope.timestamp || Date.now() } };
          });
        } else if (event === "ai.run.completed") {
          set((s) => {
            if (!s.aiRun || s.aiRun.runId !== data.run_id) return {};
            return {
              aiRun: {
                ...s.aiRun,
                active: false,
                status: data.status || "done",
                updatedAt: envelope.timestamp || Date.now(),
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

  reset: () => set({ ...initialState }),
}));

// ---- Selectors (subscribe to the narrowest slice) ----
export const selectConnected = (s) => s.connection.status === "live";
export const selectConnectionStatus = (s) => s.connection.status;
export const selectMarketData = (s) => s.marketData;
export const selectPriceTicks = (s) => s.priceTicks;
export const selectActivityUpdates = (s) => s.activityUpdates;
export const selectAIRun = (s) => s.aiRun;
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

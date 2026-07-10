import api from "./api";

// Trade service — frontend gateway to the Trading Engine (/api/trades) and
// the unified order history (/api/orders). Components never call these
// endpoints directly (see .claude/CODING_STANDARDS.md: networking lives in
// service files).

const tradeService = {
  // Lifecycle
  list: () => api.get("/trades").then(({ data }) => data),
  active: () => api.get("/trades/active").then(({ data }) => data),
  history: () => api.get("/trades/history").then(({ data }) => data),
  pnl: () => api.get("/trades/pnl").then(({ data }) => data),

  // Risk Manager
  validate: (trade) => api.post("/trades/validate", trade).then(({ data }) => data),
  riskSummary: () => api.get("/trades/risk/summary").then(({ data }) => data),

  // Entry / modify / exit
  create: (trade) => api.post("/trades", trade).then(({ data }) => data),
  modify: (tradeId, changes) => api.put(`/trades/${tradeId}`, changes).then(({ data }) => data),
  exit: (tradeId, payload) => api.post(`/trades/${tradeId}/exit`, payload).then(({ data }) => data),

  // Coaching
  liveTip: (tradeId) => api.get(`/trades/${tradeId}/live-tip`).then(({ data }) => data),

  // Unified order history (all brokers)
  orders: ({ broker, refresh } = {}) => {
    const params = new URLSearchParams();
    if (broker) params.set("broker", broker);
    if (refresh) params.set("refresh", "true");
    const qs = params.toString();
    return api.get(`/orders${qs ? `?${qs}` : ""}`).then(({ data }) => data);
  },
};

// Normalize a Trading Engine rejection (422 risk check / broker error) into
// something the UI can render: { message, violations[], warnings[] }.
export function tradeErrorDetails(err, fallback = "Trade request failed. Please retry.") {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") return { message: detail, violations: [], warnings: [] };
  if (detail && typeof detail === "object") {
    return {
      message: detail.message || fallback,
      violations: detail.violations || [],
      warnings: detail.warnings || [],
    };
  }
  return { message: fallback, violations: [], warnings: [] };
}

export default tradeService;

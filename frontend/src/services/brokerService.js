import api from "./api";

// Broker service — the single frontend gateway to the unified Broker Engine
// (/api/brokers). Components never call broker endpoints directly.
//
// TWO ADDRESSING MODES SINCE D6.4
// -------------------------------
// `account.*` addresses ONE brokerage account by its opaque `broker_account_id`.
// This is what every data and order call should use: a user may hold several
// accounts at one broker, and only the id names one of them.
//
// The broker-name calls below it are the compatibility bridge. They still work
// and are still the right thing for genuinely broker-level actions — listing
// what the deployment supports, and starting an OAuth login, which happens
// *before* an account exists. For anything account-specific the server answers
// 409 when the user holds more than one account at that broker, rather than
// choosing: there is no client-side fallback to add here, because there is no
// correct one.

const brokerService = {
  // Supported brokers, this user's per-broker status, and their accounts
  list: () => api.get("/brokers").then(({ data }) => data),
  status: () => api.get("/brokers/status").then(({ data }) => data),

  // ---- account-addressed (D6.4) ----
  accounts: () => api.get("/brokers/accounts").then(({ data }) => data.accounts),

  account: {
    get: (id) => api.get(`/brokers/accounts/${id}`).then(({ data }) => data),
    disconnect: (id) => api.post(`/brokers/accounts/${id}/disconnect`).then(({ data }) => data),
    sync: (id) => api.post(`/brokers/accounts/${id}/sync`).then(({ data }) => data),
    profile: (id) => api.get(`/brokers/accounts/${id}/profile`).then(({ data }) => data),
    holdings: (id) => api.get(`/brokers/accounts/${id}/holdings`).then(({ data }) => data),
    positions: (id) => api.get(`/brokers/accounts/${id}/positions`).then(({ data }) => data),
    funds: (id) => api.get(`/brokers/accounts/${id}/funds`).then(({ data }) => data),
    margins: (id) => api.get(`/brokers/accounts/${id}/margins`).then(({ data }) => data),
    orders: (id) => api.get(`/brokers/accounts/${id}/orders`).then(({ data }) => data),
    trades: (id) => api.get(`/brokers/accounts/${id}/trades`).then(({ data }) => data),
    placeOrder: (id, order) =>
      api.post(`/brokers/accounts/${id}/orders`, order).then(({ data }) => data),
    modifyOrder: (id, orderId, changes) =>
      api.patch(`/brokers/accounts/${id}/orders/${orderId}`, changes).then(({ data }) => data),
    cancelOrder: (id, orderId) =>
      api.delete(`/brokers/accounts/${id}/orders/${orderId}`).then(({ data }) => data),
  },

  // ---- broker-level: OAuth login, which precedes any account ----
  getLoginUrl: (broker) => api.get(`/brokers/${broker}/login-url`).then(({ data }) => data),
  exchangeSession: (broker, payload) =>
    api.post(`/brokers/${broker}/session`, payload).then(({ data }) => data),

  // ---- broker-addressed bridge: answers only when the user has ONE account
  //      there, and 409s otherwise. Kept for callers that have no id yet.
  disconnect: (broker) => api.post(`/brokers/${broker}/disconnect`).then(({ data }) => data),
  sync: (broker) => api.post(`/brokers/${broker}/sync`).then(({ data }) => data),
  holdings: (broker) => api.get(`/brokers/${broker}/holdings`).then(({ data }) => data),
  positions: (broker) => api.get(`/brokers/${broker}/positions`).then(({ data }) => data),
  funds: (broker) => api.get(`/brokers/${broker}/funds`).then(({ data }) => data),
  margins: (broker) => api.get(`/brokers/${broker}/margins`).then(({ data }) => data),
  profile: (broker) => api.get(`/brokers/${broker}/profile`).then(({ data }) => data),
  orders: (broker) => api.get(`/brokers/${broker}/orders`).then(({ data }) => data),
  trades: (broker) => api.get(`/brokers/${broker}/trades`).then(({ data }) => data),
  placeOrder: (broker, order) => api.post(`/brokers/${broker}/orders`, order).then(({ data }) => data),
  modifyOrder: (broker, orderId, changes) =>
    api.patch(`/brokers/${broker}/orders/${orderId}`, changes).then(({ data }) => data),
  cancelOrder: (broker, orderId) =>
    api.delete(`/brokers/${broker}/orders/${orderId}`).then(({ data }) => data),
};

//: HTTP status the server answers when a broker-addressed request cannot be
//: resolved to one account. Named so a component can render "choose an account"
//: instead of a generic failure.
export const BROKER_ACCOUNT_AMBIGUOUS_STATUS = 409;

export function isAmbiguousAccountError(err) {
  return (
    err?.response?.status === BROKER_ACCOUNT_AMBIGUOUS_STATUS &&
    typeof err?.response?.data?.detail === "string" &&
    err.response.data.detail.includes("broker_account_id")
  );
}

// Normalize backend/broker errors into a user-facing message
export function brokerErrorMessage(err, fallback = "Broker request failed. Please retry.") {
  return err?.response?.data?.detail || err?.response?.data?.message || fallback;
}

export default brokerService;

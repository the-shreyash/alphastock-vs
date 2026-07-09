import api from "./api";

// Broker service — the single frontend gateway to the unified Broker Engine
// (/api/brokers). Components never call broker endpoints directly.

const brokerService = {
  // Supported brokers + this user's per-broker connection status
  list: () => api.get("/brokers").then(({ data }) => data),
  status: () => api.get("/brokers/status").then(({ data }) => data),

  // OAuth
  getLoginUrl: (broker) => api.get(`/brokers/${broker}/login-url`).then(({ data }) => data),
  exchangeSession: (broker, payload) => api.post(`/brokers/${broker}/session`, payload).then(({ data }) => data),
  disconnect: (broker) => api.post(`/brokers/${broker}/disconnect`).then(({ data }) => data),

  // Portfolio
  sync: (broker) => api.post(`/brokers/${broker}/sync`).then(({ data }) => data),
  holdings: (broker) => api.get(`/brokers/${broker}/holdings`).then(({ data }) => data),
  positions: (broker) => api.get(`/brokers/${broker}/positions`).then(({ data }) => data),
  funds: (broker) => api.get(`/brokers/${broker}/funds`).then(({ data }) => data),
  margins: (broker) => api.get(`/brokers/${broker}/margins`).then(({ data }) => data),
  profile: (broker) => api.get(`/brokers/${broker}/profile`).then(({ data }) => data),

  // Orders & trade history
  orders: (broker) => api.get(`/brokers/${broker}/orders`).then(({ data }) => data),
  trades: (broker) => api.get(`/brokers/${broker}/trades`).then(({ data }) => data),
  placeOrder: (broker, order) => api.post(`/brokers/${broker}/orders`, order).then(({ data }) => data),
  modifyOrder: (broker, orderId, changes) => api.patch(`/brokers/${broker}/orders/${orderId}`, changes).then(({ data }) => data),
  cancelOrder: (broker, orderId) => api.delete(`/brokers/${broker}/orders/${orderId}`).then(({ data }) => data),
};

// Normalize backend/broker errors into a user-facing message
export function brokerErrorMessage(err, fallback = "Broker request failed. Please retry.") {
  return err?.response?.data?.detail || err?.response?.data?.message || fallback;
}

export default brokerService;

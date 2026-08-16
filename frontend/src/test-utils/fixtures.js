/**
 * Deterministic frontend test fixtures.
 *
 * Every shape here mirrors what the real backend returns (see backend/server.py)
 * so a test that passes against a fixture is evidence about production, not
 * about an invented contract. Nothing in this file may contain real user data,
 * real broker accounts, real credentials or production identifiers.
 *
 * Values are frozen constants — never `Date.now()`, never `Math.random()` — so
 * a failing test fails for the same reason on every machine and every run.
 */

/* Fixed clock reference used by every time-bearing fixture. */
export const FIXED_NOW_ISO = "2026-01-15T09:30:00.000Z";

/* ------------------------------------------------------------------ *
 * Users
 *
 * NOTE the deliberate shape difference, which is real: POST /auth/login
 * returns `id` + `token`, while GET /auth/me returns the persisted user
 * document keyed by `_id` and no token. Components must tolerate both
 * (RealtimeProvider reads `user._id || user.id`), so the fixtures keep them
 * distinct rather than papering over it.
 * ------------------------------------------------------------------ */

export const testUser = {
  _id: "u_test_000000000001",
  name: "Test Trader",
  email: "trader@test.invalid",
  role: "user",
  capital: 100000,
  risk_level: "moderate",
  created_at: FIXED_NOW_ISO,
};

export const testAdmin = {
  _id: "u_test_000000000002",
  name: "Test Admin",
  email: "admin@test.invalid",
  role: "admin",
  capital: 100000,
  risk_level: "moderate",
  created_at: FIXED_NOW_ISO,
};

export const testSuperAdmin = { ...testAdmin, _id: "u_test_000000000003", email: "super@test.invalid", role: "super_admin" };

/** The POST /auth/login | /auth/register success body. */
export function loginResponse(user = testUser) {
  return {
    id: user._id,
    name: user.name,
    email: user.email,
    role: user.role,
    capital: user.capital,
    risk_level: user.risk_level,
    token: "test.access.token",
  };
}

/* ------------------------------------------------------------------ *
 * Market data
 * ------------------------------------------------------------------ */

export const testStock = {
  symbol: "TESTCO",
  name: "Test Company Ltd",
  price: 1234.5,
  change: 12.35,
  change_percent: 1.01,
  volume: 1500000,
  previous_close: 1222.15,
};

export const testMarketOverview = {
  indices: [
    { symbol: "NIFTY 50", name: "Nifty 50", price: 22150.4, change: 120.6, change_percent: 0.55 },
    { symbol: "SENSEX", name: "Sensex", price: 73200.1, change: -85.2, change_percent: -0.12 },
  ],
  market_status: "OPEN",
  updated_at: FIXED_NOW_ISO,
};

export const testSectors = [
  { name: "IT", change_percent: 1.42, advancing: 8, declining: 2 },
  { name: "Banking", change_percent: -0.63, advancing: 3, declining: 9 },
];

/* ------------------------------------------------------------------ *
 * Portfolio / trading
 * ------------------------------------------------------------------ */

export const testPortfolioSummary = {
  total_value: 152340.25,
  invested: 140000,
  total_pnl: 12340.25,
  total_pnl_pct: 8.81,
  day_pnl: 1240.5,
  day_pnl_pct: 0.82,
  holdings_count: 4,
};

export const testOpenTrade = {
  _id: "t_test_000000000001",
  symbol: "TESTCO",
  stock_name: "Test Company Ltd",
  type: "BUY",
  quantity: 10,
  entry_price: 1200,
  current_price: 1234.5,
  stop_loss: 1150,
  target1: 1300,
  setup_type: "MOMENTUM",
  status: "OPEN",
  unrealized_pnl: 345,
  unrealized_pnl_pct: 2.88,
  entry_time: FIXED_NOW_ISO,
};

export const testClosedTrade = {
  _id: "t_test_000000000002",
  symbol: "OLDCO",
  type: "BUY",
  quantity: 5,
  entry_price: 900,
  exit_price: 960,
  stop_loss: 870,
  target1: 980,
  setup_type: "RSI_BREAKOUT",
  status: "CLOSED",
  pnl: 300,
  pnl_percent: 6.67,
  entry_time: "2026-01-10T04:00:00.000Z",
};

export const testPaperBalance = { balance: 100000, starting_balance: 100000 };

export const testPaperPnl = {
  total_pnl: 345,
  total_pnl_pct: 0.35,
  realized_pnl: 300,
  unrealized_pnl: 45,
  open_trades: 1,
  closed_trades: 1,
};

/* ------------------------------------------------------------------ *
 * Watchlist / notifications / AI
 * ------------------------------------------------------------------ */

export const testWatchlistItem = {
  symbol: "TESTCO",
  name: "Test Company Ltd",
  price: 1234.5,
  change_percent: 1.01,
  added_at: FIXED_NOW_ISO,
};

export const testNotification = {
  _id: "n_test_000000000001",
  title: "Target hit on TESTCO",
  message: "TESTCO reached your first target of 1300.",
  type: "trade",
  read: false,
  created_at: FIXED_NOW_ISO,
};

export const testConversation = {
  session_id: "chat-test-0001",
  title: "Swing setups this week",
  updated_at: FIXED_NOW_ISO,
  message_count: 4,
};

export const testAIResponse = {
  response:
    "TESTCO is showing a momentum breakout. Why: price closed above the 20-day high on 2x average volume. " +
    "Risks: the sector is extended and a broad pullback would hit this first. Watch next: a daily close back below 1200 invalidates the setup.",
  session_id: testConversation.session_id,
  model: "test-model",
};

/* ------------------------------------------------------------------ *
 * Admin
 * ------------------------------------------------------------------ */

/** GET /admin/dashboard — flat keys, matching STAT_CARDS in AdminDashboard.jsx. */
export const testAdminDashboard = {
  total_users: 1280,
  premium_users: 210,
  elite_users: 45,
  mrr: 245000,
  arr: 2940000,
  today_trades: 318,
  ai_requests_today: 3400,
  open_tickets: 7,
  broker_connections: 96,
  server_health: "healthy",
  db_health: "healthy",
  api_health: "healthy",
  // PH3.8: the backend now names its fabricated fields so the UI can mark them
  // instead of styling them like measured values.
  mock_metrics: ["revenue_today", "mrr", "arr", "api_health", "server_health"],
};

/** GET /admin/analytics/users — real counts alongside five fabricated metrics. */
export const testAdminUserAnalytics = {
  total_users: 1280,
  today_signups: 12,
  dau: 12,
  mau: 1280,
  retention_rate: 78.5,
  churn_rate: 4.2,
  growth_rate: 12.8,
  conversion_rate: 19.9,
  plan_breakdown: { user: 1025, pro: 210, elite: 45 },
  mock_metrics: ["dau", "mau", "retention_rate", "churn_rate", "growth_rate"],
};

/** GET /admin/analytics/revenue — a wholly fabricated series. */
export const testAdminRevenueAnalytics = {
  daily_revenue: [
    { date: "2026-08-15", revenue: 2500, subscriptions: 3, mock: true },
    { date: "2026-08-16", revenue: 2650, subscriptions: 4, mock: true },
  ],
  status: "mock",
  provenance: "mock",
  note: "Fabricated series generated by a formula. No payment records exist.",
  required_source: "Verified payment records.",
  mock_metrics: ["revenue", "subscriptions"],
};

/** GET /admin/analytics/features — real counts, invented percentages. */
export const testAdminFeatureAnalytics = {
  features: [
    { name: "AI Chat", usage_count: 3400, percentage: 85,
      usage_count_provenance: "derived", percentage_provenance: "mock" },
    { name: "Backtesting", usage_count: 0, percentage: 15,
      usage_count_provenance: "mock", percentage_provenance: "mock" },
  ],
  status: "mock",
  note: "Every percentage is a literal unrelated to the count beside it.",
  mock_metrics: ["percentage", "Backtesting"],
};

/* ------------------------------------------------------------------ *
 * Error bodies — the FastAPI shapes the UI's formatApiError() must survive.
 * ------------------------------------------------------------------ */

/** FastAPI HTTPException: `detail` is a plain string. */
export const errorDetailString = { detail: "Invalid email or password" };

/** FastAPI RequestValidationError (422): `detail` is a list of error objects. */
export const errorDetailValidation = {
  detail: [
    { loc: ["body", "password"], msg: "Password must contain a special character", type: "value_error" },
  ],
};

/** Trading Engine risk rejection: `detail` is an object with violations. */
export const errorDetailRisk = {
  detail: {
    message: "Trade rejected by risk manager",
    violations: ["Position size exceeds 10% of capital"],
    warnings: ["Sector exposure already at 35%"],
  },
};

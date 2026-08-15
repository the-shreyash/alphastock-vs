// ============================================================================
// StockAssist AI — PH3.5 user flows (brief §4, Scenarios A–E)
//
// Each exported function is ONE realistic user action, not one HTTP request.
// That distinction is the difference between a load test and a benchmark: a
// user does not call `GET /api/watchlist` in a tight loop, they open a page
// which fans out to a handful of endpoints and then they read it for several
// seconds. Modelling the page and the pause is what makes the resulting
// throughput number mean anything about capacity.
//
// Request mix is taken from what the frontend actually does on each screen
// (`frontend/src/pages/*`), not invented. Where an assumption was made it is
// stated in a comment above the flow.
// ============================================================================
/* eslint-disable no-undef */
import http from 'k6/http';
import { check, sleep } from 'k6';
import {
  BASE_URL, PASSWORD, ADMIN_EMAIL, SYMBOLS,
  record, authHeaders, pick, userEmail,
} from './config.js';

// ---------------------------------------------------------------------------
// Session establishment.
//
// Logged in ONCE per VU and cached, because that is what a real client does:
// a browser logs in and then reuses the session for the rest of the visit.
// Logging in per iteration would make every measurement a measurement of
// bcrypt (230 ms at the pinned cost of 12) and would exhaust the login policy
// (5 failures / 15 min) the moment anything went wrong.
//
// Authentication throughput is measured separately, by its own scenario, where
// it is the subject rather than an accident.
// ---------------------------------------------------------------------------
export function login(email) {
  const res = http.post(
    `${BASE_URL}/api/auth/login`,
    JSON.stringify({ email, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' }, tags: { kind: 'auth', name: 'POST /api/auth/login' } }
  );
  record(res, 'auth');
  check(res, { 'login 200': (r) => r.status === 200 });
  if (res.status !== 200) return null;
  try {
    return res.json('token');
  } catch (e) {
    return null;
  }
}

export function establishSession(vuIndex) {
  return login(userEmail(vuIndex));
}

export function establishAdminSession() {
  return login(ADMIN_EMAIL);
}

// A tagged GET. `kind` drives the endpoint-specific thresholds in config.js;
// `name` is the route template so k6 aggregates by route rather than by URL
// (a path parameter would otherwise explode the metric cardinality).
function get(token, path, kind, name) {
  const res = http.get(`${BASE_URL}${path}`, {
    headers: token ? authHeaders(token) : undefined,
    tags: { kind, name: name || path },
  });
  return record(res, kind);
}

function post(token, path, body, kind, name) {
  const res = http.post(`${BASE_URL}${path}`, JSON.stringify(body), {
    headers: authHeaders(token),
    tags: { kind, name: name || path },
  });
  return record(res, kind);
}

// ---------------------------------------------------------------------------
// Scenario A — Anonymous user
//
// Health probes and the public surface. Deliberately small and slow: the
// anonymous tier is PUBLIC_API = 60 requests/minute **per client IP**, and
// every virtual user in this test shares one source address. Driving anonymous
// traffic hard from a single host would measure the rate limiter, not the
// application, and would starve the other scenarios of the same budget.
// Rate-limit behaviour is validated deliberately and separately (ratelimit.js).
// ---------------------------------------------------------------------------
export function anonymousFlow() {
  const live = get(null, '/api/health/live', 'api');
  check(live, { 'health/live 200': (r) => r.status === 200 });

  const ready = get(null, '/api/health/ready', 'api');
  check(ready, { 'health/ready 200 or 503': (r) => r.status === 200 || r.status === 503 });

  get(null, '/api/stocks/universe', 'api');
  sleep(5 + Math.random() * 5);
}

// ---------------------------------------------------------------------------
// Scenario B — Authenticated user (the dashboard visit)
//
// The heaviest read fan-out in the product and the most-visited screen. This
// is the flow that decides the headline capacity number.
// ---------------------------------------------------------------------------
export function authenticatedFlow(token) {
  get(token, '/api/auth/me', 'api');
  // Watchlist and portfolio both enrich with live quotes, so they are `quote`
  // kind — held to the looser provider-inclusive threshold.
  check(get(token, '/api/watchlist', 'quote'), { 'watchlist ok': (r) => r.status === 200 || r.status === 429 });
  check(get(token, '/api/portfolio/summary', 'quote'), { 'portfolio summary ok': (r) => r.status === 200 || r.status === 429 });
  get(token, '/api/notifications', 'api');
  get(token, '/api/notifications/unread-count', 'api');
  get(token, '/api/trades/active', 'quote');
  sleep(4 + Math.random() * 6);
}

// ---------------------------------------------------------------------------
// Scenario C — Active trader
//
// Market data, then a risk check, then a paper order. `POST /api/paper/trade`
// is used rather than `POST /api/trades` deliberately: paper trading is
// simulated end to end and touches no broker, satisfying the brief's §16
// requirement that no real trade is ever executed during a load test.
// ---------------------------------------------------------------------------
export function traderFlow(token) {
  const symbol = pick(SYMBOLS);
  get(token, '/api/market/overview', 'quote');
  get(token, `/api/stocks/${symbol}`, 'quote', '/api/stocks/{symbol}');
  get(token, '/api/watchlist', 'quote');
  get(token, '/api/portfolio', 'quote');

  // Quantity 1 rather than 5. Each synthetic account starts with ₹100,000 of
  // paper capital (services/paper_trade.py DEFAULT_CAPITAL) and never sells, so
  // a 5-lot order at ₹2,500 exhausts the account after eight iterations and
  // every subsequent submission is correctly rejected with a 400.
  //
  // PH3.5's first baseline run did exactly that and reported a 4.4% 4xx rate.
  // That was the HARNESS being wrong, not the application: "Insufficient paper
  // capital" is the risk control working. Recorded here rather than quietly
  // fixed, because the useful discipline (PH3.4 §3.3) is to ask which side is
  // wrong before filing anything — and it was this side.
  const order = {
    symbol,
    stock_name: `${symbol} Ltd`,
    type: 'BUY',
    entry_price: 2500,
    quantity: 1,
    stop_loss: 2400,
    target1: 2650,
    is_paper: true,
  };

  const v = post(token, '/api/trades/validate', order, 'api');
  check(v, { 'validate ok': (r) => r.status === 200 || r.status === 429 });

  // Only submit when the risk engine approved. Submitting into a rejection
  // would exercise the 422 path repeatedly and quietly stop measuring the
  // write path this scenario exists to measure.
  if (v.status === 200) {
    let approved = false;
    try { approved = v.json('approved') === true; } catch (e) { approved = false; }
    if (approved) {
      const t = post(token, '/api/paper/trade', order, 'api');
      check(t, { 'paper trade accepted or capital-limited': (r) => r.status === 200 || r.status === 400 || r.status === 429 });

      // Out of paper capital → reset the paper account, which is precisely what
      // a real user does and which exercises a real endpoint. Without this the
      // scenario stops measuring the write path partway through a long run and
      // the throughput figure silently becomes a read-only one.
      if (t.status === 400) {
        post(token, '/api/paper/reset', {}, 'api');
      }
    }
  }

  get(token, '/api/paper/pnl', 'quote');
  sleep(3 + Math.random() * 5);
}

// ---------------------------------------------------------------------------
// Scenario D — AI user
//
// The point of this flow is NOT to measure how fast the AI is — that is the
// provider's number and the mock's sleep timer. It is to hold N requests open
// inside the application for ~1 s each and observe what that does to
// everything else: in-flight count, event-loop responsiveness, connection-pool
// occupancy. The AI mock reports its own max_concurrent so the two views can
// be reconciled.
// ---------------------------------------------------------------------------
export function aiFlow(token, vuIndex) {
  get(token, '/api/ai/status', 'api');
  get(token, '/api/ai/activity', 'api');
  get(token, '/api/market/overview', 'quote');

  const c = post(token, '/api/chat', {
    message: 'Summarise the risk in my current portfolio and what I should watch next.',
    session_id: `load-ai-${vuIndex}`,
  }, 'ai');
  check(c, { 'chat answered': (r) => r.status === 200 || r.status === 429 });

  sleep(8 + Math.random() * 7);   // AI use is bursty and infrequent per user
}

// ---------------------------------------------------------------------------
// Scenario E — Admin
//
// Small share by design: there are few admins and they are not a capacity
// driver. Included because PH3.4 optimised two admin endpoints (O-3 N+1
// removal, O-4 fan-out parallelisation) and this is the first opportunity to
// see whether those hold up under concurrency.
// ---------------------------------------------------------------------------
export function adminFlow(token) {
  get(token, '/api/admin/dashboard', 'api');
  get(token, '/api/admin/users?limit=25', 'api', '/api/admin/users');
  get(token, '/api/admin/logs?limit=25', 'api', '/api/admin/logs');
  get(token, '/api/admin/analytics/users', 'api');
  sleep(6 + Math.random() * 6);
}

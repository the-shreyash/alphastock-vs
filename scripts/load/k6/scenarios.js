// ============================================================================
// StockAssist AI — PH3.5 mixed-traffic load test (brief §4–§8)
//
// ONE script, five concurrent scenarios, sized by the LOAD_STAGE environment
// variable. Having smoke, baseline, moderate, high and stress be *the same
// script at different sizes* is what makes the results comparable; five
// separate files would drift and the comparison would quietly stop meaning
// anything.
//
//   k6 run -e LOAD_STAGE=smoke     scripts/load/k6/scenarios.js
//   k6 run -e LOAD_STAGE=baseline  scripts/load/k6/scenarios.js
//   k6 run -e LOAD_STAGE=moderate  scripts/load/k6/scenarios.js
//   k6 run -e LOAD_STAGE=high      scripts/load/k6/scenarios.js
//   k6 run -e LOAD_STAGE=stress    scripts/load/k6/scenarios.js
//
// Normally invoked through scripts/load/load-test.sh, which also does the
// preflight checks and captures the server-side metric snapshots.
// ============================================================================
/* eslint-disable no-undef */
import { sleep } from 'k6';
import exec from 'k6/execution';
import {
  THRESHOLDS, USER_COUNT, installResponseCallback,
} from './lib/config.js';
import {
  establishSession, establishAdminSession,
  anonymousFlow, authenticatedFlow, traderFlow, aiFlow, adminFlow,
} from './lib/flows.js';

// ---------------------------------------------------------------------------
// Concurrency stages (brief §5).
//
// `total` is the peak concurrent virtual users; the mix below splits it across
// the five scenarios. These numbers are the *tested* levels — the brief is
// explicit (§5) that they must not be assumed to represent real capacity, and
// §18 of the certification reports what was actually sustained rather than
// what was attempted.
// ---------------------------------------------------------------------------
const STAGES = {
  smoke: { total: 5, ramp: '5s', hold: '30s', down: '5s' },
  baseline: { total: 10, ramp: '15s', hold: '60s', down: '10s' },
  moderate: { total: 25, ramp: '20s', hold: '90s', down: '10s' },
  high: { total: 50, ramp: '30s', hold: '120s', down: '15s' },
  stress: { total: 100, ramp: '40s', hold: '120s', down: '20s' },
};

// ---------------------------------------------------------------------------
// Traffic mix (brief §4).
//
// ASSUMPTION, stated because the brief (§4) requires assumptions to be
// documented rather than buried: StockAssist has no production telemetry yet,
// so this mix is derived from the product's own structure — which screens exist
// (`frontend/src/pages`), which are on the default post-login route, and which
// are gated behind a plan or a role — not from observed traffic. It should be
// replaced with measured proportions once PH3.7 monitoring is live.
//
//   45% dashboard/portfolio browsing  — the default landing screen
//   30% active trading                — the product's core loop
//   10% AI interaction                — high value, low frequency, and the
//                                       slowest per request
//   10% anonymous                     — landing page, health probes
//    5% admin                         — a handful of operators
// ---------------------------------------------------------------------------
const MIX = { authenticated: 0.45, trader: 0.30, ai: 0.10, anonymous: 0.10, admin: 0.05 };

const stageName = __ENV.LOAD_STAGE || 'smoke';
const stage = STAGES[stageName];
if (!stage) {
  throw new Error(`unknown LOAD_STAGE=${stageName}; expected one of ${Object.keys(STAGES).join(', ')}`);
}

// At least one VU per scenario even at the smallest stage — a scenario rounded
// down to zero would silently drop out of the mix and the run would report a
// clean result for traffic it never sent.
function vus(share) {
  return Math.max(1, Math.round(stage.total * share));
}

function rampingScenario(fn, share, startIndex) {
  return {
    executor: 'ramping-vus',
    exec: fn,
    startVUs: 0,
    // Controlled ramp (brief §6): warm-up → load → cool-down. Slamming peak
    // concurrency into a cold process measures connection-pool construction and
    // lazy imports (PH3.4 §3.3 nearly attributed 288 ms of one-time import to
    // an endpoint that runs in 11 ms), not steady-state behaviour.
    stages: [
      { duration: stage.ramp, target: vus(share) },
      { duration: stage.hold, target: vus(share) },
      { duration: stage.down, target: 0 },
    ],
    gracefulRampDown: '10s',
    tags: { scenario_group: fn },
    env: { VU_OFFSET: String(startIndex) },
  };
}

export const options = {
  discardResponseBodies: false,
  thresholds: THRESHOLDS,
  scenarios: {
    // Offsets keep each scenario's VUs on a disjoint slice of the seeded user
    // pool. Two scenarios sharing a user would share that user's 120 req/min
    // budget, and the resulting 429s would be an artefact of the harness rather
    // than a property of the system.
    authenticated: rampingScenario('authenticated', MIX.authenticated, 0),
    trader: rampingScenario('trader', MIX.trader, 1000),
    ai: rampingScenario('ai', MIX.ai, 2000),
    anonymous: rampingScenario('anonymous', MIX.anonymous, 3000),
    admin: rampingScenario('admin', MIX.admin, 4000),
  },
};

installResponseCallback();

// Per-VU session cache. `exec.vu.idInInstance` is stable for the life of a VU,
// so each VU logs in once and then behaves like a browser holding a session.
const sessions = {};

function sessionFor(offset) {
  const id = exec.vu.idInInstance;
  const key = `${offset}:${id}`;
  if (!sessions[key]) {
    sessions[key] = establishSession((offset + id) % USER_COUNT);
    if (!sessions[key]) {
      // A failed login is not something to retry in a hot loop — that is how a
      // capacity test turns into a credential-stuffing pattern against its own
      // rate limiter. Back off and let the next iteration try once.
      sleep(2);
    }
  }
  return sessions[key];
}

export function authenticated() {
  const token = sessionFor(0);
  if (token) authenticatedFlow(token);
}

export function trader() {
  const token = sessionFor(1000);
  if (token) traderFlow(token);
}

export function ai() {
  const token = sessionFor(2000);
  if (token) aiFlow(token, exec.vu.idInInstance);
}

export function anonymous() {
  anonymousFlow();
}

export function admin() {
  const key = 'admin';
  if (!sessions[key]) {
    sessions[key] = establishAdminSession();
    if (!sessions[key]) sleep(2);
  }
  if (sessions[key]) adminFlow(sessions[key]);
}

export function handleSummary(data) {
  const out = {};
  const path = __ENV.LOAD_SUMMARY_PATH;
  if (path) out[path] = JSON.stringify(data, null, 2);
  out.stdout = textSummary(data);
  return out;
}

// A compact summary. k6's own `textSummary` helper lives on jslib (a remote
// import), and this harness deliberately has no network dependency at run time
// — a load test that cannot start because a CDN is down is a load test nobody
// runs.
function textSummary(data) {
  const m = data.metrics;
  const line = (k, v) => `  ${k.padEnd(34)} ${v}\n`;
  const q = (name, stat) => (m[name] && m[name].values[stat] !== undefined
    ? m[name].values[stat].toFixed(2) : 'n/a');
  const c = (name) => (m[name] ? m[name].values.count : 'n/a');
  const r = (name) => (m[name] && m[name].values.rate !== undefined
    ? (m[name].values.rate * 100).toFixed(3) + '%' : 'n/a');

  let s = `\n=== PH3.5 ${stageName} (peak ${stage.total} VUs) ===\n`;
  s += line('requests', c('http_reqs'));
  s += line('rps', q('http_reqs', 'rate'));
  s += line('http_req_duration p50', q('http_req_duration', 'med') + ' ms');
  s += line('http_req_duration p90', q('http_req_duration', 'p(90)') + ' ms');
  s += line('http_req_duration p95', q('http_req_duration', 'p(95)') + ' ms');
  s += line('http_req_duration p99', q('http_req_duration', 'p(99)') + ' ms');
  s += line('http_req_duration max', q('http_req_duration', 'max') + ' ms');
  s += line('5xx rate', r('sa_5xx_rate'));
  s += line('429 rate', r('sa_429_rate'));
  s += line('4xx rate (excl 429)', r('sa_4xx_rate'));
  s += line('timeout rate', r('sa_timeout_rate'));
  s += line('api latency p95', q('sa_api_latency', 'p(95)') + ' ms');
  s += line('ai latency p95', q('sa_ai_latency', 'p(95)') + ' ms');
  s += line('login latency p95', q('sa_auth_login_latency', 'p(95)') + ' ms');
  s += line('checks passed', r('checks'));
  s += line('iterations', c('iterations'));
  return s;
}

// ============================================================================
// StockAssist AI — PH3.5 saturation search (brief §18)
//
// WHY THIS EXISTS SEPARATELY FROM scenarios.js
// --------------------------------------------
// `scenarios.js` models realistic users: a page visit, then several seconds of
// reading. That is the right shape for asking "what does the system do under
// plausible traffic", and it is the wrong shape for asking "where does it
// break" — because throughput there is governed by the think-time, so adding
// virtual users buys linear throughput and the system never approaches its
// limit. PH3.5's mixed-traffic ladder reached 100 VUs at 68 rps with a flat
// 9 ms median, which says the envelope is comfortable and says nothing about
// the ceiling.
//
// This script removes the think-time and drives a **fixed arrival rate**
// instead, stepping it up until an acceptance threshold breaks. Arrival rate
// rather than VU count is the correct instrument for the question: with
// `ramping-arrival-rate`, k6 allocates however many VUs are needed to sustain
// the requested rate, so when the system slows down the offered load stays
// constant and the queue becomes visible. With `ramping-vus`, a system that
// slows down simply receives less traffic and hides its own saturation.
//
// SCOPE: the authenticated read path only. Deliberately:
//   * no login (bcrypt cost 12 is a known 230 ms fixed cost and would dominate
//     every step — it is measured on its own in auth.js);
//   * no AI (the mock's 900 ms sleep would dominate likewise);
//   * no writes (a saturation search that fills the database changes the thing
//     it is measuring as it runs).
// What remains is the read fan-out that serves the product's most-visited
// screens, which is what a capacity number should be about.
//
//   k6 run scripts/load/k6/saturation.js
//   k6 run -e SAT_PEAK=600 -e SAT_STEP=60 scripts/load/k6/saturation.js
// ============================================================================
/* eslint-disable no-undef */
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Trend } from 'k6/metrics';
import {
  BASE_URL, PASSWORD, USER_COUNT, userEmail,
  record, authHeaders, installResponseCallback, rate5xx, rate429,
} from './lib/config.js';

const PEAK = parseInt(__ENV.SAT_PEAK || '400', 10);   // requests/second at the top step
const STEP = parseInt(__ENV.SAT_STEP || '30', 10);    // seconds per step

// Per-step latency, so the report shows WHERE the knee is rather than one
// blended percentile across the whole ramp — a blended p95 over a ramp that
// ends in collapse looks merely mediocre, and the knee is the entire point.
const stepLatency = new Trend('sa_sat_latency', true);

// A single sustained rate instead of a ramp. The ramp finds *that* there is a
// knee; only a flat hold at one rate can say what steady-state latency at that
// rate actually is, because a ramp's percentiles blend every step it passed
// through on the way up.
const FLAT_RATE = parseInt(__ENV.SAT_RATE || '0', 10);
const FLAT_DURATION = __ENV.SAT_DURATION || '60s';

const rampingScenario = {
  executor: 'ramping-arrival-rate',
  startRate: 20,
  timeUnit: '1s',
  // Generous VU ceiling: if k6 runs out of VUs it silently stops offering the
  // requested rate and the test quietly becomes a different, easier one.
  // `dropped_iterations` is checked in the summary for exactly this.
  preAllocatedVUs: 60,
  maxVUs: 400,
  stages: [
    { duration: `${STEP}s`, target: Math.round(PEAK * 0.125) },
    { duration: `${STEP}s`, target: Math.round(PEAK * 0.25) },
    { duration: `${STEP}s`, target: Math.round(PEAK * 0.5) },
    { duration: `${STEP}s`, target: Math.round(PEAK * 0.75) },
    { duration: `${STEP}s`, target: PEAK },
    { duration: '15s', target: 0 },
  ],
};

const flatScenario = {
  executor: 'constant-arrival-rate',
  rate: FLAT_RATE,
  timeUnit: '1s',
  duration: FLAT_DURATION,
  preAllocatedVUs: Math.min(400, Math.max(20, FLAT_RATE)),
  maxVUs: 400,
};

export const options = {
  scenarios: { saturate: FLAT_RATE > 0 ? flatScenario : rampingScenario },
  thresholds: {
    // Recorded, not gated. This script's job is to FIND the point where these
    // break, so failing the run when they do would be failing on success.
    'http_req_duration{kind:api}': ['p(95)<500'],
    sa_5xx_rate: ['rate<0.01'],
    // Declared solely to make k6 materialise the `phase:load` submetrics, which
    // `handleSummary` then reports. k6 only creates a submetric for a tag
    // combination that some threshold names, so without these two lines the
    // summary below would read `n/a` for everything.
    //
    // The distinction they draw is not cosmetic. `setup()` mints tokens with
    // real logins, and a login costs ~230 ms of bcrypt. At 40 rps for 45 s the
    // run makes ~1,800 load requests, so those setup logins are ~10% of all
    // samples — enough to BE the p95. PH3.5's first flat-rate sweep reported a
    // p95 of ~240 ms at 40, 60, 80 and 100 rps alike, which is not a
    // suspiciously stable system, it is bcrypt's latency showing through a
    // percentile that should never have included it.
    'http_req_duration{phase:load}': ['p(95)>=0'],
    'http_reqs{phase:load}': ['count>=0'],
  },
};

installResponseCallback();

// ---------------------------------------------------------------------------
// Tokens are minted once in `setup()` and shared by every VU.
//
// `setup()` rather than module scope because k6 forbids HTTP in the init
// context; it runs exactly once and its return value is handed to every
// iteration, which is precisely the shape needed here.
//
// This is the one place the harness deliberately does NOT behave like a real
// client. At 400 requests/second, logging in per VU would mean hundreds of
// bcrypt verifications and the test would measure password hashing. Tokens are
// stateless JWTs, so reusing them exercises exactly the same authentication
// code (`get_current_user` decodes and looks the principal up in MongoDB on
// every single request — no cache is being skipped and no work is being
// avoided).
//
// The accounts are spread across the seeded pool rather than shared, because
// the AUTHENTICATED_API tier is 120 requests/minute PER USER: one shared
// account would cap the whole test at 2 rps and the "saturation point" found
// would be the rate limiter's, not the application's.
// ---------------------------------------------------------------------------
const TOKEN_COUNT = Math.min(200, USER_COUNT);

export function setup() {
  const tokens = [];
  // Minted in batches of 8 rather than serially. 200 serial logins at ~230 ms
  // of bcrypt each is ~46 seconds of setup per run, which at five runs is four
  // minutes of waiting for nothing. Eight at a time keeps setup to a few
  // seconds without asking the server to run 200 concurrent bcrypt operations,
  // which would be a load test of its own.
  const BATCH = 8;
  for (let i = 0; i < TOKEN_COUNT; i += BATCH) {
    const reqs = [];
    for (let j = i; j < Math.min(i + BATCH, TOKEN_COUNT); j++) {
      reqs.push(['POST', `${BASE_URL}/api/auth/login`,
        JSON.stringify({ email: userEmail(j), password: PASSWORD }),
        { headers: { 'Content-Type': 'application/json' }, tags: { phase: 'setup' } }]);
    }
    const responses = http.batch(reqs);
    for (const res of responses) {
      if (res.status === 200) {
        try { tokens.push(res.json('token')); } catch (e) { /* skip */ }
      }
    }
  }
  if (tokens.length === 0) throw new Error('saturation: could not mint any tokens');
  // eslint-disable-next-line no-console
  console.log(`saturation: minted ${tokens.length} tokens`);
  return { tokens };
}

// The read fan-out of one dashboard visit, issued back to back with no pause.
const PATHS = [
  ['/api/watchlist', 'quote'],
  ['/api/portfolio/summary', 'quote'],
  ['/api/trades/active', 'quote'],
  ['/api/notifications', 'api'],
  ['/api/notifications/unread-count', 'api'],
  ['/api/auth/me', 'api'],
];

export default function (data) {
  const token = data.tokens[exec.scenario.iterationInTest % data.tokens.length];
  const [path, kind] = PATHS[exec.scenario.iterationInTest % PATHS.length];
  const res = http.get(`${BASE_URL}${path}`, {
    headers: authHeaders(token),
    tags: { kind, name: path, phase: 'load' },
  });
  record(res, kind);
  stepLatency.add(res.timings.duration);
  check(res, { 'served (2xx or throttled)': (r) => r.status === 200 || r.status === 429 });
}

export function handleSummary(data) {
  const m = data.metrics;
  const v = (n, s) => (m[n] && m[n].values[s] !== undefined ? m[n].values[s] : null);
  const f = (x, d) => (x === null ? 'n/a' : x.toFixed(d === undefined ? 2 : d));

  const mode = FLAT_RATE > 0
    ? `flat ${FLAT_RATE} rps for ${FLAT_DURATION}`
    : `ramp to ${PEAK} rps`;
  // Everything below is the `phase:load` submetric — the setup logins are
  // excluded, for the reason recorded beside the thresholds above.
  const D = 'http_req_duration{phase:load}';
  const R = 'http_reqs{phase:load}';
  let out = `\n=== PH3.5 saturation search (${mode}) ===\n`;
  out += `  load requests       ${f(v(R, 'count'), 0)}   (setup logins excluded)\n`;
  // k6's own `rate` divides by total wall-clock, which includes `setup()` and
  // the graceful stop. For a flat run the meaningful denominator is the hold
  // itself, so it is computed here rather than quoted from k6 — otherwise a
  // run that delivered exactly the requested rate reports about half of it and
  // reads as a system that could not keep up.
  if (FLAT_RATE > 0) {
    const secs = parseInt(FLAT_DURATION, 10);
    const count = v(R, 'count');
    out += `  delivered rps       ${count !== null && secs ? f(count / secs) : 'n/a'}   (offered ${FLAT_RATE})\n`;
  } else {
    out += `  achieved rps        ${f(v(R, 'rate'))}   (whole-run average, includes ramp)\n`;
  }
  out += `  dropped iterations  ${f(v('dropped_iterations', 'count'), 0)}   (non-zero = k6 could not offer the requested rate)\n`;
  out += `  p50 / p95 / p99     ${f(v(D, 'med'))} / ${f(v(D, 'p(95)'))} / ${f(v(D, 'p(99)'))} ms\n`;
  out += `  max                 ${f(v(D, 'max'))} ms\n`;
  out += `  5xx rate            ${f((v('sa_5xx_rate', 'rate') || 0) * 100, 3)}%\n`;
  out += `  429 rate            ${f((v('sa_429_rate', 'rate') || 0) * 100, 3)}%\n`;
  out += `  timeout rate        ${f((v('sa_timeout_rate', 'rate') || 0) * 100, 3)}%\n`;
  out += `  checks              ${f((v('checks', 'rate') || 0) * 100, 2)}%\n`;
  const res = { stdout: out };
  if (__ENV.LOAD_SUMMARY_PATH) res[__ENV.LOAD_SUMMARY_PATH] = JSON.stringify(data, null, 2);
  return res;
}

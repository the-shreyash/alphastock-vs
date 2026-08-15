// ============================================================================
// StockAssist AI — PH3.5 shared load-test configuration
//
// One definition of the target, the fixtures, the traffic-model constants and
// the acceptance thresholds. Every scenario file imports from here so that a
// smoke run and a stress run are provably the same test at different sizes —
// if the two could drift, comparing their results would be meaningless.
// ============================================================================
/* eslint-disable no-undef */
import { Rate, Trend, Counter } from 'k6/metrics';
import http from 'k6/http';

export const BASE_URL = __ENV.LOAD_BASE_URL || 'http://127.0.0.1:8000';
export const WS_URL = __ENV.LOAD_WS_URL || 'ws://127.0.0.1:8000/api/ws';

// Written by backend/scripts/seed_load_fixtures.py. Read at init time (k6 only
// permits file access there). Generating credentials in the script instead
// would let the driver and the database disagree about who exists, and the
// resulting 401 storm would look exactly like a capacity failure.
export const FIXTURES = JSON.parse(open('../../fixtures.json'));

// ---------------------------------------------------------------------------
// Traffic-model constants
//
// SYMBOLS is the seeded universe. Requesting a symbol outside it would take an
// unknown-symbol branch that skips the quote fan-out — the most expensive work
// on the hot path — and would silently make the whole test cheaper.
// ---------------------------------------------------------------------------
export const SYMBOLS = FIXTURES.symbols;
export const USER_COUNT = FIXTURES.user_count;
export const PASSWORD = FIXTURES.password;
export const ADMIN_EMAIL = FIXTURES.admin_email;

export function userEmail(i) {
  return `loaduser${i % USER_COUNT}@loadtest.invalid`;
}

export function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

// ---------------------------------------------------------------------------
// Custom metrics
//
// k6's built-in `http_req_failed` folds every non-2xx into one rate. That is
// the wrong shape for this sprint: the brief (§7, §9) requires the 429 rate and
// the 5xx rate to be reported and interpreted *separately*, because a 429 is
// the rate limiter working correctly and a 5xx is the system breaking. Rolling
// them together would let a run that is 8% rate-limited and 0% broken look
// identical to one that is 8% broken.
// ---------------------------------------------------------------------------
export const rate5xx = new Rate('sa_5xx_rate');
export const rate429 = new Rate('sa_429_rate');
export const rate4xx = new Rate('sa_4xx_rate');           // excluding 429
export const rateTimeout = new Rate('sa_timeout_rate');
export const apiLatency = new Trend('sa_api_latency', true);
export const aiLatency = new Trend('sa_ai_latency', true);
export const authLatency = new Trend('sa_auth_login_latency', true);
export const reqCount = new Counter('sa_requests');

// ---------------------------------------------------------------------------
// Only a 5xx or a transport failure marks a request "failed" for k6's own
// http_req_failed. A 429 is a designed outcome under this traffic model and a
// 401/404 during a negative check is intentional; counting either as a failure
// would make the headline error rate uninterpretable. They are still recorded,
// on their own rates above.
// ---------------------------------------------------------------------------
export function installResponseCallback() {
  http.setResponseCallback(http.expectedStatuses({ min: 200, max: 499 }));
}

// ---------------------------------------------------------------------------
// Acceptance thresholds (brief §8).
//
// THESE ARE ENGINEERING TARGETS FOR THIS SYSTEM ON THIS ENVIRONMENT, not
// industry guarantees. They are declared here, before any result was seen, so
// that the certification cannot be accused of having fitted the bar to the
// measurement.
//
// Endpoint-specific bars, and why each differs:
//
//   kind:api    Ordinary database-backed reads. p95 < 500 ms / p99 < 1 s — the
//               brief's default, and reasonable for work PH3.4 measured at
//               ≤11 ms of application code.
//   kind:quote  Reads that fan out to the market-data provider. Held to a
//               looser bar because the dominant term is provider transport
//               (PH3.4 §7 measured it at >90% of total), which is a property of
//               the provider, not of StockAssist. Judging them against the
//               plain-read bar would be judging the mock's sleep timer.
//   kind:ai     AI-provider calls. The mock answers in 900 ms by configuration,
//               so anything near that is the provider and anything far above it
//               is queueing inside the application — which is the number this
//               sprint actually wants.
//   kind:auth   Login. Deliberately the loosest bar, and NOT because logins are
//               allowed to be slow: `security/passwords.py` pins bcrypt at cost
//               12, measured at 230 ms per verification on the reference host.
//               A sub-500 ms threshold here would be a threshold on bcrypt's
//               cost factor — i.e. on a security control — and the brief (§24)
//               forbids trading that for a benchmark number.
// ---------------------------------------------------------------------------
export const THRESHOLDS = {
  'http_req_duration{kind:api}': ['p(95)<500', 'p(99)<1000'],
  'http_req_duration{kind:quote}': ['p(95)<1500', 'p(99)<3000'],
  'http_req_duration{kind:ai}': ['p(95)<5000'],
  'http_req_duration{kind:auth}': ['p(95)<2000'],
  // The two that decide pass/fail. 5xx is ours; timeouts are ours.
  sa_5xx_rate: ['rate<0.01'],
  sa_timeout_rate: ['rate<0.01'],
  // Recorded, deliberately not gated: under this traffic model a 429 is the
  // rate limiter behaving as designed. §9 of the certification explains every
  // 429 observed rather than a threshold pretending none should occur.
  sa_429_rate: ['rate>=0'],
  checks: ['rate>0.99'],
};

// ---------------------------------------------------------------------------
// Request wrapper. Every scenario goes through this so that classification is
// uniform and no call site can forget to record a 429.
// ---------------------------------------------------------------------------
export function record(res, kind) {
  reqCount.add(1, { kind });
  const s = res.status;
  rate5xx.add(s >= 500, { kind });
  rate429.add(s === 429, { kind });
  rate4xx.add(s >= 400 && s < 500 && s !== 429, { kind });
  // k6 reports a transport-level failure (connection refused, read timeout) as
  // status 0. That is the timeout signal the brief §7 asks for; an HTTP 504
  // would be a different thing and is counted as a 5xx.
  rateTimeout.add(s === 0, { kind });

  if (kind === 'ai') aiLatency.add(res.timings.duration);
  else if (kind === 'auth') authLatency.add(res.timings.duration);
  else apiLatency.add(res.timings.duration);
  return res;
}

export function authHeaders(token, extra) {
  return Object.assign(
    { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    extra || {}
  );
}

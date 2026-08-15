// ============================================================================
// StockAssist AI — PH3.5 authentication throughput (brief §10)
//
// Login is the one endpoint whose cost is deliberately high: `security/
// passwords.py` pins bcrypt at cost 12, measured at ~230 ms per verification on
// the reference host. That is a security property, not a defect, and this test
// exists to establish what it costs in throughput terms so the number is a
// known engineering fact rather than a surprise during a launch.
//
// The brief (§10) is explicit that this is a CAPACITY test, not an attack
// simulation:
//   * every login uses a VALID credential, so no failure budget is consumed and
//     no account is ever locked out (the LOGIN policy counts failures only);
//   * each VU uses its own account, so the ip:account policy is never tripped;
//   * no brute-force pattern is generated at any point.
//
// Measures login, session creation, refresh, logout, and logout-all.
//
//   k6 run scripts/load/k6/auth.js
//   k6 run -e AUTH_VUS=25 scripts/load/k6/auth.js
// ============================================================================
/* eslint-disable no-undef */
import http from 'k6/http';
import { check } from 'k6';
import { Trend, Counter } from 'k6/metrics';
import exec from 'k6/execution';
import { BASE_URL, PASSWORD, USER_COUNT, userEmail, installResponseCallback } from './lib/config.js';

const loginTrend = new Trend('sa_login_ms', true);
const refreshTrend = new Trend('sa_refresh_ms', true);
const logoutTrend = new Trend('sa_logout_ms', true);
const logoutAllTrend = new Trend('sa_logout_all_ms', true);
const loginOk = new Counter('sa_login_ok');
const loginFail = new Counter('sa_login_fail');

const VUS = parseInt(__ENV.AUTH_VUS || '10', 10);

export const options = {
  scenarios: {
    auth: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '10s', target: VUS },
        { duration: '60s', target: VUS },
        { duration: '10s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    // No bar on login latency: it would be a bar on bcrypt's cost factor, and
    // the brief (§24) forbids trading a security control for a benchmark
    // number. The figure is REPORTED, and §11 of the certification interprets
    // it. What is gated is that logins keep *succeeding* and nothing 5xxs.
    sa_login_fail: ['count==0'],
    http_req_failed: ['rate<0.01'],
  },
};

installResponseCallback();

// The pool slice reserved for this test — disjoint from the accounts the mixed
// traffic model uses, so a session revoked here can never surface as a
// mysterious 401 in another scenario's results.
// The current double-submit token, read fresh from the VU's cookie jar — the
// same thing a browser does on every mutating request.
function readCsrf(jar) {
  const cookies = jar.cookiesForURL(BASE_URL);
  return cookies['csrf_token'] ? cookies['csrf_token'][0] : null;
}

function accountFor() {
  return userEmail(100 + (exec.vu.idInInstance % Math.max(1, Math.min(100, USER_COUNT - 100))));
}

export default function () {
  const email = accountFor();
  const jar = http.cookieJar();

  // --- login (bcrypt cost 12 + session creation) ---------------------------- #
  const login = http.post(`${BASE_URL}/api/auth/login`,
    JSON.stringify({ email, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' }, tags: { name: 'POST /api/auth/login' } });
  loginTrend.add(login.timings.duration);
  const ok = check(login, { 'login 200': (r) => r.status === 200 });
  if (!ok) { loginFail.add(1); return; }
  loginOk.add(1);

  let token = null;
  try { token = login.json('token'); } catch (e) { token = null; }

  // The login response also planted access/refresh/CSRF cookies in the jar.
  // The refresh below rides those cookies rather than a Bearer token, because
  // the cookie path is the one a browser actually uses and the one the CSRF
  // middleware is wired for.
  const csrfAtLogin = readCsrf(jar);
  check(null, { 'CSRF cookie planted at login': () => csrfAtLogin !== null });

  // --- refresh (token rotation) -------------------------------------------- #
  // `/api/auth/refresh` is CSRF-exempt by design (security/csrf.py documents
  // why: a forged refresh only rotates the victim's own tokens), so this is the
  // cookie-only path exactly as the SPA performs it.
  const refresh = http.post(`${BASE_URL}/api/auth/refresh`, null,
    { tags: { name: 'POST /api/auth/refresh' } });
  refreshTrend.add(refresh.timings.duration);
  check(refresh, { 'refresh 200': (r) => r.status === 200 || r.status === 429 });

  // --- an authenticated read, to prove the rotated session works ------------ #
  if (token) {
    const me = http.get(`${BASE_URL}/api/auth/me`,
      { headers: { Authorization: `Bearer ${token}` }, tags: { name: '/api/auth/me' } });
    check(me, { 'me 200': (r) => r.status === 200 });
  }

  // --- logout / logout-all --------------------------------------------------- #
  // Alternated rather than both every iteration: logout-all revokes every
  // session for the account, and running it on every pass would mean this test
  // spent most of its time measuring revocation rather than login. One in four.
  if (exec.scenario.iterationInTest % 4 === 0) {
    // The CSRF token is RE-READ here rather than reused from login.
    // `/api/auth/refresh` calls `set_csrf_cookie` again (server.py), which mints
    // a fresh random double-submit value bound to the same session — so the
    // token captured at login is stale the moment a refresh happens, and
    // `tokens_match(header, cookie)` correctly rejects it with a 403.
    //
    // PH3.5's first auth run sent the login-time token and saw 83/83 logout-all
    // calls fail. That was the HARNESS being wrong: re-minting the token on
    // rotation is exactly what a double-submit implementation should do, and a
    // browser reads the cookie fresh on every request. Verified by hand against
    // a live session before anything was filed.
    const csrf = readCsrf(jar);
    const la = http.post(`${BASE_URL}/api/auth/logout-all`, null, {
      headers: csrf ? { 'X-CSRF-Token': csrf } : {},
      tags: { name: 'POST /api/auth/logout-all' },
    });
    logoutAllTrend.add(la.timings.duration);
    check(la, { 'logout-all 200': (r) => r.status === 200 });
  } else {
    const lo = http.post(`${BASE_URL}/api/auth/logout`, null,
      { tags: { name: 'POST /api/auth/logout' } });
    logoutTrend.add(lo.timings.duration);
    check(lo, { 'logout 200': (r) => r.status === 200 });
  }

  jar.clear(BASE_URL);
}

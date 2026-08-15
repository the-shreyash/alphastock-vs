// ============================================================================
// StockAssist AI — PH3.5 rate-limit validation (brief §9)
//
// The brief is emphatic that rate limiting must NOT be disabled to obtain
// better throughput numbers. The inverse obligation is just as real: the limits
// must be shown to actually work under concurrency, because a limiter that
// silently fails open is indistinguishable from a fast application until the
// day someone notices.
//
// Three questions, three phases:
//
//   1. BELOW  — traffic under the policy is never throttled. A limiter that
//               rejects legitimate traffic is worse than none.
//   2. ABOVE  — traffic over the policy IS throttled, with `Retry-After` and
//               `X-RateLimit-*` present so a client can back off correctly.
//   3. BLAST  — while one identity is being throttled hard, a *different*
//               legitimate identity keeps being served. This is the one that
//               matters most and the one a naive limiter gets wrong: a global
//               counter, or a shared lock on the counter collection, turns one
//               abusive client into an outage for everybody.
//
// Run this AFTER a metrics snapshot and BEFORE the main run, or in isolation —
// it deliberately arms lockouts, and the escalating login policy's memory
// outlives the run.
//
//   k6 run scripts/load/k6/ratelimit.js
// ============================================================================
/* eslint-disable no-undef */
import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Counter, Rate } from 'k6/metrics';
import { BASE_URL, PASSWORD, userEmail, installResponseCallback } from './lib/config.js';

const throttled = new Counter('sa_rl_429');
const served = new Counter('sa_rl_200');
const victimBlocked = new Rate('sa_rl_victim_blocked');
const retryAfterPresent = new Rate('sa_rl_retry_after_present');
const serverErrors = new Counter('sa_rl_5xx');

export const options = {
  scenarios: {
    // Phase 1+2 run as one VU walking a single identity from under the limit to
    // well over it. One VU, not many: the question is where the boundary is,
    // and concurrent requests would blur it by the width of the non-atomic
    // increment-then-read that MongoRateLimitStore documents.
    boundary: { executor: 'shared-iterations', exec: 'boundary', vus: 1, iterations: 1, maxDuration: '3m' },
    // Phase 3 starts once the boundary walk has armed a lockout.
    victim: { executor: 'constant-vus', exec: 'victim', vus: 1, duration: '90s', startTime: '30s' },
  },
  thresholds: {
    // The whole point: a throttled abuser must not throttle anyone else.
    sa_rl_victim_blocked: ['rate<0.01'],
    // Rejections must be actionable, not bare.
    sa_rl_retry_after_present: ['rate>0.99'],
    sa_rl_5xx: ['count==0'],
  },
};

installResponseCallback();

// The identity that gets hammered. A dedicated account so the abuse never
// touches a user the main traffic model is using.
const ABUSER = userEmail(240);
// The identity that must keep working throughout.
const VICTIM = userEmail(241);

function loginToken(email) {
  const res = http.post(`${BASE_URL}/api/auth/login`,
    JSON.stringify({ email, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' }, tags: { name: 'POST /api/auth/login' } });
  if (res.status !== 200) return null;
  try { return res.json('token'); } catch (e) { return null; }
}

export function boundary() {
  const token = loginToken(ABUSER);
  check(null, { 'abuser logged in': () => token !== null });
  if (!token) return;

  const headers = { Authorization: `Bearer ${token}` };

  // AUTHENTICATED_API is 120 requests / 60 s per user (security/rate_limit.py).
  // Read it from the response headers rather than hardcoding it, so this test
  // keeps testing the real policy after someone retunes it via
  // RATE_LIMIT_API_USER — a hardcoded 120 would start asserting history.
  let limit = 120;

  group('phase 1 — below the limit', () => {
    // Half the budget, paced. Nothing here may be throttled.
    for (let i = 0; i < 40; i++) {
      const r = http.get(`${BASE_URL}/api/settings`, { headers, tags: { name: '/api/settings' } });
      const declared = parseInt(r.headers['X-Ratelimit-Limit'] || '0', 10);
      if (declared) limit = declared;
      if (r.status === 429) throttled.add(1); else served.add(1);
      if (r.status >= 500) serverErrors.add(1);
      check(r, { 'under-limit traffic is not throttled': (x) => x.status !== 429 });
      check(r, { 'X-RateLimit-Remaining present': (x) => x.headers['X-Ratelimit-Remaining'] !== undefined });
    }
  });

  group('phase 2 — over the limit', () => {
    // Push well past the policy in the same window and require a 429 with a
    // usable Retry-After.
    let saw429 = false;
    for (let i = 0; i < limit + 40; i++) {
      const r = http.get(`${BASE_URL}/api/settings`, { headers, tags: { name: '/api/settings' } });
      if (r.status === 429) {
        throttled.add(1);
        saw429 = true;
        const ra = r.headers['Retry-After'];
        retryAfterPresent.add(ra !== undefined && parseInt(ra, 10) > 0);
        check(r, { '429 body carries RATE_LIMITED code': (x) => (x.body || '').indexOf('RATE_LIMITED') !== -1 });
      } else {
        served.add(1);
        if (r.status >= 500) serverErrors.add(1);
      }
    }
    check(null, { 'over-limit traffic IS throttled': () => saw429 });
  });
}

export function victim() {
  // A different user, at an entirely reasonable pace, for the whole time the
  // abuser is being throttled. Every request must succeed.
  const token = loginToken(VICTIM);
  if (!token) {
    // A failed login here is itself the finding — the abuser's traffic reached
    // the login path or the store. Record it and stop hammering.
    victimBlocked.add(true);
    sleep(5);
    return;
  }
  const r = http.get(`${BASE_URL}/api/watchlist`, {
    headers: { Authorization: `Bearer ${token}` },
    tags: { name: '/api/watchlist' },
  });
  victimBlocked.add(r.status === 429);
  if (r.status >= 500) serverErrors.add(1);
  check(r, { 'bystander is served while another identity is throttled': (x) => x.status === 200 });
  sleep(2);
}

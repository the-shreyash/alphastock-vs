import axios from "axios";

/**
 * The one axios instance every request in the app passes through.
 *
 * ===========================================================================
 * D6.1 — how the session was made to work at all (kept: still load-bearing)
 * ===========================================================================
 *
 * L1 — `withCredentials: true`. The frontend runs on :3000 and the API on
 *      :8000, so every request is cross-origin. In credentials-omit mode a
 *      browser **ignores `Set-Cookie`** and never sends cookies. Login's
 *      `access_token`, `refresh_token` and `csrf_token` cookies were therefore
 *      never stored, and `POST /api/auth/refresh` — which reads the refresh
 *      token only from the cookie — answered `401 "No refresh token"` every
 *      single time. The backend's rotating-refresh design was correct all
 *      along; nothing was ever able to reach it.
 *
 * L4 — `X-CSRF-Token`. Once cookies flow, a cookie-authenticated mutation is
 *      subject to the CSRF layer, which requires this header. It also had to be
 *      added to the server's CORS `ALLOWED_HEADERS`, or the browser's preflight
 *      would refuse to send it and every mutation would 403.
 *
 * L2 — a single promise-coalescing refresh queue. A dashboard fires many
 *      parallel requests; at expiry they all 401 at once. Request 1 performs
 *      the refresh and requests 2..N await **the same promise**, then all of
 *      them replay once.
 *
 * WHY THE BEARER TOKEN IS STILL SENT. `server.get_current_user` prefers the
 * `access_token` **cookie** and falls back to the `Authorization` header, so
 * once cookies work the cookie is what authenticates and the header is inert.
 * It is kept as the bootstrap credential for the window between login and the
 * first refresh, and as the fallback for a deployment where cookies cannot
 * reach the API. It is dropped on the first successful refresh, after which the
 * SPA is purely cookie-authenticated. `security/csrf.py` exempts
 * Bearer-authenticated requests and enforces on cookie-only ones, so both
 * phases are covered.
 *
 * ===========================================================================
 * D6.2 — SESSION LIFECYCLE HARDENING. Four defects this file now closes.
 * ===========================================================================
 *
 * D6.2-A — **a transient failure was indistinguishable from a dead session.**
 *      `refreshSession()` caught *every* rejection the same way: it latched
 *      `refreshFailed`, announced SESSION_EXPIRED, and never attempted another
 *      refresh for the life of the page. A backend restart, a dropped Wi-Fi
 *      frame, a 502 from a proxy or the refresh endpoint's own 429 rate limit
 *      therefore threw the user onto the login screen and told them their
 *      session had expired — when in fact their perfectly valid 7-day refresh
 *      cookie was sitting in the browser the whole time. Only the server
 *      *answering* "this credential is dead" (401/403) is now definitive;
 *      everything else is transient, does not latch, does not announce, and
 *      leaves a later 401 free to try again after a short cool-down.
 *
 * D6.2-B — **a queued request could replay under a different identity.** The
 *      replay path was "401 → await refresh → re-send the original request",
 *      with nothing tying the request to the identity that issued it. A request
 *      parked on that await while the user signed out and signed in as somebody
 *      else was re-sent afterwards — carrying the *new* user's cookies. For a
 *      GET that renders A's page with B's data; for the mutations this platform
 *      exposes it is an order replayed into the wrong brokerage account. Every
 *      request is now stamped with the auth epoch that was current when it was
 *      dispatched, and a replay whose epoch is stale is abandoned with
 *      `SessionChangedError` instead of being sent.
 *
 * D6.2-C — **an explicit four-state machine at the boundary.** REFRESHING was
 *      not a state anything could observe, so "recovering" and "logged out"
 *      looked identical to the UI for the length of a round trip. The four
 *      states in `SESSION_STATE` are announced on `SESSION_STATE_EVENT` and
 *      `AuthContext` is the single place they become React state.
 *
 * D6.2-D — **a dead session left a live access cookie behind.** When a refresh
 *      is refused because the family was revoked (a logout elsewhere, or reuse
 *      detection firing), the *access* cookie can still be minutes from
 *      expiring — so another tab, or a reload, still looked signed in. A
 *      definitive expiry now best-effort calls `POST /api/auth/logout`, which
 *      clears both cookies through the server's own policy (the only thing that
 *      can: they are HttpOnly and JavaScript cannot touch them).
 */

const API_URL = process.env.REACT_APP_BACKEND_URL;

/** Emitted on `window` when the session is dead and re-authentication is required. */
export const SESSION_EXPIRED_EVENT = "stockassist:session-expired";

/**
 * Emitted on `window` for every session-state transition this client observes,
 * as `detail: { state }`. `AuthContext` is the only intended listener.
 */
export const SESSION_STATE_EVENT = "stockassist:session-state";

/**
 * The four states of an authenticated browser session (D6.2-C).
 *
 * They are deliberately NOT interchangeable, and conflating any pair of them is
 * what produced the symptoms D6.1 and D6.2 were opened for:
 *
 *   AUTHENTICATED    normal API + WebSocket operation.
 *   REFRESHING       a transient recovery is in flight. The credential is stale
 *                    but the session is not known to be over, so the UI must
 *                    NOT appear logged out — this is the state whose absence
 *                    made a routine 15-minute refresh look like a logout.
 *   SESSION_EXPIRED  a refresh was attempted and the server refused it. Stop
 *                    authenticated traffic; require re-authentication. Reached
 *                    only from a definitive (401/403) refusal — never from a
 *                    network error (D6.2-A).
 *   USER_SIGNED_OUT  the user pressed the button. No explanation is owed and
 *                    none should be shown.
 */
export const SESSION_STATE = {
  AUTHENTICATED: "AUTHENTICATED",
  REFRESHING: "REFRESHING",
  SESSION_EXPIRED: "SESSION_EXPIRED",
  USER_SIGNED_OUT: "USER_SIGNED_OUT",
};

/**
 * Rejection produced when a queued request is abandoned because the identity
 * changed underneath it (D6.2-B). A distinct class so callers — and tests — can
 * tell "we deliberately did not send this" apart from a server error.
 */
export class SessionChangedError extends Error {
  constructor(message = "Request abandoned: the session changed before it could be retried") {
    super(message);
    this.name = "SessionChangedError";
    this.code = "SESSION_CHANGED";
  }
}

/** Name of the (deliberately non-HttpOnly) CSRF cookie the server plants. */
const CSRF_COOKIE = "csrf_token";
const CSRF_HEADER = "X-CSRF-Token";

/** Methods that change state and therefore need the CSRF token. */
const MUTATING_METHODS = new Set(["post", "put", "patch", "delete"]);

/**
 * Auth-bootstrap endpoints that must never trigger a refresh.
 *
 * Refreshing in response to these would be circular: `/auth/refresh` failing is
 * the thing we are handling, refreshing a failed login is nonsense, and a 401
 * from `/auth/me` is the *bootstrap probe* — `AuthContext` recovers that one
 * explicitly (see `attemptSilentRefresh`) so that a first-time visitor with no
 * cookies at all is reported as signed out rather than as a session that
 * expired.
 *
 * D6.2 — matched by **exact path**, not `String.includes`. Substring matching
 * silently exempts any future route that happens to contain one of these as a
 * fragment, and an endpoint that quietly stops refreshing is a bug nobody
 * notices until a session dies.
 */
const NEVER_REFRESH = new Set([
  "/auth/me",
  "/auth/refresh",
  "/auth/login",
  "/auth/logout",
  "/auth/logout-all",
  "/auth/register",
  "/auth/google/login-url",
  "/auth/google/session",
]);

/**
 * How long to wait after a *transient* refresh failure before another 401 may
 * start a new refresh (D6.2-A). Without it, a burst of requests against an
 * unreachable backend would each start their own attempt. Short enough that a
 * blip costs the user one extra beat, long enough that an outage cannot turn
 * into a request storm.
 */
const TRANSIENT_REFRESH_COOLDOWN_MS = 5000;

const api = axios.create({
  baseURL: `${API_URL}/api`,
  headers: { "Content-Type": "application/json" },
  // L1. Without this the browser discards the backend's Set-Cookie on every
  // cross-origin response and sends no cookie on any request.
  withCredentials: true,
});

/** Read a cookie by name. Returns null when absent (or when document is not available). */
function readCookie(name) {
  if (typeof document === "undefined" || !document.cookie) return null;
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function emit(name, detail) {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  window.dispatchEvent(new CustomEvent(name, detail === undefined ? undefined : { detail }));
}

function announceState(state) {
  emit(SESSION_STATE_EVENT, { state });
}

// --------------------------------------------------------------------------
// Auth epoch — the identity generation counter (D6.2-B)
// --------------------------------------------------------------------------
// Incremented on every event that changes *who* the browser is: sign-in,
// registration, sign-out, and a session that expired. A request carries the
// epoch that was current when it was dispatched; if that no longer matches at
// replay time, the request belongs to somebody who is no longer here.
let authEpoch = 1;

/** The identity generation currently in force. Exported for tests + the socket. */
export function currentAuthEpoch() {
  return authEpoch;
}

function bumpAuthEpoch() {
  authEpoch += 1;
}

// --------------------------------------------------------------------------
// Refresh coalescing (L2) + failure classification (D6.2-A)
// --------------------------------------------------------------------------
// `refreshPromise` is the single in-flight refresh. Ten simultaneous 401s
// produce ONE POST /api/auth/refresh and ten awaiters of the same promise.
let refreshPromise = null;
// True once the SERVER has definitively refused a refresh. Stops the client
// hammering a backend that has already said no. Cleared by `resetRefreshState`.
let refreshFailed = false;
// When the last *transient* refresh failure happened, for the cool-down.
let lastTransientFailureAt = 0;
// Whether the in-flight refresh should announce SESSION_EXPIRED if it fails.
// Set at creation and raised (never lowered) by a non-silent joiner, so a
// silent bootstrap probe can never suppress a real expiry that a concurrent
// authenticated request discovered.
let refreshAnnounces = false;

function requestPath(url) {
  const raw = String(url || "");
  const withoutQuery = raw.split("?")[0].split("#")[0];
  return withoutQuery
    .replace(/^https?:\/\/[^/]+/i, "") // absolute URL → path
    .replace(/^\/api(?=\/)/, "");      // drop the baseURL prefix if present
}

function isNeverRefresh(url) {
  return NEVER_REFRESH.has(requestPath(url));
}

/**
 * Did the server answer, and did it say the credential itself is finished?
 *
 * D6.2-A. **Only a 401 or 403 from the refresh endpoint is definitive.** Those
 * mean the refresh cookie is absent, expired, revoked, rotated-out, or belongs
 * to a blocked account — states no amount of retrying can change. Everything
 * else is the *transport or the server* failing, not the session:
 *
 *   - no `response` at all — DNS, TCP, TLS, CORS, an aborted request, or the
 *     API simply not running. The commonest of these is a backend restart
 *     during development, which used to sign the developer out.
 *   - 5xx — the server is broken, the credential is not.
 *   - 429 — `security/rate_limit.py` caps refreshes at 20/min per session. A
 *     client that treated its own rate limit as proof of expiry would turn a
 *     brief burst into a forced re-login.
 */
function isDefinitiveRefusal(error) {
  const status = error?.response?.status;
  if (status === undefined) return false;
  return status === 401 || status === 403;
}

/**
 * Best-effort server-side teardown after a definitive expiry (D6.2-D).
 *
 * The access and refresh cookies are HttpOnly, so this is the only way to get
 * rid of them: `POST /api/auth/logout` revokes the session and clears both
 * through `security/cookies.clear_auth_cookies`, whose delete attributes match
 * how they were set. It is CSRF-exempt server-side (`security/csrf.py`), so it
 * works even though our CSRF cookie may already be stale.
 *
 * Failure is ignored on purpose: we are already on the failure path, and the
 * client-side state does not depend on the call succeeding.
 */
function clearServerSession() {
  try {
    api.post("/auth/logout").catch(() => { /* best effort */ });
  } catch { /* best effort */ }
}

/**
 * A refresh was definitively refused.
 *
 * @param {boolean} announce Whether to tell the application the session ended.
 *   False for a silent bootstrap probe, where a refusal usually means "this
 *   visitor was never signed in" — the credential is still discarded and the
 *   latch still set, because the server's answer is just as final either way,
 *   but nobody is told a session ended that may never have existed.
 */
function handleDefinitiveRefusal({ announce }) {
  // The credential is gone; drop the stale bootstrap token so nothing keeps
  // presenting it.
  localStorage.removeItem("token");
  if (!announce) return;
  // Burn the identity so no queued request replays under the next one (D6.2-B),
  // clear the server's cookies (D6.2-D) and tell the app once.
  bumpAuthEpoch();
  clearServerSession();
  announceState(SESSION_STATE.SESSION_EXPIRED);
  emit(SESSION_EXPIRED_EVENT);
}

/**
 * Perform (or join) the single in-flight refresh. Resolves true on success.
 *
 * @param {object}  [options]
 * @param {boolean} [options.silent] When true a definitive refusal does NOT
 *   announce SESSION_EXPIRED. Used for the bootstrap probe, where a refusal
 *   most often means "this visitor was never signed in" rather than "your
 *   session ended" — telling a first-time visitor their session expired is
 *   worse than saying nothing. It still latches `refreshFailed`, because the
 *   server's answer is just as final either way.
 */
export function refreshSession({ silent = false } = {}) {
  if (refreshPromise) {
    // A real (non-silent) caller joining a silent probe upgrades the outcome:
    // there IS an authenticated session behind this refresh after all.
    if (!silent) refreshAnnounces = true;
    return refreshPromise;
  }
  refreshAnnounces = !silent;
  announceState(SESSION_STATE.REFRESHING);
  refreshPromise = api
    .post("/auth/refresh")
    .then(() => {
      refreshFailed = false;
      lastTransientFailureAt = 0;
      // The refresh succeeded, which proves cookies work end to end. The
      // localStorage access token is by now provably stale (its 15-minute life
      // has elapsed — that is why we are here), and from this point the cookie
      // is what authenticates every request. Dropping it converges the SPA onto
      // cookie-only auth, where the CSRF layer actually applies, and takes a
      // long-lived credential out of reach of XSS.
      localStorage.removeItem("token");
      announceState(SESSION_STATE.AUTHENTICATED);
      return true;
    })
    .catch((err) => {
      if (isDefinitiveRefusal(err)) {
        refreshFailed = true;
        handleDefinitiveRefusal({ announce: refreshAnnounces });
      } else {
        // Transient (D6.2-A): the session is NOT known to be over. Do not
        // latch, do not announce an expiry — just stop trying for a moment so
        // an outage cannot become a request storm.
        lastTransientFailureAt = Date.now();
        announceState(SESSION_STATE.AUTHENTICATED);
      }
      throw err;
    })
    .finally(() => {
      refreshPromise = null;
    });
  return refreshPromise;
}

/**
 * A refresh that never announces an expiry. The bootstrap path (`AuthContext`
 * probing `/auth/me` on mount) uses this: a 401 there may equally mean "this is
 * a first visit" as "your session ended", and only the caller knows which
 * story to tell.
 */
export function attemptSilentRefresh() {
  return refreshSession({ silent: true });
}

/** True when a refresh is definitively refused *and* we are inside the cool-down. */
function refreshIsCurrentlyPointless() {
  if (refreshFailed) return true;
  return Date.now() - lastTransientFailureAt < TRANSIENT_REFRESH_COOLDOWN_MS;
}

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  } else if (config.headers?.Authorization) {
    // D6.2. A REPLAYED request still carries the header it was built with, and
    // by replay time the bootstrap token has been dropped (a successful refresh
    // proves cookies work, so the SPA converges onto cookie-only auth). Leaving
    // the stale header on would not break authentication — `get_current_user`
    // prefers the cookie — but `security/csrf.py` exempts any request carrying
    // a Bearer header, so a replayed mutation would quietly skip the CSRF layer
    // on the strength of a credential that no longer works. Drop it.
    if (typeof config.headers.delete === "function") {
      config.headers.delete("Authorization");
    } else {
      delete config.headers.Authorization;
    }
  }
  // L4. Only on mutations — safe methods are CSRF-exempt server-side, and
  // sending the header on a GET would put it in the preflight for no reason.
  if (MUTATING_METHODS.has((config.method || "get").toLowerCase())) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) config.headers[CSRF_HEADER] = csrf;
  }
  // D6.2-B. Stamp the identity generation ONCE, at first dispatch. A replay
  // keeps the epoch it was born with, which is precisely what makes a
  // cross-identity replay detectable.
  if (config._authEpoch === undefined) {
    config._authEpoch = authEpoch;
  }
  return config;
});

api.interceptors.response.use(
  (res) => {
    // D6.3. The epoch check below existed ONLY on the 401 path, so it caught a
    // request that had to be *replayed* across an identity change and missed the
    // one that simply *succeeded* across it. A dashboard fires many reads at
    // once; sign out mid-flight and sign in as somebody else, and every one of
    // those responses — fetched with the previous account's cookie, answered
    // 200 by the server, entirely correct as far as the server is concerned —
    // resolves into the new account's UI.
    //
    // The stamp is applied once at first dispatch, so this compares the identity
    // generation the request was *born* under with the one in force now. It is
    // enforced here, at the boundary, rather than by every caller remembering to
    // re-check after an await: there is one interceptor and hundreds of call
    // sites, and the call sites are exactly where this gets forgotten.
    //
    // Rejected with the same `SessionChangedError` the replay path uses, so
    // "we deliberately dropped this" stays distinguishable from a server error.
    //
    // The auth-lifecycle endpoints are exempt for the same reason they are
    // exempt from the 401 path: those requests ARE the identity transition, so
    // asking whether they outlived one is the wrong question. `/auth/refresh`
    // in particular is issued by `refreshSession`, which would read a rejection
    // as a transient network failure and impose a cool-down on a session that
    // had just been established.
    if (res.config && res.config._authEpoch !== undefined
        && res.config._authEpoch !== authEpoch
        && !isNeverRefresh(res.config.url)) {
      return Promise.reject(new SessionChangedError());
    }
    return res;
  },
  async (error) => {
    const originalRequest = error.config;
    const url = originalRequest?.url || "";

    if (isNeverRefresh(url)) {
      return Promise.reject(error);
    }
    if (error.response?.status !== 401 || originalRequest?._retry) {
      return Promise.reject(error);
    }
    // A dead session, or a transient failure we have already tried very
    // recently. Either way there is nothing to gain from another round trip.
    if (refreshIsCurrentlyPointless()) {
      return Promise.reject(error);
    }
    // D6.2-B. The identity moved on while this request was in flight — abandon
    // it rather than replaying it under whoever is signed in now.
    if (originalRequest._authEpoch !== authEpoch) {
      return Promise.reject(new SessionChangedError());
    }

    // `_retry` is per-request, so a request whose replay 401s again gives up
    // rather than spinning — an endpoint that 401s for a reason refresh cannot
    // fix (a blocked account, say) must not become an infinite loop.
    originalRequest._retry = true;
    try {
      await refreshSession();
    } catch {
      return Promise.reject(error);
    }
    // Re-check after the await: sign-out, sign-in and expiry are all things
    // that can happen during a round trip, and this is the moment the request
    // would otherwise be re-sent with somebody else's cookies.
    if (originalRequest._authEpoch !== authEpoch) {
      return Promise.reject(new SessionChangedError());
    }
    return api(originalRequest);
  },
);

/** True once a refresh has been definitively refused and the session is known dead. */
export function sessionIsDead() {
  return refreshFailed;
}

/**
 * Re-arm the refresh machinery and start a new identity generation.
 *
 * Called by `AuthContext` on every sign-in, registration, OAuth adoption and
 * sign-out. The epoch bump is the part that matters beyond re-arming: it
 * invalidates every request that was queued under the previous identity
 * (D6.2-B).
 */
export function resetRefreshState() {
  refreshFailed = false;
  refreshPromise = null;
  refreshAnnounces = false;
  lastTransientFailureAt = 0;
  bumpAuthEpoch();
}

/** Announce a deliberate sign-out. Distinct from an expiry, and never conflated. */
export function announceSignedOut() {
  announceState(SESSION_STATE.USER_SIGNED_OUT);
}

/** Announce that an authenticated session is established and healthy. */
export function announceAuthenticated() {
  announceState(SESSION_STATE.AUTHENTICATED);
}

export default api;

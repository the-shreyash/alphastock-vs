import axios from "axios";

/**
 * The one axios instance every request in the app passes through.
 *
 * D6.1 — SESSION LIFECYCLE (D6-L1 … D6-L4). This file held the root cause of
 * the session dying fifteen minutes into every visit, and the fix is four
 * changes that only work together:
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
 *      would refuse to send it and every mutation would 403. Fixing L1 without
 *      L4 trades one broken app for another, which is why they are one change.
 *
 * L2 — a single promise-coalescing refresh queue. A dashboard fires many
 *      parallel requests; at expiry they all 401 at once. This used to be
 *      `if (!isRefreshing)`, so exactly one request attempted recovery and the
 *      rest fell through and rejected permanently — even when the refresh
 *      succeeded. Now request 1 performs the refresh and requests 2..N await
 *      **the same promise**, then all of them replay once.
 *
 * L3/L10 — an explicit session-expired signal. The old code set a module-level
 *      `refreshFailed` latch and rejected silently: no 401 for the remaining
 *      life of the page ever attempted a refresh again, and nothing on screen
 *      said why. A dead session now emits `SESSION_EXPIRED_EVENT`, which
 *      `AuthContext` turns into a distinct state — a session that expired is
 *      not a user who signed out, and the two must not look alike.
 *
 * WHY THE BEARER TOKEN IS STILL SENT. `server.get_current_user` prefers the
 * `access_token` **cookie** and falls back to the `Authorization` header, so
 * once cookies work the cookie is what authenticates and the header is inert.
 * It is kept as the bootstrap credential for the window between login and the
 * first refresh, and as the fallback for a deployment where cookies cannot
 * reach the API. It is dropped on the first successful refresh (see below),
 * after which the SPA is purely cookie-authenticated.
 *
 * On CSRF: `security/csrf.py` exempts Bearer-authenticated requests (a
 * cross-site attacker cannot set an `Authorization` header) and enforces on
 * cookie-only ones. Both of this client's phases are therefore covered, and a
 * cross-site forgery — which carries the ambient cookie but neither header — is
 * rejected in both.
 */

const API_URL = process.env.REACT_APP_BACKEND_URL;

/** Emitted on `window` when the session is dead and re-authentication is required. */
export const SESSION_EXPIRED_EVENT = "stockassist:session-expired";

/** Name of the (deliberately non-HttpOnly) CSRF cookie the server plants. */
const CSRF_COOKIE = "csrf_token";
const CSRF_HEADER = "X-CSRF-Token";

/** Methods that change state and therefore need the CSRF token. */
const MUTATING_METHODS = new Set(["post", "put", "patch", "delete"]);

/**
 * Auth-bootstrap endpoints that must never trigger a refresh.
 *
 * Refreshing in response to these would be circular: a 401 from `/auth/me` IS
 * the signed-out signal, `/auth/refresh` failing is the thing we are handling,
 * and refreshing a failed login is nonsense.
 */
const NEVER_REFRESH = [
  "/auth/me",
  "/auth/refresh",
  "/auth/login",
  "/auth/register",
  "/auth/google",
];

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

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  // L4. Only on mutations — safe methods are CSRF-exempt server-side, and
  // sending the header on a GET would put it in the preflight for no reason.
  if (MUTATING_METHODS.has((config.method || "get").toLowerCase())) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) config.headers[CSRF_HEADER] = csrf;
  }
  return config;
});

// --------------------------------------------------------------------------
// Refresh coalescing (L2)
// --------------------------------------------------------------------------
// `refreshPromise` is the single in-flight refresh. Ten simultaneous 401s
// produce ONE POST /api/auth/refresh and ten awaiters of the same promise.
let refreshPromise = null;
// Set once a refresh has definitively failed. Stops the client hammering a
// backend that has already said no, and is cleared by `resetRefreshState()` on
// a fresh sign-in.
let refreshFailed = false;

function isNeverRefresh(url) {
  return NEVER_REFRESH.some((path) => (url || "").includes(path));
}

function announceSessionExpired() {
  // The credential is gone; drop the stale bootstrap token so nothing keeps
  // presenting it.
  localStorage.removeItem("token");
  if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
    window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
  }
}

/**
 * Perform (or join) the single in-flight refresh. Resolves true on success.
 *
 * Every caller of a failed refresh sees `refreshFailed` set and the
 * session-expired event fired exactly once, because the failure is handled
 * where the promise is created rather than by each awaiter.
 */
export function refreshSession() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = api
    .post("/auth/refresh")
    .then(() => {
      refreshFailed = false;
      // The refresh succeeded, which proves cookies work end to end. The
      // localStorage access token is by now provably stale (its 15-minute life
      // has elapsed — that is why we are here), and from this point the cookie
      // is what authenticates every request. Dropping it converges the SPA onto
      // cookie-only auth, where the CSRF layer actually applies, and takes a
      // long-lived credential out of reach of XSS.
      localStorage.removeItem("token");
      return true;
    })
    .catch((err) => {
      refreshFailed = true;
      announceSessionExpired();
      throw err;
    })
    .finally(() => {
      refreshPromise = null;
    });
  return refreshPromise;
}

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const originalRequest = error.config;
    const url = originalRequest?.url || "";

    if (isNeverRefresh(url)) {
      return Promise.reject(error);
    }
    if (error.response?.status !== 401 || originalRequest?._retry || refreshFailed) {
      return Promise.reject(error);
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
    return api(originalRequest);
  },
);

/** True once a refresh has failed and the session is known dead. */
export function sessionIsDead() {
  return refreshFailed;
}

/** Re-arm the refresh machinery. Called by AuthContext on a fresh sign-in. */
export function resetRefreshState() {
  refreshFailed = false;
  refreshPromise = null;
}

export default api;

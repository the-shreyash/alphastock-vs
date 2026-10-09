import { createContext, useContext, useState, useEffect, useCallback, useRef } from "react";
import api, {
  announceAuthenticated,
  announceSignedOut,
  attemptSilentRefresh,
  resetRefreshState,
  SESSION_EXPIRED_EVENT,
  SESSION_STATE,
  SESSION_STATE_EVENT,
} from "../services/api";
import { useRealtimeStore } from "../store/realtimeStore";
import { clearTenantLocalState } from "../lib/tenantState";

const AuthContext = createContext(null);

/**
 * How this session ended. `null` while signed in or still checking.
 *
 * D6.1 / L10. These are NOT the same event and must never render the same way:
 *
 *   SESSION_EXPIRED  — the credential aged out or was revoked. The user did
 *                      nothing; they should be told so and offered a sign-in,
 *                      not silently dumped on a login page as though they had
 *                      asked to leave.
 *   USER_SIGNED_OUT  — the user pressed the button. No explanation is owed and
 *                      none should be shown.
 *
 * Before D6.1 both produced `setUser(false)` and nothing else, so the reported
 * symptom ("it just logs me out") was indistinguishable from a deliberate
 * logout in the UI, in the logs, and to the person reporting it.
 */
export const SESSION_END = {
  EXPIRED: "SESSION_EXPIRED",
  SIGNED_OUT: "USER_SIGNED_OUT",
};

export { SESSION_STATE };

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null = checking
  const [loading, setLoading] = useState(true);
  const [sessionEnd, setSessionEnd] = useState(null);
  // D6.2-C. The authoritative four-state session machine. `sessionEnd` is the
  // narrower "how did it finish" signal the UI already consumed; this is the
  // full state, including the REFRESHING transition that has no `sessionEnd`.
  const [sessionState, setSessionState] = useState(SESSION_STATE.AUTHENTICATED);
  const hasChecked = useRef(false);

  /**
   * Resolve the signed-in user for this page load.
   *
   * D6.2 — BOOTSTRAP RECOVERY. This used to be a single `GET /auth/me`, and a
   * 401 meant "signed out", full stop. But the access token lives 15 minutes
   * and the refresh cookie lives seven days, so **reloading the tab any time
   * after the first quarter of an hour signed the user out** — with a valid
   * refresh cookie sitting unused in the browser. That is the original
   * "my session keeps dying" report in its purest form, and D6.1's interceptor
   * could not fix it because `/auth/me` is (correctly) exempt from the
   * automatic refresh path.
   *
   * So the recovery is explicit and bounded: probe once, and on a 401 attempt
   * exactly one **silent** refresh before probing again. Silent because a 401
   * here is ambiguous — a first-time visitor has no cookies either — and
   * telling somebody who never signed in that their session expired is worse
   * than saying nothing. A visitor who was genuinely never authenticated ends
   * up at `user === false` with `sessionEnd === null`, which is exactly the
   * signed-out state the app has always rendered.
   */
  const checkAuth = useCallback(async () => {
    const probe = () => api.get("/auth/me");
    try {
      const { data } = await probe();
      setUser(data);
      setSessionEnd(null);
      setSessionState(SESSION_STATE.AUTHENTICATED);
      return data;
    } catch (first) {
      if (first?.response?.status !== 401) {
        // Not an authentication answer — the API is unreachable or broken. Do
        // not manufacture a session verdict out of a transport failure
        // (D6.2-A); report signed-out for this render without claiming the
        // session expired.
        setUser(false);
        return null;
      }
      try {
        await attemptSilentRefresh();
      } catch {
        setUser(false);
        return null;
      }
      try {
        const { data } = await probe();
        setUser(data);
        setSessionEnd(null);
        setSessionState(SESSION_STATE.AUTHENTICATED);
        return data;
      } catch {
        setUser(false);
        return null;
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // If we are on the Google OAuth callback route, let AuthCallback perform the
    // code+state exchange and establish the session before we probe /me.
    if (window.location.pathname.startsWith('/auth/google/callback')) {
      setLoading(false);
      return;
    }
    // Only check auth ONCE on mount
    if (hasChecked.current) return;
    hasChecked.current = true;
    checkAuth();
  }, [checkAuth]);

  // D6.1 / L3+L10. The api client owns the refresh queue and knows, definitively,
  // when the session is dead — including when the discovery came from a
  // background poll or from the WebSocket's re-auth attempt rather than from a
  // user action. It announces that once; this is the single place the app turns
  // it into signed-out state.
  useEffect(() => {
    const onExpired = () => {
      setUser(false);
      setSessionEnd(SESSION_END.EXPIRED);
      setSessionState(SESSION_STATE.SESSION_EXPIRED);
      setLoading(false);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  // D6.2-C. Transitions the api client observes but that are not session
  // *endings*: REFRESHING while a recovery is in flight, and AUTHENTICATED when
  // it succeeds. `user` is deliberately untouched here — a refresh must never
  // make the UI flash a logged-out state for the length of a round trip.
  useEffect(() => {
    const onState = (event) => {
      const next = event?.detail?.state;
      if (next !== SESSION_STATE.REFRESHING && next !== SESSION_STATE.AUTHENTICATED) {
        return; // expiry and sign-out are owned by the handlers above/below
      }
      setSessionState((current) => {
        // Never resurrect a finished session: once it is expired or signed out,
        // only a fresh sign-in moves it forward.
        if (current === SESSION_STATE.SESSION_EXPIRED
            || current === SESSION_STATE.USER_SIGNED_OUT) {
          return current;
        }
        return next;
      });
    };
    window.addEventListener(SESSION_STATE_EVENT, onState);
    return () => window.removeEventListener(SESSION_STATE_EVENT, onState);
  }, []);

  /** State every successful sign-in converges on, whatever the mechanism. */
  const adoptUser = useCallback((data) => {
    setUser(data);
    setSessionEnd(null);
    setSessionState(SESSION_STATE.AUTHENTICATED);
    announceAuthenticated();
  }, []);

  const login = async (email, password) => {
    // D6.1 / S8 + D6.2-B. `resetRefreshState` re-arms the refresh queue AND
    // starts a new identity generation, so any request still queued under the
    // previous account can never replay under this one. The realtime store is a
    // module singleton that outlives an identity change in the same tab, so
    // A -> logout -> B login left A's portfolio, broker status, orders, ticks,
    // trades, alerts and unread badge on screen until fresh events happened to
    // overwrite them. Clearing on the way IN as well as the way OUT means a
    // stale account's data cannot survive either transition, including a login
    // that follows a crash or a reload where no logout ever ran.
    resetRefreshState();
    useRealtimeStore.getState().reset();
    // D6.3. The store lives in memory; `localStorage` outlives the tab. The
    // previous account's browsing history was still on disk here — see
    // `lib/tenantState`. Cleared BEFORE the new token is written.
    clearTenantLocalState();
    const { data } = await api.post("/auth/login", { email, password });
    if (data.token) localStorage.setItem("token", data.token);
    adoptUser(data);
    return data;
  };

  const register = async (name, email, password) => {
    resetRefreshState();
    useRealtimeStore.getState().reset();
    clearTenantLocalState();
    const { data } = await api.post("/auth/register", { name, email, password });
    if (data.token) localStorage.setItem("token", data.token);
    adoptUser(data);
    return data;
  };

  /**
   * Adopt a session established outside the login form — today, the Google
   * OAuth code exchange (D6.2 / E).
   *
   * `AuthCallback` used to store the token and call `checkAuth()` directly,
   * which skipped BOTH halves of an identity transition: the refresh queue was
   * never re-armed (so a `refreshFailed` latch left over from the previous
   * account's expiry would have kept the new session from ever refreshing) and
   * the realtime store was never reset (so the previous account's portfolio,
   * orders and broker state were still on screen). Signing in is signing in,
   * whichever button was pressed.
   */
  const adoptSession = useCallback(async (token) => {
    resetRefreshState();
    useRealtimeStore.getState().reset();
    clearTenantLocalState();
    if (token) localStorage.setItem("token", token);
    const data = await checkAuth();
    if (data) announceAuthenticated();
    return data;
  }, [checkAuth]);

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch { /* ignore */ }
    localStorage.removeItem("token");
    useRealtimeStore.getState().reset();
    clearTenantLocalState();
    resetRefreshState();
    setUser(false);
    setSessionEnd(SESSION_END.SIGNED_OUT);
    setSessionState(SESSION_STATE.USER_SIGNED_OUT);
    announceSignedOut();
  };

  return (
    <AuthContext.Provider value={{
      user, loading, sessionEnd, sessionState, login, register, logout, checkAuth,
      adoptSession,
      sessionExpired: sessionEnd === SESSION_END.EXPIRED,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
}

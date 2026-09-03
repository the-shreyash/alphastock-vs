import { createContext, useContext, useState, useEffect, useCallback, useRef } from "react";
import api, { resetRefreshState, SESSION_EXPIRED_EVENT } from "../services/api";
import { useRealtimeStore } from "../store/realtimeStore";

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

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null = checking
  const [loading, setLoading] = useState(true);
  const [sessionEnd, setSessionEnd] = useState(null);
  const hasChecked = useRef(false);

  const checkAuth = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me");
      setUser(data);
      setSessionEnd(null);
    } catch {
      setUser(false);
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
      setLoading(false);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  const login = async (email, password) => {
    resetRefreshState();
    // D6.1 / S8. The realtime store is a module singleton that outlives an
    // identity change in the same tab, so A -> logout -> B login left A's
    // portfolio, broker status, orders, ticks, trades, alerts and unread badge
    // on screen until fresh events happened to overwrite them. Clearing on the
    // way IN as well as the way OUT means a stale account's data cannot survive
    // either transition, including a login that follows a crash or a reload
    // where no logout ever ran.
    useRealtimeStore.getState().reset();
    const { data } = await api.post("/auth/login", { email, password });
    if (data.token) localStorage.setItem("token", data.token);
    setUser(data);
    setSessionEnd(null);
    return data;
  };

  const register = async (name, email, password) => {
    resetRefreshState();
    useRealtimeStore.getState().reset();
    const { data } = await api.post("/auth/register", { name, email, password });
    if (data.token) localStorage.setItem("token", data.token);
    setUser(data);
    setSessionEnd(null);
    return data;
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch { /* ignore */ }
    localStorage.removeItem("token");
    useRealtimeStore.getState().reset();
    resetRefreshState();
    setUser(false);
    setSessionEnd(SESSION_END.SIGNED_OUT);
  };

  return (
    <AuthContext.Provider value={{
      user, loading, sessionEnd, login, register, logout, checkAuth,
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

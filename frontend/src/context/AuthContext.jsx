import { createContext, useContext, useState, useEffect, useCallback, useRef } from "react";
import api, { resetRefreshState } from "../services/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null = checking
  const [loading, setLoading] = useState(true);
  const hasChecked = useRef(false);

  const checkAuth = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me");
      setUser(data);
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

  const login = async (email, password) => {
    resetRefreshState();
    const { data } = await api.post("/auth/login", { email, password });
    if (data.token) localStorage.setItem("token", data.token);
    setUser(data);
    return data;
  };

  const register = async (name, email, password) => {
    resetRefreshState();
    const { data } = await api.post("/auth/register", { name, email, password });
    if (data.token) localStorage.setItem("token", data.token);
    setUser(data);
    return data;
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch { /* ignore */ }
    localStorage.removeItem("token");
    setUser(false);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, checkAuth }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
}

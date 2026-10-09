import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import api from "../services/api";
import { useAuth } from "../context/AuthContext";

// REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH

export default function AuthCallback() {
  const hasProcessed = useRef(false);
  const navigate = useNavigate();
  const { adoptSession } = useAuth();

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const processSession = async () => {
      const searchParams = new URLSearchParams(window.location.search);
      const code = searchParams.get("code");
      const state = searchParams.get("state");

      if (!code || !state) {
        navigate("/login", { replace: true });
        return;
      }

      try {
        const { data } = await api.post("/auth/google/session", {
          code: code,
          state: state,
          redirect_uri: window.location.origin + "/auth/google/callback"
        }, { withCredentials: true });
        // D6.2 / E. `adoptSession` is the single sign-in convergence point:
        // it re-arms the refresh queue, starts a new identity generation and
        // resets the realtime store before resolving the user. Storing the
        // token and calling `checkAuth()` directly (what this did) skipped all
        // three, so a Google sign-in after another account's session had
        // expired inherited that account's dead refresh latch and its live
        // dashboard state.
        await adoptSession(data.token);
        navigate("/", { replace: true });
      } catch (err) {
        console.error("Google auth error:", err);
        navigate("/login", { replace: true });
      }
    };

    processSession();
  }, [navigate, adoptSession]);

  return (
    <div className="min-h-screen bg-[#080808] flex items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 border-zinc-700 border-t-white rounded-full animate-spin" />
        <span className="text-xs text-zinc-600 font-mono uppercase tracking-widest">Authenticating with Google...</span>
      </div>
    </div>
  );
}

import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import api from "../services/api";
import { useAuth } from "../context/AuthContext";

// REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH

export default function AuthCallback() {
  const hasProcessed = useRef(false);
  const navigate = useNavigate();
  const { checkAuth } = useAuth();

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const processSession = async () => {
      const searchParams = new URLSearchParams(window.location.search);
      const code = searchParams.get("code");

      const hash = window.location.hash;
      const match = hash.match(/session_id=([^&]+)/);
      const sessionId = match ? match[1] : null;

      if (!code && !sessionId) {
        navigate("/login", { replace: true });
        return;
      }

      try {
        const { data } = await api.post("/auth/google/session", {
          session_id: sessionId,
          code: code,
          redirect_uri: window.location.origin + "/auth/google/callback"
        });
        if (data.token) {
          localStorage.setItem("token", data.token);
        }
        await checkAuth();
        navigate("/", { replace: true });
      } catch (err) {
        console.error("Google auth error:", err);
        navigate("/login", { replace: true });
      }
    };

    processSession();
  }, [navigate, checkAuth]);

  return (
    <div className="min-h-screen bg-[#080808] flex items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 border-zinc-700 border-t-white rounded-full animate-spin" />
        <span className="text-xs text-zinc-600 font-mono uppercase tracking-widest">Authenticating with Google...</span>
      </div>
    </div>
  );
}

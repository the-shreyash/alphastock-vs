import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import api from "../services/api";

// Kite Connect redirects the browser here after login/OTP with
// ?request_token=...&status=success. The token is exchanged server-side
// (API secret never reaches the browser) and the user lands on Settings.
export default function BrokerCallback() {
  const hasProcessed = useRef(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const exchange = async () => {
      const params = new URLSearchParams(window.location.search);
      const requestToken = params.get("request_token");
      const status = params.get("status");

      if (!requestToken || status === "error") {
        navigate("/settings?zerodha=failed", { replace: true });
        return;
      }

      try {
        const { data } = await api.post("/zerodha/session", { request_token: requestToken });
        navigate(data.success ? "/settings?zerodha=connected" : "/settings?zerodha=failed", { replace: true });
      } catch (err) {
        if (err?.response?.status === 401) {
          navigate("/login", { replace: true });
          return;
        }
        navigate("/settings?zerodha=failed", { replace: true });
      }
    };

    exchange();
  }, [navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg)" }} data-testid="broker-callback-page">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 rounded-full animate-spin" style={{ borderColor: "var(--border)", borderTopColor: "var(--ai-accent)" }} />
        <span className="caption font-mono uppercase tracking-widest">Connecting Zerodha account...</span>
      </div>
    </div>
  );
}

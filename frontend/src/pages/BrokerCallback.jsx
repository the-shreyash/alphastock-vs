import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import brokerService from "../services/brokerService";

// Broker OAuth redirect landing page (frontend-side flow).
//
// Zerodha (Kite Connect) redirects here with ?request_token=...&status=success.
// Upstox redirects here with ?code=... (when the app's redirect URL points at
// the frontend instead of the backend /api/brokers/upstox/callback).
//
// The token/code is exchanged server-side — API secrets never reach the
// browser — and the user lands back on Settings with the outcome.
export default function BrokerCallback() {
  const hasProcessed = useRef(false);
  const navigate = useNavigate();
  const [broker, setBroker] = useState("broker");

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const exchange = async () => {
      const params = new URLSearchParams(window.location.search);
      const requestToken = params.get("request_token"); // Zerodha
      const code = params.get("code");                  // Upstox
      const status = params.get("status");

      let name, payload;
      if (requestToken) {
        name = "zerodha";
        payload = { request_token: requestToken };
      } else if (code) {
        name = "upstox";
        payload = { code };
      }
      setBroker(name || "broker");

      if (!name || status === "error" || params.get("error")) {
        navigate(name ? `/settings?broker=${name}&status=failed` : "/settings", { replace: true });
        return;
      }

      try {
        const data = await brokerService.exchangeSession(name, payload);
        navigate(
          data.success ? `/settings?broker=${name}&status=connected` : `/settings?broker=${name}&status=failed`,
          { replace: true }
        );
      } catch (err) {
        if (err?.response?.status === 401) {
          navigate("/login", { replace: true });
          return;
        }
        const detail = err?.response?.data?.detail;
        navigate(
          `/settings?broker=${name}&status=failed${detail ? `&error=${encodeURIComponent(detail)}` : ""}`,
          { replace: true }
        );
      }
    };

    exchange();
  }, [navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg)" }} data-testid="broker-callback-page">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 rounded-full animate-spin" style={{ borderColor: "var(--border)", borderTopColor: "var(--ai-accent)" }} />
        <span className="caption font-mono uppercase tracking-widest">
          Connecting {broker.charAt(0).toUpperCase() + broker.slice(1)} account...
        </span>
      </div>
    </div>
  );
}

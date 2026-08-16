import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Wifi, CheckCircle, XCircle, MinusCircle, AlertTriangle } from "lucide-react";
import adminService from "../../services/adminService";
import { MetricValue } from "../../components/ui/Unavailable";

const TYPE_COLORS = { market_data: "#6366F1", ai: "#8B5CF6", news: "#06B6D4", broker: "#F59E0B", notification: "#EC4899" };

/** `no_traffic` and `not_measured` are grey. Neither is a green light: one means
 *  nobody has called this provider since the process started, the other means
 *  the platform is not watching it at all. Painting either green is exactly the
 *  defect this page had — it reported a healthy platform during a total outage. */
const STATUS = {
  online: { color: "#00D68F", icon: CheckCircle, label: "Online" },
  degraded: { color: "#F59E0B", icon: AlertTriangle, label: "Degraded" },
  offline: { color: "#FF6B6B", icon: XCircle, label: "Offline" },
  no_traffic: { color: "#6B7280", icon: MinusCircle, label: "No traffic" },
  not_measured: { color: "#6B7280", icon: MinusCircle, label: "Not measured" },
};

const OVERALL = {
  operational: { color: "#00D68F", icon: CheckCircle },
  degraded: { color: "#FF6B6B", icon: AlertTriangle },
  no_traffic: { color: "#6B7280", icon: MinusCircle },
};

/**
 * PH3.9 — external integration health, from real probes and counters.
 *
 * This entire page used to be a hardcoded list: `status` meant "a credential is
 * configured" and was never probed, `latency_ms` / `requests_today` /
 * `failure_rate` were literals, and `overall_status` was the constant
 * `"healthy"`.
 *
 * **The rows changed too, which is the substantive part.** The old table listed
 * *vendors* — Yahoo Finance, Alpha Vantage — with individual latencies, and
 * those numbers can never be sourced honestly: the Market Gateway's Source
 * Manager picks an upstream per request and that choice is deliberately
 * invisible above the gateway (MARKET_DATA_ARCHITECTURE.md). It also listed
 * Razorpay, an integration that does not exist anywhere in this codebase.
 *
 * `configured` survives as its own column. It is a real and useful fact; what
 * it is not is evidence the service works, and conflating the two was the
 * original defect.
 */
export default function AdminAPIs() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    adminService.getAPIHealth()
      .then(r => { setData(r.data); setLoading(false); })
      .catch(() => { setError("API health could not be loaded."); setLoading(false); });
  }, []);

  if (loading) return <div className="space-y-6"><div className="h-8 w-48 rounded-lg animate-pulse" style={{ background: "var(--border)" }} /><div className="grid grid-cols-2 gap-4">{Array.from({length:6}).map((_,i)=><div key={i} className="h-36 rounded-2xl animate-pulse" style={{background:"var(--bg-surface)"}}/>)}</div></div>;

  if (error) {
    return (
      <div className="space-y-6">
        <h1 className="page-title">API Health</h1>
        {/* A failed load must not render as "everything is offline" — that is a
            claim about the providers, and we did not make an observation. */}
        <div role="alert" className="p-5 rounded-2xl text-sm"
             style={{ background: "var(--bg-card-glass)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          {error}{" "}
          <button onClick={() => window.location.reload()} className="underline" style={{ color: "var(--ai-accent)" }}>Retry</button>
        </div>
      </div>
    );
  }

  const overall = OVERALL[data?.overall_status] || OVERALL.no_traffic;
  const OverallIcon = overall.icon;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">API Health</h1>
          <p className="page-subtitle mt-1">External service status, from live probes and request counters</p>
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl" style={{ background: `${overall.color}15` }}>
          <OverallIcon size={16} style={{ color: overall.color }} />
          <span className="text-xs font-semibold capitalize" style={{ color: overall.color }}>
            {String(data?.overall_status || "unknown").replace(/_/g, " ")}
          </span>
        </div>
      </div>

      {data?.provider_granularity && (
        <p role="note" className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          {data.provider_granularity}
        </p>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {(data?.apis || []).map((api, i) => {
          const color = TYPE_COLORS[api.type] || "#6B7280";
          const state = STATUS[api.status] || STATUS.not_measured;
          return (
            <motion.div
              key={api.name} initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
              className="p-5 rounded-2xl transition-all duration-300"
              style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)", boxShadow: "var(--card-shadow)" }}
            >
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2.5">
                  <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}>
                    <Wifi size={16} style={{ color }} />
                  </div>
                  <div>
                    <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{api.name}</h4>
                    <span className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>
                      {api.type.replace("_", " ")}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-1 px-2 py-0.5 rounded-full" style={{ background: `${state.color}15` }} title={api.note}>
                  <div className="w-1.5 h-1.5 rounded-full" style={{ background: state.color }} />
                  <span className="text-[10px] font-semibold" style={{ color: state.color }}>{state.label}</span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 mt-4">
                <Field label="p95 latency" value={api.p95_latency_ms} format={v => `≤ ${v}ms`}
                       reason="No calls observed since this process started." />
                <Field label="Requests" value={api.requests_since_start}
                       reason={api.note || "This integration is not instrumented."} />
                <Field label="Error rate" value={api.error_rate_pct} format={v => `${v}%`}
                       color={api.error_rate_pct > 0 ? "#FF6B6B" : undefined}
                       reason="No calls observed since this process started." />
                {/* An `empty` outcome is a call that succeeded and returned
                    nothing usable — the failure mode a status-code check misses
                    entirely, which is why it has its own field. */}
                <Field label="Empty responses" value={api.empty_rate_pct} format={v => `${v}%`}
                       color={api.empty_rate_pct > 0 ? "#F59E0B" : undefined}
                       reason="No calls observed since this process started." />
              </div>

              <div className="mt-3 pt-3 text-[10px]" style={{ borderTop: "1px solid var(--border-subtle)", color: "var(--text-muted)" }}>
                Credentials: {api.configured === true ? "configured"
                  : api.configured === false ? "not configured" : "not checked"}
              </div>
            </motion.div>
          );
        })}
      </div>

      {data?.scope?.note && (
        <p role="note" className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          {data.scope.note}
        </p>
      )}
    </div>
  );
}

function Field({ label, value, format, color, reason }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>{label}</div>
      <div className="text-sm font-mono font-semibold mt-0.5" style={{ color: color || "var(--text-primary)" }}>
        <MetricValue value={value} reason={reason}
                     format={format || (v => (typeof v === "number" ? v.toLocaleString() : v))} />
      </div>
    </div>
  );
}

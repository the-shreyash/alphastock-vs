import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Brain, Zap, DollarSign, AlertTriangle, Clock } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import adminService from "../../services/adminService";
import { MetricValue } from "../../components/ui/Unavailable";

/**
 * PH3.9 — AI monitoring, from real instruments instead of literals.
 *
 * What this page used to show: `latency_ms` 1200 / 900, `failures: 0` and
 * `fallbacks: 0`, all hardcoded — sitting beside counters PH3.7 had already
 * shipped, so an operator watching this page could not see an outage the
 * platform was measuring. Per-provider request counts were the stored chat
 * message count halved and split 50/50 regardless of which provider served the
 * request, and cost was a flat per-message rate against per-token billing.
 *
 * Two labelling rules the backend enforces and this page must not undo:
 *
 * * Counters are **process-scoped** — they reset on restart and cover one
 *   worker. Every counter-derived figure is labelled "since restart", never
 *   "today", and the scope caveat is printed on the page rather than buried.
 * * Latency is a **p95 bucket bound**, not a mean, and reads "—" when nothing
 *   has been observed. `0ms` would say "instantaneous", the opposite of "we
 *   have not measured a single call".
 */
export default function AdminAI() {
  const [status, setStatus] = useState(null);
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([adminService.getAIStatus(), adminService.getAIUsage()])
      .then(([s, u]) => { setStatus(s.data); setUsage(u.data); setLoading(false); })
      .catch(() => { setError("AI monitoring could not be loaded."); setLoading(false); });
  }, []);

  if (loading) return <div className="space-y-6"><div className="h-8 w-48 rounded-lg animate-pulse" style={{ background: "var(--border)" }} /><div className="grid grid-cols-2 gap-4">{Array.from({length:2}).map((_,i)=><div key={i} className="h-48 rounded-2xl animate-pulse" style={{background:"var(--bg-surface)"}}/>)}</div></div>;

  if (error) {
    return (
      <div className="space-y-6">
        <h1 className="page-title">AI Monitoring</h1>
        <div role="alert" className="p-5 rounded-2xl text-sm"
             style={{ background: "var(--bg-card-glass)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          {error}{" "}
          <button onClick={() => window.location.reload()} className="underline" style={{ color: "var(--ai-accent)" }}>Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">AI Monitoring</h1>
        <p className="page-subtitle mt-1">Model status and usage</p>
      </div>

      {/* Summary. Chat messages are durable and survive restarts; provider
          fallbacks come from an in-process counter and do not. Labelled apart
          rather than added together. */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <SummaryCard icon={Zap} label="Chat messages today" value={status?.chat_messages_today} color="#F59E0B" />
        <SummaryCard icon={Brain} label="Chat messages (all time)" value={status?.chat_messages_total} color="#6366F1" />
        <SummaryCard icon={AlertTriangle} label="Fallbacks since restart"
                     value={status?.fallbacks_since_start} color="#EC4899"
                     hint={status?.fallback_note} />
      </div>

      {status?.scope?.note && (
        <p role="note" className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          {status.scope.note}
        </p>
      )}

      {/* Provider Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {(status?.providers || []).map((p, i) => (
          <motion.div
            key={p.name} initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.1 }}
            className="p-5 rounded-2xl"
            style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}
          >
            <div className="flex items-start justify-between mb-4">
              <div>
                <h3 className="card-title">{p.name}</h3>
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                  {p.configured ? "Credentials configured" : "Not configured"}
                </span>
              </div>
              <StatusBadge status={p.status} />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
              <Metric label="p95 latency" icon={Clock} value={p.p95_latency_ms}
                      format={v => `≤ ${v}ms`}
                      reason="No calls have been observed since this process started." />
              <Metric label="Requests since restart" icon={Zap} value={p.requests_since_start} />
              <Metric label="Failures since restart" icon={AlertTriangle}
                      value={p.failures_since_start} />
            </div>
          </motion.div>
        ))}
      </div>

      {/* Top AI Users */}
      <motion.div
        initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}
        className="p-5 rounded-2xl"
        style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}
      >
        <h3 className="card-title mb-4">Top AI Users</h3>
        {(usage?.top_users || []).length === 0 ? (
          <div className="text-sm py-8 text-center" style={{ color: "var(--text-muted)" }}>No AI usage data yet</div>
        ) : (
          <>
            <div className="h-48 mb-4">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={(usage?.top_users || []).slice(0, 8)} layout="vertical">
                  <XAxis type="number" tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fill: "var(--text-secondary)" }} width={100} />
                  <Tooltip contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", borderRadius: 12, fontSize: 12 }} />
                  <Bar dataKey="message_count" fill="#818CF8" radius={[0, 6, 6, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="overflow-x-auto">
              {/* PH3.9: the "Est. Cost" column is gone. It was `messages x
                  0.011` — a flat per-message rate standing in for per-token
                  billing, in a currency the API and this table disagreed about
                  (the rates read as USD, the cell rendered a dollar sign over an
                  INR-denominated product). A per-user cost figure is exactly the
                  kind of number that gets forwarded to finance. */}
              <table className="w-full">
                <thead><tr style={{ borderBottom: "1px solid var(--border)" }}>
                  {["User", "Email", "Role", "Messages"].map(h => (
                    <th key={h} className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {(usage?.top_users || []).map(u => (
                    <tr key={u.user_id} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                      <td className="px-3 py-2 text-sm font-medium" style={{ color: "var(--text-primary)" }}>{u.name}</td>
                      <td className="px-3 py-2 text-xs" style={{ color: "var(--text-secondary)" }}>{u.email}</td>
                      <td className="px-3 py-2"><span className="px-2 py-0.5 rounded-full text-[10px] font-semibold capitalize" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>{u.role}</span></td>
                      <td className="px-3 py-2 text-sm font-mono" style={{ color: "var(--text-primary)" }}>{u.message_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {usage?.cost_note && (
              <p role="note" className="text-[11px] mt-3" style={{ color: "var(--text-muted)" }}>
                {usage.cost_note}
              </p>
            )}
          </>
        )}
      </motion.div>
    </div>
  );
}

/** `no_traffic` is grey, not green: nobody has called this provider since the
 *  process started, which is neither healthy nor unhealthy — it is unmeasured.
 *  Painting it green is a smaller version of the defect this page had. */
const STATUS_COLORS = {
  online: "#00D68F",
  degraded: "#F59E0B",
  offline: "#FF6B6B",
  no_traffic: "#6B7280",
  not_measured: "#6B7280",
};

function StatusBadge({ status }) {
  const c = STATUS_COLORS[status] || "#6B7280";
  return (
    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full" style={{ background: `${c}15` }}>
      <div className="w-1.5 h-1.5 rounded-full" style={{ background: c, boxShadow: `0 0 6px ${c}80` }} />
      <span className="text-[11px] font-semibold capitalize" style={{ color: c }}>
        {String(status || "unknown").replace(/_/g, " ")}
      </span>
    </div>
  );
}

function SummaryCard({ icon: Icon, label, value, color, hint }) {
  return (
    <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} className="p-5 rounded-2xl" title={hint} style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-3 mb-2"><div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}><Icon size={18} style={{ color }} /></div><span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{label}</span></div>
      <div className="stat-value !text-xl">
        <MetricValue value={value} reason={hint}
                     format={v => (typeof v === "number" ? v.toLocaleString() : v)} />
      </div>
    </motion.div>
  );
}

function Metric({ label, value, format, reason }) {
  return (
    <div>
      <div className="text-xs mb-1" style={{ color: "var(--text-muted)" }}>{label}</div>
      <div className="text-sm font-semibold font-mono" style={{ color: "var(--text-primary)" }}>
        <MetricValue value={value} reason={reason}
                     format={format || (v => (typeof v === "number" ? v.toLocaleString() : v))} />
      </div>
    </div>
  );
}

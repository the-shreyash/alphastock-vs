import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Brain, Zap, DollarSign, AlertTriangle, Clock, Users } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import adminService from "../../services/adminService";

export default function AdminAI() {
  const [status, setStatus] = useState(null);
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([adminService.getAIStatus(), adminService.getAIUsage()])
      .then(([s, u]) => { setStatus(s.data); setUsage(u.data); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return <div className="space-y-6"><div className="h-8 w-48 rounded-lg animate-pulse" style={{ background: "var(--border)" }} /><div className="grid grid-cols-2 gap-4">{Array.from({length:2}).map((_,i)=><div key={i} className="h-48 rounded-2xl animate-pulse" style={{background:"var(--bg-surface)"}}/>)}</div></div>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">AI Monitoring</h1>
        <p className="page-subtitle mt-1">Model status, usage, and cost tracking</p>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <SummaryCard icon={Zap} label="Requests Today" value={status?.total_requests_today || 0} color="#F59E0B" />
        <SummaryCard icon={Brain} label="Total Requests" value={status?.total_requests_all || 0} color="#6366F1" />
        <SummaryCard icon={DollarSign} label="Estimated Cost" value={`$${status?.total_estimated_cost || 0}`} color="#00D68F" />
      </div>

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
                <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>{p.model}</span>
              </div>
              <StatusBadge status={p.status} />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <Metric label="Latency" value={`${p.latency_ms}ms`} icon={Clock} />
              <Metric label="Daily" value={p.daily_requests} icon={Zap} />
              <Metric label="Monthly" value={p.monthly_requests} icon={Brain} />
              <Metric label="Cost" value={`$${p.estimated_cost}`} icon={DollarSign} />
            </div>
            <div className="mt-4 grid grid-cols-2 gap-4">
              <div className="flex items-center gap-2">
                <AlertTriangle size={14} style={{ color: p.failures > 0 ? "#FF6B6B" : "var(--text-muted)" }} />
                <span className="text-xs" style={{ color: "var(--text-secondary)" }}>Failures: <b>{p.failures}</b></span>
              </div>
              <div className="flex items-center gap-2">
                <Zap size={14} style={{ color: p.fallbacks > 0 ? "#F59E0B" : "var(--text-muted)" }} />
                <span className="text-xs" style={{ color: "var(--text-secondary)" }}>Fallbacks: <b>{p.fallbacks}</b></span>
              </div>
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
                  <Bar dataKey="request_count" fill="#818CF8" radius={[0, 6, 6, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead><tr style={{ borderBottom: "1px solid var(--border)" }}>
                  {["User", "Email", "Role", "Requests", "Est. Cost"].map(h => (
                    <th key={h} className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {(usage?.top_users || []).map(u => (
                    <tr key={u.user_id} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                      <td className="px-3 py-2 text-sm font-medium" style={{ color: "var(--text-primary)" }}>{u.name}</td>
                      <td className="px-3 py-2 text-xs" style={{ color: "var(--text-secondary)" }}>{u.email}</td>
                      <td className="px-3 py-2"><span className="px-2 py-0.5 rounded-full text-[10px] font-semibold capitalize" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>{u.role}</span></td>
                      <td className="px-3 py-2 text-sm font-mono" style={{ color: "var(--text-primary)" }}>{u.request_count}</td>
                      <td className="px-3 py-2 text-sm font-mono" style={{ color: "var(--text-primary)" }}>${u.estimated_cost}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </motion.div>
    </div>
  );
}

function StatusBadge({ status }) {
  const c = status === "online" ? "#00D68F" : status === "offline" ? "#FF6B6B" : "#F59E0B";
  return (
    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full" style={{ background: `${c}15` }}>
      <div className="w-1.5 h-1.5 rounded-full" style={{ background: c, boxShadow: `0 0 6px ${c}80` }} />
      <span className="text-[11px] font-semibold capitalize" style={{ color: c }}>{status}</span>
    </div>
  );
}

function SummaryCard({ icon: Icon, label, value, color }) {
  return (
    <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} className="p-5 rounded-2xl" style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-3 mb-2"><div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}><Icon size={18} style={{ color }} /></div><span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{label}</span></div>
      <div className="stat-value !text-xl">{typeof value === "number" ? value.toLocaleString() : value}</div>
    </motion.div>
  );
}

function Metric({ label, value, icon: Icon }) {
  return (
    <div><div className="text-xs mb-1" style={{ color: "var(--text-muted)" }}>{label}</div><div className="text-sm font-semibold font-mono" style={{ color: "var(--text-primary)" }}>{value}</div></div>
  );
}

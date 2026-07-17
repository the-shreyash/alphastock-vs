import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Wifi, Clock, AlertTriangle, CheckCircle, XCircle } from "lucide-react";
import adminService from "../../services/adminService";

const TYPE_COLORS = { market_data: "#6366F1", ai: "#8B5CF6", news: "#06B6D4", broker: "#F59E0B", payment: "#00D68F", notification: "#EC4899" };

export default function AdminAPIs() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminService.getAPIHealth().then(r => { setData(r.data); setLoading(false); }).catch(() => setLoading(false));
  }, []);

  if (loading) return <div className="space-y-6"><div className="h-8 w-48 rounded-lg animate-pulse" style={{ background: "var(--border)" }} /><div className="grid grid-cols-2 gap-4">{Array.from({length:6}).map((_,i)=><div key={i} className="h-36 rounded-2xl animate-pulse" style={{background:"var(--bg-surface)"}}/>)}</div></div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">API Health</h1>
          <p className="page-subtitle mt-1">External service status and performance</p>
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl" style={{ background: data?.overall_status === "healthy" ? "rgba(0,214,143,0.1)" : "rgba(255,107,107,0.1)" }}>
          {data?.overall_status === "healthy" ? <CheckCircle size={16} style={{ color: "#00D68F" }} /> : <XCircle size={16} style={{ color: "#FF6B6B" }} />}
          <span className="text-xs font-semibold capitalize" style={{ color: data?.overall_status === "healthy" ? "#00D68F" : "#FF6B6B" }}>{data?.overall_status}</span>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {(data?.apis || []).map((api, i) => {
          const color = TYPE_COLORS[api.type] || "#6B7280";
          const statusColor = api.status === "online" ? "#00D68F" : api.status === "offline" ? "#FF6B6B" : "#F59E0B";
          return (
            <motion.div
              key={api.name} initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
              className="p-5 rounded-2xl group transition-all duration-300"
              style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)", boxShadow: "var(--card-shadow)" }}
              onMouseEnter={e => { e.currentTarget.style.boxShadow = "var(--card-shadow-hover)"; e.currentTarget.style.borderColor = `${color}33`; }}
              onMouseLeave={e => { e.currentTarget.style.boxShadow = "var(--card-shadow)"; e.currentTarget.style.borderColor = "var(--border)"; }}
            >
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2.5">
                  <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}>
                    <Wifi size={16} style={{ color }} />
                  </div>
                  <div>
                    <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{api.name}</h4>
                    <span className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>{api.type.replace("_", " ")}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1 px-2 py-0.5 rounded-full" style={{ background: `${statusColor}15` }}>
                  <div className="w-1.5 h-1.5 rounded-full" style={{ background: statusColor, boxShadow: `0 0 6px ${statusColor}80` }} />
                  <span className="text-[10px] font-semibold capitalize" style={{ color: statusColor }}>{api.status}</span>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 mt-4">
                <div>
                  <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>Latency</div>
                  <div className="text-sm font-mono font-semibold mt-0.5" style={{ color: api.latency_ms > 1000 ? "#F59E0B" : "var(--text-primary)" }}>{api.latency_ms}ms</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>Quota</div>
                  <div className="text-sm font-mono font-semibold mt-0.5" style={{ color: "var(--text-primary)" }}>{api.quota_remaining}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>Failure Rate</div>
                  <div className="text-sm font-mono font-semibold mt-0.5" style={{ color: api.failure_rate > 0 ? "#FF6B6B" : "var(--gain)" }}>{api.failure_rate}%</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>Requests/Day</div>
                  <div className="text-sm font-mono font-semibold mt-0.5" style={{ color: "var(--text-primary)" }}>{api.requests_today}</div>
                </div>
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { ScrollText, Search, Filter, ChevronLeft, ChevronRight } from "lucide-react";
import adminService from "../../services/adminService";

const ACTION_COLORS = {
  "user.blocked": "#FF6B6B", "user.unblocked": "#00D68F", "user.deleted": "#FF6B6B", "user.updated": "#6366F1",
  "user.plan_granted": "#8B5CF6", "payment.refunded": "#F59E0B", "feature_flag.created": "#06B6D4",
  "feature_flag.updated": "#06B6D4", "ticket.updated": "#EC4899", "announcement.created": "#F59E0B",
  "announcement.updated": "#F59E0B", "announcement.deleted": "#FF6B6B",
};

export default function AdminLogs() {
  const [logs, setLogs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [actionFilter, setActionFilter] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await adminService.getLogs({ page, limit: 30, action: actionFilter });
      setLogs(data.logs); setTotal(data.total); setPages(data.pages);
    } catch { /* */ }
    setLoading(false);
  }, [page, actionFilter]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">Audit Logs</h1>
        <p className="page-subtitle mt-1">{total} audit records</p>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
          <input value={actionFilter} onChange={e => { setActionFilter(e.target.value); setPage(1); }} placeholder="Filter by action..." className="w-full pl-10 pr-4 py-2.5 rounded-xl text-sm outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
        </div>
      </div>

      {/* Logs */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="rounded-2xl overflow-hidden" style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
        {loading ? (
          <div className="p-6 space-y-3">{Array.from({ length: 5 }).map((_, i) => <div key={i} className="h-12 rounded-xl animate-pulse" style={{ background: "var(--border)" }} />)}</div>
        ) : logs.length === 0 ? (
          <div className="p-12 text-center"><ScrollText size={32} style={{ color: "var(--text-muted)", margin: "0 auto" }} /><p className="text-sm mt-3" style={{ color: "var(--text-muted)" }}>No audit logs yet. Actions will appear here.</p></div>
        ) : (
          <div className="divide-y" style={{ borderColor: "var(--border-subtle)" }}>
            {logs.map((log, i) => {
              const color = ACTION_COLORS[log.action] || "#6B7280";
              return (
                <motion.div key={log._id} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.02 }} className="px-5 py-3.5 flex items-start gap-4 transition-colors" onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"} onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                  <div className="w-2 h-2 rounded-full mt-1.5 shrink-0" style={{ background: color }} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="px-2 py-0.5 rounded-md text-[10px] font-mono font-semibold" style={{ background: `${color}15`, color }}>{log.action}</span>
                      <span className="text-xs" style={{ color: "var(--text-secondary)" }}>by <b>{log.admin_name || "System"}</b></span>
                      {log.target && <span className="text-xs font-mono truncate max-w-[200px]" style={{ color: "var(--text-muted)" }}>{log.target}</span>}
                    </div>
                    {log.details && Object.keys(log.details).length > 0 && (
                      <div className="text-[11px] font-mono mt-1 truncate" style={{ color: "var(--text-muted)" }}>{JSON.stringify(log.details)}</div>
                    )}
                  </div>
                  <span className="text-[11px] font-mono whitespace-nowrap shrink-0" style={{ color: "var(--text-muted)" }}>{new Date(log.timestamp).toLocaleString()}</span>
                </motion.div>
              );
            })}
          </div>
        )}
        {pages > 1 && (
          <div className="flex items-center justify-between px-5 py-3" style={{ borderTop: "1px solid var(--border)" }}>
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>Page {page} of {pages}</span>
            <div className="flex gap-1">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="p-1.5 rounded-lg disabled:opacity-30" style={{ color: "var(--text-secondary)" }}><ChevronLeft size={16} /></button>
              <button disabled={page >= pages} onClick={() => setPage(p => p + 1)} className="p-1.5 rounded-lg disabled:opacity-30" style={{ color: "var(--text-secondary)" }}><ChevronRight size={16} /></button>
            </div>
          </div>
        )}
      </motion.div>
    </div>
  );
}

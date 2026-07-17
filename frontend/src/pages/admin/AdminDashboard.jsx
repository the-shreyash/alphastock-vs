import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Users, TrendingUp, Brain, CreditCard, Activity, LifeBuoy, Wifi, Shield, ArrowUpRight, ArrowDownRight } from "lucide-react";
import adminService from "../../services/adminService";

const STAT_CARDS = [
  { key: "total_users", label: "Total Users", icon: Users, color: "#6366F1", format: "number" },
  { key: "premium_users", label: "Pro Users", icon: Shield, color: "#8B5CF6", format: "number" },
  { key: "elite_users", label: "Elite Users", icon: Shield, color: "#EC4899", format: "number" },
  { key: "mrr", label: "MRR", icon: CreditCard, color: "#00D68F", format: "currency" },
  { key: "arr", label: "ARR", icon: TrendingUp, color: "#00C48C", format: "currency" },
  { key: "today_trades", label: "Trades Today", icon: TrendingUp, color: "#F59E0B", format: "number" },
  { key: "ai_requests_today", label: "AI Requests Today", icon: Brain, color: "#818CF8", format: "number" },
  { key: "open_tickets", label: "Open Tickets", icon: LifeBuoy, color: "#FF6B6B", format: "number" },
  { key: "broker_connections", label: "Broker Links", icon: Wifi, color: "#06B6D4", format: "number" },
];

function formatValue(value, format) {
  if (format === "currency") return `₹${(value || 0).toLocaleString("en-IN")}`;
  return (value || 0).toLocaleString();
}

export default function AdminDashboard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminService.getDashboard().then(r => { setData(r.data); setLoading(false); }).catch(() => setLoading(false));
  }, []);

  if (loading) return <DashboardSkeleton />;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="page-title">Dashboard</h1>
        <p className="page-subtitle mt-1">Platform overview and key metrics</p>
      </div>

      {/* Health Status Bar */}
      <motion.div
        initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
        className="flex items-center gap-4 px-5 py-3 rounded-2xl"
        style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}
      >
        <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>System Status</span>
        <HealthBadge label="Server" status={data?.server_health} />
        <HealthBadge label="Database" status={data?.db_health} />
        <HealthBadge label="API" status={data?.api_health} />
      </motion.div>

      {/* Stat Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {STAT_CARDS.map((card, i) => (
          <motion.div
            key={card.key}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            className="group relative p-5 rounded-2xl transition-all duration-300 cursor-default"
            style={{
              background: "var(--bg-card-glass)",
              backdropFilter: "blur(24px)",
              border: "1px solid var(--border)",
              boxShadow: "var(--card-shadow)",
            }}
            onMouseEnter={e => { e.currentTarget.style.boxShadow = "var(--card-shadow-hover)"; e.currentTarget.style.borderColor = `${card.color}33`; }}
            onMouseLeave={e => { e.currentTarget.style.boxShadow = "var(--card-shadow)"; e.currentTarget.style.borderColor = "var(--border)"; }}
          >
            <div className="flex items-start justify-between">
              <div>
                <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
                  {card.label}
                </span>
                <div className="mt-2 stat-value" style={{ fontSize: "1.75rem" }}>
                  {formatValue(data?.[card.key], card.format)}
                </div>
              </div>
              <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: `${card.color}15` }}>
                <card.icon size={20} style={{ color: card.color }} />
              </div>
            </div>
            <div className="mt-3 flex items-center gap-1.5">
              <ArrowUpRight size={14} style={{ color: "var(--gain)" }} />
              <span className="text-xs font-medium" style={{ color: "var(--gain)" }}>+12%</span>
              <span className="text-xs" style={{ color: "var(--text-muted)" }}>vs last month</span>
            </div>
          </motion.div>
        ))}
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SummaryCard title="Trade Activity">
          <div className="grid grid-cols-3 gap-4">
            <MiniStat label="Open Trades" value={data?.open_trades || 0} />
            <MiniStat label="Total Trades" value={data?.total_trades || 0} />
            <MiniStat label="Today" value={data?.today_trades || 0} />
          </div>
        </SummaryCard>
        <SummaryCard title="Platform Metrics">
          <div className="grid grid-cols-3 gap-4">
            <MiniStat label="Admins" value={data?.admin_users || 0} />
            <MiniStat label="Notifications" value={data?.total_notifications || 0} />
            <MiniStat label="Revenue Today" value={`₹${(data?.revenue_today || 0).toLocaleString("en-IN")}`} />
          </div>
        </SummaryCard>
      </div>
    </div>
  );
}

function HealthBadge({ label, status }) {
  const color = status === "healthy" ? "#00D68F" : status === "degraded" ? "#FFB224" : "#FF6B6B";
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-2 h-2 rounded-full" style={{ background: color, boxShadow: `0 0 6px ${color}80` }} />
      <span className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>{label}</span>
    </div>
  );
}

function SummaryCard({ title, children }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      className="p-5 rounded-2xl"
      style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}
    >
      <h3 className="card-title mb-4">{title}</h3>
      {children}
    </motion.div>
  );
}

function MiniStat({ label, value }) {
  return (
    <div className="text-center">
      <div className="stat-value !text-lg">{typeof value === "number" ? value.toLocaleString() : value}</div>
      <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>{label}</div>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6">
      <div><div className="h-8 w-48 rounded-lg animate-pulse" style={{ background: "var(--border)" }} /></div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {Array.from({ length: 9 }).map((_, i) => (
          <div key={i} className="h-32 rounded-2xl animate-pulse" style={{ background: "var(--bg-surface)" }} />
        ))}
      </div>
    </div>
  );
}

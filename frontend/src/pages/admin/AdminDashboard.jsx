import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Users, TrendingUp, Brain, CreditCard, LifeBuoy, Wifi, Shield } from "lucide-react";
import adminService from "../../services/adminService";
import { MetricValue, unavailableReason } from "../../components/ui/Unavailable";

/**
 * PH3.9 — three fabricated money figures removed from this dashboard.
 *
 * `revenue_today` was `count(all payment documents) × ₹499`; `mrr` and `arr`
 * were role counts × hardcoded prices, and roles are granted by admins with no
 * payment involved. All three now arrive as `null` with a reason, and the cards
 * render an em-dash rather than `₹0` — which is the specific coercion
 * (`(value || 0)`) that would silently undo the backend change and put a
 * measured-looking zero back on the page.
 *
 * `api_health` is gone (it was the literal "healthy" and reported a healthy
 * platform during a total outage) and `ai_requests_today` is renamed to
 * `chat_messages_today`, which is what it always counted.
 */
const STAT_CARDS = [
  { key: "total_users", label: "Total Users", icon: Users, color: "#6366F1", format: "number" },
  { key: "premium_users", label: "Pro Users", icon: Shield, color: "#8B5CF6", format: "number" },
  { key: "elite_users", label: "Elite Users", icon: Shield, color: "#EC4899", format: "number" },
  { key: "mrr", label: "MRR", icon: CreditCard, color: "#00D68F", format: "currency" },
  { key: "arr", label: "ARR", icon: TrendingUp, color: "#00C48C", format: "currency" },
  { key: "today_trades", label: "Trades Today", icon: TrendingUp, color: "#F59E0B", format: "number" },
  { key: "chat_messages_today", label: "Chat Messages Today", icon: Brain, color: "#818CF8", format: "number" },
  { key: "open_tickets", label: "Open Tickets", icon: LifeBuoy, color: "#FF6B6B", format: "number" },
  { key: "broker_connections", label: "Broker Links", icon: Wifi, color: "#06B6D4", format: "number" },
];

/** Formatters take a value that is known to be present — `MetricValue` handles
 *  the absent case, so nothing here needs (and nothing here may use) `|| 0`. */
const FORMATTERS = {
  currency: v => `₹${v.toLocaleString("en-IN")}`,
  number: v => v.toLocaleString(),
};

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
        {/* PH3.9: the "API" badge is gone — it read the literal "healthy" and
            checked nothing. Server and Database are now backed by real probes,
            and every failing critical dependency gets its own badge rather than
            being averaged into a single green light. */}
        <HealthBadge label="Server" status={data?.server_health} />
        <HealthBadge label="Database" status={data?.db_health} />
        {(data?.degraded_dependencies || []).map(name => (
          <HealthBadge key={name} label={name} status="unhealthy" />
        ))}
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
                  <MetricValue value={data?.[card.key]} format={FORMATTERS[card.format]}
                               reason={unavailableReason(data, card.key)} />
                </div>
              </div>
              <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: `${card.color}15` }}>
                <card.icon size={20} style={{ color: card.color }} />
              </div>
            </div>
            {/* PH3.8 (F-18) deleted the hardcoded "+12% vs last month" that
                every one of these nine cards carried — the same invented growth
                figure beside user counts, MRR, open tickets and broker links
                alike, in the gain colour so it read as a measured comparison.
                PH3.8 then added a "Simulated" badge here; PH3.9 removes that
                too, because there is nothing left on this surface to flag. */}
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
            <MiniStat label="Revenue Today" value={data?.revenue_today}
                      format={FORMATTERS.currency}
                      reason={unavailableReason(data, "revenue_today")} />
          </div>
        </SummaryCard>
      </div>
    </div>
  );
}

function HealthBadge({ label, status }) {
  // "serving" is what the app process can honestly assert about itself: it
  // answered this request. "unknown" is amber, not red — a probe that could not
  // run is not the same as a dependency that failed.
  const color = status === "healthy" || status === "serving" ? "#00D68F"
    : status === "degraded" || status === "unknown" ? "#FFB224"
    : "#FF6B6B";
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

function MiniStat({ label, value, format, reason }) {
  return (
    <div className="text-center">
      <div className="stat-value !text-lg">
        <MetricValue value={value} reason={reason}
                     format={format || (v => (typeof v === "number" ? v.toLocaleString() : v))} />
      </div>
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

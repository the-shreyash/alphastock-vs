import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { CreditCard, TrendingUp, DollarSign, RefreshCcw, XCircle, PieChart } from "lucide-react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart as RPieChart, Pie, Cell } from "recharts";
import adminService from "../../services/adminService";

const PIE_COLORS = ["#6366F1", "#8B5CF6", "#EC4899", "#F59E0B", "#00D68F", "#06B6D4", "#6B7280"];

export default function AdminPayments() {
  const [stats, setStats] = useState(null);
  const [revenue, setRevenue] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([adminService.getPaymentStats(), adminService.getRevenueAnalytics()])
      .then(([s, r]) => { setStats(s.data); setRevenue(r.data.daily_revenue || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return <div className="space-y-6"><div className="h-8 w-48 rounded-lg animate-pulse" style={{ background: "var(--border)" }} /><div className="grid grid-cols-4 gap-4">{Array.from({length:4}).map((_,i)=><div key={i} className="h-28 rounded-2xl animate-pulse" style={{background:"var(--bg-surface)"}}/>)}</div></div>;

  const planData = stats?.plan_distribution ? Object.entries(stats.plan_distribution).map(([name, value]) => ({ name, value })) : [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">Payments & Revenue</h1>
        <p className="page-subtitle mt-1">Financial overview and subscription metrics</p>
      </div>

      {/* Revenue Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <RevenueCard icon={DollarSign} label="MRR" value={`₹${(stats?.mrr || 0).toLocaleString("en-IN")}`} color="#00D68F" />
        <RevenueCard icon={TrendingUp} label="ARR" value={`₹${(stats?.arr || 0).toLocaleString("en-IN")}`} color="#6366F1" />
        <RevenueCard icon={CreditCard} label="Pro Users" value={stats?.premium_count || 0} color="#8B5CF6" />
        <RevenueCard icon={CreditCard} label="Elite Users" value={stats?.elite_count || 0} color="#EC4899" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Revenue Chart */}
        <motion.div
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
          className="lg:col-span-2 p-5 rounded-2xl"
          style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}
        >
          <h3 className="card-title mb-4">Revenue Trend (30 Days)</h3>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={revenue}>
                <defs>
                  <linearGradient id="revGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#6366F1" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#6366F1" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickFormatter={d => d.split("-").slice(1).join("/")} />
                <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickFormatter={v => `₹${v/1000}k`} />
                <Tooltip contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", borderRadius: 12, fontSize: 12 }} formatter={v => [`₹${v.toLocaleString()}`, "Revenue"]} />
                <Area type="monotone" dataKey="revenue" stroke="#6366F1" strokeWidth={2} fill="url(#revGrad)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </motion.div>

        {/* Plan Distribution */}
        <motion.div
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}
          className="p-5 rounded-2xl"
          style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}
        >
          <h3 className="card-title mb-4">Plan Distribution</h3>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <RPieChart>
                <Pie data={planData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={70} innerRadius={40} paddingAngle={2}>
                  {planData.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                </Pie>
                <Tooltip contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", borderRadius: 12, fontSize: 12 }} />
              </RPieChart>
            </ResponsiveContainer>
          </div>
          <div className="space-y-2 mt-2">
            {planData.map((d, i) => (
              <div key={d.name} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-full" style={{ background: PIE_COLORS[i % PIE_COLORS.length] }} />
                  <span className="text-xs capitalize" style={{ color: "var(--text-secondary)" }}>{d.name}</span>
                </div>
                <span className="text-xs font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{d.value}</span>
              </div>
            ))}
          </div>
        </motion.div>
      </div>

      {/* Additional Metrics */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <MetricCard label="Pending Payments" value={stats?.pending_payments || 0} icon={RefreshCcw} color="#F59E0B" />
        <MetricCard label="Failed Payments" value={stats?.failed_payments || 0} icon={XCircle} color="#FF6B6B" />
        <MetricCard label="Lifetime Users" value={stats?.lifetime_count || 0} icon={CreditCard} color="#00D68F" />
      </div>
    </div>
  );
}

function RevenueCard({ icon: Icon, label, value, color }) {
  return (
    <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} className="p-5 rounded-2xl" style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-3 mb-3">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}><Icon size={18} style={{ color }} /></div>
        <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{label}</span>
      </div>
      <div className="stat-value !text-xl">{value}</div>
    </motion.div>
  );
}

function MetricCard({ label, value, icon: Icon, color }) {
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="p-4 rounded-2xl flex items-center gap-4" style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
      <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}><Icon size={18} style={{ color }} /></div>
      <div><div className="stat-value !text-lg">{value}</div><div className="text-xs" style={{ color: "var(--text-muted)" }}>{label}</div></div>
    </motion.div>
  );
}

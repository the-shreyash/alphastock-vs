import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Users, TrendingUp, Target, ArrowUpRight, ArrowDownRight } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import adminService from "../../services/adminService";
import { MetricValue, UnavailablePanel, unavailableReason } from "../../components/ui/Unavailable";

/**
 * PH3.9 — Admin analytics, with the fabricated metrics removed.
 *
 * What this page used to render, all of it invented in the backend: DAU as
 * today's signup count, MAU as the total user count, retention / churn / growth
 * as literals (78.5, 4.2, 12.8), a 30-day revenue chart produced by a for-loop
 * with no database access at all, and feature-usage percentages unrelated to
 * the counts beside them. PH3.8 left them in place behind a "Simulated" badge;
 * PH3.9 removes them.
 *
 * Two of them came back as real numbers — DAU (from session activity) and
 * growth (signups, period over period). The rest are `null`, and this page's
 * job is to render `null` as *the absence of a number* rather than as zero.
 * `{value || 0}` is the specific idiom that undoes the whole backend change, so
 * every metric here goes through `MetricValue`, and the reason travels from the
 * response's `analytics.metrics[name].note` into the tooltip.
 *
 * The revenue chart is gone rather than flat: an axis with a line at zero
 * across thirty days still says "we measured thirty days and found nothing",
 * which is false. It is replaced by an explicit empty state naming what the
 * platform would need to record.
 */
export default function AdminAnalytics() {
  const [userStats, setUserStats] = useState(null);
  const [revenue, setRevenue] = useState(null);
  const [features, setFeatures] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([
      adminService.getUserAnalytics(),
      adminService.getRevenueAnalytics(),
      adminService.getFeatureAnalytics(),
    ])
      .then(([u, r, f]) => {
        setUserStats(u.data);
        setRevenue(r.data);
        setFeatures(f.data);
        setLoading(false);
      })
      .catch(() => {
        // A failed load is NOT an empty dashboard. Rendering zeros here would
        // tell an operator the platform has no users, which is a different and
        // much more alarming claim than "we could not reach the server".
        setError("Analytics could not be loaded.");
        setLoading(false);
      });
  }, []);

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="h-8 w-48 rounded-lg animate-pulse" style={{ background: "var(--border)" }} />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-28 rounded-2xl animate-pulse" style={{ background: "var(--bg-surface)" }} />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <h1 className="page-title">Analytics</h1>
        <div role="alert" className="p-5 rounded-2xl text-sm"
             style={{ background: "var(--bg-card-glass)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          {error}{" "}
          <button onClick={() => window.location.reload()} className="underline"
                  style={{ color: "var(--ai-accent)" }}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  const reason = (name) => unavailableReason(userStats, name);
  const growth = userStats?.growth_rate;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">Analytics</h1>
        <p className="page-subtitle mt-1">User engagement, revenue, and feature usage</p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <MetricCard label="Active today" value={userStats?.dau} icon={Users} color="#6366F1"
                    reason={reason("dau")} />
        <MetricCard label="Total Users" value={userStats?.total_users} icon={Users} color="#8B5CF6"
                    delta={growth} deltaReason={reason("growth_rate")} />
        <MetricCard label="Retention" value={userStats?.retention_rate} icon={Target} color="#00D68F"
                    format={v => `${v}%`} reason={reason("retention_rate")} />
        <MetricCard label="Conversion" value={userStats?.conversion_rate} icon={TrendingUp} color="#F59E0B"
                    format={v => `${v}%`} reason={reason("conversion_rate")} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel title="Revenue Trend">
          {/* Empty, never a zero line — see the component docstring. */}
          {(revenue?.daily_revenue || []).length === 0 ? (
            <UnavailablePanel
              title="Revenue reporting is not available"
              reason={revenue?.note}
              requiredSource={revenue?.required_source}
            />
          ) : (
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={revenue.daily_revenue}>
                  <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-muted)" }}
                         tickFormatter={d => d.split("-")[2]} />
                  <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
                  <Tooltip contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", borderRadius: 12, fontSize: 12 }} />
                  <Bar dataKey="revenue" fill="#00D68F" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Panel>

        <Panel title="Feature Usage">
          {/* Counts, not percentages. The bar is scaled against the largest
              count on the page so the comparison it draws is one the data
              actually supports — a percentage would imply a denominator
              (distinct users over an active base) that does not exist. */}
          <FeatureCounts features={features?.features || []} note={features?.adoption_note} />
        </Panel>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <SmallStat label="Churn Rate" value={userStats?.churn_rate} format={v => `${v}%`}
                   negative reason={reason("churn_rate")} />
        <SmallStat label="Signup Growth (30d)" value={growth} format={v => `${v}%`}
                   reason={reason("growth_rate")} />
        <SmallStat label="Today Signups" value={userStats?.today_signups} />
        <SmallStat label="Active (30d)" value={userStats?.mau} reason={reason("mau")} />
      </div>

      {Array.isArray(userStats?.unavailable_metrics) && userStats.unavailable_metrics.length > 0 && (
        <p role="note" className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          Metrics shown as <b>—</b> are not available: {userStats.unavailable_metrics.join(", ")}.
          The platform does not currently record the data they need; hover any one for the
          specific reason. See docs/architecture/ANALYTICS.md.
        </p>
      )}
    </div>
  );
}

function Panel({ title, children }) {
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="p-5 rounded-2xl"
                style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
      <h3 className="card-title mb-4">{title}</h3>
      {children}
    </motion.div>
  );
}

function FeatureCounts({ features, note }) {
  if (features.length === 0) {
    return <UnavailablePanel title="No feature usage recorded yet" reason={note} />;
  }
  const largest = Math.max(...features.map(f => f.usage_count), 1);
  return (
    <div className="space-y-3">
      {features.map((f, i) => (
        <div key={f.name} className="flex items-center gap-3">
          <span className="text-xs w-28 truncate" style={{ color: "var(--text-secondary)" }}>{f.name}</span>
          <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: "var(--border)" }}>
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${(f.usage_count / largest) * 100}%` }}
              transition={{ delay: i * 0.05, duration: 0.6 }}
              className="h-full rounded-full"
              style={{ background: `hsl(${240 + i * 20}, 70%, 60%)` }}
            />
          </div>
          <span className="text-xs font-mono font-semibold w-16 text-right" style={{ color: "var(--text-primary)" }}>
            {f.usage_count.toLocaleString()}
          </span>
        </div>
      ))}
      {note && (
        <p className="text-[11px] leading-relaxed pt-1" style={{ color: "var(--text-muted)" }}>{note}</p>
      )}
    </div>
  );
}

function MetricCard({ label, value, icon: Icon, color, delta, deltaReason, reason, format }) {
  // A delta is rendered only when there is a real one. Pre-PH3.9 this card
  // carried `+${growth_rate}%` in the gain colour unconditionally, over a
  // backend constant — a measured-looking growth badge with nothing behind it.
  const hasDelta = delta !== null && delta !== undefined;
  const positive = hasDelta && delta >= 0;
  return (
    <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} className="p-4 rounded-2xl"
                style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
      <div className="flex items-center justify-between mb-2">
        <div className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}>
          <Icon size={16} style={{ color }} />
        </div>
        {hasDelta && (
          <div className="flex items-center gap-0.5" title={deltaReason || "Signups vs the previous 30 days"}>
            {positive
              ? <ArrowUpRight size={12} style={{ color: "var(--gain)" }} />
              : <ArrowDownRight size={12} style={{ color: "var(--loss)" }} />}
            <span className="text-[10px] font-semibold" style={{ color: positive ? "var(--gain)" : "var(--loss)" }}>
              {positive ? "+" : ""}{delta}%
            </span>
          </div>
        )}
      </div>
      <div className="stat-value !text-lg">
        <MetricValue value={value} reason={reason}
                     format={format || (v => (typeof v === "number" ? v.toLocaleString() : v))} />
      </div>
      <div className="mt-1">
        <span className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>
          {label}
        </span>
      </div>
    </motion.div>
  );
}

function SmallStat({ label, value, negative, reason, format }) {
  const present = value !== null && value !== undefined;
  return (
    <div className="p-4 rounded-2xl text-center"
         style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
      <div className="stat-value !text-lg" style={negative && present ? { color: "var(--loss)" } : {}}>
        <MetricValue value={value} reason={reason}
                     format={format || (v => (typeof v === "number" ? v.toLocaleString() : v))} />
      </div>
      <div className="mt-1">
        <span className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>
          {label}
        </span>
      </div>
    </div>
  );
}

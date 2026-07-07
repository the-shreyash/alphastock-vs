import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import {
  Calendar,
  Clock,
  AlertTriangle,
  ChevronRight,
  Globe,
  Landmark,
  BarChart3,
  TrendingUp,
  DollarSign,
  Filter,
} from "lucide-react";
import api from "../../services/api";

const CATEGORY_CONFIG = {
  monetary_policy: { icon: Landmark, color: "#f59e0b", label: "RBI Policy" },
  economic_data: { icon: BarChart3, color: "#3b82f6", label: "Economic Data" },
  expiry: { icon: Clock, color: "#ef4444", label: "F&O Expiry" },
  earnings: { icon: TrendingUp, color: "#10b981", label: "Earnings" },
  government: { icon: Landmark, color: "#8b5cf6", label: "Government" },
  global: { icon: Globe, color: "#06b6d4", label: "Global" },
};

const IMPORTANCE_COLORS = {
  high: "text-[var(--loss)]",
  medium: "text-[var(--warning)]",
  low: "text-[var(--text-muted)]",
};

export default function EconomicCalendar({ compact = false }) {
  const [calendar, setCalendar] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeCategory, setActiveCategory] = useState(null);

  useEffect(() => {
    const params = { days_ahead: 30 };
    if (activeCategory) params.category = activeCategory;

    api.get("/market/calendar", { params })
      .then((r) => setCalendar(r.data))
      .catch(() => setCalendar(null))
      .finally(() => setLoading(false));
  }, [activeCategory]);

  if (loading) {
    return (
      <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] p-4">
        <div className="animate-pulse space-y-3">
          <div className="h-4 bg-[var(--bg-tertiary)] rounded w-1/3" />
          <div className="h-3 bg-[var(--bg-tertiary)] rounded w-2/3" />
          <div className="h-3 bg-[var(--bg-tertiary)] rounded w-1/2" />
        </div>
      </div>
    );
  }

  if (!calendar?.available) return null;

  const events = compact
    ? (calendar.upcoming_high || []).slice(0, 5)
    : (calendar.events || []).filter((e) => e.importance !== "low").slice(0, 20);

  return (
    <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Calendar size={14} className="text-[var(--accent)]" />
          <span className="text-sm font-semibold text-[var(--text-primary)]">
            Economic Calendar
          </span>
          {calendar.today_events?.length > 0 && (
            <span className="text-[10px] bg-[var(--loss)] text-white px-2 py-0.5 rounded-full font-medium">
              {calendar.today_events.length} today
            </span>
          )}
        </div>
      </div>

      {/* Category filters (non-compact) */}
      {!compact && (
        <div className="px-4 py-2 border-b border-[var(--border)] flex flex-wrap gap-1.5">
          <button
            onClick={() => setActiveCategory(null)}
            className={`px-2 py-1 rounded-md text-[10px] font-medium transition-all ${
              !activeCategory
                ? "bg-[var(--accent)] text-white"
                : "bg-[var(--bg-tertiary)] text-[var(--text-muted)] hover:bg-[var(--bg-hover)]"
            }`}
          >
            All
          </button>
          {(calendar.categories || []).map((cat) => {
            const config = CATEGORY_CONFIG[cat] || {};
            return (
              <button
                key={cat}
                onClick={() => setActiveCategory(cat === activeCategory ? null : cat)}
                className={`px-2 py-1 rounded-md text-[10px] font-medium transition-all ${
                  activeCategory === cat
                    ? "bg-[var(--accent)] text-white"
                    : "bg-[var(--bg-tertiary)] text-[var(--text-muted)] hover:bg-[var(--bg-hover)]"
                }`}
              >
                {config.label || cat}
              </button>
            );
          })}
        </div>
      )}

      {/* Events list */}
      <div className={compact ? "max-h-[280px] overflow-y-auto" : ""}>
        {events.length === 0 ? (
          <div className="p-6 text-center text-[var(--text-muted)] text-xs">
            No upcoming events in this category
          </div>
        ) : (
          events.map((event, i) => {
            const catConfig = CATEGORY_CONFIG[event.category] || {};
            const CatIcon = catConfig.icon || Calendar;
            const isToday = event.status === "today";
            const isPast = event.status === "past";

            return (
              <motion.div
                key={`${event.date}-${event.title}-${i}`}
                initial={{ opacity: 0, y: 5 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.03 }}
                className={`px-4 py-2.5 border-b border-[var(--border)] last:border-0 flex items-start gap-3 hover:bg-[var(--bg-hover)] transition-colors ${
                  isPast ? "opacity-50" : ""
                } ${isToday ? "bg-[var(--accent)]/5" : ""}`}
              >
                {/* Category icon */}
                <div
                  className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
                  style={{ backgroundColor: `${catConfig.color || "#666"}20` }}
                >
                  <CatIcon size={13} style={{ color: catConfig.color || "#666" }} />
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-[var(--text-primary)] truncate">
                      {event.title}
                    </span>
                    {isToday && (
                      <span className="text-[9px] bg-[var(--accent)] text-white px-1.5 py-0.5 rounded font-medium shrink-0">
                        TODAY
                      </span>
                    )}
                    {event.importance === "high" && !isToday && (
                      <AlertTriangle size={10} className="text-[var(--loss)] shrink-0" />
                    )}
                  </div>
                  {!compact && event.description && (
                    <p className="text-[10px] text-[var(--text-muted)] mt-0.5 line-clamp-1">
                      {event.description}
                    </p>
                  )}
                  {event.impact && (
                    <p className="text-[10px] text-[var(--text-muted)] mt-0.5">
                      Impact: {event.impact}
                    </p>
                  )}
                </div>

                {/* Date badge */}
                <div className="text-right shrink-0">
                  <div className="text-[10px] font-mono text-[var(--text-muted)]">
                    {new Date(event.date + "T00:00:00").toLocaleDateString("en-IN", {
                      day: "numeric",
                      month: "short",
                    })}
                  </div>
                  {event.status === "upcoming" && event.days_until != null && (
                    <div className="text-[9px] text-[var(--text-muted)]">
                      in {event.days_until}d
                    </div>
                  )}
                </div>
              </motion.div>
            );
          })
        )}
      </div>
    </div>
  );
}

import { motion } from "framer-motion";
import { CalendarClock } from "lucide-react";
import SectionUnavailable from "./SectionUnavailable";

/**
 * EconomicCalendarCard — scheduled events that can move the market today.
 *
 * Today's events lead (they are actionable now); high-importance events ahead
 * follow, so a trader can size positions knowing an RBI policy decision or F&O
 * expiry is coming. Each event states its likely impact — the platform explains
 * why an event matters rather than just naming it.
 */
const IMPORTANCE_STYLE = {
  high: { background: "rgba(244,63,94,0.12)", color: "var(--loss)" },
  medium: { background: "rgba(245,158,11,0.12)", color: "#f59e0b" },
  low: { background: "rgba(120,120,140,0.12)", color: "var(--text-muted)" },
};

function EventRow({ event, showCountdown }) {
  const badge = IMPORTANCE_STYLE[event.importance] || IMPORTANCE_STYLE.low;
  return (
    <div className="py-2.5">
      <div className="flex items-start justify-between gap-3">
        <span className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
          {event.title}
        </span>
        <span
          className="text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0"
          style={badge}
        >
          {event.importance}
        </span>
      </div>
      {event.impact && (
        <p className="text-[12px] mt-0.5" style={{ color: "var(--text-secondary)" }}>
          Impacts: {event.impact}
        </p>
      )}
      {showCountdown && event.days_until !== undefined && (
        <p className="text-[11px] font-mono mt-0.5" style={{ color: "var(--text-muted)" }}>
          {event.days_until === 1 ? "Tomorrow" : `In ${event.days_until} days`} · {event.date}
        </p>
      )}
    </div>
  );
}

export default function EconomicCalendarCard({ calendar }) {
  const today = calendar?.today || [];
  const upcoming = calendar?.upcoming || [];
  const isEmpty = today.length === 0 && upcoming.length === 0;

  return (
    <motion.div
      className="glass-card p-5"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4 }}
    >
      <h3 className="eyebrow mb-3 flex items-center gap-2">
        <CalendarClock size={13} /> Economic Calendar
      </h3>

      {!calendar?.available ? (
        <SectionUnavailable note={calendar?.note} icon={CalendarClock} />
      ) : isEmpty ? (
        <p className="text-[13px]" style={{ color: "var(--text-muted)" }}>
          No scheduled events today or in the next two weeks.
        </p>
      ) : (
        <div className="space-y-4">
          {today.length > 0 && (
            <div>
              <p className="stat-label mb-1">Today</p>
              <div className="divide-y" style={{ borderColor: "var(--border)" }}>
                {today.map((e, i) => <EventRow key={`${e.title}-${i}`} event={e} />)}
              </div>
            </div>
          )}
          {upcoming.length > 0 && (
            <div>
              <p className="stat-label mb-1">Coming Up</p>
              <div className="divide-y" style={{ borderColor: "var(--border)" }}>
                {upcoming.map((e, i) => (
                  <EventRow key={`${e.title}-${i}`} event={e} showCountdown />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </motion.div>
  );
}

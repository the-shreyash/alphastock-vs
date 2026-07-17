import { useEffect, useState, useCallback } from "react";
import { Activity, Search, Newspaper, Bell, BarChart3, Eye } from "lucide-react";
import api from "../../services/api";
import { useRealtimeStore, selectActivityUpdates, selectConnected } from "../../store/realtimeStore";

/**
 * ActivityTimeline — the AI Activity feed ("the AI never sleeps"). Backfills
 * once from GET /api/ai/activity, then streams live entries from the real-time
 * store's `activity_feed` pushes (Sprint R3); the poll is kept only as a
 * disconnected fallback. Read-only; no fabricated events.
 */
const CATEGORY_ICON = {
  scan: Search,
  news: Newspaper,
  alert: Bell,
  rank: BarChart3,
  monitor: Eye,
};

const STATUS_COLOR = {
  running: "var(--ai-accent)",
  done: "var(--gain)",
  warning: "var(--loss)",
};

export default function ActivityTimeline({ pollMs = 15000, limit = 12 }) {
  const [items, setItems] = useState(null);
  const activityUpdate = useRealtimeStore(selectActivityUpdates);
  const connected = useRealtimeStore(selectConnected);

  const load = useCallback(() => {
    api.get("/ai/activity")
      .then((r) => setItems(Array.isArray(r.data) ? r.data : []))
      .catch(() => setItems([]));
  }, []);

  // Backfill once on mount; poll only while the live feed is unavailable.
  useEffect(() => {
    load();
    if (connected) return undefined;
    const id = setInterval(load, pollMs);
    return () => clearInterval(id);
  }, [load, pollMs, connected]);

  // Prepend live pushes from the store's activity_feed stream (deduped).
  useEffect(() => {
    if (!activityUpdate) return;
    setItems((prev) => {
      const list = Array.isArray(prev) ? prev : [];
      const exists = list.some((a) => a.time === activityUpdate.time && a.action === activityUpdate.action);
      if (exists) return list;
      return [activityUpdate, ...list].slice(0, 50);
    });
  }, [activityUpdate]);

  return (
    <div className="glass-card p-5" data-testid="ai-activity-timeline">
      <h3 className="eyebrow mb-3 flex items-center gap-2">
        <Activity size={13} style={{ color: "var(--ai-accent)" }} /> AI Activity
      </h3>

      {items === null ? (
        <div className="space-y-2">{[1, 2, 3, 4].map((i) => <div key={i} className="h-9 rounded-lg skeleton" />)}</div>
      ) : items.length === 0 ? (
        <p className="text-[11px] py-3 text-center" style={{ color: "var(--text-muted)" }}>
          The AI engine is warming up — background activity will appear here shortly.
        </p>
      ) : (
        <div className="space-y-2.5 max-h-[360px] overflow-y-auto pr-1">
          {items.slice(0, limit).map((a, i) => {
            const Icon = CATEGORY_ICON[a.category] || Activity;
            const color = STATUS_COLOR[a.status] || "var(--text-muted)";
            return (
              <div key={i} className="flex items-start gap-2.5">
                <div className="w-6 h-6 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
                  style={{ background: "var(--hover)" }}>
                  <Icon size={12} style={{ color }} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] leading-snug" style={{ color: "var(--text-secondary)" }}>{a.action}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>{a.time}</span>
                    {a.status === "running" && (
                      <span className="text-[10px] font-medium animate-pulse" style={{ color }}>running…</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

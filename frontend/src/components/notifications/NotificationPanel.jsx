import { useState, useEffect, useCallback } from "react";
import api from "../../services/api";
import { X, Bell, CheckCheck, TrendingUp, TrendingDown, AlertTriangle, Info, CheckCircle2, Zap } from "lucide-react";

const TYPE_CONFIG = {
  TRADE_ENTRY: { icon: TrendingUp, color: "var(--ai-accent)", bg: "rgba(99,102,241,0.12)" },
  PROFIT_ALERT: { icon: CheckCircle2, color: "var(--gain)", bg: "rgba(16,185,129,0.12)" },
  RISK_ALERT: { icon: AlertTriangle, color: "#f59e0b", bg: "rgba(245,158,11,0.12)" },
  STOP_LOSS: { icon: TrendingDown, color: "var(--loss)", bg: "rgba(244,63,94,0.12)" },
  MORNING_REPORT: { icon: Zap, color: "var(--ai-accent)", bg: "rgba(99,102,241,0.12)" },
  EXIT_REMINDER: { icon: AlertTriangle, color: "#f59e0b", bg: "rgba(245,158,11,0.12)" },
  EOD_REPORT: { icon: Info, color: "var(--text-muted)", bg: "rgba(120,120,140,0.1)" },
};

function timeAgo(iso) {
  if (!iso) return "";
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function NotificationPanel({ onClose }) {
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    api.get("/notifications").then(({ data }) => setNotifications(data)).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const markRead = async (id) => {
    await api.put(`/notifications/${id}/read`).catch(() => {});
    setNotifications((prev) => prev.map((n) => (n._id === id ? { ...n, read: true } : n)));
  };

  const markAllRead = async () => {
    await api.put("/notifications/read-all").catch(() => {});
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  };

  const unreadCount = notifications.filter(n => !n.read).length;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40" onClick={onClose} />

      <div className="fixed right-0 top-0 h-screen w-[340px] z-50 flex flex-col border-l"
        style={{ background: "var(--bg)", borderColor: "var(--border)", boxShadow: "-8px 0 32px rgba(0,0,0,0.3)" }}
        data-testid="notification-panel">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3.5 border-b" style={{ borderColor: "var(--border)" }}>
          <div className="flex items-center gap-2">
            <Bell size={15} style={{ color: "var(--ai-accent)" }} />
            <h3 className="text-sm font-semibold" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>
              Notifications
            </h3>
            {unreadCount > 0 && (
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full min-w-[18px] text-center"
                style={{ background: "var(--loss)", color: "#fff" }}>
                {unreadCount}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            {unreadCount > 0 && (
              <button data-testid="mark-all-read-btn" onClick={markAllRead}
                className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-[10px] font-medium transition-all hover:opacity-80"
                style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}
                title="Mark all read">
                <CheckCheck size={12} /> Mark all read
              </button>
            )}
            <button data-testid="close-notifications-btn" onClick={onClose}
              className="p-1.5 rounded-lg transition-all hover:opacity-70" style={{ color: "var(--text-muted)" }}>
              <X size={14} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {loading ? (
            <div className="space-y-2 p-1">
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="h-[72px] rounded-xl animate-pulse" style={{ background: "var(--bg-surface)" }} />
              ))}
            </div>
          ) : notifications.length === 0 ? (
            <div className="flex flex-col items-center py-16 gap-3">
              <Bell size={28} className="opacity-20" style={{ color: "var(--text-muted)" }} />
              <p className="text-sm" style={{ color: "var(--text-muted)" }}>No notifications yet</p>
            </div>
          ) : (
            notifications.map((n) => {
              const cfg = TYPE_CONFIG[n.type] || TYPE_CONFIG.EOD_REPORT;
              const Icon = cfg.icon;
              return (
                <div
                  key={n._id}
                  data-testid={`notification-${n._id}`}
                  onClick={() => markRead(n._id)}
                  className="p-3 rounded-xl border cursor-pointer transition-all hover:scale-[1.01]"
                  style={{
                    borderColor: n.read ? "var(--border)" : cfg.color + "44",
                    background: n.read ? "transparent" : cfg.bg,
                    opacity: n.read ? 0.6 : 1,
                  }}>
                  <div className="flex items-start gap-3">
                    {/* Icon */}
                    <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
                      style={{ background: cfg.bg, border: `1px solid ${cfg.color}33` }}>
                      <Icon size={13} style={{ color: cfg.color }} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-semibold truncate" style={{ color: "var(--text-primary)" }}>
                          {n.title}
                        </span>
                        <div className="flex items-center gap-1.5 shrink-0">
                          {!n.read && <div className="w-1.5 h-1.5 rounded-full" style={{ background: cfg.color }} />}
                          <span className="text-[9px] font-mono" style={{ color: "var(--text-muted)" }}>
                            {timeAgo(n.created_at)}
                          </span>
                        </div>
                      </div>
                      <p className="text-[11px] mt-0.5 leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                        {n.message}
                      </p>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        {notifications.length > 0 && (
          <div className="p-3 border-t text-center" style={{ borderColor: "var(--border)" }}>
            <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
              {unreadCount > 0 ? `${unreadCount} unread notification${unreadCount > 1 ? "s" : ""}` : "All caught up ✓"}
            </p>
          </div>
        )}
      </div>
    </>
  );
}

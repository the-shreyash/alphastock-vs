/**
 * NotificationToast (Sprint R8) — global live toast host.
 *
 * Realizes the doc's flow `notification.created → toast slides in + badge
 * increments live` (REALTIME_SYSTEM.md §Animations: Slide Down → Fade →
 * Dismiss). Mounted once in Layout; fed purely by the realtime store:
 *   - `latestNotification`  — per-user pushes (AI / trade / morning-report /
 *     EOD alerts) created by the backend's create_notification helper.
 *   - `breakingNews`        — broadcast `news.breaking` batches.
 *
 * Toasts stack (newest on top, max 3), auto-dismiss, and can be dismissed by
 * click. Clicking the body navigates to the relevant surface.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  Bell, X, TrendingUp, TrendingDown, AlertTriangle, CheckCircle2, Zap, Radio,
} from "lucide-react";
import {
  useRealtimeStore, selectLatestNotification, selectBreakingNews,
} from "../../store/realtimeStore";

const MAX_VISIBLE = 3;
const AUTO_DISMISS_MS = 6000;
const BREAKING_DISMISS_MS = 9000;

const SEVERITY_CONFIG = {
  critical: { icon: AlertTriangle, color: "var(--loss)", bg: "rgba(244,63,94,0.12)" },
  warning: { icon: AlertTriangle, color: "#f59e0b", bg: "rgba(245,158,11,0.12)" },
  positive: { icon: CheckCircle2, color: "var(--gain)", bg: "rgba(16,185,129,0.12)" },
  info: { icon: Bell, color: "var(--ai-accent)", bg: "rgba(99,102,241,0.12)" },
};

// Route a notification type to the surface where the user acts on it.
const TYPE_ROUTE = {
  TRADE_ENTRY: "/trades",
  TARGET_HIT: "/trades",
  STOP_LOSS_HIT: "/trades",
  EXIT_REMINDER: "/trades",
  MORNING_REPORT: "/morning-report",
  EOD_REPORT: "/journal",
  WEEKLY_REVIEW: "/journal",
};

let toastSeq = 0;

function SentimentIcon({ sentiment }) {
  if (sentiment === "positive") return <TrendingUp size={11} style={{ color: "var(--gain)" }} />;
  if (sentiment === "negative") return <TrendingDown size={11} style={{ color: "var(--loss)" }} />;
  return null;
}

export default function NotificationToast() {
  const latestNotification = useRealtimeStore(selectLatestNotification);
  const breakingNews = useRealtimeStore(selectBreakingNews);
  const [toasts, setToasts] = useState([]);
  const timersRef = useRef({});
  const navigate = useNavigate();

  const dismiss = (id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    if (timersRef.current[id]) {
      clearTimeout(timersRef.current[id]);
      delete timersRef.current[id];
    }
  };

  const push = (toast, ttl) => {
    const id = `toast-${++toastSeq}`;
    setToasts((prev) => [{ ...toast, id }, ...prev].slice(0, MAX_VISIBLE));
    timersRef.current[id] = setTimeout(() => dismiss(id), ttl);
  };

  // Per-user notification pushes → severity-styled toast.
  useEffect(() => {
    if (!latestNotification) return;
    push(
      {
        kind: "notification",
        title: latestNotification.title || "Notification",
        message: latestNotification.message || "",
        severity: latestNotification.severity || "info",
        route: TYPE_ROUTE[latestNotification.type] || null,
      },
      AUTO_DISMISS_MS
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestNotification]);

  // Broadcast breaking-news batches → one toast per batch (lead headline).
  useEffect(() => {
    if (!breakingNews?.articles?.length) return;
    const lead = breakingNews.articles[0];
    push(
      {
        kind: "breaking",
        title: "Breaking News",
        message: lead.title,
        sentiment: lead.sentiment,
        extra: breakingNews.articles.length - 1,
        route: "/news",
      },
      BREAKING_DISMISS_MS
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [breakingNews]);

  // Clear timers on unmount.
  useEffect(() => () => {
    Object.values(timersRef.current).forEach(clearTimeout);
  }, []);

  return (
    <div
      className="fixed top-16 right-4 z-[70] flex flex-col gap-2 w-[320px] max-w-[calc(100vw-32px)] pointer-events-none"
      data-testid="notification-toast-host"
    >
      <AnimatePresence>
        {toasts.map((t) => {
          const cfg = t.kind === "breaking"
            ? { icon: Radio, color: "var(--loss)", bg: "rgba(244,63,94,0.12)" }
            : SEVERITY_CONFIG[t.severity] || SEVERITY_CONFIG.info;
          const Icon = t.kind === "breaking" && t.severity === "positive" ? Zap : cfg.icon;
          return (
            <motion.div
              key={t.id}
              layout
              initial={{ opacity: 0, y: -16, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.97 }}
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
              className="pointer-events-auto rounded-xl border overflow-hidden cursor-pointer"
              style={{
                background: "var(--bg-surface)",
                borderColor: `${cfg.color}44`,
                boxShadow: "0 8px 32px rgba(0,0,0,0.28)",
                backdropFilter: "blur(12px)",
              }}
              onClick={() => {
                if (t.route) navigate(t.route);
                dismiss(t.id);
              }}
              data-testid={`live-toast-${t.kind}`}
            >
              <div className="flex items-start gap-3 p-3">
                <div
                  className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
                  style={{ background: cfg.bg, border: `1px solid ${cfg.color}33` }}
                >
                  <Icon size={13} style={{ color: cfg.color }} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold truncate" style={{ color: "var(--text-primary)" }}>
                      {t.title}
                    </span>
                    {t.kind === "breaking" && (
                      <span className="text-[8px] font-bold px-1.5 py-0.5 rounded shrink-0"
                        style={{ background: "var(--loss-bg)", color: "var(--loss)" }}>
                        LIVE
                      </span>
                    )}
                    {t.sentiment && <SentimentIcon sentiment={t.sentiment} />}
                  </div>
                  <p className="text-[11px] mt-0.5 leading-relaxed line-clamp-2" style={{ color: "var(--text-secondary)" }}>
                    {t.message}
                  </p>
                  {t.extra > 0 && (
                    <p className="text-[10px] mt-1 font-medium" style={{ color: "var(--text-muted)" }}>
                      +{t.extra} more breaking {t.extra === 1 ? "headline" : "headlines"}
                    </p>
                  )}
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); dismiss(t.id); }}
                  className="p-1 rounded-lg shrink-0 transition-all hover:opacity-70"
                  style={{ color: "var(--text-muted)" }}
                  data-testid="dismiss-toast-btn"
                >
                  <X size={12} />
                </button>
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

import { useState, useEffect } from "react";
import api from "../../services/api";
import { Shield, AlertTriangle, TrendingUp, Activity, RefreshCw, Heart } from "lucide-react";
import { useRealtimeStore, selectConnected, selectPortfolioUpdate } from "../../store/realtimeStore";

const SEVERITY_STYLES = {
  critical: { color: "var(--loss)", bg: "rgba(244,63,94,0.08)", icon: AlertTriangle },
  positive: { color: "var(--gain)", bg: "rgba(16,185,129,0.08)", icon: TrendingUp },
  warning: { color: "#F59E0B", bg: "rgba(245,158,11,0.08)", icon: AlertTriangle },
  info: { color: "var(--ai-accent)", bg: "var(--ai-accent-soft)", icon: Activity },
};

export default function PortfolioMonitor() {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const connected = useRealtimeStore(selectConnected);
  const portfolioUpdate = useRealtimeStore(selectPortfolioUpdate);

  useEffect(() => {
    fetchHealth(); // seed on mount
    // While connected, we recompute on real portfolio_update pushes instead of
    // a fixed timer; keep the 60s poll only as a disconnected fallback.
    if (connected) return undefined;
    const interval = setInterval(fetchHealth, 60000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  // Recompute health when the live portfolio changes (event-triggered refetch).
  useEffect(() => {
    if (portfolioUpdate) fetchHealth();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [portfolioUpdate]);

  const fetchHealth = async () => {
    try {
      const { data } = await api.get("/monitor/health");
      setHealth(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const runManual = async () => {
    setRefreshing(true);
    try {
      const { data } = await api.post("/monitor/run");
      setHealth(data);
    } catch (err) {
      console.error(err);
    } finally {
      setRefreshing(false);
    }
  };

  if (loading) {
    return (
      <div className="glass-card p-5">
        <div className="h-4 w-48 rounded-lg animate-pulse mb-3" style={{ background: "var(--bg-surface)" }} />
        <div className="h-20 rounded-lg animate-pulse" style={{ background: "var(--bg-surface)" }} />
      </div>
    );
  }

  const score = health?.health_score ?? 100;
  const scoreColor = score >= 75 ? "var(--gain)" : score >= 50 ? "#F59E0B" : "var(--loss)";

  return (
    <div data-testid="portfolio-monitor" className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-bold uppercase tracking-[0.15em] flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
          <Shield size={12} style={{ color: scoreColor }} /> AI Portfolio Monitor
        </h3>
        <button data-testid="monitor-refresh-btn" onClick={runManual} style={{ color: "var(--text-muted)" }}>
          <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
        </button>
      </div>

      {/* Health Score */}
      <div className="flex items-center gap-4 mb-4">
        <div className="flex items-center gap-2">
          <Heart size={16} style={{ color: scoreColor }} />
          <span className="text-2xl font-mono font-bold" style={{ color: scoreColor }}>{score}</span>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>/100</span>
        </div>
        <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-surface)" }}>
          <div className="h-full rounded-full transition-all duration-500" style={{ width: `${score}%`, background: scoreColor }} />
        </div>
      </div>

      {/* Summary */}
      <p className="text-sm mb-4" style={{ color: "var(--text-secondary)" }}>{health?.summary}</p>

      {/* Alerts */}
      {health?.alerts?.length > 0 && (
        <div className="space-y-2 max-h-48 overflow-y-auto">
          {health.alerts.map((alert, i) => {
            const style = SEVERITY_STYLES[alert.severity] || SEVERITY_STYLES.info;
            const Icon = style.icon;
            return (
              <div key={i} data-testid={`monitor-alert-${i}`} className="flex items-start gap-2 p-2.5 rounded-xl" style={{ background: style.bg }}>
                <Icon size={14} className="shrink-0 mt-0.5" style={{ color: style.color }} />
                <div className="flex-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold" style={{ color: style.color }}>{alert.symbol}</span>
                    {alert.pnl != null && (
                      <span className="text-[10px] font-mono" style={{ color: alert.pnl >= 0 ? "var(--gain)" : "var(--loss)" }}>
                        {alert.pnl >= 0 ? "+" : ""}INR {alert.pnl.toFixed(2)}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] mt-0.5" style={{ color: "var(--text-secondary)" }}>{alert.message}</p>
                  <span className="text-[10px] font-medium mt-1 inline-block" style={{ color: style.color }}>{alert.action}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {health?.alerts?.length === 0 && (
        <div className="text-center py-4">
          <Shield size={24} className="mx-auto mb-2" style={{ color: "var(--gain)" }} />
          <p className="text-sm" style={{ color: "var(--gain)" }}>All positions healthy</p>
        </div>
      )}
    </div>
  );
}

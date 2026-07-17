import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Cpu, Activity, Clock, Wifi, WifiOff } from "lucide-react";
import api from "../../services/api";
import { useRealtimeStore, selectConnected, selectEngineStatus } from "../../store/realtimeStore";

export default function MarketEngineStatus({ compact = true }) {
  const [status, setStatus] = useState(null);
  const connected = useRealtimeStore(selectConnected);
  const liveStatus = useRealtimeStore(selectEngineStatus);

  useEffect(() => {
    const fetch = () => {
      api.get("/market/engine/status")
        .then((r) => setStatus(r.data))
        .catch(() => setStatus(null));
    };
    fetch(); // seed on mount
    // Live pushes (market.engine.status) cover updates while connected.
    if (connected) return undefined;
    const interval = setInterval(fetch, 30000);
    return () => clearInterval(interval);
  }, [connected]);

  // Prefer the live-pushed status when available.
  useEffect(() => { if (liveStatus) setStatus(liveStatus); }, [liveStatus]);

  if (!status) return null;

  if (compact) {
    return (
      <div className="flex items-center gap-1.5">
        <div className={`w-1.5 h-1.5 rounded-full ${status.initialized ? "bg-[var(--gain)]" : "bg-[var(--loss)]"}`} />
        <span className="text-[10px] text-[var(--text-muted)]">
          Engine {status.initialized ? "Online" : "Offline"}
        </span>
        {status.market_hours && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-[var(--gain)]/10 text-[var(--gain)] font-medium">
            LIVE
          </span>
        )}
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] p-4"
    >
      <div className="flex items-center gap-2 mb-3">
        <Cpu size={14} className="text-[var(--accent)]" />
        <span className="text-sm font-semibold text-[var(--text-primary)]">Market Engine</span>
        <div className={`ml-auto w-2 h-2 rounded-full ${status.initialized ? "bg-[var(--gain)] animate-pulse" : "bg-[var(--loss)]"}`} />
      </div>

      <div className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <div className="text-[var(--text-muted)] text-[10px]">Status</div>
          <div className="font-medium text-[var(--text-primary)] flex items-center gap-1">
            {status.initialized ? <Wifi size={10} className="text-[var(--gain)]" /> : <WifiOff size={10} className="text-[var(--loss)]" />}
            {status.initialized ? "Online" : "Offline"}
          </div>
        </div>
        <div>
          <div className="text-[var(--text-muted)] text-[10px]">Market</div>
          <div className="font-medium text-[var(--text-primary)]">
            {status.market_hours ? "Open" : status.pre_market ? "Pre-Market" : "Closed"}
          </div>
        </div>
        <div>
          <div className="text-[var(--text-muted)] text-[10px]">Subscribers</div>
          <div className="font-mono text-[var(--text-primary)]">{status.event_bus_subscribers || 0}</div>
        </div>
        <div>
          <div className="text-[var(--text-muted)] text-[10px]">Events</div>
          <div className="font-mono text-[var(--text-primary)]">{status.recent_events_count || 0}</div>
        </div>
      </div>

      {status.version && (
        <div className="mt-2 pt-2 border-t border-[var(--border)] text-[9px] text-[var(--text-muted)]">
          v{status.version}
        </div>
      )}
    </motion.div>
  );
}

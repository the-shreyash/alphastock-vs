/**
 * ScannerLiveFeed (Sprint R4) — the push-driven scanner hit feed.
 *
 * Renders the store's scanner slice: `scanner.breakout`, `scanner.volume_spike`
 * and `scanner.momentum` events streamed by the continuous scanner worker.
 * Hits are novelty-gated server-side, so every card is a NEW opportunity.
 * New cards play the doc's entrance (Slide Right → Fade → Glow → Settle) via
 * useCardEntrance; existing cards never re-animate. No fetching here — the
 * feed exists purely on pushed events.
 */
import { Activity, BarChart3, ArrowUpRight, Radar, WifiOff } from "lucide-react";
import {
  useRealtimeStore,
  selectScannerFeed,
  selectConnectionStatus,
} from "../../store/realtimeStore";
import { useCardEntrance } from "../../hooks/useCardEntrance";

const MAX_VISIBLE = 12;

const KIND_META = {
  breakout: { label: "Breakout", Icon: Activity, className: "bg-[var(--gain-bg)] text-[var(--gain)]" },
  volume_spike: { label: "Volume Spike", Icon: BarChart3, className: "bg-[var(--ai-accent-soft)] text-[var(--ai-accent)]" },
  momentum: { label: "Momentum", Icon: ArrowUpRight, className: "bg-[var(--gain-bg)] text-[var(--gain)]" },
};

function relativeTime(timestamp) {
  if (!timestamp) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(timestamp).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

function HitCard({ hit }) {
  const ref = useCardEntrance();
  const meta = KIND_META[hit.kind] || { label: hit.kind, Icon: Radar, className: "bg-[var(--bg-tertiary)] text-[var(--text-secondary)]" };
  const { Icon } = meta;

  return (
    <div
      ref={ref}
      className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-3"
    >
      <div className="flex items-center justify-between mb-2">
        <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full flex items-center gap-1 ${meta.className}`}>
          <Icon size={10} />
          {meta.label}
        </span>
        <span className="text-[9px] text-[var(--text-muted)]">{relativeTime(hit.timestamp)}</span>
      </div>
      <div className="space-y-1.5">
        {hit.candidates.slice(0, 3).map((c) => {
          const changePct = c.change_pct || 0;
          return (
            <div key={c.symbol} className="flex items-center justify-between text-xs">
              <span className="font-semibold text-[var(--text-primary)]">{c.symbol}</span>
              <span className="flex items-center gap-2 font-mono">
                {c.price != null && (
                  <span className="text-[var(--text-secondary)]">
                    {c.price.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                  </span>
                )}
                <span className={changePct >= 0 ? "text-[var(--gain)]" : "text-[var(--loss)]"}>
                  {changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%
                </span>
                {c.volume_ratio != null && (
                  <span className="text-[var(--text-muted)]">{c.volume_ratio.toFixed(1)}x</span>
                )}
              </span>
            </div>
          );
        })}
        {hit.count > 3 && (
          <div className="text-[9px] text-[var(--text-muted)]">
            +{hit.count - 3} more candidate{hit.count - 3 > 1 ? "s" : ""}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ScannerLiveFeed() {
  const feed = useRealtimeStore(selectScannerFeed);
  const status = useRealtimeStore(selectConnectionStatus);
  const live = status === "live";

  return (
    <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] overflow-hidden">
      <div className="px-4 py-3 border-b border-[var(--border)] flex items-center gap-2">
        <Radar size={14} className="text-[var(--ai-accent)]" />
        <span className="text-sm font-semibold text-[var(--text-primary)]">Live Scanner Hits</span>
        <div className="ml-auto flex items-center gap-1.5">
          <div className={`w-1.5 h-1.5 rounded-full ${live ? "bg-[var(--gain)] animate-pulse" : "bg-[var(--loss)]"}`} />
          <span className="text-[10px] text-[var(--text-muted)]">{live ? "Live" : "Offline"}</span>
        </div>
      </div>

      <div className="p-3 space-y-2 max-h-[560px] overflow-y-auto">
        {feed.length === 0 && (
          <div className="py-8 text-center">
            {live ? (
              <>
                <Radar size={22} className="mx-auto mb-2 text-[var(--text-muted)]" />
                <p className="text-xs text-[var(--text-muted)]">
                  Listening for live hits — breakouts, volume spikes and momentum will appear here.
                </p>
              </>
            ) : (
              <>
                <WifiOff size={22} className="mx-auto mb-2 text-[var(--text-muted)]" />
                <p className="text-xs text-[var(--text-muted)]">Live feed offline — reconnecting…</p>
              </>
            )}
          </div>
        )}
        {feed.slice(0, MAX_VISIBLE).map((hit) => (
          <HitCard key={hit.id} hit={hit} />
        ))}
      </div>
    </div>
  );
}

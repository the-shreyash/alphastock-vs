import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import {
  Layers,
  TrendingUp,
  TrendingDown,
  ArrowUpRight,
  ArrowDownRight,
  BarChart3,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Minus,
} from "lucide-react";
import api from "../../services/api";

const MOMENTUM_LABELS = {
  strong_bullish: { text: "Strong Bull", color: "var(--gain)" },
  bullish: { text: "Bullish", color: "var(--gain)" },
  neutral: { text: "Neutral", color: "var(--warning)" },
  bearish: { text: "Bearish", color: "var(--loss)" },
  strong_bearish: { text: "Strong Bear", color: "var(--loss)" },
};

export default function SectorAnalysis() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [expandedSector, setExpandedSector] = useState(null);

  const fetchData = () => {
    setLoading(true);
    api.get("/market/sector-analysis")
      .then((r) => setData(r.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] p-4">
        <div className="animate-pulse space-y-3">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-10 bg-[var(--bg-tertiary)] rounded-lg" />
          ))}
        </div>
      </div>
    );
  }

  if (!data?.available) return null;

  const sectors = data.sectors || [];
  const rotation = data.rotation || {};

  return (
    <div className="space-y-4">
      {/* Rotation summary */}
      {(rotation.inflow?.length > 0 || rotation.outflow?.length > 0) && (
        <div className="grid grid-cols-2 gap-3">
          {rotation.inflow?.length > 0 && (
            <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <ArrowUpRight size={12} className="text-[var(--gain)]" />
                <span className="text-[10px] font-medium text-[var(--gain)] uppercase tracking-wider">
                  Money Inflow
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {rotation.inflow.map((s) => (
                  <span key={s} className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--gain)]/10 text-[var(--gain)] font-medium">
                    {s}
                  </span>
                ))}
              </div>
            </div>
          )}
          {rotation.outflow?.length > 0 && (
            <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <ArrowDownRight size={12} className="text-[var(--loss)]" />
                <span className="text-[10px] font-medium text-[var(--loss)] uppercase tracking-wider">
                  Money Outflow
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {rotation.outflow.map((s) => (
                  <span key={s} className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--loss)]/10 text-[var(--loss)] font-medium">
                    {s}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Sector cards */}
      <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] overflow-hidden">
        <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Layers size={14} className="text-[var(--accent)]" />
            <span className="text-sm font-semibold text-[var(--text-primary)]">Sector Deep Dive</span>
            <span className="text-[10px] text-[var(--text-muted)]">{sectors.length} sectors</span>
          </div>
          <button onClick={fetchData} className="text-[var(--text-muted)] hover:text-[var(--accent)]">
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          </button>
        </div>

        {sectors.map((sector, i) => {
          const isExpanded = expandedSector === sector.name;
          const changePct = sector.change_pct || 0;
          const isPositive = changePct >= 0;
          const momentumConfig = MOMENTUM_LABELS[sector.momentum] || MOMENTUM_LABELS.neutral;
          const breadth = sector.breadth || {};

          return (
            <motion.div
              key={sector.name}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: i * 0.04 }}
            >
              {/* Sector row */}
              <button
                onClick={() => setExpandedSector(isExpanded ? null : sector.name)}
                className="w-full px-4 py-3 border-b border-[var(--border)] last:border-0 flex items-center gap-3 hover:bg-[var(--bg-hover)] transition-colors text-left"
              >
                {/* Strength indicator */}
                <div className="w-1 h-8 rounded-full shrink-0" style={{
                  backgroundColor: sector.strength_score > 60
                    ? "var(--gain)"
                    : sector.strength_score > 40
                    ? "var(--warning)"
                    : "var(--loss)",
                }} />

                {/* Name + momentum */}
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-semibold text-[var(--text-primary)]">{sector.name}</div>
                  <div className="text-[10px] flex items-center gap-1.5 mt-0.5">
                    <span style={{ color: momentumConfig.color }}>{momentumConfig.text}</span>
                    <span className="text-[var(--text-muted)]">
                      Strength {sector.strength_score}/100
                    </span>
                  </div>
                </div>

                {/* Change */}
                <div className={`text-right font-mono text-xs font-medium ${isPositive ? "text-[var(--gain)]" : "text-[var(--loss)]"}`}>
                  {changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%
                </div>

                {/* Breadth mini bar */}
                <div className="w-16 h-1.5 rounded-full bg-[var(--bg-tertiary)] overflow-hidden shrink-0">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${(breadth.advance_ratio || 0.5) * 100}%`,
                      backgroundColor: (breadth.advance_ratio || 0.5) > 0.5 ? "var(--gain)" : "var(--loss)",
                    }}
                  />
                </div>

                {/* Expand icon */}
                {isExpanded ? <ChevronUp size={12} className="text-[var(--text-muted)]" /> : <ChevronDown size={12} className="text-[var(--text-muted)]" />}
              </button>

              {/* Expanded detail */}
              {isExpanded && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  className="px-4 py-3 bg-[var(--bg-tertiary)]/50 border-b border-[var(--border)]"
                >
                  <div className="grid grid-cols-3 gap-3 text-[10px]">
                    {/* Breadth */}
                    <div>
                      <div className="text-[var(--text-muted)] uppercase tracking-wider mb-1">Breadth</div>
                      <div className="space-y-0.5">
                        <div className="flex justify-between">
                          <span className="text-[var(--gain)]">Advancing</span>
                          <span className="font-mono">{breadth.advancing || 0}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-[var(--loss)]">Declining</span>
                          <span className="font-mono">{breadth.declining || 0}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-[var(--text-muted)]">Unchanged</span>
                          <span className="font-mono">{breadth.unchanged || 0}</span>
                        </div>
                      </div>
                    </div>

                    {/* Leaders */}
                    <div>
                      <div className="text-[var(--text-muted)] uppercase tracking-wider mb-1">Leaders</div>
                      {(sector.leaders || []).map((l) => (
                        <div key={l.symbol} className="flex justify-between">
                          <span className="text-[var(--text-primary)]">{l.symbol}</span>
                          <span className={`font-mono ${(l.change_pct || 0) >= 0 ? "text-[var(--gain)]" : "text-[var(--loss)]"}`}>
                            {(l.change_pct || 0) >= 0 ? "+" : ""}{(l.change_pct || 0).toFixed(1)}%
                          </span>
                        </div>
                      ))}
                    </div>

                    {/* Laggards */}
                    <div>
                      <div className="text-[var(--text-muted)] uppercase tracking-wider mb-1">Laggards</div>
                      {(sector.laggards || []).map((l) => (
                        <div key={l.symbol} className="flex justify-between">
                          <span className="text-[var(--text-primary)]">{l.symbol}</span>
                          <span className={`font-mono ${(l.change_pct || 0) >= 0 ? "text-[var(--gain)]" : "text-[var(--loss)]"}`}>
                            {(l.change_pct || 0) >= 0 ? "+" : ""}{(l.change_pct || 0).toFixed(1)}%
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </motion.div>
              )}
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

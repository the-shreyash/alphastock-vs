import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import {
  Trophy,
  ArrowUpRight,
  ArrowDownRight,
  RefreshCw,
  TrendingUp,
  Shield,
  BarChart3,
  Activity,
  Newspaper,
  Layers,
  Droplets,
  Brain,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import api from "../../services/api";

const SIGNAL_CONFIG = {
  strong_buy: { label: "Strong Buy", color: "#10b981", bg: "#10b98120" },
  buy: { label: "Buy", color: "#22c55e", bg: "#22c55e20" },
  neutral: { label: "Neutral", color: "#f59e0b", bg: "#f59e0b20" },
  sell: { label: "Sell", color: "#ef4444", bg: "#ef444420" },
  strong_sell: { label: "Strong Sell", color: "#dc2626", bg: "#dc262620" },
};

const DIMENSION_ICONS = {
  momentum: TrendingUp,
  trend: Activity,
  volume: BarChart3,
  risk: Shield,
  news: Newspaper,
  sector: Layers,
  liquidity: Droplets,
  ai_confidence: Brain,
};

export default function RankingTable({ compact = false }) {
  const [rankings, setRankings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedRow, setExpandedRow] = useState(null);
  const [sector, setSector] = useState("");

  const fetchRankings = () => {
    setLoading(true);
    const params = { top_n: compact ? 5 : 10 };
    if (sector) params.sector = sector;
    api.get("/market/ranking", { params })
      .then((r) => setRankings(r.data.rankings || []))
      .catch(() => setRankings([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchRankings();
  }, [sector]);

  if (loading) {
    return (
      <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] p-4">
        <div className="animate-pulse space-y-3">
          {[...Array(compact ? 3 : 5)].map((_, i) => (
            <div key={i} className="h-10 bg-[var(--bg-tertiary)] rounded-lg" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Trophy size={14} className="text-[var(--accent)]" />
          <span className="text-sm font-semibold text-[var(--text-primary)]">
            {compact ? "Top Opportunities" : "Stock Rankings"}
          </span>
          <span className="text-[10px] text-[var(--text-muted)]">{rankings.length} stocks</span>
        </div>
        <div className="flex items-center gap-2">
          {!compact && (
            <select
              value={sector}
              onChange={(e) => setSector(e.target.value)}
              className="text-[10px] px-2 py-1 rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border border-[var(--border)]"
            >
              <option value="">All Sectors</option>
              {["Banking", "IT", "Pharma", "Auto", "FMCG", "Oil & Gas", "Metals", "Power"].map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          )}
          <button onClick={fetchRankings} className="text-[var(--text-muted)] hover:text-[var(--accent)]">
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* Rankings */}
      {rankings.length === 0 ? (
        <div className="p-6 text-center text-[var(--text-muted)] text-xs">
          No ranked opportunities available
        </div>
      ) : (
        rankings.map((stock, i) => {
          const signal = SIGNAL_CONFIG[stock.signal] || SIGNAL_CONFIG.neutral;
          const isExpanded = expandedRow === stock.symbol;
          const changePct = stock.change_pct || 0;
          const isPositive = changePct >= 0;

          return (
            <div key={stock.symbol}>
              <motion.button
                initial={{ opacity: 0, y: 5 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.04 }}
                onClick={() => setExpandedRow(isExpanded ? null : stock.symbol)}
                className="w-full px-4 py-2.5 border-b border-[var(--border)] last:border-0 flex items-center gap-3 hover:bg-[var(--bg-hover)] transition-colors text-left"
              >
                {/* Rank badge */}
                <div className="w-5 h-5 rounded-full bg-[var(--bg-tertiary)] flex items-center justify-center text-[10px] font-bold text-[var(--text-muted)] shrink-0">
                  {i + 1}
                </div>

                {/* Stock info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-[var(--text-primary)]">{stock.symbol}</span>
                    <span className={`text-[10px] font-mono ${isPositive ? "text-[var(--gain)]" : "text-[var(--loss)]"}`}>
                      {changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%
                    </span>
                  </div>
                  <div className="text-[10px] text-[var(--text-muted)] truncate">{stock.name}</div>
                </div>

                {/* Opportunity score */}
                <div className="text-center shrink-0">
                  <div className="text-sm font-bold text-[var(--text-primary)] font-mono">
                    {stock.opportunity_score}
                  </div>
                  <div className="text-[8px] text-[var(--text-muted)] uppercase">Score</div>
                </div>

                {/* Signal badge */}
                <span
                  className="text-[9px] font-semibold px-2 py-0.5 rounded-full shrink-0"
                  style={{ backgroundColor: signal.bg, color: signal.color }}
                >
                  {signal.label}
                </span>

                {!compact && (
                  isExpanded
                    ? <ChevronUp size={12} className="text-[var(--text-muted)] shrink-0" />
                    : <ChevronDown size={12} className="text-[var(--text-muted)] shrink-0" />
                )}
              </motion.button>

              {/* Expanded dimensions */}
              {!compact && isExpanded && stock.dimensions && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  className="px-4 py-3 bg-[var(--bg-tertiary)]/50 border-b border-[var(--border)]"
                >
                  <div className="grid grid-cols-4 gap-2">
                    {Object.entries(stock.dimensions).map(([dim, info]) => {
                      const DimIcon = DIMENSION_ICONS[dim] || Activity;
                      const score = info.score || 0;
                      return (
                        <div key={dim} className="text-center">
                          <DimIcon size={12} className="mx-auto mb-1 text-[var(--text-muted)]" />
                          <div className="text-[10px] font-medium text-[var(--text-primary)] capitalize">
                            {dim.replace("_", " ")}
                          </div>
                          <div
                            className="text-sm font-bold font-mono"
                            style={{
                              color: score >= 65 ? "var(--gain)" : score >= 40 ? "var(--warning)" : "var(--loss)",
                            }}
                          >
                            {score.toFixed(0)}
                          </div>
                          <div className="text-[8px] text-[var(--text-muted)] mt-0.5 line-clamp-2">
                            {info.reason}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </motion.div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

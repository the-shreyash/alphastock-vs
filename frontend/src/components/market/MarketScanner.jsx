import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Search,
  SlidersHorizontal,
  TrendingUp,
  TrendingDown,
  Zap,
  RotateCcw,
  ChevronDown,
  Activity,
  BarChart3,
  Target,
  ArrowUpRight,
  ArrowDownRight,
  Filter,
  X,
} from "lucide-react";
import api from "../../services/api";

const STRATEGY_ICONS = {
  intraday: Zap,
  swing: TrendingUp,
  momentum: ArrowUpRight,
  breakout: Activity,
  reversal: RotateCcw,
  value: Target,
  growth: BarChart3,
  dividend: TrendingDown,
};

export default function MarketScanner() {
  const [presets, setPresets] = useState([]);
  const [activeStrategy, setActiveStrategy] = useState(null);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [sector, setSector] = useState("");
  const [customFilters, setCustomFilters] = useState({});

  const SECTORS = [
    "Banking", "IT", "Pharma", "Auto", "FMCG", "Oil & Gas",
    "Metals", "Power", "Telecom", "Infrastructure", "Finance", "Consumer",
  ];

  useEffect(() => {
    api.get("/market/scanner/presets")
      .then((r) => setPresets(r.data.presets || []))
      .catch(() => {});
  }, []);

  const runScan = useCallback(async (strategy) => {
    setLoading(true);
    setActiveStrategy(strategy);
    try {
      const params = { limit: 15 };
      if (strategy) params.strategy = strategy;
      if (sector) params.sector = sector;
      Object.entries(customFilters).forEach(([k, v]) => {
        if (v !== "" && v !== null && v !== undefined) params[k] = v;
      });
      const { data } = await api.get("/market/scanner", { params });
      setResults(data);
    } catch {
      setResults(null);
    } finally {
      setLoading(false);
    }
  }, [sector, customFilters]);

  useEffect(() => {
    runScan(null);
  }, [runScan]);

  return (
    <div className="space-y-4">
      {/* Strategy presets */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => runScan(null)}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
            !activeStrategy
              ? "bg-[var(--accent)] text-white"
              : "bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
          }`}
        >
          All Stocks
        </button>
        {presets.map((p) => {
          const Icon = STRATEGY_ICONS[p.key] || Activity;
          return (
            <button
              key={p.key}
              onClick={() => runScan(p.key)}
              title={p.description}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 ${
                activeStrategy === p.key
                  ? "bg-[var(--accent)] text-white"
                  : "bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
              }`}
            >
              <Icon size={12} />
              {p.label}
            </button>
          );
        })}

        <button
          onClick={() => setShowFilters(!showFilters)}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] flex items-center gap-1.5 ml-auto"
        >
          <Filter size={12} />
          Filters
          <ChevronDown size={12} className={`transition-transform ${showFilters ? "rotate-180" : ""}`} />
        </button>
      </div>

      {/* Custom filters panel */}
      <AnimatePresence>
        {showFilters && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-4 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)]">
              <div>
                <label className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Sector</label>
                <select
                  value={sector}
                  onChange={(e) => setSector(e.target.value)}
                  className="w-full mt-1 px-2 py-1.5 rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-primary)] text-xs border border-[var(--border)]"
                >
                  <option value="">All Sectors</option>
                  {SECTORS.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">RSI Min</label>
                <input
                  type="number"
                  placeholder="e.g. 30"
                  value={customFilters.rsi_min || ""}
                  onChange={(e) => setCustomFilters({ ...customFilters, rsi_min: e.target.value })}
                  className="w-full mt-1 px-2 py-1.5 rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-primary)] text-xs border border-[var(--border)]"
                />
              </div>
              <div>
                <label className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">RSI Max</label>
                <input
                  type="number"
                  placeholder="e.g. 70"
                  value={customFilters.rsi_max || ""}
                  onChange={(e) => setCustomFilters({ ...customFilters, rsi_max: e.target.value })}
                  className="w-full mt-1 px-2 py-1.5 rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-primary)] text-xs border border-[var(--border)]"
                />
              </div>
              <div>
                <label className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Min Vol Ratio</label>
                <input
                  type="number"
                  step="0.1"
                  placeholder="e.g. 1.5"
                  value={customFilters.volume_ratio_min || ""}
                  onChange={(e) => setCustomFilters({ ...customFilters, volume_ratio_min: e.target.value })}
                  className="w-full mt-1 px-2 py-1.5 rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-primary)] text-xs border border-[var(--border)]"
                />
              </div>
              <div className="col-span-2 sm:col-span-4 flex gap-2 pt-1">
                <button
                  onClick={() => runScan(activeStrategy)}
                  className="px-4 py-1.5 rounded-lg bg-[var(--accent)] text-white text-xs font-medium hover:opacity-90 flex items-center gap-1"
                >
                  <Search size={12} /> Apply Filters
                </button>
                <button
                  onClick={() => {
                    setCustomFilters({});
                    setSector("");
                    runScan(activeStrategy);
                  }}
                  className="px-4 py-1.5 rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-secondary)] text-xs hover:bg-[var(--bg-hover)] flex items-center gap-1"
                >
                  <X size={12} /> Clear
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Results */}
      <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] overflow-hidden">
        {/* Header */}
        <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <SlidersHorizontal size={14} className="text-[var(--accent)]" />
            <span className="text-sm font-semibold text-[var(--text-primary)]">
              {results?.strategy_label || "Scanner Results"}
            </span>
            {results && (
              <span className="text-[10px] text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2 py-0.5 rounded-full">
                {results.total_matched}/{results.total_scanned} matched
              </span>
            )}
          </div>
          <button
            onClick={() => runScan(activeStrategy)}
            disabled={loading}
            className="text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
          >
            <RotateCcw size={14} className={loading ? "animate-spin" : ""} />
          </button>
        </div>

        {/* Loading state */}
        {loading && (
          <div className="p-8 text-center">
            <div className="w-6 h-6 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin mx-auto mb-2" />
            <p className="text-xs text-[var(--text-muted)]">Scanning market...</p>
          </div>
        )}

        {/* Results table */}
        {!loading && results?.results?.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[var(--text-muted)] border-b border-[var(--border)]">
                  <th className="text-left px-4 py-2 font-medium">Stock</th>
                  <th className="text-right px-3 py-2 font-medium">Price</th>
                  <th className="text-right px-3 py-2 font-medium">Change</th>
                  <th className="text-right px-3 py-2 font-medium">RSI</th>
                  <th className="text-right px-3 py-2 font-medium">Vol Ratio</th>
                  <th className="text-left px-3 py-2 font-medium">Sector</th>
                </tr>
              </thead>
              <tbody>
                {results.results.map((stock, i) => {
                  const changePct = stock.change_pct || 0;
                  const isPositive = changePct >= 0;
                  return (
                    <motion.tr
                      key={stock.symbol}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: i * 0.03 }}
                      className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--bg-hover)] transition-colors cursor-pointer"
                    >
                      <td className="px-4 py-2.5">
                        <div className="font-semibold text-[var(--text-primary)]">{stock.symbol}</div>
                        <div className="text-[10px] text-[var(--text-muted)] truncate max-w-[120px]">
                          {stock.name}
                        </div>
                      </td>
                      <td className="text-right px-3 py-2.5 font-mono text-[var(--text-primary)]">
                        {stock.price?.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                      </td>
                      <td className={`text-right px-3 py-2.5 font-mono font-medium ${isPositive ? "text-[var(--gain)]" : "text-[var(--loss)]"}`}>
                        <span className="flex items-center justify-end gap-0.5">
                          {isPositive ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />}
                          {changePct >= 0 ? "+" : ""}{changePct?.toFixed(2)}%
                        </span>
                      </td>
                      <td className="text-right px-3 py-2.5 font-mono text-[var(--text-secondary)]">
                        {stock.rsi?.toFixed(0) || "—"}
                      </td>
                      <td className="text-right px-3 py-2.5 font-mono text-[var(--text-secondary)]">
                        {stock.volume_ratio?.toFixed(1) || "—"}x
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--bg-tertiary)] text-[var(--text-muted)]">
                          {stock.sector || "—"}
                        </span>
                      </td>
                    </motion.tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Empty state */}
        {!loading && results && results.results?.length === 0 && (
          <div className="p-8 text-center">
            <Search size={24} className="mx-auto mb-2 text-[var(--text-muted)]" />
            <p className="text-sm text-[var(--text-muted)]">No stocks match the current filters</p>
            <p className="text-[10px] text-[var(--text-muted)] mt-1">Try adjusting your filters or strategy</p>
          </div>
        )}
      </div>
    </div>
  );
}

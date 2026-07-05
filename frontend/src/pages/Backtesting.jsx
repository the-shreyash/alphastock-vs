import { useState } from "react";
import api from "../services/api";
import {
  BarChart3, Play, TrendingUp, TrendingDown, Minus, Award,
  AlertTriangle, RefreshCw, Info, ChevronDown,
} from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell, ReferenceLine,
} from "recharts";

const STRATEGIES = [
  { value: "RSI_STRATEGY", label: "RSI Strategy", desc: "Buy oversold (RSI<30), sell overbought" },
  { value: "EMA_CROSSOVER", label: "EMA Crossover", desc: "EMA9 × EMA21 golden/death cross" },
  { value: "VWAP_REVERSION", label: "VWAP Reversion", desc: "Buy dips below VWAP with volume spike" },
  { value: "MACD_SIGNAL", label: "MACD Signal", desc: "Histogram positive/negative crossovers" },
];

const NSE_SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL", "AXISBANK", "MARUTI", "TATAMOTORS"];

function MetricCard({ label, value, sub, color, icon: Icon }) {
  return (
    <div className="stat-card">
      <div className="flex items-start justify-between mb-1">
        <span className="text-[10px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--text-muted)" }}>{label}</span>
        {Icon && <Icon size={14} style={{ color: color || "var(--text-muted)" }} />}
      </div>
      <div className="text-xl font-semibold font-mono" style={{ color: color || "var(--text-primary)" }}>{value}</div>
      {sub && <div className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>{sub}</div>}
    </div>
  );
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="px-3 py-2 rounded-xl text-xs shadow-xl" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
      <p style={{ color: "var(--text-muted)" }}>{label}</p>
      <p className="font-mono font-semibold" style={{ color: "var(--ai-accent)" }}>
        ₹{payload[0].value?.toLocaleString("en-IN")}
      </p>
    </div>
  );
};

export default function Backtesting() {
  const [form, setForm] = useState({
    symbol: "RELIANCE",
    strategy: "RSI_STRATEGY",
    start_date: "2024-01-01",
    end_date: "2024-12-31",
    stop_loss_pct: 2,
    target_pct: 4,
    initial_capital: 100000,
  });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [aiVerdict, setAiVerdict] = useState(null);
  const [aiLoading, setAiLoading] = useState(false);

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const runBacktest = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    setAiVerdict(null);
    try {
      const res = await api.post("/backtest", {
        ...form,
        stop_loss_pct: parseFloat(form.stop_loss_pct),
        target_pct: parseFloat(form.target_pct),
        initial_capital: parseFloat(form.initial_capital),
      });
      setResult(res.data);
    } catch (e) {
      setError(e.response?.data?.detail || "Backtest failed. Check symbol and dates.");
    } finally {
      setLoading(false);
    }
  };

  const getAiVerdict = async () => {
    if (!result) return;
    setAiLoading(true);
    try {
      const prompt = `Analyze this backtest result for ${result.symbol} using ${result.strategy}:
Win Rate: ${result.win_rate}% | Total Return: ${result.total_return_pct}% | Max Drawdown: ${result.max_drawdown_pct}%
Sharpe Ratio: ${result.sharpe_ratio} | Total Trades: ${result.total_trades} | Avg Trade: ${result.avg_trade_pct}%
Period: ${result.period?.start} to ${result.period?.end}

Is this strategy viable for live trading? What are the key risks? What improvements would you suggest? Keep it under 150 words.`;
      const res = await api.post("/chat", { message: prompt, session_id: "backtest-analysis" });
      setAiVerdict(res.data.response || res.data.message || "No verdict generated.");
    } catch { setAiVerdict("AI verdict unavailable. Please check AI provider configuration."); }
    finally { setAiLoading(false); }
  };

  const strategyInfo = STRATEGIES.find(s => s.value === form.strategy);
  const pnlColor = (v) => v > 0 ? "var(--gain)" : v < 0 ? "var(--loss)" : "var(--text-muted)";

  return (
    <div className="space-y-5" data-testid="backtesting-page">
      {/* Header */}
      <div>
        <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight font-display" style={{ color: "var(--text-primary)" }}>
          Backtesting
        </h1>
        <p className="text-[13px] mt-0.5" style={{ color: "var(--text-secondary)" }}>
          Test trading strategies on historical NSE data
        </p>
      </div>

      {/* Config Panel */}
      <div className="glass-card p-6 space-y-5">
        <h3 className="text-xs font-bold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>Strategy Configuration</h3>

        {/* Row 1: Symbol + Strategy */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="text-[10px] font-bold uppercase tracking-widest mb-1.5 block" style={{ color: "var(--text-muted)" }}>NSE Symbol</label>
            <input
              value={form.symbol} onChange={e => set("symbol", e.target.value.toUpperCase())}
              placeholder="RELIANCE"
              list="nse-symbols"
              className="w-full px-3 py-2.5 rounded-xl text-sm font-mono"
              style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
            <datalist id="nse-symbols">
              {NSE_SYMBOLS.map(s => <option key={s} value={s} />)}
            </datalist>
          </div>
          <div>
            <label className="text-[10px] font-bold uppercase tracking-widest mb-1.5 block" style={{ color: "var(--text-muted)" }}>Strategy</label>
            <div className="relative">
              <select value={form.strategy} onChange={e => set("strategy", e.target.value)}
                className="w-full px-3 py-2.5 rounded-xl text-sm appearance-none"
                style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                {STRATEGIES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
              <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--text-muted)" }} />
            </div>
            {strategyInfo && (
              <p className="text-[10px] mt-1" style={{ color: "var(--text-muted)" }}>{strategyInfo.desc}</p>
            )}
          </div>
        </div>

        {/* Row 2: Date range */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {[{ key: "start_date", label: "Start Date" }, { key: "end_date", label: "End Date" }].map(f => (
            <div key={f.key}>
              <label className="text-[10px] font-bold uppercase tracking-widest mb-1.5 block" style={{ color: "var(--text-muted)" }}>{f.label}</label>
              <input type="date" value={form[f.key]} onChange={e => set(f.key, e.target.value)}
                className="w-full px-3 py-2.5 rounded-xl text-sm"
                style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>
          ))}
        </div>

        {/* Row 3: Sliders + Capital */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="text-[10px] font-bold uppercase tracking-widest mb-1.5 flex justify-between" style={{ color: "var(--text-muted)" }}>
              Stop Loss % <span style={{ color: "var(--loss)" }}>{form.stop_loss_pct}%</span>
            </label>
            <input type="range" min="0.5" max="5" step="0.5" value={form.stop_loss_pct}
              onChange={e => set("stop_loss_pct", e.target.value)}
              className="w-full accent-red-400" />
          </div>
          <div>
            <label className="text-[10px] font-bold uppercase tracking-widest mb-1.5 flex justify-between" style={{ color: "var(--text-muted)" }}>
              Target % <span style={{ color: "var(--gain)" }}>{form.target_pct}%</span>
            </label>
            <input type="range" min="1" max="10" step="0.5" value={form.target_pct}
              onChange={e => set("target_pct", e.target.value)}
              className="w-full accent-green-400" />
          </div>
          <div>
            <label className="text-[10px] font-bold uppercase tracking-widest mb-1.5 block" style={{ color: "var(--text-muted)" }}>Initial Capital (₹)</label>
            <input type="number" value={form.initial_capital} onChange={e => set("initial_capital", e.target.value)}
              className="w-full px-3 py-2 rounded-xl text-sm font-mono"
              style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          </div>
        </div>

        <button onClick={runBacktest} disabled={loading}
          className="flex items-center gap-2 px-6 py-3 rounded-xl text-sm font-semibold transition-all"
          style={{ background: "var(--ai-accent)", color: "#fff", opacity: loading ? 0.75 : 1 }}>
          {loading ? <RefreshCw size={16} className="animate-spin" /> : <Play size={16} />}
          {loading ? "Running Backtest..." : "Run Backtest"}
        </button>

        {error && (
          <div className="flex items-center gap-2 text-sm p-3 rounded-xl" style={{ background: "rgba(248,113,113,0.1)", color: "var(--loss)" }}>
            <AlertTriangle size={14} /> {error}
          </div>
        )}
      </div>

      {/* Loading skeleton */}
      {loading && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[1, 2, 3, 4].map(i => <div key={i} className="h-24 rounded-2xl animate-pulse" style={{ background: "var(--bg-surface)" }} />)}
          </div>
          <div className="h-64 rounded-2xl animate-pulse" style={{ background: "var(--bg-surface)" }} />
        </div>
      )}

      {/* Results */}
      {result && !loading && (
        <div className="space-y-5">
          {/* Data source note */}
          {result.data_source === "synthetic" && (
            <div className="flex items-center gap-2 text-xs p-3 rounded-xl" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
              <Info size={13} /> Using simulated historical data (yfinance unavailable). Install it with: <code className="font-mono">pip install yfinance</code>
            </div>
          )}

          {/* Metric Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <MetricCard
              label="Win Rate"
              value={`${result.win_rate}%`}
              sub={`${result.winning_trades}W / ${result.losing_trades}L`}
              color={result.win_rate >= 50 ? "var(--gain)" : "var(--loss)"}
              icon={result.win_rate >= 50 ? TrendingUp : TrendingDown}
            />
            <MetricCard
              label="Total Return"
              value={`${result.total_return_pct >= 0 ? "+" : ""}${result.total_return_pct}%`}
              sub={`₹${result.initial_capital?.toLocaleString("en-IN")} → ₹${result.final_capital?.toLocaleString("en-IN")}`}
              color={pnlColor(result.total_return_pct)}
              icon={result.total_return_pct >= 0 ? TrendingUp : TrendingDown}
            />
            <MetricCard
              label="Max Drawdown"
              value={`-${result.max_drawdown_pct}%`}
              sub="Peak to trough"
              color="var(--loss)"
              icon={AlertTriangle}
            />
            <MetricCard
              label="Sharpe Ratio"
              value={result.sharpe_ratio}
              sub={result.sharpe_ratio > 1 ? "Good risk-adjusted returns" : result.sharpe_ratio > 0 ? "Acceptable" : "Poor"}
              color={result.sharpe_ratio > 1 ? "var(--gain)" : result.sharpe_ratio > 0 ? "#f59e0b" : "var(--loss)"}
              icon={Award}
            />
          </div>

          {/* Secondary metrics */}
          <div className="grid grid-cols-3 gap-3">
            <div className="glass-card p-4 text-center">
              <p className="text-[10px] uppercase tracking-widest mb-1" style={{ color: "var(--text-muted)" }}>Best Trade</p>
              <p className="text-lg font-mono font-semibold" style={{ color: "var(--gain)" }}>+{result.best_trade_pct}%</p>
            </div>
            <div className="glass-card p-4 text-center">
              <p className="text-[10px] uppercase tracking-widest mb-1" style={{ color: "var(--text-muted)" }}>Worst Trade</p>
              <p className="text-lg font-mono font-semibold" style={{ color: "var(--loss)" }}>{result.worst_trade_pct}%</p>
            </div>
            <div className="glass-card p-4 text-center">
              <p className="text-[10px] uppercase tracking-widest mb-1" style={{ color: "var(--text-muted)" }}>Avg Trade</p>
              <p className="text-lg font-mono font-semibold" style={{ color: pnlColor(result.avg_trade_pct) }}>
                {result.avg_trade_pct >= 0 ? "+" : ""}{result.avg_trade_pct}%
              </p>
            </div>
          </div>

          {/* Equity Curve */}
          {result.equity_curve?.length > 1 && (
            <div className="glass-card p-5">
              <h3 className="text-xs font-bold uppercase tracking-widest mb-4" style={{ color: "var(--text-muted)" }}>
                Equity Curve — {result.symbol} · {result.strategy.replace(/_/g, " ")}
              </h3>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={result.equity_curve} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickLine={false}
                    tickFormatter={v => v.slice(5)} interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickLine={false} axisLine={false}
                    tickFormatter={v => `₹${(v / 1000).toFixed(0)}k`} />
                  <Tooltip content={<CustomTooltip />} />
                  <ReferenceLine y={result.initial_capital} stroke="var(--border)" strokeDasharray="4 4" />
                  <Line type="monotone" dataKey="capital" stroke="var(--ai-accent)" strokeWidth={2}
                    dot={false} activeDot={{ r: 4, fill: "var(--ai-accent)" }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Trades Table */}
          {result.trades?.length > 0 && (
            <div className="glass-card p-5">
              <h3 className="text-xs font-bold uppercase tracking-widest mb-3" style={{ color: "var(--text-muted)" }}>
                Trade Log ({result.trades.length} shown)
              </h3>
              <div className="overflow-x-auto max-h-72 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0" style={{ background: "var(--bg-surface)" }}>
                    <tr style={{ borderBottom: "1px solid var(--border)" }}>
                      {["Entry Date", "Exit Date", "Entry ₹", "Exit ₹", "Shares", "P&L ₹", "P&L %", "Result"].map(h => (
                        <th key={h} className="text-left py-2 px-2 font-bold uppercase tracking-widest" style={{ color: "var(--text-muted)", fontSize: "9px" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td className="py-2 px-2 font-mono" style={{ color: "var(--text-muted)" }}>{t.entry_date}</td>
                        <td className="py-2 px-2 font-mono" style={{ color: "var(--text-muted)" }}>{t.exit_date}</td>
                        <td className="py-2 px-2 font-mono" style={{ color: "var(--text-primary)" }}>₹{t.entry_price}</td>
                        <td className="py-2 px-2 font-mono" style={{ color: "var(--text-primary)" }}>₹{t.exit_price}</td>
                        <td className="py-2 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{t.shares}</td>
                        <td className="py-2 px-2 font-mono font-semibold" style={{ color: pnlColor(t.pnl) }}>
                          {t.pnl >= 0 ? "+" : ""}₹{t.pnl?.toFixed(0)}
                        </td>
                        <td className="py-2 px-2 font-mono font-semibold" style={{ color: pnlColor(t.pnl_pct) }}>
                          {t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct?.toFixed(2)}%
                        </td>
                        <td className="py-2 px-2">
                          <span className="font-bold text-[9px] px-2 py-0.5 rounded-full"
                            style={{ background: t.result === "WIN" ? "rgba(52,211,153,0.15)" : "rgba(248,113,113,0.15)", color: t.result === "WIN" ? "var(--gain)" : "var(--loss)" }}>
                            {t.result}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* AI Analysis */}
          <div className="glass-card p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-bold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>AI Strategy Verdict</h3>
              <button onClick={getAiVerdict} disabled={aiLoading}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
                style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
                {aiLoading ? <RefreshCw size={12} className="animate-spin" /> : <BarChart3 size={12} />}
                {aiLoading ? "Analyzing..." : "Get AI Verdict"}
              </button>
            </div>
            {aiVerdict ? (
              <p className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>{aiVerdict}</p>
            ) : (
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                Click "Get AI Verdict" to have Claude/Gemini analyze whether this strategy is viable for live trading.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

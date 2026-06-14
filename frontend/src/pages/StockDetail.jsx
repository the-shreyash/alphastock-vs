import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import api from "../services/api";
import { formatCurrency, formatNumber, formatPercent } from "../utils/formatters";
import {
  ArrowLeft, TrendingUp, TrendingDown, BarChart3, Activity, Info,
  Zap, ShieldCheck, ShieldAlert, Minus, Eye, RefreshCw,
} from "lucide-react";
import TradingChart from "../components/charts/TradingChart";

// ─── Pattern Signal Colours ───────────────────────────────────
const SIGNAL_STYLE = {
  bullish: { color: "var(--gain)", bg: "rgba(52,211,153,0.12)", label: "Bullish" },
  bearish: { color: "var(--loss)", bg: "rgba(248,113,113,0.12)", label: "Bearish" },
  neutral: { color: "var(--ai-accent)", bg: "var(--ai-accent-soft)", label: "Neutral" },
};

const STRENGTH_DOTS = { strong: 3, moderate: 2, weak: 1 };

function SignalIcon({ signal, size = 14 }) {
  if (signal === "bullish") return <TrendingUp size={size} />;
  if (signal === "bearish") return <TrendingDown size={size} />;
  return <Minus size={size} />;
}

function PatternCard({ pattern }) {
  const s = SIGNAL_STYLE[pattern.signal] || SIGNAL_STYLE.neutral;
  const dots = STRENGTH_DOTS[pattern.strength] || 1;
  return (
    <div
      className="rounded-xl p-4 border transition-all hover:scale-[1.01]"
      style={{ background: s.bg, borderColor: s.color + "33" }}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        {/* Pattern name + signal badge */}
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className="text-xs font-bold uppercase tracking-widest px-2 py-0.5 rounded-full"
            style={{ background: s.color + "22", color: s.color }}
          >
            {s.label}
          </span>
          <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            {pattern.pattern}
          </span>
        </div>
        {/* Signal icon */}
        <span style={{ color: s.color }}>
          <SignalIcon signal={pattern.signal} size={16} />
        </span>
      </div>

      {/* Strength dots */}
      <div className="flex items-center gap-1 mb-2">
        <span className="text-[10px] uppercase tracking-widest mr-1" style={{ color: "var(--text-muted)" }}>
          Strength
        </span>
        {[1, 2, 3].map((d) => (
          <span
            key={d}
            className="w-2 h-2 rounded-full"
            style={{ background: d <= dots ? s.color : "var(--border)" }}
          />
        ))}
      </div>

      {/* Description */}
      <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>
        {pattern.description}
      </p>

      {/* Detected at price */}
      {pattern.price && (
        <div className="mt-2 text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
          Detected at ₹{pattern.price.toFixed(2)}
        </div>
      )}
    </div>
  );
}

function PatternBiasBar({ bullish, bearish, total }) {
  if (!total) return null;
  const bPct = Math.round((bullish / total) * 100);
  const rPct = Math.round((bearish / total) * 100);
  const nPct = 100 - bPct - rPct;
  return (
    <div className="flex rounded-full overflow-hidden h-2 w-full mt-1">
      <div style={{ width: `${bPct}%`, background: "var(--gain)" }} />
      <div style={{ width: `${nPct}%`, background: "var(--ai-accent)" }} />
      <div style={{ width: `${rPct}%`, background: "var(--loss)" }} />
    </div>
  );
}

// ─── Main Component ────────────────────────────────────────────
export default function StockDetail() {
  const { symbol } = useParams();
  const navigate = useNavigate();

  const [quote, setQuote] = useState(null);
  const [chartData, setChartData] = useState([]);
  const [period, setPeriod] = useState("1D");
  const [loading, setLoading] = useState(true);

  // Pattern detection state
  const [patterns, setPatterns] = useState(null);
  const [patternsLoading, setPatternsLoading] = useState(false);
  const [patternsError, setPatternsError] = useState(null);

  useEffect(() => {
    if (!symbol) return;
    fetchData();
    fetchPatterns();
  }, [symbol, period]);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [q, c] = await Promise.all([
        api.get(`/stocks/${symbol}`),
        api.get(`/stocks/${symbol}/chart?period=${period}`),
      ]);
      setQuote(q.data);
      setChartData(c.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const fetchPatterns = async () => {
    setPatternsLoading(true);
    setPatternsError(null);
    try {
      const res = await api.get(`/stocks/${symbol}/patterns`);
      setPatterns(res.data);
    } catch (err) {
      console.error("Pattern fetch error:", err);
      setPatternsError("Could not load pattern data.");
    } finally {
      setPatternsLoading(false);
    }
  };

  if (loading) return (
    <div className="space-y-4">
      <div className="h-10 w-48 rounded-xl animate-pulse" style={{ background: "var(--bg-surface)" }} />
      <div className="h-96 rounded-xl animate-pulse" style={{ background: "var(--bg-surface)" }} />
    </div>
  );

  if (!quote) return (
    <div className="text-center py-20">
      <p style={{ color: "var(--text-muted)" }}>Stock not found</p>
      <button onClick={() => navigate(-1)} className="mt-4 text-sm underline" style={{ color: "var(--ai-accent)" }}>Go back</button>
    </div>
  );

  const isPos = (quote.change || 0) >= 0;
  const totalPatterns = patterns?.patterns?.length || 0;

  return (
    <div data-testid="stock-detail-page" className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button data-testid="back-btn" onClick={() => navigate(-1)} className="p-2 rounded-xl" style={{ color: "var(--text-muted)" }}>
          <ArrowLeft size={20} />
        </button>
        <div>
          <h1 className="text-2xl font-medium tracking-tight" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>
            {quote.name}
          </h1>
          <span className="text-sm font-mono" style={{ color: "var(--text-muted)" }}>{quote.symbol} | {quote.sector}</span>
        </div>
        <div className="ml-auto text-right">
          <div className="text-3xl font-semibold font-mono" style={{ color: "var(--text-primary)" }}>{formatCurrency(quote.price)}</div>
          <div className="flex items-center gap-1 justify-end text-sm font-mono" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
            {isPos ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
            {isPos ? "+" : ""}{formatNumber(quote.change)} ({formatPercent(quote.change_pct)})
          </div>
        </div>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
        {[
          { label: "Open",       value: formatCurrency(quote.open) },
          { label: "High",       value: formatCurrency(quote.high) },
          { label: "Low",        value: formatCurrency(quote.low) },
          { label: "Prev Close", value: formatCurrency(quote.prev_close) },
          { label: "Volume",     value: formatNumber(quote.volume, 0) },
          { label: "VWAP",       value: formatCurrency(quote.vwap) },
        ].map((s) => (
          <div key={s.label} className="card-premium p-3">
            <span className="text-[10px] font-bold uppercase tracking-widest block" style={{ color: "var(--text-muted)" }}>{s.label}</span>
            <span className="text-sm font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{s.value}</span>
          </div>
        ))}
      </div>

      {/* Chart */}
      <div className="card-premium p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-bold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>Price Chart</h3>
          <div className="flex gap-1">
            {["1D", "1W", "1M"].map((p) => (
              <button key={p} data-testid={`chart-period-${p}`} onClick={() => setPeriod(p)}
                className="px-3 py-1 rounded-lg text-xs font-medium transition-all"
                style={{ background: period === p ? "var(--ai-accent-soft)" : "transparent", color: period === p ? "var(--ai-accent)" : "var(--text-muted)" }}>
                {p}
              </button>
            ))}
          </div>
        </div>
        <TradingChart data={chartData} symbol={symbol} height={380} />
      </div>

      {/* ─── Chart Pattern Detection Panel ─── */}
      <div className="card-premium p-5" data-testid="pattern-detection-panel">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Eye size={16} style={{ color: "var(--ai-accent)" }} />
            <h3 className="text-xs font-bold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>
              Chart Pattern Detection
            </h3>
            {totalPatterns > 0 && (
              <span
                className="text-[10px] font-bold px-2 py-0.5 rounded-full"
                style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}
              >
                {totalPatterns} found
              </span>
            )}
          </div>
          <button
            onClick={fetchPatterns}
            disabled={patternsLoading}
            className="flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg transition-all"
            style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}
            title="Refresh pattern scan"
          >
            <RefreshCw size={12} className={patternsLoading ? "animate-spin" : ""} />
            Rescan
          </button>
        </div>

        {/* Loading skeleton */}
        {patternsLoading && (
          <div className="space-y-3">
            {[1, 2].map((i) => (
              <div key={i} className="h-24 rounded-xl animate-pulse" style={{ background: "var(--bg-surface)" }} />
            ))}
          </div>
        )}

        {/* Error state */}
        {!patternsLoading && patternsError && (
          <div className="text-center py-8 text-sm" style={{ color: "var(--text-muted)" }}>
            {patternsError}
          </div>
        )}

        {/* No patterns */}
        {!patternsLoading && !patternsError && patterns && totalPatterns === 0 && (
          <div className="text-center py-8">
            <BarChart3 size={32} className="mx-auto mb-2" style={{ color: "var(--text-muted)", opacity: 0.4 }} />
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>No strong patterns detected in recent price action.</p>
            <p className="text-xs mt-1" style={{ color: "var(--text-muted)", opacity: 0.6 }}>
              Patterns emerge as more price history forms. Check again later.
            </p>
          </div>
        )}

        {/* Patterns found */}
        {!patternsLoading && !patternsError && patterns && totalPatterns > 0 && (
          <>
            {/* Bias summary card */}
            <div
              className="rounded-xl p-4 mb-4 border"
              style={{
                background: patterns.bias === "Bullish" ? "rgba(52,211,153,0.07)"
                  : patterns.bias === "Bearish" ? "rgba(248,113,113,0.07)"
                  : "var(--ai-accent-soft)",
                borderColor: patterns.bias === "Bullish" ? "rgba(52,211,153,0.25)"
                  : patterns.bias === "Bearish" ? "rgba(248,113,113,0.25)"
                  : "var(--border)",
              }}
            >
              <div className="flex items-center gap-2 mb-1">
                {patterns.bias === "Bullish" ? (
                  <ShieldCheck size={16} style={{ color: "var(--gain)" }} />
                ) : patterns.bias === "Bearish" ? (
                  <ShieldAlert size={16} style={{ color: "var(--loss)" }} />
                ) : (
                  <Zap size={16} style={{ color: "var(--ai-accent)" }} />
                )}
                <span
                  className="text-sm font-semibold"
                  style={{
                    color: patterns.bias === "Bullish" ? "var(--gain)"
                      : patterns.bias === "Bearish" ? "var(--loss)"
                      : "var(--ai-accent)",
                  }}
                >
                  Overall Bias: {patterns.bias}
                </span>
              </div>
              <p className="text-xs mb-2" style={{ color: "var(--text-secondary)" }}>{patterns.summary}</p>
              {/* Bias bar */}
              <PatternBiasBar
                bullish={patterns.bullish_count}
                bearish={patterns.bearish_count}
                total={totalPatterns}
              />
              <div className="flex justify-between text-[10px] mt-1" style={{ color: "var(--text-muted)" }}>
                <span>🟢 {patterns.bullish_count} Bullish</span>
                <span>🔵 {totalPatterns - patterns.bullish_count - patterns.bearish_count} Neutral</span>
                <span>🔴 {patterns.bearish_count} Bearish</span>
              </div>
            </div>

            {/* AI note */}
            <p
              className="text-xs flex items-start gap-2 p-2 rounded-lg mb-4"
              style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}
            >
              <Info size={12} className="shrink-0 mt-0.5" />
              Patterns are detected algorithmically from 3 months of daily OHLCV data.
              They increase probability — not certainty. Always use stop-losses.
            </p>

            {/* Pattern cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {patterns.patterns.map((p, i) => (
                <PatternCard key={`${p.pattern}-${i}`} pattern={p} />
              ))}
            </div>

            <p className="text-[10px] mt-3 text-right" style={{ color: "var(--text-muted)" }}>
              Scanned {patterns.data_points} daily candles
            </p>
          </>
        )}
      </div>

      {/* Technical Indicators + Market Data */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card-premium p-5">
          <h3 className="text-xs font-bold uppercase tracking-widest mb-3" style={{ color: "var(--text-muted)" }}>Technical Indicators</h3>
          <p className="text-xs mb-3 p-2 rounded-lg flex items-start gap-2" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
            <Info size={12} className="shrink-0 mt-0.5" /> RSI above 70 = overbought (may fall). Below 30 = oversold (may rise). MACD crossing signal line = trend change.
          </p>
          <div className="space-y-3">
            {[
              { label: "RSI (14)", value: quote.rsi, hint: quote.rsi > 70 ? "Overbought" : quote.rsi < 30 ? "Oversold" : "Neutral" },
              { label: "MACD", value: quote.macd },
              { label: "MACD Signal", value: quote.macd_signal },
              { label: "Volume Ratio", value: `${quote.volume_ratio}x avg` },
            ].map((ind) => (
              <div key={ind.label} className="flex items-center justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
                <span className="text-sm" style={{ color: "var(--text-secondary)" }}>{ind.label}</span>
                <div className="text-right">
                  <span className="text-sm font-mono font-medium" style={{ color: "var(--text-primary)" }}>{ind.value}</span>
                  {ind.hint && <span className="text-[10px] ml-2" style={{ color: "var(--text-muted)" }}>{ind.hint}</span>}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="card-premium p-5">
          <h3 className="text-xs font-bold uppercase tracking-widest mb-3" style={{ color: "var(--text-muted)" }}>Market Data</h3>
          <div className="space-y-3">
            {[
              { label: "Market Cap", value: `${(quote.market_cap_cr / 100).toFixed(0)}K Cr` },
              { label: "P/E Ratio", value: quote.pe_ratio },
              { label: "Day Range", value: quote.day_range },
              { label: "52W High", value: formatCurrency(quote.week_52_high) },
              { label: "52W Low", value: formatCurrency(quote.week_52_low) },
            ].map((d) => (
              <div key={d.label} className="flex items-center justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
                <span className="text-sm" style={{ color: "var(--text-secondary)" }}>{d.label}</span>
                <span className="text-sm font-mono font-medium" style={{ color: "var(--text-primary)" }}>{d.value}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

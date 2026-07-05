import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import api from "../services/api";
import { formatCurrency, formatNumber, formatPercent } from "../utils/formatters";
import {
  ArrowLeft, TrendingUp, TrendingDown, BarChart3, Activity, Info,
  Zap, ShieldCheck, ShieldAlert, Minus, Eye, RefreshCw, HelpCircle, Brain, ChevronRight, Star, Plus
} from "lucide-react";
import { Link } from "react-router-dom";
import { useTheme } from "../context/ThemeContext";
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
  const { displayMode } = useTheme();

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    <div className="space-y-5 animate-fade-in-up">
      <div className="h-4 w-64 rounded-lg skeleton" />
      <div className="flex items-center gap-4"><div className="h-8 w-48 rounded-xl skeleton" /><div className="ml-auto h-10 w-32 rounded-xl skeleton" /></div>
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">{[...Array(6)].map((_, i) => <div key={i} className="stat-card space-y-2"><div className="h-3 w-1/2 skeleton rounded" /><div className="h-5 w-2/3 skeleton rounded" /></div>)}</div>
      <div className="glass-card p-5 h-96 skeleton" />
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
    <div data-testid="stock-detail-page" className="space-y-5">
      {/* Breadcrumb */}
      <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
        <Link to="/dashboard" className="hover:opacity-80 transition-opacity">StockAssist AI</Link>
        <ChevronRight size={10} />
        <Link to="/markets" className="hover:opacity-80 transition-opacity">Markets</Link>
        <ChevronRight size={10} />
        <span>Stocks</span>
        <ChevronRight size={10} />
        <span style={{ color: "var(--text-primary)" }}>{quote.symbol}</span>
      </div>

      {/* Header */}
      <div className="flex items-center gap-4">
        <button data-testid="back-btn" onClick={() => navigate(-1)} className="p-2 rounded-xl transition-all" style={{ color: "var(--text-muted)" }}
          onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
          <ArrowLeft size={18} />
        </button>
        <div>
          <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight font-display" style={{ color: "var(--text-primary)" }}>
            {quote.name}
          </h1>
          <span className="text-[12px] font-mono" style={{ color: "var(--text-muted)" }}>{quote.symbol} · {quote.sector}</span>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <div className="text-right">
            <div className="text-2xl sm:text-3xl font-semibold font-mono" style={{ color: "var(--text-primary)" }}>{formatCurrency(quote.price)}</div>
            <div className="flex items-center gap-1.5 justify-end text-[13px] font-mono font-semibold" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
              {isPos ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
              {isPos ? "+" : ""}₹{formatNumber(Math.abs(quote.change))} ({isPos ? "+" : ""}{quote.change_pct?.toFixed(2)}%)
            </div>
          </div>
          <button className="btn-ghost py-2 px-3 text-[11px] hidden sm:flex" title="Add to Watchlist">
            <Star size={13} /> Watchlist
          </button>
          <button className="btn-primary py-2 px-3 text-[11px] hidden sm:flex">
            <Brain size={13} /> AI Analysis
          </button>
        </div>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 stagger-children">
        {[
          { label: "Open",       value: formatCurrency(quote.open) },
          { label: "High",       value: formatCurrency(quote.high) },
          { label: "Low",        value: formatCurrency(quote.low) },
          { label: "Prev Close", value: formatCurrency(quote.prev_close) },
          { label: "Volume",     value: formatNumber(quote.volume, 0) },
          { label: "VWAP",       value: formatCurrency(quote.vwap) },
        ].map((s) => (
          <div key={s.label} className="stat-card !py-3 !px-4">
            <span className="text-[10px] font-bold uppercase tracking-[0.12em] block" style={{ color: "var(--text-muted)" }}>{s.label}</span>
            <span className="text-[14px] font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{s.value}</span>
          </div>
        ))}
      </div>

      {/* Chart */}
      <div className="glass-card p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--text-muted)" }}>Price Chart</h3>
          <div className="segment-control">
            {["1D", "1W", "1M"].map((p) => (
              <button key={p} data-testid={`chart-period-${p}`} onClick={() => setPeriod(p)}
                className={`segment-btn text-[11px] ${period === p ? "active" : ""}`}>
                {p}
              </button>
            ))}
          </div>
        </div>
        <TradingChart data={chartData} symbol={symbol} height={380} />
      </div>

      {/* ─── Chart Pattern Detection Panel ─── */}
      <div className="glass-card p-5" data-testid="pattern-detection-panel">
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
        {displayMode === "beginner" ? (
          <div className="glass-card p-5">
            <h3 className="text-xs font-bold uppercase tracking-widest mb-3 flex items-center gap-1.5" style={{ color: "var(--ai-accent)" }}>
              <Brain size={14} /> Simplified AI Indicators
            </h3>
            <p className="text-xs mb-4 p-2 rounded-lg flex items-start gap-2" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
              <Info size={12} className="shrink-0 mt-0.5" />
              We have simplified the technical charts to show easy-to-understand metrics. Switch to <strong>Advanced</strong> mode at the top for raw stats.
            </p>
            
            <div className="space-y-4">
              <div className="py-2 border-b" style={{ borderColor: "var(--border)" }}>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-sm font-semibold flex items-center gap-1" style={{ color: "var(--text-primary)" }}>
                    Buyer Activity Status <HelpCircle size={12} className="text-slate-400" title="RSI (Relative Strength Index) tracks current demand." />
                  </span>
                  <span className="text-xs font-mono font-bold" style={{ color: quote.rsi > 70 ? "var(--loss)" : quote.rsi < 30 ? "var(--gain)" : "var(--text-secondary)" }}>
                    {quote.rsi > 70 ? "Extremely High (Overbought)" : quote.rsi < 30 ? "Extremely Low (Oversold)" : "Healthy (Balanced)"}
                  </span>
                </div>
                <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                  {quote.rsi > 70 
                    ? "Many people have bought this stock recently. Prices could undergo a short-term pullback." 
                    : quote.rsi < 30 
                    ? "Very few people are buying this stock right now. It could be due for a potential reversal upward." 
                    : "Trading demand is stable. Price is moving within standard parameters."}
                </p>
              </div>

              <div className="py-2 border-b" style={{ borderColor: "var(--border)" }}>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-sm font-semibold flex items-center gap-1" style={{ color: "var(--text-primary)" }}>
                    Volume Expansion <HelpCircle size={12} className="text-slate-400" title="Tracks how many shares are traded compared to the daily average." />
                  </span>
                  <span className="text-xs font-mono font-bold" style={{ color: quote.volume_ratio > 1.5 ? "var(--gain)" : "var(--text-secondary)" }}>
                    {quote.volume_ratio > 2.0 ? "Massive Activity" : quote.volume_ratio > 1.2 ? "Elevated Activity" : "Normal Activity"} ({quote.volume_ratio}x usual)
                  </span>
                </div>
                <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                  {quote.volume_ratio > 1.5 
                    ? "A large amount of money is moving into this stock today. This indicates heavy institution block deal action." 
                    : "Standard trading volume. Retail and long-term investors are active at usual rates."}
                </p>
              </div>

              <div className="py-2">
                <div className="flex justify-between items-center mb-1">
                  <span className="text-sm font-semibold flex items-center gap-1" style={{ color: "var(--text-primary)" }}>
                    Price vs Average Price <HelpCircle size={12} className="text-slate-400" title="VWAP (Volume Weighted Average Price) is the average price paid today." />
                  </span>
                  <span className="text-xs font-mono font-bold" style={{ color: "var(--text-primary)" }}>
                    {Math.abs(quote.price - quote.vwap) / quote.price < 0.015 ? "Buying Near Average" : "Paying Above Average"}
                  </span>
                </div>
                <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                  The average buyer today paid ₹{quote.vwap.toFixed(2)}. The stock is currently trading at ₹{quote.price.toFixed(2)}.
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div className="glass-card p-5">
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
        )}

        <div className="glass-card p-5">
          <h3 className="text-xs font-bold uppercase tracking-widest mb-3" style={{ color: "var(--text-muted)" }}>{displayMode === "beginner" ? "Company Context" : "Market Data"}</h3>
          <div className="space-y-3">
            {displayMode === "beginner" ? (
              [
                { label: "Company Size", value: quote.market_cap_cr > 100000 ? "Mega Cap Enterprise" : quote.market_cap_cr > 25000 ? "Large Cap Enterprise" : "Mid Cap Enterprise" },
                { label: "Price Range Today", value: quote.day_range },
                { label: "52-Week High Point", value: formatCurrency(quote.week_52_high) },
                { label: "52-Week Low Point", value: formatCurrency(quote.week_52_low) },
              ].map((d) => (
                <div key={d.label} className="flex items-center justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
                  <span className="text-sm" style={{ color: "var(--text-secondary)" }}>{d.label}</span>
                  <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{d.value}</span>
                </div>
              ))
            ) : (
              [
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
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

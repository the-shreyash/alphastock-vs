import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import api from "../services/api";
import { formatCurrency, formatNumber, formatPercent } from "../utils/formatters";
import {
  ArrowLeft, TrendingUp, TrendingDown, BarChart3, Activity, Info,
  Zap, ShieldCheck, ShieldAlert, Minus, Eye, RefreshCw, Brain, ChevronRight, Star, Plus
} from "lucide-react";
import { Link } from "react-router-dom";
import TradingChart from "../components/charts/TradingChart";
import OrderTicket from "../components/stock/OrderTicket";
import { useRealtimeStore, selectPriceTicks } from "../store/realtimeStore";
import { applyLivePrices } from "../lib/livePrices";

// ─── Pattern Signal Colours ───────────────────────────────────
const SIGNAL_STYLE = {
  bullish: { color: "var(--gain)", bg: "rgba(52,211,153,0.12)", label: "Bullish" },
  bearish: { color: "var(--loss)", bg: "rgba(248,113,113,0.12)", label: "Bearish" },
  neutral: { color: "var(--text-secondary)", bg: "rgba(148,163,184,0.12)", label: "Neutral" },
};

function confidenceDots(confidence) {
  if (confidence >= 0.75) return 3;
  if (confidence >= 0.5) return 2;
  return 1;
}

function SignalIcon({ signal, size = 14 }) {
  if (signal === "bullish") return <TrendingUp size={size} />;
  if (signal === "bearish") return <TrendingDown size={size} />;
  return <Minus size={size} />;
}

function PatternCard({ pattern, index = 0 }) {
  const [expanded, setExpanded] = useState(false);
  const s = SIGNAL_STYLE[pattern.signal] || SIGNAL_STYLE.neutral;
  const conf = typeof pattern.confidence === "number" ? pattern.confidence : 0.5;
  const dots = confidenceDots(conf);
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4, delay: index * 0.05 }}
      className="rounded-xl p-4 border transition-all hover:scale-[1.01]"
      style={{ background: s.bg, borderColor: s.color + "33" }}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        {/* Pattern name + clickable signal badge */}
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            data-testid={`pattern-badge-${pattern.pattern}`}
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            title="Click for pattern details"
            className="text-xs font-bold uppercase tracking-widest px-2 py-0.5 rounded-full cursor-pointer transition-transform hover:scale-105"
            style={{ background: s.color + "22", color: s.color }}
          >
            {s.label}
          </button>
          <span className="card-subtitle font-semibold" style={{ color: "var(--text-primary)" }}>
            {pattern.pattern}
          </span>
        </div>
        {/* Signal icon */}
        <span style={{ color: s.color }}>
          <SignalIcon signal={pattern.signal} size={16} />
        </span>
      </div>

      {/* Confidence dots */}
      <div className="flex items-center gap-1 mb-2">
        <span className="caption uppercase tracking-widest mr-1">
          Confidence {Math.round(conf * 100)}%
        </span>
        {[1, 2, 3].map((d) => (
          <span
            key={d}
            className="w-2 h-2 rounded-full"
            style={{ background: d <= dots ? s.color : "var(--border)" }}
          />
        ))}
      </div>

      {/* Description — hidden until the badge is clicked */}
      {expanded ? (
        <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          {pattern.description}
        </p>
      ) : (
        <p className="text-[10px] italic" style={{ color: "var(--text-muted)" }}>
          Tap the badge for details
        </p>
      )}

      {/* Detected timestamp */}
      {pattern.timestamp && (
        <div className="mt-2 text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
          Detected {pattern.timestamp}
        </div>
      )}
    </motion.div>
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

  // Watchlist state
  const [inWatchlist, setInWatchlist] = useState(false);
  const [watchlistLoading, setWatchlistLoading] = useState(false);

  const priceTicks = useRealtimeStore(selectPriceTicks);

  /**
   * D5.19 — the detail page follows the live feed.
   *
   * This page had no realtime subscription at all: it fetched once on mount and
   * on a period change, so the screen a user opens to look closely at ONE
   * instrument was the least live surface in the product, while the dashboard
   * strip behind it moved. That matters more now that index cards open this
   * page (D-3) — NIFTY's detail view is where a user lands expecting the number
   * to keep moving.
   *
   * Routed through the shared `applyLivePrices` rather than a fifth inline
   * effect, for the rule it carries: it writes `price` and `change_pct` and
   * refuses to write a null. A canonical MarketTick has no day-change, and the
   * header renders one — an unguarded merge would blank a true +2.62% at the
   * exact moment the price started updating.
   */
  useEffect(() => {
    if (!priceTicks) return;
    setQuote((prev) => (prev ? applyLivePrices([prev], priceTicks)[0] : prev));
  }, [priceTicks]);

  useEffect(() => {
    if (!symbol) return;
    fetchData();
    fetchPatterns();
    checkWatchlist();

    // Track recently viewed stocks for the Dashboard widget
    try {
      const key = "sa_recent_stocks";
      const max = 6;
      const stored = JSON.parse(localStorage.getItem(key) || "[]");
      const filtered = stored.filter((s) => s.symbol !== symbol);
      const updated = [{ symbol, viewedAt: Date.now() }, ...filtered].slice(0, max);
      localStorage.setItem(key, JSON.stringify(updated));
    } catch { /* localStorage unavailable */ }

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

  const checkWatchlist = async () => {
    try {
      const { data } = await api.get("/watchlist");
      const symbols = (data || []).map((w) => (w.symbol || "").toUpperCase());
      setInWatchlist(symbols.includes((symbol || "").toUpperCase()));
    } catch {
      // Not logged in or watchlist unavailable — leave as false
    }
  };

  const toggleWatchlist = async () => {
    setWatchlistLoading(true);
    try {
      if (inWatchlist) {
        await api.delete(`/watchlist/${symbol}`);
        setInWatchlist(false);
      } else {
        await api.post("/watchlist", { symbol: symbol.toUpperCase() });
        setInWatchlist(true);
      }
    } catch (err) {
      console.error("Watchlist toggle error:", err);
    } finally {
      setWatchlistLoading(false);
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
          <h1 className="page-title">
            {quote.name}
          </h1>
          <span className="caption font-mono">{quote.symbol} · {quote.sector}</span>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <div className="text-right">
            <div data-testid="detail-price" className="stat-value">{formatCurrency(quote.price)}</div>
            <div data-testid="detail-change" className="flex items-center gap-1.5 justify-end text-[13px] font-mono font-semibold" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
              {isPos ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
              {isPos ? "+" : ""}₹{formatNumber(Math.abs(quote.change))} ({isPos ? "+" : ""}{quote.change_pct?.toFixed(2)}%)
            </div>
            {/* Freshness, never provenance (Developer Rule 4). The same claim
                the ranking table and the feed indicator make, in the same
                words, so one product does not describe one fact two ways. */}
            {quote.source_tier && (
              <span
                data-testid="detail-tier"
                className="mt-1 inline-block text-[9px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded"
                style={{
                  background: quote.source_tier === "streaming" ? "var(--gain-bg, #10b98118)" : "var(--hover)",
                  color: quote.source_tier === "streaming" ? "var(--gain)" : "var(--text-muted)",
                }}
              >
                {quote.source_tier === "streaming" ? "Live" : "Delayed"}
              </span>
            )}
          </div>
          <button
            onClick={toggleWatchlist}
            disabled={watchlistLoading}
            className={`btn-secondary btn-sm hidden sm:inline-flex transition-all ${inWatchlist ? "!bg-[var(--accent)]/15 !border-[var(--accent)]" : ""}`}
            title={inWatchlist ? "Remove from Watchlist" : "Add to Watchlist"}
            style={inWatchlist ? { color: "var(--accent)" } : undefined}
          >
            <Star size={13} fill={inWatchlist ? "currentColor" : "none"} />
            {watchlistLoading ? "..." : inWatchlist ? "Watching" : "Watchlist"}
          </button>
          <button className="btn-primary btn-sm hidden sm:inline-flex">
            <Brain size={13} /> AI Analysis
          </button>
        </div>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {[
          { label: "Open",       value: formatCurrency(quote.open) },
          { label: "High",       value: formatCurrency(quote.high) },
          { label: "Low",        value: formatCurrency(quote.low) },
          { label: "Prev Close", value: formatCurrency(quote.prev_close) },
          { label: "Volume",     value: formatNumber(quote.volume, 0) },
          { label: "VWAP",       value: formatCurrency(quote.vwap) },
        ].map((s, i) => (
          <motion.div
            key={s.label}
            className="stat-card !py-3 !px-4"
            initial={{ opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.4, delay: i * 0.05 }}
          >
            <span className="stat-label block">{s.label}</span>
            <span className="text-[15px] font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{s.value}</span>
          </motion.div>
        ))}
      </div>

      {/* Order entry — D5.19 (D-7). The one screen that already knows exactly
          which instrument the user means, so it is where order entry belongs.
          The component itself refuses to place anything without an explicit
          two-step confirmation; see components/stock/OrderTicket.jsx. */}
      <OrderTicket symbol={quote.symbol} exchange={quote.exchange === "BSE" ? "BSE" : "NSE"} price={quote.price} />

      {/* Chart */}
      <motion.div
        className="glass-card p-5"
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }}
        transition={{ duration: 0.4 }}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="card-title">Price Chart</h3>
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
      </motion.div>

      {/* ─── Chart Pattern Detection Panel ─── */}
      <motion.div
        className="glass-card p-5"
        data-testid="pattern-detection-panel"
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }}
        transition={{ duration: 0.4 }}
      >
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Eye size={18} style={{ color: "var(--ai-accent)" }} />
            <h3 className="card-title">
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
            className="btn-ghost btn-sm"
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
                <PatternCard key={`${p.pattern}-${i}`} pattern={p} index={i} />
              ))}
            </div>

            <p className="text-[10px] mt-3 text-right" style={{ color: "var(--text-muted)" }}>
              Scanned {patterns.data_points} daily candles
            </p>
          </>
        )}
      </motion.div>

      {/* Technical Indicators + Market Data */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <motion.div
          className="glass-card p-5"
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-60px" }}
          transition={{ duration: 0.4 }}
        >
          <h3 className="card-title mb-3">Technical Indicators</h3>
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
        </motion.div>

        <motion.div
          className="glass-card p-5"
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-60px" }}
          transition={{ duration: 0.4, delay: 0.05 }}
        >
          <h3 className="card-title mb-3">Market Data</h3>
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
        </motion.div>
      </div>
    </div>
  );
}

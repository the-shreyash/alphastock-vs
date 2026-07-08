import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import {
  TrendingUp, TrendingDown, BarChart3, Info, Zap, ShieldCheck, ShieldAlert,
  Minus, Eye, RefreshCw,
} from "lucide-react";
import api from "../../services/api";

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

// ─── Chart Pattern Detection Panel ────────────────────────────
export default function PatternDetection({ symbol }) {
  const [patterns, setPatterns] = useState(null);
  const [patternsLoading, setPatternsLoading] = useState(false);
  const [patternsError, setPatternsError] = useState(null);

  const fetchPatterns = useCallback(async () => {
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
  }, [symbol]);

  useEffect(() => {
    if (symbol) fetchPatterns();
  }, [symbol, fetchPatterns]);

  const totalPatterns = patterns?.patterns?.length || 0;

  return (
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
  );
}

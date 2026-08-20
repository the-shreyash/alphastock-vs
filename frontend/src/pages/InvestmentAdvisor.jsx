import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import api from "../services/api";
import { formatCurrency } from "../utils/formatters";
import {
  Sparkles, TrendingUp, Shield, Target, ChevronDown, ChevronUp,
  Newspaper, Layers, Gauge, AlertTriangle, Clock, ArrowRight,
} from "lucide-react";

/* Scroll-reveal wrapper — matches the rest of the platform (StockPicks.jsx). */
function Reveal({ children, delay = 0, className }) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4, delay, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.div>
  );
}

// Horizon segmented-control options. Kept in sync with backend HORIZON_CONFIG.
const HORIZONS = [
  { key: "long", label: "Long Term", hint: "1-3 years" },
  { key: "medium", label: "Medium Term", hint: "3-6 months" },
  { key: "short", label: "Short Term", hint: "2-4 weeks" },
  { key: "swing", label: "Swing", hint: "3-10 days" },
  { key: "intraday", label: "Intraday", hint: "Same day" },
];

const RISK_APPETITES = [
  { value: "conservative", label: "Conservative" },
  { value: "moderate", label: "Moderate" },
  { value: "aggressive", label: "Aggressive" },
];

const inputStyle = { background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" };
const inputClass = "w-full rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-[var(--ai-accent)] transition-colors";

function riskColor(risk) {
  if (risk === "Low") return "var(--gain)";
  if (risk === "Medium") return "#F59E0B";
  return "var(--loss)";
}

function confidenceColor(v) {
  return v >= 80 ? "var(--gain)" : v >= 70 ? "#F59E0B" : "var(--loss)";
}

/* Circular confidence ring drawn with an SVG stroke (no external deps). */
function ConfidenceRing({ value }) {
  const size = 52, stroke = 5, r = (size - stroke) / 2, c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, value));
  const color = confidenceColor(value);
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} title={`Confidence ${value}/100`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--border)" strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke}
          strokeDasharray={c} strokeDashoffset={c - (pct / 100) * c} strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="text-[13px] font-mono font-bold" style={{ color }}>{value}</span>
      </div>
    </div>
  );
}

function Chip({ icon: Icon, children, color = "var(--text-secondary)", bg = "var(--bg-surface)" }) {
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium" style={{ background: bg, color, border: "1px solid var(--border)" }}>
      {Icon && <Icon size={12} />}
      {children}
    </span>
  );
}

function ReasonList({ title, icon: Icon, items, positive }) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      <h5 className="eyebrow mb-2 flex items-center gap-1.5">
        <Icon size={12} /> {title}
      </h5>
      <ul className="space-y-1.5">
        {items.map((r, i) => (
          <li key={i} className="flex items-start gap-2 text-[12.5px]" style={{ color: "var(--text-secondary)" }}>
            <span className="mt-0.5 shrink-0" style={{ color: positive ? "var(--gain)" : "var(--ai-accent)" }}>
              {positive ? "+" : "•"}
            </span>
            <span>{r}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RecommendationCard({ rec, index }) {
  const [expanded, setExpanded] = useState(index === 0);
  const monogram = (rec.symbol || "?").slice(0, 2).toUpperCase();
  const rColor = riskColor(rec.risk);

  return (
    <div data-testid={`advisor-card-${rec.symbol}`} className="glass-card overflow-hidden">
      {/* Header */}
      <div className="p-5 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div
              className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0 font-bold text-sm"
              style={{ background: "linear-gradient(135deg, var(--ai-accent) 0%, #7C3AED 100%)", color: "#fff" }}
            >
              {monogram}
            </div>
            <div className="min-w-0">
              <h3 className="card-title truncate">{rec.name}</h3>
              <span className="caption font-mono">{rec.symbol} · {rec.sector || "NSE"} · {formatCurrency(rec.price)}</span>
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <div className="text-right hidden sm:block">
              <span className="stat-label block">Confidence</span>
              <span className="text-[11px] font-mono" style={{ color: confidenceColor(rec.confidence) }}>{rec.confidence}/100</span>
            </div>
            <ConfidenceRing value={rec.confidence} />
          </div>
        </div>

        {/* Quick chips */}
        <div className="flex items-center gap-2 flex-wrap mt-3">
          <span
            data-testid={`advisor-risk-${rec.symbol}`}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold uppercase tracking-wide"
            style={{ background: `${rColor}15`, color: rColor, border: `1px solid ${rColor}30` }}
          >
            <Shield size={12} /> {rec.risk} Risk
          </span>
          <Chip icon={TrendingUp} color="var(--gain)" bg="var(--gain-bg)">
            Exp. Return {rec.expected_return_pct >= 0 ? "+" : ""}{rec.expected_return_pct}%
          </Chip>
          <Chip icon={Clock}>{rec.holding_period}</Chip>
          {rec.pattern && <Chip icon={Layers} color="var(--ai-accent)">{rec.pattern}</Chip>}
        </div>
      </div>

      {/* Risk-reward row */}
      <div className="p-5 border-b grid grid-cols-2 sm:grid-cols-4 gap-4" style={{ borderColor: "var(--border)" }}>
        <div>
          <span className="stat-label block mb-1">Entry Zone</span>
          <span className="text-sm font-mono" style={{ color: "var(--text-primary)" }}>
            {formatCurrency(rec.entry_zone?.low)} – {formatCurrency(rec.entry_zone?.high)}
          </span>
        </div>
        <div>
          <span className="stat-label block mb-1">Stop Loss</span>
          <span className="text-sm font-mono text-loss">{formatCurrency(rec.stop_loss)}</span>
        </div>
        <div className="col-span-2">
          <span className="stat-label block mb-1">Targets</span>
          <div className="flex items-center gap-2 flex-wrap">
            {(rec.targets || []).map((t, i) => (
              <span key={i} className="inline-flex items-center gap-1 text-sm font-mono text-gain">
                {i > 0 && <ArrowRight size={11} style={{ color: "var(--text-muted)" }} />}
                {formatCurrency(t)}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* AI summary */}
      {rec.ai_summary && (
        <div className="p-5 border-b" style={{ borderColor: "var(--border)" }}>
          <h5 className="eyebrow mb-2 flex items-center gap-1.5" style={{ color: "var(--ai-accent)" }}>
            <Sparkles size={12} /> AI Summary
          </h5>
          <blockquote
            className="body-text pl-3 text-[13px]"
            style={{ borderLeft: "2px solid var(--ai-accent)", color: "var(--text-secondary)" }}
          >
            {rec.ai_summary}
          </blockquote>
        </div>
      )}

      {/* Collapsible reasons */}
      <div className="p-5">
        <button
          data-testid={`advisor-toggle-${rec.symbol}`}
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1.5 text-xs font-medium mb-1 transition-colors"
          style={{ color: "var(--text-muted)" }}
        >
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          {expanded ? "Hide" : "Show"} full analysis
        </button>

        <AnimatePresence initial={false}>
          {expanded && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.25 }}
              className="overflow-hidden"
            >
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5 pt-3">
                <ReasonList title="Technical Reasons" icon={Gauge} items={rec.technical_reasons} positive />
                <ReasonList title="Fundamental Reasons" icon={Layers} items={rec.fundamental_reasons} />
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-4">
                <div className="p-3 rounded-xl" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                  <span className="eyebrow flex items-center gap-1.5 mb-1.5"><Newspaper size={12} /> News Impact</span>
                  <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{rec.news_impact}</p>
                </div>
                <div className="p-3 rounded-xl" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                  <span className="eyebrow flex items-center gap-1.5 mb-1.5"><Layers size={12} /> Sector Strength</span>
                  <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{rec.sector_strength}</p>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function CardSkeleton() {
  return <div className="h-72 rounded-2xl skeleton" />;
}

export default function InvestmentAdvisor() {
  const [horizon, setHorizon] = useState("swing");
  const [riskAppetite, setRiskAppetite] = useState("moderate");
  const [capital, setCapital] = useState(100000);
  const [recs, setRecs] = useState([]);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [touched, setTouched] = useState(false);

  const handleRecommend = async () => {
    setLoading(true);
    setError(null);
    setTouched(true);
    try {
      const { data } = await api.post("/advisor/recommend", {
        horizon,
        risk_appetite: riskAppetite,
        capital: Number(capital) || undefined,
      });
      setRecs(data.recommendations || []);
      setMeta(data);
      if (data.available === false) {
        setError(data.note || "Live market data is temporarily unavailable — please try again shortly.");
      }
    } catch (err) {
      console.error("Advisor error:", err);
      setError(err.response?.data?.detail || "Unable to fetch AI recommendations. Please try again.");
      setRecs([]);
    } finally {
      setLoading(false);
    }
  };

  const activeHorizon = HORIZONS.find((h) => h.key === horizon);

  return (
    <div data-testid="investment-advisor-page" className="space-y-6">
      <Reveal>
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
            <Sparkles size={20} />
          </div>
          <div>
            <h1 className="page-title">Investment Advisor</h1>
            <p className="page-subtitle mt-1">AI-selected NSE stocks across every horizon — each fully explained with entry, risk, reward, technicals, fundamentals, news and sector strength.</p>
          </div>
        </div>
      </Reveal>

      {/* Controls */}
      <Reveal delay={0.05}>
        <div className="glass-card p-6 space-y-5">
          {/* Horizon segmented control */}
          <div>
            <label className="stat-label block mb-2">Investment Horizon</label>
            <div
              data-testid="horizon-selector"
              className="flex flex-wrap gap-1.5 p-1 rounded-xl"
              style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}
            >
              {HORIZONS.map((h) => {
                const active = h.key === horizon;
                return (
                  <button
                    key={h.key}
                    data-testid={`horizon-${h.key}`}
                    onClick={() => setHorizon(h.key)}
                    className="flex-1 min-w-[92px] rounded-lg px-3 py-2 text-center transition-all"
                    style={{
                      background: active ? "var(--ai-accent)" : "transparent",
                      color: active ? "#fff" : "var(--text-secondary)",
                      boxShadow: active ? "0 4px 14px rgba(99,102,241,0.35)" : "none",
                    }}
                  >
                    <span className="block text-[13px] font-semibold">{h.label}</span>
                    <span className="block text-[10px] font-mono opacity-80">{h.hint}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Risk + capital */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="stat-label block mb-1.5">Risk Appetite</label>
              <select
                data-testid="advisor-risk-select"
                value={riskAppetite}
                onChange={(e) => setRiskAppetite(e.target.value)}
                className={inputClass}
                style={inputStyle}
              >
                {RISK_APPETITES.map((r) => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="stat-label block mb-1.5">Capital (INR, optional)</label>
              <input
                data-testid="advisor-capital-input"
                type="number"
                value={capital}
                min={0}
                onChange={(e) => setCapital(e.target.value)}
                className={`${inputClass} font-mono`}
                style={inputStyle}
              />
            </div>
          </div>

          <button
            data-testid="advisor-recommend-btn"
            onClick={handleRecommend}
            disabled={loading}
            className="btn-primary w-full"
          >
            <Sparkles size={16} />
            {loading ? "Analyzing live market…" : `Get AI Recommendations`}
          </button>
        </div>
      </Reveal>

      {/* Results */}
      {loading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => <CardSkeleton key={i} />)}
        </div>
      ) : error ? (
        <Reveal>
          <div data-testid="advisor-error" className="glass-card p-8 text-center">
            <AlertTriangle size={28} className="mx-auto mb-3" style={{ color: "var(--loss)" }} />
            <p className="body-text mb-4">{error}</p>
            <button onClick={handleRecommend} className="btn-secondary">Retry</button>
          </div>
        </Reveal>
      ) : recs.length > 0 ? (
        <div className="space-y-4">
          <Reveal>
            <div className="flex items-center justify-between flex-wrap gap-2">
              <h2 className="section-title">
                {recs.length} {activeHorizon?.label} idea{recs.length > 1 ? "s" : ""}
              </h2>
              <div className="flex items-center gap-2">
                {meta?.ai_powered ? (
                  <Chip icon={Sparkles} color="var(--ai-accent)">AI-powered narrative</Chip>
                ) : (
                  <Chip icon={Gauge}>Rule-based narrative</Chip>
                )}
                {/* Reads the freshness tier, not a provider name. The old
                    `data_source === "yahoo_finance"` test would have rendered
                    "Fallback data" over a live broker feed (DD-2). */}
                <Chip>{meta?.source_tier === "streaming" ? "Live market data"
                     : meta?.source_tier === "delayed" ? "Delayed market data"
                     : "Market data unavailable"}</Chip>
              </div>
            </div>
          </Reveal>
          {recs.map((rec, i) => (
            <Reveal key={rec.symbol} delay={i * 0.05}>
              <RecommendationCard rec={rec} index={i} />
            </Reveal>
          ))}
          <Reveal>
            <p className="caption text-center pt-2">
              For education only — not investment advice. Levels are derived from live technicals; always do your own research and manage risk.
            </p>
          </Reveal>
        </div>
      ) : touched ? (
        <Reveal>
          <div data-testid="advisor-empty" className="glass-card p-8 text-center">
            <Target size={28} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
            <p className="body-text">No matching setups found for this horizon right now. Try a different horizon or risk appetite.</p>
          </div>
        </Reveal>
      ) : (
        <Reveal>
          <div className="glass-card p-10 text-center">
            <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
              <Sparkles size={26} />
            </div>
            <h3 className="card-title mb-1.5">Pick a horizon and let the AI scan the market</h3>
            <p className="body-text max-w-md mx-auto">
              The advisor ranks live NSE stocks by technicals, patterns and sector strength, then tunes entry, stop loss and targets to your chosen time frame.
            </p>
          </div>
        </Reveal>
      )}
    </div>
  );
}

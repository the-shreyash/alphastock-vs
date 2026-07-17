import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import api from "../services/api";
import { formatNumber } from "../utils/formatters";
import { Wallet, RefreshCw, Download, ArrowUpRight, ArrowDownRight, Brain, Shield, AlertTriangle, Layers, Scale, Award, TrendingDown, Coins, Sparkles, Loader2, Activity, LineChart as LineChartIcon } from "lucide-react";
import { PieChart as RechartsPie, Pie, Cell, ResponsiveContainer, Tooltip, AreaChart, Area, XAxis, YAxis, CartesianGrid } from "recharts";
import { motion } from "framer-motion";
import AnimatedNumber from "../components/ui/AnimatedNumber";
import { usePriceFlash } from "../hooks/usePriceFlash";
import {
  useRealtimeStore,
  selectConnected,
  selectPortfolioLive,
  selectPortfolioSynced,
} from "../store/realtimeStore";

const COLORS = ["#6366F1", "#10B981", "#F59E0B", "#F43F5E", "#06B6D4", "#8B5CF6", "#EC4899", "#14B8A6"];
const TABS = ["Overview", "Holdings", "Performance", "Allocation", "AI Review", "Transactions"];
const AMBER = "#F59E0B";
const SEVERITY_COLOR = { critical: "var(--loss)", warning: AMBER, positive: "var(--gain)", info: "var(--ai-accent)" };

/* Scroll-reveal wrapper — fades/slides content in as it enters the viewport */
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

/* Circular health gauge — colour keyed to the score band. pathLength normalises
   the dash array to 0–100 so the arc maps directly to the score percentage. */
function ScoreRing({ score, size = 96 }) {
  const s = Math.max(0, Math.min(100, Math.round(score ?? 0)));
  const color = s >= 80 ? "var(--gain)" : s >= 50 ? AMBER : "var(--loss)";
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
        <circle cx="18" cy="18" r="15.5" fill="none" stroke="var(--border)" strokeWidth="3" />
        <circle cx="18" cy="18" r="15.5" fill="none" stroke={color} strokeWidth="3" strokeLinecap="round"
          pathLength="100" strokeDasharray={`${s} ${100 - s}`} className="score-gauge-circle" />
      </svg>
      <span className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-bold font-mono leading-none" style={{ color, fontSize: size / 4.5 }}>{s}</span>
        <span className="mt-0.5" style={{ color: "var(--text-muted)", fontSize: size / 11 }}>/100</span>
      </span>
    </div>
  );
}

/* Compact labelled metric tile with an AI-style reasoning line. */
function DimensionCard({ icon: Icon, label, value, valueColor, note, testid }) {
  return (
    <div data-testid={testid} className="rounded-2xl p-3.5" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-1.5 mb-1.5">
        <Icon size={13} style={{ color: "var(--text-muted)" }} />
        <span className="eyebrow">{label}</span>
      </div>
      <div className="text-[17px] font-semibold font-mono leading-tight" style={{ color: valueColor || "var(--text-primary)" }}>{value}</div>
      {note && <p className="text-[11px] leading-snug mt-1" style={{ color: "var(--text-muted)" }}>{note}</p>}
    </div>
  );
}

// Minimum ms between event-triggered silent refetches of the intelligence
// bundle (risk / suggestions / health / AI summary). Live marks stream in via
// the socket; this only keeps the AI layer in step without hammering the API.
const INTEL_REFRESH_MIN_MS = 60000;

/* Live indicator — pulsing dot + last snapshot time while streaming. */
function LiveBadge({ live }) {
  if (!live) return null;
  const at = live.updatedAt ? new Date(live.updatedAt) : null;
  return (
    <span data-testid="portfolio-live-badge" className="badge-status inline-flex items-center gap-1.5" style={{ background: "var(--gain-bg)", color: "var(--gain)" }}>
      <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: "var(--gain)" }} />
      LIVE{at ? ` · ${at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : ""}
    </span>
  );
}

/* One holdings-table row. Isolated so a live tick re-renders and flashes ONLY
   the affected row (the event-driven "update only what changed" mandate). */
function HoldingRow({ holding: h }) {
  const isPosH = (h.pnl ?? 0) >= 0;
  const priceRef = usePriceFlash(h.current_price);
  return (
    <tr>
      <td>
        <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{h.symbol}</span>
        <SourceBadge source={h.source} />
      </td>
      <td className="font-mono text-[13px]">{h.quantity}</td>
      <td className="font-mono text-[13px]">₹{formatNumber(h.avg_price)}</td>
      <td>
        <span ref={priceRef} className="font-mono text-[13px] inline-block rounded px-1 -mx-1">
          {h.current_price != null ? `₹${formatNumber(h.current_price)}` : "--"}
        </span>
      </td>
      <td className="font-mono text-[13px]">₹{formatNumber(h.invested)}</td>
      <td className="font-mono text-[13px]">₹{formatNumber(h.current_value)}</td>
      <td className="font-mono text-[13px] font-semibold" style={{ color: isPosH ? "var(--gain)" : "var(--loss)" }}>
        {isPosH ? "+" : ""}₹{formatNumber(Math.abs(h.pnl ?? 0))}
      </td>
      <td className="font-mono text-[13px] font-semibold" style={{ color: isPosH ? "var(--gain)" : "var(--loss)" }}>
        {isPosH ? "+" : ""}{(h.pnl_pct ?? 0).toFixed(2)}%
      </td>
      <td className="font-mono text-[13px]" style={{ color: (h.day_change_pct ?? 0) >= 0 ? "var(--gain)" : "var(--loss)" }}>
        {h.day_change_pct != null ? `${h.day_change_pct >= 0 ? "+" : ""}${h.day_change_pct.toFixed(2)}%` : "--"}
      </td>
    </tr>
  );
}

function SourceBadge({ source }) {
  const isBroker = source === "broker";
  return (
    <span className="badge-status ml-2" style={{
      background: isBroker ? "var(--ai-accent-bg, var(--hover))" : "var(--hover)",
      color: isBroker ? "var(--ai-accent)" : "var(--text-muted)",
      fontSize: 9,
    }}>{isBroker ? "BROKER" : "MANUAL"}</span>
  );
}

/* Sector allocation bars — shared by Overview + Allocation tabs. */
function SectorBars({ sectors }) {
  if (!sectors?.length) return <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>No sector data — add holdings to see allocation.</p>;
  return (
    <div className="space-y-2">
      {sectors.map((s, i) => {
        const over = s.pct > 40;
        return (
          <div key={s.sector} data-testid={`sector-${s.sector}`}>
            <div className="flex items-center justify-between text-[12px] mb-1">
              <span style={{ color: "var(--text-secondary)" }}>{s.sector}</span>
              <span className="font-mono font-semibold" style={{ color: over ? "var(--loss)" : "var(--text-primary)" }}>{s.pct.toFixed(1)}%</span>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--hover)" }}>
              <motion.div className="h-full rounded-full" initial={{ width: 0 }} whileInView={{ width: `${Math.min(100, s.pct)}%` }}
                viewport={{ once: true }} transition={{ duration: 0.6, delay: i * 0.05, ease: [0.16, 1, 0.3, 1] }}
                style={{ background: over ? "var(--loss)" : COLORS[i % COLORS.length] }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function AllocationPie({ data }) {
  if (!data?.length) return (
    <div className="h-[120px] flex items-center justify-center">
      <span className="body-text" style={{ color: "var(--text-muted)" }}>No holdings yet</span>
    </div>
  );
  return (
    <ResponsiveContainer width="100%" height={140}>
      <RechartsPie>
        <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={34} outerRadius={58} paddingAngle={2} stroke="none">
          {data.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
        </Pie>
        <Tooltip contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", borderRadius: 12, fontSize: 11 }} />
      </RechartsPie>
    </ResponsiveContainer>
  );
}

export default function Portfolio() {
  const [intel, setIntel] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [zerodhaAccount, setZerodhaAccount] = useState(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [tab, setTab] = useState("Overview");

  // AI Review (on-demand narrative) + Transactions (lazy-loaded per tab)
  const [aiReview, setAiReview] = useState(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [transactions, setTransactions] = useState(null);
  const [txLoading, setTxLoading] = useState(false);

  // ─── Real-time (Sprint R5): one shared socket → global store → this page ──
  const connected = useRealtimeStore(selectConnected);
  const live = useRealtimeStore(selectPortfolioLive); // portfolio.updated snapshot
  const synced = useRealtimeStore(selectPortfolioSynced); // portfolio.synced trigger
  const lastIntelFetchRef = useRef(0);

  const fetchPortfolio = useCallback(async () => {
    lastIntelFetchRef.current = Date.now();
    try {
      const [i, p, z] = await Promise.all([
        api.get("/portfolio/intelligence"),
        api.get("/portfolio/performance").catch(() => ({ data: { available: false } })),
        api.get("/zerodha/account").catch(() => ({ data: null })),
      ]);
      setIntel(i.data); setPerformance(p.data); setZerodhaAccount(z.data);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchPortfolio(); }, [fetchPortfolio]);

  // Broker sync completed → the holdings set itself changed; refresh the full
  // intelligence bundle silently (no skeleton — fetchPortfolio only gates the
  // initial skeleton via `loading`).
  useEffect(() => {
    if (!synced) return;
    if (Date.now() - lastIntelFetchRef.current < 2000) return; // dedupe burst
    fetchPortfolio();
  }, [synced, fetchPortfolio]);

  // AI portfolio refresh: live marks stream in via the socket, but risk score,
  // suggestions and the health scan are computed server-side — refetch them at
  // most once a minute, TRIGGERED by real portfolio events (never a timer).
  useEffect(() => {
    if (!live) return;
    if (Date.now() - lastIntelFetchRef.current < INTEL_REFRESH_MIN_MS) return;
    fetchPortfolio();
  }, [live, fetchPortfolio]);

  // Merge the live snapshot's per-symbol marks over the intelligence holdings
  // so table rows / movers stay current between bundle refetches.
  const liveMarks = useMemo(() => {
    const map = {};
    for (const h of live?.holdings ?? []) if (h?.symbol) map[h.symbol] = h;
    return map;
  }, [live]);

  // GSAP green/red flash on the headline value + P&L when a snapshot moves them
  // (hooks stay above the loading early-return; they track live values only).
  const valueFlashRef = usePriceFlash(live?.pnl?.current_value);
  const pnlFlashRef = usePriceFlash(live?.pnl?.unrealized);

  // Lazy-load transactions the first time the tab is opened.
  useEffect(() => {
    if (tab === "Transactions" && transactions === null && !txLoading) {
      setTxLoading(true);
      api.get("/trades/history")
        .then(({ data }) => setTransactions(data || []))
        .catch(() => setTransactions([]))
        .finally(() => setTxLoading(false));
    }
  }, [tab, transactions, txLoading]);

  // Re-run the AI portfolio monitor, then refresh the server intelligence bundle.
  const runAnalysis = async () => {
    setAnalyzing(true);
    try { await api.post("/monitor/run").catch(() => {}); await fetchPortfolio(); }
    finally { setAnalyzing(false); }
  };

  // On-demand AI narrative review (Portfolio Manager prompt).
  const runAiReview = async () => {
    setAiLoading(true);
    try { const { data } = await api.post("/ai/portfolio-review"); setAiReview(data); }
    catch (err) { console.error(err); setAiReview({ content: "AI review is temporarily unavailable. Please try again." }); }
    finally { setAiLoading(false); }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const res = await api.get("/portfolio/export", { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url; a.download = "portfolio.csv";
      document.body.appendChild(a); a.click(); a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) { console.error(err); }
    finally { setExporting(false); }
  };

  if (loading) return (
    <div className="space-y-5 animate-fade-in-up">
      <div className="h-8 w-40 rounded-xl skeleton" />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[...Array(4)].map((_, i) => <div key={i} className="stat-card space-y-3"><div className="h-3 w-1/2 skeleton rounded" /><div className="h-6 w-2/3 skeleton rounded-lg" /></div>)}
      </div>
      <div className="glass-card p-5 h-64 skeleton" />
    </div>
  );

  // ─── Server-computed intelligence + live stream overlay ──────────────────
  // The intelligence bundle stays the single source of truth for analytics;
  // `portfolio.updated` snapshots overlay only the live marks (price/value/
  // P&L/allocation) so the page moves with the market between refetches.
  const holdings = (intel?.holdings ?? []).map(h => {
    const mark = liveMarks[h.symbol];
    if (!mark) return h;
    const merged = { ...h };
    for (const key of ["current_price", "current_value", "pnl", "pnl_pct", "day_change_pct"]) {
      if (mark[key] != null) merged[key] = mark[key];
    }
    return merged;
  });
  const pnl = { ...(intel?.pnl ?? {}), ...(live?.pnl ?? {}) };
  const allocation = live?.allocation?.by_holding?.length
    ? live.allocation
    : (intel?.allocation ?? { by_holding: [], by_sector: [] });
  // Equity curve = real EOD snapshots + one streaming "Live" point, so the
  // chart's last segment moves with the market (doc: "Chart Animation").
  const equityCurve = performance?.available
    ? (live?.pnl?.current_value != null
        ? [...performance.curve, { date: "Live", value: live.pnl.current_value, invested: live.pnl.invested, pnl: live.pnl.unrealized }]
        : performance.curve)
    : [];
  const diversification = intel?.diversification ?? {};
  const risk = intel?.risk ?? {};
  const movers = intel?.movers ?? { strong: [], weak: [] };
  const suggestions = intel?.suggestions ?? [];
  const dividends = intel?.dividends ?? { available: false };
  const health = intel?.health ?? null;
  const alerts = health?.alerts ?? [];
  const sources = intel?.sources ?? [];

  const totalPnl = pnl.unrealized ?? 0;
  const totalPnlPct = pnl.unrealized_pct ?? 0;
  const isPos = totalPnl >= 0;

  const pieData = allocation.by_holding.map(h => ({ name: h.symbol, value: h.value }));
  const overCount = allocation.by_holding.filter(h => h.pct > 30).length + allocation.by_sector.filter(s => s.pct > 40).length;
  const winners = holdings.filter(h => (h.pnl ?? 0) > 0);
  const unrealizedGain = winners.reduce((a, h) => a + (h.pnl ?? 0), 0);
  const riskColor = risk.level === "High" ? "var(--loss)" : risk.level === "Elevated" ? AMBER : "var(--gain)";
  const divColor = ["Excellent", "Good"].includes(diversification.label) ? "var(--gain)"
    : diversification.label === "Moderate" ? AMBER : "var(--loss)";

  return (
    <div data-testid="portfolio-page" className="space-y-5">
      {/* Header */}
      <Reveal>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="page-title">Portfolio</h1>
            <p className="page-subtitle mt-1">
              Your holdings and performance overview
              {sources.length > 0 && <span className="caption ml-2">· {sources.map(s => s === "broker" ? "Broker" : "Manual").join(" + ")}</span>}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={fetchPortfolio} className="btn-ghost btn-sm" style={{ padding: "10px" }} title="Refresh">
              <RefreshCw size={15} />
            </button>
            <button onClick={handleExport} disabled={exporting || holdings.length === 0} className="btn-ghost btn-sm" style={{ padding: "10px" }} title="Export CSV">
              {exporting ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
            </button>
          </div>
        </div>
      </Reveal>

      {/* Tab Bar */}
      <Reveal>
        <div className="tab-bar">
          {TABS.map(t => (
            <button key={t} onClick={() => setTab(t)} className={`tab-btn ${tab === t ? "active" : ""}`}>{t}</button>
          ))}
        </div>
      </Reveal>

      {/* Portfolio Value Strip — shown on every tab for constant context */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Reveal delay={0} className="lg:col-span-2">
          <div className="glass-card p-5 h-full">
            <div className="flex items-center gap-2 mb-1.5">
              <span className="stat-label">Total Portfolio Value</span>
              {connected && <LiveBadge live={live} />}
            </div>
            <div className="flex items-baseline gap-3 flex-wrap">
              <span ref={valueFlashRef} className="stat-value inline-block rounded-lg px-1 -mx-1" data-testid="portfolio-total-value">
                ₹<AnimatedNumber value={pnl.current_value ?? 0} format={(v) => formatNumber(v)} />
              </span>
              <span ref={pnlFlashRef} className="text-sm font-mono font-semibold flex items-center gap-1 rounded px-1 -mx-1" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
                {isPos ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
                {isPos ? "+" : ""}₹<AnimatedNumber value={Math.abs(totalPnl)} format={(v) => formatNumber(v)} />
                {" ("}{isPos ? "+" : ""}<AnimatedNumber value={totalPnlPct} format={(v) => v.toFixed(2)} />%)
              </span>
            </div>
            <div className="flex items-center gap-4 mt-2 flex-wrap">
              <span className="caption">Invested: <b style={{ color: "var(--text-primary)" }}>₹{formatNumber(pnl.invested ?? 0)}</b></span>
              <span className="caption">Realized P&L: <b style={{ color: (pnl.realized ?? 0) >= 0 ? "var(--gain)" : "var(--loss)" }}>{(pnl.realized ?? 0) >= 0 ? "+" : ""}₹{formatNumber(Math.abs(pnl.realized ?? 0))}</b></span>
              <span className="caption">Holdings: <b style={{ color: "var(--text-primary)" }}>{intel?.holdings_count ?? 0}</b></span>
            </div>
          </div>
        </Reveal>
        <Reveal delay={0.06}>
          <div className="glass-card p-5 h-full">
            <span className="stat-label block mb-2">Allocation by Holding</span>
            <AllocationPie data={pieData} />
          </div>
        </Reveal>
      </div>

      {/* ═══════════ OVERVIEW ═══════════ */}
      {tab === "Overview" && (
        <Reveal>
          <div data-testid="portfolio-intelligence" className="glass-card p-5">
            {/* Hero: score ring + summary + re-analyze */}
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div className="flex items-center gap-4 min-w-0">
                <ScoreRing score={health?.health_score ?? 100} />
                <div className="min-w-0">
                  <h3 className="eyebrow flex items-center gap-2 mb-1">
                    <Brain size={13} style={{ color: "var(--ai-accent)" }} /> Portfolio Intelligence
                  </h3>
                  <p className="text-[13px] leading-snug" style={{ color: "var(--text-secondary)" }}>
                    {intel?.summary || "Add open positions to unlock AI portfolio analysis."}
                  </p>
                  <div className="flex items-center gap-3 mt-1.5 flex-wrap">
                    <span className="caption">Open positions: <b style={{ color: "var(--text-primary)" }}>{health?.open_positions ?? holdings.length}</b></span>
                    <span className="caption">At risk: <b style={{ color: (health?.at_risk ?? 0) ? "var(--loss)" : "var(--text-primary)" }}>{health?.at_risk ?? 0}</b></span>
                    {health?.last_check && <span className="caption">Updated {new Date(health.last_check).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
                  </div>
                </div>
              </div>
              <button data-testid="reanalyze-btn" onClick={runAnalysis} disabled={analyzing} className="btn-secondary btn-sm shrink-0">
                {analyzing ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                {analyzing ? "Analyzing…" : "Re-analyze"}
              </button>
            </div>

            {/* Dimension grid */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mt-4">
              <DimensionCard icon={Layers} testid="dim-diversification" label="Diversification" value={diversification.label || "—"} valueColor={divColor}
                note={holdings.length ? `${diversification.n_holdings} holdings · ${diversification.n_sectors} sector${diversification.n_sectors === 1 ? "" : "s"} · top ${(diversification.top_weight ?? 0).toFixed(0)}% · eff. ${diversification.effective_holdings}` : "No holdings yet."} />
              <DimensionCard icon={AlertTriangle} testid="dim-overexposure" label="Overexposure" value={overCount === 0 ? "Balanced" : `${overCount} flag${overCount === 1 ? "" : "s"}`}
                valueColor={overCount ? "var(--loss)" : "var(--gain)"}
                note={overCount ? "One or more positions/sectors exceed safe concentration limits." : "No single name >30% or sector >40%."} />
              <DimensionCard icon={Shield} testid="dim-risk" label="Risk" value={risk.level || "—"} valueColor={riskColor}
                note={holdings.length ? `Risk score ${risk.score}/100 across ${risk.factors?.length ?? 0} factor${(risk.factors?.length ?? 0) === 1 ? "" : "s"}.` : "No exposure."} />
              <DimensionCard icon={Coins} testid="dim-profit-potential" label="Unrealized Gain" value={`₹${formatNumber(unrealizedGain, 0)}`} valueColor={unrealizedGain > 0 ? "var(--gain)" : "var(--text-primary)"}
                note={`${winners.length} winner${winners.length === 1 ? "" : "s"} of ${holdings.length}.`} />
            </div>

            {/* Risk factors */}
            {risk.factors?.length > 0 && (
              <div className="mt-5">
                <div className="flex items-center gap-1.5 mb-2.5">
                  <Shield size={13} style={{ color: "var(--text-muted)" }} />
                  <span className="eyebrow">Risk Factors</span>
                </div>
                <div className="space-y-1.5">
                  {risk.factors.map((f, i) => (
                    <div key={i} className="flex items-center justify-between gap-2 p-2.5 rounded-xl" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                      <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{f.name} — <span style={{ color: "var(--text-muted)" }}>{f.detail}</span></span>
                      <span className="text-[12px] font-mono font-semibold shrink-0" style={{ color: AMBER }}>+{f.points}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Sector allocation bars */}
            <div className="mt-5">
              <div className="flex items-center gap-1.5 mb-2.5">
                <Scale size={13} style={{ color: "var(--text-muted)" }} />
                <span className="eyebrow">Sector Allocation</span>
              </div>
              <SectorBars sectors={allocation.by_sector} />
            </div>

            {/* Strong vs Weak holdings */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-5">
              <div data-testid="strong-holdings" className="rounded-2xl p-3.5" style={{ background: "var(--gain-bg)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-1.5 mb-2">
                  <Award size={13} style={{ color: "var(--gain)" }} />
                  <span className="eyebrow" style={{ color: "var(--gain)" }}>Strong Holdings</span>
                </div>
                {movers.strong.length ? movers.strong.map(h => (
                  <div key={h.symbol} className="flex items-center justify-between py-1">
                    <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>{h.symbol}</span>
                    <span className="text-[12px] font-mono font-semibold" style={{ color: "var(--gain)" }}>+{h.pnl_pct.toFixed(2)}%</span>
                  </div>
                )) : <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>No profitable positions yet.</p>}
              </div>
              <div data-testid="weak-holdings" className="rounded-2xl p-3.5" style={{ background: "var(--loss-bg)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-1.5 mb-2">
                  <TrendingDown size={13} style={{ color: "var(--loss)" }} />
                  <span className="eyebrow" style={{ color: "var(--loss)" }}>Weak Holdings</span>
                </div>
                {movers.weak.length ? movers.weak.map(h => (
                  <div key={h.symbol} className="flex items-center justify-between py-1">
                    <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>{h.symbol}</span>
                    <span className="text-[12px] font-mono font-semibold" style={{ color: "var(--loss)" }}>{h.pnl_pct.toFixed(2)}%</span>
                  </div>
                )) : <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>No losing positions — nice work.</p>}
              </div>
            </div>

            {/* Rebalancing suggestions */}
            <div className="mt-5">
              <div className="flex items-center gap-1.5 mb-2.5">
                <Sparkles size={13} style={{ color: "var(--ai-accent)" }} />
                <span className="eyebrow">Rebalancing Suggestions</span>
              </div>
              <div className="space-y-1.5">
                {suggestions.map((r, i) => (
                  <div key={i} data-testid={`rebalance-${i}`} className="flex items-start gap-2 p-2.5 rounded-xl" style={{ background: r.tone === "positive" ? "var(--gain-bg)" : "var(--bg-surface)", border: "1px solid var(--border)" }}>
                    <span className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0" style={{ background: SEVERITY_COLOR[r.tone] || "var(--ai-accent)" }} />
                    <span className="text-[12px] leading-snug" style={{ color: "var(--text-secondary)" }}>{r.text}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* AI alert feed */}
            {alerts.length > 0 && (
              <div className="mt-5">
                <div className="flex items-center gap-1.5 mb-2.5">
                  <Brain size={13} style={{ color: "var(--ai-accent)" }} />
                  <span className="eyebrow">AI Alerts</span>
                </div>
                <div className="space-y-1.5">
                  {alerts.slice(0, 6).map((a, i) => (
                    <div key={i} className="flex items-start gap-2 p-2.5 rounded-xl" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                      <span className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0" style={{ background: SEVERITY_COLOR[a.severity] || "var(--text-muted)" }} />
                      <div className="min-w-0">
                        <span className="text-[12px] leading-snug" style={{ color: "var(--text-secondary)" }}>{a.message}</span>
                        {a.action && <span className="text-[11px] font-semibold ml-1" style={{ color: SEVERITY_COLOR[a.severity] || "var(--text-muted)" }}>→ {a.action}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Dividend analysis — real trailing data, else explicit unavailable */}
            <div data-testid="dim-dividend" className="mt-5 p-3.5 rounded-xl" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <Coins size={13} style={{ color: dividends.available ? "var(--gain)" : "var(--text-muted)" }} />
                  <span className="eyebrow">Dividend Analysis</span>
                </div>
                {dividends.available ? (
                  <span className="text-[13px] font-mono font-semibold" style={{ color: "var(--gain)" }}>
                    ~₹{formatNumber(dividends.annual_income, 0)}/yr · {dividends.portfolio_yield?.toFixed(2)}%
                  </span>
                ) : (
                  <span className="badge-status" style={{ background: "var(--hover)", color: "var(--text-muted)" }}>Unavailable</span>
                )}
              </div>
              <p className="text-[11px] leading-snug" style={{ color: "var(--text-muted)" }}>
                {dividends.available
                  ? "Estimated annual dividend income from live trailing dividend rates across your holdings."
                  : (dividends.reason || "Live dividend data is not available for the current holdings.")}
              </p>
            </div>
          </div>
        </Reveal>
      )}

      {/* ═══════════ HOLDINGS ═══════════ */}
      {tab === "Holdings" && (
        <Reveal>
          <div className="glass-card overflow-hidden">
            <div className="p-4 pb-0">
              <h3 className="eyebrow">Holdings ({holdings.length})</h3>
            </div>
            <div className="overflow-x-auto">
              <table className="data-table mt-3">
                <thead>
                  <tr>
                    <th>Stock</th><th>Qty</th><th>Avg Price</th><th>Current Price</th>
                    <th>Invested</th><th>Value</th><th>P/L</th><th>P/L %</th><th>Day Change</th>
                  </tr>
                </thead>
                <tbody>
                  {holdings.map(h => <HoldingRow key={h.symbol} holding={h} />)}
                  {holdings.length === 0 && (
                    <tr><td colSpan={9} className="text-center py-8 text-[13px]" style={{ color: "var(--text-muted)" }}>No holdings found. Connect a broker or log a trade to build your portfolio.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </Reveal>
      )}

      {/* ═══════════ PERFORMANCE ═══════════ */}
      {tab === "Performance" && (
        <Reveal>
          <div className="glass-card p-5">
            <h3 className="eyebrow flex items-center gap-2 mb-4"><LineChartIcon size={13} style={{ color: "var(--ai-accent)" }} /> Equity Curve</h3>
            {performance?.available ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
                  <DimensionCard icon={Activity} label="Return" value={`${performance.pct_return >= 0 ? "+" : ""}${performance.pct_return.toFixed(2)}%`} valueColor={performance.pct_return >= 0 ? "var(--gain)" : "var(--loss)"} note={`₹${formatNumber(performance.abs_return)} since ${performance.start_date}`} />
                  <DimensionCard icon={LineChartIcon} label="Data Points" value={performance.points} note="Daily EOD snapshots" />
                  <DimensionCard icon={ArrowUpRight} label="Best Day" value={performance.best_day ? `+${performance.best_day.pct.toFixed(2)}%` : "--"} valueColor="var(--gain)" note={performance.best_day?.date} />
                  <DimensionCard icon={ArrowDownRight} label="Worst Day" value={performance.worst_day ? `${performance.worst_day.pct.toFixed(2)}%` : "--"} valueColor="var(--loss)" note={performance.worst_day?.date} />
                </div>
                <ResponsiveContainer width="100%" height={260}>
                  <AreaChart data={equityCurve} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
                    <defs>
                      <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#6366F1" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="#6366F1" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickLine={false} axisLine={false} minTickGap={24} />
                    <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickLine={false} axisLine={false} width={54} tickFormatter={v => `₹${formatNumber(v, 0)}`} domain={["auto", "auto"]} />
                    <Tooltip contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", borderRadius: 12, fontSize: 11 }} formatter={v => [`₹${formatNumber(v)}`, "Value"]} />
                    <Area type="monotone" dataKey="value" stroke="#6366F1" strokeWidth={2} fill="url(#eqGrad)" />
                  </AreaChart>
                </ResponsiveContainer>
              </>
            ) : (
              <div className="flex flex-col items-center justify-center text-center py-12">
                <LineChartIcon size={32} style={{ color: "var(--text-muted)" }} />
                <p className="text-[14px] font-semibold mt-3" style={{ color: "var(--text-primary)" }}>Equity curve is being recorded</p>
                <p className="text-[12px] mt-1 max-w-md" style={{ color: "var(--text-muted)" }}>
                  {performance?.reason || "Your performance chart appears after a couple of end-of-day snapshots. We build it forward from real marks — never with synthetic history."}
                </p>
              </div>
            )}
          </div>
        </Reveal>
      )}

      {/* ═══════════ ALLOCATION ═══════════ */}
      {tab === "Allocation" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Reveal>
            <div className="glass-card p-5 h-full">
              <h3 className="eyebrow mb-3">By Holding</h3>
              <AllocationPie data={pieData} />
              <div className="mt-4 space-y-1.5">
                {allocation.by_holding.map((h, i) => (
                  <div key={h.symbol} className="flex items-center justify-between text-[12px]">
                    <span className="flex items-center gap-2" style={{ color: "var(--text-secondary)" }}>
                      <span className="w-2 h-2 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />{h.symbol}
                    </span>
                    <span className="font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{h.pct.toFixed(1)}%</span>
                  </div>
                ))}
                {allocation.by_holding.length === 0 && <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>No holdings yet.</p>}
              </div>
            </div>
          </Reveal>
          <Reveal delay={0.06}>
            <div className="glass-card p-5 h-full">
              <h3 className="eyebrow mb-3">By Sector</h3>
              <SectorBars sectors={allocation.by_sector} />
            </div>
          </Reveal>
        </div>
      )}

      {/* ═══════════ AI REVIEW ═══════════ */}
      {tab === "AI Review" && (
        <Reveal>
          <div className="glass-card p-5">
            <div className="flex items-start justify-between gap-3 flex-wrap mb-3">
              <h3 className="eyebrow flex items-center gap-2"><Brain size={13} style={{ color: "var(--ai-accent)" }} /> AI Portfolio Review</h3>
              <button onClick={runAiReview} disabled={aiLoading || holdings.length === 0} className="btn-secondary btn-sm">
                {aiLoading ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                {aiLoading ? "Reviewing…" : aiReview ? "Regenerate" : "Generate Review"}
              </button>
            </div>
            {holdings.length === 0 ? (
              <p className="text-[13px]" style={{ color: "var(--text-muted)" }}>Add holdings to receive an AI portfolio review.</p>
            ) : aiReview ? (
              <div className="space-y-3">
                {aiReview.model_used && <span className="badge-status" style={{ background: "var(--hover)", color: "var(--ai-accent)" }}>{aiReview.model_used}</span>}
                <p className="text-[13.5px] leading-relaxed whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>{aiReview.content}</p>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center text-center py-10">
                <Brain size={30} style={{ color: "var(--ai-accent)" }} />
                <p className="text-[13px] mt-3 max-w-md" style={{ color: "var(--text-muted)" }}>
                  Generate a narrative review of your allocation, risk and rebalancing priorities — grounded in your live holdings and the health scan.
                </p>
              </div>
            )}
          </div>
        </Reveal>
      )}

      {/* ═══════════ TRANSACTIONS ═══════════ */}
      {tab === "Transactions" && (
        <Reveal>
          <div className="glass-card overflow-hidden">
            <div className="p-4 pb-0"><h3 className="eyebrow">Closed Trades</h3></div>
            {txLoading ? (
              <div className="p-5 space-y-2">{[...Array(4)].map((_, i) => <div key={i} className="h-8 skeleton rounded-lg" />)}</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table mt-3">
                  <thead>
                    <tr><th>Stock</th><th>Type</th><th>Entry</th><th>Exit</th><th>Qty</th><th>P/L</th><th>Closed</th></tr>
                  </thead>
                  <tbody>
                    {(transactions ?? []).map(t => {
                      const isPosT = (t.pnl ?? 0) >= 0;
                      return (
                        <tr key={t._id}>
                          <td><span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{t.symbol}</span></td>
                          <td className="text-[13px]">{t.type}</td>
                          <td className="font-mono text-[13px]">₹{formatNumber(t.entry_price)}</td>
                          <td className="font-mono text-[13px]">{t.exit_price != null ? `₹${formatNumber(t.exit_price)}` : "--"}</td>
                          <td className="font-mono text-[13px]">{t.quantity}</td>
                          <td className="font-mono text-[13px] font-semibold" style={{ color: isPosT ? "var(--gain)" : "var(--loss)" }}>
                            {t.pnl != null ? `${isPosT ? "+" : ""}₹${formatNumber(Math.abs(t.pnl))}` : "--"}
                          </td>
                          <td className="text-[12px]" style={{ color: "var(--text-muted)" }}>{t.exit_time ? new Date(t.exit_time).toLocaleDateString() : "--"}</td>
                        </tr>
                      );
                    })}
                    {(transactions ?? []).length === 0 && (
                      <tr><td colSpan={7} className="text-center py-8 text-[13px]" style={{ color: "var(--text-muted)" }}>No closed trades yet.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Reveal>
      )}

      {/* Zerodha Account — shown on Overview + Holdings for quick funds context */}
      {zerodhaAccount && ["Overview", "Holdings"].includes(tab) && (
        <Reveal>
          <div className="glass-card p-5">
            <h3 className="eyebrow mb-3 flex items-center gap-2">
              <Wallet size={13} /> Zerodha Account
              <span className="badge-status" style={{
                background: zerodhaAccount.status?.connected ? "var(--gain-bg)" : "var(--hover)",
                color: zerodhaAccount.status?.connected ? "var(--gain)" : "var(--text-muted)",
              }}>
                {zerodhaAccount.status?.mode?.toUpperCase()}
              </span>
            </h3>
            {zerodhaAccount.funds && (
              <div className="grid grid-cols-3 gap-4">
                <div><span className="stat-label">Available</span><div className="text-lg font-mono font-semibold" style={{ color: "var(--text-primary)" }}>₹{formatNumber(zerodhaAccount.funds.available)}</div></div>
                <div><span className="stat-label">Used</span><div className="text-lg font-mono font-semibold" style={{ color: "var(--text-primary)" }}>₹{formatNumber(zerodhaAccount.funds.used)}</div></div>
                <div><span className="stat-label">Total</span><div className="text-lg font-mono font-semibold" style={{ color: "var(--text-primary)" }}>₹{formatNumber(zerodhaAccount.funds.total)}</div></div>
              </div>
            )}
          </div>
        </Reveal>
      )}
    </div>
  );
}

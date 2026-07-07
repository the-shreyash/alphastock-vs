import { useState, useEffect } from "react";
import api from "../services/api";
import { formatNumber } from "../utils/formatters";
import { Wallet, RefreshCw, Download, ArrowUpRight, ArrowDownRight, Brain, Shield, AlertTriangle, Layers, Scale, Award, TrendingDown, Coins, Sparkles, Loader2 } from "lucide-react";
import { PieChart as RechartsPie, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import { motion } from "framer-motion";

const COLORS = ["#6366F1", "#10B981", "#F59E0B", "#F43F5E", "#06B6D4", "#8B5CF6", "#EC4899", "#14B8A6"];
const TABS = ["Overview", "Holdings", "Performance", "Allocation", "AI Review", "Transactions"];
const AMBER = "#F59E0B";

// Concentration guidelines used for overexposure detection. Kept conservative:
// a single name above 30% or a single sector above 40% flags rebalancing.
const STOCK_LIMIT = 30;
const SECTOR_LIMIT = 40;

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

const SEVERITY_COLOR = { critical: "var(--loss)", warning: AMBER, positive: "var(--gain)", info: "var(--ai-accent)" };

export default function Portfolio() {
  const [holdings, setHoldings] = useState([]);
  const [summary, setSummary] = useState(null);
  const [zerodhaAccount, setZerodhaAccount] = useState(null);
  const [health, setHealth] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("Overview");

  useEffect(() => { fetchPortfolio(); }, []);

  const fetchPortfolio = async () => {
    try {
      const [h, s, z, m] = await Promise.all([
        api.get("/portfolio"),
        api.get("/portfolio/summary"),
        api.get("/zerodha/account").catch(() => ({ data: null })),
        api.get("/monitor/health").catch(() => ({ data: null })),
      ]);
      setHoldings(h.data); setSummary(s.data); setZerodhaAccount(z.data); setHealth(m.data);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  };

  // Re-run the AI portfolio monitor on demand. POST /monitor/run recomputes
  // health, persists any critical/positive alerts, and returns the fresh result.
  const runAnalysis = async () => {
    setAnalyzing(true);
    try {
      const { data } = await api.post("/monitor/run");
      if (data) setHealth(data);
    } catch (err) { console.error(err); }
    finally { setAnalyzing(false); }
  };

  const pieData = holdings.map(h => ({ name: h.symbol, value: h.current_value || h.invested }));
  const totalPnl = summary?.total_pnl ?? 0;
  const totalPnlPct = summary?.total_pnl_pct ?? 0;
  const isPos = totalPnl >= 0;

  // ─── Derived intelligence (client-side over real holdings) ───────────────
  const enriched = holdings.map(h => {
    const invested = h.invested ?? (h.avg_price * h.quantity) ?? 0;
    const value = h.current_value ?? (h.current_price * h.quantity) ?? invested;
    const pnl = h.pnl ?? (value - invested);
    const pnlPct = h.pnl_pct ?? (invested ? (pnl / invested) * 100 : 0);
    const sector = h.sector && String(h.sector).trim() ? h.sector : "Unclassified";
    return { ...h, invested, value, pnl, pnlPct, sector };
  });
  const totalValue = enriched.reduce((a, h) => a + h.value, 0) || (summary?.current_value ?? 0);

  // Sector allocation weights
  const sectorMap = {};
  enriched.forEach(h => { sectorMap[h.sector] = (sectorMap[h.sector] || 0) + h.value; });
  const sectorWeights = Object.entries(sectorMap)
    .map(([name, val]) => ({ name, value: val, pct: totalValue ? (val / totalValue) * 100 : 0 }))
    .sort((a, b) => b.value - a.value);

  // Single-name weights (for overexposure)
  const stockWeights = enriched
    .map(h => ({ ...h, weight: totalValue ? (h.value / totalValue) * 100 : 0 }))
    .sort((a, b) => b.weight - a.weight);

  const overStocks = stockWeights.filter(s => s.weight > STOCK_LIMIT);
  const overSectors = sectorWeights.filter(s => s.pct > SECTOR_LIMIT);
  const overCount = overStocks.length + overSectors.length;

  // Strong vs weak holdings by P&L %
  const byPnl = [...enriched].sort((a, b) => b.pnlPct - a.pnlPct);
  const strong = byPnl.filter(h => h.pnlPct > 0).slice(0, 3);
  const weak = [...byPnl].reverse().filter(h => h.pnlPct < 0).slice(0, 3);

  // Diversification assessment
  const nHoldings = enriched.length;
  const nSectors = sectorWeights.length;
  const topWeight = stockWeights[0]?.weight ?? 0;
  const divLabel = nHoldings === 0 ? "—"
    : (nHoldings >= 8 && topWeight < 25) ? "Excellent"
    : (nHoldings >= 5 && topWeight < 35) ? "Good"
    : (nHoldings >= 3) ? "Moderate" : "Concentrated";
  const divColor = divLabel === "Excellent" || divLabel === "Good" ? "var(--gain)"
    : divLabel === "Moderate" ? AMBER : "var(--loss)";

  // Risk assessment (from AI health)
  const alerts = health?.alerts ?? [];
  const criticalAlerts = alerts.filter(a => a.severity === "critical");
  const atRisk = health?.at_risk ?? 0;
  const riskLevel = nHoldings === 0 ? "—"
    : (criticalAlerts.length >= 2 || atRisk >= 2) ? "High"
    : (criticalAlerts.length >= 1 || atRisk >= 1) ? "Elevated" : "Low";
  const riskColor = riskLevel === "High" ? "var(--loss)" : riskLevel === "Elevated" ? AMBER : "var(--gain)";

  // Profit potential (winners + AI target-proximity signals)
  const winners = enriched.filter(h => h.pnl > 0);
  const unrealizedGain = winners.reduce((a, h) => a + h.pnl, 0);
  const targetSignals = alerts.filter(a => ["NEAR_TARGET", "TARGET_HIT"].includes(a.type));

  // Rebalancing suggestions (overexposure + AI actionable alerts)
  const rebalance = [];
  overStocks.forEach(s => rebalance.push({ tone: "warning", text: `Trim ${s.symbol} — it is ${s.weight.toFixed(1)}% of the portfolio, above the ${STOCK_LIMIT}% single-stock guideline. Concentrated positions amplify single-name risk.` }));
  overSectors.forEach(s => rebalance.push({ tone: "warning", text: `Diversify out of ${s.name} — ${s.pct.toFixed(1)}% of holdings sit in one sector (above ${SECTOR_LIMIT}%), which raises correlated drawdown risk.` }));
  alerts
    .filter(a => ["RISK_HIGH", "STOP_LOSS_HIT", "SIGNIFICANT_LOSS", "RSI_OVERBOUGHT"].includes(a.type) && a.action)
    .slice(0, 4)
    .forEach(a => rebalance.push({ tone: a.severity === "critical" ? "critical" : "warning", text: `${a.symbol}: ${a.action}. ${a.message}` }));

  if (loading) return (
    <div className="space-y-5 animate-fade-in-up">
      <div className="h-8 w-40 rounded-xl skeleton" />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[...Array(4)].map((_, i) => <div key={i} className="stat-card space-y-3"><div className="h-3 w-1/2 skeleton rounded" /><div className="h-6 w-2/3 skeleton rounded-lg" /></div>)}
      </div>
      <div className="glass-card p-5 h-64 skeleton" />
    </div>
  );

  return (
    <div data-testid="portfolio-page" className="space-y-5">
      {/* Header */}
      <Reveal>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="page-title">Portfolio</h1>
            <p className="page-subtitle mt-1">Your holdings and performance overview</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={fetchPortfolio} className="btn-ghost btn-sm" style={{ padding: "10px" }}>
              <RefreshCw size={15} />
            </button>
            <button className="btn-ghost btn-sm" style={{ padding: "10px" }}>
              <Download size={15} />
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

      {/* Portfolio Value Strip */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Reveal delay={0} className="lg:col-span-2">
        <div className="glass-card p-5 h-full">
          <span className="stat-label block mb-1.5">Total Portfolio Value</span>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="stat-value">
              ₹{formatNumber(summary?.current_value ?? summary?.total_value ?? 0)}
            </span>
            <span className="text-sm font-mono font-semibold flex items-center gap-1" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
              {isPos ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
              {isPos ? "+" : ""}₹{formatNumber(Math.abs(totalPnl))} ({isPos ? "+" : ""}{totalPnlPct.toFixed(2)}%)
            </span>
          </div>
        </div>
        </Reveal>

        {/* Allocation Chart */}
        <Reveal delay={0.06}>
        <div className="glass-card p-5 h-full">
          <span className="stat-label block mb-2">Allocation by Holding</span>
          {pieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={120}>
              <RechartsPie>
                <Pie data={pieData} dataKey="value" cx="50%" cy="50%" innerRadius={30} outerRadius={50} paddingAngle={2} stroke="none">
                  {pieData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                </Pie>
                <Tooltip contentStyle={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", borderRadius: 12, fontSize: 11 }} />
              </RechartsPie>
            </ResponsiveContainer>
          ) : (
            <div className="h-[120px] flex items-center justify-center">
              <span className="body-text" style={{ color: "var(--text-muted)" }}>No holdings yet</span>
            </div>
          )}
        </div>
        </Reveal>
      </div>

      {/* ═══════════ Portfolio Intelligence ═══════════ */}
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
                {health?.summary || "Add open positions to unlock AI portfolio analysis."}
              </p>
              <div className="flex items-center gap-3 mt-1.5 flex-wrap">
                <span className="caption">Open positions: <b style={{ color: "var(--text-primary)" }}>{health?.open_positions ?? nHoldings}</b></span>
                <span className="caption">At risk: <b style={{ color: atRisk ? "var(--loss)" : "var(--text-primary)" }}>{atRisk}</b></span>
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
          <DimensionCard icon={Layers} testid="dim-diversification" label="Diversification" value={divLabel} valueColor={divColor}
            note={nHoldings ? `${nHoldings} holdings across ${nSectors} sector${nSectors === 1 ? "" : "s"}; top position ${topWeight.toFixed(0)}%.` : "No holdings yet."} />
          <DimensionCard icon={AlertTriangle} testid="dim-overexposure" label="Overexposure" value={overCount === 0 ? "Balanced" : `${overCount} flag${overCount === 1 ? "" : "s"}`}
            valueColor={overCount ? "var(--loss)" : "var(--gain)"}
            note={overCount ? "One or more positions/sectors exceed safe concentration limits." : `No single name >${STOCK_LIMIT}% or sector >${SECTOR_LIMIT}%.`} />
          <DimensionCard icon={Shield} testid="dim-risk" label="Risk" value={riskLevel} valueColor={riskColor}
            note={nHoldings ? `${criticalAlerts.length} critical alert${criticalAlerts.length === 1 ? "" : "s"}, ${atRisk} position${atRisk === 1 ? "" : "s"} near stop.` : "No exposure."} />
          <DimensionCard icon={Coins} testid="dim-profit-potential" label="Profit Potential" value={`₹${formatNumber(unrealizedGain, 0)}`} valueColor={unrealizedGain > 0 ? "var(--gain)" : "var(--text-primary)"}
            note={`${winners.length} winner${winners.length === 1 ? "" : "s"}${targetSignals.length ? `, ${targetSignals.length} near target` : ""}.`} />
        </div>

        {/* Sector allocation bars */}
        <div className="mt-5">
          <div className="flex items-center gap-1.5 mb-2.5">
            <Scale size={13} style={{ color: "var(--text-muted)" }} />
            <span className="eyebrow">Sector Allocation</span>
          </div>
          {sectorWeights.length ? (
            <div className="space-y-2">
              {sectorWeights.map((s, i) => {
                const over = s.pct > SECTOR_LIMIT;
                return (
                  <div key={s.name} data-testid={`sector-${s.name}`}>
                    <div className="flex items-center justify-between text-[12px] mb-1">
                      <span style={{ color: "var(--text-secondary)" }}>{s.name}</span>
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
          ) : (
            <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>No sector data — add holdings to see allocation.</p>
          )}
        </div>

        {/* Strong vs Weak holdings */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-5">
          <div data-testid="strong-holdings" className="rounded-2xl p-3.5" style={{ background: "var(--gain-bg)", border: "1px solid var(--border)" }}>
            <div className="flex items-center gap-1.5 mb-2">
              <Award size={13} style={{ color: "var(--gain)" }} />
              <span className="eyebrow" style={{ color: "var(--gain)" }}>Strong Holdings</span>
            </div>
            {strong.length ? strong.map(h => (
              <div key={h.symbol} className="flex items-center justify-between py-1">
                <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>{h.symbol}</span>
                <span className="text-[12px] font-mono font-semibold" style={{ color: "var(--gain)" }}>+{h.pnlPct.toFixed(2)}%</span>
              </div>
            )) : <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>No profitable positions yet.</p>}
          </div>
          <div data-testid="weak-holdings" className="rounded-2xl p-3.5" style={{ background: "var(--loss-bg)", border: "1px solid var(--border)" }}>
            <div className="flex items-center gap-1.5 mb-2">
              <TrendingDown size={13} style={{ color: "var(--loss)" }} />
              <span className="eyebrow" style={{ color: "var(--loss)" }}>Weak Holdings</span>
            </div>
            {weak.length ? weak.map(h => (
              <div key={h.symbol} className="flex items-center justify-between py-1">
                <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>{h.symbol}</span>
                <span className="text-[12px] font-mono font-semibold" style={{ color: "var(--loss)" }}>{h.pnlPct.toFixed(2)}%</span>
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
          {rebalance.length ? (
            <div className="space-y-1.5">
              {rebalance.map((r, i) => (
                <div key={i} data-testid={`rebalance-${i}`} className="flex items-start gap-2 p-2.5 rounded-xl" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                  <span className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0" style={{ background: SEVERITY_COLOR[r.tone] || "var(--ai-accent)" }} />
                  <span className="text-[12px] leading-snug" style={{ color: "var(--text-secondary)" }}>{r.text}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex items-start gap-2 p-2.5 rounded-xl" style={{ background: "var(--gain-bg)", border: "1px solid var(--border)" }}>
              <span className="text-[11px] mt-0.5" style={{ color: "var(--gain)" }}>✓</span>
              <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>Portfolio is well balanced — no rebalancing needed right now.</span>
            </div>
          )}
        </div>

        {/* AI alert feed (only when the monitor produced alerts) */}
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

        {/* Dividend analysis — not yet available from the data layer */}
        <div data-testid="dim-dividend" className="mt-5 flex items-center justify-between p-3 rounded-xl" style={{ background: "var(--bg-surface)", border: "1px dashed var(--border)" }}>
          <div className="flex items-center gap-2">
            <Coins size={13} style={{ color: "var(--text-muted)" }} />
            <span className="eyebrow" style={{ color: "var(--text-muted)" }}>Dividend Analysis</span>
          </div>
          <span className="badge-status" style={{ background: "var(--hover)", color: "var(--text-muted)" }}>Coming soon</span>
        </div>
      </div>
      </Reveal>

      {/* Holdings Table */}
      <Reveal>
      <div className="glass-card overflow-hidden">
        <div className="p-4 pb-0">
          <h3 className="eyebrow">Holdings ({holdings.length})</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="data-table mt-3">
            <thead>
              <tr>
                <th>Stock</th>
                <th>Qty</th>
                <th>Avg Price</th>
                <th>Current Price</th>
                <th>P/L</th>
                <th>P/L %</th>
                <th>Day Change</th>
              </tr>
            </thead>
            <tbody>
              {holdings.map(h => {
                const pnl = (h.current_price - h.avg_price) * h.quantity;
                const pnlPct = h.avg_price ? ((h.current_price - h.avg_price) / h.avg_price * 100) : 0;
                const isPosH = pnl >= 0;
                return (
                  <tr key={h.symbol}>
                    <td>
                      <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{h.symbol}</span>
                    </td>
                    <td className="font-mono text-[13px]">{h.quantity}</td>
                    <td className="font-mono text-[13px]">₹{formatNumber(h.avg_price)}</td>
                    <td className="font-mono text-[13px]">₹{formatNumber(h.current_price)}</td>
                    <td className="font-mono text-[13px] font-semibold" style={{ color: isPosH ? "var(--gain)" : "var(--loss)" }}>
                      {isPosH ? "+" : ""}₹{formatNumber(Math.abs(pnl))}
                    </td>
                    <td className="font-mono text-[13px] font-semibold" style={{ color: isPosH ? "var(--gain)" : "var(--loss)" }}>
                      {isPosH ? "+" : ""}{pnlPct.toFixed(2)}%
                    </td>
                    <td className="font-mono text-[13px]" style={{ color: (h.day_change_pct ?? 0) >= 0 ? "var(--gain)" : "var(--loss)" }}>
                      {(h.day_change_pct ?? 0) >= 0 ? "+" : ""}{(h.day_change_pct ?? 0).toFixed(2)}%
                    </td>
                  </tr>
                );
              })}
              {holdings.length === 0 && (
                <tr><td colSpan={7} className="text-center py-8 text-[13px]" style={{ color: "var(--text-muted)" }}>No holdings found. Start trading to build your portfolio.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      </Reveal>

      {/* Zerodha Account */}
      {zerodhaAccount && (
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

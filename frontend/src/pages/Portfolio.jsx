import { useState, useEffect } from "react";
import api from "../services/api";
import { formatCurrency, formatPercent, formatNumber } from "../utils/formatters";
import { Briefcase, TrendingUp, TrendingDown, PieChart, Wallet, RefreshCw, Download, ArrowUpRight, ArrowDownRight, Brain } from "lucide-react";
import { PieChart as RechartsPie, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";

const COLORS = ["#6366F1", "#10B981", "#F59E0B", "#F43F5E", "#06B6D4", "#8B5CF6", "#EC4899", "#14B8A6"];
const TABS = ["Overview", "Holdings", "Performance", "Allocation", "AI Review", "Transactions"];

export default function Portfolio() {
  const [holdings, setHoldings] = useState([]);
  const [summary, setSummary] = useState(null);
  const [zerodhaAccount, setZerodhaAccount] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("Overview");

  useEffect(() => { fetchPortfolio(); }, []);

  const fetchPortfolio = async () => {
    try {
      const [h, s, z] = await Promise.all([
        api.get("/portfolio"),
        api.get("/portfolio/summary"),
        api.get("/zerodha/account").catch(() => ({ data: null })),
      ]);
      setHoldings(h.data); setSummary(s.data); setZerodhaAccount(z.data);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  };

  const pieData = holdings.map(h => ({ name: h.symbol, value: h.current_value || h.invested }));
  const totalPnl = summary?.total_pnl ?? 0;
  const totalPnlPct = summary?.total_pnl_pct ?? 0;
  const isPos = totalPnl >= 0;

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
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight font-display" style={{ color: "var(--text-primary)" }}>Portfolio</h1>
          <p className="text-[13px] mt-0.5" style={{ color: "var(--text-secondary)" }}>Your holdings and performance overview</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={fetchPortfolio} className="p-2.5 rounded-xl transition-all" style={{ color: "var(--text-muted)" }}
            onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
            onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
            <RefreshCw size={15} />
          </button>
          <button className="p-2.5 rounded-xl transition-all" style={{ color: "var(--text-muted)" }}
            onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
            onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
            <Download size={15} />
          </button>
        </div>
      </div>

      {/* Tab Bar */}
      <div className="tab-bar">
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)} className={`tab-btn ${tab === t ? "active" : ""}`}>{t}</button>
        ))}
      </div>

      {/* Portfolio Value Strip */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="glass-card p-5 lg:col-span-2">
          <span className="text-[10px] font-bold uppercase tracking-[0.12em] block mb-1" style={{ color: "var(--text-muted)" }}>Total Portfolio Value</span>
          <div className="flex items-baseline gap-3">
            <span className="text-3xl font-bold font-mono" style={{ color: "var(--text-primary)" }}>
              ₹{formatNumber(summary?.total_value || 0)}
            </span>
            <span className="text-sm font-mono font-semibold flex items-center gap-1" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
              {isPos ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
              {isPos ? "+" : ""}₹{formatNumber(Math.abs(totalPnl))} ({isPos ? "+" : ""}{totalPnlPct.toFixed(2)}%)
            </span>
          </div>
        </div>

        {/* Allocation Chart */}
        <div className="glass-card p-5">
          <span className="text-[10px] font-bold uppercase tracking-[0.12em] block mb-2" style={{ color: "var(--text-muted)" }}>Allocation by Sector</span>
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
              <span className="text-[12px]" style={{ color: "var(--text-muted)" }}>No holdings yet</span>
            </div>
          )}
        </div>
      </div>

      {/* AI Portfolio Review */}
      <div className="glass-card p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
            <Brain size={13} style={{ color: "var(--ai-accent)" }} /> AI Portfolio Review
          </h3>
          <div className="relative w-14 h-14">
            <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
              <circle cx="18" cy="18" r="14" fill="none" stroke="var(--border)" strokeWidth="3" />
              <circle cx="18" cy="18" r="14" fill="none" stroke="var(--gain)" strokeWidth="3" strokeLinecap="round"
                strokeDasharray={`${87 * 0.88} ${88 - 87 * 0.88}`} className="score-gauge-circle" />
            </svg>
            <span className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-[13px] font-bold font-mono" style={{ color: "var(--gain)" }}>87</span>
              <span className="text-[7px]" style={{ color: "var(--text-muted)" }}>/100</span>
            </span>
          </div>
        </div>
        <div className="space-y-1.5">
          {["Well-diversified across sectors", "Consider reducing IT allocation", "Good risk management", "Boost pharmaceutical exposure"].map((tip, i) => (
            <div key={i} className="flex items-start gap-2 py-1">
              <span className="text-[10px] mt-0.5" style={{ color: "var(--gain)" }}>✓</span>
              <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{tip}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Holdings Table */}
      <div className="glass-card overflow-hidden">
        <div className="p-4 pb-0">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--text-muted)" }}>Holdings ({holdings.length})</h3>
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

      {/* Zerodha Account */}
      {zerodhaAccount && (
        <div className="glass-card p-5">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] mb-3 flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
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
              <div><span className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>Available</span><div className="text-lg font-mono font-semibold" style={{ color: "var(--text-primary)" }}>₹{formatNumber(zerodhaAccount.funds.available)}</div></div>
              <div><span className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>Used</span><div className="text-lg font-mono font-semibold" style={{ color: "var(--text-primary)" }}>₹{formatNumber(zerodhaAccount.funds.used)}</div></div>
              <div><span className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>Total</span><div className="text-lg font-mono font-semibold" style={{ color: "var(--text-primary)" }}>₹{formatNumber(zerodhaAccount.funds.total)}</div></div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

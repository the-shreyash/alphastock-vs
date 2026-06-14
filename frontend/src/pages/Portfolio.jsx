import { useState, useEffect } from "react";
import api from "../services/api";
import { formatCurrency, formatPercent } from "../utils/formatters";
import { Briefcase, TrendingUp, TrendingDown, PieChart, Wallet, RefreshCw } from "lucide-react";
import { PieChart as RechartsPie, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";

const COLORS = ["#6366F1", "#10B981", "#F59E0B", "#F43F5E", "#06B6D4", "#8B5CF6", "#EC4899", "#14B8A6"];

export default function Portfolio() {
  const [holdings, setHoldings] = useState([]);
  const [summary, setSummary] = useState(null);
  const [zerodhaAccount, setZerodhaAccount] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("platform");

  useEffect(() => {
    fetchPortfolio();
  }, []);

  const fetchPortfolio = async () => {
    try {
      const [h, s, z] = await Promise.all([
        api.get("/portfolio"),
        api.get("/portfolio/summary"),
        api.get("/zerodha/account").catch(() => ({ data: null })),
      ]);
      setHoldings(h.data);
      setSummary(s.data);
      setZerodhaAccount(z.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const pieData = holdings.map((h) => ({ name: h.symbol, value: h.current_value || h.invested }));

  return (
    <div data-testid="portfolio-page" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-medium tracking-tight" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>Portfolio</h1>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>Your holdings and performance overview</p>
        </div>
        <button onClick={fetchPortfolio} className="p-2 rounded-xl" style={{ color: "var(--text-muted)" }}>
          <RefreshCw size={16} />
        </button>
      </div>

      {/* Zerodha Account Overview */}
      {zerodhaAccount && (
        <div data-testid="zerodha-account" className="card-premium p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-bold uppercase tracking-widest flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
              <Wallet size={12} /> Zerodha Account
              <span className="text-[10px] px-2 py-0.5 rounded-lg" style={{ background: zerodhaAccount.status?.connected ? "rgba(16,185,129,0.08)" : "var(--bg-surface)", color: zerodhaAccount.status?.connected ? "var(--gain)" : "var(--text-muted)" }}>
                {zerodhaAccount.status?.mode?.toUpperCase()}
              </span>
            </h3>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <span className="text-[10px] font-bold uppercase" style={{ color: "var(--text-muted)" }}>Available Margin</span>
              <div className="text-xl font-mono font-semibold" style={{ color: "var(--gain)" }}>{formatCurrency(zerodhaAccount.funds?.equity?.available_margin)}</div>
            </div>
            <div>
              <span className="text-[10px] font-bold uppercase" style={{ color: "var(--text-muted)" }}>Used Margin</span>
              <div className="text-xl font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{formatCurrency(zerodhaAccount.funds?.equity?.used_margin)}</div>
            </div>
            <div>
              <span className="text-[10px] font-bold uppercase" style={{ color: "var(--text-muted)" }}>Opening Balance</span>
              <div className="text-xl font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{formatCurrency(zerodhaAccount.funds?.equity?.opening_balance)}</div>
            </div>
            <div>
              <span className="text-[10px] font-bold uppercase" style={{ color: "var(--text-muted)" }}>Zerodha Holdings</span>
              <div className="text-xl font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{zerodhaAccount.holdings?.holdings?.length || 0}</div>
            </div>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-0 border-b" style={{ borderColor: "var(--border)" }}>
        {[{ id: "platform", label: "Platform Trades" }, { id: "zerodha", label: "Zerodha Holdings" }].map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)} data-testid={`portfolio-tab-${t.id}`}
            className="px-4 py-2.5 text-xs uppercase tracking-widest font-medium transition-all border-b-2"
            style={{ borderColor: tab === t.id ? "var(--ai-accent)" : "transparent", color: tab === t.id ? "var(--text-primary)" : "var(--text-muted)" }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Summary Cards */}
      {tab === "platform" && summary && (
        <div data-testid="portfolio-summary" className="grid grid-cols-2 md:grid-cols-4 gap-1">
          <div className="card-premium  p-4">
            <span className="text-[10px] text-muted uppercase block">Invested</span>
            <span className="text-xl font-mono text-primary">{formatCurrency(summary.total_invested)}</span>
          </div>
          <div className="card-premium  p-4">
            <span className="text-[10px] text-muted uppercase block">Current Value</span>
            <span className="text-xl font-mono text-primary">{formatCurrency(summary.current_value)}</span>
          </div>
          <div className="card-premium  p-4">
            <span className="text-[10px] text-muted uppercase block">Total P&L</span>
            <span className={`text-xl font-mono ${summary.total_pnl >= 0 ? "text-gain" : "text-loss"}`}>
              {summary.total_pnl >= 0 ? "+" : ""}{formatCurrency(summary.total_pnl)}
            </span>
            <span className={`text-xs font-mono ml-1 ${summary.total_pnl_pct >= 0 ? "text-gain" : "text-loss"}`}>
              ({formatPercent(summary.total_pnl_pct)})
            </span>
          </div>
          <div className="card-premium  p-4">
            <span className="text-[10px] text-muted uppercase block">Capital</span>
            <span className="text-xl font-mono text-primary">{formatCurrency(summary.capital)}</span>
          </div>
        </div>
      )}

      {tab === "platform" && (
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-1">

      {/* Zerodha Holdings Tab */}
        {/* Holdings List */}
        <div className="lg:col-span-2">
          <div data-testid="holdings-list" className="card-premium ">
            <div className="p-3 border-b">
              <h3 className="text-xs text-muted uppercase tracking-widest">Holdings ({holdings.length})</h3>
            </div>
            {holdings.length === 0 ? (
              <div className="p-8 text-center text-muted text-sm">No holdings. Enter trades to build your portfolio.</div>
            ) : (
              <div className="divide-y divide-zinc-800/50">
                {holdings.map((h) => (
                  <div key={h.symbol} data-testid={`holding-${h.symbol}`} className="p-3 flex items-center justify-between bg-hover transition-colors">
                    <div>
                      <span className="text-sm text-primary font-medium">{h.symbol}</span>
                      <span className="text-[10px] text-muted ml-2">{h.name}</span>
                      <div className="text-[10px] text-muted font-mono mt-0.5">
                        Qty: {h.quantity} | Avg: {formatCurrency(h.avg_price)}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-mono text-primary">{formatCurrency(h.current_value)}</div>
                      <div className={`text-xs font-mono ${(h.pnl || 0) >= 0 ? "text-gain" : "text-loss"}`}>
                        {(h.pnl || 0) >= 0 ? "+" : ""}{formatCurrency(h.pnl)} ({formatPercent(h.pnl_pct)})
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Pie Chart */}
        <div className="card-premium  p-4">
          <h3 className="text-xs text-muted uppercase tracking-widest mb-3 flex items-center gap-2">
            <PieChart size={12} /> Allocation
          </h3>
          {pieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <RechartsPie>
                <Pie data={pieData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} dataKey="value" stroke="#262626" strokeWidth={1}>
                  {pieData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                </Pie>
                <Tooltip contentStyle={{ background: "#121212", border: "1px solid #262626", borderRadius: "2px", fontSize: "12px" }} />
              </RechartsPie>
            </ResponsiveContainer>
          ) : (
            <div className="h-48 flex items-center justify-center text-muted text-xs">No data</div>
          )}
          <div className="space-y-1 mt-2">
            {pieData.map((d, i) => (
              <div key={d.name} className="flex items-center gap-2 text-xs">
                <div className="w-2 h-2 rounded-xl" style={{ backgroundColor: COLORS[i % COLORS.length] }} />
                <span className="text-secondary">{d.name}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      )}
      {tab === "zerodha" && zerodhaAccount?.holdings?.holdings && (
        <div className="card-premium">
          <div className="p-4 border-b" style={{ borderColor: "var(--border)" }}>
            <h3 className="text-xs font-bold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>
              Zerodha Holdings ({zerodhaAccount.holdings.holdings.length})
              <span className="ml-2 text-[10px] px-2 py-0.5 rounded-lg" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
                {zerodhaAccount.holdings.source}
              </span>
            </h3>
          </div>
          {zerodhaAccount.holdings.holdings.length === 0 ? (
            <div className="p-8 text-center text-sm" style={{ color: "var(--text-muted)" }}>No Zerodha holdings found</div>
          ) : (
            <div>
              {zerodhaAccount.holdings.holdings.map((h) => (
                <div key={h.tradingsymbol} className="p-4 flex items-center justify-between border-b transition-all hover:bg-[var(--hover)]" style={{ borderColor: "var(--border)" }}>
                  <div>
                    <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{h.tradingsymbol}</span>
                    <span className="text-[10px] ml-2" style={{ color: "var(--text-muted)" }}>{h.exchange}</span>
                    <div className="text-[10px] font-mono mt-0.5" style={{ color: "var(--text-muted)" }}>
                      Qty: {h.quantity} | Avg: {formatCurrency(h.average_price)}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{formatCurrency(h.last_price)}</div>
                    <div className="text-xs font-mono" style={{ color: (h.pnl || 0) >= 0 ? "var(--gain)" : "var(--loss)" }}>
                      {(h.pnl || 0) >= 0 ? "+" : ""}{formatCurrency(h.pnl)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

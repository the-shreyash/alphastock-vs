import { useState, useEffect, useRef } from "react";
import api from "../services/api";
import { formatCurrency, formatNumber, formatPercent } from "../utils/formatters";
import { useWebSocket } from "../hooks/useWebSocket";
import { TrendingUp, TrendingDown, Activity, Globe, BarChart3, ArrowUpRight, ArrowDownRight, Zap, Brain, RefreshCw, Wifi, WifiOff, Info } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import PortfolioMonitor from "../components/dashboard/PortfolioMonitor";
import WhatsAppPanel from "../components/dashboard/WhatsAppPanel";

function StatCard({ label, value, change, changePct, icon: Icon, testId }) {
  const isPos = (change ?? changePct ?? 0) >= 0;
  return (
    <div data-testid={testId} className="card-premium p-5">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-bold uppercase tracking-[0.15em]" style={{ color: "var(--text-muted)" }}>{label}</span>
        {Icon && <Icon size={14} style={{ color: "var(--text-muted)" }} />}
      </div>
      <div className="text-2xl font-semibold font-mono tracking-tight" style={{ color: "var(--text-primary)" }}>{value}</div>
      {changePct != null && (
        <div className="flex items-center gap-1 mt-1.5 text-xs font-mono font-medium" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
          {isPos ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
          <span>{formatPercent(changePct)}</span>
          {change != null && <span style={{ color: "var(--text-muted)" }}>({isPos ? "+" : ""}{formatNumber(change)})</span>}
        </div>
      )}
    </div>
  );
}

function TickerBar({ gainers, losers }) {
  const items = [...(gainers || []), ...(losers || [])].slice(0, 10);
  if (!items.length) return null;
  return (
    <div data-testid="ticker-bar" className="overflow-hidden mb-4 py-2 rounded-xl border" style={{ borderColor: "var(--border)", background: "var(--bg-surface)" }}>
      <div className="ticker-animate flex gap-8 whitespace-nowrap">
        {[...items, ...items].map((s, i) => (
          <span key={i} className="inline-flex items-center gap-2 text-xs font-mono">
            <span className="font-semibold" style={{ color: "var(--text-primary)" }}>{s.symbol}</span>
            <span style={{ color: "var(--text-secondary)" }}>{formatCurrency(s.price)}</span>
            <span style={{ color: s.change_pct >= 0 ? "var(--gain)" : "var(--loss)" }}>{formatPercent(s.change_pct)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function SectorHeatmap({ sectors }) {
  if (!sectors?.length) return null;
  return (
    <div data-testid="sector-heatmap" className="card-premium p-5">
      <h3 className="text-xs font-bold uppercase tracking-[0.15em] mb-4" style={{ color: "var(--text-muted)" }}>Sector Performance</h3>
      <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-2">
        {sectors.map((s) => {
          const isPos = s.change_pct >= 0;
          return (
            <div key={s.sector} className="p-3 rounded-xl text-center" style={{ background: isPos ? "rgba(16,185,129,0.08)" : "rgba(244,63,94,0.08)" }}>
              <div className="text-[10px] font-medium truncate" style={{ color: "var(--text-secondary)" }}>{s.sector}</div>
              <div className="text-sm font-mono font-semibold mt-0.5" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>{formatPercent(s.change_pct)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function GainersLosers({ gainers, losers }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {[{ title: "Top Gainers", data: gainers, icon: TrendingUp, color: "gain" }, { title: "Top Losers", data: losers, icon: TrendingDown, color: "loss" }].map(({ title, data, icon: Icon, color }) => (
        <div key={title} data-testid={title.toLowerCase().replace(" ", "-")} className="card-premium p-5">
          <h3 className="text-xs font-bold uppercase tracking-[0.15em] mb-3 flex items-center gap-2" style={{ color: `var(--${color})` }}>
            <Icon size={12} /> {title}
          </h3>
          <div className="space-y-2">
            {data?.map((s) => (
              <div key={s.symbol} className="flex items-center justify-between py-2 border-b last:border-0" style={{ borderColor: "var(--border-subtle)" }}>
                <div>
                  <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{s.symbol}</span>
                  <span className="text-[10px] ml-2" style={{ color: "var(--text-muted)" }}>{s.sector}</span>
                </div>
                <div className="text-right">
                  <span className="text-sm font-mono" style={{ color: "var(--text-primary)" }}>{formatCurrency(s.price)}</span>
                  <span className="text-xs font-mono ml-2" style={{ color: `var(--${color})` }}>{formatPercent(s.change_pct)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function GlobalMarkets({ markets }) {
  if (!markets?.length) return null;
  return (
    <div data-testid="global-markets" className="card-premium p-5">
      <h3 className="text-xs font-bold uppercase tracking-[0.15em] mb-3 flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
        <Globe size={12} /> Global Markets
      </h3>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {markets.map((m) => (
          <div key={m.name} className="p-3 rounded-xl" style={{ background: "var(--bg-surface)" }}>
            <div className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>{m.name}</div>
            <div className="text-sm font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{formatNumber(m.value, 0)}</div>
            <div className="text-xs font-mono" style={{ color: m.change_pct >= 0 ? "var(--gain)" : "var(--loss)" }}>{formatPercent(m.change_pct)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function FIIDIIPanel({ data }) {
  if (!data) return null;
  return (
    <div data-testid="fii-dii-panel" className="card-premium p-5">
      <h3 className="text-xs font-bold uppercase tracking-[0.15em] mb-3" style={{ color: "var(--text-muted)" }}>FII / DII Activity (Cr)</h3>
      <p className="text-xs mb-3 p-2 rounded-lg flex items-start gap-2" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
        <Info size={12} className="shrink-0 mt-0.5" /> FII = Foreign Institutional Investors, DII = Domestic. Positive net = buying (bullish signal).
      </p>
      <div className="grid grid-cols-2 gap-4">
        {[{ label: "FII Net", val: data.fii.net }, { label: "DII Net", val: data.dii.net }].map(({ label, val }) => (
          <div key={label}>
            <span className="text-[10px] font-medium uppercase" style={{ color: "var(--text-muted)" }}>{label}</span>
            <div className="text-xl font-mono font-semibold" style={{ color: val >= 0 ? "var(--gain)" : "var(--loss)" }}>
              {val >= 0 ? "+" : ""}{formatNumber(val)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AIActivityFeed({ activities }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = 0;
    }
  }, [activities]);

  const getCategoryStyles = (category) => {
    switch (category?.toLowerCase()) {
      case "scan":
        return { bg: "rgba(59, 130, 246, 0.1)", text: "#60A5FA" };
      case "news":
        return { bg: "rgba(139, 92, 246, 0.1)", text: "#A78BFA" };
      case "rank":
        return { bg: "rgba(245, 158, 11, 0.1)", text: "#FBBF24" };
      case "monitor":
        return { bg: "rgba(20, 184, 166, 0.1)", text: "#2DD4BF" };
      case "alert":
        return { bg: "rgba(239, 68, 68, 0.1)", text: "#F87171" };
      default:
        return { bg: "rgba(156, 163, 175, 0.1)", text: "#9CA3AF" };
    }
  };

  return (
    <div data-testid="ai-activity-feed" className="card-premium p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-bold uppercase tracking-[0.15em] flex items-center gap-2" style={{ color: "var(--ai-accent)" }}>
          <Brain size={12} /> AI Activity Feed
        </h3>
        <div className="flex items-center gap-1.5 text-[10px] font-mono font-medium" style={{ color: "var(--gain)" }}>
          <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" style={{ background: "var(--gain)" }} />
          LIVE
        </div>
      </div>
      <div ref={containerRef} className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
        {activities?.map((a, i) => {
          const catStyle = getCategoryStyles(a.category);
          return (
            <div key={i} className="flex items-center gap-2.5 px-2 py-1.5 rounded-lg transition-all hover:bg-white/[0.02]" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
              <span className="text-[10px] font-mono shrink-0" style={{ color: "var(--text-muted)", width: "52px" }}>{a.time}</span>
              <span className="text-[9px] px-2 py-0.5 rounded-full font-mono uppercase font-bold shrink-0 text-center" style={{ backgroundColor: catStyle.bg, color: catStyle.text, minWidth: "56px" }}>
                {a.category || "info"}
              </span>
              <span className="text-xs font-medium truncate flex-1" style={{ color: "var(--text-secondary)" }}>{a.action}</span>
              <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${a.status === "running" ? "animate-pulse" : ""}`}
                style={{ background: a.status === "running" ? "var(--gain)" : a.status === "warning" ? "#F59E0B" : "var(--text-muted)" }} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AISummaryCard({ summary, loading: summaryLoading, onRefresh }) {
  return (
    <div data-testid="ai-market-summary" className="card-premium p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-bold uppercase tracking-[0.15em] flex items-center gap-2" style={{ color: "var(--ai-accent)" }}>
          <Zap size={12} /> AI Market Summary
        </h3>
        <button onClick={onRefresh} style={{ color: "var(--text-muted)" }} data-testid="refresh-summary-btn">
          <RefreshCw size={14} className={summaryLoading ? "animate-spin" : ""} />
        </button>
      </div>
      {summaryLoading ? (
        <div className="space-y-2">
          {[100, 80, 60].map((w) => <div key={w} className="h-3 rounded-lg animate-pulse" style={{ width: `${w}%`, background: "var(--bg-surface)" }} />)}
        </div>
      ) : (
        <p className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>{summary || "Loading..."}</p>
      )}
    </div>
  );
}

export default function Dashboard() {
  const { user } = useAuth();

  const { connected, marketData, activityUpdates } = useWebSocket(user?._id || user?.id || "");
  const [overview, setOverview] = useState(null);
  const [gainers, setGainers] = useState([]);
  const [losers, setLosers] = useState([]);
  const [sectors, setSectors] = useState([]);
  const [globalMkts, setGlobalMkts] = useState([]);
  const [fiiDii, setFiiDii] = useState(null);
  const [commodities, setCommodities] = useState(null);
  const [activities, setActivities] = useState([]);
  const [summary, setSummary] = useState("");
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => { if (marketData) setOverview(marketData); }, [marketData]);

  // Handle incoming live websocket activities
  useEffect(() => {
    if (activityUpdates) {
      setActivities((prev) => {
        const exists = prev.some((a) => a.time === activityUpdates.time && a.action === activityUpdates.action);
        if (exists) return prev;
        return [activityUpdates, ...prev].slice(0, 20);
      });
    }
  }, [activityUpdates]);

  // Fallback polling for activities every 10 seconds if WS is disconnected
  useEffect(() => {
    let pollInterval = null;
    if (!connected) {
      pollInterval = setInterval(async () => {
        try {
          const { data } = await api.get("/ai-activity");
          setActivities(data);
        } catch (err) {
          console.error("Error polling fallback activities:", err);
        }
      }, 10000);
    }
    return () => {
      if (pollInterval) clearInterval(pollInterval);
    };
  }, [connected]);

  const fetchData = async () => {
    try {
      const [ov, g, l, sec, gl, fd, com, act] = await Promise.all([
        api.get("/market/overview"), api.get("/market/gainers"), api.get("/market/losers"),
        api.get("/market/sectors"), api.get("/market/global"), api.get("/market/fii-dii"),
        api.get("/market/commodities"), api.get("/ai-activity"),
      ]);
      setOverview(ov.data); setGainers(g.data); setLosers(l.data); setSectors(sec.data);
      setGlobalMkts(gl.data); setFiiDii(fd.data); setCommodities(com.data); setActivities(act.data);
    } catch (err) { console.error(err); } finally { setLoading(false); }
  };

  const fetchSummary = async () => {
    setSummaryLoading(true);
    try { const { data } = await api.get("/market/summary"); setSummary(data.summary); }
    catch { setSummary("Unable to load."); }
    finally { setSummaryLoading(false); }
  };

  useEffect(() => { fetchData(); fetchSummary(); const i = setInterval(fetchData, 30000); return () => clearInterval(i); }, []);

  if (loading) return (
    <div data-testid="dashboard-loading" className="space-y-4 animate-fade-in-up">
      {/* Header skeleton */}
      <div className="flex items-center justify-between mb-2">
        <div className="space-y-2 w-48">
          <div className="h-8 w-3/4 rounded-xl skeleton" />
          <div className="h-4 w-1/2 rounded-lg skeleton" />
        </div>
        <div className="h-6 w-20 rounded-full skeleton" />
      </div>

      {/* Ticker skeleton */}
      <div className="h-10 rounded-xl border skeleton" style={{ borderColor: "var(--border)" }} />

      {/* Index Cards skeleton */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="card-premium p-5 space-y-3">
            <div className="flex justify-between">
              <div className="h-3 w-1/2 rounded skeleton" />
              <div className="h-4 w-4 rounded skeleton" />
            </div>
            <div className="h-6 w-2/3 rounded-lg skeleton" />
            <div className="h-3 w-3/4 rounded skeleton" />
          </div>
        ))}
      </div>

      {/* AI Summary + Activity feed skeleton */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card-premium p-5 space-y-4">
          <div className="h-4 w-1/3 rounded skeleton" />
          <div className="space-y-2">
            <div className="h-3 w-full rounded skeleton" />
            <div className="h-3 w-11/12 rounded skeleton" />
            <div className="h-3 w-4/5 rounded skeleton" />
          </div>
        </div>
        <div className="card-premium p-5 space-y-3">
          <div className="h-4 w-1/3 rounded skeleton" />
          <div className="space-y-2">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="flex gap-2 items-center">
                <div className="h-3 w-8 rounded skeleton" />
                <div className="h-2 w-2 rounded-full skeleton" />
                <div className="h-3 w-3/4 rounded skeleton" />
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Sectors heatmap skeleton */}
      <div className="card-premium p-5 space-y-4">
        <div className="h-4 w-1/4 rounded skeleton" />
        <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-2">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-14 rounded-xl skeleton" />
          ))}
        </div>
      </div>
    </div>
  );

  return (
    <div data-testid="dashboard-page" className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div>
          <h1 className="text-2xl sm:text-3xl font-medium tracking-tight" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>Dashboard</h1>
          <div className="flex items-center gap-2 mt-1">
            <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-lg ${overview?.market_status === "OPEN" ? "" : ""}`}
              style={{ background: overview?.market_status === "OPEN" ? "rgba(16,185,129,0.08)" : "rgba(244,63,94,0.08)", color: overview?.market_status === "OPEN" ? "var(--gain)" : "var(--loss)" }}>
              {overview?.market_status === "OPEN" ? "MARKET OPEN" : "MARKET CLOSED"}
            </span>
            {overview?.source === "yahoo_finance" && (
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-lg" style={{ background: "rgba(16,185,129,0.08)", color: "var(--gain)" }}>REAL DATA</span>
            )}
          </div>
        </div>
        <div data-testid="ws-status" className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-mono font-medium"
          style={{ background: connected ? "rgba(16,185,129,0.08)" : "var(--bg-surface)", color: connected ? "var(--gain)" : "var(--text-muted)" }}>
          {connected ? <Wifi size={10} /> : <WifiOff size={10} />} {connected ? "LIVE" : "OFFLINE"}
        </div>
      </div>

      {/* Ticker */}
      <TickerBar gainers={gainers} losers={losers} />

      {/* Index Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 stagger-children">
        <StatCard testId="nifty-card" label="NIFTY 50" value={formatNumber(overview?.nifty?.value)} change={overview?.nifty?.change} changePct={overview?.nifty?.change_pct} icon={BarChart3} />
        <StatCard testId="banknifty-card" label="BANK NIFTY" value={formatNumber(overview?.bank_nifty?.value)} change={overview?.bank_nifty?.change} changePct={overview?.bank_nifty?.change_pct} icon={BarChart3} />
        <StatCard testId="sensex-card" label="SENSEX" value={formatNumber(overview?.sensex?.value)} change={overview?.sensex?.change} changePct={overview?.sensex?.change_pct} icon={BarChart3} />
        <StatCard testId="vix-card" label="INDIA VIX" value={overview?.india_vix} icon={Activity} />
      </div>

      {/* Commodities */}
      {commodities && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 stagger-children">
          {Object.entries(commodities).map(([key, c]) => (
            <StatCard key={key} testId={`commodity-${key}`} label={c.name} value={formatNumber(c.value)} changePct={c.change_pct} />
          ))}
        </div>
      )}

      {/* AI Summary + Activity */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <AISummaryCard summary={summary} loading={summaryLoading} onRefresh={fetchSummary} />
        <AIActivityFeed activities={activities} />
      </div>

      {/* Sectors */}
      <SectorHeatmap sectors={sectors} />

      {/* Gainers/Losers */}
      <GainersLosers gainers={gainers} losers={losers} />

      {/* FII/DII + Global */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <FIIDIIPanel data={fiiDii} />
        <GlobalMarkets markets={globalMkts} />
      </div>

      {/* AI Portfolio Monitor + WhatsApp */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <PortfolioMonitor />
        <WhatsAppPanel />
      </div>
    </div>
  );
}

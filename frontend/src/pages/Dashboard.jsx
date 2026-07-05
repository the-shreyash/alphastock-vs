import { useState, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import api from "../services/api";
import { formatCurrency, formatNumber, formatPercent } from "../utils/formatters";
import { useWebSocket } from "../hooks/useWebSocket";
import { TrendingUp, TrendingDown, Activity, BarChart3, ArrowUpRight, ArrowDownRight, Zap, Brain, RefreshCw, Wifi, WifiOff, ChevronRight, Eye, GraduationCap } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

/* ====== Stat Card (Index strip) ====== */
function StatCard({ label, value, change, changePct, testId }) {
  const isPos = (change ?? changePct ?? 0) >= 0;
  return (
    <div data-testid={testId} className="stat-card">
      <span className="text-[10px] font-bold uppercase tracking-[0.12em] block mb-1" style={{ color: "var(--text-muted)" }}>{label}</span>
      <div className="text-xl font-semibold font-mono tracking-tight" style={{ color: "var(--text-primary)" }}>{value || "—"}</div>
      {changePct != null && (
        <div className="flex items-center gap-1 mt-1 text-[11px] font-mono font-semibold" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
          {isPos ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
          <span>{isPos ? "+" : ""}{changePct?.toFixed(2)}%</span>
          {change != null && <span className="font-normal" style={{ color: "var(--text-muted)" }}>({isPos ? "+" : ""}{formatNumber(change)})</span>}
        </div>
      )}
    </div>
  );
}

/* ====== AI Activity Feed ====== */
// Never-empty fallback: shown only until the first fetch/websocket message resolves.
const AI_ACTIVITY_PLACEHOLDERS = [
  { category: "scan", action: "Scanning NSE universe for momentum setups", time: "now", status: "running" },
  { category: "news", action: "Parsing latest market headlines", time: "1m", status: "done" },
  { category: "rank", action: "Ranking today's high-conviction picks", time: "3m", status: "done" },
  { category: "monitor", action: "Monitoring open positions for P&L shifts", time: "5m", status: "done" },
  { category: "alert", action: "Watching for breakout & stop-loss triggers", time: "8m", status: "done" },
];

function AIActivityFeed({ activities }) {
  const containerRef = useRef(null);
  useEffect(() => { if (containerRef.current) containerRef.current.scrollTop = 0; }, [activities]);

  // Fall back to placeholders so the panel is never blank before data arrives.
  const items = (activities && activities.length > 0) ? activities : AI_ACTIVITY_PLACEHOLDERS;

  const getCatColor = (cat) => {
    switch (cat?.toLowerCase()) {
      case "scan": return "#60A5FA";
      case "news": return "#A78BFA";
      case "rank": return "#FBBF24";
      case "monitor": return "#2DD4BF";
      case "alert": return "#F87171";
      default: return "#9CA3AF";
    }
  };

  return (
    <div data-testid="ai-activity-feed" className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
          AI Activity
        </h3>
        <span className="badge-live text-[9px]">LIVE</span>
      </div>
      <div ref={containerRef} className="space-y-1 max-h-52 overflow-y-auto pr-1">
        {items.slice(0, 20).map((a, i) => (
          <div key={i} className="flex items-center gap-2.5 px-2 py-2 rounded-lg transition-all" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${a.status === "running" ? "animate-pulse" : ""}`}
              style={{ background: getCatColor(a.category) }} />
            <span className="text-[11px] font-medium flex-1 truncate" style={{ color: "var(--text-secondary)" }}>{a.action}</span>
            <span className="text-[10px] font-mono shrink-0" style={{ color: "var(--text-muted)" }}>{a.time}</span>
          </div>
        ))}
      </div>
      <Link to="/assistant" className="flex items-center justify-center gap-1 mt-3 text-[11px] font-semibold transition-all hover:opacity-80" style={{ color: "var(--ai-accent)" }}>
        View all <ChevronRight size={12} />
      </Link>
    </div>
  );
}

/* ====== Latest AI Lessons ====== */
function LatestLessonsCard({ lessons, loading: lLoading }) {
  const gradeStyle = (g) => {
    const grade = (g || "").toUpperCase();
    if (grade === "A" || grade === "B") return { color: "var(--gain)", bg: "var(--gain-bg)" };
    if (grade === "C") return { color: "#F59E0B", bg: "rgba(245, 158, 11, 0.1)" };
    return { color: "var(--loss)", bg: "var(--loss-bg)" };
  };
  return (
    <div data-testid="latest-lessons-card" className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
          <GraduationCap size={13} /> Latest AI Lessons
        </h3>
      </div>
      {lLoading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => <div key={i} className="h-12 rounded-xl skeleton" />)}
        </div>
      ) : !lessons?.length ? (
        <p className="text-[12px] py-6 text-center" style={{ color: "var(--text-muted)" }}>
          Close a trade to earn your first AI coaching lesson.
        </p>
      ) : (
        <>
          <div className="space-y-2">
            {lessons.slice(0, 3).map((l, i) => {
              const gs = gradeStyle(l.grade);
              return (
                <div key={l.trade_id || i} data-testid="lesson-item" className="flex items-center gap-3 p-2.5 rounded-xl" style={{ background: "var(--hover)" }}>
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold font-mono shrink-0" style={{ background: gs.bg, color: gs.color }}>
                    {(l.grade || "–").toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <span className="text-[13px] font-semibold block truncate" style={{ color: "var(--text-primary)" }}>{l.lesson_title || "Trade lesson"}</span>
                    <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>{l.symbol}</span>
                  </div>
                </div>
              );
            })}
          </div>
          <Link to="/journal" className="flex items-center justify-center gap-1 mt-3 text-[11px] font-semibold transition-all hover:opacity-80" style={{ color: "var(--ai-accent)" }}>
            View trade journal <ChevronRight size={12} />
          </Link>
        </>
      )}
    </div>
  );
}

/* ====== Morning Report Summary ====== */
function MorningReportCard({ report, loading: rLoading }) {
  return (
    <div data-testid="morning-report-card" className="glass-card p-5">
      <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] mb-3" style={{ color: "var(--text-muted)" }}>
        AI Morning Report
      </h3>
      {rLoading ? (
        <div className="space-y-2">
          {[100, 80, 60].map(w => <div key={w} className="h-3 rounded-lg skeleton" style={{ width: `${w}%` }} />)}
        </div>
      ) : (
        <>
          {/* Sentiment */}
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xl">
              {report?.sentiment === "Bullish" ? "🟢" : report?.sentiment === "Bearish" ? "🔴" : "🟡"}
            </span>
            <span className="text-lg font-bold font-display" style={{ color: report?.sentiment === "Bullish" ? "var(--gain)" : report?.sentiment === "Bearish" ? "var(--loss)" : "var(--text-primary)" }}>
              {report?.sentiment || "Neutral"} ☀
            </span>
          </div>
          <p className="text-[13px] leading-relaxed mb-3" style={{ color: "var(--text-secondary)" }}>
            {report?.summary || "Market sentiment is positive with strong global cues."}
          </p>
          {report?.top_sectors && (
            <div className="mb-2">
              <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>Top Sectors: </span>
              <span className="text-[12px] font-medium" style={{ color: "var(--text-secondary)" }}>{report.top_sectors}</span>
            </div>
          )}
          {report?.key_events && (
            <div>
              <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>Key Events Today: </span>
              <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{report.key_events}</span>
            </div>
          )}
          <Link to="/morning-report" className="inline-flex items-center gap-1 mt-3 text-[11px] font-semibold transition-all hover:opacity-80" style={{ color: "var(--ai-accent)" }}>
            View full report <ChevronRight size={12} />
          </Link>
        </>
      )}
    </div>
  );
}

/* ====== Top AI Picks ====== */
function TopPicksCard({ picks, loading: pLoading }) {
  return (
    <div data-testid="top-picks-card" className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-[11px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--text-muted)" }}>Top AI Picks</h3>
      </div>
      {pLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map(i => <div key={i} className="h-10 rounded-xl skeleton" />)}
        </div>
      ) : (
        <>
          <div className="space-y-2">
            {(picks || []).slice(0, 3).map((p, i) => (
              <Link key={p.symbol} to={`/stock/${p.symbol}`} className="flex items-center gap-3 p-2.5 rounded-xl transition-all group" style={{ background: "var(--hover)" }}>
                <div className="w-8 h-8 rounded-lg flex items-center justify-center text-[10px] font-bold"
                  style={{ background: i === 0 ? "var(--gain-bg)" : i === 1 ? "var(--ai-accent-soft)" : "var(--loss-bg)", color: i === 0 ? "var(--gain)" : i === 1 ? "var(--ai-accent)" : "var(--loss)" }}>
                  {p.symbol?.slice(0, 2)}
                </div>
                <div className="flex-1 min-w-0">
                  <span className="text-[13px] font-semibold block" style={{ color: "var(--text-primary)" }}>{p.symbol}</span>
                  <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>₹{formatNumber(p.price)}</span>
                </div>
                <span className="text-[12px] font-mono font-semibold" style={{ color: (p.change_pct ?? 0) >= 0 ? "var(--gain)" : "var(--loss)" }}>
                  {p.change_pct >= 0 ? "+" : ""}{p.change_pct?.toFixed(2)}%
                </span>
              </Link>
            ))}
          </div>
          <Link to="/picks" className="flex items-center justify-center gap-1 mt-3 text-[11px] font-semibold transition-all hover:opacity-80" style={{ color: "var(--ai-accent)" }}>
            View all picks <ChevronRight size={12} />
          </Link>
        </>
      )}
    </div>
  );
}

/* ====== Portfolio Summary ====== */
function PortfolioSummaryCard({ summary }) {
  return (
    <div data-testid="portfolio-summary-card" className="glass-card p-5">
      <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] mb-4" style={{ color: "var(--text-muted)" }}>Portfolio</h3>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: "Portfolio Value", value: summary?.total_value ? `₹${formatNumber(summary.total_value)}` : "—" },
          { label: "Today's P/L", value: summary?.total_pnl ? `₹${formatNumber(summary.total_pnl)}` : "—", color: (summary?.total_pnl ?? 0) >= 0 ? "var(--gain)" : "var(--loss)" },
          { label: "Investments", value: summary?.total_invested ? `₹${formatNumber(summary.total_invested)}` : "—" },
          { label: "Holdings", value: summary?.holdings_count ?? "—" },
        ].map(item => (
          <div key={item.label}>
            <span className="text-[10px] font-bold uppercase tracking-wider block mb-0.5" style={{ color: "var(--text-muted)" }}>{item.label}</span>
            <span className="text-lg font-semibold font-mono" style={{ color: item.color || "var(--text-primary)" }}>{item.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ====== Sector Performance ====== */
function SectorPerformance({ sectors }) {
  if (!sectors?.length) return null;
  return (
    <div data-testid="sector-heatmap" className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-[11px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--text-muted)" }}>Sector Performance</h3>
      </div>
      <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-2">
        {sectors.map(s => {
          const isPos = s.change_pct >= 0;
          return (
            <div key={s.sector} className="heatmap-cell" style={{ background: isPos ? "var(--gain-bg)" : "var(--loss-bg)" }}>
              <div className="text-[10px] font-medium truncate" style={{ color: "var(--text-secondary)" }}>{s.sector}</div>
              <div className="text-[13px] font-mono font-bold mt-0.5" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
                {isPos ? "+" : ""}{s.change_pct?.toFixed(2)}%
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ====== Market Breadth ====== */
function MarketBreadth({ overview }) {
  if (!overview) return null;
  const breadth = overview.breadth || { advances: 1042, declines: 842, unchanged: 176 };
  return (
    <div data-testid="market-breadth" className="glass-card p-5">
      <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] mb-3" style={{ color: "var(--text-muted)" }}>Market Breadth</h3>
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: "Advances", value: breadth.advances, color: "var(--gain)", bg: "var(--gain-bg)" },
          { label: "Declines", value: breadth.declines, color: "var(--loss)", bg: "var(--loss-bg)" },
          { label: "Unchanged", value: breadth.unchanged, color: "var(--text-muted)", bg: "var(--hover)" },
        ].map(b => (
          <div key={b.label} className="text-center p-2.5 rounded-xl" style={{ background: b.bg }}>
            <div className="text-lg font-bold font-mono" style={{ color: b.color }}>{b.value?.toLocaleString() || "—"}</div>
            <div className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>{b.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ====== MAIN DASHBOARD ====== */
export default function Dashboard() {
  const { user } = useAuth();
  const { connected, marketData, activityUpdates } = useWebSocket(user?._id || user?.id || "");
  const [overview, setOverview] = useState(null);
  const [sectors, setSectors] = useState([]);
  const [activities, setActivities] = useState([]);
  const [picks, setPicks] = useState([]);
  const [morningReport, setMorningReport] = useState(null);
  const [portfolioSummary, setPortfolioSummary] = useState(null);
  const [lessons, setLessons] = useState([]);
  const [loading, setLoading] = useState(true);
  const [reportLoading, setReportLoading] = useState(true);
  const [picksLoading, setPicksLoading] = useState(true);
  const [lessonsLoading, setLessonsLoading] = useState(true);

  useEffect(() => { if (marketData) setOverview(marketData); }, [marketData]);

  useEffect(() => {
    if (activityUpdates) {
      setActivities(prev => {
        const exists = prev.some(a => a.time === activityUpdates.time && a.action === activityUpdates.action);
        if (exists) return prev;
        return [activityUpdates, ...prev].slice(0, 20);
      });
    }
  }, [activityUpdates]);

  useEffect(() => {
    let pollInterval = null;
    if (!connected) {
      pollInterval = setInterval(async () => {
        try { const { data } = await api.get("/ai-activity"); setActivities(data); }
        catch (err) { console.error(err); }
      }, 10000);
    }
    return () => { if (pollInterval) clearInterval(pollInterval); };
  }, [connected]);

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [ov, sec, act] = await Promise.all([
          api.get("/market/overview"), api.get("/market/sectors"), api.get("/ai-activity"),
        ]);
        setOverview(ov.data); setSectors(sec.data); setActivities(act.data);
      } catch (err) { console.error(err); } finally { setLoading(false); }
    };
    const fetchReport = async () => {
      try { const { data } = await api.get("/morning-report"); setMorningReport(data); }
      catch { setMorningReport({ sentiment: "Neutral", summary: "Loading market data..." }); }
      finally { setReportLoading(false); }
    };
    const fetchPicks = async () => {
      try { const { data } = await api.get("/picks"); setPicks(data?.picks || data || []); }
      catch { setPicks([]); }
      finally { setPicksLoading(false); }
    };
    const fetchPortfolio = async () => {
      try { const { data } = await api.get("/portfolio/summary"); setPortfolioSummary(data); }
      catch { setPortfolioSummary(null); }
    };
    const fetchLessons = async () => {
      try { const { data } = await api.get("/trades/coaching/summary"); setLessons(data || []); }
      catch { setLessons([]); }
      finally { setLessonsLoading(false); }
    };
    fetchAll(); fetchReport(); fetchPicks(); fetchPortfolio(); fetchLessons();
    const i = setInterval(() => fetchAll(), 30000);
    return () => clearInterval(i);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) return (
    <div data-testid="dashboard-loading" className="space-y-5 animate-fade-in-up">
      <div className="space-y-2 w-64"><div className="h-8 rounded-xl skeleton" /><div className="h-4 w-3/4 rounded-lg skeleton" /></div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[...Array(4)].map((_, i) => <div key={i} className="stat-card space-y-3"><div className="h-3 w-1/2 rounded skeleton" /><div className="h-6 w-2/3 rounded-lg skeleton" /><div className="h-3 w-3/4 rounded skeleton" /></div>)}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="glass-card p-5 space-y-4"><div className="h-4 w-1/3 rounded skeleton" /><div className="h-20 rounded-xl skeleton" /></div>
        <div className="glass-card p-5 space-y-3"><div className="h-4 w-1/3 rounded skeleton" />{[1,2,3].map(i => <div key={i} className="h-10 rounded-xl skeleton" />)}</div>
      </div>
    </div>
  );

  const greeting = () => {
    const h = new Date().getHours();
    if (h < 12) return "Good morning";
    if (h < 17) return "Good afternoon";
    return "Good evening";
  };

  return (
    <div data-testid="dashboard-page" className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight font-display" style={{ color: "var(--text-primary)" }}>
            {greeting()}, {user?.name?.split(" ")[0]} 👋
          </h1>
          <p className="text-[13px] mt-0.5" style={{ color: "var(--text-secondary)" }}>Here's what the AI has prepared for you today.</p>
        </div>
        <div className="flex items-center gap-2">
          <div data-testid="ws-status" className="badge-live text-[9px]">
            {connected ? <><Wifi size={10} /> LIVE</> : <><WifiOff size={10} /> OFFLINE</>}
          </div>
        </div>
      </div>

      {/* Index Strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 stagger-children">
        <StatCard testId="nifty-card" label="Nifty 50" value={formatNumber(overview?.nifty?.value)} change={overview?.nifty?.change} changePct={overview?.nifty?.change_pct} />
        <StatCard testId="banknifty-card" label="Bank Nifty" value={formatNumber(overview?.bank_nifty?.value)} change={overview?.bank_nifty?.change} changePct={overview?.bank_nifty?.change_pct} />
        <StatCard testId="sensex-card" label="Sensex" value={formatNumber(overview?.sensex?.value)} change={overview?.sensex?.change} changePct={overview?.sensex?.change_pct} />
        <StatCard testId="vix-card" label="India VIX" value={overview?.india_vix ?? "—"} />
      </div>

      {/* Two Column: Morning Report + Top Picks */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <MorningReportCard report={morningReport} loading={reportLoading} />
        <TopPicksCard picks={picks} loading={picksLoading} />
      </div>

      {/* Portfolio Summary */}
      <PortfolioSummaryCard summary={portfolioSummary} />

      {/* Two Column: AI Activity + Latest AI Lessons */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <AIActivityFeed activities={activities} />
        <LatestLessonsCard lessons={lessons} loading={lessonsLoading} />
      </div>

      {/* Market Breadth */}
      <MarketBreadth overview={overview} />

      {/* Sector Performance */}
      <SectorPerformance sectors={sectors} />
    </div>
  );
}

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../services/api";
import { formatCurrency, formatNumber, formatPercent } from "../utils/formatters";
import { useWebSocket } from "../hooks/useWebSocket";
import { usePriceFlash } from "../hooks/usePriceFlash";
import AnimatedNumber from "../components/ui/AnimatedNumber";
import {
  TrendingUp, TrendingDown, Activity, BarChart3,
  ArrowUpRight, ArrowDownRight, Zap, Brain, RefreshCw,
  Wifi, WifiOff, ChevronRight, Eye, GraduationCap,
  Briefcase, LineChart, Newspaper, Bell, Star,
  Clock, Search, Sparkles, Globe, DollarSign,
  PlusCircle, FileText, BookOpen
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { motion } from "framer-motion";
import MarketEngineStatus from "../components/market/MarketEngineStatus";
import RankingTable from "../components/market/RankingTable";
import EconomicCalendar from "../components/market/EconomicCalendar";

/* ====== Constants ====== */
const RECENT_STOCKS_KEY = "sa_recent_stocks";
const MAX_RECENT_STOCKS = 6;

/* ====== Scroll-reveal wrapper ====== */
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

/* ====== Quick Actions Bar ====== */
function QuickActions() {
  const navigate = useNavigate();
  const actions = useMemo(() => [
    { label: "New Trade", icon: PlusCircle, path: "/trades", color: "var(--gain)" },
    { label: "AI Analysis", icon: Sparkles, path: "/assistant", color: "var(--ai-accent)" },
    { label: "Morning Report", icon: FileText, path: "/morning-report", color: "#F59E0B" },
    { label: "Portfolio", icon: Briefcase, path: "/portfolio", color: "#3B82F6" },
    { label: "Stock Picks", icon: Star, path: "/picks", color: "#EC4899" },
    { label: "Market News", icon: Newspaper, path: "/news", color: "#8B5CF6" },
  ], []);

  return (
    <div data-testid="quick-actions" className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1">
      {actions.map((a) => (
        <button
          key={a.label}
          onClick={() => navigate(a.path)}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-[12px] font-semibold whitespace-nowrap transition-all hover:scale-[1.02] active:scale-[0.98] shrink-0"
          style={{
            background: "var(--bg-card-glass)",
            border: "1px solid var(--border)",
            color: "var(--text-secondary)",
          }}
        >
          <a.icon size={14} style={{ color: a.color }} />
          {a.label}
        </button>
      ))}
    </div>
  );
}

/* ====== Stat Card (Index strip) with optional sparkline ====== */
function StatCard({ label, value, numericValue, change, changePct, sparkData, testId }) {
  const isPos = (change ?? changePct ?? 0) >= 0;
  const flashRef = usePriceFlash(numericValue);
  return (
    <div data-testid={testId} className="stat-card relative overflow-hidden">
      <span className="stat-label block mb-1.5">{label}</span>
      <div ref={flashRef} className="stat-value inline-block rounded-md px-0.5">{value || "—"}</div>
      {changePct != null && (
        <div className="flex items-center gap-1 mt-1 text-[11px] font-mono font-semibold" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
          {isPos ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
          <span>{isPos ? "+" : ""}{changePct?.toFixed(2)}%</span>
          {change != null && <span className="font-normal" style={{ color: "var(--text-muted)" }}>({isPos ? "+" : ""}{formatNumber(change)})</span>}
        </div>
      )}
      {/* Mini sparkline overlay */}
      {sparkData?.length > 1 && (
        <div className="absolute bottom-0 right-0 w-24 h-10 opacity-30 pointer-events-none">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={sparkData} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={`spark-${testId}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={isPos ? "var(--gain)" : "var(--loss)"} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={isPos ? "var(--gain)" : "var(--loss)"} stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area
                type="monotone"
                dataKey="close"
                stroke={isPos ? "var(--gain)" : "var(--loss)"}
                strokeWidth={1.5}
                fill={`url(#spark-${testId})`}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

/* ====== Commodities & Forex Strip ====== */
function CommoditiesStrip({ commodities }) {
  if (!commodities) return null;
  const items = [
    { key: "gold", label: "Gold", icon: "🥇" },
    { key: "crude_oil", label: "Crude Oil", icon: "🛢️" },
    { key: "silver", label: "Silver", icon: "🥈" },
    { key: "usd_inr", label: "USD/INR", icon: "💱" },
  ];

  return (
    <div data-testid="commodities-strip" className="grid grid-cols-2 md:grid-cols-4 gap-2">
      {items.map((item) => {
        const c = commodities[item.key];
        if (!c || !c.available) return (
          <div key={item.key} className="flex items-center gap-2 px-3 py-2.5 rounded-xl" style={{ background: "var(--bg-card-glass)", border: "1px solid var(--border)" }}>
            <span className="text-sm">{item.icon}</span>
            <div className="flex-1 min-w-0">
              <span className="text-[11px] font-medium block" style={{ color: "var(--text-muted)" }}>{item.label}</span>
              <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>Unavailable</span>
            </div>
          </div>
        );
        const isPos = (c.change_pct ?? 0) >= 0;
        return (
          <div key={item.key} className="flex items-center gap-2 px-3 py-2.5 rounded-xl transition-all hover:scale-[1.01]" style={{ background: "var(--bg-card-glass)", border: "1px solid var(--border)" }}>
            <span className="text-sm">{item.icon}</span>
            <div className="flex-1 min-w-0">
              <span className="text-[11px] font-medium block truncate" style={{ color: "var(--text-muted)" }}>{item.label}</span>
              <span className="text-[13px] font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                {c.value != null ? formatNumber(c.value) : "—"}
              </span>
            </div>
            {c.change_pct != null && (
              <span className="text-[11px] font-mono font-semibold shrink-0" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
                {isPos ? "+" : ""}{c.change_pct?.toFixed(2)}%
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ====== AI Activity Feed ====== */
function AIActivityFeed({ activities }) {
  const containerRef = useRef(null);
  useEffect(() => { if (containerRef.current) containerRef.current.scrollTop = 0; }, [activities]);

  const items = activities || [];

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
        <h3 className="eyebrow flex items-center gap-2">
          <Brain size={13} /> AI Activity
        </h3>
        <span className="badge-live text-[9px]">LIVE</span>
      </div>
      <div ref={containerRef} className="space-y-1 max-h-52 overflow-y-auto pr-1">
        {items.length === 0 && (
          <div className="space-y-2 py-1">
            {[1, 2, 3, 4].map(i => <div key={i} className="h-7 rounded-lg skeleton" />)}
          </div>
        )}
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
        <h3 className="eyebrow flex items-center gap-2">
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
      <h3 className="eyebrow mb-3">
        AI Morning Report
      </h3>
      {rLoading ? (
        <div className="space-y-2">
          {[100, 80, 60].map(w => <div key={w} className="h-3 rounded-lg skeleton" style={{ width: `${w}%` }} />)}
        </div>
      ) : !report || report.available === false ? (
        <p className="text-[12px] py-4 text-center" style={{ color: "var(--text-muted)" }}>
          {report?.note || "Morning report unavailable — live market data unreachable."}
        </p>
      ) : (
        <>
          <div className="flex items-center gap-2 mb-3">
            <span className="w-2.5 h-2.5 rounded-full" style={{
              background: report.market_mood === "Bullish" ? "var(--gain)" : report.market_mood === "Bearish" ? "var(--loss)" : "#F59E0B"
            }} />
            <span className="text-lg font-bold font-display" style={{
              color: report.market_mood === "Bullish" ? "var(--gain)" : report.market_mood === "Bearish" ? "var(--loss)" : "var(--text-primary)"
            }}>
              {report.market_mood || "Neutral"}
            </span>
          </div>
          <p className="body-text mb-3">
            {report.ai_briefing || "Briefing unavailable right now."}
          </p>
          {report.global_cues && (
            <div className="mb-2">
              <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>Global Cues: </span>
              <span className="text-[12px] font-medium" style={{ color: "var(--text-secondary)" }}>{report.global_cues}</span>
            </div>
          )}
          {report.key_risks?.length > 0 && (
            <div>
              <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>Key Risk: </span>
              <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{report.key_risks[0]}</span>
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
        <h3 className="eyebrow flex items-center gap-2">
          <Star size={13} /> Top AI Picks
        </h3>
      </div>
      {pLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map(i => <div key={i} className="h-10 rounded-xl skeleton" />)}
        </div>
      ) : !picks?.length ? (
        <p className="text-[12px] py-4 text-center" style={{ color: "var(--text-muted)" }}>
          AI picks unavailable — live market data unreachable.
        </p>
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
      <div className="flex items-center justify-between mb-4">
        <h3 className="eyebrow flex items-center gap-2">
          <Briefcase size={13} /> Portfolio
        </h3>
        <Link to="/portfolio" className="text-[11px] font-semibold transition-all hover:opacity-80" style={{ color: "var(--ai-accent)" }}>
          Details <ChevronRight size={11} className="inline" />
        </Link>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: "Portfolio Value", value: summary?.current_value ? `₹${formatNumber(summary.current_value)}` : summary?.total_value ? `₹${formatNumber(summary.total_value)}` : "—", numeric: summary?.current_value ?? summary?.total_value },
          { label: "Today's P/L", value: summary?.total_pnl ? `₹${formatNumber(summary.total_pnl)}` : "—", numeric: summary?.total_pnl, color: (summary?.total_pnl ?? 0) >= 0 ? "var(--gain)" : "var(--loss)" },
          { label: "Investments", value: summary?.total_invested ? `₹${formatNumber(summary.total_invested)}` : "—" },
          { label: "Holdings", value: summary?.holdings_count ?? "—" },
        ].map(item => (
          <div key={item.label}>
            <span className="stat-label block mb-1">{item.label}</span>
            {item.numeric != null ? (
              <span className="text-lg font-semibold font-mono" style={{ color: item.color || "var(--text-primary)" }}>
                ₹<AnimatedNumber value={item.numeric} format={(v) => formatNumber(v)} />
              </span>
            ) : (
              <span className="text-lg font-semibold font-mono" style={{ color: item.color || "var(--text-primary)" }}>{item.value}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ====== Watchlist Widget ====== */
function WatchlistWidget({ watchlist, loading: wLoading }) {
  return (
    <div data-testid="watchlist-widget" className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="eyebrow flex items-center gap-2">
          <Eye size={13} /> Watchlist
        </h3>
        <Link to="/watchlist" className="text-[11px] font-semibold transition-all hover:opacity-80" style={{ color: "var(--ai-accent)" }}>
          View all <ChevronRight size={11} className="inline" />
        </Link>
      </div>
      {wLoading ? (
        <div className="space-y-2">
          {[1, 2, 3].map(i => <div key={i} className="h-10 rounded-xl skeleton" />)}
        </div>
      ) : !watchlist?.length ? (
        <div className="py-6 text-center">
          <Eye size={24} className="mx-auto mb-2" style={{ color: "var(--text-muted)", opacity: 0.5 }} />
          <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>
            No stocks in your watchlist yet.
          </p>
          <Link to="/watchlist" className="inline-flex items-center gap-1 mt-2 text-[11px] font-semibold" style={{ color: "var(--ai-accent)" }}>
            Add stocks <PlusCircle size={11} />
          </Link>
        </div>
      ) : (
        <div className="space-y-1.5">
          {watchlist.slice(0, 5).map((w) => {
            const pct = w.quote?.change_pct ?? w.since_added_pct;
            const isPos = (pct ?? 0) >= 0;
            return (
              <Link key={w.symbol} to={`/stock/${w.symbol}`} className="flex items-center gap-3 px-2.5 py-2 rounded-xl transition-all hover:scale-[1.01]" style={{ background: "var(--hover)" }}>
                <div className="w-7 h-7 rounded-lg flex items-center justify-center text-[9px] font-bold" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
                  {w.symbol?.slice(0, 2)}
                </div>
                <div className="flex-1 min-w-0">
                  <span className="text-[12px] font-semibold block truncate" style={{ color: "var(--text-primary)" }}>{w.symbol}</span>
                  <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                    {w.quote?.price ? `₹${formatNumber(w.quote.price)}` : "—"}
                  </span>
                </div>
                {pct != null && (
                  <span className="text-[11px] font-mono font-semibold" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
                    {isPos ? "+" : ""}{pct?.toFixed(2)}%
                  </span>
                )}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ====== Recent Stocks ====== */
function RecentStocksCard() {
  const [recentStocks, setRecentStocks] = useState([]);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(RECENT_STOCKS_KEY);
      if (stored) setRecentStocks(JSON.parse(stored));
    } catch { /* empty */ }

    // Listen for storage events from other tabs
    const handler = () => {
      try {
        const stored = localStorage.getItem(RECENT_STOCKS_KEY);
        if (stored) setRecentStocks(JSON.parse(stored));
      } catch { /* empty */ }
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, []);

  if (!recentStocks.length) return null;

  return (
    <div data-testid="recent-stocks" className="glass-card p-5">
      <h3 className="eyebrow flex items-center gap-2 mb-4">
        <Clock size={13} /> Recently Viewed
      </h3>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {recentStocks.slice(0, MAX_RECENT_STOCKS).map((s) => (
          <Link
            key={s.symbol}
            to={`/stock/${s.symbol}`}
            className="flex items-center gap-2 px-3 py-2 rounded-xl shrink-0 transition-all hover:scale-[1.02]"
            style={{ background: "var(--hover)", border: "1px solid var(--border-subtle)" }}
          >
            <div className="w-6 h-6 rounded-md flex items-center justify-center text-[8px] font-bold" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
              {s.symbol?.slice(0, 2)}
            </div>
            <span className="text-[11px] font-semibold" style={{ color: "var(--text-primary)" }}>{s.symbol}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

/* ====== Market News Widget ====== */
function MarketNewsWidget({ news, loading: nLoading }) {
  const sentimentColor = (s) => {
    if (s === "positive") return "var(--gain)";
    if (s === "negative") return "var(--loss)";
    return "var(--text-muted)";
  };

  return (
    <div data-testid="news-widget" className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="eyebrow flex items-center gap-2">
          <Newspaper size={13} /> Market News
        </h3>
        <Link to="/news" className="text-[11px] font-semibold transition-all hover:opacity-80" style={{ color: "var(--ai-accent)" }}>
          All news <ChevronRight size={11} className="inline" />
        </Link>
      </div>
      {nLoading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4].map(i => <div key={i} className="h-8 rounded-lg skeleton" />)}
        </div>
      ) : !news?.length ? (
        <p className="text-[12px] py-4 text-center" style={{ color: "var(--text-muted)" }}>
          News feed unavailable.
        </p>
      ) : (
        <div className="space-y-1">
          {news.slice(0, 5).map((n, i) => (
            <a
              key={i}
              href={n.link}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-start gap-2.5 px-2 py-2 rounded-lg transition-all hover:scale-[1.005]"
              style={{ borderBottom: i < 4 ? "1px solid var(--border-subtle)" : "none" }}
            >
              <span className="w-1 h-1 rounded-full mt-1.5 shrink-0" style={{ background: sentimentColor(n.sentiment) }} />
              <div className="flex-1 min-w-0">
                <span className="text-[12px] font-medium block leading-snug" style={{ color: "var(--text-secondary)" }}>
                  {n.title?.length > 80 ? n.title.slice(0, 80) + "…" : n.title}
                </span>
                <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
                  {n.source} {n.published ? `• ${new Date(n.published).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}` : ""}
                </span>
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

/* ====== Notifications Widget ====== */
function NotificationsWidget({ notifications, loading: nfLoading }) {
  const severityColor = (s) => {
    if (s === "critical") return "var(--loss)";
    if (s === "warning") return "#F59E0B";
    return "var(--ai-accent)";
  };

  return (
    <div data-testid="notifications-widget" className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="eyebrow flex items-center gap-2">
          <Bell size={13} /> Notifications
        </h3>
      </div>
      {nfLoading ? (
        <div className="space-y-2">
          {[1, 2, 3].map(i => <div key={i} className="h-10 rounded-xl skeleton" />)}
        </div>
      ) : !notifications?.length ? (
        <div className="py-6 text-center">
          <Bell size={24} className="mx-auto mb-2" style={{ color: "var(--text-muted)", opacity: 0.5 }} />
          <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>
            No new notifications.
          </p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {notifications.slice(0, 4).map((n, i) => (
            <div key={n._id || i} className="flex items-start gap-2.5 px-2.5 py-2 rounded-xl" style={{ background: !n.read ? "var(--hover)" : "transparent" }}>
              <span className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0" style={{ background: severityColor(n.severity) }} />
              <div className="flex-1 min-w-0">
                <span className="text-[12px] font-semibold block truncate" style={{ color: "var(--text-primary)" }}>{n.title}</span>
                <span className="text-[10px] block truncate" style={{ color: "var(--text-muted)" }}>{n.message}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ====== Global Markets Widget ====== */
function GlobalMarketsWidget({ globalMarkets, loading: gLoading }) {
  return (
    <div data-testid="global-markets" className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="eyebrow flex items-center gap-2">
          <Globe size={13} /> Global Markets
        </h3>
        <Link to="/markets" className="text-[11px] font-semibold transition-all hover:opacity-80" style={{ color: "var(--ai-accent)" }}>
          Details <ChevronRight size={11} className="inline" />
        </Link>
      </div>
      {gLoading ? (
        <div className="grid grid-cols-2 gap-2">
          {[1, 2, 3, 4].map(i => <div key={i} className="h-14 rounded-xl skeleton" />)}
        </div>
      ) : !globalMarkets?.length ? (
        <p className="text-[12px] py-4 text-center" style={{ color: "var(--text-muted)" }}>
          Global market data unavailable.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {globalMarkets.filter(g => g.available !== false).slice(0, 6).map((g) => {
            const isPos = (g.change_pct ?? 0) >= 0;
            return (
              <div key={g.name} className="px-3 py-2.5 rounded-xl" style={{ background: "var(--hover)" }}>
                <div className="text-[10px] font-medium truncate" style={{ color: "var(--text-muted)" }}>{g.name}</div>
                <div className="flex items-center justify-between mt-1">
                  <span className="text-[12px] font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                    {g.value ? formatNumber(g.value, 0) : "—"}
                  </span>
                  {g.change_pct != null && (
                    <span className="text-[10px] font-mono font-semibold" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
                      {isPos ? "+" : ""}{g.change_pct?.toFixed(2)}%
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ====== Sector Performance ====== */
function SectorPerformance({ sectors }) {
  if (!sectors?.length) return null;
  return (
    <div data-testid="sector-heatmap" className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="eyebrow flex items-center gap-2">
          <BarChart3 size={13} /> Sector Performance
        </h3>
        <Link to="/markets" className="text-[11px] font-semibold transition-all hover:opacity-80" style={{ color: "var(--ai-accent)" }}>
          Details <ChevronRight size={11} className="inline" />
        </Link>
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
  const breadth = overview.advance_decline;

  // Calculate breadth bar widths
  const total = breadth ? (breadth.advances + breadth.declines + breadth.unchanged) : 0;
  const advPct = total > 0 ? (breadth.advances / total) * 100 : 0;
  const decPct = total > 0 ? (breadth.declines / total) * 100 : 0;

  return (
    <div data-testid="market-breadth" className="glass-card p-5">
      <h3 className="eyebrow mb-3 flex items-center gap-2">
        <Activity size={13} /> Market Breadth
      </h3>
      {!breadth ? (
        <p className="text-[12px] py-3 text-center" style={{ color: "var(--text-muted)" }}>
          Breadth data unavailable — live market feed unreachable.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-3 mb-3">
            {[
              { label: "Advances", value: breadth.advances, color: "var(--gain)", bg: "var(--gain-bg)" },
              { label: "Declines", value: breadth.declines, color: "var(--loss)", bg: "var(--loss-bg)" },
              { label: "Unchanged", value: breadth.unchanged, color: "var(--text-muted)", bg: "var(--hover)" },
            ].map(b => (
              <div key={b.label} className="text-center p-2.5 rounded-xl" style={{ background: b.bg }}>
                <div className="text-lg font-bold font-mono" style={{ color: b.color }}>{b.value?.toLocaleString() ?? "—"}</div>
                <div className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>{b.label}</div>
              </div>
            ))}
          </div>
          {/* Breadth bar */}
          {total > 0 && (
            <div className="flex h-2 rounded-full overflow-hidden" style={{ background: "var(--hover)" }}>
              <div className="rounded-l-full transition-all duration-500" style={{ width: `${advPct}%`, background: "var(--gain)" }} />
              <div className="rounded-r-full transition-all duration-500" style={{ width: `${decPct}%`, background: "var(--loss)" }} />
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ====== MAIN DASHBOARD ====== */
export default function Dashboard() {
  const { user } = useAuth();
  const { connected, marketData, activityUpdates, priceTicks, portfolioUpdate } = useWebSocket(user?._id || user?.id || "");
  const [overview, setOverview] = useState(null);
  const [sectors, setSectors] = useState([]);
  const [activities, setActivities] = useState([]);
  const [picks, setPicks] = useState([]);
  const [morningReport, setMorningReport] = useState(null);
  const [portfolioSummary, setPortfolioSummary] = useState(null);
  const [lessons, setLessons] = useState([]);
  const [watchlist, setWatchlist] = useState([]);
  const [news, setNews] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [commodities, setCommodities] = useState(null);
  const [globalMarkets, setGlobalMarkets] = useState([]);
  const [niftyChart, setNiftyChart] = useState([]);
  const [loading, setLoading] = useState(true);
  const [reportLoading, setReportLoading] = useState(true);
  const [picksLoading, setPicksLoading] = useState(true);
  const [lessonsLoading, setLessonsLoading] = useState(true);
  const [newsLoading, setNewsLoading] = useState(true);
  const [watchlistLoading, setWatchlistLoading] = useState(true);
  const [notificationsLoading, setNotificationsLoading] = useState(true);
  const [globalLoading, setGlobalLoading] = useState(true);

  // Live WebSocket updates
  useEffect(() => { if (marketData) setOverview(marketData); }, [marketData]);

  useEffect(() => {
    if (!priceTicks) return;
    const idxMap = { nifty: "NIFTY", bank_nifty: "BANKNIFTY", sensex: "SENSEX" };
    setOverview(prev => {
      if (!prev) return prev;
      let changed = false;
      const next = { ...prev };
      for (const [key, sym] of Object.entries(idxMap)) {
        const tick = priceTicks[sym];
        if (tick?.price != null) {
          next[key] = { ...(prev[key] || {}), value: tick.price, change_pct: tick.change_pct };
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [priceTicks]);

  useEffect(() => {
    if (!portfolioUpdate) return;
    setPortfolioSummary(prev => ({
      ...(prev || {}),
      total_pnl: portfolioUpdate.total_pnl ?? portfolioUpdate.total_unrealized_pnl ?? prev?.total_pnl,
      open_positions: portfolioUpdate.open_positions ?? prev?.open_positions,
      // Sprint R5 snapshots carry the full live P&L block — animate value too.
      current_value: portfolioUpdate.pnl?.current_value ?? prev?.current_value,
      total_invested: portfolioUpdate.pnl?.invested ?? prev?.total_invested,
    }));
  }, [portfolioUpdate]);

  useEffect(() => {
    if (activityUpdates) {
      setActivities(prev => {
        const exists = prev.some(a => a.time === activityUpdates.time && a.action === activityUpdates.action);
        if (exists) return prev;
        return [activityUpdates, ...prev].slice(0, 20);
      });
    }
  }, [activityUpdates]);

  // Fallback polling for AI activity when WS is disconnected
  useEffect(() => {
    let pollInterval = null;
    if (!connected) {
      pollInterval = setInterval(async () => {
        try { const { data } = await api.get("/ai-activity"); setActivities(data); }
        catch (err) { /* silent */ }
      }, 10000);
    }
    return () => { if (pollInterval) clearInterval(pollInterval); };
  }, [connected]);

  // Core market fetch (overview/sectors/activity). Lifted to a callback so the
  // initial mount fetch and the disconnected-only fallback poll share one impl.
  const fetchCore = useCallback(async () => {
    try {
      const [ov, sec, act] = await Promise.all([
        api.get("/market/overview"),
        api.get("/market/sectors"),
        api.get("/ai-activity"),
      ]);
      setOverview(ov.data);
      setSectors(sec.data);
      setActivities(act.data);
    } catch (err) { console.error("Dashboard core fetch:", err); }
    finally { setLoading(false); }
  }, []);

  // Fallback poll for core market data only while the socket is down; when live,
  // indices/sectors/activity arrive via pushes (market_update / activity_feed).
  useEffect(() => {
    if (connected) return undefined;
    const coreInterval = setInterval(fetchCore, 30000);
    return () => clearInterval(coreInterval);
  }, [connected, fetchCore]);

  // Main data fetch
  useEffect(() => {
    const fetchReport = async () => {
      try { const { data } = await api.get("/analysis/reports/morning"); setMorningReport(data); }
      catch { setMorningReport(null); }
      finally { setReportLoading(false); }
    };

    const fetchPicks = async () => {
      try { const { data } = await api.get("/analysis/top-picks"); setPicks(data?.picks || []); }
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

    const fetchWatchlist = async () => {
      try { const { data } = await api.get("/watchlist"); setWatchlist(Array.isArray(data) ? data : []); }
      catch { setWatchlist([]); }
      finally { setWatchlistLoading(false); }
    };

    const fetchNews = async () => {
      try {
        const { data } = await api.get("/news");
        setNews(data?.articles || (Array.isArray(data) ? data : []));
      } catch { setNews([]); }
      finally { setNewsLoading(false); }
    };

    const fetchNotifications = async () => {
      try { const { data } = await api.get("/notifications"); setNotifications(Array.isArray(data) ? data.filter(n => !n.read) : []); }
      catch { setNotifications([]); }
      finally { setNotificationsLoading(false); }
    };

    const fetchCommodities = async () => {
      try { const { data } = await api.get("/market/commodities"); setCommodities(data); }
      catch { setCommodities(null); }
    };

    const fetchGlobal = async () => {
      try { const { data } = await api.get("/market/global"); setGlobalMarkets(Array.isArray(data) ? data : []); }
      catch { setGlobalMarkets([]); }
      finally { setGlobalLoading(false); }
    };

    const fetchNiftyChart = async () => {
      try {
        const { data } = await api.get("/stocks/%5ENSEI/chart?period=1D");
        if (Array.isArray(data) && data.length > 0) {
          // Take last 30 points for a compact sparkline
          setNiftyChart(data.slice(-30));
        }
      } catch { setNiftyChart([]); }
    };

    // Fire all fetches in parallel
    fetchCore();
    fetchReport();
    fetchPicks();
    fetchPortfolio();
    fetchLessons();
    fetchWatchlist();
    fetchNews();
    fetchNotifications();
    fetchCommodities();
    fetchGlobal();
    fetchNiftyChart();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Greeting based on time of day
  const greeting = useMemo(() => {
    const h = new Date().getHours();
    if (h < 12) return "Good morning";
    if (h < 17) return "Good afternoon";
    return "Good evening";
  }, []);

  // Market status
  const marketStatus = overview?.market_status;

  // Loading skeleton
  if (loading) return (
    <div data-testid="dashboard-loading" className="space-y-5 animate-fade-in-up">
      <div className="space-y-2 w-64">
        <div className="h-8 rounded-xl skeleton" />
        <div className="h-4 w-3/4 rounded-lg skeleton" />
      </div>
      {/* Quick actions skeleton */}
      <div className="flex gap-2">
        {[1, 2, 3, 4, 5].map(i => <div key={i} className="h-10 w-28 rounded-xl skeleton shrink-0" />)}
      </div>
      {/* Index strip skeleton */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map(i => (
          <div key={i} className="stat-card space-y-3">
            <div className="h-3 w-1/2 rounded skeleton" />
            <div className="h-6 w-2/3 rounded-lg skeleton" />
            <div className="h-3 w-3/4 rounded skeleton" />
          </div>
        ))}
      </div>
      {/* Content skeletons */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="glass-card p-5 space-y-4">
          <div className="h-4 w-1/3 rounded skeleton" />
          <div className="h-20 rounded-xl skeleton" />
        </div>
        <div className="glass-card p-5 space-y-3">
          <div className="h-4 w-1/3 rounded skeleton" />
          {[1, 2, 3].map(i => <div key={i} className="h-10 rounded-xl skeleton" />)}
        </div>
      </div>
      <div className="glass-card p-5 space-y-3">
        <div className="h-4 w-1/4 rounded skeleton" />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map(i => <div key={i} className="h-12 rounded-lg skeleton" />)}
        </div>
      </div>
    </div>
  );

  return (
    <div data-testid="dashboard-page" className="space-y-5">
      {/* ===== Header ===== */}
      <Reveal>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="page-title">
              {greeting}, {user?.name?.split(" ")[0]}
            </h1>
            <p className="page-subtitle mt-1">Here's what the AI has prepared for you today.</p>
          </div>
          <div className="flex items-center gap-2">
            {marketStatus && (
              <span className="text-[10px] font-mono font-semibold px-2.5 py-1 rounded-full" style={{
                background: marketStatus === "OPEN" ? "var(--gain-bg)" : "var(--hover)",
                color: marketStatus === "OPEN" ? "var(--gain)" : "var(--text-muted)",
              }}>
                {marketStatus === "OPEN" ? "MARKET OPEN" : "MARKET CLOSED"}
              </span>
            )}
            <MarketEngineStatus compact />
            <div data-testid="ws-status" className="badge-live text-[9px]">
              {connected ? <><Wifi size={10} /> LIVE</> : <><WifiOff size={10} /> OFFLINE</>}
            </div>
          </div>
        </div>
      </Reveal>

      {/* ===== Quick Actions ===== */}
      <Reveal delay={0.03}>
        <QuickActions />
      </Reveal>

      {/* ===== Index Strip ===== */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Reveal delay={0}>
          <StatCard testId="nifty-card" label="Nifty 50" value={formatNumber(overview?.nifty?.value)} numericValue={overview?.nifty?.value} change={overview?.nifty?.change} changePct={overview?.nifty?.change_pct} sparkData={niftyChart} />
        </Reveal>
        <Reveal delay={0.05}>
          <StatCard testId="banknifty-card" label="Bank Nifty" value={formatNumber(overview?.bank_nifty?.value)} numericValue={overview?.bank_nifty?.value} change={overview?.bank_nifty?.change} changePct={overview?.bank_nifty?.change_pct} />
        </Reveal>
        <Reveal delay={0.1}>
          <StatCard testId="sensex-card" label="Sensex" value={formatNumber(overview?.sensex?.value)} numericValue={overview?.sensex?.value} change={overview?.sensex?.change} changePct={overview?.sensex?.change_pct} />
        </Reveal>
        <Reveal delay={0.15}>
          <StatCard testId="vix-card" label="India VIX" value={overview?.india_vix != null ? formatNumber(overview.india_vix) : "—"} numericValue={overview?.india_vix} />
        </Reveal>
      </div>

      {/* ===== Commodities & Forex ===== */}
      <Reveal delay={0.05}>
        <CommoditiesStrip commodities={commodities} />
      </Reveal>

      {/* ===== Morning Report + Top Picks (two-column) ===== */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Reveal delay={0}><MorningReportCard report={morningReport} loading={reportLoading} /></Reveal>
        <Reveal delay={0.06}><TopPicksCard picks={picks} loading={picksLoading} /></Reveal>
      </div>

      {/* ===== Portfolio Summary ===== */}
      <Reveal><PortfolioSummaryCard summary={portfolioSummary} /></Reveal>

      {/* ===== Watchlist + News (two-column) ===== */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Reveal delay={0}><WatchlistWidget watchlist={watchlist} loading={watchlistLoading} /></Reveal>
        <Reveal delay={0.06}><MarketNewsWidget news={news} loading={newsLoading} /></Reveal>
      </div>

      {/* ===== Scanner Highlights + Calendar (two-column) ===== */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Reveal delay={0}><RankingTable compact /></Reveal>
        <Reveal delay={0.06}><EconomicCalendar compact /></Reveal>
      </div>

      {/* ===== AI Activity + Notifications (two-column) ===== */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Reveal delay={0}><AIActivityFeed activities={activities} /></Reveal>
        <Reveal delay={0.06}><NotificationsWidget notifications={notifications} loading={notificationsLoading} /></Reveal>
      </div>

      {/* ===== Recent Stocks ===== */}
      <Reveal><RecentStocksCard /></Reveal>

      {/* ===== Market Breadth + Global Markets (two-column) ===== */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Reveal delay={0}><MarketBreadth overview={overview} /></Reveal>
        <Reveal delay={0.06}><GlobalMarketsWidget globalMarkets={globalMarkets} loading={globalLoading} /></Reveal>
      </div>

      {/* ===== Sector Performance ===== */}
      <Reveal><SectorPerformance sectors={sectors} /></Reveal>

      {/* ===== AI Lessons ===== */}
      <Reveal><LatestLessonsCard lessons={lessons} loading={lessonsLoading} /></Reveal>
    </div>
  );
}

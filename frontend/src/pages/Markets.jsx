import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import api from "../services/api";
import { formatNumber } from "../utils/formatters";
import { TrendingUp, TrendingDown, Globe, ArrowUpRight, ArrowDownRight, SlidersHorizontal, Layers, Calendar, Trophy } from "lucide-react";
import { motion } from "framer-motion";
import MarketScanner from "../components/market/MarketScanner";
import EconomicCalendar from "../components/market/EconomicCalendar";
import SectorAnalysis from "../components/market/SectorAnalysis";
import RankingTable from "../components/market/RankingTable";
import MarketEngineStatus from "../components/market/MarketEngineStatus";

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

function IndexStrip({ overview }) {
  if (!overview) return null;
  const indices = [
    { label: "Nifty 50", data: overview.nifty },
    { label: "Bank Nifty", data: overview.bank_nifty },
    { label: "Sensex", data: overview.sensex },
    { label: "India VIX", data: { value: overview.india_vix } },
  ];
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {indices.map((idx, i) => {
        const isPos = (idx.data?.change_pct ?? 0) >= 0;
        return (
          <Reveal key={idx.label} delay={i * 0.05}>
            <div className="stat-card">
              <span className="stat-label block mb-1.5">{idx.label}</span>
              <div className="text-xl font-semibold font-mono" style={{ color: "var(--text-primary)" }}>
                {idx.data?.value ? formatNumber(idx.data.value) : "—"}
              </div>
              {idx.data?.change_pct != null && (
                <div className="flex items-center gap-1 mt-1 text-[11px] font-mono font-semibold" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
                  {isPos ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                  {isPos ? "+" : ""}{idx.data.change_pct.toFixed(2)}%
                </div>
              )}
            </div>
          </Reveal>
        );
      })}
    </div>
  );
}

function MarketHeatmap({ gainers, losers }) {
  const allStocks = [...(gainers || []), ...(losers || [])].slice(0, 24);
  if (!allStocks.length) return null;

  return (
    <div className="glass-card p-5">
      <h3 className="eyebrow mb-4">Market Heatmap</h3>
      <div className="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-8 gap-1.5">
        {allStocks.map(s => {
          const isPos = s.change_pct >= 0;
          const intensity = Math.min(Math.abs(s.change_pct || 0) / 5, 1);
          return (
            <Link key={s.symbol} to={`/stock/${s.symbol}`} className="heatmap-cell" style={{
              background: isPos
                ? `rgba(0, 214, 143, ${0.08 + intensity * 0.25})`
                : `rgba(255, 107, 107, ${0.08 + intensity * 0.25})`,
            }}>
              <div className="text-[10px] font-bold truncate" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>{s.symbol}</div>
              <div className="text-[9px] font-mono font-semibold" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
                {isPos ? "+" : ""}{s.change_pct?.toFixed(1)}%
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function TopMovers({ title, data, icon: Icon, color }) {
  return (
    <div className="glass-card p-5">
      <h3 className="eyebrow mb-3 flex items-center gap-2" style={{ color: `var(--${color})` }}>
        <Icon size={13} /> {title}
      </h3>
      <div className="space-y-1">
        {data?.slice(0, 6).map(s => (
          <Link key={s.symbol} to={`/stock/${s.symbol}`} className="flex items-center justify-between py-2 px-2 rounded-lg transition-all" style={{ borderBottom: "1px solid var(--border-subtle)" }}
            onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
            onMouseLeave={e => e.currentTarget.style.background = "transparent"}
          >
            <div className="flex items-center gap-2">
              <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>{s.symbol}</span>
              {s.sector && <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: "var(--hover)", color: "var(--text-muted)" }}>{s.sector}</span>}
            </div>
            <div className="flex items-center gap-3">
              <span className="text-[12px] font-mono" style={{ color: "var(--text-secondary)" }}>₹{formatNumber(s.price)}</span>
              <span className="text-[11px] font-mono font-semibold min-w-[52px] text-right" style={{ color: `var(--${color})` }}>
                {s.change_pct >= 0 ? "+" : ""}{s.change_pct?.toFixed(2)}%
              </span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function MarketBreadth({ overview }) {
  const breadth = overview?.advance_decline;
  return (
    <div className="glass-card p-5">
      <h3 className="eyebrow mb-3">Market Breadth</h3>
      {!breadth ? (
        <p className="text-[12px] py-3 text-center" style={{ color: "var(--text-muted)" }}>
          Breadth data unavailable — live market feed unreachable.
        </p>
      ) : (
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: "Advances", value: breadth.advances, color: "var(--gain)", bg: "var(--gain-bg)" },
            { label: "Declines", value: breadth.declines, color: "var(--loss)", bg: "var(--loss-bg)" },
            { label: "Unchanged", value: breadth.unchanged, color: "var(--text-muted)", bg: "var(--hover)" },
          ].map(b => (
            <div key={b.label} className="text-center p-3 rounded-xl" style={{ background: b.bg }}>
              <div className="text-lg font-bold font-mono" style={{ color: b.color }}>{b.value?.toLocaleString() ?? "—"}</div>
              <div className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>{b.label}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function GlobalMarkets({ markets }) {
  if (!markets?.length) return null;
  return (
    <div className="glass-card p-5">
      <h3 className="eyebrow mb-3 flex items-center gap-2">
        <Globe size={13} /> Global Markets
      </h3>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {markets.map(m => (
          <div key={m.name} className="p-3 rounded-xl" style={{ background: "var(--hover)" }}>
            <div className="text-[10px] font-medium" style={{ color: "var(--text-muted)" }}>{m.name}</div>
            <div className="text-[13px] font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{formatNumber(m.value, 0)}</div>
            <div className="text-[11px] font-mono" style={{ color: (m.change_pct ?? 0) >= 0 ? "var(--gain)" : "var(--loss)" }}>
              {m.change_pct != null ? `${m.change_pct >= 0 ? "+" : ""}${m.change_pct.toFixed(2)}%` : "unavailable"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Tab navigation for market sections
const MARKET_TABS = [
  { key: "overview", label: "Overview", icon: TrendingUp },
  { key: "scanner", label: "Scanner", icon: SlidersHorizontal },
  { key: "rankings", label: "Rankings", icon: Trophy },
  { key: "sectors", label: "Sectors", icon: Layers },
  { key: "calendar", label: "Calendar", icon: Calendar },
];

export default function Markets() {
  const [overview, setOverview] = useState(null);
  const [gainers, setGainers] = useState([]);
  const [losers, setLosers] = useState([]);
  const [sectors, setSectors] = useState([]);
  const [globalMkts, setGlobalMkts] = useState([]);
  const [fiiDii, setFiiDii] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("overview");

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [ov, g, l, sec, gl, fd] = await Promise.all([
          api.get("/market/overview"), api.get("/market/gainers"), api.get("/market/losers"),
          api.get("/market/sectors"), api.get("/market/global"), api.get("/market/fii-dii"),
        ]);
        setOverview(ov.data); setGainers(g.data); setLosers(l.data);
        setSectors(sec.data); setGlobalMkts(gl.data); setFiiDii(fd.data);
      } catch (err) { console.error(err); }
      finally { setLoading(false); }
    };
    fetchData();
    const i = setInterval(fetchData, 30000);
    return () => clearInterval(i);
  }, []);

  if (loading) return (
    <div className="space-y-5 animate-fade-in-up">
      <div className="h-8 w-40 rounded-xl skeleton" />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[...Array(4)].map((_, i) => <div key={i} className="stat-card space-y-3"><div className="h-3 w-1/2 skeleton rounded" /><div className="h-6 w-2/3 skeleton rounded-lg" /></div>)}
      </div>
      <div className="glass-card p-5 h-48 skeleton" />
    </div>
  );

  return (
    <div data-testid="markets-page" className="space-y-5">
      {/* Header */}
      <Reveal>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="page-title">Markets</h1>
            <p className="page-subtitle mt-1">Real-time market overview and insights</p>
          </div>
          <div className="flex items-center gap-2">
            <MarketEngineStatus compact />
            {overview?.market_status && (
              <span className="badge-status" style={{
                background: overview.market_status === "OPEN" ? "var(--gain-bg)" : "var(--loss-bg)",
                color: overview.market_status === "OPEN" ? "var(--gain)" : "var(--loss)",
              }}>
                Market {overview.market_status === "OPEN" ? "Open" : "Closed"}
              </span>
            )}
            {overview?.source === "yahoo_finance" && (
              <span className="badge-status" style={{ background: "var(--gain-bg)", color: "var(--gain)" }}>NSE</span>
            )}
          </div>
        </div>
      </Reveal>

      {/* Index Strip */}
      <IndexStrip overview={overview} />

      {/* Tab Navigation */}
      <Reveal>
        <div className="flex gap-1 p-1 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] overflow-x-auto">
          {MARKET_TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium transition-all whitespace-nowrap ${
                  activeTab === tab.key
                    ? "bg-[var(--accent)] text-white shadow-sm"
                    : "text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
                }`}
              >
                <Icon size={13} />
                {tab.label}
              </button>
            );
          })}
        </div>
      </Reveal>

      {/* Tab Content */}
      {activeTab === "overview" && (
        <>
          {/* Heatmap */}
          <Reveal><MarketHeatmap gainers={gainers} losers={losers} /></Reveal>

          {/* Two Column: Gainers + Losers */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Reveal delay={0}><TopMovers title="Top Gainers" data={gainers} icon={TrendingUp} color="gain" /></Reveal>
            <Reveal delay={0.06}><TopMovers title="Top Losers" data={losers} icon={TrendingDown} color="loss" /></Reveal>
          </div>

          {/* Two Column: Breadth + Sectors */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Reveal delay={0}><MarketBreadth overview={overview} /></Reveal>
            <Reveal delay={0.06}>
              <div className="glass-card p-5">
                <h3 className="eyebrow mb-4">Sector Performance</h3>
                <div className="space-y-2">
                  {sectors?.map(s => {
                    const isPos = s.change_pct >= 0;
                    const barWidth = Math.min(Math.abs(s.change_pct || 0) * 15, 100);
                    return (
                      <div key={s.sector} className="flex items-center gap-3">
                        <span className="text-[11px] font-medium w-20 shrink-0 truncate" style={{ color: "var(--text-secondary)" }}>{s.sector}</span>
                        <div className="flex-1 h-5 rounded-md overflow-hidden relative" style={{ background: "var(--hover)" }}>
                          <div className="h-full rounded-md transition-all duration-500" style={{
                            width: `${barWidth}%`,
                            background: isPos ? "var(--gain)" : "var(--loss)",
                            opacity: 0.7,
                          }} />
                        </div>
                        <span className="text-[11px] font-mono font-semibold min-w-[50px] text-right" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
                          {isPos ? "+" : ""}{s.change_pct?.toFixed(2)}%
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </Reveal>
          </div>

          {/* FII/DII */}
          {fiiDii && (
            <Reveal>
              <div className="glass-card p-5">
                <h3 className="eyebrow mb-3">FII / DII Activity (₹ Cr)</h3>
                <div className="grid grid-cols-2 gap-6">
                  {[{ label: "FII Net", val: fiiDii.fii?.net }, { label: "DII Net", val: fiiDii.dii?.net }].map(({ label, val }) => (
                    <div key={label}>
                      <span className="stat-label">{label}</span>
                      <div className="text-xl font-mono font-bold" style={{ color: (val ?? 0) >= 0 ? "var(--gain)" : "var(--loss)" }}>
                        {val != null ? `${val >= 0 ? "+" : ""}${formatNumber(val)}` : "unavailable"}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </Reveal>
          )}

          {/* Global Markets */}
          <Reveal><GlobalMarkets markets={globalMkts} /></Reveal>
        </>
      )}

      {activeTab === "scanner" && (
        <Reveal>
          <MarketScanner />
        </Reveal>
      )}

      {activeTab === "rankings" && (
        <Reveal>
          <RankingTable />
        </Reveal>
      )}

      {activeTab === "sectors" && (
        <Reveal>
          <SectorAnalysis />
        </Reveal>
      )}

      {activeTab === "calendar" && (
        <Reveal>
          <EconomicCalendar />
        </Reveal>
      )}
    </div>
  );
}

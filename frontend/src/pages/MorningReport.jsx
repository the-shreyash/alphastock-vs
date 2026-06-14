import { useState, useEffect, useCallback } from "react";
import { useNavigate, Link } from "react-router-dom";
import api from "../services/api";
import { formatCurrency } from "../utils/formatters";
import {
  Sun, RefreshCw, TrendingUp, TrendingDown, Minus,
  AlertTriangle, Globe, BarChart3, ArrowUpRight, ArrowDownRight,
} from "lucide-react";

const MOOD_CONFIG = {
  Bullish: { color: "#10b981", bg: "rgba(16,185,129,0.12)", border: "rgba(16,185,129,0.3)", icon: TrendingUp },
  Cautious: { color: "#f59e0b", bg: "rgba(245,158,11,0.12)", border: "rgba(245,158,11,0.3)", icon: TrendingUp },
  Neutral: { color: "var(--text-muted)", bg: "rgba(120,120,140,0.1)", border: "rgba(120,120,140,0.2)", icon: Minus },
  Bearish: { color: "#f43f5e", bg: "rgba(244,63,94,0.12)", border: "rgba(244,63,94,0.3)", icon: TrendingDown },
};

function IndexCard({ label, value, change_pct }) {
  const isPos = change_pct >= 0;
  return (
    <div className="card-premium p-5 flex flex-col gap-1">
      <span className="text-[10px] font-bold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>{label}</span>
      <span className="text-2xl font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
        {value?.toLocaleString("en-IN")}
      </span>
      <span className="text-sm font-mono font-medium flex items-center gap-1" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
        {isPos ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
        {isPos ? "+" : ""}{change_pct?.toFixed(2)}%
      </span>
    </div>
  );
}

function PickCard({ pick }) {
  const isPos = (pick.change_pct || 0) >= 0;
  return (
    <div className="p-4 rounded-xl border transition-all hover:scale-[1.01]"
      style={{ background: "var(--bg-surface)", borderColor: "var(--border)" }}>
      <div className="flex items-center justify-between mb-2">
        <div>
          <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{pick.name}</p>
          <p className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>{pick.symbol}</p>
        </div>
        <span className="text-xs font-bold px-2 py-0.5 rounded-full"
          style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
          {pick.confidence}% conf
        </span>
      </div>
      <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{pick.reason}</p>
      <div className="flex items-center justify-between mt-2">
        <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
          SL: ₹{pick.stop_loss} | T: ₹{pick.target}
        </span>
        <span className="text-xs font-mono font-semibold" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
          {isPos ? "+" : ""}{pick.change_pct?.toFixed(2)}%
        </span>
      </div>
    </div>
  );
}

export default function MorningReport() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async (force = false) => {
    if (force) setRefreshing(true);
    else setLoading(true);
    try {
      const { data } = await api.get("/analysis/reports/morning");
      setReport(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return (
    <div className="space-y-4">
      <div className="h-20 rounded-2xl animate-pulse" style={{ background: "var(--bg-surface)" }} />
      <div className="grid grid-cols-3 gap-3">
        {[1, 2, 3].map(i => <div key={i} className="h-24 rounded-2xl animate-pulse" style={{ background: "var(--bg-surface)" }} />)}
      </div>
      <div className="h-32 rounded-2xl animate-pulse" style={{ background: "var(--bg-surface)" }} />
    </div>
  );

  if (!report) return (
    <div className="text-center py-20" style={{ color: "var(--text-muted)" }}>
      <Sun size={40} className="mx-auto mb-3 opacity-40" />
      <p>Unable to load morning report. Please try refreshing.</p>
      <button onClick={() => load(true)} className="mt-4 px-4 py-2 rounded-xl text-sm"
        style={{ background: "var(--ai-accent)", color: "#fff" }}>Retry</button>
    </div>
  );

  const mood = MOOD_CONFIG[report.market_mood] || MOOD_CONFIG.Neutral;
  const MoodIcon = mood.icon;

  return (
    <div className="space-y-5 max-w-4xl" data-testid="morning-report-page">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Sun size={20} style={{ color: "#f59e0b" }} />
            <h1 className="text-xl font-semibold" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>
              Morning Briefing
            </h1>
          </div>
          <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>
            {new Date(report.generated_at || Date.now()).toLocaleString("en-IN", {
              weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit",
            })}
          </p>
        </div>
        <button onClick={() => load(true)} disabled={refreshing}
          className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-medium transition-all"
          style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
          <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} />
          {refreshing ? "Refreshing..." : "Refresh Report"}
        </button>
      </div>

      {/* Market Mood Banner */}
      <div className="rounded-2xl p-5 flex items-center justify-between"
        style={{ background: mood.bg, border: `1px solid ${mood.border}` }}>
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-xl flex items-center justify-center"
            style={{ background: mood.bg, border: `1px solid ${mood.border}` }}>
            <MoodIcon size={24} style={{ color: mood.color }} />
          </div>
          <div>
            <p className="text-xs font-bold uppercase tracking-widest mb-0.5" style={{ color: mood.color }}>
              Market Mood
            </p>
            <p className="text-2xl font-semibold" style={{ fontFamily: "Outfit", color: mood.color }}>
              {report.market_mood}
            </p>
          </div>
        </div>
        {/* Mood gauge */}
        <div className="hidden sm:flex flex-col items-end gap-1">
          <span className="text-[10px] uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>Mood Score</span>
          <span className="text-3xl font-mono font-semibold" style={{ color: mood.color }}>
            {report.mood_score >= 0 ? "+" : ""}{(report.mood_score * 100).toFixed(0)}
          </span>
        </div>
      </div>

      {/* Index Cards */}
      <div className="grid grid-cols-3 gap-3">
        <IndexCard label="Nifty 50" value={report.nifty?.value} change_pct={report.nifty?.change_pct} />
        <IndexCard label="Bank Nifty" value={report.banknifty?.value} change_pct={report.banknifty?.change_pct} />
        <IndexCard label="Sensex" value={report.sensex?.value} change_pct={report.sensex?.change_pct} />
      </div>

      {/* AI Briefing */}
      <div className="card-premium p-5">
        <h3 className="text-xs font-bold uppercase tracking-widest mb-3 flex items-center gap-2" style={{ color: "var(--ai-accent)" }}>
          <BarChart3 size={12} /> AI Market Briefing
        </h3>
        <blockquote className="text-sm leading-relaxed border-l-2 pl-4"
          style={{ color: "var(--text-secondary)", borderColor: "var(--ai-accent)" }}>
          {report.ai_briefing}
        </blockquote>
      </div>

      {/* Top Picks */}
      <div>
        <h3 className="text-xs font-bold uppercase tracking-widest mb-3 flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
          <TrendingUp size={12} /> Today's Top Picks
        </h3>
        {report.top_picks?.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {report.top_picks.map((p, i) => <PickCard key={i} pick={p} />)}
          </div>
        ) : (
          <div className="card-premium p-5 text-center text-sm" style={{ color: "var(--text-muted)" }}>
            Picks generating — check back in a moment.
          </div>
        )}
      </div>

      {/* Risk + Global + FII in a 2-column grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* Key Risks */}
        <div className="rounded-2xl p-5" style={{ background: "rgba(244,63,94,0.07)", border: "1px solid rgba(244,63,94,0.2)" }}>
          <h3 className="text-xs font-bold uppercase tracking-widest mb-3 flex items-center gap-2" style={{ color: "var(--loss)" }}>
            <AlertTriangle size={12} /> Key Risks Today
          </h3>
          <ul className="space-y-2">
            {report.key_risks?.map((r, i) => (
              <li key={i} className="flex items-start gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
                <span className="mt-0.5 shrink-0" style={{ color: "var(--loss)" }}>▸</span> {r}
              </li>
            ))}
          </ul>
        </div>

        {/* Global Cues + FII/DII */}
        <div className="space-y-3">
          <div className="rounded-2xl p-5" style={{ background: "rgba(99,102,241,0.08)", border: "1px solid rgba(99,102,241,0.2)" }}>
            <h3 className="text-xs font-bold uppercase tracking-widest mb-2 flex items-center gap-2" style={{ color: "#818cf8" }}>
              <Globe size={12} /> Global Cues
            </h3>
            <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{report.global_cues}</p>
          </div>

          <div className="card-premium p-4">
            <h3 className="text-xs font-bold uppercase tracking-widest mb-3" style={{ color: "var(--text-muted)" }}>FII / DII Flow</h3>
            <div className="grid grid-cols-2 gap-3">
              {[
                { label: "FII Net", value: report.fii_dii?.fii_net, color: (report.fii_dii?.fii_net || 0) >= 0 ? "var(--gain)" : "var(--loss)" },
                { label: "DII Net", value: report.fii_dii?.dii_net, color: (report.fii_dii?.dii_net || 0) >= 0 ? "var(--gain)" : "var(--loss)" },
              ].map(f => (
                <div key={f.label} className="text-center">
                  <p className="text-[10px] uppercase tracking-widest mb-1" style={{ color: "var(--text-muted)" }}>{f.label}</p>
                  <p className="text-sm font-mono font-semibold" style={{ color: f.color }}>
                    {(f.value || 0) >= 0 ? "+" : ""}₹{Math.abs(f.value || 0).toLocaleString("en-IN")} Cr
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Footer link */}
      <div className="text-center pt-2">
        <Link to="/picks" className="text-xs font-medium hover:underline" style={{ color: "var(--ai-accent)" }}>
          View full AI stock picks →
        </Link>
      </div>
    </div>
  );
}

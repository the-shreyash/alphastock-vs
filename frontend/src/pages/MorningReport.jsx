import { useState, useEffect, useCallback } from "react";
import { useNavigate, Link } from "react-router-dom";
import { motion } from "framer-motion";
import api from "../services/api";
import { formatCurrency } from "../utils/formatters";
import { useRealtimeStore } from "../store/realtimeStore";
import AIPipelineProgress from "../components/ai/AIPipelineProgress";
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
    <div className="stat-card flex flex-col gap-1.5">
      <span className="stat-label">{label}</span>
      <span className="stat-value">
        {value?.toLocaleString("en-IN")}
      </span>
      <span className="text-[13px] font-mono font-medium flex items-center gap-1" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
        {isPos ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
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
          <p className="text-[15px] font-semibold font-display" style={{ color: "var(--text-primary)" }}>{pick.name}</p>
          <p className="text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>{pick.symbol}</p>
        </div>
        <span className="text-xs font-bold px-2 py-0.5 rounded-full"
          style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
          {pick.confidence}% conf
        </span>
      </div>
      <p className="text-[13px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>{pick.reason}</p>
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
  // Correlation id for the request in flight — matches the live AI pipeline
  // events (ai.run.* / ai.step over WebSocket) to this fetch (Sprint R7).
  const [activeRunId, setActiveRunId] = useState(null);
  const navigate = useNavigate();

  const load = useCallback(async (force = false) => {
    const runId = (crypto?.randomUUID?.() || `run-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    setActiveRunId(runId);
    if (force) setRefreshing(true);
    else setLoading(true);
    try {
      const { data } = await api.get("/analysis/reports/morning", { params: { run_id: runId } });
      setReport(data);
    } catch (e) {
      // Settle the live pipeline as warning so no step stays "running".
      useRealtimeStore.getState().resolveAIRun(runId, "warning");
      console.error(e);
    } finally {
      // REST settled — reconcile lost WS frames, then drop the finished run.
      const store = useRealtimeStore.getState();
      store.resolveAIRun(runId);
      store.clearAIRun(runId);
      setActiveRunId(null);
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const loadingSkeleton = (
    <div className="space-y-4">
      <div className="h-20 rounded-2xl animate-pulse" style={{ background: "var(--bg-surface)" }} />
      <div className="grid grid-cols-3 gap-3">
        {[1, 2, 3].map(i => <div key={i} className="h-24 rounded-2xl animate-pulse" style={{ background: "var(--bg-surface)" }} />)}
      </div>
      <div className="h-32 rounded-2xl animate-pulse" style={{ background: "var(--bg-surface)" }} />
    </div>
  );

  // While generating: the live AI pipeline (Collecting Market Data → Reading
  // News → …) once ai.run.started arrives; the skeletons before that or on a
  // cache hit, which never starts a run.
  if (loading) return (
    <AIPipelineProgress
      runId={activeRunId}
      title="Generating your morning briefing"
      fallback={loadingSkeleton}
    />
  );

  if (!report || report.available === false) return (
    <div className="text-center py-20" style={{ color: "var(--text-muted)" }}>
      <Sun size={40} className="mx-auto mb-3 opacity-40" />
      <p className="body-text mb-5">
        {report?.note || "Unable to load morning report. Please try refreshing."}
      </p>
      <button onClick={() => load(true)} className="btn-primary">Retry</button>
    </div>
  );

  const mood = MOOD_CONFIG[report.market_mood] || MOOD_CONFIG.Neutral;
  const MoodIcon = mood.icon;

  return (
    <div className="space-y-6 max-w-4xl" data-testid="morning-report-page">
      {/* Header */}
      <motion.div className="flex items-start justify-between flex-wrap gap-3"
        initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Sun size={22} style={{ color: "#f59e0b" }} />
            <h1 className="page-title">
              Morning Briefing
            </h1>
          </div>
          <p className="caption font-mono">
            {new Date(report.generated_at || Date.now()).toLocaleString("en-IN", {
              weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit",
            })}
          </p>
        </div>
        <button onClick={() => load(true)} disabled={refreshing} className="btn-secondary btn-sm">
          <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} />
          {refreshing ? "Refreshing..." : "Refresh Report"}
        </button>
      </motion.div>

      {/* Market Mood Banner */}
      <motion.div className="rounded-2xl p-5 flex items-center justify-between"
        style={{ background: mood.bg, border: `1px solid ${mood.border}` }}
        initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.4 }}>
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-xl flex items-center justify-center"
            style={{ background: mood.bg, border: `1px solid ${mood.border}` }}>
            <MoodIcon size={24} style={{ color: mood.color }} />
          </div>
          <div>
            <p className="eyebrow mb-0.5" style={{ color: mood.color }}>
              Market Mood
            </p>
            <p className="card-title" style={{ color: mood.color }}>
              {report.market_mood}
            </p>
          </div>
        </div>
        {/* Mood gauge */}
        <div className="hidden sm:flex flex-col items-end gap-1">
          <span className="stat-label">Mood Score</span>
          <span className="stat-value" style={{ color: mood.color }}>
            {report.mood_score >= 0 ? "+" : ""}{(report.mood_score * 100).toFixed(0)}
          </span>
        </div>
      </motion.div>

      {/* Index Cards */}
      <div className="grid grid-cols-3 gap-3">
        <IndexCard label="Nifty 50" value={report.nifty?.value} change_pct={report.nifty?.change_pct} />
        <IndexCard label="Bank Nifty" value={report.banknifty?.value} change_pct={report.banknifty?.change_pct} />
        <IndexCard label="Sensex" value={report.sensex?.value} change_pct={report.sensex?.change_pct} />
      </div>

      {/* AI Briefing */}
      <motion.div className="glass-card p-5"
        initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.4 }}>
        <h3 className="eyebrow mb-3 flex items-center gap-2" style={{ color: "var(--ai-accent)" }}>
          <BarChart3 size={13} /> AI Market Briefing
        </h3>
        <blockquote className="body-text border-l-2 pl-4"
          style={{ borderColor: "var(--ai-accent)" }}>
          {report.ai_briefing}
        </blockquote>
      </motion.div>

      {/* Top Picks */}
      <motion.div initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.4 }}>
        <h3 className="eyebrow mb-3 flex items-center gap-2">
          <TrendingUp size={13} /> Today's Top Picks
        </h3>
        {report.top_picks?.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {report.top_picks.map((p, i) => <PickCard key={i} pick={p} />)}
          </div>
        ) : (
          <div className="glass-card p-5 text-center text-sm" style={{ color: "var(--text-muted)" }}>
            Picks generating — check back in a moment.
          </div>
        )}
      </motion.div>

      {/* Risk + Global + FII in a 2-column grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* Key Risks */}
        <motion.div className="rounded-2xl p-5" style={{ background: "rgba(244,63,94,0.07)", border: "1px solid rgba(244,63,94,0.2)" }}
          initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.4 }}>
          <h3 className="eyebrow mb-3 flex items-center gap-2" style={{ color: "var(--loss)" }}>
            <AlertTriangle size={13} /> Key Risks Today
          </h3>
          <ul className="space-y-2">
            {report.key_risks?.map((r, i) => (
              <li key={i} className="flex items-start gap-2 text-[13px]" style={{ color: "var(--text-secondary)" }}>
                <span className="mt-0.5 shrink-0" style={{ color: "var(--loss)" }}>▸</span> {r}
              </li>
            ))}
          </ul>
        </motion.div>

        {/* Global Cues + FII/DII */}
        <div className="space-y-3">
          <motion.div className="rounded-2xl p-5" style={{ background: "rgba(99,102,241,0.08)", border: "1px solid rgba(99,102,241,0.2)" }}
            initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.4, delay: 0.05 }}>
            <h3 className="eyebrow mb-2 flex items-center gap-2" style={{ color: "#818cf8" }}>
              <Globe size={13} /> Global Cues
            </h3>
            <p className="body-text">{report.global_cues}</p>
          </motion.div>

          <motion.div className="glass-card p-4"
            initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.4, delay: 0.1 }}>
            <h3 className="eyebrow mb-3">FII / DII Flow</h3>
            <div className="grid grid-cols-2 gap-3">
              {[
                { label: "FII Net", value: report.fii_dii?.fii_net, color: (report.fii_dii?.fii_net || 0) >= 0 ? "var(--gain)" : "var(--loss)" },
                { label: "DII Net", value: report.fii_dii?.dii_net, color: (report.fii_dii?.dii_net || 0) >= 0 ? "var(--gain)" : "var(--loss)" },
              ].map(f => (
                <div key={f.label} className="text-center">
                  <p className="stat-label mb-1">{f.label}</p>
                  <p className="text-sm font-mono font-semibold" style={{ color: f.color }}>
                    {(f.value || 0) >= 0 ? "+" : ""}₹{Math.abs(f.value || 0).toLocaleString("en-IN")} Cr
                  </p>
                </div>
              ))}
            </div>
          </motion.div>
        </div>
      </div>

      {/* Footer link */}
      <div className="text-center pt-2">
        <Link to="/picks" className="text-sm font-medium hover:underline" style={{ color: "var(--ai-accent)" }}>
          View full AI stock picks →
        </Link>
      </div>
    </div>
  );
}

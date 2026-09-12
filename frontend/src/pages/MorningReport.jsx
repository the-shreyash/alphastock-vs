import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import api from "../services/api";
import { useRealtimeStore } from "../store/realtimeStore";
import AIPipelineProgress from "../components/ai/AIPipelineProgress";
import GiftNiftyCard from "../components/morning/GiftNiftyCard";
import GlobalMarketsCard from "../components/morning/GlobalMarketsCard";
import NewsHeadlines from "../components/morning/NewsHeadlines";
import EconomicCalendarCard from "../components/morning/EconomicCalendarCard";
import PortfolioAlertsCard from "../components/morning/PortfolioAlertsCard";
import ReportProvenance, {
  ReportUnavailable, BriefingAttribution,
} from "../components/morning/ReportProvenance";
import { deriveReportStatus, formatInstant } from "../lib/reportProvenance";
import {
  Sun, RefreshCw, TrendingUp, TrendingDown, Minus,
  AlertTriangle, BarChart3, ArrowUpRight, ArrowDownRight,
} from "lucide-react";

const MOOD_CONFIG = {
  Bullish: { color: "#10b981", bg: "rgba(16,185,129,0.12)", border: "rgba(16,185,129,0.3)", icon: TrendingUp },
  Cautious: { color: "#f59e0b", bg: "rgba(245,158,11,0.12)", border: "rgba(245,158,11,0.3)", icon: TrendingUp },
  Neutral: { color: "var(--text-muted)", bg: "rgba(120,120,140,0.1)", border: "rgba(120,120,140,0.2)", icon: Minus },
  Bearish: { color: "#f43f5e", bg: "rgba(244,63,94,0.12)", border: "rgba(244,63,94,0.3)", icon: TrendingDown },
};

function IndexCard({ label, value, change_pct }) {
  const isPos = change_pct >= 0;
  const available = value !== null && value !== undefined;
  return (
    <div className="stat-card flex flex-col gap-1.5">
      <span className="stat-label">{label}</span>
      {available ? (
        <>
          <span className="stat-value">{value.toLocaleString("en-IN")}</span>
          <span className="text-[13px] font-mono font-medium flex items-center gap-1" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
            {isPos ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
            {isPos ? "+" : ""}{change_pct?.toFixed(2)}%
          </span>
        </>
      ) : (
        <span className="text-[13px] font-mono" style={{ color: "var(--text-muted)" }}>unavailable</span>
      )}
    </div>
  );
}

/**
 * FII / DII net institutional flow.
 *
 * NSE publishes these only after market close, so an absent value is routine
 * and is labelled as such — rendering it as ₹0 Cr would read as "institutions
 * were flat", which is a materially different claim from "not published yet".
 */
function FiiDiiCard({ fiiDii }) {
  const flows = [
    { label: "FII Net", value: fiiDii?.fii_net },
    { label: "DII Net", value: fiiDii?.dii_net },
  ];
  return (
    <motion.div className="glass-card p-4"
      initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.4, delay: 0.05 }}>
      <h3 className="eyebrow mb-3">FII / DII Flow</h3>
      <div className="grid grid-cols-2 gap-3">
        {flows.map(f => {
          const available = f.value !== null && f.value !== undefined;
          const isPos = available && f.value >= 0;
          return (
            <div key={f.label} className="text-center">
              <p className="stat-label mb-1">{f.label}</p>
              {available ? (
                <p className="text-sm font-mono font-semibold" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
                  {isPos ? "+" : "−"}₹{Math.abs(f.value).toLocaleString("en-IN")} Cr
                </p>
              ) : (
                <p className="text-[12px] font-mono" style={{ color: "var(--text-muted)" }}>not published</p>
              )}
            </div>
          );
        })}
      </div>
    </motion.div>
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
        {/* D6.9 — `confidence` is the technical scan's own score (RSI, volume,
            MACD, patterns), not a model's judgement. It kept the AI accent
            colour and the bare label "conf", which on a page headed "AI" read
            as an AI confidence. Neutral colour, and the tooltip names what it
            actually measures. */}
        <span className="text-xs font-bold px-2 py-0.5 rounded-full"
          title="Technical score from RSI, volume, MACD and detected patterns"
          style={{ background: "var(--bg-elevated, rgba(120,120,140,0.12))", color: "var(--text-secondary)" }}>
          {pick.confidence}% signal
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
  // D6.9 — CURRENT AI service health, fetched independently of the report.
  // It is a second axis and never evidence about the report's history: an
  // offline provider does not make the 08:30 briefing fake, and an online one
  // does not prove today's briefing was generated.
  const [aiStatus, setAiStatus] = useState(null);
  // Ticks once a minute so the rendered age advances between refetches. It
  // re-derives freshness from the report already in hand; it fetches nothing,
  // so this is not polling and does not touch the event-driven refresh below.
  const [now, setNow] = useState(() => Date.now());
  // Correlation id for the request in flight — matches the live AI pipeline
  // events (ai.run.* / ai.step over WebSocket) to this fetch (Sprint R7).
  const [activeRunId, setActiveRunId] = useState(null);

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

  useEffect(() => {
    let cancelled = false;
    api.get("/ai/status")
      .then((r) => !cancelled && setAiStatus(r.data))
      // A failed status request is not proof of an outage — it is the absence
      // of an answer. `online: null` renders no claim either way.
      .catch(() => !cancelled && setAiStatus(null));
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(id);
  }, []);

  // Sprint R8: the 8:30 pipeline broadcasts morningreport.generated when the
  // fresh report lands — refetch in place (refresh spinner, not a skeleton
  // wipe) so an open page shows the new briefing without a reload.
  const reportReadyAt = useRealtimeStore((s) => s.morningReportReadyAt);
  useEffect(() => {
    if (reportReadyAt) load(true);
  }, [reportReadyAt, load]);

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

  // D6.9 — ONE derivation, shared by every surface below. The page no longer
  // infers "an AI wrote this" from the presence of report data.
  const status = deriveReportStatus(report, { generating: refreshing, aiStatus, now });

  if (!status.hasReport) return (
    <ReportUnavailable status={status} note={report?.note} onRetry={() => load(true)} />
  );

  const mood = MOOD_CONFIG[report.market_mood] || MOOD_CONFIG.Neutral;
  const MoodIcon = mood.icon;
  // Ties the picks to the generation that produced them, so a stale report's
  // picks are visibly as old as the report rather than reading as live.
  const generatedInstant = formatInstant(status.completedAt);
  const pickTimingNote = generatedInstant ? ` · selected ${generatedInstant}` : "";

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
          {/* The report's own date — the trading day it covers. It is NOT a
              generation time and is no longer presented as one.

              D6.9 — this read `new Date(report.generated_at || Date.now())`.
              On a legacy document with no `generated_at`, the `|| Date.now()`
              rendered the page-load time as the generation time: a report of
              unknown age presented as generated seconds ago. The real
              generation instant, or the honest absence of it, is in
              <ReportProvenance /> below. */}
          <p className="caption font-mono" data-testid="report-date">{report.date}</p>
        </div>
        <button onClick={() => load(true)} disabled={refreshing} className="btn-secondary btn-sm">
          <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} />
          {refreshing ? "Refreshing..." : "Refresh Report"}
        </button>
      </motion.div>

      {/* When it was generated, how old it is now, whether a model wrote it,
          and whether AI is up right now — four separate facts, stated as four
          separate facts. */}
      <ReportProvenance status={status} />

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

      {/* Market Briefing.

          D6.9 — the heading was unconditionally "AI Market Briefing". It is the
          truth only when a model actually answered; the same block otherwise
          holds the grounded restatement of the collected numbers, and on a
          deployment whose key had no credit it held the provider's own
          "AI services are currently offline" text. The heading and the accent
          now follow `status.isAIGenerated`, which comes from the backend's
          record of what ran — never from the presence of a string here. */}
      <motion.div className="glass-card p-5"
        initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.4 }}
        data-testid="market-briefing">
        <h3 className="eyebrow mb-1 flex items-center gap-2"
          style={{ color: status.isAIGenerated ? "var(--ai-accent)" : "var(--text-secondary)" }}
          data-testid="briefing-heading">
          <BarChart3 size={13} /> {status.isAIGenerated ? "AI Market Briefing" : "Market Briefing"}
        </h3>
        <div className="mb-3"><BriefingAttribution status={status} /></div>
        <blockquote className="body-text border-l-2 pl-4"
          style={{ borderColor: status.isAIGenerated ? "var(--ai-accent)" : "var(--border)" }}>
          {report.ai_briefing}
        </blockquote>
      </motion.div>

      {/* Top Picks.

          D6.9 — these are a deterministic RSI / volume / MACD / pattern scan
          and have never involved a model, but they sat under an AI-framed page
          beside an AI-accented "% conf" badge and above a "View full AI stock
          picks" link. They are labelled for what they are, and tied to the
          generation that produced them — a pick's age is this report's
          `completed_at`, not now. */}
      <motion.div initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.4 }}>
        <h3 className="eyebrow mb-1 flex items-center gap-2">
          <TrendingUp size={13} /> Today's Top Picks
        </h3>
        <p className="text-[11px] font-mono mb-3" style={{ color: "var(--text-muted)" }}
          data-testid="picks-attribution">
          {status.picksAreDeterministic
            ? "Deterministic technical scan — not AI-generated"
            : "Source not recorded"}
          {pickTimingNote}
        </p>
        {report.top_picks?.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {report.top_picks.map((p, i) => <PickCard key={i} pick={p} />)}
          </div>
        ) : (
          // "Picks generating — check back in a moment" was false: generation
          // had finished, and the scan had returned nothing. A completed run
          // with an empty result is stated as one.
          <div className="glass-card p-5 text-center text-sm" style={{ color: "var(--text-muted)" }}
            data-testid="picks-empty">
            No setups met the scan's criteria for this report.
          </div>
        )}
      </motion.div>

      {/* Your holdings, read against everything above — the report's payoff. */}
      <PortfolioAlertsCard portfolio={report.portfolio} />

      {/* Overnight: where the world closed, and where Nifty is likely to open. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <GlobalMarketsCard globalMarkets={report.global_markets} />
        <div className="space-y-4">
          <GiftNiftyCard giftNifty={report.gift_nifty} />
          <FiiDiiCard fiiDii={report.fii_dii} />
        </div>
      </div>

      {/* What happened, and what is scheduled to happen. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <NewsHeadlines news={report.news} sentiment={report.news_sentiment} />
        <EconomicCalendarCard calendar={report.economic_calendar} />
      </div>

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

      {/* Footer link */}
      <div className="text-center pt-2">
        <Link to="/picks" className="text-sm font-medium hover:underline" style={{ color: "var(--ai-accent)" }}>
          View full stock picks →
        </Link>
      </div>
    </div>
  );
}

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import api from "../services/api";
import { formatCurrency, formatPercent } from "../utils/formatters";
import {
  BookOpen, TrendingUp, TrendingDown, Award, Brain, RefreshCw,
  GraduationCap, X, CheckCircle, XCircle, ArrowRight, Loader2,
} from "lucide-react";

const GRADE_CONFIG = {
  A: { color: "var(--gain)", bg: "rgba(16,185,129,0.15)", border: "rgba(16,185,129,0.3)", label: "Excellent" },
  B: { color: "#10b981", bg: "rgba(16,185,129,0.1)", border: "rgba(16,185,129,0.2)", label: "Good" },
  C: { color: "#f59e0b", bg: "rgba(245,158,11,0.12)", border: "rgba(245,158,11,0.25)", label: "Average" },
  D: { color: "var(--loss)", bg: "rgba(244,63,94,0.12)", border: "rgba(244,63,94,0.25)", label: "Needs Work" },
};

function CoachingDrawer({ trade, onClose }) {
  const [coaching, setCoaching] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get(`/trades/${trade.id}/coaching`)
      .then(r => setCoaching(r.data))
      .catch(() => setCoaching({ error: true }))
      .finally(() => setLoading(false));
  }, [trade.id]);

  const grade = coaching?.grade || "C";
  const cfg = GRADE_CONFIG[grade] || GRADE_CONFIG.C;

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.7)" }}
      onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-lg rounded-2xl p-6 shadow-2xl space-y-4 max-h-[90vh] overflow-y-auto"
        style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <GraduationCap size={20} style={{ color: "var(--ai-accent)" }} />
            <span className="card-title">AI Trade Coach</span>
          </div>
          <button onClick={onClose} style={{ color: "var(--text-muted)" }}><X size={16} /></button>
        </div>

        {/* Trade summary */}
        <div className="flex items-center justify-between p-3 rounded-xl" style={{ background: "var(--bg)", border: "1px solid var(--border)" }}>
          <div>
            <p className="text-sm font-semibold font-mono" style={{ color: "var(--text-primary)" }}>{trade.symbol}</p>
            <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>{trade.type} · {trade.status}</p>
          </div>
          <div className="text-right">
            <p className="text-sm font-mono font-semibold" style={{ color: (trade.pnl || 0) >= 0 ? "var(--gain)" : "var(--loss)" }}>
              {(trade.pnl || 0) >= 0 ? "+" : ""}{formatCurrency(trade.pnl)}
            </p>
            <p className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
              {(trade.pnl_percent || 0) >= 0 ? "+" : ""}{(trade.pnl_percent || 0).toFixed(2)}%
            </p>
          </div>
        </div>

        {loading ? (
          <div className="flex flex-col items-center py-8 gap-3">
            <Loader2 size={28} className="animate-spin" style={{ color: "var(--ai-accent)" }} />
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>AI is analyzing your trade...</p>
            {[1, 2, 3].map(i => (
              <div key={i} className="w-full h-12 rounded-xl animate-pulse" style={{ background: "var(--bg)" }} />
            ))}
          </div>
        ) : coaching?.error ? (
          <p className="text-sm text-center py-8" style={{ color: "var(--text-muted)" }}>
            Coaching unavailable for this trade. Please try again.
          </p>
        ) : (
          <>
            {/* Grade badge */}
            <div className="flex items-center gap-4 p-4 rounded-2xl" style={{ background: cfg.bg, border: `1px solid ${cfg.border}` }}>
              <div className="w-14 h-14 rounded-xl flex items-center justify-center text-3xl font-bold"
                style={{ background: cfg.bg, border: `1px solid ${cfg.border}`, color: cfg.color }}>
                {grade}
              </div>
              <div>
                <p className="font-bold text-base" style={{ color: cfg.color }}>{cfg.label} Trade</p>
                <p className="text-xs" style={{ color: "var(--text-secondary)" }}>{coaching.grade_reason}</p>
              </div>
            </div>

            {/* Lesson title */}
            {coaching.lesson_title && (
              <div className="p-3 rounded-xl text-center" style={{ background: "var(--ai-accent-soft)" }}>
                <p className="text-xs uppercase tracking-widest mb-0.5" style={{ color: "var(--ai-accent)" }}>Today's Lesson</p>
                <p className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>{coaching.lesson_title}</p>
              </div>
            )}

            {/* Three sections */}
            <div className="space-y-3">
              <div className="p-3.5 rounded-xl" style={{ background: "rgba(16,185,129,0.08)", border: "1px solid rgba(16,185,129,0.2)" }}>
                <p className="text-[10px] font-bold uppercase tracking-widest mb-1.5 flex items-center gap-1" style={{ color: "var(--gain)" }}>
                  <CheckCircle size={10} /> What Went Right
                </p>
                <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{coaching.what_went_right}</p>
              </div>
              <div className="p-3.5 rounded-xl" style={{ background: "rgba(244,63,94,0.07)", border: "1px solid rgba(244,63,94,0.2)" }}>
                <p className="text-[10px] font-bold uppercase tracking-widest mb-1.5 flex items-center gap-1" style={{ color: "var(--loss)" }}>
                  <XCircle size={10} /> What Went Wrong
                </p>
                <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{coaching.what_went_wrong}</p>
              </div>
              <div className="p-3.5 rounded-xl" style={{ background: "var(--ai-accent-soft)", border: "1px solid rgba(99,102,241,0.25)" }}>
                <p className="text-[10px] font-bold uppercase tracking-widest mb-1.5 flex items-center gap-1" style={{ color: "var(--ai-accent)" }}>
                  <ArrowRight size={10} /> Next Time
                </p>
                <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{coaching.next_time}</p>
              </div>
            </div>

            {/* Full coaching text */}
            {coaching.coaching_text && (
              <div className="p-4 rounded-xl border-l-2" style={{ background: "var(--bg)", borderColor: "var(--ai-accent)" }}>
                <p className="text-[10px] uppercase tracking-widest mb-1.5" style={{ color: "var(--ai-accent)" }}>Coach's Summary</p>
                <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{coaching.coaching_text}</p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function TradeJournal() {
  const [stats, setStats] = useState(null);
  const [journal, setJournal] = useState([]);
  const [review, setReview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [period, setPeriod] = useState(7);
  const [coachingTrade, setCoachingTrade] = useState(null);

  useEffect(() => { fetchData(); }, [period]);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [s, j] = await Promise.all([
        api.get(`/journal/stats?days=${period}`),
        api.get(`/journal?days=${period}`),
      ]);
      setStats(s.data);
      setJournal(j.data);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  };

  const fetchReview = async () => {
    setReviewLoading(true);
    try {
      const { data } = await api.get("/journal/weekly-review");
      setReview(data.review);
    } catch (err) { console.error(err); setReview("Unable to generate review."); }
    finally { setReviewLoading(false); }
  };

  if (loading) return (
    <div className="space-y-5 animate-fade-in-up">
      <div className="h-8 w-40 rounded-xl skeleton" />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">{[...Array(4)].map((_, i) => <div key={i} className="stat-card space-y-2"><div className="h-3 w-1/2 skeleton rounded" /><div className="h-6 w-2/3 skeleton rounded" /></div>)}</div>
      <div className="glass-card p-5 h-40 skeleton" />
    </div>
  );

  const recent = stats?.recent || {};
  const allTime = stats?.all_time || {};

  return (
    <div data-testid="trade-journal-page" className="space-y-4">
      {coachingTrade && <CoachingDrawer trade={coachingTrade} onClose={() => setCoachingTrade(null)} />}

      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">Trade Journal</h1>
          <p className="page-subtitle mt-0.5">Performance tracking & AI coaching</p>
        </div>
        <div className="segment-control">
          {[7, 30, 90].map((p) => (
            <button key={p} data-testid={`period-${p}d`} onClick={() => setPeriod(p)}
              className={`segment-btn text-[11px] ${period === p ? "active" : ""}`}>
              {p}D
            </button>
          ))}
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: "Total P&L", value: formatCurrency(recent.total_pnl), color: (recent.total_pnl || 0) >= 0 ? "var(--gain)" : "var(--loss)" },
          { label: "Win Rate", value: `${recent.win_rate || 0}%`, color: (recent.win_rate || 0) >= 50 ? "var(--gain)" : "var(--loss)" },
          { label: "Trades", value: recent.total || 0, color: "var(--text-primary)" },
          { label: "Best Trade", value: formatCurrency(recent.best), color: "var(--gain)" },
        ].map((s, i) => (
          <motion.div
            key={s.label}
            className="stat-card"
            initial={{ opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.4, delay: i * 0.05 }}
          >
            <span className="stat-label block">{s.label}</span>
            <span className="stat-value" style={{ color: s.color }}>{s.value}</span>
          </motion.div>
        ))}
      </div>

      {/* All Time Stats */}
      <motion.div
        className="glass-card p-5"
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }}
        transition={{ duration: 0.4 }}
      >
        <h3 className="card-title mb-3 flex items-center gap-2">
          <Award size={16} /> All-Time Performance
        </h3>
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-4 text-center">
          {[
            { l: "Trades", v: allTime.total },
            { l: "Wins", v: allTime.wins },
            { l: "Losses", v: allTime.losses },
            { l: "Win Rate", v: `${allTime.win_rate}%` },
            { l: "Total P&L", v: formatCurrency(allTime.total_pnl) },
            { l: "Avg P&L", v: formatCurrency(allTime.avg_pnl) },
          ].map((s) => (
            <div key={s.l}>
              <div className="stat-label">{s.l}</div>
              <div className="text-sm font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{s.v}</div>
            </div>
          ))}
        </div>
      </motion.div>

      {/* AI Weekly Review */}
      <motion.div
        className="glass-card p-5"
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }}
        transition={{ duration: 0.4 }}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="card-title flex items-center gap-2" style={{ color: "var(--ai-accent)" }}>
            <Brain size={18} /> AI Performance Review
          </h3>
          <button data-testid="generate-review-btn" onClick={fetchReview} disabled={reviewLoading}
            className="btn-secondary btn-sm">
            {reviewLoading ? <RefreshCw size={14} className="animate-spin" /> : <Brain size={14} />}
            {reviewLoading ? "Analyzing..." : "Generate Review"}
          </button>
        </div>
        {review ? (
          <div className="body-text whitespace-pre-wrap">{review}</div>
        ) : (
          <p className="body-text" style={{ color: "var(--text-muted)" }}>Click "Generate Review" to get AI analysis of your trading performance.</p>
        )}
      </motion.div>

      {/* Trade History */}
      <motion.div
        className="glass-card"
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }}
        transition={{ duration: 0.4 }}
      >
        <div className="p-4 border-b" style={{ borderColor: "var(--border)" }}>
          <h3 className="card-title">Trade History ({journal.length})</h3>
        </div>
        {journal.length === 0 ? (
          <div className="p-12 text-center">
            <BookOpen size={32} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
            <p style={{ color: "var(--text-muted)" }}>No closed trades in this period</p>
          </div>
        ) : (
          <div className="divide-y" style={{ borderColor: "var(--border)" }}>
            {journal.map((t, i) => (
              <motion.div
                key={t.id}
                className="p-4 flex items-center justify-between transition-all hover:bg-[var(--hover)]"
                initial={{ opacity: 0, y: 16 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-40px" }}
                transition={{ duration: 0.35, delay: Math.min(i, 8) * 0.04 }}
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-xl flex items-center justify-center"
                    style={{ background: (t.pnl || 0) >= 0 ? "rgba(16,185,129,0.1)" : "rgba(244,63,94,0.1)" }}>
                    {(t.pnl || 0) >= 0 ? <TrendingUp size={14} style={{ color: "var(--gain)" }} /> : <TrendingDown size={14} style={{ color: "var(--loss)" }} />}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="card-subtitle font-semibold" style={{ color: "var(--text-primary)" }}>{t.symbol}</span>
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                        style={{
                          background: t.status === "TARGET_HIT" ? "rgba(16,185,129,0.1)" : t.status === "SL_HIT" ? "rgba(244,63,94,0.1)" : "var(--bg-surface)",
                          color: t.status === "TARGET_HIT" ? "var(--gain)" : t.status === "SL_HIT" ? "var(--loss)" : "var(--text-muted)"
                        }}>
                        {t.status}
                      </span>
                    </div>
                    <div className="text-[10px] font-mono mt-0.5" style={{ color: "var(--text-muted)" }}>
                      {t.entry_price && `Entry: ₹${t.entry_price}`} {t.exit_price && `Exit: ₹${t.exit_price}`} | Qty: {t.quantity}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="text-right">
                    <div className="text-sm font-mono font-semibold" style={{ color: (t.pnl || 0) >= 0 ? "var(--gain)" : "var(--loss)" }}>
                      {(t.pnl || 0) >= 0 ? "+" : ""}{formatCurrency(t.pnl)}
                    </div>
                    <div className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>{formatPercent(t.pnl_percent)}</div>
                  </div>
                  {/* Coaching button */}
                  <button
                    onClick={() => setCoachingTrade(t)}
                    className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[10px] font-bold transition-all hover:scale-105"
                    style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}
                    title="View AI Coaching"
                  >
                    <GraduationCap size={11} />
                    Coach
                  </button>
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </motion.div>
    </div>
  );
}

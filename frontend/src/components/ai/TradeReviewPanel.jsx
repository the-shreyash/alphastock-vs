import { useEffect, useState } from "react";
import { ClipboardCheck, Cpu, TrendingUp, TrendingDown } from "lucide-react";
import api from "../../services/api";
import AIText from "./AIText";

/**
 * TradeReviewPanel — the Trading Coach. Lists the user's closed trades and, on
 * selection, generates (or loads a cached) AI review via POST /api/ai/trade-review.
 * Grounded in the real trade numbers only — no fabrication.
 */
export default function TradeReviewPanel() {
  const [trades, setTrades] = useState(null);
  const [selected, setSelected] = useState(null);
  const [review, setReview] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.get("/trades/history")
      .then((r) => setTrades(Array.isArray(r.data) ? r.data : []))
      .catch(() => setTrades([]));
  }, []);

  const runReview = async (trade) => {
    setSelected(trade._id);
    setReview(null);
    setLoading(true);
    try {
      const { data } = await api.post("/ai/trade-review", { trade_id: trade._id });
      setReview(data);
    } catch {
      setReview({ content: "Could not generate a review for this trade. Please try again." });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4" data-testid="trade-review-panel">
      {/* Trade list */}
      <div className="glass-card p-4" style={{ maxHeight: "calc(100vh - 320px)", overflowY: "auto" }}>
        <h3 className="eyebrow mb-3 flex items-center gap-2">
          <ClipboardCheck size={13} style={{ color: "var(--ai-accent)" }} /> Closed trades
        </h3>
        {trades === null ? (
          <div className="space-y-2">{[1, 2, 3].map((i) => <div key={i} className="h-14 rounded-xl skeleton" />)}</div>
        ) : trades.length === 0 ? (
          <p className="text-[11px] py-4 text-center" style={{ color: "var(--text-muted)" }}>
            No closed trades yet. Close a position to get an AI review.
          </p>
        ) : (
          <div className="space-y-2">
            {trades.map((t) => {
              const win = (t.pnl || 0) >= 0;
              const active = selected === t._id;
              return (
                <button key={t._id} onClick={() => runReview(t)} className="w-full text-left p-3 rounded-xl transition-all"
                  style={{ background: active ? "var(--ai-accent-soft)" : "var(--hover)", border: active ? "1px solid var(--ai-accent-glow)" : "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between">
                    <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{t.symbol}</span>
                    <span className="text-[11px] font-bold flex items-center gap-1" style={{ color: win ? "var(--gain)" : "var(--loss)" }}>
                      {win ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                      {t.pnl_percent != null ? `${t.pnl_percent}%` : "—"}
                    </span>
                  </div>
                  <div className="text-[10px] mt-0.5" style={{ color: "var(--text-muted)" }}>
                    ₹{t.entry_price} → ₹{t.exit_price ?? "—"}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Review */}
      <div className="lg:col-span-2 glass-card p-5" style={{ minHeight: 300 }}>
        {!selected ? (
          <div className="h-full flex flex-col items-center justify-center text-center py-10">
            <ClipboardCheck size={40} className="mb-3" style={{ color: "var(--ai-accent)", opacity: 0.5 }} />
            <p className="body-text max-w-xs">Select a closed trade to get an AI coaching review — mistakes, strengths and lessons.</p>
          </div>
        ) : loading ? (
          <div className="space-y-2">{[1, 2, 3, 4, 5, 6].map((i) => <div key={i} className="h-4 rounded skeleton" />)}</div>
        ) : (
          <>
            <div className="flex items-center gap-2 mb-3">
              <h4 className="card-title">Trade Review — {review?.symbol}</h4>
              {review?.model_used && (
                <span className="text-[10px] flex items-center gap-1 px-2 py-0.5 rounded-md" style={{ background: "var(--hover)", color: "var(--text-muted)" }}>
                  <Cpu size={10} /> {review.model_used}
                </span>
              )}
              {review?.cached && (
                <span className="text-[10px] px-2 py-0.5 rounded-md" style={{ background: "var(--hover)", color: "var(--text-muted)" }}>saved</span>
              )}
            </div>
            <AIText text={review?.content} />
          </>
        )}
      </div>
    </div>
  );
}

import { useState } from "react";
import { Briefcase, Cpu, ShieldCheck, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import api from "../../services/api";
import AIText from "./AIText";

/**
 * PortfolioReviewPanel — Portfolio AI. On demand, generates a narrative review
 * of the user's holdings via POST /api/ai/portfolio-review, grounded in live
 * holdings + the deterministic health scan. Empty portfolios get a clear CTA.
 */
export default function PortfolioReviewPanel() {
  const [state, setState] = useState({ loading: false, data: null, error: null });

  const run = async () => {
    setState({ loading: true, data: null, error: null });
    try {
      const { data } = await api.post("/ai/portfolio-review");
      setState({ loading: false, data, error: null });
    } catch {
      setState({ loading: false, data: null, error: "Could not generate a portfolio review. Please try again." });
    }
  };

  const { loading, data, error } = state;

  return (
    <div className="space-y-4" data-testid="portfolio-review-panel">
      <div className="glass-card p-5 flex items-start gap-3">
        <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0" style={{ background: "var(--ai-accent-soft)" }}>
          <Briefcase size={18} style={{ color: "var(--ai-accent)" }} />
        </div>
        <div className="flex-1">
          <h3 className="card-title mb-1">Portfolio AI</h3>
          <p className="body-text">Get an AI review of your allocation, diversification, risk and weak/strong holdings — grounded in your live positions.</p>
        </div>
        <button className="btn-primary px-4 self-center" onClick={run} disabled={loading} data-testid="portfolio-review-btn">
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> {data ? "Re-run" : "Review"}
        </button>
      </div>

      {loading && (
        <div className="glass-card p-5 space-y-2">{[1, 2, 3, 4, 5, 6].map((i) => <div key={i} className="h-4 rounded skeleton" />)}</div>
      )}

      {error && <div className="glass-card p-5"><p className="text-[13px]" style={{ color: "var(--loss)" }}>{error}</p></div>}

      {data?.empty && (
        <div className="glass-card p-8 text-center">
          <Briefcase size={40} className="mx-auto mb-3" style={{ color: "var(--ai-accent)", opacity: 0.5 }} />
          <p className="body-text mb-4">{data.content}</p>
          <Link to="/portfolio" className="btn-primary btn-lg">Go to Portfolio</Link>
        </div>
      )}

      {data && !data.empty && (
        <div className="glass-card p-5">
          <div className="flex flex-wrap items-center gap-2 mb-4">
            <h4 className="card-title">Portfolio Review</h4>
            {data.model_used && (
              <span className="text-[10px] flex items-center gap-1 px-2 py-0.5 rounded-md" style={{ background: "var(--hover)", color: "var(--text-muted)" }}>
                <Cpu size={10} /> {data.model_used}
              </span>
            )}
            {data.health?.health_score != null && (
              <span className="text-[11px] flex items-center gap-1 px-2.5 py-1 rounded-md font-medium"
                style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
                <ShieldCheck size={12} /> Health {data.health.health_score}/100
                {data.health.at_risk ? ` · ${data.health.at_risk} at risk` : ""}
              </span>
            )}
            <span className="text-[11px] px-2 py-1 rounded-md" style={{ background: "var(--hover)", color: "var(--text-muted)" }}>
              {data.holdings_count} holdings
            </span>
          </div>
          <AIText text={data.content} />
        </div>
      )}
    </div>
  );
}

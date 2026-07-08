import { motion } from "framer-motion";
import { Newspaper, ExternalLink } from "lucide-react";
import useStockSection from "../../hooks/useStockSection";
import SectionState from "./SectionState";

const SENTIMENT_STYLE = {
  positive: { color: "var(--gain)", bg: "rgba(52,211,153,0.12)", label: "Positive" },
  negative: { color: "var(--loss)", bg: "rgba(248,113,113,0.12)", label: "Negative" },
  neutral: { color: "var(--text-secondary)", bg: "rgba(148,163,184,0.12)", label: "Neutral" },
};

export default function StockNewsPanel({ symbol, name = "", enabled = true }) {
  const { data, loading, error, retry } = useStockSection(
    `/news/stock/${symbol}${name ? `?name=${encodeURIComponent(name)}` : ""}`,
    { enabled },
  );
  const articles = data?.articles || [];

  return (
    <motion.div
      className="glass-card p-5"
      data-testid="stock-news-panel"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4 }}
    >
      <div className="flex items-center gap-2 mb-4">
        <Newspaper size={16} style={{ color: "var(--ai-accent)" }} />
        <h3 className="card-title">News · {symbol}</h3>
      </div>

      <SectionState loading={loading} error={error} onRetry={retry} data={data} rows={5}>
        {articles.length === 0 ? (
          <p className="text-sm py-4 text-center" style={{ color: "var(--text-muted)" }}>
            No recent headlines found for {symbol}.
          </p>
        ) : (
          <div className="space-y-3">
            {articles.map((a, i) => {
              const s = SENTIMENT_STYLE[a.sentiment] || SENTIMENT_STYLE.neutral;
              return (
                <a
                  key={`${a.link || a.title}-${i}`}
                  href={a.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block rounded-xl p-3 border transition-all hover:scale-[1.005]"
                  style={{ borderColor: "var(--border)", background: "var(--bg-surface)" }}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium leading-snug" style={{ color: "var(--text-primary)" }}>
                        {a.title}
                      </p>
                      {a.summary && (
                        <p className="text-xs mt-1 line-clamp-2" style={{ color: "var(--text-secondary)" }}>
                          {a.summary}
                        </p>
                      )}
                      <div className="flex items-center gap-2 mt-2 text-[10px]" style={{ color: "var(--text-muted)" }}>
                        {a.source && <span>{a.source}</span>}
                        {a.published && <span>· {a.published}</span>}
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-2 shrink-0">
                      <span
                        className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full"
                        style={{ background: s.bg, color: s.color }}
                      >
                        {s.label}
                      </span>
                      <ExternalLink size={12} style={{ color: "var(--text-muted)" }} />
                    </div>
                  </div>
                </a>
              );
            })}
          </div>
        )}
      </SectionState>
    </motion.div>
  );
}

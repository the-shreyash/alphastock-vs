import { motion } from "framer-motion";
import { Newspaper, ExternalLink } from "lucide-react";
import SectionUnavailable from "./SectionUnavailable";

/**
 * NewsHeadlines — the overnight headlines that matter, ranked by the backend.
 *
 * Market-moving stories carry a "Breaking" badge; sentiment is shown as a dot
 * rather than a word so the list stays scannable at a glance before the open.
 * Every headline links out to its source — the platform summarizes news, it
 * never asks the user to take its word for it.
 */
const SENTIMENT_COLOR = {
  positive: "var(--gain)",
  negative: "var(--loss)",
  neutral: "var(--text-muted)",
};

function Headline({ article }) {
  return (
    <a
      href={article.link}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex items-start gap-3 py-2.5 transition-opacity hover:opacity-80"
    >
      <span
        className="w-1.5 h-1.5 rounded-full mt-[7px] shrink-0"
        style={{ background: SENTIMENT_COLOR[article.sentiment] || SENTIMENT_COLOR.neutral }}
      />
      <span className="flex-1 min-w-0">
        <span className="flex items-start gap-2">
          <span className="text-[13px] leading-snug" style={{ color: "var(--text-primary)" }}>
            {article.title}
          </span>
          <ExternalLink
            size={11}
            className="mt-0.5 shrink-0 opacity-0 group-hover:opacity-60 transition-opacity"
            style={{ color: "var(--text-muted)" }}
          />
        </span>
        <span className="flex items-center gap-2 mt-1">
          <span className="text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>
            {article.source}
          </span>
          {article.importance === "high" && (
            <span
              className="text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded"
              style={{ background: "rgba(244,63,94,0.12)", color: "var(--loss)" }}
            >
              Breaking
            </span>
          )}
        </span>
      </span>
    </a>
  );
}

export default function NewsHeadlines({ news, sentiment }) {
  const headlines = news?.headlines || [];

  return (
    <motion.div
      className="glass-card p-5"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4 }}
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="eyebrow flex items-center gap-2">
          <Newspaper size={13} /> Overnight News
        </h3>
        {sentiment?.available && (
          <span className="text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>
            Sentiment{" "}
            <span
              className="font-semibold"
              style={{
                color:
                  sentiment.label === "Bullish" ? "var(--gain)"
                    : sentiment.label === "Bearish" ? "var(--loss)"
                      : "var(--text-secondary)",
              }}
            >
              {sentiment.label} {sentiment.score}/100
            </span>
          </span>
        )}
      </div>

      {!news?.available || headlines.length === 0 ? (
        <SectionUnavailable note={news?.note} icon={Newspaper} />
      ) : (
        <div className="divide-y" style={{ borderColor: "var(--border)" }}>
          {headlines.map((a, i) => <Headline key={a.link || i} article={a} />)}
        </div>
      )}
    </motion.div>
  );
}

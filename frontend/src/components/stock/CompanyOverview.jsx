import { useState } from "react";
import { motion } from "framer-motion";
import { Building2, Globe, Users, MapPin, Layers } from "lucide-react";
import useStockSection from "../../hooks/useStockSection";
import SectionState from "./SectionState";
import { formatNumber } from "../../utils/formatters";

function Chip({ icon: Icon, children, href }) {
  const content = (
    <span
      className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border"
      style={{ borderColor: "var(--border)", color: "var(--text-secondary)", background: "var(--bg-surface)" }}
    >
      <Icon size={12} style={{ color: "var(--ai-accent)" }} />
      {children}
    </span>
  );
  if (href) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className="hover:opacity-80 transition-opacity">
        {content}
      </a>
    );
  }
  return content;
}

export default function CompanyOverview({ symbol, enabled = true }) {
  const { data, loading, error, retry } = useStockSection(`/stocks/${symbol}/profile`, { enabled });
  const [expanded, setExpanded] = useState(false);

  return (
    <motion.div
      className="glass-card p-5"
      data-testid="company-overview"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4 }}
    >
      <div className="flex items-center gap-2 mb-3">
        <Building2 size={16} style={{ color: "var(--ai-accent)" }} />
        <h3 className="card-title">Company Overview</h3>
      </div>

      <SectionState loading={loading} error={error} onRetry={retry} data={data} rows={3}>
        <div className="flex flex-wrap gap-2 mb-3">
          {data?.industry && <Chip icon={Layers}>{data.industry}</Chip>}
          {data?.employees != null && <Chip icon={Users}>{formatNumber(data.employees, 0)} employees</Chip>}
          {(data?.city || data?.country) && (
            <Chip icon={MapPin}>{[data.city, data.country].filter(Boolean).join(", ")}</Chip>
          )}
          {data?.website && (
            <Chip icon={Globe} href={data.website}>
              {data.website.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}
            </Chip>
          )}
        </div>

        {data?.description ? (
          <>
            <p
              className="text-sm leading-relaxed"
              style={{
                color: "var(--text-secondary)",
                ...(expanded
                  ? {}
                  : {
                      display: "-webkit-box",
                      WebkitLineClamp: 4,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }),
              }}
            >
              {data.description}
            </p>
            <button
              onClick={() => setExpanded((v) => !v)}
              className="mt-2 text-xs font-medium underline"
              style={{ color: "var(--ai-accent)" }}
            >
              {expanded ? "Show less" : "Read more"}
            </button>
          </>
        ) : (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            No company description available from the live source.
          </p>
        )}
      </SectionState>
    </motion.div>
  );
}

import { motion } from "framer-motion";
import { Landmark } from "lucide-react";
import useStockSection from "../../hooks/useStockSection";
import SectionState from "./SectionState";
import { formatNumber } from "../../utils/formatters";

// Group → field definitions matching the backend /fundamentals contract.
// Missing Yahoo values arrive as null and render as "—" (never invented).
const GROUPS = [
  {
    key: "valuation",
    title: "Valuation",
    fields: [
      { key: "pe", label: "P/E (TTM)" },
      { key: "forward_pe", label: "Forward P/E" },
      { key: "pb", label: "Price / Book" },
      { key: "peg", label: "PEG Ratio" },
      { key: "ev_ebitda", label: "EV / EBITDA" },
    ],
  },
  {
    key: "per_share",
    title: "Per Share",
    fields: [
      { key: "eps", label: "EPS (TTM)", prefix: "₹" },
      { key: "book_value", label: "Book Value", prefix: "₹" },
      { key: "dividend_rate", label: "Dividend / Share", prefix: "₹" },
      { key: "dividend_yield_pct", label: "Dividend Yield", suffix: "%" },
    ],
  },
  {
    key: "profitability",
    title: "Profitability",
    fields: [
      { key: "roe_pct", label: "ROE", suffix: "%" },
      { key: "roa_pct", label: "ROA", suffix: "%" },
      { key: "gross_margin_pct", label: "Gross Margin", suffix: "%" },
      { key: "operating_margin_pct", label: "Operating Margin", suffix: "%" },
      { key: "net_margin_pct", label: "Net Margin", suffix: "%" },
    ],
  },
  {
    key: "growth",
    title: "Growth",
    fields: [
      { key: "revenue_growth_pct", label: "Revenue Growth (YoY)", suffix: "%", signed: true },
      { key: "earnings_growth_pct", label: "Earnings Growth (YoY)", suffix: "%", signed: true },
      { key: "week_52_change_pct", label: "52-Week Change", suffix: "%", signed: true },
    ],
  },
  {
    key: "health",
    title: "Financial Health",
    fields: [
      { key: "debt_to_equity", label: "Debt / Equity" },
      { key: "current_ratio", label: "Current Ratio" },
      { key: "total_cash_cr", label: "Total Cash", suffix: " Cr", prefix: "₹" },
      { key: "free_cashflow_cr", label: "Free Cash Flow", suffix: " Cr", prefix: "₹" },
    ],
  },
  {
    key: "market",
    title: "Market",
    fields: [
      { key: "market_cap_cr", label: "Market Cap", suffix: " Cr", prefix: "₹" },
      { key: "beta", label: "Beta" },
      { key: "shares_outstanding_cr", label: "Shares Outstanding", suffix: " Cr" },
    ],
  },
];

function formatValue(field, value) {
  if (value == null) return "—";
  const num = formatNumber(value);
  const sign = field.signed && value > 0 ? "+" : "";
  return `${sign}${field.prefix || ""}${num}${field.suffix || ""}`;
}

export default function FundamentalsPanel({ symbol, enabled = true }) {
  const { data, loading, error, retry } = useStockSection(`/stocks/${symbol}/fundamentals`, { enabled });

  return (
    <motion.div
      className="glass-card p-5"
      data-testid="fundamentals-panel"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4 }}
    >
      <div className="flex items-center gap-2 mb-4">
        <Landmark size={16} style={{ color: "var(--ai-accent)" }} />
        <h3 className="card-title">Fundamental Analysis</h3>
      </div>

      <SectionState loading={loading} error={error} onRetry={retry} data={data} rows={6}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {GROUPS.map((group) => {
            const values = data?.[group.key] || {};
            return (
              <div key={group.key} className="rounded-xl p-4 border" style={{ borderColor: "var(--border)", background: "var(--bg-surface)" }}>
                <h4 className="eyebrow mb-3">{group.title}</h4>
                <div className="space-y-2">
                  {group.fields.map((f) => {
                    const v = values[f.key];
                    const colored = f.signed && v != null;
                    return (
                      <div key={f.key} className="flex items-center justify-between">
                        <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{f.label}</span>
                        <span
                          className="text-sm font-mono font-medium"
                          style={{ color: colored ? (v >= 0 ? "var(--gain)" : "var(--loss)") : "var(--text-primary)" }}
                        >
                          {formatValue(f, v)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
        <p className="text-[10px] mt-3 text-right" style={{ color: "var(--text-muted)" }}>
          Live from Yahoo Finance · missing fields shown as —
        </p>
      </SectionState>
    </motion.div>
  );
}

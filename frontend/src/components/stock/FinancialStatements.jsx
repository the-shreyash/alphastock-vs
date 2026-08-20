import { useState } from "react";
import { motion } from "framer-motion";
import { FileText } from "lucide-react";
import useStockSection from "../../hooks/useStockSection";
import SectionState from "./SectionState";
import { formatNumber } from "../../utils/formatters";

const STATEMENTS = [
  { key: "income", label: "Income" },
  { key: "balance", label: "Balance Sheet" },
  { key: "cashflow", label: "Cash Flow" },
];

const PERIODS = [
  { key: "annual", label: "Annual" },
  { key: "quarterly", label: "Quarterly" },
];

export default function FinancialStatements({ symbol, enabled = true }) {
  const [statement, setStatement] = useState("income");
  const [period, setPeriod] = useState("annual");
  const { data, loading, error, retry } = useStockSection(
    `/stocks/${symbol}/financials?statement=${statement}&period=${period}`,
    { enabled },
  );

  const columns = data?.columns || [];
  const rows = data?.rows || [];

  return (
    <motion.div
      className="glass-card p-5"
      data-testid="financial-statements"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4 }}
    >
      <div className="flex items-center justify-between gap-3 flex-wrap mb-4">
        <div className="flex items-center gap-2">
          <FileText size={16} style={{ color: "var(--ai-accent)" }} />
          <h3 className="card-title">Financial Statements</h3>
          <span className="caption">· {data?.unit || "₹ Cr"}</span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="segment-control">
            {STATEMENTS.map((s) => (
              <button
                key={s.key}
                data-testid={`statement-${s.key}`}
                onClick={() => setStatement(s.key)}
                className={`segment-btn text-[11px] ${statement === s.key ? "active" : ""}`}
              >
                {s.label}
              </button>
            ))}
          </div>
          <div className="segment-control">
            {PERIODS.map((p) => (
              <button
                key={p.key}
                data-testid={`period-${p.key}`}
                onClick={() => setPeriod(p.key)}
                className={`segment-btn text-[11px] ${period === p.key ? "active" : ""}`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <SectionState loading={loading} error={error} onRetry={retry} data={data} rows={6}>
        {rows.length === 0 ? (
          <p className="text-sm py-4 text-center" style={{ color: "var(--text-muted)" }}>
            No {period} {statement} statement data available from the live source.
          </p>
        ) : (
          <div className="overflow-x-auto -mx-1">
            <table className="w-full text-sm">
              <thead>
                <tr style={{ color: "var(--text-muted)" }}>
                  <th className="font-medium text-[11px] uppercase tracking-wider py-2 px-1 text-left sticky left-0" style={{ background: "var(--bg-card)" }}>
                    Line Item
                  </th>
                  {columns.map((c) => (
                    <th key={c} className="font-medium text-[11px] uppercase tracking-wider py-2 px-2 text-right whitespace-nowrap font-mono">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.key} className="border-t" style={{ borderColor: "var(--border)" }}>
                    <td className="py-2.5 px-1 sticky left-0" style={{ color: "var(--text-secondary)", background: "var(--bg-card)" }}>
                      {row.label}
                    </td>
                    {columns.map((c, i) => {
                      const v = row.values?.[i];
                      return (
                        <td
                          key={c}
                          className="py-2.5 px-2 text-right font-mono whitespace-nowrap"
                          style={{ color: v != null && v < 0 ? "var(--loss)" : "var(--text-primary)" }}
                        >
                          {v != null ? formatNumber(v, 0) : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-[10px] mt-3 text-right" style={{ color: "var(--text-muted)" }}>
          All figures in {data?.unit || "₹ Cr"} · live market data
        </p>
      </SectionState>
    </motion.div>
  );
}

import { motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Users } from "lucide-react";
import useStockSection from "../../hooks/useStockSection";
import SectionState from "./SectionState";
import { formatCurrency, formatNumber } from "../../utils/formatters";

export default function PeersTable({ symbol, enabled = true }) {
  const { data, loading, error, retry } = useStockSection(`/stocks/${symbol}/peers`, { enabled });
  const navigate = useNavigate();
  const peers = data?.peers || [];

  return (
    <motion.div
      className="glass-card p-5"
      data-testid="peers-table"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4 }}
    >
      <div className="flex items-center gap-2 mb-3">
        <Users size={16} style={{ color: "var(--ai-accent)" }} />
        <h3 className="card-title">Sector Peers</h3>
        {data?.sector && <span className="caption">· {data.sector}</span>}
      </div>

      <SectionState loading={loading} error={error} onRetry={retry} data={data} rows={4}>
        {peers.length === 0 ? (
          <p className="text-sm py-4 text-center" style={{ color: "var(--text-muted)" }}>
            No other stocks from this sector are tracked yet.
          </p>
        ) : (
          <div className="overflow-x-auto -mx-1">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left" style={{ color: "var(--text-muted)" }}>
                  <th className="font-medium text-[11px] uppercase tracking-wider py-2 px-1">Stock</th>
                  <th className="font-medium text-[11px] uppercase tracking-wider py-2 px-1 text-right">Price</th>
                  <th className="font-medium text-[11px] uppercase tracking-wider py-2 px-1 text-right">Change</th>
                  <th className="font-medium text-[11px] uppercase tracking-wider py-2 px-1 text-right hidden sm:table-cell">Volume</th>
                </tr>
              </thead>
              <tbody>
                {peers.map((p) => {
                  const pos = (p.change_pct || 0) >= 0;
                  return (
                    <tr
                      key={p.symbol}
                      data-testid={`peer-row-${p.symbol}`}
                      onClick={() => navigate(`/stock/${p.symbol}`)}
                      className="cursor-pointer border-t transition-colors hover:bg-[var(--hover)]"
                      style={{ borderColor: "var(--border)" }}
                    >
                      <td className="py-2.5 px-1">
                        <span className="font-semibold block" style={{ color: "var(--text-primary)" }}>{p.symbol}</span>
                        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{p.name}</span>
                      </td>
                      <td className="py-2.5 px-1 text-right font-mono" style={{ color: "var(--text-primary)" }}>
                        {p.price != null ? formatCurrency(p.price) : "—"}
                      </td>
                      <td className="py-2.5 px-1 text-right font-mono font-medium" style={{ color: pos ? "var(--gain)" : "var(--loss)" }}>
                        {p.change_pct != null ? `${pos ? "+" : ""}${p.change_pct.toFixed(2)}%` : "—"}
                      </td>
                      <td className="py-2.5 px-1 text-right font-mono hidden sm:table-cell" style={{ color: "var(--text-secondary)" }}>
                        {p.volume != null ? formatNumber(p.volume, 0) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionState>
    </motion.div>
  );
}

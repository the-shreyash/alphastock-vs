import { motion } from "framer-motion";
import { Info } from "lucide-react";

// Extracted verbatim from StockDetail.jsx — live RSI/MACD/volume readout.
export default function TechnicalIndicators({ quote }) {
  if (!quote) return null;
  return (
    <motion.div
      className="glass-card p-5"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4 }}
    >
      <h3 className="card-title mb-3">Technical Indicators</h3>
      <p className="text-xs mb-3 p-2 rounded-lg flex items-start gap-2" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
        <Info size={12} className="shrink-0 mt-0.5" /> RSI above 70 = overbought (may fall). Below 30 = oversold (may rise). MACD crossing signal line = trend change.
      </p>
      <div className="space-y-3">
        {[
          { label: "RSI (14)", value: quote.rsi, hint: quote.rsi > 70 ? "Overbought" : quote.rsi < 30 ? "Oversold" : "Neutral" },
          { label: "MACD", value: quote.macd },
          { label: "MACD Signal", value: quote.macd_signal },
          { label: "Volume Ratio", value: `${quote.volume_ratio}x avg` },
        ].map((ind) => (
          <div key={ind.label} className="flex items-center justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span className="text-sm" style={{ color: "var(--text-secondary)" }}>{ind.label}</span>
            <div className="text-right">
              <span className="text-sm font-mono font-medium" style={{ color: "var(--text-primary)" }}>{ind.value}</span>
              {ind.hint && <span className="text-[10px] ml-2" style={{ color: "var(--text-muted)" }}>{ind.hint}</span>}
            </div>
          </div>
        ))}
      </div>
    </motion.div>
  );
}

import { motion } from "framer-motion";
import { Info } from "lucide-react";

import { isFieldAvailable, fieldUnavailableReason } from "../../lib/fieldQuality";
import { MetricValue } from "../ui/Unavailable";

/**
 * The live RSI / MACD / volume readout on the stock detail page.
 *
 * D6.8-A — THE THIRD SURFACE D5.19 DID NOT REACH (§G-6), AND THE SECOND COPY
 * OF IT.
 *
 * This file was "extracted verbatim from StockDetail.jsx" and then imported by
 * nothing, so the page kept rendering its own inline copy and this one sat
 * beside it as a duplicate waiting to drift. StockDetail now renders this
 * component and the inline copy is gone: one implementation, so a correction
 * here cannot be true in one place and false in the other.
 *
 * WHAT THE CORRECTION IS
 * ----------------------
 * Each row used to render `quote.rsi` straight into the DOM, and the backend
 * guaranteed a number by substituting one (`rsi = 50.0`, `volume_ratio = 1.0`,
 * `macd = 0.0`). A newly listed stock with no 14-day history therefore
 * displayed **"RSI (14)  50  Neutral"** — a value, a label and a verdict, in
 * the same typeface as a real reading, none of it measured. `Volume Ratio` was
 * worse still: the template literal `${quote.volume_ratio}x avg` rendered the
 * literal string "undefined x avg" the moment the substitution was removed.
 *
 * `MetricValue` is the design system's existing answer to "we cannot compute
 * this number" (PH3.9): a muted em-dash where the figure would be, the reason
 * on hover, and the card, label and grid left intact so the absence is noticed
 * rather than hidden. The hint is computed only when the reading is real — a
 * "Neutral" derived from a value we do not have is the same fabricated claim as
 * the number itself, one column to the right.
 */
export default function TechnicalIndicators({ quote }) {
  if (!quote) return null;

  const indicators = [
    {
      label: "RSI (14)",
      field: "rsi",
      value: quote.rsi,
      hint: isFieldAvailable(quote, "rsi")
        ? quote.rsi > 70
          ? "Overbought"
          : quote.rsi < 30
          ? "Oversold"
          : "Neutral"
        : null,
    },
    { label: "MACD", field: "macd", value: quote.macd },
    { label: "MACD Signal", field: "macd_signal", value: quote.macd_signal },
    {
      label: "Volume Ratio",
      field: "volume_ratio",
      value: quote.volume_ratio,
      format: (v) => `${v}x avg`,
    },
  ];

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
        {indicators.map((ind) => (
          <div key={ind.label} className="flex items-center justify-between py-1 border-b" style={{ borderColor: "var(--border)" }}>
            <span className="text-sm" style={{ color: "var(--text-secondary)" }}>{ind.label}</span>
            <div className="text-right">
              <span className="text-sm font-mono font-medium" style={{ color: "var(--text-primary)" }}>
                <MetricValue
                  value={isFieldAvailable(quote, ind.field) ? ind.value : null}
                  reason={fieldUnavailableReason(quote, ind.field)}
                  format={ind.format}
                />
              </span>
              {ind.hint && <span className="text-[10px] ml-2" style={{ color: "var(--text-muted)" }}>{ind.hint}</span>}
            </div>
          </div>
        ))}
      </div>
    </motion.div>
  );
}

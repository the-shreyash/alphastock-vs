import { motion } from "framer-motion";
import { Zap, ArrowUpRight, ArrowDownRight } from "lucide-react";
import SectionUnavailable from "./SectionUnavailable";

/**
 * GiftNiftyCard — the pre-market read on where Nifty is likely to open.
 *
 * Gift Nifty trades on NSE International Exchange while the NSE cash market is
 * closed, which makes it the single most-watched number before the open. It
 * requires a licensed feed; when none is connected the card says exactly that
 * (see services/market_engine/gift_nifty.py) instead of showing a derived guess.
 */
export default function GiftNiftyCard({ giftNifty }) {
  const available = giftNifty?.available;
  const changePct = giftNifty?.change_pct;
  const hasChange = changePct !== null && changePct !== undefined;
  const isPos = hasChange && changePct >= 0;

  const gapLabel = !hasChange
    ? null
    : Math.abs(changePct) < 0.15
      ? "Flat open indicated"
      : `${isPos ? "Gap-up" : "Gap-down"} open indicated`;

  return (
    <motion.div
      className="glass-card p-5"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4 }}
    >
      <h3 className="eyebrow mb-3 flex items-center gap-2">
        <Zap size={13} style={{ color: "#f59e0b" }} /> Gift Nifty
      </h3>

      {!available ? (
        <SectionUnavailable note={giftNifty?.note} />
      ) : (
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="stat-value">{giftNifty.value?.toLocaleString("en-IN")}</p>
            {gapLabel && (
              <p className="text-[12px] mt-1" style={{ color: "var(--text-muted)" }}>
                {gapLabel}
              </p>
            )}
          </div>
          {hasChange && (
            <div className="text-right">
              <span
                className="text-[15px] font-mono font-semibold flex items-center gap-1 justify-end"
                style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}
              >
                {isPos ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
                {isPos ? "+" : ""}{changePct.toFixed(2)}%
              </span>
              {giftNifty.change !== null && giftNifty.change !== undefined && (
                <span className="text-[12px] font-mono" style={{ color: "var(--text-muted)" }}>
                  {isPos ? "+" : ""}{giftNifty.change.toFixed(2)} pts
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </motion.div>
  );
}

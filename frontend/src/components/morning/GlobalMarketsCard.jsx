import { motion } from "framer-motion";
import { Globe } from "lucide-react";
import SectionUnavailable from "./SectionUnavailable";

/**
 * GlobalMarketsCard — the overnight cues that set the tone for the Indian open.
 *
 * Indices are grouped by region (US → Asia → UK) because that is the order they
 * actually close in overnight, so the list reads as a timeline of the session
 * the trader slept through. An index whose quote could not be fetched is shown
 * as "—" rather than omitted: a missing Dow is itself information.
 */
const REGION_ORDER = ["US", "Asia", "UK"];

function MarketRow({ market }) {
  const isPos = (market.change_pct || 0) >= 0;
  return (
    <div className="flex items-center justify-between py-2">
      <span className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
        {market.name}
      </span>
      {market.available ? (
        <span className="flex items-baseline gap-2">
          <span className="text-[13px] font-mono" style={{ color: "var(--text-secondary)" }}>
            {market.value?.toLocaleString("en-US", { maximumFractionDigits: 2 })}
          </span>
          <span
            className="text-[13px] font-mono font-semibold w-[62px] text-right"
            style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}
          >
            {isPos ? "+" : ""}{market.change_pct?.toFixed(2)}%
          </span>
        </span>
      ) : (
        <span className="text-[12px] font-mono" style={{ color: "var(--text-muted)" }}>
          unavailable
        </span>
      )}
    </div>
  );
}

export default function GlobalMarketsCard({ globalMarkets }) {
  const markets = globalMarkets?.markets || [];

  const byRegion = REGION_ORDER
    .map((region) => ({ region, items: markets.filter((m) => m.region === region) }))
    .filter((g) => g.items.length > 0);

  return (
    <motion.div
      className="glass-card p-5"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4 }}
    >
      <h3 className="eyebrow mb-3 flex items-center gap-2" style={{ color: "#818cf8" }}>
        <Globe size={13} /> Global Markets
      </h3>

      {markets.length === 0 ? (
        <SectionUnavailable note={globalMarkets?.summary} icon={Globe} />
      ) : (
        <>
          <p className="body-text mb-4">{globalMarkets.summary}</p>
          <div className="space-y-3">
            {byRegion.map(({ region, items }) => (
              <div key={region}>
                <p className="stat-label mb-1">{region}</p>
                <div style={{ borderTop: "1px solid var(--border)" }}>
                  {items.map((m) => <MarketRow key={m.name} market={m} />)}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </motion.div>
  );
}

/**
 * Stock rankings — "Top Opportunities" on the dashboard, "Stock Rankings" on Markets.
 *
 * D5.19 — WHAT THIS COMPONENT WAS, AND WHY IT LOOKED FAKE
 * -------------------------------------------------------
 * The brief that reached this sprint said the list was hardcoded. It never was:
 * `/market/ranking` runs the real multi-dimensional scorer over real universe
 * quotes, and the five symbols on the dashboard are today's top five by score.
 * D5.18 verified that against the live endpoint.
 *
 * What was true is that the surface behaved like a fixture, for three separate
 * reasons, and any one of them is enough to make a real list look invented:
 *
 *   1. **The prices never moved.** It fetched once on mount and subscribed to
 *      nothing. Every other price on the dashboard followed `priceTicks`; these
 *      sat at the value they were ranked with until the tab was reloaded. It
 *      now folds the same store through the same `applyLivePrices` helper the
 *      watchlist, portfolio and Top AI Picks use — one merge rule, one place to
 *      get the identity semantics right (LIM-D5.18-4).
 *
 *   2. **It gave no reason.** A row asserted "Buy · 67.3" and offered no
 *      evidence, and in `compact` mode the dimension panel was not rendered at
 *      all. Each row now carries the scoring factors that actually placed it
 *      there.
 *
 *   3. **It went nowhere.** Clicking expanded a panel. A stock is now a link to
 *      its stock, like every other stock in the product.
 *
 * THE EVIDENCE IS THE SERVER'S, VERBATIM
 * --------------------------------------
 * `evidence` is chosen by `ranking_engine.build_evidence` from dimensions whose
 * inputs were actually present, ordered by weighted contribution. This file
 * renders those strings and composes nothing.
 *
 * That restraint is the whole feature. Before D5.19 the engine emitted a reason
 * for every dimension whether or not it had the data — it reported "MACD
 * bearish" for stocks with no MACD and "Very low liquidity" for a stock that
 * had traded 8.3 million shares that morning — so piping the dimension panel
 * to the browser would have shipped fabricated evidence under a "why this
 * stock" heading. An empty `evidence` array is a valid, honest answer and is
 * rendered as silence, never as a filler sentence.
 */
import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import {
  Trophy,
  RefreshCw,
  TrendingUp,
  Shield,
  BarChart3,
  Activity,
  Newspaper,
  Layers,
  Droplets,
  Brain,
  ChevronRight,
} from "lucide-react";
import api from "../../services/api";
import { useRealtimeStore, selectPriceTicks } from "../../store/realtimeStore";
import { applyLivePrices } from "../../lib/livePrices";
import { formatNumber } from "../../utils/formatters";

const SIGNAL_CONFIG = {
  strong_buy: { label: "Strong Buy", color: "#10b981", bg: "#10b98120" },
  buy: { label: "Buy", color: "#22c55e", bg: "#22c55e20" },
  neutral: { label: "Neutral", color: "#f59e0b", bg: "#f59e0b20" },
  sell: { label: "Sell", color: "#ef4444", bg: "#ef444420" },
  strong_sell: { label: "Strong Sell", color: "#dc2626", bg: "#dc262620" },
};

const DIMENSION_ICONS = {
  momentum: TrendingUp,
  trend: Activity,
  volume: BarChart3,
  risk: Shield,
  news: Newspaper,
  sector: Layers,
  liquidity: Droplets,
  ai_confidence: Brain,
};

/**
 * How fresh the ranked prices are — never which provider supplied them.
 *
 * `source_tier` is the only provenance a REST response may carry
 * (MARKET_DATA_ARCHITECTURE.md, Developer Rule 4), and the label is the
 * platform's vocabulary rather than the tier string, because "streaming" is an
 * implementation word and "Live" is what it means to a reader.
 */
function TierBadge({ tier }) {
  if (!tier) return null;
  const live = tier === "streaming";
  return (
    <span
      data-testid="ranking-tier"
      className="text-[9px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded"
      style={{
        background: live ? "var(--gain-bg, #10b98118)" : "var(--hover)",
        color: live ? "var(--gain)" : "var(--text-muted)",
      }}
      title={
        live
          ? "Prices are streaming from a connected live feed."
          : "Prices are from the delayed baseline feed."
      }
    >
      {live ? "Live" : "Delayed"}
    </span>
  );
}

/**
 * The answer to "why this stock?", drawn from the scoring engine and nothing else.
 *
 * Renders no fallback. A stock the engine could not explain shows its score and
 * its price and says nothing about why — which is the honest output, and the
 * reason `build_evidence` is allowed to return an empty list at all.
 */
function Evidence({ symbol, evidence }) {
  if (!evidence?.length) return null;
  return (
    <div className="mt-1.5 space-y-1">
      {evidence.map((item, i) => {
        const Icon = DIMENSION_ICONS[item.dimension] || Activity;
        return (
          <div key={`${item.dimension}-${i}`} className="flex items-start gap-1.5">
            <Icon size={10} className="mt-[3px] shrink-0 text-[var(--text-muted)]" />
            <span
              data-testid={`ranking-evidence-${symbol}-${i}`}
              className="text-[10px] leading-snug text-[var(--text-secondary)]"
            >
              {item.reason}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default function RankingTable({ compact = false }) {
  const [rankings, setRankings] = useState([]);
  const [tier, setTier] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sector, setSector] = useState("");
  const navigate = useNavigate();
  const priceTicks = useRealtimeStore(selectPriceTicks);

  const fetchRankings = useCallback(() => {
    setLoading(true);
    const params = { top_n: compact ? 5 : 10 };
    if (sector) params.sector = sector;
    api.get("/market/ranking", { params })
      .then((r) => {
        setRankings(r.data.rankings || []);
        setTier(r.data.source_tier || null);
      })
      .catch(() => {
        setRankings([]);
        setTier(null);
      })
      .finally(() => setLoading(false));
  }, [compact, sector]);

  useEffect(() => {
    fetchRankings();
  }, [fetchRankings]);

  /**
   * D-6 — a broker tick moves a ranked price.
   *
   * The *ranking* is deliberately not recomputed on a tick: the order is a
   * server-side score over the whole universe, and re-sorting five rows on the
   * client against prices that arrived for some of them would produce an
   * ordering the score never justified. A tick updates what a stock costs, not
   * where it placed.
   */
  useEffect(() => {
    if (!priceTicks) return;
    setRankings((prev) => applyLivePrices(prev, priceTicks));
  }, [priceTicks]);

  if (loading) {
    return (
      <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] p-4">
        <div className="animate-pulse space-y-3">
          {[...Array(compact ? 3 : 5)].map((_, i) => (
            <div key={i} className="h-10 bg-[var(--bg-tertiary)] rounded-lg" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl bg-[var(--bg-secondary)] border border-[var(--border)] overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Trophy size={14} className="text-[var(--accent)]" />
          <span className="text-sm font-semibold text-[var(--text-primary)]">
            {compact ? "Top Opportunities" : "Stock Rankings"}
          </span>
          <span className="text-[10px] text-[var(--text-muted)]">{rankings.length} stocks</span>
          <TierBadge tier={tier} />
        </div>
        <div className="flex items-center gap-2">
          {!compact && (
            <select
              value={sector}
              onChange={(e) => setSector(e.target.value)}
              aria-label="Filter by sector"
              className="text-[10px] px-2 py-1 rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border border-[var(--border)]"
            >
              <option value="">All Sectors</option>
              {["Banking", "IT", "Pharma", "Auto", "FMCG", "Oil & Gas", "Metals", "Power"].map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          )}
          <button onClick={fetchRankings} aria-label="Refresh rankings" className="text-[var(--text-muted)] hover:text-[var(--accent)]">
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* Rankings */}
      {rankings.length === 0 ? (
        <div className="p-6 text-center text-[var(--text-muted)] text-xs">
          No ranked opportunities available
        </div>
      ) : (
        rankings.map((stock, i) => {
          const signal = SIGNAL_CONFIG[stock.signal] || SIGNAL_CONFIG.neutral;
          const changePct = stock.change_pct;
          const isPositive = (changePct ?? 0) >= 0;

          return (
            <motion.button
              key={stock.symbol}
              data-testid={`ranking-row-${stock.symbol}`}
              type="button"
              initial={{ opacity: 0, y: 5 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.04 }}
              onClick={() => navigate(`/stock/${stock.symbol}`)}
              aria-label={`${stock.symbol} details`}
              className="w-full px-4 py-2.5 border-b border-[var(--border)] last:border-0 flex items-start gap-3 hover:bg-[var(--bg-hover)] transition-colors text-left"
            >
              {/* Rank badge */}
              <div className="w-5 h-5 mt-0.5 rounded-full bg-[var(--bg-tertiary)] flex items-center justify-center text-[10px] font-bold text-[var(--text-muted)] shrink-0">
                {i + 1}
              </div>

              {/* Stock info and the reason it is here */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-[var(--text-primary)]">{stock.symbol}</span>
                  {/* The live price. `applyLivePrices` writes this field and no
                      other, so a tick can never fabricate the day change beside
                      it — a canonical MarketTick does not carry one. */}
                  <span
                    data-testid={`ranking-price-${stock.symbol}`}
                    className="text-[11px] font-mono text-[var(--text-secondary)]"
                  >
                    {stock.price != null ? formatNumber(stock.price) : "—"}
                  </span>
                  {changePct != null && (
                    <span className={`text-[10px] font-mono ${isPositive ? "text-[var(--gain)]" : "text-[var(--loss)]"}`}>
                      {isPositive ? "+" : ""}{changePct.toFixed(2)}%
                    </span>
                  )}
                </div>
                <div className="text-[10px] text-[var(--text-muted)] truncate">{stock.name}</div>
                <Evidence symbol={stock.symbol} evidence={stock.evidence} />
              </div>

              {/* Opportunity score */}
              <div className="text-center shrink-0 mt-0.5">
                <div className="text-sm font-bold text-[var(--text-primary)] font-mono">
                  {stock.opportunity_score}
                </div>
                <div className="text-[8px] text-[var(--text-muted)] uppercase">Score</div>
              </div>

              {/* Signal badge */}
              <span
                className="text-[9px] font-semibold px-2 py-0.5 rounded-full shrink-0 mt-1"
                style={{ backgroundColor: signal.bg, color: signal.color }}
              >
                {signal.label}
              </span>

              <ChevronRight size={12} className="text-[var(--text-muted)] shrink-0 mt-1.5" />
            </motion.button>
          );
        })
      )}
    </div>
  );
}

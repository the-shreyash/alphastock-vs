import { ArrowUpRight, ArrowDownRight } from "lucide-react";
import { cn } from "../../lib/utils";
import { formatNumber } from "../../utils/formatters";
import { Unavailable } from "../ui/Unavailable";

/**
 * A signed market change: "+1.28% (+36.20)" in green, "-0.42%" in red.
 *
 * This is the single most-repeated fragment in the product — it was hand-built
 * in the dashboard stat cards, the index strip, holdings rows, watchlist rows,
 * movers lists, sector bars and the stock header, each with its own arrow size,
 * decimal count and sign logic. Small divergences there are exactly what makes
 * a product feel assembled from parts.
 *
 * TWO RULES ENCODED HERE
 * ----------------------
 * 1. **Zero is flat, not positive.** `>= 0` (the idiom this replaces) paints an
 *    unchanged instrument green with an up-arrow, which is a false claim about
 *    the market. Exact zero renders neutral.
 * 2. **null is unavailable, not zero.** A missing change renders the shared
 *    `Unavailable` treatment rather than "+0.00%", keeping the platform's
 *    "never present absent data as measured data" rule intact.
 */

const SIZES = {
  sm: { text: "text-[11px]", icon: 10, gap: "gap-0.5" },
  md: { text: "text-[13px]", icon: 12, gap: "gap-1" },
  lg: { text: "text-[15px]", icon: 14, gap: "gap-1" },
};

/** `null`/`undefined` -> null (unknown); otherwise -1, 0 or 1. */
export function directionOf(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  const n = Number(value);
  return n > 0 ? 1 : n < 0 ? -1 : 0;
}

/** Machine-readable direction, mirroring what the colour communicates. */
export const DIRECTION_LABEL = { 1: "up", "-1": "down", 0: "flat" };

/** The token a direction should paint with. Flat stays neutral, never green. */
export function colorForDirection(direction) {
  if (direction === 1) return "var(--gain)";
  if (direction === -1) return "var(--loss)";
  return "var(--text-muted)";
}

export default function DeltaValue({
  /** Percentage change, e.g. 1.28 for +1.28%. */
  percent,
  /** Absolute change in currency/points, rendered in parentheses. */
  absolute,
  size = "md",
  showIcon = true,
  /** Hide the "(+36.20)" tail even when `absolute` is supplied. */
  compact = false,
  className,
  reason,
  ...rest
}) {
  const source = percent ?? absolute;
  const direction = directionOf(source);

  if (direction === null) return <Unavailable reason={reason} label="Change not available" />;

  const { text, icon, gap } = SIZES[size] ?? SIZES.md;
  const color = colorForDirection(direction);
  const sign = direction > 0 ? "+" : ""; // negatives carry their own "-"
  const Arrow = direction === 1 ? ArrowUpRight : ArrowDownRight;

  return (
    <span
      /*
       * `data-direction` states the reading in the DOM rather than only in the
       * colour. Colour alone is not a signal a screen reader, a colour-blind
       * user or a test can read — the arrow covers the first two, and this
       * covers the third: jsdom discards `var()` values entirely, so an
       * assertion on the inline colour can never fail and would prove nothing.
       */
      data-direction={DIRECTION_LABEL[direction]}
      className={cn("inline-flex items-center font-mono font-semibold tabular-nums", gap, text, className)}
      style={{ color }}
      {...rest}
    >
      {showIcon && direction !== 0 && <Arrow size={icon} aria-hidden="true" />}
      {percent != null && (
        <span>
          {sign}
          {Number(percent).toFixed(2)}%
        </span>
      )}
      {absolute != null && !compact && (
        <span className="font-normal" style={{ color: "var(--text-muted)" }}>
          ({sign}
          {formatNumber(absolute)})
        </span>
      )}
      {percent == null && absolute != null && compact && (
        <span>
          {sign}
          {formatNumber(absolute)}
        </span>
      )}
    </span>
  );
}

import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { useId } from "react";
import { cn } from "../../lib/utils";
import { Card } from "./Card";
import DeltaValue, { directionOf, colorForDirection } from "./DeltaValue";
import { Unavailable } from "../ui/Unavailable";

/**
 * The headline-number card: Portfolio Value, Today's P&L, NIFTY 50, Watchlist
 * count, AI Signals — the row of four that opens the dashboard and the markets
 * page in the reference.
 *
 * The value uses the mono `stat-value` treatment so digits are tabular and
 * columns of figures line up; the label is the uppercase `stat-label`. Both are
 * existing platform classes, so a MetricCard and a hand-built stat card sitting
 * side by side during migration are indistinguishable.
 *
 * A missing `value` renders the shared `Unavailable` em-dash rather than "0" —
 * the platform never presents absent data as a measured figure.
 */
export default function MetricCard({
  label,
  value,
  /** Percentage change shown under the value. */
  changePct,
  /** Absolute change, rendered in parentheses after the percentage. */
  change,
  /** Free-text line under the value, used when there is no numeric delta. */
  hint,
  /** `[{ close: number }]` — draws a faint area sparkline in the corner. */
  sparkData,
  /** Turns the whole card into a button. */
  onClick,
  /**
   * Ref attached to the value element. The dashboard's price-flash hook needs
   * a handle on the exact node whose number changed, so it is exposed rather
   * than leaving callers to re-implement the card to get at it.
   */
  valueRef,
  loading = false,
  reason,
  className,
  ...rest
}) {
  const gradientId = useId().replace(/:/g, "");
  const direction = directionOf(changePct ?? change);

  if (loading) {
    return (
      <Card padding="md" className={cn("relative overflow-hidden", className)} {...rest}>
        <div className="h-3 w-20 rounded skeleton mb-2.5" />
        <div className="h-7 w-28 rounded skeleton" />
      </Card>
    );
  }

  return (
    <Card
      as={onClick ? "button" : "div"}
      padding="md"
      interactive={!!onClick}
      onClick={onClick}
      type={onClick ? "button" : undefined}
      className={cn("relative overflow-hidden", onClick && "w-full text-left", className)}
      {...rest}
    >
      <span className="stat-label block mb-1.5">{label}</span>

      <div ref={valueRef} className="stat-value inline-block rounded-md px-0.5">
        {value === null || value === undefined || value === "" ? (
          <Unavailable reason={reason} label={`${label} not available`} />
        ) : (
          value
        )}
      </div>

      {(changePct != null || change != null) && (
        <div className="mt-1">
          <DeltaValue percent={changePct} absolute={change} size="sm" />
        </div>
      )}
      {hint && changePct == null && change == null && (
        <p className="caption mt-1">{hint}</p>
      )}

      {sparkData?.length > 1 && (
        <div className="absolute bottom-0 right-0 w-24 h-10 opacity-30 pointer-events-none" aria-hidden="true">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={sparkData} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={`spark-${gradientId}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={colorForDirection(direction)} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={colorForDirection(direction)} stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area
                type="monotone"
                dataKey="close"
                stroke={colorForDirection(direction)}
                strokeWidth={1.5}
                fill={`url(#spark-${gradientId})`}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}

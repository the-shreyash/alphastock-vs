import { cn } from "../../lib/utils";

/**
 * The one status pill for the product: connection state, market state, broker
 * state, order state, data freshness.
 *
 * Before this existed each surface built its own `<span>` with inline
 * background/colour pairs, so "Connected", "LIVE", "Market Open" and "Filled"
 * — four instances of the same idea — rendered at three different sizes with
 * four different green tints.
 *
 * The `tone` prop is deliberately semantic (`positive`, `negative`, `warning`,
 * `neutral`, `info`, `ai`) rather than a colour name, so a token change is a
 * one-line edit here instead of a sweep.
 */

const TONES = {
  positive: { bg: "var(--gain-bg)", fg: "var(--gain)" },
  negative: { bg: "var(--loss-bg)", fg: "var(--loss)" },
  warning: { bg: "var(--warning-bg)", fg: "var(--warning)" },
  info: { bg: "var(--info-bg)", fg: "var(--info)" },
  ai: { bg: "var(--ai-accent-soft)", fg: "var(--ai-accent)" },
  confirm: { bg: "var(--confirm-soft)", fg: "var(--confirm)" },
  neutral: { bg: "var(--hover)", fg: "var(--text-muted)" },
};

const SIZES = {
  sm: "text-[9px] px-1.5 py-0.5 gap-1",
  md: "text-[10px] px-2.5 py-1 gap-1.5",
  lg: "text-[11px] px-3 py-1.5 gap-1.5",
};

export default function StatusBadge({
  children,
  tone = "neutral",
  size = "md",
  /** Show a small filled dot before the label. `pulse` animates it. */
  dot = false,
  pulse = false,
  icon: Icon,
  className,
  ...rest
}) {
  const resolved = TONES[tone] ? tone : "neutral";
  const { bg, fg } = TONES[resolved];
  return (
    <span
      // The resolved tone, in the DOM. An unknown tone falls back to neutral,
      // and this is what makes that fallback observable.
      data-tone={resolved}
      className={cn(
        "inline-flex items-center rounded-full font-mono font-bold uppercase tracking-wider whitespace-nowrap",
        SIZES[size] ?? SIZES.md,
        className
      )}
      style={{ background: bg, color: fg }}
      {...rest}
    >
      {dot && (
        <span
          className={cn("rounded-full shrink-0", pulse && "animate-pulse")}
          style={{ width: 6, height: 6, background: fg }}
          aria-hidden="true"
        />
      )}
      {Icon && <Icon size={size === "sm" ? 9 : 11} aria-hidden="true" />}
      {children}
    </span>
  );
}

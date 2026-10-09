import { useRef } from "react";
import { cn } from "../../lib/utils";

/**
 * The product's tab row, in the two shapes the design language actually uses:
 *
 *   variant="pill"       a segmented control on a tinted track — for switching
 *                        *workspaces* within a page (Markets: Indices /
 *                        Sectors / Top Gainers).
 *   variant="underline"  a flat row with an accent rule under the active item
 *                        — for switching *views of one subject* (Stock
 *                        Analysis: Overview / Chart / AI Analysis / Financials).
 *
 * Both were previously hand-rolled per page: Markets, Portfolio, StockDetail,
 * News and Settings each had their own, and the Markets one was painted with
 * the shadcn `accent` compat token, which holds a bare HSL triple rather than
 * a colour — so its active tab had no background at all.
 *
 * KEYBOARD BEHAVIOUR
 * ------------------
 * Left/Right move between tabs, Home/End jump to the ends, and only the active
 * tab is in the tab order (`tabIndex=-1` on the rest). That is the WAI-ARIA
 * tabs pattern; a row of plain buttons — what this replaces — forces a screen
 * reader user to tab through every option to reach the content.
 */
export default function SegmentedTabs({
  /** `[{ key, label, icon?, badge?, disabled? }]` */
  tabs,
  value,
  onChange,
  variant = "pill",
  size = "md",
  /** Accessible name for the tab row, e.g. "Market views". */
  label,
  className,
  ...rest
}) {
  const refs = useRef({});

  const enabled = tabs.filter((t) => !t.disabled);

  const onKeyDown = (event) => {
    const idx = enabled.findIndex((t) => t.key === value);
    if (idx === -1) return;
    let next = null;
    if (event.key === "ArrowRight") next = enabled[(idx + 1) % enabled.length];
    else if (event.key === "ArrowLeft") next = enabled[(idx - 1 + enabled.length) % enabled.length];
    else if (event.key === "Home") next = enabled[0];
    else if (event.key === "End") next = enabled[enabled.length - 1];
    if (!next) return;
    event.preventDefault();
    onChange(next.key);
    refs.current[next.key]?.focus();
  };

  const isPill = variant === "pill";
  const pad = size === "sm" ? "px-3 py-1.5 text-[12px]" : "px-4 py-2 text-[13px]";

  return (
    <div
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cn(
        "flex overflow-x-auto",
        isPill
          ? "gap-1 p-1 rounded-xl"
          : "gap-1 border-b",
        className
      )}
      style={
        isPill
          ? { background: "var(--bg-elevated)", border: "1px solid var(--border)" }
          : { borderColor: "var(--border)" }
      }
      {...rest}
    >
      {tabs.map((tab) => {
        const active = tab.key === value;
        const Icon = tab.icon;
        return (
          <button
            key={tab.key}
            ref={(el) => { refs.current[tab.key] = el; }}
            role="tab"
            type="button"
            aria-selected={active}
            aria-controls={`panel-${tab.key}`}
            id={`tab-${tab.key}`}
            tabIndex={active ? 0 : -1}
            disabled={tab.disabled}
            onClick={() => onChange(tab.key)}
            className={cn(
              "flex items-center gap-1.5 font-medium whitespace-nowrap shrink-0 transition-colors",
              pad,
              isPill ? "rounded-lg" : "rounded-t-lg border-b-2 -mb-px",
              tab.disabled && "opacity-40 cursor-not-allowed"
            )}
            style={{
              // The active state is a subtle tinted surface with an accent
              // marker, never a saturated fill — see --nav-active-* in
              // index.css for why.
              background: isPill && active ? "var(--bg-card-glass)" : "transparent",
              boxShadow: isPill && active ? "var(--shadow-sm)" : "none",
              borderBottomColor: !isPill && active ? "var(--nav-active-marker)" : "transparent",
              color: active ? "var(--text-primary)" : "var(--text-secondary)",
              fontWeight: active ? 600 : 500,
            }}
          >
            {Icon && <Icon size={13} aria-hidden="true" />}
            {tab.label}
            {tab.badge != null && (
              <span
                className="ml-0.5 text-[10px] font-mono px-1.5 rounded-full"
                style={{ background: "var(--hover)", color: "var(--text-muted)" }}
              >
                {tab.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The panel matching a `SegmentedTabs` tab. Wiring `role="tabpanel"` and the
 * id/aria-labelledby pair here means a page cannot forget them.
 */
export function TabPanel({ tabKey, value, children, className, ...rest }) {
  if (tabKey !== value) return null;
  return (
    <div
      role="tabpanel"
      id={`panel-${tabKey}`}
      aria-labelledby={`tab-${tabKey}`}
      tabIndex={0}
      className={className}
      {...rest}
    >
      {children}
    </div>
  );
}

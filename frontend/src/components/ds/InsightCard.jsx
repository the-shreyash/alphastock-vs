import { Sparkles, ArrowRight } from "lucide-react";
import { cn } from "../../lib/utils";
import { Card } from "./Card";
import StatusBadge from "./StatusBadge";

/**
 * The AI insight surface — the product's intelligence layer made visible.
 *
 * THE STRUCTURE IS THE POINT
 * --------------------------
 * The product rule is that the AI must never simply recommend: every insight
 * answers *what happened*, *why it matters* and *what to watch*. That rule was
 * previously enforced by whoever happened to be writing the page, so some
 * surfaces rendered a bare sentence of model output with no reasoning at all.
 *
 * Here those three are first-class props. A caller that has only a headline
 * gets a card that visibly lacks its reasoning section, which makes the gap
 * obvious in review instead of invisible in production.
 *
 * `variant="featured"` is the dark, high-contrast treatment used for the single
 * top insight on a page. It is deliberately scarce: if every insight is
 * featured, none is.
 */
export default function InsightCard({
  title,
  /** What happened — the observation. */
  summary,
  /** Why it matters — the reasoning. */
  rationale,
  /** What to watch — the forward-looking note. */
  watch,
  /** e.g. "Banking", "Portfolio", "NIFTY 50". */
  category,
  /** ISO string or pre-formatted "2 hours ago". */
  timestamp,
  variant = "default",
  /** Renders a call-to-action row at the bottom. */
  action,
  onAction,
  actionLabel = "Read full analysis",
  className,
  ...rest
}) {
  const featured = variant === "featured";

  return (
    <Card
      padding="lg"
      className={cn("relative overflow-hidden", className)}
      style={
        featured
          ? {
              // A deep, desaturated green — the "intelligence" surface. It is a
              // literal colour rather than a token because it is the one place
              // in the product that inverts, and inverting a token would drag
              // every other consumer of that token with it.
              background: "linear-gradient(150deg, #0D2A1F 0%, #10241C 100%)",
              borderColor: "rgba(255,255,255,0.08)",
              color: "#F2F7F4",
            }
          : undefined
      }
      {...rest}
    >
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        {featured ? (
          <span
            className="inline-flex items-center gap-1.5 text-[9px] font-mono font-bold uppercase tracking-wider px-2 py-1 rounded-full"
            style={{ background: "rgba(0,196,140,0.18)", color: "#4ADE9C" }}
          >
            <Sparkles size={9} aria-hidden="true" /> Top insight
          </span>
        ) : (
          <StatusBadge tone="ai" size="sm" icon={Sparkles}>
            AI insight
          </StatusBadge>
        )}
        {category && (
          <span
            className="text-[11px] font-medium"
            style={{ color: featured ? "rgba(242,247,244,0.65)" : "var(--text-muted)" }}
          >
            {category}
          </span>
        )}
        {timestamp && (
          <span
            className="text-[11px] ml-auto"
            style={{ color: featured ? "rgba(242,247,244,0.5)" : "var(--text-muted)" }}
          >
            {timestamp}
          </span>
        )}
      </div>

      <h3
        className="card-title mb-2"
        style={featured ? { color: "#FFFFFF" } : undefined}
      >
        {title}
      </h3>

      {summary && (
        <p
          className="body-text"
          style={featured ? { color: "rgba(242,247,244,0.82)" } : undefined}
        >
          {summary}
        </p>
      )}

      {(rationale || watch) && (
        <dl className="mt-4 space-y-3">
          {rationale && (
            <div>
              <dt
                className="eyebrow mb-1"
                style={featured ? { color: "rgba(242,247,244,0.5)" } : undefined}
              >
                Why it matters
              </dt>
              <dd
                className="body-text"
                style={featured ? { color: "rgba(242,247,244,0.82)" } : undefined}
              >
                {rationale}
              </dd>
            </div>
          )}
          {watch && (
            <div>
              <dt
                className="eyebrow mb-1"
                style={featured ? { color: "rgba(242,247,244,0.5)" } : undefined}
              >
                What to watch
              </dt>
              <dd
                className="body-text"
                style={featured ? { color: "rgba(242,247,244,0.82)" } : undefined}
              >
                {watch}
              </dd>
            </div>
          )}
        </dl>
      )}

      {(action || onAction) && (
        <div className="mt-5">
          {action ?? (
            <button
              type="button"
              onClick={onAction}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-[13px] font-semibold transition-transform hover:-translate-y-px"
              style={
                featured
                  ? { background: "#FFFFFF", color: "#0D2A1F" }
                  : { background: "var(--brand-accent)", color: "var(--brand-accent-fg)" }
              }
            >
              {actionLabel}
              <ArrowRight size={13} aria-hidden="true" />
            </button>
          )}
        </div>
      )}
    </Card>
  );
}

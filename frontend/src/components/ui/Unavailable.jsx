import { HelpCircle } from "lucide-react";

/**
 * PH3.9 — the design-system answer to "we cannot compute this number".
 *
 * WHY THIS EXISTS AS A COMPONENT RATHER THAN `value ?? 0`
 * -------------------------------------------------------
 * The whole point of the backend contract change is that an unavailable metric
 * arrives as `null` and must not be coerced into a number on this side of the
 * HTTP boundary. `{stats?.mrr || 0}` — the idiom these pages used everywhere —
 * silently undoes it: it renders `₹0`, which reads as a measured fact, and it
 * does so in the same typeface, weight and colour as a real figure.
 *
 * **Zero and "no data" are different claims.** `₹0` says we looked and found no
 * revenue. Nothing here says we have no revenue *source*. On a dashboard those
 * two are indistinguishable unless the interface makes them look different, so
 * this is a rendering concern as much as an API one.
 *
 * The treatment is deliberately calm — a muted em-dash where the number would
 * be, and the reason available on hover — rather than a warning colour. An
 * unavailable metric is not an error and must not make the page look broken
 * (the interface should not cry wolf about a known, documented gap); it is
 * simply a number we do not have. That is also why it keeps the surrounding
 * card, label and layout intact: removing the card would shift the grid and
 * make the absence harder to notice, not easier.
 */
export function Unavailable({ reason, label = "Not available" }) {
  return (
    <span
      className="inline-flex items-center gap-1"
      style={{ color: "var(--text-muted)" }}
      title={reason || "This metric cannot be computed from the data the platform records."}
      aria-label={reason ? `${label}: ${reason}` : label}
    >
      <span className="font-mono select-none" aria-hidden="true">—</span>
      <HelpCircle size={11} aria-hidden="true" />
    </span>
  );
}

/**
 * Render `children` when a value is present, the unavailable treatment when it
 * is not.
 *
 * `null` and `undefined` are unavailable; **`0` is a value** and renders
 * normally. That distinction is the entire contract, so it is expressed once
 * here rather than at thirty call sites where one of them will get it wrong
 * with a falsy check.
 */
export function MetricValue({ value, reason, format, children }) {
  if (value === null || value === undefined) return <Unavailable reason={reason} />;
  if (children) return children;
  return <>{format ? format(value) : value}</>;
}

/**
 * The reason text for `name`, pulled out of an analytics envelope.
 *
 * Every PH3.9 response carries `analytics.metrics[name].note` explaining why a
 * metric is unavailable. Surfacing it beats a generic tooltip: "the platform
 * has no payment integration" and "session records are reaped after 7 days" are
 * different problems with different owners, and an operator who can read which
 * one applies does not have to open the source to find out.
 */
export function unavailableReason(payload, name) {
  return payload?.analytics?.metrics?.[name]?.note || "";
}

/**
 * A full-panel empty state, for a chart whose entire dataset is unavailable.
 *
 * Used where the alternative would be an axis with no line on it — which reads
 * as "we measured and the value was flat at zero" and is the chart-shaped
 * version of the same defect.
 */
export function UnavailablePanel({ title, reason, requiredSource }) {
  return (
    <div
      role="note"
      className="flex flex-col items-center justify-center text-center px-6 py-10 rounded-xl gap-2"
      style={{ background: "var(--bg-surface)", border: "1px dashed var(--border)" }}
    >
      <HelpCircle size={20} style={{ color: "var(--text-muted)" }} aria-hidden="true" />
      <p className="text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
        {title || "No data available"}
      </p>
      {reason && (
        <p className="text-[11px] leading-relaxed max-w-md" style={{ color: "var(--text-muted)" }}>
          {reason}
        </p>
      )}
      {requiredSource && (
        <p className="text-[11px] leading-relaxed max-w-md" style={{ color: "var(--text-muted)" }}>
          <b>Needs:</b> {requiredSource}
        </p>
      )}
    </div>
  );
}

export default Unavailable;

import { RefreshCw, CloudOff, Info } from "lucide-react";

/**
 * Shared loading / error / unavailable wrapper for stock-detail sections.
 *
 * Renders (in order of precedence):
 *  1. skeleton rows while `loading`
 *  2. error message + Retry button when `error`
 *  3. explicit "live source unavailable" note when the API answered
 *     `{available: false}` (hard data rule: never simulate)
 *  4. `children` once real data is present
 */
export default function SectionState({ loading, error, onRetry, data, rows = 4, children }) {
  if (loading) {
    return (
      <div className="space-y-3" data-testid="section-loading">
        {[...Array(rows)].map((_, i) => (
          <div key={i} className="h-10 rounded-xl skeleton" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-8" data-testid="section-error">
        <CloudOff size={28} className="mx-auto mb-2" style={{ color: "var(--text-muted)", opacity: 0.5 }} />
        <p className="text-sm mb-3" style={{ color: "var(--text-muted)" }}>{error}</p>
        {onRetry && (
          <button onClick={onRetry} className="btn-secondary btn-sm">
            <RefreshCw size={12} /> Retry
          </button>
        )}
      </div>
    );
  }

  if (data && data.available === false) {
    return (
      <div
        className="flex items-start gap-2 p-3 rounded-xl text-xs"
        data-testid="section-unavailable"
        style={{ background: "var(--ai-accent-soft)", color: "var(--text-secondary)" }}
      >
        <Info size={14} className="shrink-0 mt-0.5" style={{ color: "var(--ai-accent)" }} />
        <div>
          <p className="font-semibold mb-0.5" style={{ color: "var(--text-primary)" }}>
            Live data temporarily unavailable
          </p>
          <p>{data.note || "This section will populate when the live data source recovers."}</p>
          {onRetry && (
            <button onClick={onRetry} className="mt-2 underline" style={{ color: "var(--ai-accent)" }}>
              Try again
            </button>
          )}
        </div>
      </div>
    );
  }

  if (!data) return null;
  return children;
}

/**
 * The application's error boundary (PH3.7).
 *
 * WHY REACT NEEDS THIS AND WHY IT HAD TO BE A CLASS
 * -------------------------------------------------
 * Since React 16, an error thrown during render, in a lifecycle method, or in a
 * constructor **unmounts the entire component tree** if nothing catches it. Not
 * a broken panel — a white page, with the error visible only in a console the
 * user will never open. This application had no boundary at all, so every
 * render bug in any of ~30 lazy-loaded pages had exactly that blast radius.
 *
 * `componentDidCatch` and `getDerivedStateFromError` have no hooks equivalent;
 * a class component is still the only way to catch a render error, which is why
 * this is the one class component in the codebase.
 *
 * WHAT IT DOES NOT CATCH
 * ----------------------
 * Event handlers, `setTimeout` callbacks, and async code. Those never pass
 * through React's render path, so no boundary sees them — they are covered by
 * the `window` handlers in `services/telemetry.js`, which is why the two are
 * installed together and why neither alone is sufficient.
 *
 * WHY THERE ARE TWO LEVELS OF BOUNDARY
 * ------------------------------------
 * `App` wraps the router in one boundary (the last line of defence, which shows
 * a full-page recovery screen) and each routed page in another. Without the
 * inner one, a crash inside the portfolio page would blank the navigation
 * alongside it, and the user's only route out of a broken screen would be the
 * browser's back button. With it, the shell survives and the failure is
 * contained to the panel that caused it.
 *
 * WHAT IT SHOWS THE USER
 * ----------------------
 * In production: what happened, in a sentence, and two ways forward. Never the
 * stack, never the raw message — a React error message can quote component
 * props, which in this application means positions, prices and account values,
 * and a screenshot of a crash then becomes a leak. Outside production the
 * message is shown, because that is the whole value of the screen to a
 * developer.
 */
import React from "react";
import {
  reportClientError,
  isChunkLoadError,
  routeOf,
  TELEMETRY_KINDS,
} from "@/services/telemetry";

/**
 * Guard against a reload loop on a chunk-load failure.
 *
 * A stale `index.html` pointing at a purged bundle is fixed by one reload. If
 * the reload does not fix it — the deploy is genuinely broken, or a proxy is
 * serving a bad cache — reloading again is an infinite refresh against a
 * failing origin, from every affected browser at once. `sessionStorage` bounds
 * it to a single attempt per tab.
 */
const RELOAD_FLAG = "sa:chunk-reload-attempted";

function hasAttemptedReload() {
  try {
    return sessionStorage.getItem(RELOAD_FLAG) === "1";
  } catch {
    // Private browsing, or storage disabled. Treat as "already attempted": a
    // missing guard is worse than a missed auto-recovery, because the user
    // still has the manual Reload button below.
    return true;
  }
}

function markReloadAttempted() {
  try {
    sessionStorage.setItem(RELOAD_FLAG, "1");
  } catch {
    /* Nothing to do; the guard above already fails safe. */
  }
}

const isProduction = () => process.env.NODE_ENV === "production";

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, isChunkError: false };
  }

  static getDerivedStateFromError(error) {
    return { error, isChunkError: isChunkLoadError(error) };
  }

  componentDidCatch(error, info) {
    // A chunk-load failure is a deploy artefact, not a bug, and it is reported
    // under its own kind so it can be excluded from the alert on render errors
    // — otherwise every release would page someone.
    reportClientError(
      isChunkLoadError(error) ? TELEMETRY_KINDS.CHUNK_LOAD : TELEMETRY_KINDS.RENDER,
      error,
      { route: routeOf(window.location?.pathname) },
    );

    if (!isProduction()) {
      // Development only. In production this would put a component stack —
      // which can contain rendered prop values — into a console log that
      // extensions and screenshots can read.
      // eslint-disable-next-line no-console
      console.error("ErrorBoundary caught an error", error, info?.componentStack);
    }

    if (isChunkLoadError(error) && !hasAttemptedReload()) {
      markReloadAttempted();
      window.location.reload();
    }
  }

  handleRetry = () => {
    // Clearing the error re-renders the children. Enough for a transient
    // failure (a race, a momentarily-undefined prop); if the cause is
    // deterministic the boundary simply catches again, which is the honest
    // outcome and not a loop, because it needs a click each time.
    this.setState({ error: null, isChunkError: false });
    this.props.onRetry?.();
  };

  handleReload = () => {
    window.location.reload();
  };

  render() {
    const { error, isChunkError } = this.state;
    if (!error) return this.props.children;

    const title = isChunkError ? "A new version is available" : "Something went wrong";
    const description = isChunkError
      ? "This page was updated while you had it open. Reloading will pick up the new version."
      : "This section could not be displayed. The rest of the app is still working, and nothing you have saved is affected.";

    return (
      <div
        role="alert"
        className="min-h-[50vh] flex items-center justify-center p-6"
        style={{ background: "var(--bg)" }}
      >
        <div
          className="max-w-md w-full rounded-2xl p-8 flex flex-col gap-4"
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            boxShadow: "var(--shadow-lg, 0 20px 40px rgba(0,0,0,0.08))",
          }}
        >
          <span
            className="text-[10px] font-mono uppercase tracking-widest"
            style={{ color: "var(--text-muted)" }}
          >
            {isChunkError ? "Update required" : "Error"}
          </span>

          <h2 className="text-xl font-semibold" style={{ color: "var(--text)" }}>
            {title}
          </h2>

          <p className="text-sm leading-relaxed" style={{ color: "var(--text-muted)" }}>
            {description}
          </p>

          {/* Development only — see the module docstring on why the message is
              withheld in production. */}
          {!isProduction() && error?.message ? (
            <pre
              className="text-xs overflow-x-auto rounded-lg p-3 whitespace-pre-wrap"
              style={{ background: "var(--bg)", color: "var(--text-muted)" }}
            >
              {error.message}
            </pre>
          ) : null}

          <div className="flex gap-3 pt-2">
            {!isChunkError && (
              <button
                type="button"
                onClick={this.handleRetry}
                className="px-4 py-2 rounded-lg text-sm font-medium transition-opacity hover:opacity-80"
                style={{ background: "var(--surface-2, var(--bg))", color: "var(--text)", border: "1px solid var(--border)" }}
              >
                Try again
              </button>
            )}
            <button
              type="button"
              onClick={this.handleReload}
              className="px-4 py-2 rounded-lg text-sm font-medium transition-opacity hover:opacity-80"
              style={{ background: "var(--ai-accent)", color: "var(--bg)" }}
            >
              Reload page
            </button>
          </div>
        </div>
      </div>
    );
  }
}

/**
 * Client-side failure reporting (PH3.7).
 *
 * WHY THIS EXISTS
 * ---------------
 * Until this module, a frontend crash was completely invisible to the people
 * running the system. The request that served the bundle returned 200 several
 * minutes earlier; a React render error, a rejected promise or a failed chunk
 * load raises nothing on the server, writes no log line, and moves no metric.
 * Every backend dashboard reads perfectly healthy while the user stares at a
 * blank page. The only report anyone gets is a support ticket, hours later,
 * saying "the site is broken", with no version, no route and no error.
 *
 * This module closes that hole with the smallest thing that works: catch the
 * three ways a browser surfaces an unhandled failure, reduce each to a bounded,
 * non-identifying summary, and POST it to `/api/observability/client-errors`,
 * where it becomes `frontend_errors_total{kind=...}` and one structured log
 * line.
 *
 * WHAT IS DELIBERATELY NOT COLLECTED
 * ----------------------------------
 * No third-party SDK, no session replay, no analytics, no user identity. In
 * particular, and by construction rather than by convention:
 *
 *   * **No query strings.** `location.search` in this app can carry a Google
 *     OAuth `code`, a broker callback token and a password-recovery token. Only
 *     `location.pathname` is sent, and it is normalised (see `routeOf`) so an
 *     id in the path does not travel either.
 *   * **No user id, email or token.** Nothing reads `localStorage`, the auth
 *     context, or a cookie. The report says what broke and where in the app,
 *     never to whom — the server correlates by time and version, which is
 *     enough to find a deploy-shaped incident and is all an unauthenticated
 *     endpoint should ever carry.
 *   * **No request or response bodies.**
 *   * **No `window.location.href`,** for the query-string reason above.
 *
 * WHY IT IS RATE LIMITED IN THE CLIENT AS WELL AS THE SERVER
 * ----------------------------------------------------------
 * A render loop can throw thousands of times a second, and an error inside a
 * reporting path is the classic way to turn one bug into a self-inflicted
 * denial of service against your own API. So: a hard per-session cap, plus
 * deduplication by signature, applied *before* anything is sent. The server's
 * per-IP limiter is the backstop, not the control.
 */

/**
 * Absolute, because the API is a different origin from the bundle in every
 * deployment of this app (see `services/api.js`), and `sendBeacon` resolves a
 * relative URL against the *document* — which would post every report to the
 * static host, where nothing is listening and nothing would ever say so.
 */
const ENDPOINT = `${process.env.REACT_APP_BACKEND_URL || ""}/api/observability/client-errors`;

/**
 * Hard cap on reports per page session. Twenty is enough to characterise an
 * incident (the first one is nearly always the real cause) and small enough
 * that a render loop cannot become a traffic source.
 */
const MAX_REPORTS_PER_SESSION = 20;

/** Field caps, mirroring the server's. Clipping here saves the bandwidth. */
const MAX_MESSAGE = 300;
const MAX_STACK = 2000;
const MAX_NAME = 100;

const KINDS = Object.freeze({
  RENDER: "render",
  UNHANDLED_REJECTION: "unhandled_rejection",
  UNCAUGHT: "uncaught",
  CHUNK_LOAD: "chunk_load",
  API: "api",
  WEBSOCKET: "websocket",
});

let reportCount = 0;
let installed = false;
const seenSignatures = new Set();

/** Recent reports, for the error boundary to show a support reference. Bounded. */
const recent = [];
const MAX_RECENT = 10;

function clip(value, limit) {
  if (typeof value !== "string") return "";
  // Newlines are stripped, not escaped: these end up in a server log line, and
  // a newline in an anonymous field is log injection.
  return value.replace(/[\r\n]+/g, " ").slice(0, limit).trim();
}

/**
 * The app route a failure happened on, with anything id-shaped removed.
 *
 * `/stock/RELIANCE` and `/trades/6512a…` would otherwise be a different route
 * per symbol and per trade — unbounded on a server-side label, and needless
 * detail in a log. The pathname only; never `search`, never `hash`.
 */
export function routeOf(pathname) {
  if (typeof pathname !== "string" || !pathname) return "/";
  return clip(
    pathname
      .split("/")
      .map((segment) => {
        if (!segment) return segment;
        // A Mongo ObjectId, a UUID, or anything mostly-numeric is an id.
        if (/^[0-9a-f]{24}$/i.test(segment)) return ":id";
        if (/^[0-9a-f-]{32,36}$/i.test(segment)) return ":id";
        if (/^\d+$/.test(segment)) return ":id";
        return segment;
      })
      .join("/"),
    200,
  );
}

/**
 * True when an error is a failed lazy-route chunk rather than a code bug.
 *
 * This is the most common "the site is broken" report a code-split SPA
 * produces, and it is not a bug at all: the user is holding an `index.html`
 * from before the last deploy, and the hashed bundle it points at no longer
 * exists. It is worth separating because the fix is different (reload, not
 * rollback) and because a spike of these immediately after a release is a
 * deploy signature, not a regression.
 */
export function isChunkLoadError(error) {
  const name = error?.name || "";
  const message = error?.message || "";
  return (
    name === "ChunkLoadError" ||
    /Loading chunk [\w-]+ failed/i.test(message) ||
    /Loading CSS chunk/i.test(message) ||
    /dynamically imported module/i.test(message)
  );
}

/** A stable key for deduplication: same error, same place, reported once. */
function signatureOf(kind, error, route) {
  return `${kind}|${error?.name || ""}|${clip(error?.message, 120)}|${route}`;
}

function appVersion() {
  return clip(process.env.REACT_APP_VERSION || "", MAX_NAME);
}

/**
 * Send one report. Never throws, never returns a rejected promise.
 *
 * `sendBeacon` first: it survives the page being torn down, which is exactly
 * the state a fatal error tends to precede, and it does not need CORS
 * preflight or a custom header. `fetch` with `keepalive` is the fallback for
 * browsers where the beacon queue is full or the API is missing.
 */
function send(body) {
  const payload = JSON.stringify(body);
  try {
    if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
      // text/plain rather than application/json, deliberately: it keeps the
      // request a CORS "simple request" and so avoids a preflight that a
      // sendBeacon cannot perform. FastAPI parses the body by content, not by
      // this header.
      const blob = new Blob([payload], { type: "text/plain;charset=UTF-8" });
      if (navigator.sendBeacon(ENDPOINT, blob)) return;
    }
    if (typeof fetch === "function") {
      // `.catch()` and not `await`: a failure to report a failure must be
      // silent. Surfacing it would risk triggering the very handler that
      // called us, which is how a reporting path becomes an infinite loop.
      fetch(ENDPOINT, {
        method: "POST",
        // text/plain here too, for the same reason as the beacon: it keeps this
        // a CORS simple request, so a report never depends on a preflight
        // succeeding at the moment the app is already failing.
        headers: { "Content-Type": "text/plain;charset=UTF-8" },
        body: payload,
        keepalive: true,
      }).catch(() => {});
    }
  } catch {
    /* Reporting is best-effort by definition. */
  }
}

/**
 * Report a client-side failure.
 *
 * @param {string} kind    one of `TELEMETRY_KINDS`; anything else is refused
 *                         by the server and counted as a rejected report.
 * @param {unknown} error  the thrown value; non-Errors are tolerated, because
 *                         `throw "boom"` and rejected non-Error promises are
 *                         real and reach these handlers.
 * @param {{route?: string}} [options]
 * @returns {boolean} whether a report was sent (false when capped or deduped).
 */
export function reportClientError(kind, error, options = {}) {
  try {
    if (reportCount >= MAX_REPORTS_PER_SESSION) return false;

    const route =
      options.route ??
      routeOf(typeof window !== "undefined" ? window.location?.pathname : "/");

    const normalised =
      error instanceof Error ? error : { name: "Error", message: String(error ?? "") };
    const resolvedKind = isChunkLoadError(normalised) ? KINDS.CHUNK_LOAD : kind;

    const signature = signatureOf(resolvedKind, normalised, route);
    if (seenSignatures.has(signature)) return false;
    seenSignatures.add(signature);
    reportCount += 1;

    const body = {
      kind: resolvedKind,
      name: clip(normalised.name, MAX_NAME),
      message: clip(normalised.message, MAX_MESSAGE),
      route,
      appVersion: appVersion(),
      stack: clip(normalised.stack, MAX_STACK),
    };

    recent.unshift({ ...body, at: new Date().toISOString() });
    if (recent.length > MAX_RECENT) recent.length = MAX_RECENT;

    send(body);
    return true;
  } catch {
    return false;
  }
}

/** The most recent reports this session, newest first. For debugging and tests. */
export function recentReports() {
  return recent.slice();
}

/**
 * Install the global handlers. Idempotent; call once from the app entry point.
 *
 * Covers the two failure paths React cannot see:
 *
 *   * `unhandledrejection` — an async action with no `.catch()`. In this
 *     codebase that is overwhelmingly a failed API call inside an effect, and
 *     before this it produced a red line in a console nobody was watching.
 *   * `error` — a genuinely uncaught exception, including one thrown from an
 *     event handler or a timer, where React's error boundaries do not apply.
 *
 * Render errors come from `ErrorBoundary` instead, which is the only thing that
 * can see them.
 */
export function installTelemetry() {
  if (installed || typeof window === "undefined") return;
  installed = true;

  window.addEventListener("unhandledrejection", (event) => {
    reportClientError(KINDS.UNHANDLED_REJECTION, event?.reason);
  });

  window.addEventListener("error", (event) => {
    // Resource load failures (a broken <img>) also fire this event but carry no
    // `error`, and reporting them would drown the real signal in noise from
    // third-party images and ad blockers.
    if (!event?.error) return;
    reportClientError(KINDS.UNCAUGHT, event.error);
  });
}

/** Test support: forget the caps and the dedup set. */
export function resetTelemetryForTests() {
  reportCount = 0;
  installed = false;
  seenSignatures.clear();
  recent.length = 0;
}

export const TELEMETRY_KINDS = KINDS;

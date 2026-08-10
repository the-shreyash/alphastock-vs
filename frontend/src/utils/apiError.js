/**
 * Turning an API failure into a sentence a user can act on.
 *
 * The backend speaks three different `detail` dialects and the UI must survive
 * all of them (see backend/server.py):
 *
 *   HTTPException          → detail is a string       "Invalid email or password"
 *   RequestValidationError → detail is an array       [{ loc, msg, type }, …]
 *   Trading Engine reject  → detail is an object      { message, violations[] }
 *
 * Rendering any of the last two directly produces "[object Object]" on screen,
 * which tells a user nothing and tells support even less.
 *
 * Extracted in PH3.2 from the copies that had drifted apart in Login.jsx and
 * Register.jsx, so both screens now explain failures identically.
 */

const GENERIC_MESSAGE = "Something went wrong. Please try again.";
const CONNECTION_MESSAGE = "Could not reach the server. Check your connection and try again.";

/** Render a FastAPI `detail` payload, in any of its three shapes, as text. */
export function formatApiDetail(detail) {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((e) => e?.msg || (typeof e === "string" ? e : JSON.stringify(e))).join(" ");
  }
  if (typeof detail === "object") {
    if (typeof detail.message === "string") return detail.message;
    // An unrecognised object shape: `String(obj)` would print "[object Object]",
    // which is worse than useless on screen and in a support ticket. Serialising
    // it at least preserves what the server actually said.
    try {
      return JSON.stringify(detail);
    } catch {
      return "";
    }
  }
  return String(detail);
}

/**
 * Resolve any thrown API error into a user-facing message.
 *
 * Precedence:
 *   1. The server's own explanation (`response.data.detail`) — always the most
 *      specific and the only one that can say *why*.
 *   2. A message thrown by our own code (e.g. googleAuth's "Google sign-in is
 *      unavailable right now."). These are written for users.
 *   3. A friendly fallback. Raw axios text — "Network Error", "timeout of 0ms
 *      exceeded" — never reaches the screen: `isAxiosError` marks transport
 *      failures, whose messages are diagnostics, not user copy.
 *
 * @param {unknown} err      the caught error
 * @param {string} [fallback] message when nothing better is available
 */
export function resolveApiErrorMessage(err, fallback = GENERIC_MESSAGE) {
  const detail = err?.response?.data?.detail;
  const fromServer = formatApiDetail(detail);
  if (fromServer) return fromServer;

  if (err?.isAxiosError === true) {
    // A transport failure with no response at all: the user's problem is
    // connectivity, so say that rather than "Something went wrong".
    return err.response ? fallback : CONNECTION_MESSAGE;
  }

  if (typeof err?.message === "string" && err.message) return err.message;

  return fallback;
}

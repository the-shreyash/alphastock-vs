/**
 * Browser-local state that belongs to ONE signed-in account (D6.3).
 *
 * THE DEFECT THIS EXISTS FOR
 * --------------------------
 * D6.1 / S8 reset the Zustand realtime store on every identity transition, and
 * D6.2 / F closed the stale-socket and stale-response windows around it. Both
 * are about state held in *memory* for the life of the tab. Neither touches the
 * state the app writes to `localStorage`, which outlives the tab entirely.
 *
 * Two keys were written per user and read back unconditionally:
 *
 *   `sa_recent_stocks`     — the symbols this user opened, written by
 *                            `StockDetail` and rendered by the Dashboard's
 *                            Recent Stocks card.
 *   `ap-recent-searches`   — the symbols this user searched, written and
 *                            rendered by `SearchBox`.
 *
 * Neither was cleared on sign-out. Reproduced in Chrome against a real server:
 * Alice opened DIVISLAB, signed out, Bob signed in in the same tab — and
 * `/stock/DIVISLAB` was a link on Bob's dashboard. Bob's watchlist from the
 * server was correctly empty, so the server-side boundary held; the leak was
 * entirely client-side, and it is exactly the "private activity" class the D6.3
 * invariant names.
 *
 * WHY A KEEP-LIST AND NOT A CLEAR-LIST
 * ------------------------------------
 * A list of keys to *remove* has to be extended by whoever adds the next
 * per-user key, and the failure mode of forgetting is a silent leak that nobody
 * sees until two people share a laptop. A list of keys to *keep* fails the other
 * way: forget to classify a new key and it is wiped on sign-out, which costs a
 * convenience and discloses nothing. The invariant belongs to the boundary, not
 * to every future call site — the same reason `event_bridge.PRIVATE_DOMAINS`
 * drops an unclassified private event rather than broadcasting it.
 *
 * `token` is deliberately not kept: `AuthContext` owns its lifecycle and writes
 * it *after* this runs on the way in, and removes it explicitly on the way out.
 */

/** Keys that are genuinely about the browser, not about the account. */
export const SHARED_LOCAL_KEYS = Object.freeze([
  // Light/dark preference. A device setting; it survives a sign-out on purpose,
  // and it discloses nothing about who was signed in.
  "ap-theme",
]);

/**
 * Forget every browser-local value that belonged to the previous account.
 *
 * Called on sign-in, registration, OAuth adoption and sign-out — the same four
 * transitions that reset the realtime store, for the same reason. Safe to call
 * when storage is unavailable (private mode, a browser that blocks site data):
 * every access is guarded, because failing to clear must never be able to break
 * signing in.
 *
 * Returns the keys it removed, so a test can assert on the effect rather than
 * on the absence of an exception.
 */
export function clearTenantLocalState() {
  const removed = [];
  try {
    const keep = new Set(SHARED_LOCAL_KEYS);
    const keys = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key !== null) keys.push(key);
    }
    for (const key of keys) {
      if (keep.has(key)) continue;
      localStorage.removeItem(key);
      removed.push(key);
    }
  } catch {
    /* storage unavailable — nothing to clear, and nothing to fail */
  }
  try {
    sessionStorage.clear();
  } catch {
    /* as above */
  }
  return removed;
}

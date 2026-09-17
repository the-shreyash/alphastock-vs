/**
 * Per-field data quality — the frontend's projection of the backend's
 * `field_quality` contract (D6.8-A / D6-Q1).
 *
 * WHY THIS MODULE EXISTS
 * ----------------------
 * `services/market_engine/field_quality.py` decides, for each market-data field
 * on a quote, whether the platform may describe it as a measurement. That
 * decision is made once, on the server, by the layer that actually knows —
 * whether a historical window was short, whether a vendor failed, how old the
 * reading is. This module translates the answer into what a user is shown.
 *
 * It contains **no inference**. It never decides a field is stale, never times
 * anything, never derives a state from a value. A second opinion in React would
 * be a second answer to a question the platform has one answer for, and the two
 * would disagree exactly when it mattered. That is `feedState.js`'s rule and
 * this module is its sibling for a different axis.
 *
 * WHAT IT DOES DO IS REFUSE
 * -------------------------
 * The six states are a closed enumeration on the backend. Anything outside them
 * is dropped rather than rendered, so a payload that grows a provider name, a
 * broker code or an exception string under this key leaks nothing — an
 * unrecognised value is not copied at all.
 *
 * The safe direction to be wrong in is DOWN: an unrecognised state projects to
 * `missing`, never to `available`. A field is rendered as a measurement only
 * when the backend has positively said it is one.
 */

/** The six states, byte-identical to `FieldQuality` on the backend. */
export const FIELD_QUALITY = {
  AVAILABLE: "available",
  MISSING: "missing",
  INSUFFICIENT_HISTORY: "insufficient_history",
  STALE: "stale",
  PROVIDER_ERROR: "provider_error",
  UNAVAILABLE: "unavailable",
};

const STATES = new Set(Object.values(FIELD_QUALITY));

/**
 * What each non-available state means, in the platform's words.
 *
 * Plain English with no internal vocabulary: no provider name, no health state,
 * no "capability", no error text. These strings reach a tooltip beside a price,
 * so they are governed by the same disclosure rule as every other generated
 * sentence (`test_d519_surface_disclosure.py`).
 *
 * They deliberately mirror `field_quality.describe()` on the backend rather than
 * inventing a second vocabulary for the same six facts — a user reading "not
 * enough price history to compute" in a tooltip and an AI answer saying
 * something different about the same field is the drift this avoids.
 */
const REASONS = {
  [FIELD_QUALITY.MISSING]: "This reading was not included in the latest market data.",
  [FIELD_QUALITY.INSUFFICIENT_HISTORY]:
    "There is not enough price history for this stock to compute this indicator yet.",
  [FIELD_QUALITY.STALE]:
    "The last reading for this indicator is out of date, so it is not shown as current.",
  [FIELD_QUALITY.PROVIDER_ERROR]:
    "This reading could not be retrieved for the latest market data.",
  [FIELD_QUALITY.UNAVAILABLE]:
    "This indicator is not available from the market data currently serving this stock.",
};

/**
 * The state of `field` on `quote`.
 *
 * Mirrors the backend's `quality_of` in the one respect a client must mirror it:
 * an undeclared `null` is MISSING, and a declared state wins. It deliberately
 * does NOT re-derive staleness from `observed_at` — the server has already done
 * that and shipped the answer, and a browser clock is not the platform's.
 */
export function fieldQuality(quote, field) {
  const map = quote && typeof quote === "object" ? quote.field_quality : null;
  const declared = map && typeof map === "object" ? map[field] : null;

  if (declared !== null && declared !== undefined) {
    // A declaration this build does not recognise is NOT the same as no
    // declaration. The server said something about this field, so falling back
    // to "there is a number, therefore it is a measurement" would upgrade an
    // unknown verdict to the strongest one available. It projects DOWN to
    // `missing` instead — `feedState.js`'s rule, for the same reason.
    return typeof declared === "string" && STATES.has(declared)
      ? declared
      : FIELD_QUALITY.MISSING;
  }

  const value = quote && typeof quote === "object" ? quote[field] : null;
  return value === null || value === undefined
    ? FIELD_QUALITY.MISSING
    : FIELD_QUALITY.AVAILABLE;
}

/** Whether `field` may be rendered as a measurement of the market. */
export function isFieldAvailable(quote, field) {
  return fieldQuality(quote, field) === FIELD_QUALITY.AVAILABLE;
}

/** Why `field` is not a measurement, or `""` when it is one. */
export function fieldUnavailableReason(quote, field) {
  const state = fieldQuality(quote, field);
  if (state === FIELD_QUALITY.AVAILABLE) return "";
  return REASONS[state] || REASONS[FIELD_QUALITY.MISSING];
}

export default fieldQuality;

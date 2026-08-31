/**
 * Consumer feed state — the frontend's single projection of the backend's
 * market-feed contract (D5.14, closing LIM-D5.13-1).
 *
 * WHY THIS MODULE EXISTS AND WHAT IT DELIBERATELY DOES NOT DO
 * -----------------------------------------------------------
 * The backend spent D5.12/D5.13 establishing that "a provider can be tried" and
 * "the feed is actually usable" are different questions, and that only the
 * Source Manager can answer either. Everything that decides which state the
 * feed is in — health, cool-downs, readiness, probation, freshness, delivery
 * latency, provider selection — lives there and is published as ONE payload:
 *
 *     { state, tier, reason, capabilities, previous_tier, change_reason? }
 *
 * This module translates that payload into what a user is told. It contains no
 * inference: it never derives a state from a tier, never times a recovery,
 * never guesses which provider is serving. A second source of truth in React
 * would be a second answer to a question the platform has one answer for, and
 * the two would disagree exactly when it mattered.
 *
 * WHAT IT DOES DO IS REFUSE
 * -------------------------
 * Everything crossing into the UI passes an allow-list. `state`, `tier`,
 * `reason` and `change_reason` are closed enumerations on the backend; anything
 * outside them is dropped rather than rendered. That is what makes "a broker's
 * wire code can never reach a user" true by construction here rather than by
 * the backend never making a mistake — and it is why a payload that grows a
 * `broker`, an `access_token` or an exception string leaks nothing: unlisted
 * fields are not copied at all.
 *
 * The safe direction to be wrong in is DOWN. An unrecognised state projects to
 * `unavailable`, never to `available`.
 */

/** The three-valued consumer feed state (D5.13). */
export const FEED_STATE = {
  /** The feed is actually serving usable data. */
  AVAILABLE: "available",
  /**
   * A provider is being retried after its failure cool-down expired. It is a
   * genuine candidate and a genuine non-answer — a refinement of "not
   * available", NOT a shade of available. Nothing here is live data.
   */
  RECOVERING: "recovering",
  /** No usable feed at all. */
  UNAVAILABLE: "unavailable",
};

/** Freshness of data actually being served. Meaningful only in `available`. */
export const FEED_TIER = { STREAMING: "streaming", DELAYED: "delayed" };

/** Why a feed CHANGED (D5.13). A property of the transition, not the state. */
export const FEED_CHANGE_REASON = {
  ENTITLEMENT_REFUSED: "entitlement_refused",
  SESSION_EXPIRED: "session_expired",
  FEED_DISCONNECTED: "feed_disconnected",
};

/** Why no provider could be resolved (`UnavailableReason` on the backend). */
export const FEED_UNAVAILABLE_REASON = {
  NO_PROVIDERS_REGISTERED: "no_providers_registered",
  NOT_ENTITLED: "not_entitled",
  CAPABILITY_UNSUPPORTED: "capability_unsupported",
  ALL_PROVIDERS_DOWN: "all_providers_down",
};

const STATES = new Set(Object.values(FEED_STATE));
const TIERS = new Set(Object.values(FEED_TIER));
const CHANGE_REASONS = new Set(Object.values(FEED_CHANGE_REASON));
const UNAVAILABLE_REASONS = new Set(Object.values(FEED_UNAVAILABLE_REASON));

const oneOf = (allowed, value) =>
  typeof value === "string" && allowed.has(value) ? value : null;

/**
 * Project one `provider.status` payload onto the fields the UI may use.
 *
 * Field-by-field and allow-listed — never a spread of the payload — so a key
 * the backend adds tomorrow (or a key that should never have been there) is
 * inert until somebody deliberately lists it.
 */
export function projectFeedState(payload) {
  const data = payload && typeof payload === "object" ? payload : {};
  const state = oneOf(STATES, data.state) || FEED_STATE.UNAVAILABLE;
  const available = state === FEED_STATE.AVAILABLE;

  return {
    state,
    // A tier is a claim about the freshness of data a consumer is RECEIVING.
    // Outside `available` nothing is being received, so there is nothing to
    // make the claim about — stamping one there is LIM-D5.12-1 verbatim. The
    // backend already nulls it; this is the second lock on the same door.
    tier: available ? oneOf(TIERS, data.tier) : null,
    reason: oneOf(UNAVAILABLE_REASONS, data.reason),
    changeReason: oneOf(CHANGE_REASONS, data.change_reason),
    previousTier: oneOf(TIERS, data.previous_tier),
    capabilities: Array.isArray(data.capabilities)
      ? data.capabilities.filter((c) => typeof c === "string")
      : [],
  };
}

// ── User-facing vocabulary ────────────────────────────────────────────────
//
// Four words, because there are four things a user can act on differently:
// their data is live; their data is delayed but real; their feed is coming back
// and they must not trade on what is on screen; their feed is gone. The state
// carries the first distinction and the tier the second — `tier` is NOT
// overloaded to carry connection state, and the state is never inferred from
// the tier.

const LABELS = {
  [FEED_STATE.AVAILABLE]: { streaming: "Live", delayed: "Delayed", unknown: "Market data" },
  [FEED_STATE.RECOVERING]: "Recovering",
  [FEED_STATE.UNAVAILABLE]: "Unavailable",
};

/**
 * What each state means, in the platform's words.
 *
 * `recovering` states plainly that the data on screen is not live and stops
 * there: no percentage, no countdown, no ETA, no broker name. The platform
 * genuinely does not know when — or whether — the retry will answer, and the
 * only honest thing to say about an unknown is nothing.
 */
const STATE_DETAIL = {
  [FEED_STATE.AVAILABLE]: {
    streaming: "Streaming market data is live.",
    delayed: "Showing delayed market data.",
    unknown: "Market data is available.",
  },
  [FEED_STATE.RECOVERING]: "The market-data feed is being restored. Prices on screen are not live right now.",
  [FEED_STATE.UNAVAILABLE]: "Market data is unavailable right now.",
};

/**
 * Why the feed CHANGED. Broker-neutral by construction: each sentence says what
 * the platform did to the user's feed and what the user can do about it, never
 * what a broker said to cause it. The broker's own words stay in the audit row
 * and the admin diagnostics, where somebody has read that broker's error table.
 */
const CHANGE_REASON_DETAIL = {
  [FEED_CHANGE_REASON.ENTITLEMENT_REFUSED]:
    "Your market-data connection needs attention — your account is not cleared for this data.",
  [FEED_CHANGE_REASON.SESSION_EXPIRED]:
    "Your market-data access has expired. Reconnect your account to resume the live feed.",
  [FEED_CHANGE_REASON.FEED_DISCONNECTED]:
    "Your market-data account is no longer connected, so streaming data has stopped.",
};

/**
 * More specific than the state alone, where the platform genuinely knows more.
 * Deliberately partial: `no_providers_registered` is a startup bug and
 * `all_providers_down` is an outage, and neither is a sentence a user can act
 * on differently, so both fall back to the state's own line.
 */
const UNAVAILABLE_REASON_DETAIL = {
  [FEED_UNAVAILABLE_REASON.NOT_ENTITLED]:
    "Your plan does not include a live market-data feed.",
  [FEED_UNAVAILABLE_REASON.CAPABILITY_UNSUPPORTED]:
    "This kind of market data is not available on your current feed.",
};

const EMPTY_VIEW = {
  state: FEED_STATE.UNAVAILABLE,
  label: LABELS[FEED_STATE.UNAVAILABLE],
  detail: STATE_DETAIL[FEED_STATE.UNAVAILABLE],
  live: false,
  tone: "loss",
};

/**
 * Render-ready view of a projected feed state.
 *
 * `live` is the one field a component should branch on to decide whether it may
 * present streaming presentation, and it is true in exactly one case: the state
 * is `available` AND the tier is `streaming`. `recovering` cannot reach it —
 * not because a branch excludes it, but because it has no tier to qualify with.
 */
export function describeFeed(feed) {
  if (!feed || typeof feed !== "object") return EMPTY_VIEW;
  const state = oneOf(STATES, feed.state) || FEED_STATE.UNAVAILABLE;

  if (state === FEED_STATE.AVAILABLE) {
    const key = feed.tier && TIERS.has(feed.tier) ? feed.tier : "unknown";
    return {
      state,
      label: LABELS[state][key],
      // No change-reason sentence here on purpose: every reason in that map
      // explains why a feed STOPPED, and pairing "your access has expired" with
      // a live badge would be the contradiction this sprint exists to remove.
      detail: STATE_DETAIL[state][key],
      live: key === FEED_TIER.STREAMING,
      tone: key === FEED_TIER.STREAMING ? "profit" : "neutral",
    };
  }

  return {
    state,
    label: LABELS[state],
    detail:
      CHANGE_REASON_DETAIL[feed.changeReason] ||
      UNAVAILABLE_REASON_DETAIL[feed.reason] ||
      STATE_DETAIL[state],
    live: false,
    tone: state === FEED_STATE.RECOVERING ? "warn" : "loss",
  };
}

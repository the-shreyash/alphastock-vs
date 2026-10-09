/**
 * The one place the UI decides what an AI artifact's status actually is (D6.9).
 *
 * WHY THIS IS A MODULE AND NOT A FEW TERNARIES IN THE PAGE
 * --------------------------------------------------------
 * Before D6.9 four surfaces — the Morning Report page, the dashboard's morning
 * card, the AI Picks page and the model-status pill — each inferred AI state
 * from whatever data they happened to hold, and each inferred it differently.
 * The page read `report.available` and concluded "an AI wrote this"; the pill
 * read an environment-variable check and concluded "AI ready"; neither asked
 * whether a model had actually answered. On a deployment whose API key had no
 * credit, all four rendered confidently, and all four were wrong.
 *
 * Every one of those questions is answered here, from the backend's provenance
 * record, and nowhere else.
 *
 * THE FOUR FACTS THAT MUST NOT BE COLLAPSED
 * ------------------------------------------
 *   the report exists          →  state !== NO_REPORT
 *   generation succeeded       →  provenance.status === "completed"
 *   a model wrote the briefing →  provenance.is_ai_generated
 *   AI works RIGHT NOW         →  the /api/ai/status payload, a separate input
 *
 * The fourth is deliberately a separate argument and never derived from the
 * first three. An offline provider does not retroactively make the 08:30 report
 * fake, and an online provider is not evidence that today's report generated.
 *
 * FRESHNESS IS RECOMPUTED HERE, BUT THE POLICY IS NOT DEFINED HERE
 * ----------------------------------------------------------------
 * The backend ships `freshness_policy` (its own thresholds) alongside the
 * record, and this module applies them to `completed_at`. That lets the age tick
 * upward between refetches without the browser inventing a second, divergent
 * definition of "stale" — the numbers still live in one place,
 * `backend/services/ai_provenance.py`.
 */

/** Report states the UI renders. Exhaustive over what the backend can return. */
export const ReportState = {
  NO_REPORT: "NO_REPORT",
  GENERATING: "GENERATING",
  REPORT_FRESH: "REPORT_FRESH",
  REPORT_STALE: "REPORT_STALE",
  REPORT_VERY_STALE: "REPORT_VERY_STALE",
  /** Completed, but the completion instant was never recorded (pre-D6.9 rows). */
  REPORT_UNKNOWN_AGE: "REPORT_UNKNOWN_AGE",
  GENERATION_FAILED: "GENERATION_FAILED",
  AI_UNAVAILABLE_WITH_EXISTING_REPORT: "AI_UNAVAILABLE_WITH_EXISTING_REPORT",
  AI_UNAVAILABLE_WITHOUT_REPORT: "AI_UNAVAILABLE_WITHOUT_REPORT",
};

export const Freshness = {
  FRESH: "fresh",
  STALE: "stale",
  VERY_STALE: "very_stale",
  UNKNOWN: "unknown",
  UNAVAILABLE: "unavailable",
};

const DEFAULT_POLICY = { fresh_max_seconds: 3 * 3600, stale_max_seconds: 12 * 3600 };

/**
 * Parse a stored ISO instant, or return null.
 *
 * Returns null for anything unparseable rather than a guess — the browser
 * mirror of `ai_provenance.parse_instant`, and for the same reason: an invented
 * timestamp becomes an invented age, which becomes an invented freshness badge.
 */
export function parseInstant(value) {
  if (!value || typeof value !== "string") return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

/**
 * Seconds since generation *succeeded*, or null when that is not knowable.
 *
 * Measured from `completed_at` and from nothing else. Never from the page load,
 * never from `generated_at` (which is when the attempt *began*), and never from
 * a database modification time.
 */
export function ageSeconds(provenance, now = Date.now()) {
  if (!provenance || provenance.status !== "completed") return null;
  const completedAt = parseInstant(provenance.completed_at);
  if (completedAt === null) return null;
  return Math.max(0, (now - completedAt) / 1000);
}

/**
 * Apply the backend's own thresholds to the live age.
 *
 * UNKNOWN AND UNAVAILABLE ARE NOT THE SAME ANSWER, and the difference is the
 * whole legacy-data rule — the mirror of `ai_provenance.freshness`. A record
 * that was never written (absent here, `status: "unknown"` once the backend has
 * described it) cannot testify that generation did not succeed; only that
 * nobody recorded whether it did. UNAVAILABLE requires a record that explicitly
 * says the generation failed, is still running, or could not be attempted.
 * Collapsing the two reports every pre-D6.9 document as a failed generation.
 */
export function freshnessOf(provenance, now = Date.now()) {
  if (!provenance) return Freshness.UNKNOWN;
  if (provenance.status === "unknown" || provenance.known === false) return Freshness.UNKNOWN;
  if (provenance.status !== "completed") return Freshness.UNAVAILABLE;
  const age = ageSeconds(provenance, now);
  if (age === null) return Freshness.UNKNOWN;
  const policy = provenance.freshness_policy || DEFAULT_POLICY;
  if (age <= policy.fresh_max_seconds) return Freshness.FRESH;
  if (age <= policy.stale_max_seconds) return Freshness.STALE;
  return Freshness.VERY_STALE;
}

const FRESHNESS_STATE = {
  [Freshness.FRESH]: ReportState.REPORT_FRESH,
  [Freshness.STALE]: ReportState.REPORT_STALE,
  [Freshness.VERY_STALE]: ReportState.REPORT_VERY_STALE,
  [Freshness.UNKNOWN]: ReportState.REPORT_UNKNOWN_AGE,
};

/**
 * The normalized view every Morning Report surface renders from.
 *
 * @param {object|null} report      the `/analysis/reports/morning` payload
 * @param {object} opts
 * @param {boolean} opts.generating a generation is in flight in this tab
 * @param {object|null} opts.aiStatus the `/ai/status` payload — CURRENT health,
 *        which is an independent axis and never used to infer anything about
 *        the report's history
 * @param {number} opts.now         injectable clock, so freshness is testable
 */
export function deriveReportStatus(report, { generating = false, aiStatus = null, now = Date.now() } = {}) {
  const provenance = report?.provenance || null;
  // `online` is absent until /ai/status resolves. Unknown is not "offline":
  // rendering an outage because a status request has not come back yet would
  // be the same class of error as rendering "AI ready" for a dead key.
  const aiOnline = aiStatus ? Boolean(aiStatus.online) : null;
  const hasReport = Boolean(report && report.available);

  const freshness = freshnessOf(provenance, now);
  const age = ageSeconds(provenance, now);

  let state;
  if (generating) {
    state = ReportState.GENERATING;
  } else if (provenance?.status === "generating") {
    state = ReportState.GENERATING;
  } else if (hasReport) {
    // A report that exists is shown at every age. AI being offline right now
    // changes the banner, never whether the report is rendered — the 08:30
    // analysis is a historical fact and does not stop being one at 14:00.
    state = aiOnline === false
      ? ReportState.AI_UNAVAILABLE_WITH_EXISTING_REPORT
      : (FRESHNESS_STATE[freshness] || ReportState.REPORT_UNKNOWN_AGE);
  } else if (provenance?.status === "failed") {
    state = ReportState.GENERATION_FAILED;
  } else if (aiOnline === false) {
    state = ReportState.AI_UNAVAILABLE_WITHOUT_REPORT;
  } else {
    // Includes `status: "unavailable"` — the inputs were unreachable — and the
    // plain absence of any record for today.
    state = ReportState.NO_REPORT;
  }

  return {
    state,
    hasReport,
    /** Generation ran to completion. NOT the same as "a model wrote it". */
    generationSucceeded: provenance?.status === "completed",
    /** The ONLY licence to label this report's briefing AI-generated. */
    isAIGenerated: Boolean(provenance?.is_ai_generated),
    /** Provenance was recorded at all. False for every pre-D6.9 document. */
    provenanceKnown: Boolean(provenance?.known),
    freshness,
    ageSeconds: age,
    /** When generation SUCCEEDED. Null when it did not, or was never recorded. */
    completedAt: provenance?.completed_at || null,
    /** When generation BEGAN. Not a freshness clock. */
    generatedAt: provenance?.generated_at || null,
    aiProvider: provenance?.ai?.provider || null,
    aiModel: provenance?.ai?.model || null,
    marketDataObservedAt: provenance?.market_data?.observed_at || null,
    marketDataTier: provenance?.market_data?.source_tier || null,
    error: provenance?.error || null,
    /** Current provider health. Tri-state: true / false / null = not yet known. */
    aiOnline,
    /** Deterministic scan, per the backend's stamp — never inferred from shape. */
    picksAreDeterministic: report?.top_picks_source === "deterministic_technical_scan",
  };
}

/**
 * "6h 10m" — a compact, honest age.
 *
 * Returns null rather than "0m" or "just now" when the age is unknown, so a
 * caller cannot accidentally render a missing timestamp as a fresh one.
 */
export function formatAge(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return null;
  const total = Math.floor(seconds);
  if (total < 60) return "less than a minute";
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
  if (hours > 0) return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  return `${minutes}m`;
}

/**
 * "today at 08:30" — the generation instant, in the reader's locale.
 *
 * Returns null when the instant is unknown. Callers MUST render that null as
 * "generation time not recorded" and never substitute the current time: a page
 * that falls back to `Date.now()` presents a legacy row as generated seconds
 * ago, which is exactly the fabrication the legacy-data rule forbids.
 */
export function formatInstant(iso) {
  const ms = parseInstant(iso);
  if (ms === null) return null;
  return new Date(ms).toLocaleString(undefined, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

/**
 * The frontend's only interpretation of an AI artifact's provenance (D6.9).
 *
 * Production failures these catch: a report of unknown age rendered as
 * generated seconds ago; a deterministic briefing labelled "AI Market
 * Briefing"; a stale analysis rendered as live; and — the one that shipped —
 * an offline provider read as evidence that the morning's report was fake, or
 * an online provider read as evidence that it exists.
 */
import {
  ReportState,
  Freshness,
  deriveReportStatus,
  freshnessOf,
  ageSeconds,
  formatAge,
  formatInstant,
  parseInstant,
} from "../reportProvenance";

const NOW = Date.parse("2026-09-11T09:00:00Z");
const POLICY = { fresh_max_seconds: 3 * 3600, stale_max_seconds: 12 * 3600 };

/** A backend `describe()` payload. */
const prov = (over = {}) => ({
  known: true,
  status: "completed",
  generated_at: "2026-09-11T08:29:00Z",
  completed_at: "2026-09-11T08:30:00Z",
  ai: { outcome: "succeeded", provider: "claude", model: "claude-3-haiku-20240307" },
  market_data: { source_tier: "delayed", observed_at: "2026-09-11T08:28:00Z" },
  is_ai_generated: true,
  freshness_policy: POLICY,
  error: null,
  ...over,
});

const report = (over = {}, provOver = {}) => ({
  available: true,
  date: "2026-09-11",
  ai_briefing: "text",
  top_picks_source: "deterministic_technical_scan",
  provenance: prov(provOver),
  ...over,
});

describe("freshness comes from completed_at and from nothing else", () => {
  it("is fresh inside the policy's fresh window", () => {
    expect(freshnessOf(prov(), NOW)).toBe(Freshness.FRESH);
  });

  it("becomes stale past the fresh threshold", () => {
    const later = Date.parse("2026-09-11T14:00:00Z"); // 5h30m after 08:30
    expect(freshnessOf(prov(), later)).toBe(Freshness.STALE);
  });

  it("becomes very stale past the stale threshold", () => {
    const nextDay = Date.parse("2026-09-12T08:00:00Z");
    expect(freshnessOf(prov(), nextDay)).toBe(Freshness.VERY_STALE);
  });

  it("ignores generated_at, which is when the attempt BEGAN", () => {
    // Started 20 hours ago, succeeded a minute ago. Reading the wrong field
    // would report a fresh analysis as a previous session's.
    const p = prov({
      generated_at: "2026-09-10T13:00:00Z",
      completed_at: "2026-09-11T08:59:00Z",
    });
    expect(freshnessOf(p, NOW)).toBe(Freshness.FRESH);
  });

  it("never derives an age from the page-load time", () => {
    expect(ageSeconds(prov({ completed_at: null }), NOW)).toBeNull();
    expect(ageSeconds(prov({ completed_at: "not-a-date" }), NOW)).toBeNull();
    expect(parseInstant("not-a-date")).toBeNull();
  });

  it("applies the backend's thresholds rather than its own", () => {
    // A backend that widened its fresh window must widen the browser's too.
    const wide = prov({ freshness_policy: { fresh_max_seconds: 86400, stale_max_seconds: 172800 } });
    const later = Date.parse("2026-09-11T20:00:00Z");

    expect(freshnessOf(prov(), later)).toBe(Freshness.STALE);
    expect(freshnessOf(wide, later)).toBe(Freshness.FRESH);
  });
});

describe("the legacy-data rule", () => {
  it("reports unknown age for a record that was never written", () => {
    const legacy = report({ provenance: undefined });
    const status = deriveReportStatus(legacy, { now: NOW });

    expect(status.freshness).toBe(Freshness.UNKNOWN);
    expect(status.state).toBe(ReportState.REPORT_UNKNOWN_AGE);
    expect(status.completedAt).toBeNull();
    expect(status.ageSeconds).toBeNull();
    expect(status.provenanceKnown).toBe(false);
  });

  it("never labels a report with no provenance as AI-generated", () => {
    expect(deriveReportStatus(report({ provenance: undefined }), { now: NOW }).isAIGenerated).toBe(false);
  });

  it("renders an unknown instant as null, never as a formatted 'now'", () => {
    expect(formatInstant(null)).toBeNull();
    expect(formatInstant("garbage")).toBeNull();
    expect(formatAge(null)).toBeNull();
    expect(formatAge(undefined)).toBeNull();
  });

  it("keeps a missing record distinct from a failed generation", () => {
    // An absent record cannot testify that generation failed.
    expect(freshnessOf(null, NOW)).toBe(Freshness.UNKNOWN);
    expect(freshnessOf(prov({ status: "failed" }), NOW)).toBe(Freshness.UNAVAILABLE);
  });

  it("reads the backend's OWN legacy shape as unknown, not unavailable", () => {
    // `ai_provenance.describe(None)` — the exact object the API sends for a
    // pre-D6.9 document. Testing only the `null` case above hid this: the
    // browser read `status: "unknown"` as "not completed" and downgraded a
    // legacy report to UNAVAILABLE, disagreeing with the very payload it was
    // handed. Caught by the page test, locked here.
    const legacyView = {
      known: false,
      status: "unknown",
      generated_at: null,
      completed_at: null,
      ai: { outcome: "not_applicable", provider: null, model: null },
      market_data: { source_tier: null, observed_at: null },
      is_ai_generated: false,
      freshness: "unknown",
      age_seconds: null,
      freshness_policy: POLICY,
      error: null,
    };

    expect(freshnessOf(legacyView, NOW)).toBe(Freshness.UNKNOWN);
    // And the browser's recomputation agrees with the value the backend shipped.
    expect(freshnessOf(legacyView, NOW)).toBe(legacyView.freshness);

    const status = deriveReportStatus(report({ provenance: legacyView }), { now: NOW });
    expect(status.state).toBe(ReportState.REPORT_UNKNOWN_AGE);
    expect(status.isAIGenerated).toBe(false);
  });
});

describe("current AI health and historical generation status are separate axes", () => {
  it("keeps a report visible and attributed when AI goes offline (SCENARIO A)", () => {
    const status = deriveReportStatus(report(), { aiStatus: { online: false }, now: NOW });

    expect(status.hasReport).toBe(true);
    expect(status.isAIGenerated).toBe(true);
    expect(status.aiProvider).toBe("claude");
    expect(status.state).toBe(ReportState.AI_UNAVAILABLE_WITH_EXISTING_REPORT);
  });

  it("does not imply a report exists just because AI is online (SCENARIO C)", () => {
    const status = deriveReportStatus(null, { aiStatus: { online: true }, now: NOW });

    expect(status.hasReport).toBe(false);
    expect(status.isAIGenerated).toBe(false);
    expect(status.state).toBe(ReportState.NO_REPORT);
  });

  it("says so plainly when AI is offline and nothing was generated (SCENARIO B)", () => {
    const status = deriveReportStatus(null, { aiStatus: { online: false }, now: NOW });

    expect(status.state).toBe(ReportState.AI_UNAVAILABLE_WITHOUT_REPORT);
    expect(status.isAIGenerated).toBe(false);
  });

  it("treats an unresolved AI status as unknown, not as an outage", () => {
    // Rendering an outage because a status request has not returned yet is the
    // same error as rendering "AI ready" for a dead key.
    const status = deriveReportStatus(report(), { aiStatus: null, now: NOW });

    expect(status.aiOnline).toBeNull();
    expect(status.state).toBe(ReportState.REPORT_FRESH);
  });
});

describe("generation status", () => {
  it("reports a failed generation as failed, not as a very stale report", () => {
    const failed = report({ available: false }, {
      status: "failed",
      completed_at: null,
      is_ai_generated: false,
      error: { code: "generation_error", message: "Report generation failed." },
    });
    const status = deriveReportStatus(failed, { now: NOW });

    expect(status.state).toBe(ReportState.GENERATION_FAILED);
    expect(status.generationSucceeded).toBe(false);
    expect(status.error.code).toBe("generation_error");
  });

  it("reports a run in flight as generating", () => {
    expect(deriveReportStatus(report(), { generating: true, now: NOW }).state)
      .toBe(ReportState.GENERATING);
    expect(deriveReportStatus(report({}, { status: "generating" }), { now: NOW }).state)
      .toBe(ReportState.GENERATING);
  });

  it("does not call a deterministic briefing AI-generated (SCENARIO E's cousin)", () => {
    const det = report({}, {
      ai: { outcome: "provider_error", provider: null, model: null },
      is_ai_generated: false,
    });
    const status = deriveReportStatus(det, { now: NOW });

    expect(status.generationSucceeded).toBe(true);
    expect(status.isAIGenerated).toBe(false);
    expect(status.aiProvider).toBeNull();
    expect(status.aiModel).toBeNull();
  });

  it("reads the picks' source from the backend's stamp, not from their shape", () => {
    expect(deriveReportStatus(report(), { now: NOW }).picksAreDeterministic).toBe(true);
    expect(deriveReportStatus(report({ top_picks_source: undefined }), { now: NOW })
      .picksAreDeterministic).toBe(false);
  });
});

describe("formatAge", () => {
  it.each([
    [30, "less than a minute"],
    [90, "1m"],
    [3600, "1h"],
    [22200, "6h 10m"],
    [90000, "1d 1h"],
  ])("renders %ss as %s", (seconds, expected) => {
    expect(formatAge(seconds)).toBe(expected);
  });
});

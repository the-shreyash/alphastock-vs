/**
 * Morning Report page — provenance and freshness rendering (D6.9).
 *
 * The screen this suite exists because of: on a deployment whose API key had no
 * billing credit, the page rendered the heading "AI Market Briefing" over the
 * text "AI services are currently offline or unavailable. Please check that
 * ANTHROPIC_API_KEY and GOOGLE_GEMINI_KEY are configured in your backend .env
 * file", under a header that formatted `Date.now()` as the generation time.
 *
 * Production failures these catch: a briefing no model wrote labelled as AI; a
 * deterministic technical scan presented as AI picks; a six-hour-old analysis
 * rendered as live; a legacy report shown as generated seconds ago; and an
 * empty result announced as "still generating".
 */
import { screen, waitFor } from "@testing-library/react";
import MorningReport from "../MorningReport";
import {
  renderWithProviders,
  installApiMock,
  stubRemainingWith,
  mockAuthenticatedUser,
  resetRealtimeStore,
  HTTP,
} from "../../test-utils";

let mock;

beforeEach(() => {
  mock = installApiMock();
  mockAuthenticatedUser(mock);
  resetRealtimeStore();
  jest.useFakeTimers({ doNotFake: ["queueMicrotask"] });
  // 14:00 UTC — five and a half hours after the 08:30 generation below.
  jest.setSystemTime(new Date("2026-09-11T14:00:00Z"));
});

afterEach(() => {
  jest.useRealTimers();
  mock.restore();
});

const AI_PROVENANCE = {
  known: true,
  status: "completed",
  generated_at: "2026-09-11T08:29:00Z",
  completed_at: "2026-09-11T08:30:00Z",
  ai: {
    outcome: "succeeded",
    provider: "claude",
    model: "claude-3-haiku-20240307",
    prompt_key: "morning_report",
    prompt_version: "1.0.0",
  },
  market_data: { source_tier: "delayed", observed_at: "2026-09-11T08:28:00Z" },
  is_ai_generated: true,
  age_seconds: 19800,
  freshness: "stale",
  freshness_policy: { fresh_max_seconds: 10800, stale_max_seconds: 43200 },
  error: null,
};

const DETERMINISTIC_PROVENANCE = {
  ...AI_PROVENANCE,
  ai: { outcome: "provider_error", provider: null, model: null,
        prompt_key: "morning_report", prompt_version: "1.0.0" },
  is_ai_generated: false,
};

const baseReport = (over = {}) => ({
  date: "2026-09-11",
  type: "morning",
  available: true,
  market_mood: "Cautious",
  mood_score: 0.2,
  nifty: { value: 24500, change_pct: 0.8 },
  banknifty: { value: 52000, change_pct: -0.7 },
  sensex: { value: 80500, change_pct: 0.3 },
  ai_briefing: "Nifty is holding 24,500 with breadth narrowing.",
  briefing_source: "ai",
  top_picks: [{ symbol: "RELIANCE", name: "Reliance", confidence: 82,
                reason: "Volume breakout", stop_loss: 2900, target: 3100, change_pct: 1.2 }],
  top_picks_source: "deterministic_technical_scan",
  key_risks: ["India VIX at 17.2 — elevated volatility"],
  provenance: AI_PROVENANCE,
  ...over,
});

/** Render the page with a given report payload and AI health. */
async function renderPage({ report = baseReport(), aiStatus = { online: true } } = {}) {
  mock.onGet(/\/analysis\/reports\/morning/).reply(HTTP.OK, report);
  mock.onGet(/\/ai\/status/).reply(HTTP.OK, aiStatus);
  stubRemainingWith(mock, {});
  renderWithProviders(<MorningReport />);
  await waitFor(() => expect(screen.queryByTestId("morning-report-page")
    || screen.queryByTestId("report-unavailable")).toBeInTheDocument());
}

describe("the briefing is labelled by what actually produced it", () => {
  it("says AI Market Briefing, with the model, when a model wrote it", async () => {
    await renderPage();

    expect(screen.getByTestId("briefing-heading")).toHaveTextContent("AI Market Briefing");
    expect(screen.getByTestId("market-briefing")).toHaveTextContent("claude-3-haiku-20240307");
  });

  it("drops the AI label when no model wrote it", async () => {
    await renderPage({
      report: baseReport({ briefing_source: "deterministic", provenance: DETERMINISTIC_PROVENANCE }),
    });

    const heading = screen.getByTestId("briefing-heading");
    expect(heading).toHaveTextContent("Market Briefing");
    expect(heading).not.toHaveTextContent("AI Market Briefing");
    expect(screen.getAllByTestId("briefing-attribution")[0]).toHaveTextContent(/not ai-generated/i);
  });
});

describe("freshness is stated, and the report stays visible at every age", () => {
  it("shows a stale warning over a report generated five hours ago", async () => {
    await renderPage();

    const banner = screen.getByTestId("report-provenance");
    expect(banner).toHaveAttribute("data-freshness", "stale");
    expect(screen.getByTestId("freshness-label")).toHaveTextContent(/stale/i);
    expect(banner).toHaveTextContent(/market conditions may have changed/i);
    // The report itself is still rendered — a stale analysis is not an error.
    expect(screen.getByTestId("morning-report-page")).toBeInTheDocument();
    expect(screen.getByText(/Nifty is holding 24,500/)).toBeInTheDocument();
  });

  it("shows the age alongside the generation instant", async () => {
    await renderPage();

    expect(screen.getByTestId("report-generated-at")).toHaveTextContent(/5h 30m ago/);
  });

  it("does not nag when the analysis is fresh", async () => {
    jest.setSystemTime(new Date("2026-09-11T09:00:00Z")); // 30 minutes old
    await renderPage({
      report: baseReport({ provenance: { ...AI_PROVENANCE, freshness: "fresh", age_seconds: 1800 } }),
    });

    expect(screen.getByTestId("report-provenance")).toHaveAttribute("data-freshness", "fresh");
    expect(screen.queryByTestId("freshness-label")).not.toBeInTheDocument();
  });

  it("marks a report from a previous session very stale", async () => {
    jest.setSystemTime(new Date("2026-09-12T09:00:00Z"));
    await renderPage();

    expect(screen.getByTestId("report-provenance")).toHaveAttribute("data-freshness", "very_stale");
    expect(screen.getByTestId("freshness-label")).toHaveTextContent(/very stale/i);
  });
});

describe("the legacy-data rule", () => {
  it("says the generation time is not recorded rather than inventing one", async () => {
    // The page previously read `new Date(report.generated_at || Date.now())`,
    // which rendered the page-load time for exactly this document.
    await renderPage({
      report: baseReport({
        provenance: { known: false, status: "unknown", generated_at: null, completed_at: null,
                      ai: { outcome: "not_applicable", provider: null, model: null },
                      market_data: { source_tier: null, observed_at: null },
                      is_ai_generated: false, freshness: "unknown", age_seconds: null,
                      freshness_policy: { fresh_max_seconds: 10800, stale_max_seconds: 43200 },
                      error: null },
      }),
    });

    expect(screen.getByTestId("report-generated-at")).toHaveTextContent("Generation time not recorded");
    expect(screen.getByTestId("report-provenance")).toHaveAttribute("data-freshness", "unknown");
    expect(screen.getByTestId("briefing-heading")).not.toHaveTextContent("AI Market Briefing");
    // Nothing on the page may render "14:00", the render time, as provenance.
    expect(screen.getByTestId("report-provenance")).not.toHaveTextContent(/19:30|14:00/);
  });
});

describe("current AI status and report status do not contradict each other", () => {
  it("keeps the 08:30 report and its attribution when AI is offline now", async () => {
    await renderPage({ aiStatus: { online: false } });

    expect(screen.getByTestId("ai-service-status")).toHaveTextContent(/AI service offline/i);
    // Both statements stand together: the service is down, the report is real.
    expect(screen.getByTestId("briefing-heading")).toHaveTextContent("AI Market Briefing");
    expect(screen.getByTestId("report-generated-at")).toHaveTextContent(/Generated/);
    expect(screen.getByText(/Nifty is holding 24,500/)).toBeInTheDocument();
  });

  it("does not claim a report exists when AI is online and none generated", async () => {
    await renderPage({
      report: { available: false, date: "2026-09-11", note: "No report yet." },
      aiStatus: { online: true },
    });

    expect(screen.getByTestId("report-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("morning-report-page")).not.toBeInTheDocument();
    expect(screen.queryByText(/AI Market Briefing/)).not.toBeInTheDocument();
  });

  it("states both facts when AI is offline and nothing generated", async () => {
    await renderPage({
      report: { available: false, date: "2026-09-11" },
      aiStatus: { online: false },
    });

    const empty = screen.getByTestId("report-unavailable");
    expect(empty).toHaveAttribute("data-report-state", "AI_UNAVAILABLE_WITHOUT_REPORT");
    expect(empty).toHaveTextContent(/no report was successfully generated/i);
    expect(screen.getByTestId("ai-offline-note")).toBeInTheDocument();
  });

  it("renders a failed generation as failed, with no fabricated picks", async () => {
    await renderPage({
      report: {
        available: false, date: "2026-09-11",
        provenance: { ...DETERMINISTIC_PROVENANCE, status: "failed", completed_at: null,
                      freshness: "unavailable",
                      error: { code: "generation_error",
                               message: "Report generation failed. No analysis was produced for this date." } },
      },
      aiStatus: { online: true },
    });

    const empty = screen.getByTestId("report-unavailable");
    expect(empty).toHaveAttribute("data-report-state", "GENERATION_FAILED");
    expect(screen.getByTestId("report-unavailable-note"))
      .toHaveTextContent(/no analysis was produced/i);
    expect(screen.queryByText(/RELIANCE/)).not.toBeInTheDocument();
  });
});

describe("Top Picks are not presented as AI output", () => {
  it("labels them a deterministic scan and ties them to this generation", async () => {
    await renderPage();

    const attribution = screen.getByTestId("picks-attribution");
    expect(attribution).toHaveTextContent(/deterministic technical scan — not ai-generated/i);
    expect(attribution).toHaveTextContent(/selected/i);
  });

  it("does not claim picks are still generating when the run finished", async () => {
    await renderPage({ report: baseReport({ top_picks: [] }) });

    expect(screen.getByTestId("picks-empty"))
      .toHaveTextContent(/no setups met the scan's criteria/i);
    expect(screen.queryByText(/check back in a moment/i)).not.toBeInTheDocument();
  });

  it("does not send the reader to 'AI stock picks'", async () => {
    await renderPage();

    expect(screen.getByRole("link", { name: /view full stock picks/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /AI stock picks/i })).not.toBeInTheDocument();
  });
});

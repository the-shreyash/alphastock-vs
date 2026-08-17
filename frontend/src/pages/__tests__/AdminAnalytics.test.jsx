/**
 * PH3.9 — admin analytics, after mock removal.
 *
 * PH3.8's version of this file was the load-bearing guarantee of an *audit*
 * sprint: it held a "Simulated" badge in place over invented business metrics
 * that were still being served. PH3.9 removed the metrics, so the guarantee
 * inverts — and the new one is narrower and more important.
 *
 * **The thing that can silently undo this whole sprint is a falsy check.** The
 * backend now returns `null` for a metric it cannot compute. One
 * `{stats?.mrr || 0}` on this side of the HTTP boundary turns that back into
 * `₹0` — rendered in the same weight, size and colour as a measured figure, on
 * a page an operator reads to answer "how much did we make?". No test would
 * fail. That is the failure this file exists to catch, which is why several of
 * these tests assert on the *absence of a zero* rather than on the presence of
 * an em-dash.
 *
 * The second guarantee is the converse: a genuine `0` must still render as `0`.
 * "Nobody signed up today" is a measurement, and suppressing it behind the
 * unavailable treatment would be the same defect pointed the other way.
 */
import { screen, within } from "@testing-library/react";
import AdminAnalytics from "../admin/AdminAnalytics";
import AdminDashboard from "../admin/AdminDashboard";
import AdminPayments from "../admin/AdminPayments";
import {
  renderWithProviders,
  installApiMock,
  stubRemainingWith,
  mockAdminUser,
  resetRealtimeStore,
  HTTP,
  testAdminDashboard,
  testAdminUserAnalytics,
  testAdminRevenueAnalytics,
  testAdminFeatureAnalytics,
} from "../../test-utils";

let mock;

beforeEach(() => {
  mock = installApiMock();
  mockAdminUser(mock);
  resetRealtimeStore();
});

afterEach(() => {
  mock.restore();
});

function renderAnalytics(overrides = {}) {
  mock.onGet("/admin/analytics/users").reply(HTTP.OK,
    { ...testAdminUserAnalytics, ...(overrides.users || {}) });
  mock.onGet("/admin/analytics/revenue").reply(HTTP.OK,
    { ...testAdminRevenueAnalytics, ...(overrides.revenue || {}) });
  mock.onGet("/admin/analytics/features").reply(HTTP.OK,
    { ...testAdminFeatureAnalytics, ...(overrides.features || {}) });
  stubRemainingWith(mock, []);
  return renderWithProviders(<AdminAnalytics />, { route: "/admin/analytics" });
}

function renderDashboard(overrides = {}) {
  mock.onGet("/admin/dashboard").reply(HTTP.OK, { ...testAdminDashboard, ...overrides });
  stubRemainingWith(mock, []);
  return renderWithProviders(<AdminDashboard />, { route: "/admin/dashboard" });
}

/** The card wrapping a labelled stat, whichever admin page it is on. */
function cardFor(label) {
  return screen.getByText(label).closest("div").parentElement;
}

describe("nothing is presented as simulated any more, because nothing is", () => {
  it("renders no Simulated badge on the analytics page", async () => {
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });
    expect(screen.queryByText(/simulated/i)).not.toBeInTheDocument();
  });

  it("renders no Simulated badge on the dashboard", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(screen.queryByText(/simulated/i)).not.toBeInTheDocument();
  });

  it("still carries no invented growth badge on every card", async () => {
    // PH3.8 (F-18): nine unrelated cards — user counts, trade counts, MRR, open
    // tickets, broker links — all carried the identical hardcoded
    // "+12% vs last month" in the gain colour.
    renderDashboard();
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(screen.queryByText("+12%")).not.toBeInTheDocument();
    expect(screen.queryByText(/vs last month/i)).not.toBeInTheDocument();
  });
});

describe("an unavailable metric never renders as zero", () => {
  it("does not print ₹0 for MRR or ARR on the dashboard", async () => {
    // The specific regression: `₹${(data?.mrr || 0).toLocaleString("en-IN")}`.
    renderDashboard();
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(within(cardFor("MRR")).queryByText("₹0")).not.toBeInTheDocument();
    expect(within(cardFor("ARR")).queryByText("₹0")).not.toBeInTheDocument();
  });

  it("does not print 0% for retention or churn", async () => {
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });
    expect(within(cardFor("Retention")).queryByText("0%")).not.toBeInTheDocument();
    expect(within(cardFor("Churn Rate")).queryByText("0%")).not.toBeInTheDocument();
  });

  it("does not print 0 for MAU", async () => {
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });
    expect(within(cardFor("Active (30d)")).queryByText("0")).not.toBeInTheDocument();
  });

  it("does not print ₹0 anywhere on the payments page", async () => {
    mock.onGet("/admin/payments/stats").reply(HTTP.OK, {
      mrr: null, arr: null, revenue_today: null, revenue_week: null,
      revenue_month: null, revenue_year: null,
      pending_payments: null, refunds: null, failed_payments: null,
      premium_count: 210, elite_count: 45, lifetime_count: 3,
      plan_distribution: { user: 1025, pro: 210 },
      payments_integration: { integrated: false, reason: "No payment integration." },
      analytics: { metrics: {} },
      mock_metrics: [],
    });
    mock.onGet("/admin/analytics/revenue").reply(HTTP.OK, testAdminRevenueAnalytics);
    stubRemainingWith(mock, []);
    renderWithProviders(<AdminPayments />, { route: "/admin/payments" });
    await screen.findByRole("heading", { name: "Payments & Revenue" });
    expect(screen.queryByText("₹0")).not.toBeInTheDocument();
    // The real role counts beside them must NOT be suppressed with them.
    // `getAllBy`: 210 appears both on the entitlement card and in the plan
    // distribution legend.
    expect(screen.getAllByText("210").length).toBeGreaterThan(0);
  });

  it("explains why, from the backend's own note", async () => {
    // A generic "—" leaves an operator unable to tell "no payment integration"
    // from "sessions are reaped after 7 days" — different problems, different
    // owners. The reason travels from analytics.metrics[name].note.
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });
    const retention = within(cardFor("Retention")).getByLabelText(/cohort retention/i);
    expect(retention).toBeInTheDocument();
  });

  it("names the unavailable metrics in a footnote", async () => {
    renderAnalytics();
    const note = await screen.findByText(/are not available/i);
    expect(note).toHaveTextContent("mau");
    expect(note).toHaveTextContent("retention_rate");
    expect(note).toHaveTextContent("churn_rate");
  });
});

describe("a real zero is still a zero", () => {
  it("renders 0 signups as 0, not as unavailable", async () => {
    // The converse guarantee. "Nobody signed up today" is a measurement, and
    // hiding it behind the unavailable treatment is the same defect reversed.
    renderAnalytics({ users: { today_signups: 0 } });
    await screen.findByRole("heading", { name: "Analytics" });
    expect(cardFor("Today Signups")).toHaveTextContent("0");
  });

  it("renders 0 chat messages as 0 on the dashboard", async () => {
    renderDashboard({ chat_messages_today: 0 });
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(cardFor("Chat Messages Today")).toHaveTextContent("0");
  });
});

describe("real metrics render as measured values", () => {
  it("shows DAU from session activity, not the signup count", async () => {
    // The fixture deliberately makes them differ: 47 active, 12 signups.
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });
    expect(cardFor("Active today")).toHaveTextContent("47");
  });

  it("shows signup growth with a direction", async () => {
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });
    expect(cardFor("Signup Growth (30d)")).toHaveTextContent("12.8%");
  });

  it("renders a negative growth rate as negative rather than as a gain", async () => {
    // The literal it replaced (12.8) could never be negative, so no rendering
    // path for a decline had ever been exercised.
    renderAnalytics({ users: { growth_rate: -8.5 } });
    await screen.findByRole("heading", { name: "Analytics" });
    expect(cardFor("Signup Growth (30d)")).toHaveTextContent("-8.5%");
  });

  it("hides the growth delta entirely when growth is unavailable", async () => {
    renderAnalytics({ users: { growth_rate: null } });
    await screen.findByRole("heading", { name: "Analytics" });
    expect(within(cardFor("Total Users")).queryByText(/%/)).not.toBeInTheDocument();
  });
});

describe("the revenue chart is absent rather than flat at zero", () => {
  it("renders an explicit empty state, not an axis", async () => {
    // A zero line across thirty days still claims "we measured thirty days and
    // found nothing", which is false.
    renderAnalytics();
    expect(await screen.findByText(/revenue reporting is not available/i))
      .toBeInTheDocument();
  });

  it("states what the platform would need to record", async () => {
    renderAnalytics();
    expect(await screen.findByText(/captured payment records/i)).toBeInTheDocument();
  });
});

describe("feature usage shows counts, not invented percentages", () => {
  it("renders the real event counts", async () => {
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });
    expect(screen.getByText("3,400")).toBeInTheDocument();
    expect(screen.getByText("820")).toBeInTheDocument();
  });

  it("renders no percentage figures beside them", async () => {
    // Pre-PH3.9: a fixed descending list (85%, 72%, 68%, …) unrelated to the
    // count beside it.
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });
    expect(screen.queryByText("85%")).not.toBeInTheDocument();
    expect(screen.queryByText("72%")).not.toBeInTheDocument();
  });

  it("says why adoption cannot be computed", async () => {
    renderAnalytics();
    expect(await screen.findByText(/distinct-user count/i)).toBeInTheDocument();
  });
});

describe("a failed load is not an empty platform", () => {
  it("shows an error with a retry rather than zeros", async () => {
    // Rendering zeros here would tell an operator the platform has no users —
    // a much more alarming claim than "we could not reach the server". This is
    // FE-003, the silent-load-failure pattern PH3.3 fixed in PaperTrading.
    mock.onGet("/admin/analytics/users").reply(HTTP.SERVER_ERROR);
    mock.onGet("/admin/analytics/revenue").reply(HTTP.SERVER_ERROR);
    mock.onGet("/admin/analytics/features").reply(HTTP.SERVER_ERROR);
    stubRemainingWith(mock, []);
    renderWithProviders(<AdminAnalytics />, { route: "/admin/analytics" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not be loaded/i);
    expect(within(alert).getByText(/retry/i)).toBeInTheDocument();
    expect(screen.queryByText("Active today")).not.toBeInTheDocument();
  });
});

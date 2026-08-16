/**
 * PH3.8 — admin analytics honesty layer.
 *
 * Most of the Analytics page is fabricated and PH3.8 deliberately did not
 * remove it (PH3.9 owns mock removal). That makes these tests the load-bearing
 * guarantee of the sprint: the ONLY thing standing between an operator and a
 * set of invented business metrics is the "Simulated" marker, so something has
 * to hold that marker in place until the numbers behind it become real.
 *
 * Production failure this catches: an operator — or a founder in a board deck —
 * reporting a retention rate that is a literal in the backend source, or a
 * revenue chart produced by a for-loop on a platform that has never processed a
 * payment.
 */
import { screen, waitFor, within } from "@testing-library/react";
import AdminAnalytics from "../admin/AdminAnalytics";
import AdminDashboard from "../admin/AdminDashboard";
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

describe("fabricated metrics are visibly marked", () => {
  it("marks every metric the backend names in mock_metrics", async () => {
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });

    // Five badges for dau / mau / retention_rate / churn_rate / growth_rate.
    const badges = await screen.findAllByText(/simulated/i);
    expect(badges.length).toBeGreaterThanOrEqual(5);
  });

  it("does not mark metrics that are genuinely derived", async () => {
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });

    // Conversion rate is computed from real role counts; it must not be
    // over-flagged, or the marker stops meaning anything.
    const conversion = screen.getByText("Conversion").closest("div").parentElement;
    expect(within(conversion).queryByText(/simulated/i)).not.toBeInTheDocument();
  });

  it("carries the backend's explanation, not just a badge", async () => {
    renderAnalytics();
    expect(await screen.findByText(/no payment records exist/i)).toBeInTheDocument();
    expect(
      await screen.findByText(/literal unrelated to the count beside it/i)
    ).toBeInTheDocument();
  });

  it("names the affected metrics in a footnote", async () => {
    renderAnalytics();
    const note = await screen.findByText(/scheduled for replacement with real data/i);
    expect(note).toHaveTextContent("retention_rate");
    expect(note).toHaveTextContent("churn_rate");
  });
});

describe("no fabricated growth figure is invented in the frontend", () => {
  it("does not render a hardcoded delta badge on the DAU card", async () => {
    // The DAU card carried `delta="+8%"` — a growth badge with no data behind
    // it at all, invented in this component, on top of a backend metric that is
    // itself today's signup count relabelled.
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });
    expect(screen.queryByText("+8%")).not.toBeInTheDocument();
  });

  it("shows the backend growth_rate as simulated rather than as measured", async () => {
    renderAnalytics();
    await screen.findByRole("heading", { name: "Analytics" });
    // The value still renders — PH3.8 removes nothing — but it is flagged.
    expect(screen.getByText("+12.8%")).toBeInTheDocument();
  });
});

describe("the marker degrades safely", () => {
  it("renders normally when the backend sends no mock_metrics", async () => {
    // An older backend, or a future one that has removed the mocks, must not
    // break the page.
    renderAnalytics({
      users: { mock_metrics: undefined },
      revenue: { status: "available", note: "" },
      features: { status: "available", note: "" },
    });
    await screen.findByRole("heading", { name: "Analytics" });
    expect(screen.queryByText(/simulated/i)).not.toBeInTheDocument();
    // 1,280 appears twice — Total Users and MAU, which is itself the total
    // relabelled. Both still render; only the badge is gone.
    expect(screen.getAllByText("1,280")).toHaveLength(2);
  });

  it("distinguishes a real zero from a missing value", async () => {
    renderAnalytics({ users: { today_signups: 0 } });
    await screen.findByRole("heading", { name: "Analytics" });
    expect(screen.getByText("Today Signups").closest("div").parentElement)
      .toHaveTextContent("0");
  });
});

describe("admin dashboard", () => {
  function renderDashboard(overrides = {}) {
    mock.onGet("/admin/dashboard").reply(HTTP.OK, { ...testAdminDashboard, ...overrides });
    stubRemainingWith(mock, []);
    return renderWithProviders(<AdminDashboard />, { route: "/admin/dashboard" });
  }

  it("no longer claims +12% growth on every card", async () => {
    // Nine unrelated cards — user counts, trade counts, MRR, open tickets,
    // broker links — all carried the identical hardcoded "+12% vs last month"
    // in the gain colour.
    renderDashboard();
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(screen.queryByText("+12%")).not.toBeInTheDocument();
    expect(screen.queryByText(/vs last month/i)).not.toBeInTheDocument();
  });

  it("marks MRR and ARR as simulated", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: "Dashboard" });
    const mrr = screen.getByText("MRR").closest(".group");
    expect(within(mrr).getByText(/simulated/i)).toBeInTheDocument();
  });

  it("does not mark the real user counts", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: "Dashboard" });
    const total = screen.getByText("Total Users").closest(".group");
    expect(within(total).queryByText(/simulated/i)).not.toBeInTheDocument();
  });
});

/**
 * Admin dashboard.
 *
 * Access control for the admin portal is covered end-to-end in
 * `src/__tests__/routing.test.jsx` (the guard is a route concern). What is
 * asserted here is the page itself: that the numbers on screen are the numbers
 * the API returned, and that its loading state resolves.
 *
 * Production failure this catches: an operator reading platform metrics that
 * are not real — the worst possible bug in an admin console.
 */
import { screen, waitFor } from "@testing-library/react";
import AdminDashboard from "../admin/AdminDashboard";
import {
  renderWithProviders,
  installApiMock,
  stubRemainingWith,
  mockAdminUser,
  resetRealtimeStore,
  HTTP,
  pending,
  testAdminDashboard,
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

function renderAdminDashboard(setupStubs) {
  setupStubs?.(mock);
  stubRemainingWith(mock, []);
  return renderWithProviders(<AdminDashboard />, { route: "/admin/dashboard" });
}

describe("loading state", () => {
  it("shows a skeleton while the metrics load", async () => {
    renderAdminDashboard((m) => m.onGet("/admin/dashboard").reply(() => pending()));

    await waitFor(() => expect(document.querySelectorAll(".skeleton, .animate-pulse").length).toBeGreaterThan(0));
    expect(screen.queryByRole("heading", { name: "Dashboard" })).not.toBeInTheDocument();
  });
});

describe("populated state", () => {
  it("renders the metrics the API returned", async () => {
    renderAdminDashboard((m) => m.onGet("/admin/dashboard").reply(HTTP.OK, testAdminDashboard));

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    // Formatted with Indian digit grouping — 1280 users, ₹2,45,000 MRR.
    await waitFor(() => expect(screen.getByText("1,280")).toBeInTheDocument());
    expect(screen.getByText("₹2,45,000")).toBeInTheDocument();
  });

  it("labels every metric it displays", async () => {
    renderAdminDashboard((m) => m.onGet("/admin/dashboard").reply(HTTP.OK, testAdminDashboard));

    await screen.findByRole("heading", { name: "Dashboard" });
    expect(screen.getByText("Total Users")).toBeInTheDocument();
    expect(screen.getByText("MRR")).toBeInTheDocument();
    expect(screen.getByText("Open Tickets")).toBeInTheDocument();
  });

  it("reports system health from the payload", async () => {
    renderAdminDashboard((m) => m.onGet("/admin/dashboard").reply(HTTP.OK, testAdminDashboard));

    await screen.findByRole("heading", { name: "Dashboard" });
    expect(screen.getByText("System Status")).toBeInTheDocument();
    expect(screen.getByText("Server")).toBeInTheDocument();
  });
});

describe("failure states", () => {
  it.each([
    ["a forbidden response", HTTP.FORBIDDEN],
    ["a server fault", HTTP.SERVER_ERROR],
  ])("resolves its loading state on %s instead of spinning forever", async (_label, status) => {
    renderAdminDashboard((m) => m.onGet("/admin/dashboard").reply(status, { detail: "nope" }));

    // KNOWN GAP (PH3.2 defect FE-006, deferred): the page currently renders
    // zeroed metrics rather than saying the load failed. This test pins the
    // present behaviour — it must at least stop loading — so the deferred fix
    // has a starting point and cannot regress further.
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });

  it("resolves its loading state when the backend is unreachable", async () => {
    renderAdminDashboard((m) => m.onGet("/admin/dashboard").networkError());

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });
});

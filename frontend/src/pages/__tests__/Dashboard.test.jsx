/**
 * Dashboard shell.
 *
 * The dashboard fans out to a dozen independent endpoints and each widget owns
 * its own loading/empty state. That design is deliberate — one dead endpoint
 * must degrade one card, never the page — so these tests pin the property that
 * makes it worth having: partial failure stays partial.
 *
 * Production failures these catch: a widget that renders its skeleton forever
 * because the `finally` clearing its loading flag was lost; an empty API
 * response rendering as a blank card with no explanation; and one failing
 * endpoint taking the whole dashboard down with it.
 */
import { screen, waitFor, within } from "@testing-library/react";
import Dashboard from "../Dashboard";
import {
  renderWithProviders,
  installApiMock,
  stubRemainingWith,
  mockAuthenticatedUser,
  resetRealtimeStore,
  HTTP,
  pending,
  testMarketOverview,
  testWatchlistItem,
  testNotification,
  testPortfolioSummary,
} from "../../test-utils";

let mock;

beforeEach(() => {
  mock = installApiMock();
  mockAuthenticatedUser(mock);
  resetRealtimeStore();
});

afterEach(() => {
  mock.restore();
});

/** Render the dashboard with every unstubbed endpoint resolving to empty. */
async function renderDashboard() {
  stubRemainingWith(mock, []);
  const utils = renderWithProviders(<Dashboard />, { route: "/dashboard" });
  await screen.findByTestId("quick-actions");
  return utils;
}

describe("shell", () => {
  it("renders the dashboard shell", async () => {
    await renderDashboard();

    expect(screen.getByTestId("quick-actions")).toBeInTheDocument();
    expect(screen.getByTestId("watchlist-widget")).toBeInTheDocument();
    expect(screen.getByTestId("notifications-widget")).toBeInTheDocument();
    expect(screen.getByTestId("top-picks-card")).toBeInTheDocument();
  });

  it("requests the core market data on mount", async () => {
    await renderDashboard();

    await waitFor(() => {
      const urls = mock.history.get.map((r) => r.url);
      expect(urls).toEqual(expect.arrayContaining(["/market/overview", "/market/sectors", "/ai-activity"]));
    });
  });
});

describe("loading states", () => {
  it("shows placeholders while widget data is still in flight", async () => {
    mock.onGet("/watchlist").reply(() => pending());
    mock.onGet("/notifications").reply(() => pending());
    stubRemainingWith(mock, []);

    renderWithProviders(<Dashboard />, { route: "/dashboard" });

    const watchlist = await screen.findByTestId("watchlist-widget");
    // Skeletons, not an "empty" message — the difference between "loading" and
    // "you have nothing" is the whole point of the distinction.
    expect(within(watchlist).queryByText(/no stocks in your watchlist/i)).not.toBeInTheDocument();
    expect(watchlist.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("leaves no widget stuck on its skeleton once its request settles", async () => {
    await renderDashboard();

    const watchlist = await screen.findByTestId("watchlist-widget");
    await waitFor(() => expect(watchlist.querySelectorAll(".skeleton")).toHaveLength(0));
  });
});

describe("empty states", () => {
  it("explains an empty watchlist and offers the way to fill it", async () => {
    mock.onGet("/watchlist").reply(HTTP.OK, []);
    await renderDashboard();

    const watchlist = screen.getByTestId("watchlist-widget");
    await waitFor(() =>
      expect(within(watchlist).getByText(/no stocks in your watchlist yet/i)).toBeInTheDocument(),
    );
    expect(within(watchlist).getByRole("link", { name: /add stocks/i })).toBeInTheDocument();
  });

  it("says so when there are no notifications", async () => {
    mock.onGet("/notifications").reply(HTTP.OK, []);
    await renderDashboard();

    const notifications = screen.getByTestId("notifications-widget");
    await waitFor(() => expect(within(notifications).getByText(/no new notifications/i)).toBeInTheDocument());
  });

  it("explains why AI picks are missing rather than showing a blank card", async () => {
    mock.onGet("/analysis/top-picks").reply(HTTP.OK, { picks: [] });
    await renderDashboard();

    const picks = screen.getByTestId("top-picks-card");
    await waitFor(() => expect(within(picks).getByText(/unavailable/i)).toBeInTheDocument());
  });
});

describe("populated states", () => {
  it("renders watchlist entries returned by the API", async () => {
    mock.onGet("/watchlist").reply(HTTP.OK, [testWatchlistItem]);
    await renderDashboard();

    const watchlist = screen.getByTestId("watchlist-widget");
    await waitFor(() => expect(within(watchlist).getByText(testWatchlistItem.symbol)).toBeInTheDocument());
    expect(within(watchlist).queryByText(/no stocks in your watchlist/i)).not.toBeInTheDocument();
  });

  it("renders notifications returned by the API", async () => {
    mock.onGet("/notifications").reply(HTTP.OK, [testNotification]);
    await renderDashboard();

    const panel = screen.getByTestId("notifications-widget");
    await waitFor(() => expect(within(panel).getByText(testNotification.title)).toBeInTheDocument());
  });

  it("renders the portfolio summary returned by the API", async () => {
    mock.onGet("/portfolio/summary").reply(HTTP.OK, testPortfolioSummary);
    await renderDashboard();

    const card = screen.getByTestId("portfolio-summary-card");
    // Formatted with Indian digit grouping, via utils/formatters.
    await waitFor(() => expect(card.textContent).toMatch(/1,52,340/));
  });

  it("renders market indices returned by the API", async () => {
    mock.onGet("/market/overview").reply(HTTP.OK, testMarketOverview);
    await renderDashboard();

    await waitFor(() => expect(screen.getByText(/NIFTY 50/i)).toBeInTheDocument());
  });
});

describe("resilience to failing endpoints", () => {
  it.each([
    ["401 unauthorized", HTTP.UNAUTHORIZED],
    ["403 forbidden", HTTP.FORBIDDEN],
    ["429 rate limited", HTTP.RATE_LIMITED],
    ["500 server error", HTTP.SERVER_ERROR],
  ])("keeps the rest of the dashboard usable when a widget's endpoint returns %s", async (_label, status) => {
    mock.onGet("/watchlist").reply(status, { detail: "nope" });
    mock.onGet("/notifications").reply(HTTP.OK, [testNotification]);
    await renderDashboard();

    // The failing widget degrades to its empty state…
    const watchlist = screen.getByTestId("watchlist-widget");
    await waitFor(() => expect(within(watchlist).getByText(/no stocks in your watchlist yet/i)).toBeInTheDocument());
    // …while its neighbours keep rendering their data.
    const notifications = screen.getByTestId("notifications-widget");
    await waitFor(() => expect(within(notifications).getByText(testNotification.title)).toBeInTheDocument());
  });

  it("still renders when the core market fetch fails outright", async () => {
    const consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
    mock.onGet("/market/overview").networkError();
    mock.onGet("/market/sectors").networkError();
    mock.onGet("/ai-activity").networkError();

    await renderDashboard();

    expect(screen.getByTestId("quick-actions")).toBeInTheDocument();
    consoleError.mockRestore();
  });

  it("survives a malformed payload where an array was expected", async () => {
    // A provider outage that returns `{}` instead of `[]` must not white-screen
    // the dashboard with "watchlist.slice is not a function".
    mock.onGet("/watchlist").reply(HTTP.OK, { unexpected: "shape" });
    await renderDashboard();

    const watchlist = screen.getByTestId("watchlist-widget");
    await waitFor(() => expect(within(watchlist).getByText(/no stocks in your watchlist yet/i)).toBeInTheDocument());
  });
});

describe("accessibility baseline", () => {
  it("labels every quick action", async () => {
    await renderDashboard();

    const actions = within(screen.getByTestId("quick-actions")).getAllByRole("button");
    expect(actions.length).toBeGreaterThan(0);
    actions.forEach((action) => expect(action).toHaveAccessibleName());
  });
});

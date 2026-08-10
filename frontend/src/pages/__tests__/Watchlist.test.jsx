/**
 * Watchlist.
 *
 * Production failures these catch: a watchlist that renders "empty" while its
 * request is still running; a removal that disappears from the screen but never
 * reaches the API; a search box that fires a request on every keystroke; and a
 * duplicate add offered for a stock the user already tracks.
 */
import { act, screen, waitFor, within } from "@testing-library/react";
import Watchlist from "../Watchlist";
import {
  renderWithProviders,
  installApiMock,
  stubRemainingWith,
  mockAuthenticatedUser,
  resetRealtimeStore,
  realtimeStore,
  HTTP,
  pending,
  testWatchlistItem,
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

const OTHER_ITEM = { ...testWatchlistItem, symbol: "OTHERCO", name: "Other Company Ltd" };

/**
 * Render the page with its stubs in place.
 *
 * `stubRemaining` must come last: axios-mock-adapter matches handlers in
 * registration order, so a catch-all registered first would swallow the
 * specific routes a test cares about.
 *
 * @param {Array}    items        the GET /watchlist payload
 * @param {Function} [setupStubs] register per-test routes before the catch-all
 */
async function renderWatchlist(items = [], setupStubs) {
  mock.onGet("/watchlist").reply(HTTP.OK, items);
  setupStubs?.(mock);
  stubRemainingWith(mock, []);

  const utils = renderWithProviders(<Watchlist />, { route: "/watchlist" });
  await screen.findByTestId("watchlist-page", {}, { timeout: 5000 });
  return utils;
}

/** Stub the symbol search with the given results. */
const withSearch = (results) => (m) => m.onGet(/\/stocks\/search/).reply(HTTP.OK, results);

describe("loading state", () => {
  it("shows placeholders rather than the empty state while loading", async () => {
    // Showing "your watchlist is empty" during the fetch is a lie that makes
    // users re-add stocks they already track.
    mock.onGet("/watchlist").reply(() => pending());
    stubRemainingWith(mock, []);

    renderWithProviders(<Watchlist />, { route: "/watchlist" });

    await waitFor(() => expect(document.querySelectorAll(".skeleton").length).toBeGreaterThan(0));
    expect(screen.queryByText(/your watchlist is empty/i)).not.toBeInTheDocument();
  });
});

describe("empty state", () => {
  it("explains what a watchlist is for and how to fill it", async () => {
    await renderWatchlist([]);

    expect(screen.getByText(/your watchlist is empty/i)).toBeInTheDocument();
    expect(screen.getByTestId("watchlist-search-input")).toBeInTheDocument();
  });
});

describe("populated state", () => {
  it("lists tracked stocks and counts them", async () => {
    await renderWatchlist([testWatchlistItem, OTHER_ITEM]);

    expect(screen.getByText(testWatchlistItem.symbol)).toBeInTheDocument();
    expect(screen.getByText(OTHER_ITEM.symbol)).toBeInTheDocument();
    expect(screen.getByText(/tracking 2 stocks/i)).toBeInTheDocument();
  });

  it("uses the singular for a single stock", async () => {
    await renderWatchlist([testWatchlistItem]);

    expect(screen.getByText(/tracking 1 stock$/i)).toBeInTheDocument();
  });
});

describe("adding a stock", () => {
  it("waits for the user to stop typing before searching", async () => {
    // Debounced at 250ms: a request per keystroke would hammer the search API.
    const { user } = await renderWatchlist([], withSearch([{ symbol: "TESTCO", name: "Test Company Ltd" }]));

    await user.type(screen.getByTestId("watchlist-search-input"), "TEST");

    expect(mock.history.get.filter((r) => r.url.startsWith("/stocks/search"))).toHaveLength(0);
    await waitFor(() =>
      expect(mock.history.get.filter((r) => r.url.startsWith("/stocks/search")).length).toBe(1),
    );
  });

  it("adds the chosen stock and refreshes the list", async () => {
    const { user } = await renderWatchlist([], (m) => {
      withSearch([{ symbol: "TESTCO", name: "Test Company Ltd" }])(m);
      m.onPost("/watchlist").reply(HTTP.OK, { symbol: "TESTCO" });
    });

    await user.type(screen.getByTestId("watchlist-search-input"), "TEST");
    await user.click(await screen.findByTestId("watchlist-add-TESTCO"));

    await waitFor(() => expect(mock.history.post.filter((r) => r.url === "/watchlist")).toHaveLength(1));
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ symbol: "TESTCO" });
  });

  it("does not offer to add a stock that is already tracked", async () => {
    const { user } = await renderWatchlist(
      [testWatchlistItem],
      withSearch([{ symbol: testWatchlistItem.symbol, name: testWatchlistItem.name }]),
    );

    await user.type(screen.getByTestId("watchlist-search-input"), "TEST");

    const option = await screen.findByTestId(`watchlist-add-${testWatchlistItem.symbol}`);
    expect(option).toBeDisabled();
    expect(within(option).getByText("Added")).toBeInTheDocument();
  });

  it("shows no suggestions when the search fails", async () => {
    const { user } = await renderWatchlist([], (m) =>
      m.onGet(/\/stocks\/search/).reply(HTTP.SERVER_ERROR, {}),
    );

    await user.type(screen.getByTestId("watchlist-search-input"), "TEST");

    await waitFor(() =>
      expect(mock.history.get.filter((r) => r.url.startsWith("/stocks/search")).length).toBe(1),
    );
    expect(screen.queryByTestId("watchlist-add-TESTCO")).not.toBeInTheDocument();
  });
});

describe("removing a stock", () => {
  it("calls the API and drops the row", async () => {
    const { user } = await renderWatchlist([testWatchlistItem, OTHER_ITEM], (m) =>
      m.onDelete(`/watchlist/${testWatchlistItem.symbol}`).reply(HTTP.OK, {}),
    );

    await user.click(screen.getByTestId(`watchlist-remove-${testWatchlistItem.symbol}`));

    await waitFor(() => expect(screen.queryByText(testWatchlistItem.symbol)).not.toBeInTheDocument());
    expect(mock.history.delete).toHaveLength(1);
    // The other stock is untouched.
    expect(screen.getByText(OTHER_ITEM.symbol)).toBeInTheDocument();
  });

  it("keeps the row when the removal fails, so the UI does not lie", async () => {
    const consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
    const { user } = await renderWatchlist([testWatchlistItem], (m) =>
      m.onDelete(`/watchlist/${testWatchlistItem.symbol}`).reply(HTTP.SERVER_ERROR, {}),
    );

    await user.click(screen.getByTestId(`watchlist-remove-${testWatchlistItem.symbol}`));

    await waitFor(() => expect(mock.history.delete).toHaveLength(1));
    expect(screen.getByText(testWatchlistItem.symbol)).toBeInTheDocument();

    consoleError.mockRestore();
  });
});

describe("live cross-surface sync", () => {
  it("drops a stock removed from another surface", async () => {
    // A watchlist.updated push (another tab, or the dashboard widget) must be
    // reflected here without a reload.
    await renderWatchlist([testWatchlistItem, OTHER_ITEM]);

    act(() => {
      realtimeStore.getState().applyMessages([
        {
          type: "event",
          event: "watchlist.updated",
          data: { action: "removed", symbol: testWatchlistItem.symbol },
        },
      ]);
    });

    await waitFor(() => expect(screen.queryByText(testWatchlistItem.symbol)).not.toBeInTheDocument());
    expect(screen.getByText(OTHER_ITEM.symbol)).toBeInTheDocument();
  });
});

describe("accessibility baseline", () => {
  it("names the search field and every remove control", async () => {
    await renderWatchlist([testWatchlistItem]);

    expect(screen.getByTestId("watchlist-search-input")).toHaveAccessibleName();
    expect(screen.getByTestId(`watchlist-remove-${testWatchlistItem.symbol}`)).toHaveAccessibleName();
  });
});

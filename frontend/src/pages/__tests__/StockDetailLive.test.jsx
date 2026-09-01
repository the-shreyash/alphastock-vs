/**
 * D5.19 — the detail page is live, and it is where a user can actually trade.
 *
 * TWO GAPS, ONE PAGE
 * ------------------
 * `StockDetail` had no realtime subscription of any kind. It fetched a quote on
 * mount and on a period change, and nothing else — so the page a user opens to
 * look closely at one instrument was the *least* live surface in the product,
 * while the dashboard strip behind it moved. That matters more after D5.19's
 * D-3: index cards now open this page, so NIFTY's detail view is where a user
 * lands expecting the number to keep moving.
 *
 * It also had no way to act. D5.18 found `brokerService.placeOrder` with no
 * caller anywhere in the frontend (LIM-D5.18-2), and the stock detail page is
 * the natural place for order entry — it is the one screen that already knows
 * exactly which instrument the user means.
 *
 * WHY THE PRICE MERGE IS THE SHARED HELPER
 * ----------------------------------------
 * `applyLivePrices` writes `price` and `change_pct` and nothing else, and
 * refuses to write a null. That second rule is load-bearing here: a canonical
 * MarketTick carries no day-change, and this page renders the day's change in
 * its header. An unguarded merge would blank a true change the moment the first
 * real tick arrived — the exact regression D5.17 caught on the index strip.
 */
import { screen, waitFor, act } from "@testing-library/react";

// `lightweight-charts` ships ESM that Jest's transform does not process, and it
// is not what this file is about. Stubbed so the page under test can mount.
jest.mock("../../components/charts/TradingChart", () => ({
  __esModule: true,
  default: () => <div data-testid="trading-chart" />,
}));

import StockDetail from "../StockDetail";
import { useRealtimeStore } from "../../store/realtimeStore";
import {
  renderWithProviders,
  installApiMock,
  stubRemainingWith,
  mockAuthenticatedUser,
  resetRealtimeStore,
} from "../../test-utils";

let mock;

const QUOTE = {
  symbol: "RELIANCE", name: "Reliance Industries", sector: "Oil & Gas",
  price: 1310.5, open: 1285, high: 1311.8, low: 1280, prev_close: 1277,
  change: 33.5, change_pct: 2.62, volume: 8358076, vwap: 1300.93,
  source_tier: "streaming",
};

const tickEvent = (ticks) => ({
  type: "event",
  event: "market.tick",
  data: {
    ticks: ticks.map(([symbol, price]) => ({
      symbol, price, exchange: "NSE", volume: null,
      ingested_at: "2026-09-01T07:45:20.120Z",
    })),
    count: ticks.length,
    source_tier: "streaming",
    ingested_at: "2026-09-01T07:45:20.121Z",
  },
});

const push = (event) => act(() => {
  useRealtimeStore.getState().applyEvent(event);
});

beforeEach(() => {
  mock = installApiMock();
  mockAuthenticatedUser(mock);
  resetRealtimeStore();
  mock.onGet("/stocks/RELIANCE").reply(200, QUOTE);
  mock.onGet(/\/stocks\/RELIANCE\/chart/).reply(200, []);
  mock.onGet("/stocks/RELIANCE/patterns").reply(200, { patterns: [] });
  mock.onGet("/watchlist").reply(200, []);
  mock.onGet("/brokers/status").reply(200, {});
});

afterEach(() => mock.restore());

async function renderDetail() {
  stubRemainingWith(mock, []);
  const utils = renderWithProviders(<StockDetail />, {
    route: "/stock/RELIANCE",
    path: "/stock/:symbol",
  });
  await screen.findByTestId("stock-detail-page");
  return utils;
}

describe("the detail page follows the live feed", () => {
  it("updates the price when a broker tick arrives", async () => {
    await renderDetail();
    await waitFor(() =>
      expect(screen.getByTestId("detail-price")).toHaveTextContent("1,310.5"));

    push(tickEvent([["RELIANCE", 1322.4]]));

    await waitFor(() =>
      expect(screen.getByTestId("detail-price")).toHaveTextContent("1,322.4"));
  });

  it("ignores a tick for a different symbol", async () => {
    await renderDetail();

    push(tickEvent([["TCS", 4100]]));

    await waitFor(() =>
      expect(screen.getByTestId("detail-price")).toHaveTextContent("1,310.5"));
  });

  it("keeps the day change a tick does not carry", async () => {
    /**
     * The regression that would otherwise ship with this change. A canonical
     * tick has a price and no day-change; writing `change_pct: undefined` over
     * a real +2.62% would erase the day's move at the exact moment the price
     * started updating.
     */
    await renderDetail();

    push(tickEvent([["RELIANCE", 1322.4]]));

    await waitFor(() =>
      expect(screen.getByTestId("detail-price")).toHaveTextContent("1,322.4"));
    expect(screen.getByTestId("detail-change")).toHaveTextContent("2.62%");
  });
});

describe("the detail page states its freshness", () => {
  it("labels the tier the quote was served at", async () => {
    await renderDetail();

    await waitFor(() =>
      expect(screen.getByTestId("detail-tier")).toHaveTextContent(/live/i));
  });

  it("never names the provider", async () => {
    await renderDetail();

    expect(document.body.textContent)
      .not.toMatch(/yahoo|upstox|zerodha|kite|fyers|dhan|angel/i);
  });
});

describe("the detail page offers order entry", () => {
  it("renders the order ticket", async () => {
    await renderDetail();

    await waitFor(() =>
      expect(screen.getByTestId("order-ticket")).toBeInTheDocument());
  });

  it("says no broker can trade when none is connected", async () => {
    await renderDetail();

    await waitFor(() =>
      expect(screen.getByTestId("order-unavailable")).toBeInTheDocument());
  });
});

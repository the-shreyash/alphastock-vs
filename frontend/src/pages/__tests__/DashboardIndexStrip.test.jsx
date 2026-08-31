/**
 * D5.17 — a broker tick moves the index card.
 *
 * WHY THIS IS A PAGE TEST AND NOT A MODULE TEST
 * ----------------------------------------------
 * `liveIndexPrices.test.js` proves the merge rules. It cannot prove that the
 * dashboard *uses* them, and that is exactly the gap this sprint's mutation
 * campaign found on the server side: every layer of the boundary was proved and
 * the one line wiring it to an account was not, so deleting it left the suite
 * green. The frontend has the identical shape — an effect nothing calls is
 * indistinguishable from an effect that works.
 *
 * So this drives the whole chain the acceptance criterion names:
 *
 *     market.tick  →  realtimeStore.applyEvent  →  priceTicks
 *                  →  Dashboard effect  →  the rendered index card
 *
 * through the *real* store and the real page, with only the HTTP layer stubbed.
 * The event payload is the one the backend publishes, batch shape and all.
 */
import { act, screen, waitFor, within } from "@testing-library/react";
import Dashboard from "../Dashboard";
import { useRealtimeStore } from "../../store/realtimeStore";
import {
  renderWithProviders,
  installApiMock,
  stubRemainingWith,
  mockAuthenticatedUser,
  resetRealtimeStore,
} from "../../test-utils";

let mock;

/** `/market/overview` as the route returns it: values and real day-changes. */
const OVERVIEW = {
  available: true,
  nifty: { value: 24810, change: 120.5, change_pct: 0.49, available: true },
  bank_nifty: { value: 52400, change: -80.2, change_pct: -0.15, available: true },
  sensex: { value: 81020, change: 300.1, change_pct: 0.37, available: true },
  india_vix: 13.4,
  market_status: "OPEN",
  source_tier: "delayed",
};

/**
 * One `market.tick` event, in the exact envelope the Market Gateway publishes:
 * ONE event per broker frame carrying a batch, `source_tier`, and **no
 * `change_pct` on any tick** — a canonical MarketTick has none.
 */
const tickEvent = (ticks) => ({
  type: "event",
  event: "market.tick",
  data: {
    ticks: ticks.map(([symbol, price]) => ({
      symbol,
      price,
      exchange: symbol === "SENSEX" ? "BSE" : "NSE",
      volume: null,
      ingested_at: "2026-08-31T09:45:20.120Z",
    })),
    count: ticks.length,
    source_tier: "streaming",
    ingested_at: "2026-08-31T09:45:20.121Z",
  },
});

beforeEach(() => {
  mock = installApiMock();
  mockAuthenticatedUser(mock);
  resetRealtimeStore();
  mock.onGet("/market/overview").reply(200, OVERVIEW);
});

afterEach(() => {
  mock.restore();
});

async function renderDashboard() {
  stubRemainingWith(mock, []);
  const utils = renderWithProviders(<Dashboard />, { route: "/dashboard" });
  await screen.findByTestId("quick-actions");
  // The strip is rendered from the overview fetch, so wait for the baseline to
  // land before pushing a tick — otherwise a passing assertion could be a race.
  await waitFor(() =>
    expect(screen.getByTestId("nifty-card")).toHaveTextContent("24,810"));
  return utils;
}

/** Push one event through the real store, exactly as the socket does. */
const push = (event) => act(() => {
  useRealtimeStore.getState().applyEvent(event);
});

describe("a canonical market.tick reaches the index strip", () => {
  it("moves Nifty, Bank Nifty and Sensex", async () => {
    await renderDashboard();

    push(tickEvent([["NIFTY", 24815.25], ["BANKNIFTY", 52444.1], ["SENSEX", 81099.9]]));

    await waitFor(() =>
      expect(screen.getByTestId("nifty-card")).toHaveTextContent("24,815.25"));
    expect(screen.getByTestId("banknifty-card")).toHaveTextContent("52,444.1");
    expect(screen.getByTestId("sensex-card")).toHaveTextContent("81,099.9");
  });

  it("moves India VIX", async () => {
    await renderDashboard();

    push(tickEvent([["INDIAVIX", 12.85]]));

    await waitFor(() =>
      expect(screen.getByTestId("vix-card")).toHaveTextContent("12.85"));
  });

  it("keeps the day-change the overview supplied", async () => {
    /**
     * THE REGRESSION THIS SPRINT WOULD HAVE SHIPPED.
     *
     * The effect this replaced wrote `change_pct: tick.change_pct`
     * unconditionally, and a canonical tick carries none — so the first real
     * index tick would have blanked the day's change on the card at the exact
     * moment the price started moving live. A page-level assertion, because
     * the symptom is a row that disappears from the DOM.
     */
    await renderDashboard();
    expect(screen.getByTestId("nifty-card")).toHaveTextContent("+0.49%");

    push(tickEvent([["NIFTY", 24815.25]]));

    await waitFor(() =>
      expect(screen.getByTestId("nifty-card")).toHaveTextContent("24,815.25"));
    expect(screen.getByTestId("nifty-card")).toHaveTextContent("+0.49%");
  });

  it("leaves an index the batch did not carry alone", async () => {
    await renderDashboard();

    push(tickEvent([["NIFTY", 24815.25]]));

    await waitFor(() =>
      expect(screen.getByTestId("nifty-card")).toHaveTextContent("24,815.25"));
    expect(screen.getByTestId("sensex-card")).toHaveTextContent("81,020");
    expect(screen.getByTestId("banknifty-card")).toHaveTextContent("52,400");
  });

  it("never renders a provider name beside a live index", async () => {
    // Developer Rule 4: the card must be unable to say where its number came
    // from, whichever tier produced it.
    await renderDashboard();
    push(tickEvent([["NIFTY", 24815.25]]));

    await waitFor(() =>
      expect(screen.getByTestId("nifty-card")).toHaveTextContent("24,815.25"));
    const text = screen.getByTestId("nifty-card").textContent.toLowerCase();
    for (const name of ["yahoo", "zerodha", "kite", "upstox", "angel", "fyers", "dhan"]) {
      expect(text).not.toContain(name);
    }
  });
});

describe("the commodities strip states its unit", () => {
  it("renders the unit beside the number", async () => {
    /**
     * Gold read `Gold  3,450` while the payload called it `"Gold (MCX)"` in
     * `"INR/10g"` and the ticker behind it was COMEX gold in USD per ounce.
     * The number was real; the reading was off by a factor of about thirty.
     */
    mock.onGet("/market/commodities").reply(200, {
      gold: { name: "Gold (COMEX)", value: 3450.2, unit: "USD/oz", change_pct: 0.42, available: true },
      silver: { name: "Silver (COMEX)", value: 41.15, unit: "USD/oz", change_pct: -0.2, available: true },
      crude_oil: { name: "Brent Crude", value: 62.1, unit: "USD/bbl", change_pct: 1.1, available: true },
      usd_inr: { name: "USD/INR", value: 83.2, unit: "INR", change_pct: 0.05, available: true },
    });
    await renderDashboard();

    const strip = await screen.findByTestId("commodities-strip");
    await waitFor(() => expect(strip).toHaveTextContent("USD/oz"));
    expect(strip).toHaveTextContent("USD/bbl");
    expect(within(strip).getAllByText("USD/oz")).toHaveLength(2);
  });

  it("renders no stray unit when the server states none", async () => {
    mock.onGet("/market/commodities").reply(200, {
      gold: { name: "Gold", value: 3450.2, change_pct: 0.42, available: true },
    });
    await renderDashboard();

    const strip = await screen.findByTestId("commodities-strip");
    await waitFor(() => expect(strip).toHaveTextContent("3,450.2"));
    expect(strip).not.toHaveTextContent("undefined");
  });
});

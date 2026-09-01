/**
 * D5.19 (D-3) — an index card is a market instrument, so it opens like one.
 *
 * WHAT WAS WRONG
 * --------------
 * `StatCard` rendered a plain `<div>`. Every stock on the dashboard opened a
 * detail page on click; NIFTY, BANK NIFTY, SENSEX and INDIA VIX — the four most
 * prominent numbers on the screen, and after D5.17 the four that a broker feed
 * actually ticks — were the only market instruments in the product that could
 * not be opened at all (D5.18's D-3, LIM-D5.18-5).
 *
 * WHY THIS IS ONLY NOW SAFE TO WIRE
 * ---------------------------------
 * It is one `onClick`, and D5.18 audited it without fixing it. The reason to
 * hold it was not the click: `GET /api/stocks/{symbol}` already answered for
 * all four indices with real OHLC, and still does. But it answered with the
 * WRONG DAY CHANGE — it fetches a 3-month window, and `prev_close` was read
 * from the vendor's range-dependent `chartPreviousClose`. Measured live on
 * 2026-09-01, the dashboard card read NIFTY +0.13% and the detail page behind
 * it read +3.11%, because the page was showing a three-month change under a
 * "today" label.
 *
 * Wiring navigation before fixing that would have shipped a click that takes a
 * user from one number to a contradicting one — D5.18's D-1 defect (two
 * surfaces of one product disagreeing) reintroduced by the fix for D-3. See
 * `backend/tests/test_day_change_is_the_days_change.py`.
 *
 * IDENTITY IS THE D5.17 CANONICAL SYMBOL
 * --------------------------------------
 * `NIFTY`, `BANKNIFTY`, `SENSEX`, `INDIAVIX` — the platform's spellings, the
 * keys `INDEX_OVERVIEW_KEYS` folds ticks onto and the keys
 * `brokers/catalogue.py` resolves against all five brokers' masters. A route
 * built from the card's *label* would read `/stock/Bank Nifty`, which matches
 * nothing at any broker and nothing in the overview.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import Dashboard from "../Dashboard";
import {
  renderWithProviders,
  installApiMock,
  stubRemainingWith,
  mockAuthenticatedUser,
  resetRealtimeStore,
} from "../../test-utils";

let mock;

/**
 * The current route, rendered into the tree.
 *
 * Asserting on this rather than spying on `useNavigate` means the test drives
 * the real router: a card that calls `navigate()` with the wrong argument, or
 * a `<Link>` with a malformed `to`, fails here. A mocked hook would record the
 * call and prove nothing about where the user lands.
 */
function LocationProbe() {
  return <span data-testid="route">{useLocation().pathname}</span>;
}

const currentRoute = () => screen.getByTestId("route").textContent;

const OVERVIEW = {
  available: true,
  nifty: { value: 24810, change: 120.5, change_pct: 0.49, available: true },
  bank_nifty: { value: 52400, change: -80.2, change_pct: -0.15, available: true },
  sensex: { value: 81020, change: 300.1, change_pct: 0.37, available: true },
  india_vix: 13.4,
  market_status: "OPEN",
  source_tier: "delayed",
};

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
  const utils = renderWithProviders(
    <><Dashboard /><LocationProbe /></>,
    { route: "/dashboard" }
  );
  await screen.findByTestId("quick-actions");
  await waitFor(() =>
    expect(screen.getByTestId("nifty-card")).toHaveTextContent("24,810"));
  return utils;
}

/** Card test id -> the canonical symbol its detail page must be opened with. */
const INDEX_ROUTES = [
  ["nifty-card", "NIFTY"],
  ["banknifty-card", "BANKNIFTY"],
  ["sensex-card", "SENSEX"],
  ["vix-card", "INDIAVIX"],
];

describe("index cards open their detail page", () => {
  it.each(INDEX_ROUTES)("%s navigates to /stock/%s", async (testId, symbol) => {
    await renderDashboard();

    await userEvent.click(screen.getByTestId(testId));

    await waitFor(() => expect(currentRoute()).toBe(`/stock/${symbol}`));
  });

  it("uses the canonical symbol, not the rendered label", async () => {
    /**
     * Falsification. Every assertion above would also pass if the route were
     * built from the card's label — until BANK NIFTY, whose label contains a
     * space and whose canonical symbol does not. `/stock/Bank Nifty` resolves
     * to nothing in `INDEX_OVERVIEW_KEYS` and to nothing in any broker's
     * master, so the page would load empty and the live tick would never find
     * it.
     */
    await renderDashboard();

    await userEvent.click(screen.getByTestId("banknifty-card"));

    await waitFor(() => expect(currentRoute()).toBe("/stock/BANKNIFTY"));
    expect(currentRoute()).not.toContain(" ");
    expect(currentRoute()).not.toMatch(/bank nifty/i);
  });

  it("is reachable by keyboard", async () => {
    /**
     * A div with an onClick is not a control. Making the four headline numbers
     * openable only by mouse would be a new accessibility regression in the
     * same change that fixes a usability one.
     */
    await renderDashboard();

    const card = screen.getByTestId("nifty-card");
    expect(card.tagName).toBe("BUTTON");
  });

  it("still renders the value and the day change it had before", async () => {
    /** Making the card a control must not cost it its content. */
    await renderDashboard();

    expect(screen.getByTestId("nifty-card")).toHaveTextContent("24,810");
    expect(screen.getByTestId("nifty-card")).toHaveTextContent("0.49%");
  });
});

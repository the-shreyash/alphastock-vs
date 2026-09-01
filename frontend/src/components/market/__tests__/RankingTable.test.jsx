/**
 * D5.19 — Top Opportunities is a live, explainable, navigable surface.
 *
 * WHAT WAS WRONG (D5.18's D-4/D-5/D-6, LIM-D5.18-3 and -4)
 * --------------------------------------------------------
 * The premise the brief starts from — that this list is hardcoded — is false,
 * and D5.18 established that: `/market/ranking` runs the real multi-dimensional
 * scorer over real quotes, and the symbols on screen are genuinely today's top
 * five by score. What was actually wrong is narrower and worse:
 *
 *   * **The prices were frozen.** The component fetched once on mount and never
 *     subscribed to `priceTicks`. On a page left open, the most prominent
 *     recommendation on the dashboard showed the price it was ranked at, for as
 *     long as the tab lived — while every other price surface moved. A list of
 *     live-looking stocks with dead numbers is what "static/demo values" looks
 *     like from the outside, and it is why the brief believed it was fixtures.
 *
 *   * **There was no way in.** Clicking a row expanded a panel. Every other
 *     stock in the product opens a detail page.
 *
 *   * **There was no "why".** In `compact` mode — the dashboard — the dimension
 *     panel was not rendered at all, so the surface asserted "Buy, score 63.6"
 *     and offered no evidence whatsoever.
 *
 * WHY THE EVIDENCE COMES FROM THE SERVER
 * --------------------------------------
 * The reasons are the scorer's own strings, chosen server-side by
 * `ranking_engine.build_evidence` from dimensions whose inputs were actually
 * present. This component may not compose, template or reorder them — if it
 * did, the explanation would be a frontend invention about a backend score, and
 * the two would drift the first time a weight changed. See
 * `backend/tests/test_ranking_evidence.py` for why "a reason string exists" is
 * not the same question as "the reason is true".
 */
import { screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import RankingTable from "../RankingTable";
import { useRealtimeStore } from "../../../store/realtimeStore";
import {
  renderWithProviders,
  installApiMock,
  mockAuthenticatedUser,
  resetRealtimeStore,
} from "../../../test-utils";

let mock;

function LocationProbe() {
  return <span data-testid="route">{useLocation().pathname}</span>;
}

const RANKING = {
  available: true,
  count: 2,
  source_tier: "streaming",
  rankings: [
    {
      symbol: "RELIANCE",
      name: "Reliance Industries",
      price: 1310.5,
      change_pct: 2.57,
      sector: "Oil & Gas",
      opportunity_score: 67.3,
      signal: "buy",
      dimensions: {},
      evidence: [
        { dimension: "momentum", score: 95, reason: "RSI 51 in bullish zone; Strong +2.5% day move", contribution: 9.0 },
        { dimension: "sector", score: 95.7, reason: "Sector ranked #3/14 (leading)", contribution: 4.57 },
      ],
    },
    {
      symbol: "TATASTEEL",
      name: "Tata Steel",
      price: 142.2,
      change_pct: 1.0,
      sector: "Metals",
      opportunity_score: 69.7,
      signal: "buy",
      dimensions: {},
      evidence: [
        { dimension: "trend", score: 80, reason: "MACD bullish crossover", contribution: 5.4 },
      ],
    },
  ],
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
  mock.onGet("/market/ranking").reply(200, RANKING);
});

afterEach(() => mock.restore());

async function renderTable(props = { compact: true }) {
  const utils = renderWithProviders(
    <><RankingTable {...props} /><LocationProbe /></>,
    { route: "/dashboard" }
  );
  await screen.findByText("RELIANCE");
  return utils;
}

/** Re-point `/market/ranking` at a different payload before rendering. */
function serve(payload) {
  mock.resetHandlers();
  mockAuthenticatedUser(mock);
  mock.onGet("/market/ranking").reply(200, payload);
}

// --------------------------------------------------------------------------- //
// D-6 — the prices are live                                                    //
// --------------------------------------------------------------------------- //

describe("Top Opportunities follows the live feed", () => {
  it("updates a ranked price when a broker tick arrives", async () => {
    await renderTable();
    expect(screen.getByTestId("ranking-price-RELIANCE")).toHaveTextContent("1,310.5");

    push(tickEvent([["RELIANCE", 1318.75]]));

    await waitFor(() =>
      expect(screen.getByTestId("ranking-price-RELIANCE")).toHaveTextContent("1,318.75"));
  });

  it("leaves a row no tick arrived for untouched", async () => {
    await renderTable();

    push(tickEvent([["RELIANCE", 1318.75]]));

    await waitFor(() =>
      expect(screen.getByTestId("ranking-price-RELIANCE")).toHaveTextContent("1,318.75"));
    expect(screen.getByTestId("ranking-price-TATASTEEL")).toHaveTextContent("142.2");
  });

  it("does not overwrite the day change with a tick that carries none", async () => {
    /**
     * A canonical MarketTick has a price and no day-change — a day's change
     * needs a previous close the tick contract does not carry. Writing one
     * would render a real live price beside a fabricated "unchanged". This is
     * the rule `applyLivePrices` enforces, re-asserted here because this is a
     * new call site for it.
     */
    await renderTable();

    push(tickEvent([["RELIANCE", 1318.75]]));

    await waitFor(() =>
      expect(screen.getByTestId("ranking-price-RELIANCE")).toHaveTextContent("1,318.75"));
    expect(screen.getByTestId("ranking-row-RELIANCE")).toHaveTextContent("2.57%");
  });
});

// --------------------------------------------------------------------------- //
// D-5 — the freshness label                                                    //
// --------------------------------------------------------------------------- //

describe("Top Opportunities states the freshness of its data", () => {
  it("labels a streaming tier", async () => {
    await renderTable();

    expect(screen.getByTestId("ranking-tier")).toHaveTextContent(/live/i);
  });

  it("labels a delayed tier", async () => {
    serve({ ...RANKING, source_tier: "delayed" });
    await renderTable();

    expect(screen.getByTestId("ranking-tier")).toHaveTextContent(/delayed/i);
  });

  it("never names the provider", async () => {
    /** Developer Rule 4: freshness is publishable, provenance is not. */
    await renderTable();

    expect(document.body.textContent)
      .not.toMatch(/yahoo|upstox|zerodha|kite|fyers|dhan|angel/i);
  });
});

// --------------------------------------------------------------------------- //
// "Why is this a top opportunity?"                                             //
// --------------------------------------------------------------------------- //

describe("every opportunity explains itself", () => {
  it("shows the scoring evidence on the dashboard card", async () => {
    await renderTable();

    expect(screen.getByTestId("ranking-row-RELIANCE"))
      .toHaveTextContent("RSI 51 in bullish zone; Strong +2.5% day move");
  });

  it("renders the reasons the server actually sent, verbatim", async () => {
    /**
     * Falsification: the explanation may not be authored here. Every rendered
     * reason must be a string the payload carried.
     */
    await renderTable();

    const sent = RANKING.rankings.flatMap((r) => r.evidence.map((e) => e.reason));
    screen.getAllByTestId(/^ranking-evidence-/)
      .forEach((el) => expect(sent).toContain(el.textContent));
  });

  it("says nothing rather than inventing a reason when there is no evidence", async () => {
    /**
     * The core rule. A stock the engine could not explain — a new listing with
     * no 26-bar MACD, a suspended one with no volume — must produce an honest
     * absence, not a filler sentence. Fabricating one here is exactly the
     * failure `build_evidence` returns an empty list to prevent.
     */
    serve({ ...RANKING, rankings: [{ ...RANKING.rankings[0], evidence: [] }] });
    await renderTable();

    expect(screen.queryByTestId("ranking-evidence-RELIANCE-0")).not.toBeInTheDocument();
    expect(screen.getByTestId("ranking-row-RELIANCE")).toBeInTheDocument();
  });

  it("tolerates a payload with no evidence field at all", async () => {
    /** An older cached response must not blank the list. */
    const { evidence, ...withoutEvidence } = RANKING.rankings[0];
    serve({ ...RANKING, rankings: [withoutEvidence] });

    await renderTable();

    expect(screen.getByText("RELIANCE")).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// Navigation                                                                   //
// --------------------------------------------------------------------------- //

describe("an opportunity opens its stock", () => {
  it("navigates to the detail page on click", async () => {
    await renderTable();

    await userEvent.click(screen.getByTestId("ranking-row-RELIANCE"));

    await waitFor(() =>
      expect(screen.getByTestId("route").textContent).toBe("/stock/RELIANCE"));
  });
});

/**
 * Paper trading — the order-entry surface.
 *
 * This is the most consequential UI in the product after authentication: it
 * takes an order, validates it, and reports what the engine did with it. The
 * tests below assert the four things a trader must be able to trust:
 *
 *   1. What is on screen is what the API returned (never a stale or invented
 *      balance).
 *   2. A submitted order carries exactly the numbers that were typed.
 *   3. A rejected order says why, and does not look like a success.
 *   4. A failure to load says so, instead of rendering an empty account.
 */
import { screen, waitFor, within } from "@testing-library/react";
import PaperTrading from "../PaperTrading";
import {
  renderWithProviders,
  installApiMock,
  mockAuthenticatedUser,
  resetRealtimeStore,
  HTTP,
  pending,
  testPaperBalance,
  testPaperPnl,
  testOpenTrade,
  testClosedTrade,
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

/** Stub the three endpoints the page loads on mount. */
function stubAccount({ balance = testPaperBalance, pnl = testPaperPnl, trades = [testOpenTrade, testClosedTrade] } = {}) {
  mock.onGet("/paper/balance").reply(HTTP.OK, balance);
  mock.onGet("/paper/pnl").reply(HTTP.OK, pnl);
  mock.onGet("/paper/trades").reply(HTTP.OK, trades);
}

async function renderPaperTrading() {
  const utils = renderWithProviders(<PaperTrading />, { route: "/paper-trading" });
  await screen.findByTestId("paper-trading-page", {}, { timeout: 5000 });
  return utils;
}

/** Fill the new-trade modal with a valid order. */
async function fillOrder(user, { symbol = "TESTCO", quantity = "25", entry = "1200", stop = "1150", target = "1300" } = {}) {
  await user.clear(screen.getByPlaceholderText("RELIANCE"));
  await user.type(screen.getByPlaceholderText("RELIANCE"), symbol);
  const qty = screen.getByLabelText("Quantity");
  await user.clear(qty);
  await user.type(qty, quantity);
  await user.type(screen.getByLabelText(/entry price/i), entry);
  await user.type(screen.getByLabelText(/stop loss/i), stop);
  await user.type(screen.getByLabelText(/target/i), target);
}

describe("loading state", () => {
  it("shows placeholders while the account is loading", async () => {
    mock.onGet("/paper/balance").reply(() => pending());
    mock.onGet("/paper/pnl").reply(() => pending());
    mock.onGet("/paper/trades").reply(() => pending());

    renderWithProviders(<PaperTrading />, { route: "/paper-trading" });

    expect(await screen.findByTestId("paper-trading-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("paper-trading-page")).not.toBeInTheDocument();
  });
});

describe("populated account", () => {
  it("renders the balance and P&L the API returned", async () => {
    stubAccount();
    await renderPaperTrading();

    expect(screen.getByText("₹1,00,000.00")).toBeInTheDocument();
    expect(screen.getByText(/1 closed/)).toBeInTheDocument();
  });

  it("lists open positions", async () => {
    stubAccount();
    await renderPaperTrading();

    expect(screen.getByText(testOpenTrade.symbol)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /open \(1\)/i })).toBeInTheDocument();
  });

  it("shows closed positions on the closed tab", async () => {
    stubAccount();
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /closed \(1\)/i }));

    expect(await screen.findByText(testClosedTrade.symbol)).toBeInTheDocument();
  });
});

describe("empty states", () => {
  it("invites the user to place a first trade when there are no open positions", async () => {
    stubAccount({ trades: [] });
    await renderPaperTrading();

    expect(screen.getByText(/no open paper trades/i)).toBeInTheDocument();
  });

  it("says so when no trade has been closed yet", async () => {
    stubAccount({ trades: [] });
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /closed \(0\)/i }));

    expect(await screen.findByText(/no closed paper trades yet/i)).toBeInTheDocument();
  });
});

describe("load failure", () => {
  // Regression guard (PH3.2 defect FE-003): a failed load used to fall through
  // to the normal view, showing a zero balance and "no open trades" — which a
  // trader reads as "my positions are gone", not "the server is down".
  it.each([
    ["a server fault", HTTP.SERVER_ERROR],
    ["rate limiting", HTTP.RATE_LIMITED],
    ["a forbidden account", HTTP.FORBIDDEN],
  ])("states the problem instead of rendering an empty account on %s", async (_label, status) => {
    mock.onGet("/paper/balance").reply(status, { detail: "Paper trading engine unavailable" });
    mock.onGet("/paper/pnl").reply(status, {});
    mock.onGet("/paper/trades").reply(status, {});

    renderWithProviders(<PaperTrading />, { route: "/paper-trading" });

    const error = await screen.findByTestId("paper-trading-error");
    expect(error).toHaveTextContent("Paper trading engine unavailable");
    expect(screen.queryByText(/no open paper trades/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId("paper-trading-page")).not.toBeInTheDocument();
  });

  it("explains an unreachable backend", async () => {
    mock.onGet("/paper/balance").networkError();
    mock.onGet("/paper/pnl").networkError();
    mock.onGet("/paper/trades").networkError();

    renderWithProviders(<PaperTrading />, { route: "/paper-trading" });

    expect(await screen.findByTestId("paper-trading-error")).toHaveTextContent(/could not reach the server/i);
  });

  it("recovers when the user retries and the backend has come back", async () => {
    mock.onGet("/paper/balance").replyOnce(HTTP.SERVER_ERROR, {});
    mock.onGet("/paper/pnl").replyOnce(HTTP.SERVER_ERROR, {});
    mock.onGet("/paper/trades").replyOnce(HTTP.SERVER_ERROR, {});
    stubAccount();

    const { user } = renderWithProviders(<PaperTrading />, { route: "/paper-trading" });

    await user.click(await screen.findByTestId("paper-trading-retry"));

    expect(await screen.findByTestId("paper-trading-page")).toBeInTheDocument();
    expect(screen.queryByTestId("paper-trading-error")).not.toBeInTheDocument();
  });

  it("announces the failure to assistive technology", async () => {
    mock.onGet("/paper/balance").reply(HTTP.SERVER_ERROR, {});
    mock.onGet("/paper/pnl").reply(HTTP.SERVER_ERROR, {});
    mock.onGet("/paper/trades").reply(HTTP.SERVER_ERROR, {});

    renderWithProviders(<PaperTrading />, { route: "/paper-trading" });

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

describe("placing an order", () => {
  it("opens the order ticket", async () => {
    stubAccount();
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /new paper trade/i }));

    expect(await screen.findByRole("heading", { name: /new paper trade/i })).toBeInTheDocument();
    // The simulated badge must be unmissable — this is not real money.
    expect(screen.getAllByText("SIMULATED").length).toBeGreaterThan(0);
  });

  it("submits exactly the numbers the trader typed, as numbers", async () => {
    stubAccount();
    mock.onPost("/paper/trade").reply(HTTP.OK, { _id: "t_new" });
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /new paper trade/i }));
    await fillOrder(user);
    await user.click(screen.getByRole("button", { name: /place paper trade/i }));

    await waitFor(() => expect(mock.history.post).toHaveLength(1));
    const body = JSON.parse(mock.history.post[0].data);
    expect(body).toMatchObject({
      symbol: "TESTCO",
      type: "BUY",
      quantity: 25,
      entry_price: 1200,
      stop_loss: 1150,
      target1: 1300,
    });
    // Strings here would be silently coerced (or rejected) by the engine.
    expect(typeof body.quantity).toBe("number");
    expect(typeof body.entry_price).toBe("number");
  });

  it("records the side the trader selected", async () => {
    stubAccount();
    mock.onPost("/paper/trade").reply(HTTP.OK, {});
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /new paper trade/i }));
    await user.click(screen.getByRole("button", { name: "SELL" }));
    await fillOrder(user);
    await user.click(screen.getByRole("button", { name: /place paper trade/i }));

    await waitFor(() => expect(mock.history.post).toHaveLength(1));
    expect(JSON.parse(mock.history.post[0].data).type).toBe("SELL");
  });

  it("confirms the fill and refreshes the account", async () => {
    stubAccount();
    mock.onPost("/paper/trade").reply(HTTP.OK, {});
    const { user } = await renderPaperTrading();
    const loadsBefore = mock.history.get.filter((r) => r.url === "/paper/trades").length;

    await user.click(screen.getByRole("button", { name: /new paper trade/i }));
    await fillOrder(user);
    await user.click(screen.getByRole("button", { name: /place paper trade/i }));

    expect(await screen.findByText(/paper trade placed successfully/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(mock.history.get.filter((r) => r.url === "/paper/trades").length).toBeGreaterThan(loadsBefore),
    );
  });

  it("closes the ticket on success so the order cannot be sent twice", async () => {
    stubAccount();
    mock.onPost("/paper/trade").reply(HTTP.OK, {});
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /new paper trade/i }));
    await fillOrder(user);
    await user.click(screen.getByRole("button", { name: /place paper trade/i }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /place paper trade/i })).not.toBeInTheDocument(),
    );
  });

  it("disables the submit button while the order is in flight", async () => {
    stubAccount();
    mock.onPost("/paper/trade").reply(() => pending());
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /new paper trade/i }));
    await fillOrder(user);
    const submit = screen.getByRole("button", { name: /place paper trade/i });
    await user.click(submit);

    await waitFor(() => expect(screen.getByRole("button", { name: /placing/i })).toBeDisabled());
    expect(mock.history.post).toHaveLength(1);
  });

  it("prefills the entry price from the live quote when the symbol is entered", async () => {
    stubAccount();
    mock.onGet("/stocks/TESTCO").reply(HTTP.OK, { price: 1234.5, name: "Test Company Ltd" });
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /new paper trade/i }));
    await user.type(screen.getByPlaceholderText("RELIANCE"), "TESTCO");
    await user.tab(); // blur triggers the quote lookup

    await waitFor(() => expect(screen.getByLabelText(/entry price/i)).toHaveValue(1234.5));
  });
});

describe("rejected orders", () => {
  it("shows the risk manager's reason and keeps the ticket open for correction", async () => {
    stubAccount();
    mock.onPost("/paper/trade").reply(422, { detail: "Position size exceeds 10% of capital" });
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /new paper trade/i }));
    await fillOrder(user, { quantity: "10000" });
    await user.click(screen.getByRole("button", { name: /place paper trade/i }));

    expect(await screen.findByText("Position size exceeds 10% of capital")).toBeInTheDocument();
    // Still open: the trader must be able to fix the order, not retype it.
    expect(screen.getByRole("button", { name: /place paper trade/i })).toBeInTheDocument();
  });

  it("never reports a rejected order as placed", async () => {
    stubAccount();
    mock.onPost("/paper/trade").reply(HTTP.SERVER_ERROR, { detail: "Engine offline" });
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /new paper trade/i }));
    await fillOrder(user);
    await user.click(screen.getByRole("button", { name: /place paper trade/i }));

    expect(await screen.findByText("Engine offline")).toBeInTheDocument();
    expect(screen.queryByText(/placed successfully/i)).not.toBeInTheDocument();
  });

  it("re-enables the submit button after a rejection", async () => {
    stubAccount();
    mock.onPost("/paper/trade").reply(422, { detail: "Rejected" });
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /new paper trade/i }));
    await fillOrder(user);
    await user.click(screen.getByRole("button", { name: /place paper trade/i }));

    await screen.findByText("Rejected");
    expect(screen.getByRole("button", { name: /place paper trade/i })).toBeEnabled();
  });
});

describe("closing a position", () => {
  it("closes the position and reports the realised P&L", async () => {
    stubAccount();
    mock.onPost(`/paper/close/${testOpenTrade._id}`).reply(HTTP.OK, { exit_price: 1250, pnl: 500 });
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(await screen.findByText(/Closed at ₹1250/)).toBeInTheDocument();
  });

  it("explains a failed close instead of implying the position is gone", async () => {
    stubAccount();
    mock.onPost(`/paper/close/${testOpenTrade._id}`).reply(HTTP.SERVER_ERROR, { detail: "Could not close position" });
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(await screen.findByText("Could not close position")).toBeInTheDocument();
    // The position is still listed — nothing has changed.
    expect(screen.getByText(testOpenTrade.symbol)).toBeInTheDocument();
  });
});

describe("resetting paper capital", () => {
  it("asks for confirmation before wiping the account", async () => {
    stubAccount();
    const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(false);
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /reset capital/i }));

    expect(confirmSpy).toHaveBeenCalled();
    expect(mock.history.post.filter((r) => r.url === "/paper/reset")).toHaveLength(0);

    confirmSpy.mockRestore();
  });

  it("resets only after the user confirms", async () => {
    stubAccount();
    mock.onPost("/paper/reset").reply(HTTP.OK, {});
    const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(true);
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /reset capital/i }));

    await waitFor(() => expect(mock.history.post.filter((r) => r.url === "/paper/reset")).toHaveLength(1));
    expect(await screen.findByText(/reset to ₹1,00,000/i)).toBeInTheDocument();

    confirmSpy.mockRestore();
  });

  it("reports a failed reset", async () => {
    stubAccount();
    mock.onPost("/paper/reset").reply(HTTP.SERVER_ERROR, {});
    const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(true);
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /reset capital/i }));

    expect(await screen.findByText(/reset failed/i)).toBeInTheDocument();

    confirmSpy.mockRestore();
  });
});

describe("accessibility baseline", () => {
  it("labels every field in the order ticket", async () => {
    stubAccount();
    const { user } = await renderPaperTrading();

    await user.click(screen.getByRole("button", { name: /new paper trade/i }));

    const ticket = (await screen.findByRole("heading", { name: /new paper trade/i })).closest("div.glass-card");
    within(ticket)
      .getAllByRole("spinbutton")
      .forEach((field) => expect(field).toHaveAccessibleName());
    expect(within(ticket).getByRole("combobox")).toHaveAccessibleName(/setup type/i);
  });

  it("gives the primary actions accessible names", async () => {
    stubAccount();
    await renderPaperTrading();

    expect(screen.getByRole("button", { name: /new paper trade/i })).toHaveAccessibleName();
    expect(screen.getByRole("button", { name: /reset capital/i })).toHaveAccessibleName();
  });
});

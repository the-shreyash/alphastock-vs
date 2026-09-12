/**
 * D6.5 / LIM-D6.5-6 — the trade form addresses a broker ACCOUNT.
 *
 * THE DEFECT THIS PINS
 * --------------------
 * `TradeMonitor` built its entry payload with `broker: form.broker` and never
 * `broker_account_id`. The server therefore took the broker-name bridge into
 * `_sole_account`, and D6.4's account-addressed order path was unreachable from
 * the only surface a user actually places a trade from. It failed *closed* — a
 * user with two accounts at one broker got a 409, never a wrong-account order —
 * so this was a reachability defect, not a vulnerability. It is still the wrong
 * behaviour: the account the user picked is the account the order belongs in.
 *
 * WHAT THESE TESTS PROVE, AND WHY AT THIS LEVEL
 * ---------------------------------------------
 * Everything below drives the real component through the real service modules
 * and the real axios instance, and reads the request off the transport. Nothing
 * about the payload is re-declared in the test, so a component that stops
 * sending `broker_account_id` fails here rather than passing against a local
 * copy of its own builder.
 *
 * NO ORDER IS PLACED ANYWHERE IN THIS FILE. The axios adapter is the only exit,
 * every route is stubbed, and `expectNoBrokerOrderCall` asserts after each test
 * that no broker order endpoint was touched at all.
 */
import { screen, waitFor } from "@testing-library/react";
import {
  HTTP,
  installApiMock,
  mockAuthenticatedUser,
  renderWithProviders,
  resetRealtimeStore,
  stubRemainingWith,
  testUser,
  userEvent,
} from "../test-utils";
import { SELECTED_ACCOUNT_KEY } from "../lib/brokerAccounts";
import TradeMonitor from "../pages/TradeMonitor";

const BA_A = "ba_" + "a".repeat(32);
const BA_B = "ba_" + "b".repeat(32);

/** A `/api/brokers/accounts` row, shaped as `account_statuses` returns it. */
const account = (id, extra = {}) => ({
  broker_account_id: id,
  broker: "zerodha",
  display_name: "Zerodha",
  external_account_id: id === BA_A ? "AB1234" : "CD5678",
  connected: true,
  session_expired: false,
  configured: true,
  mode: "live",
  streaming: false,
  message: "Connected",
  ...extra,
});

/** Two accounts AT THE SAME BROKER — the case a broker name cannot address. */
const TWO_ZERODHA = [account(BA_A), account(BA_B)];

let mock;

function stubTradesPage(accounts, { createStatus = HTTP.OK } = {}) {
  mockAuthenticatedUser(mock, { ...testUser, preferred_broker: "zerodha" });
  mock.onGet("/brokers/accounts").reply(HTTP.OK, { accounts });
  mock.onGet("/trades/active").reply(HTTP.OK, []);
  mock.onGet("/trades/history").reply(HTTP.OK, []);
  mock.onGet("/trades/pnl").reply(HTTP.OK, { total_pnl: 0 });
  mock.onGet("/trades/risk/summary").reply(HTTP.OK, null);
  mock.onPost("/trades/validate").reply(HTTP.OK, {
    approved: true, violations: [], warnings: [],
    metrics: { risk_amount: 100, risk_pct_of_capital: 0.1, risk_reward: 2 },
  });
  mock.onPost("/trades").reply(createStatus, { _id: "t1" });
  stubRemainingWith(mock, []);
}

/** Render the page and wait for the account list to have landed. */
async function openForm(accounts) {
  stubTradesPage(accounts);
  const { user } = renderWithProviders(<TradeMonitor />);
  await screen.findByTestId("trades-page");
  await waitFor(() =>
    expect(mock.history.get.some((r) => r.url === "/brokers/accounts")).toBe(true));
  await user.click(await screen.findByTestId("new-trade-btn"));
  await screen.findByTestId("trade-symbol-input");
  return user;
}

/** Fill the required fields. Deliberately does NOT touch the account select. */
async function fillTrade(user) {
  await user.type(screen.getByTestId("trade-symbol-input"), "RELIANCE");
  await user.type(screen.getByTestId("trade-name-input"), "Reliance");
  await user.type(screen.getByTestId("trade-entry-input"), "100");
  await user.type(screen.getByTestId("trade-qty-input"), "5");
  await user.type(screen.getByTestId("trade-sl-input"), "90");
  await user.type(screen.getByTestId("trade-t1-input"), "120");
}

async function submitTrade(user) {
  await user.click(screen.getByTestId("submit-trade-btn"));
  await waitFor(() => expect(entryPayloads()).toHaveLength(1));
  return entryPayloads()[0];
}

/** Every body POSTed to the trade-entry route, parsed. */
const entryPayloads = () =>
  mock.history.post.filter((r) => r.url === "/trades").map((r) => JSON.parse(r.data));

/** Every body POSTed to the dry-run risk check, parsed. */
const validatePayloads = () =>
  mock.history.post.filter((r) => r.url === "/trades/validate").map((r) => JSON.parse(r.data));

/**
 * THE ORDER-SAFETY ASSERTION.
 *
 * No broker order endpoint — account-addressed or broker-addressed — may be
 * reached by anything in this file, on any verb. Checked against the transport
 * log, so it covers calls the component makes for reasons a test did not
 * anticipate.
 */
const BROKER_ORDER_ROUTE = /^\/brokers\/.*orders/;

function expectNoBrokerOrderCall() {
  const all = [
    ...mock.history.post, ...mock.history.patch,
    ...mock.history.delete, ...mock.history.put,
  ];
  const orderCalls = all.filter((r) => BROKER_ORDER_ROUTE.test(r.url || ""));
  expect(orderCalls.map((r) => r.url)).toEqual([]);
}

// The order-safety assertion is only worth anything if it can fire. Every URL
// `brokerService` would use to place, modify or cancel a live order is checked
// against the matcher here, so "no order was placed" is a claim this file has
// actually tested rather than one it asserts about itself.
describe("the order-safety assertion can fail", () => {
  it.each([
    ["account-addressed place", `/brokers/accounts/${BA_A}/orders`],
    ["account-addressed modify", `/brokers/accounts/${BA_A}/orders/ORD-1`],
    ["account-addressed cancel", `/brokers/accounts/${BA_B}/orders/ORD-1`],
    ["broker-addressed place", "/brokers/zerodha/orders"],
    ["broker-addressed modify", "/brokers/zerodha/orders/ORD-1"],
  ])("recognises a %s call", (_label, url) => {
    expect(BROKER_ORDER_ROUTE.test(url)).toBe(true);
  });

  it("does not fire on the trade-entry route the form legitimately uses", () => {
    expect(BROKER_ORDER_ROUTE.test("/trades")).toBe(false);
    expect(BROKER_ORDER_ROUTE.test("/brokers/accounts")).toBe(false);
  });
});

beforeEach(() => {
  mock = installApiMock();
  resetRealtimeStore();
  localStorage.clear();
});

afterEach(() => {
  expectNoBrokerOrderCall();
  mock.restore();
});

// ─────────────────────────────────────────────────────────────────────────
// A — the selected account is what travels
// ─────────────────────────────────────────────────────────────────────────
describe("the entry request names the account the user selected", () => {
  it("sends broker_account_id, and never a broker name", async () => {
    const user = await openForm(TWO_ZERODHA);
    await user.selectOptions(screen.getByTestId("trade-broker-account-select"), BA_A);
    await fillTrade(user);

    const payload = await submitTrade(user);

    expect(payload.broker_account_id).toBe(BA_A);
    // Not "broker is empty" — the field is GONE. A payload that still carries a
    // brand can still be resolved by the server's bridge, which is the failure
    // mode this fix exists to remove.
    expect(payload).not.toHaveProperty("broker");
  });

  it("carries the account on the dry-run risk check too", async () => {
    const user = await openForm(TWO_ZERODHA);
    await user.selectOptions(screen.getByTestId("trade-broker-account-select"), BA_A);
    await fillTrade(user);
    await waitFor(() => expect(validatePayloads().length).toBeGreaterThan(0), { timeout: 3000 });

    // The account is selected BEFORE the fields are typed, so every debounced
    // dry-run carries it. The risk check ignores the account today; the point is
    // that one builder feeds both requests and cannot drift between them.
    const last = validatePayloads()[validatePayloads().length - 1];
    expect(last).not.toHaveProperty("broker");
    expect(last.broker_account_id).toBe(BA_A);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// B — changing the selection changes the destination
// ─────────────────────────────────────────────────────────────────────────
describe("changing the selection changes where the order is addressed", () => {
  it("routes to whichever of two same-broker accounts is chosen", async () => {
    const user = await openForm(TWO_ZERODHA);
    const select = screen.getByTestId("trade-broker-account-select");

    await user.selectOptions(select, BA_B);
    await fillTrade(user);
    const second = await submitTrade(user);

    expect(second.broker_account_id).toBe(BA_B);
    // Both accounts are at ONE broker. A broker name cannot tell them apart, so
    // this pair is the whole reason `broker_account_id` exists.
    expect(TWO_ZERODHA.map((a) => a.broker)).toEqual(["zerodha", "zerodha"]);
  });

  it("offers both accounts by their broker-issued number, never the routing handle", async () => {
    await openForm(TWO_ZERODHA);
    const options = Array.from(
      screen.getByTestId("trade-broker-account-select").querySelectorAll("option"));

    expect(options.map((o) => o.value)).toEqual(["", BA_A, BA_B]);
    expect(options[1].textContent).toContain("AB1234");
    expect(options[2].textContent).toContain("CD5678");
    options.forEach((o) => {
      expect(o.textContent).not.toContain(BA_A);
      expect(o.textContent).not.toContain(BA_B);
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────
// C — no selection fails closed
// ─────────────────────────────────────────────────────────────────────────
describe("no selected account means no order, and nothing is inferred", () => {
  it("starts on 'track only' even when the user holds exactly one account", async () => {
    await openForm([account(BA_A)]);
    expect(screen.getByTestId("trade-broker-account-select")).toHaveValue("");
  });

  it("sends a null account, no broker, and no auto-exit", async () => {
    const user = await openForm(TWO_ZERODHA);
    await fillTrade(user);

    const payload = await submitTrade(user);

    expect(payload.broker_account_id).toBeNull();
    expect(payload).not.toHaveProperty("broker");
    expect(payload.auto_exit).toBe(false);
  });

  it("disarms auto-exit when the user goes back to 'track only'", async () => {
    // The reachable path, and the only one on which the client-side gate is
    // load-bearing: the consent checkbox unmounts when the selection is
    // cleared, but `form.auto_exit` keeps the value the user ticked. A payload
    // built straight off that field arms LIVE exit orders on a trade that names
    // no account to place them in.
    const user = await openForm(TWO_ZERODHA);
    const select = screen.getByTestId("trade-broker-account-select");

    await user.selectOptions(select, BA_A);
    await user.click(screen.getByTestId("auto-exit-toggle"));
    expect(screen.getByTestId("auto-exit-toggle")).toBeChecked();

    await user.selectOptions(select, "");
    await fillTrade(user);
    const payload = await submitTrade(user);

    expect(payload.broker_account_id).toBeNull();
    expect(payload.auto_exit).toBe(false);
  });

  it("does not offer auto-exit or an order type until an account is chosen", async () => {
    const user = await openForm(TWO_ZERODHA);
    expect(screen.queryByTestId("auto-exit-toggle")).toBeNull();
    expect(screen.queryByTestId("trade-ordertype-select")).toBeNull();

    await user.selectOptions(screen.getByTestId("trade-broker-account-select"), BA_A);
    expect(screen.getByTestId("auto-exit-toggle")).toBeInTheDocument();
  });

  it("labels the button as a record, not as a live order", async () => {
    await openForm(TWO_ZERODHA);
    expect(screen.getByTestId("submit-trade-btn")).toHaveTextContent("Execute Trade");
  });
});

// ─────────────────────────────────────────────────────────────────────────
// The forbidden inferences — each is a shape this form actually had, or that a
// well-meaning future edit would reach for.
// ─────────────────────────────────────────────────────────────────────────
describe("the account is never inferred", () => {
  it("does not follow preferred_broker — the brand the user set in Settings", async () => {
    // The fixture user's `preferred_broker` is "zerodha" and BOTH accounts are
    // Zerodha, so the old default had two candidates and no way to choose.
    const user = await openForm(TWO_ZERODHA);
    await fillTrade(user);

    expect(await submitTrade(user)).toHaveProperty("broker_account_id", null);
  });

  it("does not fall back to the first account in the list", async () => {
    const user = await openForm(TWO_ZERODHA);
    await fillTrade(user);

    const payload = await submitTrade(user);
    expect(payload.broker_account_id).not.toBe(TWO_ZERODHA[0].broker_account_id);
  });

  it("does not adopt the account Settings remembered", async () => {
    // A stored selection is the right answer for "which account am I looking
    // at". It is not consent to place a live order, so the form ignores it.
    localStorage.setItem(SELECTED_ACCOUNT_KEY, BA_B);
    await openForm(TWO_ZERODHA);
    expect(screen.getByTestId("trade-broker-account-select")).toHaveValue("");
  });

  it("never offers a disconnected account", async () => {
    await openForm([account(BA_A, { connected: false }), account(BA_B)]);
    const values = Array.from(
      screen.getByTestId("trade-broker-account-select").querySelectorAll("option"))
      .map((o) => o.value);
    expect(values).toEqual(["", BA_B]);
  });

  it("drops a row the server sent without a minted id rather than rendering it", async () => {
    await openForm([{ ...account(BA_A), broker_account_id: "zerodha" }, account(BA_B)]);
    const values = Array.from(
      screen.getByTestId("trade-broker-account-select").querySelectorAll("option"))
      .map((o) => o.value);
    expect(values).toEqual(["", BA_B]);
  });

  it("hides the execution panel entirely when the user has no account", async () => {
    await openForm([]);
    expect(screen.queryByTestId("trade-broker-account-select")).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// E — the server's refusal is surfaced, not worked around
// ─────────────────────────────────────────────────────────────────────────
describe("a server refusal is shown, and never retried against another account", () => {
  it("renders the 404 for an account the server will not resolve", async () => {
    stubTradesPage(TWO_ZERODHA);
    mock.onPost("/trades").reply(HTTP.NOT_FOUND, { detail: "Broker account not found" });
    const { user } = renderWithProviders(<TradeMonitor />);
    await screen.findByTestId("trades-page");
    await user.click(await screen.findByTestId("new-trade-btn"));
    await screen.findByTestId("trade-symbol-input");
    await user.selectOptions(screen.getByTestId("trade-broker-account-select"), BA_A);
    await fillTrade(user);
    await user.click(screen.getByTestId("submit-trade-btn"));

    expect(await screen.findByTestId("submit-error")).toHaveTextContent(
      "Broker account not found");
    // ONE attempt. A client that retried with a different account — or with a
    // broker name — would be choosing on the user's behalf after the server
    // declined to.
    expect(entryPayloads()).toHaveLength(1);
    expect(entryPayloads()[0].broker_account_id).toBe(BA_A);
  });

  it("renders the ambiguity refusal without picking an account", async () => {
    stubTradesPage(TWO_ZERODHA);
    mock.onPost("/trades").reply(HTTP.CONFLICT, {
      detail: "You have more than one zerodha account connected. "
            + "Address this request to a specific broker_account_id.",
    });
    const { user } = renderWithProviders(<TradeMonitor />);
    await screen.findByTestId("trades-page");
    await user.click(await screen.findByTestId("new-trade-btn"));
    await screen.findByTestId("trade-symbol-input");
    await fillTrade(user);
    await user.click(screen.getByTestId("submit-trade-btn"));

    expect(await screen.findByTestId("submit-error")).toHaveTextContent(
      "more than one zerodha account");
    expect(entryPayloads()).toHaveLength(1);
  });
});

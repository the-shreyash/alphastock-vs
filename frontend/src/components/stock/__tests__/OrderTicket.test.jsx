/**
 * D5.19 (D-7) — the order path, and the boundary it must never cross by itself.
 *
 * WHAT WAS MISSING
 * ----------------
 * D5.18 traced this end to end and found the break was in exactly one place.
 * `POST /api/brokers/{broker}/orders` exists, is user-scoped off the JWT,
 * validates through `BrokerOrderCreate`, is broker-neutral, and the connected
 * broker declares `place_order`. `brokerService.placeOrder` exists too — and a
 * repository-wide search returned its definition and no consumer at all
 * (LIM-D5.18-2). The product had a working order API and no way for a user to
 * reach it.
 *
 * The two things that looked like order entry were not:
 *   * The "Buy" beside each Top Opportunity is a `SIGNAL_CONFIG.buy` badge — a
 *     `<span>`. It has never been a button.
 *   * TradeMonitor's New Trade modal posts to `/api/trades`, which places a
 *     live broker order **only when `data.broker` is set**, and `EMPTY_FORM.broker`
 *     is `""`. The default action journals and places nothing — which is safe,
 *     and is also why nobody noticed the gap.
 *
 * WHAT THESE TESTS ARE ACTUALLY FOR
 * ---------------------------------
 * Placing an order is the one irreversible action in this product: it spends
 * real money at a real exchange and there is no undo. So the majority of what
 * follows does not test that an order CAN be placed — that is one test. The
 * rest test that one is never placed by accident:
 *
 *   * not on mount, not on render, not on a data fetch
 *   * not by filling the form
 *   * not by a single click anywhere
 *   * never with an unselected broker (the `broker: ""` bug's shape)
 *   * never with an unstated quantity, side, or type
 *
 * The confirmation step is not UX politeness; it is the mechanism that makes
 * "no automatic order placement" and "no AI-generated order may execute without
 * explicit user confirmation" testable properties rather than intentions.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OrderTicket from "../OrderTicket";
import brokerService from "../../../services/brokerService";
import {
  renderWithProviders,
  installApiMock,
  mockAuthenticatedUser,
} from "../../../test-utils";

jest.mock("../../../services/brokerService", () => ({
  __esModule: true,
  default: {
    status: jest.fn(),
    placeOrder: jest.fn(),
  },
  brokerErrorMessage: (err, fallback) =>
    err?.response?.data?.detail || fallback,
}));

let mock;

/** `/brokers/status` as the engine returns it, keyed by broker. */
const STATUS_TWO_CONNECTED = {
  upstox: {
    broker: "upstox", display_name: "Upstox", configured: true, connected: true,
    account_id: "ABC123", streaming: true,
    capabilities: ["place_order", "orders", "holdings"],
  },
  zerodha: {
    broker: "zerodha", display_name: "Zerodha", configured: true, connected: true,
    account_id: "XY9999", streaming: false,
    capabilities: ["place_order", "orders"],
  },
  dhan: {
    broker: "dhan", display_name: "Dhan", configured: true, connected: true,
    account_id: "D-1", streaming: true,
    // No place_order — the adapter does not declare it.
    capabilities: ["holdings", "positions"],
  },
  fyers: {
    broker: "fyers", display_name: "Fyers", configured: true, connected: false,
    capabilities: ["place_order"],
  },
};

const NONE_CONNECTED = {
  upstox: { broker: "upstox", display_name: "Upstox", configured: true, connected: false, capabilities: ["place_order"] },
};

beforeEach(() => {
  mock = installApiMock();
  mockAuthenticatedUser(mock);
  brokerService.status.mockResolvedValue(STATUS_TWO_CONNECTED);
  brokerService.placeOrder.mockResolvedValue({ order_id: "250901000123", status: "COMPLETE" });
});

afterEach(() => {
  jest.clearAllMocks();
  mock.restore();
});

async function renderTicket(props = {}) {
  const utils = renderWithProviders(
    <OrderTicket symbol="RELIANCE" exchange="NSE" price={1310.5} {...props} />,
    { route: "/stock/RELIANCE" }
  );
  // Wait for the SETTLED state, not merely for the component. `order-ticket` is
  // present on the loading skeleton too, so waiting on it alone resolves before
  // `/brokers/status` has answered and every later query races the fetch.
  await waitFor(() =>
    expect(
      screen.queryByTestId("order-broker") || screen.queryByTestId("order-unavailable")
    ).toBeInTheDocument()
  );
  return utils;
}

/** Fill the form to a valid, submittable state. */
async function fillValidOrder({ broker = "upstox", quantity = "10" } = {}) {
  await userEvent.selectOptions(screen.getByTestId("order-broker"), broker);
  await userEvent.clear(screen.getByTestId("order-quantity"));
  await userEvent.type(screen.getByTestId("order-quantity"), quantity);
}

// --------------------------------------------------------------------------- //
// Nothing places an order on its own                                           //
// --------------------------------------------------------------------------- //

describe("no order is ever placed without an explicit human action", () => {
  it("places nothing on mount", async () => {
    await renderTicket();

    expect(brokerService.placeOrder).not.toHaveBeenCalled();
  });

  it("places nothing while the form is being filled", async () => {
    await renderTicket();

    await fillValidOrder();

    expect(brokerService.placeOrder).not.toHaveBeenCalled();
  });

  it("places nothing on the first click — review is a separate step", async () => {
    /**
     * The single most important assertion in this file. One click must not be
     * able to spend money, so the control that submits the form is not the
     * control that sends the order.
     */
    await renderTicket();
    await fillValidOrder();

    await userEvent.click(screen.getByTestId("order-review"));

    expect(brokerService.placeOrder).not.toHaveBeenCalled();
    expect(screen.getByTestId("order-confirm")).toBeInTheDocument();
  });

  it("places the order only after the confirmation is clicked", async () => {
    await renderTicket();
    await fillValidOrder();
    await userEvent.click(screen.getByTestId("order-review"));

    await userEvent.click(screen.getByTestId("order-confirm"));

    await waitFor(() => expect(brokerService.placeOrder).toHaveBeenCalledTimes(1));
  });

  it("abandons the order when the review is cancelled", async () => {
    await renderTicket();
    await fillValidOrder();
    await userEvent.click(screen.getByTestId("order-review"));

    await userEvent.click(screen.getByTestId("order-cancel"));

    expect(brokerService.placeOrder).not.toHaveBeenCalled();
    expect(screen.queryByTestId("order-confirm")).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// The order is fully specified — no silent defaults                            //
// --------------------------------------------------------------------------- //

describe("every order is explicit", () => {
  it("sends the selected broker, never an empty one", async () => {
    /**
     * The `EMPTY_FORM.broker = ""` shape, refused. An order with no broker is
     * not a smaller order, it is an order sent nowhere — or, worse, to a
     * default the user did not choose.
     */
    await renderTicket();
    await fillValidOrder({ broker: "zerodha" });
    await userEvent.click(screen.getByTestId("order-review"));
    await userEvent.click(screen.getByTestId("order-confirm"));

    await waitFor(() => expect(brokerService.placeOrder).toHaveBeenCalled());
    const [broker, payload] = brokerService.placeOrder.mock.calls[0];
    expect(broker).toBe("zerodha");
    expect(broker).toBeTruthy();
    expect(payload.symbol).toBe("RELIANCE");
  });

  it("cannot be reviewed until a broker is chosen", async () => {
    await renderTicket();
    await userEvent.clear(screen.getByTestId("order-quantity"));
    await userEvent.type(screen.getByTestId("order-quantity"), "10");

    expect(screen.getByTestId("order-review")).toBeDisabled();
  });

  it("does not preselect a broker for the user", async () => {
    await renderTicket();

    expect(screen.getByTestId("order-broker")).toHaveValue("");
  });

  it("sends the stated side, quantity and order type", async () => {
    await renderTicket();
    await userEvent.selectOptions(screen.getByTestId("order-broker"), "upstox");
    await userEvent.click(screen.getByTestId("order-side-SELL"));
    await userEvent.clear(screen.getByTestId("order-quantity"));
    await userEvent.type(screen.getByTestId("order-quantity"), "7");
    await userEvent.click(screen.getByTestId("order-review"));
    await userEvent.click(screen.getByTestId("order-confirm"));

    await waitFor(() => expect(brokerService.placeOrder).toHaveBeenCalled());
    const [, payload] = brokerService.placeOrder.mock.calls[0];
    expect(payload).toMatchObject({
      symbol: "RELIANCE",
      exchange: "NSE",
      transaction_type: "SELL",
      quantity: 7,
      order_type: "MARKET",
    });
  });

  it("refuses a quantity of zero", async () => {
    await renderTicket();
    await fillValidOrder({ quantity: "0" });

    expect(screen.getByTestId("order-review")).toBeDisabled();
  });

  it("requires a price on a limit order", async () => {
    await renderTicket();
    await fillValidOrder();
    await userEvent.selectOptions(screen.getByTestId("order-type"), "LIMIT");

    expect(screen.getByTestId("order-review")).toBeDisabled();
  });

  it("sends the limit price when one is given", async () => {
    await renderTicket();
    await fillValidOrder();
    await userEvent.selectOptions(screen.getByTestId("order-type"), "LIMIT");
    await userEvent.type(screen.getByTestId("order-price"), "1305");
    await userEvent.click(screen.getByTestId("order-review"));
    await userEvent.click(screen.getByTestId("order-confirm"));

    await waitFor(() => expect(brokerService.placeOrder).toHaveBeenCalled());
    const [, payload] = brokerService.placeOrder.mock.calls[0];
    expect(payload.order_type).toBe("LIMIT");
    expect(payload.price).toBe(1305);
  });
});

// --------------------------------------------------------------------------- //
// The review states what will actually happen                                  //
// --------------------------------------------------------------------------- //

describe("the confirmation restates the order", () => {
  it("shows the side, quantity, symbol and broker about to be used", async () => {
    await renderTicket();
    await fillValidOrder({ broker: "zerodha", quantity: "12" });
    await userEvent.click(screen.getByTestId("order-review"));

    const review = screen.getByTestId("order-review-panel");
    expect(review).toHaveTextContent("BUY");
    expect(review).toHaveTextContent("12");
    expect(review).toHaveTextContent("RELIANCE");
    expect(review).toHaveTextContent("Zerodha");
  });

  it("warns that this is a real order", async () => {
    await renderTicket();
    await fillValidOrder();
    await userEvent.click(screen.getByTestId("order-review"));

    expect(screen.getByTestId("order-review-panel")).toHaveTextContent(/real|live/i);
  });
});

// --------------------------------------------------------------------------- //
// Broker eligibility                                                           //
// --------------------------------------------------------------------------- //

describe("only a broker that can actually place the order is offered", () => {
  it("offers connected brokers that declare place_order", async () => {
    await renderTicket();

    const options = Array.from(screen.getByTestId("order-broker").options).map((o) => o.value);
    expect(options).toContain("upstox");
    expect(options).toContain("zerodha");
  });

  it("omits a connected broker that does not declare place_order", async () => {
    await renderTicket();

    const options = Array.from(screen.getByTestId("order-broker").options).map((o) => o.value);
    expect(options).not.toContain("dhan");
  });

  it("omits a broker the user has not connected", async () => {
    await renderTicket();

    const options = Array.from(screen.getByTestId("order-broker").options).map((o) => o.value);
    expect(options).not.toContain("fyers");
  });

  it("says so honestly when no broker can trade", async () => {
    brokerService.status.mockResolvedValue(NONE_CONNECTED);
    await renderTicket();

    expect(screen.getByTestId("order-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("order-review")).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------- //
// Outcomes                                                                     //
// --------------------------------------------------------------------------- //

describe("the outcome is reported", () => {
  it("shows the broker's order id on success", async () => {
    await renderTicket();
    await fillValidOrder();
    await userEvent.click(screen.getByTestId("order-review"));
    await userEvent.click(screen.getByTestId("order-confirm"));

    await waitFor(() =>
      expect(screen.getByTestId("order-result")).toHaveTextContent("250901000123"));
  });

  it("shows a readable message when the broker rejects the order", async () => {
    brokerService.placeOrder.mockRejectedValue({
      response: { data: { detail: "Insufficient funds" } },
    });
    await renderTicket();
    await fillValidOrder();
    await userEvent.click(screen.getByTestId("order-review"));
    await userEvent.click(screen.getByTestId("order-confirm"));

    await waitFor(() =>
      expect(screen.getByTestId("order-error")).toHaveTextContent("Insufficient funds"));
  });

  it("never renders a token, key or authorization header", async () => {
    /** A broker error path is a classic place for a raw upstream payload to
     *  reach the DOM. See the PH1 security sweeps. */
    brokerService.placeOrder.mockRejectedValue({
      response: { data: { detail: "Order rejected by exchange" } },
    });
    await renderTicket();
    await fillValidOrder();
    await userEvent.click(screen.getByTestId("order-review"));
    await userEvent.click(screen.getByTestId("order-confirm"));

    await waitFor(() => expect(screen.getByTestId("order-error")).toBeInTheDocument());
    expect(document.body.textContent).not.toMatch(
      /bearer |access_token|api[_-]?key|authorization|password|secret/i
    );
  });

  it("does not resend the order while one is in flight", async () => {
    let resolve;
    brokerService.placeOrder.mockReturnValue(new Promise((r) => { resolve = r; }));
    await renderTicket();
    await fillValidOrder();
    await userEvent.click(screen.getByTestId("order-review"));

    await userEvent.click(screen.getByTestId("order-confirm"));
    await userEvent.click(screen.getByTestId("order-confirm"));

    expect(brokerService.placeOrder).toHaveBeenCalledTimes(1);
    resolve({ order_id: "1", status: "COMPLETE" });
  });
});

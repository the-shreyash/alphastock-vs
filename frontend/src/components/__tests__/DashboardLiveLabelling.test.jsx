/**
 * D5.16 §8 — what the dashboard is allowed to claim is live.
 *
 * D5.16 makes NSE/BSE **cash equities** broker-powered and deliberately does not
 * touch indices, commodities or currency: those need a different instrument
 * segment at every broker, no adapter carries them today, and Phase 8 defers the
 * work rather than faking it.
 *
 * That deferral creates a specific way to mislead. The feed-state indicator
 * (D5.14) reads `Live` when the account is on a streaming feed, and it sits on
 * the same page as a Gold price that was fetched once on mount from a delayed
 * provider and has not moved since. Nothing false is *stated*, and a user would
 * still be right to conclude their gold price is live. This file pins the
 * labelling that makes the difference visible, so "D5.16 shipped" cannot quietly
 * mean "the whole dashboard is broker-live".
 */

import { render, screen } from "@testing-library/react";
import { CommoditiesStrip } from "../../pages/Dashboard";

const COMMODITIES = {
  gold: { value: 71234, change_pct: 0.42, available: true },
  silver: { value: 89100, change_pct: -0.2, available: true },
  crude_oil: { value: 6210, change_pct: 1.1, available: true },
  usd_inr: { value: 83.2, change_pct: 0.05, available: true },
};

test("the commodities strip states that it is not a live broker feed", () => {
  render(<CommoditiesStrip commodities={COMMODITIES} />);
  const strip = screen.getByTestId("commodities-strip");
  expect(strip).toHaveTextContent(/delayed/i);
});

test("it names no provider while doing so", () => {
  /**
   * Developer Rule 4 still applies to an honesty label. "Delayed" is a
   * freshness claim the platform already makes everywhere; "Yahoo" would be a
   * provider identity, and the fact that this surface happens to be served by
   * one provider today is not a reason to put its name on the screen.
   */
  render(<CommoditiesStrip commodities={COMMODITIES} />);
  const strip = screen.getByTestId("commodities-strip");
  for (const name of ["yahoo", "upstox", "zerodha", "angel", "fyers", "dhan", "broker"]) {
    expect(strip.textContent.toLowerCase()).not.toContain(name);
  }
});

test("it never claims to be live", () => {
  render(<CommoditiesStrip commodities={COMMODITIES} />);
  expect(screen.getByTestId("commodities-strip")).not.toHaveTextContent(/\blive\b/i);
});

test("an unavailable strip renders nothing rather than an unlabelled one", () => {
  const { container } = render(<CommoditiesStrip commodities={null} />);
  expect(container).toBeEmptyDOMElement();
});

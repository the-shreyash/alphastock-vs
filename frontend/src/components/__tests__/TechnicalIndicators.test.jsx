/**
 * D6.8-A — the third surface D5.19 did not reach (§G-6), tested at the
 * component that owns it.
 *
 * THE DEFECT THIS FILE DEFENDS AGAINST
 * ------------------------------------
 * Each row rendered `quote.rsi` straight into the DOM, and the backend
 * guaranteed a number by substituting one (`rsi = 50.0`, `volume_ratio = 1.0`,
 * `macd = 0.0`). A newly listed stock with no 14-day history therefore
 * displayed:
 *
 *     RSI (14)          50   Neutral
 *     MACD               0
 *     Volume Ratio      1x avg
 *
 * A value, a label and a verdict, in the same typeface, weight and colour as a
 * real reading, none of it measured. That is ADR-058's rule broken in the place
 * it is least visible — not in a generated sentence, but in a table cell that
 * looks like every other table cell.
 *
 * Testing it here rather than only through the page is deliberate: this
 * component is now the single implementation (the page used to carry an inline
 * copy beside it), so this is the layer that makes the guarantee and therefore
 * the layer that should fail if the guarantee breaks.
 */
import { render, screen } from "@testing-library/react";

import TechnicalIndicators from "../stock/TechnicalIndicators";

// framer-motion animates on mount; the readout is what is under test.
jest.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => ({ children, ...p }) => <div {...p}>{children}</div> }),
}));

const MEASURED = {
  rsi: 62.4,
  macd: 3.1,
  macd_signal: 1.2,
  volume_ratio: 1.8,
};

describe("TechnicalIndicators", () => {
  it("renders a real reading as its number", () => {
    render(<TechnicalIndicators quote={MEASURED} />);

    expect(screen.getByText("62.4")).toBeInTheDocument();
    expect(screen.getByText("3.1")).toBeInTheDocument();
    expect(screen.getByText("1.8x avg")).toBeInTheDocument();
  });

  it("renders the RSI verdict only from a real reading", () => {
    render(<TechnicalIndicators quote={MEASURED} />);

    expect(screen.getByText("Neutral")).toBeInTheDocument();
  });

  it("shows an em-dash instead of a number the platform does not have", () => {
    render(
      <TechnicalIndicators
        quote={{
          rsi: null,
          macd: null,
          macd_signal: null,
          volume_ratio: null,
          field_quality: {
            rsi: "insufficient_history",
            macd: "insufficient_history",
            macd_signal: "insufficient_history",
            volume_ratio: "unavailable",
          },
        }}
      />
    );

    expect(screen.getAllByText("—")).toHaveLength(4);
    expect(screen.queryByText("50")).not.toBeInTheDocument();
    expect(screen.queryByText("1x avg")).not.toBeInTheDocument();
    // The template literal used to render this string once the substitution
    // was removed from the backend — the failure mode that is worse than the
    // fabrication, because it is visible and meaningless.
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
  });

  it("withholds the RSI verdict when there is no RSI", () => {
    // "Neutral" derived from a value we do not have is the same fabricated
    // claim as the number itself, one column to the right.
    render(
      <TechnicalIndicators
        quote={{ rsi: null, field_quality: { rsi: "insufficient_history" } }}
      />
    );

    expect(screen.queryByText("Neutral")).not.toBeInTheDocument();
    expect(screen.queryByText("Overbought")).not.toBeInTheDocument();
    expect(screen.queryByText("Oversold")).not.toBeInTheDocument();
  });

  it("explains why a reading is absent, in the platform's words", () => {
    render(
      <TechnicalIndicators
        quote={{ rsi: null, field_quality: { rsi: "insufficient_history" } }}
      />
    );

    expect(
      screen.getByTitle(/not enough price history/i)
    ).toBeInTheDocument();
  });

  it("withholds a stale reading even though it is a real number", () => {
    // The state that did not exist before D6.8-A, and the dangerous one: the
    // number is real, so nothing on this side could ever have told.
    render(
      <TechnicalIndicators
        quote={{ ...MEASURED, field_quality: { rsi: "stale" } }}
      />
    );

    expect(screen.queryByText("62.4")).not.toBeInTheDocument();
    expect(screen.getByTitle(/out of date/i)).toBeInTheDocument();
    // …and the fields that are NOT stale are untouched, so this is a per-field
    // decision and not a whole-card blackout.
    expect(screen.getByText("3.1")).toBeInTheDocument();
  });

  it("keeps the label and the row so the absence is noticed, not hidden", () => {
    render(<TechnicalIndicators quote={{ rsi: null }} />);

    expect(screen.getByText("RSI (14)")).toBeInTheDocument();
    expect(screen.getByText("MACD Signal")).toBeInTheDocument();
  });

  it("treats an unrecognised state as absent rather than available", () => {
    // The safe direction to be wrong in is DOWN. A declaration this build does
    // not recognise must not render as a measurement just because a number
    // happens to sit beside it — that would upgrade an unknown verdict to the
    // strongest one available.
    render(
      <TechnicalIndicators
        quote={{ rsi: 62.4, field_quality: { rsi: "served_by_a_broker" } }}
      />
    );

    expect(screen.queryByText("62.4")).not.toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});

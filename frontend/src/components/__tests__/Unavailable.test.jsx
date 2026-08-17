/**
 * PH3.9 — the one distinction the whole sprint rests on, tested at the
 * component that owns it.
 *
 * `MetricValue` is the single place in the frontend that decides whether a
 * number is present. Every admin page routes through it precisely so that the
 * `null` vs `0` rule is expressed once instead of at thirty call sites where
 * one of them will eventually be written as `value || 0` — which renders `₹0`
 * in the same weight and colour as a measured figure and silently undoes the
 * backend contract.
 *
 * Testing it here rather than only through the pages is deliberate: this is the
 * layer that makes the guarantee, so this is the layer that should fail if the
 * guarantee breaks.
 */
import { render, screen } from "@testing-library/react";
import { MetricValue, Unavailable, UnavailablePanel } from "../ui/Unavailable";

describe("MetricValue", () => {
  it("renders zero as zero", () => {
    // "Nobody signed up today" is a measurement. Suppressing it would be the
    // same defect as fabricating one, pointed the other way.
    render(<MetricValue value={0} />);
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("renders a formatted zero, not an em-dash", () => {
    render(<MetricValue value={0} format={(v) => `₹${v}`} />);
    expect(screen.getByText("₹0")).toBeInTheDocument();
  });

  it("renders null as unavailable", () => {
    render(<MetricValue value={null} reason="No payment integration." />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("renders undefined as unavailable", () => {
    // A field the backend omitted entirely must not become a zero either — an
    // older or partially-deployed backend is exactly when this matters.
    render(<MetricValue value={undefined} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("never applies the formatter to an absent value", () => {
    // `format` is written assuming a value is present, so calling it with null
    // is how `₹null` or a TypeError reaches the page.
    const format = jest.fn((v) => `₹${v}`);
    render(<MetricValue value={null} format={format} />);
    expect(format).not.toHaveBeenCalled();
  });

  it("renders a false value rather than treating it as missing", () => {
    // `false` is falsy but present. A `value || fallback` implementation fails
    // this; the explicit null/undefined check does not.
    render(<MetricValue value={false} format={(v) => String(v)} />);
    expect(screen.getByText("false")).toBeInTheDocument();
  });
});

describe("Unavailable", () => {
  it("carries the backend's reason as an accessible name", () => {
    // A bare em-dash leaves an operator unable to tell "no payment integration"
    // from "session records are reaped after 7 days" — different problems with
    // different owners. It also has to reach a screen-reader user, for whom a
    // hover tooltip does not exist.
    render(<Unavailable reason="Sessions are retained for 7 days." />);
    expect(screen.getByLabelText(/retained for 7 days/i)).toBeInTheDocument();
  });

  it("falls back to a generic explanation rather than nothing", () => {
    render(<Unavailable />);
    expect(screen.getByLabelText(/not available/i)).toBeInTheDocument();
  });
});

describe("UnavailablePanel", () => {
  it("states the reason and what would be needed", () => {
    render(
      <UnavailablePanel
        title="Revenue reporting is not available"
        reason="No payment integration."
        requiredSource="Captured payment records."
      />
    );
    expect(screen.getByRole("note")).toHaveTextContent("No payment integration.");
    expect(screen.getByRole("note")).toHaveTextContent("Captured payment records.");
  });

  it("renders without a required source", () => {
    render(<UnavailablePanel title="No data yet" />);
    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });
});

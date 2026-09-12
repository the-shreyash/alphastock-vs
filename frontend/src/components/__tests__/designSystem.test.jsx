/**
 * The design-system component contracts.
 *
 * These components exist to make one product decision in one place, so the
 * tests here assert the *decisions*, not the markup: that a flat market is not
 * painted green, that a missing metric is not rendered as zero, that the tab
 * row is operable from the keyboard. Snapshotting the DOM would lock in the
 * styling and prove none of it.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DeltaValue from "../ds/DeltaValue";
import StatusBadge from "../ds/StatusBadge";
import PageHeader from "../ds/PageHeader";
import MetricCard from "../ds/MetricCard";
import EmptyState from "../ds/EmptyState";
import SegmentedTabs, { TabPanel } from "../ds/SegmentedTabs";
import { Card, CardHeader } from "../ds/Card";

/*
 * A NOTE ON ASSERTING COLOUR
 * --------------------------
 * jsdom drops `var()` values on the floor: an element styled
 * `color: var(--gain)` reports no style attribute at all. `toHaveStyle({color:
 * "var(--gain)"})` therefore matches *anything*, including an element painted
 * red — the assertion cannot fail, so it proves nothing. That is why these
 * tests read `data-direction` / `data-tone`, which carry the same decision in a
 * form the DOM actually retains.
 */
describe("DeltaValue", () => {
  it("marks a rise as up and a fall as down", () => {
    const { rerender, container } = render(<DeltaValue percent={1.28} />);
    expect(container.firstChild).toHaveAttribute("data-direction", "up");
    expect(screen.getByText("+1.28%")).toBeInTheDocument();

    rerender(<DeltaValue percent={-0.42} />);
    expect(container.firstChild).toHaveAttribute("data-direction", "down");
    expect(screen.getByText("-0.42%")).toBeInTheDocument();
  });

  it("treats an unchanged instrument as flat, not as a gain", () => {
    /**
     * The idiom this component replaced was `change >= 0 ? green : red`, which
     * paints a stock that has not moved all session bright green with an
     * up-arrow. That is a false statement about the market, and it appeared on
     * every card that used the idiom.
     */
    const { container } = render(<DeltaValue percent={0} />);
    expect(container.firstChild).toHaveAttribute("data-direction", "flat");
    // No direction arrow either — there is no direction.
    expect(container.querySelector("svg")).toBeNull();
  });

  it("renders a missing change as unavailable rather than as +0.00%", () => {
    render(<DeltaValue percent={null} absolute={null} reason="Feed offline" />);
    expect(screen.queryByText(/0\.00%/)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Change not available: Feed offline/i)).toBeInTheDocument();
  });

  it("signs the absolute tail to match the percentage", () => {
    render(<DeltaValue percent={1.28} absolute={36.2} />);
    expect(screen.getByText("(+36.20)")).toBeInTheDocument();
  });

  it("omits the absolute tail when compact", () => {
    render(<DeltaValue percent={1.28} absolute={36.2} compact />);
    expect(screen.queryByText(/\(/)).not.toBeInTheDocument();
  });
});

describe("StatusBadge", () => {
  it("resolves a semantic tone", () => {
    const { container, rerender } = render(<StatusBadge tone="positive">Live</StatusBadge>);
    expect(container.firstChild).toHaveAttribute("data-tone", "positive");

    rerender(<StatusBadge tone="warning">Degraded</StatusBadge>);
    expect(container.firstChild).toHaveAttribute("data-tone", "warning");
  });

  it("falls back to neutral for an unknown tone rather than rendering unstyled", () => {
    // Without the fallback the badge destructures `undefined` and throws, or
    // renders with no colours at all. Neutral is the safe, legible default.
    const { container } = render(<StatusBadge tone="chartreuse">Odd</StatusBadge>);
    expect(container.firstChild).toHaveAttribute("data-tone", "neutral");
    expect(screen.getByText("Odd")).toBeInTheDocument();
  });
});

describe("PageHeader", () => {
  it("renders the title as the page's h1 so the document outline is usable", () => {
    render(<PageHeader title="Your Portfolio" subtitle="Track your investments." />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Your Portfolio");
    expect(screen.getByText("Track your investments.")).toBeInTheDocument();
  });

  it("renders the actions slot", () => {
    render(<PageHeader title="Markets" actions={<button>Refresh</button>} />);
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
  });

  it("omits the subtitle element entirely when there is none", () => {
    const { container } = render(<PageHeader title="Settings" />);
    expect(container.querySelector(".page-subtitle")).toBeNull();
  });
});

describe("MetricCard", () => {
  it("renders a missing value as unavailable, never as zero", () => {
    render(<MetricCard label="Today's P&L" value={null} reason="Broker disconnected" />);
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Today's P&L not available: Broker disconnected/i)).toBeInTheDocument();
  });

  it("renders zero as a real measured value", () => {
    // Zero P&L is a fact, not an absence — the distinction the platform's
    // Unavailable contract exists to preserve.
    render(<MetricCard label="Today's P&L" value="₹0.00" />);
    expect(screen.getByText("₹0.00")).toBeInTheDocument();
  });

  it("becomes a button only when it has an action", () => {
    const { rerender } = render(<MetricCard label="NIFTY 50" value="24,810" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();

    rerender(<MetricCard label="NIFTY 50" value="24,810" onClick={() => {}} aria-label="NIFTY 50 details" />);
    expect(screen.getByRole("button", { name: "NIFTY 50 details" })).toBeInTheDocument();
  });

  it("shows a skeleton instead of an empty card while loading", () => {
    const { container } = render(<MetricCard label="NIFTY 50" loading />);
    expect(container.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
    expect(screen.queryByText("NIFTY 50")).not.toBeInTheDocument();
  });
});

describe("SegmentedTabs", () => {
  const TABS = [
    { key: "overview", label: "Overview" },
    { key: "scanner", label: "Scanner" },
    { key: "rankings", label: "Rankings" },
  ];

  function Harness({ initial = "overview" }) {
    const [value, setValue] = require("react").useState(initial);
    return (
      <>
        <SegmentedTabs label="Market views" tabs={TABS} value={value} onChange={setValue} />
        <TabPanel tabKey="overview" value={value}>Overview panel</TabPanel>
        <TabPanel tabKey="scanner" value={value}>Scanner panel</TabPanel>
        <TabPanel tabKey="rankings" value={value}>Rankings panel</TabPanel>
      </>
    );
  }

  it("exposes the WAI-ARIA tabs structure", () => {
    render(<Harness />);
    const list = screen.getByRole("tablist", { name: "Market views" });
    expect(within(list).getAllByRole("tab")).toHaveLength(3);
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Scanner" })).toHaveAttribute("aria-selected", "false");
  });

  it("keeps only the active tab in the tab order", () => {
    // The pattern that makes a long tab row usable with a keyboard: one stop
    // for the whole row, then arrows within it.
    render(<Harness />);
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Scanner" })).toHaveAttribute("tabindex", "-1");
  });

  it("moves between tabs with the arrow keys, wrapping at the ends", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    screen.getByRole("tab", { name: "Overview" }).focus();

    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Scanner" })).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Rankings" })).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  });

  it("renders only the selected panel, wired to its tab", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.getByText("Overview panel")).toBeInTheDocument();
    expect(screen.queryByText("Scanner panel")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Scanner" }));
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveTextContent("Scanner panel");
    expect(panel).toHaveAttribute("aria-labelledby", "tab-scanner");
  });

  it("skips disabled tabs when arrowing", async () => {
    const user = userEvent.setup();
    const tabs = [
      { key: "a", label: "A" },
      { key: "b", label: "B", disabled: true },
      { key: "c", label: "C" },
    ];
    function H() {
      const [v, setV] = require("react").useState("a");
      return <SegmentedTabs label="t" tabs={tabs} value={v} onChange={setV} />;
    }
    render(<H />);
    screen.getByRole("tab", { name: "A" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "C" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("EmptyState", () => {
  it("always explains itself and can offer a way forward", () => {
    render(
      <EmptyState
        title="Your watchlist is empty"
        description="Search for a stock above to start tracking it."
        action={<button>Add a stock</button>}
      />
    );
    expect(screen.getByText("Your watchlist is empty")).toBeInTheDocument();
    expect(screen.getByText(/Search for a stock above/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a stock" })).toBeInTheDocument();
  });
});

describe("Card", () => {
  it("uses the platform card surface so it cannot drift from hand-written cards", () => {
    const { container } = render(<Card>content</Card>);
    expect(container.firstChild).toHaveClass("glass-card");
  });

  it("only animates on hover when it is itself a click target", () => {
    const { container, rerender } = render(<Card>plain</Card>);
    expect(container.firstChild).not.toHaveClass("sa-card-interactive");

    rerender(<Card interactive>clickable</Card>);
    expect(container.firstChild).toHaveClass("sa-card-interactive");
  });

  it("gives CardHeader a real heading element at the requested level", () => {
    render(<CardHeader title="Sector Performance" headingLevel={2} subtitle="Today" />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Sector Performance");
    expect(screen.getByText("Today")).toBeInTheDocument();
  });
});

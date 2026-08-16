/**
 * PH3.7 — the error boundary: containment, reporting, and what the user sees.
 *
 * React logs every caught error to `console.error` from inside its own
 * internals, which floods the output and makes a real failure hard to spot. It
 * is silenced per-test rather than globally so a genuinely unexpected console
 * error still shows up somewhere.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import ErrorBoundary from "@/components/ErrorBoundary";
import { resetTelemetryForTests, recentReports } from "@/services/telemetry";

function Boom({ error }) {
  throw error;
}

function renderBoundary(ui, props = {}) {
  const spy = jest.spyOn(console, "error").mockImplementation(() => {});
  const result = render(<ErrorBoundary {...props}>{ui}</ErrorBoundary>);
  spy.mockRestore();
  return result;
}

beforeEach(() => {
  resetTelemetryForTests();
  navigator.sendBeacon = jest.fn(() => true);
  sessionStorage.clear();
});

describe("ErrorBoundary — containment", () => {
  it("renders its children when nothing throws", () => {
    render(
      <ErrorBoundary>
        <p>healthy content</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("healthy content")).toBeInTheDocument();
  });

  it("shows a recovery screen instead of unmounting the tree", () => {
    // Since React 16 an uncaught render error unmounts the ENTIRE tree — a
    // white page, with the cause visible only in a console the user will never
    // open. This is the assertion that the app no longer does that.
    renderBoundary(<Boom error={new TypeError("x is undefined")} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
  });

  it("announces the failure to assistive technology", () => {
    renderBoundary(<Boom error={new Error("boom")} />);
    // role="alert" is what makes a screen reader speak the failure; without it
    // the page silently changes and a non-sighted user learns nothing.
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("offers a way forward", () => {
    renderBoundary(<Boom error={new Error("boom")} />);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reload page/i })).toBeInTheDocument();
  });

  it("re-renders its children when the user retries", () => {
    let shouldThrow = true;
    function Flaky() {
      if (shouldThrow) throw new Error("transient");
      return <p>recovered</p>;
    }
    renderBoundary(<Flaky />);
    expect(screen.getByRole("alert")).toBeInTheDocument();

    shouldThrow = false;
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(screen.getByText("recovered")).toBeInTheDocument();
  });
});

describe("ErrorBoundary — reporting", () => {
  it("reports a render error", () => {
    renderBoundary(<Boom error={new TypeError("x is undefined")} />);
    const [report] = recentReports();
    expect(report).toMatchObject({ kind: "render", name: "TypeError" });
  });

  it("reports a chunk-load failure under its own kind", () => {
    // Separated so a release does not page anyone: a burst of these right after
    // a deploy is users holding a stale index.html, not a regression.
    renderBoundary(<Boom error={new Error("Loading chunk 3 failed.")} />);
    expect(recentReports()[0].kind).toBe("chunk_load");
  });
});

describe("ErrorBoundary — what the user is shown", () => {
  const originalEnv = process.env.NODE_ENV;
  afterEach(() => {
    process.env.NODE_ENV = originalEnv;
  });

  it("shows the message in development", () => {
    process.env.NODE_ENV = "development";
    renderBoundary(<Boom error={new Error("useSelector is not a function")} />);
    expect(screen.getByText(/useSelector is not a function/)).toBeInTheDocument();
  });

  it("withholds the message in production", () => {
    // A React error message can quote component props, which in this
    // application means positions, prices and account values — so a screenshot
    // of a crash would otherwise be a leak.
    process.env.NODE_ENV = "production";
    renderBoundary(
      <Boom error={new Error("cannot read holdings of {qty: 500, avgPrice: 2431.55}")} />,
    );
    expect(screen.queryByText(/2431.55/)).not.toBeInTheDocument();
    expect(screen.queryByText(/holdings/)).not.toBeInTheDocument();
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
  });

  it("never renders a stack trace", () => {
    process.env.NODE_ENV = "production";
    const error = new Error("boom");
    error.stack = "Error: boom\n    at /src/pages/Portfolio.jsx:42:11";
    const { container } = renderBoundary(<Boom error={error} />);
    expect(container.textContent).not.toContain("Portfolio.jsx");
  });
});

describe("ErrorBoundary — chunk-load auto-recovery", () => {
  const originalLocation = window.location;
  beforeEach(() => {
    delete window.location;
    window.location = { ...originalLocation, pathname: "/dashboard", reload: jest.fn() };
  });
  afterEach(() => {
    window.location = originalLocation;
  });

  it("reloads once on a stale-bundle failure", () => {
    renderBoundary(<Boom error={new Error("Loading chunk 12 failed.")} />);
    expect(window.location.reload).toHaveBeenCalledTimes(1);
  });

  it("does not reload a second time", () => {
    // The guard that matters. If the reload does not fix it — a genuinely
    // broken deploy, or a proxy serving a bad cache — reloading again is an
    // infinite refresh against a failing origin, from every affected browser
    // at once.
    renderBoundary(<Boom error={new Error("Loading chunk 12 failed.")} />);
    window.location.reload.mockClear();
    renderBoundary(<Boom error={new Error("Loading chunk 12 failed.")} />);
    expect(window.location.reload).not.toHaveBeenCalled();
  });

  it("never auto-reloads on an ordinary render error", () => {
    renderBoundary(<Boom error={new TypeError("x is undefined")} />);
    expect(window.location.reload).not.toHaveBeenCalled();
  });
});

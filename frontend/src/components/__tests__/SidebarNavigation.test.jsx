/**
 * Primary navigation structure.
 *
 * The rail was restructured from sixteen flat entries to nine top-level
 * sections with their views nested underneath. The risk in that change is not
 * visual — it is that a page quietly becomes unreachable, or that a user who
 * deep-links into a nested page (a bookmarked /sip, an emailed /journal link)
 * lands somewhere the rail cannot explain.
 *
 * So these tests assert reachability and orientation, not appearance.
 */
import { screen, waitFor, within } from "@testing-library/react";
import Sidebar, { NAV_PATHS } from "../layout/Sidebar";
import {
  renderWithProviders,
  installApiMock,
  mockAuthenticatedUser,
  mockAdminUser,
  resetRealtimeStore,
} from "../../test-utils";

let mock;

beforeEach(() => {
  mock = installApiMock();
  resetRealtimeStore();
});

afterEach(() => mock.restore());

/** Render the rail pinned open at `route`, with a signed-in user. */
async function renderSidebar(route = "/dashboard", { admin = false } = {}) {
  (admin ? mockAdminUser : mockAuthenticatedUser)(mock);
  const utils = renderWithProviders(
    <Sidebar collapsed={false} setCollapsed={() => {}} />,
    { route }
  );
  await screen.findByTestId("sidebar");
  return utils;
}

/** The nine sections the product presents at the top level. */
const TOP_LEVEL = [
  "Dashboard", "Markets", "AI Insights", "Watchlist",
  "Portfolio", "Trading", "Research", "News", "Settings",
];

const tid = (label) => `nav-${label.toLowerCase().replace(/\s/g, "-")}`;

describe("the rail's top level", () => {
  it("presents exactly the nine product sections to a normal user", async () => {
    await renderSidebar();
    const nav = screen.getByRole("navigation", { name: "Primary" });

    for (const label of TOP_LEVEL) {
      expect(within(nav).getByTestId(tid(label))).toBeInTheDocument();
    }
    expect(within(nav).queryByTestId(tid("Admin Portal"))).not.toBeInTheDocument();
  });

  it("adds the admin portal only for an admin", async () => {
    await renderSidebar("/dashboard", { admin: true });
    await waitFor(() =>
      expect(screen.getByTestId(tid("Admin Portal"))).toBeInTheDocument());
  });
});

describe("no page was lost in the restructure", () => {
  it("still reaches every page the old sixteen-entry rail reached", () => {
    /**
     * The exact set the flat rail linked to before the change. If a future
     * edit drops one of these from the rail — or moves it somewhere the rail
     * no longer links — this fails rather than the page silently becoming
     * findable only by typing its URL.
     */
    const PREVIOUSLY_REACHABLE = [
      "/dashboard", "/markets", "/watchlist", "/picks", "/morning-report",
      "/assistant", "/advisor", "/sip", "/portfolio", "/trades",
      "/paper-trading", "/journal", "/news", "/admin", "/settings",
    ];
    for (const path of PREVIOUSLY_REACHABLE) {
      expect(NAV_PATHS).toContain(path);
    }
    // Backtesting was only reachable by URL before; it is now the Research
    // section, so the rail covers strictly more than it used to.
    expect(NAV_PATHS).toContain("/backtesting");
  });

  it("links each path exactly once, so no page has two homes", () => {
    expect(new Set(NAV_PATHS).size).toBe(NAV_PATHS.length);
  });
});

describe("nested views appear when the user is inside their section", () => {
  it("keeps a section's views out of the way while the user is elsewhere", async () => {
    await renderSidebar("/dashboard");
    expect(screen.queryByTestId(tid("Stock Scanner"))).not.toBeInTheDocument();
    expect(screen.queryByTestId(tid("SIP Advisor"))).not.toBeInTheDocument();
    expect(screen.queryByTestId(tid("Paper Trading"))).not.toBeInTheDocument();
  });

  it("reveals a section's views when the user opens that section", async () => {
    await renderSidebar("/markets");
    expect(screen.getByTestId(tid("Stock Scanner"))).toBeInTheDocument();
    expect(screen.getByTestId(tid("Morning Report"))).toBeInTheDocument();
    // ...and only that section's.
    expect(screen.queryByTestId(tid("SIP Advisor"))).not.toBeInTheDocument();
  });

  it("reveals the section when the user deep-links straight to a nested page", async () => {
    /**
     * THE ORIENTATION BUG THIS PREVENTS.
     *
     * A bookmarked /sip is a page with no parent visible: if expansion were
     * driven by the *parent* route alone, the user would land on SIP Advisor
     * with "AI Insights" collapsed and no indication of where they were. The
     * section must open when any of its children is the current page.
     */
    await renderSidebar("/sip");
    expect(screen.getByTestId(tid("SIP Advisor"))).toBeInTheDocument();
    expect(screen.getByTestId(tid("Investment Advisor"))).toBeInTheDocument();
    expect(screen.getByTestId(tid("SIP Advisor"))).toHaveAttribute("aria-current", "page");
  });
});

describe("the active item is identifiable without relying on colour", () => {
  it("marks the current page with aria-current", async () => {
    await renderSidebar("/portfolio");
    expect(screen.getByTestId(tid("Portfolio"))).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId(tid("Markets"))).not.toHaveAttribute("aria-current");
  });

  it("treats the root path as the dashboard", async () => {
    await renderSidebar("/");
    expect(screen.getByTestId(tid("Dashboard"))).toHaveAttribute("aria-current", "page");
  });
});

/**
 * Routing & route guards.
 *
 * These tests drive the application's real route table (`AppRouter` from
 * App.js), not a test-local copy — so a guard deleted from the route
 * declaration fails here rather than passing against a stand-in.
 *
 * Production failures these catch: a signed-out visitor reaching the dashboard;
 * a signed-in user bounced back to the login screen; and — the one that matters
 * most — a non-admin reaching the admin portal because the AdminRoute wrapper
 * was dropped from a route.
 *
 * Guard behaviour is asserted through what the user ends up seeing, never
 * through the guards' internal state.
 */
import { screen, waitFor } from "@testing-library/react";
import {
  renderAppAt,
  installApiMock,
  stubRemainingWith,
  mockAuthenticatedUser,
  mockAdminUser,
  mockUnauthenticatedUser,
  resetRealtimeStore,
  pending,
  testSuperAdmin,
  testUser,
} from "../test-utils";

let mock;

beforeEach(() => {
  mock = installApiMock();
  resetRealtimeStore();
});

afterEach(() => {
  mock.restore();
});

/**
 * Pages fan out to many endpoints on mount. Routing tests care only about
 * *which page* renders, so everything past the auth probe resolves to an empty
 * payload. Specific stubs registered earlier still win.
 */
const stubPageData = () => stubRemainingWith(mock, []);

const findLogin = () => screen.findByTestId("login-page", {}, { timeout: 5000 });

describe("public routes", () => {
  it("shows the landing page to a signed-out visitor", async () => {
    mockUnauthenticatedUser(mock);
    stubPageData();

    renderAppAt("/");

    expect(await screen.findByRole("heading", { level: 1 }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByTestId("app-layout")).not.toBeInTheDocument();
  });

  it("shows the login page at /login", async () => {
    mockUnauthenticatedUser(mock);
    stubPageData();

    renderAppAt("/login");

    expect(await findLogin()).toBeInTheDocument();
  });

  it("shows the registration page at /register", async () => {
    mockUnauthenticatedUser(mock);
    stubPageData();

    renderAppAt("/register");

    expect(await screen.findByTestId("register-page", {}, { timeout: 5000 })).toBeInTheDocument();
  });

  it("sends an already signed-in user from /login to the dashboard", async () => {
    mockAuthenticatedUser(mock);
    stubPageData();

    renderAppAt("/login");

    // The dashboard shell (Layout) is what a signed-in user must land on.
    expect(await screen.findByTestId("app-layout", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByTestId("login-page")).not.toBeInTheDocument();
  });
});

describe("protected routes", () => {
  it("redirects a signed-out visitor from /dashboard to the login page", async () => {
    mockUnauthenticatedUser(mock);
    stubPageData();

    renderAppAt("/dashboard");

    expect(await findLogin()).toBeInTheDocument();
    expect(screen.queryByTestId("app-layout")).not.toBeInTheDocument();
  });

  it.each(["/portfolio", "/trades", "/watchlist", "/settings", "/paper-trading"])(
    "redirects a signed-out visitor from %s to the login page",
    async (route) => {
      mockUnauthenticatedUser(mock);
      stubPageData();

      renderAppAt(route);

      expect(await findLogin()).toBeInTheDocument();
    },
  );

  it("renders the application shell for a signed-in user", async () => {
    mockAuthenticatedUser(mock);
    stubPageData();

    renderAppAt("/dashboard");

    expect(await screen.findByTestId("app-layout", {}, { timeout: 5000 })).toBeInTheDocument();
  });

  it("waits instead of redirecting while the session check is still in flight", async () => {
    // The bug this guards against: a guard that treats "not yet known" as
    // "signed out" flashes every returning user through the login screen on
    // every hard refresh.
    mock.onGet("/auth/me").reply(() => pending());
    stubPageData();

    renderAppAt("/dashboard");

    await waitFor(() => expect(screen.getByText(/loading/i)).toBeInTheDocument());
    expect(screen.queryByTestId("login-page")).not.toBeInTheDocument();
  });
});

describe("admin routes", () => {
  it("redirects a signed-out visitor from /admin to the login page", async () => {
    mockUnauthenticatedUser(mock);
    stubPageData();

    renderAppAt("/admin/dashboard");

    expect(await findLogin()).toBeInTheDocument();
  });

  it("redirects an authenticated non-admin away from the admin portal", async () => {
    mockAuthenticatedUser(mock, testUser); // role: "user"
    stubPageData();

    renderAppAt("/admin/dashboard");

    // Bounced into the normal app shell, never into the admin layout.
    expect(await screen.findByTestId("app-layout", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByText("Admin Portal")).not.toBeInTheDocument();
  });

  it.each(["/admin/users", "/admin/payments", "/admin/logs", "/admin/feature-flags"])(
    "redirects an authenticated non-admin away from %s",
    async (route) => {
      mockAuthenticatedUser(mock, testUser);
      stubPageData();

      renderAppAt(route);

      expect(await screen.findByTestId("app-layout", {}, { timeout: 5000 })).toBeInTheDocument();
      expect(screen.queryByText("Admin Portal")).not.toBeInTheDocument();
    },
  );

  it("admits a user with the admin role", async () => {
    mockAdminUser(mock);
    stubPageData();

    renderAppAt("/admin/dashboard");

    // The admin shell — reached only through AdminRoute.
    expect(await screen.findAllByText("Admin Portal", {}, { timeout: 5000 })).not.toHaveLength(0);
    expect(screen.queryByTestId("app-layout")).not.toBeInTheDocument();
  });

  it("admits a user with the super_admin role", async () => {
    mockAdminUser(mock, testSuperAdmin);
    stubPageData();

    renderAppAt("/admin/dashboard");

    expect(await screen.findAllByText("Admin Portal", {}, { timeout: 5000 })).not.toHaveLength(0);
  });
});

describe("unknown routes", () => {
  it("sends a signed-out visitor on an unknown path to the landing page", async () => {
    mockUnauthenticatedUser(mock);
    stubPageData();

    renderAppAt("/this-route-does-not-exist");

    expect(await screen.findByRole("heading", { level: 1 }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByTestId("app-layout")).not.toBeInTheDocument();
  });

  it("sends a signed-in user on an unknown path into the application shell", async () => {
    mockAuthenticatedUser(mock);
    stubPageData();

    renderAppAt("/this-route-does-not-exist");

    expect(await screen.findByTestId("app-layout", {}, { timeout: 5000 })).toBeInTheDocument();
  });
});

/**
 * Authentication — end-to-end through the real route table.
 *
 * The component tests cover each screen in isolation; this suite covers the
 * journeys that cross them, because that is where the regressions actually
 * happen. Nothing is stubbed except the network.
 *
 * Journeys:
 *   sign in  → land in the application shell
 *   sign out → return to the login screen, credentials cleared
 *   refresh  → an existing session is restored without a login prompt
 */
import { screen, waitFor } from "@testing-library/react";
import {
  renderAppAt,
  installApiMock,
  stubRemainingWith,
  stubLocation,
  mockAuthenticatedUser,
  mockUnauthenticatedUser,
  resetRealtimeStore,
  userEvent,
  HTTP,
  testUser,
  loginResponse,
} from "../test-utils";

let mock;
let restoreLocation;

beforeEach(() => {
  mock = installApiMock();
  resetRealtimeStore();
  restoreLocation = stubLocation({ pathname: "/login" });
});

afterEach(() => {
  mock.restore();
  restoreLocation();
});

describe("signing in", () => {
  it("takes a user from the login screen into the application shell", async () => {
    const user = userEvent.setup();
    // Signed out at first; once /auth/login succeeds the app holds the user in
    // context, so no second /auth/me probe is needed to enter the shell.
    mockUnauthenticatedUser(mock);
    mock.onPost("/auth/login").reply(HTTP.OK, loginResponse());
    stubRemainingWith(mock, []);

    renderAppAt("/login");

    await user.type(await screen.findByTestId("login-email-input"), testUser.email);
    await user.type(screen.getByTestId("login-password-input"), "correct-horse-battery");
    await user.click(screen.getByTestId("login-submit-btn"));

    expect(await screen.findByTestId("app-layout", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByTestId("login-page")).not.toBeInTheDocument();
  });

  it("keeps a user with bad credentials on the login screen", async () => {
    const user = userEvent.setup();
    mockUnauthenticatedUser(mock);
    mock.onPost("/auth/login").reply(HTTP.UNAUTHORIZED, { detail: "Invalid email or password" });
    stubRemainingWith(mock, []);

    renderAppAt("/login");

    await user.type(await screen.findByTestId("login-email-input"), testUser.email);
    await user.type(screen.getByTestId("login-password-input"), "wrong-password");
    await user.click(screen.getByTestId("login-submit-btn"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(screen.getByTestId("login-page")).toBeInTheDocument();
    expect(screen.queryByTestId("app-layout")).not.toBeInTheDocument();
  });
});

describe("signing out", () => {
  it("returns the user to the login screen and clears the stored credential", async () => {
    const user = userEvent.setup();
    mockAuthenticatedUser(mock);
    mock.onPost("/auth/logout").reply(HTTP.OK, { message: "Logged out" });
    stubRemainingWith(mock, []);
    localStorage.setItem("token", "test.access.token");

    renderAppAt("/dashboard");

    await screen.findByTestId("app-layout", {}, { timeout: 5000 });
    await user.click(screen.getByTestId("sidebar-logout-btn"));

    // The guard sees `user === false` and redirects — the observable contract.
    expect(await screen.findByTestId("login-page", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(localStorage.getItem("token")).toBeNull();
  });

  it("signs the user out locally even when the logout request fails", async () => {
    const user = userEvent.setup();
    mockAuthenticatedUser(mock);
    mock.onPost("/auth/logout").networkError();
    stubRemainingWith(mock, []);
    localStorage.setItem("token", "test.access.token");

    renderAppAt("/dashboard");

    await screen.findByTestId("app-layout", {}, { timeout: 5000 });
    await user.click(screen.getByTestId("sidebar-logout-btn"));

    expect(await screen.findByTestId("login-page", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(localStorage.getItem("token")).toBeNull();
  });
});

describe("session recovery on reload", () => {
  it("restores an existing session without showing the login screen", async () => {
    // The hard-refresh case: a valid cookie session and a deep link.
    mockAuthenticatedUser(mock);
    stubRemainingWith(mock, []);

    renderAppAt("/portfolio");

    expect(await screen.findByTestId("app-layout", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByTestId("login-page")).not.toBeInTheDocument();
  });

  it("sends a user whose session has expired back to the login screen", async () => {
    mockUnauthenticatedUser(mock); // expired cookie → 401 from /auth/me
    stubRemainingWith(mock, []);
    localStorage.setItem("token", "stale.token");

    renderAppAt("/portfolio");

    expect(await screen.findByTestId("login-page", {}, { timeout: 5000 })).toBeInTheDocument();
  });

  it("attaches the stored bearer token when restoring a session", async () => {
    localStorage.setItem("token", "test.access.token");
    mockAuthenticatedUser(mock);
    stubRemainingWith(mock, []);

    renderAppAt("/dashboard");

    await screen.findByTestId("app-layout", {}, { timeout: 5000 });
    await waitFor(() => {
      const probe = mock.history.get.find((r) => r.url === "/auth/me");
      expect(probe.headers.Authorization).toBe("Bearer test.access.token");
    });
  });
});

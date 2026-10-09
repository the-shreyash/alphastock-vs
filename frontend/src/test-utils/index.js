/**
 * Shared test rendering utilities.
 *
 * Design rule: these helpers assemble the *real* provider tree (ThemeProvider →
 * AuthProvider → RealtimeProvider) around the component under test. They do not
 * fake auth state with a stub context — authentication is established the way
 * production establishes it, by answering `GET /auth/me` at the network mock.
 * A test that proves "an admin sees the admin nav" therefore also proves the
 * real AuthProvider parses the real response shape.
 */
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import { ThemeProvider } from "../context/ThemeContext";
import { RealtimeProvider } from "../context/RealtimeProvider";
import { useRealtimeStore } from "../store/realtimeStore";
import { HTTP } from "./apiMock";
import { testUser, testAdmin } from "./fixtures";

export * from "./apiMock";
export * from "./fixtures";
export { userEvent };

/**
 * Answer `GET /auth/me` with a signed-in user, so AuthProvider resolves to the
 * authenticated state exactly as it does in production.
 *
 * Registered with `.onGet("/auth/me")` — the app's axios baseURL already
 * carries the `/api` prefix, so paths here are relative to it.
 */
export function mockAuthenticatedUser(mock, user = testUser) {
  mock.onGet("/auth/me").reply(HTTP.OK, user);
  return user;
}

/** Same, with an admin-role user. */
export function mockAdminUser(mock, user = testAdmin) {
  return mockAuthenticatedUser(mock, user);
}

/**
 * Answer `GET /auth/me` with 401 — no session. AuthProvider settles to
 * `user === false`, which is what the route guards treat as "signed out".
 */
export function mockUnauthenticatedUser(mock) {
  mock.onGet("/auth/me").reply(HTTP.UNAUTHORIZED, { detail: "Not authenticated" });
  // D6.2 bootstrap recovery. A 401 from the mount probe is ambiguous — the
  // access token lives 15 minutes and the refresh cookie seven days, so a
  // reload used to sign the user out with a perfectly good refresh cookie in
  // the browser. `AuthContext` now answers that 401 with ONE silent refresh
  // before concluding anything, so "unauthenticated" is two stubs, not one:
  // without this the probe would hit the adapter's no-match handler and the
  // browser's signed-out state would depend on how that happened to fail.
  mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, { detail: "No refresh token" });
}

/**
 * Render `ui` inside the app's provider tree and a MemoryRouter.
 *
 * @param {React.ReactElement} ui         component under test
 * @param {object}   [options]
 * @param {string}   [options.route]      initial URL
 * @param {string}   [options.path]       route pattern, when `ui` reads params
 *                                        (e.g. "/stock/:symbol")
 * @param {boolean}  [options.withRealtime] mount RealtimeProvider (default true;
 *                                        its socket is the inert stub from
 *                                        setupTests, so this stays offline)
 */
export function renderWithProviders(ui, { route = "/", path, withRealtime = true, ...renderOptions } = {}) {
  const Realtime = withRealtime ? RealtimeProvider : ({ children }) => children;

  function Wrapper({ children }) {
    return (
      <MemoryRouter initialEntries={[route]}>
        <ThemeProvider>
          <AuthProvider>
            <Realtime>
              {path ? <Routes><Route path={path} element={children} /></Routes> : children}
            </Realtime>
          </AuthProvider>
        </ThemeProvider>
      </MemoryRouter>
    );
  }

  return {
    user: userEvent.setup(),
    ...render(ui, { wrapper: Wrapper, ...renderOptions }),
  };
}

/**
 * Render the application's real route table at `route`.
 *
 * Used by the routing/guard tests: nothing about the route configuration is
 * re-declared in the test, so a guard removed from App.js fails these tests
 * instead of quietly passing against a test-local copy.
 */
export function renderAppAt(route = "/", options = {}) {
  // Imported lazily so the (large, lazily-chunked) page tree is only pulled in
  // by the suites that actually exercise routing.
  const { AppRouter } = require("../App");
  return renderWithProviders(<AppRouter />, { route, ...options });
}

/**
 * Reset the Zustand realtime store between tests.
 *
 * The store is a module singleton: without this, live prices pushed by one test
 * leak into the next and produce order-dependent passes.
 */
export function resetRealtimeStore() {
  const s = useRealtimeStore.getState();
  s.reset?.();
  useRealtimeStore.setState({ connection: { status: "offline", lastPongAt: null }, send: null }, false);
}

/** Convenience: the current realtime store state. */
export const realtimeStore = useRealtimeStore;

/**
 * Replace `window.location` with a writable stand-in.
 *
 * Needed because the OAuth entry point performs a full-page redirect by
 * assigning `window.location.href`, and the callback screen reads
 * `window.location.search`. jsdom refuses to navigate and its real `location`
 * is read-only, so the redirect is unobservable without this.
 *
 * @returns {() => void} restore function — call it in afterEach.
 */
export function stubLocation({ origin = "http://localhost", pathname = "/", search = "" } = {}) {
  const original = window.location;
  delete window.location;
  window.location = {
    origin,
    pathname,
    search,
    href: `${origin}${pathname}${search}`,
    assign: jest.fn(),
    replace: jest.fn(),
  };
  return () => {
    window.location = original;
  };
}

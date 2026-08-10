/**
 * Google OAuth callback.
 *
 * The riskiest screen in the auth flow: it exchanges an authorization code for
 * a session, and every failure path must land the user somewhere sane rather
 * than on a permanent spinner.
 *
 * Production failures these catch: a callback opened without `code`/`state`
 * (user hit back, or a tampered link) hanging forever; a rejected CSRF `state`
 * silently leaving the user "authenticating"; and the exchange firing twice
 * under React's StrictMode double-effect, which burns a single-use code and
 * makes the second attempt fail.
 */
import { screen, waitFor } from "@testing-library/react";
import { Routes, Route } from "react-router-dom";
import AuthCallback from "../AuthCallback";
import {
  renderWithProviders,
  installApiMock,
  stubLocation,
  HTTP,
  testUser,
  loginResponse,
} from "../../test-utils";

let mock;
let restoreLocation;

beforeEach(() => {
  mock = installApiMock();
});

afterEach(() => {
  mock.restore();
  restoreLocation?.();
});

/**
 * Mount the callback screen with destination markers for the two places it can
 * send the user. AuthCallback reads the query string from `window.location`
 * (not from the router), so the stub carries the OAuth parameters.
 */
function renderCallback(search) {
  restoreLocation = stubLocation({ pathname: "/auth/google/callback", search });

  return renderWithProviders(
    <Routes>
      <Route path="/auth/google/callback" element={<AuthCallback />} />
      <Route path="/login" element={<div data-testid="login-destination" />} />
      <Route path="/" element={<div data-testid="app-destination" />} />
    </Routes>,
    { route: `/auth/google/callback${search}` },
  );
}

const VALID_PARAMS = "?code=test-auth-code&state=test-csrf-state";

describe("while the exchange is in flight", () => {
  it("tells the user what is happening", () => {
    mock.onPost("/auth/google/session").reply(HTTP.OK, loginResponse());
    mock.onGet("/auth/me").reply(HTTP.OK, testUser);

    renderCallback(VALID_PARAMS);

    expect(screen.getByText(/authenticating with google/i)).toBeInTheDocument();
  });
});

describe("successful exchange", () => {
  it("sends the code, state and redirect_uri the backend needs to verify the callback", async () => {
    mock.onPost("/auth/google/session").reply(HTTP.OK, loginResponse());
    mock.onGet("/auth/me").reply(HTTP.OK, testUser);

    renderCallback(VALID_PARAMS);

    await waitFor(() => expect(mock.history.post).toHaveLength(1));
    expect(JSON.parse(mock.history.post[0].data)).toEqual({
      code: "test-auth-code",
      state: "test-csrf-state",
      redirect_uri: "http://localhost/auth/google/callback",
    });
  });

  it("sends the exchange with credentials so the state cookie is replayed", async () => {
    // The CSRF `state` lives in a short-lived httponly cookie planted at
    // login-url time; without withCredentials the backend cannot verify it.
    mock.onPost("/auth/google/session").reply(HTTP.OK, loginResponse());
    mock.onGet("/auth/me").reply(HTTP.OK, testUser);

    renderCallback(VALID_PARAMS);

    await waitFor(() => expect(mock.history.post).toHaveLength(1));
    expect(mock.history.post[0].withCredentials).toBe(true);
  });

  it("stores the returned token and lands the user in the app", async () => {
    mock.onPost("/auth/google/session").reply(HTTP.OK, loginResponse());
    mock.onGet("/auth/me").reply(HTTP.OK, testUser);

    renderCallback(VALID_PARAMS);

    expect(await screen.findByTestId("app-destination")).toBeInTheDocument();
    expect(localStorage.getItem("token")).toBe("test.access.token");
  });

  it("establishes the session before navigating, so the app never renders signed-out", async () => {
    mock.onPost("/auth/google/session").reply(HTTP.OK, loginResponse());
    mock.onGet("/auth/me").reply(HTTP.OK, testUser);

    renderCallback(VALID_PARAMS);

    await screen.findByTestId("app-destination");
    expect(mock.history.get.filter((r) => r.url === "/auth/me")).toHaveLength(1);
  });

  it("exchanges the single-use code exactly once", async () => {
    mock.onPost("/auth/google/session").reply(HTTP.OK, loginResponse());
    mock.onGet("/auth/me").reply(HTTP.OK, testUser);

    renderCallback(VALID_PARAMS);

    await screen.findByTestId("app-destination");
    expect(mock.history.post.filter((r) => r.url === "/auth/google/session")).toHaveLength(1);
  });
});

describe("missing OAuth parameters", () => {
  it.each([
    ["no parameters at all", ""],
    ["a code with no state", "?code=test-auth-code"],
    ["a state with no code", "?state=test-csrf-state"],
    ["an error returned by Google", "?error=access_denied"],
  ])("returns the user to the login screen when there is %s", async (_label, search) => {
    renderCallback(search);

    expect(await screen.findByTestId("login-destination")).toBeInTheDocument();
    // Nothing is exchanged — a malformed callback must not reach the backend.
    expect(mock.history.post).toHaveLength(0);
  });
});

describe("failed exchange", () => {
  it.each([
    ["a rejected CSRF state", HTTP.BAD_REQUEST, { detail: "Invalid state" }],
    ["an expired authorization code", HTTP.UNAUTHORIZED, { detail: "Invalid code" }],
    ["a backend fault", HTTP.SERVER_ERROR, { detail: "Internal server error" }],
  ])("returns the user to the login screen on %s", async (_label, status, body) => {
    const consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
    mock.onPost("/auth/google/session").reply(status, body);

    renderCallback(VALID_PARAMS);

    expect(await screen.findByTestId("login-destination")).toBeInTheDocument();
    expect(localStorage.getItem("token")).toBeNull();

    consoleError.mockRestore();
  });

  it("returns the user to the login screen when the backend is unreachable", async () => {
    const consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
    mock.onPost("/auth/google/session").networkError();

    renderCallback(VALID_PARAMS);

    expect(await screen.findByTestId("login-destination")).toBeInTheDocument();

    consoleError.mockRestore();
  });
});

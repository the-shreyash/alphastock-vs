/**
 * AuthContext — the frontend's single source of truth for "who is signed in".
 *
 * Every route guard, the admin gate and the realtime socket all read from it,
 * so its observable states matter more than its internals. These tests assert
 * the three states the rest of the app branches on:
 *
 *   user === null   → still checking (guards must wait, not redirect)
 *   user === false  → definitively signed out
 *   user === object → signed in
 *
 * Duplicating backend auth tests is explicitly not the goal; what is tested
 * here is how the frontend *reacts* to each backend outcome.
 */
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AuthProvider, useAuth } from "../AuthContext";
import { installApiMock, HTTP, pending } from "../../test-utils/apiMock";
import { testUser, loginResponse, errorDetailString } from "../../test-utils/fixtures";

let mock;

beforeEach(() => {
  mock = installApiMock();
  window.history.pushState({}, "", "/");
});

afterEach(() => {
  mock.restore();
});

/** Probe component: renders the auth state as text and exposes the actions. */
function AuthProbe() {
  const { user, loading, login, register, logout } = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="user">
        {user === null ? "checking" : user === false ? "signed-out" : user.email}
      </span>
      <button onClick={() => login("trader@test.invalid", "correct-horse-battery").catch(() => {})}>
        login
      </button>
      <button onClick={() => register("Test Trader", "trader@test.invalid", "correct-horse-battery").catch(() => {})}>
        register
      </button>
      <button onClick={() => logout()}>logout</button>
    </div>
  );
}

function renderAuth() {
  return {
    user: userEvent.setup(),
    ...render(<AuthProvider><AuthProbe /></AuthProvider>),
  };
}

describe("session restoration on mount", () => {
  it("restores the signed-in user when /auth/me succeeds", async () => {
    mock.onGet("/auth/me").reply(HTTP.OK, testUser);

    renderAuth();

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent(testUser.email));
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
  });

  it("settles to signed-out when /auth/me returns 401", async () => {
    mock.onGet("/auth/me").reply(HTTP.UNAUTHORIZED, { detail: "Not authenticated" });

    renderAuth();

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("signed-out"));
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
  });

  it("settles to signed-out when the backend is unreachable", async () => {
    // A network failure must not strand the app on a permanent spinner.
    mock.onGet("/auth/me").networkError();

    renderAuth();

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("signed-out"));
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
  });

  it("stays in the 'checking' state while /auth/me is in flight", async () => {
    mock.onGet("/auth/me").reply(() => pending());

    renderAuth();

    // Guards rely on this: `loading` true means "do not redirect yet".
    expect(screen.getByTestId("loading")).toHaveTextContent("true");
    expect(screen.getByTestId("user")).toHaveTextContent("checking");
  });

  it("probes /auth/me exactly once per mount", async () => {
    mock.onGet("/auth/me").reply(HTTP.OK, testUser);

    renderAuth();

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent(testUser.email));
    expect(mock.history.get.filter((r) => r.url === "/auth/me")).toHaveLength(1);
  });

  it("defers the session probe on the Google callback route", async () => {
    // AuthCallback must complete the code exchange first; probing /auth/me here
    // would race it and flash the user through the signed-out state.
    window.history.pushState({}, "", "/auth/google/callback?code=abc&state=xyz");

    renderAuth();

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(mock.history.get.filter((r) => r.url === "/auth/me")).toHaveLength(0);
    expect(screen.getByTestId("user")).toHaveTextContent("checking");
  });
});

describe("login", () => {
  beforeEach(() => {
    mock.onGet("/auth/me").reply(HTTP.UNAUTHORIZED, {});
  });

  it("signs the user in and persists the token", async () => {
    mock.onPost("/auth/login").reply(HTTP.OK, loginResponse());

    const { user } = renderAuth();
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("signed-out"));

    await user.click(screen.getByRole("button", { name: "login" }));

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent(testUser.email));
    expect(localStorage.getItem("token")).toBe("test.access.token");
  });

  it("sends the submitted credentials to /auth/login", async () => {
    mock.onPost("/auth/login").reply(HTTP.OK, loginResponse());

    const { user } = renderAuth();
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("signed-out"));

    await user.click(screen.getByRole("button", { name: "login" }));

    await waitFor(() => expect(mock.history.post).toHaveLength(1));
    expect(JSON.parse(mock.history.post[0].data)).toEqual({
      email: "trader@test.invalid",
      password: "correct-horse-battery",
    });
  });

  it("leaves the user signed out and stores no token when credentials are rejected", async () => {
    mock.onPost("/auth/login").reply(HTTP.UNAUTHORIZED, errorDetailString);

    const { user } = renderAuth();
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("signed-out"));

    await user.click(screen.getByRole("button", { name: "login" }));

    await waitFor(() => expect(mock.history.post).toHaveLength(1));
    expect(screen.getByTestId("user")).toHaveTextContent("signed-out");
    expect(localStorage.getItem("token")).toBeNull();
  });

  it("signs in without a token in the body (cookie-only session)", async () => {
    // The backend may issue cookies only; the client must not require `token`.
    const { token, ...noToken } = loginResponse();
    mock.onPost("/auth/login").reply(HTTP.OK, noToken);

    const { user } = renderAuth();
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("signed-out"));

    await user.click(screen.getByRole("button", { name: "login" }));

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent(testUser.email));
    expect(localStorage.getItem("token")).toBeNull();
  });
});

describe("register", () => {
  beforeEach(() => {
    mock.onGet("/auth/me").reply(HTTP.UNAUTHORIZED, {});
  });

  it("signs the new user in on success", async () => {
    mock.onPost("/auth/register").reply(HTTP.OK, loginResponse());

    const { user } = renderAuth();
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("signed-out"));

    await user.click(screen.getByRole("button", { name: "register" }));

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent(testUser.email));
    expect(localStorage.getItem("token")).toBe("test.access.token");
  });

  it("leaves the user signed out when the email is already taken", async () => {
    mock.onPost("/auth/register").reply(HTTP.CONFLICT, { detail: "Email already registered" });

    const { user } = renderAuth();
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("signed-out"));

    await user.click(screen.getByRole("button", { name: "register" }));

    await waitFor(() => expect(mock.history.post).toHaveLength(1));
    expect(screen.getByTestId("user")).toHaveTextContent("signed-out");
  });
});

describe("logout", () => {
  it("revokes the session server-side and clears local state", async () => {
    mock.onGet("/auth/me").reply(HTTP.OK, testUser);
    mock.onPost("/auth/logout").reply(HTTP.OK, { message: "Logged out" });
    localStorage.setItem("token", "test.access.token");

    const { user } = renderAuth();
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent(testUser.email));

    await user.click(screen.getByRole("button", { name: "logout" }));

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("signed-out"));
    expect(localStorage.getItem("token")).toBeNull();
    expect(mock.history.post.filter((r) => r.url === "/auth/logout")).toHaveLength(1);
  });

  it("still signs the user out locally when the logout call fails", async () => {
    // Otherwise a user on a flaky connection is stuck in a session they have
    // already asked to leave — a real security complaint on shared machines.
    mock.onGet("/auth/me").reply(HTTP.OK, testUser);
    mock.onPost("/auth/logout").reply(HTTP.SERVER_ERROR, {});
    localStorage.setItem("token", "test.access.token");

    const { user } = renderAuth();
    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent(testUser.email));

    await user.click(screen.getByRole("button", { name: "logout" }));

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("signed-out"));
    expect(localStorage.getItem("token")).toBeNull();
  });
});

describe("useAuth contract", () => {
  it("fails loudly when used outside an AuthProvider", () => {
    // A component tree rendered outside the provider would otherwise read
    // undefined state and silently behave as if signed out.
    const spy = jest.spyOn(console, "error").mockImplementation(() => {});
    function Orphan() {
      useAuth();
      return null;
    }

    expect(() => render(<Orphan />)).toThrow(/must be inside AuthProvider/);

    spy.mockRestore();
  });
});

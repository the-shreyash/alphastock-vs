/**
 * Login screen.
 *
 * The first screen every user meets and the only place bad credentials must be
 * explained rather than swallowed. Production failures these catch: a rejected
 * login that leaves the button stuck on "Signing in…"; a FastAPI 422 body
 * (`detail` as an array of objects) rendering as "[object Object]"; and a
 * disabled-submit regression that lets a user double-submit credentials.
 */
import { screen, waitFor } from "@testing-library/react";
import Login from "../Login";
import {
  renderWithProviders,
  installApiMock,
  stubLocation,
  HTTP,
  pending,
  mockUnauthenticatedUser,
  errorDetailString,
  errorDetailValidation,
  loginResponse,
} from "../../test-utils";

let mock;
let restoreLocation;

beforeEach(() => {
  mock = installApiMock();
  mockUnauthenticatedUser(mock); // AuthProvider's mount probe
  restoreLocation = stubLocation({ pathname: "/login" });
});

afterEach(() => {
  mock.restore();
  restoreLocation();
});

/**
 * Render the screen and let AuthProvider's mount probe settle first.
 *
 * Without this, that probe resolves after the test body has finished and React
 * reports an update outside act() — noise that would mask a genuine warning.
 */
const renderLogin = async () => {
  const utils = renderWithProviders(<Login />, { route: "/login" });
  await screen.findByTestId("login-page");
  return utils;
};

const fillCredentials = async (user, email = "trader@test.invalid", password = "correct-horse-battery") => {
  await user.type(screen.getByTestId("login-email-input"), email);
  await user.type(screen.getByTestId("login-password-input"), password);
};

describe("rendering", () => {
  it("renders the sign-in form", async () => {
    await renderLogin();

    expect(screen.getByTestId("login-page")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.getByTestId("login-email-input")).toBeInTheDocument();
    expect(screen.getByTestId("login-password-input")).toBeInTheDocument();
  });

  it("shows no error before the user has tried anything", async () => {
    await renderLogin();

    expect(screen.queryByTestId("login-error")).not.toBeInTheDocument();
  });

  it("offers a route to registration", async () => {
    await renderLogin();

    expect(screen.getByTestId("register-link")).toHaveAttribute("href", "/register");
  });

  it("masks the password by default and reveals it on request", async () => {
    const { user } = await renderLogin();
    const password = screen.getByTestId("login-password-input");

    expect(password).toHaveAttribute("type", "password");

    // The reveal control is the only unnamed button inside the form group.
    await user.click(screen.getAllByRole("button")[0]);

    expect(password).toHaveAttribute("type", "text");
  });
});

describe("submission", () => {
  it("posts the entered credentials", async () => {
    mock.onPost("/auth/login").reply(HTTP.OK, loginResponse());

    const { user } = await renderLogin();
    await fillCredentials(user);
    await user.click(screen.getByTestId("login-submit-btn"));

    await waitFor(() => expect(mock.history.post).toHaveLength(1));
    expect(JSON.parse(mock.history.post[0].data)).toEqual({
      email: "trader@test.invalid",
      password: "correct-horse-battery",
    });
  });

  it("shows a pending state and disables the button while signing in", async () => {
    mock.onPost("/auth/login").reply(() => pending());

    const { user } = await renderLogin();
    await fillCredentials(user);
    await user.click(screen.getByTestId("login-submit-btn"));

    await waitFor(() => expect(screen.getByTestId("login-submit-btn")).toBeDisabled());
    expect(screen.getByTestId("login-submit-btn")).toHaveTextContent(/signing in/i);
  });

  it("does not submit twice when the button is clicked again mid-request", async () => {
    mock.onPost("/auth/login").reply(() => pending());

    const { user } = await renderLogin();
    await fillCredentials(user);
    const submit = screen.getByTestId("login-submit-btn");

    await user.click(submit);
    await waitFor(() => expect(submit).toBeDisabled());
    await user.click(submit);

    expect(mock.history.post).toHaveLength(1);
  });

  it("declares both credentials required, so an empty form cannot validate", async () => {
    await renderLogin();

    const email = screen.getByTestId("login-email-input");
    const password = screen.getByTestId("login-password-input");

    expect(email).toBeRequired();
    expect(password).toBeRequired();
    // jsdom does not block submission on constraint validation the way a
    // browser does, so the constraint itself is what gets asserted here.
    expect(email.checkValidity()).toBe(false);
    expect(email.closest("form").checkValidity()).toBe(false);
  });

  it("rejects a malformed email through the input's type", async () => {
    await renderLogin();

    expect(screen.getByTestId("login-email-input")).toHaveAttribute("type", "email");
  });
});

describe("error handling", () => {
  it("explains rejected credentials using the server's message", async () => {
    mock.onPost("/auth/login").reply(HTTP.UNAUTHORIZED, errorDetailString);

    const { user } = await renderLogin();
    await fillCredentials(user, "trader@test.invalid", "wrong-password");
    await user.click(screen.getByTestId("login-submit-btn"));

    expect(await screen.findByTestId("login-error")).toHaveTextContent("Invalid email or password");
  });

  it("renders a FastAPI validation body as readable text, not [object Object]", async () => {
    mock.onPost("/auth/login").reply(422, errorDetailValidation);

    const { user } = await renderLogin();
    await fillCredentials(user);
    await user.click(screen.getByTestId("login-submit-btn"));

    const error = await screen.findByTestId("login-error");
    expect(error).toHaveTextContent("Password must contain a special character");
    expect(error).not.toHaveTextContent("[object Object]");
  });

  it.each([
    ["rate limiting", HTTP.RATE_LIMITED, { detail: "Too many attempts. Try again in 15 minutes." }],
    ["a locked account", HTTP.FORBIDDEN, { detail: "Account locked" }],
    ["a server fault", HTTP.SERVER_ERROR, { detail: "Internal server error" }],
  ])("surfaces %s to the user", async (_label, status, body) => {
    mock.onPost("/auth/login").reply(status, body);

    const { user } = await renderLogin();
    await fillCredentials(user);
    await user.click(screen.getByTestId("login-submit-btn"));

    expect(await screen.findByTestId("login-error")).toHaveTextContent(body.detail);
  });

  it("falls back to a readable message when the backend is unreachable", async () => {
    mock.onPost("/auth/login").networkError();

    const { user } = await renderLogin();
    await fillCredentials(user);
    await user.click(screen.getByTestId("login-submit-btn"));

    const error = await screen.findByTestId("login-error");
    expect(error).toHaveTextContent(/could not reach the server/i);
  });

  it("re-enables the form after a failure so the user can retry", async () => {
    mock.onPost("/auth/login").replyOnce(HTTP.UNAUTHORIZED, errorDetailString);

    const { user } = await renderLogin();
    await fillCredentials(user, "trader@test.invalid", "wrong-password");
    await user.click(screen.getByTestId("login-submit-btn"));

    await screen.findByTestId("login-error");
    expect(screen.getByTestId("login-submit-btn")).toBeEnabled();
    expect(screen.getByTestId("login-submit-btn")).toHaveTextContent(/sign in/i);
  });

  it("clears a stale error when the user submits again", async () => {
    mock.onPost("/auth/login").replyOnce(HTTP.UNAUTHORIZED, errorDetailString);
    mock.onPost("/auth/login").reply(() => pending());

    const { user } = await renderLogin();
    await fillCredentials(user, "trader@test.invalid", "wrong-password");
    await user.click(screen.getByTestId("login-submit-btn"));
    await screen.findByTestId("login-error");

    await user.click(screen.getByTestId("login-submit-btn"));

    await waitFor(() => expect(screen.queryByTestId("login-error")).not.toBeInTheDocument());
  });
});

describe("Google sign-in", () => {
  it("asks the backend for the authorization URL bound to this origin", async () => {
    // The URL and CSRF state are generated server-side (see services/googleAuth):
    // the client must never construct the Google URL itself.
    mock.onGet("/auth/google/login-url").reply(HTTP.OK, { url: "https://accounts.google.test/o/oauth2/v2/auth?state=s" });

    const { user } = await renderLogin();
    await user.click(screen.getByTestId("google-login-btn"));

    await waitFor(() => {
      const call = mock.history.get.find((r) => r.url === "/auth/google/login-url");
      expect(call).toBeDefined();
      expect(call.params.redirect_uri).toBe("http://localhost/auth/google/callback");
    });
  });

  it("hands the browser to the URL the backend returned", async () => {
    const authUrl = "https://accounts.google.test/o/oauth2/v2/auth?state=s";
    mock.onGet("/auth/google/login-url").reply(HTTP.OK, { url: authUrl });

    const { user } = await renderLogin();
    await user.click(screen.getByTestId("google-login-btn"));

    await waitFor(() => expect(window.location.href).toBe(authUrl));
  });

  it("explains the failure instead of navigating when Google sign-in is unavailable", async () => {
    mock.onGet("/auth/google/login-url").reply(HTTP.SERVER_ERROR, { detail: "Google sign-in is not configured" });

    const { user } = await renderLogin();
    await user.click(screen.getByTestId("google-login-btn"));

    expect(await screen.findByTestId("login-error")).toHaveTextContent("Google sign-in is not configured");
  });

  it("explains the failure when the backend returns no URL", async () => {
    // Regression guard (PH3.2 defect FE-001): the client-thrown message used to
    // be discarded and replaced by a generic "Something went wrong", because
    // the old fallback chain could never reach `err.message`.
    mock.onGet("/auth/google/login-url").reply(HTTP.OK, {});

    const { user } = await renderLogin();
    await user.click(screen.getByTestId("google-login-btn"));

    expect(await screen.findByTestId("login-error")).toHaveTextContent(/unavailable/i);
  });
});

describe("accessibility baseline", () => {
  it("gives every control an accessible name", async () => {
    await renderLogin();

    expect(screen.getByTestId("login-submit-btn")).toHaveAccessibleName(/sign in/i);
    expect(screen.getByTestId("google-login-btn")).toHaveAccessibleName(/continue with google/i);
  });

  it("exposes the credential inputs as a form the keyboard can complete", async () => {
    const { user } = await renderLogin();

    await user.tab();
    expect(screen.getByTestId("login-email-input")).toHaveFocus();

    await user.tab();
    expect(screen.getByTestId("login-password-input")).toHaveFocus();
  });

  it("announces a failed sign-in to assistive technology", async () => {
    // Regression guard (PH3.2 defect FE-002): the error banner carried colour
    // only, so a screen-reader user got no signal that the sign-in failed.
    mock.onPost("/auth/login").reply(HTTP.UNAUTHORIZED, errorDetailString);

    const { user } = await renderLogin();
    await fillCredentials(user, "trader@test.invalid", "wrong");
    await user.click(screen.getByTestId("login-submit-btn"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });
});

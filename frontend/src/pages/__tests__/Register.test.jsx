/**
 * Registration screen.
 *
 * Production failures these catch: a weak password reaching the API because the
 * client-side minimum stopped matching the server policy (PH1.5); the server's
 * password-policy 422 rendering as "[object Object]"; and a duplicate-email 409
 * leaving the user with a spinner and no explanation.
 */
import { screen, waitFor } from "@testing-library/react";
import Register from "../Register";
import {
  renderWithProviders,
  installApiMock,
  stubLocation,
  HTTP,
  pending,
  mockUnauthenticatedUser,
  errorDetailValidation,
  loginResponse,
} from "../../test-utils";

let mock;
let restoreLocation;

beforeEach(() => {
  mock = installApiMock();
  mockUnauthenticatedUser(mock);
  restoreLocation = stubLocation({ pathname: "/register" });
});

afterEach(() => {
  mock.restore();
  restoreLocation();
});

const renderRegister = async () => {
  const utils = renderWithProviders(<Register />, { route: "/register" });
  await screen.findByTestId("register-page");
  return utils;
};

const fillForm = async (user, { name = "Test Trader", email = "trader@test.invalid", password = "correct-horse-battery" } = {}) => {
  await user.type(screen.getByTestId("register-name-input"), name);
  await user.type(screen.getByTestId("register-email-input"), email);
  await user.type(screen.getByTestId("register-password-input"), password);
};

describe("rendering", () => {
  it("renders the account creation form", async () => {
    await renderRegister();

    expect(screen.getByRole("heading", { name: /create account/i })).toBeInTheDocument();
    expect(screen.getByTestId("register-name-input")).toBeInTheDocument();
    expect(screen.getByTestId("register-email-input")).toBeInTheDocument();
    expect(screen.getByTestId("register-password-input")).toBeInTheDocument();
  });

  it("offers a route back to sign-in", async () => {
    await renderRegister();

    expect(screen.getByTestId("login-link")).toHaveAttribute("href", "/login");
  });

  it("declares every field required", async () => {
    await renderRegister();

    expect(screen.getByTestId("register-name-input")).toBeRequired();
    expect(screen.getByTestId("register-email-input")).toBeRequired();
    expect(screen.getByTestId("register-password-input")).toBeRequired();
  });
});

describe("client-side password policy", () => {
  it("rejects a password shorter than the server minimum without calling the API", async () => {
    // Mirrors the PH1.5 server policy. Catching it here saves a round trip and
    // gives immediate feedback; the server remains the authority.
    const { user } = await renderRegister();
    await fillForm(user, { password: "short" });

    await user.click(screen.getByTestId("register-submit-btn"));

    expect(await screen.findByTestId("register-error")).toHaveTextContent(/at least 12 characters/i);
    expect(mock.history.post.filter((r) => r.url === "/auth/register")).toHaveLength(0);
  });

  it("accepts a password at exactly the minimum length", async () => {
    mock.onPost("/auth/register").reply(HTTP.OK, loginResponse());

    const { user } = await renderRegister();
    await fillForm(user, { password: "abcdefghijkl" }); // 12 characters

    await user.click(screen.getByTestId("register-submit-btn"));

    await waitFor(() => expect(
      mock.history.post.filter((r) => r.url === "/auth/register")).toHaveLength(1));
  });

  it("does not block a password one character over the minimum", async () => {
    mock.onPost("/auth/register").reply(HTTP.OK, loginResponse());

    const { user } = await renderRegister();
    await fillForm(user, { password: "abcdefghijklm" }); // 13 characters

    await user.click(screen.getByTestId("register-submit-btn"));

    await waitFor(() => expect(
      mock.history.post.filter((r) => r.url === "/auth/register")).toHaveLength(1));
    expect(screen.queryByTestId("register-error")).not.toBeInTheDocument();
  });
});

describe("submission", () => {
  it("posts the entered details", async () => {
    mock.onPost("/auth/register").reply(HTTP.OK, loginResponse());

    const { user } = await renderRegister();
    await fillForm(user);
    await user.click(screen.getByTestId("register-submit-btn"));

    await waitFor(() => expect(
      mock.history.post.filter((r) => r.url === "/auth/register")).toHaveLength(1));
    expect(JSON.parse(
      mock.history.post.filter((r) => r.url === "/auth/register")[0].data)).toEqual({
      name: "Test Trader",
      email: "trader@test.invalid",
      password: "correct-horse-battery",
    });
  });

  it("shows a pending state and blocks a second submission", async () => {
    mock.onPost("/auth/register").reply(() => pending());

    const { user } = await renderRegister();
    await fillForm(user);
    const submit = screen.getByTestId("register-submit-btn");

    await user.click(submit);
    await waitFor(() => expect(submit).toBeDisabled());
    expect(submit).toHaveTextContent(/creating/i);

    await user.click(submit);
    expect(mock.history.post.filter((r) => r.url === "/auth/register")).toHaveLength(1);
  });
});

describe("server-side validation and errors", () => {
  it("renders the server's password-policy rejection as readable text", async () => {
    mock.onPost("/auth/register").reply(422, errorDetailValidation);

    const { user } = await renderRegister();
    await fillForm(user);
    await user.click(screen.getByTestId("register-submit-btn"));

    const error = await screen.findByTestId("register-error");
    expect(error).toHaveTextContent("Password must contain a special character");
    expect(error).not.toHaveTextContent("[object Object]");
  });

  it("explains a duplicate email", async () => {
    mock.onPost("/auth/register").reply(HTTP.CONFLICT, { detail: "Email already registered" });

    const { user } = await renderRegister();
    await fillForm(user);
    await user.click(screen.getByTestId("register-submit-btn"));

    expect(await screen.findByTestId("register-error")).toHaveTextContent("Email already registered");
  });

  it.each([
    ["rate limiting", HTTP.RATE_LIMITED, "Too many sign-up attempts"],
    ["a server fault", HTTP.SERVER_ERROR, "Internal server error"],
  ])("surfaces %s", async (_label, status, detail) => {
    mock.onPost("/auth/register").reply(status, { detail });

    const { user } = await renderRegister();
    await fillForm(user);
    await user.click(screen.getByTestId("register-submit-btn"));

    expect(await screen.findByTestId("register-error")).toHaveTextContent(detail);
  });

  it("explains an unreachable backend and re-enables the form", async () => {
    mock.onPost("/auth/register").networkError();

    const { user } = await renderRegister();
    await fillForm(user);
    await user.click(screen.getByTestId("register-submit-btn"));

    expect(await screen.findByTestId("register-error")).toHaveTextContent(/could not reach the server/i);
    expect(screen.getByTestId("register-submit-btn")).toBeEnabled();
  });
});

describe("accessibility baseline", () => {
  it("announces a failed registration to assistive technology", async () => {
    mock.onPost("/auth/register").reply(HTTP.CONFLICT, { detail: "Email already registered" });

    const { user } = await renderRegister();
    await fillForm(user);
    await user.click(screen.getByTestId("register-submit-btn"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Email already registered");
  });

  it("gives the primary actions accessible names", async () => {
    await renderRegister();

    expect(screen.getByTestId("register-submit-btn")).toHaveAccessibleName(/create account/i);
    expect(screen.getByTestId("google-register-btn")).toHaveAccessibleName(/continue with google/i);
  });
});

/**
 * API error message resolution.
 *
 * The backend returns `detail` in three different shapes. Every one of them
 * reaches a user-facing string through this module, so a regression here shows
 * up as "[object Object]" or a blank error banner on a failed login, a rejected
 * trade, or a refused admin action.
 */
import { formatApiDetail, resolveApiErrorMessage } from "../apiError";
import { errorDetailString, errorDetailValidation, errorDetailRisk } from "../../test-utils/fixtures";

/** Build an axios-shaped rejection with the given status and body. */
function axiosError(status, data) {
  return { isAxiosError: true, message: "Request failed", response: { status, data } };
}

describe("formatApiDetail", () => {
  it("passes a plain HTTPException string through", () => {
    expect(formatApiDetail(errorDetailString.detail)).toBe("Invalid email or password");
  });

  it("joins the messages of a 422 validation list", () => {
    expect(formatApiDetail(errorDetailValidation.detail)).toBe("Password must contain a special character");
  });

  it("joins multiple validation messages into one sentence", () => {
    const detail = [{ msg: "Email is invalid" }, { msg: "Password is too short" }];

    expect(formatApiDetail(detail)).toBe("Email is invalid Password is too short");
  });

  it("reads the message out of a Trading Engine rejection object", () => {
    expect(formatApiDetail(errorDetailRisk.detail)).toBe("Trade rejected by risk manager");
  });

  it("never renders an object as [object Object]", () => {
    expect(formatApiDetail({ unexpected: "shape" })).not.toContain("[object Object]");
  });

  it("returns an empty string when there is no detail", () => {
    expect(formatApiDetail(null)).toBe("");
    expect(formatApiDetail(undefined)).toBe("");
  });
});

describe("resolveApiErrorMessage", () => {
  it("prefers the server's explanation above everything else", () => {
    expect(resolveApiErrorMessage(axiosError(401, errorDetailString))).toBe("Invalid email or password");
  });

  it("resolves a 422 body to its validation messages", () => {
    expect(resolveApiErrorMessage(axiosError(422, errorDetailValidation)))
      .toBe("Password must contain a special character");
  });

  it("keeps a message thrown by application code", () => {
    // e.g. googleAuth throwing "Google sign-in is unavailable right now."
    const err = new Error("Google sign-in is unavailable right now.");

    expect(resolveApiErrorMessage(err)).toBe("Google sign-in is unavailable right now.");
  });

  it("replaces a raw transport failure with connection guidance", () => {
    // "Network Error" is a diagnostic, not something a user can act on.
    const err = { isAxiosError: true, message: "Network Error" };

    expect(resolveApiErrorMessage(err)).toMatch(/could not reach the server/i);
    expect(resolveApiErrorMessage(err)).not.toContain("Network Error");
  });

  it("replaces a timeout with connection guidance", () => {
    const err = { isAxiosError: true, message: "timeout of 0ms exceeded", code: "ECONNABORTED" };

    expect(resolveApiErrorMessage(err)).toMatch(/could not reach the server/i);
  });

  it("falls back generically when the server answers with no detail", () => {
    expect(resolveApiErrorMessage(axiosError(500, {}))).toMatch(/something went wrong/i);
  });

  it("honours a caller-supplied fallback", () => {
    expect(resolveApiErrorMessage(axiosError(500, {}), "Trade request failed.")).toBe("Trade request failed.");
  });

  it("never returns an empty string, whatever it is given", () => {
    for (const err of [null, undefined, {}, new Error(""), "a string", 42]) {
      expect(resolveApiErrorMessage(err)).toBeTruthy();
    }
  });
});

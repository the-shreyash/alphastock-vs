/**
 * API client — the axios instance every request in the app passes through.
 *
 * These are the highest-value frontend tests in the suite: the response
 * interceptor implements silent session refresh. If it regresses, either every
 * user gets logged out on the first expired access token, or the client enters
 * an infinite refresh loop and hammers the backend. Neither is visible in a
 * component test.
 *
 * The adapter is mocked; the interceptors under test are the real ones.
 */
import MockAdapter from "axios-mock-adapter";
import api, { resetRefreshState } from "../api";
import { HTTP } from "../../test-utils/apiMock";

let mock;

beforeEach(() => {
  mock = new MockAdapter(api, { onNoMatch: "throwException" });
  // The refresh latch is module-level state shared by every consumer; clear it
  // so each test starts from a client that has not yet seen a failed refresh.
  resetRefreshState();
});

afterEach(() => {
  mock.restore();
});

describe("base configuration", () => {
  it("targets the backend's /api prefix", () => {
    expect(api.defaults.baseURL).toBe("http://backend.test/api");
  });
});

describe("request interceptor — bearer token", () => {
  it("attaches the stored token to outgoing requests", async () => {
    localStorage.setItem("token", "stored-token-value");
    mock.onGet("/portfolio").reply(HTTP.OK, {});

    await api.get("/portfolio");

    expect(mock.history.get[0].headers.Authorization).toBe("Bearer stored-token-value");
  });

  it("sends no Authorization header when there is no stored token", async () => {
    mock.onGet("/portfolio").reply(HTTP.OK, {});

    await api.get("/portfolio");

    expect(mock.history.get[0].headers.Authorization).toBeUndefined();
  });
});

describe("response interceptor — silent session refresh", () => {
  it("refreshes and replays the original request when it 401s", async () => {
    // First call 401s (access token expired), refresh succeeds, replay succeeds.
    mock.onGet("/portfolio").replyOnce(HTTP.UNAUTHORIZED, { detail: "Token expired" });
    mock.onPost("/auth/refresh").reply(HTTP.OK, { token: "fresh" });
    mock.onGet("/portfolio").reply(HTTP.OK, { total_value: 152340.25 });

    const { data } = await api.get("/portfolio");

    expect(data).toEqual({ total_value: 152340.25 });
    expect(mock.history.post.filter((r) => r.url === "/auth/refresh")).toHaveLength(1);
    expect(mock.history.get.filter((r) => r.url === "/portfolio")).toHaveLength(2);
  });

  it("retries a given request only once, so a still-401 endpoint cannot loop", async () => {
    // The replayed request 401s again — the interceptor must give up, not spin.
    mock.onGet("/portfolio").reply(HTTP.UNAUTHORIZED, { detail: "Token expired" });
    mock.onPost("/auth/refresh").reply(HTTP.OK, { token: "fresh" });

    await expect(api.get("/portfolio")).rejects.toMatchObject({
      response: { status: HTTP.UNAUTHORIZED },
    });

    expect(mock.history.post.filter((r) => r.url === "/auth/refresh")).toHaveLength(1);
    expect(mock.history.get.filter((r) => r.url === "/portfolio")).toHaveLength(2);
  });

  it("clears the stored token and rejects when the refresh itself fails", async () => {
    localStorage.setItem("token", "expired-token");
    mock.onGet("/portfolio").reply(HTTP.UNAUTHORIZED, { detail: "Token expired" });
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, { detail: "Refresh expired" });

    await expect(api.get("/portfolio")).rejects.toMatchObject({
      response: { status: HTTP.UNAUTHORIZED },
    });

    expect(localStorage.getItem("token")).toBeNull();
  });

  it("stops attempting refresh for later requests once a refresh has failed", async () => {
    mock.onGet("/portfolio").reply(HTTP.UNAUTHORIZED, {});
    mock.onGet("/trades").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});

    await expect(api.get("/portfolio")).rejects.toBeDefined();
    await expect(api.get("/trades")).rejects.toBeDefined();

    // One refresh attempt in total — the dead-session latch held for the second
    // request. Without it, every 401 on a dashboard full of widgets would fire
    // its own refresh against a backend that has already said no.
    expect(mock.history.post.filter((r) => r.url === "/auth/refresh")).toHaveLength(1);
  });

  it("re-arms the refresh latch after a fresh sign-in", async () => {
    mock.onGet("/portfolio").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").replyOnce(HTTP.UNAUTHORIZED, {});

    await expect(api.get("/portfolio")).rejects.toBeDefined();

    resetRefreshState(); // what AuthContext.login() does
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});

    await expect(api.get("/portfolio")).rejects.toBeDefined();
    expect(mock.history.post.filter((r) => r.url === "/auth/refresh")).toHaveLength(2);
  });
});

describe("response interceptor — endpoints excluded from refresh", () => {
  // Refreshing in response to these would be circular: /auth/me 401 IS the
  // signed-out signal, and refreshing a failed login would be nonsense.
  it.each(["/auth/me", "/auth/refresh", "/auth/login", "/auth/register", "/auth/google/session"])(
    "never triggers a refresh when %s returns 401",
    async (url) => {
      mock.onAny(url).reply(HTTP.UNAUTHORIZED, { detail: "Not authenticated" });

      await expect(api.get(url)).rejects.toMatchObject({
        response: { status: HTTP.UNAUTHORIZED },
      });

      expect(mock.history.post.filter((r) => r.url === "/auth/refresh")).toHaveLength(0);
    },
  );
});

describe("response interceptor — non-401 failures pass through", () => {
  it.each([
    ["400 validation", HTTP.BAD_REQUEST],
    ["403 forbidden", HTTP.FORBIDDEN],
    ["404 not found", HTTP.NOT_FOUND],
    ["409 conflict", HTTP.CONFLICT],
    ["429 rate limited", HTTP.RATE_LIMITED],
    ["500 server error", HTTP.SERVER_ERROR],
  ])("rejects a %s untouched, without attempting a refresh", async (_label, status) => {
    mock.onGet("/trades").reply(status, { detail: "nope" });

    await expect(api.get("/trades")).rejects.toMatchObject({ response: { status } });
    expect(mock.history.post.filter((r) => r.url === "/auth/refresh")).toHaveLength(0);
  });

  it("propagates a network failure as a rejection with no response", async () => {
    mock.onGet("/trades").networkError();

    const err = await api.get("/trades").catch((e) => e);

    expect(err).toBeInstanceOf(Error);
    expect(err.response).toBeUndefined();
  });

  it("propagates a timeout as a rejection", async () => {
    mock.onGet("/trades").timeout();

    await expect(api.get("/trades")).rejects.toBeDefined();
  });
});

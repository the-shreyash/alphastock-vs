/**
 * D6.1 — SESSION LIFECYCLE regression suite (D6-L1 … D6-L4, L10, S8).
 *
 * WHAT WENT WRONG, IN ONE PARAGRAPH
 * ---------------------------------
 * The backend's rotating-refresh design was correct and complete. The SPA could
 * never reach it: `services/api.js` created its axios instance **without**
 * `withCredentials`, so for a cross-origin XHR (`:3000` -> `:8000`) the browser
 * ignored every `Set-Cookie` and sent no cookie on any request. Login's
 * `access_token`, `refresh_token` and `csrf_token` were therefore never stored,
 * `POST /api/auth/refresh` (which reads the refresh token only from the cookie)
 * answered `401 "No refresh token"` every time, one 401 latched refresh off for
 * the life of the page, and at t+15min the app died silently. The WebSocket then
 * re-offered the same expired token forever with nothing on screen saying so.
 *
 * These tests pin each half of the fix. They are transport-level (the mock
 * adapter replaces the axios adapter, so every interceptor under test is the
 * real one) rather than service-level, because the defect lived in the client
 * stack itself and a service mock would have proved nothing about it.
 */
import { act, render, waitFor } from "@testing-library/react";
import MockAdapter from "axios-mock-adapter";

import api, { refreshSession, resetRefreshState, SESSION_EXPIRED_EVENT } from "../services/api";
import { AuthProvider, SESSION_END, useAuth } from "../context/AuthContext";
import { RealtimeProvider } from "../context/RealtimeProvider";
import { useRealtimeStore } from "../store/realtimeStore";
import { HTTP } from "../test-utils/apiMock";

let mock;

beforeEach(() => {
  mock = new MockAdapter(api, { onNoMatch: "throwException" });
  resetRefreshState();
  useRealtimeStore.getState().reset();
  document.cookie = "csrf_token=; Max-Age=0; path=/";
});

afterEach(() => {
  mock.restore();
});

// ===========================================================================
// L1 — the SPA must send and store cookies
// ===========================================================================
describe("L1 — cross-origin credentials", () => {
  it("sends credentials on every request", () => {
    // The root cause, in one assertion. Without this the browser discards the
    // backend's Set-Cookie and the refresh endpoint can never see a token.
    expect(api.defaults.withCredentials).toBe(true);
  });

  it("carries credentials on the refresh call itself", async () => {
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});
    await refreshSession();
    expect(mock.history.post[0].withCredentials).toBe(true);
  });
});

// ===========================================================================
// L4 — CSRF token echoed on mutations
// ===========================================================================
describe("L4 — X-CSRF-Token", () => {
  it("echoes the csrf cookie on a mutating request", async () => {
    document.cookie = "csrf_token=csrf-value-123; path=/";
    mock.onPost("/trades").reply(HTTP.OK, {});

    await api.post("/trades", { symbol: "RELIANCE" });

    expect(mock.history.post[0].headers["X-CSRF-Token"]).toBe("csrf-value-123");
  });

  it.each(["put", "patch", "delete"])("echoes it on %s too", async (method) => {
    document.cookie = "csrf_token=csrf-value-123; path=/";
    mock[`on${method[0].toUpperCase()}${method.slice(1)}`]("/trades/1").reply(HTTP.OK, {});

    await api[method]("/trades/1", {});

    expect(mock.history[method][0].headers["X-CSRF-Token"]).toBe("csrf-value-123");
  });

  it("does not send it on a read — a GET is CSRF-exempt server-side", async () => {
    document.cookie = "csrf_token=csrf-value-123; path=/";
    mock.onGet("/portfolio").reply(HTTP.OK, {});

    await api.get("/portfolio");

    expect(mock.history.get[0].headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("omits the header when there is no cookie, rather than sending an empty one", async () => {
    mock.onPost("/trades").reply(HTTP.OK, {});
    await api.post("/trades", {});
    expect(mock.history.post[0].headers["X-CSRF-Token"]).toBeUndefined();
  });
});

// ===========================================================================
// L2 — one refresh for N concurrent 401s
// ===========================================================================
describe("L2 — promise-coalescing refresh queue", () => {
  it("ten simultaneous 401s produce ONE refresh and ten successful replays", async () => {
    const paths = Array.from({ length: 10 }, (_, i) => `/widget-${i}`);
    paths.forEach((p) => {
      mock.onGet(p).replyOnce(HTTP.UNAUTHORIZED, { detail: "Token expired" });
      mock.onGet(p).reply(HTTP.OK, { path: p });
    });
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});

    const results = await Promise.all(paths.map((p) => api.get(p)));

    // The defect: `if (!isRefreshing)` meant exactly one request attempted
    // recovery and the other nine rejected permanently — even though the
    // refresh succeeded. A dashboard fires exactly this burst at expiry.
    expect(mock.history.post.filter((r) => r.url === "/auth/refresh")).toHaveLength(1);
    expect(results.map((r) => r.data.path)).toEqual(paths);
  });

  it("queued requests all fail together when the refresh fails, without ten attempts", async () => {
    const paths = Array.from({ length: 10 }, (_, i) => `/widget-${i}`);
    paths.forEach((p) => mock.onGet(p).reply(HTTP.UNAUTHORIZED, {}));
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});

    const settled = await Promise.allSettled(paths.map((p) => api.get(p)));

    expect(settled.every((s) => s.status === "rejected")).toBe(true);
    expect(mock.history.post.filter((r) => r.url === "/auth/refresh")).toHaveLength(1);
  });

  it("retries each queued request exactly once", async () => {
    // Every replay 401s again. The client must give up, not spin.
    mock.onGet("/portfolio").reply(HTTP.UNAUTHORIZED, {});
    mock.onGet("/trades").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});

    await Promise.allSettled([api.get("/portfolio"), api.get("/trades")]);

    expect(mock.history.get.filter((r) => r.url === "/portfolio")).toHaveLength(2);
    expect(mock.history.get.filter((r) => r.url === "/trades")).toHaveLength(2);
  });

  it("a later 401 starts a fresh refresh once the first one has finished", async () => {
    mock.onGet("/portfolio").replyOnce(HTTP.UNAUTHORIZED, {});
    mock.onGet("/portfolio").reply(HTTP.OK, {});
    mock.onGet("/trades").replyOnce(HTTP.UNAUTHORIZED, {});
    mock.onGet("/trades").reply(HTTP.OK, {});
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});

    await api.get("/portfolio");
    await api.get("/trades");

    // Coalescing must not become caching: two separate expiries are two
    // separate refreshes.
    expect(mock.history.post.filter((r) => r.url === "/auth/refresh")).toHaveLength(2);
  });

  it("drops the bootstrap token once cookies have proven to work", async () => {
    localStorage.setItem("token", "stale-bootstrap-token");
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});

    await refreshSession();

    // A successful refresh proves the cookie path works end to end, and the
    // localStorage token is by then provably stale. Dropping it converges the
    // SPA onto cookie-only auth (where the CSRF layer actually applies) and
    // takes a long-lived credential out of reach of XSS.
    expect(localStorage.getItem("token")).toBeNull();
  });
});

// ===========================================================================
// L3 / L10 — SESSION_EXPIRED is not USER_SIGNED_OUT
// ===========================================================================
describe("L10 — session expiry is distinguishable from signing out", () => {
  function Probe({ onState }) {
    const { user, sessionEnd } = useAuth();
    onState({ user, sessionEnd });
    return null;
  }

  it("a failed refresh announces SESSION_EXPIRED exactly once", async () => {
    const heard = [];
    const listener = () => heard.push("expired");
    window.addEventListener(SESSION_EXPIRED_EVENT, listener);
    mock.onGet("/a").reply(HTTP.UNAUTHORIZED, {});
    mock.onGet("/b").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});

    await Promise.allSettled([api.get("/a"), api.get("/b")]);
    window.removeEventListener(SESSION_EXPIRED_EVENT, listener);

    // Once, not once per queued request: the failure is handled where the
    // shared promise is created, not by each awaiter.
    expect(heard).toEqual(["expired"]);
  });

  it("AuthContext reports EXPIRED — not a plain signed-out state", async () => {
    const states = [];
    mock.onGet("/auth/me").reply(HTTP.OK, { _id: "user-a", email: "a@example.com" });
    render(
      <AuthProvider>
        <Probe onState={(s) => states.push(s)} />
      </AuthProvider>,
    );
    await waitFor(() => expect(states.at(-1).user).toBeTruthy());

    act(() => { window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT)); });

    await waitFor(() => expect(states.at(-1).sessionEnd).toBe(SESSION_END.EXPIRED));
    expect(states.at(-1).user).toBe(false);
  });

  it("a deliberate logout reports SIGNED_OUT, not EXPIRED", async () => {
    const states = [];
    let auth;
    function Capture() {
      auth = useAuth();
      states.push({ user: auth.user, sessionEnd: auth.sessionEnd });
      return null;
    }
    mock.onGet("/auth/me").reply(HTTP.OK, { _id: "user-a", email: "a@example.com" });
    mock.onPost("/auth/logout").reply(HTTP.OK, {});
    render(<AuthProvider><Capture /></AuthProvider>);
    await waitFor(() => expect(states.at(-1).user).toBeTruthy());

    await act(async () => { await auth.logout(); });

    expect(states.at(-1).sessionEnd).toBe(SESSION_END.SIGNED_OUT);
    expect(states.at(-1).sessionEnd).not.toBe(SESSION_END.EXPIRED);
  });
});

// ===========================================================================
// L3 — the WebSocket stops offering a dead credential
// ===========================================================================
describe("L3 — WebSocket handshake rejection", () => {
  let mockUser;

  const socket = () => global.WebSocket.instances.at(-1);
  /** The server closes an unauthenticated handshake with 1008 BEFORE accept(),
   *  so the client sees a close with no preceding open. */
  const rejectHandshake = () =>
    act(() => { socket().onclose?.({ code: 1008 }); });

  beforeEach(() => {
    jest.useFakeTimers();
    mockUser = { _id: "user-a", email: "a@example.com" };
    jest.spyOn(require("../context/AuthContext"), "useAuth")
      .mockImplementation(() => ({ user: mockUser }));
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it("attempts ONE re-authentication and does not retry the dead token on a timer", async () => {
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const socketsAfterMount = global.WebSocket.instances.length;

    rejectHandshake();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    // Before D6.1 the reconnect loop re-offered the same expired token forever,
    // backing off to 30s and retrying until the tab closed. Advancing well past
    // every backoff window must now produce no further connection attempt.
    act(() => { jest.advanceTimersByTime(120000); });
    expect(global.WebSocket.instances.length).toBe(socketsAfterMount);
    expect(mock.history.post.filter((r) => r.url === "/auth/refresh")).toHaveLength(1);
  });

  it("surfaces the expiry as a distinct connection state, not 'reconnecting'", async () => {
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});
    render(<RealtimeProvider><div /></RealtimeProvider>);

    rejectHandshake();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    // "Reconnecting" would be a spinner describing a condition no amount of
    // waiting can fix.
    expect(useRealtimeStore.getState().connection.status).toBe("unauthenticated");
  });

  it("reconnects immediately when re-authentication succeeds", async () => {
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const before = global.WebSocket.instances.length;

    rejectHandshake();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(global.WebSocket.instances.length).toBe(before + 1);
  });

  it("a drop AFTER a successful open still backs off and reconnects", async () => {
    // The regression guard on the other side: treating every close as an auth
    // failure would break ordinary network resilience.
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const before = global.WebSocket.instances.length;
    act(() => { socket().readyState = 1; socket().onopen?.(); });
    act(() => { socket().onclose?.({ code: 1006 }); });

    act(() => { jest.advanceTimersByTime(5000); });

    expect(global.WebSocket.instances.length).toBe(before + 1);
    expect(mock.history.post.filter((r) => r.url === "/auth/refresh")).toHaveLength(0);
  });

  it("requests no private channel", () => {
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const sent = jest.spyOn(socket(), "send");
    act(() => { socket().readyState = 1; socket().onopen?.(); });

    const subscribe = sent.mock.calls
      .map(([raw]) => JSON.parse(raw))
      .find((m) => m.type === "subscribe");

    // D6.1 / S6: the server refuses these, and per-user events are delivered by
    // `send_to_user` with no subscription at all — asking was always a no-op
    // dressed as a capability.
    for (const priv of ["trades", "portfolio", "broker", "notifications", "watchlist"]) {
      expect(subscribe.channels).not.toContain(priv);
    }
    expect(subscribe.channels).toContain("market");
  });
});

// ===========================================================================
// S8 — realtime state must not survive an identity change
// ===========================================================================
describe("S8 — realtime state is reset on an identity change", () => {
  let mockUser;
  beforeEach(() => {
    jest.useFakeTimers();
    mockUser = { _id: "user-a", email: "a@example.com" };
    jest.spyOn(require("../context/AuthContext"), "useAuth")
      .mockImplementation(() => ({ user: mockUser }));
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it("A's live data is gone when B connects in the same tab", () => {
    const { rerender } = render(<RealtimeProvider><div /></RealtimeProvider>);

    // A's account state, as real events would have left it.
    act(() => {
      useRealtimeStore.setState({
        portfolioUpdate: { total: 1520000 },
        brokerStatus: { broker: "zerodha", connected: true },
        brokerOrders: [{ order_id: "A-1", symbol: "RELIANCE" }],
        unreadCount: 7,
      });
    });
    expect(useRealtimeStore.getState().brokerOrders).toHaveLength(1);

    mockUser = { _id: "user-b", email: "b@example.com" };
    rerender(<RealtimeProvider><div /></RealtimeProvider>);

    const s = useRealtimeStore.getState();
    expect(s.portfolioUpdate).toBeNull();
    expect(s.brokerStatus).toBeNull();
    expect(s.brokerOrders).toEqual([]);
    expect(s.unreadCount).toBe(0);
  });

  it("signing out clears it too", () => {
    const { rerender } = render(<RealtimeProvider><div /></RealtimeProvider>);
    act(() => { useRealtimeStore.setState({ unreadCount: 7, portfolioUpdate: { total: 1 } }); });

    mockUser = null;
    rerender(<RealtimeProvider><div /></RealtimeProvider>);

    expect(useRealtimeStore.getState().unreadCount).toBe(0);
    expect(useRealtimeStore.getState().portfolioUpdate).toBeNull();
  });

  it("a re-render for the SAME user does not wipe live data", () => {
    // The regression guard: resetting unconditionally would blank the dashboard
    // on every parent re-render.
    const { rerender } = render(<RealtimeProvider><div /></RealtimeProvider>);
    act(() => { useRealtimeStore.setState({ unreadCount: 7 }); });

    rerender(<RealtimeProvider><div /></RealtimeProvider>);

    expect(useRealtimeStore.getState().unreadCount).toBe(7);
  });
});

/**
 * D6.2 — WEBSOCKET AUTH LIFECYCLE regression suite (scope C, and the socket
 * half of scope E).
 *
 * The socket is the subsystem where "the credential is stale" and "the network
 * is gone" are hardest to tell apart, because **a browser reports them
 * identically**. The server closes an unauthenticated handshake with 1008
 * before `accept()`; at the ASGI layer that is an HTTP 403 to the upgrade
 * request, and the browser surfaces a handshake that never completed as
 * `CloseEvent` code 1006 — exactly what it reports for a server that is not
 * listening. D6.1 inferred "never opened ⇒ bad credential" from that, which was
 * right for the case it was written for and wrong for every outage: an ordinary
 * backend restart burned the single auth retry and then stopped reconnecting
 * permanently. Realtime never came back without a page reload.
 *
 * Every test below is therefore built around what the client can actually
 * observe, and the pairs matter: for each "recovers correctly" there is a
 * "does not do this to the other kind of failure".
 */
import { act, render } from "@testing-library/react";
import MockAdapter from "axios-mock-adapter";

import api, { resetRefreshState } from "../services/api";
import { RealtimeProvider } from "../context/RealtimeProvider";
import { useRealtimeStore } from "../store/realtimeStore";
import { HTTP } from "../test-utils/apiMock";

let mock;
let mockUser;

const sockets = () => global.WebSocket.instances;
const socket = () => sockets().at(-1);
const status = () => useRealtimeStore.getState().connection.status;

/** The connection came up and then dropped: an ordinary network event. */
const dropAfterOpen = () => act(() => {
  socket().readyState = 1;
  socket().onopen?.();
  socket().onclose?.({ code: 1006 });
});

/** The connection never came up. Cause unknown to the client by construction. */
const failHandshake = () => act(() => { socket().onclose?.({ code: 1006 }); });

/** Settle the diagnosis probe and any refresh it starts (fake timers rule out
 *  `waitFor`, which needs real ones). */
const settle = () => act(async () => {
  for (let i = 0; i < 12; i += 1) await Promise.resolve();
});

beforeEach(() => {
  jest.useFakeTimers();
  mock = new MockAdapter(api, { onNoMatch: "throwException" });
  resetRefreshState();
  useRealtimeStore.getState().reset();
  mockUser = { _id: "user-a", email: "a@example.com" };
  jest.spyOn(require("../context/AuthContext"), "useAuth")
    .mockImplementation(() => ({ user: mockUser }));
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
  mock.restore();
});

const refreshes = () => mock.history.post.filter((r) => r.url === "/auth/refresh");

// ===========================================================================
// D6.2-E — a failed handshake is diagnosed, not assumed
// ===========================================================================
describe("D6.2-E — an outage must not be mistaken for an expired credential", () => {
  it("keeps reconnecting when the session is fine and the server is not", async () => {
    // THE regression. A backend restart used to leave realtime permanently
    // dead: the failed handshake was read as an auth failure, the one auth
    // retry was spent, and no reconnect was ever scheduled again.
    mock.onGet("/auth/me").reply(HTTP.OK, { _id: "user-a" });
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const before = sockets().length;

    failHandshake();
    await settle();
    act(() => { jest.advanceTimersByTime(5000); });

    expect(sockets().length).toBeGreaterThan(before);
    expect(refreshes()).toHaveLength(0);
    expect(status()).not.toBe("unauthenticated");
  });

  it("keeps reconnecting when the API is unreachable too", async () => {
    // The whole backend is down, so the probe cannot answer either. That is
    // still a network problem, not a credential problem.
    mock.onGet("/auth/me").networkError();
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const before = sockets().length;

    failHandshake();
    await settle();
    act(() => { jest.advanceTimersByTime(5000); });

    expect(sockets().length).toBeGreaterThan(before);
    expect(refreshes()).toHaveLength(0);
  });

  it("recovers over and over from repeated outages, without a budget to exhaust", async () => {
    mock.onGet("/auth/me").reply(HTTP.OK, { _id: "user-a" });
    render(<RealtimeProvider><div /></RealtimeProvider>);

    for (let i = 0; i < 4; i += 1) {
      const before = sockets().length;
      failHandshake();
      await settle();
      act(() => { jest.advanceTimersByTime(60000); });
      expect(sockets().length).toBeGreaterThan(before);
    }
    expect(refreshes()).toHaveLength(0);
  });

  it("refreshes exactly once when the credential really is stale", async () => {
    mock.onGet("/auth/me").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const before = sockets().length;

    failHandshake();
    await settle();

    expect(refreshes()).toHaveLength(1);
    expect(sockets().length).toBe(before + 1);
  });

  it("stops for good when the refresh is refused", async () => {
    mock.onGet("/auth/me").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/logout").reply(HTTP.OK, {});
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const before = sockets().length;

    failHandshake();
    await settle();
    act(() => { jest.advanceTimersByTime(120000); });

    expect(sockets().length).toBe(before);
    expect(status()).toBe("unauthenticated");
  });

  it("does not retry at all for a blocked account", async () => {
    // 403 is not something a refresh can fix, so spending the refresh on it
    // would be a request that cannot succeed by construction.
    mock.onGet("/auth/me").reply(403, {});
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const before = sockets().length;

    failHandshake();
    await settle();
    act(() => { jest.advanceTimersByTime(120000); });

    expect(sockets().length).toBe(before);
    expect(refreshes()).toHaveLength(0);
    expect(status()).toBe("unauthenticated");
  });

  it("falls back to ordinary reconnect when the refresh itself is unreachable", async () => {
    // The credential looks stale but the refresh endpoint cannot be reached —
    // which means the network is the problem after all (D6.2-A).
    mock.onGet("/auth/me").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").networkError();
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const before = sockets().length;

    failHandshake();
    await settle();
    act(() => { jest.advanceTimersByTime(5000); });

    expect(sockets().length).toBeGreaterThan(before);
  });

  it("an ordinary drop after a successful open never probes or refreshes", async () => {
    // The cheap path stays cheap: a normal disconnect must not cost a round
    // trip to /auth/me on every reconnect.
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const before = sockets().length;

    dropAfterOpen();
    act(() => { jest.advanceTimersByTime(5000); });

    expect(sockets().length).toBe(before + 1);
    expect(mock.history.get).toHaveLength(0);
    expect(refreshes()).toHaveLength(0);
  });
});

// ===========================================================================
// D6.2-F — one socket, and never the previous identity's
// ===========================================================================
describe("D6.2-F — no duplicate or orphaned sockets", () => {
  it("a refresh that resolves after an identity change opens no second socket", async () => {
    // The race: A's handshake fails, A's refresh is in flight, the user becomes
    // B, and A's `.then(connect)` fires afterwards. The "are we still wanted?"
    // flag used to be a ref shared across effect runs — B's effect cleared it
    // before A's continuation read it — so A's continuation connected again,
    // orphaning a socket that stayed open and kept writing to the shared store.
    let release;
    mock.onGet("/auth/me").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(() => new Promise((resolve) => {
      release = () => resolve([HTTP.OK, {}]);
    }));
    const { rerender } = render(<RealtimeProvider><div /></RealtimeProvider>);

    failHandshake();
    await settle();                       // the probe answered; refresh is held

    mockUser = { _id: "user-b", email: "b@example.com" };
    rerender(<RealtimeProvider><div /></RealtimeProvider>);
    const afterSwitch = sockets().length; // exactly one socket, B's

    await act(async () => { release?.(); for (let i = 0; i < 12; i += 1) await Promise.resolve(); });

    expect(sockets().length).toBe(afterSwitch);
  });

  it("a superseded socket cannot write to the store", async () => {
    // A stale socket's frames are the previous identity's private data.
    const { rerender } = render(<RealtimeProvider><div /></RealtimeProvider>);
    const aSocket = socket();
    act(() => { aSocket.readyState = 1; aSocket.onopen?.(); });

    mockUser = { _id: "user-b", email: "b@example.com" };
    rerender(<RealtimeProvider><div /></RealtimeProvider>);

    act(() => {
      aSocket.onmessage?.({ data: JSON.stringify({
        type: "portfolio_update", data: { total: 1520000 },
      }) });
      jest.advanceTimersByTime(200);
    });

    expect(useRealtimeStore.getState().portfolioUpdate).toBeNull();
  });

  it("a superseded socket's close does not disturb the live connection", async () => {
    const { rerender } = render(<RealtimeProvider><div /></RealtimeProvider>);
    const aSocket = socket();

    mockUser = { _id: "user-b", email: "b@example.com" };
    rerender(<RealtimeProvider><div /></RealtimeProvider>);
    act(() => { socket().readyState = 1; socket().onopen?.(); });
    expect(status()).toBe("live");

    act(() => { aSocket.onclose?.({ code: 1006 }); });

    expect(status()).toBe("live");
  });

  it("signing out closes the socket and opens no replacement", async () => {
    const { rerender } = render(<RealtimeProvider><div /></RealtimeProvider>);
    act(() => { socket().readyState = 1; socket().onopen?.(); });
    const before = sockets().length;

    mockUser = null;
    rerender(<RealtimeProvider><div /></RealtimeProvider>);
    act(() => { jest.advanceTimersByTime(120000); });

    expect(sockets().length).toBe(before);
    expect(status()).toBe("offline");
  });

  it("B gets exactly one fresh socket after A signs out", async () => {
    const { rerender } = render(<RealtimeProvider><div /></RealtimeProvider>);
    act(() => { socket().readyState = 1; socket().onopen?.(); });

    mockUser = null;
    rerender(<RealtimeProvider><div /></RealtimeProvider>);
    const afterLogout = sockets().length;

    mockUser = { _id: "user-b", email: "b@example.com" };
    rerender(<RealtimeProvider><div /></RealtimeProvider>);

    expect(sockets().length).toBe(afterLogout + 1);
    expect(status()).toBe("connecting");
  });

  it("a reconnect never leaves the previous attempt's socket open", async () => {
    render(<RealtimeProvider><div /></RealtimeProvider>);
    const first = socket();

    dropAfterOpen();
    act(() => { jest.advanceTimersByTime(5000); });

    expect(first.readyState).toBe(3); // CLOSED
    expect(socket()).not.toBe(first);
  });
});

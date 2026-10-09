/**
 * D6.2 — SESSION LIFECYCLE HARDENING regression suite.
 *
 * D6.1 made the browser session *work*: cookies flow, one refresh serves N
 * concurrent 401s, and a dead session is distinguishable from a deliberate
 * sign-out. D6.2 is about the cases where that machinery reached the wrong
 * conclusion, and every test here is named for the user-visible symptom it
 * prevents rather than for the code it touches.
 *
 * Transport-level throughout: `axios-mock-adapter` replaces the adapter on the
 * app's real axios instance, so every interceptor under test is the production
 * one. A service-level mock would prove nothing about the client stack, which
 * is where all six defects lived.
 */
import { act, render, waitFor } from "@testing-library/react";
import MockAdapter from "axios-mock-adapter";

import api, {
  attemptSilentRefresh,
  currentAuthEpoch,
  refreshSession,
  resetRefreshState,
  SessionChangedError,
  SESSION_EXPIRED_EVENT,
  SESSION_STATE,
  SESSION_STATE_EVENT,
} from "../services/api";
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

const refreshes = () => mock.history.post.filter((r) => r.url === "/auth/refresh");
const expiryEvents = () => {
  const heard = [];
  const listener = () => heard.push("expired");
  window.addEventListener(SESSION_EXPIRED_EVENT, listener);
  return {
    heard,
    stop: () => window.removeEventListener(SESSION_EXPIRED_EVENT, listener),
  };
};

// ===========================================================================
// D6.2-A — a transient failure is not a dead session
// ===========================================================================
describe("D6.2-A — transient failures never become SESSION_EXPIRED", () => {
  it.each([
    ["the API is unreachable", (m) => m.onPost("/auth/refresh").networkError()],
    ["the API returns 500", (m) => m.onPost("/auth/refresh").reply(500, {})],
    ["the API returns 502", (m) => m.onPost("/auth/refresh").reply(502, {})],
    ["the refresh rate limit fires", (m) => m.onPost("/auth/refresh").reply(429, {})],
    ["the request times out", (m) => m.onPost("/auth/refresh").timeout()],
  ])("does not sign the user out when %s", async (_label, stub) => {
    // THE defect. Every one of these used to latch the refresh machinery off
    // for the life of the page and announce SESSION_EXPIRED — so a backend
    // restart, a proxy hiccup or the client's own rate limit threw the user
    // onto the login screen and told them their session had ended, while their
    // seven-day refresh cookie sat untouched in the browser.
    stub(mock);
    const listener = expiryEvents();

    await expect(refreshSession()).rejects.toBeDefined();
    listener.stop();

    expect(listener.heard).toEqual([]);
  });

  it("still signs the user out when the server actually refuses (401)", async () => {
    // The other side of the same coin: the guard must not be so permissive
    // that a genuinely dead session keeps looking alive.
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/logout").reply(HTTP.OK, {});
    const listener = expiryEvents();

    await expect(refreshSession()).rejects.toBeDefined();
    listener.stop();

    expect(listener.heard).toEqual(["expired"]);
  });

  it("signs the user out on 403 too — a blocked account cannot be refreshed", async () => {
    mock.onPost("/auth/refresh").reply(HTTP.FORBIDDEN ?? 403, {});
    mock.onPost("/auth/logout").reply(HTTP.OK, {});
    const listener = expiryEvents();

    await expect(refreshSession()).rejects.toBeDefined();
    listener.stop();

    expect(listener.heard).toEqual(["expired"]);
  });

  it("lets a later 401 try again once the blip has passed", async () => {
    // A transient failure must not latch. It does hold a short cool-down, so
    // this test proves recovery is possible rather than that it is instant.
    mock.onPost("/auth/refresh").networkError();
    await expect(refreshSession()).rejects.toBeDefined();

    jest.spyOn(Date, "now").mockReturnValue(Date.now() + 60000);
    mock.reset();
    mock.onGet("/portfolio").replyOnce(HTTP.UNAUTHORIZED, {});
    mock.onGet("/portfolio").reply(HTTP.OK, { ok: true });
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});

    const { data } = await api.get("/portfolio");

    expect(data.ok).toBe(true);
    expect(refreshes()).toHaveLength(1);
    Date.now.mockRestore();
  });

  it("does not start a refresh storm while the backend is down", async () => {
    // Not letting a transient failure latch has a cost: without a brake, every
    // subsequent 401 is free to try again, and a dashboard polling against a
    // dead API turns one outage into a refresh per request. The requests here
    // are deliberately SEQUENTIAL — concurrent ones would be absorbed by the
    // promise-coalescing queue and would prove nothing about the cool-down.
    mock.onPost("/auth/refresh").networkError();
    const paths = Array.from({ length: 10 }, (_, i) => `/widget-${i}`);
    paths.forEach((p) => mock.onGet(p).reply(HTTP.UNAUTHORIZED, {}));

    for (const path of paths) {
      await api.get(path).catch(() => {});
    }

    expect(refreshes()).toHaveLength(1);
  });
});

// ===========================================================================
// D6.2-B — a queued request must not replay under a new identity
// ===========================================================================
describe("D6.2-B — cross-identity replay", () => {
  it("abandons a queued request when the identity changes mid-refresh", async () => {
    // The serious one. A's request parks on the refresh await; A signs out and
    // B signs in; the request is then re-sent carrying B's cookies. For a GET
    // that renders A's page with B's data. For `POST /trades` it is an order
    // placed in the wrong brokerage account.
    let releaseRefresh;
    mock.onGet("/trades").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(() => new Promise((resolve) => {
      releaseRefresh = () => resolve([HTTP.OK, {}]);
    }));

    const inflight = api.get("/trades");
    await Promise.resolve();
    // …the user signs out and somebody else signs in.
    resetRefreshState();
    releaseRefresh?.();

    await expect(inflight).rejects.toBeInstanceOf(SessionChangedError);
    // And it was ABANDONED, not merely failed: the request never went out again.
    expect(mock.history.get.filter((r) => r.url === "/trades")).toHaveLength(1);
  });

  it("abandons it when the identity changes DURING the refresh round trip", async () => {
    // The window the first test does not reach. There are two epoch checks —
    // one before the refresh is started and one after it resolves — and only
    // the second one covers the case where the sign-out lands while the refresh
    // is on the wire. Bumping the epoch from inside the refresh handler puts it
    // there exactly. (Found by mutation: deleting the post-refresh check left
    // the suite green.)
    mock.onGet("/trades").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(() => {
      resetRefreshState();          // the user signed out and back in as B
      return [HTTP.OK, {}];
    });

    await expect(api.get("/trades")).rejects.toBeInstanceOf(SessionChangedError);

    expect(mock.history.get.filter((r) => r.url === "/trades")).toHaveLength(1);
  });

  it("replays normally when the identity has not changed", async () => {
    // The regression guard on the other side: an epoch check that fires when it
    // should not would break every ordinary refresh.
    mock.onGet("/trades").replyOnce(HTTP.UNAUTHORIZED, {});
    mock.onGet("/trades").reply(HTTP.OK, { ok: true });
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});

    const { data } = await api.get("/trades");

    expect(data.ok).toBe(true);
  });

  it("bumps the epoch on sign-in, sign-out and expiry alike", async () => {
    const start = currentAuthEpoch();
    resetRefreshState();
    expect(currentAuthEpoch()).toBe(start + 1);

    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/logout").reply(HTTP.OK, {});
    await expect(refreshSession()).rejects.toBeDefined();

    // An expiry is an identity boundary too: anything queued under the dead
    // session must not be resurrected by the next sign-in.
    expect(currentAuthEpoch()).toBe(start + 2);
  });

  it("does not send a mutation queued under the previous identity", async () => {
    let releaseRefresh;
    mock.onPost("/trades").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(() => new Promise((resolve) => {
      releaseRefresh = () => resolve([HTTP.OK, {}]);
    }));

    const order = api.post("/trades", { symbol: "RELIANCE", qty: 10 });
    await Promise.resolve();
    resetRefreshState();
    releaseRefresh?.();

    await expect(order).rejects.toBeInstanceOf(SessionChangedError);
    expect(mock.history.post.filter((r) => r.url === "/trades")).toHaveLength(1);
  });
});

// ===========================================================================
// D6.2 — bootstrap recovery: a reload must not end the session
// ===========================================================================
describe("bootstrap — a reload after 15 minutes stays signed in", () => {
  function Probe({ onState }) {
    const { user, sessionEnd, sessionState } = useAuth();
    onState({ user, sessionEnd, sessionState });
    return null;
  }

  it("recovers the session through the refresh cookie", async () => {
    // The original "my session keeps dying" report, in its purest form: the
    // access token lives 15 minutes and the refresh cookie seven days, and
    // reloading the tab used to throw away the second one.
    mock.onGet("/auth/me").replyOnce(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});
    mock.onGet("/auth/me").reply(HTTP.OK, { _id: "user-a", email: "a@example.com" });

    const states = [];
    render(<AuthProvider><Probe onState={(s) => states.push(s)} /></AuthProvider>);

    await waitFor(() => expect(states.at(-1).user).toBeTruthy());
    expect(states.at(-1).user.email).toBe("a@example.com");
    expect(refreshes()).toHaveLength(1);
  });

  it("tells a first-time visitor nothing about an expired session", async () => {
    // A visitor who was never signed in has no cookies either, so the 401 is
    // ambiguous. Announcing an expiry here would greet a brand-new user with
    // "your session expired".
    mock.onGet("/auth/me").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});

    const states = [];
    render(<AuthProvider><Probe onState={(s) => states.push(s)} /></AuthProvider>);

    await waitFor(() => expect(states.at(-1).user).toBe(false));
    expect(states.at(-1).sessionEnd).toBeNull();
  });

  it("attempts the bootstrap refresh exactly once", async () => {
    mock.onGet("/auth/me").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});

    render(<AuthProvider><Probe onState={() => {}} /></AuthProvider>);
    await waitFor(() => expect(refreshes().length).toBeGreaterThan(0));
    await act(async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); });

    expect(refreshes()).toHaveLength(1);
  });

  it("does not claim a session ended when the API is simply unreachable", async () => {
    mock.onGet("/auth/me").networkError();
    const listener = expiryEvents();

    const states = [];
    render(<AuthProvider><Probe onState={(s) => states.push(s)} /></AuthProvider>);
    await waitFor(() => expect(states.at(-1).user).toBe(false));
    listener.stop();

    expect(listener.heard).toEqual([]);
    expect(states.at(-1).sessionEnd).toBeNull();
    expect(refreshes()).toHaveLength(0);
  });

  it("a silent probe does not suppress an expiry a real request discovered", async () => {
    // Coalescing must not let the *quietest* caller decide what everyone hears.
    mock.onPost("/auth/refresh").reply(() => new Promise((resolve) => {
      setTimeout(() => resolve([HTTP.UNAUTHORIZED, {}]), 0);
    }));
    mock.onPost("/auth/logout").reply(HTTP.OK, {});
    const listener = expiryEvents();

    const silent = attemptSilentRefresh().catch(() => {});
    const loud = refreshSession().catch(() => {});
    await Promise.all([silent, loud]);
    listener.stop();

    expect(listener.heard).toEqual(["expired"]);
  });
});

// ===========================================================================
// D6.2-C — the four-state session machine
// ===========================================================================
describe("D6.2-C — session state machine", () => {
  function Probe({ onState }) {
    const { user, sessionState } = useAuth();
    onState({ user, sessionState });
    return null;
  }

  const mountSignedIn = async (states) => {
    mock.onGet("/auth/me").reply(HTTP.OK, { _id: "user-a", email: "a@example.com" });
    render(<AuthProvider><Probe onState={(s) => states.push(s)} /></AuthProvider>);
    await waitFor(() => expect(states.at(-1).user).toBeTruthy());
  };

  it("AUTHENTICATED → REFRESHING → AUTHENTICATED, without ever looking logged out", async () => {
    const states = [];
    await mountSignedIn(states);
    expect(states.at(-1).sessionState).toBe(SESSION_STATE.AUTHENTICATED);

    let release;
    mock.onGet("/portfolio").replyOnce(HTTP.UNAUTHORIZED, {});
    mock.onGet("/portfolio").reply(HTTP.OK, {});
    // The refresh is held open so REFRESHING is observable rather than a state
    // that exists for one microtask and is gone before anything can read it.
    mock.onPost("/auth/refresh").reply(() => new Promise((resolve) => {
      release = () => resolve([HTTP.OK, {}]);
    }));

    const inflight = api.get("/portfolio");
    await waitFor(() => expect(states.at(-1).sessionState).toBe(SESSION_STATE.REFRESHING));
    // The point of having the state at all: the user is still signed in.
    expect(states.at(-1).user).toBeTruthy();

    await act(async () => { release?.(); await inflight; });
    await waitFor(() => expect(states.at(-1).sessionState).toBe(SESSION_STATE.AUTHENTICATED));
  });

  it("AUTHENTICATED → REFRESHING → SESSION_EXPIRED", async () => {
    const states = [];
    await mountSignedIn(states);

    mock.onGet("/portfolio").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/logout").reply(HTTP.OK, {});

    await act(async () => { await api.get("/portfolio").catch(() => {}); });

    await waitFor(() => expect(states.at(-1).sessionState).toBe(SESSION_STATE.SESSION_EXPIRED));
    expect(states.at(-1).user).toBe(false);
  });

  it("an explicit logout reaches USER_SIGNED_OUT, never SESSION_EXPIRED", async () => {
    const states = [];
    let auth;
    function Capture() {
      auth = useAuth();
      states.push({ sessionState: auth.sessionState, sessionEnd: auth.sessionEnd });
      return null;
    }
    mock.onGet("/auth/me").reply(HTTP.OK, { _id: "user-a", email: "a@example.com" });
    mock.onPost("/auth/logout").reply(HTTP.OK, {});
    render(<AuthProvider><Capture /></AuthProvider>);
    await waitFor(() => expect(states.at(-1).sessionState).toBe(SESSION_STATE.AUTHENTICATED));

    await act(async () => { await auth.logout(); });

    expect(states.at(-1).sessionState).toBe(SESSION_STATE.USER_SIGNED_OUT);
    expect(states.at(-1).sessionEnd).toBe(SESSION_END.SIGNED_OUT);
  });

  it("a network failure never reaches SESSION_EXPIRED", async () => {
    const states = [];
    await mountSignedIn(states);

    mock.onGet("/portfolio").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").networkError();

    await act(async () => { await api.get("/portfolio").catch(() => {}); });

    expect(states.at(-1).sessionState).not.toBe(SESSION_STATE.SESSION_EXPIRED);
    expect(states.at(-1).user).toBeTruthy();
  });

  it("a finished session is not resurrected by a stray AUTHENTICATED signal", async () => {
    const states = [];
    await mountSignedIn(states);
    act(() => { window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT)); });
    await waitFor(() => expect(states.at(-1).sessionState).toBe(SESSION_STATE.SESSION_EXPIRED));

    act(() => {
      window.dispatchEvent(new CustomEvent(SESSION_STATE_EVENT,
        { detail: { state: SESSION_STATE.AUTHENTICATED } }));
    });

    expect(states.at(-1).sessionState).toBe(SESSION_STATE.SESSION_EXPIRED);
  });
});

// ===========================================================================
// D6.2-D — a definitive expiry clears the server-side cookies
// ===========================================================================
describe("D6.2-D — no stale credential survives a definitive expiry", () => {
  it("asks the server to clear its cookies", async () => {
    // A refresh refused because the family was revoked (a logout elsewhere, or
    // reuse detection) can leave an access cookie that is still minutes from
    // expiring — so another tab, or a reload, still looked signed in. Only the
    // server can clear it: both cookies are HttpOnly.
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/logout").reply(HTTP.OK, {});

    await expect(refreshSession()).rejects.toBeDefined();
    await act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); });

    expect(mock.history.post.filter((r) => r.url === "/auth/logout")).toHaveLength(1);
  });

  it("drops the bootstrap bearer token as well", async () => {
    localStorage.setItem("token", "stale-bootstrap-token");
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/logout").reply(HTTP.OK, {});

    await expect(refreshSession()).rejects.toBeDefined();

    expect(localStorage.getItem("token")).toBeNull();
  });

  it("does NOT clear the server session on a transient failure", async () => {
    // Calling logout here would turn a network blip into a real, irreversible
    // sign-out — the opposite of the recovery D6.2-A is for.
    mock.onPost("/auth/refresh").networkError();

    await expect(refreshSession()).rejects.toBeDefined();
    await act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); });

    expect(mock.history.post.filter((r) => r.url === "/auth/logout")).toHaveLength(0);
  });

  it("does not clear cookies for a visitor who was never signed in", async () => {
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});

    await expect(attemptSilentRefresh()).rejects.toBeDefined();
    await act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); });

    expect(mock.history.post.filter((r) => r.url === "/auth/logout")).toHaveLength(0);
  });
});

// ===========================================================================
// Interceptor hygiene
// ===========================================================================
describe("interceptor recursion and endpoint exemption", () => {
  it("never refreshes in response to the refresh endpoint itself", async () => {
    mock.onPost("/auth/refresh").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/logout").reply(HTTP.OK, {});

    await expect(refreshSession()).rejects.toBeDefined();

    expect(refreshes()).toHaveLength(1);
  });

  it.each(["/auth/me", "/auth/login", "/auth/register", "/auth/logout"])(
    "does not refresh on a 401 from %s", async (path) => {
      const verb = path === "/auth/me" ? "onGet" : "onPost";
      mock[verb](path).reply(HTTP.UNAUTHORIZED, {});

      await expect(
        path === "/auth/me" ? api.get(path) : api.post(path, {}),
      ).rejects.toBeDefined();

      expect(refreshes()).toHaveLength(0);
    });

  it("matches the exemption list by exact path, not by substring", async () => {
    // `String.includes` silently exempts any route that merely CONTAINS an
    // exempt path — a route that quietly stops refreshing is a bug nobody
    // notices until a session dies on that page alone.
    mock.onPost("/brokers/zerodha/auth/login-url").replyOnce(HTTP.UNAUTHORIZED, {});
    mock.onPost("/brokers/zerodha/auth/login-url").reply(HTTP.OK, { ok: true });
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});

    const { data } = await api.post("/brokers/zerodha/auth/login-url", {});

    expect(data.ok).toBe(true);
    expect(refreshes()).toHaveLength(1);
  });

  it("a replay does not carry the stale bearer token", async () => {
    // The bootstrap token is dropped by a successful refresh, but the replayed
    // request was built with the header already on it. Leaving it would not
    // break auth — the server prefers the cookie — but `security/csrf.py`
    // exempts any Bearer-carrying request, so the replayed mutation would skip
    // the CSRF layer on the strength of a credential that no longer works.
    localStorage.setItem("token", "stale-bootstrap-token");
    document.cookie = "csrf_token=csrf-value-123; path=/";
    mock.onPost("/trades").replyOnce(HTTP.UNAUTHORIZED, {});
    mock.onPost("/trades").reply(HTTP.OK, { ok: true });
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});

    await api.post("/trades", { symbol: "RELIANCE" });

    const attempts = mock.history.post.filter((r) => r.url === "/trades");
    expect(attempts).toHaveLength(2);
    expect(attempts[0].headers.Authorization).toBe("Bearer stale-bootstrap-token");
    expect(attempts[1].headers.Authorization).toBeUndefined();
    expect(attempts[1].headers["X-CSRF-Token"]).toBe("csrf-value-123");
  });

  it("a request that 401s again after a successful refresh gives up", async () => {
    mock.onGet("/blocked").reply(HTTP.UNAUTHORIZED, {});
    mock.onPost("/auth/refresh").reply(HTTP.OK, {});

    await expect(api.get("/blocked")).rejects.toBeDefined();

    expect(mock.history.get.filter((r) => r.url === "/blocked")).toHaveLength(2);
    expect(refreshes()).toHaveLength(1);
  });

  it("leaves ordinary 2xx and non-401 errors completely alone", async () => {
    mock.onGet("/ok").reply(HTTP.OK, { ok: true });
    mock.onGet("/missing").reply(404, {});
    mock.onGet("/broken").reply(500, {});

    await api.get("/ok");
    await expect(api.get("/missing")).rejects.toBeDefined();
    await expect(api.get("/broken")).rejects.toBeDefined();

    expect(refreshes()).toHaveLength(0);
  });

  it("does not refresh on a 403 — refreshing cannot unblock an account", async () => {
    mock.onGet("/admin/thing").reply(403, {});

    await expect(api.get("/admin/thing")).rejects.toBeDefined();

    expect(refreshes()).toHaveLength(0);
  });
});

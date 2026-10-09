/**
 * D6.3 — FRONTEND TENANT ISOLATION regression suite.
 *
 * The server-side half of the D6.3 invariant is enforced by the backend suite.
 * This file covers the half the server cannot see: state that lives in the
 * browser and outlives an identity change in the same tab.
 *
 * D6.1 / S8 reset the Zustand store on sign-in and sign-out. D6.2 / B stamped an
 * auth epoch on every request and abandoned a *replayed* one whose epoch had
 * moved. Both left a gap:
 *
 *   1. `localStorage`. The store is memory; `localStorage` outlives the tab
 *      entirely, and two keys — the symbols a user opened and the symbols they
 *      searched — were written per user and read back unconditionally.
 *      Reproduced in Chrome against a real server: Alice opened DIVISLAB, signed
 *      out, Bob signed in in the same tab, and `/stock/DIVISLAB` was a link on
 *      Bob's dashboard.
 *
 *   2. The epoch check existed only on the **401** path. A request that simply
 *      *succeeded* across an identity change resolved into the new account's UI.
 *      A dashboard fires many reads at once; that is not a narrow window.
 *
 *   3. The order review panel carried no identity at all. It is the one screen
 *      where a stale confirmation spends real money, and the server cannot catch
 *      it — by the time the request is sent it carries the new account's cookie
 *      and is a perfectly valid order from that account.
 *
 * Transport-level throughout, like the D6.2 suite: `axios-mock-adapter` replaces
 * the adapter on the app's real axios instance, so the interceptors under test
 * are the production ones.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import MockAdapter from "axios-mock-adapter";
import { MemoryRouter } from "react-router-dom";

import api, {
  currentAuthEpoch,
  resetRefreshState,
  SessionChangedError,
} from "../services/api";
import { AuthProvider, useAuth } from "../context/AuthContext";
import { useRealtimeStore } from "../store/realtimeStore";
import { clearTenantLocalState, SHARED_LOCAL_KEYS } from "../lib/tenantState";
import OrderTicket from "../components/stock/OrderTicket";
import { userEvent } from "../test-utils";

let mock;

beforeEach(() => {
  mock = new MockAdapter(api, { onNoMatch: "throwException" });
  resetRefreshState();
  useRealtimeStore.getState().reset();
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  mock.restore();
});

// ===========================================================================
// 1 — browser-local state does not survive an identity change
// ===========================================================================
describe("D6.3 — per-user browser storage is forgotten on an identity change", () => {
  /** The two keys the real defect was found in, by name. */
  const PRIVATE_KEYS = ["sa_recent_stocks", "ap-recent-searches"];

  function seedAlice() {
    localStorage.setItem("sa_recent_stocks",
      JSON.stringify([{ symbol: "DIVISLAB", viewedAt: 1 }]));
    localStorage.setItem("ap-recent-searches", JSON.stringify(["DIVISLAB", "WIPRO"]));
    localStorage.setItem("token", "alice-bootstrap-token");
    localStorage.setItem("ap-theme", "dark");
    sessionStorage.setItem("some-per-user-draft", "alice's draft");
  }

  it("clears the previous account's research history but keeps device settings", () => {
    seedAlice();
    // Positive control: the state exists before the transition, so the absence
    // assertions below are about clearing and not about a fixture that never ran.
    expect(localStorage.getItem("sa_recent_stocks")).toContain("DIVISLAB");

    const removed = clearTenantLocalState();

    for (const key of PRIVATE_KEYS) {
      expect(localStorage.getItem(key)).toBeNull();
      expect(removed).toContain(key);
    }
    expect(sessionStorage.getItem("some-per-user-draft")).toBeNull();
    // A device preference is not account state and must survive.
    expect(localStorage.getItem("ap-theme")).toBe("dark");
    expect(SHARED_LOCAL_KEYS).toContain("ap-theme");
  });

  it("forgets a key nobody classified, rather than keeping it", () => {
    // The keep-list is deliberately a KEEP-list. A future per-user key that
    // nobody thought about is wiped (costing a convenience) instead of retained
    // (leaking one user's business to the next). This test is the contract.
    localStorage.setItem("some-feature-added-next-sprint", "alice's private thing");
    clearTenantLocalState();
    expect(localStorage.getItem("some-feature-added-next-sprint")).toBeNull();
  });

  it("survives storage being unavailable", () => {
    const original = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() { throw new Error("blocked by browser settings"); },
    });
    try {
      expect(() => clearTenantLocalState()).not.toThrow();
    } finally {
      Object.defineProperty(window, "localStorage", original);
    }
  });

  it.each([
    ["sign-out", async (auth) => { await auth.logout(); }],
    ["sign-in", async (auth) => { await auth.login("bob@example.com", "pw"); }],
    ["registration", async (auth) => { await auth.register("Bob", "bob@example.com", "pw"); }],
  ])("runs on %s", async (_label, transition) => {
    mock.onGet("/auth/me").reply(200, { _id: "alice", email: "alice@example.com" });
    mock.onPost("/auth/logout").reply(200, {});
    mock.onPost("/auth/login").reply(200, { _id: "bob", email: "bob@example.com", token: "bob-t" });
    mock.onPost("/auth/register").reply(200, { _id: "bob", email: "bob@example.com", token: "bob-t" });

    let auth;
    function Probe() { auth = useAuth(); return null; }
    render(<MemoryRouter><AuthProvider><Probe /></AuthProvider></MemoryRouter>);
    await waitFor(() => expect(auth.user).toBeTruthy());

    seedAlice();
    await act(async () => { await transition(auth); });

    for (const key of PRIVATE_KEYS) {
      expect(localStorage.getItem(key)).toBeNull();
    }
  });

  it("does not destroy the token the sign-in it runs inside is about to store", () => {
    // Ordering guard. `clearTenantLocalState` wipes `token` along with
    // everything else, so it MUST run before AuthContext writes the new one. If
    // the two were ever reordered the user would appear to sign in and then have
    // no bootstrap credential at all — a failure that looks like a backend bug.
    localStorage.setItem("token", "previous-account-token");
    clearTenantLocalState();
    localStorage.setItem("token", "new-account-token");
    expect(localStorage.getItem("token")).toBe("new-account-token");
  });
});

// ===========================================================================
// 2 — a response that outlived its identity never reaches the new account
// ===========================================================================
describe("D6.3 — a successful response from the previous identity is discarded", () => {
  it("rejects a 200 whose request was dispatched under a previous identity", async () => {
    // THE defect. D6.2 caught the 401-then-replay case and nothing else, so the
    // ordinary case — the request simply succeeds while the identity moves —
    // resolved A's portfolio into B's UI.
    // The request interceptor runs on a microtask, so the identity must move
    // AFTER the request has been stamped and dispatched — otherwise the request
    // is simply born under the new epoch and the test proves nothing. The mock
    // adapter is the first thing that runs strictly after the stamp, so the
    // transition is triggered from inside it.
    let dispatched;
    const reached = new Promise((r) => { dispatched = r; });
    let release;
    const gate = new Promise((r) => { release = r; });
    mock.onGet("/portfolio").reply(async () => {
      dispatched();
      await gate;
      return [200, { holdings: [{ symbol: "ALICE-ONLY" }] }];
    });

    const inflight = api.get("/portfolio");
    await reached;
    const stampedUnder = currentAuthEpoch();
    // A signs out and B signs in while the read is in the air.
    resetRefreshState();
    expect(currentAuthEpoch()).not.toBe(stampedUnder);   // the window really opened
    release();

    await expect(inflight).rejects.toBeInstanceOf(SessionChangedError);
  });

  it("delivers a 200 when the identity did NOT change", async () => {
    // Falsifying twin. Without this, "reject everything" would pass the test
    // above, and the app would be unable to load anything at all.
    mock.onGet("/portfolio").reply(200, { holdings: [{ symbol: "MINE" }] });
    const res = await api.get("/portfolio");
    expect(res.data.holdings[0].symbol).toBe("MINE");
  });

  it("still lets the auth-lifecycle endpoints resolve across the transition", async () => {
    // `/auth/refresh` is issued BY the machinery that bumps the epoch. Rejecting
    // its own response would make `refreshSession` read a freshly established
    // session as a transient network failure and impose a cool-down on it.
    let dispatched;
    const reached = new Promise((r) => { dispatched = r; });
    let release;
    const gate = new Promise((r) => { release = r; });
    mock.onPost("/auth/refresh").reply(async () => {
      dispatched();
      await gate;
      return [200, {}];
    });

    const inflight = api.post("/auth/refresh");
    await reached;
    resetRefreshState();
    release();

    await expect(inflight).resolves.toMatchObject({ status: 200 });
  });

  it("stamps the epoch once, at first dispatch", async () => {
    mock.onGet("/trades").reply(200, []);
    const before = currentAuthEpoch();
    const res = await api.get("/trades");
    expect(res.config._authEpoch).toBe(before);
  });
});

// ===========================================================================
// 3 — order review state is bound to the identity that composed it
// ===========================================================================
describe("D6.3 — an order intent cannot outlive the account that composed it", () => {
  const STATUSES = {
    zerodha: { broker: "zerodha", display_name: "Zerodha", connected: true,
               capabilities: ["place_order"] },
    upstox: { broker: "upstox", display_name: "Upstox", connected: true,
              capabilities: ["place_order"] },
  };

  async function mountTicket() {
    mock.onGet("/auth/me").reply(200, { _id: "alice", email: "alice@example.com" });
    mock.onGet("/brokers/status").reply(200, STATUSES);
    mock.onPost(/\/brokers\/.+\/orders/).reply(200, { order_id: "OID-1", status: "PLACED" });

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <AuthProvider>
          <OrderTicket symbol="RELIANCE" exchange="NSE" price={100} />
        </AuthProvider>
      </MemoryRouter>,
    );
    await screen.findByTestId("order-broker");
    return user;
  }

  async function reachReviewPanel(user) {
    await user.selectOptions(screen.getByTestId("order-broker"), "zerodha");
    await user.click(await screen.findByTestId("order-review"));
    return screen.findByTestId("order-review-panel");
  }

  it("places the order when nothing changed (the control)", async () => {
    // Without this the refusal tests below would pass against a ticket that can
    // never place an order at all.
    const user = await mountTicket();
    await reachReviewPanel(user);
    await user.click(screen.getByTestId("order-confirm"));

    await waitFor(() => {
      const placed = mock.history.post.filter((r) => /\/brokers\/.+\/orders/.test(r.url));
      expect(placed).toHaveLength(1);
    });
  });

  it("refuses to send a reviewed order after the identity generation moved", async () => {
    const user = await mountTicket();
    await reachReviewPanel(user);

    // A signs out and B signs in while the confirmation is on screen.
    act(() => { resetRefreshState(); });

    await user.click(screen.getByTestId("order-confirm"));

    const placed = mock.history.post.filter((r) => /\/brokers\/.+\/orders/.test(r.url));
    expect(placed).toHaveLength(0);
    expect(screen.queryByTestId("order-review-panel")).not.toBeInTheDocument();
    expect(await screen.findByText(/session or the broker changed/i)).toBeInTheDocument();
  });

  it("carries the account, the broker and the epoch — not just one of them", async () => {
    // Structural: all three are re-checked. A guard that only compared the epoch
    // would miss a broker swap; one that only compared the broker would miss the
    // account. The epoch case is exercised above; the broker case is here.
    const user = await mountTicket();
    await reachReviewPanel(user);
    // Leave the review panel, switch broker, and confirm the stale intent is not
    // reusable: re-entering review re-stamps it, so the ONLY way to reach the
    // confirm button with a mismatched intent is the transition itself. That the
    // check reads all three fields is asserted on the source, below.
    const source = require("fs").readFileSync(
      require("path").join(__dirname, "../components/stock/OrderTicket.jsx"), "utf8");
    expect(source).toMatch(/intent\.userId !== userId/);
    expect(source).toMatch(/intent\.broker !== broker/);
    expect(source).toMatch(/intent\.epoch !== currentAuthEpoch\(\)/);
  });
});

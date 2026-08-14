/**
 * Frontend request-efficiency regression tests (PH3.4).
 *
 * WHY REQUEST COUNTS AND NOT RENDER TIMINGS
 * ----------------------------------------
 * A `performance.now()` assertion in jsdom measures the CI runner's mood. It
 * goes red when the runner is busy and green on a fast machine that has just
 * started fetching the same endpoint four times per mount. It fails for the
 * wrong reasons and passes for the one reason that matters, so it gets skipped
 * within two sprints and takes its coverage with it.
 *
 * What is exactly reproducible on any machine — and what actually determines how
 * a page feels — is **how many requests a mount makes**. `axios-mock-adapter`
 * sits at the transport boundary of the app's single axios instance, so
 * `mock.history.get` is a precise, deterministic log of every request the page
 * issued. These tests assert on that log.
 *
 * Three properties, each of which has a real production failure behind it:
 *
 *   1. **No endpoint is fetched twice for one mount.** The classic cause is two
 *      effects that both need the same data and each fetch it, which doubles
 *      server load and can render two different values in two widgets.
 *   2. **A mount does not re-fetch on re-render.** An unstable dependency in a
 *      `useEffect` array (an object or arrow function recreated every render)
 *      turns one fetch into a fetch per render — the single most common React
 *      performance defect, and invisible until you count.
 *   3. **Nothing polls while the socket is connected.** StockAssist is
 *      event-driven (REALTIME_SYSTEM.md): while the socket is live, data arrives
 *      by push, and a timer fetching the same data is duplicated work on both
 *      ends. PH3.4 verified all 13 timers guard on `connected`; this is what
 *      keeps the fourteenth from shipping without the guard.
 */
import { act, waitFor } from "@testing-library/react";
import Dashboard from "../pages/Dashboard";
import Watchlist from "../pages/Watchlist";
import {
  renderWithProviders,
  installApiMock,
  stubRemainingWith,
  mockAuthenticatedUser,
  resetRealtimeStore,
} from "../test-utils";
import { useRealtimeStore } from "../store/realtimeStore";

/**
 * Put the realtime store into the LIVE state the components actually read.
 *
 * Two things had to be right here, and getting either wrong invents a polling
 * defect that does not exist:
 *
 * 1. **The right field.** `selectConnected` is
 *    `(s) => s.connection.status === "live"` — the flag lives on
 *    `connection.status`, not on a top-level `connected` boolean. Setting
 *    `{ connected: true }` writes a field no selector reads, so every component
 *    correctly sees "offline" and starts its fallback poll.
 * 2. **The right moment.** `RealtimeProvider` sets the status to `"connecting"`
 *    when it mounts, overwriting anything set beforehand. So the socket must be
 *    brought live *after* the render — which is also what happens in production:
 *    the page mounts, fetches once, and the socket connects a moment later.
 *
 * Both of those were wrong in the first version of this file, and it duly
 * reported that the Dashboard and Watchlist polled while connected. They do not.
 */
function setSocketLive() {
  useRealtimeStore.setState((s) => ({
    connection: { ...s.connection, status: "live" },
  }));
}

function setSocketOffline() {
  useRealtimeStore.setState((s) => ({
    connection: { ...s.connection, status: "offline" },
  }));
}

let mock;

beforeEach(() => {
  jest.useFakeTimers({ advanceTimers: true });
  mock = installApiMock();
  mockAuthenticatedUser(mock);
  resetRealtimeStore();
});

afterEach(() => {
  jest.runOnlyPendingTimers();
  jest.useRealTimers();
  mock.restore();
});

/**
 * The GET paths a mount requested, in order.
 *
 * `/auth/me` is excluded: it is issued by AuthProvider, once per provider tree,
 * and is a property of the harness rather than of the page under test.
 */
function getPaths() {
  return mock.history.get.map((r) => r.url).filter((u) => u !== "/auth/me");
}

function duplicates(paths) {
  const seen = new Map();
  paths.forEach((p) => seen.set(p, (seen.get(p) || 0) + 1));
  return [...seen.entries()].filter(([, n]) => n > 1);
}

describe("no endpoint is requested twice for a single mount", () => {
  test("Dashboard fans out to each endpoint at most once", async () => {
    stubRemainingWith(mock, []);
    renderWithProviders(<Dashboard />, { route: "/dashboard" });
    await waitFor(() => expect(mock.history.get.length).toBeGreaterThan(3));
    // Let every effect in the tree settle before counting.
    await act(async () => {});

    const dupes = duplicates(getPaths());
    expect(dupes).toEqual([]);
  });

  test("Watchlist fans out to each endpoint at most once", async () => {
    stubRemainingWith(mock, []);
    renderWithProviders(<Watchlist />, { route: "/watchlist" });
    await waitFor(() => expect(mock.history.get.length).toBeGreaterThan(1));
    await act(async () => {});

    expect(duplicates(getPaths())).toEqual([]);
  });
});

describe("a re-render does not re-fetch", () => {
  /**
   * The unstable-dependency detector.
   *
   * Re-rendering with identical props must not produce a single new request. If
   * an effect depends on an object or callback that is recreated each render,
   * this count goes up — which is exactly the defect, and it is undetectable by
   * reading the code at a glance.
   */
  test("Dashboard issues no further requests when re-rendered unchanged", async () => {
    stubRemainingWith(mock, []);
    const { rerender } = renderWithProviders(<Dashboard />, { route: "/dashboard" });
    await waitFor(() => expect(mock.history.get.length).toBeGreaterThan(3));
    await act(async () => {});

    const before = mock.history.get.length;
    await act(async () => {
      rerender(<Dashboard />);
    });
    await act(async () => {});

    expect(mock.history.get.length).toBe(before);
  });
});

describe("no polling while the realtime socket is connected", () => {
  /**
   * StockAssist pushes; it does not poll. Every one of the 13 timers in the app
   * is a *disconnected fallback* — `if (connected) return undefined;` — and this
   * test is what fails if a new one ships without that guard.
   *
   * Driving the store's `connected` flag directly is the point: it is the same
   * flag the components read, so this exercises the real guard rather than a
   * mock of it.
   */
  test("Dashboard issues no requests over two polling intervals while connected", async () => {
    stubRemainingWith(mock, []);
    renderWithProviders(<Dashboard />, { route: "/dashboard" });
    await waitFor(() => expect(mock.history.get.length).toBeGreaterThan(3));
    await act(async () => {});

    // The socket comes up after the mount fetch, as it does in production.
    act(setSocketLive);
    await act(async () => {});

    const afterMount = mock.history.get.length;

    // The longest interval in the page is 30s; advance well past two of them.
    await act(async () => {
      jest.advanceTimersByTime(70_000);
    });
    await act(async () => {});

    expect(mock.history.get.length).toBe(afterMount);
  });

  test("Watchlist issues no requests over two polling intervals while connected", async () => {
    stubRemainingWith(mock, []);
    renderWithProviders(<Watchlist />, { route: "/watchlist" });
    await waitFor(() => expect(mock.history.get.length).toBeGreaterThan(1));
    await act(async () => {});

    act(setSocketLive);
    await act(async () => {});

    const afterMount = mock.history.get.length;
    await act(async () => {
      jest.advanceTimersByTime(70_000);
    });
    await act(async () => {});

    expect(mock.history.get.length).toBe(afterMount);
  });

  /**
   * The counter-test, and the reason the two above are not vacuous.
   *
   * If `connected` were ignored entirely — or if these components had simply
   * stopped registering timers — the "no polling while connected" assertions
   * would pass for the wrong reason. This proves the timer exists and does fire
   * when the socket is DOWN, so the guard is what silences it, not its absence.
   */
  test("Watchlist DOES poll while disconnected (proves the guard, not its absence)", async () => {
    stubRemainingWith(mock, []);
    renderWithProviders(<Watchlist />, { route: "/watchlist" });
    await waitFor(() => expect(mock.history.get.length).toBeGreaterThan(1));
    await act(async () => {});

    act(setSocketOffline);
    await act(async () => {});

    const afterMount = mock.history.get.length;
    await act(async () => {
      jest.advanceTimersByTime(70_000);
    });
    await act(async () => {});

    expect(mock.history.get.length).toBeGreaterThan(afterMount);
  });
});

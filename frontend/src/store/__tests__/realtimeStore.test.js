/**
 * Realtime store — the reducer behind every live number in the product.
 *
 * Every WebSocket frame lands here before any component sees it, so a bug in
 * this file shows up as a wrong price on a dozen screens at once. These are
 * pure state transitions, tested without React.
 *
 * Production failures these catch: a malformed frame wiping live prices; the
 * bounded event lists growing without limit during a long session (a slow
 * memory leak on a screen that stays open all trading day); and the batching
 * path dropping the last message of a burst.
 */
import { useRealtimeStore } from "../realtimeStore";

/** Wrap a payload in the R2 event envelope the socket delivers. */
const event = (name, data, timestamp = "2026-01-15T09:30:00.000Z") => ({
  type: "event",
  event: name,
  data,
  timestamp,
});

const store = () => useRealtimeStore.getState();

beforeEach(() => {
  useRealtimeStore.getState().reset();
});

describe("connection state machine", () => {
  it("starts offline", () => {
    expect(store().connection.status).toBe("offline");
  });

  it.each(["connecting", "live", "reconnecting", "offline"])("records the %s state", (status) => {
    store().setConnection(status);

    expect(store().connection.status).toBe(status);
  });

  it("records the last pong alongside the status", () => {
    store().setConnection("live", { lastPongAt: 1737000000000 });

    expect(store().connection).toMatchObject({ status: "live", lastPongAt: 1737000000000 });
  });

  it("holds the imperative sender only while connected", () => {
    const send = jest.fn();

    store().setSend(send);
    expect(store().send).toBe(send);

    store().setSend(null);
    expect(store().send).toBeNull();
  });
});

describe("price ingestion", () => {
  it("stores a broadcast price map", () => {
    store().applyMessages([{ type: "prices", data: { TESTCO: { price: 1234.5, change_pct: 1.01 } } }]);

    expect(store().priceTicks.TESTCO).toMatchObject({ price: 1234.5, change_pct: 1.01 });
  });

  it("stores a single symbol tick", () => {
    store().applyMessages([{ type: "price_tick", data: { symbol: "TESTCO", price: 1240 } }]);

    expect(store().priceTicks.TESTCO.price).toBe(1240);
  });

  it("merges a later tick over the earlier one", () => {
    store().applyMessages([{ type: "prices", data: { TESTCO: { price: 1234.5, change_pct: 1.01 } } }]);
    store().applyMessages([{ type: "prices", data: { TESTCO: { price: 1250, change_pct: 2.3 } } }]);

    expect(store().priceTicks.TESTCO).toMatchObject({ price: 1250, change_pct: 2.3 });
  });

  it("leaves other symbols untouched when one ticks", () => {
    store().applyMessages([
      { type: "prices", data: { TESTCO: { price: 1000 }, OTHERCO: { price: 500 } } },
    ]);
    const otherBefore = store().priceTicks.OTHERCO;

    store().applyMessages([{ type: "prices", data: { TESTCO: { price: 1100 } } }]);

    // Same object identity — memoized per-symbol rows must not re-render.
    expect(store().priceTicks.OTHERCO).toBe(otherBefore);
  });

  it("keeps object identity when a tick repeats unchanged", () => {
    store().applyMessages([{ type: "prices", data: { TESTCO: { price: 1000, change_pct: 1 } } }]);
    const before = store().priceTicks.TESTCO;

    store().applyMessages([{ type: "prices", data: { TESTCO: { price: 1000, change_pct: 1 } } }]);

    expect(store().priceTicks.TESTCO).toBe(before);
  });

  it("folds an index update into the price store under its canonical symbol", () => {
    store().applyMessages([event("market.index.updated", { key: "nifty", value: 22150.4, change_pct: 0.55 })]);

    expect(store().priceTicks.NIFTY).toMatchObject({ price: 22150.4, change_pct: 0.55 });
  });

  it("coalesces a burst of price messages into the final value", () => {
    store().applyMessages([
      { type: "prices", data: { TESTCO: { price: 1000 } } },
      { type: "prices", data: { TESTCO: { price: 1010 } } },
      { type: "prices", data: { TESTCO: { price: 1020 } } },
    ]);

    // The last message of a burst must win — an off-by-one here shows a stale
    // price on screen after every heartbeat.
    expect(store().priceTicks.TESTCO.price).toBe(1020);
  });
});

describe("malformed and hostile frames", () => {
  it.each([
    ["an empty batch", []],
    ["a null message", [null]],
    ["a message with no type", [{}]],
    ["an unknown event", [event("some.future.event", { anything: true })]],
    ["a price frame with no data", [{ type: "prices" }]],
    ["a price tick with no symbol", [{ type: "price_tick", data: { price: 10 } }]],
  ])("survives %s without corrupting existing state", (_label, messages) => {
    store().applyMessages([{ type: "prices", data: { TESTCO: { price: 1234.5 } } }]);

    expect(() => store().applyMessages(messages)).not.toThrow();
    expect(store().priceTicks.TESTCO.price).toBe(1234.5);
  });
});

describe("bounded collections", () => {
  // A dashboard left open all day must not accumulate unbounded history.
  it("caps trade updates", () => {
    for (let i = 0; i < 80; i++) {
      store().applyMessages([{ type: "trade_update", data: { trade_id: `t${i}`, status: "OPEN" } }]);
    }

    expect(store().tradeUpdates.length).toBeLessThanOrEqual(50);
  });

  it("caps alerts", () => {
    for (let i = 0; i < 80; i++) {
      store().applyMessages([{ type: "alert", data: { id: `a${i}`, message: `Alert ${i}` } }]);
    }

    expect(store().alerts.length).toBeLessThanOrEqual(50);
  });

  it("caps concurrent AI runs", () => {
    for (let i = 0; i < 20; i++) {
      store().applyMessages([event("ai.run.started", { run_id: `r${i}`, steps: ["one"] })]);
      store().applyMessages([event("ai.run.completed", { run_id: `r${i}`, status: "done" })]);
    }

    expect(store().aiRunOrder.length).toBeLessThanOrEqual(6);
  });
});

describe("AI runs", () => {
  it("tracks a run's steps as the backend streams them", () => {
    store().applyMessages([
      event("ai.run.started", { run_id: "run-1", steps: ["Fetching quote", "Debating"] }),
      event("ai.step", { run_id: "run-1", index: 0, status: "running" }),
    ]);

    const run = store().aiRuns["run-1"];
    expect(run.steps).toHaveLength(2);
    expect(run.steps[0].status).toBe("running");
    expect(run.steps[1].status).toBe("pending");
    expect(run.active).toBe(true);
  });

  it("ignores steps for a run it never saw start", () => {
    expect(() =>
      store().applyMessages([event("ai.step", { run_id: "unknown-run", index: 0, status: "running" })]),
    ).not.toThrow();
    expect(store().aiRuns["unknown-run"]).toBeUndefined();
  });

  it("settles every unfinished step when a run is resolved", () => {
    // Guards the "stuck spinner" case: WebSocket frames lost mid-run must not
    // leave a step animating forever once the reply has arrived.
    store().applyMessages([event("ai.run.started", { run_id: "run-1", steps: ["one", "two"] })]);
    store().applyMessages([event("ai.step", { run_id: "run-1", index: 0, status: "running" })]);

    store().resolveAIRun("run-1");

    const run = store().aiRuns["run-1"];
    expect(run.active).toBe(false);
    expect(run.steps.every((s) => s.status !== "running")).toBe(true);
  });

  it("drops a finished run so the map cannot grow forever", () => {
    store().applyMessages([event("ai.run.started", { run_id: "run-1", steps: ["one"] })]);

    store().clearAIRun("run-1");

    expect(store().aiRuns["run-1"]).toBeUndefined();
    expect(store().aiRunOrder).not.toContain("run-1");
  });

  it("tolerates resolving or clearing a run that does not exist", () => {
    expect(() => store().resolveAIRun("nope")).not.toThrow();
    expect(() => store().clearAIRun("nope")).not.toThrow();
    expect(() => store().resolveAIRun(null)).not.toThrow();
  });
});

describe("notifications", () => {
  it("records the latest push and its unread count", () => {
    store().applyMessages([
      event("notification.created", {
        notification_id: "n1",
        title: "Target hit",
        message: "TESTCO reached target 1",
        severity: "info",
      }),
    ]);

    expect(store().latestNotification).toMatchObject({ notification_id: "n1", title: "Target hit" });
    expect(store().unreadCount).toBe(1);
  });

  it("decrements the unread count when one is read", () => {
    useRealtimeStore.setState({ unreadCount: 2 });

    store().decrementUnread();

    expect(store().unreadCount).toBe(1);
  });

  it("never drives the unread count below zero", () => {
    useRealtimeStore.setState({ unreadCount: 0 });

    store().decrementUnread();

    expect(store().unreadCount).toBe(0);
  });
});

describe("watchlist sync", () => {
  it.each(["added", "removed"])("records a cross-surface %s event", (action) => {
    store().applyMessages([event("watchlist.updated", { action, symbol: "TESTCO" })]);

    expect(store().watchlistEvent).toMatchObject({ action, symbol: "TESTCO" });
  });
});

describe("reset", () => {
  it("clears every live slice back to the initial state", () => {
    store().setConnection("live");
    store().applyMessages([
      { type: "prices", data: { TESTCO: { price: 1000 } } },
      event("notification.created", { notification_id: "n1", title: "x", message: "y" }),
      event("ai.run.started", { run_id: "run-1", steps: ["one"] }),
    ]);

    store().reset();

    expect(store().priceTicks).toEqual({});
    expect(store().aiRuns).toEqual({});
    expect(store().unreadCount).toBe(0);
    expect(store().latestNotification).toBeNull();
    expect(store().connection.status).toBe("offline");
  });
});

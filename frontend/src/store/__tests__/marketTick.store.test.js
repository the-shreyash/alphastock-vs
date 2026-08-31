/**
 * `market.tick` ingestion (D5.15) — the last link of the canonical path.
 *
 * D5.15 traced a real broker tick end to end. Everything on the backend worked
 * once the feed had instruments to subscribe to: the packet decoded, became a
 * canonical `MarketTick`, the provider reached READY, served probation, the
 * Source Manager selected it, and `MarketGateway._ingest_ticks` published
 * `market.tick` with `source_tier: "streaming"`.
 *
 * And the browser threw it away. `applyEvent` routes on `event.split(".")[0]`,
 * so `market.tick` landed in the `market` branch — which tests for
 * `market.index.updated`, `market.global.updated`, `market.movers.updated`,
 * `market.engine.status` and then a top-level `data.symbol && data.price`. A
 * tick batch has none of those: the payload is `{ ticks: [...] }`. Every branch
 * missed and the batch fell out of the reducer. The batched ingest path
 * (`priceMapFromMessage`) had the same hole.
 *
 * These tests drive the store with the EXACT envelope
 * `services/realtime/event_bridge.py` builds from `MarketGateway._ingest_ticks`,
 * with a payload copied from a real captured event, so a change to either shape
 * breaks them rather than being absorbed.
 */
import { useRealtimeStore, selectPriceTicks, selectTickForSymbol } from "../realtimeStore";

const store = () => useRealtimeStore.getState();

/**
 * The bridged envelope for a canonical tick batch. `market.tick` maps to the
 * `market` channel via `DOMAIN_CHANNEL`, and carries `user_id` when the feed is
 * owned by one account — which makes the bridge deliver it with `send_to_user`
 * rather than a channel broadcast.
 */
const tickEvent = (ticks, extra = {}) => ({
  type: "event",
  event: "market.tick",
  channel: "market",
  data: {
    ticks,
    count: ticks.length,
    source_tier: "streaming",
    ingested_at: "2026-08-31T09:26:23.970321+00:00",
    ...extra,
  },
  timestamp: "2026-08-31T09:26:23.970321+00:00",
});

/** A tick exactly as `MarketTick.as_dict()` serialises one. */
const tick = (symbol, price, over = {}) => ({
  symbol,
  price,
  exchange: null,
  volume: null,
  ingested_at: "2026-08-31T09:26:23.969990+00:00",
  ...over,
});

beforeEach(() => {
  useRealtimeStore.getState().reset();
});

describe("a canonical tick batch reaches the price store", () => {
  it("unpacks a batch into the symbol-keyed price store", () => {
    store().applyEvent(tickEvent([tick("RELIANCE", 1290.4), tick("TCS", 2333.0)]));

    expect(selectTickForSymbol("RELIANCE")(store()).price).toBe(1290.4);
    expect(selectTickForSymbol("TCS")(store()).price).toBe(2333.0);
  });

  it("updates the same symbol when a later batch carries a new price", () => {
    store().applyEvent(tickEvent([tick("RELIANCE", 1290.4)]));
    store().applyEvent(tickEvent([tick("RELIANCE", 1290.0)]));

    expect(selectTickForSymbol("RELIANCE")(store()).price).toBe(1290.0);
  });

  it("also unpacks the batch on the batched ingest path", () => {
    // `applyMessages` is what RealtimeProvider actually calls — it coalesces a
    // ~40ms burst. A fix that only touched `applyEvent` would leave every tick
    // arriving through the real socket still being dropped.
    store().applyMessages([tickEvent([tick("SBIN", 812.5)])]);

    expect(selectTickForSymbol("SBIN")(store()).price).toBe(812.5);
  });

  it("coalesces a burst into ONE store write rather than one per batch", () => {
    // This is the reason `market.tick` is handled in `priceMapFromMessage` and
    // not only in `applyEvent`: without the batched-path entry the burst still
    // *arrives* (applyEvent catches each message), so every price assertion in
    // this file would pass while the Sprint R9 coalescing silently regressed —
    // one store notification per tick batch, and at ~7 batches a second from a
    // live feed that is a re-render storm on every subscribed component.
    let writes = 0;
    const unsubscribe = useRealtimeStore.subscribe(() => {
      writes += 1;
    });
    try {
      store().applyMessages([
        tickEvent([tick("RELIANCE", 1290.4)]),
        tickEvent([tick("TCS", 2333.0)]),
        tickEvent([tick("SBIN", 812.5)]),
      ]);
    } finally {
      unsubscribe();
    }

    expect(writes).toBe(1);
    expect(selectTickForSymbol("SBIN")(store()).price).toBe(812.5);
  });

  it("coalesces a burst of batches for one symbol into the latest price", () => {
    store().applyMessages([
      tickEvent([tick("RELIANCE", 1290.4)]),
      tickEvent([tick("RELIANCE", 1290.2)]),
      tickEvent([tick("RELIANCE", 1290.0)]),
    ]);

    expect(selectTickForSymbol("RELIANCE")(store()).price).toBe(1290.0);
  });

  it("canonicalises the symbol key so a tick and a quote share one entry", () => {
    store().applyEvent(tickEvent([tick("reliance", 1290.4)]));

    expect(Object.keys(selectPriceTicks(store()))).toEqual(["RELIANCE"]);
  });
});

describe("what a tick must not overwrite or invent", () => {
  it("does not fabricate a change percentage the tick contract has no field for", () => {
    store().applyEvent(tickEvent([tick("RELIANCE", 1290.4)]));

    expect(selectTickForSymbol("RELIANCE")(store()).change_pct).toBeUndefined();
  });

  it("keeps a change percentage a quote already supplied", () => {
    // The 15s quote path and the push path write the same slice. A tick must
    // refresh the price without blanking the day's change beside it — the
    // merge is field-by-field for exactly this reason.
    store()._mergePrices({ RELIANCE: { price: 1285.0, change_pct: 1.4 } });
    store().applyEvent(tickEvent([tick("RELIANCE", 1290.4)]));

    const entry = selectTickForSymbol("RELIANCE")(store());
    expect(entry.price).toBe(1290.4);
    expect(entry.change_pct).toBe(1.4);
  });

  it("does not blank a volume the feed did not carry", () => {
    // Upstox `ltpc` and Kite LTP carry no cumulative volume, so the tick's
    // `volume` is null. Writing that null over a value a quote supplied would
    // make a narrower subscription look like missing data.
    store()._mergePrices({ RELIANCE: { price: 1285.0, volume: 4200000 } });
    store().applyEvent(tickEvent([tick("RELIANCE", 1290.4)]));

    expect(selectTickForSymbol("RELIANCE")(store()).volume).toBe(4200000);
  });

  it("carries a volume when the feed does supply one", () => {
    store().applyEvent(tickEvent([tick("RELIANCE", 1290.4, { volume: 4200000 })]));

    expect(selectTickForSymbol("RELIANCE")(store()).volume).toBe(4200000);
  });

  it("ignores a tick with no price rather than writing a hole", () => {
    store().applyEvent(tickEvent([tick("RELIANCE", null), tick("TCS", 2333.0)]));

    expect(selectTickForSymbol("RELIANCE")(store())).toBeNull();
    expect(selectTickForSymbol("TCS")(store()).price).toBe(2333.0);
  });

  it("ignores an empty batch rather than blanking the store", () => {
    store().applyEvent(tickEvent([tick("RELIANCE", 1290.4)]));
    store().applyEvent(tickEvent([]));

    expect(selectTickForSymbol("RELIANCE")(store()).price).toBe(1290.4);
  });

  it("survives a malformed payload without throwing", () => {
    expect(() => store().applyEvent({ type: "event", event: "market.tick", data: {} })).not.toThrow();
    expect(() => store().applyEvent({ type: "event", event: "market.tick", data: { ticks: "no" } })).not.toThrow();
    expect(selectPriceTicks(store())).toEqual({});
  });
});

describe("the consumer cannot tell which provider produced the price", () => {
  it("stores no source tier, provider identity or broker name against a symbol", () => {
    // Developer Rule 4. `source_tier` rides the payload so the feed-state
    // indicator (D5.14) can report the tier once, for the feed — not stamped
    // onto every symbol where a component could branch on it.
    store().applyEvent(
      tickEvent([tick("RELIANCE", 1290.4)], {
        user_id: "acct-1",
        provider: "brokerfeed:upstox:acct-1",
        broker: "upstox",
      })
    );

    const rendered = JSON.stringify(selectPriceTicks(store())).toLowerCase();
    for (const forbidden of ["upstox", "brokerfeed", "streaming", "acct-1", "source_tier"]) {
      expect(rendered).not.toContain(forbidden);
    }
  });

  it("renders a delayed batch and a streaming batch identically", () => {
    store().applyEvent(tickEvent([tick("RELIANCE", 1290.4)], { source_tier: "delayed" }));
    const delayed = selectTickForSymbol("RELIANCE")(store());

    useRealtimeStore.getState().reset();
    store().applyEvent(tickEvent([tick("RELIANCE", 1290.4)], { source_tier: "streaming" }));

    expect(selectTickForSymbol("RELIANCE")(store())).toEqual(delayed);
  });
});

describe("the other market.* events still route where they did", () => {
  it("still folds an index update into the price store", () => {
    store().applyEvent({
      type: "event",
      event: "market.index.updated",
      channel: "market",
      data: { symbol: "nifty", value: 24800.5, change_pct: 0.4 },
    });

    expect(selectTickForSymbol("NIFTY")(store()).price).toBe(24800.5);
  });

  it("still routes the engine status away from the price store", () => {
    store().applyEvent({
      type: "event",
      event: "market.engine.status",
      channel: "market",
      data: { initialized: true },
    });

    expect(selectPriceTicks(store())).toEqual({});
    expect(store().engineStatus).toEqual({ initialized: true });
  });
});

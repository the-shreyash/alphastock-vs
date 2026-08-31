/**
 * The index strip on a live feed (D5.17).
 *
 * Until D5.17 nothing could put a broker tick into `priceTicks.NIFTY`, so the
 * only thing that ever reached the index strip was the 15-second `prices`
 * broadcast — which carries a price AND a day-change. The effect this module
 * replaced was written against that and wrote both fields unconditionally.
 *
 * A canonical `MarketTick` carries a price and no day-change: a day's change
 * needs a previous close the tick contract does not have. So the first real
 * index tick would have written `change_pct: undefined` over a true value from
 * the REST overview, and `StatCard`'s `changePct != null` guard would have made
 * the day's change vanish from the card at the exact moment the price started
 * moving live. That is the failure these tests exist for, and it is not
 * hypothetical — it is what D5.17 would have shipped.
 */
import {
  applyLiveIndexPrices,
  INDEX_OVERVIEW_KEYS,
  VIX_OVERVIEW_KEY,
  VIX_SYMBOL,
} from "../livePrices";

/** The `/market/overview` payload shape, as the route returns it. */
const overview = (over = {}) => ({
  nifty: { value: 24810, change: 120.5, change_pct: 0.49, available: true },
  bank_nifty: { value: 52400, change: -80.2, change_pct: -0.15, available: true },
  sensex: { value: 81020, change: 300.1, change_pct: 0.37, available: true },
  india_vix: 13.4,
  market_status: "OPEN",
  ...over,
});

/** A canonical `market.tick` as `tickBatchToPriceMap` leaves it: price only. */
const tick = (price) => ({ price, updated_at: "2026-08-31T09:45:20Z" });

describe("a canonical index tick moves the price and nothing else", () => {
  it("writes the price without touching the day-change", () => {
    const before = overview();
    const after = applyLiveIndexPrices(before, { NIFTY: tick(24815.25) });

    expect(after.nifty.value).toBe(24815.25);
    // THE REGRESSION. `undefined` here is the bug; 0 would be a fabrication.
    expect(after.nifty.change_pct).toBe(0.49);
    expect(after.nifty.change).toBe(120.5);
    expect(after.nifty.available).toBe(true);
  });

  it("leaves every index the batch did not carry exactly as it was", () => {
    const before = overview();
    const after = applyLiveIndexPrices(before, { NIFTY: tick(24815.25) });

    expect(after.bank_nifty).toBe(before.bank_nifty);
    expect(after.sensex).toBe(before.sensex);
    expect(after.market_status).toBe("OPEN");
  });

  it("takes a day-change when the feed does supply one", () => {
    // The polled `prices` broadcast does carry `change_pct`, and both message
    // kinds land in the same store — so the merge must not be one-or-the-other.
    const after = applyLiveIndexPrices(overview(), {
      NIFTY: { price: 24815.25, change_pct: 0.51 },
    });

    expect(after.nifty.value).toBe(24815.25);
    expect(after.nifty.change_pct).toBe(0.51);
  });

  it("moves all three indices in one batch", () => {
    const after = applyLiveIndexPrices(overview(), {
      NIFTY: tick(24815.25),
      BANKNIFTY: tick(52444.1),
      SENSEX: tick(81099.9),
    });

    expect(after.nifty.value).toBe(24815.25);
    expect(after.bank_nifty.value).toBe(52444.1);
    expect(after.sensex.value).toBe(81099.9);
    expect(after.bank_nifty.change_pct).toBe(-0.15);
  });
});

describe("India VIX", () => {
  it("is a bare number, matching the shape the overview publishes", () => {
    const after = applyLiveIndexPrices(overview(), { [VIX_SYMBOL]: tick(12.85) });

    expect(after[VIX_OVERVIEW_KEY]).toBe(12.85);
    expect(typeof after[VIX_OVERVIEW_KEY]).toBe("number");
  });

  it("is keyed by the canonical symbol the backend publishes", () => {
    // If these two ever disagree the card silently stops updating and nothing
    // errors — the same class of break D5.15 found between the tick batch and
    // the price store.
    expect(VIX_SYMBOL).toBe("INDIAVIX");
    expect(INDEX_OVERVIEW_KEYS).toEqual({
      NIFTY: "nifty",
      BANKNIFTY: "bank_nifty",
      SENSEX: "sensex",
    });
  });
});

describe("identity is preserved when nothing moved (Sprint R9)", () => {
  it("returns the same object for a tick batch that repeats the current price", () => {
    const before = overview();
    const after = applyLiveIndexPrices(before, {
      NIFTY: tick(24810),
      [VIX_SYMBOL]: tick(13.4),
    });

    expect(after).toBe(before);
  });

  it("returns the same object for symbols the strip does not render", () => {
    const before = overview();
    expect(applyLiveIndexPrices(before, { RELIANCE: tick(2891) })).toBe(before);
  });

  it("survives an empty store and an absent overview", () => {
    const before = overview();
    expect(applyLiveIndexPrices(before, {})).toBe(before);
    expect(applyLiveIndexPrices(before, null)).toBe(before);
    expect(applyLiveIndexPrices(null, { NIFTY: tick(1) })).toBe(null);
  });

  it("does not fabricate a price from a tick that carries none", () => {
    const before = overview();
    expect(applyLiveIndexPrices(before, { NIFTY: { volume: 100 } })).toBe(before);
    expect(applyLiveIndexPrices(before, { NIFTY: { price: null } })).toBe(before);
  });
});

describe("an index the baseline could not supply", () => {
  it("is published from the feed rather than left blank", () => {
    // A live NIFTY beside a missing baseline is strictly better than nothing,
    // and no field the tick did not carry is invented alongside it.
    const after = applyLiveIndexPrices(
      { nifty: null, bank_nifty: null, sensex: null },
      { NIFTY: tick(24815.25) },
    );

    expect(after.nifty.value).toBe(24815.25);
    expect(after.nifty.change_pct).toBeUndefined();
  });
});

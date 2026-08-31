/**
 * D5.16 §6 — folding a live tick into a rendered row.
 *
 * The dashboard had exactly four `priceTicks` readers, and Top AI Picks was not
 * one of them: the card fetched `/analysis/top-picks` once on mount and then
 * showed that number until the page was reloaded, however many real ticks
 * arrived for those symbols. The backend half of D5.16 makes the pick's price
 * canonical; this is the half that makes it *move*.
 *
 * The logic lives here rather than in a component effect because it is the same
 * fold for every row-shaped surface — picks, watchlist, any future list — and
 * because the interesting properties are about identity and absence, which are
 * awkward to assert through a render and trivial to assert on a function.
 */

import { applyLivePrices } from "../livePrices";

const ROWS = [
  { symbol: "RELIANCE", price: 1285, change_pct: 0.5 },
  { symbol: "TCS", price: 3900, change_pct: -0.2 },
];

test("a tick moves the row it names", () => {
  const next = applyLivePrices(ROWS, { RELIANCE: { price: 1290.4 } });
  expect(next[0].price).toBe(1290.4);
  expect(next[1].price).toBe(3900);
});

test("a tick with no price is ignored rather than blanking the row", () => {
  const next = applyLivePrices(ROWS, { RELIANCE: { change_pct: 1.1 } });
  expect(next[0].price).toBe(1285);
  expect(next[0].change_pct).toBe(1.1);
});

test("a field the tick does not carry survives", () => {
  /**
   * A canonical `MarketTick` carries a price and no day-change — there is no
   * previous close in a tick. Overwriting `change_pct` with 0 would render a
   * real live price beside a fabricated "unchanged", which is the one thing the
   * tick contract is careful not to claim.
   */
  const next = applyLivePrices(ROWS, { RELIANCE: { price: 1290.4 } });
  expect(next[0].change_pct).toBe(0.5);
});

test("nothing changing preserves array and row identity", () => {
  /**
   * Sprint R9 selective rendering: a burst that moves one symbol must not
   * re-render every row, and a burst that moves nothing must not render at all.
   */
  const same = applyLivePrices(ROWS, { RELIANCE: { price: 1285 } });
  expect(same).toBe(ROWS);

  const moved = applyLivePrices(ROWS, { RELIANCE: { price: 1290.4 } });
  expect(moved).not.toBe(ROWS);
  expect(moved[1]).toBe(ROWS[1]);
});

test("a symbol with no tick is left exactly as it was", () => {
  expect(applyLivePrices(ROWS, { INFY: { price: 1 } })).toBe(ROWS);
});

test("empty and missing inputs are not errors", () => {
  expect(applyLivePrices(ROWS, null)).toBe(ROWS);
  expect(applyLivePrices(ROWS, {})).toBe(ROWS);
  expect(applyLivePrices(null, { RELIANCE: { price: 1 } })).toEqual([]);
});

test("symbols are matched case-insensitively", () => {
  const next = applyLivePrices([{ symbol: "reliance", price: 1 }],
    { RELIANCE: { price: 2 } });
  expect(next[0].price).toBe(2);
});

test("only price and change_pct are taken from a tick", () => {
  /**
   * The store's tick entries carry `volume`, `exchange`, `updated_at` and a
   * `source_tier`. A row is a rendered domain object — an AI pick carries a
   * stop-loss, targets and reasoning — and a blind spread would let a price
   * source write fields it has no business writing, `source_tier` included:
   * freshness is the feed indicator's to state (D5.14), not a per-row badge.
   */
  const next = applyLivePrices(
    [{ symbol: "RELIANCE", price: 1, target1: 99 }],
    { RELIANCE: { price: 2, volume: 5, source_tier: "streaming", target1: 1 } },
  );
  expect(next[0]).toEqual({ symbol: "RELIANCE", price: 2, target1: 99 });
});

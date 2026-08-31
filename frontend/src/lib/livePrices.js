/**
 * Folding the live price store into a list of rendered rows (D5.16).
 *
 * WHY THIS IS A MODULE AND NOT AN EFFECT
 * ---------------------------------------
 * Before D5.16 exactly four places in the app read `realtimeStore.priceTicks`,
 * each with its own inline `useEffect`, and Top AI Picks was not one of them —
 * so the most prominent recommendation on the dashboard showed the price it was
 * generated with until the page was reloaded, no matter how many real ticks
 * arrived for those symbols. Adding a fifth copy of the same effect would have
 * made a fifth place to get the identity rules subtly wrong.
 *
 * The rules are the interesting part and they are not obvious:
 *
 * * **Absence is not zero.** A canonical `MarketTick` carries a price and no
 *   day-change — a day's change needs a previous close the tick contract does
 *   not have. Writing `change_pct: 0` would render a real live price beside a
 *   fabricated "unchanged", which is the fabrication the tick contract is most
 *   careful to avoid.
 * * **Identity is load-bearing.** Sprint R9's selective rendering depends on an
 *   unchanged row being the *same object* and an unchanged list being the same
 *   array, so a 7-batch-a-second feed does not re-render a page seven times a
 *   second for prices that did not move.
 * * **A price source may write prices.** Not `source_tier`, not `volume`, not
 *   anything else the store happens to carry. Freshness is stated once, by the
 *   feed-state indicator (D5.14), and a per-row tier badge would be a second
 *   answer to a question the platform answers in one place.
 *
 * This module knows nothing about brokers, providers or tiers — it takes the
 * store's symbol-keyed map, which is the same shape whether a price came from a
 * broker socket or a polled quote. That indistinguishability is the contract
 * (MARKET_DATA_ARCHITECTURE.md, Developer Rule 4).
 */

/** The only fields a live price may write onto a rendered row. */
const LIVE_FIELDS = ["price", "change_pct"];

/**
 * Apply the live price map to a list of rows.
 *
 * @param {Array<object>} rows  Row objects carrying a `symbol`.
 * @param {object} ticks        `priceTicks` from the realtime store.
 * @returns {Array<object>} The same array when nothing moved; otherwise a new
 *   array in which only the rows that moved are new objects.
 */
export function applyLivePrices(rows, ticks) {
  if (!Array.isArray(rows)) return [];
  if (!ticks) return rows;

  let changed = false;
  const next = rows.map((row) => {
    const symbol = row?.symbol ? String(row.symbol).toUpperCase() : null;
    const tick = symbol ? ticks[symbol] : null;
    if (!tick) return row;

    let patch = null;
    for (const field of LIVE_FIELDS) {
      const value = tick[field];
      // `undefined` and `null` both mean "this feed did not say", and neither
      // is a reason to overwrite what the row already shows.
      if (value == null || value === row[field]) continue;
      patch = patch || {};
      patch[field] = value;
    }
    if (!patch) return row;
    changed = true;
    return { ...row, ...patch };
  });

  return changed ? next : rows;
}

/**
 * The dashboard's index strip: which overview key each canonical symbol feeds.
 *
 * The overview is not a list of rows and does not use `price` — it is a fixed
 * object whose index blocks are keyed by position (`nifty`, `bank_nifty`,
 * `sensex`) and carry `value`. India VIX is not even a block: it is a bare
 * number at `india_vix`, because the provider publishes no day-change for it.
 * That is why this is a second function rather than a call to the one above.
 */
export const INDEX_OVERVIEW_KEYS = {
  NIFTY: "nifty",
  BANKNIFTY: "bank_nifty",
  SENSEX: "sensex",
};

export const VIX_SYMBOL = "INDIAVIX";
export const VIX_OVERVIEW_KEY = "india_vix";

/**
 * Apply the live price map to the market overview (D5.17).
 *
 * WHY THIS REPLACED AN INLINE EFFECT
 * -----------------------------------
 * The effect it replaces wrote `change_pct: tick.change_pct` unconditionally.
 * That was harmless while the only thing reaching `priceTicks.NIFTY` was the
 * 15-second `prices` broadcast, which carries a day-change — and became a bug
 * the moment D5.17 put indices on broker feeds, because a canonical
 * `MarketTick` carries a price and no day-change. The first real index tick
 * would have written `change_pct: undefined` over a true value from the REST
 * overview, and the card's `changePct != null` guard would have made the day's
 * change silently disappear at the exact moment the price started moving.
 *
 * Absence is not zero and absence is not an overwrite — the same rule
 * `applyLivePrices` states for rows, applied to the one surface that predates
 * it.
 *
 * @param {object|null} overview  The `/market/overview` payload in state.
 * @param {object|null} ticks     `priceTicks` from the realtime store.
 * @returns {object|null} The same object when nothing moved.
 */
export function applyLiveIndexPrices(overview, ticks) {
  if (!overview || !ticks) return overview;

  let next = null;
  const write = (key, patch) => {
    next = next || { ...overview };
    next[key] = patch;
  };

  for (const [symbol, key] of Object.entries(INDEX_OVERVIEW_KEYS)) {
    const tick = ticks[symbol];
    if (!tick) continue;
    const block = (next || overview)[key] || {};
    const patch = {};
    if (tick.price != null && tick.price !== block.value) patch.value = tick.price;
    if (tick.change_pct != null && tick.change_pct !== block.change_pct) {
      patch.change_pct = tick.change_pct;
    }
    if (Object.keys(patch).length) write(key, { ...block, ...patch });
  }

  const vix = ticks[VIX_SYMBOL];
  if (vix?.price != null && vix.price !== (next || overview)[VIX_OVERVIEW_KEY]) {
    // A bare number, matching the shape the overview already publishes. The
    // card reads `overview.india_vix` and cannot tell a broker tick from the
    // polled baseline, which is the contract (Developer Rule 4).
    write(VIX_OVERVIEW_KEY, vix.price);
  }

  return next || overview;
}

export default applyLivePrices;

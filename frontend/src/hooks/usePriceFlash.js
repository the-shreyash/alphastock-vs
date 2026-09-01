/**
 * usePriceFlash — the visual contract for a live price update.
 *
 * Sprint R3/G7 shipped this as a GSAP tween: on every change of the tracked
 * value it wrote an inline `backgroundColor` tint (green up / red down) and a
 * `scale` of 1.05 or 0.95, then settled back over 600ms.
 *
 * D5.18 — WHY THE ANIMATION IS GONE
 * ---------------------------------
 * That behaviour was written for a feed that moved a few times a minute. With
 * a real broker socket attached it is a different thing entirely. Measured in
 * the browser on 2026-09-01 during NSE hours, with the Upstox feed stable and
 * the dashboard open: **24,968 class/style mutations in 100 seconds**, roughly
 * 250 per second, essentially all of them this tween writing and clearing
 * inline styles. What the user sees is the number sitting inside a coloured
 * box that never stops flickering, and the index cards visibly jumping as the
 * glyphs scale.
 *
 * The brief's requirement is precise, and it is not "stop updating": keep the
 * price live, remove the distracting flashing — no background flash, no
 * repeated green/red animation, no layout jump, no CSS animation triggered on
 * every price event. A colour may communicate direction only if it is stable,
 * and a tint that appears and disappears 250 times a second is the opposite of
 * stable.
 *
 * So a tick now does exactly one thing: the number changes. Direction is still
 * communicated, but by the fields that are *already* stable — the signed change
 * and percentage rendered beside the price, which hold their colour between
 * ticks instead of strobing on each one.
 *
 * This also removes the animation-as-evidence trap the brief names: a moving
 * highlight was being read as proof the data was live, which it never was —
 * before D5.18 the same tween fired just as happily on a 15-second delayed
 * baseline poll.
 *
 * WHY THE HOOK STAYS
 * ------------------
 * It is a no-op by design, not by accident, and keeping it is deliberate:
 *
 *   * Every price surface in the product — Dashboard, Portfolio, Watchlist,
 *     TradeMonitor — already routes through it, so this is the one place the
 *     contract can be enforced and, in `__tests__/usePriceFlash.test.jsx`,
 *     tested. Deleting it would scatter the decision across four pages and
 *     leave nothing to assert against.
 *   * The returned ref keeps all existing call sites working unchanged, which
 *     is what makes this a contained change rather than a four-page refactor.
 *
 * A future direction cue belongs here, and it must satisfy the same tests:
 * stable between ticks, no background, no transform.
 */
import { useRef } from "react";

export function usePriceFlash(_value) {
  // The value is intentionally unused: a price change must not start an
  // animation. The parameter is kept so call sites read as documentation of
  // which value the element renders, and so a future stable direction cue has
  // the value it would need.
  return useRef(null);
}

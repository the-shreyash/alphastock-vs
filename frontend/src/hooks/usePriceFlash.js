/**
 * usePriceFlash (Sprint R3, G7).
 *
 * Returns a ref to attach to a price element; when the tracked value rises it
 * pulses a green tint + scale-up, when it falls a red tint + scale-down, then
 * settles — the doc's "Price Up → Green Flash → Scale 1.05 → Return" spec. Only
 * the affected element animates (no page-level re-render), matching the
 * event-driven "animate only what changed" mandate.
 *
 * Uses explicit rgba flash colors (gsap can't tween CSS `var()` colors).
 */
import { useRef, useEffect } from "react";
import gsap from "gsap";

const GAIN_FLASH = "rgba(16,185,129,0.18)"; // matches --gain
const LOSS_FLASH = "rgba(244,63,94,0.18)"; // matches --loss
const TRANSPARENT = "rgba(0,0,0,0)";

export function usePriceFlash(value) {
  const ref = useRef(null);
  const prev = useRef(value);

  useEffect(() => {
    const el = ref.current;
    const before = prev.current;
    prev.current = value;

    if (!el || value == null || before == null || value === before) return;

    const rising = value > before;
    gsap.fromTo(
      el,
      { backgroundColor: rising ? GAIN_FLASH : LOSS_FLASH, scale: rising ? 1.05 : 0.95 },
      {
        backgroundColor: TRANSPARENT,
        scale: 1,
        duration: 0.6,
        ease: "power2.out",
        clearProps: "backgroundColor,scale,transform",
      }
    );
  }, [value]);

  return ref;
}

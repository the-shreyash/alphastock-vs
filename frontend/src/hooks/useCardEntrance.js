/**
 * useCardEntrance (Sprint R4).
 *
 * Returns a ref to attach to a newly inserted card; on mount the card plays
 * the doc's scanner-card entrance — Slide Right → Fade → Glow → Settle
 * (REALTIME_SYSTEM.md, Animations). Runs once per element, so only freshly
 * inserted cards animate (list re-renders never replay it), matching the
 * "animate only what changed" mandate.
 *
 * Uses explicit rgba glow colors (gsap can't tween CSS `var()` colors).
 */
import { useRef, useEffect } from "react";
import gsap from "gsap";

const GLOW = "0 0 0 1px rgba(99,102,241,0.35), 0 0 18px rgba(99,102,241,0.30)"; // matches --accent
const NO_GLOW = "0 0 0 0 rgba(0,0,0,0), 0 0 0 rgba(0,0,0,0)";

export function useCardEntrance() {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    gsap.fromTo(
      el,
      { x: -24, opacity: 0, boxShadow: GLOW },
      {
        x: 0,
        opacity: 1,
        boxShadow: NO_GLOW,
        duration: 0.55,
        ease: "power2.out",
        clearProps: "x,opacity,boxShadow,transform",
      }
    );
  }, []);

  return ref;
}

/**
 * AnimatedNumber (Sprint R3, G7).
 *
 * Smoothly counts from the previous value to the new one on change (the doc's
 * "Portfolio → Smooth Counter" spec). Renders via a ref so the tween updates
 * text content without re-rendering React on every frame.
 */
import { useRef, useEffect } from "react";
import gsap from "gsap";

export default function AnimatedNumber({
  value,
  format = (v) => Math.round(v).toLocaleString(),
  duration = 0.6,
  className,
  style,
  "data-testid": testId,
}) {
  const ref = useRef(null);
  const tweenObj = useRef({ v: Number(value) || 0 });

  useEffect(() => {
    const target = Number(value);
    if (!ref.current || Number.isNaN(target)) return undefined;
    const tween = gsap.to(tweenObj.current, {
      v: target,
      duration,
      ease: "power2.out",
      onUpdate: () => {
        if (ref.current) ref.current.textContent = format(tweenObj.current.v);
      },
    });
    return () => tween.kill();
  }, [value, duration, format]);

  const initial = Number(value);
  return (
    <span ref={ref} className={className} style={style} data-testid={testId}>
      {Number.isNaN(initial) ? "—" : format(initial)}
    </span>
  );
}

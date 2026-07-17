/**
 * useVirtualList (Sprint R9) — dependency-free list windowing.
 *
 * For long uniform-row lists (watchlist, result tables) the DOM should hold
 * only the rows near the viewport, not every item — the doc's "Virtualize
 * long lists" performance rule. This hook computes the visible index window
 * from the scroll position and returns spacer paddings that preserve the
 * scrollbar geometry, so consumers render `items.slice(start, end)` inside a
 * fixed-height scroll container.
 *
 * Row height is corrected from the first rendered row (attach `measureRowRef`
 * to any row) so the estimate only has to be roughly right.
 *
 * Usage:
 *   const v = useVirtualList({ count: items.length, estimatedRowHeight: 61,
 *                              enabled: items.length > 60 });
 *   <div ref={v.containerRef} onScroll={v.onScroll}
 *        style={{ maxHeight: 560, overflowY: "auto" }}>
 *     <div style={{ paddingTop: v.padTop, paddingBottom: v.padBottom }}>
 *       {items.slice(v.start, v.end).map((item, i) => (
 *         <Row key={item.id} ref={i === 0 ? v.measureRowRef : undefined} … />
 *       ))}
 *     </div>
 *   </div>
 *
 * With `enabled: false` it degrades to a no-op window covering every item, so
 * small lists keep their existing markup (and entrance animations) untouched.
 */
import { useCallback, useRef, useState } from "react";

const DEFAULT_VIEWPORT_ROWS = 12; // used before the container reports a height

export function useVirtualList({
  count,
  estimatedRowHeight = 60,
  overscan = 6,
  enabled = true,
}) {
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(0);
  const [rowHeight, setRowHeight] = useState(estimatedRowHeight);
  const nodeRef = useRef(null);

  const containerRef = useCallback((node) => {
    nodeRef.current = node;
    if (node) setViewportHeight(node.clientHeight);
  }, []);

  // Correct the row-height estimate from a real rendered row (first row of
  // the current window). Only updates on a meaningful (>1px) difference so
  // sub-pixel rendering never causes a re-render loop.
  const measureRowRef = useCallback((node) => {
    if (!node) return;
    const h = node.offsetHeight;
    if (h > 0) {
      setRowHeight((prev) => (Math.abs(prev - h) > 1 ? h : prev));
    }
  }, []);

  const onScroll = useCallback((e) => {
    setScrollTop(e.currentTarget.scrollTop);
    // Keep the viewport height fresh (covers container resizes for free).
    const h = e.currentTarget.clientHeight;
    setViewportHeight((prev) => (prev === h ? prev : h));
  }, []);

  if (!enabled) {
    return {
      containerRef,
      measureRowRef,
      onScroll: undefined,
      start: 0,
      end: count,
      padTop: 0,
      padBottom: 0,
      virtual: false,
    };
  }

  const visibleRows = viewportHeight
    ? Math.ceil(viewportHeight / rowHeight)
    : DEFAULT_VIEWPORT_ROWS;
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const end = Math.min(count, start + visibleRows + overscan * 2);

  return {
    containerRef,
    measureRowRef,
    onScroll,
    start,
    end,
    padTop: start * rowHeight,
    padBottom: Math.max(0, (count - end) * rowHeight),
    virtual: true,
  };
}

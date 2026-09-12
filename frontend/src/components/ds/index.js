/**
 * StockAssist design system — the shared component layer.
 *
 * Everything a page needs to look like StockAssist comes from here. Pages
 * should not hand-build a card, a page header, a tab row, a status pill or a
 * signed change value; if something is missing, add it to this folder rather
 * than inventing it locally.
 *
 * This layer sits ABOVE `components/ui/` (the unstyled shadcn/Radix
 * primitives). `ui/` owns behaviour and accessibility for generic widgets;
 * `ds/` owns what StockAssist looks like. A component belongs here when it
 * encodes a product decision — how a metric is presented, what an AI insight
 * must contain, which green means "profit" and which means "confirm".
 *
 * See ./README.md for the full contract.
 */
export { default as Card, Card as SurfaceCard, CardHeader } from "./Card";
export { default as PageHeader } from "./PageHeader";
export { default as MetricCard } from "./MetricCard";
export { default as InsightCard } from "./InsightCard";
export { default as StatusBadge } from "./StatusBadge";
export { default as DeltaValue, directionOf, colorForDirection } from "./DeltaValue";
export { default as SegmentedTabs, TabPanel } from "./SegmentedTabs";
export { default as EmptyState } from "./EmptyState";

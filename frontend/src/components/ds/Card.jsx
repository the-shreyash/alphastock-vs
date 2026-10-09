import { forwardRef } from "react";
import { cn } from "../../lib/utils";

/**
 * The card surface — the single container primitive for the whole product.
 *
 * WHY THIS WRAPS AN EXISTING CLASS RATHER THAN INVENTING A NEW ONE
 * ----------------------------------------------------------------
 * `.glass-card` is already applied at ~100 sites. If this component grew its
 * own surface we would have *two* card looks, which is precisely the problem
 * the design system exists to remove. So `Card` renders the established class
 * and the class itself is where the visual refinements land — every existing
 * `.glass-card` in the codebase improves at the same moment this component
 * does, and a page half-migrated to `<Card>` still looks like one page.
 *
 * (The `glass-` name is historical. The surface is a near-opaque white/near-
 * black panel with a hairline border and a very soft shadow; it is not heavy
 * glassmorphism.)
 */

const PADDING = {
  none: "",
  sm: "p-4",
  md: "p-5",
  lg: "p-6",
};

export const Card = forwardRef(function Card(
  { as: Tag = "div", padding = "md", interactive = false, className, children, ...rest },
  ref
) {
  return (
    <Tag
      ref={ref}
      className={cn(
        "glass-card",
        // Lift + pointer only when the whole card is a click target. A card
        // that merely *contains* a button must not animate on hover, or the
        // page reads as though everything is clickable.
        interactive && "cursor-pointer sa-card-interactive",
        PADDING[padding] ?? PADDING.md,
        className
      )}
      {...rest}
    >
      {children}
    </Tag>
  );
});

/**
 * The header row inside a card: an optional icon, a title, an optional
 * subtitle, and an optional action slot pinned right.
 *
 * This replaces the `<h3 className="eyebrow mb-4">Section</h3>` idiom that was
 * hand-written in most cards, which is why section headings drifted between
 * `eyebrow`, `card-title` and raw `text-sm font-semibold` from page to page.
 *
 * `title` renders as a real heading element so the page keeps a usable
 * document outline for screen readers; pass `headingLevel` when a card sits
 * under a section that already owns an h2.
 */
export function CardHeader({
  title,
  subtitle,
  icon: Icon,
  action,
  headingLevel = 3,
  className,
  ...rest
}) {
  const Heading = `h${headingLevel}`;
  return (
    <div className={cn("flex items-start justify-between gap-3 mb-4", className)} {...rest}>
      <div className="flex items-start gap-2.5 min-w-0">
        {Icon && (
          <Icon
            size={16}
            className="shrink-0 mt-0.5"
            style={{ color: "var(--brand-accent)" }}
            aria-hidden="true"
          />
        )}
        <div className="min-w-0">
          <Heading className="card-title !text-[15px] font-semibold truncate">{title}</Heading>
          {subtitle && (
            <p className="card-subtitle mt-0.5 truncate" title={typeof subtitle === "string" ? subtitle : undefined}>
              {subtitle}
            </p>
          )}
        </div>
      </div>
      {action && <div className="shrink-0 flex items-center gap-1.5">{action}</div>}
    </div>
  );
}

export default Card;

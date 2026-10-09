import { cn } from "../../lib/utils";

/**
 * The page header every authenticated screen opens with: a serif display
 * title, a supporting line, and an actions/status slot on the right.
 *
 * WHY A COMPONENT FOR SIX LINES OF MARKUP
 * ---------------------------------------
 * It was already hand-written on 27 pages, and the copies had drifted:
 * `mt-1` vs `mt-0.5` under the title, `items-center` vs `items-start` on the
 * row, some wrapping on mobile and some overflowing. The header is the first
 * thing on every page, so its inconsistencies are the ones a user notices
 * while navigating — the exact "collection of independently designed pages"
 * feeling this system is meant to remove.
 *
 * The serif `.page-title` is inherited straight from the landing page, which
 * is what visually ties the authenticated app back to it.
 */
export default function PageHeader({
  title,
  subtitle,
  /** Right-hand slot: status badges, filters, primary action. */
  actions,
  /** Optional element rendered above the title (breadcrumb, back link). */
  eyebrow,
  className,
  ...rest
}) {
  return (
    <div
      className={cn(
        // Stacks on mobile so a long title plus three status pills never
        // overflow the viewport; sits on one row from `sm` up.
        "flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between",
        className
      )}
      {...rest}
    >
      <div className="min-w-0">
        {eyebrow && <div className="mb-1.5">{eyebrow}</div>}
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="page-subtitle mt-1">{subtitle}</p>}
      </div>
      {actions && (
        <div className="flex items-center gap-2 flex-wrap sm:justify-end shrink-0">{actions}</div>
      )}
    </div>
  );
}

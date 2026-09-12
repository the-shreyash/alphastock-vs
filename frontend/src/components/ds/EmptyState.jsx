import { cn } from "../../lib/utils";

/**
 * The empty / no-results / not-connected state.
 *
 * The platform standard is that an empty state always explains itself and
 * offers a way forward — an icon, a title, a sentence of context and at least
 * one action. A bare "No data" string (the previous house style in several
 * widgets) leaves the user unable to tell a quiet market from a broken feed
 * from a broker they never connected.
 *
 * This is deliberately *calm*: muted icon, normal text colour, no warning
 * tint. An empty watchlist is not an error and must not make the page look
 * broken.
 */
export default function EmptyState({
  icon: Icon,
  title,
  description,
  /** Primary call to action — a node, so it can be a button or a Link. */
  action,
  /** Optional lower-emphasis second action. */
  secondaryAction,
  /** `sm` for inside a card, `md` for a whole page region. */
  size = "md",
  className,
  ...rest
}) {
  const compact = size === "sm";
  return (
    <div
      className={cn("flex flex-col items-center text-center", compact ? "py-8 px-4" : "py-14 px-6", className)}
      {...rest}
    >
      {Icon && (
        <div
          className="flex items-center justify-center rounded-2xl mb-4"
          style={{
            width: compact ? 40 : 52,
            height: compact ? 40 : 52,
            background: "var(--bg-elevated)",
            color: "var(--text-muted)",
          }}
        >
          <Icon size={compact ? 18 : 24} aria-hidden="true" />
        </div>
      )}
      <p
        className={cn("font-semibold", compact ? "text-[14px]" : "text-[16px]")}
        style={{ color: "var(--text-primary)" }}
      >
        {title}
      </p>
      {description && (
        <p
          className={cn("mt-1.5 max-w-sm", compact ? "text-[12px]" : "text-[13px]")}
          style={{ color: "var(--text-muted)", lineHeight: 1.6 }}
        >
          {description}
        </p>
      )}
      {(action || secondaryAction) && (
        <div className="flex items-center gap-2 mt-5 flex-wrap justify-center">
          {action}
          {secondaryAction}
        </div>
      )}
    </div>
  );
}

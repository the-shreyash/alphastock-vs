/**
 * AIText — lightweight, dependency-free renderer for AI-generated text.
 *
 * The AI returns lightly-formatted markdown-ish output (headings, **bold**,
 * bullet lists, numbered lists). Rather than pull in a full markdown library,
 * this renders the small subset the AI actually emits so responses read cleanly
 * while staying safe (no raw HTML injection — everything is plain React nodes).
 */

// Render inline **bold** segments within a line of text.
function renderInline(text, keyPrefix) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={`${keyPrefix}-b-${i}`} style={{ color: "var(--text-primary)", fontWeight: 600 }}>
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <span key={`${keyPrefix}-t-${i}`}>{part}</span>;
  });
}

export default function AIText({ text, className = "" }) {
  if (!text) return null;
  const lines = String(text).split("\n");

  return (
    <div className={`ai-text space-y-2 ${className}`} style={{ color: "var(--text-secondary)", lineHeight: 1.65 }}>
      {lines.map((raw, i) => {
        const line = raw.trimEnd();
        if (!line.trim()) return <div key={i} className="h-1" />;

        // Headings: markdown (#) or ALL-CAPS-ish section labels ending in ':'
        const headingMatch = line.match(/^#{1,3}\s+(.*)$/);
        if (headingMatch) {
          return (
            <h4 key={i} className="text-[13px] font-semibold mt-3" style={{ color: "var(--text-primary)" }}>
              {renderInline(headingMatch[1], i)}
            </h4>
          );
        }

        // Numbered list item
        const numMatch = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
        if (numMatch) {
          return (
            <div key={i} className="flex gap-2 text-[13px]">
              <span className="font-semibold shrink-0" style={{ color: "var(--ai-accent)" }}>{numMatch[1]}.</span>
              <span>{renderInline(numMatch[2], i)}</span>
            </div>
          );
        }

        // Bullet list item
        const bulletMatch = line.match(/^\s*[-•*]\s+(.*)$/);
        if (bulletMatch) {
          return (
            <div key={i} className="flex gap-2 text-[13px]">
              <span className="shrink-0 mt-[7px] w-1 h-1 rounded-full" style={{ background: "var(--ai-accent)" }} />
              <span>{renderInline(bulletMatch[1], i)}</span>
            </div>
          );
        }

        return (
          <p key={i} className="text-[13px]">
            {renderInline(line, i)}
          </p>
        );
      })}
    </div>
  );
}

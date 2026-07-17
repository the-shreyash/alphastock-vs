import { CloudOff } from "lucide-react";

/**
 * SectionUnavailable — the honest empty state for a Morning Report section.
 *
 * Every section of the briefing can fail independently (a dead RSS feed, an
 * unlicensed feed, an unreachable provider). When one does, the platform states
 * the gap and its reason rather than substituting a plausible-looking value —
 * a trader who knows a number is missing can go find it; a trader shown an
 * invented one cannot.
 *
 * `note` is the backend's own explanation, rendered verbatim so the UI never
 * has to guess why data is absent.
 */
export default function SectionUnavailable({ note, icon: Icon = CloudOff }) {
  return (
    <div
      className="flex items-start gap-3 rounded-xl p-4"
      style={{ background: "var(--bg-surface)", border: "1px dashed var(--border)" }}
    >
      <Icon size={16} className="mt-0.5 shrink-0" style={{ color: "var(--text-muted)" }} />
      <p className="text-[13px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
        {note || "This section is temporarily unavailable."}
      </p>
    </div>
  );
}

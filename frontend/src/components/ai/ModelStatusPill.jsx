import { useEffect, useState } from "react";
import { Sparkles, CircleDot } from "lucide-react";
import api from "../../services/api";

/**
 * ModelStatusPill — shows which AI models are live (Claude / Gemini) and whether
 * the full dual-AI debate is available. Reflects the Model Router status from
 * GET /api/ai/status, giving the workspace an honest, transparent header
 * (per AI_AGENT_SYSTEM.md → AI Transparency).
 */
export default function ModelStatusPill() {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api.get("/ai/status")
      .then((r) => !cancelled && setStatus(r.data))
      .catch(() => !cancelled && setStatus({ online: false, providers: {} }));
    return () => { cancelled = true; };
  }, []);

  if (!status) {
    return <div className="h-7 w-40 rounded-full skeleton" />;
  }

  const claude = status.providers?.claude?.configured;
  const gemini = status.providers?.gemini?.configured;
  const online = status.online;

  return (
    <div
      className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-[11px] font-medium"
      style={{ background: "var(--ai-accent-soft)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
      title={online ? "AI models are live" : "AI offline — configure ANTHROPIC_API_KEY / GOOGLE_GEMINI_KEY"}
      data-testid="model-status-pill"
    >
      <Sparkles size={12} style={{ color: "var(--ai-accent)" }} />
      {online ? (
        <>
          {status.full_debate ? "Dual-AI ready" : "AI ready"}
          <span className="flex items-center gap-1.5 ml-1">
            <ProviderDot label="Claude" on={claude} />
            <ProviderDot label="Gemini" on={gemini} />
          </span>
        </>
      ) : (
        <span style={{ color: "var(--text-muted)" }}>AI offline</span>
      )}
    </div>
  );
}

function ProviderDot({ label, on }) {
  return (
    <span className="inline-flex items-center gap-1" style={{ color: on ? "var(--gain)" : "var(--text-muted)" }}>
      <CircleDot size={9} />
      {label}
    </span>
  );
}

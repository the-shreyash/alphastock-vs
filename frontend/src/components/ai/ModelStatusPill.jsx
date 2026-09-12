import { useEffect, useState } from "react";
import { Sparkles, CircleDot } from "lucide-react";
import api from "../../services/api";

/**
 * ModelStatusPill — shows which AI models are live (Claude / Gemini) and whether
 * the full dual-AI debate is available. Reflects the Model Router status from
 * GET /api/ai/status, giving the workspace an honest, transparent header
 * (per AI_AGENT_SYSTEM.md → AI Transparency).
 *
 * D6.9 — WHAT "READY" NOW MEANS, AND WHAT IT USED TO MEAN.
 * `status.online` was `bool(ANTHROPIC_API_KEY || GOOGLE_GEMINI_KEY)`, so this
 * pill said **AI ready** for a revoked key, an unbilled key and a typo'd key
 * alike. The backend now requires that no configured provider is in an
 * observed-failure state, and ships per-provider `health` so the pill can say
 * *which* one is failing rather than just going dark.
 *
 * A configured provider that has never been called is NOT shown as broken:
 * a cold process knows nothing, and rendering an outage on no evidence is the
 * same error running the other way.
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

  const health = status.health || {};
  const online = status.online;
  // A provider is only shown as up when it is configured AND not in an
  // observed-failure state. `health` is absent on an older backend, in which
  // case this degrades to the previous key-presence reading rather than
  // reporting every provider as broken.
  const providerUp = (name) => {
    const h = health[name];
    const configured = status.providers?.[name]?.configured;
    return h ? Boolean(h.configured && !h.degraded) : Boolean(configured);
  };
  // Named so the tooltip can say which provider failed, using the classified
  // error label — never the provider's raw error string, which the API does
  // not carry for exactly this reason.
  const degraded = ["claude", "gemini"].filter((n) => health[n]?.configured && health[n]?.degraded);

  const title = online
    ? "AI models are live"
    : degraded.length
      ? `AI unavailable — last call failed (${degraded.map((n) => `${n}: ${health[n].last_error_class || "error"}`).join(", ")})`
      : "AI offline — configure ANTHROPIC_API_KEY / GOOGLE_GEMINI_KEY";

  return (
    <div
      className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-[11px] font-medium"
      style={{ background: "var(--ai-accent-soft)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
      title={title}
      data-testid="model-status-pill"
    >
      <Sparkles size={12} style={{ color: "var(--ai-accent)" }} />
      {online ? (
        <>
          {status.full_debate ? "Dual-AI ready" : "AI ready"}
          <span className="flex items-center gap-1.5 ml-1">
            <ProviderDot label="Claude" on={providerUp("claude")} />
            <ProviderDot label="Gemini" on={providerUp("gemini")} />
          </span>
        </>
      ) : (
        // "Unavailable", not "offline", when a configured provider is failing:
        // the key is present and the service is not answering, which is a
        // different operator problem from nothing being configured.
        <span style={{ color: "var(--text-muted)" }} data-testid="model-status-offline">
          {degraded.length ? "AI unavailable" : "AI offline"}
        </span>
      )}
    </div>
  );
}

function ProviderDot({ label, on }) {
  return (
    <span className="inline-flex items-center gap-1" style={{ color: on ? "var(--gain)" : "var(--text-muted)" }}
      data-testid={`provider-dot-${label.toLowerCase()}`} data-on={String(Boolean(on))}>
      <CircleDot size={9} />
      {label}
    </span>
  );
}

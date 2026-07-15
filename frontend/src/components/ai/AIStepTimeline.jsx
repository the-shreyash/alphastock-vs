import { motion, AnimatePresence } from "framer-motion";
import { Check, Loader2, AlertTriangle } from "lucide-react";
import { useRealtimeStore, selectAIRun } from "../../store/realtimeStore";

/**
 * AIStepTimeline — the live "AI Thinking Process" (Sprint R7).
 *
 * Replaces the static "Thinking…" dots inside the assistant chat bubble with the
 * real stages the backend is running, streamed over the `ai.run.*` / `ai.step`
 * events (see services/ai_activity.py). It reads the correlated run from the
 * real-time store and, when the run matches the request in flight (`runId`),
 * renders each stage with a live status.
 *
 * When no matching live run is available yet — the first paint, or if the
 * WebSocket is down — it falls back to the original three-dot pulse so the
 * "assistant is working" affordance is never lost.
 */
const DOT_PULSE = (
  <div className="flex gap-1.5" data-testid="ai-thinking-dots">
    {[0, 1, 2].map((i) => (
      <div
        key={i}
        className="w-2 h-2 rounded-full animate-pulse"
        style={{ background: "var(--ai-accent)", animationDelay: `${i * 0.15}s` }}
      />
    ))}
  </div>
);

function StepIcon({ status }) {
  if (status === "done") return <Check size={13} style={{ color: "var(--gain)" }} />;
  if (status === "warning") return <AlertTriangle size={13} style={{ color: "var(--loss)" }} />;
  if (status === "running") {
    return <Loader2 size={13} className="animate-spin" style={{ color: "var(--ai-accent)" }} />;
  }
  // pending
  return (
    <span
      className="block w-[7px] h-[7px] rounded-full"
      style={{ border: "1.5px solid var(--text-muted)", opacity: 0.5 }}
    />
  );
}

export default function AIStepTimeline({ runId }) {
  const aiRun = useRealtimeStore(selectAIRun);

  // Only show live steps when they belong to this request. If the id doesn't
  // match (stale run, event not arrived, or socket offline), fall back to dots.
  const live = aiRun && runId && aiRun.runId === runId && aiRun.steps.length > 0;

  if (!live) return DOT_PULSE;

  return (
    <div className="space-y-2 min-w-[190px]" data-testid="ai-step-timeline">
      <AnimatePresence initial={false}>
        {aiRun.steps.map((step, i) => {
          const active = step.status === "running";
          const muted = step.status === "pending";
          return (
            <motion.div
              key={step.label + i}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: muted ? 0.55 : 1, x: 0 }}
              transition={{ duration: 0.28, delay: i * 0.04 }}
              className="flex items-center gap-2.5"
            >
              <div className="w-4 flex items-center justify-center shrink-0">
                <StepIcon status={step.status} />
              </div>
              <span
                className="text-[12px] leading-snug"
                style={{
                  color: active ? "var(--ai-accent)" : "var(--text-secondary)",
                  fontWeight: active ? 600 : 400,
                }}
              >
                {step.label}
              </span>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

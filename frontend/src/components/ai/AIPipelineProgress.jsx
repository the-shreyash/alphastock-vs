import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Sparkles, CheckCircle2 } from "lucide-react";
import { useRealtimeStore, selectAIRunById } from "../../store/realtimeStore";
import { StepIcon } from "./AIStepTimeline";
import AnimatedNumber from "../ui/AnimatedNumber";
import { useCardEntrance } from "../../hooks/useCardEntrance";

/**
 * AIPipelineProgress — the page-level "AI Thinking Process" (Sprint R7).
 *
 * The big sibling of AIStepTimeline: where the chat bubble shows a compact
 * step list, page surfaces (Morning Report) show the full pipeline card —
 * progress bar, "X of N" counter and every stage the backend is actually
 * running, streamed over ai.run.* / ai.step events.
 *
 * Truthful by construction: it renders `fallback` until this request's
 * ai.run.started arrives (first paint, socket offline, or a cache hit that
 * never starts a run), and degrades back to `fallback` if an active run goes
 * silent for `staleMs` (lost socket mid-run) instead of freezing.
 */
export default function AIPipelineProgress({
  runId,
  title = "AI is working",
  fallback = null,
  staleMs = 45000,
}) {
  const run = useRealtimeStore(selectAIRunById(runId));
  const cardRef = useCardEntrance();
  const [, forceTick] = useState(0);

  // Re-evaluate staleness while the run is active so a silent socket drop
  // degrades to the fallback instead of an eternally spinning step.
  useEffect(() => {
    if (!run?.active) return undefined;
    const id = setInterval(() => forceTick((t) => t + 1), 5000);
    return () => clearInterval(id);
  }, [run?.active]);

  const stale =
    run?.active &&
    staleMs > 0 &&
    Date.now() - new Date(run.updatedAt).getTime() > staleMs;

  if (!run || run.steps.length === 0 || stale) return fallback;

  const total = run.steps.length;
  const settled = run.steps.filter(
    (s) => s.status === "done" || s.status === "warning"
  ).length;
  const pct = Math.round((settled / total) * 100);
  const completed = !run.active;

  return (
    <div ref={cardRef} className="glass-card p-6 max-w-xl" data-testid="ai-pipeline-progress">
      <div className="flex items-center justify-between mb-4">
        <h3 className="eyebrow flex items-center gap-2" style={{ color: "var(--ai-accent)" }}>
          {completed ? <CheckCircle2 size={13} /> : <Sparkles size={13} className="animate-pulse" />}
          {completed ? "Completed" : title}
        </h3>
        <span className="text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>
          <AnimatedNumber value={settled} duration={0.4} /> of {total}
        </span>
      </div>

      {/* Progress bar */}
      <div className="h-1.5 rounded-full overflow-hidden mb-5" style={{ background: "var(--hover)" }}>
        <div
          className="h-full rounded-full transition-all duration-500 ease-out"
          style={{ width: `${completed ? 100 : pct}%`, background: "var(--ai-accent)" }}
        />
      </div>

      {/* Stage list */}
      <div className="space-y-3">
        <AnimatePresence initial={false}>
          {run.steps.map((step, i) => {
            const active = step.status === "running";
            const muted = step.status === "pending";
            return (
              <motion.div
                key={step.label + i}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: muted ? 0.5 : 1, x: 0 }}
                transition={{ duration: 0.3, delay: i * 0.05 }}
                className="flex items-center gap-3"
              >
                <div className="w-5 flex items-center justify-center shrink-0">
                  <StepIcon status={step.status} />
                </div>
                <span
                  className="text-[13px] leading-snug"
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
    </div>
  );
}

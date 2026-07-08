import { useState } from "react";
import { GraduationCap, Send, Cpu } from "lucide-react";
import api from "../../services/api";
import AIText from "./AIText";

/**
 * LearningPanel — the Learning Mentor. Teaches any trading/markets concept in
 * beginner-friendly language via POST /api/ai/learn. Fulfils the Learning
 * pillar of the AI Workspace (education is a first-class feature).
 */
const SUGGESTIONS = ["RSI", "MACD", "Support & Resistance", "Position sizing", "P/E ratio", "Stop loss discipline"];
const LEVELS = ["beginner", "intermediate", "advanced"];

export default function LearningPanel() {
  const [topic, setTopic] = useState("");
  const [level, setLevel] = useState("beginner");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const teach = async (t) => {
    const concept = (t || topic).trim();
    if (!concept || loading) return;
    setTopic(concept);
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const { data } = await api.post("/ai/learn", { topic: concept, level });
      setResult(data);
    } catch {
      setError("Could not generate the lesson. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4" data-testid="learning-panel">
      <div className="lg:col-span-2 space-y-4">
        <div className="glass-card p-5">
          <h3 className="card-title mb-1 flex items-center gap-2">
            <GraduationCap size={18} style={{ color: "var(--ai-accent)" }} /> Learn a concept
          </h3>
          <p className="body-text mb-4">Ask the AI mentor to explain any market or trading concept, tuned to your level.</p>

          <div className="flex gap-2 mb-3">
            <input
              className="search-input flex-1" style={{ paddingLeft: 14 }}
              placeholder="e.g. What is RSI and how do I use it?"
              value={topic} onChange={(e) => setTopic(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && teach()}
              data-testid="learn-input"
            />
            <select className="search-input capitalize" style={{ paddingLeft: 12, maxWidth: 150 }} value={level} onChange={(e) => setLevel(e.target.value)}>
              {LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
            <button className="btn-primary px-4" onClick={() => teach()} disabled={loading || !topic.trim()} data-testid="learn-btn">
              <Send size={15} />
            </button>
          </div>

          <div className="flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button key={s} onClick={() => teach(s)} className="text-[11px] font-medium px-3 py-1.5 rounded-full transition-all"
                style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)", border: "1px solid var(--border)" }}>
                {s}
              </button>
            ))}
          </div>
        </div>

        {(loading || result || error) && (
          <div className="glass-card p-5">
            {loading ? (
              <div className="space-y-2">{[1, 2, 3, 4, 5].map((i) => <div key={i} className="h-4 rounded skeleton" />)}</div>
            ) : error ? (
              <p className="text-[13px]" style={{ color: "var(--loss)" }}>{error}</p>
            ) : (
              <>
                <div className="flex items-center gap-2 mb-3">
                  <h4 className="card-title capitalize">{result.topic}</h4>
                  {result.model_used && (
                    <span className="text-[10px] flex items-center gap-1 px-2 py-0.5 rounded-md" style={{ background: "var(--hover)", color: "var(--text-muted)" }}>
                      <Cpu size={10} /> {result.model_used}
                    </span>
                  )}
                </div>
                <AIText text={result.content} />
              </>
            )}
          </div>
        )}
      </div>

      <div className="glass-card p-5 h-fit">
        <h3 className="eyebrow mb-2">Why learn here?</h3>
        <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          Every explanation is grounded, structured and honest — the mentor defines terms, shows a worked example,
          flags common mistakes and gives one practical tip. It never guarantees outcomes.
        </p>
      </div>
    </div>
  );
}

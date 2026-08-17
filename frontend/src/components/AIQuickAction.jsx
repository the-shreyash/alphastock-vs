import { useState, useRef, useEffect, useMemo, useCallback } from "react";
import { useLocation } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Sparkles, X, Send, Bot } from "lucide-react";
import api from "../services/api";

/**
 * AIQuickAction — a floating, context-aware AI companion.
 *
 * Rendered once inside the authenticated Layout so it appears on every
 * protected page. A single click opens a compact quick-action popover with
 * preset questions and a free-text box — no long typing required.
 *
 * Context awareness: the current route (and stock symbol on /stock/:symbol)
 * is parsed from the pathname and prefixed to the message sent to the AI, so
 * replies stay relevant to what the user is looking at without them typing it.
 *
 * Reuses the shared axios client and the existing `POST /api/chat`
 * endpoint ({ message, session_id } -> { response }). No new backend.
 */

// Friendly labels for the app's routes. Keep in sync with App.js routes.
const PAGE_LABELS = {
  dashboard: "Dashboard",
  markets: "Markets",
  watchlist: "Watchlist",
  picks: "AI Stock Picks",
  trades: "Trade Monitor",
  portfolio: "Portfolio",
  assistant: "AI Workspace",
  sip: "SIP Advisor",
  news: "News",
  journal: "Trade Journal",
  settings: "Settings",
  "paper-trading": "Paper Trading",
  backtesting: "Backtesting",
  "morning-report": "Morning Report",
};

const PRESETS = [
  "How is my trade?",
  "What changed today?",
  "Should I hold?",
  "Should I exit?",
];

/**
 * Derive human + machine readable context from the current pathname.
 * Detects the stock detail route (/stock/{SYMBOL}) so the AI knows the
 * exact instrument in focus.
 */
function deriveContext(pathname) {
  const stockMatch = pathname.match(/^\/stock\/([^/]+)/i);
  if (stockMatch) {
    const symbol = decodeURIComponent(stockMatch[1]).toUpperCase();
    return {
      symbol,
      label: symbol,
      // Sentence prefixed to the message so replies are already on-topic.
      prefix: `The user is currently viewing the ${symbol} stock detail page.`,
    };
  }
  const segment = pathname.replace(/^\/+/, "").split("/")[0] || "dashboard";
  const label = PAGE_LABELS[segment] || "app";
  return {
    symbol: null,
    label,
    prefix: `The user is currently on the ${label} page.`,
  };
}

export default function AIQuickAction() {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]); // in-memory session history
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  // No `error` state: failures are surfaced per-message via the `isError` flag
  // on the assistant reply below, which is what the message list actually
  // renders. A parallel boolean was written here and never read.
  const [sessionId] = useState(() => `quick-${Date.now()}`);

  const scrollRef = useRef(null);
  const inputRef = useRef(null);

  const context = useMemo(() => deriveContext(location.pathname), [location.pathname]);

  // Keep the answer area pinned to the latest message.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  // Focus the input when the panel opens for immediate typing.
  useEffect(() => {
    if (open) {
      const t = setTimeout(() => inputRef.current?.focus(), 120);
      return () => clearTimeout(t);
    }
  }, [open]);

  // Close on Escape for keyboard accessibility.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const send = useCallback(async (raw) => {
    const text = (raw ?? input).trim();
    if (!text || loading) return;

    setInput("");
    // Display the clean question; the context prefix is only sent to the AI.
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setLoading(true);

    // Snapshot context at send time (route may change while awaiting).
    const contextualMessage = `[Context: ${context.prefix}]\n\nUser question: ${text}`;

    try {
      const { data } = await api.post("/chat", {
        message: contextualMessage,
        session_id: sessionId,
      });
      const reply = data?.response || "I couldn't generate a response. Please try again.";
      setMessages((prev) => [...prev, { role: "assistant", content: reply }]);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Sorry, I ran into an issue reaching the AI. Please try again.", isError: true },
      ]);
    } finally {
      setLoading(false);
    }
  }, [input, loading, context.prefix, sessionId]);

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const panelWidth = "min(360px, calc(100vw - 32px))";

  return (
    <div className="fixed bottom-6 right-6 z-[45] flex flex-col items-end gap-3 print:hidden">
      <AnimatePresence>
        {open && (
          <motion.div
            key="ai-quick-action-panel"
            data-testid="ai-quick-action-panel"
            role="dialog"
            aria-label="AI quick assistant"
            initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 12, scale: 0.97 }}
            transition={{ type: "spring", damping: 26, stiffness: 320 }}
            className="glass-card overflow-hidden flex flex-col"
            style={{
              width: panelWidth,
              maxHeight: "min(520px, calc(100vh - 120px))",
              borderRadius: 20,
              transformOrigin: "bottom right",
              boxShadow: "0 20px 60px -12px rgba(0,0,0,0.35), 0 0 0 1px var(--border)",
            }}
          >
            {/* Header */}
            <div className="flex items-center gap-3 px-4 py-3.5" style={{ borderBottom: "1px solid var(--border)" }}>
              <div className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0" style={{ background: "var(--ai-accent-soft)" }}>
                <Sparkles size={16} style={{ color: "var(--ai-accent)" }} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-semibold leading-tight" style={{ color: "var(--text-primary)" }}>Ask AI</div>
                <div className="text-[11px] leading-tight truncate flex items-center gap-1" style={{ color: "var(--text-muted)" }}>
                  <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: "var(--ai-accent)" }} />
                  {context.symbol ? `Viewing ${context.symbol}` : `On ${context.label}`}
                </div>
              </div>
              <button
                onClick={() => setOpen(false)}
                aria-label="Close AI assistant"
                className="p-1.5 rounded-lg transition-colors shrink-0"
                style={{ color: "var(--text-muted)" }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "var(--hover)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
              >
                <X size={16} />
              </button>
            </div>

            {/* Preset quick-question chips */}
            <div className="px-4 pt-3 pb-1 flex flex-wrap gap-1.5">
              {PRESETS.map((q) => (
                <button
                  key={q}
                  onClick={() => send(q)}
                  disabled={loading}
                  className="text-[11px] font-medium px-2.5 py-1.5 rounded-full transition-all disabled:opacity-50"
                  style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)", border: "1px solid var(--border)" }}
                  onMouseEnter={(e) => { if (!loading) e.currentTarget.style.background = "var(--ai-accent-glow)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "var(--ai-accent-soft)"; }}
                >
                  {q}
                </button>
              ))}
            </div>

            {/* Answer area */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3" style={{ minHeight: 96 }}>
              {messages.length === 0 && !loading && (
                <div className="flex flex-col items-center justify-center text-center py-6 gap-2">
                  <div className="w-10 h-10 rounded-2xl flex items-center justify-center" style={{ background: "var(--ai-accent-soft)" }}>
                    <Bot size={18} style={{ color: "var(--ai-accent)" }} />
                  </div>
                  <p className="text-[12px] leading-relaxed max-w-[240px]" style={{ color: "var(--text-muted)" }}>
                    Pick a question above or ask anything. I already know you're
                    {context.symbol ? ` looking at ${context.symbol}.` : ` on the ${context.label}.`}
                  </p>
                </div>
              )}

              {messages.map((msg, i) => (
                <div key={i} className={`flex gap-2 ${msg.role === "user" ? "justify-end" : ""}`}>
                  {msg.role === "assistant" && (
                    <div className="w-6 h-6 rounded-lg flex items-center justify-center shrink-0 mt-0.5" style={{ background: "var(--ai-accent-soft)" }}>
                      <Bot size={13} style={{ color: "var(--ai-accent)" }} />
                    </div>
                  )}
                  <div
                    className={`max-w-[80%] px-3 py-2 text-[12.5px] leading-relaxed rounded-2xl ${msg.role === "user" ? "rounded-br-md" : "rounded-bl-md"}`}
                    style={{
                      background: msg.role === "user" ? "var(--ai-accent)" : "var(--bg-surface)",
                      color: msg.role === "user" ? "#FFFFFF" : msg.isError ? "var(--loss)" : "var(--text-secondary)",
                      border: msg.role === "user" ? "none" : "1px solid var(--border)",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {msg.content}
                  </div>
                </div>
              ))}

              {loading && (
                <div className="flex gap-2">
                  <div className="w-6 h-6 rounded-lg flex items-center justify-center shrink-0" style={{ background: "var(--ai-accent-soft)" }}>
                    <Bot size={13} style={{ color: "var(--ai-accent)" }} />
                  </div>
                  <div className="px-3 py-2.5 rounded-2xl rounded-bl-md" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                    <div className="flex gap-1">
                      {[0, 1, 2].map((d) => (
                        <motion.span
                          key={d}
                          className="w-1.5 h-1.5 rounded-full"
                          style={{ background: "var(--ai-accent)" }}
                          animate={{ opacity: [0.3, 1, 0.3], y: [0, -2, 0] }}
                          transition={{ duration: 0.9, repeat: Infinity, delay: d * 0.15 }}
                        />
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Input */}
            <div className="p-3 flex items-center gap-2" style={{ borderTop: "1px solid var(--border)" }}>
              <input
                ref={inputRef}
                data-testid="ai-quick-action-input"
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Ask anything..."
                disabled={loading}
                className="flex-1 text-[12.5px] px-3 py-2 rounded-xl outline-none transition-colors"
                style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                onFocus={(e) => { e.currentTarget.style.borderColor = "var(--ai-accent)"; }}
                onBlur={(e) => { e.currentTarget.style.borderColor = "var(--border)"; }}
              />
              <button
                onClick={() => send()}
                disabled={loading || !input.trim()}
                aria-label="Send message"
                className="btn-primary shrink-0 flex items-center justify-center"
                style={{ width: 38, height: 38, padding: 0, borderRadius: 12 }}
              >
                <Send size={15} />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Floating Action Button */}
      <motion.button
        data-testid="ai-quick-action-fab"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Close AI assistant" : "Open AI assistant"}
        aria-expanded={open}
        whileHover={{ scale: 1.06 }}
        whileTap={{ scale: 0.94 }}
        className="relative flex items-center justify-center rounded-full shrink-0"
        style={{
          width: 56,
          height: 56,
          background: "linear-gradient(135deg, var(--ai-accent), color-mix(in srgb, var(--ai-accent) 70%, #000 12%))",
          boxShadow: "0 8px 28px -6px var(--ai-accent-glow), 0 4px 12px rgba(0,0,0,0.25)",
        }}
      >
        {/* Idle pulsing glow ring — hidden while the panel is open to stay calm. */}
        {!open && (
          <motion.span
            aria-hidden
            className="absolute inset-0 rounded-full"
            style={{ border: "2px solid var(--ai-accent)" }}
            animate={{ scale: [1, 1.4], opacity: [0.45, 0] }}
            transition={{ duration: 2.2, repeat: Infinity, ease: "easeOut" }}
          />
        )}
        <AnimatePresence mode="wait" initial={false}>
          {open ? (
            <motion.span
              key="close"
              initial={{ rotate: -90, opacity: 0 }}
              animate={{ rotate: 0, opacity: 1 }}
              exit={{ rotate: 90, opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="flex items-center justify-center"
            >
              <X size={22} color="#FFFFFF" />
            </motion.span>
          ) : (
            <motion.span
              key="open"
              initial={{ rotate: 90, opacity: 0 }}
              animate={{ rotate: 0, opacity: 1 }}
              exit={{ rotate: -90, opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="flex items-center justify-center"
            >
              <Sparkles size={22} color="#FFFFFF" />
            </motion.span>
          )}
        </AnimatePresence>
      </motion.button>
    </div>
  );
}

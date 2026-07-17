import { useRef, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Send, Bot, User, Sparkles } from "lucide-react";
import Backtesting from "./Backtesting";
import useAIWorkspace from "../hooks/useAIWorkspace";
import AIText from "../components/ai/AIText";
import ModelStatusPill from "../components/ai/ModelStatusPill";
import ConversationSidebar from "../components/ai/ConversationSidebar";
import MemoryPanel from "../components/ai/MemoryPanel";
import ActivityTimeline from "../components/ai/ActivityTimeline";
import AIStepTimeline from "../components/ai/AIStepTimeline";
import LearningPanel from "../components/ai/LearningPanel";
import TradeReviewPanel from "../components/ai/TradeReviewPanel";
import PortfolioReviewPanel from "../components/ai/PortfolioReviewPanel";

const TABS = ["AI Chat", "Trade Review", "Portfolio AI", "Learning", "Strategy Builder"];

const QUICK_ACTIONS = [
  "What's a good swing trade setup this week?",
  "Explain RSI in simple terms",
  "How should I size my positions?",
  "What's the market outlook today?",
];

export default function AIAssistant() {
  const [tab, setTab] = useState("AI Chat");
  const {
    conversations, loadingConvos, activeId, messages, sending, activeRunId,
    send, newChat, selectConversation, deleteConversation,
  } = useAIWorkspace();
  const [input, setInput] = useState("");
  const bottomRef = useRef(null);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, sending]);

  const handleSend = (text) => {
    const msg = (text ?? input).trim();
    if (!msg) return;
    setInput("");
    send(msg);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  return (
    <div data-testid="ai-workspace-page" className="space-y-6">
      {/* Header */}
      <motion.div className="flex items-start justify-between gap-4 flex-wrap"
        initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
        <div>
          <h1 className="page-title">AI Workspace</h1>
          <p className="page-subtitle mt-1">Chat, review, learn — one assistant, powered by Claude &amp; Gemini</p>
        </div>
        <ModelStatusPill />
      </motion.div>

      {/* Tab Bar */}
      <div className="tab-bar">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`tab-btn ${tab === t ? "active" : ""}`}>{t}</button>
        ))}
      </div>

      {tab === "AI Chat" && (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          {/* Conversation history */}
          <div className="lg:col-span-1">
            <ConversationSidebar
              conversations={conversations}
              activeId={activeId}
              loading={loadingConvos}
              onSelect={selectConversation}
              onNew={newChat}
              onDelete={deleteConversation}
            />
          </div>

          {/* Chat panel */}
          <div className="lg:col-span-2 glass-card flex flex-col" style={{ height: "calc(100vh - 280px)", minHeight: 400 }}>
            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              {messages.map((msg, i) => (
                <div key={i} className={`flex gap-3 ${msg.role === "user" ? "justify-end" : ""}`}>
                  {msg.role === "assistant" && (
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: "var(--ai-accent-soft)" }}>
                      <Bot size={16} style={{ color: "var(--ai-accent)" }} />
                    </div>
                  )}
                  <div className={`max-w-[80%] p-3.5 rounded-2xl ${msg.role === "user" ? "rounded-br-md" : "rounded-bl-md"}`}
                    style={{
                      background: msg.role === "user" ? "var(--ai-accent)" : "var(--bg-surface)",
                      border: msg.role === "user" ? "none" : "1px solid var(--border)",
                    }}>
                    {msg.role === "user"
                      ? <div className="text-[13px] leading-relaxed" style={{ color: "#FFFFFF", whiteSpace: "pre-wrap" }}>{msg.content}</div>
                      : <AIText text={msg.content} />}
                  </div>
                  {msg.role === "user" && (
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: "var(--hover)" }}>
                      <User size={16} style={{ color: "var(--text-secondary)" }} />
                    </div>
                  )}
                </div>
              ))}
              {sending && (
                <div className="flex gap-3">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: "var(--ai-accent-soft)" }}>
                    <Bot size={16} style={{ color: "var(--ai-accent)" }} />
                  </div>
                  <div className="p-3.5 rounded-2xl rounded-bl-md" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                    <AIStepTimeline runId={activeRunId} />
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>

            {messages.length <= 1 && (
              <div className="px-5 pb-2 flex flex-wrap gap-2">
                {QUICK_ACTIONS.map((q, i) => (
                  <button key={i} onClick={() => handleSend(q)} className="text-[11px] font-medium px-3 py-1.5 rounded-full transition-all"
                    style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)", border: "1px solid var(--border)" }}>
                    {q}
                  </button>
                ))}
              </div>
            )}

            <div className="p-4 flex gap-3" style={{ borderTop: "1px solid var(--border)" }}>
              <input
                data-testid="chat-input"
                type="text"
                placeholder="Ask anything about markets..."
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                className="search-input flex-1"
                style={{ paddingLeft: 16 }}
                disabled={sending}
              />
              <button data-testid="chat-send-btn" onClick={() => handleSend()} disabled={sending || !input.trim()} className="btn-primary px-4 py-2.5">
                <Send size={15} />
              </button>
            </div>
          </div>

          {/* Right rail: memory + activity */}
          <div className="lg:col-span-1 space-y-4">
            <MemoryPanel />
            <ActivityTimeline />
          </div>
        </div>
      )}

      {tab === "Trade Review" && (
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
          <TradeReviewPanel />
        </motion.div>
      )}

      {tab === "Portfolio AI" && (
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
          <PortfolioReviewPanel />
        </motion.div>
      )}

      {tab === "Learning" && (
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
          <LearningPanel />
        </motion.div>
      )}

      {tab === "Strategy Builder" && (
        <motion.div className="space-y-5" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
          <div className="glass-card p-5 flex items-start gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0" style={{ background: "var(--ai-accent-soft)" }}>
              <Sparkles size={18} style={{ color: "var(--ai-accent)" }} />
            </div>
            <div>
              <h3 className="card-title mb-1">Strategy Builder</h3>
              <p className="body-text">Configure a strategy, backtest it against historical NSE data, and get an AI verdict on whether it is viable for live trading — all in one place.</p>
            </div>
          </div>
          <Backtesting />
        </motion.div>
      )}
    </div>
  );
}

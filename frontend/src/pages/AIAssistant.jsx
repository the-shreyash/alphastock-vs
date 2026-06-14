import { useState, useRef, useEffect } from "react";
import api from "../services/api";
import { Send, Bot, User, Trash2 } from "lucide-react";

export default function AIAssistant() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId] = useState(() => `chat-${Date.now()}`);
  const bottomRef = useRef(null);

  useEffect(() => {
    fetchHistory();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const fetchHistory = async () => {
    try {
      const { data } = await api.get("/chat/history");
      if (data.length > 0) {
        setMessages(data.map((m) => ({ role: m.role, content: m.content })));
      } else {
        setMessages([{
          role: "assistant",
          content: "Welcome to AlphaPartner AI. I'm your personal trading assistant for Indian markets (NSE/BSE). Ask me about:\n\n- Stock analysis & technical indicators\n- Intraday trading strategies\n- Risk management & position sizing\n- Market outlook & sector analysis\n- SIP & mutual fund guidance\n\nHow can I help you today?"
        }]);
      }
    } catch {
      setMessages([{
        role: "assistant",
        content: "Welcome to AlphaPartner AI. I'm your personal Indian stock market assistant. How can I help you?"
      }]);
    }
  };

  const handleSend = async () => {
    if (!input.trim() || loading) return;
    const msg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setLoading(true);
    try {
      const { data } = await api.post("/chat", { message: msg, session_id: sessionId });
      setMessages((prev) => [...prev, { role: "assistant", content: data.response }]);
    } catch (err) {
      setMessages((prev) => [...prev, { role: "assistant", content: "Sorry, I encountered an error. Please try again." }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div data-testid="ai-assistant-page" className="flex flex-col h-[calc(100vh-120px)]">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h1 className="text-2xl font-medium text-primary tracking-tight">AI Assistant</h1>
          <p className="text-xs text-muted">Dual AI-powered trading guidance (Claude + Gemini)</p>
        </div>
      </div>

      {/* Chat Messages */}
      <div className="flex-1 overflow-y-auto card-premium  p-4 space-y-4">
        {messages.map((msg, i) => (
          <div key={i} data-testid={`chat-message-${i}`} className={`flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            {msg.role === "assistant" && (
              <div className="w-7 h-7 rounded-xl bg-ai-soft border border-ai/30 flex items-center justify-center shrink-0">
                <Bot size={14} className="text-ai" />
              </div>
            )}
            <div className={`max-w-[80%] p-3 rounded-xl text-sm leading-relaxed whitespace-pre-wrap ${msg.role === "user"
                ? "bg-white/5 border border-main text-secondary"
                : "bg-surface border border-main text-secondary"
              }`}>
              {msg.content}
            </div>
            {msg.role === "user" && (
              <div className="w-7 h-7 rounded-xl bg-zinc-800 flex items-center justify-center shrink-0">
                <User size={14} className="text-secondary" />
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="flex gap-3">
            <div className="w-7 h-7 rounded-xl bg-ai-soft border border-ai/30 flex items-center justify-center">
              <Bot size={14} className="text-ai" />
            </div>
            <div className="bg-surface  p-3 text-sm text-muted">
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 bg-zinc-600 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                <span className="w-1.5 h-1.5 bg-zinc-600 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                <span className="w-1.5 h-1.5 bg-zinc-600 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="mt-2 flex gap-2">
        <textarea
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about stocks, trading strategies, risk management..."
          rows={1}
          className="flex-1 card-premium  px-3 py-2.5 text-sm text-primary placeholder-zinc-600 focus:outline-none focus:border-zinc-600 resize-none"
        />
        <button
          data-testid="chat-send-btn"
          onClick={handleSend}
          disabled={!input.trim() || loading}
          className="px-4  rounded-xl hover:opacity-90 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <Send size={16} />
        </button>
      </div>

      {/* Quick Actions */}
      <div className="mt-2 flex flex-wrap gap-1">
        {[
          "What's the market outlook today?",
          "Analyze RELIANCE for intraday",
          "Best risk management for beginners",
          "Explain RSI indicator",
        ].map((q) => (
          <button
            key={q}
            onClick={() => { setInput(q); }}
            className="px-2 py-1 bg-zinc-800/50  text-[10px] text-muted hover:text-secondary hover:bg-zinc-800 transition-colors"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

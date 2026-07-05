import { useState, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import api from "../services/api";
import { Send, Bot, User, Trash2, Brain, LineChart, Briefcase, Lightbulb, ChevronRight, Sparkles, TrendingUp, Target, BarChart3 } from "lucide-react";

const TABS = ["AI Chat", "Trade Analysis", "Portfolio Review", "Strategy Builder"];

const QUICK_ACTIONS = [
  "What's the best stock for swing trading this week?",
  "Analyze my portfolio",
  "Explain RSI indicator",
  "Build a strategy",
  "Market outlook today",
];

const TRADE_IDEAS = [
  { symbol: "RELIANCE", type: "BUY", entry: "₹2,950", sl: "₹2,880", target: "₹3,100", confidence: 89 },
  { symbol: "TCS", type: "BUY", entry: "₹3,450", sl: "₹3,380", target: "₹3,600", confidence: 82 },
  { symbol: "HDFCBANK", type: "HOLD", entry: "₹1,620", sl: "₹1,580", target: "₹1,700", confidence: 76 },
];

export default function AIAssistant() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId] = useState(() => `chat-${Date.now()}`);
  const [tab, setTab] = useState("AI Chat");
  const bottomRef = useRef(null);

  useEffect(() => { fetchHistory(); }, []);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const fetchHistory = async () => {
    try {
      const { data } = await api.get("/chat/history");
      if (data.length > 0) {
        setMessages(data.map(m => ({ role: m.role, content: m.content })));
      } else {
        setMessages([{
          role: "assistant",
          content: "Hi! I'm your StockAssist AI assistant. I can help you with stock analysis, trading strategies, portfolio review, and market insights. What would you like to know?"
        }]);
      }
    } catch {
      setMessages([{ role: "assistant", content: "Welcome to StockAssist AI. How can I help you today?" }]);
    }
  };

  const handleSend = async (text) => {
    const msg = (text || input).trim();
    if (!msg || loading) return;
    setInput("");
    setMessages(prev => [...prev, { role: "user", content: msg }]);
    setLoading(true);
    try {
      const { data } = await api.post("/chat", { message: msg, session_id: sessionId });
      setMessages(prev => [...prev, { role: "assistant", content: data.response }]);
    } catch {
      setMessages(prev => [...prev, { role: "assistant", content: "Sorry, I encountered an error. Please try again." }]);
    } finally { setLoading(false); }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const clearChat = () => {
    setMessages([{ role: "assistant", content: "Chat cleared. How can I help you?" }]);
  };

  return (
    <div data-testid="ai-workspace-page" className="space-y-5">
      {/* Header */}
      <div>
        <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight font-display" style={{ color: "var(--text-primary)" }}>AI Workspace</h1>
        <p className="text-[13px] mt-0.5" style={{ color: "var(--text-secondary)" }}>Your AI-powered trading assistant</p>
      </div>

      {/* Tab Bar */}
      <div className="tab-bar">
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)} className={`tab-btn ${tab === t ? "active" : ""}`}>{t}</button>
        ))}
      </div>

      {tab === "AI Chat" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Chat Panel */}
          <div className="lg:col-span-2 glass-card flex flex-col" style={{ height: "calc(100vh - 280px)", minHeight: 400 }}>
            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              {messages.map((msg, i) => (
                <div key={i} className={`flex gap-3 ${msg.role === "user" ? "justify-end" : ""}`}>
                  {msg.role === "assistant" && (
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: "var(--ai-accent-soft)" }}>
                      <Bot size={16} style={{ color: "var(--ai-accent)" }} />
                    </div>
                  )}
                  <div className={`max-w-[75%] p-3.5 rounded-2xl text-[13px] leading-relaxed ${msg.role === "user" ? "rounded-br-md" : "rounded-bl-md"}`}
                    style={{
                      background: msg.role === "user" ? "var(--ai-accent)" : "var(--bg-surface)",
                      color: msg.role === "user" ? "#FFFFFF" : "var(--text-secondary)",
                      border: msg.role === "user" ? "none" : "1px solid var(--border)",
                    }}>
                    <div style={{ whiteSpace: "pre-wrap" }}>{msg.content}</div>
                  </div>
                  {msg.role === "user" && (
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: "var(--hover)" }}>
                      <User size={16} style={{ color: "var(--text-secondary)" }} />
                    </div>
                  )}
                </div>
              ))}
              {loading && (
                <div className="flex gap-3">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: "var(--ai-accent-soft)" }}>
                    <Bot size={16} style={{ color: "var(--ai-accent)" }} />
                  </div>
                  <div className="p-3.5 rounded-2xl rounded-bl-md" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                    <div className="flex gap-1.5">
                      {[0, 1, 2].map(i => (
                        <div key={i} className="w-2 h-2 rounded-full animate-pulse" style={{ background: "var(--ai-accent)", animationDelay: `${i * 0.15}s` }} />
                      ))}
                    </div>
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>

            {/* Quick actions */}
            {messages.length <= 2 && (
              <div className="px-5 pb-2 flex flex-wrap gap-2">
                {QUICK_ACTIONS.map((q, i) => (
                  <button key={i} onClick={() => handleSend(q)} className="text-[11px] font-medium px-3 py-1.5 rounded-full transition-all"
                    style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)", border: "1px solid var(--border)" }}
                    onMouseEnter={e => e.currentTarget.style.background = "var(--ai-accent-glow)"}
                    onMouseLeave={e => e.currentTarget.style.background = "var(--ai-accent-soft)"}>
                    {q}
                  </button>
                ))}
              </div>
            )}

            {/* Input */}
            <div className="p-4 flex gap-3" style={{ borderTop: "1px solid var(--border)" }}>
              <div className="flex-1 relative">
                <input
                  data-testid="chat-input"
                  type="text"
                  placeholder="Ask anything about markets..."
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  className="search-input w-full pr-10"
                  style={{ paddingLeft: "16px" }}
                  disabled={loading}
                />
              </div>
              <button data-testid="chat-send-btn" onClick={() => handleSend()} disabled={loading || !input.trim()} className="btn-primary px-4 py-2.5">
                <Send size={15} />
              </button>
              <button onClick={clearChat} className="p-2.5 rounded-xl transition-all" style={{ color: "var(--text-muted)" }}
                onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                <Trash2 size={15} />
              </button>
            </div>
          </div>

          {/* Right Panel: Trade Ideas */}
          <div className="space-y-4">
            <div className="glass-card p-5">
              <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] mb-3 flex items-center gap-2" style={{ color: "var(--text-muted)" }}>
                <Sparkles size={13} style={{ color: "var(--ai-accent)" }} /> AI Trade Ideas
              </h3>
              <div className="space-y-2">
                {TRADE_IDEAS.map(t => (
                  <Link key={t.symbol} to={`/stock/${t.symbol}`} className="block p-3 rounded-xl transition-all" style={{ background: "var(--hover)", border: "1px solid var(--border)" }}
                    onMouseEnter={e => e.currentTarget.style.borderColor = "var(--ai-accent-glow)"}
                    onMouseLeave={e => e.currentTarget.style.borderColor = "var(--border)"}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{t.symbol}</span>
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-md" style={{
                        background: t.type === "BUY" ? "var(--gain-bg)" : "var(--ai-accent-soft)",
                        color: t.type === "BUY" ? "var(--gain)" : "var(--ai-accent)",
                      }}>{t.type}</span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-[10px]">
                      <div><span style={{ color: "var(--text-muted)" }}>Entry</span><div className="font-mono font-semibold" style={{ color: "var(--text-secondary)" }}>{t.entry}</div></div>
                      <div><span style={{ color: "var(--text-muted)" }}>SL</span><div className="font-mono font-semibold" style={{ color: "var(--loss)" }}>{t.sl}</div></div>
                      <div><span style={{ color: "var(--text-muted)" }}>Target</span><div className="font-mono font-semibold" style={{ color: "var(--gain)" }}>{t.target}</div></div>
                    </div>
                  </Link>
                ))}
              </div>
            </div>

            {/* Quick Links */}
            <div className="glass-card p-5">
              <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] mb-3" style={{ color: "var(--text-muted)" }}>Quick Actions</h3>
              <div className="space-y-1.5">
                {[
                  { label: "Analyze Stock", icon: Target, to: "/picks" },
                  { label: "Portfolio Review", icon: Briefcase, to: "/portfolio" },
                  { label: "Trading Strategies", icon: BarChart3, to: "/backtesting" },
                  { label: "Risk Calculator", icon: LineChart, to: "/sip" },
                ].map(item => (
                  <Link key={item.label} to={item.to} className="flex items-center gap-2.5 p-2.5 rounded-xl transition-all text-[12px] font-medium"
                    style={{ color: "var(--text-secondary)" }}
                    onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
                    onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                    <item.icon size={14} style={{ color: "var(--ai-accent)" }} />
                    {item.label}
                    <ChevronRight size={12} className="ml-auto" style={{ color: "var(--text-muted)" }} />
                  </Link>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {tab === "Trade Analysis" && (
        <div className="glass-card p-6 text-center" style={{ minHeight: 300 }}>
          <Brain size={40} className="mx-auto mb-4" style={{ color: "var(--ai-accent)", opacity: 0.5 }} />
          <h3 className="text-lg font-semibold font-display mb-2" style={{ color: "var(--text-primary)" }}>Trade Analysis</h3>
          <p className="text-[13px] mb-4" style={{ color: "var(--text-secondary)" }}>AI-powered trade analysis with entry/exit recommendations.</p>
          <Link to="/picks" className="btn-primary px-6 py-2.5 text-[13px]">View AI Picks</Link>
        </div>
      )}

      {tab === "Portfolio Review" && (
        <div className="glass-card p-6 text-center" style={{ minHeight: 300 }}>
          <Briefcase size={40} className="mx-auto mb-4" style={{ color: "var(--ai-accent)", opacity: 0.5 }} />
          <h3 className="text-lg font-semibold font-display mb-2" style={{ color: "var(--text-primary)" }}>Portfolio Review</h3>
          <p className="text-[13px] mb-4" style={{ color: "var(--text-secondary)" }}>Get AI insights on your portfolio allocation and performance.</p>
          <Link to="/portfolio" className="btn-primary px-6 py-2.5 text-[13px]">Go to Portfolio</Link>
        </div>
      )}

      {tab === "Strategy Builder" && (
        <div className="glass-card p-6 text-center" style={{ minHeight: 300 }}>
          <Lightbulb size={40} className="mx-auto mb-4" style={{ color: "var(--ai-accent)", opacity: 0.5 }} />
          <h3 className="text-lg font-semibold font-display mb-2" style={{ color: "var(--text-primary)" }}>Strategy Builder</h3>
          <p className="text-[13px] mb-4" style={{ color: "var(--text-secondary)" }}>Build and backtest trading strategies with AI assistance.</p>
          <Link to="/backtesting" className="btn-primary px-6 py-2.5 text-[13px]">Go to Backtesting</Link>
        </div>
      )}
    </div>
  );
}

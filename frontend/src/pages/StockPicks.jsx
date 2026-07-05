import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import api from "../services/api";
import { formatCurrency, formatPercent } from "../utils/formatters";

import { Target, Shield, TrendingUp, AlertTriangle, Brain, RefreshCw, ChevronDown, ChevronUp, Info } from "lucide-react";

function ConfidenceBadge({ value }) {
  const color = value >= 80 ? "var(--gain)" : value >= 70 ? "#F59E0B" : "var(--loss)";
  return <span className="px-2.5 py-1 rounded-lg text-xs font-mono font-semibold" style={{ background: `${color}15`, color, border: `1px solid ${color}30` }}>{value}%</span>;
}

function RiskBadge({ level }) {
  const c = level === "LOW" ? "var(--gain)" : level === "MEDIUM" ? "#F59E0B" : "var(--loss)";
  return <span className="px-2 py-0.5 rounded-lg text-[10px] font-mono font-semibold uppercase" style={{ background: `${c}15`, color: c }}>{level}</span>;
}

function PickCard({ pick, onExplain, explaining, onTrade }) {
  const [expanded, setExpanded] = useState(false);
  const navigate = useNavigate();

  return (
    <div data-testid={`pick-card-${pick.symbol}`} className="glass-card tracing-beam overflow-hidden">
      {/* Header */}
      <div className="p-5 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-3">
            <div>
              <h3 className="text-base font-semibold" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>{pick.name}</h3>
              <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>{pick.symbol} | {pick.sector}</span>
            </div>
          </div>
          <div className="text-right">
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-muted uppercase">Confidence</span>
              <ConfidenceBadge value={pick.confidence} />
            </div>
          </div>
        </div>

        {/* Price Levels */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
          <div>
            <span className="text-[10px] text-muted uppercase block">Entry</span>
            <span className="text-sm font-mono text-primary">{formatCurrency(pick.entry)}</span>
          </div>
          <div>
            <span className="text-[10px] text-muted uppercase block">Stop Loss</span>
            <span className="text-sm font-mono text-loss">{formatCurrency(pick.stop_loss)}</span>
          </div>
          <div>
            <span className="text-[10px] text-muted uppercase block">Target 1</span>
            <span className="text-sm font-mono text-gain">{formatCurrency(pick.target1)}</span>
          </div>
          <div>
            <span className="text-[10px] text-muted uppercase block">Target 2</span>
            <span className="text-sm font-mono text-gain">{formatCurrency(pick.target2)}</span>
          </div>
        </div>

        <div className="flex items-center gap-4 mt-3">
          <RiskBadge level={pick.risk_level} />
          <span className="text-xs text-muted font-mono">R:R {pick.risk_reward}</span>
          <span className="text-xs text-muted font-mono">{pick.pattern}</span>
          <span className="text-xs text-muted font-mono">Win: {pick.historical_success}</span>
        </div>
      </div>

      {/* Reasons */}
      <div className="p-4">
        <button onClick={() => setExpanded(!expanded)} className="flex items-center gap-1 text-xs text-muted hover:text-secondary transition-colors mb-2">
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          Why Selected
        </button>
        {expanded && (
          <div className="space-y-1.5 mb-3">
            {pick.reasons?.map((r, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <span className="text-gain mt-0.5">+</span>
                <span className="text-secondary">{r}</span>
              </div>
            ))}
            {pick.risk_factors?.map((r, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <AlertTriangle size={10} className="text-amber-500 mt-0.5 shrink-0" />
                <span className="text-muted">{r}</span>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-3 mt-3">
          <button
            data-testid={`explain-btn-${pick.symbol}`}
            onClick={() => onExplain(pick)}
            disabled={explaining}
            className="text-xs flex items-center gap-1 px-3 py-1.5 rounded-lg disabled:opacity-50 transition-all"
            style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}
          >
            <Brain size={12} />
            {explaining ? "Analyzing..." : "Full AI Report"}
          </button>
          <button
            data-testid={`trade-btn-${pick.symbol}`}
            onClick={() => onTrade(pick)}
            className="text-xs flex items-center gap-1 px-3 py-1.5 rounded-lg font-semibold transition-all hover:-translate-y-px"
            style={{ background: "var(--gain)", color: "#fff" }}
          >
            <TrendingUp size={12} />
            Trade Now
          </button>
          <button
            onClick={() => navigate(`/stock/${pick.symbol}`)}
            className="text-xs flex items-center gap-1 px-3 py-1.5 rounded-lg transition-all"
            style={{ background: "var(--bg-surface)", color: "var(--text-secondary)" }}
          >
            View Chart
          </button>
        </div>
      </div>
    </div>
  );
}

function AIDebateModal({ data, onClose }) {
  if (!data) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }} onClick={onClose}>
      <div className="glass-card max-w-4xl w-full max-h-[85vh] overflow-y-auto" style={{ background: "var(--bg)" }} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="p-5 border-b" style={{ borderColor: "var(--border)" }}>
          <h3 className="text-xl font-semibold" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>
            Full AI Analysis: {data.name || data.symbol}
          </h3>
          <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>Dual AI Debate + Scoring Breakdown + Related News</p>
        </div>

        {/* Confidence Score */}
        {data.confidence_score != null && (
          <div className="p-5 border-b" style={{ borderColor: "var(--border)" }}>
            <div className="flex items-center gap-4 mb-3">
              <span className="text-3xl font-mono font-bold" style={{ color: data.confidence_score >= 70 ? "var(--gain)" : "var(--loss)" }}>
                {data.confidence_score}
              </span>
              <span className="text-sm" style={{ color: "var(--text-muted)" }}>/100 Confidence Score</span>
            </div>
            {/* Scoring Breakdown */}
            {data.scoring_breakdown && (
              <div className="space-y-2">
                {data.scoring_breakdown.map((b, i) => (
                  <div key={i} className="flex items-center gap-3">
                    <span className="text-xs font-semibold w-24 shrink-0" style={{ color: "var(--text-secondary)" }}>{b.factor}</span>
                    <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-surface)" }}>
                      <div className="h-full rounded-full" style={{ width: `${(b.score / b.max) * 100}%`, background: b.score >= b.max * 0.7 ? "var(--gain)" : b.score >= b.max * 0.4 ? "#F59E0B" : "var(--loss)" }} />
                    </div>
                    <span className="text-xs font-mono w-12 text-right" style={{ color: "var(--text-primary)" }}>{b.score}/{b.max}</span>
                  </div>
                ))}
                {data.scoring_breakdown.map((b, i) => (
                  <p key={`r-${i}`} className="text-[11px] pl-28" style={{ color: "var(--text-muted)" }}>{b.reason}</p>
                ))}
              </div>
            )}
          </div>
        )}

        {/* AI Debate */}
        <div className="grid grid-cols-1 md:grid-cols-2">
          <div className="p-5 border-b md:border-b-0 md:border-r" style={{ borderColor: "var(--border)" }}>
            <div className="flex items-center gap-2 mb-3">
              {/* Claude avatar — inline SVG, no CDN */}
              <div className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0 font-bold text-sm" style={{ background: "linear-gradient(135deg, #2D1B69 0%, #4F46E5 100%)", color: "#fff" }}>C</div>
              <div>
                <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Claude Opus</span>
                <span className="text-[10px] block" style={{ color: "var(--ai-accent)" }}>Market Analyst</span>
              </div>
            </div>
            <p className="text-xs leading-relaxed whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>{data.claude_analysis}</p>
          </div>
          <div className="p-5">
            <div className="flex items-center gap-2 mb-3">
              {/* Gemini avatar — inline SVG, no CDN */}
              <div className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0 font-bold text-sm" style={{ background: "linear-gradient(135deg, #1a3a5c 0%, #0ea5e9 100%)", color: "#fff" }}>G</div>
              <div>
                <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Gemini Pro</span>
                <span className="text-[10px] block" style={{ color: "#F59E0B" }}>Risk Analyst</span>
              </div>
            </div>
            <p className="text-xs leading-relaxed whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>{data.gemini_analysis}</p>
          </div>
        </div>

        {/* Final Verdict */}
        <div className="p-5 border-t" style={{ borderColor: "var(--border)", background: "var(--bg-surface)" }}>
          <h4 className="text-xs font-bold uppercase tracking-widest mb-2 flex items-center gap-1" style={{ color: "var(--ai-accent)" }}>
            <Target size={12} /> Final Verdict
          </h4>
          <p className="text-sm leading-relaxed whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>{data.final_verdict}</p>
        </div>

        {/* Related News */}
        {data.related_news?.length > 0 && (
          <div className="p-5 border-t" style={{ borderColor: "var(--border)" }}>
            <h4 className="text-xs font-bold uppercase tracking-widest mb-3" style={{ color: "var(--text-muted)" }}>Related News</h4>
            <div className="space-y-2">
              {data.related_news.map((n, i) => (
                <a key={i} href={n.link} target="_blank" rel="noopener noreferrer" className="block p-2 rounded-lg transition-all hover:bg-[var(--hover)]">
                  <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>{n.title}</span>
                  <span className="text-[10px] ml-2" style={{ color: "var(--text-muted)" }}>{n.source}</span>
                </a>
              ))}
            </div>
          </div>
        )}

        <div className="p-4 border-t flex justify-end" style={{ borderColor: "var(--border)" }}>
          <button data-testid="close-debate-btn" onClick={onClose} className="px-5 py-2 rounded-xl text-xs font-medium"
            style={{ background: "var(--brand)", color: "var(--bg)" }}>Close</button>
        </div>
      </div>
    </div>
  );
}

export default function StockPicks() {
  const [picks, setPicks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [explaining, setExplaining] = useState(null);
  const [debateData, setDebateData] = useState(null);
  const [tradingPick, setTradingPick] = useState(null);
  const [tradeLoading, setTradeLoading] = useState(false);
  const [tradeResult, setTradeResult] = useState(null);

  useEffect(() => {
    fetchPicks();
  }, []);

  const fetchPicks = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/analysis/top-picks");
      setPicks(data.picks || []);
    } catch (err) {
      console.error("Picks error:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleExplain = async (pick) => {
    setExplaining(pick.symbol);
    try {
      const { data } = await api.post("/analysis/full-report", { symbol: pick.symbol, name: pick.name });
      setDebateData({ ...data, symbol: pick.symbol, name: pick.name });
    } catch (err) {
      console.error("Explain error:", err);
    } finally {
      setExplaining(null);
    }
  };

  const handleTrade = (pick) => {
    setTradingPick(pick);
    setTradeResult(null);
  };

  const executeTrade = async () => {
    if (!tradingPick) return;
    setTradeLoading(true);
    try {
      const { data } = await api.post("/zerodha/quick-trade", {
        symbol: tradingPick.symbol,
        stock_name: tradingPick.name,
        entry_price: tradingPick.entry,
        quantity: tradingPick.qty || 1,
        stop_loss: tradingPick.stop_loss,
        target1: tradingPick.target1,
        target2: tradingPick.target2,
        confidence: tradingPick.confidence,
      });
      setTradeResult(data);
    } catch (err) {
      setTradeResult({ message: "Trade failed: " + (err.response?.data?.detail || err.message) });
    } finally {
      setTradeLoading(false);
    }
  };

  return (
    <div data-testid="stock-picks-page" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-medium tracking-tight" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>AI Stock Picks</h1>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>Top 3 AI-selected stocks. Click "Full AI Report" for complete analysis or "Trade Now" to execute instantly.</p>
        </div>
        <button data-testid="refresh-picks-btn" onClick={fetchPicks} className="p-2 transition-colors" style={{ color: "var(--text-muted)" }}>
          <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {loading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => <div key={i} className="h-48 rounded-2xl skeleton" />)}
        </div>
      ) : (
        <div className="space-y-3 stagger-children">
          {picks.map((pick) => (
            <PickCard key={pick.symbol} pick={pick} onExplain={handleExplain} explaining={explaining === pick.symbol} onTrade={handleTrade} />
          ))}
        </div>
      )}

      <AIDebateModal data={debateData} onClose={() => setDebateData(null)} />

      {/* Trade Confirmation Modal */}
      {tradingPick && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }} onClick={() => { setTradingPick(null); setTradeResult(null); }}>
          <div className="glass-card max-w-md w-full p-6" style={{ background: "var(--bg)" }} onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold mb-4" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>
              Confirm Trade: {tradingPick.name}
            </h3>
            <div className="grid grid-cols-2 gap-3 mb-4">
              {[
                { l: "Symbol", v: tradingPick.symbol },
                { l: "Entry", v: `INR ${tradingPick.entry}` },
                { l: "Stop Loss", v: `INR ${tradingPick.stop_loss}`, c: "var(--loss)" },
                { l: "Target 1", v: `INR ${tradingPick.target1}`, c: "var(--gain)" },
                { l: "R:R", v: tradingPick.risk_reward },
                { l: "Confidence", v: `${tradingPick.confidence}%` },
              ].map((s) => (
                <div key={s.l}>
                  <span className="text-[10px] font-bold uppercase tracking-widest block" style={{ color: "var(--text-muted)" }}>{s.l}</span>
                  <span className="text-sm font-mono font-semibold" style={{ color: s.c || "var(--text-primary)" }}>{s.v}</span>
                </div>
              ))}
            </div>
            <div className="mb-4">
              <label className="text-[10px] font-bold uppercase tracking-widest block mb-1" style={{ color: "var(--text-muted)" }}>Quantity</label>
              <input data-testid="trade-qty-modal" type="number" defaultValue={1} min={1}
                onChange={(e) => setTradingPick({ ...tradingPick, qty: parseInt(e.target.value) || 1 })}
                className="w-full rounded-xl px-3 py-2 text-sm font-mono focus:outline-none"
                style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>

            {tradeResult && (
              <div className="mb-4 p-3 rounded-xl text-sm" style={{
                background: tradeResult.order?.source === "zerodha" ? "rgba(16,185,129,0.08)" : "var(--ai-accent-soft)",
                color: tradeResult.order?.source === "zerodha" ? "var(--gain)" : "var(--ai-accent)"
              }}>
                {tradeResult.message}
                {tradeResult.order?.order_id && <span className="block text-[10px] font-mono mt-1">Order ID: {tradeResult.order.order_id}</span>}
              </div>
            )}

            <div className="flex gap-2">
              <button onClick={() => { setTradingPick(null); setTradeResult(null); }}
                className="flex-1 py-2.5 rounded-xl text-sm font-medium"
                style={{ background: "var(--bg-surface)", color: "var(--text-secondary)" }}>Cancel</button>
              <button data-testid="confirm-trade-btn" onClick={executeTrade} disabled={tradeLoading || tradeResult}
                className="flex-1 py-2.5 rounded-xl text-sm font-semibold transition-all hover:-translate-y-px disabled:opacity-50"
                style={{ background: "var(--gain)", color: "#fff" }}>
                {tradeLoading ? "Placing..." : tradeResult ? "Done" : "Place Order"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

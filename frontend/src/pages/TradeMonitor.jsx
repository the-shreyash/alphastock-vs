import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Link } from "react-router-dom";
import api from "../services/api";
import { formatCurrency, formatPercent, formatNumber } from "../utils/formatters";
import { useWebSocket } from "../hooks/useWebSocket";
import { useAuth } from "../context/AuthContext";
import { Plus, X, Sparkles, Sliders, BarChart3, Newspaper, ChevronDown, Loader2, Target, ShieldAlert } from "lucide-react";

// ─── Per-trade math helpers ──────────────────────────────────────────────
// Risk = (entry − stop) × qty for a long (inverted for a short). Reward is the
// symmetric distance to target1. Both feed the R:R ratio surfaced in Details.
function tradeRisk(t) {
  const qty = t.quantity || 0;
  const entry = t.entry_price || 0;
  const sl = t.stop_loss || 0;
  const target = t.target1 || 0;
  const isShort = t.type === "SELL";
  const perShareRisk = isShort ? sl - entry : entry - sl;
  const perShareReward = isShort ? entry - target : target - entry;
  const riskAmt = perShareRisk * qty;
  const rewardAmt = perShareReward * qty;
  const riskPct = entry ? (perShareRisk / entry) * 100 : 0;
  const rr = perShareRisk > 0 ? Math.abs(perShareReward / perShareRisk) : 0;
  return { riskAmt, rewardAmt, riskPct, rr };
}

// Proximity flags: within 1% of target1 → "Near Target"; within 1% of SL → "Near SL".
function proximityFlags(t) {
  const price = t.current_price;
  if (price == null) return { nearTarget: false, nearSL: false };
  const nearTarget = t.target1 ? Math.abs(price - t.target1) / t.target1 <= 0.01 : false;
  const nearSL = t.stop_loss ? Math.abs(price - t.stop_loss) / t.stop_loss <= 0.01 : false;
  return { nearTarget, nearSL };
}

export default function TradeMonitor() {
  const { user } = useAuth();
  const { tradeUpdates } = useWebSocket(user?._id || user?.id || "");
  const [activeTrades, setActiveTrades] = useState([]);
  const [history, setHistory] = useState([]);
  const [pnl, setPnl] = useState(null);
  const [showNew, setShowNew] = useState(false);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("active");
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [tips, setTips] = useState({}); // { [tradeId]: "live coaching tip" }
  const [analyzingId, setAnalyzingId] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [modifyTrade, setModifyTrade] = useState(null);
  const [modifyNotes, setModifyNotes] = useState("");
  const [savingModify, setSavingModify] = useState(false);
  const [closingTrade, setClosingTrade] = useState(null);
  const [exitPrice, setExitPrice] = useState("");

  // New trade form
  const [form, setForm] = useState({ symbol: "", stock_name: "", entry_price: "", quantity: "", stop_loss: "", target1: "", target2: "", notes: "" });

  // Stock search with debounce
  const searchStocks = useCallback(async (query) => {
    if (query.length < 1) { setSuggestions([]); return; }
    try {
      const { data } = await api.get(`/stocks/search?q=${query}`);
      setSuggestions(data.slice(0, 8));
      setShowSuggestions(true);
    } catch { setSuggestions([]); }
  }, []);

  const handleSymbolChange = (val) => {
    setForm({ ...form, symbol: val });
    searchStocks(val);
  };

  const selectSuggestion = (stock) => {
    setForm({ ...form, symbol: stock.symbol, stock_name: stock.name });
    setSuggestions([]);
    setShowSuggestions(false);
  };

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchActive, 15000);
    return () => clearInterval(interval);
  }, []);

  // Live-patch open positions from the AI heartbeat's trade_update pushes so
  // current price / unrealized P&L tick between the 15s polling fallback.
  useEffect(() => {
    if (!tradeUpdates?.length) return;
    const latest = tradeUpdates[0];
    setActiveTrades(prev => prev.map(t =>
      (t._id === latest.trade_id || t.symbol === latest.symbol)
        ? {
            ...t,
            current_price: latest.current_price ?? t.current_price,
            unrealized_pnl: latest.unrealized_pnl ?? t.unrealized_pnl,
            unrealized_pnl_pct: latest.unrealized_pnl_pct ?? t.unrealized_pnl_pct,
          }
        : t
    ));
  }, [tradeUpdates]);

  // Live AI coaching tip per open trade, refreshed every 5 minutes.
  const activeIds = activeTrades.map((t) => t._id).join(",");
  useEffect(() => {
    if (!activeIds) return;
    fetchTips();
    const tipInterval = setInterval(fetchTips, 5 * 60 * 1000);
    return () => clearInterval(tipInterval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIds]);

  const fetchTips = async () => {
    const entries = await Promise.all(
      activeTrades.map(async (t) => {
        try {
          const { data } = await api.get(`/trades/${t._id}/live-tip`);
          return [t._id, data.tip];
        } catch {
          return [t._id, null];
        }
      })
    );
    setTips((prev) => ({ ...prev, ...Object.fromEntries(entries) }));
  };

  // On-demand AI analysis for a single position (reuses the live-tip endpoint).
  const analyzeTrade = async (t) => {
    setAnalyzingId(t._id);
    setExpandedId(t._id); // reveal the card body so the fresh tip is visible
    try {
      const { data } = await api.get(`/trades/${t._id}/live-tip`);
      setTips((prev) => ({ ...prev, [t._id]: data.tip }));
    } catch (err) {
      console.error(err);
    } finally {
      setAnalyzingId(null);
    }
  };

  const fetchAll = async () => {
    setLoading(true);
    try {
      const [a, h, p] = await Promise.all([
        api.get("/trades/active"),
        api.get("/trades/history"),
        api.get("/trades/pnl"),
      ]);
      setActiveTrades(a.data);
      setHistory(h.data);
      setPnl(p.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const fetchActive = async () => {
    try {
      const { data } = await api.get("/trades/active");
      setActiveTrades(data);
    } catch {}
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      // Create trade record
      await api.post("/trades", {
        symbol: form.symbol.toUpperCase(),
        stock_name: form.stock_name,
        entry_price: parseFloat(form.entry_price),
        quantity: parseInt(form.quantity),
        stop_loss: parseFloat(form.stop_loss),
        target1: parseFloat(form.target1),
        target2: form.target2 ? parseFloat(form.target2) : null,
        notes: form.notes,
      });

      // Also try to place order via Zerodha
      try {
        const { data: orderResult } = await api.post("/zerodha/order", {
          symbol: form.symbol.toUpperCase(),
          transaction_type: "BUY",
          quantity: parseInt(form.quantity),
          price: parseFloat(form.entry_price),
        });
        if (orderResult.source === "simulated") {
          console.log("Zerodha: Simulated order -", orderResult.message);
        }
      } catch (err) {
        console.log("Zerodha order skipped:", err.message);
      }

      setShowNew(false);
      setForm({ symbol: "", stock_name: "", entry_price: "", quantity: "", stop_loss: "", target1: "", target2: "", notes: "" });
      fetchAll();
    } catch (err) {
      console.error(err);
    }
  };

  const closeTrade = async (tradeId, price) => {
    try {
      await api.put(`/trades/${tradeId}`, { exit_price: parseFloat(price) });
      setClosingTrade(null);
      setExitPrice("");
      fetchAll();
    } catch (err) {
      console.error(err);
    }
  };

  // Only `notes` is editable server-side (PUT /trades accepts exit_price/status/notes).
  // Stop-loss and targets are shown read-only in the modal and clearly disabled.
  const saveModify = async () => {
    if (!modifyTrade) return;
    setSavingModify(true);
    try {
      await api.put(`/trades/${modifyTrade._id}`, { notes: modifyNotes });
      setModifyTrade(null);
      fetchAll();
    } catch (err) {
      console.error(err);
    } finally {
      setSavingModify(false);
    }
  };

  const openModify = (t) => { setModifyTrade(t); setModifyNotes(t.notes || ""); };
  const openClose = (t) => { setClosingTrade(t); setExitPrice(t.current_price ?? t.entry_price ?? ""); };

  return (
    <div data-testid="trades-page" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">Trade Monitor</h1>
          <p className="page-subtitle mt-0.5">Track your active positions and trade history</p>
        </div>
        <button data-testid="new-trade-btn" onClick={() => setShowNew(true)} className="btn-primary btn-lg">
          <Plus size={18} /> New Trade
        </button>
      </div>

      {/* PnL Summary */}
      {pnl && (
        <div data-testid="pnl-summary" className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {[
            { label: "Total P&L", value: `${pnl.total_pnl >= 0 ? "+" : ""}${formatCurrency(pnl.total_pnl)}`, color: pnl.total_pnl >= 0 ? "var(--gain)" : "var(--loss)" },
            { label: "Today P&L", value: `${pnl.today_pnl >= 0 ? "+" : ""}${formatCurrency(pnl.today_pnl)}`, color: pnl.today_pnl >= 0 ? "var(--gain)" : "var(--loss)" },
            { label: "Win Rate", value: `${pnl.win_rate}%`, color: "var(--text-primary)" },
            { label: "Open", value: pnl.open_trades, color: "var(--text-primary)" },
            { label: "Total Trades", value: pnl.total_trades, color: "var(--text-primary)" },
          ].map((s, i) => (
            <motion.div
              key={s.label}
              className="stat-card"
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ duration: 0.4, delay: i * 0.05 }}
            >
              <span className="stat-label block">{s.label}</span>
              <span className="stat-value" style={{ color: s.color }}>{s.value}</span>
            </motion.div>
          ))}
        </div>
      )}

      {/* Tabs */}
      <div className="tab-bar w-fit">
        {["active", "history"].map((t) => (
          <button
            key={t}
            data-testid={`tab-${t}`}
            onClick={() => setTab(t)}
            className={`tab-btn ${tab === t ? "active" : ""}`}
          >
            {t === "active" ? `Active (${activeTrades.length})` : `History (${history.length})`}
          </button>
        ))}
      </div>

      {/* Active Trades */}
      {tab === "active" && (
        <div className="space-y-1.5">
          {activeTrades.length === 0 ? (
            <div className="glass-card p-8 text-center">
              <p className="text-muted text-sm">No active trades. Click "New Trade" to get started.</p>
            </div>
          ) : (
            activeTrades.map((trade, i) => {
              const risk = tradeRisk(trade);
              const { nearTarget, nearSL } = proximityFlags(trade);
              const invested = (trade.entry_price || 0) * (trade.quantity || 0);
              const expanded = expandedId === trade._id;
              return (
              <motion.div
                key={trade._id}
                data-testid={`active-trade-${trade.symbol}`}
                className="glass-card p-4"
                initial={{ opacity: 0, y: 16 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-40px" }}
                transition={{ duration: 0.4, delay: Math.min(i, 8) * 0.05 }}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-3">
                    <span className={`px-1.5 py-0.5 text-[10px] font-mono rounded-xl ${trade.type === "BUY" ? "bg-gain/10 text-gain border border-gain/30" : "bg-loss/10 text-loss border border-loss/30"}`}>
                      {trade.type}
                    </span>
                    <div>
                      <span className="card-subtitle font-semibold" style={{ color: "var(--text-primary)" }}>{trade.stock_name}</span>
                      <span className="text-xs text-muted ml-2 font-mono">{trade.symbol}</span>
                    </div>
                    {/* Proximity alerts */}
                    {nearTarget && (
                      <span data-testid={`near-target-${trade.symbol}`} className="px-1.5 py-0.5 text-[10px] font-semibold rounded-lg flex items-center gap-1" style={{ background: "var(--gain-bg)", color: "var(--gain)" }}>
                        <Target size={10} /> Near Target
                      </span>
                    )}
                    {nearSL && (
                      <span data-testid={`near-sl-${trade.symbol}`} className="px-1.5 py-0.5 text-[10px] font-semibold rounded-lg flex items-center gap-1" style={{ background: "var(--loss-bg)", color: "var(--loss)" }}>
                        <ShieldAlert size={10} /> Near SL
                      </span>
                    )}
                  </div>
                  <div className="text-right">
                    {trade.unrealized_pnl != null && (
                      <div className={`text-sm font-mono ${trade.unrealized_pnl >= 0 ? "text-gain" : "text-loss"}`}>
                        {trade.unrealized_pnl >= 0 ? "+" : ""}{formatCurrency(trade.unrealized_pnl)}
                        <span className="text-[10px] ml-1">({formatPercent(trade.unrealized_pnl_pct)})</span>
                      </div>
                    )}
                  </div>
                </div>
                <div className="grid grid-cols-3 md:grid-cols-5 gap-2 text-xs">
                  <div><span className="text-muted">Entry</span><div className="font-mono text-primary">{formatCurrency(trade.entry_price)}</div></div>
                  <div><span className="text-muted">Current</span><div className="font-mono text-primary">{formatCurrency(trade.current_price)}</div></div>
                  <div><span className="text-muted">SL</span><div className="font-mono text-loss">{formatCurrency(trade.stop_loss)}</div></div>
                  <div><span className="text-muted">Target</span><div className="font-mono text-gain">{formatCurrency(trade.target1)}</div></div>
                  <div data-testid={`risk-${trade.symbol}`}><span className="text-muted">Risk</span><div className="font-mono text-loss">{formatCurrency(Math.abs(risk.riskAmt))}<span className="text-[10px] ml-1">({risk.riskPct.toFixed(1)}%)</span></div></div>
                </div>
                {tips[trade._id] && (
                  <div data-testid={`live-tip-${trade.symbol}`} className="flex items-start gap-2 mt-3 p-2.5 rounded-xl" style={{ background: "var(--ai-accent-soft)", border: "1px solid var(--ai-accent-glow)" }}>
                    <Sparkles size={13} className="shrink-0 mt-0.5" style={{ color: "var(--ai-accent)" }} />
                    <div className="min-w-0">
                      <span className="text-[9px] font-bold uppercase tracking-[0.12em] block mb-0.5" style={{ color: "var(--ai-accent)" }}>Live Coaching Tip</span>
                      <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>{tips[trade._id]}</p>
                    </div>
                  </div>
                )}

                {/* Expandable details + risk math */}
                <AnimatePresence initial={false}>
                  {expanded && (
                    <motion.div
                      data-testid={`details-${trade.symbol}`}
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                      className="overflow-hidden"
                    >
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3 p-3 rounded-xl text-xs" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                        <div><span className="text-muted">Quantity</span><div className="font-mono text-primary">{trade.quantity}</div></div>
                        <div><span className="text-muted">Invested</span><div className="font-mono text-primary">{formatCurrency(invested)}</div></div>
                        <div><span className="text-muted">Target 2</span><div className="font-mono text-gain">{trade.target2 ? formatCurrency(trade.target2) : "—"}</div></div>
                        <div><span className="text-muted">Entry Time</span><div className="font-mono text-primary">{trade.entry_time ? new Date(trade.entry_time).toLocaleDateString() : "—"}</div></div>
                        <div><span className="text-muted">Risk / Share</span><div className="font-mono text-loss">{formatCurrency(Math.abs(risk.riskAmt / (trade.quantity || 1)))}</div></div>
                        <div><span className="text-muted">Total Risk</span><div className="font-mono text-loss">{formatCurrency(Math.abs(risk.riskAmt))} ({risk.riskPct.toFixed(2)}%)</div></div>
                        <div><span className="text-muted">Reward @ T1</span><div className="font-mono text-gain">{formatCurrency(Math.abs(risk.rewardAmt))}</div></div>
                        <div><span className="text-muted">Risk : Reward</span><div className="font-mono text-primary">1 : {risk.rr.toFixed(2)}</div></div>
                      </div>
                      {trade.notes && (
                        <div className="mt-2 p-2.5 rounded-xl text-[11px]" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                          <span className="text-muted font-semibold">Notes: </span>{trade.notes}
                        </div>
                      )}
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Action bar */}
                <div className="flex flex-wrap items-center gap-2 mt-3">
                  <button
                    data-testid={`analyze-trade-${trade.symbol}`}
                    onClick={() => analyzeTrade(trade)}
                    disabled={analyzingId === trade._id}
                    className="btn-secondary btn-sm"
                  >
                    {analyzingId === trade._id ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                    Analyze
                  </button>
                  <button
                    data-testid={`modify-trade-${trade.symbol}`}
                    onClick={() => openModify(trade)}
                    className="btn-ghost btn-sm"
                  >
                    <Sliders size={13} /> Modify
                  </button>
                  <button
                    data-testid={`details-trade-${trade.symbol}`}
                    onClick={() => setExpandedId(expanded ? null : trade._id)}
                    className="btn-ghost btn-sm"
                  >
                    <ChevronDown size={13} className="transition-transform" style={{ transform: expanded ? "rotate(180deg)" : "none" }} /> Details
                  </button>
                  <Link data-testid={`chart-link-${trade.symbol}`} to={`/stock/${trade.symbol}`} className="btn-ghost btn-sm">
                    <BarChart3 size={13} /> Chart
                  </Link>
                  <Link data-testid={`news-link-${trade.symbol}`} to={`/news?symbol=${trade.symbol}`} className="btn-ghost btn-sm">
                    <Newspaper size={13} /> News
                  </Link>
                  <button
                    data-testid={`close-trade-${trade.symbol}`}
                    onClick={() => openClose(trade)}
                    className="btn-secondary btn-sm ml-auto"
                  >
                    Sell / Close
                  </button>
                </div>
              </motion.div>
              );
            })
          )}
        </div>
      )}

      {/* History */}
      {tab === "history" && (
        <div className="space-y-1">
          {history.length === 0 ? (
            <div className="glass-card p-8 text-center">
              <p className="text-muted text-sm">No trade history yet.</p>
            </div>
          ) : (
            history.map((trade, i) => (
              <motion.div
                key={trade._id}
                className="glass-card p-3 flex items-center justify-between"
                initial={{ opacity: 0, y: 16 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-40px" }}
                transition={{ duration: 0.35, delay: Math.min(i, 8) * 0.04 }}
              >
                <div className="flex items-center gap-3">
                  <span className={`px-1.5 py-0.5 text-[10px] font-mono rounded-xl ${trade.status === "TARGET_HIT" ? "bg-gain/10 text-gain" : trade.status === "SL_HIT" ? "bg-loss/10 text-loss" : "bg-zinc-800 text-secondary"}`}>
                    {trade.status}
                  </span>
                  <div>
                    <span className="card-subtitle font-semibold" style={{ color: "var(--text-primary)" }}>{trade.symbol}</span>
                    <span className="text-xs text-muted ml-2">Qty: {trade.quantity}</span>
                  </div>
                </div>
                <div className="text-right">
                  <div className={`text-sm font-mono ${(trade.pnl || 0) >= 0 ? "text-gain" : "text-loss"}`}>
                    {(trade.pnl || 0) >= 0 ? "+" : ""}{formatCurrency(trade.pnl)}
                  </div>
                  <div className="text-[10px] text-muted font-mono">{formatPercent(trade.pnl_percent)}</div>
                </div>
              </motion.div>
            ))
          )}
        </div>
      )}

      {/* New Trade Modal */}
      {showNew && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4" onClick={() => setShowNew(false)}>
          <div className="glass-card w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b" style={{ borderColor: "var(--border)" }}>
              <h3 className="card-title">New Trade Entry</h3>
              <button onClick={() => setShowNew(false)} style={{ color: "var(--text-muted)" }}><X size={18} /></button>
            </div>
            <form onSubmit={handleSubmit} className="p-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="stat-label block mb-1">Symbol</label>
                  <div className="relative">
                    <input data-testid="trade-symbol-input" value={form.symbol} onChange={(e) => handleSymbolChange(e.target.value)} required className="w-full rounded-xl px-3 py-2 text-sm font-mono focus:outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} placeholder="Type stock name..." autoComplete="off" />
                    {showSuggestions && suggestions.length > 0 && (
                      <div className="absolute z-50 top-full left-0 right-0 mt-1 rounded-xl border overflow-hidden max-h-48 overflow-y-auto" style={{ background: "var(--bg-elevated)", borderColor: "var(--border)" }}>
                        {suggestions.map((s) => (
                          <button key={s.symbol} type="button" onClick={() => selectSuggestion(s)}
                            className="w-full text-left px-3 py-2 text-sm flex justify-between transition-all hover:bg-[var(--hover)]"
                            style={{ borderBottom: "1px solid var(--border)" }}>
                            <span><span className="font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{s.symbol}</span> <span className="text-xs" style={{ color: "var(--text-muted)" }}>{s.name}</span></span>
                            <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>{s.sector}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
                <div>
                  <label className="stat-label block mb-1">Stock Name</label>
                  <input data-testid="trade-name-input" value={form.stock_name} onChange={(e) => setForm({ ...form, stock_name: e.target.value })} required className="w-full rounded-xl px-3 py-2 text-sm focus:outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} placeholder="Reliance Industries" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="stat-label block mb-1">Entry Price</label>
                  <input data-testid="trade-entry-input" type="number" step="0.01" value={form.entry_price} onChange={(e) => setForm({ ...form, entry_price: e.target.value })} required className="w-full rounded-xl px-3 py-2 text-sm font-mono focus:outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Quantity</label>
                  <input data-testid="trade-qty-input" type="number" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} required className="w-full rounded-xl px-3 py-2 text-sm font-mono focus:outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="stat-label block mb-1">Stop Loss</label>
                  <input data-testid="trade-sl-input" type="number" step="0.01" value={form.stop_loss} onChange={(e) => setForm({ ...form, stop_loss: e.target.value })} required className="w-full rounded-xl px-3 py-2 text-sm font-mono focus:outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Target 1</label>
                  <input data-testid="trade-t1-input" type="number" step="0.01" value={form.target1} onChange={(e) => setForm({ ...form, target1: e.target.value })} required className="w-full rounded-xl px-3 py-2 text-sm font-mono focus:outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Target 2</label>
                  <input data-testid="trade-t2-input" type="number" step="0.01" value={form.target2} onChange={(e) => setForm({ ...form, target2: e.target.value })} className="w-full rounded-xl px-3 py-2 text-sm font-mono focus:outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                </div>
              </div>
              <div>
                <label className="stat-label block mb-1">Notes</label>
                <textarea data-testid="trade-notes-input" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} rows={2} className="w-full rounded-xl px-3 py-2 text-sm focus:outline-none resize-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              </div>
              <button data-testid="submit-trade-btn" type="submit" className="btn-primary btn-lg btn-block">
                Execute Trade
              </button>
            </form>
          </div>
        </div>
      )}

      {/* Modify Trade Modal */}
      {modifyTrade && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4" onClick={() => setModifyTrade(null)}>
          <div data-testid="modify-modal" className="glass-card w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b" style={{ borderColor: "var(--border)" }}>
              <h3 className="card-title">Modify — {modifyTrade.symbol}</h3>
              <button onClick={() => setModifyTrade(null)} style={{ color: "var(--text-muted)" }}><X size={18} /></button>
            </div>
            <div className="p-4 space-y-3">
              {/* SL / targets are not server-updatable yet — shown read-only. */}
              <div className="grid grid-cols-3 gap-3" title="Editing stop-loss and targets isn't supported by the server yet">
                <div>
                  <label className="stat-label block mb-1">Stop Loss</label>
                  <input disabled value={modifyTrade.stop_loss ?? ""} className="w-full rounded-xl px-3 py-2 text-sm font-mono opacity-50 cursor-not-allowed" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-muted)" }} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Target 1</label>
                  <input disabled value={modifyTrade.target1 ?? ""} className="w-full rounded-xl px-3 py-2 text-sm font-mono opacity-50 cursor-not-allowed" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-muted)" }} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Target 2</label>
                  <input disabled value={modifyTrade.target2 ?? ""} className="w-full rounded-xl px-3 py-2 text-sm font-mono opacity-50 cursor-not-allowed" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-muted)" }} />
                </div>
              </div>
              <p className="text-[11px] leading-snug" style={{ color: "var(--text-muted)" }}>
                Stop-loss and targets are locked once a trade is live. To change them, close this position and open a new one. You can still update your notes below.
              </p>
              <div>
                <label className="stat-label block mb-1">Notes</label>
                <textarea data-testid="modify-notes-input" value={modifyNotes} onChange={(e) => setModifyNotes(e.target.value)} rows={3} className="w-full rounded-xl px-3 py-2 text-sm focus:outline-none resize-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} placeholder="Trade rationale, adjustments, reminders…" />
              </div>
              <button data-testid="save-modify-btn" onClick={saveModify} disabled={savingModify} className="btn-primary btn-lg btn-block">
                {savingModify ? <Loader2 size={16} className="animate-spin" /> : null}
                {savingModify ? "Saving…" : "Save Changes"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Close Trade Modal */}
      {closingTrade && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4" onClick={() => setClosingTrade(null)}>
          <div data-testid="close-modal" className="glass-card w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b" style={{ borderColor: "var(--border)" }}>
              <h3 className="card-title">Close — {closingTrade.symbol}</h3>
              <button onClick={() => setClosingTrade(null)} style={{ color: "var(--text-muted)" }}><X size={18} /></button>
            </div>
            <div className="p-4 space-y-3">
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div><span className="text-muted">Entry</span><div className="font-mono text-primary">{formatCurrency(closingTrade.entry_price)}</div></div>
                <div><span className="text-muted">Current</span><div className="font-mono text-primary">{formatCurrency(closingTrade.current_price)}</div></div>
              </div>
              <div>
                <label className="stat-label block mb-1">Exit Price</label>
                <input data-testid="exit-price-input" type="number" step="0.01" value={exitPrice} onChange={(e) => setExitPrice(e.target.value)} className="w-full rounded-xl px-3 py-2 text-sm font-mono focus:outline-none" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              </div>
              {exitPrice !== "" && !isNaN(parseFloat(exitPrice)) && (
                <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
                  Realized P&L:{" "}
                  {(() => {
                    const px = parseFloat(exitPrice);
                    const raw = (closingTrade.type === "SELL" ? closingTrade.entry_price - px : px - closingTrade.entry_price) * (closingTrade.quantity || 0);
                    return <span className="font-mono font-semibold" style={{ color: raw >= 0 ? "var(--gain)" : "var(--loss)" }}>{raw >= 0 ? "+" : ""}{formatCurrency(raw)}</span>;
                  })()}
                </p>
              )}
              <button data-testid="confirm-close-btn" onClick={() => exitPrice !== "" && closeTrade(closingTrade._id, exitPrice)} disabled={exitPrice === "" || isNaN(parseFloat(exitPrice))} className="btn-primary btn-lg btn-block">
                Confirm Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

import { useState, useEffect, useCallback } from "react";
import api from "../services/api";
import { formatCurrency, formatPercent, formatNumber } from "../utils/formatters";
import { Plus, X, TrendingUp, TrendingDown, AlertTriangle, Target, Clock, Search, Sparkles } from "lucide-react";

export default function TradeMonitor() {
  const [activeTrades, setActiveTrades] = useState([]);
  const [history, setHistory] = useState([]);
  const [pnl, setPnl] = useState(null);
  const [showNew, setShowNew] = useState(false);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("active");
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [tips, setTips] = useState({}); // { [tradeId]: "live coaching tip" }

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

  const closeTrade = async (tradeId, exitPrice) => {
    try {
      await api.put(`/trades/${tradeId}`, { exit_price: parseFloat(exitPrice) });
      fetchAll();
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div data-testid="trades-page" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-medium text-primary tracking-tight">Trade Monitor</h1>
          <p className="text-xs text-muted">Track your active positions and trade history</p>
        </div>
        <button data-testid="new-trade-btn" onClick={() => setShowNew(true)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-xl transition-colors" style={{ background: "var(--brand)", color: "var(--bg)" }}>
          <Plus size={14} /> New Trade
        </button>
      </div>

      {/* PnL Summary */}
      {pnl && (
        <div data-testid="pnl-summary" className="grid grid-cols-2 md:grid-cols-5 gap-1">
          <div className="glass-card  p-3">
            <span className="text-[10px] text-muted uppercase block">Total P&L</span>
            <span className={`text-lg font-mono ${pnl.total_pnl >= 0 ? "text-gain" : "text-loss"}`}>
              {pnl.total_pnl >= 0 ? "+" : ""}{formatCurrency(pnl.total_pnl)}
            </span>
          </div>
          <div className="glass-card  p-3">
            <span className="text-[10px] text-muted uppercase block">Today P&L</span>
            <span className={`text-lg font-mono ${pnl.today_pnl >= 0 ? "text-gain" : "text-loss"}`}>
              {pnl.today_pnl >= 0 ? "+" : ""}{formatCurrency(pnl.today_pnl)}
            </span>
          </div>
          <div className="glass-card  p-3">
            <span className="text-[10px] text-muted uppercase block">Win Rate</span>
            <span className="text-lg font-mono text-primary">{pnl.win_rate}%</span>
          </div>
          <div className="glass-card  p-3">
            <span className="text-[10px] text-muted uppercase block">Open</span>
            <span className="text-lg font-mono text-primary">{pnl.open_trades}</span>
          </div>
          <div className="glass-card  p-3">
            <span className="text-[10px] text-muted uppercase block">Total Trades</span>
            <span className="text-lg font-mono text-primary">{pnl.total_trades}</span>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-0 border-b">
        {["active", "history"].map((t) => (
          <button
            key={t}
            data-testid={`tab-${t}`}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-xs uppercase tracking-widest transition-colors border-b-2 ${tab === t ? "border-white text-primary" : "border-transparent text-muted hover:text-secondary"}`}
          >
            {t === "active" ? `Active (${activeTrades.length})` : `History (${history.length})`}
          </button>
        ))}
      </div>

      {/* Active Trades */}
      {tab === "active" && (
        <div className="space-y-1">
          {activeTrades.length === 0 ? (
            <div className="glass-card  p-8 text-center">
              <p className="text-muted text-sm">No active trades. Click "New Trade" to get started.</p>
            </div>
          ) : (
            activeTrades.map((trade) => (
              <div key={trade._id} data-testid={`active-trade-${trade.symbol}`} className="glass-card  p-4">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-3">
                    <span className={`px-1.5 py-0.5 text-[10px] font-mono rounded-xl ${trade.type === "BUY" ? "bg-gain/10 text-gain border border-gain/30" : "bg-loss/10 text-loss border border-loss/30"}`}>
                      {trade.type}
                    </span>
                    <div>
                      <span className="text-sm text-primary font-medium">{trade.stock_name}</span>
                      <span className="text-xs text-muted ml-2 font-mono">{trade.symbol}</span>
                    </div>
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
                <div className="grid grid-cols-4 gap-2 text-xs">
                  <div><span className="text-muted">Entry</span><div className="font-mono text-primary">{formatCurrency(trade.entry_price)}</div></div>
                  <div><span className="text-muted">Current</span><div className="font-mono text-primary">{formatCurrency(trade.current_price)}</div></div>
                  <div><span className="text-muted">SL</span><div className="font-mono text-loss">{formatCurrency(trade.stop_loss)}</div></div>
                  <div><span className="text-muted">Target</span><div className="font-mono text-gain">{formatCurrency(trade.target1)}</div></div>
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
                <div className="flex gap-2 mt-3">
                  <button
                    data-testid={`close-trade-${trade.symbol}`}
                    onClick={() => {
                      const price = prompt("Exit price:");
                      if (price) closeTrade(trade._id, price);
                    }}
                    className="px-3 py-1 bg-zinc-800 text-secondary text-xs rounded-xl bg-hover:hover transition-colors"
                  >
                    Close Trade
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* History */}
      {tab === "history" && (
        <div className="space-y-1">
          {history.length === 0 ? (
            <div className="glass-card  p-8 text-center">
              <p className="text-muted text-sm">No trade history yet.</p>
            </div>
          ) : (
            history.map((trade) => (
              <div key={trade._id} className="glass-card  p-3 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className={`px-1.5 py-0.5 text-[10px] font-mono rounded-xl ${trade.status === "TARGET_HIT" ? "bg-gain/10 text-gain" : trade.status === "SL_HIT" ? "bg-loss/10 text-loss" : "bg-zinc-800 text-secondary"}`}>
                    {trade.status}
                  </span>
                  <div>
                    <span className="text-sm text-primary">{trade.symbol}</span>
                    <span className="text-xs text-muted ml-2">Qty: {trade.quantity}</span>
                  </div>
                </div>
                <div className="text-right">
                  <div className={`text-sm font-mono ${(trade.pnl || 0) >= 0 ? "text-gain" : "text-loss"}`}>
                    {(trade.pnl || 0) >= 0 ? "+" : ""}{formatCurrency(trade.pnl)}
                  </div>
                  <div className="text-[10px] text-muted font-mono">{formatPercent(trade.pnl_percent)}</div>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* New Trade Modal */}
      {showNew && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4" onClick={() => setShowNew(false)}>
          <div className="glass-card  w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b">
              <h3 className="text-sm font-medium text-primary">New Trade Entry</h3>
              <button onClick={() => setShowNew(false)} className="text-muted hover:text-secondary"><X size={16} /></button>
            </div>
            <form onSubmit={handleSubmit} className="p-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[10px] text-muted uppercase block mb-1">Symbol</label>
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
                  <label className="text-[10px] text-muted uppercase block mb-1">Stock Name</label>
                  <input data-testid="trade-name-input" value={form.stock_name} onChange={(e) => setForm({ ...form, stock_name: e.target.value })} required className="w-full bg-surface  px-2 py-1.5 text-sm text-primary focus:outline-none focus:border-zinc-600" placeholder="Reliance Industries" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[10px] text-muted uppercase block mb-1">Entry Price</label>
                  <input data-testid="trade-entry-input" type="number" step="0.01" value={form.entry_price} onChange={(e) => setForm({ ...form, entry_price: e.target.value })} required className="w-full bg-surface  px-2 py-1.5 text-sm text-primary font-mono focus:outline-none focus:border-zinc-600" />
                </div>
                <div>
                  <label className="text-[10px] text-muted uppercase block mb-1">Quantity</label>
                  <input data-testid="trade-qty-input" type="number" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} required className="w-full bg-surface  px-2 py-1.5 text-sm text-primary font-mono focus:outline-none focus:border-zinc-600" />
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="text-[10px] text-muted uppercase block mb-1">Stop Loss</label>
                  <input data-testid="trade-sl-input" type="number" step="0.01" value={form.stop_loss} onChange={(e) => setForm({ ...form, stop_loss: e.target.value })} required className="w-full bg-surface  px-2 py-1.5 text-sm text-primary font-mono focus:outline-none focus:border-zinc-600" />
                </div>
                <div>
                  <label className="text-[10px] text-muted uppercase block mb-1">Target 1</label>
                  <input data-testid="trade-t1-input" type="number" step="0.01" value={form.target1} onChange={(e) => setForm({ ...form, target1: e.target.value })} required className="w-full bg-surface  px-2 py-1.5 text-sm text-primary font-mono focus:outline-none focus:border-zinc-600" />
                </div>
                <div>
                  <label className="text-[10px] text-muted uppercase block mb-1">Target 2</label>
                  <input data-testid="trade-t2-input" type="number" step="0.01" value={form.target2} onChange={(e) => setForm({ ...form, target2: e.target.value })} className="w-full bg-surface  px-2 py-1.5 text-sm text-primary font-mono focus:outline-none focus:border-zinc-600" />
                </div>
              </div>
              <div>
                <label className="text-[10px] text-muted uppercase block mb-1">Notes</label>
                <textarea data-testid="trade-notes-input" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} rows={2} className="w-full bg-surface  px-2 py-1.5 text-sm text-primary focus:outline-none focus:border-zinc-600 resize-none" />
              </div>
              <button data-testid="submit-trade-btn" type="submit" className="w-full py-3 rounded-xl text-xs font-semibold transition-colors" style={{ background: "var(--brand)", color: "var(--bg)" }}>
                Execute Trade
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import api from "../services/api";
import { formatCurrency, formatNumber, formatPercent } from "../utils/formatters";
import {
  FlaskConical, TrendingUp, TrendingDown, Plus, X, RefreshCw,
  RotateCcw, ChevronDown, AlertTriangle, CheckCircle2, Clock,
} from "lucide-react";

const SETUP_TYPES = [
  "RSI_BREAKOUT", "VWAP_CROSS", "BULLISH_ENGULFING", "BEARISH_ENGULFING",
  "EMA_CROSSOVER", "MACD_SIGNAL", "SUPPORT_BOUNCE", "RESISTANCE_BREAK",
  "TRIANGLE_BREAKOUT", "GAP_UP_PLAY", "MOMENTUM",
];

function Toast({ msg, type, onClose }) {
  useEffect(() => {
    const t = setTimeout(onClose, 3500);
    return () => clearTimeout(t);
  }, [onClose]);
  return (
    <div
      className="fixed bottom-6 right-6 z-50 flex items-center gap-3 px-5 py-3 rounded-2xl shadow-2xl text-sm font-medium animate-fade-in"
      style={{
        background: type === "success" ? "var(--gain)" : "var(--loss)",
        color: "#fff",
      }}
    >
      {type === "success" ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
      {msg}
    </div>
  );
}

function StatCard({ label, value, sub, color, index = 0 }) {
  return (
    <motion.div
      className="stat-card flex flex-col gap-1"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4, delay: index * 0.05 }}
    >
      <span className="stat-label">{label}</span>
      <span className="stat-value" style={{ color: color || "var(--text-primary)" }}>{value}</span>
      {sub && <span className="caption">{sub}</span>}
    </motion.div>
  );
}

function NewTradeModal({ onClose, onSubmit, loading }) {
  const [form, setForm] = useState({
    symbol: "", stock_name: "", type: "BUY", quantity: 10,
    entry_price: "", stop_loss: "", target1: "", target2: "",
    setup_type: "MOMENTUM", notes: "",
  });

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const fetchPrice = async () => {
    if (!form.symbol) return;
    try {
      const res = await api.get(`/stocks/${form.symbol}`);
      set("entry_price", res.data.price || "");
      set("stock_name", res.data.name || form.symbol.toUpperCase());
    } catch { /* ignore */ }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit({
      ...form,
      quantity: parseInt(form.quantity),
      entry_price: parseFloat(form.entry_price),
      stop_loss: parseFloat(form.stop_loss),
      target1: parseFloat(form.target1),
      target2: parseFloat(form.target2) || parseFloat(form.target1),
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.7)" }}>
      <div className="glass-card w-full max-w-lg p-6 shadow-2xl">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2">
            <FlaskConical size={20} style={{ color: "var(--ai-accent)" }} />
            <h2 className="card-title">New Paper Trade</h2>
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: "#f59e0b22", color: "#f59e0b" }}>SIMULATED</span>
          </div>
          <button onClick={onClose} style={{ color: "var(--text-muted)" }}><X size={18} /></button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Symbol row */}
          <div className="flex gap-2">
            <div className="flex-1">
              <label className="stat-label mb-1 block">NSE Symbol</label>
              <input
                value={form.symbol} onChange={e => set("symbol", e.target.value.toUpperCase())}
                onBlur={fetchPrice}
                placeholder="RELIANCE"
                required
                className="w-full px-3 py-2 rounded-xl text-sm font-mono"
                style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
            <div>
              <label className="stat-label mb-1 block">Type</label>
              <div className="flex rounded-xl overflow-hidden border" style={{ borderColor: "var(--border)" }}>
                {["BUY", "SELL"].map(t => (
                  <button key={t} type="button" onClick={() => set("type", t)}
                    className="px-4 py-2 text-xs font-bold transition-all"
                    style={{
                      background: form.type === t ? (t === "BUY" ? "var(--gain)" : "var(--loss)") : "var(--bg)",
                      color: form.type === t ? "#fff" : "var(--text-muted)",
                    }}>
                    {t}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Qty + Price */}
          <div className="grid grid-cols-2 gap-3">
            {[
              { key: "quantity", label: "Quantity", type: "number", min: 1 },
              { key: "entry_price", label: "Entry Price (₹)", type: "number", step: "0.01" },
              { key: "stop_loss", label: "Stop Loss (₹)", type: "number", step: "0.01" },
              { key: "target1", label: "Target (₹)", type: "number", step: "0.01" },
            ].map(f => (
              <div key={f.key}>
                <label className="stat-label mb-1 block">{f.label}</label>
                <input
                  type={f.type} step={f.step} min={f.min}
                  value={form[f.key]} onChange={e => set(f.key, e.target.value)}
                  required
                  className="w-full px-3 py-2 rounded-xl text-sm font-mono"
                  style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                />
              </div>
            ))}
          </div>

          {/* Setup type */}
          <div>
            <label className="stat-label mb-1 block">Setup Type</label>
            <div className="relative">
              <select
                value={form.setup_type} onChange={e => set("setup_type", e.target.value)}
                className="w-full px-3 py-2 rounded-xl text-sm appearance-none"
                style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              >
                {SETUP_TYPES.map(s => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
              </select>
              <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--text-muted)" }} />
            </div>
          </div>

          <button
            type="submit" disabled={loading}
            className="btn-primary btn-lg btn-block"
            style={{ opacity: loading ? 0.7 : 1 }}
          >
            {loading ? "Placing..." : "Place Paper Trade"}
          </button>
        </form>
      </div>
    </div>
  );
}

export default function PaperTrading() {
  const [balance, setBalance] = useState(null);
  const [pnl, setPnl] = useState(null);
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [toast, setToast] = useState(null);
  const [resetting, setResetting] = useState(false);
  const [tab, setTab] = useState("open");

  const showToast = (msg, type = "success") => setToast({ msg, type });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [b, p, t] = await Promise.all([
        api.get("/paper/balance"),
        api.get("/paper/pnl"),
        api.get("/paper/trades"),
      ]);
      setBalance(b.data);
      setPnl(p.data);
      setTrades(t.data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleTrade = async (form) => {
    setPlacing(true);
    try {
      await api.post("/paper/trade", form);
      showToast("Paper trade placed successfully!");
      setModalOpen(false);
      load();
    } catch (e) {
      showToast(e.response?.data?.detail || "Trade failed", "error");
    } finally {
      setPlacing(false);
    }
  };

  const closeTrade = async (id) => {
    try {
      const res = await api.post(`/paper/close/${id}`);
      showToast(`Closed at ₹${res.data.exit_price} · P&L: ₹${res.data.pnl}`);
      load();
    } catch (e) {
      showToast(e.response?.data?.detail || "Close failed", "error");
    }
  };

  const handleReset = async () => {
    if (!window.confirm("Reset paper capital to ₹1,00,000? All open trades will be closed.")) return;
    setResetting(true);
    try {
      await api.post("/paper/reset");
      showToast("Paper capital reset to ₹1,00,000!");
      load();
    } catch { showToast("Reset failed", "error"); }
    finally { setResetting(false); }
  };

  const openTrades = trades.filter(t => t.status === "OPEN");
  const closedTrades = trades.filter(t => t.status === "CLOSED");

  if (loading) return (
    <div className="space-y-4">
      {[1, 2, 3].map(i => <div key={i} className="h-24 rounded-2xl animate-pulse" style={{ background: "var(--bg-surface)" }} />)}
    </div>
  );

  const pnlColor = (v) => v > 0 ? "var(--gain)" : v < 0 ? "var(--loss)" : "var(--text-muted)";

  return (
    <div className="space-y-5" data-testid="paper-trading-page">
      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
      {modalOpen && <NewTradeModal onClose={() => setModalOpen(false)} onSubmit={handleTrade} loading={placing} />}

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div>
            <h1 className="page-title flex items-center gap-2">
              <FlaskConical size={24} style={{ color: "#f59e0b" }} />Paper Trading
              <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: "#f59e0b22", color: "#f59e0b" }}>SIMULATED</span>
            </h1>
            <p className="page-subtitle mt-0.5">Practice trading with virtual money</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className="btn-ghost btn-sm" title="Refresh">
            <RefreshCw size={16} />
          </button>
          <button onClick={handleReset} disabled={resetting}
            className="btn-secondary btn-sm"
            style={{ background: "rgba(248,113,113,0.12)", color: "var(--loss)", border: "1px solid rgba(248,113,113,0.2)" }}>
            <RotateCcw size={13} />
            Reset Capital
          </button>
          <button onClick={() => setModalOpen(true)} className="btn-primary btn-lg">
            <Plus size={16} />
            New Paper Trade
          </button>
        </div>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Paper Capital" value={formatCurrency(balance?.balance)} sub="Starting: ₹1,00,000" index={0} />
        <StatCard
          label="Total P&L"
          value={`${pnl?.total_pnl >= 0 ? "+" : ""}${formatCurrency(pnl?.total_pnl)}`}
          sub={`${pnl?.total_pnl_pct >= 0 ? "+" : ""}${pnl?.total_pnl_pct?.toFixed(2)}% return`}
          color={pnlColor(pnl?.total_pnl)}
          index={1}
        />
        <StatCard label="Open Trades" value={pnl?.open_trades ?? 0} sub={`${pnl?.closed_trades ?? 0} closed`} index={2} />
        <StatCard
          label="Realized P&L"
          value={formatCurrency(pnl?.realized_pnl)}
          sub={`Unrealized: ${formatCurrency(pnl?.unrealized_pnl)}`}
          color={pnlColor(pnl?.realized_pnl)}
          index={3}
        />
      </div>

      {/* Trades Table */}
      <motion.div
        className="glass-card p-5"
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }}
        transition={{ duration: 0.4 }}
      >
        {/* Tabs */}
        <div className="tab-bar mb-4 w-fit">
          {[{ key: "open", label: `Open (${openTrades.length})` }, { key: "closed", label: `Closed (${closedTrades.length})` }].map(t => (
            <button key={t.key} onClick={() => setTab(t.key)} className={`tab-btn ${tab === t.key ? "active" : ""}`}>
              {t.label}
            </button>
          ))}
        </div>

        {tab === "open" && (
          openTrades.length === 0 ? (
            <div className="text-center py-12">
              <FlaskConical size={32} className="mx-auto mb-2 opacity-30" style={{ color: "var(--text-muted)" }} />
              <p className="text-sm" style={{ color: "var(--text-muted)" }}>No open paper trades. Place your first one!</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    {["Symbol", "Type", "Qty", "Entry", "Current", "P&L", "SL", "Target", "Setup", "Action"].map(h => (
                      <th key={h} className="text-left py-2 px-2 text-[10px] font-bold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {openTrades.map(t => {
                    const unPnl = t.unrealized_pnl || 0;
                    const unPct = t.unrealized_pnl_pct || 0;
                    return (
                      <tr key={t._id} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td className="py-3 px-2 font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{t.symbol}</td>
                        <td className="py-3 px-2">
                          <span className="text-[10px] font-bold px-2 py-0.5 rounded-full"
                            style={{ background: t.type === "BUY" ? "rgba(52,211,153,0.15)" : "rgba(248,113,113,0.15)", color: t.type === "BUY" ? "var(--gain)" : "var(--loss)" }}>
                            {t.type}
                          </span>
                        </td>
                        <td className="py-3 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>{t.quantity}</td>
                        <td className="py-3 px-2 font-mono" style={{ color: "var(--text-primary)" }}>₹{t.entry_price}</td>
                        <td className="py-3 px-2 font-mono" style={{ color: "var(--text-secondary)" }}>₹{t.current_price || "—"}</td>
                        <td className="py-3 px-2 font-mono font-semibold" style={{ color: pnlColor(unPnl) }}>
                          {unPnl >= 0 ? "+" : ""}₹{unPnl.toFixed(0)} ({unPct >= 0 ? "+" : ""}{unPct.toFixed(2)}%)
                        </td>
                        <td className="py-3 px-2 font-mono text-xs" style={{ color: "var(--loss)" }}>₹{t.stop_loss}</td>
                        <td className="py-3 px-2 font-mono text-xs" style={{ color: "var(--gain)" }}>₹{t.target1}</td>
                        <td className="py-3 px-2 text-[10px]" style={{ color: "var(--text-muted)" }}>{(t.setup_type || "—").replace(/_/g, " ")}</td>
                        <td className="py-3 px-2">
                          <button onClick={() => closeTrade(t._id)}
                            className="btn-secondary btn-sm"
                            style={{ background: "rgba(248,113,113,0.15)", color: "var(--loss)", border: "1px solid rgba(248,113,113,0.25)" }}>
                            Close
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        )}

        {tab === "closed" && (
          closedTrades.length === 0 ? (
            <div className="text-center py-12">
              <Clock size={32} className="mx-auto mb-2 opacity-30" style={{ color: "var(--text-muted)" }} />
              <p className="text-sm" style={{ color: "var(--text-muted)" }}>No closed paper trades yet.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    {["Symbol", "Type", "Entry", "Exit", "Qty", "P&L", "P&L%", "Setup", "Date"].map(h => (
                      <th key={h} className="text-left py-2 px-2 text-[10px] font-bold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {closedTrades.map(t => (
                    <tr key={t._id} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td className="py-3 px-2 font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{t.symbol}</td>
                      <td className="py-3 px-2 text-xs" style={{ color: t.type === "BUY" ? "var(--gain)" : "var(--loss)" }}>{t.type}</td>
                      <td className="py-3 px-2 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>₹{t.entry_price}</td>
                      <td className="py-3 px-2 font-mono text-xs" style={{ color: "var(--text-secondary)" }}>₹{t.exit_price || "—"}</td>
                      <td className="py-3 px-2 font-mono text-xs" style={{ color: "var(--text-muted)" }}>{t.quantity}</td>
                      <td className="py-3 px-2 font-mono font-semibold" style={{ color: pnlColor(t.pnl) }}>
                        {(t.pnl || 0) >= 0 ? "+" : ""}₹{(t.pnl || 0).toFixed(0)}
                      </td>
                      <td className="py-3 px-2 font-mono text-xs font-semibold" style={{ color: pnlColor(t.pnl_percent) }}>
                        {(t.pnl_percent || 0) >= 0 ? "+" : ""}{(t.pnl_percent || 0).toFixed(2)}%
                      </td>
                      <td className="py-3 px-2 text-[10px]" style={{ color: "var(--text-muted)" }}>{(t.setup_type || "—").replace(/_/g, " ")}</td>
                      <td className="py-3 px-2 text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>{(t.entry_time || "").slice(0, 10)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
      </motion.div>
    </div>
  );
}

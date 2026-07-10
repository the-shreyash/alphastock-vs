import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Link } from "react-router-dom";
import api from "../services/api";
import tradeService, { tradeErrorDetails } from "../services/tradeService";
import brokerService, { brokerErrorMessage } from "../services/brokerService";
import { formatCurrency, formatPercent } from "../utils/formatters";
import { useWebSocket } from "../hooks/useWebSocket";
import { useAuth } from "../context/AuthContext";
import {
  Plus, X, Sparkles, Sliders, BarChart3, Newspaper, ChevronDown, Loader2,
  Target, ShieldAlert, ShieldCheck, TrendingUp, RefreshCw, AlertTriangle,
  CheckCircle2, Ban, Zap,
} from "lucide-react";

// ─── Per-trade math helpers ──────────────────────────────────────────────
// Risk = (entry − stop) × open qty for a long (inverted for a short). Reward
// is the symmetric distance to target1. Both feed the R:R ratio in Details.
function tradeRisk(t) {
  const qty = t.quantity_open ?? t.quantity ?? 0;
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

const ORDER_STATUS_STYLE = {
  FILLED: { background: "var(--gain-bg)", color: "var(--gain)" },
  PARTIALLY_FILLED: { background: "var(--gain-bg)", color: "var(--gain)" },
  OPEN: { background: "var(--ai-accent-soft)", color: "var(--ai-accent)" },
  PENDING: { background: "var(--ai-accent-soft)", color: "var(--ai-accent)" },
  CREATED: { background: "var(--ai-accent-soft)", color: "var(--ai-accent)" },
  CANCELLED: { background: "var(--bg-surface)", color: "var(--text-muted)" },
  EXPIRED: { background: "var(--bg-surface)", color: "var(--text-muted)" },
  REJECTED: { background: "var(--loss-bg)", color: "var(--loss)" },
};

const CANCELLABLE = new Set(["OPEN", "PENDING", "CREATED", "PARTIALLY_FILLED"]);

const inputCls = "w-full rounded-xl px-3 py-2 text-sm font-mono focus:outline-none";
const inputStyle = { background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" };

const EMPTY_FORM = {
  symbol: "", stock_name: "", type: "BUY", entry_price: "", quantity: "",
  stop_loss: "", target1: "", target2: "", target3: "", notes: "",
  trail_enabled: false, trail_type: "percent", trail_value: "",
  broker: "", order_type: "MARKET", auto_exit: false,
};

export default function TradeMonitor() {
  const { user } = useAuth();
  const { tradeUpdates, engineEvents } = useWebSocket(user?._id || user?.id || "");
  const [activeTrades, setActiveTrades] = useState([]);
  const [history, setHistory] = useState([]);
  const [pnl, setPnl] = useState(null);
  const [riskSummary, setRiskSummary] = useState(null);
  const [connectedBrokers, setConnectedBrokers] = useState([]);
  const [showNew, setShowNew] = useState(false);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("active");
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [tips, setTips] = useState({}); // { [tradeId]: "live coaching tip" }
  const [analyzingId, setAnalyzingId] = useState(null);
  const [expandedId, setExpandedId] = useState(null);

  // New-trade form + live risk check
  const [form, setForm] = useState(EMPTY_FORM);
  const [riskCheck, setRiskCheck] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  // Modify modal
  const [modifyTrade, setModifyTrade] = useState(null);
  const [modifyForm, setModifyForm] = useState(null);
  const [modifyError, setModifyError] = useState("");
  const [savingModify, setSavingModify] = useState(false);

  // Close / exit modal
  const [closingTrade, setClosingTrade] = useState(null);
  const [exitPrice, setExitPrice] = useState("");
  const [exitQty, setExitQty] = useState("");
  const [exitError, setExitError] = useState("");
  const [exiting, setExiting] = useState(false);

  // Orders tab
  const [orders, setOrders] = useState(null); // null = not loaded yet
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [ordersError, setOrdersError] = useState("");
  const [orderBusyId, setOrderBusyId] = useState(null);
  const [modifyOrderId, setModifyOrderId] = useState(null);
  const [orderEdit, setOrderEdit] = useState({ price: "", quantity: "" });

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

  // Trading Engine pushes (trailing SL moved / target hit / SL exit) change
  // server-side state — refetch positions + risk usage when one arrives.
  useEffect(() => {
    if (!engineEvents?.length) return;
    fetchActive();
    tradeService.riskSummary().then(setRiskSummary).catch(() => {});
  }, [engineEvents]);

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
          const { tip } = await tradeService.liveTip(t._id);
          return [t._id, tip];
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
      const { tip } = await tradeService.liveTip(t._id);
      setTips((prev) => ({ ...prev, [t._id]: tip }));
    } catch (err) {
      console.error(err);
    } finally {
      setAnalyzingId(null);
    }
  };

  const fetchAll = async () => {
    setLoading(true);
    try {
      const [a, h, p, r] = await Promise.all([
        tradeService.active(),
        tradeService.history(),
        tradeService.pnl(),
        tradeService.riskSummary().catch(() => null),
      ]);
      setActiveTrades(a);
      setHistory(h);
      setPnl(p);
      setRiskSummary(r);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
    // Which brokers can execute? Non-blocking — the form works without any.
    brokerService.status()
      .then((statuses) => setConnectedBrokers(
        Object.values(statuses || {}).filter((s) => s.connected)))
      .catch(() => setConnectedBrokers([]));
  };

  const fetchActive = async () => {
    try {
      setActiveTrades(await tradeService.active());
    } catch {}
  };

  // Default the execution broker to the platform the user chose in Settings
  // (Trading Platform). No choice there → the form starts on "Track only".
  useEffect(() => {
    if (!showNew) return;
    const preferred = user?.preferred_broker;
    if (preferred && connectedBrokers.some((b) => b.broker === preferred)) {
      setForm((f) => (f.broker ? f : { ...f, broker: preferred }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showNew, connectedBrokers]);

  // ─── Live risk check (debounced against /trades/validate) ────────────────
  const validateTimer = useRef(null);
  useEffect(() => {
    if (!showNew) return;
    const entry = parseFloat(form.entry_price);
    const qty = parseInt(form.quantity);
    const sl = parseFloat(form.stop_loss);
    const t1 = parseFloat(form.target1);
    if (!form.symbol || !(entry > 0) || !(qty > 0) || !(sl > 0) || !(t1 > 0)) {
      setRiskCheck(null);
      return;
    }
    clearTimeout(validateTimer.current);
    validateTimer.current = setTimeout(async () => {
      try {
        setRiskCheck(await tradeService.validate(buildTradePayload()));
      } catch {
        setRiskCheck(null); // preview is best-effort; submit re-validates
      }
    }, 450);
    return () => clearTimeout(validateTimer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showNew, form.symbol, form.type, form.entry_price, form.quantity,
      form.stop_loss, form.target1, form.target2, form.target3]);

  const buildTradePayload = () => ({
    symbol: form.symbol.toUpperCase(),
    stock_name: form.stock_name || form.symbol.toUpperCase(),
    type: form.type,
    entry_price: parseFloat(form.entry_price),
    quantity: parseInt(form.quantity),
    stop_loss: parseFloat(form.stop_loss),
    target1: parseFloat(form.target1),
    target2: form.target2 ? parseFloat(form.target2) : null,
    target3: form.target3 ? parseFloat(form.target3) : null,
    trailing_stop: form.trail_enabled && parseFloat(form.trail_value) > 0
      ? { enabled: true, type: form.trail_type, value: parseFloat(form.trail_value) }
      : null,
    notes: form.notes,
    broker: form.broker || null,
    order_type: form.order_type,
    auto_exit: form.broker ? form.auto_exit : false,
    override_warnings: Boolean(riskCheck?.warnings?.length),
  });

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      await tradeService.create(buildTradePayload());
      setShowNew(false);
      setForm(EMPTY_FORM);
      setRiskCheck(null);
      fetchAll();
    } catch (err) {
      setSubmitError(tradeErrorDetails(err));
    } finally {
      setSubmitting(false);
    }
  };

  // ─── Modify (SL / targets / trailing are live-editable, Sprint 9) ────────
  const openModify = (t) => {
    setModifyError("");
    setModifyTrade(t);
    setModifyForm({
      stop_loss: t.stop_loss ?? "",
      target1: t.target1 ?? "",
      target2: t.target2 ?? "",
      target3: t.target3 ?? "",
      trail_enabled: Boolean(t.trailing_stop?.enabled),
      trail_type: t.trailing_stop?.type || "percent",
      trail_value: t.trailing_stop?.value || "",
      notes: t.notes || "",
    });
  };

  const saveModify = async () => {
    if (!modifyTrade || !modifyForm) return;
    setSavingModify(true);
    setModifyError("");
    try {
      const f = modifyForm;
      await tradeService.modify(modifyTrade._id, {
        stop_loss: parseFloat(f.stop_loss),
        target1: parseFloat(f.target1),
        target2: f.target2 ? parseFloat(f.target2) : null,
        target3: f.target3 ? parseFloat(f.target3) : null,
        trailing_stop: f.trail_enabled && parseFloat(f.trail_value) > 0
          ? { enabled: true, type: f.trail_type, value: parseFloat(f.trail_value) }
          : { enabled: false, type: f.trail_type, value: 0 },
        notes: f.notes,
      });
      setModifyTrade(null);
      fetchAll();
    } catch (err) {
      setModifyError(tradeErrorDetails(err, "Could not save changes.").message);
    } finally {
      setSavingModify(false);
    }
  };

  // ─── Exit (manual price, partial quantity, or live market via broker) ────
  const openClose = (t) => {
    setExitError("");
    setClosingTrade(t);
    setExitPrice(t.current_price ?? t.entry_price ?? "");
    setExitQty(String(t.quantity_open ?? t.quantity ?? ""));
  };

  const submitExit = async (atMarket = false) => {
    if (!closingTrade) return;
    setExiting(true);
    setExitError("");
    try {
      await tradeService.exit(closingTrade._id, {
        exit_price: exitPrice !== "" ? parseFloat(exitPrice) : null,
        quantity: exitQty !== "" ? parseInt(exitQty) : null,
        at_market: atMarket,
      });
      setClosingTrade(null);
      setExitPrice("");
      setExitQty("");
      fetchAll();
    } catch (err) {
      setExitError(tradeErrorDetails(err, "Exit failed. Please retry.").message);
    } finally {
      setExiting(false);
    }
  };

  // ─── Orders tab (unified order history across brokers) ───────────────────
  const fetchOrders = async (refresh = false) => {
    setOrdersLoading(true);
    setOrdersError("");
    try {
      const data = await tradeService.orders({ refresh });
      setOrders(data.orders || []);
      const errs = Object.entries(data.sync_errors || {});
      if (errs.length) setOrdersError(errs.map(([b, m]) => `${b}: ${m}`).join(" · "));
    } catch (err) {
      setOrdersError(brokerErrorMessage(err, "Could not load order history."));
    } finally {
      setOrdersLoading(false);
    }
  };

  useEffect(() => {
    if (tab === "orders" && orders === null) fetchOrders(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const cancelOrder = async (order) => {
    setOrderBusyId(order.order_id);
    try {
      await brokerService.cancelOrder(order.broker, order.order_id);
      await fetchOrders(true);
    } catch (err) {
      setOrdersError(brokerErrorMessage(err, "Cancel failed."));
    } finally {
      setOrderBusyId(null);
    }
  };

  const saveOrderModify = async (order) => {
    setOrderBusyId(order.order_id);
    try {
      const changes = {};
      if (orderEdit.price !== "") changes.price = parseFloat(orderEdit.price);
      if (orderEdit.quantity !== "") changes.quantity = parseInt(orderEdit.quantity);
      await brokerService.modifyOrder(order.broker, order.order_id, changes);
      setModifyOrderId(null);
      await fetchOrders(true);
    } catch (err) {
      setOrdersError(brokerErrorMessage(err, "Modify failed."));
    } finally {
      setOrderBusyId(null);
    }
  };

  const targetLevels = (t) =>
    [1, 2, 3].filter((n) => t[`target${n}`] != null && t[`target${n}`] !== 0);
  const hitLevels = (t) => new Set((t.targets_hit || []).map((h) => h.level));

  return (
    <div data-testid="trades-page" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">Trade Monitor</h1>
          <p className="page-subtitle mt-0.5">Track your active positions, orders and trade history</p>
        </div>
        <button data-testid="new-trade-btn" onClick={() => { setSubmitError(null); setShowNew(true); }} className="btn-primary btn-lg">
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

      {/* Risk Manager strip — today's usage vs the user's own limits */}
      {riskSummary && (
        <div data-testid="risk-summary" className="glass-card p-4">
          <div className="flex items-center gap-2 mb-3">
            <ShieldCheck size={15} style={{ color: "var(--ai-accent)" }} />
            <span className="card-subtitle font-semibold" style={{ color: "var(--text-primary)" }}>Risk Manager</span>
            {riskSummary.trading_halted && (
              <span className="px-2 py-0.5 text-[10px] font-bold rounded-lg flex items-center gap-1" style={{ background: "var(--loss-bg)", color: "var(--loss)" }}>
                <Ban size={11} /> DAILY LOSS LIMIT HIT — TRADING PAUSED
              </span>
            )}
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div>
              <span className="text-muted">Trades Today</span>
              <div className="font-mono text-primary text-sm">
                {riskSummary.trades_today}{riskSummary.max_trades_per_day ? ` / ${riskSummary.max_trades_per_day}` : ""}
              </div>
            </div>
            <div>
              <span className="text-muted">Loss Budget Left</span>
              <div className="font-mono text-sm" style={{ color: riskSummary.loss_budget_used_pct >= 80 ? "var(--loss)" : "var(--text-primary)" }}>
                {riskSummary.loss_budget_remaining != null ? formatCurrency(riskSummary.loss_budget_remaining) : "—"}
                {riskSummary.max_daily_loss ? <span className="text-[10px] text-muted ml-1">({riskSummary.loss_budget_used_pct}% used)</span> : null}
              </div>
            </div>
            <div>
              <span className="text-muted">Open Risk</span>
              <div className="font-mono text-loss text-sm">
                {formatCurrency(riskSummary.open_risk)}
                {riskSummary.capital ? <span className="text-[10px] ml-1">({riskSummary.open_risk_pct_of_capital}% of capital)</span> : null}
              </div>
            </div>
            <div>
              <span className="text-muted">Open Exposure</span>
              <div className="font-mono text-primary text-sm">{formatCurrency(riskSummary.open_exposure)}</div>
            </div>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="tab-bar w-fit">
        {["active", "orders", "history"].map((t) => (
          <button
            key={t}
            data-testid={`tab-${t}`}
            onClick={() => setTab(t)}
            className={`tab-btn ${tab === t ? "active" : ""}`}
          >
            {t === "active" ? `Active (${activeTrades.length})`
              : t === "orders" ? "Orders"
              : `History (${history.length})`}
          </button>
        ))}
      </div>

      {/* Active Trades */}
      {tab === "active" && (
        <div className="space-y-1.5">
          {loading ? (
            <div className="glass-card p-8 flex items-center justify-center gap-2 text-muted text-sm">
              <Loader2 size={16} className="animate-spin" /> Loading positions…
            </div>
          ) : activeTrades.length === 0 ? (
            <div className="glass-card p-8 text-center">
              <p className="text-muted text-sm">No active trades. Click "New Trade" to get started.</p>
            </div>
          ) : (
            activeTrades.map((trade, i) => {
              const risk = tradeRisk(trade);
              const { nearTarget, nearSL } = proximityFlags(trade);
              const qtyOpen = trade.quantity_open ?? trade.quantity;
              const invested = (trade.entry_price || 0) * (qtyOpen || 0);
              const expanded = expandedId === trade._id;
              const levels = targetLevels(trade);
              const hits = hitLevels(trade);
              const trailing = trade.trailing_stop?.enabled;
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
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className={`px-1.5 py-0.5 text-[10px] font-mono rounded-xl ${trade.type === "BUY" ? "bg-gain/10 text-gain border border-gain/30" : "bg-loss/10 text-loss border border-loss/30"}`}>
                      {trade.type}
                    </span>
                    <div>
                      <span className="card-subtitle font-semibold" style={{ color: "var(--text-primary)" }}>{trade.stock_name}</span>
                      <span className="text-xs text-muted ml-2 font-mono">{trade.symbol}</span>
                    </div>
                    {trade.broker && (
                      <span data-testid={`broker-badge-${trade.symbol}`} className="px-1.5 py-0.5 text-[10px] font-semibold rounded-lg flex items-center gap-1" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>
                        <Zap size={10} /> {trade.broker}{trade.auto_exit ? " · auto-exit" : ""}
                      </span>
                    )}
                    {trailing && (
                      <span data-testid={`trailing-badge-${trade.symbol}`} className="px-1.5 py-0.5 text-[10px] font-semibold rounded-lg flex items-center gap-1" style={{ background: "var(--gain-bg)", color: "var(--gain)" }}>
                        <TrendingUp size={10} /> TSL {trade.trailing_stop.value}{trade.trailing_stop.type === "percent" ? "%" : " pts"}
                      </span>
                    )}
                    {/* Multi-target progress */}
                    {levels.length > 0 && (
                      <span className="flex items-center gap-1">
                        {levels.map((n) => (
                          <span key={n} className="px-1.5 py-0.5 text-[10px] font-mono rounded-lg" style={hits.has(n)
                            ? { background: "var(--gain-bg)", color: "var(--gain)" }
                            : { background: "var(--bg-surface)", color: "var(--text-muted)", border: "1px solid var(--border)" }}>
                            T{n}{hits.has(n) ? " ✓" : ""}
                          </span>
                        ))}
                      </span>
                    )}
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
                    {trade.realized_pnl ? (
                      <div className="text-[10px] font-mono text-muted">
                        booked {trade.realized_pnl >= 0 ? "+" : ""}{formatCurrency(trade.realized_pnl)}
                      </div>
                    ) : null}
                  </div>
                </div>
                <div className="grid grid-cols-3 md:grid-cols-6 gap-2 text-xs">
                  <div><span className="text-muted">Entry</span><div className="font-mono text-primary">{formatCurrency(trade.entry_price)}</div></div>
                  <div><span className="text-muted">Current</span><div className="font-mono text-primary">{formatCurrency(trade.current_price)}</div></div>
                  <div><span className="text-muted">SL{trailing ? " (trailing)" : ""}</span><div className="font-mono text-loss">{formatCurrency(trade.stop_loss)}</div></div>
                  <div><span className="text-muted">Target</span><div className="font-mono text-gain">{formatCurrency(trade.target1)}</div></div>
                  <div><span className="text-muted">Qty Open</span><div className="font-mono text-primary">{qtyOpen}{qtyOpen !== trade.quantity ? ` / ${trade.quantity}` : ""}</div></div>
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

                {/* Expandable details + risk math + engine timeline */}
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
                        <div><span className="text-muted">Invested (open)</span><div className="font-mono text-primary">{formatCurrency(invested)}</div></div>
                        <div><span className="text-muted">Target 2</span><div className="font-mono text-gain">{trade.target2 ? formatCurrency(trade.target2) : "—"}</div></div>
                        <div><span className="text-muted">Target 3</span><div className="font-mono text-gain">{trade.target3 ? formatCurrency(trade.target3) : "—"}</div></div>
                        <div><span className="text-muted">Entry Time</span><div className="font-mono text-primary">{trade.entry_time ? new Date(trade.entry_time).toLocaleDateString() : "—"}</div></div>
                        <div><span className="text-muted">Initial SL</span><div className="font-mono text-loss">{trade.initial_stop_loss ? formatCurrency(trade.initial_stop_loss) : formatCurrency(trade.stop_loss)}</div></div>
                        <div><span className="text-muted">Total Risk</span><div className="font-mono text-loss">{formatCurrency(Math.abs(risk.riskAmt))} ({risk.riskPct.toFixed(2)}%)</div></div>
                        <div><span className="text-muted">Reward @ T1</span><div className="font-mono text-gain">{formatCurrency(Math.abs(risk.rewardAmt))}</div></div>
                        <div><span className="text-muted">Risk : Reward</span><div className="font-mono text-primary">1 : {risk.rr.toFixed(2)}</div></div>
                      </div>
                      {(trade.events || []).length > 0 && (
                        <div data-testid={`events-${trade.symbol}`} className="mt-2 p-2.5 rounded-xl text-[11px] space-y-1" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                          <span className="text-muted font-semibold block mb-1">Engine Timeline</span>
                          {[...trade.events].slice(-5).reverse().map((ev, idx) => (
                            <div key={idx} className="flex items-start gap-2">
                              <span className="font-mono text-[9px] px-1 py-0.5 rounded shrink-0" style={{ background: "var(--bg-elevated)", color: "var(--ai-accent)" }}>{ev.type}</span>
                              <span style={{ color: "var(--text-secondary)" }}>{ev.message}</span>
                            </div>
                          ))}
                        </div>
                      )}
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
                    {trade.type === "SELL" ? "Cover / Close" : "Sell / Close"}
                  </button>
                </div>
              </motion.div>
              );
            })
          )}
        </div>
      )}

      {/* Orders — unified order history across brokers */}
      {tab === "orders" && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted">Order book across every connected broker. Pending orders can be modified or cancelled.</p>
            <button data-testid="refresh-orders-btn" onClick={() => fetchOrders(true)} disabled={ordersLoading} className="btn-ghost btn-sm">
              <RefreshCw size={13} className={ordersLoading ? "animate-spin" : ""} /> Refresh
            </button>
          </div>
          {ordersError && (
            <div data-testid="orders-error" className="glass-card p-3 flex items-center gap-2 text-xs" style={{ color: "var(--loss)" }}>
              <AlertTriangle size={13} /> {ordersError}
            </div>
          )}
          {ordersLoading && orders === null ? (
            <div className="glass-card p-8 flex items-center justify-center gap-2 text-muted text-sm">
              <Loader2 size={16} className="animate-spin" /> Syncing order book…
            </div>
          ) : (orders || []).length === 0 ? (
            <div className="glass-card p-8 text-center">
              <p className="text-muted text-sm">No orders yet. Orders placed via a connected broker appear here.</p>
              <Link to="/settings" className="btn-secondary btn-sm mt-3 inline-flex">Connect a broker</Link>
            </div>
          ) : (
            (orders || []).map((o, i) => {
              const statusStyle = ORDER_STATUS_STYLE[o.status] || ORDER_STATUS_STYLE.PENDING;
              const cancellable = CANCELLABLE.has(o.status);
              const editing = modifyOrderId === o.order_id;
              const busy = orderBusyId === o.order_id;
              return (
                <motion.div
                  key={`${o.broker}-${o.order_id}`}
                  data-testid={`order-${o.order_id}`}
                  className="glass-card p-3"
                  initial={{ opacity: 0, y: 12 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, margin: "-40px" }}
                  transition={{ duration: 0.3, delay: Math.min(i, 10) * 0.03 }}
                >
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-3">
                      <span className="px-1.5 py-0.5 text-[10px] font-mono rounded-lg" style={statusStyle}>{o.status}</span>
                      <span className={`px-1.5 py-0.5 text-[10px] font-mono rounded-xl ${o.transaction_type === "BUY" ? "bg-gain/10 text-gain" : "bg-loss/10 text-loss"}`}>{o.transaction_type}</span>
                      <div>
                        <span className="card-subtitle font-semibold" style={{ color: "var(--text-primary)" }}>{o.symbol}</span>
                        <span className="text-[10px] text-muted ml-2 font-mono uppercase">{o.broker} · {o.order_type}{o.product ? ` · ${o.product}` : ""}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-4 text-xs">
                      <div><span className="text-muted">Qty</span><div className="font-mono text-primary">{o.filled_quantity ?? 0}/{o.quantity}</div></div>
                      <div><span className="text-muted">Price</span><div className="font-mono text-primary">{o.average_price ? formatCurrency(o.average_price) : o.price ? formatCurrency(o.price) : "MKT"}</div></div>
                      <div><span className="text-muted">Placed</span><div className="font-mono text-primary">{o.placed_at ? new Date(o.placed_at).toLocaleTimeString() : "—"}</div></div>
                      {cancellable && (
                        <div className="flex items-center gap-1.5">
                          <button
                            data-testid={`modify-order-${o.order_id}`}
                            onClick={() => { setModifyOrderId(editing ? null : o.order_id); setOrderEdit({ price: o.price ?? "", quantity: o.pending_quantity ?? o.quantity ?? "" }); }}
                            className="btn-ghost btn-sm" disabled={busy}
                          >
                            <Sliders size={12} /> Modify
                          </button>
                          <button
                            data-testid={`cancel-order-${o.order_id}`}
                            onClick={() => cancelOrder(o)}
                            className="btn-ghost btn-sm" style={{ color: "var(--loss)" }} disabled={busy}
                          >
                            {busy ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />} Cancel
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                  {o.status === "REJECTED" && o.status_message && (
                    <p className="text-[11px] mt-1.5" style={{ color: "var(--loss)" }}>{o.status_message}</p>
                  )}
                  {editing && (
                    <div className="flex items-end gap-2 mt-2 p-2.5 rounded-xl" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                      <div>
                        <label className="stat-label block mb-1">Price</label>
                        <input data-testid={`order-price-input-${o.order_id}`} type="number" step="0.05" value={orderEdit.price} onChange={(e) => setOrderEdit({ ...orderEdit, price: e.target.value })} className={inputCls} style={{ ...inputStyle, width: 110 }} />
                      </div>
                      <div>
                        <label className="stat-label block mb-1">Quantity</label>
                        <input data-testid={`order-qty-input-${o.order_id}`} type="number" value={orderEdit.quantity} onChange={(e) => setOrderEdit({ ...orderEdit, quantity: e.target.value })} className={inputCls} style={{ ...inputStyle, width: 90 }} />
                      </div>
                      <button data-testid={`save-order-${o.order_id}`} onClick={() => saveOrderModify(o)} disabled={busy} className="btn-primary btn-sm">
                        {busy ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle2 size={13} />} Save
                      </button>
                    </div>
                  )}
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
                    {trade.broker && <span className="text-[10px] text-muted ml-2 font-mono uppercase">{trade.broker}</span>}
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
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4 overflow-y-auto" onClick={() => setShowNew(false)}>
          <div className="glass-card w-full max-w-lg my-8" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b" style={{ borderColor: "var(--border)" }}>
              <h3 className="card-title">New Trade Entry</h3>
              <button onClick={() => setShowNew(false)} style={{ color: "var(--text-muted)" }}><X size={18} /></button>
            </div>
            <form onSubmit={handleSubmit} className="p-4 space-y-3">
              {/* Side toggle */}
              <div className="tab-bar w-fit">
                {["BUY", "SELL"].map((side) => (
                  <button key={side} type="button" data-testid={`side-${side}`}
                    onClick={() => setForm({ ...form, type: side })}
                    className={`tab-btn ${form.type === side ? "active" : ""}`}
                    style={form.type === side ? { color: side === "BUY" ? "var(--gain)" : "var(--loss)" } : {}}>
                    {side === "BUY" ? "Buy / Long" : "Sell / Short"}
                  </button>
                ))}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="stat-label block mb-1">Symbol</label>
                  <div className="relative">
                    <input data-testid="trade-symbol-input" value={form.symbol} onChange={(e) => handleSymbolChange(e.target.value)} required className={inputCls} style={inputStyle} placeholder="Type stock name..." autoComplete="off" />
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
                  <input data-testid="trade-name-input" value={form.stock_name} onChange={(e) => setForm({ ...form, stock_name: e.target.value })} required className={inputCls} style={inputStyle} placeholder="Reliance Industries" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="stat-label block mb-1">Entry Price</label>
                  <input data-testid="trade-entry-input" type="number" step="0.01" value={form.entry_price} onChange={(e) => setForm({ ...form, entry_price: e.target.value })} required className={inputCls} style={inputStyle} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Quantity</label>
                  <input data-testid="trade-qty-input" type="number" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} required className={inputCls} style={inputStyle} />
                </div>
              </div>
              <div className="grid grid-cols-4 gap-3">
                <div>
                  <label className="stat-label block mb-1">Stop Loss</label>
                  <input data-testid="trade-sl-input" type="number" step="0.01" value={form.stop_loss} onChange={(e) => setForm({ ...form, stop_loss: e.target.value })} required className={inputCls} style={inputStyle} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Target 1</label>
                  <input data-testid="trade-t1-input" type="number" step="0.01" value={form.target1} onChange={(e) => setForm({ ...form, target1: e.target.value })} required className={inputCls} style={inputStyle} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Target 2</label>
                  <input data-testid="trade-t2-input" type="number" step="0.01" value={form.target2} onChange={(e) => setForm({ ...form, target2: e.target.value })} className={inputCls} style={inputStyle} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Target 3</label>
                  <input data-testid="trade-t3-input" type="number" step="0.01" value={form.target3} onChange={(e) => setForm({ ...form, target3: e.target.value })} className={inputCls} style={inputStyle} />
                </div>
              </div>

              {/* Trailing stop */}
              <div className="p-3 rounded-xl space-y-2" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input data-testid="trail-toggle" type="checkbox" checked={form.trail_enabled} onChange={(e) => setForm({ ...form, trail_enabled: e.target.checked })} />
                  <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
                    <TrendingUp size={12} className="inline mr-1" style={{ color: "var(--gain)" }} />
                    Trailing Stop — ratchets the SL toward the best price, never loosens
                  </span>
                </label>
                {form.trail_enabled && (
                  <div className="flex items-center gap-2">
                    <select data-testid="trail-type" value={form.trail_type} onChange={(e) => setForm({ ...form, trail_type: e.target.value })} className={inputCls} style={{ ...inputStyle, width: 120 }}>
                      <option value="percent">Percent</option>
                      <option value="points">Points</option>
                    </select>
                    <input data-testid="trail-value" type="number" step="0.1" min="0" placeholder={form.trail_type === "percent" ? "e.g. 2 (%)" : "e.g. 5 (₹)"} value={form.trail_value} onChange={(e) => setForm({ ...form, trail_value: e.target.value })} className={inputCls} style={{ ...inputStyle, width: 130 }} />
                    <span className="text-[10px] text-muted">below the best price {form.type === "SELL" ? "(above, for shorts)" : ""}</span>
                  </div>
                )}
              </div>

              {/* Broker execution */}
              {connectedBrokers.length > 0 && (
                <div className="p-3 rounded-xl space-y-2" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center gap-2">
                    <Zap size={12} style={{ color: "var(--ai-accent)" }} />
                    <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Execution</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <select data-testid="trade-broker-select" value={form.broker} onChange={(e) => setForm({ ...form, broker: e.target.value })} className={inputCls} style={{ ...inputStyle, width: 180 }}>
                      <option value="">Track only (no order)</option>
                      {connectedBrokers.map((b) => (
                        <option key={b.broker} value={b.broker}>
                          Live order — {b.display_name}{user?.preferred_broker === b.broker ? " (your platform)" : ""}
                        </option>
                      ))}
                    </select>
                    {form.broker && (
                      <select data-testid="trade-ordertype-select" value={form.order_type} onChange={(e) => setForm({ ...form, order_type: e.target.value })} className={inputCls} style={{ ...inputStyle, width: 110 }}>
                        <option value="MARKET">Market</option>
                        <option value="LIMIT">Limit</option>
                      </select>
                    )}
                  </div>
                  {form.broker && (
                    <label className="flex items-start gap-2 cursor-pointer">
                      <input data-testid="auto-exit-toggle" type="checkbox" checked={form.auto_exit} onChange={(e) => setForm({ ...form, auto_exit: e.target.checked })} className="mt-0.5" />
                      <span className="text-[11px] leading-snug" style={{ color: "var(--text-secondary)" }}>
                        <strong style={{ color: "var(--text-primary)" }}>Auto-exit via broker.</strong> I authorize the engine to place LIVE
                        market exit orders on this trade when a stop loss or target is hit. Without this, you'll only be alerted.
                      </span>
                    </label>
                  )}
                </div>
              )}

              {/* Live risk check */}
              {riskCheck && (
                <div data-testid="risk-check-panel" className="p-3 rounded-xl space-y-1.5" style={{ background: riskCheck.approved ? "var(--bg-surface)" : "var(--loss-bg)", border: `1px solid ${riskCheck.approved ? "var(--border)" : "var(--loss)"}` }}>
                  <div className="flex items-center gap-2 text-xs font-semibold" style={{ color: riskCheck.approved ? "var(--gain)" : "var(--loss)" }}>
                    {riskCheck.approved ? <CheckCircle2 size={13} /> : <Ban size={13} />}
                    {riskCheck.approved ? "Risk check passed" : "Risk check failed — trade will be blocked"}
                    {riskCheck.metrics && (
                      <span className="font-mono font-normal text-muted ml-auto">
                        Risk {formatCurrency(riskCheck.metrics.risk_amount)} ({riskCheck.metrics.risk_pct_of_capital}%) · R:R 1:{riskCheck.metrics.risk_reward}
                      </span>
                    )}
                  </div>
                  {riskCheck.violations.map((v, i) => (
                    <p key={i} className="text-[11px] flex items-start gap-1.5" style={{ color: "var(--loss)" }}><Ban size={11} className="shrink-0 mt-0.5" />{v}</p>
                  ))}
                  {riskCheck.warnings.map((w, i) => (
                    <p key={i} className="text-[11px] flex items-start gap-1.5" style={{ color: "var(--warning, #d97706)" }}><AlertTriangle size={11} className="shrink-0 mt-0.5" />{w}</p>
                  ))}
                </div>
              )}

              {/* Submit failure (server-side risk block / broker rejection) */}
              {submitError && (
                <div data-testid="submit-error" className="p-3 rounded-xl text-[11px] space-y-1" style={{ background: "var(--loss-bg)", border: "1px solid var(--loss)", color: "var(--loss)" }}>
                  <p className="font-semibold">{submitError.message}</p>
                  {submitError.violations.map((v, i) => <p key={i}>• {v}</p>)}
                </div>
              )}

              <div>
                <label className="stat-label block mb-1">Notes</label>
                <textarea data-testid="trade-notes-input" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} rows={2} className={`${inputCls} resize-none`} style={inputStyle} />
              </div>
              <button data-testid="submit-trade-btn" type="submit" disabled={submitting || (riskCheck && !riskCheck.approved)} className="btn-primary btn-lg btn-block">
                {submitting ? <Loader2 size={16} className="animate-spin" /> : null}
                {submitting ? "Placing…" : form.broker ? `Place Live ${form.type} Order` : "Execute Trade"}
              </button>
            </form>
          </div>
        </div>
      )}

      {/* Modify Trade Modal — SL / targets / trailing are live-editable */}
      {modifyTrade && modifyForm && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4" onClick={() => setModifyTrade(null)}>
          <div data-testid="modify-modal" className="glass-card w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b" style={{ borderColor: "var(--border)" }}>
              <h3 className="card-title">Modify — {modifyTrade.symbol}</h3>
              <button onClick={() => setModifyTrade(null)} style={{ color: "var(--text-muted)" }}><X size={18} /></button>
            </div>
            <div className="p-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="stat-label block mb-1">Stop Loss</label>
                  <input data-testid="modify-sl-input" type="number" step="0.01" value={modifyForm.stop_loss} onChange={(e) => setModifyForm({ ...modifyForm, stop_loss: e.target.value })} className={inputCls} style={inputStyle} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Target 1</label>
                  <input data-testid="modify-t1-input" type="number" step="0.01" value={modifyForm.target1} onChange={(e) => setModifyForm({ ...modifyForm, target1: e.target.value })} className={inputCls} style={inputStyle} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Target 2</label>
                  <input data-testid="modify-t2-input" type="number" step="0.01" value={modifyForm.target2} onChange={(e) => setModifyForm({ ...modifyForm, target2: e.target.value })} className={inputCls} style={inputStyle} placeholder="—" />
                </div>
                <div>
                  <label className="stat-label block mb-1">Target 3</label>
                  <input data-testid="modify-t3-input" type="number" step="0.01" value={modifyForm.target3} onChange={(e) => setModifyForm({ ...modifyForm, target3: e.target.value })} className={inputCls} style={inputStyle} placeholder="—" />
                </div>
              </div>
              <div className="p-3 rounded-xl space-y-2" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input data-testid="modify-trail-toggle" type="checkbox" checked={modifyForm.trail_enabled} onChange={(e) => setModifyForm({ ...modifyForm, trail_enabled: e.target.checked })} />
                  <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Trailing Stop</span>
                </label>
                {modifyForm.trail_enabled && (
                  <div className="flex items-center gap-2">
                    <select data-testid="modify-trail-type" value={modifyForm.trail_type} onChange={(e) => setModifyForm({ ...modifyForm, trail_type: e.target.value })} className={inputCls} style={{ ...inputStyle, width: 120 }}>
                      <option value="percent">Percent</option>
                      <option value="points">Points</option>
                    </select>
                    <input data-testid="modify-trail-value" type="number" step="0.1" min="0" value={modifyForm.trail_value} onChange={(e) => setModifyForm({ ...modifyForm, trail_value: e.target.value })} className={inputCls} style={{ ...inputStyle, width: 130 }} />
                  </div>
                )}
              </div>
              <div>
                <label className="stat-label block mb-1">Notes</label>
                <textarea data-testid="modify-notes-input" value={modifyForm.notes} onChange={(e) => setModifyForm({ ...modifyForm, notes: e.target.value })} rows={3} className={`${inputCls} resize-none`} style={inputStyle} placeholder="Trade rationale, adjustments, reminders…" />
              </div>
              {modifyError && (
                <p data-testid="modify-error" className="text-[11px] flex items-start gap-1.5" style={{ color: "var(--loss)" }}>
                  <AlertTriangle size={11} className="shrink-0 mt-0.5" /> {modifyError}
                </p>
              )}
              <button data-testid="save-modify-btn" onClick={saveModify} disabled={savingModify} className="btn-primary btn-lg btn-block">
                {savingModify ? <Loader2 size={16} className="animate-spin" /> : null}
                {savingModify ? "Saving…" : "Save Changes"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Close / Exit Trade Modal */}
      {closingTrade && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4" onClick={() => setClosingTrade(null)}>
          <div data-testid="close-modal" className="glass-card w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b" style={{ borderColor: "var(--border)" }}>
              <h3 className="card-title">Exit — {closingTrade.symbol}</h3>
              <button onClick={() => setClosingTrade(null)} style={{ color: "var(--text-muted)" }}><X size={18} /></button>
            </div>
            <div className="p-4 space-y-3">
              <div className="grid grid-cols-3 gap-3 text-xs">
                <div><span className="text-muted">Entry</span><div className="font-mono text-primary">{formatCurrency(closingTrade.entry_price)}</div></div>
                <div><span className="text-muted">Current</span><div className="font-mono text-primary">{formatCurrency(closingTrade.current_price)}</div></div>
                <div><span className="text-muted">Open Qty</span><div className="font-mono text-primary">{closingTrade.quantity_open ?? closingTrade.quantity}</div></div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="stat-label block mb-1">Quantity</label>
                  <input data-testid="exit-qty-input" type="number" min="1" max={closingTrade.quantity_open ?? closingTrade.quantity} value={exitQty} onChange={(e) => setExitQty(e.target.value)} className={inputCls} style={inputStyle} />
                </div>
                <div>
                  <label className="stat-label block mb-1">Exit Price</label>
                  <input data-testid="exit-price-input" type="number" step="0.01" value={exitPrice} onChange={(e) => setExitPrice(e.target.value)} className={inputCls} style={inputStyle} />
                </div>
              </div>
              {exitPrice !== "" && !isNaN(parseFloat(exitPrice)) && (
                <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
                  Realized P&L on this exit:{" "}
                  {(() => {
                    const px = parseFloat(exitPrice);
                    const qty = parseInt(exitQty) || (closingTrade.quantity_open ?? closingTrade.quantity) || 0;
                    const raw = (closingTrade.type === "SELL" ? closingTrade.entry_price - px : px - closingTrade.entry_price) * qty;
                    return <span className="font-mono font-semibold" style={{ color: raw >= 0 ? "var(--gain)" : "var(--loss)" }}>{raw >= 0 ? "+" : ""}{formatCurrency(raw)}</span>;
                  })()}
                </p>
              )}
              {exitError && (
                <p data-testid="exit-error" className="text-[11px] flex items-start gap-1.5" style={{ color: "var(--loss)" }}>
                  <AlertTriangle size={11} className="shrink-0 mt-0.5" /> {exitError}
                </p>
              )}
              <button data-testid="confirm-close-btn" onClick={() => submitExit(false)} disabled={exiting || exitPrice === "" || isNaN(parseFloat(exitPrice))} className="btn-primary btn-lg btn-block">
                {exiting ? <Loader2 size={16} className="animate-spin" /> : null}
                Confirm Exit
              </button>
              {closingTrade.broker && (
                <button data-testid="exit-market-btn" onClick={() => submitExit(true)} disabled={exiting} className="btn-secondary btn-lg btn-block">
                  <Zap size={14} /> Exit at Market via {closingTrade.broker}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

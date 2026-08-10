import { useState, useEffect, useCallback, useMemo, useRef, memo, forwardRef } from "react";
import { Link } from "react-router-dom";
import api from "../services/api";
import { formatNumber } from "../utils/formatters";
import { usePriceFlash } from "../hooks/usePriceFlash";
import { useVirtualList } from "../hooks/useVirtualList";
import {
  useRealtimeStore,
  selectConnected,
  selectWatchlistEvent,
  selectTickForSymbol,
} from "../store/realtimeStore";
import { Eye, Search, Plus, Trash2, ArrowUpRight, ArrowDownRight, Activity } from "lucide-react";
import { motion } from "framer-motion";

// Beyond this many rows the list windows itself (Sprint R9 virtualization);
// smaller lists keep the staggered entrance animation.
const VIRTUALIZE_AFTER = 60;
const ROW_HEIGHT_ESTIMATE = 61;

/* Scroll-reveal wrapper — fades/slides content in as it enters the viewport */
function Reveal({ children, delay = 0, className }) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4, delay, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.div>
  );
}

function AddStockSearch({ onAdd, existing }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!query.trim()) { setResults([]); return; }
    const t = setTimeout(async () => {
      try {
        const { data } = await api.get(`/stocks/search?q=${encodeURIComponent(query)}`);
        setResults(data.slice(0, 8));
        setOpen(true);
      } catch { setResults([]); }
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    const onClick = e => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const handleAdd = async (symbol) => {
    setQuery(""); setResults([]); setOpen(false);
    await onAdd(symbol);
  };

  return (
    <div ref={boxRef} className="relative w-full sm:w-72">
      <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
      <input
        data-testid="watchlist-search-input"
        aria-label="Search for a stock to add to your watchlist"
        type="text"
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => results.length && setOpen(true)}
        placeholder="Add stock to watchlist..."
        className="search-input w-full text-xs"
        style={{ padding: "9px 12px 9px 32px", borderRadius: "10px" }}
      />
      {open && results.length > 0 && (
        <div className="absolute top-full mt-1.5 left-0 right-0 z-20 rounded-xl overflow-hidden" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.25)" }}>
          {results.map(s => {
            const already = existing.includes(s.symbol);
            return (
              <button
                key={s.symbol}
                data-testid={`watchlist-add-${s.symbol}`}
                disabled={already}
                onClick={() => handleAdd(s.symbol)}
                className="w-full flex items-center justify-between px-3 py-2.5 text-left transition-all disabled:opacity-40"
                onMouseEnter={e => !already && (e.currentTarget.style.background = "var(--hover)")}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"}
              >
                <div>
                  <span className="text-[12px] font-semibold block" style={{ color: "var(--text-primary)" }}>{s.symbol}</span>
                  <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>{s.name}</span>
                </div>
                {already
                  ? <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>Added</span>
                  : <Plus size={14} style={{ color: "var(--ai-accent)" }} />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/**
 * One watchlist row (Sprint R9 selective rendering). Memoized and subscribed
 * to ITS OWN symbol tick via the factory selector — combined with the store's
 * tick identity preservation, a price burst re-renders only the rows whose
 * symbols actually moved, instead of the page cloning the whole list.
 */
const WatchlistRow = memo(forwardRef(function WatchlistRow({ item, onRemove }, ref) {
  const tick = useRealtimeStore(
    useMemo(() => selectTickForSymbol(item.symbol), [item.symbol])
  );
  // Live tick fields overlay the REST quote (price/change from the 15s
  // stream; RSI/volume ratio from the 120s watchlist.quotes stream).
  const q = useMemo(() => {
    if (!tick || tick.price == null) return item.quote;
    return { ...(item.quote || {}), ...tick };
  }, [item.quote, tick]);
  // Since-added P&L recomputed from the streamed price when possible.
  const sincePct = item.added_price && q?.price != null
    ? Math.round(((q.price - item.added_price) / item.added_price) * 10000) / 100
    : item.since_added_pct;
  const isPos = (q?.change_pct ?? 0) >= 0;
  const sincePos = (sincePct ?? 0) >= 0;
  const priceFlashRef = usePriceFlash(q?.price);
  return (
    <div ref={ref} className="flex items-center gap-3 px-4 py-3 transition-all" style={{ borderBottom: "1px solid var(--border-subtle)" }}
      onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
      onMouseLeave={e => e.currentTarget.style.background = "transparent"}
    >
      <Link to={`/stock/${item.symbol}`} className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{item.symbol}</span>
          {q?.sector && <span className="text-[9px] px-1.5 py-0.5 rounded hidden sm:inline" style={{ background: "var(--hover)", color: "var(--text-muted)" }}>{q.sector}</span>}
        </div>
        <span className="text-[10px] truncate block" style={{ color: "var(--text-muted)" }}>{q?.name || item.note || ""}</span>
      </Link>

      <div className="text-right w-24 shrink-0">
        <div ref={priceFlashRef} className="text-[13px] font-mono font-semibold inline-block rounded px-1" style={{ color: "var(--text-primary)" }}>
          {q?.price != null ? `₹${formatNumber(q.price)}` : "—"}
        </div>
        {q?.change_pct != null && (
          <div className="flex items-center justify-end gap-0.5 text-[11px] font-mono font-semibold" style={{ color: isPos ? "var(--gain)" : "var(--loss)" }}>
            {isPos ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />}
            {isPos ? "+" : ""}{q.change_pct.toFixed(2)}%
          </div>
        )}
      </div>

      <div className="text-right w-20 shrink-0 hidden md:block">
        <span className="text-[9px] font-medium uppercase block" style={{ color: "var(--text-muted)" }}>RSI</span>
        <span className="text-[12px] font-mono font-semibold" style={{ color: q?.rsi > 70 ? "var(--loss)" : q?.rsi < 30 ? "var(--gain)" : "var(--text-secondary)" }}>
          {q?.rsi != null ? q.rsi.toFixed(0) : "—"}
        </span>
      </div>

      <div className="text-right w-24 shrink-0 hidden lg:block">
        <span className="text-[9px] font-medium uppercase block" style={{ color: "var(--text-muted)" }}>Since Added</span>
        <span className="text-[12px] font-mono font-semibold" style={{ color: sincePos ? "var(--gain)" : "var(--loss)" }}>
          {sincePct != null ? `${sincePos ? "+" : ""}${sincePct.toFixed(2)}%` : "—"}
        </span>
      </div>

      <button
        data-testid={`watchlist-remove-${item.symbol}`}
        aria-label={`Remove ${item.symbol} from watchlist`}
        onClick={() => onRemove(item.symbol)}
        className="btn-ghost shrink-0"
        style={{ padding: "8px", color: "var(--text-muted)" }}
        onMouseEnter={e => { e.currentTarget.style.color = "var(--loss)"; e.currentTarget.style.background = "var(--loss-bg)"; }}
        onMouseLeave={e => { e.currentTarget.style.color = "var(--text-muted)"; e.currentTarget.style.background = "transparent"; }}
        title={`Remove ${item.symbol}`}
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}));

export default function Watchlist() {
  const connected = useRealtimeStore(selectConnected);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchWatchlist = useCallback(async () => {
    try {
      const { data } = await api.get("/watchlist");
      setItems(data);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchWatchlist();
  }, [fetchWatchlist]);

  // Every field is streamed while connected (prices every 15s, RSI/volume via
  // watchlist.quotes) — the refetch poll survives only as a disconnected
  // fallback, per the no-polling architecture.
  useEffect(() => {
    if (connected) return undefined;
    const i = setInterval(fetchWatchlist, 30000);
    return () => clearInterval(i);
  }, [connected, fetchWatchlist]);

  // Live price/RSI/volume patching moved INTO each row (Sprint R9): every
  // WatchlistRow subscribes to its own symbol tick, so a burst re-renders only
  // the rows that moved — the page no longer clones the whole list per tick.

  // Cross-surface sync (Sprint R8): an add/remove made in another tab or the
  // dashboard widget lands here as a per-user watchlist.updated event.
  const watchlistEvent = useRealtimeStore(selectWatchlistEvent);
  useEffect(() => {
    if (!watchlistEvent?.symbol) return;
    const { action, symbol } = watchlistEvent;
    setItems(prev => {
      const present = prev.some(i => i.symbol === symbol);
      if (action === "removed" && present) return prev.filter(i => i.symbol !== symbol);
      if (action === "added" && !present) fetchWatchlist(); // need the full row
      return prev;
    });
  }, [watchlistEvent, fetchWatchlist]);

  const handleAdd = async (symbol) => {
    try {
      await api.post("/watchlist", { symbol });
      await fetchWatchlist();
    } catch (err) { console.error(err); }
  };

  // Stable reference so memoized rows never re-render because of the handler.
  const handleRemove = useCallback(async (symbol) => {
    try {
      await api.delete(`/watchlist/${symbol}`);
      setItems(prev => prev.filter(i => i.symbol !== symbol));
    } catch (err) { console.error(err); }
  }, []);

  // Windowing (Sprint R9): long lists render only the rows near the viewport.
  const virtualized = items.length > VIRTUALIZE_AFTER;
  const vlist = useVirtualList({
    count: items.length,
    estimatedRowHeight: ROW_HEIGHT_ESTIMATE,
    enabled: virtualized,
  });

  if (loading) return (
    <div className="space-y-5 animate-fade-in-up">
      <div className="h-8 w-40 rounded-xl skeleton" />
      <div className="glass-card p-5 space-y-3">
        {[...Array(5)].map((_, i) => <div key={i} className="h-12 skeleton rounded-lg" />)}
      </div>
    </div>
  );

  return (
    <div data-testid="watchlist-page" className="space-y-5">
      {/* Header */}
      <Reveal>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h1 className="page-title">Watchlist</h1>
            <p className="page-subtitle mt-1">
              Stocks you're tracking — prices, RSI and signals stream in live
            </p>
          </div>
          <AddStockSearch onAdd={handleAdd} existing={items.map(i => i.symbol)} />
        </div>
      </Reveal>

      {/* List */}
      {items.length === 0 ? (
        <Reveal>
          <div className="glass-card p-12 text-center">
            <Eye size={32} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
            <h3 className="card-title mb-1">Your watchlist is empty</h3>
            <p className="body-text max-w-sm mx-auto">
              Search for a stock above to start tracking it. The AI keeps an eye on price, momentum, and volume for every stock you add.
            </p>
          </div>
        </Reveal>
      ) : (
        <Reveal>
          <div className="glass-card overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
              <Activity size={13} style={{ color: "var(--ai-accent)" }} />
              <span className="eyebrow">
                Tracking {items.length} {items.length === 1 ? "stock" : "stocks"}
              </span>
            </div>
            {virtualized ? (
              <div
                ref={vlist.containerRef}
                onScroll={vlist.onScroll}
                style={{ maxHeight: 640, overflowY: "auto" }}
                data-testid="watchlist-virtual-scroll"
              >
                <div style={{ paddingTop: vlist.padTop, paddingBottom: vlist.padBottom }}>
                  {items.slice(vlist.start, vlist.end).map((item, i) => (
                    <WatchlistRow
                      key={item.symbol}
                      ref={i === 0 ? vlist.measureRowRef : undefined}
                      item={item}
                      onRemove={handleRemove}
                    />
                  ))}
                </div>
              </div>
            ) : (
              items.map((item, i) => (
                <motion.div
                  key={item.symbol}
                  initial={{ opacity: 0, y: 12 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, margin: "-40px" }}
                  transition={{ duration: 0.35, delay: Math.min(i, 8) * 0.04 }}
                >
                  <WatchlistRow item={item} onRemove={handleRemove} />
                </motion.div>
              ))
            )}
          </div>
        </Reveal>
      )}
    </div>
  );
}

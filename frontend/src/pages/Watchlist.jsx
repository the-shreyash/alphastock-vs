import { useState, useEffect, useCallback, useRef } from "react";
import { Link } from "react-router-dom";
import api from "../services/api";
import { formatNumber } from "../utils/formatters";
import { Eye, Search, Plus, Trash2, ArrowUpRight, ArrowDownRight, Activity } from "lucide-react";

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

function WatchlistRow({ item, onRemove }) {
  const q = item.quote;
  const isPos = (q?.change_pct ?? 0) >= 0;
  const sincePos = (item.since_added_pct ?? 0) >= 0;
  return (
    <div className="flex items-center gap-3 px-4 py-3 transition-all" style={{ borderBottom: "1px solid var(--border-subtle)" }}
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
        <div className="text-[13px] font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
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
          {item.since_added_pct != null ? `${sincePos ? "+" : ""}${item.since_added_pct.toFixed(2)}%` : "—"}
        </span>
      </div>

      <button
        data-testid={`watchlist-remove-${item.symbol}`}
        onClick={() => onRemove(item.symbol)}
        className="p-2 rounded-lg transition-all shrink-0"
        style={{ color: "var(--text-muted)" }}
        onMouseEnter={e => { e.currentTarget.style.color = "var(--loss)"; e.currentTarget.style.background = "var(--loss-bg)"; }}
        onMouseLeave={e => { e.currentTarget.style.color = "var(--text-muted)"; e.currentTarget.style.background = "transparent"; }}
        title={`Remove ${item.symbol}`}
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}

export default function Watchlist() {
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
    const i = setInterval(fetchWatchlist, 30000);
    return () => clearInterval(i);
  }, [fetchWatchlist]);

  const handleAdd = async (symbol) => {
    try {
      await api.post("/watchlist", { symbol });
      await fetchWatchlist();
    } catch (err) { console.error(err); }
  };

  const handleRemove = async (symbol) => {
    try {
      await api.delete(`/watchlist/${symbol}`);
      setItems(prev => prev.filter(i => i.symbol !== symbol));
    } catch (err) { console.error(err); }
  };

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
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight font-display" style={{ color: "var(--text-primary)" }}>Watchlist</h1>
          <p className="text-[13px] mt-0.5" style={{ color: "var(--text-secondary)" }}>
            Stocks you're tracking — live prices refresh every 30 seconds
          </p>
        </div>
        <AddStockSearch onAdd={handleAdd} existing={items.map(i => i.symbol)} />
      </div>

      {/* List */}
      {items.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <Eye size={32} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
          <h3 className="text-[15px] font-semibold mb-1" style={{ color: "var(--text-primary)" }}>Your watchlist is empty</h3>
          <p className="text-[12px] max-w-sm mx-auto" style={{ color: "var(--text-secondary)" }}>
            Search for a stock above to start tracking it. The AI keeps an eye on price, momentum, and volume for every stock you add.
          </p>
        </div>
      ) : (
        <div className="glass-card overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
            <Activity size={13} style={{ color: "var(--ai-accent)" }} />
            <span className="text-[11px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--text-muted)" }}>
              Tracking {items.length} {items.length === 1 ? "stock" : "stocks"}
            </span>
          </div>
          {items.map(item => (
            <WatchlistRow key={item.symbol} item={item} onRemove={handleRemove} />
          ))}
        </div>
      )}
    </div>
  );
}

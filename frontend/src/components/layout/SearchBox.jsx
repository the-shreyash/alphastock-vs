import { Search, Clock, TrendingUp, X, CornerDownLeft } from "lucide-react";
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import api from "../../services/api";

/**
 * SearchBox — Global stock search for the top navigation bar.
 *
 * Features:
 *  - Debounced (~250ms) autocomplete against GET /api/stocks/search?q=
 *    (endpoint returns a plain JSON array of { symbol, name, sector, base_price }).
 *  - Empty+focused state surfaces "Recent" (localStorage) and "Popular" stocks.
 *  - Full keyboard support (Arrow Up/Down, Enter, Escape) + click-outside close.
 *  - Selecting a result navigates to /stock/{SYMBOL} and records it in recents.
 *
 * Kept as a standalone component so the Navbar stays declarative and the
 * search logic (network + local state) lives in one reusable place.
 */

const RECENT_KEY = "ap-recent-searches";
const MAX_RECENT = 5;

// A small static list of liquid NSE large-caps used as the "Popular" fallback
// when the user focuses the empty input. Mirrors the backend STOCK_UNIVERSE.
const POPULAR = [
  { symbol: "RELIANCE", name: "Reliance Industries", sector: "Oil & Gas" },
  { symbol: "TCS", name: "Tata Consultancy Services", sector: "IT" },
  { symbol: "HDFCBANK", name: "HDFC Bank", sector: "Banking" },
  { symbol: "INFY", name: "Infosys", sector: "IT" },
  { symbol: "ICICIBANK", name: "ICICI Bank", sector: "Banking" },
];

// Sector → accent colour for the monogram avatar. Every sector in the backend
// universe is mapped; unknown sectors fall back to a stable hashed palette hue.
const SECTOR_COLORS = {
  Banking: "#6366F1",
  Finance: "#4F46E5",
  IT: "#0EA5E9",
  Pharma: "#10B981",
  Auto: "#F59E0B",
  FMCG: "#EC4899",
  "Oil & Gas": "#F43F5E",
  Metals: "#8B5CF6",
  Power: "#EAB308",
  Telecom: "#14B8A6",
  Infrastructure: "#F97316",
  Consumer: "#D946EF",
  Conglomerate: "#A855F7",
  Mining: "#84CC16",
};
const HEX_PALETTE = [
  "#6366F1", "#0EA5E9", "#10B981", "#F59E0B", "#EC4899", "#F43F5E",
  "#8B5CF6", "#14B8A6", "#F97316", "#84CC16", "#A855F7", "#EAB308",
];

function sectorColor(sector) {
  if (sector && SECTOR_COLORS[sector]) return SECTOR_COLORS[sector];
  const s = sector || "?";
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return HEX_PALETTE[h % HEX_PALETTE.length];
}

function loadRecent() {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.slice(0, MAX_RECENT) : [];
  } catch {
    return [];
  }
}

function Monogram({ symbol, sector }) {
  const color = sectorColor(sector);
  return (
    <div
      className="flex items-center justify-center font-display font-bold shrink-0"
      style={{
        width: 34,
        height: 34,
        borderRadius: 10,
        background: `${color}22`,
        color,
        fontSize: 14,
      }}
      aria-hidden="true"
    >
      {symbol?.[0]?.toUpperCase() || "?"}
    </div>
  );
}

export default function SearchBox() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [recent, setRecent] = useState(loadRecent);
  const [activeIndex, setActiveIndex] = useState(-1);

  const containerRef = useRef(null);
  const inputRef = useRef(null);
  const activeItemRef = useRef(null);
  const seqRef = useRef(0);

  const trimmed = query.trim();
  const showingResults = trimmed.length >= 1;

  // Build the sections that render inside the dropdown. When the user has typed,
  // we show live results; otherwise we surface Recent + Popular.
  const sections = useMemo(() => {
    if (showingResults) {
      return [{ key: "results", label: null, items: results }];
    }
    const out = [];
    if (recent.length) out.push({ key: "recent", label: "Recent", items: recent });
    const popular = POPULAR.filter((p) => !recent.some((r) => r.symbol === p.symbol));
    if (popular.length) out.push({ key: "popular", label: "Popular", items: popular });
    return out;
  }, [showingResults, results, recent]);

  // Flattened, ordered list mirroring render order — drives keyboard navigation.
  const flat = useMemo(() => sections.flatMap((s) => s.items), [sections]);

  // Debounced autocomplete. A monotonic seq guard drops stale (out-of-order)
  // responses so fast typing never renders results for an older query.
  useEffect(() => {
    if (!trimmed) {
      setResults([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    const seq = ++seqRef.current;
    const timer = setTimeout(async () => {
      try {
        const res = await api.get("/stocks/search", { params: { q: trimmed } });
        if (seq !== seqRef.current) return;
        const data = Array.isArray(res.data)
          ? res.data
          : res.data?.results || res.data?.stocks || [];
        setResults(data);
      } catch {
        if (seq === seqRef.current) setResults([]);
      } finally {
        if (seq === seqRef.current) setLoading(false);
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [trimmed]);

  // Reset the highlighted row whenever the visible list changes.
  useEffect(() => {
    setActiveIndex(-1);
  }, [query, open]);

  // Keep the highlighted row visible when navigating with the keyboard.
  useEffect(() => {
    if (activeIndex >= 0) activeItemRef.current?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  // Close on outside click.
  useEffect(() => {
    function onDocClick(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const persistRecent = useCallback((stock) => {
    const entry = {
      symbol: stock.symbol.toUpperCase(),
      name: stock.name || "",
      sector: stock.sector || "",
    };
    setRecent((prev) => {
      const next = [entry, ...prev.filter((r) => r.symbol !== entry.symbol)].slice(0, MAX_RECENT);
      try {
        localStorage.setItem(RECENT_KEY, JSON.stringify(next));
      } catch {
        /* ignore quota / privacy-mode errors */
      }
      return next;
    });
  }, []);

  const selectStock = useCallback(
    (stock) => {
      if (!stock || !stock.symbol) return;
      const sym = stock.symbol.trim().toUpperCase();
      if (!sym) return;
      persistRecent(stock);
      setQuery("");
      setResults([]);
      setOpen(false);
      setActiveIndex(-1);
      inputRef.current?.blur();
      navigate(`/stock/${sym}`);
    },
    [navigate, persistRecent]
  );

  const clearRecent = useCallback(() => {
    setRecent([]);
    try {
      localStorage.removeItem(RECENT_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const onKeyDown = (e) => {
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        if (!open) {
          setOpen(true);
          return;
        }
        setActiveIndex((i) => (flat.length ? Math.min(i + 1, flat.length - 1) : -1));
        break;
      case "ArrowUp":
        e.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
        break;
      case "Enter":
        e.preventDefault();
        if (activeIndex >= 0 && flat[activeIndex]) {
          selectStock(flat[activeIndex]);
        } else if (trimmed) {
          // No row highlighted: open the best match, or the raw typed symbol.
          selectStock(flat[0] || { symbol: trimmed });
        }
        break;
      case "Escape":
        e.preventDefault();
        setOpen(false);
        inputRef.current?.blur();
        break;
      default:
        break;
    }
  };

  const showDropdown = open;
  let runningIndex = 0;

  return (
    <div ref={containerRef} className="relative hidden sm:block w-full max-w-md">
      <Search
        size={15}
        className="absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none"
        style={{ color: "var(--text-muted)" }}
      />
      <input
        ref={inputRef}
        data-testid="navbar-search-input"
        type="text"
        role="combobox"
        aria-expanded={showDropdown}
        aria-controls="search-results-dropdown"
        aria-autocomplete="list"
        placeholder="Search stocks by symbol or name..."
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          if (!open) setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        className="search-input"
        style={{ paddingLeft: 40, paddingRight: query ? 36 : 16 }}
        autoComplete="off"
        spellCheck={false}
      />
      {query && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => {
            setQuery("");
            setResults([]);
            inputRef.current?.focus();
          }}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 p-1 rounded-md transition-colors"
          style={{ color: "var(--text-muted)" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--hover)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <X size={14} />
        </button>
      )}

      <AnimatePresence>
        {showDropdown && (
          <motion.div
            id="search-results-dropdown"
            data-testid="search-results-dropdown"
            role="listbox"
            initial={{ opacity: 0, y: -6, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.985 }}
            transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
            className="absolute left-0 right-0 mt-2 overflow-hidden z-50"
            style={{
              background: "var(--bg-card-glass)",
              backdropFilter: "blur(20px) saturate(160%)",
              WebkitBackdropFilter: "blur(20px) saturate(160%)",
              border: "1px solid var(--border)",
              borderRadius: 14,
              boxShadow: "var(--card-shadow-hover)",
            }}
          >
            <div className="max-h-[380px] overflow-y-auto py-1.5">
              {/* Loading state */}
              {showingResults && loading && (
                <div className="px-4 py-6 text-center caption">Searching…</div>
              )}

              {/* Empty result state */}
              {showingResults && !loading && results.length === 0 && (
                <div className="px-4 py-6 text-center">
                  <div className="caption" style={{ color: "var(--text-secondary)" }}>
                    No matches for "{trimmed}"
                  </div>
                  <div className="caption mt-1">Press Enter to open {trimmed.toUpperCase()}</div>
                </div>
              )}

              {/* Empty state with nothing to suggest */}
              {!showingResults && sections.length === 0 && (
                <div className="px-4 py-6 text-center caption">
                  Start typing to search stocks
                </div>
              )}

              {/* Sections */}
              {(!showingResults || (!loading && results.length > 0)) &&
                sections.map((section) => (
                  <div key={section.key} className="py-0.5">
                    {section.label && (
                      <div className="flex items-center justify-between px-4 pt-2 pb-1">
                        <span className="eyebrow flex items-center gap-1.5">
                          {section.key === "recent" ? (
                            <Clock size={11} />
                          ) : (
                            <TrendingUp size={11} />
                          )}
                          {section.label}
                        </span>
                        {section.key === "recent" && (
                          <button
                            type="button"
                            onClick={clearRecent}
                            className="caption transition-colors"
                            style={{ color: "var(--text-muted)" }}
                            onMouseEnter={(e) => (e.currentTarget.style.color = "var(--text-secondary)")}
                            onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-muted)")}
                          >
                            Clear
                          </button>
                        )}
                      </div>
                    )}

                    {section.items.map((item) => {
                      const idx = runningIndex++;
                      const isActive = idx === activeIndex;
                      return (
                        <button
                          key={`${section.key}-${item.symbol}`}
                          ref={isActive ? activeItemRef : null}
                          type="button"
                          role="option"
                          aria-selected={isActive}
                          data-testid={`search-result-${item.symbol}`}
                          onMouseEnter={() => setActiveIndex(idx)}
                          onMouseDown={(e) => e.preventDefault() /* keep focus */}
                          onClick={() => selectStock(item)}
                          className="w-full flex items-center gap-3 px-3 py-2 mx-1 text-left rounded-xl transition-colors"
                          style={{
                            width: "calc(100% - 8px)",
                            background: isActive ? "var(--hover)" : "transparent",
                          }}
                        >
                          <Monogram symbol={item.symbol} sector={item.sector} />
                          <div className="min-w-0 flex-1">
                            <div
                              className="text-[13.5px] font-semibold truncate"
                              style={{ color: "var(--text-primary)" }}
                            >
                              {item.symbol}
                            </div>
                            {item.name && (
                              <div className="caption truncate">{item.name}</div>
                            )}
                          </div>
                          {item.sector && (
                            <span
                              className="caption shrink-0 px-2 py-0.5 rounded-full"
                              style={{
                                background: "var(--bg-surface)",
                                border: "1px solid var(--border-subtle)",
                                color: "var(--text-secondary)",
                              }}
                            >
                              {item.sector}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                ))}
            </div>

            {/* Keyboard hint footer */}
            {flat.length > 0 && (
              <div
                className="flex items-center gap-3 px-4 py-2 caption"
                style={{ borderTop: "1px solid var(--border-subtle)" }}
              >
                <span className="flex items-center gap-1">
                  <kbd className="kbd-hint">↑</kbd>
                  <kbd className="kbd-hint">↓</kbd>
                  to navigate
                </span>
                <span className="flex items-center gap-1">
                  <kbd className="kbd-hint">
                    <CornerDownLeft size={10} />
                  </kbd>
                  to open
                </span>
                <span className="flex items-center gap-1">
                  <kbd className="kbd-hint">esc</kbd>
                  to close
                </span>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

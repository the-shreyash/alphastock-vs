import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import api from "../services/api";
import { Newspaper, ExternalLink, RefreshCw, Search, TrendingUp, TrendingDown, Globe, AlertCircle, Tag, Building2 } from "lucide-react";
import { useRealtimeStore, selectNews, selectConnected } from "../store/realtimeStore";

const NEWS_TABS = ["All", "Market", "Economy", "FOSS", "Global"];
const IMPORTANCE_COLORS = {
  high: { bg: "var(--loss-bg)", color: "var(--loss)" },
  medium: { bg: "var(--hover)", color: "var(--text-muted)" },
  low: { bg: "var(--hover)", color: "var(--text-muted)" },
};

function SentimentBadge({ sentiment }) {
  if (!sentiment) return null;
  const isPos = sentiment?.toLowerCase() === "positive" || sentiment?.toLowerCase() === "bullish";
  const isNeg = sentiment?.toLowerCase() === "negative" || sentiment?.toLowerCase() === "bearish";
  return (
    <span className="badge-status text-[9px]" style={{
      background: isPos ? "var(--gain-bg)" : isNeg ? "var(--loss-bg)" : "var(--hover)",
      color: isPos ? "var(--gain)" : isNeg ? "var(--loss)" : "var(--text-muted)",
    }}>
      {isPos ? <TrendingUp size={9} /> : isNeg ? <TrendingDown size={9} /> : null} {sentiment}
    </span>
  );
}

export default function News() {
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [activeTab, setActiveTab] = useState("All");
  const [selectedArticle, setSelectedArticle] = useState(null);
  const [sentiment, setSentiment] = useState(null);
  const [sentimentLoading, setSentimentLoading] = useState(true);

  const liveNews = useRealtimeStore(selectNews);
  const connected = useRealtimeStore(selectConnected);

  useEffect(() => { fetchNews(); }, []);

  // Live stream (Sprint R8): headlines pushed by news.received / news.breaking
  // merge into the list as they arrive — breaking items land on top, the rest
  // keep their published order. No refresh needed while connected.
  useEffect(() => {
    if (!liveNews?.length) return;
    setArticles((prev) => {
      const seen = new Set(prev.map((a) => (a.title || "").toLowerCase()));
      const fresh = liveNews.filter((a) => !seen.has((a.title || "").toLowerCase()));
      if (!fresh.length) return prev;
      // Newest first within the fresh batch; breaking items lead it.
      fresh.sort((a, b) =>
        (b.is_breaking ? 1 : 0) - (a.is_breaking ? 1 : 0) ||
        (b.published || "").localeCompare(a.published || ""));
      return [...fresh, ...prev].slice(0, 100);
    });
  }, [liveNews]);

  const fetchNews = async (force = false) => {
    setLoading(true);
    setSentimentLoading(true);
    try {
      const url = force ? "/news/refresh" : "/news";
      const { data } = await api.get(url);
      setArticles(data.articles || []);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
    try {
      const { data } = await api.get("/news/sentiment");
      setSentiment(data);
    } catch (err) {
      console.error(err);
      setSentiment(null);
    } finally { setSentimentLoading(false); }
  };

  const filtered = articles.filter(a => {
    const matchesSearch = !filter || (a.title + a.summary + a.source).toLowerCase().includes(filter.toLowerCase());
    const matchesTab = activeTab === "All" || (a.category || "market").toLowerCase() === activeTab.toLowerCase();
    return matchesSearch && matchesTab;
  });

  const sources = [...new Set(articles.map(a => a.source))];

  return (
    <div data-testid="news-page" className="space-y-6">
      {/* Header */}
      <motion.div className="flex items-center justify-between"
        initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="page-title">Market Intelligence</h1>
            {connected && (
              <span className="flex items-center gap-1.5 text-[10px] font-semibold px-2 py-1 rounded-full"
                style={{ background: "var(--gain-bg)", color: "var(--gain)" }} data-testid="news-live-badge">
                <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: "var(--gain)" }} />
                LIVE
              </span>
            )}
          </div>
          <p className="page-subtitle mt-1">Breaking headlines stream in as they happen</p>
        </div>
        <button data-testid="refresh-news-btn" onClick={() => fetchNews(true)}
          className="btn-ghost btn-sm" style={{ color: "var(--text-muted)" }}>
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} /> Refresh
        </button>
      </motion.div>

      {/* Tab Bar */}
      <div className="tab-bar">
        {NEWS_TABS.map(t => (
          <button key={t} onClick={() => setActiveTab(t)} className={`tab-btn ${activeTab === t ? "active" : ""}`}>{t}</button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Left: News Feed */}
        <div className="lg:col-span-2 space-y-4">
          {/* Search */}
          <div className="relative">
            <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
            <input data-testid="news-search-input" value={filter} onChange={e => setFilter(e.target.value)} placeholder="Search news..."
              className="search-input" style={{ paddingLeft: "40px" }} />
          </div>

          {/* Articles */}
          {loading ? (
            <div className="space-y-3">
              {[1, 2, 3, 4, 5].map(i => <div key={i} className="h-24 rounded-xl skeleton" />)}
            </div>
          ) : filtered.length === 0 ? (
            <div className="glass-card p-12 text-center">
              <Newspaper size={32} className="mx-auto mb-3 opacity-30" style={{ color: "var(--text-muted)" }} />
              <p style={{ color: "var(--text-muted)" }}>No news found</p>
            </div>
          ) : (
            <div className="space-y-2.5">
              {filtered.map((article, i) => (
                <motion.div key={i} data-testid={`news-article-${i}`}
                  className="glass-card p-4 cursor-pointer transition-all hover:-translate-y-px"
                  onClick={() => setSelectedArticle(article)}
                  initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, margin: "-40px" }} transition={{ duration: 0.35, delay: Math.min(i, 8) * 0.04 }}>
                  <div className="flex items-center gap-2 mb-2 flex-wrap">
                    <span className="badge-status" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>{article.source}</span>
                    <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
                      {article.published ? new Date(article.published).toLocaleString("en-IN", { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" }) : ""}
                    </span>
                    {article.sentiment && <SentimentBadge sentiment={article.sentiment} />}
                    {article.importance === "high" && (
                      <span className="text-[8px] font-bold px-1.5 py-0.5 rounded" style={{ background: "var(--loss-bg)", color: "var(--loss)" }}>
                        HIGH
                      </span>
                    )}
                    {article.is_breaking && (
                      <span className="text-[8px] font-bold px-1.5 py-0.5 rounded bg-red-500/20 text-red-400">
                        BREAKING
                      </span>
                    )}
                  </div>
                  <h3 className="text-[15px] font-semibold mb-1 font-display" style={{ color: "var(--text-primary)" }}>{article.title}</h3>
                  {article.summary && <p className="body-text line-clamp-2">{article.summary}</p>}
                  {/* Company/sector tags */}
                  {((article.companies?.length > 0) || (article.sectors?.length > 0)) && (
                    <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                      {article.companies?.slice(0, 3).map(c => (
                        <span key={c} className="text-[9px] px-1.5 py-0.5 rounded-full flex items-center gap-0.5" style={{ background: "var(--hover)", color: "var(--text-secondary)" }}>
                          <Building2 size={8} /> {c}
                        </span>
                      ))}
                      {article.sectors?.slice(0, 2).map(s => (
                        <span key={s} className="text-[9px] px-1.5 py-0.5 rounded-full flex items-center gap-0.5" style={{ background: "var(--hover)", color: "var(--text-muted)" }}>
                          <Tag size={8} /> {s}
                        </span>
                      ))}
                    </div>
                  )}
                </motion.div>
              ))}
            </div>
          )}
        </div>

        {/* Right: Sentiment Panel */}
        <div className="space-y-4">
          <motion.div className="glass-card p-5"
            initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.4 }}>
            <h3 className="eyebrow mb-3">Market Sentiment</h3>
            {sentimentLoading ? (
              <div className="w-20 h-20 mx-auto mb-3 rounded-full skeleton" />
            ) : sentiment?.available ? (
              <>
                <div className="relative w-20 h-20 mx-auto mb-3">
                  <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
                    <circle cx="18" cy="18" r="14" fill="none" stroke="var(--border)" strokeWidth="3" />
                    <circle cx="18" cy="18" r="14" fill="none"
                      stroke={sentiment.score >= 60 ? "var(--gain)" : sentiment.score <= 40 ? "var(--loss)" : "var(--text-muted)"}
                      strokeWidth="3" strokeLinecap="round"
                      strokeDasharray={`${(sentiment.score / 100) * 88} ${88 - (sentiment.score / 100) * 88}`}
                      className="score-gauge-circle" />
                  </svg>
                  <span className="absolute inset-0 flex flex-col items-center justify-center">
                    <span className="text-lg font-bold font-mono"
                      style={{ color: sentiment.score >= 60 ? "var(--gain)" : sentiment.score <= 40 ? "var(--loss)" : "var(--text-muted)" }}>
                      {sentiment.score}
                    </span>
                    <span className="text-[7px]" style={{ color: "var(--text-muted)" }}>{sentiment.label}</span>
                  </span>
                </div>
                <p className="caption text-center" style={{ color: "var(--text-secondary)" }}>
                  Derived from {sentiment.articles_analyzed} live headlines — {sentiment.positive} positive, {sentiment.negative} negative.
                </p>
              </>
            ) : (
              <div className="text-center py-4">
                <AlertCircle size={20} className="mx-auto mb-2 opacity-40" style={{ color: "var(--text-muted)" }} />
                <p className="caption" style={{ color: "var(--text-muted)" }}>
                  Sentiment unavailable — news feeds are unreachable right now.
                </p>
              </div>
            )}
          </motion.div>

          <motion.div className="glass-card p-5"
            initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.4, delay: 0.06 }}>
            <h3 className="eyebrow mb-3">Sources</h3>
            <div className="space-y-1.5">
              {sources.slice(0, 6).map(src => (
                <div key={src} className="flex items-center justify-between px-2.5 py-2 rounded-lg" style={{ background: "var(--hover)" }}>
                  <span className="text-[13px] font-medium" style={{ color: "var(--text-secondary)" }}>{src}</span>
                  <span className="text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>{articles.filter(a => a.source === src).length}</span>
                </div>
              ))}
            </div>
          </motion.div>
        </div>
      </div>

      {/* Article Viewer Modal */}
      {selectedArticle && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(8px)" }} onClick={() => setSelectedArticle(null)}>
          <div className="max-w-2xl w-full max-h-[80vh] overflow-y-auto glass-card" onClick={e => e.stopPropagation()} style={{ borderRadius: "var(--card-radius-lg)" }}>
            <div className="p-6">
              <div className="flex items-center gap-2 mb-3">
                <span className="badge-status" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>{selectedArticle.source}</span>
                <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
                  {selectedArticle.published ? new Date(selectedArticle.published).toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}
                </span>
              </div>
              <h2 className="card-title mb-4">{selectedArticle.title}</h2>
              <div className="body-text mb-6">
                {selectedArticle.summary || "Full article content loading..."}
              </div>
              <div className="flex gap-3">
                <a href={selectedArticle.link} target="_blank" rel="noopener noreferrer" className="btn-ghost btn-sm">
                  <ExternalLink size={14} /> Read Full Article
                </a>
                <button onClick={() => setSelectedArticle(null)} className="btn-primary btn-sm">Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

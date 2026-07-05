import { useState, useEffect } from "react";
import api from "../services/api";
import { Newspaper, ExternalLink, RefreshCw, Search, TrendingUp, TrendingDown, Globe, AlertCircle } from "lucide-react";

const NEWS_TABS = ["All", "Market", "Economy", "FOSS", "Global"];

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

  useEffect(() => { fetchNews(); }, []);

  const fetchNews = async (force = false) => {
    setLoading(true);
    try {
      const url = force ? "/news/refresh" : "/news";
      const { data } = await api.get(url);
      setArticles(data.articles || []);
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  };

  const filtered = articles.filter(a => {
    const matchesSearch = !filter || (a.title + a.summary + a.source).toLowerCase().includes(filter.toLowerCase());
    const matchesTab = activeTab === "All" || (a.category || "market").toLowerCase() === activeTab.toLowerCase();
    return matchesSearch && matchesTab;
  });

  const sources = [...new Set(articles.map(a => a.source))];

  return (
    <div data-testid="news-page" className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl sm:text-[28px] font-semibold tracking-tight font-display" style={{ color: "var(--text-primary)" }}>Market Intelligence</h1>
          <p className="text-[13px] mt-0.5" style={{ color: "var(--text-secondary)" }}>Stay updated with real-time market events</p>
        </div>
        <button data-testid="refresh-news-btn" onClick={() => fetchNews(true)}
          className="p-2.5 rounded-xl transition-all" style={{ color: "var(--text-muted)" }}
          onMouseEnter={e => e.currentTarget.style.background = "var(--hover)"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

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
            <div className="space-y-2">
              {filtered.map((article, i) => (
                <div key={i} data-testid={`news-article-${i}`}
                  className="glass-card p-4 cursor-pointer transition-all hover:-translate-y-px"
                  onClick={() => setSelectedArticle(article)}>
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="badge-status" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>{article.source}</span>
                    <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
                      {article.published ? new Date(article.published).toLocaleString("en-IN", { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" }) : ""}
                    </span>
                    {article.sentiment && <SentimentBadge sentiment={article.sentiment} />}
                  </div>
                  <h3 className="text-[13px] font-semibold mb-1" style={{ color: "var(--text-primary)" }}>{article.title}</h3>
                  {article.summary && <p className="text-[12px] leading-relaxed line-clamp-2" style={{ color: "var(--text-secondary)" }}>{article.summary}</p>}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: Sentiment Panel */}
        <div className="space-y-4">
          <div className="glass-card p-5">
            <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] mb-3" style={{ color: "var(--text-muted)" }}>Market Sentiment</h3>
            <div className="relative w-20 h-20 mx-auto mb-3">
              <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
                <circle cx="18" cy="18" r="14" fill="none" stroke="var(--border)" strokeWidth="3" />
                <circle cx="18" cy="18" r="14" fill="none" stroke="var(--gain)" strokeWidth="3" strokeLinecap="round"
                  strokeDasharray={`${72 * 0.88} ${88 - 72 * 0.88}`} className="score-gauge-circle" />
              </svg>
              <span className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-lg font-bold font-mono" style={{ color: "var(--gain)" }}>72</span>
                <span className="text-[7px]" style={{ color: "var(--text-muted)" }}>Bullish</span>
              </span>
            </div>
            <p className="text-[11px] text-center" style={{ color: "var(--text-secondary)" }}>Overall market sentiment is positive with strong buying pressure.</p>
          </div>

          <div className="glass-card p-5">
            <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] mb-3" style={{ color: "var(--text-muted)" }}>Sources</h3>
            <div className="space-y-1.5">
              {sources.slice(0, 6).map(src => (
                <div key={src} className="flex items-center justify-between px-2 py-1.5 rounded-lg" style={{ background: "var(--hover)" }}>
                  <span className="text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>{src}</span>
                  <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>{articles.filter(a => a.source === src).length}</span>
                </div>
              ))}
            </div>
          </div>
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
              <h2 className="text-xl font-semibold mb-4 font-display" style={{ color: "var(--text-primary)" }}>{selectedArticle.title}</h2>
              <div className="text-[13px] leading-relaxed mb-6" style={{ color: "var(--text-secondary)" }}>
                {selectedArticle.summary || "Full article content loading..."}
              </div>
              <div className="flex gap-2">
                <a href={selectedArticle.link} target="_blank" rel="noopener noreferrer" className="btn-ghost text-[12px] py-2 px-4">
                  <ExternalLink size={12} /> Read Full Article
                </a>
                <button onClick={() => setSelectedArticle(null)} className="btn-primary text-[12px] py-2 px-4">Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

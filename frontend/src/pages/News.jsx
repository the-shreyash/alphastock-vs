import { useState, useEffect } from "react";
import api from "../services/api";
import { Newspaper, ExternalLink, RefreshCw, Search } from "lucide-react";

export default function News() {
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
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

  const filtered = filter
    ? articles.filter((a) => (a.title + a.summary + a.source).toLowerCase().includes(filter.toLowerCase()))
    : articles;

  const sources = [...new Set(articles.map((a) => a.source))];

  return (
    <div data-testid="news-page" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl sm:text-3xl font-medium tracking-tight" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>Market News</h1>
          <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
            Live from {sources.join(", ")} | {articles.length} articles
          </p>
        </div>
        <button data-testid="refresh-news-btn" onClick={() => fetchNews(true)} className="p-2 rounded-xl" style={{ color: "var(--text-muted)" }}>
          <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {/* Search */}
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
        <input data-testid="news-search-input" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search news..."
          className="w-full pl-10 pr-4 py-2.5 rounded-xl text-sm focus:outline-none"
          style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
      </div>

      {/* Articles */}
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4, 5].map((i) => <div key={i} className="h-24 rounded-xl animate-pulse" style={{ background: "var(--bg-surface)" }} />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="card-premium p-12 text-center">
          <Newspaper size={32} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
          <p style={{ color: "var(--text-muted)" }}>No news found</p>
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map((article, i) => (
            <div key={i} data-testid={`news-article-${i}`}
              className="card-premium p-4 cursor-pointer transition-all hover:-translate-y-px"
              onClick={() => setSelectedArticle(article)}>
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-lg"
                  style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>{article.source}</span>
                <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
                  {article.published ? new Date(article.published).toLocaleString("en-IN", { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" }) : ""}
                </span>
              </div>
              <h3 className="text-sm font-semibold mb-1" style={{ color: "var(--text-primary)" }}>{article.title}</h3>
              {article.summary && <p className="text-xs leading-relaxed line-clamp-2" style={{ color: "var(--text-secondary)" }}>{article.summary}</p>}
            </div>
          ))}
        </div>
      )}

      {/* Article Viewer Modal */}
      {selectedArticle && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }} onClick={() => setSelectedArticle(null)}>
          <div className="max-w-2xl w-full max-h-[80vh] overflow-y-auto rounded-2xl border" style={{ background: "var(--bg)", borderColor: "var(--border)" }} onClick={(e) => e.stopPropagation()}>
            <div className="p-6">
              <div className="flex items-center gap-2 mb-3">
                <span className="text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-lg" style={{ background: "var(--ai-accent-soft)", color: "var(--ai-accent)" }}>{selectedArticle.source}</span>
                <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
                  {selectedArticle.published ? new Date(selectedArticle.published).toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}
                </span>
              </div>
              <h2 className="text-xl font-semibold mb-4" style={{ fontFamily: "Outfit", color: "var(--text-primary)" }}>{selectedArticle.title}</h2>
              <div className="text-sm leading-relaxed mb-6" style={{ color: "var(--text-secondary)" }}>
                {selectedArticle.summary || "Full article content loading..."}
              </div>
              <div className="p-3 rounded-xl mb-4" style={{ background: "var(--bg-surface)" }}>
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                  This article is sourced from {selectedArticle.source}. For the complete article with images and detailed analysis, visit the original source.
                </p>
              </div>
              <div className="flex gap-2">
                <a href={selectedArticle.link} target="_blank" rel="noopener noreferrer"
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-medium"
                  style={{ background: "var(--bg-surface)", color: "var(--text-secondary)" }}>
                  <ExternalLink size={12} /> Read Full Article
                </a>
                <button onClick={() => setSelectedArticle(null)}
                  className="px-4 py-2 rounded-xl text-xs font-medium"
                  style={{ background: "var(--brand)", color: "var(--bg)" }}>Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

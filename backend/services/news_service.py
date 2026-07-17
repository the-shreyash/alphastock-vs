"""Stock market news aggregator from RSS feeds, with deterministic
keyword-based sentiment and importance derived from real headlines
(never random).

Sprint R8: every article also carries `importance` ("high" | "normal") and
`is_breaking` so the live pipeline (heartbeat `task_scan_news` →
`news.breaking` event) and the News UI can surface market-moving headlines
the moment they appear. `filter_breaking_novel()` is the flood gate: it keeps
a per-headline cooldown so a published `news.breaking` event always carries
headlines the platform has not streamed before (mirrors
market_engine.scanner_worker.filter_novel).
"""
# pyrefly: ignore [missing-import]
import feedparser
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

from services.cache import cache_get, cache_set, cache_delete

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=3)

RSS_FEEDS = [
    {"name": "MoneyControl", "url": "https://www.moneycontrol.com/rss/latestnews.xml", "category": "general"},
    {"name": "ET Markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "category": "markets"},
    {"name": "LiveMint", "url": "https://www.livemint.com/rss/markets", "category": "markets"},
    {"name": "MoneyControl Markets", "url": "https://www.moneycontrol.com/rss/marketreports.xml", "category": "markets"},
    {"name": "ET Stocks", "url": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms", "category": "stocks"},
]

NEWS_CACHE_KEY = "news_articles"
CACHE_TTL = 300  # 5 minutes

# Deterministic sentiment keywords for Indian financial headlines
_POSITIVE_TERMS = (
    "surge", "surges", "rally", "rallies", "gain", "gains", "jump", "jumps",
    "rise", "rises", "soar", "soars", "record high", "all-time high", "upgrade",
    "upgraded", "bullish", "profit", "beats", "strong", "buy", "outperform",
    "advance", "advances", "recovery", "rebound", "growth", "positive",
)
_NEGATIVE_TERMS = (
    "fall", "falls", "drop", "drops", "decline", "declines", "crash", "crashes",
    "plunge", "plunges", "slump", "slumps", "loss", "losses", "downgrade",
    "downgraded", "bearish", "weak", "sell-off", "selloff", "misses", "concern",
    "concerns", "fear", "fears", "pressure", "slide", "slides", "negative", "cut",
)


# Headlines matching any of these are market-moving enough to interrupt the
# user (breaking badge + live toast). Deliberately conservative: routine
# "stock rises 2%" coverage must never trigger a breaking push.
_BREAKING_TERMS = (
    "crash", "crashes", "circuit breaker", "upper circuit", "lower circuit",
    "plunge", "plunges", "sensex tanks", "nifty tanks", "market rout",
    "emergency", "rbi rate", "repo rate", "rate cut", "rate hike",
    "monetary policy", "fed rate", "record high", "all-time high",
    "all time high", "lifetime high", "black monday", "sell-off deepens",
    "fraud", "scam", "default", "insolvency", "bankruptcy", "sebi bans",
    "sebi order", "trading halt", "halted", "war", "sanctions", "tariff",
    "budget 2", "union budget", "election result", "downgrade to junk",
    "moratorium", "merger", "acquisition", "acquires", "takeover", "delisting",
    "ipo opens", "ipo allotment", "stake sale", "open offer", "buyback",
)

BREAKING_COOLDOWN_MINUTES = 120  # one live push per headline per 2h window

# Process-local memory of already-streamed breaking headlines (title-keyed).
_recent_breaking: dict = {}


def _classify_importance(text: str) -> str:
    """Deterministic importance for a headline/summary: 'high' when it matches
    a market-moving term, otherwise 'normal'."""
    t = (text or "").lower()
    return "high" if any(term in t for term in _BREAKING_TERMS) else "normal"


def filter_breaking_novel(articles, now=None):
    """Return only breaking articles not streamed within the cooldown window.

    Survivors are recorded so the next scan suppresses them; expired entries
    are pruned to bound memory. State is process-local by design — only the
    heartbeat process publishes `news.breaking`, and a restart harmlessly
    re-arms the cooldown.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=BREAKING_COOLDOWN_MINUTES)

    for key, seen_at in list(_recent_breaking.items()):
        if seen_at < cutoff:
            del _recent_breaking[key]

    novel = []
    for article in articles or []:
        if not article.get("is_breaking"):
            continue
        key = (article.get("title") or "").lower()[:80]
        if not key or key in _recent_breaking:
            continue
        _recent_breaking[key] = now
        novel.append(article)
    return novel


def reset_breaking_state() -> None:
    """Clear the breaking-news cooldown memory (test isolation)."""
    _recent_breaking.clear()


def _classify_sentiment(text: str) -> str:
    """Keyword-based sentiment for a headline/summary. Deterministic — the same
    text always yields the same label."""
    t = (text or "").lower()
    pos = sum(1 for w in _POSITIVE_TERMS if w in t)
    neg = sum(1 for w in _NEGATIVE_TERMS if w in t)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _strip_html(text):
    """Remove HTML tags and decode entities from text."""
    import re
    import html
    clean = re.sub(r'<[^>]+>', '', text)
    return html.unescape(clean).strip()


def _parse_feed(feed_info):
    """Parse a single RSS feed (blocking)."""
    try:
        parsed = feedparser.parse(feed_info["url"])
        articles = []
        for entry in parsed.entries[:10]:
            published = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published = datetime(*entry.published_parsed[:6]).isoformat()
                except Exception:
                    published = entry.get("published", "")

            title = _strip_html(entry.get("title", "")).strip()
            summary = _strip_html(entry.get("summary", ""))[:200].strip()
            importance = _classify_importance(f"{title} {summary}")
            articles.append({
                "title": title,
                "link": entry.get("link", ""),
                "summary": summary,
                "published": published,
                "source": feed_info["name"],
                "category": feed_info["category"],
                "sentiment": _classify_sentiment(f"{title} {summary}"),
                "importance": importance,
                "is_breaking": importance == "high",
            })
        return articles
    except Exception as e:
        logger.error(f"RSS feed error ({feed_info['name']}): {e}")
        return []


async def fetch_news(force=False):
    """Fetch news from all RSS feeds with caching (Redis when configured)."""
    if force:
        await cache_delete(NEWS_CACHE_KEY)
    else:
        cached = await cache_get(NEWS_CACHE_KEY)
        if cached:
            return cached

    loop = asyncio.get_event_loop()
    all_articles = []
    for feed in RSS_FEEDS:
        try:
            articles = await loop.run_in_executor(executor, _parse_feed, feed)
            all_articles.extend(articles)
        except Exception as e:
            logger.error(f"Feed fetch error: {e}")

    # Sort by published date (newest first), deduplicate by title
    seen = set()
    unique = []
    for a in all_articles:
        key = a["title"].lower()[:50]
        if key not in seen:
            seen.add(key)
            unique.append(a)

    unique.sort(key=lambda x: x.get("published", ""), reverse=True)

    articles = unique[:50]
    if articles:
        await cache_set(NEWS_CACHE_KEY, articles, CACHE_TTL)
    return articles


async def get_market_sentiment():
    """Aggregate market sentiment derived from real news headlines.
    Returns an explicit unavailable payload when no articles could be fetched."""
    articles = await fetch_news()
    if not articles:
        return {
            "available": False,
            "score": None,
            "label": None,
            "note": "News feeds are temporarily unreachable — sentiment unavailable.",
        }

    positive = sum(1 for a in articles if a.get("sentiment") == "positive")
    negative = sum(1 for a in articles if a.get("sentiment") == "negative")
    neutral = len(articles) - positive - negative

    # 50 = balanced; each net positive/negative headline shifts the score
    score = int(max(0, min(100, round(50 + (positive - negative) / len(articles) * 50))))
    label = "Bullish" if score >= 60 else ("Bearish" if score <= 40 else "Neutral")

    return {
        "available": True,
        "score": score,
        "label": label,
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "articles_analyzed": len(articles),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def search_stock_news(symbol: str, stock_name: str = ""):
    """Search news relevant to a specific stock."""
    all_news = await fetch_news()
    query = symbol.upper()
    name_parts = stock_name.lower().split() if stock_name else []

    relevant = []
    for article in all_news:
        text = (article["title"] + " " + article["summary"]).lower()
        if query.lower() in text or any(p in text for p in name_parts if len(p) > 3):
            relevant.append(article)

    return relevant[:10]

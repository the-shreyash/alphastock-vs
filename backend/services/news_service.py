"""Stock market news aggregator from RSS feeds, with deterministic
keyword-based sentiment derived from real headlines (never random)."""
# pyrefly: ignore [missing-import]
import feedparser
import logging
import asyncio
from datetime import datetime, timezone
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
            articles.append({
                "title": title,
                "link": entry.get("link", ""),
                "summary": summary,
                "published": published,
                "source": feed_info["name"],
                "category": feed_info["category"],
                "sentiment": _classify_sentiment(f"{title} {summary}"),
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

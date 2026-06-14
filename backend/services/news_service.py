"""Stock market news aggregator from RSS feeds."""
# pyrefly: ignore [missing-import]
import feedparser
import logging
import asyncio
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=3)

RSS_FEEDS = [
    {"name": "MoneyControl", "url": "https://www.moneycontrol.com/rss/latestnews.xml", "category": "general"},
    {"name": "ET Markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "category": "markets"},
    {"name": "LiveMint", "url": "https://www.livemint.com/rss/markets", "category": "markets"},
    {"name": "MoneyControl Markets", "url": "https://www.moneycontrol.com/rss/marketreports.xml", "category": "markets"},
    {"name": "ET Stocks", "url": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms", "category": "stocks"},
]

# Cache
_news_cache = {"data": [], "fetched_at": None}
CACHE_TTL = 300  # 5 minutes


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

            articles.append({
                "title": _strip_html(entry.get("title", "")).strip(),
                "link": entry.get("link", ""),
                "summary": _strip_html(entry.get("summary", ""))[:200].strip(),
                "published": published,
                "source": feed_info["name"],
                "category": feed_info["category"],
            })
        return articles
    except Exception as e:
        logger.error(f"RSS feed error ({feed_info['name']}): {e}")
        return []


async def fetch_news(force=False):
    """Fetch news from all RSS feeds with caching."""
    global _news_cache
    now = datetime.now(timezone.utc)

    if not force and _news_cache["fetched_at"]:
        elapsed = (now - _news_cache["fetched_at"]).total_seconds()
        if elapsed < CACHE_TTL and _news_cache["data"]:
            return _news_cache["data"]

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

    _news_cache = {"data": unique[:50], "fetched_at": now}
    return _news_cache["data"]


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

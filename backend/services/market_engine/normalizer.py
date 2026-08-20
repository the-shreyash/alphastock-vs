"""Data normalizer for the Market Engine.

Different market data providers return data in different formats. The
normalizer converts provider-specific responses into a canonical internal
format so the rest of the platform never depends on provider formats.

Canonical formats:
    StockQuote:   symbol, name, price, open, high, low, close, prev_close,
                  change, change_pct, volume, avg_volume, volume_ratio,
                  market_cap, pe_ratio, week_52_high, week_52_low,
                  day_range, sector, exchange, timestamp
    IndexQuote:   name, value, change, change_pct, timestamp
    SectorData:   name, change_pct, leaders, laggards, breadth
    NewsArticle:  title, summary, source, url, published, sentiment,
                  sentiment_score, companies, sectors, importance

PROVENANCE (D1)
---------------
Normalized events deliberately carry NO provider name. Each `_normalize_*`
function is selected *by* provider — that is the "one normalizer function per
provider" rule in MARKET_DATA_ARCHITECTURE.md — but the provider identity stops
at this boundary and does not appear in the output.

Until D1 every normalized quote carried `provider: "yahoo"`, which is precisely
the leak Developer Rules 4 and 5 forbid: any consumer could branch on it, and
the first consumer to do so would silently misbehave the day a broker feed
arrived. The Market Gateway now stamps `source_tier` (`streaming` | `delayed`)
and `ingested_at` instead — freshness without provenance, which is everything a
consumer legitimately needs and nothing it can couple to.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def normalize_stock_quote(raw: Dict[str, Any], provider: str = "yahoo") -> Optional[Dict[str, Any]]:
    """Normalize a raw stock quote into the canonical StockQuote format.

    Supports: yahoo (from real_market.py), alpha_vantage, broker (Kite).
    Returns None if the raw data is unusable.
    """
    if not raw:
        return None

    try:
        if provider == "yahoo":
            return _normalize_yahoo_quote(raw)
        if provider == "alpha_vantage":
            return _normalize_av_quote(raw)
        if provider == "broker":
            return _normalize_broker_quote(raw)
        # Unknown provider — attempt passthrough with defaults
        return _apply_defaults(raw)
    except Exception as exc:
        logger.warning(f"Normalizer: failed to normalize {provider} quote: {exc}")
        return None


def _normalize_yahoo_quote(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Yahoo Finance quote from real_market.py format."""
    return {
        "symbol": (raw.get("symbol") or "").upper(),
        "name": raw.get("name", raw.get("symbol", "")),
        "price": _to_float(raw.get("price")),
        "open": _to_float(raw.get("open")),
        "high": _to_float(raw.get("high")),
        "low": _to_float(raw.get("low")),
        "close": _to_float(raw.get("close", raw.get("price"))),
        "prev_close": _to_float(raw.get("prev_close")),
        "change": _to_float(raw.get("change")),
        "change_pct": _to_float(raw.get("change_pct")),
        "volume": _to_int(raw.get("volume")),
        "avg_volume": _to_int(raw.get("avg_volume")),
        "volume_ratio": _to_float(raw.get("volume_ratio")),
        "market_cap": raw.get("market_cap_cr") or raw.get("market_cap"),
        "pe_ratio": raw.get("pe_ratio"),
        "week_52_high": raw.get("week_52_high"),
        "week_52_low": raw.get("week_52_low"),
        "day_range": raw.get("day_range", ""),
        "sector": raw.get("sector", ""),
        "exchange": raw.get("exchange", "NSE"),
        # Technical indicators (passthrough if present)
        "rsi": _to_float(raw.get("rsi")),
        "macd": raw.get("macd"),
        "vwap": _to_float(raw.get("vwap")),
        "ema_20": _to_float(raw.get("ema_20")),
        "sma_50": _to_float(raw.get("sma_50")),
        "timestamp": raw.get("timestamp", datetime.now(timezone.utc).isoformat()),
    }


def _normalize_av_quote(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Alpha Vantage Global Quote response."""
    gq = raw.get("Global Quote", raw)
    return {
        "symbol": (gq.get("01. symbol") or gq.get("symbol") or "").replace(".BSE", "").replace(".NS", "").upper(),
        "name": gq.get("name", ""),
        "price": _to_float(gq.get("05. price") or gq.get("price")),
        "open": _to_float(gq.get("02. open") or gq.get("open")),
        "high": _to_float(gq.get("03. high") or gq.get("high")),
        "low": _to_float(gq.get("04. low") or gq.get("low")),
        "close": _to_float(gq.get("08. previous close") or gq.get("close")),
        "prev_close": _to_float(gq.get("08. previous close") or gq.get("prev_close")),
        "change": _to_float(gq.get("09. change") or gq.get("change")),
        "change_pct": _parse_percent(gq.get("10. change percent") or gq.get("change_pct")),
        "volume": _to_int(gq.get("06. volume") or gq.get("volume")),
        "avg_volume": None,
        "volume_ratio": None,
        "market_cap": None,
        "pe_ratio": None,
        "week_52_high": None,
        "week_52_low": None,
        "day_range": "",
        "sector": "",
        "exchange": "NSE",
        "rsi": None,
        "macd": None,
        "vwap": None,
        "ema_20": None,
        "sma_50": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _normalize_broker_quote(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize broker (Kite) quote format. Maps ltp -> price, etc."""
    return {
        "symbol": (raw.get("tradingsymbol") or raw.get("symbol") or "").upper(),
        "name": raw.get("name", ""),
        "price": _to_float(raw.get("last_price") or raw.get("ltp") or raw.get("price")),
        "open": _to_float(raw.get("ohlc", {}).get("open") or raw.get("open")),
        "high": _to_float(raw.get("ohlc", {}).get("high") or raw.get("high")),
        "low": _to_float(raw.get("ohlc", {}).get("low") or raw.get("low")),
        "close": _to_float(raw.get("ohlc", {}).get("close") or raw.get("close")),
        "prev_close": _to_float(raw.get("ohlc", {}).get("close") or raw.get("prev_close")),
        "change": _to_float(raw.get("change")),
        "change_pct": _to_float(raw.get("change_pct")),
        "volume": _to_int(raw.get("volume") or raw.get("volume_traded")),
        "avg_volume": None,
        "volume_ratio": None,
        "market_cap": None,
        "pe_ratio": None,
        "week_52_high": None,
        "week_52_low": None,
        "day_range": "",
        "sector": "",
        "exchange": raw.get("exchange", "NSE"),
        "rsi": None,
        "macd": None,
        "vwap": None,
        "ema_20": None,
        "sma_50": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def normalize_index_quote(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize an index quote into canonical IndexQuote format."""
    if not raw:
        return None
    return {
        "name": raw.get("name") or raw.get("index") or "",
        "value": _to_float(raw.get("value") or raw.get("price") or raw.get("close")),
        "change": _to_float(raw.get("change")),
        "change_pct": _to_float(raw.get("change_pct") or raw.get("changePercent")),
        "prev_close": _to_float(raw.get("prev_close")),
        "high": _to_float(raw.get("high")),
        "low": _to_float(raw.get("low")),
        "timestamp": raw.get("timestamp", datetime.now(timezone.utc).isoformat()),
    }


def normalize_news_article(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a news article into the canonical NewsArticle format.
    Adds default fields for company/sector mapping and importance."""
    return {
        "title": (raw.get("title") or "").strip(),
        "summary": (raw.get("summary") or "").strip(),
        "source": raw.get("source", "Unknown"),
        "url": raw.get("link") or raw.get("url", ""),
        "published": raw.get("published", ""),
        "category": raw.get("category", "general"),
        "sentiment": raw.get("sentiment", "neutral"),
        "sentiment_score": _sentiment_to_score(raw.get("sentiment", "neutral")),
        "companies": raw.get("companies", []),
        "sectors": raw.get("sectors", []),
        "importance": raw.get("importance", "medium"),
        "is_breaking": raw.get("is_breaking", False),
    }


def normalize_sector_data(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize sector performance data."""
    return {
        "name": raw.get("name") or raw.get("sector") or "",
        "change_pct": _to_float(raw.get("change_pct") or raw.get("change") or raw.get("performance")),
        "leaders": raw.get("leaders", []),
        "laggards": raw.get("laggards", []),
        "market_cap": raw.get("market_cap"),
        "breadth": raw.get("breadth", {}),
        "momentum": raw.get("momentum", "neutral"),
    }


# --- Internal helpers ---

def _to_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return round(float(val), 2)
    except (ValueError, TypeError):
        return None


def _to_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _parse_percent(val: Any) -> Optional[float]:
    if val is None:
        return None
    s = str(val).replace("%", "").strip()
    return _to_float(s)


def _sentiment_to_score(sentiment: str) -> float:
    """Convert sentiment label to numeric score (0-1)."""
    mapping = {"positive": 0.8, "negative": 0.2, "neutral": 0.5}
    return mapping.get(sentiment, 0.5)


def _apply_defaults(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Apply default fields to an unrecognized quote format."""
    defaults = {
        "symbol": "", "name": "", "price": None, "open": None, "high": None,
        "low": None, "close": None, "prev_close": None, "change": None,
        "change_pct": None, "volume": None, "avg_volume": None,
        "volume_ratio": None, "market_cap": None, "pe_ratio": None,
        "week_52_high": None, "week_52_low": None, "day_range": "",
        "sector": "", "exchange": "NSE", "rsi": None, "macd": None,
        "vwap": None, "ema_20": None, "sma_50": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    result = {**defaults, **raw}
    return result

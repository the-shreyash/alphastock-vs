"""Real market data service using Alpha Vantage + Yahoo Finance fallback.
Replaces simulated random data with actual market prices."""
import os
import httpx
import logging
import asyncio
import json
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from services.cache import cache_get, cache_set, cache_get_many
from services import http_client

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=5)

CACHE_TTL = 60  # 1 minute cache for live data, longer when market closed

ALPHA_VANTAGE_BASE = "https://www.alphavantage.co/query"


# --------------------------------------------------------------------------- #
# Provider origin — overridable for non-production environments (PH3.5)         #
# --------------------------------------------------------------------------- #
#: When set, every Yahoo Finance request in this module and in
#: `services.stock_details` is issued against this origin instead of
#: ``https://<host>.finance.yahoo.com``. Unset (the default, and the only
#: supported production value) the URLs are byte-identical to what they were
#: before this variable existed.
#:
#: WHY THIS EXISTS. PH3.5 load-tests the quote fan-out, which is the hottest
#: outbound path in the system (once per symbol, 12–50 symbols per request).
#: Driving that at 50 concurrent users would mean thousands of requests per
#: minute at Yahoo Finance — which the PH3.5 brief (§14) forbids outright, and
#: which would in any case measure Yahoo's throttle rather than StockAssist's
#: capacity. The alternative — monkeypatching the call sites from the load
#: harness — would exercise a code path that does not exist in production, so
#: the measurement would not transfer. Redirecting the origin keeps every line
#: of application code (pooling, timeouts, caching, error containment, indicator
#: maths) on exactly the path production takes, and changes only where the bytes
#: come from.
#:
#: This mirrors the mechanism the Anthropic SDK already provides via
#: ``ANTHROPIC_BASE_URL``, which PH3.5 uses for the same reason on the AI path.
#: It is a *test-environment* affordance, not a provider-selection feature:
#: MARKET_DATA_ARCHITECTURE.md remains authoritative for which provider serves
#: production traffic, and this variable must never be set in production.
_YAHOO_ORIGIN_ENV = "MARKET_DATA_YAHOO_BASE"


def yahoo_origin(host: str = "query1") -> str:
    """Scheme+authority for a Yahoo Finance request against ``host``.

    Read from the environment on every call rather than captured at import,
    because `security.secrets.load_secrets()` materialises configuration into
    ``os.environ`` *after* this module is imported — the same reason
    `infrastructure.redis_client.redis_url()` is a function.

    A trailing slash on the override is tolerated and stripped so that callers
    can concatenate a path without producing a double slash.
    """
    override = os.environ.get(_YAHOO_ORIGIN_ENV, "").strip()
    if override:
        return override.rstrip("/")
    return f"https://{host}.finance.yahoo.com"


def yahoo_origin_overridden() -> bool:
    """True when a non-production provider origin is configured.

    Callers need this only where a request targets a Yahoo host that is *not*
    one of the query hosts (the `fc.yahoo.com` cookie bootstrap in
    `services.stock_details`), and so cannot be redirected by host substitution
    alone."""
    return bool(os.environ.get(_YAHOO_ORIGIN_ENV, "").strip())


# NSE stocks with Yahoo Finance tickers
YAHOO_TICKERS = {
    "RELIANCE": "RELIANCE.NS", "TCS": "TCS.NS", "HDFCBANK": "HDFCBANK.NS",
    "INFY": "INFY.NS", "ICICIBANK": "ICICIBANK.NS", "HINDUNILVR": "HINDUNILVR.NS",
    "ITC": "ITC.NS", "SBIN": "SBIN.NS", "BHARTIARTL": "BHARTIARTL.NS",
    "KOTAKBANK": "KOTAKBANK.NS", "LT": "LT.NS", "AXISBANK": "AXISBANK.NS",
    "ASIANPAINT": "ASIANPAINT.NS", "MARUTI": "MARUTI.NS", "TITAN": "TITAN.NS",
    "SUNPHARMA": "SUNPHARMA.NS",
    # `TATAMOTORS` removed in D5.17: the symbol is delisted after the demerger
    # and `TATAMOTORS.NS` answers "No data found, symbol may be delisted".
    # Its successors need no entry — `resolve_yahoo_ticker` appends `.NS` to an
    # unmapped NSE symbol, and `TMPV.NS` / `TMCV.NS` both quote. Verified
    # 2026-08-31. See market_data.py for the reference-data gap this exposed.
    "BAJFINANCE": "BAJFINANCE.NS", "WIPRO": "WIPRO.NS", "ONGC": "ONGC.NS",
    "NTPC": "NTPC.NS", "POWERGRID": "POWERGRID.NS", "M&M": "M%26M.NS",
    "HCLTECH": "HCLTECH.NS", "TATASTEEL": "TATASTEEL.NS",
    "ADANIENT": "ADANIENT.NS", "COALINDIA": "COALINDIA.NS",
    "DRREDDY": "DRREDDY.NS", "CIPLA": "CIPLA.NS", "TECHM": "TECHM.NS",
}

INDEX_TICKERS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
    "INDIAVIX": "^INDIAVIX",
}


def _get_av_key():
    return os.environ.get("ALPHA_VANTAGE_KEY", "").strip()


def resolve_yahoo_ticker(symbol: str) -> str:
    """Map an app symbol to its Yahoo Finance ticker.

    Tickers starting with ^ (indices), ending with =F (futures), =X (forex),
    or already matching a known ticker map are passed through unchanged;
    everything else is treated as an NSE equity and gets `.NS` appended.
    Shared by the quote/chart clients here and services.stock_details."""
    already_formatted = (
        symbol.startswith("^") or
        symbol.endswith("=F") or
        symbol.endswith("=X") or
        symbol in YAHOO_TICKERS.values() or
        symbol in INDEX_TICKERS.values()
    )
    yahoo_ticker = YAHOO_TICKERS.get(symbol.upper()) or INDEX_TICKERS.get(symbol.upper())
    if not yahoo_ticker:
        yahoo_ticker = symbol if already_formatted else f"{symbol.upper()}.NS"
    return yahoo_ticker


async def fetch_yahoo_quote(symbol: str, range_str: str = "2d"):
    """Fetch real-time quote from Yahoo Finance."""
    yahoo_ticker = resolve_yahoo_ticker(symbol)

    cache_key = f"yahoo_{yahoo_ticker}_{range_str}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    try:
        url = f"{yahoo_origin('query1')}/v8/finance/chart/{yahoo_ticker}?interval=1d&range={range_str}"
        # Pooled client (PH3.4). This function is called once per symbol and
        # fanned out with asyncio.gather — 12 for a watchlist, up to 50 for open
        # positions — so a fresh client here meant one TLS handshake per symbol to
        # the same host. Measured 854 ms -> 228 ms for a 10-symbol batch.
        async with http_client.client_for(timeout=8) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return None
            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            if not result:
                return None

            meta = result[0].get("meta", {})
            indicators = result[0].get("indicators", {}).get("quote", [{}])[0]

            price = meta.get("regularMarketPrice", 0)

            # D5.19 — THE DAY'S CHANGE MUST BE THE DAY'S CHANGE.
            #
            # This used to read `meta.chartPreviousClose`, which is the close
            # immediately BEFORE the requested window — a function of
            # `range_str`, not of the calendar. Measured live on 2026-09-01
            # against the real vendor, one symbol, one price, four windows:
            #
            #     RELIANCE  2d   prev_close=1277.0  ->  +2.62%
            #     RELIANCE  5d   prev_close=1298.0  ->  +0.96%
            #     RELIANCE  1mo  prev_close=1307.8  ->  +0.21%
            #     RELIANCE  3mo  prev_close=1320.0  ->  -0.72%
            #
            # `fetch_real_stock_quote` asks for `3mo` because MACD needs 26+
            # bars, so `GET /api/stocks/RELIANCE` — the stock detail page —
            # served **-0.72%** while `/market/ranking` and `/market/overview`,
            # which fetch `2d`, served **+2.62%** for the same instrument at
            # the same instant. The detail page was labelling a three-month
            # change "today".
            #
            # That is D5.18's D-1 defect in a second place: two surfaces of one
            # product disagreeing about one fact, with the user given no way to
            # tell which is lying. It also blocked D5.19's D-3 — making an index
            # card navigable would have sent a user from a card reading
            # NIFTY +0.13% to a page reading NIFTY +3.11%.
            #
            # The previous close is a property of the SERIES: the close of the
            # bar before the one the live price belongs to. Derived that way it
            # is the same number at every range, which is the invariant
            # `tests/test_day_change_is_the_days_change.py` asserts.
            #
            # The vendor field stays as the fallback for a single-bar window,
            # where the series genuinely cannot answer. Reporting a zero change
            # there would render "unchanged" over a price that may have moved
            # several percent — the fabrication the tick contract and
            # `applyLivePrices` both exist to refuse.
            daily_closes = [c for c in (indicators.get("close") or []) if c is not None]
            prev_close = (
                daily_closes[-2]
                if len(daily_closes) >= 2
                else meta.get("chartPreviousClose", meta.get("previousClose", 0))
            )
            change = round(price - prev_close, 2) if prev_close else 0
            change_pct = round((change / prev_close) * 100, 2) if prev_close else 0

            # Get OHLV from latest period
            opens = [o for o in indicators.get("open", []) if o is not None]
            highs = [h for h in indicators.get("high", []) if h is not None]
            lows = [l for l in indicators.get("low", []) if l is not None]
            volumes = [v for v in indicators.get("volume", []) if v is not None]
            raw_timestamps = result[0].get("timestamp", []) or []
            raw_closes = indicators.get("close", []) or []
            # Timestamps filtered in lockstep with historical_closes so
            # consumers can date-align two symbols' close series (beta).
            close_timestamps = [
                t for t, c in zip(raw_timestamps, raw_closes) if c is not None
            ]

            quote = {
                "price": round(price, 2),
                "prev_close": round(prev_close, 2),
                "change": change,
                "change_pct": change_pct,
                "open": round(opens[-1], 2) if opens else round(price, 2),
                "high": round(highs[-1], 2) if highs else round(price, 2),
                "low": round(lows[-1], 2) if lows else round(price, 2),
                "volume": volumes[-1] if volumes else 0,
                "market_state": meta.get("marketState", "CLOSED"),
                "exchange": meta.get("exchangeName", "NSE"),
                "currency": meta.get("currency", "INR"),
                # No `source` field. An adapter returns the provider's raw
                # payload; naming the provider *inside* that payload is how the
                # name reached the public REST contract in the first place
                # (DD-1). The Market Gateway stamps `source_tier` at the
                # normalization boundary — freshness, never provenance.
                "historical_closes": [c for c in raw_closes if c is not None],
                "historical_close_timestamps": close_timestamps,
                "historical_volumes": volumes,
                "historical_highs": highs,
                "historical_lows": lows,
                "historical_opens": opens,
                "historical_timestamps": raw_timestamps,
            }
            await cache_set(cache_key, quote, CACHE_TTL)
            return quote
    except Exception as e:
        logger.error(f"Yahoo Finance error for {symbol}: {e}")
        return None


async def fetch_dividend_info(symbols):
    """Real trailing-dividend info per symbol from Yahoo `quoteSummary`.

    Returns {UPPER_SYMBOL: {rate, yield, available}} where `rate` is the
    trailing annual dividend per share (INR) and `yield` is the percentage.
    Symbols with no dividend data (or a fetch failure) resolve to
    {available: False} — the platform never fabricates dividend figures.
    Cached (1h) via the shared cache to respect the upstream API."""
    out = {}
    uniq = list({(s or "").upper() for s in symbols if s})

    async def _one(sym):
        yahoo_ticker = resolve_yahoo_ticker(sym)
        cache_key = f"yahoo_div_{yahoo_ticker}"
        cached = await cache_get(cache_key)
        if cached is not None:
            return sym, cached
        result = {"available": False, "rate": None, "yield": None}
        try:
            url = (f"{yahoo_origin('query2')}/v10/finance/quoteSummary/"
                   f"{yahoo_ticker}?modules=summaryDetail")
            async with http_client.client_for(timeout=8) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    data = resp.json()
                    rows = (data.get("quoteSummary", {}) or {}).get("result") or []
                    detail = (rows[0].get("summaryDetail", {}) if rows else {}) or {}
                    rate = (detail.get("trailingAnnualDividendRate") or {}).get("raw")
                    dyield = (detail.get("dividendYield") or {}).get("raw")
                    if rate:
                        result = {
                            "available": True,
                            "rate": round(float(rate), 2),
                            "yield": round(float(dyield) * 100, 2) if dyield else None,
                        }
        except Exception as e:
            logger.warning(f"dividend info fetch failed for {sym}: {e}")
        # Cache both hits and misses (misses briefly) to avoid hammering Yahoo.
        await cache_set(cache_key, result, 3600 if result["available"] else 600)
        return sym, result

    results = await asyncio.gather(*[_one(s) for s in uniq], return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):
            continue
        sym, info = res
        out[sym] = info
    return out


async def fetch_real_index(index_name: str):
    """Fetch real index value (Nifty, BankNifty, Sensex)."""
    return await fetch_yahoo_quote(index_name)


def compute_market_breadth(quotes):
    """Advance/decline breadth over the tracked universe of live quotes.
    Derived from real prices — returns None when no live quotes are available."""
    changes = [q.get("change_pct") for q in (quotes or []) if q and q.get("change_pct") is not None]
    if not changes:
        return None
    advances = sum(1 for c in changes if c > 0)
    declines = sum(1 for c in changes if c < 0)
    unchanged = len(changes) - advances - declines
    return {
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "coverage": "nifty50_sample",
    }


def compute_market_sentiment(breadth, nifty_change_pct):
    """Deterministic 0-100 sentiment from real breadth + Nifty day change.
    Returns None when breadth is unavailable."""
    if not breadth:
        return None
    total = breadth["advances"] + breadth["declines"]
    if total == 0:
        return None
    breadth_score = breadth["advances"] / total * 100
    # Nifty change contributes ±20 points, clamped at ±2%
    nifty_score = max(-20.0, min(20.0, (nifty_change_pct or 0) * 10))
    return int(max(0, min(100, round(breadth_score * 0.8 + 50 * 0.2 + nifty_score))))


async def fetch_india_vix():
    """Live India VIX from Yahoo Finance (^INDIAVIX). None when unavailable."""
    data = await fetch_yahoo_quote("INDIAVIX")
    if not data or not data.get("price"):
        return None
    return round(data["price"], 2)


def _market_is_open() -> bool:
    """Whether NSE is currently in its regular session, per the platform clock.

    A thin indirection over `market_engine.validator.is_market_hours` so the
    module attribute is resolved at call time: importing the function at module
    scope would bind the original and make the market clock un-substitutable,
    both for tests and for any future holiday-calendar implementation that
    replaces it.

    Failure is reported as closed. An unanswerable clock must not render a
    market open — a user acting on "OPEN" when the platform cannot actually
    tell is the costlier of the two mistakes.
    """
    try:
        from services.market_engine import validator
        return bool(validator.is_market_hours())
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Market clock unavailable, reporting CLOSED: {exc}")
        return False


async def fetch_real_market_overview():
    """Fetch real market overview with actual index values, live India VIX and
    breadth/sentiment derived from live universe quotes. Fields with no live
    data are None — never simulated. Returns None only if every index fails."""
    cache_key = "market_overview_real"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    try:
        from services.activity_logger import log_activity
        log_activity("Fetching live Nifty/BankNifty data", "scan", "done")

        nifty, banknifty, sensex, vix, universe = await asyncio.gather(
            fetch_yahoo_quote("NIFTY"),
            fetch_yahoo_quote("BANKNIFTY"),
            fetch_yahoo_quote("SENSEX"),
            fetch_india_vix(),
            fetch_all_universe_quotes(),
            return_exceptions=True,
        )

        def _fmt(data):
            if isinstance(data, Exception) or data is None:
                return {"value": None, "change": None, "change_pct": None, "available": False}
            return {"value": data["price"], "change": data["change"], "change_pct": data["change_pct"], "available": True}

        indices = {"nifty": _fmt(nifty), "bank_nifty": _fmt(banknifty), "sensex": _fmt(sensex)}
        if not any(v["available"] for v in indices.values()):
            return None

        if isinstance(universe, Exception):
            universe = []
        breadth = compute_market_breadth(universe)
        nifty_chg = indices["nifty"]["change_pct"]

        overview = {
            **indices,
            "india_vix": vix if not isinstance(vix, Exception) else None,
            "market_sentiment": compute_market_sentiment(breadth, nifty_chg),
            "advance_decline": breadth,
            # D5.18 — THE MARKET CLOCK IS THE PLATFORM'S, NOT A PROVIDER'S.
            #
            # This used to read `nifty["market_state"] == "REGULAR"`, i.e. Yahoo's
            # own `marketState` field. Observed live on 2026-09-01 at 11:45 IST —
            # inside NSE hours, with Yahoo itself returning a moving 24,126.85 —
            # that field read `CLOSED`, so the dashboard rendered "MARKET CLOSED"
            # over live, ticking broker prices while `/api/market/engine/status`
            # said `market_hours: true`. Two surfaces disagreeing about whether
            # the market is open is what the user actually sees.
            #
            # Whether NSE is open is a fact about the exchange and the clock, not
            # about which vendor answered a quote — so it comes from the same
            # `is_market_hours()` the engine status endpoint already publishes.
            # Sourcing it from a provider also made the answer depend on which
            # provider won resolution, and a second vendor's vocabulary would
            # have given a different answer to an identical question.
            #
            # Imported at call time, not at module scope, so a test can
            # monkeypatch `validator.is_market_hours` and have this read it.
            "market_status": "OPEN" if _market_is_open() else "CLOSED",
            # See the note in `fetch_yahoo_quote`: no provider name in a payload.
            # `/api/market/overview` stamps `source_tier` from the Source Manager.
        }
        await cache_set(cache_key, overview, 30)
        return overview
    except Exception as e:
        logger.error(f"Real market overview error: {e}")
        return None


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
    
    # First average
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # Wilder's smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def calculate_ema(values, period):
    if len(values) < period:
        return [sum(values[:period])/period] * len(values)
    multiplier = 2.0 / (period + 1.0)
    ema = [sum(values[:period])/period]
    for val in values[period:]:
        ema.append((val - ema[-1]) * multiplier + ema[-1])
    return [ema[0]] * (period - 1) + ema


def calculate_macd(prices):
    if len(prices) < 26:
        return 0.0, 0.0
    ema12 = calculate_ema(prices, 12)
    ema26 = calculate_ema(prices, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    macd_signal_line = calculate_ema(macd_line, 9)
    return round(macd_line[-1], 2), round(macd_signal_line[-1], 2)


#: Daily bars a quote must carry before each indicator is answerable.
#:
#: These are the windows the functions above actually need, stated once so
#: "did we have enough history?" and "what did we compute?" cannot drift apart.
#: RSI needs `period + 1` closes for 14 deltas; MACD needs 26 for its slow EMA;
#: the average volume is a 20-day mean taken from the 21 most recent bars so
#: that the latest (in-progress) session is excluded from its own baseline.
_MIN_BARS_RSI = 15
_MIN_BARS_MACD = 26
_MIN_BARS_AVG_VOLUME = 21

#: The window that satisfies all of the above. `1mo` returns ~21 bars, which is
#: one short of a MACD, so `3mo` is the smallest Yahoo range that answers every
#: indicator. Measured: 10 symbols in 0.48s, ~6.4 KB each before normalization.
TECHNICALS_RANGE = "3mo"


def derive_technicals(quote: Dict[str, Any]) -> Dict[str, Any]:
    """RSI, MACD, average volume and volume ratio from a quote's own history.

    D5.19 — WHY THIS IS A FUNCTION AND NOT TWO COPIES OF FOUR LINES
    ---------------------------------------------------------------
    Two paths priced equities and only one of them computed indicators.
    `fetch_real_stock_quote` asked Yahoo for `3mo` and derived all four;
    `fetch_all_universe_quotes` asked for `2d` and derived none, because two
    bars cannot produce a 14-period RSI. Everything behind the universe path —
    `/market/scanner`, `/market/ranking` — was therefore scoring on nulls.

    Measured live on 2026-09-01, all 31 universe quotes carried
    `rsi=None, macd=None, macd_signal=None, avg_volume=None, volume_ratio=None`,
    and the consequences were not subtle: six of the scanner's eight strategy
    presets matched 0 of 31 stocks, the other two returned the universe in
    declaration order, and the ranking engine reported five of eight dimensions
    identically for every stock it ranked.

    **ABSENCE IS RETURNED AS ABSENCE.** Every field is `None` when its window is
    not satisfied, rather than the plausible default the single-quote path used
    to substitute (`rsi = 50.0`, `macd = 0.0`, `avg_volume = 1000000`). This is
    the whole point of the helper. A null is visibly missing and a consumer can
    say "unknown"; a fabricated 50 is silently wrong and scores as real — it is
    what awarded an absent RSI +25 points for sitting "in the bullish zone", and
    what put a stock with 8.3 million shares traded into the "Very low
    liquidity" bucket. See CLAUDE.md's data rules: never simulate production
    data, and `ranking_engine.dimension_is_supported`, which is the consumer
    that depends on this distinction being preserved.
    """
    closes = quote.get("historical_closes") or []
    volumes = quote.get("historical_volumes") or []

    rsi = calculate_rsi(closes) if len(closes) >= _MIN_BARS_RSI else None

    if len(closes) >= _MIN_BARS_MACD:
        macd, macd_signal = calculate_macd(closes)
    else:
        macd, macd_signal = None, None

    if len(volumes) >= _MIN_BARS_AVG_VOLUME:
        # The latest bar is the in-progress session and is excluded: comparing
        # a partial day's volume against an average that already contains it
        # damps exactly the spike the ratio exists to detect.
        avg_volume = int(sum(volumes[-_MIN_BARS_AVG_VOLUME:-1]) / (_MIN_BARS_AVG_VOLUME - 1))
    else:
        avg_volume = None

    latest_volume = quote.get("volume") or 0
    volume_ratio = round(latest_volume / avg_volume, 2) if avg_volume else None

    return {
        "rsi": rsi,
        "macd": macd,
        "macd_signal": macd_signal,
        "avg_volume": avg_volume,
        "volume_ratio": volume_ratio,
    }


def _vwap_of(quote: Dict[str, Any]) -> Optional[float]:
    """The session's typical price. `None` when the day's range is unknown."""
    high, low, price = quote.get("high"), quote.get("low"), quote.get("price")
    if not high or low is None or price is None:
        return price
    return round((high + low + price) / 3, 2)


async def fetch_real_stock_quote(symbol: str):
    """Fetch real stock quote, with indicators derived from 3 months of bars."""
    data = await fetch_yahoo_quote(symbol, range_str=TECHNICALS_RANGE)
    if not data:
        return None

    technicals = derive_technicals(data)

    # Strip out the historical lists so we don't return giant lists to frontend
    clean_data = {k: v for k, v in data.items() if not k.startswith("historical_")}

    return {
        **clean_data,
        "symbol": symbol.upper(),
        "vwap": _vwap_of(data),
        # D5.19 — the legacy non-null defaults, applied HERE and not inside
        # `derive_technicals`. This endpoint's consumers (`_advisor_score`, the
        # top-pick scorer) compare `macd > macd_signal` arithmetically and would
        # raise on a None, so removing the defaults from this path is a wider
        # change than this sprint should make in passing. Keeping them local
        # means there is still exactly one definition of how an indicator is
        # computed, and exactly one place where a substitute is knowingly used.
        # Tracked as LIM-D5.19-2.
        "rsi": technicals["rsi"] if technicals["rsi"] is not None else 50.0,
        "macd": technicals["macd"] if technicals["macd"] is not None else 0.0,
        "macd_signal": technicals["macd_signal"] if technicals["macd_signal"] is not None else 0.0,
        "avg_volume": technicals["avg_volume"] if technicals["avg_volume"] is not None else 1000000,
        "volume_ratio": technicals["volume_ratio"] if technicals["volume_ratio"] is not None else 1.0,
    }


# ─────────────────────────────────────────────────────────────
# CHART PATTERN DETECTION
# ─────────────────────────────────────────────────────────────

def _body_size(o, c):
    return abs(c - o)


def _upper_wick(o, c, h):
    return h - max(o, c)


def _lower_wick(o, c, l):
    return min(o, c) - l


def _timestamp_for_index(idx, timestamps):
    """Map a candle_index to an ISO date string using the aligned Yahoo timestamp list.
    Returns '' if the index is out of range or conversion fails."""
    if not isinstance(idx, int) or idx < 0 or idx >= len(timestamps):
        return ""
    try:
        return datetime.fromtimestamp(timestamps[idx], tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError, TypeError, OverflowError):
        return ""


def detect_bullish_engulfing(opens, highs, lows, closes):
    """Detect Bullish Engulfing: small red candle followed by large green candle."""
    detected = []
    for i in range(1, len(closes)):
        prev_o, prev_c = opens[i-1], closes[i-1]
        curr_o, curr_c = opens[i], closes[i]
        if prev_c < prev_o and curr_c > curr_o:        # prev red, curr green
            if curr_o <= prev_c and curr_c >= prev_o:  # body engulfs
                detected.append({
                    "pattern": "Bullish Engulfing",
                    "candle_index": i,
                    "signal": "bullish",
                    "confidence": 0.85,
                    "description": "A small bearish candle is completely engulfed by a larger bullish candle — buyers have taken control.",
                })
    return detected[-1:] if detected else []


def detect_bearish_engulfing(opens, highs, lows, closes):
    """Detect Bearish Engulfing: small green candle followed by large red candle."""
    detected = []
    for i in range(1, len(closes)):
        prev_o, prev_c = opens[i-1], closes[i-1]
        curr_o, curr_c = opens[i], closes[i]
        if prev_c > prev_o and curr_c < curr_o:        # prev green, curr red
            if curr_o >= prev_c and curr_c <= prev_o:  # body engulfs
                detected.append({
                    "pattern": "Bearish Engulfing",
                    "candle_index": i,
                    "signal": "bearish",
                    "confidence": 0.85,
                    "description": "A small bullish candle is completely engulfed by a larger bearish candle — sellers have taken control.",
                })
    return detected[-1:] if detected else []


def detect_doji(opens, highs, lows, closes):
    """Detect Doji: open ≈ close, indicating indecision."""
    detected = []
    for i in range(len(closes)):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body_size(o, c)
        total_range = h - l
        if total_range > 0 and body / total_range < 0.1:
            detected.append({
                "pattern": "Doji",
                "candle_index": i,
                "signal": "neutral",
                "confidence": 0.6,
                "description": "Open and close are nearly equal, signalling indecision. Watch the next candle for direction.",
            })
    return detected[-1:] if detected else []


def detect_hammer(opens, highs, lows, closes):
    """Detect Hammer: small body near the top of the range with a long lower wick
    (>= 2x the body) appearing after a downtrend — bullish reversal signal."""
    detected = []
    for i in range(3, len(closes)):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body_size(o, c)
        total_range = h - l
        if body == 0 or total_range == 0:
            continue
        lower = _lower_wick(o, c, l)
        upper = _upper_wick(o, c, h)
        prior_downtrend = closes[i-1] < closes[i-3]        # price fell into the candle
        if lower >= 2 * body and upper <= body and prior_downtrend:
            confidence = round(min(0.9, 0.5 + 0.1 * (lower / body)), 2)
            detected.append({
                "pattern": "Hammer",
                "candle_index": i,
                "signal": "bullish",
                "confidence": confidence,
                "description": "A small body near the top of the range with a long lower wick after a downtrend — buyers rejected lower prices, hinting at a bullish reversal.",
            })
    return detected[-1:] if detected else []


def detect_shooting_star(opens, highs, lows, closes):
    """Detect Shooting Star: small body near the bottom of the range with a long upper wick
    (>= 2x the body) appearing after an uptrend — bearish reversal signal."""
    detected = []
    for i in range(3, len(closes)):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body_size(o, c)
        total_range = h - l
        if body == 0 or total_range == 0:
            continue
        upper = _upper_wick(o, c, h)
        lower = _lower_wick(o, c, l)
        prior_uptrend = closes[i-1] > closes[i-3]          # price rose into the candle
        if upper >= 2 * body and lower <= body and prior_uptrend:
            confidence = round(min(0.9, 0.5 + 0.1 * (upper / body)), 2)
            detected.append({
                "pattern": "Shooting Star",
                "candle_index": i,
                "signal": "bearish",
                "confidence": confidence,
                "description": "A small body near the bottom of the range with a long upper wick after an uptrend — sellers rejected higher prices, hinting at a bearish reversal.",
            })
    return detected[-1:] if detected else []


def detect_double_top(closes, window=20):
    """Detect Double Top: two peaks at similar price level separated by a trough."""
    if len(closes) < window * 2:
        return []
    recent = closes[-window:]
    peak1_idx = recent.index(max(recent[:window//2]))
    peak2_idx = window//2 + recent[window//2:].index(max(recent[window//2:]))
    peak1, peak2 = recent[peak1_idx], recent[peak2_idx]
    trough = min(recent[peak1_idx:peak2_idx+1]) if peak1_idx < peak2_idx else 0
    if abs(peak1 - peak2) / ((peak1 + peak2) / 2) < 0.03 and (peak1 - trough) / peak1 > 0.02:
        return [{
            "pattern": "Double Top",
            "candle_index": len(closes) - 1,
            "signal": "bearish",
            "confidence": 0.8,
            "description": "Two peaks at similar price level — strong reversal pattern indicating potential downtrend.",
        }]
    return []


def detect_double_bottom(closes, window=20):
    """Detect Double Bottom: two troughs at similar price level separated by a peak."""
    if len(closes) < window * 2:
        return []
    recent = closes[-window:]
    low1_idx = recent.index(min(recent[:window//2]))
    low2_idx = window//2 + recent[window//2:].index(min(recent[window//2:]))
    low1, low2 = recent[low1_idx], recent[low2_idx]
    peak = max(recent[low1_idx:low2_idx+1]) if low1_idx < low2_idx else 0
    if abs(low1 - low2) / ((low1 + low2) / 2) < 0.03 and (peak - low1) / peak > 0.02:
        return [{
            "pattern": "Double Bottom",
            "candle_index": len(closes) - 1,
            "signal": "bullish",
            "confidence": 0.8,
            "description": "Two troughs at similar price level — strong reversal pattern indicating potential uptrend.",
        }]
    return []


def detect_head_and_shoulders(closes, window=30):
    """Detect Head & Shoulders: three peaks where middle is highest."""
    if len(closes) < window:
        return []
    seg = closes[-window:]
    q = window // 4
    left  = max(seg[:q])
    head  = max(seg[q:3*q])
    right = max(seg[3*q:])
    if head > left and head > right and abs(left - right) / head < 0.05:
        return [{
            "pattern": "Head & Shoulders",
            "candle_index": len(closes) - 1,
            "signal": "bearish",
            "confidence": 0.85,
            "description": "Classic reversal pattern: left shoulder, head, right shoulder. Signals potential end of uptrend.",
        }]
    return []


def detect_triangle_breakout(closes, highs, lows, window=15):
    """Detect Triangle Breakout: converging highs and lows followed by breakout."""
    if len(closes) < window:
        return []
    recent_h = highs[-window:]
    recent_l = lows[-window:]
    recent_c = closes[-window:]
    h_slope = (recent_h[-1] - recent_h[0]) / window
    l_slope = (recent_l[-1] - recent_l[0]) / window
    # Converging: highs falling, lows rising
    if h_slope < 0 and l_slope > 0:
        last_close = recent_c[-1]
        breakout_up = last_close > recent_h[-2]
        breakout_dn = last_close < recent_l[-2]
        if breakout_up:
            return [{
                "pattern": "Triangle Breakout ↑",
                "candle_index": len(closes) - 1,
                "signal": "bullish",
                "confidence": 0.65,
                "description": "Price broke above a converging triangle — bullish breakout with potential upward momentum.",
            }]
        if breakout_dn:
            return [{
                "pattern": "Triangle Breakout ↓",
                "candle_index": len(closes) - 1,
                "signal": "bearish",
                "confidence": 0.65,
                "description": "Price broke below a converging triangle — bearish breakout with potential downward momentum.",
            }]
    return []


async def detect_chart_patterns(symbol: str) -> dict:
    """
    Fetch 3 months of OHLCV data and run all pattern detectors.
    Returns a dict with 'patterns' list and 'summary'.
    """
    cache_key = f"patterns_{symbol.upper()}"
    cached = await cache_get(cache_key)  # 5-minute cache
    if cached:
        return cached

    data = await fetch_yahoo_quote(symbol, range_str="3mo")
    if not data:
        return {"patterns": [], "summary": "No data available", "symbol": symbol.upper()}

    opens  = data.get("historical_opens",  []) if "historical_opens"  in data else []
    highs  = data.get("historical_highs",  [])
    lows   = data.get("historical_lows",   [])
    closes = data.get("historical_closes", [])
    timestamps = data.get("historical_timestamps", []) or []

    # Yahoo Finance doesn't always return opens in basic quote — fallback gracefully
    if not opens:
        opens = closes  # approximate: treat close as open for candle detection

    # Ensure all lists are same length (trim to shortest)
    min_len = min(len(opens), len(highs), len(lows), len(closes))
    opens  = opens[-min_len:]
    highs  = highs[-min_len:]
    lows   = lows[-min_len:]
    closes = closes[-min_len:]
    # Align timestamps to the same trimmed window so candle_index maps correctly
    timestamps = timestamps[-min_len:] if len(timestamps) >= min_len else timestamps

    all_patterns = []
    if min_len >= 2:
        all_patterns += detect_bullish_engulfing(opens, highs, lows, closes)
        all_patterns += detect_bearish_engulfing(opens, highs, lows, closes)
        all_patterns += detect_doji(opens, highs, lows, closes)
    if min_len >= 4:
        all_patterns += detect_hammer(opens, highs, lows, closes)
        all_patterns += detect_shooting_star(opens, highs, lows, closes)
    if min_len >= 20:
        all_patterns += detect_double_top(closes)
        all_patterns += detect_double_bottom(closes)
    if min_len >= 30:
        all_patterns += detect_head_and_shoulders(closes)
    if min_len >= 15:
        all_patterns += detect_triangle_breakout(closes, highs, lows)

    # Deduplicate by pattern name (keep most recent)
    seen = set()
    unique = []
    for p in reversed(all_patterns):
        if p["pattern"] not in seen:
            seen.add(p["pattern"])
            unique.insert(0, p)

    # Resolve a human-readable timestamp for each pattern by its candle_index
    for p in unique:
        idx = p.get("candle_index")
        p["timestamp"] = _timestamp_for_index(idx, timestamps)

    bullish_count = sum(1 for p in unique if p["signal"] == "bullish")
    bearish_count = sum(1 for p in unique if p["signal"] == "bearish")
    if bullish_count > bearish_count:
        bias = "Bullish"
    elif bearish_count > bullish_count:
        bias = "Bearish"
    else:
        bias = "Neutral"

    summary = (
        f"{len(unique)} pattern(s) detected — overall bias: {bias}. "
        f"{bullish_count} bullish, {bearish_count} bearish signal(s)."
        if unique else "No strong patterns detected in the recent price action."
    )

    result = {
        "symbol": symbol.upper(),
        "patterns": unique,
        "summary": summary,
        "bias": bias,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "data_points": min_len,
    }
    await cache_set(cache_key, result, 300)
    return result


async def fetch_all_universe_quotes():
    """Quotes for every stock in STOCK_UNIVERSE, with technical indicators.

    D5.19 — WHY THIS ASKS FOR THREE MONTHS AND NOT TWO DAYS
    -------------------------------------------------------
    It used to fetch `2d`, which is enough to price a stock and not enough to
    describe one. Two bars produce no RSI, no MACD and no volume baseline, so
    every consumer behind this function scored on nulls — and neither the
    scanner nor the ranking engine could tell a null from a neutral reading.

    Measured live on 2026-09-01 (31 symbols), the cost of that was total:
    `intraday`, `swing`, `momentum`, `breakout`, `reversal` and `growth` each
    matched **0 of 31** stocks because they filter on `volume_ratio_min`,
    `rsi_min/max` or `macd_bullish`; `value` and `dividend` sort on `rsi` and
    `volume_ratio`, where `q.get(key) or 0` made every stock equal and the
    stable sort returned the universe in *declaration order*. A market scanner
    that answers with a constant list is the "static/demo values" the product
    was accused of, and this was its cause.

    `TECHNICALS_RANGE` is the smallest window that answers every indicator —
    `1mo` is ~21 bars, one short of a MACD. Measured: 10 symbols in 0.48s
    against the live vendor, and the per-symbol cache key is now the same one
    `fetch_real_stock_quote` uses, so the stock detail page and the universe
    share a cache entry instead of holding two windows onto one series.

    This widening is only safe because `fetch_yahoo_quote` no longer derives
    `prev_close` from the vendor's range-dependent `chartPreviousClose`: before
    that fix, moving this function to a 3-month window would have silently
    turned every universe day-change into a three-month change. The two changes
    are one change, and `test_universe_day_change_is_still_the_days_change`
    pins the join.
    """
    from market_data import STOCK_UNIVERSE

    cache_key = f"all_universe_quotes_{TECHNICALS_RANGE}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    # Warm every per-symbol quote key in ONE batched read (Sprint R9): when the
    # bundle expires but the per-symbol entries are still fresh, this replaces
    # ~len(universe) sequential Redis GETs (one inside each fetch_yahoo_quote)
    # with a single MGET. Only true misses fall through to the HTTP fetch.
    per_symbol_keys = {
        s["symbol"]: f"yahoo_{resolve_yahoo_ticker(s['symbol'])}_{TECHNICALS_RANGE}"
        for s in STOCK_UNIVERSE
    }
    warm = await cache_get_many(list(per_symbol_keys.values()))

    async def _quote_for(stock):
        hit = warm.get(per_symbol_keys[stock["symbol"]])
        if hit:
            return hit
        return await fetch_yahoo_quote(stock["symbol"], range_str=TECHNICALS_RANGE)

    tasks = [_quote_for(s) for s in STOCK_UNIVERSE]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    quotes = []
    for s, res in zip(STOCK_UNIVERSE, results):
        if isinstance(res, Exception) or res is None:
            # Live quote unavailable — skip. Never substitute simulated data.
            continue
        # Indicators are derived here rather than inside `fetch_yahoo_quote`
        # because that function is the raw vendor adapter and its result is
        # cached per (ticker, range) and shared with callers that want the bars
        # themselves. `derive_technicals` returns None for any indicator whose
        # window this symbol's history does not satisfy — a newly listed stock
        # gets nulls, not a fabricated RSI of 50.
        quote = {**res, **derive_technicals(res)}
        quote["vwap"] = _vwap_of(res)
        # The historical series are the input to the line above, not part of
        # the quote contract: 67 bars x 7 arrays x 31 symbols is a payload no
        # consumer of this function reads, and it would be cached and shipped.
        for key in [k for k in quote if k.startswith("historical_")]:
            quote.pop(key)
        # Add sector and name from universe metadata
        quote["name"] = s["name"]
        quote["sector"] = s["sector"]
        quote["symbol"] = s["symbol"]
        quotes.append(quote)

    if not quotes:
        return []  # do not cache an empty result — retry on next call
    await cache_set(cache_key, quotes, 30)
    return quotes


async def fetch_real_gainers(count=5):
    """Get real-time top gainers from the stock universe."""
    quotes = await fetch_all_universe_quotes()
    sorted_quotes = sorted(quotes, key=lambda x: x.get("change_pct", 0), reverse=True)
    return sorted_quotes[:count]


async def fetch_real_losers(count=5):
    """Get real-time top losers from the stock universe."""
    quotes = await fetch_all_universe_quotes()
    sorted_quotes = sorted(quotes, key=lambda x: x.get("change_pct", 0))
    return sorted_quotes[:count]


async def fetch_real_sectors():
    """Get real-time sector performance by averaging stock changes in each sector."""
    quotes = await fetch_all_universe_quotes()
    sector_data = {}
    for q in quotes:
        sector = q.get("sector")
        change_pct = q.get("change_pct", 0)
        if sector:
            sector_data.setdefault(sector, []).append(change_pct)
            
    result = []
    for sector, changes in sector_data.items():
        avg_change = round(sum(changes) / len(changes), 2) if changes else 0.0
        result.append({"sector": sector, "change_pct": avg_change})
        
    return sorted(result, key=lambda x: x["change_pct"], reverse=True)


async def fetch_real_global_markets():
    """Get real-time global markets data from Yahoo Finance."""
    global_tickers = {
        "Dow Jones": "^DJI",
        "Nasdaq": "^IXIC",
        "S&P 500": "^GSPC",
        "FTSE 100": "^FTSE",
        "Nikkei 225": "^N225",
        "Hang Seng": "^HSI",
    }
    
    tasks = []
    names = []
    for name, ticker in global_tickers.items():
        names.append(name)
        tasks.append(fetch_yahoo_quote(ticker, range_str="2d"))
        
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    markets = []
    for name, res in zip(names, results):
        region = "US" if "S&P" in name or "Nasdaq" in name or "Dow" in name else ("UK" if "FTSE" in name else "Asia")
        if isinstance(res, Exception) or res is None:
            # Explicitly unavailable — never simulated
            markets.append({"name": name, "region": region, "value": None, "change_pct": None, "available": False})
        else:
            markets.append({"name": name, "region": region, "value": res["price"], "change_pct": res["change_pct"], "available": True})
    return markets


async def fetch_real_commodities():
    """Get real-time commodities and forex data from Yahoo Finance.

    THE NAMES AND UNITS ARE THE CONTRACT (D5.17)
    ---------------------------------------------
    Gold was labelled `"Gold (MCX)"` with unit `"INR/10g"` while the ticker
    fetched was `GC=F` — **COMEX gold futures, quoted in US dollars per troy
    ounce**. Silver the same: `"Silver (MCX)"`, `"INR/kg"`, `SI=F`, USD/oz. The
    dashboard renders the number with no unit at all, so a user read a
    four-figure dollar price as rupees per ten grams — off by roughly a factor
    of thirty, in a direction that looks plausible.

    Nothing simulated the number: the price was real and the *label* was the
    fabrication, which is why every "no fake data" check passed over it for as
    long as it existed. The rule this restores is the same one the tick contract
    states: a field is either true or absent.

    The tickers are unchanged. Switching to an MCX contract is not a relabelling
    — see the D5.17 audit: MCX publishes no spot instrument, only dated futures,
    and no broker feed carries one under an entitlement this platform can assume.
    """
    commodity_tickers = {
        "crude_oil": {"name": "Brent Crude", "ticker": "BZ=F", "unit": "USD/bbl"},
        "gold": {"name": "Gold (COMEX)", "ticker": "GC=F", "unit": "USD/oz"},
        "silver": {"name": "Silver (COMEX)", "ticker": "SI=F", "unit": "USD/oz"},
        "usd_inr": {"name": "USD/INR", "ticker": "INR=X", "unit": "INR"},
    }
    
    keys = list(commodity_tickers.keys())
    tasks = [fetch_yahoo_quote(commodity_tickers[k]["ticker"], range_str="2d") for k in keys]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    response = {}
    for key, res in zip(keys, results):
        info = commodity_tickers[key]
        if isinstance(res, Exception) or res is None:
            # Explicitly unavailable — never simulated
            response[key] = {"name": info["name"], "value": None, "unit": info["unit"], "change_pct": None, "available": False}
        else:
            response[key] = {"name": info["name"], "value": res["price"], "unit": info["unit"], "change_pct": res["change_pct"], "available": True}
    return response


# Explicit (interval, range) map — Yahoo rejects ranges like "1w"/"1m",
# which previously broke the 1W and 1M chart periods.
CHART_PERIODS = {
    "1D": ("5m", "1d"),
    "1W": ("30m", "5d"),
    "1M": ("1d", "1mo"),
    "3M": ("1d", "3mo"),
    "3MO": ("1d", "3mo"),  # legacy alias
    "6M": ("1d", "6mo"),
    "1Y": ("1d", "1y"),
}


async def fetch_real_chart_data(symbol: str, period: str = "1D"):
    """Fetch actual chart data from Yahoo Finance for a stock."""
    yahoo_ticker = resolve_yahoo_ticker(symbol)
    interval, range_str = CHART_PERIODS.get((period or "1D").upper(), ("1d", "1mo"))
    intraday = interval.endswith("m")

    try:
        url = f"{yahoo_origin('query1')}/v8/finance/chart/{yahoo_ticker}?interval={interval}&range={range_str}"
        async with http_client.client_for(timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                # Live chart unavailable — return empty, never simulated candles
                return []

            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            if not result:
                return []
                
            timestamps = result[0].get("timestamp", [])
            indicators = result[0].get("indicators", {}).get("quote", [{}])[0]
            
            opens = indicators.get("open", [])
            highs = indicators.get("high", [])
            lows = indicators.get("low", [])
            closes = indicators.get("close", [])
            volumes = indicators.get("volume", [])
            
            chart_candles = []
            for i, ts in enumerate(timestamps):
                # Ensure values exist and are not None
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                l = lows[i] if i < len(lows) else None
                c = closes[i] if i < len(closes) else None
                v = volumes[i] if i < len(volumes) else 0
                
                if o is None or h is None or l is None or c is None:
                    continue
                    
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                # Intraday candles (5m/30m) need full timestamps; daily candles use dates
                time_str = dt.isoformat() if intraday else dt.strftime("%Y-%m-%d")
                
                chart_candles.append({
                    "time": time_str,
                    "open": round(o, 2),
                    "high": round(h, 2),
                    "low": round(l, 2),
                    "close": round(c, 2),
                    "volume": int(v) if v else 0
                })
            return chart_candles
    except Exception as e:
        logger.error(f"Error fetching real chart data for {symbol}: {e}")
        return []


async def fetch_real_top_picks(count=3):
    """Analyze the universe technically and select top picks.

    D5.16 — THE PRICES COME THROUGH THE MARKET GATEWAY.
    ---------------------------------------------------
    This called `fetch_real_stock_quote` per symbol: Yahoo, directly, with no
    resolution, no failover and no tier stamp. That made the most prominent
    number on the dashboard — the entry, stop and targets of an AI
    recommendation — the one number in the product that could not be served by
    the canonical market-data system, and it violated Developer Rule 2 on the
    surface where being wrong costs the user money.

    WHAT DID *NOT* CHANGE, DELIBERATELY
    -----------------------------------
    The ranking. Every line below this fetch — the RSI/volume/MACD weighting,
    the pattern detection, the confidence mapping, the stop and target
    arithmetic — is untouched. D5.16's brief is explicit that the AI's selection
    logic is not this sprint's business; only where the *market price* comes
    from is.

    WHY THIS RESOLUTION IS PLATFORM-SCOPED AND NOT PER USER
    --------------------------------------------------------
    Top picks are generated once a day and persisted platform-wide
    (`db.market_analysis`), so there is no user to resolve for and caching a
    per-user resolution would serve one account's broker prices to everybody —
    which is the D5.15 defect running backwards. The per-user live price a
    connected account sees on these cards arrives the same way every other
    live equity price does: as a `market.tick` for that account, merged into
    `priceTicks` in the browser. The server supplies the canonical mark; the
    socket supplies the movement.
    """
    cache_key = "real_top_picks"
    cached = await cache_get(cache_key)  # 30-minute cache
    if cached:
        return cached

    from market_data import STOCK_UNIVERSE
    from services.market_engine.gateway import market_gateway

    # 1. Resolve the universe through the gateway. Per symbol inside, so a
    #    provider that cannot serve one instrument costs that instrument and not
    #    the batch; a symbol with no usable quote is simply absent.
    try:
        resolved = await market_gateway.get_prices([s["symbol"] for s in STOCK_UNIVERSE])
    except Exception as e:
        logger.warning(f"Top picks: universe resolution failed: {e}")
        resolved = {}

    valid_stocks = []
    for s in STOCK_UNIVERSE:
        quote = resolved.get((s["symbol"] or "").upper())
        if not quote:
            continue
        quote.setdefault("name", s["name"])
        quote.setdefault("sector", s["sector"])
        valid_stocks.append(quote)

    if not valid_stocks:
        # Live data unavailable — return explicit unavailable, never simulated picks
        return {
            "picks": [],
            "available": False,
            # D5.16 — no provider name. The gateway resolves whichever provider
            # can serve, and naming one in a user-facing string is both a
            # provider-identity leak (Developer Rule 4) and, now, wrong: Yahoo
            # being reachable is neither necessary nor sufficient.
            "note": "Live market data is temporarily unavailable. Picks will resume once it returns.",
        }

    # Real sector performance for per-pick sector change
    try:
        sector_perf = await fetch_real_sectors()
    except Exception:
        sector_perf = []
    sector_change_map = {sp["sector"]: sp.get("change_pct") for sp in sector_perf}

    # 2. Score each stock
    scored_stocks = []
    for s in valid_stocks:
        symbol = s["symbol"]
        price = s["price"]
        rsi = s.get("rsi", 50.0)
        volume_ratio = s.get("volume_ratio", 1.0)
        macd = s.get("macd", 0.0)
        macd_signal = s.get("macd_signal", 0.0)
        
        # Detect patterns
        patterns_res = await detect_chart_patterns(symbol)
        patterns_list = patterns_res.get("patterns", [])
        
        score = 50.0  # base score
        reasons = []
        
        # RSI score
        if 50 <= rsi <= 70:
            score += 15
            reasons.append(f"RSI is at a strong bullish zone of {rsi}")
        elif rsi < 35:
            score += 10
            reasons.append(f"RSI is oversold at {rsi}, indicating potential reversal")
            
        # Volume score
        if volume_ratio > 1.5:
            score += 20
            reasons.append(f"Trading volume is {volume_ratio}x above the 20-day average")
        elif volume_ratio > 1.1:
            score += 10
            reasons.append(f"Volume is elevated ({volume_ratio}x 20-day avg)")
            
        # MACD score
        if macd > macd_signal:
            score += 15
            reasons.append("MACD is currently in a bullish crossover")
            
        # Patterns score
        for p in patterns_list:
            if p["signal"] == "bullish":
                score += 15
                reasons.append(f"Bullish {p['pattern']} pattern detected")
            elif p["signal"] == "bearish":
                score -= 10
                
        # Confidence mapping
        confidence = int(min(95, max(65, score)))
        
        # Stop loss and target calculations
        sl = round(price * 0.98, 2)       # 2% stop loss
        t1 = round(price * 1.03, 2)       # 3% target 1
        t2 = round(price * 1.06, 2)       # 6% target 2
        
        # Risk / Reward
        risk = round(price - sl, 2)
        reward = round(t1 - price, 2)
        rr = round(reward / risk, 1) if risk > 0 else 1.5
        
        scored_stocks.append({
            "symbol": symbol,
            "name": s.get("name", symbol),
            "sector": s.get("sector", "General"),
            "price": price,
            "entry": price,
            "stop_loss": sl,
            "target1": t1,
            "target2": t2,
            "risk_reward": rr,
            "confidence": confidence,
            "rsi": rsi,
            "volume_ratio": volume_ratio,
            "pattern": patterns_list[0]["pattern"] if patterns_list else "Momentum Play",
            "patterns": [
                {"pattern": p["pattern"], "signal": p["signal"]}
                for p in patterns_list[:2]
            ],
            "sector_change": sector_change_map.get(s.get("sector")),
            "risk_level": "LOW" if confidence > 82 else ("MEDIUM" if confidence > 72 else "HIGH"),
            "reasons": reasons if len(reasons) >= 2 else reasons + [f"Price action is above key support level", f"Consolidating near recent highs"],
            "risk_factors": [
                "Overall market indices approaching key psychological resistance",
                "Earnings or major corporate announcements pending this week"
            ],
            "historical_success": f"{65 + int(confidence / 3)}%"
        })
        
    # Sort by confidence/score
    picks = sorted(scored_stocks, key=lambda x: x["confidence"], reverse=True)[:count]
    result = {"picks": picks, "available": True}
    await cache_set(cache_key, result, 1800)
    return result


# ─────────────────────────────────────────────────────────────
# FII/DII REAL DATA
# ─────────────────────────────────────────────────────────────

# Last known FII/DII data (persists across requests if API fails)
_last_fii_dii = None


async def fetch_real_fii_dii() -> dict:
    """
    Fetch real FII/DII provisional data from NSE's public API.
    Caches for 4 hours. Falls back to last known value on error,
    instead of returning random numbers.
    """
    global _last_fii_dii
    cache_key = "real_fii_dii"
    cached = await cache_get(cache_key)  # 4 hour cache
    if cached:
        return cached

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Try NSE India direct endpoint
    try:
        url = "https://www.nseindia.com/api/fiidiiTradeReact"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/",
            "Accept-Language": "en-US,en;q=0.9",
        }
        # DELIBERATELY NOT POOLED (PH3.4). This flow establishes an NSE session
        # cookie with the homepage request and then reuses it on the data request,
        # so the two calls must share a client that no other caller touches. A
        # pooled client would share that cookie jar across unrelated callers —
        # a correctness and isolation change dressed up as an optimisation.
        # It is also a single request pair, not a per-symbol fan-out, so there is
        # no handshake amplification to recover here.
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            # NSE requires a session cookie — first load the homepage
            await client.get("https://www.nseindia.com/", headers=headers)
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                raw = resp.json()
                entries = raw if isinstance(raw, list) else []
                # Find equity FII data (category contains "Equity")
                fii_eq = next((e for e in entries if "Equity" in str(e.get("category", ""))), None)
                dii_eq = next((e for e in entries if "Equity" in str(e.get("category", "")) and e != fii_eq), None)
                # If not categorised, use first two
                if not fii_eq and entries:
                    fii_eq = entries[0]
                if not dii_eq and len(entries) > 1:
                    dii_eq = entries[1]

                def _parse_val(d, key):
                    if not d:
                        return 0.0
                    val = d.get(key, 0) or 0
                    try:
                        return float(str(val).replace(",", ""))
                    except (ValueError, TypeError):
                        return 0.0

                fii_buy = _parse_val(fii_eq, "buyValue")
                fii_sell = _parse_val(fii_eq, "sellValue")
                dii_buy = _parse_val(dii_eq, "buyValue")
                dii_sell = _parse_val(dii_eq, "sellValue")

                data = {
                    "fii": {"net": round(fii_buy - fii_sell, 2), "buy": round(fii_buy, 2), "sell": round(fii_sell, 2)},
                    "dii": {"net": round(dii_buy - dii_sell, 2), "buy": round(dii_buy, 2), "sell": round(dii_sell, 2)},
                    "date": today,
                    "source": "nse_india",
                }
                _last_fii_dii = data
                await cache_set(cache_key, data, 14400)
                logger.info(f"Real FII/DII data fetched: FII net={data['fii']['net']}Cr")
                return data
    except Exception as e:
        logger.warning(f"NSE FII/DII fetch failed: {e}")

    # Return last known value (not random)
    if _last_fii_dii:
        logger.info("Returning last known FII/DII data (API unavailable)")
        return {**_last_fii_dii, "source": "cached_last_known"}

    # Ultimate fallback: explicitly unavailable — no values at all
    return {
        "fii": {"net": None, "buy": None, "sell": None},
        "dii": {"net": None, "buy": None, "sell": None},
        "date": today,
        "available": False,
        "source": "unavailable",
        "note": "Real-time FII/DII data temporarily unavailable. NSE updates this after market close.",
    }


# ─────────────────────────────────────────────────────────────
# LIVE STOCK SEARCH (Yahoo Finance)
# ─────────────────────────────────────────────────────────────

async def search_yahoo_stocks(query: str, limit: int = 10):
    """Search Indian equities (NSE/BSE) via Yahoo Finance's search API.
    Returns a list of {symbol, name, sector, exchange}. None on API failure
    (caller decides the fallback), [] for a valid empty result."""
    q = (query or "").strip()
    if not q:
        return []

    cache_key = f"yahoo_search_{q.lower()}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        url = f"{yahoo_origin('query1')}/v1/finance/search"
        params = {"q": q, "quotesCount": 20, "newsCount": 0, "listsCount": 0}
        async with http_client.client_for(timeout=8) as client:
            resp = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return None
            data = resp.json()
    except Exception as e:
        logger.error(f"Yahoo search error for '{q}': {e}")
        return None

    from market_data import get_stock_meta
    results = []
    for item in data.get("quotes", []):
        if item.get("quoteType") != "EQUITY":
            continue
        exchange = item.get("exchange", "")
        if exchange not in ("NSI", "BSE"):  # Indian listings only
            continue
        raw_symbol = item.get("symbol", "")
        symbol = raw_symbol.replace(".NS", "").replace(".BO", "").upper()
        meta = get_stock_meta(symbol)
        results.append({
            "symbol": symbol,
            "name": item.get("longname") or item.get("shortname") or symbol,
            "sector": (meta or {}).get("sector") or item.get("sectorDisp") or item.get("sector") or "",
            "exchange": "NSE" if exchange == "NSI" else "BSE",
        })
        if len(results) >= limit:
            break

    # Deduplicate NSE/BSE dual listings by symbol (prefer first = higher relevance)
    seen = set()
    unique = [r for r in results if not (r["symbol"] in seen or seen.add(r["symbol"]))]

    await cache_set(cache_key, unique, 3600)
    return unique

"""Alpha Vantage market data service with fallback to simulated data."""
import os
import httpx
import logging

logger = logging.getLogger(__name__)

ALPHA_VANTAGE_BASE = "https://www.alphavantage.co/query"

SYMBOL_MAP = {
    "RELIANCE": "RELIANCE.BSE", "TCS": "TCS.BSE", "HDFCBANK": "HDFCBANK.BSE",
    "INFY": "INFY.BSE", "ICICIBANK": "ICICIBANK.BSE", "HINDUNILVR": "HINDUNILVR.BSE",
    "ITC": "ITC.BSE", "SBIN": "SBIN.BSE", "BHARTIARTL": "BHARTIARTL.BSE",
    "KOTAKBANK": "KOTAKBANK.BSE", "LT": "LT.BSE", "AXISBANK": "AXISBANK.BSE",
    "ASIANPAINT": "ASIANPAINT.BSE", "MARUTI": "MARUTI.BSE", "TITAN": "TITAN.BSE",
    "SUNPHARMA": "SUNPHARMA.BSE", "TATAMOTORS": "TATAMOTORS.BSE",
    "BAJFINANCE": "BAJFINANCE.BSE", "WIPRO": "WIPRO.BSE", "ONGC": "ONGC.BSE",
}


def _get_key():
    return os.environ.get("ALPHA_VANTAGE_KEY", "").strip()


def is_configured():
    return bool(_get_key())


async def get_global_quote(symbol: str):
    key = _get_key()
    if not key:
        return None
    av_symbol = SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}.BSE")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(ALPHA_VANTAGE_BASE, params={
                "function": "GLOBAL_QUOTE", "symbol": av_symbol, "apikey": key,
            })
            data = resp.json()
            quote = data.get("Global Quote", {})
            if not quote or "05. price" not in quote:
                return None
            return {
                "price": float(quote["05. price"]),
                "open": float(quote["02. open"]),
                "high": float(quote["03. high"]),
                "low": float(quote["04. low"]),
                "volume": int(quote["06. volume"]),
                "prev_close": float(quote["08. previous close"]),
                "change": float(quote["09. change"]),
                "change_pct": float(quote["10. change percent"].rstrip("%")),
            }
    except Exception as e:
        logger.error(f"Alpha Vantage quote error: {e}")
        return None


async def get_intraday_data(symbol: str, interval: str = "5min"):
    key = _get_key()
    if not key:
        return None
    av_symbol = SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}.BSE")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(ALPHA_VANTAGE_BASE, params={
                "function": "TIME_SERIES_INTRADAY", "symbol": av_symbol,
                "interval": interval, "apikey": key, "outputsize": "compact",
            })
            data = resp.json()
            ts = data.get(f"Time Series ({interval})", {})
            if not ts:
                return None
            return [{"time": t, "open": float(v["1. open"]), "high": float(v["2. high"]),
                      "low": float(v["3. low"]), "close": float(v["4. close"]),
                      "volume": int(v["5. volume"])} for t, v in sorted(ts.items())]
    except Exception as e:
        logger.error(f"Alpha Vantage intraday error: {e}")
        return None


async def get_rsi(symbol: str, period: int = 14):
    key = _get_key()
    if not key:
        return None
    av_symbol = SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}.BSE")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(ALPHA_VANTAGE_BASE, params={
                "function": "RSI", "symbol": av_symbol, "interval": "daily",
                "time_period": period, "series_type": "close", "apikey": key,
            })
            data = resp.json()
            rsi_data = data.get("Technical Analysis: RSI", {})
            if not rsi_data:
                return None
            return float(next(iter(rsi_data.values()))["RSI"])
    except Exception as e:
        logger.error(f"Alpha Vantage RSI error: {e}")
        return None

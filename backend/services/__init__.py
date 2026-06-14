"""Alpha Vantage market data service with fallback to simulated data."""
import os
import httpx
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ALPHA_VANTAGE_BASE = "https://www.alphavantage.co/query"

# NSE symbol mapping for Alpha Vantage (format: SYMBOL.BSE or SYMBOL.NSE)
SYMBOL_MAP = {
    "RELIANCE": "RELIANCE.BSE",
    "TCS": "TCS.BSE",
    "HDFCBANK": "HDFCBANK.BSE",
    "INFY": "INFY.BSE",
    "ICICIBANK": "ICICIBANK.BSE",
    "HINDUNILVR": "HINDUNILVR.BSE",
    "ITC": "ITC.BSE",
    "SBIN": "SBIN.BSE",
    "BHARTIARTL": "BHARTIARTL.BSE",
    "KOTAKBANK": "KOTAKBANK.BSE",
    "LT": "LT.BSE",
    "AXISBANK": "AXISBANK.BSE",
    "ASIANPAINT": "ASIANPAINT.BSE",
    "MARUTI": "MARUTI.BSE",
    "TITAN": "TITAN.BSE",
    "SUNPHARMA": "SUNPHARMA.BSE",
    "TATAMOTORS": "TATAMOTORS.BSE",
    "BAJFINANCE": "BAJFINANCE.BSE",
    "WIPRO": "WIPRO.BSE",
    "ONGC": "ONGC.BSE",
}


def _get_key():
    return os.environ.get("ALPHA_VANTAGE_KEY", "").strip()


def is_configured():
    """Check if Alpha Vantage API key is configured."""
    return bool(_get_key())


async def get_global_quote(symbol: str) -> dict | None:
    """Get real-time quote from Alpha Vantage. Returns None if unavailable."""
    key = _get_key()
    if not key:
        return None
    av_symbol = SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}.BSE")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(ALPHA_VANTAGE_BASE, params={
                "function": "GLOBAL_QUOTE",
                "symbol": av_symbol,
                "apikey": key,
            })
            data = resp.json()
            quote = data.get("Global Quote", {})
            if not quote or "05. price" not in quote:
                logger.warning(f"Alpha Vantage: No data for {av_symbol}")
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
        logger.error(f"Alpha Vantage quote error for {symbol}: {e}")
        return None


async def get_intraday_data(symbol: str, interval: str = "5min") -> list | None:
    """Get intraday OHLCV data. Returns None if unavailable."""
    key = _get_key()
    if not key:
        return None
    av_symbol = SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}.BSE")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(ALPHA_VANTAGE_BASE, params={
                "function": "TIME_SERIES_INTRADAY",
                "symbol": av_symbol,
                "interval": interval,
                "apikey": key,
                "outputsize": "compact",
            })
            data = resp.json()
            ts_key = f"Time Series ({interval})"
            ts = data.get(ts_key, {})
            if not ts:
                return None
            result = []
            for time_str, vals in sorted(ts.items()):
                result.append({
                    "time": time_str,
                    "open": float(vals["1. open"]),
                    "high": float(vals["2. high"]),
                    "low": float(vals["3. low"]),
                    "close": float(vals["4. close"]),
                    "volume": int(vals["5. volume"]),
                })
            return result
    except Exception as e:
        logger.error(f"Alpha Vantage intraday error for {symbol}: {e}")
        return None


async def get_rsi(symbol: str, period: int = 14) -> float | None:
    """Get RSI indicator."""
    key = _get_key()
    if not key:
        return None
    av_symbol = SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}.BSE")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(ALPHA_VANTAGE_BASE, params={
                "function": "RSI",
                "symbol": av_symbol,
                "interval": "daily",
                "time_period": period,
                "series_type": "close",
                "apikey": key,
            })
            data = resp.json()
            rsi_data = data.get("Technical Analysis: RSI", {})
            if not rsi_data:
                return None
            latest = next(iter(rsi_data.values()))
            return float(latest["RSI"])
    except Exception as e:
        logger.error(f"Alpha Vantage RSI error for {symbol}: {e}")
        return None


async def get_macd(symbol: str) -> dict | None:
    """Get MACD indicator."""
    key = _get_key()
    if not key:
        return None
    av_symbol = SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}.BSE")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(ALPHA_VANTAGE_BASE, params={
                "function": "MACD",
                "symbol": av_symbol,
                "interval": "daily",
                "series_type": "close",
                "apikey": key,
            })
            data = resp.json()
            macd_data = data.get("Technical Analysis: MACD", {})
            if not macd_data:
                return None
            latest = next(iter(macd_data.values()))
            return {
                "macd": float(latest["MACD"]),
                "signal": float(latest["MACD_Signal"]),
                "histogram": float(latest["MACD_Hist"]),
            }
    except Exception as e:
        logger.error(f"Alpha Vantage MACD error for {symbol}: {e}")
        return None

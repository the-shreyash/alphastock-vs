"""Stock detail data service (Sprint 5).

Live company profile, fundamentals, financial statements, peer comparison,
support/resistance levels, trade setup and risk analytics for the stock
detail page. All data comes from Yahoo Finance (quoteSummary + chart APIs).

Hard rule (Sprint 2 legacy): data is LIVE. When a live source is
unreachable, every function returns an explicit
``{"available": False, "note": ...}`` payload — values are never simulated.
Missing Yahoo fields are ``None`` — never invented.

The quoteSummary endpoint requires a cookie + crumb session. We bootstrap
one lazily (GET fc.yahoo.com for cookies → GET v1/test/getcrumb), cache it
in-process for ~1 hour, and retry once with a fresh session on 401/403.
Financial statements additionally fall back to the crumb-less
fundamentals-timeseries endpoint.
"""
import asyncio
import logging
import math
import time
from datetime import datetime, timezone

import httpx

from services.cache import cache_get, cache_set
from services.real_market import (
    resolve_yahoo_ticker,
    fetch_yahoo_quote,
    fetch_real_stock_quote,
    yahoo_origin,
    yahoo_origin_overridden as _origin_overridden,
)

logger = logging.getLogger(__name__)

# ── Cache TTLs (seconds) ─────────────────────────────────────
PROFILE_TTL = 86400        # 24h — company profiles rarely change
FUNDAMENTALS_TTL = 21600   # 6h
FINANCIALS_TTL = 86400     # 24h — statements change quarterly
PEERS_TTL = 300            # 5min
LEVELS_TTL = 600           # 10min
SETUP_TTL = 300            # 5min
RISK_TTL = 900             # 15min

SESSION_TTL = 3600         # crumb/cookie session lifetime (~1h)

VALID_STATEMENTS = ("income", "balance", "cashflow")
VALID_PERIODS = ("annual", "quarterly")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

_QUERY_HOSTS = ("query1", "query2")

TRADE_DISCLAIMER = (
    "Educational analysis derived from live market data — not investment "
    "advice. Markets carry risk; always size positions responsibly and use "
    "stop-losses."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unavailable(symbol: str, note: str) -> dict:
    """Consistent explicit-unavailable payload (never simulated data)."""
    return {"symbol": symbol.upper(), "available": False, "as_of": _now_iso(), "note": note}


# ─────────────────────────────────────────────────────────────
# Yahoo quoteSummary client (cookie + crumb session)
# ─────────────────────────────────────────────────────────────

_session = {"cookies": None, "crumb": None, "ts": 0.0}
_session_lock = asyncio.Lock()


async def _get_crumb_session(force: bool = False):
    """Return (cookies, crumb) for Yahoo quoteSummary, bootstrapping and
    caching an in-process session. Returns (None, None) when Yahoo cannot
    be reached — callers surface an explicit unavailable state."""
    global _session
    now = time.time()
    if not force and _session["crumb"] and now - _session["ts"] < SESSION_TTL:
        return _session["cookies"], _session["crumb"]

    async with _session_lock:
        # Another coroutine may have refreshed while we waited on the lock
        if not force and _session["crumb"] and time.time() - _session["ts"] < SESSION_TTL:
            return _session["cookies"], _session["crumb"]
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=_HEADERS) as client:
                # The cookie-planting request goes to a *different* Yahoo host
                # than the query hosts, so it follows the origin override
                # separately (PH3.5). With an override in place there is no
                # `fc.yahoo.com` to reach and no reason to reach for it — going
                # anyway would be the one outbound request a redirected origin
                # failed to redirect.
                cookie_url = yahoo_origin("fc") if _origin_overridden() else "https://fc.yahoo.com"
                try:
                    # Any response (even 404) sets the required cookies
                    await client.get(cookie_url)
                except httpx.HTTPError:
                    pass
                for host in _QUERY_HOSTS:
                    try:
                        resp = await client.get(f"{yahoo_origin(host)}/v1/test/getcrumb")
                    except httpx.HTTPError:
                        continue
                    crumb = (resp.text or "").strip()
                    if resp.status_code == 200 and crumb and "<" not in crumb:
                        _session = {"cookies": dict(client.cookies), "crumb": crumb, "ts": time.time()}
                        logger.info("Yahoo quoteSummary session bootstrapped")
                        return _session["cookies"], _session["crumb"]
        except Exception as e:
            logger.warning(f"Yahoo crumb bootstrap failed: {e}")
    return None, None


async def fetch_quote_summary(symbol: str, modules: list):
    """Fetch quoteSummary modules for a symbol.

    Returns ``(data, error)``:
      - ``(dict, None)``       → success; dict maps module name → payload
      - ``(None, "not_found")``→ Yahoo does not know this symbol (→ 404)
      - ``(None, "unavailable")`` → live source unreachable (→ available:false)
    """
    ticker = resolve_yahoo_ticker(symbol)

    for attempt in (0, 1):
        cookies, crumb = await _get_crumb_session(force=(attempt == 1))
        if not crumb:
            continue
        params = {"modules": ",".join(modules), "formatted": "false", "crumb": crumb}
        auth_failed = False
        for host in _QUERY_HOSTS:
            url = f"{yahoo_origin(host)}/v10/finance/quoteSummary/{ticker}"
            try:
                async with httpx.AsyncClient(timeout=10, headers=_HEADERS, cookies=cookies) as client:
                    resp = await client.get(url, params=params)
            except Exception as e:
                logger.warning(f"quoteSummary request failed ({host}, {ticker}): {e}")
                continue
            if resp.status_code == 404:
                return None, "not_found"
            if resp.status_code in (401, 403):
                auth_failed = True  # stale crumb — refresh session and retry once
                break
            if resp.status_code != 200:
                continue
            try:
                payload = resp.json()
            except ValueError:
                continue
            qs = payload.get("quoteSummary", {}) or {}
            result = qs.get("result")
            if result:
                return result[0], None
            err_code = str((qs.get("error") or {}).get("code", ""))
            if "not found" in err_code.lower():
                return None, "not_found"
            return None, "unavailable"
        if not auth_failed:
            break
    return None, "unavailable"


def _num(node):
    """Extract a number from a Yahoo field that may be a plain number or a
    ``{"raw": x, "fmt": "..."}`` wrapper. None when absent — never invented."""
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        return node
    if isinstance(node, dict):
        raw = node.get("raw")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return raw
    return None


def _rnd(value, digits: int = 2):
    return round(value, digits) if isinstance(value, (int, float)) else None


def _pct(value, digits: int = 2):
    """Ratio (0.145) → percentage (14.5). None-safe."""
    return round(value * 100, digits) if isinstance(value, (int, float)) else None


def _crores(value, digits: int = 2):
    """Absolute currency value → ₹ crores. None-safe."""
    return round(value / 1e7, digits) if isinstance(value, (int, float)) else None


# ─────────────────────────────────────────────────────────────
# Pure math helpers (no network — unit-tested)
# ─────────────────────────────────────────────────────────────

def calculate_atr(highs, lows, closes, period: int = 14):
    """Average True Range with Wilder smoothing. None when there is not
    enough history (needs period+1 bars)."""
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    highs, lows, closes = highs[-n:], lows[-n:], closes[-n:]
    true_ranges = []
    for i in range(1, n):
        true_ranges.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 2)


def annualized_volatility(closes):
    """Annualized volatility (%) from daily closes (sample stdev × √252)."""
    if len(closes) < 20:
        return None
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return round(math.sqrt(variance) * math.sqrt(252) * 100, 2)


def calculate_beta(stock_closes, index_closes):
    """Beta of daily stock returns vs index returns (cov / var).
    Series are aligned from the end; None with <30 overlapping bars."""
    n = min(len(stock_closes), len(index_closes))
    if n < 30:
        return None
    s, m = stock_closes[-n:], index_closes[-n:]
    s_ret = [s[i] / s[i - 1] - 1 for i in range(1, n) if s[i - 1] and m[i - 1]]
    m_ret = [m[i] / m[i - 1] - 1 for i in range(1, n) if s[i - 1] and m[i - 1]]
    k = min(len(s_ret), len(m_ret))
    if k < 20:
        return None
    s_ret, m_ret = s_ret[-k:], m_ret[-k:]
    mean_s = sum(s_ret) / k
    mean_m = sum(m_ret) / k
    cov = sum((a - mean_s) * (b - mean_m) for a, b in zip(s_ret, m_ret)) / (k - 1)
    var_m = sum((b - mean_m) ** 2 for b in m_ret) / (k - 1)
    if var_m == 0:
        return None
    return round(cov / var_m, 2)


def align_close_series(stock_data: dict, index_data: dict):
    """Pair two fetch_yahoo_quote close series by trading date.

    Yahoo occasionally returns None closes for one symbol but not the other;
    naive tail alignment then pairs different dates and the daily-return
    streams decorrelate (beta collapses toward 0). Aligning on the candle
    timestamps' UTC dates keeps only genuinely common sessions. Falls back
    to the raw series when either payload lacks aligned timestamps
    (e.g. a pre-existing cached quote)."""
    def _by_date(data):
        closes = data.get("historical_closes") or []
        ts = data.get("historical_close_timestamps") or []
        if not ts or len(ts) != len(closes):
            return None
        return {
            datetime.fromtimestamp(t, tz=timezone.utc).date(): c
            for t, c in zip(ts, closes)
        }

    s_map, m_map = _by_date(stock_data), _by_date(index_data)
    if s_map is None or m_map is None:
        return (
            stock_data.get("historical_closes") or [],
            index_data.get("historical_closes") or [],
        )
    common = sorted(set(s_map) & set(m_map))
    return [s_map[d] for d in common], [m_map[d] for d in common]


def pivot_points(high, low, close):
    """Classic floor-trader pivots from the previous session's H/L/C."""
    p = (high + low + close) / 3
    return {
        "p": round(p, 2),
        "r1": round(2 * p - low, 2),
        "r2": round(p + (high - low), 2),
        "r3": round(high + 2 * (p - low), 2),
        "s1": round(2 * p - high, 2),
        "s2": round(p - (high - low), 2),
        "s3": round(low - 2 * (high - p), 2),
    }


def _cluster_levels(levels, cluster_pct: float = 1.0):
    """Group price levels within cluster_pct% of the running cluster mean.
    Returns [{"level", "touches"}] ranked by touches (strongest first)."""
    if not levels:
        return []
    clusters = []
    for lv in sorted(levels):
        if clusters:
            mean = clusters[-1]["_sum"] / clusters[-1]["touches"]
            if mean and abs(lv - mean) / mean * 100 <= cluster_pct:
                clusters[-1]["_sum"] += lv
                clusters[-1]["touches"] += 1
                continue
        clusters.append({"_sum": lv, "touches": 1})
    out = [{"level": round(c["_sum"] / c["touches"], 2), "touches": c["touches"]} for c in clusters]
    return sorted(out, key=lambda c: c["touches"], reverse=True)


def find_swing_levels(highs, lows, window: int = 2, cluster_pct: float = 1.0):
    """Find swing highs/lows as 5-bar local extrema (window=2 each side),
    clustered within cluster_pct% and ranked by touches.
    Returns (swing_highs, swing_lows) as [{"level", "touches"}]."""
    n = min(len(highs), len(lows))
    highs, lows = highs[-n:], lows[-n:]
    swing_highs, swing_lows = [], []
    for i in range(window, n - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i - window:i + window + 1]):
            swing_lows.append(lows[i])
    return _cluster_levels(swing_highs, cluster_pct), _cluster_levels(swing_lows, cluster_pct)


def max_drawdown(closes):
    """Maximum peak-to-trough drawdown (%) over the series. None-safe."""
    if len(closes) < 2:
        return None
    peak = closes[0]
    mdd = 0.0
    for c in closes:
        peak = max(peak, c)
        if peak:
            mdd = max(mdd, (peak - c) / peak)
    return round(mdd * 100, 2)


# ─────────────────────────────────────────────────────────────
# Company profile
# ─────────────────────────────────────────────────────────────

async def get_stock_profile(symbol: str):
    """Company profile from quoteSummary assetProfile. None → unknown symbol."""
    from market_data import get_stock_meta
    sym = symbol.upper()
    cache_key = f"stock_profile_{sym}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    data, err = await fetch_quote_summary(sym, ["assetProfile"])
    if err == "not_found":
        return None
    profile = (data or {}).get("assetProfile")
    if not profile:
        return _unavailable(sym, "Company profile is temporarily unavailable from the live data source.")

    meta = get_stock_meta(sym) or {}
    result = {
        "symbol": sym,
        "available": True,
        "as_of": _now_iso(),
        "name": meta.get("name"),
        "description": profile.get("longBusinessSummary"),
        "sector": profile.get("sectorDisp") or profile.get("sector") or meta.get("sector"),
        "industry": profile.get("industryDisp") or profile.get("industry"),
        "website": profile.get("website"),
        "employees": profile.get("fullTimeEmployees"),
        "city": profile.get("city"),
        "country": profile.get("country"),
    }
    await cache_set(cache_key, result, PROFILE_TTL)
    return result


# ─────────────────────────────────────────────────────────────
# Fundamentals (grouped)
# ─────────────────────────────────────────────────────────────

async def get_stock_fundamentals(symbol: str):
    """Grouped fundamentals from summaryDetail + defaultKeyStatistics +
    financialData. Missing Yahoo fields are None — never invented."""
    sym = symbol.upper()
    cache_key = f"stock_fundamentals_{sym}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    data, err = await fetch_quote_summary(sym, ["summaryDetail", "defaultKeyStatistics", "financialData"])
    if err == "not_found":
        return None
    if not data:
        return _unavailable(sym, "Live fundamentals are temporarily unavailable from the data source.")

    summary = data.get("summaryDetail") or {}
    stats = data.get("defaultKeyStatistics") or {}
    fin = data.get("financialData") or {}

    result = {
        "symbol": sym,
        "available": True,
        "as_of": _now_iso(),
        "valuation": {
            "pe": _rnd(_num(summary.get("trailingPE"))),
            "forward_pe": _rnd(_num(summary.get("forwardPE")) or _num(stats.get("forwardPE"))),
            "pb": _rnd(_num(stats.get("priceToBook"))),
            "peg": _rnd(_num(stats.get("pegRatio"))),
            "ev_ebitda": _rnd(_num(stats.get("enterpriseToEbitda"))),
        },
        "per_share": {
            "eps": _rnd(_num(stats.get("trailingEps"))),
            "book_value": _rnd(_num(stats.get("bookValue"))),
            "dividend_rate": _rnd(_num(summary.get("dividendRate"))),
            "dividend_yield_pct": _pct(_num(summary.get("dividendYield"))),
        },
        "profitability": {
            "roe_pct": _pct(_num(fin.get("returnOnEquity"))),
            "roa_pct": _pct(_num(fin.get("returnOnAssets"))),
            "gross_margin_pct": _pct(_num(fin.get("grossMargins"))),
            "operating_margin_pct": _pct(_num(fin.get("operatingMargins"))),
            "net_margin_pct": _pct(_num(fin.get("profitMargins"))),
        },
        "growth": {
            "revenue_growth_pct": _pct(_num(fin.get("revenueGrowth"))),
            "earnings_growth_pct": _pct(_num(fin.get("earningsGrowth"))),
            "week_52_change_pct": _pct(_num(stats.get("52WeekChange"))),
        },
        "health": {
            "debt_to_equity": _rnd(_num(fin.get("debtToEquity"))),
            "current_ratio": _rnd(_num(fin.get("currentRatio"))),
            "total_cash_cr": _crores(_num(fin.get("totalCash"))),
            "free_cashflow_cr": _crores(_num(fin.get("freeCashflow"))),
        },
        "market": {
            "market_cap_cr": _crores(_num(summary.get("marketCap"))),
            "beta": _rnd(_num(summary.get("beta"))),
            "shares_outstanding_cr": _crores(_num(stats.get("sharesOutstanding"))),
        },
    }
    await cache_set(cache_key, result, FUNDAMENTALS_TTL)
    return result


# ─────────────────────────────────────────────────────────────
# Financial statements
# ─────────────────────────────────────────────────────────────

_QS_MODULE = {
    ("income", "annual"): "incomeStatementHistory",
    ("income", "quarterly"): "incomeStatementHistoryQuarterly",
    ("balance", "annual"): "balanceSheetHistory",
    ("balance", "quarterly"): "balanceSheetHistoryQuarterly",
    ("cashflow", "annual"): "cashflowStatementHistory",
    ("cashflow", "quarterly"): "cashflowStatementHistoryQuarterly",
}
_QS_LIST_KEY = {
    "income": "incomeStatementHistory",
    "balance": "balanceSheetStatements",
    "cashflow": "cashflowStatements",
}
_QS_ROWS = {
    "income": [
        ("totalRevenue", "Total Revenue"),
        ("costOfRevenue", "Cost of Revenue"),
        ("grossProfit", "Gross Profit"),
        ("totalOperatingExpenses", "Operating Expenses"),
        ("operatingIncome", "Operating Income"),
        ("interestExpense", "Interest Expense"),
        ("incomeBeforeTax", "Pre-tax Income"),
        ("incomeTaxExpense", "Tax Expense"),
        ("netIncome", "Net Income"),
    ],
    "balance": [
        ("totalAssets", "Total Assets"),
        ("totalCurrentAssets", "Current Assets"),
        ("cash", "Cash & Equivalents"),
        ("netReceivables", "Receivables"),
        ("inventory", "Inventory"),
        ("totalLiab", "Total Liabilities"),
        ("totalCurrentLiabilities", "Current Liabilities"),
        ("longTermDebt", "Long-term Debt"),
        ("totalStockholderEquity", "Shareholders' Equity"),
    ],
    "cashflow": [
        ("totalCashFromOperatingActivities", "Operating Cash Flow"),
        ("capitalExpenditures", "Capital Expenditure"),
        ("totalCashflowsFromInvestingActivities", "Investing Cash Flow"),
        ("totalCashFromFinancingActivities", "Financing Cash Flow"),
        ("dividendsPaid", "Dividends Paid"),
        ("changeInCash", "Net Change in Cash"),
    ],
}
# fundamentals-timeseries fallback (crumb-less endpoint)
_TS_ROWS = {
    "income": [
        ("TotalRevenue", "Total Revenue"),
        ("GrossProfit", "Gross Profit"),
        ("OperatingIncome", "Operating Income"),
        ("PretaxIncome", "Pre-tax Income"),
        ("TaxProvision", "Tax Expense"),
        ("NetIncome", "Net Income"),
    ],
    "balance": [
        ("TotalAssets", "Total Assets"),
        ("CurrentAssets", "Current Assets"),
        ("CashAndCashEquivalents", "Cash & Equivalents"),
        ("CurrentLiabilities", "Current Liabilities"),
        ("LongTermDebt", "Long-term Debt"),
        ("StockholdersEquity", "Shareholders' Equity"),
    ],
    "cashflow": [
        ("OperatingCashFlow", "Operating Cash Flow"),
        ("CapitalExpenditure", "Capital Expenditure"),
        ("InvestingCashFlow", "Investing Cash Flow"),
        ("FinancingCashFlow", "Financing Cash Flow"),
        ("FreeCashFlow", "Free Cash Flow"),
    ],
}


def _statement_date(node):
    """endDate may be {"raw": epoch, "fmt": "YYYY-MM-DD"} or a plain epoch."""
    if isinstance(node, dict) and node.get("fmt"):
        return node["fmt"]
    raw = _num(node)
    if raw is not None:
        try:
            return datetime.fromtimestamp(raw, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return None
    return None


def _build_table_from_quote_summary(statements: list, statement: str):
    """quoteSummary statement list → {columns, rows} table (₹ Cr values)."""
    columns = []
    per_column = []
    for st in statements[:5]:
        date = _statement_date(st.get("endDate"))
        if not date:
            continue
        columns.append(date)
        per_column.append(st)
    if not columns:
        return None
    rows = []
    for key, label in _QS_ROWS[statement]:
        values = [_crores(_num(st.get(key))) for st in per_column]
        if all(v is None for v in values):
            continue  # row entirely absent from Yahoo — omit rather than invent
        rows.append({"key": key, "label": label, "values": values})
    return {"columns": columns, "rows": rows} if rows else None


async def _fetch_timeseries_table(ticker: str, statement: str, period: str):
    """Crumb-less fundamentals-timeseries fallback → {columns, rows} or None."""
    keys = [f"{period}{key}" for key, _ in _TS_ROWS[statement]]
    now = int(time.time())
    params = {
        "type": ",".join(keys),
        "period1": now - 5 * 365 * 86400,
        "period2": now,
        "merge": "false",
    }
    for host in _QUERY_HOSTS:
        url = f"{yahoo_origin(host)}/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker}"
        try:
            async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
                resp = await client.get(url, params=params)
            if resp.status_code != 200:
                continue
            results = resp.json().get("timeseries", {}).get("result", [])
        except Exception as e:
            logger.warning(f"fundamentals-timeseries failed ({host}, {ticker}): {e}")
            continue

        by_type = {}  # type → {date: value}
        for item in results:
            ts_type = (item.get("meta", {}).get("type") or [None])[0]
            if not ts_type or ts_type not in item:
                continue
            points = {}
            for point in item.get(ts_type) or []:
                if not point:
                    continue
                date = point.get("asOfDate")
                value = _num(point.get("reportedValue"))
                if date and value is not None:
                    points[date] = value
            if points:
                by_type[ts_type] = points

        all_dates = sorted({d for pts in by_type.values() for d in pts}, reverse=True)[:5]
        if not all_dates:
            continue
        rows = []
        for key, label in _TS_ROWS[statement]:
            points = by_type.get(f"{period}{key}", {})
            values = [_crores(points.get(d)) for d in all_dates]
            if all(v is None for v in values):
                continue
            rows.append({"key": key, "label": label, "values": values})
        if rows:
            return {"columns": all_dates, "rows": rows}
    return None


async def get_stock_financials(symbol: str, statement: str, period: str):
    """Financial statements table. Primary: quoteSummary statement history;
    fallback: fundamentals-timeseries (no crumb). None → unknown symbol."""
    sym = symbol.upper()
    cache_key = f"stock_financials_{sym}_{statement}_{period}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    module = _QS_MODULE[(statement, period)]
    data, err = await fetch_quote_summary(sym, [module])
    if err == "not_found":
        return None

    table = None
    if data:
        statements = ((data.get(module) or {}).get(_QS_LIST_KEY[statement])) or []
        table = _build_table_from_quote_summary(statements, statement)
    if table is None:
        table = await _fetch_timeseries_table(resolve_yahoo_ticker(sym), statement, period)
    if table is None:
        return _unavailable(sym, "Financial statements are temporarily unavailable from the live data source.")

    result = {
        "symbol": sym,
        "available": True,
        "as_of": _now_iso(),
        "statement": statement,
        "period": period,
        "currency": "INR",
        "unit": "₹ Cr",
        **table,
    }
    await cache_set(cache_key, result, FINANCIALS_TTL)
    return result


# ─────────────────────────────────────────────────────────────
# Peers
# ─────────────────────────────────────────────────────────────

async def get_stock_peers(symbol: str):
    """Same-sector peers from STOCK_UNIVERSE with live quotes
    (bounded concurrency, Semaphore(5))."""
    from market_data import STOCK_UNIVERSE, get_stock_meta
    sym = symbol.upper()
    meta = get_stock_meta(sym)
    if not meta:
        return _unavailable(
            sym, "Peer comparison is currently available for Nifty-50 universe stocks only."
        )

    cache_key = f"stock_peers_{sym}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    peers_meta = [s for s in STOCK_UNIVERSE if s["sector"] == meta["sector"] and s["symbol"] != sym]
    semaphore = asyncio.Semaphore(5)

    async def fetch_peer(s):
        async with semaphore:
            quote = await fetch_yahoo_quote(s["symbol"], range_str="2d")
        if not quote:
            return None
        return {
            "symbol": s["symbol"],
            "name": s["name"],
            "sector": s["sector"],
            "price": quote.get("price"),
            "change_pct": quote.get("change_pct"),
            "volume": quote.get("volume"),
        }

    results = await asyncio.gather(*(fetch_peer(s) for s in peers_meta), return_exceptions=True)
    peers = [r for r in results if isinstance(r, dict)]
    if peers_meta and not peers:
        return _unavailable(sym, "Live peer quotes are temporarily unavailable.")

    peers.sort(key=lambda p: p.get("change_pct") or 0, reverse=True)
    result = {
        "symbol": sym,
        "available": True,
        "as_of": _now_iso(),
        "sector": meta["sector"],
        "peers": peers,
    }
    await cache_set(cache_key, result, PEERS_TTL)
    return result


# ─────────────────────────────────────────────────────────────
# Support / resistance levels
# ─────────────────────────────────────────────────────────────

def _strength(touches: int) -> str:
    if touches >= 3:
        return "strong"
    if touches == 2:
        return "moderate"
    return "weak"


def _ohlc_history(data: dict):
    """Aligned (highs, lows, closes) from a fetch_yahoo_quote payload."""
    highs = data.get("historical_highs") or []
    lows = data.get("historical_lows") or []
    closes = data.get("historical_closes") or []
    n = min(len(highs), len(lows), len(closes))
    return highs[-n:], lows[-n:], closes[-n:]


async def get_stock_levels(symbol: str):
    """Pivot points + clustered swing support/resistance from 3-month
    daily history. None → unknown symbol."""
    from market_data import get_stock_meta
    sym = symbol.upper()
    cache_key = f"stock_levels_{sym}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    data = await fetch_yahoo_quote(sym, range_str="3mo")
    if not data:
        if not get_stock_meta(sym):
            return None
        return _unavailable(sym, "Live price history is temporarily unavailable — levels cannot be computed.")

    highs, lows, closes = _ohlc_history(data)
    price = data.get("price")
    if len(closes) < 20 or not price:
        return _unavailable(sym, "Not enough price history yet to compute reliable levels.")

    # Pivots use the last completed session (skip today's forming candle)
    idx = -2 if (data.get("market_state") == "REGULAR" and len(closes) >= 2) else -1
    pivots = pivot_points(highs[idx], lows[idx], closes[idx])

    swing_highs, swing_lows = find_swing_levels(highs, lows)

    def to_entries(clusters, side):
        entries = []
        for c in clusters:
            level = c["level"]
            if side == "support" and level >= price:
                continue
            if side == "resistance" and level <= price:
                continue
            entries.append({
                "level": level,
                "touches": c["touches"],
                "strength": _strength(c["touches"]),
                "distance_pct": round((level - price) / price * 100, 2),
            })
        # nearest to price first
        entries.sort(key=lambda e: abs(e["distance_pct"]))
        return entries[:4]

    result = {
        "symbol": sym,
        "available": True,
        "as_of": _now_iso(),
        "price": price,
        "pivot": pivots,
        "supports": to_entries(swing_lows + swing_highs, "support"),
        "resistances": to_entries(swing_highs + swing_lows, "resistance"),
    }
    await cache_set(cache_key, result, LEVELS_TTL)
    return result


# ─────────────────────────────────────────────────────────────
# Trade setup
# ─────────────────────────────────────────────────────────────

MIN_RISK_REWARD = 1.5


async def get_trade_setup(symbol: str):
    """Education-first trade setup from live levels + ATR(14) + RSI/MACD/VWAP.
    Setups with risk:reward below 1.5 are rejected (setup: null + note).
    None → unknown symbol."""
    from market_data import get_stock_meta
    sym = symbol.upper()
    cache_key = f"stock_trade_setup_{sym}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    quote = await fetch_real_stock_quote(sym)
    levels = await get_stock_levels(sym)
    if not quote or levels is None:
        if not get_stock_meta(sym):
            return None
        return _unavailable(sym, "Live market data is temporarily unavailable — no trade setup can be computed.")
    if not levels.get("available"):
        return _unavailable(sym, levels.get("note", "Levels unavailable — no trade setup can be computed."))

    history = await fetch_yahoo_quote(sym, range_str="3mo")
    highs, lows, closes = _ohlc_history(history or {})
    atr = calculate_atr(highs, lows, closes)
    if atr is None:
        return _unavailable(sym, "Not enough price history to compute ATR — no trade setup available.")

    price = quote["price"]
    rsi = quote.get("rsi")
    macd, macd_signal = quote.get("macd"), quote.get("macd_signal")
    vwap = quote.get("vwap")
    volume_ratio = quote.get("volume_ratio")

    bull, bear = [], []
    if macd is not None and macd_signal is not None:
        (bull if macd > macd_signal else bear).append(
            f"MACD ({macd}) is {'above' if macd > macd_signal else 'below'} its signal line ({macd_signal}) — "
            f"{'bullish' if macd > macd_signal else 'bearish'} momentum"
        )
    if rsi is not None:
        if rsi >= 55:
            bull.append(f"RSI at {rsi} shows buyers in control (55–70 is a strength zone)")
        elif rsi <= 45:
            bear.append(f"RSI at {rsi} shows sellers in control (below 45 signals weakness)")
    if vwap:
        (bull if price > vwap else bear).append(
            f"Price ₹{price} is trading {'above' if price > vwap else 'below'} VWAP ₹{vwap} — "
            f"{'buyers' if price > vwap else 'sellers'} paid the average"
        )

    if len(bull) >= 2 and len(bull) > len(bear):
        bias = "long"
    elif len(bear) >= 2 and len(bear) > len(bull):
        bias = "short"
    else:
        bias = "neutral"

    supports = levels.get("supports") or []
    resistances = levels.get("resistances") or []
    reasoning = bull + bear
    setup = None
    note = None

    def _round2(v):
        return round(v, 2)

    if bias == "neutral":
        note = "Signals are mixed — no high-conviction setup right now. Wait for indicator alignment."
    elif bias == "long":
        if not supports or not resistances:
            note = "No clean support/resistance structure nearby — skipping the setup rather than forcing one."
        else:
            entry = price
            stop_loss = _round2(supports[0]["level"] - 0.5 * atr)
            target_1 = resistances[0]["level"]
            target_2 = resistances[1]["level"] if len(resistances) > 1 else _round2(entry + 2 * (target_1 - entry))
            risk = entry - stop_loss
            reward = target_1 - entry
            rr = round(reward / risk, 2) if risk > 0 else 0
            if rr < MIN_RISK_REWARD:
                note = (f"Setup rejected: risk:reward {rr} is below the {MIN_RISK_REWARD} minimum. "
                        "The nearest resistance is too close to justify the stop distance.")
            else:
                confidence = min(90, 40 + 15 * len(bull) + 5 * supports[0]["touches"])
                setup = {
                    "entry": _round2(entry), "stop_loss": stop_loss,
                    "target_1": target_1, "target_2": target_2,
                    "risk_reward": rr, "confidence": confidence,
                }
                reasoning.append(
                    f"Stop-loss ₹{stop_loss} sits 0.5×ATR below the nearest support "
                    f"₹{supports[0]['level']} ({supports[0]['touches']} touches) to avoid noise stop-outs"
                )
                reasoning.append(f"Targets are the next resistance zones: ₹{target_1} and ₹{target_2}")
    else:  # short
        if not resistances or not supports:
            note = "No clean support/resistance structure nearby — skipping the setup rather than forcing one."
        else:
            entry = price
            stop_loss = _round2(resistances[0]["level"] + 0.5 * atr)
            target_1 = supports[0]["level"]
            target_2 = supports[1]["level"] if len(supports) > 1 else _round2(entry - 2 * (entry - target_1))
            risk = stop_loss - entry
            reward = entry - target_1
            rr = round(reward / risk, 2) if risk > 0 else 0
            if rr < MIN_RISK_REWARD:
                note = (f"Setup rejected: risk:reward {rr} is below the {MIN_RISK_REWARD} minimum. "
                        "The nearest support is too close to justify the stop distance.")
            else:
                confidence = min(90, 40 + 15 * len(bear) + 5 * resistances[0]["touches"])
                setup = {
                    "entry": _round2(entry), "stop_loss": stop_loss,
                    "target_1": target_1, "target_2": target_2,
                    "risk_reward": rr, "confidence": confidence,
                }
                reasoning.append(
                    f"Stop-loss ₹{stop_loss} sits 0.5×ATR above the nearest resistance "
                    f"₹{resistances[0]['level']} ({resistances[0]['touches']} touches)"
                )
                reasoning.append(f"Targets are the next support zones: ₹{target_1} and ₹{target_2}")

    watch = [
        f"Volume vs 20-day average (currently {volume_ratio}x) — breakouts need volume confirmation"
        if volume_ratio is not None else "Volume confirmation on any breakout attempt",
        "Nifty direction — setups work best with an index tailwind",
    ]
    if setup:
        watch.insert(1, f"A daily close beyond ₹{setup['stop_loss']} invalidates this setup — exit, don't hope")

    result = {
        "symbol": sym,
        "available": True,
        "as_of": _now_iso(),
        "bias": bias,
        "atr": atr,
        "setup": setup,
        "note": note,
        "reasoning": reasoning,
        "watch": watch,
        "disclaimer": TRADE_DISCLAIMER,
    }
    await cache_set(cache_key, result, SETUP_TTL)
    return result


# ─────────────────────────────────────────────────────────────
# Risk analysis
# ─────────────────────────────────────────────────────────────

def _risk_level(score: int) -> str:
    if score < 35:
        return "Low"
    if score < 60:
        return "Moderate"
    return "High"


async def get_risk_analysis(symbol: str):
    """Risk profile from 1-year history: annualized volatility, ATR, beta vs
    ^NSEI, max drawdown, 52-week positioning, weighted 0-100 risk score.
    None → unknown symbol."""
    from market_data import get_stock_meta
    sym = symbol.upper()
    cache_key = f"stock_risk_{sym}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    data = await fetch_yahoo_quote(sym, range_str="1y")
    if not data:
        if not get_stock_meta(sym):
            return None
        return _unavailable(sym, "Live price history is temporarily unavailable — risk metrics cannot be computed.")

    highs, lows, closes = _ohlc_history(data)
    price = data.get("price")
    if len(closes) < 60 or not price:
        return _unavailable(sym, "Not enough price history yet to compute reliable risk metrics.")

    nifty = await fetch_yahoo_quote("NIFTY", range_str="1y")

    volatility = annualized_volatility(closes)
    beta = None
    if nifty:
        stock_aligned, index_aligned = align_close_series(data, nifty)
        beta = calculate_beta(stock_aligned, index_aligned)
    drawdown = max_drawdown(closes)
    atr = calculate_atr(highs, lows, closes)
    atr_pct = round(atr / price * 100, 2) if atr else None
    high_52w = max(closes)
    dist_52w = round((price - high_52w) / high_52w * 100, 2) if high_52w else None

    def _score(value, scale):
        return min(100.0, max(0.0, value / scale * 100)) if value is not None else None

    components = []

    def add_component(factor, value, weight, score, explanation):
        if value is None or score is None:
            return
        components.append({
            "factor": factor,
            "value": value,
            "weight": weight,
            "score": round(score),
            "explanation": explanation,
        })

    add_component(
        "Annualized Volatility", volatility, 30, _score(volatility, 60),
        f"Daily swings annualize to {volatility}%. Above ~30% means large day-to-day moves; size positions smaller.",
    )
    add_component(
        "Beta vs Nifty", beta, 20, _score(abs(beta) if beta is not None else None, 2),
        f"Beta {beta}: the stock tends to move {beta}x the Nifty. Above 1 amplifies index swings both ways."
        if beta is not None else "",
    )
    add_component(
        "Max Drawdown (1Y)", drawdown, 25, _score(drawdown, 50),
        f"Worst peak-to-trough fall in the past year was {drawdown}%. This is the pain a holder had to sit through.",
    )
    add_component(
        "ATR % of Price", atr_pct, 15, _score(atr_pct, 5),
        f"Average daily true range is {atr_pct}% of price (₹{atr}). Wider ranges need wider stops.",
    )
    add_component(
        "Distance from 52W High", dist_52w, 10, _score(abs(dist_52w) if dist_52w is not None else None, 40),
        f"Price is {abs(dist_52w)}% below its 52-week high — deeper discounts often mean a broken trend, not a bargain."
        if dist_52w is not None else "",
    )

    if not components:
        return _unavailable(sym, "Risk components could not be computed from the available history.")

    total_weight = sum(c["weight"] for c in components)
    risk_score = round(sum(c["score"] * c["weight"] for c in components) / total_weight)

    result = {
        "symbol": sym,
        "available": True,
        "as_of": _now_iso(),
        "volatility_annual_pct": volatility,
        "atr": atr,
        "atr_pct": atr_pct,
        "beta": beta,
        "max_drawdown_pct": drawdown,
        "dist_from_52w_high_pct": dist_52w,
        "risk_score": risk_score,
        "risk_level": _risk_level(risk_score),
        "components": components,
    }
    await cache_set(cache_key, result, RISK_TTL)
    return result

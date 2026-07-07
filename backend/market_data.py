"""Static NSE reference metadata (symbols, company names, sectors).

This module intentionally contains NO market data. All prices, indicators,
charts, picks and market metrics come from live sources in
services.real_market (Yahoo Finance / NSE). When live data is unavailable,
endpoints return an explicit "unavailable" payload — never simulated values.
"""

# --- NSE Stock Universe (factual reference metadata only) ---
STOCK_UNIVERSE = [
    {"symbol": "RELIANCE", "name": "Reliance Industries", "sector": "Oil & Gas"},
    {"symbol": "TCS", "name": "Tata Consultancy Services", "sector": "IT"},
    {"symbol": "HDFCBANK", "name": "HDFC Bank", "sector": "Banking"},
    {"symbol": "INFY", "name": "Infosys", "sector": "IT"},
    {"symbol": "ICICIBANK", "name": "ICICI Bank", "sector": "Banking"},
    {"symbol": "HINDUNILVR", "name": "Hindustan Unilever", "sector": "FMCG"},
    {"symbol": "ITC", "name": "ITC Limited", "sector": "FMCG"},
    {"symbol": "SBIN", "name": "State Bank of India", "sector": "Banking"},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel", "sector": "Telecom"},
    {"symbol": "KOTAKBANK", "name": "Kotak Mahindra Bank", "sector": "Banking"},
    {"symbol": "LT", "name": "Larsen & Toubro", "sector": "Infrastructure"},
    {"symbol": "AXISBANK", "name": "Axis Bank", "sector": "Banking"},
    {"symbol": "ASIANPAINT", "name": "Asian Paints", "sector": "Consumer"},
    {"symbol": "MARUTI", "name": "Maruti Suzuki", "sector": "Auto"},
    {"symbol": "TITAN", "name": "Titan Company", "sector": "Consumer"},
    {"symbol": "SUNPHARMA", "name": "Sun Pharma", "sector": "Pharma"},
    {"symbol": "TATAMOTORS", "name": "Tata Motors", "sector": "Auto"},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance", "sector": "Finance"},
    {"symbol": "WIPRO", "name": "Wipro", "sector": "IT"},
    {"symbol": "ONGC", "name": "ONGC", "sector": "Oil & Gas"},
    {"symbol": "NTPC", "name": "NTPC", "sector": "Power"},
    {"symbol": "POWERGRID", "name": "Power Grid Corp", "sector": "Power"},
    {"symbol": "M&M", "name": "Mahindra & Mahindra", "sector": "Auto"},
    {"symbol": "HCLTECH", "name": "HCL Technologies", "sector": "IT"},
    {"symbol": "TATASTEEL", "name": "Tata Steel", "sector": "Metals"},
    {"symbol": "ADANIENT", "name": "Adani Enterprises", "sector": "Conglomerate"},
    {"symbol": "COALINDIA", "name": "Coal India", "sector": "Mining"},
    {"symbol": "DRREDDY", "name": "Dr. Reddy's Labs", "sector": "Pharma"},
    {"symbol": "CIPLA", "name": "Cipla", "sector": "Pharma"},
    {"symbol": "TECHM", "name": "Tech Mahindra", "sector": "IT"},
]

SECTORS = [
    "Banking", "IT", "Pharma", "Auto", "FMCG", "Oil & Gas",
    "Metals", "Power", "Telecom", "Infrastructure", "Finance", "Consumer"
]

_META_BY_SYMBOL = {s["symbol"]: s for s in STOCK_UNIVERSE}


def get_stock_meta(symbol: str):
    """Return factual metadata {symbol, name, sector} for a known symbol, else None."""
    return _META_BY_SYMBOL.get((symbol or "").upper())


def search_stocks(query: str):
    """Metadata-only search over the local universe (fallback for live search)."""
    q = (query or "").upper()
    if not q:
        return []
    return [s for s in STOCK_UNIVERSE if q in s["symbol"] or q in s["name"].upper()][:10]

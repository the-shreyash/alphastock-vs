"""Static NSE reference metadata (symbols, company names, sectors).

This module intentionally contains NO market data. All prices, indicators,
charts, picks and market metrics come from live sources in
services.real_market (Yahoo Finance / NSE). When live data is unavailable,
endpoints return an explicit "unavailable" payload — never simulated values.

WHAT THIS LIST IS, AND WHAT IT IS NOT (D5.17, closing LIM-D5.16-2)
------------------------------------------------------------------
It is an **application universe**: the thirty large caps the dashboard heatmap,
the movers, the scanner and the default feed universe cover. It is not
authoritative reference data — the exchanges' own instrument masters are, and
five of them are downloaded daily by `services/brokers/catalogue.py`.

That distinction became load-bearing in D5.16, which found `TATAMOTORS` here and
in no broker's current master, and correctly declined to delete a symbol merely
because today's catalogues did not carry it. D5.17 established *why* it was
missing, which is the only basis on which a symbol may be removed:

    Tata Motors demerged. `TATAMOTORS` no longer trades under that name at all.
    Verified on 2026-08-31 against all five live masters — Kite, Angel One,
    Dhan, Fyers, Upstox — none of which carries the symbol, and each of which
    carries its two successors: `TMPV` (Tata Motors Passenger Vehicles, NSE
    3456 — the *old* TATAMOTORS token, renamed) and `TMCV` (Tata Motors
    Commercial Vehicles, NSE 759782). Yahoo Finance agrees and is blunter:
    `TATAMOTORS.NS` answers `"No data found, symbol may be delisted"`, while
    `TMPV.NS` and `TMCV.NS` both quote.

So the symbol was not merely absent from broker catalogues; it was unpriceable
by *every* source the platform has, on every surface. It is replaced by its two
successors rather than dropped, because the sector exposure it represented still
exists and a universe that silently loses Auto coverage is a different defect.

THE REAL GAP, WHICH THIS DOES NOT CLOSE
----------------------------------------
There is **no reference-data refresh mechanism**. A corporate action — a
demerger, a renaming, a delisting — reaches this file only when a human notices
a price that stopped moving. One symbol was wrong for months and the symptom was
a permanently blank row. Recorded as LIM-D5.17-1 rather than invented here: a
refresh that reconciles this list against the instrument masters is a sprint,
not a side effect of one.
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
    # Tata Motors demerged — see the module docstring. Two rows, not one:
    # `TMCV` is the entity the masters still name "TATA MOTORS LIMITED".
    {"symbol": "TMPV", "name": "Tata Motors Passenger Vehicles", "sector": "Auto"},
    {"symbol": "TMCV", "name": "Tata Motors Commercial Vehicles", "sector": "Auto"},
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

#: The sector labels the universe uses.
#:
#: `Conglomerate` (ADANIENT) and `Mining` (COALINDIA) were missing — two rows
#: carried a sector this list did not name. Nothing read the list, so nothing
#: broke; but the two are supposed to be one statement, and a consumer that
#: ever filtered on this one would have dropped those stocks silently. Found by
#: `test_every_row_is_canonical_and_complete`, which now holds them together.
SECTORS = [
    "Banking", "IT", "Pharma", "Auto", "FMCG", "Oil & Gas",
    "Metals", "Power", "Telecom", "Infrastructure", "Finance", "Consumer",
    "Conglomerate", "Mining",
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

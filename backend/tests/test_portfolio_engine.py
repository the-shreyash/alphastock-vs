"""Sprint 8 — Portfolio Intelligence engine tests (hermetic, no live market).

Covers the server-side single-source-of-truth analytics in
services/portfolio_engine.py:
  • broker-primary merge of db.holdings + manual db.trades (+ de-dup, paper exclusion)
  • allocation / diversification (HHI) / P&L / movers
  • additive, factor-explained risk score
  • rebalancing suggestions thresholds
  • dividends: real value path + explicit unavailable fallback (never fabricated)
  • performance equity curve: empty-state until ≥2 snapshots, returns math
  • daily snapshot upsert

Live market + dividend fetchers are injected as stubs — no test touches Yahoo.
"""
import asyncio

from tests._fakedb import FakeDB

from services import portfolio_engine as pe


UID = "user-123"


def _run(coro):
    return asyncio.run(coro)


async def _quotes(symbols):
    """Deterministic stub live-quote map for the test universe."""
    book = {
        "RELIANCE": {"price": 2600, "sector": "Energy", "change_pct": 1.2, "rsi": 60, "volume_ratio": 1.1},
        "TCS": {"price": 3800, "sector": "IT", "change_pct": -0.5, "rsi": 45, "volume_ratio": 0.9},
        "INFY": {"price": 1500, "sector": "IT", "change_pct": 0.3, "rsi": 80, "volume_ratio": 3.0},
    }
    return {s.upper(): book.get(s.upper()) for s in symbols}


def _db_with(holdings=None, trades=None, snapshots=None):
    db = FakeDB()
    for h in (holdings or []):
        db.holdings.docs.append(dict(h))
    for t in (trades or []):
        db.trades.docs.append(dict(t))
    for s in (snapshots or []):
        db.portfolio_snapshots.docs.append(dict(s))
    return db


# ---------------------------------------------------------------- merge / holdings

def test_broker_primary_merge_and_dedup():
    db = _db_with(
        holdings=[{
            "user_id": UID, "broker": "zerodha", "symbol": "RELIANCE", "quantity": 10,
            "average_price": 2500, "invested_value": 25000, "market_value": 26000,
            "last_price": 2600, "isin": "INE002A01018",
        }],
        trades=[
            # duplicate of a broker holding — must be dropped (broker wins)
            {"user_id": UID, "symbol": "RELIANCE", "status": "OPEN", "quantity": 5,
             "entry_price": 2400, "stock_name": "Reliance"},
            # manual-only position — kept, tagged source=manual
            {"user_id": UID, "symbol": "TCS", "status": "OPEN", "quantity": 2,
             "entry_price": 3500, "stock_name": "TCS"},
            # paper trade — excluded from the real portfolio entirely
            {"user_id": UID, "symbol": "INFY", "status": "OPEN", "quantity": 4,
             "entry_price": 1400, "stock_name": "Infosys", "is_paper": True},
        ],
    )
    holdings = _run(pe.build_holdings(db, {"_id": UID}, _quotes))
    by_sym = {h["symbol"]: h for h in holdings}

    assert set(by_sym) == {"RELIANCE", "TCS"}          # no INFY (paper), no dup
    assert by_sym["RELIANCE"]["source"] == "broker"
    assert by_sym["RELIANCE"]["quantity"] == 10        # broker qty, not 10+5
    assert by_sym["TCS"]["source"] == "manual"
    # live enrichment applied
    assert by_sym["RELIANCE"]["current_value"] == 26000
    assert by_sym["RELIANCE"]["pnl"] == 1000
    assert by_sym["TCS"]["current_value"] == 7600
    assert by_sym["TCS"]["sector"] == "IT"


def test_holdings_fallback_to_broker_mark_when_quote_missing():
    db = _db_with(holdings=[{
        "user_id": UID, "broker": "zerodha", "symbol": "ZZZ", "quantity": 3,
        "average_price": 100, "invested_value": 300, "market_value": 330, "last_price": 110,
    }])

    async def empty_quotes(symbols):
        return {s.upper(): None for s in symbols}

    holdings = _run(pe.build_holdings(db, {"_id": UID}, empty_quotes))
    h = holdings[0]
    # No live quote → fall back to the LAST REAL broker mark, never fabricated.
    assert h["current_value"] == 330
    assert h["current_price"] == 110
    assert h["pnl"] == 30


# ---------------------------------------------------------------- pure analytics

def _sample_holdings():
    return [
        {"symbol": "RELIANCE", "sector": "Energy", "quantity": 10, "invested": 25000,
         "current_value": 26000, "pnl": 1000, "pnl_pct": 4.0, "rsi": 60},
        {"symbol": "TCS", "sector": "IT", "quantity": 2, "invested": 7000,
         "current_value": 7600, "pnl": 600, "pnl_pct": 8.57, "rsi": 45},
        {"symbol": "HDFCBANK", "sector": "Banking", "quantity": 5, "invested": 8000,
         "current_value": 7400, "pnl": -600, "pnl_pct": -7.5, "rsi": 40},
    ]


def test_allocation_sums_to_100():
    alloc = pe.compute_allocation(_sample_holdings())
    assert round(sum(x["pct"] for x in alloc["by_holding"])) == 100
    assert round(sum(x["pct"] for x in alloc["by_sector"])) == 100
    assert alloc["by_holding"][0]["symbol"] == "RELIANCE"     # largest first
    assert {s["sector"] for s in alloc["by_sector"]} == {"Energy", "IT", "Banking"}


def test_diversification_hhi():
    div = pe.compute_diversification(_sample_holdings())
    assert div["n_holdings"] == 3
    assert div["n_sectors"] == 3
    assert 0 < div["hhi"] <= 1
    # effective holdings = 1/HHI, must not exceed the number of holdings
    assert div["effective_holdings"] <= 3
    assert div["label"] in {"Excellent", "Good", "Moderate", "Concentrated"}


def test_empty_diversification():
    div = pe.compute_diversification([])
    assert div["label"] == "—" and div["n_holdings"] == 0


def test_pnl_realized_and_unrealized():
    pnl = pe.compute_pnl(_sample_holdings(), realized=1234.5)
    assert pnl["invested"] == 40000
    assert pnl["current_value"] == 41000
    assert pnl["unrealized"] == 1000
    assert pnl["realized"] == 1234.5
    assert pnl["total"] == 2234.5
    assert pnl["best"]["symbol"] == "TCS"       # +8.57%
    assert pnl["worst"]["symbol"] == "HDFCBANK"  # -7.5%


def test_movers():
    m = pe.compute_movers(_sample_holdings())
    assert [x["symbol"] for x in m["strong"]] == ["TCS", "RELIANCE"]
    assert m["weak"][0]["symbol"] == "HDFCBANK"


def test_risk_score_factors_explained():
    # Concentrated single name (RELIANCE ~63%) should push risk up with named factors.
    risk = pe.compute_risk_score(_sample_holdings())
    assert 0 <= risk["score"] <= 100
    assert risk["level"] in {"Low", "Elevated", "High"}
    assert risk["factors"], "risk score must expose its contributing factors"
    assert all("detail" in f and f["points"] > 0 for f in risk["factors"])


def test_risk_score_empty():
    assert pe.compute_risk_score([]) == {"score": 0, "level": "—", "factors": []}


def test_suggestions_flag_overexposure():
    holdings = _sample_holdings()
    alloc = pe.compute_allocation(holdings)
    div = pe.compute_diversification(holdings)
    sugg = pe.build_suggestions(holdings, alloc, div)
    text = " ".join(s["text"] for s in sugg)
    assert "Trim RELIANCE" in text            # >30% single-stock guideline
    assert "Diversify out of Energy" in text  # >40% single-sector guideline


def test_suggestions_balanced_portfolio():
    balanced = [
        {"symbol": f"S{i}", "sector": f"Sec{i}", "quantity": 1, "invested": 1000,
         "current_value": 1000, "pnl": 0, "pnl_pct": 0} for i in range(6)
    ]
    alloc = pe.compute_allocation(balanced)
    div = pe.compute_diversification(balanced)
    sugg = pe.build_suggestions(balanced, alloc, div)
    assert len(sugg) == 1 and sugg[0]["tone"] == "positive"


# ---------------------------------------------------------------- dividends

def test_dividends_real_value_path():
    holdings = [{"symbol": "RELIANCE", "quantity": 10, "current_value": 26000},
                {"symbol": "TCS", "quantity": 2, "current_value": 7600}]

    async def fetch_div(symbols):
        return {"RELIANCE": {"available": True, "rate": 9.0, "yield": 0.35},
                "TCS": {"available": True, "rate": 115.0, "yield": 3.0}}

    res = _run(pe.compute_dividends(holdings, fetch_div))
    assert res["available"] is True
    assert res["annual_income"] == round(9.0 * 10 + 115.0 * 2, 2)   # 320.0
    assert res["items"][0]["symbol"] == "TCS"                        # largest first


def test_dividends_unavailable_is_explicit_not_fabricated():
    holdings = [{"symbol": "RELIANCE", "quantity": 10, "current_value": 26000}]

    async def fetch_none(symbols):
        return {"RELIANCE": {"available": False, "rate": None, "yield": None}}

    res = _run(pe.compute_dividends(holdings, fetch_none))
    assert res["available"] is False
    assert res["annual_income"] == 0.0
    assert "unavailable" in res["reason"].lower()


# ---------------------------------------------------------------- performance

def test_performance_empty_state_until_two_snapshots():
    db = _db_with(snapshots=[
        {"user_id": UID, "date": "2026-07-01", "invested": 40000, "current_value": 41000, "pnl": 1000},
    ])
    res = _run(pe.get_performance(db, UID))
    assert res["available"] is False
    assert res["curve"] == []


def test_performance_curve_and_returns():
    db = _db_with(snapshots=[
        {"user_id": UID, "date": "2026-07-01", "invested": 40000, "current_value": 40000, "pnl": 0},
        {"user_id": UID, "date": "2026-07-02", "invested": 40000, "current_value": 42000, "pnl": 2000},
        {"user_id": UID, "date": "2026-07-03", "invested": 40000, "current_value": 41000, "pnl": 1000},
    ])
    res = _run(pe.get_performance(db, UID))
    assert res["available"] is True
    assert res["points"] == 3
    assert res["abs_return"] == 1000                 # 40000 -> 41000
    assert res["pct_return"] == 2.5
    assert res["best_day"]["date"] == "2026-07-02"   # +5%
    assert res["worst_day"]["date"] == "2026-07-03"  # -2.38%


def test_record_snapshot_upserts_today():
    db = _db_with(holdings=[{
        "user_id": UID, "broker": "zerodha", "symbol": "RELIANCE", "quantity": 10,
        "average_price": 2500, "invested_value": 25000, "market_value": 26000, "last_price": 2600,
    }])
    snap = _run(pe.record_snapshot(db, {"_id": UID}, _quotes))
    assert snap is not None
    assert snap["current_value"] == 26000
    assert snap["invested"] == 25000
    stored = db.portfolio_snapshots.docs
    assert len(stored) == 1 and stored[0]["user_id"] == UID


def test_record_snapshot_noop_without_holdings():
    db = _db_with()
    assert _run(pe.record_snapshot(db, {"_id": UID}, _quotes)) is None


# ---------------------------------------------------------------- orchestrator

def test_build_intelligence_bundle_shape():
    db = _db_with(
        holdings=[{
            "user_id": UID, "broker": "zerodha", "symbol": "RELIANCE", "quantity": 10,
            "average_price": 2500, "invested_value": 25000, "market_value": 26000, "last_price": 2600,
        }],
        trades=[
            {"user_id": UID, "symbol": "TCS", "status": "OPEN", "quantity": 2,
             "entry_price": 3500, "stock_name": "TCS"},
            {"user_id": UID, "symbol": "OLD", "status": "CLOSED", "pnl": 500},
        ],
    )

    async def fetch_div(symbols):
        return {s.upper(): {"available": False, "rate": None, "yield": None} for s in symbols}

    bundle = _run(pe.build_intelligence(db, {"_id": UID}, _quotes, health={"at_risk": 0, "alerts": []}, fetch_div=fetch_div))
    for key in ("holdings", "allocation", "diversification", "pnl", "risk",
                "movers", "suggestions", "dividends", "sources", "summary"):
        assert key in bundle, f"missing {key}"
    assert bundle["holdings_count"] == 2
    assert set(bundle["sources"]) == {"broker", "manual"}
    assert bundle["pnl"]["realized"] == 500          # from the closed trade
    assert bundle["dividends"]["available"] is False

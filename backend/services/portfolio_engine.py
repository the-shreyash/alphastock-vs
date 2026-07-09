"""Portfolio Intelligence engine (Sprint 8).

Single server-side source of truth for the Portfolio surface. The same computed
numbers power the Portfolio page, the AI Portfolio Review, the Dashboard summary
card and the morning report — nothing is recomputed in the browser.

Design decision (broker-primary merge): when a broker is connected its synced
holdings (``db.holdings``) ARE the portfolio; manual, non-paper open trades
(``db.trades``) are merged in as a ``source``-tagged layer. A symbol held in both
places keeps the broker row (real settled position) and drops the manual
duplicate so totals are never double-counted. When no broker is connected the
portfolio degrades gracefully to trade-derived holdings.

No fabricated data: when a live quote is missing we fall back to the last real
broker mark (or invested basis) — never a random value — and dividend/performance
surfaces return an explicit ``available: False`` when real data does not exist.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Concentration guidelines (kept identical to the values the frontend used to
# apply client-side, now centralized): a single name above 30% or a single
# sector above 40% of portfolio value flags rebalancing.
STOCK_LIMIT = 30.0
SECTOR_LIMIT = 40.0

# Type of the injected async quote fetcher: symbols -> {UPPER_SYMBOL: quote|None}.
QuotesMapFunc = Callable[[list], Awaitable[dict]]


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Holdings assembly
# --------------------------------------------------------------------------- #
async def build_holdings(db, user: dict, quotes_map_func: QuotesMapFunc) -> list:
    """Assemble the unified, live-enriched holdings list for ``user``.

    ``quotes_map_func`` is an async callable ``(symbols) -> {UPPER: quote|None}``
    (in the app: ``server.real_quotes_map``). Injecting it keeps this module
    decoupled from the live market layer and trivially testable.
    """
    user_id = user["_id"]

    rows: dict = {}
    order: list = []

    # 1) Broker holdings (primary). Aggregate across brokers for the same symbol.
    broker_docs = await db.holdings.find({"user_id": user_id}).to_list(500)
    for d in broker_docs:
        sym = (d.get("symbol") or "").upper()
        if not sym:
            continue
        qty = _num(d.get("quantity"))
        avg = _num(d.get("average_price"))
        invested = d.get("invested_value")
        invested = round(avg * qty, 2) if invested is None else _num(invested)
        if sym in rows:
            r = rows[sym]
            r["quantity"] += qty
            r["invested"] += invested
            r["_broker_value"] += _num(d.get("market_value"))
        else:
            rows[sym] = {
                "symbol": sym,
                "name": d.get("name") or sym,
                "quantity": qty,
                "invested": invested,
                "avg_price": avg,
                "source": "broker",
                "broker": d.get("broker"),
                "isin": d.get("isin"),
                "_broker_value": _num(d.get("market_value")),
                "_broker_price": d.get("last_price"),
            }
            order.append(sym)
    broker_symbols = set(rows.keys())

    # 2) Manual open trades (non-paper), de-duped against broker positions.
    trades = await db.trades.find(
        {"user_id": user_id, "status": "OPEN"}
    ).to_list(500)
    manual: dict = {}
    for t in trades:
        if t.get("is_paper"):
            continue
        sym = (t.get("symbol") or "").upper()
        if not sym or sym in broker_symbols:
            continue  # broker-primary: real settled position wins
        m = manual.setdefault(sym, {
            "symbol": sym,
            "name": t.get("stock_name") or sym,
            "quantity": 0.0,
            "invested": 0.0,
            "avg_price": 0.0,
            "source": "manual",
        })
        m["quantity"] += _num(t.get("quantity"))
        m["invested"] += _num(t.get("entry_price")) * _num(t.get("quantity"))
    for sym, m in manual.items():
        m["avg_price"] = round(m["invested"] / m["quantity"], 2) if m["quantity"] else 0.0
        rows[sym] = m
        order.append(sym)

    # Recompute blended average for aggregated broker rows.
    for sym in broker_symbols:
        r = rows[sym]
        r["avg_price"] = round(r["invested"] / r["quantity"], 2) if r["quantity"] else r.get("avg_price", 0.0)

    # 3) Enrich with live quotes (sector, current price, day change, indicators).
    quotes = await quotes_map_func(order) if order else {}
    result = []
    for sym in order:
        r = rows[sym]
        q = (quotes or {}).get(sym)
        if q and q.get("price") is not None:
            price = _num(q.get("price"))
            r["current_price"] = round(price, 2)
            r["current_value"] = round(price * r["quantity"], 2)
            r["pnl"] = round(r["current_value"] - r["invested"], 2)
            r["pnl_pct"] = round(r["pnl"] / r["invested"] * 100, 2) if r["invested"] else 0.0
            r["sector"] = q.get("sector") or ""
            r["day_change_pct"] = q.get("change_pct")
            r["rsi"] = q.get("rsi")
            r["volume_ratio"] = q.get("volume_ratio")
        else:
            # Live quote unavailable — fall back to the LAST REAL broker mark,
            # else the invested basis. Never fabricate a price or a gain.
            bval = r.get("_broker_value") or 0.0
            r["current_price"] = r.get("_broker_price")
            r["current_value"] = round(bval if bval else r["invested"], 2)
            r["pnl"] = round(r["current_value"] - r["invested"], 2)
            r["pnl_pct"] = round(r["pnl"] / r["invested"] * 100, 2) if r["invested"] else 0.0
            r["sector"] = r.get("sector") or ""
            r["day_change_pct"] = None
            r["rsi"] = None
            r["volume_ratio"] = None
        r.pop("_broker_value", None)
        r.pop("_broker_price", None)
        result.append(r)
    return result


# --------------------------------------------------------------------------- #
# Pure analytics (operate on the enriched holdings list — easy to unit test)
# --------------------------------------------------------------------------- #
def _total_value(holdings: list) -> float:
    return sum(_num(h.get("current_value")) for h in holdings)


def compute_allocation(holdings: list) -> dict:
    """Allocation by holding and by sector, each with value + weight %."""
    total = _total_value(holdings)
    by_holding = sorted(
        [{
            "symbol": h["symbol"],
            "value": round(_num(h.get("current_value")), 2),
            "pct": round(_num(h.get("current_value")) / total * 100, 2) if total else 0.0,
        } for h in holdings],
        key=lambda x: x["value"], reverse=True,
    )
    sector_map: dict = {}
    for h in holdings:
        sec = (h.get("sector") or "").strip() or "Unclassified"
        sector_map[sec] = sector_map.get(sec, 0.0) + _num(h.get("current_value"))
    by_sector = sorted(
        [{
            "sector": s,
            "value": round(v, 2),
            "pct": round(v / total * 100, 2) if total else 0.0,
        } for s, v in sector_map.items()],
        key=lambda x: x["value"], reverse=True,
    )
    return {"total_value": round(total, 2), "by_holding": by_holding, "by_sector": by_sector}


def compute_diversification(holdings: list) -> dict:
    """Herfindahl-Hirschman concentration index + a human diversification label.

    HHI = Σ(weightᵢ²) over portfolio weights; the reciprocal is the "effective
    number of holdings" (1/HHI), a standard concentration measure."""
    total = _total_value(holdings)
    n = len(holdings)
    if not n or not total:
        return {"label": "—", "n_holdings": n, "n_sectors": 0,
                "top_weight": 0.0, "hhi": 0.0, "effective_holdings": 0.0}
    weights = [_num(h.get("current_value")) / total for h in holdings]
    hhi = sum(w * w for w in weights)
    top_weight = round(max(weights) * 100, 1)
    n_sectors = len({(h.get("sector") or "").strip() or "Unclassified" for h in holdings})
    label = ("Excellent" if n >= 8 and top_weight < 25 else
             "Good" if n >= 5 and top_weight < 35 else
             "Moderate" if n >= 3 else "Concentrated")
    return {
        "label": label,
        "n_holdings": n,
        "n_sectors": n_sectors,
        "top_weight": top_weight,
        "hhi": round(hhi, 4),
        "effective_holdings": round(1 / hhi, 1) if hhi else 0.0,
    }


def compute_pnl(holdings: list, realized: float = 0.0) -> dict:
    """Unrealized (live) + realized (closed trades) P&L with best/worst movers."""
    invested = sum(_num(h.get("invested")) for h in holdings)
    current = _total_value(holdings)
    unrealized = round(current - invested, 2)
    ranked = sorted(holdings, key=lambda h: _num(h.get("pnl_pct")))
    best = ranked[-1] if ranked else None
    worst = ranked[0] if ranked else None
    return {
        "invested": round(invested, 2),
        "current_value": round(current, 2),
        "unrealized": unrealized,
        "unrealized_pct": round(unrealized / invested * 100, 2) if invested else 0.0,
        "realized": round(_num(realized), 2),
        "total": round(unrealized + _num(realized), 2),
        "best": {"symbol": best["symbol"], "pnl_pct": round(_num(best.get("pnl_pct")), 2)} if best else None,
        "worst": {"symbol": worst["symbol"], "pnl_pct": round(_num(worst.get("pnl_pct")), 2)} if worst else None,
    }


def compute_movers(holdings: list) -> dict:
    """Top strong (in profit) and weak (in loss) holdings by P&L %."""
    ranked = sorted(holdings, key=lambda h: _num(h.get("pnl_pct")), reverse=True)
    strong = [{"symbol": h["symbol"], "pnl_pct": round(_num(h.get("pnl_pct")), 2)}
              for h in ranked if _num(h.get("pnl_pct")) > 0][:3]
    weak = [{"symbol": h["symbol"], "pnl_pct": round(_num(h.get("pnl_pct")), 2)}
            for h in reversed(ranked) if _num(h.get("pnl_pct")) < 0][:3]
    return {"strong": strong, "weak": weak}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def compute_risk_score(holdings: list, health: Optional[dict] = None,
                       diversification: Optional[dict] = None,
                       allocation: Optional[dict] = None) -> dict:
    """Additive 0-100 risk score (higher = riskier) with transparent factors.

    Every point of risk traces back to a named, explainable factor — honoring
    the AI-transparency rule (never present a number without its reasoning)."""
    if not holdings:
        return {"score": 0, "level": "—", "factors": []}
    div = diversification or compute_diversification(holdings)
    alloc = allocation or compute_allocation(holdings)
    health = health or {}
    factors = []

    def add(points, name, detail):
        if points > 0:
            factors.append({"name": name, "points": round(points, 1), "detail": detail})

    # Single-name concentration (starts biting above 20% weight).
    top = div["top_weight"]
    conc = _clamp((top - 20) * 1.2, 0, 30)
    add(conc, "Single-name concentration",
        f"Largest position is {top:.0f}% of the portfolio.")

    # Sector concentration.
    top_sector = alloc["by_sector"][0] if alloc["by_sector"] else None
    if top_sector:
        sc = _clamp((top_sector["pct"] - 30) * 1.0, 0, 25)
        add(sc, "Sector concentration",
            f"{top_sector['sector']} is {top_sector['pct']:.0f}% of holdings.")

    # Low diversification.
    eff = div["effective_holdings"]
    if eff and eff < 3:
        add(15, "Low diversification", f"Effective holdings ≈ {eff} (very few independent bets).")
    elif eff and eff < 5:
        add(8, "Low diversification", f"Effective holdings ≈ {eff}.")

    # Portfolio-level unrealized drawdown.
    pnl = compute_pnl(holdings)
    if pnl["unrealized_pct"] < 0:
        dd = _clamp(-pnl["unrealized_pct"] * 1.5, 0, 20)
        add(dd, "Unrealized drawdown", f"Portfolio is {pnl['unrealized_pct']:.1f}% underwater.")

    # Positions the health scan flagged as near stop / breached.
    at_risk = _num(health.get("at_risk"))
    if at_risk:
        add(_clamp(at_risk * 10, 0, 20), "Positions at risk",
            f"{int(at_risk)} position(s) near or beyond stop loss.")
    criticals = len([a for a in health.get("alerts", []) if a.get("severity") == "critical"])
    if criticals:
        add(_clamp(criticals * 5, 0, 15), "Critical alerts",
            f"{criticals} critical monitoring alert(s) active.")

    # Overbought / extended names.
    extended = len([h for h in holdings if _num(h.get("rsi")) > 75])
    if extended:
        add(_clamp(extended * 5, 0, 10), "Overbought names",
            f"{extended} holding(s) with RSI > 75 (extended).")

    score = round(_clamp(sum(f["points"] for f in factors), 0, 100))
    level = "Low" if score < 30 else "Elevated" if score < 60 else "High"
    return {"score": score, "level": level,
            "factors": sorted(factors, key=lambda f: f["points"], reverse=True)}


def build_suggestions(holdings: list, allocation: dict, diversification: dict,
                      health: Optional[dict] = None) -> list:
    """Actionable rebalancing suggestions (server version of the old client
    heuristics) merged with actionable AI monitoring alerts."""
    suggestions = []
    for h in allocation.get("by_holding", []):
        if h["pct"] > STOCK_LIMIT:
            suggestions.append({"tone": "warning", "text":
                f"Trim {h['symbol']} — it is {h['pct']:.1f}% of the portfolio, above the "
                f"{int(STOCK_LIMIT)}% single-stock guideline. Concentrated positions amplify "
                f"single-name risk."})
    for s in allocation.get("by_sector", []):
        if s["pct"] > SECTOR_LIMIT:
            suggestions.append({"tone": "warning", "text":
                f"Diversify out of {s['sector']} — {s['pct']:.1f}% of holdings sit in one sector "
                f"(above {int(SECTOR_LIMIT)}%), which raises correlated drawdown risk."})
    if diversification.get("label") == "Concentrated" and diversification.get("n_holdings", 0) < 3:
        suggestions.append({"tone": "warning", "text":
            "Portfolio is concentrated in very few names — adding uncorrelated positions would "
            "reduce single-stock and sector risk."})
    for a in (health or {}).get("alerts", []):
        if a.get("type") in ("RISK_HIGH", "STOP_LOSS_HIT", "SIGNIFICANT_LOSS", "RSI_OVERBOUGHT") and a.get("action"):
            suggestions.append({
                "tone": "critical" if a.get("severity") == "critical" else "warning",
                "text": f"{a['symbol']}: {a['action']}. {a['message']}",
            })
    if not suggestions and holdings:
        suggestions.append({"tone": "positive",
                            "text": "Portfolio is well balanced — no rebalancing needed right now."})
    return suggestions[:8]


# --------------------------------------------------------------------------- #
# Dividends (real data or explicit unavailable — never fabricated)
# --------------------------------------------------------------------------- #
async def _default_fetch_dividends(symbols: list) -> dict:
    from services.real_market import fetch_dividend_info
    return await fetch_dividend_info(symbols)


async def compute_dividends(holdings: list, fetch_div=None) -> dict:
    """Estimated annual dividend income from REAL trailing dividend rates.

    Returns ``available: False`` when no live dividend data exists for the
    current holdings — the platform never invents dividend figures."""
    if not holdings:
        return {"available": False, "reason": "No holdings to analyze.",
                "annual_income": 0.0, "portfolio_yield": 0.0, "items": []}
    fetch_div = fetch_div or _default_fetch_dividends
    try:
        info = await fetch_div([h["symbol"] for h in holdings])
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"dividend fetch failed: {e}")
        info = {}

    items, income, any_available = [], 0.0, False
    for h in holdings:
        d = (info or {}).get(h["symbol"].upper()) or {}
        rate = _num(d.get("rate"))
        if d.get("available") and rate > 0:
            any_available = True
            amt = round(rate * _num(h.get("quantity")), 2)
            income += amt
            items.append({"symbol": h["symbol"], "annual_dividend": amt,
                          "yield": d.get("yield"), "rate": round(rate, 2)})
    if not any_available:
        return {"available": False,
                "reason": "Dividend data unavailable for current holdings.",
                "annual_income": 0.0, "portfolio_yield": 0.0, "items": []}
    total_val = _total_value(holdings)
    return {
        "available": True,
        "annual_income": round(income, 2),
        "portfolio_yield": round(income / total_val * 100, 2) if total_val else 0.0,
        "items": sorted(items, key=lambda x: x["annual_dividend"], reverse=True),
    }


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
async def build_intelligence(db, user: dict, quotes_map_func: QuotesMapFunc,
                             health: Optional[dict] = None, fetch_div=None) -> dict:
    """Assemble the full Portfolio Intelligence bundle consumed by the UI, the
    AI review and the dashboard."""
    holdings = await build_holdings(db, user, quotes_map_func)

    closed = await db.trades.find(
        {"user_id": user["_id"], "status": {"$ne": "OPEN"}}
    ).to_list(500)
    realized = round(sum(_num(t.get("pnl")) for t in closed), 2)

    allocation = compute_allocation(holdings)
    diversification = compute_diversification(holdings)
    pnl = compute_pnl(holdings, realized)
    risk = compute_risk_score(holdings, health, diversification, allocation)
    movers = compute_movers(holdings)
    suggestions = build_suggestions(holdings, allocation, diversification, health)
    dividends = await compute_dividends(holdings, fetch_div)
    sources = sorted({h.get("source") for h in holdings if h.get("source")})

    if holdings:
        summary = (f"{len(holdings)} holding(s) worth ₹{pnl['current_value']:,.0f}, "
                   f"unrealised P&L ₹{pnl['unrealized']:,.0f} "
                   f"({pnl['unrealized_pct']:+.1f}%).")
    else:
        summary = "No holdings yet. Connect a broker or log a trade to build your portfolio."

    return {
        "holdings_count": len(holdings),
        "holdings": holdings,
        "summary": summary,
        "sources": sources,
        "allocation": allocation,
        "diversification": diversification,
        "pnl": pnl,
        "risk": risk,
        "movers": movers,
        "suggestions": suggestions,
        "dividends": dividends,
        "health": health,
    }


# --------------------------------------------------------------------------- #
# Performance / equity curve (built forward from real daily snapshots)
# --------------------------------------------------------------------------- #
_RANGE_DAYS = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "ALL": None}


async def record_snapshot(db, user: dict, quotes_map_func: QuotesMapFunc) -> Optional[dict]:
    """Upsert today's portfolio equity snapshot for ``user`` (idempotent per
    day). Returns the snapshot, or None when the user has no holdings."""
    from datetime import datetime, timezone
    holdings = await build_holdings(db, user, quotes_map_func)
    if not holdings:
        return None
    invested = round(sum(_num(h.get("invested")) for h in holdings), 2)
    current = round(_total_value(holdings), 2)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = {
        "user_id": user["_id"],
        "date": today,
        "invested": invested,
        "current_value": current,
        "pnl": round(current - invested, 2),
        "holdings_count": len(holdings),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.portfolio_snapshots.update_one(
        {"user_id": user["_id"], "date": today},
        {"$set": snapshot}, upsert=True,
    )
    return snapshot


async def get_performance(db, user_id: str, range_key: str = "ALL") -> dict:
    """Equity curve + returns from stored snapshots. Returns ``available: False``
    while fewer than two snapshots exist — the curve is built forward from real
    end-of-day marks, never back-filled with synthetic history."""
    snaps = await db.portfolio_snapshots.find({"user_id": user_id}).to_list(1000)
    snaps = sorted(snaps, key=lambda s: s.get("date", ""))
    days = _RANGE_DAYS.get((range_key or "ALL").upper())
    if days is not None:
        snaps = snaps[-days:]
    if len(snaps) < 2:
        return {"available": False,
                "reason": "Performance history is still being recorded. Your equity curve "
                          "appears after a couple of end-of-day snapshots.",
                "curve": [], "points": len(snaps)}

    curve = [{"date": s["date"], "value": _num(s.get("current_value")),
              "invested": _num(s.get("invested")), "pnl": _num(s.get("pnl"))} for s in snaps]
    first, last = curve[0], curve[-1]
    abs_return = round(last["value"] - first["value"], 2)
    pct_return = round((last["value"] - first["value"]) / first["value"] * 100, 2) if first["value"] else 0.0

    # Best / worst single-day move across the window.
    best_day = worst_day = None
    for prev, cur in zip(curve, curve[1:]):
        if prev["value"]:
            chg = round((cur["value"] - prev["value"]) / prev["value"] * 100, 2)
            if best_day is None or chg > best_day["pct"]:
                best_day = {"date": cur["date"], "pct": chg}
            if worst_day is None or chg < worst_day["pct"]:
                worst_day = {"date": cur["date"], "pct": chg}

    return {
        "available": True,
        "curve": curve,
        "points": len(curve),
        "start_date": first["date"],
        "end_date": last["date"],
        "abs_return": abs_return,
        "pct_return": pct_return,
        "best_day": best_day,
        "worst_day": worst_day,
    }

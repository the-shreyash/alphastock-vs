import random
from datetime import datetime, timezone, timedelta

# --- NSE Stock Universe ---
STOCK_UNIVERSE = [
    {"symbol": "RELIANCE", "name": "Reliance Industries", "sector": "Oil & Gas", "base_price": 2890},
    {"symbol": "TCS", "name": "Tata Consultancy Services", "sector": "IT", "base_price": 3680},
    {"symbol": "HDFCBANK", "name": "HDFC Bank", "sector": "Banking", "base_price": 1740},
    {"symbol": "INFY", "name": "Infosys", "sector": "IT", "base_price": 1520},
    {"symbol": "ICICIBANK", "name": "ICICI Bank", "sector": "Banking", "base_price": 1265},
    {"symbol": "HINDUNILVR", "name": "Hindustan Unilever", "sector": "FMCG", "base_price": 2480},
    {"symbol": "ITC", "name": "ITC Limited", "sector": "FMCG", "base_price": 445},
    {"symbol": "SBIN", "name": "State Bank of India", "sector": "Banking", "base_price": 820},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel", "sector": "Telecom", "base_price": 1640},
    {"symbol": "KOTAKBANK", "name": "Kotak Mahindra Bank", "sector": "Banking", "base_price": 1890},
    {"symbol": "LT", "name": "Larsen & Toubro", "sector": "Infrastructure", "base_price": 3520},
    {"symbol": "AXISBANK", "name": "Axis Bank", "sector": "Banking", "base_price": 1180},
    {"symbol": "ASIANPAINT", "name": "Asian Paints", "sector": "Consumer", "base_price": 2840},
    {"symbol": "MARUTI", "name": "Maruti Suzuki", "sector": "Auto", "base_price": 12400},
    {"symbol": "TITAN", "name": "Titan Company", "sector": "Consumer", "base_price": 3280},
    {"symbol": "SUNPHARMA", "name": "Sun Pharma", "sector": "Pharma", "base_price": 1720},
    {"symbol": "TATAMOTORS", "name": "Tata Motors", "sector": "Auto", "base_price": 960},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance", "sector": "Finance", "base_price": 6840},
    {"symbol": "WIPRO", "name": "Wipro", "sector": "IT", "base_price": 460},
    {"symbol": "ONGC", "name": "ONGC", "sector": "Oil & Gas", "base_price": 265},
    {"symbol": "NTPC", "name": "NTPC", "sector": "Power", "base_price": 380},
    {"symbol": "POWERGRID", "name": "Power Grid Corp", "sector": "Power", "base_price": 310},
    {"symbol": "M&M", "name": "Mahindra & Mahindra", "sector": "Auto", "base_price": 2860},
    {"symbol": "HCLTECH", "name": "HCL Technologies", "sector": "IT", "base_price": 1680},
    {"symbol": "TATASTEEL", "name": "Tata Steel", "sector": "Metals", "base_price": 155},
    {"symbol": "ADANIENT", "name": "Adani Enterprises", "sector": "Conglomerate", "base_price": 2940},
    {"symbol": "COALINDIA", "name": "Coal India", "sector": "Mining", "base_price": 440},
    {"symbol": "DRREDDY", "name": "Dr. Reddy's Labs", "sector": "Pharma", "base_price": 6280},
    {"symbol": "CIPLA", "name": "Cipla", "sector": "Pharma", "base_price": 1480},
    {"symbol": "TECHM", "name": "Tech Mahindra", "sector": "IT", "base_price": 1620},
]

SECTORS = [
    "Banking", "IT", "Pharma", "Auto", "FMCG", "Oil & Gas",
    "Metals", "Power", "Telecom", "Infrastructure", "Finance", "Consumer"
]

CHART_PATTERNS = [
    "Bull Flag Breakout", "Double Bottom", "Cup & Handle", "Ascending Triangle",
    "Bullish Engulfing", "Morning Star", "Hammer", "Inverse Head & Shoulders",
    "Breakout Above Resistance", "VWAP Crossover", "Golden Cross 15-min"
]


def _jitter(base, pct=3):
    return round(base * (1 + random.uniform(-pct, pct) / 100), 2)


def get_market_overview():
    nifty_base = 24180
    nifty = _jitter(nifty_base, 1)
    nifty_change = round(nifty - nifty_base, 2)
    nifty_pct = round((nifty_change / nifty_base) * 100, 2)

    banknifty_base = 52340
    banknifty = _jitter(banknifty_base, 1.2)
    banknifty_change = round(banknifty - banknifty_base, 2)

    sensex_base = 79820
    sensex = _jitter(sensex_base, 1)
    sensex_change = round(sensex - sensex_base, 2)

    return {
        "nifty": {"value": nifty, "change": nifty_change, "change_pct": nifty_pct},
        "bank_nifty": {"value": banknifty, "change": banknifty_change, "change_pct": round((banknifty_change / banknifty_base) * 100, 2)},
        "sensex": {"value": sensex, "change": sensex_change, "change_pct": round((sensex_change / sensex_base) * 100, 2)},
        "india_vix": round(random.uniform(11, 18), 2),
        "market_sentiment": random.randint(35, 80),
        "advance_decline": {"advances": random.randint(700, 1400), "declines": random.randint(400, 1100)},
        "market_status": "OPEN" if 9 <= datetime.now().hour < 16 else "CLOSED",
    }


def get_top_gainers(count=5):
    stocks = random.sample(STOCK_UNIVERSE, count)
    result = []
    for s in stocks:
        change_pct = round(random.uniform(1.5, 6.5), 2)
        price = round(s["base_price"] * (1 + change_pct / 100), 2)
        result.append({
            "symbol": s["symbol"], "name": s["name"], "sector": s["sector"],
            "price": price, "change": round(price - s["base_price"], 2), "change_pct": change_pct,
            "volume": random.randint(500000, 5000000),
        })
    return sorted(result, key=lambda x: x["change_pct"], reverse=True)


def get_top_losers(count=5):
    stocks = random.sample(STOCK_UNIVERSE, count)
    result = []
    for s in stocks:
        change_pct = round(random.uniform(-6.5, -1.5), 2)
        price = round(s["base_price"] * (1 + change_pct / 100), 2)
        result.append({
            "symbol": s["symbol"], "name": s["name"], "sector": s["sector"],
            "price": price, "change": round(price - s["base_price"], 2), "change_pct": change_pct,
            "volume": random.randint(500000, 5000000),
        })
    return sorted(result, key=lambda x: x["change_pct"])


def get_sector_performance():
    result = []
    for sector in SECTORS:
        change = round(random.uniform(-3, 4), 2)
        result.append({"sector": sector, "change_pct": change})
    return sorted(result, key=lambda x: x["change_pct"], reverse=True)


def get_global_markets():
    return [
        {"name": "Dow Jones", "region": "US", "value": _jitter(42850, 0.8), "change_pct": round(random.uniform(-1.5, 1.5), 2)},
        {"name": "Nasdaq", "region": "US", "value": _jitter(19200, 1), "change_pct": round(random.uniform(-2, 2), 2)},
        {"name": "S&P 500", "region": "US", "value": _jitter(5920, 0.8), "change_pct": round(random.uniform(-1.5, 1.5), 2)},
        {"name": "FTSE 100", "region": "UK", "value": _jitter(8380, 0.7), "change_pct": round(random.uniform(-1, 1.2), 2)},
        {"name": "Nikkei 225", "region": "Japan", "value": _jitter(38900, 0.9), "change_pct": round(random.uniform(-1.5, 1.8), 2)},
        {"name": "Hang Seng", "region": "HK", "value": _jitter(21200, 1.2), "change_pct": round(random.uniform(-2, 2), 2)},
    ]


def get_commodities():
    return {
        "crude_oil": {"name": "Brent Crude", "value": _jitter(82.5, 2), "unit": "USD/bbl", "change_pct": round(random.uniform(-2, 2), 2)},
        "gold": {"name": "Gold (MCX)", "value": _jitter(72500, 1), "unit": "INR/10g", "change_pct": round(random.uniform(-1, 1.5), 2)},
        "silver": {"name": "Silver (MCX)", "value": _jitter(84200, 1.5), "unit": "INR/kg", "change_pct": round(random.uniform(-2, 2), 2)},
        "usd_inr": {"name": "USD/INR", "value": _jitter(83.25, 0.3), "unit": "", "change_pct": round(random.uniform(-0.5, 0.5), 2)},
    }


def get_fii_dii():
    return {
        "fii": {"net": round(random.uniform(-3500, 3500), 2), "buy": round(random.uniform(5000, 15000), 2), "sell": round(random.uniform(5000, 15000), 2)},
        "dii": {"net": round(random.uniform(-2500, 4000), 2), "buy": round(random.uniform(4000, 12000), 2), "sell": round(random.uniform(4000, 12000), 2)},
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def get_stock_quote(symbol: str):
    stock = next((s for s in STOCK_UNIVERSE if s["symbol"] == symbol.upper()), None)
    if not stock:
        return None
    price = _jitter(stock["base_price"], 3)
    open_price = _jitter(stock["base_price"], 1)
    high = round(max(price, open_price) * (1 + random.uniform(0, 2) / 100), 2)
    low = round(min(price, open_price) * (1 - random.uniform(0, 2) / 100), 2)
    prev_close = _jitter(stock["base_price"], 0.5)
    change = round(price - prev_close, 2)
    volume = random.randint(800000, 8000000)
    avg_volume = random.randint(600000, 5000000)
    rsi = round(random.uniform(30, 80), 1)
    vwap = round((high + low + price) / 3, 2)

    return {
        "symbol": stock["symbol"],
        "name": stock["name"],
        "sector": stock["sector"],
        "price": price,
        "open": open_price,
        "high": high,
        "low": low,
        "prev_close": prev_close,
        "change": change,
        "change_pct": round((change / prev_close) * 100, 2),
        "volume": volume,
        "avg_volume": avg_volume,
        "volume_ratio": round(volume / avg_volume, 2),
        "rsi": rsi,
        "macd": round(random.uniform(-5, 5), 2),
        "macd_signal": round(random.uniform(-3, 3), 2),
        "vwap": vwap,
        "day_range": f"{low} - {high}",
        "week_52_high": round(high * random.uniform(1.05, 1.25), 2),
        "week_52_low": round(low * random.uniform(0.6, 0.85), 2),
        "market_cap_cr": random.randint(10000, 1500000),
        "pe_ratio": round(random.uniform(10, 60), 1),
    }


def generate_top_picks(count=3):
    picked = random.sample(STOCK_UNIVERSE, count)
    picks = []
    for stock in picked:
        price = _jitter(stock["base_price"], 2)
        rsi = round(random.uniform(55, 72), 1)
        volume_ratio = round(random.uniform(1.5, 3.5), 1)
        sl_pct = random.uniform(0.8, 1.5)
        target1_pct = random.uniform(1.2, 2.5)
        target2_pct = random.uniform(2.0, 4.0)
        sl = round(price * (1 - sl_pct / 100), 2)
        target1 = round(price * (1 + target1_pct / 100), 2)
        target2 = round(price * (1 + target2_pct / 100), 2)
        risk = abs(price - sl)
        reward = abs(target1 - price)
        rr = round(reward / risk, 1) if risk > 0 else 0
        confidence = random.randint(68, 92)
        pattern = random.choice(CHART_PATTERNS)
        sector_change = round(random.uniform(0.3, 2.5), 1)

        picks.append({
            "symbol": stock["symbol"],
            "name": stock["name"],
            "sector": stock["sector"],
            "price": price,
            "entry": price,
            "stop_loss": sl,
            "target1": target1,
            "target2": target2,
            "risk_reward": rr,
            "confidence": confidence,
            "rsi": rsi,
            "volume_ratio": volume_ratio,
            "pattern": pattern,
            "sector_change": sector_change,
            "risk_level": "LOW" if confidence > 80 else ("MEDIUM" if confidence > 70 else "HIGH"),
            "reasons": [
                f"RSI breakout above {rsi} (was {round(rsi - random.uniform(5, 15), 1)} yesterday)",
                f"Volume {volume_ratio}x above 20-day average",
                f"{stock['sector']} sector up {sector_change}% today",
                f"Price crossed above VWAP at 9:{random.randint(15, 30)} AM",
                f"{pattern} detected on 15-min chart",
            ],
            "risk_factors": [
                f"Nifty approaching resistance at {random.randint(24000, 24500)}",
                f"Global cues mixed — watch US futures",
            ],
            "historical_success": f"{random.randint(60, 78)}%",
        })
    return sorted(picks, key=lambda x: x["confidence"], reverse=True)


def get_chart_data(symbol: str, period: str = "1D"):
    stock = next((s for s in STOCK_UNIVERSE if s["symbol"] == symbol.upper()), None)
    if not stock:
        return []
    base = stock["base_price"]
    data = []
    now = datetime.now(timezone.utc)
    if period == "1D":
        points = 78  # 5-min candles for a trading day
        for i in range(points):
            t = now - timedelta(minutes=(points - i) * 5)
            o = _jitter(base, 1.5)
            c = _jitter(o, 0.8)
            h = round(max(o, c) * (1 + random.uniform(0, 0.5) / 100), 2)
            l = round(min(o, c) * (1 - random.uniform(0, 0.5) / 100), 2)
            data.append({"time": t.isoformat(), "open": o, "high": h, "low": l, "close": c, "volume": random.randint(10000, 200000)})
            base = c
    else:
        points = 60
        for i in range(points):
            t = now - timedelta(days=(points - i))
            o = _jitter(base, 2)
            c = _jitter(o, 1.5)
            h = round(max(o, c) * (1 + random.uniform(0, 1) / 100), 2)
            l = round(min(o, c) * (1 - random.uniform(0, 1) / 100), 2)
            data.append({"time": t.strftime("%Y-%m-%d"), "open": o, "high": h, "low": l, "close": c, "volume": random.randint(500000, 5000000)})
            base = c
    return data


def get_ai_activity_feed():
    now = datetime.now(timezone.utc)
    activities = [
        {"time": "06:00", "action": "Checking US market overnight movement", "status": "done", "icon": "globe"},
        {"time": "06:03", "action": "Reading RBI and economic news", "status": "done", "icon": "newspaper"},
        {"time": "06:05", "action": "Analyzing Bank Nifty structure", "status": "done", "icon": "chart"},
        {"time": "06:08", "action": "Scanning 50 NSE stocks for setups", "status": "done", "icon": "search"},
        {"time": "06:12", "action": "Detecting unusual volume patterns", "status": "done", "icon": "volume"},
        {"time": "06:15", "action": "Ranking top 3 setups by confidence", "status": "done", "icon": "brain"},
        {"time": "09:15", "action": "Market opened — monitoring live", "status": "active", "icon": "activity"},
    ]
    # Add some dynamic recent ones
    symbols = random.sample([s["symbol"] for s in STOCK_UNIVERSE], 3)
    activities.append({"time": now.strftime("%H:%M"), "action": f"{symbols[0]} breakout detected", "status": "alert", "icon": "target"})
    activities.append({"time": now.strftime("%H:%M"), "action": f"Monitoring {random.randint(2, 5)} active trades", "status": "active", "icon": "eye"})
    if random.random() > 0.5:
        activities.append({"time": now.strftime("%H:%M"), "action": f"{symbols[1]} momentum slowing — alert sent", "status": "warning", "icon": "alert"})
    return activities


def search_stocks(query: str):
    q = query.upper()
    return [s for s in STOCK_UNIVERSE if q in s["symbol"] or q in s["name"].upper()][:10]

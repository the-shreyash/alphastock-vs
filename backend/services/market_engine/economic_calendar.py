"""Economic Calendar — Indian market event calendar.

Provides upcoming and recent economic events relevant to Indian markets:
    - RBI monetary policy dates
    - GDP / inflation data releases
    - Earnings season windows
    - FII/DII reporting dates
    - Options expiry dates
    - Government policy events
    - Global events affecting Indian markets

Data sources:
    Phase 1: Curated static calendar + NSE holiday list
    Phase 2: Live scraping from economic calendar APIs
"""
import logging
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, List, Optional

from services.market_engine.event_bus import event_bus
from services.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

CALENDAR_CACHE_KEY = "market_engine:calendar"
CALENDAR_CACHE_TTL = 3600  # 1 hour


# ── Static calendar events (curated for Indian markets) ──────

def _generate_recurring_events(year: int) -> List[Dict[str, Any]]:
    """Generate recurring Indian market events for a given year."""
    events = []

    # RBI Policy meetings (approximate dates — typically bi-monthly)
    rbi_months = [2, 4, 6, 8, 10, 12]
    for m in rbi_months:
        day = min(7, 28)  # First week of the month
        events.append({
            "date": f"{year}-{m:02d}-{day:02d}",
            "title": f"RBI Monetary Policy Decision",
            "category": "monetary_policy",
            "importance": "high",
            "description": "Reserve Bank of India monetary policy committee (MPC) rate decision.",
            "impact": "Bank Nifty, financials, bond markets",
        })

    # Quarterly GDP data (released ~2 months after quarter end)
    gdp_releases = [(2, 28), (5, 31), (8, 31), (11, 30)]
    for m, d in gdp_releases:
        events.append({
            "date": f"{year}-{m:02d}-{d:02d}",
            "title": "India GDP Data Release",
            "category": "economic_data",
            "importance": "high",
            "description": "Quarterly GDP growth data from the Ministry of Statistics.",
            "impact": "Broad market, Nifty 50",
        })

    # Monthly CPI inflation
    for m in range(1, 13):
        events.append({
            "date": f"{year}-{m:02d}-12",
            "title": "India CPI Inflation Data",
            "category": "economic_data",
            "importance": "medium",
            "description": "Consumer Price Index inflation release.",
            "impact": "Rate-sensitive sectors, banking, FMCG",
        })

    # Monthly IIP (Industrial Production)
    for m in range(1, 13):
        events.append({
            "date": f"{year}-{m:02d}-12",
            "title": "India IIP Industrial Production",
            "category": "economic_data",
            "importance": "medium",
            "description": "Index of Industrial Production data.",
            "impact": "Manufacturing, infrastructure, capital goods",
        })

    # Options expiry — last Thursday of each month
    for m in range(1, 13):
        # Find last Thursday
        last_day = date(year, m + 1 if m < 12 else 1, 1) - timedelta(days=1)
        if m == 12:
            last_day = date(year, 12, 31)
        while last_day.weekday() != 3:  # Thursday = 3
            last_day -= timedelta(days=1)
        events.append({
            "date": last_day.isoformat(),
            "title": "Monthly F&O Expiry",
            "category": "expiry",
            "importance": "high",
            "description": "Nifty and Bank Nifty monthly options and futures expiry.",
            "impact": "Nifty, Bank Nifty, high-beta stocks",
        })

    # Weekly expiry — every Thursday (Nifty weekly)
    d = date(year, 1, 1)
    while d.weekday() != 3:
        d += timedelta(days=1)
    while d.year == year:
        events.append({
            "date": d.isoformat(),
            "title": "Weekly Nifty Options Expiry",
            "category": "expiry",
            "importance": "low",
            "description": "Nifty weekly options expiry.",
            "impact": "Nifty options, volatility",
        })
        d += timedelta(days=7)

    # Earnings seasons
    earnings_windows = [
        (1, 15, 2, 15, "Q3 (Oct-Dec)"),
        (4, 15, 5, 15, "Q4 (Jan-Mar)"),
        (7, 15, 8, 15, "Q1 (Apr-Jun)"),
        (10, 15, 11, 15, "Q2 (Jul-Sep)"),
    ]
    for sm, sd, em, ed, label in earnings_windows:
        events.append({
            "date": f"{year}-{sm:02d}-{sd:02d}",
            "end_date": f"{year}-{em:02d}-{ed:02d}",
            "title": f"Earnings Season — {label}",
            "category": "earnings",
            "importance": "high",
            "description": f"Corporate earnings reporting period for {label} results.",
            "impact": "All sectors, individual stock moves",
        })

    # Union Budget
    events.append({
        "date": f"{year}-02-01",
        "title": "Union Budget Presentation",
        "category": "government",
        "importance": "high",
        "description": "Annual Union Budget presented by the Finance Minister.",
        "impact": "Broad market, tax-sensitive sectors, infra, defence",
    })

    # US Fed meetings (global impact)
    fed_months = [1, 3, 5, 6, 7, 9, 11, 12]
    for m in fed_months:
        events.append({
            "date": f"{year}-{m:02d}-15",
            "title": "US Fed FOMC Decision",
            "category": "global",
            "importance": "high",
            "description": "US Federal Reserve interest rate decision. Impacts global risk appetite.",
            "impact": "IT exports, Nifty, FII flows",
        })

    return events


def _filter_events(
    events: List[Dict],
    category: Optional[str] = None,
    importance: Optional[str] = None,
    days_ahead: int = 30,
    days_behind: int = 7,
) -> List[Dict]:
    """Filter events by category, importance, and date range."""
    today = date.today()
    start = today - timedelta(days=days_behind)
    end = today + timedelta(days=days_ahead)

    filtered = []
    for ev in events:
        ev_date = date.fromisoformat(ev["date"])
        if not (start <= ev_date <= end):
            continue
        if category and ev.get("category") != category:
            continue
        if importance and ev.get("importance") != importance:
            continue

        # Add computed fields
        ev_copy = {**ev}
        delta = (ev_date - today).days
        if delta < 0:
            ev_copy["status"] = "past"
            ev_copy["days_ago"] = abs(delta)
        elif delta == 0:
            ev_copy["status"] = "today"
            ev_copy["days_ago"] = 0
        else:
            ev_copy["status"] = "upcoming"
            ev_copy["days_until"] = delta

        filtered.append(ev_copy)

    # Sort: today first, then upcoming by date, then past by date desc
    def sort_key(e):
        if e["status"] == "today":
            return (0, 0)
        elif e["status"] == "upcoming":
            return (1, e.get("days_until", 0))
        else:
            return (2, -e.get("days_ago", 0))

    filtered.sort(key=sort_key)
    return filtered


async def get_calendar(
    category: Optional[str] = None,
    importance: Optional[str] = None,
    days_ahead: int = 30,
    days_behind: int = 7,
) -> Dict[str, Any]:
    """Get economic calendar events.

    Returns:
        {
            "events": [...],
            "today_events": [...],
            "upcoming_high": [...],
            "categories": [...],
            "generated_at": str,
        }
    """
    now = datetime.now(timezone.utc)
    current_year = now.year

    # Generate events for current and next year
    all_events = _generate_recurring_events(current_year)
    if now.month >= 10:
        all_events.extend(_generate_recurring_events(current_year + 1))

    # Filter
    filtered = _filter_events(
        all_events,
        category=category,
        importance=importance,
        days_ahead=days_ahead,
        days_behind=days_behind,
    )

    # Separate today's events
    today_events = [e for e in filtered if e.get("status") == "today"]

    # Upcoming high-importance
    upcoming_high = [
        e for e in filtered
        if e.get("status") == "upcoming" and e.get("importance") == "high"
    ][:5]

    # Available categories
    categories = sorted(set(e.get("category", "") for e in all_events))

    if today_events:
        await event_bus.publish("calendar.event", {
            "count": len(today_events),
            "titles": [e["title"] for e in today_events[:3]],
        })

    return {
        "events": filtered,
        "today_events": today_events,
        "upcoming_high": upcoming_high,
        "categories": categories,
        "total": len(filtered),
        "available": True,
        "generated_at": now.isoformat(),
    }

"""Morning Report — the automated pre-market briefing (Sprint 10).

Generated every market day before the open, and on demand from the Morning
Report page. Answers the only question that matters at 8:30 AM: *what changed
overnight, and what does it mean for me today?*

Two layers
──────────
The report is deliberately split, because its two halves have different
identities, costs, and lifetimes:

  Market layer    Identical for every user (global markets, Gift Nifty, news,
                  economic calendar, scanner, top picks, risk warnings). Tens of
                  provider calls, so it is generated once per day, persisted to
                  ``db.reports`` and served from there.

  Personal layer  Different for every user (portfolio alerts). Computed per
                  request from that user's live holdings and never written into
                  the shared document.

Keeping them separate is a correctness requirement, not an optimization: the
shared document is cached by *date alone*, so any per-user field written into it
would be served to whichever user happened to request the report second. The two
layers are merged at read time by :func:`get_morning_report`.

Data access
───────────
Every market read goes through the Market Gateway (MARKET_DATA_ARCHITECTURE.md)
— this module never touches a provider. Sections degrade independently: a dead
news feed costs the news section and nothing else. Any section that cannot be
sourced is marked ``available: false`` with a reason and is *never* filled with a
plausible-looking substitute.

Transparency
────────────
Generation streams a truthful AIRun step timeline (REALTIME_SYSTEM.md → "AI
Thinking Process"); each step wraps the real work it names.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.ai_activity import AIRun
from services.market_engine import market_gateway

logger = logging.getLogger(__name__)

REPORT_TYPE = "morning"

# Step labels for the shared market layer, in execution order. One label per
# real phase of _build_market_layer() — never a label for work that isn't done.
MARKET_STEPS = [
    "Collecting Market Data",
    "Reading Global Markets",
    "Reading News",
    "Checking Economic Calendar",
    "Scanning NSE",
    "Analyzing Sector Flows",
    "Generating Report",
    "Saving Report",
]

# Appended only when the report is generated for a signed-in user.
PERSONAL_STEP = "Reviewing Your Portfolio"

MAX_HEADLINES = 6
MAX_CALENDAR_EVENTS = 5


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pct(v: Optional[float]) -> str:
    return f"{v:+.2f}%" if v is not None else "unavailable"


async def _safe(coro, section: str, default, step=None):
    """Run a section fetch; degrade to `default` instead of failing the report.

    One unreachable feed must never cost the user their whole briefing. When a
    `step` is given, a failure marks that step `warning` so the live timeline
    reports the degradation instead of claiming the work succeeded.
    """
    try:
        return await coro
    except Exception as exc:
        logger.warning("Morning report: %s section failed: %s", section, exc)
        if step is not None:
            step.warn()
        return default


# --------------------------------------------------------------------------- #
# Market layer — shared, generated once per day
# --------------------------------------------------------------------------- #

def _summarize_global(markets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Global markets grouped by region with a factual overnight read.

    The previous implementation asserted a fixed sentence ("US futures and Asian
    markets influencing early Indian session") every morning regardless of what
    markets actually did. This states only what the quotes show.
    """
    live = [m for m in (markets or []) if m.get("available") and m.get("change_pct") is not None]
    if not live:
        return {
            "available": False,
            "markets": markets or [],
            "summary": "Global market quotes are temporarily unavailable — overnight cues cannot be read.",
            "advancing": 0,
            "declining": 0,
        }

    advancing = [m for m in live if m["change_pct"] > 0]
    declining = [m for m in live if m["change_pct"] < 0]

    if len(advancing) > len(declining):
        tone = "Overnight global cues are broadly positive"
    elif len(declining) > len(advancing):
        tone = "Overnight global cues are broadly negative"
    else:
        tone = "Overnight global cues are mixed"

    best = max(live, key=lambda m: m["change_pct"])
    worst = min(live, key=lambda m: m["change_pct"])
    summary = (
        f"{tone} — {len(advancing)} of {len(live)} tracked indices closed higher. "
        f"{best['name']} {_pct(best['change_pct'])} led; {worst['name']} {_pct(worst['change_pct'])} lagged."
    )

    return {
        "available": True,
        "markets": markets,
        "summary": summary,
        "advancing": len(advancing),
        "declining": len(declining),
    }


def _select_headlines(articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Top pre-market headlines — high-importance first, then most recent."""
    if not articles:
        return {
            "available": False,
            "headlines": [],
            "note": "News feeds are temporarily unreachable — no headlines available.",
        }

    # High-importance headlines first, newest first within each band.
    ranked = sorted(
        articles,
        key=lambda a: (a.get("importance") != "high", _neg_time(a.get("published"))),
    )
    headlines = [
        {
            "title": a.get("title"),
            "source": a.get("source"),
            "link": a.get("link"),
            "sentiment": a.get("sentiment"),
            "importance": a.get("importance"),
            "published": a.get("published"),
        }
        for a in ranked[:MAX_HEADLINES]
    ]
    return {"available": True, "headlines": headlines, "note": None}


def _neg_time(published: Optional[str]) -> float:
    """Sort key placing newer articles first; undated articles last."""
    if not published:
        return 0.0
    try:
        return -datetime.fromisoformat(published).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _select_calendar(calendar: Dict[str, Any]) -> Dict[str, Any]:
    """Today's events plus the nearest high-importance ones ahead."""
    if not calendar or not calendar.get("available"):
        return {"available": False, "today": [], "upcoming": [],
                "note": "Economic calendar is temporarily unavailable."}

    def slim(e: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": e.get("title"),
            "date": e.get("date"),
            "category": e.get("category"),
            "importance": e.get("importance"),
            "description": e.get("description"),
            "impact": e.get("impact"),
            "status": e.get("status"),
            "days_until": e.get("days_until"),
        }

    return {
        "available": True,
        "today": [slim(e) for e in calendar.get("today_events", [])],
        "upcoming": [slim(e) for e in calendar.get("upcoming_high", [])[:MAX_CALENDAR_EVENTS]],
        "note": None,
    }


def _build_risk_warnings(
    *,
    vix: Optional[float],
    fii_net: Optional[float],
    banknifty_chg: float,
    news_sentiment: Optional[Dict[str, Any]],
    calendar: Dict[str, Any],
    gift_nifty: Dict[str, Any],
) -> List[str]:
    """Risk warnings grounded in the data actually collected.

    Each line names its evidence so the user can verify it — per the product
    rule that the AI educates rather than asserts.
    """
    warnings: List[str] = []

    if vix is not None:
        warnings.append(
            f"India VIX at {vix} — {'elevated volatility, size positions down' if vix > 15 else 'moderate volatility'}"
        )
    else:
        warnings.append("India VIX unavailable — volatility reading pending")

    if fii_net is not None:
        warnings.append(
            f"FII net flow ₹{fii_net:,.0f} Cr — "
            f"{'sustained selling pressure' if fii_net < 0 else 'supportive institutional buying'}"
        )
    else:
        warnings.append("FII/DII flow unavailable — NSE publishes after market close")

    warnings.append(
        f"{'Weak' if banknifty_chg < -0.5 else 'Mixed'} Bank Nifty ({_pct(banknifty_chg)}) — "
        "watch financials for index direction"
    )

    if news_sentiment and news_sentiment.get("available"):
        warnings.append(
            f"News sentiment {news_sentiment['label']} ({news_sentiment['score']}/100 "
            f"across {news_sentiment['articles_analyzed']} headlines)"
        )
    else:
        warnings.append("News sentiment unavailable — feeds temporarily unreachable")

    # A high-importance event today is a risk in itself (gap/whipsaw potential).
    for event in calendar.get("today", [])[:2]:
        if event.get("importance") == "high":
            warnings.append(
                f"{event['title']} today — expect volatility in {event.get('impact', 'affected sectors')}"
            )

    if gift_nifty.get("available") and gift_nifty.get("change_pct") is not None:
        gap = gift_nifty["change_pct"]
        if abs(gap) >= 0.5:
            warnings.append(
                f"Gift Nifty {_pct(gap)} — a {'gap-up' if gap > 0 else 'gap-down'} open is indicated; "
                "avoid chasing the first candle"
            )

    return warnings


def _compute_mood(nifty_chg: float, banknifty_chg: float, sentiment: Optional[float]) -> Dict[str, Any]:
    """Weighted market mood. Sentiment defaults to neutral when unavailable so a
    dead news feed can't drag the mood bearish."""
    sentiment_component = (sentiment / 100 * 0.2) if sentiment is not None else 0.1
    score = round(nifty_chg * 0.5 + banknifty_chg * 0.3 + sentiment_component, 3)
    if score > 0.5:
        mood = "Bullish"
    elif score > 0:
        mood = "Cautious"
    elif score > -0.5:
        mood = "Neutral"
    else:
        mood = "Bearish"
    return {"market_mood": mood, "mood_score": score}


async def _generate_briefing(facts: Dict[str, Any]) -> str:
    """AI briefing from the centralized prompt library, with a grounded fallback.

    The fallback is not a degraded experience — it restates real collected
    numbers. The AI adds narrative, never data.
    """
    fallback = (
        f"Good morning. Nifty at {facts['nifty_str']} ({_pct(facts['nifty_chg'])}), "
        f"Bank Nifty {_pct(facts['banknifty_chg'])}. Market mood: {facts['market_mood']}. "
        f"{facts['picks_count']} quality setups identified. Stay disciplined and respect your stops."
    )

    try:
        from server import claude_configured, gemini_configured, get_debate_engine
        from services.prompt_library import get_prefer, get_prompt
    except Exception as exc:
        logger.debug("Morning report: AI briefing unavailable (%s)", exc)
        return fallback

    if not (claude_configured() or gemini_configured()):
        return fallback

    try:
        system_prompt = get_prompt("morning_report")
        context = (
            f"Nifty: {facts['nifty_str']} ({_pct(facts['nifty_chg'])})\n"
            f"Bank Nifty: {_pct(facts['banknifty_chg'])}\n"
            f"Sensex: {facts['sensex_str']}\n"
            f"Market mood: {facts['market_mood']}\n"
            f"Gift Nifty: {facts['gift_nifty_str']}\n"
            f"Global markets: {facts['global_summary']}\n"
            f"FII net: {facts['fii_str']}\n"
            f"News sentiment: {facts['news_str']}\n"
            f"Top headlines: {facts['headlines_str']}\n"
            f"Economic events today: {facts['events_str']}\n"
            f"Leading sectors: {facts['sectors_str']}\n"
            f"Top picks: {facts['picks_str']}\n\n"
            "Write a 3-4 sentence pre-market briefing for Indian traders. Use only the "
            "numbers above. Anything marked unavailable must be omitted, never guessed."
        )
        engine = get_debate_engine()
        briefing = await engine.simple_chat(
            system_prompt, context, prefer=get_prefer("morning_report"), max_tokens=260
        )
        return briefing.strip() or fallback
    except Exception as exc:
        logger.warning("Morning report: AI briefing failed, using grounded fallback: %s", exc)
        return fallback


async def _build_market_layer(db, run: AIRun) -> Dict[str, Any]:
    """Generate the shared market report. Returns the persisted document shape."""
    # Step 1 — Collecting Market Data
    async with run.step() as step:
        overview = await _safe(market_gateway.get_indices(), "indices", {}, step)
        if not overview:
            step.warn()

    if not overview:
        return {
            "date": _today(),
            "type": REPORT_TYPE,
            "available": False,
            "note": "Live market data is temporarily unavailable — the morning report cannot be generated right now.",
            "generated_at": _now_iso(),
        }

    # Step 2 — Reading Global Markets (+ Gift Nifty: both are the overnight read)
    async with run.step() as step:
        global_markets, gift_nifty = await asyncio.gather(
            _safe(market_gateway.get_global_markets(), "global markets", [], step),
            _safe(market_gateway.get_gift_nifty(), "gift nifty", {"available": False}, step),
        )
        global_section = _summarize_global(global_markets)
        if not global_section["available"]:
            step.warn()

    # Step 3 — Reading News
    async with run.step() as step:
        from services.news_service import get_market_sentiment

        articles, news_sentiment = await asyncio.gather(
            _safe(market_gateway.get_news(), "news", [], step),
            _safe(get_market_sentiment(), "news sentiment", None, step),
        )
        news_section = _select_headlines(articles)
        if not news_section["available"]:
            step.warn()

    # Step 4 — Checking Economic Calendar
    async with run.step() as step:
        calendar_raw = await _safe(
            market_gateway.get_calendar(days_ahead=14, days_behind=0), "calendar", {}, step
        )
        calendar_section = _select_calendar(calendar_raw)

    # Step 5 — Scanning NSE
    async with run.step() as step:
        from services.real_market import fetch_real_top_picks

        picks_res = await _safe(fetch_real_top_picks(3), "scanner", {}, step)
        picks = (picks_res or {}).get("picks", [])
        if not picks:
            step.warn()

    # Step 6 — Analyzing Sector Flows
    async with run.step() as step:
        from services.real_market import fetch_real_fii_dii

        fii_dii, sectors = await asyncio.gather(
            _safe(fetch_real_fii_dii(), "fii/dii", {}, step),
            _safe(market_gateway.get_sectors(), "sectors", [], step),
        )

        nifty = overview.get("nifty") or {}
        bank_nifty = overview.get("bank_nifty") or {}
        sensex = overview.get("sensex") or {}
        nifty_chg = nifty.get("change_pct") or 0
        banknifty_chg = bank_nifty.get("change_pct") or 0
        vix = overview.get("india_vix")
        fii_net = (fii_dii.get("fii") or {}).get("net")

        mood = _compute_mood(nifty_chg, banknifty_chg, overview.get("market_sentiment"))
        risk_warnings = _build_risk_warnings(
            vix=vix,
            fii_net=fii_net,
            banknifty_chg=banknifty_chg,
            news_sentiment=news_sentiment,
            calendar=calendar_section,
            gift_nifty=gift_nifty,
        )

    # Step 7 — Generating Report
    async with run.step():
        nifty_val, bnk_val, sensex_val = nifty.get("value"), bank_nifty.get("value"), sensex.get("value")
        gift_str = (
            f"{gift_nifty['value']:,.0f} ({_pct(gift_nifty.get('change_pct'))})"
            if gift_nifty.get("available") else "unavailable"
        )
        briefing = await _generate_briefing({
            "nifty_str": f"{nifty_val:,.0f}" if nifty_val is not None else "unavailable",
            "sensex_str": f"{sensex_val:,.0f}" if sensex_val is not None else "unavailable",
            "nifty_chg": nifty_chg,
            "banknifty_chg": banknifty_chg,
            "market_mood": mood["market_mood"],
            "gift_nifty_str": gift_str,
            "global_summary": global_section["summary"],
            "fii_str": f"₹{fii_net:,.0f} Cr" if fii_net is not None else "unavailable",
            "news_str": (news_sentiment or {}).get("label") or "unavailable",
            "headlines_str": "; ".join(h["title"] for h in news_section["headlines"][:3]) or "unavailable",
            "events_str": ", ".join(e["title"] for e in calendar_section["today"]) or "none scheduled",
            "sectors_str": ", ".join(
                f"{s.get('name') or s.get('sector')} ({_pct(s.get('change_pct'))})" for s in sectors[:3]
            ) or "unavailable",
            "picks_str": ", ".join(p["name"] for p in picks[:3]) or "unavailable",
            "picks_count": len(picks),
        })

        report = {
            "date": _today(),
            "type": REPORT_TYPE,
            "available": True,
            **mood,
            "nifty": {"value": nifty_val, "change_pct": nifty_chg},
            "banknifty": {"value": bnk_val, "change_pct": banknifty_chg},
            "sensex": {"value": sensex_val, "change_pct": sensex.get("change_pct")},
            "gift_nifty": gift_nifty,
            "global_markets": global_section,
            # Retained for the Dashboard summary card and existing API consumers.
            # Same text as global_markets.summary — now derived from real quotes
            # rather than the fixed sentence this field used to carry.
            "global_cues": global_section["summary"],
            "news": news_section,
            "news_sentiment": news_sentiment,
            "economic_calendar": calendar_section,
            "sectors": sectors[:6],
            "top_picks": picks,
            "key_risks": risk_warnings,
            "ai_briefing": briefing,
            "fii_dii": {"fii_net": fii_net, "dii_net": (fii_dii.get("dii") or {}).get("net")},
            "generated_at": _now_iso(),
        }

    # Step 8 — Saving Report
    async with run.step():
        await db.reports.update_one(
            {"date": report["date"], "type": REPORT_TYPE},
            {"$set": {**report}},
            upsert=True,
        )

    return report


# --------------------------------------------------------------------------- #
# Personal layer — per user, never cached in the shared document
# --------------------------------------------------------------------------- #

async def _quotes_map(symbols: List[str]) -> Dict[str, Any]:
    """Batch live quotes through the gateway for portfolio valuation."""
    uniq = list({(s or "").upper() for s in symbols if s})
    if not uniq:
        return {}
    results = await asyncio.gather(
        *[market_gateway.get_quote(s) for s in uniq], return_exceptions=True
    )
    return {sym: (None if isinstance(r, Exception) else r) for sym, r in zip(uniq, results)}


def _portfolio_alerts(intelligence: Dict[str, Any], market: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Cross-reference the user's holdings against this morning's market state.

    This is the report's payoff: generic market news becomes "this affects *your*
    Reliance position". Every alert carries its reasoning, per the product rule
    that the AI explains rather than asserts.
    """
    alerts: List[Dict[str, Any]] = []
    holdings = intelligence.get("holdings") or []
    if not holdings:
        return alerts

    held = {(h.get("symbol") or "").upper(): h for h in holdings}

    # 1. Risk factors the portfolio engine already surfaced (single source of truth).
    risk = intelligence.get("risk") or {}
    for factor in (risk.get("factors") or [])[:2]:
        alerts.append({
            "severity": "warning" if factor["points"] >= 15 else "info",
            "symbol": None,
            "title": factor["name"],
            "message": factor["detail"],
            "why": f"Contributes {factor['points']} points to your {risk.get('level', '—')} risk score of {risk.get('score')}/100.",
        })

    # 2. Holdings sitting in this morning's weakest sectors.
    weak_sectors = {
        (s.get("name") or s.get("sector")): s.get("change_pct")
        for s in (market.get("sectors") or [])
        if (s.get("change_pct") or 0) < -0.5
    }
    for sector, change in list(weak_sectors.items())[:2]:
        exposed = [h["symbol"] for h in holdings if (h.get("sector") or "") == sector]
        if exposed:
            alerts.append({
                "severity": "warning",
                "symbol": exposed[0] if len(exposed) == 1 else None,
                "title": f"{sector} weakness affects your holdings",
                "message": f"{sector} is down {change}% — you hold {', '.join(exposed[:3])}.",
                "why": "Stocks in a weak sector tend to move with it, so these positions carry correlated downside today.",
            })

    # 3. Today's headlines naming a stock the user actually owns.
    for article in (market.get("news") or {}).get("headlines", []):
        title_upper = (article.get("title") or "").upper()
        for symbol, holding in held.items():
            name = (holding.get("name") or "").upper()
            if symbol in title_upper or (len(name) > 4 and name in title_upper):
                alerts.append({
                    "severity": "critical" if article.get("importance") == "high" else "info",
                    "symbol": symbol,
                    "title": f"News on your {symbol} position",
                    "message": article.get("title"),
                    "why": f"You hold {symbol}. Sentiment on this headline reads {article.get('sentiment', 'neutral')}.",
                })
                break

    # 4. Positions already flagged by the monitoring engine as actionable.
    for suggestion in (intelligence.get("suggestions") or []):
        if suggestion.get("tone") == "critical":
            alerts.append({
                "severity": "critical",
                "symbol": None,
                "title": "Action needed on a position",
                "message": suggestion.get("text"),
                "why": "Flagged critical by continuous portfolio monitoring — review before the open.",
            })

    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: order.get(a["severity"], 3))
    return alerts[:6]


async def _build_personal_layer(db, user: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
    """Portfolio alerts for one user. Failure degrades this section only."""
    try:
        from services import portfolio_engine

        intelligence = await portfolio_engine.build_intelligence(db, user, _quotes_map)
    except Exception as exc:
        logger.warning("Morning report: portfolio layer failed for user: %s", exc)
        return {
            "available": False,
            "alerts": [],
            "note": "Your portfolio could not be reviewed right now.",
        }

    if not intelligence.get("holdings_count"):
        return {
            "available": True,
            "alerts": [],
            "holdings_count": 0,
            "note": "No holdings yet — connect a broker or log a trade to get personalized morning alerts.",
        }

    return {
        "available": True,
        "alerts": _portfolio_alerts(intelligence, market),
        "holdings_count": intelligence["holdings_count"],
        "risk": intelligence.get("risk"),
        "pnl": intelligence.get("pnl"),
        "note": None,
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

async def get_morning_report(
    db,
    user: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Today's morning report — shared market layer + this user's personal layer.

    The market layer is generated once per day and reused; the personal layer is
    always fresh. `force` regenerates the market layer (used by the scheduler,
    which must not serve yesterday's cached document).
    """
    today = _today()
    cached = None if force else await db.reports.find_one({"date": today, "type": REPORT_TYPE})

    steps: List[str] = []
    if not cached:
        steps.extend(MARKET_STEPS)
    if user:
        steps.append(PERSONAL_STEP)

    run = AIRun(user["_id"] if user else None, None, steps, run_id=run_id)
    await run.start()

    try:
        if cached:
            cached.pop("_id", None)
            market = cached
        else:
            market = await _build_market_layer(db, run)

        if not market.get("available"):
            await run.complete("warning")
            return market

        if user:
            async with run.step():
                market = {**market, "portfolio": await _build_personal_layer(db, user, market)}

        await run.complete()
        return market
    except Exception:
        await run.complete("warning")
        raise


async def generate_and_notify(db) -> Dict[str, Any]:
    """Scheduled 8:30 AM entry point: regenerate the report and notify users.

    Returns the generated market report.
    """
    report = await get_morning_report(db, user=None, force=True)

    try:
        from services.market_engine.event_bus import event_bus

        await event_bus.publish("morningreport.generated", {
            "date": report.get("date"),
            "picks": len(report.get("top_picks") or []),
            "available": report.get("available", False),
        })
    except Exception as exc:
        logger.warning("morningreport.generated publish failed: %s", exc)

    await notify_users(db, report)
    return report


async def notify_users(db, report: Dict[str, Any]) -> int:
    """Notify every user who opted into the morning report.

    Honors the `morning_report` notification preference that models.py defines —
    the previous implementation checked `trade_alerts` and only reached users who
    had already traded, so a new user never received the report they subscribed
    to. Returns the number of users notified.
    """
    from services.notification_service import create_notification

    if report.get("available"):
        picks = report.get("top_picks") or []
        if picks:
            message = (
                f"{report.get('market_mood', 'Market')} open indicated. "
                f"Top pick: {picks[0]['name']} ({picks[0]['confidence']}% confidence). "
                f"{len(picks)} setups ready."
            )
        else:
            message = (
                f"{report.get('market_mood', 'Market')} open indicated. "
                "Your pre-market briefing is ready."
            )
    else:
        message = "Live market data was unavailable this morning — the briefing could not be generated."

    notified = 0
    cursor = db.users.find({}, {"_id": 1, "email": 1, "notification_prefs": 1})
    async for user in cursor:
        prefs = user.get("notification_prefs") or {}
        if not prefs.get("morning_report", True):
            continue

        created = await create_notification(
            db, str(user["_id"]),
            type="MORNING_REPORT",
            title="Morning Briefing Ready",
            message=message,
            dedupe_minutes=180,  # one briefing per morning, even if the job retries
        )
        if not created:
            continue
        notified += 1

        if prefs.get("email_alerts", False) and user.get("email"):
            try:
                from services.email_service import build_morning_report_email, send_email

                subject, html = build_morning_report_email(report)
                await send_email(user["email"], subject, html)
            except Exception as exc:
                logger.error("Morning report email failed for %s: %s", user.get("email"), exc)

    logger.info("Morning report: notified %d user(s)", notified)
    return notified

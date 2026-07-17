"""Sprint 10 — Morning Report tests (hermetic: no network, Redis, or Mongo).

Locks the contracts that make the automated briefing trustworthy:

  • Every sprint section is present (global markets, Gift Nifty, news, economic
    calendar, scanner, top picks, risk warnings, portfolio alerts).
  • Nothing is fabricated: an unavailable section says so rather than inventing
    a plausible value — the rule that motivated this sprint's rewrite.
  • Gift Nifty degrades honestly with no licensed feed, and picks up a real one
    the moment an adapter is registered.
  • Portfolio alerts are per-user and never leak into the shared cached doc.
  • Notifications honor the `morning_report` preference and reach users who have
    never traded.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.market_engine import gift_nifty as gn

REAL_OVERVIEW = {
    "nifty": {"value": 24500.0, "change_pct": 0.8},
    "bank_nifty": {"value": 52000.0, "change_pct": -0.7},
    "sensex": {"value": 80500.0, "change_pct": 0.3},
    "india_vix": 17.2,
}
TOP_PICKS = {"picks": [{"symbol": "RELIANCE", "name": "Reliance", "confidence": 82}]}
SECTORS = [{"sector": "IT", "change_pct": 1.2}, {"sector": "Banking", "change_pct": -1.4}]
GLOBAL_MARKETS = [
    {"name": "Dow Jones", "region": "US", "value": 44000.0, "change_pct": 0.4, "available": True},
    {"name": "Nasdaq", "region": "US", "value": 20000.0, "change_pct": 0.9, "available": True},
    {"name": "Nikkei 225", "region": "Asia", "value": 39000.0, "change_pct": -0.2, "available": True},
]
NEWS_ARTICLES = [
    {"title": "RELIANCE surges on refining margin beat", "source": "ET", "link": "http://e.x/1",
     "sentiment": "positive", "importance": "high", "published": "2026-07-16T02:00:00"},
    {"title": "Markets steady ahead of data", "source": "BS", "link": "http://e.x/2",
     "sentiment": "neutral", "importance": "normal", "published": "2026-07-16T01:00:00"},
]
NEWS_SENTIMENT = {"available": True, "score": 62, "label": "Bullish", "articles_analyzed": 40}


def _run(coro):
    return asyncio.run(coro)


def _patches(**overrides):
    targets = {
        "services.real_market.fetch_real_market_overview": REAL_OVERVIEW,
        "services.real_market.fetch_real_top_picks": TOP_PICKS,
        "services.real_market.fetch_real_sectors": SECTORS,
        "services.real_market.fetch_real_fii_dii": {"fii": {"net": -1200}, "dii": {"net": 900}},
        "services.real_market.fetch_real_global_markets": GLOBAL_MARKETS,
        "services.news_service.fetch_news": NEWS_ARTICLES,
        "services.news_service.get_market_sentiment": NEWS_SENTIMENT,
    }
    targets.update(overrides)
    return [
        patch(t, new_callable=AsyncMock, **(
            {"side_effect": v} if isinstance(v, Exception) else {"return_value": v}))
        for t, v in targets.items()
    ]


def _generate(fake_db, user=None, **overrides):
    from services.morning_report import get_morning_report

    async def run():
        patches = _patches(**overrides)
        for p in patches:
            p.start()
        try:
            return await get_morning_report(fake_db, user=user)
        finally:
            for p in patches:
                p.stop()

    return _run(run())


@pytest.fixture(autouse=True)
def _isolate_gift_nifty():
    """Gift Nifty caches its answer (including unavailability) for 60s.

    Both the adapter registry and the cache are process-global, so without this
    one test's result would leak into the next.
    """
    from services.cache import cache_delete

    def _reset():
        gn.reset_adapters()
        _run(cache_delete(gn.CACHE_KEY))

    _reset()
    yield
    _reset()


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def test_report_contains_every_sprint_section(fake_db, no_ai):
    report = _generate(fake_db)

    for section in ("global_markets", "gift_nifty", "news", "economic_calendar",
                    "top_picks", "key_risks", "sectors", "fii_dii"):
        assert section in report, f"missing '{section}'"


def test_global_markets_summary_reflects_real_quotes(fake_db, no_ai):
    """The summary must describe what markets actually did.

    This replaced a hardcoded sentence that claimed the same thing every day.
    """
    report = _generate(fake_db)
    section = report["global_markets"]

    assert section["available"] is True
    assert section["advancing"] == 2 and section["declining"] == 1
    assert "broadly positive" in section["summary"]
    assert "Nasdaq" in section["summary"]  # best performer named


def test_global_markets_unavailable_is_not_invented(fake_db, no_ai):
    report = _generate(fake_db, **{"services.real_market.fetch_real_global_markets": []})

    assert report["global_markets"]["available"] is False
    assert "unavailable" in report["global_markets"]["summary"].lower()


def test_news_headlines_rank_high_importance_first(fake_db, no_ai):
    report = _generate(fake_db)
    headlines = report["news"]["headlines"]

    assert report["news"]["available"] is True
    assert headlines[0]["importance"] == "high"
    assert headlines[0]["title"].startswith("RELIANCE")


def test_economic_calendar_section_present(fake_db, no_ai):
    report = _generate(fake_db)
    calendar = report["economic_calendar"]

    assert calendar["available"] is True
    assert isinstance(calendar["today"], list)
    assert isinstance(calendar["upcoming"], list)


def test_risk_warnings_are_grounded_in_collected_data(fake_db, no_ai):
    report = _generate(fake_db)
    risks = " ".join(report["key_risks"])

    assert "17.2" in risks           # the real VIX reading
    assert "1,200" in risks or "1200" in risks  # the real FII net outflow
    assert "Bank Nifty" in risks


def test_unavailable_inputs_are_reported_not_invented(fake_db, no_ai):
    report = _generate(fake_db, **{
        "services.real_market.fetch_real_fii_dii": {},
        "services.news_service.get_market_sentiment": {"available": False},
    })
    risks = " ".join(report["key_risks"]).lower()

    assert "fii/dii flow unavailable" in risks
    assert "news sentiment unavailable" in risks


# ---------------------------------------------------------------------------
# Gift Nifty
# ---------------------------------------------------------------------------

def test_gift_nifty_unavailable_without_a_licensed_feed(fake_db, no_ai):
    report = _generate(fake_db)
    gift = report["gift_nifty"]

    assert gift["available"] is False
    assert gift["value"] is None, "Gift Nifty must never be fabricated"
    assert "NSE IX" in gift["note"]


def test_gift_nifty_uses_a_registered_adapter():
    async def run():
        gn.register_adapter(
            "test_feed",
            AsyncMock(return_value={"value": 24610.0, "previous_close": 24500.0}),
            tier="streaming",
        )
        return await gn.get_gift_nifty(force=True)

    quote = _run(run())

    assert quote["available"] is True
    assert quote["value"] == 24610.0
    assert quote["change"] == 110.0
    assert quote["change_pct"] == 0.45
    assert quote["source_tier"] == "streaming"


def test_gift_nifty_falls_through_a_failing_adapter():
    """One broken provider must not take the section down."""
    async def run():
        gn.register_adapter("broken", AsyncMock(side_effect=RuntimeError("feed down")))
        gn.register_adapter("backup", AsyncMock(return_value={"value": 24600.0}))
        return await gn.get_gift_nifty(force=True)

    quote = _run(run())

    assert quote["available"] is True
    assert quote["value"] == 24600.0
    assert quote["change_pct"] is None  # no previous close supplied — not guessed


def test_gift_nifty_rejects_a_nonsense_quote():
    async def run():
        gn.register_adapter("bad", AsyncMock(return_value={"value": -5}))
        return await gn.get_gift_nifty(force=True)

    assert _run(run())["available"] is False


def test_gift_nifty_gap_becomes_a_risk_warning(fake_db, no_ai):
    gn.register_adapter(
        "test_feed", AsyncMock(return_value={"value": 24990.0, "previous_close": 24500.0}))

    report = _generate(fake_db)
    risks = " ".join(report["key_risks"])

    assert "Gift Nifty" in risks and "gap-up" in risks


# ---------------------------------------------------------------------------
# Personal layer
# ---------------------------------------------------------------------------

HOLDINGS_INTEL = {
    "holdings_count": 2,
    "holdings": [
        {"symbol": "RELIANCE", "name": "Reliance", "sector": "Energy", "pnl_pct": 4.0},
        {"symbol": "HDFCBANK", "name": "HDFC Bank", "sector": "Banking", "pnl_pct": -3.0},
    ],
    "risk": {"score": 55, "level": "Elevated",
             "factors": [{"name": "Sector concentration", "points": 20,
                          "detail": "Banking is 60% of holdings."}]},
    "pnl": {"unrealized": -1200.0},
    "suggestions": [],
}


def _with_holdings(fake_db, user):
    with patch("services.portfolio_engine.build_intelligence",
               new_callable=AsyncMock, return_value=HOLDINGS_INTEL):
        return _generate(fake_db, user=user)


def test_portfolio_alerts_connect_market_events_to_holdings(fake_db, no_ai):
    report = _with_holdings(fake_db, {"_id": "u1", "email": "a@b.c"})
    alerts = report["portfolio"]["alerts"]

    assert report["portfolio"]["available"] is True
    titles = " ".join(a["title"] for a in alerts)

    # Banking is down 1.4% today and the user holds HDFCBANK.
    assert "Banking weakness affects your holdings" in titles
    # A high-importance headline names a stock the user owns.
    assert "News on your RELIANCE position" in titles
    # Every alert explains itself, per the product's education-first rule.
    assert all(a.get("why") for a in alerts)


def test_portfolio_alerts_are_severity_ordered(fake_db, no_ai):
    report = _with_holdings(fake_db, {"_id": "u1", "email": "a@b.c"})
    rank = {"critical": 0, "warning": 1, "info": 2}
    severities = [rank[a["severity"]] for a in report["portfolio"]["alerts"]]

    assert severities == sorted(severities)


def test_no_holdings_gives_a_helpful_empty_state(fake_db, no_ai):
    empty = {**HOLDINGS_INTEL, "holdings_count": 0, "holdings": []}
    with patch("services.portfolio_engine.build_intelligence",
               new_callable=AsyncMock, return_value=empty):
        report = _generate(fake_db, user={"_id": "u1"})

    portfolio = report["portfolio"]
    assert portfolio["alerts"] == []
    assert "connect a broker" in portfolio["note"].lower()


def test_portfolio_failure_degrades_only_that_section(fake_db, no_ai):
    with patch("services.portfolio_engine.build_intelligence",
               new_callable=AsyncMock, side_effect=RuntimeError("broker down")):
        report = _generate(fake_db, user={"_id": "u1"})

    assert report["available"] is True        # briefing still delivered
    assert report["portfolio"]["available"] is False
    assert report["ai_briefing"]


def test_personal_layer_never_enters_the_shared_cache(fake_db, no_ai):
    """The shared doc is keyed by date alone — a per-user field written into it
    would be served to the next user who asks."""
    _with_holdings(fake_db, {"_id": "u1", "email": "a@b.c"})

    stored = [d for d in fake_db.reports.docs if d.get("type") == "morning"]
    assert len(stored) == 1
    assert "portfolio" not in stored[0]


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def test_notifications_honor_the_morning_report_preference(fake_db):
    """The old job checked `trade_alerts` and only swept users who had traded,
    so a subscriber who had never placed a trade was silently skipped."""
    from services.morning_report import notify_users

    fake_db.users.docs.extend([
        {"_id": "opted_in", "email": "in@x.com",
         "notification_prefs": {"morning_report": True}},
        {"_id": "opted_out", "email": "out@x.com",
         "notification_prefs": {"morning_report": False}},
        {"_id": "never_traded", "email": "new@x.com", "notification_prefs": {}},
    ])
    report = {"available": True, "market_mood": "Bullish", "top_picks": TOP_PICKS["picks"]}

    notified = _run(notify_users(fake_db, report))

    recipients = {n["user_id"] for n in fake_db.notifications.docs}
    assert notified == 2
    assert recipients == {"opted_in", "never_traded"}, "default-on pref must reach new users"


def test_notification_message_names_the_top_pick(fake_db):
    from services.morning_report import notify_users

    fake_db.users.docs.append({"_id": "u1", "email": "u@x.com", "notification_prefs": {}})
    report = {"available": True, "market_mood": "Bullish", "top_picks": TOP_PICKS["picks"]}

    _run(notify_users(fake_db, report))

    message = fake_db.notifications.docs[0]["message"]
    assert "Reliance" in message and "82%" in message


def test_unavailable_report_notifies_honestly(fake_db):
    from services.morning_report import notify_users

    fake_db.users.docs.append({"_id": "u1", "email": "u@x.com", "notification_prefs": {}})

    _run(notify_users(fake_db, {"available": False}))

    message = fake_db.notifications.docs[0]["message"]
    assert "unavailable" in message.lower()

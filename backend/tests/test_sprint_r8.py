"""Sprint R8 — Notifications & Watchlist live migration.

Covers the new pure logic introduced in R8:
  • breaking-news importance classification + novelty gating (news_service)
  • event bridge channel routing for the new watchlist/morningreport domains
"""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DISABLE_BACKGROUND_ENGINE", "1")

from services import news_service
from services.realtime.event_bridge import resolve_channel


# ── Importance classification ─────────────────────────────────────────────

def test_breaking_terms_classified_high():
    assert news_service._classify_importance("Sensex crashes 1,200 points") == "high"
    assert news_service._classify_importance("RBI rate decision: repo rate cut by 25 bps") == "high"
    assert news_service._classify_importance("Nifty hits record high on FII inflows") == "high"


def test_routine_headlines_classified_normal():
    assert news_service._classify_importance("TCS shares rise 2% after strong quarter") == "normal"
    assert news_service._classify_importance("Five stocks to watch this week") == "normal"
    assert news_service._classify_importance("") == "normal"


# ── Breaking novelty gate ─────────────────────────────────────────────────

def _article(title, breaking=True):
    return {"title": title, "is_breaking": breaking, "summary": ""}


def test_filter_breaking_novel_suppresses_repeats():
    news_service.reset_breaking_state()
    first = news_service.filter_breaking_novel([_article("Sensex crashes 1,200 points")])
    assert len(first) == 1
    # Same headline on the next scan: suppressed within the cooldown window.
    second = news_service.filter_breaking_novel([_article("Sensex crashes 1,200 points")])
    assert second == []


def test_filter_breaking_novel_ignores_non_breaking():
    news_service.reset_breaking_state()
    out = news_service.filter_breaking_novel([_article("Quiet day on D-Street", breaking=False)])
    assert out == []


def test_filter_breaking_novel_expires_cooldown():
    news_service.reset_breaking_state()
    past = datetime.now(timezone.utc) - timedelta(
        minutes=news_service.BREAKING_COOLDOWN_MINUTES + 1)
    assert news_service.filter_breaking_novel([_article("SEBI bans broker X")], now=past)
    # After the cooldown expires the same headline may stream again.
    again = news_service.filter_breaking_novel([_article("SEBI bans broker X")])
    assert len(again) == 1


def test_filter_breaking_novel_skips_untitled():
    news_service.reset_breaking_state()
    assert news_service.filter_breaking_novel([{"is_breaking": True, "title": ""}]) == []


# ── Bridge channel routing (Sprint R8 domains) ────────────────────────────

def test_watchlist_events_route_to_watchlist_channel():
    assert resolve_channel("watchlist.quotes") == "watchlist"
    assert resolve_channel("watchlist.updated") == "watchlist"


def test_morningreport_routes_to_ai_channel():
    assert resolve_channel("morningreport.generated") == "ai"


def test_news_breaking_routes_to_news_channel():
    assert resolve_channel("news.breaking") == "news"

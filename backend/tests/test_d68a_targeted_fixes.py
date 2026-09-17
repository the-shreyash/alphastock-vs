"""D6.8-A targeted fix pass — F-1, F-2, F-6, F-5.

THE FOUR DEFECTS, AND THE ONE THING THEY HAVE IN COMMON
──────────────────────────────────────────────────────
D6.8-A stopped the platform fabricating market readings. It did that by
replacing `quote.get(field) or <placeholder>` with a `FieldQuality` contract —
and every one of the four defects below is a place where the *removal* of the
fabricated value was not matched by the consumer that had been relying on it.

    F-1  the contract was applied to fields it does not cover, so a field with
         no staleness clock of its own borrowed one that does not describe it
    F-2  a consumer kept a `dict.get` default that never fired, so the value it
         was defaulting *away from* reached a comparison as `None`
    F-6  four more of the same, in aggregations and batch scans, where the
         raise took down every symbol and not just the unmeasured one
    F-5  a conditional expression whose scope was wider than its author meant,
         so an absent reading deleted the whole narrative rather than one clause

NOT A REDESIGN OF `FieldQuality`, and not a change to any score. Section 5
asserts the second of those directly rather than trusting it.

Each section pairs the defect's regression test with a FALSIFYING TWIN — the
measured case that must still behave — because a "no crash" assertion passes
just as well against a function that now returns nothing at all.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

import pytest

import server
from services import heartbeat_engine, portfolio_monitor, real_market
from services.market_engine import field_quality, scanner_engine
from services.market_engine.field_quality import (
    OBSERVED_AT_KEY,
    QUALITY_KEY,
    QUALITY_TRACKED_FIELDS,
    FieldQuality,
)
from tests._fakedb import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _code_only(source: str) -> str:
    """`source` with `#` comments dropped, line by line.

    A source sweep that reads the comments finds its own forbidden string in the
    sentence explaining why it is forbidden, and then fails when the comment is
    written and passes when it is deleted — exactly backwards.

    Crude on purpose, and the same stripper `test_d68_data_quality.py` uses: a
    `#` inside a string literal truncates that line too, which can only ever
    make the sweep STRICTER. A false PASS is not reachable from here.
    """
    return "\n".join(line.split("#")[0] for line in source.splitlines())


def _ago(**kwargs):
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()


# --------------------------------------------------------------------------- #
# 1. F-1 — price and volume are not governed by technical-field staleness       #
# --------------------------------------------------------------------------- #
#
# `OBSERVED_AT_KEY` is stamped by `real_market._series_observed_at` from the
# newest DAILY BAR the indicators were computed from. It ages the six fields in
# `QUALITY_TRACKED_FIELDS`. `price` and `volume` are not among them and are not
# derived from the bar series at all — they are the live quote.
#
# Routing them through `field_quality.reading()` lent them that clock, so a quote
# whose daily series had not advanced (a Monday morning, or a feed that stopped
# delivering bars) had its perfectly current price classified STALE, and
# `price_min` / `price_max` / `volume_min` refused it.

#: The shape the defect needs: current price and volume, a three-day-old bar
#: series, and one technical that is a real reading of that old series.
STALE_SERIES_QUOTE = {
    "symbol": "OLDBARS",
    "sector": "Oil & Gas",
    "price": 1000.0,
    "volume": 5_000_000,
    "rsi": 55.0,
    OBSERVED_AT_KEY: _ago(days=3),
}


def test_the_quote_under_test_really_is_stale_by_the_technical_clock():
    """The premise, asserted rather than assumed.

    If the fixture were not stale the three assertions below would pass against
    the unfixed code too, and this whole section would be vacuous.
    """
    assert field_quality.quality_of(STALE_SERIES_QUOTE, "rsi") is FieldQuality.STALE
    assert field_quality.reading(STALE_SERIES_QUOTE, "rsi") is None


def test_a_stale_bar_series_does_not_make_the_current_price_unusable():
    """F-1: `price_min` and `price_max` test the live price, whatever the daily
    series is doing."""
    assert scanner_engine._passes_filters(STALE_SERIES_QUOTE, {"price_min": 500}) is True
    assert scanner_engine._passes_filters(STALE_SERIES_QUOTE, {"price_max": 1500}) is True


def test_a_stale_bar_series_does_not_make_the_current_volume_unusable():
    """F-1: `volume_min` keeps its existing semantics — a real volume is
    compared against the bound."""
    assert scanner_engine._passes_filters(STALE_SERIES_QUOTE, {"volume_min": 1_000_000}) is True
    assert scanner_engine._passes_filters(STALE_SERIES_QUOTE, {"volume_min": 9_000_000}) is False


@pytest.mark.parametrize(
    "bare",
    [
        pytest.param({"symbol": "B", "price": None, "volume": None}, id="key-present-null"),
        # The key ABSENT is the case a re-introduced `quote.get(field, 0)` default
        # would silently pass — 0 satisfies a `price_max` and, before D6.8-A, the
        # old `or 0` made exactly that substitution.
        pytest.param({"symbol": "B"}, id="key-absent"),
    ],
)
def test_the_price_and_volume_bounds_still_refuse_an_absent_value(bare):
    """The existing None/missing semantics are preserved, not loosened. A quote
    with no price is still refused by a price bound, whichever way it is absent."""
    assert scanner_engine._passes_filters(bare, {"price_min": 1}) is False
    assert scanner_engine._passes_filters(bare, {"price_max": 10_000}) is False
    assert scanner_engine._passes_filters(bare, {"volume_min": 1}) is False


def test_the_technical_fields_are_still_governed_by_that_clock():
    """THE OTHER HALF OF F-1, and the reason the fix is narrow.

    `price`/`volume` leaving the contract must not take the tracked fields with
    them: a three-day-old RSI is still not a fact about now, so a
    `volume_ratio_min` bound still refuses and an RSI bound is still reported as
    unverified.
    """
    stale = dict(STALE_SERIES_QUOTE, volume_ratio=2.0)

    assert scanner_engine._passes_filters(stale, {"volume_ratio_min": 1.0}) is False
    assert scanner_engine.unverified_filters(stale, {"rsi_min": 40}) == ["rsi_min"]


def test_a_stale_technical_is_refused_even_while_price_and_volume_pass():
    """The two rules in one scan, which is the state the fix creates: the stock
    is measurable by price and not by its technicals."""
    stale = dict(STALE_SERIES_QUOTE, change_pct=3.0)
    filters = {"price_min": 500, "volume_min": 1_000_000, "change_pct_min": 1.0}

    assert scanner_engine._passes_filters(stale, {"price_min": 500}) is True
    assert scanner_engine._passes_filters(stale, filters) is False


def test_the_fresh_twin_passes_every_one_of_those_bounds():
    """The falsifying twin: the same quote with a current series passes the
    whole filter set, so the refusal above is the staleness and not the shape."""
    fresh = dict(STALE_SERIES_QUOTE, change_pct=3.0, **{OBSERVED_AT_KEY: _ago(hours=1)})
    filters = {"price_min": 500, "volume_min": 1_000_000, "change_pct_min": 1.0}

    assert scanner_engine._passes_filters(fresh, filters) is True


def test_price_and_volume_did_not_become_quality_tracked():
    """The fix is a routing change, not an extension of the contract. D6.8-A's
    closed vocabulary is unchanged."""
    assert "price" not in QUALITY_TRACKED_FIELDS
    assert "volume" not in QUALITY_TRACKED_FIELDS
    assert len(FieldQuality) == 6
    assert field_quality.DAILY_READING_MAX_AGE_SECONDS == 24 * 3600


# --------------------------------------------------------------------------- #
# 2. F-2 — the portfolio monitor may not alert on a reading it does not have    #
# --------------------------------------------------------------------------- #

_TRADE = {
    "user_id": "u1",
    "status": "OPEN",
    "symbol": "RELIANCE",
    "entry_price": 1000.0,
    "stop_loss": 900.0,
    "target1": 1200.0,
    "quantity": 10,
}


def _monitor(quote, trade=None):
    """Drive `analyze_portfolio_health` over one open position carrying `quote`."""
    db = FakeDB(trades=[dict(trade or _TRADE)], users=[], notifications=[])
    return _run(portfolio_monitor.analyze_portfolio_health(db, "u1", None, lambda symbol: dict(quote, symbol=symbol)))


def _types(result):
    return {a["type"] for a in result["alerts"]}


def test_an_available_overbought_rsi_still_raises_its_alert():
    """The positive control. Everything below asserts an alert is ABSENT, and
    an absent alert proves nothing unless the present one is reachable."""
    result = _monitor({"price": 1050.0, "rsi": 80.0, "volume_ratio": 1.0})

    assert "RSI_OVERBOUGHT" in _types(result)


def test_an_available_volume_spike_still_raises_its_alert():
    result = _monitor({"price": 1050.0, "rsi": 50.0, "volume_ratio": 3.0})

    assert "VOLUME_SPIKE" in _types(result)


def test_a_none_rsi_neither_crashes_nor_alerts():
    """F-2: `quote.get("rsi", 50)` defaults on a MISSING KEY, and the key is
    present carrying None — so `None > 75` raised out of the whole cycle."""
    result = _monitor({"price": 1050.0, "rsi": None, "volume_ratio": 3.0})

    assert "RSI_OVERBOUGHT" not in _types(result)
    assert "VOLUME_SPIKE" in _types(result)


def test_a_none_volume_ratio_neither_crashes_nor_alerts():
    result = _monitor({"price": 1050.0, "rsi": 80.0, "volume_ratio": None})

    assert "VOLUME_SPIKE" not in _types(result)
    assert "RSI_OVERBOUGHT" in _types(result)


@pytest.mark.parametrize(
    "state",
    [
        FieldQuality.INSUFFICIENT_HISTORY,
        FieldQuality.PROVIDER_ERROR,
        FieldQuality.UNAVAILABLE,
        FieldQuality.MISSING,
    ],
)
def test_no_non_available_state_can_produce_an_alert(state):
    """AN ALERT TEXT IS A FACTUAL CLAIM.

    "RELIANCE RSI at 80 (overbought)" asserts a measurement. A declared
    non-AVAILABLE state must therefore silence the alert even when the payload
    still carries a number — which is exactly the case a `try`/`except TypeError`
    around the comparison would have let through.
    """
    result = _monitor(
        {
            "price": 1050.0,
            "rsi": 80.0,
            "volume_ratio": 3.0,
            QUALITY_KEY: field_quality.all_missing(QUALITY_TRACKED_FIELDS, state),
        }
    )

    assert "RSI_OVERBOUGHT" not in _types(result)
    assert "VOLUME_SPIKE" not in _types(result)


def test_a_stale_reading_cannot_produce_an_alert_either():
    """STALE is the state the value is real for, and it is still not now."""
    result = _monitor(
        {
            "price": 1050.0,
            "rsi": 80.0,
            "volume_ratio": 3.0,
            OBSERVED_AT_KEY: _ago(days=3),
        }
    )

    assert "RSI_OVERBOUGHT" not in _types(result)
    assert "VOLUME_SPIKE" not in _types(result)


def test_the_stale_twin_alerts_when_the_reading_is_current():
    """The falsifying twin for the staleness gate: same payload, fresh clock."""
    result = _monitor(
        {
            "price": 1050.0,
            "rsi": 80.0,
            "volume_ratio": 3.0,
            OBSERVED_AT_KEY: _ago(hours=1),
        }
    )

    assert {"RSI_OVERBOUGHT", "VOLUME_SPIKE"} <= _types(result)


def test_a_position_with_no_technicals_is_still_monitored_for_price_risk():
    """The gate withholds claims about absent fields and nothing else. The
    price-based alerts are the ones that actually protect capital, and a quote
    with no indicators must not cost the user those.
    """
    result = _monitor({"price": 890.0, "rsi": None, "volume_ratio": None})

    assert "STOP_LOSS_HIT" in _types(result)
    assert result["at_risk"] == 1


def test_one_user_without_technicals_does_not_abort_the_cycle_for_the_rest():
    """F-2's blast radius, which is the reason it is HIGH and not MEDIUM.

    `analyze_portfolio_health` raising took `run_monitoring_cycle` with it, so a
    single unmeasured symbol silenced alerting for EVERY user behind it in the
    loop — including the stop-loss alerts of users whose own data was fine.
    `FakeCollection.distinct` follows first appearance, so the user whose quote
    carries no technicals is processed FIRST and the assertion is about what
    survives it.
    """
    from bson import ObjectId

    bad, good = str(ObjectId()), str(ObjectId())
    db = FakeDB(
        trades=[
            dict(_TRADE, user_id=bad, symbol="NOTECH"),
            dict(_TRADE, user_id=good, symbol="OK"),
        ],
        users=[{"_id": ObjectId(uid), "email": None, "notification_prefs": {}} for uid in (bad, good)],
        notifications=[],
    )
    quotes = {
        # No technicals at all — the payload that used to raise.
        "NOTECH": {"price": 1050.0, "rsi": None, "volume_ratio": None},
        # Below its stop loss: a critical alert the cycle must still deliver.
        "OK": {"price": 890.0, "rsi": 50.0, "volume_ratio": 1.0},
    }

    total = _run(portfolio_monitor.run_monitoring_cycle(db, lambda s: quotes[s]))

    delivered = _run(db.notifications.find({}).to_list(None))
    assert total == 1, delivered
    assert [(n["user_id"], n["type"]) for n in delivered] == [(good, "STOP_LOSS_HIT")]


def test_that_cycle_assertion_could_have_failed():
    """The falsifying twin for the blast-radius test: with the second user's
    position ALSO healthy, the cycle delivers nothing — so the single
    notification above is the alert being produced, not a constant."""
    from bson import ObjectId

    bad, good = str(ObjectId()), str(ObjectId())
    db = FakeDB(
        trades=[
            dict(_TRADE, user_id=bad, symbol="NOTECH"),
            dict(_TRADE, user_id=good, symbol="OK"),
        ],
        users=[{"_id": ObjectId(uid), "email": None, "notification_prefs": {}} for uid in (bad, good)],
        notifications=[],
    )
    quotes = {
        "NOTECH": {"price": 1050.0, "rsi": None, "volume_ratio": None},
        "OK": {"price": 1050.0, "rsi": 50.0, "volume_ratio": 1.0},
    }

    assert _run(portfolio_monitor.run_monitoring_cycle(db, lambda s: quotes[s])) == 0


def test_the_monitor_does_not_read_a_technical_through_a_dict_default():
    """`quote.get("rsi", 50)` only fires on a missing KEY, and the key is always
    present — a default covering for a fabrication that no longer exists.

    A source assertion because the alternative is unreachable through behaviour:
    a `dict.get` default and a `field_quality.reading` agree on every payload
    whose key is absent, and disagree only on the payloads production sends.
    """
    source = _code_only(inspect.getsource(portfolio_monitor.analyze_portfolio_health))

    assert 'quote.get("rsi", 50)' not in source
    assert 'quote.get("volume_ratio", 1.0)' not in source
    assert 'field_quality.reading(quote, "rsi")' in source
    assert 'field_quality.reading(quote, "volume_ratio")' in source
    assert "or 50" not in source and "or 1.0" not in source


# --------------------------------------------------------------------------- #
# 3. F-6 — gainers, losers, sectors and the heartbeat batch scans               #
# --------------------------------------------------------------------------- #


def _universe(monkeypatch, quotes):
    async def _fetch():
        return [dict(q) for q in quotes]

    monkeypatch.setattr(real_market, "fetch_all_universe_quotes", _fetch)


#: Two measured stocks and one whose day change the platform does not have.
_MOVERS = [
    {"symbol": "UP", "sector": "Oil & Gas", "price": 100.0, "change_pct": 4.0},
    {"symbol": "DOWN", "sector": "Oil & Gas", "price": 100.0, "change_pct": -3.0},
    {
        "symbol": "UNMEASURED",
        "sector": "Oil & Gas",
        "price": 100.0,
        "change_pct": None,
        QUALITY_KEY: {"change_pct": FieldQuality.UNAVAILABLE.value},
    },
]


def test_an_unmeasured_day_change_does_not_abort_the_gainers_list(monkeypatch):
    """F-6: `sorted(quotes, key=lambda x: x.get("change_pct", 0))` raised on the
    first `None`, and the `dict.get` default never fired because the key is
    present. One symbol took the whole list down."""
    _universe(monkeypatch, _MOVERS)

    gainers = _run(real_market.fetch_real_gainers(5))

    assert [g["symbol"] for g in gainers] == ["UP", "DOWN"]


def test_an_unmeasured_day_change_does_not_abort_the_losers_list(monkeypatch):
    _universe(monkeypatch, _MOVERS)

    losers = _run(real_market.fetch_real_losers(5))

    assert [loser["symbol"] for loser in losers] == ["DOWN", "UP"]


def test_an_unmeasured_stock_is_not_published_as_a_flat_mover(monkeypatch):
    """The second half of F-6, and the one a `key=... or 0` fallback would miss.

    Membership in "Top Gainers" IS the claim. A substituted 0 would have placed
    a stock nobody measured in a ranking of day changes and shown it at 0.00%.
    """
    _universe(monkeypatch, _MOVERS)

    gainers = _run(real_market.fetch_real_gainers(5))

    assert all(g["change_pct"] is not None for g in gainers)
    assert "UNMEASURED" not in {g["symbol"] for g in gainers}


def test_a_stale_day_change_is_not_a_mover(monkeypatch):
    """The movers filter reads the QUALITY, not just `is not None`.

    `change_pct` is a quality-tracked field, so a real number from a bar series
    that stopped advancing is not a fact about today's session — and "Top
    Gainers" is a claim about today. A `q.get("change_pct") is not None` test
    would publish it.
    """
    _universe(
        monkeypatch,
        [
            {"symbol": "FRESH", "change_pct": 1.0, OBSERVED_AT_KEY: _ago(hours=1)},
            {"symbol": "STALE", "change_pct": 9.0, OBSERVED_AT_KEY: _ago(days=3)},
        ],
    )

    assert [g["symbol"] for g in _run(real_market.fetch_real_gainers(5))] == ["FRESH"]


def test_the_movers_list_is_still_ordered_by_the_real_numbers(monkeypatch):
    """The falsifying twin: a fully measured universe orders exactly as before."""
    _universe(
        monkeypatch,
        [
            {"symbol": "A", "change_pct": 1.0},
            {"symbol": "B", "change_pct": 5.0},
            {"symbol": "C", "change_pct": -2.0},
        ],
    )

    assert [g["symbol"] for g in _run(real_market.fetch_real_gainers(3))] == ["B", "A", "C"]
    assert [x["symbol"] for x in _run(real_market.fetch_real_losers(3))] == ["C", "A", "B"]


def test_sector_performance_ignores_an_unmeasurable_stock(monkeypatch):
    """F-6: `sum()` over a list containing `None` raises; and had the old
    default fired, an unmeasured stock would have pulled its sector's PUBLISHED
    performance toward zero."""
    _universe(
        monkeypatch,
        [
            {"symbol": "A", "sector": "Energy", "change_pct": 2.0},
            {"symbol": "B", "sector": "Energy", "change_pct": 4.0},
            {
                "symbol": "C",
                "sector": "Energy",
                "change_pct": None,
                QUALITY_KEY: {"change_pct": FieldQuality.PROVIDER_ERROR.value},
            },
        ],
    )

    sectors = _run(real_market.fetch_real_sectors())

    # 3.0 = mean(2, 4). Folding C in as 0 would publish 2.0.
    assert sectors == [{"sector": "Energy", "change_pct": 3.0}]


def test_a_sector_with_no_measured_stock_is_omitted_rather_than_reported_flat(monkeypatch):
    """ "Energy 0.00%" is a claim about a sector, and reporting it from zero
    measurements is the fabrication this phase removes."""
    _universe(
        monkeypatch,
        [
            {
                "symbol": "A",
                "sector": "Energy",
                "change_pct": None,
                QUALITY_KEY: {"change_pct": FieldQuality.UNAVAILABLE.value},
            },
            {"symbol": "B", "sector": "Metals", "change_pct": 1.5},
        ],
    )

    assert _run(real_market.fetch_real_sectors()) == [{"sector": "Metals", "change_pct": 1.5}]


def test_a_stale_day_change_is_left_out_of_its_sector_average(monkeypatch):
    """The sector average reads the QUALITY too, for the same reason the movers
    list does: a published sector performance is a claim about today, and a
    number from a bar series that stopped advancing is a fact about Friday.

    A `q.get("change_pct") is not None` test would fold it in.
    """
    _universe(
        monkeypatch,
        [
            {"symbol": "A", "sector": "Energy", "change_pct": 2.0, OBSERVED_AT_KEY: _ago(hours=1)},
            {"symbol": "B", "sector": "Energy", "change_pct": 8.0, OBSERVED_AT_KEY: _ago(days=3)},
        ],
    )

    # 2.0, not mean(2, 8) == 5.0.
    assert _run(real_market.fetch_real_sectors()) == [{"sector": "Energy", "change_pct": 2.0}]


def test_sector_averages_are_unchanged_for_a_fully_measured_universe(monkeypatch):
    """The falsifying twin."""
    _universe(
        monkeypatch,
        [
            {"symbol": "A", "sector": "Energy", "change_pct": 2.0},
            {"symbol": "B", "sector": "Energy", "change_pct": 4.0},
            {"symbol": "C", "sector": "Metals", "change_pct": -1.0},
        ],
    )

    assert _run(real_market.fetch_real_sectors()) == [
        {"sector": "Energy", "change_pct": 3.0},
        {"sector": "Metals", "change_pct": -1.0},
    ]


# ── the heartbeat tasks ───────────────────────────────────────────────────── #


class _ActivityLog:
    """Captures what a heartbeat task actually claimed it did.

    A task swallows every exception into `log_activity(..., "warning")`, so
    "it did not raise" is true of the broken code too. The status is the only
    observable that tells a completed scan from an aborted one.
    """

    def __init__(self):
        self.entries = []

    def __call__(self, action, category, status="done", **kwargs):
        self.entries.append((action, status))

    @property
    def statuses(self):
        return [status for _action, status in self.entries]

    def said(self, fragment):
        return [a for a, _s in self.entries if fragment in a]


@pytest.fixture
def activity(monkeypatch):
    import services.activity_logger as activity_logger

    log = _ActivityLog()
    monkeypatch.setattr(activity_logger, "log_platform_activity", log)

    async def _publish(*_a, **_kw):
        return None

    monkeypatch.setattr(heartbeat_engine, "_publish", _publish)
    return log


def test_a_global_index_without_a_change_does_not_lose_the_whole_reading(monkeypatch, activity):
    """F-6: `max(valid, key=lambda m: m.get("change_pct", 0))` compared `None`
    against a float, and `f"{None:+.2f}"` could not render it either."""

    async def _markets():
        return [
            {"name": "Nikkei 225", "region": "Asia", "value": 39000.0, "change_pct": None, "available": True},
            {"name": "Nasdaq", "region": "US", "value": 18000.0, "change_pct": 1.4, "available": True},
        ]

    monkeypatch.setattr(real_market, "fetch_real_global_markets", _markets)
    _run(heartbeat_engine.task_global_markets())

    assert "warning" not in activity.statuses, activity.entries
    assert activity.said("Nasdaq +1.40%"), activity.entries


def test_global_markets_name_no_leader_when_none_can_be_ranked(monkeypatch, activity):
    """…and when nothing is measurable the task still completes, without
    naming a leader it could not have chosen."""

    async def _markets():
        return [{"name": "Nikkei 225", "region": "Asia", "value": 39000.0, "change_pct": None, "available": True}]

    monkeypatch.setattr(real_market, "fetch_real_global_markets", _markets)
    _run(heartbeat_engine.task_global_markets())

    assert "warning" not in activity.statuses, activity.entries
    assert not activity.said("Nikkei 225 "), activity.entries


def test_a_us_index_without_a_change_does_not_lose_the_other_two(monkeypatch, activity):
    """F-6: one unrenderable index used to abort the whole line."""

    async def _quote(ticker, range_str="2d", *a, **kw):
        if ticker == "^IXIC":
            return {"price": 18000.0, "change_pct": None}
        return {"price": 5000.0, "change_pct": 0.8}

    monkeypatch.setattr(real_market, "fetch_yahoo_quote", _quote)
    _run(heartbeat_engine.task_us_markets())

    assert "warning" not in activity.statuses, activity.entries
    assert activity.said("S&P 500 +0.80%"), activity.entries
    assert not activity.said("Nasdaq"), activity.entries


def test_an_unmeasured_symbol_does_not_abort_the_breakout_scan(monkeypatch, activity):
    """F-6: the raise escaped the comprehension, so ONE unmeasured symbol turned
    the whole scan into "Finding Breakouts failed"."""

    async def _quotes():
        return [
            {
                "symbol": "BAD",
                "price": 100.0,
                "high": 100.0,
                "change_pct": None,
                QUALITY_KEY: {"change_pct": FieldQuality.PROVIDER_ERROR.value},
            },
            {"symbol": "GOOD", "price": 200.0, "high": 200.0, "change_pct": 3.0},
        ]

    monkeypatch.setattr(real_market, "fetch_all_universe_quotes", _quotes)
    monkeypatch.setattr(heartbeat_engine.scanner_worker, "filter_novel", lambda _k, c: list(c))
    _run(heartbeat_engine.task_find_breakouts())

    assert "warning" not in activity.statuses, activity.entries
    assert activity.said("Found 1 breakout candidate(s): GOOD"), activity.entries


def test_the_breakout_scan_still_finds_nothing_when_there_is_nothing(monkeypatch, activity):
    """The falsifying twin: a clean universe with no breakout reports exactly
    that, so "found 1" above is discrimination and not a constant."""

    async def _quotes():
        return [{"symbol": "FLAT", "price": 100.0, "high": 110.0, "change_pct": 0.1}]

    monkeypatch.setattr(real_market, "fetch_all_universe_quotes", _quotes)
    _run(heartbeat_engine.task_find_breakouts())

    assert activity.said("No breakouts right now"), activity.entries


def test_an_unmeasured_symbol_does_not_abort_the_volume_batch(monkeypatch, activity):
    """F-6, the fourth site. Same shape, same blast radius: the whole rotating
    batch was lost to one symbol."""

    async def _quote(symbol, *a, **kw):
        if symbol == "BAD":
            return {"symbol": "BAD", "volume_ratio": None, QUALITY_KEY: {"volume_ratio": FieldQuality.MISSING.value}}
        return {"symbol": symbol, "volume_ratio": 2.4}

    monkeypatch.setattr(real_market, "fetch_real_stock_quote", _quote)
    monkeypatch.setattr(heartbeat_engine, "_next_volume_batch", lambda: ["BAD", "OK"])
    monkeypatch.setattr(heartbeat_engine.scanner_worker, "filter_novel", lambda _k, c: list(c))
    _run(heartbeat_engine.task_check_volume())

    assert "warning" not in activity.statuses, activity.entries
    assert activity.said("1/2 stocks with unusual volume: OK"), activity.entries


def test_the_volume_batch_still_reports_a_quiet_market(monkeypatch, activity):
    """The falsifying twin."""

    async def _quote(symbol, *a, **kw):
        return {"symbol": symbol, "volume_ratio": 1.0}

    monkeypatch.setattr(real_market, "fetch_real_stock_quote", _quote)
    monkeypatch.setattr(heartbeat_engine, "_next_volume_batch", lambda: ["A", "B"])
    _run(heartbeat_engine.task_check_volume())

    assert activity.said("Volume normal across 2 stocks scanned"), activity.entries


# --------------------------------------------------------------------------- #
# 4. F-5 — the advisor narrative's conditional governs one clause              #
# --------------------------------------------------------------------------- #

_REC = {
    "name": "Reliance Industries",
    "confidence": 78,
    "risk": "Medium",
    "entry_zone": {"low": 990.0, "high": 1010.0},
    "stop_loss": 940.0,
    "targets": [1100.0, 1180.0],
    "expected_return_pct": 9.5,
    "sector_strength": "Oil & Gas is leading today.",
}

#: Every structural element the summary promises, independent of the reasoning
#: clause. These are the levels a user is asked to trade on.
_STRUCTURE = (
    "Reliance Industries",
    "78/100",
    "₹990.0",
    "₹1010.0",
    "₹940.0",
    "₹1100.0",
    "₹1180.0",
    "+9.5%",
)


def test_an_empty_reasons_list_still_produces_the_whole_narrative():
    """F-5: the conditional expression spanned every `+` before it, so Python
    bound it to the ENTIRE concatenation. An empty reasons list did not swap the
    Reasoning clause — it deleted name, confidence, entry zone, stop, targets and
    expected move, leaving the summary as one bare sentence."""
    summary = server._advisor_deterministic_narrative(dict(_REC, technical_reasons=[]), "Swing Trade")["ai_summary"]

    for fragment in _STRUCTURE:
        assert fragment in summary, f"{fragment!r} missing from {summary!r}"
    assert summary.endswith("No technical evidence supports this level.")


def test_the_structural_assertion_could_have_failed():
    """The oracle discriminates: a summary that is only the fallback sentence
    carries none of those fragments, which is exactly the string the defect
    produced."""
    defective = "No technical evidence supports this level."

    assert not any(fragment in defective for fragment in _STRUCTURE)


def test_a_non_empty_reasons_list_quotes_the_first_reason():
    """The falsifying twin: the normal path is unchanged."""
    summary = server._advisor_deterministic_narrative(
        dict(_REC, technical_reasons=["RSI at 55 sits in a healthy bullish zone."]),
        "Swing Trade",
    )["ai_summary"]

    for fragment in _STRUCTURE:
        assert fragment in summary
    assert summary.endswith("Reasoning: RSI at 55 sits in a healthy bullish zone.")
    assert "No technical evidence" not in summary


def test_only_the_reasoning_clause_differs_between_the_two_paths():
    """The scope of the conditional, asserted directly: everything up to the
    reasoning clause is byte-identical."""
    with_reasons = server._advisor_deterministic_narrative(
        dict(_REC, technical_reasons=["Some reading."]), "Swing Trade"
    )["ai_summary"]
    without = server._advisor_deterministic_narrative(dict(_REC, technical_reasons=[]), "Swing Trade")["ai_summary"]

    assert with_reasons[: -len("Reasoning: Some reading.")] == (
        without[: -len("No technical evidence supports this level.")]
    )


def test_the_single_target_variant_still_omits_the_second_target():
    """The other conditional in the same expression is untouched."""
    summary = server._advisor_deterministic_narrative(
        dict(_REC, targets=[1100.0], technical_reasons=[]), "Swing Trade"
    )["ai_summary"]

    assert "₹1100.0" in summary
    assert " and ₹" not in summary


def test_the_news_impact_half_is_unchanged():
    """`news_impact` shares the function and must not have been caught by the
    restructure."""
    news = server._advisor_deterministic_narrative(dict(_REC, technical_reasons=[]), "Swing Trade")["news_impact"]

    assert "No stock-specific news feed is modeled" in news
    assert "Oil & Gas is leading today." in news


# --------------------------------------------------------------------------- #
# 5. The scoring invariant — no number moved                                    #
# --------------------------------------------------------------------------- #


def test_the_scoring_placeholders_are_untouched_by_this_pass():
    """D6-Q1: "Scores stay unchanged; only what the platform claims changes."
    None of the four fixes is allowed to reach the arithmetic."""
    from services.market_engine.ranking_engine import SCORING_PLACEHOLDERS

    assert SCORING_PLACEHOLDERS == {
        "rsi": 50.0,
        "macd": 0.0,
        "macd_signal": 0.0,
        "avg_volume": 0,
        "volume_ratio": 1.0,
        "change_pct": 0.0,
    }


def test_scoring_input_still_substitutes_exactly_the_expression_it_replaced():
    """`scoring_input` is `value or placeholder`, deliberately — including its
    treatment of a falsy real value (LIM-D6.8A-1), which this pass does not
    touch."""
    for value in (None, 0, 0.0, "", 55.0, -3.0):
        payload = {"rsi": value}
        assert field_quality.scoring_input(payload, "rsi", 50.0) == (value or 50.0)


def test_the_advisor_confidence_is_unchanged_by_this_pass():
    """The literals recorded against the PRE-D6.8-A scorer in
    `test_d68_data_quality.py`, re-asserted here so a change made for F-5 (which
    lives in the same module) cannot move them unnoticed."""
    measured, _ = server._advisor_score(
        {
            "symbol": "R",
            "price": 100.0,
            "rsi": 55.0,
            "macd": 3.0,
            "macd_signal": 1.0,
            "volume_ratio": 1.9,
            "change_pct": 2.0,
        },
        [],
        "swing",
    )
    unmeasured, _ = server._advisor_score({"symbol": "N", "price": 100.0}, [], "swing")

    assert measured == 95
    assert unmeasured == 61


def test_the_scanner_verdict_is_unchanged_for_a_fully_measured_universe():
    """F-1 changed how a bound READS a field. For a universe in which every
    field is a real reading, every preset must still return exactly what the
    PRE-D6.8-A `_passes_filters` returned.

    Asserted against `_legacy_passes_filters` — the transcription of the old
    arithmetic that `test_d68_data_quality.py` already maintains — rather than
    against literals recomputed here, so this is a comparison with the old code
    and not with the new code's opinion of itself.
    """
    from tests.test_d68_data_quality import _legacy_passes_filters

    universe = [
        {
            "symbol": "A",
            "sector": "Oil & Gas",
            "price": 1000.0,
            "volume": 5_000_000,
            "rsi": 55.0,
            "macd": 3.0,
            "macd_signal": 1.0,
            "volume_ratio": 1.9,
            "change_pct": 2.0,
        },
        {
            "symbol": "B",
            "sector": "Oil & Gas",
            "price": 500.0,
            "volume": 1_000_000,
            "rsi": 30.0,
            "macd": 1.0,
            "macd_signal": 3.0,
            "volume_ratio": 0.4,
            "change_pct": -2.0,
        },
    ]
    # Price and volume bounds specifically, which are the fields F-1 moved.
    extra = [{"price_min": 600}, {"price_max": 600}, {"volume_min": 2_000_000}]

    outcomes = []
    for filters in [s["filters"] for s in scanner_engine.STRATEGY_PRESETS.values()] + extra:
        new = [q["symbol"] for q in universe if scanner_engine._passes_filters(q, filters)]
        old = [q["symbol"] for q in universe if _legacy_passes_filters(q, filters)]
        assert new == old, f"{filters} changed: {old} -> {new}"
        outcomes.append(tuple(old))

    # The oracle discriminates — otherwise the loop above compares empty lists.
    assert len(set(outcomes)) > 1, outcomes

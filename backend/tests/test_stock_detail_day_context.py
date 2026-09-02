"""A live broker price must not cost the detail page its day (D5.19 live fix).

THE REGRESSION, FOUND IN THE BROWSER ON 2026-09-02
---------------------------------------------------
D5.19 made `GET /api/stocks/{symbol}` user-scoped so a connected broker's
promoted feed could serve it. It works — measured live at 04:41:02Z with an
authenticated Upstox session, the same endpoint answered two callers
differently at the same instant:

    AUTHED (broker)   tier=streaming  price=1304.0   change=None  prev_close=None
    ANON   (baseline) tier=delayed    price=1303.5   change=-5.5  prev_close=1309.0

That is the scoping fix working exactly as designed. It is also a regression,
because a canonical `MarketTick` carries a price and nothing else (LIM-D5.16-1)
— no previous close, no OHLC, no volume — and the detail page renders all of
them. Before D5.19 this route was anonymous, so it always got the baseline's
complete quote and the question never arose.

What the browser actually showed for RELIANCE:

    ₹1,303.70   ↗ +₹0.00 (+%)        [LIVE]
    OPEN —

`Math.abs(null)` is `0`, so a **fabricated "unchanged"** was rendered in green
beside a live, moving price, with an empty percentage next to it. That is the
precise fabrication `applyLivePrices` and the tick contract exist to refuse,
reintroduced by a fix for a different problem.

THE RULE APPLIED
----------------
A price source may supply a price. It may not supply — or erase — a day.

The route now resolves the user's feed for the price and fills only the fields
a tick structurally cannot carry from the platform baseline. Both resolutions
go through the Market Gateway; neither bypasses it. The broker's price always
wins, because that is the freshest true statement available; the baseline's
previous close, OHLC and volume are used only where the winning quote is
silent, because a null there is not a value, it is an absence.

This is the shape the architecture already documents and accepts for the
overview — a live index level beside a baseline day change (LIM-D5.17-4) — now
applied to the surface that renders both.
"""
import asyncio

import pytest

import server


def _run(coro):
    return asyncio.run(coro)


BROKER_QUOTE = {
    "symbol": "RELIANCE", "name": "Reliance", "price": 1304.0,
    "change": None, "change_pct": None, "prev_close": None,
    "open": None, "high": None, "low": None, "volume": None,
    "source_tier": "streaming",
}

BASELINE_QUOTE = {
    "symbol": "RELIANCE", "name": "Reliance", "price": 1303.5,
    "change": -5.5, "change_pct": -0.42, "prev_close": 1309.0,
    "open": 1298.0, "high": 1304.9, "low": 1293.1, "volume": 2368643,
    "source_tier": "delayed",
}

DAY_CONTEXT = ("change", "change_pct", "prev_close", "open", "high", "low", "volume")


@pytest.fixture
def quotes(monkeypatch):
    """Serve a different quote per identity, recording who was asked."""
    seen = []

    async def _real_quote(symbol, user_id=None):
        seen.append(user_id)
        return dict(BROKER_QUOTE) if user_id else dict(BASELINE_QUOTE)

    monkeypatch.setattr(server, "real_quote", _real_quote)
    return seen


USER = "6a5e6228aa11bb22cc33dd44"


def test_the_broker_price_is_the_one_served(quotes):
    """The point of the scoping fix. The freshest true price wins."""
    quote = _run(server.stock_detail("RELIANCE", user_id=USER))

    assert quote["price"] == 1304.0
    assert quote["source_tier"] == "streaming"


def test_the_day_context_a_tick_cannot_carry_comes_from_the_baseline(quotes):
    """The observed facts are taken verbatim.

    `change` and `change_pct` are deliberately NOT in this list: they are
    *derived* from the live price against this previous close, because the
    baseline's own change was measured against the baseline's own price. See
    `test_the_change_is_recomputed_against_the_live_price`, which supersedes
    the earlier version of this assertion.
    """
    quote = _run(server.stock_detail("RELIANCE", user_id=USER))

    assert quote["prev_close"] == 1309.0
    assert quote["open"] == 1298.0
    assert quote["high"] == 1304.9
    assert quote["low"] == 1293.1
    assert quote["volume"] == 2368643


def test_no_day_field_is_left_null_when_the_baseline_knows_it(quotes):
    """The browser regression, stated as one assertion.

    A null here renders as `+₹0.00 (+%)` — a fabricated "unchanged" in green
    beside a live price.
    """
    quote = _run(server.stock_detail("RELIANCE", user_id=USER))

    absent = [f for f in DAY_CONTEXT if quote.get(f) is None]
    assert absent == [], f"day context lost to the thin quote: {absent}"


def test_the_baseline_never_overwrites_a_price_the_feed_did_supply(quotes):
    """Fill, never replace. A stale 1303.5 must not displace a live 1304.0."""
    quote = _run(server.stock_detail("RELIANCE", user_id=USER))

    assert quote["price"] != BASELINE_QUOTE["price"]


def test_the_tier_still_describes_the_feed_that_supplied_the_price(quotes):
    """Freshness is a claim about the price, and the price is the broker's.

    Reporting `delayed` because some fields were filled would understate a live
    price; reporting `streaming` is the honest description of the number the
    user is looking at.
    """
    quote = _run(server.stock_detail("RELIANCE", user_id=USER))

    assert quote["source_tier"] == "streaming"


def test_an_anonymous_caller_is_unchanged(quotes):
    """No second resolution, and the same payload as before D5.19."""
    quote = _run(server.stock_detail("RELIANCE", user_id=None))

    assert quote["price"] == 1303.5
    assert quote["change_pct"] == -0.42
    assert quotes == [None], "the baseline path must not resolve twice"


def test_a_complete_feed_quote_costs_no_second_resolution(monkeypatch):
    """The baseline is consulted only when something is actually missing.

    A broker whose quote carries a full day — or a future richer tick — must
    not pay for a resolution it does not need.
    """
    seen = []

    async def _real_quote(symbol, user_id=None):
        seen.append(user_id)
        return {**BASELINE_QUOTE, "source_tier": "streaming"}

    monkeypatch.setattr(server, "real_quote", _real_quote)

    quote = _run(server.stock_detail("RELIANCE", user_id=USER))

    assert seen == [USER]
    assert quote["source_tier"] == "streaming"


def test_an_unavailable_baseline_does_not_break_the_live_price(monkeypatch):
    """Degradation, not failure.

    If the baseline cannot answer, the user still gets the live price with the
    day fields honestly absent — which the frontend renders as nothing rather
    than as zero.
    """
    async def _real_quote(symbol, user_id=None):
        return dict(BROKER_QUOTE) if user_id else None

    monkeypatch.setattr(server, "real_quote", _real_quote)

    quote = _run(server.stock_detail("RELIANCE", user_id=USER))

    assert quote["price"] == 1304.0
    assert quote["change_pct"] is None


def test_an_unknown_symbol_still_404s(monkeypatch):
    from fastapi import HTTPException

    async def _real_quote(symbol, user_id=None):
        return None

    monkeypatch.setattr(server, "real_quote", _real_quote)
    monkeypatch.setattr(server, "get_stock_meta", lambda s: None)

    with pytest.raises(HTTPException) as exc:
        _run(server.stock_detail("NOPE", user_id=None))
    assert exc.value.status_code == 404


def test_the_change_is_recomputed_against_the_live_price(quotes):
    """The three numbers on screen must agree with each other.

    Found by the D5.19 live consistency check. Filling `change` straight from
    the baseline leaves the page stating a change measured against a price it
    is not showing:

        price 1306.3 (broker)   prev_close 1309.0 (baseline)
        change -2.2 (baseline, measured against ITS price of 1303.6)
        but 1306.3 - 1309.0 = -2.7

    Small — ~0.04% — and wrong in the way that matters: a reader who subtracts
    the two numbers on the screen gets a third number the screen does not show.
    Both inputs are real facts (a broker price, a baseline previous close), so
    their difference is the honest day change and nothing is invented by taking
    it.
    """
    quote = _run(server.stock_detail("RELIANCE", user_id=USER))

    assert quote["price"] == 1304.0
    assert quote["prev_close"] == 1309.0
    assert quote["change"] == pytest.approx(-5.0)
    assert quote["change_pct"] == pytest.approx(-0.38, abs=0.01)


def test_the_recomputed_change_agrees_with_the_rendered_numbers(quotes):
    """Stated as the arithmetic a reader would actually do."""
    q = _run(server.stock_detail("RELIANCE", user_id=USER))

    assert q["change"] == pytest.approx(q["price"] - q["prev_close"], abs=0.01)
    assert q["change_pct"] == pytest.approx(
        (q["price"] - q["prev_close"]) / q["prev_close"] * 100, abs=0.01
    )


def test_nothing_is_recomputed_without_a_previous_close(monkeypatch):
    """No previous close, no derived change — and no fabricated zero."""
    async def _real_quote(symbol, user_id=None):
        return dict(BROKER_QUOTE) if user_id else None

    monkeypatch.setattr(server, "real_quote", _real_quote)

    q = _run(server.stock_detail("RELIANCE", user_id=USER))

    assert q["prev_close"] is None
    assert q["change"] is None
    assert q["change_pct"] is None


def test_the_baseline_path_keeps_its_own_arithmetic(quotes):
    """An anonymous caller's quote is the provider's, untouched."""
    q = _run(server.stock_detail("RELIANCE", user_id=None))

    assert q["change"] == -5.5
    assert q["change_pct"] == -0.42

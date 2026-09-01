"""D5.18 — the market-status clock is the platform's, not a provider's.

`/api/market/overview` reported `market_status` by reading Yahoo's own
`marketState` field off the NIFTY quote. Observed live on 2026-09-01 at
11:45 IST — inside NSE hours, with Yahoo itself returning a moving price of
24,126.85 — that field read `CLOSED`, so the dashboard rendered "MARKET
CLOSED" over live, ticking prices while the platform's own authoritative
clock (`validator.is_market_hours`) said the market was open.

Two things are wrong with sourcing it from the provider, and the tests below
pin both:

* **It is a provider field presented as platform truth.** Whether NSE is open
  is a fact about the exchange and the clock, not about which vendor answered
  a quote. A second provider with a different vendor vocabulary would give a
  different answer to the same question.
* **It contradicts the platform's own answer.** `/api/market/engine/status`
  already publishes `market_hours` from `is_market_hours()`. Two surfaces
  disagreeing about whether the market is open is the defect a user sees.
"""
import asyncio

import pytest

from services.real_market import fetch_real_market_overview


def _run(coro):
    return asyncio.run(coro)


class _Quote(dict):
    pass


def _quote(price, market_state):
    return {"price": price, "change": 1.0, "change_pct": 0.1,
            "market_state": market_state}


@pytest.fixture
def _stub_overview_inputs(monkeypatch):
    """Stub only the *fetches*; the status expression under test still runs."""
    async def fake_quote(symbol):
        # Yahoo says CLOSED while quoting a live, moving price — the exact
        # shape observed live.
        return _quote({"NIFTY": 24126.85, "BANKNIFTY": 57637.8,
                       "SENSEX": 77165.37}[symbol], "CLOSED")

    async def fake_vix():
        return 11.0

    async def fake_universe():
        return []

    monkeypatch.setattr("services.real_market.fetch_yahoo_quote", fake_quote)
    monkeypatch.setattr("services.real_market.fetch_india_vix", fake_vix)
    monkeypatch.setattr("services.real_market.fetch_all_universe_quotes", fake_universe)
    # The overview is cached for 30s; keep every case independent.
    async def _no_cache(_key):
        return None
    async def _no_set(*_a, **_k):
        return None
    monkeypatch.setattr("services.real_market.cache_get", _no_cache)
    monkeypatch.setattr("services.real_market.cache_set", _no_set)


def test_market_open_when_platform_clock_says_open(
        monkeypatch, _stub_overview_inputs):
    """The platform clock decides — even when the provider says CLOSED.

    This is the live-observed regression: provider `market_state == "CLOSED"`,
    platform clock open. Before D5.18 this returned "CLOSED".
    """
    monkeypatch.setattr("services.market_engine.validator.is_market_hours",
                        lambda: True)
    overview = _run(fetch_real_market_overview())
    assert overview["market_status"] == "OPEN"


def test_market_closed_when_platform_clock_says_closed(
        monkeypatch, _stub_overview_inputs):
    """The converse, so the fix cannot be "always OPEN"."""
    monkeypatch.setattr("services.market_engine.validator.is_market_hours",
                        lambda: False)
    overview = _run(fetch_real_market_overview())
    assert overview["market_status"] == "CLOSED"


def test_status_ignores_provider_market_state_entirely(
        monkeypatch, _stub_overview_inputs):
    """A provider claiming REGULAR cannot open a market the clock has shut.

    The inverse of the observed bug, and the one that stops the fix from being
    "trust the provider unless it says CLOSED".
    """
    async def regular_quote(symbol):
        return _quote(100.0, "REGULAR")
    monkeypatch.setattr("services.real_market.fetch_yahoo_quote", regular_quote)
    monkeypatch.setattr("services.market_engine.validator.is_market_hours",
                        lambda: False)
    overview = _run(fetch_real_market_overview())
    assert overview["market_status"] == "CLOSED"


def test_overview_agrees_with_engine_status_clock(
        monkeypatch, _stub_overview_inputs):
    """The two surfaces that answer "is the market open" must not disagree.

    `/api/market/engine/status` publishes `is_market_hours()` directly. This
    asserts the overview is derived from the same source rather than from a
    parallel one that can drift.
    """
    from services.market_engine import validator
    for clock in (True, False):
        monkeypatch.setattr(validator, "is_market_hours", lambda c=clock: c)
        overview = _run(fetch_real_market_overview())
        expected = "OPEN" if clock else "CLOSED"
        assert overview["market_status"] == expected

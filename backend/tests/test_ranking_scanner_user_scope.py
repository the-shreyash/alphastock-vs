"""Ranking and the scanner must resolve for the requesting user (D5.19, D-4/D-5).

THE DEFECT (D5.18's D-4 and D-5, carried as LIM-D5.18-3)
--------------------------------------------------------
`rank_universe()` and `scan()` both called `market_gateway.get_universe_quotes()`
with no `user_id`. The gateway supports one — every accessor on it takes the
keyword, and `source_manager` resolves a per-user context when given one — but
neither of these two passed it.

So both resolved in `GLOBAL_CONTEXT`, where the only registered provider is the
platform's Yahoo baseline. A user with an authenticated, healthy, promoted
broker feed covering all 31 universe symbols could not be served by it here, at
any ranking, ever: their feed was not a candidate because their identity never
reached the resolver. D5.18 observed this live — 266 `market.tick` events in 40
seconds on a promoted `brokerfeed:upstox` provider, while `/market/ranking`
returned `delayed` prices throughout.

`/market/ranking` also carried no `source_tier` at all, so the surface rendering
those prices could not have labelled them even if it had wanted to — which is
why "Top Opportunities" showed a broker user delayed prices with no indication
that they were delayed.

WHAT THESE TESTS PIN
--------------------
That the identity reaches the gateway (not merely that a parameter exists), and
that the freshness label is the resolver's answer rather than a literal — the
two halves that make a connected broker actually able to serve this surface.

Authentication stays OPTIONAL on both endpoints: they are market-wide reads
that a signed-out visitor may make, and requiring a token would have been a
behaviour change dressed as a scoping fix. Present token, user context; absent
token, the platform baseline — which is exactly what these endpoints did for
everybody before.
"""
import asyncio

import pytest

from services.market_engine import ranking_engine, scanner_engine


def _run(coro):
    return asyncio.run(coro)


QUOTE = {
    "symbol": "RELIANCE", "name": "Reliance", "price": 1300.0, "change_pct": 1.5,
    "sector": "Oil & Gas", "rsi": 55.0, "macd": 3.0, "macd_signal": 1.0,
    "avg_volume": 6_000_000, "volume_ratio": 1.4, "source_tier": "streaming",
}


class _Gateway:
    """Records the `user_id` each accessor was called with."""

    def __init__(self, quotes=None, tier="streaming"):
        self.seen = {}
        self._quotes = quotes if quotes is not None else [dict(QUOTE)]
        self._tier = tier

    async def get_universe_quotes(self, *, user_id=None):
        self.seen["universe_quotes"] = user_id
        return [dict(q) for q in self._quotes]

    async def get_sectors(self, *, user_id=None):
        self.seen["sectors"] = user_id
        return [{"name": "Oil & Gas", "change_pct": 1.2}]

    def source_tier(self, _capability=None, *, user_id=None):
        self.seen["source_tier"] = user_id
        return self._tier


@pytest.fixture
def gateway(monkeypatch):
    gw = _Gateway()
    import services.market_engine.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "market_gateway", gw)

    async def _publish(*_a, **_k):
        return None

    monkeypatch.setattr(ranking_engine.event_bus, "publish", _publish)
    monkeypatch.setattr(scanner_engine.event_bus, "publish", _publish)
    return gw


USER = "6a5e6228aa11bb22cc33dd44"


# --------------------------------------------------------------------------- #
# The identity reaches the resolver                                            #
# --------------------------------------------------------------------------- #

def test_ranking_resolves_for_the_requesting_user(gateway):
    _run(ranking_engine.rank_universe(top_n=5, user_id=USER))

    assert gateway.seen["universe_quotes"] == USER


def test_ranking_resolves_sectors_for_the_requesting_user(gateway):
    """Both halves, not just the one the price comes from.

    A ranking whose prices are a broker's and whose sector context is the
    platform's would score a live price against a stale sector rotation.
    """
    _run(ranking_engine.rank_universe(top_n=5, user_id=USER))

    assert gateway.seen["sectors"] == USER


def test_scanner_resolves_for_the_requesting_user(gateway):
    _run(scanner_engine.scan(limit=5, user_id=USER, publish=False))

    assert gateway.seen["universe_quotes"] == USER


def test_no_user_still_resolves_the_platform_baseline(gateway):
    """A signed-out visitor is served, exactly as before this change."""
    _run(ranking_engine.rank_universe(top_n=5))
    assert gateway.seen["universe_quotes"] is None

    _run(scanner_engine.scan(limit=5, publish=False))
    assert gateway.seen["universe_quotes"] is None


# --------------------------------------------------------------------------- #
# The freshness label                                                          #
# --------------------------------------------------------------------------- #

def test_ranking_reports_the_tier_that_actually_served_it(gateway):
    result = _run(ranking_engine.rank_universe_report(top_n=5, user_id=USER))

    assert result["source_tier"] == "streaming"
    assert gateway.seen["source_tier"] == USER


def test_ranking_tier_is_the_resolvers_answer_not_a_literal(gateway, monkeypatch):
    """Falsification: a hardcoded "delayed" would pass the previous test's shape.

    The gateway is told to report `delayed`; the payload must follow it.
    """
    gateway._tier = "delayed"

    result = _run(ranking_engine.rank_universe_report(top_n=5, user_id=USER))

    assert result["source_tier"] == "delayed"


def test_ranking_rows_still_carry_the_scored_evidence(gateway):
    """Scoping must not have cost the explanation."""
    result = _run(ranking_engine.rank_universe_report(top_n=5, user_id=USER))

    assert result["rankings"]
    assert result["rankings"][0]["evidence"]


def test_scanner_reports_the_tier_that_actually_served_it(gateway):
    result = _run(scanner_engine.scan(limit=5, user_id=USER, publish=False))

    assert result["source_tier"] == "streaming"
    # The identity, not just the value. Falsification M16 dropped `user_id`
    # from this call and the assertion above still passed, because the fake
    # returns one tier for every caller — exactly as a single-provider
    # deployment would. A tier read in the global context describes a different
    # resolution than the one that produced these rows, and the value alone
    # cannot tell the two apart.
    assert gateway.seen["source_tier"] == USER

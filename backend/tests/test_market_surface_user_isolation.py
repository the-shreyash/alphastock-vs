"""The surfaces D5.19 scoped must not cross users (Phase 6).

WHY THIS FILE IS NEW WORK AND NOT A DUPLICATE
---------------------------------------------
D5.17 proved per-user isolation for the watchlist stream
(`test_watchlist_stream_isolation.py`). That covered a *push* surface — one
socket, many users, one subscription set each.

D5.19 opened three **pull** surfaces to user identity for the first time:
`/market/ranking`, `/market/scanner` and `/api/stocks/{symbol}`. Before this
sprint they took no user at all, which was a defect (a connected broker could
never serve them) and also, incidentally, made them impossible to get wrong in
this particular way. Adding the identity adds the failure mode, so it adds the
tests.

The failure that matters here is not a leak of holdings or credentials — these
endpoints return market data, which is the same fact for everybody. It is
**resolution crossing**: user A's request resolving against user B's broker
feed. That is a real harm even for public data, because a feed is a paid,
rate-limited, per-account entitlement — serving A from B's socket spends B's
quota, and on a broker that bills per subscription it spends B's money.

The other half is the anonymous case. `get_optional_user_id` returns None for a
caller with no credential, and None must mean *the platform baseline*, never
"whoever asked last". A cached or module-level identity would be invisible in
every single-user test and catastrophic under concurrency.
"""
import asyncio

import pytest

from services.market_engine import ranking_engine, scanner_engine


def _run(coro):
    return asyncio.run(coro)


USER_A = "aaaaaaaaaaaaaaaaaaaaaaaa"
USER_B = "bbbbbbbbbbbbbbbbbbbbbbbb"

QUOTE = {
    "symbol": "RELIANCE", "name": "Reliance", "price": 1300.0, "change_pct": 1.5,
    "sector": "Oil & Gas", "rsi": 55.0, "macd": 3.0, "macd_signal": 1.0,
    "avg_volume": 6_000_000, "volume_ratio": 1.4,
}


class _PerUserGateway:
    """A gateway that answers differently per user, and records who asked.

    The price differs by user on purpose. A test whose fake returns one payload
    for every caller cannot tell a correctly-scoped call from an unscoped one —
    it is the shape that let falsification M16 survive the first round.
    """

    def __init__(self):
        self.calls = []

    def _price_for(self, user_id):
        return {None: 1300.0, USER_A: 1301.0, USER_B: 1302.0}[user_id]

    async def get_universe_quotes(self, *, user_id=None):
        self.calls.append(("universe_quotes", user_id))
        return [{**QUOTE, "price": self._price_for(user_id)}]

    async def get_sectors(self, *, user_id=None):
        self.calls.append(("sectors", user_id))
        return [{"name": "Oil & Gas", "change_pct": 1.2}]

    def source_tier(self, _c=None, *, user_id=None):
        self.calls.append(("source_tier", user_id))
        return {None: "delayed", USER_A: "streaming", USER_B: "delayed"}[user_id]


@pytest.fixture
def gateway(monkeypatch):
    gw = _PerUserGateway()
    import services.market_engine.gateway as gm

    monkeypatch.setattr(gm, "market_gateway", gw)

    async def _publish(*_a, **_k):
        return None

    monkeypatch.setattr(ranking_engine.event_bus, "publish", _publish)
    monkeypatch.setattr(scanner_engine.event_bus, "publish", _publish)
    return gw


def _identities(gateway, accessor):
    return {u for name, u in gateway.calls if name == accessor}


# --------------------------------------------------------------------------- #
# Ranking                                                                      #
# --------------------------------------------------------------------------- #

def test_two_users_ranking_requests_do_not_cross(gateway):
    a = _run(ranking_engine.rank_universe_report(top_n=1, user_id=USER_A))
    b = _run(ranking_engine.rank_universe_report(top_n=1, user_id=USER_B))

    assert a["rankings"][0]["price"] == 1301.0
    assert b["rankings"][0]["price"] == 1302.0
    assert a["source_tier"] == "streaming"
    assert b["source_tier"] == "delayed"


def test_a_users_identity_is_the_only_one_that_reaches_the_resolver(gateway):
    _run(ranking_engine.rank_universe_report(top_n=1, user_id=USER_A))

    assert _identities(gateway, "universe_quotes") == {USER_A}
    assert _identities(gateway, "sectors") == {USER_A}
    assert _identities(gateway, "source_tier") == {USER_A}


def test_an_anonymous_ranking_does_not_inherit_the_previous_caller(gateway):
    """None means the platform baseline, never "whoever asked last".

    A module-level or cached identity would pass every single-user test in this
    repository and serve one user's paid feed to an anonymous visitor under any
    concurrency at all.
    """
    _run(ranking_engine.rank_universe_report(top_n=1, user_id=USER_A))
    gateway.calls.clear()

    anon = _run(ranking_engine.rank_universe_report(top_n=1))

    assert anon["rankings"][0]["price"] == 1300.0
    assert anon["source_tier"] == "delayed"
    assert _identities(gateway, "universe_quotes") == {None}


def test_interleaved_ranking_requests_stay_separate(gateway):
    """Concurrency, not sequence — the shape a request-scoped bug hides from."""

    async def _both():
        return await asyncio.gather(
            ranking_engine.rank_universe_report(top_n=1, user_id=USER_A),
            ranking_engine.rank_universe_report(top_n=1, user_id=USER_B),
        )

    a, b = _run(_both())

    assert a["rankings"][0]["price"] == 1301.0
    assert b["rankings"][0]["price"] == 1302.0


# --------------------------------------------------------------------------- #
# Scanner                                                                      #
# --------------------------------------------------------------------------- #

def test_two_users_scans_do_not_cross(gateway):
    a = _run(scanner_engine.scan(limit=1, user_id=USER_A, publish=False))
    b = _run(scanner_engine.scan(limit=1, user_id=USER_B, publish=False))

    assert a["results"][0]["price"] == 1301.0
    assert b["results"][0]["price"] == 1302.0
    assert a["source_tier"] == "streaming"
    assert b["source_tier"] == "delayed"


def test_an_anonymous_scan_does_not_inherit_the_previous_caller(gateway):
    _run(scanner_engine.scan(limit=1, user_id=USER_B, publish=False))
    gateway.calls.clear()

    anon = _run(scanner_engine.scan(limit=1, publish=False))

    assert anon["results"][0]["price"] == 1300.0
    assert _identities(gateway, "universe_quotes") == {None}


# --------------------------------------------------------------------------- #
# Nothing user-identifying reaches the payload                                 #
# --------------------------------------------------------------------------- #

def test_the_ranking_payload_names_no_user(gateway):
    """These responses are market data. A user id in one is a leak with no purpose."""
    import json

    payload = json.dumps(_run(ranking_engine.rank_universe_report(top_n=1, user_id=USER_A)))

    assert USER_A not in payload
    assert USER_B not in payload


def test_the_scanner_payload_names_no_user(gateway):
    import json

    payload = json.dumps(_run(scanner_engine.scan(limit=1, user_id=USER_A, publish=False)))

    assert USER_A not in payload


def test_neither_payload_names_a_provider(gateway):
    """Developer Rule 4 — freshness travels, provenance does not."""
    import json
    import re

    for payload in (
        json.dumps(_run(ranking_engine.rank_universe_report(top_n=1, user_id=USER_A))),
        json.dumps(_run(scanner_engine.scan(limit=1, user_id=USER_A, publish=False))),
    ):
        assert not re.search(
            r"yahoo|upstox|zerodha|kite|fyers|dhan|angel", payload, re.I
        )

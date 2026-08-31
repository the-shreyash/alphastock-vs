"""Sprint D5.16 §4/§9/§13 — an empty demat is still a market feed, at all five brokers.

THE STATE THIS FILE MAKES IMPOSSIBLE
------------------------------------
D5.15's live run reached it with a real, authenticated Upstox account::

    connected broker, socket open, 0 holdings, 0 positions
      -> 0 instruments subscribed
      -> a feed structurally incapable of ever delivering a tick
      -> registered as a streaming market-data provider all the same.

D5.15 fixed it for Upstox by widening the universe to the account's watchlist
and the dashboard set, and by adding `INSTRUMENT_CATALOGUE` — which **only
Upstox implemented**. For the other four brokers the gateway's capability gate
returned `{}` and the account fell straight back to a portfolio-only
subscription, so the identical dead-feed state was still reachable on Zerodha,
Angel One, Fyers and Dhan. That is what D5.16 §3 closes and what this file pins,
once per broker, against the real adapters.

WHY THIS IS SEPARATE FROM `test_instrument_catalogue.py`
--------------------------------------------------------
That file asks whether an adapter can turn master rows into identifiers. This
one asks the product question: *given an account that owns nothing, does a
subscription actually get planned, and can an arriving tick be named?* Those are
different failures. A catalogue that resolves perfectly is still worthless if
`_plan_tick_subscription` never consults it, or if the instrument map is rebuilt
without it and every resulting tick is dropped one step later at the canonical
boundary — which is precisely how this class of bug hides.

No test here opens a socket or reaches a broker API.
"""

import pytest

from services.brokers.catalogue import EQUITY_SEGMENT

from services.brokers.feed_universe import FeedInstrument
from tests.test_broker_streaming import run

BROKERS = ("zerodha", "upstox", "angelone", "fyers", "dhan")

#: What each broker's catalogue would answer for a watchlisted, unheld NSE
#: equity — one identifier per broker, in that broker's own format, taken from
#: its live master. The formats differ on purpose: the property under test is
#: that the engine plans a subscription *whatever* the identifier looks like.
CATALOGUE_ANSWERS = {
    "zerodha": {"SBIN": 779521},
    "upstox": {"SBIN": "NSE_EQ|INE062A01020"},
    "angelone": {"SBIN": "1|3045"},
    "fyers": {"SBIN": "sf|nse_cm|3045"},
    "dhan": {"SBIN": "NSE_EQ|3045"},
}


@pytest.fixture
def engine_with_empty_account(monkeypatch):
    """A `BrokerEngine` whose account owns nothing and watches SBIN."""
    from services import broker_engine as module

    engine = module.BrokerEngine()
    engine.db = None  # the watchlist read is stubbed per test

    async def _no_session(user_id, broker):
        return {"access_token": "x"}

    monkeypatch.setattr(engine, "get_session", _no_session)

    async def _watchlist(user_id):
        return ["SBIN"]

    monkeypatch.setattr(engine, "_feed_watchlist_symbols", _watchlist)
    return engine


def _catalogue_for(broker, monkeypatch):
    """Stub the adapter's index so no test downloads a master."""
    from services.brokers.registry import broker_registry

    adapter = broker_registry.get(broker)
    # D5.17 — keys carry the segment. These fixtures are all cash equities, so
    # the segment is EQUITY; an index fixture would key the same way with
    # INDEX_SEGMENT and is covered in `test_index_feed_routing.py`.
    index = {(EQUITY_SEGMENT, "NSE", symbol): identifier
             for symbol, identifier in CATALOGUE_ANSWERS[broker].items()}

    async def _fixture(self):
        return index

    monkeypatch.setattr(type(adapter), "_instrument_catalogue", _fixture)
    return adapter


# ==================================================================
# A. Every broker plans a subscription for an account that owns nothing
# ==================================================================


@pytest.mark.parametrize("broker", BROKERS)
def test_an_account_with_no_holdings_still_subscribes(broker, monkeypatch,
                                                      engine_with_empty_account):
    """The D5.16 headline, once per broker."""
    _catalogue_for(broker, monkeypatch)

    tokens, symbols = run(engine_with_empty_account._plan_tick_subscription(
        "u1", broker, {}, holdings=[], positions=[]))

    assert tokens, (
        f"{broker}: an account with an empty portfolio planned an empty "
        f"subscription — the socket can never deliver a tick"
    )
    assert CATALOGUE_ANSWERS[broker]["SBIN"] in tokens
    assert "SBIN" in symbols, (
        f"{broker}: the instrument was subscribed but the map cannot name it, "
        f"so every arriving tick is dropped at the canonical boundary"
    )


@pytest.mark.parametrize("broker", BROKERS)
def test_a_tick_for_an_unheld_watchlist_instrument_can_be_named(broker, monkeypatch,
                                                                engine_with_empty_account):
    """Subscription and resolution are two halves of the same fix.

    A subscription the instrument map cannot read back is the same defect as no
    subscription, reached one step later and more quietly: the wire carries the
    prices and `canonical_ticks` drops every one of them.
    """
    from services.brokers.instruments import canonical_ticks

    _catalogue_for(broker, monkeypatch)
    run(engine_with_empty_account._plan_tick_subscription(
        "u1", broker, {}, holdings=[], positions=[]))
    instrument_map = run(engine_with_empty_account._instrument_map("u1", broker))

    ticks = canonical_ticks(
        [{"instrument_token": CATALOGUE_ANSWERS[broker]["SBIN"], "last_price": 812.5}],
        instrument_map, broker=broker,
    )

    assert [t["symbol"] for t in ticks] == ["SBIN"], (
        f"{broker}: a tick for the instrument the feed was aimed at could not "
        f"be named"
    )
    assert ticks[0]["price"] == 812.5


@pytest.mark.parametrize("broker", BROKERS)
def test_the_subscription_carries_no_instrument_the_account_did_not_ask_for(
        broker, monkeypatch, engine_with_empty_account):
    """A catalogue that resolves is not a licence to subscribe to the exchange.
    The universe is the account's, and an instrument absent from it is absent
    from the wire."""
    _catalogue_for(broker, monkeypatch)

    tokens, symbols = run(engine_with_empty_account._plan_tick_subscription(
        "u1", broker, {}, holdings=[], positions=[]))

    from services.brokers.feed_universe import dashboard_symbols

    allowed = set(dashboard_symbols()) | {"SBIN"}
    assert set(symbols) <= allowed, (
        f"{broker}: {sorted(set(symbols) - allowed)} entered the subscription "
        f"without being in this account's universe"
    )


# ==================================================================
# B. The zero-subscription case is still refused (D5.15 invariant)
# ==================================================================


@pytest.mark.parametrize("broker", BROKERS)
def test_an_account_with_nothing_at_all_subscribes_to_nothing(broker, monkeypatch):
    """Zero holdings, zero positions, empty watchlist, and — critically — an
    empty dashboard set. The universe is genuinely empty and the fix must not
    invent an instrument to fill it.

    The dashboard set is stubbed empty rather than left alone, because with the
    real universe present this account would legitimately subscribe to ~50
    instruments and the assertion could not fail.
    """
    from services import broker_engine as module

    engine = module.BrokerEngine()
    engine.db = None

    async def _watchlist(user_id):
        return []

    monkeypatch.setattr(engine, "_feed_watchlist_symbols", _watchlist)
    monkeypatch.setattr(module, "dashboard_symbols", lambda: ())
    _catalogue_for(broker, monkeypatch)

    tokens, symbols = run(engine._plan_tick_subscription(
        "u1", broker, {}, holdings=[], positions=[]))

    assert tokens == [] and symbols == ()


def test_a_feed_with_no_subscription_cannot_serve_ticks():
    """And the provider built from that empty universe still refuses to claim
    market evidence — the D5.15 invariant, re-proved rather than assumed,
    because D5.16 changed what feeds a subscription."""
    from services.market_engine.providers import Capability, ResolutionContext
    from services.market_engine.providers.streaming import StreamingTickProvider

    feed = StreamingTickProvider("brokerfeed:nova:u1", owner_user_id="u1",
                                 probation_seconds=0.0)
    run(feed.connect())

    assert not feed.is_ready
    assert not feed.is_eligible_for(
        ResolutionContext(user_id="u1", symbol="SBIN", capability=Capability.TICKS))


# ==================================================================
# C. Degradation, per broker
# ==================================================================


@pytest.mark.parametrize("broker", BROKERS)
def test_an_unreachable_master_leaves_the_portfolio_subscription_intact(
        broker, monkeypatch, engine_with_empty_account):
    """A catalogue widens coverage; it is not load-bearing. An account that
    holds something keeps that subscription when the master cannot be read."""
    from services.brokers.errors import BrokerError
    from services.brokers.registry import broker_registry

    adapter = broker_registry.get(broker)

    async def _unreachable(self):
        raise BrokerError("master unavailable")

    monkeypatch.setattr(type(adapter), "_instrument_catalogue", _unreachable)

    _tokens, symbols = run(engine_with_empty_account._plan_tick_subscription(
        "u1", broker, {},
        holdings=[{"symbol": "RELIANCE", "exchange": "NSE",
                   "instrument_token": "738561"}],
        positions=[]))

    assert "RELIANCE" in symbols, (
        f"{broker}: an unreachable instrument master cost the account the "
        f"portfolio subscription it already had"
    )


@pytest.mark.parametrize("broker", BROKERS)
def test_the_engine_asks_for_exchange_qualified_instruments(broker, monkeypatch,
                                                            engine_with_empty_account):
    """The engine passes `FeedInstrument`s, not symbols — which is what lets an
    adapter answer for the right listing. Asserted at the call rather than by
    result, because a symbol-only call would still resolve correctly for an NSE
    watchlist and only go wrong for BSE."""
    from services.brokers.registry import broker_registry

    adapter = broker_registry.get(broker)
    seen = []

    async def _record(self, instruments, session=None):
        seen.extend(instruments)
        return {}

    monkeypatch.setattr(type(adapter), "resolve_instruments", _record)

    run(engine_with_empty_account._plan_tick_subscription(
        "u1", broker, {}, holdings=[], positions=[]))

    assert seen, f"{broker}: the engine resolved no instruments at all"
    assert all(isinstance(i, FeedInstrument) for i in seen), (
        f"{broker}: the engine passed bare symbols, so no adapter could tell "
        f"which listing was meant"
    )
    assert all(i.exchange for i in seen)

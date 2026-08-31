"""Sprint D5.17 — the index strip's price, from broker socket to price store.

WHAT WAS WRONG, PRECISELY
--------------------------
`_index_prices` read `market_gateway.get_indices`, which serves
`Capability.INDICES`. Only a *polling* provider declares that capability — a
broker feed publishes TICKS and QUOTES and has no notion of a market overview —
so the index price on this path was the delayed baseline for every account,
including one whose own feed was streaming NIFTY several times a second.

That was invisible while nothing subscribed indices. The moment D5.17 put them
in the feed universe it became visible and worse than before: the tick arrived
on `market.tick` and wrote 24815.25 into the price store, and 15 seconds later
this loop wrote the baseline's 24810 over it. An index card oscillating between
a live number and a stale one, under a feed indicator reading `Live`.

The fix asks the same canonical question every equity on the page is already
asked — `get_prices(..., user_id=...)`, per symbol, through the Source Manager —
and takes only `price` from the answer, because a thin streaming quote carries
no day-change and writing one that is not there is the fabrication the whole
path exists to prevent.

WHAT THESE TESTS DO NOT PROVE
------------------------------
That a broker socket actually delivers an index packet. That is a live-session
fact and is recorded as unverified. What is proved here is everything on this
side of it: given a feed that covers `NIFTY`, the index strip is served from
that feed, for that user alone, without inventing a field.
"""

from services.market_engine.providers import YahooPollingAdapter
from tests.test_broker_streaming import _clean_provider_registry, run
from tests.test_dashboard_price_path import _promoted_feed

#: What the delayed overview says, and what a feed says. Deliberately different
#: at every index so a test cannot pass by reading the wrong one.
BASELINE_OVERVIEW = {
    "nifty": {"value": 24810.0, "change": 120.5, "change_pct": 0.49, "available": True},
    "bank_nifty": {"value": 52400.0, "change": -80.2, "change_pct": -0.15, "available": True},
    "sensex": {"value": 81020.0, "change": 300.1, "change_pct": 0.37, "available": True},
    "india_vix": 13.4,
    "market_status": "OPEN",
}


class _IndexBaseline(YahooPollingAdapter):
    """The polled provider, answering the overview and a quote for any symbol.

    Subclassed rather than mocked so the resolution under test picks a *real*
    baseline with the real tier, priority and capability set — in particular a
    real `INDICES` capability, which is the thing a broker feed does not have
    and the whole reason this path needed changing.
    """

    async def fetch_indices(self):
        return dict(BASELINE_OVERVIEW)

    async def fetch_quote(self, symbol):
        key = {"NIFTY": "nifty", "BANKNIFTY": "bank_nifty", "SENSEX": "sensex"}.get(symbol)
        if key:
            block = BASELINE_OVERVIEW[key]
            return {"symbol": symbol, "price": block["value"],
                    "change_pct": block["change_pct"]}
        if symbol == "INDIAVIX":
            return {"symbol": symbol, "price": BASELINE_OVERVIEW["india_vix"]}
        return None


def _index_prices(user_id=None, feed=None):
    """One `_index_prices` call against a clean registry."""
    import services.heartbeat_engine as heartbeat

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = _IndexBaseline()
        registry.register(baseline)
        run(baseline.connect())
        if feed is not None:
            registry.register(feed)
        return run(heartbeat._index_prices(user_id))


# ==================================================================
# A. The baseline, unchanged
# ==================================================================


def test_the_three_indices_and_the_vix_are_published():
    prices = _index_prices()

    assert prices["NIFTY"]["price"] == 24810.0
    assert prices["BANKNIFTY"]["price"] == 52400.0
    assert prices["SENSEX"]["price"] == 81020.0
    assert prices["INDIAVIX"]["price"] == 13.4


def test_the_vix_carries_no_day_change_because_the_provider_publishes_none():
    """`change_pct: 0` would render a real price beside a fabricated
    "unchanged" — the exact fabrication the canonical tick contract refuses."""
    assert "change_pct" not in _index_prices()["INDIAVIX"]


def test_the_three_indices_keep_the_day_change_the_overview_supplies():
    prices = _index_prices()

    assert prices["NIFTY"]["change_pct"] == 0.49
    assert prices["SENSEX"]["change_pct"] == 0.37


def test_the_canonical_index_symbols_are_the_ones_the_catalogue_names():
    """One spelling, or the delayed baseline and a broker tick land in two
    different slots of the price store and the card follows whichever wrote
    last. This is the join D5.15 found broken between the tick batch and the
    store, asserted before it can happen again."""
    from services.brokers.catalogue import INDEX_EXCHANGES
    from services.heartbeat_engine import INDEX_PRICE_SYMBOLS

    assert set(INDEX_PRICE_SYMBOLS) == set(INDEX_EXCHANGES)


# ==================================================================
# B. A user on their own feed is served that feed
# ==================================================================


def test_the_owner_of_a_feed_covering_an_index_is_served_that_feed():
    """The headline claim of D5.17 at the price-broadcast layer."""
    with _clean_provider_registry():
        feed, _clock = _promoted_feed("u1", symbol="NIFTY")
        run(feed.subscribe(["NIFTY"]))
        prices = _index_prices("u1", feed=feed)

    assert prices["NIFTY"]["price"] == _promoted_feed_price()


def test_a_feed_price_does_not_erase_the_day_change_from_the_overview():
    """A `MarketTick` has no `change_pct`. Only `price` is taken from the
    resolution, so the overview's real day-change survives beside the live
    price — the server-side half of the same rule `applyLiveIndexPrices`
    enforces in the browser."""
    with _clean_provider_registry():
        feed, _clock = _promoted_feed("u1", symbol="NIFTY")
        run(feed.subscribe(["NIFTY"]))
        prices = _index_prices("u1", feed=feed)

    assert prices["NIFTY"]["change_pct"] == 0.49


def test_an_index_the_feed_does_not_cover_still_comes_from_the_baseline():
    """Per-symbol eligibility: a feed carrying NIFTY does not disqualify the
    baseline from serving SENSEX (MARKET_DATA_ARCHITECTURE.md)."""
    with _clean_provider_registry():
        feed, _clock = _promoted_feed("u1", symbol="NIFTY")
        run(feed.subscribe(["NIFTY"]))
        prices = _index_prices("u1", feed=feed)

    assert prices["SENSEX"]["price"] == 81020.0
    assert prices["BANKNIFTY"]["price"] == 52400.0


def test_one_users_index_feed_never_reaches_another_user():
    """A broker feed is legally its owner's own data (Category 2). An index is
    not an exception to that because it happens to be a public number: the feed
    it came from is the user's entitlement, not the platform's."""
    with _clean_provider_registry():
        feed, _clock = _promoted_feed("u1", symbol="NIFTY")
        run(feed.subscribe(["NIFTY"]))
        theirs = _index_prices("u2", feed=feed)

    assert theirs["NIFTY"]["price"] == 24810.0


def test_a_platform_resolution_is_not_served_a_users_feed():
    with _clean_provider_registry():
        feed, _clock = _promoted_feed("u1", symbol="NIFTY")
        run(feed.subscribe(["NIFTY"]))
        platform = _index_prices(None, feed=feed)

    assert platform["NIFTY"]["price"] == 24810.0


# ==================================================================
# C. Degradation
# ==================================================================


def test_an_unavailable_overview_does_not_cost_the_feed_its_prices():
    """The two reads are independent. An overview outage used to return `{}`
    and take every index with it; a covering feed can still price them."""
    import services.heartbeat_engine as heartbeat

    class _NoOverview(_IndexBaseline):
        async def fetch_indices(self):
            raise RuntimeError("overview unavailable")

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = _NoOverview()
        registry.register(baseline)
        run(baseline.connect())
        feed, _clock = _promoted_feed("u1", symbol="NIFTY")
        run(feed.subscribe(["NIFTY"]))
        registry.register(feed)
        prices = run(heartbeat._index_prices("u1"))

    assert prices["NIFTY"]["price"] == _promoted_feed_price()
    assert "change_pct" not in prices["NIFTY"], "a day-change was invented"


def test_no_provider_at_all_publishes_nothing_rather_than_zeroes():
    import services.heartbeat_engine as heartbeat

    with _clean_provider_registry() as registry:
        registry.clear()
        assert run(heartbeat._index_prices()) == {}


def _promoted_feed_price():
    from tests.test_dashboard_price_path import FEED_PRICE
    return FEED_PRICE


# ==================================================================
# D. The watchlist stream is resolved for its owner (D5.16 carry-over)
# ==================================================================


def test_the_watchlist_stream_passes_the_user_to_the_gateway():
    """D5.16 §5 moved this call onto the gateway and did not pass `user_id`, so
    every account's watchlist was still resolved platform-wide — the bypass it
    reported closing was closed only halfway. Its own three isolation tests were
    red in the tree; this asserts the call itself, which is the thing that was
    wrong, and fails even if those tests are later relaxed.
    """
    import services.heartbeat_engine as heartbeat
    from services.market_engine.gateway import market_gateway

    seen = {}
    original = market_gateway.get_prices

    async def _spy(symbols, *, user_id=None):
        seen["user_id"] = user_id
        return await original(symbols, user_id=user_id)

    with _clean_provider_registry() as registry:
        registry.clear()
        baseline = _IndexBaseline()
        registry.register(baseline)
        run(baseline.connect())
        market_gateway.get_prices = _spy
        try:
            run(heartbeat._watchlist_quotes("u1", ["NIFTY"]))
        finally:
            market_gateway.get_prices = original

    assert seen["user_id"] == "u1", (
        "the watchlist stream resolved without a user — a broker feed cannot win"
    )

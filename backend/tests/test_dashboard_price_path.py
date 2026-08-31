"""Sprint D5.15 — the price a dashboard actually renders (hermetic).

WHAT THIS FILE PINS, AND WHY IT EXISTS
---------------------------------------
D5.15's live run reached a state that should have been impossible: a real broker
feed was READY, STABLE and **selected** for its owner, `market.tick` was
publishing at ~7 batches a second with `source_tier: "streaming"` — and the
number on the dashboard was still Yahoo's.

The reason was not in the Market Engine. It was that the loop which produces the
message every price surface renders (`heartbeat_engine._price_stream_loop`)
never asked the Market Engine anything:

  * it called Yahoo **directly** (`fetch_all_universe_quotes`), behind a
    300-second bundle cache, so the prices were up to five minutes old while
    being presented as live — the "non-changing dashboard" symptom;
  * it built **one global map** and broadcast it to every socket, so it had no
    user. With no user no per-user provider is ever a candidate, and a promoted
    broker feed was unreachable **by construction** — not misranked, not stale,
    simply never asked;
  * the symbol set was `db.watchlist.distinct("symbol")` with **no filter**, so
    every socket received every other user's watchlist symbols.

This file pins the path that replaced it. The properties are deliberately about
*routing and scope* rather than about numbers: which provider answers, for whom,
and who receives the answer.

WHAT IS NOT RE-TESTED HERE
--------------------------
Readiness (D4.5), probation (D5.2), freshness (D5.3), latency (D5.4/D5.9),
health recovery (D5.6/D5.7) and the consumer feed contract (D5.13) all keep
their own suites and are used here rather than re-asserted — with one exception:
the fallback case reaches through the real freshness window, because "the
dashboard stops showing a dead feed's prices" is the property D5.15 owes and the
one a routing change could silently break.

No test sleeps, opens a socket or reaches a broker API.
"""

from services.market_engine.providers import YahooPollingAdapter
from services.market_engine.providers.streaming import StreamingTickProvider
from tests.test_broker_streaming import _clean_provider_registry, run
from tests.test_provider_probation import FakeClock, _tick

BASELINE_PRICE = 1285.0
FEED_PRICE = 1290.4


class _Baseline(YahooPollingAdapter):
    """The shared polled provider, answering a fixed price.

    Subclassed rather than mocked so the thing under test resolves a *real*
    baseline provider with the real tier, priority and capability set — the
    parts of the answer this file is actually about.
    """

    async def fetch_quote(self, symbol):
        return {"symbol": symbol, "price": BASELINE_PRICE, "change_pct": 0.5}


def _promoted_feed(user_id, symbol="RELIANCE", clock=None):
    """A per-user streaming feed that has earned selection.

    Built through the real provider with a real tick, so `is_ready`,
    `has_fresh_evidence` and coverage are all earned rather than stubbed. The
    probation window is zeroed because this file is about routing, not about
    re-proving D5.2.
    """
    clock = clock or FakeClock()
    feed = StreamingTickProvider(
        f"brokerfeed:nova:{user_id}",
        owner_user_id=user_id,
        probation_seconds=0.0,
        clock=clock,
    )
    run(feed.connect())
    run(feed.subscribe([symbol]))
    run(feed.on_raw([_tick(symbol, price=FEED_PRICE)]))
    assert feed.is_ready, "fixture did not reach the state the test is about"
    return feed, clock


def _registry_with(*providers):
    from services.market_engine.providers import provider_registry

    baseline = _Baseline()
    provider_registry.register(baseline)
    run(baseline.connect())
    for provider in providers:
        provider_registry.register(provider)
    return provider_registry, baseline


# ==================================================================
# A. Resolution is per user, and the broker feed wins for its owner
# ==================================================================


def test_the_owner_of_a_promoted_feed_is_served_that_feed():
    """The headline acceptance criterion, at the resolution layer: a user with a
    live broker feed must not be silently served the baseline."""
    from services.market_engine.gateway import market_gateway

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1")
        _registry_with(feed)

        prices = run(market_gateway.get_prices(["RELIANCE"], user_id="u1"))

    assert prices["RELIANCE"]["price"] == FEED_PRICE
    assert prices["RELIANCE"]["source_tier"] == "streaming"


def test_a_user_without_a_feed_is_served_the_baseline():
    from services.market_engine.gateway import market_gateway

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1")
        _registry_with(feed)

        prices = run(market_gateway.get_prices(["RELIANCE"], user_id="u2"))

    assert prices["RELIANCE"]["price"] == BASELINE_PRICE
    assert prices["RELIANCE"]["source_tier"] == "delayed"


def test_one_users_broker_prices_never_reach_another_user():
    """Cross-user isolation on the price path. A broker feed is legally its
    owner's own data (MARKET_DATA_ARCHITECTURE.md, Category 2), and the old
    global broadcast is exactly how it would have been redistributed."""
    from services.market_engine.gateway import market_gateway

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1")
        _registry_with(feed)

        theirs = run(market_gateway.get_prices(["RELIANCE"], user_id="u1"))
        others = run(market_gateway.get_prices(["RELIANCE"], user_id="u2"))
        platform = run(market_gateway.get_prices(["RELIANCE"]))

    assert theirs["RELIANCE"]["price"] == FEED_PRICE
    assert others["RELIANCE"]["price"] == BASELINE_PRICE
    assert platform["RELIANCE"]["price"] == BASELINE_PRICE


def test_a_symbol_the_feed_does_not_cover_still_comes_from_the_baseline():
    """Per-symbol eligibility, on the surface that renders many symbols at once:
    a broker feed covering one instrument must not blank the rest of the
    dashboard, and must not claim them either."""
    from services.market_engine.gateway import market_gateway

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1", symbol="RELIANCE")
        _registry_with(feed)

        prices = run(market_gateway.get_prices(["RELIANCE", "TCS"], user_id="u1"))

    assert prices["RELIANCE"]["source_tier"] == "streaming"
    assert prices["TCS"]["source_tier"] == "delayed"
    assert prices["TCS"]["price"] == BASELINE_PRICE


# ==================================================================
# B. Fallback — a dead feed stops being the dashboard's price
# ==================================================================


def test_a_stale_broker_feed_falls_back_to_the_baseline():
    """The D5.3 freshness window, exercised through the price path.

    Driven on the provider's injected clock rather than by sleeping. This is
    the case the market close produces every day, and the one a routing change
    could silently break by caching a provider choice.
    """
    from services.market_engine.gateway import market_gateway
    from services.market_engine.providers import DEFAULT_TICK_MAX_AGE_SECONDS

    with _clean_provider_registry() as registry:
        registry.clear()
        clock = FakeClock()
        feed, _clock = _promoted_feed("u1", clock=clock)
        _registry_with(feed)

        live = run(market_gateway.get_prices(["RELIANCE"], user_id="u1"))
        clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)
        stale = run(market_gateway.get_prices(["RELIANCE"], user_id="u1"))

    assert live["RELIANCE"]["source_tier"] == "streaming"
    assert stale["RELIANCE"]["source_tier"] == "delayed", (
        "a feed that stopped delivering was still the dashboard's price source"
    )
    assert stale["RELIANCE"]["price"] == BASELINE_PRICE


def test_a_feed_whose_link_dropped_stops_serving_the_dashboard():
    from services.market_engine.gateway import market_gateway

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1")
        _registry_with(feed)
        run(feed.mark_link_down("socket closed"))

        prices = run(market_gateway.get_prices(["RELIANCE"], user_id="u1"))

    assert prices["RELIANCE"]["source_tier"] == "delayed"


# ==================================================================
# C. The fan-out shortcut is sound
# ==================================================================


def test_a_user_with_no_feed_shares_the_platform_resolution():
    """`baseline_prices_are_shared` is what makes per-user delivery affordable:
    with no per-user provider eligible, resolving for this user and resolving
    for the platform choose from the identical candidate set."""
    from services.market_engine.gateway import market_gateway

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1")
        _registry_with(feed)

        assert market_gateway.baseline_prices_are_shared("u2")
        shared = run(market_gateway.get_prices(["RELIANCE"], user_id="u2"))
        platform = run(market_gateway.get_prices(["RELIANCE"]))

    # Compared on what the shortcut is a claim about — which provider answered
    # and what it said — rather than on the whole payload, which carries a
    # per-call fetch timestamp that differs between any two resolutions.
    assert shared["RELIANCE"]["price"] == platform["RELIANCE"]["price"]
    assert shared["RELIANCE"]["source_tier"] == platform["RELIANCE"]["source_tier"], (
        "the shortcut served a user an answer their own resolution would not have given"
    )


def test_the_owner_of_a_promoted_feed_does_not_share_it():
    """The other direction, and the one that matters: a promoted user must be
    resolved on their own or the shortcut would hand them the baseline."""
    from services.market_engine.gateway import market_gateway

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1")
        _registry_with(feed)

        assert not market_gateway.baseline_prices_are_shared("u1")


def test_a_stale_feeds_owner_rejoins_the_shared_fan_out():
    """The shortcut follows resolution rather than "has a broker connected" —
    an unready, stale or disconnected feed's owner is back on the baseline and
    must not cost a second resolution."""
    from services.market_engine.gateway import market_gateway
    from services.market_engine.providers import DEFAULT_TICK_MAX_AGE_SECONDS

    with _clean_provider_registry() as registry:
        registry.clear()
        clock = FakeClock()
        feed, _clock = _promoted_feed("u1", clock=clock)
        _registry_with(feed)
        assert not market_gateway.baseline_prices_are_shared("u1")

        clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)

        assert market_gateway.baseline_prices_are_shared("u1")


# ==================================================================
# D. Symbol selection is scoped to the account
# ==================================================================


def test_the_price_symbol_set_is_scoped_to_one_account():
    """The unfiltered `distinct("symbol")` is the defect this pins: per-user
    *delivery* without per-user *selection* would have kept one user's watchlist
    in another user's payload and merely hidden it."""
    import services.heartbeat_engine as heartbeat
    from tests._fakedb import FakeDB

    db = FakeDB()
    run(db.watchlist.insert_one({"user_id": "u1", "symbol": "MINE"}))
    run(db.watchlist.insert_one({"user_id": "u2", "symbol": "THEIRS"}))

    previous = heartbeat._db
    heartbeat._db = db
    try:
        mine = run(heartbeat._user_price_symbols("u1"))
    finally:
        heartbeat._db = previous

    assert "MINE" in mine
    assert "THEIRS" not in mine, "another account's watchlist symbol entered this user's price set"


def test_the_dashboard_universe_is_covered_for_an_account_that_watches_nothing():
    """An account with an empty watchlist and an empty portfolio still renders a
    dashboard, which is the whole of the D5.15 premise."""
    import services.heartbeat_engine as heartbeat
    from services.brokers.feed_universe import dashboard_symbols
    from tests._fakedb import FakeDB

    previous = heartbeat._db
    heartbeat._db = FakeDB()
    try:
        symbols = run(heartbeat._user_price_symbols("u1"))
    finally:
        heartbeat._db = previous

    assert set(dashboard_symbols()) <= symbols


# ==================================================================
# E. Nothing about the provider reaches the payload
# ==================================================================


def test_a_resolved_price_carries_a_tier_and_no_provider_identity():
    """Developer Rule 4 on the most-rendered payload in the product."""
    from services.market_engine.gateway import market_gateway

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1")
        _registry_with(feed)

        prices = run(market_gateway.get_prices(["RELIANCE"], user_id="u1"))

    rendered = repr(prices).lower()
    for forbidden in ("brokerfeed", "nova", "provider", "yahoo", "u1"):
        assert forbidden not in rendered, f"{forbidden} reached the price payload"
    assert prices["RELIANCE"]["source_tier"] == "streaming"


# ==================================================================
# F. A feed that goes stale is ANNOUNCED, not just demoted
# ==================================================================


class _Sockets:
    """The `ConnectionManager` surface the price loop uses, and nothing else."""

    def __init__(self, *users):
        self.user_connections = {u: {object()} for u in users}
        self.active = set(self.user_connections)
        self.sent = []

    async def send_to_user(self, user_id, message):
        self.sent.append((user_id, message))


def _forget_published_status(*users):
    """Clear the Source Manager's per-user status memo.

    `publish_status` is change-gated against a module-level cache, so a prior
    test in the same process that published for the same user id makes the first
    publish here a no-op. Without this the staleness assertions below pass in
    isolation and fail when the file runs after another that used "u1" — which
    is how they were first written, and how they first failed.
    """
    from services.market_engine.source_manager import source_manager

    for user in users:
        source_manager.forget_user_status(user)
    source_manager.forget_user_status(None)


def _price_loop_with(sockets, db=None):
    """Run one cycle of the real per-user price publish against `sockets`."""
    import services.heartbeat_engine as heartbeat
    from tests._fakedb import FakeDB

    previous_ws, previous_db = heartbeat._ws, heartbeat._db
    heartbeat._ws, heartbeat._db = sockets, db or FakeDB()
    try:
        return run(heartbeat._publish_prices())
    finally:
        heartbeat._ws, heartbeat._db = previous_ws, previous_db


def test_a_feed_going_stale_is_announced_to_its_owner():
    """The defect this closes was observed live: a feed stopped delivering at
    09:45:20Z and the last `provider.status` on the bus was 09:44:02Z, tier
    `streaming`.

    Staleness is not a state transition — no provider registers, unregisters or
    changes readiness — so every other publish path is silent for it and the
    demotion happens lazily inside resolution. Prices fall back correctly and
    the indicator is never told, which is the one outcome the feed-state
    contract exists to prevent.
    """
    from services.market_engine.event_bus import event_bus
    from services.market_engine.providers import DEFAULT_TICK_MAX_AGE_SECONDS
    from services.market_engine.source_manager import PROVIDER_STATUS_TOPIC

    with _clean_provider_registry() as registry:
        registry.clear()
        clock = FakeClock()
        feed, _clock = _promoted_feed("u1", clock=clock)
        _registry_with(feed)

        _forget_published_status("u1")
        published = []

        async def capture(event):
            published.append(event.get("data") or {})

        event_bus.subscribe(PROVIDER_STATUS_TOPIC, capture)
        try:
            sockets = _Sockets("u1")
            _price_loop_with(sockets)
            live = [p for p in published if p.get("user_id") == "u1"]

            clock.advance(DEFAULT_TICK_MAX_AGE_SECONDS + 1)
            published.clear()
            _price_loop_with(sockets)
            after = [p for p in published if p.get("user_id") == "u1"]
        finally:
            event_bus.unsubscribe(PROVIDER_STATUS_TOPIC, capture)

    assert live and live[-1]["tier"] == "streaming", "the promotion was never announced"
    assert after, "the feed went stale and the owner was never told"
    assert after[-1]["tier"] == "delayed"
    assert after[-1]["previous_tier"] == "streaming"


def test_a_steady_feed_announces_nothing_on_every_cycle():
    """Change-gated, so the cadence does not become an event storm: a feed that
    has not moved must produce no `provider.status` at all."""
    from services.market_engine.event_bus import event_bus
    from services.market_engine.source_manager import PROVIDER_STATUS_TOPIC

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1")
        _registry_with(feed)

        _forget_published_status("u1")
        sockets = _Sockets("u1")
        _price_loop_with(sockets)

        published = []

        async def capture(event):
            published.append(event)

        event_bus.subscribe(PROVIDER_STATUS_TOPIC, capture)
        try:
            _price_loop_with(sockets)
            _price_loop_with(sockets)
        finally:
            event_bus.unsubscribe(PROVIDER_STATUS_TOPIC, capture)

    assert published == [], "a steady feed published a status event per cycle"


def test_each_account_is_sent_its_own_prices_and_only_its_own():
    """Per-user delivery, through the real loop. The message this replaced was a
    single `broadcast` to every socket."""
    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1")
        _registry_with(feed)

        sockets = _Sockets("u1", "u2")
        served = _price_loop_with(sockets)

    assert served == 2
    recipients = [user for user, _msg in sockets.sent]
    assert sorted(recipients) == ["u1", "u2"]

    by_user = {user: msg for user, msg in sockets.sent}
    assert by_user["u1"]["data"]["RELIANCE"]["price"] == FEED_PRICE
    assert by_user["u2"]["data"]["RELIANCE"]["price"] == BASELINE_PRICE, (
        "a second account was served the first account's broker prices"
    )

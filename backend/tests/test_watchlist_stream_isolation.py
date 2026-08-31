"""Sprint D5.16 §2 — the watchlist realtime stream is one account's, and only one.

THE DEFECT THIS FILE EXISTS FOR
-------------------------------
`heartbeat_engine.task_watchlist_stream` read the symbol set with::

    symbols = await _db.watchlist.distinct("symbol")     # no filter

and published the result as a bus event carrying **no `user_id`**. The event
bridge routes on exactly that field: a payload with one goes to `send_to_user`,
a payload without one goes to `broadcast_to_channel`. So every socket subscribed
to the `watchlist` channel received every user's watchlisted symbols and their
prices — and not merely as inert JSON. `realtimeStore.priceMapFromMessage` folds
`watchlist.quotes` straight into `priceTicks`, so another account's instruments
entered the victim's live price store and rendered.

D5.15 named this exact query as the thing to avoid — in a docstring, 130 lines
below the loop that was still doing it. It fixed the `prices` broadcast and left
`watchlist.quotes` alone. That is the shape of defect this file is written to
make impossible to reintroduce: the scoping is asserted at the **publish**, not
at the render, because a consumer-side filter is not a security boundary.

WHAT IS ASSERTED, AND AT WHICH LAYER
-------------------------------------
* **Selection** — the symbols one cycle gathers for an account are that
  account's. Proven by reading a real `FakeDB` holding two users' rows.
* **Delivery** — the published envelope carries the owner's `user_id`, which is
  the field that makes the bridge deliver rather than broadcast. Proven against
  the real `event_bridge._deliver`, not against a description of it.
* **Query shape** — no `distinct` on the watchlist collection is ever issued
  without an owner filter. A spy records every call, so a future refactor that
  reintroduces the global read fails here rather than in production.

No test sleeps, opens a socket, or reaches a market API.
"""

import logging

from services.market_engine.providers import YahooPollingAdapter
from tests._fakedb import FakeDB
from tests.test_broker_streaming import _clean_provider_registry, run
from tests.test_dashboard_price_path import (
    BASELINE_PRICE,
    FEED_PRICE,
    _promoted_feed,
    _registry_with,
)

A_SYMBOLS = ("AAPL", "RELIANCE")
B_SYMBOLS = ("TCS", "INFY")


class _SpyDB(FakeDB):
    """A `FakeDB` that remembers how the watchlist collection was queried.

    The filter is the whole point: a `distinct("symbol")` with `None` for a
    filter is the defect, and it is indistinguishable from a correct call by
    looking at the result alone when only one user has rows. Recording the call
    is what lets a single-user fixture still falsify the global read.
    """

    def __init__(self):
        super().__init__()
        self.distinct_calls = []
        spy = self

        class _Watchlist:
            def __init__(self, inner):
                self._inner = inner

            async def distinct(self, key, flt=None):
                spy.distinct_calls.append((key, flt))
                return await self._inner.distinct(key, flt)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        self.watchlist = _Watchlist(super().__getattr__("watchlist"))


class _Sockets:
    """The `ConnectionManager` surface the heartbeat tasks read."""

    def __init__(self, *users):
        self.user_connections = {u: {object()} for u in users}
        self.active = set(self.user_connections)
        self.sent = []

    async def send_to_user(self, user_id, message):
        self.sent.append((str(user_id), message))

    async def broadcast_to_channel(self, channel, message):
        self.sent.append(("*BROADCAST*", message))

    async def broadcast(self, message):
        self.sent.append(("*BROADCAST*", message))


class _Baseline(YahooPollingAdapter):
    """The shared polled provider, answering a fixed price for any symbol."""

    async def fetch_quote(self, symbol):
        return {"symbol": symbol, "price": BASELINE_PRICE, "change_pct": 0.5,
                "rsi": 55.0, "volume_ratio": 1.2}


def _db_with_watchlists(**by_user):
    """A `_SpyDB` seeded with `{user_id: (symbols…)}`."""
    db = _SpyDB()
    for user_id, symbols in by_user.items():
        for symbol in symbols:
            run(db.watchlist.insert_one({"user_id": user_id, "symbol": symbol}))
    return db


def _run_watchlist_cycle(sockets, db):
    """One cycle of the real task, with the real event bridge wired to
    `sockets`.

    Deliberately routed through `event_bridge._deliver` rather than asserting on
    the bus event: "who receives this" is a property of the bridge's `user_id`
    branch, and a test that stopped at the bus would pass for a payload that the
    bridge then broadcasts to everybody — which is precisely the bug.
    """
    import services.heartbeat_engine as heartbeat
    from services.market_engine.event_bus import event_bus
    from services.realtime.event_bridge import _deliver

    delivered = []

    async def bridge(event):
        delivered.append(event)
        await _deliver(sockets, event)

    previous_ws, previous_db = heartbeat._ws, heartbeat._db
    heartbeat._ws, heartbeat._db = sockets, db
    event_bus.subscribe("watchlist.quotes", bridge)
    try:
        run(heartbeat.task_watchlist_stream())
    finally:
        event_bus.unsubscribe("watchlist.quotes", bridge)
        heartbeat._ws, heartbeat._db = previous_ws, previous_db
    return delivered


def _quotes_by_recipient(sockets):
    """`{user_id: {SYMBOL: quote}}` from what actually reached the sockets."""
    out = {}
    for user_id, message in sockets.sent:
        data = (message or {}).get("data") or {}
        if message.get("event") != "watchlist.quotes":
            continue
        out.setdefault(user_id, {}).update(data.get("quotes") or {})
    return out


# ==================================================================
# A. Cross-user isolation — the P0
# ==================================================================


def test_each_account_receives_only_its_own_watchlist_symbols():
    """The headline criterion. A watches AAPL/RELIANCE, B watches TCS/INFY, and
    neither ever sees the other's instruments."""
    with _clean_provider_registry() as registry:
        registry.clear()
        _registry_with()
        sockets = _Sockets("A", "B")
        _run_watchlist_cycle(sockets, _db_with_watchlists(A=A_SYMBOLS, B=B_SYMBOLS))

    received = _quotes_by_recipient(sockets)
    assert set(received) == {"A", "B"}, f"unexpected recipients: {sorted(received)}"
    assert set(received["A"]) == set(A_SYMBOLS)
    assert set(received["B"]) == set(B_SYMBOLS)
    for leaked in B_SYMBOLS:
        assert leaked not in received["A"], f"{leaked} — B's symbol entered A's payload"
    for leaked in A_SYMBOLS:
        assert leaked not in received["B"], f"{leaked} — A's symbol entered B's payload"


def test_the_stream_is_never_broadcast_to_every_socket():
    """The structural half. The bridge broadcasts a payload with no `user_id`,
    so an event that reaches `broadcast_to_channel` has already leaked whatever
    it carries — regardless of what the symbols happened to be."""
    with _clean_provider_registry() as registry:
        registry.clear()
        _registry_with()
        sockets = _Sockets("A", "B")
        delivered = _run_watchlist_cycle(sockets, _db_with_watchlists(A=A_SYMBOLS, B=B_SYMBOLS))

    assert delivered, "the fixture produced no event at all — the test proves nothing"
    for event in delivered:
        assert (event.get("data") or {}).get("user_id"), (
            "a watchlist.quotes event carried no owner, so the bridge broadcasts it"
        )
    recipients = [user for user, _ in sockets.sent]
    assert "*BROADCAST*" not in recipients


def test_the_watchlist_is_never_read_without_an_owner_filter():
    """One account, one row — and the assertion still falsifies the global read.

    A results-only test cannot: with a single user in the database,
    `distinct("symbol")` and `distinct("symbol", {"user_id": "A"})` return the
    identical list. The query is the evidence.
    """
    db = _db_with_watchlists(A=A_SYMBOLS)
    with _clean_provider_registry() as registry:
        registry.clear()
        _registry_with()
        _run_watchlist_cycle(_Sockets("A"), db)

    assert db.distinct_calls, "no watchlist read was issued — the probe could not have failed"
    for key, flt in db.distinct_calls:
        assert flt and flt.get("user_id"), (
            f"watchlist.{key} was read with filter {flt!r} — that is every user's watchlist"
        )


def test_an_account_that_watches_nothing_is_sent_nothing():
    """An empty watchlist must produce no payload, not an empty one and
    certainly not somebody else's."""
    with _clean_provider_registry() as registry:
        registry.clear()
        _registry_with()
        sockets = _Sockets("A", "B")
        _run_watchlist_cycle(sockets, _db_with_watchlists(B=B_SYMBOLS))

    received = _quotes_by_recipient(sockets)
    assert "A" not in received, "an account with an empty watchlist received a payload"
    assert set(received.get("B", {})) == set(B_SYMBOLS)


def test_a_disconnected_account_is_not_served_at_all():
    """The universe of recipients is the set of connected accounts. A user with
    rows in the database but no socket must not cause a publish — that is the
    difference between per-user delivery and a broadcast with extra steps."""
    with _clean_provider_registry() as registry:
        registry.clear()
        _registry_with()
        sockets = _Sockets("A")
        _run_watchlist_cycle(sockets, _db_with_watchlists(A=A_SYMBOLS, B=B_SYMBOLS))

    received = _quotes_by_recipient(sockets)
    assert set(received) == {"A"}
    assert not set(received["A"]) & set(B_SYMBOLS)


def test_one_accounts_prices_cannot_overwrite_anothers_for_a_shared_symbol():
    """Both accounts watch RELIANCE; only A has a promoted broker feed.

    The cross-user overwrite case: a single resolution reused for everybody
    would put A's broker price in B's payload. Each payload is resolved under
    its own owner, so the same symbol legitimately carries two different prices.
    """
    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("A", symbol="RELIANCE")
        _registry_with(feed)
        sockets = _Sockets("A", "B")
        _run_watchlist_cycle(
            sockets, _db_with_watchlists(A=("RELIANCE",), B=("RELIANCE",)))

    received = _quotes_by_recipient(sockets)
    assert received["A"]["RELIANCE"]["price"] == FEED_PRICE
    assert received["B"]["RELIANCE"]["price"] == BASELINE_PRICE, (
        "the second account was served the first account's broker price"
    )


# ==================================================================
# B. The same cycle is canonically routed (D5.16 §5)
# ==================================================================


def test_the_watchlist_stream_resolves_through_the_market_gateway():
    """The owner of a promoted feed is quoted from that feed.

    This is not a second assertion of the test above: that one is about two
    accounts not contaminating each other, this one is about the path. Before
    D5.16 this task called `fetch_real_stock_quote` directly, so a broker feed
    could not have won here no matter how it was ranked.
    """
    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("A", symbol="RELIANCE")
        _registry_with(feed)
        sockets = _Sockets("A")
        _run_watchlist_cycle(sockets, _db_with_watchlists(A=("RELIANCE",)))

    received = _quotes_by_recipient(sockets)
    assert received["A"]["RELIANCE"]["price"] == FEED_PRICE


def test_a_symbol_the_feed_does_not_cover_still_comes_from_the_baseline():
    """Per-instrument fallback, on this path too (D5.16 §10). One unresolvable
    instrument must not cost the account the rest of its watchlist."""
    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("A", symbol="RELIANCE")
        _registry_with(feed)
        sockets = _Sockets("A")
        _run_watchlist_cycle(sockets, _db_with_watchlists(A=("RELIANCE", "TCS")))

    received = _quotes_by_recipient(sockets)["A"]
    assert received["RELIANCE"]["price"] == FEED_PRICE
    assert received["TCS"]["price"] == BASELINE_PRICE


def test_the_payload_carries_no_provider_identity():
    """Developer Rule 4 on this path: a consumer learns freshness, never who."""
    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("A", symbol="RELIANCE")
        _registry_with(feed)
        sockets = _Sockets("A")
        _run_watchlist_cycle(sockets, _db_with_watchlists(A=("RELIANCE",)))

    quote = _quotes_by_recipient(sockets)["A"]["RELIANCE"]
    blob = repr(quote).lower()
    for forbidden in ("brokerfeed", "nova", "zerodha", "upstox", "angelone",
                      "fyers", "dhan", "yahoo", "provider"):
        assert forbidden not in blob, f"{forbidden!r} reached a consumer payload: {quote!r}"


# ==================================================================
# C. Logging
# ==================================================================


def test_the_cycle_logs_no_account_identifier_at_debug(caplog):
    """A per-user loop is a new opportunity to log a user id per cycle. The
    symbols are the user's own data and the id identifies them; neither belongs
    in a shared log at DEBUG."""
    with _clean_provider_registry() as registry:
        registry.clear()
        _registry_with()
        with caplog.at_level(logging.DEBUG):
            _run_watchlist_cycle(_Sockets("user-abc-123"), _db_with_watchlists(**{"user-abc-123": A_SYMBOLS}))

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "user-abc-123" not in text, f"an account identifier was logged:\n{text}"

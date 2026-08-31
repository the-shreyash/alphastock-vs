"""Sprint D5.16 §5/§7/§10 — every rendered equity price comes through the gateway.

WHAT THE AUDIT FOUND, AND WHY IT WAS BIGGER THAN THE BRIEF SAID
----------------------------------------------------------------
D5.16 opened with one named bypass: Top AI Picks calling Yahoo directly. The
audit found four, and the one nobody had named was by far the widest:

    server.real_quote / server.real_quotes_map

Two functions, ~30 lines, and the equity-price source for `GET /api/watchlist`,
the watchlist add, `/analysis/explain`, `/analysis/full-report`, the advisor,
the open-trades view, the exit-price fallback, the portfolio monitor and the
socket's own `subscribe_prices` handler. None of them had ever touched the
Market Gateway, so for every one of those surfaces a user's own promoted broker
feed was unreachable **by construction** — not misranked, never asked.

The other three: `fetch_real_top_picks` (the brief's), `task_watchlist_stream`
(which was also the security P0, pinned in `test_watchlist_stream_isolation.py`)
and `portfolio_stream.quotes_map`, which marked a holding from Yahoo on every
recompute a broker tick did not drive — so the live number and the snapshot
number came from different providers and could disagree by a day's move.

WHAT THIS FILE ASSERTS
----------------------
Routing and scope, not numbers: which provider answers, for whom, and whether
one instrument's unavailability costs the others. The provider fixtures are real
`YahooPollingAdapter` and `StreamingTickProvider` instances so that resolution,
tier stamping and eligibility are earned rather than stubbed.

No test sleeps, opens a socket, or reaches a market API.
"""

from services.market_engine.providers import YahooPollingAdapter
from tests.test_broker_streaming import _clean_provider_registry, run
from tests.test_dashboard_price_path import (
    BASELINE_PRICE,
    FEED_PRICE,
    _promoted_feed,
)

#: Technical fields the delayed provider carries and a tick never can.
TECHNICALS = {"rsi": 61.5, "volume_ratio": 1.8, "macd": 4.2, "macd_signal": 1.1}


class _Baseline(YahooPollingAdapter):
    """The shared polled provider, answering for any symbol."""

    async def fetch_quote(self, symbol):
        return {"symbol": symbol, "price": BASELINE_PRICE, "change_pct": 0.5,
                "name": symbol, "sector": "Energy", **TECHNICALS}

    async def fetch_universe_quotes(self):
        return [await self.fetch_quote("RELIANCE")]


def _registry_with(*providers):
    from services.market_engine.providers import provider_registry

    baseline = _Baseline()
    provider_registry.register(baseline)
    run(baseline.connect())
    for provider in providers:
        provider_registry.register(provider)
    return baseline


# ==================================================================
# A. The REST equity-quote helpers (server.real_quote / real_quotes_map)
# ==================================================================


def test_the_rest_quote_helper_serves_a_promoted_feed_to_its_owner():
    """`GET /api/watchlist` and eight other surfaces price equities through
    this function. Before D5.16 a broker feed could not have won here."""
    import server

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1", symbol="RELIANCE")
        _registry_with(feed)

        mine = run(server.real_quote("RELIANCE", user_id="u1"))
        theirs = run(server.real_quote("RELIANCE", user_id="u2"))

    assert mine["price"] == FEED_PRICE
    assert theirs["price"] == BASELINE_PRICE, (
        "a second account was served the first account's broker price"
    )


def test_the_batch_helper_is_scoped_to_one_account():
    import server

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1", symbol="RELIANCE")
        _registry_with(feed)

        mine = run(server.real_quotes_map(["RELIANCE"], user_id="u1"))
        theirs = run(server.real_quotes_map(["RELIANCE"], user_id="u2"))

    assert mine["RELIANCE"]["price"] == FEED_PRICE
    assert theirs["RELIANCE"]["price"] == BASELINE_PRICE


def test_fallback_is_per_instrument_on_the_rest_path():
    """D5.16 §10. One instrument the feed cannot serve must not downgrade the
    rest of the request — the whole point of resolving per symbol."""
    import server

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1", symbol="RELIANCE")
        _registry_with(feed)

        quotes = run(server.real_quotes_map(["RELIANCE", "TCS"], user_id="u1"))

    assert quotes["RELIANCE"]["price"] == FEED_PRICE
    assert quotes["TCS"]["price"] == BASELINE_PRICE


def test_an_unresolvable_symbol_is_explicitly_unavailable_not_absent():
    """The pre-D5.16 contract: a symbol with no live quote maps to None so a
    caller renders an explicit unavailable state rather than a hole."""
    import server

    class _Nothing(_Baseline):
        async def fetch_quote(self, symbol):
            return None

    with _clean_provider_registry() as registry:
        registry.clear()
        from services.market_engine.providers import provider_registry

        provider = _Nothing()
        provider_registry.register(provider)
        run(provider.connect())

        quotes = run(server.real_quotes_map(["NOSUCH"]))

    assert quotes == {"NOSUCH": None}


def test_the_rest_quote_carries_a_tier_and_no_provider_identity():
    """Developer Rule 4 on the busiest read path in the product."""
    import server

    with _clean_provider_registry() as registry:
        registry.clear()
        _registry_with()
        quote = run(server.real_quote("RELIANCE"))

    assert quote["source_tier"] == "delayed"
    blob = repr(quote).lower()
    for forbidden in ("yahoo", "brokerfeed", "provider", "adapter"):
        assert forbidden not in blob, f"{forbidden!r} reached a consumer payload"


# ==================================================================
# B. The canonical quote keeps the fields the product scores on
# ==================================================================


def test_the_canonical_quote_carries_both_halves_of_the_macd_pair():
    """`macd_signal` was the one field the normalizer dropped, and nothing
    noticed while every consumer of it read a raw Yahoo quote.

    Routing those consumers through the gateway is what made the omission
    load-bearing: `server._advisor_score` and the top-pick scorer both branch on
    `macd > macd_signal`, and an absent signal line becomes `0.0` — a comparison
    that is true for every stock with positive momentum. The bug would not have
    raised, logged, or failed a price assertion; it would have changed what the
    product recommends.
    """
    import server

    with _clean_provider_registry() as registry:
        registry.clear()
        _registry_with()
        quote = run(server.real_quote("RELIANCE"))

    assert quote["macd"] == TECHNICALS["macd"]
    assert quote["macd_signal"] == TECHNICALS["macd_signal"]
    assert quote["macd"] > quote["macd_signal"]


def test_a_thin_streaming_quote_omits_technicals_rather_than_zeroing_them():
    """A tick carries a price, not an RSI. The honest canonical quote leaves
    them None; writing 0.0 would put a fabricated indicator beside a real
    price, and every scorer treats 0.0 as a reading."""
    import server

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1", symbol="RELIANCE")
        _registry_with(feed)
        quote = run(server.real_quote("RELIANCE", user_id="u1"))

    assert quote["price"] == FEED_PRICE
    assert quote["rsi"] is None and quote["macd_signal"] is None


# ==================================================================
# C. Top AI Picks (D5.16 §7)
# ==================================================================


def _top_picks():
    """One uncached generation.

    The 30-minute cache is cleared rather than worked around: without this the
    second test in this section reads the first one's picks and asserts nothing
    at all — a probe that could not fail.
    """
    from services import cache
    from services.real_market import fetch_real_top_picks

    cache._memory.pop("real_top_picks", None)
    return run(fetch_real_top_picks(3))


def test_top_picks_prices_come_from_the_market_gateway():
    """The AI ranking is untouched; only the price acquisition moved. With the
    gateway's baseline answering, every pick is priced at the baseline — which
    it could not have been while the function called Yahoo itself, because the
    registry here has no Yahoo in it at all beyond the fixture."""
    with _clean_provider_registry() as registry:
        registry.clear()
        _registry_with()
        result = _top_picks()

    assert result["available"], result
    assert result["picks"], "no picks were produced from a working provider"
    for pick in result["picks"]:
        assert pick["price"] == BASELINE_PRICE, (
            "a pick was priced by something other than the resolved provider"
        )
        # The derived levels come off the canonical price, so they move with it.
        assert pick["stop_loss"] < pick["price"] < pick["target1"]


def test_top_picks_are_unavailable_rather_than_invented_when_nothing_resolves():
    """No provider can serve. The honest answer is an explicit unavailable, not
    a pick priced from a stale or simulated number."""
    with _clean_provider_registry() as registry:
        registry.clear()
        result = _top_picks()

    assert result["available"] is False
    assert result["picks"] == []


def test_the_unavailable_note_names_no_provider():
    """It used to say "once Yahoo Finance is reachable" — a provider identity in
    a user-facing string, and since D5.16 also simply wrong: Yahoo being
    reachable is neither necessary nor sufficient."""
    with _clean_provider_registry() as registry:
        registry.clear()
        note = _top_picks().get("note", "").lower()

    for forbidden in ("yahoo", "upstox", "zerodha", "angel", "fyers", "dhan"):
        assert forbidden not in note, f"the unavailable note names {forbidden!r}"


# ==================================================================
# D. Portfolio marks (D5.16 §5)
# ==================================================================


def test_portfolio_marks_are_resolved_for_the_account_that_owns_them():
    """A holding whose price is arriving live on the account's own feed was
    marked from the delayed baseline on every recompute a tick did not drive."""
    from services import portfolio_stream

    with _clean_provider_registry() as registry:
        registry.clear()
        feed, _clock = _promoted_feed("u1", symbol="RELIANCE")
        _registry_with(feed)

        mine = run(portfolio_stream.quotes_map(["RELIANCE"], user_id="u1"))
        theirs = run(portfolio_stream.quotes_map(["RELIANCE"], user_id="u2"))

    assert mine["RELIANCE"]["price"] == FEED_PRICE
    assert theirs["RELIANCE"]["price"] == BASELINE_PRICE, (
        "one account's broker price was used to mark another account's holding"
    )


def test_an_unpriceable_holding_is_none_rather_than_zero():
    from services import portfolio_stream

    class _Nothing(_Baseline):
        async def fetch_quote(self, symbol):
            return None

    with _clean_provider_registry() as registry:
        registry.clear()
        from services.market_engine.providers import provider_registry

        provider = _Nothing()
        provider_registry.register(provider)
        run(provider.connect())

        quotes = run(portfolio_stream.quotes_map(["RELIANCE"]))

    assert quotes == {"RELIANCE": None}


# ==================================================================
# E. No direct-Yahoo import survives on these paths
# ==================================================================


def test_the_routed_functions_no_longer_reach_the_yahoo_client():
    """A structural backstop for the four bypasses this sprint closed.

    Behavioural assertions above prove the gateway *is* used; this proves the
    old path is gone, which is the half that a partially-reverted refactor would
    otherwise satisfy — a function that calls the gateway and then overwrites
    the result with a direct fetch passes every test above.
    """
    import ast
    import inspect
    import textwrap

    import server
    from services import portfolio_stream, real_market

    def _called_names(fn):
        """Every name this function actually CALLS.

        Parsed rather than grepped. Each of these functions documents the
        bypass it replaced, so a substring search matches its own docstring and
        the probe fails for a reason that has nothing to do with the code — the
        shape of false positive that gets a real control deleted.
        """
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        return {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }

    banned = {"fetch_real_stock_quote", "fetch_yahoo_quote", "fetch_all_universe_quotes"}
    for name, fn in [
        ("server.real_quote", server.real_quote),
        ("server.real_quotes_map", server.real_quotes_map),
        ("portfolio_stream.quotes_map", portfolio_stream.quotes_map),
        ("real_market.fetch_real_top_picks", real_market.fetch_real_top_picks),
    ]:
        leaked = _called_names(fn) & banned
        assert not leaked, f"{name} still calls {sorted(leaked)} directly"

    assert "get_prices" in _called_names(real_market.fetch_real_top_picks)
    assert "get_prices" in _called_names(server.real_quotes_map)

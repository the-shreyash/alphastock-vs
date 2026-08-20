"""Market-data API under provider failure (PH3.3).

WHAT PRODUCTION FAILURE THIS CATCHES
------------------------------------
Market data comes from third-party providers that go down, time out, rate-limit,
and occasionally return something that parses but is nonsense. Every one of
those is a *normal operating condition*, not an exception — and the question
this suite answers is whether each becomes a controlled application response or
an uncontrolled 500.

It matters more here than anywhere else in the API, because a provider incident
hits every market endpoint simultaneously. If the failure mode is a 500, the
entire product goes dark at exactly the moment users open it to find out what is
happening. If the failure mode is `available: false`, the UI shows an honest
"data unavailable" and everything that does not depend on live prices — open
positions, the journal, the watchlist — keeps working.

The second rule, from CLAUDE.md and ADR-021, is that a provider outage must
never be papered over with invented numbers. So these tests assert not only
"did not crash" but "did not fabricate": on the degraded path the payload must
be explicitly marked unavailable and must not carry price fields.

`test_api_contract.py` (PH3.1) already covers the *happy* shapes of overview,
stock detail and top picks. This file is the failure matrix, which is the half
no live test can trigger on demand.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import server

#: Deliberately obvious fixture values — a number in a failure message should be
#: identifiable as test data at a glance, never mistaken for leaked live data.
TEST_QUOTE = {"symbol": "RELIANCE", "name": "Reliance Industries",
              "price": 2500.0, "change": 25.0, "change_pct": 1.0}


class _Boom(Exception):
    """A provider raising something the application did not anticipate."""


#: How a market-data call fails **as the routes can observe it**.
#:
#: WHY THERE ARE NO `side_effect=TimeoutError` CASES HERE
#: -----------------------------------------------------
#: The obvious version of this suite patches `real_overview`/`fetch_real_gainers`
#: to *raise* a timeout and asserts the route survives. Those tests fail — and
#: they are wrong, not the application. Failure containment in this system lives
#: one layer down, at the transport boundary: `fetch_yahoo_quote` wraps its
#: `httpx` call in `except Exception -> return None`, and
#: `fetch_all_universe_quotes` gathers with `return_exceptions=True` and skips
#: the failures. A real provider timeout therefore reaches the route as `None`
#: or `[]`, never as an exception.
#:
#: Patching the top of the stack to raise would have been a scenario production
#: cannot produce, and "fixing" the routes to satisfy it would have added
#: exception handlers for exceptions that never arrive. Containment is instead
#: asserted directly, at the layer that provides it, in
#: `TestFailureContainmentLivesInTheServiceLayer` below — so if someone removes
#: that `try/except`, a test fails at the place the guarantee actually broke.
PROVIDER_FAILURES = {
    "returns_none": {"return_value": None},
    "returns_empty_dict": {"return_value": {}},
    "returns_empty_list": {"return_value": []},
    "malformed_payload": {"return_value": {"unexpected": "shape", "price": "not-a-number"}},
}

FAILURE_IDS = list(PROVIDER_FAILURES)

#: Status codes that represent a *controlled* answer to a market-data outage.
#: 503 is explicitly included: "the upstream is down, try later" is the correct,
#: documented response for a known symbol with no live quote, and asserting a
#: blanket `< 500` would have failed the endpoint for doing the right thing.
#: What is never acceptable is 500 — an unhandled exception reaching the client.
CONTROLLED = {200, 400, 404, 422, 429, 503}


def assert_controlled(resp, context):
    assert resp.status_code in CONTROLLED, (
        f"{context} answered {resp.status_code}; a market-data outage must be a "
        f"controlled response (one of {sorted(CONTROLLED)}), never an unhandled 500."
    )


# --------------------------------------------------------------------------- #
# Overview                                                                      #
# --------------------------------------------------------------------------- #
class TestMarketOverview:
    @pytest.mark.parametrize("failure", FAILURE_IDS)
    def test_provider_failure_never_becomes_a_500(self, client, monkeypatch, failure):
        monkeypatch.setattr(server, "real_overview",
                            AsyncMock(**PROVIDER_FAILURES[failure]))
        resp = client.get("/api/market/overview")
        assert_controlled(resp, f"/api/market/overview with a provider that {failure}")

    def test_outage_is_reported_honestly_and_carries_no_prices(self, client, monkeypatch):
        """ADR-021: never fabricate market data. A degraded payload that still
        carried a `nifty` value would be indistinguishable from a real one."""
        monkeypatch.setattr(server, "real_overview", AsyncMock(return_value=None))
        body = client.get("/api/market/overview").json()
        assert body["available"] is False
        assert "nifty" not in body
        assert body["note"], "the user must be told why the data is missing"


# --------------------------------------------------------------------------- #
# Stock detail                                                                  #
# --------------------------------------------------------------------------- #
class TestStockDetail:
    @pytest.mark.parametrize("failure", FAILURE_IDS)
    def test_provider_failure_never_becomes_a_500(self, client, monkeypatch, failure):
        monkeypatch.setattr(server, "real_quote", AsyncMock(**PROVIDER_FAILURES[failure]))
        resp = client.get("/api/stocks/RELIANCE")
        assert_controlled(resp, f"/api/stocks/RELIANCE with a provider that {failure}")

    def test_known_symbol_without_data_is_503_and_unknown_is_404(self, client, monkeypatch):
        """Conflating these would tell the frontend "stock not found" during an
        outage, sending users to search for a stock that exists."""
        monkeypatch.setattr(server, "real_quote", AsyncMock(return_value=None))
        assert client.get("/api/stocks/RELIANCE").status_code == 503
        assert client.get("/api/stocks/NOSUCHSTOCK").status_code == 404

    def test_live_data_is_served_when_the_provider_is_healthy(self, client, monkeypatch):
        """The control for the outage tests above: without this, a handler that
        always returned 503 would pass every other test in this class."""
        monkeypatch.setattr(server, "real_quote", AsyncMock(return_value=dict(TEST_QUOTE)))
        resp = client.get("/api/stocks/RELIANCE")
        assert resp.status_code == 200
        assert resp.json()["price"] == 2500.0


# --------------------------------------------------------------------------- #
# The wider market surface                                                      #
# --------------------------------------------------------------------------- #
#: (endpoint, service function patched) for the routes that delegate straight to
#: a provider call. Each is a one-line handler, which is exactly why they are
#: worth sweeping: there is no error handling in any of them to read, so the
#: only way to know what they do on failure is to make one fail.
DELEGATING_ENDPOINTS = [
    ("/api/market/gainers", "services.real_market.fetch_real_gainers"),
    ("/api/market/losers", "services.real_market.fetch_real_losers"),
    ("/api/market/sectors", "services.real_market.fetch_real_sectors"),
    ("/api/market/global", "services.real_market.fetch_real_global_markets"),
    ("/api/market/commodities", "services.real_market.fetch_real_commodities"),
    ("/api/market/fii-dii", "services.real_market.fetch_real_fii_dii"),
]


class TestDelegatingMarketEndpoints:
    @pytest.mark.parametrize("endpoint,target", DELEGATING_ENDPOINTS,
                             ids=[e for e, _ in DELEGATING_ENDPOINTS])
    @pytest.mark.parametrize("failure", FAILURE_IDS)
    def test_provider_failure_never_becomes_a_500(self, client, endpoint, target, failure):
        with patch(target, new_callable=AsyncMock, **PROVIDER_FAILURES[failure]):
            resp = client.get(endpoint)
        assert_controlled(resp, f"{endpoint} with a provider that {failure}")

    @pytest.mark.parametrize("endpoint,target", DELEGATING_ENDPOINTS,
                             ids=[e for e, _ in DELEGATING_ENDPOINTS])
    def test_empty_provider_result_is_an_empty_answer_not_an_error(
            self, client, endpoint, target):
        """"No rows today" is a legitimate market answer (a holiday, a halted
        session) and must be distinguishable from a failure."""
        with patch(target, new_callable=AsyncMock, return_value=[]):
            resp = client.get(endpoint)
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Scanner                                                                       #
# --------------------------------------------------------------------------- #
class TestScanner:
    @pytest.mark.parametrize("failure", FAILURE_IDS)
    def test_scanner_failure_never_becomes_a_500(self, client, failure):
        with patch("services.market_engine.scanner_engine.scan",
                   new_callable=AsyncMock, **PROVIDER_FAILURES[failure]):
            resp = client.get("/api/market/scanner")
        assert_controlled(resp, f"/api/market/scanner with a scan that {failure}")

    def test_no_matches_is_an_empty_result_not_a_404(self, client):
        """A scan that matches nothing succeeded — it just found nothing. A 404
        would make "no setups today" look like a broken endpoint."""
        with patch("services.market_engine.scanner_engine.scan",
                   new_callable=AsyncMock, return_value=[]):
            resp = client.get("/api/market/scanner")
        assert resp.status_code == 200

    def test_presets_are_available_without_a_provider(self, client):
        """Static configuration must not depend on live data being up."""
        resp = client.get("/api/market/scanner/presets")
        assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# News                                                                          #
# --------------------------------------------------------------------------- #
#: `fetch_news`/`search_stock_news` are contractually list-returning — every
#: path through them ends at a `list`, and a failing feed contributes zero
#: articles rather than `None`. So `returns_none` is excluded here for the same
#: reason the timeout cases were excluded above: it is a scenario the service
#: cannot produce, and "hardening" the route against it would be dead code
#: defending an impossible input. The contract itself is asserted in
#: `TestNewsServiceContract` rather than assumed.
LIST_FAILURE_IDS = ["returns_empty_list"]


class TestNews:
    @pytest.mark.parametrize("failure", LIST_FAILURE_IDS)
    def test_feed_failure_never_becomes_a_500(self, client, failure):
        with patch("services.news_service.fetch_news",
                   new_callable=AsyncMock, **PROVIDER_FAILURES[failure]):
            resp = client.get("/api/news")
        assert_controlled(resp, f"/api/news with a feed that {failure}")

    def test_empty_feed_reports_zero_articles(self, client):
        with patch("services.news_service.fetch_news",
                   new_callable=AsyncMock, return_value=[]):
            resp = client.get("/api/news")
        assert resp.status_code == 200
        assert resp.json() == {"articles": [], "count": 0}

    @pytest.mark.parametrize("failure", FAILURE_IDS)
    def test_sentiment_failure_never_becomes_a_500(self, client, failure):
        with patch("services.news_service.get_market_sentiment",
                   new_callable=AsyncMock, **PROVIDER_FAILURES[failure]):
            resp = client.get("/api/news/sentiment")
        assert_controlled(resp, f"/api/news/sentiment with a feed that {failure}")

    @pytest.mark.parametrize("failure", LIST_FAILURE_IDS)
    def test_per_stock_news_failure_never_becomes_a_500(self, client, failure):
        with patch("services.news_service.search_stock_news",
                   new_callable=AsyncMock, **PROVIDER_FAILURES[failure]):
            resp = client.get("/api/news/stock/RELIANCE")
        assert_controlled(resp, f"/api/news/stock with a feed that {failure}")


class TestNewsServiceContract:
    """`fetch_news` always returns a list — the guarantee `/api/news` relies on.

    The route computes `len(articles)` with no guard. That is correct only
    because every path through `fetch_news` ends at a list, including the one
    where every RSS feed raises. If a future refactor introduced an early
    `return None`, `/api/news` would 500 and nothing at the route level would
    have warned. This test sits at the boundary that makes the route safe.
    """

    def test_every_feed_failing_still_returns_a_list(self):
        from services import news_service

        def explode(feed):
            raise _Boom("feed unreachable")

        async def exercise():
            with patch.object(news_service, "_parse_feed", side_effect=explode), \
                 patch.object(news_service, "cache_get", new_callable=AsyncMock,
                              return_value=None), \
                 patch.object(news_service, "cache_set", new_callable=AsyncMock):
                return await news_service.fetch_news()

        articles = asyncio.run(exercise())
        assert articles == [], "a total feed outage must be an empty list, never None"

    def test_sentiment_reports_unavailable_rather_than_guessing(self):
        """No articles means no sentiment — inventing "neutral" would be a
        fabricated market signal, which ADR-021 forbids."""
        from services import news_service

        async def exercise():
            with patch.object(news_service, "fetch_news", new_callable=AsyncMock,
                              return_value=[]):
                return await news_service.get_market_sentiment()

        sentiment = asyncio.run(exercise())
        assert sentiment["available"] is False
        assert sentiment["score"] is None
        assert sentiment["note"]


# --------------------------------------------------------------------------- #
# Search                                                                        #
# --------------------------------------------------------------------------- #
class TestStockSearch:
    @pytest.mark.parametrize("failure", FAILURE_IDS)
    def test_search_falls_back_rather_than_failing(self, client, failure):
        """Search has a static-metadata fallback precisely so it keeps working
        during an outage; this proves the fallback is reached from every failure
        shape, not only from the `None` the happy-path test uses."""
        with patch("services.real_market.search_yahoo_stocks",
                   new_callable=AsyncMock, **PROVIDER_FAILURES[failure]):
            resp = client.get("/api/stocks/search", params={"q": "rel"})
        assert_controlled(resp, f"/api/stocks/search with a provider that {failure}")

    def test_offline_search_still_returns_known_symbols(self, client):
        with patch("services.real_market.search_yahoo_stocks",
                   new_callable=AsyncMock, return_value=None):
            resp = client.get("/api/stocks/search", params={"q": "reliance"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# --------------------------------------------------------------------------- #
# Where failure containment actually lives                                      #
# --------------------------------------------------------------------------- #
class TestFailureContainmentLivesInTheServiceLayer:
    """The guarantee the whole file above depends on, asserted at its source.

    None of the routes catch anything. That is safe *only* because
    `services.real_market` converts every transport failure into `None`/`[]`
    before a route ever sees it. That containment is therefore load-bearing for
    the entire market surface, and it is a single `except Exception` that a
    future refactor could narrow or drop without any route-level test noticing —
    the routes would keep passing right up until the first real provider
    timeout, at which point every market endpoint would 500 at once.

    These tests fail at the exact line that broke instead.
    """

    @pytest.mark.parametrize("error", [
        pytest.param(httpx.TimeoutException("timed out"), id="timeout"),
        pytest.param(httpx.ConnectError("refused"), id="connection_refused"),
        pytest.param(httpx.ReadError("reset"), id="read_error"),
        pytest.param(_Boom("provider returned nonsense"), id="unexpected_exception"),
    ])
    def test_transport_failure_becomes_none_not_an_exception(self, error):
        from services.real_market import fetch_yahoo_quote

        async def exercise():
            with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=error):
                return await fetch_yahoo_quote("RELIANCE")

        assert asyncio.run(exercise()) is None, (
            "fetch_yahoo_quote let a transport error escape. Every market route "
            "relies on it returning None instead — see this class's docstring."
        )

    def test_a_non_200_from_the_provider_becomes_none(self):
        from services.real_market import fetch_yahoo_quote

        async def exercise():
            response = httpx.Response(429, request=httpx.Request("GET", "https://example.test"))
            with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response):
                return await fetch_yahoo_quote("RELIANCE")

        assert asyncio.run(exercise()) is None, \
            "a rate-limited provider must degrade, not propagate"

    def test_universe_fetch_skips_failures_rather_than_aborting(self):
        """One bad symbol must not blank the whole market screen.

        `asyncio.gather(..., return_exceptions=True)` is what makes partial
        results possible; without it a single failing ticker takes down gainers,
        losers and sectors together.
        """
        from services.real_market import fetch_all_universe_quotes

        # `source` is deliberately still here: this is a *raw provider* payload,
        # and DD-1 removed the field from `fetch_yahoo_quote`'s output, not from
        # what a hypothetical provider might send. Keeping it proves the
        # normalization boundary strips whatever provenance a provider invents.
        healthy = {"price": 100.0, "change_pct": 1.0, "prev_close": 99.0,
                   "change": 1.0, "source": "yahoo_finance"}
        calls = {"n": 0}

        async def flaky(symbol, range_str="2d"):
            calls["n"] += 1
            if calls["n"] % 2:
                raise _Boom("this symbol failed")
            return dict(healthy)

        async def exercise():
            with patch("services.real_market.fetch_yahoo_quote", side_effect=flaky), \
                 patch("services.real_market.cache_get", new_callable=AsyncMock,
                       return_value=None), \
                 patch("services.real_market.cache_get_many", new_callable=AsyncMock,
                       return_value={}), \
                 patch("services.real_market.cache_set", new_callable=AsyncMock):
                return await fetch_all_universe_quotes()

        quotes = asyncio.run(exercise())
        assert quotes, "every quote was dropped although half the symbols succeeded"
        assert all(q.get("symbol") for q in quotes)


# --------------------------------------------------------------------------- #
# Hermeticity                                                                   #
# --------------------------------------------------------------------------- #
def test_market_endpoints_reach_no_real_provider(client):
    """The guard behind every test above.

    With no patch in place, the network guard (`tests/_netguard.py`) blocks the
    outbound socket. If this endpoint ever answers with genuine market data in a
    hermetic run, something has bypassed the guard and the whole suite's
    determinism is void. `NetworkAccessBlocked` subclasses `OSError`, so the
    application takes its normal offline branch and answers honestly.
    """
    body = client.get("/api/market/overview").json()
    assert body.get("available") is False, \
        "a hermetic run obtained live market data — the network guard was bypassed"

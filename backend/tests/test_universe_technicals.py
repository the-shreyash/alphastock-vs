"""The universe path must carry the fields the scanner and ranker score on (D5.19).

THE DEFECT
----------
Two paths priced equities and each had exactly the defect the other did not.

`fetch_real_stock_quote` (the single-quote path, `GET /api/stocks/{symbol}`)
asked Yahoo for `3mo` and computed RSI, MACD, average volume and volume ratio
from the returned series.

`fetch_all_universe_quotes` (the universe path, behind `/market/scanner` and
`/market/ranking`) asked for `2d` and computed none of them — two bars cannot
produce a 14-period RSI. Measured live on 2026-09-01, all 31 universe quotes
carried `rsi=None, macd=None, macd_signal=None, avg_volume=None,
volume_ratio=None`.

Everything downstream was scoring on absent inputs, and neither consumer could
tell absent from neutral:

* **The scanner returned no real picks.** Six of its eight strategy presets
  filter on `volume_ratio_min`, `rsi_min/max` or `macd_bullish`; measured live,
  `intraday`, `swing`, `momentum`, `breakout`, `reversal` and `growth` each
  matched **0 of 31** stocks. The remaining two, `value` and `dividend`, sort on
  `rsi` and `volume_ratio` — `q.get(key) or 0` makes every stock equal, the sort
  is stable, and so they returned the universe in *declaration order*
  (RELIANCE, TCS, HDFCBANK, INFY...). That constant, market-independent list is
  what reads as a hardcoded demo fixture, and this is where it came from.

* **The ranking engine fabricated its evidence.** `quote.get("rsi") or 50.0`
  turns an absent RSI into a 50 and awards it +25 for sitting "in the bullish
  zone"; `macd or 0.0` makes `macd > macd_signal` false for every stock, so
  every stock was reported "MACD bearish"; `avg_volume or 0` put RELIANCE — 8.3
  million shares traded that morning — in the "Very low liquidity" bucket.
  Measured live, five of the eight dimensions were byte-identical across all ten
  ranked stocks.

So the product's "why this stock" evidence was a constant, and its scanner
strategies were inert. Both are one root cause: the universe path had no
history.

WHAT THESE TESTS PIN
--------------------
That the universe path computes the fields, that it computes them *from the
data* (swap the series, get a different answer — the mutation a hardcoded
fixture cannot survive), and that when history is genuinely too short the
fields are **None rather than a plausible default**. The last one is the point:
a fabricated 50 is worse than a null, because a null is visibly missing and a
50 is silently wrong.
"""
import asyncio

import pytest

from services import real_market


def _run(coro):
    return asyncio.run(coro)


def _series(n, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def _quote(closes, volumes=None):
    """A raw Yahoo quote as `fetch_yahoo_quote` returns it."""
    n = len(closes)
    return {
        "price": closes[-1],
        "prev_close": closes[-2] if n >= 2 else closes[-1],
        "change": 0.0,
        "change_pct": 0.0,
        "open": closes[-1],
        "high": closes[-1] + 1,
        "low": closes[-1] - 1,
        "volume": (volumes or [1000] * n)[-1],
        "market_state": "REGULAR",
        "exchange": "NSE",
        "currency": "INR",
        "historical_closes": list(closes),
        "historical_volumes": list(volumes or [1000] * n),
        "historical_highs": [c + 1 for c in closes],
        "historical_lows": [c - 1 for c in closes],
        "historical_opens": list(closes),
        "historical_timestamps": list(range(n)),
        "historical_close_timestamps": list(range(n)),
    }


TECHNICAL_FIELDS = ("rsi", "macd", "macd_signal", "avg_volume", "volume_ratio")


# --------------------------------------------------------------------------- #
# The derivation itself                                                        #
# --------------------------------------------------------------------------- #

def test_technicals_are_computed_when_history_is_sufficient():
    fields = real_market.derive_technicals(_quote(_series(70)))

    for name in TECHNICAL_FIELDS:
        assert fields[name] is not None, f"{name} should be derivable from 70 bars"


def test_technicals_are_none_when_history_is_too_short():
    """Absence is reported, not defaulted.

    Two bars cannot produce a 14-period RSI or a 26-period MACD. The honest
    answer is "unknown"; the answer this replaces was 50.0, 0.0 and a literal
    `avg_volume = 1000000`, all of which score as real values downstream.
    """
    fields = real_market.derive_technicals(_quote(_series(2)))

    for name in TECHNICAL_FIELDS:
        assert fields[name] is None, f"{name} should be None with 2 bars, got {fields[name]!r}"


def test_technicals_follow_the_data():
    """Swap the series, get a different answer.

    The mutation a hardcoded fixture cannot survive: a rising series and a
    falling series must not produce the same RSI.
    """
    rising = real_market.derive_technicals(_quote(_series(70, step=1.0)))
    falling = real_market.derive_technicals(_quote(_series(70, start=170.0, step=-1.0)))

    assert rising["rsi"] != falling["rsi"]
    assert rising["rsi"] > falling["rsi"]


def test_volume_ratio_is_todays_volume_against_the_average():
    volumes = [1_000_000] * 69 + [3_000_000]
    fields = real_market.derive_technicals(_quote(_series(70), volumes))

    assert fields["avg_volume"] == pytest.approx(1_000_000, rel=0.01)
    assert fields["volume_ratio"] == pytest.approx(3.0, rel=0.01)


# --------------------------------------------------------------------------- #
# The universe path                                                            #
# --------------------------------------------------------------------------- #

@pytest.fixture
def universe(monkeypatch):
    """Drive `fetch_all_universe_quotes` over a scripted two-symbol universe."""
    state = {"closes": {}, "ranges": []}

    monkeypatch.setattr(
        real_market, "STOCK_UNIVERSE", None, raising=False
    )
    import market_data

    monkeypatch.setattr(
        market_data,
        "STOCK_UNIVERSE",
        [
            {"symbol": "AAA", "name": "Alpha", "sector": "IT"},
            {"symbol": "BBB", "name": "Beta", "sector": "Banking"},
        ],
    )

    async def _cache_get(_k):
        return None

    async def _cache_get_many(_keys):
        return {}

    async def _cache_set(*_a, **_k):
        return None

    monkeypatch.setattr(real_market, "cache_get", _cache_get)
    monkeypatch.setattr(real_market, "cache_get_many", _cache_get_many)
    monkeypatch.setattr(real_market, "cache_set", _cache_set)

    async def _fetch(symbol, range_str="2d"):
        state["ranges"].append(range_str)
        closes = state["closes"].get(symbol)
        return _quote(closes) if closes else None

    monkeypatch.setattr(real_market, "fetch_yahoo_quote", _fetch)
    return state


def test_universe_quotes_carry_technicals(universe):
    universe["closes"] = {"AAA": _series(70), "BBB": _series(70, start=200.0)}

    quotes = _run(real_market.fetch_all_universe_quotes())

    assert len(quotes) == 2
    for q in quotes:
        for name in TECHNICAL_FIELDS:
            assert q.get(name) is not None, f"{q['symbol']}.{name} is None"


def test_universe_requests_enough_history_to_compute_them(universe):
    """The 2-bar window is the defect; this pins that it is gone.

    Asserting on the requested range rather than only on the output means a
    "fix" that fabricates plausible technicals from two bars fails here.
    """
    universe["closes"] = {"AAA": _series(70), "BBB": _series(70)}

    _run(real_market.fetch_all_universe_quotes())

    assert universe["ranges"], "no fetch was made"
    assert "2d" not in universe["ranges"], (
        f"universe still fetches a 2-bar window: {set(universe['ranges'])}"
    )


def test_universe_technicals_differ_between_stocks(universe):
    """Two stocks with different histories must not score identically.

    This is the assertion that would have caught the live defect: before the
    fix every universe quote carried the same five nulls, and every stock
    therefore scored the same on five of eight dimensions.
    """
    universe["closes"] = {
        "AAA": _series(70, step=1.0),
        "BBB": _series(70, start=170.0, step=-1.0),
    }

    quotes = {q["symbol"]: q for q in _run(real_market.fetch_all_universe_quotes())}

    assert quotes["AAA"]["rsi"] != quotes["BBB"]["rsi"]
    assert quotes["AAA"]["macd"] != quotes["BBB"]["macd"]


def test_a_symbol_with_no_live_quote_is_omitted_never_substituted(universe):
    """The pre-existing contract, re-pinned because this sprint touched the loop."""
    universe["closes"] = {"AAA": _series(70)}  # BBB returns None

    quotes = _run(real_market.fetch_all_universe_quotes())

    assert [q["symbol"] for q in quotes] == ["AAA"]


def test_universe_day_change_is_still_the_days_change(universe):
    """The range widened; the day's change must not widen with it.

    This is the coupling between this sprint's two market-data changes: moving
    the universe to a longer window would have silently turned its day change
    into a three-month change, had the `prev_close` derivation not been fixed
    first (see test_day_change_is_the_days_change.py).
    """
    closes = _series(69) + [200.0]
    universe["closes"] = {"AAA": closes, "BBB": closes}

    quotes = _run(real_market.fetch_all_universe_quotes())

    assert quotes[0]["prev_close"] == closes[-2]

"""A scanner pick must say what it matched on (D5.19, D-5).

WHY THE SCANNER LOOKED LIKE A FIXTURE
-------------------------------------
The brief reports the Scanner showing "test/demo/static selections". It was
neither hardcoded nor demo data — it scanned the real universe through the
gateway — but its output was indistinguishable from a fixture, because the
universe path carried no technical indicators at all (see
`test_universe_technicals.py`). Measured live on 2026-09-01:

  * `intraday`, `swing`, `momentum`, `breakout`, `reversal`, `growth` — six of
    eight presets — matched **0 of 31** stocks, because they filter on
    `volume_ratio_min`, `rsi_min/max` or `macd_bullish` and every one of those
    was `None`.
  * `value` and `dividend` sort on `rsi` and `volume_ratio`; `q.get(key) or 0`
    made all 31 stocks equal, the sort is stable, and so they returned the
    universe in *declaration order* — RELIANCE, TCS, HDFCBANK, INFY — every
    time, for every market condition. A list that never changes is a fixture as
    far as a user can tell, and that is what was being seen.

With the indicators supplied, the presets discriminate again. This file covers
the second half of the requirement: that a pick can say *why* it is a pick.

WHY `matched_on` IS BUILT ON THE SERVER
---------------------------------------
The same rule the ranking evidence follows. A scanner pick's reason is the
filter it satisfied, and the filters are the scanner's own — reconstructing
them in the browser would put the criteria in two places and let them drift the
first time a preset changed. It is also the only side that can honestly say a
filter was *skipped*: `_passes_filters` ignores an RSI bound when `rsi` is
None, so a stock can appear in an RSI-filtered scan without an RSI, and a
frontend rendering "RSI 50" from a default would be inventing the very thing
that made this sprint necessary.
"""
import asyncio

import pytest

from services.market_engine import scanner_engine


def _run(coro):
    return asyncio.run(coro)


def _quote(symbol, **over):
    base = {
        "symbol": symbol, "name": symbol, "price": 100.0, "change_pct": 1.0,
        "sector": "IT", "rsi": 55.0, "macd": 3.0, "macd_signal": 1.0,
        "avg_volume": 5_000_000, "volume_ratio": 1.6, "source_tier": "delayed",
    }
    base.update(over)
    return base


class _Gateway:
    def __init__(self, quotes):
        self._quotes = quotes

    async def get_universe_quotes(self, *, user_id=None):
        return [dict(q) for q in self._quotes]

    def source_tier(self, _c=None, *, user_id=None):
        return "delayed"


@pytest.fixture
def gateway(monkeypatch):
    def _install(quotes):
        import services.market_engine.gateway as gm
        monkeypatch.setattr(gm, "market_gateway", _Gateway(quotes))
    return _install


def _scan(**kw):
    return _run(scanner_engine.scan(publish=False, **kw))


def _first(result):
    return result["results"][0]


# --------------------------------------------------------------------------- #
# The evidence                                                                 #
# --------------------------------------------------------------------------- #

def test_a_pick_states_the_filters_it_satisfied(gateway):
    gateway([_quote("AAA")])

    pick = _first(_scan(strategy="intraday"))

    assert pick["matched_on"], "a filtered pick must say what it matched"


def test_the_evidence_names_the_actual_value_and_the_threshold(gateway):
    gateway([_quote("AAA", volume_ratio=2.4)])

    pick = _first(_scan(filters={"volume_ratio_min": 1.3}))

    text = " ".join(pick["matched_on"])
    assert "2.4" in text
    assert "1.3" in text


def test_evidence_follows_the_data(gateway):
    """The mutation a hardcoded reason cannot survive."""
    gateway([_quote("AAA", rsi=61.0)])
    high = _first(_scan(filters={"rsi_min": 40, "rsi_max": 70}))["matched_on"]

    gateway([_quote("AAA", rsi=44.0)])
    low = _first(_scan(filters={"rsi_min": 40, "rsi_max": 70}))["matched_on"]

    assert high != low


def test_an_unfiltered_scan_claims_no_criteria(gateway):
    """With no filters there is nothing to have matched, and saying otherwise
    would be inventing a reason for a stock that is simply in the universe."""
    gateway([_quote("AAA")])

    assert _first(_scan())["matched_on"] == []


def test_a_filter_the_stock_has_no_data_for_is_not_claimed(gateway):
    """The honesty case.

    `_passes_filters` skips an RSI bound when `rsi` is None, so a stock with no
    RSI passes an RSI-filtered scan. It must not then be told it matched on an
    RSI it does not have — that is the fabricated-evidence failure the ranking
    engine had, in the scanner's own shape.
    """
    gateway([_quote("AAA", rsi=None)])

    result = _scan(filters={"rsi_min": 40, "rsi_max": 70})

    assert result["results"], "a stock with no RSI still passes an RSI filter"
    assert not any("RSI" in reason for reason in _first(result)["matched_on"])


def test_the_sector_filter_is_not_evidence(gateway):
    """A sector is the scope of the scan, not a reason a stock was selected."""
    gateway([_quote("AAA", sector="IT")])

    pick = _first(_scan(sector="IT"))

    assert not any("sector" in r.lower() for r in pick["matched_on"])


def test_macd_evidence_is_only_claimed_when_both_legs_are_known(gateway):
    gateway([_quote("AAA", macd=None, macd_signal=None)])

    result = _scan(filters={"macd_bullish": True})

    if result["results"]:
        assert not any("MACD" in r for r in _first(result)["matched_on"])


def test_scanning_still_selects_on_the_data(gateway):
    """The filters must actually filter — the defect that produced a constant list."""
    gateway([
        _quote("HIGH", volume_ratio=2.5),
        _quote("LOW", volume_ratio=0.2),
    ])

    result = _scan(filters={"volume_ratio_min": 1.3})

    assert [r["symbol"] for r in result["results"]] == ["HIGH"]


def test_replacing_the_market_data_changes_the_selection(gateway):
    """Mutation: a hardcoded pick list cannot survive a different market."""
    gateway([_quote("AAA", change_pct=5.0), _quote("BBB", change_pct=-5.0)])
    first = [r["symbol"] for r in _scan(filters={"change_pct_min": 1.0})["results"]]

    gateway([_quote("AAA", change_pct=-5.0), _quote("BBB", change_pct=5.0)])
    second = [r["symbol"] for r in _scan(filters={"change_pct_min": 1.0})["results"]]

    assert first == ["AAA"]
    assert second == ["BBB"]

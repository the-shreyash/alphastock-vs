""""Why is this a top opportunity?" must be answered from the actual score (D5.19).

THE TRAP THIS FILE EXISTS TO CLOSE
-----------------------------------
The brief asks for an explanation beside every opportunity, built from "actual
scoring evidence" and explicitly not from invented reasons. The ranking engine
already emits a `reason` string per dimension, so the obvious implementation is
to pipe those to the browser.

That would have shipped fabricated evidence. Measured live on 2026-09-01, every
universe quote reached the scorer with `rsi=None, macd=None, macd_signal=None,
avg_volume=None`, and the scorers coalesce (`quote.get("rsi") or 50.0`). So the
engine was reporting, for RELIANCE — 8.3 million shares traded that morning:

    momentum   95.0  "RSI 50 in bullish zone; Strong +2.6% day move"
    trend      40.0  "MACD bearish"
    liquidity  25.0  "Very low liquidity"

Only the "+2.6% day move" half of the first line came from the market. The RSI
of 50 was `or 50.0`, "MACD bearish" was `None or 0.0 > None or 0.0`, and "Very
low liquidity" was `avg_volume or 0` bucketing a mega-cap at the bottom. Five of
the eight dimensions were byte-identical across all ten ranked stocks.

`derive_technicals` now supplies the real inputs, which fixes today. It does not
fix the *class*: a newly listed stock has no 26-bar MACD, a suspended one has no
volume, and on those the coalescing returns and the engine starts narrating
defaults again — silently, because a fabricated reason is indistinguishable from
a real one once it is a string.

So the explanation is built on an explicit **availability** answer per dimension
rather than on the presence of a reason string, and the tests below drive the
scorers with absent inputs on purpose. That is the case that must stay honest.
"""
import asyncio

import pytest

from services.market_engine import ranking_engine


def _run(coro):
    return asyncio.run(coro)


def _quote(**over):
    """A quote with every scored input present. Override to remove one."""
    base = {
        "symbol": "TESTSYM",
        "name": "Test",
        "price": 100.0,
        "change_pct": 2.5,
        "sector": "IT",
        "rsi": 55.0,
        "macd": 5.0,
        "macd_signal": 2.0,
        "avg_volume": 8_000_000,
        "volume_ratio": 2.1,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# Availability                                                                 #
# --------------------------------------------------------------------------- #

def test_every_dimension_is_available_when_every_input_is_present():
    # `news_sentiment` is passed explicitly: the default 0.5 is the value the
    # caller supplies when it has no sentiment at all, and the engine correctly
    # reports that as unavailable rather than as a neutral reading.
    ranked = ranking_engine.rank_stock(
        _quote(), news_sentiment=0.8, sector_rank=1, sector_change=1.5
    )

    unavailable = [d for d, v in ranked["dimensions"].items() if not v["available"]]
    assert unavailable == []


@pytest.mark.parametrize(
    "missing, dimension",
    [
        ({"rsi": None}, "momentum"),
        ({"macd": None, "macd_signal": None}, "trend"),
        ({"volume_ratio": None}, "volume"),
        ({"avg_volume": None}, "liquidity"),
    ],
)
def test_a_dimension_whose_inputs_are_absent_is_marked_unavailable(missing, dimension):
    ranked = ranking_engine.rank_stock(_quote(**missing))

    assert ranked["dimensions"][dimension]["available"] is False


def test_an_unavailable_dimension_states_no_reason():
    """The fabricated string is removed, not merely flagged.

    "MACD bearish" for a stock with no MACD is the exact sentence this sprint
    found in production, and a flag beside it would still leave it renderable.
    """
    ranked = ranking_engine.rank_stock(_quote(macd=None, macd_signal=None))

    assert ranked["dimensions"]["trend"]["reason"] is None


def test_sector_is_unavailable_when_the_stock_has_no_sector_context():
    """`rank_stock` is called with no sector rank for an unclassified stock."""
    ranked = ranking_engine.rank_stock(_quote(), sector_rank=None, sector_change=None)

    assert ranked["dimensions"]["sector"]["available"] is False


# --------------------------------------------------------------------------- #
# The explanation                                                              #
# --------------------------------------------------------------------------- #

def test_evidence_is_present_for_a_fully_scored_stock():
    ranked = ranking_engine.rank_stock(_quote(), sector_rank=0, sector_change=2.0)

    assert ranked["evidence"], "a fully scored stock must be explainable"
    for item in ranked["evidence"]:
        assert item["dimension"]
        assert item["reason"]
        assert item["score"] is not None


def test_evidence_never_cites_an_unavailable_dimension():
    """The whole point. An absent MACD must not appear as a reason to buy."""
    ranked = ranking_engine.rank_stock(
        _quote(macd=None, macd_signal=None, avg_volume=None),
        sector_rank=0,
        sector_change=2.0,
    )

    cited = {item["dimension"] for item in ranked["evidence"]}
    assert "trend" not in cited
    assert "liquidity" not in cited


def test_evidence_text_is_the_scorers_own_reason():
    """Falsification: the explanation may not be authored anywhere else.

    Every evidence string must be findable in the dimension it claims to come
    from. A generated summary, an AI paraphrase or a template would fail this,
    which is the point — the brief forbids explanations the scoring engine did
    not produce.
    """
    ranked = ranking_engine.rank_stock(_quote(), sector_rank=0, sector_change=2.0)

    for item in ranked["evidence"]:
        assert item["reason"] == ranked["dimensions"][item["dimension"]]["reason"]


def test_evidence_is_ordered_by_actual_contribution():
    """The first line must be the factor that most moved the score.

    Contribution is the dimension's weighted distance from neutral, so a
    heavily weighted dimension sitting at 50 ranks below a lightly weighted one
    at 95 — which is what makes this an explanation rather than a field dump.
    """
    ranked = ranking_engine.rank_stock(_quote(), sector_rank=0, sector_change=2.0)

    contributions = [item["contribution"] for item in ranked["evidence"]]
    assert contributions == sorted(contributions, key=abs, reverse=True)


def test_a_dimension_sitting_at_neutral_is_not_evidence():
    """50/100 explains nothing and must not pad the list."""
    ranked = ranking_engine.rank_stock(
        _quote(rsi=None, macd=None, macd_signal=None, volume_ratio=None, avg_volume=None),
        sector_rank=None,
        sector_change=None,
    )

    for item in ranked["evidence"]:
        assert item["score"] != 50.0


def test_an_available_dimension_at_exactly_neutral_is_not_evidence():
    """The case the test above could not reach, and a mutation found.

    Falsification M9 removed both the neutral-score guard and the
    zero-contribution guard, and the suite stayed green — because every quote
    it drove into the neutral case had *absent* inputs, so the availability
    filter removed those dimensions before the neutral rule was ever consulted.
    A rule that only ever fires behind another rule is untested.

    `RSI 80` (overbought, -10) with `+0.6%` (positive, +10) lands momentum on
    exactly 50.0 with both inputs present and a real, non-empty reason — a
    dimension that is available, articulate, and contributed nothing. It says
    "RSI 80 overbought" and "Positive +0.6% today" in one breath, which is
    precisely the sort of line that reads as insight and is not one.

    The rest of the quote is deliberately sparse. A fully-populated one hid the
    bug a second time: with three strongly-contributing dimensions present,
    `MAX_EVIDENCE_ITEMS` truncated the zero-contribution rows away regardless
    of the guard, so the assertion passed for a reason that had nothing to do
    with what it claimed to test. Here only `risk` contributes, leaving room in
    the list for the neutral rows to appear if the guard is removed — which is
    what makes this falsifiable rather than merely true.
    """
    ranked = ranking_engine.rank_stock(
        _quote(rsi=80.0, change_pct=0.6, macd=None, macd_signal=None,
               volume_ratio=None, avg_volume=None),
        sector_rank=None,
        sector_change=None,
    )

    momentum = ranked["dimensions"]["momentum"]
    assert momentum["available"] is True
    assert momentum["score"] == 50.0
    assert momentum["reason"]

    cited = {item["dimension"] for item in ranked["evidence"]}
    assert "momentum" not in cited
    # `ai_confidence` is the same case reached by a different route: available,
    # articulate ("Neutral technical setup"), and exactly neutral.
    assert "ai_confidence" not in cited
    assert cited == {"risk"}


def test_an_empty_reason_is_never_evidence():
    """`score_volume` returns "" for an unremarkable ratio — a real case.

    A blank bullet under "why this stock" is worse than one fewer bullet.
    """
    ranked = ranking_engine.rank_stock(_quote(volume_ratio=0.8))

    assert all(item["reason"].strip() for item in ranked["evidence"])


def test_evidence_changes_when_the_market_data_changes():
    """The mutation a hardcoded explanation cannot survive."""
    strong = ranking_engine.rank_stock(_quote(rsi=58.0, change_pct=3.0))
    weak = ranking_engine.rank_stock(_quote(rsi=82.0, change_pct=-3.0))

    assert strong["evidence"] != weak["evidence"]


def test_a_stock_with_no_scorable_input_is_honestly_unexplainable():
    """No evidence is a valid answer. An empty list is not a failure state.

    The alternative — always producing at least one bullet — is what forces an
    engine to invent, and it is how "Very low liquidity" got printed under a
    mega-cap.
    """
    ranked = ranking_engine.rank_stock(
        {"symbol": "NEWLISTING", "name": "New", "price": 10.0, "change_pct": None,
         "rsi": None, "macd": None, "macd_signal": None,
         "avg_volume": None, "volume_ratio": None},
        sector_rank=None,
        sector_change=None,
    )

    assert ranked["evidence"] == []


# --------------------------------------------------------------------------- #
# The score itself is unchanged                                                #
# --------------------------------------------------------------------------- #

def test_adding_evidence_did_not_change_what_the_engine_recommends():
    """The brief preserves the scoring logic; this pins that it was preserved.

    `opportunity_score` and `signal` are computed from the same weights over
    the same dimension scores as before. Only the explanation is new.
    """
    ranked = ranking_engine.rank_stock(_quote(), sector_rank=0, sector_change=2.0)

    expected = sum(
        ranked["dimensions"][dim]["score"] * weight
        for dim, weight in ranking_engine.DIMENSION_WEIGHTS.items()
    )
    assert ranked["opportunity_score"] == pytest.approx(round(min(100.0, max(0.0, expected)), 1))
    assert ranked["signal"] in {"strong_buy", "buy", "neutral", "sell", "strong_sell"}

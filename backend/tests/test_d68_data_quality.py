"""D6.8-A — Data Quality / Intelligence Isolation (D6-Q1's six-state model).

WHAT THIS FILE IS DEFENDING
───────────────────────────
One rule, stated six ways:

    **The platform may not describe a number as a measurement of the market
    unless it measured it.**

ADR-058 (D5.19) established that rule for the ranking engine's evidence strings
and said, in its own text, that it had fixed the instance and not the class. The
class is here: a `FieldQuality` carried from the producer — which alone knows
whether a window was short, a vendor raised, or a payload simply omitted a value
— to the consumer, which alone renders it.

WHAT EACH SECTION BELOW EXISTS TO CATCH
───────────────────────────────────────
* **Enum** — the six states, with the exact names D6-Q1 specifies, and a closed
  vocabulary that refuses everything else.
* **Producer** — every one of the six is actually REACHED by a real input. An
  enum member no code path produces is documentation, not a control.
* **Stale** — the state that did not exist before and is the most dangerous of
  the six, because a stale reading is a real number and therefore scored as
  fresh. Derived on read, never persisted, never invented from a missing clock.
* **Gateway** — the map survives normalization on every provider family, with
  an identical payload shape, carrying no provider identity.
* **Ranking** — the scores are numerically UNCHANGED (D6-Q1: "Scores stay
  unchanged; only what the platform claims changes") while the claims are gated.
* **Scanner** — eligibility is explicit: a filter can never be satisfied by a
  substitute, and a stock the platform cannot rank is not ranked first.
* **AI** — no model is handed a number the platform does not have, and the
  context can finally say "as of 10:42 IST".
* **Surfaces** — advisor, top picks and the full report, which are the three
  D5.19 did not reach (§G-6).
* **Isolation** — the standing rule from `test_d519_surface_disclosure.py`: a
  generated string is how internal state escapes, because a leak here does not
  look like a leak, it looks like a sentence.
"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from services import real_market
from services.market_engine import field_quality, ranking_engine, scanner_engine
from services.market_engine.field_quality import (
    DAILY_READING_MAX_AGE_SECONDS,
    OBSERVED_AT_KEY,
    QUALITY_KEY,
    QUALITY_TRACKED_FIELDS,
    FieldQuality,
)
from services.market_engine.normalizer import normalize_stock_quote


def _run(coro):
    return asyncio.run(coro)


NOW = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _bars(n, *, start=100.0, volume=1000, last_bar=NOW):
    """A quote payload carrying `n` daily bars ending at `last_bar`."""
    return {
        "historical_closes": [start + i for i in range(n)],
        "historical_volumes": [volume + i for i in range(n)],
        "historical_close_timestamps": [(last_bar - timedelta(days=n - 1 - i)).timestamp() for i in range(n)],
        "volume": volume + n,
    }


# --------------------------------------------------------------------------- #
# The enum                                                                     #
# --------------------------------------------------------------------------- #


def test_the_six_states_exist_under_the_names_the_brief_specifies():
    """The names are the contract. D6-Q1 in `.claude/TASK.md` §G names these six
    exactly, and a renamed member is a different contract wearing the same
    docstring."""
    assert {q.name for q in FieldQuality} == {
        "AVAILABLE",
        "MISSING",
        "INSUFFICIENT_HISTORY",
        "STALE",
        "PROVIDER_ERROR",
        "UNAVAILABLE",
    }


def test_there_is_no_seventh_state():
    """A state nobody derived from the architecture is a state nobody can act
    on. The brief is explicit: stop and report rather than invent one."""
    assert len(FieldQuality) == 6


@pytest.mark.parametrize(
    "bogus",
    [
        "AVAILABLE",
        "Available",
        "fresh",
        "ok",
        "",
        None,
        0,
        1,
        True,
        {"nested": "object"},
        ["available"],
        "provider_error ",
    ],
)
def test_an_invalid_state_is_refused_rather_than_carried(bogus):
    """`sanitize` is the only constructor, and it admits nothing else.

    Note `"AVAILABLE"` is in this list: the enum's *values* are lowercase, so the
    uppercase name is not a valid wire value, and accepting it would mean two
    spellings of one state reaching consumers that compare strings.
    """
    assert field_quality.sanitize({"rsi": bogus}) == {}


def test_an_unknown_field_name_is_refused():
    """The map's keys are a closed set too.

    Every rejected key here carries a VALID state as its value. That is the
    whole point: with an invalid value the value check alone would drop the
    entry, and the key check would be untested — a control passing on somebody
    else's evidence. `"served_by": "available"` is refused for its key and
    nothing else.
    """
    assert field_quality.sanitize(
        {
            "rsi": "available",
            "provider": "available",
            "served_by": "stale",
            "owner_user_id": "missing",
            "broker_account_id": "available",
        }
    ) == {"rsi": "available"}


def test_a_key_that_is_not_a_tracked_field_is_refused_even_with_a_valid_state():
    """Stated once more on its own, because the assertion above bundles it with
    the happy path and a bundled assertion is easy to satisfy by accident."""
    assert field_quality.sanitize({"price": "available"}) == {}
    assert field_quality.sanitize({"symbol": "available"}) == {}


def test_the_tracked_fields_are_the_ones_that_become_sentences():
    assert set(QUALITY_TRACKED_FIELDS) == {
        "rsi",
        "macd",
        "macd_signal",
        "avg_volume",
        "volume_ratio",
        "change_pct",
    }


# --------------------------------------------------------------------------- #
# The producer — every state is REACHED                                        #
# --------------------------------------------------------------------------- #


def test_sufficient_history_is_available():
    quote = {**_bars(40), **real_market.derive_technicals(_bars(40))}

    for name in ("rsi", "macd", "macd_signal", "avg_volume", "volume_ratio"):
        assert quote[name] is not None, name
        assert field_quality.quality_of(quote, name, now=NOW) is FieldQuality.AVAILABLE, name


def test_insufficient_rsi_history_says_so():
    """14 bars: enough for a volume baseline is 21 and for RSI is 15, so this
    payload separates the two windows rather than failing everything at once."""
    raw = _bars(14)
    quote = {**raw, **real_market.derive_technicals(raw)}

    assert quote["rsi"] is None
    assert field_quality.quality_of(quote, "rsi") is FieldQuality.INSUFFICIENT_HISTORY


def test_insufficient_macd_history_says_so():
    """20 bars is enough for RSI and not for MACD's 26-bar slow EMA. The
    assertion is only meaningful next to the RSI half: a producer that marked
    everything INSUFFICIENT_HISTORY would pass a MACD-only test."""
    raw = _bars(20)
    quote = {**raw, **real_market.derive_technicals(raw)}

    assert field_quality.quality_of(quote, "rsi", now=NOW) is FieldQuality.AVAILABLE
    assert quote["macd"] is None and quote["macd_signal"] is None
    assert field_quality.quality_of(quote, "macd") is FieldQuality.INSUFFICIENT_HISTORY
    assert field_quality.quality_of(quote, "macd_signal") is FieldQuality.INSUFFICIENT_HISTORY


def test_insufficient_volume_history_says_so():
    raw = _bars(30)
    raw["historical_volumes"] = raw["historical_volumes"][:10]
    quote = {**raw, **real_market.derive_technicals(raw)}

    assert field_quality.quality_of(quote, "macd", now=NOW) is FieldQuality.AVAILABLE
    assert quote["avg_volume"] is None
    assert field_quality.quality_of(quote, "avg_volume") is FieldQuality.INSUFFICIENT_HISTORY
    # The ratio inherits its baseline's reason rather than inventing its own.
    assert field_quality.quality_of(quote, "volume_ratio") is FieldQuality.INSUFFICIENT_HISTORY


def test_a_vendor_series_that_cannot_be_computed_is_a_provider_error():
    """A series the vendor sent in an unusable shape.

    Before D6.8-A this raised out of `derive_technicals`, out of
    `fetch_all_universe_quotes`'s gather, and the symbol vanished from the
    universe — a provider fault presenting as a smaller market.
    """
    raw = _bars(40)
    raw["historical_closes"] = ["n/a"] * 40
    quote = {**raw, **real_market.derive_technicals(raw)}

    assert field_quality.quality_of(quote, "rsi") is FieldQuality.PROVIDER_ERROR
    assert field_quality.quality_of(quote, "macd") is FieldQuality.PROVIDER_ERROR
    # The rest of the quote survives: the volume series was fine.
    assert field_quality.quality_of(quote, "avg_volume", now=NOW) is FieldQuality.AVAILABLE


def test_a_provider_error_is_not_reported_as_missing():
    """The G-3 assertion, stated as the thing it must NOT be.

    A degraded vendor and a quiet market produced the identical outcome before
    this phase, which is how a data-supply incident became invisible.
    """
    raw = _bars(40)
    raw["historical_closes"] = ["n/a"] * 40
    quote = {**raw, **real_market.derive_technicals(raw)}

    assert field_quality.quality_of(quote, "rsi") is not FieldQuality.MISSING
    assert field_quality.quality_of(quote, "rsi") is not FieldQuality.INSUFFICIENT_HISTORY


def test_a_genuinely_absent_field_is_missing():
    """The bar series is there and the session volume is not.

    `volume_ratio` has two inputs and therefore two ways to be absent, and they
    are not the same absence: the baseline was computed, so the reason is the
    missing session volume and nothing else. This is also the branch that used
    to produce a real-looking **0.0** from `quote.get("volume") or 0`.
    """
    raw = _bars(40)
    raw.pop("volume")
    quote = {**raw, **real_market.derive_technicals(raw)}

    assert field_quality.quality_of(quote, "avg_volume", now=NOW) is FieldQuality.AVAILABLE
    assert quote["volume_ratio"] is None
    assert quote["volume_ratio"] != 0.0
    assert field_quality.quality_of(quote, "volume_ratio") is FieldQuality.MISSING


def test_no_series_at_all_is_unavailable_not_insufficient_history():
    """The MISSING/UNAVAILABLE distinction at the producer.

    Zero bars does not mean "this stock is too young for a 26-bar MACD"; it
    means the record these indicators derive from was never served. Calling it
    INSUFFICIENT_HISTORY would tell a newly-listed-stock story about a feed that
    returned nothing.
    """
    technicals = real_market.derive_technicals({})

    for name in ("rsi", "macd", "macd_signal", "avg_volume", "volume_ratio"):
        assert technicals[QUALITY_KEY][name] == FieldQuality.UNAVAILABLE.value, name


def test_missing_and_unavailable_stay_distinct():
    """Both are reachable, from different inputs, in one assertion."""
    served_without_the_field = _bars(40)
    served_without_the_field.pop("volume")
    served = {
        **served_without_the_field,
        **real_market.derive_technicals(served_without_the_field),
    }
    never_served = real_market.derive_technicals({})

    assert field_quality.quality_of(served, "volume_ratio") is FieldQuality.MISSING
    assert never_served[QUALITY_KEY]["volume_ratio"] == FieldQuality.UNAVAILABLE.value


def test_an_undeclared_null_is_missing_which_is_the_safest_absence():
    """MISSING is byte-for-byte the meaning `value is None` already carried, so
    a path that has not been taught to classify cannot make a stronger claim
    than the old code made."""
    assert field_quality.quality_of({"rsi": None}, "rsi") is FieldQuality.MISSING
    assert field_quality.quality_of({}, "rsi") is FieldQuality.MISSING


def test_a_declared_reason_is_not_overruled_by_the_value_being_present():
    """A producer that says PROVIDER_ERROR wins over an inference from a value.

    Without this, any carrier that happened to leave a stale number in the field
    would silently upgrade the state back to AVAILABLE.
    """
    quote = {"rsi": 55.0, QUALITY_KEY: {"rsi": "provider_error"}}

    assert field_quality.quality_of(quote, "rsi") is FieldQuality.PROVIDER_ERROR
    assert field_quality.reading(quote, "rsi") is None


def test_the_day_change_is_no_longer_fabricated_as_flat():
    """G-9's sixth substituted default, in `fetch_yahoo_quote` rather than in
    `fetch_real_stock_quote`. See `test_day_change_is_the_days_change.py`."""
    assert (
        field_quality.quality_of({"change_pct": None, QUALITY_KEY: {"change_pct": "missing"}}, "change_pct")
        is FieldQuality.MISSING
    )


# --------------------------------------------------------------------------- #
# Stale                                                                        #
# --------------------------------------------------------------------------- #


def test_a_fresh_reading_is_not_stale():
    quote = {"rsi": 55.0, OBSERVED_AT_KEY: _iso(NOW - timedelta(hours=6))}

    assert field_quality.quality_of(quote, "rsi", now=NOW) is FieldQuality.AVAILABLE


def test_a_reading_beyond_the_threshold_is_stale():
    quote = {
        "rsi": 55.0,
        OBSERVED_AT_KEY: _iso(NOW - timedelta(seconds=DAILY_READING_MAX_AGE_SECONDS + 60)),
    }

    assert field_quality.quality_of(quote, "rsi", now=NOW) is FieldQuality.STALE


def test_the_threshold_is_a_boundary_and_not_a_range():
    """Exactly at the window is still current; one second past it is not.

    Pins the comparison as `>` rather than `>=`, and — more usefully — makes a
    mutation that widens the window by an order of magnitude fail.
    """
    at = {"rsi": 55.0, OBSERVED_AT_KEY: _iso(NOW - timedelta(seconds=DAILY_READING_MAX_AGE_SECONDS))}
    past = {"rsi": 55.0, OBSERVED_AT_KEY: _iso(NOW - timedelta(seconds=DAILY_READING_MAX_AGE_SECONDS + 1))}

    assert field_quality.quality_of(at, "rsi", now=NOW) is FieldQuality.AVAILABLE
    assert field_quality.quality_of(past, "rsi", now=NOW) is FieldQuality.STALE


def test_a_future_timestamp_does_not_become_stale():
    """A clock that disagrees is a clock problem. Refusing good data on the
    strength of a skew nobody measured would be a fabrication in the other
    direction."""
    quote = {"rsi": 55.0, OBSERVED_AT_KEY: _iso(NOW + timedelta(days=30))}

    assert field_quality.quality_of(quote, "rsi", now=NOW) is FieldQuality.AVAILABLE
    assert field_quality.observation_age_seconds(quote, now=NOW) < 0


def test_staleness_is_derived_from_the_observation_time_not_the_read_time():
    """One payload, two clocks, two answers — and the payload is not touched.

    This is what "derive age on read" means operationally, and it is what makes
    the classification correct for a value served from a cache: the age belongs
    to the reading, not to the request.
    """
    quote = {"rsi": 55.0, OBSERVED_AT_KEY: _iso(NOW)}
    before = json.dumps(quote, sort_keys=True)

    assert field_quality.quality_of(quote, "rsi", now=NOW + timedelta(hours=1)) is FieldQuality.AVAILABLE
    assert field_quality.quality_of(quote, "rsi", now=NOW + timedelta(days=3)) is FieldQuality.STALE
    assert json.dumps(quote, sort_keys=True) == before, "aging must not mutate the payload"


def test_a_payload_with_no_observation_instant_is_never_called_stale():
    """No timestamp is invented for it — `ai_provenance`'s rule 3 in this
    module's shape. An unknown age is reported as unknown."""
    quote = {"rsi": 55.0}

    assert field_quality.observation_age_seconds(quote, now=NOW) is None
    assert field_quality.quality_of(quote, "rsi", now=NOW) is FieldQuality.AVAILABLE


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-date",
        "",
        None,
        "2026-09-16T06:00:00",
        datetime(2026, 9, 16, 6, 0),
    ],
)
def test_an_unparseable_or_naive_instant_yields_no_age(bad):
    """A naive datetime read as UTC is a guess, and a guessed age is how a stale
    reading would be certified fresh."""
    assert field_quality.observation_age_seconds({OBSERVED_AT_KEY: bad}, now=NOW) is None


def test_the_producer_supplies_a_real_observation_instant_not_now():
    """The bar's own timestamp, not the clock.

    `normalizer._normalize_yahoo_quote` falls back to `datetime.now()` for
    `timestamp` because the vendor payload carries no quote time — so a quote
    from a dead feed would be stamped with the instant it was SERVED and could
    never be classified stale. This is why `observed_at` is a separate key.
    """
    last_bar = NOW - timedelta(days=4)
    raw = _bars(40, last_bar=last_bar)

    technicals = real_market.derive_technicals(raw)

    observed = field_quality.parse_instant(technicals[OBSERVED_AT_KEY])
    assert observed is not None
    assert abs((observed - last_bar).total_seconds()) < 1
    # And that instant is what makes the readings stale, four days on.
    quote = {**raw, **technicals}
    assert field_quality.quality_of(quote, "rsi", now=NOW) is FieldQuality.STALE


def test_a_stale_reading_still_carries_its_value():
    """STALE changes the claim, never the data. Nothing is hidden or deleted —
    the value travels and the platform simply stops calling it current."""
    raw = _bars(40, last_bar=NOW - timedelta(days=4))
    quote = {**raw, **real_market.derive_technicals(raw)}

    assert quote["rsi"] is not None
    assert field_quality.quality_of(quote, "rsi", now=NOW) is FieldQuality.STALE
    assert field_quality.reading(quote, "rsi", now=NOW) is None


# --------------------------------------------------------------------------- #
# Gateway carriage                                                             #
# --------------------------------------------------------------------------- #

_PROVIDER_FAMILIES = ["yahoo", "alpha_vantage", "broker", "canonical"]


@pytest.mark.parametrize("provider", _PROVIDER_FAMILIES)
def test_quality_survives_canonical_normalization(provider):
    raw = {
        "symbol": "RELIANCE",
        "price": 1300.0,
        "last_price": 1300.0,
        OBSERVED_AT_KEY: _iso(NOW),
        QUALITY_KEY: {"rsi": "insufficient_history", "macd": "provider_error"},
    }

    normalized = normalize_stock_quote(raw, provider=provider)

    assert normalized[QUALITY_KEY] == {
        "rsi": "insufficient_history",
        "macd": "provider_error",
    }
    assert normalized[OBSERVED_AT_KEY] == _iso(NOW)


@pytest.mark.parametrize("provider", _PROVIDER_FAMILIES)
def test_the_payload_shape_still_carries_both_keys_on_every_tier(provider):
    """A key present on a delayed quote and absent on a streaming one is a
    consumer able to tell which provider answered — Developer Rule 4 defeated by
    schema rather than by a string. Both keys exist on every family, including
    the ones that carry no indicators at all."""
    normalized = normalize_stock_quote({"symbol": "X", "price": 1.0, "last_price": 1.0}, provider=provider)

    assert QUALITY_KEY in normalized
    assert OBSERVED_AT_KEY in normalized


def test_the_quality_key_is_identical_across_every_provider_family():
    shapes = {
        provider: sorted(normalize_stock_quote({"symbol": "X", "price": 1.0, "last_price": 1.0}, provider=provider))
        for provider in _PROVIDER_FAMILIES
    }

    assert len({tuple(keys) for keys in shapes.values()}) == 1, shapes


def test_normalization_strips_anything_that_is_not_one_of_the_six_states():
    """The containment gate at the boundary a payload crosses on its way out."""
    raw = {
        "symbol": "X",
        "price": 1.0,
        QUALITY_KEY: {
            "rsi": "available",
            "macd": "served_by_zerodha",
            "provider": "yahoo",
            "access_token": "eyJhbGciOiJIUzI1NiJ9.SECRET",
            "owner_user_id": "6a5e6228aa11bb22cc33dd44",
        },
    }

    normalized = normalize_stock_quote(raw, provider="yahoo")

    assert normalized[QUALITY_KEY] == {"rsi": "available"}
    assert not PROVIDER_NAMES.search(json.dumps(normalized[QUALITY_KEY]))


def test_an_unrecognised_provider_payload_is_sanitized_too():
    """The passthrough normalizer is the one input nothing has vetted, and so it
    is exactly where an un-normalized key would reach a consumer wearing the
    platform's own field name."""
    normalized = normalize_stock_quote(
        {"symbol": "X", "price": 1.0, QUALITY_KEY: {"rsi": "yahoo_said_no"}},
        provider="some_future_vendor",
    )

    assert normalized[QUALITY_KEY] == {}


# --------------------------------------------------------------------------- #
# Ranking — the scores do not move                                             #
# --------------------------------------------------------------------------- #

FULL_QUOTE = {
    "symbol": "RELIANCE",
    "name": "Reliance Industries",
    "price": 1300.0,
    "change_pct": 2.5,
    "sector": "Oil & Gas",
    "rsi": 55.0,
    "macd": 3.0,
    "macd_signal": 1.0,
    "avg_volume": 8_000_000,
    "volume_ratio": 1.8,
}

#: The scores this quote produced BEFORE D6.8-A, measured against the
#: pre-change `ranking_engine` and recorded here as literals.
#:
#: Literals, not a recomputation, because a recomputation through the same code
#: under test cannot fail. These numbers are the D6-Q1 contract — "scores stay
#: unchanged" — in the only form that can be violated.
_PRE_D68A_SCORES = {
    "momentum": 95.0,
    "trend": 90.0,
    "volume": 75.0,
    "risk": 80.0,
    "news": 50.0,
    "sector": 100.0,
    "liquidity": 90.0,
    "ai_confidence": 80.0,
}
_PRE_D68A_OPPORTUNITY = 84.4

#: And the scores for a quote carrying NO technicals at all, which is the case
#: where the substitutes actually fire. Unchanged is the harder half to hold.
_PRE_D68A_EMPTY_SCORES = {
    "momentum": 75.0,
    "trend": 40.0,
    "volume": 50.0,
    "risk": 80.0,
    "news": 50.0,
    "sector": 50.0,
    "liquidity": 25.0,
    "ai_confidence": 50.0,
}
_PRE_D68A_EMPTY_OPPORTUNITY = 55.0


def test_available_values_produce_exactly_the_scores_they_produced_before():
    ranked = ranking_engine.rank_stock(dict(FULL_QUOTE), sector_rank=0, total_sectors=12, sector_change=1.2)

    assert {k: v["score"] for k, v in ranked["dimensions"].items()} == _PRE_D68A_SCORES
    assert ranked["opportunity_score"] == _PRE_D68A_OPPORTUNITY
    assert ranked["signal"] == "strong_buy"


def test_an_unmeasured_quote_scores_exactly_what_it_scored_before():
    """The substitutes still fire, numerically, at every site.

    This is deliberate and is the D6-Q1 boundary: withholding a score would
    change what the engine RECOMMENDS, and this phase changes only what it
    CLAIMS. The substitution moved from an invisible `or` into a named call; it
    did not move in value. See LIM-D6.8A-1 for the one case where `or`'s
    treatment of a falsy real reading is knowingly preserved.
    """
    ranked = ranking_engine.rank_stock({"symbol": "NEW", "name": "New", "price": 100.0})

    assert {k: v["score"] for k, v in ranked["dimensions"].items()} == _PRE_D68A_EMPTY_SCORES
    assert ranked["opportunity_score"] == _PRE_D68A_EMPTY_OPPORTUNITY


def test_the_scoring_placeholders_are_exactly_the_literals_they_replaced():
    """The six `or <default>` literals G-5 named, pinned as values.

    Without this the table is only observable through scores, and a score is a
    lossy observation: `rsi` moved from 50.0 to 60.0 leaves `momentum`, `risk`
    and `ai_confidence` byte-identical for an unmeasured quote, so the golden
    scores below cannot see it. An inert mutation is not a passing control.
    """
    assert ranking_engine.SCORING_PLACEHOLDERS == {
        "rsi": 50.0,
        "change_pct": 0.0,
        "macd": 0.0,
        "macd_signal": 0.0,
        "volume_ratio": 1.0,
        "avg_volume": 0,
    }


@pytest.mark.parametrize(
    "value,placeholder,expected",
    [
        (55.0, 50.0, 55.0),
        (None, 50.0, 50.0),
        (0, 50.0, 50.0),  # LIM-D6.8A-1: `or`'s behaviour, preserved on purpose
        (0.0, 1.0, 1.0),
        (False, 1.0, 1.0),
        (-3.2, 0.0, -3.2),
        (1.8, 1.0, 1.8),
    ],
)
def test_the_scoring_placeholder_is_numerically_the_expression_it_replaced(value, placeholder, expected):
    """`scoring_input` IS `quote.get(field) or placeholder`, exhaustively.

    The whole no-score-moves guarantee rests on this one equivalence, so it is
    asserted directly rather than inferred from the golden scores above.
    """
    assert field_quality.scoring_input({"f": value}, "f", placeholder) == expected


def test_a_stale_reading_scores_as_it_always_did_but_may_not_be_evidence():
    """The state that did not exist before, and the reason it is the dangerous
    one: the number is real, so nothing downstream could tell."""
    stale = {
        **FULL_QUOTE,
        OBSERVED_AT_KEY: _iso(datetime.now(timezone.utc) - timedelta(days=4)),
    }

    ranked = ranking_engine.rank_stock(stale, sector_rank=0, total_sectors=12, sector_change=1.2)

    assert ranked["opportunity_score"] == _PRE_D68A_OPPORTUNITY
    assert ranked["dimensions"]["momentum"]["available"] is False
    assert ranked["dimensions"]["momentum"]["reason"] is None
    assert not any(e["dimension"] == "momentum" for e in ranked["evidence"])


@pytest.mark.parametrize(
    "state",
    [
        FieldQuality.MISSING,
        FieldQuality.INSUFFICIENT_HISTORY,
        FieldQuality.PROVIDER_ERROR,
        FieldQuality.UNAVAILABLE,
        FieldQuality.STALE,
    ],
)
def test_no_non_available_state_can_masquerade_as_a_real_reading(state):
    """Every one of the five, over the dimension whose sentence is the most
    persuasive. `momentum` reads "RSI 55 in bullish zone; Strong +2.5% day
    move" — the clause a reader weighs most is the invented one."""
    quote = {**FULL_QUOTE, QUALITY_KEY: {"rsi": state.value}}

    ranked = ranking_engine.rank_stock(quote)

    assert ranked["dimensions"]["momentum"]["available"] is False
    assert "RSI" not in json.dumps(ranked["evidence"])


def test_the_falsifying_twin_a_fully_available_quote_does_speak():
    """Without this, every assertion above passes against an engine that
    published no evidence at all."""
    ranked = ranking_engine.rank_stock(dict(FULL_QUOTE), sector_rank=0, total_sectors=12, sector_change=1.2)

    assert ranked["dimensions"]["momentum"]["available"] is True
    assert "RSI 55 in bullish zone" in json.dumps(ranked["evidence"])


def test_ranking_order_is_unchanged_for_measured_quotes():
    quotes = [
        {**FULL_QUOTE, "symbol": "A", "rsi": 55.0, "change_pct": 2.5},
        {**FULL_QUOTE, "symbol": "B", "rsi": 80.0, "change_pct": -3.0},
        {**FULL_QUOTE, "symbol": "C", "rsi": 60.0, "change_pct": 1.0},
    ]

    ranked = sorted(
        (ranking_engine.rank_stock(q) for q in quotes),
        key=lambda r: r["opportunity_score"],
        reverse=True,
    )

    assert [r["symbol"] for r in ranked] == ["A", "C", "B"]


# --------------------------------------------------------------------------- #
# Scanner                                                                      #
# --------------------------------------------------------------------------- #


def _legacy_passes_filters(quote, filters):
    """`_passes_filters` EXACTLY as it stood before D6.8-A.

    An independent oracle, transcribed rather than imported, so "every preset
    matches what it always matched" is asserted against the old arithmetic and
    not against the new code's opinion of itself.
    """
    price = quote.get("price") or 0
    rsi = quote.get("rsi")
    volume = quote.get("volume") or 0
    volume_ratio = quote.get("volume_ratio") or 0
    change_pct = quote.get("change_pct") or 0
    macd = quote.get("macd") or 0
    macd_signal = quote.get("macd_signal") or 0
    sector = (quote.get("sector") or "").lower()

    if "price_min" in filters and price < filters["price_min"]:
        return False
    if "price_max" in filters and price > filters["price_max"]:
        return False
    if "volume_min" in filters and volume < filters["volume_min"]:
        return False
    if "sector" in filters and filters["sector"]:
        if sector != filters["sector"].lower():
            return False
    if rsi is not None:
        if "rsi_min" in filters and rsi < filters["rsi_min"]:
            return False
        if "rsi_max" in filters and rsi > filters["rsi_max"]:
            return False
    if "change_pct_min" in filters and change_pct < filters["change_pct_min"]:
        return False
    if "change_pct_max" in filters and change_pct > filters["change_pct_max"]:
        return False
    if "volume_ratio_min" in filters and volume_ratio < filters["volume_ratio_min"]:
        return False
    if filters.get("macd_bullish") and macd <= macd_signal:
        return False
    return True


def _universe():
    """A universe spanning measured, partly measured and unmeasured stocks."""
    base = {"sector": "Oil & Gas", "price": 1000.0, "volume": 5_000_000}
    return [
        {
            **base,
            "symbol": "FULL",
            "rsi": 55.0,
            "macd": 3.0,
            "macd_signal": 1.0,
            "volume_ratio": 1.9,
            "change_pct": 2.0,
        },
        {
            **base,
            "symbol": "WEAK",
            "rsi": 30.0,
            "macd": 1.0,
            "macd_signal": 3.0,
            "volume_ratio": 0.4,
            "change_pct": -2.0,
        },
        {
            **base,
            "symbol": "NORSI",
            "rsi": None,
            "macd": 3.0,
            "macd_signal": 1.0,
            "volume_ratio": 1.9,
            "change_pct": 2.0,
            QUALITY_KEY: {"rsi": "insufficient_history"},
        },
        {
            **base,
            "symbol": "BARE",
            "rsi": None,
            "macd": None,
            "macd_signal": None,
            "volume_ratio": None,
            "change_pct": None,
            QUALITY_KEY: field_quality.all_missing(QUALITY_TRACKED_FIELDS, FieldQuality.UNAVAILABLE),
        },
        {
            **base,
            "symbol": "NOVOL",
            "rsi": 60.0,
            "macd": 3.0,
            "macd_signal": 1.0,
            "volume_ratio": None,
            "change_pct": 1.5,
            QUALITY_KEY: {"volume_ratio": "missing"},
        },
    ]


@pytest.mark.parametrize("preset", sorted(scanner_engine.STRATEGY_PRESETS))
def test_every_preset_matches_exactly_what_it_matched_before(preset):
    """The no-silent-change guarantee, preset by preset, against the old code.

    Every preset's bounds are the refusing kind, and the substitutes already
    failed them, so the quality-aware rule changes nothing here. What it changes
    is the fabricated-PASS case below, which no preset exercises.
    """
    filters = scanner_engine.STRATEGY_PRESETS[preset]["filters"]

    new = [q["symbol"] for q in _universe() if scanner_engine._passes_filters(q, filters)]
    old = [q["symbol"] for q in _universe() if _legacy_passes_filters(q, filters)]

    assert new == old, f"{preset} changed: {old} -> {new}"


def test_the_preset_parity_check_could_have_failed():
    """The oracle actually discriminates — several presets must reject several
    stocks, or the parity above is a comparison of two empty lists."""
    outcomes = {
        preset: [q["symbol"] for q in _universe() if _legacy_passes_filters(q, spec["filters"])]
        for preset, spec in scanner_engine.STRATEGY_PRESETS.items()
    }

    assert any(o for o in outcomes.values()), outcomes
    assert len({tuple(o) for o in outcomes.values()}) > 1, outcomes


def test_a_bound_can_no_longer_be_satisfied_by_a_substituted_value():
    """The one behavioural change, named.

    `change_pct_min: -5` admitted every stock whose day change the platform
    never had, because `None or 0` is -5's superior. The stock was then selected
    by a scan for stocks that had not fallen far, and told it matched.
    """
    filters = {"change_pct_min": -5.0}
    unmeasured = next(q for q in _universe() if q["symbol"] == "BARE")

    assert _legacy_passes_filters(unmeasured, filters) is True
    assert scanner_engine._passes_filters(unmeasured, filters) is False


def test_a_measured_stock_still_passes_that_same_bound():
    """The falsifying twin: the rule refuses substitutes, not stocks."""
    measured = next(q for q in _universe() if q["symbol"] == "FULL")

    assert scanner_engine._passes_filters(measured, {"change_pct_min": -5.0}) is True


def test_an_rsi_bound_is_still_skipped_for_a_stock_with_no_rsi():
    """The documented exception, preserved. `match_evidence`'s docstring
    predates this phase: refusing it would hide a real stock over missing
    reference data."""
    norsi = next(q for q in _universe() if q["symbol"] == "NORSI")

    assert scanner_engine._passes_filters(norsi, {"rsi_min": 40, "rsi_max": 70}) is True


def test_a_skipped_bound_is_reported_rather_than_left_invisible():
    """…and the stock says so, which is what makes the skip honest.

    Before this, a stock that passed an RSI-filtered scan without an RSI was
    indistinguishable in the result from one that passed with a real one.
    """
    norsi = next(q for q in _universe() if q["symbol"] == "NORSI")
    full = next(q for q in _universe() if q["symbol"] == "FULL")
    filters = {"rsi_min": 40, "rsi_max": 70}

    assert set(scanner_engine.unverified_filters(norsi, filters)) == {"rsi_min", "rsi_max"}
    assert scanner_engine.unverified_filters(full, filters) == []


def test_a_stale_rsi_cannot_satisfy_an_rsi_filter_as_a_real_one():
    """STALE in the scanner: the value is real and is not a fact about now."""
    stale = {
        "symbol": "STALE",
        "price": 1000.0,
        "volume": 1,
        "rsi": 55.0,
        OBSERVED_AT_KEY: _iso(datetime.now(timezone.utc) - timedelta(days=4)),
    }

    assert scanner_engine.unverified_filters(stale, {"rsi_min": 40}) == ["rsi_min"]


def test_a_macd_filter_refuses_a_stock_with_only_half_a_macd():
    """`macd > macd_signal` against an absent signal line is the comparison
    D5.16 documented as silently true for every stock with positive momentum."""
    half = {"symbol": "H", "price": 1.0, "macd": 3.0, "macd_signal": None}

    assert scanner_engine._passes_filters(half, {"macd_bullish": True}) is False


#: A universe built for the sort, not for the filters: three stocks with an RSI
#: and two without, all of which pass an unfiltered scan.
_SORTABLE = [
    {"symbol": "LOW", "price": 100.0, "volume": 1, "rsi": 20.0},
    {"symbol": "MID", "price": 100.0, "volume": 1, "rsi": 50.0},
    {"symbol": "HIGH", "price": 100.0, "volume": 1, "rsi": 80.0},
    {"symbol": "NONE1", "price": 100.0, "volume": 1, "rsi": None, QUALITY_KEY: {"rsi": "insufficient_history"}},
    {"symbol": "NONE2", "price": 100.0, "volume": 1, "rsi": None, QUALITY_KEY: {"rsi": "provider_error"}},
]


@pytest.mark.parametrize(
    "descending,expected_measured",
    [
        (False, ["LOW", "MID", "HIGH"]),
        (True, ["HIGH", "MID", "LOW"]),
    ],
)
def test_an_unmeasured_sort_key_ranks_last_in_both_directions(monkeypatch, descending, expected_measured):
    """`q.get(sort_key) or 0` gave every unmeasured stock a sort value of zero,
    and zero is not neutral in an ordering — it is an extreme.

    `reversal` sorts RSI ascending to surface the most oversold stock, so a
    stock with no RSI at all took that slot, under a heading asserting it is the
    most oversold thing on the exchange. `value` sorts the same way.

    Both directions, unconditionally, because a flag that is merely "not
    negated" passes an ascending-only test and inverts the descending one.
    """
    monkeypatch.setitem(
        scanner_engine.STRATEGY_PRESETS,
        "_d68a_probe",
        {"label": "probe", "description": "", "filters": {}, "sort_key": "rsi", "sort_desc": descending},
    )

    result = _run(_scan(_SORTABLE, strategy="_d68a_probe"))
    symbols = [r["symbol"] for r in result["results"]]

    assert symbols[:3] == expected_measured
    assert set(symbols[3:]) == {"NONE1", "NONE2"}


def test_coverage_does_not_let_a_provider_failure_look_like_a_smaller_market():
    """STEP 13's invariant, asked of the REAL gateway.

    `total_scanned` alone says how many stocks were examined and is silent about
    how many were meant to be. A vendor that failed on seven of thirty-one
    symbols made the scan report 24, which does not read as an incident; it
    reads as a smaller, healthy market.

    Asserted against `market_gateway.universe_coverage` and not through the
    scanner's doubled gateway, because a double that reimplements the
    arithmetic is a second copy of the thing under test, and it will agree with
    a bug in the first copy — which is precisely how this assertion survived its
    own mutation on the first pass.
    """
    from market_data import STOCK_UNIVERSE
    from services.market_engine.gateway import market_gateway

    coverage = market_gateway.universe_coverage(2)

    assert coverage["requested"] == len(STOCK_UNIVERSE)
    assert coverage["requested"] != coverage["available"]
    assert coverage["available"] == 2
    assert coverage["unavailable"] == len(STOCK_UNIVERSE) - 2


def test_the_scan_envelope_carries_that_coverage():
    """…and the scanner publishes it rather than computing its own."""
    result = _run(_scan(_universe()[:2]))

    assert result["total_scanned"] == 2
    assert result["coverage"]["available"] == 2
    assert result["coverage"]["requested"] > 2


def test_coverage_is_present_even_when_nothing_resolved():
    result = _run(_scan([]))

    assert result["available"] is False
    assert result["coverage"]["available"] == 0
    assert result["coverage"]["requested"] > 0


def test_the_producer_counts_a_raising_symbol_as_a_failure_not_an_absence(monkeypatch):
    """The G-3 distinction the universe loop used to discard.

    Driven through `fetch_all_universe_quotes` with a real fake vendor rather
    than by seeding the counter, because a test that writes the number it then
    reads back asserts on its own fixture and not on the loop — which is exactly
    how a mutation that swaps the two branches survives.
    """
    from market_data import STOCK_UNIVERSE

    raisers = {s["symbol"] for s in STOCK_UNIVERSE[:3]}
    emptys = {s["symbol"] for s in STOCK_UNIVERSE[3:5]}

    async def _fetch(symbol, range_str=None):
        if symbol in raisers:
            raise RuntimeError("vendor exploded")
        if symbol in emptys:
            return None
        return _bars(40)

    monkeypatch.setattr(real_market, "fetch_yahoo_quote", _fetch)
    monkeypatch.setattr(real_market, "cache_get", _none)
    monkeypatch.setattr(real_market, "cache_get_many", _empty_map)
    monkeypatch.setattr(real_market, "cache_set", _noop)

    quotes = _run(real_market.fetch_all_universe_quotes())
    coverage = real_market.universe_coverage()

    assert coverage["requested"] == len(STOCK_UNIVERSE)
    assert coverage["available"] == len(quotes) == len(STOCK_UNIVERSE) - 5
    assert coverage["provider_error"] == 3
    assert coverage["unavailable"] == 2


def test_the_producer_coverage_is_clean_when_nothing_failed(monkeypatch):
    """The falsifying twin: without it, a loop that counted EVERY symbol as a
    provider error would pass the assertion above just as well."""
    from market_data import STOCK_UNIVERSE

    async def _fetch(symbol, range_str=None):
        return _bars(40)

    monkeypatch.setattr(real_market, "fetch_yahoo_quote", _fetch)
    monkeypatch.setattr(real_market, "cache_get", _none)
    monkeypatch.setattr(real_market, "cache_get_many", _empty_map)
    monkeypatch.setattr(real_market, "cache_set", _noop)

    _run(real_market.fetch_all_universe_quotes())
    coverage = real_market.universe_coverage()

    assert coverage["provider_error"] == 0
    assert coverage["unavailable"] == 0
    assert coverage["available"] == len(STOCK_UNIVERSE)


async def _none(*_a, **_k):
    return None


async def _empty_map(*_a, **_k):
    return {}


async def _noop(*_a, **_k):
    return None


def test_coverage_omits_the_failure_split_when_no_live_fetch_happened():
    """A cached universe is served without a fetch, and reporting an earlier
    fetch's failures as though they had just occurred is the same class of error
    this counter exists to remove."""
    real_market._record_universe_coverage({})

    result = _run(_scan(_universe()[:2]))

    assert "provider_error" not in result["coverage"]


class _Gateway:
    """The scanner's gateway, doubled — quotes in, tier and coverage out."""

    def __init__(self, quotes):
        self._quotes = quotes

    async def get_universe_quotes(self, *, user_id=None):
        return [dict(q) for q in self._quotes]

    def source_tier(self, _capability=None, *, user_id=None):
        return "delayed"

    def universe_coverage(self, served):
        from market_data import STOCK_UNIVERSE
        from services.real_market import universe_coverage as producer

        requested = len(STOCK_UNIVERSE)
        coverage = {
            "requested": requested,
            "available": served,
            "unavailable": max(0, requested - served),
        }
        live = producer()
        if live.get("requested") == requested:
            coverage["provider_error"] = live.get("provider_error", 0)
        return coverage


async def _scan(quotes, **kwargs):
    import services.market_engine.gateway as gateway_module

    real = gateway_module.market_gateway
    gateway_module.market_gateway = _Gateway(quotes)
    try:
        return await scanner_engine.scan(publish=False, **kwargs)
    finally:
        gateway_module.market_gateway = real


# --------------------------------------------------------------------------- #
# AI                                                                           #
# --------------------------------------------------------------------------- #


def test_an_unavailable_rsi_is_not_rendered_as_a_number_for_a_model():
    quote = {"rsi": None, QUALITY_KEY: {"rsi": "insufficient_history"}}

    rendered = field_quality.describe_field(quote, "rsi")

    assert rendered == "not enough price history to compute"
    assert not re.search(r"\d", rendered)


def test_an_insufficient_history_macd_is_not_rendered_as_a_valid_macd():
    quote = {"macd": None, "macd_signal": None, QUALITY_KEY: {"macd": "insufficient_history"}}

    assert field_quality.describe_field(quote, "macd") == "not enough price history to compute"
    assert "0" not in field_quality.describe_field(quote, "macd")


def test_a_provider_error_is_not_converted_into_a_number_for_a_model():
    quote = {"rsi": None, QUALITY_KEY: {"rsi": "provider_error"}}

    assert field_quality.describe_field(quote, "rsi") == "could not be retrieved"


def test_a_stale_reading_is_labelled_rather_than_handed_over_as_current():
    quote = {"rsi": 55.0, OBSERVED_AT_KEY: _iso(NOW - timedelta(days=4))}

    rendered = field_quality.describe_field(quote, "rsi", now=NOW)

    assert rendered == "last reading is out of date"
    assert "55" not in rendered


def test_a_real_reading_is_still_rendered_as_its_number():
    """The falsifying twin for every AI assertion above."""
    assert field_quality.describe_field({"rsi": 55.0}, "rsi") == "55.0"


@pytest.mark.parametrize("state", list(FieldQuality))
def test_every_state_has_a_phrase_and_none_of_them_names_anything_internal(state):
    """These strings reach AI prompts and tooltips, so they are governed by the
    standing disclosure rule."""
    phrase = field_quality.describe(state)

    assert phrase
    assert not PROVIDER_NAMES.search(phrase)
    assert not CREDENTIALS.search(phrase)
    assert not PROVIDER_INTERNALS.search(phrase)


def test_the_explain_prompt_no_longer_interpolates_a_substituted_indicator():
    """`/analysis/explain` read `quote['rsi']` directly, and that key was
    non-null only because `fetch_real_stock_quote` substituted 50.0."""
    import server

    source = _source_between(server.explain_stock)
    assert "quote['rsi']" not in source and 'quote["rsi"]' not in source
    assert "describe_field" in source


def test_the_full_report_prompt_no_longer_interpolates_substituted_indicators():
    import server

    source = _source_between(server.full_ai_report)
    assert 'quote["rsi"]' not in source and "quote['rsi']" not in source
    assert "describe_field" in source


def test_the_advisor_enrichment_prompt_does_not_hand_a_model_a_bare_none():
    import server

    source = _source_between(server._advisor_ai_enrich)
    assert "RSI {r.get('rsi')}" not in source
    assert "describe_field" in source


def _source_between(func):
    """A function's source with its comments stripped.

    Comments are stripped deliberately and this is the opposite of the usual
    rule. A sweep for removed code normally has to INCLUDE comments, because a
    banned name hiding in one is a name a future author will copy back out. Here
    the reverse holds: these sweeps look for expressions D6.8-A deleted, and the
    comment that replaced each one QUOTES it, in order to say what was wrong
    with it. A comment-inclusive sweep would therefore fail on the very
    documentation that records the fix, and the only way to make it pass would
    be to delete the explanation — which is the wrong thing to optimise for.

    The property being asserted is about executable code, so the sweep reads
    executable code.
    """
    import inspect

    return _strip_comments(inspect.getsource(func))


def _strip_comments(source: str) -> str:
    """Drop `#` comments from a function's source, line by line.

    Crude on purpose: a `#` inside a string literal would truncate that line
    too. That only ever makes the sweep STRICTER — it can lose code, never gain
    it — so a false PASS is not reachable from here, and none of the swept
    functions contains a `#` in a string.
    """
    return NEWLINE.join(line.split("#")[0] for line in source.splitlines())


NEWLINE = chr(10)


def test_the_ai_context_carries_a_real_observation_time():
    """G-8 — the module's own docstring has claimed since D1 that the context
    carries timestamps so the model can say "as of 10:42 AM". It did not."""
    from services import ai_context_builder

    rendered = ai_context_builder._render_market(
        {"market_status": "OPEN", "observed_at": "2026-09-16T05:12:00+00:00"},
        "delayed",
    )

    assert "10:42 IST" in rendered
    assert "Data freshness: delayed" in rendered


def test_the_ai_context_omits_the_time_rather_than_inventing_one():
    """A payload with no observation instant gets no line, rather than the
    clock's answer to a question it was not asked. Rendering the READ time would
    stamp a cached snapshot as current, which is the dangerous direction."""
    from services import ai_context_builder

    rendered = ai_context_builder._render_market({"market_status": "OPEN"})

    assert "Observed at" not in rendered
    assert ai_context_builder.observed_at_str({"market_status": "OPEN"}) is None


def test_the_market_overview_stamps_the_instant_it_was_fetched():
    """The timestamp half of G-8, at the producer. Stamped before the cache
    write, so a cache hit carries the instant the vendor was actually asked."""
    import inspect

    source = inspect.getsource(real_market.fetch_real_market_overview)
    assert '"observed_at"' in source


# --------------------------------------------------------------------------- #
# The three surfaces D5.19 did not reach (§G-6)                                #
# --------------------------------------------------------------------------- #


def test_the_advisor_does_not_narrate_an_indicator_it_does_not_have():
    """`_advisor_score`'s reasons are published as `technical_reasons` on every
    recommendation and quoted into the deterministic narrative."""
    import server

    confidence, reasons = server._advisor_score({"symbol": "NEW", "price": 100.0}, [], "swing")

    assert reasons == []
    assert isinstance(confidence, int)


def test_the_advisor_still_narrates_an_indicator_it_does_have():
    import server

    _confidence, reasons = server._advisor_score(dict(FULL_QUOTE), [], "swing")

    assert any("RSI at 55" in r for r in reasons)


def test_the_advisor_confidence_is_unchanged_by_the_gating():
    """The arithmetic is untouched: an unmeasured stock scores exactly what it
    scored before, and only its sentences are withheld."""
    import server

    measured, _ = server._advisor_score(dict(FULL_QUOTE), [], "swing")
    unmeasured, _ = server._advisor_score({"symbol": "N", "price": 100.0}, [], "swing")

    # Measured against the PRE-D6.8-A `_advisor_score` and recorded as
    # literals, for the reason the ranking goldens are literals: a
    # recomputation through the code under test cannot fail.
    assert measured == 95
    assert unmeasured == 61


def test_the_advisor_states_the_absence_rather_than_asserting_a_setup():
    """The fallback used to read "Constructive technical setup on the daily
    timeframe" — a claim about a chart, made precisely when the scorer had
    nothing to say about it."""
    import server

    source = _source_between(server.build_advisor_recommendations)
    assert '["Constructive technical setup on the daily timeframe."]' not in source
    assert "not supported by technical evidence" in source


def test_top_picks_publish_the_reading_and_not_the_scoring_placeholder():
    """These two keys are rendered verbatim on the AI Picks card."""
    source = _source_between(real_market.fetch_real_top_picks)
    assert '"rsi": field_quality.reading(s, "rsi")' in source
    assert '"rsi": rsi,' not in source


def _top_picks(monkeypatch, quote):
    """Drive `fetch_real_top_picks` over a one-symbol universe carrying `quote`."""
    import market_data
    from services.market_engine import gateway as gateway_module

    universe = [{"symbol": "TESTSYM", "name": "Test Ltd", "sector": "Oil & Gas"}]
    monkeypatch.setattr(market_data, "STOCK_UNIVERSE", universe)

    class _G:
        async def get_prices(self, symbols, *, user_id=None):
            return {"TESTSYM": {**quote, "symbol": "TESTSYM", "price": 1000.0}}

    monkeypatch.setattr(gateway_module, "market_gateway", _G())
    monkeypatch.setattr(real_market, "cache_get", _none)
    monkeypatch.setattr(real_market, "cache_set", _noop)

    async def _patterns(_symbol):
        return {"patterns": []}

    async def _sectors():
        return []

    monkeypatch.setattr(real_market, "detect_chart_patterns", _patterns)
    monkeypatch.setattr(real_market, "fetch_real_sectors", _sectors)

    result = _run(real_market.fetch_real_top_picks(1))
    assert result["picks"], result
    return result["picks"][0]


def test_top_picks_do_not_narrate_an_indicator_they_do_not_have(monkeypatch):
    """The scorer, driven — not swept.

    A source sweep proves the payload publishes a reading; it says nothing about
    whether the `reasons` list, which is rendered on the card as the case for
    buying the stock, is gated. Those are two different controls and only one of
    them was under test.
    """
    pick = _top_picks(
        monkeypatch,
        {
            "rsi": None,
            "volume_ratio": None,
            "macd": None,
            "macd_signal": None,
            QUALITY_KEY: field_quality.all_missing(QUALITY_TRACKED_FIELDS, FieldQuality.INSUFFICIENT_HISTORY),
        },
    )

    assert pick["reasons"] == []
    assert pick["rsi"] is None
    assert pick["volume_ratio"] is None


def test_top_picks_still_narrate_an_indicator_they_do_have(monkeypatch):
    """The falsifying twin."""
    pick = _top_picks(
        monkeypatch,
        {
            "rsi": 60.0,
            "volume_ratio": 1.9,
            "macd": 3.0,
            "macd_signal": 1.0,
        },
    )

    assert any("RSI is at a strong bullish zone of 60.0" in r for r in pick["reasons"])
    assert any("MACD is currently in a bullish crossover" in r for r in pick["reasons"])
    assert pick["rsi"] == 60.0


def test_top_picks_confidence_is_unchanged_by_the_gating(monkeypatch):
    """The arithmetic still substitutes, so an unmeasured stock scores exactly
    what it scored before and only its sentences are withheld."""
    measured = _top_picks(
        monkeypatch,
        {
            "rsi": 60.0,
            "volume_ratio": 1.9,
            "macd": 3.0,
            "macd_signal": 1.0,
        },
    )
    unmeasured = _top_picks(
        monkeypatch,
        {
            "rsi": None,
            "volume_ratio": None,
            "macd": None,
            "macd_signal": None,
            QUALITY_KEY: field_quality.all_missing(QUALITY_TRACKED_FIELDS, FieldQuality.INSUFFICIENT_HISTORY),
        },
    )

    # Measured against the PRE-D6.8-A scorer and recorded as literals.
    #
    # The unmeasured case is the one that matters, and it is the one that could
    # only be measured by feeding the old scorer what production actually gave
    # it — `fetch_real_stock_quote`'s substituted 50.0 / 1.0 / 0.0, not a null,
    # because a null made the old code raise on `macd > macd_signal`. Under
    # those substitutes it produced confidence 65 and published:
    #
    #     rsi: 50.0
    #     reasons: ["RSI is at a strong bullish zone of 50.0"]
    #
    # about a stock whose RSI nobody had ever computed. The confidence is
    # identical below; the sentence and the published number are gone.
    assert measured["confidence"] == 95
    assert unmeasured["confidence"] == 65


def test_top_picks_no_longer_read_a_technical_through_a_dict_default():
    """`s.get("rsi", 50.0)` only fires on a MISSING KEY, and the key was always
    present carrying a substituted 50.0 — a default covering for a fabrication
    upstream."""
    source = _source_between(real_market.fetch_real_top_picks)
    assert 's.get("rsi", 50.0)' not in source
    assert 's.get("volume_ratio", 1.0)' not in source


# --------------------------------------------------------------------------- #
# Fabricated defaults are gone from every live producer                        #
# --------------------------------------------------------------------------- #


def test_the_single_quote_path_no_longer_substitutes_a_technical():
    """LIM-D5.19-2, closed. This was the one place in the product where a
    missing indicator became a number with no marker of any kind."""
    raw = _bars(5)
    quote = {**raw, **real_market.derive_technicals(raw)}

    assert quote["rsi"] is None
    assert quote["macd"] is None
    assert quote["avg_volume"] is None
    assert quote["volume_ratio"] is None


def test_the_indicator_calculators_return_absence_rather_than_a_plausible_value():
    """`calculate_rsi` used to `return 50.0` and `calculate_macd` `0.0, 0.0` for
    a series they could not measure. Guarded callers made those unreachable, and
    an unreachable fabrication is one refactor from a reachable one."""
    assert real_market.calculate_rsi([1.0, 2.0, 3.0]) is None
    assert real_market.calculate_macd([1.0, 2.0, 3.0]) == (None, None)


def test_no_live_producer_substitutes_a_technical_default():
    """A source sweep over the D5.19-2 literals, in the producer module.

    Literal-shaped rather than identifier-shaped, because that is how these
    defaults were written — and a sweep for `rsi` would have missed
    `technicals["rsi"] if … else 50.0` entirely.
    """
    import pathlib

    source = pathlib.Path(real_market.__file__).read_text()
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))

    for banned in (
        'technicals["rsi"] if',
        "else 1000000",
        's.get("rsi", 50.0)',
    ):
        assert banned not in code, banned


# --------------------------------------------------------------------------- #
# Security / isolation                                                         #
# --------------------------------------------------------------------------- #
#
# Lifted deliberately from `test_d519_surface_disclosure.py`, which is the
# security precedent the brief names. A quality map travels to the browser and
# into AI prompts, so it is governed by MARKET_DATA_ARCHITECTURE.md's Developer
# Rules 4 and 5 exactly like `source_tier` is.

PROVIDER_NAMES = re.compile(r"yahoo|upstox|zerodha|kite|fyers|dhan|angel\s*one|angelone", re.I)

CREDENTIALS = re.compile(
    r"access[_-]?token|refresh[_-]?token|api[_-]?key|api[_-]?secret|"
    r"authorization|bearer\s|password|passwd|secret|private[_-]?key|"
    r"session[_-]?token|jwt",
    re.I,
)

PROVIDER_INTERNALS = re.compile(
    r"probation|cooldown|cool[_-]down|degraded|health[_-]?state|"
    r"shard|p95|latency_ms|circuit|failover|owner_user_id|"
    r"provider_registry|source_manager",
    re.I,
)

BROKER_PRIVATE_IDS = re.compile(
    r"NSE_EQ\||NSE_INDEX\||BSE_EQ\||" r"\bNSE:[A-Z0-9]+-(EQ|INDEX)\b|" r"\bif\d{2}[A-Z]",
    re.I,
)


@pytest.mark.parametrize(
    "planted",
    [
        {"rsi": "served_by_kite"},
        {"rsi": "yahoo"},
        {"access_token": "eyJhbGciOiJIUzI1NiJ9.SECRET"},
        {"owner_user_id": "6a5e6228aa11bb22cc33dd44"},
        {"user_id": "6a5e6228aa11bb22cc33dd44"},
        {"session_id": "sess_abc123"},
        {"broker_account_id": "ZG1234"},
        {"instrument_token": "NSE_EQ|INE002A01018"},
        {"rsi": "probation"},
        {"macd": "upstream said: 502 Bad Gateway from api.kite.trade"},
    ],
)
def test_nothing_but_a_state_survives_into_a_quality_map(planted):
    """The containment property, stated as an attempt rather than a promise.

    Each of these is a real identifier class the platform holds and must never
    publish: a provider name, a broker name, a broker account id, a credential,
    a user id, a session id, an internal connection identifier, a raw upstream
    error. None of them survives, and none of them survives for the same reason:
    the vocabulary is closed, so there is no channel at all.
    """
    sanitized = field_quality.sanitize(planted)
    blob = json.dumps(sanitized)

    assert not PROVIDER_NAMES.search(blob)
    assert not CREDENTIALS.search(blob)
    assert not PROVIDER_INTERNALS.search(blob)
    assert not BROKER_PRIVATE_IDS.search(blob)
    assert all(v in {q.value for q in FieldQuality} for v in sanitized.values())


def test_a_quality_map_is_not_a_provider_discriminator():
    """G-10 — field quality must not become a provider identity side-channel.

    A state is a function of the PAYLOAD, never of who produced it: two
    providers in the same situation produce the same state. Asserted by running
    one payload through every normalizer family and requiring one answer.
    """
    raw = {
        "symbol": "X",
        "price": 1.0,
        "last_price": 1.0,
        QUALITY_KEY: {"rsi": "insufficient_history"},
    }

    answers = {
        provider: normalize_stock_quote(dict(raw), provider=provider)[QUALITY_KEY] for provider in _PROVIDER_FAMILIES
    }

    assert len({json.dumps(a, sort_keys=True) for a in answers.values()}) == 1, answers


def test_the_scanner_caveat_names_only_the_scanners_own_vocabulary():
    """`unverified_filters` carries filter names, which the scanner already
    publishes to the browser in `STRATEGY_PRESETS` — never a value, a provider
    or a reason string."""
    bare = next(q for q in _universe() if q["symbol"] == "BARE")

    names = scanner_engine.unverified_filters(bare, {"rsi_min": 40, "rsi_max": 70})

    assert set(names) <= set(scanner_engine._FILTER_FIELD)
    assert not PROVIDER_NAMES.search(json.dumps(names))
    assert not PROVIDER_INTERNALS.search(json.dumps(names))


def test_a_scan_envelope_discloses_nothing_internal():
    result = _run(_scan(_universe()))
    blob = json.dumps(result, default=str)

    assert not PROVIDER_NAMES.search(blob)
    assert not CREDENTIALS.search(blob)
    assert not PROVIDER_INTERNALS.search(blob)
    assert not BROKER_PRIVATE_IDS.search(blob)


def test_the_disclosure_sweep_could_have_failed():
    """The planted-value check: if the regexes did not match a real leak, every
    assertion above would be vacuous."""
    assert PROVIDER_NAMES.search(json.dumps({"rsi": "yahoo"}))
    assert CREDENTIALS.search(json.dumps({"access_token": "x"}))
    assert PROVIDER_INTERNALS.search(json.dumps({"state": "probation"}))

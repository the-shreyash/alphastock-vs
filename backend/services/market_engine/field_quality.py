"""Per-field data quality for market data (D6.8-A / D6-Q1).

THE QUESTION THIS MODULE ANSWERS
────────────────────────────────
"May the platform describe this number as a measurement of the market?"

Before D6.8-A the answer was binary and implicit. `ranking_engine`'s
`dimension_is_supported` asked `quote.get(field) is not None`, and that single
predicate had to stand in for five genuinely different situations:

    a newly listed stock with no 26-bar MACD
    a broker feed that does not carry indicators at all
    a vendor that raised while supplying the bar series
    a real reading from a feed that stopped advancing this morning
    a field the payload simply did not contain

ADR-058 (D5.19) fixed the *instance* of this — the ranking engine stopped
saying "RSI 50 in bullish zone" about a stock whose RSI it never had — and said
in its own text that it had not fixed the *class*. The coalescing survived
(`quote.get("rsi") or 50.0`), the reason for an absence was discarded at the
producer, and the most dangerous of the five above was not expressible at all:
**a stale reading is a real number, so it scored as fresh.**

This module is the class fix. It carries the reason from the producer, which
alone knows it, to the consumer, which alone renders it.

WHAT IT IS NOT
──────────────
* **Not SourceTier.** `SourceTier` says how fresh a *feed's* tier claims to be
  (`streaming` | `delayed`) and is a property of the resolution. FieldQuality is
  a property of one value in one payload. A `streaming` quote can carry an
  INSUFFICIENT_HISTORY MACD, and a `delayed` one can carry an AVAILABLE RSI.
  They are orthogonal and neither substitutes for the other.
* **Not the feed state machine.** `FEED_AVAILABLE` / `FEED_RECOVERING` /
  `FEED_UNAVAILABLE` (source_manager) answer "can anything serve this
  capability". This module never reads or writes them.
* **Not AI freshness.** `ai_provenance.Freshness` ages an *artifact* a model
  produced. This ages a market-data reading.
* **Not a scoring input.** Nothing here changes a number. D6-Q1 is explicit:
  "Scores stay unchanged; only what the platform claims changes."

THE SIX STATES, AND WHY MISSING AND UNAVAILABLE ARE NOT THE SAME
────────────────────────────────────────────────────────────────
The three the repository already defines are taken verbatim from D6-Q1 in
`.claude/TASK.md` §G. The two it did not define are derived here from the
distinction the code already draws, and the derivation is the whole reason they
stay separate:

**UNAVAILABLE is record-scoped. MISSING is field-scoped.**

`fetch_all_universe_quotes` and `derive_technicals` already tell these apart
and then throw the difference away. A quote that arrives with no bar series at
all has no input for *any* indicator — nothing was served that could have
carried one — and that is UNAVAILABLE. A quote that arrives with a bar series
and no `volume` has an input for `avg_volume` and none for `volume_ratio`, and
that is MISSING. The same shape appears one layer up, which is where the
vocabulary comes from: `Freshness.UNAVAILABLE` in `ai_provenance` means "there
is no successful generation, so there is no age to report", and `available:
False` on a scan means "no quotes at all", never "this quote lacks a field".

Stated as a rule a producer can apply without guessing:

    UNAVAILABLE  the input record this field derives from was never served
    MISSING      the record was served and this field is not in it

MISSING is also the **safest default**, and it is what an undeclared `None`
resolves to (:func:`quality_of`). That is deliberate: MISSING is byte-for-byte
the meaning `value is None` already had, so a path that has not been taught to
classify cannot accidentally make a stronger claim than the old code made.

SECURITY CONTAINMENT
────────────────────
A quality map travels to the browser and into AI prompts, so it is governed by
MARKET_DATA_ARCHITECTURE.md's Developer Rules 4 and 5 exactly like `source_tier`
is. Two properties, both enforced here rather than documented:

1. **A closed vocabulary.** :func:`sanitize` is the only way a quality map is
   built, and it admits nothing but the six enum members keyed by a known field
   name. There is no channel through which a provider name, broker name, broker
   account id, credential, user id, session id, connection id or upstream
   exception string could ride along, because no value other than an enum member
   survives the boundary.
2. **No new discriminator.** A state is a function of the *payload*, never of
   who produced it. Two providers in the same situation produce the same state,
   and a consumer learns nothing from a quality map that it could not already
   learn from the value being `None` and from `source_tier`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional


class FieldQuality(str, Enum):
    """What the platform knows about one market-data field in one payload.

    `str` mixin so a state serialises to JSON and compares to a plain string
    without a converter at every boundary — the same choice `SourceTier`,
    `ProviderState` and `ai_provenance.GenerationStatus` already make.
    """

    #: A real measurement, from a reading current enough to be described as one.
    AVAILABLE = "available"

    #: The record was served and this field is not in it. The safest absence,
    #: and the meaning `value is None` carried before this module existed.
    MISSING = "missing"

    #: Computable in principle; the required historical window is not
    #: satisfied. RSI needs 15 bars, MACD 26, the volume baseline 21 — see
    #: `real_market._MIN_BARS_*`. A newly listed stock lands here, and telling a
    #: user that is a different (and true) sentence from "data unavailable".
    INSUFFICIENT_HISTORY = "insufficient_history"

    #: A real reading whose observation instant is older than
    #: :data:`DAILY_READING_MAX_AGE_SECONDS`. Derived on read, never persisted.
    STALE = "stale"

    #: The source failed while supplying this field's input. Distinct from
    #: MISSING because a degraded vendor must not read as a quiet market.
    PROVIDER_ERROR = "provider_error"

    #: The input record this field derives from was never served at all.
    UNAVAILABLE = "unavailable"


#: The one key a canonical payload uses to carry quality. One key, so the
#: payload shape stays identical across tiers — a key present on a delayed quote
#: and absent on a streaming one is a consumer able to tell which provider
#: answered, which is the leak `test_the_payload_shape_is_identical_across_tiers`
#: exists to prevent.
QUALITY_KEY = "field_quality"

#: When this payload's quality-tracked values were actually observed, ISO-8601.
#:
#: A real reading instant supplied by the producer — for the baseline path, the
#: timestamp of the newest bar the indicators were computed from. Never "now":
#: a payload with no honest observation instant carries none, and a field on it
#: is never classified STALE. That is `ai_provenance`'s rule 3 in this module's
#: shape — an unknown age is reported as unknown and never invented.
OBSERVED_AT_KEY = "observed_at"

#: The market-data fields whose quality the platform tracks.
#:
#: These six, and not every numeric key, because these are the ones an
#: intelligence surface turns into a *sentence*. `price` is rendered as a number
#: beside a tier badge and its absence is already visible; an RSI becomes "in a
#: healthy bullish zone" and a day change becomes "Strong +2.6% day move", and
#: both of those assert. Closed so a consumer iterating the map cannot be
#: surprised by a key, and so a quality map cannot become a place to stash
#: arbitrary strings.
QUALITY_TRACKED_FIELDS = (
    "rsi",
    "macd",
    "macd_signal",
    "avg_volume",
    "volume_ratio",
    "change_pct",
)

_TRACKED = frozenset(QUALITY_TRACKED_FIELDS)

#: How old a daily reading may be before intelligence must stop calling it
#: current.
#:
#: NOT A TUNED NUMBER — a derivation from what these fields are.
#:
#: Every quality-tracked field is computed from **daily** bars over
#: `real_market.TECHNICALS_RANGE`, so the reading a payload carries is the
#: newest daily bar, and it stays the operative reading until the series
#: advances to the next session. NSE runs one session per trading day at a fixed
#: clock, so consecutive bars are one day apart: a bar older than one day means
#: the series did not advance into a session that has since opened — the feed
#: failed to deliver today's bar. That is exactly D6-Q1's "a real reading from a
#: dead feed", and it is the only reading in the six that scores as fresh today.
#:
#: Checked against the live cadence: NSE daily bars are stamped at the 09:15 IST
#: open, so at 09:14 the newest bar is 23h59m old and still current, and by
#: 09:16 the new bar has arrived. The window therefore never fires during a
#: normal session and fires within a minute of a feed that has stopped.
#:
#: CONSEQUENCE, ACCEPTED AND DOCUMENTED: at 09:00 on a Monday the newest bar is
#: Friday's and these fields read STALE. That is correct and is the point —
#: Friday's RSI is a fact about Friday. It is not an error state and nothing is
#: hidden; the value still travels, and only the claim changes.
DAILY_READING_MAX_AGE_SECONDS = 24 * 3600


def sanitize(mapping: Any) -> Dict[str, str]:
    """The only constructor for a quality map, and the containment boundary.

    Admits a key only if it is a tracked field and a value only if it is one of
    the six states. Everything else — a provider name, an account id, an
    upstream exception string, a nested object, a field nobody declared — is
    dropped rather than carried, so there is no path by which a payload leaving
    the gateway acquires provenance through this key.
    """
    if not isinstance(mapping, Mapping):
        return {}
    out: Dict[str, str] = {}
    for field, state in mapping.items():
        if field not in _TRACKED:
            continue
        try:
            out[field] = FieldQuality(state).value
        except (ValueError, TypeError):
            continue
    return out


def quality_map(payload: Any) -> Dict[str, str]:
    """The sanitized quality map carried by `payload`, or an empty one."""
    if not isinstance(payload, Mapping):
        return {}
    return sanitize(payload.get(QUALITY_KEY))


def attach(payload: Dict[str, Any], mapping: Mapping[str, Any]) -> Dict[str, Any]:
    """Stamp a sanitized quality map onto `payload`, in place.

    Merges over whatever the payload already carried rather than replacing it,
    so a carrier that adds one field's state does not silently erase another's.
    """
    merged = {**quality_map(payload), **sanitize(mapping)}
    payload[QUALITY_KEY] = merged
    return payload


def declared(payload: Any, field: str) -> Optional[FieldQuality]:
    """The state the producer declared for `field`, if it declared one."""
    state = quality_map(payload).get(field)
    return FieldQuality(state) if state else None


def parse_instant(value: Any) -> Optional[datetime]:
    """Parse an ISO observation instant, or return None.

    Returns None for anything that is not an unambiguous instant — a naive
    `datetime` included, because reading a naive timestamp as UTC is a guess,
    and a guessed age is how a stale reading would be certified fresh. A None
    here means "no age is known", never "age zero".
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def observation_age_seconds(payload: Any, *, now: Optional[datetime] = None) -> Optional[float]:
    """How old this payload's quality-tracked reading is, in seconds.

    None when the payload carries no parseable observation instant. A reading
    stamped in the future yields a negative age and is therefore never stale:
    a clock that disagrees is a clock problem, and inventing staleness from one
    would refuse good data on the strength of a skew nobody measured.
    """
    if not isinstance(payload, Mapping):
        return None
    observed_at = parse_instant(payload.get(OBSERVED_AT_KEY))
    if observed_at is None:
        return None
    return ((now or datetime.now(timezone.utc)) - observed_at).total_seconds()


def quality_of(payload: Any, field: str, *, now: Optional[datetime] = None) -> FieldQuality:
    """The state of `field` on `payload`, derived on read.

    Three rules, in order:

    1. A **declared non-AVAILABLE** state wins outright. The producer knew why
       the value is absent and nothing downstream may overrule it.
    2. A value of `None` with nothing declared is MISSING — the safest absence,
       and precisely what `value is None` meant before this module existed.
    3. A real value is AVAILABLE unless its observation instant makes it
       :data:`FieldQuality.STALE`. Staleness is derived here and never
       persisted: nothing mutates a stored value because time passed, and the
       same payload read an hour later answers differently because it *is*
       an hour older.
    """
    if not isinstance(payload, Mapping):
        return FieldQuality.MISSING

    state = declared(payload, field)
    if state is not None and state is not FieldQuality.AVAILABLE:
        return state

    if payload.get(field) is None:
        return FieldQuality.MISSING

    age = observation_age_seconds(payload, now=now)
    if age is not None and age > DAILY_READING_MAX_AGE_SECONDS:
        return FieldQuality.STALE
    return FieldQuality.AVAILABLE


def is_available(payload: Any, field: str, *, now: Optional[datetime] = None) -> bool:
    """Whether `field` may be described as a measurement of the market.

    AVAILABLE and nothing else. This is the predicate every intelligence
    surface asks before it renders a sentence about a field.
    """
    return quality_of(payload, field, now=now) is FieldQuality.AVAILABLE


def reading(payload: Any, field: str, *, now: Optional[datetime] = None) -> Optional[Any]:
    """The value of `field` when it is a real reading, otherwise None.

    For consumers that render or narrate. Never returns a substitute: a caller
    that gets None here has nothing to say about this field, which is the whole
    point.
    """
    if not is_available(payload, field, now=now):
        return None
    return payload.get(field)


def scoring_input(payload: Any, field: str, placeholder: Any, *, now: Optional[datetime] = None) -> Any:
    """The number a scorer's arithmetic uses. **Never** evidence.

    WHY THIS EXISTS RATHER THAN `quote.get(field) or placeholder`
    -------------------------------------------------------------
    Numerically it is that expression, deliberately and exactly — including its
    treatment of a falsy real value, which is preserved so that no score moves
    in this phase (see LIM-D6.8A-1). What changes is that the substitution is
    no longer *invisible*. `rsi = quote.get("rsi") or 50.0` reads like a
    reading with a default; it is the single line ADR-058 identified as "one bug
    written eight times", and it is the line a future author copies. Spelled as
    a call with `placeholder` in the signature, the substitution is named at
    every site, and `is_available` — not this function — is what decides whether
    the platform may say anything about the field.

    The separation is the D5.19 architecture restated: the score is computed
    either way, because withholding it would change what the engine
    *recommends*; the claim is withheld, because making it would change what
    the platform *says* — and only one of those is this phase's business.
    """
    value = payload.get(field) if isinstance(payload, Mapping) else None
    return placeholder if not value else value


#: How each state reads to a person or a model.
#:
#: Plain English, no provider vocabulary, and no internal state names: this text
#: reaches AI prompts and the browser, where `PROVIDER_INTERNALS` in
#: `test_d519_surface_disclosure.py` is the standing rule.
_DESCRIPTIONS = {
    FieldQuality.AVAILABLE: "available",
    FieldQuality.MISSING: "not available",
    FieldQuality.INSUFFICIENT_HISTORY: "not enough price history to compute",
    FieldQuality.STALE: "last reading is out of date",
    FieldQuality.PROVIDER_ERROR: "could not be retrieved",
    FieldQuality.UNAVAILABLE: "not available from this data source",
}


def describe(state: FieldQuality) -> str:
    """A human- and model-readable phrase for `state`."""
    return _DESCRIPTIONS.get(state, _DESCRIPTIONS[FieldQuality.MISSING])


def describe_field(payload: Any, field: str, *, now: Optional[datetime] = None) -> str:
    """`field`'s phrase — its value when real, otherwise why it is not.

    The one formatter for every surface that has to say something about a
    field it may not have, so the ranking evidence, the advisor narrative and
    an AI prompt cannot come to disagree about how absence is worded.
    """
    state = quality_of(payload, field, now=now)
    if state is FieldQuality.AVAILABLE:
        return f"{payload.get(field)}"
    return describe(state)


def claim(reasons: list, supported: bool, text: str) -> None:
    """Append `text` to `reasons` only when the reading behind it is real.

    THE RULE THIS ENCODES: a sentence may not outlive its reading.

    Every scorer in the platform has the same shape — a branch that adjusts a
    number and then appends a clause explaining it — and the two halves have
    different rules. The number is computed either way, because withholding it
    would change what the platform *recommends*. The clause is withheld,
    because publishing it would change what the platform *says* about a
    company, and that is the half ADR-058 is about.

    A helper rather than an `if supported:` beside each `append` for two
    reasons, both practical: the gate cannot be forgotten at a branch added
    later, and the branch count of the scorer stays where it was before the
    gating existed — a scorer that trips a complexity limit is a scorer somebody
    will "simplify" by deleting the gates.
    """
    if supported:
        reasons.append(text)


def all_missing(fields: Iterable[str], state: FieldQuality) -> Dict[str, str]:
    """A quality map assigning one state to several fields.

    The producer-side convenience for "none of these could be computed, and
    here is the single reason why" — a whole-record verdict written once
    instead of repeated per field, which is where the reasons drift apart.
    """
    return sanitize({field: state for field in fields})

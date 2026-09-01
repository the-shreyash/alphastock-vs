"""What the surfaces D5.19 added are allowed to say (Phase 7).

This sprint put five new strings in front of a user: the ranking evidence, the
scanner's match reasons, the morning report's session line, the order review,
and the stock detail tier badge. Each is generated from internal state, and
generated text is the classic route by which internal state escapes — a leak
here would not look like a leak, it would look like a sentence.

The disclosure rules being enforced, and where each comes from:

* **No provider name, anywhere** (MARKET_DATA_ARCHITECTURE.md Developer Rule 4).
  `source_tier` is the only provenance a response may carry. The evidence
  strings are the risk: they are assembled from dimensions whose data came from
  a named provider, and "MACD bullish crossover (broker feed)" would be a
  perfectly natural thing for a future author to add.

* **No provider *state*.** Health, probation, cooldown, latency percentiles and
  shard identity are the Source Manager's internals. D5.2 is explicit that
  probation is a ranking term with no user-facing meaning, and a user told
  their prices are "on probation" would reasonably conclude something is wrong
  with their account.

* **No credential material of any kind**, and no raw upstream exception — the
  order path is the one place in this sprint that talks to a broker's API and
  therefore the one place a raw error object could carry request context.

* **No broker-private instrument identifiers.** An Upstox instrument key, a
  Kite numeric token, a Fyers HSM topic or a Dhan security id are matched, not
  published (D4.3). They also *name the broker by their shape alone*, which is
  Rule 4 defeated by format rather than by string.
"""
import asyncio
import json
import re

import pytest

from services.market_engine import ranking_engine, scanner_engine
from services import morning_report


def _run(coro):
    return asyncio.run(coro)


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

#: Identifier shapes that name a broker by their format alone.
BROKER_PRIVATE_IDS = re.compile(
    r"NSE_EQ\||NSE_INDEX\||BSE_EQ\||"        # Upstox instrument keys
    r"\bNSE:[A-Z0-9]+-(EQ|INDEX)\b|"          # Fyers HSM topics
    r"\bif\d{2}[A-Z]",                        # Fyers HSM channel prefixes
    re.I,
)

QUOTE = {
    "symbol": "RELIANCE", "name": "Reliance Industries", "price": 1300.0,
    "change_pct": 2.5, "sector": "Oil & Gas", "rsi": 55.0, "macd": 3.0,
    "macd_signal": 1.0, "avg_volume": 8_000_000, "volume_ratio": 1.8,
    # Deliberately planted: a normalized quote must not be carrying these, and
    # if one ever does, the payload assembled from it must still not publish
    # them.
    "instrument_token": "NSE_EQ|INE002A01018",
    "access_token": "eyJhbGciOiJIUzI1NiJ9.SECRET",
    "owner_user_id": "6a5e6228aa11bb22cc33dd44",
    "probation": True,
}


class _Gateway:
    async def get_universe_quotes(self, *, user_id=None):
        return [dict(QUOTE)]

    async def get_sectors(self, *, user_id=None):
        return [{"name": "Oil & Gas", "change_pct": 1.2}]

    def source_tier(self, _c=None, *, user_id=None):
        return "streaming"


@pytest.fixture
def gateway(monkeypatch):
    import services.market_engine.gateway as gm

    monkeypatch.setattr(gm, "market_gateway", _Gateway())

    async def _publish(*_a, **_k):
        return None

    monkeypatch.setattr(ranking_engine.event_bus, "publish", _publish)
    monkeypatch.setattr(scanner_engine.event_bus, "publish", _publish)


def _evidence_text(rankings):
    """Only the generated prose — the strings a user actually reads.

    Scoped deliberately. Asserting over the whole payload would also flag the
    planted fields above, which is a different (and pre-existing) question about
    what the normalizer passes through; this file is about what the SENTENCES
    this sprint wrote are allowed to contain.
    """
    return " ".join(
        item["reason"]
        for row in rankings
        for item in row.get("evidence", [])
    )


# --------------------------------------------------------------------------- #
# The ranking explanation                                                      #
# --------------------------------------------------------------------------- #

def test_opportunity_evidence_names_no_provider(gateway):
    result = _run(ranking_engine.rank_universe_report(top_n=5, user_id="u1"))

    assert not PROVIDER_NAMES.search(_evidence_text(result["rankings"]))


def test_opportunity_evidence_discloses_no_credential(gateway):
    result = _run(ranking_engine.rank_universe_report(top_n=5, user_id="u1"))

    assert not CREDENTIALS.search(_evidence_text(result["rankings"]))


def test_opportunity_evidence_discloses_no_provider_state(gateway):
    """Probation, cooldown and health are ranking internals, not user language."""
    result = _run(ranking_engine.rank_universe_report(top_n=5, user_id="u1"))

    assert not PROVIDER_INTERNALS.search(_evidence_text(result["rankings"]))


def test_opportunity_evidence_carries_no_broker_private_identifier(gateway):
    result = _run(ranking_engine.rank_universe_report(top_n=5, user_id="u1"))

    assert not BROKER_PRIVATE_IDS.search(_evidence_text(result["rankings"]))


def test_the_ranking_envelope_states_only_the_tier(gateway):
    """The envelope this sprint added — not the rows, which predate it."""
    result = _run(ranking_engine.rank_universe_report(top_n=5, user_id="u1"))
    envelope = {k: v for k, v in result.items() if k != "rankings"}

    assert result["source_tier"] == "streaming"
    assert not PROVIDER_NAMES.search(json.dumps(envelope))
    assert not CREDENTIALS.search(json.dumps(envelope))


# --------------------------------------------------------------------------- #
# The scanner explanation                                                      #
# --------------------------------------------------------------------------- #

def test_scanner_match_reasons_disclose_nothing_internal(gateway):
    result = _run(scanner_engine.scan(
        filters={"rsi_min": 40, "rsi_max": 70, "volume_ratio_min": 1.2},
        limit=5, user_id="u1", publish=False,
    ))
    text = " ".join(r for row in result["results"] for r in row["matched_on"])

    assert text, "the scan matched nothing, so this test proved nothing"
    assert not PROVIDER_NAMES.search(text)
    assert not CREDENTIALS.search(text)
    assert not PROVIDER_INTERNALS.search(text)
    assert not BROKER_PRIVATE_IDS.search(text)


# --------------------------------------------------------------------------- #
# The morning report's session line                                            #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("market_open", [True, False])
def test_the_session_instruction_discloses_nothing_internal(market_open):
    text = morning_report._session_instruction(market_open, "10:26 IST")

    assert not PROVIDER_NAMES.search(text)
    assert not CREDENTIALS.search(text)
    assert not PROVIDER_INTERNALS.search(text)


def test_the_session_context_carries_only_a_clock_answer(monkeypatch):
    monkeypatch.setattr(morning_report, "_market_is_open", lambda: True)

    context = morning_report.build_session_context()

    assert set(context) == {"market_open", "observed_at", "observed_at_str"}
    assert not PROVIDER_NAMES.search(json.dumps(context))

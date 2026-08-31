"""D5.17 — the boundaries a second instrument segment must not have crossed.

Every sprint since D4.2 has added a way for broker knowledge to leak upward, and
every one has been caught by a sweep rather than by a reviewer. D5.17 adds two
new opportunities and this file closes both:

* an index is the first instrument whose **canonical name differs from the
  broker's**, so the temptation is a translation table on the market side;
* an index is the first instrument the platform names as a *constant*, so the
  temptation is a per-broker constant in a shared module.

It also sweeps the surface the sprint touched for credentials at DEBUG, because
the index path runs the same downloads and the same subscription frames the
equity path does.
"""

import ast
import logging
import pathlib
import re

import pytest

from tests.test_broker_framework import _strip_comments_and_strings as _strip_source
from tests.test_broker_streaming import _executable_strings

BACKEND = pathlib.Path(__file__).resolve().parent.parent
MARKET_ENGINE = BACKEND / "services" / "market_engine"
BROKERS = BACKEND / "services" / "brokers"

#: Names no module on the market side of the line may ACT on.
#:
#: Matched against identifiers and against executable string literals, never
#: against prose — the repository's established line (see
#: `_executable_strings`). A market module may explain in a docstring why a
#: broker's index naming forced a table; what it may not do is contain
#: `if broker == "zerodha"`, which survives an import ban and an identifier
#: sweep untouched.
BROKER_NAMES = re.compile(
    r"\b(zerodha|kite|upstox|angelone|smartapi|fyers|dhan|hsm)\b", re.IGNORECASE)


def _sources(root):
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


# ==================================================================
# A. The market side still knows no broker
# ==================================================================


def test_the_market_engine_never_imports_a_broker_module():
    """Re-asserted for D5.17, not assumed: the index work touched
    `market_engine.ticks` consumers and `heartbeat_engine`, and the shortest
    path to "resolve an index" is an import of the catalogue."""
    for path in _sources(MARKET_ENGINE):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("services.brokers"):
                pytest.fail(f"{path.relative_to(BACKEND)} imports {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("services.brokers"), path


def test_no_broker_name_is_actionable_anywhere_in_the_market_engine():
    """Identifiers and executable literals, per the established sweep.

    Re-run for D5.17 rather than assumed: an index is the first instrument whose
    canonical name differs from the broker's, so the shortest implementation of
    "resolve NIFTY" is a per-broker branch on the market side. This is the sweep
    that would have caught it.
    """
    for path in _sources(MARKET_ENGINE):
        source = path.read_text()
        relative = path.relative_to(BACKEND)
        assert not BROKER_NAMES.findall(_strip_source(source)), (
            f"{relative} contains a broker name as code")
        literals = " ".join(_executable_strings(source))
        assert not BROKER_NAMES.findall(literals), (
            f"{relative} contains a broker name in an executable string literal")


def test_the_canonical_tick_contract_was_not_widened():
    """D5.17 explicitly refused to widen `MarketTick`. An index level is a
    price, `exchange` already names NSE/BSE, and `volume` is already optional —
    so a new field would have been convenience, not necessity."""
    from services.market_engine.ticks import MarketTick

    assert {f for f in MarketTick.__dataclass_fields__} == {
        "symbol", "price", "exchange", "volume", "ingested_at"}


def test_an_index_level_is_representable_as_a_canonical_tick():
    """The other half of the same claim: refusing to widen is only correct if
    the existing contract can carry the values. SENSEX is the largest number the
    dashboard renders and India VIX among the smallest."""
    from services.market_engine.ticks import MarketInstrument, MarketTick

    for symbol, exchange, price in (
        ("SENSEX", "BSE", 81020.55),
        ("NIFTY", "NSE", 24815.25),
        ("INDIAVIX", "NSE", 12.85),
    ):
        tick = MarketTick.create(MarketInstrument.of(symbol, exchange), price)
        assert tick.as_dict()["price"] == price
        assert "instrument_token" not in tick.as_dict()


# ==================================================================
# B. The index policy is shared, not per broker
# ==================================================================


def test_the_index_name_table_lives_in_exactly_one_module():
    """Five copies of a spelling table would be five chances to disagree, and
    they would disagree silently — the same argument `catalogue.py` makes for
    the cash-series policy. An adapter may name its own *discriminator* (Kite's
    `INDICES` segment, Angel's `AMXIDX`); it may not name the platform's
    indices."""
    from services.brokers.catalogue import INDEX_ALIASES

    spellings = {alias for aliases in INDEX_ALIASES.values() for alias in aliases}
    # The canonical symbols themselves are legitimate elsewhere; the *other*
    # spellings are what only the table may hold.
    broker_only = {s for s in spellings if s not in INDEX_ALIASES}

    for path in _sources(BROKERS):
        if path.name == "catalogue.py":
            continue
        # Executable literals only: an adapter's docstring may quote its own
        # master's spelling as evidence — that is the documentation doing its
        # job — while a literal it can compare against is the duplication.
        code = " ".join(_executable_strings(path.read_text()))
        for spelling in broker_only:
            assert spelling not in code, (
                f"{path.relative_to(BACKEND)} carries the index spelling "
                f"{spelling!r} — it belongs in catalogue.INDEX_ALIASES"
            )


def test_no_adapter_hardcodes_an_index_identifier():
    """A token pasted into an adapter is a catalogue that cannot go stale and
    cannot be checked. Every index identifier must come from a parsed master
    row, which is what makes a renumbering at the broker a download away from
    being correct rather than a release away."""
    tokens = ("256265", "260105", "264969", "99926000", "99926009", "99926017",
              "99919000", "26000", "26009", "26017")
    for path in _sources(BROKERS):
        code = _strip_source(path.read_text()) + " ".join(_executable_strings(path.read_text()))
        for token in tokens:
            assert token not in code, (
                f"{path.relative_to(BACKEND)} hardcodes the index identifier {token}"
            )


# ==================================================================
# C. Credentials, at DEBUG, on the paths D5.17 runs
# ==================================================================

FAKE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.D517FAKEd517fake.sIgNaTuReFaKe999"


def test_planning_an_index_subscription_logs_no_credential(caplog):
    """`_plan_tick_subscription` now carries four more instruments through the
    same gateway call, the same catalogue and the same instrument map. The
    session is the thing that must never reach a log line, and this drives the
    real method with a real session dict rather than describing it.
    """
    from services import broker_engine as module
    from services.brokers.registry import broker_registry
    from tests.test_broker_streaming import run
    from tests.test_index_feed_routing import EXPECTED, _adapter

    session = {"access_token": FAKE_TOKEN, "api_key": "ak_FAKE_d517",
               "client_id": "AB9999"}
    engine = module.BrokerEngine()
    engine.db = None

    async def _no_watchlist(user_id):
        return []

    engine._feed_watchlist_symbols = _no_watchlist

    with caplog.at_level(logging.DEBUG):
        for broker in sorted(EXPECTED):
            row_groups, _ = EXPECTED[broker]
            index = _adapter(broker).build_catalogue_index(*row_groups)
            adapter_cls = type(broker_registry.get(broker))
            original = adapter_cls._instrument_catalogue

            async def _catalogue(self, _index=index):
                return _index

            adapter_cls._instrument_catalogue = _catalogue
            try:
                run(engine._plan_tick_subscription(
                    "u1", broker, session, holdings=[], positions=[]))
            finally:
                adapter_cls._instrument_catalogue = original

    text = "\n".join(record.getMessage() for record in caplog.records)
    for needle in session.values():
        assert needle not in text, f"{needle[:14]}… leaked into a log line"


def test_a_canonical_index_tick_carries_no_broker_identifier():
    """The D4.3 containment property, on the newest instrument kind. Each
    broker's index identifier has a different shape and every one of them must
    stop at the boundary."""
    from services.brokers.instruments import InstrumentMap, canonical_ticks

    identifiers = (256265, "1|99926000", "IDX_I|13", "NSE_INDEX|Nifty 50",
                   "if|nse_cm|26000")
    for identifier in identifiers:
        instrument_map = InstrumentMap.from_portfolio(catalogue={"NIFTY": identifier})
        ticks = canonical_ticks(
            [{"instrument_token": identifier, "last_price": 24815.25}],
            instrument_map, broker="broker")
        assert len(ticks) == 1
        assert ticks[0]["symbol"] == "NIFTY"
        assert str(identifier) not in str(ticks[0]), (
            f"the broker identifier {identifier!r} survived the boundary")

"""Sprint D5.17 — indices are broker instruments, and they reach the dashboard.

WHAT D5.17 CLAIMS, AND WHERE EACH CLAIM IS PROVED
--------------------------------------------------
D5.16 left the index strip on the delayed baseline and said why: the catalogue
resolved one segment. The audit that opened D5.17 found that the *brokers* were
never the obstacle — all five publish NIFTY, BANKNIFTY, SENSEX and INDIA VIX in
the same masters the equity catalogue already downloads, in segments whose ticks
the five codecs already decode. The obstacle was entirely on this side of the
line, in three places:

  1. the catalogue key had no segment, so an index had nowhere to be filed;
  2. the feed universe had no index source, so nothing asked for one;
  3. every master spells the indices differently from the platform.

(3) is the one that is not bookkeeping and is the reason this file exists at all.
An equity's canonical symbol *is* its trading symbol at every broker; an index's
is not — `NIFTY` is `"NIFTY 50"` in Kite's dump and `"NIFTY"` in the other four,
`INDIAVIX` is `"INDIA VIX"` in four and `"INDIAVIX"` in one. A catalogue that
matched on identity would have resolved indices at *some* brokers and silently
not at others, which is the worst of the three possible outcomes.

WHAT IS NOT PROVED HERE, STATED PLAINLY
----------------------------------------
No test in this file connects to a broker, and none can. The fixture rows are
verbatim-shaped extracts of the five live published masters, checked against them
on 2026-08-31, so what is proved is that the parsers turn real master rows into
the right identifiers. Whether a broker's socket then *delivers* an index tick
for those identifiers is a live-session fact, recorded as unverified in TASK.md
— and, for Fyers, as a specific named risk: its index topic prefix (`if` rather
than `sf`) is documented behaviour this repository has never observed.
"""

import pytest

from services.brokers.catalogue import (
    EQUITY_SEGMENT,
    INDEX_ALIASES,
    INDEX_EXCHANGES,
    INDEX_SEGMENT,
    InstrumentCatalogue,
    canonical_index,
    resolve_from_index,
)
from services.brokers.feed_universe import (
    FeedInstrument,
    build_feed_universe,
    index_instruments,
)
from tests.test_broker_streaming import run


# ==================================================================
# A. The canonical index name table
# ==================================================================


def test_every_index_the_platform_names_has_an_exchange_and_aliases():
    """The two tables are one statement and must not drift: an index with a
    spelling table and no exchange resolves against the wrong master, and one
    with an exchange and no spellings resolves at no broker."""
    assert set(INDEX_EXCHANGES) == set(INDEX_ALIASES)
    assert set(INDEX_EXCHANGES) == {"NIFTY", "BANKNIFTY", "SENSEX", "INDIAVIX"}
    assert INDEX_EXCHANGES["SENSEX"] == "BSE", "SENSEX is a BSE instrument"
    assert all(v in ("NSE", "BSE") for v in INDEX_EXCHANGES.values())


@pytest.mark.parametrize("spelling,canonical", [
    # Every spelling that appears in a live master, per broker.
    ("NIFTY 50", "NIFTY"),        # Kite tradingsymbol
    ("Nifty 50", "NIFTY"),        # Angel One / Upstox `name`
    ("NIFTY", "NIFTY"),           # Angel/Dhan/Upstox/Fyers identity column
    ("NIFTY BANK", "BANKNIFTY"),  # Kite
    ("Nifty Bank", "BANKNIFTY"),  # Upstox `name`
    ("BANKNIFTY", "BANKNIFTY"),
    ("SENSEX", "SENSEX"),
    ("BSE SENSEX", "SENSEX"),     # Upstox `name`
    ("INDIA VIX", "INDIAVIX"),    # Kite / Angel / Dhan / Upstox
    ("INDIAVIX", "INDIAVIX"),     # Fyers
    ("  india   vix  ", "INDIAVIX"),
])
def test_a_masters_own_spelling_resolves_to_the_platform_symbol(spelling, canonical):
    assert canonical_index(spelling) == canonical


@pytest.mark.parametrize("other", [
    # Real neighbours in the same segment of the same files. Each one is a
    # symbol a looser rule would have swallowed.
    "NIFTY 500", "NIFTY 100", "NIFTY 50 EQUAL WEIGHT", "NIFTY MIDCAP 50",
    "NIFTY PVT BANK", "BANKEX", "NIFTY NEXT 50", "SENSEX 50", "BSE100",
    "NIFTY25SEPFUT", "", "   ", None, True, 50,
])
def test_an_index_the_platform_does_not_name_resolves_to_nothing(other):
    """A closed table, not a filter. `NIFTY 500` is one space away from
    `NIFTY 50` and is a different instrument; a normalizer that stripped
    whitespace to make `"NIFTY 50"` match `"NIFTY"` would also have made
    `"NIFTY 500"` match `"NIFTY500"` and then argued about the rest."""
    assert canonical_index(other) is None


# ==================================================================
# B. The segmented catalogue key
# ==================================================================


def test_an_index_and_an_equity_of_one_name_are_two_entries():
    """The reason the key gained a segment. Without it these collide, and the
    collision resolves to whichever the parser offered second — an index level
    published under a share's name, or the reverse."""
    catalogue = InstrumentCatalogue()
    catalogue.offer("NSE", "SENSEX", "equity-id", series="EQ")
    catalogue.offer("NSE", "SENSEX", "index-id", rank=0, segment=INDEX_SEGMENT)
    index = catalogue.build()
    assert index[(EQUITY_SEGMENT, "NSE", "SENSEX")] == "equity-id"
    assert index[(INDEX_SEGMENT, "NSE", "SENSEX")] == "index-id"


def test_two_rows_claiming_one_index_are_dropped_not_guessed():
    """An index has no series, so every candidate ranks equally and the
    ambiguity rule applies unchanged. Reached through the real branch: both
    rows are accepted candidates, so deleting the tie check turns this red."""
    catalogue = InstrumentCatalogue()
    catalogue.offer("NSE", "NIFTY", "first", rank=0, segment=INDEX_SEGMENT)
    catalogue.offer("NSE", "NIFTY", "second", rank=0, segment=INDEX_SEGMENT)
    assert catalogue.build() == {}


def test_a_segment_the_platform_does_not_support_is_refused_at_offer():
    catalogue = InstrumentCatalogue()
    assert catalogue.offer("NSE", "GOLD26OCTFUT", "id", rank=0, segment="FUTURES") is False
    assert catalogue.build() == {}


def test_resolution_reads_the_segment_off_the_instrument():
    """An equity instrument must not be answered from the index half of the
    catalogue, nor the reverse — which is what a defaulted segment would do."""
    index = {
        (EQUITY_SEGMENT, "NSE", "SENSEX"): "equity-id",
        (INDEX_SEGMENT, "BSE", "SENSEX"): "index-id",
    }
    assert resolve_from_index(
        [FeedInstrument.of("SENSEX", "BSE", INDEX_SEGMENT)], index) == {"SENSEX": "index-id"}
    assert resolve_from_index(
        [FeedInstrument.of("SENSEX", "NSE")], index) == {"SENSEX": "equity-id"}


def test_an_instrument_that_names_no_segment_resolves_to_nothing():
    """Not defaulted to EQUITY. A caller passing a bare object is a caller this
    contract does not cover, and answering it with an equity lookup is exactly
    how an index would silently resolve to a share of the same name."""

    class _Bare:
        symbol = "SENSEX"
        exchange = "BSE"

    assert resolve_from_index([_Bare()], {(EQUITY_SEGMENT, "BSE", "SENSEX"): "x"}) == {}


# ==================================================================
# C. The feed universe carries the four indices
# ==================================================================


def test_the_index_universe_is_exchange_and_segment_qualified():
    instruments = index_instruments()
    assert {i.symbol for i in instruments} == set(INDEX_EXCHANGES)
    assert all(i.segment == INDEX_SEGMENT for i in instruments)
    assert {(i.symbol, i.exchange) for i in instruments} == {
        ("NIFTY", "NSE"), ("BANKNIFTY", "NSE"), ("SENSEX", "BSE"), ("INDIAVIX", "NSE"),
    }


def test_an_unsupported_segment_yields_no_feed_instrument():
    """MCX gold is refused on the exchange; a commodity *segment* on a supported
    exchange must be refused too, or a caller could name one and have it enter a
    subscription no adapter resolves."""
    assert FeedInstrument.of("GOLD", "NSE", "COMMODITY") is None
    assert FeedInstrument.of("NIFTY", "NSE", INDEX_SEGMENT) is not None


def test_indices_outrank_the_dashboard_set_but_not_the_account_itself():
    """Position in the order IS the priority statement: the ceiling trims from
    the end, so this is what decides which instruments an over-subscribed
    account loses."""
    universe = build_feed_universe(
        holdings=[{"symbol": "RELIANCE", "exchange": "BSE"}],
        watchlist=["TCS"],
        indices=index_instruments(),
        dashboard=["INFY", "WIPRO"],
    )
    symbols = [i.symbol for i in universe]
    assert symbols[:2] == ["RELIANCE", "TCS"]
    assert symbols[-2:] == ["INFY", "WIPRO"]
    assert set(symbols[2:-2]) == set(INDEX_EXCHANGES)


def test_omitting_indices_reproduces_the_d516_universe_exactly():
    """Widening stayed opt-in per caller. A caller that does not ask for indices
    gets the universe it got before D5.17, which is what made this safe to add
    beneath five adapters at once."""
    assert build_feed_universe(watchlist=["TCS"], dashboard=["INFY"]) == (
        FeedInstrument.of("TCS"), FeedInstrument.of("INFY"))


def test_the_index_universe_is_still_bounded_by_the_ceiling():
    universe = build_feed_universe(
        watchlist=[f"SYM{i}" for i in range(50)],
        indices=index_instruments(),
        limit=10,
    )
    assert len(universe) == 10


# ==================================================================
# D. Per-adapter index parsing, from real master shapes
# ==================================================================
#
# Rows are shaped exactly as the live published masters carry them, including
# the columns that make each broker's index row *distinguishable from its own
# equity rows* — which is the discriminator each parser is responsible for and
# the one thing a wrong fixture would hide.

KITE_INDEX_ROWS = [
    # `instrument_type: "EQ"` on an index is not a typo: it is what Kite
    # publishes, and it is why the equity branch keys on the segment.
    {"instrument_token": "256265", "tradingsymbol": "NIFTY 50", "name": "NIFTY 50",
     "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE"},
    {"instrument_token": "260105", "tradingsymbol": "NIFTY BANK", "name": "NIFTY BANK",
     "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE"},
    {"instrument_token": "264969", "tradingsymbol": "INDIA VIX", "name": "INDIA VIX",
     "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE"},
    {"instrument_token": "265", "tradingsymbol": "SENSEX", "name": "SENSEX",
     "instrument_type": "EQ", "segment": "INDICES", "exchange": "BSE"},
    {"instrument_token": "268041", "tradingsymbol": "NIFTY 500", "name": "NIFTY 500",
     "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE"},
    {"instrument_token": "738561", "tradingsymbol": "RELIANCE", "name": "RELIANCE",
     "instrument_type": "EQ", "segment": "NSE", "exchange": "NSE"},
]

ANGEL_INDEX_ROWS = [
    {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY",
     "instrumenttype": "AMXIDX", "exch_seg": "NSE"},
    {"token": "99926009", "symbol": "Nifty Bank", "name": "BANKNIFTY",
     "instrumenttype": "AMXIDX", "exch_seg": "NSE"},
    {"token": "99926017", "symbol": "India VIX", "name": "INDIA VIX",
     "instrumenttype": "AMXIDX", "exch_seg": "NSE"},
    {"token": "99919000", "symbol": "SENSEX", "name": "SENSEX",
     "instrumenttype": "AMXIDX", "exch_seg": "BSE"},
    {"token": "99926004", "symbol": "Nifty 500", "name": "NIFTY 500",
     "instrumenttype": "AMXIDX", "exch_seg": "NSE"},
    {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE",
     "instrumenttype": "", "exch_seg": "NSE"},
]

DHAN_INDEX_ROWS = [
    {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "I", "SEM_SMST_SECURITY_ID": "13",
     "SEM_INSTRUMENT_NAME": "INDEX", "SEM_TRADING_SYMBOL": "NIFTY", "SEM_SERIES": "X"},
    {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "I", "SEM_SMST_SECURITY_ID": "25",
     "SEM_INSTRUMENT_NAME": "INDEX", "SEM_TRADING_SYMBOL": "BANKNIFTY", "SEM_SERIES": "X"},
    {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "I", "SEM_SMST_SECURITY_ID": "21",
     "SEM_INSTRUMENT_NAME": "INDEX", "SEM_TRADING_SYMBOL": "INDIA VIX", "SEM_SERIES": "X"},
    {"SEM_EXM_EXCH_ID": "BSE", "SEM_SEGMENT": "I", "SEM_SMST_SECURITY_ID": "51",
     "SEM_INSTRUMENT_NAME": "INDEX", "SEM_TRADING_SYMBOL": "SENSEX", "SEM_SERIES": "X"},
    {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "I", "SEM_SMST_SECURITY_ID": "1",
     "SEM_INSTRUMENT_NAME": "INDEX", "SEM_TRADING_SYMBOL": "NIFTY MIDCAP 150",
     "SEM_SERIES": "X"},
    {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "E", "SEM_SMST_SECURITY_ID": "2885",
     "SEM_INSTRUMENT_NAME": "EQUITY", "SEM_TRADING_SYMBOL": "RELIANCE", "SEM_SERIES": "EQ"},
]

UPSTOX_INDEX_NSE_ROWS = [
    {"segment": "NSE_INDEX", "exchange": "NSE", "instrument_key": "NSE_INDEX|Nifty 50",
     "trading_symbol": "NIFTY", "instrument_type": "INDEX"},
    {"segment": "NSE_INDEX", "exchange": "NSE", "instrument_key": "NSE_INDEX|Nifty Bank",
     "trading_symbol": "BANKNIFTY", "instrument_type": "INDEX"},
    {"segment": "NSE_INDEX", "exchange": "NSE", "instrument_key": "NSE_INDEX|India VIX",
     "trading_symbol": "INDIA VIX", "instrument_type": "INDEX"},
    {"segment": "NSE_INDEX", "exchange": "NSE", "instrument_key": "NSE_INDEX|Nifty 500",
     "trading_symbol": "NIFTY 500", "instrument_type": "INDEX"},
    {"segment": "NSE_EQ", "exchange": "NSE", "instrument_key": "NSE_EQ|INE002A01018",
     "trading_symbol": "RELIANCE", "instrument_type": "EQ"},
]

UPSTOX_INDEX_BSE_ROWS = [
    {"segment": "BSE_INDEX", "exchange": "BSE", "instrument_key": "BSE_INDEX|SENSEX",
     "trading_symbol": "SENSEX", "instrument_type": "INDEX"},
]

# Headerless: 0 fyToken, 9 exchange-qualified ticker, 13 underlying name.
FYERS_INDEX_ROWS = [
    ["101000000026000", "NIFTY 50", "10", "0", "0.05", "", "0915-1530",
     "2026-08-28", "", "NSE:NIFTY50-INDEX", "10", "10", "26000", "NIFTY", "26000"],
    ["101000000026009", "NIFTY BANK", "10", "0", "0.05", "", "0915-1530",
     "2026-08-28", "", "NSE:NIFTYBANK-INDEX", "10", "10", "26009", "BANKNIFTY", "26009"],
    ["101000000026017", "INDIA VIX", "10", "0", "0.01", "", "0915-1530",
     "2026-08-28", "", "NSE:INDIAVIX-INDEX", "10", "10", "26017", "INDIAVIX", "26017"],
    ["12100000001", "SENSEX", "10", "0", "0.01", "", "0915-1530",
     "2026-08-28", "", "BSE:SENSEX-INDEX", "12", "10", "1", "SENSEX", "1"],
    ["121000000039", "BSE ENERGY INDEX", "10", "0", "0.05", "", "0915-1530",
     "2026-08-28", "", "BSE:ENERGY-INDEX", "12", "10", "39", "ENERGY", "39"],
    ["10100000002885", "RELIANCE INDUSTRIES", "0", "1", "0.05", "INE002A01018",
     "0915-1530", "2026-08-28", "", "NSE:RELIANCE-EQ", "10", "10", "2885",
     "RELIANCE", "2885"],
]

#: The identifier each adapter must produce for each canonical index — taken
#: from the live masters on 2026-08-31, not from the implementation.
EXPECTED = {
    "zerodha": (
        (KITE_INDEX_ROWS,),
        {"NIFTY": 256265, "BANKNIFTY": 260105, "INDIAVIX": 264969, "SENSEX": 265},
    ),
    "angelone": (
        (ANGEL_INDEX_ROWS,),
        {"NIFTY": "1|99926000", "BANKNIFTY": "1|99926009",
         "INDIAVIX": "1|99926017", "SENSEX": "3|99919000"},
    ),
    "dhan": (
        (DHAN_INDEX_ROWS,),
        {"NIFTY": "IDX_I|13", "BANKNIFTY": "IDX_I|25",
         "INDIAVIX": "IDX_I|21", "SENSEX": "IDX_I|51"},
    ),
    "upstox": (
        (UPSTOX_INDEX_NSE_ROWS, UPSTOX_INDEX_BSE_ROWS),
        {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank",
         "INDIAVIX": "NSE_INDEX|India VIX", "SENSEX": "BSE_INDEX|SENSEX"},
    ),
    "fyers": (
        (FYERS_INDEX_ROWS,),
        {"NIFTY": "if|nse_cm|26000", "BANKNIFTY": "if|nse_cm|26009",
         "INDIAVIX": "if|nse_cm|26017", "SENSEX": "if|bse_cm|1"},
    ),
}


def _adapter(broker):
    from services.brokers.registry import broker_registry
    return type(broker_registry.get(broker))


@pytest.mark.parametrize("broker", sorted(EXPECTED))
def test_every_broker_resolves_every_index_the_dashboard_shows(broker):
    """The headline claim of D5.17, per broker, against real master shapes."""
    row_groups, expected = EXPECTED[broker]
    index = _adapter(broker).build_catalogue_index(*row_groups)
    for symbol, identifier in expected.items():
        key = (INDEX_SEGMENT, INDEX_EXCHANGES[symbol], symbol)
        assert index.get(key) == identifier, f"{broker} did not resolve {symbol}"


@pytest.mark.parametrize("broker", sorted(EXPECTED))
def test_an_index_the_platform_does_not_name_stays_out(broker):
    """Every fixture carries a real neighbouring index — `NIFTY 500`,
    `NIFTY MIDCAP 150`, `BSE ENERGY INDEX`. A parser that admitted its whole
    index segment would subscribe hundreds of instruments per account whose
    ticks nothing can name."""
    row_groups, expected = EXPECTED[broker]
    index = _adapter(broker).build_catalogue_index(*row_groups)
    indices = {key[2] for key in index if key[0] == INDEX_SEGMENT}
    assert indices == set(expected), f"{broker} admitted an unnamed index"


@pytest.mark.parametrize("broker", sorted(EXPECTED))
def test_the_equity_half_is_unchanged_by_the_index_half(broker):
    """Every fixture also carries `RELIANCE`. The index branch is additive: it
    must not consume, reorder or shadow an equity row."""
    row_groups, _ = EXPECTED[broker]
    index = _adapter(broker).build_catalogue_index(*row_groups)
    equities = {key[2] for key in index if key[0] == EQUITY_SEGMENT}
    assert "RELIANCE" in equities


@pytest.mark.parametrize("broker", sorted(EXPECTED))
def test_an_index_never_enters_the_equity_segment(broker):
    """The collision D5.16's equity-only key could not have expressed, and the
    one whose symptom is a plausible number under the wrong name."""
    row_groups, expected = EXPECTED[broker]
    index = _adapter(broker).build_catalogue_index(*row_groups)
    for symbol in expected:
        for exchange in ("NSE", "BSE"):
            assert (EQUITY_SEGMENT, exchange, symbol) not in index


@pytest.mark.parametrize("broker", sorted(EXPECTED))
def test_the_universe_resolves_end_to_end_at_every_broker(broker):
    """`index_instruments()` → the adapter's own index → `{symbol: identifier}`,
    through the real `resolve_from_index`. This is the join D5.17 adds, and the
    per-broker assertions above cannot show that its two halves agree: the
    universe states `("SENSEX", "BSE", INDEX)` and only this asserts that the
    key the parser filed is the key the universe asks for."""
    row_groups, expected = EXPECTED[broker]
    index = _adapter(broker).build_catalogue_index(*row_groups)
    assert resolve_from_index(index_instruments(), index) == expected


def test_a_broker_whose_master_omits_an_index_loses_only_that_index():
    """Degradation is per instrument, as everywhere else in this boundary: the
    missing one is absent from the subscription and falls back to the baseline,
    and the other three are unaffected."""
    from services.brokers.registry import broker_registry

    adapter = type(broker_registry.get("zerodha"))
    rows = [r for r in KITE_INDEX_ROWS if r["tradingsymbol"] != "SENSEX"]
    resolved = resolve_from_index(index_instruments(), adapter.build_catalogue_index(rows))
    assert set(resolved) == {"NIFTY", "BANKNIFTY", "INDIAVIX"}


# ==================================================================
# E. Fyers' index topic prefix
# ==================================================================


def test_fyers_index_topics_use_the_index_prefix_not_the_scrip_one():
    """The one place a wrong answer would be invisible.

    A Fyers tick is identified by the topic string the *server* returns on the
    snapshot record, not by what was subscribed. Subscribe an index as
    `sf|nse_cm|26000` and — if HSM answers `if|nse_cm|26000`, which is what its
    index feed publishes — the instrument map has no entry for the topic that
    arrives, `canonical_ticks` drops every packet, and the symptom is an index
    that never ticks while the socket is healthy and the log is quiet.
    """
    from services.brokers.fyers import INDEX_TOPIC, SCRIP_TOPIC, instrument_id

    assert instrument_id("101000000026000", INDEX_TOPIC) == "if|nse_cm|26000"
    assert instrument_id("10100000002885") == "sf|nse_cm|2885", (
        "the default prefix changed — every pre-D5.17 caller subscribes scrips"
    )
    assert instrument_id("101000000026000", "dp") is None, (
        "a depth topic's field zero is a bid, not a traded price"
    )
    assert SCRIP_TOPIC == "sf" and INDEX_TOPIC == "if"


# ==================================================================
# F. The engine actually asks for them
# ==================================================================
#
# WHY THIS SECTION EXISTS: mutation M22.
#
# Deleting `indices=index_instruments()` from `BrokerEngine`'s one call to
# `build_feed_universe` left every test above green. Every part of the boundary
# was proved — the aliases, the segmented key, the five parsers, the universe,
# the end-to-end join — and none of it would have reached a broker socket,
# because nothing asserted the single line that connects the universe to an
# account. That is the same shape of gap D5.16 wrote `test_empty_portfolio_feed`
# for: a catalogue that resolves perfectly is worthless if the planner never
# consults it.


@pytest.mark.parametrize("broker", sorted(EXPECTED))
def test_an_accounts_planned_subscription_covers_the_index_strip(broker, monkeypatch):
    """`_plan_tick_subscription`, per broker: the indices are on the wire AND
    the map can name them.

    Both halves, because a subscription the map cannot read back is the same
    defect as no subscription, reached one step later — `canonical_ticks` drops
    what it cannot name and the symptom is a silent socket.
    """
    from services import broker_engine as module
    from services.brokers.registry import broker_registry

    row_groups, expected = EXPECTED[broker]
    index = _adapter(broker).build_catalogue_index(*row_groups)

    async def _catalogue(self):
        return index

    monkeypatch.setattr(type(broker_registry.get(broker)), "_instrument_catalogue", _catalogue)

    engine = module.BrokerEngine()
    engine.db = None

    async def _no_watchlist(user_id):
        return []

    monkeypatch.setattr(engine, "_feed_watchlist_symbols", _no_watchlist)

    tokens, symbols = run(engine._plan_tick_subscription(
        "u1", broker, {}, holdings=[], positions=[]))

    for canonical, identifier in expected.items():
        assert identifier in tokens, (
            f"{broker}: {canonical} was never subscribed — the index strip "
            f"cannot move however well the catalogue resolves it"
        )
        assert canonical in symbols, (
            f"{broker}: {canonical} was subscribed but the account's instrument "
            f"map cannot name it, so every arriving tick is dropped"
        )


def test_a_fyers_index_tick_resolves_through_the_instrument_map():
    """End to end at the identity boundary: the topic the catalogue produced is
    the key an arriving tick is looked up under."""
    from services.brokers.instruments import InstrumentMap, canonical_ticks
    from services.brokers.registry import broker_registry

    adapter = type(broker_registry.get("fyers"))
    catalogue = resolve_from_index(
        index_instruments(), adapter.build_catalogue_index(FYERS_INDEX_ROWS))
    instrument_map = InstrumentMap.from_portfolio(catalogue=catalogue)

    ticks = canonical_ticks(
        [{"instrument_token": "if|nse_cm|26000", "last_price": 24815.25,
          "exchange": "NSE", "volume": 0}],
        instrument_map, broker="fyers")
    assert [(t["symbol"], t["price"]) for t in ticks] == [("NIFTY", 24815.25)]

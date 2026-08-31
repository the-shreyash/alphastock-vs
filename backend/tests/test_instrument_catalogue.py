"""Sprint D5.16 §3 — the broker-neutral, exchange-aware equity catalogue.

WHY THE CONTRACT HAD TO CHANGE AND NOT JUST GAIN IMPLEMENTATIONS
-----------------------------------------------------------------
D5.15 introduced `resolve_instruments(symbols: Sequence[str])` and one
implementation. A bare symbol cannot say which listing it means, and that is not
a theoretical gap — it is the most-traded instrument in the country. Verified
against every one of the five brokers' own published masters on 2026-08-31:

    RELIANCE   Kite  NSE 738561          BSE 128083204
               Angel NSE 2885            BSE 500325
               Fyers NSE sf|nse_cm|2885  BSE sf|bse_cm|500325
               Dhan  NSE 2885            BSE 500325
               Upstox NSE_EQ|INE002A01018  BSE_EQ|INE002A01018

Two listings, two identifiers, one symbol. A catalogue keyed on the symbol alone
answers with whichever row it happened to index first — and the D5.15
implementation, being an NSE-only master, would have answered a BSE request with
the NSE key and said nothing about it. The account would then have subscribed to
the wrong listing and been marked at the wrong price, with nothing raising.

So the canonical unit of a feed universe is `(symbol, exchange, segment)` —
:class:`FeedInstrument` — and the adapter half is keyed by `(exchange, symbol)`.

WHAT IS TESTED WITH FIXTURES AND WHY THAT IS THE RIGHT LINE
------------------------------------------------------------
No test here reaches the network. What each adapter is actually responsible for
is **turning its broker's master rows into an exchange-keyed index**, and that is
pure. The fixture rows below are verbatim-shaped extracts of the real published
masters (field names, value spellings and series codes as they actually appear),
so a format change at the broker breaks the fetch, not the meaning of these
assertions — and the fetch is the part no hermetic test can honestly cover.
"""

import pytest

from services.brokers.catalogue import (
    BSE_CASH_SERIES,
    DEFAULT_EQUITY_EXCHANGE,
    EQUITY_SEGMENT,
    INDEX_SEGMENT,
    NSE_CASH_SERIES,
    InstrumentCatalogue,
    series_rank,
)
from services.brokers.feed_universe import FeedInstrument, build_feed_universe
from tests.test_broker_streaming import run


# ==================================================================
# A. The canonical unit
# ==================================================================


def test_a_feed_instrument_is_exchange_qualified_and_segment_scoped():
    instrument = FeedInstrument.of("reliance")
    assert instrument.symbol == "RELIANCE"
    assert instrument.exchange == DEFAULT_EQUITY_EXCHANGE == "NSE"
    assert instrument.segment == EQUITY_SEGMENT == "EQUITY"


def test_the_default_exchange_is_a_stated_default_not_a_first_match():
    """An unqualified symbol resolves to one named exchange, deterministically.

    The alternative — letting the catalogue pick whichever listing it indexed
    first — is the behaviour D5.16 forbids, and it is untestable by nature
    because it depends on file order at the broker.
    """
    assert FeedInstrument.of("RELIANCE").exchange == "NSE"
    assert FeedInstrument.of("RELIANCE", "bse").exchange == "BSE"
    assert FeedInstrument.of("RELIANCE", "  nse  ").exchange == "NSE"


def test_an_unsupported_exchange_is_refused_rather_than_defaulted():
    """Silently rewriting MCX to NSE would resolve a commodity to an equity.
    Phase 8 defers commodities; it does not mis-resolve them."""
    assert FeedInstrument.of("GOLD", "MCX") is None
    assert FeedInstrument.of("", "NSE") is None
    assert FeedInstrument.of(None) is None


def test_two_listings_of_one_symbol_are_two_instruments():
    assert FeedInstrument.of("RELIANCE", "NSE") != FeedInstrument.of("RELIANCE", "BSE")
    assert FeedInstrument.of("RELIANCE", "NSE") == FeedInstrument.of("reliance", "nse")
    # Hashable, because the universe de-duplicates on it.
    assert len({FeedInstrument.of("RELIANCE"), FeedInstrument.of("reliance")}) == 1


# ==================================================================
# B. The universe carries exchanges (D5.16 §4)
# ==================================================================


def test_a_held_instrument_keeps_the_exchange_the_account_holds_it_on():
    universe = build_feed_universe(
        holdings=[{"symbol": "RELIANCE", "exchange": "BSE"}])
    assert universe == (FeedInstrument.of("RELIANCE", "BSE"),)


def test_a_watchlist_symbol_takes_the_platform_default_exchange():
    """A watchlist row has no exchange column — the schema has never had one —
    so the default is what it gets, and the default is stated, not guessed."""
    universe = build_feed_universe(watchlist=["TCS"])
    assert universe == (FeedInstrument.of("TCS", "NSE"),)


def test_a_symbol_held_on_bse_and_watched_resolves_once_at_its_holding():
    """De-duplication is on the instrument, and the portfolio comes first, so
    the account's own record decides the exchange."""
    universe = build_feed_universe(
        holdings=[{"symbol": "RELIANCE", "exchange": "BSE"}],
        watchlist=["RELIANCE"],
    )
    assert universe == (FeedInstrument.of("RELIANCE", "BSE"),)


def test_the_same_symbol_on_two_exchanges_is_not_collapsed():
    """Two genuine listings both survive — the de-duplication key is the
    instrument, not the symbol."""
    universe = build_feed_universe(
        holdings=[{"symbol": "RELIANCE", "exchange": "NSE"},
                  {"symbol": "RELIANCE", "exchange": "BSE"}])
    assert universe == (FeedInstrument.of("RELIANCE", "NSE"),
                        FeedInstrument.of("RELIANCE", "BSE"))


def test_a_row_on_an_unsupported_exchange_costs_only_itself():
    universe = build_feed_universe(
        holdings=[{"symbol": "GOLD", "exchange": "MCX"},
                  {"symbol": "RELIANCE", "exchange": "NSE"}])
    assert universe == (FeedInstrument.of("RELIANCE", "NSE"),)


def test_holdings_and_positions_alone_still_reproduce_the_prior_universe():
    universe = build_feed_universe(
        holdings=[{"symbol": "RELIANCE"}], positions=[{"symbol": "TCS"}])
    assert [i.symbol for i in universe] == ["RELIANCE", "TCS"]


def test_the_universe_is_still_bounded():
    universe = build_feed_universe(
        watchlist=[f"SYM{i}" for i in range(50)], limit=10)
    assert len(universe) == 10


# ==================================================================
# C. The shared cash-equity policy
# ==================================================================


def test_a_primary_listing_outranks_a_secondary_series():
    """Two series that are BOTH cash equity — `EQ` (rolling settlement) and `BE`
    (trade-for-trade) — for one symbol. The ordinary share must win.

    Both offered series are deliberately ones the policy *accepts*. The first
    version of this test paired `EQ` against `D1`, which never reached the
    ranking at all: `D1` is not an equity series, so `offer` refuses it and the
    key had exactly one candidate. It asserted the right answer for the wrong
    reason and stayed green with the ordering removed entirely — found by
    mutation M06.
    """
    catalogue = InstrumentCatalogue()
    catalogue.offer("NSE", "CHOLAFIN", "BE-id", series="BE")
    catalogue.offer("NSE", "CHOLAFIN", "EQ-id", series="EQ")
    assert catalogue.build() == {(EQUITY_SEGMENT, "NSE", "CHOLAFIN"): "EQ-id"}


def test_the_winner_does_not_depend_on_the_order_rows_arrive_in():
    """The masters do not agree on ordering — one file lists the secondary
    series first and another lists it last — so a resolution that depended on
    arrival order would be correct at one broker by luck."""
    for first, second in (("EQ", "BE"), ("BE", "EQ")):
        catalogue = InstrumentCatalogue()
        catalogue.offer("NSE", "CHOLAFIN", f"{first}-id", series=first)
        catalogue.offer("NSE", "CHOLAFIN", f"{second}-id", series=second)
        assert catalogue.build() == {(EQUITY_SEGMENT, "NSE", "CHOLAFIN"): "EQ-id"}


def test_two_candidates_the_master_cannot_tell_apart_are_dropped():
    """Equal rank, which is what a master with no series column produces: Kite's
    dump carries only an `EQ` flag, and Angel One's BSE rows carry no series at
    all, so every accepted row there ranks the same.

    Neither candidate can be shown to be the ordinary share, so the key is
    omitted and the instrument falls back to the baseline — the same outcome as
    a symbol the broker never heard of, reached by the same code.

    The first version offered `N1`/`N2`/`N3`, which `offer` refuses outright as
    non-equity series: the ambiguity branch was never reached and the test
    stayed green with the branch deleted. Found by mutation M07.
    """
    catalogue = InstrumentCatalogue()
    catalogue.offer("NSE", "IMC1", "first-id", rank=0)
    catalogue.offer("NSE", "IMC1", "second-id", rank=0)
    assert catalogue.build() == {}


def test_an_unequal_pair_at_the_same_key_still_resolves():
    """The drop is for a genuine tie, not for any duplicate — otherwise every
    dual-series symbol would vanish instead of resolving to its ordinary
    share."""
    catalogue = InstrumentCatalogue()
    catalogue.offer("NSE", "IMC1", "worse-id", rank=1)
    catalogue.offer("NSE", "IMC1", "better-id", rank=0)
    assert catalogue.build() == {(EQUITY_SEGMENT, "NSE", "IMC1"): "better-id"}


def test_a_non_equity_series_never_enters_the_catalogue():
    catalogue = InstrumentCatalogue()
    catalogue.offer("NSE", "SGBAUG28", "sgb-id", series="SG")
    catalogue.offer("BSE", "ENERGY", "index-id", series="INDEX")
    assert catalogue.build() == {}


def test_the_two_exchanges_do_not_share_a_key():
    catalogue = InstrumentCatalogue()
    catalogue.offer("NSE", "RELIANCE", "nse-id", series="EQ")
    catalogue.offer("BSE", "RELIANCE", "bse-id", series="A")
    assert catalogue.build() == {(EQUITY_SEGMENT, "NSE", "RELIANCE"): "nse-id",
                                 (EQUITY_SEGMENT, "BSE", "RELIANCE"): "bse-id"}


def test_series_rank_is_exchange_specific():
    assert series_rank("NSE", "EQ") == 0
    assert series_rank("BSE", "A") == 0
    assert series_rank("NSE", "A") is None, "a BSE group is not an NSE series"
    assert series_rank("BSE", "EQ") is None
    assert NSE_CASH_SERIES[0] == "EQ" and BSE_CASH_SERIES[0] == "A"


def test_an_identifier_an_adapter_could_not_build_is_not_indexed():
    catalogue = InstrumentCatalogue()
    catalogue.offer("NSE", "RELIANCE", None, series="EQ")
    assert catalogue.build() == {}


# ==================================================================
# D. Per-adapter index building, from real master shapes
# ==================================================================

KITE_ROWS = [
    {"instrument_token": "738561", "tradingsymbol": "RELIANCE", "name": "RELIANCE",
     "instrument_type": "EQ", "segment": "NSE", "exchange": "NSE"},
    {"instrument_token": "128083204", "tradingsymbol": "RELIANCE", "name": "RELIANCE",
     "instrument_type": "EQ", "segment": "BSE", "exchange": "BSE"},
    {"instrument_token": "2953217", "tradingsymbol": "TCS", "name": "TCS",
     "instrument_type": "EQ", "segment": "NSE", "exchange": "NSE"},
    {"instrument_token": "216455429", "tradingsymbol": "BANKEX26SEPFUT", "name": "BANKEX",
     "instrument_type": "FUT", "segment": "BFO-FUT", "exchange": "BFO"},
    {"instrument_token": "256265", "tradingsymbol": "NIFTY 50", "name": "NIFTY 50",
     "instrument_type": "EQ", "segment": "INDICES", "exchange": "NSE"},
]

ANGEL_ROWS = [
    {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE",
     "instrumenttype": "", "exch_seg": "NSE"},
    {"token": "500325", "symbol": "RELIANCE", "name": "RELIANCE",
     "instrumenttype": "", "exch_seg": "BSE"},
    {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY",
     "instrumenttype": "AMXIDX", "exch_seg": "NSE"},
    {"token": "1234", "symbol": "SGBAUG28-SG", "name": "SGBAUG28",
     "instrumenttype": "", "exch_seg": "NSE"},
]

# Headerless. Column 0 is the fyToken, 9 the exchange-qualified ticker, 13 the
# underlying scrip name — as published.
FYERS_ROWS = [
    ["10100000002885", "RELIANCE INDUSTRIES", "0", "1", "0.05", "INE002A01018",
     "0915-1530", "2026-08-28", "", "NSE:RELIANCE-EQ", "10", "10", "2885",
     "RELIANCE", "2885"],
    ["1210000000500325", "RELIANCE INDUSTRIES", "0", "1", "0.05", "INE002A01018",
     "0915-1530", "2026-08-28", "", "BSE:RELIANCE-A", "12", "10", "500325",
     "RELIANCE", "500325"],
    ["1010000000685", "CHOLAMANDALAM", "0", "1", "0.05", "INE121A01024",
     "0915-1530", "2026-08-28", "", "NSE:CHOLAFIN-EQ", "10", "10", "685",
     "CHOLAFIN", "685"],
    ["101000000019257", "CHOLAMANDALAM DVR", "0", "1", "0.05", "INE121A01024",
     "0915-1530", "2026-08-28", "", "NSE:CHOLAFIN-D1", "10", "10", "19257",
     "CHOLAFIN", "19257"],
    ["121000000039", "BSE ENERGY INDEX", "0", "1", "0.05", "",
     "0915-1530", "2026-08-28", "", "BSE:ENERGY-INDEX", "12", "10", "39",
     "ENERGY", "39"],
]

DHAN_ROWS = [
    {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "E", "SEM_SMST_SECURITY_ID": "2885",
     "SEM_INSTRUMENT_NAME": "EQUITY", "SEM_TRADING_SYMBOL": "RELIANCE", "SEM_SERIES": "EQ"},
    {"SEM_EXM_EXCH_ID": "BSE", "SEM_SEGMENT": "E", "SEM_SMST_SECURITY_ID": "500325",
     "SEM_INSTRUMENT_NAME": "EQUITY", "SEM_TRADING_SYMBOL": "RELIANCE", "SEM_SERIES": "A"},
    {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "E", "SEM_SMST_SECURITY_ID": "685",
     "SEM_INSTRUMENT_NAME": "EQUITY", "SEM_TRADING_SYMBOL": "CHOLAFIN", "SEM_SERIES": "EQ"},
    {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "E", "SEM_SMST_SECURITY_ID": "19257",
     "SEM_INSTRUMENT_NAME": "EQUITY", "SEM_TRADING_SYMBOL": "CHOLAFIN", "SEM_SERIES": "D1"},
    {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "D", "SEM_SMST_SECURITY_ID": "44444",
     "SEM_INSTRUMENT_NAME": "OPTSTK", "SEM_TRADING_SYMBOL": "RELIANCE-Sep2026-CE",
     "SEM_SERIES": ""},
    # Cash SEGMENT, non-equity INSTRUMENT. Both filters exist and this row is
    # what separates them: the option above is refused by the segment check
    # alone, so without this row the instrument-name check could be deleted
    # with every assertion still passing. Found by mutation M18.
    {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "E", "SEM_SMST_SECURITY_ID": "55555",
     "SEM_INSTRUMENT_NAME": "ETF", "SEM_TRADING_SYMBOL": "NIFTYBEES",
     "SEM_SERIES": "EQ"},
]

UPSTOX_NSE_ROWS = [
    {"segment": "NSE_EQ", "exchange": "NSE", "instrument_key": "NSE_EQ|INE002A01018",
     "trading_symbol": "RELIANCE", "instrument_type": "EQ"},
    {"segment": "NSE_EQ", "exchange": "NSE", "instrument_key": "NSE_EQ|INE467B01029",
     "trading_symbol": "TCS", "instrument_type": "EQ"},
    {"segment": "NSE_INDEX", "exchange": "NSE", "instrument_key": "NSE_INDEX|Nifty 50",
     "trading_symbol": "Nifty 50", "instrument_type": "INDEX"},
]

UPSTOX_BSE_ROWS = [
    {"segment": "BSE_EQ", "exchange": "BSE", "instrument_key": "BSE_EQ|INE002A01018",
     "trading_symbol": "RELIANCE", "instrument_type": "A"},
]


def _index(adapter_cls, *row_groups):
    return adapter_cls.build_catalogue_index(*row_groups)


def test_zerodha_indexes_both_listings_of_one_symbol():
    from services.brokers.zerodha import ZerodhaAdapter

    index = _index(ZerodhaAdapter, KITE_ROWS)
    assert index[(EQUITY_SEGMENT, "NSE", "RELIANCE")] == 738561
    assert index[(EQUITY_SEGMENT, "BSE", "RELIANCE")] == 128083204
    assert index[(EQUITY_SEGMENT, "NSE", "TCS")] == 2953217
    assert (EQUITY_SEGMENT, "NSE", "BANKEX26SEPFUT") not in index, "a future entered an equity catalogue"
    assert (EQUITY_SEGMENT, "NSE", "NIFTY 50") not in index, "an index entered an equity catalogue"


def test_zerodha_identifiers_are_the_integers_its_ticker_subscribes_by():
    from services.brokers.zerodha import ZerodhaAdapter

    for value in _index(ZerodhaAdapter, KITE_ROWS).values():
        assert isinstance(value, int), (
            "a string token serializes into the subscribe frame as a string and "
            "Kite rejects the whole subscription"
        )


def test_angelone_indexes_both_listings_and_strips_only_nse_series():
    from services.brokers.angelone import AngelOneAdapter

    index = _index(AngelOneAdapter, ANGEL_ROWS)
    assert index[(EQUITY_SEGMENT, "NSE", "RELIANCE")] == "1|2885"
    assert index[(EQUITY_SEGMENT, "BSE", "RELIANCE")] == "3|500325"
    assert (EQUITY_SEGMENT, "NSE", "NIFTY") not in index and (EQUITY_SEGMENT, "NSE", "Nifty 50") not in index
    # Asserted on the total key set rather than on one guessed key. The first
    # version checked `("NSE", "SGBAUG28")`, which this adapter never builds —
    # `trading_symbol` does not strip `-SG`, so an admitted bond would have
    # entered as `("NSE", "SGBAUG28-SG")` and the probe could not have failed.
    # Found by mutation M14.
    #
    # D5.17 — the `AMXIDX` row in the fixture is now expected, under the INDEX
    # segment. That is the point of the segmented key: the assertion above still
    # falsifies an index reaching the EQUITY segment, which is the defect this
    # test names, while the set below states the whole truth about the fixture
    # rather than being weakened to accommodate the new entry.
    assert set(index) == {
        (EQUITY_SEGMENT, "NSE", "RELIANCE"),
        (EQUITY_SEGMENT, "BSE", "RELIANCE"),
        (INDEX_SEGMENT, "NSE", "NIFTY"),
    }, "a non-equity cash row entered the catalogue"


def test_fyers_indexes_both_listings_as_hsm_topics():
    from services.brokers.fyers import FyersAdapter

    index = _index(FyersAdapter, FYERS_ROWS)
    assert index[(EQUITY_SEGMENT, "NSE", "RELIANCE")] == "sf|nse_cm|2885"
    assert index[(EQUITY_SEGMENT, "BSE", "RELIANCE")] == "sf|bse_cm|500325"
    assert index[(EQUITY_SEGMENT, "NSE", "CHOLAFIN")] == "sf|nse_cm|685", "the DVR line won a symbol lookup"
    assert (EQUITY_SEGMENT, "BSE", "ENERGY") not in index, "a BSE index entered an equity catalogue"


def test_dhan_indexes_both_listings_as_segment_qualified_ids():
    from services.brokers.dhan import DhanAdapter

    index = _index(DhanAdapter, DHAN_ROWS)
    assert index[(EQUITY_SEGMENT, "NSE", "RELIANCE")] == "NSE_EQ|2885"
    assert index[(EQUITY_SEGMENT, "BSE", "RELIANCE")] == "BSE_EQ|500325"
    assert index[(EQUITY_SEGMENT, "NSE", "CHOLAFIN")] == "NSE_EQ|685"
    assert set(index) == {(EQUITY_SEGMENT, "NSE", "RELIANCE"), (EQUITY_SEGMENT, "BSE", "RELIANCE"), (EQUITY_SEGMENT, "NSE", "CHOLAFIN")}, (
        "a derivative or a non-equity cash instrument entered the catalogue"
    )


def test_upstox_indexes_both_listings_and_no_longer_answers_nse_for_bse():
    from services.brokers.upstox import UpstoxAdapter

    index = _index(UpstoxAdapter, UPSTOX_NSE_ROWS, UPSTOX_BSE_ROWS)
    assert index[(EQUITY_SEGMENT, "NSE", "RELIANCE")] == "NSE_EQ|INE002A01018"
    assert index[(EQUITY_SEGMENT, "BSE", "RELIANCE")] == "BSE_EQ|INE002A01018"
    assert (EQUITY_SEGMENT, "NSE", "NIFTY 50") not in index


@pytest.mark.parametrize("broker,rows,expected", [
    ("zerodha", (KITE_ROWS,), {"NSE": 738561, "BSE": 128083204}),
    ("angelone", (ANGEL_ROWS,), {"NSE": "1|2885", "BSE": "3|500325"}),
    ("fyers", (FYERS_ROWS,), {"NSE": "sf|nse_cm|2885", "BSE": "sf|bse_cm|500325"}),
    ("dhan", (DHAN_ROWS,), {"NSE": "NSE_EQ|2885", "BSE": "BSE_EQ|500325"}),
    ("upstox", (UPSTOX_NSE_ROWS, UPSTOX_BSE_ROWS),
     {"NSE": "NSE_EQ|INE002A01018", "BSE": "BSE_EQ|INE002A01018"}),
])
def test_every_broker_disambiguates_reliance_by_exchange(broker, rows, expected):
    """The acceptance criterion of Phase 3, once per broker: five adapters, five
    identifier formats, one canonical question, and never the wrong listing."""
    from services.brokers.registry import broker_registry

    adapter = broker_registry.get(broker)
    index = type(adapter).build_catalogue_index(*rows)
    assert index[(EQUITY_SEGMENT, "NSE", "RELIANCE")] == expected["NSE"]
    assert index[(EQUITY_SEGMENT, "BSE", "RELIANCE")] == expected["BSE"]
    assert index[(EQUITY_SEGMENT, "NSE", "RELIANCE")] != index[(EQUITY_SEGMENT, "BSE", "RELIANCE")]


# ==================================================================
# E. Resolution, end to end through the adapter
# ==================================================================

_ADAPTER_FIXTURES = {
    "zerodha": (KITE_ROWS,),
    "angelone": (ANGEL_ROWS,),
    "fyers": (FYERS_ROWS,),
    "dhan": (DHAN_ROWS,),
    "upstox": (UPSTOX_NSE_ROWS, UPSTOX_BSE_ROWS),
}


def _adapter_with_catalogue(broker, monkeypatch):
    """A registered adapter whose master is the fixture, not the network."""
    from services.brokers.registry import broker_registry

    adapter = broker_registry.get(broker)
    index = type(adapter).build_catalogue_index(*_ADAPTER_FIXTURES[broker])

    async def _fixture_catalogue(self):
        return index

    monkeypatch.setattr(type(adapter), "_instrument_catalogue", _fixture_catalogue)
    return adapter


@pytest.mark.parametrize("broker", sorted(_ADAPTER_FIXTURES))
def test_every_broker_declares_the_catalogue_capability(broker):
    from services.brokers.capabilities import BrokerCapability
    from services.brokers.gateway import broker_gateway

    assert broker_gateway.supports(broker, BrokerCapability.INSTRUMENT_CATALOGUE), (
        f"{broker} does not declare INSTRUMENT_CATALOGUE, so an account with no "
        f"holdings subscribes to nothing and can never receive a tick"
    )


@pytest.mark.parametrize("broker", sorted(_ADAPTER_FIXTURES))
def test_a_bse_request_never_receives_the_nse_identifier(broker, monkeypatch):
    """The defect the contract change exists to make unrepresentable."""
    adapter = _adapter_with_catalogue(broker, monkeypatch)

    nse = run(adapter.resolve_instruments([FeedInstrument.of("RELIANCE", "NSE")]))
    bse = run(adapter.resolve_instruments([FeedInstrument.of("RELIANCE", "BSE")]))

    assert nse and bse
    assert nse["RELIANCE"] != bse["RELIANCE"], (
        "the BSE request was answered with the NSE listing's identifier"
    )


@pytest.mark.parametrize("broker", sorted(_ADAPTER_FIXTURES))
def test_an_unresolvable_instrument_is_omitted_not_sentinelled(broker, monkeypatch):
    adapter = _adapter_with_catalogue(broker, monkeypatch)
    resolved = run(adapter.resolve_instruments([
        FeedInstrument.of("RELIANCE", "NSE"),
        FeedInstrument.of("NOSUCHSTOCK", "NSE"),
    ]))
    assert set(resolved) == {"RELIANCE"}


@pytest.mark.parametrize("broker", sorted(_ADAPTER_FIXTURES))
def test_resolution_of_nothing_is_nothing_and_costs_no_download(broker, monkeypatch):
    """An empty universe must not fetch a master. A restart with N idle accounts
    would otherwise perform N downloads to answer N empty questions."""
    from services.brokers.registry import broker_registry

    adapter = broker_registry.get(broker)
    fetched = []

    async def _explode(self):
        fetched.append(broker)
        return {}

    monkeypatch.setattr(type(adapter), "_instrument_catalogue", _explode)
    assert run(adapter.resolve_instruments([])) == {}
    assert fetched == []


@pytest.mark.parametrize("broker", sorted(_ADAPTER_FIXTURES))
def test_a_catalogue_that_cannot_be_read_degrades_rather_than_failing(broker, monkeypatch):
    """The catalogue widens coverage; it is not load-bearing. An unreachable
    master must leave the account with the portfolio-derived subscription it had
    before, not with no stream."""
    from services.brokers.errors import BrokerError
    from services.brokers.gateway import broker_gateway
    from services.brokers.registry import broker_registry

    adapter = broker_registry.get(broker)

    async def _unreachable(self):
        raise BrokerError("instrument master unavailable")

    monkeypatch.setattr(type(adapter), "_instrument_catalogue", _unreachable)
    assert run(broker_gateway.resolve_instruments(
        broker, [FeedInstrument.of("RELIANCE")], {})) == {}

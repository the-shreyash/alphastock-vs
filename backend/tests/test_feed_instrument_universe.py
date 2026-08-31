"""Sprint D5.15 — the instrument universe and the end-to-end market-data path.

WHAT THIS FILE PINS, AND THE LIVE RUN THAT MOTIVATED IT
--------------------------------------------------------
D5.15 traced a real, authenticated Upstox account through the running platform
during NSE market hours. Authentication succeeded, the portfolio sync succeeded,
both broker WebSockets were open (confirmed at the TCP layer), the feed was
registered as a market-data provider — and in ten minutes the platform published
**zero** `market.tick` events.

The account held nothing. `stream_instruments` derives its subscription from
`holdings + positions`, both were empty, so the subscribe frame was empty and
the socket was structurally incapable of delivering a packet. Two further
consequences followed from the same rule and either would have been enough on
its own:

  * `InstrumentMap.from_portfolio([], [])` is empty, so a tick that *did* arrive
    could not have been named and would have been dropped at the identity
    boundary;
  * the feed nevertheless advertised the TICKS capability to its owner, because
    link-level eligibility asked only whether a socket existed.

None of that is a broker defect and none of it is visible to a test that gives
the account a portfolio. Every test below therefore starts from **zero holdings
and zero positions**, which is the state the live account was actually in.

THE CONTRACT
------------
  * **A feed's universe is not the portfolio.** It is the portfolio *plus* what
    the user watches *plus* what the dashboard shows — assembled in canonical
    symbols by a module that names no broker.
  * **Symbol → broker instrument identifier is the adapter's, always.** The
    Market Engine and the broker engine name no instrument format. A broker
    without a catalogue keeps exactly its pre-D5.15 behaviour.
  * **A subscription the instrument map cannot read back is not a subscription.**
    Whatever the feed is aimed at must be nameable when it arrives.
  * **Zero subscriptions is not an active market-data feed** — the D5.15 half of
    the D4.5 readiness rule.
  * **The price a dashboard renders comes through the Market Gateway**, resolved
    for the user who is going to see it, and is delivered to that user alone.

Nothing here sleeps on a probation window, opens a socket or reaches a broker
API. The live validation is recorded in TASK.md; these tests are hermetic and
claim nothing about a real broker.
"""

from services.brokers.capabilities import (
    CAPABILITY_METHODS,
    IMPLEMENTABLE_CAPABILITIES,
    BrokerCapability,
)
from services.brokers.feed_universe import (
    MAX_FEED_UNIVERSE,
    FeedInstrument,
    build_feed_universe,
    dashboard_symbols,
)
from services.brokers.instruments import InstrumentMap, canonical_ticks
from services.market_engine.providers import Capability, ResolutionContext
from services.market_engine.providers.streaming import StreamingTickProvider
from tests.test_broker_streaming import (  # noqa: F401  (fixtures + helpers)
    NovaAdapter,
    nova_registered,
    run,
)

def _symbols(universe):
    """The canonical symbols of a universe, in order.

    D5.16 changed the element type from `str` to `FeedInstrument`, because a
    bare symbol cannot say which listing it means. Every property this section
    pins — order, de-duplication, canonicalisation, one bad row costing only
    itself — is about the *sequence*, not the element, so the assertions read
    the symbol out rather than being rewritten. The exchange half has its own
    coverage in `test_instrument_catalogue.py`.
    """
    return tuple(instrument.symbol for instrument in universe)


# ==================================================================
# A. The universe is broker-neutral, ordered, and additive
# ==================================================================


def test_an_account_with_no_holdings_and_no_positions_still_has_a_universe():
    """The live defect, stated as a contract.

    This is the exact input the real Upstox account presented: nothing owned,
    nothing open. Before D5.15 the answer was the empty tuple and the feed was
    silent forever.
    """
    universe = build_feed_universe(
        holdings=[], positions=[], watchlist=["SBIN"], dashboard=("RELIANCE",))

    assert universe, "an account with an empty portfolio got an empty feed universe"
    assert set(_symbols(universe)) == {"SBIN", "RELIANCE"}


def test_holdings_and_positions_alone_reproduce_the_pre_d5_15_universe():
    """Widening is opt-in per caller. A caller that passes only the portfolio
    gets exactly what it got before, which is what makes this safe beneath five
    adapters at once."""
    universe = build_feed_universe(
        holdings=[{"symbol": "TCS"}], positions=[{"symbol": "INFY"}])

    assert _symbols(universe) == ("TCS", "INFY")


def test_the_portfolio_comes_first_so_a_ceiling_never_costs_an_owned_instrument():
    """Order is contract, not incident: every downstream trim takes from the
    end, so the instruments dropped are the ones the account is least entitled
    to expect."""
    universe = build_feed_universe(
        holdings=[{"symbol": "TCS"}],
        positions=[{"symbol": "INFY"}],
        watchlist=["SBIN"],
        dashboard=["RELIANCE"],
    )

    assert _symbols(universe) == ("TCS", "INFY", "SBIN", "RELIANCE")


def test_a_symbol_held_and_watched_appears_once_at_its_portfolio_position():
    universe = build_feed_universe(
        holdings=[{"symbol": "TCS"}], watchlist=["TCS", "SBIN"])

    assert _symbols(universe) == ("TCS", "SBIN")


def test_symbols_are_canonicalised_the_way_the_platform_names_instruments():
    universe = build_feed_universe(watchlist=["  sbin ", "Tcs"])

    assert _symbols(universe) == ("SBIN", "TCS")


def test_an_unnameable_row_costs_only_itself():
    universe = build_feed_universe(
        holdings=[{"symbol": None}, "not-a-row", {"no_symbol": 1}, {"symbol": "TCS"}])

    assert _symbols(universe) == ("TCS",)


def test_the_universe_is_bounded():
    universe = build_feed_universe(
        watchlist=[f"SYM{i}" for i in range(MAX_FEED_UNIVERSE + 50)])

    assert len(universe) == MAX_FEED_UNIVERSE


def test_the_dashboard_set_is_the_universe_the_product_actually_renders():
    """Read from `market_data.STOCK_UNIVERSE` rather than a second constant. A
    dashboard list that could drift from the rendered one would reproduce the
    D5.15 symptom: a price on screen no feed was asked to cover."""
    from market_data import STOCK_UNIVERSE

    assert set(dashboard_symbols()) == {s["symbol"].upper() for s in STOCK_UNIVERSE}


def test_the_universe_module_names_no_broker():
    """Broker-neutrality of the layer that decides *what* to cover."""
    import services.brokers.feed_universe as module

    with open(module.__file__) as handle:
        source = handle.read().lower()
    for broker in ("zerodha", "kite", "upstox", "angel", "smartapi", "fyers", "dhan"):
        assert broker not in source, f"the feed universe names {broker}"


# ==================================================================
# B. The catalogue seam: declared, verified, and degradable
# ==================================================================


def test_the_catalogue_capability_is_bound_to_a_real_adapter_method():
    """A capability with no method behind it is a comment — the registry
    verifies every declaration against this map at registration time."""
    assert CAPABILITY_METHODS[BrokerCapability.INSTRUMENT_CATALOGUE] == "resolve_instruments"
    assert BrokerCapability.INSTRUMENT_CATALOGUE in IMPLEMENTABLE_CAPABILITIES


def test_a_broker_without_a_catalogue_resolves_nothing_and_is_not_an_error():
    """Nova declares TICK_STREAM and no catalogue — the pre-D5.15 broker. The
    gateway answers `{}` so no caller has to ask two questions to get one."""
    from services.brokers.gateway import broker_gateway

    with nova_registered():
        assert not broker_gateway.supports("nova", BrokerCapability.INSTRUMENT_CATALOGUE)
        assert run(broker_gateway.resolve_instruments(
            "nova", [FeedInstrument.of("RELIANCE")], {})) == {}


def test_a_catalogue_that_fails_degrades_to_the_portfolio_rather_than_failing_the_stream():
    """A catalogue widens coverage; it is not load-bearing. An adapter whose
    master file is unreachable must cost the account the *widening*, not the
    feed it already had."""
    from services.brokers.errors import BrokerError
    from services.brokers.gateway import broker_gateway

    class Broken(NovaAdapter):
        capabilities = NovaAdapter.capabilities | {BrokerCapability.INSTRUMENT_CATALOGUE}

        async def resolve_instruments(self, instruments, session=None):
            raise BrokerError("instrument master unreachable")

    with nova_registered(Broken()):
        assert run(broker_gateway.resolve_instruments(
            "nova", [FeedInstrument.of("RELIANCE")], {})) == {}


def test_an_unresolvable_symbol_is_omitted_rather_than_carried_as_a_sentinel():
    """A key the wire will reject can take a whole subscribe frame down with
    it, so an unknown symbol must disappear from the subscription."""
    from services.brokers.gateway import broker_gateway

    class Partial(NovaAdapter):
        capabilities = NovaAdapter.capabilities | {BrokerCapability.INSTRUMENT_CATALOGUE}

        async def resolve_instruments(self, instruments, session=None):
            return {
                i.symbol: (f"NOVA:{i.symbol}" if i.symbol == "RELIANCE" else None)
                for i in instruments
            }

    with nova_registered(Partial()):
        resolved = run(broker_gateway.resolve_instruments(
            "nova",
            [FeedInstrument.of("RELIANCE"), FeedInstrument.of("NOPE")],
            {},
        ))

    assert resolved == {"RELIANCE": "NOVA:RELIANCE"}


def test_the_engine_never_names_an_instrument_format():
    """Broker-neutrality of the layer that decides *how* to cover it. The
    identifier is opaque to everything above the adapter."""
    import services.broker_engine as module

    with open(module.__file__) as handle:
        source = handle.read()
    for token in ("instrument_key", "NSE_EQ", "fyToken", "instrument_token=", "securityId"):
        assert token not in source, f"the broker engine names the instrument format {token}"


# ==================================================================
# C. A subscription the map cannot read back is not a subscription
# ==================================================================


def test_a_catalogue_instrument_can_be_named_when_its_tick_arrives():
    """The other half of the live defect. Aiming the feed at an instrument the
    account does not own is pointless unless the arriving tick can be resolved
    — `canonical_ticks` drops what it cannot name, silently."""
    instrument_map = InstrumentMap.from_portfolio(
        [], [], {"RELIANCE": "NOVA:RELIANCE"})

    ticks = canonical_ticks(
        [{"instrument_token": "NOVA:RELIANCE", "last_price": 1290.4}],
        instrument_map, broker="nova")

    assert [t["symbol"] for t in ticks] == ["RELIANCE"]
    assert ticks[0]["price"] == 1290.4


def test_without_the_catalogue_that_same_tick_is_dropped():
    """The falsifying twin of the test above: this is what the platform did
    with a real broker packet before D5.15."""
    ticks = canonical_ticks(
        [{"instrument_token": "NOVA:RELIANCE", "last_price": 1290.4}],
        InstrumentMap.from_portfolio([], []), broker="nova")

    assert ticks == []


def test_a_portfolio_row_outranks_a_catalogue_entry_for_the_same_instrument():
    """A held instrument carries an exchange and a broker-confirmed identifier;
    a catalogue entry is a lookup. Where they disagree the account's own record
    is the better fact."""
    instrument_map = InstrumentMap.from_portfolio(
        [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": "NOVA:RELIANCE"}],
        [],
        {"RELIANCE": "NOVA:RELIANCE"},
    )

    resolved = instrument_map.resolve(instrument_token="NOVA:RELIANCE")
    assert resolved.exchange == "NSE", "the catalogue overwrote a held instrument's exchange"


def test_the_catalogue_does_not_invent_an_exchange():
    """`MarketInstrument` treats an exchange as a statement. A lookup that did
    not carry one must produce an unqualified symbol, not a guess."""
    instrument_map = InstrumentMap.from_portfolio([], [], {"SBIN": "NOVA:SBIN"})

    assert instrument_map.resolve(instrument_token="NOVA:SBIN").exchange is None


# ==================================================================
# D. Zero subscriptions is not an active market-data feed
# ==================================================================


def _feed(symbols=()):
    provider = StreamingTickProvider("brokerfeed:nova:u1", owner_user_id="u1")
    run(provider.connect())
    if symbols:
        run(provider.subscribe(symbols))
    return provider


def test_a_connected_feed_with_no_subscription_does_not_serve_ticks():
    """Observed live: the platform told the owner of an empty-portfolio account
    that the TICKS capability was available, for a socket that could never
    deliver a packet."""
    provider = _feed()

    assert provider.is_link_up, "the fixture did not reach a link-up state"
    assert not provider.is_eligible_for(
        ResolutionContext(user_id="u1", capability=Capability.TICKS))


def test_the_same_feed_serves_ticks_once_it_has_asked_for_something():
    provider = _feed(["RELIANCE"])

    assert provider.is_eligible_for(
        ResolutionContext(user_id="u1", capability=Capability.TICKS))


def test_an_unsubscribed_feed_is_still_this_users_feed_for_diagnostics():
    """The applicability question is not the delivery question. Hiding an
    attached-but-idle feed from the surfaces whose job is to explain why it is
    not serving would replace one wrong answer with another."""
    provider = _feed()

    assert provider.is_eligible_for(ResolutionContext(user_id="u1"))


def test_an_unsubscribed_feed_is_not_promoted_over_the_baseline_either():
    """The D4.5 readiness gate, restated: a feed that asked for nothing cannot
    reach READY, so it cannot displace the baseline for quotes."""
    provider = _feed()

    assert not provider.is_ready
    assert not provider.is_eligible_for(
        ResolutionContext(user_id="u1", symbol="RELIANCE", capability=Capability.QUOTES))


def test_a_feed_that_loses_its_last_instrument_stops_serving_ticks():
    provider = _feed(["RELIANCE"])
    run(provider.unsubscribe(["RELIANCE"]))

    assert not provider.is_eligible_for(
        ResolutionContext(user_id="u1", capability=Capability.TICKS))


# ==================================================================
# E. The consumer payload carries no broker and no credential
# ==================================================================


def test_a_canonical_tick_carries_no_broker_identity_and_no_instrument_key():
    """Developer Rule 4 across the widened path: the catalogue introduced a new
    identifier into the system and none of it may reach a consumer."""
    instrument_map = InstrumentMap.from_portfolio([], [], {"RELIANCE": "NSE_EQ|INE002A01018"})

    ticks = canonical_ticks(
        [{"instrument_token": "NSE_EQ|INE002A01018", "last_price": 1290.4}],
        instrument_map, broker="upstox")

    assert ticks, "the fixture produced no tick to inspect"
    for tick in ticks:
        rendered = repr(tick).lower()
        for forbidden in ("upstox", "nse_eq|", "ine002a01018", "token", "bearer"):
            assert forbidden not in rendered, f"{forbidden} reached the canonical tick"
        assert set(tick) == {"symbol", "price", "exchange", "volume", "ingested_at"}

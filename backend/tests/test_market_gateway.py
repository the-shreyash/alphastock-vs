"""Phase D1 — Market Gateway foundation tests (hermetic, no network).

WHAT THIS SUITE IS PROTECTING
-----------------------------
D1's whole claim is that StockAssist AI no longer depends on Yahoo Finance —
that a second provider can be added with one adapter and one registry entry, and
that nothing above the Source Manager can tell which provider answered. A claim
like that is worthless unless a test could fail if it were false, so every test
here is written so that reverting the D1 change turns it red:

  • the provider-swap tests register a *fake streaming provider* at broker
    priority and assert the gateway serves from it with no gateway change —
    the property "adding a provider requires no Market Engine edit" made
    executable rather than asserted in a docstring;
  • the leak tests assert on the absence of provider identity in normalized
    events, in feed status, and in the Market Engine's source text;
  • the failure tests drive real failures through the gateway boundary and
    assert the platform degrades to *nothing* rather than to a plausible
    fabricated number, which is the one failure mode CLAUDE.md treats as
    unacceptable.

Every provider here is a fake. No test touches Yahoo Finance.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.market_engine import gateway as gateway_module
from services.market_engine.event_bus import event_bus
from services.market_engine.gateway import MarketGateway, register_default_providers
from services.market_engine.providers import (
    Capability,
    CapabilityUnavailable,
    MarketDataProvider,
    ProviderKind,
    ProviderRegistry,
    ProviderState,
    SourceTier,
    YahooPollingAdapter,
    provider_registry,
)
from services.market_engine.providers.base import (
    DOWN_AFTER_FAILURES,
    DEGRADED_AFTER_FAILURES,
)
from services.market_engine.source_manager import (
    FEED_AVAILABLE,
    FEED_UNAVAILABLE,
    PROVIDER_STATUS_TOPIC,
    SourceManager,
)


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #

RAW_BROKER_QUOTE = {
    "tradingsymbol": "RELIANCE",
    "last_price": 2891.5,
    "ohlc": {"open": 2870.0, "high": 2900.0, "low": 2860.0, "close": 2875.0},
    "change_pct": 0.57,
    "volume": 4_200_000,
}

RAW_YAHOO_QUOTE = {
    "symbol": "RELIANCE",
    "price": 2891.5,
    "open": 2870.0,
    "high": 2900.0,
    "low": 2860.0,
    "prev_close": 2875.0,
    "change": 16.5,
    "change_pct": 0.57,
    "volume": 4_200_000,
    "source": "yahoo_finance",
}


class FakeStreamingProvider(MarketDataProvider):
    """A broker-priority streaming provider that speaks the broker payload shape.

    Stands in for the Zerodha adapter D3 will add. It exists to prove the D1
    claim structurally: registering it is the *only* change needed for the
    gateway to start serving streaming data through it.
    """

    name = "fake_broker"
    kind = ProviderKind.STREAMING
    tier = SourceTier.STREAMING
    normalizer_key = "broker"
    priority = 1
    capabilities = frozenset({Capability.QUOTES, Capability.TICKS})

    def __init__(self, quote=None, raises=None):
        super().__init__()
        self._quote = quote if quote is not None else dict(RAW_BROKER_QUOTE)
        self._raises = raises
        self.calls = 0

    async def fetch_quote(self, symbol):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._quote


class FakePollingProvider(MarketDataProvider):
    """A baseline-priority polled provider speaking the Yahoo payload shape."""

    name = "fake_baseline"
    kind = ProviderKind.POLLING
    tier = SourceTier.DELAYED
    normalizer_key = "yahoo"
    priority = 3
    capabilities = frozenset({Capability.QUOTES, Capability.SECTORS})

    def __init__(self, quote=None, raises=None):
        super().__init__()
        self._quote = quote if quote is not None else dict(RAW_YAHOO_QUOTE)
        self._raises = raises
        self.calls = 0

    async def fetch_quote(self, symbol):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._quote

    async def fetch_sectors(self):
        return [{"sector": "IT", "change_pct": 1.2}]


class _BusSpy:
    """Record events published on the singleton bus for the given topics."""

    def __init__(self, *topics):
        self.events = []
        self._topics = topics

    async def _handler(self, event):
        self.events.append(event)

    def __enter__(self):
        for topic in self._topics:
            event_bus.subscribe(topic, self._handler)
        return self

    def __exit__(self, *exc):
        for topic in self._topics:
            event_bus.unsubscribe(topic, self._handler)
        return False


@pytest.fixture
def isolated():
    """A gateway + source manager over an empty, private registry.

    The singletons are left untouched so this suite cannot leak provider state
    into the rest of the session — the Yahoo adapter registered at import is a
    process-wide object with mutable health counters.
    """
    registry = ProviderRegistry()
    manager = SourceManager(registry)
    gateway = MarketGateway()
    with patch.object(gateway_module, "source_manager", manager):
        yield gateway, manager, registry


# --------------------------------------------------------------------------- #
# The provider adapter contract                                                #
# --------------------------------------------------------------------------- #

class TestProviderContract:

    def test_undeclared_capability_raises_rather_than_returning_empty(self):
        """A provider asked for data it never claimed must fail loudly.

        Returning `[]` would be indistinguishable from "the market has no
        movers today", so a resolution bug would surface as a permanently empty
        section rather than as an error anyone investigates.
        """
        provider = FakePollingProvider()
        assert not provider.supports(Capability.DEPTH)
        with pytest.raises(CapabilityUnavailable):
            _run(provider.fetch_commodities())

    def test_every_declared_capability_is_actually_implemented(self):
        """Declaring a capability without implementing it is the one way an
        adapter can lie to the Source Manager. Yahoo is the provider every user
        falls back to, so it is the one that must not."""
        adapter = YahooPollingAdapter()
        method_for = {
            Capability.QUOTES: lambda: adapter.fetch_quote("RELIANCE"),
            Capability.UNIVERSE_QUOTES: adapter.fetch_universe_quotes,
            Capability.INDICES: adapter.fetch_indices,
            Capability.SECTORS: adapter.fetch_sectors,
            Capability.MOVERS: adapter.fetch_gainers,
            Capability.GLOBAL_MARKETS: adapter.fetch_global_markets,
            Capability.COMMODITIES: adapter.fetch_commodities,
            Capability.OHLC: lambda: adapter.fetch_chart("RELIANCE"),
            Capability.SEARCH: lambda: adapter.search("rel"),
        }
        for capability in adapter.capabilities:
            assert capability in method_for, (
                f"{adapter.name} declares {capability.value} but this test has no "
                "method mapped for it — extend the map or drop the capability"
            )
            # Calling the base-class stub is what raises; reaching the network
            # is prevented by the patch below, so a raise here means the adapter
            # never overrode the method.
            with patch.dict("sys.modules"), \
                 patch("services.real_market.fetch_real_stock_quote",
                       new_callable=AsyncMock, return_value=None):
                try:
                    _run(_maybe_await(method_for[capability]))
                except CapabilityUnavailable:  # pragma: no cover - the failure
                    pytest.fail(
                        f"{adapter.name} declares {capability.value} but does not "
                        "implement it"
                    )
                except Exception:
                    # Any other error is the (patched-out) provider client
                    # failing, which this test does not care about.
                    pass

    def test_yahoo_never_claims_tick_or_depth_capability(self):
        """Yahoo publishes neither. Claiming them would have the Source Manager
        resolve Yahoo for an order-book request it can only answer with nulls."""
        adapter = YahooPollingAdapter()
        assert Capability.TICKS not in adapter.capabilities
        assert Capability.DEPTH not in adapter.capabilities

    def test_yahoo_is_labelled_delayed_not_streaming(self):
        """`tier` is what the AI calibrates its language against. A polled feed
        labelled `streaming` makes the model call a minute-old number live."""
        adapter = YahooPollingAdapter()
        assert adapter.tier is SourceTier.DELAYED
        assert adapter.kind is ProviderKind.POLLING

    def test_connect_and_disconnect_are_idempotent(self):
        provider = FakePollingProvider()
        _run(provider.connect())
        _run(provider.connect())
        assert provider.is_connected
        _run(provider.disconnect())
        _run(provider.disconnect())
        assert not provider.is_connected


async def _maybe_await(fn):
    result = fn()
    if asyncio.iscoroutine(result):
        return await result
    return result


# --------------------------------------------------------------------------- #
# Provider health                                                              #
# --------------------------------------------------------------------------- #

class TestProviderHealth:

    def test_failures_escalate_up_then_down(self):
        provider = FakePollingProvider()
        for _ in range(DEGRADED_AFTER_FAILURES):
            provider.record_failure(RuntimeError("boom"))
        assert provider.health().state is ProviderState.DEGRADED

        for _ in range(DOWN_AFTER_FAILURES - DEGRADED_AFTER_FAILURES):
            provider.record_failure(RuntimeError("boom"))
        assert provider.health().state is ProviderState.DOWN

    def test_one_success_restores_a_failing_provider(self):
        """Recovery must be automatic. A provider that comes back and stays
        excluded is an outage the platform inflicted on itself."""
        provider = FakePollingProvider()
        for _ in range(DOWN_AFTER_FAILURES):
            provider.record_failure(RuntimeError("boom"))
        assert provider.health().state is ProviderState.DOWN

        provider.record_success()
        assert provider.health().state is ProviderState.UP
        assert provider.health().consecutive_failures == 0

    def test_an_empty_response_does_not_reset_the_failure_streak(self):
        """A provider answering 200-with-no-data for everything is not healthy.
        Counting that as a success is how a silently empty feed keeps its slot."""
        provider = FakePollingProvider()
        for _ in range(DEGRADED_AFTER_FAILURES):
            provider.record_failure(RuntimeError("boom"))
        provider.record_success(empty=True)

        assert provider.health().state is ProviderState.DEGRADED
        assert provider.health().consecutive_failures == DEGRADED_AFTER_FAILURES
        assert provider.health().total_empty == 1


# --------------------------------------------------------------------------- #
# Registry                                                                     #
# --------------------------------------------------------------------------- #

class TestProviderRegistry:

    def test_providers_are_ordered_by_priority_not_registration(self):
        registry = ProviderRegistry()
        baseline = FakePollingProvider()
        broker = FakeStreamingProvider()
        registry.register(baseline)   # registered first, lower priority
        registry.register(broker)

        assert [p.name for p in registry.all()] == ["fake_broker", "fake_baseline"]

    def test_duplicate_registration_is_ignored_not_fatal(self):
        """Startup paths can run twice. Two adapters under one name would mean
        two divergent sets of health counters for the same feed."""
        registry = ProviderRegistry()
        first = FakePollingProvider()
        registry.register(first)
        registry.register(FakePollingProvider())

        assert len(registry) == 1
        assert registry.get("fake_baseline") is first

    def test_replace_is_explicit(self):
        registry = ProviderRegistry()
        registry.register(FakePollingProvider())
        replacement = FakePollingProvider()
        registry.register(replacement, replace=True)

        assert registry.get("fake_baseline") is replacement

    def test_candidates_exclude_providers_lacking_the_capability(self):
        registry = ProviderRegistry()
        registry.register(FakeStreamingProvider())   # quotes + ticks
        registry.register(FakePollingProvider())     # quotes + sectors

        names = [p.name for p in registry.candidates_for(Capability.SECTORS)]
        assert names == ["fake_baseline"]

    def test_candidates_exclude_down_providers_but_keep_degraded_ones(self):
        """Degraded data beats no data — the tier below may be materially
        worse. Only a consistently failing provider is disqualifying."""
        registry = ProviderRegistry()
        broker = FakeStreamingProvider()
        baseline = FakePollingProvider()
        registry.register(broker)
        registry.register(baseline)

        for _ in range(DEGRADED_AFTER_FAILURES):
            broker.record_failure(RuntimeError("blip"))
        assert [p.name for p in registry.candidates_for(Capability.QUOTES)] == [
            "fake_broker", "fake_baseline",
        ]

        for _ in range(DOWN_AFTER_FAILURES):
            broker.record_failure(RuntimeError("outage"))
        assert [p.name for p in registry.candidates_for(Capability.QUOTES)] == [
            "fake_baseline",
        ]

    def test_unregistering_drops_the_provider(self):
        """The path a broker disconnect takes: the entitlement ends, the
        adapter leaves, and the feed falls to the tier below."""
        registry = ProviderRegistry()
        registry.register(FakeStreamingProvider())
        registry.register(FakePollingProvider())

        registry.unregister("fake_broker")
        assert [p.name for p in registry.all()] == ["fake_baseline"]
        assert registry.unregister("fake_broker") is None


# --------------------------------------------------------------------------- #
# Source Manager                                                               #
# --------------------------------------------------------------------------- #

class TestSourceManager:

    def test_highest_priority_healthy_provider_wins(self):
        registry = ProviderRegistry()
        registry.register(FakePollingProvider())
        registry.register(FakeStreamingProvider())
        manager = SourceManager(registry)

        assert manager.resolve(Capability.QUOTES).name == "fake_broker"
        assert manager.active_tier(Capability.QUOTES) is SourceTier.STREAMING

    def test_a_healthy_lower_tier_beats_a_degraded_higher_tier(self):
        registry = ProviderRegistry()
        broker = FakeStreamingProvider()
        registry.register(broker)
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        for _ in range(DEGRADED_AFTER_FAILURES):
            broker.record_failure(RuntimeError("blip"))

        assert manager.resolve(Capability.QUOTES).name == "fake_baseline"

    def test_a_degraded_provider_still_serves_when_it_is_the_only_one(self):
        registry = ProviderRegistry()
        baseline = FakePollingProvider()
        registry.register(baseline)
        manager = SourceManager(registry)

        for _ in range(DEGRADED_AFTER_FAILURES):
            baseline.record_failure(RuntimeError("blip"))

        assert manager.resolve(Capability.QUOTES) is baseline

    def test_resolution_returns_none_when_every_provider_is_down(self):
        """Not an exception: an unavailable feed is a runtime condition the
        gateway degrades through, not an error for a route handler to leak."""
        registry = ProviderRegistry()
        baseline = FakePollingProvider()
        registry.register(baseline)
        manager = SourceManager(registry)

        for _ in range(DOWN_AFTER_FAILURES):
            baseline.record_failure(RuntimeError("outage"))

        assert manager.resolve(Capability.QUOTES) is None
        assert manager.status()["state"] == FEED_UNAVAILABLE
        assert manager.status()["tier"] is None

    def test_status_never_carries_a_provider_name(self):
        """Developer Rule 4: tier is the maximum provenance a consumer receives."""
        registry = ProviderRegistry()
        registry.register(FakeStreamingProvider())
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        blob = repr(manager.status())
        assert "fake_broker" not in blob
        assert "fake_baseline" not in blob
        assert manager.status()["tier"] == "streaming"
        assert manager.status()["state"] == FEED_AVAILABLE

    def test_diagnostics_does_carry_provider_names(self):
        """The documented exception — an admin/diagnostics surface may show
        provider detail. Keeping it in a separate method is what stops
        `status()` from quietly growing one."""
        registry = ProviderRegistry()
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        assert "fake_baseline" in repr(manager.diagnostics())

    def test_provider_status_is_published_only_when_it_changes(self):
        registry = ProviderRegistry()
        baseline = FakePollingProvider()
        registry.register(baseline)
        manager = SourceManager(registry)

        with _BusSpy(PROVIDER_STATUS_TOPIC) as spy:
            _run(manager.publish_status())          # first: available
            _run(manager.publish_status())          # unchanged: silent
            assert len(spy.events) == 1

            for _ in range(DOWN_AFTER_FAILURES):
                baseline.record_failure(RuntimeError("outage"))
            _run(manager.publish_status())          # changed: unavailable

            assert len(spy.events) == 2
            latest = spy.events[-1]["data"]
            assert latest["state"] == FEED_UNAVAILABLE
            assert latest["previous_tier"] == "delayed"
            assert "fake_baseline" not in repr(latest)

    def test_user_id_is_accepted_and_inert_in_d1(self):
        """Documented as inert so no caller mistakes it for working per-user
        selection. D3 makes it load-bearing."""
        registry = ProviderRegistry()
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        assert manager.resolve(Capability.QUOTES, user_id="u1") is \
            manager.resolve(Capability.QUOTES, user_id="u2")


# --------------------------------------------------------------------------- #
# Gateway — provider independence                                              #
# --------------------------------------------------------------------------- #

class TestGatewayProviderIndependence:

    def test_gateway_serves_a_newly_registered_provider_with_no_gateway_change(self, isolated):
        """The D1 claim, made executable.

        A streaming provider speaking an entirely different payload shape is
        registered, and the gateway serves from it — selecting the matching
        normalizer and stamping the streaming tier — with no edit to the
        gateway, the Market Engine, or any consumer.
        """
        gateway, manager, registry = isolated
        registry.register(FakePollingProvider())

        quote = _run(gateway.get_quote("RELIANCE"))
        assert quote["source_tier"] == "delayed"

        broker = FakeStreamingProvider()
        registry.register(broker)

        quote = _run(gateway.get_quote("RELIANCE"))
        assert broker.calls == 1
        assert quote["source_tier"] == "streaming"
        # The broker payload keys `last_price`/`tradingsymbol` — reaching the
        # canonical `price`/`symbol` proves the broker normalizer ran.
        assert quote["price"] == 2891.5
        assert quote["symbol"] == "RELIANCE"

    def test_normalized_quotes_carry_a_tier_and_no_provider_identity(self, isolated):
        gateway, manager, registry = isolated
        registry.register(FakePollingProvider())

        quote = _run(gateway.get_quote("RELIANCE"))

        assert quote["source_tier"] in ("delayed", "streaming")
        assert quote["ingested_at"]
        assert "provider" not in quote, (
            "normalized events must not carry provider identity — "
            "MARKET_DATA_ARCHITECTURE.md Developer Rules 4 and 5"
        )
        assert "yahoo" not in repr(quote).lower()

    def test_the_payload_shape_is_identical_across_tiers(self, isolated):
        """Consumers must be correct under a mid-session provider switch, which
        requires both tiers to produce the same canonical keys — only the values
        and the update frequency may differ."""
        gateway, manager, registry = isolated
        registry.register(FakePollingProvider())
        delayed = _run(gateway.get_quote("RELIANCE"))

        registry.register(FakeStreamingProvider())
        streaming = _run(gateway.get_quote("RELIANCE"))

        assert set(delayed) == set(streaming)

    def test_a_price_update_event_is_published_for_every_served_quote(self, isolated):
        gateway, manager, registry = isolated
        registry.register(FakePollingProvider())

        with _BusSpy("price.updated") as spy:
            _run(gateway.get_quote("RELIANCE"))

        assert len(spy.events) == 1
        assert spy.events[0]["data"]["symbol"] == "RELIANCE"


# --------------------------------------------------------------------------- #
# Gateway — failure at the provider boundary                                   #
# --------------------------------------------------------------------------- #

class TestGatewayFailureBehaviour:

    def test_a_failing_provider_yields_no_data_never_fabricated_data(self, isolated):
        """The one failure mode CLAUDE.md rules out: a plausible number
        substituted for a missing one."""
        gateway, manager, registry = isolated
        registry.register(FakePollingProvider(raises=RuntimeError("upstream 503")))

        assert _run(gateway.get_quote("RELIANCE")) is None

    def test_no_provider_at_all_yields_empty_results_not_an_exception(self, isolated):
        """With an empty registry every gateway read must degrade quietly. A
        raised exception here becomes a 500 on a dashboard route."""
        gateway, manager, registry = isolated

        assert _run(gateway.get_quote("RELIANCE")) is None
        assert _run(gateway.get_universe_quotes()) == []
        assert _run(gateway.get_indices()) == {}
        assert _run(gateway.get_sectors()) == []
        assert _run(gateway.get_gainers()) == []
        assert _run(gateway.get_losers()) == []
        assert _run(gateway.get_global_markets()) == []
        assert _run(gateway.get_commodities()) == {}
        assert _run(gateway.get_chart("RELIANCE")) == []
        assert _run(gateway.search("rel")) is None

    def test_repeated_failures_take_the_provider_out_of_resolution(self, isolated):
        """Failover is a property of health bookkeeping, not of switching code:
        once the provider is DOWN the registry stops offering it, and the tier
        below takes over with nothing else involved."""
        gateway, manager, registry = isolated
        broken = FakePollingProvider(raises=RuntimeError("upstream 503"))
        registry.register(broken)

        for _ in range(DOWN_AFTER_FAILURES):
            _run(gateway.get_quote("RELIANCE"))

        assert broken.health().state is ProviderState.DOWN
        assert manager.resolve(Capability.QUOTES) is None
        assert manager.status()["state"] == FEED_UNAVAILABLE

        calls_before = broken.calls
        assert _run(gateway.get_quote("RELIANCE")) is None
        assert broken.calls == calls_before, "a DOWN provider must not be called"

    def test_a_broken_higher_tier_falls_through_to_the_baseline(self, isolated):
        """The canonical failover path in MARKET_DATA_ARCHITECTURE.md: a broker
        feed dies mid-session and the user's symbols rejoin the polled
        baseline. The feed degrades in freshness, never in availability."""
        gateway, manager, registry = isolated
        broker = FakeStreamingProvider(raises=RuntimeError("ws closed"))
        baseline = FakePollingProvider()
        registry.register(broker)
        registry.register(baseline)

        for _ in range(DOWN_AFTER_FAILURES):
            _run(gateway.get_quote("RELIANCE"))

        quote = _run(gateway.get_quote("RELIANCE"))
        assert quote is not None, "the feed must survive the loss of one provider"
        assert quote["source_tier"] == "delayed"
        assert baseline.calls >= 1

    def test_a_recovered_provider_is_promoted_back(self, isolated):
        gateway, manager, registry = isolated
        broker = FakeStreamingProvider(raises=RuntimeError("ws closed"))
        registry.register(broker)
        registry.register(FakePollingProvider())

        for _ in range(DOWN_AFTER_FAILURES):
            _run(gateway.get_quote("RELIANCE"))
        assert manager.active_tier(Capability.QUOTES) is SourceTier.DELAYED

        broker._raises = None
        broker.record_success()

        assert manager.active_tier(Capability.QUOTES) is SourceTier.STREAMING
        assert _run(gateway.get_quote("RELIANCE"))["source_tier"] == "streaming"

    def test_the_feed_status_event_fires_when_the_tier_flips(self, isolated):
        gateway, manager, registry = isolated
        registry.register(FakeStreamingProvider(raises=RuntimeError("ws closed")))
        registry.register(FakePollingProvider())

        with _BusSpy(PROVIDER_STATUS_TOPIC) as spy:
            for _ in range(DOWN_AFTER_FAILURES):
                _run(gateway.get_quote("RELIANCE"))

        tiers = [event["data"]["tier"] for event in spy.events]
        assert "delayed" in tiers, f"expected a tier flip to delayed, saw {tiers}"


# --------------------------------------------------------------------------- #
# Gateway — index normalization                                                #
# --------------------------------------------------------------------------- #

class TestIndexNormalization:

    def test_overview_keys_the_provider_supplies_survive_normalization(self, isolated):
        """`available` is what every overview consumer branches on. Normalizing
        an index must add fields, never drop them."""
        gateway, manager, registry = isolated

        class OverviewProvider(FakePollingProvider):
            capabilities = frozenset({Capability.INDICES})

            async def fetch_indices(self):
                return {
                    "nifty": {"value": 24610.0, "change": 110.0,
                              "change_pct": 0.45, "available": True},
                    "india_vix": 13.2,
                    "source": "yahoo_finance",
                }

        registry.register(OverviewProvider())
        overview = _run(gateway.get_indices())

        assert overview["nifty"]["available"] is True
        assert overview["nifty"]["value"] == 24610.0
        assert overview["nifty"]["change_pct"] == 0.45
        assert overview["india_vix"] == 13.2

    def test_the_index_normalizer_actually_runs(self, isolated):
        """Before D1 it silently never did: the provider's index sub-dicts carry
        no `name`, a nameless index fails validation, and the gateway kept the
        raw dict. The gateway now supplies the name at the boundary."""
        gateway, manager, registry = isolated

        class OverviewProvider(FakePollingProvider):
            capabilities = frozenset({Capability.INDICES})

            async def fetch_indices(self):
                return {"nifty": {"value": 24610.0, "change_pct": 0.45,
                                  "available": True}}

        registry.register(OverviewProvider())
        overview = _run(gateway.get_indices())

        assert overview["nifty"]["name"] == "NIFTY 50"
        assert overview["nifty"]["timestamp"]


# --------------------------------------------------------------------------- #
# The Yahoo migration                                                          #
# --------------------------------------------------------------------------- #

class TestYahooMigration:

    def test_yahoo_is_registered_by_default_at_baseline_priority(self):
        """MARKET_DATA_ARCHITECTURE.md guarantees Yahoo as the permanent floor:
        a user may never end up with no provider while Yahoo is reachable."""
        register_default_providers()
        yahoo = provider_registry.get("yahoo")

        assert yahoo is not None
        assert yahoo.priority == 3
        assert all(p.priority <= yahoo.priority for p in provider_registry.all()), (
            "nothing may be registered below the baseline floor"
        )

    def test_registering_yahoo_twice_does_not_duplicate_it(self):
        register_default_providers()
        register_default_providers()
        assert len([p for p in provider_registry.all() if p.name == "yahoo"]) == 1

    def test_the_adapter_delegates_to_the_hardened_yahoo_client(self):
        """D1 places `real_market` behind the contract rather than
        reimplementing it — pooled HTTP, Redis caching and error containment all
        continue to run on the production path."""
        adapter = YahooPollingAdapter()

        with patch("services.real_market.fetch_real_stock_quote",
                   new_callable=AsyncMock, return_value=dict(RAW_YAHOO_QUOTE)) as mock:
            result = _run(adapter.fetch_quote("RELIANCE"))

        mock.assert_awaited_once_with("RELIANCE")
        assert result["price"] == 2891.5

    def test_the_gateway_reaches_yahoo_only_through_the_adapter(self):
        """The gateway must hold no import of the provider client for any
        capability the registry serves."""
        source = Path(gateway_module.__file__).read_text()
        forbidden = [
            "fetch_real_stock_quote",
            "fetch_all_universe_quotes",
            "fetch_real_market_overview",
            "fetch_real_sectors",
            "fetch_real_gainers",
            "fetch_real_losers",
            "fetch_real_global_markets",
            "fetch_real_commodities",
            "fetch_real_chart_data",
            "search_yahoo_stocks",
        ]
        leaked = [name for name in forbidden if name in source]
        assert not leaked, f"gateway still imports provider client functions: {leaked}"


# --------------------------------------------------------------------------- #
# Leak guards                                                                  #
# --------------------------------------------------------------------------- #

MARKET_ENGINE_DIR = Path(gateway_module.__file__).parent

#: Modules permitted to name a provider. Per Developer Rule 1, provider names
#: may appear in exactly two places: adapter modules, and their normalizer /
#: symbol-mapping modules.
PROVIDER_NAME_ALLOWLIST = {
    "providers/yahoo.py",      # the adapter itself
    "providers/base.py",       # contract docs
    "providers/registry.py",   # registration docs
    "normalizer.py",           # one normalizer family per provider
    "gateway.py",              # normalizer-key fallback + migration rationale
    "source_manager.py",       # architecture rationale
    "gift_nifty.py",           # documents which feeds do NOT carry Gift Nifty
    "__init__.py",
    "providers/__init__.py",   # re-exports the adapter by name
}


class TestProviderLeakGuards:

    def test_no_market_engine_module_names_a_provider_outside_the_allowlist(self):
        """The rule that keeps 'add a provider' from becoming 'edit the engine'.

        A new module that hardcodes `yahoo` fails here, which is the point: the
        allowlist can only shrink, and every entry on it is one the D2/D3 work
        has to justify keeping.
        """
        offenders = {}
        for path in sorted(MARKET_ENGINE_DIR.rglob("*.py")):
            relative = str(path.relative_to(MARKET_ENGINE_DIR))
            if relative in PROVIDER_NAME_ALLOWLIST or "__pycache__" in relative:
                continue
            text = path.read_text().lower()
            hits = [name for name in ("yahoo", "zerodha", "kite", "upstox")
                    if name in text]
            if hits:
                offenders[relative] = hits

        assert not offenders, (
            "provider names outside an adapter/normalizer module: "
            f"{offenders} — see MARKET_DATA_ARCHITECTURE.md Developer Rule 1"
        )

    def test_the_ai_context_builder_does_not_import_a_provider_client(self):
        """MARKET_DATA_ARCHITECTURE.md makes the Context Builder the only door
        between the AI system and market data, and the gateway the only door to
        a provider."""
        import ast

        from services import ai_context_builder

        source = Path(ai_context_builder.__file__).read_text()

        # Parsed rather than grepped: the module's own docstring explains why it
        # no longer calls `real_market`, and a substring check would match that
        # prose and pass for the wrong reason — or fail forever once someone
        # rewords it. Only real import statements count.
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(alias.name for alias in node.names)

        assert not {name for name in imported if "real_market" in name}, (
            "the AI Context Builder imports the provider client directly — "
            "every market read must go through the Market Gateway"
        )
        assert "market_gateway" in imported

    def test_the_engine_status_payload_is_provider_free(self):
        """`MarketGateway.status` is served by `/api/market/engine/status`, so
        it is a public surface. It gained a `feed` block in D1 — that block is
        the frontend's future tier indicator and must carry tier, never a
        provider name."""
        from services.market_engine import market_gateway

        register_default_providers()
        status = market_gateway.status

        assert status["feed"]["tier"] in ("delayed", "streaming", None)
        assert "yahoo" not in repr(status).lower()

    def test_normalized_quotes_from_every_family_are_provider_free(self):
        """Whichever normalizer runs, the output must be indistinguishable in
        provenance — that is what makes a mid-session switch invisible."""
        from services.market_engine.normalizer import normalize_stock_quote

        for family, raw in (
            ("yahoo", RAW_YAHOO_QUOTE),
            ("broker", RAW_BROKER_QUOTE),
            ("alpha_vantage", {"symbol": "RELIANCE", "price": 2891.5}),
        ):
            normalized = normalize_stock_quote(dict(raw), provider=family)
            assert "provider" not in normalized, (
                f"the {family} normalizer still stamps a provider name"
            )


# --------------------------------------------------------------------------- #
# The gateway-bypass register                                                  #
# --------------------------------------------------------------------------- #

#: Modules that still reach `services.real_market` without going through the
#: Market Gateway, frozen as of D1.
#:
#: WHY THIS EXISTS. D1 migrated the Market Engine and the AI layer. The API
#: routes and several engines still call the provider client directly, and
#: moving them is genuinely blocked on a shape reconciliation the API contract
#: cannot absorb inside one sprint — `/api/market/sectors` returns the provider's
#: `{"sector": …}` while the gateway returns the canonical `{"name": …}`, and
#: the frontend reads the former.
#:
#: A register with a test behind it is the difference between a known debt and a
#: forgotten one. This set may only SHRINK: a new bypass fails here, and D2
#: closes the remaining entries. Deleting an entry after migrating a module is
#: the intended way to interact with this list.
KNOWN_GATEWAY_BYPASSES = frozenset({
    "server.py",
    "services/heartbeat_engine.py",
    "services/morning_report.py",
    "services/paper_trade.py",
    "services/portfolio_engine.py",
    "services/portfolio_stream.py",
    "services/scheduler.py",
    "services/stock_details.py",
    "services/market_engine/gateway.py",  # FII/DII + patterns, documented
})

BACKEND_DIR = MARKET_ENGINE_DIR.parent.parent


class TestGatewayBypassRegister:

    def test_no_new_module_bypasses_the_market_gateway(self):
        """Developer Rule 2: nothing fetches from a provider directly — not for
        a quick script, not for a one-off endpoint, not "temporarily"."""
        skip_dirs = {"tests", "venv", "__pycache__", ".venv"}
        bypasses = set()

        for path in sorted(BACKEND_DIR.rglob("*.py")):
            relative = path.relative_to(BACKEND_DIR)
            if set(relative.parts) & skip_dirs:
                continue
            if relative.name == "real_market.py":
                continue
            # Adapter modules are exactly where the provider client belongs.
            if "providers" in relative.parts:
                continue
            text = path.read_text()
            if "real_market import" in text or "import real_market" in text:
                bypasses.add(str(relative))

        new = bypasses - KNOWN_GATEWAY_BYPASSES
        assert not new, (
            f"new Market Gateway bypass introduced: {sorted(new)}. Market data "
            "must be read through `market_gateway`, never from the provider "
            "client — MARKET_DATA_ARCHITECTURE.md Developer Rule 2."
        )

    def test_the_register_does_not_list_modules_that_no_longer_bypass(self):
        """Keeps the register honest as D2 migrates modules off the provider
        client — a stale entry would let a genuine regression back in silently."""
        skip_dirs = {"tests", "venv", "__pycache__", ".venv"}
        bypasses = set()

        for path in sorted(BACKEND_DIR.rglob("*.py")):
            relative = path.relative_to(BACKEND_DIR)
            if set(relative.parts) & skip_dirs:
                continue
            if relative.name == "real_market.py":
                continue
            # Adapter modules are exactly where the provider client belongs.
            if "providers" in relative.parts:
                continue
            text = path.read_text()
            if "real_market import" in text or "import real_market" in text:
                bypasses.add(str(relative))

        stale = KNOWN_GATEWAY_BYPASSES - bypasses
        assert not stale, (
            f"these modules no longer bypass the gateway: {sorted(stale)} — "
            "remove them from KNOWN_GATEWAY_BYPASSES"
        )

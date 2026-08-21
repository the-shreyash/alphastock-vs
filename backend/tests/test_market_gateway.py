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
        self.pushed = []

    async def fetch_quote(self, symbol):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._quote

    async def on_raw(self, payload):
        """The push entry point every streaming provider must have (D4.4).

        A double for a broker feed that could not be pushed into would not be a
        double for a broker feed. Registration refuses one, which is what makes
        this three-line method a fixture correction rather than a workaround.
        """
        self.pushed.append(payload)
        await self._emit(payload)
        return 1


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

    def test_a_globally_entitled_provider_serves_every_user(self):
        """D1 accepted `user_id` and ignored it; D2 honours it. A provider with
        a platform-wide entitlement — Yahoo, a licensed feed — must still
        resolve identically for everyone, or making the parameter load-bearing
        would have quietly partitioned the free tier."""
        registry = ProviderRegistry()
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        assert manager.resolve(Capability.QUOTES, user_id="u1") is \
            manager.resolve(Capability.QUOTES, user_id="u2")
        assert manager.resolve(Capability.QUOTES) is \
            manager.resolve(Capability.QUOTES, user_id="u1")


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


# =========================================================================== #
# Phase D2 — Source Manager                                                    #
# =========================================================================== #
#
# D1 proved a provider could be swapped without touching the platform. D2's
# claim is narrower and sharper: the *right* provider is chosen, for the right
# request, and a request survives the preferred provider failing underneath it.
#
# Every test below is written so that reverting the D2 change turns it red. The
# failover tests in particular assert on call counts and on the tier of the
# answer, not merely that "something came back" — D1 already returned something
# eventually, and a test that cannot tell "recovered after eight failed
# requests" from "recovered inside this one" would pass against the bug D2
# exists to close.

from services.market_engine.providers import ResolutionContext  # noqa: E402
from services.market_engine.source_manager import (  # noqa: E402
    HEALTH_RANK,
    Resolution,
    UnavailableReason,
)


class UserScopedProvider(MarketDataProvider):
    """A provider entitled to exactly one user — the shape every broker adapter
    takes in D3, where the feed is legally the user's own data."""

    name = "fake_user_broker"
    kind = ProviderKind.STREAMING
    tier = SourceTier.STREAMING
    normalizer_key = "broker"
    priority = 1
    capabilities = frozenset({Capability.QUOTES})

    def __init__(self, owner_user_id, quote=None, raises=None):
        super().__init__()
        self.owner_user_id = owner_user_id
        self._quote = quote if quote is not None else dict(RAW_BROKER_QUOTE)
        self._raises = raises
        self.calls = 0
        self.pushed = []

    async def fetch_quote(self, symbol):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._quote

    async def on_raw(self, payload):
        self.pushed.append(payload)
        await self._emit(payload)
        return 1


class SymbolScopedProvider(FakeStreamingProvider):
    """Partial instrument coverage — the "broker carries NSE equities but not a
    US index" case MARKET_DATA_ARCHITECTURE.md calls out by name."""

    name = "fake_partial_broker"

    def __init__(self, covers, **kwargs):
        super().__init__(**kwargs)
        self._covers = set(covers)

    def is_eligible_for(self, context):
        if not super().is_eligible_for(context):
            return False
        return context.symbol is None or context.symbol in self._covers


# --------------------------------------------------------------------------- #
# Health — the fourth state                                                    #
# --------------------------------------------------------------------------- #

class TestUnknownHealthState:

    def test_a_never_called_provider_reports_unknown_not_up(self):
        """D1 started every provider at UP, so a broker registered one
        millisecond ago and a baseline with ten thousand clean requests behind
        it read identically on the one surface whose job is telling them
        apart."""
        provider = FakePollingProvider()

        assert provider.health().state is ProviderState.UNKNOWN
        assert provider.describe()["health"]["state"] == "unknown"

    def test_the_first_success_moves_unknown_to_up(self):
        provider = FakePollingProvider()

        assert provider.record_success() is ProviderState.UP
        assert provider.health().state is ProviderState.UP

    def test_an_empty_first_answer_leaves_the_provider_unknown(self):
        """A 200-with-no-data demonstrates reachability, not health. Promoting
        on it is how a silently empty feed keeps its primary slot."""
        provider = FakePollingProvider()

        provider.record_success(empty=True)

        assert provider.health().state is ProviderState.UNKNOWN

    def test_reset_returns_a_provider_to_unknown_not_to_up(self):
        provider = FakePollingProvider()
        provider.record_success()

        provider.reset_health()

        assert provider.health().state is ProviderState.UNKNOWN

    def test_all_four_states_are_reachable_and_distinct(self):
        """The brief's four-state requirement, made executable rather than
        asserted: unknown / healthy / degraded / unavailable."""
        provider = FakePollingProvider()
        seen = [provider.health().state]

        provider.record_success()
        seen.append(provider.health().state)
        for _ in range(DEGRADED_AFTER_FAILURES):
            provider.record_failure(RuntimeError("blip"))
        seen.append(provider.health().state)
        for _ in range(DOWN_AFTER_FAILURES - DEGRADED_AFTER_FAILURES):
            provider.record_failure(RuntimeError("outage"))
        seen.append(provider.health().state)

        assert seen == [
            ProviderState.UNKNOWN,
            ProviderState.UP,
            ProviderState.DEGRADED,
            ProviderState.DOWN,
        ]

    def test_unknown_is_not_ranked_below_up(self):
        """Load-bearing, not cosmetic. Ranking UNKNOWN below UP deadlocks the
        priority algorithm: a new priority-1 broker leaves UNKNOWN only by being
        called and is called only by being selected, so it would sit behind a
        healthy baseline forever and the platform's headline feature would never
        engage."""
        assert HEALTH_RANK[ProviderState.UNKNOWN] == HEALTH_RANK[ProviderState.UP]
        assert HEALTH_RANK[ProviderState.DEGRADED] > HEALTH_RANK[ProviderState.UP]

    def test_a_freshly_registered_higher_tier_provider_is_selected_immediately(self):
        """The deadlock above, driven end to end."""
        registry = ProviderRegistry()
        baseline = FakePollingProvider()
        registry.register(baseline)
        manager = SourceManager(registry)
        for _ in range(5):
            baseline.record_success()

        broker = FakeStreamingProvider()          # never called: UNKNOWN
        registry.register(broker)

        assert manager.resolve(Capability.QUOTES) is broker


# --------------------------------------------------------------------------- #
# Capability resolution                                                        #
# --------------------------------------------------------------------------- #

class TestCapabilityResolution:

    def test_a_capability_resolves_without_any_caller_naming_a_provider(self):
        """The whole point of the D2 brief: the caller says "I need quotes",
        never "give me Yahoo"."""
        registry = ProviderRegistry()
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        resolution = manager.resolve_feed(Capability.QUOTES)

        assert resolution.available
        assert resolution.provider.supports(Capability.QUOTES)

    def test_different_capabilities_can_resolve_to_different_providers(self):
        """A broker serves ticks and a baseline serves sectors; neither call
        site knows either name."""
        registry = ProviderRegistry()
        broker = FakeStreamingProvider()      # QUOTES + TICKS, priority 1
        baseline = FakePollingProvider()      # QUOTES + SECTORS, priority 3
        registry.register(broker)
        registry.register(baseline)
        manager = SourceManager(registry)

        assert manager.resolve(Capability.TICKS) is broker
        assert manager.resolve(Capability.SECTORS) is baseline
        assert manager.resolve(Capability.QUOTES) is broker

    def test_a_capability_no_provider_serves_resolves_to_nothing(self):
        registry = ProviderRegistry()
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        resolution = manager.resolve_feed(Capability.DEPTH)

        assert not resolution.available
        assert resolution.reason is UnavailableReason.CAPABILITY_UNSUPPORTED

    def test_a_provider_is_never_asked_for_a_capability_it_lacks(self):
        """The Source Manager filters before the gateway calls, so
        `CapabilityUnavailable` — a programming error, not a runtime condition —
        cannot be reached through the gateway."""
        registry = ProviderRegistry()
        baseline = FakePollingProvider()
        registry.register(baseline)
        manager = SourceManager(registry)

        assert manager.resolve(Capability.DEPTH) is None
        assert baseline.calls == 0

    def test_the_capability_set_is_the_one_the_providers_declare(self):
        """No second capability vocabulary: `status()` reports exactly the
        capabilities registered providers declare, computed from the same enum
        the adapters use."""
        registry = ProviderRegistry()
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        served = set(manager.status()["capabilities"])

        assert served == {c.value for c in FakePollingProvider.capabilities}


# --------------------------------------------------------------------------- #
# Explicit unavailable results                                                 #
# --------------------------------------------------------------------------- #

class TestExplicitUnavailableState:
    """Four incidents D1 reported as one silence."""

    def test_an_empty_registry_says_so(self):
        manager = SourceManager(ProviderRegistry())

        resolution = manager.resolve_feed(Capability.QUOTES)

        assert resolution.reason is UnavailableReason.NO_PROVIDERS_REGISTERED
        assert resolution.provider is None
        assert resolution.chain == ()

    def test_an_unentitled_user_is_distinguished_from_an_outage(self):
        registry = ProviderRegistry()
        registry.register(UserScopedProvider("owner"))
        manager = SourceManager(registry)

        assert manager.resolve_feed(Capability.QUOTES, user_id="someone_else").reason \
            is UnavailableReason.NOT_ENTITLED

    def test_an_unserved_capability_is_distinguished_from_an_outage(self):
        registry = ProviderRegistry()
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        assert manager.resolve_feed(Capability.DEPTH).reason \
            is UnavailableReason.CAPABILITY_UNSUPPORTED

    def test_a_total_outage_says_so(self):
        registry = ProviderRegistry()
        baseline = FakePollingProvider()
        registry.register(baseline)
        manager = SourceManager(registry)

        for _ in range(DOWN_AFTER_FAILURES):
            baseline.record_failure(RuntimeError("outage"))

        assert manager.resolve_feed(Capability.QUOTES).reason \
            is UnavailableReason.ALL_PROVIDERS_DOWN

    def test_the_reason_reaches_feed_status_without_a_provider_name(self):
        registry = ProviderRegistry()
        baseline = FakePollingProvider()
        registry.register(baseline)
        manager = SourceManager(registry)
        for _ in range(DOWN_AFTER_FAILURES):
            baseline.record_failure(RuntimeError("outage"))

        status = manager.status()

        assert status["state"] == FEED_UNAVAILABLE
        assert status["reason"] == "all_providers_down"
        assert "fake_baseline" not in repr(status)

    def test_an_available_feed_carries_no_reason(self):
        registry = ProviderRegistry()
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        assert manager.status()["reason"] is None

    def test_the_gateway_records_the_unavailable_state_explicitly(self, isolated):
        """D1 returned a bare `[]` here, indistinguishable from "the provider
        answered and there were no gainers"."""
        gateway, manager, registry = isolated

        assert _run(gateway.get_gainers()) == []

        recorded = gateway.status["last_unavailable"]
        assert recorded["capability"] == "movers"
        assert recorded["reason"] == "no_providers_registered"
        assert "yahoo" not in repr(recorded)

    def test_the_recorded_unavailable_state_clears_once_served(self, isolated):
        gateway, manager, registry = isolated
        _run(gateway.get_quote("RELIANCE"))
        assert gateway.status["last_unavailable"] is not None

        registry.register(FakePollingProvider())
        _run(gateway.get_quote("RELIANCE"))

        assert gateway.status["last_unavailable"] is None


# --------------------------------------------------------------------------- #
# Failover foundation                                                          #
# --------------------------------------------------------------------------- #

class TestFailoverChain:

    def test_the_chain_lists_the_preferred_provider_first(self):
        registry = ProviderRegistry()
        registry.register(FakePollingProvider())
        registry.register(FakeStreamingProvider())
        manager = SourceManager(registry)

        chain = manager.failover_chain(Capability.QUOTES)

        assert [p.name for p in chain] == ["fake_broker", "fake_baseline"]
        assert chain[0] is manager.resolve(Capability.QUOTES)

    def test_a_degraded_preferred_provider_is_demoted_within_the_chain(self):
        registry = ProviderRegistry()
        broker = FakeStreamingProvider()
        registry.register(broker)
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        for _ in range(DEGRADED_AFTER_FAILURES):
            broker.record_failure(RuntimeError("blip"))

        chain = manager.failover_chain(Capability.QUOTES)

        assert [p.name for p in chain] == ["fake_baseline", "fake_broker"]

    def test_a_down_provider_leaves_the_chain_entirely(self):
        registry = ProviderRegistry()
        broker = FakeStreamingProvider()
        registry.register(broker)
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        for _ in range(DOWN_AFTER_FAILURES):
            broker.record_failure(RuntimeError("outage"))

        assert [p.name for p in manager.failover_chain(Capability.QUOTES)] \
            == ["fake_baseline"]

    def test_the_gateway_fails_over_inside_a_single_request(self, isolated):
        """The D2 claim, and the one D1 could not make.

        D1 called the preferred provider alone and returned nothing on failure;
        the baseline only took over after DOWN_AFTER_FAILURES *whole requests*
        had each served a user an empty dashboard. Asserting on the first
        request is what makes this test fail against D1."""
        gateway, manager, registry = isolated
        broker = FakeStreamingProvider(raises=RuntimeError("ws closed"))
        baseline = FakePollingProvider()
        registry.register(broker)
        registry.register(baseline)

        quote = _run(gateway.get_quote("RELIANCE"))

        assert quote is not None, "the very first request must already recover"
        assert quote["source_tier"] == "delayed"
        assert broker.calls == 1, "the preferred provider is still tried first"
        assert baseline.calls == 1, "and the next eligible one answers"

    def test_failover_does_not_fabricate_when_every_provider_fails(self, isolated):
        """The chain must exhaust into nothing, never into a plausible
        number — the one failure mode CLAUDE.md rules out."""
        gateway, manager, registry = isolated
        broker = FakeStreamingProvider(raises=RuntimeError("ws closed"))
        baseline = FakePollingProvider(raises=RuntimeError("upstream 503"))
        registry.register(broker)
        registry.register(baseline)

        assert _run(gateway.get_quote("RELIANCE")) is None
        assert broker.calls == 1 and baseline.calls == 1

    def test_an_empty_answer_does_not_trigger_failover(self, isolated):
        """An empty gainers list at 3am is the correct answer. Failing over on
        it would double every provider call on a quiet market to produce the
        same empty list."""
        gateway, manager, registry = isolated
        broker = FakeStreamingProvider(quote={})
        baseline = FakePollingProvider()
        registry.register(broker)
        registry.register(baseline)

        _run(gateway.get_quote("RELIANCE"))

        assert broker.calls == 1
        assert baseline.calls == 0, "an empty result is an answer, not a failure"

    def test_in_request_failover_does_not_mask_the_failure(self, isolated):
        """Serving the user from the baseline must not hide that the preferred
        provider is broken. Without health damage accruing, a permanently dead
        preferred provider would be called first on every request forever and
        every request would pay its timeout."""
        gateway, manager, registry = isolated
        broker = FakeStreamingProvider(raises=RuntimeError("ws closed"))
        baseline = FakePollingProvider()
        registry.register(broker)
        registry.register(baseline)

        for _ in range(DEGRADED_AFTER_FAILURES):
            assert _run(gateway.get_quote("RELIANCE"))["source_tier"] == "delayed"

        assert broker.health().state is ProviderState.DEGRADED

    def test_a_demoted_provider_stops_being_called(self, isolated):
        """Once demoted below a healthy peer it is last in the chain, and the
        chain stops at the first provider that answers. This is the intended
        outcome — a broken provider costs nothing — and it is also the reason a
        demoted provider cannot recover on its own (see the test below)."""
        gateway, manager, registry = isolated
        broker = FakeStreamingProvider(raises=RuntimeError("ws closed"))
        registry.register(broker)
        registry.register(FakePollingProvider())

        for _ in range(DEGRADED_AFTER_FAILURES):
            _run(gateway.get_quote("RELIANCE"))
        calls_at_demotion = broker.calls

        for _ in range(5):
            _run(gateway.get_quote("RELIANCE"))

        assert broker.calls == calls_at_demotion

    def test_a_demoted_provider_has_no_self_recovery_path_in_d2(self):
        """A KNOWN D2 LIMITATION, pinned so it cannot regress silently and so
        D5 has a red-to-green target.

        A demoted provider is never called, and health only improves on a
        successful call, so a broker that blips three times stays on the delayed
        tier until something external revives it. MARKET_DATA_ARCHITECTURE.md
        assigns the fix — probation windows and periodic re-probing — to Phase 5
        (sprint D5). Building a re-probe scheduler here would be the
        "sophisticated automatic failover policy" D2 is explicitly scoped out
        of, and it needs a clock source and a background sweeper that D2 has no
        other use for.

        Until then, recovery happens on process restart or on an explicit
        `record_success` from the connection layer that D3 introduces — which is
        the natural owner, because a broker adapter learns its WebSocket
        reconnected without anyone polling it.
        """
        registry = ProviderRegistry()
        broker = FakeStreamingProvider(raises=RuntimeError("ws closed"))
        registry.register(broker)
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        for _ in range(DEGRADED_AFTER_FAILURES):
            broker.record_failure(RuntimeError("ws closed"))
        assert manager.active_tier(Capability.QUOTES) is SourceTier.DELAYED

        # Nothing the Source Manager does on its own restores it...
        for _ in range(10):
            manager.resolve_feed(Capability.QUOTES)
        assert manager.active_tier(Capability.QUOTES) is SourceTier.DELAYED

        # ...but the connection layer telling it so does. D3 wires this to a
        # reconnected WebSocket.
        broker.record_success()
        assert manager.active_tier(Capability.QUOTES) is SourceTier.STREAMING

    def test_a_recovered_provider_re_enters_the_chain_at_its_priority(self, isolated):
        gateway, manager, registry = isolated
        broker = FakeStreamingProvider(raises=RuntimeError("ws closed"))
        registry.register(broker)
        registry.register(FakePollingProvider())
        for _ in range(DOWN_AFTER_FAILURES):
            _run(gateway.get_quote("RELIANCE"))

        broker._raises = None
        broker.record_success()

        assert _run(gateway.get_quote("RELIANCE"))["source_tier"] == "streaming"


# --------------------------------------------------------------------------- #
# User / request context                                                       #
# --------------------------------------------------------------------------- #

class TestResolutionContext:

    def test_a_user_scoped_provider_serves_only_its_owner(self):
        """Category 2's cornerstone made enforceable: a broker feed is the
        user's own entitlement and must never serve anybody else."""
        registry = ProviderRegistry()
        broker = UserScopedProvider("user_a")
        baseline = FakePollingProvider()
        registry.register(broker)
        registry.register(baseline)
        manager = SourceManager(registry)

        assert manager.resolve(Capability.QUOTES, user_id="user_a") is broker
        assert manager.resolve(Capability.QUOTES, user_id="user_b") is baseline

    def test_a_request_with_no_user_never_reaches_a_user_scoped_provider(self):
        """A scheduled universe refresh or a scanner sweep has no user
        attached; resolving somebody's broker feed for it would consume their
        entitlement for platform-wide work."""
        registry = ProviderRegistry()
        broker = UserScopedProvider("user_a")
        registry.register(broker)
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        assert manager.resolve(Capability.QUOTES).name == "fake_baseline"

    def test_the_user_scoped_provider_is_absent_from_another_users_chain(self):
        registry = ProviderRegistry()
        registry.register(UserScopedProvider("user_a"))
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        chain = manager.failover_chain(Capability.QUOTES, user_id="user_b")

        assert [p.name for p in chain] == ["fake_baseline"]

    def test_per_symbol_coverage_can_send_one_symbol_down_a_different_tier(self):
        """MARKET_DATA_ARCHITECTURE.md by name: "a broker feed covering NSE
        equities does not disqualify Yahoo from serving a US index the broker
        doesn't carry"."""
        registry = ProviderRegistry()
        partial = SymbolScopedProvider(covers={"RELIANCE"})
        baseline = FakePollingProvider()
        registry.register(partial)
        registry.register(baseline)
        manager = SourceManager(registry)

        covered = ResolutionContext(symbol="RELIANCE")
        uncovered = ResolutionContext(symbol="SPX")

        assert manager.resolve(Capability.QUOTES, context=covered) is partial
        assert manager.resolve(Capability.QUOTES, context=uncovered) is baseline

    def test_the_gateway_supplies_the_symbol_so_call_sites_do_not(self, isolated):
        """Per-symbol routing must work through the public gateway API with no
        caller change — otherwise D3 has to touch every call site after all."""
        gateway, manager, registry = isolated
        partial = SymbolScopedProvider(covers={"RELIANCE"})
        baseline = FakePollingProvider()
        registry.register(partial)
        registry.register(baseline)

        assert _run(gateway.get_quote("RELIANCE"))["source_tier"] == "streaming"
        assert _run(gateway.get_quote("SPX"))["source_tier"] == "delayed"

    def test_context_and_user_id_are_two_spellings_of_one_thing(self):
        registry = ProviderRegistry()
        registry.register(UserScopedProvider("user_a"))
        manager = SourceManager(registry)

        assert manager.resolve(Capability.QUOTES, user_id="user_a") is \
            manager.resolve(Capability.QUOTES,
                            context=ResolutionContext(user_id="user_a"))

    def test_context_carries_no_provider_preference(self):
        """The context describes the *request*, never the answer. A field named
        after a provider here would reintroduce caller-side selection through
        the back door.

        `capability` joined the set in D4.5 and belongs to the request side of
        that line: it says what is being asked for, not who should answer, and
        it is stamped by the registry rather than supplied by a caller. The
        guard the assertion actually encodes — no field a caller could use to
        express a preference for a particular provider — is re-stated below so
        widening the set cannot quietly become a way to relax it.
        """
        fields = set(ResolutionContext.__dataclass_fields__)

        assert fields == {"user_id", "symbol", "exchange", "capability"}
        # Every field names a property of the request. None names a provider,
        # a tier, a priority, or a preference.
        forbidden = ("provider", "tier", "priority", "prefer", "source", "adapter")
        assert not [f for f in fields if any(word in f for word in forbidden)]


# --------------------------------------------------------------------------- #
# The Market Engine asks by capability, never by name                          #
# --------------------------------------------------------------------------- #

class TestMarketEngineDecoupling:

    def test_the_market_engine_never_names_a_provider_when_asking_for_data(self):
        """Every gateway read resolves through the Source Manager by
        capability. A `registry.get("yahoo")` or a `provider.name ==` anywhere
        outside the adapters would be caller-side selection."""
        engine_dir = MARKET_ENGINE_DIR
        offenders = []

        for path in sorted(engine_dir.rglob("*.py")):
            if "providers" in path.relative_to(engine_dir).parts:
                continue
            text = path.read_text()
            for needle in ('registry.get("', "registry.get('", ".name =="):
                if needle in text:
                    offenders.append(f"{path.name}: {needle}")

        assert not offenders, (
            f"provider selected by name inside the Market Engine: {offenders}. "
            "Ask for a capability — MARKET_DATA_ARCHITECTURE.md Developer Rule 3."
        )

    def test_every_gateway_read_goes_through_a_capability(self, isolated):
        """Structural: each public gateway read resolves a Capability, so none
        can quietly acquire a provider of its own."""
        gateway, manager, registry = isolated
        registry.register(FakePollingProvider())
        resolved = []

        original = manager.resolve_feed

        def spy(capability, context=None, **kwargs):
            resolved.append(capability)
            return original(capability, context, **kwargs)

        manager.resolve_feed = spy
        _run(gateway.get_quote("RELIANCE"))
        _run(gateway.get_sectors())
        _run(gateway.get_gainers())
        manager.resolve_feed = original

        assert Capability.QUOTES in resolved
        assert Capability.SECTORS in resolved
        assert Capability.MOVERS in resolved
        assert all(isinstance(c, Capability) for c in resolved)

    def test_a_resolution_is_explicit_in_both_directions(self):
        """No third shape: a Resolution either has a provider and a chain, or a
        reason. A caller never has to infer an outage from an empty value."""
        registry = ProviderRegistry()
        manager = SourceManager(registry)

        unavailable = manager.resolve_feed(Capability.QUOTES)
        registry.register(FakePollingProvider())
        available = manager.resolve_feed(Capability.QUOTES)

        assert isinstance(unavailable, Resolution) and isinstance(available, Resolution)
        assert (unavailable.provider is None) and (unavailable.reason is not None)
        assert (available.provider is not None) and (available.reason is None)
        assert available.chain[0] is available.provider


# --------------------------------------------------------------------------- #
# AI decoupling                                                                #
# --------------------------------------------------------------------------- #

class TestAIProviderDecoupling:

    #: Every AI-side module. Named explicitly rather than globbed: a substring
    #: match on "ai" also matches `stock_det-ai-ls.py`, which is not an AI
    #: module, and a guard that fires on the wrong file gets deleted rather
    #: than fixed.
    AI_MODULES = (
        "ai_activity.py",
        "ai_context_builder.py",
        "ai_debate_engine.py",
        "ai_memory.py",
        "ai_provider.py",
        "claude_provider.py",
        "gemini_direct.py",
        "gemini_provider.py",
        "model_router.py",
        "prompt_library.py",
        "trade_review.py",
    )

    def test_no_ai_module_imports_a_provider_client_or_the_registry(self):
        """The AI Context Builder is the only door between the AI system and
        market data, and it consumes normalized context. An AI module importing
        the provider client, the registry, or the Source Manager would be
        selecting a provider — Developer Rule 5.

        Parsed rather than grepped: `ai_context_builder.py`'s own docstring
        explains why it no longer calls `real_market`, and a substring check
        would match that prose and fail forever once someone reworded it.
        """
        import ast

        services_dir = MARKET_ENGINE_DIR.parent
        offenders = {}
        checked = 0

        for name in self.AI_MODULES:
            path = services_dir / name
            if not path.exists():
                continue
            checked += 1
            imported = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add(node.module or "")
                    imported.update(alias.name for alias in node.names)

            hits = sorted(
                item for item in imported
                if any(needle in item for needle in
                       ("real_market", "provider_registry", "source_manager",
                        "zerodha_service", "alpha_vantage"))
            )
            if hits:
                offenders[name] = hits

        assert checked >= 8, (
            f"only {checked} AI modules found — the guard would pass vacuously; "
            "update AI_MODULES if the layout changed"
        )
        assert not offenders, (
            f"AI module reaching for a provider: {offenders}. AI consumes "
            "normalized market context — MARKET_DATA_ARCHITECTURE.md "
            "Developer Rule 5."
        )

    def test_the_ai_context_builder_selects_no_provider(self):
        """It may call the gateway; it may not resolve, rank, or name one."""
        from services import ai_context_builder

        source = Path(ai_context_builder.__file__).read_text()
        code_lines = [
            line for line in source.splitlines()
            if not line.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)

        for needle in ("resolve_feed(", "resolve(", "failover_chain(",
                       "provider_registry", "normalizer_key"):
            assert needle not in code, (
                f"the AI Context Builder performs provider selection ({needle})"
            )

    def test_the_ai_facing_status_payload_carries_tier_and_reason_only(self):
        """What the AI may learn about the feed: how fresh it is and whether it
        is being served. Never who is serving it."""
        registry = ProviderRegistry()
        registry.register(FakeStreamingProvider())
        registry.register(UserScopedProvider("user_a"))
        manager = SourceManager(registry)

        status = manager.status(user_id="user_a")

        assert set(status) == {"state", "tier", "reason", "capabilities"}
        blob = repr(status)
        for name in ("fake_broker", "fake_user_broker", "yahoo", "zerodha"):
            assert name not in blob

    def test_diagnostics_remains_the_only_surface_with_names(self):
        registry = ProviderRegistry()
        registry.register(FakeStreamingProvider())
        registry.register(FakePollingProvider())
        manager = SourceManager(registry)

        diagnostics = manager.diagnostics()

        assert diagnostics["selected_for_quotes"] == "fake_broker"
        assert diagnostics["failover_chain"] == ["fake_broker", "fake_baseline"]
        assert "fake_broker" not in repr(diagnostics["feed"])


# =========================================================================== #
# DD-1 — the public REST contract carries no provider identity                 #
# =========================================================================== #
#
# The guard that makes DD-1 stay closed.
#
# Until DD-1 the public contract returned `source: "yahoo_finance"` (and
# `data_source` on the advisor). Two separate defects lived in that one string.
# It named a provider on a public surface, which Developer Rule 4 forbids. And
# it was a *literal*, so the day a broker feed served a quote the field would
# have kept reporting "yahoo_finance" — `InvestmentAdvisor.jsx` branched on
# exactly that value to choose between "Live market data" and "Fallback data",
# so a streaming broker quote would have been shown to the user as fallback.
#
# These tests sweep responses rather than source text: a provider name that
# reaches a client is the thing that matters, and a grep over handlers would
# miss one assembled at runtime while failing on a comment.

#: Every provider name that must never appear in a market-data response.
FORBIDDEN_PROVIDER_NAMES = (
    "yahoo", "yahoo_finance", "yfinance",
    "zerodha", "kite", "upstox", "angelone", "fyers", "dhan",
    "alpha_vantage", "alphavantage",
)

#: Public market-data endpoints and the provider-client function that feeds each.
#: Patched at the service module because every adapter imports it function-local
#: and looks it up at call time.
PUBLIC_MARKET_ENDPOINTS = [
    ("/api/market/overview", "services.real_market.fetch_real_market_overview",
     {"nifty": {"value": 24000.0, "change": 10.0, "change_pct": 0.04},
      "bank_nifty": {"value": 51000.0, "change": 5.0, "change_pct": 0.01},
      "sensex": {"value": 80000.0, "change": 20.0, "change_pct": 0.03},
      "india_vix": 13.2, "market_sentiment": "neutral", "market_status": "OPEN"}),
    ("/api/market/gainers", "services.real_market.fetch_real_gainers",
     [{"symbol": "RELIANCE", "change_pct": 3.1}]),
    ("/api/market/losers", "services.real_market.fetch_real_losers",
     [{"symbol": "TCS", "change_pct": -2.4}]),
    ("/api/market/sectors", "services.real_market.fetch_real_sectors",
     [{"sector": "IT", "change_pct": 1.2}]),
    ("/api/market/global", "services.real_market.fetch_real_global_markets",
     [{"name": "Dow Jones", "region": "US", "value": 39000.0, "change_pct": 0.2}]),
    ("/api/market/commodities", "services.real_market.fetch_real_commodities",
     {"gold": {"price": 62000.0, "change_pct": 0.4}}),
]


class TestPublicContractCarriesNoProviderIdentity:

    @pytest.mark.parametrize(
        "endpoint,target,payload", PUBLIC_MARKET_ENDPOINTS,
        ids=[e for e, _, _ in PUBLIC_MARKET_ENDPOINTS])
    def test_no_provider_name_appears_in_a_market_response(
            self, client, endpoint, target, payload):
        with patch(target, new_callable=AsyncMock, return_value=payload):
            resp = client.get(endpoint)

        assert resp.status_code == 200, resp.text
        blob = resp.text.lower()
        leaked = [name for name in FORBIDDEN_PROVIDER_NAMES if name in blob]
        assert not leaked, (
            f"{endpoint} leaked provider identity {leaked} into the public "
            "contract — MARKET_DATA_ARCHITECTURE.md Developer Rule 4"
        )

    def test_the_guard_would_catch_a_leak(self, client):
        """The control. Without it, a sweep reading the wrong payload — or an
        endpoint quietly 500ing — would pass silently forever.

        Planted on `/api/market/gainers` rather than `/api/market/sectors`
        because gainers is a passthrough: the gateway returns the provider's
        rows unchanged, so an invented `source` really does reach the client.
        Sector rows go through `normalize_sector_data`, whose canonical output
        drops unknown keys — a leak planted there is stripped by the
        architecture itself and would prove nothing about the sweep.
        """
        leaky = [{"symbol": "RELIANCE", "change_pct": 3.1, "source": "yahoo_finance"}]
        with patch("services.real_market.fetch_real_gainers",
                   new_callable=AsyncMock, return_value=leaky):
            blob = client.get("/api/market/gainers").text.lower()

        assert any(name in blob for name in FORBIDDEN_PROVIDER_NAMES), (
            "the sweep cannot observe a provider name in a response, so the "
            "tests above prove nothing"
        )

    def test_the_overview_declares_a_freshness_tier(self, client):
        """`source_tier` replaces `source`: what a consumer legitimately needs
        (how fresh) without what it must never couple to (who served it)."""
        endpoint, target, payload = PUBLIC_MARKET_ENDPOINTS[0]
        with patch(target, new_callable=AsyncMock, return_value=payload):
            body = client.get(endpoint).json()

        assert body["available"] is True
        assert body["source_tier"] in ("delayed", "streaming")
        assert "source" not in body

    def test_the_sector_shape_carries_both_canonical_and_legacy_keys(self, client):
        """The DD-1 reconciliation itself.

        The provider keys rows `{"sector": ...}`; the gateway normalizes to the
        canonical `{"name": ...}`. Routing the endpoint through the gateway
        without emitting both would have blanked every sector label in the UI —
        which is precisely why ADR-028 recorded this as real work rather than a
        rename, and why DD-1 stayed open through two sprints.
        """
        with patch("services.real_market.fetch_real_sectors", new_callable=AsyncMock,
                   return_value=[{"sector": "IT", "change_pct": 1.2}]):
            rows = client.get("/api/market/sectors").json()

        assert rows, "the fixture must produce a row or this asserts nothing"
        assert rows[0]["name"] == "IT", "canonical key missing"
        assert rows[0]["sector"] == "IT", "legacy alias missing — unmigrated consumers break"
        assert rows[0]["change_pct"] == 1.2

    def test_a_malformed_provider_payload_is_dropped_not_propagated(self, client):
        """Found by the DD-1 migration: a provider answering with a dict where a
        list belongs was iterated into its keys and raised `AttributeError` —
        an unhandled 500 on a dashboard route, from a merely-wrong shape."""
        with patch("services.real_market.fetch_real_sectors", new_callable=AsyncMock,
                   return_value={"unexpected": "shape"}):
            resp = client.get("/api/market/sectors")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_the_advisor_reports_a_tier_not_a_provider(self, authenticated_client):
        """`data_source: "yahoo_finance"` is gone — the field `InvestmentAdvisor.jsx`
        branched on to choose "Live market data" vs "Fallback data", which would
        have labelled a streaming broker feed as fallback.

        Driven down the advisor's own outage path (an empty universe), where
        `available` is false and the tier is legitimately null.
        """
        with patch("services.real_market.fetch_all_universe_quotes",
                   new_callable=AsyncMock, return_value=[]):
            resp = authenticated_client.post(
                "/api/advisor/recommend", json={"horizon": "swing"})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data_source" not in body
        assert body["available"] is False
        assert body["source_tier"] is None
        assert not any(n in resp.text.lower() for n in FORBIDDEN_PROVIDER_NAMES)

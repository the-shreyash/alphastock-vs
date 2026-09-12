"""D6.6 — broker capability verification.

WHAT D6.6 ASKS THAT D3 DID NOT
-------------------------------
`tests/test_broker_framework.py` proves the capability *model* works: a fictional
broker declares a partial set, the registry rejects an adapter that lies, and the
gateway refuses an undeclared capability before the adapter is reached. Every one
of those proofs runs against `AcmeBrokerAdapter` — a broker built in the test
file from the public contract.

That is the right way to prove a mechanism and the wrong way to prove a fact
about *this deployment*. Acme cannot tell you whether Angel One really refuses an
order, whether the five registered adapters' declarations still match their
implementations, or whether any broker has quietly acquired a capability nobody
audited. Every assertion in this module is therefore made against the **live
registry** — `broker_registry`, as `services/brokers/__init__.py` populated it —
and not against a fixture.

The three questions, and the sections that answer them:

  §1  MATRIX      What does each registered broker actually declare, and does
                  every declaration resolve to a real implementation?
  §2  ENFORCEMENT Can a false declaration reach the registry? (executable
                  mutations: a declared capability with no method, a method
                  deleted out from under a declaration, a falsely advertised
                  SESSION_REFRESH)
  §3  NEGATIVE    For every (broker, capability) pair that is NOT declared, does
                  the platform refuse *explicitly*, without a network call,
                  without falling back to another broker, and without treating
                  the absence as success?
  §4  SESSION     Does anything on this platform claim an expired session is
                  live? (LIM-D6.5-3, closed here)

WHAT THIS MODULE DELIBERATELY DOES NOT DO
------------------------------------------
* **No real broker call.** Adapter HTTP is never reached: §3's whole point is
  that the refusal happens above the adapter, and §1 reads declarations. Nothing
  here needs a credential and nothing here has one.
* **No order is placed, modified or cancelled anywhere.** The order-capable
  paths are exercised only in their *refusing* direction.
* **No duplication of D6.4/D6.5 routing tests.** Ownership scoping, the foreign
  `broker_account_id`, the ambiguity refusal and the per-account session cache
  are proved in `test_d64_identity.py` and `test_d65_live_gate.py`. §3.4 asserts
  only the seam those files do not cover: that a *capability* refusal for one
  broker does not become a call to a different broker.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

from services.broker_engine import BrokerEngine
from services.brokers import (
    BrokerAdapterInvalid,
    BrokerCapability,
    BrokerRegistry,
    broker_gateway,
    broker_registry,
)
from services.brokers.accounts import BrokerAccountStatus, broker_accounts
from services.brokers.base import AdapterStreamChannel, BrokerAdapter
from services.brokers.capabilities import CAPABILITY_METHODS
from services.brokers.errors import (
    BrokerAuthError,
    BrokerErrorCode,
    CapabilityUnsupported,
)
from services.brokers.gateway import BrokerGateway
from services.brokers.streaming import StreamEventKind
from tests._accounts import account_doc, account_ref, fixture_account_id


def _run(coro):
    return asyncio.run(coro)


# ==================================================================
# §1 — THE AUTHORITATIVE MATRIX
# ==================================================================
#
# The declared sets are written down here, in full, per broker. That is
# deliberate duplication of the adapters and it is the point: a capability is a
# public contract (it appears in `/api/brokers`, in `account_statuses`, and in
# the frontend's order-account filter), so *adding or removing one must be a
# deliberate edit in two places*. A single-source assertion — "the declared set
# equals the declared set" — cannot fail and would document nothing.

#: Broker -> the exact capability set it declares, as audited on 2026-09-10.
DECLARED = {
    "zerodha": {
        "profile", "holdings", "positions", "funds", "margins", "orders",
        "trades", "place_order", "modify_order", "cancel_order",
        "session_invalidate", "order_stream", "tick_stream",
        "instrument_catalogue",
    },
    "upstox": {
        "profile", "holdings", "positions", "funds", "margins", "orders",
        "trades", "place_order", "modify_order", "cancel_order",
        "session_invalidate", "order_stream", "tick_stream",
        "instrument_catalogue",
    },
    "angelone": {
        "profile", "holdings", "positions", "funds", "margins",
        "session_invalidate", "tick_stream", "instrument_catalogue",
    },
    "fyers": {
        "profile", "holdings", "positions", "funds", "margins",
        "session_invalidate", "tick_stream", "instrument_catalogue",
    },
    "dhan": {
        "profile", "holdings", "positions", "funds",
        "tick_stream", "instrument_catalogue",
    },
}

ALL_CAPABILITIES = frozenset(c.value for c in BrokerCapability)


class TestTheMatrixIsWhatTheRegistryActuallyHolds:
    """§1. The declared surface of this deployment, pinned."""

    def test_the_registered_broker_set_is_exactly_the_audited_one(self):
        """A sixth broker, or a missing fifth, invalidates every row below.

        Named explicitly rather than derived, because "the matrix covers every
        registered broker" is only true if the test knows which brokers those
        are — a derived list makes a newly added adapter silently in-scope and
        un-audited.
        """
        assert set(broker_registry.names()) == set(DECLARED)

    @pytest.mark.parametrize("broker", sorted(DECLARED))
    def test_each_broker_declares_exactly_its_audited_capabilities(self, broker):
        adapter = broker_registry.require(broker)
        assert {c.value for c in adapter.capabilities} == DECLARED[broker]

    @pytest.mark.parametrize("broker", sorted(DECLARED))
    def test_every_declared_capability_resolves_to_a_real_implementation(self, broker):
        """Registration-time verification, re-asserted against the real adapters.

        `BrokerRegistry.validate` runs this check at import. Re-running it here
        is not redundant: import-time enforcement can only fail the *process*,
        and a process that fails to start is diagnosed by a human reading a
        traceback. This turns the same fact into a named test, so the failure
        says which broker and which capability rather than "backend won't boot".
        """
        adapter = broker_registry.require(broker)
        for capability in adapter.capabilities:
            method = CAPABILITY_METHODS[capability]
            implementation = getattr(type(adapter), method, None)
            assert implementation is not None, f"{broker}: {capability.value} -> {method}() missing"
            assert not getattr(implementation, "_capability_stub", False), (
                f"{broker} declares {capability.value} but {method}() is still the "
                "raising default"
            )

    @pytest.mark.parametrize("broker", sorted(DECLARED))
    def test_the_gateway_agrees_with_the_adapter_on_every_capability(self, broker):
        """`supports()` is the question core code asks; it must not have its own
        opinion. Asserted across the whole enum — including the undeclared ones,
        which is the half a "does it support what it supports" check misses."""
        adapter = broker_registry.require(broker)
        for capability in BrokerCapability:
            assert broker_gateway.supports(broker, capability) is (
                capability in adapter.capabilities
            ), f"{broker}/{capability.value}: gateway and adapter disagree"

    def test_no_broker_on_this_platform_can_refresh_a_session(self):
        """The single most consequential row in the matrix (D6.6 §6).

        Zero of five adapters declare SESSION_REFRESH, because Indian retail
        broker APIs issue daily tokens with no refresh grant this platform may
        hold: Kite publishes none; Upstox v2 publishes none; SmartAPI's renewal
        consumes a refresh token the publisher-login redirect never returns;
        Fyers' requires the user's trading PIN, which SECURITY.md forbids
        storing; Dhan's behaviour on a partner token is unverified.

        The consequence is architectural: **every broker session on this
        platform dies on a fixed daily schedule and only a user re-login
        restores it.** `REAUTH_REQUIRED` is therefore the entire recovery
        contract, which is what §4 exists to protect.

        If this test ever goes red because a broker gained a real refresh, that
        is good news — but it must be an audited change, not a silent one, since
        `BrokerEngine.get_session` will begin attempting renewals it has never
        attempted before.
        """
        refreshing = [
            name for name in broker_registry.names()
            if broker_gateway.supports(name, BrokerCapability.SESSION_REFRESH)
        ]
        assert refreshing == [], (
            f"{refreshing} now declare SESSION_REFRESH — audit the renewal path "
            "before accepting this"
        )

    def test_no_capability_is_implemented_but_undeclared_in_a_way_that_could_be_reached(self):
        """The fail-open direction, checked explicitly.

        `declared ⊆ implemented` is what the registry enforces. The reverse —
        an adapter that *implements* a capability it does not declare — is not
        an error and is not enforced: Dhan inherits a working `get_margins`
        from the base default (which delegates to `get_funds`) while
        deliberately not declaring MARGINS, because Dhan's margin surface is an
        order *calculator* rather than an account report.

        What must stay true is that the undeclared implementation is
        unreachable. Proved here at the gateway rather than by reading Dhan's
        source, because the gateway is the only door.
        """
        adapter = broker_registry.require("dhan")
        assert getattr(type(adapter), "get_margins", None) is not None
        assert not broker_gateway.supports("dhan", BrokerCapability.MARGINS)
        with pytest.raises(CapabilityUnsupported):
            _run(broker_gateway.get_margins("dhan", {"access_token": "irrelevant"}))


# ==================================================================
# §2 — REGISTRY ENFORCEMENT (executable mutations)
# ==================================================================
#
# Each test below builds an adapter that lies in one specific way and requires
# the registry to refuse it. These are the D6.6 M1 / M2 / M7 mutations written
# as permanent tests rather than applied to production source and reverted: a
# mutation that lives in the suite cannot be forgotten to be un-applied, and it
# keeps killing the mutant on every future run.


class _MinimalAdapter(BrokerAdapter):
    """The smallest adapter that can legally register: no capabilities at all.

    Subclassed by each mutation below so the *only* difference between a
    registering adapter and a refused one is the lie under test.
    """

    name = "mutant"
    display_name = "Mutant Broker"
    capabilities = frozenset()

    def get_login_url(self, state: str = None) -> dict:
        return {"url": "https://mutant.example/login", "configured": True}

    async def exchange_token(self, auth_payload: dict) -> dict:
        return {"access_token": "t", "account_id": "M1"}

    def session_expiry(self, connected_at: datetime) -> datetime:
        return connected_at + timedelta(hours=8)


class TestAFalseDeclarationCannotReachTheRegistry:
    """§2 / M1, M2, M7."""

    def test_the_control_registers(self):
        """The falsifying twin. Without it, every refusal below could be caused
        by something other than the lie — a bad name, a missing abstract method
        — and the tests would pass while proving nothing about capabilities."""
        registry = BrokerRegistry()
        adapter = registry.register(_MinimalAdapter())
        assert registry.get("mutant") is adapter

    def test_declaring_place_order_without_implementing_it_is_refused(self):
        """M1. The mutation the brief names first, at its most dangerous:
        a broker advertised as order-capable whose order method does not exist.
        Caught at registration, so the process never starts."""

        class LyingAdapter(_MinimalAdapter):
            capabilities = frozenset({BrokerCapability.PLACE_ORDER})

        with pytest.raises(BrokerAdapterInvalid) as exc:
            BrokerRegistry().register(LyingAdapter())
        assert "place_order" in str(exc.value)
        assert "place_order()" in str(exc.value)

    def test_removing_the_method_under_a_live_declaration_is_refused(self):
        """M2, from the other direction: the declaration is honest today and the
        implementation is deleted tomorrow.

        Modelled by an adapter that implements `get_holdings` and a subclass
        that puts the raising default back — which is exactly what deleting the
        override does to the MRO.
        """

        class Honest(_MinimalAdapter):
            capabilities = frozenset({BrokerCapability.HOLDINGS})

            async def get_holdings(self, session: dict) -> list:
                return []

        BrokerRegistry().register(Honest())  # control: this one is fine

        class MethodDeleted(Honest):
            get_holdings = BrokerAdapter.get_holdings

        with pytest.raises(BrokerAdapterInvalid) as exc:
            BrokerRegistry().register(MethodDeleted())
        assert "holdings" in str(exc.value)

    def test_falsely_advertising_session_refresh_is_refused(self):
        """M7, and the reason it matters more than it looks.

        `BrokerEngine.get_session` branches on this capability: with it
        declared, an expired session triggers `refresh_session` and the engine
        believes a renewal was attempted. An adapter that declares the
        capability while inheriting the base stub would make the engine attempt
        a refresh that cannot succeed *instead of* setting REAUTH_REQUIRED — and
        since no broker here can actually refresh, that would break the only
        recovery contract the platform has.
        """

        class PretendsToRefresh(_MinimalAdapter):
            capabilities = frozenset({BrokerCapability.SESSION_REFRESH})

        with pytest.raises(BrokerAdapterInvalid) as exc:
            BrokerRegistry().register(PretendsToRefresh())
        assert "session_refresh" in str(exc.value)
        assert "refresh_session()" in str(exc.value)

    def test_a_refused_adapter_is_not_registered_in_a_downgraded_form(self):
        """No silent downgrade. The brief's requirement stated as an assertion:
        a registry that quietly dropped the unimplementable capability and
        registered the rest would satisfy `pytest.raises` nowhere and would be
        strictly worse than failing — the broker would appear in
        `/api/brokers` with a capability set nobody declared."""

        class HalfLying(_MinimalAdapter):
            capabilities = frozenset(
                {BrokerCapability.HOLDINGS, BrokerCapability.PLACE_ORDER})

            async def get_holdings(self, session: dict) -> list:
                return []

        registry = BrokerRegistry()
        with pytest.raises(BrokerAdapterInvalid):
            registry.register(HalfLying())
        assert registry.get("mutant") is None
        assert len(registry) == 0

    def test_a_stream_capability_no_channel_carries_is_refused(self):
        """The realtime half of the same invariant (D4.7).

        A declared TICK_STREAM that no channel delivers is the *silent* failure
        mode: the socket connects, the reconnect loop is content, and every tick
        is dropped by the per-channel narrowing in `stream.py`. In the logs that
        is indistinguishable from a market with no trades, and the account's
        market-data provider has already been registered on the strength of the
        declaration.

        A **multi-channel** broker is the only shape that can express the lie:
        a single-channel adapter gets `AdapterStreamChannel`, whose `delivers`
        is read off the adapter's own capabilities, so its one channel always
        carries whatever it declared. That is exactly why D4.7 added the check —
        and why the control below (the same adapter with the channel narrowed
        correctly) has to register for the refusal to mean anything.
        """

        class _TwoChannel(_MinimalAdapter):
            capabilities = frozenset(
                {BrokerCapability.TICK_STREAM, BrokerCapability.ORDER_STREAM})
            stream_protocol = "mutant_orders"

            async def stream_endpoint(self, session: dict, credentials: dict = None):
                return "wss://mutant.example/feed"

            def decode_stream_frame(self, frame, context=None):
                return []

            def stream_instruments(self, holdings=None, positions=None) -> list:
                return []

            def normalize_stream_order(self, payload, context=None):
                return {}

        class Honest(_TwoChannel):
            def stream_channels(self):
                return (
                    AdapterStreamChannel(self, "orders",
                                         frozenset({StreamEventKind.ORDER})),
                    AdapterStreamChannel(self, "market",
                                         frozenset({StreamEventKind.TICKS})),
                )

        # Control: both declared capabilities are carried, so it registers.
        BrokerRegistry().register(Honest())

        class TicksCarriedByNothing(_TwoChannel):
            def stream_channels(self):
                return (
                    AdapterStreamChannel(self, "orders",
                                         frozenset({StreamEventKind.ORDER})),
                )

        with pytest.raises(BrokerAdapterInvalid) as exc:
            BrokerRegistry().register(TicksCarriedByNothing())
        assert "tick_stream" in str(exc.value)


# ==================================================================
# §3 — NEGATIVE CAPABILITY BEHAVIOUR
# ==================================================================


def _undeclared_pairs():
    """Every (broker, capability) this deployment does NOT offer.

    Derived rather than listed, because the interesting property is universal —
    "*every* absence refuses" — and a hand-written list would silently stop
    covering a capability the moment one is added to the enum.
    """
    for broker in sorted(DECLARED):
        for capability in sorted(ALL_CAPABILITIES - DECLARED[broker]):
            yield broker, capability


#: Capability -> how the gateway is asked for it, with throwaway arguments.
#: Only the capabilities with a typed gateway method appear; the others are
#: reached through `gateway.call` in the catch-all below.
_GATEWAY_CALLS = {
    "profile": lambda g, b: g.get_profile(b, {"access_token": "x"}),
    "holdings": lambda g, b: g.get_holdings(b, {"access_token": "x"}),
    "positions": lambda g, b: g.get_positions(b, {"access_token": "x"}),
    "funds": lambda g, b: g.get_funds(b, {"access_token": "x"}),
    "margins": lambda g, b: g.get_margins(b, {"access_token": "x"}),
    "orders": lambda g, b: g.get_orders(b, {"access_token": "x"}),
    "trades": lambda g, b: g.get_trades(b, {"access_token": "x"}),
    "place_order": lambda g, b: g.place_order(
        b, {"access_token": "x"},
        {"symbol": "RELIANCE", "transaction_type": "BUY", "quantity": 1,
         "order_type": "MARKET", "exchange": "NSE"}),
    "modify_order": lambda g, b: g.modify_order(
        b, {"access_token": "x"}, "ORD-1", {"quantity": 2}),
    "cancel_order": lambda g, b: g.cancel_order(b, {"access_token": "x"}, "ORD-1"),
}


class TestAnUndeclaredCapabilityRefusesExplicitly:
    """§3.1 — the universal negative, over all 5 brokers × 15 capabilities."""

    @pytest.mark.parametrize(
        "broker,capability",
        [(b, c) for b, c in _undeclared_pairs() if c in _GATEWAY_CALLS],
        ids=lambda v: str(v),
    )
    def test_the_gateway_refuses_before_the_adapter_is_reached(self, broker, capability):
        """Refused, permanently, with the code the frontend branches on.

        `_request` is patched with a mock that *fails the test if called*
        rather than merely being asserted about afterwards: the guarantee is
        "no network round trip", and a mock that returns something would let a
        refusal-after-the-call pass this test.
        """
        adapter = broker_registry.require(broker)
        with patch.object(type(adapter), "_request",
                          new=AsyncMock(side_effect=AssertionError(
                              f"{broker} was CALLED for undeclared {capability}"))):
            with pytest.raises(CapabilityUnsupported) as exc:
                _run(_GATEWAY_CALLS[capability](broker_gateway, broker))

        error = exc.value
        assert error.code == BrokerErrorCode.UNSUPPORTED.value
        assert error.retryable is False, "an unsupported capability is never transient"
        assert error.broker == broker
        assert error.capability == capability
        # The user-facing sentence names the broker the user chose, and carries
        # no developer detail (D3's error contract).
        assert adapter.display_name in error.user_message

    @pytest.mark.parametrize("broker", ["angelone", "fyers", "dhan"])
    def test_the_three_read_only_brokers_refuse_every_order_operation(self, broker):
        """§3.2 — the brief's named cases, asserted together.

        These three adapters are market-data and portfolio integrations; their
        order surfaces are unvalidated against a live account and are therefore
        *not declared*. The capability model exists precisely so that shows up
        as an honest refusal rather than a stub that returns a fabricated
        acknowledgement.
        """
        for capability in ("place_order", "modify_order", "cancel_order"):
            assert not broker_gateway.supports(broker, BrokerCapability(capability))
            with pytest.raises(CapabilityUnsupported):
                _run(_GATEWAY_CALLS[capability](broker_gateway, broker))

    def test_an_unsupported_capability_never_counts_against_broker_health(self):
        """§3.3. A missing capability says nothing about whether the broker is
        up. Counting it would let a UI that probes every capability on page load
        mark a perfectly healthy broker DOWN."""
        adapter = broker_registry.require("dhan")
        adapter.health.reset()
        before = adapter.health.as_dict()
        for _ in range(10):
            with pytest.raises(CapabilityUnsupported):
                _run(broker_gateway.get_orders("dhan", {"access_token": "x"}))
        assert adapter.health.as_dict()["state"] == before["state"]

    def test_a_missing_capability_is_never_reported_as_an_empty_success(self):
        """§3.4 / M2. "No orders" and "this broker has no order book" are
        different answers and must not collapse into `[]`.

        The distinction is not academic: an empty list rendered in the orders
        table tells a user their orders were all filled or cancelled. A
        capability error tells them the truth.
        """
        with pytest.raises(CapabilityUnsupported):
            _run(broker_gateway.get_orders("angelone", {"access_token": "x"}))
        with pytest.raises(CapabilityUnsupported):
            _run(broker_gateway.get_trades("fyers", {"access_token": "x"}))


class TestAnUnsupportedCapabilityNeverReachesAnotherBroker:
    """§3.5 / M3 — the fallback that must not exist."""

    def test_the_refusal_names_only_the_broker_that_was_asked(self):
        """A capability error is about one broker. If the platform ever grew a
        "try a broker that can" path, the error would either disappear or name
        a broker the caller never chose."""
        with pytest.raises(CapabilityUnsupported) as exc:
            _run(_GATEWAY_CALLS["place_order"](broker_gateway, "angelone"))
        assert exc.value.broker == "angelone"
        for other in ("zerodha", "upstox", "fyers", "dhan"):
            assert other not in str(exc.value)
            assert broker_registry.require(other).display_name not in exc.value.user_message

    def test_no_order_capable_adapter_is_touched_when_an_incapable_one_refuses(self):
        """The assertion the message check cannot make.

        Zerodha and Upstox — the two brokers that *can* place an order — have
        their transports armed to fail the test if they are called at all. A
        fallback would show up here even if it produced no error and no name.
        """
        capable = [broker_registry.require(n) for n in ("zerodha", "upstox")]
        with patch.object(type(capable[0]), "_request",
                          new=AsyncMock(side_effect=AssertionError("zerodha was called"))), \
             patch.object(type(capable[1]), "_request",
                          new=AsyncMock(side_effect=AssertionError("upstox was called"))):
            for broker in ("angelone", "fyers", "dhan"):
                with pytest.raises(CapabilityUnsupported):
                    _run(_GATEWAY_CALLS["place_order"](broker_gateway, broker))

    def test_the_gateway_holds_no_ordering_by_which_a_fallback_could_choose(self):
        """Structural, and the reason a broker registry is not a provider registry.

        `services/market_engine/providers/registry.py` ranks its members,
        because market data is a shared good and the platform picks. A broker is
        an account the user owns; `BrokerRegistry` therefore exposes membership
        and filtering and **no ranking, priority or "best" accessor**. There is
        nothing for a fallback to select with.
        """
        forbidden = {"priority", "rank", "best", "preferred", "primary", "fallback",
                     "next_available", "any_capable", "first_capable"}
        surface = {n for n in dir(BrokerRegistry) if not n.startswith("_")}
        assert forbidden & surface == set(), forbidden & surface


class TestBestEffortCapabilitiesDegradeWithoutInventing:
    """§3.6 — the two capabilities whose contract is "empty, not error".

    These are the exception to §3.1 and each has a documented reason. Asserted
    so the exception stays *narrow*: a future capability must not join this list
    by accident, which is what the last test in the class checks.
    """

    def test_a_broker_with_no_tick_feed_subscribes_to_nothing(self):
        """`stream_instruments` answers `[]` so a caller never has to ask two
        questions. Every registered broker declares TICK_STREAM, so this is
        proved against a fresh adapter that does not."""
        registry = BrokerRegistry()
        registry.register(_MinimalAdapter())
        assert BrokerGateway(registry).stream_instruments("mutant") == []

    def test_a_broker_with_no_catalogue_resolves_nothing_rather_than_failing(self):
        registry = BrokerRegistry()
        registry.register(_MinimalAdapter())
        gateway = BrokerGateway(registry)
        assert _run(gateway.resolve_instruments("mutant", ["RELIANCE"])) == {}

    def test_refresh_is_none_for_every_registered_broker_not_an_invented_renewal(self):
        """The gateway answers None for a broker without SESSION_REFRESH — the
        same answer a failed refresh gives — and the engine's response to both
        is REAUTH_REQUIRED. Asserted for all five so that "no broker refreshes"
        is a fact about behaviour, not only about the declaration."""
        for broker in broker_registry.names():
            assert _run(broker_gateway.refresh_session(
                broker, {"access_token": "x", "refresh_token": "y"})) is None

    def test_only_the_two_documented_capabilities_answer_empty_instead_of_refusing(self):
        """The narrowness guard. Any *other* capability that started returning a
        benign value for a broker that does not declare it would be a silent
        downgrade, which §3.4 exists to forbid."""
        registry = BrokerRegistry()
        registry.register(_MinimalAdapter())
        gateway = BrokerGateway(registry)
        for capability, call in _GATEWAY_CALLS.items():
            with pytest.raises(CapabilityUnsupported):
                _run(call(gateway, "mutant"))


class TestTheEngineRefusesAnUnsupportedCapabilityForAResolvedAccount:
    """§3.7 — the same refusal one layer up, addressed by `broker_account_id`.

    D6.4/D6.5 prove *ownership* routing. This proves the seam those files do not
    touch: an account that resolves correctly, is owned correctly, and holds a
    perfectly live session still cannot be made to place an order at a broker
    that does not offer one — and the refusal happens without a broker call.
    """

    def _engine(self, fake_db, uid, broker):
        engine = BrokerEngine()
        engine.configure(fake_db)
        fake_db.broker_accounts.docs.append({
            "_id": ObjectId(),
            **account_doc(uid, broker, suffix="only", external_account_id="EXT-1"),
            "access_token": "LIVE-TOKEN",
            "connected": True,
            "connected_at": "2026-09-10T04:00:00+00:00",
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        return engine, account_ref(uid, broker, suffix="only")

    @pytest.mark.parametrize("broker", ["angelone", "fyers", "dhan"])
    def test_a_live_owned_account_still_cannot_place_an_order(self, fake_db, broker):
        uid = str(ObjectId())
        engine, account = self._engine(fake_db, uid, broker)
        adapter = broker_registry.require(broker)

        # The session is genuinely live — proving the refusal is the capability
        # check and not a stale token.
        assert _run(engine.get_session(account))["access_token"] == "LIVE-TOKEN"

        with patch.object(type(adapter), "_request",
                          new=AsyncMock(side_effect=AssertionError("broker was called"))):
            with pytest.raises(CapabilityUnsupported) as exc:
                _run(engine.place_order(account, {
                    "symbol": "RELIANCE", "transaction_type": "BUY",
                    "quantity": 1, "order_type": "MARKET", "exchange": "NSE"}))
        assert exc.value.broker == broker


# ==================================================================
# §4 — SESSION LIFECYCLE
# ==================================================================


class TestNothingClaimsAnExpiredSessionIsLive:
    """§4 / M6, and the closure of LIM-D6.5-3.

    Two surfaces answer "is this account connected?" and until D6.6 they could
    disagree:

      * `BrokerGateway.connection` derives it from the session's expiry, and has
        always been right.
      * `broker_accounts.status` is a stored value, and startup restore did not
        write it. Real data on this deployment carried two accounts with
        `status: "connected"` whose tokens expired two months earlier, so
        `account_statuses` returned `connected: false` and `status: "connected"`
        in the same record.

    Nothing routed on the stale value — every call path re-derives freshness —
    but the directory is what the UI, the reconnect prompt and an operator read,
    and it is the only signal the platform has that a re-login is owed. Since
    §1 proves no broker can refresh, that signal is the whole recovery contract.
    """

    def test_the_connection_contract_never_reports_an_expired_session_as_connected(self):
        """The surface that was already correct, pinned so it stays that way."""
        expired = {"access_token": "x", "expires_at": "2026-07-10T00:30:00+00:00"}
        for broker in broker_registry.names():
            connection = broker_gateway.connection(
                user_id="u1", broker=broker, session=expired)
            assert connection.connected is False, broker
            assert connection.session_expired is True, broker
            # And it says so in words, because "expired" and "never connected"
            # need different sentences from the UI.
            assert connection.connected_at is None
            assert connection.expires_at is None

    def test_a_session_that_is_fresh_by_one_minute_is_still_connected(self):
        """The falsifying twin for the test above: without it, a
        `connected = False` constant would pass."""
        soon = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        connection = broker_gateway.connection(
            user_id="u1", broker="upstox",
            session={"access_token": "x", "expires_at": soon})
        assert connection.connected is True
        assert connection.session_expired is False

    def test_startup_restore_records_reauth_required_for_an_expired_account(self, fake_db):
        """LIM-D6.5-3. The startup path must write down what it discovered.

        Before D6.6 `load_sessions` logged "reconnect required" and moved on,
        leaving `status: "connected"` on a row whose token was dead. The account
        was unusable either way — this is about the platform being able to
        *say* so.
        """
        broker_accounts.configure(fake_db)
        engine = BrokerEngine()
        engine.configure(fake_db)
        uid = str(ObjectId())
        fake_db.broker_accounts.docs.append({
            "_id": ObjectId(),
            **account_doc(uid, "upstox", suffix="dead", external_account_id="EXT-D"),
            "access_token": "EXPIRED-TOKEN",
            "connected": True,
            "connected_at": "2026-07-09T04:00:00+00:00",
            "expires_at": "2026-07-10T22:00:00+00:00",
        })

        restored = _run(engine.load_sessions())

        assert restored == 0
        doc = _run(fake_db.broker_accounts.find_one(
            {"broker_account_id": fixture_account_id(uid, "upstox", "dead")}))
        assert doc["status"] == BrokerAccountStatus.REAUTH_REQUIRED

    def test_startup_restore_records_reauth_required_when_the_credential_is_unusable(
            self, fake_db):
        """The second branch of the same discovery: a live-status account whose
        stored token cannot be read at all (a rotated encryption key, a row
        written by a failed connect). One login fixes it, so it is
        REAUTH_REQUIRED and not DISCONNECTED — the user did not detach it."""
        broker_accounts.configure(fake_db)
        engine = BrokerEngine()
        engine.configure(fake_db)
        uid = str(ObjectId())
        fake_db.broker_accounts.docs.append({
            "_id": ObjectId(),
            **account_doc(uid, "zerodha", suffix="notoken", external_account_id="EXT-N"),
            "access_token": "",
            "connected": True,
            "expires_at": "2099-01-01T00:00:00+00:00",
        })

        assert _run(engine.load_sessions()) == 0
        doc = _run(fake_db.broker_accounts.find_one(
            {"broker_account_id": fixture_account_id(uid, "zerodha", "notoken")}))
        assert doc["status"] == BrokerAccountStatus.REAUTH_REQUIRED

    def test_a_restorable_account_is_left_connected(self, fake_db):
        """The falsifying twin. Without it, a `_mark_reauth_required` call on
        every account — which would log every user out on every restart —
        passes both tests above."""
        broker_accounts.configure(fake_db)
        engine = BrokerEngine()
        engine.configure(fake_db)
        uid = str(ObjectId())
        fake_db.broker_accounts.docs.append({
            "_id": ObjectId(),
            **account_doc(uid, "upstox", suffix="alive", external_account_id="EXT-A"),
            "access_token": "GOOD-TOKEN",
            "connected": True,
            "connected_at": "2026-09-10T04:00:00+00:00",
            "expires_at": "2099-01-01T00:00:00+00:00",
        })

        with patch.object(BrokerEngine, "start_stream", new=AsyncMock(return_value=None)):
            assert _run(engine.load_sessions()) == 1

        doc = _run(fake_db.broker_accounts.find_one(
            {"broker_account_id": fixture_account_id(uid, "upstox", "alive")}))
        assert doc["status"] == BrokerAccountStatus.CONNECTED

    def test_reading_an_expired_session_sets_reauth_required_and_refuses(self, fake_db):
        """The call-time path, which has always been correct. Asserted beside
        the startup path so the two are visibly the same state transition — a
        divergence between them is what LIM-D6.5-3 was."""
        broker_accounts.configure(fake_db)
        engine = BrokerEngine()
        engine.configure(fake_db)
        uid = str(ObjectId())
        fake_db.broker_accounts.docs.append({
            "_id": ObjectId(),
            **account_doc(uid, "upstox", suffix="stale", external_account_id="EXT-S"),
            "access_token": "EXPIRED-TOKEN",
            "connected": True,
            "expires_at": "2026-07-10T22:00:00+00:00",
        })
        account = account_ref(uid, "upstox", suffix="stale")

        with pytest.raises(BrokerAuthError):
            _run(engine.get_session(account))

        doc = _run(fake_db.broker_accounts.find_one(
            {"broker_account_id": account.broker_account_id}))
        assert doc["status"] == BrokerAccountStatus.REAUTH_REQUIRED

    def test_every_broker_declares_a_finite_session_expiry(self):
        """No adapter may model a session that never dies.

        A `session_expiry` far in the future would make `session_is_fresh`
        permanently true, and an account would sit CONNECTED forever while the
        broker rejected every call — the exact state §4 exists to prevent, with
        no expired-session signal anywhere to catch it.
        """
        now = datetime.now(timezone.utc)
        for broker in broker_registry.names():
            expiry = broker_gateway.session_expiry(broker, now)
            assert expiry > now, broker
            assert expiry - now <= timedelta(hours=48), (
                f"{broker} models a session lasting {expiry - now} — Indian "
                "retail broker tokens are daily")


class TestSessionInvalidationIsDeclaredWhereItIsRealAndNowhereElse:
    """§4.2 — the disconnect half of the lifecycle."""

    def test_the_four_brokers_with_a_logout_endpoint_declare_it(self):
        for broker in ("zerodha", "upstox", "angelone", "fyers"):
            assert broker_gateway.supports(broker, BrokerCapability.SESSION_INVALIDATE)

    def test_dhan_does_not_claim_a_revocation_it_cannot_perform(self):
        """Dhan publishes no logout for the partner flow; a 24-hour token simply
        expires. The gateway answers False rather than raising, because the
        token is discarded locally either way and a disconnect must not fail."""
        assert not broker_gateway.supports("dhan", BrokerCapability.SESSION_INVALIDATE)
        assert _run(broker_gateway.invalidate_session("dhan", {"access_token": "x"})) is False

    def test_invalidation_is_best_effort_and_never_fails_a_disconnect(self):
        """A broker rejecting the logout of an already-dead token must not stop
        the user detaching their account."""
        adapter = broker_registry.require("upstox")
        with patch.object(type(adapter), "invalidate_session",
                          new=AsyncMock(side_effect=RuntimeError("token already dead"))):
            assert _run(broker_gateway.invalidate_session(
                "upstox", {"access_token": "x"})) is False


# ==================================================================
# §5 — MARKET DATA SEPARATION
# ==================================================================


class TestBrokerCapabilitiesDoNotBecomeTheMarketDataArchitecture:
    """§5 / Phase 9. A broker tick feed is *evidence of a broker session*, not a
    public market-data entitlement.

    D6.6 audits broker capabilities, and TICK_STREAM is the one capability whose
    verification could quietly promote a personal broker API into the platform's
    market-data source of record. The separation is structural and is asserted
    here so a capability audit cannot erode it.
    """

    def test_a_broker_feed_is_registered_only_on_the_declared_capability(self):
        """`services/brokers/market_feed.py` gates provider registration on
        TICK_STREAM and on nothing else — not on `hasattr`, not on the broker's
        name. A priority-1 streaming provider that could only deliver silence
        would outrank the working baseline."""
        source = (
            __import__("pathlib").Path(__file__).resolve().parent.parent
            / "services" / "brokers" / "market_feed.py"
        ).read_text()
        assert "BrokerCapability.TICK_STREAM" in source
        for name in ("zerodha", "upstox", "angelone", "fyers", "dhan"):
            assert f'== "{name}"' not in source
            assert f"== '{name}'" not in source

    def test_the_private_and_public_tick_events_are_different_events(self):
        """The invariant D6.3/D6.5 established, re-asserted from the capability
        side.

        One batch of broker ticks leaves `BrokerEngine._on_ticks` twice, and the
        two exits are not interchangeable:

          * `publish_market_ticks(account, ...)` — into the Market Gateway, where
            it becomes *market* data: the provider identity is erased, the tier
            is stamped, and `market.tick` is broadcast to channel subscribers
            with no `user_id` and no `broker_account_id` on it.
          * `_push(user_id, {"type": "broker_price_tick", ...})` — a private
            event, carrying both the broker and the `broker_account_id`,
            delivered only to the owning user's own sockets.

        Two names and two shapes, so a private tick cannot become a broadcast by
        someone forgetting a filter. Asserted structurally because the property
        is about *which function is called with what*, and a runtime test that
        stubbed both would be asserting about its own stubs.
        """
        engine_src = (
            __import__("pathlib").Path(__file__).resolve().parent.parent
            / "services" / "broker_engine.py"
        ).read_text()

        # The private event is addressed to one user and names the account.
        assert 'await self._push(user_id, {"type": "broker_price_tick"' in engine_src
        assert '"broker_account_id": account.broker_account_id' in engine_src

        # The broker transport publishes no public market channel of its own:
        # the only way broker ticks become public is through the Market Gateway.
        transport = (
            __import__("pathlib").Path(__file__).resolve().parent.parent
            / "services" / "brokers" / "stream.py"
        ).read_text()
        assert "market.tick" not in transport
        assert "broker_price_tick" not in transport


# ==================================================================
# §6 — ACCOUNT ADDRESSING (found by a SURVIVING mutation)
# ==================================================================


class TestAnAccountAddressedRouteNeverAcceptsABrokerName:
    """§6 / M4. The mutation that survived the D6.6 campaign, and the gap it
    exposed.

    THE MUTATION
    ------------
    `server.py`, `_account`::

        if not is_broker_account_id(broker_account_id):
            raise HTTPException(status_code=404, ...)
        ->
        if not is_broker_account_id(broker_account_id):
            return await _sole_account(user, broker_account_id)

    188 tests passed with that applied — the whole of D6.4, D6.5 and §1–§5 of
    this module.

    WHAT IT ACTUALLY DOES
    ---------------------
    It makes ``GET /api/brokers/accounts/upstox/holdings`` *work*. The path
    segment is not an account id, so the mutated branch hands it to the
    broker-name bridge, which resolves the caller's sole Upstox account and
    serves it. Broker-name addressing — the exact semantics D6.4 exists to
    remove — is silently restored on every account-addressed route, and on the
    order routes among them.

    It is **not** a cross-tenant bypass: `_sole_account` is owner-scoped, so A
    still cannot reach B's account. That is precisely what made it survivable.
    The boundary it breaks is *addressing*: a client that names an account is
    entitled to be refused when the name is not an account, rather than served
    whichever account the platform would have picked. With two accounts at one
    broker the bridge 409s — so the damage is worst for the single-account user,
    who is served silently and never learns their request was malformed.

    WHY NOTHING CAUGHT IT
    ---------------------
    `test_d64_identity.py::TestTheIdIsMintedNotDerived
    ::test_a_value_that_is_not_an_account_id_is_rejected_by_shape` asserts the
    right property of the wrong thing: it calls `is_broker_account_id` directly.
    The predicate stays correct under this mutation — what changes is what the
    route *does* with a false answer, and no test drove a non-id through a route.

    The tests below drive the route.
    """

    #: Values a confused, lazy or hostile client might put where an account id
    #: belongs. Every one must 404 — including the two that would resolve to a
    #: real, owned account under the mutation.
    NOT_ACCOUNT_IDS = (
        "upstox",          # a broker name the caller genuinely owns an account at
        "zerodha",         # the other one
        "angelone",        # a broker the caller has no account at
        "me",
        "default",
        "ba_",             # the prefix alone
        "ba_not-hex-at-all",
    )

    @pytest.mark.parametrize("path", ["holdings", "positions", "funds", "orders",
                                      "profile", "trades", "margins"])
    @pytest.mark.parametrize("candidate", NOT_ACCOUNT_IDS)
    def test_a_read_route_refuses_anything_that_is_not_an_account_id(
            self, client, fake_db, test_user, candidate, path):
        """404, and no broker call.

        `get_holdings` is patched to a value that would be unmistakable in the
        response body, so a route that resolved *something* cannot pass by
        returning an empty list.
        """
        _seed_owned_accounts(fake_db, test_user)
        with patch("services.brokers.gateway.broker_gateway.get_holdings",
                   new=AsyncMock(return_value=[{"symbol": "SHOULD-NOT-APPEAR"}])):
            resp = client.get(f"/api/brokers/accounts/{candidate}/{path}",
                              headers=_auth(test_user))
        assert resp.status_code == 404, (
            f"{candidate!r} was accepted as an account id on /{path}: {resp.text}")
        assert "SHOULD-NOT-APPEAR" not in resp.text

    @pytest.mark.parametrize("candidate", NOT_ACCOUNT_IDS)
    def test_the_order_route_refuses_anything_that_is_not_an_account_id(
            self, client, fake_db, test_user, candidate):
        """The same rule where it matters most.

        `place_order` is patched to fail the test if it is reached at all: an
        order that was never addressed to an account must not be placed in one
        the platform chose. No real broker is involved — the patch replaces the
        gateway method itself.
        """
        _seed_owned_accounts(fake_db, test_user)
        with patch("services.brokers.gateway.broker_gateway.place_order",
                   new=AsyncMock(side_effect=AssertionError(
                       "an order was placed for a request that named no account"))):
            resp = client.post(
                f"/api/brokers/accounts/{candidate}/orders",
                json={"symbol": "RELIANCE", "transaction_type": "BUY",
                      "quantity": 1, "order_type": "MARKET", "exchange": "NSE"},
                headers=_auth(test_user))
        assert resp.status_code == 404, resp.text

    def test_a_real_account_id_still_resolves(self, client, fake_db, test_user):
        """The falsifying twin. Without it, a route that 404s unconditionally —
        or an `is_broker_account_id` that returns False for everything — passes
        every test above."""
        accounts = _seed_owned_accounts(fake_db, test_user)
        account_id = accounts["upstox"]["broker_account_id"]
        with patch("services.brokers.gateway.broker_gateway.get_holdings",
                   new=AsyncMock(return_value=[{"symbol": "TCS"}])):
            resp = client.get(f"/api/brokers/accounts/{account_id}/holdings",
                              headers=_auth(test_user))
        assert resp.status_code == 200, resp.text
        assert resp.json()["broker_account_id"] == account_id

    def test_the_broker_addressed_bridge_still_exists_and_is_still_separate(
            self, client, fake_db, test_user):
        """The bridge is not being removed — it is being kept where it belongs.

        `/api/brokers/{broker}/holdings` is the broker-addressed route and
        answering a broker name there is its job. The defect the mutation
        creates is that the *account-addressed* route starts doing the same
        thing. Asserted so a future reader does not close §6 by deleting the
        bridge.
        """
        _seed_owned_accounts(fake_db, test_user)
        with patch("services.brokers.gateway.broker_gateway.get_holdings",
                   new=AsyncMock(return_value=[{"symbol": "TCS"}])):
            resp = client.get("/api/brokers/upstox/holdings",
                              headers=_auth(test_user))
        assert resp.status_code == 200, resp.text
        assert resp.json()["broker"] == "upstox"


def _seed_owned_accounts(fake_db, user) -> dict:
    """One Zerodha and one Upstox account, both owned by `user`, both live.

    Single accounts per broker on purpose: the broker-name bridge only *picks*
    when there is exactly one, so this is the seeding under which the M4
    mutation succeeds silently rather than 409ing. A two-account seed would let
    §6 pass for the wrong reason.
    """
    uid = str(user["_id"])
    out = {}
    for broker, ext in (("zerodha", "OWN-KITE"), ("upstox", "OWN-UPX")):
        doc = {
            "_id": ObjectId(),
            **account_doc(uid, broker, external_account_id=ext),
            "access_token": f"TOKEN-{ext}",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "connected_at": "2026-09-10T04:00:00+00:00",
        }
        fake_db.broker_accounts.docs.append(doc)
        out[broker] = doc
    return out


def _auth(user):
    from tests.conftest import _headers_for

    return _headers_for(user)


class TestConfigurationIsNotConnection:
    """§4.3 / M13 — the second surviving mutation, and why it survived.

    THE MUTATION
    ------------
    `services/brokers/gateway.py`, `BrokerGateway.connection`::

        fresh = bool(has_token and adapter.session_is_fresh(session))
        ->
        fresh = bool(adapter.is_configured()
                     or (has_token and adapter.session_is_fresh(session)))

    221 tests passed with that applied, including §4's
    `test_the_connection_contract_never_reports_an_expired_session_as_connected`.

    WHY IT SURVIVED
    ---------------
    Not because the suite lacked an assertion, but because the assertion could
    not fail: **no test in the repository builds a connection for a broker that
    is actually configured.** `is_configured()` reads the environment, the
    hermetic suite sets no broker credentials, and so it is False everywhere —
    which makes the injected `or` term dead in the tests and live in production,
    where `UPSTOX_API_KEY` and `KITE_API_KEY` are set.

    `test_broker_framework.py::test_connection_contract_separates_configured_
    connected_and_expired` looks like the test that should have caught it, and
    it is the clearest example of the problem: it asserts
    ``"ready" if expired.configured else "disconnected"`` — it *adapts* to
    whichever value it finds, so the configured dimension is unfalsifiable by
    construction.

    WHAT THE MUTANT DOES IN PRODUCTION
    -----------------------------------
    Every user of a deployment that holds Upstox API keys is reported connected
    to Upstox — with no session, no token and no account — and the UI, the
    reconnect prompt and `account_statuses` all agree. It is the exact inverse
    of §4: instead of an expired session claiming to be live, a *deployment
    setting* claims to be a live session.

    The tests below configure the broker for real (via the adapter's own
    declared environment variables, so they cannot drift from the spec) and then
    require the three facts to stay orthogonal.
    """

    @staticmethod
    def _configure(monkeypatch, broker: str) -> None:
        """Make `is_configured()` genuinely True, using the adapter's own spec.

        Reading the variable names off `credential_spec` rather than writing
        `UPSTOX_API_KEY` here is what keeps this test honest for a broker whose
        configuration shape is unusual — Angel One has no secret, Dhan's keys
        are partner credentials — and for one added later.
        """
        adapter = broker_registry.require(broker)
        spec = adapter.credential_spec
        for env in (spec.api_key_env, spec.api_secret_env, spec.redirect_url_env):
            if env:
                monkeypatch.setenv(env, f"test-value-for-{env.lower()}")
        for env in (spec.extra_env or {}).values():
            if env:
                monkeypatch.setenv(env, f"test-value-for-{env.lower()}")
        assert adapter.is_configured(), f"{broker} did not become configured"

    @pytest.mark.parametrize("broker", sorted(DECLARED))
    def test_a_configured_broker_with_no_session_is_not_connected(
            self, monkeypatch, broker):
        """Holding a deployment's API keys says nothing about any user."""
        self._configure(monkeypatch, broker)
        connection = broker_gateway.connection(
            user_id="u1", broker=broker, session=None)
        assert connection.configured is True
        assert connection.connected is False
        assert connection.session_expired is False
        # `ready` — keys present, login owed. Distinct from both `live` and
        # `disconnected`, which is the whole reason the third state exists.
        assert connection.mode == "ready"

    @pytest.mark.parametrize("broker", sorted(DECLARED))
    def test_a_configured_broker_with_an_expired_session_is_not_connected(
            self, monkeypatch, broker):
        """The mutant's exact target. Configured + expired must stay disconnected."""
        self._configure(monkeypatch, broker)
        connection = broker_gateway.connection(
            user_id="u1", broker=broker,
            session={"access_token": "x", "expires_at": "2026-07-10T00:30:00+00:00"})
        assert connection.configured is True
        assert connection.connected is False
        assert connection.session_expired is True
        assert connection.mode == "ready"
        # No session-derived detail leaks out of a connection that is not live.
        assert connection.connected_at is None
        assert connection.expires_at is None

    @pytest.mark.parametrize("broker", sorted(DECLARED))
    def test_a_configured_broker_with_a_live_session_is_connected(
            self, monkeypatch, broker):
        """The falsifying twin: without it, `connected = False` passes both
        tests above and the contract would report nobody as connected."""
        self._configure(monkeypatch, broker)
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        connection = broker_gateway.connection(
            user_id="u1", broker=broker,
            session={"access_token": "x", "expires_at": future,
                     "account_id": "EXT-1"})
        assert connection.connected is True
        assert connection.mode == "live"
        assert connection.account_id == "EXT-1"

    @pytest.mark.parametrize("broker", sorted(DECLARED))
    def test_an_unconfigured_broker_with_a_live_session_is_still_connected(
            self, monkeypatch, broker):
        """The orthogonality proved in the other direction.

        A session is a fact about the user; configuration is a fact about the
        deployment. A live session must not be downgraded because the keys were
        rotated out from under it — the token in hand still works until the
        broker says otherwise.
        """
        adapter = broker_registry.require(broker)
        spec = adapter.credential_spec
        for env in (spec.api_key_env, spec.api_secret_env, spec.redirect_url_env):
            if env:
                monkeypatch.delenv(env, raising=False)
        for env in (spec.extra_env or {}).values():
            if env:
                monkeypatch.delenv(env, raising=False)
        assert not adapter.is_configured()

        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        connection = broker_gateway.connection(
            user_id="u1", broker=broker,
            session={"access_token": "x", "expires_at": future})
        assert connection.connected is True
        assert connection.configured is False

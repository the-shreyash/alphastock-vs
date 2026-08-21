"""Sprint D3 — Broker Provider Framework tests (hermetic, no real broker calls).

The framework's promise is one sentence: **adding a broker is one adapter plus
one registry entry, and nothing else in the platform changes.** A promise like
that is only worth anything if something fails when it stops being true, so the
centrepiece of this module is `AcmeBrokerAdapter` — a fictional broker built
here, in the test file, from nothing but the public contract. If a future change
makes a new broker require editing the Trading Engine, a route, or the Market
Engine, the tests below go red rather than the property quietly evaporating.

Covered:
  • the capability model, and that it is verified rather than declarative
  • the registry: membership, unknown brokers, adapters that lie
  • the gateway: capability enforcement, canonical shapes, error normalization
  • broker health, and the auth-failure exception that keeps it honest
  • the authentication / configuration boundary
  • the user -> broker association contract
  • Source Manager broker-connection tracking (the D3/D4 seam)
  • structural proofs that no core module knows a broker's name

No test reaches a real broker API: adapter HTTP is mocked at
`BrokerAdapter._request`, the single chokepoint every adapter call goes through.
"""

import asyncio
import inspect
import pathlib
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.brokers import (
    BrokerAdapterInvalid,
    BrokerCapability,
    BrokerRegistry,
    broker_gateway,
    broker_registry,
)
from services.brokers.base import BrokerAdapter
from services.brokers.capabilities import CAPABILITY_METHODS
from services.brokers.contracts import (
    BrokerFunds,
    BrokerHolding,
    BrokerOrderAck,
    coerce_funds,
    coerce_holdings,
    coerce_orders,
)
from services.brokers.credentials import BrokerCredentialSpec, resolve_credentials
from services.brokers.errors import (
    BrokerAuthError,
    BrokerContractError,
    BrokerError,
    BrokerErrorCode,
    CapabilityUnsupported,
    UnknownBrokerError,
    normalize_broker_error,
)
from services.brokers.gateway import BrokerGateway
from services.brokers.health import DOWN_AFTER_FAILURES, BrokerConnectionState
from services.brokers.zerodha import ZerodhaAdapter
from services.market_engine.event_bus import event_bus
from services.market_engine.source_manager import SourceManager

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def run(coro):
    """Drive one coroutine on a fresh event loop.

    `asyncio.run` rather than `get_event_loop().run_until_complete`: the latter
    passes in isolation and fails in a full-suite run, because by then some
    earlier test has left the thread with no current loop. A helper whose
    correctness depends on which other tests ran first is not a helper.
    """
    return asyncio.run(coro)


# ==================================================================
# The fictional broker — the extensibility proof
# ==================================================================


class AcmeBrokerAdapter(BrokerAdapter):
    """A broker that does not exist, built only from the public contract.

    Deliberately *partial*: it serves holdings and order placement, and offers
    no positions, no funds, no trades, no order modification and no stream. That
    combination is the realistic shape of a small or newly-launched broker, and
    it is exactly what the pre-D3 contract could not express — every method was
    abstract, so this adapter could only have been written with five stub
    methods that lie about what the broker can do.
    """

    name = "acme"
    display_name = "Acme Securities"
    capabilities = frozenset(
        {
            BrokerCapability.HOLDINGS,
            BrokerCapability.PLACE_ORDER,
        }
    )
    credential_spec = BrokerCredentialSpec(
        api_key_env="ACME_API_KEY",
        api_secret_env="ACME_API_SECRET",
    )
    #: Acme calls its delivery product "DELIVERY" — neither Zerodha's "CNC" nor
    #: Upstox's "D". The pre-D3 route default (`"CNC" if zerodha else "D"`)
    #: would have silently placed every Acme order with Upstox's product code.
    default_product = "DELIVERY"

    def get_login_url(self, user_id: str = None) -> dict:
        if not self.is_configured():
            return {"url": None, "configured": False, "message": "Acme not configured"}
        return {"url": f"https://acme.example/login?uid={user_id}", "configured": True}

    async def exchange_token(self, auth_payload: dict) -> dict:
        return {
            "access_token": "acme-token",
            "expires_at": self.session_expiry(datetime.now(timezone.utc)).isoformat(),
            "account_id": "ACME1",
            "profile": {"user_id": "ACME1", "broker": "ACME"},
        }

    def session_expiry(self, connected_at: datetime) -> datetime:
        return connected_at + timedelta(hours=12)

    async def get_holdings(self, session: dict) -> list:
        return [
            {
                "symbol": "ACMECO",
                "exchange": "NSE",
                "quantity": 10,
                "average_price": 100.0,
                "last_price": 110.0,
                "market_value": 1100.0,
                "invested_value": 1000.0,
                "pnl": 100.0,
                "pnl_percent": 10.0,
                "product": "DELIVERY",
                # A broker-specific extra the canonical contract does not name.
                "acme_internal_ref": "XYZ-1",
            }
        ]

    async def place_order(self, session: dict, order: dict) -> dict:
        self.last_order = dict(order)
        return {"order_id": "ACME-ORD-1", "status": "PENDING"}


@pytest.fixture
def acme_registry():
    """A registry containing only Acme — isolated from the global singleton."""
    registry = BrokerRegistry()
    adapter = registry.register(AcmeBrokerAdapter())
    return registry, adapter


@pytest.fixture
def acme_gateway(acme_registry):
    registry, adapter = acme_registry
    return BrokerGateway(registry), adapter


# ------------------------------------------------------------------
# THE HEADLINE PROOF
# ------------------------------------------------------------------


def test_a_new_broker_needs_only_an_adapter_and_a_registry_entry(acme_gateway):
    """The framework's whole promise, asserted end to end.

    Acme was written above using nothing but `BrokerAdapter`, `BrokerCapability`
    and `BrokerCredentialSpec`. No core module was edited to make this pass: not
    the Trading Engine, not the Broker Engine, not a route, not the Market
    Engine. If that stops being true, this test is where it shows.
    """
    gateway, adapter = acme_gateway
    session = {"access_token": "t", "expires_at": _future()}

    # It is discoverable, with its own capabilities and its own product code.
    listing = gateway.list_brokers()
    assert [b["name"] for b in listing] == ["acme"]
    assert listing[0]["capabilities"] == ["holdings", "place_order"]
    assert gateway.default_product("acme") == "DELIVERY"

    # It serves what it declared, in canonical shape.
    holdings = run(gateway.get_holdings("acme", session))
    assert holdings[0]["symbol"] == "ACMECO"

    # It places an order with ITS product code, chosen by nothing but the adapter.
    ack = run(gateway.place_order("acme", session, {"symbol": "ACMECO", "quantity": 1, "transaction_type": "BUY"}))
    assert ack == {"order_id": "ACME-ORD-1", "status": "PENDING", "broker": "acme"}
    assert adapter.last_order["product"] == "DELIVERY"

    # And what it does NOT offer is refused honestly, not attempted.
    with pytest.raises(CapabilityUnsupported):
        run(gateway.get_funds("acme", session))


def test_core_modules_do_not_know_any_broker_by_name():
    """No core service branches on, or configures itself from, a broker name.

    The structural half of the proof above: Acme works because the modules below
    ask the framework rather than naming a broker. Scanned as source text with
    comments and docstrings stripped, so the explanatory comments D3 left behind
    (which quote the branches they replaced) do not count as violations — only
    executable code does.
    """
    protected = [
        "services/trading_engine.py",
        "services/portfolio_engine.py",
        "services/portfolio_stream.py",
        "services/trade_stream.py",
        "services/ai_context_builder.py",
        "services/paper_trade.py",
        "services/broker_engine.py",
        "services/brokers/registry.py",
        "services/brokers/gateway.py",
        "services/brokers/contracts.py",
        "services/brokers/capabilities.py",
        # Joined the ban in D4.2. D3 exempted it because it held the per-protocol
        # transports themselves; it no longer holds any.
        "services/brokers/stream.py",
        "services/brokers/streaming.py",
    ]
    pattern = re.compile(r"zerodha|upstox|kite", re.IGNORECASE)
    offenders = {}
    for relative in protected:
        code = _strip_comments_and_strings((BACKEND / relative).read_text())
        hits = sorted(set(pattern.findall(code)))
        if hits:
            offenders[relative] = hits
    assert not offenders, f"core modules naming a broker in executable code: {offenders}"


def test_routes_never_compare_a_broker_name():
    """`server.py` may *route* `/api/zerodha/*` — it may not *branch* on a broker.

    The legacy `/api/zerodha/*` endpoints are a deprecated public URL surface
    that delegates to the Broker Engine, so the name appears there legitimately.
    What must never come back are the two comparisons D3 removed: the order
    product default (`"CNC" if broker == "zerodha" else "D"`, whose `else`
    silently handed Upstox's product code to every broker added after it) and
    the OAuth callback's parsing branch (whose `else` assumed every future
    broker speaks Upstox's dialect).

    Written as a ban on the comparison rather than on the name, so the ban is
    precise enough to be kept.
    """
    code = _strip_comments_and_strings((BACKEND / "server.py").read_text())
    for comparison in ("broker ==", "broker!=", "broker !="):
        assert comparison not in code, f"server.py branches on a broker name: {comparison!r}"


def test_the_stream_transport_holds_no_broker_wire_format():
    """`stream.py`'s D3 exemption from the name ban above is withdrawn (D4.2).

    D3 exempted this module for a stated reason: it held the per-protocol
    transports themselves — the code that spoke Kite's binary framing and
    Upstox's JSON feed — so protocol names and endpoint URLs legitimately lived
    there. Dispatch was a protocol lookup rather than a broker-name chain, which
    removed the branch but not the knowledge. It is now in the protected list
    above with every other core module.

    This test guards the same property one level below the names, because a wire
    format can return without one: a broker's endpoint is a `wss://` literal, a
    binary framing is `struct`, and a JSON envelope is `json.loads`. None of the
    three has any business in a module whose entire job is to open a socket and
    hand each frame to the adapter that owns it — and each is exactly what was
    here before D4.2.
    """
    source = (BACKEND / "services/brokers/stream.py").read_text()
    code = _strip_comments_and_strings(source)
    for machinery in ("import struct", "import json", "json.loads", "struct.unpack"):
        assert machinery not in code, f"stream.py is decoding a broker wire format again: {machinery!r}"
    for literal in ("ws://", "wss://"):
        assert literal not in source, f"stream.py names a broker endpoint again: {literal!r}"


def test_every_streaming_broker_resolves_a_transport():
    """A declared stream must have something able to run it.

    The D3 invariant, preserved through the D4.2 change of mechanism: it used to
    mean "this protocol has an entry in PROTOCOL_RUNNERS", and now means "this
    protocol resolves to a transport" — the generic WebSocket one unless the
    broker's protocol needs its own. The failure it guards is unchanged: a
    broker that declares a realtime capability and then connects to nothing.
    """
    from services.brokers.stream import resolve_transport

    for adapter in broker_registry.all():
        streams = broker_gateway.stream_capabilities(adapter.name)
        if streams["orders"] or streams["ticks"]:
            assert resolve_transport(adapter) is not None, f"{adapter.name} declares a stream with no transport"
        else:
            assert resolve_transport(adapter) is None, f"{adapter.name} has a transport but declares no stream"


def test_broker_engine_reads_no_broker_credentials_from_the_environment():
    """`BrokerEngine` used to read `KITE_API_KEY` by name to open a stream.

    A broker's secret name in the engine is the same coupling as a broker's name
    in the engine: it makes the engine unable to open a stream for a broker it
    was not written to know about. Credential material now arrives from the
    adapter through `BrokerGateway.stream_credentials`.
    """
    source = _strip_comments_and_strings((BACKEND / "services/broker_engine.py").read_text())
    assert "os.environ" not in source
    assert "getenv" not in source


def test_adapters_declare_credentials_instead_of_reading_the_environment():
    """The configuration boundary, asserted on every registered adapter."""
    for adapter in broker_registry.all():
        source = _strip_comments_and_strings(inspect.getsource(type(adapter)))
        assert "os.environ" not in source, f"{adapter.name} reads the environment directly"
        assert adapter.credential_spec.api_key_env, f"{adapter.name} declares no credential spec"


# ==================================================================
# Capability model
# ==================================================================


def test_every_capability_maps_to_a_method_name():
    """A capability with no method behind it could never be verified."""
    for capability in BrokerCapability:
        assert capability in CAPABILITY_METHODS
        assert CAPABILITY_METHODS[capability], f"{capability} names no adapter method"


def test_zerodha_declares_the_capabilities_it_implements():
    adapter = broker_registry.require("zerodha")
    assert isinstance(adapter, ZerodhaAdapter)
    assert adapter.supports(BrokerCapability.HOLDINGS)
    assert adapter.supports(BrokerCapability.PLACE_ORDER)
    assert adapter.supports(BrokerCapability.TICK_STREAM)


def test_zerodha_does_not_declare_session_refresh():
    """The absence is the point.

    Kite Connect issues daily tokens with no refresh grant for retail apps.
    Before D3 that fact was buried in a `return None` override; declaring it as
    a missing capability is what lets the engine prompt a reconnect without
    attempting a refresh that cannot succeed.
    """
    adapter = broker_registry.require("zerodha")
    assert not adapter.supports(BrokerCapability.SESSION_REFRESH)
    assert run(broker_gateway.refresh_session("zerodha", {"access_token": "t"})) is None


def test_upstox_does_not_declare_tick_stream():
    """Two brokers, genuinely different capabilities — the model is not cosmetic.

    Upstox's portfolio stream carries order updates only; its market feed is a
    separate protobuf endpoint this adapter does not speak. Zerodha's ticker
    carries both.
    """
    upstox = broker_registry.require("upstox")
    zerodha = broker_registry.require("zerodha")
    assert upstox.supports(BrokerCapability.ORDER_STREAM)
    assert not upstox.supports(BrokerCapability.TICK_STREAM)
    assert zerodha.supports(BrokerCapability.TICK_STREAM)
    assert broker_gateway.stream_capabilities("upstox") == {"orders": True, "ticks": False}


def test_stream_instruments_is_empty_for_a_broker_without_a_tick_feed():
    """Asked in capability terms, answered without a broker-name branch.

    `BrokerEngine.start_stream` used to decide what to subscribe to with
    `if broker == "zerodha":`. It now asks the gateway, and a broker with no
    tick feed answers with nothing rather than with an error the caller must
    handle.
    """
    holdings = [{"instrument_token": 42}]
    assert broker_gateway.stream_instruments("upstox", holdings=holdings) == []
    assert broker_gateway.stream_instruments("zerodha", holdings=holdings) == [42]


def test_the_gateway_refuses_before_the_adapter_even_when_the_method_exists():
    """The gateway enforces capabilities; it does not merely relay the adapter's refusal.

    This test exists because the obvious version of it cannot fail. Asking an
    adapter for a capability it neither declares nor implements raises from the
    base stub whether or not the gateway checks anything first — so a test built
    that way passes with capability enforcement deleted, which was verified by
    deleting it.

    `SecretiveAdapter` separates the two: it *implements* `get_trades` and does
    not *declare* TRADES. If the gateway relayed instead of enforcing, the call
    would succeed and return data the broker was never declared able to serve.
    """

    class SecretiveAdapter(AcmeBrokerAdapter):
        name = "secretive"
        capabilities = frozenset({BrokerCapability.HOLDINGS})

        async def get_trades(self, session: dict) -> list:
            raise AssertionError("the gateway called an undeclared capability")

    registry = BrokerRegistry()
    registry.register(SecretiveAdapter())
    gateway = BrokerGateway(registry)

    with pytest.raises(CapabilityUnsupported):
        run(gateway.get_trades("secretive", {"access_token": "t"}))


def test_unsupported_capability_is_refused_without_calling_the_broker(acme_gateway):
    """A missing capability must cost nothing and must never look like an outage."""
    gateway, adapter = acme_gateway
    with patch.object(BrokerAdapter, "_request", new=AsyncMock()) as request:
        with pytest.raises(CapabilityUnsupported) as excinfo:
            run(gateway.get_trades("acme", {"access_token": "t"}))
    request.assert_not_called()
    error = excinfo.value
    assert error.code == BrokerErrorCode.UNSUPPORTED.value
    assert error.retryable is False
    assert "Acme Securities" in error.user_message
    # A permanent property of the broker is not evidence that it is unhealthy.
    assert adapter.health.state is BrokerConnectionState.UNKNOWN


# ==================================================================
# Registry
# ==================================================================


def test_registry_rejects_an_adapter_that_declares_what_it_does_not_implement():
    """The check that makes the capability model trustworthy.

    Without it, a capability set is a comment: an adapter could claim TRADES,
    inherit the base stub, and the mistake would surface as a user-facing error
    at runtime instead of a crash at startup.
    """

    class LyingAdapter(AcmeBrokerAdapter):
        name = "liar"
        capabilities = frozenset({BrokerCapability.TRADES})

    registry = BrokerRegistry()
    with pytest.raises(BrokerAdapterInvalid) as excinfo:
        registry.register(LyingAdapter())
    assert "trades -> get_trades()" in str(excinfo.value)
    assert "liar" not in registry


def test_registry_accepts_a_capability_served_by_a_working_inherited_default():
    """`get_margins` delegates to `get_funds` in the base class.

    Inheriting a default that *works* is reuse; inheriting one that only raises
    is a broken declaration. The registry distinguishes them by the
    `@capability_stub` mark rather than by identity with the base class, which
    could not tell the two apart.
    """

    class MarginsAdapter(AcmeBrokerAdapter):
        name = "margins-broker"
        capabilities = frozenset({BrokerCapability.FUNDS, BrokerCapability.MARGINS})

        async def get_funds(self, session: dict) -> dict:
            return {"available_margin": 500.0}

    registry = BrokerRegistry()
    registry.register(MarginsAdapter())
    assert "margins-broker" in registry
    gateway = BrokerGateway(registry)
    assert run(gateway.get_margins("margins-broker", {}))["available_margin"] == 500.0


def test_registry_rejects_an_unnamed_adapter():
    class Unnamed(AcmeBrokerAdapter):
        name = "base"

    with pytest.raises(BrokerAdapterInvalid):
        BrokerRegistry().register(Unnamed())


def test_duplicate_registration_is_ignored_not_fatal(acme_registry):
    """Startup paths run more than once; a harmless duplicate must not crash."""
    registry, first = acme_registry
    second = registry.register(AcmeBrokerAdapter())
    assert second is first
    assert len(registry) == 1


def test_unknown_broker_raises_a_normalized_broker_error():
    """A `BrokerError` subclass, so existing `except BrokerError` handlers catch it."""
    with pytest.raises(UnknownBrokerError) as excinfo:
        broker_gateway.resolve("not-a-broker")
    assert isinstance(excinfo.value, BrokerError)
    assert excinfo.value.code == BrokerErrorCode.UNKNOWN_BROKER.value


def test_supports_is_safe_for_an_unknown_broker():
    """Callers branching on a feature must not have to validate input first."""
    assert broker_gateway.supports("not-a-broker", BrokerCapability.HOLDINGS) is False


def test_the_default_registry_holds_one_instance_per_broker():
    """Health has to accumulate somewhere.

    `create_adapter` used to build a fresh instance per call, so nothing could
    observe a broker's error rate across requests — which is why the Admin
    Portal monitoring in BROKER_INTEGRATION.md had no data source.
    """
    from services.brokers import create_adapter

    assert create_adapter("zerodha") is create_adapter("zerodha")
    assert create_adapter("zerodha") is broker_registry.require("zerodha")


# ==================================================================
# Canonical contracts
# ==================================================================


def test_broker_specific_fields_do_not_survive_the_gateway(acme_gateway):
    """Leak containment, enforced at the boundary rather than by convention.

    Acme's holding carries `acme_internal_ref`. The canonical contract does not
    name it, so nothing above the gateway can come to depend on a field only one
    broker produces.
    """
    gateway, _ = acme_gateway
    holdings = run(gateway.get_holdings("acme", {"access_token": "t"}))
    assert "acme_internal_ref" not in holdings[0]
    assert set(holdings[0]) == set(BrokerHolding().as_dict())


def test_kite_style_raw_margin_tree_is_dropped():
    """The concrete leak this contract was written to stop.

    Zerodha's `get_funds` returned Kite's whole `equity`/`commodity` margin tree
    under a `raw` key, and Upstox mirrored the habit. Nothing read either; any
    consumer that started to would have been reading a shape only one broker
    produces.
    """
    funds = coerce_funds(
        {
            "available_margin": 100.0,
            "raw": {"equity": {"available": {"live_balance": 100.0}}},
        }
    )
    assert "raw" not in funds
    assert set(funds) == set(BrokerFunds().as_dict())


def test_every_broker_produces_identical_holding_keys():
    """The property the Portfolio Engine depends on and could not previously rely on."""
    zerodha_shape = coerce_holdings([{"symbol": "RELIANCE", "quantity": "5"}])[0]
    acme_shape = coerce_holdings([{"symbol": "ACMECO", "quantity": 5, "extra": 1}])[0]
    assert set(zerodha_shape) == set(acme_shape)
    # Strings from a broker become numbers exactly once, here.
    assert zerodha_shape["quantity"] == 5


def test_position_side_is_derived_when_a_broker_omits_it():
    """Lenient where leniency is safe: the sign of the quantity IS the side."""
    from services.brokers.contracts import coerce_positions

    assert coerce_positions([{"symbol": "X", "quantity": -3}])[0]["side"] == "SHORT"
    assert coerce_positions([{"symbol": "X", "quantity": 0}])[0]["side"] == "FLAT"


def test_an_order_without_an_id_is_rejected_rather_than_stored():
    """Strict where strictness matters: an untrackable order must not enter the book.

    An order with no id can never be modified, cancelled or reconciled. Passing
    it through would put a permanently orphaned row in `db.orders`.
    """
    with pytest.raises(BrokerContractError):
        coerce_orders([{"symbol": "X", "status": "OPEN"}], "acme")


def test_an_unmapped_order_status_is_rejected():
    """Status is a closed set; an unmapped value means the adapter mapping broke."""
    with pytest.raises(BrokerContractError):
        coerce_orders([{"order_id": "1", "status": "WEIRD_NEW_STATE"}], "acme")


def test_an_order_acknowledgement_is_not_inflated_into_a_full_order():
    """Why `BrokerOrderAck` exists as a separate contract.

    `BrokerEngine.place_order` persists `{**request, **ack}`. If the ack were
    coerced into a full `BrokerOrder`, its default zeros for quantity, price and
    symbol would overwrite the real values from the request and write a hollow
    row into the unified order book.
    """
    ack = BrokerOrderAck.from_broker({"order_id": "1"}, "acme").as_dict()
    assert set(ack) == {"order_id", "status", "broker"}
    request = {"symbol": "ACMECO", "quantity": 10, "price": 99.0}
    assert {**request, **ack}["quantity"] == 10


def test_holdings_from_a_broker_returning_nothing_is_an_empty_list_not_an_error():
    assert coerce_holdings(None) == []


# ==================================================================
# Error normalization
# ==================================================================


def test_an_arbitrary_adapter_exception_becomes_a_broker_error(acme_gateway):
    """No `KeyError`, `httpx` error or `struct.error` may cross the gateway.

    Anything that does becomes a 500 with a stack trace, or a broker's internal
    wording on a StockAssist surface.
    """
    gateway, adapter = acme_gateway
    with patch.object(type(adapter), "get_holdings", new=AsyncMock(side_effect=KeyError("last_price"))):
        with pytest.raises(BrokerError) as excinfo:
            run(gateway.get_holdings("acme", {}))
    error = excinfo.value
    assert error.code == BrokerErrorCode.ERROR.value
    assert error.broker == "acme"
    assert error.operation == "holdings"
    assert "KeyError" not in error.user_message
    assert "Acme Securities" in error.user_message
    # The original is chained, so the traceback survives in logs.
    assert isinstance(error.__cause__, KeyError)


def test_error_payload_never_carries_the_developer_message():
    error = BrokerError(
        "GET https://api.kite.trade/orders failed: token abc123", user_message="Please retry.", broker="zerodha"
    )
    assert error.as_dict()["message"] == "Please retry."
    assert "abc123" not in str(error.as_dict())


def test_retry_policy_is_derived_from_the_code_not_re_decided_per_call_site():
    assert BrokerError("x", code=BrokerErrorCode.TIMEOUT.value).retryable is True
    assert BrokerError("x", code=BrokerErrorCode.NETWORK.value).retryable is True
    assert BrokerError("x", code=BrokerErrorCode.RATE_LIMIT.value).retryable is True
    assert BrokerError("x", code=BrokerErrorCode.REJECTED.value).retryable is False
    assert BrokerAuthError().recovery == "reconnect_broker"


def test_normalize_leaves_an_already_normalized_error_alone_but_fills_context():
    original = BrokerError("boom")
    result = normalize_broker_error(original, broker="acme", operation="holdings")
    assert result is original
    assert result.broker == "acme" and result.operation == "holdings"


# ==================================================================
# Broker health
# ==================================================================


def test_repeated_api_failures_degrade_then_down_the_broker(acme_gateway):
    gateway, adapter = acme_gateway
    failure = BrokerError("upstream 503", code=BrokerErrorCode.NETWORK.value)
    with patch.object(type(adapter), "get_holdings", new=AsyncMock(side_effect=failure)):
        for _ in range(DOWN_AFTER_FAILURES):
            with pytest.raises(BrokerError):
                run(gateway.get_holdings("acme", {}))
    assert adapter.health.state is BrokerConnectionState.DOWN
    assert adapter.health.total_errors == DOWN_AFTER_FAILURES


def test_one_success_restores_a_degraded_broker(acme_gateway):
    gateway, adapter = acme_gateway
    failure = BrokerError("upstream 503", code=BrokerErrorCode.NETWORK.value)
    with patch.object(type(adapter), "get_holdings", new=AsyncMock(side_effect=failure)):
        for _ in range(3):
            with pytest.raises(BrokerError):
                run(gateway.get_holdings("acme", {}))
    assert adapter.health.state is BrokerConnectionState.DEGRADED
    run(gateway.get_holdings("acme", {"access_token": "t"}))
    assert adapter.health.state is BrokerConnectionState.UP
    assert adapter.health.consecutive_failures == 0


def test_an_expired_user_session_never_marks_the_broker_down(acme_gateway):
    """The load-bearing rule of the health model.

    Kite invalidates every access token daily at 06:00 IST. At 06:01 every
    connected user's next call raises `BrokerAuthError`. Counting those against
    broker health would drive Zerodha to DOWN every single morning while its API
    was perfectly available — and a dashboard that cries outage daily is a
    dashboard nobody reads.
    """
    gateway, adapter = acme_gateway
    with patch.object(type(adapter), "get_holdings", new=AsyncMock(side_effect=BrokerAuthError())):
        for _ in range(DOWN_AFTER_FAILURES * 2):
            with pytest.raises(BrokerAuthError):
                run(gateway.get_holdings("acme", {}))
    assert adapter.health.state is BrokerConnectionState.UNKNOWN
    assert adapter.health.total_errors == 0
    # Counted, though — a spike in these is a real signal, just a different one.
    assert adapter.health.total_auth_failures == DOWN_AFTER_FAILURES * 2


def test_a_rejected_order_does_not_mark_the_broker_down(acme_gateway):
    """A refusal is evidence about the request, not about the broker's API.

    Otherwise a user placing ten malformed orders marks the broker DOWN for
    everybody.
    """
    gateway, adapter = acme_gateway
    rejection = BrokerError("insufficient funds", code=BrokerErrorCode.REJECTED.value)
    with patch.object(type(adapter), "place_order", new=AsyncMock(side_effect=rejection)):
        for _ in range(DOWN_AFTER_FAILURES):
            with pytest.raises(BrokerError):
                run(gateway.place_order("acme", {}, {"symbol": "X", "quantity": 1}))
    assert adapter.health.state is BrokerConnectionState.UNKNOWN


def test_broker_health_starts_unknown_not_up():
    """A broker registered a millisecond ago must not report as healthy."""
    assert AcmeBrokerAdapter().health.state is BrokerConnectionState.UNKNOWN


# ==================================================================
# Authentication / configuration boundary
# ==================================================================


def test_credentials_are_read_at_call_time(monkeypatch):
    """Rotation must not require a process restart."""
    adapter = AcmeBrokerAdapter()
    monkeypatch.delenv("ACME_API_KEY", raising=False)
    monkeypatch.delenv("ACME_API_SECRET", raising=False)
    assert adapter.is_configured() is False
    monkeypatch.setenv("ACME_API_KEY", "k")
    monkeypatch.setenv("ACME_API_SECRET", "s")
    assert adapter.is_configured() is True
    assert adapter.credentials.api_key == "k"


def test_required_credentials_differ_per_broker(monkeypatch):
    """Upstox will not issue a token without a registered redirect URL; Zerodha will.

    Encoding that in the spec is why `is_configured()` can be one implementation
    for every broker instead of three lines re-written per adapter.
    """
    monkeypatch.setenv("UPSTOX_API_KEY", "k")
    monkeypatch.setenv("UPSTOX_API_SECRET", "s")
    monkeypatch.delenv("UPSTOX_REDIRECT_URL", raising=False)
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    assert broker_registry.require("upstox").is_configured() is False
    assert broker_registry.require("zerodha").is_configured() is True


def test_credentials_never_render_their_values():
    """SECURITY.md's no-credentials-in-logs rule, made inconvenient to break."""
    creds = resolve_credentials("acme", BrokerCredentialSpec(api_key_env="PATH"))
    assert "PATH" not in repr(creds)
    assert creds.api_key not in repr(creds)


def test_broker_urls_are_stripped_of_query_strings_before_logging():
    """Kite's logout endpoint carries the access token in the query string.

    The one pre-D3 place that logged a raw broker URL — the 401/403 branch in
    `_request` — would have written a live access token into the application log
    when a logout was rejected, which is exactly what happens for a token
    already dead at the broker.
    """
    from services.brokers.base import _safe_url

    url = "https://api.kite.trade/session/token?api_key=k&access_token=LIVE_SECRET"
    assert _safe_url(url) == "https://api.kite.trade/session/token"
    assert "LIVE_SECRET" not in _safe_url(url)


def test_each_broker_parses_its_own_oauth_callback():
    """Callback parsing is protocol knowledge and belongs to the adapter.

    The public callback route used to branch `if broker == "zerodha": ... else:
    # upstox`, where the `else` silently assumed every future broker speaks
    Upstox's dialect. Acme inherits the standard OAuth2 default and works with
    no route change.
    """
    assert broker_gateway.parse_callback_params("zerodha", {"status": "success", "request_token": "rt"}) == {
        "request_token": "rt"
    }
    assert broker_gateway.parse_callback_params("zerodha", {"status": "cancelled"}) is None
    assert broker_gateway.parse_callback_params("upstox", {"code": "c"}) == {"code": "c"}
    assert broker_gateway.parse_callback_params("upstox", {"error": "denied"}) is None
    assert AcmeBrokerAdapter().parse_callback_params({"code": "c"}) == {"code": "c"}


# ==================================================================
# User -> broker association
# ==================================================================


def test_connection_contract_separates_configured_connected_and_expired(acme_gateway):
    """Three orthogonal facts that used to be tangled in one inline dict."""
    gateway, _ = acme_gateway

    never = gateway.connection(user_id="u1", broker="acme", session=None)
    assert (never.connected, never.session_expired, never.mode) == (False, False, "disconnected")

    expired = gateway.connection(user_id="u1", broker="acme", session={"access_token": "t", "expires_at": _past()})
    assert (expired.connected, expired.session_expired, expired.mode) == (
        False,
        True,
        "ready" if expired.configured else "disconnected",
    )

    live = gateway.connection(
        user_id="u1", broker="acme", session={"access_token": "t", "expires_at": _future(), "account_id": "ACME1"}
    )
    assert (live.connected, live.session_expired, live.mode) == (True, False, "live")
    assert live.account_id == "ACME1"


def test_connection_contract_carries_no_token_material(acme_gateway):
    """It travels to routes, events, logs and AI context — it must be safe there."""
    gateway, _ = acme_gateway
    connection = gateway.connection(
        user_id="u1",
        broker="acme",
        session={"access_token": "SUPER_SECRET", "refresh_token": "ALSO_SECRET", "expires_at": _future()},
    )
    rendered = str(connection.as_dict())
    assert "SUPER_SECRET" not in rendered
    assert "ALSO_SECRET" not in rendered
    assert "access_token" not in connection.as_dict()


def test_connection_contract_reports_the_brokers_capabilities(acme_gateway):
    """So a consumer can decide what a connection enables without importing brokers."""
    gateway, _ = acme_gateway
    connection = gateway.connection(user_id="u1", broker="acme", session=None)
    assert connection.capabilities == ["holdings", "place_order"]


# ==================================================================
# Source Manager integration (the D3 -> D4 seam)
# ==================================================================


def test_source_manager_tracks_connected_brokers_from_the_event_bus():
    """MARKET_DATA_ARCHITECTURE.md Source Manager responsibility 1.

    Unimplementable before D3 for a mundane reason: `broker.connected` and
    `broker.disconnected` were documented in BROKER_INTEGRATION.md and never
    published by anything. The two subsystems meet only on the Event Bus — the
    Market Engine imports no broker module and the broker layer imports no
    Market Engine module.
    """
    manager = SourceManager()
    manager.subscribe_broker_events()
    try:
        run(
            event_bus.publish(
                "broker.connected", {"user_id": "u1", "broker": "zerodha", "capabilities": ["holdings", "tick_stream"]}
            )
        )
        assert manager.connected_brokers("u1") == ["zerodha"]
        assert manager.streaming_brokers("u1") == ["zerodha"]
        assert manager.has_broker_connected("u1") is True

        run(event_bus.publish("broker.disconnected", {"user_id": "u1", "broker": "zerodha"}))
        assert manager.connected_brokers("u1") == []
        assert manager.has_broker_connected("u1") is False
    finally:
        _unsubscribe(manager)


def test_a_broker_without_a_tick_feed_is_connected_but_not_a_streaming_candidate():
    """Read from the capabilities on the event, not from a broker name.

    This is precisely the question D4's feed switch asks before promoting a
    broker to a priority-1 market provider.
    """
    manager = SourceManager()
    manager.subscribe_broker_events()
    try:
        run(
            event_bus.publish(
                "broker.connected", {"user_id": "u2", "broker": "upstox", "capabilities": ["holdings", "orders"]}
            )
        )
        assert manager.connected_brokers("u2") == ["upstox"]
        assert manager.streaming_brokers("u2") == []
    finally:
        _unsubscribe(manager)


def test_broker_tracking_is_scoped_per_user():
    """A broker feed is legally the user's own data and must never leak across users."""
    manager = SourceManager()
    manager.record_broker_connected("u1", "zerodha", ["tick_stream"])
    assert manager.connected_brokers("u2") == []
    assert manager.streaming_brokers("u2") == []


def test_subscribing_twice_does_not_double_handle_events():
    """The Event Bus appends handlers without de-duplicating."""
    manager = SourceManager()
    manager.subscribe_broker_events()
    manager.subscribe_broker_events()
    try:
        handlers = [
            h for h in event_bus._handlers.get("broker.connected", []) if getattr(h, "__self__", None) is manager
        ]
        assert len(handlers) == 1
    finally:
        _unsubscribe(manager)


def test_d3_does_not_register_a_broker_as_a_market_data_provider():
    """The deliberate D3/D4 line, pinned so D4 has a red-to-green target.

    Registering a broker feed now would mean either a fabricated streaming tier
    (forbidden by CLAUDE.md's data rules) or a REST-polled provider silently
    taking a connected user's quotes away from the baseline with none of the
    make-before-break machinery that makes such a switch safe. D3 delivers the
    record; D4 attaches the registration to it.
    """
    from services.market_engine.providers import provider_registry

    manager = SourceManager()
    manager.record_broker_connected("u1", "zerodha", ["tick_stream"])

    assert manager.streaming_brokers("u1") == ["zerodha"]
    assert "zerodha" not in provider_registry
    assert not any(p.owner_user_id == "u1" for p in provider_registry.all())


def test_connecting_a_broker_publishes_the_lifecycle_event_the_source_manager_needs():
    """The engine -> Event Bus -> Source Manager path, end to end.

    Asserted through `BrokerEngine` rather than by publishing by hand, because
    the failure this guards against is the one that existed for the whole life
    of the codebase before D3: the topic was documented, the subscriber was
    specified, and nothing ever published.
    """
    from services.broker_engine import BrokerEngine
    from tests._fakedb import FakeDB

    engine = BrokerEngine()
    engine.configure(FakeDB())
    manager = SourceManager()
    manager.subscribe_broker_events()
    try:
        session = {"access_token": "t", "expires_at": _future(), "account_id": "AB1", "profile": {"user_id": "AB1"}}
        with (
            patch.object(ZerodhaAdapter, "exchange_token", new=AsyncMock(return_value=session)),
            patch.object(BrokerEngine, "sync_portfolio", new=AsyncMock(return_value={"summary": {}})),
        ):
            run(engine.complete_auth("zerodha", "u9", {"request_token": "rt"}))

        assert manager.connected_brokers("u9") == ["zerodha"]
        assert manager.streaming_brokers("u9") == ["zerodha"]

        with patch.object(ZerodhaAdapter, "invalidate_session", new=AsyncMock()):
            run(engine.disconnect("zerodha", "u9"))
        assert manager.connected_brokers("u9") == []
    finally:
        _unsubscribe(manager)


# ==================================================================
# Helpers
# ==================================================================


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()


def _unsubscribe(manager) -> None:
    event_bus.unsubscribe("broker.connected", manager._on_broker_connected)
    event_bus.unsubscribe("broker.disconnected", manager._on_broker_disconnected)


def _strip_comments_and_strings(source: str) -> str:
    """Executable code only — comments, docstrings and string literals removed.

    D3 left explanatory comments quoting the broker-name branches it deleted
    ("this used to read `if broker == \\"zerodha\\"`"). Those are documentation of
    a fixed defect, not a recurrence of it, and a structural test that cannot
    tell them apart would force the explanation to be deleted to stay green.
    """
    without_docstrings = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', '""', source)
    without_strings = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', '""', without_docstrings)
    return re.sub(r"#[^\n]*", "", without_strings)

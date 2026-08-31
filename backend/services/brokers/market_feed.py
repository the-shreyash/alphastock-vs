"""Broker stream → market-data provider: the registration seam (D4.4).

WHERE THIS SITS
---------------
This is the last link in a chain the previous three sprints built one segment at
a time::

    broker wire frame
          ↓  broker-owned codec                        (D4.2, streaming.py)
    BrokerTick
          ↓  instrument identity mapping                (D4.3, instruments.py)
    MarketTick                                          canonical
          ↓  THIS MODULE
    StreamingTickProvider                               a registered provider
          ↓
    Market Gateway → Source Manager → Event Bus → Market Engine

Before D4.4 a broker's ticks reached `portfolio_stream`, `trade_stream` and the
user's socket and stopped there. They drove P&L and nothing else: the Market
Engine had no idea a live feed existed, `source_manager.status()` reported the
delayed baseline to a user watching tick-by-tick prices, and the TICKS
capability resolved to nothing for everybody. A broker feed was real data that
was not market data. This module is what makes it market data.

WHY THE CONSTRUCTION LIVES HERE AND NOT IN THE MARKET ENGINE
-------------------------------------------------------------
The Market Engine may not import the broker layer — pinned by
`test_the_market_engine_never_imports_a_broker_module`, and load-bearing rather
than stylistic: it is what lets a broker WebSocket and a licensed exchange feed
be indistinguishable to the Source Manager, so priority stays provider metadata
instead of becoming `if broker == …`. broker → market is the permitted
direction, so the broker side constructs the provider, names it, and injects it
through the Market Gateway. `StreamingTickProvider` itself is entirely generic
and names no broker.

Adding a second streaming broker therefore adds nothing here, nothing in the
Market Engine, and nothing in any core service. It adds one adapter, exactly as
Developer Rule 9 of MARKET_DATA_ARCHITECTURE.md requires.

WHAT D4.5 ADDED: THE ACCOUNT'S SIDE OF THE READINESS GATE
----------------------------------------------------------
D4.4 registered the feed and stopped there — the provider declared `TICKS` and
not `QUOTES`, so it took nothing away from the baseline, and no switch existed.
D4.5 builds the switch in the Market Engine, generically, and this module
supplies the two account-level facts the gate needs and only the broker side
knows:

  * **what the feed was asked for.** :func:`attach_market_feed` subscribes the
    provider to the account's canonical instrument universe. A provider with no
    subscription can never reach READY, which is deliberate: the platform will
    not promote a feed over the baseline on the strength of a socket nobody can
    say what was asked of.
  * **whether the wire is actually up.** :func:`set_market_feed_link` relays the
    transport's own connect/disconnect, which is what makes failover immediate
    and push-driven — the side holding the socket already knows the moment it
    dies, so nothing polls and nothing waits for a health counter.

What this module still does not do is *decide* anything. It has no notion of
primary, standby, promotion or failover; those live entirely in
`StreamingTickProvider` and the Source Manager, where they are the same for
every feed. That is what keeps a second broker from needing a line here. See
`StreamingTickProvider` for the full reasoning and ADR-035.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from services.brokers.capabilities import BrokerCapability
from services.brokers.gateway import broker_gateway
from services.brokers.sharding import DEFAULT_SHARD_ID
from services.market_engine.gateway import market_gateway
from services.market_engine.providers import StreamingTickProvider, provider_registry
# Re-exported for the broker engine, which names these reasons at the three
# call sites that detach a feed. This module is the documented seam between the
# broker layer and the Market Engine, so the vocabulary crossing it crosses
# here and not in four places.
from services.market_engine.source_manager import FeedChangeReason  # noqa: F401

logger = logging.getLogger(__name__)

#: Prefix for provider names minted here. Namespaced so a broker-fed provider
#: can never collide with a platform-wide one — `"zerodha"` as a bare provider
#: name would be ambiguous the day a licensed feed is sourced through the same
#: broker's institutional API.
FEED_NAME_PREFIX = "brokerfeed"


def feed_provider_name(user_id: Any, broker: str) -> str:
    """The stable registry name for one account's market feed.

    Carries the broker name and the user id. Both are legitimate *here*: a
    provider name reaches the registry, the gateway's logs and the admin
    diagnostics surface and nowhere else — `source_manager.status()`, every
    normalized event and every API response carry a `source_tier` and no
    identity at all (Developer Rule 4).
    """
    return f"{FEED_NAME_PREFIX}:{broker}:{user_id}"


async def attach_market_feed(
    user_id: Any,
    broker: str,
    symbols: Optional[Sequence[str]] = None,
    shards: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """Register this account's broker stream as a market-data provider.

    Returns the provider name, or None when the broker is not a candidate. The
    single gate is the broker's own capability declaration: a broker that does
    not declare `TICK_STREAM` has no tick feed to register, and registering one
    anyway would produce a priority-1 streaming provider that can only ever
    deliver silence — which the Source Manager would rank *above* the working
    baseline. The capability model is the authority on what a broker serves;
    this module does not second-guess it and does not probe for methods.

    `symbols` is the account's canonical instrument universe — the same
    holdings-and-positions set the stream subscribes to on the wire, named the
    way the platform names instruments. Passing it is what lets the provider
    reach READY at all (D4.5): registering and connecting are not evidence a
    feed can serve, and a feed that never declared what it asked for cannot
    later claim to be delivering it. Attaching without symbols is legal and
    yields a feed that serves TICKS and never displaces the baseline — the
    correct behaviour for an account with nothing to stream.

    `shards` is how many broker connections this account's subscription needed
    and what they are called (D5.10) — the plan `services/brokers/sharding.py`
    produced, passed here for the same reason `symbols` is. A feed that never
    said what it is made of cannot be asked whether *all* of it is working, and
    "every connection has fresh evidence" quantified over nothing is vacuously
    true — which is exactly how a healthy connection would come to mask a dead
    one. Omitting it is one connection, which is what every caller written
    before D5.10 means and what an unsharded feed is.

    ONE PROVIDER, HOWEVER MANY CONNECTIONS. This function does not gain a loop:
    the account still gets exactly one `StreamingTickProvider` under exactly one
    registry name, because a shard is not a provider. Registering one per shard
    would put N feeds of one account into the Source Manager's ranking, each
    covering a slice of the portfolio, each earning readiness and probation
    separately and each able to displace the others — a second market-data
    architecture, which is the one thing D5.10 must not build.

    Idempotent. A reconnecting stream re-registers under the same name and
    replaces the provider bound to the socket that died.
    """
    if not user_id or not broker:
        return None
    if not broker_gateway.supports(broker, BrokerCapability.TICK_STREAM):
        logger.debug(
            "Broker %s does not declare %s — not registering it as a market-data provider",
            broker,
            BrokerCapability.TICK_STREAM.value,
        )
        return None

    name = feed_provider_name(user_id, broker)
    provider = StreamingTickProvider(name, owner_user_id=str(user_id))
    provider.declare_shards(shards or ())
    await market_gateway.register_streaming_provider(provider)
    if symbols:
        await provider.subscribe(symbols)
    return name


async def set_market_feed_link(user_id: Any, broker: str, *, up: bool,
                               reason: str = "", shard: str = DEFAULT_SHARD_ID) -> bool:
    """Relay one of this account's transport connect/disconnects to its provider.

    True when the provider's readiness actually changed. False — not an error —
    when no provider is registered for the account, which is the normal state
    for a broker with no tick stream.

    WHY THIS IS NOT A DETACH
    ------------------------
    A dropped socket that is reconnecting is not an ended entitlement. Detaching
    would unregister the provider, discard its diagnostics, and then re-register
    a fresh one on every blip of a flapping connection. Marking the link down
    leaves the feed registered and merely un-resolvable: the baseline serves the
    next request, and the feed climbs back through the same readiness gate it
    passed the first time, on the connection that actually exists.

    Entitlement termination is a different event with a different handler —
    :func:`detach_market_feed`.

    `shard` names which of the account's connections moved (D5.10). Relaying it
    is what lets one connection drop without blanking the instruments the others
    are still delivering, and — the other half, which matters more — what stops
    a healthy connection from covering for a dead one: the provider discards the
    lost connection's evidence and its prices, and every provider-level claim
    (freshness, the tier a user is told they are on, latency, stability)
    tightens immediately. See `StreamingTickProvider.mark_link_down`.
    """
    provider = provider_registry.get(feed_provider_name(user_id, broker))
    if provider is None:
        return False
    if up:
        return await provider.mark_link_up(shard)
    return await provider.mark_link_down(reason, shard)


async def detach_market_feed(
    user_id: Any,
    broker: str,
    *,
    change_reason: Optional[FeedChangeReason] = None,
) -> bool:
    """Unregister this account's market feed. True when one was removed.

    Called when the entitlement ends — disconnect, revoked token, expired
    session. A broker feed is legally the user's own data, so an ended
    entitlement must stop being resolvable immediately rather than at the next
    health transition.

    `change_reason` (D5.13) names which of those three it was, in the Market
    Engine's own consumer-facing vocabulary, so the owner's `provider.status`
    can say why their tier moved instead of only that it did (LIM-D5.5-2). It
    carries nothing the broker said: no wire code, no error text, no broker
    name. Those stay in the audit row and the admin diagnostics, which are the
    surfaces allowed to hold them.

    The direction of the dependency is the one the platform has enforced since
    D3 — the broker layer names a Market Engine value, and the Market Engine
    imports nothing from here.
    """
    if not user_id or not broker:
        return False
    return await market_gateway.unregister_streaming_provider(
        feed_provider_name(user_id, broker), change_reason=change_reason)


async def publish_market_ticks(user_id: Any, broker: str, ticks: Sequence[Any],
                               shard: str = DEFAULT_SHARD_ID) -> int:
    """Push a batch of canonical ticks into this account's provider.

    `ticks` are `MarketTick` dicts, exactly as `instruments.canonical_ticks`
    produces them — this module performs no conversion of its own, because a
    second conversion path is a second place for the two to drift.

    Returns how many records the provider accepted. Zero when no provider is
    registered for the account, which is the normal state for a broker with no
    tick stream and is not an error.

    The account's provider is looked up in the *existing* provider registry
    rather than in a map kept here. A second registry would have to be kept in
    step with the first across register, unregister, replace and process
    restart, and would answer differently the moment one of those was missed.

    `shard` names which of the account's connections delivered them (D5.10), so
    the provider can attribute coverage, freshness and delivery cadence to the
    socket that actually earned them. Merging them would make a feed appear to
    get faster for having been split, and would let one connection's ticks stand
    as evidence that another is alive.
    """
    if not ticks:
        return 0
    provider = provider_registry.get(feed_provider_name(user_id, broker))
    if provider is None:
        return 0
    return await provider.on_raw(list(ticks), shard)

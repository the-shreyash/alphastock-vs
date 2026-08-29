"""Sprint D5.10 — instrument sharding across several broker connections.

WHAT THIS SPRINT IS, AND WHAT IT IS NOT
----------------------------------------
Every streaming broker caps how many instruments one connection may carry, and
until D5.10 every adapter answered an over-cap subscription the same way: take a
deterministic prefix, warn, and leave the account's feed quietly narrower than
its portfolio. D5.10 replaces that with "use as many connections as the
instruments need" — **below the canonical provider boundary**.

That last clause is the whole sprint, and most of this module tests it rather
than the arithmetic. A shard is not a provider: it has no registry entry, it is
not ranked, it earns no readiness a consumer can observe, and it appears in no
consumer payload. The unit everything above the boundary sees is still exactly
one `StreamingTickProvider` per account per broker.

THE TWO REQUIREMENTS THAT PULL AGAINST EACH OTHER
--------------------------------------------------
The brief asks for both of these, and they cannot both be satisfied by one
predicate:

  * **preserve valid data from the healthy shards** when one shard dies. A feed
    of three connections that loses one must go on answering for the
    instruments the other two are still delivering.
  * **never let a healthy shard mask a dead one.** A feed whose second
    connection died an hour ago must not report itself live.

They are only jointly satisfiable because the existing architecture already
separates the two questions, and D5.10 resolves them along that existing seam
rather than inventing a third state:

  * `READY` and per-symbol `covers()` — the *serving* gate — remain "at least one
    connection is delivering", which is the faithful reading of what READY has
    always meant ("this feed has proved it can produce valid canonical data").
    Partial coverage has lived in `covers()` since D4.5, and it is exact: a lost
    shard's prices are discarded the instant its link drops, so its instruments
    fall to the baseline while its siblings' keep serving.
  * every provider-level *claim* — freshness, the tier a user is told they are
    on, latency, stability — becomes "**every** declared connection", by making
    `_last_evidence_at` the minimum over shards and `_ready_since` the maximum.
    Neither `has_fresh_evidence` nor `stability` needed a line changed.

THE COST OF RESOLVING IT THAT WAY, STATED RATHER THAN HIDDEN
--------------------------------------------------------------
`stability` reads `has_fresh_evidence` as its final term (D5.3), so a feed with
a lost connection falls to PROBATION — and D5.2 ranks a probationary provider
below a steady one. **A partially failed sharded feed is therefore ranked below
the delayed baseline for every instrument, including the ones its healthy
connections are still delivering, until the lost connection is restored and has
served a full window.** Its data is genuinely preserved — the feed stays
eligible, keeps its coverage, and answers whenever nothing steadier remains —
but it is not preferred.

That is D5.2's published rule applied unchanged, and it is the honest reading of
"one connection of this feed has just failed". The alternative — a per-shard
stability term so surviving connections keep their claim to the primary position
— is the second ranking system the brief forbids, and it would let a feed with a
permanently dead connection hold the primary position indefinitely. Recorded as
LIM-D5.10-3 and asserted below rather than left to be discovered.

Tests below are grouped by the brief's sections. No test opens a socket or
reaches a broker API.
"""

import ast
import contextlib
import inspect
import logging
import pathlib
import re
from unittest.mock import AsyncMock, patch

import pytest

from services.brokers.capabilities import BrokerCapability
from services.brokers.sharding import (
    DEFAULT_SHARD_ID,
    InstrumentShard,
    plan_shards,
    shard_id,
)
from services.brokers.stream import BrokerStream, BrokerStreamManager
from services.brokers.streaming import DEFAULT_STREAM_CHANNEL, BrokerStreamChannel, StreamEventKind
from services.market_engine.providers import (
    DEFAULT_FEED_SHARD,
    LATENCY_TAIL_WINDOW_SAMPLES,
    LATENCY_WINDOW_SAMPLES,
    PROBATION_WINDOW_SECONDS,
    Capability,
    FeedReadiness,
    FeedStability,
    ProviderRegistry,
    ResolutionContext,
    StreamingTickProvider,
    YahooPollingAdapter,
)
from services.market_engine.source_manager import SourceManager

from tests.test_broker_streaming import (
    NovaAdapter,
    _clean_provider_registry,
    _strip_source,
    nova_registered,
    run,
)
from tests.test_provider_probation import FakeClock, _tick

BACKEND = pathlib.Path(__file__).resolve().parent.parent

#: A cadence a healthy feed delivers at, and one a struggling one does.
FAST = 0.25
SLOW = 4.0


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _sharded_feed(shards=("0", "1", "2"), symbols=("A", "B", "C"), clock=None,
                  user_id="u1"):
    """A registered feed spread over `shards`, connected and subscribed.

    Every connection is reported up, which is the state the transport leaves a
    freshly opened plan in: sockets open, subscribe frames away, nothing
    delivered yet.
    """
    clock = clock or FakeClock()
    registry = ProviderRegistry()
    baseline = YahooPollingAdapter()
    registry.register(baseline)
    run(baseline.connect())
    feed = StreamingTickProvider(f"feed:{user_id}", owner_user_id=user_id, clock=clock)
    feed.declare_shards(shards)
    registry.register(feed)
    run(feed.connect())
    run(feed.subscribe(symbols))
    for shard in shards:
        run(feed.mark_link_up(shard))
    return registry, SourceManager(registry), baseline, feed, clock


def _quote_provider(manager, user_id="u1", symbol="A"):
    return manager.resolve(
        Capability.QUOTES, context=ResolutionContext(user_id=user_id, symbol=symbol)
    )


def _deliver(feed, shard, symbol="A", price=100.0):
    return run(feed.on_raw([_tick(symbol=symbol, price=price)], shard))


def _serve_window(feed, clock, shards, symbols=None):
    """Deliver valid data on EVERY named connection across a full probation window.

    Every connection, not one at a time: `_ready_since` is the newest
    connection's and `_last_evidence_at` the oldest's, so serving them
    sequentially leaves the window measured between two different connections
    and the feed never leaves probation. That is the aggregation working, and
    the helper has to respect it.
    """
    symbols = symbols or {shard: "A" for shard in shards}
    for _ in range(2):
        for shard in shards:
            _deliver(feed, shard, symbols[shard])
        clock.advance(PROBATION_WINDOW_SECONDS + 1)
    for shard in shards:
        _deliver(feed, shard, symbols[shard])


def _establish_latency(feed, clock, shard, gap, samples=LATENCY_TAIL_WINDOW_SAMPLES,
                       symbol="A"):
    """Fill one connection's interval window at a fixed cadence."""
    _deliver(feed, shard, symbol)
    for _ in range(samples):
        clock.advance(gap)
        _deliver(feed, shard, symbol)


class _LimitedChannel(BrokerStreamChannel):
    """A fictional broker's tick channel with a small per-connection ceiling."""

    name = "market"
    protocol = "nova_feed"
    delivers = frozenset({StreamEventKind.TICKS})
    max_instruments_per_connection = 2


class _ShardingNova(NovaAdapter):
    """Nova, whose one socket holds two instruments (D5.10, the real seam).

    Tick-only, because its single channel declares only TICKS and the registry
    refuses an adapter claiming a capability no channel delivers — a D4.7
    control this fictional broker is subject to like any real one.
    """

    capabilities = frozenset({BrokerCapability.TICK_STREAM})

    def stream_channels(self):
        return (_LimitedChannel(),)


# ══════════════════════════════════════════════════════════════════
# §5 — Shard planning rules
# ══════════════════════════════════════════════════════════════════

def test_empty_input_produces_zero_shards():
    """Not one empty shard.

    A connection with nothing subscribed on it delivers nothing, and opening one
    would register a link the provider then waits forever to hear from — which
    is the "declared but never delivering" shard that makes every all-shards
    predicate below permanently false.
    """
    assert list(plan_shards([], max_instruments_per_connection=10)) == []
    assert list(plan_shards(None, max_instruments_per_connection=10)) == []


def test_no_declared_limit_is_one_shard_holding_everything():
    """The default, and the whole of the byte-for-byte preservation claim.

    Every channel written before D5.10 declares no limit. A missing limit is
    never guessed at: an invented ceiling would shard a broker that does not need
    it and spend a connection the broker may not permit.
    """
    plan = plan_shards(list(range(1000)))
    assert len(plan) == 1
    assert plan.shards[0].id == DEFAULT_SHARD_ID
    assert len(plan.shards[0]) == 1000
    assert plan.limit is None


@pytest.mark.parametrize("count,limit,expected", [
    (1, 10, 1),
    (9, 10, 1),
    (10, 10, 1),      # exactly at the limit is ONE connection
    (11, 10, 2),      # the limit plus one is TWO
    (20, 10, 2),
    (21, 10, 3),
    (10_000, 3_000, 4),
])
def test_the_shard_count_is_the_minimum_that_fits(count, limit, expected):
    """`ceil(N / L)`, and never one more.

    The at-the-limit / limit-plus-one pair is the off-by-one that would either
    cost an account its last instrument or open a whole connection for nothing.
    """
    plan = plan_shards(list(range(count)), max_instruments_per_connection=limit)
    assert len(plan) == expected
    assert plan.instrument_count == count


def test_no_instrument_is_lost_duplicated_or_reordered():
    """The property the whole planner exists to keep.

    Concatenating the shards in plan order must reproduce the input exactly —
    which simultaneously rules out a dropped final instrument, a duplicated one,
    and any reordering.
    """
    instruments = [f"INS{i}" for i in range(97)]
    plan = plan_shards(instruments, max_instruments_per_connection=10)
    rebuilt = [i for shard in plan for i in shard.instruments]
    assert rebuilt == instruments
    assert len(set(rebuilt)) == len(instruments)


def test_the_plan_is_deterministic_across_equivalent_calls():
    """Same inputs, same shard ids holding the same instruments, every time.

    Not incidental: an account whose plan changed shape between two identical
    portfolio syncs would reconnect every connection for no reason, and — worse
    — would re-earn readiness and re-serve probation on all of them.
    """
    instruments = [f"INS{i}" for i in range(55)]
    first = plan_shards(instruments, max_instruments_per_connection=7)
    second = plan_shards(list(instruments), max_instruments_per_connection=7)
    assert first == second
    assert first.ids == second.ids


def test_duplicates_are_removed_rather_than_subscribed_twice():
    """A repeat must not become two wire subscriptions on two sockets.

    Left alone, a duplicate straddling a shard boundary is the same instrument
    subscribed on two connections — billed against the broker's limit twice and
    answered with two tick streams — and it inflates the count the planner
    divides by, so a subscription could be split where one connection would have
    held it.
    """
    plan = plan_shards([1, 2, 2, 3, 1, 4], max_instruments_per_connection=2)
    assert [list(s.instruments) for s in plan] == [[1, 2], [3, 4]]


def test_the_concurrent_connection_ceiling_caps_the_plan_and_says_so(caplog):
    """A broker that permits five connections is never handed six.

    One of the five existing brokers does not refuse a connection past its
    ceiling — it disconnects the *oldest*. A plan that ignored the ceiling would
    therefore destroy the connection it opened first, on every plan, forever.
    """
    with caplog.at_level(logging.WARNING):
        plan = plan_shards(list(range(60)), max_instruments_per_connection=10,
                           max_connections=5, broker="nova", channel="market")
    assert len(plan) == 5
    assert plan.instrument_count == 50
    assert plan.dropped == 10
    assert "60" in caplog.text and "5" in caplog.text


def test_shard_ids_are_positional_and_carry_nothing_else():
    """A shard id reaches a registry key, a task name and a log line.

    So it is a position and nothing more: no symbol, no user id, no broker name,
    no credential. Asserted rather than assumed, because it is the input to the
    security sweep further down.
    """
    plan = plan_shards(["SECRET-TOKEN-ABC", "RELIANCE", "TCS"],
                       max_instruments_per_connection=1)
    assert plan.ids == ("0", "1", "2")
    assert shard_id(0) == DEFAULT_SHARD_ID


def test_the_planner_names_no_broker():
    """The same ban `stream.py` and `broker_engine.py` are under.

    Chunking a list is not broker knowledge; the number to chunk at is, and it
    arrives as a declared capability. A broker name appearing in this module
    would mean a per-broker branch had been written into generic sharding logic.
    """
    #: Comment-inclusive, deliberately stricter than `stream.py`'s sweep. This
    #: module is new, so nothing forces its prose to discuss a broker — and its
    #: first draft named two in a docstring, which is exactly the drift a
    #: stripped sweep would have let through. Each broker's numbers live in
    #: BROKER_INTEGRATION.md, where they can be maintained.
    source = (BACKEND / "services" / "brokers" / "sharding.py").read_text()
    for broker in ("zerodha", "kite", "upstox", "angel", "smartapi", "fyers", "hsm", "dhan"):
        assert not re.search(broker, source, re.IGNORECASE), (
            f"the shard planner names {broker}")


def test_a_broker_specific_branch_cannot_hide_behind_a_variable():
    """Mutation-shaped: an identifier sweep is not enough on its own.

    D4.4 found that a string literal defeats an identifier sweep, so this reads
    the parsed tree and asserts the planner branches on nothing but its declared
    arguments — no attribute lookup on a broker, no registry, no adapter.
    """
    tree = ast.parse((BACKEND / "services" / "brokers" / "sharding.py").read_text())
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "services" not in imported, (
        "the shard planner imports from the platform — it must be pure arithmetic "
        "over a list and two numbers")


# ══════════════════════════════════════════════════════════════════
# §4 — per-broker limits, and the one that must NOT be sharded
# ══════════════════════════════════════════════════════════════════

def _channel(broker, name=None):
    from services.brokers.registry import broker_registry

    channels = broker_registry.require(broker).stream_channels()
    for channel in channels:
        if StreamEventKind.TICKS in channel.delivers and (name is None or channel.name == name):
            return channel
    raise AssertionError(f"{broker} declares no tick channel")


@pytest.mark.parametrize("broker,limit", [
    ("zerodha", 3000),
    ("upstox", 5000),
    ("fyers", 5000),
    ("dhan", 5000),
])
def test_each_brokers_per_connection_limit_is_its_own(broker, limit):
    """Copied from no other broker, and read from the repository's own record.

    The numbers are the ones D4.6–D4.11 documented when each adapter recorded
    "enforced by trimming rather than by sharding (D5 owns sharding)".
    """
    assert _channel(broker).max_instruments_per_connection == limit


def test_a_session_quota_is_not_declared_as_a_connection_limit():
    """The audit finding, pinned: sharding cannot raise a per-session quota.

    One of the five brokers caps tokens per *session*, counted across the client
    code. Declaring that number as a per-connection limit would open a second
    socket the same quota refuses — spending one of that broker's three
    permitted connections to subscribe to nothing, and turning today's honest
    warning into a dead feed. `None` is the truthful answer and it produces
    exactly one connection.
    """
    from services.brokers import angelone

    channel = _channel("angelone")
    assert channel.max_instruments_per_connection is None
    assert angelone.MAX_SUBSCRIBED_INSTRUMENTS == 1000, (
        "the quota itself is unchanged — it is still enforced by trimming")
    plan = plan_shards(
        list(range(4000)),
        max_instruments_per_connection=channel.max_instruments_per_connection,
    )
    assert len(plan) == 1, "a session quota was sharded"


def test_a_frame_limit_is_not_a_connection_limit():
    """Two adapters batch one subscription across several frames on one socket.

    That is wire framing the codec already owns. Confusing it with a connection
    limit would open fifty sockets where one would do.
    """
    from services.brokers import dhan, fyers

    assert dhan.MAX_INSTRUMENTS_PER_FRAME < dhan.MAX_INSTRUMENTS_PER_CONNECTION
    assert _channel("dhan").max_instruments_per_connection == dhan.MAX_INSTRUMENTS_PER_CONNECTION
    assert fyers.SUBSCRIBE_BATCH_SIZE < fyers.MAX_SUBSCRIBED_INSTRUMENTS
    assert _channel("fyers").max_instruments_per_connection == fyers.MAX_SUBSCRIBED_INSTRUMENTS


def test_only_the_broker_that_documents_a_connection_ceiling_declares_one():
    """No invented ceilings. One broker documents one; the rest declare None."""
    from services.brokers import dhan

    assert _channel("dhan").max_connections == dhan.MAX_CONNECTIONS_PER_USER == 5
    for broker in ("zerodha", "upstox", "fyers", "angelone"):
        assert _channel(broker).max_connections is None


def test_a_channel_that_carries_no_instruments_declares_no_limit():
    """An order channel has no instrument subscription for a limit to apply to."""
    from services.brokers.registry import broker_registry

    for channel in broker_registry.require("upstox").stream_channels():
        if StreamEventKind.TICKS not in channel.delivers:
            assert channel.max_instruments_per_connection is None


def test_the_default_channel_contract_is_unchanged():
    """A channel that has never heard of sharding is a single-connection channel."""
    assert BrokerStreamChannel.max_instruments_per_connection is None
    assert BrokerStreamChannel.max_connections is None


# ══════════════════════════════════════════════════════════════════
# §2 — a shard is not a provider
# ══════════════════════════════════════════════════════════════════

def test_a_sharded_account_registers_exactly_one_provider():
    """The invariant, stated as the thing a mutation would break.

    One provider per account per broker, however many connections it holds.
    Registering one per shard would put N feeds of one account into the Source
    Manager's ranking, each covering a slice of the portfolio and each able to
    displace the others — a second market-data architecture.
    """
    from services.brokers.market_feed import attach_market_feed, feed_provider_name

    with _clean_provider_registry() as registry, nova_registered(_ShardingNova()):
        name = run(attach_market_feed("u1", "nova", ("A", "B", "C", "D", "E"),
                                      ("0", "1", "2")))
        assert name == feed_provider_name("u1", "nova")
        mine = [n for n in registry._providers if n.startswith("brokerfeed:nova:")]
        assert mine == [name], "a shard was registered as a provider of its own"
        assert registry.get(name).shard_count == 3


def test_the_source_manager_ranks_the_provider_not_its_connections():
    """`status()` reports one streaming feed for a three-connection account."""
    _registry, manager, _baseline, feed, clock = _sharded_feed()
    _serve_window(feed, clock, ("0", "1", "2"), {"0": "A", "1": "B", "2": "C"})
    # The Source Manager's own view of the account: one streaming feed, one
    # tier. A shard registered as a provider of its own would appear here as a
    # second streaming candidate for the same user.
    status = manager.status(user_id="u1")
    assert status["tier"] == "streaming"
    ranked = manager.resolve_feed(
        Capability.QUOTES, ResolutionContext(user_id="u1", symbol="A",
                                             capability=Capability.QUOTES))
    assert ranked.available
    streaming = [
        provider for provider in manager._registry.all()
        if getattr(provider, "owner_user_id", None) == "u1"
    ]
    assert len(streaming) == 1, "an account's connections were ranked as separate providers"


# ══════════════════════════════════════════════════════════════════
# §6 / §7 — readiness, freshness, probation, latency, failure isolation
# ══════════════════════════════════════════════════════════════════

def test_one_connections_data_does_not_make_the_whole_feed_fresh():
    """The masking failure, and the reason `_last_evidence_at` is a minimum.

    Two connections deliver, one has never delivered. The feed can serve the
    instruments it actually holds prices for — but it may not claim to be a live
    feed, because a third of the account has no data at all. This is D5.3's
    finding one layer along: the symbol-less resolution is what tells a user
    which tier they are on.
    """
    _registry, manager, baseline, feed, _clock = _sharded_feed()
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")

    assert feed.readiness is FeedReadiness.READY, "the feed cannot serve what it holds"
    assert feed.covers("A") and feed.covers("B")
    assert not feed.covers("C")
    assert not feed.has_fresh_evidence, "a silent connection was masked by its siblings"
    # The tier question — capability-scoped, no instrument — falls to the baseline.
    tier = manager.resolve(Capability.QUOTES, context=ResolutionContext(user_id="u1"))
    assert tier is baseline


def test_the_feed_is_fresh_only_when_every_connection_is():
    _registry, _manager, _baseline, feed, _clock = _sharded_feed()
    for shard in ("0", "1"):
        _deliver(feed, shard)
    assert not feed.has_fresh_evidence
    _deliver(feed, "2")
    assert feed.has_fresh_evidence


def test_a_middle_connection_failing_preserves_the_other_two():
    """§7, stated literally: A up, B up, C up, then B down.

    A's and C's instruments keep being answered from live data; B's fall to the
    baseline immediately rather than after the two-minute coverage backstop,
    because prices from a socket that no longer exists may not answer a quote.
    """
    registry, manager, baseline, feed, _clock = _sharded_feed()
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    _deliver(feed, "2", "C")
    assert feed.has_fresh_evidence

    run(feed.mark_link_down("socket closed", "1"))

    # Preserved: A and C still hold live prices and the feed still serves them.
    assert feed.covers("A") and feed.covers("C"), "healthy connections were blanked"
    assert run(feed.fetch_quote("A"))["price"] == pytest.approx(100.0)
    assert set(feed.covered_symbols) == {"A", "C"}
    assert feed.is_eligible_for(ResolutionContext(
        user_id="u1", symbol="A", capability=Capability.QUOTES))

    # Correctly represented: B's prices came off a socket that no longer exists,
    # so they are gone immediately rather than after the coverage backstop.
    assert not feed.covers("B"), "a dead connection's prices still answer quotes"
    assert not feed.is_eligible_for(ResolutionContext(
        user_id="u1", symbol="B", capability=Capability.QUOTES))
    assert not feed.has_fresh_evidence, "the loss of a connection was masked"

    # And the ranking consequence, pinned: the degraded feed is on probation, so
    # the steady baseline is preferred for every instrument — but the feed is
    # still in the chain and answers the moment nothing steadier remains, which
    # is what "probation ranks, never filters" means (LIM-D5.10-3).
    assert feed.is_on_probation
    assert _quote_provider(manager, symbol="A") is baseline
    registry.unregister(baseline.name)
    assert _quote_provider(manager, symbol="A") is feed
    assert not manager.resolve_feed(
        Capability.QUOTES,
        ResolutionContext(user_id="u1", symbol="B", capability=Capability.QUOTES),
    ).available


@pytest.mark.parametrize("lost", ["0", "1", "2"])
def test_first_middle_and_last_connection_failures_behave_identically(lost):
    """Position in the plan is not a special case anywhere."""
    _registry, _manager, _baseline, feed, _clock = _sharded_feed()
    for shard, symbol in (("0", "A"), ("1", "B"), ("2", "C")):
        _deliver(feed, shard, symbol)
    run(feed.mark_link_down("closed", lost))

    survivors = {"0": "A", "1": "B", "2": "C"}
    for shard, symbol in survivors.items():
        assert feed.covers(symbol) is (shard != lost)
    assert feed.readiness is FeedReadiness.READY


def test_losing_every_connection_demotes_the_feed():
    """The single-connection behaviour, reached by exhausting the plan."""
    _registry, manager, baseline, feed, _clock = _sharded_feed()
    for shard, symbol in (("0", "A"), ("1", "B"), ("2", "C")):
        _deliver(feed, shard, symbol)
    for shard in ("0", "1"):
        run(feed.mark_link_down("closed", shard))
    assert feed.readiness is FeedReadiness.READY
    run(feed.mark_link_down("closed", "2"))
    assert feed.readiness is FeedReadiness.FAILED
    assert not feed.covered_symbols
    assert _quote_provider(manager, symbol="A") is baseline


def test_a_partially_covered_feed_stays_on_probation():
    """The ranking-level expression of partial loss.

    A feed that has lost a connection still serves — probation ranks, it never
    filters (D5.2) — but it may not displace a provider that is steady. Every
    connection has to have served the window.
    """
    _registry, _manager, _baseline, feed, clock = _sharded_feed(shards=("0", "1"))
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    clock.advance(PROBATION_WINDOW_SECONDS + 1)
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    assert feed.stability is FeedStability.STABLE

    run(feed.mark_link_down("closed", "1"))
    assert feed.stability is FeedStability.PROBATION
    assert feed.is_on_probation
    # Ranked, not filtered: still eligible for what it covers.
    assert feed.is_eligible_for(ResolutionContext(
        user_id="u1", symbol="A", capability=Capability.QUOTES))


def test_a_reconnecting_connection_re_serves_the_window_and_its_siblings_do_not():
    """Two halves of one rule, and neither may be traded for the other.

    The connection that came back has proved nothing, so the *feed* is back on
    probation. Its siblings never dropped, so they keep the window they served —
    making them re-serve it would mean a feed with several connections could
    never be stable at all.
    """
    _registry, _manager, _baseline, feed, clock = _sharded_feed(shards=("0", "1"))
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    clock.advance(PROBATION_WINDOW_SECONDS + 1)
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    assert feed.stability is FeedStability.STABLE

    run(feed.mark_link_down("closed", "1"))
    run(feed.mark_link_up("1"))
    assert feed.stability is FeedStability.PROBATION

    # The sibling kept its window: one tick on the reconnected connection is not
    # enough, but a full window on it — with the sibling still delivering — is.
    _deliver(feed, "1", "B")
    assert feed.stability is FeedStability.PROBATION
    clock.advance(PROBATION_WINDOW_SECONDS + 1)
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    assert feed.stability is FeedStability.STABLE


def test_a_reconnect_inherits_no_readiness_probation_or_latency():
    """The D5.2/D5.3/D5.4 reset, applied to exactly one connection."""
    _registry, _manager, _baseline, feed, clock = _sharded_feed(shards=("0", "1"))
    _establish_latency(feed, clock, "0", FAST)
    _establish_latency(feed, clock, "1", FAST, symbol="B")
    assert feed.delivery_latency == pytest.approx(FAST)

    run(feed.mark_link_down("closed", "1"))
    run(feed.mark_link_up("1"))

    ledger = feed._shards["1"]
    assert ledger.ready_since is None, "a reconnect inherited its readiness instant"
    assert ledger.last_evidence_at is None, "a reconnect inherited freshness"
    assert not ledger.intervals, "a reconnect inherited latency samples"
    assert feed._shards["0"].intervals, "a sibling's latency was discarded"
    assert feed.delivery_latency is None, "the feed reported a latency it cannot support"


def test_shard_count_is_never_a_latency_advantage():
    """The mutation that would silently promote every sharded feed.

    Three connections each delivering every `SLOW` seconds interleave into a
    merged series of roughly `SLOW / 3`. Measured per connection — which is what
    a consumer actually waits for, since their instrument arrives on exactly one
    — the feed's latency is `SLOW`, and the feed does not become three times
    faster for having been split.
    """
    _registry, _manager, _baseline, feed, clock = _sharded_feed()
    # Interleave the three connections at a third of a slow cadence each, so a
    # merged series would read FAST-ish and a per-connection one reads SLOW.
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    _deliver(feed, "2", "C")
    for _ in range(LATENCY_TAIL_WINDOW_SAMPLES):
        for shard, symbol in (("0", "A"), ("1", "B"), ("2", "C")):
            clock.advance(SLOW / 3.0)
            _deliver(feed, shard, symbol)

    assert feed.delivery_latency == pytest.approx(SLOW)
    merged_would_be = SLOW / 3.0
    assert feed.delivery_latency > merged_would_be * 2


def test_the_feeds_latency_is_its_slowest_connections():
    """A quote is answered from one connection and nobody knows which in advance.

    So the only value that cannot overstate the feed is its worst connection's.
    The minimum would let one fast connection speak for a slow one and rank the
    feed above a steadier provider it does not beat.
    """
    _registry, _manager, _baseline, feed, clock = _sharded_feed(shards=("0", "1"))
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    # Connection 0 delivers four times for every one of connection 1's, so the
    # two have genuinely different cadences at the same instant — a first draft
    # of this test advanced the clock for both and made them identical, which is
    # exactly the shape that leaves a min/max mutation green.
    slow_gap = FAST * 4
    for _ in range(LATENCY_TAIL_WINDOW_SAMPLES):
        for _ in range(4):
            clock.advance(FAST)
            _deliver(feed, "0", "A")
        _deliver(feed, "1", "B")

    assert feed._percentile_over(LATENCY_WINDOW_SAMPLES, statistic="median") is not None
    per_shard = sorted(
        __import__("statistics").median(list(shard.intervals)[-LATENCY_WINDOW_SAMPLES:])
        for shard in feed._shards.values()
    )
    assert per_shard[0] == pytest.approx(FAST)
    assert per_shard[-1] == pytest.approx(slow_gap)
    assert feed.delivery_latency == pytest.approx(slow_gap), (
        "the feed reported its fastest connection's cadence, not its slowest")
    assert feed.delivery_latency != pytest.approx(FAST)


def test_latency_is_unestablished_until_every_connection_has_been_timed():
    """`None` is "not established", never "fast" (D5.4), and a feed with an
    untimed connection has not been timed."""
    _registry, _manager, _baseline, feed, clock = _sharded_feed(shards=("0", "1"))
    _establish_latency(feed, clock, "0", FAST)
    _deliver(feed, "1", "B")
    assert feed.delivery_latency is None
    assert feed.delivery_latency_p95 is None
    assert feed.latency_profile.samples == 0

    _establish_latency(feed, clock, "1", FAST, symbol="B")
    assert feed.delivery_latency is not None


def test_the_latency_profile_reports_the_least_warmed_connection():
    _registry, _manager, _baseline, feed, clock = _sharded_feed(shards=("0", "1"))
    _establish_latency(feed, clock, "0", FAST, samples=LATENCY_WINDOW_SAMPLES)
    _deliver(feed, "1", "B")
    clock.advance(FAST)
    _deliver(feed, "1", "B")
    assert feed.latency_profile.samples == 1


# ══════════════════════════════════════════════════════════════════
# §6 — the single-connection feed is byte-for-byte unaffected
# ══════════════════════════════════════════════════════════════════

def test_an_unsharded_feed_behaves_exactly_as_before():
    """Every aggregate over one connection is that connection's own value.

    The compatibility claim, driven through the whole lifecycle with no shard
    ever named — which is what every caller written before D5.10 does.
    """
    clock = FakeClock()
    feed = StreamingTickProvider("feed:u1", owner_user_id="u1", clock=clock)
    run(feed.connect())
    run(feed.subscribe(["A"]))
    run(feed.mark_link_up())
    assert feed.shard_count == 1
    assert feed.readiness is FeedReadiness.SUBSCRIBED

    run(feed.on_raw([_tick(symbol="A")]))
    assert feed.readiness is FeedReadiness.READY
    assert feed.has_fresh_evidence and feed.covers("A")
    assert feed.stability is FeedStability.PROBATION

    clock.advance(PROBATION_WINDOW_SECONDS + 1)
    run(feed.on_raw([_tick(symbol="A")]))
    assert feed.stability is FeedStability.STABLE

    run(feed.mark_link_down("closed"))
    assert feed.readiness is FeedReadiness.FAILED
    assert not feed.covered_symbols and not feed.has_fresh_evidence


def test_the_default_shard_constants_are_pinned_equal():
    """Two constants rather than an import, because the import is not allowed.

    The Market Engine may not import the broker layer, and a shard id is an
    opaque string to the provider exactly as a provider name is. So the pin is a
    test — which is the only thing that can fail if one of them moves.
    """
    assert DEFAULT_SHARD_ID == DEFAULT_FEED_SHARD


def test_an_undeclared_connection_cannot_widen_the_conjunction():
    """A batch from a plan that has already been replaced is refused.

    Filing it under a shard invented on arrival would let an unplanned
    connection join "every shard has fresh evidence" for the life of the feed,
    and nothing would ever clear it.
    """
    _registry, _manager, _baseline, feed, _clock = _sharded_feed(shards=("0", "1"))
    assert run(feed.on_raw([_tick(symbol="A")], "7")) == 0
    assert feed.shard_count == 2
    assert run(feed.mark_link_up("7")) is False
    assert run(feed.mark_link_down("gone", "7")) is False


def test_an_unnamed_connection_means_the_default_one_not_all_of_them():
    """`shard=None` has two possible meanings and only one of them is correct here.

    To `_discard_evidence` it means *every* connection — that is what
    `disconnect()` needs. To `mark_link_up` / `mark_link_down` / `on_raw` it
    means *this feed's only connection*, which is what every caller written
    before D5.10 means by saying nothing.

    A first draft let the unresolved `None` fall through from the second meaning
    into the first, so `mark_link_down()` on a three-connection feed blanked all
    three connections' prices while marking exactly one of them down — a feed
    left with no coverage and two sockets it still believed were up. Found by
    review rather than by a test, which is why there is now a test.
    """
    _registry, _manager, _baseline, feed, _clock = _sharded_feed()
    for shard, symbol in (("0", "A"), ("1", "B"), ("2", "C")):
        _deliver(feed, shard, symbol)

    run(feed.mark_link_down("closed"))

    assert set(feed.covered_symbols) == {"B", "C"}, (
        "an unnamed connection was read as every connection")
    assert feed._shards["0"].link_up is False
    assert feed._shards["1"].link_up is True and feed._shards["2"].link_up is True
    assert feed._shards["1"].last_evidence_at is not None

    # `disconnect()` is the caller that does mean every connection.
    run(feed.disconnect())
    assert not feed.covered_symbols
    assert not any(ledger.link_up for ledger in feed._shards.values())


def test_redeclaring_an_unchanged_plan_is_not_a_reconnect():
    """A reshard that did not touch a connection must not reset it."""
    _registry, _manager, _baseline, feed, clock = _sharded_feed(shards=("0", "1"))
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    clock.advance(PROBATION_WINDOW_SECONDS + 1)
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    assert feed.stability is FeedStability.STABLE

    feed.declare_shards(("0", "1"))
    assert feed.stability is FeedStability.STABLE, "an unchanged plan reset the feed"


def test_a_connection_dropped_from_the_plan_stops_answering():
    """Its prices describe a socket that is being closed."""
    _registry, _manager, _baseline, feed, _clock = _sharded_feed(shards=("0", "1"))
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    feed.declare_shards(("0",))
    assert feed.shard_count == 1
    assert feed.covers("A")
    assert not feed.covers("B")


# ══════════════════════════════════════════════════════════════════
# §14 — isolation matrix
# ══════════════════════════════════════════════════════════════════

def test_no_users_connections_can_affect_anothers():
    """Two users, same broker, several connections each.

    Structural rather than defended by a rule: one provider per account, one
    ledger per provider, and `owner_user_id` refuses a resolution for anybody
    else before any of this is reached.
    """
    clock = FakeClock()
    registry = ProviderRegistry()
    baseline = YahooPollingAdapter()
    registry.register(baseline)
    run(baseline.connect())
    feeds = {}
    for user in ("userA", "userB"):
        feed = StreamingTickProvider(f"feed:{user}", owner_user_id=user, clock=clock)
        feed.declare_shards(("0", "1"))
        registry.register(feed)
        run(feed.connect())
        run(feed.subscribe(["A", "B"]))
        for shard in ("0", "1"):
            run(feed.mark_link_up(shard))
        feeds[user] = feed
    manager = SourceManager(registry)
    # Both feeds serve a full window, so a resolution below is decided by
    # entitlement and coverage rather than by probation.
    for feed in feeds.values():
        _serve_window(feed, clock, ("0", "1"), {"0": "A", "1": "B"})
        assert feed.stability is FeedStability.STABLE

    run(feeds["userA"].mark_link_down("closed", "1"))

    assert not feeds["userA"].has_fresh_evidence
    assert feeds["userB"].has_fresh_evidence, "one user's connection loss reached another"
    assert feeds["userA"]._shards["0"].intervals is not feeds["userB"]._shards["0"].intervals
    assert _quote_provider(manager, "userB", "B") is feeds["userB"]
    assert _quote_provider(manager, "userA", "B") is baseline


def test_a_fictional_broker_shards_through_the_real_attach_seam():
    """The Developer Rule 9 check: a broker nobody wrote code for, sharded.

    Nova declares a two-instrument connection limit and nothing else. Five
    instruments become three connections, one provider, through the real engine
    path — with no line of D5.10 naming a broker.
    """
    from services.broker_engine import BrokerEngine
    from services.brokers.market_feed import feed_provider_name
    from services.brokers.stream import stream_manager

    engine = BrokerEngine()
    holdings = [{"symbol": s, "quantity": 1} for s in ("A", "B", "C", "D", "E")]
    started = []

    async def record(*args, **kwargs):
        started.append(kwargs)

    with _clean_provider_registry() as registry, nova_registered(_ShardingNova()):
        with patch.object(engine, "get_session", new=AsyncMock(return_value={"access_token": "t"})), \
                patch.object(stream_manager, "start_stream", new=record), \
                patch.object(stream_manager, "status", return_value=[]):
            run(engine.start_stream("u1", "nova", holdings=holdings, positions=[]))

        assert [k["shard"] for k in started] == ["0", "1", "2"]
        assert [list(k["instrument_tokens"]) for k in started] == [["A", "B"], ["C", "D"], ["E"]]
        provider = registry.get(feed_provider_name("u1", "nova"))
        assert provider is not None and provider.shard_count == 3


def test_a_tick_is_attributed_to_the_connection_it_arrived_on():
    """End to end: transport callback → engine → provider ledger.

    The three seams that carry the shard from the plan to the evidence are the
    tick callback, the link-state callback and `set_market_feed_link`. Every one
    of them defaults to the single connection, so a mutation that simply stops
    binding the shard is *silently* correct for an unsharded feed — and for a
    sharded one it files every connection's evidence under the first, which is
    the masking failure arriving through the back door.

    Driven through `BrokerEngine.start_stream`'s real callbacks rather than by
    calling the provider directly, because binding is the thing under test.
    """
    from services.broker_engine import BrokerEngine
    from services.brokers.market_feed import feed_provider_name
    from services.brokers.stream import stream_manager

    engine = BrokerEngine()
    holdings = [{"symbol": s, "quantity": 1} for s in ("A", "B", "C", "D")]
    opened = {}

    async def record(*args, **kwargs):
        opened[kwargs["shard"]] = kwargs

    with _clean_provider_registry() as registry, nova_registered(_ShardingNova()):
        with patch.object(engine, "get_session", new=AsyncMock(return_value={"access_token": "t"})), \
                patch.object(stream_manager, "start_stream", new=record), \
                patch.object(stream_manager, "status", return_value=[]):
            run(engine.start_stream("u1", "nova", holdings=holdings, positions=[]))

        provider = registry.get(feed_provider_name("u1", "nova"))
        assert set(opened) == {"0", "1"} and provider.shard_count == 2

        # Both connections report up through their own bound callback.
        for shard, kwargs in opened.items():
            run(kwargs["on_link_state"]("u1", "nova", True, "", "market"))
        assert all(ledger.link_up for ledger in provider._shards.values())

        # Only connection 1 delivers. Its evidence must land on connection 1.
        run(opened["1"]["on_tick"]("u1", "nova", [
            {"instrument_token": None, "symbol": "C", "last_price": 100.0}]))

        assert provider._shards["1"].last_evidence_at is not None
        assert provider._shards["0"].last_evidence_at is None, (
            "a connection's evidence was filed under a different one")
        assert not provider.has_fresh_evidence

        # And a link change on connection 0 alone must not touch connection 1.
        run(opened["1"]["on_tick"]("u1", "nova", [
            {"instrument_token": None, "symbol": "C", "last_price": 101.0}]))
        run(opened["0"]["on_link_state"]("u1", "nova", False, "socket closed", "market"))
        assert provider._shards["1"].last_evidence_at is not None, (
            "a link change on one connection discarded another's evidence")
        assert provider._shards["0"].link_up is False
        assert provider._shards["1"].link_up is True


# ══════════════════════════════════════════════════════════════════
# §10 — subscription updates and resharding
# ══════════════════════════════════════════════════════════════════

@contextlib.contextmanager
def _engine_with_manager():
    """A real `BrokerStreamManager` and an engine, with no socket opened.

    `BrokerStream.start` is replaced by a no-op that leaves the entry in the
    registry and reports it running, so the resharding decisions — which
    connection is reused, which is replaced, which is stopped — are exercised
    against the real registry rather than a mock of it.
    """
    from services.broker_engine import BrokerEngine
    from services.brokers import stream as stream_module

    manager = BrokerStreamManager()
    engine = BrokerEngine()

    class _NoSocketStream(BrokerStream):
        def start(self):
            self._task = object()
            return self._task

        @property
        def running(self):
            return self._task is not None

        async def stop(self):
            self._task = None

    with patch.object(stream_module, "BrokerStream", _NoSocketStream), \
            patch.object(stream_module, "stream_manager", manager), \
            patch("services.broker_engine.stream_manager", manager), \
            patch.object(engine, "get_session", new=AsyncMock(return_value={"access_token": "t"})):
        yield engine, manager


def _plan_of(manager, broker="nova", channel="market"):
    return {
        row["shard"]: tuple(manager.get(row["user_id"], broker, channel, row["shard"]).instrument_tokens)
        for row in manager.status() if row["channel"] == channel
    }


def _sync(engine, symbols):
    holdings = [{"symbol": s, "quantity": 1} for s in symbols]
    run(engine.start_stream("u1", "nova", holdings=holdings, positions=[]))


def test_the_initial_subscription_opens_one_connection_per_shard():
    with _engine_with_manager() as (engine, manager), _clean_provider_registry(), \
            nova_registered(_ShardingNova()):
        _sync(engine, ["A", "B", "C", "D", "E"])
        assert _plan_of(manager) == {"0": ("A", "B"), "1": ("C", "D"), "2": ("E",)}


def test_adding_instruments_leaves_the_unchanged_connections_alone():
    """Make-before-break at this layer, and the churn rule.

    Adding an instrument at the end changes exactly one connection's membership.
    The connections that did not change are not stopped, not restarted, and keep
    the readiness and probation they earned — so no instrument is ever left
    uncovered because the planner was rebuilding.
    """
    with _engine_with_manager() as (engine, manager), _clean_provider_registry(), \
            nova_registered(_ShardingNova()):
        _sync(engine, ["A", "B", "C"])
        kept = manager.get("u1", "nova", "market", "0")
        _sync(engine, ["A", "B", "C", "D"])
        assert _plan_of(manager) == {"0": ("A", "B"), "1": ("C", "D")}
        assert manager.get("u1", "nova", "market", "0") is kept, (
            "an unchanged connection was torn down and rebuilt")


def test_adding_enough_instruments_opens_another_connection():
    with _engine_with_manager() as (engine, manager), _clean_provider_registry(), \
            nova_registered(_ShardingNova()):
        _sync(engine, ["A", "B"])
        assert set(_plan_of(manager)) == {"0"}
        kept = manager.get("u1", "nova", "market", "0")
        _sync(engine, ["A", "B", "C"])
        assert set(_plan_of(manager)) == {"0", "1"}
        assert manager.get("u1", "nova", "market", "0") is kept


def test_removing_enough_instruments_collapses_the_extra_connection():
    """And the collapse happens *after* the survivors are in place."""
    with _engine_with_manager() as (engine, manager), _clean_provider_registry(), \
            nova_registered(_ShardingNova()):
        _sync(engine, ["A", "B", "C", "D", "E"])
        assert set(_plan_of(manager)) == {"0", "1", "2"}
        _sync(engine, ["A", "B"])
        assert _plan_of(manager) == {"0": ("A", "B")}
        assert manager.get("u1", "nova", "market", "1") is None
        assert manager.get("u1", "nova", "market", "2") is None


def test_resharding_never_drops_or_duplicates_an_instrument():
    """Driven across a range of sizes, which is where an off-by-one shows up."""
    with _engine_with_manager() as (engine, manager), _clean_provider_registry(), \
            nova_registered(_ShardingNova()):
        for size in (1, 2, 3, 4, 5, 4, 3, 7, 2, 6):
            symbols = [f"S{i}" for i in range(size)]
            _sync(engine, symbols)
            live = _plan_of(manager)
            flattened = [i for shard in sorted(live) for i in live[shard]]
            assert flattened == symbols, f"resharding to {size} lost or reordered instruments"
            assert len(set(flattened)) == size


def test_a_reconnect_of_one_connection_does_not_touch_the_others():
    """D5.6's re-probe, applied to a sharded channel.

    A connection that is not running is rebuilt; its running siblings are not
    asked anything. There is no second recovery ladder — this is the existing
    channel re-attach, and the reuse rule is what scopes it.
    """
    with _engine_with_manager() as (engine, manager), _clean_provider_registry(), \
            nova_registered(_ShardingNova()):
        _sync(engine, ["A", "B", "C", "D"])
        healthy = manager.get("u1", "nova", "market", "0")
        broken = manager.get("u1", "nova", "market", "1")
        run(broken.stop())

        _sync(engine, ["A", "B", "C", "D"])

        assert manager.get("u1", "nova", "market", "0") is healthy, (
            "a healthy connection was re-probed")
        assert manager.get("u1", "nova", "market", "1") is not broken
        assert manager.get("u1", "nova", "market", "1").running


def test_a_changed_session_rebuilds_every_connection():
    """An old socket authenticated with material the account no longer uses."""
    with _engine_with_manager() as (engine, manager), _clean_provider_registry(), \
            nova_registered(_ShardingNova()):
        _sync(engine, ["A", "B", "C"])
        before = {s: manager.get("u1", "nova", "market", s) for s in ("0", "1")}
        with patch.object(engine, "get_session", new=AsyncMock(return_value={"access_token": "new"})):
            _sync(engine, ["A", "B", "C"])
        for shard, stream in before.items():
            assert manager.get("u1", "nova", "market", shard) is not stream


# ══════════════════════════════════════════════════════════════════
# §11 — instrument identity is unchanged by a shard boundary
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("broker,identifiers", [
    ("zerodha", [738561, 2953217, 408065]),
    ("upstox", ["NSE_EQ|INE002A01018", "NSE_EQ|INE467B01029"]),
    ("angelone", ["1|2885", "1|11536", "3|500325"]),
    ("fyers", ["sf|10|101000000002885", "sf|10|101000000011536"]),
    ("dhan", ["NSE_EQ|2885", "NSE_FNO|43492"]),
])
def test_a_shard_boundary_never_changes_an_instruments_identity(broker, identifiers):
    """The planner slices a list; it does not touch what is in it.

    Each broker's identifiers come out of the plan `==` and `is`-identical to
    what went in, in the same order, whichever side of a boundary they land on.
    A planner that normalised, re-typed or re-cased an identifier would resolve a
    tick to the wrong company's holding with nothing raised.
    """
    plan = plan_shards(identifiers, max_instruments_per_connection=1)
    rebuilt = [i for shard in plan for i in shard.instruments]
    assert rebuilt == identifiers
    assert all(a is b for a, b in zip(rebuilt, identifiers))
    assert [type(i) for i in rebuilt] == [type(i) for i in identifiers]


def test_a_tick_resolves_the_same_whichever_connection_it_arrived_on():
    """Identity is settled below sharding, in `InstrumentMap`, and unchanged."""
    from services.brokers.instruments import InstrumentMap, canonical_ticks

    instrument_map = InstrumentMap.from_portfolio(
        [{"symbol": "RELIANCE", "exchange": "NSE", "instrument_token": 738561}], [])
    raw = [{"instrument_token": 738561, "last_price": 2650.0}]
    first = canonical_ticks(raw, instrument_map, broker="nova")
    second = canonical_ticks(raw, instrument_map, broker="nova")
    # Everything but the ingest instant, which is a wall-clock stamp taken at
    # the boundary and is expected to differ between two calls.
    for field in ("symbol", "price", "exchange", "volume"):
        assert first[0][field] == second[0][field]
    assert first[0]["symbol"] == "RELIANCE"


# ══════════════════════════════════════════════════════════════════
# §12 — security
# ══════════════════════════════════════════════════════════════════

#: Live-looking fakes. Nothing here is real, and nothing here may appear
#: anywhere a shard identifier does.
FAKE_CREDENTIALS = {
    "api_key": "nova_live_ak_9f3c1b7e42d0",
    "access_token": "nova_live_at_5a71e9c4d83f0b26",
}


def test_no_credential_reaches_a_shard_id_a_registry_key_or_a_log(caplog):
    """A full sharded lifecycle at DEBUG, with live-looking fake credentials.

    Everything a shard id touches is swept: the id itself, the transport
    registry key, the task name, the provider's own diagnostics, and every log
    line the lifecycle produced.
    """
    manager = BrokerStreamManager()
    plan = plan_shards(["A", "B", "C"], max_instruments_per_connection=2)
    with caplog.at_level(logging.DEBUG):
        for shard in plan:
            stream = BrokerStream(
                "u1", "nova", {"access_token": FAKE_CREDENTIALS["access_token"]},
                credentials=FAKE_CREDENTIALS,
                instrument_tokens=list(shard.instruments),
                channel="market", shard=shard.id,
            )
            manager._streams[("u1", "nova", "market", shard.id)] = stream
            assert stream.shard == shard.id

        feed = StreamingTickProvider("brokerfeed:nova:u1", owner_user_id="u1")
        feed.declare_shards(plan.ids)
        run(feed.connect())
        run(feed.subscribe(["A", "B", "C"]))
        for shard in plan:
            run(feed.mark_link_up(shard.id))
            run(feed.on_raw([_tick(symbol="A")], shard.id))
        run(feed.mark_link_down("closed", plan.ids[-1]))

    surfaces = (
        [str(sid) for sid in plan.ids]
        + [repr(key) for key in manager._streams]
        + [str(row) for row in manager.status()]
        + [str(feed.describe()), str(feed.health()), caplog.text]
    )
    for secret in FAKE_CREDENTIALS.values():
        for surface in surfaces:
            assert secret not in surface, f"a credential reached {surface[:80]!r}"


def test_no_shard_vocabulary_reaches_a_consumer_payload():
    """A shard id is implementation metadata; a consumer must never see one.

    Swept across the surfaces a consumer actually reads: a quote, the Source
    Manager's status, and the canonical tick itself. `describe()` is admin
    diagnostics and reports a *count*, which is deliberately not an identifier.
    """
    _registry, manager, _baseline, feed, _clock = _sharded_feed()
    for shard, symbol in (("0", "A"), ("1", "B"), ("2", "C")):
        _deliver(feed, shard, symbol)

    quote = run(feed.fetch_quote("A"))
    payloads = [str(quote), str(manager.status()),
                str(manager.status(context=ResolutionContext(user_id="u1")))]
    for payload in payloads:
        assert "shard" not in payload.lower(), f"shard vocabulary reached {payload[:120]!r}"
    assert "shard" not in str(feed.describe()).lower()
    assert feed.describe()["connections"] == 3


def test_the_shard_id_appears_in_exactly_three_kinds_of_place():
    """Stated as a structural fact so widening it is a deliberate act.

    A registry key, a task name, and a log line. None of the three is a payload,
    and the first two never leave the process.
    """
    source = (BACKEND / "services" / "brokers" / "stream.py").read_text()
    tree = ast.parse(source)
    uses = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "shard"
    ]
    assert uses, "the transport no longer carries a shard at all"
    # Nothing in the transport passes the shard to a consumer callback.
    for callback in ("on_tick", "on_order_update", "on_link_state", "on_expired",
                     "on_not_entitled"):
        assert f"self.{callback}(self.user_id, self.broker, self.shard" not in source
        assert "self.shard)" not in source.split(f"self.{callback}(")[-1][:200]


# ══════════════════════════════════════════════════════════════════
# §13 — falsification: mutations that must go RED
# ══════════════════════════════════════════════════════════════════

def test_an_off_by_one_shard_size_would_lose_an_instrument():
    """Mutation: `range(needed)` computed with `floor` instead of `ceil`."""
    import math

    instruments = list(range(11))
    real = plan_shards(instruments, max_instruments_per_connection=10)
    mutated_shard_count = math.floor(len(instruments) / 10)
    mutated = tuple(
        InstrumentShard(id=shard_id(i), instruments=tuple(instruments[i * 10:(i + 1) * 10]))
        for i in range(mutated_shard_count)
    )
    assert len(real) == 2 and sum(len(s) for s in real) == 11
    assert sum(len(s) for s in mutated) == 10, "the mutation did not take"
    assert real.shards != mutated


def test_a_healthy_connection_masking_a_dead_one_is_caught():
    """Mutation: `_last_evidence_at` taken as the MAX over connections.

    Applied by hand and asserted to change the answer, so the minimum is doing
    work rather than coinciding with the maximum.
    """
    _registry, _manager, _baseline, feed, clock = _sharded_feed(shards=("0", "1"))
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    clock.advance(1_000.0)
    _deliver(feed, "0", "A")

    assert not feed.has_fresh_evidence
    stamps = [s.last_evidence_at for s in feed._shards.values()]
    assert max(stamps) != min(stamps), "the mutation would be indistinguishable here"
    assert (clock.now - max(stamps)) <= feed.tick_max_age_seconds, (
        "with the maximum, the feed would have claimed to be fresh")


def test_one_connection_failing_may_not_demote_every_healthy_one():
    """The mutation in the other direction: link-down blanking the whole feed.

    Asserted as an outcome rather than a call count — with the healthy
    connections' prices discarded, a user whose instrument is on a working
    socket falls to the delayed baseline for no reason.
    """
    registry, manager, baseline, feed, _clock = _sharded_feed()
    for shard, symbol in (("0", "A"), ("1", "B"), ("2", "C")):
        _deliver(feed, shard, symbol)
    run(feed.mark_link_down("closed", "1"))

    # Asserted as coverage rather than as a resolution, because the resolution
    # is settled by probation (see LIM-D5.10-3). The mutation this catches is
    # `mark_link_down` discarding every connection's prices instead of the lost
    # connection's — under it, `covered_symbols` would be empty and the feed
    # could never answer for A or C again on this link.
    assert set(feed.covered_symbols) == {"A", "C"}, (
        "one connection's loss discarded a healthy one's prices")
    registry.unregister(baseline.name)
    assert _quote_provider(manager, symbol="A") is feed
    assert _quote_provider(manager, symbol="C") is feed


def test_sharing_one_ledger_across_providers_would_be_visible():
    """Mutation: `_ShardEvidence` held as a class attribute rather than per feed."""
    a = StreamingTickProvider("feed:a", owner_user_id="a")
    b = StreamingTickProvider("feed:b", owner_user_id="b")
    a.declare_shards(("0", "1"))
    b.declare_shards(("0", "1"))
    assert a._shards is not b._shards
    for shard in ("0", "1"):
        assert a._shards[shard] is not b._shards[shard]
        assert a._shards[shard].intervals is not b._shards[shard].intervals


def test_a_stale_connection_cannot_stay_covered_indefinitely():
    """D5.3's backstop, per connection.

    A connection that goes quiet loses its instruments to the coverage window
    even while its siblings keep the feed READY.
    """
    _registry, manager, baseline, feed, clock = _sharded_feed(shards=("0", "1"))
    _deliver(feed, "0", "A")
    _deliver(feed, "1", "B")
    clock.advance(feed.tick_max_age_seconds + 1)
    _deliver(feed, "0", "A")

    assert feed.covers("A")
    assert not feed.covers("B"), "a silent connection stayed covered"
    assert _quote_provider(manager, symbol="B") is baseline


def test_an_entitlement_refusal_does_not_retry_through_the_transport_ladder():
    """D5.5 is unchanged by sharding: the refusal is terminal for the channel.

    Every connection of the refused channel stops, none reconnects, and the
    account's other channels and other users are untouched.
    """
    from services.broker_engine import BrokerEngine
    from services.brokers.stream import stream_manager

    engine = BrokerEngine()
    stopped = []

    async def record_stop(user_id, broker, channel=None, shard=None):
        stopped.append((user_id, broker, channel, shard))

    with _clean_provider_registry(), nova_registered(_ShardingNova()), \
            patch.object(stream_manager, "discard", return_value=True) as discarded, \
            patch.object(stream_manager, "stop_stream", new=record_stop), \
            patch.object(engine, "_audit", new=AsyncMock()):
        run(engine._on_stream_not_entitled("u1", "nova", "market", shard="1"))

    discarded.assert_called_once_with("u1", "nova", "market", "1")
    assert stopped == [("u1", "nova", "market", None)], (
        "the refusal did not end every connection of the refused channel")


def test_an_expired_token_discards_only_the_reporting_connection():
    """Mutation: `discard` scoped to the channel would leak a sibling's task.

    Discarding a sibling here removes it from the registry *without* stopping
    it, so the `stop_stream` that follows can no longer find it and its task
    runs forever.
    """
    from services.broker_engine import BrokerEngine
    from services.brokers.stream import stream_manager

    engine = BrokerEngine()
    with _clean_provider_registry(), nova_registered(_ShardingNova()), \
            patch.object(stream_manager, "discard", return_value=True) as discarded, \
            patch.object(stream_manager, "stop_stream", new=AsyncMock()), \
            patch.object(engine, "_audit", new=AsyncMock()):
        run(engine._on_stream_expired("u1", "nova", "market", shard="2"))

    discarded.assert_called_once_with("u1", "nova", "market", "2")


def test_the_transport_registry_would_replace_a_sibling_without_the_shard_key():
    """Mutation: the D4.7 key kept, with no shard element.

    Two connections of one channel would collapse to one — half the account's
    instruments live, half gone, nothing raised. Exactly the failure D4.7 fixed
    one scope out.
    """
    manager = BrokerStreamManager()
    for shard in ("0", "1", "2"):
        manager._streams[("u1", "nova", "market", shard)] = object()
    assert len(manager._streams) == 3
    collapsed = {("u1", "nova", "market") for _ in manager._streams}
    assert len(collapsed) == 1, "the mutation did not take"


def test_stopping_a_channel_stops_every_one_of_its_connections():
    """`shard=None` means the whole channel — every pre-D5.10 caller's meaning."""
    manager = BrokerStreamManager()
    stopped = []

    class _Fake:
        def __init__(self, key):
            self.key = key

        async def stop(self):
            stopped.append(self.key)

    for shard in ("0", "1", "2"):
        manager._streams[("u1", "nova", "market", shard)] = _Fake(shard)
    manager._streams[("u1", "nova", "orders", "0")] = _Fake("orders")

    run(manager.stop_stream("u1", "nova", "market"))
    assert sorted(stopped) == ["0", "1", "2"]
    assert ("u1", "nova", "orders", "0") in manager._streams


# ══════════════════════════════════════════════════════════════════
# §9 — health semantics stay where their ADRs put them
# ══════════════════════════════════════════════════════════════════

def test_shard_health_never_reaches_the_shared_store():
    """D5.8's boundary is untouched: a live socket's health is process-local.

    A shard is a socket, and `health_is_shared` is already False for exactly the
    reason that applies here — publishing it would let a dead connection's
    verdict be inherited by a fresh one another worker opened.
    """
    feed = StreamingTickProvider("feed:u1", owner_user_id="u1")
    feed.declare_shards(("0", "1", "2"))
    assert feed.health_is_shared is False

    source = (BACKEND / "services" / "market_engine" / "source_manager.py").read_text()
    assert "shard" not in source.lower(), (
        "the Source Manager learned about shards — the provider is the unit it ranks")


def test_the_market_engine_still_knows_nothing_about_brokers_or_planning():
    """The dependency direction, re-asserted for the module D5.10 changed most."""
    source = (BACKEND / "services" / "market_engine" / "providers" / "streaming.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("services.brokers"), (
                f"the Market Engine imported {node.module}")
    assert "plan_shards" not in source, (
        "the provider learned how shards are planned — it is told, it does not decide")


def test_the_transport_still_names_no_broker_and_plans_nothing():
    """The D4.2 property, re-asserted for the module D5.10 added a key element to.

    Executable code only, via the same comment-stripping helper the D4.7 sweep
    uses — `stream.py`'s prose has always discussed the brokers whose behaviour
    forced each generalisation, and that is the documentation working rather
    than a leak.
    """
    source = (BACKEND / "services" / "brokers" / "stream.py").read_text(encoding="utf-8")
    stripped = _strip_source(source).lower()
    for broker in ("zerodha", "kite", "upstox", "angel", "smartapi", "fyers", "hsm", "dhan"):
        assert broker not in stripped, f"stream.py names {broker!r} in executable code"
    assert "plan_shards" not in source, "the transport learned to plan subscriptions"


def test_the_signature_widening_is_the_smallest_one_that_works():
    """Every widened signature keeps its pre-D5.10 meaning by defaulting.

    The compatibility claim, asserted rather than asserted-by-the-suite-passing:
    a caller that says nothing about shards gets the single-connection behaviour
    it had, at every seam D5.10 touched.
    """
    from services.brokers import market_feed

    for func, param in (
        (StreamingTickProvider.on_raw, "shard"),
        (StreamingTickProvider.mark_link_up, "shard"),
        (StreamingTickProvider.mark_link_down, "shard"),
        (market_feed.attach_market_feed, "shards"),
        (market_feed.set_market_feed_link, "shard"),
        (market_feed.publish_market_ticks, "shard"),
        (BrokerStream.__init__, "shard"),
        (BrokerStreamManager.start_stream, "shard"),
        (BrokerStreamManager.stop_stream, "shard"),
        (BrokerStreamManager.discard, "shard"),
    ):
        signature = inspect.signature(func)
        assert param in signature.parameters, f"{func.__qualname__} lost {param}"
        assert signature.parameters[param].default is not inspect.Parameter.empty, (
            f"{func.__qualname__}.{param} has no default — an existing caller would break")

    assert inspect.signature(BrokerStream.__init__).parameters["shard"].default == DEFAULT_SHARD_ID
    assert inspect.signature(BrokerStreamManager.stop_stream).parameters["shard"].default is None, (
        "stopping a channel must still mean stopping every one of its connections")


def test_the_channel_contract_did_not_grow_a_method():
    """Two class attributes, no new abstract method, no changed signature.

    A widened `subscribe_frames` or `decode` would have failed an unmigrated
    broker on a live socket rather than at import — the trade D4.7 and D4.10
    both refused, refused again here.
    """
    for method in ("endpoint", "open", "subscribe_frames", "connect_error", "decode"):
        parameters = list(inspect.signature(getattr(BrokerStreamChannel, method)).parameters)
        assert "shard" not in parameters, f"{method} was widened for sharding"
    assert DEFAULT_STREAM_CHANNEL == "default"

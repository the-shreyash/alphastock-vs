"""Instrument sharding — one logical subscription across several connections (D5.10).

WHAT THIS SOLVES, AND WHAT IT DELIBERATELY DOES NOT
----------------------------------------------------
Every streaming broker caps how many instruments one connection may carry, and
until this sprint every adapter answered an over-cap subscription the same way:
take a deterministic prefix, log a warning, and let the account's feed be
quietly narrower than its portfolio. That answer is defensible when the cap is
5,000 and a retail portfolio holds forty — it was recorded as a limitation in
D4.6, D4.7, D4.9, D4.10 and D4.11 for exactly that reason — but it is an answer
that gets worse as the platform grows, and it is not the answer the broker's own
API implies. Every one of these brokers permits *several* connections.

So this module turns "trim to fit one connection" into "use as many connections
as the instruments need", and it does it **below the canonical provider
boundary**. That distinction is the whole design:

    logical instrument set                 (what the account asked for)
            ↓  plan_shards()               THIS MODULE
    N broker-valid instrument batches
            ↓  one BrokerStream each       (stream.py, unchanged but for its key)
    N independent broker connections
            ↓  one canonical tick stream
    ONE StreamingTickProvider              (the unit everything above sees)

A shard is **not** a provider. It has no entry in the provider registry, it is
not ranked by the Source Manager, it earns no readiness of its own that a
consumer can observe, it never appears in a market event, and it never appears
in a consumer-facing payload. `MarketTick`, `InstrumentMap`, the Market Gateway,
the Source Manager, the provider registry, the fallback chain and the readiness
state machine are all untouched and unduplicated — which is the bar D5.10 was
set: prove the existing architecture can represent multiple broker connections
without growing a second market-data architecture.

WHY THE PLANNER IS HERE AND NOT IN AN ADAPTER
----------------------------------------------
Chunking a list is not broker knowledge. What *is* broker knowledge is the
number to chunk at, and that arrives as a broker-neutral capability the channel
declares (`BrokerStreamChannel.max_instruments_per_connection`) — the same shape
every other broker fact takes since D3. This module therefore names no broker,
reads no adapter, and is asserted to contain no broker name, exactly as
`stream.py` and `broker_engine.py` are.

The reverse split matters as much: **frame batching is not sharding.** Two of
the existing adapters already split one subscription across several *frames* on
one socket, because their brokers cap the size of a subscribe message. That is a
wire-format concern the codec owns and it is untouched here. Sharding splits
across *sockets*, and only a limit that is genuinely per connection can be
raised by opening another one. BROKER_INTEGRATION.md carries each broker's
numbers; this module carries none of them.

WHY A PER-CONNECTION LIMIT IS NOT INTERCHANGEABLE WITH A QUOTA
---------------------------------------------------------------
The audit that opened this sprint found the five existing adapters do not all
cap the same thing, and treating their numbers as equivalent would have made one
broker strictly worse:

* four adapters cap **instruments per connection**. Opening a second connection
  genuinely doubles what the account can subscribe to.
* one adapter's documented cap is a **per-session token quota**, counted across
  the client code rather than across the socket. Sharding it would open a second
  socket that the same quota refuses — spending one of that broker's scarce
  concurrent connections to subscribe to nothing, and turning a warning into a
  dead feed.

So a channel declares a per-connection limit only when its broker's limit is
genuinely per connection, and a channel that declares none is planned as exactly
one shard. `None` means "no *shardable* limit known", never "unlimited", and it
is the default: a broker that has never heard of sharding gets the single shard
it has always had, byte for byte.

A concurrent-connection ceiling is the second half of the same honesty.
Two of the five brokers document one, and one of them disconnects the *oldest*
connection when the ceiling is exceeded — so a planner that produced shards
without regard to it would knock out the shard it opened first, on every plan,
forever. `max_connections` caps the shard count, and instruments beyond what the
capped shards can hold are trimmed with a warning naming the numbers: the same
honest failure the adapters already had, reached only after the broker's real
capacity is exhausted rather than after one connection's.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: The shard id of a subscription that needs exactly one connection.
#:
#: Named rather than empty, for the reason `DEFAULT_STREAM_CHANNEL` is named:
#: every stream in the platform — sharded or not — is addressed the same way, as
#: `(user, broker, channel, shard)`. A key that is sometimes absent is a key
#: with two shapes, and the second one is always the one that gets missed.
#:
#: It is also what every pre-D5.10 caller gets by defaulting, which is what makes
#: this sprint invisible to a single-connection feed.
DEFAULT_SHARD_ID = "0"


def shard_id(index: int) -> str:
    """The stable id of the `index`-th shard of a plan.

    Positional and nothing else. A shard id reaches a registry key, a task name
    and a log line, so it carries no credential, no session, no symbol and no
    user identity — see the security section of ADR-050.
    """
    return str(int(index))


@dataclass(frozen=True)
class InstrumentShard:
    """One connection's worth of a logical subscription."""

    #: Position in the plan, as :func:`shard_id` renders it.
    id: str
    #: The broker's own opaque instrument identifiers, in plan order. This
    #: module never reads one — it counts them and hands them back, exactly as
    #: `BrokerStream` does.
    instruments: Tuple[Any, ...]

    def __len__(self) -> int:
        return len(self.instruments)


@dataclass(frozen=True)
class ShardPlan:
    """How a logical instrument set is spread over broker connections.

    Deterministic in the strong sense: the same instruments, limit and ceiling
    produce the same shard ids holding the same instruments in the same order,
    every time and in every process. Nothing here is random, hashed, or ordered
    by symbol — see :func:`plan_shards`.
    """

    shards: Tuple[InstrumentShard, ...]
    #: Instruments the broker's own concurrent-connection ceiling could not
    #: accommodate. Zero in every case except an account that exceeds the
    #: broker's entire documented capacity.
    dropped: int = 0
    #: The per-connection limit this plan was built against; `None` when the
    #: channel declares none.
    limit: Optional[int] = None

    @property
    def ids(self) -> Tuple[str, ...]:
        """The shard ids, in plan order — what the provider is told to expect."""
        return tuple(shard.id for shard in self.shards)

    @property
    def instrument_count(self) -> int:
        return sum(len(shard) for shard in self.shards)

    def __len__(self) -> int:
        return len(self.shards)

    def __iter__(self):
        return iter(self.shards)


def _deduplicate(instruments: Iterable[Any]) -> List[Any]:
    """The input with repeats removed, first occurrence winning.

    Duplicates are removed rather than rejected, and rather than left alone.

    Left alone, a repeated identifier would be subscribed twice — on one socket
    at best, and on *two different sockets* once a plan straddles a boundary,
    which is a duplicate wire subscription the broker bills against a limit and
    answers with two tick streams for one instrument. That would also inflate
    the count the planner divides by, so a subscription could be split across
    two connections when one would have held it.

    Rejected, a single repeated row in a portfolio sync would cost the account
    its entire feed. Every adapter already de-duplicates inside
    `stream_instruments`, so a repeat reaching here means an unusual caller
    rather than an unusable subscription.

    Order is preserved rather than sorted: the adapters sort already, and
    re-sorting here would impose one identifier's ordering (ints, strings,
    segment-qualified tuples) on identifiers that do not share a type. Sorting a
    mixed list is a `TypeError` on a live subscription.
    """
    seen = set()
    unique: List[Any] = []
    for instrument in instruments or ():
        try:
            marker = instrument if isinstance(instrument, (str, int, float, bool, tuple)) else repr(instrument)
        except Exception:  # pragma: no cover - defensive
            marker = id(instrument)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(instrument)
    return unique


def plan_shards(
    instruments: Optional[Sequence[Any]],
    *,
    max_instruments_per_connection: Optional[int] = None,
    max_connections: Optional[int] = None,
    broker: str = "",
    channel: str = "",
) -> ShardPlan:
    """Split a logical instrument set into broker-valid, connection-sized batches.

    Given `N` instruments and a per-connection limit `L`, produces `ceil(N / L)`
    shards of contiguous instruments in input order — the minimum number of
    connections that can carry the subscription, never more.

    The rules, each of which has a test and a mutation:

    * **empty input produces zero shards.** Not one empty shard: a connection
      with nothing subscribed on it delivers nothing, and opening one would
      register a link the provider would then wait forever to hear from.
    * **no declared limit produces exactly one shard** holding everything. This
      is the pre-D5.10 behaviour and it is the default, so a channel that has
      never heard of sharding is unaffected. A missing limit is never guessed
      at — an invented ceiling would shard a broker that does not need it and
      spend a connection the broker may not permit.
    * **exactly at the limit is one shard; the limit plus one is two.** The
      off-by-one that would otherwise cost an account its last instrument, or
      open a second connection for nothing.
    * **shard membership is contiguous and in input order**, so two equivalent
      subscriptions produce byte-identical plans. No hashing, no round-robin, no
      alphabetical grouping: an account whose plan changed shape between two
      identical syncs would reconnect every shard for no reason.
    * **duplicates are removed** before counting — see :func:`_deduplicate`.
    * **the concurrent-connection ceiling is respected.** Where `ceil(N / L)`
      exceeds `max_connections`, the plan is capped there and the tail is
      dropped with a warning naming both numbers, because a broker that is
      handed more connections than it permits does not refuse the extra one —
      one of these brokers silently disconnects the *oldest*, which would
      destroy the shard the plan opened first.

    `broker` and `channel` are for the log line only; they are names the platform
    already logs everywhere and carry no credential. Nothing in this function
    branches on either, and a test asserts that.
    """
    unique = _deduplicate(instruments)
    if not unique:
        return ShardPlan(shards=(), dropped=0, limit=max_instruments_per_connection)

    limit = max_instruments_per_connection
    if limit is None or int(limit) <= 0:
        # No *shardable* limit declared. One connection, everything on it —
        # which is exactly what every channel did before this sprint.
        return ShardPlan(
            shards=(InstrumentShard(id=shard_id(0), instruments=tuple(unique)),),
            dropped=0,
            limit=None,
        )

    limit = int(limit)
    needed = math.ceil(len(unique) / limit)
    dropped = 0
    if max_connections is not None and int(max_connections) > 0 and needed > int(max_connections):
        ceiling = int(max_connections)
        capacity = ceiling * limit
        dropped = len(unique) - capacity
        logger.warning(
            "%s %s subscription: %d instruments need %d connections but this broker permits %d — "
            "subscribing to the first %d and dropping %d",
            broker or "broker", channel or "feed",
            len(unique), needed, ceiling, capacity, dropped,
        )
        unique = unique[:capacity]
        needed = ceiling

    shards = tuple(
        InstrumentShard(id=shard_id(index), instruments=tuple(unique[index * limit:(index + 1) * limit]))
        for index in range(needed)
    )
    if len(shards) > 1:
        logger.info(
            "%s %s subscription: %d instruments sharded across %d connections (limit %d per connection)",
            broker or "broker", channel or "feed", len(unique), len(shards), limit,
        )
    return ShardPlan(shards=shards, dropped=dropped, limit=limit)

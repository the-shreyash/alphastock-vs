"""Streaming tick provider — the seam a pushed feed enters the platform through.

WHAT THIS IS
------------
A :class:`~services.market_engine.providers.base.MarketDataProvider` that is fed
by something else pushing into it, rather than by the gateway pulling from it.
It completes the chain MARKET_DATA_ARCHITECTURE.md has always specified and the
platform has never had::

    a pushed feed
          ↓  canonical MarketTick (services/market_engine/ticks.py)
    StreamingTickProvider.on_raw()          ← this module
          ↓  Market Gateway sink
    Market Gateway  →  Source Manager  →  Event Bus  →  Market Engine

WHY IT IS GENERIC AND NAMES NO FEED
------------------------------------
This module is in the Market Engine, which may not import the broker layer
(pinned by `test_the_market_engine_never_imports_a_broker_module`) and must be
able to resolve, rank and deliver a streaming feed without knowing that brokers
exist as a concept. That is not tidiness: it is what lets a broker WebSocket, a
licensed exchange feed and a future vendor feed be *the same kind of thing* to
the Source Manager, so priority ordering stays provider metadata instead of
becoming a chain of `if broker == …`.

So the construction direction is: the side that owns the feed builds one of
these, names it, sets `owner_user_id`, and registers it through the Market
Gateway. This module never reaches back. A second, entirely fictional feed
therefore needs zero lines here — which is what
`test_a_second_fictional_broker_uses_the_same_seam` exists to keep true.

WHY THERE IS NO NORMALIZER FAMILY FOR THE *PUSH* DIRECTION
-----------------------------------------------------------
Every other provider returns its own raw payload shape and the gateway
normalizes it, because a provider shape is the provider's business and the
platform's shape is the platform's. On the push side the two are already the
same object: what arrives is a :class:`~services.market_engine.ticks.MarketTick`,
the platform's own canonical tick, produced at the feed's own adapter boundary.

That is also why :meth:`StreamingTickProvider.on_raw` is strict about unknown
keys. Accepting a record with a field `MarketTick` does not define would mean
something upstream sent a *feed-shaped* payload rather than a canonical one, and
silently dropping the extra key would let that go unnoticed until the day the
extra key was the only identifier the record had. Rejecting it is what makes
"no raw feed payload reaches the Market Engine" a property of this boundary
instead of a habit of its callers.

The *pull* direction is different and does have a family: see
:meth:`StreamingTickProvider.fetch_quote` and `normalizer.py`'s `canonical`
family, which turns one canonical tick into the platform's StockQuote shape.

═══════════════════════════════════════════════════════════════════════
D4.5 — THE READINESS GATE, AND WHY QUOTES IS NOW DECLARED
═══════════════════════════════════════════════════════════════════════

D4.4 shipped this provider declaring `TICKS` and deliberately **not** `QUOTES`.
Its reasoning, quoted from that sprint, is still the reasoning here:

    Declaring QUOTES would make this provider outrank the polled baseline
    (priority 1 vs 3) for every quote request its owner makes, the moment it
    registered — which is the feed *switch*, performed without the
    make-before-break gate MARKET_DATA_ARCHITECTURE.md requires: connect the
    new provider, confirm first valid data, *then* release the old one.

D4.5 builds that gate, so the capability can be declared. The invariant it
enforces is the one that matters:

    **A provider does not become the primary quote source by declaring QUOTES.
    It becomes eligible by proving it can produce valid canonical data, and it
    stops being eligible the instant that stops being true.**

Declaring a capability and being resolvable for it are two different things, and
this class is where they come apart. `capabilities` says what this provider
could serve; :meth:`is_eligible_for` says whether it may serve it *now*.

THE STATE MODEL
---------------
Three distinctions, in increasing strength — the CONNECTED != READY != PRIMARY
line D4.5 exists to draw::

    REGISTERED   constructed; nothing has been established
    CONNECTING   `connect()` entered — a session is being established
    CONNECTED    a session exists.  NOT evidence that data will ever arrive.
    SUBSCRIBED   instruments have been requested.  Still not evidence.
    READY        a valid canonical tick has actually arrived on this link.
    FAILED       the link reported failure; evidence discarded
    DISCONNECTED the link is down; evidence discarded

**PRIMARY is deliberately not a state on this class.** Being primary is the
*outcome* of one resolution in the Source Manager — the head of the chain for a
given capability and context — and storing it here would create a second,
lagging copy of a fact the resolver already computes. Two providers could then
both believe they were primary, which is exactly the state
MARKET_DATA_ARCHITECTURE.md forbids for one quote stream. With promotion
expressed purely as "which provider does `resolve_feed` return", it cannot
happen: there is one function, it returns one head, and it recomputes from
current readiness every time. That is what makes promotion atomic without a
lock, a transaction, or a handover protocol.

WHAT COUNTS AS EVIDENCE
-----------------------
Exactly one thing: a record that survived :meth:`_coerce` into a canonical
:class:`MarketTick`, accepted while the link was up and instruments were
subscribed. Not "the socket opened", not "authentication succeeded", not "a
subscribe frame was sent", and not the passage of time — a sleep-based gate
would promote a feed that connected and then said nothing at all, which is the
precise failure mode a make-before-break switch exists to prevent.

Evidence is *per link*. A reconnect discards it: a feed that ticked once, died,
and came back has proved nothing about the new connection, and promoting it on
the strength of the old one would be promoting on a memory.

PER-SYMBOL COVERAGE, AND WHY A QUOTE NEEDS MORE THAN READINESS
---------------------------------------------------------------
Readiness makes the feed eligible for the quote capability at all. Coverage
decides *which instruments* it may answer for, and it is the mechanism behind
MARKET_DATA_ARCHITECTURE.md's rule that a feed covering NSE equities does not
disqualify the baseline from serving a US index that feed does not carry.

A quote needs a price, so the feed may answer for a symbol only if it holds a
recent tick for it. Two consequences, both wanted:

  * the baseline keeps serving every instrument the feed does not stream, in the
    same request, with no caller aware that two providers were involved;
  * a feed whose ticks stop being delivered stops covering anything within
    :data:`DEFAULT_TICK_MAX_AGE_SECONDS` and the baseline resumes — a backstop
    beneath the explicit link-down signal, evaluated lazily at resolution time
    with no timer and no poll loop.

═══════════════════════════════════════════════════════════════════════
D5.2 — PROBATION: READY IS NOT STABLE
═══════════════════════════════════════════════════════════════════════

D4.5's gate answers one question — *can this feed produce a valid canonical
price?* — and D5.2 exists because the platform was reading the answer as though
it were the answer to a second, stronger one: *should this feed be preferred
over a source that is already working?*

They come apart on a flapping link, and the failure is entirely mechanical::

    connect → one valid tick → READY → preferred
            → socket dies    → demoted, baseline resumes
            → reconnect      → one valid tick → READY → preferred
            → socket dies    → ...

Every individual step is correct. The composite is a feed whose user watches
their tier indicator alternate between live and delayed every few seconds, with
each promotion resting on a single packet from a connection that has repeatedly
demonstrated it cannot survive. D5.1 fixed the transport half of this (the
reconnect no longer comes back in 1.5s forever); the provider half is that
readiness alone should never have outranked a steady source.

So a second axis, orthogonal to readiness::

    READY / PROBATION   valid data is arriving, and that is all that is known
    READY / STABLE      valid data has kept arriving across the full window

See :class:`FeedStability` for the pair and :meth:`StreamingTickProvider.stability`
for the rule that separates them.

WHAT PROBATION IS AND IS NOT ALLOWED TO DO
-------------------------------------------
Probation is a **ranking** term, never an eligibility filter, and the whole
design turns on that distinction:

  * a probationary feed is still eligible, still in the failover chain, and
    still answers when nothing steadier remains — so probation can never be the
    reason a request returns no data at all;
  * a steady provider — including the polled baseline, which is the steady
    provider in the ordinary single-broker case — keeps the primary position
    while a competitor is on probation;
  * a probationary feed that loses its link loses its probation with it, so a
    reconnect serves the window again from the beginning.

The ranking itself lives in the Source Manager, where every other selection rule
already lives, and reads one generic property
(:attr:`MarketDataProvider.is_on_probation`). Nothing about it knows that this
provider is fed by a broker, and a provider that does not implement the
readiness gate reports False and is unaffected — the polled baseline included.

WHY NOT A SECOND STATE MACHINE, A TIMER, OR A STORED FLAG
----------------------------------------------------------
* **No second lifecycle.** Stability is a property of a feed that is already in
  the D4.5 state machine, computed from timestamps that machine already had to
  record. A parallel probation registry would be a second copy of provider
  membership to keep in step with the first.
* **No timer.** Nothing schedules a promotion. The window is evaluated at
  resolution time, so a feed nobody asks about costs nothing, and there is no
  scheduled callback to cancel when a link drops.
* **No stored "is stable" flag.** For the same reason PRIMARY is not a state
  (above): a stored flag is a lagging copy of something derivable, and the two
  disagree exactly when it matters. Stability is derived on every read.

═══════════════════════════════════════════════════════════════════════
D5.3 — STALE-FEED DEMOTION: STABLE IS NOT PERMANENT
═══════════════════════════════════════════════════════════════════════

D5.2 left one question open: stability did not decay. Auditing it produced two
findings, and they share a single cause — *the coverage window was only ever
asked per-instrument*::

    is_eligible_for(ctx with a symbol)  →  covers(symbol)  →  120s window ✓
    is_eligible_for(ctx with no symbol) →  return True     →  no window at all ✗

The second branch is the one `active_tier()`, `status()` and `source_tier()`
take. So a feed whose socket stayed up but whose data stopped kept winning that
resolution indefinitely, and the platform went on telling the user — and the AI —
`tier: streaming` while serving them nothing but baseline prices. Meanwhile
:meth:`StreamingTickProvider.stability` compared two past instants with no upper
bound, so the same dead feed still reported STABLE and still outranked a feed
that was genuinely delivering.

Both are closed by one predicate, :meth:`StreamingTickProvider.has_fresh_evidence`,
read in both places. What that buys is that D5.3 introduces **no new state, no
new constant, no new timer and no new registry** — the demotion is the coverage
rule the platform already published, finally asked in both of the places that
needed to ask it. See ADR-043.

The evidence is still, and only, an accepted canonical tick. Nothing here reads
the socket, the session, or how many times the link has flapped: transport
health and provider evidence stay the two separate facts D4.5 made them.

═══════════════════════════════════════════════════════════════════════
D5.4 — DELIVERY LATENCY, AND THE LATENCY THIS PLATFORM CANNOT MEASURE
═══════════════════════════════════════════════════════════════════════

MARKET_DATA_ARCHITECTURE.md has asked for latency scoring since Phase 5 was
written, and its own sentence carries the precondition that decides what is
possible here: "computes `latency_ms` **where the provider supplies an exchange
timestamp**". The D5.4 audit tested that precondition instead of assuming it,
and **no provider supplies one at this boundary**. A canonical tick has five
fields and none of them is an exchange instant — `ticks.py` states why, and the
reason is still good: brokers disagree on format and timezone, and a wrong
parse is worse than no parse. Below the canonical line the picture is worse
still: three of the five brokers put no timestamp on the wire at all in the
mode the platform subscribes, and the two that do use different units on an
exchange clock whose offset from ours has never been measured.

So `now − broker_timestamp` is not available, and manufacturing it would be a
number rather than a measurement. What *is* available, exactly and on one clock,
is how long a consumer of this feed waits between usable prices:

    delivery latency  =  median of the last LATENCY_WINDOW_SAMPLES intervals
                         between accepted canonical batches, on this
                         provider's own monotonic clock

Stated plainly because it will otherwise be assumed to mean the other thing:
**this is not exchange-to-ingest latency and must never be presented as such.**
It answers the question selection actually asks — of two feeds that are equally
healthy, equally fresh and equally proven, which one delivers sooner — and it is
the only latency question this platform can answer truthfully today. ADR-044
records the gap as LIM-D5.4-1.

Four properties follow from the shape of the statistic rather than from anything
this class remembers to do:

* **One outlier cannot demote a feed.** A median of nine tolerates four bad
  samples before the statistic itself moves, so a feed must be slow *most* of
  the time to be scored slow. A mean or an EWMA is moved arbitrarily far by one
  600-second gap, which is what a broker's midday hiccup looks like.
* **Old evidence expires, twice.** The window forgets by eviction — nine newer
  intervals remove every older one — and the whole score expires when the feed
  loses fresh evidence, reusing `tick_max_age_seconds` for the third time in
  three sprints rather than declaring a decay constant.
* **Nothing schedules anything.** Samples are produced only by arriving data.
* **A reconnect starts over**, in `_discard_evidence`, with the ticks and the
  probation timestamps that already reset there. Intervals measured on a link
  that no longer exists describe a connection the platform cannot ask anything
  of — and clearing `_last_evidence_at` also means the gap *spanning* a
  disconnection is never recorded as one enormous fictitious sample.

Latency creates nothing. It is the third element of a sort key applied to
candidates that have already survived entitlement, capability, health, readiness
and coverage; it cannot make a feed ready, cannot make one eligible, and ranks
*below* probation so it can never promote an unproven feed past a proven one.
And it cannot contradict D5.3's freshness predicate, because the two are read
off one series of arrival instants: freshness asks whether the *current* gap is
inside the coverage window, latency asks what the typical *completed* gap is.
That is LIM-D5.3-3's reconciliation, and it is stronger than a precedence rule
would have been.

WHAT A TICK-DERIVED QUOTE DOES AND DOES NOT CARRY
--------------------------------------------------
A canonical tick carries symbol, exchange, price, volume and an ingest
timestamp. It carries no previous close, so a quote derived from it has no
`change` / `change_pct`, and no OHLC. That is honest rather than convenient:
the platform states what the feed actually sent and fabricates nothing, which
CLAUDE.md's data rules require. Recorded as a known limitation in TASK.md — the
canonical tick grows those fields when a real feed that populates them lands,
not before, because a contract nothing populates is worse than an absent one.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from collections import deque
from dataclasses import fields as dataclass_fields
from enum import Enum
from typing import Any, Awaitable, Callable, Deque, Dict, Iterable, List, Optional, Tuple

from services.market_engine.providers.base import (
    Capability,
    LatencyProfile,
    MarketDataProvider,
    ProviderKind,
    ResolutionContext,
    SourceTier,
)
from services.market_engine.ticks import MarketTick, MarketTickError

logger = logging.getLogger(__name__)

#: Priority 1 in the Provider Priority Algorithm — above a licensed exchange
#: feed (2) and the polled baseline (3). A pushed feed is the freshest data the
#: platform can obtain, so it leads; the polled baseline remains the permanent
#: floor beneath it.
#:
#: Priority alone never promotes anything: a provider outranks the baseline only
#: among the candidates that survived eligibility, and this provider is not a
#: candidate for a quote until the readiness gate below has opened.
STREAMING_FEED_PRIORITY = 1

#: The exact field set a canonical tick record may carry. Read off the dataclass
#: rather than written out, so a field added to the canonical tick cannot be
#: rejected here by an out-of-date literal.
TICK_FIELDS = frozenset(f.name for f in dataclass_fields(MarketTick))

#: How old the newest tick for a symbol may be before this feed stops answering
#: quotes for it.
#:
#: The number is a judgement, and the direction of the judgement is what matters:
#: the baseline it falls back to is 15–60 seconds delayed, so answering from a
#: tick older than that is strictly worse than falling back, *and* it would be
#: labelled `streaming` while being older than the delayed tier. Two minutes
#: leaves room for a genuinely illiquid instrument that simply has not traded,
#: while keeping a silently dead link from serving yesterday's price as live.
DEFAULT_TICK_MAX_AGE_SECONDS = 120.0

#: How long a feed must keep delivering valid canonical data before it may
#: outrank a provider that is already serving reliably (D5.2).
#:
#: The number is not invented here and is not a second policy.
#: MARKET_DATA_ARCHITECTURE.md fixes the platform's definition of a provider
#: that has proved itself — "a provider that just recovered must deliver clean
#: data for a probation window (e.g. 30 seconds of valid messages) before it is
#: eligible to become primary again — this prevents flapping" — and D5.1's
#: transport ladder already reads the same sentence for
#: `STABLE_CONNECTION_SECONDS`.
#:
#: The two constants are separate *names* for one published policy because the
#: layers may not import each other: the transport's reliability module is
#: pinned to the standard library alone, and the Market Engine may not import
#: the broker layer at all. `test_the_two_layers_share_one_stability_window`
#: fails the moment they drift, which is the property that actually matters.
#:
#: What differs between the layers is the *evidence*, not the window, and that
#: difference is correct: the transport can only observe how long a socket
#: lasted, while this layer can observe whether data actually kept arriving on
#: it. A link that stays open for a silent minute is STABLE to the transport and
#: still on probation here — see :meth:`StreamingTickProvider.stability`.
PROBATION_WINDOW_SECONDS = 30.0

#: How many recent delivery intervals a feed's latency score is the median of —
#: and, because they are one question, how many it takes before that score
#: counts as established at all (D5.4).
#:
#: ONE CONSTANT WITH TWO USES, NOT TWO CONSTANTS
#: The deque's `maxlen` and the warm-up requirement are the same number because
#: they are the same rule: latency is established exactly when the window is
#: full. A separate warm-up threshold would be a second answer to one question,
#: free to drift from the first — the mistake ADR-043 spent a sprint not making.
#:
#: WHY NINE, AND WHY IT IS HONESTLY A NEW NUMBER
#: Unlike `PROBATION_WINDOW_SECONDS` and `tick_max_age_seconds`, this is not a
#: policy the platform had already published somewhere else, so there was
#: nothing to reuse and reaching for a health threshold because it is also
#: roughly this size would be a false economy dressed as consistency.
#:
#: The justification is a property of the statistic. A median of N tolerates
#: ⌊(N-1)/2⌋ outliers before the median itself becomes one, so at nine a feed
#: must be slow across a *majority* of recent deliveries to be scored slow —
#: which is the brief's "one outlier is not a permanent demotion" expressed as
#: arithmetic rather than as a hoped-for behaviour. Odd, so the median is an
#: observed interval rather than the average of two. Small enough that warm-up
#: costs a real feed a fraction of a second, and irrelevant at the pathological
#: end: a feed slow enough for nine intervals to take minutes is stale long
#: before then, and a stale feed has no latency score at all.
LATENCY_WINDOW_SAMPLES = 9

#: How many recent delivery intervals the feed retains, and therefore how many
#: the p95 is taken over and how many it takes before a p95 exists (D5.9).
#:
#: WHY THIS IS NOT ALSO NINE, WHICH IS THE WHOLE OF THE D5.9 STATISTICS PROBLEM
#: With the nearest-rank method (below), the p95 of N samples is the
#: ``ceil(0.95 * N)``-th smallest. For every N up to and including 19 that
#: expression equals N, so the "p95" *is the maximum* — a single worst delivery
#: becomes the whole statistic, which is precisely the one-outlier sensitivity
#: `LATENCY_WINDOW_SAMPLES` was chosen to avoid for the median. Reporting a
#: maximum under the name p95 would be the "precision it does not have" the D5.9
#: brief forbids, and it is why ADR-044 recorded p95 as unavailable rather than
#: computing one from nine samples.
#:
#: TWENTY IS DERIVED, NOT PICKED
#: ``ceil(0.95 * N) < N`` first holds at N = 20, where the p95 is the 19th of 20
#: — the second-largest observed interval, with exactly one worst sample
#: excluded. So 20 is the *smallest* sample size at which a p95 is a different
#: statistic from a maximum, and it is chosen for that and for nothing else.
#: Unlike ADR-047's 60 seconds this is not a judgement call: any smaller number
#: makes the statistic degenerate and any larger one buys tail resolution with
#: warm-up the platform has no evidence it needs.
#:
#: TWO WINDOWS, ONE SERIES
#: This is the deque's `maxlen`; `LATENCY_WINDOW_SAMPLES` is the newest slice of
#: the same deque. There is one recording site, one eviction rule, one reset and
#: one clock — a second series is what D5.9 rule 6 forbids, and none is created.
#: The two thresholds are not the drift hazard ADR-044 warned about, because
#: that hazard was *one* statistic with a maxlen and a separate warm-up free to
#: disagree; here each statistic's window is its own warm-up, exactly as before.
LATENCY_TAIL_WINDOW_SAMPLES = 20

#: The percentile the tail statistic reports.
LATENCY_TAIL_PERCENTILE = 0.95


#: The shard id of a feed whose whole subscription fits on one connection (D5.10).
#:
#: Every pre-D5.10 caller means this by saying nothing, which is what makes a
#: single-connection feed byte-for-byte unaffected by the shard ledger below:
#: with one shard every aggregate over shards is that shard's own value.
#:
#: WHY THIS IS DEFINED HERE AND NOT IMPORTED
#: The party that plans shards is the one that owns the wire, and it lives in
#: the broker layer — which this module may not import (Developer Rule 9's
#: dependency direction, pinned by
#: `test_the_market_engine_never_imports_a_broker_module`). A shard id is an
#: opaque string to this class in exactly the way a provider name is: it is
#: minted upstream, used as a dictionary key, and never parsed. The two
#: constants are pinned equal by a test rather than by an import, because the
#: import is the thing that is not allowed.
DEFAULT_FEED_SHARD = "0"


class _ShardEvidence:
    """What one broker connection of a feed has proved, on the link it holds now.

    THE D5.10 CHANGE, IN ONE OBJECT
    --------------------------------
    Before D5.10 this class held four scalars — link state, the instant
    readiness was earned, the instant valid data last arrived, and the recent
    delivery intervals — because a feed was one socket and a scalar was the
    whole truth. A sharded feed is several sockets that **fail independently**,
    and every one of those four facts is a fact about *one* of them:

    * a shard that drops must discard its own evidence and no one else's, or
      losing one connection would blank a feed that is still delivering most of
      the account's instruments (the D4.5 rule applied per link, which is what
      it always meant);
    * a shard that reconnects must re-earn readiness, re-serve probation and
      re-establish latency on the connection that actually exists, without
      inheriting anything — and without making its healthy siblings re-earn
      anything;
    * a shard that dies permanently must not be *masked* by its healthy
      siblings. This is the D5.3 defect one layer along: a provider whose
      `_last_evidence_at` was advanced by any shard would report itself live
      forever while a third of the portfolio had no socket at all.

    So the scalars became one of these per shard, and the provider's answers
    became aggregations over them. Which aggregation each answer uses is the
    substance of ADR-050 and is documented on each property.
    """

    __slots__ = ("link_up", "ready_since", "last_evidence_at", "intervals")

    def __init__(self) -> None:
        #: Whether this shard's transport reports a connection. Not readiness.
        self.link_up: bool = False
        #: When this shard first delivered valid canonical data on its current
        #: link — the instant its probation window opens.
        self.ready_since: Optional[float] = None
        #: When it last did. The freshness evidence, per D5.3.
        self.last_evidence_at: Optional[float] = None
        #: Gaps between accepted batches on this shard's current link (D5.4 /
        #: D5.9). Per shard rather than merged, because merging N shards each
        #: delivering every second produces intervals of 1/N and would score a
        #: wide subscription as fast — turning shard count into a latency
        #: advantage the feed has not earned. See `_percentile_over`.
        self.intervals: Deque[float] = deque(maxlen=LATENCY_TAIL_WINDOW_SAMPLES)

    def discard(self) -> None:
        """Forget everything this shard's current link produced.

        Called whenever this one connection drops. `link_up` is deliberately not
        touched here: it is set by the caller that knows which transition it is
        reporting, and a single owner for it is what keeps "the link came back"
        and "the evidence is gone" from being two half-applied facts.
        """
        self.ready_since = None
        self.last_evidence_at = None
        self.intervals.clear()


class FeedReadiness(str, Enum):
    """How far a pushed feed has got towards being usable.

    See the module docstring for what separates the members and why PRIMARY is
    not one of them.
    """

    REGISTERED = "registered"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    SUBSCRIBED = "subscribed"
    READY = "ready"
    FAILED = "failed"
    DISCONNECTED = "disconnected"


#: States in which the feed's link is considered up. `READY` is a strict
#: refinement of `SUBSCRIBED`, which is a strict refinement of `CONNECTED` — the
#: whole point of the enum — so link-level questions ask this set rather than
#: comparing to a single member and silently excluding the stronger states.
LINK_UP_STATES = frozenset({
    FeedReadiness.CONNECTED,
    FeedReadiness.SUBSCRIBED,
    FeedReadiness.READY,
})

#: Capabilities served by pushing. A feed is a legitimate answer for these while
#: its link is up, because nothing is *fetched* for them: the question they
#: answer is "is a live stream attached to this request", and a stream that has
#: not ticked yet is still attached. The quote capability is the one that
#: displaces the baseline, and it is gated on readiness instead.
LINK_LEVEL_CAPABILITIES = frozenset({Capability.TICKS, Capability.DEPTH})


class FeedStability(str, Enum):
    """Whether a feed has proved it is *reliable*, as opposed to merely valid.

    The second axis of a feed's state, orthogonal to :class:`FeedReadiness` and
    deliberately not folded into it. Readiness answers "can this feed produce a
    canonical price"; stability answers "has it kept doing so long enough to be
    trusted with the primary position". A feed is `READY / PROBATION` and then
    `READY / STABLE`; it is never stable without being ready, and the pair is
    what the D5.2 brief calls the composite state.

    Two members and no third. "Not applicable" was considered for a feed that is
    not ready at all and rejected: it would have to rank *somewhere*, and the
    only safe place to rank an unproven feed is with the other unproven ones.
    """

    #: Serving, or able to serve, but not yet entitled to displace a steady
    #: provider. Also the honest answer for a feed that is not ready at all.
    PROBATION = "probation"
    #: Has delivered valid canonical data across the full probation window on
    #: the current link, without losing it.
    STABLE = "stable"


#: Either axis of a feed's state, as it travels to the listener: a
#: :class:`FeedReadiness` or a :class:`FeedStability` member. Typed as `Any`
#: rather than as a union because the listener's contract is "something with a
#: `.value` that named the state before and after" — a third axis, if D5 ever
#: grows one, must not require every binder's signature to change.
FeedState = Any

#: Called with (provider, previous_state, new_state) on every state transition —
#: readiness (D4.5) and stability (D5.2) both, because the announcement is the
#: same announcement: a consumer's tier moved. Bound by the Market Gateway at
#: registration, for the same reason the tick sink is: a provider that chose its
#: own listener could announce a promotion around the gateway.
#:
#: One channel rather than two binders. The gateway's handler does not branch on
#: which axis moved — it logs the transition and republishes the owner's feed
#: status — so a second callback would be a second copy of one path, with the
#: standing risk that a later change is applied to one and not the other.
FeedStateListener = Callable[
    ["StreamingTickProvider", FeedState, FeedState], Awaitable[None]
]

#: Retained name for the D4.5 alias, which described the same callback when
#: readiness was the only axis there was.
ReadinessListener = FeedStateListener


class StreamingTickProvider(MarketDataProvider):
    """A market-data provider whose data is pushed into it as canonical ticks.

    One instance per feed connection. For a per-user feed that means one per
    (owner, feed) pair, with `owner_user_id` set — which is what makes
    :meth:`MarketDataProvider.is_eligible_for` refuse to serve it to anybody
    else, by construction rather than by every call site remembering to check.
    """

    kind = ProviderKind.STREAMING
    tier = SourceTier.STREAMING
    #: QUOTES is declared, and declaring it grants nothing on its own — see the
    #: readiness section of the module docstring. TICKS is what the feed serves
    #: from the moment its link is up.
    capabilities = frozenset({Capability.TICKS, Capability.QUOTES})
    normalizer_key = "canonical"
    priority = STREAMING_FEED_PRIORITY

    #: Never shared across workers (D5.8 / DB-1), and this is the sharpest
    #: instance of that sprint's "do not over-distribute" rule.
    #:
    #: Everything this class measures — readiness, the probation window, freshness
    #: evidence, delivery-latency intervals and the health streak that rides with
    #: them — is evidence about **one live socket held by one process**. No other
    #: worker holds that socket, no other worker has this provider registered, and
    #: `_reset_link_evidence` already discards all of it on reconnect because
    #: evidence from a dead link says nothing about a new one (D5.3).
    #:
    #: Publishing it would create the one failure DB-1 must not introduce: worker
    #: A's socket dies DOWN, worker B re-attaches the account, and the fresh link
    #: inherits a verdict about a socket that no longer exists — instead of
    #: earning READY and serving its probation window as D5.5/D5.6 require of a
    #: re-attached feed.
    health_is_shared = False

    def __init__(
        self,
        name: str,
        *,
        owner_user_id: Optional[str] = None,
        priority: int = STREAMING_FEED_PRIORITY,
        tick_max_age_seconds: float = DEFAULT_TICK_MAX_AGE_SECONDS,
        probation_seconds: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        name = (name or "").strip()
        if not name:
            raise ValueError("a streaming provider needs a stable name")
        self.name = name
        self.owner_user_id = str(owner_user_id) if owner_user_id else None
        self.priority = priority
        self.tick_max_age_seconds = float(tick_max_age_seconds)
        #: Read from the module constant at construction rather than captured as
        #: a default argument, so the published policy is what a feed built at
        #: runtime gets — and so a test can lower the window without reaching
        #: into an instance.
        self.probation_seconds = float(
            PROBATION_WINDOW_SECONDS if probation_seconds is None else probation_seconds
        )
        #: Monotonic and injectable, never wall-clock. Every duration this class
        #: measures — tick age, probation — is a duration, and a clock an NTP
        #: step can move backwards would promote a feed that had proved nothing.
        #: Injectable because a 30-second window is otherwise only testable by
        #: waiting 30 seconds, and a test that sleeps is a test nobody runs.
        self._clock = clock
        self._accepted = 0
        self._rejected = 0
        self._readiness = FeedReadiness.REGISTERED
        #: Newest tick per canonical symbol, with the monotonic instant it
        #: arrived and the shard that delivered it. Bounded by the subscribed
        #: instrument set, which the owner chose — this is not an unbounded
        #: cache of everything ever seen.
        #:
        #: The shard is carried (D5.10) so that a connection dropping discards
        #: exactly the instruments *it* was covering. Without it, one shard's
        #: loss would either blank the whole feed — throwing away prices from
        #: connections that never dropped — or leave its instruments answering
        #: quotes from a socket that no longer exists, which is the D4.5 rule
        #: this ledger exists to keep.
        self._last_tick: Dict[str, Tuple[MarketTick, float, str]] = {}
        #: What each of this feed's broker connections has proved (D5.10).
        #:
        #: One entry for an unsharded feed, and that entry holds exactly the
        #: four scalars this class carried before D5.10 — so every aggregate
        #: below reduces to the value it used to read, which is what makes a
        #: single-connection feed unaffected. See :class:`_ShardEvidence` and
        #: :meth:`declare_shards`.
        self._shards: Dict[str, _ShardEvidence] = {DEFAULT_FEED_SHARD: _ShardEvidence()}
        self._readiness_listener: Optional[FeedStateListener] = None
        self._last_failure: str = ""

    # ── Shards: the feed's broker connections (D5.10) ────

    def declare_shards(self, shard_ids: Iterable[str]) -> Tuple[str, ...]:
        """Say how many broker connections this feed is spread across.

        Called by whoever owns the wire, once, immediately after construction —
        the same party that supplies the subscription, and for the same reason:
        **a feed that never said what it is made of cannot be asked whether all
        of it is working.** Without a declaration, "every shard has fresh
        evidence" would be quantified over whichever connections happened to
        have delivered something, which is vacuously true of a feed whose second
        connection has never come up at all. That is precisely the "a healthy
        shard masks a dead shard" failure, and a declaration is what makes the
        conjunction mean something.

        An empty or absent declaration is the single default shard, which is
        what every feed built before D5.10 has and what an unsharded feed still
        is. Idempotent, and safe to call with the same ids twice; ids that are
        already declared keep their evidence, so re-declaring an unchanged plan
        is not a reconnect.

        Ids are opaque strings. This class never parses one, never orders by
        one, and never puts one in anything a consumer reads — see
        :meth:`describe`, which reports a *count*.
        """
        wanted = tuple(dict.fromkeys(str(sid) for sid in (shard_ids or ()) if str(sid).strip()))
        if not wanted:
            wanted = (DEFAULT_FEED_SHARD,)
        # Rebuilt as a fresh mapping in declaration order rather than mutated,
        # so a shard that left the plan cannot survive as a stale key that
        # "every shard" would then quantify over forever. Retained shards keep
        # the identical `_ShardEvidence` object: a reshard that did not touch a
        # connection must not make it re-earn readiness or re-serve probation.
        self._shards = {sid: self._shards.get(sid) or _ShardEvidence() for sid in wanted}
        # Prices delivered by a shard the plan has dropped describe a connection
        # that is being closed; they may not answer a quote, exactly as a dropped
        # link's may not.
        for symbol in [sym for sym, entry in self._last_tick.items() if entry[2] not in self._shards]:
            self._last_tick.pop(symbol, None)
        return wanted

    @property
    def shard_count(self) -> int:
        """How many broker connections this feed is spread across. One, usually."""
        return len(self._shards)

    def _shard(self, shard: Optional[str]) -> Optional[_ShardEvidence]:
        """The ledger for one connection, or None when it is not in the plan.

        `None` is not an error to this method and is not silently created. A
        record or a link transition naming an undeclared shard is a statement
        about a connection this provider was never told exists — a stale batch
        from a plan that has just been replaced, most likely — and registering
        it on arrival would let an unplanned connection widen the "every shard"
        conjunction for the life of the feed. The callers reject it and say so.
        """
        return self._shards.get(str(shard if shard is not None else DEFAULT_FEED_SHARD))

    # ── Readiness ────────────────────────────────────────

    @property
    def readiness(self) -> FeedReadiness:
        return self._readiness

    @property
    def _last_evidence_at(self) -> Optional[float]:
        """When the feed *as a whole* last had valid data on every connection.

        The **oldest** shard's last-evidence instant, and `None` if any declared
        shard has none at all (D5.10). Every property that reads it —
        :attr:`has_fresh_evidence`, :meth:`stability` — therefore answers about
        the whole subscription rather than about whichever connection ticked
        most recently, and neither of them needed a line changed.

        Why the minimum and not the maximum: the maximum is the mask. A feed
        whose second connection died an hour ago would report evidence from a
        second ago on the strength of its first, and the symbol-less resolution
        path — `active_tier()`, `status()`, the AI's freshness context — would
        tell a user their data is live while a third of their portfolio had no
        socket at all. That is the D5.3 defect exactly, arriving by a second
        route, and the minimum is the same answer D5.3 gave it.

        With one shard this is that shard's own timestamp, which is the scalar
        this attribute was before D5.10.
        """
        stamps = [shard.last_evidence_at for shard in self._shards.values()]
        if not stamps or any(stamp is None for stamp in stamps):
            return None
        return min(stamps)

    @property
    def _ready_since(self) -> Optional[float]:
        """When the feed as a whole began its current probation window.

        The **newest** shard's readiness instant, and `None` if any declared
        shard has not earned readiness on its current link. Paired with the
        minimum above, `stability`'s untouched `last_evidence - ready_since >=
        window` test becomes "every connection has been delivering valid data
        for a full window", which is the only reading of the published probation
        rule that a partially-covered feed can satisfy honestly.

        It is also what makes a reconnect cost the *provider* its probation
        without costing its healthy siblings theirs: the shard that came back
        clears its own `ready_since`, so this is `None` until it re-earns one,
        and then it is that shard's — the latest — so the window restarts from
        the reconnect rather than from whatever the oldest connection remembers.

        With one shard this is that shard's own timestamp, which is the scalar
        this attribute was before D5.10.
        """
        stamps = [shard.ready_since for shard in self._shards.values()]
        if not stamps or any(stamp is None for stamp in stamps):
            return None
        return max(stamps)

    @property
    def is_link_up(self) -> bool:
        """Whether a session exists. NOT a statement that data is arriving."""
        return self._connected and self._readiness in LINK_UP_STATES

    @property
    def is_ready(self) -> bool:
        """Whether this feed has proved it can produce valid canonical data.

        The gate. Distinct from health, which is evidence accumulated from past
        *calls*, and distinct from connectedness, which is evidence of nothing
        at all: a feed that authenticated, subscribed and then went silent is
        connected, healthy by every counter the platform keeps, and cannot
        serve a single price.
        """
        return self._connected and self._readiness is FeedReadiness.READY

    @property
    def has_fresh_evidence(self) -> bool:
        """Whether valid canonical data has arrived recently enough to count.

        The D5.3 predicate, and the *only* thing this class treats as current
        market-data evidence. It reads one timestamp — the instant the last
        accepted canonical tick arrived — and never the socket, the session, the
        subscription or a reconnect counter. That is deliberate: the whole point
        of the D4.5/D5.2 gate is that transport liveness and data evidence are
        different facts, and a predicate that consulted both would quietly undo
        it (see ADR-043, question E).

        The window is :attr:`tick_max_age_seconds`, reused rather than reinvented.
        The platform already publishes exactly one answer to "how old may this
        feed's data be before falling back is strictly better" — the per-symbol
        coverage window — and a second constant here would be a second staleness
        policy for one question, free to drift from the first.

        Equivalent to "at least one subscribed symbol is still covered", by
        construction: every accepted batch stamps its ticks and this timestamp
        with the same instant, so the newest entry in the coverage map is always
        exactly this old. Kept as a scalar because it is O(1) and because the
        *question* being asked here is about the feed, not about an instrument.
        """
        last_evidence = self._last_evidence_at
        if last_evidence is None:
            return False
        return (self._clock() - last_evidence) <= self.tick_max_age_seconds

    @property
    def delivery_latency(self) -> Optional[float]:
        """This feed's established delivery latency in seconds, or None (D5.4).

        The median of the last :data:`LATENCY_WINDOW_SAMPLES` intervals between
        accepted canonical batches, measured on this provider's own monotonic
        clock — see the D5.4 section of the module docstring for why that is the
        only latency this platform can state truthfully, and why it is *not*
        exchange-to-ingest latency.

        `None` means "not established", which is a different fact from "fast"
        and is never reported as a number. Three things establish it, and all
        must hold:

        * **The feed is ready.** The same gate `_fresh_tick` applies, for the
          same reason: an unready feed's data may not be used, so a statistic
          computed from it is not a measurement of anything the platform would
          act on. Without this, a feed that connected but never subscribed —
          which can never serve a quote — would still accumulate intervals and
          report a cadence on `describe()`, and would carry a finite sort key
          into the link-level (TICKS) comparison it *is* a candidate for.
        * **The window is full.** Rule 8 of the D5.4 brief — one lucky tick may
          not become a provider's score — enforced by there being no score at
          all until nine intervals have been observed. Note that a *pushed* feed
          accumulates these whether or not it is currently the primary, so
          unlike health there is no be-selected-to-improve cycle to deadlock on.
        * **The feed has fresh evidence.** A median assembled from gaps that all
          closed ten minutes ago is not a current measurement of anything, and
          reporting it as one would mislead whoever read it. This is also the
          second of the two independent reasons a stale feed can never be
          preferred on latency; the first is that staleness already puts it on
          probation, which ranks above this term.

        Read by :func:`services.market_engine.source_manager._selection_rank`
        through the provider contract, and surfaced on :meth:`describe` for
        diagnostics. It reaches no consumer payload and no market event.
        """
        return self._percentile_over(LATENCY_WINDOW_SAMPLES, statistic="median")

    @property
    def delivery_latency_p95(self) -> Optional[float]:
        """This feed's 95th-percentile delivery interval in seconds, or None (D5.9).

        The same series, the same clock and the same three establishment gates
        as :attr:`delivery_latency` — it differs only in taking the whole
        retained window rather than its newest slice, and in reporting a tail
        rather than a centre. Where the median answers "what does this feed
        usually cost a consumer", this answers "what does a bad delivery on this
        feed cost", which is the question an operator watching a feed actually
        has and which a median cannot be stretched to answer.

        `None` until `LATENCY_TAIL_WINDOW_SAMPLES` intervals have been observed
        on the current link — a longer warm-up than the median's, deliberately,
        because a tail statistic taken over too few samples is a maximum wearing
        a percentile's name. See `LATENCY_TAIL_WINDOW_SAMPLES` for why 20 is the
        smallest window at which that stops being true.

        **Reported, never ranked on.** It reaches :meth:`health` and
        :meth:`describe` and it does not appear in
        :func:`services.market_engine.source_manager._selection_rank` — ADR-049
        records why the selection metric stays the median.
        """
        return self._percentile_over(
            LATENCY_TAIL_WINDOW_SAMPLES, statistic="p95"
        )

    @property
    def latency_profile(self) -> LatencyProfile:
        """This feed's cadence as `health()` reports it (D5.9).

        Assembled from the two properties above rather than from the deque, so
        there is exactly one implementation of each statistic and of the gates
        that establish it. `samples` is the retained interval count — a size,
        never an instant, so no monotonic reading leaves this class.
        """
        p50 = self.delivery_latency
        return LatencyProfile(
            established=p50 is not None,
            p50_seconds=p50,
            p95_seconds=self.delivery_latency_p95,
            # The *least* warmed-up connection's sample count (D5.10), because
            # that is the one the establishment gate is still waiting for. The
            # maximum would report a full window while a statistic that needs
            # every shard is still `None`, which reads as a bug in the gate.
            # One shard makes this that shard's count, unchanged.
            samples=min((len(shard.intervals) for shard in self._shards.values()), default=0),
        )

    def _percentile_over(self, window: int, *, statistic: str) -> Optional[float]:
        """The `statistic` of the newest `window` delivery intervals, or None.

        One place where the three establishment gates are applied and one place
        where a window is sliced, so the median and the p95 cannot drift apart
        on either. The gates are D5.4's and are unchanged:

        * **the feed is ready** — an unready feed's data may not be used, so a
          statistic over it measures nothing the platform would act on;
        * **the window is full** — one lucky tick may not become a score, and
          each statistic's own window is its own warm-up;
        * **the evidence is fresh** — a percentile of gaps that all closed ten
          minutes ago is not a current measurement of anything.

        THE PERCENTILE METHOD IS NEAREST-RANK, AND IT IS PINNED
        For the tail: sort ascending, take the `ceil(p * N)`-th value
        (1-indexed). No interpolation, no averaging of neighbours, no
        distribution assumption. Two reasons, both the same reason ADR-044 chose
        an odd median window: the result is an interval this feed was actually
        observed to deliver rather than a number between two of them, and it is
        exactly reproducible from the retained samples, so a test can assert the
        value and not a tolerance. At N = 20 the index is 19, so the single
        worst sample is excluded and one catastrophic gap cannot become the
        reported tail.
        """
        if not self.is_ready:
            return None
        if not self.has_fresh_evidence:
            return None
        # D5.10 — ONE SERIES PER CONNECTION, AND THE WORST ONE IS THE ANSWER.
        #
        # Merging every shard's arrivals into one series would be the single
        # most dangerous thing this sprint could do to ranking. Three
        # connections each delivering once a second produce a merged inter-
        # arrival gap of a third of a second, so a feed would appear to get
        # three times faster for having been split — a latency advantage bought
        # by owning more sockets rather than by delivering any instrument
        # sooner. A consumer waits for *their* instrument, which arrives on
        # exactly one shard at that shard's own cadence, so the per-shard series
        # is the one that measures something real.
        #
        # Aggregated by maximum for the same reason: a quote is answered from
        # one connection and the platform cannot know in advance which, so the
        # only value that cannot overstate the feed is its slowest connection's.
        # The minimum would let one fast shard speak for a slow one and rank the
        # feed above a steadier provider it does not beat.
        #
        # Unestablished on any shard is unestablished for the feed. `None` is
        # "not established" rather than "fast" (D5.4), and a feed with a
        # connection nobody has timed yet has not been timed.
        per_shard: List[float] = []
        for shard in self._shards.values():
            samples = shard.intervals
            if len(samples) < window:
                return None
            recent = list(samples)[-window:]
            if statistic == "median":
                per_shard.append(statistics.median(recent))
            else:
                rank = math.ceil(LATENCY_TAIL_PERCENTILE * window)
                per_shard.append(sorted(recent)[rank - 1])
        if not per_shard:
            return None
        return max(per_shard)

    @property
    def stability(self) -> FeedStability:
        """Whether this feed has proved itself *reliable* on the current link.

        Derived from two timestamps and nothing else — the instant readiness was
        earned on this link, and the instant valid data last arrived on it. It is
        STABLE when the second is at least a full probation window after the
        first:

            valid data at t0  …  valid data still arriving at t0 + window

        which is the platform's published probation rule — "deliver clean data
        for a probation window (e.g. 30 seconds of valid messages)" — read
        literally. Three properties follow from reading it that way rather than
        as a timer:

        * **A silent feed never leaves probation.** One tick and then thirty
          seconds of nothing is not thirty seconds of valid messages, and a
          plain elapsed-time gate would promote exactly that — the same mistake
          as promoting on `connected`, one layer along.
        * **Nothing schedules anything.** No timer fires, no task polls; the
          window is evaluated at resolution time from values already recorded,
          so a feed nobody is asking about costs nothing.
        * **A reconnect starts over.** Losing the link discards the evidence and
          clears both timestamps, so probation is re-served on the connection
          that actually exists. A feed that flaps therefore never accumulates a
          claim to the primary position, which is the whole point of D5.2.

        Not ready is reported as PROBATION rather than as a third state: an
        unproven feed has to rank somewhere, and the only safe place to rank it
        is with the other unproven ones.

        D5.3 — STABILITY DECAYS, AND DECAYS THROUGH COVERAGE
        -----------------------------------------------------
        The rule above is a statement about two *past* instants, so on its own it
        has no upper bound: a feed that served its window and then went silent
        for an hour still satisfied it, and stayed STABLE — and therefore stayed
        preferred — on the strength of data nobody could still use. D5.3 closes
        that by adding the term that was missing, :attr:`has_fresh_evidence`, and
        closes it *with the coverage window rather than with a new mechanism*:
        stability is not a memory of what a feed once did, it is a claim about
        what it is doing, and a claim with no current evidence behind it is
        exactly what probation is for.

        There is no decay state, no decay constant and no decay timer. Staleness
        is the absence of fresh evidence, evaluated on read like everything else
        here, so a feed that goes quiet is demoted by the next resolution that
        asks, and a feed nobody asks about still costs nothing.

        Evidence that resumes on the *same* link restores STABLE immediately
        rather than re-serving the window. The link never dropped, so nothing was
        discarded and the window this feed proved is still the window of the
        connection it is still on; requiring it to be re-proved would mean an
        instrument that trades every few minutes could never be stable, which
        would demote honest feeds for being illiquid. A link that actually
        dropped is the other case, and `_discard_evidence` already makes that one
        start over.
        """
        if not self.is_ready:
            return FeedStability.PROBATION
        ready_since, last_evidence = self._ready_since, self._last_evidence_at
        if ready_since is None or last_evidence is None:
            return FeedStability.PROBATION
        if (last_evidence - ready_since) < self.probation_seconds:
            return FeedStability.PROBATION
        if not self.has_fresh_evidence:
            return FeedStability.PROBATION
        return FeedStability.STABLE

    @property
    def is_stable(self) -> bool:
        """Whether this feed may displace a provider that is already steady."""
        return self.stability is FeedStability.STABLE

    @property
    def is_on_probation(self) -> bool:
        """The Source Manager's ranking term — see :meth:`stability`.

        Note what this is *not*: an eligibility filter. A probationary feed is
        still a candidate, still in the failover chain, and still answers when
        nothing steadier remains. Probation decides who is preferred, never who
        may serve.
        """
        return self.stability is FeedStability.PROBATION

    def bind_readiness_listener(self, listener: Optional[FeedStateListener]) -> None:
        """Point this feed's state transitions at the Market Gateway.

        Both axes travel this one callback — readiness (D4.5) and stability
        (D5.2). Keeping the D4.5 method name is deliberate: the gateway binds by
        name, every existing caller uses it, and renaming a working seam to
        describe a widened payload would be churn charged to every one of them.

        Bound at registration and cleared at unregistration, exactly like
        :meth:`MarketDataProvider.bind_sink`. A promotion or demotion is a
        platform-level event, and announcing it is the gateway's job — Developer
        Rule 2 forbids anything reaching consumers around the gateway, and that
        applies to a status change as much as to a price.
        """
        self._readiness_listener = listener

    async def _advance(self, state: FeedReadiness, *, reason: str = "") -> bool:
        """Move to `state`, notifying the listener when it actually changed.

        Returns True on a real transition. Idempotent by design: a feed that
        reports its link up twice, or delivers a second tick after readiness,
        must not produce a second promotion — repeated lifecycle events are
        normal on a reconnecting transport and a duplicate promotion would put
        two identical status events on the bus for one fact.
        """
        if state is self._readiness:
            return False
        previous, self._readiness = self._readiness, state
        # D5.10 — the probation window is no longer opened here.
        #
        # It used to be stamped on the READY transition, from `_last_evidence_at`,
        # which was correct while a feed was one connection: the transition and
        # the tick that caused it were the same event. With several connections
        # they are not — the second shard's first tick opens *its* window without
        # moving the provider's readiness at all — so the stamp moved to the
        # shard that earned it (`on_raw`), and `_ready_since` reads the newest of
        # them. The value for a single-shard feed is identical: the tick that
        # earns readiness stamps `arrived_at`, which is exactly what
        # `_last_evidence_at` returned here.
        self._last_failure = reason if state is FeedReadiness.FAILED else ""
        logger.info(
            "Streaming provider %s readiness %s -> %s%s",
            self.name, previous.value, state.value,
            f" ({reason})" if reason else "",
        )
        listener = self._readiness_listener
        if listener is not None:
            await listener(self, previous, state)
        return True

    def _discard_evidence(self, shard: Optional[str] = None) -> None:
        """Forget every tick one connection produced — or every connection's.

        Called whenever a link drops. Readiness is evidence about *that*
        connection, and prices from a connection that no longer exists must not
        answer a quote — nor let a reconnected feed skip the gate on the
        strength of what the previous one sent.

        D5.2: this clears the probation evidence with it. A feed that served a
        full window, dropped, and came back has proved nothing about the new
        connection — inheriting the old link's window would hand a flapping feed
        the primary position it has just demonstrated it cannot hold.

        D5.4: intervals measured on a link that no longer exists describe a
        connection the platform cannot ask anything of — the same argument D4.5
        made for coverage and D5.2 for probation. Clearing the shard's evidence
        timestamp also disposes of a defect that would otherwise need its own
        guard: the gap *spanning* the disconnection is never recorded, because
        the first batch after a reconnect has no predecessor to measure against.

        D5.10 — SCOPED TO THE CONNECTION THAT DROPPED. `shard=None` still means
        every connection, which is what `disconnect()` means and what an
        unsharded feed's one link has always been. Naming a shard discards that
        shard's window, its intervals and *its* cached prices, and touches
        nothing its siblings proved: a feed of three connections that loses one
        must go on answering for the instruments the other two are still
        delivering, or one blip would blank a portfolio two working sockets are
        covering perfectly well.
        """
        if shard is None:
            for evidence in self._shards.values():
                evidence.discard()
            self._last_tick.clear()
            return
        evidence = self._shard(shard)
        if evidence is None:
            return
        evidence.discard()
        key = str(shard)
        for symbol in [sym for sym, entry in self._last_tick.items() if entry[2] == key]:
            self._last_tick.pop(symbol, None)

    # ── Lifecycle ────────────────────────────────────────

    async def connect(self) -> None:
        """Establish the platform-side session for this feed.

        Idempotent. The transport itself lives with whoever owns the feed; what
        happens here is that the provider becomes willing to receive. It is
        explicitly *not* readiness — see the module docstring.
        """
        if self.is_ready:
            # Idempotent in the strong sense: re-establishing a session that is
            # already delivering must not walk the gate backwards and demote a
            # feed that is serving perfectly well.
            return
        await self._advance(FeedReadiness.CONNECTING)
        await super().connect()
        await self._advance(
            FeedReadiness.SUBSCRIBED if self._subscribed else FeedReadiness.CONNECTED
        )

    async def disconnect(self) -> None:
        """Tear the session down. Idempotent."""
        await super().disconnect()
        self._discard_evidence()
        for evidence in self._shards.values():
            # Every connection of this feed is finished, not merely evidence-less
            # (D5.10). Leaving the flags set would let a later single-shard
            # link-down believe a sibling was still carrying the feed.
            evidence.link_up = False
        await self._advance(FeedReadiness.DISCONNECTED)

    async def mark_link_down(self, reason: str = "", shard: Optional[str] = None) -> bool:
        """The feed's transport reports one of its connections lost.

        The demotion half of make-before-break, and the reason failover here
        needs no polling: the side that owns the socket already knows the moment
        it dies, so it says so, and the next resolution — the very next one —
        ranks the baseline first again. Nothing waits for a health counter to
        escalate and nothing checks on a timer.

        Distinct from :meth:`disconnect` because the provider stays *registered*:
        a dropped socket that is reconnecting is not an ended entitlement, and
        unregistering on every blip would churn the registry and throw away the
        feed's diagnostics. It becomes un-resolvable, not absent.

        D5.10 — WHAT ONE SHARD'S LOSS DOES, AND WHAT IT MUST NOT DO
        ------------------------------------------------------------
        The connection that dropped discards its evidence and its prices, and
        the feed's *readiness* is only walked back when there is no connection
        left. That is the failure-isolation requirement read literally: a shard
        going down must preserve what its healthy siblings are delivering, so
        their instruments keep answering quotes and the feed is not blanked by a
        blip on a connection carrying a fifth of the account.

        It is emphatically not "one healthy shard means the feed is fine". Every
        provider-level claim tightens the moment a shard goes: the lost shard has
        no evidence, so `_last_evidence_at` — the minimum — is `None`,
        `has_fresh_evidence` is False, the symbol-less resolution that reports a
        user's tier stops answering, latency stops being established, and
        stability falls to PROBATION until the shard is back and has served a
        full window again. What survives is exactly the per-instrument coverage
        those two working sockets have actually earned, which is where partial
        coverage has lived since D4.5.
        """
        # Resolved once, here. `shard=None` means "this feed's only connection"
        # to every caller of this method — never "every connection", which is
        # what it means to `_discard_evidence` and what an unresolved `None`
        # reaching it would silently do: blank three connections' prices while
        # marking one of them down.
        shard = str(shard) if shard is not None else DEFAULT_FEED_SHARD
        evidence = self._shard(shard)
        if evidence is None:
            logger.warning(
                "Provider %s was told a connection it does not have is down — ignored", self.name)
            return False
        before = self.stability
        self._discard_evidence(shard)
        evidence.link_up = False
        if any(other.link_up for other in self._shards.values()):
            # Still connected somewhere. Readiness does not move — the feed can
            # still produce valid canonical data — but the aggregates above have
            # already tightened, so announce the stability those aggregates lost.
            await self._announce_stability(before)
            return False
        return await self._advance(
            FeedReadiness.FAILED if reason else FeedReadiness.DISCONNECTED,
            reason=reason,
        )

    async def mark_link_up(self, shard: Optional[str] = None) -> bool:
        """One of the feed's connections is established (or re-established).

        Never promotes: it moves the feed no further than CONNECTED/SUBSCRIBED,
        because "the socket is open" is the single most tempting and most wrong
        readiness signal there is. Readiness is re-earned by the next valid tick.

        D5.10 — A RECONNECT INHERITS NOTHING, AND COSTS ITS SIBLINGS NOTHING
        ---------------------------------------------------------------------
        The connection that came back discards its own evidence first, so it
        re-earns readiness, re-serves probation and re-establishes latency on the
        link that actually exists — the D5.2/D5.3/D5.4 reset, applied to the one
        connection it is about. Its siblings keep everything they proved: making
        two healthy sockets re-serve a probation window because a third
        reconnected would mean a feed with several connections could never be
        stable at all.

        The *provider's* readiness is only walked back to SUBSCRIBED when no
        other connection is currently delivering. A feed still serving prices
        from two live sockets is not demoted because a third came back — the
        provider-level aggregates already report the reconnecting shard as
        unproven, which is the honest statement, and demoting readiness on top of
        it would blank the instruments the other two are covering.
        """
        # Resolved once, here — see `mark_link_down` for why an unresolved
        # `None` must not reach `_discard_evidence`.
        shard = str(shard) if shard is not None else DEFAULT_FEED_SHARD
        evidence = self._shard(shard)
        if evidence is None:
            logger.warning(
                "Provider %s was told a connection it does not have is up — ignored", self.name)
            return False
        if not self._connected:
            await super().connect()
        before = self.stability
        self._discard_evidence(shard)
        evidence.link_up = True
        if self._readiness is FeedReadiness.READY and any(
            other.last_evidence_at is not None for other in self._shards.values()
        ):
            await self._announce_stability(before)
            return False
        # A feed already in READY is demoted back to SUBSCRIBED here, not left
        # alone: a link that came back is a *new* link, its predecessor's
        # evidence has just been discarded, and readiness must be re-earned on
        # the connection that actually exists.
        return await self._advance(
            FeedReadiness.SUBSCRIBED if self._subscribed else FeedReadiness.CONNECTED
        )

    # ── Subscription ─────────────────────────────────────

    async def subscribe(self, symbols: Iterable[str]) -> Tuple[str, ...]:
        """Request instruments, advancing the gate from CONNECTED to SUBSCRIBED.

        A feed with no subscription can never become ready, which is the correct
        default rather than an oversight: the owner of the feed is the only
        party that knows what it asked the wire for, so a feed that never said
        cannot claim to be delivering it.
        """
        active = await super().subscribe(symbols)
        if active and self._readiness is FeedReadiness.CONNECTED:
            await self._advance(FeedReadiness.SUBSCRIBED)
        return active

    async def unsubscribe(self, symbols: Iterable[str]) -> Tuple[str, ...]:
        remaining = await super().unsubscribe(symbols)
        for symbol in set(self._last_tick) - set(remaining):
            self._last_tick.pop(symbol, None)
        if not remaining and self._readiness in (FeedReadiness.SUBSCRIBED, FeedReadiness.READY):
            await self._advance(FeedReadiness.CONNECTED)
        return remaining

    # ── Entitlement, readiness and coverage ──────────────

    def is_eligible_for(self, context: ResolutionContext) -> bool:
        """Whether this feed may serve `context` — the whole gate, in one place.

        Three filters, strongest last:

        1. **Entitlement.** A per-user feed is legally that user's own data
           (MARKET_DATA_ARCHITECTURE.md, Category 2) and may never be resolved
           for anybody else. Inherited unchanged from the base class, so a
           failure of the switching logic cannot widen it.
        2. **Link, for the pushed capabilities.** TICKS and DEPTH are answered
           by a stream existing, and a stream with a live link exists.
        3. **Readiness and coverage, for everything else** — which today means
           the quote capability, the one that displaces the baseline. The feed
           must have proved itself on this link *and* hold a recent price for
           the instrument being asked about.

        A context with no capability (diagnostics, `entitled_for`) is treated as
        the link-level question: it is asking whether this feed applies to the
        request at all, not who wins it.
        """
        if not super().is_eligible_for(context):
            return False
        if context.capability is None or context.capability in LINK_LEVEL_CAPABILITIES:
            return self.is_link_up
        if not self.is_ready:
            return False
        if context.symbol:
            return self.covers(context.symbol)
        # A capability-scoped request with no instrument is not a request for
        # data — every fetch path in the gateway supplies the symbol it is
        # asking about. What reaches here with no symbol is the *reporting*
        # question: `status()`, `active_tier()`, `diagnostics()` asking which
        # feed this user is on. A ready feed *that is still receiving data* is
        # the honest answer to that, and answering `False` while data is
        # arriving would report the baseline's tier to a user whose data is
        # genuinely live.
        #
        # Per-instrument truth is not lost by this: every quote that actually
        # leaves the gateway is stamped with the tier of the provider that
        # answered *it*, so an instrument the feed does not stream is still
        # labelled delayed on the payload the consumer receives.
        #
        # D5.3 — WHY THIS IS NOT `return True`
        # Until D5.3 it was, and that made the 120-second coverage backstop
        # *per-symbol only*: it fired for `covers(symbol)` above and had no
        # counterpart on this branch. A feed whose link stayed up but whose data
        # stopped was therefore filtered out of every real quote — correctly —
        # while still winning this resolution, so `active_tier()` and `status()`
        # went on reporting `streaming` to a user who had not received a price in
        # hours. The tier indicator and the AI's freshness context both read that
        # path, which made it a claim about live data the platform could not
        # support (CLAUDE.md data rules), not merely a ranking blemish.
        #
        # The same window governs both branches now, which is the point: one
        # staleness policy, asked per-instrument where an instrument was named
        # and per-feed where none was.
        return self.has_fresh_evidence

    def covers(self, symbol: Any) -> bool:
        """Whether this feed holds a usable, recent price for `symbol`."""
        return self._fresh_tick(symbol) is not None

    @property
    def covered_symbols(self) -> Tuple[str, ...]:
        """Symbols this feed can currently answer a quote for."""
        return tuple(sorted(s for s in self._last_tick if self._fresh_tick(s) is not None))

    def _fresh_tick(self, symbol: Any) -> Optional[MarketTick]:
        if not self.is_ready:
            return None
        key = str(symbol or "").strip().upper()
        entry = self._last_tick.get(key)
        if entry is None:
            return None
        tick, arrived_at, _shard = entry
        if (self._clock() - arrived_at) > self.tick_max_age_seconds:
            return None
        return tick

    # ── Pull surface ─────────────────────────────────────

    async def fetch_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """The last streamed price for `symbol`, in this provider's raw shape.

        Raw here *is* the canonical tick — `normalizer.py`'s `canonical` family
        turns it into the platform's StockQuote. Nothing is computed, inferred
        or filled in: the fields a tick does not carry stay absent rather than
        being reconstructed from a stale reference (see the module docstring).

        Raises when the feed cannot answer, rather than returning nothing.
        Resolution filters this provider out for a symbol it does not cover, so
        reaching here means a caller bypassed resolution — and an empty return
        would end the request with no quote at all, because the gateway
        advances its failover chain on an exception and deliberately not on an
        empty result. Raising is what hands the request to the baseline.
        """
        tick = self._fresh_tick(symbol)
        if tick is None:
            raise self._unsupported(Capability.QUOTES)
        return tick.as_dict()

    # ── Push surface ─────────────────────────────────────

    async def on_raw(self, payload: Any, shard: Optional[str] = None) -> int:
        """Accept one pushed payload — a canonical tick, or a batch of them.

        Returns how many records were accepted. Nothing raises: a feed frame is
        a batch, and one unusable record must not cost the rest of the batch
        their prices nor drop a live connection. That is the same discipline the
        canonical boundary one layer down already applies, for the same reason.

        A batch that yields nothing usable emits nothing at all, rather than an
        empty delivery. The gateway then has one shape for "nothing arrived"
        instead of two.

        This is also the only place readiness is *earned* (D4.5). A batch in
        which every record was rejected is not evidence — a feed delivering a
        shape this boundary does not recognise has demonstrated the opposite of
        readiness — so the gate stays shut and the baseline keeps the quote.

        `shard` names which of this feed's connections delivered the batch
        (D5.10). Omitting it means the feed's only connection, which is what
        every caller written before D5.10 means and what an unsharded feed is.
        A batch naming a connection this provider was never told about is
        refused rather than filed under a shard invented on arrival — it
        describes a socket from a subscription plan that has already been
        replaced, and admitting it would let an unplanned connection widen the
        "every shard" conjunction for the life of the feed.
        """
        shard = str(shard) if shard is not None else DEFAULT_FEED_SHARD
        evidence = self._shard(shard)
        if evidence is None:
            logger.warning(
                "Provider %s was pushed a batch from a connection it does not have — dropped",
                self.name,
            )
            return 0
        records = payload if isinstance(payload, (list, tuple)) else [payload]
        ticks: List[MarketTick] = []
        rejected = 0

        for record in records:
            try:
                ticks.append(self._coerce(record))
            except MarketTickError as exc:
                rejected += 1
                logger.warning("Provider %s rejected a pushed record: %s", self.name, exc)

        self._accepted += len(ticks)
        self._rejected += rejected

        if not ticks:
            if rejected:
                # Loud, because a feed whose every record is rejected looks
                # exactly like a quiet market from outside and means the
                # opposite.
                logger.error(
                    "Provider %s accepted none of %d pushed records — the feed is delivering "
                    "a shape this boundary does not recognise",
                    self.name,
                    rejected,
                )
            return 0

        before = self.stability
        arrived_at = self._clock()
        self._record_delivery_interval(evidence, arrived_at)
        for tick in ticks:
            # Tagged with the connection that delivered it, so that connection
            # dropping discards exactly these prices and no others (D5.10).
            self._last_tick[tick.symbol] = (tick, arrived_at, shard)
        evidence.last_evidence_at = arrived_at
        if evidence.ready_since is None:
            # This connection's probation window opens at the tick that earned
            # its readiness, not at the instant a transition was processed —
            # the D5.2 rule, now stamped per connection because a sharded feed's
            # connections earn readiness at different moments. Stamped once and
            # never re-stamped: re-stamping would restart the window on every
            # arrival and no feed would ever leave probation.
            evidence.ready_since = arrived_at

        await self._earn_readiness()
        await self._announce_stability(before)
        await self._emit(ticks)
        return len(ticks)

    def _record_delivery_interval(self, evidence: "_ShardEvidence", arrived_at: float) -> None:
        """Record how long this batch made a consumer wait, on its own connection (D5.4).

        One sample per accepted *batch*, not per tick: a batch is one delivery,
        and every tick in it is stamped with the same arrival instant, so
        counting them individually would record eight intervals of zero for one
        frame carrying eight instruments and score a wide subscription as fast.

        Called before `_last_evidence_at` is advanced, because the value it is
        about to replace is the other end of the interval being measured. The
        first batch on a link produces no sample at all — there is nothing to
        measure against — which is exactly the behaviour a reconnect needs.

        A negative interval is dropped rather than recorded or clamped. The
        clock is monotonic, so it cannot happen in production; if it does, the
        clock is not what this class was told it was, and a negative number is
        not a fast delivery.
        """
        # Measured against THIS connection's previous arrival (D5.10), never
        # against the feed's. Interleaving several connections into one series
        # measures how often *any* socket spoke, which shrinks with shard count
        # and describes nothing a consumer waits for. See `_percentile_over`.
        previous = evidence.last_evidence_at
        if previous is None:
            return
        interval = arrived_at - previous
        if interval < 0:
            logger.warning(
                "Provider %s saw its clock move backwards — delivery interval dropped",
                self.name,
            )
            return
        evidence.intervals.append(interval)

    async def _announce_stability(self, previous: FeedStability) -> None:
        """Tell the gateway when this batch was the one that ended probation.

        Announced for the same reason readiness is: leaving probation moves the
        owner's tier from delayed to live, and a consumer that is never told
        keeps rendering a tier that is no longer true. Nothing else happens here
        — resolution recomputes stability from the timestamps on every request,
        so the switch has already taken effect by the time this runs.

        Announced in both directions since D5.10, and only from callers that
        did *not* also move readiness. Before sharding, losing stability always
        accompanied a readiness transition — which announces itself — so a second
        event here would have been one fact reported twice. One connection of a
        sharded feed dropping is the case that breaks that: the feed stays READY
        because its siblings are still delivering, so nothing else announces the
        probation the provider has just fallen back into, and a consumer would
        go on rendering a stable tier for a feed that has lost part of its
        coverage. The callers that do move readiness return before reaching
        here, so the "one fact, one event" property is unchanged.
        """
        current = self.stability
        if current is previous:
            return
        if current is FeedStability.STABLE:
            logger.info(
                "Streaming provider %s left probation after %.0fs of valid data",
                self.name, self.probation_seconds,
            )
        else:
            logger.info(
                "Streaming provider %s returned to probation — not every connection "
                "is delivering valid data",
                self.name,
            )
        listener = self._readiness_listener
        if listener is not None:
            await listener(self, previous, current)

    async def _earn_readiness(self) -> None:
        """Promote to READY on valid data, if the link and subscription allow it.

        The promotion is one assignment and it is atomic with respect to
        resolution for the same reason the demotion is: the Source Manager reads
        readiness at resolve time and never caches it, so the request before
        this transition resolves to the baseline and the request after resolves
        to this feed, with no interval in which both are the primary quote
        source.

        A feed whose link is down, or which never declared a subscription, is
        not promoted however good its data looks — a record arriving on a dead
        link is a record from a link the platform cannot ask anything of.
        """
        if self._readiness is FeedReadiness.SUBSCRIBED and self._connected:
            await self._advance(FeedReadiness.READY)

    def _coerce(self, record: Any) -> MarketTick:
        """One pushed record → a canonical tick, or :class:`MarketTickError`.

        Already-canonical instances pass through; dicts are rebuilt field by
        field from the closed set. An unrecognised field is refused rather than
        dropped — see the module docstring.
        """
        if isinstance(record, MarketTick):
            return record
        if not isinstance(record, dict):
            raise MarketTickError(f"pushed record is {type(record).__name__}, not a canonical tick")

        unknown = sorted(set(record) - TICK_FIELDS)
        if unknown:
            raise MarketTickError(
                f"pushed record carries non-canonical field(s) {unknown} — "
                "only canonical market ticks may cross this boundary"
            )

        ingested_at = record.get("ingested_at")
        fields: Dict[str, Any] = {
            "symbol": record.get("symbol"),
            "price": record.get("price"),
            "exchange": record.get("exchange"),
            "volume": record.get("volume"),
        }
        if isinstance(ingested_at, str) and ingested_at.strip():
            fields["ingested_at"] = ingested_at
        try:
            return MarketTick(**fields)
        except MarketTickError:
            raise
        except (TypeError, ValueError) as exc:
            raise MarketTickError(f"pushed record is not a usable tick: {exc}")

    # ── Introspection ────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        return {
            **super().describe(),
            "accepted_records": self._accepted,
            "rejected_records": self._rejected,
            "readiness": self._readiness.value,
            "stability": self.stability.value,
            "covered_symbols": len(self.covered_symbols),
            # D5.10 — a COUNT, never an id. How many broker connections this
            # feed is spread across is an operational fact an admin diagnostic
            # may state; which connection carried which price is implementation
            # metadata that reaches nothing, here or anywhere else. A shard id
            # appears in exactly three places — a registry key, a task name and
            # a log line — and none of them is a payload (ADR-050).
            "connections": self.shard_count,
            # D5.9. The p50 is already on the base payload as
            # `delivery_latency_seconds`; this is the tail beside it, `None`
            # until the wider window fills. The full three-state picture with
            # the sample count travels inside `health`.
            "delivery_latency_p95_seconds": self.delivery_latency_p95,
            "last_failure": self._last_failure,
        }

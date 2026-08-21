"""Streaming tick provider — the seam a pushed feed enters the platform through (D4.4).

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
Gateway. This module never reaches back. A second, entirely fictional broker
therefore needs zero lines here — which is what
`test_a_second_fictional_broker_uses_the_same_seam` exists to keep true.

WHY IT DECLARES `TICKS` AND NOT `QUOTES` (the D4.4 scope line)
--------------------------------------------------------------
Declaring QUOTES would make this provider outrank the polled baseline (priority
1 vs 3) for
every quote request its owner makes, the moment it registered — which is the
feed *switch*, and a switch performed without the make-before-break gate
MARKET_DATA_ARCHITECTURE.md requires: connect the new provider, confirm first
valid data, *then* release the old one. Registering the provider and switching
the feed onto it are two separable pieces of work, and D4.4 is deliberately only
the first. Until the second lands, this provider answers the TICKS capability —
which no provider has ever served, so nothing is taken away from anybody — and
the baseline continues to serve every quote for every user exactly as before.

WHY THERE IS NO NORMALIZER FAMILY
----------------------------------
Every other provider returns its own raw payload shape and the gateway
normalizes it, because a provider shape is the provider's business and the
platform's shape is the platform's. Here the two are already the same object:
what arrives is a :class:`~services.market_engine.ticks.MarketTick`, the
platform's own canonical tick, produced at the feed's own adapter boundary. A
normalizer would have nothing to convert. `normalizer_key` says exactly that
rather than naming a family in `normalizer.py` that does not exist.

That is also why :meth:`StreamingTickProvider.on_raw` is strict about unknown
keys. Accepting a record with a field `MarketTick` does not define would mean
something upstream sent a *feed-shaped* payload rather than a canonical one, and
silently dropping the extra key would let that go unnoticed until the day the
extra key was the only identifier the record had. Rejecting it is what makes
"no raw feed payload reaches the Market Engine" a property of this boundary
instead of a habit of its callers.
"""

from __future__ import annotations

import logging
from dataclasses import fields as dataclass_fields
from typing import Any, Dict, List, Optional

from services.market_engine.providers.base import (
    Capability,
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
STREAMING_FEED_PRIORITY = 1

#: The exact field set a canonical tick record may carry. Read off the dataclass
#: rather than written out, so a field added to the canonical tick cannot be
#: rejected here by an out-of-date literal.
TICK_FIELDS = frozenset(f.name for f in dataclass_fields(MarketTick))


class StreamingTickProvider(MarketDataProvider):
    """A market-data provider whose data is pushed into it as canonical ticks.

    One instance per feed connection. For a per-user feed that means one per
    (owner, feed) pair, with `owner_user_id` set — which is what makes
    :meth:`MarketDataProvider.is_eligible_for` refuse to serve it to anybody
    else, by construction rather than by every call site remembering to check.
    """

    kind = ProviderKind.STREAMING
    tier = SourceTier.STREAMING
    capabilities = frozenset({Capability.TICKS})
    normalizer_key = "canonical"  # see the module docstring — no family exists
    priority = STREAMING_FEED_PRIORITY

    def __init__(
        self,
        name: str,
        *,
        owner_user_id: Optional[str] = None,
        priority: int = STREAMING_FEED_PRIORITY,
    ) -> None:
        super().__init__()
        name = (name or "").strip()
        if not name:
            raise ValueError("a streaming provider needs a stable name")
        self.name = name
        self.owner_user_id = str(owner_user_id) if owner_user_id else None
        self.priority = priority
        self._accepted = 0
        self._rejected = 0

    # ── Readiness ────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        """A pushed feed can serve only while its connection is up.

        Distinct from health on purpose: a provider registered one millisecond
        ago whose socket has not opened yet has no failures to its name and is
        still unusable. Health answers "has this provider been working"; this
        answers "can it work right now", and only the second one can be true of
        something that has never been asked anything.
        """
        return self._connected

    def is_eligible_for(self, context: ResolutionContext) -> bool:
        """Entitlement *and* liveness — the override `base.py` anticipated.

        A feed whose connection has dropped is still entitled and is not usable,
        and resolving it anyway would hand a request to a provider that can only
        answer with silence while a healthy tier sat below it.
        """
        return super().is_eligible_for(context) and self.is_ready

    # ── Push surface ─────────────────────────────────────

    async def on_raw(self, payload: Any) -> int:
        """Accept one pushed payload — a canonical tick, or a batch of them.

        Returns how many records were accepted. Nothing raises: a feed frame is
        a batch, and one unusable record must not cost the rest of the batch
        their prices nor drop a live connection. That is the same discipline the
        canonical boundary one layer down already applies, for the same reason.

        A batch that yields nothing usable emits nothing at all, rather than an
        empty delivery. The gateway then has one shape for "nothing arrived"
        instead of two.
        """
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

        await self._emit(ticks)
        return len(ticks)

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
        }

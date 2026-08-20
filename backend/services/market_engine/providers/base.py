"""Provider Adapter contract — the only place provider-specific code may live.

MARKET_DATA_ARCHITECTURE.md is authoritative for this boundary. Its Developer
Rule 9 states the whole point of this module: adding a provider must be

    new adapter + new normalizer + registry entry. Nothing else.

If a future provider forces a change to the Market Engine, the Event Bus, the AI
layer, or the frontend, the design has been breached and the breach — not the
symptom — is what gets fixed.

WHAT AN ADAPTER IS
------------------
An adapter speaks exactly one provider's protocol and returns that provider's
*raw* payload shape. It does not normalize, does not cache into the platform
cache, does not touch the Event Bus, Redis, the database, or the Market Engine,
and contains no business logic (no scanner rules, no P&L maths, no alerting).
The Market Gateway owns all of that, which keeps normalization in one testable
place instead of one per provider.

WHY THE FETCH SURFACE IS CAPABILITY-GATED
-----------------------------------------
Providers differ enormously in coverage: Yahoo carries global indices and
commodities but no order-book depth; a broker WebSocket carries tick-level
depth for NSE/BSE instruments but knows nothing about the FTSE. Rather than
force every adapter to implement every method — which produces a wall of stub
methods that lie about what a provider can do — each adapter *declares* its
capabilities and implements only those. Every unimplemented method raises
:class:`CapabilityUnavailable`, and the Source Manager filters candidates by
capability before ever calling one (MARKET_DATA_ARCHITECTURE.md, "Resolution
procedure", step 3). A provider that cannot serve a symbol universe simply
falls through to the next one for that universe.

WHY THERE IS NO `subscribe()` / `on_raw()` IN D1
------------------------------------------------
The target contract in MARKET_DATA_ARCHITECTURE.md includes a push surface
(`subscribe`, `unsubscribe`, `on_raw`) for streaming providers. D1 ships one
provider — Yahoo, which is request/response only — and no consumer capable of
receiving pushed ticks. Defining that surface now would mean writing plumbing
that nothing implements and nothing calls, which is the "do not over-engineer
future providers" instruction in the D1 brief and dead code by any measure. The
push surface arrives in D3 with the first broker WebSocket adapter, alongside
the consumer that needs it. :attr:`MarketDataProvider.kind` already distinguishes
the two families so nothing above this layer has to be rewritten when it lands.
See ADR-028 in DECISIONS.md.
"""
from __future__ import annotations

import logging
from abc import ABC
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SourceTier(str, Enum):
    """Freshness tier — the ONLY provenance any consumer below the Source
    Manager may see.

    MARKET_DATA_ARCHITECTURE.md deliberately replaces the provider name with
    this value on every event that leaves the gateway: the frontend may render
    "Live" or "Delayed", and the AI may calibrate its language ("live price" vs
    "as of 10:42 AM"), but neither may ever learn *who* produced a quote. The
    real provider id survives only in gateway logs and internal metrics.
    """

    STREAMING = "streaming"
    DELAYED = "delayed"


class ProviderKind(str, Enum):
    """How data physically arrives from the provider.

    Distinct from :class:`SourceTier`, which is what consumers are told. A
    licensed exchange feed and a broker WebSocket are both STREAMING/streaming,
    but the distinction matters *inside* the gateway: polling providers are
    driven by a poll loop and have no push surface, streaming providers hold a
    persistent connection whose silence is itself a health signal.
    """

    POLLING = "polling"
    STREAMING = "streaming"


class Capability(str, Enum):
    """A unit of market data a provider can serve.

    Each member corresponds 1:1 to a `fetch_*` method on
    :class:`MarketDataProvider`, so the Source Manager can resolve a provider
    for a request without knowing anything about the provider itself.
    """

    QUOTES = "quotes"                    # single-instrument quote
    UNIVERSE_QUOTES = "universe_quotes"  # batched quotes for the tracked universe
    INDICES = "indices"                  # index levels + market overview
    SECTORS = "sectors"                  # sector performance
    MOVERS = "movers"                    # gainers / losers
    GLOBAL_MARKETS = "global_markets"    # non-Indian indices
    COMMODITIES = "commodities"          # commodities + forex
    OHLC = "ohlc"                        # candles / chart series
    SEARCH = "search"                    # instrument search

    # Declared but not served by any D1 provider. Present because the Source
    # Manager must be able to *resolve nothing* for them rather than have call
    # sites invent a provider — a broker adapter (D3) fills them in.
    TICKS = "ticks"
    DEPTH = "depth"


class ProviderState(str, Enum):
    """Health state of a single provider connection.

    UP        serving normally
    DEGRADED  failing intermittently — still a candidate, ranked lower
    DOWN      failing consistently — filtered out of resolution entirely
    """

    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


class CapabilityUnavailable(RuntimeError):
    """Raised when a provider is asked for data it never declared it could serve.

    This is a programming error, not a runtime condition: the Source Manager
    filters by capability before resolving, so reaching this means a call site
    bypassed resolution and reached for an adapter directly.
    """


#: Consecutive failures before a provider is considered degraded, then down.
#: Two thresholds rather than one because a single blip must not cost a provider
#: its primary slot — MARKET_DATA_ARCHITECTURE.md's flap-suppression concern in
#: its simplest useful form. Full latency scoring and probation windows are
#: Phase 5 of that document; D1 needs only "stop asking a provider that is
#: consistently answering with errors".
DEGRADED_AFTER_FAILURES = 3
DOWN_AFTER_FAILURES = 8


@dataclass
class ProviderHealth:
    """Rolling health of one provider, owned by the adapter, read by the
    Source Manager.

    Deliberately counter-based rather than time-window based: a time window
    needs a clock source, a background sweeper, and tests that manipulate time,
    all to answer a question ("is this provider answering?") that consecutive
    failure counts already answer correctly for a polled provider.
    """

    state: ProviderState = ProviderState.UP
    consecutive_failures: int = 0
    total_calls: int = 0
    total_errors: int = 0
    total_empty: int = 0
    last_success_at: Optional[str] = None
    last_error_at: Optional[str] = None
    last_error_class: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "total_calls": self.total_calls,
            "total_errors": self.total_errors,
            "total_empty": self.total_empty,
            "last_success_at": self.last_success_at,
            "last_error_at": self.last_error_at,
            "last_error_class": self.last_error_class,
        }


class MarketDataProvider(ABC):
    """Base class every market-data provider adapter implements.

    Subclasses set :attr:`name`, :attr:`kind`, :attr:`tier`,
    :attr:`capabilities` and :attr:`normalizer_key`, then override only the
    `fetch_*` methods matching the capabilities they declared.
    """

    #: Stable provider identifier — "yahoo", "zerodha", "nse_licensed", …
    #: Appears in gateway logs and the registry ONLY. It must never reach a
    #: normalized event, an API response, the AI, or the frontend.
    name: str = "unnamed"

    #: How data arrives (poll loop vs persistent connection).
    kind: ProviderKind = ProviderKind.POLLING

    #: What consumers are told about freshness. Set honestly: labelling a
    #: 15-minute-delayed poll as STREAMING would make the AI say "live price"
    #: about a stale number, which CLAUDE.md's data rules forbid outright.
    tier: SourceTier = SourceTier.DELAYED

    #: Capabilities this adapter actually serves.
    capabilities: frozenset = frozenset()

    #: Which normalizer family in `normalizer.py` understands this provider's
    #: raw payload shape. Separate from `name` so two providers sharing a wire
    #: format (e.g. two brokers on the same vendor API) can share a normalizer.
    normalizer_key: str = "unknown"

    #: Resolution priority — lower wins. Mirrors the Provider Priority
    #: Algorithm in MARKET_DATA_ARCHITECTURE.md:
    #:     1 connected broker WebSocket
    #:     2 licensed exchange feed
    #:     3 polled baseline (Yahoo) — the permanent floor
    priority: int = 100

    def __init__(self) -> None:
        self._health = ProviderHealth()
        self._connected = False

    # ── Lifecycle ────────────────────────────────────────

    async def connect(self) -> None:
        """Establish the provider session. Must be idempotent.

        A polling provider over a stateless HTTP API has nothing to establish,
        so the default is a no-op that simply records the connected flag. A
        streaming adapter overrides this to open its WebSocket.
        """
        self._connected = True

    async def disconnect(self) -> None:
        """Tear the session down. Must be idempotent."""
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Health ───────────────────────────────────────────

    def health(self) -> ProviderHealth:
        """Current health. Read by the Source Manager during resolution."""
        return self._health

    def record_success(self, *, empty: bool = False) -> Optional[ProviderState]:
        """Record a successful call. Returns the new state if it changed.

        `empty` marks a call that succeeded at the transport level but returned
        nothing. It is counted separately and does NOT reset the failure streak
        on its own — a provider answering 200-with-no-data for every symbol is
        not healthy, and treating it as healthy is exactly how a silently empty
        feed keeps its primary slot.
        """
        self._health.total_calls += 1
        if empty:
            self._health.total_empty += 1
            return None

        self._health.last_success_at = _now_iso()
        self._health.consecutive_failures = 0
        return self._transition(ProviderState.UP)

    def record_failure(self, exc: BaseException) -> Optional[ProviderState]:
        """Record a failed call. Returns the new state if it changed."""
        self._health.total_calls += 1
        self._health.total_errors += 1
        self._health.consecutive_failures += 1
        self._health.last_error_at = _now_iso()
        self._health.last_error_class = type(exc).__name__

        failures = self._health.consecutive_failures
        if failures >= DOWN_AFTER_FAILURES:
            return self._transition(ProviderState.DOWN)
        if failures >= DEGRADED_AFTER_FAILURES:
            return self._transition(ProviderState.DEGRADED)
        return None

    def reset_health(self) -> None:
        """Drop all health state. Startup and tests only."""
        self._health = ProviderHealth()

    def _transition(self, state: ProviderState) -> Optional[ProviderState]:
        if self._health.state == state:
            return None
        previous = self._health.state
        self._health.state = state
        logger.info(
            "Provider %s health %s -> %s (consecutive_failures=%d)",
            self.name, previous.value, state.value,
            self._health.consecutive_failures,
        )
        return state

    # ── Capability-gated fetch surface ───────────────────
    #
    # Every method returns the provider's RAW payload shape. Normalization is
    # the gateway's job. Each default raises so that an adapter which declares
    # a capability but forgets to implement it fails loudly at the first call
    # instead of silently returning nothing — a provider that quietly serves
    # empty data is indistinguishable from a market with no movers.

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def _unsupported(self, capability: Capability) -> CapabilityUnavailable:
        return CapabilityUnavailable(
            f"provider {self.name!r} does not serve capability {capability.value!r}"
        )

    async def fetch_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        raise self._unsupported(Capability.QUOTES)

    async def fetch_universe_quotes(self) -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.UNIVERSE_QUOTES)

    async def fetch_indices(self) -> Optional[Dict[str, Any]]:
        raise self._unsupported(Capability.INDICES)

    async def fetch_sectors(self) -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.SECTORS)

    async def fetch_gainers(self, count: int = 5) -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.MOVERS)

    async def fetch_losers(self, count: int = 5) -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.MOVERS)

    async def fetch_global_markets(self) -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.GLOBAL_MARKETS)

    async def fetch_commodities(self) -> Dict[str, Any]:
        raise self._unsupported(Capability.COMMODITIES)

    async def fetch_chart(self, symbol: str, period: str = "1D") -> List[Dict[str, Any]]:
        raise self._unsupported(Capability.OHLC)

    async def search(self, query: str, limit: int = 10) -> Optional[List[Dict[str, Any]]]:
        raise self._unsupported(Capability.SEARCH)

    # ── Introspection ────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        """Diagnostic snapshot. Carries the provider name, so this is for
        admin/diagnostics surfaces and logs only — never for a market event,
        an AI context, or a live UI surface."""
        return {
            "name": self.name,
            "kind": self.kind.value,
            "tier": self.tier.value,
            "priority": self.priority,
            "connected": self._connected,
            "capabilities": sorted(c.value for c in self.capabilities),
            "health": self._health.as_dict(),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r} tier={self.tier.value}>"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

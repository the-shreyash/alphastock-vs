"""Yahoo Finance provider adapter — the platform's permanent baseline feed.

This is the ONLY module in the Market Engine allowed to know that Yahoo Finance
exists. Everything above it resolves a provider through the Source Manager and
receives normalized events that carry a `source_tier` and no provider identity
whatsoever.

WHY IT IS `tier=DELAYED` AND `kind=POLLING`
--------------------------------------------
Yahoo Finance offers no streaming interface. Quotes are fetched request/response
and cached, so freshness is bounded by the poll interval plus Yahoo's own delay —
15–60 seconds in practice (MARKET_DATA_ARCHITECTURE.md, Category 1). Declaring
this honestly matters beyond bookkeeping: `tier` is what the AI calibrates its
language against, so mislabelling it STREAMING would have the AI say "the live
price is ₹2,891" about a number that is a minute old. CLAUDE.md's data rules and
the failover rules in MARKET_DATA_ARCHITECTURE.md both forbid that.

WHY THE ADAPTER WRAPS `services/real_market.py` RATHER THAN REPLACING IT
------------------------------------------------------------------------
`real_market.py` is the production-hardened Yahoo client: pooled HTTP (PH3.4),
Redis-backed caching, a batched cache warm for the universe fan-out (Sprint R9),
an overridable origin for load testing (PH3.5), and error containment that every
market route depends on. Reimplementing it behind the new contract would risk
all of that to make the file tree look tidier, which the D1 brief explicitly
rules out. So D1 places it *behind* the contract: `real_market` becomes this
adapter's provider client, and the rest of the platform loses its ability to
reach it directly.

Two things in `real_market.py` are not provider concerns and are knowingly left
in place for D2 (see DECISIONS.md, ADR-028):

  1. **Derived analytics.** RSI/MACD/VWAP, market breadth, sentiment scoring and
     gainer/loser ranking are computed inside the Yahoo module. They are Market
     Engine business logic that happens to live in the provider file. Lifting
     them out is a mechanical but wide change with real regression surface, and
     it does not block a second provider from being added.
  2. **Non-Yahoo collectors.** FII/DII comes from NSE India's public API and
     news from RSS feeds, both inside modules the gateway calls directly. They
     are separate providers wearing no adapter yet.

WHY IMPORTS ARE FUNCTION-LOCAL
------------------------------
Every `real_market` import below is inside its method. This matches the existing
gateway and keeps two properties the test suite relies on: module import stays
cheap and cycle-free at server startup, and the function is looked up on the
module object at call time, so `patch("services.real_market.fetch_real_gainers")`
still intercepts it. Hoisting these to module scope would silently break the
market-data test suite by binding the original functions at import.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.market_engine.providers.base import (
    Capability,
    MarketDataProvider,
    ProviderKind,
    SourceTier,
)

logger = logging.getLogger(__name__)

#: Priority 3 in the Provider Priority Algorithm — below a connected broker
#: WebSocket (1) and a licensed exchange feed (2). Yahoo is the permanent floor:
#: MARKET_DATA_ARCHITECTURE.md guarantees a user can never end up with *no*
#: provider while Yahoo is reachable, so nothing may ever be registered below it.
YAHOO_PRIORITY = 3


class YahooPollingAdapter(MarketDataProvider):
    """Polling adapter over the Yahoo Finance HTTP APIs.

    Returns raw `services.real_market` payload shapes. The gateway normalizes
    them through `normalizer.py`'s `yahoo` family — the adapter itself never
    reshapes anything, so there is exactly one place to look when a Yahoo field
    changes meaning.
    """

    name = "yahoo"
    kind = ProviderKind.POLLING
    tier = SourceTier.DELAYED
    normalizer_key = "yahoo"
    priority = YAHOO_PRIORITY
    capabilities = frozenset({
        Capability.QUOTES,
        Capability.UNIVERSE_QUOTES,
        Capability.INDICES,
        Capability.SECTORS,
        Capability.MOVERS,
        Capability.GLOBAL_MARKETS,
        Capability.COMMODITIES,
        Capability.OHLC,
        Capability.SEARCH,
    })
    # Deliberately absent: TICKS and DEPTH. Yahoo publishes neither, and
    # declaring them would make the Source Manager resolve Yahoo for an
    # order-book request it can only answer with nulls.

    # ── Lifecycle ────────────────────────────────────────

    async def connect(self) -> None:
        """No session to establish.

        Yahoo Finance is a stateless HTTP API — there is no handshake, no
        token, and no connection to hold open. `real_market` opens a pooled
        client per request. Marking the adapter connected is therefore honest:
        it *is* ready to serve. (`services.stock_details` bootstraps a cookie +
        crumb session lazily for the `quoteSummary` endpoint, but that is scoped
        to that module and is not a precondition for quotes or charts.)
        """
        await super().connect()

    # ── Quotes ───────────────────────────────────────────

    async def fetch_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Raw quote with technical indicators. None when Yahoo has no data."""
        from services.real_market import fetch_real_stock_quote
        return await fetch_real_stock_quote(symbol)

    async def fetch_universe_quotes(self) -> List[Dict[str, Any]]:
        """Raw quotes for the tracked universe. Symbols with no live quote are
        omitted by the client — never substituted."""
        from services.real_market import fetch_all_universe_quotes
        return await fetch_all_universe_quotes()

    # ── Indices ──────────────────────────────────────────

    async def fetch_indices(self) -> Optional[Dict[str, Any]]:
        """Market overview: Nifty / Bank Nifty / Sensex, India VIX, breadth.

        The index sub-dicts carry no `name` of their own — they are keyed by
        position in the overview. The gateway supplies the name when
        normalizing, because an unnamed index fails validation and would be
        dropped.
        """
        from services.real_market import fetch_real_market_overview
        return await fetch_real_market_overview()

    # ── Sectors and movers ───────────────────────────────

    async def fetch_sectors(self) -> List[Dict[str, Any]]:
        from services.real_market import fetch_real_sectors
        return await fetch_real_sectors()

    async def fetch_gainers(self, count: int = 5) -> List[Dict[str, Any]]:
        from services.real_market import fetch_real_gainers
        return await fetch_real_gainers(count)

    async def fetch_losers(self, count: int = 5) -> List[Dict[str, Any]]:
        from services.real_market import fetch_real_losers
        return await fetch_real_losers(count)

    # ── Global markets and commodities ───────────────────

    async def fetch_global_markets(self) -> List[Dict[str, Any]]:
        from services.real_market import fetch_real_global_markets
        return await fetch_real_global_markets()

    async def fetch_commodities(self) -> Dict[str, Any]:
        from services.real_market import fetch_real_commodities
        return await fetch_real_commodities()

    # ── Charts and search ────────────────────────────────

    async def fetch_chart(self, symbol: str, period: str = "1D") -> List[Dict[str, Any]]:
        from services.real_market import fetch_real_chart_data
        return await fetch_real_chart_data(symbol, period)

    async def search(self, query: str, limit: int = 10) -> Optional[List[Dict[str, Any]]]:
        """Instrument search. Returns None on provider failure (distinct from
        `[]`, a valid empty result) — the caller decides the fallback."""
        from services.real_market import search_yahoo_stocks
        return await search_yahoo_stocks(query, limit)

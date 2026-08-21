"""Broker instrument identity → canonical market identity (D4.3).

This module is the boundary a broker's instrument identifier does not cross.

THE PROBLEM
-----------
Every broker names instruments its own way. Kite subscribes and ticks by a
32-bit integer (`738561`); Upstox by an instrument key (`"NSE_EQ|INE002A01018"`);
Angel One by a numeric token in a string; a fictional-but-entirely-ordinary
broker simply by trading symbol. `BrokerTick.instrument_token` is typed `Any`
for exactly that reason — it is the broker's own opaque handle, and the broker
layer is the only place entitled to interpret it.

Before D4.3 nothing interpreted it *there*. The raw identifier travelled into
`portfolio_stream`, `trade_stream` and the browser, and each consumer joined it
against `db.holdings` itself. That made two core services depend on a shape only
one broker guarantees, duplicated the join, and — the sharp edge — gave a
symbol-identified broker no join key at all, so its users' live P&L silently
stopped updating while its socket kept delivering perfectly good prices.

WHAT THIS MODULE DOES
---------------------
:class:`InstrumentMap` is built from the rows this platform already syncs for a
user's brokerage account — holdings and positions, both canonical
:class:`~services.brokers.contracts.BrokerHolding` /
:class:`~services.brokers.contracts.BrokerPosition` shapes, which carry the
broker's `instrument_token` *and* the trading symbol and exchange side by side.
That pairing is the mapping table; nothing had to be invented or fetched to
build it, and it is per-account by nature, which is correct: instrument
identifiers are only meaningful within the broker that issued them.

:func:`canonical_ticks` then converts a batch of `BrokerTick` dicts into
canonical :class:`~services.market_engine.ticks.MarketTick` dicts, dropping what
it cannot resolve.

TWO IDENTIFICATION STYLES, ONE BOUNDARY
---------------------------------------
* **Numeric-token brokers** (Kite): the tick carries a token and no symbol. The
  token is looked up; a token the account has no row for is *unresolvable* and
  the tick is dropped. It is never used as a symbol — a fallback like that would
  push `738561` into `db.holdings`, the trade snapshot and the AI's context as
  if it were an instrument name.
* **Symbol-identified brokers**: the tick carries the trading symbol, which is
  canonical identity already (modulo case). The map is still consulted, to
  qualify the symbol with the exchange the account holds it on; a symbol the
  account does not hold still resolves, because a trading symbol is meaningful
  platform-wide while a token is not.

Adding a broker of either kind needs no change here and no change in any core
service, which is the property `test_a_symbol_identified_broker_needs_no_core_change`
exists to keep true.

WHAT NEVER LEAVES
-----------------
`instrument_token` — in any form, under any key. `canonical_ticks` builds its
output from :meth:`MarketTick.as_dict`, a closed field list, rather than by
copying and patching the incoming dict, so containment is a property of the
boundary rather than of every future caller remembering to `pop()` a key.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

from services.market_engine.ticks import MarketInstrument, MarketTick, MarketTickError

logger = logging.getLogger(__name__)


def _token_key(token: Any) -> Optional[str]:
    """A hashable, comparable form of a broker's instrument identifier.

    Stringified because the same identifier reaches this module through two
    routes that disagree on type: a Kite token is an `int` on a synced holding
    row and an `int` from the binary codec, but MongoDB round-trips and JSON
    payloads can turn either into `"738561"`. Comparing on `str` makes the
    mapping insensitive to that, which is safe here because the value is only
    ever matched, never interpreted.
    """
    if token is None or isinstance(token, bool):
        return None
    key = str(token).strip()
    return key or None


class InstrumentMap:
    """Broker instrument identifiers → canonical identity, for one account.

    Immutable once built. `BrokerEngine` rebuilds it when the account's
    portfolio changes rather than mutating one in place, so a stream reading the
    map never observes a half-updated table.
    """

    __slots__ = ("_by_token", "_by_symbol")

    def __init__(
        self,
        by_token: Optional[Dict[str, MarketInstrument]] = None,
        by_symbol: Optional[Dict[str, MarketInstrument]] = None,
    ) -> None:
        self._by_token: Dict[str, MarketInstrument] = dict(by_token or {})
        self._by_symbol: Dict[str, MarketInstrument] = dict(by_symbol or {})

    # -- construction ---------------------------------------------------------
    @classmethod
    def from_portfolio(
        cls,
        holdings: Optional[Iterable[Dict[str, Any]]] = None,
        positions: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> "InstrumentMap":
        """Build the table from canonical holding / position rows.

        Rows without a symbol are skipped rather than rejected: a broker may
        return an instrument this platform cannot name, and one such row must
        not cost the account every other mapping in the batch.
        """
        by_token: Dict[str, MarketInstrument] = {}
        by_symbol: Dict[str, MarketInstrument] = {}
        for row in list(holdings or []) + list(positions or []):
            if not isinstance(row, dict):
                continue
            try:
                instrument = MarketInstrument.of(row.get("symbol"), row.get("exchange"))
            except MarketTickError:
                continue
            key = _token_key(row.get("instrument_token"))
            if key is not None:
                by_token.setdefault(key, instrument)
            # An exchange-qualified row wins over an unqualified one for the same
            # symbol: both identify the instrument, one says more.
            existing = by_symbol.get(instrument.symbol)
            if existing is None or (existing.exchange is None and instrument.exchange is not None):
                by_symbol[instrument.symbol] = instrument
        return cls(by_token=by_token, by_symbol=by_symbol)

    # -- resolution -----------------------------------------------------------
    def resolve(
        self,
        *,
        instrument_token: Any = None,
        symbol: Any = None,
        exchange: Any = None,
    ) -> Optional[MarketInstrument]:
        """Canonical identity for one broker-identified instrument, or None.

        `None` means "this account has no way to name that instrument" and is a
        normal outcome, not an error: a broker may tick an instrument the user
        no longer holds, or one that arrived between two portfolio syncs.
        """
        key = _token_key(instrument_token)
        if key is not None:
            mapped = self._by_token.get(key)
            if mapped is not None:
                return mapped

        if symbol not in (None, ""):
            try:
                candidate = MarketInstrument.of(symbol, exchange)
            except MarketTickError:
                return None
            mapped = self._by_symbol.get(candidate.symbol)
            if mapped is not None and candidate.exchange is None:
                return mapped
            return candidate

        # A token this account cannot name, and no symbol to fall back on. The
        # token is NOT used as a symbol — see the module docstring.
        return None

    def __len__(self) -> int:
        return len(self._by_token) + len(self._by_symbol)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<InstrumentMap tokens={len(self._by_token)} symbols={len(self._by_symbol)}>"


#: An account whose portfolio is empty or not yet synced. Resolves nothing by
#: token, everything a symbol-identified broker sends by symbol.
EMPTY_INSTRUMENT_MAP = InstrumentMap()


#: Minimum seconds between two "nothing resolved" warnings for one broker.
#:
#: The condition that triggers that warning is persistent by nature — a stale
#: map stays stale until the next sync — while the ticks that hit it arrive
#: several times a second per connected account. Unthrottled, one stale map
#: writes tens of thousands of identical WARNING lines an hour and buries every
#: other signal in the log. Throttled, the condition is still visible, which is
#: the whole point of logging it at WARNING in the first place.
WARN_INTERVAL_SECONDS = 60.0

_last_warned: Dict[str, float] = {}


def _warn_allowed(broker: str) -> bool:
    key = broker or "broker"
    now = time.monotonic()
    last = _last_warned.get(key)
    if last is not None and (now - last) < WARN_INTERVAL_SECONDS:
        return False
    _last_warned[key] = now
    return True


def reset_warn_state() -> None:
    """Re-arm the warning throttle. For tests; there is no runtime caller."""
    _last_warned.clear()


def canonical_ticks(
    ticks: Sequence[Any],
    instrument_map: Optional[InstrumentMap] = None,
    *,
    broker: str = "",
) -> List[Dict[str, Any]]:
    """Convert a batch of broker ticks into canonical market ticks.

    Input is whatever `BrokerStream` forwarded: `BrokerTick` dicts (the normal
    case) or `BrokerTick` instances. Output is `MarketTick.as_dict()` — symbol,
    exchange, price, volume, ingested_at, and nothing else.

    Nothing raises. A tick that cannot be resolved or cannot be represented is
    dropped and counted; the surviving ticks are returned. That is the same
    batch discipline `BrokerStreamEvent.tick_event` applies one layer down and
    for the same reason: a frame is a batch of hundreds of packets, and one
    short packet must not cost the other 299 their prices — nor drop a live
    socket.

    The drop counts are logged at WARNING when *everything* was dropped, because
    a stream that resolves nothing looks exactly like a quiet market from
    outside, and that is precisely the failure this boundary exists to reveal.
    """
    instrument_map = instrument_map if instrument_map is not None else EMPTY_INSTRUMENT_MAP
    out: List[Dict[str, Any]] = []
    unresolved = 0
    malformed = 0

    for raw in ticks or ():
        payload = raw.as_dict() if hasattr(raw, "as_dict") else raw
        if not isinstance(payload, dict):
            malformed += 1
            continue
        instrument = instrument_map.resolve(
            instrument_token=payload.get("instrument_token"),
            symbol=payload.get("symbol"),
            exchange=payload.get("exchange"),
        )
        if instrument is None:
            unresolved += 1
            continue
        try:
            tick = MarketTick.create(
                instrument,
                payload.get("last_price"),
                volume=payload.get("volume") or None,
            )
        except MarketTickError as exc:
            malformed += 1
            logger.debug("%s tick dropped at the canonical boundary: %s", broker or "broker", exc)
            continue
        out.append(tick.as_dict())

    if not out and (unresolved or malformed) and _warn_allowed(broker):
        logger.warning(
            "%s stream: no tick in a batch of %d reached the canonical boundary "
            "(%d unmapped instruments, %d unusable) — the account's instrument map may be stale",
            broker or "broker",
            unresolved + malformed,
            unresolved,
            malformed,
        )
    elif unresolved or malformed:
        logger.debug(
            "%s stream: %d of %d ticks dropped (%d unmapped, %d unusable)",
            broker or "broker",
            unresolved + malformed,
            len(out) + unresolved + malformed,
            unresolved,
            malformed,
        )
    return out

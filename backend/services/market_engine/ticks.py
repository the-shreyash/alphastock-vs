"""Canonical market tick + instrument identity — the shape core services consume.

WHERE THIS SITS
---------------
D4.2 stopped a broker's *wire format* at the adapter: a codec decodes a frame
and may return nothing but a :class:`~services.brokers.streaming.BrokerTick`.
That closed the shape leak. It did not close the **identity** leak, and D4.3 is
where that one closes::

    broker wire frame
          ↓  broker-owned codec (D4.2)
    BrokerStreamEvent → BrokerTick        broker-opaque instrument identity
          ↓  instrument mapping (D4.3, services/brokers/instruments.py)
    MarketTick                            canonical identity — this module
          ↓
    portfolio_stream / trade_stream / the user's app WebSocket

A `BrokerTick` identifies its instrument the way its own broker does — one
sends an opaque 32-bit integer, another a compound string key, the next a plain
trading symbol. Before D4.3 that identifier travelled all the way into
`portfolio_stream.apply_broker_ticks`, `trade_stream.apply_broker_ticks` and the
browser, and both services did the token→symbol join themselves against
`db.holdings`. (This module deliberately names no broker; the examples live in
`services/brokers/instruments.py`, on the side of the line entitled to know
them.)

Two consequences, both real rather than theoretical:

* **Core services were coupled to a broker's identifier format.** A broker whose
  feed identifies instruments *by symbol* rather than by an opaque token
  carries no token to join on, so every join produced nothing, `override` stayed
  empty, and every live P&L recompute for that broker's users stopped. Silently:
  no exception, no log line, a connected socket delivering good prices into a
  dead end. This is the same class of defect D4.2 found one layer down.
* **The join was written twice**, once in each consumer, so the two could drift
  and a third consumer would have written it a third time.

WHAT IS CANONICAL HERE
----------------------
`symbol` and `exchange`, exactly as MARKET_DATA_ARCHITECTURE.md already defines
them for every other market event: "Symbols are normalized to StockAssist's
internal symbol convention (uppercase, exchange-qualified where ambiguous)".
This module invents no new identity scheme — it names the one the platform has
always used for quotes, holdings, trades and the watchlist, so a canonical tick
joins against all of them without a translation table.

WHY THE FIELD LIST IS SHORT
---------------------------
`symbol`, `exchange`, `price`, `volume`, `ingested_at` — nothing else. Depth,
bid/ask, trade side and the rest of the `trade.tick` surface in
MARKET_DATA_ARCHITECTURE.md belong to a *market data* feed, and no broker is
registered as a market-data provider yet (that is later D4 work). Defining those
fields now would mean shipping a contract nothing populates and nothing reads.

`ingested_at` rather than the broker's own timestamp is deliberate.
`BrokerTick.timestamp` is a verbatim broker string precisely because brokers
disagree on format and timezone and a wrong parse is worse than no parse, while
canonical market events are UTC by rule. So the canonical tick carries the one
timestamp this platform can state truthfully — when *we* received it — under the
same name the Market Gateway already stamps on every normalized event.

WHY IT LIVES IN THE MARKET ENGINE AND NOT THE BROKER LAYER
----------------------------------------------------------
A canonical tick is a market concept: its consumers are market/portfolio
services, and the market layer must be able to hold one without knowing brokers
exist (pinned by `test_the_market_engine_never_imports_a_broker_module`). The
mapping *from* a broker's identifier *to* this shape is broker knowledge and
lives on the broker side of the line, in `services/brokers/instruments.py`,
which imports this module. broker→market is the permitted direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.market_engine.validator import MAX_STOCK_PRICE, MIN_STOCK_PRICE


class MarketTickError(ValueError):
    """A tick that cannot be represented canonically.

    Raised by construction rather than returned as a flag: an unusable tick has
    no safe default (a tick with no symbol cannot be joined to anything, and a
    tick with no price cannot mark a position), and the callers that decode
    batches — `services.brokers.instruments.canonical_ticks` — catch it per tick
    so one bad record is dropped instead of a whole frame, or a whole stream.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MarketInstrument:
    """Canonical instrument identity: what every core service already keys on.

    Constructed through :meth:`of`, which is where normalization happens.
    Constructing one directly with a non-canonical symbol raises, so "canonical"
    is a property of the type rather than of every caller remembering to
    `.upper()`.
    """

    symbol: str
    exchange: Optional[str] = None

    def __post_init__(self) -> None:
        symbol = self.symbol
        if not isinstance(symbol, str) or not symbol.strip():
            raise MarketTickError("instrument has no symbol")
        if symbol != symbol.strip().upper():
            raise MarketTickError(f"instrument symbol {symbol!r} is not canonical — use MarketInstrument.of()")

    @classmethod
    def of(cls, symbol: Any, exchange: Any = None) -> "MarketInstrument":
        """Normalize a symbol/exchange pair into canonical identity."""
        symbol = "" if symbol is None else str(symbol).strip().upper()
        if not symbol:
            raise MarketTickError("instrument has no symbol")
        exchange = None if exchange in (None, "") else str(exchange).strip().upper() or None
        return cls(symbol=symbol, exchange=exchange)


@dataclass(frozen=True)
class MarketTick:
    """One canonical price tick.

    The only tick shape any core service sees. It carries no broker name, no
    broker instrument identifier and no provider identity — a consumer cannot
    branch on where the price came from, which is Developer Rule 4 in
    MARKET_DATA_ARCHITECTURE.md applied to the streaming path.
    """

    symbol: str
    price: float
    exchange: Optional[str] = None
    volume: Optional[int] = None
    ingested_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        # Identity is validated by the same rules as MarketInstrument, so a tick
        # built by hand cannot be less canonical than one built through `of()`.
        MarketInstrument(symbol=self.symbol, exchange=self.exchange)
        price = self.price
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise MarketTickError(f"{self.symbol}: tick price is not numeric ({price!r})")
        # The Market Engine's own quote bounds, not a second opinion: a price a
        # quote would be rejected for must not enter through the tick path.
        # `MIN_STOCK_PRICE` also rejects 0.0, which is what a truncated binary
        # packet decodes to and what would mark a whole position at zero.
        if not (MIN_STOCK_PRICE <= float(price) <= MAX_STOCK_PRICE):
            raise MarketTickError(f"{self.symbol}: tick price out of range ({price})")
        if self.volume is not None:
            if isinstance(self.volume, bool) or not isinstance(self.volume, int) or self.volume < 0:
                raise MarketTickError(f"{self.symbol}: tick volume is invalid ({self.volume!r})")

    @classmethod
    def create(
        cls,
        instrument: MarketInstrument,
        price: Any,
        *,
        volume: Any = None,
    ) -> "MarketTick":
        """Build a canonical tick from a resolved identity and a raw price."""
        if not isinstance(instrument, MarketInstrument):
            raise MarketTickError(f"a tick needs a resolved MarketInstrument, got {type(instrument).__name__}")
        try:
            price = float(price)
        except (TypeError, ValueError):
            raise MarketTickError(f"{instrument.symbol}: tick price is not numeric ({price!r})")
        if volume in (None, ""):
            volume = None
        else:
            try:
                volume = int(volume)
            except (TypeError, ValueError):
                raise MarketTickError(f"{instrument.symbol}: tick volume is not numeric ({volume!r})")
        return cls(
            symbol=instrument.symbol,
            price=price,
            exchange=instrument.exchange,
            volume=volume,
        )

    def as_dict(self) -> Dict[str, Any]:
        """The wire form. Dicts are the currency at service boundaries here —
        these go onto the Event Bus and out of the app WebSocket as JSON."""
        return {
            "symbol": self.symbol,
            "price": self.price,
            "exchange": self.exchange,
            "volume": self.volume,
            "ingested_at": self.ingested_at,
        }

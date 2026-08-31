"""What a user's market feed should cover — the instrument universe (D5.15).

WHY THIS MODULE EXISTS
----------------------
Until D5.15 a broker feed's instrument universe was defined in one place and by
one rule: whatever `holdings` and `positions` carried an identifier for. That
rule is correct about *portfolio* instruments and silently wrong about everything
else the product shows, and D5.15's live run is where it stopped being a
theoretical objection::

    a real, authenticated broker account, both sockets open, 0 holdings and
    0 positions  ->  0 instruments subscribed  ->  a socket that is structurally
    incapable of ever delivering a tick, while the platform registered it as a
    streaming market-data provider and told its owner the TICKS capability was
    available.

The account was not broken and neither was the adapter. The *universe rule* was:
an authenticated user with an empty demat still opens a dashboard, still keeps a
watchlist, and still expects those prices to move.

WHAT IS AND IS NOT DECIDED HERE
-------------------------------
This module answers exactly one question — **which canonical symbols should this
account's feed cover** — and it answers it in canonical symbols only. It holds:

* no broker name, no broker instrument identifier, and no import from an
  adapter. Turning a symbol into something a wire can subscribe to is the
  adapter's half of the catalogue (`BrokerCapability.INSTRUMENT_CATALOGUE`), and
  the two halves meet in `BrokerEngine.start_stream` and nowhere else;
* no policy about readiness, probation, freshness or selection. A wider universe
  changes what a feed is *asked* for, never what it is *believed* to be
  delivering — D5.2/D5.3 semantics are untouched by construction, because a
  symbol enters this list without carrying any evidence with it.

WHY ORDER IS PART OF THE CONTRACT
---------------------------------
The result is ordered, not a set, and the order is portfolio → watchlist →
dashboard. A broker's per-connection instrument ceiling is enforced downstream
(`services/brokers/sharding.py`, and each adapter's own backstop), and both trim
from the end. So when an account's universe does not fit, the instruments it
loses are the ones it is least entitled to expect: nobody's position goes
unpriced because a default dashboard symbol took its slot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.brokers.catalogue import (
    DEFAULT_EQUITY_EXCHANGE,
    EQUITY_SEGMENT,
    INDEX_EXCHANGES,
    INDEX_SEGMENT,
    SUPPORTED_SEGMENTS,
    normalize_exchange,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedInstrument:
    """One instrument a feed should cover: `(symbol, exchange, segment)`.

    WHY A SYMBOL WAS NOT ENOUGH (D5.16)
    ------------------------------------
    D5.15's universe was a tuple of strings, and an adapter resolved a string.
    `RELIANCE` is two instruments — NSE and BSE — with different identifiers at
    every one of the five brokers, so a bare symbol asked a question the
    catalogue could only answer by picking a listing. It picked NSE, silently,
    because the only implemented master was NSE-only. A BSE holding would have
    been subscribed and marked at the NSE price with nothing raising.

    Frozen and hashable because this is the de-duplication key of a feed
    universe: a symbol held on BSE and watched (which means NSE, the platform
    default) is genuinely two subscriptions, and collapsing them on symbol would
    lose one.

    `segment` was fixed at `EQUITY` in D5.16 and carried anyway, as "the field
    that makes widening a *value* change rather than a signature change". D5.17
    spends it: `INDEX` is the second value, and the signature did not change.

    It is also the field that lets a catalogue refuse, by construction, what the
    platform cannot resolve — a segment outside `SUPPORTED_SEGMENTS` yields no
    instrument at all rather than an instrument nothing can look up.
    """

    symbol: str
    exchange: str
    segment: str = EQUITY_SEGMENT

    @classmethod
    def of(
        cls,
        symbol: Any,
        exchange: Any = None,
        segment: str = EQUITY_SEGMENT,
    ) -> "Optional[FeedInstrument]":
        """Build one, or None when it is not a supported instrument.

        `exchange=None` means "the platform's default", which is a *stated*
        default (`catalogue.DEFAULT_EQUITY_EXCHANGE`) and not a first-match. An
        exchange outside the supported set returns None rather than being
        rewritten — see `catalogue.normalize_exchange`.

        An unsupported `segment` returns None for the same reason and with more
        force: an exchange at least names a real venue, while a segment nothing
        resolves produces a subscription entry every adapter will silently omit,
        which is indistinguishable downstream from a broker that has never heard
        of the instrument.
        """
        if segment not in SUPPORTED_SEGMENTS:
            return None
        canonical = _canonical(symbol)
        if not canonical:
            return None
        if exchange is None or not str(exchange).strip():
            name = DEFAULT_EQUITY_EXCHANGE
        else:
            name = normalize_exchange(exchange)
            if name is None:
                return None
        return cls(symbol=canonical, exchange=name, segment=segment)

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.exchange}:{self.symbol}"


#: Hard ceiling on one account's feed universe.
#:
#: Not a broker limit — those are declared per channel and enforced by the shard
#: planner, which is the only layer that knows them. This is the platform
#: refusing to plan an unbounded subscription at all: every input below is
#: user-controlled (a watchlist has no server-side size limit) and an account
#: that asked for fifty thousand instruments would turn one connection into a
#: shard plan of dozens before any broker ever saw the request.
MAX_FEED_UNIVERSE = 500


def dashboard_symbols() -> Tuple[str, ...]:
    """The instruments every account's dashboard shows.

    Read from `market_data.STOCK_UNIVERSE` — the list the platform already uses
    for the heatmap, the movers, the universe quotes and the scanner — rather
    than from a second constant defined here. A dashboard set that could drift
    from the universe the rest of the product renders would produce the exact
    symptom D5.15 exists to remove: a price on screen that no feed was ever
    asked to cover.

    Imported inside the function because `market_data` is a top-level module
    with import-time cost, and a broker whose adapter declares no catalogue must
    not pay it.
    """
    try:
        from market_data import STOCK_UNIVERSE
    except Exception:  # pragma: no cover - a missing universe is not a stream failure
        logger.warning("Dashboard universe unavailable — feed universe falls back to the account's own instruments")
        return ()
    return tuple(
        symbol
        for symbol in (_canonical(row.get("symbol")) for row in STOCK_UNIVERSE if isinstance(row, dict))
        if symbol
    )


def index_instruments() -> Tuple[FeedInstrument, ...]:
    """The indices every account's dashboard shows, as feed instruments.

    WHY THESE ARE NOT `dashboard_symbols()` WITH A DIFFERENT SEGMENT (D5.17)
    ------------------------------------------------------------------------
    Two reasons, and the second is the load-bearing one.

    * They carry an **exchange that is not the default**. `SENSEX` is a BSE
      instrument at all five brokers; the other three are NSE. An unqualified
      symbol would take `DEFAULT_EQUITY_EXCHANGE` and `SENSEX` would resolve
      against NSE's index master, where it does not exist — a permanently
      missing subscription with no error anywhere.
    * They are the **only instruments on the dashboard whose canonical symbol is
      not the brokers' symbol**. `RELIANCE` is `RELIANCE` in five masters;
      `NIFTY` is `"NIFTY 50"` in one and `"NIFTY"` in four, and `INDIAVIX` is
      `"INDIA VIX"` in four and `"INDIAVIX"` in one. That translation is the
      catalogue's (`catalogue.INDEX_ALIASES`), and it can only happen if the
      instrument arriving there is *marked* as an index. The segment is what
      marks it.

    Read from `catalogue.INDEX_EXCHANGES` rather than restated here, so the
    universe and the alias table cannot come to disagree about which indices the
    platform names — the same rule `dashboard_symbols` follows for equities.
    """
    return tuple(
        instrument
        for instrument in (
            FeedInstrument.of(symbol, exchange, INDEX_SEGMENT)
            for symbol, exchange in INDEX_EXCHANGES.items()
        )
        if instrument is not None
    )


def _canonical(symbol: Any) -> Optional[str]:
    """A symbol in the platform's canonical form, or None if it is not one.

    Deliberately the same normalization `MarketInstrument.of` performs — upper,
    stripped — rather than an import of it. This module runs before any tick
    exists and must be able to name a symbol the platform has never seen a price
    for; what it must not do is invent a *second* spelling of one.
    """
    if symbol is None:
        return None
    text = str(symbol).strip().upper()
    return text or None


def _instruments_from_rows(
    rows: Optional[Iterable[Dict[str, Any]]],
) -> List[FeedInstrument]:
    """Feed instruments out of holding / position rows, in row order.

    The row's own `exchange` is used when it names a supported one, because the
    account's record is the strongest statement available about which listing it
    holds. A row on an exchange this sprint does not cover (MCX, CDS) is skipped
    rather than rewritten to NSE — see `FeedInstrument.of`.
    """
    out: List[FeedInstrument] = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        instrument = FeedInstrument.of(row.get("symbol"), row.get("exchange"))
        if instrument is not None:
            out.append(instrument)
    return out


def build_feed_universe(
    *,
    holdings: Optional[Sequence[Dict[str, Any]]] = None,
    positions: Optional[Sequence[Dict[str, Any]]] = None,
    watchlist: Optional[Sequence[Any]] = None,
    indices: Optional[Sequence[FeedInstrument]] = None,
    dashboard: Optional[Sequence[Any]] = None,
    limit: int = MAX_FEED_UNIVERSE,
) -> Tuple[FeedInstrument, ...]:
    """The instruments this account's market feed should cover.

    Every argument is optional and an omitted one contributes nothing, so the
    pre-D5.15 call — holdings and positions alone — produces exactly the
    pre-D5.15 universe. That is the property that makes this safe to introduce
    beneath five adapters at once: widening is opt-in per caller, not implied.

    D5.16 — the elements are :class:`FeedInstrument`, not symbols. Holding and
    position rows carry the exchange the account holds the instrument on;
    watchlist rows and the dashboard universe have no exchange column and take
    the platform default. De-duplication is therefore on `(symbol, exchange)`:
    a symbol held on BSE **and** watched is two genuine subscriptions, and one
    keyed on the symbol alone would have silently lost one of them.

    First-occurrence order is preserved, so an instrument the account holds
    *and* watches appears once, at its portfolio position — which is also what
    makes the account's own exchange win over the default. Returns a tuple
    because this value is read by the stream, the instrument map and the
    provider's subscription, and a shared mutable list is a way for one of them
    to change what the other two were told.

    D5.17 — `indices` are already :class:`FeedInstrument`s and are inserted
    between the watchlist and the dashboard set. Their position in the order is
    the whole of their priority statement: the ceiling trims from the end, an
    account is entitled to its own instruments before the platform's defaults,
    and four indices that every page renders outrank thirty dashboard equities
    that one card does. They are *not* run through the unqualified-symbol loop
    below — each one states an exchange (`SENSEX` is BSE) and a segment, and
    that is a statement, not a default.
    """
    ordered: List[FeedInstrument] = []
    ordered.extend(_instruments_from_rows(holdings))
    ordered.extend(_instruments_from_rows(positions))
    # An UNQUALIFIED symbol — a watchlist row, a dashboard entry — carries no
    # exchange, so it means "the platform default listing". If the account
    # already covers that symbol on some *other* exchange because a holding row
    # said so, the default is not a second instrument the user wants: it is this
    # function guessing that a user who holds RELIANCE on BSE and watches
    # "RELIANCE" means a different company's shares. Worse, both would key the
    # same canonical symbol downstream, so the account would subscribe to the
    # NSE listing and then name its ticks with the BSE holding's identity.
    #
    # Two EXPLICITLY qualified rows that disagree are a different case and are
    # both kept: the account's own records say it holds two listings, and that
    # is a statement rather than a default.
    covered = {instrument.symbol for instrument in ordered}
    for symbol in watchlist or ():
        instrument = FeedInstrument.of(symbol)
        if instrument is not None and instrument.symbol not in covered:
            covered.add(instrument.symbol)
            ordered.append(instrument)
    for instrument in indices or ():
        # Already qualified — appended, not rebuilt. Guarded on symbol for the
        # same reason the loops around it are: an account that somehow holds an
        # instrument named `SENSEX` has stated something about it, and a
        # platform default must not add a second entry that keys the same
        # canonical symbol downstream.
        if isinstance(instrument, FeedInstrument) and instrument.symbol not in covered:
            covered.add(instrument.symbol)
            ordered.append(instrument)
    for symbol in dashboard or ():
        instrument = FeedInstrument.of(symbol)
        if instrument is not None and instrument.symbol not in covered:
            covered.add(instrument.symbol)
            ordered.append(instrument)

    universe = tuple(dict.fromkeys(ordered))
    if limit is not None and len(universe) > limit:
        # Logged rather than silent: a truncated universe is a feed that will
        # never cover instruments the user asked for, and the symptom of that
        # — "one of my watchlist prices never moves" — is otherwise
        # indistinguishable from a broker problem.
        logger.warning(
            "Feed universe of %d instruments exceeds the %d ceiling — covering the first %d",
            len(universe), limit, limit,
        )
        universe = universe[:limit]
    return universe

"""What counts as a tradable instrument, and which listing wins — for five brokers.

WHY THIS IS NOT FIVE PRIVATE HELPERS
------------------------------------
Every adapter parses a different file: Kite a CSV with a header, Angel One a JSON
array, Fyers a headerless CSV, Dhan a CSV with `SEM_`-prefixed columns, Upstox a
gzipped JSON per exchange. That part is irreducibly per-broker and stays in the
adapter, which is the only module entitled to know its broker's format.

What is *not* per-broker is the question those five parsers are all answering:

    of the rows that name this symbol on this exchange, which one is the
    ordinary share — and what do we do when none of them is?

That is a statement about Indian cash markets, not about any broker. Five copies
of it would be five chances to disagree, and they would disagree silently: the
symptom of picking the wrong row is a correct-looking price for the wrong
instrument.

THE POLICY, AND THE EVIDENCE FOR IT
-----------------------------------
Verified against all five brokers' live published masters on 2026-08-31:

* **`RELIANCE` is two instruments.** NSE token 2885 / BSE 500325 at Angel One and
  Dhan; NSE 738561 / BSE 128083204 at Kite; `sf|nse_cm|2885` / `sf|bse_cm|500325`
  at Fyers. A catalogue keyed on the symbol alone answers with whichever row it
  indexed first. Hence exchange-qualified keys, everywhere.
* **A symbol can name more than one series on one exchange.** `CHOLAFIN` is
  `-EQ` (ordinary) and `-D1` (differential voting rights). `ELECTCAST` is `-EQ`
  and `-W1` (warrants). `MOTHERSON` likewise. A user who watches `CHOLAFIN`
  means the share. Hence the preference order below, and hence "first row wins"
  is not good enough even *after* the exchange is fixed.
* **Some symbols are genuinely ambiguous.** `IMC1` is three NCD series
  (`N1`/`N2`/`N3`) and none of them is an equity. Guessing between equals would
  mark a position at an unrelated instrument's price, so the key is **dropped**:
  the instrument is omitted from the subscription and the account falls back to
  the baseline for it, which is the same outcome as a symbol the broker has
  never heard of and is handled by the same code.

With this policy applied to the real masters, the ambiguous-drop count is **0**
for every broker and `RELIANCE`, `CHOLAFIN`, `ELECTCAST` and `MOTHERSON` all
resolve to their ordinary shares on both exchanges.

THE SECOND SEGMENT (D5.17)
--------------------------
D5.16 built this for one segment and said so: "the field that makes widening to
F&O, currency or commodity a *value* change rather than a signature change".
D5.17 spends that, for **indices** — and finds that a segment is not free, for a
reason specific to what an index is.

An equity's canonical symbol *is* its trading symbol: `RELIANCE` in every one of
the five masters is `RELIANCE`, so an equity catalogue needs no name table. An
index's is not. Verified against all five live published masters on 2026-08-31:

    canonical    Kite           Angel One     Dhan          Upstox        Fyers
    NIFTY        "NIFTY 50"     "NIFTY"       "NIFTY"       "NIFTY"       "NIFTY"
    BANKNIFTY    "NIFTY BANK"   "BANKNIFTY"   "BANKNIFTY"   "BANKNIFTY"   "BANKNIFTY"
    SENSEX       "SENSEX"       "SENSEX"      "SENSEX"      "SENSEX"      "SENSEX"
    INDIAVIX     "INDIA VIX"    "INDIA VIX"   "INDIA VIX"   "INDIA VIX"   "INDIAVIX"

Four spellings of two instruments, and no broker agrees with the platform on all
four. So the index half of a catalogue needs a **name table**, which the equity
half does not — and that table is a statement about how Indian indices are
spelled, not about any broker, which is why :data:`INDEX_ALIASES` lives here with
the series policy rather than five times over in five adapters.

The key therefore carries the segment: `(segment, exchange, symbol)`. A bare
`(exchange, symbol)` would let an index and an equity of the same name occupy
one slot, which is the same class of collision `(exchange, symbol)` exists to
prevent one level up — and, more practically, would make "is this catalogue
entry an index?" unanswerable at the point where the feed universe asks.

WHAT IS DELIBERATELY OUT
------------------------
F&O, currency, commodity and mutual-fund rows never enter a catalogue built
here. Not an omission and not a deferral in the D5.16 sense: D5.17's audit
established that **no Indian broker publishes a spot instrument for gold,
silver, crude or USD-INR at all**. What the masters carry is dated MCX/CDS
futures contracts — `GOLD26OCTFUT`, `USDINR26SEPFUT` — a different instrument
with a rolling identity, a separate segment entitlement, and a price that is not
the spot number the dashboard shows. Those surfaces stay on the documented Yahoo
path and are labelled as such. See ADR-056.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: The segments this catalogue resolves. Constants rather than bare strings so
#: the day a third segment is supported is a change with a name.
EQUITY_SEGMENT = "EQUITY"
INDEX_SEGMENT = "INDEX"

#: Every segment a `FeedInstrument` may name. A segment outside this set is
#: refused at construction rather than carried into a subscription that no
#: adapter can resolve — the same discipline `normalize_exchange` applies to MCX.
SUPPORTED_SEGMENTS: Tuple[str, ...] = (EQUITY_SEGMENT, INDEX_SEGMENT)

#: The exchanges the equity catalogue covers.
SUPPORTED_EQUITY_EXCHANGES: Tuple[str, ...] = ("NSE", "BSE")

#: The exchange an unqualified symbol means.
#:
#: A watchlist row carries no exchange — the collection has never had the column
#: — and neither does the dashboard universe. Something has to decide, and the
#: choice is between *stating* a default and letting the catalogue answer with
#: whichever listing its master happened to list first. The platform's own
#: universe (`market_data.STOCK_UNIVERSE`), its index set and every symbol its
#: AI reasons about are NSE, so NSE is the honest statement of what the product
#: already means. It is named here so that a future per-user or per-row exchange
#: preference has one place to override rather than five.
DEFAULT_EQUITY_EXCHANGE = "NSE"

#: NSE cash-market series that are equity, in preference order.
#:
#: `EQ` first because it is the ordinary rolling-settlement share and is what a
#: user naming a symbol means. `BE`/`BZ` are trade-for-trade and surveillance
#: segments of the *same* company. `BL` is block deals, `SM`/`ST` the SME and
#: SME trade-for-trade boards, `IQ` the institutional platform.
#:
#: Deliberately NARROWER than the adapters' own `CASH_SERIES_SUFFIXES`, which
#: also carry `GB`/`GS` (government securities). Those exist there to *name* an
#: instrument the account already holds; here the question is which instruments
#: the platform will go and subscribe to, and a government bond is not a cash
#: equity. Verified: excluding them keeps sovereign gold bonds (`-SG`), treasury
#: bills (`-TB`), NCDs (`-N0`/`-N1`) and mutual-fund units (`-MF`) out.
NSE_CASH_SERIES: Tuple[str, ...] = ("EQ", "BE", "BZ", "BL", "SM", "ST", "IQ")

#: BSE equity groups, in preference order.
#:
#: `A` is the most-liquid, best-compliance group and is where the large caps sit
#: (`RELIANCE` is BSE group `A`). `B` is the general group, `T`/`MT` trade-for-
#: trade, `M`/`MT` the SME platform, `X`/`XT` the smaller-company groups and `Z`
#: the non-compliant group. Every one of them is an equity; the ordering is
#: about which listing a bare symbol means, and a symbol never appears in two
#: groups at once in the published masters.
BSE_CASH_SERIES: Tuple[str, ...] = ("A", "B", "T", "M", "MT", "X", "XT", "Z")

_SERIES_BY_EXCHANGE: Dict[str, Tuple[str, ...]] = {
    "NSE": NSE_CASH_SERIES,
    "BSE": BSE_CASH_SERIES,
}

#: The indices the platform names, and the exchange each one is published on.
#:
#: Four, and exactly the four the dashboard's index strip renders. This is not a
#: shortlist of a larger supported set: an index the product does not show is an
#: instrument nothing would subscribe to, and every entry here costs one slot in
#: every account's feed universe and one subscription on every broker socket.
#:
#: `SENSEX` is BSE and the other three are NSE, at all five brokers — the one
#: place where "the platform default exchange" would have been wrong, and the
#: reason these are stated as pairs rather than defaulted like a watchlist row.
INDEX_EXCHANGES: Dict[str, str] = {
    "NIFTY": "NSE",
    "BANKNIFTY": "NSE",
    "SENSEX": "BSE",
    "INDIAVIX": "NSE",
}

#: Canonical index symbol -> every spelling the five published masters use.
#:
#: WHY A TABLE AND NOT A NORMALIZER
#: --------------------------------
#: The temptation is to strip spaces and compare — `"NIFTY 50"` → `"NIFTY50"`,
#: `"INDIA VIX"` → `"INDIAVIX"` — and it fails on the first entry: `NIFTY50` is
#: not `NIFTY`, and a rule loose enough to join them also joins `NIFTY 500`,
#: `NIFTY 50 EQUAL WEIGHT` and the twenty other `NIFTY *` indices in the same
#: segment of the same file. A closed table of exact spellings cannot do that.
#:
#: Matching is on the master's own string with internal whitespace collapsed and
#: nothing else removed, so a broker that starts publishing `"Nifty  50"` still
#: matches and one that starts publishing `"NIFTY 50 TR"` correctly does not.
#:
#: Verified against all five live published masters on 2026-08-31: every
#: canonical symbol resolves at every broker, on the right exchange, with **zero
#: collisions** — no two rows in any master's index segment map to one canonical
#: symbol. The collision case is not merely absent, it is handled: two equal-rank
#: candidates for one key are dropped by `build()`, exactly as for an ambiguous
#: equity.
INDEX_ALIASES: Dict[str, Tuple[str, ...]] = {
    "NIFTY": ("NIFTY", "NIFTY 50", "NIFTY50"),
    "BANKNIFTY": ("BANKNIFTY", "NIFTY BANK", "NIFTYBANK"),
    "SENSEX": ("SENSEX", "BSE SENSEX"),
    "INDIAVIX": ("INDIAVIX", "INDIA VIX"),
}

_INDEX_BY_ALIAS: Dict[str, str] = {
    alias: canonical
    for canonical, aliases in INDEX_ALIASES.items()
    for alias in aliases
}


def canonical_index(name: Any) -> Optional[str]:
    """The platform's symbol for an index a master names `name`, or None.

    None for every index the product does not show, which is most of them: 233
    rows in Kite's `INDICES` segment, 139 in Upstox's `NSE_INDEX`. Returning None
    is what keeps them out — an index catalogue is a closed list by construction,
    not a filtered one, because an unrecognised index has no canonical symbol and
    a tick nothing can name is dropped one layer later for no reason anybody
    could diagnose.
    """
    if name is None or isinstance(name, bool):
        return None
    # Internal whitespace collapsed, case folded, and nothing else touched —
    # see INDEX_ALIASES for why the normalization is deliberately this weak.
    return _INDEX_BY_ALIAS.get(" ".join(str(name).strip().upper().split()))


def normalize_exchange(value: Any) -> Optional[str]:
    """A supported equity exchange name, or None.

    None for `MCX`, `CDS`, `NFO` and anything else. Returning None rather than
    falling back to the default is the whole point: rewriting `MCX` to `NSE`
    would resolve a commodity to an equity of the same name and mark it at the
    wrong price, with nothing raising.
    """
    if value is None or isinstance(value, bool):
        return None
    name = str(value).strip().upper()
    return name if name in SUPPORTED_EQUITY_EXCHANGES else None


def series_rank(exchange: Any, series: Any) -> Optional[int]:
    """Preference rank of one series on one exchange; lower wins. None = not an
    equity series *on that exchange*.

    Exchange-specific on purpose. `A` is BSE's premier group and is not an NSE
    series at all; `EQ` is NSE's ordinary share and is not a BSE group. A shared
    set would let a mislabelled row cross exchanges.
    """
    name = normalize_exchange(exchange)
    if name is None or series is None:
        return None
    code = str(series).strip().upper()
    table = _SERIES_BY_EXCHANGE[name]
    return table.index(code) if code in table else None


#: The key one catalogue entry is filed under: `(segment, exchange, symbol)`.
CatalogueKey = Tuple[str, str, str]


class InstrumentCatalogue:
    """Accumulates candidate rows, then resolves each `(segment, exchange,
    symbol)` to one broker identifier.

    Used as: `offer()` every row an adapter's parser accepts, then `build()`.
    Two phases rather than one because the winner cannot be known until every
    candidate for a key has been seen — a master lists `CHOLAFIN-D1` before
    `CHOLAFIN-EQ` in one broker's file and after it in another's, and a
    resolution that depended on that would be correct by luck.

    D5.17 — one catalogue holds both segments rather than one per segment. The
    ambiguity rule is the reason: two rows that claim one key must be visible to
    each other to be dropped, and a per-segment catalogue would file an index
    and an equity of the same name under different objects and resolve both.
    """

    __slots__ = ("_candidates",)

    def __init__(self) -> None:
        self._candidates: Dict[CatalogueKey, List[Tuple[int, Any]]] = {}

    def offer(
        self,
        exchange: Any,
        symbol: Any,
        identifier: Any,
        *,
        series: Any = None,
        rank: Optional[int] = None,
        segment: str = EQUITY_SEGMENT,
    ) -> bool:
        """Offer one master row. Returns whether it was accepted as a candidate.

        `series` is the exchange's own series/group code and is turned into a
        rank here. `rank` is for a master that publishes no series at all — Kite
        carries an `instrument_type` of `EQ` and nothing finer — where the
        adapter has already established the row is an ordinary equity and every
        candidate is therefore equal; a duplicate key then resolves to *dropped*,
        which is the correct answer for a master that cannot tell two rows apart.

        `segment` defaults to EQUITY so that the five equity parsers D5.16 wrote
        are unchanged by D5.17. An index row is offered with `segment=INDEX` and
        `rank=0`: an index has no series, every candidate for one is therefore
        equal, and two rows claiming one index are dropped rather than guessed —
        the same answer, reached by the same code, as an ambiguous equity.

        An identifier the adapter could not build (`None`) is refused here rather
        than being indexed and skipped later: an unusable identifier in a
        subscribe frame is rejected by the broker for the whole frame at three of
        the five brokers, so it must never reach one.
        """
        if segment not in SUPPORTED_SEGMENTS:
            return False
        name = normalize_exchange(exchange)
        if name is None or identifier is None:
            return False
        canonical = str(symbol or "").strip().upper()
        if not canonical:
            return False
        if rank is None:
            rank = series_rank(name, series)
            if rank is None:
                return False
        self._candidates.setdefault((segment, name, canonical), []).append((rank, identifier))
        return True

    def build(self) -> Dict[CatalogueKey, Any]:
        """`{(SEGMENT, EXCHANGE, SYMBOL): broker identifier}`.

        A key whose two best candidates share a rank is **omitted**. It is the
        only outcome that is not a guess, and its cost is bounded and already
        handled: the instrument is absent from the subscription, absent from the
        instrument map, and therefore resolved from the baseline per symbol.
        """
        index: Dict[CatalogueKey, Any] = {}
        ambiguous = 0
        for key, candidates in self._candidates.items():
            candidates.sort(key=lambda pair: pair[0])
            if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
                ambiguous += 1
                continue
            index[key] = candidates[0][1]
        if ambiguous:
            # Counted, not listed: this is a property of the exchange's master,
            # identical for every account, and one line per symbol would repeat
            # thousands of times a day across processes for no new information.
            logger.debug(
                "Instrument catalogue: %d symbols omitted as ambiguous", ambiguous)
        return index


class CatalogueCache:
    """One process-wide download of an instrument master, shared by every account.

    An instrument master is a fact about an exchange, not about anybody's
    account: one download serves every user of that broker. The lock is what
    makes that true under the condition where it matters most — a process
    restart restoring N sessions at once, which is exactly when the cache is
    coldest. Without it, N accounts start N downloads of the same 8 MB file.

    Held per adapter class rather than on a shared base, so two brokers cannot
    accidentally share one cache slot, and re-checked *inside* the lock so every
    caller that queued behind a download uses its result instead of starting its
    own.
    """

    __slots__ = ("_ttl", "_value", "_lock")

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._value: Optional[Tuple[float, Dict[Tuple[str, str], Any]]] = None
        self._lock: Optional[Any] = None

    async def get(self, download) -> Dict[Tuple[str, str], Any]:
        """The cached index, downloading through `download()` when stale."""
        import asyncio
        import time

        cached = self._value
        if cached is not None and (time.time() - cached[0]) < self._ttl:
            return cached[1]
        if self._lock is None:
            # Created lazily on the running loop: an `asyncio.Lock` built at
            # import time binds to whichever loop imported the module.
            self._lock = asyncio.Lock()
        async with self._lock:
            cached = self._value
            if cached is not None and (time.time() - cached[0]) < self._ttl:
                return cached[1]
            index = await download()
            self._value = (time.time(), index)
            return index

    def forget(self) -> None:
        """Drop the cached master. For tests; there is no runtime caller."""
        self._value = None


def resolve_from_index(
    instruments,
    index: Dict[Tuple[str, str], Any],
) -> Dict[str, Any]:
    """`{CANONICAL_SYMBOL: broker identifier}` for a universe, against an index.

    The shared half of every adapter's `resolve_instruments`. Shared because the
    *lookup* is identical for all five — key on `(exchange, symbol)`, omit what
    is not there — while only the index construction is broker-specific.

    Keyed by symbol on the way out, not by instrument, because that is what
    `InstrumentMap` matches an arriving tick against: a tick carries the
    broker's identifier and, once resolved, a canonical symbol. Where a universe
    genuinely contains two listings of one symbol — only possible when the
    account's own records name both — the first wins and the second is skipped,
    because the map can hold one canonical identity per symbol and inventing a
    second spelling would be worse than covering one listing.

    A symbol the index does not carry is **omitted**, never mapped to a
    sentinel: an unresolvable instrument must disappear from the subscription
    rather than enter it as a key the wire will reject — which at three of the
    five brokers takes down the whole subscribe frame, not just that entry.
    """
    resolved: Dict[str, Any] = {}
    for instrument in instruments or ():
        symbol = getattr(instrument, "symbol", None)
        exchange = getattr(instrument, "exchange", None)
        # Read off the instrument rather than defaulted to EQUITY: an instrument
        # that names no segment is not an equity by assumption, it is a caller
        # this contract does not cover, and answering it with an equity lookup
        # is how an index would silently resolve to a share of the same name.
        segment = getattr(instrument, "segment", None)
        if not symbol or not segment or symbol in resolved:
            continue
        identifier = index.get((segment, exchange, symbol))
        if identifier is not None:
            resolved[symbol] = identifier
    return resolved

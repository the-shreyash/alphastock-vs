"""Complete, bounded iteration over a fan-out query (D6.7 / A3).

THE DEFECT CLASS THIS REPLACES
------------------------------
Background work on this platform sweeps the whole platform and produces per-user
output: the trade monitor over every OPEN trade, the portfolio monitor over every
holding, the market-alert loop over every user, session restore over every live
broker account. Every one of them was written as::

    rows = await db.trades.find({"status": "OPEN"}).to_list(200)

`to_list(N)` is not a page. It is a **silent truncation**: Mongo returns the
first N documents in whatever order the storage engine produced them, the code
iterates those, and the rest of the platform is simply not processed. No error,
no warning, no metric — the 201st open trade is never checked against its stop
loss, and nothing anywhere says so.

That is qualitatively different from a display cap. `GET /api/orders` returning
the 200 most recent orders is a bounded answer to a bounded question, with a
sort that makes "the first 200" meaningful. `find({"status": "OPEN"}).to_list(200)`
has no sort at all, so which users get monitored is decided by disk layout.

WHY ITERATION AND NOT A BIGGER NUMBER
-------------------------------------
Raising 200 to 2,000 moves the cliff; it does not remove it, and it removes the
one thing that made the cliff noticeable — that it was low enough to hit in
testing. A cursor streams the whole result set in batches, so the work is
complete by construction and memory stays bounded by the batch, not the total.

The ceiling below is therefore **not** a limit on correctness. It is a circuit
breaker against a query that has gone wrong (a missing filter, a runaway
collection), and the entire point of it is that crossing it is **loud**: it logs
at ERROR, names the query, and says exactly how many documents were left
unprocessed. A cap that can be hit silently is the defect; a cap that announces
itself is an operational signal.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Callable, List, Optional

logger = logging.getLogger(__name__)

#: Circuit-breaker ceiling for a platform-wide sweep. Chosen to be far above any
#: plausible real working set (100k open trades, holdings or users) and far below
#: "this process will run out of memory", so reaching it means the query is
#: wrong, not that the platform grew.
DEFAULT_CEILING = 100_000


async def stream(cursor, *, label: str,
                 ceiling: int = DEFAULT_CEILING) -> AsyncIterator[Any]:
    """Yield every document a cursor produces, and shout if the ceiling is hit.

    `label` names the sweep in the log line. It is required rather than optional
    because the whole value of the breaker is that the message identifies which
    query ran away — "fan-out ceiling reached" with no subject is the same
    silence this module exists to remove, one level up.
    """
    seen = 0
    async for doc in cursor:
        seen += 1
        if seen > ceiling:
            logger.error(
                "Fan-out ceiling reached for %s: stopped after %d documents. "
                "Work for every remaining document was NOT performed. This is a "
                "circuit breaker, not a page size — a sweep this large almost "
                "always means the query lost a filter.", label, ceiling)
            return
        yield doc


async def collect(cursor, *, label: str,
                  ceiling: int = DEFAULT_CEILING,
                  key: Optional[Callable[[Any], Any]] = None) -> List[Any]:
    """`stream`, materialised — for callers that need the whole list at once.

    Several sweeps genuinely do: they derive the set of symbols to prefetch from
    the rows before processing any of them, so streaming twice would double the
    database read. `key`, when given, deduplicates as it collects, which is what
    those callers actually wanted from the list.
    """
    out: List[Any] = []
    seen = set()
    async for doc in stream(cursor, label=label, ceiling=ceiling):
        if key is not None:
            k = key(doc)
            if k in seen:
                continue
            seen.add(k)
        out.append(doc)
    return out

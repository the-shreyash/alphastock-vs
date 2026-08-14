"""Performance measurement instruments for the hermetic backend suite (PH3.4).

WHAT THIS MODULE MEASURES, AND WHAT IT DELIBERATELY DOES NOT
-------------------------------------------------------------
The hermetic suite runs with `FakeDB` in place of Mongo, blank credentials and a
socket guard. That makes it a *bad* place to measure wall-clock latency — there
is no database round trip, no TLS handshake and no provider to wait for, so a
timing number produced here describes a machine, not a deployment.

It makes it an unusually *good* place to measure the things that actually
determine latency once those costs are real:

* **How many database operations an endpoint issues.** This is the number that
  multiplies by network round-trip time in production. An endpoint issuing 102
  queries where 3 would do is slow on every deployment, on every machine, at
  every data volume — and the count is perfectly deterministic, so it can be
  asserted in CI without flaking.
* **How many documents an endpoint reads to answer.** An unbounded scan looks
  identical to a bounded one at 4 seeded documents and stops the site at
  400,000. Counting *documents examined* separates them while the collection is
  still small.
* **How large a response is.** Payload bytes cost the user their bandwidth
  regardless of how fast the server produced them.
* **Whether a cache is actually consulted.** A cache that never hits is pure
  overhead plus a staleness risk.

`PH3.4_PERFORMANCE_CERTIFICATION.md` §2 records this boundary explicitly. Where
a metric cannot be obtained in this environment it is reported as unavailable
rather than estimated.

WHY QUERY COUNTS AND NOT TIMINGS FOR THE REGRESSION TESTS
----------------------------------------------------------
A wall-clock assertion (`assert elapsed < 0.05`) fails on a loaded CI runner and
passes on a fast laptop that has just regressed by 40 queries. It measures the
runner. `assert queries == 3` measures the code, and it fails for exactly one
reason: someone added a query. That is the property the brief's §17 asks for, and
it is why the PH3.4 regression tests assert counts, document reads and payload
sizes rather than durations.
"""
from __future__ import annotations

import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from tests import _fakedb


#: Every `FakeCollection` method that corresponds to one server round trip in
#: production. `find` is included even though it is synchronous in the double —
#: in Motor it creates a cursor whose first `to_list`/`async for` is the round
#: trip, so one `find` is one query. Cursor chaining (`sort`/`skip`/`limit`) is
#: NOT counted: those mutate the query, they do not issue another one.
_QUERY_METHODS = (
    "find_one",
    "find",
    "insert_one",
    "insert_many",
    "update_one",
    "update_many",
    "delete_one",
    "delete_many",
    "count_documents",
    "aggregate",
)

#: Index creation is a startup concern, not a request-path one. Counting it
#: would make any measurement taken across app startup meaningless.
_IGNORED_METHODS = ("create_index",)


@dataclass
class QueryLog:
    """The database operations issued inside one measurement window.

    `by_op` is the headline number; `documents_examined` is the one that catches
    an unbounded scan whose query count is a perfectly innocent 1.
    """

    calls: List[tuple] = field(default_factory=list)   # (collection, op)
    documents_examined: int = 0

    @property
    def total(self) -> int:
        return len(self.calls)

    @property
    def by_op(self) -> Dict[str, int]:
        return dict(Counter(op for _, op in self.calls))

    @property
    def by_collection(self) -> Dict[str, int]:
        return dict(Counter(col for col, _ in self.calls))

    def count(self, collection: Optional[str] = None, op: Optional[str] = None) -> int:
        """Operations matching a collection and/or an operation name."""
        return sum(
            1 for col, o in self.calls
            if (collection is None or col == collection) and (op is None or o == op)
        )

    def describe(self) -> str:
        """A one-line summary for a failing assertion's message.

        A bare `assert log.total == 3` that fails tells you the number changed.
        This tells you which collection grew, which is the whole diagnosis.
        """
        parts = ", ".join(f"{col}.{op}" for col, op in self.calls)
        return f"{self.total} queries ({self.documents_examined} docs examined): {parts}"


@contextmanager
def count_queries():
    """Count `FakeDB` operations issued inside the block.

    Patches the methods on `FakeCollection` itself rather than wrapping a
    `FakeDB` instance, because `FakeDB.__getattr__` creates collections lazily:
    an endpoint touching a collection no test has seeded yet would otherwise
    escape a per-instance wrapper. Patching the class catches every collection,
    including ones created mid-request, and including the second handle
    (`broker_engine.db`) that `fake_db` also points at the same double.

    Restored in a `finally` so a failing assertion inside the block cannot leave
    the double instrumented for the rest of the session — that would turn one
    red test into a suite-wide slowdown with no obvious cause.
    """
    log = QueryLog()
    originals = {}

    def wrap(name, fn):
        def recorder(self, *args, **kwargs):
            log.calls.append((getattr(self, "name", "<unnamed>"), name))
            # Documents *examined*, not returned: this is what a collection scan
            # costs. A `find` with no index reads the whole collection whether it
            # returns one document or all of them, so charging the full length is
            # the honest figure and the one that grows with the data.
            log.documents_examined += len(self.docs)
            return fn(self, *args, **kwargs)
        return recorder

    for name in _QUERY_METHODS:
        original = getattr(_fakedb.FakeCollection, name)
        originals[name] = original
        setattr(_fakedb.FakeCollection, name, wrap(name, original))
    try:
        yield log
    finally:
        for name, original in originals.items():
            setattr(_fakedb.FakeCollection, name, original)


@dataclass
class Measurement:
    """One endpoint, everything worth recording about it.

    Two timings, deliberately, because conflating them produces a number that is
    wrong in an important way.

    `cold_seconds` is the very first call in the process. Several handlers in
    `server.py` import their service module *inside the function*
    (`from services import portfolio_engine`), so the first request to each
    endpoint pays that import. An early version of the PH3.4 profiler reported
    only this figure and attributed 288 ms to
    `GET /api/portfolio/intelligence` — which then profiled at ~12 ms warm. The
    288 ms was real, but it was a once-per-process import, not a per-request cost,
    and reporting it as the endpoint's latency would have sent the sprint
    optimising the wrong thing.

    `warm_seconds` is the steady state every request after the first one gets.
    """

    method: str
    path: str
    status: int
    queries: int
    documents_examined: int
    response_bytes: int
    cold_seconds: float
    warm_seconds: float
    log: QueryLog

    def row(self) -> str:
        return (
            f"{self.method:6} {self.path:44} {self.status:4} "
            f"q={self.queries:4} docs={self.documents_examined:6} "
            f"bytes={self.response_bytes:7} "
            f"cold={self.cold_seconds * 1000:8.1f}ms warm={self.warm_seconds * 1000:7.1f}ms"
        )


def measure(client, method: str, path: str, *, repeat: int = 3, **kwargs) -> Measurement:
    """Call an endpoint and record its cost.

    The first call supplies the query count, the document count, the response and
    `cold_seconds`. The query count is taken from the **cold** call on purpose: a
    later call can legitimately issue fewer queries because a cache is warm by
    then, and the cold count is the one that describes a real user arriving on a
    page.

    `warm_seconds` is the **minimum** of the subsequent calls, not the mean. Every
    sample is the true cost plus a non-negative amount of interference from
    whatever else is on this machine, so the minimum is the least-contaminated
    estimate available.

    Rate limiting is the trap with `repeat`: the platform limiter is mounted on
    the real app, so a loop tight enough to be useful can trip it and start
    measuring 429s. Keep `repeat` small and check `status`.
    """
    fn = getattr(client, method.lower())

    with count_queries() as first_log:
        start = time.perf_counter()
        first_response = fn(path, **kwargs)
        cold = time.perf_counter() - start

    warm = None
    for _ in range(max(0, repeat - 1)):
        start = time.perf_counter()
        fn(path, **kwargs)
        elapsed = time.perf_counter() - start
        warm = elapsed if warm is None else min(warm, elapsed)

    return Measurement(
        method=method.upper(),
        path=path,
        status=first_response.status_code,
        queries=first_log.total,
        documents_examined=first_log.documents_examined,
        response_bytes=len(first_response.content),
        cold_seconds=cold,
        warm_seconds=warm if warm is not None else cold,
        log=first_log,
    )

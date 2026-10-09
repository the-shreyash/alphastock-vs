"""Recognising MongoDB's errors without importing the driver (D6.7).

WHY THIS IS ONE SHARED PREDICATE AND NOT FOUR LOCAL COPIES
----------------------------------------------------------
D6.7 made several writes depend on a **unique index doing the rejecting**: the
scheduler's leader election, the broker order identity, the per-symbol holdings
upsert. In each, a duplicate-key error is not a failure — it is the expected,
load-bearing signal that another writer got there first, and the correct
response is to carry on rather than to raise.

Getting that recognition wrong is silent and severe in both directions. Treat a
duplicate key as a hard error and a routine race becomes a 500; treat some
*other* error as a duplicate key and a real write failure is swallowed as "the
other writer did it", which is how a lost order record looks from the outside.

It lives in ``infrastructure/`` because it is a fact about the database driver
and knows nothing about portfolios, orders or leases — the boundary this package
documents. Matching on the numeric code rather than on
``pymongo.errors.DuplicateKeyError`` keeps every caller importable in the
hermetic test suite, whose ``FakeDB`` raises its own exception types and never
loads pymongo at all.
"""
from __future__ import annotations

#: MongoDB's duplicate-key error code. Stable across every server version this
#: platform supports, and the value the driver itself reports in `exc.code`.
DUPLICATE_KEY = 11000


def is_duplicate_key(exc: BaseException) -> bool:
    """True when `exc` is Mongo rejecting a write that violates a unique index."""
    if getattr(exc, "code", None) == DUPLICATE_KEY:
        return True
    # The string fallback covers a driver that wraps the original (a
    # `BulkWriteError`, or a `WriteError` re-raised by a helper) and loses the
    # top-level `code` attribute while keeping the server's message.
    text = str(exc)
    return "E11000" in text or "duplicate key" in text.lower()

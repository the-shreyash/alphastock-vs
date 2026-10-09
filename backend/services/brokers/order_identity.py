"""The uniqueness of a broker order record (D6.7 / Phase 4).

THE IDENTITY, AND WHY IT IS NOT `(user_id, broker, order_id)`
------------------------------------------------------------
A broker order id is a sequence issued **by one brokerage account**. It is
unique within that account and nowhere else: two Zerodha accounts held by the
same person can legitimately both be handed order `250910000123456`.

D6.4 made `broker_account_id` the authoritative routing identity, and
`BrokerEngine._record_order` upserts on `(broker_account_id, order_id)`
accordingly. What was missing until D6.7 is the **constraint**: the index
`ensure_indexes()` declared for that pair was not unique, so the upsert's filter
was a convention the database did not enforce.

That gap is exactly the shape of race this sprint exists to close. `update_one`
with `upsert=True` is not atomic against a concurrent insert of the same key
unless a unique index exists — both callers find nothing, both insert, and the
collection ends up with **two authoritative rows for one real broker order**.
Every path that reads an order back (`GET /api/orders`, the trade journal, the
order-status stream) then sees a duplicate it has no way to reconcile, and the
two rows can disagree, because each was written from a different snapshot of the
broker's answer.

Three concurrent writers actually exist:

* `sync_orders`, looping the broker's whole order book;
* `_on_stream_order`, driven by the broker's realtime order feed;
* `place_order`, recording its own acknowledgement.

A fill that arrives while a sync is running drives the first two simultaneously
against the same key. This is not a hypothetical interleaving.

WHY THE INDEX IS BUILT DEFENSIVELY RATHER THAN ASSUMED
------------------------------------------------------
`create_index(..., unique=True)` fails if the collection already violates the
constraint — which is precisely the state the missing constraint allowed. The
brief for this work is explicit that production order records are **never** to
be deleted to make an index build, and that is the right rule: two rows for one
order is a fact about a real brokerage account, and collapsing them
automatically would destroy the evidence of which one the broker actually sent.

So this module never merges and never deletes. It:

1. tries to build the unique index;
2. on failure, finds and **reports** every offending key so a person can
   reconcile them;
3. guarantees the non-unique index still exists, so reads stay fast while the
   constraint is absent.

The result is honest in both directions: a clean collection gets the constraint,
and a dirty one gets a loud, specific, actionable error instead of either a
silent skip or a destructive fix.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

#: The logical identity of a broker order record.
ORDER_IDENTITY_KEYS = [("broker_account_id", 1), ("order_id", 1)]

#: Name pinned so the unique and non-unique variants cannot coexist under
#: Mongo's auto-generated `broker_account_id_1_order_id_1`. Two indexes on one
#: key pattern is how a dropped constraint stays invisible: the queries still
#: use an index, so nothing gets slower and nothing complains.
ORDER_IDENTITY_INDEX = "order_identity_unique"

#: The same argument, for the holdings collection. `BrokerEngine._replace_holdings`
#: upserts one row per `(broker_account_id, symbol)` and relies on the unique
#: index to make that upsert atomic against a concurrent sync of the same
#: account — without it, two syncs both find nothing for a symbol and both
#: insert, which is the duplication the generation ordering exists to prevent.
HOLDING_IDENTITY_KEYS = [("broker_account_id", 1), ("symbol", 1)]
HOLDING_IDENTITY_INDEX = "holding_identity_unique"


@dataclass
class OrderIndexReport:
    """What `ensure_order_identity_index` actually achieved."""

    unique: bool = False
    duplicates: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.unique

    def as_dict(self) -> Dict[str, Any]:
        return {"unique": self.unique, "duplicate_keys": len(self.duplicates),
                "duplicates": self.duplicates[:20], "error": self.error}


async def find_duplicate_orders(db, limit: int = 100) -> List[Dict[str, Any]]:
    """Every `(broker_account_id, order_id)` carrying more than one row.

    Read-only. Rows whose `broker_account_id` is absent are excluded: those are
    pre-D6.4 records the account migration could not name, they are not part of
    the identity this index constrains, and the partial filter below exempts
    them for the same reason.
    """
    pipeline = [
        {"$match": {"broker_account_id": {"$exists": True},
                    "order_id": {"$exists": True}}},
        {"$group": {"_id": {"broker_account_id": "$broker_account_id",
                            "order_id": "$order_id"},
                    "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    try:
        rows = await db.orders.aggregate(pipeline).to_list(limit)
    except Exception as e:
        logger.warning("Could not scan db.orders for duplicate identities: %s", e)
        return []
    return [{"broker_account_id": r["_id"].get("broker_account_id"),
             "order_id": r["_id"].get("order_id"),
             "count": r.get("count")} for r in rows]


async def ensure_order_identity_index(db) -> OrderIndexReport:
    """Build the unique constraint on `(broker_account_id, order_id)`.

    PARTIAL, on both fields existing. Mongo treats a missing field and a null as
    one value in a unique index, so without the filter every pre-D6.4 order the
    account migration could not name would collide with every other one — and a
    constraint that cannot be satisfied by legacy data is a constraint that
    never gets deployed.

    Never raises. A database that refuses the index must not stop the process
    booting: the upsert filter is still correct, the race is still narrow, and a
    backend that will not start is a worse outcome than a constraint that is
    reported missing.
    """
    report = OrderIndexReport()
    try:
        await db.orders.create_index(
            ORDER_IDENTITY_KEYS, unique=True, name=ORDER_IDENTITY_INDEX,
            partialFilterExpression={"broker_account_id": {"$exists": True},
                                     "order_id": {"$exists": True}})
        report.unique = True
        return report
    except Exception as e:
        report.error = str(e)

    # The build failed. The overwhelmingly likely reason is existing duplicates,
    # so name them — an operator cannot reconcile what the log will not identify.
    report.duplicates = await find_duplicate_orders(db)
    if report.duplicates:
        logger.error(
            "db.orders holds %d broker order identities with more than one row, "
            "so the unique index (%s) could not be built. These are REAL ORDER "
            "RECORDS and are deliberately NOT merged or deleted here — each "
            "duplicate must be reconciled by a person. Offending keys (first "
            "20): %s", len(report.duplicates), ORDER_IDENTITY_INDEX,
            report.duplicates[:20])
    else:
        logger.error(
            "The unique index %s on db.orders could not be built and no "
            "duplicate identities were found: %s", ORDER_IDENTITY_INDEX,
            report.error)

    # Fall back to the non-unique index so reads keep their access path while
    # the constraint is absent. Distinctly named, so the presence of the unique
    # one is never inferred from "an index on those keys exists".
    try:
        await db.orders.create_index(ORDER_IDENTITY_KEYS,
                                     name="order_identity_nonunique")
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Could not build the fallback order identity index: %s", e)
    return report


async def find_duplicate_holdings(db, limit: int = 100) -> List[Dict[str, Any]]:
    """Every `(broker_account_id, symbol)` carrying more than one row."""
    pipeline = [
        {"$match": {"broker_account_id": {"$exists": True},
                    "symbol": {"$exists": True}}},
        {"$group": {"_id": {"broker_account_id": "$broker_account_id",
                            "symbol": "$symbol"},
                    "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    try:
        rows = await db.holdings.aggregate(pipeline).to_list(limit)
    except Exception as e:
        logger.warning("Could not scan db.holdings for duplicate identities: %s", e)
        return []
    return [{"broker_account_id": r["_id"].get("broker_account_id"),
             "symbol": r["_id"].get("symbol"),
             "count": r.get("count")} for r in rows]


async def ensure_holding_identity_index(db) -> OrderIndexReport:
    """Build the unique constraint on `(broker_account_id, symbol)`.

    Deliberately NOT the same posture as `ensure_order_identity_index`, and the
    difference is the point.

    An order record is a permanent, irreplaceable statement about something that
    happened in a real brokerage account; a duplicate must be reconciled by a
    person and this code refuses to touch it. A holding row is a **cache of the
    broker's current position book**, rewritten in full on every sync. A
    duplicate there is a transient artifact of the very race being fixed, it
    carries no history, and the authoritative copy is one API call away — so
    collapsing duplicates to the newest row is safe, and leaving them is not:
    they double the portfolio value the user is shown.

    Still never silent. What was collapsed is logged with its keys.
    """
    report = OrderIndexReport()
    try:
        await db.holdings.create_index(
            HOLDING_IDENTITY_KEYS, unique=True, name=HOLDING_IDENTITY_INDEX,
            partialFilterExpression={"broker_account_id": {"$exists": True},
                                     "symbol": {"$exists": True}})
        report.unique = True
        return report
    except Exception as e:
        report.error = str(e)

    report.duplicates = await find_duplicate_holdings(db)
    if not report.duplicates:
        logger.error("The unique index %s on db.holdings could not be built and "
                     "no duplicates were found: %s",
                     HOLDING_IDENTITY_INDEX, report.error)
        return report

    logger.warning(
        "db.holdings holds %d duplicated (account, symbol) rows — the signature "
        "of the pre-D6.7 delete-then-insert sync. Collapsing each to its newest "
        "row so the unique index can build; the position book is re-fetched from "
        "the broker on the next sync, so nothing authoritative is lost. Keys: %s",
        len(report.duplicates), report.duplicates[:20])
    for dup in report.duplicates:
        try:
            rows = await db.holdings.find(
                {"broker_account_id": dup["broker_account_id"],
                 "symbol": dup["symbol"]}).sort("updated_at", -1).to_list(1000)
            for stale in rows[1:]:
                await db.holdings.delete_one({"_id": stale["_id"]})
        except Exception as e:  # pragma: no cover - defensive
            logger.error("Could not collapse duplicate holding %s: %s", dup, e)

    try:
        await db.holdings.create_index(
            HOLDING_IDENTITY_KEYS, unique=True, name=HOLDING_IDENTITY_INDEX,
            partialFilterExpression={"broker_account_id": {"$exists": True},
                                     "symbol": {"$exists": True}})
        report.unique = True
        report.error = ""
    except Exception as e:
        report.error = str(e)
        logger.error("The unique index %s on db.holdings still could not be "
                     "built after collapsing duplicates: %s",
                     HOLDING_IDENTITY_INDEX, e)
    return report

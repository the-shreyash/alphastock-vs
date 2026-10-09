"""Backfill of `broker_account_id` onto pre-D6.4 data.

WHAT HAS TO BE TRUE FOR THIS TO BE SAFE
---------------------------------------
Before D6.4 the identity of a brokerage account was ``(user_id, broker)``, and
``db.broker_accounts`` carried a **unique index on exactly that pair**. That
index is the reason this migration can be deterministic at all: it guarantees
that every legacy account maps to exactly one document, so "which account does
this order belong to" has one answer and never needs to be chosen.

The migration therefore does not merge, does not pick, and does not guess. Where
the invariant does not hold — a deployment whose index was dropped, leaving two
rows for one pair — the pair is **skipped and reported**, and every row that
references it is left untouched (D6.4 / §4: "If ambiguity exists: STOP and
report it").

WHAT IT DOES
------------
1. Every ``broker_accounts`` document without a ``broker_account_id`` gets one
   minted, plus the fields the new model needs: ``external_account_id`` lifted
   from the session's own ``account_id`` (which every adapter has written since
   D3), ``status`` derived from the legacy ``connected`` boolean, and
   ``created_at``.
2. Every collection that referenced an account only as ``(user_id, broker)`` —
   ``orders``, ``holdings``, ``portfolios``, ``trades`` — gets
   ``broker_account_id`` stamped onto the rows that match a resolved account and
   do not already carry one.

PROPERTIES
----------
*Deterministic* — the (user_id, broker) → document mapping is 1:1 or the pair is
skipped. *Idempotent* — every step filters on the field being absent, so a second
run is a no-op and a partially completed run resumes exactly where it stopped.
*Non-destructive* — it only adds fields; the legacy ``broker`` and ``connected``
fields are left in place, so a rollback is a deploy and not a data restore.
*Auditable* — it returns a report and writes one ``schema_migrations`` row per
completed run.

WHY THERE IS NO MIGRATION FRAMEWORK
-----------------------------------
This repository has no Alembic-equivalent; schema work has always been done in
``ensure_indexes`` at startup (``server.py``). This module follows that
convention rather than introducing a second one, and runs from the same place —
before the broker engine restores any session, because a session restored against
an unmigrated document would be keyed by an id that does not exist yet.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from services.brokers.accounts import (
    BrokerAccountStatus,
    _clean_external,
    new_broker_account_id,
)

logger = logging.getLogger(__name__)

#: Identifier written to `db.schema_migrations` when a run completes.
MIGRATION_ID = "d64_broker_account_identity"

#: Collections whose rows named an account as `(user_id, broker)` and now also
#: carry `broker_account_id`.
#:
#: `positions` is absent because this platform never persisted positions — they
#: are fetched live on every sync (`BrokerEngine.sync_portfolio`). `paper_trades`
#: is absent because a paper trade has no brokerage account by definition, and
#: stamping one on would assert a link that does not exist. `activity` and
#: `audit_logs` are absent because they are append-only history: a historical row
#: describes what was true when it was written, and back-dating an identity onto
#: it would rewrite the record rather than migrate it.
REFERENCING_COLLECTIONS: Tuple[str, ...] = ("orders", "holdings", "portfolios", "trades")


@dataclass
class MigrationReport:
    """What a run did. Returned, logged, and persisted."""

    accounts_scanned: int = 0
    accounts_migrated: int = 0
    accounts_already_migrated: int = 0
    #: `(user_id, broker)` pairs with more than one document. Never resolved.
    ambiguous: List[Dict[str, Any]] = field(default_factory=list)
    #: collection -> rows stamped.
    rows_stamped: Dict[str, int] = field(default_factory=dict)
    #: Non-fatal problems; the run continues past each one.
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.ambiguous and not self.errors

    def as_dict(self) -> Dict[str, Any]:
        return {
            "migration": MIGRATION_ID,
            "accounts_scanned": self.accounts_scanned,
            "accounts_migrated": self.accounts_migrated,
            "accounts_already_migrated": self.accounts_already_migrated,
            "ambiguous": list(self.ambiguous),
            "rows_stamped": dict(self.rows_stamped),
            "errors": list(self.errors),
            "ok": self.ok,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_for(doc: Dict[str, Any]) -> str:
    """The lifecycle state a legacy document maps to.

    The legacy model had one boolean, `connected`. A row with it false is
    `DISCONNECTED`; there is deliberately no attempt to infer `REAUTH_REQUIRED`
    from an expired `expires_at`, because an expired token on a *connected* row
    is exactly the state the engine already re-derives on its next call, and
    guessing it here would put the account into a state the user never saw.
    """
    return (BrokerAccountStatus.CONNECTED if doc.get("connected", False)
            else BrokerAccountStatus.DISCONNECTED)


async def migrate_broker_accounts(db) -> MigrationReport:
    """Assign `broker_account_id` to every legacy account and its rows.

    Safe to call on an empty database, on already-migrated data, on a partially
    migrated database, and concurrently with a running application: every write
    is a targeted `$set` of fields that were absent.
    """
    report = MigrationReport()
    if db is None:
        return report

    try:
        docs = await db.broker_accounts.find({}).to_list(10000)
    except Exception as e:  # pragma: no cover - defensive
        report.errors.append(f"broker_accounts scan failed: {e}")
        logger.error("D6.4 migration could not read broker_accounts: %s", e)
        return report

    report.accounts_scanned = len(docs)

    # Group first, migrate second. The grouping is what detects the ambiguity
    # this migration refuses to resolve; migrating as we scan would have written
    # an id onto the first of two rival documents before noticing the second.
    by_pair: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for doc in docs:
        user_id, broker = doc.get("user_id"), doc.get("broker")
        if not user_id or not broker:
            report.errors.append(
                f"broker_accounts document {doc.get('_id')} has no user_id/broker; skipped")
            continue
        by_pair.setdefault((str(user_id), str(broker)), []).append(doc)

    resolved = await _assign_account_ids(db, by_pair, report)
    await _stamp_referencing_rows(db, resolved, report)

    if report.accounts_migrated or report.rows_stamped:
        logger.info("D6.4 broker account migration: %s", report.as_dict())

    try:
        await db.schema_migrations.update_one(
            {"migration": MIGRATION_ID},
            {"$set": {"migration": MIGRATION_ID, "last_run": _now_iso(),
                      "report": report.as_dict()}},
            upsert=True)
    except Exception as e:  # pragma: no cover - reporting only
        logger.warning("Could not record the D6.4 migration run: %s", e)

    return report


async def _assign_account_ids(
    db,
    by_pair: Dict[Tuple[str, str], List[Dict[str, Any]]],
    report: MigrationReport,
) -> Dict[Tuple[str, str], str]:
    """Give every legacy account an id. Returns `(user, broker) -> id`.

    Split out from :func:`migrate_broker_accounts` so each pass stays inside the
    complexity ceiling and — more usefully — so the ambiguity refusal is a
    branch in one readable function rather than one arm of a loop that is also
    doing row stamping.
    """
    resolved: Dict[Tuple[str, str], str] = {}
    for (user_id, broker), group in sorted(by_pair.items()):
        if len(group) > 1:
            # Two documents claiming one legacy identity. Merging them would
            # join two credential sets and two portfolio histories under one id;
            # picking one would silently orphan the other's orders. Neither is a
            # migration, so the pair is reported and every row referencing it is
            # left exactly as it is.
            report.ambiguous.append({
                "user_id": user_id, "broker": broker, "documents": len(group),
                "broker_account_ids": [d.get("broker_account_id") for d in group],
            })
            logger.error(
                "D6.4 migration: user %s has %d %s account documents — refusing "
                "to merge or choose. Resolve manually before these accounts can "
                "be used.", user_id, len(group), broker)
            continue

        doc = group[0]
        existing_id = doc.get("broker_account_id")
        if existing_id:
            report.accounts_already_migrated += 1
            resolved[(user_id, broker)] = str(existing_id)
            continue

        account_id = new_broker_account_id()
        # The external identity was already on the row: every adapter's
        # `exchange_token` returns `account_id`, and `_save_account` stored the
        # session dict wholesale. Where it is absent the account is recorded as
        # unverified rather than invented — `BrokerAccountDirectory.link` fills
        # it in on the next successful login, on this same row.
        external = _clean_external(doc.get("account_id"))
        update = {
            "broker_account_id": account_id,
            "external_account_id": external,
            "external_identity_verified": bool(external),
            "status": _status_for(doc),
            "created_at": doc.get("connected_at") or _now_iso(),
            "updated_at": _now_iso(),
        }
        try:
            await db.broker_accounts.update_one(
                {"_id": doc.get("_id")}, {"$set": update})
        except Exception as e:
            report.errors.append(
                f"could not assign an id to the {broker} account of user {user_id}: {e}")
            continue
        resolved[(user_id, broker)] = account_id
        report.accounts_migrated += 1
    return resolved


async def _stamp_referencing_rows(
    db,
    resolved: Dict[Tuple[str, str], str],
    report: MigrationReport,
) -> None:
    """Stamp `broker_account_id` onto the rows that named an account by pair.

    Only pairs in `resolved` are touched, which is what keeps an ambiguous
    account's rows untouched: an unresolved pair is not in the mapping, so
    nothing that references it is stamped with a guess.
    """
    for collection in REFERENCING_COLLECTIONS:
        stamped = 0
        for (user_id, broker), account_id in resolved.items():
            try:
                result = await db[collection].update_many(
                    {"user_id": user_id, "broker": broker,
                     "broker_account_id": {"$exists": False}},
                    {"$set": {"broker_account_id": account_id}})
            except Exception as e:
                report.errors.append(f"{collection} backfill failed: {e}")
                break
            stamped += getattr(result, "modified_count", 0) or 0
        if stamped:
            report.rows_stamped[collection] = stamped

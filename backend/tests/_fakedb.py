"""Minimal in-memory async MongoDB double for hermetic backend tests.

Supports just enough of the Motor API surface used by the AlphaPartner
endpoints/services under test (find_one, find().sort().skip().limit().to_list(),
insert_one, update_one, update_many, delete_one/many, count_documents,
aggregate, list_collection_names, command) so that DB-backed FastAPI routes can
run under a synchronous TestClient without a real Mongo connection (and without
Motor's per-request event-loop binding).

**Not a general Mongo implementation** — only the operators the routes actually
use. That is a deliberate limit, not an oversight: a half-correct reimplementation
of the full query language is a worse test double than an obviously partial one,
because the half that is wrong looks like it works.

PH3.3 extended the double along three axes, each because a route under test
needed it and the alternative was leaving that route untested:

* **Result objects grew `deleted_count`.** `DELETE /api/watchlist/{symbol}`
  branches on it. The old `_Result` had no such attribute, so the route raised
  `AttributeError` → 500 under test, which reads as an application bug and is not
  one.
* **Cursors grew `skip`/`limit` and collections grew `aggregate`,
  `list_collection_names` and `command`.** Every paginated admin endpoint uses
  them; without them the whole admin surface was unreachable from a hermetic
  test.
* **`_match` grew `$or`, `$in`, `$nin`, `$regex`, `$lt`, `$gt`.** These were not
  merely missing — they were silently *ignored*, which is the dangerous failure:
  an unsupported operator inside a `dict` condition fell through every branch and
  the document matched. A filter meant to narrow a result set widened it to
  everything, and any test asserting a count over such a filter would have agreed
  with the wrong answer.

**Unsupported operators now raise** (`UnsupportedQuery`) rather than matching
everything. The next person to use an operator this double does not model gets a
loud, named failure in their own test instead of a silently wrong count in
someone else's.
"""
import copy
import re

from bson import ObjectId


class UnsupportedQuery(AssertionError):
    """A test drove the double with a query operator it does not implement.

    Deliberately an `AssertionError`: this is a defect in the *test setup* (or a
    signal that the double needs extending), never a production condition, and it
    must never be mistaken for an application error the route should handle.
    """


#: Operators `_match` understands inside a field condition. Anything else raises.
_SUPPORTED_FIELD_OPS = frozenset({
    "$ne", "$exists", "$gte", "$lte", "$gt", "$lt", "$in", "$nin", "$regex",
    "$options", "$not",
})


def _mongo_eq(value, target):
    """MongoDB's equality semantics, including the ARRAY rule (D6.7).

    In MongoDB a filter `{tags: "x"}` matches a document whose `tags` is the
    string `"x"` *and* one whose `tags` is a list containing `"x"`. The
    consequence that matters here is the negation: `{tags: {$ne: "x"}}` does NOT
    match `{tags: ["x"]}`.

    This double compared with a plain `==`, so `["SL"] == "SL"` was False and a
    document that Mongo excludes was reported as matching. That is invisible for
    every scalar field in the repository and load-bearing for exactly one
    pattern: the compare-and-swap claim in `services/trading_engine.claim_exit`,
    whose entire safety argument is that `{$ne: claim}` stops matching once the
    claim is in the array. Under the old comparison the second caller matched
    too, so a test of the guard would have passed with the guard removed.
    """
    if isinstance(value, list) and not isinstance(target, list):
        return value == target or target in value
    return value == target


def _match_field(value, cond):
    """Evaluate one `{field: {<op>: ...}}` condition against a document value."""
    unknown = set(cond) - _SUPPORTED_FIELD_OPS
    if unknown:
        raise UnsupportedQuery(
            f"FakeDB does not implement {sorted(unknown)}. Extend tests/_fakedb.py "
            f"rather than asserting against an unmodelled operator."
        )
    if "$ne" in cond and _mongo_eq(value, cond["$ne"]):
        return False
    if "$exists" in cond and (value is not None) != bool(cond["$exists"]):
        return False
    for op, cmp in (("$gte", lambda a, b: a >= b), ("$lte", lambda a, b: a <= b),
                    ("$gt", lambda a, b: a > b), ("$lt", lambda a, b: a < b)):
        if op in cond:
            if value is None:
                return False
            try:
                if not cmp(value, cond[op]):
                    return False
            except TypeError:
                return False
    if "$in" in cond and value not in cond["$in"]:
        return False
    if "$nin" in cond and value in cond["$nin"]:
        return False
    if "$regex" in cond:
        if not isinstance(value, str):
            return False
        flags = re.IGNORECASE if "i" in (cond.get("$options") or "") else 0
        if not re.search(cond["$regex"], value, flags):
            return False
    return True


def _match(doc, flt):
    """Return True if `doc` satisfies the (subset-of-Mongo) filter `flt`."""
    for key, cond in (flt or {}).items():
        if key == "$or":
            if not any(_match(doc, sub) for sub in cond):
                return False
        elif key == "$and":
            if not all(_match(doc, sub) for sub in cond):
                return False
        elif key == "_id":
            # Compared as strings so a test may seed either an ObjectId or its
            # string form and have both `find_one({"_id": oid})` and
            # `find_one({"_id": str(oid)})` behave the same way. Real Mongo is
            # stricter; routes here normalize before querying.
            if isinstance(cond, dict):
                if not _match_field(str(doc.get("_id")), cond):
                    return False
            elif str(doc.get("_id")) != str(cond):
                return False
        elif isinstance(cond, dict):
            if not _match_field(doc.get(key), cond):
                return False
        else:
            if not _mongo_eq(doc.get(key), cond):
                return False
    return True


def _project(doc, projection):
    """Apply a Mongo projection ({field: 1} include / {field: 0} exclude).

    `_id` follows Mongo's rule: included by default, and exemptable from an
    otherwise-inclusive projection with `{"_id": 0}`.
    """
    if not projection:
        return dict(doc)
    fields = {k: v for k, v in projection.items() if k != "_id"}
    if fields and all(not v for v in fields.values()):          # exclusion
        out = {k: v for k, v in doc.items() if k not in fields}
    elif fields:                                                 # inclusion
        out = {k: v for k, v in doc.items() if k in fields}
        out["_id"] = doc.get("_id")
    else:
        out = dict(doc)
    if projection.get("_id") == 0:
        out.pop("_id", None)
    return out


class _Result:
    def __init__(self, inserted_id=None, modified=0, matched=0, deleted=0):
        self.inserted_id = inserted_id
        self.modified_count = modified
        self.matched_count = matched
        # Motor's DeleteResult exposes `deleted_count`; routes branch on it
        # (e.g. watchlist removal returning 404). Present on every result object
        # so a caller never has to know which operation produced it.
        self.deleted_count = deleted
        self.upserted_id = None


class _DeleteResult:
    """pymongo's `DeleteResult` shape: `deleted_count` and nothing else.

    D6.8. Deletes used to return `_Result`, which also carries
    `modified_count`. Real `DeleteResult` has no such attribute, so production
    code that read `modified_count` after a delete got 0 against MongoDB and the
    true count against this double — `delete_conversation` did exactly that.
    A double that answers a question the driver cannot is one that agrees with
    the bug.
    """

    def __init__(self, deleted):
        self.deleted_count = deleted
        self.acknowledged = True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=-1):
        try:
            self._docs = sorted(
                self._docs, key=lambda d: (d.get(key) is None, d.get(key)),
                reverse=(direction < 0),
            )
        except TypeError:
            pass
        return self

    def skip(self, n):
        # Mongo rejects a negative skip with an OperationFailure rather than
        # treating it as zero. The double must do the same, or a route that
        # computes `skip` from an unvalidated `page` parameter looks safe here
        # and 500s in production. See PH3.3 defect D-1.
        if n < 0:
            raise UnsupportedQuery("skip must be non-negative (Mongo raises here too)")
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        if n < 0:
            raise UnsupportedQuery("limit must be non-negative (Mongo raises here too)")
        if n:
            self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return [dict(d) for d in (self._docs[:length] if length else self._docs)]

    async def __aiter__(self):
        """Async iteration, as Motor cursors support.

        Services that fan out over a large collection (e.g. the morning-report
        notification sweep) stream with `async for` rather than loading every
        document into memory, so the double has to speak the same protocol.
        """
        for d in self._docs:
            yield dict(d)


class FakeCollection:
    def __init__(self, docs=None, name="<unnamed>"):
        self.docs = [dict(d) for d in (docs or [])]
        #: The attribute this collection was reached through (`db.trades` -> "trades").
        #: Motor collections know their own name; this double did not, which made it
        #: impossible for `tests/_perf.py` to report *which* collection an endpoint
        #: over-queried — the counts alone say "102 find_one calls" without saying
        #: where to look. Set by `FakeDB`, defaulted here so a directly-constructed
        #: collection (several tests do that) still has the attribute.
        self.name = name

    async def find_one(self, flt=None, projection=None):
        for d in self.docs:
            if _match(d, flt or {}):
                return _project(d, projection)
        return None

    def find(self, flt=None, projection=None):
        return _Cursor([_project(d, projection) for d in self.docs if _match(d, flt or {})])

    async def insert_one(self, doc):
        d = dict(doc)
        d.setdefault("_id", ObjectId())
        self.docs.append(d)
        return _Result(inserted_id=d["_id"])

    async def insert_many(self, docs):
        ids = []
        for doc in docs:
            r = await self.insert_one(doc)
            ids.append(r.inserted_id)
        return _Result(inserted_id=ids)

    async def update_one(self, flt, update, upsert=False):
        for d in self.docs:
            if _match(d, flt or {}):
                self._apply_update(d, update)
                return _Result(modified=1, matched=1)
        if upsert:
            newd = {}
            _id = (flt or {}).get("_id")
            if _id is not None:
                newd["_id"] = _id if isinstance(_id, ObjectId) else ObjectId(str(_id))
            for k, v in (flt or {}).items():
                if k != "_id" and not isinstance(v, dict):
                    newd[k] = v
            self._apply_update(newd, update)
            self.docs.append(newd)
        return _Result(modified=0, matched=0)

    async def replace_one(self, flt, replacement, upsert=False):
        """Motor's `replace_one` (D6.9).

        Added because production started depending on it, and for the reason it
        did: `update_one` with `$set` MERGES, so re-persisting a document whose
        new shape has fewer keys leaves the previous version's keys standing
        beside the new ones. For the morning report that meant a failed
        regeneration left yesterday's sections and `available: true` sitting
        next to a `status: failed` provenance record — a partial artifact
        presented as a complete one. A double that only offers `$set` would let
        that defect pass its tests.

        `_id` is preserved across the replacement, as MongoDB does.
        """
        for d in self.docs:
            if _match(d, flt or {}):
                existing_id = d.get("_id")
                d.clear()
                d.update(copy.deepcopy(replacement))
                d.pop("_id", None)
                if existing_id is not None:
                    d["_id"] = existing_id
                return _Result(modified=1, matched=1)
        if upsert:
            newd = copy.deepcopy(replacement)
            newd.setdefault("_id", ObjectId())
            self.docs.append(newd)
        return _Result(modified=0, matched=0)

    async def find_one_and_update(self, flt, update, upsert=False,
                                  return_document=False, projection=None,
                                  sort=None):
        """Motor's `find_one_and_update` (D6.7).

        Added because production started depending on it, and for the reason it
        did: `$inc` followed by a separate `find_one` is a read-modify-write,
        so a caller that needs the value its OWN increment produced has to get
        it from the same operation. A double that cannot express that forces the
        code under test back into the racy shape.

        `return_document` follows pymongo's `ReturnDocument`, whose `AFTER` is
        literally `True` and `BEFORE` literally `False`.
        """
        for d in self.docs:
            if _match(d, flt or {}):
                before = dict(d)
                self._apply_update(d, update)
                chosen = d if return_document else before
                return _project(dict(chosen), projection)
        if not upsert:
            return None
        newd = {}
        _id = (flt or {}).get("_id")
        if _id is not None:
            newd["_id"] = _id if isinstance(_id, ObjectId) else ObjectId(str(_id))
        for k, v in (flt or {}).items():
            if k != "_id" and not isinstance(v, dict):
                newd[k] = v
        self._apply_update(newd, update)
        self.docs.append(newd)
        # An upsert that inserted has no "before" document; Mongo returns None
        # for BEFORE and the new document for AFTER.
        return _project(dict(newd), projection) if return_document else None

    async def update_many(self, flt, update):
        n = 0
        for d in self.docs:
            if _match(d, flt or {}):
                self._apply_update(d, update)
                n += 1
        return _Result(modified=n, matched=n)

    @staticmethod
    def _apply_update(doc, update):
        if "$set" in update:
            doc.update(update["$set"])
        if "$unset" in update:
            for k in update["$unset"]:
                doc.pop(k, None)
        if "$inc" in update:
            for k, v in update["$inc"].items():
                doc[k] = doc.get(k, 0) + v
        if "$push" in update:
            for k, v in update["$push"].items():
                doc.setdefault(k, []).append(v)
        if "$addToSet" in update:
            for k, v in update["$addToSet"].items():
                bucket = doc.setdefault(k, [])
                if v not in bucket:
                    bucket.append(v)

    async def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if _match(d, flt or {}):
                del self.docs[i]
                return _DeleteResult(1)
        return _DeleteResult(0)

    async def delete_many(self, flt):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, flt or {})]
        return _DeleteResult(before - len(self.docs))

    async def distinct(self, key, flt=None):
        """Motor's `distinct`, including its filter argument (D5.15).

        Added because the double did not have it at all, which made every code
        path that calls it fall into its caller's `except` branch and return a
        degraded-but-plausible answer. That is the worst shape a test double can
        take: `heartbeat_engine._user_price_symbols` would have "passed" a
        scoping test by raising, and the assertion that one account's watchlist
        stays out of another's price payload could not have failed.

        Order follows first appearance so a caller that renders the result is
        deterministic under test; Mongo makes no ordering promise, and no caller
        may rely on one.
        """
        seen = []
        for doc in self.docs:
            if not _match(doc, flt or {}):
                continue
            value = doc.get(key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                if item is not None and item not in seen:
                    seen.append(item)
        return seen

    async def count_documents(self, flt=None):
        return len([d for d in self.docs if _match(d, flt or {})])

    async def create_index(self, *args, **kwargs):
        return None

    def aggregate(self, pipeline):
        """Evaluate a pipeline built from `$match`, `$group`, `$sort`, `$limit`.

        Only `$sum` accumulators are modelled, because that is the whole of what
        the admin analytics and webhook-log endpoints use. Anything else raises
        rather than returning a plausible-looking wrong answer.
        """
        docs = [dict(d) for d in self.docs]
        for stage in pipeline or []:
            (op, spec), = stage.items()
            if op == "$match":
                docs = [d for d in docs if _match(d, spec)]
            elif op == "$group":
                docs = self._group(docs, spec)
            elif op == "$sort":
                for key, direction in reversed(list(spec.items())):
                    docs = sorted(docs, key=lambda d: (d.get(key) is None, d.get(key)),
                                  reverse=(direction < 0))
            elif op == "$limit":
                docs = docs[:spec]
            elif op == "$skip":
                docs = docs[spec:]
            else:
                raise UnsupportedQuery(f"FakeDB aggregate does not implement {op}")
        return _Cursor(docs)

    @staticmethod
    def _group(docs, spec):
        key_spec = spec["_id"]
        groups = {}
        for d in docs:
            key = (d.get(key_spec[1:]) if isinstance(key_spec, str) and key_spec.startswith("$")
                   else key_spec)
            acc = groups.setdefault(key, {"_id": key})
            for field, accumulator in spec.items():
                if field == "_id":
                    continue
                (fn, arg), = accumulator.items()
                if fn != "$sum":
                    raise UnsupportedQuery(f"FakeDB aggregate does not implement {fn}")
                value = (d.get(arg[1:], 0) or 0) if isinstance(arg, str) and arg.startswith("$") else arg
                acc[field] = acc.get(field, 0) + value
        return list(groups.values())


class FakeDB:
    """Attribute access (db.users, db.trades, ...) lazily creates collections."""

    def __init__(self, **collections):
        object.__setattr__(self, "_collections", {})
        for name, docs in collections.items():
            self._collections[name] = FakeCollection(docs, name=name)

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_collections")
        if name not in cols:
            cols[name] = FakeCollection(name=name)
        return cols[name]

    def __getitem__(self, name):
        return getattr(self, name)

    async def list_collection_names(self):
        """Only collections that actually hold a document, matching Mongo:
        a collection is created lazily on first insert, and routes branch on
        its *absence* (e.g. admin payments returning an empty page)."""
        return [name for name, col in self._collections.items() if col.docs]

    async def command(self, name, *args, **kwargs):
        """Answer the two admin/health commands the routes issue."""
        if name == "ping":
            return {"ok": 1}
        if name == "dbStats":
            return {
                "db": "stockassist_pytest",
                "collections": len(self._collections),
                "dataSize": 0,
                "storageSize": 0,
                "indexes": 0,
                "objects": sum(len(c.docs) for c in self._collections.values()),
            }
        raise UnsupportedQuery(f"FakeDB does not implement db.command({name!r})")

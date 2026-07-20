"""Minimal in-memory async MongoDB double for hermetic backend tests.

Supports just enough of the Motor API surface used by the AlphaPartner
endpoints/services under test (find_one, find().sort().to_list(),
insert_one, update_one, update_many, aggregate for webhook logs) so that
DB-backed FastAPI routes can run under a synchronous TestClient without a
real Mongo connection (and without Motor's per-request event-loop binding).
Not a general Mongo implementation — only the operators the tests exercise.
"""
from bson import ObjectId


def _match(doc, flt):
    """Return True if `doc` satisfies the (subset-of-Mongo) filter `flt`."""
    for key, cond in (flt or {}).items():
        if key == "_id":
            if str(doc.get("_id")) != str(cond):
                return False
        elif isinstance(cond, dict):
            if "$ne" in cond and doc.get(key) == cond["$ne"]:
                return False
            if "$exists" in cond and (key in doc) != cond["$exists"]:
                return False
            if "$gte" in cond:
                val = doc.get(key)
                if val is None or val < cond["$gte"]:
                    return False
            if "$lte" in cond:
                val = doc.get(key)
                if val is None or val > cond["$lte"]:
                    return False
        else:
            if doc.get(key) != cond:
                return False
    return True


class _Result:
    def __init__(self, inserted_id=None, modified=0, matched=0):
        self.inserted_id = inserted_id
        self.modified_count = modified
        self.matched_count = matched


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
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]

    async def find_one(self, flt=None, projection=None):
        for d in self.docs:
            if _match(d, flt or {}):
                return dict(d)
        return None

    def find(self, flt=None, projection=None):
        return _Cursor([d for d in self.docs if _match(d, flt or {})])

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
                if "$set" in update:
                    d.update(update["$set"])
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        d[k] = d.get(k, 0) + v
                return _Result(modified=1, matched=1)
        if upsert:
            newd = {}
            _id = (flt or {}).get("_id")
            if _id is not None:
                newd["_id"] = _id if isinstance(_id, ObjectId) else ObjectId(str(_id))
            for k, v in (flt or {}).items():
                if k != "_id" and not isinstance(v, dict):
                    newd[k] = v
            if "$set" in update:
                newd.update(update["$set"])
            if "$inc" in update:
                for k, v in update["$inc"].items():
                    newd[k] = newd.get(k, 0) + v
            self.docs.append(newd)
        return _Result(modified=0, matched=0)

    async def update_many(self, flt, update):
        n = 0
        for d in self.docs:
            if _match(d, flt or {}):
                if "$set" in update:
                    d.update(update["$set"])
                n += 1
        return _Result(modified=n, matched=n)

    async def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if _match(d, flt or {}):
                del self.docs[i]
                return _Result(modified=1, matched=1)
        return _Result(modified=0, matched=0)

    async def delete_many(self, flt):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, flt or {})]
        return _Result(modified=before - len(self.docs), matched=before - len(self.docs))

    async def count_documents(self, flt=None):
        return len([d for d in self.docs if _match(d, flt or {})])

    async def create_index(self, *args, **kwargs):
        return None


class FakeDB:
    """Attribute access (db.users, db.trades, ...) lazily creates collections."""

    def __init__(self, **collections):
        object.__setattr__(self, "_collections", {})
        for name, docs in collections.items():
            self._collections[name] = FakeCollection(docs)

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_collections")
        if name not in cols:
            cols[name] = FakeCollection()
        return cols[name]

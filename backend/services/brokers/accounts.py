"""Broker account identity — the durable `broker_account_id` boundary (D6.4).

WHAT THIS FIXES
---------------
Until D6.4 a brokerage account's identity in this platform was the pair
``(user_id, broker)``. It was the key of ``db.broker_accounts`` (a unique index),
the key of the engine's in-memory session cache, the key of the stream registry,
the third segment of a market-feed provider name, and the first two parameters of
every public method on ``BrokerEngine``.

That pair is not an account. It is *a user and a brand*. Three consequences
followed from treating it as one:

  * **A user could hold exactly one account per broker.** Not by policy — by a
    unique index. A second Zerodha account did not fail; it silently *replaced*
    the first, taking its tokens, its stream and its portfolio rows with it.
  * **Nothing downstream could tell two accounts apart**, so an order row, a
    holding, a background task and a realtime event all named the broker and
    hoped. The moment a second account existed they would have named the wrong
    one, and no error would have been raised anywhere.
  * **Reconnect had no identity to be idempotent against.** Re-authorizing wrote
    over whatever ``(user_id, broker)`` pointed at, whether or not it was the
    same brokerage account.

THE MODEL
---------
::

    user_id                  the platform identity
      └── broker_account_id  an authorized external brokerage account   <- THIS
            ├── broker       which brand it is at
            ├── external_account_id  what the broker calls it
            ├── auth session credentials, expiry, refresh    (db.broker_accounts)
            └── connections  websockets, shards              (stream registry)

``broker_account_id`` is opaque, internal, immutable and minted exactly once per
external brokerage account per user. It is **not** derived from the broker name,
the OAuth state, the access token, a websocket, or any client-supplied value, and
none of those can be substituted for it.

WHY THE ID IS RANDOM AND THE *LOOKUP* IS DETERMINISTIC
-------------------------------------------------------
The obvious alternative is a deterministic id — ``hash(user_id, broker,
external_account_id)`` — which makes migration trivially idempotent because the
same inputs always produce the same id. It also makes the id *derivable by
anyone who knows those three values*, and it welds the identity to fields that
must be allowed to change (a legacy row that never recorded an external id later
learns one; the id must not move underneath the orders already pointing at it).

So the id is a random 128-bit token minted once, and **determinism lives in the
lookup**: :meth:`BrokerAccountDirectory.link` resolves ``(user_id, broker,
external_account_id)`` to an existing row before it mints anything. Re-linking the
same brokerage account is idempotent because the lookup finds the row, not
because the id was recomputed from it.

CREDENTIAL SEPARATION
---------------------
Account metadata and session credentials share the ``broker_accounts`` document —
splitting the collection is a migration this sprint does not need in order to
establish the identity boundary. What *is* enforced is a shape boundary:
:class:`BrokerAccountRef` is a frozen dataclass with a fixed field list, none of
which is a credential, and every read in this module projects the credential
fields away. A directory result therefore cannot carry a token even if the
document does, and :meth:`BrokerAccountRef.public_dict` narrows it further for
anything client-facing.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Prefix on every minted id. Present so a `broker_account_id` is recognisable
#: in a log line, an audit row or a URL, and so a value that is obviously a
#: broker name, a Mongo ObjectId or a client code fails the syntactic check in
#: `is_broker_account_id` before it ever reaches a database query.
BROKER_ACCOUNT_ID_PREFIX = "ba_"

#: Bytes of entropy in a minted id. 128 bits, same order as the OAuth state
#: handles minted by `security.oauth_state`.
_ID_BYTES = 16

#: Document fields the directory must never carry out of the collection.
#: `BrokerAccountRef` could not hold them anyway — this is the belt to that
#: braces, and it is what `_project` subtracts.
CREDENTIAL_FIELDS = frozenset({
    "access_token", "refresh_token", "public_token", "feed_token",
    "api_key", "api_secret", "session_token", "enctoken",
})


class BrokerAccountStatus:
    """Account lifecycle states (D6.4 / §16).

    Deliberately the minimum vocabulary the platform can act on. Each value
    exists because some code branches on it:

    ``CONNECTED``
        Credentials are held and believed live. The only state a broker call is
        attempted from.
    ``DISCONNECTED``
        The user detached the account. Credentials are cleared; the account
        *identity and its history remain*, which is the whole reason disconnect
        is not deletion — reconnecting must land on the same
        ``broker_account_id``.
    ``REAUTH_REQUIRED``
        The broker rejected the credentials (token expired, session killed). The
        account is real, the user owns it, and one login fixes it. Distinct from
        ``DISCONNECTED`` because the UI must say something different and because
        a background re-probe treats them differently.
    ``REVOKED``
        Authorization was withdrawn at the broker or by an admin. Credentials
        are cleared and the account may not be silently reconnected by a
        background path; only an explicit user re-link moves it out.

    ``BLOCKED`` and ``DELETED`` from the brief are deliberately absent: nothing
    in this codebase can enter or act on them today, and a state vocabulary with
    unreachable members is a vocabulary nobody can trust.
    """

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    REAUTH_REQUIRED = "reauth_required"
    REVOKED = "revoked"

    ALL = frozenset({CONNECTED, DISCONNECTED, REAUTH_REQUIRED, REVOKED})

    #: States a broker call may be attempted from.
    LIVE = frozenset({CONNECTED})


class BrokerAccountError(Exception):
    """Base for account-identity failures."""


class AmbiguousBrokerAccount(BrokerAccountError):
    """A broker-addressed request reached a user who holds several accounts there.

    Raised by :meth:`BrokerAccountDirectory.sole_for_broker`, the compatibility
    bridge that lets the pre-D6.4 broker-addressed routes keep working. It exists
    so the bridge can **fail closed** rather than choose: "first connected",
    "last connected" and "any connected" are all forbidden (D6.4 / §6), and the
    honest answer to "which of this user's two Zerodha accounts did you mean" is
    that the caller has to say.
    """

    def __init__(self, user_id: str, broker: str, count: int):
        self.user_id = str(user_id)
        self.broker = broker
        self.count = count
        super().__init__(
            f"user holds {count} {broker} accounts; the request must name a "
            f"broker_account_id"
        )


class UnknownBrokerAccount(BrokerAccountError):
    """No account with this id is owned by this user.

    One exception for "no such account" and "someone else's account" on purpose:
    the caller must not be able to tell them apart (D6.4 / §7). The routes turn
    this into a single 404.
    """


def new_broker_account_id() -> str:
    """Mint an opaque account identifier. Called once per external account."""
    return f"{BROKER_ACCOUNT_ID_PREFIX}{secrets.token_hex(_ID_BYTES)}"


def is_broker_account_id(value: Any) -> bool:
    """Whether `value` is syntactically a minted account id.

    A cheap shape check run on client input *before* it reaches a query, so a
    broker name, an ObjectId or a broker's own client code is rejected as
    malformed rather than looked up. It proves nothing about ownership — that is
    :meth:`BrokerAccountDirectory.resolve`'s job and cannot be delegated to a
    string check.
    """
    if not isinstance(value, str):
        return False
    if not value.startswith(BROKER_ACCOUNT_ID_PREFIX):
        return False
    body = value[len(BROKER_ACCOUNT_ID_PREFIX):]
    return len(body) == _ID_BYTES * 2 and all(c in "0123456789abcdef" for c in body)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_external(value: Any) -> Optional[str]:
    """Normalize a broker's own account identifier, or None if it gave none.

    Whitespace and case are normalized because a broker's client code is a case-
    insensitive handle in every integration here (Kite `user_id`, SmartAPI
    `clientcode`, Dhan `dhanClientId`, Fyers `fy_id`, Upstox `user_id`) and an
    account that comes back capitalized differently on the next login must not
    become a second account.
    """
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


@dataclass(frozen=True)
class BrokerAccountRef:
    """One authorized brokerage account. The routing token for every broker call.

    Frozen, and its field list is the whole of what the account layer will carry:
    there is no field here a credential could be assigned to, which is what makes
    "the directory cannot leak a token" a property of the type rather than of
    every call site's discipline.
    """

    broker_account_id: str
    user_id: str
    broker: str
    #: What the broker calls this account (Kite user id, SmartAPI client code,
    #: Dhan client id, Fyers fy_id). None only for a legacy row that predates
    #: D6.4 and never recorded one — see `external_identity_verified`.
    external_account_id: Optional[str] = None
    #: Whether `external_account_id` came from the broker's own authenticated
    #: response. False marks a legacy account whose external identity is unknown;
    #: it is the flag that stops such a row being treated as a *distinct*
    #: account from one the broker later names (see `link`).
    external_identity_verified: bool = False
    status: str = BrokerAccountStatus.DISCONNECTED
    display_name: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    connected_at: Optional[str] = None
    disconnected_at: Optional[str] = None
    last_sync: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def is_live(self) -> bool:
        return self.status in BrokerAccountStatus.LIVE

    def owned_by(self, user_id: Any) -> bool:
        """The ownership predicate, spelled once.

        Every account-addressed operation is guarded by this. It compares the
        *string* forms because a `user_id` reaches this layer as a str from a
        JWT, an ObjectId from a Mongo read, and a str from a background task.
        """
        return bool(user_id) and str(user_id) == self.user_id

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def public_dict(self) -> Dict[str, Any]:
        """The client-facing shape.

        Narrower than `as_dict` on purpose: `external_identity_verified` is an
        internal migration fact, not something a UI should render or branch on.
        """
        return {
            "broker_account_id": self.broker_account_id,
            "broker": self.broker,
            "external_account_id": self.external_account_id,
            "status": self.status,
            "display_name": self.display_name,
            "capabilities": list(self.capabilities),
            "connected_at": self.connected_at,
            "last_sync": self.last_sync,
        }


#: Document fields that make up a `BrokerAccountRef`. Used as a Mongo projection
#: so a directory read never pulls a credential into memory in the first place.
_REF_FIELDS = (
    "broker_account_id", "user_id", "broker", "external_account_id",
    "external_identity_verified", "status", "display_name", "capabilities",
    "connected_at", "disconnected_at", "last_sync", "expires_at",
    "created_at", "updated_at",
)

_REF_PROJECTION = {name: 1 for name in _REF_FIELDS}


def ref_from_doc(doc: Dict[str, Any]) -> Optional[BrokerAccountRef]:
    """Build a ref from a `broker_accounts` document, or None if it has no id.

    A document with no `broker_account_id` is pre-migration data. It is returned
    as None rather than given a synthesized id, because synthesizing one here
    would mint a *different* id on every read and orphan every row that
    referenced the last one. The backfill is the only thing allowed to assign
    an id.
    """
    if not doc:
        return None
    account_id = doc.get("broker_account_id")
    if not account_id:
        return None
    capabilities = doc.get("capabilities")
    return BrokerAccountRef(
        broker_account_id=str(account_id),
        user_id=str(doc.get("user_id") or ""),
        broker=str(doc.get("broker") or ""),
        external_account_id=_clean_external(doc.get("external_account_id")),
        external_identity_verified=bool(doc.get("external_identity_verified")),
        status=str(doc.get("status") or BrokerAccountStatus.DISCONNECTED),
        display_name=doc.get("display_name"),
        capabilities=list(capabilities) if isinstance(capabilities, (list, tuple)) else [],
        connected_at=doc.get("connected_at"),
        disconnected_at=doc.get("disconnected_at"),
        last_sync=doc.get("last_sync"),
        expires_at=doc.get("expires_at"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


class BrokerAccountDirectory:
    """The only thing that turns identity into an account, and the only thing
    that mints one.

    Every method that takes a `user_id` uses it as a **filter**, not as a
    validation step performed afterwards: a lookup for an account the user does
    not own returns nothing from the database rather than returning a row that
    is then compared. That is what makes "a request for another user's account
    must fail without revealing whether the account exists" (D6.4 / §7) a
    property of the query instead of a check somebody can forget to write.
    """

    def __init__(self, db=None):
        self.db = db

    def configure(self, db) -> None:
        self.db = db

    # -- reads -----------------------------------------------------------------
    async def resolve(self, user_id: Any, broker_account_id: Any) -> BrokerAccountRef:
        """The owner-scoped resolution. Raises `UnknownBrokerAccount` otherwise.

        This is the authorization boundary for every account-addressed request.
        The id alone is never enough — the filter carries both, so an id that
        belongs to another user is indistinguishable from an id that does not
        exist, at the database level, with no branch to get wrong.
        """
        if self.db is None:
            raise UnknownBrokerAccount("broker account directory is not configured")
        if not user_id or not is_broker_account_id(broker_account_id):
            raise UnknownBrokerAccount("unknown broker account")
        doc = await self.db.broker_accounts.find_one(
            {"broker_account_id": broker_account_id, "user_id": str(user_id)},
            _REF_PROJECTION)
        ref = ref_from_doc(doc)
        if ref is None:
            raise UnknownBrokerAccount("unknown broker account")
        return ref

    async def get_unscoped(self, broker_account_id: Any) -> Optional[BrokerAccountRef]:
        """An account by id with NO owner filter — internal callers only.

        The two legitimate callers are startup session restore and the account's
        own background tasks, both of which are acting *as* the account and have
        no authenticated user to scope by. It is deliberately named so that its
        appearance in a request path is visible in review, and
        `tests/test_d64_identity.py` pins the caller list.
        """
        if self.db is None or not is_broker_account_id(broker_account_id):
            return None
        doc = await self.db.broker_accounts.find_one(
            {"broker_account_id": broker_account_id}, _REF_PROJECTION)
        return ref_from_doc(doc)

    async def list_for_user(self, user_id: Any, broker: str = None) -> List[BrokerAccountRef]:
        """Every account this user holds, optionally narrowed to one broker."""
        if self.db is None or not user_id:
            return []
        query: Dict[str, Any] = {"user_id": str(user_id)}
        if broker:
            query["broker"] = broker
        docs = await self.db.broker_accounts.find(query, _REF_PROJECTION).to_list(200)
        refs = [ref for ref in (ref_from_doc(d) for d in docs) if ref is not None]
        # Stable order so a list endpoint and a UI never reshuffle. Sorted by
        # creation then id — never by "most recently connected", which is one of
        # the selection semantics D6.4 forbids and would be an invitation to
        # index into.
        refs.sort(key=lambda r: (r.created_at or "", r.broker_account_id))
        return refs

    async def sole_for_broker(self, user_id: Any, broker: str) -> Optional[BrokerAccountRef]:
        """The user's one account at `broker`; None if they have none.

        Raises :class:`AmbiguousBrokerAccount` when there are several. This is
        the compatibility bridge for the broker-addressed routes and background
        rows written before D6.4, and the raise is the point of it: the bridge
        may resolve an unambiguous case and must refuse an ambiguous one. It
        never picks.
        """
        refs = await self.list_for_user(user_id, broker)
        if not refs:
            return None
        if len(refs) > 1:
            raise AmbiguousBrokerAccount(user_id, broker, len(refs))
        return refs[0]

    async def live_accounts(self) -> List[BrokerAccountRef]:
        """Every account in a live state, for startup restore. Owner-agnostic."""
        if self.db is None:
            return []
        # D6.7 — streamed. This feeds startup session restore, so a cap meant
        # accounts past the 1,000th were never restored and their owners found a
        # disconnected broker after a deploy, with nothing logged.
        from services import fanout
        docs = await fanout.collect(
            self.db.broker_accounts.find(
                {"status": {"$in": sorted(BrokerAccountStatus.LIVE)}},
                _REF_PROJECTION),
            label="broker_accounts.live_accounts")
        return [ref for ref in (ref_from_doc(d) for d in docs) if ref is not None]

    # -- writes ----------------------------------------------------------------
    async def link(self, user_id: Any, broker: str, external_account_id: Any,
                   *, display_name: str = None,
                   capabilities: "Optional[List[str]]" = None) -> BrokerAccountRef:
        """Resolve — or mint — the account for a verified external identity.

        The whole of D6.4's reconnect semantics (§9) is this method:

        1. **The same external account relinks to the same id.** Looked up by
           ``(user_id, broker, external_account_id)``, so re-authorizing is
           idempotent and every order, holding and portfolio row already pointing
           at that id keeps pointing at the right account.
        2. **A legacy row adopts the identity it never had.** A pre-D6.4 account
           has no ``external_account_id``; the first successful re-link fills it
           in *on the existing row* rather than minting a second account beside
           it. Safe because the pre-D6.4 unique index guarantees at most one such
           row per ``(user_id, broker)`` — there is nothing to choose between.
        3. **A different external account is a different account.** No match and
           no adoptable legacy row means a new id, which is what makes a second
           Zerodha account possible at all.

        `external_account_id` may be None only when the broker genuinely did not
        name the account. That case cannot be told apart from a *second* unnamed
        account at the same broker, so it adopts-or-creates exactly one unnamed
        account per broker and is recorded as unverified. A caller that wants the
        stricter behaviour asks the broker for an identity first; every adapter in
        this repository does.
        """
        if self.db is None:
            raise BrokerAccountError("broker account directory is not configured")
        if not user_id or not broker:
            raise BrokerAccountError("user_id and broker are required to link an account")
        user_id = str(user_id)
        external = _clean_external(external_account_id)
        now = _now_iso()

        existing = await self._find_linkable(user_id, broker, external)
        if existing is not None:
            update: Dict[str, Any] = {"updated_at": now}
            if external and not existing.get("external_account_id"):
                # Step 2: the legacy row learns who it is. Recorded as verified
                # because it came from an authenticated broker response.
                update["external_account_id"] = external
                update["external_identity_verified"] = True
            if display_name:
                update["display_name"] = display_name
            if capabilities is not None:
                update["capabilities"] = list(capabilities)
            await self.db.broker_accounts.update_one(
                {"broker_account_id": existing["broker_account_id"]}, {"$set": update})
            merged = {**existing, **update}
            return ref_from_doc(merged)

        account_id = new_broker_account_id()
        doc = {
            "broker_account_id": account_id,
            "user_id": user_id,
            "broker": broker,
            "external_account_id": external,
            "external_identity_verified": bool(external),
            "status": BrokerAccountStatus.DISCONNECTED,
            "display_name": display_name,
            "capabilities": list(capabilities or []),
            "created_at": now,
            "updated_at": now,
            "connected": False,
        }
        await self.db.broker_accounts.insert_one(dict(doc))
        logger.info("Linked new broker account %s (%s) for user %s",
                    account_id, broker, user_id)
        return ref_from_doc(doc)

    async def _find_linkable(self, user_id: str, broker: str,
                             external: Optional[str]) -> Optional[Dict[str, Any]]:
        """The existing row this link should land on, or None to mint.

        Two lookups, in this order, and the order is load-bearing: an exact
        external-identity match always wins, so a user who holds a legacy
        (unnamed) account *and* the account the broker just named cannot have the
        named one adopted into the unnamed one.
        """
        if external:
            doc = await self.db.broker_accounts.find_one({
                "user_id": user_id, "broker": broker, "external_account_id": external})
            if doc:
                return doc
        # The adoptable legacy row: same user, same broker, never named. There is
        # at most one, guaranteed by the pre-D6.4 unique index.
        legacy = await self.db.broker_accounts.find({
            "user_id": user_id, "broker": broker}).to_list(200)
        unnamed = [d for d in legacy if not d.get("external_account_id")
                   and d.get("broker_account_id")]
        if len(unnamed) == 1:
            return unnamed[0]
        if len(unnamed) > 1:
            # Cannot happen with data this platform wrote, and must not be
            # resolved by picking. Minting a new account keeps the ambiguity
            # visible instead of merging two histories together.
            logger.error(
                "user %s has %d unnamed %s accounts; refusing to adopt one — "
                "linking a new account instead", user_id, len(unnamed), broker)
        return None

    async def set_status(self, broker_account_id: str, status: str,
                         **extra: Any) -> None:
        """Move an account's lifecycle state. Never deletes identity."""
        if self.db is None:
            return
        if status not in BrokerAccountStatus.ALL:
            raise BrokerAccountError(f"unknown broker account status: {status}")
        update = {"status": status, "updated_at": _now_iso(), **extra}
        await self.db.broker_accounts.update_one(
            {"broker_account_id": broker_account_id}, {"$set": update})


#: Process-wide directory. Configured alongside the engine in `server.py`.
broker_accounts = BrokerAccountDirectory()

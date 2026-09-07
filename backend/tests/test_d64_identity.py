"""D6.4 — the broker-account identity boundary (hermetic).

WHAT THIS FILE PINS
-------------------
Before D6.4 a brokerage account's identity was `(user_id, broker)`. That is a
user and a *brand*, and treating it as an account produced a specific, silent
class of defect: a user's second account at one broker did not fail to connect,
it **replaced** the first — its row, its cached session, its stream registry
entry, its market-feed provider, its recovery candidate, and (through
`delete_many({user_id, broker})`) its holdings.

The rules this file falsifies:

  * **`broker_account_id` is the identity.** Opaque, minted once, never derived
    from a broker name, a token, an OAuth state or anything a client sends.
  * **Reconnect is idempotent by LOOKUP, not by derivation.** The same external
    brokerage account relinks to the same id; a different one mints a new id.
  * **Ownership is a filter, not a check.** An account id belonging to another
    user does not resolve, so a route cannot "forget" to compare owners.
  * **The broker-addressed bridge refuses rather than choosing.** No "first
    connected", no "last connected", no "any connected".
  * **Migration is deterministic, idempotent, non-destructive**, and REFUSES
    ambiguity rather than merging or picking.
  * **Every substrate keyed on the account.** Sessions, instrument maps, stream
    registry, market-feed provider names, recovery candidates.

WHAT IT DOES NOT PROVE
----------------------
No test here opens a socket, reaches a broker API or places an order. Two
*genuinely distinct external* brokerage accounts at one broker are represented
by two adapter responses carrying different `account_id`s, which is the exact
input a real second account produces — but LIVE BROKER VERIFICATION WAS NOT
PERFORMED, and no independently authorized second live account existed to run
it against.
"""
import ast
import asyncio
import inspect
import pathlib
import re
from unittest.mock import AsyncMock, patch

import pytest
from _accounts import account_doc, account_ref, fixture_account_id  # noqa: E402
from bson import ObjectId

import server
from services.broker_engine import BrokerEngine, broker_engine
from services.brokers.account_migration import (
    MIGRATION_ID,
    migrate_broker_accounts,
)
from services.brokers.accounts import (
    AmbiguousBrokerAccount,
    BrokerAccountDirectory,
    BrokerAccountStatus,
    UnknownBrokerAccount,
    broker_accounts,
    is_broker_account_id,
    new_broker_account_id,
)
from services.brokers.market_feed import feed_provider_name
from tests._fakedb import FakeDB

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _directory(*docs) -> BrokerAccountDirectory:
    return BrokerAccountDirectory(FakeDB(broker_accounts=list(docs)))


# =========================================================================== #
# §1 / §2 — identity and uniqueness                                            #
# =========================================================================== #


class TestTheIdentifier:
    def test_a_minted_id_is_opaque_and_unguessable(self):
        """It carries no user, no broker and no client code.

        A derived id — `hash(user, broker, client_code)` — would be reproducible
        by anyone who knows those three, and would have to *move* when a legacy
        row later learns its external identity, orphaning every order pointing
        at the old one.
        """
        ids = {new_broker_account_id() for _ in range(200)}
        assert len(ids) == 200, "minted ids collided"
        for value in ids:
            assert is_broker_account_id(value)
            assert "zerodha" not in value and "user" not in value

    @pytest.mark.parametrize("candidate", [
        "zerodha",                       # a broker name
        "6a9d9bb6b14cacdbb7843dd5",      # an ObjectId
        "AB1234",                        # a broker's own client code
        "ba_",                           # the prefix alone
        "ba_" + "z" * 32,                # right shape, not hex
        "ba_" + "a" * 31,                # one short
        "",
        None,
        12345,
        {"broker_account_id": "ba_" + "a" * 32},
    ])
    def test_a_value_that_is_not_an_account_id_is_rejected_by_shape(self, candidate):
        """Run before any database query, so a broker name never becomes a
        filter that matches something."""
        assert is_broker_account_id(candidate) is False

    def test_the_engine_never_derives_an_id_from_a_broker_or_a_token(self):
        """The source-level half. A derivation would satisfy every behavioural
        test in this file while quietly making the id guessable and unstable."""
        source = (BACKEND / "services" / "brokers" / "accounts.py").read_text()
        tree = ast.parse(source)
        minters = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "new_broker_account_id"
        ]
        assert len(minters) == 1
        minter = minters[0]

        # It takes NO arguments, which is the strongest form of the claim: a
        # function with no inputs cannot derive its output from a user, a broker,
        # a client code or a token, whatever it does inside.
        args = minter.args
        assert not (args.args or args.posonlyargs or args.kwonlyargs or
                    args.vararg or args.kwarg), (
            "new_broker_account_id() takes an argument — an id that can be "
            "derived from anything is an id that moves when that thing changes")

        # And what it does inside is draw entropy, not hash.
        calls = {ast.dump(node.func) for node in ast.walk(minter)
                 if isinstance(node, ast.Call)}
        assert any("token_hex" in call or "token_urlsafe" in call for call in calls), (
            "the id is not drawn from a CSPRNG")
        for banned in ("sha256", "md5", "sha1", "sha512", "blake2"):
            assert not any(banned in call for call in calls), (
                f"new_broker_account_id() derives its value with {banned}")


class TestExternalIdentity:
    """§2 — what the platform treats as the broker's own name for an account."""

    @pytest.mark.parametrize("broker,field,payload", [
        ("zerodha", "user_id", {"user_id": "AB1234"}),
        ("upstox", "user_id", {"user_id": "UPX999"}),
        ("angelone", "clientcode", {"clientcode": "A12345"}),
        ("fyers", "fy_id", {"fy_id": "XY01234"}),
        ("dhan", "dhanClientId", {"dhanClientId": "1100112233"}),
    ])
    def test_every_adapter_exposes_a_stable_external_account_identifier(
            self, broker, field, payload):
        """Recorded per broker rather than assumed.

        The brief warned against assuming `client_id` is the right external
        identity. It is not one field across five brokers — Kite and Upstox call
        it `user_id`, SmartAPI `clientcode`, Fyers `fy_id`, Dhan `dhanClientId` —
        and each adapter is the only code entitled to know which. What the
        platform requires is only that the value is stable across logins and
        unique within the broker, which is true of all five (they are the
        account numbers printed on the user's own contract notes).

        This test pins the *field name per broker* so that an adapter rewrite
        that quietly starts returning a session id, an order id or a token in
        `account_id` fails here rather than in production, where it would make
        every login look like a brand-new account.
        """
        from services.brokers import broker_registry

        adapter = broker_registry.require(broker)
        source = inspect.getsource(type(adapter))
        assert re.search(rf'"account_id":\s*[^,\n]*{re.escape(field)}', source), (
            f"{broker}'s adapter no longer reports {field} as account_id")

    def test_the_external_identifier_is_normalized_so_case_is_not_a_new_account(self):
        directory = _directory()
        first = _run(directory.link("u1", "zerodha", "ab1234"))
        second = _run(directory.link("u1", "zerodha", "AB1234"))
        assert first.broker_account_id == second.broker_account_id
        assert first.external_account_id == "AB1234"


# =========================================================================== #
# §9 — reconnect / duplicate-account semantics                                 #
# =========================================================================== #


class TestLinking:
    def test_relinking_the_same_external_account_is_idempotent(self):
        directory = _directory()
        first = _run(directory.link("u1", "zerodha", "AB1234"))
        again = _run(directory.link("u1", "zerodha", "AB1234"))
        assert first.broker_account_id == again.broker_account_id
        assert len(directory.db.broker_accounts.docs) == 1

    def test_a_different_external_account_at_the_same_broker_is_a_different_account(self):
        """The headline capability. Under the pre-D6.4 unique index this was not
        a second account — it was an upsert over the first."""
        directory = _directory()
        first = _run(directory.link("u1", "zerodha", "AB1234"))
        second = _run(directory.link("u1", "zerodha", "CD5678"))
        assert first.broker_account_id != second.broker_account_id
        assert len(directory.db.broker_accounts.docs) == 2
        assert {a.external_account_id
                for a in _run(directory.list_for_user("u1"))} == {"AB1234", "CD5678"}

    def test_two_users_at_the_same_broker_with_the_same_client_code_are_separate(self):
        """Not a realistic broker state, and asserted anyway: the uniqueness key
        leads with `user_id`, so even an identical external id cannot join two
        users' accounts."""
        directory = _directory()
        a = _run(directory.link("user-a", "zerodha", "AB1234"))
        b = _run(directory.link("user-b", "zerodha", "AB1234"))
        assert a.broker_account_id != b.broker_account_id
        assert a.user_id == "user-a" and b.user_id == "user-b"

    def test_a_legacy_unnamed_account_adopts_the_identity_it_never_had(self):
        """Migration safety. A pre-D6.4 row has no `external_account_id`; the
        first successful re-login must fill it in ON THAT ROW, not mint a second
        account beside it — every order and holding already points at the first.
        """
        legacy = account_doc("u1", "zerodha", external_account_id=None)
        directory = _directory(legacy)
        linked = _run(directory.link("u1", "zerodha", "AB1234"))
        assert linked.broker_account_id == legacy["broker_account_id"]
        assert linked.external_account_id == "AB1234"
        assert linked.external_identity_verified is True
        assert len(directory.db.broker_accounts.docs) == 1

    def test_a_named_account_is_never_adopted_into_an_unnamed_one(self):
        """The ordering inside `_find_linkable`, asserted. An exact external
        match must win over the adoptable legacy row, or a user holding both
        would have their named account swallowed by the unnamed one."""
        named = account_doc("u1", "zerodha", suffix="named", external_account_id="AB1234")
        unnamed = account_doc("u1", "zerodha", suffix="legacy", external_account_id=None)
        directory = _directory(named, unnamed)
        linked = _run(directory.link("u1", "zerodha", "AB1234"))
        assert linked.broker_account_id == named["broker_account_id"]

    def test_two_unnamed_accounts_are_never_merged_by_a_relink(self):
        """The ambiguity this platform's own data cannot produce, and which is
        still refused rather than resolved by picking."""
        a = account_doc("u1", "zerodha", suffix="one", external_account_id=None)
        b = account_doc("u1", "zerodha", suffix="two", external_account_id=None)
        directory = _directory(a, b)
        linked = _run(directory.link("u1", "zerodha", "AB1234"))
        assert linked.broker_account_id not in (a["broker_account_id"],
                                                b["broker_account_id"])
        assert len(directory.db.broker_accounts.docs) == 3

    def test_a_disconnected_account_reconnects_onto_the_same_id(self):
        """§16 — disconnect clears credentials and keeps identity, so history
        stays attached across a disconnect/reconnect cycle."""
        directory = _directory()
        first = _run(directory.link("u1", "zerodha", "AB1234"))
        _run(directory.set_status(first.broker_account_id,
                                  BrokerAccountStatus.DISCONNECTED))
        again = _run(directory.link("u1", "zerodha", "AB1234"))
        assert again.broker_account_id == first.broker_account_id


# =========================================================================== #
# §7 — the authorization boundary                                              #
# =========================================================================== #


class TestOwnership:
    def test_an_account_resolves_only_for_its_owner(self):
        directory = _directory(account_doc("user-a", "zerodha"))
        account_id = fixture_account_id("user-a", "zerodha")

        assert _run(directory.resolve("user-a", account_id)).user_id == "user-a"
        with pytest.raises(UnknownBrokerAccount):
            _run(directory.resolve("user-b", account_id))

    def test_someone_elses_account_and_a_nonexistent_one_are_indistinguishable(self):
        """§7 — a caller probing ids must not learn which ones exist."""
        directory = _directory(account_doc("user-a", "zerodha"))

        errors = []
        for account_id in (fixture_account_id("user-a", "zerodha"),
                           new_broker_account_id()):
            with pytest.raises(UnknownBrokerAccount) as excinfo:
                _run(directory.resolve("user-b", account_id))
            errors.append(str(excinfo.value))
        assert errors[0] == errors[1], (
            "the error distinguishes 'not yours' from 'no such account'")

    def test_the_owner_filter_is_in_the_QUERY_not_in_a_later_comparison(self):
        """The structural half, and the reason this is not merely a convention.

        A `find_one({"broker_account_id": ...})` followed by
        `if doc["user_id"] != user_id` is a check somebody can forget, reorder,
        or short-circuit. Filtering in the query means the wrong user's document
        is never in memory at all.
        """
        source = inspect.getsource(BrokerAccountDirectory.resolve)
        assert '"user_id": str(user_id)' in source, (
            "resolve() does not filter by owner in the database query")

    def test_the_unscoped_reader_has_exactly_the_sanctioned_callers(self):
        """`get_unscoped` bypasses the owner filter by design — background paths
        act AS the account. Its caller list is pinned so a request handler
        cannot quietly start using it."""
        allowed = {
            "services/broker_engine.py",     # re-probe: the id came from our own register
            "services/trading_engine.py",    # auto-exit: the id came off the trade row
            "services/brokers/accounts.py",  # the definition
        }
        found = set()
        for path in BACKEND.rglob("*.py"):
            if "venv" in path.parts or "tests" in path.parts:
                continue
            if "get_unscoped" in path.read_text():
                found.add(str(path.relative_to(BACKEND)))
        assert found <= allowed, f"get_unscoped gained a caller: {found - allowed}"


# =========================================================================== #
# §6 — no hidden account selection                                             #
# =========================================================================== #


class TestTheBrokerAddressedBridgeRefusesRatherThanChoosing:
    def test_one_account_resolves(self):
        directory = _directory(account_doc("u1", "zerodha"))
        account = _run(directory.sole_for_broker("u1", "zerodha"))
        assert account.broker_account_id == fixture_account_id("u1", "zerodha")

    def test_no_account_is_none_not_an_error(self):
        assert _run(_directory().sole_for_broker("u1", "zerodha")) is None

    def test_two_accounts_raise_instead_of_picking_one(self):
        directory = _directory(
            account_doc("u1", "zerodha", suffix="one", external_account_id="AB1234"),
            account_doc("u1", "zerodha", suffix="two", external_account_id="CD5678"),
        )
        with pytest.raises(AmbiguousBrokerAccount) as excinfo:
            _run(directory.sole_for_broker("u1", "zerodha"))
        assert excinfo.value.count == 2

    def test_the_listing_order_is_stable_and_is_not_connection_order(self):
        """A caller must not be able to build "the first one" out of this list.

        Sorted by creation time then id — deliberately NOT by `connected_at` or
        `last_sync`, either of which would make the list reorder itself under a
        UI that indexes into it.
        """
        directory = _directory(
            account_doc("u1", "zerodha", suffix="one", external_account_id="AB1234",
                        created_at="2026-01-01T00:00:00+00:00",
                        connected_at="2026-09-01T00:00:00+00:00"),
            account_doc("u1", "upstox", suffix="two", external_account_id="UP1",
                        created_at="2026-02-01T00:00:00+00:00",
                        connected_at="2026-01-01T00:00:00+00:00"),
        )
        order = [a.external_account_id for a in _run(directory.list_for_user("u1"))]
        assert order == ["AB1234", "UP1"]
        assert order == [a.external_account_id
                         for a in _run(directory.list_for_user("u1"))]


class TestForbiddenSelectionSemanticsCannotReturn:
    """§17 — the static sweep.

    Behavioural tests prove the current code is correct; this one makes the
    *shape* of the old defect visible if it is reintroduced. It reads executable
    code only, so the explanations throughout this sprint's comments — which
    necessarily name the patterns they removed — are not violations.
    """

    #: Modules that route or resolve a brokerage account.
    SCOPE = (
        "services/broker_engine.py",
        "services/brokers/accounts.py",
        "services/brokers/stream.py",
        "services/brokers/market_feed.py",
        "services/brokers/recovery.py",
        "services/trading_engine.py",
    )

    @staticmethod
    def _executable_source(path: pathlib.Path) -> str:
        """Source with docstrings, comments and string literals removed."""
        source = path.read_text()
        without_docstrings = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', '""', source)
        without_strings = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', '""', without_docstrings)
        return re.sub(r"#[^\n]*", "", without_strings)

    def test_no_module_reaches_for_an_account_without_naming_one(self):
        banned = {
            "any_connected_session": "the D6.1 defect, by name",
            "first_connected": "'first connected' selection",
            "last_connected": "'last connected' selection",
            "default_broker_account": "a default account",
            "find_by_broker": "'find by broker' selection",
        }
        for relative in self.SCOPE:
            code = self._executable_source(BACKEND / relative)
            for token, why in banned.items():
                assert token not in code, f"{relative} reintroduced {why}"

    def test_no_broker_account_lookup_is_keyed_by_the_broker_name_alone(self):
        """The specific query shape that made two accounts collide.

        `broker_accounts.find_one({"user_id": ..., "broker": ...})` answers
        "whichever document Mongo returned first" the moment a user holds two.
        Every remaining `broker_accounts` query outside the directory must be
        keyed by `broker_account_id`.
        """
        for relative in self.SCOPE:
            code = (BACKEND / relative).read_text()
            if relative == "services/brokers/accounts.py":
                continue          # the directory is where the pair is legitimate
            for match in re.finditer(r"broker_accounts\.(find_one|update_one|find)\(", code):
                window = code[match.end():match.end() + 200]
                assert "broker_account_id" in window, (
                    f"{relative} queries broker_accounts without naming the "
                    f"account: ...{window[:80]}")


# =========================================================================== #
# §5 — user / account / session / connection are four different things         #
# =========================================================================== #


class TestTheSubstrateIsKeyedByTheAccount:
    def test_the_session_cache_and_instrument_map_are_account_keyed(self):
        engine = BrokerEngine()
        engine.configure(FakeDB())
        a = account_ref("u1", "zerodha", suffix="one")
        b = account_ref("u1", "zerodha", suffix="two")

        engine._sessions[a.broker_account_id] = {"access_token": "A"}
        engine._remember_instrument_map(a, holdings=[])

        assert b.broker_account_id not in engine._sessions
        assert b.broker_account_id not in engine._instrument_maps

    def test_two_accounts_at_one_broker_get_two_market_feed_providers(self):
        """The pre-D6.4 name was `brokerfeed:<broker>:<user_id>`, so a user's two
        Zerodha accounts shared ONE provider: the second attach replaced the
        first's registration and its symbol coverage, and disconnecting either
        one detached the feed both were using."""
        a = account_ref("u1", "zerodha", suffix="one")
        b = account_ref("u1", "zerodha", suffix="two")
        assert feed_provider_name(a) != feed_provider_name(b)
        assert feed_provider_name(a).endswith(a.broker_account_id)

    def test_the_stream_registry_refuses_to_open_a_connection_it_cannot_name(self):
        """Enforcement by signature, not by convention: a caller with no account
        cannot open a stream at all, so the collision cannot be reintroduced by
        someone forgetting to pass one."""
        from services.brokers.stream import BrokerStreamManager

        manager = BrokerStreamManager()
        with pytest.raises(TypeError):
            _run(manager.start_stream("u1", "zerodha", {"access_token": "t"}))
        with pytest.raises(ValueError):
            _run(manager.start_stream("u1", "zerodha", {"access_token": "t"},
                                      broker_account_id=""))

    def test_two_accounts_of_one_user_hold_two_stream_registry_entries(self):
        from services.brokers.stream import BrokerStream, BrokerStreamManager

        manager = BrokerStreamManager()
        a = fixture_account_id("u1", "zerodha", "one")
        b = fixture_account_id("u1", "zerodha", "two")
        with patch.object(BrokerStream, "start", lambda self: None):
            _run(manager.start_stream("u1", "zerodha", {"access_token": "A"},
                                      broker_account_id=a))
            _run(manager.start_stream("u1", "zerodha", {"access_token": "B"},
                                      broker_account_id=b))
        rows = {row["broker_account_id"] for row in manager.status()}
        assert rows == {a, b}, "one account's stream replaced the other's"

    def test_stopping_one_accounts_stream_leaves_the_others_running(self):
        from services.brokers.stream import BrokerStream, BrokerStreamManager

        manager = BrokerStreamManager()
        a = fixture_account_id("u1", "zerodha", "one")
        b = fixture_account_id("u1", "zerodha", "two")
        with patch.object(BrokerStream, "start", lambda self: None), \
                patch.object(BrokerStream, "stop", new=AsyncMock()):
            _run(manager.start_stream("u1", "zerodha", {"access_token": "A"},
                                      broker_account_id=a))
            _run(manager.start_stream("u1", "zerodha", {"access_token": "B"},
                                      broker_account_id=b))
            _run(manager.stop_stream(a))
        assert {row["broker_account_id"] for row in manager.status()} == {b}

    def test_a_recovery_candidate_belongs_to_one_account(self):
        from services.brokers.recovery import RecoveryClass, RecoveryRegister

        register = RecoveryRegister()
        a = fixture_account_id("u1", "zerodha", "one")
        b = fixture_account_id("u1", "zerodha", "two")
        register.record_withdrawal(a, "default", RecoveryClass.REPROBE)
        register.record_withdrawal(b, "default", RecoveryClass.REPROBE)

        assert register.discharge(a) == 1
        assert register.get(b, "default") is not None, (
            "one account's evidence discharged the other's withdrawal")


# =========================================================================== #
# §18 — the two-user / three-account matrix, end to end over HTTP              #
# =========================================================================== #


ACCOUNT_PATHS = ("holdings", "positions", "funds", "orders", "profile",
                 "trades", "margins")


@pytest.fixture
def matrix(fake_db, test_user, other_user):
    """A: two accounts (one Zerodha, one Upstox). B: one Zerodha account.

    Two brokers because the matrix has to separate "wrong account" from "wrong
    broker", and A/X vs A/Y at the *same* broker is covered separately by
    `TestTwoAccountsAtOneBroker` — which is where the pre-D6.4 collision lived.
    """
    a, b = str(test_user["_id"]), str(other_user["_id"])
    accounts = {
        "A/X": account_doc(a, "zerodha", external_account_id="A-KITE"),
        "A/Y": account_doc(a, "upstox", external_account_id="A-UPX"),
        "B/X": account_doc(b, "zerodha", external_account_id="B-KITE"),
    }
    for doc in accounts.values():
        fake_db.broker_accounts.docs.append({
            **doc, "access_token": f"TOKEN-{doc['external_account_id']}",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "connected_at": "2026-01-01T00:00:00+00:00",
        })
    return accounts


def _auth(user):
    """A real JWT minted by the application's own issuer, as conftest does."""
    from tests.conftest import _headers_for

    return _headers_for(user)


class TestTheAccountMatrix:
    def test_each_user_sees_exactly_their_own_accounts(
            self, client, fake_db, matrix, test_user, other_user):
        a_ids = {row["broker_account_id"] for row in
                 client.get("/api/brokers/accounts", headers=_auth(test_user)).json()["accounts"]}
        b_ids = {row["broker_account_id"] for row in
                 client.get("/api/brokers/accounts", headers=_auth(other_user)).json()["accounts"]}

        assert a_ids == {matrix["A/X"]["broker_account_id"],
                         matrix["A/Y"]["broker_account_id"]}
        assert b_ids == {matrix["B/X"]["broker_account_id"]}
        assert not (a_ids & b_ids)

    @pytest.mark.parametrize("owned", ["A/X", "A/Y"])
    def test_a_can_reach_both_of_their_own_accounts(
            self, client, fake_db, matrix, test_user, owned):
        """The positive control. Without it every attack below could be passing
        because the route is broken for everyone."""
        account_id = matrix[owned]["broker_account_id"]
        with patch("services.brokers.gateway.broker_gateway.get_holdings",
                   new=AsyncMock(return_value=[])):
            resp = client.get(f"/api/brokers/accounts/{account_id}/holdings",
                              headers=_auth(test_user))
        assert resp.status_code == 200, resp.text
        assert resp.json()["broker_account_id"] == account_id

    @pytest.mark.parametrize("path", ACCOUNT_PATHS)
    @pytest.mark.parametrize("victim,attacker_key", [("A/X", "other"), ("A/Y", "other")])
    def test_b_cannot_reach_any_of_as_accounts(
            self, client, fake_db, matrix, test_user, other_user, path, victim,
            attacker_key):
        account_id = matrix[victim]["broker_account_id"]
        with patch("services.brokers.gateway.broker_gateway.get_holdings",
                   new=AsyncMock(return_value=[{"symbol": "LEAK"}])):
            resp = client.get(f"/api/brokers/accounts/{account_id}/{path}",
                              headers=_auth(other_user))
        assert resp.status_code == 404, resp.text
        assert "LEAK" not in resp.text
        assert account_id not in resp.text, "the response confirmed the id exists"

    @pytest.mark.parametrize("path", ACCOUNT_PATHS)
    def test_a_cannot_reach_bs_account(
            self, client, fake_db, matrix, test_user, path):
        account_id = matrix["B/X"]["broker_account_id"]
        resp = client.get(f"/api/brokers/accounts/{account_id}/{path}",
                          headers=_auth(test_user))
        assert resp.status_code == 404

    def test_b_cannot_place_an_order_in_as_account(
            self, client, fake_db, matrix, test_user, other_user):
        """The order path is the one where a wrong answer moves real money."""
        placed = []

        async def _spy(*args, **kwargs):
            placed.append(args)
            return {"order_id": "SHOULD-NOT-HAPPEN"}

        with patch("services.brokers.gateway.broker_gateway.place_order", new=_spy):
            resp = client.post(
                f"/api/brokers/accounts/{matrix['A/X']['broker_account_id']}/orders",
                json={"symbol": "RELIANCE", "exchange": "NSE",
                      "transaction_type": "BUY", "quantity": 1,
                      "order_type": "MARKET"},
                headers=_auth(other_user))
        assert resp.status_code == 404
        assert placed == [], "an order reached the broker for another user's account"

    def test_b_cannot_disconnect_as_account(
            self, client, fake_db, matrix, test_user, other_user):
        account_id = matrix["A/X"]["broker_account_id"]
        resp = client.post(f"/api/brokers/accounts/{account_id}/disconnect",
                           headers=_auth(other_user))
        assert resp.status_code == 404
        row = next(d for d in fake_db.broker_accounts.docs
                   if d.get("broker_account_id") == account_id)
        assert row["access_token"] == "TOKEN-A-KITE", "the account was disconnected"

    def test_b_cannot_read_as_account_metadata(
            self, client, fake_db, matrix, test_user, other_user):
        """§7 — not the holdings, and not the account record either."""
        account_id = matrix["A/X"]["broker_account_id"]
        resp = client.get(f"/api/brokers/accounts/{account_id}",
                          headers=_auth(other_user))
        assert resp.status_code == 404
        assert "A-KITE" not in resp.text


class TestTwoAccountsAtOneBroker:
    """A/X1 and A/X2 — the case the pre-D6.4 model could not express at all."""

    @pytest.fixture
    def two_kite_accounts(self, fake_db, test_user):
        uid = str(test_user["_id"])
        docs = [
            account_doc(uid, "zerodha", suffix="one", external_account_id="AB1234"),
            account_doc(uid, "zerodha", suffix="two", external_account_id="CD5678"),
        ]
        for doc in docs:
            fake_db.broker_accounts.docs.append({
                **doc, "access_token": f"TOKEN-{doc['external_account_id']}",
                "expires_at": "2099-01-01T00:00:00+00:00"})
        return docs

    def test_both_accounts_are_listed_and_distinguishable(
            self, client, fake_db, two_kite_accounts, test_user):
        rows = client.get("/api/brokers/accounts",
                          headers=_auth(test_user)).json()["accounts"]
        assert len(rows) == 2
        assert {r["external_account_id"] for r in rows} == {"AB1234", "CD5678"}
        assert len({r["broker_account_id"] for r in rows}) == 2

    @pytest.mark.parametrize("index", [0, 1])
    def test_each_account_answers_with_its_own_session(
            self, client, fake_db, two_kite_accounts, test_user, index):
        """The property the shared session cache destroyed: two accounts, two
        tokens, and the token used is the one belonging to the account named."""
        doc = two_kite_accounts[index]
        seen = {}

        async def _holdings(broker, session):
            seen["token"] = session["access_token"]
            return []

        broker_engine._sessions.clear()
        with patch("services.brokers.gateway.broker_gateway.get_holdings", new=_holdings):
            resp = client.get(
                f"/api/brokers/accounts/{doc['broker_account_id']}/holdings",
                headers=_auth(test_user))
        assert resp.status_code == 200, resp.text
        assert seen["token"] == f"TOKEN-{doc['external_account_id']}"

    def test_the_broker_addressed_route_refuses_rather_than_choosing(
            self, client, fake_db, two_kite_accounts, test_user):
        """The whole point of the bridge failing closed: with two Zerodha
        accounts, `/api/brokers/zerodha/holdings` has no honest answer."""
        resp = client.get("/api/brokers/zerodha/holdings", headers=_auth(test_user))
        assert resp.status_code == 409, resp.text
        assert "broker_account_id" in resp.json()["detail"]

    def test_the_broker_addressed_ORDER_route_also_refuses(
            self, client, fake_db, two_kite_accounts, test_user):
        placed = []

        async def _spy(*a, **k):
            placed.append(a)
            return {"order_id": "X"}

        with patch("services.brokers.gateway.broker_gateway.place_order", new=_spy):
            resp = client.post("/api/brokers/zerodha/orders",
                               json={"symbol": "RELIANCE", "exchange": "NSE",
                                     "transaction_type": "BUY", "quantity": 1,
                                     "order_type": "MARKET"},
                               headers=_auth(test_user))
        assert resp.status_code == 409
        assert placed == [], "an ambiguous request still placed an order"

    def test_the_per_broker_status_view_reports_the_ambiguity_instead_of_a_boolean(
            self, client, fake_db, two_kite_accounts, test_user):
        """`get_status` cannot answer "is your Zerodha connected" with one
        boolean any more, and inventing one would be `any_connected` again."""
        status = client.get("/api/brokers/status", headers=_auth(test_user)).json()
        assert status["zerodha"]["ambiguous"] is True
        assert status["zerodha"]["broker_account_id"] is None
        assert status["zerodha"]["account_count"] == 2
        assert len(status["zerodha"]["accounts"]) == 2

    @pytest.mark.parametrize("target", [0, 1])
    def test_disconnecting_one_account_leaves_the_other_connected(
            self, client, fake_db, two_kite_accounts, test_user, target):
        """Run against BOTH accounts, and the second case is the one that
        matters.

        An update filtered by `(user_id, broker)` rather than by the account
        writes to whichever document the database returns first. Disconnecting
        the *first* account therefore looks correct even when the filter is
        wrong — the right row happens to be the one hit. Disconnecting the
        *second* is what exposes it: the wrong account's credentials are cleared
        and the one the user asked to remove stays live.
        """
        chosen = two_kite_accounts[target]
        other = two_kite_accounts[1 - target]
        with patch("services.brokers.gateway.broker_gateway.invalidate_session",
                   new=AsyncMock()):
            resp = client.post(
                f"/api/brokers/accounts/{chosen['broker_account_id']}/disconnect",
                headers=_auth(test_user))
        assert resp.status_code == 200, resp.text

        rows = {d["broker_account_id"]: d for d in fake_db.broker_accounts.docs}
        assert rows[chosen["broker_account_id"]]["access_token"] == "", \
            "the account the user asked to disconnect kept its credentials"
        assert rows[other["broker_account_id"]]["access_token"] == \
            f"TOKEN-{other['external_account_id']}", \
            "disconnecting one account cleared the other account's credentials"
        # And the legacy per-broker flag still says the broker is connected,
        # because one of the two accounts still is.
        user = next(u for u in fake_db.users.docs if u["_id"] == test_user["_id"])
        assert user.get("zerodha_connected") is True


# =========================================================================== #
# §13 — order-path safety (no submission anywhere in this class)               #
# =========================================================================== #


class TestOrderPathBindsTheAccount:
    def test_a_trade_records_the_account_its_entry_order_went_to(
            self, client, fake_db, matrix, test_user):
        with patch("services.brokers.gateway.broker_gateway.place_order",
                   new=AsyncMock(return_value={"order_id": "KITE-1"})):
            resp = client.post("/api/trades", json={
                "symbol": "RELIANCE", "stock_name": "Reliance", "type": "BUY",
                "entry_price": 100.0, "quantity": 1, "stop_loss": 90.0,
                "target1": 120.0,
                "broker_account_id": matrix["A/X"]["broker_account_id"],
            }, headers=_auth(test_user))
        assert resp.status_code == 200, resp.text
        stored = fake_db.trades.docs[0]
        assert stored["broker_account_id"] == matrix["A/X"]["broker_account_id"]
        assert stored["broker"] == "zerodha"

    def test_a_trade_cannot_be_opened_against_another_users_account(
            self, client, fake_db, matrix, test_user, other_user):
        placed = []

        async def _spy(*a, **k):
            placed.append(a)
            return {"order_id": "X"}

        with patch("services.brokers.gateway.broker_gateway.place_order", new=_spy):
            resp = client.post("/api/trades", json={
                "symbol": "RELIANCE", "stock_name": "Reliance", "type": "BUY",
                "entry_price": 100.0, "quantity": 1, "stop_loss": 90.0,
                "target1": 120.0,
                "broker_account_id": matrix["A/X"]["broker_account_id"],
            }, headers=_auth(other_user))
        assert resp.status_code == 404
        assert placed == []
        assert fake_db.trades.docs == []

    def test_the_broker_field_cannot_steer_an_order_away_from_the_named_account(
            self, client, fake_db, matrix, test_user):
        """A request naming account A/X (Zerodha) and `broker: "upstox"` must go
        to A/X. The broker is derived from the account, not taken twice."""
        seen = {}

        async def _place(broker, session, order):
            seen["broker"] = broker
            return {"order_id": "OK"}

        with patch("services.brokers.gateway.broker_gateway.place_order", new=_place):
            resp = client.post("/api/trades", json={
                "symbol": "RELIANCE", "stock_name": "Reliance", "type": "BUY",
                "entry_price": 100.0, "quantity": 1, "stop_loss": 90.0,
                "target1": 120.0,
                "broker_account_id": matrix["A/X"]["broker_account_id"],
                "broker": "upstox",
            }, headers=_auth(test_user))
        assert resp.status_code == 200, resp.text
        assert seen["broker"] == "zerodha"
        assert fake_db.trades.docs[0]["broker"] == "zerodha"

    def test_a_market_exit_returns_to_the_account_the_entry_was_placed_in(
            self, client, fake_db, matrix, test_user):
        """A/Y is the *later* account and the one the broker-name bridge would
        not even reach; the exit must follow the id on the trade."""
        trade_id = ObjectId()
        fake_db.trades.docs.append({
            "_id": trade_id, "user_id": str(test_user["_id"]), "symbol": "RELIANCE",
            "stock_name": "Reliance", "type": "BUY", "entry_price": 100.0,
            "quantity": 5, "quantity_open": 5, "realized_pnl": 0.0,
            "stop_loss": 90.0, "initial_stop_loss": 90.0, "target1": 120.0,
            "best_price": 100.0, "targets_hit": [], "trailing_stop": {"enabled": False},
            "status": "OPEN", "events": [], "is_paper": False,
            "broker": "upstox",
            "broker_account_id": matrix["A/Y"]["broker_account_id"],
            "entry_time": "2026-01-01T00:00:00+00:00",
        })
        seen = {}

        async def _place(broker, session, order):
            seen["broker"] = broker
            seen["token"] = session["access_token"]
            return {"order_id": "EXIT-1"}

        with patch("services.brokers.gateway.broker_gateway.place_order", new=_place), \
                patch.object(server, "real_quote",
                             new=AsyncMock(return_value={"symbol": "RELIANCE",
                                                         "price": 110.0})):
            broker_engine._sessions.clear()
            resp = client.post(f"/api/trades/{trade_id}/exit",
                               json={"at_market": True},
                               headers=_auth(test_user))
        assert resp.status_code == 200, resp.text
        assert seen["broker"] == "upstox"
        assert seen["token"] == "TOKEN-A-UPX"

    def test_an_order_id_is_deduplicated_per_account_not_per_user(self):
        """Two accounts at one broker can legitimately issue the same order id —
        they are per-account sequences. The old key `(user_id, broker, order_id)`
        merged them into one row."""
        engine = BrokerEngine()
        engine.configure(FakeDB())
        a = account_ref("u1", "zerodha", suffix="one")
        b = account_ref("u1", "zerodha", suffix="two")

        _run(engine._record_order(a, {"order_id": "SAME", "symbol": "RELIANCE"}))
        _run(engine._record_order(b, {"order_id": "SAME", "symbol": "TCS"}))

        rows = engine.db.orders.docs
        assert len(rows) == 2, "one account's order overwrote the other's"
        assert {r["symbol"] for r in rows} == {"RELIANCE", "TCS"}


# =========================================================================== #
# §10 — background tasks                                                       #
# =========================================================================== #


class TestBackgroundTasksCarryTheirAccount:
    def test_the_auto_exit_path_refuses_a_trade_it_cannot_attribute(self):
        """The safest possible failure: a scheduler with no authenticated user
        must never choose an account to sell in.

        The directory is deliberately seeded with a PERFECTLY GOOD Zerodha
        account belonging to this trade's owner. That is what makes the test
        falsifiable: a fallback of any kind — "resolve the broker name",
        "this user's zerodha", "the only one there is" — would find that account
        and place a live market order in it. Refusing has to be a decision, not
        an accident of there being nothing to find.
        """
        from services import trading_engine

        directory_db = FakeDB(broker_accounts=[account_doc("u1", "zerodha")])
        broker_accounts.configure(directory_db)
        try:
            placed = []

            class _Engine:
                async def place_order(self, account, order):
                    placed.append(account)
                    return {"order_id": "X"}

            trade = {"user_id": "u1", "symbol": "RELIANCE", "broker": "zerodha",
                     "quantity": 1, "type": "BUY"}
            assert _run(trading_engine._broker_exit(_Engine(), trade, 1, "SL")) is None
            assert placed == [], (
                "a trade with no broker_account_id was exited into an account "
                "this path chose for itself")
        finally:
            broker_accounts.configure(None)

    def test_the_auto_exit_path_uses_the_account_recorded_on_the_trade(self):
        from services import trading_engine

        directory_db = FakeDB(broker_accounts=[account_doc("u1", "upstox")])
        broker_accounts.configure(directory_db)
        try:
            seen = []

            class _Engine:
                async def place_order(self, account, order):
                    seen.append(account.broker_account_id)
                    return {"order_id": "X"}

            trade = {"user_id": "u1", "symbol": "RELIANCE", "broker": "upstox",
                     "broker_account_id": fixture_account_id("u1", "upstox"),
                     "quantity": 1, "type": "BUY"}
            _run(trading_engine._broker_exit(_Engine(), trade, 1, "SL"))
            assert seen == [fixture_account_id("u1", "upstox")]
        finally:
            broker_accounts.configure(None)

    def test_the_auto_exit_path_refuses_an_account_that_is_not_the_trades_owners(self):
        """The id came off a row filtered by `user_id` when it was written, and
        the ownership predicate is asserted anyway: a mismatched pair is a
        corrupted row, and selling into it would be selling in a stranger's
        account."""
        from services import trading_engine

        directory_db = FakeDB(broker_accounts=[account_doc("someone-else", "upstox")])
        broker_accounts.configure(directory_db)
        try:
            placed = []

            class _Engine:
                async def place_order(self, account, order):
                    placed.append(account)
                    return {"order_id": "X"}

            trade = {"user_id": "u1", "symbol": "RELIANCE", "broker": "upstox",
                     "broker_account_id": fixture_account_id("someone-else", "upstox"),
                     "quantity": 1, "type": "BUY"}
            assert _run(trading_engine._broker_exit(_Engine(), trade, 1, "SL")) is None
            assert placed == []
        finally:
            broker_accounts.configure(None)

    def test_a_reprobe_for_an_account_that_no_longer_exists_attaches_nothing(self):
        engine = BrokerEngine()
        engine.configure(FakeDB())
        with patch.object(BrokerEngine, "start_stream", new=AsyncMock()) as started:
            _run(engine._reattach_channel(new_broker_account_id(), "default"))
        assert started.await_count == 0

    def test_a_restored_session_is_cached_under_its_own_account(self):
        """§10's headline: account X's task must keep using X when Y appears."""
        engine = BrokerEngine()
        docs = [
            {**account_doc("u1", "zerodha", suffix="one", external_account_id="AB1234"),
             "access_token": "TOKEN-X", "expires_at": "2099-01-01T00:00:00+00:00"},
            {**account_doc("u1", "zerodha", suffix="two", external_account_id="CD5678"),
             "access_token": "TOKEN-Y", "expires_at": "2099-01-01T00:00:00+00:00"},
        ]
        engine.configure(FakeDB(broker_accounts=docs))
        with patch.object(BrokerEngine, "start_stream", new=AsyncMock()), \
                patch.object(BrokerEngine, "_publish_connection", new=AsyncMock()):
            restored = _run(engine.load_sessions())
        assert restored == 2
        assert engine._sessions[fixture_account_id("u1", "zerodha", "one")]["access_token"] == "TOKEN-X"
        assert engine._sessions[fixture_account_id("u1", "zerodha", "two")]["access_token"] == "TOKEN-Y"


# =========================================================================== #
# §11 — realtime                                                               #
# =========================================================================== #


class TestRealtimeIsAccountScoped:
    def test_a_private_broker_event_names_the_account_it_came_from(self):
        engine = BrokerEngine()
        engine.configure(FakeDB())
        pushed = []

        async def _push(user_id, message):
            pushed.append((user_id, message))

        engine.ws_push = _push
        account = account_ref("u1", "zerodha", suffix="one")
        _run(engine._on_stream_order(account, {"order_id": "O-1", "status": "OPEN"}))

        assert pushed[0][0] == "u1"
        assert pushed[0][1]["data"]["broker_account_id"] == account.broker_account_id

    def test_the_connection_lifecycle_event_names_the_account(self):
        """The Source Manager's per-user registry keys on it, so a disconnect of
        one account no longer removes the broker while the other is streaming."""
        from services.market_engine.source_manager import SourceManager

        manager = SourceManager()
        a = account_ref("u1", "zerodha", suffix="one")
        b = account_ref("u1", "zerodha", suffix="two")
        manager.record_broker_connected("u1", "zerodha", ["tick_stream"],
                                        broker_account_id=a.broker_account_id)
        manager.record_broker_connected("u1", "zerodha", ["tick_stream"],
                                        broker_account_id=b.broker_account_id)
        manager.record_broker_disconnected("u1", "zerodha",
                                           broker_account_id=a.broker_account_id)

        assert manager.connected_brokers("u1") == ["zerodha"], (
            "disconnecting one account took the other account's broker with it")
        manager.record_broker_disconnected("u1", "zerodha",
                                           broker_account_id=b.broker_account_id)
        assert manager.connected_brokers("u1") == []

    def test_a_tick_re_marks_only_the_holdings_of_the_account_it_arrived_on(self):
        """One user, two Zerodha accounts, DIFFERENT holdings.

        The two accounts deliberately hold different symbols, and the tick that
        arrives names the symbol only the OTHER account holds. Written with the
        same symbol in both accounts, a broker-scoped fold looks correct: Mongo's
        `update_one` writes one document, so the second account's row keeps its
        old price and the test agrees with the bug. With different symbols there
        is exactly one row a wrongly-scoped fold can touch, and touching it is
        the defect — account X's socket re-pricing account Y's position.
        """
        from services import portfolio_stream

        a = fixture_account_id("u1", "zerodha", "one")
        b = fixture_account_id("u1", "zerodha", "two")
        db = FakeDB(holdings=[
            {"user_id": "u1", "broker": "zerodha", "broker_account_id": a,
             "symbol": "RELIANCE", "quantity": 10, "last_price": 100.0},
            {"user_id": "u1", "broker": "zerodha", "broker_account_id": b,
             "symbol": "TCS", "quantity": 5, "last_price": 300.0},
        ])
        published = []

        async def _snapshot(*args, **kwargs):
            published.append(kwargs.get("price_override"))
            return {}

        with patch.object(portfolio_stream, "publish_snapshot", new=_snapshot):
            result = _run(portfolio_stream.apply_broker_ticks(
                db, "u1", "zerodha", [{"symbol": "TCS", "price": 999.0}],
                broker_account_id=a))

        by_account = {d["broker_account_id"]: d for d in db.holdings.docs}
        assert by_account[b]["last_price"] == 300.0, (
            "account X's tick re-marked account Y's holding")
        assert by_account[a]["last_price"] == 100.0
        # Nothing in the account the tick arrived on matched, so no snapshot at
        # all — the correct outcome, and the one a broker-scoped fold destroys.
        assert result is None
        assert published == []


# =========================================================================== #
# §8 — OAuth / account linking                                                 #
# =========================================================================== #


class TestOAuthLinking:
    def _engine(self, *docs):
        engine = BrokerEngine()
        engine.configure(FakeDB(broker_accounts=list(docs)))
        return engine

    def test_the_account_comes_from_the_brokers_own_response(self):
        engine = self._engine()
        session = {"access_token": "t", "account_id": "AB1234",
                   "expires_at": "2099-01-01T00:00:00+00:00",
                   "profile": {"user_name": "A Trader"}}
        with patch("services.brokers.gateway.broker_gateway.exchange_token",
                   new=AsyncMock(return_value=session)), \
                patch.object(BrokerEngine, "sync_portfolio", new=AsyncMock()), \
                patch.object(BrokerEngine, "_push", new=AsyncMock()):
            result = _run(engine.complete_auth("zerodha", "u1", {"request_token": "rt"}))

        assert is_broker_account_id(result["broker_account_id"])
        row = engine.db.broker_accounts.docs[0]
        assert row["external_account_id"] == "AB1234"
        assert row["external_identity_verified"] is True
        assert row["user_id"] == "u1"

    def test_a_second_login_to_the_same_brokerage_account_updates_it(self):
        engine = self._engine()

        def _connect(external):
            session = {"access_token": f"t-{external}", "account_id": external,
                       "expires_at": "2099-01-01T00:00:00+00:00"}
            with patch("services.brokers.gateway.broker_gateway.exchange_token",
                       new=AsyncMock(return_value=session)), \
                    patch.object(BrokerEngine, "sync_portfolio", new=AsyncMock()), \
                    patch.object(BrokerEngine, "_push", new=AsyncMock()):
                return _run(engine.complete_auth("zerodha", "u1", {"request_token": "x"}))

        first = _connect("AB1234")
        again = _connect("AB1234")
        assert first["broker_account_id"] == again["broker_account_id"]
        assert len(engine.db.broker_accounts.docs) == 1

        other = _connect("CD5678")
        assert other["broker_account_id"] != first["broker_account_id"]
        assert len(engine.db.broker_accounts.docs) == 2

    def test_the_callback_reads_no_client_supplied_account_id(self):
        """§8 — a `broker_account_id` in the callback query string is ignored.

        Both callbacks are checked, because the legacy Zerodha alias is what
        `KITE_REDIRECT_URL` actually points at in this repository's own `.env`.
        """
        source = (BACKEND / "server.py").read_text()
        for route in ("broker_oauth_callback", "zerodha_callback"):
            start = source.index(f"async def {route}(")
            body = source[start:start + 4000]
            executable = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', '""', body)
            executable = re.sub(r"#[^\n]*", "", executable)
            assert "params.get(\"uid\")" not in executable, f"{route} reads uid"
            assert "broker_account_id" not in executable, (
                f"{route} takes an account id from the client")

    def test_the_legacy_zerodha_callback_no_longer_trusts_uid(self, client, fake_db):
        """D6.4 / V-1 — the vulnerability this sprint found.

        `GET /api/zerodha/callback?request_token=...&status=success&uid=<victim>`
        used to attach whichever brokerage account completed the Kite login to
        whichever platform user the query string named — in either direction, and
        with no credential of the other party.
        """
        victim = ObjectId()
        fake_db.users.docs.append({"_id": victim, "email": "v@example.com",
                                   "name": "Victim", "role": "user"})
        exchanged = []

        async def _exchange(broker, payload):
            exchanged.append(payload)
            return {"access_token": "attacker-token", "account_id": "ATTACKER"}

        with patch("services.brokers.gateway.broker_gateway.exchange_token",
                   new=_exchange):
            resp = client.get("/api/zerodha/callback",
                              params={"request_token": "rt", "status": "success",
                                      "uid": str(victim)},
                              follow_redirects=False)

        assert resp.status_code in (302, 307)
        assert "status=failed" in resp.headers["location"]
        assert exchanged == [], "the token exchange ran for an unproven callback"
        assert fake_db.broker_accounts.docs == [], (
            "an account was grafted onto a user named in the query string")

    def test_the_legacy_zerodha_login_url_puts_no_user_id_on_the_wire(
            self, client, fake_db, test_user):
        """It used to pass `str(user["_id"])` into the adapter's `state`
        parameter — the D6.1 defect with the parameter renamed."""
        resp = client.get("/api/zerodha/login-url", headers=_auth(test_user))
        assert resp.status_code == 200, resp.text
        url = resp.json().get("url") or ""
        assert str(test_user["_id"]) not in url
        assert "uid=" not in url


# =========================================================================== #
# §19 — migration                                                              #
# =========================================================================== #


class TestMigration:
    def test_an_empty_database_migrates_cleanly(self):
        db = FakeDB()
        report = _run(migrate_broker_accounts(db))
        assert report.ok
        assert report.accounts_scanned == 0
        assert report.accounts_migrated == 0

    def test_a_single_legacy_account_gets_an_id_and_its_external_identity(self):
        db = FakeDB(broker_accounts=[{
            "_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
            "connected": True, "access_token": "enc", "account_id": "AB1234",
            "connected_at": "2026-01-01T00:00:00+00:00",
        }])
        report = _run(migrate_broker_accounts(db))

        assert report.accounts_migrated == 1
        row = db.broker_accounts.docs[0]
        assert is_broker_account_id(row["broker_account_id"])
        assert row["external_account_id"] == "AB1234"
        assert row["external_identity_verified"] is True
        assert row["status"] == BrokerAccountStatus.CONNECTED
        # Non-destructive: nothing the old model relied on was removed.
        assert row["access_token"] == "enc"
        assert row["broker"] == "zerodha"
        assert row["connected"] is True

    def test_a_disconnected_legacy_account_keeps_its_identity(self):
        db = FakeDB(broker_accounts=[{
            "_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
            "connected": False, "access_token": "", "account_id": "AB1234",
        }])
        _run(migrate_broker_accounts(db))
        row = db.broker_accounts.docs[0]
        assert is_broker_account_id(row["broker_account_id"])
        assert row["status"] == BrokerAccountStatus.DISCONNECTED

    def test_a_legacy_account_the_broker_never_named_is_marked_unverified(self):
        db = FakeDB(broker_accounts=[{
            "_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
            "connected": True, "access_token": "enc",
        }])
        _run(migrate_broker_accounts(db))
        row = db.broker_accounts.docs[0]
        assert is_broker_account_id(row["broker_account_id"])
        assert row["external_account_id"] is None
        assert row["external_identity_verified"] is False

    def test_running_it_twice_changes_nothing(self):
        db = FakeDB(broker_accounts=[{
            "_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
            "connected": True, "account_id": "AB1234",
        }])
        _run(migrate_broker_accounts(db))
        first = dict(db.broker_accounts.docs[0])

        second_report = _run(migrate_broker_accounts(db))

        assert second_report.accounts_migrated == 0
        assert second_report.accounts_already_migrated == 1
        assert db.broker_accounts.docs[0]["broker_account_id"] == first["broker_account_id"]

    def test_a_partially_migrated_database_resumes(self):
        migrated = account_doc("u1", "zerodha", external_account_id="AB1234")
        db = FakeDB(broker_accounts=[
            dict(migrated),
            {"_id": ObjectId(), "user_id": "u2", "broker": "upstox",
             "connected": True, "account_id": "UP999"},
        ])
        report = _run(migrate_broker_accounts(db))
        assert report.accounts_migrated == 1
        assert report.accounts_already_migrated == 1
        assert db.broker_accounts.docs[0]["broker_account_id"] == migrated["broker_account_id"]
        assert is_broker_account_id(db.broker_accounts.docs[1]["broker_account_id"])

    def test_referencing_rows_are_stamped_with_their_account(self):
        db = FakeDB(
            broker_accounts=[{"_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
                              "connected": True, "account_id": "AB1234"}],
            orders=[{"_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
                     "order_id": "O-1"}],
            holdings=[{"_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
                       "symbol": "RELIANCE"}],
            portfolios=[{"_id": ObjectId(), "user_id": "u1", "broker": "zerodha"}],
            trades=[{"_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
                     "symbol": "RELIANCE"}],
        )
        _run(migrate_broker_accounts(db))
        account_id = db.broker_accounts.docs[0]["broker_account_id"]
        for collection in ("orders", "holdings", "portfolios", "trades"):
            row = getattr(db, collection).docs[0]
            assert row["broker_account_id"] == account_id, collection

    def test_no_historical_row_changes_owner(self):
        """The property that makes this safe to run against production data."""
        db = FakeDB(
            broker_accounts=[
                {"_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
                 "connected": True, "account_id": "AB1234"},
                {"_id": ObjectId(), "user_id": "u2", "broker": "zerodha",
                 "connected": True, "account_id": "CD5678"},
            ],
            orders=[
                {"_id": ObjectId(), "user_id": "u1", "broker": "zerodha", "order_id": "1"},
                {"_id": ObjectId(), "user_id": "u2", "broker": "zerodha", "order_id": "2"},
            ],
        )
        _run(migrate_broker_accounts(db))
        by_user = {d["user_id"]: d["broker_account_id"] for d in db.broker_accounts.docs}
        for order in db.orders.docs:
            assert order["broker_account_id"] == by_user[order["user_id"]]
            assert order["user_id"] in ("u1", "u2")

    def test_a_trade_that_names_no_broker_is_left_alone(self):
        """A manual or paper trade has no brokerage account, and stamping one on
        would assert a link that does not exist."""
        db = FakeDB(
            broker_accounts=[{"_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
                              "connected": True, "account_id": "AB1234"}],
            trades=[{"_id": ObjectId(), "user_id": "u1", "symbol": "RELIANCE",
                     "is_paper": True}],
        )
        _run(migrate_broker_accounts(db))
        assert "broker_account_id" not in db.trades.docs[0]

    def test_an_ambiguous_legacy_pair_is_reported_and_nothing_is_touched(self):
        """§4 — the STOP condition. Two documents claiming one legacy identity
        cannot be merged (that joins two credential sets and two histories) and
        must not be chosen between (that orphans one's orders)."""
        db = FakeDB(
            broker_accounts=[
                {"_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
                 "connected": True, "account_id": "AB1234"},
                {"_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
                 "connected": True, "account_id": "CD5678"},
            ],
            orders=[{"_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
                     "order_id": "O-1"}],
        )
        report = _run(migrate_broker_accounts(db))

        assert not report.ok
        assert report.ambiguous == [
            {"user_id": "u1", "broker": "zerodha", "documents": 2,
             "broker_account_ids": [None, None]}]
        assert report.accounts_migrated == 0
        for row in db.broker_accounts.docs:
            assert "broker_account_id" not in row
        assert "broker_account_id" not in db.orders.docs[0], (
            "a row referencing an ambiguous account was stamped anyway")

    def test_an_ambiguous_pair_does_not_stop_every_other_account_migrating(self):
        db = FakeDB(broker_accounts=[
            {"_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
             "connected": True, "account_id": "AB1234"},
            {"_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
             "connected": True, "account_id": "CD5678"},
            {"_id": ObjectId(), "user_id": "u2", "broker": "upstox",
             "connected": True, "account_id": "UP1"},
        ])
        report = _run(migrate_broker_accounts(db))
        assert report.accounts_migrated == 1
        assert len(report.ambiguous) == 1
        healthy = next(d for d in db.broker_accounts.docs if d["user_id"] == "u2")
        assert is_broker_account_id(healthy["broker_account_id"])

    def test_the_run_is_recorded(self):
        db = FakeDB()
        _run(migrate_broker_accounts(db))
        assert db.schema_migrations.docs[0]["migration"] == MIGRATION_ID

    def test_a_restored_session_after_migration_uses_the_assigned_id(self):
        """Restart-after-migration: the migration runs before `load_sessions`,
        so every restored session is cached under an id that exists."""
        db = FakeDB(broker_accounts=[{
            "_id": ObjectId(), "user_id": "u1", "broker": "zerodha",
            "connected": True, "access_token": "live", "account_id": "AB1234",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }])
        _run(migrate_broker_accounts(db))
        account_id = db.broker_accounts.docs[0]["broker_account_id"]

        engine = BrokerEngine()
        engine.configure(db)
        with patch.object(BrokerEngine, "start_stream", new=AsyncMock()), \
                patch.object(BrokerEngine, "_publish_connection", new=AsyncMock()):
            assert _run(engine.load_sessions()) == 1
        assert engine._sessions[account_id]["access_token"] == "live"


# =========================================================================== #
# §15 — indexes                                                                #
# =========================================================================== #


class TestIndexes:
    def test_the_pre_d64_unique_index_is_dropped_and_replaced(self):
        """`create_index` does not redefine an existing index with the same key
        pattern, so leaving `{user_id, broker}` UNIQUE in place would keep a
        second account failing with a duplicate-key error after every code path
        had agreed it was legal."""
        source = (BACKEND / "server.py").read_text()
        assert 'drop_index("user_id_1_broker_1")' in source
        assert 'create_index("broker_account_id", unique=True' in source
        assert '[("user_id", 1), ("broker", 1), ("external_account_id", 1)], unique=True' in source
        # And the surviving (user_id, broker) index must NOT be unique.
        pair = source.index('await db.broker_accounts.create_index([("user_id", 1), ("broker", 1)])')
        assert "unique" not in source[pair:pair + 120]

    def test_the_external_identity_index_is_partial(self):
        """Mongo treats missing and null as one value in a unique index, so
        without the filter two legacy accounts the broker never named would
        collide — and that is exactly the population the migration cannot name.
        """
        source = (BACKEND / "server.py").read_text()
        start = source.index('[("user_id", 1), ("broker", 1), ("external_account_id", 1)]')
        window = source[start:start + 300]
        assert "partialFilterExpression" in window
        assert '"external_account_id": {"$exists": True, "$type": "string"}' in window

"""D6.5 — regressions found by the live two-user / two-broker entry gate.

WHY THIS FILE EXISTS
--------------------
D6.5 is an entry gate, not a feature sprint: it re-ran D6.4's identity boundary
against real, independently authorized brokerage accounts and against a fresh
mutation campaign. One mutation **survived** that campaign, and a surviving
mutation is a statement about the tests rather than about the code:

    services/broker_engine.py, `BrokerEngine.get_session`:
        key = account.broker_account_id     ->     key = account.broker

530 tests passed with that applied.

WHAT THAT MUTATION ACTUALLY DOES
--------------------------------
`get_session` reads the cache under `key`, falls back to `_load_session`, and
then **writes the resolved session back under the same `key`**::

    session = self._sessions.get(key) or await self._load_session(account)
    ...
    self._sessions[key] = session

`_load_session` is addressed by `broker_account_id` and stays correct, so the
first call for either account still loads the right credentials. The damage is
done on the *second* call: with `key` degraded to the broker name, the cache is
warm under `"upstox"`, and the second account at that broker gets a **cache hit
carrying the first account's access token**. Every subsequent broker call for
that account — holdings, positions, funds, order placement — is then executed
against the other brokerage account, with no error raised anywhere.

That is the precise failure D6.4 was written to make impossible, and it is the
one a second same-broker account is required to expose.

WHY THE EXISTING TEST DID NOT CATCH IT
--------------------------------------
`test_d64_identity.py::TestTheSubstrateIsKeyedByTheAccount
::test_the_session_cache_and_instrument_map_are_account_keyed` asserts the right
property but proves it of the wrong thing: it writes
``engine._sessions[a.broker_account_id]`` **itself** and then asserts that
``b.broker_account_id`` is absent. It never calls `get_session`, so it is
asserting on a dictionary the test populated by hand — the production keying
expression is never evaluated, and the test could not have failed no matter what
`get_session` did with its key.

The test below closes that gap the only way it can be closed: by driving two
same-broker accounts *through* `get_session`, warming the cache with the first,
and requiring the second to come back with its own credentials.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

from services.broker_engine import BrokerEngine
from tests._accounts import account_doc, account_ref


def _run(coro):
    return asyncio.run(coro)


def _seed(fake_db, user_id: str, broker: str, suffix: str, token: str) -> None:
    """A connected account whose stored session carries a distinguishable token."""
    fake_db.broker_accounts.docs.append({
        "_id": ObjectId(),
        **account_doc(user_id, broker, suffix=suffix,
                      external_account_id=f"EXT-{suffix.upper()}"),
        "access_token": token,
        "connected": True,
        "connected_at": "2026-09-07T04:00:00+00:00",
        # Far future so `session_is_fresh` is satisfied and the cache-write
        # branch under test is the one that runs.
        "expires_at": "2099-01-01T00:00:00+00:00",
    })


class TestTheLiveSessionCacheIsKeyedByTheAccount:
    """D6.5 / M8. The session cache key is `broker_account_id`, evaluated in
    production code, not asserted about a dict the test filled in."""

    def test_two_accounts_at_one_broker_get_their_own_sessions_through_get_session(
            self, fake_db):
        """The second account must not inherit the first's warm cache entry.

        Order matters: the first `get_session` is what populates the cache, so
        the assertion that can fail is the *second* one. With the cache keyed by
        broker, the second call returns `"TOKEN-FIRST"`.
        """
        engine = BrokerEngine()
        engine.configure(fake_db)
        uid = str(ObjectId())
        _seed(fake_db, uid, "upstox", "first", "TOKEN-FIRST")
        _seed(fake_db, uid, "upstox", "second", "TOKEN-SECOND")

        first = account_ref(uid, "upstox", suffix="first")
        second = account_ref(uid, "upstox", suffix="second")

        # Warm the cache with the first account.
        assert _run(engine.get_session(first))["access_token"] == "TOKEN-FIRST"

        # The second account resolves to ITS OWN credentials, warm cache and all.
        assert _run(engine.get_session(second))["access_token"] == "TOKEN-SECOND"

        # And the first is still itself afterwards — a cache keyed by the broker
        # would also let the second call overwrite the first's entry.
        assert _run(engine.get_session(first))["access_token"] == "TOKEN-FIRST"

    def test_the_cache_is_populated_under_the_account_id(self, fake_db):
        """The stored key is the account id, not the broker name.

        Asserted after a real `get_session` call, so it constrains the
        production expression rather than restating the fixture.
        """
        engine = BrokerEngine()
        engine.configure(fake_db)
        uid = str(ObjectId())
        _seed(fake_db, uid, "upstox", "only", "TOKEN-ONLY")
        ref = account_ref(uid, "upstox", suffix="only")

        _run(engine.get_session(ref))

        assert ref.broker_account_id in engine._sessions
        assert "upstox" not in engine._sessions

    def test_two_users_at_one_broker_do_not_share_a_cached_session(self, fake_db):
        """The cross-USER form of the same defect.

        The live D6.5 topology has two platform users holding accounts at the
        same broker, so a broker-keyed cache would serve one user's token to the
        other — the tenant-boundary version of the mutation above.
        """
        engine = BrokerEngine()
        engine.configure(fake_db)
        user_a, user_b = str(ObjectId()), str(ObjectId())
        _seed(fake_db, user_a, "upstox", "a", "TOKEN-A")
        _seed(fake_db, user_b, "upstox", "b", "TOKEN-B")

        a = account_ref(user_a, "upstox", suffix="a")
        b = account_ref(user_b, "upstox", suffix="b")

        assert _run(engine.get_session(a))["access_token"] == "TOKEN-A"
        assert _run(engine.get_session(b))["access_token"] == "TOKEN-B"
        assert _run(engine.get_session(a))["access_token"] == "TOKEN-A"


class TestSyncingOneAccountDoesNotDeleteAnothersHoldings:
    """D6.5 / M11 — the D5-era portfolio contamination, named in the D6.5 brief
    (§13) and described in `sync_portfolio`'s own docstring, had no test.

    The mutation that exposed it restores the pre-D6.4 delete scope::

        db.holdings.delete_many({"user_id": user_id, "broker_account_id": id})
                  ->  delete_many({"user_id": user_id, "broker": broker})

    560 tests across `test_d63_isolation`, `test_broker_integration`,
    `test_broker_streaming`, `test_portfolio_engine` and `test_portfolio_stream`
    passed with it applied. Nothing anywhere synced ONE account while a SECOND
    account at the SAME broker held rows — which is the only arrangement in which
    the two delete scopes differ.
    """

    @staticmethod
    def _holding(symbol: str) -> dict:
        return {"symbol": symbol, "quantity": 1, "average_price": 100.0,
                "last_price": 110.0, "invested_value": 100.0,
                "market_value": 110.0, "pnl": 10.0, "pnl_percent": 10.0}

    def test_a_sibling_accounts_holdings_survive_a_sync(self, fake_db):
        from unittest.mock import patch

        engine = BrokerEngine()
        engine.configure(fake_db)
        uid = str(ObjectId())
        _seed(fake_db, uid, "upstox", "first", "TOKEN-FIRST")
        _seed(fake_db, uid, "upstox", "second", "TOKEN-SECOND")
        first = account_ref(uid, "upstox", suffix="first")
        second = account_ref(uid, "upstox", suffix="second")

        # The first account already holds a position, as a real one would.
        fake_db.holdings.docs.append({
            "_id": ObjectId(), "user_id": uid, "broker": "upstox",
            "broker_account_id": first.broker_account_id,
            **self._holding("RELIANCE")})

        async def _holdings(broker, session):
            return [self._holding("TATASTEEL")]

        async def _positions(broker, session):
            return []

        async def _funds(broker, session):
            return {"available_margin": 0.0}

        with patch("services.brokers.gateway.broker_gateway.get_holdings", new=_holdings), \
             patch("services.brokers.gateway.broker_gateway.get_positions", new=_positions), \
             patch("services.brokers.gateway.broker_gateway.get_funds", new=_funds):
            _run(engine.sync_portfolio(second))

        rows = list(fake_db.holdings.docs)
        by_account = {}
        for row in rows:
            by_account.setdefault(row["broker_account_id"], []).append(row["symbol"])

        # The account that was synced holds what the broker just reported.
        assert by_account.get(second.broker_account_id) == ["TATASTEEL"]
        # THE ASSERTION THAT FAILS UNDER THE MUTATION: the sibling account at the
        # same broker was not touched.
        assert by_account.get(first.broker_account_id) == ["RELIANCE"], (
            "syncing one account deleted a sibling account's holdings — the "
            "pre-D6.4 delete_many({user_id, broker}) contamination")


class TestATradeResolvesToTheAccountItsEntryOrderWentTo:
    """D6.5 / M13 — the recorded `broker_account_id` wins over the broker bridge.

    The mutation that exposed the gap makes `_trade_broker_account` ignore the
    id recorded on the trade and re-resolve by broker name. 259 tests across the
    trading, identity and isolation suites passed with it applied.

    CLASSIFICATION: this mutation is **not** a security hole. Falling through to
    `_sole_account` fails CLOSED — a user holding two accounts at that broker
    gets a 409 rather than an exit routed into the wrong account, because D6.4
    never deletes an account row, so the original is always still there to make
    the bridge ambiguous. What it changes is availability, and what it proves is
    that the branch which makes a stored trade self-describing had no test.
    """

    def test_the_recorded_account_is_used_even_when_the_bridge_would_refuse(
            self, fake_db, test_user):
        from server import _trade_broker_account

        uid = str(test_user["_id"])
        _seed(fake_db, uid, "upstox", "entry", "TOKEN-ENTRY")
        _seed(fake_db, uid, "upstox", "other", "TOKEN-OTHER")
        entry = account_ref(uid, "upstox", suffix="entry")

        trade = {"broker": "upstox",
                 "broker_account_id": entry.broker_account_id}

        resolved = _run(_trade_broker_account(test_user, trade))
        assert resolved.broker_account_id == entry.broker_account_id

    def test_a_legacy_trade_with_no_account_id_refuses_when_ambiguous(
            self, fake_db, test_user):
        """The fallback's own contract: the bridge may resolve or refuse, never
        pick. A legacy row at a broker where the user now holds two accounts has
        no honest answer."""
        from fastapi import HTTPException
        from server import _trade_broker_account

        uid = str(test_user["_id"])
        _seed(fake_db, uid, "upstox", "one", "TOKEN-ONE")
        _seed(fake_db, uid, "upstox", "two", "TOKEN-TWO")

        with pytest.raises(HTTPException) as excinfo:
            _run(_trade_broker_account(test_user, {"broker": "upstox"}))
        assert excinfo.value.status_code == 409


# =========================================================================== #
# LIM-D6.5-6 — the entry route's account contract, from the client's side      #
# =========================================================================== #
#
# `TradeMonitor` now sends `broker_account_id` instead of `broker` (the D6.5
# reachability gap). These tests are the server half of that seam: they pin the
# three answers the form now depends on, driven over HTTP through the real route
# and the real ownership filter.
#
# NO ORDER IS PLACED. `broker_gateway.place_order` is replaced by a spy in every
# test that could reach it, and each asserts the spy was either called with the
# account it named or not called at all.


def _auth(user):
    from tests.conftest import _headers_for

    return _headers_for(user)


ENTRY = {"symbol": "RELIANCE", "stock_name": "Reliance", "type": "BUY",
         "entry_price": 100.0, "quantity": 1, "stop_loss": 90.0, "target1": 120.0}


class TestTheEntryRouteAnswersTheAccountTheClientNamed:
    """The account-addressed entry path, exercised with two accounts at ONE
    broker — the only configuration in which a broker name has no honest answer,
    and therefore the only one that proves the id is what routed the order."""

    @pytest.fixture
    def two_upstox(self, fake_db, test_user):
        uid = str(test_user["_id"])
        _seed(fake_db, uid, "upstox", "one", "TOKEN-ONE")
        _seed(fake_db, uid, "upstox", "two", "TOKEN-TWO")
        from services import broker_engine as _be

        _be.broker_engine._sessions.clear()
        return (account_ref(uid, "upstox", suffix="one"),
                account_ref(uid, "upstox", suffix="two"))

    @pytest.mark.parametrize("index", [0, 1])
    def test_the_order_is_placed_with_the_named_accounts_own_credentials(
            self, client, fake_db, two_upstox, test_user, index):
        """Parametrised over BOTH accounts on purpose: an implementation that
        resolved "the first one" would still pass for index 0."""
        target = two_upstox[index]
        seen = {}

        async def _place(broker, session, order):
            seen["broker"] = broker
            seen["token"] = session["access_token"]
            return {"order_id": "ORDER-1"}

        with patch("services.brokers.gateway.broker_gateway.place_order", new=_place):
            resp = client.post("/api/trades",
                               json={**ENTRY,
                                     "broker_account_id": target.broker_account_id},
                               headers=_auth(test_user))

        assert resp.status_code == 200, resp.text
        assert seen["token"] == ("TOKEN-ONE" if index == 0 else "TOKEN-TWO"), \
            "the order was placed with the sibling account's credentials"
        assert seen["broker"] == "upstox"
        stored = fake_db.trades.docs[0]
        assert stored["broker_account_id"] == target.broker_account_id
        assert stored["broker"] == "upstox"

    def test_the_entry_narration_names_the_resolved_accounts_broker(
            self, client, fake_db, two_upstox, test_user):
        """An account-addressed request carries no `broker` field at all. The
        ENTRY event used to read the client's `data.broker` and so would have
        gone silent about a live order the moment the form stopped sending it."""
        with patch("services.brokers.gateway.broker_gateway.place_order",
                   new=AsyncMock(return_value={"order_id": "ORDER-2"})):
            resp = client.post("/api/trades",
                               json={**ENTRY,
                                     "broker_account_id": two_upstox[0].broker_account_id},
                               headers=_auth(test_user))

        assert resp.status_code == 200, resp.text
        entry_event = fake_db.trades.docs[0]["events"][0]
        assert "via upstox" in entry_event["message"]
        assert "ORDER-2" in entry_event["message"]

    def test_a_broker_name_still_refuses_rather_than_choosing(
            self, client, fake_db, two_upstox, test_user):
        """The bridge the form no longer uses must keep failing CLOSED. This is
        the behaviour that made LIM-D6.5-6 a reachability defect rather than a
        vulnerability, and it is not allowed to soften now that the form has
        moved off it."""
        placed = []

        async def _spy(*a, **k):
            placed.append(a)
            return {"order_id": "X"}

        with patch("services.brokers.gateway.broker_gateway.place_order", new=_spy):
            resp = client.post("/api/trades", json={**ENTRY, "broker": "upstox"},
                               headers=_auth(test_user))

        assert resp.status_code == 409, resp.text
        assert "broker_account_id" in resp.json()["detail"]
        assert placed == [], "an ambiguous request still placed an order"
        assert fake_db.trades.docs == [], "an unplaced order still opened a trade"

    def test_no_account_and_no_broker_records_the_trade_and_places_nothing(
            self, client, fake_db, two_upstox, test_user):
        """"Track only" — what the form sends when no account is selected. The
        trade is recorded, `broker_account_id` is null, auto-exit is off, and the
        broker is never called."""
        placed = []

        async def _spy(*a, **k):
            placed.append(a)
            return {"order_id": "X"}

        with patch("services.brokers.gateway.broker_gateway.place_order", new=_spy):
            resp = client.post("/api/trades",
                               json={**ENTRY, "broker_account_id": None,
                                     "auto_exit": True},
                               headers=_auth(test_user))

        assert resp.status_code == 200, resp.text
        assert placed == []
        stored = fake_db.trades.docs[0]
        assert stored["broker_account_id"] is None
        assert stored["broker_order_id"] is None
        assert stored["auto_exit"] is False, \
            "auto-exit was armed on a trade with no account to exit in"


class TestAForeignAccountIsIndistinguishableFromAnAbsentOne:
    """D6.4 already proves a foreign id is refused and places no order. What it
    does not prove is that the refusal *says the same thing* as the refusal for
    an id that exists nowhere — and a client that can tell those two apart has an
    account-existence oracle for other users."""

    def test_the_refusals_are_byte_identical_and_neither_reaches_the_broker(
            self, client, fake_db, test_user, other_user):
        uid_b = str(other_user["_id"])
        _seed(fake_db, uid_b, "upstox", "theirs", "TOKEN-THEIRS")
        theirs = account_ref(uid_b, "upstox", suffix="theirs").broker_account_id
        # Same shape, minted for nobody.
        nowhere = "ba_" + "f" * 32

        placed = []

        async def _spy(*a, **k):
            placed.append(a)
            return {"order_id": "X"}

        with patch("services.brokers.gateway.broker_gateway.place_order", new=_spy):
            foreign = client.post("/api/trades",
                                  json={**ENTRY, "broker_account_id": theirs},
                                  headers=_auth(test_user))
            absent = client.post("/api/trades",
                                 json={**ENTRY, "broker_account_id": nowhere},
                                 headers=_auth(test_user))

        assert foreign.status_code == absent.status_code == 404
        assert foreign.json() == absent.json(), (
            "the refusal for another user's account differs from the refusal for "
            "an account that does not exist — an existence oracle")
        assert theirs not in foreign.text
        assert placed == [], "a refused request still reached the broker"
        assert fake_db.trades.docs == []

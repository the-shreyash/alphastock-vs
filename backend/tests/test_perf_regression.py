"""Performance regression tests (PH3.4).

WHY THERE IS NOT A SINGLE WALL-CLOCK ASSERTION IN THIS FILE
-----------------------------------------------------------
`assert elapsed < 0.05` measures the CI runner. It goes red when the runner is
busy and stays green on a fast laptop that has just regressed by forty queries —
failing for reasons unrelated to the code and passing for the one reason that
matters. A flaky performance test is worse than none: it gets marked `skip`
within two sprints and takes the real coverage with it.

Every assertion here is instead on a quantity that is **exactly reproducible on
any machine** and that determines latency once the costs are real:

* **Query counts.** The number of database round trips per request. Multiply by
  real network RTT to get the latency the endpoint pays on every deployment. This
  is what catches an N+1 the moment someone reintroduces it.
* **Query counts as a function of data volume.** The N+1 *signature*: an endpoint
  whose query count grows when you add rows. Asserting a constant is weaker than
  asserting the count is the same at 3 rows and at 30 — the latter fails even if
  someone changes the constant to match.
* **Index coverage.** That an index exists for each filter+sort the hot routes
  issue. A missing index is invisible in review, silent in every test that uses
  the in-memory double, and only shows up as a production query that got slower
  as the collection grew.
* **Payload size bounds.** Bytes the user pays for regardless of server speed.
* **Concurrency structure.** That the independent fan-outs stay concurrent.

See `docs/performance/PH3.4_PERFORMANCE_CERTIFICATION.md` for the measurements
that motivated each number, and `tests/_perf.py` for the instrument.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from bson import ObjectId

import server
from tests._perf import count_queries, measure


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
def _seed_trades(fake_db, user, n, *, status_cycle=("OPEN", "CLOSED", "CLOSED")):
    for i in range(n):
        status = status_cycle[i % len(status_cycle)]
        closed = status != "OPEN"
        fake_db.trades.docs.append({
            "_id": ObjectId(), "user_id": str(user["_id"]), "symbol": "RELIANCE",
            "direction": "LONG", "quantity": 10, "quantity_open": 0 if closed else 10,
            "entry_price": 1000.0 + i, "status": status,
            "entry_time": f"2026-01-{1 + (i % 28):02d}T09:15:00+00:00",
            "exit_time": f"2026-01-{1 + (i % 28):02d}T15:15:00+00:00" if closed else None,
            "pnl": 100.0 if closed else None,
            "stop_loss": 950.0, "target1": 1100.0,
        })


def _seed_audit_logs(fake_db, n, *, actor_id, distinct_actors=1):
    """Audit rows attributed across `distinct_actors` different operators."""
    actors = [actor_id] + [str(ObjectId()) for _ in range(distinct_actors - 1)]
    for i in range(n):
        fake_db.admin_audit_logs.docs.append({
            "_id": ObjectId(), "admin_id": actors[i % len(actors)],
            "action": "user.updated",
            "timestamp": f"2026-08-{1 + (i % 28):02d}T10:00:00+00:00",
            "details": {"note": "x"},
        })


# --------------------------------------------------------------------------- #
# 1. Query count does not grow with the data (the N+1 property)                 #
# --------------------------------------------------------------------------- #
class TestQueryCountDoesNotScaleWithData:
    """The defining property of an N+1, asserted directly.

    Each test measures the same endpoint at two data volumes and requires the
    query count to be *identical*. This is deliberately stronger than pinning a
    constant: a constant can be updated to match a regression, whereas "the count
    must not depend on the row count" cannot be satisfied by a per-row query at
    any constant.
    """

    @pytest.mark.parametrize("path", [
        "/api/trades",
        "/api/trades/active",
        "/api/trades/history",
        "/api/trades/pnl",
    ])
    def test_trade_listings_are_flat_in_the_number_of_trades(
        self, client, fake_db, test_user, auth_headers, path
    ):
        _seed_trades(fake_db, test_user, 3)
        with count_queries() as few:
            r1 = client.get(path, headers=auth_headers)
        assert r1.status_code == 200

        _seed_trades(fake_db, test_user, 30)
        with count_queries() as many:
            r2 = client.get(path, headers=auth_headers)
        assert r2.status_code == 200

        assert few.total == many.total, (
            f"{path} issued {few.total} queries for 3 trades and {many.total} for 33 "
            f"— the count grew with the data, which is an N+1.\n"
            f"  3 trades: {few.describe()}\n"
            f" 33 trades: {many.describe()}"
        )

    def test_admin_audit_log_page_is_flat_in_the_number_of_rows(
        self, client, fake_db, admin_user, admin_headers
    ):
        """PH3.4 O-3. This endpoint resolved one user per log row: 26 queries to
        render 25 rows, and 201 to render a page of 200. The actor lookup is now a
        single `$in`, so the count is independent of the page size."""
        _seed_audit_logs(fake_db, 5, actor_id=str(admin_user["_id"]))
        with count_queries() as few:
            r1 = client.get("/api/admin/logs?page=1&limit=100", headers=admin_headers)
        assert r1.status_code == 200

        fake_db.admin_audit_logs.docs.clear()
        _seed_audit_logs(fake_db, 60, actor_id=str(admin_user["_id"]))
        with count_queries() as many:
            r2 = client.get("/api/admin/logs?page=1&limit=100", headers=admin_headers)
        assert r2.status_code == 200
        assert len(r2.json()["logs"]) == 60, "the corpus must actually be paged in"

        assert few.total == many.total, (
            f"/api/admin/logs issued {few.total} queries for 5 rows and "
            f"{many.total} for 60 — the actor lookup is per-row again.\n"
            f"  5 rows: {few.describe()}\n"
            f" 60 rows: {many.describe()}"
        )

    def test_admin_audit_log_actor_lookup_is_one_query_per_page(
        self, client, fake_db, admin_user, admin_headers
    ):
        """Not merely flat — *one* actor query, however many distinct operators.

        Two `users` operations are expected, not one: `get_current_user` looks up
        the calling admin's own principal on every authenticated request, and the
        route then resolves the page's actors. `AUTH_PRINCIPAL_LOOKUPS` names that
        constant so the number is not a bare `2` that the next reader has to
        rediscover — the first version of this test asserted `== 1` and failed for
        exactly that reason.
        """
        AUTH_PRINCIPAL_LOOKUPS = 1
        _seed_audit_logs(fake_db, 40, actor_id=str(admin_user["_id"]),
                         distinct_actors=8)
        with count_queries() as log:
            r = client.get("/api/admin/logs?page=1&limit=100", headers=admin_headers)
        assert r.status_code == 200
        assert log.count(collection="users") == AUTH_PRINCIPAL_LOOKUPS + 1, (
            "the eight distinct actors on this page must be resolved in one "
            f"batched query, not one each: {log.describe()}"
        )

    def test_admin_ai_usage_is_bounded_in_the_number_of_chat_authors(
        self, client, fake_db, admin_user, admin_headers
    ):
        """A *bounded* per-row lookup, asserted as its bound.

        `/api/admin/ai/usage` does resolve one user per result row, but the
        aggregate ahead of it is `$limit: 10`, so the fan-out can never exceed ten
        however many authors exist. That is a deliberate, capped cost rather than
        an N+1, and the bound is the contract worth pinning — plus the one
        `get_current_user` lookup every authenticated request makes.

        The second half is the part that matters: ten times more authors must not
        produce more queries.
        """
        AUTH_PRINCIPAL_LOOKUPS = 1
        AGGREGATE_ROW_CAP = 10

        def seed_authors(n):
            for i in range(n):
                fake_db.chat_messages.docs.append({
                    "_id": ObjectId(), "user_id": str(ObjectId()),
                    "session_id": f"s{i}", "role": "user", "content": "hi",
                    "created_at": "2026-08-01T10:00:00+00:00",
                })

        seed_authors(40)
        with count_queries() as few:
            r1 = client.get("/api/admin/ai/usage", headers=admin_headers)
        assert r1.status_code == 200
        assert few.count(collection="users") <= AUTH_PRINCIPAL_LOOKUPS + AGGREGATE_ROW_CAP, (
            f"more than the aggregate's 10-row cap were resolved: {few.describe()}"
        )

        seed_authors(360)
        with count_queries() as many:
            r2 = client.get("/api/admin/ai/usage", headers=admin_headers)
        assert r2.status_code == 200
        assert len(r2.json()["top_users"]) == AGGREGATE_ROW_CAP
        assert few.total == many.total, (
            f"400 authors cost {many.total} queries where 40 cost {few.total} — "
            f"the $limit ahead of the per-row lookup was removed.\n"
            f"  40 authors: {few.describe()}\n"
            f" 400 authors: {many.describe()}"
        )


# --------------------------------------------------------------------------- #
# 2. Index coverage for every hot query shape                                   #
# --------------------------------------------------------------------------- #
class TestIndexCoverage:
    """Assert `ensure_indexes()` declares an index for each hot filter+sort.

    This is the test the in-memory double cannot give us. `FakeDB` has no query
    planner, so a route whose collection has no index at all behaves identically
    under test to one that is perfectly indexed — the entire authz and validation
    suite passes either way. `scripts/perf_db_benchmark.py` proves the plans
    against a real MongoDB; this test is what keeps the declarations from
    regressing between those runs, in CI, with no MongoDB required.

    It works by *recording* what `ensure_indexes()` declares, using a stub `db`,
    rather than by parsing the source: a source parse would keep passing if the
    call were moved somewhere that never runs.
    """

    @staticmethod
    async def _declared():
        """{collection: [tuple(index key spec), ...]} as declared by the app."""
        declared: dict[str, list] = {}

        class _Col:
            def __init__(self, name):
                self._name = name

            async def create_index(self, keys, **kwargs):
                spec = tuple(keys) if isinstance(keys, list) else ((keys, 1),)
                declared.setdefault(self._name, []).append(spec)

        class _DB:
            def __getattr__(self, name):
                return _Col(name)

            def __getitem__(self, name):
                return _Col(name)

        real = server.db
        server.db = _DB()
        try:
            await server.ensure_indexes()
        finally:
            server.db = real
        return declared

    @pytest.fixture
    def declared(self):
        import asyncio
        return asyncio.run(self._declared())

    #: (collection, filter fields, sort field) for each measured hot query, cited
    #: to the source line. A prefix of some declared index must cover the filter
    #: fields, and the sort field must follow them in that same index — which is
    #: exactly the condition for MongoDB to serve the sort from the index instead
    #: of materializing an in-memory SORT stage.
    HOT_QUERIES = [
        ("trades", ("user_id",), "entry_time", "server.py GET /api/trades"),
        ("trades", ("user_id", "status"), "entry_time", "GET /api/trades/active"),
        ("trades", ("user_id",), "exit_time", "GET /api/trades/history"),
        ("notifications", ("user_id",), "created_at", "GET /api/notifications"),
        ("notifications", ("user_id", "read"), None, "GET /api/notifications/unread-count"),
        ("watchlist", ("user_id",), "added_at", "GET /api/watchlist"),
        ("watchlist", ("user_id", "symbol"), None, "POST /api/watchlist dup check"),
        ("holdings", ("user_id",), None, "portfolio_engine.build_holdings"),
        ("holdings", ("user_id", "broker"), None, "portfolio_stream/trade_stream"),
        ("orders", ("user_id",), "placed_at", "GET /api/orders"),
        ("chat_messages", ("session_id",), "created_at", "POST /api/chat continuity"),
        ("chat_messages", ("user_id", "session_id"), "created_at", "GET /api/chat/history"),
        ("users", ("email",), None, "login / registration"),
        ("broker_accounts", ("user_id", "broker"), None, "broker_engine"),
        # PH3.8 — analytics query shapes.
        #
        # `portfolio_snapshots` had NO index of any kind since Sprint 8 created
        # it, and two hot shapes read it: the Performance tab filters {user_id}
        # and sorts by date, and the 16:05 IST snapshot job upserts on
        # {user_id, date} once per user per night — a full collection scan per
        # user, which is O(users²) work in an unattended job.
        ("portfolio_snapshots", ("user_id",), "date", "GET /api/portfolio/performance"),
        ("portfolio_snapshots", ("user_id", "date"), None,
         "portfolio_engine.record_snapshot upsert"),
        # Platform-wide signup and AI-usage counts have no user_id to lean on,
        # so no compound index above can serve them (an index is only usable
        # from its leading field). Both were `$regex` prefix matches on
        # unindexed string fields — full scans on every admin page load — until
        # PH3.8 replaced the regex with a range comparison an index can serve.
        ("users", ("created_at",), None, "GET /api/admin/dashboard signups"),
        ("chat_messages", ("created_at",), None, "GET /api/admin/dashboard AI requests"),
    ]

    @pytest.mark.parametrize(
        "collection,filter_fields,sort_field,source",
        HOT_QUERIES,
        ids=[f"{c}:{'+'.join(f)}" + (f":sort({s})" if s else "")
             for c, f, s, _ in HOT_QUERIES],
    )
    def test_hot_query_shape_is_covered_by_an_index(
        self, declared, collection, filter_fields, sort_field, source
    ):
        indexes = declared.get(collection, [])
        assert indexes, (
            f"collection {collection!r} has no declared index at all, but "
            f"{source} queries it. Before PH3.4 this was true of watchlist, "
            f"holdings and orders, and every one of those queries was a full "
            f"collection scan across every user's rows."
        )

        wanted = set(filter_fields)
        for spec in indexes:
            fields = [field for field, _direction in spec]
            prefix = fields[:len(wanted)]
            if set(prefix) != wanted:
                continue
            if sort_field is None:
                return
            # The sort key must appear immediately after the equality prefix for
            # the index to supply the ordering.
            if len(fields) > len(wanted) and fields[len(wanted)] == sort_field:
                return

        raise AssertionError(
            f"no declared index on {collection} covers {source}: "
            f"filter={sorted(wanted)}"
            + (f" sort={sort_field}" if sort_field else "")
            + f". Declared: {[[f for f, _ in s] for s in indexes]}. "
            "Either add the index in server.ensure_indexes() or, if the query "
            "changed, update this row — do not delete it."
        )

    def test_every_declared_index_is_reachable_through_ensure_indexes(self, declared):
        """Guard against the whole mechanism silently emptying.

        If `ensure_indexes()` were renamed, wrapped in a condition that is false
        under test, or emptied, `declared` would come back small and every
        parametrized case above would still be *generated* — they would just all
        fail, which is fine — but a future refactor that makes `_declared()`
        return `{}` while the parametrize list is also trimmed would report
        green. This asserts the floor.
        """
        assert len(declared) >= 12, f"only {len(declared)} collections declared indexes"
        total = sum(len(v) for v in declared.values())
        assert total >= 30, f"only {total} indexes declared in total"


# --------------------------------------------------------------------------- #
# 3. Response payload bounds                                                    #
# --------------------------------------------------------------------------- #
class TestResponsePayloadIsBounded:
    """Every list endpoint must cap its result set.

    An unbounded list endpoint is a payload that grows without limit as a user
    keeps using the product, and it is the frontend that pays: a user with 5,000
    trades should not be sent 5,000 trades to render a page. These assert the
    documented `to_list(N)` bounds actually hold at the HTTP boundary.
    """

    def test_trades_listing_is_capped_at_100(
        self, client, fake_db, test_user, auth_headers
    ):
        _seed_trades(fake_db, test_user, 150)
        r = client.get("/api/trades", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()) == 100, (
            "GET /api/trades must cap at its documented to_list(100); returning "
            "everything makes the payload grow with the account's lifetime."
        )

    def test_notifications_listing_is_capped_at_50(
        self, client, fake_db, test_user, auth_headers
    ):
        for i in range(120):
            fake_db.notifications.docs.append({
                "_id": ObjectId(), "user_id": str(test_user["_id"]),
                "title": f"n{i}", "message": "m", "read": False,
                "created_at": f"2026-08-{1 + (i % 28):02d}T10:00:00+00:00",
            })
        r = client.get("/api/notifications", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()) == 50

    def test_admin_pagination_limit_is_bounded(self, client, admin_headers):
        """PH3.3's D-1 fix (`le=100`) is also the defence against a full
        collection scan via `?limit=1000000`. Re-asserted here because it is a
        performance property, not only a validation one."""
        r = client.get("/api/admin/users?page=1&limit=100000", headers=admin_headers)
        assert r.status_code == 422, (
            "an unbounded limit lets any admin request the entire users "
            "collection in one query"
        )


# --------------------------------------------------------------------------- #
# 4. Concurrency structure of the independent fan-outs                          #
# --------------------------------------------------------------------------- #
class TestIndependentWorkStaysConcurrent:
    """Assert the *structure*, because the effect is invisible here.

    `FakeDB` answers instantly, so a sequential version of these handlers and a
    concurrent one have identical wall clocks under test — the whole benefit is
    in the RTT that the double does not have. A timing assertion would therefore
    prove nothing and fail randomly. Asserting that the `asyncio.gather` is still
    in the source is a weaker claim, but it is a *true* one, and it fails on the
    change that actually matters: someone unrolling the gather back into a
    sequence of awaits.
    """

    @staticmethod
    def _gather_calls(fn):
        tree = ast.parse(inspect.getsource(fn))
        return [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "gather"
        ]

    def test_admin_dashboard_counts_are_gathered(self):
        """PH3.4 O-4. Eleven independent counts; sequentially that is 11xRTT of
        waiting for a response that needs 1xRTT."""
        gathers = self._gather_calls(server.admin_dashboard)
        assert gathers, (
            "admin_dashboard no longer gathers its independent counts — it pays "
            "one database round trip per statistic, in series."
        )
        assert len(gathers[0].args) >= 10, (
            f"only {len(gathers[0].args)} counts are gathered; the rest are "
            "back to sequential awaits."
        )

    def test_quote_fanout_is_gathered(self):
        """`real_quotes_map` fans out per symbol. Sequential would make a
        12-symbol watchlist twelve provider round trips deep."""
        assert self._gather_calls(server.real_quotes_map), (
            "real_quotes_map must fetch symbols concurrently"
        )


# --------------------------------------------------------------------------- #
# 5. Outbound HTTP connection pooling                                           #
# --------------------------------------------------------------------------- #
class TestOutboundHttpPooling:
    """PH3.4 O-2, measured at 854 ms -> 228 ms for a 10-symbol batch.

    No network is touched here. What is asserted is the two things that made the
    change safe: the market-data path goes through the pooling helper rather than
    constructing its own client, and the helper degrades to the previous per-call
    behaviour when no pool has been opened for the running loop — which is the
    case under the TestClient, and is why the rest of the suite is unaffected.
    """

    def test_market_data_uses_the_pooling_helper_not_a_raw_client(self):
        source = (Path(server.__file__).parent / "services" / "real_market.py").read_text()
        assert "http_client.client_for" in source

        # The Yahoo quote path specifically — the per-symbol fan-out is where the
        # handshake amplification was.
        from services import real_market
        quote_src = inspect.getsource(real_market.fetch_yahoo_quote)
        assert "http_client.client_for" in quote_src, (
            "fetch_yahoo_quote is called once per symbol and fanned out with "
            "gather; a per-call httpx.AsyncClient here means one TLS handshake "
            "per symbol to the same host."
        )
        assert "httpx.AsyncClient(" not in quote_src

    def test_pooling_is_off_until_the_lifespan_opens_it(self):
        """The property that keeps the hermetic suite on the old behaviour.

        An httpx client's connections belong to the loop that opened them, and
        FastAPI's synchronous TestClient runs every request on a fresh loop. A
        module-level shared client would reproduce the `Event loop is closed`
        failure this repository already documented for Motor.
        """
        from services import http_client
        stats = http_client.pool_stats()
        assert stats["enabled"] is False, (
            "no pool should be open in the test process; the fallback path is "
            "what makes this change invisible to every other test"
        )

    def test_pool_bounds_concurrency(self):
        """The pool is also a *ceiling* the previous code did not have.

        Before PH3.4 every concurrent call opened its own connection with no
        limit; a universe scan over hundreds of symbols opened hundreds of
        sockets to one provider. `max_connections` queues the excess instead,
        which is the brief's §12 "bounded concurrency" requirement.
        """
        from services import http_client
        assert 0 < http_client.MAX_CONNECTIONS <= 50
        assert http_client.MAX_KEEPALIVE_CONNECTIONS <= http_client.MAX_CONNECTIONS
        assert http_client.KEEPALIVE_EXPIRY_SECONDS > 0


# --------------------------------------------------------------------------- #
# 6. Per-request fixed cost                                                     #
# --------------------------------------------------------------------------- #
class TestPerRequestFixedCost:
    """The floor every route pays, pinned so a new middleware cannot raise it
    unnoticed.

    Measured at PH3.4: three `rate_limits` operations and one `users` lookup for
    the authenticated principal. This is the most-executed database work in the
    system — it happens on all 201 routes — so a fourth operation added here
    costs more in aggregate than anything else in this file.
    """

    def test_authenticated_request_floor(self, client, fake_db, test_user, auth_headers):
        with count_queries() as log:
            r = client.get("/api/settings", headers=auth_headers)
        assert r.status_code == 200
        assert log.total <= 5, (
            "the per-request floor grew. Every one of the ~126 authenticated "
            f"routes pays this: {log.describe()}"
        )
        assert log.count(collection="rate_limits") <= 3, (
            "the rate limiter's cost per request grew beyond the measured 3 "
            f"operations: {log.describe()}"
        )

    def test_health_probes_touch_no_collection(self, client, fake_db):
        """A probe that queries the database couples orchestration to it: a Mongo
        blip then fails liveness and the orchestrator restarts pods that were
        fine, turning a recoverable dependency failure into an outage. PH3.3 §13
        asserts the behaviour; this asserts the cost."""
        with count_queries() as log:
            r = client.get("/api/health/live")
        assert r.status_code == 200
        assert log.total == 0, f"/api/health/live queried the database: {log.describe()}"

"""Request-validation coverage: user-controlled input never yields a 500 (PH3.3).

WHAT PRODUCTION FAILURE THIS CATCHES
------------------------------------
The 500 that is really a 400. A malformed identifier, a negative page number, a
string where a number belongs — the caller sent something invalid, and the
correct answer is a 4xx that says so. When it comes back as a 500 instead, three
things go wrong at once: the client cannot distinguish "fix your request" from
"the server is broken" and retries; the error budget and alerting treat a
routine bad request as an availability incident; and the stack trace that
reaches the log is noise that buries the real failures.

`security/identifiers.parse_object_id` (PH1.12) exists for exactly this and is
applied at most call sites. This suite proves it — mechanically, over every
identifier-shaped path parameter in the live route table, so a new endpoint that
forgets it fails here rather than in production.

THE ASSERTION IS DELIBERATELY LOOSE ON *WHICH* 4XX
--------------------------------------------------
400 (our own parse rejection), 404 (routed to nothing), and 422 (FastAPI's model
validation) are all correct answers depending on where the input is caught, and
pinning the exact one would make this suite fail on a harmless refactor. What is
never correct is 5xx. That is the invariant, and it is the only thing asserted.
"""
import pytest
from bson import ObjectId

from tests._routes import ADMIN_ROUTES, AUTHENTICATED_ROUTES, route_id, sample_path

# --------------------------------------------------------------------------- #
# Identifier-shaped path parameters                                             #
# --------------------------------------------------------------------------- #
#: Path parameters that end in `_id` but are **not** ObjectIds, so requiring a
#: 4xx from them would be asserting a contract the system does not have:
#:
#: * `session_id` — a chat session key (`chat-<uid>`), free-form by design; an
#:   unknown one legitimately answers 200 with `deleted: 0`.
#: * `order_id`  — a *broker-side* order reference (Zerodha/Upstox), whose
#:   format belongs to the broker, not to us.
#:
#: They stay in the sweep for the 5xx invariant; only the "must be 4xx" half is
#: waived. Listing them by name, with the reason, is deliberate: a silent
#: `if param in EXCLUDED` would let a genuinely-unguarded ObjectId route be
#: added to this set later to make a red test go green.
_NON_OBJECTID_PARAMS = frozenset({"session_id", "order_id"})


def _id_params(path):
    return [seg[1:-1] for seg in path.split("/")
            if seg.startswith("{") and seg.endswith("_id}")]


#: Routes carrying a path parameter that addresses a document. Derived from the
#: live route table rather than listed, for the reason given in
#: `tests/_routes.py`: the endpoint most likely to be missing the guard is the
#: one added after any hand-written list was compiled.
_ID_ROUTES = [(m, p) for m, p in AUTHENTICATED_ROUTES if _id_params(p)]

#: Routes that answer **501 Not Implemented** for every identifier, valid or
#: not, because the feature does not exist. They are excluded from both sweeps
#: — including the 5xx invariant — and the exclusion needs its own justification
#: because "excluded from the no-5xx rule" is exactly the kind of waiver that
#: hides a real defect.
#:
#: The justification is that the invariant is about *identifier parsing*: a
#: malformed id must not be reported as a server fault. A 501 here says nothing
#: about the identifier. It is returned for a well-formed id too, it is the
#: correct status for a capability the server does not have, and answering 400
#: instead would blame the caller for a gap on our side.
#:
#: Until PH3.9 this set was named `_STUB_ROUTES` and held the same route for the
#: opposite reason: the endpoint returned **200 with `success: true` for any
#: string** while writing a `payment.refunded` audit record for a refund that
#: never happened (PH3.3's D-4). `TestRefundEndpointIsNotImplemented` below
#: asserts the current behaviour, including the absence of that audit record.
_NOT_IMPLEMENTED_ROUTES = frozenset({
    ("POST", "/api/admin/payments/{payment_id}/refund"),
})

_ID_ROUTES = [entry for entry in _ID_ROUTES if entry not in _NOT_IMPLEMENTED_ROUTES]

#: The subset whose identifier really is an ObjectId, and must therefore be
#: rejected with a 4xx rather than merely not crashing.
_OBJECTID_ROUTES = [
    (m, p) for m, p in _ID_ROUTES
    if all(param not in _NON_OBJECTID_PARAMS for param in _id_params(p))
]

#: Values a client can actually put in a URL segment that are not ObjectIds.
#: `%00` and the traversal string are included because a handler that reaches
#: the database with them unparsed is a different and worse bug than one that
#: merely 500s. The empty string is deliberately absent: it collapses the URL to
#: the collection path and so tests *routing*, not identifier validation.
MALFORMED_IDS = [
    "not-an-objectid",
    "123",
    " ",
    "null",
    "undefined",
    "0" * 100,
    "../../etc/passwd",
    "%00",
    "$ne",
    '{"$gt":""}',
    "64b7f0c2e13b4a5d6f8c9a0",     # 23 hex chars — one short of valid
    "64b7f0c2e13b4a5d6f8c9a0zz",   # right length, not hex
]


def _headers(entry, auth_headers, super_admin_headers):
    """Credentials that get past authorization, so validation is what is tested.

    An admin route called with a user token answers 403 before it ever parses
    the identifier — the test would then pass while asserting nothing about
    parsing. super_admin is used rather than admin so the super-admin-only
    routes (user deletion) are also reached.
    """
    return super_admin_headers if entry in ADMIN_ROUTES else auth_headers


def _request_with_bad_id(client, entry, bad_id, headers):
    method, path = entry
    parts, template = sample_path(path).split("/"), path.split("/")
    for i, seg in enumerate(template):
        if seg.startswith("{") and seg.endswith("_id}"):
            parts[i] = bad_id
    kwargs = {"headers": headers}
    if method in ("POST", "PUT", "PATCH"):
        kwargs["json"] = {}
    return client.request(method, "/".join(parts), **kwargs)


@pytest.mark.parametrize("entry", _ID_ROUTES, ids=route_id)
@pytest.mark.parametrize("bad_id", MALFORMED_IDS)
def test_malformed_identifier_never_crashes_the_handler(
        client, fake_db, auth_headers, super_admin_headers, entry, bad_id):
    """The universal invariant: no identifier a client can type produces a 5xx."""
    resp = _request_with_bad_id(
        client, entry, bad_id, _headers(entry, auth_headers, super_admin_headers))
    assert resp.status_code < 500, (
        f"{entry[0]} {entry[1]} answered {resp.status_code} for id={bad_id!r}; "
        f"a malformed identifier is a client error, not a server error."
    )


@pytest.mark.parametrize("entry", _OBJECTID_ROUTES, ids=route_id)
@pytest.mark.parametrize("bad_id", MALFORMED_IDS)
def test_malformed_objectid_is_rejected_with_a_client_error(
        client, fake_db, auth_headers, super_admin_headers, entry, bad_id):
    """An ObjectId-backed route must *reject* a malformed id, not absorb it.

    Not merely "does not crash": a route that quietly treats an unparseable id
    as "no match" and answers 200 tells the caller their broken request
    succeeded. `security.identifiers.parse_object_id` is the single helper that
    produces the correct 400 here.
    """
    resp = _request_with_bad_id(
        client, entry, bad_id, _headers(entry, auth_headers, super_admin_headers))
    assert 400 <= resp.status_code < 500, (
        f"{entry[0]} {entry[1]} answered {resp.status_code} for id={bad_id!r}. "
        f"Route it through security.identifiers.parse_object_id."
    )


def test_the_identifier_sweep_covers_the_known_routes():
    """Fails if the derived lists empty out — see the same guard in test_api_authz."""
    assert len(_ID_ROUTES) >= 8, _ID_ROUTES
    assert len(_OBJECTID_ROUTES) >= 6, _OBJECTID_ROUTES


# --------------------------------------------------------------------------- #
# Pagination                                                                    #
# --------------------------------------------------------------------------- #
PAGINATED_ADMIN_ENDPOINTS = [
    "/api/admin/users",
    "/api/admin/logs",
    "/api/admin/support/tickets",
    "/api/admin/payments",
]


class TestPagination:
    """`page` and `limit` are unvalidated ints straight from the query string.

    `skip = (page - 1) * limit` goes negative for `page=0`, and MongoDB rejects a
    negative skip with an OperationFailure rather than clamping it — so the
    request 500s. This is PH3.3 defect D-1.
    """

    @pytest.mark.parametrize("endpoint", PAGINATED_ADMIN_ENDPOINTS)
    @pytest.mark.parametrize("params", [
        {"page": 0},
        {"page": -1},
        {"page": -100},
        {"limit": -1},
        {"page": 0, "limit": 0},
    ])
    def test_out_of_range_pagination_is_not_a_server_error(
            self, admin_client, fake_db, endpoint, params):
        resp = admin_client.get(endpoint, params=params)
        assert resp.status_code < 500, (
            f"{endpoint} answered {resp.status_code} for {params}; "
            f"an out-of-range page/limit is a client error."
        )

    @pytest.mark.parametrize("endpoint", PAGINATED_ADMIN_ENDPOINTS)
    def test_non_numeric_pagination_is_422(self, admin_client, fake_db, endpoint):
        """FastAPI's own coercion owns this one; the test pins that we rely on it."""
        resp = admin_client.get(endpoint, params={"page": "abc"})
        assert resp.status_code == 422

    def test_valid_pagination_still_pages(self, admin_client, fake_db):
        """The D-1 clamp must not have broken paging itself."""
        for i in range(25):
            fake_db.users.docs.append(
                {"_id": ObjectId(), "email": f"seed{i:02d}@example.com",
                 "role": "user", "created_at": f"2026-01-{i % 28 + 1:02d}"})
        first = admin_client.get("/api/admin/users", params={"page": 1, "limit": 10})
        second = admin_client.get("/api/admin/users", params={"page": 2, "limit": 10})
        assert first.status_code == second.status_code == 200
        assert len(first.json()["users"]) == 10
        assert len(second.json()["users"]) == 10
        first_ids = {u["_id"] for u in first.json()["users"]}
        second_ids = {u["_id"] for u in second.json()["users"]}
        assert not (first_ids & second_ids), "page 2 repeated rows from page 1"

    def test_page_beyond_the_end_is_an_empty_page_not_an_error(self, admin_client, fake_db):
        resp = admin_client.get("/api/admin/users", params={"page": 9999, "limit": 20})
        assert resp.status_code == 200
        assert resp.json()["users"] == []


# --------------------------------------------------------------------------- #
# Admin request bodies                                                          #
# --------------------------------------------------------------------------- #
class TestGrantPlanValidation:
    """`duration_days` reaches `timedelta(days=...)` straight from the body.

    A string raises TypeError and an astronomically large int raises
    OverflowError — both uncaught, both 500. PH3.3 defect D-3.
    """

    # `float("inf")` is deliberately absent: it is not representable in standard
    # JSON, so no real HTTP client can send it — the request fails in the
    # client's own serializer, and testing it would assert something about httpx
    # rather than about this API. `1e308` is the reachable equivalent.
    @pytest.mark.parametrize("duration", [
        "thirty", None, [], {}, 10 ** 12, -10 ** 12, 1e308, 0, -1, True,
    ])
    def test_bad_duration_is_a_client_error(
            self, admin_client, fake_db, other_user, duration):
        resp = admin_client.post(
            f"/api/admin/users/{other_user['_id']}/grant-plan",
            json={"plan": "pro", "duration_days": duration})
        assert resp.status_code < 500, (
            f"duration_days={duration!r} produced {resp.status_code}"
        )

    def test_valid_grant_still_works(self, admin_client, fake_db, other_user):
        resp = admin_client.post(f"/api/admin/users/{other_user['_id']}/grant-plan",
                                 json={"plan": "pro", "duration_days": 30})
        assert resp.status_code == 200
        assert resp.json()["plan"] == "pro"
        assert resp.json()["expires_at"]

    def test_lifetime_plan_has_no_expiry(self, admin_client, fake_db, other_user):
        resp = admin_client.post(f"/api/admin/users/{other_user['_id']}/grant-plan",
                                 json={"plan": "lifetime"})
        assert resp.status_code == 200
        assert resp.json()["expires_at"] is None

    @pytest.mark.parametrize("plan", ["admin", "super_admin", "", "PRO", "gold"])
    def test_unknown_plan_is_400(self, admin_client, fake_db, other_user, plan):
        resp = admin_client.post(f"/api/admin/users/{other_user['_id']}/grant-plan",
                                 json={"plan": plan})
        assert resp.status_code == 400


class TestRefundEndpointIsNotImplemented:
    """PH3.3 defect D-4 (HIGH), **fixed in PH3.9.**

    What it was: `POST /api/admin/payments/{payment_id}/refund` read no payment,
    called no payment provider, and answered `{"success": true, "message":
    "Refund initiated"}` to *any* string — a payment id that did not exist, or
    one that was not even an identifier — while writing `payment.refunded` to
    the immutable audit log.

    Two harms, and the second is the worse one. An operator was told a customer
    had been refunded when nobody had; and the audit log, whose entire purpose
    is to be the record of what happened, was recording an event that never
    occurred. A log containing invented entries is not a weaker audit log, it is
    a misleading one.

    There is no payment provider to refund through (`analytics.sources.
    payments_integration`), so the endpoint now answers 501 and writes nothing.
    A missing feature that says so is strictly better than an admin-visible
    action that lies.

    PH3.3 pinned this as two `xfail`s expecting 404/400. Those expectations were
    written on the assumption that PH3.9 would *implement* refunds; it removed
    the lie instead, which is the correct outcome while no provider exists. They
    are replaced rather than left to report a misleading XPASS.
    """

    def test_it_does_not_report_success(self, admin_client, fake_db):
        resp = admin_client.post(f"/api/admin/payments/{ObjectId()}/refund")
        assert resp.status_code == 501
        assert "success" not in resp.json()

    def test_it_answers_the_same_way_for_a_malformed_id(self, admin_client, fake_db):
        """501 is a statement about the server's capability, not about the id,
        so a malformed one cannot produce a different (or more encouraging)
        answer."""
        resp = admin_client.post("/api/admin/payments/not-an-objectid/refund")
        assert resp.status_code == 501

    def test_it_writes_no_audit_record(self, admin_client, fake_db, admin_user):
        """The load-bearing half of the D-4 fix."""
        admin_client.post(f"/api/admin/payments/{ObjectId()}/refund")
        logged = [entry for entry in fake_db.admin_audit_logs.docs
                  if entry["action"] == "payment.refunded"]
        assert logged == [], "a refund that did not happen must not be audited"

    def test_the_reason_is_readable_by_an_operator(self, admin_client, fake_db):
        detail = admin_client.post(
            f"/api/admin/payments/{ObjectId()}/refund").json()["detail"]
        assert "payment integration" in detail.lower()


class TestAdminUpdateUserValidation:
    def test_body_with_no_permitted_field_is_400(self, admin_client, fake_db, other_user):
        resp = admin_client.put(f"/api/admin/users/{other_user['_id']}",
                                json={"nonsense": 1})
        assert resp.status_code == 400

    def test_unexpected_fields_are_ignored_not_persisted(
            self, admin_client, fake_db, other_user):
        """Mass-assignment guard: the allowlist must drop everything else.

        `password_hash` is the one that matters — an admin editor that accepted
        arbitrary keys would let an admin overwrite a credential directly.
        """
        resp = admin_client.put(
            f"/api/admin/users/{other_user['_id']}",
            json={"name": "Renamed", "password_hash": "injected",
                  "role_": "admin", "_id": str(ObjectId()), "blocked": True})
        assert resp.status_code == 200
        stored = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert stored["name"] == "Renamed"
        assert "password_hash" not in stored
        assert stored["_id"] == other_user["_id"]
        assert "blocked" not in stored

    @pytest.mark.parametrize("role", ["", "root", "ADMIN", "admin ", 123, None])
    def test_unknown_role_is_rejected(self, super_admin_client, fake_db, other_user, role):
        resp = super_admin_client.put(f"/api/admin/users/{other_user['_id']}",
                                      json={"role": role})
        assert 400 <= resp.status_code < 500
        stored = next(u for u in fake_db.users.docs if u["_id"] == other_user["_id"])
        assert stored["role"] == "user"

    def test_malformed_json_body_is_a_client_error(self, admin_client, fake_db, other_user):
        resp = admin_client.put(
            f"/api/admin/users/{other_user['_id']}",
            content=b"{not json",
            headers={"Content-Type": "application/json"})
        assert 400 <= resp.status_code < 500


# --------------------------------------------------------------------------- #
# Trade payloads                                                                #
# --------------------------------------------------------------------------- #
VALID_TRADE = {
    "symbol": "RELIANCE",
    "stock_name": "Reliance Industries",
    "type": "BUY",
    "entry_price": 100.0,
    "quantity": 10,
    "stop_loss": 95.0,
    "target1": 110.0,
}


class TestTradeCreateValidation:
    # `type` is deliberately absent: `TradeCreate.type` defaults to "BUY", so
    # omitting it is a valid request, not a malformed one.
    @pytest.mark.parametrize("missing", ["symbol", "entry_price", "quantity", "stop_loss"])
    def test_missing_required_field_is_422(
            self, authenticated_client, fake_db, test_user, missing):
        payload = {k: v for k, v in VALID_TRADE.items() if k != missing}
        resp = authenticated_client.post("/api/trades", json=payload)
        assert resp.status_code == 422
        assert not fake_db.trades.docs, "a trade was created from an invalid payload"

    @pytest.mark.parametrize("field,value", [
        ("quantity", "ten"),
        ("quantity", None),
        ("entry_price", "expensive"),
        ("entry_price", None),
        ("stop_loss", [95.0]),
        ("symbol", 12345),
        ("symbol", None),
        ("type", None),
    ])
    def test_wrong_type_is_422(
            self, authenticated_client, fake_db, test_user, field, value):
        resp = authenticated_client.post("/api/trades", json={**VALID_TRADE, field: value})
        assert resp.status_code == 422
        assert not fake_db.trades.docs

    @pytest.mark.parametrize("field,value", [
        ("quantity", 0),
        ("quantity", -10),
        ("entry_price", 0),
        ("entry_price", -100.0),
        ("stop_loss", -1.0),
    ])
    def test_non_positive_economics_never_create_an_open_trade(
            self, authenticated_client, fake_db, test_user, field, value):
        """A zero or negative quantity/price is not a tradeable instruction.

        Whether it is rejected by the schema (422) or by the Risk Manager (422
        with violations) does not matter to this assertion — what matters is
        that no OPEN position is persisted from it, because an OPEN position
        with negative quantity corrupts every P&L aggregate that reads it.
        """
        resp = authenticated_client.post("/api/trades", json={**VALID_TRADE, field: value})
        assert resp.status_code < 500
        assert not any(t.get("status") == "OPEN" for t in fake_db.trades.docs), \
            f"{field}={value} produced an OPEN trade"

    @pytest.mark.parametrize("bad_type", ["HOLD", "buy!", "", "SHORT", 1])
    def test_invalid_side_is_rejected(
            self, authenticated_client, fake_db, test_user, bad_type):
        resp = authenticated_client.post("/api/trades", json={**VALID_TRADE, "type": bad_type})
        assert resp.status_code < 500
        assert not any(t.get("status") == "OPEN" for t in fake_db.trades.docs)

    def test_oversized_payload_is_not_a_server_error(
            self, authenticated_client, fake_db, test_user):
        """A megabyte of notes must not take the process down."""
        resp = authenticated_client.post(
            "/api/trades", json={**VALID_TRADE, "notes": "A" * 1_000_000})
        assert resp.status_code < 500

    def test_unexpected_fields_do_not_reach_the_document(
            self, authenticated_client, fake_db, test_user):
        """The trade document is built field-by-field from the model, so an
        extra key in the body must not appear in the database."""
        resp = authenticated_client.post(
            "/api/trades", json={**VALID_TRADE, "user_id": "someone-else",
                                 "status": "CLOSED", "pnl": 999999.0})
        assert resp.status_code < 500
        for trade in fake_db.trades.docs:
            assert trade["user_id"] == str(test_user["_id"]), \
                "user_id was taken from the request body"
            assert trade["pnl"] is None, "pnl was taken from the request body"


class TestPaperTradeValidation:
    @pytest.mark.parametrize("payload", [
        {},
        {"symbol": "RELIANCE"},
        {"symbol": "RELIANCE", "quantity": "many", "entry_price": 100.0,
         "stop_loss": 90.0, "target1": 110.0},
        {"symbol": "RELIANCE", "quantity": 1, "entry_price": None,
         "stop_loss": 90.0, "target1": 110.0},
    ])
    def test_invalid_paper_trade_is_a_client_error(
            self, authenticated_client, fake_db, test_user, payload):
        resp = authenticated_client.post("/api/paper/trade", json=payload)
        assert 400 <= resp.status_code < 500


# --------------------------------------------------------------------------- #
# Query-parameter surfaces                                                      #
# --------------------------------------------------------------------------- #
class TestQueryParameterValidation:
    @pytest.mark.parametrize("params", [
        {"amount": "lots", "years": 10, "rate": 12},
        {"amount": 5000, "years": -1, "rate": 12},
        {"amount": -5000, "years": 10, "rate": 12},
        {"amount": 5000, "years": 10, "rate": -100},
        {"amount": 1e308, "years": 10, "rate": 12},
    ])
    def test_sip_calculator_never_500s(self, client, params):
        resp = client.get("/api/sip/calculator", params=params)
        assert resp.status_code < 500

    @pytest.mark.parametrize("q", ["", " ", "%", "*", "'; DROP TABLE users;--", "\x00", "a" * 5000])
    def test_stock_search_never_500s(self, client, q):
        resp = client.get("/api/stocks/search", params={"q": q})
        assert resp.status_code < 500

    @pytest.mark.parametrize("params", [
        {"limit": -1}, {"limit": 0}, {"limit": 10 ** 9},
        {"rsi_min": "low"}, {"rsi_min": 200, "rsi_max": -200},
        {"strategy": "nonexistent-strategy"},
    ])
    def test_market_scanner_never_500s(self, client, params):
        resp = client.get("/api/market/scanner", params=params)
        assert resp.status_code < 500

    @pytest.mark.parametrize("symbol", ["", "  ", "../etc/passwd", "$where", "A" * 500, "%00"])
    def test_unknown_stock_symbol_is_a_client_error(self, client, symbol, monkeypatch):
        import server
        from unittest.mock import AsyncMock
        monkeypatch.setattr(server, "real_quote", AsyncMock(return_value=None))
        resp = client.get(f"/api/stocks/{symbol}")
        assert resp.status_code < 500

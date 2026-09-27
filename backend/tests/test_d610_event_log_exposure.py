"""D6.10-P0 — the process-global EventBus log is not reachable over HTTP.

THE DEFECT THIS FILE PINS
-------------------------
`GET /api/market/events` returned `EventBus.recent_events()`: the *whole*
in-process 500-entry event log, to any caller, with no credential. That log is
not market telemetry. Every private domain publishes into it —

    trade.updated          → a user's OPEN positions and live P&L
    portfolio.updated      → a user's holdings and total value
    notification.created   → a user's notification title and body
    broker.order.updated   → a user's broker orders, per account
    trade.review.ready     → a user's trade review

— each payload stamped with the owning `user_id`. Executed against the real
payload shapes, an anonymous `GET /api/market/events` returned another user's
id, positions, notifications and broker orders verbatim.

WHY THE FIX IS DELETION, NOT AUTHENTICATION
-------------------------------------------
Adding `Depends(get_current_user)` would have turned an internet-wide leak into
a tenant-wide one: the log has no owner dimension to filter on, so every
signed-in user would still have read every other user's account activity. The
route had no frontend call site and no service caller, so it was removed.

WHAT THIS FILE ASSERTS
----------------------
1. The route is gone — from the app's route table and over the wire.
2. It is gone *while the log still holds private payloads*. Without that
   control, "no leak" is also what an empty event bus looks like.
3. No other production call site reaches for the event payloads (§2). The
   surviving callers take `len(...)` — a count is not tenant data. This is the
   mechanical half: re-exposing the log anywhere in `backend/` fails here, not
   only re-adding this one path. The sweep matches the accessor
   (`recent_events()`) *and* the buffer behind it (`_event_log`, however
   spelled), because a matcher that knows only the accessor is one a one-line
   edit walks around — which is what mutation 4b did.
4. The scoped activity surfaces D6.1/S4 built (`/api/market/activity-feed`,
   `/api/ai/activity`, `/api/ai-activity`) still answer, still carry the
   platform stream, and did **not** inherit the deleted route's content.

`PINNED_PUBLIC_ROUTES` in `test_d68_entitlements.py` is the other half: the
route's absence from that pin is asserted there by set equality, so re-adding
the endpoint without a credential fails that test too.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

import server

DELETED_PATH = "/api/market/events"

#: The payloads an anonymous caller used to be able to read. Each is the shape
#: its publisher actually sends (`services/trade_stream.py`,
#: `services/portfolio_stream.py`, `services/notification_service.py`,
#: `services/broker_engine.py`, `services/trade_review.py`).
_VICTIM = "victim-user-id"
_PRIVATE_EVENTS = [
    ("trade.updated", {"user_id": _VICTIM, "trades": [
        {"symbol": "RELIANCE", "qty": 10, "entry": 2900.0, "pnl": 4321.0}]}),
    ("portfolio.updated", {"user_id": _VICTIM, "total_value": 1234567.0,
                           "holdings": [{"symbol": "TCS", "qty": 5}]}),
    ("notification.created", {"user_id": _VICTIM, "notification_id": "n1",
                              "type": "order", "title": "Order executed",
                              "message": "BUY 10 RELIANCE @ 2950",
                              "severity": "info"}),
    ("broker.order.updated", {"user_id": _VICTIM, "broker": "zerodha",
                              "broker_account_id": "zerodha:acct-1",
                              "order": {"order_id": "o1", "status": "FILLED"}}),
    ("trade.review.ready", {"user_id": _VICTIM, "trade_id": "t1",
                            "review": "entered late, sized too large"}),
]

#: Strings that must not appear in any response body. Drawn from the payloads
#: above — the victim's id, and one distinctive value per private domain.
_SECRETS = (_VICTIM, "BUY 10 RELIANCE @ 2950", "1234567", "zerodha:acct-1",
            "entered late, sized too large", "4321")


@pytest.fixture
def event_log_with_private_payloads():
    """Seed the real `event_bus` with the private events, then restore it.

    Restores rather than clears: the log is a process-global singleton shared
    with every other test in the session, so this fixture must leave it exactly
    as it found it.
    """
    from services.market_engine.event_bus import event_bus

    saved = list(event_bus._event_log)
    event_bus._event_log = []
    for event_type, data in _PRIVATE_EVENTS:
        asyncio.run(event_bus.publish(event_type, data))
    # The control: the leak surface is loaded. A later "nothing leaked" is
    # therefore about the route, not about an empty bus.
    assert len(event_bus.recent_events(limit=500)) == len(_PRIVATE_EVENTS)
    try:
        yield event_bus
    finally:
        event_bus._event_log = saved


# =========================================================================== #
# §1 — THE ROUTE IS GONE                                                       #
# =========================================================================== #
class TestTheGlobalEventRouteIsGone:
    def test_no_route_in_the_app_serves_the_deleted_path(self):
        paths = {r.path for r in server.app.routes if isinstance(r, APIRoute)}
        assert DELETED_PATH not in paths, (
            f"{DELETED_PATH} is registered again. It served the process-global "
            f"EventBus log; do not reintroduce it.")

    @pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
    def test_the_path_answers_nothing_over_the_wire(
            self, client, fake_db, event_log_with_private_payloads, method):
        """404/405 for every verb, with the log full of private payloads."""
        resp = getattr(client, method)(DELETED_PATH)
        assert resp.status_code in (404, 405), (
            f"{method.upper()} {DELETED_PATH} answered {resp.status_code}")
        for secret in _SECRETS:
            assert secret not in resp.text, f"{secret!r} leaked in a {resp.status_code}"

    def test_query_parameters_do_not_resurrect_it(
            self, client, fake_db, event_log_with_private_payloads):
        """The old signature took `event_type` and `limit`; neither is a way back
        in, and no sibling market route answers the path by prefix match."""
        for qs in ("?event_type=trade", "?limit=200", "?event_type=&limit=1"):
            resp = client.get(DELETED_PATH + qs)
            assert resp.status_code == 404, (DELETED_PATH + qs, resp.status_code)
            for secret in _SECRETS:
                assert secret not in resp.text, secret

    def test_the_event_bus_itself_still_works(self, event_log_with_private_payloads):
        """The falsifying twin for §1. Deleting the *bus* would also make every
        assertion above pass, and would silently kill live delivery."""
        events = event_log_with_private_payloads.recent_events(limit=500)
        assert [e["type"] for e in events] == [t for t, _ in _PRIVATE_EVENTS]
        assert events[0]["data"]["user_id"] == _VICTIM


# =========================================================================== #
# §2 — NO PRODUCTION CALL SITE HOLDS THE EVENT PAYLOADS                        #
# =========================================================================== #
#: Files excluded from the sweep: the tests themselves, vendored code, caches.
_SKIP_PARTS = {"venv", "__pycache__", "node_modules", "tests", ".git"}

#: The module that *owns* the buffer. Its own reads are the implementation of
#: the bounded log — append, trim, filter — so flagging them would amount to
#: asking the EventBus not to have an event log. Every read *outside* this file
#: is some other component holding cross-tenant payloads.
#:
#: The exclusion cannot rot silently: it is a path equality, so if the module
#: moves, its new location stops matching and the sweep goes red there.
#: `test_the_owner_module_is_the_one_file_allowed_to_hold_the_log` additionally
#: asserts the exclusion is load-bearing — that this file really does contain
#: reads the matcher would otherwise flag.
_EVENT_BUS_MODULE = Path("services/market_engine/event_bus.py")

#: The private buffer behind `recent_events()`.
_LOG_ATTRIBUTE = "_event_log"

BACKEND = Path(server.__file__).resolve().parent


def _python_sources():
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND)
        if _SKIP_PARTS & set(rel.parts) or rel == _EVENT_BUS_MODULE:
            continue
        yield path


def _event_log_exposures(tree: ast.AST):
    """Line numbers where this source holds the event log's *payloads*.

    Three spellings reach the same list, and a guard that knows only the first
    is a guard a one-line edit walks around — exactly what mutation 4b did:

        event_bus.recent_events(...)          the accessor
        event_bus._event_log                  the buffer, read directly
        getattr(event_bus, "_event_log")      the buffer, spelled dynamically

    Wrapping any of them in `len(...)` yields a number — how many events the
    bus has seen — which carries no tenant data and is already published on the
    engine status surface. Any *other* use has the event dicts in hand, and
    those dicts are what leaked.

    Writes (`event_bus._event_log = []`) are deliberately not flagged: the
    concern is disclosure, and an assignment target discloses nothing. Only
    `ast.Load` accesses — return it, slice it, iterate it, nest it in a
    response body — put payloads in a caller's hands.

    Known limit: a fully dynamic read (`vars(bus)["_event_log"]`) is outside
    what a source matcher can see. §1 and §3 are the behavioural half.
    """
    # A `len(...)` wrapper neutralises whatever it encloses, so record the
    # identity of its argument and skip that node below.
    counted = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "len" and len(node.args) == 1):
            counted.add(id(node.args[0]))

    hits = []
    for node in ast.walk(tree):
        if id(node) in counted:
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "recent_events":
                hits.append(node.lineno)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == _LOG_ATTRIBUTE):
            hits.append(node.lineno)
        elif (isinstance(node, ast.Attribute) and node.attr == _LOG_ATTRIBUTE
                and isinstance(node.ctx, ast.Load)):
            hits.append(node.lineno)
    return sorted(hits)


class TestNoOtherRouteExposesTheEventLog:
    def test_no_backend_module_reads_the_event_payloads(self):
        offenders = []
        for path in _python_sources():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for lineno in _event_log_exposures(tree):
                offenders.append(f"{path.relative_to(BACKEND)}:{lineno}")
        assert not offenders, (
            "these call sites hold the process-global event log's payloads, "
            "which are cross-tenant: " + ", ".join(sorted(offenders)) +
            ". Only `len(...)` — a count — is permitted outside "
            f"{_EVENT_BUS_MODULE}.")

    def test_the_owner_module_is_the_one_file_allowed_to_hold_the_log(self):
        """The exclusion above must be load-bearing, not decorative.

        If `event_bus.py` stopped containing reads the matcher flags, the
        exclusion would be silently excluding nothing — and a later reviewer
        could widen it without anything going red."""
        owner = BACKEND / _EVENT_BUS_MODULE
        assert owner.exists(), (
            f"{_EVENT_BUS_MODULE} moved. Re-point the sweep's exclusion; until "
            "then its new location is swept like any other module.")
        assert _event_log_exposures(ast.parse(owner.read_text(encoding="utf-8"))), (
            f"{_EVENT_BUS_MODULE} no longer reads its own buffer, so excluding "
            "it proves nothing. Drop the exclusion or re-point it.")

    # ---------------- falsifying controls: the matcher can go red ----------- #
    # A source scan that matches nothing is indistinguishable from a scan whose
    # matcher is broken, so each accepted spelling is run past it explicitly.

    def test_the_sweep_would_catch_the_deleted_handler(self):
        """The original exposure: `recent_events()` in a response body."""
        resurrected = ast.parse(
            "async def market_events(event_type=None, limit=50):\n"
            "    events = event_bus.recent_events(event_type=event_type, limit=limit)\n"
            "    return {'events': events, 'count': len(events)}\n")
        assert _event_log_exposures(resurrected) == [2]

    @pytest.mark.parametrize("source, expected", [
        # Mutation 4b: skip the accessor, read the buffer. The `recent_events`
        # matcher alone saw nothing here.
        ("async def market_events(limit: int = 50):\n"
         "    events = event_bus._event_log[-limit:]\n"
         "    return {'events': events, 'count': len(events)}\n", [2]),
        # Returned whole.
        ("def dump():\n"
         "    return event_bus._event_log\n", [2]),
        # Iterated — payloads reach the caller one at a time instead of at once.
        ("def stream():\n"
         "    for event in event_bus._event_log:\n"
         "        yield event['data']\n", [2]),
        # Copied first, exposed later.
        ("def dump():\n"
         "    snapshot = list(event_bus._event_log)\n"
         "    return {'events': snapshot}\n", [2]),
        # Filtered — a narrower leak is still a leak.
        ("def dump(kind):\n"
         "    return [e for e in event_bus._event_log if e['type'] == kind]\n", [2]),
        # Spelled dynamically to dodge an attribute matcher.
        ("def dump():\n"
         "    return getattr(event_bus, '_event_log')\n", [2]),
    ], ids=["sliced", "returned", "iterated", "copied", "filtered", "getattr"])
    def test_the_sweep_catches_a_direct_event_log_read(self, source, expected):
        """The hardening mutation 4b demanded: the buffer is as exposed as the
        accessor, and the matcher must see both."""
        assert _event_log_exposures(ast.parse(source)) == expected

    @pytest.mark.parametrize("source", [
        # The surviving production shape (server.py, gateway.py, resource_probe).
        "status = {'recent_events_count': len(event_bus.recent_events(limit=500))}\n",
        # A count taken off the buffer directly — still only a number.
        "status = {'events_seen': len(event_bus._event_log)}\n",
        # The bounded-buffer trim test, as the EventBus itself writes it.
        "if len(self._event_log) > self._max_log_size:\n    pass\n",
        # A count via the dynamic spelling.
        "n = len(getattr(event_bus, '_event_log'))\n",
        # A write discloses nothing; only reads put payloads in a caller's hands.
        "event_bus._event_log = []\n",
    ], ids=["len_accessor", "len_buffer", "len_trim_check", "len_getattr", "write"])
    def test_a_counted_or_written_use_is_not_flagged(self, source):
        """The other side of the matcher. Without these, a matcher that flags
        everything would also pass every test above, and §2 would be
        unmaintainable rather than strict."""
        assert _event_log_exposures(ast.parse(source)) == []


# =========================================================================== #
# §3 — THE SCOPED ACTIVITY SURFACES SURVIVE, AND DID NOT INHERIT THE LOG       #
# =========================================================================== #
#: The three surfaces D6.1/S4 scoped. They stay readable signed out by design;
#: what they return is scoped to the caller.
SCOPED_ACTIVITY_PATHS = ("/api/market/activity-feed", "/api/ai/activity",
                         "/api/ai-activity")


@pytest.fixture
def clean_activity_log():
    from services import activity_logger
    activity_logger.reset_for_tests()
    yield activity_logger
    activity_logger.reset_for_tests()


class TestTheScopedActivitySurfacesStillHold:
    def test_they_are_still_registered(self):
        paths = {r.path for r in server.app.routes if isinstance(r, APIRoute)}
        missing = [p for p in SCOPED_ACTIVITY_PATHS if p not in paths]
        assert not missing, f"D6.10-P0 removed a scoped surface it must preserve: {missing}"

    @pytest.mark.parametrize("path", SCOPED_ACTIVITY_PATHS)
    def test_they_do_not_serve_the_event_bus_log(
            self, client, fake_db, clean_activity_log, event_log_with_private_payloads,
            path):
        """The migration check. Deleting one exposure is worth nothing if the
        payloads reappear on the endpoint the frontend actually calls."""
        clean_activity_log.log_platform_activity("Scanning News", "news", "done")

        resp = client.get(path)
        assert resp.status_code == 200, (path, resp.status_code)
        assert "Scanning News" in resp.text, f"{path} lost the platform stream"
        for secret in _SECRETS:
            assert secret not in resp.text, f"{secret!r} reached {path}"

    @pytest.mark.parametrize("path", SCOPED_ACTIVITY_PATHS)
    def test_another_users_private_activity_is_still_not_in_my_feed(
            self, client, fake_db, clean_activity_log, other_headers, path):
        """D6.1/S4's property, re-asserted here because D6.10-P0 touched the
        public-route pin these endpoints sit in."""
        clean_activity_log.log_platform_activity("Finding Breakouts", "scan", "done")
        clean_activity_log.log_activity(
            "Order placed on Zerodha: BUY 10 RELIANCE", "monitor", "done",
            user_id=_VICTIM)

        for headers in ({}, other_headers):
            actions = [e["action"] for e in client.get(path, headers=headers).json()]
            assert "Finding Breakouts" in actions, f"{path} lost the platform stream"
            assert "Order placed on Zerodha: BUY 10 RELIANCE" not in actions, path

    @pytest.mark.parametrize("path", SCOPED_ACTIVITY_PATHS)
    def test_the_owner_still_sees_their_own(
            self, client, fake_db, clean_activity_log, test_user, auth_headers, path):
        """The owner-positive control: without it, an endpoint that returns an
        empty list for everyone passes every assertion above."""
        clean_activity_log.log_activity(
            "Order placed on Zerodha: BUY 10 RELIANCE", "monitor", "done",
            user_id=str(test_user["_id"]))

        actions = [e["action"] for e in client.get(path, headers=auth_headers).json()]
        assert "Order placed on Zerodha: BUY 10 RELIANCE" in actions, path

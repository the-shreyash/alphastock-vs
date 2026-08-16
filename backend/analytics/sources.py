"""Authoritative production sources for the metrics PH3.9 removed mocks from.

WHY THIS MODULE EXISTS
----------------------
PH3.8 classified seventeen admin metrics as MOCK and left them in place with a
label. Removing them means answering, for each one, a question the label
deferred: *is there a real source, and does it actually support the number the
dashboard claims to show?* That answer is not "read the collection" — it is a
judgement about what the stored data can and cannot support, and it belongs in
one reviewed place rather than inline in a route handler where the next person
to touch the endpoint will not see it.

Every function here returns an :class:`analytics.contract.Metric`, never a bare
number, because the whole point of the sprint is that "we cannot compute this"
must survive the trip to the frontend as something other than ``0``.

THE RULE THIS MODULE ENFORCES
-----------------------------
    A metric is AVAILABLE only when the stored data can answer the question
    the metric's *name* asks.

Not "when a query returns rows". Those are different, and the difference is
every defect PH3.8 catalogued. Two applications of it dominate this file:

* **Revenue is gated on integration, not on emptiness.** ``db.payments`` has no
  writer anywhere in this codebase. An aggregation over it returns ₹0, and ₹0 is
  a *wrong answer that formats beautifully*. Worse, gating on emptiness means
  the first stray document flips revenue to "available" and reports it as fact —
  the same defect PH3.8 found (``count(payments) × ₹499``) wearing a new
  implementation. So the gate is :func:`payments_integration`, a single named
  predicate about the platform, and the aggregation below it is real and ready.
* **Activity is gated on the retention horizon.** ``db.sessions`` carries a TTL
  index that deletes a session at ``last_used_at + JWT_REFRESH_TTL_SECONDS``
  (seven days by default). A "30-day active users" figure computed over a
  collection that only retains seven days is not a monthly number; it is a
  weekly number with a monthly label. :func:`active_users` refuses the window
  rather than serving it — see the note there.
"""
from __future__ import annotations

import logging
from typing import Optional

from analytics import contract
from analytics.periods import Window, preceding

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Payment integration status                                                    #
# --------------------------------------------------------------------------- #
#: Reason strings, shared by every revenue metric so the explanation an operator
#: reads is identical wherever they meet it.
NO_PAYMENT_INTEGRATION = (
    "The platform has no payment integration: nothing in the codebase writes to "
    "`db.payments`, so no verified payment record exists to sum. This is not "
    "'₹0 of revenue' — it is the absence of a revenue source. Wiring a provider "
    "(webhook → verified payment records with amount, currency, status and "
    "captured_at) is what makes this metric available."
)

#: Payment statuses that count as money actually received. Declared here rather
#: than inline so that a future integration cannot quietly widen it: `created`
#: and `pending` are *intents*, not revenue, and `authorized` is a hold, not a
#: capture. Only a captured payment has moved money.
CAPTURED_STATUSES = ("captured", "paid", "succeeded", "settled")

#: Statuses that reverse revenue. Kept separate from CAPTURED_STATUSES because
#: netting them silently is how a refund disappears from reporting.
REFUNDED_STATUSES = ("refunded", "partially_refunded")

PENDING_STATUSES = ("created", "pending", "authorized", "requires_action")

FAILED_STATUSES = ("failed", "cancelled", "canceled", "expired")


def payments_integration() -> dict:
    """Does this deployment write verified payment records?

    **Currently, no — and that is a fact about the platform, not about the
    data.** This is deliberately one predicate in one place rather than a check
    scattered across the four routes that report money, so that the day a
    provider is wired the change is a single reviewed edit here and every
    revenue metric below becomes available at once, already computed correctly.

    Returns a dict rather than a bool so the *reason* travels with the answer
    into the API response; a consumer that only wants the boolean reads
    ``["integrated"]``.
    """
    return {
        "integrated": False,
        "provider": None,
        "reason": NO_PAYMENT_INTEGRATION,
        # The collection is indexed at startup and read by three admin routes,
        # which is why its emptiness reads as "no revenue" rather than "no
        # source". Naming it here keeps the distinction greppable.
        "collection": "db.payments",
    }


async def _sum_captured(db, window: Optional[Window]) -> dict:
    """Σ verified payment amounts in ``window``, aggregated in the database.

    Real, tested, and currently unreachable in production because
    :func:`payments_integration` gates it. It is written now rather than left as
    a TODO so that the integration sprint inherits a correct aggregation instead
    of writing revenue arithmetic under deadline — and so the tests that prove
    "pending and failed payments are not revenue" exist before any money does.
    """
    match = {"status": {"$in": list(CAPTURED_STATUSES)}}
    if window is not None:
        match.update(window.filter_for("captured_at"))
    rows = await db.payments.aggregate([
        {"$match": match},
        {"$group": {"_id": None, "amount": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    row = rows[0] if rows else {}
    return {"amount": float(row.get("amount") or 0), "count": int(row.get("count") or 0)}


async def revenue(db, window: Optional[Window], *, name: str) -> contract.Metric:
    """Revenue over ``window``, or an explicit UNAVAILABLE.

    ``db`` is accepted and unused on the unavailable path on purpose: the
    signature is the one the integrated implementation needs, so wiring a
    provider does not change a single call site.
    """
    status = payments_integration()
    if not status["integrated"]:
        return contract.unavailable(
            name, note=status["reason"], unit=contract.INR, period=window,
            source=status["collection"])
    total = await _sum_captured(db, window)
    return contract.derived(
        name, round(total["amount"], 2), unit=contract.INR, period=window,
        source="db.payments (captured)",
        note="Gross of refunds; see the refunded_total metric beside it.")


async def payment_state_count(db, statuses, *, name: str,
                              window: Optional[Window] = None) -> contract.Metric:
    """How many payments sit in one of ``statuses``, or UNAVAILABLE.

    The zero this replaces was the clearest example of the defect in §1 of
    ANALYTICS.md: `refunds: 0` was a literal, and it was *contradicted by the
    product itself*, which had an endpoint writing `payment.refunded` audit
    records. Zero and "we do not track this" must not render identically.
    """
    status = payments_integration()
    if not status["integrated"]:
        return contract.unavailable(name, note=status["reason"],
                                    period=window, source=status["collection"])
    match = {"status": {"$in": list(statuses)}}
    if window is not None:
        match.update(window.filter_for("created_at"))
    return contract.derived(name, await db.payments.count_documents(match),
                            period=window, source="db.payments.status")


async def subscription_revenue(db, *, name: str, months: int = 1) -> contract.Metric:
    """MRR (``months=1``) or ARR (``months=12``), or an explicit UNAVAILABLE.

    The mock this replaces multiplied *role counts* by a hardcoded price. Two
    independent reasons it could never be right, both worth keeping in view:
    roles are granted by an admin through ``grant-plan`` with no payment, so
    every comped, internal and beta account was counted as paying; and the price
    was a literal in the route rather than the plan the user actually bought, so
    it could not survive a price change or a discount.

    Recurring revenue needs *subscription* records — plan, price, currency,
    interval, status, current period end — which is a strictly larger
    requirement than payment records: a one-off payment is not recurring
    revenue, and summing captures over a month is not MRR.
    """
    return contract.unavailable(
        name, unit=contract.INR,
        source="(no subscription records)",
        note=("Recurring revenue requires active subscription records (plan, price, "
              "currency, billing interval, status, current_period_end) reconciled "
              "against captured payments. Neither exists: " + NO_PAYMENT_INTEGRATION
              + " Inferring it from role counts × a hardcoded price — the previous "
                "implementation — counted every admin-granted, comped and internal "
                "account as paying."))


# --------------------------------------------------------------------------- #
# User activity                                                                 #
# --------------------------------------------------------------------------- #
def session_retention_seconds() -> int:
    """How far back ``db.sessions`` can actually answer a question.

    A session document is deleted by Mongo's TTL reaper at ``expires_at``, which
    :class:`security.sessions.SessionStore` sets to ``last_used_at + refresh
    TTL`` and slides forward on every rotation. So the collection retains
    activity for one refresh lifetime and no longer — read from configuration
    rather than hardcoded, because an operator who raises
    ``JWT_REFRESH_TTL_SECONDS`` genuinely does extend how far back this can see.
    """
    from security.jwt import refresh_ttl_seconds
    return refresh_ttl_seconds()


async def active_users(db, window: Window, *, name: str) -> contract.Metric:
    """Distinct users with session activity inside ``window``.

    WHY THIS CAN ANSWER "TODAY" BUT REFUSES "THIRTY DAYS"

    ``last_used_at`` is written when a session is created (login) and advanced
    on every refresh-token rotation. Access tokens live 15 minutes, so an active
    user rotates several times an hour: within a single day the field is a
    faithful activity signal, and today's window sits far inside the retention
    horizon.

    Thirty days does not. The TTL index deletes sessions one refresh lifetime
    after last use — seven days by default — so the rows a 30-day query would
    need have been *physically removed by the database*. The query would still
    return a number, and that number would be a 7-day count wearing a 30-day
    label: undercounting by construction, and undercounting in a way that grows
    with how long ago a user churned. PH3.8's inventory prescribed exactly this
    query for MAU; implementing it as prescribed would have replaced a fabricated
    number with a systematically wrong one, which is the specific failure this
    sprint exists to avoid.

    So the window is checked against the horizon and refused when it exceeds it.
    The refusal is self-correcting: raise the refresh TTL past thirty days and
    the same call starts returning a value, because the data really would be
    there.
    """
    horizon = session_retention_seconds()
    if window.bounded and window.start is not None:
        span = (window.end - window.start).total_seconds()
        if span > horizon:
            days = round(span / 86400, 1)
            return contract.unavailable(
                name, period=window, source="db.sessions",
                note=(f"Requires {days} days of activity history; `db.sessions` retains "
                      f"{round(horizon / 86400, 1)} days. The collection has a TTL index "
                      "that deletes a session one refresh lifetime after its last use "
                      "(JWT_REFRESH_TTL_SECONDS), so the older rows this window needs "
                      "have been removed by the database and cannot be counted. "
                      "Computing it anyway would report a truncated count under a "
                      "full-window label. A durable per-user activity record — or a "
                      "retained activity event stream — is what this needs."))

    # Two $group stages rather than pulling ids into the process: the first
    # collapses to one row per distinct user, the second counts those rows. The
    # count crosses the wire, not the users. (This shape is also within the
    # subset the FakeDB test double implements, so it is exercised hermetically.)
    rows = await db.sessions.aggregate([
        {"$match": window.filter_for("last_used_at")},
        {"$group": {"_id": "$user_id"}},
        {"$group": {"_id": None, "n": {"$sum": 1}}},
    ]).to_list(1)
    return contract.derived(
        name, int(rows[0]["n"]) if rows else 0, period=window,
        source="db.sessions.last_used_at",
        note=("Distinct users whose session was created or refreshed in this window. "
              "A signed-in user who made no request in the window is not counted."))


async def signup_growth(db, window: Window, *, name: str) -> contract.Metric:
    """Signup growth this window versus the window immediately before it.

    Replaces the literal ``12.8``. The comparison base comes from
    :func:`analytics.periods.preceding`, so the two halves are guaranteed to
    cover the same span — a 30-day count over a 31-day base would report a
    calendar artefact as a business trend.

    Growth from a zero base is UNAVAILABLE, not ``+100%`` and not ``+∞``: the
    first signup of a platform's life is not "infinite growth", and rendering it
    as a percentage is meaningless in either direction.
    """
    base_window = preceding(window)
    current = await db.users.count_documents(window.filter_for("created_at"))
    previous = await db.users.count_documents(base_window.filter_for("created_at"))
    comparison = {"previous": previous, "current": current,
                  "previous_period": base_window.as_dict()}
    if previous == 0:
        return contract.unavailable(
            name, unit=contract.PERCENT, period=window, source="db.users.created_at",
            note=(f"No signups in the comparison period ({base_window.label}), so a "
                  "percentage change has no base. The absolute count for this period "
                  f"is {current}."),
            comparison=comparison)
    return contract.derived(
        name, round((current - previous) / previous * 100, 1), unit=contract.PERCENT,
        period=window, source="db.users.created_at", comparison=comparison,
        note=f"Signups in {window.label} vs the {base_window.label.lower()}.")


def retention_rate(*, name: str) -> contract.Metric:
    """Cohort retention — UNAVAILABLE, replacing the literal ``78.5``.

    Retention asks: of the users first seen in week N, what fraction were active
    in week N+k? Both halves need activity history older than
    :func:`session_retention_seconds` allows, and the cohort half additionally
    needs a durable "first seen" that survives it. Neither exists, and neither
    can be back-filled: an activity event that was never recorded cannot be
    reconstructed from a user document's ``created_at``.
    """
    return contract.unavailable(
        name, unit=contract.PERCENT, source="(no retained activity history)",
        note=("Cohort retention needs per-user activity history spanning several "
              "weeks. `db.sessions` is reaped by a TTL index one refresh lifetime "
              "after last use, and no other collection records activity, so the "
              "history does not exist and cannot be reconstructed — an event never "
              "written cannot be back-filled. A durable activity or login-event "
              "record is the required source."))


def churn_rate(*, name: str) -> contract.Metric:
    """Subscription churn — UNAVAILABLE, replacing the literal ``4.2``.

    Churn is cancellations over an active-subscription base. Both terms come
    from subscription records this platform does not keep, so it is blocked on
    the same integration as :func:`subscription_revenue` and should be delivered
    in the same change rather than approximated first.
    """
    return contract.unavailable(
        name, unit=contract.PERCENT, source="(no subscription records)",
        note=("Churn is cancellations and expiries over an active-subscription base. "
              "Neither term exists: " + NO_PAYMENT_INTEGRATION))

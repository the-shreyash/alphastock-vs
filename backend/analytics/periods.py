"""Analytics time windows and the one documented timezone strategy (PH3.8).

THE PROBLEM THIS MODULE EXISTS TO SOLVE
---------------------------------------
Before PH3.8 every analytics date boundary in the product was written inline as
one of two idioms:

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ... {"exit_time": {"$regex": f"^{today}"}}          # admin counts
    ... [t for t in trades if t["exit_time"].startswith(today)]   # trade metrics

Both are *UTC* day boundaries. The product is an NSE trading platform: its
market, its users, its currency and its scheduler (`AsyncIOScheduler(timezone=
"Asia/Kolkata")`) are all IST. A UTC day rolls over at **05:30 IST**, so
"today" silently meant "since 05:30 this morning". Continuous intraday metrics
were unaffected — the 09:15–15:30 IST session sits inside a single UTC date —
but every metric with a boundary near midnight was attributed to the wrong day,
and nothing in the codebase said which timezone any window was in.

THE STRATEGY
------------
**Storage is UTC. Boundaries are IST. Nothing is ever computed in server-local
or browser-local time.**

* Timestamps are persisted exactly as they are today: timezone-aware UTC
  ISO-8601 strings (``2026-08-16T10:00:00.123456+00:00``). This module changes
  no storage format and requires no migration.
* Every *window* — today, 7 days, month-to-date — is resolved against
  ``Asia/Kolkata``, the exchange timezone, and then converted back to UTC for
  querying. A "day" is an IST calendar day: 00:00:00 IST to 00:00:00 IST.
* A window is always a **half-open interval** ``[start, end)``. Half-open is
  what makes adjacent windows partition time exactly once: an event at midnight
  belongs to the later day and to no other, so "today" + "yesterday" can never
  double-count and can never drop an instant.

WHY HALF-OPEN RANGES REPLACE THE PREFIX MATCH
---------------------------------------------
``exit_time.startswith("2026-08-16")`` can only ever express a UTC day, because
the prefix it matches *is* the stored UTC date. An IST day is not a prefix of
anything. A range comparison is the only form that can express it — and it is
also the faster form: ``{"exit_time": {"$gte": ..., "$lt": ...}}`` is served by
a B-tree index, while ``$regex`` on a non-anchored-index field is a collection
scan. The lexicographic ordering of same-offset ISO-8601 strings is identical
to chronological ordering, so the comparison is correct on the strings already
stored.

MARKET-SESSION SEMANTICS
------------------------
For trading analytics the unit that matters is not the calendar day but the
**session**: one NSE trading day, 09:15–15:30 IST, Monday to Friday. A calendar
day and a session date coincide for anything that happens during the session
(which is where trade events happen), so :func:`session_date` returns the IST
calendar date and flags whether it was a trading day. Exchange holidays are NOT
modelled — this module has no holiday calendar and does not pretend to; a
metric that needs one must say so rather than silently treating Diwali as a
session. See ``docs/architecture/ANALYTICS.md`` §5.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

#: The exchange timezone. A fixed +05:30 offset rather than a `ZoneInfo` lookup:
#: India has observed no DST since 1945 and has a single timezone, so the offset
#: is a constant, and a fixed offset cannot fail at runtime on a host with no
#: tzdata installed (which `ZoneInfo("Asia/Kolkata")` can, and does, inside
#: slim containers). `services/brokers/base.py` already defines the same
#: constant for broker token expiry; this is the analytics-side authority.
IST = timezone(timedelta(hours=5, minutes=30))

#: NSE regular-session bounds, IST. Pre-open (09:00–09:15) and the post-close
#: auction are deliberately excluded: they are not where trades in this product
#: are executed.
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

#: Every window key this module resolves. Anything else is a programming error,
#: not a user error — the API layer validates its own query parameters against
#: this set and rejects unknown values rather than silently falling back to
#: "all time", which would quietly widen a metric instead of failing it.
PERIODS = (
    "today", "yesterday", "7d", "30d", "90d", "mtd", "prev_month", "ytd", "all",
)

_LABELS = {
    "today": "Today (IST)",
    "yesterday": "Yesterday (IST)",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "90d": "Last 90 days",
    "mtd": "Month to date",
    "prev_month": "Previous month",
    "ytd": "Year to date",
    "all": "All time",
}


class UnknownPeriod(ValueError):
    """A caller asked for a window this module does not define.

    Deliberately loud. The alternative — falling back to "all time" — turns a
    typo in a query parameter into a metric that silently covers the wrong span,
    which is exactly the class of defect PH3.8 exists to remove.
    """


@dataclass(frozen=True)
class Window:
    """A resolved, half-open analytics window ``[start, end)``.

    ``start``/``end`` are timezone-aware UTC datetimes. ``start_iso``/``end_iso``
    are the UTC ISO-8601 strings used to query the string timestamps this
    product persists. ``start`` is ``None`` for the unbounded ``all`` window —
    unbounded is represented honestly rather than as a sentinel date far in the
    past, so a caller that must reject unbounded scans can detect it.
    """

    key: str
    label: str
    start: Optional[datetime]
    end: datetime

    @property
    def start_iso(self) -> Optional[str]:
        return self.start.isoformat() if self.start else None

    @property
    def end_iso(self) -> str:
        return self.end.isoformat()

    @property
    def bounded(self) -> bool:
        return self.start is not None

    def mongo_range(self) -> dict:
        """The Mongo condition for a UTC-ISO-string timestamp field.

        Returns ``{}`` for the unbounded window so it can be spread into a
        filter unconditionally: ``{"user_id": uid, **{"exit_time": w.mongo_range()}}``
        would add an empty condition, so callers use :meth:`filter_for` instead.
        """
        if not self.bounded:
            return {}
        return {"$gte": self.start_iso, "$lt": self.end_iso}

    def filter_for(self, field: str) -> dict:
        """A complete (possibly empty) filter fragment for ``field``."""
        rng = self.mongo_range()
        return {field: rng} if rng else {}

    def contains(self, moment) -> bool:
        """Is ``moment`` inside this window?

        Accepts a UTC ISO-8601 string (what the database stores) or an aware
        datetime. ``None``, an empty string and an unparseable value are all
        **outside** every window — an event with no usable timestamp is never
        silently attributed to the current period.
        """
        stamp = to_datetime(moment)
        if stamp is None:
            return False
        # `self.start is not None` rather than `self.bounded`: the property is
        # the same test, but a type checker cannot narrow Optional through it.
        if self.start is not None and stamp < self.start:
            return False
        return stamp < self.end

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label,
                "start": self.start_iso, "end": self.end_iso, "timezone": "Asia/Kolkata"}


def to_datetime(value) -> Optional[datetime]:
    """Parse a stored timestamp into an aware UTC datetime, or ``None``.

    Returns ``None`` — never raises, never guesses — for ``None``, a
    non-string/non-datetime, an unparseable string, or a naive datetime that
    cannot be placed on the timeline. Naive strings are the one exception: they
    are assumed UTC, because every naive timestamp this codebase writes came
    from `datetime.utcnow()` on a UTC-storing path. That assumption is recorded
    here rather than at forty call sites.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        # `fromisoformat` on Python 3.9/3.10 does not accept a trailing 'Z'.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_ist(now: Optional[datetime] = None) -> datetime:
    return (now or now_utc()).astimezone(IST)


def ist_date(moment=None) -> Optional[date]:
    """The IST calendar date an instant falls on."""
    if moment is None:
        return now_ist().date()
    stamp = to_datetime(moment)
    return stamp.astimezone(IST).date() if stamp else None


def _ist_midnight_utc(day: date) -> datetime:
    """00:00:00 IST on ``day``, expressed in UTC."""
    return datetime.combine(day, time(0, 0), tzinfo=IST).astimezone(timezone.utc)


def resolve(period: str, now: Optional[datetime] = None) -> Window:
    """Resolve a period key into a half-open UTC window with IST boundaries.

    ``now`` is injectable so every window is testable at an exact instant
    (including the 05:30 IST / 00:00 UTC boundary where the pre-PH3.8 UTC-day
    idiom and the IST day disagree) without freezing the process clock.
    """
    key = (period or "").strip().lower()
    if key not in PERIODS:
        raise UnknownPeriod(
            f"Unknown analytics period {period!r}. Valid periods: {', '.join(PERIODS)}.")

    reference = now or now_utc()
    today = reference.astimezone(IST).date()
    label = _LABELS[key]
    # `end` is the start of tomorrow (IST) for every window that includes the
    # present, so an event happening *right now* is inside "today". Using
    # `reference` itself as the end would exclude anything written in the same
    # millisecond and make the window non-reproducible.
    tomorrow = _ist_midnight_utc(today + timedelta(days=1))

    if key == "today":
        return Window(key, label, _ist_midnight_utc(today), tomorrow)
    if key == "yesterday":
        return Window(key, label, _ist_midnight_utc(today - timedelta(days=1)),
                      _ist_midnight_utc(today))
    if key in ("7d", "30d", "90d"):
        # "Last 7 days" is 7 whole IST days ENDING WITH TODAY — today plus the
        # six before it — not "the 168 hours before this instant". A trader
        # comparing a 7-day figure across two page loads an hour apart expects
        # the same number; a rolling-instant window silently changes it.
        days = int(key.rstrip("d"))
        return Window(key, label, _ist_midnight_utc(today - timedelta(days=days - 1)), tomorrow)
    if key == "mtd":
        return Window(key, label, _ist_midnight_utc(today.replace(day=1)), tomorrow)
    if key == "prev_month":
        this_month = today.replace(day=1)
        prev_month = (this_month - timedelta(days=1)).replace(day=1)
        return Window(key, label, _ist_midnight_utc(prev_month), _ist_midnight_utc(this_month))
    if key == "ytd":
        return Window(key, label, _ist_midnight_utc(today.replace(month=1, day=1)), tomorrow)
    return Window(key, label, None, tomorrow)  # "all"


def window_of_days(days: int, now: Optional[datetime] = None) -> Window:
    """An arbitrary N-whole-IST-day window ending with today.

    The caller-supplied counterpart to :func:`resolve`, for the endpoints that
    accept a free-form ``?days=`` parameter rather than one of the fixed period
    keys. Same semantics as ``7d``/``30d``: whole IST days, half-open, ending
    at tomorrow's IST midnight so events happening right now are inside it.

    ``days`` is clamped to at least 1 — ``?days=0`` previously produced a
    zero-width window that returned an empty result indistinguishable from "you
    have no trades".
    """
    reference = now or now_utc()
    today = reference.astimezone(IST).date()
    span = max(int(days), 1)
    return Window(f"{span}d", f"Last {span} days",
                  _ist_midnight_utc(today - timedelta(days=span - 1)),
                  _ist_midnight_utc(today + timedelta(days=1)))


def preceding(window: Window) -> Window:
    """The window of identical span immediately *before* ``window`` (PH3.9).

    Period-over-period growth needs a comparison base, and computing one by hand
    at the call site is how the two halves of a growth figure end up covering
    different spans — a 30-day numerator over a 31-day denominator reads as
    −3% of "churn" that is really a calendar artefact. Deriving the base from
    the window guarantees the two are the same length and share a boundary:
    ``preceding(w).end == w.start`` exactly, so the pair partitions time with no
    gap and no overlap (the same half-open property §5.2 relies on).

    Raises for the unbounded ``all`` window: "the 30 days before all of time"
    is not a question with an answer, and returning something anyway is how a
    growth rate ends up dividing by a window that does not exist.
    """
    if not window.bounded or window.start is None:
        raise UnknownPeriod(
            "An unbounded window has no preceding window. Growth over 'all time' "
            "is not defined; ask for a bounded period.")
    span = window.end - window.start
    return Window(f"prev_{window.key}", f"Previous {window.label.lower()}",
                  window.start - span, window.start)


def session_date(moment=None) -> dict:
    """The NSE session an instant belongs to, IST.

    Returns ``{"date", "is_trading_day", "in_session"}``. ``is_trading_day`` is
    a weekday check ONLY — **exchange holidays are not modelled here** and a
    metric that depends on them must not use this field as if they were. See
    the module docstring.
    """
    stamp = to_datetime(moment) if moment is not None else now_utc()
    if stamp is None:
        return {"date": None, "is_trading_day": False, "in_session": False}
    local = stamp.astimezone(IST)
    weekday = local.weekday() < 5
    return {
        "date": local.date().isoformat(),
        "is_trading_day": weekday,
        "in_session": weekday and MARKET_OPEN <= local.time() <= MARKET_CLOSE,
    }

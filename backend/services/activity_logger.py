"""AI activity feed — scoped, with private entries delivered only to their owner.

D6.1 / S4. This module used to hold ONE process-global 50-entry deque, served by
two unauthenticated REST endpoints and pushed to **every** socket by
`ws_manager.broadcast()`. What went into it was not platform telemetry:

    "Order placed on Zerodha: BUY 10 RELIANCE"     (broker_engine)
    "Teaching concept: <the user's own question>"  (POST /api/ai/learn)
    "Zerodha portfolio synced — 12 holdings, 3 positions"
    "Running backtest: <strategy> on <symbol>"

User A's live trade flow was therefore visible to User B in real time, and to
anyone on the internet by polling one URL.

TWO SCOPES, AND THE API MAKES YOU PICK
--------------------------------------
* **platform** — the heartbeat engine's market-wide work ("Scanning News",
  "Nifty +0.4%", "Finding Breakouts"). About the market, owned by nobody, safe
  for every viewer. Logged with :func:`log_platform_activity`.
* **private** — anything about one account: their orders, their portfolio, their
  AI questions, their backtests, their broker connections. Logged with
  :func:`log_activity`, whose ``user_id`` is a **required keyword argument**.

The enforcement is the signature, not a convention. ``log_activity("...", "x")``
raises ``TypeError`` at the call site — a caller cannot forget to say whose
event this is, because there is no way to call it without saying. The D6.0
audit's standing complaint about the event bridge ("one omitted keyword argument
away from recurring, and nothing would fail") does not apply here: omitting it
fails immediately and loudly. `tests/test_d61_security.py` additionally sweeps
the source for call sites that reach for the platform variant from a module that
handles per-user work.

DELIVERY
--------
Registered callbacks receive ``(entry, user_id)``. ``user_id is None`` means
platform scope — broadcast. Anything else is delivered to that user's sockets
only (`server.ws_activity_broadcast`).

READS
-----
:func:`get_recent_activity` takes the reader's identity. An anonymous reader
sees platform entries alone; an authenticated reader sees the platform stream
merged with **their own** private entries, newest first. There is no argument
that returns another user's entries and no code path that reaches them.

BOUNDS
------
The platform deque keeps 50 entries as before. Private entries are kept per user
in a bounded LRU of at most ``MAX_TRACKED_USERS`` deques of ``MAX_USER_ENTRIES``
each, so a large user base cannot grow this module without limit — the same
retention discipline PH3.6 applied to the socket manager's maps.
"""
import collections
import itertools
import logging
import asyncio
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: Platform-scope entries (market-wide AI work). Broadcast; readable by anyone.
MAX_PLATFORM_ENTRIES = 50
#: Private entries retained per user, and how many users are tracked at once.
MAX_USER_ENTRIES = 25
MAX_TRACKED_USERS = 500
#: Entries returned by a single read.
READ_LIMIT = 20

# In-memory deque holding the last 50 platform activities.
activity_deque = collections.deque(maxlen=MAX_PLATFORM_ENTRIES)

#: Per-user private entries. An OrderedDict used as an LRU: the least recently
#: written user is evicted once MAX_TRACKED_USERS is exceeded. Eviction costs a
#: user their in-memory feed history, never anyone else's privacy.
_user_activity: "collections.OrderedDict[str, collections.deque]" = collections.OrderedDict()

_broadcast_callbacks = []

#: Monotonic write counter used to order the two scopes against each other.
#:
#: The client-facing `time` field is an "HH:MM:SS" wall-clock string, which is
#: not an ordering key: it wraps at midnight, so merging on it would put the
#: 23:59 entries above the 00:01 ones for the first minutes of every day. The
#: sequence is stripped before an entry leaves this module — it is a sort key,
#: not part of the feed contract.
_sequence = itertools.count()
_SEQ = "_seq"


def register_broadcast_callback(cb: Callable):
    """Register a callback invoked as ``cb(entry, user_id)`` on each new entry.

    ``user_id`` is ``None`` for platform scope and a user id string for a
    private entry. The callback is responsible for routing; this module is
    responsible for never handing it a private entry without an owner.
    """
    _broadcast_callbacks.append(cb)


def _entry(action: str, category: str, status: str) -> dict:
    return {
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "action": action,
        "category": category,
        "status": status,
        _SEQ: next(_sequence),
    }


def _public(entry: dict) -> dict:
    """An entry as a reader sees it — without this module's internal sort key."""
    return {k: v for k, v in entry.items() if k != _SEQ}


def _dispatch(entry: dict, user_id: Optional[str]) -> None:
    for cb in _broadcast_callbacks:
        try:
            if asyncio.iscoroutinefunction(cb):
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(cb(entry, user_id))
                else:
                    asyncio.run(cb(entry, user_id))
            else:
                cb(entry, user_id)
        except Exception as e:
            logger.error(f"Activity broadcast callback error: {e}")


def _user_deque(user_id: str) -> collections.deque:
    dq = _user_activity.get(user_id)
    if dq is None:
        dq = collections.deque(maxlen=MAX_USER_ENTRIES)
        _user_activity[user_id] = dq
        while len(_user_activity) > MAX_TRACKED_USERS:
            _user_activity.popitem(last=False)
    else:
        _user_activity.move_to_end(user_id)
    return dq


def log_activity(action: str, category: str, status: str = "done", *, user_id):
    """Log a PRIVATE activity owned by ``user_id``.

    ``user_id`` is keyword-only and has no default: a call site that does not
    know whose activity this is cannot use this function, and will not silently
    publish an account's business to every connected socket (D6.1 / S4).

    Categories: 'scan', 'news', 'alert', 'rank', 'monitor'.
    Statuses: 'running', 'done', 'warning'.
    """
    if not user_id:
        # Fail closed rather than degrade to a broadcast. A caller that reached
        # this function has private content by construction; the correct
        # response to "I do not know the owner" is to drop the entry, not to
        # tell everyone. Logged at warning so it is visible rather than silent.
        logger.warning("Dropped a private activity entry with no owner: %r", action)
        return
    entry = _entry(action, category, status)
    _user_deque(str(user_id)).append(entry)
    _dispatch(_public(entry), str(user_id))


def log_platform_activity(action: str, category: str, status: str = "done"):
    """Log a PLATFORM-SCOPE activity — market-wide AI work owned by nobody.

    Only modules whose work is genuinely market-wide may call this: the
    heartbeat engine, the market data layer, and the scheduler's aggregate
    passes. It is imported under the alias ``log_activity`` in those modules so
    their ~60 existing call sites read unchanged, and
    ``tests/test_d61_security.py`` asserts the importer list stays closed.
    """
    entry = _entry(action, category, status)
    activity_deque.append(entry)
    _dispatch(_public(entry), None)


def get_recent_activity(user_id: Optional[str] = None):
    """The reader's feed: platform entries, merged with their own private ones.

    ``user_id=None`` (anonymous, or a caller with no identity) returns the
    platform stream alone. There is deliberately no parameter that selects
    another user's entries.

    Newest first, capped at :data:`READ_LIMIT`, ordered by the module's write
    sequence rather than by the ``time`` string — see ``_sequence``.
    """
    platform = list(activity_deque)
    private = list(_user_activity.get(str(user_id), ())) if user_id else []
    merged = sorted(platform + private, key=lambda e: e.get(_SEQ, 0), reverse=True)
    return [_public(e) for e in merged[:READ_LIMIT]]


def reset_for_tests() -> None:
    """Forget every entry in both scopes (test isolation)."""
    activity_deque.clear()
    _user_activity.clear()


# NOTE: The feed intentionally starts EMPTY. It is filled within seconds of
# startup by the AI heartbeat engine (services/heartbeat_engine.py), which logs
# a truthful running -> done/warning trace of real background work (live market
# fetches, news scans, trade monitoring). No fake pre-population.

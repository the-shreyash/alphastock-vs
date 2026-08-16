"""Error classification (PH3.7).

WHY THIS MODULE EXISTS
----------------------
At 03:00 the question is never "what was the error message?". It is "which
*kind* of thing broke?" — because that is what decides who gets woken up. A
provider timeout, an expired credential, a Mongo failover and a validation
rejection all surface as red on a dashboard and as a stack trace in the log, and
telling them apart currently requires reading the message. Messages are free
text: they get reworded, they get truncated, they embed IDs, and no two
subsystems phrase the same failure the same way. Grouping incidents by message
means grouping by a string nobody controls.

So this module defines a **small, closed vocabulary** of failure classes and one
function that maps any exception onto it. Two properties make it useful:

1. **It is bounded.** Twelve values, fixed at import. That is what makes a class
   safe to use as a metric label — the thing an arbitrary error message can
   never be (see the cardinality discussion in `observability.metrics`; an
   unbounded label is how a metrics change takes down the system it observes).
2. **It is stable.** The class of a failure does not change when someone
   improves the wording of an exception, so an alert rule and a dashboard panel
   written today still mean the same thing in six months.

WHY NAME MATCHING AND NOT isinstance
------------------------------------
The obvious implementation is a chain of `isinstance(exc, pymongo.errors...)`.
That would make this module import `pymongo`, `redis`, `httpx` and `anthropic` —
turning the one module every subsystem depends on into the module with the most
dependencies, and making a classification call fail at import time in any
environment missing an optional client library. Worse, it inverts the layering:
`observability` would depend on the infrastructure it is supposed to be able to
describe.

Instead the exception's class hierarchy is walked and matched by
``module.QualName``. It is a string comparison against a fixed table, it needs
no imports, it cannot fail, and it degrades to :data:`INTERNAL` for anything
unrecognised — which is the correct answer for "an exception this application
has never classified before".

WHY THE ORDER OF CHECKS MATTERS
-------------------------------
Exception hierarchies overlap. ``pymongo.errors.ServerSelectionTimeoutError`` is
both a Mongo error and a timeout; ``httpx.ConnectTimeout`` is both a provider
error and a timeout. There is no universally right answer, so this module picks
one and states it: **the subsystem wins over the failure mode.** "MongoDB is
unreachable" routes to the database owner; "something timed out somewhere" routes
to nobody. :data:`TIMEOUT` is therefore reserved for timeouts with no subsystem
attached (a bare ``asyncio.TimeoutError`` from ``wait_for``), and per-subsystem
timeouts stay with their subsystem. Callers that need the distinction have
:func:`is_timeout`.
"""
from __future__ import annotations

from typing import Optional

# --------------------------------------------------------------------------- #
# The vocabulary                                                                #
#                                                                               #
# Every value is a lowercase snake_case token safe to use verbatim as a metric   #
# label value and as a structured-log field. Adding one is a deliberate act:     #
# it widens the label space of every metric that carries an `error_class`, and   #
# it invalidates any alert rule written as an exhaustive match. Prefer reusing   #
# an existing class over minting a near-synonym.                                 #
# --------------------------------------------------------------------------- #

#: The caller sent something malformed. Never an alert — a spike is a client
#: bug or an API change, not an outage, and paging on it trains people to
#: ignore the pager.
VALIDATION = "validation"

#: The caller could not be identified: bad credentials, invalid/expired JWT,
#: failed refresh, replayed token. A *spike* is alert-worthy (credential
#: stuffing); a steady background rate is normal internet.
AUTHENTICATION = "authentication"

#: The caller was identified and is not allowed. Distinguished from
#: AUTHENTICATION because the response is different: authentication failures at
#: volume mean an attack on the front door, authorization failures at volume
#: mean either a broken client or someone enumerating a boundary.
AUTHORIZATION = "authorization"

#: A rate limit or quota refused the request — ours (429 out) or a provider's
#: (429 in). Both are capacity signals rather than faults.
RATE_LIMIT = "rate_limit"

#: MongoDB: unreachable, failing over, rejecting writes, timing out.
DATABASE = "database"

#: Redis: connection refused, command error, circuit open. Separate from
#: DATABASE because in this application Redis is *not* critical — the cache
#: falls back in-process — so the two classes carry different severities.
CACHE = "cache"

#: A third-party HTTP dependency that is not an AI model: market data, brokers,
#: news, email/WhatsApp/Telegram delivery. The class an on-call engineer can do
#: nothing about except degrade gracefully.
EXTERNAL_PROVIDER = "external_provider"

#: An AI model provider specifically. Split from EXTERNAL_PROVIDER because the
#: failure modes, the cost of a retry and the user-visible degradation are all
#: different, and because AI spend makes its error rate a business metric as
#: well as an operational one.
AI_PROVIDER = "ai_provider"

#: Something exceeded its deadline with no subsystem attached. See the module
#: docstring on why per-subsystem timeouts do NOT land here.
TIMEOUT = "timeout"

#: Required configuration is missing, malformed, or contradictory. Almost always
#: a deploy-time fault, which makes it the one class where "it started at the
#: exact moment of the release" is the whole diagnosis.
CONFIGURATION = "configuration"

#: A dependency was deliberately not called: unconfigured, or a circuit breaker
#: is open. NOT a failure of that call — recording it as one would make a
#: degraded-but-serving instance look like a broken one.
UNAVAILABLE = "unavailable"

#: Our own bug. The default, and the class that should be smallest.
INTERNAL = "internal"

#: Cooperative cancellation (shutdown, client disconnect). Present so that
#: instrumentation can recognise it and *not* count it as an error — a clean
#: shutdown cancelling twelve in-flight operations must not read as an incident.
CANCELLED = "cancelled"

#: Every legal value. Used by the tests to prove no call site invents a
#: thirteenth class, which is how a bounded label quietly becomes unbounded.
ERROR_CLASSES = frozenset({
    VALIDATION,
    AUTHENTICATION,
    AUTHORIZATION,
    RATE_LIMIT,
    DATABASE,
    CACHE,
    EXTERNAL_PROVIDER,
    AI_PROVIDER,
    TIMEOUT,
    CONFIGURATION,
    UNAVAILABLE,
    INTERNAL,
    CANCELLED,
})


class ConfigurationError(Exception):
    """Required configuration is missing or invalid.

    Defined here rather than in `security.secrets` so that raising it does not
    drag a security module into an import graph, and so the classifier can
    recognise it without a special case. Raise it at the point configuration is
    *read and found wanting*, not at the point the resulting failure surfaces —
    the whole value of the CONFIGURATION class is that it points at the deploy.
    """


# --------------------------------------------------------------------------- #
# The mapping table                                                             #
#                                                                               #
# Keyed by `module.QualName`. Matching walks the exception's MRO, so listing a   #
# base class (`pymongo.errors.PyMongoError`) covers every subclass, and a more   #
# specific entry earlier in the MRO wins naturally without any ordering logic    #
# here. Entries for libraries this deployment may not have installed are simply  #
# never matched.                                                                 #
# --------------------------------------------------------------------------- #
_EXCEPTION_CLASSES: dict[str, str] = {
    # -- cancellation (checked first via MRO position; see note below) -------- #
    "asyncio.exceptions.CancelledError": CANCELLED,
    "concurrent.futures._base.CancelledError": CANCELLED,

    # -- configuration -------------------------------------------------------- #
    "observability.errors.ConfigurationError": CONFIGURATION,

    # -- MongoDB -------------------------------------------------------------- #
    # PyMongoError is the root of the whole pymongo hierarchy, so this one entry
    # classifies every Mongo failure including the timeout subclasses — which is
    # the "subsystem wins over failure mode" rule from the module docstring.
    "pymongo.errors.PyMongoError": DATABASE,
    "bson.errors.BSONError": DATABASE,
    "bson.errors.InvalidId": VALIDATION,  # a malformed ObjectId is caller input

    # -- Redis ---------------------------------------------------------------- #
    "redis.exceptions.RedisError": CACHE,
    "redis.exceptions.ConnectionError": CACHE,

    # -- Outbound HTTP (market data, brokers, news, notifications) ------------ #
    "httpx.HTTPError": EXTERNAL_PROVIDER,
    "httpx.InvalidURL": CONFIGURATION,
    "aiohttp.client_exceptions.ClientError": EXTERNAL_PROVIDER,
    "urllib.error.URLError": EXTERNAL_PROVIDER,
    "requests.exceptions.RequestException": EXTERNAL_PROVIDER,

    # -- AI providers --------------------------------------------------------- #
    "anthropic.APIError": AI_PROVIDER,
    "anthropic.AnthropicError": AI_PROVIDER,
    "anthropic.APIStatusError": AI_PROVIDER,
    "anthropic.RateLimitError": RATE_LIMIT,
    "anthropic.AuthenticationError": CONFIGURATION,  # a bad key is a deploy fault
    "openai.OpenAIError": AI_PROVIDER,
    "openai.RateLimitError": RATE_LIMIT,
    "google.api_core.exceptions.GoogleAPIError": AI_PROVIDER,
    "google.api_core.exceptions.ResourceExhausted": RATE_LIMIT,
    "google.api_core.exceptions.Unauthenticated": CONFIGURATION,

    # -- validation ----------------------------------------------------------- #
    "pydantic_core._pydantic_core.ValidationError": VALIDATION,
    "pydantic.error_wrappers.ValidationError": VALIDATION,
    "json.decoder.JSONDecodeError": VALIDATION,

    # -- bare timeouts (no subsystem) ----------------------------------------- #
    "asyncio.exceptions.TimeoutError": TIMEOUT,
    "builtins.TimeoutError": TIMEOUT,
    "socket.timeout": TIMEOUT,
}

# `KeyError` on an environment variable and `ValueError` on a parsed setting are
# both extremely common and mean nothing on their own, so neither is mapped.
# Configuration faults must raise ConfigurationError to be classified as such —
# guessing from the type would misclassify half the application's ValueErrors.


def _qualified_names(exc_type: type) -> list[str]:
    """`module.QualName` for the exception type and each of its bases, in MRO order."""
    names = []
    for klass in exc_type.__mro__:
        module = getattr(klass, "__module__", "") or ""
        qualname = getattr(klass, "__qualname__", None) or getattr(klass, "__name__", "")
        names.append(f"{module}.{qualname}" if module else qualname)
    return names


def classify_exception(exc: BaseException, *, default: str = INTERNAL) -> str:
    """The failure class of ``exc``.

    Walks the MRO and returns the first mapped class, so the most specific
    registered ancestor wins. Unrecognised exceptions get ``default``.

    Never raises. This is called from ``except`` blocks and from ``finally``
    blocks on the request path; a classifier that could itself throw would turn
    a handled error into an unhandled one, which is the single worst thing an
    observability helper can do.
    """
    try:
        # CancelledError inherits from BaseException, not Exception, in 3.8+.
        # Checked explicitly rather than relying on the table because a bare
        # `except Exception` never sees it and a caller that *does* catch it is
        # almost always a shutdown path being asked to report a non-incident.
        import asyncio

        if isinstance(exc, asyncio.CancelledError):
            return CANCELLED
        for name in _qualified_names(type(exc)):
            mapped = _EXCEPTION_CLASSES.get(name)
            if mapped is not None:
                return mapped
    except Exception:  # pragma: no cover - defensive; see docstring
        pass
    return default


def classify_status(status_code: int) -> Optional[str]:
    """The failure class implied by an HTTP status code, or None for a success.

    Used for *inbound* requests (what we returned to a caller) and for
    *outbound* ones (what a provider returned to us) — the mapping is the same
    in both directions, which is why it does not live in either middleware.

    5xx maps to :data:`INTERNAL` rather than to a subsystem: a 500 is by
    definition a failure we did not classify at the point it happened, and
    inferring "it was probably the database" from a status code would put a
    guess into a field whose entire purpose is to not be a guess.
    """
    if status_code < 400:
        return None
    if status_code == 401:
        return AUTHENTICATION
    if status_code == 403:
        return AUTHORIZATION
    if status_code == 408:
        return TIMEOUT
    if status_code == 429:
        return RATE_LIMIT
    if status_code == 503:
        return UNAVAILABLE
    if status_code == 504:
        return TIMEOUT
    if status_code < 500:
        return VALIDATION
    return INTERNAL


def is_timeout(exc: BaseException) -> bool:
    """True when ``exc`` is a deadline expiry, whatever subsystem raised it.

    The escape hatch for the "subsystem wins" rule: a `ServerSelectionTimeout`
    classifies as :data:`DATABASE` (correct for routing the page) while still
    answering True here (correct for deciding whether to retry).
    """
    try:
        import asyncio

        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return True
        return any(
            "timeout" in name.rsplit(".", 1)[-1].lower()
            for name in _qualified_names(type(exc))
        )
    except Exception:  # pragma: no cover - defensive
        return False


def is_error_class(value: str) -> bool:
    """True when ``value`` is part of the closed vocabulary.

    Instrumentation uses this to refuse an unknown class rather than emit it,
    so a typo at a call site cannot silently widen a metric's label space.
    """
    return value in ERROR_CLASSES

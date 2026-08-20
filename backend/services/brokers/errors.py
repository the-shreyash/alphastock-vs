"""Broker error normalization — one error vocabulary for every broker.

BROKER_INTEGRATION.md requires that every broker error carry four things: a user
message, a developer log, a retry strategy and a recovery suggestion. Before D3
the first two existed (:attr:`BrokerError.user_message` and the exception text)
and the last two did not, so every call site invented its own answer to "should
I retry this?" — or, far more often, did not ask.

WHY A CODE ENUM RATHER THAN STRING CODES
----------------------------------------
The codes themselves predate D3: `"BROKER_ERROR"`, `"BROKER_AUTH"`,
`"BROKER_REJECTED"` and `"RATE_LIMIT"` are already on the wire, already mapped to
HTTP statuses in `server.py`, and already read by the frontend. The enum adopts
those exact strings rather than tidier ones, because renaming them would be a
silent breaking change to a public contract for a cosmetic gain. What the enum
adds is that the set is now closed and enumerable: retry policy and HTTP status
can be derived from the code instead of restated at each handler, and a new code
cannot be introduced by a typo in a string literal.

WHY `normalize_broker_error` EXISTS
------------------------------------
An adapter speaks HTTP, JSON, binary frames and a vendor SDK's error shapes. Any
of those can raise something that is not a :class:`BrokerError` — an
`httpx.HTTPError`, a `KeyError` on an unexpected payload, a `struct.error` on a
malformed frame. If that reaches a route handler it becomes a 500 with a stack
trace, and if it reaches a user it becomes a broker's internal wording on a
StockAssist surface. `normalize_broker_error` is the Broker Gateway's guarantee
that exactly one exception family crosses the boundary, with a message written
for a person rather than for a log.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class BrokerErrorCode(str, Enum):
    """Canonical broker error codes.

    Values are the strings already on the public contract — do not "improve"
    them without a contract change.
    """

    #: Session missing, expired or rejected. The user must reconnect.
    AUTH = "BROKER_AUTH"
    #: The broker understood the request and refused it (insufficient funds,
    #: invalid quantity, order outside the market window, product not allowed).
    REJECTED = "BROKER_REJECTED"
    #: Broker API rate limit reached.
    RATE_LIMIT = "RATE_LIMIT"
    #: The broker did not answer in time.
    TIMEOUT = "BROKER_TIMEOUT"
    #: Could not reach the broker at all.
    NETWORK = "BROKER_NETWORK"
    #: This broker does not offer the requested capability. A permanent, honest
    #: "no" — never a transient failure, never retryable.
    UNSUPPORTED = "BROKER_UNSUPPORTED"
    #: The broker's API credentials are not configured on this deployment.
    NOT_CONFIGURED = "BROKER_NOT_CONFIGURED"
    #: No such broker is registered.
    UNKNOWN_BROKER = "BROKER_UNKNOWN"
    #: The caller's request was invalid before it reached the broker.
    INVALID_REQUEST = "BROKER_INVALID_REQUEST"
    #: The broker answered with something the canonical contract cannot
    #: represent. A defect in the adapter or a change at the broker — either
    #: way, not the user's fault and not retryable.
    CONTRACT = "BROKER_CONTRACT"
    #: Anything else.
    ERROR = "BROKER_ERROR"


#: Codes worth retrying, and what the platform should do about them.
#: BROKER_INTEGRATION.md's Retry Policy in machine-readable form: transient
#: transport problems and rate limits are retryable; a refusal, a dead session,
#: a missing capability and a contract breach are not, and retrying them just
#: burns the broker's rate limit on a request that will fail identically.
_RETRYABLE = frozenset(
    {
        BrokerErrorCode.TIMEOUT,
        BrokerErrorCode.NETWORK,
        BrokerErrorCode.RATE_LIMIT,
    }
)

#: What the user can do about each code. Surfaced alongside `user_message` so a
#: UI can render an action, not just a sentence.
_RECOVERY = {
    BrokerErrorCode.AUTH: "reconnect_broker",
    BrokerErrorCode.REJECTED: "review_order",
    BrokerErrorCode.RATE_LIMIT: "wait_and_retry",
    BrokerErrorCode.TIMEOUT: "retry",
    BrokerErrorCode.NETWORK: "retry",
    BrokerErrorCode.UNSUPPORTED: "use_supported_broker",
    BrokerErrorCode.NOT_CONFIGURED: "contact_support",
    BrokerErrorCode.UNKNOWN_BROKER: "choose_supported_broker",
    BrokerErrorCode.INVALID_REQUEST: "correct_request",
    BrokerErrorCode.CONTRACT: "contact_support",
    BrokerErrorCode.ERROR: "retry",
}


class BrokerError(Exception):
    """Base error for broker operations.

    `user_message` is the only field safe to render to a user: it never contains
    a stack trace, a URL, a token, or a broker's internal error type. The
    exception text itself carries the developer detail and goes to logs.

    `code`, `retryable` and `recovery` are D3 additions with defaults derived
    from the code, so every `raise BrokerError(...)` already in the codebase —
    all of which pass a message and optionally `user_message`/`code` — keeps
    working unchanged and gains the retry/recovery metadata for free.
    """

    def __init__(
        self,
        message: str,
        user_message: str = None,
        code: str = BrokerErrorCode.ERROR.value,
        *,
        broker: Optional[str] = None,
        operation: Optional[str] = None,
        retryable: Optional[bool] = None,
        recovery: Optional[str] = None,
    ):
        super().__init__(message)
        self.user_message = user_message or message
        self.code = code.value if isinstance(code, BrokerErrorCode) else code
        self.broker = broker
        self.operation = operation
        resolved = _coerce_code(self.code)
        self.retryable = _RETRYABLE.__contains__(resolved) if retryable is None else retryable
        self.recovery = recovery or _RECOVERY.get(resolved, "retry")

    def as_dict(self) -> dict:
        """Consumer-safe error payload.

        Deliberately omits `str(self)`: the developer message can contain the
        broker's own wording, a request path or a vendor error type, none of
        which belong on a response body.
        """
        return {
            "code": self.code,
            "message": self.user_message,
            "retryable": self.retryable,
            "recovery": self.recovery,
            "broker": self.broker,
        }


class BrokerAuthError(BrokerError):
    """Session missing/expired — the user must reconnect the broker.

    Kept as its own class (rather than a code on `BrokerError`) because
    `server.py` registers a dedicated exception handler for it that answers 409
    instead of 502: a dead broker session is a state the user can fix, not a
    platform failure. It must also never count against broker *health* — one
    user's expired token says nothing about whether the broker's API is up.
    """

    def __init__(self, message: str = "Broker session expired. Please reconnect.", **kwargs):
        kwargs.setdefault("user_message", message)
        kwargs.pop("code", None)
        super().__init__(message, code=BrokerErrorCode.AUTH.value, **kwargs)


class CapabilityUnsupported(BrokerError):
    """The broker does not offer the requested capability.

    Raised by the Broker Gateway *before* the adapter is called, so an
    unsupported capability costs no network call and cannot be mistaken for an
    outage. This is a permanent property of the broker, not a failure.
    """

    def __init__(self, broker: str, capability, display_name: str = None):
        capability_value = getattr(capability, "value", capability)
        name = display_name or broker
        super().__init__(
            f"broker {broker!r} does not support capability {capability_value!r}",
            user_message=f"{name} does not support this feature.",
            code=BrokerErrorCode.UNSUPPORTED.value,
            broker=broker,
            operation=capability_value,
        )
        self.capability = capability_value


class UnknownBrokerError(BrokerError):
    """No adapter is registered under that name."""

    def __init__(self, broker: str):
        super().__init__(
            f"unsupported broker: {broker!r}",
            user_message="That broker is not supported.",
            code=BrokerErrorCode.UNKNOWN_BROKER.value,
            broker=broker,
        )


class BrokerContractError(BrokerError):
    """A broker payload could not be expressed in the canonical contract.

    Signals an adapter defect or an upstream API change. Surfaced rather than
    swallowed: a holding silently dropped because one field changed shape is a
    portfolio that quietly reports the wrong value, which is worse than an
    error.
    """

    def __init__(self, message: str, *, broker: str = None, operation: str = None):
        super().__init__(
            message,
            user_message="Your broker returned data we could not read. Please retry.",
            code=BrokerErrorCode.CONTRACT.value,
            broker=broker,
            operation=operation,
        )


def _coerce_code(code) -> BrokerErrorCode:
    try:
        return BrokerErrorCode(getattr(code, "value", code))
    except ValueError:
        return BrokerErrorCode.ERROR


def normalize_broker_error(
    exc: BaseException,
    *,
    broker: str,
    operation: str,
    display_name: str = None,
) -> BrokerError:
    """Convert any exception raised beneath the gateway into a `BrokerError`.

    Already-normalized errors pass through with `broker`/`operation` filled in
    if the adapter did not set them — the adapter knows the failure, the gateway
    knows the context, and neither should have to know both.

    Everything else becomes a generic `BrokerError` whose *user* message names
    the broker and nothing else. The original exception is chained so the
    traceback survives in logs.
    """
    if isinstance(exc, BrokerError):
        if exc.broker is None:
            exc.broker = broker
        if exc.operation is None:
            exc.operation = operation
        return exc

    name = display_name or broker
    normalized = BrokerError(
        f"{broker}.{operation} failed: {type(exc).__name__}: {exc}",
        user_message=f"Could not complete this request with {name}. Please retry.",
        code=BrokerErrorCode.ERROR.value,
        broker=broker,
        operation=operation,
    )
    normalized.__cause__ = exc
    return normalized

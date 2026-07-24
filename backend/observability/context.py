"""Request correlation context (PH2.5).

WHY THIS MODULE EXISTS
----------------------
A single user action fans out across middleware, a route handler, two or three
services, a Mongo call and maybe a market-data provider — each of which logs
independently. Without a shared key, a production log stream is a pile of
unrelated sentences: you can see that *something* failed at 09:41:07, but not
which request it belonged to, what else that request did, or whether the slow
Mongo query two lines below was the same one.

A request ID fixes that. One value is minted (or accepted from the edge) at the
front door, carried for the lifetime of the request, attached to every log line
automatically, returned to the client in `X-Request-ID`, and recorded on audit
events. A support ticket then becomes a grep: the user pastes the ID from the
error toast and the whole causal chain comes back in one query.

WHY contextvars AND NOT AN ARGUMENT
-----------------------------------
The alternative is threading a `request_id` parameter through every function
signature in the codebase. That is a mechanical change to hundreds of call
sites, it rots the moment someone forgets, and it puts transport concerns into
domain functions. `contextvars.ContextVar` is the asyncio-native answer: the
value is bound to the logical task, is inherited by tasks the request spawns,
and is isolated between concurrent requests on the same event loop — unlike a
module-level global or `threading.local`, both of which would leak one user's
request ID onto another user's log lines under async interleaving.

WHY INBOUND IDs ARE VALIDATED, NOT TRUSTED
------------------------------------------
`X-Request-ID` is attacker-controlled input. Accepting it verbatim into a log
line is log injection: a newline in the header forges log entries, a
multi-kilobyte value bloats every record of the request, and unbounded values
are a cardinality hazard the moment anyone groups by them. So an inbound ID is
accepted only if it looks like an ID (bounded length, conservative charset);
anything else is silently replaced with a fresh one. Propagation is a
convenience for tracing across a proxy — never a trust boundary.

This module deliberately imports nothing from the rest of the codebase, so that
`security.audit` and every `observability.*` module can depend on it freely.
"""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Optional

# The canonical header, in the casing operators expect to see. HTTP header names
# are case-insensitive on the wire; this constant exists so the spelling is
# identical in the middleware, the audit logger and the documentation.
REQUEST_ID_HEADER = "X-Request-ID"

# Accepted shape for an inbound ID. Bounded on both ends and restricted to
# characters that cannot break a JSON string, a log line, or a shell pipeline:
# no whitespace (log injection), no quotes/backslashes (JSON escaping), no
# control characters. 8 is the floor because a 1-character "ID" correlates
# nothing; 128 is the ceiling because it comfortably fits a W3C traceparent
# (55 chars) or a UUID with a prefix, and nothing legitimate is longer.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")

# Sentinel used when no request is in scope (a scheduler tick, a startup task, a
# unit test). An explicit marker beats an empty string: "-" reads unambiguously
# in a log column and never looks like a missing field.
NO_REQUEST_ID = "-"


@dataclass(frozen=True)
class RequestContext:
    """Immutable per-request correlation facts.

    Frozen because it is shared by reference with every task the request spawns:
    a mutable context would let a background task rewrite the parent's fields
    mid-flight. Updating means binding a new instance, which is cheap.

    ``path`` is the raw request path with the query string already discarded —
    a query string can carry a recovery token or an OAuth code, and this value
    reaches log lines. It is never used as a metric label (see
    ``observability.metrics`` on cardinality); the route *template* is.
    """

    request_id: str
    method: str = "-"
    path: str = "-"


_EMPTY = RequestContext(request_id=NO_REQUEST_ID)

# One ContextVar holding the whole context rather than one per field: binding is
# a single set/reset pair on the hot path instead of three.
_CONTEXT: ContextVar[RequestContext] = ContextVar(
    "stockassist_request_context", default=_EMPTY
)


def new_request_id() -> str:
    """Mint a fresh request ID.

    `uuid4().hex` — 32 hex characters, 122 bits of entropy, no hyphens (one less
    thing to quote in a log query). Collision probability across any realistic
    request volume is negligible, and unlike a counter it is stateless, so every
    worker process and every replica mints IDs independently with no
    coordination and no risk of two pods emitting the same ID.
    """
    return uuid.uuid4().hex


def is_valid_request_id(value: Optional[str]) -> bool:
    """True when ``value`` is safe to adopt as this request's correlation ID."""
    return bool(value) and _ID_PATTERN.match(value or "") is not None


def resolve_request_id(inbound: Optional[str]) -> str:
    """The ID this request will carry: the caller's if plausible, else a new one.

    This is the whole trust decision, in one place. A well-formed inbound ID is
    honored so a request can be followed across a load balancer, an API gateway
    or a sibling service that already stamped one. Anything malformed, empty or
    absent yields a fresh ID rather than an error — a bad header must never be
    able to fail a request.
    """
    if is_valid_request_id(inbound):
        return inbound  # type: ignore[return-value]
    return new_request_id()


def bind(request_id: str, method: str = "-", path: str = "-") -> Token:
    """Bind a request context for the current task; returns the reset token.

    The caller **must** pass the token to :func:`reset` in a ``finally`` block.
    Under a long-lived server process, leaking bindings is not merely untidy: the
    next request handled by a task that inherited a stale context would log the
    previous request's ID, which is worse than logging none at all — it produces
    confidently wrong correlation.
    """
    return _CONTEXT.set(RequestContext(request_id=request_id, method=method, path=path))


def reset(token: Token) -> None:
    """Restore the context that was in scope before the matching :func:`bind`.

    Never raises. A ``ValueError`` here means the token belongs to a different
    Context (possible if a caller binds in one task and resets in another); the
    binding is then already out of scope for this task and there is nothing to
    undo, so swallowing it is correct and keeps a teardown path from masking the
    real exception it is unwinding.
    """
    try:
        _CONTEXT.reset(token)
    except ValueError:  # pragma: no cover - defensive; see docstring
        pass


def current() -> RequestContext:
    """The active request context, or an empty one outside a request."""
    return _CONTEXT.get()


def current_request_id() -> str:
    """The active request ID, or ``"-"`` when no request is in scope.

    Returns the sentinel rather than ``None`` so callers can drop it straight
    into a log field without a conditional. Use :func:`current_request_id_or_none`
    where the distinction matters (e.g. a nullable database column).
    """
    return _CONTEXT.get().request_id


def current_request_id_or_none() -> Optional[str]:
    """The active request ID, or ``None`` when no request is in scope."""
    rid = _CONTEXT.get().request_id
    return None if rid == NO_REQUEST_ID else rid

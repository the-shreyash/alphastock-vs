"""Centralized ObjectId parsing (PH1.12 / finding F-2).

The single place an untrusted string is turned into a :class:`bson.ObjectId`.

A raw ``ObjectId(value)`` call raises ``bson.errors.InvalidId`` on any malformed
input. Left uncaught inside a request handler that surfaces to the client as an
HTTP 500 — an *implementation* error leaking out — when the correct response to
a malformed identifier in the URL, query string, or request body is a clean
HTTP 400 (the caller sent something invalid). Routing every user-supplied
identifier through :func:`parse_object_id` guarantees that posture uniformly and
removes a class of accidental 500s that also thin out log noise and error
budgets.

Scope — where to use it:

* USE for identifiers that cross the trust boundary: path parameters, query
  parameters, and request-body fields supplied by the client.
* SKIP for identifiers that are ObjectId-valid by construction: an ``_id`` read
  back from Mongo, or the ``sub`` of an already-verified JWT. Re-parsing those
  is harmless but adds noise; leaving them as raw ``ObjectId(...)`` documents at
  a glance that the value is trusted.

Framework note: like ``security.cookies`` and ``security.rate_limit``, this is a
request-layer helper (not a pure primitive like ``security.jwt``), so it raises
``fastapi.HTTPException`` directly — that is the whole point, giving call sites a
clean 4xx with zero boilerplate.
"""
from __future__ import annotations

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

__all__ = ["parse_object_id"]


def parse_object_id(value, resource: str = "resource") -> ObjectId:
    """Parse an untrusted identifier into an :class:`~bson.ObjectId`.

    Args:
        value: The candidate identifier (typically a ``str`` from a path/query
            parameter or request body). An existing ``ObjectId`` is returned
            unchanged so the helper is safe to apply idempotently.
        resource: Human-readable name of the resource the id addresses (e.g.
            ``"user"``, ``"trade"``). Used only to shape the error message.

    Returns:
        The parsed ``ObjectId``.

    Raises:
        HTTPException: ``400 Bad Request`` with detail ``"Invalid <resource> id"``
            if ``value`` is not a syntactically valid ObjectId. The invalid value
            itself is never echoed back, to avoid reflecting arbitrary input.
    """
    if isinstance(value, ObjectId):
        return value
    # Guard the surprising ``ObjectId(None)`` behavior: bson treats a ``None``
    # argument as "generate a brand-new id" rather than raising. An untrusted
    # missing/null field must be rejected, not silently turned into a random id.
    # Restrict parsing to strings — the only shape an HTTP identifier arrives in.
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"Invalid {resource} id")
    try:
        return ObjectId(value)
    except (InvalidId, TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid {resource} id")

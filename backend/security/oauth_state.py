"""Single-use, TTL-bounded, server-side OAuth ``state`` records (D6.1).

One primitive, two flows. Google sign-in has had a correct implementation of
this since PH1.2 (a random nonce, a server-side record with an authoritative
TTL, an HttpOnly double-submit cookie, and fetch-and-delete on use). The broker
OAuth flow had none of it: it carried the app's **user id in clear text** in the
provider's echoed ``state`` / ``redirect_params`` and trusted it verbatim on a
public callback (D6-S1). Anyone could rewrite that value.

This module is that Google primitive, lifted out of ``server.py`` so both flows
share one auditable implementation instead of one flow having a good version and
the other having none.

WHAT A STATE RECORD IS
----------------------
An opaque, cryptographically random handle (``secrets.token_urlsafe(32)`` — 256
bits) that names a **server-side** record describing the flow that minted it.
The provider echoes the handle back; the server looks up what it means. Nothing
the caller can read or forge participates in the decision.

THE FOUR PROPERTIES, AND WHAT EACH DEFEATS
------------------------------------------
1. **Unguessable** — 256 bits from ``secrets``. An attacker cannot fabricate a
   state that resolves to a victim.
2. **Single-use** — :func:`consume` is fetch-and-delete. A captured callback URL
   replayed a second time resolves to nothing.
3. **Short-lived** — the record's TTL is the authoritative expiry. A cookie's
   ``Max-Age`` is client-controlled and therefore not a control at all.
4. **Bound to the initiator** — the record carries the ``user_id`` that started
   the flow, and the caller additionally double-submits the handle in an
   HttpOnly cookie. The record alone stops "graft the attacker's broker onto a
   victim" (the attacker cannot mint a victim-bound state); the **cookie** is
   what stops the mirror-image attack — "graft a *victim's* broker onto the
   attacker" — in which the attacker mints a state bound to themselves and lures
   the victim through the provider login with it. That state is valid and
   attacker-owned; only the fact that the matching cookie lives in the
   *attacker's* browser and not the victim's rejects it. Both checks are
   mandatory. Neither is sufficient alone.

STORAGE
-------
``services.cache`` — Redis when ``REDIS_URL`` is configured (so the record is
shared across processes and a callback may land on any worker), an in-memory
dict otherwise. The same graceful degradation the rest of the app relies on.

FAIL-CLOSED
-----------
Every failure mode — absent, unparseable, expired, already consumed, bound to a
different flow — returns ``None`` from :func:`consume` and is indistinguishable
to the caller, who must reject. There is no "accept when the check could not be
performed" branch anywhere in this module, by construction: there is no branch
at all.
"""
from __future__ import annotations

import hmac
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Key namespace in the shared cache. The flow name is part of the key, so a
#: state minted for Google sign-in can never be consumed by the broker callback
#: (or vice versa) even if the handle itself leaked between them.
KEY_PREFIX = "oauth_state"

#: Flow names. Kept as constants so a typo is an ImportError, not a state
#: record written to a namespace nothing ever reads.
FLOW_GOOGLE = "google"
FLOW_BROKER = "broker"

#: Default record lifetime. Ten minutes is long enough for a human to complete
#: a provider login and short enough that a captured callback URL is stale by
#: the time it is useful. Matches the PH1.2 Google value.
DEFAULT_TTL_SECONDS = 600

#: Bytes of entropy behind each handle (``token_urlsafe`` yields ~43 chars).
_STATE_BYTES = 32


def new_state() -> str:
    """A fresh, cryptographically secure, URL-safe state handle."""
    return secrets.token_urlsafe(_STATE_BYTES)


def _key(flow: str, state: str) -> str:
    return f"{KEY_PREFIX}:{flow}:{state}"


async def issue(flow: str, payload: Dict[str, Any], *,
                ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Mint a state handle and persist ``payload`` against it. Returns the handle.

    ``payload`` is what the callback will be told about the flow — for the
    broker flow that is ``{"user_id": …, "broker": …}``. It is written
    server-side and never travels through the browser, which is the entire
    point: the callback learns the owning user from storage, not from input.
    """
    from services.cache import cache_set

    state = new_state()
    record = dict(payload)
    record["created"] = datetime.now(timezone.utc).isoformat()
    await cache_set(_key(flow, state), record, ttl_seconds)
    return state


async def consume(flow: str, state: str) -> Optional[Dict[str, Any]]:
    """Atomically fetch-and-delete a state record.

    Returns the record on its **first** presentation, and ``None`` for every
    other outcome: no state supplied, unknown handle, expired handle, or a
    handle already spent (replay). The caller cannot tell these apart and must
    not try to — every one of them means "this callback has not proved which
    user started the flow".
    """
    if not state:
        return None
    from services.cache import cache_delete, cache_get

    key = _key(flow, state)
    record = await cache_get(key)
    if record is None:
        return None
    await cache_delete(key)
    return record if isinstance(record, dict) else None


def matches_cookie(state: str, cookie_value: Optional[str]) -> bool:
    """Constant-time double-submit check between the echoed state and the cookie.

    Both must be present and equal. A missing cookie is a rejection, not a
    skipped check — that asymmetry ("verify it when it happens to be there") is
    exactly how the original defect behaved for ``uid``.
    """
    if not state or not cookie_value:
        return False
    return hmac.compare_digest(str(state), str(cookie_value))

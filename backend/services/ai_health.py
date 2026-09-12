"""Observed AI provider health — what the models actually did, not what is configured.

D6.9 — WHY THIS EXISTS.
──────────────────────
Before this module the platform had exactly one notion of "AI status":

    is_configured -> bool(os.environ.get("ANTHROPIC_API_KEY"))

and `/api/ai/status` published it as ``online``, which the workspace header
renders as **"AI ready"**. A key with no billing credit, a revoked key, a
typo'd key and a healthy key are indistinguishable under that test, so a
deployment whose every model call was failing displayed *AI ready* while the
Morning Report page underneath it displayed the provider's own outage text.
That contradiction was not a rendering bug — the backend genuinely did not know
the difference, because nothing ever recorded the *outcome* of a call.

`observability.instruments.track_ai` already counts those outcomes, but into
Prometheus counters: monotonic, aggregate, and unreadable as "did the last call
work". This module is the other half — the small amount of *state* a status
endpoint needs, recorded at the same two chokepoints.

WHAT IS AND IS NOT RECORDED
───────────────────────────
Recorded: provider name, the model id that answered, the instant, and a
*classified* error label drawn from the closed vocabulary in
`observability.errors`. Never recorded: the provider's error string, which can
carry a request id, an account identifier or an echoed prompt (the same reason
`track_ai.failed` classifies rather than stores).

IT FAILS TOWARD "UNKNOWN"
─────────────────────────
A provider that has been configured but never called reports
``verified=False`` with no failure — the honest answer, and the one that keeps
a freshly booted process behaving exactly as it does today. Only an observed
failure moves a provider out of the healthy state, so this can never invent an
outage it did not see.

SCOPE (limitation, deliberate)
──────────────────────────────
Process-local, like the AI activity deque (LIM-D6.1-3). With
``WEB_CONCURRENCY > 1`` each worker reports the calls it personally made. That
fails toward missing evidence, never toward false confidence: a worker that has
seen no failure says "not yet verified", not "healthy".
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

#: Providers this module will accept. A closed set, mirroring
#: `observability.instruments.AI_PROVIDERS`, so a typo becomes a KeyError in
#: development rather than a silently-never-read health row in production.
PROVIDERS = ("claude", "gemini")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _ProviderHealth:
    """The last observed outcome for one provider.

    Mutated only through the module-level `record_*` functions. Every field is
    a plain scalar assigned in one statement, so a concurrent reader sees a
    consistent field even without a lock — and a torn read across two fields
    would at worst produce a slightly-stale status line, never a wrong
    authorization decision. A lock here would serialise every AI call in the
    process to protect a display value.
    """

    __slots__ = ("last_success_at", "last_success_model", "last_failure_at",
                 "last_error_class", "calls_attempted")

    def __init__(self) -> None:
        self.last_success_at: Optional[str] = None
        self.last_success_model: Optional[str] = None
        self.last_failure_at: Optional[str] = None
        self.last_error_class: Optional[str] = None
        self.calls_attempted: int = 0


_health: Dict[str, _ProviderHealth] = {p: _ProviderHealth() for p in PROVIDERS}


def _slot(provider: str) -> Optional[_ProviderHealth]:
    return _health.get(provider)


def record_success(provider: str, model: Optional[str] = None) -> None:
    """A real model returned real content."""
    slot = _slot(provider)
    if slot is None:
        return
    slot.calls_attempted += 1
    slot.last_success_model = model
    slot.last_success_at = _now_iso()


def record_failure(provider: str, error_class: Optional[str] = None) -> None:
    """A real call was made and did not produce usable content.

    `error_class` must be a label from the closed `observability.errors`
    vocabulary. IT IS ENFORCED, NOT REQUESTED: anything else is replaced with
    the generic AI-provider class and never stored.

    A docstring asking callers not to pass raw provider text is not a control —
    it holds exactly until someone writes `record_failure(p, resp.error)`, which
    is the obvious thing to write, and then a request id, an account identifier
    or an echoed prompt is sitting in a field that `/api/ai/status` serves. The
    closed vocabulary is small and the coercion is total, so the leak is not
    reachable from any caller.
    """
    slot = _slot(provider)
    if slot is None:
        return
    slot.calls_attempted += 1
    slot.last_error_class = _closed_label(error_class)
    slot.last_failure_at = _now_iso()


def _closed_label(value: object) -> str:
    """Coerce to a member of the closed error vocabulary, or the generic class."""
    from observability import errors

    return value if value in errors.ERROR_CLASSES else errors.AI_PROVIDER


def classify(error_text: object) -> str:
    """Map a provider error to the closed error vocabulary.

    Delegates to the same classifier `track_ai` uses, so the label on a health
    row and the label on the corresponding metric can never disagree. Any
    failure to classify degrades to the generic AI-provider class rather than
    leaking the text.
    """
    from observability import errors, instruments

    try:
        if isinstance(error_text, BaseException):
            return errors.classify_exception(error_text, default=errors.AI_PROVIDER)
        if isinstance(error_text, str):
            return instruments._classify_ai_error_text(error_text)
    except Exception:  # pragma: no cover - defensive
        pass
    return errors.AI_PROVIDER


def provider_status(provider: str, *, configured: bool) -> Dict[str, object]:
    """One provider's honest status line.

    `configured` is supplied by the caller because key presence is the
    provider module's fact, not this module's — mixing the two here is exactly
    the conflation D6.9 exists to undo.

    The three states a consumer can distinguish:

      configured=False                     → no key; nothing was ever attempted
      configured=True,  verified=False     → key present, no successful call yet
      configured=True,  degraded=True      → the most recent call FAILED

    `verified` is deliberately "has ever succeeded in this process" and not
    "succeeded recently": a report generated at 08:30 proves the provider
    worked at 08:30, and that fact does not expire. Whether the provider works
    *now* is `degraded`, which is what a live status pill should read.
    """
    slot = _slot(provider) or _ProviderHealth()
    degraded = bool(
        slot.last_failure_at
        and (slot.last_success_at is None or slot.last_failure_at > slot.last_success_at)
    )
    return {
        "configured": bool(configured),
        "verified": slot.last_success_at is not None,
        "degraded": degraded,
        "calls_attempted": slot.calls_attempted,
        "last_success_at": slot.last_success_at,
        "last_success_model": slot.last_success_model,
        "last_failure_at": slot.last_failure_at,
        # A classified label from a closed vocabulary — never the provider's text.
        "last_error_class": slot.last_error_class,
    }


def reset() -> None:
    """Clear all observed health. Test-only; there is no production caller."""
    for slot in _health.values():
        slot.__init__()  # type: ignore[misc]

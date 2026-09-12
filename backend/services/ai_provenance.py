"""Canonical provenance for AI-generated artifacts (D6.9).

THE QUESTION THIS MODULE ANSWERS
────────────────────────────────
"Is what I'm looking at a real AI analysis, and how old is it?"

Before D6.9 the platform could not answer it for any artifact. The Morning
Report carried a `generated_at` and an `available: true`, and from those two
fields the UI inferred everything else — which model wrote the briefing (it
guessed "AI"), whether a model wrote it at all (it assumed so), and how current
it was (it didn't ask). Four *different* facts were collapsed into one:

    GENERATION TIME  ≠  MARKET DATA TIME  ≠  CURRENT AI HEALTH  ≠  FRESHNESS

Collapsing them produced the failure this module exists to make impossible: on
a deployment whose API key had no credit, the header said **AI ready**, the
Morning Report said **AI Market Briefing**, and the text underneath was the
provider's own "AI services are currently offline" message — persisted into
`db.reports` and served to every user for the rest of the day.

WHY IT IS NOT MORNING-REPORT-SHAPED
───────────────────────────────────
Morning Report is the first artifact, not the only one. Top Picks, AI Insights,
trade reviews and portfolio reviews are all artifacts whose consumers will ask
the same seven questions. So the model here is a plain, artifact-agnostic
document keyed by `artifact_type`, and adding an artifact means calling
`begin()` / `completed()` / `failed()` — never extending this module.

THE SEVEN QUESTIONS, AND THE FIELD THAT ANSWERS EACH
────────────────────────────────────────────────────
    what generated it        artifact_type + analysis_version
    when                     generated_at (start) / completed_at (success)
    which provider/model     ai.provider / ai.model  (None when no model ran)
    which data snapshot      market_data.source_tier + market_data.observed_at
    did it actually succeed  status  (the ONLY field that licenses "AI-generated")
    how fresh is it now      freshness(), derived from completed_at alone
    is it being updated      status == GENERATING

RULES THIS MODULE ENFORCES, NOT DOCUMENTS
─────────────────────────────────────────
1. `ai.provider` / `ai.model` are set **only** by :func:`ai_succeeded`. There is
   no code path that writes a model name without a model having answered, so a
   fabricated attribution is not a discipline question.
2. Freshness derives from `completed_at` — the instant a generation *succeeded*
   — and from nothing else. Not `updated_at`, not the request clock, not the
   database's `_id` timestamp.
3. A record whose `completed_at` is absent or unparseable is
   :data:`Freshness.UNKNOWN`. It is never assigned an age, and no timestamp is
   invented for it. Legacy documents written before D6.9 land here by
   construction, which is the correct answer for them: their provenance is
   genuinely not known.
4. Error detail that leaves this module is a *classified label*, never a
   provider error string — those carry request ids, account identifiers and
   echoed prompts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

#: Shape version of the provenance document. Bump when a consumer would read an
#: older document incorrectly — a record carrying a lower version is readable,
#: but only for the fields that version defined.
PROVENANCE_VERSION = 1

#: Artifact types. A closed set: an artifact nobody declared cannot acquire
#: provenance by accident, and the freshness policy table below is total over it.
ARTIFACT_MORNING_REPORT = "morning_report"

ARTIFACT_TYPES = frozenset({ARTIFACT_MORNING_REPORT})


class GenerationStatus(str, Enum):
    """What actually happened to a generation attempt.

    `str` mixin so the value serialises to JSON and compares to a plain string
    without a converter at every boundary.
    """

    #: A generation is in flight. Any artifact body present is partial.
    GENERATING = "generating"
    #: Generation ran to completion and produced a usable artifact.
    COMPLETED = "completed"
    #: Generation was attempted and did not produce a usable artifact.
    FAILED = "failed"
    #: Generation could not be attempted (its inputs were unreachable).
    UNAVAILABLE = "unavailable"


class Freshness(str, Enum):
    """How old a *successful* generation is, right now."""

    FRESH = "fresh"
    STALE = "stale"
    VERY_STALE = "very_stale"
    #: Completed, but the completion instant is not known (legacy records).
    UNKNOWN = "unknown"
    #: There is no successful generation, so there is no age to report. A
    #: failed artifact is not "very stale" — it never became an artifact.
    UNAVAILABLE = "unavailable"


class AIOutcome(str, Enum):
    """Why an artifact's AI layer is or is not attributable to a model."""

    #: A configured model returned content.
    SUCCEEDED = "succeeded"
    #: No model was configured, so none was called.
    NOT_CONFIGURED = "not_configured"
    #: A model was called and did not return usable content.
    PROVIDER_ERROR = "provider_error"
    #: The artifact has no AI layer at all (fully deterministic).
    NOT_APPLICABLE = "not_applicable"


# --------------------------------------------------------------------------- #
# Freshness policy
# --------------------------------------------------------------------------- #
#
# WHERE THESE NUMBERS COME FROM — they are derived from the NSE session, not
# chosen for roundness.
#
# The Morning Report is generated at 08:30 IST, 45 minutes before the 09:15
# open, and it describes the session about to begin.
#
#   FRESH      ≤ 3h   08:30 + 3h = 11:30 IST. The pre-open read is still the
#                     operative one through the first two hours of trading;
#                     this is the window in which the briefing's framing of the
#                     day has not yet been overtaken by the day.
#   STALE      ≤ 12h  08:30 + 12h = 20:30 IST — after the 15:30 close. Still
#                     the same trading day, so the analysis remains the correct
#                     historical record of this morning, but the market has
#                     moved and the reader must be told so.
#   VERY_STALE > 12h  Necessarily predates today's pre-open window: this is a
#                     previous session's briefing.
#
# The report is never hidden at any age — Phase 6 of the D6.9 brief and the
# product rule that a previously generated analysis stays viewable. These
# thresholds change the *label*, never the availability.
FRESH_MAX_SECONDS = 3 * 3600
STALE_MAX_SECONDS = 12 * 3600

#: Shipped to clients alongside the provenance so the browser can let the age
#: advance between refetches without re-deciding the policy. The thresholds
#: live here and only here.
FRESHNESS_POLICY: Dict[str, int] = {
    "fresh_max_seconds": FRESH_MAX_SECONDS,
    "stale_max_seconds": STALE_MAX_SECONDS,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_instant(value: Any) -> Optional[datetime]:
    """Parse a stored ISO instant, or return None.

    Returns None for anything that is not an unambiguous instant — including a
    `datetime` with no timezone, because a naive timestamp read as UTC is a
    guess, and guessing is the one thing a provenance module may not do. A None
    here becomes :data:`Freshness.UNKNOWN`, never a fabricated age.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #

def begin(artifact_type: str, artifact_id: str, *, analysis_version: str) -> Dict[str, Any]:
    """Open a provenance record for a generation that is starting now.

    `generated_at` is stamped here and never moved: it is when the attempt
    began. The instant that matters for freshness is `completed_at`, which only
    :func:`completed` can set.
    """
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError(f"unknown artifact type: {artifact_type!r}")
    return {
        "provenance_version": PROVENANCE_VERSION,
        "artifact_type": artifact_type,
        "artifact_id": artifact_id,
        "status": GenerationStatus.GENERATING.value,
        "generated_at": _iso(_now()),
        "completed_at": None,
        "analysis_version": analysis_version,
        "ai": {
            "outcome": AIOutcome.NOT_APPLICABLE.value,
            "provider": None,
            "model": None,
            "prompt_key": None,
            "prompt_version": None,
        },
        "market_data": {"source_tier": None, "observed_at": None},
        "error": None,
    }


def ai_succeeded(
    provenance: Dict[str, Any],
    *,
    provider: str,
    model: Optional[str],
    prompt_key: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Record that a real model produced this artifact's AI layer.

    THE ONLY WRITER OF `ai.provider` / `ai.model`. Everything downstream that
    labels content "AI-generated" reads `ai.outcome == succeeded`, so a
    fabricated attribution would have to be introduced here, in one place, on
    purpose — rather than emerging from any caller that forgot to check.
    """
    provenance["ai"] = {
        "outcome": AIOutcome.SUCCEEDED.value,
        "provider": provider,
        "model": model,
        "prompt_key": prompt_key,
        "prompt_version": prompt_version,
    }
    return provenance


def ai_did_not_run(
    provenance: Dict[str, Any],
    *,
    outcome: AIOutcome,
    prompt_key: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Record that no model produced this artifact's AI layer, and why.

    Leaves `provider` and `model` None — an artifact whose model layer did not
    run has no model to name, and naming the provider that was *attempted*
    would read, to every consumer, as the provider that answered.
    """
    provenance["ai"] = {
        "outcome": AIOutcome(outcome).value,
        "provider": None,
        "model": None,
        "prompt_key": prompt_key,
        "prompt_version": prompt_version,
    }
    return provenance


def market_data(
    provenance: Dict[str, Any],
    *,
    source_tier: Optional[str],
    observed_at: Optional[str],
) -> Dict[str, Any]:
    """Record which market snapshot this artifact was built from.

    `source_tier` and never a provider name: MARKET_DATA_ARCHITECTURE.md
    Developer Rule 4 makes the tier the only market-data provenance permitted
    to leave the gateway, and an artifact is not an exemption from it.
    """
    provenance["market_data"] = {"source_tier": source_tier, "observed_at": observed_at}
    return provenance


def completed(provenance: Dict[str, Any]) -> Dict[str, Any]:
    """Mark a generation successful, stamping the canonical freshness clock."""
    provenance["status"] = GenerationStatus.COMPLETED.value
    provenance["completed_at"] = _iso(_now())
    provenance["error"] = None
    return provenance


def failed(
    provenance: Dict[str, Any],
    *,
    code: str,
    message: str,
    status: GenerationStatus = GenerationStatus.FAILED,
) -> Dict[str, Any]:
    """Mark a generation as failed or unavailable.

    `completed_at` stays None — there is no successful generation, so there is
    no instant for freshness to measure from, and inventing one would make a
    failure read as a fresh report.

    `message` must be a message written for a user. Provider error strings,
    stack traces and exception reprs are not; they carry request ids, account
    identifiers and echoed prompts, and this field is served over the API.
    """
    provenance["status"] = GenerationStatus(status).value
    provenance["completed_at"] = None
    provenance["error"] = {"code": code, "message": message}
    return provenance


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def age_seconds(provenance: Optional[Dict[str, Any]], *, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds since the artifact's generation *succeeded*, or None.

    None means "not knowable" and is returned for every record that has no
    parseable `completed_at` — a failure, a generation in flight, or a legacy
    document. It is never approximated from another field.
    """
    if not provenance:
        return None
    if provenance.get("status") != GenerationStatus.COMPLETED.value:
        return None
    completed_at = parse_instant(provenance.get("completed_at"))
    if completed_at is None:
        return None
    return max(0.0, ((now or _now()) - completed_at).total_seconds())


def freshness(provenance: Optional[Dict[str, Any]], *, now: Optional[datetime] = None) -> Freshness:
    """How current this artifact is, from its own completion instant.

    UNKNOWN AND UNAVAILABLE ARE NOT THE SAME ANSWER, and the difference is the
    whole legacy-data rule. A *missing* record cannot tell you that generation
    did not succeed — only that nobody recorded whether it did — so it is
    UNKNOWN. UNAVAILABLE requires a record that explicitly says the generation
    failed, is still running, or could not be attempted. Collapsing the two
    would let every pre-D6.9 report be reported as a failed generation.
    """
    if not provenance:
        return Freshness.UNKNOWN
    if provenance.get("status") != GenerationStatus.COMPLETED.value:
        return Freshness.UNAVAILABLE
    age = age_seconds(provenance, now=now)
    if age is None:
        return Freshness.UNKNOWN
    if age <= FRESH_MAX_SECONDS:
        return Freshness.FRESH
    if age <= STALE_MAX_SECONDS:
        return Freshness.STALE
    return Freshness.VERY_STALE


def is_ai_generated(provenance: Optional[Dict[str, Any]]) -> bool:
    """Whether this artifact's AI layer may truthfully be labelled AI-generated.

    Both halves are required, and each rules out a failure the other misses:
    a completed artifact whose model call failed carries deterministic prose,
    and a succeeded model call inside an artifact that then failed to complete
    has no published artifact to attribute.
    """
    if not provenance or provenance.get("status") != GenerationStatus.COMPLETED.value:
        return False
    return (provenance.get("ai") or {}).get("outcome") == AIOutcome.SUCCEEDED.value


def describe(provenance: Optional[Dict[str, Any]], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The client-facing provenance view.

    A superset of the stored record plus the two derived values, so no consumer
    has to re-implement the policy. Everything here is safe to serve: the
    stored record holds no secret, no raw provider error and no prompt text.

    A missing record is described rather than omitted — a legacy document with
    no provenance is `status: unknown`, which is the true statement about it and
    is distinguishable by every consumer from a verified one.
    """
    if not provenance:
        return {
            "provenance_version": PROVENANCE_VERSION,
            "status": "unknown",
            "known": False,
            "generated_at": None,
            "completed_at": None,
            "ai": {"outcome": AIOutcome.NOT_APPLICABLE.value, "provider": None, "model": None,
                   "prompt_key": None, "prompt_version": None},
            "market_data": {"source_tier": None, "observed_at": None},
            "analysis_version": None,
            "artifact_type": None,
            "artifact_id": None,
            "error": None,
            "freshness": Freshness.UNKNOWN.value,
            "age_seconds": None,
            "is_ai_generated": False,
            "freshness_policy": dict(FRESHNESS_POLICY),
        }

    return {
        **provenance,
        "known": True,
        "freshness": freshness(provenance, now=now).value,
        "age_seconds": age_seconds(provenance, now=now),
        "is_ai_generated": is_ai_generated(provenance),
        "freshness_policy": dict(FRESHNESS_POLICY),
    }

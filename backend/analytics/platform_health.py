"""Platform-health analytics, read from real probes and real counters (PH3.9).

WHAT THIS REPLACES
------------------
Three admin surfaces reported the platform's own health from literals:

* ``GET /api/admin/apis/health`` returned a hardcoded list in which ``status``
  meant *a credential is configured* and never *the dependency answered*, with
  literal latencies, literal request counts and ``overall_status: "healthy"`` —
  a constant. That page reported a healthy platform during a total outage,
  which is the exact inverse of what an operational dashboard is for.
* ``GET /api/admin/ai/status`` reported ``latency_ms: 1200``, ``failures: 0``
  and ``fallbacks: 0`` as literals, *beside* a live failure counter PH3.7 had
  already shipped. An operator could not see an outage the platform was
  measuring.
* ``GET /api/admin/system/health`` reported Redis as the constant
  ``"not_configured"`` and the scheduler as the constant ``"running"`` — the
  latter stays ``"running"`` after the scheduler dies.

All three now read ``observability.health`` (live probes) and
``observability.metrics`` (real counters and latency histograms).

THREE HONESTY CONSTRAINTS THAT SHAPED THE OUTPUT
------------------------------------------------
**1. Counters are process-scoped and reset on restart.** They answer "since this
process started", never "today". PH3.8's inventory said to rewire "AI requests
today" to ``ai_requests_total``; doing that literally would have swapped a
fabricated number for a mislabelled one — a counter that silently resets on
every deploy, and on a multi-worker deployment covers one worker of N. So every
counter-derived figure here is named ``*_since_start`` and ships with
``process_uptime_seconds`` and an explicit ``scope`` field. Nothing sourced from
a counter is labelled "today".

**2. A latency histogram must not be averaged.** ``sum / count`` is the number
that hides outages: ninety-nine 10ms requests and one 10s request average to
110ms and every dashboard looks calm. :func:`_latency_bound` reports the bucket
boundary the 95th percentile falls at or below — a true upper bound read off the
stored buckets, not an interpolation that would invent precision the histogram
does not have.

**3. The gateway deliberately hides which upstream served a request.** The old
page listed *vendors* — Yahoo Finance, Alpha Vantage — with individual latencies.
Those numbers can never be sourced honestly, because
``MARKET_DATA_ARCHITECTURE.md`` makes the Source Manager's provider choice
invisible above the gateway on purpose, and ``instruments.PROVIDERS`` is a closed
vocabulary of *logical* providers for exactly that reason. This module reports
the logical providers that are actually instrumented and says plainly which
integrations are not measured, rather than inventing a per-vendor breakdown the
architecture forbids.
"""
from __future__ import annotations

import logging
from typing import Optional

from observability import health as obs_health
from observability import instruments, metrics, runtime

logger = logging.getLogger(__name__)

#: Reported for a dependency or integration that has no probe and no counter.
#: Distinct from "down": we are not measuring it, which is a different fact and
#: must not render as a green badge or a red one.
NOT_MEASURED = "not_measured"

CONFIGURED_ONLY_NOTE = (
    "Credential configuration only — this integration has no health probe and no "
    "request counter, so the platform does not know whether it is reachable. "
    "`configured` is a fact about the environment, never about the service."
)


# --------------------------------------------------------------------------- #
# Reading the metric registry                                                   #
# --------------------------------------------------------------------------- #
def _sum_by_outcome(counter, provider: str, provider_index: int,
                    outcome_index: int) -> dict:
    """``{outcome: count}`` for one provider, summed over every other label.

    Read through :meth:`collect` rather than by enumerating the label vocabulary
    and calling ``value()``: a series exists only once something has been
    recorded into it, so enumerating would manufacture zeros for outcomes that
    have never occurred — and a zero error count nobody observed is
    indistinguishable, on a dashboard, from one that was measured.
    """
    totals: dict = {}
    for _name, label_values, value in counter.collect():
        if provider_index >= len(label_values) or outcome_index >= len(label_values):
            continue
        if label_values[provider_index] != provider:
            continue
        outcome = label_values[outcome_index]
        totals[outcome] = totals.get(outcome, 0.0) + value
    return {k: int(v) for k, v in totals.items()}


def _latency_bound(histogram, label_prefix: tuple, quantile: float = 0.95) -> Optional[float]:
    """The bucket boundary at or below which ``quantile`` of observations fell.

    Returns milliseconds, or ``None`` when nothing has been observed — ``None``
    rather than ``0``, because "no calls yet" and "instantaneous" are opposite
    facts and PH3.8's central finding is that they must not share a rendering.

    Observations beyond the largest bucket return ``None`` too: the histogram
    genuinely does not know how slow they were, and reporting the top bucket
    bound would understate a tail that is running off the end of the scale.
    """
    aggregate = [0] * len(histogram.buckets)
    total = 0
    for _name, label_values, value in histogram.collect():
        if not _name.endswith("_count"):
            continue
        if tuple(label_values[:len(label_prefix)]) != label_prefix:
            continue
        total += int(value)
    if total == 0:
        return None
    for _name, label_values, value in histogram.collect():
        if not _name.endswith("_bucket"):
            continue
        if tuple(label_values[:len(label_prefix)]) != label_prefix:
            continue
        bound = label_values[-1]
        if bound == "+Inf":
            continue
        try:
            index = histogram.buckets.index(float(bound))
        except ValueError:
            continue
        aggregate[index] += int(value)

    target = quantile * total
    for index, bound in enumerate(histogram.buckets):
        if aggregate[index] >= target:
            return round(bound * 1000, 1)
    return None


def process_scope() -> dict:
    """The caveat every counter-derived number on this page carries.

    Emitted as structured fields rather than prose so a consumer can render it
    without parsing English, and so it is impossible to ship a counter total
    from this module without the scope travelling alongside it.
    """
    return {
        "basis": "process_lifetime",
        "process_uptime_seconds": round(runtime.uptime_seconds(), 1),
        "note": ("Counters live in this process and reset when it restarts. These "
                 "are totals since this worker started, not totals for a calendar "
                 "period, and on a multi-worker deployment they describe one "
                 "worker. For durable period totals, scrape /metrics into a "
                 "time-series database."),
    }


# --------------------------------------------------------------------------- #
# Dependency probes                                                             #
# --------------------------------------------------------------------------- #
async def dependency_status(use_cache: bool = True) -> dict:
    """Live results from every registered readiness probe, keyed by name.

    Values are the probe's own three-way answer — ``pass`` / ``fail`` / ``skip``
    — preserved rather than collapsed to a boolean. ``skip`` means *not
    configured*, which for an optional dependency like Redis is a valid
    deployment rather than a fault, and flattening it into "unhealthy" would
    make every correctly-configured install look broken.
    """
    results = await obs_health.run_checks(use_cache=use_cache)
    return {
        r.name: {
            "status": r.status,
            "healthy": r.healthy,
            "critical": r.critical,
            "duration_ms": round(r.duration_ms, 2),
            **({"detail": r.detail} if r.detail else {}),
        }
        for r in results
    }


def scheduler_status() -> dict:
    """Whether the cron scheduler is actually running, asked of the scheduler.

    Replaces the literal ``"running"``. The import is local because
    ``services.scheduler`` constructs an ``AsyncIOScheduler`` at module import,
    and a health endpoint must not be the thing that instantiates it.
    """
    try:
        from services.scheduler import scheduler
        running = bool(scheduler.running)
        return {
            "status": "running" if running else "stopped",
            "running": running,
            "jobs": len(scheduler.get_jobs()) if running else 0,
            "source": "apscheduler.running",
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("scheduler status probe failed: %s", exc)
        return {"status": NOT_MEASURED, "running": None,
                "source": "apscheduler.running",
                "detail": exc.__class__.__name__}


# --------------------------------------------------------------------------- #
# Provider and AI reporting                                                     #
# --------------------------------------------------------------------------- #
def _provider_row(name: str, label: str, kind: str, *, configured: Optional[bool],
                  instrumented: bool) -> dict:
    """One row of the API-health table, built from counters when they exist."""
    row = {
        "name": label,
        "provider": name,
        "type": kind,
        "configured": configured,
        "instrumented": instrumented,
    }
    if not instrumented:
        row.update({
            "status": NOT_MEASURED,
            "requests_since_start": None,
            "error_rate_pct": None,
            "p95_latency_ms": None,
            "note": CONFIGURED_ONLY_NOTE,
        })
        return row

    outcomes = _sum_by_outcome(metrics.provider_requests_total, name,
                               provider_index=0, outcome_index=2)
    total = sum(outcomes.values())
    errors = outcomes.get(instruments.ERROR, 0)
    empty = outcomes.get(instruments.EMPTY, 0)
    row.update({
        # No traffic yet is its own state. Calling it "online" would be the same
        # class of claim the hardcoded list made.
        "status": ("no_traffic" if total == 0
                   else "degraded" if errors else "online"),
        "requests_since_start": total,
        "outcomes": outcomes,
        "error_rate_pct": round(errors / total * 100, 2) if total else None,
        # An `empty` outcome is a call that succeeded and returned nothing
        # usable — the failure mode a status-code check misses entirely, and the
        # reason this column exists separately from the error rate.
        "empty_rate_pct": round(empty / total * 100, 2) if total else None,
        "p95_latency_ms": _latency_bound(metrics.provider_request_duration_seconds,
                                         (name,)),
    })
    return row


def api_health(configured: dict) -> dict:
    """The external-integration health table, from probes and counters.

    ``configured`` maps a logical provider name to whether its credentials are
    present. It is passed in rather than read here because the credential checks
    live with the services that own them, and it is reported as its own column:
    a configured integration is not a working one, and the old page's central
    defect was using the first to claim the second.
    """
    instrumented = {"market_data", "news"}
    rows = [
        _provider_row("market_data", "Market data (gateway)", "market_data",
                      configured=configured.get("market_data"), instrumented=True),
        _provider_row("news", "News", "news",
                      configured=configured.get("news"), instrumented=True),
    ]
    for name, label, kind in (
        ("broker_zerodha", "Zerodha Kite", "broker"),
        ("email", "Email (SMTP)", "notification"),
        ("whatsapp", "WhatsApp", "notification"),
        ("telegram", "Telegram", "notification"),
    ):
        rows.append(_provider_row(name, label, kind,
                                  configured=configured.get(name),
                                  instrumented=name in instrumented))

    measured = [r for r in rows if r["instrumented"]]
    if any(r["status"] == "degraded" for r in measured):
        overall = "degraded"
    elif any(r["status"] == "online" for r in measured):
        overall = "operational"
    else:
        overall = "no_traffic"
    return {
        "apis": rows,
        "overall_status": overall,
        "scope": process_scope(),
        "provider_granularity": (
            "Logical providers, not vendors. The Market Gateway's Source Manager "
            "chooses an upstream per request and that choice is deliberately not "
            "exposed above the gateway (MARKET_DATA_ARCHITECTURE.md), so a "
            "per-vendor latency column cannot be sourced honestly and is not "
            "offered."),
    }


def ai_providers(configured: dict) -> dict:
    """Per-AI-provider request counts, failures, fallbacks and p95 latency.

    ``fallbacks`` — previously the literal ``0`` — is
    ``ai_requests_total{provider="simulated"}``: every one of those is a user who
    received a canned response because no real model answered. It is the single
    most important number on the page and it was hardcoded to zero.
    """
    rows = []
    for name, label in (("claude", "Claude (Anthropic)"), ("gemini", "Gemini (Google)")):
        outcomes = _sum_by_outcome(metrics.ai_requests_total, name,
                                   provider_index=0, outcome_index=1)
        total = sum(outcomes.values())
        errors = _sum_by_outcome(metrics.ai_request_errors_total, name,
                                 provider_index=0, outcome_index=1)
        rows.append({
            "name": label,
            "provider": name,
            "configured": configured.get(name),
            "status": ("no_traffic" if total == 0
                       else "degraded" if outcomes.get(instruments.ERROR) else "online"),
            "requests_since_start": total,
            "outcomes": outcomes,
            "failures_since_start": sum(errors.values()),
            "error_classes": errors,
            "p95_latency_ms": _latency_bound(metrics.ai_request_duration_seconds, (name,)),
        })

    simulated = _sum_by_outcome(metrics.ai_requests_total, "simulated",
                                provider_index=0, outcome_index=1)
    return {
        "providers": rows,
        "fallbacks_since_start": sum(simulated.values()),
        "fallback_note": ("Requests served by the simulated provider — a user received "
                          "a canned response because no real model answered."),
        "scope": process_scope(),
    }

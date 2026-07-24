"""Runtime diagnostics — what this process is, and since when (PH2.5).

WHY THIS MODULE EXISTS
----------------------
Every incident review contains the same two questions, and a surprising number
of them stall on the second: *what is running?* and *when did it start?*

Without an answer served by the application itself, "which version is in
production?" is settled by correlating a deploy log with a CI run with a git
tag — three systems, none of which is the process actually serving traffic, and
all of which can be wrong (a rollback that never completed, a pod that failed to
pull the new image, a cache that served the old bundle). An endpoint that
reports its own build provenance ends that argument in one request.

Uptime answers the second, and answers more than it looks like it does. A
service whose uptime keeps resetting to under a minute is crash-looping, and
that is visible from this endpoint long before it is visible in aggregate error
rates — a container that dies during startup may never serve a single failing
request to be counted.

THE SECURITY LINE
-----------------
Diagnostics report *facts about the deployment*, never *configuration values*.
A version string, an environment name and a git SHA are things a deployment
already reveals through its behaviour. A connection URI, a host name, an API
key or the contents of the environment are not, and there is no operational
question here worth the risk of leaking one — so this module never reads a
secret's value. Where knowing whether an integration is wired up is genuinely
useful, it reports a boolean ("configured" / "not configured") obtained through
`security.secrets.is_configured`, which is presence-only by construction.

The endpoint is additionally gated in production (see `observability.routes`),
because even build provenance is a small gift to someone deciding which CVE to
try against you.
"""
from __future__ import annotations

import os
import platform
import time
from datetime import datetime, timezone
from typing import Dict, Optional

# Logical service name. Distinguishes this process's telemetry from the
# frontend's or a future worker's once they share a log stream (PH2.6).
SERVICE_NAME = "stockassist-backend"

# Fallback version for a checkout run straight from source. Deliberately marked
# `-dev`: an unlabelled "1.0.0" in a log stream that actually came from someone's
# laptop is worse than an honest "unknown".
DEFAULT_VERSION = "0.0.0-dev"
UNKNOWN = "unknown"

# Process start, captured at import — the earliest moment this module can
# observe. `time.time()` for the wall-clock timestamp (comparable across hosts
# and log systems) and `time.monotonic()` for the elapsed measure (immune to NTP
# steps and DST, which can otherwise make uptime jump or go negative).
_START_WALL = time.time()
_START_MONOTONIC = time.monotonic()
_START_ISO = datetime.fromtimestamp(_START_WALL, tz=timezone.utc).isoformat()


def service_name() -> str:
    return SERVICE_NAME


def service_version() -> str:
    """The application version.

    Sourced from `APP_VERSION`, which `backend/Dockerfile` promotes from the
    build argument that also becomes the image's OCI version label — so the
    image metadata and the running process cannot disagree.
    """
    return os.environ.get("APP_VERSION", "").strip() or DEFAULT_VERSION


def vcs_ref() -> str:
    """The git commit this build came from (`VCS_REF`), or `unknown`.

    The single most valuable field here: it turns "reproduce the bug" from
    guesswork into `git checkout <sha>`.
    """
    return os.environ.get("VCS_REF", "").strip() or UNKNOWN


def build_date() -> str:
    """Image build timestamp (`BUILD_DATE`), or `unknown`.

    Distinct from process start time, and the gap between them is itself
    informative: an image built three weeks ago and started four minutes ago is
    a restart, not a deploy.
    """
    return os.environ.get("BUILD_DATE", "").strip() or UNKNOWN


def environment() -> str:
    """Deployment environment, via the one existing primitive.

    Delegates to `security.secrets.app_env()` so environment semantics never
    drift between the security posture and the telemetry — a diagnostics
    endpoint claiming "staging" while the cookie policy has decided "production"
    would be actively misleading. Imported lazily to keep this module free of an
    import-time dependency on the configuration layer.
    """
    try:
        from security.secrets import app_env

        return app_env()
    except Exception:  # pragma: no cover - defensive
        return os.environ.get("APP_ENV", UNKNOWN).strip().lower() or UNKNOWN


def started_at() -> str:
    """ISO-8601 UTC timestamp of process start."""
    return _START_ISO


def uptime_seconds() -> float:
    """Seconds since process start, from the monotonic clock."""
    return max(0.0, time.monotonic() - _START_MONOTONIC)


def python_version() -> str:
    return platform.python_version()


def build_info() -> Dict[str, str]:
    """Provenance of the artifact: version, commit, build time."""
    return {
        "version": service_version(),
        "revision": vcs_ref(),
        "build_date": build_date(),
    }


def process_info() -> Dict[str, object]:
    """Facts about this process. No configuration values.

    `WEB_CONCURRENCY` is included because "why is my in-memory rate-limit
    counter/metric wrong?" is nearly always "there are four workers and you are
    talking to one of them" — a thing worth being able to check in one request.
    """
    return {
        "pid": os.getpid(),
        "python_version": python_version(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "workers": os.environ.get("WEB_CONCURRENCY", "1"),
    }


def dependency_configuration() -> Dict[str, str]:
    """Which optional integrations are wired up — presence only, never values.

    Answers "is Redis actually configured in this environment?" without printing
    a URL that contains a password. `is_configured` is the existing
    presence-only accessor from the secrets registry; the value never leaves it.
    """
    try:
        from security.secrets import is_configured

        def state(name: str) -> str:
            return "configured" if is_configured(name) else "not_configured"

        return {
            "mongodb": state("MONGO_URL"),
            "redis": state("REDIS_URL"),
        }
    except Exception:  # pragma: no cover - defensive
        return {}


def runtime_info(*, lifecycle: Optional[str] = None) -> Dict[str, object]:
    """The full diagnostics payload served by `/api/diagnostics`.

    ``lifecycle`` is injected by the caller rather than imported here, so this
    module stays independent of `observability.health` (which imports nothing
    from it either) — two modules, no cycle, each testable alone.
    """
    payload: Dict[str, object] = {
        "service": service_name(),
        "environment": environment(),
        "build": build_info(),
        "process": process_info(),
        "started_at": started_at(),
        "uptime_seconds": round(uptime_seconds(), 3),
        "dependencies": dependency_configuration(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if lifecycle is not None:
        payload["lifecycle"] = lifecycle
    return payload

#!/bin/sh
# ==============================================================================
# StockAssist AI — Container health check (PH2.1)
#
# WHY THIS FILE EXISTS
# --------------------
# Docker considers a container "up" as long as PID 1 has not exited. That is a
# uselessly low bar for a web service: a process can be alive and still be
# deadlocked, still be starting, or have failed to bind its port. A health check
# converts "the process exists" into "the service actually answers", which is
# what every layer above the container consumes:
#
#   * `docker run`      → the `(healthy)` / `(unhealthy)` state in `docker ps`
#   * Docker Compose    → `depends_on: condition: service_healthy` (PH2.3)
#   * Swarm / ECS / K8s → rolling-deploy gating and automatic replacement
#
# Without it, a rolling deploy happily shifts 100% of traffic onto a revision
# that has not finished starting — or never will.
#
# EXIT CODE CONTRACT (Docker's, not ours — do not invent codes)
#   0 → healthy
#   1 → unhealthy
# Docker reserves every other status; anything non-zero other than 1 is
# undefined behaviour across runtimes. Every failure path below exits exactly 1.
# ==============================================================================

set -eu

# The probe targets the loopback interface, NOT the container's published port
# or hostname. A health check must measure the application, not the network path
# to it: going out through the host would make the result depend on port
# publishing, DNS and firewall rules, all of which are the orchestrator's
# problem and produce false negatives here.
HEALTHCHECK_HOST="${HEALTHCHECK_HOST:-127.0.0.1}"
HEALTHCHECK_PORT="${HEALTHCHECK_PORT:-${PORT:-8000}}"
# `/api` is chosen deliberately:
#   * it is unauthenticated — a probe holds no credentials;
#   * it is listed in _MIDDLEWARE_EXEMPT_PATHS (security/rate_limit.py), so a
#     30-second probe cadence can never consume the anonymous rate-limit budget
#     or, worse, get itself throttled into reporting a false "unhealthy";
#   * it touches no external dependency.
# That last point is the important design decision. This is a LIVENESS probe:
# it answers "should this container be restarted?". Deliberately NOT included is
# a MongoDB round-trip — if the database has a blip, a DB-coupled liveness probe
# marks every replica unhealthy simultaneously and the orchestrator restarts the
# entire fleet, turning a recoverable dependency outage into a full outage.
# Dependency health belongs in a separate readiness probe (PH2.10).
HEALTHCHECK_PATH="${HEALTHCHECK_PATH:-/api}"
HEALTHCHECK_TIMEOUT="${HEALTHCHECK_TIMEOUT:-4}"

# The probe is written in Python, using only the standard library, instead of
# `curl` or `wget`. Both would have to be apt-installed into the runtime image:
# more megabytes, more packages to patch, and a general-purpose HTTP client
# sitting on a production server — a well-known convenience for an attacker who
# lands a shell and wants to pull a second-stage payload. The interpreter is
# already present because it is the application runtime, so this costs nothing.
exec python - <<PYTHON_HEALTHCHECK
import json
import sys
import urllib.error
import urllib.request

URL = "http://${HEALTHCHECK_HOST}:${HEALTHCHECK_PORT}${HEALTHCHECK_PATH}"
TIMEOUT = ${HEALTHCHECK_TIMEOUT}


def unhealthy(reason: str) -> "None":
    # stdout/stderr from a health check is captured by the daemon and surfaced
    # by 'docker inspect' under .State.Health.Log. It is often the only forensic
    # trace of why a container was restarted at 03:00, so say something specific.
    print(f"unhealthy: {reason}", file=sys.stderr)
    sys.exit(1)


try:
    request = urllib.request.Request(URL, method="GET")
    request.add_header("User-Agent", "stockassist-healthcheck/1.0")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        status = response.status
        body = response.read(4096)
except urllib.error.HTTPError as exc:
    unhealthy(f"{URL} returned HTTP {exc.code}")
except urllib.error.URLError as exc:
    # Connection refused / DNS / TLS. During the start period this simply means
    # the server has not finished binding yet, which is why the HEALTHCHECK
    # instruction sets --start-period.
    unhealthy(f"cannot reach {URL}: {exc.reason}")
except Exception as exc:  # timeout, malformed response, anything unforeseen
    unhealthy(f"probe error against {URL}: {exc.__class__.__name__}: {exc}")

if status != 200:
    unhealthy(f"{URL} returned HTTP {status}")

# Verifying the payload — not just the status code — is what makes this a check
# of the *application* rather than of "something is listening on the port". A
# misrouted proxy or a stale process can return 200 with an unrelated body.
try:
    payload = json.loads(body)
except ValueError:
    unhealthy(f"{URL} returned a non-JSON body")

# A JSON document is not necessarily a JSON *object* — a misrouted proxy can
# return a 200 with a list or a bare string. Check the shape before indexing it,
# so the probe reports a diagnosable reason rather than an AttributeError
# traceback in the health log at the exact moment someone needs to read it.
if not isinstance(payload, dict):
    unhealthy(f"{URL} returned JSON of type {type(payload).__name__}, expected an object")

if payload.get("status") != "running":
    unhealthy(f"{URL} reported status={payload.get('status')!r}")

print(f"healthy: {URL} -> {payload.get('message', 'ok')} "
      f"(version {payload.get('version', 'unknown')})")
sys.exit(0)
PYTHON_HEALTHCHECK

#!/usr/bin/env bash
# ==============================================================================
# StockAssist AI — post-recovery verification (PH2.10)
#
# Usage:
#   ./scripts/dr/dr_verify.sh [--level quick|full] [--base-url URL]
#                             [--expect-version V] [--expect-manifest FILE]
#                             [--timeout SECONDS] [--quiet]
#
# WHY THIS FILE EXISTS
# --------------------
# Every runbook in docs/operations/DISASTER_RECOVERY.md ends with the same
# question — "is it actually back?" — and during an incident that question is
# answered badly. The service responds, someone says "looks fine", the incident
# is closed, and four hours later it turns out the backend came up against an
# empty database, or against the OLD image, or with Redis unreachable and every
# request silently served from the in-process fallback cache.
#
# The failure mode is not that people are careless. It is that "it works" is
# checked at the only layer that is easy to check — the front door — while the
# layers underneath are inferred. This script checks them in order and refuses
# to infer anything.
#
# WHY IT REPORTS EVERY CHECK INSTEAD OF STOPPING AT THE FIRST FAILURE
# -------------------------------------------------------------------
# A test suite should stop early; a diagnostic must not. During recovery the
# operator needs the SHAPE of the failure — "containers up, Mongo fine, Redis
# unreachable" is a different incident from "nothing is running" — and getting
# that shape one round-trip at a time, re-running after each fix, is how a
# fifteen-minute recovery becomes an hour. So every check runs, and checks whose
# PREREQUISITE failed report SKIP rather than a second, misleading failure.
#
# It is also a diagnosis tool, not only a verification tool: it is designed to
# be run against a broken system, which is why nothing here assumes the
# application is up.
#
# Exit codes:  0 everything passed   1 at least one check failed   2 usage error
#
# Full documentation: docs/operations/DISASTER_RECOVERY.md §Verification
# ==============================================================================
set -euo pipefail

DR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Sourced, not reimplemented. lib.sh already owns the four things this script
# needs and would otherwise duplicate: logging that separates human output from
# machine output, the non-`source` .env parser, and — the load-bearing one — the
# MongoDB transport that works in BOTH `docker` and `direct` mode. A verifier
# that reached MongoDB differently from the way the backup and restore scripts
# reach it could report healthy against a database the restore never touched.
# shellcheck source=../backup/lib.sh
. "${DR_DIR}/../backup/lib.sh"

LEVEL="full"
BASE_URL=""
EXPECT_VERSION=""
EXPECT_MANIFEST=""
HTTP_TIMEOUT="10"
QUIET=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --level)           LEVEL="${2:-}"; shift 2 ;;
        --base-url)        BASE_URL="${2:-}"; shift 2 ;;
        --expect-version)  EXPECT_VERSION="${2:-}"; shift 2 ;;
        --expect-manifest) EXPECT_MANIFEST="${2:-}"; shift 2 ;;
        --timeout)         HTTP_TIMEOUT="${2:-}"; shift 2 ;;
        --quiet)           QUIET=1; shift ;;
        -h|--help)         sed -n '2,40p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)                 usage_error "unknown option: $1 (try --help)" ;;
    esac
done

case "${LEVEL}" in
    quick|full) ;;
    *) usage_error "--level must be 'quick' or 'full' (got: ${LEVEL})" ;;
esac

bk_load_config

BASE_URL="${BASE_URL:-http://127.0.0.1:${BACKEND_PORT:-8000}}"
BASE_URL="${BASE_URL%/}"
DR_BACKEND_SERVICE="${DR_BACKEND_SERVICE:-backend}"

# ------------------------------------------------------------------------------
# Check accounting
#
# Three outcomes, not two. SKIP exists because "the application health check
# failed" is a lie when the container it lives in was never started — the
# operator would chase a health-endpoint problem that does not exist. A skipped
# check is still a non-pass, so it is reported, but it is never reported as a
# failure of the thing it did not get to test.
# ------------------------------------------------------------------------------
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
FAILED_NAMES=""

_report() {
    local status="$1" name="$2" detail="${3:-}"
    [[ ${QUIET} -eq 1 && "${status}" == "PASS" ]] && return 0
    if [[ -n "${detail}" ]]; then
        printf '  %-5s %-34s %s\n' "${status}" "${name}" "${detail}" >&2
    else
        printf '  %-5s %-34s\n' "${status}" "${name}" >&2
    fi
}

pass() { PASS_COUNT=$((PASS_COUNT + 1)); _report PASS "$1" "${2:-}"; }
skip() { SKIP_COUNT=$((SKIP_COUNT + 1)); _report SKIP "$1" "${2:-}"; }
fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_NAMES="${FAILED_NAMES}${FAILED_NAMES:+, }$1"
    _report FAIL "$1" "${2:-}"
}

section() { printf '\n%s\n' "$1" >&2; }

# Prerequisite gates. Each layer sets one, and the layer below reads it, so the
# dependency between layers is explicit rather than implied by ordering.
HOST_OK=0
CONTAINERS_OK=0
MONGO_OK=0
APP_OK=0

# ------------------------------------------------------------------------------
# HTTP helper
#
# `--max-time` matters more than it looks: without it a hung backend makes the
# VERIFIER hang, and a verification step that never returns during an incident
# gets killed and skipped, which is the same as not having one.
# ------------------------------------------------------------------------------
http_status() {
    local code
    # curl already prints `000` for a connection it never made, so the `|| code=`
    # is only for the case where curl itself is missing. Emitting a second `000`
    # from a `|| printf` here produces the memorable "HTTP 000000".
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time "${HTTP_TIMEOUT}" "$1" 2>/dev/null)" || code=""
    printf '%s' "${code:-000}"
}

# The token header is built as an ARRAY, not interpolated into the command line.
# `curl ${TOKEN:+-H "Authorization: Bearer $TOKEN"}` looks correct and is not:
# unquoted expansion splits the header into three arguments and curl receives a
# header of "Authorization:" with two stray URLs after it.
http_body() {
    local auth=()
    [[ -n "${DR_OPS_TOKEN:-}" ]] && auth=(-H "Authorization: Bearer ${DR_OPS_TOKEN}")
    curl -sS --max-time "${HTTP_TIMEOUT}" "${auth[@]+"${auth[@]}"}" "$1" 2>/dev/null || true
}

# ==============================================================================
# LAYER 1 — Host
#
# The bottom of the stack. If Docker is not running, everything above is noise.
# ==============================================================================
section "Layer 1 — host"

if [[ "${BACKUP_MODE}" == "direct" ]]; then
    # `direct` mode is a developer laptop or a managed database; there is no
    # local Docker stack to check, and pretending otherwise would produce three
    # failures that mean nothing.
    skip "docker daemon" "BACKUP_MODE=direct — no local container stack"
    skip "compose file parses"
    HOST_OK=1
elif ! command -v docker >/dev/null 2>&1; then
    fail "docker daemon" "docker command not found on this host"
elif ! docker info >/dev/null 2>&1; then
    fail "docker daemon" "docker is installed but the daemon is not reachable"
else
    pass "docker daemon"
    if docker compose -f "${BACKUP_COMPOSE_FILE}" config --quiet >/dev/null 2>&1; then
        pass "compose file parses" "$(basename "${BACKUP_COMPOSE_FILE}")"
        HOST_OK=1
    else
        # This is the single most common failure after a host rebuild: the
        # compose file is fine and the environment it interpolates from is not.
        fail "compose file parses" "interpolation failed — is .env present? (docker compose config)"
    fi
fi

# ==============================================================================
# LAYER 2 — Containers
#
# "Running" and "healthy" are different facts, and the gap between them is where
# recovery incidents live: a backend that restart-loops is `running` for a
# second at a time, and a `docker compose ps` glanced at during an incident
# shows it as up.
# ==============================================================================
section "Layer 2 — containers"

container_state() {
    docker compose -f "${BACKUP_COMPOSE_FILE}" ps -q "$1" 2>/dev/null | head -n 1
}

check_container() {
    local svc="$1" cid state health restarts
    cid="$(container_state "${svc}")"
    if [[ -z "${cid}" ]]; then
        fail "container: ${svc}" "not created"
        return 1
    fi
    state="$(docker inspect -f '{{.State.Status}}' "${cid}" 2>/dev/null || echo unknown)"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${cid}" 2>/dev/null || echo none)"
    restarts="$(docker inspect -f '{{.RestartCount}}' "${cid}" 2>/dev/null || echo 0)"

    if [[ "${state}" != "running" ]]; then
        fail "container: ${svc}" "state=${state}"
        return 1
    fi
    if [[ "${health}" == "unhealthy" ]]; then
        fail "container: ${svc}" "running but healthcheck reports unhealthy"
        return 1
    fi
    if [[ "${health}" == "starting" ]]; then
        # Not a failure. A container inside its start period during a recovery
        # is the expected state, and calling it a failure trains operators to
        # ignore this script's output.
        skip "container: ${svc}" "healthcheck still in its start period"
        return 1
    fi
    # A restart count above zero on a container that was just recreated means it
    # crashed and came back — the classic "it is up now" that is about to be
    # down again.
    if [[ "${restarts}" -gt 0 ]]; then
        pass "container: ${svc}" "running/${health} — WARNING: ${restarts} restart(s), check logs"
    else
        pass "container: ${svc}" "running/${health}"
    fi
    return 0
}

if [[ ${HOST_OK} -eq 0 ]]; then
    skip "containers" "layer 1 failed"
elif [[ "${BACKUP_MODE}" == "direct" ]]; then
    skip "containers" "BACKUP_MODE=direct"
    CONTAINERS_OK=1
else
    CONTAINERS_OK=1
    for svc in "${DR_BACKEND_SERVICE}" "${BACKUP_MONGO_SERVICE}" "${BACKUP_REDIS_SERVICE}"; do
        check_container "${svc}" || CONTAINERS_OK=0
    done
fi

# ==============================================================================
# LAYER 3 — Data
#
# The layer that gets skipped, and the only one that answers the question the
# business actually asked: is the DATA back? A restored stack serving an empty
# database passes every check above this one.
# ==============================================================================
section "Layer 3 — data"

if [[ "${LEVEL}" != "full" ]]; then
    skip "mongodb reachable" "--level quick"
    skip "mongodb has data"
    skip "redis reachable"
elif [[ ${CONTAINERS_OK} -eq 0 && "${BACKUP_MODE}" != "direct" ]]; then
    skip "mongodb reachable" "layer 2 failed"
    skip "mongodb has data"
    skip "redis reachable"
elif [[ "${BACKUP_MODE}" == "direct" && -z "${MONGO_URL:-}" ]]; then
    # Guarded rather than left to fail inside the library: bk_mongo_eval calls
    # die() when MONGO_URL is missing in direct mode, which would abort the
    # verifier mid-run and suppress the summary — the one output an operator is
    # reading during an incident.
    skip "mongodb reachable" "BACKUP_MODE=direct with no MONGO_URL set"
    skip "mongodb has data"
    skip "redis reachable"
else
    if PING_OUT="$(bk_mongo_eval 'db.runCommand({ping:1}).ok' 2>/dev/null | tr -d '\r' | tail -n 1)" \
        && [[ "${PING_OUT}" == "1" ]]; then
        pass "mongodb reachable"
        MONGO_OK=1
    else
        fail "mongodb reachable" "ping failed — credentials, or mongod not accepting connections"
    fi

    if [[ ${MONGO_OK} -eq 1 ]]; then
        COUNTS="$(bk_collection_counts "${MONGO_DB_NAME}" 2>/dev/null || true)"
        if [[ -z "${COUNTS}" || "${COUNTS}" == "{}" ]]; then
            # Deliberately a FAILURE and not a warning. An empty database after a
            # recovery is the single most expensive thing this script can catch,
            # and it is indistinguishable from a healthy system at every other
            # layer.
            fail "mongodb has data" "database '${MONGO_DB_NAME}' has no collections — did the restore run?"
        else
            NCOLL="$(printf '%s' "${COUNTS}" | tr ',' '\n' | grep -c ':' || true)"
            pass "mongodb has data" "${NCOLL} collection(s) in '${MONGO_DB_NAME}'"
        fi

        # The strongest available statement about fidelity: compare what is in
        # the database now against the per-collection counts captured at dump
        # time. `mongorestore` exits 0 on a restore that moved nothing; this is
        # what turns "the command succeeded" into "the data is there".
        if [[ -n "${EXPECT_MANIFEST}" ]]; then
            if [[ ! -r "${EXPECT_MANIFEST}" ]]; then
                fail "counts match manifest" "manifest not readable: ${EXPECT_MANIFEST}"
            elif ! command -v python3 >/dev/null 2>&1; then
                skip "counts match manifest" "python3 not available to compare"
            else
                CMP_OUT="$(printf '%s' "${COUNTS}" | python3 -c '
import json, sys
actual = json.load(sys.stdin)
expected = json.load(open(sys.argv[1])).get("collections") or {}
if not expected:
    print("MANIFEST_HAS_NO_BASELINE"); raise SystemExit(0)
bad = ["%s expected=%s actual=%s" % (c, n, actual.get(c, "MISSING"))
       for c, n in sorted(expected.items()) if actual.get(c) != n]
print("OK %d collection(s)" % len(expected) if not bad else "MISMATCH " + "; ".join(bad))
' "${EXPECT_MANIFEST}" 2>/dev/null || echo "COMPARE_FAILED")"
                case "${CMP_OUT}" in
                    OK\ *)                    pass "counts match manifest" "${CMP_OUT#OK }" ;;
                    MANIFEST_HAS_NO_BASELINE) skip "counts match manifest" "manifest carries no collection baseline" ;;
                    *)                        fail "counts match manifest" "${CMP_OUT}" ;;
                esac
            fi
        fi
    else
        skip "mongodb has data" "mongodb unreachable"
    fi

    # Redis is NOT a system of record (BACKUP_AND_RESTORE.md §6), so an empty
    # Redis is correct after a recovery and is not checked for content. What is
    # checked is that the backend will not spend the next hour serving from its
    # in-process fallback cache while every health check passes.
    REDIS_AUTH=()
    [[ -n "${REDIS_PASSWORD:-}" ]] && REDIS_AUTH=(-a "${REDIS_PASSWORD}")
    if [[ "${BACKUP_MODE}" == "direct" ]]; then
        skip "redis reachable" "BACKUP_MODE=direct"
    elif docker compose -f "${BACKUP_COMPOSE_FILE}" exec -T "${BACKUP_REDIS_SERVICE}" \
            redis-cli "${REDIS_AUTH[@]+"${REDIS_AUTH[@]}"}" --no-auth-warning PING 2>/dev/null | grep -q PONG; then
        pass "redis reachable"
    else
        fail "redis reachable" "PING failed — the app will run on its degraded in-process cache"
    fi
fi

# ==============================================================================
# LAYER 4 — Application
#
# The three probes are asked separately and mean different things (MONITORING.md
# §3). Asking only the aggregate endpoint is how "it returns 200" gets confused
# with "it is serving traffic correctly".
# ==============================================================================
section "Layer 4 — application"

LIVE_CODE="$(http_status "${BASE_URL}/api/health/live")"
if [[ "${LIVE_CODE}" == "200" ]]; then
    pass "liveness  /api/health/live"
    APP_OK=1
else
    fail "liveness  /api/health/live" "HTTP ${LIVE_CODE} — the process is not answering at all"
fi

if [[ ${APP_OK} -eq 1 ]]; then
    READY_CODE="$(http_status "${BASE_URL}/api/health/ready")"
    case "${READY_CODE}" in
        200) pass "readiness /api/health/ready" ;;
        503) fail "readiness /api/health/ready" "503 — alive but a dependency probe is failing (see layer 3)" ;;
        *)   fail "readiness /api/health/ready" "HTTP ${READY_CODE}" ;;
    esac

    STARTUP_CODE="$(http_status "${BASE_URL}/api/health/startup")"
    if [[ "${STARTUP_CODE}" == "200" ]]; then
        pass "startup  /api/health/startup"
    else
        skip "startup  /api/health/startup" "HTTP ${STARTUP_CODE} — still booting (index build?)"
    fi
else
    skip "readiness /api/health/ready" "liveness failed"
    skip "startup  /api/health/startup" "liveness failed"
fi

# WHICH BUILD IS ACTUALLY RUNNING.
#
# This is the check that makes a deployment rollback verifiable rather than
# assumed. `docker compose up -d` with an unchanged tag is a no-op that prints
# nothing alarming, so "I rolled back" and "the old code is running" are two
# claims, and only this one tests the second. /api/diagnostics is gated in
# production — export DR_OPS_TOKEN to read it.
if [[ ${APP_OK} -eq 1 ]]; then
    DIAG="$(http_body "${BASE_URL}/api/diagnostics")"
    # The build identity is NESTED under "build" — {"build":{"version":…,"revision":…}}
    # (server.py /api/diagnostics, backed by observability/runtime.py). Both
    # patterns therefore anchor on "build" and match the key INSIDE that object.
    #
    # PH2.12 fixed this. It previously read a flat "app_version"/"vcs_ref" pair
    # that the endpoint has never emitted, so RUNNING_VERSION was always empty
    # and this check could only ever SKIP — or, once --expect-version was
    # supplied, FAIL a perfectly good deployment while blaming DR_OPS_TOKEN. The
    # hermetic stub in test_disaster_recovery.py encoded the same wrong shape, so
    # the suite agreed with the bug instead of catching it. When a probe and its
    # test share an assumption, only the real endpoint can settle it.
    #
    # `[^}]*` keeps the match inside the build object, so the unrelated
    # "python_version" under "process" cannot be picked up instead.
    RUNNING_VERSION="$(printf '%s' "${DIAG}" | sed -n 's/.*"build"[[:space:]]*:[[:space:]]*{[^}]*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
    RUNNING_REF="$(printf '%s' "${DIAG}" | sed -n 's/.*"build"[[:space:]]*:[[:space:]]*{[^}]*"revision"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
    if [[ -z "${RUNNING_VERSION}" ]]; then
        if [[ -n "${EXPECT_VERSION}" ]]; then
            fail "running build" "could not read /api/diagnostics (gated? set DR_OPS_TOKEN)"
        else
            skip "running build" "diagnostics unreadable (gated? set DR_OPS_TOKEN)"
        fi
    elif [[ -n "${EXPECT_VERSION}" && "${RUNNING_VERSION}" != "${EXPECT_VERSION}" ]]; then
        fail "running build" "expected ${EXPECT_VERSION}, serving ${RUNNING_VERSION} (${RUNNING_REF:-no ref})"
    else
        pass "running build" "${RUNNING_VERSION} (${RUNNING_REF:-no ref})"
    fi
else
    skip "running build" "liveness failed"
fi

# ==============================================================================
# Summary
#
# One machine-readable line on stdout, everything else on stderr, so this can be
# both read by an operator and consumed by a wrapper.
# ==============================================================================
printf '\n' >&2
if [[ ${FAIL_COUNT} -eq 0 && ${SKIP_COUNT} -eq 0 ]]; then
    log "VERIFIED — ${PASS_COUNT} checks passed"
elif [[ ${FAIL_COUNT} -eq 0 ]]; then
    log "VERIFIED WITH SKIPS — ${PASS_COUNT} passed, ${SKIP_COUNT} skipped (re-run once the skipped preconditions hold)"
else
    err "NOT VERIFIED — ${FAIL_COUNT} failed: ${FAILED_NAMES}"
fi

printf 'dr_verify level=%s pass=%d fail=%d skip=%d\n' \
    "${LEVEL}" "${PASS_COUNT}" "${FAIL_COUNT}" "${SKIP_COUNT}"

[[ ${FAIL_COUNT} -eq 0 ]] || exit 1
exit 0

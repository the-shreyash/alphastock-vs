#!/bin/sh
# ==============================================================================
# StockAssist AI — Container entrypoint (PH2.1)
#
# WHY THIS FILE EXISTS
# --------------------
# A Dockerfile `CMD` can only start a process. A production container has to do
# three things before that: prove its configuration is sane, run any one-off
# pre-start work (migrations, cache warming), and then hand PID 1 to the server
# so the orchestrator's signals reach it. Encoding that in the Dockerfile would
# bake it into an immutable image layer — every change to startup behaviour
# would force a full rebuild, and the logic would be invisible to anyone reading
# the repository. Keeping it in a script makes startup behaviour reviewable,
# testable, and independently overridable.
#
# CONTRACT
# --------
#   * With no arguments  → validate configuration, run pre-start hooks, exec the
#                          API server.
#   * With arguments     → validate configuration, then exec those arguments.
#                          This is the escape hatch for one-shot jobs:
#                              docker run --rm <image> python scripts/seed_dev_admin.py
#                              docker run --rm <image> sh
#                          The same validated environment applies, so a one-shot
#                          job can never run against a broken configuration.
#
# POSIX `sh`, not bash: the runtime image is Debian slim, which ships `dash` as
# /bin/sh and no bash. Depending on bash would mean installing a shell into a
# production image purely for `[[ ]]` syntax — a package, a CVE surface, and a
# few megabytes for nothing.
# ==============================================================================

# -e  abort on any failing command — a half-initialised container must never
#     start serving traffic and report itself healthy.
# -u  abort on an unset variable — catches a typo'd env var name at startup
#     rather than silently substituting an empty string into a bind address.
# (`pipefail` is intentionally omitted: it is a bash/ksh extension, not POSIX,
#  and dash rejects it. No pipeline below depends on it.)
set -eu

readonly APP_MODULE="server:app"

log()  { printf '[entrypoint] %s\n' "$*"; }
fail() { printf '[entrypoint] FATAL: %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------------------
# 1. Runtime configuration
#
# Every value is an environment variable with a production-safe default. Nothing
# is read from a file and nothing is compiled into the image: the same image
# artifact runs in development, staging and production, and only the injected
# environment differs. This is the "build once, deploy many" rule — if an image
# has to be rebuilt to move between environments, it is not the artifact that
# was tested.
# ------------------------------------------------------------------------------
APP_ENV="${APP_ENV:-production}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
LOG_LEVEL="${LOG_LEVEL:-info}"
# Trust `X-Forwarded-For` / `X-Forwarded-Proto` only from the immediate peer by
# default. A container behind a load balancer needs proxy headers to see the
# real client IP (the rate limiter in security/rate_limit.py keys anonymous
# traffic on it) — but trusting them from *any* source lets a caller forge its
# own IP and walk straight through per-IP throttling. The reverse-proxy sprint
# sets this to the proxy's address on the compose network.
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
# Seconds uvicorn waits for in-flight requests to finish after SIGTERM. Must be
# comfortably below the orchestrator's kill grace period (Docker's default is
# 10s, Kubernetes' terminationGracePeriodSeconds default is 30s) so connections
# drain instead of being severed mid-response during a rolling deploy.
TIMEOUT_GRACEFUL_SHUTDOWN="${TIMEOUT_GRACEFUL_SHUTDOWN:-20}"
TIMEOUT_KEEP_ALIVE="${TIMEOUT_KEEP_ALIVE:-5}"

log "StockAssist AI backend — APP_ENV=${APP_ENV} uid=$(id -u) gid=$(id -g)"

# ------------------------------------------------------------------------------
# 2. Startup validation — fail fast, fail closed
#
# The container refuses to start rather than starting misconfigured. A server
# that boots with a missing JWT_SECRET and only fails on the first login is far
# more expensive to diagnose than one that never reports Ready: the orchestrator
# will halt the rollout and keep the previous healthy revision serving.
# ------------------------------------------------------------------------------

# 2a. Structural checks on values this script itself interpolates. These are
#     cheap and catch the classic "PORT=${PORT}" unexpanded-template mistake
#     before Python is even started.
case "${APP_ENV}" in
    development|staging|production) ;;
    *) fail "APP_ENV='${APP_ENV}' is not one of: development, staging, production." ;;
esac

case "${PORT}" in
    ''|*[!0-9]*) fail "PORT='${PORT}' is not a number." ;;
esac
[ "${PORT}" -ge 1 ] && [ "${PORT}" -le 65535 ] || fail "PORT='${PORT}' is out of range (1-65535)."

case "${WEB_CONCURRENCY}" in
    ''|*[!0-9]*) fail "WEB_CONCURRENCY='${WEB_CONCURRENCY}' is not a number." ;;
esac
[ "${WEB_CONCURRENCY}" -ge 1 ] || fail "WEB_CONCURRENCY must be at least 1."

# The application runs an in-process APScheduler (server.py startup, no env
# guard and no leader election), a heartbeat engine and an in-memory WebSocket
# connection registry. Every uvicorn worker is a separate OS process, so N
# workers means N schedulers firing the same jobs N times.
#
# PH3.10 CORRECTION — this block used to advise "scale with additional
# replicas, not workers", and that advice was dangerous. A replica is also a
# separate process running `setup_scheduler()`, so it duplicates the scheduler
# exactly as a worker does. The WebSocket half of multi-process was solved in
# PH2.7 (Redis pub/sub fan-out), which is probably why replicas looked safe —
# but the scheduler half was not. `trade_monitor` runs every 60 s during market
# hours and calls `trading_engine.run_cycle`, which places REAL BROKER EXIT
# ORDERS on stop-loss and target hits. Two schedulers means two exit orders for
# one position, in a live brokerage account, with real money.
#
# Until a single-leader scheduler exists, the deployment is ONE backend process:
# one worker, one replica. Warn loudly instead of silently capping — the
# operator stays in control, but cannot claim they were not told.
if [ "${WEB_CONCURRENCY}" -gt 1 ]; then
    log "WARNING: WEB_CONCURRENCY=${WEB_CONCURRENCY}. The in-process scheduler is NOT"
    log "WARNING: multi-process safe: each process runs the trade monitor, which"
    log "WARNING: places real broker exit orders. Duplicate orders are possible."
    log "WARNING: Run exactly ONE backend process (1 worker, 1 replica) until a"
    log "WARNING: single-leader scheduler ships. Do NOT scale with replicas either."
fi

# 2b. Full secret resolution + configuration validation, delegated to the
#     application's own authority on the subject: security/secrets.py (PH1.8,
#     extended in PH2.3). Reimplementing "which variables are required in which
#     environment" — or "where does a secret come from" — in shell would create a
#     second source of truth that drifts from the code that actually reads them.
#
#     This is a DRY RUN, in a throwaway Python process. It resolves every secret
#     source (Docker secrets, `<NAME>_FILE` pointers, plaintext env) and validates
#     the result, so an unreadable secret file or a missing variable is reported
#     here, cleanly and all at once. The server process performs the identical
#     resolution again for itself at import time (server.py), against the same
#     inputs — so this cannot mask a problem the real boot would hit. It exists so
#     the operator sees an aggregated report instead of a Python traceback
#     emerging from the middle of uvicorn's startup.
#
#     Note the resolved values never cross the process boundary: this child exits
#     before uvicorn starts, so nothing it read is added to the environment the
#     server inherits. That is deliberate — the validator is allowed to see
#     secrets, `docker inspect` is not.
log "Resolving secret sources and validating configuration for APP_ENV=${APP_ENV} ..."
python - <<'PYTHON_VALIDATION'
import sys

try:
    from security import secrets
except Exception as exc:  # import-time failure = broken image, not bad config
    print(f"[entrypoint] FATAL: cannot load the configuration validator: {exc}",
          file=sys.stderr)
    sys.exit(1)

try:
    report = secrets.validate_config()
except secrets.SecretValidationError as exc:
    # Already an aggregated, value-free, operator-facing message. Print it as-is
    # and suppress the traceback: the stack is noise, the message is the signal.
    print(str(exc), file=sys.stderr)
    sys.exit(1)

for warning in report.warnings:
    print(f"[entrypoint] warning: {warning}", file=sys.stderr)

# Names only — this line answers "which secrets are still plaintext?" without
# printing a single value.
file_backed = report.from_file()
if file_backed:
    print(f"[entrypoint] file-backed secrets: {', '.join(file_backed)}")
print(f"[entrypoint] {report.summary_line()}")
PYTHON_VALIDATION
log "Configuration OK."

# ------------------------------------------------------------------------------
# 3. Pre-start hooks
#
# A working extension point rather than a placeholder. Any executable dropped
# into docker/pre-start.d/ runs, in lexical order, after validation and before
# the server starts. This is the same convention used by the official postgres
# (`docker-entrypoint-initdb.d`) and nginx (`docker-entrypoint.d`) images, and
# it exists so that future one-off startup work — schema migrations, index
# backfills, cache warming — can be added WITHOUT editing this file or the
# Dockerfile.
#
# Note on migrations specifically: MongoDB index creation currently happens in
# the FastAPI `startup` event (server.py), which is safe and idempotent. When a
# real migration runner arrives it belongs here (or, better for multi-replica
# deployments, as a separate one-shot job using the `exec "$@"` contract above,
# so N replicas do not race to migrate the same database).
#
# `set -e` applies: a failing hook aborts startup. That is deliberate — a
# migration that half-applied must not be followed by a server that starts
# serving against a half-migrated database.
# ------------------------------------------------------------------------------
HOOK_DIR="$(dirname "$0")/pre-start.d"
if [ -d "${HOOK_DIR}" ]; then
    for hook in "${HOOK_DIR}"/*; do
        [ -f "${hook}" ] && [ -x "${hook}" ] || continue
        log "Running pre-start hook: $(basename "${hook}")"
        "${hook}"
    done
fi

# ------------------------------------------------------------------------------
# 4. Hand off
#
# `exec` REPLACES this shell with the target process, so the server inherits
# PID 1 directly. This is the single most important line in the file:
#
#   * Without `exec`, the shell stays PID 1 and the server is its child. `sh`
#     does not forward signals to children, so `docker stop` / a Kubernetes pod
#     eviction would deliver SIGTERM to the shell, the server would never learn
#     it should drain, and 10 seconds later SIGKILL would sever every in-flight
#     request and open WebSocket. Every rolling deploy would drop traffic.
#   * With `exec`, uvicorn receives SIGTERM itself, stops accepting new
#     connections, lets in-flight requests finish within
#     TIMEOUT_GRACEFUL_SHUTDOWN, runs the FastAPI `shutdown` event (which stops
#     the scheduler, closes broker WebSocket streams and the Mongo client), and
#     exits 0.
#
# No init/reaper (tini, dumb-init) is needed: uvicorn reaps its own workers, and
# with `exec` there is no intermediate shell to leave zombies behind.
# ------------------------------------------------------------------------------
if [ "$#" -gt 0 ]; then
    log "Executing override command: $*"
    exec "$@"
fi

log "Starting uvicorn on ${HOST}:${PORT} (workers=${WEB_CONCURRENCY}, log-level=${LOG_LEVEL})"

# `--reload` is deliberately absent and must never be added here: it runs a file
# watcher and a supervisor process, breaks signal handling, and re-executes code
# from disk — none of which belongs in an immutable production image.
# `--no-server-header` suppresses the `server: uvicorn` response header, which
# otherwise advertises the exact stack to every caller (PH1.4 header policy).
exec python -m uvicorn "${APP_MODULE}" \
    --host "${HOST}" \
    --port "${PORT}" \
    --workers "${WEB_CONCURRENCY}" \
    --log-level "${LOG_LEVEL}" \
    --proxy-headers \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS}" \
    --timeout-graceful-shutdown "${TIMEOUT_GRACEFUL_SHUTDOWN}" \
    --timeout-keep-alive "${TIMEOUT_KEEP_ALIVE}" \
    --no-server-header

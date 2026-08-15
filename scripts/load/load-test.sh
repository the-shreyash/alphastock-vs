#!/usr/bin/env bash
# ==============================================================================
# StockAssist AI — PH3.5 load-test runner
#
# One command per kind of run, so that a load test is a workflow an engineer
# invokes deliberately rather than a pile of flags they have to remember:
#
#     scripts/load/load-test.sh smoke        # 5 VUs, 40s   — is the stack sane?
#     scripts/load/load-test.sh baseline     # 10 VUs, 85s
#     scripts/load/load-test.sh moderate     # 25 VUs, 120s
#     scripts/load/load-test.sh high         # 50 VUs, 165s
#     scripts/load/load-test.sh stress       # 100 VUs, 180s
#     scripts/load/load-test.sh saturation   # find the read-path throughput ceiling
#     scripts/load/load-test.sh auth         # authentication throughput
#     scripts/load/load-test.sh ratelimit    # rate-limit validation
#     scripts/load/load-test.sh websocket    # real-time connection load
#     scripts/load/load-test.sh failure      # controlled provider failure injection
#
#     scripts/load/load-test.sh up           # start mocks + redis, seed fixtures
#     scripts/load/load-test.sh down         # stop mocks + redis
#     scripts/load/load-test.sh preflight    # verify the environment, run nothing
#
# Every run captures a server-side metric snapshot before and after (CPU,
# memory, MongoDB, Redis, provider mocks) and writes the k6 summary, both
# snapshots and the computed delta into scripts/load/results/<run-id>/.
#
# ⚠  This starts and drives a NON-PRODUCTION stack only. `preflight` refuses to
#    proceed unless the target reports APP_ENV=staging AND a seeded account can
#    authenticate — the second check is the one that matters, because it is the
#    only way to prove the server is on the load database rather than merely
#    claiming to be. PH3.5 setup lost twenty minutes to exactly that confusion:
#    `backend/server.py` calls `load_dotenv(..., override=True)`, so a developer
#    `.env` silently wins over the exported environment.
#
# ⚠  BASH 3.2 COMPATIBLE, matching scripts/backup/lib.sh — macOS still ships
#    bash 3.2 as /bin/bash. No associative arrays, no `mapfile`, no `${var^^}`.
#
# Full documentation: docs/performance/PH3.5_LOAD_TEST_CERTIFICATION.md
# ==============================================================================
set -uo pipefail

LOAD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${LOAD_DIR}/../.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
ENV_FILE="${LOAD_DIR}/env/loadtest.env"
RESULTS_DIR="${LOAD_DIR}/results"
PID_DIR="${LOAD_DIR}/.run"

REDIS_CONTAINER="stockassist-loadtest-redis"
MARKET_MOCK_PORT="${MARKET_MOCK_PORT:-9020}"
AI_MOCK_PORT="${AI_MOCK_PORT:-9030}"
LOAD_BASE_URL="${LOAD_BASE_URL:-http://127.0.0.1:8000}"

# ------------------------------------------------------------------------------
# Logging — everything to stderr except values a caller may want to capture.
# ------------------------------------------------------------------------------
_ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
info()  { printf '[%s] %s\n' "$(_ts)" "$*" >&2; }
warn()  { printf '[%s] WARN  %s\n' "$(_ts)" "$*" >&2; }
fatal() { printf '[%s] FATAL %s\n' "$(_ts)" "$*" >&2; exit 1; }

load_env() {
  [ -f "${ENV_FILE}" ] || fatal "missing ${ENV_FILE}"
  # shellcheck disable=SC1090
  set -a; . "${ENV_FILE}"; set +a
  export LOAD_BASE_URL
}

py() {
  # The backend virtualenv, because the probe and the seeder need pymongo and
  # the application's own password hasher.
  if [ -x "${BACKEND_DIR}/venv/bin/python" ]; then
    "${BACKEND_DIR}/venv/bin/python" "$@"
  else
    python3 "$@"
  fi
}

# ------------------------------------------------------------------------------
# Environment lifecycle
# ------------------------------------------------------------------------------
start_redis() {
  if docker ps --format '{{.Names}}' | grep -q "^${REDIS_CONTAINER}$"; then
    info "redis already running (${REDIS_CONTAINER})"
    return 0
  fi
  docker rm -f "${REDIS_CONTAINER}" >/dev/null 2>&1 || true
  # maxmemory + an eviction policy on purpose: an unbounded cache under load is
  # a pending OOM, and `redis_server_evicted_keys_total` only means anything if
  # eviction is actually possible.
  docker run -d --name "${REDIS_CONTAINER}" -p 6379:6379 --memory 512m \
    redis:7.2-alpine redis-server \
    --maxmemory 256mb --maxmemory-policy allkeys-lru --save '' >/dev/null \
    || fatal "could not start redis"
  info "redis started (${REDIS_CONTAINER}, maxmemory 256mb, allkeys-lru)"
}

start_mocks() {
  mkdir -p "${PID_DIR}"
  _start_mock market_provider "${MARKET_MOCK_PORT}"
  _start_mock ai_provider "${AI_MOCK_PORT}"
}

_start_mock() {
  name="$1"; port="$2"
  if curl -sf "http://127.0.0.1:${port}/__health" >/dev/null 2>&1; then
    info "${name} mock already listening on ${port}"
    return 0
  fi
  nohup python3 "${LOAD_DIR}/mocks/${name}.py" --port "${port}" \
    > "${PID_DIR}/${name}.log" 2>&1 &
  echo $! > "${PID_DIR}/${name}.pid"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf "http://127.0.0.1:${port}/__health" >/dev/null 2>&1; then
      info "${name} mock listening on ${port}"
      return 0
    fi
    sleep 0.5
  done
  fatal "${name} mock failed to start — see ${PID_DIR}/${name}.log"
}

stop_mocks() {
  for name in market_provider ai_provider; do
    if [ -f "${PID_DIR}/${name}.pid" ]; then
      kill "$(cat "${PID_DIR}/${name}.pid")" 2>/dev/null || true
      rm -f "${PID_DIR}/${name}.pid"
      info "${name} mock stopped"
    fi
  done
}

seed() {
  info "seeding synthetic fixtures into ${DB_NAME}"
  ( cd "${BACKEND_DIR}" && py scripts/seed_load_fixtures.py --users "${LOAD_USERS:-250}" ) \
    || fatal "seeding failed"
}

# ------------------------------------------------------------------------------
# Preflight
#
# Six checks. Each one exists because its absence produced a wrong or wasted
# run during PH3.5 development.
# ------------------------------------------------------------------------------
preflight() {
  command -v k6 >/dev/null 2>&1 || fatal "k6 not installed — 'brew install k6'"
  command -v docker >/dev/null 2>&1 || fatal "docker not available"

  curl -sf "${LOAD_BASE_URL}/api/health/live" >/dev/null \
    || fatal "backend not reachable at ${LOAD_BASE_URL} — start it with the load environment"

  ready="$(curl -s "${LOAD_BASE_URL}/api/health/ready")"
  case "${ready}" in
    *'"status":"ready"'*) : ;;
    *) fatal "backend is not ready: ${ready}" ;;
  esac

  # The environment must be `staging`. A run against `development` or `testing`
  # would be measured with LENIENT_ENVIRONMENTS security configuration and
  # would not describe the deployment it is meant to predict.
  diag="$(curl -s -H "Authorization: Bearer ${METRICS_TOKEN:-}" "${LOAD_BASE_URL}/api/diagnostics")"
  case "${diag}" in
    *'"environment": "staging"'*|*'"environment":"staging"'*) : ;;
    *) fatal "target does not report APP_ENV=staging — refusing to run. Got: $(printf '%s' "${diag}" | head -c 200)" ;;
  esac

  # THE check. Everything above can pass while the server is quietly attached to
  # the developer's database; only a seeded credential can prove otherwise.
  [ -f "${LOAD_DIR}/fixtures.json" ] || fatal "no fixtures.json — run '$0 up' first"
  pw="$(py -c 'import json,sys;print(json.load(open(sys.argv[1]))["password"])' "${LOAD_DIR}/fixtures.json")"
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "${LOAD_BASE_URL}/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"loaduser1@loadtest.invalid\",\"password\":\"${pw}\"}")"
  [ "${code}" = "200" ] || fatal \
    "a seeded account could not authenticate (HTTP ${code}). The backend is almost
    certainly attached to a different database than the one that was seeded —
    check that PYTHON_DOTENV_DISABLED=1 was exported before uvicorn started."

  # Providers must be mocked. A run that reaches real Yahoo or real Anthropic is
  # a brief violation (§14, §15), not merely an inaccurate measurement.
  curl -sf "http://127.0.0.1:${MARKET_MOCK_PORT}/__health" >/dev/null \
    || fatal "market-data mock not running — external provider would receive load"
  curl -sf "http://127.0.0.1:${AI_MOCK_PORT}/__health" >/dev/null \
    || fatal "AI mock not running — external provider would receive load"

  info "preflight OK — staging, seeded database reachable, both providers mocked"
}

# ------------------------------------------------------------------------------
# Runs
# ------------------------------------------------------------------------------
run_k6() {
  script="$1"; label="$2"; shift 2

  run_id="$(date -u '+%Y%m%dT%H%M%SZ')-${label}"
  out="${RESULTS_DIR}/${run_id}"
  mkdir -p "${out}"

  info "=== ${label} → ${out} ==="
  ( cd "${BACKEND_DIR}" && py scripts/load_metrics_probe.py --out "${out}/before.json" ) >/dev/null

  LOAD_SUMMARY_PATH="${out}/k6-summary.json" \
    k6 run --summary-trend-stats='min,med,avg,p(90),p(95),p(99),max' \
      "$@" "${LOAD_DIR}/k6/${script}" 2>&1 | tee "${out}/k6.log"
  k6_status=${PIPESTATUS[0]}

  ( cd "${BACKEND_DIR}" && py scripts/load_metrics_probe.py --out "${out}/after.json" ) >/dev/null
  ( cd "${BACKEND_DIR}" && py scripts/load_metrics_probe.py \
      --delta "${out}/before.json" "${out}/after.json" ) > "${out}/delta.json"

  info "server-side delta:"
  ( cd "${BACKEND_DIR}" && py scripts/load_metrics_probe.py --summary \
      --delta "${out}/before.json" "${out}/after.json" ) >&2

  info "artifacts: ${out}"
  return ${k6_status}
}

# Controlled failure injection (brief §17). Each phase changes ONE thing, and
# the baseline phase runs first so every later number has something to be
# compared against on the same stack in the same minute.
run_failure() {
  mc="http://127.0.0.1:${MARKET_MOCK_PORT}/__control"
  ac="http://127.0.0.1:${AI_MOCK_PORT}/__control"
  reset() {
    curl -sf -X POST "${mc}/reset" >/dev/null || true
    curl -sf -X POST "${ac}/reset" >/dev/null || true
  }
  trap reset EXIT

  info "--- phase 0: clean baseline ---"
  reset
  run_k6 scenarios.js failure-0-baseline -e LOAD_STAGE=baseline

  info "--- phase 1: market provider slow (800ms) ---"
  curl -sf -X POST "${mc}" -H 'Content-Type: application/json' -d '{"latency_ms":800}' >/dev/null
  run_k6 scenarios.js failure-1-slow-market -e LOAD_STAGE=baseline

  info "--- phase 2: market provider failing (30% 503) ---"
  reset
  curl -sf -X POST "${mc}" -H 'Content-Type: application/json' -d '{"error_rate":0.3}' >/dev/null
  run_k6 scenarios.js failure-2-market-errors -e LOAD_STAGE=baseline

  info "--- phase 3: market provider timing out (10%) ---"
  reset
  curl -sf -X POST "${mc}" -H 'Content-Type: application/json' -d '{"timeout_rate":0.1,"timeout_sleep_s":30}' >/dev/null
  run_k6 scenarios.js failure-3-market-timeouts -e LOAD_STAGE=baseline

  info "--- phase 4: AI provider slow (6s) + rate limiting (20%) ---"
  reset
  curl -sf -X POST "${ac}" -H 'Content-Type: application/json' -d '{"latency_ms":6000,"rate_limit_rate":0.2}' >/dev/null
  run_k6 scenarios.js failure-4-ai-degraded -e LOAD_STAGE=baseline

  info "--- phase 5: recovery — everything healthy again ---"
  reset
  run_k6 scenarios.js failure-5-recovery -e LOAD_STAGE=baseline
}

usage() {
  sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 2
}

# ------------------------------------------------------------------------------
main() {
  cmd="${1:-}"; [ -n "${cmd}" ] || usage
  shift || true
  load_env
  mkdir -p "${RESULTS_DIR}" "${PID_DIR}"

  case "${cmd}" in
    up)
      start_redis; start_mocks; seed
      info "environment ready. Start the backend with:"
      info "  set -a; . scripts/load/env/loadtest.env; set +a"
      info "  cd backend && source venv/bin/activate && uvicorn server:app --host 127.0.0.1 --port 8000"
      ;;
    down)
      stop_mocks
      docker rm -f "${REDIS_CONTAINER}" >/dev/null 2>&1 && info "redis stopped" || true
      ;;
    preflight)  preflight ;;
    smoke|baseline|moderate|high|stress)
      preflight; run_k6 scenarios.js "${cmd}" -e "LOAD_STAGE=${cmd}" "$@" ;;
    auth)       preflight; run_k6 auth.js auth "$@" ;;
    saturation) preflight; run_k6 saturation.js saturation "$@" ;;
    ratelimit)  preflight; run_k6 ratelimit.js ratelimit "$@" ;;
    websocket)  preflight; run_k6 websocket.js websocket "$@" ;;
    failure)    preflight; run_failure ;;
    *)          usage ;;
  esac
}

main "$@"

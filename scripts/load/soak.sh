#!/usr/bin/env bash
# ==============================================================================
# StockAssist AI — PH3.6 sustained-soak runner
#
# WHY THIS EXISTS ALONGSIDE load-test.sh
# --------------------------------------
# PH3.5's runs are minutes long, which is the right length for throughput and
# latency and the wrong length for everything PH3.6 is about. A leak is a shape
# — a count that only ever rises — and three minutes of any shape looks flat.
#
# Two things separate this from `load-test.sh saturation`:
#
#   1. **Duration.** A flat arrival rate held for tens of minutes, not a ramp
#      held for thirty seconds per step.
#   2. **What is sampled.** `load-test.sh` snapshots server metrics BEFORE and
#      AFTER a run, which cannot distinguish "grew and came back" from "never
#      grew". This samples `/api/metrics` on a fixed cadence for the whole run
#      and writes one CSV row per sample, so the SERIES is the artefact — the
#      only form in which a ratchet is visible.
#
# The columns that matter are the PH3.6 gauges (`websocket_tracked_users`,
# `app_cache_entries`, `background_tasks_running`, `event_bus_subscribers`),
# not RSS. RSS is sampled and reported because operators watch it, but Python
# returns freed arenas to its allocator rather than to the OS, so a flat RSS is
# not evidence of no leak and a rising one is not evidence of a leak. The counts
# are the evidence.
#
# USAGE
#     scripts/load/soak.sh http  [seconds] [rps]     # default 1800s @ 150 rps
#     scripts/load/soak.sh ws    [seconds]           # WebSocket connect/churn
#     scripts/load/soak.sh sample [seconds] [label]  # sample only, drive nothing
#
# Requires the load environment already up (`scripts/load/load-test.sh up`) and
# a backend started against `env/loadtest.env`. `load-test.sh preflight` runs
# first and refuses to proceed against anything that is not the staging/load
# database — the same guard, not a re-implementation of it.
#
# BASH 3.2 COMPATIBLE, matching load-test.sh.
# ==============================================================================
set -uo pipefail

LOAD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${LOAD_DIR}/../.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
ENV_FILE="${LOAD_DIR}/env/loadtest.env"
RESULTS_DIR="${LOAD_DIR}/results"
LOAD_BASE_URL="${LOAD_BASE_URL:-http://127.0.0.1:8000}"
SAMPLE_INTERVAL="${SAMPLE_INTERVAL:-30}"

_ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
info() { printf '[%s] %s\n' "$(_ts)" "$*" >&2; }
fatal() { printf '[%s] FATAL %s\n' "$(_ts)" "$*" >&2; exit 1; }

# The gauges written to the CSV, in order. Plain metric names only — the
# labelled `app_cache_entries` series are extracted separately below.
GAUGES="process_resident_memory_bytes process_open_fds websocket_connections websocket_tracked_users websocket_channel_subscriptions background_tasks_running event_bus_subscribers http_requests_in_flight"
CACHES="ai_chat_context market_memory_fallback portfolio_throttle trade_throttle"

# ------------------------------------------------------------------------------
# Sampler
#
# One `curl` per interval against /api/metrics, parsed with awk. Deliberately
# not the Python probe `load-test.sh` uses: that one opens its own Mongo and
# Redis clients, and a sampler that adds a connection every 30 seconds would be
# contributing to the very number it is measuring.
# ------------------------------------------------------------------------------
sample_once() {
  csv="$1"
  body="$(curl -s --max-time 5 -H "Authorization: Bearer ${METRICS_TOKEN:-}" "${LOAD_BASE_URL}/api/metrics")"
  [ -n "${body}" ] || return 0

  row="$(_ts)"
  for name in ${GAUGES}; do
    value="$(printf '%s\n' "${body}" | awk -v n="${name}" '$1 == n { print $2; exit }')"
    row="${row},${value:-}"
  done
  for cache in ${CACHES}; do
    value="$(printf '%s\n' "${body}" \
      | awk -v k="app_cache_entries{cache=\"${cache}\"}" '$1 == k { print $2; exit }')"
    row="${row},${value:-}"
  done
  printf '%s\n' "${row}" >> "${csv}"
}

start_sampler() {
  csv="$1"; duration="$2"
  header="timestamp"
  for name in ${GAUGES}; do header="${header},${name}"; done
  for cache in ${CACHES}; do header="${header},cache_${cache}"; done
  printf '%s\n' "${header}" > "${csv}"

  # `> /dev/null 2>&1` is NOT tidiness — it is what makes this function usable.
  #
  # The caller runs `pid="$(start_sampler ...)"`. Command substitution reads the
  # subshell's stdout until EOF, and a background child that INHERITS stdout
  # holds that pipe open for its whole life. Without the redirect the caller
  # blocks for the entire sampling window and the load generator it was supposed
  # to start never starts — which is exactly what happened on this sprint's first
  # soak attempt: samples appeared on schedule, k6 never ran, and the run looked
  # healthy for six minutes while measuring an idle server.
  (
    elapsed=0
    while [ "${elapsed}" -lt "${duration}" ]; do
      sample_once "${csv}"
      sleep "${SAMPLE_INTERVAL}"
      elapsed=$((elapsed + SAMPLE_INTERVAL))
    done
    sample_once "${csv}"
  ) > /dev/null 2>&1 &
  echo $!
}

# ------------------------------------------------------------------------------
# Verdict
#
# Compares the FIRST and LAST sample of every count column and reports the
# delta. This is intentionally arithmetic rather than statistical: with one
# sample every 30 seconds over half an hour there are ~60 points, and the
# question — "did this come back down?" — does not need a regression line.
# ------------------------------------------------------------------------------
summarize() {
  csv="$1"
  awk -F, '
    NR == 1 { for (i = 2; i <= NF; i++) name[i] = $i; cols = NF; next }
    NR == 2 { for (i = 2; i <= cols; i++) { first[i] = $i; peak[i] = $i } ; rows++ ; next }
    {
      rows++
      for (i = 2; i <= cols; i++) {
        last[i] = $i
        if ($i + 0 > peak[i] + 0) peak[i] = $i
      }
    }
    END {
      if (rows < 2) { print "not enough samples"; exit }
      printf "%-38s %12s %12s %12s %10s\n", "series", "first", "peak", "last", "delta"
      for (i = 2; i <= cols; i++) {
        d = last[i] - first[i]
        printf "%-38s %12s %12s %12s %+10d\n", name[i], first[i], peak[i], last[i], d
      }
    }
  ' "${csv}"
}

preflight() { "${LOAD_DIR}/load-test.sh" preflight || fatal "preflight failed"; }

load_env() {
  [ -f "${ENV_FILE}" ] || fatal "missing ${ENV_FILE}"
  # shellcheck disable=SC1090
  set -a; . "${ENV_FILE}"; set +a
}

# ------------------------------------------------------------------------------
# Runs
# ------------------------------------------------------------------------------
soak_http() {
  duration="${1:-1800}"; rate="${2:-150}"
  run_id="$(date -u '+%Y%m%dT%H%M%SZ')-soak-http"
  out="${RESULTS_DIR}/${run_id}"; mkdir -p "${out}"
  csv="${out}/samples.csv"

  info "=== HTTP soak: ${rate} rps for ${duration}s → ${out} ==="
  sampler_pid="$(start_sampler "${csv}" $((duration + 120)))"

  k6 run \
    -e LOAD_BASE_URL="${LOAD_BASE_URL}" \
    -e SAT_RATE="${rate}" \
    -e SAT_DURATION="${duration}s" \
    -e LOAD_SUMMARY_PATH="${out}/k6-summary.json" \
    "${LOAD_DIR}/k6/saturation.js" > "${out}/k6.log" 2>&1
  k6_status=$?

  # One more minute of sampling with NO load: the settle phase. A count that
  # only falls after the load stops is the difference between a working cache
  # and a leak, and it is invisible without this window.
  info "load finished (k6 exit ${k6_status}); sampling 60s of idle settle"
  sleep 60
  kill "${sampler_pid}" 2>/dev/null

  summarize "${csv}" | tee "${out}/verdict.txt"
  info "artefacts in ${out}"
}

soak_ws() {
  duration="${1:-900}"
  run_id="$(date -u '+%Y%m%dT%H%M%SZ')-soak-ws"
  out="${RESULTS_DIR}/${run_id}"; mkdir -p "${out}"
  csv="${out}/samples.csv"

  info "=== WebSocket churn soak: ${duration}s → ${out} ==="
  sampler_pid="$(start_sampler "${csv}" $((duration + 120)))"

  # Churn mode, not hold mode. Holding 150 idle sockets proves very little;
  # repeatedly connecting and dropping them is what exercises the reap path and
  # the per-user map that PH3.6 found retaining a key per connection.
  k6 run \
    -e LOAD_BASE_URL="${LOAD_BASE_URL}" \
    -e WS_CONNECTIONS="${WS_CONNECTIONS:-100}" \
    -e WS_HOLD="${duration}" \
    -e WS_CHURN=1 \
    -e WS_CHURN_HOLD_MS="${WS_CHURN_HOLD_MS:-2000}" \
    -e LOAD_SUMMARY_PATH="${out}/k6-summary.json" \
    "${LOAD_DIR}/k6/websocket.js" > "${out}/k6.log" 2>&1
  k6_status=$?

  info "churn finished (k6 exit ${k6_status}); sampling 60s of idle settle"
  sleep 60
  kill "${sampler_pid}" 2>/dev/null

  summarize "${csv}" | tee "${out}/verdict.txt"
  info "artefacts in ${out}"
}

sample_only() {
  duration="${1:-300}"; label="${2:-idle}"
  run_id="$(date -u '+%Y%m%dT%H%M%SZ')-soak-${label}"
  out="${RESULTS_DIR}/${run_id}"; mkdir -p "${out}"
  csv="${out}/samples.csv"
  info "=== sampling only (${label}) for ${duration}s → ${out} ==="
  sampler_pid="$(start_sampler "${csv}" "${duration}")"
  sleep "${duration}"
  kill "${sampler_pid}" 2>/dev/null
  summarize "${csv}" | tee "${out}/verdict.txt"
}

main() {
  load_env
  case "${1:-}" in
    http)   preflight; shift; soak_http "$@" ;;
    ws)     preflight; shift; soak_ws "$@" ;;
    sample) shift; sample_only "$@" ;;
    *) printf 'usage: %s {http|ws|sample} [seconds] [rps|label]\n' "$0" >&2; exit 2 ;;
  esac
}

main "$@"

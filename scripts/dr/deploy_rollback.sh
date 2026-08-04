#!/usr/bin/env bash
# ==============================================================================
# StockAssist AI — deployment ledger & rollback (PH2.10)
#
# Usage:
#   ./scripts/dr/deploy_rollback.sh record  [--tag TAG] [--note "..."]
#   ./scripts/dr/deploy_rollback.sh list    [-n COUNT]
#   ./scripts/dr/deploy_rollback.sh current
#   ./scripts/dr/deploy_rollback.sh rollback (--to TAG | --previous)
#                                            [--pull] [--yes] [--dry-run]
#                                            [--no-verify] [--timeout SECONDS]
#
# WHY THIS FILE EXISTS
# --------------------
# "Roll back the deployment" is one sentence and four separate facts, and a
# deployment that cannot answer all four cannot be rolled back:
#
#   1. WHICH version is running right now?
#   2. WHICH version was running before it?
#   3. Is that previous image STILL ON THIS HOST?
#   4. Did the rollback actually take effect?
#
# There is no CD pipeline and no image registry in this deployment yet
# (PH2.7b), so nothing answers 1 and 2 by itself — the tag lives in a `.env`
# file that an operator edits by hand, and `docker compose up -d` with an
# unchanged tag is a silent no-op. That is the gap this script closes: an
# append-only ledger for 1 and 2, a hard precondition check for 3, and a
# verified apply for 4.
#
# WHY THE LEDGER LIVES UNDER $BACKUP_ROOT
# ---------------------------------------
# Because the question "what was running before the incident?" is asked most
# urgently in the incident where the host is gone. $BACKUP_ROOT is the one
# directory this deployment already copies off the host (BACKUP_AND_RESTORE.md
# §4), so the ledger rides along with the backups it belongs to. A deployment
# history stored only on the machine it describes is a history that disappears
# exactly when it is needed.
#
# WHAT THIS SCRIPT DELIBERATELY DOES NOT DO
# -----------------------------------------
# * It does not `git checkout`. Moving an operator's working tree underneath
#   them during an incident, while they have half a diagnosis in their
#   scrollback, is a clever thing to do once. The ledger RECORDS the commit and
#   the script PRINTS the exact command; a human runs it.
# * It does not reverse data migrations. Rolling an image back over a database
#   the newer version has already migrated can be strictly worse than the
#   failure being rolled back — the old code meets a schema it has never seen.
#   The script asks about this before it touches anything; it cannot decide it.
#
# Exit codes:  0 success   1 failure (including a rollback that was reverted)
#              2 usage error
#
# Full documentation: docs/operations/DISASTER_RECOVERY.md §Deployment rollback
# ==============================================================================
set -euo pipefail

DR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../backup/lib.sh
. "${DR_DIR}/../backup/lib.sh"

COMMAND="${1:-}"
[[ -n "${COMMAND}" ]] || usage_error "no command given (record | list | current | rollback) — try --help"
case "${COMMAND}" in
    -h|--help) sed -n '2,55p' "${BASH_SOURCE[0]}"; exit 0 ;;
esac
shift

TARGET_TAG=""
USE_PREVIOUS=0
DO_PULL=0
ASSUME_YES=0
DRY_RUN=0
DO_VERIFY=1
WAIT_TIMEOUT="120"
LIST_COUNT="10"
NOTE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --to)         TARGET_TAG="${2:-}"; shift 2 ;;
        --tag)        TARGET_TAG="${2:-}"; shift 2 ;;
        --previous)   USE_PREVIOUS=1; shift ;;
        --pull)       DO_PULL=1; shift ;;
        --yes)        ASSUME_YES=1; shift ;;
        --dry-run)    DRY_RUN=1; shift ;;
        --no-verify)  DO_VERIFY=0; shift ;;
        --timeout)    WAIT_TIMEOUT="${2:-}"; shift 2 ;;
        --note)       NOTE="${2:-}"; shift 2 ;;
        -n)           LIST_COUNT="${2:-}"; shift 2 ;;
        -h|--help)    sed -n '2,55p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)            usage_error "unknown option: $1 (try --help)" ;;
    esac
done

bk_load_config

DEPLOY_LEDGER="${DR_DEPLOY_LEDGER:-${BACKUP_ROOT}/deployments.tsv}"
DEPLOY_ENV_FILE="${DR_ENV_FILE:-${REPO_ROOT}/.env}"
DR_BACKEND_SERVICE="${DR_BACKEND_SERVICE:-backend}"
BACKEND_IMAGE="${BACKEND_IMAGE:-stockassist-backend}"
DEFAULT_TAG="local"

# ------------------------------------------------------------------------------
# Reading the current state
#
# The RUNNING CONTAINER is the authority, not the env file. They disagree
# whenever someone edited `.env` and has not applied it yet — which during an
# incident is not an edge case, it is the most likely state of the world.
# ------------------------------------------------------------------------------
running_image() {
    local cid
    command -v docker >/dev/null 2>&1 || return 1
    cid="$(docker compose -f "${BACKUP_COMPOSE_FILE}" ps -q "${DR_BACKEND_SERVICE}" 2>/dev/null | head -n 1)"
    [[ -n "${cid}" ]] || return 1
    docker inspect -f '{{.Config.Image}}' "${cid}" 2>/dev/null || return 1
}

env_file_tag() {
    [[ -r "${DEPLOY_ENV_FILE}" ]] || { printf '%s' "${DEFAULT_TAG}"; return 0; }
    local v
    v="$(grep -E '^[[:space:]]*BACKEND_IMAGE_TAG[[:space:]]*=' "${DEPLOY_ENV_FILE}" 2>/dev/null | tail -n 1 | sed 's/^[^=]*=//' | tr -d '"'"'"' \t\r')"
    printf '%s' "${v:-${DEFAULT_TAG}}"
}

current_tag() {
    local img
    if img="$(running_image)" && [[ -n "${img}" ]]; then
        printf '%s' "${img##*:}"
    else
        env_file_tag
    fi
}

git_commit() {
    # `git status`/`git rev-parse` exiting non-zero must not kill the script
    # under `set -e` — a deployment outside a git checkout is unusual but not an
    # error, and this is metadata, not a precondition. (PH2.9 lost an entire
    # config backup to exactly this, silently.)
    (cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null) || printf 'unknown'
}

git_dirty() {
    if (cd "${REPO_ROOT}" && git diff --quiet HEAD 2>/dev/null); then
        printf 'clean'
    else
        printf 'dirty'
    fi
}

# ------------------------------------------------------------------------------
# The ledger
#
# TSV, append-only, no rotation. It is a few hundred bytes per deployment and it
# is the cheapest artifact in this repository to keep forever; a rotated
# deployment history loses precisely the old entry someone needs to answer "what
# were we running in March, when this started?".
#
# NOTHING SECRET GOES IN IT. It records tags, commits and notes, and it is
# synced off-host with the backups, so a field that could carry a credential
# would be a credential in object storage.
# ------------------------------------------------------------------------------
LEDGER_HEADER=$'#timestamp_utc\ttag\timage\tapp_version\tgit_commit\tgit_state\tactor\tnote'

ledger_append() {
    local tag="$1" note="$2" line
    mkdir -p "$(dirname "${DEPLOY_LEDGER}")"
    [[ -f "${DEPLOY_LEDGER}" ]] || printf '%s\n' "${LEDGER_HEADER}" > "${DEPLOY_LEDGER}"
    # Tabs and newlines in a note would corrupt the format; they are squeezed
    # out rather than rejected, because refusing to record a deployment over a
    # stray tab is the wrong trade.
    note="$(printf '%s' "${note}" | tr '\t\n' '  ')"
    line="$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        "${tag}" \
        "${BACKEND_IMAGE}" \
        "${APP_VERSION:-unknown}" \
        "$(git_commit)" \
        "$(git_dirty)" \
        "${SUDO_USER:-${USER:-unknown}}" \
        "${note}")"
    printf '%s\n' "${line}" >> "${DEPLOY_LEDGER}"
}

ledger_entries() { grep -v '^#' "${DEPLOY_LEDGER}" 2>/dev/null || true; }

# The most recent recorded tag that is NOT the one running now. "Previous" means
# the last DIFFERENT version, not the previous ledger line: re-recording the
# same tag (a restart, a config change) must not make `--previous` a no-op.
previous_tag() {
    local cur="$1"
    ledger_entries | awk -F'\t' -v cur="${cur}" '$2 != cur && $2 != "" { last = $2 } END { print last }'
}

# ==============================================================================
# COMMANDS
# ==============================================================================
case "${COMMAND}" in

current)
    CUR="$(current_tag)"
    if RIMG="$(running_image)"; then SRC="running container"; else RIMG="(not running)"; SRC="${DEPLOY_ENV_FILE}"; fi
    log "current tag : ${CUR}   [source: ${SRC}]"
    log "image       : ${RIMG}"
    log "env file tag: $(env_file_tag)"
    log "git         : $(git_commit) ($(git_dirty))"
    log "ledger      : ${DEPLOY_LEDGER}"
    printf '%s\n' "${CUR}"
    ;;

record)
    TAG="${TARGET_TAG:-$(current_tag)}"
    [[ -n "${TAG}" ]] || die "could not determine the tag to record — pass --tag"
    ledger_append "${TAG}" "${NOTE}"
    log "recorded deployment: ${BACKEND_IMAGE}:${TAG} → ${DEPLOY_LEDGER}"
    ;;

list)
    if [[ ! -f "${DEPLOY_LEDGER}" ]]; then
        # Not an error. It is the state every deployment starts in, and the
        # message an operator needs is the one that fixes it.
        warn "no deployment ledger at ${DEPLOY_LEDGER}"
        warn "record the current deployment with: $(basename "${BASH_SOURCE[0]}") record --note 'baseline'"
        exit 0
    fi
    log "deployment history (newest last) — ${DEPLOY_LEDGER}"
    printf '%-21s %-24s %-12s %-10s %s\n' "TIMESTAMP" "TAG" "COMMIT" "STATE" "NOTE" >&2
    ledger_entries | tail -n "${LIST_COUNT}" \
        | awk -F'\t' '{ printf "%-21s %-24s %-12s %-10s %s\n", $1, $2, $5, $6, $8 }'
    ;;

rollback)
    CURRENT="$(current_tag)"

    if [[ ${USE_PREVIOUS} -eq 1 ]]; then
        [[ -z "${TARGET_TAG}" ]] || usage_error "--to and --previous are mutually exclusive"
        TARGET_TAG="$(previous_tag "${CURRENT}")"
        [[ -n "${TARGET_TAG}" ]] || die "no previous deployment in the ledger (${DEPLOY_LEDGER}) — pass --to TAG explicitly"
    fi
    [[ -n "${TARGET_TAG}" ]] || usage_error "rollback needs --to TAG or --previous"

    log "current : ${BACKEND_IMAGE}:${CURRENT}"
    log "target  : ${BACKEND_IMAGE}:${TARGET_TAG}"

    if [[ "${TARGET_TAG}" == "${CURRENT}" ]]; then
        log "already running ${TARGET_TAG} — nothing to do"
        exit 0
    fi

    require_cmd docker

    # ------------------------------------------------------------------------
    # PRECONDITION — the target image must exist BEFORE anything is stopped.
    #
    # This is the whole reason this script exists. Without a registry, a
    # rollback target that has been pruned off the host is simply not
    # recoverable, and the moment to discover that is now — with the current
    # version still serving traffic — and not after the backend has been
    # recreated against an image that is not there.
    # ------------------------------------------------------------------------
    if ! docker image inspect "${BACKEND_IMAGE}:${TARGET_TAG}" >/dev/null 2>&1; then
        if [[ ${DO_PULL} -eq 1 ]]; then
            log "image not present locally — pulling ${BACKEND_IMAGE}:${TARGET_TAG}"
            docker pull "${BACKEND_IMAGE}:${TARGET_TAG}" \
                || die "pull failed: ${BACKEND_IMAGE}:${TARGET_TAG} is not available locally or from a registry"
        else
            err "target image is NOT on this host: ${BACKEND_IMAGE}:${TARGET_TAG}"
            err "nothing has been changed. Options:"
            err "  --pull                          if a registry holds it (none is configured yet — PH2.7b)"
            err "  rebuild it from the recorded commit, then re-run:"
            err "    git checkout <commit> && docker build -t ${BACKEND_IMAGE}:${TARGET_TAG} ./backend"
            err "  or roll forward with a fix instead of back"
            exit 1
        fi
    fi
    log "precondition OK — target image is present on this host"

    # ------------------------------------------------------------------------
    # The question no script can answer for you.
    # ------------------------------------------------------------------------
    if [[ ${ASSUME_YES} -eq 0 ]]; then
        cat >&2 <<EOF

  ⚠ Before rolling back, confirm ONE thing this script cannot check:

    Did ${CURRENT} change the DATABASE in a way ${TARGET_TAG} does not
    understand — a migration, a new required field, a renamed collection?

    If it did, rolling the image back is NOT a rollback. The old code will
    meet a schema it has never seen. In that case the correct move is a
    restore (docs/operations/DISASTER_RECOVERY.md §Runbook R4) or a
    roll-FORWARD fix — not this command.

EOF
    fi
    bk_confirm_destructive "${TARGET_TAG}" "${ASSUME_YES}"

    if [[ ${DRY_RUN} -eq 1 ]]; then
        log "--dry-run: would set BACKEND_IMAGE_TAG=${TARGET_TAG} in ${DEPLOY_ENV_FILE}"
        log "--dry-run: would run: docker compose up -d --no-deps ${DR_BACKEND_SERVICE}"
        exit 0
    fi

    # Record the roll-FORWARD point before changing anything, so the version
    # being left behind is recoverable by the same mechanism.
    ledger_append "${CURRENT}" "state before rollback to ${TARGET_TAG}"

    # ------------------------------------------------------------------------
    # Apply — write the tag atomically.
    #
    # A partially-written `.env` is worse than either version of it: compose
    # interpolation fails and the stack will not start at all, converting a
    # rollback into an outage. Temp file in the same directory (so the rename
    # is atomic, not a cross-filesystem copy), mode preserved, then mv.
    # ------------------------------------------------------------------------
    set_env_tag() {
        local tag="$1" tmp
        tmp="$(mktemp "${DEPLOY_ENV_FILE}.XXXXXX")"
        chmod 600 "${tmp}"
        if [[ -f "${DEPLOY_ENV_FILE}" ]]; then
            awk -v tag="${tag}" '
                /^[[:space:]]*BACKEND_IMAGE_TAG[[:space:]]*=/ { print "BACKEND_IMAGE_TAG=" tag; found = 1; next }
                { print }
                END { if (!found) print "BACKEND_IMAGE_TAG=" tag }
            ' "${DEPLOY_ENV_FILE}" > "${tmp}"
        else
            printf 'BACKEND_IMAGE_TAG=%s\n' "${tag}" > "${tmp}"
        fi
        mv -f "${tmp}" "${DEPLOY_ENV_FILE}"
    }

    apply_tag() {
        local tag="$1"
        set_env_tag "${tag}"
        # `--no-deps`: recreate ONLY the backend. Recreating mongo and redis
        # during an application rollback restarts the database for no reason and
        # empties the cache, turning a 20-second rollback into a cold start on
        # every tier at once.
        docker compose -f "${BACKUP_COMPOSE_FILE}" up -d --no-deps "${DR_BACKEND_SERVICE}"
    }

    log "applying ${TARGET_TAG}"
    if ! apply_tag "${TARGET_TAG}"; then
        err "compose failed to bring up ${TARGET_TAG} — reverting to ${CURRENT}"
        apply_tag "${CURRENT}" || err "REVERT ALSO FAILED — manual intervention required, see §Runbook R5"
        exit 1
    fi

    # ------------------------------------------------------------------------
    # Verify — a rollback that is not verified is a second, unmonitored deploy.
    # ------------------------------------------------------------------------
    if [[ ${DO_VERIFY} -eq 0 ]]; then
        warn "--no-verify: NOT checking that ${TARGET_TAG} is healthy. Run dr_verify.sh yourself."
        ledger_append "${TARGET_TAG}" "rollback from ${CURRENT} (unverified)"
        exit 0
    fi

    log "waiting up to ${WAIT_TIMEOUT}s for the rolled-back build to become healthy"
    DEADLINE=$(( $(date +%s) + WAIT_TIMEOUT ))
    VERIFIED=0
    while [[ $(date +%s) -lt ${DEADLINE} ]]; do
        if "${DR_DIR}/dr_verify.sh" --level quick --quiet >/dev/null 2>&1; then
            VERIFIED=1
            break
        fi
        sleep 5
    done

    if [[ ${VERIFIED} -eq 1 ]]; then
        log "rollback verified: ${BACKEND_IMAGE}:${TARGET_TAG} is healthy"
        ledger_append "${TARGET_TAG}" "rollback from ${CURRENT}${NOTE:+ — ${NOTE}}"
        log "run a full check before closing the incident:"
        log "  ./scripts/dr/dr_verify.sh --level full"
        exit 0
    fi

    # ------------------------------------------------------------------------
    # AUTOMATIC REVERT.
    #
    # A rollback to a version that is ALSO broken is the worst outcome of the
    # three, because it consumes the operator's remaining confidence in the
    # mechanism. Going back to the state we started from is not a fix, but it is
    # a known state, and a known state is where diagnosis can restart.
    # ------------------------------------------------------------------------
    err "rollback target ${TARGET_TAG} did NOT become healthy within ${WAIT_TIMEOUT}s"
    err "reverting to ${CURRENT} — the state before this command ran"
    if apply_tag "${CURRENT}"; then
        ledger_append "${CURRENT}" "auto-revert: rollback to ${TARGET_TAG} failed verification"
        err "reverted to ${CURRENT}. Neither version is confirmed healthy — this is now an"
        err "incident, not a rollback. See docs/operations/DISASTER_RECOVERY.md §Runbook R5."
    else
        err "REVERT ALSO FAILED. The stack may be down. §Runbook R5, escalate immediately."
    fi
    exit 1
    ;;

*)
    usage_error "unknown command: ${COMMAND} (record | list | current | rollback)"
    ;;
esac

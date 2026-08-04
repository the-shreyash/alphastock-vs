#!/usr/bin/env bash
# ==============================================================================
# StockAssist AI — MongoDB restore (PH2.9)
#
#   ./scripts/backup/restore_mongo.sh --latest                        # into the original database
#   ./scripts/backup/restore_mongo.sh <artifact>                      # a specific backup
#   ./scripts/backup/restore_mongo.sh <artifact> --target-db scratch  # non-destructive inspection
#   ./scripts/backup/restore_mongo.sh <artifact> --drop --yes         # unattended, full replace
#
# WHAT PROBLEM THIS SOLVES
# ------------------------
# It is 03:00, the database is wrong, and the person at the keyboard is not the
# person who wrote the backup system. Everything in this script is arranged for
# that moment: it verifies before it writes, it says out loud what it is about
# to overwrite, it refuses to do it silently, it measures how long it took, and
# it checks afterwards that the data is actually there.
#
# THE FOUR RULES, AND WHY EACH ONE IS HERE
# ----------------------------------------
#   1. VERIFY BEFORE WRITING.  A structural verification runs first, always. The
#      one unrecoverable way to use a restore tool is to drop a live collection
#      and then discover the archive is corrupt — at which point the bad data
#      you were replacing is also gone. Verification takes seconds; that
#      ordering is free insurance.
#
#   2. THE DEFAULT IS NON-DESTRUCTIVE.  Without `--drop`, mongorestore MERGES:
#      documents whose _id already exists are skipped, everything else is
#      inserted. That is deliberately the default, because "insert what is
#      missing" is recoverable and "replace everything" is not. `--drop` exists
#      and is honest about what it does.
#
#   3. CONFIRMATION IS TYPED, NOT A KEYSTROKE.  A y/N prompt at 03:00 is
#      answered by muscle memory. Retyping the database name is the same
#      protocol `terraform destroy` uses, for the same reason.
#
#   4. VERIFY AFTER WRITING.  mongorestore exits 0 on a restore that inserted
#      nothing. The post-restore count comparison against the manifest baseline
#      is what turns "the command succeeded" into "the data is there".
#
# ⚠ RESTORING INTO A LIVE APPLICATION
#   Stop the backend first. `docker compose stop backend` before, and start it
#   after. A restore that runs while the application is writing produces a
#   database that is neither the backup nor the previous state, and the
#   application's in-memory caches will additionally be describing data that no
#   longer exists. See docs/operations/BACKUP_AND_RESTORE.md §Restore Procedure
#   for the full ordered checklist.
# ==============================================================================
set -euo pipefail

# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ARTIFACT=""
TARGET_DB=""
USE_LATEST=0
DO_DROP=0
ASSUME_YES=0
SKIP_VERIFY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --latest)      USE_LATEST=1; shift ;;
        --target-db)   TARGET_DB="${2:-}"; shift 2 ;;
        --drop)        DO_DROP=1; shift ;;
        --yes)         ASSUME_YES=1; shift ;;
        --skip-verify) SKIP_VERIFY=1; shift ;;
        -h|--help)     sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*)            usage_error "unknown option: $1 (try --help)" ;;
        *)             ARTIFACT="$1"; shift ;;
    esac
done

bk_load_config

if [[ ${USE_LATEST} -eq 1 ]]; then
    [[ -z "${ARTIFACT}" ]] || usage_error "--latest and an explicit artifact path are mutually exclusive"
    newest="$(find "${BACKUP_ROOT}/mongo" -type f \
        \( -name '*.archive.gz' -o -name '*.archive.gz.enc' \) 2>/dev/null \
        | sed 's#.*/##' | sort -r | head -n 1)"
    [[ -n "${newest}" ]] || die "no backups found under ${BACKUP_ROOT}/mongo"
    ARTIFACT="$(find "${BACKUP_ROOT}/mongo" -type f -name "${newest}" | head -n 1)"
fi

[[ -n "${ARTIFACT}" ]] || usage_error "no artifact given (pass a path, or --latest)"
[[ -f "${ARTIFACT}" ]] || die "artifact not found: ${ARTIFACT}"

MANIFEST="$(dirname "${ARTIFACT}")/$(bk_artifact_base "$(basename "${ARTIFACT}")").manifest.json"
[[ -f "${MANIFEST}" ]] || die "no manifest beside ${ARTIFACT} — cannot establish what this artifact contains, refusing to restore"

SOURCE_DB="$(bk_manifest_get "${MANIFEST}" database)"
ENCRYPTION="$(bk_manifest_get "${MANIFEST}" encryption)"
CREATED_AT="$(bk_manifest_get "${MANIFEST}" created_at)"
EXPECTED="$(bk_manifest_get "${MANIFEST}" collections)"
[[ -n "${SOURCE_DB}" ]] || die "manifest does not record a source database name"

TARGET_DB="${TARGET_DB:-${SOURCE_DB}}"

# ------------------------------------------------------------------------------
# 1. Verify the archive BEFORE touching the database.
# ------------------------------------------------------------------------------
if [[ ${SKIP_VERIFY} -eq 1 ]]; then
    # There is one legitimate use: a restore where the artifact is enormous, the
    # verification has already been run in this session, and every minute of RTO
    # counts. It is loud because using it to "save time" on an unverified
    # artifact is how a corrupt backup gets restored over good data.
    warn "--skip-verify: restoring an UNVERIFIED artifact"
else
    log "verifying ${ARTIFACT} before restoring…"
    "${BACKUP_LIB_DIR}/verify_backup.sh" --level structural "${ARTIFACT}" \
        || die "verification failed — NOTHING was restored and the target database is untouched"
fi

# ------------------------------------------------------------------------------
# 2. Inspect the target and say out loud what is about to happen.
# ------------------------------------------------------------------------------
bk_prepare_mongo

TARGET_STATE="$(bk_mongo_eval "
    const d = db.getSiblingDB('${TARGET_DB}');
    const names = d.getCollectionNames().filter(function (c) { return c.indexOf('system.') !== 0; });
    let total = 0;
    names.forEach(function (c) { total += d.getCollection(c).countDocuments({}); });
    print(names.length + ' ' + total);
" 2>/dev/null | tr -d '\r' | grep -E '^[0-9]+ [0-9]+$' | head -n 1 || true)"

TARGET_COLLECTIONS="${TARGET_STATE%% *}"
TARGET_DOCUMENTS="${TARGET_STATE##* }"
TARGET_COLLECTIONS="${TARGET_COLLECTIONS:-0}"
TARGET_DOCUMENTS="${TARGET_DOCUMENTS:-0}"

printf '\n' >&2
log "RESTORE PLAN"
log "  artifact    : ${ARTIFACT}"
log "  taken       : ${CREATED_AT}"
log "  source db   : ${SOURCE_DB}"
log "  target db   : ${TARGET_DB}$( [[ "${TARGET_DB}" != "${SOURCE_DB}" ]] && printf ' (namespace remap)' )"
log "  target now  : ${TARGET_COLLECTIONS} collections, ${TARGET_DOCUMENTS} documents"
log "  mode        : $( [[ ${DO_DROP} -eq 1 ]] && printf 'DROP AND REPLACE' || printf 'merge (existing _ids preserved)' )"
log "  encryption  : ${ENCRYPTION}"
printf '\n' >&2

# Only a restore that can destroy existing data prompts. Restoring into an empty
# database — the disaster-recovery case, and the case where a prompt is most
# obstructive — proceeds without one, because there is nothing there to lose.
if [[ "${TARGET_DOCUMENTS}" != "0" ]]; then
    if [[ ${DO_DROP} -eq 1 ]]; then
        bk_confirm_destructive "${TARGET_DB}" "${ASSUME_YES}"
    else
        # Even a merge can change live data (it inserts), so it is announced —
        # but it does not require the typed confirmation, because nothing that
        # exists is removed.
        warn "target database is not empty; restoring in MERGE mode — existing documents with matching _id are kept"
    fi
fi

# ------------------------------------------------------------------------------
# 3. Restore.
# ------------------------------------------------------------------------------
RESTORE_ARGS=(--archive --gzip --quiet)
# `[[ cond ]] && arr[n]=x` as a statement is a trap under `set -e`: when the
# condition is false the statement's exit status is 1 and the script aborts.
if [[ ${DO_DROP} -eq 1 ]]; then
    RESTORE_ARGS[${#RESTORE_ARGS[@]}]="--drop"
fi
if [[ "${TARGET_DB}" != "${SOURCE_DB}" ]]; then
    RESTORE_ARGS[${#RESTORE_ARGS[@]}]="--nsFrom=${SOURCE_DB}.*"
    RESTORE_ARGS[${#RESTORE_ARGS[@]}]="--nsTo=${TARGET_DB}.*"
fi

log "restoring…"
START_EPOCH="$(date -u '+%s')"
rc=0

if [[ "${ENCRYPTION}" != "none" && -n "${ENCRYPTION}" ]]; then
    PASSPHRASE_FILE="$(bk_passphrase_file)" \
        || die "artifact is encrypted but no passphrase is configured"
    bk_decrypt_filter "${PASSPHRASE_FILE}" < "${ARTIFACT}" \
        | bk_mongo_tool mongorestore "${RESTORE_ARGS[@]}" || rc=$?
else
    bk_mongo_tool mongorestore "${RESTORE_ARGS[@]}" < "${ARTIFACT}" || rc=$?
fi

END_EPOCH="$(date -u '+%s')"
DURATION=$(( END_EPOCH - START_EPOCH ))

[[ $rc -eq 0 ]] || die "mongorestore failed (exit ${rc}) after ${DURATION}s — the target database is in an INDETERMINATE state; do not start the application against it"

log "mongorestore completed in ${DURATION}s"

# ------------------------------------------------------------------------------
# 4. Verify the restore actually landed.
#
# This is the step most restore procedures skip, and it is the one that catches
# the failure that matters: a restore that succeeded structurally and moved no
# data (wrong namespace filter, wrong source database in the archive, a
# permission that silently dropped writes).
# ------------------------------------------------------------------------------
if [[ -z "${EXPECTED}" || "${EXPECTED}" == "{}" ]]; then
    warn "manifest carries no collection baseline — the restore CANNOT be verified, only observed"
else
    log "comparing restored counts against the manifest baseline…"
    REPORT="$(bk_mongo_eval "
        const expected = ${EXPECTED};
        const d = db.getSiblingDB('${TARGET_DB}');
        const present = {};
        d.getCollectionNames().forEach(function (c) { present[c] = true; });
        Object.keys(expected).sort().forEach(function (c) {
            if (!present[c]) { print('MISSING ' + c + ' ' + expected[c] + ' -'); return; }
            const n = d.getCollection(c).countDocuments({});
            if (n === expected[c])               print('MATCH ' + c + ' ' + expected[c] + ' ' + n);
            else if (expected[c] > 0 && n === 0) print('EMPTY ' + c + ' ' + expected[c] + ' ' + n);
            else                                 print('DIFF ' + c + ' ' + expected[c] + ' ' + n);
        });
    " 2>/dev/null | tr -d '\r' | grep -E '^(MATCH|MISSING|EMPTY|DIFF) ' || true)"

    matched=0; diffed=0; failed=0
    while IFS=' ' read -r status name exp act; do
        [[ -n "${status}" ]] || continue
        case "${status}" in
            MATCH)   matched=$((matched + 1)) ;;
            # In merge mode a HIGHER count than the baseline is the expected
            # outcome, not an anomaly: the target kept documents the backup did
            # not contain. Reported, never fatal.
            DIFF)    diffed=$((diffed + 1)); warn "  ${name}: manifest=${exp} restored=${act}" ;;
            MISSING) failed=$((failed + 1)); err "  MISSING: ${name} (expected ${exp} documents)" ;;
            EMPTY)   failed=$((failed + 1)); err "  EMPTY: ${name} (expected ${exp} documents, got 0)" ;;
        esac
    done <<< "${REPORT}"

    if [[ ${failed} -gt 0 ]]; then
        die "restore verification FAILED: ${failed} collection(s) missing or empty — do not start the application against this database"
    fi
    log "verification: ${matched} collections matched exactly, ${diffed} differ (expected in merge mode)"
fi

printf '\n' >&2
log "RESTORE COMPLETE — ${TARGET_DB} restored from ${CREATED_AT} in ${DURATION}s"
log "Next: run the post-restore checklist in docs/operations/BACKUP_AND_RESTORE.md §Restore Procedure"
printf 'RESTORE_SECONDS=%s\n' "${DURATION}"
exit 0

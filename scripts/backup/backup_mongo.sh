#!/usr/bin/env bash
# ==============================================================================
# StockAssist AI — MongoDB full backup (PH2.9)
#
#   ./scripts/backup/backup_mongo.sh                 # tier chosen from the calendar
#   ./scripts/backup/backup_mongo.sh --tier monthly  # force a tier
#   ./scripts/backup/backup_mongo.sh --no-prune      # keep every existing backup
#   ./scripts/backup/backup_mongo.sh --print-path    # emit the artifact path on stdout
#
# WHAT PROBLEM THIS SOLVES
# ------------------------
# Everything the product cannot recreate lives in MongoDB: users, portfolios,
# trades, the trade journal, sessions, and the security audit trail. Market data
# is refetchable and the Redis cache is disposable, but a dropped `users`
# collection is the end of the company. Until this script existed, the only copy
# of that data was a single Docker named volume on a single host — which
# survives `docker compose down` and nothing else. Not a bad migration, not a
# `down -v` typed into the wrong terminal, not a failed disk.
#
# WHY IT IS SHAPED LIKE THIS
# --------------------------
# Four properties, each of which is what separates a backup that works from one
# that only exists:
#
#   STREAMED, NEVER STAGED.  mongodump writes an `--archive` to stdout, which is
#   piped through gzip (mongodump's own) and then through openssl, and lands on
#   disk already compressed and encrypted. At no point does a plaintext copy of
#   the database exist on a filesystem, so there is nothing to forget to delete
#   and nothing for an interrupted run to leave behind.
#
#   ATOMIC PUBLICATION.  The stream is written to `<name>.partial` and renamed
#   into place only after it has been checksummed. A backup directory therefore
#   never contains a truncated file that looks complete — which matters most on
#   the day you are choosing which backup to restore and cannot afford to guess.
#
#   SELF-DESCRIBING.  Every artifact is accompanied by a manifest recording what
#   it is, how it was made, its SHA-256, and the per-collection document counts
#   at dump time. That last field is what makes verification possible at all:
#   "mongorestore exited 0" is not evidence, "the restored database has the 1696
#   notifications the manifest says it should" is.
#
#   PRUNE LAST.  Retention runs only after the new backup is on disk and
#   verified. Pruning first and then failing to produce the replacement is how a
#   retention policy quietly becomes a data-loss policy.
#
# ⚠ CONSISTENCY — READ THIS BEFORE RELYING ON IT FOR A FINANCIAL RECONCILIATION.
#   Against a STANDALONE mongod (what docker-compose.yml runs today) mongodump
#   is consistent within each collection but NOT across collections: writes that
#   land after `trades` is dumped and before `portfolios` is dumped appear in one
#   and not the other. For a crash-recovery restore that is acceptable — you are
#   recovering to "approximately 03:00", not to a transaction boundary. Removing
#   the caveat requires converting mongod to a single-node REPLICA SET, which
#   enables `mongodump --oplog` and true point-in-time restore. That conversion
#   is deliberately out of scope for this sprint and is the first item in
#   docs/operations/BACKUP_AND_RESTORE.md §Known Limitations.
# ==============================================================================
set -euo pipefail

# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TIER=""
DO_PRUNE=1
PRINT_PATH=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tier)       TIER="${2:-}"; shift 2 ;;
        --db)         MONGO_DB_NAME="${2:-}"; export MONGO_DB_NAME; shift 2 ;;
        --no-prune)   DO_PRUNE=0; shift ;;
        --print-path) PRINT_PATH=1; shift ;;
        -h|--help)    sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)            usage_error "unknown option: $1 (try --help)" ;;
    esac
done

bk_load_config

case "${TIER}" in
    "")                      TIER="$(bk_tier_for_now)" ;;
    daily|weekly|monthly)    ;;
    *)                       usage_error "--tier must be daily, weekly or monthly (got '${TIER}')" ;;
esac

readonly DEST_DIR="${BACKUP_ROOT}/mongo/${TIER}"
mkdir -p "${DEST_DIR}"
# The backup directory holds the entire database in a form that is only as
# private as the filesystem allows. 700, always — including when the operator's
# umask says otherwise.
chmod 700 "${BACKUP_ROOT}" "${BACKUP_ROOT}/mongo" "${DEST_DIR}" 2>/dev/null || true

TIMESTAMP="$(bk_timestamp)"
BASE="mongo-${MONGO_DB_NAME}-${TIMESTAMP}-${TIER}"

ENCRYPT=0
if bk_should_encrypt; then ENCRYPT=1; fi

if [[ $ENCRYPT -eq 1 ]]; then
    ARTIFACT="${DEST_DIR}/${BASE}.archive.gz.enc"
    ENCRYPTION="openssl-aes-256-cbc-pbkdf2-600000"
else
    ARTIFACT="${DEST_DIR}/${BASE}.archive.gz"
    ENCRYPTION="none"
fi
MANIFEST="${DEST_DIR}/${BASE}.manifest.json"

log "database   : ${MONGO_DB_NAME}"
log "mode       : ${BACKUP_MODE}"
log "tier       : ${TIER}"
log "destination: ${ARTIFACT}"
log "encryption : ${ENCRYPTION}"

# ------------------------------------------------------------------------------
# 1. Baseline — collection document counts.
#
# Sampled BEFORE the dump so that a collection dropped mid-run is caught by the
# restore comparison rather than silently absent from both sides. On a live
# system the counts drift by whatever was written during the dump; the drill in
# verify_backup.sh treats a small positive drift as expected and a MISSING
# collection or a zeroed one as a failure. See §Verification in the docs.
# ------------------------------------------------------------------------------
# Stage the mongo transport before the streaming pipeline below — see
# bk_prepare_mongo for why doing it lazily inside a pipeline leaks a credential
# file into the database container.
bk_prepare_mongo

log "sampling collection counts…"
COLLECTIONS="$(bk_collection_counts "${MONGO_DB_NAME}" || true)"
if [[ -z "${COLLECTIONS}" ]]; then
    # Not fatal. A missing baseline weakens verification but a backup without a
    # baseline is still infinitely better than no backup, and refusing to run
    # because mongosh is unavailable would be the tail wagging the dog.
    warn "could not sample collection counts — the manifest will have no verification baseline"
    COLLECTIONS="{}"
fi

# ------------------------------------------------------------------------------
# 2. Dump → compress → encrypt → disk, in one stream.
# ------------------------------------------------------------------------------
# mongodump streams an `--archive` to stdout; bk_publish_artifact takes it from
# there — encrypt, write to `.partial`, checksum, rename, re-checksum. See the
# four failure modes documented on that function.
START_EPOCH="$(date -u '+%s')"

bk_publish_artifact "${ARTIFACT}" "${ENCRYPT}" \
    bk_mongo_tool mongodump --db="${MONGO_DB_NAME}" --archive --gzip --quiet \
    || die "mongodump failed — no backup was written (every existing backup is untouched)"

END_EPOCH="$(date -u '+%s')"
DURATION=$(( END_EPOCH - START_EPOCH ))
SIZE="${_BK_PUBLISH_SIZE}"
CHECKSUM="${_BK_PUBLISH_SHA256}"

# ------------------------------------------------------------------------------
# 3. Record what was written.
# ------------------------------------------------------------------------------
bk_manifest_write "${MANIFEST}" \
    schema           "${BACKUP_MANIFEST_SCHEMA}" \
    kind             "mongo" \
    database         "${MONGO_DB_NAME}" \
    tier             "${TIER}" \
    created_at       "$(_bk_ts)" \
    artifact         "$(basename "${ARTIFACT}")" \
    format           "mongodump-archive-gzip" \
    encryption       "${ENCRYPTION}" \
    sha256           "${CHECKSUM}" \
    size_bytes       "${SIZE}" \
    duration_seconds "${DURATION}" \
    backup_mode      "${BACKUP_MODE}" \
    consistency      "per-collection (standalone mongod, no --oplog)" \
    collections      "${COLLECTIONS}"

log "wrote ${ARTIFACT} ($(bk_human_size "${SIZE}")) in ${DURATION}s"
log "sha256 ${CHECKSUM}"

# ------------------------------------------------------------------------------
# 4. Retention — last, and only on success.
# ------------------------------------------------------------------------------
if [[ $DO_PRUNE -eq 1 ]]; then
    bk_prune_tier "${DEST_DIR}" "$(bk_retention_for_tier "${TIER}")" \
        "^mongo-.*\.archive\.gz(\.enc)?$"
else
    log "retention: skipped (--no-prune)"
fi

# ------------------------------------------------------------------------------
# 5. Self-check — and it is LOAD-BEARING, not a nicety.
#
# A backup job that has never been restored from is a hope, not a backup. A full
# drill is too expensive to run nightly, so the nightly job runs the two cheap
# levels (checksum + decrypt + CRC + archive-magic) and the expensive level — an
# actual restore into a scratch database — runs on the monthly drill.
#
# This is also the ONLY place that can catch an empty dump when encryption is
# on: openssl turns zero bytes of input into a ~32-byte file, so the
# non-empty check inside bk_publish_artifact is satisfied by an artifact
# containing nothing at all. Decrypting and checking the payload is what sees
# through that.
#
# ON FAILURE THE ARTIFACT IS RENAMED, NOT DELETED.
# `.rejected` is outside every glob this system uses — `--latest` will not
# select it, retention will not count it, a restore cannot reach it by
# accident — so it is inert. But it is still on disk, because the file is the
# evidence for why the backup failed, and deleting evidence during an incident
# is how the same incident happens again next month.
# ------------------------------------------------------------------------------
if [[ -x "${BACKUP_LIB_DIR}/verify_backup.sh" ]]; then
    if ! "${BACKUP_LIB_DIR}/verify_backup.sh" --level structural "${ARTIFACT}"; then
        mv -f "${ARTIFACT}" "${ARTIFACT}.rejected" 2>/dev/null || true
        mv -f "${MANIFEST}" "${MANIFEST}.rejected" 2>/dev/null || true
        die "the backup FAILED verification and was quarantined as ${ARTIFACT}.rejected — it is NOT a usable backup; the previous backups are untouched"
    fi
fi

log "backup complete"
if [[ $PRINT_PATH -eq 1 ]]; then printf '%s\n' "${ARTIFACT}"; fi
exit 0

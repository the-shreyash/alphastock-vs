#!/usr/bin/env bash
# ==============================================================================
# StockAssist AI — upload storage backup (PH2.9)
#
#   ./scripts/backup/backup_uploads.sh                    # back up the Docker volume
#   ./scripts/backup/backup_uploads.sh --path /srv/uploads  # back up a host directory
#   ./scripts/backup/backup_uploads.sh --restore <artifact> --yes
#
# STATUS TODAY: THERE IS NOTHING TO BACK UP YET, AND THAT IS THE POINT
# --------------------------------------------------------------------
# docker-compose.yml DECLARES the `backend_uploads` volume but deliberately does
# not mount it (see the commented block in the backend service: mounting it
# requires the image to pre-create the directory owned by uid 10001, which is a
# PH2.1 change). So the volume is empty, and this script exits cleanly saying so.
#
# It exists now anyway, for a reason worth stating plainly: the moment uploads
# ship — avatars, imported broker statements, exported reports — they will be
# live user data on day one, and the first day of a new data store is exactly
# when nobody remembers to add it to the backup rotation. Writing the procedure
# while the volume is empty means the feature launches WITH a backup path
# instead of acquiring one after the first data-loss incident.
#
# The corresponding tripwire is in the documentation's monthly checklist: if
# this script reports "0 files" for a component that is supposed to be in use,
# that is an alert, not a pass.
#
# WHY A DOCKER VOLUME NEEDS A HELPER CONTAINER
# --------------------------------------------
# A named volume lives inside Docker's storage area, which on macOS and Windows
# is inside a virtual machine and is not a host path at all. `tar` on the host
# cannot read it. The portable way to read a named volume is to mount it into a
# throwaway container and stream a tar out of that container's stdout.
#
# The helper image defaults to the REDIS image the stack already pins and has
# already pulled, rather than introducing `alpine:latest` or `busybox:latest`.
# That is a supply-chain decision: an unpinned `:latest` in a backup path is a
# third party who can change what runs against your data, and a backup script is
# the last place to accept that. Anything with a busybox/GNU `tar` works —
# override with BACKUP_HELPER_IMAGE if the stack changes.
# ==============================================================================
set -euo pipefail

# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

HOST_PATH=""
RESTORE_FROM=""
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --path)    HOST_PATH="${2:-}"; shift 2 ;;
        --restore) RESTORE_FROM="${2:-}"; shift 2 ;;
        --yes)     ASSUME_YES=1; shift ;;
        -h|--help) sed -n '2,7p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)         usage_error "unknown option: $1 (try --help)" ;;
    esac
done

bk_load_config

# The compose project is named `stockassist` (docker-compose.yml `name:`), so
# volumes appear on the host with that prefix regardless of the checkout
# directory's name — which is precisely why that line is there.
BACKUP_UPLOADS_VOLUME="${BACKUP_UPLOADS_VOLUME:-stockassist_backend_uploads}"
BACKUP_HELPER_IMAGE="${BACKUP_HELPER_IMAGE:-redis:${REDIS_IMAGE_TAG:-7.2-alpine}}"

readonly DEST_DIR="${BACKUP_ROOT}/uploads"

# ------------------------------------------------------------------------------
# Source abstraction: a host directory or a Docker named volume.
# ------------------------------------------------------------------------------
uploads_source_description() {
    if [[ -n "${HOST_PATH}" ]]; then printf 'host path %s' "${HOST_PATH}"
    else printf 'docker volume %s' "${BACKUP_UPLOADS_VOLUME}"; fi
}

uploads_file_count() {
    if [[ -n "${HOST_PATH}" ]]; then
        find "${HOST_PATH}" -type f 2>/dev/null | wc -l | tr -d '[:space:]'
    else
        docker run --rm -v "${BACKUP_UPLOADS_VOLUME}:/data:ro" "${BACKUP_HELPER_IMAGE}" \
            sh -c 'find /data -type f | wc -l' 2>/dev/null | tr -d '[:space:]\r'
    fi
}

# Streams a gzipped tar of the upload tree to stdout.
#
# `-C <root> .` rather than `-C / <root>`: every path inside the archive is
# relative to the upload root, so the archive can be unpacked into a volume, a
# host directory, or an object-storage prefix without path surgery. An archive
# of absolute paths is an archive that has to be rewritten before it can be used.
# shellcheck disable=SC2329  # invoked indirectly, as bk_publish_artifact's producer
uploads_producer() {
    if [[ -n "${HOST_PATH}" ]]; then
        tar -C "${HOST_PATH}" -czf - .
    else
        # `:ro` — a backup must not be able to modify the thing it is backing up.
        docker run --rm -i -v "${BACKUP_UPLOADS_VOLUME}:/data:ro" "${BACKUP_HELPER_IMAGE}" \
            tar -C /data -czf - .
    fi
}

# ------------------------------------------------------------------------------
# Restore
# ------------------------------------------------------------------------------
if [[ -n "${RESTORE_FROM}" ]]; then
    [[ -f "${RESTORE_FROM}" ]] || die "artifact not found: ${RESTORE_FROM}"
    MANIFEST="$(dirname "${RESTORE_FROM}")/$(bk_artifact_base "$(basename "${RESTORE_FROM}")").manifest.json"
    [[ -f "${MANIFEST}" ]] || die "no manifest beside ${RESTORE_FROM} — refusing to restore"

    "${BACKUP_LIB_DIR}/verify_backup.sh" --level checksum "${RESTORE_FROM}" \
        || die "verification failed — nothing was restored"

    ENCRYPTION="$(bk_manifest_get "${MANIFEST}" encryption)"
    bk_confirm_destructive "$(uploads_source_description)" "${ASSUME_YES}"

    log "restoring uploads into $(uploads_source_description)…"
    START_EPOCH="$(date -u '+%s')"

    # Unpacking OVER the existing tree, not replacing it: files present in the
    # archive are overwritten, files added since the backup are left alone. For
    # user uploads that is almost always the intent — the alternative deletes
    # every file uploaded after the backup was taken, which turns a partial loss
    # into a total one. Wipe first, deliberately and separately, if that really
    # is what is wanted.
    if [[ -n "${HOST_PATH}" ]]; then
        mkdir -p "${HOST_PATH}"
        UNPACK=(tar -C "${HOST_PATH}" -xzf -)
    else
        UNPACK=(docker run --rm -i -v "${BACKUP_UPLOADS_VOLUME}:/data" "${BACKUP_HELPER_IMAGE}" tar -C /data -xzf -)
    fi

    if [[ "${ENCRYPTION}" != "none" && -n "${ENCRYPTION}" ]]; then
        PASSPHRASE_FILE="$(bk_passphrase_file)"
        bk_decrypt_filter "${PASSPHRASE_FILE}" < "${RESTORE_FROM}" | "${UNPACK[@]}"
    else
        "${UNPACK[@]}" < "${RESTORE_FROM}"
    fi

    END_EPOCH="$(date -u '+%s')"
    log "uploads restored in $(( END_EPOCH - START_EPOCH ))s — now $(uploads_file_count) files present"
    exit 0
fi

# ------------------------------------------------------------------------------
# Backup
# ------------------------------------------------------------------------------
if [[ -z "${HOST_PATH}" ]]; then
    require_cmd docker
    if ! docker volume inspect "${BACKUP_UPLOADS_VOLUME}" >/dev/null 2>&1; then
        # Exit 0, not 1. This is the EXPECTED state until uploads are wired up
        # (see the header), and a nightly job that emails a failure every night
        # for a component that does not exist yet is a job whose failures stop
        # being read — which is how the real failure, later, gets missed.
        log "volume '${BACKUP_UPLOADS_VOLUME}' does not exist — nothing to back up (expected until uploads are mounted in docker-compose.yml)"
        exit 0
    fi
else
    [[ -d "${HOST_PATH}" ]] || die "not a directory: ${HOST_PATH}"
fi

FILE_COUNT="$(uploads_file_count)"
FILE_COUNT="${FILE_COUNT:-0}"

if [[ "${FILE_COUNT}" == "0" ]]; then
    log "$(uploads_source_description) holds 0 files — nothing to back up"
    log "⚠ if uploads ARE in use, this is an ALERT, not a pass — check the mount"
    exit 0
fi

mkdir -p "${DEST_DIR}"
chmod 700 "${BACKUP_ROOT}" "${DEST_DIR}" 2>/dev/null || true

TIER="$(bk_tier_for_now)"
TIMESTAMP="$(bk_timestamp)"
BASE="uploads-${TIMESTAMP}-${TIER}"

ENCRYPT=0
if bk_should_encrypt; then ENCRYPT=1; fi

if [[ ${ENCRYPT} -eq 1 ]]; then
    ARTIFACT="${DEST_DIR}/${BASE}.tar.gz.enc"
    ENCRYPTION="openssl-aes-256-cbc-pbkdf2-600000"
else
    ARTIFACT="${DEST_DIR}/${BASE}.tar.gz"
    ENCRYPTION="none"
fi
MANIFEST="${DEST_DIR}/${BASE}.manifest.json"

log "source     : $(uploads_source_description)"
log "files      : ${FILE_COUNT}"
log "destination: ${ARTIFACT}"

START_EPOCH="$(date -u '+%s')"
bk_publish_artifact "${ARTIFACT}" "${ENCRYPT}" uploads_producer \
    || die "upload backup failed — nothing was written"
END_EPOCH="$(date -u '+%s')"

bk_manifest_write "${MANIFEST}" \
    schema           "${BACKUP_MANIFEST_SCHEMA}" \
    kind             "uploads" \
    tier             "${TIER}" \
    created_at       "$(_bk_ts)" \
    artifact         "$(basename "${ARTIFACT}")" \
    format           "tar-gzip" \
    encryption       "${ENCRYPTION}" \
    sha256           "${_BK_PUBLISH_SHA256}" \
    size_bytes       "${_BK_PUBLISH_SIZE}" \
    duration_seconds "$(( END_EPOCH - START_EPOCH ))" \
    source           "$(uploads_source_description)" \
    file_count       "${FILE_COUNT}"

log "wrote ${ARTIFACT} ($(bk_human_size "${_BK_PUBLISH_SIZE}"), ${FILE_COUNT} files)"

"${BACKUP_LIB_DIR}/verify_backup.sh" --level checksum "${ARTIFACT}" >/dev/null \
    || die "the upload backup was written but failed its checksum — treat it as unusable"

bk_prune_tier "${DEST_DIR}" "$(bk_retention_for_tier "${TIER}")" "^uploads-.*\.tar\.gz(\.enc)?$"

log "upload backup complete"
exit 0

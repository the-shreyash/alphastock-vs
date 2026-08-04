#!/usr/bin/env bash
# ==============================================================================
# StockAssist AI — configuration & secret material backup (PH2.9)
#
#   ./scripts/backup/backup_config.sh
#   ./scripts/backup/backup_config.sh --list      # show what would be included
#
# WHAT PROBLEM THIS SOLVES
# ------------------------
# Restoring the database is only half of a recovery. A restored MongoDB is
# useless to an application that cannot start, and this application cannot start
# without JWT_SECRET, MONGO_APP_PASSWORD, CSRF_SECRET, BROKER_TOKEN_KEY and the
# third-party API keys — none of which are in git, by design, and none of which
# can be regenerated:
#
#   * JWT_SECRET regenerated  → every session token ever issued is invalid.
#     Every logged-in user is logged out at once.
#   * BROKER_TOKEN_KEY lost   → every stored broker token in the database is
#     permanently undecryptable. Every user has to re-link their broker.
#   * A provider API key lost → a support ticket and a wait, per provider.
#
# So the encryption keys ARE part of the data. A backup strategy that protects
# the database and not the key material protects the ciphertext and throws away
# the key.
#
# WHY ENCRYPTION IS MANDATORY HERE, WITH NO DEVELOPMENT EXEMPTION
# ---------------------------------------------------------------
# backup_mongo.sh permits an unencrypted artifact outside production, because a
# developer's local database contains seed data. This archive is different: it
# is one hundred percent credential material and nothing else. A plaintext copy
# of it in a backup directory — or, worse, synced to object storage — is a
# complete compromise of the deployment. There is no environment in which
# writing it unencrypted is the right default, so there is no flag for it.
#
# ⚠⚠ THE RECURSIVE-DEPENDENCY TRAP — the single most important paragraph here.
#
#   The passphrase that encrypts this archive CANNOT be stored in this archive,
#   in the repository, in the deployment's environment, in the secrets manager
#   this deployment uses, or on the host being backed up. Every one of those is
#   unavailable in the disaster this backup exists for.
#
#   It belongs in an OFFLINE ESCROW that survives the loss of the entire
#   deployment: a password manager owned by a person, a printed copy in a safe,
#   or a cloud KMS in a different account with different credentials. At least
#   two people must be able to reach it, or the company's recovery plan has a
#   single point of failure with a pulse.
#
#   "We could not decrypt our backups because the key was in the vault that was
#   down" is one of the most common ways a tested backup strategy still fails.
#
# WHAT IS DELIBERATELY *NOT* INCLUDED
# -----------------------------------
#   docker-compose*.yml, Dockerfiles, redis.conf, the init scripts — all tracked
#   in git. `git clone` restores them at the exact reviewed revision; a copy in a
#   backup tarball restores them at whatever they happened to be, which is worse
#   in the only case that matters. The tarball records the git commit instead, so
#   a recovery can check out the revision the secrets were captured against.
#
#   *.example files — tracked, and by definition carry no secrets.
# ==============================================================================
set -euo pipefail

# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

LIST_ONLY=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --list)    LIST_ONLY=1; shift ;;
        -h|--help) sed -n '2,8p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)         usage_error "unknown option: $1 (try --help)" ;;
    esac
done

bk_load_config

# ------------------------------------------------------------------------------
# What goes in.
#
# Paths are relative to the repository root and each is included only if it
# exists — a deployment that uses Docker secrets has `secrets/`, one that uses a
# plain env file has `.env`, and most have exactly one of the two.
# ------------------------------------------------------------------------------
CANDIDATES=(
    "secrets"               # Docker secret files (PH2.3) — the primary target
    ".env"                  # single-file deployments
    "compose.env"           # infrastructure credentials (PH2.2 two-file split)
    "production.env"        # production application secrets
    "backend/.env"          # legacy location, still read by load_dotenv
)

# Defaults to the repository root, which is where all five candidates live in
# every deployment this project ships. It is overridable for two real reasons:
# a deployment that keeps its secret files outside the checkout, and the test
# suite — which must be able to exercise this script without reading (or
# archiving) the machine's actual credentials.
CONFIG_SOURCE_ROOT="${BACKUP_CONFIG_SOURCE_ROOT:-${REPO_ROOT}}"
[[ -d "${CONFIG_SOURCE_ROOT}" ]] || die "BACKUP_CONFIG_SOURCE_ROOT is not a directory: ${CONFIG_SOURCE_ROOT}"

INCLUDE=()
for rel in "${CANDIDATES[@]}"; do
    if [[ -e "${CONFIG_SOURCE_ROOT}/${rel}" ]]; then
        INCLUDE[${#INCLUDE[@]}]="${rel}"
    fi
done

if [[ ${#INCLUDE[@]} -eq 0 ]]; then
    die "nothing to back up: none of ${CANDIDATES[*]} exist under ${CONFIG_SOURCE_ROOT}"
fi

if [[ ${LIST_ONLY} -eq 1 ]]; then
    log "would include (relative to ${CONFIG_SOURCE_ROOT}):"
    for rel in "${INCLUDE[@]}"; do
        printf '  %s\n' "${rel}"
        if [[ -d "${CONFIG_SOURCE_ROOT}/${rel}" ]]; then
            find "${CONFIG_SOURCE_ROOT}/${rel}" -type f ! -name '*.example' ! -name 'README.md' \
                | sed "s#${CONFIG_SOURCE_ROOT}/#    #"
        fi
    done
    exit 0
fi

# ------------------------------------------------------------------------------
# Encryption — hard requirement, checked before anything is read.
# ------------------------------------------------------------------------------
if ! bk_encryption_available; then
    die "refusing to write a configuration backup without encryption. This archive is credential material and nothing else. Set BACKUP_ENCRYPTION_PASSPHRASE_FILE — and read the RECURSIVE-DEPENDENCY warning at the top of this script before choosing where that passphrase lives."
fi
require_cmd openssl
require_cmd tar

# ------------------------------------------------------------------------------
# Destination
#
# Configuration changes on human timescales — a rotated key, a new provider —
# not nightly. It gets its own directory and its own retention count rather than
# the daily/weekly/monthly tiers, because "the last 20 versions of the secrets"
# is the useful window, and it costs kilobytes.
# ------------------------------------------------------------------------------
readonly DEST_DIR="${BACKUP_ROOT}/config"
mkdir -p "${DEST_DIR}"
chmod 700 "${BACKUP_ROOT}" "${DEST_DIR}" 2>/dev/null || true

TIMESTAMP="$(bk_timestamp)"
BASE="config-${TIMESTAMP}"
ARTIFACT="${DEST_DIR}/${BASE}.tar.gz.enc"
MANIFEST="${DEST_DIR}/${BASE}.manifest.json"

# Recording the revision the secrets were captured against is what makes them
# meaningful later: a recovery checks out this commit, then unpacks this archive
# over it, and gets a deployment whose code and configuration agree.
#
# Both `|| true`-guarded: the config source is not necessarily a git checkout
# (a deployment may keep its secrets outside the repository), and `set -e` turns
# a non-zero `git status` into an aborted BACKUP. Provenance is a nice-to-have;
# it must never be the reason the credentials did not get backed up.
GIT_COMMIT="$(git -C "${CONFIG_SOURCE_ROOT}" rev-parse HEAD 2>/dev/null || printf 'unknown')"
GIT_DIRTY="$(git -C "${CONFIG_SOURCE_ROOT}" status --porcelain 2>/dev/null | head -n 1 || true)"
if [[ -n "${GIT_DIRTY}" ]]; then
    warn "the working tree has uncommitted changes — the recorded commit ${GIT_COMMIT} does not fully describe this deployment"
fi

log "destination: ${ARTIFACT}"
log "including  : ${INCLUDE[*]}"
log "git commit : ${GIT_COMMIT}"

# ------------------------------------------------------------------------------
# Produce → encrypt → publish.
#
# `--exclude` drops the files that are in git anyway. Keeping them out is not
# about size (they are tiny) — it is about the archive containing ONLY things
# that cannot be recovered another way, so an operator unpacking it during an
# incident does not have to reason about which of these files is authoritative.
#
# `tar -C "${CONFIG_SOURCE_ROOT}"` so every path inside the archive is repository-relative
# and unpacks over a fresh clone with no path surgery. An archive of absolute
# paths is an archive that has to be rewritten before it can be used.
# ------------------------------------------------------------------------------
# shellcheck disable=SC2329  # invoked indirectly, as bk_publish_artifact's producer
config_producer() {
    tar -C "${CONFIG_SOURCE_ROOT}" \
        --exclude='*.example' \
        --exclude='README.md' \
        --exclude='generate.sh' \
        -czf - "${INCLUDE[@]}"
}

START_EPOCH="$(date -u '+%s')"
bk_publish_artifact "${ARTIFACT}" 1 config_producer \
    || die "configuration backup failed — nothing was written"
END_EPOCH="$(date -u '+%s')"

# ------------------------------------------------------------------------------
# Round-trip verification, which also produces the manifest's file list.
#
# Decryption is verified here rather than deferred to a separate step, because
# an unrecoverable secrets archive is indistinguishable from a recoverable one
# until the day it matters — and unlike the Mongo archive, this one is small
# enough to fully decrypt on every run. Reading the file list back out of the
# PUBLISHED artifact rather than re-running tar means the manifest describes
# what is actually in the file, not what the producer intended to put there.
#
# The manifest lists file NAMES and never contents or per-file hashes. A per-file
# hash would let anyone holding the (unencrypted) manifest confirm a guessed
# secret value offline — exactly the property the encryption was there to remove.
# ------------------------------------------------------------------------------
PASSPHRASE_FILE="$(bk_passphrase_file)"
FILE_LIST="$(bk_decrypt_filter "${PASSPHRASE_FILE}" < "${ARTIFACT}" | tar -tzf - 2>/dev/null | grep -v '/$' | sort | tr '\n' ' ' || true)"
[[ -n "${FILE_LIST}" ]] \
    || die "the configuration backup cannot be decrypted and listed — treat it as unusable"
FILE_COUNT="$(printf '%s' "${FILE_LIST}" | wc -w | tr -d '[:space:]')"
log "round-trip verified: decrypts and lists ${FILE_COUNT} files"

bk_manifest_write "${MANIFEST}" \
    schema           "${BACKUP_MANIFEST_SCHEMA}" \
    kind             "config" \
    tier             "config" \
    created_at       "$(_bk_ts)" \
    artifact         "$(basename "${ARTIFACT}")" \
    format           "tar-gzip" \
    encryption       "openssl-aes-256-cbc-pbkdf2-600000" \
    sha256           "${_BK_PUBLISH_SHA256}" \
    size_bytes       "${_BK_PUBLISH_SIZE}" \
    duration_seconds "$(( END_EPOCH - START_EPOCH ))" \
    git_commit       "${GIT_COMMIT}" \
    file_count       "${FILE_COUNT}" \
    files            "${FILE_LIST}"

log "wrote ${ARTIFACT} ($(bk_human_size "${_BK_PUBLISH_SIZE}"), ${FILE_COUNT} files)"
log "sha256 ${_BK_PUBLISH_SHA256}"

if ! "${BACKUP_LIB_DIR}/verify_backup.sh" --level checksum "${ARTIFACT}" >/dev/null; then
    die "the configuration backup was written but failed its checksum — treat it as unusable"
fi

bk_prune_tier "${DEST_DIR}" "${BACKUP_RETAIN_CONFIG:-20}" "^config-.*\.tar\.gz\.enc$"

printf '\n' >&2
warn "REMINDER: this archive is worthless without its passphrase, and the passphrase must NOT live on this host, in this repository, or in this deployment's secret store. See docs/operations/BACKUP_AND_RESTORE.md §Configuration & Secret Recovery."
exit 0

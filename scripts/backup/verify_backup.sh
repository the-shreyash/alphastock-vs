#!/usr/bin/env bash
# ==============================================================================
# StockAssist AI — backup verification (PH2.9)
#
#   ./scripts/backup/verify_backup.sh --level checksum   <artifact>
#   ./scripts/backup/verify_backup.sh --level structural <artifact>   # default
#   ./scripts/backup/verify_backup.sh --level drill      <artifact>
#   ./scripts/backup/verify_backup.sh --latest --level drill
#   ./scripts/backup/verify_backup.sh --all --level checksum
#
# WHAT PROBLEM THIS SOLVES
# ------------------------
# The overwhelming majority of backup failures are not "the job did not run".
# They are "the job ran every night for fourteen months and produced files that
# cannot be restored" — a wrong credential that dumped an empty database, a
# passphrase rotated without re-keying, a full disk that truncated every
# artifact, a missing `-T` that CRLF-mangled a binary stream. Every one of those
# produces a file of plausible size with a recent mtime, and a monitoring check
# that looks at "was a file written" reports green through all of them.
#
# The only thing that distinguishes a backup from a file is a restore. This
# script is the graduated approximation of one.
#
# THREE LEVELS, BECAUSE VERIFICATION HAS A COST CURVE
# ---------------------------------------------------
#   checksum    Manifest is present and readable, its schema is understood, and
#               the artifact's SHA-256 still matches what was recorded when it
#               was written. Catches bit rot, truncation, and a partial transfer
#               to off-host storage. Offline, no database, ~1s.
#
#   structural  checksum, plus: decrypt the artifact and run the ENTIRE
#               decompressed stream through gzip's CRC (`gzip -t`), then confirm
#               the mongodump archive magic number at its head. This proves the
#               passphrase is correct, the ciphertext is intact end to end, and
#               the payload really is a mongodump archive rather than an error
#               message that got captured into a file. Still offline and still
#               needs no database — which matters, because the host you verify a
#               backup on during a disaster may not have one yet. ~seconds.
#               ← the level the nightly backup job runs
#
#   drill       structural, plus an ACTUAL restore into a scratch database on a
#               live server, a per-collection comparison of restored document
#               counts against the manifest's baseline, and the removal of the
#               scratch database afterwards. This is the only level that is
#               evidence rather than inference, and the only one that produces a
#               real RTO measurement. ~minutes.
#               ← the level the monthly drill runs
#
# WHY THE SCRATCH DATABASE IS NOT OPTIONAL
# ----------------------------------------
# The intuitive drill restores over the real database on a staging host. That
# makes the drill itself a risk, so it gets run rarely, so it stops being run at
# all. Restoring into `<db>__drill_<timestamp>` via mongorestore's `--nsFrom` /
# `--nsTo` namespace remapping makes the drill non-destructive by construction,
# which is what makes it safe to run monthly — and a drill you actually run is
# worth more than a perfect drill you do not.
# ==============================================================================
set -euo pipefail

# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

LEVEL="structural"
TARGETS=()
SELECT_LATEST=0
SELECT_ALL=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --level)   LEVEL="${2:-}"; shift 2 ;;
        --latest)  SELECT_LATEST=1; shift ;;
        --all)     SELECT_ALL=1; shift ;;
        -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*)        usage_error "unknown option: $1 (try --help)" ;;
        *)         TARGETS[${#TARGETS[@]}]="$1"; shift ;;
    esac
done

case "${LEVEL}" in
    checksum|structural|drill) ;;
    *) usage_error "--level must be checksum, structural or drill (got '${LEVEL}')" ;;
esac

bk_load_config

# ------------------------------------------------------------------------------
# Target selection
# ------------------------------------------------------------------------------
bk_all_artifacts() {
    find "${BACKUP_ROOT}/mongo" -type f \
        \( -name '*.archive.gz' -o -name '*.archive.gz.enc' \) 2>/dev/null | sort
}

if [[ ${SELECT_ALL} -eq 1 ]]; then
    while IFS= read -r f; do
        if [[ -n "$f" ]]; then TARGETS[${#TARGETS[@]}]="$f"; fi
    done <<< "$(bk_all_artifacts)"
elif [[ ${SELECT_LATEST} -eq 1 ]]; then
    # Newest across ALL tiers. The filename carries a sortable UTC timestamp, so
    # this is a time sort that does not trust mtime — which an off-host copy,
    # an rsync or a restore from object storage would have rewritten.
    latest="$(bk_all_artifacts | sed 's#.*/##' | sort -r | head -n 1)"
    [[ -n "$latest" ]] || die "no backups found under ${BACKUP_ROOT}/mongo"
    TARGETS[${#TARGETS[@]}]="$(bk_all_artifacts | grep -F "/${latest}" | head -n 1)"
fi

[[ ${#TARGETS[@]} -gt 0 ]] || usage_error "no artifact given (pass a path, or --latest / --all)"

# ------------------------------------------------------------------------------
# Level 1 — checksum
# ------------------------------------------------------------------------------
verify_checksum() {
    local artifact="$1" manifest schema recorded actual

    [[ -f "${artifact}" ]] || { err "missing artifact: ${artifact}"; return 1; }
    [[ -s "${artifact}" ]] || { err "artifact is empty: ${artifact}"; return 1; }

    manifest="$(dirname "${artifact}")/$(bk_artifact_base "$(basename "${artifact}")").manifest.json"
    [[ -f "${manifest}" ]] || { err "missing manifest for ${artifact} (expected ${manifest})"; return 1; }

    # A manifest from a future schema may use the same field names with different
    # meanings. Refusing is the only safe response: a verifier that reports OK
    # because it silently misread a field is worse than one that reports an
    # error, because the error gets fixed.
    schema="$(bk_manifest_get "${manifest}" schema)"
    [[ "${schema}" == "${BACKUP_MANIFEST_SCHEMA}" ]] \
        || { err "manifest schema ${schema:-<absent>} != ${BACKUP_MANIFEST_SCHEMA} (this tool cannot verify it)"; return 1; }

    recorded="$(bk_manifest_get "${manifest}" sha256)"
    [[ -n "${recorded}" ]] || { err "manifest records no sha256: ${manifest}"; return 1; }

    actual="$(bk_sha256 "${artifact}")"
    if [[ "${actual}" != "${recorded}" ]]; then
        err "CHECKSUM MISMATCH on ${artifact}"
        err "  recorded ${recorded}"
        err "  actual   ${actual}"
        err "  the artifact has changed since it was written — treat it as unusable"
        return 1
    fi

    log "  checksum   OK  ($(bk_human_size "$(bk_file_size "${artifact}")"))"
    return 0
}

# ------------------------------------------------------------------------------
# Level 2 — structural
#
# Reads the artifact ONCE as a stream: decrypt (if encrypted) → gzip -t.
#
# `gzip -t` is doing much more work here than it appears to. mongodump's
# `--archive --gzip` gzips the whole archive stream, so gzip's CRC-32 covers
# every byte of the payload; a single flipped bit anywhere in a 40 GB artifact
# fails it. It is also, in the encrypted case, the closest thing this scheme has
# to an authentication tag: AES-CBC will happily "decrypt" with a wrong
# passphrase and emit garbage, and garbage does not pass a CRC.
#
# The magic-number check that follows catches the one thing a CRC cannot: a
# perfectly valid gzip file whose contents are not a mongodump archive. That
# happens more often than it sounds — a shell redirection that captured an error
# message, or a `--db` typo that dumped nothing.
# ------------------------------------------------------------------------------
readonly MONGO_ARCHIVE_MAGIC="6de29981"   # little-endian 0x8199e26d

verify_structural() {
    local artifact="$1" encryption manifest pf magic
    verify_checksum "${artifact}" || return 1

    require_cmd gzip
    manifest="$(dirname "${artifact}")/$(bk_artifact_base "$(basename "${artifact}")").manifest.json"
    encryption="$(bk_manifest_get "${manifest}" encryption)"

    local wd; wd="$(bk_workdir)"

    if [[ "${encryption}" != "none" && -n "${encryption}" ]]; then
        bk_encryption_available \
            || { err "artifact is encrypted (${encryption}) but no passphrase is configured — set BACKUP_ENCRYPTION_PASSPHRASE_FILE"; return 1; }
        require_cmd openssl
        pf="$(bk_passphrase_file)"
        if ! bk_decrypt_filter "${pf}" < "${artifact}" > "${wd}/plain.gz" 2>"${wd}/openssl.err"; then
            err "DECRYPTION FAILED for ${artifact}"
            err "  $(tail -n 1 "${wd}/openssl.err" 2>/dev/null || true)"
            err "  the passphrase in use does not match the one this artifact was written with"
            return 1
        fi
    else
        # A symlink, not a copy: a 40 GB artifact should not be duplicated just
        # to give the next two steps a stable path.
        ln -sf "$(cd "$(dirname "${artifact}")" && pwd)/$(basename "${artifact}")" "${wd}/plain.gz"
    fi

    if ! gzip -t "${wd}/plain.gz" 2>"${wd}/gzip.err"; then
        err "GZIP INTEGRITY FAILED for ${artifact}"
        err "  $(tail -n 1 "${wd}/gzip.err" 2>/dev/null || true)"
        return 1
    fi

    magic="$(gzip -cd "${wd}/plain.gz" 2>/dev/null | od -An -tx1 -N4 | tr -d ' \n')"
    if [[ "${magic}" != "${MONGO_ARCHIVE_MAGIC}" ]]; then
        err "NOT A MONGODUMP ARCHIVE: ${artifact}"
        err "  leading bytes ${magic:-<empty>}, expected ${MONGO_ARCHIVE_MAGIC}"
        return 1
    fi

    rm -f "${wd}/plain.gz"
    log "  structural OK  (decrypted, CRC verified, mongodump archive confirmed)"
    return 0
}

# ------------------------------------------------------------------------------
# Level 3 — drill
# ------------------------------------------------------------------------------
verify_drill() {
    local artifact="$1" manifest database expected scratch pf
    local start_epoch end_epoch duration rc=0

    verify_structural "${artifact}" || return 1

    manifest="$(dirname "${artifact}")/$(bk_artifact_base "$(basename "${artifact}")").manifest.json"
    database="$(bk_manifest_get "${manifest}" database)"
    [[ -n "${database}" ]] || { err "manifest records no database name"; return 1; }

    expected="$(bk_manifest_get "${manifest}" collections)"
    [[ -n "${expected}" && "${expected}" != "{}" ]] \
        || { err "manifest has no collection baseline — this artifact cannot be drilled, only structurally verified"; return 1; }

    scratch="${database}__drill_$(bk_timestamp)"
    # The scratch name is constructed two lines above, so this can only fail if
    # someone edits that line. It is checked anyway, because the next statement
    # after the restore is a dropDatabase and the cost of being wrong there is
    # the production database.
    case "${scratch}" in
        *__drill_*) ;;
        *) die "internal error: refusing to drill into a database whose name is not a scratch name (${scratch})" ;;
    esac

    log "  drill: restoring into scratch database '${scratch}'…"
    bk_prepare_mongo
    start_epoch="$(date -u '+%s')"

    # --nsFrom/--nsTo remap every namespace out of the real database name, so
    # nothing this command does can touch live data even if it is pointed at a
    # production server. --drop applies to the SCRATCH namespaces only.
    #
    # --noIndexRestore is deliberately NOT passed: index build time is a real
    # and often dominant part of RTO, and a drill that skips it produces an RTO
    # figure that is wrong in the optimistic direction.
    if [[ "$(bk_manifest_get "${manifest}" encryption)" != "none" ]]; then
        pf="$(bk_passphrase_file)"
        bk_decrypt_filter "${pf}" < "${artifact}" \
            | bk_mongo_tool mongorestore --archive --gzip --quiet --drop \
                --nsFrom="${database}.*" --nsTo="${scratch}.*" || rc=$?
    else
        bk_mongo_tool mongorestore --archive --gzip --quiet --drop \
            --nsFrom="${database}.*" --nsTo="${scratch}.*" < "${artifact}" || rc=$?
    fi

    end_epoch="$(date -u '+%s')"
    duration=$(( end_epoch - start_epoch ))

    if [[ $rc -ne 0 ]]; then
        err "RESTORE FAILED (exit ${rc}) — dropping the scratch database"
        bk_drop_scratch "${scratch}"
        return 1
    fi

    log "  drill: restore completed in ${duration}s — comparing against the manifest baseline"

    # The comparison runs as JavaScript inside mongosh rather than as shell JSON
    # parsing: the baseline is already JSON, mongosh already speaks JSON, and a
    # hand-rolled shell JSON parser is a bug generator. Every line it prints is
    # `STATUS collection expected actual`, which the shell reads as records.
    local report
    report="$(bk_mongo_eval "
        const expected = ${expected};
        const d = db.getSiblingDB('${scratch}');
        const present = {};
        d.getCollectionNames().forEach(function (c) { present[c] = true; });
        Object.keys(expected).sort().forEach(function (c) {
            if (!present[c]) { print('MISSING ' + c + ' ' + expected[c] + ' -'); return; }
            const n = d.getCollection(c).countDocuments({});
            if (n === expected[c])      print('MATCH ' + c + ' ' + expected[c] + ' ' + n);
            else if (expected[c] > 0 && n === 0) print('EMPTY ' + c + ' ' + expected[c] + ' ' + n);
            else                        print('DRIFT ' + c + ' ' + expected[c] + ' ' + n);
        });
    " 2>/dev/null | tr -d '\r' | grep -E '^(MATCH|MISSING|EMPTY|DRIFT) ' || true)"

    local matched=0 drifted=0 failed=0 status name exp act
    while IFS=' ' read -r status name exp act; do
        [[ -n "${status}" ]] || continue
        case "${status}" in
            MATCH)   matched=$((matched + 1)) ;;
            DRIFT)
                # A live source database is written to while it is being dumped,
                # so a small difference is the system working, not a fault. It is
                # reported at every drill so a *growing* drift is visible.
                drifted=$((drifted + 1))
                warn "    drift: ${name} manifest=${exp} restored=${act}"
                ;;
            MISSING) failed=$((failed + 1)); err "    MISSING collection after restore: ${name} (expected ${exp} documents)" ;;
            EMPTY)   failed=$((failed + 1)); err "    EMPTY after restore: ${name} (expected ${exp} documents, got 0)" ;;
        esac
    done <<< "${report}"

    bk_drop_scratch "${scratch}"

    if [[ -z "${report}" ]]; then
        err "  drill: the comparison produced no output — the restored database could not be inspected"
        return 1
    fi
    if [[ ${failed} -gt 0 ]]; then
        err "  drill FAILED: ${failed} collection(s) missing or empty after restore"
        return 1
    fi

    log "  drill      OK  (${matched} collections matched, ${drifted} within live-write drift, restore ${duration}s)"
    printf 'DRILL_RESTORE_SECONDS=%s\n' "${duration}"
    return 0
}

# Drops a scratch database, and ONLY a scratch database.
#
# The guard is not decoration. This function is called on the failure path,
# where the variable holding the name is most likely to be empty or unexpected,
# and `db.getSiblingDB('').dropDatabase()` against a production cluster is not a
# recoverable mistake.
bk_drop_scratch() {
    local name="$1"
    case "${name}" in
        *__drill_*) ;;
        *) err "refusing to drop '${name}': not a scratch database name"; return 1 ;;
    esac
    log "  drill: dropping scratch database '${name}'"
    bk_mongo_eval "db.getSiblingDB('${name}').dropDatabase();" >/dev/null 2>&1 \
        || warn "could not drop scratch database '${name}' — remove it manually"
}

# ------------------------------------------------------------------------------
# Run
# ------------------------------------------------------------------------------
PASSED=0
FAILED=0

for target in "${TARGETS[@]}"; do
    log "verifying (${LEVEL}): ${target}"
    ok=0
    case "${LEVEL}" in
        checksum)   verify_checksum   "${target}" && ok=1 || ok=0 ;;
        structural) verify_structural "${target}" && ok=1 || ok=0 ;;
        drill)      verify_drill      "${target}" && ok=1 || ok=0 ;;
    esac
    if [[ ${ok} -eq 1 ]]; then PASSED=$((PASSED + 1)); else FAILED=$((FAILED + 1)); fi
done

log "verification summary: ${PASSED} passed, ${FAILED} failed (level=${LEVEL})"
[[ ${FAILED} -eq 0 ]] || exit 1
exit 0

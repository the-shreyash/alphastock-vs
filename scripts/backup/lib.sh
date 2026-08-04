# shellcheck shell=bash
# ==============================================================================
# StockAssist AI — shared backup/restore library (PH2.9)
#
# WHY THIS FILE EXISTS
# --------------------
# Five scripts in this directory all need the same six things: where backups
# live, how to reach MongoDB, how to encrypt a stream, how to checksum an
# artifact, how to write and read a manifest, and how to refuse to do something
# destructive. Duplicating those across five files guarantees they drift, and
# the drift is not cosmetic — the day `restore_mongo.sh` decrypts differently
# from how `backup_mongo.sh` encrypted, the backups are gone and nobody finds
# out until the restore. One definition, sourced by all of them, means the
# encrypt and decrypt paths are provably the same code.
#
# This file is SOURCED, never executed. It defines functions and defaults and
# performs no side effects beyond that, so sourcing it is always safe.
#
# ⚠ BASH 3.2 COMPATIBLE, deliberately.
#   macOS still ships bash 3.2 (2007) as /bin/bash for licensing reasons, and
#   developers run these scripts on macOS while production runs Linux with bash
#   5.x. A script that only works on one of them is a script whose behaviour is
#   first observed during an incident. So: no associative arrays (`declare -A`),
#   no `mapfile`/`readarray`, no `${var^^}`, no `&>>`. Indexed arrays, `[[ ]]`
#   and `local` are all fine in 3.2.
#
# Full documentation: docs/operations/BACKUP_AND_RESTORE.md
# ==============================================================================

# ------------------------------------------------------------------------------
# Paths
#
# Resolved from THIS file's location, not from $PWD. A backup script invoked by
# cron runs with $PWD set to the crontab owner's home directory, and every
# relative path in it would then resolve somewhere unintended — the classic way
# a backup silently writes into /root and fills a disk.
# ------------------------------------------------------------------------------
BACKUP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${BACKUP_LIB_DIR}/../.." && pwd)"
readonly BACKUP_LIB_DIR REPO_ROOT

# Schema version of the manifest format. Bumped when a field's MEANING changes,
# never for an addition. `verify_backup.sh` refuses a manifest whose major
# version it does not understand rather than silently misreading a field — a
# verifier that reports "OK" because it could not find the checksum field is
# worse than one that reports an error.
#
# shellcheck disable=SC2034  # read by the scripts that source this file
readonly BACKUP_MANIFEST_SCHEMA="1"

# ------------------------------------------------------------------------------
# Logging
#
# Everything goes to stderr except the machine-readable values a script is
# designed to emit on stdout (an artifact path, a manifest field). Keeping the
# two apart is what lets `PATH=$(backup_mongo.sh --print-path)` work while the
# human-readable progress still reaches the operator's terminal and the cron
# mail.
# ------------------------------------------------------------------------------
_bk_ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

log()  { printf '[%s] %-5s %s\n' "$(_bk_ts)" "INFO" "$*" >&2; }
warn() { printf '[%s] %-5s %s\n' "$(_bk_ts)" "WARN" "$*" >&2; }
err()  { printf '[%s] %-5s %s\n' "$(_bk_ts)" "ERROR" "$*" >&2; }

# Exit code 1 is a genuine failure (a backup did not happen). Exit code 2 is
# reserved for usage errors — a typo'd flag. Monitoring can therefore alert on
# 1 and page nobody for 2.
die()   { err "$*"; exit 1; }
usage_error() { err "$*"; exit 2; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1 — see docs/operations/BACKUP_AND_RESTORE.md §Prerequisites"
}

# ------------------------------------------------------------------------------
# Temporary working directory
#
# One per process, mode 700, removed by an EXIT trap that fires on success,
# failure AND on Ctrl-C. Passphrase files and partial artifacts live here, and
# "the backup script was interrupted and left the encryption passphrase in
# /tmp" is a real incident, not a hypothetical.
# ------------------------------------------------------------------------------
# A SINGLE cleanup function behind a single trap.
#
# Registering `trap 'rm -rf ...' EXIT` in two places does not chain — the second
# trap silently replaces the first, and the resource the first one owned leaks
# forever. Everything that needs teardown registers itself with a flag that this
# function checks.
_bk_cleanup() {
    local rc=$?
    if [[ -n "${_BK_CONTAINER_CONFIG:-}" ]]; then
        # Best effort: the container may already be gone, which is fine — so is
        # the file inside it.
        docker compose -f "${BACKUP_COMPOSE_FILE}" exec -T "${BACKUP_MONGO_SERVICE}" \
            rm -f "${_BK_CONTAINER_CONFIG}" >/dev/null 2>&1 || true
        _BK_CONTAINER_CONFIG=""
    fi
    [[ -n "${_BK_WORKDIR:-}" ]] && rm -rf "${_BK_WORKDIR}"
    return $rc
}

# ⚠ MUST be called from the MAIN SHELL, never from inside `$( … )`.
#
# A command substitution runs in a subshell, and bash fires an EXIT trap when a
# subshell exits. If the working directory were created lazily inside
# `pf="$(bk_passphrase_file)"`, the mktemp would run in that subshell, the trap
# would be registered in that subshell, and the directory would be deleted the
# instant the substitution closed — leaving the caller holding a path to
# nothing. (This is not hypothetical: it is exactly how the first version of
# this library failed its first end-to-end run.)
#
# bk_load_config calls this, so every script gets it in the main shell for free
# and every later `$(bk_workdir)` is a pure read.
bk_init_workdir() {
    [[ -n "${_BK_WORKDIR:-}" ]] && return 0
    _BK_WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/stockassist-backup.XXXXXXXX")"
    chmod 700 "${_BK_WORKDIR}"
    trap _bk_cleanup EXIT INT TERM
    return 0
}

bk_workdir() {
    [[ -n "${_BK_WORKDIR:-}" ]] || die "internal error: bk_init_workdir was not called from the main shell"
    printf '%s' "${_BK_WORKDIR}"
}

# ------------------------------------------------------------------------------
# Configuration
#
# WHY BACKUP SETTINGS ARE **NOT** IN backend/security/secrets.py
# ---------------------------------------------------------------
# That registry is the authoritative inventory of the APPLICATION's
# configuration surface — the variables the FastAPI process reads, validated
# fail-closed at boot and mirrored into `.env.example` by a generator that CI
# drift-checks. BACKUP_ROOT and BACKUP_RETAIN_DAILY are read by a shell script
# on the host; the application neither reads them nor should fail to boot
# because one is missing. Registering them there would put host-operations
# settings into the app's `.env.example`, imply the app validates them (it does
# not), and blur a boundary that is currently clean.
#
# They are ordinary environment variables, documented in
# docs/operations/BACKUP_AND_RESTORE.md §Configuration, and this loader will
# additionally read the repository `.env` so an operator does not have to state
# MONGO_DB_NAME twice.
# ------------------------------------------------------------------------------

# Reads KEY=VALUE lines from a file into the environment WITHOUT sourcing it.
#
# `source .env` is the obvious implementation and it is a remote-code-execution
# primitive: a line like `FOO=$(rm -rf /)` in a file that half the team can
# write executes as whoever runs the backup — which is root on most hosts. This
# parses instead: comments and blanks skipped, only well-formed shell-safe keys
# accepted, surrounding quotes stripped, and an ALREADY-SET variable is never
# overwritten (so an explicit `BACKUP_ROOT=... ./backup_mongo.sh` beats the file,
# which is the precedence every operator expects).
bk_load_env_file() {
    local file="$1" line key value
    [[ -r "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            ''|'#'*) continue ;;
        esac
        [[ "$line" == *=* ]] || continue
        key="${line%%=*}"
        value="${line#*=}"
        key="$(printf '%s' "$key" | tr -d '[:space:]')"
        key="${key#export}"
        # Reject anything that is not a plain shell identifier. This is what
        # stops `a b=c`, `PATH[0]=x` and other shapes from reaching `eval`-like
        # handling downstream.
        case "$key" in
            ''|*[!A-Za-z0-9_]*) continue ;;
            [0-9]*) continue ;;
        esac
        # Strip one layer of matching quotes; leave inner content untouched.
        case "$value" in
            \"*\") value="${value%\"}"; value="${value#\"}" ;;
            \'*\') value="${value%\'}"; value="${value#\'}" ;;
        esac
        # Never clobber an explicitly-exported value.
        if [[ -z "$(eval "printf '%s' \"\${${key}:-}\"")" ]]; then
            export "${key}=${value}"
        fi
    done < "$file"
}

bk_load_config() {
    bk_load_env_file "${REPO_ROOT}/.env"

    # WHERE BACKUPS GO.
    # Default is inside the repository only because that is the one directory
    # guaranteed to exist on a developer laptop. It is git-ignored, and §Storage
    # of the documentation is emphatic that a production BACKUP_ROOT must be on
    # a DIFFERENT FILESYSTEM from the Docker volumes — a backup that shares a
    # disk with the database it protects survives exactly the failures that do
    # not matter (a bad migration) and none of the ones that do (a dead disk).
    BACKUP_ROOT="${BACKUP_ROOT:-${REPO_ROOT}/backups}"

    # Which database. Matches the compose variable of the same name so the two
    # cannot disagree.
    MONGO_DB_NAME="${MONGO_DB_NAME:-alpha_stock}"

    # `docker` (default) or `direct`.
    #
    #   docker  — run mongodump INSIDE the mongo container via `compose exec -T`
    #             and stream the archive to the host over stdout. This is the
    #             only mode that works against the production stack, because
    #             docker-compose.yml puts mongo on an `internal: true` network
    #             with no published ports: there is no host-reachable socket by
    #             design, and adding one for the backup would undo that.
    #   direct  — run mongodump on this host against MONGO_URL. For Atlas, a
    #             managed instance, or a developer's local mongod.
    BACKUP_MODE="${BACKUP_MODE:-docker}"

    # Compose wiring for `docker` mode.
    BACKUP_COMPOSE_FILE="${BACKUP_COMPOSE_FILE:-${REPO_ROOT}/docker-compose.yml}"
    BACKUP_MONGO_SERVICE="${BACKUP_MONGO_SERVICE:-mongo}"
    BACKUP_REDIS_SERVICE="${BACKUP_REDIS_SERVICE:-redis}"

    # GRANDFATHER-FATHER-SON RETENTION, COUNT-BASED PER TIER.
    #
    # Note this deliberately DIFFERS from the log retention in PH2.6, which
    # applies age first and count second. Logs carry a legal commitment phrased
    # in wall-clock time ("audit records kept 365 days"), so age must win.
    # Backups carry a recovery commitment phrased in coverage ("we can restore
    # to any of the last 7 days, any of the last 4 weeks"), and coverage is a
    # count. Pruning a daily backup because it turned 8 days old, on a week when
    # the job failed twice, would silently reduce coverage to five points while
    # every wall-clock rule still passed. Count-based retention cannot do that:
    # 7 means seven restorable artifacts, however long it took to collect them.
    BACKUP_RETAIN_DAILY="${BACKUP_RETAIN_DAILY:-7}"
    BACKUP_RETAIN_WEEKLY="${BACKUP_RETAIN_WEEKLY:-4}"
    BACKUP_RETAIN_MONTHLY="${BACKUP_RETAIN_MONTHLY:-6}"

    # Encryption passphrase. Either the value or a path to a file holding it;
    # the file form is strongly preferred because an environment variable is
    # visible in `/proc/<pid>/environ` and inherited by every child process,
    # including mongodump.
    BACKUP_ENCRYPTION_PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:-}"
    BACKUP_ENCRYPTION_PASSPHRASE_FILE="${BACKUP_ENCRYPTION_PASSPHRASE_FILE:-}"

    # Environment profile. In `production`, encryption is MANDATORY: the script
    # refuses to write a plaintext database dump rather than doing it with a
    # warning nobody reads in a cron mail nobody opens.
    APP_ENV="${APP_ENV:-production}"

    export BACKUP_ROOT MONGO_DB_NAME BACKUP_MODE APP_ENV

    # In the main shell, where it must be. See bk_init_workdir.
    bk_init_workdir
}

# ------------------------------------------------------------------------------
# Encryption
#
# THE DECISION: OpenSSL AES-256-CBC with PBKDF2 (600 000 iterations), ONE path.
#
# The tempting design offers gpg when present and openssl otherwise. It is a
# trap: the encrypt path is exercised nightly and the decrypt path is exercised
# during an outage, so a two-tool scheme means the restore is the first time
# anyone discovers the recovery host has only one of the two tools. One path
# that is always available beats a better path that is sometimes available.
#
# openssl is present on every Linux distribution, every macOS, and in the mongo
# image itself. gpg is not, and `age` — genuinely the better modern choice — is
# installed almost nowhere by default.
#
# ⚠ HONEST LIMITATION: CBC provides CONFIDENTIALITY, NOT AUTHENTICITY.
#   It detects accidental corruption (via the checksums below and gzip's own
#   CRC) but not deliberate tampering by someone who can rewrite both the
#   artifact and its manifest. For backups sitting in object storage with
#   write-once/object-lock enabled, that is an acceptable trade. If the backup
#   store is not write-once, see §Known Limitations for the age/gpg migration —
#   the manifest records `encryption` per artifact precisely so a future format
#   can be introduced without orphaning today's files.
# ------------------------------------------------------------------------------

# Materializes the passphrase into a mode-600 file inside the workdir and echoes
# its path. Callers pass it to openssl as `-pass file:...`, never as
# `-pass pass:...` — the latter puts the passphrase in argv, where `ps` shows it
# to every user on the host.
bk_passphrase_file() {
    local wd pf
    wd="$(bk_workdir)"
    pf="${wd}/passphrase"

    if [[ -n "${BACKUP_ENCRYPTION_PASSPHRASE_FILE}" ]]; then
        [[ -r "${BACKUP_ENCRYPTION_PASSPHRASE_FILE}" ]] \
            || die "BACKUP_ENCRYPTION_PASSPHRASE_FILE is not readable: ${BACKUP_ENCRYPTION_PASSPHRASE_FILE}"
        # Strip a trailing newline. An editor adds one silently, openssl treats
        # it as part of the passphrase, and the result is an artifact that can
        # only be decrypted by whoever reproduces the same trailing byte. This
        # single line prevents a whole category of "the passphrase is right but
        # it will not decrypt" incidents.
        printf '%s' "$(cat "${BACKUP_ENCRYPTION_PASSPHRASE_FILE}")" > "${pf}"
    elif [[ -n "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]; then
        printf '%s' "${BACKUP_ENCRYPTION_PASSPHRASE}" > "${pf}"
    else
        return 1
    fi

    chmod 600 "${pf}"
    [[ -s "${pf}" ]] || die "encryption passphrase resolved to an empty value"
    printf '%s' "${pf}"
}

bk_encryption_available() {
    [[ -n "${BACKUP_ENCRYPTION_PASSPHRASE_FILE}" || -n "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]
}

# Decides — and enforces — whether this run encrypts.
#
# Returns 0 (encrypt) or 1 (plaintext). In production, "no passphrase" is a
# hard failure, not a downgrade: a plaintext dump of the users collection is a
# breach waiting for whoever finds the backup directory.
bk_should_encrypt() {
    if bk_encryption_available; then
        require_cmd openssl
        return 0
    fi
    if [[ "${APP_ENV}" == "production" ]]; then
        die "refusing to write an UNENCRYPTED backup in APP_ENV=production — set BACKUP_ENCRYPTION_PASSPHRASE_FILE (see docs/operations/BACKUP_AND_RESTORE.md §Encryption)"
    fi
    warn "no BACKUP_ENCRYPTION_PASSPHRASE[_FILE] set — writing a PLAINTEXT backup (allowed only because APP_ENV=${APP_ENV})"
    return 1
}

# Filter: stdin -> encrypted stdout.
bk_encrypt_filter() {
    local pf="$1"
    openssl enc -aes-256-cbc -salt -pbkdf2 -iter 600000 -md sha512 -pass "file:${pf}"
}

# Filter: encrypted stdin -> plaintext stdout. Same parameters, one definition
# away from the encrypt side so they cannot drift apart.
bk_decrypt_filter() {
    local pf="$1"
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha512 -pass "file:${pf}"
}

# ------------------------------------------------------------------------------
# Checksums
#
# `sha256sum` on Linux, `shasum -a 256` on macOS. Both exist somewhere on both
# platforms in different packages, so probe rather than assume.
# ------------------------------------------------------------------------------
bk_sha256() {
    local file="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$file" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$file" | awk '{print $1}'
    else
        die "no sha256 tool found (need sha256sum or shasum)"
    fi
}

bk_file_size() {
    # `stat` flags differ between GNU and BSD; wc -c is identical everywhere and
    # a backup artifact is read once here, not in a loop.
    wc -c < "$1" | tr -d '[:space:]'
}

bk_human_size() {
    local bytes="$1"
    awk -v b="$bytes" 'BEGIN {
        split("B KB MB GB TB", u, " "); i = 1
        while (b >= 1024 && i < 5) { b /= 1024; i++ }
        printf (i == 1 ? "%d %s" : "%.1f %s"), b, u[i]
    }'
}

# ------------------------------------------------------------------------------
# Manifest
#
# A backup artifact alone cannot answer the questions asked during a restore:
# which database is this, when was it taken, was it encrypted, is it intact,
# and how much data should be there when I am done. The manifest answers all
# five, and the last one is the important one — comparing restored collection
# counts against counts recorded AT DUMP TIME is what turns "mongorestore exited
# 0" into "the data is actually there".
#
# Hand-rolled JSON rather than jq: jq is not installed on a minimal production
# host, and a backup script that cannot run because a JSON formatter is missing
# is a backup script that does not run.
# ------------------------------------------------------------------------------

# Escapes a value for inclusion in a JSON string. Handles the characters that
# actually occur here (quotes, backslashes, control chars); a database name or
# an ISO timestamp will never contain anything else.
bk_json_escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr -d '\000-\037'
}

# bk_manifest_write <path> <key> <value> [<key> <value> ...]
#
# Values that look like a JSON number or a JSON array/object are emitted raw;
# everything else is quoted. That keeps `size_bytes` a number (so a monitoring
# scraper can compare it) without needing a type system.
bk_manifest_write() {
    local path="$1"; shift
    local first=1 key value
    {
        printf '{\n'
        while [[ $# -gt 0 ]]; do
            key="$1"; value="$2"; shift 2
            [[ $first -eq 1 ]] || printf ',\n'
            first=0
            case "$value" in
                ''|*[!0-9]*)
                    case "$value" in
                        \[*\]|\{*\}) printf '  "%s": %s' "$(bk_json_escape "$key")" "$value" ;;
                        *)           printf '  "%s": "%s"' "$(bk_json_escape "$key")" "$(bk_json_escape "$value")" ;;
                    esac
                    ;;
                *) printf '  "%s": %s' "$(bk_json_escape "$key")" "$value" ;;
            esac
        done
        printf '\n}\n'
    } > "${path}"
    chmod 600 "${path}"
}

# bk_manifest_get <manifest-path> <key>
#
# Deliberately NOT a general JSON parser. It reads exactly the flat,
# one-key-per-line shape bk_manifest_write produces, and returns empty for
# anything it cannot find — callers treat empty as "field absent" and fail
# rather than proceeding on a guess.
bk_manifest_get() {
    local path="$1" key="$2"
    [[ -r "$path" ]] || return 0
    sed -n "s/^[[:space:]]*\"${key}\"[[:space:]]*:[[:space:]]*\(.*\)$/\1/p" "$path" \
        | head -n 1 \
        | sed -e 's/,[[:space:]]*$//' -e 's/^"//' -e 's/"$//'
}

# ------------------------------------------------------------------------------
# Artifact publication
#
# Every backup in this system — Mongo archive, config tarball, uploads tarball —
# goes through the identical four steps, and each step exists because of a
# specific way backups fail:
#
#   1. STREAM to `<artifact>.partial`, never to `<artifact>`. A backup directory
#      must never contain a truncated file that looks complete; the day you are
#      choosing which backup to restore is the worst possible day to have to
#      guess which files are whole.
#   2. REFUSE an empty result. A zero-byte artifact is a plausible-looking file
#      that restores to nothing, and it is exactly what an authentication
#      failure or a wrong volume name produces.
#   3. CHECKSUM, rename, then CHECKSUM AGAIN. `mv` across a filesystem boundary
#      is a copy, and a copy is where a full disk truncates silently. Hashing
#      the file that will actually be restored — rather than the bytes that were
#      written — is the difference between a checksum and a comment.
#   4. `umask 077` around the whole thing, so the artifact is never even briefly
#      world-readable.
#
# ⚠ MUST be called from the MAIN SHELL, not inside `$( … )`: it reports its
#   results through globals precisely because a `die` inside a command
#   substitution would only kill the subshell and let the caller carry on with
#   an empty string.
#
#   bk_publish_artifact <artifact-path> <encrypt 0|1> <producer-command...>
#
# The producer writes the payload to stdout and may be a function (bk_mongo_tool
# is one). On success sets _BK_PUBLISH_SHA256 and _BK_PUBLISH_SIZE and returns 0;
# on failure removes the partial file, leaves every existing backup untouched,
# and returns non-zero.
# ------------------------------------------------------------------------------
bk_publish_artifact() {
    local artifact="$1" encrypt="$2"; shift 2
    local partial="${artifact}.partial"
    local pf sha sha2 size

    umask 077
    rm -f "${partial}"

    if [[ "${encrypt}" == "1" ]]; then
        pf="$(bk_passphrase_file)"
        if ! "$@" | bk_encrypt_filter "${pf}" > "${partial}"; then
            rm -f "${partial}"; err "producer failed: $1"; return 1
        fi
    else
        if ! "$@" > "${partial}"; then
            rm -f "${partial}"; err "producer failed: $1"; return 1
        fi
    fi

    # Catches an empty PLAINTEXT artifact. It cannot catch an empty ENCRYPTED
    # one, and that is worth stating because it is counter-intuitive: openssl
    # emits a 16-byte header plus one padding block even for zero bytes of
    # input, so a dump that produced nothing at all yields a ~32-byte file that
    # is, by this test, "not empty". That is precisely why the structural
    # verification after publication is mandatory rather than advisory — it
    # decrypts and checks the payload, where the emptiness is still visible.
    if [[ ! -s "${partial}" ]]; then
        rm -f "${partial}"; err "producer wrote an empty artifact — refusing to publish it"; return 1
    fi

    size="$(bk_file_size "${partial}")"
    sha="$(bk_sha256 "${partial}")"
    mv -f "${partial}" "${artifact}"
    sha2="$(bk_sha256 "${artifact}")"

    if [[ "${sha}" != "${sha2}" ]]; then
        rm -f "${artifact}"
        err "checksum changed during publication (${sha} -> ${sha2}) — artifact removed; check free space on ${BACKUP_ROOT}"
        return 1
    fi

    _BK_PUBLISH_SHA256="${sha}"
    _BK_PUBLISH_SIZE="${size}"
    return 0
}

# ------------------------------------------------------------------------------
# Retention tiers
#
# The tier is decided AT BACKUP TIME from the calendar and baked into the
# filename and the directory. The alternative — deciding at prune time by
# looking at dates — sounds equivalent and is not: it means the pruner has to
# re-derive intent from timestamps, and a backup taken at 00:59 on the 1st
# versus 23:59 on the 31st lands in a different tier depending on the pruner's
# timezone. Decide once, record it, never re-derive.
#
# All timestamps are UTC. A backup set that changes tier because the host moved
# through a DST boundary is a genuine, and genuinely baffling, bug.
# ------------------------------------------------------------------------------
bk_tier_for_now() {
    local dom dow
    dom="$(date -u '+%d')"
    dow="$(date -u '+%u')"   # 1=Monday .. 7=Sunday
    if [[ "$dom" == "01" ]]; then
        printf 'monthly'
    elif [[ "$dow" == "7" ]]; then
        printf 'weekly'
    else
        printf 'daily'
    fi
}

# THE NAMING CONVENTION, in one place.
#
#   <base>.archive.gz[.enc]      the artifact   (mongo)
#   <base>.tar.gz[.enc]          the artifact   (config, uploads)
#   <base>.manifest.json         its manifest
#
# where <base> is `<kind>-<subject>-<UTC timestamp>-<tier>`. The timestamp is
# `YYYYmmddTHHMMSSZ`, which is both human-readable and lexicographically
# ordered, so `ls | sort` is a chronological sort with no dependence on mtime
# (which rsync, `cp -r` and a restore-from-object-storage all rewrite).
bk_artifact_base() {
    local n="$1"
    n="${n%.enc}"; n="${n%.gz}"; n="${n%.archive}"; n="${n%.tar}"
    printf '%s' "$n"
}

bk_timestamp() { date -u '+%Y%m%dT%H%M%SZ'; }

bk_retention_for_tier() {
    case "$1" in
        daily)   printf '%s' "${BACKUP_RETAIN_DAILY}" ;;
        weekly)  printf '%s' "${BACKUP_RETAIN_WEEKLY}" ;;
        monthly) printf '%s' "${BACKUP_RETAIN_MONTHLY}" ;;
        *) die "unknown retention tier: $1" ;;
    esac
}

# bk_prune_tier <tier-directory> <keep-count> <filename-glob>
#
# FOUR SAFETY RULES, each of which exists because its absence has destroyed
# someone's backups:
#
#   1. Only files matching the glob this script itself writes are considered.
#      An operator's `mongo-preupgrade-KEEP.archive.gz.enc` is invisible to the
#      pruner. (Same rule as PH2.6's log pruner: never delete a file you cannot
#      prove you created.)
#   2. A manifest is deleted only together with its artifact, never on its own,
#      and never before it — so an interrupted prune leaves an artifact with a
#      manifest, not an unidentifiable blob.
#   3. keep-count < 1 is rejected. A typo'd BACKUP_RETAIN_DAILY= (empty) must
#      not evaluate to "keep zero backups".
#   4. The newest artifact in a tier is never deleted, whatever the arithmetic
#      says. This is the last line of defence against a bug in rules 1-3.
#
# And the rule that lives in the CALLER, not here: prune runs AFTER a new backup
# has been written and checksummed, never before. Pruning first and then failing
# to produce the replacement is how a retention policy becomes a data-loss
# policy.
bk_prune_tier() {
    local dir="$1" keep="$2" pattern="$3"
    local files count victim path base n=0
    local candidates=()

    [[ -d "$dir" ]] || return 0

    case "$keep" in
        ''|*[!0-9]*) die "invalid retention count '${keep}' for ${dir} — refusing to prune" ;;
    esac
    [[ "$keep" -ge 1 ]] || die "retention count must be >= 1 (got ${keep}) — refusing to prune ${dir}"

    # Rule 1 in action: the candidate set is built from a shell glob filtered by
    # the caller's regex, so only files this system writes are ever considered.
    # (A `ls | grep` here would be shorter and would also hand the pruner every
    # oddly-named file in the directory to reason about; a delete loop is the
    # wrong place to be clever about parsing.)
    for path in "$dir"/*; do
        [[ -f "$path" ]] || continue
        base="${path##*/}"
        [[ "$base" =~ $pattern ]] || continue
        candidates[${#candidates[@]}]="$base"
    done
    [[ ${#candidates[@]} -gt 0 ]] || return 0

    # Newest first. The filename embeds a UTC timestamp in a lexicographically
    # sortable format, so a reverse name sort IS a reverse time sort — no
    # dependence on mtime, which `cp -r`, rsync and a restore from object
    # storage all rewrite.
    files="$(printf '%s\n' "${candidates[@]}" | sort -r)"
    count=${#candidates[@]}
    [[ "$count" -gt "$keep" ]] || return 0

    while IFS= read -r victim; do
        n=$((n + 1))
        [[ $n -gt $keep ]] || continue
        # Rule 4: belt and braces. n > keep >= 1 already excludes the newest.
        [[ $n -gt 1 ]] || continue
        log "retention: removing ${dir}/${victim}"
        # Rule 2: artifact first, manifest second. An interrupt between the two
        # leaves an orphaned manifest — noise — rather than an unidentifiable
        # artifact with no checksum to verify it against.
        rm -f "${dir}/${victim}"
        rm -f "${dir}/$(bk_artifact_base "$victim").manifest.json"
    done <<< "$files"
}

# ------------------------------------------------------------------------------
# MongoDB access
#
# Two transports, one interface. Every caller says "run mongodump with these
# arguments" and this decides whether that means a local process or a
# `compose exec`. Nothing above this line knows which.
# ------------------------------------------------------------------------------

# Builds the connection URI. Assembled here, written to a mode-600 YAML config
# file, and passed to the mongo tools as `--config` — never as `--uri` on the
# command line, because argv is world-readable through `ps` on a shared host.
#
# In docker mode the tools run inside the mongo container and connect over its
# own loopback as the ROOT user: mongodump against a single database needs no
# privilege beyond `read`, but a full-fidelity dump also reads
# `system.users`/`system.version` when `--oplog` or a whole-cluster dump is
# requested, and the least-privilege application user (readWrite on one
# database, see docker/mongodb/init-app-user.js) deliberately cannot do that.
# Backups are an operator task; they use the operator credential. That is
# exactly why PH2.2 kept the root password out of the backend container.
bk_mongo_uri() {
    if [[ "${BACKUP_MODE}" == "direct" ]]; then
        [[ -n "${MONGO_URL:-}" ]] || die "BACKUP_MODE=direct requires MONGO_URL"
        printf '%s' "${MONGO_URL}"
        return 0
    fi

    local user pass
    user="${MONGO_ROOT_USERNAME:-}"
    pass="${MONGO_ROOT_PASSWORD:-}"
    if [[ -z "$user" || -z "$pass" ]]; then
        die "docker mode requires MONGO_ROOT_USERNAME and MONGO_ROOT_PASSWORD (operator credentials — see docs/operations/BACKUP_AND_RESTORE.md §Credentials)"
    fi
    # authSource=admin: the root user lives in the admin database, unlike the
    # application user which authenticates against the application database.
    printf 'mongodb://%s:%s@127.0.0.1:27017/%s?authSource=admin' \
        "$(bk_urlencode "$user")" "$(bk_urlencode "$pass")" "${MONGO_DB_NAME}"
}

# Percent-encodes the characters that are structurally significant in a MongoDB
# connection URI. A generated password containing `@` or `/` silently truncates
# the URI otherwise, and the resulting error ("no reachable servers") points
# nowhere near the cause.
bk_urlencode() {
    printf '%s' "$1" | awk '
        BEGIN { for (i = 0; i < 256; i++) ord[sprintf("%c", i)] = i }
        {
            for (i = 1; i <= length($0); i++) {
                c = substr($0, i, 1)
                if (c ~ /[A-Za-z0-9._~-]/) printf "%s", c
                else printf "%%%02X", ord[c]
            }
        }'
}

# Writes the tools config file and echoes its path.
bk_mongo_config_file() {
    local wd cf
    wd="$(bk_workdir)"
    cf="${wd}/mongo-tools.yaml"
    if [[ ! -f "$cf" ]]; then
        printf 'uri: "%s"\n' "$(bk_mongo_uri)" > "$cf"
        chmod 600 "$cf"
    fi
    printf '%s' "$cf"
}

# Copies the tools config into the mongo container, once per process.
#
# It has to go INSIDE the container because that is where the tool runs, and it
# cannot ride on the tool's stdin because for `mongorestore` stdin is already
# carrying the archive. So it is staged by its own exec, in a file the container
# owns at mode 600, and removed by _bk_cleanup on every exit path including
# Ctrl-C. `docker compose cp` is not used: it would leave the file with the
# host's ownership semantics and cannot chmod it in the same operation.
bk_stage_container_config() {
    [[ -n "${_BK_CONTAINER_CONFIG:-}" ]] && return 0
    local target="/tmp/.stockassist-mt-$$.yaml"
    docker compose -f "${BACKUP_COMPOSE_FILE}" exec -T "${BACKUP_MONGO_SERVICE}" \
        sh -c "umask 077 && cat > '${target}'" < "$(bk_mongo_config_file)" \
        || die "could not stage the mongo tools config inside the '${BACKUP_MONGO_SERVICE}' container — is the stack running?"
    _BK_CONTAINER_CONFIG="${target}"
}

# Runs a mongo tool (`mongodump`, `mongorestore`) with the config file applied,
# in whichever transport BACKUP_MODE selects. stdin and stdout are the tool's
# own — binary-clean in both directions, which is the whole point.
bk_mongo_tool() {
    local tool="$1"; shift

    if [[ "${BACKUP_MODE}" == "direct" ]]; then
        require_cmd "$tool"
        "$tool" --config="$(bk_mongo_config_file)" "$@"
        return $?
    fi

    require_cmd docker
    bk_stage_container_config

    # `-T` IS LOAD-BEARING, not a style preference.
    #
    # Without it, Docker allocates a pseudo-TTY, and a TTY performs newline
    # translation on the stream. The archive is binary; a single 0x0A rewritten
    # to 0x0D 0x0A corrupts it. The corruption is silent — mongodump exits 0,
    # the file has a plausible size, and it fails to restore months later. Every
    # "all of our backups turned out to be corrupt" story of this shape is a
    # missing -T.
    docker compose -f "${BACKUP_COMPOSE_FILE}" exec -T \
        "${BACKUP_MONGO_SERVICE}" \
        "$tool" --config="${_BK_CONTAINER_CONFIG}" "$@"
}

# MUST be called once, in the main shell, before any pipeline that uses
# bk_mongo_tool.
#
# WHY: bash runs every element of a pipeline in a subshell. `bk_mongo_tool
# mongodump | openssl > file` therefore stages the container-side config inside
# a subshell, where the assignment to _BK_CONTAINER_CONFIG is invisible to the
# parent — so the parent's _bk_cleanup never removes it, and a file containing a
# database URI with the root password is left inside the mongo container. Doing
# the staging eagerly, in the main shell, is what keeps the cleanup honest.
bk_prepare_mongo() {
    bk_init_workdir
    if [[ "${BACKUP_MODE}" == "docker" ]]; then
        bk_stage_container_config
    fi
    return 0
}

# Runs a JavaScript expression via mongosh and returns its stdout.
# Used for collection counts (the manifest's verification baseline) and for the
# restore guard's "is the target database empty?" check.
bk_mongo_eval() {
    local js="$1"
    if [[ "${BACKUP_MODE}" == "direct" ]]; then
        require_cmd mongosh
        mongosh "$(bk_mongo_uri)" --quiet --eval "$js"
    else
        require_cmd docker
        docker compose -f "${BACKUP_COMPOSE_FILE}" exec -T \
            "${BACKUP_MONGO_SERVICE}" \
            mongosh "$(bk_mongo_uri)" --quiet --eval "$js"
    fi
}

# Emits a compact JSON object of collection -> document count for a database.
# This is the manifest's `collections` field and the thing a restore is checked
# against.
#
# ⚠ `countDocuments` (an aggregation, exact) rather than `count`/`estimatedDocumentCount`
#   (metadata, fast, and WRONG after an unclean shutdown). A verification
#   baseline that can be off by a few thousand documents is not a baseline.
bk_collection_counts() {
    local db="$1"
    bk_mongo_eval "
        const d = db.getSiblingDB('$(bk_json_escape "$db")');
        const out = {};
        d.getCollectionNames().sort().forEach(function (c) {
            if (c.indexOf('system.') === 0) return;
            out[c] = d.getCollection(c).countDocuments({});
        });
        print(JSON.stringify(out));
    " | tr -d '\r' | grep -E '^\{' | head -n 1
}

# ------------------------------------------------------------------------------
# Confirmation for destructive operations
#
# A restore overwrites production data. It must be possible to automate (drills
# run unattended) and it must be hard to do by accident (a mistyped database
# name at 03:00 during an incident). The resolution used here is the one `terraform
# destroy` and `gh repo delete` settled on: retype the name to proceed, with an
# explicit --yes for automation. Never a bare y/N — during an incident, "y" is
# muscle memory.
# ------------------------------------------------------------------------------
bk_confirm_destructive() {
    local subject="$1" assume_yes="$2" typed=""

    if [[ "$assume_yes" == "1" ]]; then
        warn "--yes given: proceeding with destructive operation on '${subject}' without confirmation"
        return 0
    fi

    if [[ ! -t 0 ]]; then
        die "destructive operation on '${subject}' requires an interactive terminal, or --yes for automation"
    fi

    printf '\n  This will OVERWRITE data in: %s\n  Type the name to confirm: ' "${subject}" >&2
    IFS= read -r typed
    [[ "$typed" == "$subject" ]] || die "confirmation did not match — aborted, nothing was changed"
}

#!/usr/bin/env bash
# ==============================================================================
# StockAssist AI — secret file generator (PH2.3)
#
# Materializes the host-side secret files consumed by docker-compose.secrets.yml.
#
#   ./secrets/generate.sh              # create anything missing, never overwrite
#   ./secrets/generate.sh --rotate     # regenerate the ones this script owns
#   ./secrets/generate.sh --check      # report status, write nothing
#
# WHY A GENERATOR AND NOT A TEMPLATE FILE
# ---------------------------------------
# The single most common secret-management failure is not a missing secret — it
# is a WEAK one. Given a template with `JWT_SECRET=REPLACE_ME`, a meaningful
# fraction of operators type something memorable, and `security/secrets.py` will
# reject it (which is the system working, but only after a failed deploy). A
# generator removes the opportunity: every value it writes is 48 bytes from the
# OS CSPRNG, url-safe so it survives being embedded in a connection URI.
#
# WHAT IT DELIBERATELY DOES NOT DO
# --------------------------------
# It never generates a THIRD-PARTY credential. An Anthropic key or a Zerodha API
# secret is issued by that provider; a placeholder here would be indistinguishable
# from a real value to everything downstream. Those files are yours to create —
# the script tells you which ones and where.
#
# ⚠ The files this writes are PLAINTEXT ON DISK. That is the accepted trade for a
#   single-host Compose deployment (see docs/deployment/SECRETS.md §7 for the
#   Swarm / Kubernetes / cloud-manager paths that remove it). They are chmod 600
#   and the directory is git-ignored.
# ==============================================================================
set -euo pipefail

SECRETS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="create"

case "${1:-}" in
    "")         MODE="create" ;;
    --rotate)   MODE="rotate" ;;
    --check)    MODE="check" ;;
    -h|--help)  sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)          echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
esac

# Prefer python3's `secrets` module — the same CSPRNG the application itself is
# told to use, so a hand-generated and a script-generated value are equivalent.
# `openssl rand` is the fallback for a host without python3.
generate() {
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
    elif command -v openssl >/dev/null 2>&1; then
        # `tr -d` strips the base64 characters that are unsafe inside a URI.
        openssl rand -base64 48 | tr -d '=+/\n'
    else
        echo "FATAL: need python3 or openssl to generate secrets" >&2
        exit 1
    fi
}

# 0600 BEFORE the value is written, never after: a file created 0644 and
# tightened a moment later is world-readable for that moment, and on a shared
# host that is all an attacker needs.
write_secret() {
    local name="$1" value="$2" path="${SECRETS_DIR}/$1"
    ( umask 077; printf '%s' "${value}" > "${path}" )
    chmod 600 "${path}"
}

# Written WITHOUT a trailing newline. The loader strips whitespace anyway, but a
# file whose bytes are exactly the secret is what every other consumer expects —
# and `docker secret create` does not strip anything.
ensure() {
    local name="$1" value="$2" label="$3"
    local path="${SECRETS_DIR}/${name}"

    if [ "${MODE}" = "check" ]; then
        if [ -s "${path}" ]; then
            printf '  ✓ %-22s %s\n' "${name}" "${label}"
        else
            printf '  ✗ %-22s MISSING — %s\n' "${name}" "${label}"
        fi
        return 0
    fi

    if [ -s "${path}" ] && [ "${MODE}" != "rotate" ]; then
        printf '  · %-22s exists, leaving alone\n' "${name}"
        return 0
    fi

    write_secret "${name}" "${value}"
    printf '  ✓ %-22s %s\n' "${name}" \
        "$([ "${MODE}" = "rotate" ] && echo "ROTATED" || echo "generated")"
}

# A file the operator must supply themselves — reported, never invented.
require_manual() {
    local name="$1" label="$2"
    local path="${SECRETS_DIR}/${name}"
    if [ -s "${path}" ]; then
        printf '  ✓ %-22s %s\n' "${name}" "${label}"
    else
        printf '  ○ %-22s not set — %s\n' "${name}" "${label}"
    fi
}

echo "StockAssist AI — secrets in ${SECRETS_DIR} (mode: ${MODE})"
echo

# ── Generated credentials ─────────────────────────────────────────────────────
echo "Infrastructure credentials:"
MONGO_ROOT_USERNAME="stockassist_root"
MONGO_APP_USERNAME="stockassist_app"
MONGO_DB_NAME="${MONGO_DB_NAME:-alpha_stock}"

ensure mongo_root_username "${MONGO_ROOT_USERNAME}" "mongo root user"
ensure mongo_root_password "$(generate)"           "mongo root password"
ensure mongo_app_password  "$(generate)"           "mongo application-user password"
ensure redis_password      "$(generate)"           "redis requirepass"

# ── Composed URIs ─────────────────────────────────────────────────────────────
# Built FROM the passwords above rather than generated independently, so the URI
# and the credential it embeds can never drift apart — the failure that produces
# an authentication error looking exactly like a network error.
#
# Always rebuilt (not `ensure`d) when the underlying password was just written,
# because a stale URI beside a rotated password is worse than no URI at all.
if [ "${MODE}" != "check" ]; then
    APP_PASSWORD="$(cat "${SECRETS_DIR}/mongo_app_password")"
    REDIS_PASSWORD="$(cat "${SECRETS_DIR}/redis_password")"
    write_secret mongo_url \
        "mongodb://${MONGO_APP_USERNAME}:${APP_PASSWORD}@mongo:27017/${MONGO_DB_NAME}?authSource=${MONGO_DB_NAME}"
    write_secret redis_url "redis://:${REDIS_PASSWORD}@redis:6379/0"
    printf '  ✓ %-22s composed from the above\n' "mongo_url"
    printf '  ✓ %-22s composed from the above\n' "redis_url"
else
    require_manual mongo_url "backend connection URI"
    require_manual redis_url "backend cache/pubsub URI"
fi

echo
echo "Application signing keys:"
ensure jwt_secret      "$(generate)" "JWT signing key"
ensure csrf_secret     "$(generate)" "CSRF token HMAC key"
ensure recovery_secret "$(generate)" "recovery token HMAC key"
ensure webhook_api_key "$(generate)" "inbound automation webhook key"

# BROKER_TOKEN_KEY must be a valid Fernet key (32 bytes, urlsafe-base64), not a
# token_urlsafe string — security/secrets.py validates the shape at boot, so a
# wrong-format value here fails the deploy rather than the first broker connect.
if [ "${MODE}" = "check" ]; then
    require_manual broker_token_key "Fernet key for broker tokens at rest"
elif [ ! -s "${SECRETS_DIR}/broker_token_key" ] || [ "${MODE}" = "rotate" ]; then
    if command -v python3 >/dev/null 2>&1; then
        FERNET="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
        # ⚠ ROTATING THIS ONE IS NOT SAFE IN ISOLATION: broker tokens already
        # encrypted with the old key become undecryptable. See SECRETS.md §6.
        write_secret broker_token_key "${FERNET}"
        printf '  ✓ %-22s %s\n' "broker_token_key" \
            "$([ "${MODE}" = "rotate" ] && echo "ROTATED ⚠ re-encrypt stored broker tokens" || echo "generated")"
    else
        printf '  ○ %-22s needs python3 to generate a Fernet key\n' "broker_token_key"
    fi
else
    printf '  · %-22s exists, leaving alone\n' "broker_token_key"
fi

# ── Third-party credentials — never invented ──────────────────────────────────
echo
echo "Third-party credentials (create these yourself — a placeholder would be"
echo "indistinguishable from a real key to everything downstream):"
require_manual anthropic_api_key "console.anthropic.com → API keys"
require_manual google_gemini_key "aistudio.google.com → API keys"

echo
if [ "${MODE}" = "check" ]; then
    echo "Nothing was written."
else
    echo "Done. These files are plaintext on disk, chmod 600, and git-ignored."
    echo "Bring the stack up with:"
    echo
    echo "  docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d"
    echo
    echo "MONGO_APP_USERNAME / MONGO_APP_PASSWORD must ALSO be in .env — the"
    echo "MongoDB init script runs under mongosh and cannot read a secret file."
    echo "See docs/deployment/SECRETS.md §8 (L2)."
fi

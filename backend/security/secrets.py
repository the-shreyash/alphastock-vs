"""Centralized secret & configuration management (PH1.9, extended in PH2.3).

Single source of truth for **which** environment variables StockAssist AI
depends on, **where each one's value comes from**, **which are required in which
environment**, and **whether the process is allowed to start** given what is (and
isn't) configured. This is the secrets/supply-chain counterpart to the other
``security.*`` policy modules: the one place that knows the shape of the app's
configuration surface.

PH2.3 added the *source* half of that sentence. Before it, a secret could only
arrive as a plaintext environment variable — which means it is visible in
``docker inspect``, in ``/proc/<pid>/environ``, in a crash dump, and in the
environment of every child process the app ever spawns. The loader below adds
file-backed sources (Docker/Swarm secrets, Kubernetes projected volumes, and the
``*_FILE`` convention the official postgres/mysql/mongo images established), with
one resolution order applied uniformly to every registered variable.

Design decisions (see docs/deployment/SECRETS.md, .claude/SECRETS.md /
SECURITY_ARCHITECTURE.md §23–§24, PH1.9 + PH2.3):

* **One loader, then one environment.** :func:`load_secrets` resolves every
  configuration input from its highest-precedence source and *materializes* the
  result into ``os.environ`` exactly once, at process start, before anything
  reads it. Roughly thirty modules already read configuration through small
  call-time ``os.environ.get`` resolvers; hydrating the environment means every
  one of them transparently gains file-backed secret support without a single
  call-site change — and, critically, without a second place that knows how to
  find a secret. The alternative (rewriting thirty call sites to route through an
  accessor) buys nothing here and risks regressions in security-critical paths.

* **Fail fast, fail closed.** ``validate_config()`` runs once at process
  startup (called from ``server.py`` immediately after ``load_dotenv`` and
  *before* the Mongo client or any router is constructed). A missing critical
  secret aborts the boot with a single, aggregated, human-readable error instead
  of a raw ``KeyError`` surfacing deep in a request handler. Every problem is
  collected and reported together so an operator fixes the whole environment in
  one pass, not one variable per crash-loop.

* **Environment-aware severity.** The same registry drives all four
  environments. In ``production`` the rules are strict: required secrets must be
  present, signing secrets must meet a minimum length, and known placeholder /
  weak-default values are rejected outright. In ``development`` and ``testing``
  (and, to a lesser extent, ``staging``) the *same* findings degrade to warnings
  so a laptop or a CI runner with a half-filled environment still boots — except
  for the small core trio the server genuinely cannot run without (``MONGO_URL``,
  ``DB_NAME``, ``JWT_SECRET``), which are hard requirements everywhere.
  ``testing`` mirrors ``development``'s leniency but is a distinct, honestly-
  labelled environment for automated suites and CI (see
  :data:`LENIENT_ENVIRONMENTS`).

* **No secret ever touches a log.** The validator reports variable *names* and
  *presence*, never values. :func:`redact` exists for the rare case a value must
  appear in diagnostics; it collapses to a fixed mask. The startup summary is
  presence-only by construction.

* **The registry is the documentation.** :data:`SECRET_REGISTRY` is the
  authoritative inventory of every configuration input — its category,
  sensitivity, which environments require it, a one-line purpose, and a
  placeholder for the example file. ``SECRETS.md`` and ``backend/.env.example``
  are generated to match it; ``scripts/generate_env_example.py`` keeps the
  template in sync so the two never drift.

* **Framework-agnostic.** Pure ``os.environ`` + stdlib, no FastAPI import, so it
  is unit-testable in isolation and reusable by scripts (seeders, the example
  generator, CI checks). Environment detection reuses
  :func:`security.cookies.is_production` so "are we in production?" has exactly
  one definition across the whole security package.

* **Injectable I/O.** File reads go through a ``reader`` callable that defaults
  to the real filesystem. Tests substitute a dict-backed reader, so the whole
  resolution matrix — precedence, conflicts, empty files, unreadable paths — is
  covered hermetically, with no temp directories and no root privileges.
"""
from __future__ import annotations

import base64
import hmac
import hashlib
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple
from urllib.parse import urlsplit

# Reuse the single production-detection primitive so environment semantics never
# drift between the cookie policy, CORS, and secret validation.
from security.cookies import is_production  # noqa: F401  (re-exported)

# --------------------------------------------------------------------------- #
# Environment model                                                             #
# --------------------------------------------------------------------------- #
DEVELOPMENT = "development"
TESTING = "testing"
STAGING = "staging"
PRODUCTION = "production"
KNOWN_ENVIRONMENTS: Set[str] = {DEVELOPMENT, TESTING, STAGING, PRODUCTION}

# Environments that share development's *lenient* validation posture: a missing
# optional secret is a warning, not a boot failure, so a laptop or a CI runner
# with a half-filled environment still boots. ``testing`` exists as a distinct,
# first-class value (PH2.8) rather than an alias for ``development`` so that an
# automated suite or CI job can label its environment honestly — the logs, the
# /api/diagnostics `environment` field, and any future env-scoped behaviour then
# say "testing", instead of masquerading as a developer's laptop or, worse,
# tripping the "unknown APP_ENV" error. It is non-production everywhere by
# construction: every production gate keys on ``env == PRODUCTION`` (see
# security.cookies.is_production), which ``testing`` can never satisfy.
LENIENT_ENVIRONMENTS: Set[str] = {DEVELOPMENT, TESTING}


def app_env(environ: Optional[Mapping[str, str]] = None) -> str:
    """Normalized deployment environment: one of ``development`` / ``testing`` /
    ``staging`` / ``production``.

    Reads ``APP_ENV`` (default ``development``). An unrecognized value is
    *reported* by the validator but resolves here to ``development`` so callers
    always receive a valid member of :data:`KNOWN_ENVIRONMENTS`.
    """
    # `environ is None`, not `environ or os.environ`: an EMPTY mapping is a
    # legitimate argument meaning "an environment in which nothing is set", and
    # the truthiness form silently substituted the host process environment for
    # it. For a security-configuration reader that is the wrong answer in the
    # most dangerous direction — a caller asking "what would this resolve to
    # with no configuration?" was answered with the host's real configuration.
    # Found by PH3.1 when the test process stopped inheriting a bare `.env`.
    env = (os.environ if environ is None else environ).get("APP_ENV", DEVELOPMENT).strip().lower()
    return env if env in KNOWN_ENVIRONMENTS else DEVELOPMENT


# --------------------------------------------------------------------------- #
# Weak / placeholder value detection                                            #
# --------------------------------------------------------------------------- #
# Case-insensitive substrings that mark a value as a template placeholder or a
# well-known weak default that must never reach production. Kept deliberately
# conservative so a legitimate secret is not misflagged.
PLACEHOLDER_MARKERS: Set[str] = {
    "your_",
    "_here",
    "changeme",
    "change_me",
    "change_this",
    "change-this",
    "replace_me",
    "replaceme",
    "placeholder",
    "supersecret",
    "admin123",
    "alphapartner123",
    "testkey",
    "testsecret",
    "testtoken",
    "example_key",
    "dummy",
    "xxxx",
}

# Minimum length for a symmetric signing / HMAC secret to be considered strong
# enough for production (128 bits of entropy ≈ 32 hex/base-ish chars).
MIN_SIGNING_SECRET_LENGTH = 32

# --- Secret-source constants (used by the registry below and by the resolution
#     layer further down; defined here because SECRET_REGISTRY references them
#     at import time). Full rationale accompanies the resolution section. ------
#
# The mount point Docker (and Swarm) uses for `secrets:`; also the conventional
# location for a Kubernetes secret volume. Overridable via SECRETS_DIR.
DEFAULT_SECRETS_DIR = "/run/secrets"

# The suffix that turns a variable into a *pointer* to a value rather than the
# value itself — the convention established by the official postgres, mysql,
# mongo and elasticsearch images.
FILE_SUFFIX = "_FILE"


def looks_like_placeholder(value: str) -> bool:
    """True when ``value`` contains a known placeholder / weak-default marker."""
    low = value.strip().lower()
    if not low:
        return False
    return any(marker in low for marker in PLACEHOLDER_MARKERS)


# Values that are *syntactically* fine but carry almost no entropy. Placeholder
# detection catches "changeme"; this catches the other classic — an operator who
# needed to get past a length check and typed `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`
# or `12345678901234567890123456789012`. Both pass `min_length`, and neither
# survives ten seconds of offline cracking against a leaked JWT.
_SEQUENTIAL_RUNS = ("0123456789", "abcdefghijklmnopqrstuvwxyz", "qwertyuiop")
MIN_UNIQUE_CHARS = 8


def looks_weak(value: str) -> bool:
    """True when ``value`` is structurally low-entropy.

    Deliberately conservative — a false positive here blocks a production boot,
    so every rule below describes a value no generator would ever emit:

    * shorter than 8 characters (nothing sensitive is legitimately that short),
    * four or fewer distinct characters at any length (``aaaa…``, ``abab…``),
    * fewer than :data:`MIN_UNIQUE_CHARS` distinct characters once the value is
      long enough for that to be meaningful (≥ 16 chars),
    * a substring of a keyboard/alphabet/digit run (``1234567890abcdef`` style).

    A real ``secrets.token_urlsafe(48)`` has ~50 distinct characters and clears
    all four by a wide margin.
    """
    v = value.strip()
    if not v:
        return False  # absence is the validator's job, not weakness
    if len(v) < 8:
        return True
    unique = len(set(v))
    if unique <= 4:
        return True
    if len(v) >= 16 and unique < MIN_UNIQUE_CHARS:
        return True
    low = v.lower()
    if any(low in run * 4 for run in _SEQUENTIAL_RUNS):
        return True
    return False


def redact(value: Optional[str]) -> str:
    """Collapse a secret to a fixed mask for the rare diagnostic that must
    reference it. Never reveals length or content."""
    return "********" if value else "<unset>"


def fingerprint(value: Optional[str]) -> str:
    """A short, stable, value-free identifier for a secret — for answering "did
    this secret actually change after I rotated it?" without printing it.

    Keyed HMAC-SHA256 truncated to 12 hex characters. It is a **change
    detector**, not an anonymizer: a 48-bit digest of a low-entropy value can be
    confirmed by anyone who can guess the value. Never treat a fingerprint as
    safe to publish alongside a hint about what the secret is; it belongs in
    operator-facing rotation output, not in a customer-visible log.
    """
    if not value:
        return "<unset>"
    digest = hmac.new(b"stockassist-secret-fingerprint",
                      value.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:12]


# --------------------------------------------------------------------------- #
# Registry                                                                      #
# --------------------------------------------------------------------------- #
# Categories are used purely for grouping in the inventory / example file.
CAT_CORE = "core"
CAT_AUTH = "auth-signing"
CAT_OAUTH = "oauth"
CAT_AI = "ai-provider"
CAT_MARKET = "market-data"
CAT_BROKER = "broker"
CAT_NOTIFY = "notifications"
CAT_ADMIN = "admin-bootstrap"
CAT_INFRA = "infrastructure"
CAT_APPCFG = "app-config"


@dataclass(frozen=True)
class SecretSpec:
    """Declarative description of one configuration input.

    ``required_in`` lists the environments in which the variable MUST be present
    and non-placeholder; in other environments its absence is at most a warning.
    ``sensitive`` marks true secrets (keys, passwords, tokens) — these are the
    values that must never be logged and that placeholder-detection guards most
    strictly. ``min_length`` (when set) is enforced as a hard error in
    production. ``example`` is the safe, non-secret placeholder written to
    ``backend/.env.example``.
    """

    name: str
    category: str
    description: str
    sensitive: bool = False
    required_in: Set[str] = field(default_factory=set)
    min_length: Optional[int] = None
    example: str = ""
    rotation: str = "On suspected exposure"
    # PH2.3 — whether the low-entropy check (:func:`looks_weak`) applies. Default
    # on for every sensitive value, because a key with no entropy is a key with
    # no protection. Turned OFF for the handful of sensitive-but-not-random
    # values: a *username* is sensitive enough not to log, but it is chosen by a
    # provider, not generated — SendGrid's SMTP username is literally `apikey`,
    # six characters, and rejecting it would block a legitimate production boot.
    entropy_checked: bool = True


# The authoritative configuration surface. Order here is the order used in the
# generated example file. `required_in={PRODUCTION,...}` means "must be set in
# those environments"; an empty set means "feature-gated / optional everywhere".
SECRET_REGISTRY: List[SecretSpec] = [
    # ── Core (server cannot run without these anywhere) ──────────────────────
    SecretSpec(
        "MONGO_URL", CAT_CORE, "MongoDB connection string (may embed credentials).",
        sensitive=True, required_in=set(KNOWN_ENVIRONMENTS),
        example="mongodb://localhost:27017", rotation="Rotate DB credentials quarterly / on exposure",
    ),
    SecretSpec(
        "DB_NAME", CAT_CORE, "MongoDB database name.",
        required_in=set(KNOWN_ENVIRONMENTS), example="alpha_stock_db", rotation="N/A (not a secret)",
    ),
    SecretSpec(
        "JWT_SECRET", CAT_AUTH,
        "HS256 signing key for access/refresh JWTs; also the fallback HMAC key for CSRF & recovery tokens.",
        sensitive=True, required_in=set(KNOWN_ENVIRONMENTS),
        min_length=MIN_SIGNING_SECRET_LENGTH,
        example="generate-with: python -c \"import secrets;print(secrets.token_urlsafe(48))\"",
        rotation="Rotate quarterly; rotation invalidates all live sessions (forces re-login)",
    ),
    # ── App config ───────────────────────────────────────────────────────────
    SecretSpec(
        "APP_ENV", CAT_APPCFG, "Deployment environment: development | testing | staging | production.",
        example=DEVELOPMENT, rotation="N/A",
    ),
    SecretSpec(
        "FRONTEND_URL", CAT_APPCFG, "Public origin of the SPA; used for OAuth redirects and links.",
        required_in={STAGING, PRODUCTION}, example="http://localhost:3000", rotation="N/A",
    ),
    SecretSpec(
        "CORS_ALLOWED_ORIGINS", CAT_APPCFG,
        "Comma-separated exact-match CORS origin allowlist (never '*'). See security/cors.py.",
        required_in={PRODUCTION}, example="https://app.stockassist.ai,https://www.stockassist.ai",
        rotation="N/A",
    ),
    SecretSpec(
        "SECRETS_DIR", CAT_APPCFG,
        "Directory scanned for file-backed secrets (Docker/Swarm/K8s mount point).",
        example=DEFAULT_SECRETS_DIR, rotation="N/A (not a secret)",
    ),
    SecretSpec(
        "REQUIRE_FILE_SECRETS", CAT_APPCFG,
        "When true, every sensitive secret MUST arrive from a file; plaintext env delivery becomes a boot error.",
        example="false", rotation="N/A (not a secret)",
    ),
    SecretSpec(
        "CSRF_SECRET", CAT_AUTH,
        "Optional dedicated HMAC key for CSRF tokens; falls back to JWT_SECRET when unset.",
        sensitive=True, min_length=MIN_SIGNING_SECRET_LENGTH,
        example="", rotation="Rotate quarterly; rotation invalidates outstanding CSRF tokens",
    ),
    SecretSpec(
        "RECOVERY_SECRET", CAT_AUTH,
        "Optional dedicated HMAC key for email-verify / password-reset tokens; falls back to JWT_SECRET.",
        sensitive=True, min_length=MIN_SIGNING_SECRET_LENGTH,
        example="", rotation="Rotate quarterly; rotation invalidates outstanding recovery links",
    ),
    # ── AI providers (product core — at least one required in production) ─────
    SecretSpec(
        "ANTHROPIC_API_KEY", CAT_AI, "Anthropic (Claude) API key for the AI engine.",
        sensitive=True, example="", rotation="Rotate in the Anthropic console; 90 days",
    ),
    SecretSpec(
        "GOOGLE_GEMINI_KEY", CAT_AI, "Google Gemini API key for the dual-model AI debate.",
        sensitive=True, example="", rotation="Rotate in Google AI Studio; 90 days",
    ),
    # ── Google OAuth (feature-gated; both-or-neither) ────────────────────────
    SecretSpec(
        "GOOGLE_CLIENT_ID", CAT_OAUTH, "Google OAuth 2.0 client id ('Continue with Google').",
        example="", rotation="Rotate in Google Cloud console on exposure",
    ),
    SecretSpec(
        "GOOGLE_CLIENT_SECRET", CAT_OAUTH, "Google OAuth 2.0 client secret.",
        sensitive=True, example="", rotation="Rotate in Google Cloud console; on exposure",
    ),
    # ── Market data (optional fallback) ──────────────────────────────────────
    SecretSpec(
        "ALPHA_VANTAGE_KEY", CAT_MARKET, "Alpha Vantage API key (intraday fallback source).",
        sensitive=True, example="", rotation="Rotate in the Alpha Vantage dashboard on exposure",
    ),
    # ── Brokers (feature-gated; each is a both-or-neither pair) ───────────────
    SecretSpec(
        "KITE_API_KEY", CAT_BROKER, "Zerodha Kite Connect API key.",
        sensitive=True, example="", rotation="Rotate in the Kite developer console on exposure",
    ),
    SecretSpec(
        "KITE_API_SECRET", CAT_BROKER, "Zerodha Kite Connect API secret.",
        sensitive=True, example="", rotation="Rotate in the Kite developer console on exposure",
    ),
    SecretSpec(
        "KITE_REDIRECT_URL", CAT_BROKER, "Zerodha OAuth redirect URL.",
        example="http://localhost:8000/api/zerodha/callback", rotation="N/A",
    ),
    SecretSpec(
        "UPSTOX_API_KEY", CAT_BROKER, "Upstox API key.",
        sensitive=True, example="", rotation="Rotate in the Upstox developer console on exposure",
    ),
    SecretSpec(
        "UPSTOX_API_SECRET", CAT_BROKER, "Upstox API secret.",
        sensitive=True, example="", rotation="Rotate in the Upstox developer console on exposure",
    ),
    SecretSpec(
        "UPSTOX_REDIRECT_URL", CAT_BROKER, "Upstox OAuth redirect URL.",
        example="http://localhost:8000/api/brokers/upstox/callback", rotation="N/A",
    ),
    SecretSpec(
        "ANGELONE_API_KEY", CAT_BROKER, "Angel One SmartAPI key.",
        sensitive=True, example="", rotation="Rotate in the SmartAPI developer portal on exposure",
    ),
    SecretSpec(
        "ANGELONE_REDIRECT_URL", CAT_BROKER, "Angel One SmartAPI publisher-login redirect URL.",
        example="http://localhost:8000/api/brokers/angelone/callback", rotation="N/A",
    ),
    SecretSpec(
        "FYERS_APP_ID", CAT_BROKER, "Fyers API v3 app id (client id).",
        sensitive=True, example="", rotation="Rotate in the Fyers API dashboard on exposure",
    ),
    SecretSpec(
        "FYERS_SECRET_ID", CAT_BROKER, "Fyers API v3 secret id.",
        sensitive=True, example="", rotation="Rotate in the Fyers API dashboard on exposure",
    ),
    SecretSpec(
        "FYERS_REDIRECT_URL", CAT_BROKER, "Fyers OAuth redirect URL.",
        example="http://localhost:8000/api/brokers/fyers/callback", rotation="N/A",
    ),
    SecretSpec(
        "DHAN_PARTNER_ID", CAT_BROKER, "Dhan partner id (DhanHQ v2 partner consent flow).",
        sensitive=True, example="", rotation="Rotate in the Dhan partner dashboard on exposure",
    ),
    SecretSpec(
        # Sent as a REQUEST HEADER on generate-consent / consume-consent, never
        # as a query parameter — a consent login URL is shown to the user.
        "DHAN_PARTNER_SECRET", CAT_BROKER, "Dhan partner secret; sent as a request header, never in a URL.",
        sensitive=True, example="", rotation="Rotate in the Dhan partner dashboard on exposure",
    ),
    SecretSpec(
        "DHAN_REDIRECT_URL", CAT_BROKER, "Dhan partner-consent redirect URL; receives ?tokenId= on success.",
        example="http://localhost:8000/api/brokers/dhan/callback", rotation="N/A",
    ),
    SecretSpec(
        "BROKER_TOKEN_KEY", CAT_BROKER,
        "Optional Fernet key encrypting broker tokens at rest; derived from JWT_SECRET when unset.",
        sensitive=True, example="",
        rotation="Rotate carefully: re-encrypts stored broker tokens; on exposure",
    ),
    # ── Notifications (optional) ─────────────────────────────────────────────
    SecretSpec(
        "TWILIO_ACCOUNT_SID", CAT_NOTIFY, "Twilio account SID (WhatsApp alerts).",
        sensitive=True, example="", rotation="Rotate in the Twilio console on exposure",
    ),
    SecretSpec(
        "TWILIO_AUTH_TOKEN", CAT_NOTIFY, "Twilio auth token.",
        sensitive=True, example="", rotation="Rotate in the Twilio console; on exposure",
    ),
    SecretSpec(
        "TWILIO_WHATSAPP_FROM", CAT_NOTIFY, "Twilio WhatsApp sender number.",
        example="+14155238886", rotation="N/A",
    ),
    SecretSpec(
        "USER_WHATSAPP_TO", CAT_NOTIFY, "Default WhatsApp recipient number.",
        example="", rotation="N/A",
    ),
    SecretSpec(
        "SENDGRID_API_KEY", CAT_NOTIFY, "SendGrid API key (transactional email — option A).",
        sensitive=True, example="", rotation="Rotate in the SendGrid console on exposure",
    ),
    SecretSpec(
        "SMTP_HOST", CAT_NOTIFY, "SMTP host (transactional email — option B).",
        example="", rotation="N/A",
    ),
    SecretSpec(
        "SMTP_PORT", CAT_NOTIFY, "SMTP port.", example="587", rotation="N/A",
    ),
    SecretSpec(
        "SMTP_USER", CAT_NOTIFY, "SMTP username.", sensitive=True, example="",
        rotation="Rotate with the mail provider on exposure",
        # Provider-chosen, not generated: SendGrid's is the literal string
        # `apikey`. See SecretSpec.entropy_checked.
        entropy_checked=False,
    ),
    SecretSpec(
        "SMTP_PASSWORD", CAT_NOTIFY, "SMTP password.", sensitive=True, example="",
        rotation="Rotate with the mail provider on exposure",
    ),
    SecretSpec(
        "EMAIL_FROM", CAT_NOTIFY, "From address for outbound email.",
        example="alerts@alphapartner.ai", rotation="N/A",
    ),
    SecretSpec(
        "EMAIL_FROM_NAME", CAT_NOTIFY, "From display name for outbound email.",
        example="AlphaPartner", rotation="N/A",
    ),
    SecretSpec(
        "WEBHOOK_API_KEY", CAT_NOTIFY,
        "Shared secret for inbound n8n automation webhooks (X-Webhook-Key header).",
        sensitive=True, example="", rotation="Rotate whenever an automation integration is rotated",
    ),
    SecretSpec(
        "TELEGRAM_BOT_TOKEN", CAT_NOTIFY, "Telegram bot token (optional alert channel).",
        sensitive=True, example="", rotation="Rotate via BotFather on exposure",
    ),
    SecretSpec(
        "TELEGRAM_CHAT_ID", CAT_NOTIFY, "Telegram chat id for alerts.",
        example="", rotation="N/A",
    ),
    # ── Infrastructure (optional) ────────────────────────────────────────────
    SecretSpec(
        "REDIS_URL", CAT_INFRA,
        "Redis connection URL for cross-process realtime fan-out; single-process/dev works without it.",
        sensitive=True, example="", rotation="Rotate Redis credentials on exposure",
    ),
    # ── Redis connection tuning (PH2.7) ──────────────────────────────────────
    # All optional, all clamped, all safe to leave unset — the defaults are the
    # values documented in docs/infrastructure/REDIS.md. They are registered here
    # rather than left as undocumented os.environ reads so that they appear in
    # .env.example and an operator can discover them without reading the source.
    SecretSpec(
        "REDIS_MAX_CONNECTIONS", CAT_INFRA,
        "Connection-pool ceiling per process (default 24). Bounds how many "
        "sockets a traffic burst can open against Redis; raising it is rarely "
        "the fix for slowness, since a saturated pool usually means a slow "
        "command rather than too few connections.",
        example="24", rotation="N/A",
    ),
    SecretSpec(
        "REDIS_CONNECT_TIMEOUT_SECONDS", CAT_INFRA,
        "TCP connect timeout (default 1.5). Must stay below "
        "HEALTH_PROBE_TIMEOUT_SECONDS or a slow-to-accept Redis makes the "
        "readiness probe time out instead of reporting a failed Redis check.",
        example="1.5", rotation="N/A",
    ),
    SecretSpec(
        "REDIS_SOCKET_TIMEOUT_SECONDS", CAT_INFRA,
        "Per-command timeout (default 2.0). Redis answers in microseconds, so "
        "reaching this means the server is blocked and waiting longer only ties "
        "up the event loop.",
        example="2.0", rotation="N/A",
    ),
    SecretSpec(
        "REDIS_HEALTH_CHECK_INTERVAL_SECONDS", CAT_INFRA,
        "PING a pooled connection before use if idle longer than this (default "
        "30). What makes a Redis RESTART invisible: without it the first "
        "operations after a restart each fail once on a stale pooled socket.",
        example="30", rotation="N/A",
    ),
    SecretSpec(
        "REDIS_RETRY_ATTEMPTS", CAT_INFRA,
        "Retries after the first attempt, for connection errors and timeouts "
        "only (default 2). Covers one dead pooled connection without turning a "
        "real outage into a latency multiplier.",
        example="2", rotation="N/A",
    ),
    SecretSpec(
        "REDIS_CIRCUIT_FAILURE_THRESHOLD", CAT_INFRA,
        "Consecutive connection failures before the circuit breaker opens and "
        "the process degrades to its in-memory cache (default 5). One failure "
        "is noise; five in a row is a dependency.",
        example="5", rotation="N/A",
    ),
    SecretSpec(
        "REDIS_CIRCUIT_RESET_SECONDS", CAT_INFRA,
        "How long the breaker stays open before admitting one trial operation "
        "(default 10). Long enough not to hammer a down Redis, short enough to "
        "notice recovery within a few seconds.",
        example="10", rotation="N/A",
    ),
    SecretSpec(
        "REDIS_STATS_INTERVAL_SECONDS", CAT_INFRA,
        "Cadence of the background INFO sample that fills the redis_server_* "
        "metrics (default 30; 0 disables). Sampled on a timer, never at scrape "
        "time, so a metrics scraper cannot drive load onto Redis.",
        example="30", rotation="N/A",
    ),
    # ── Admin bootstrap (dev seed only; never used by the API server) ─────────
    SecretSpec(
        "ADMIN_EMAIL", CAT_ADMIN, "Dev-seed admin email (scripts/seed_dev_admin.py only).",
        example="admin@alphapartner.com", rotation="N/A",
    ),
    SecretSpec(
        "ADMIN_PASSWORD", CAT_ADMIN,
        "Dev-seed admin password (scripts/seed_dev_admin.py only; the seeder refuses to run in production).",
        sensitive=True, example="", rotation="N/A (dev only)",
    ),
    SecretSpec(
        "ENABLE_AUTO_LOGIN", CAT_ADMIN,
        "Dev convenience flag; MUST be false/unset in production.",
        example="false", rotation="N/A",
    ),
    # ── Observability (PH2.5) ────────────────────────────────────────────────
    # Registered here for the same reason every other variable is: this registry
    # is the authoritative description of the configuration surface and it
    # generates backend/.env.example. A variable that changes production
    # behaviour but is absent from the registry is undocumented by construction,
    # and the next operator finds it only by reading source.
    SecretSpec(
        "APP_VERSION", CAT_APPCFG,
        "Application version reported by /api/diagnostics and the app_info metric. "
        "Set from the Docker build arg of the same name; defaults to 0.0.0-dev.",
        example="0.0.0-dev", rotation="N/A (set per build)",
    ),
    SecretSpec(
        "VCS_REF", CAT_APPCFG,
        "Git commit this build came from; reported by /api/diagnostics. Set by CI at image build time.",
        example="", rotation="N/A (set per build)",
    ),
    SecretSpec(
        "BUILD_DATE", CAT_APPCFG,
        "ISO-8601 image build timestamp; reported by /api/diagnostics. Set by CI at image build time.",
        example="", rotation="N/A (set per build)",
    ),
    SecretSpec(
        "LOG_LEVEL", CAT_APPCFG,
        "Root log level: DEBUG | INFO | WARNING | ERROR (case-insensitive). "
        "Defaults to INFO; an unrecognized value falls back to INFO rather than "
        "failing the boot. docker/entrypoint.sh separately normalizes this to "
        "lowercase for uvicorn's own --log-level flag, which is case-sensitive.",
        example="info", rotation="N/A",
    ),
    SecretSpec(
        "LOG_FORMAT", CAT_APPCFG,
        "Log output format: json | text. Defaults to json in production, text elsewhere.",
        example="text", rotation="N/A",
    ),
    SecretSpec(
        "LOG_SCRUB_MESSAGES", CAT_APPCFG,
        "Blank credential-shaped values (token=..., password=...) inside free-text "
        "log messages. Defaults to on; turn off only to debug the scrubber itself.",
        example="1", rotation="N/A",
    ),
    SecretSpec(
        "LOG_HEALTH_REQUESTS", CAT_APPCFG,
        "Access-log health/metrics probe traffic. Off by default — a 10s probe "
        "cadence across replicas is tens of thousands of identical lines a day. "
        "Metrics still count every probe.",
        example="0", rotation="N/A",
    ),
    # ── PH2.6 file sinks ──────────────────────────────────────────────────────
    # All optional and all off by default: stdout remains the twelve-factor
    # default and the recommended path. These exist for deployments with no
    # collector yet, or with a compliance rule that requires durable local
    # retention. Every one of them degrades to its default with a stderr warning
    # rather than failing the boot — a logging misconfiguration must never be
    # able to stop a deployment.
    SecretSpec(
        "LOG_TO_FILES", CAT_APPCFG,
        "Write structured logs to rotated files IN ADDITION to stdout. Off by "
        "default. Enable for a host with no log collector, or where a retention "
        "rule requires logs on durable storage. Never replaces stdout, so "
        "`docker logs` and any attached collector are unaffected.",
        example="0", rotation="N/A",
    ),
    SecretSpec(
        "LOG_DIR", CAT_APPCFG,
        "Directory for log files when LOG_TO_FILES is on. Defaults to "
        "/var/log/stockassist (the FHS location, pre-created in the image and "
        "owned by the runtime user). An unwritable directory degrades to "
        "stdout-only with a warning rather than failing the boot.",
        example="/var/log/stockassist", rotation="N/A",
    ),
    SecretSpec(
        "LOG_FILE_STREAMS", CAT_APPCFG,
        "Comma-separated subset of streams to write to file: application, "
        "access, security, audit, error. Defaults to all five. Use a subset "
        "when a collector already takes everything but the audit trail must "
        "also be retained locally (LOG_FILE_STREAMS=audit,security).",
        example="", rotation="N/A",
    ),
    SecretSpec(
        "LOG_FILE_MAX_BYTES", CAT_APPCFG,
        "Size at which a log file is rotated (default 52428800 = 50 MB). "
        "Clamped to [64 KiB, 2 GiB]; an out-of-range value is clamped with a "
        "warning, never rejected.",
        example="52428800", rotation="N/A",
    ),
    SecretSpec(
        "LOG_FILE_BACKUP_COUNT", CAT_APPCFG,
        "Rotated segments kept per stream (default 10). With the default size "
        "that bounds one stream at ~550 MB uncompressed, ~100 MB gzipped. Zero "
        "means bound the live file and keep no history.",
        example="10", rotation="N/A",
    ),
    SecretSpec(
        "LOG_RETENTION_DAYS", CAT_APPCFG,
        "Maximum age of a rotated segment (default 14). Applied BEFORE the "
        "backup count, so an age-based retention commitment is honoured even "
        "when the count would have kept the segment. Zero disables age-based "
        "pruning and leaves the count as the only bound.",
        example="14", rotation="N/A",
    ),
    SecretSpec(
        "LOG_FILE_COMPRESS", CAT_APPCFG,
        "Gzip rotated segments (default on). JSON logs compress at roughly "
        "10:1. Compression runs on the log writer thread, never on a request.",
        example="1", rotation="N/A",
    ),
    SecretSpec(
        "LOG_QUEUE_SIZE", CAT_APPCFG,
        "Records buffered between the application and the log writer thread "
        "(default 10000, clamped to [100, 1000000]). The queue is what keeps "
        "disk I/O off the event loop. When it is full records are DROPPED, not "
        "blocked on, and counted by the log_records_dropped_total metric.",
        example="10000", rotation="N/A",
    ),
    SecretSpec(
        "METRICS_TOKEN", CAT_INFRA,
        "Shared secret required to read /api/metrics and /api/diagnostics IN "
        "PRODUCTION. Unset in production, both endpoints fail closed with 403. "
        "Presented as 'Authorization: Bearer <token>' or 'X-Metrics-Token'.",
        sensitive=True, min_length=MIN_SIGNING_SECRET_LENGTH,
        example="generate-with: python -c \"import secrets;print(secrets.token_urlsafe(32))\"",
        rotation="Rotate on exposure or when scrape credentials change",
    ),
    SecretSpec(
        "METRICS_ALLOW_UNAUTHENTICATED", CAT_INFRA,
        "Serve /api/metrics and /api/diagnostics without a token in production. "
        "ONLY for a deployment where those paths are unreachable from outside "
        "(private scrape network / mesh-only listener).",
        example="", rotation="N/A",
    ),
    SecretSpec(
        "METRICS_MAX_SERIES", CAT_INFRA,
        "Per-metric ceiling on distinct label combinations (default 500). A "
        "backstop against a mislabelling bug exhausting memory; overflow is "
        "counted by the metrics_series_dropped_total gauge.",
        example="500", rotation="N/A",
    ),
    SecretSpec(
        "HEALTH_PROBE_TIMEOUT_SECONDS", CAT_INFRA,
        "Per-dependency readiness probe timeout (default 2.0). Must stay well "
        "below the load balancer's probe timeout.",
        example="2.0", rotation="N/A",
    ),
    SecretSpec(
        "HEALTH_CACHE_TTL_SECONDS", CAT_INFRA,
        "How long a readiness result is reused (default 2.0). Prevents several "
        "independent pollers from generating continuous ping load on MongoDB.",
        example="2.0", rotation="N/A",
    ),
]

# Fast lookup by name.
_REGISTRY_BY_NAME: Dict[str, SecretSpec] = {spec.name: spec for spec in SECRET_REGISTRY}


# --------------------------------------------------------------------------- #
# Secret sources & resolution (PH2.3)                                           #
#                                                                               #
#   Docker / Swarm / Kubernetes secret                                          #
#            │  (tmpfs- or projected-volume-mounted file)                       #
#            ▼                                                                  #
#   ┌─────────────────────────────────────────────────────────────┐             #
#   │  resolve_all()  — ONE precedence order, every variable       │             #
#   │    1. <NAME>_FILE          explicit path      (highest)      │             #
#   │    2. $SECRETS_DIR/<name>  discovered mount                  │             #
#   │    3. <NAME>               plaintext env      (lowest)       │             #
#   └─────────────────────────────────────────────────────────────┘             #
#            │                                                                  #
#            ▼   load_secrets() materializes into os.environ once               #
#   security.jwt · security.csrf · security.recovery · services.* · server.py    #
#                                                                               #
# WHY FILES BEAT ENVIRONMENT VARIABLES                                           #
# An environment variable is inherited by every child process, is readable at    #
# /proc/<pid>/environ, is printed verbatim by `docker inspect`, and is captured  #
# by most crash reporters and APM agents. A file is readable only by a process   #
# that opens that path, is not inherited, does not appear in container metadata, #
# and — for Docker secrets — lives in a tmpfs that never touches disk.           #
# --------------------------------------------------------------------------- #
# `DEFAULT_SECRETS_DIR` (the Docker/Swarm/K8s secret mount point, overridable via
# SECRETS_DIR) and `FILE_SUFFIX` (the `<NAME>_FILE` pointer convention the
# official postgres/mysql/mongo images established) are declared near the top of
# this module, alongside the other constants, because SECRET_REGISTRY references
# them at import time.
#
# A mounted secret is a credential, not a payload. Anything larger than this is a
# mistake (a mounted directory listing, a wrong volume, a log file) and reading it
# wholesale into memory and then into os.environ would be the actual harm.
MAX_SECRET_FILE_BYTES = 64 * 1024

# Where a resolved value came from. Reported per-variable so an operator can see,
# at a glance, which secrets are still arriving in plaintext.
SOURCE_FILE_REF = "file"          # <NAME>_FILE pointed at a path
SOURCE_SECRETS_DIR = "secrets-dir"  # discovered under $SECRETS_DIR
SOURCE_ENV = "env"                # plaintext environment variable
SOURCE_ABSENT = "absent"

# Sources that keep the value off the process environment of every child and out
# of `docker inspect`.
FILE_SOURCES = (SOURCE_FILE_REF, SOURCE_SECRETS_DIR)


class SecretSourceError(RuntimeError):
    """A configured secret source could not be read. Carries the *path*, never
    the contents."""


# A reader maps an absolute path to its contents, or raises. Injected so the
# resolution matrix is testable without touching a filesystem.
SecretReader = Callable[[str], str]


def _default_reader(path: str) -> str:
    """Read a secret file from the real filesystem.

    Guards, in order: the path must exist and be a regular file (a mounted
    *directory* is the classic Docker volume typo and produces a baffling
    IsADirectoryError deep in startup), and it must be within
    :data:`MAX_SECRET_FILE_BYTES`.
    """
    if not os.path.exists(path):
        raise SecretSourceError(f"secret file not found: {path}")
    if not os.path.isfile(path):
        raise SecretSourceError(
            f"secret path is not a regular file (mounted a directory?): {path}"
        )
    try:
        size = os.path.getsize(path)
        if size > MAX_SECRET_FILE_BYTES:
            raise SecretSourceError(
                f"secret file is {size} bytes, over the {MAX_SECRET_FILE_BYTES}-byte "
                f"limit — this is almost certainly the wrong file: {path}"
            )
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except SecretSourceError:
        raise
    except OSError as exc:
        # Permission denied is the common one: a secret mounted 0400 root:root
        # into a container running as uid 10001.
        raise SecretSourceError(f"cannot read secret file {path}: {exc.strerror}") from exc
    except UnicodeDecodeError as exc:
        raise SecretSourceError(
            f"secret file {path} is not valid UTF-8 text — a binary key must be "
            f"base64-encoded before mounting"
        ) from exc


def _null_reader(path: str) -> str:
    """A reader with no filesystem behind it — every path is absent.

    Used when a caller supplies an explicit ``environ`` mapping and no reader.
    The contract for "here is the exact environment to evaluate" is a *closed
    world*: a test (or a drift check, or the env-example generator) must produce
    the same answer on a laptop, in CI, and inside a container that happens to
    have a real ``/run/secrets`` mounted. Silently consulting the host filesystem
    from a supposedly pure call is how a test suite starts passing for the wrong
    reason.
    """
    raise SecretSourceError(f"no filesystem available for {path}")


def _clean_file_value(raw: str) -> str:
    """Normalize file contents into a secret value.

    Surrounding whitespace is stripped because essentially every tool that
    writes a secret file appends a trailing newline (``echo``, ``printf '%s\\n'``,
    every text editor, ``kubectl create secret --from-file``), and a JWT signed
    with ``"key\\n"`` fails to verify against ``"key"`` in a way that is genuinely
    hard to diagnose. This matches :func:`get`, which already strips every value
    read from the environment, so one rule applies to both sources.
    """
    return raw.strip()


@dataclass(frozen=True)
class ResolvedSecret:
    """One variable, resolved. ``value`` is excluded from ``repr`` so an
    accidental ``print(resolution)`` in a log cannot leak it."""

    name: str
    value: Optional[str] = field(default=None, repr=False, compare=False)
    source: str = SOURCE_ABSENT
    origin: str = ""  # the path, for file sources; "" otherwise

    @property
    def from_file(self) -> bool:
        return self.source in FILE_SOURCES

    @property
    def present(self) -> bool:
        return bool(self.value)


@dataclass
class SecretResolution:
    """Outcome of one resolution pass: what each variable resolved to, where it
    came from, and every problem found while looking. Safe to log in full — it
    carries names, sources and paths, never values."""

    resolved: Dict[str, ResolvedSecret] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def values(self) -> Dict[str, str]:
        """The resolved name → value mapping, present values only."""
        return {n: r.value for n, r in self.resolved.items() if r.value}

    def names_from(self, *sources: str) -> List[str]:
        return sorted(n for n, r in self.resolved.items()
                      if r.source in sources and r.present)

    def summary_line(self) -> str:
        from_file = len(self.names_from(*FILE_SOURCES))
        from_env = len(self.names_from(SOURCE_ENV))
        return (f"[secrets] sources: file={from_file} env={from_env} "
                f"errors={len(self.errors)}")


def _candidate_paths(name: str, secrets_dir: str) -> Tuple[str, ...]:
    """Filenames a Docker/K8s secret for ``name`` might plausibly be mounted at.

    Both cases are checked because the ecosystem is split: Compose/Swarm secret
    names are conventionally lowercase (``jwt_secret``) while a Kubernetes secret
    key is usually written exactly as the variable (``JWT_SECRET``). Checking one
    and not the other would make the feature work on one platform and silently
    not on the other — the worst kind of half-support.
    """
    seen: List[str] = []
    for candidate in (name, name.lower()):
        path = os.path.join(secrets_dir, candidate)
        if path not in seen:
            seen.append(path)
    return tuple(seen)


def resolve_secret(name: str,
                   environ: Mapping[str, str],
                   secrets_dir: str,
                   reader: SecretReader,
                   errors: List[str],
                   warnings: List[str],
                   discover: bool = True,
                   ignore_env: Optional[Set[str]] = None) -> ResolvedSecret:
    """Resolve one variable against the full precedence order.

    Precedence, highest first:

    1. ``<NAME>_FILE`` — an explicit path. Highest because it is an unambiguous
       operator instruction: "this value lives there, not here."
    2. ``$SECRETS_DIR/<name>`` — a discovered mount (``discover=False`` turns
       this off for variables that should never be auto-discovered).
    3. ``<NAME>`` — a plaintext environment variable. The development fallback,
       and the reason an existing ``.env`` workflow keeps working unchanged.

    **Two sources for the same variable is an error, not a merge.** It means two
    people (or two layers of a deployment) each believe they own that value, and
    silently picking one would let a rotated secret be shadowed by a stale one —
    a failure that surfaces days later as mysterious auth errors. Fail closed and
    make the operator delete one.

    With one deliberate exception: a successful ``_FILE`` pointer **suppresses
    discovery entirely** rather than competing with it. The two are usually the
    same file — ``JWT_SECRET_FILE=/run/secrets/jwt_secret`` names precisely the
    path discovery scans, which is the normal Docker-secrets layout — so treating
    them as rivals would make the documented configuration fail every boot.
    Comparing paths instead would still be wrong on a case-insensitive
    filesystem (macOS, Windows), where ``JWT_SECRET`` and ``jwt_secret`` are one
    file under two names. An explicit pointer is an unambiguous instruction; once
    it resolves there is nothing left to disambiguate. The conflict rule keeps
    its teeth where ambiguity is real: a file source competing with a plaintext
    environment variable, which genuinely means two owners.

    ``ignore_env`` names variables whose environment entry *this module wrote
    itself* on an earlier pass. Without it, the second call to
    :func:`load_secrets` would see the file AND the value it had just
    materialized from that same file, and report a conflict against itself —
    which would make re-hydration (and therefore rotation) impossible.
    """
    file_ref_key = f"{name}{FILE_SUFFIX}"
    file_ref = (environ.get(file_ref_key) or "").strip()
    env_value = "" if (ignore_env and name in ignore_env) else (environ.get(name) or "").strip()

    found: List[ResolvedSecret] = []

    # 1. Explicit pointer.
    if file_ref:
        try:
            value = _clean_file_value(reader(file_ref))
        except SecretSourceError as exc:
            # Fail closed: an unreadable pointer is never silently downgraded to
            # the plaintext variable. That downgrade is exactly how a rotation
            # "succeeds" while the app keeps using the old value.
            errors.append(f"{file_ref_key} → {exc}")
            return ResolvedSecret(name=name, source=SOURCE_ABSENT)
        if not value:
            errors.append(
                f"{file_ref_key} points at '{file_ref}', which is empty. A mounted "
                f"but empty secret is a misconfiguration, not an unset value."
            )
            return ResolvedSecret(name=name, source=SOURCE_ABSENT)
        found.append(ResolvedSecret(name, value, SOURCE_FILE_REF, file_ref))

    # 2. Discovered mount — skipped when an explicit pointer already won, since
    #    it almost always names this very file (see the docstring).
    if discover and not found:
        for path in _candidate_paths(name, secrets_dir):
            try:
                raw = reader(path)
            except SecretSourceError:
                continue  # absent/unreadable during discovery is simply "not there"
            value = _clean_file_value(raw)
            if not value:
                warnings.append(
                    f"{path} exists but is empty — ignoring it. Remove the file or "
                    f"populate it; an empty secret file is always a mistake."
                )
                continue
            found.append(ResolvedSecret(name, value, SOURCE_SECRETS_DIR, path))
            break

    # 3. Plaintext environment.
    if env_value:
        found.append(ResolvedSecret(name, env_value, SOURCE_ENV, ""))

    if not found:
        return ResolvedSecret(name=name, source=SOURCE_ABSENT)

    if len(found) > 1:
        described = ", ".join(
            f"{r.source}({r.origin})" if r.origin else r.source for r in found
        )
        errors.append(
            f"{name} is supplied by more than one source ({described}). Exactly one "
            f"source must own a secret — remove the others so a rotation cannot be "
            f"shadowed by a stale value."
        )

    return found[0]


def resolve_all(environ: Optional[Mapping[str, str]] = None,
                secrets_dir: Optional[str] = None,
                reader: Optional[SecretReader] = None,
                ignore_env: Optional[Set[str]] = None) -> SecretResolution:
    """Resolve the whole configuration surface. Pure — reads, never writes.

    ``environ=None`` evaluates the live process environment against the real
    filesystem. An explicit ``environ`` with no explicit ``reader`` evaluates a
    closed world (see :func:`_null_reader`); pass a fake reader to exercise file
    sources hermetically.

    Two families of names are considered:

    * every variable in :data:`SECRET_REGISTRY` (the known surface — these are
      eligible for ``$SECRETS_DIR`` auto-discovery), and
    * every ``<NAME>_FILE`` key present in ``environ``, even for an unregistered
      ``<NAME>``. That keeps the ``_FILE`` convention universal: an operator who
      wants ``JWT_ISSUER_FILE`` gets it without a code change. Auto-discovery is
      *not* extended to unregistered names — a stray file in the secrets
      directory must never be able to invent an environment variable.
    """
    if reader is None:
        reader = _default_reader if environ is None else _null_reader
    environ = environ if environ is not None else os.environ
    resolution = SecretResolution()

    if secrets_dir is None:
        secrets_dir = (environ.get("SECRETS_DIR") or "").strip() or DEFAULT_SECRETS_DIR

    for spec in SECRET_REGISTRY:
        resolution.resolved[spec.name] = resolve_secret(
            spec.name, environ, secrets_dir, reader,
            resolution.errors, resolution.warnings,
            ignore_env=ignore_env,
        )

    for key in sorted(environ):
        if not key.endswith(FILE_SUFFIX) or len(key) <= len(FILE_SUFFIX):
            continue
        base = key[: -len(FILE_SUFFIX)]
        if base in resolution.resolved:
            continue  # already handled above, with discovery enabled
        resolution.resolved[base] = resolve_secret(
            base, environ, secrets_dir, reader,
            resolution.errors, resolution.warnings,
            discover=False, ignore_env=ignore_env,
        )

    return resolution


# Set once :func:`load_secrets` has materialized a resolution into os.environ.
# Only used to make the "did anything hydrate this process?" question answerable
# from a diagnostic; loading twice is harmless and explicitly supported.
_last_resolution: Optional[SecretResolution] = None

# Names this module wrote into os.environ from a file source. Tracked so a second
# pass does not mistake its own materialized value for an independent, competing
# source. See the `ignore_env` note in :func:`resolve_secret`.
_materialized: Set[str] = set()


def _reset_loader_state() -> None:
    """Forget what this process has hydrated. Test-support only — it exists so a
    test can assert first-load behaviour after another test has already loaded."""
    global _last_resolution
    _last_resolution = None
    _materialized.clear()


def load_secrets(secrets_dir: Optional[str] = None,
                 reader: Optional[SecretReader] = None,
                 raise_on_error: bool = False) -> SecretResolution:
    """Resolve every secret and **materialize** the result into ``os.environ``.

    This is the one function that turns file-backed secrets into something the
    rest of the codebase can see. It must run before any module reads
    configuration — ``server.py`` calls it immediately after ``load_dotenv`` and
    before importing anything else, and ``docker/entrypoint.sh`` reaches it
    through :func:`validate_config`.

    Only values that came from a *file* source are written back; a value that was
    already a plaintext environment variable is left exactly as it was, so this
    is a no-op for a development ``.env`` workflow.

    Idempotent and safely re-runnable: calling it again re-reads every file and
    updates ``os.environ`` in place. That is the mechanism behind
    :func:`reload_secrets` — see the rotation notes in docs/deployment/SECRETS.md
    for which secrets that actually rotates and which still need a restart.
    """
    global _last_resolution
    resolution = resolve_all(secrets_dir=secrets_dir, reader=reader,
                             ignore_env=set(_materialized))

    for name, entry in resolution.resolved.items():
        if entry.from_file and entry.value:
            os.environ[name] = entry.value
            _materialized.add(name)
        elif name in _materialized and not entry.present:
            # The file source went away. Drop our own stale write rather than
            # leaving a value in the environment that no source still backs —
            # otherwise a *deleted* secret keeps working, which is the one thing
            # a revocation must never do.
            os.environ.pop(name, None)
            _materialized.discard(name)

    _last_resolution = resolution
    if resolution.errors and raise_on_error:
        raise SecretValidationError(_format_source_errors(resolution))
    return resolution


def reload_secrets(secrets_dir: Optional[str] = None,
                   reader: Optional[SecretReader] = None) -> Dict[str, str]:
    """Re-read file-backed secrets and report which ones **changed**, by
    fingerprint. Never returns or logs a value.

    This works because a rotated Docker config / Kubernetes projected-volume
    secret updates the file *in place* under the same mount path, and because
    nearly every consumer in this codebase reads ``os.environ`` at call time
    rather than capturing it at import. It does **not** make rotation
    consequence-free: rotating ``JWT_SECRET`` invalidates every live session no
    matter how the new value arrived. See docs/deployment/SECRETS.md §6.
    """
    # Registry names, plus anything this process previously materialized — the
    # latter covers unregistered `<NAME>_FILE` variables, which would otherwise
    # rotate invisibly.
    watched = set(_REGISTRY_BY_NAME) | set(_materialized)
    before = {name: fingerprint(os.environ.get(name)) for name in watched}
    load_secrets(secrets_dir=secrets_dir, reader=reader)
    changed: Dict[str, str] = {}
    for name, old in before.items():
        new = fingerprint(os.environ.get(name))
        if new != old:
            changed[name] = f"{old} → {new}"
    return changed


def last_resolution() -> Optional[SecretResolution]:
    """The most recent :func:`load_secrets` result, or ``None`` if this process
    has never hydrated. Diagnostic only."""
    return _last_resolution


def _format_source_errors(resolution: SecretResolution) -> str:
    lines = [
        "",
        "=" * 72,
        " StockAssist AI — secret sources could not be resolved",
        "=" * 72,
        "",
    ]
    lines += [f"   ✗ {e}" for e in resolution.errors]
    lines += ["", " See docs/deployment/SECRETS.md.", "=" * 72, ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Result types                                                                  #
# --------------------------------------------------------------------------- #
class SecretValidationError(RuntimeError):
    """Raised at startup when configuration is invalid for the current
    environment. Its message aggregates every problem; it never contains a
    secret value."""


class MissingSecret(KeyError):
    """Raised by :func:`require` when a demanded variable is absent/empty."""


@dataclass
class ConfigReport:
    """Presence-only summary of a validation pass. Safe to log in full — it
    carries variable names and booleans, never values."""

    environment: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    present: List[str] = field(default_factory=list)
    missing_optional: List[str] = field(default_factory=list)
    # PH2.3 — where each present value came from (name → SOURCE_*). Presence-only
    # by construction: a source name and, for file sources, a path.
    sources: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def from_file(self) -> List[str]:
        """Names whose value arrived from a file (Docker secret / ``_FILE``)."""
        return sorted(n for n, s in self.sources.items() if s in FILE_SOURCES)

    def from_env(self) -> List[str]:
        """Names still arriving as plaintext environment variables."""
        return sorted(n for n, s in self.sources.items() if s == SOURCE_ENV)

    def summary_line(self) -> str:
        return (
            f"[secrets] env={self.environment} "
            f"configured={len(self.present)} "
            f"file-backed={len(self.from_file())} plaintext={len(self.from_env())} "
            f"warnings={len(self.warnings)} errors={len(self.errors)}"
        )


# --------------------------------------------------------------------------- #
# Accessors                                                                     #
# --------------------------------------------------------------------------- #
def get(name: str, default: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Read a configuration value (stripped). Returns ``default`` when unset or
    blank. Prefer this over ``os.environ`` so reads route through one place."""
    # `environ is None` rather than `environ or os.environ` — see app_env().
    # An explicitly empty mapping must read as "unset", not as the host env.
    raw = (os.environ if environ is None else environ).get(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


def require(name: str, environ: Optional[Mapping[str, str]] = None) -> str:
    """Return a required value or raise :class:`MissingSecret`. For call sites
    that must have a value and want a clear error rather than a silent empty."""
    val = get(name, environ=environ)
    if not val:
        raise MissingSecret(name)
    return val


def is_configured(name: str, environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when ``name`` is set to a non-blank, non-placeholder value."""
    val = get(name, environ=environ)
    return bool(val) and not looks_like_placeholder(val)


# --------------------------------------------------------------------------- #
# Cross-field production invariants                                             #
# --------------------------------------------------------------------------- #
def _truthy(value: Optional[str]) -> bool:
    return bool(value) and value.strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# Value-shape validators (PH2.3)                                                #
#                                                                               #
# Presence and length are necessary but not sufficient. Each of the checks below #
# encodes a *specific* production incident this project can now no longer have:  #
# a database URI that silently authenticates as nobody, an internet-reachable    #
# passwordless Redis, an encryption key that is not a valid key, an API key      #
# pasted from the wrong console.                                                 #
# --------------------------------------------------------------------------- #
def uri_has_credentials(uri: str) -> bool:
    """True when a ``scheme://user:pass@host`` style URI carries a password.

    Both parts are required: ``mongodb://user@host`` authenticates as nobody and
    a server with ``--auth`` enabled rejects it with an error that reads like a
    network problem.
    """
    try:
        parts = urlsplit(uri.strip())
    except ValueError:
        return False
    return bool(parts.username) and bool(parts.password)


def uri_host(uri: str) -> str:
    """Hostname of a URI, or ``""`` if it cannot be parsed."""
    try:
        return (urlsplit(uri.strip()).hostname or "").lower()
    except ValueError:
        return ""


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def is_valid_fernet_key(value: str) -> bool:
    """True when ``value`` is a well-formed Fernet key: 32 raw bytes, urlsafe
    base64-encoded (44 characters ending in ``=``).

    Validated up front because ``cryptography`` only raises when the *first*
    token is encrypted or decrypted — which, for ``BROKER_TOKEN_KEY``, is the
    first time a user connects a broker account, long after deployment.
    """
    raw = value.strip()
    if len(raw) != 44:
        return False
    try:
        return len(base64.urlsafe_b64decode(raw)) == 32
    except (ValueError, TypeError):
        return False


def _check_cross_field(env: str, environ: Mapping[str, str],
                       errors: List[str], warnings: List[str]) -> None:
    """Invariants that span more than one variable. Errors in production,
    warnings elsewhere (except where noted)."""
    prod = env == PRODUCTION

    # At least one AI provider must be configured in production — the product's
    # core is the AI engine; booting prod with neither key is a silent failure.
    if prod and not (is_configured("ANTHROPIC_API_KEY", environ)
                     or is_configured("GOOGLE_GEMINI_KEY", environ)):
        errors.append(
            "No AI provider configured: set ANTHROPIC_API_KEY and/or GOOGLE_GEMINI_KEY."
        )

    # OAuth is both-or-neither: a half-configured client is a misconfiguration.
    cid = is_configured("GOOGLE_CLIENT_ID", environ)
    csec = is_configured("GOOGLE_CLIENT_SECRET", environ)
    if cid != csec:
        msg = ("Google OAuth is half-configured: set BOTH GOOGLE_CLIENT_ID and "
               "GOOGLE_CLIENT_SECRET, or neither.")
        (errors if prod else warnings).append(msg)

    # Broker pairs: key without secret (or vice-versa) can never authenticate.
    for label, a, b in (
        ("Zerodha Kite", "KITE_API_KEY", "KITE_API_SECRET"),
        ("Upstox", "UPSTOX_API_KEY", "UPSTOX_API_SECRET"),
    ):
        if is_configured(a, environ) != is_configured(b, environ):
            (errors if prod else warnings).append(
                f"{label} is half-configured: set BOTH {a} and {b}, or neither."
            )

    # Auto-login is a development-only convenience and must never be on in prod.
    if prod and _truthy(get("ENABLE_AUTO_LOGIN", environ=environ)):
        errors.append("ENABLE_AUTO_LOGIN must be false/unset in production.")

    # A weak dev-seed admin password must not linger in a production environment.
    admin_pw = get("ADMIN_PASSWORD", environ=environ)
    if prod and admin_pw and looks_like_placeholder(admin_pw):
        errors.append("ADMIN_PASSWORD is a known weak/default value; unset it in production.")

    # ── Datastore credentials (PH2.3) ────────────────────────────────────────
    # A MongoDB URI with no credentials means the server is running without
    # --auth, or is about to fail every query. In 2017 this exact configuration
    # put tens of thousands of databases into a ransom wave; it is the single
    # highest-consequence thing to get wrong in this file.
    mongo_url = get("MONGO_URL", environ=environ)
    if mongo_url:
        if not uri_has_credentials(mongo_url):
            msg = ("MONGO_URL carries no username:password — the database is either "
                   "unauthenticated or every query will fail authentication.")
            (errors if prod else warnings).append(msg)
        if prod and uri_host(mongo_url) in LOOPBACK_HOSTS:
            warnings.append(
                "MONGO_URL points at a loopback address in production. Confirm this is "
                "an intentional sidecar and not a development value that shipped."
            )

    # Redis has no user model, so an unauthenticated instance is total access —
    # and `CONFIG SET dir` + `SAVE` turns that into arbitrary file write, i.e.
    # remote code execution, not merely a cache leak.
    redis_url = get("REDIS_URL", environ=environ)
    if redis_url:
        try:
            redis_password = urlsplit(redis_url.strip()).password
        except ValueError:
            redis_password = None
        if not redis_password:
            msg = ("REDIS_URL has no password. An unauthenticated Redis is a remote "
                   "code execution primitive (CONFIG SET dir + SAVE), not just a "
                   "cache leak. Use redis://:<password>@host:6379/0.")
            (errors if prod else warnings).append(msg)

    # ── Encryption keys (PH2.3) ──────────────────────────────────────────────
    # Wrong in every environment, not only production: an invalid Fernet key does
    # not fail at boot, it fails the first time a user connects a broker.
    broker_key = get("BROKER_TOKEN_KEY", environ=environ)
    if broker_key and not is_valid_fernet_key(broker_key):
        errors.append(
            "BROKER_TOKEN_KEY is not a valid Fernet key (44-character urlsafe-base64 "
            "of 32 bytes). Generate with: python -c \"from cryptography.fernet import "
            "Fernet; print(Fernet.generate_key().decode())\""
        )

    # ── Provider credential shapes (PH2.3) ───────────────────────────────────
    # Warnings, never errors: a provider is free to change its key format, and
    # blocking a production boot over a prefix would be the wrong trade. But the
    # overwhelmingly common cause of a shape mismatch is a value pasted into the
    # wrong variable, which otherwise surfaces as a 401 from a third party hours
    # later.
    if prod:
        anthropic = get("ANTHROPIC_API_KEY", environ=environ)
        if anthropic and not anthropic.startswith("sk-ant-"):
            warnings.append(
                "ANTHROPIC_API_KEY does not start with 'sk-ant-' — check it was not "
                "swapped with another provider's key."
            )
        client_id = get("GOOGLE_CLIENT_ID", environ=environ)
        if client_id and not client_id.endswith(".apps.googleusercontent.com"):
            warnings.append(
                "GOOGLE_CLIENT_ID does not end with '.apps.googleusercontent.com' — "
                "this is usually the client *secret* or a project id pasted by mistake."
            )

    # Distinct signing keys are recommended in production so rotating one token
    # class does not invalidate the others.
    if prod:
        for name in ("CSRF_SECRET", "RECOVERY_SECRET"):
            if not is_configured(name, environ):
                warnings.append(
                    f"{name} not set — falling back to JWT_SECRET. A dedicated key is recommended in production."
                )
        if not is_configured("BROKER_TOKEN_KEY", environ):
            warnings.append(
                "BROKER_TOKEN_KEY not set — broker tokens are encrypted with a JWT_SECRET-derived key. "
                "A dedicated Fernet key is recommended in production."
            )
        if not is_configured("WEBHOOK_API_KEY", environ):
            warnings.append(
                "WEBHOOK_API_KEY not set — inbound automation webhooks are disabled."
            )


# --------------------------------------------------------------------------- #
# The validator                                                                 #
# --------------------------------------------------------------------------- #
# The minimal set the server literally cannot construct without — enforced as a
# hard error in EVERY environment (server.py reads these unconditionally).
CORE_REQUIRED: Set[str] = {"MONGO_URL", "DB_NAME", "JWT_SECRET"}


def validate_config(environ: Optional[Mapping[str, str]] = None,
                    raise_on_error: bool = True,
                    secrets_dir: Optional[str] = None,
                    reader: Optional[SecretReader] = None) -> ConfigReport:
    """Resolve every secret source, then validate the result for the current
    environment.

    Two modes, deliberately:

    * ``environ=None`` (startup) — calls :func:`load_secrets`, which resolves
      file-backed secrets and **materializes them into ``os.environ``**, then
      validates the hydrated environment. This is the path ``server.py`` and
      ``docker/entrypoint.sh`` take, and it is why validation and the running
      application always see exactly the same values.

    * ``environ=<mapping>`` (tests, scripts, drift checks) — resolves *against
      that mapping* and validates the result without touching ``os.environ``.
      Pure and hermetic.

    Collects every problem into a :class:`ConfigReport`. When ``raise_on_error``
    is true (the default, used at startup) and any error was found, raises
    :class:`SecretValidationError` with an aggregated, value-free message so the
    process fails closed. Returns the report either way (tests pass
    ``raise_on_error=False`` to inspect it).
    """
    # ── Stage 1: resolve sources ────────────────────────────────────────────
    if environ is None:
        resolution = load_secrets(secrets_dir=secrets_dir, reader=reader)
        effective: Mapping[str, str] = os.environ
    else:
        resolution = resolve_all(environ=environ, secrets_dir=secrets_dir, reader=reader)
        # A read-only view of the resolved world: the caller's mapping with every
        # file-backed value layered on top, exactly as load_secrets would have
        # written it into os.environ.
        effective = {**environ, **resolution.values()}

    env = app_env(effective)
    report = ConfigReport(environment=env)
    # Source problems are configuration errors like any other, and belong in the
    # same aggregated report — an operator should never have to fix an unreadable
    # secret file, restart, and only then discover a missing variable.
    report.errors.extend(resolution.errors)
    report.warnings.extend(resolution.warnings)
    report.sources = {n: r.source for n, r in resolution.resolved.items() if r.present}

    # Unrecognized APP_ENV is a hard misconfiguration — we do not want to guess.
    raw_env = effective.get("APP_ENV", "").strip().lower()
    if raw_env and raw_env not in KNOWN_ENVIRONMENTS:
        report.errors.append(
            f"APP_ENV='{raw_env}' is not one of {sorted(KNOWN_ENVIRONMENTS)}."
        )

    prod = env == PRODUCTION
    # Opt-in strict posture: once a deployment has finished migrating to
    # file-backed secrets, this turns "still plaintext" from an advisory into a
    # boot failure, so a regression cannot creep back in unnoticed.
    require_files = _truthy(get("REQUIRE_FILE_SECRETS", environ=effective))

    for spec in SECRET_REGISTRY:
        raw = effective.get(spec.name)
        value = raw.strip() if raw else ""
        present = bool(value)
        required = env in spec.required_in or spec.name in CORE_REQUIRED

        if present:
            report.present.append(spec.name)
        elif required:
            report.errors.append(f"{spec.name} is required in {env} but is not set.")
            continue
        else:
            report.missing_optional.append(spec.name)
            continue  # nothing more to check on an absent optional value

        # -- value is present: quality checks --------------------------------
        if looks_like_placeholder(value):
            msg = f"{spec.name} looks like a placeholder / weak default value."
            if prod or required:
                report.errors.append(msg)
            else:
                report.warnings.append(msg)

        if spec.min_length and len(value) < spec.min_length:
            msg = (f"{spec.name} is shorter than the recommended "
                   f"{spec.min_length} characters for a signing secret.")
            # Hard requirement for the always-required signing key; strict in prod.
            if prod or spec.name in CORE_REQUIRED:
                report.errors.append(msg)
            else:
                report.warnings.append(msg)

        # Low-entropy values that clear the length bar (PH2.3). Only sensitive
        # variables: a low-entropy DB_NAME or EMAIL_FROM is not a finding. And
        # only generated ones — see SecretSpec.entropy_checked.
        if spec.sensitive and spec.entropy_checked and looks_weak(value):
            msg = (f"{spec.name} has very low entropy (too short, or too few distinct "
                   f"characters) — it will not survive an offline attack.")
            if prod:
                report.errors.append(msg)
            else:
                report.warnings.append(msg)

        # -- delivery posture (PH2.3) ----------------------------------------
        # The residual risk this sprint exists to make visible: a sensitive value
        # arriving as a plaintext environment variable is exposed via
        # `docker inspect`, /proc/<pid>/environ, and every child process.
        if spec.sensitive and report.sources.get(spec.name) == SOURCE_ENV:
            msg = (f"{spec.name} is delivered as a plaintext environment variable. "
                   f"Prefer a file source ({spec.name}{FILE_SUFFIX} or a Docker "
                   f"secret) — see docs/deployment/SECRETS.md.")
            if require_files:
                report.errors.append(msg)
            elif prod:
                report.warnings.append(msg)

    _check_cross_field(env, effective, report.errors, report.warnings)

    if report.errors and raise_on_error:
        raise SecretValidationError(_format_error(report))

    return report


def _format_error(report: ConfigReport) -> str:
    lines = [
        "",
        "=" * 72,
        f" StockAssist AI — configuration invalid for APP_ENV={report.environment}",
        "=" * 72,
        " The process was stopped before startup because required secrets are",
        " missing or misconfigured. Fix the following and restart:",
        "",
    ]
    lines += [f"   ✗ {e}" for e in report.errors]
    if report.warnings:
        lines.append("")
        lines.append(" Warnings (non-fatal):")
        lines += [f"   • {w}" for w in report.warnings]
    lines += [
        "",
        " See .claude/SECRETS.md for the full inventory and remediation steps.",
        "=" * 72,
        "",
    ]
    return "\n".join(lines)


def registry_for_category(category: str) -> Iterable[SecretSpec]:
    """All specs in a category, in registry order (used by the example
    generator and docs)."""
    return (s for s in SECRET_REGISTRY if s.category == category)


def get_spec(name: str) -> Optional[SecretSpec]:
    """Return the :class:`SecretSpec` for ``name``, or ``None`` if unregistered."""
    return _REGISTRY_BY_NAME.get(name)

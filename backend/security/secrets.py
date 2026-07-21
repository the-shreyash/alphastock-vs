"""Centralized secret & configuration management (PH1.9).

Single source of truth for **which** environment variables StockAssist AI
depends on, **which are required in which environment**, and **whether the
process is allowed to start** given what is (and isn't) configured. This is the
secrets/supply-chain counterpart to the other ``security.*`` policy modules: the
one place that knows the shape of the app's configuration surface.

Design decisions (see SECRETS.md / SECURITY_ARCHITECTURE.md §23–§24, PH1.9):

* **Fail fast, fail closed.** ``validate_config()`` runs once at process
  startup (called from ``server.py`` immediately after ``load_dotenv`` and
  *before* the Mongo client or any router is constructed). A missing critical
  secret aborts the boot with a single, aggregated, human-readable error instead
  of a raw ``KeyError`` surfacing deep in a request handler. Every problem is
  collected and reported together so an operator fixes the whole environment in
  one pass, not one variable per crash-loop.

* **Environment-aware severity.** The same registry drives all three
  environments. In ``production`` the rules are strict: required secrets must be
  present, signing secrets must meet a minimum length, and known placeholder /
  weak-default values are rejected outright. In ``development`` (and to a lesser
  extent ``staging``) the *same* findings degrade to warnings so a laptop with a
  half-filled ``.env`` still boots — except for the small core trio the server
  genuinely cannot run without (``MONGO_URL``, ``DB_NAME``, ``JWT_SECRET``),
  which are hard requirements everywhere.

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
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Set

# Reuse the single production-detection primitive so environment semantics never
# drift between the cookie policy, CORS, and secret validation.
from security.cookies import is_production  # noqa: F401  (re-exported)

# --------------------------------------------------------------------------- #
# Environment model                                                             #
# --------------------------------------------------------------------------- #
DEVELOPMENT = "development"
STAGING = "staging"
PRODUCTION = "production"
KNOWN_ENVIRONMENTS: Set[str] = {DEVELOPMENT, STAGING, PRODUCTION}


def app_env(environ: Optional[Mapping[str, str]] = None) -> str:
    """Normalized deployment environment: one of ``development`` /
    ``staging`` / ``production``.

    Reads ``APP_ENV`` (default ``development``). An unrecognized value is
    *reported* by the validator but resolves here to ``development`` so callers
    always receive a valid member of :data:`KNOWN_ENVIRONMENTS`.
    """
    env = (environ or os.environ).get("APP_ENV", DEVELOPMENT).strip().lower()
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


def looks_like_placeholder(value: str) -> bool:
    """True when ``value`` contains a known placeholder / weak-default marker."""
    low = value.strip().lower()
    if not low:
        return False
    return any(marker in low for marker in PLACEHOLDER_MARKERS)


def redact(value: Optional[str]) -> str:
    """Collapse a secret to a fixed mask for the rare diagnostic that must
    reference it. Never reveals length or content."""
    return "********" if value else "<unset>"


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
        "APP_ENV", CAT_APPCFG, "Deployment environment: development | staging | production.",
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
]

# Fast lookup by name.
_REGISTRY_BY_NAME: Dict[str, SecretSpec] = {spec.name: spec for spec in SECRET_REGISTRY}


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

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary_line(self) -> str:
        return (
            f"[secrets] env={self.environment} "
            f"configured={len(self.present)} "
            f"warnings={len(self.warnings)} errors={len(self.errors)}"
        )


# --------------------------------------------------------------------------- #
# Accessors                                                                     #
# --------------------------------------------------------------------------- #
def get(name: str, default: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Read a configuration value (stripped). Returns ``default`` when unset or
    blank. Prefer this over ``os.environ`` so reads route through one place."""
    raw = (environ or os.environ).get(name)
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
                    raise_on_error: bool = True) -> ConfigReport:
    """Validate the process configuration for the current environment.

    Collects every problem into a :class:`ConfigReport`. When ``raise_on_error``
    is true (the default, used at startup) and any error was found, raises
    :class:`SecretValidationError` with an aggregated, value-free message so the
    process fails closed. Returns the report either way (tests pass
    ``raise_on_error=False`` to inspect it).
    """
    environ = environ if environ is not None else os.environ
    env = app_env(environ)
    report = ConfigReport(environment=env)

    # Unrecognized APP_ENV is a hard misconfiguration — we do not want to guess.
    raw_env = environ.get("APP_ENV", "").strip().lower()
    if raw_env and raw_env not in KNOWN_ENVIRONMENTS:
        report.errors.append(
            f"APP_ENV='{raw_env}' is not one of {sorted(KNOWN_ENVIRONMENTS)}."
        )

    prod = env == PRODUCTION

    for spec in SECRET_REGISTRY:
        raw = environ.get(spec.name)
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

    _check_cross_field(env, environ, report.errors, report.warnings)

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

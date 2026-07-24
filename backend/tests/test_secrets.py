"""Tests for the centralized secret & configuration validator (PH1.9).

Hermetic and pure: every case passes an explicit ``environ`` mapping to
``validate_config`` so nothing depends on the process environment, the real
``.env``, Mongo, or any network. We assert on the aggregated
:class:`ConfigReport` (``raise_on_error=False``) and, separately, that a fatal
config raises :class:`SecretValidationError`.

Coverage:
* development boots with the minimal core trio; weak/placeholder optionals are
  warnings, not errors,
* production is strict: missing required secrets, short signing keys, and
  placeholder values are hard errors,
* the core trio (MONGO_URL / DB_NAME / JWT_SECRET) is required in EVERY env,
* cross-field invariants (AI provider present, OAuth both-or-neither, broker
  pairs, ENABLE_AUTO_LOGIN off in prod),
* no secret value ever appears in the error text or the report (redaction),
* registry integrity, and the accessors (get / require / is_configured).
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from security import secrets as sc  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
STRONG_JWT = "Zt7Qv3La9Rb2Nc8Kd1Pe6Mf4Sg0Wh5Yj-strong-key"  # > 32 chars, no placeholder markers


def base_prod_env(**overrides):
    """A minimal, VALID production environment; override to break one thing.

    ``MONGO_URL`` carries credentials because PH2.3 requires it to in production:
    a credential-free URI means the database is either unauthenticated or every
    query fails auth. See ``test_production_mongo_url_without_credentials``.
    """
    env = {
        "APP_ENV": "production",
        "MONGO_URL": "mongodb://app_user:t9Wq2Lm5Rv8Bn3Xz@db:27017/alpha_stock_db?authSource=alpha_stock_db",
        "DB_NAME": "alpha_stock_db",
        "JWT_SECRET": STRONG_JWT,
        "FRONTEND_URL": "https://app.stockassist.ai",
        "CORS_ALLOWED_ORIGINS": "https://app.stockassist.ai",
        "ANTHROPIC_API_KEY": "sk-ant-live-abc123",
    }
    env.update(overrides)
    return env


# --------------------------------------------------------------------------- #
# Environment resolution                                                         #
# --------------------------------------------------------------------------- #
def test_app_env_defaults_to_development():
    assert sc.app_env({}) == sc.DEVELOPMENT


def test_app_env_normalizes_and_rejects_unknown():
    assert sc.app_env({"APP_ENV": "  Production "}) == sc.PRODUCTION
    assert sc.app_env({"APP_ENV": "staging"}) == sc.STAGING
    assert sc.app_env({"APP_ENV": "  Testing "}) == sc.TESTING
    # Unknown resolves to development (but the validator flags it — see below).
    assert sc.app_env({"APP_ENV": "prod"}) == sc.DEVELOPMENT


def test_testing_is_a_recognized_environment():
    # PH2.8: `testing` is first-class, not an alias — and never confused with prod.
    assert sc.TESTING in sc.KNOWN_ENVIRONMENTS
    assert sc.TESTING in sc.LENIENT_ENVIRONMENTS
    assert sc.PRODUCTION not in sc.LENIENT_ENVIRONMENTS
    report = sc.validate_config({"APP_ENV": "testing", "MONGO_URL": "m",
                                 "DB_NAME": "db", "JWT_SECRET": STRONG_JWT},
                                raise_on_error=False)
    assert report.environment == sc.TESTING
    # A recognized env must NOT raise the "unknown APP_ENV" error.
    assert not any("is not one of" in e for e in report.errors)


def test_testing_is_lenient_like_development():
    # Placeholder optionals are warnings (as in development), not fatal errors.
    env = {"APP_ENV": "testing", "MONGO_URL": "mongodb://localhost:27017",
           "DB_NAME": "db", "JWT_SECRET": STRONG_JWT,
           "KITE_API_KEY": "testkey", "KITE_API_SECRET": "testsecret"}
    report = sc.validate_config(env, raise_on_error=False)
    assert report.ok, report.errors
    assert any("KITE_API_KEY" in w for w in report.warnings)


def test_unknown_app_env_is_reported_error():
    report = sc.validate_config(base_prod_env(APP_ENV="prod"), raise_on_error=False)
    assert any("APP_ENV" in e for e in report.errors)


# --------------------------------------------------------------------------- #
# Development: lenient                                                           #
# --------------------------------------------------------------------------- #
def test_development_minimal_config_ok():
    env = {"MONGO_URL": "mongodb://localhost:27017",
           "DB_NAME": "alpha_stock_db", "JWT_SECRET": STRONG_JWT}
    report = sc.validate_config(env, raise_on_error=False)
    assert report.ok, report.errors
    assert report.environment == sc.DEVELOPMENT


def test_development_placeholder_optional_is_warning_not_error():
    env = {"MONGO_URL": "mongodb://localhost:27017", "DB_NAME": "db",
           "JWT_SECRET": STRONG_JWT, "KITE_API_KEY": "testkey",
           "KITE_API_SECRET": "testsecret"}
    report = sc.validate_config(env, raise_on_error=False)
    assert report.ok, report.errors
    assert any("KITE_API_KEY" in w for w in report.warnings)


def test_development_short_jwt_is_still_fatal_core_trio():
    # JWT_SECRET is core-required everywhere and must meet min length everywhere.
    env = {"MONGO_URL": "m", "DB_NAME": "db", "JWT_SECRET": "short"}
    report = sc.validate_config(env, raise_on_error=False)
    assert not report.ok
    assert any("JWT_SECRET" in e and "characters" in e for e in report.errors)


# --------------------------------------------------------------------------- #
# Core trio required in every environment                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("env_name", ["development", "testing", "staging", "production"])
@pytest.mark.parametrize("missing", ["MONGO_URL", "DB_NAME", "JWT_SECRET"])
def test_core_trio_required_everywhere(env_name, missing):
    env = base_prod_env(APP_ENV=env_name)
    env.pop(missing)
    report = sc.validate_config(env, raise_on_error=False)
    assert not report.ok
    assert any(missing in e for e in report.errors)


# --------------------------------------------------------------------------- #
# Production: strict                                                             #
# --------------------------------------------------------------------------- #
def test_production_valid_config_ok():
    report = sc.validate_config(base_prod_env(), raise_on_error=False)
    assert report.ok, report.errors


def test_production_missing_frontend_url_is_error():
    env = base_prod_env()
    env.pop("FRONTEND_URL")
    report = sc.validate_config(env, raise_on_error=False)
    assert not report.ok
    assert any("FRONTEND_URL" in e for e in report.errors)


def test_production_weak_jwt_secret_rejected():
    report = sc.validate_config(
        base_prod_env(JWT_SECRET="supersecretjwtkey"), raise_on_error=False)
    assert not report.ok
    assert any("JWT_SECRET" in e for e in report.errors)


def test_production_requires_an_ai_provider():
    env = base_prod_env()
    env.pop("ANTHROPIC_API_KEY")
    report = sc.validate_config(env, raise_on_error=False)
    assert not report.ok
    assert any("AI provider" in e for e in report.errors)


def test_production_gemini_alone_satisfies_ai_requirement():
    env = base_prod_env()
    env.pop("ANTHROPIC_API_KEY")
    env["GOOGLE_GEMINI_KEY"] = "gm-live-xyz"
    report = sc.validate_config(env, raise_on_error=False)
    assert report.ok, report.errors


def test_production_oauth_half_configured_is_error():
    report = sc.validate_config(
        base_prod_env(GOOGLE_CLIENT_ID="123.apps.googleusercontent.com"),
        raise_on_error=False)
    assert not report.ok
    assert any("OAuth" in e for e in report.errors)


def test_production_broker_pair_half_configured_is_error():
    report = sc.validate_config(
        base_prod_env(KITE_API_KEY="realkey0000"), raise_on_error=False)
    assert not report.ok
    assert any("Kite" in e for e in report.errors)


def test_production_auto_login_must_be_off():
    report = sc.validate_config(
        base_prod_env(ENABLE_AUTO_LOGIN="true"), raise_on_error=False)
    assert not report.ok
    assert any("ENABLE_AUTO_LOGIN" in e for e in report.errors)


def test_production_weak_admin_password_rejected():
    report = sc.validate_config(
        base_prod_env(ADMIN_PASSWORD="admin123"), raise_on_error=False)
    assert not report.ok
    assert any("ADMIN_PASSWORD" in e for e in report.errors)


def test_production_missing_optional_secret_keys_are_warnings():
    # CSRF_SECRET / RECOVERY_SECRET / BROKER_TOKEN_KEY unset → recommended-only.
    report = sc.validate_config(base_prod_env(), raise_on_error=False)
    assert report.ok
    joined = " ".join(report.warnings)
    assert "CSRF_SECRET" in joined and "RECOVERY_SECRET" in joined


# --------------------------------------------------------------------------- #
# Fail-closed behaviour                                                          #
# --------------------------------------------------------------------------- #
def test_raise_on_error_aggregates_all_problems():
    env = {"APP_ENV": "production"}  # nearly everything missing
    with pytest.raises(sc.SecretValidationError) as exc:
        sc.validate_config(env)  # raise_on_error defaults True
    msg = str(exc.value)
    # Aggregated: multiple distinct problems in one message.
    assert "MONGO_URL" in msg and "JWT_SECRET" in msg and "AI provider" in msg


def test_valid_config_does_not_raise():
    # Should return normally (no exception) on a valid prod env.
    report = sc.validate_config(base_prod_env())
    assert report.ok


# --------------------------------------------------------------------------- #
# No secret ever leaks into output                                              #
# --------------------------------------------------------------------------- #
def test_error_message_never_contains_secret_values():
    secret_val = "sk-ant-SUPER-SECRET-DO-NOT-LOG-000000"
    env = base_prod_env(JWT_SECRET="short", ANTHROPIC_API_KEY=secret_val)
    report = sc.validate_config(env, raise_on_error=False)
    blob = "\n".join(report.errors + report.warnings + report.present)
    assert secret_val not in blob
    # And the raised, formatted message is equally clean.
    with pytest.raises(sc.SecretValidationError) as exc:
        sc.validate_config(env)
    assert secret_val not in str(exc.value)


def test_redact_hides_value():
    assert sc.redact("anything") == "********"
    assert sc.redact("") == "<unset>"
    assert sc.redact(None) == "<unset>"


def test_report_present_lists_names_not_values():
    report = sc.validate_config(base_prod_env(), raise_on_error=False)
    assert "JWT_SECRET" in report.present
    assert STRONG_JWT not in report.present  # names only


# --------------------------------------------------------------------------- #
# Registry integrity                                                            #
# --------------------------------------------------------------------------- #
def test_registry_has_no_duplicate_names():
    names = [s.name for s in sc.SECRET_REGISTRY]
    assert len(names) == len(set(names))


def test_core_required_are_registered_and_required_everywhere():
    for name in sc.CORE_REQUIRED:
        spec = sc.get_spec(name)
        assert spec is not None, f"{name} missing from registry"


def test_signing_secrets_declare_min_length():
    for name in ("JWT_SECRET", "CSRF_SECRET", "RECOVERY_SECRET"):
        spec = sc.get_spec(name)
        assert spec is not None and spec.min_length == sc.MIN_SIGNING_SECRET_LENGTH


def test_example_file_is_in_sync_with_registry():
    """The committed backend/.env.example must match what the generator would
    produce — guards against a registry change landing without a template
    refresh."""
    import importlib.util
    backend = Path(__file__).resolve().parent.parent
    gen_path = backend / "scripts" / "generate_env_example.py"
    spec = importlib.util.spec_from_file_location("gen_env", gen_path)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    expected = gen.render()
    example = backend / ".env.example"
    assert example.exists(), "backend/.env.example is missing"
    assert example.read_text() == expected, (
        "backend/.env.example is stale — run scripts/generate_env_example.py")


# --------------------------------------------------------------------------- #
# Accessors                                                                     #
# --------------------------------------------------------------------------- #
def test_get_strips_and_defaults(monkeypatch):
    monkeypatch.setenv("X_TEST_VAR", "  value  ")
    assert sc.get("X_TEST_VAR") == "value"
    monkeypatch.delenv("X_TEST_VAR", raising=False)
    assert sc.get("X_TEST_VAR", "fallback") == "fallback"


def test_require_raises_when_missing(monkeypatch):
    monkeypatch.delenv("X_MISSING_VAR", raising=False)
    with pytest.raises(sc.MissingSecret):
        sc.require("X_MISSING_VAR")


def test_is_configured_rejects_placeholder():
    assert sc.is_configured("K", {"K": "real-value-123"})
    assert not sc.is_configured("K", {"K": "your_key_here"})
    assert not sc.is_configured("K", {"K": ""})


def test_looks_like_placeholder():
    assert sc.looks_like_placeholder("your_anthropic_api_key_here")
    assert sc.looks_like_placeholder("changeme")
    assert sc.looks_like_placeholder("admin123")
    assert not sc.looks_like_placeholder("sk-ant-real-9f83j2")

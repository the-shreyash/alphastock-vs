"""Tests for the secret SOURCE layer — Docker secrets, the ``_FILE`` convention,
and environment materialization (PH2.3).

``test_secrets.py`` covers *what* the configuration surface must contain. This
file covers *where each value comes from* and *what happens when a source is
broken* — the two questions PH2.3 added.

Almost everything here is hermetic by construction: file reads go through an
injected ``reader``, so precedence, conflicts, empty files and unreadable paths
are exercised with a dict, no temp directories and no privileges. The handful of
cases that are specifically ABOUT the real filesystem (a mounted directory, an
oversized file, non-UTF-8 bytes, a genuine Docker-style mount layout) use
``tmp_path`` and the real reader, because a fake reader could not prove those.

Coverage map (mirrors the PH2.3 sprint's testing checklist):
* environment-variable loading                    — test_env_var_is_the_fallback_source
* Docker Secret loading                           — test_discovers_docker_secret_*
* ``_FILE`` convention                            — test_file_ref_*
* precedence                                      — test_precedence_*
* missing secret handling                         — test_missing_file_ref_is_an_error*
* invalid secret rejection                        — test_weak_*, test_invalid_fernet_*
* placeholder rejection                           — test_placeholder_*
* production boot failure                         — test_production_boot_fails_*
* development fallback                            — test_development_*
* rotation                                        — test_reload_*
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from security import secrets as sc  # noqa: E402


STRONG_JWT = "Zt7Qv3La9Rb2Nc8Kd1Pe6Mf4Sg0Wh5Yj-strong-key"
STRONG_ALT = "Pq2Vx8Kd4Rn6Tm1Zw9Lb3Hy7Jc5Gf0Su-rotated-key"
CREDENTIALED_MONGO = "mongodb://app_user:t9Wq2Lm5Rv8Bn3Xz@db:27017/alpha_stock_db?authSource=alpha_stock_db"


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
def fake_fs(**files):
    """Build a reader over an in-memory {path: contents} map.

    Raises :class:`SecretSourceError` for anything absent — the same exception
    the real reader raises for a missing path, so resolution logic cannot tell
    the two apart.
    """
    def reader(path: str) -> str:
        if path not in files:
            raise sc.SecretSourceError(f"secret file not found: {path}")
        return files[path]
    return reader


def base_prod_env(**overrides):
    env = {
        "APP_ENV": "production",
        "MONGO_URL": CREDENTIALED_MONGO,
        "DB_NAME": "alpha_stock_db",
        "JWT_SECRET": STRONG_JWT,
        "FRONTEND_URL": "https://app.stockassist.ai",
        "CORS_ALLOWED_ORIGINS": "https://app.stockassist.ai",
        "ANTHROPIC_API_KEY": "sk-ant-live-abc123",
    }
    env.update(overrides)
    return env


def resolve(environ, reader=None, secrets_dir=None):
    return sc.resolve_all(environ=environ, reader=reader, secrets_dir=secrets_dir)


@pytest.fixture
def clean_process_env(monkeypatch):
    """Isolate tests that exercise os.environ materialization.

    ``monkeypatch.setattr(os, "environ", ...)`` replaces the whole mapping, so
    nothing this test writes can leak into the process or into another test, and
    the loader's own module-level bookkeeping is reset around each use.
    """
    fake_environ = {}
    monkeypatch.setattr(os, "environ", fake_environ)
    sc._reset_loader_state()
    yield fake_environ
    sc._reset_loader_state()


# --------------------------------------------------------------------------- #
# Source 3 — plaintext environment (the development fallback)                   #
# --------------------------------------------------------------------------- #
def test_env_var_is_the_fallback_source():
    res = resolve({"JWT_SECRET": STRONG_JWT})
    entry = res.resolved["JWT_SECRET"]
    assert entry.value == STRONG_JWT
    assert entry.source == sc.SOURCE_ENV
    assert not entry.from_file
    assert res.ok


def test_absent_variable_resolves_to_absent_not_empty_string():
    entry = resolve({}).resolved["JWT_SECRET"]
    assert entry.source == sc.SOURCE_ABSENT
    assert entry.value is None
    assert not entry.present


def test_explicit_environ_does_not_touch_the_host_filesystem():
    """An explicit mapping is a CLOSED WORLD. Without this, a test suite running
    inside a container with a real /run/secrets would resolve differently from
    the same suite on a laptop — and would pass for the wrong reason."""
    res = resolve({"JWT_SECRET": STRONG_JWT}, secrets_dir="/run/secrets")
    assert res.resolved["JWT_SECRET"].source == sc.SOURCE_ENV
    assert res.ok


# --------------------------------------------------------------------------- #
# Source 1 — the `_FILE` convention                                             #
# --------------------------------------------------------------------------- #
def test_file_ref_reads_the_pointed_at_file():
    res = resolve({"JWT_SECRET_FILE": "/run/secrets/jwt"},
                  reader=fake_fs(**{"/run/secrets/jwt": STRONG_JWT}))
    entry = res.resolved["JWT_SECRET"]
    assert entry.value == STRONG_JWT
    assert entry.source == sc.SOURCE_FILE_REF
    assert entry.origin == "/run/secrets/jwt"
    assert entry.from_file


def test_file_ref_strips_the_trailing_newline():
    """`echo secret > file` appends \\n. A JWT signed with "key\\n" fails to
    verify against "key", and the symptom looks nothing like the cause."""
    res = resolve({"JWT_SECRET_FILE": "/s/jwt"},
                  reader=fake_fs(**{"/s/jwt": f"  {STRONG_JWT}\n\n"}))
    assert res.resolved["JWT_SECRET"].value == STRONG_JWT


def test_file_ref_works_for_an_unregistered_variable():
    """The convention is universal, not limited to the registry — an operator
    can add JWT_ISSUER_FILE without a code change."""
    res = resolve({"JWT_ISSUER_FILE": "/s/iss"}, reader=fake_fs(**{"/s/iss": "acme"}))
    assert res.resolved["JWT_ISSUER"].value == "acme"
    assert res.resolved["JWT_ISSUER"].source == sc.SOURCE_FILE_REF


def test_missing_file_ref_is_an_error():
    res = resolve({"JWT_SECRET_FILE": "/run/secrets/absent"}, reader=fake_fs())
    assert not res.ok
    assert any("JWT_SECRET_FILE" in e and "not found" in e for e in res.errors)
    assert res.resolved["JWT_SECRET"].source == sc.SOURCE_ABSENT


def test_missing_file_ref_never_silently_falls_back_to_the_env_var():
    """The single most important fail-closed property in this module. A silent
    downgrade is exactly how a rotation "succeeds" while the app keeps signing
    with the old key."""
    res = resolve({"JWT_SECRET_FILE": "/run/secrets/absent", "JWT_SECRET": STRONG_JWT},
                  reader=fake_fs())
    assert not res.ok
    assert res.resolved["JWT_SECRET"].source == sc.SOURCE_ABSENT
    assert res.resolved["JWT_SECRET"].value is None


def test_empty_file_ref_target_is_an_error():
    """A mounted-but-empty secret is a misconfiguration, not an unset value."""
    res = resolve({"JWT_SECRET_FILE": "/s/jwt"}, reader=fake_fs(**{"/s/jwt": "\n"}))
    assert not res.ok
    assert any("empty" in e for e in res.errors)
    assert res.resolved["JWT_SECRET"].source == sc.SOURCE_ABSENT


# --------------------------------------------------------------------------- #
# Source 2 — Docker secret auto-discovery                                       #
# --------------------------------------------------------------------------- #
def test_discovers_docker_secret_by_lowercase_name():
    """Compose/Swarm secret names are conventionally lowercase."""
    res = resolve({}, reader=fake_fs(**{"/run/secrets/jwt_secret": STRONG_JWT}),
                  secrets_dir="/run/secrets")
    entry = res.resolved["JWT_SECRET"]
    assert entry.value == STRONG_JWT
    assert entry.source == sc.SOURCE_SECRETS_DIR
    assert entry.origin == "/run/secrets/jwt_secret"


def test_discovers_docker_secret_by_exact_name():
    """A Kubernetes secret key is usually written exactly as the variable."""
    res = resolve({}, reader=fake_fs(**{"/run/secrets/JWT_SECRET": STRONG_JWT}),
                  secrets_dir="/run/secrets")
    assert res.resolved["JWT_SECRET"].source == sc.SOURCE_SECRETS_DIR
    assert res.resolved["JWT_SECRET"].origin == "/run/secrets/JWT_SECRET"


def test_secrets_dir_is_configurable_via_the_environment():
    res = resolve({"SECRETS_DIR": "/vault/secrets"},
                  reader=fake_fs(**{"/vault/secrets/jwt_secret": STRONG_JWT}))
    assert res.resolved["JWT_SECRET"].origin == "/vault/secrets/jwt_secret"


def test_discovery_is_limited_to_registered_names():
    """A stray file in the secrets directory must never be able to invent an
    environment variable — that would turn write access to a mount into
    arbitrary configuration injection."""
    res = resolve({}, reader=fake_fs(**{"/run/secrets/totally_made_up": "x"}),
                  secrets_dir="/run/secrets")
    assert "TOTALLY_MADE_UP" not in res.resolved


def test_empty_discovered_file_is_ignored_with_a_warning():
    """Unlike an explicit pointer, discovery is passive: an empty file there is
    noise to skip, not an instruction that failed."""
    res = resolve({"JWT_SECRET": STRONG_JWT},
                  reader=fake_fs(**{"/run/secrets/jwt_secret": "   \n"}),
                  secrets_dir="/run/secrets")
    assert res.ok
    assert any("empty" in w for w in res.warnings)
    assert res.resolved["JWT_SECRET"].source == sc.SOURCE_ENV


# --------------------------------------------------------------------------- #
# Precedence & conflicts                                                        #
# --------------------------------------------------------------------------- #
def test_precedence_file_ref_beats_discovery_and_env():
    res = resolve({"JWT_SECRET_FILE": "/explicit/jwt"},
                  reader=fake_fs(**{"/explicit/jwt": "from-file-ref",
                                    "/run/secrets/jwt_secret": "from-discovery"}),
                  secrets_dir="/run/secrets")
    assert res.resolved["JWT_SECRET"].value == "from-file-ref"
    assert res.resolved["JWT_SECRET"].source == sc.SOURCE_FILE_REF
    # An explicit pointer SUPPRESSES discovery rather than competing with it.
    assert res.ok, res.errors


def test_file_ref_pointing_into_the_secrets_dir_is_not_a_self_conflict():
    """The documented Docker-secrets layout: `JWT_SECRET_FILE=/run/secrets/
    jwt_secret` names exactly the path discovery scans. Treating the pointer and
    the discovery as two rival sources would make docker-compose.secrets.yml fail
    on every boot — and comparing paths instead would still be wrong on a
    case-insensitive filesystem, where JWT_SECRET and jwt_secret are one file."""
    res = resolve({"JWT_SECRET_FILE": "/run/secrets/jwt_secret"},
                  reader=fake_fs(**{"/run/secrets/jwt_secret": STRONG_JWT}),
                  secrets_dir="/run/secrets")
    assert res.ok, res.errors
    assert res.resolved["JWT_SECRET"].value == STRONG_JWT


def test_file_ref_and_plaintext_env_still_conflict():
    """The suppression above must not blunt the rule where ambiguity is real: a
    file source and an environment variable genuinely mean two owners."""
    res = resolve({"JWT_SECRET_FILE": "/s/jwt", "JWT_SECRET": "from-env"},
                  reader=fake_fs(**{"/s/jwt": STRONG_JWT}))
    assert not res.ok
    assert any("more than one source" in e for e in res.errors)


def test_precedence_discovery_beats_plaintext_env():
    res = resolve({"JWT_SECRET": "from-env"},
                  reader=fake_fs(**{"/run/secrets/jwt_secret": "from-discovery"}),
                  secrets_dir="/run/secrets")
    assert res.resolved["JWT_SECRET"].value == "from-discovery"
    assert res.resolved["JWT_SECRET"].source == sc.SOURCE_SECRETS_DIR


def test_two_sources_for_one_secret_is_an_error_not_a_merge():
    """Two sources means two owners. Silently picking one lets a rotated secret
    be shadowed by a stale one — a failure that surfaces days later."""
    res = resolve({"JWT_SECRET": "from-env"},
                  reader=fake_fs(**{"/run/secrets/jwt_secret": "from-discovery"}),
                  secrets_dir="/run/secrets")
    assert not res.ok
    assert any("more than one source" in e for e in res.errors)


def test_conflict_error_names_the_sources_but_never_the_values():
    res = resolve({"JWT_SECRET": "PLAINTEXT-VALUE-DO-NOT-LEAK"},
                  reader=fake_fs(**{"/run/secrets/jwt_secret": "FILE-VALUE-DO-NOT-LEAK"}),
                  secrets_dir="/run/secrets")
    blob = " ".join(res.errors)
    assert "PLAINTEXT-VALUE-DO-NOT-LEAK" not in blob
    assert "FILE-VALUE-DO-NOT-LEAK" not in blob
    assert "/run/secrets/jwt_secret" in blob  # the path IS safe and is what you fix


# --------------------------------------------------------------------------- #
# Real filesystem — the cases a fake reader cannot prove                         #
# --------------------------------------------------------------------------- #
def test_real_docker_style_mount_resolves(tmp_path):
    """End-to-end against a real directory laid out the way Docker mounts one."""
    secrets_dir = tmp_path / "run-secrets"
    secrets_dir.mkdir()
    (secrets_dir / "jwt_secret").write_text(STRONG_JWT + "\n")
    res = sc.resolve_all(environ={}, secrets_dir=str(secrets_dir),
                         reader=sc._default_reader)
    assert res.resolved["JWT_SECRET"].value == STRONG_JWT


def test_real_mounted_directory_is_rejected(tmp_path):
    """The classic Docker volume typo: mounting a directory where a file was
    meant. Produces a baffling IsADirectoryError deep in startup otherwise."""
    target = tmp_path / "jwt"
    target.mkdir()
    res = sc.resolve_all(environ={"JWT_SECRET_FILE": str(target)},
                         reader=sc._default_reader)
    assert not res.ok
    assert any("not a regular file" in e for e in res.errors)


def test_real_oversized_file_is_rejected(tmp_path):
    target = tmp_path / "huge"
    target.write_text("A" * (sc.MAX_SECRET_FILE_BYTES + 1))
    res = sc.resolve_all(environ={"JWT_SECRET_FILE": str(target)},
                         reader=sc._default_reader)
    assert not res.ok
    assert any("over the" in e for e in res.errors)


def test_real_binary_file_is_rejected_with_actionable_advice(tmp_path):
    target = tmp_path / "raw.key"
    target.write_bytes(b"\xff\xfe\x00\x01binary-key")
    res = sc.resolve_all(environ={"JWT_SECRET_FILE": str(target)},
                         reader=sc._default_reader)
    assert not res.ok
    assert any("base64" in e for e in res.errors)


# --------------------------------------------------------------------------- #
# Materialization into os.environ                                               #
# --------------------------------------------------------------------------- #
def test_load_secrets_materializes_file_values_into_the_environment(clean_process_env):
    """The whole point of the loader: ~30 modules read os.environ at call time,
    and this is what lets every one of them see a file-backed secret."""
    clean_process_env["JWT_SECRET_FILE"] = "/s/jwt"
    sc.load_secrets(reader=fake_fs(**{"/s/jwt": STRONG_JWT}))
    assert os.environ["JWT_SECRET"] == STRONG_JWT


def test_load_secrets_leaves_plain_env_values_untouched(clean_process_env):
    clean_process_env["JWT_SECRET"] = STRONG_JWT
    res = sc.load_secrets(reader=fake_fs())
    assert os.environ["JWT_SECRET"] == STRONG_JWT
    assert res.resolved["JWT_SECRET"].source == sc.SOURCE_ENV


def test_load_secrets_is_idempotent(clean_process_env):
    """Re-running must not see its own materialized value as a competing source.
    Without the loader's self-write bookkeeping this raises a false conflict —
    and rotation becomes impossible."""
    clean_process_env["JWT_SECRET_FILE"] = "/s/jwt"
    reader = fake_fs(**{"/s/jwt": STRONG_JWT})
    first = sc.load_secrets(reader=reader)
    second = sc.load_secrets(reader=reader)
    assert first.ok and second.ok, second.errors
    assert os.environ["JWT_SECRET"] == STRONG_JWT


def test_resolution_summary_line_is_value_free(clean_process_env):
    clean_process_env["JWT_SECRET_FILE"] = "/s/jwt"
    res = sc.load_secrets(reader=fake_fs(**{"/s/jwt": STRONG_JWT}))
    assert STRONG_JWT not in res.summary_line()
    assert "file=1" in res.summary_line()


def test_resolved_secret_repr_does_not_expose_the_value():
    entry = sc.ResolvedSecret("JWT_SECRET", STRONG_JWT, sc.SOURCE_FILE_REF, "/s/jwt")
    assert STRONG_JWT not in repr(entry)


# --------------------------------------------------------------------------- #
# Rotation                                                                      #
# --------------------------------------------------------------------------- #
def test_reload_detects_a_rotated_secret_by_fingerprint(clean_process_env):
    """A rotated Docker config / K8s projected volume updates the file in place;
    re-reading picks the new value up without a restart."""
    clean_process_env["JWT_SECRET_FILE"] = "/s/jwt"
    store = {"/s/jwt": STRONG_JWT}
    sc.load_secrets(reader=fake_fs(**store))

    store["/s/jwt"] = STRONG_ALT
    changed = sc.reload_secrets(reader=fake_fs(**store))

    assert "JWT_SECRET" in changed
    assert os.environ["JWT_SECRET"] == STRONG_ALT
    # The change report is fingerprints, never values.
    assert STRONG_JWT not in changed["JWT_SECRET"]
    assert STRONG_ALT not in changed["JWT_SECRET"]


def test_reload_reports_nothing_when_no_secret_changed(clean_process_env):
    clean_process_env["JWT_SECRET_FILE"] = "/s/jwt"
    reader = fake_fs(**{"/s/jwt": STRONG_JWT})
    sc.load_secrets(reader=reader)
    assert sc.reload_secrets(reader=reader) == {}


def test_reload_drops_a_revoked_secret_from_the_environment(clean_process_env):
    """A deleted secret must stop working. Leaving our own stale write behind
    would mean a revocation silently did nothing."""
    clean_process_env["JWT_SECRET_FILE"] = "/s/jwt"
    sc.load_secrets(reader=fake_fs(**{"/s/jwt": STRONG_JWT}))
    assert os.environ["JWT_SECRET"] == STRONG_JWT

    # The pointer now dangles; resolution errors AND the stale value is dropped.
    res = sc.load_secrets(reader=fake_fs())
    assert not res.ok
    assert "JWT_SECRET" not in os.environ


def test_fingerprint_is_stable_distinct_and_value_free():
    assert sc.fingerprint(STRONG_JWT) == sc.fingerprint(STRONG_JWT)
    assert sc.fingerprint(STRONG_JWT) != sc.fingerprint(STRONG_ALT)
    assert STRONG_JWT not in sc.fingerprint(STRONG_JWT)
    assert sc.fingerprint(None) == "<unset>"
    assert sc.fingerprint("") == "<unset>"


# --------------------------------------------------------------------------- #
# validate_config over resolved sources                                         #
# --------------------------------------------------------------------------- #
def test_validate_config_accepts_a_fully_file_backed_production_env():
    env = {"APP_ENV": "production", "DB_NAME": "alpha_stock_db",
           "FRONTEND_URL": "https://app.stockassist.ai",
           "CORS_ALLOWED_ORIGINS": "https://app.stockassist.ai",
           "MONGO_URL_FILE": "/s/mongo", "JWT_SECRET_FILE": "/s/jwt",
           "ANTHROPIC_API_KEY_FILE": "/s/anthropic"}
    reader = fake_fs(**{"/s/mongo": CREDENTIALED_MONGO, "/s/jwt": STRONG_JWT,
                        "/s/anthropic": "sk-ant-live-abc123"})
    report = sc.validate_config(env, raise_on_error=False, reader=reader)
    assert report.ok, report.errors
    assert set(report.from_file()) == {"MONGO_URL", "JWT_SECRET", "ANTHROPIC_API_KEY"}
    # Non-sensitive configuration (APP_ENV, DB_NAME, FRONTEND_URL, …) stays in the
    # environment by design — it is deployment wiring, not a credential. What must
    # be empty is the set of SENSITIVE values still arriving in plaintext.
    sensitive_in_env = [n for n in report.from_env()
                        if (spec := sc.get_spec(n)) and spec.sensitive]
    assert sensitive_in_env == []


def test_validate_config_reports_which_secrets_are_still_plaintext():
    report = sc.validate_config(base_prod_env(), raise_on_error=False)
    assert "JWT_SECRET" in report.from_env()
    assert report.from_file() == []
    assert any("plaintext environment variable" in w for w in report.warnings)


def test_plaintext_in_production_is_a_warning_not_a_boot_failure():
    """The migration must be adoptable. Breaking every existing env-file
    deployment on upgrade would guarantee the feature is reverted, not adopted."""
    report = sc.validate_config(base_prod_env(), raise_on_error=False)
    assert report.ok, report.errors


def test_require_file_secrets_turns_plaintext_into_a_boot_failure():
    """Opt-in strict posture: once a deployment has finished migrating, this
    stops a regression from creeping back in unnoticed."""
    with pytest.raises(sc.SecretValidationError) as exc:
        sc.validate_config(base_prod_env(REQUIRE_FILE_SECRETS="true"))
    assert "JWT_SECRET" in str(exc.value)
    assert STRONG_JWT not in str(exc.value)


def test_require_file_secrets_is_satisfied_by_file_sources():
    env = {"APP_ENV": "production", "DB_NAME": "alpha_stock_db",
           "REQUIRE_FILE_SECRETS": "true",
           "FRONTEND_URL": "https://app.stockassist.ai",
           "CORS_ALLOWED_ORIGINS": "https://app.stockassist.ai",
           "MONGO_URL_FILE": "/s/mongo", "JWT_SECRET_FILE": "/s/jwt",
           "ANTHROPIC_API_KEY_FILE": "/s/anthropic"}
    reader = fake_fs(**{"/s/mongo": CREDENTIALED_MONGO, "/s/jwt": STRONG_JWT,
                        "/s/anthropic": "sk-ant-live-abc123"})
    report = sc.validate_config(env, raise_on_error=False, reader=reader)
    assert report.ok, report.errors


def test_development_is_not_nagged_about_plaintext_secrets():
    """Development is where plaintext is the correct trade-off. A warning that
    fires on every laptop boot is a warning nobody reads in production either."""
    report = sc.validate_config(
        {"MONGO_URL": "mongodb://localhost:27017", "DB_NAME": "alpha_stock_db",
         "JWT_SECRET": STRONG_JWT}, raise_on_error=False)
    assert report.ok, report.errors
    assert not any("plaintext" in w for w in report.warnings)


def test_production_boot_fails_on_an_unreadable_secret_file():
    """A broken source is a boot failure, aggregated with everything else — an
    operator should never fix one problem, restart, and discover the next."""
    env = base_prod_env(JWT_SECRET_FILE="/s/absent")
    env.pop("JWT_SECRET")
    with pytest.raises(sc.SecretValidationError) as exc:
        sc.validate_config(env, reader=fake_fs())
    message = str(exc.value)
    assert "JWT_SECRET_FILE" in message
    assert "JWT_SECRET is required in production" in message


def test_source_errors_never_contain_the_secret_value():
    env = base_prod_env(JWT_SECRET_FILE="/s/jwt")
    report = sc.validate_config(env, raise_on_error=False,
                                reader=fake_fs(**{"/s/jwt": "FILE-SIDE-SECRET"}))
    blob = " ".join(report.errors + report.warnings)
    assert "FILE-SIDE-SECRET" not in blob
    assert STRONG_JWT not in blob


# --------------------------------------------------------------------------- #
# Invalid / weak secret rejection (PH2.3 value-shape rules)                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [
    "short",                                # under 8 characters
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",  # one distinct character
    "abababababababababababababababababab",  # two distinct characters
    "0123456789012345678901234567890123456",  # a digit run
])
def test_weak_secret_values_are_detected(value):
    assert sc.looks_weak(value), value


@pytest.mark.parametrize("value", [
    STRONG_JWT,
    "sk-ant-api03-9fJk2LmQ8xR4vT7bN1zC",
    "mongodb://db:27017",
])
def test_strong_or_structured_values_are_not_flagged_weak(value):
    assert not sc.looks_weak(value), value


def test_weak_signing_secret_fails_production_boot():
    long_but_worthless = "a" * 48  # clears min_length, ~0 bits of entropy
    with pytest.raises(sc.SecretValidationError) as exc:
        sc.validate_config(base_prod_env(JWT_SECRET=long_but_worthless))
    assert "low entropy" in str(exc.value)
    assert long_but_worthless not in str(exc.value)


def test_weak_secret_is_only_a_warning_in_development():
    report = sc.validate_config(
        {"MONGO_URL": "mongodb://localhost:27017", "DB_NAME": "alpha_stock_db",
         "JWT_SECRET": "a" * 48}, raise_on_error=False)
    assert report.ok
    assert any("low entropy" in w for w in report.warnings)


def test_placeholder_secret_still_fails_production_boot():
    """Regression guard: PH2.3 rewrote validate_config's plumbing; the PH1.9
    placeholder rule must survive it."""
    with pytest.raises(sc.SecretValidationError) as exc:
        sc.validate_config(base_prod_env(JWT_SECRET="your_jwt_secret_here_replace_me"))
    assert "JWT_SECRET" in str(exc.value)


def test_placeholder_arriving_from_a_file_is_rejected_too():
    """Changing the delivery mechanism must not weaken value validation — a
    placeholder mounted as a Docker secret is still a placeholder."""
    env = base_prod_env(JWT_SECRET_FILE="/s/jwt")
    env.pop("JWT_SECRET")
    report = sc.validate_config(env, raise_on_error=False,
                                reader=fake_fs(**{"/s/jwt": "changeme-changeme-changeme-xyz"}))
    assert not report.ok
    assert any("placeholder" in e for e in report.errors)


# --------------------------------------------------------------------------- #
# Datastore / encryption-key validation                                         #
# --------------------------------------------------------------------------- #
def test_production_mongo_url_without_credentials_is_rejected():
    with pytest.raises(sc.SecretValidationError) as exc:
        sc.validate_config(base_prod_env(MONGO_URL="mongodb://db:27017"))
    assert "MONGO_URL carries no username:password" in str(exc.value)


def test_development_mongo_url_without_credentials_is_only_a_warning():
    report = sc.validate_config(
        {"MONGO_URL": "mongodb://localhost:27017", "DB_NAME": "alpha_stock_db",
         "JWT_SECRET": STRONG_JWT}, raise_on_error=False)
    assert report.ok
    assert any("username:password" in w for w in report.warnings)


def test_production_loopback_mongo_url_warns():
    report = sc.validate_config(
        base_prod_env(MONGO_URL="mongodb://u:p9Kd2Lm5Rv8Bn3Xz@localhost:27017/db"),
        raise_on_error=False)
    assert any("loopback" in w for w in report.warnings)


def test_production_redis_without_a_password_is_rejected():
    with pytest.raises(sc.SecretValidationError) as exc:
        sc.validate_config(base_prod_env(REDIS_URL="redis://redis:6379/0"))
    assert "REDIS_URL has no password" in str(exc.value)


def test_production_redis_with_a_password_is_accepted():
    report = sc.validate_config(
        base_prod_env(REDIS_URL="redis://:c7Vm2Kd9Rx4Tn6Lb@redis:6379/0"),
        raise_on_error=False)
    assert report.ok, report.errors


@pytest.mark.parametrize("value,valid", [
    ("c3RvY2thc3Npc3Qtc2VjcmV0LWtleS0zMi1ieXRlcyE=", True),   # 44 chars → 32 bytes
    ("too-short", False),
    ("!" * 44, False),                                          # right length, not base64
])
def test_fernet_key_shape_validation(value, valid):
    assert sc.is_valid_fernet_key(value) is valid


def test_invalid_fernet_key_is_rejected_in_every_environment():
    """An invalid encryption key does not fail at boot — it fails the first time
    a user connects a broker account, long after deployment. So catch it now,
    and in development too."""
    report = sc.validate_config(
        {"MONGO_URL": CREDENTIALED_MONGO, "DB_NAME": "alpha_stock_db",
         "JWT_SECRET": STRONG_JWT, "BROKER_TOKEN_KEY": "not-a-fernet-key-at-all"},
        raise_on_error=False)
    assert not report.ok
    assert any("BROKER_TOKEN_KEY" in e and "Fernet" in e for e in report.errors)


def test_valid_fernet_key_is_accepted():
    report = sc.validate_config(
        base_prod_env(BROKER_TOKEN_KEY="c3RvY2thc3Npc3Qtc2VjcmV0LWtleS0zMi1ieXRlcyE="),
        raise_on_error=False)
    assert report.ok, report.errors


def test_swapped_provider_keys_are_flagged_as_warnings_not_errors():
    """A provider may change its key format tomorrow; blocking a production boot
    over a prefix would be the wrong trade. But a mis-pasted key otherwise
    surfaces as a third-party 401 hours later."""
    report = sc.validate_config(
        base_prod_env(ANTHROPIC_API_KEY="AIzaSyDx-google-style-key-pasted-here",
                      GOOGLE_CLIENT_ID="123456789", GOOGLE_CLIENT_SECRET="GOCSPX-abc123xyz"),
        raise_on_error=False)
    assert report.ok, report.errors
    assert any("sk-ant-" in w for w in report.warnings)
    assert any("apps.googleusercontent.com" in w for w in report.warnings)


# --------------------------------------------------------------------------- #
# Registry integrity                                                            #
# --------------------------------------------------------------------------- #
def test_no_registered_name_collides_with_the_file_suffix():
    """A variable literally named FOO_FILE would be ambiguous: is it a value or
    a pointer to BAR's value? The registry must never contain one."""
    for spec in sc.SECRET_REGISTRY:
        assert not spec.name.endswith(sc.FILE_SUFFIX), spec.name


def test_secret_source_control_variables_are_registered():
    """They are part of the configuration surface, so they belong in the
    registry — that is what keeps .env.example and the docs from drifting."""
    assert sc.get_spec("SECRETS_DIR") is not None
    assert sc.get_spec("REQUIRE_FILE_SECRETS") is not None


def test_sendgrid_style_smtp_username_is_not_rejected_as_weak():
    """SMTP_USER is sensitive but provider-CHOSEN, not generated — SendGrid's is
    the literal string `apikey`, six characters. Applying the entropy rule to it
    would block a legitimate production boot, so the registry opts it out."""
    report = sc.validate_config(
        base_prod_env(SMTP_HOST="smtp.sendgrid.net", SMTP_USER="apikey",
                      SMTP_PASSWORD="SG.9fJk2LmQ8xR4vT7bN1zC-real-key"),
        raise_on_error=False)
    assert report.ok, report.errors
    # The only SMTP_USER remark allowed is the plaintext-delivery advisory, which
    # is correct and applies to every sensitive env-delivered value.
    assert not any("SMTP_USER" in w and "entropy" in w for w in report.warnings)


def test_a_generated_secret_is_still_entropy_checked():
    """The opt-out must be narrow: it applies to SMTP_USER, not to keys."""
    assert sc.get_spec("SMTP_USER").entropy_checked is False
    for name in ("JWT_SECRET", "ANTHROPIC_API_KEY", "BROKER_TOKEN_KEY", "WEBHOOK_API_KEY"):
        assert sc.get_spec(name).entropy_checked is True, name


def test_reload_watches_unregistered_file_backed_variables(clean_process_env):
    """An operator using JWT_ISSUER_FILE must not have it rotate invisibly."""
    clean_process_env["JWT_ISSUER_FILE"] = "/s/iss"
    store = {"/s/iss": "issuer-one"}
    sc.load_secrets(reader=fake_fs(**store))
    store["/s/iss"] = "issuer-two"
    assert "JWT_ISSUER" in sc.reload_secrets(reader=fake_fs(**store))

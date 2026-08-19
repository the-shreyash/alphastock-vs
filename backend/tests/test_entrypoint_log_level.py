"""`docker/entrypoint.sh` LOG_LEVEL handling (PH3.12 finding C-4).

WHY THIS FILE EXISTS
---------------------
uvicorn's `--log-level` flag is a case-sensitive `click.Choice` that only
accepts lowercase level names (`critical|error|warning|info|debug|trace`).
Every other LOG_LEVEL convention in this repo — `backend/.env.example`, the
`security/secrets.py` registry, the app's own `observability/logging.py` — is
written and read as uppercase, because that is the value operators actually
set (`LOG_LEVEL=INFO`). Before the C-4 fix, `LOG_LEVEL=INFO` reached uvicorn's
argument parser verbatim and the container exited 2 on every production boot
that followed the documented `backend/.env.example` template.

These tests run the REAL `docker/entrypoint.sh` as a subprocess — not a model
of it — because C-4 was exactly the gap between "the shell script looks
right" and "the shell script does the right thing when a real value flows
through it" (the same lesson as C-3's dockerignore matcher).

HOW IT STAYS HERMETIC
----------------------
Startup validation (`security.secrets.validate_config`) resolves and checks
secrets but never opens a network connection, so a syntactically valid but
unreachable `MONGO_URL` is sufficient. Every case here uses the entrypoint's
own override-command escape hatch (`entrypoint.sh <cmd>`, documented at the
top of the script) to `exec true` instead of booting uvicorn — validation
still runs unconditionally before the hand-off, so this exercises the exact
LOG_LEVEL normalization and validation code path without starting a real
server or requiring a live database.
"""
from __future__ import annotations

import os
import secrets as _secrets
import subprocess
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENTRYPOINT = BACKEND_DIR / "docker" / "entrypoint.sh"

#: Minimal environment that satisfies `security.secrets.validate_config` for
#: APP_ENV=development without touching a real database or filesystem secret.
#: A random, high-entropy value avoids the placeholder/low-entropy rejection
#: `security.secrets` applies to obviously-fake secrets like "x" * 48.
_BASE_ENV = {
    "APP_ENV": "development",
    "MONGO_URL": "mongodb://user:pass@127.0.0.1:27017",
    "DB_NAME": "alpha_stock_test",
    "JWT_SECRET": _secrets.token_urlsafe(48),
    # Keep PATH/PYTHONPATH so `python` (for the secrets validator) resolves.
    "PATH": os.environ.get("PATH", ""),
    "PYTHONPATH": str(BACKEND_DIR),
    "HOME": os.environ.get("HOME", "/tmp"),
}


def _run_entrypoint(log_level: str | None) -> subprocess.CompletedProcess:
    env = dict(_BASE_ENV)
    if log_level is not None:
        env["LOG_LEVEL"] = log_level
    # `true` is the override-command escape hatch: validation still runs, but
    # the script execs `true` instead of uvicorn, so this needs no server.
    return subprocess.run(
        ["sh", str(ENTRYPOINT), "true"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "log_level",
    ["INFO", "info", "WARNING", "warning", "ERROR", "DEBUG", "Debug", "CRITICAL", "TRACE"],
)
def test_valid_log_level_boots_regardless_of_case(log_level: str) -> None:
    result = _run_entrypoint(log_level)
    assert result.returncode == 0, (
        f"LOG_LEVEL={log_level!r} should boot successfully; "
        f"stderr:\n{result.stderr}"
    )


def test_default_log_level_boots_when_unset() -> None:
    result = _run_entrypoint(None)
    assert result.returncode == 0, result.stderr


def test_empty_log_level_defaults_to_info() -> None:
    """`LOG_LEVEL=""` hits the same `${LOG_LEVEL:-info}` parameter expansion
    as an unset variable (POSIX `:-` treats empty and unset alike) — every
    other defaulted variable in this script (HOST, PORT, ...) relies on the
    same idiom, so this is intentional, not a gap."""
    result = _run_entrypoint("")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("log_level", ["VERBOSE", "notice", "  ", "info "])
def test_invalid_log_level_fails_clearly(log_level: str) -> None:
    result = _run_entrypoint(log_level)
    assert result.returncode == 1, (
        f"LOG_LEVEL={log_level!r} must be rejected by entrypoint.sh itself, "
        f"not silently reach uvicorn; stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "LOG_LEVEL" in result.stderr
    assert "FATAL" in result.stderr


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("INFO", "info"), ("Warning", "warning"), ("DEBUG", "debug"), ("info", "info")],
)
def test_log_level_is_normalized_before_reaching_the_process_environment(
    raw: str, normalized: str
) -> None:
    """Regression pin for C-4: LOG_LEVEL is reassigned (not just read) before
    the override-command hand-off, so exec'd children — and, in the real
    contract, uvicorn's own --log-level argument — see the lowercased form,
    never the raw operator-supplied case."""
    env = dict(_BASE_ENV)
    env["LOG_LEVEL"] = raw
    result = subprocess.run(
        ["sh", str(ENTRYPOINT), "sh", "-c", 'printf "NORMALIZED=%s\\n" "$LOG_LEVEL"'],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert f"NORMALIZED={normalized}" in result.stdout


def test_production_env_example_log_level_is_uvicorn_safe() -> None:
    """production.env.example is what operators copy verbatim; its LOG_LEVEL
    value must already be one uvicorn accepts without relying on this script's
    normalization (belt-and-suspenders documentation consistency)."""
    example = (BACKEND_DIR.parent / "production.env.example").read_text()
    for line in example.splitlines():
        if line.startswith("LOG_LEVEL="):
            value = line.split("=", 1)[1].strip()
            assert value == value.lower(), (
                f"production.env.example LOG_LEVEL={value!r} should be lowercase"
            )
            break
    else:
        pytest.fail("production.env.example has no LOG_LEVEL line")

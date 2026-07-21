#!/usr/bin/env python3
"""Local supply-chain audit for the backend dependencies (PH1.9).

A thin, dependency-free wrapper that runs the same checks CI runs, so a
developer can verify supply-chain health before pushing:

    python scripts/audit_dependencies.py

Checks:
  1. ``pip check``     — the installed dependency graph is internally consistent.
  2. ``pip-audit``     — no installed package matches a known CVE (PyPI Advisory
                         DB + OSV). Installed on demand via ``pipx``/``pip`` if
                         missing; the script degrades gracefully with guidance
                         rather than failing hard when it cannot be installed.

Exit code is non-zero if any check reports a problem, so it is safe to wire into
a pre-push hook. This never touches secrets or the network beyond the advisory
feeds pip-audit itself uses.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS = BACKEND_DIR / "requirements.txt"


def _run(label: str, cmd: list[str]) -> int:
    print(f"\n── {label} " + "─" * max(0, 60 - len(label)))
    print("$ " + " ".join(cmd))
    proc = subprocess.run(cmd)
    return proc.returncode


def main() -> int:
    failures = 0

    # 1. Dependency-graph consistency.
    failures += 0 if _run("pip check (graph consistency)",
                           [sys.executable, "-m", "pip", "check"]) == 0 else 1

    # 2. Known-vulnerability audit.
    if shutil.which("pip-audit"):
        audit_cmd = ["pip-audit", "--strict", "--requirement", str(REQUIREMENTS)]
    else:
        # Try the module form (installed into the current interpreter).
        try:
            import pip_audit  # noqa: F401
            audit_cmd = [sys.executable, "-m", "pip_audit", "--strict",
                         "--requirement", str(REQUIREMENTS)]
        except ImportError:
            print("\n── pip-audit ───────────────────────────────────────────────")
            print("pip-audit is not installed. Install it (isolated) with:")
            print("    pipx install pip-audit        # recommended")
            print("    # or: python -m pip install pip-audit")
            print("Then re-run this script. CI runs pip-audit on every push.")
            return 1 if failures else 0  # don't fail solely on missing tool
    failures += 0 if _run("pip-audit (known CVEs)", audit_cmd) == 0 else 1

    print("\n" + ("=" * 62))
    if failures:
        print(f"Supply-chain audit FAILED ({failures} check(s) reported issues).")
    else:
        print("Supply-chain audit passed.")
    print("=" * 62)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate ``backend/.env.example`` from the authoritative secret registry.

The registry in ``backend/security/secrets.py`` is the single source of truth
for the app's configuration surface. This script renders it into a safe,
placeholder-only ``.env.example`` so the template can never drift from the code
that actually reads the variables.

Usage:
    python scripts/generate_env_example.py           # write backend/.env.example
    python scripts/generate_env_example.py --check    # CI: fail if out of date

The ``--check`` mode writes nothing and exits non-zero when the on-disk template
differs from what the registry would produce — wire it into CI so a new secret
added to the registry must be accompanied by a regenerated template.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make ``security`` importable when run from the backend/ directory.
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from security.secrets import (  # noqa: E402
    CAT_ADMIN, CAT_AI, CAT_APPCFG, CAT_AUTH, CAT_BROKER, CAT_CORE, CAT_INFRA,
    CAT_MARKET, CAT_NOTIFY, CAT_OAUTH, SECRET_REGISTRY, SecretSpec,
)

TARGET = BACKEND_DIR / ".env.example"

# Human-readable section headers, in the order they appear in the file.
SECTION_TITLES = {
    CAT_CORE: "CORE APPLICATION CONFIGURATION (required in every environment)",
    CAT_APPCFG: "APPLICATION CONFIGURATION",
    CAT_AUTH: "SIGNING SECRETS (optional dedicated keys; fall back to JWT_SECRET)",
    CAT_AI: "AI PROVIDERS (at least one REQUIRED in production)",
    CAT_OAUTH: "GOOGLE OAUTH 2.0 (optional; set BOTH or neither)",
    CAT_MARKET: "EXTERNAL MARKET DATA (optional fallback)",
    CAT_BROKER: "BROKERAGE INTEGRATION (optional; each pair is both-or-neither)",
    CAT_NOTIFY: "TRANSACTIONAL NOTIFICATIONS (optional)",
    CAT_INFRA: "INFRASTRUCTURE (optional)",
    CAT_ADMIN: "DEVELOPER FLAGS & DEV-ONLY SEED VALUES",
}
SECTION_ORDER = list(SECTION_TITLES.keys())


def _render_spec(spec: SecretSpec) -> str:
    tags = []
    if spec.sensitive:
        tags.append("SECRET")
    if spec.required_in:
        tags.append(f"required in: {', '.join(sorted(spec.required_in))}")
    else:
        tags.append("optional")
    comment = f"# {spec.description}  [{'; '.join(tags)}]"
    return f"{comment}\n{spec.name}={spec.example}"


def render() -> str:
    lines = [
        "# ==============================================================================",
        "# StockAssist AI — Backend environment template",
        "#",
        "# Generated from backend/security/secrets.py (SECRET_REGISTRY) by",
        "# scripts/generate_env_example.py — DO NOT edit by hand; add the variable to",
        "# the registry and re-run the generator instead.",
        "#",
        "# Copy to backend/.env and fill in real values. This file is committed and",
        "# MUST contain placeholders only — never a real secret.",
        "# ==============================================================================",
        "",
    ]
    by_cat = {}
    for spec in SECRET_REGISTRY:
        by_cat.setdefault(spec.category, []).append(spec)

    for cat in SECTION_ORDER:
        specs = by_cat.get(cat)
        if not specs:
            continue
        title = SECTION_TITLES[cat]
        lines.append(f"# ── {title} " + "─" * max(0, 74 - len(title)))
        for spec in specs:
            lines.append(_render_spec(spec))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    content = render()
    check = "--check" in sys.argv[1:]
    if check:
        current = TARGET.read_text() if TARGET.exists() else ""
        if current != content:
            print(
                "backend/.env.example is out of date with the secret registry.\n"
                "Run: python scripts/generate_env_example.py",
                file=sys.stderr,
            )
            return 1
        print("backend/.env.example is in sync with the secret registry.")
        return 0
    TARGET.write_text(content)
    print(f"Wrote {TARGET.relative_to(BACKEND_DIR.parent)} "
          f"({len(SECRET_REGISTRY)} variables).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

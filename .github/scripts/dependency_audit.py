#!/usr/bin/env python3
"""Enforce `.github/dependency-triage.yml` against both dependency ecosystems.

WHY THIS EXISTS (PH3.11 remediation)
------------------------------------
The `dependency-audit` workflow used to be two bare auditor invocations. That
produced a gate with three defects, and this script exists to close each one:

1. **The npm half could not be satisfied.** `npm audit --audit-level=high` has
   no suppression mechanism, so 18 advisories in the Create React App build
   chain — none of them reachable, none shipped to a browser — failed the build
   unconditionally. There was nothing an engineer could do except ignore the
   job.

2. **Python suppressions rotted invisibly.** Eight of the fifteen
   `--ignore-vuln` flags named `litellm` and `ecdsa`, packages that had already
   been removed from `requirements.txt`. They matched nothing. Nothing checked.
   This script FAILS on a register entry that matches no live finding, which is
   the check that would have caught it.

3. **The exit status could not be trusted.** The audit ran through a pipe in one
   place, so the shell reported the exit code of `tail` rather than of the
   auditor. Every subprocess here is invoked directly and its return code read
   from the process object — never through a pipeline.

WHAT IT DOES
------------
* Runs `pip-audit` with **no** ignore flags and `npm audit --json`, so the
  auditors report everything and this script — not the auditor's CLI — decides
  what is acceptable.
* Requires every finding to have a matching, unexpired register entry.
* Fails on any expired entry. There is deliberately no grace period: a date that
  slips silently is not a deadline.
* Fails on any register entry that matches nothing (stale-entry check).
* Warns, without failing, when an entry expires within `policy.warn_within_days`.

WHERE THIS LIVES, AND THE OTHER AUDIT SCRIPT
--------------------------------------------
It sits in `.github/scripts/` beside the register it enforces and the workflow
that runs it. `scripts/` is documented as host-side operator bash, and
`backend/scripts/` is Python that runs inside the image; this is neither.

`backend/scripts/audit_dependencies.py` is a **different tool and still useful**:
a developer convenience wrapper that runs `pip check` plus `pip-audit` against
the *installed* virtualenv. It is Python-only and register-unaware, so it will
report the triaged starlette advisories as findings. **This script is the
authority on whether the build passes**; that one answers "is my local
environment internally consistent and roughly current?".

USAGE
-----
    python .github/scripts/dependency_audit.py --ecosystem all
    python .github/scripts/dependency_audit.py --ecosystem python
    python .github/scripts/dependency_audit.py --ecosystem npm
    python .github/scripts/dependency_audit.py --ecosystem all --summary "$GITHUB_STEP_SUMMARY"

Exit codes: 0 clean · 1 policy violation · 2 tooling failure (auditor could not
run). 2 is distinct on purpose — "the check could not be performed" must never
be reported as "the check passed".
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # .github/scripts/ -> repo root
REGISTER = REPO_ROOT / ".github" / "dependency-triage.yml"
BACKEND = REPO_ROOT / "backend"
FRONTEND = REPO_ROOT / "frontend"

EXIT_OK, EXIT_POLICY, EXIT_TOOLING = 0, 1, 2

NPM_SEVERITY_ORDER = ["info", "low", "moderate", "high", "critical"]


# --------------------------------------------------------------------------- #
# Register                                                                     #
# --------------------------------------------------------------------------- #
class Entry:
    """One triaged advisory."""

    REQUIRED = ("id", "ecosystem", "package", "severity", "classification",
                "reason", "reachability", "mitigation", "owner", "expires")
    CLASSIFICATIONS = ("not-reachable", "temporarily-accepted")

    def __init__(self, raw: Dict[str, Any], index: int):
        missing = [f for f in self.REQUIRED if not raw.get(f)]
        if missing:
            raise ValueError(
                f"entry #{index} ({raw.get('id', '?')}) is missing required "
                f"field(s): {', '.join(missing)}")
        self.id = str(raw["id"])
        self.ecosystem = str(raw["ecosystem"])
        self.package = str(raw["package"])
        self.severity = str(raw["severity"])
        self.classification = str(raw["classification"])
        self.owner = str(raw["owner"])
        self.reason = str(raw["reason"])
        self.evidence = str(raw.get("evidence") or "")
        if self.ecosystem not in ("python", "npm"):
            raise ValueError(f"{self.id}: ecosystem must be python|npm")
        if self.classification not in self.CLASSIFICATIONS:
            raise ValueError(
                f"{self.id}: classification must be one of {self.CLASSIFICATIONS}")
        # `not-reachable` is the stronger claim, so it must carry re-runnable
        # evidence. `temporarily-accepted` only claims "we know and we are
        # living with it", which the mitigation field already covers.
        if self.classification == "not-reachable" and not self.evidence:
            raise ValueError(
                f"{self.id}: classification 'not-reachable' requires `evidence` "
                f"a reviewer can re-run")
        self.expires = self._parse_date(raw["expires"])
        self.matched = False

    @staticmethod
    def _parse_date(value: Any) -> date:
        if isinstance(value, date):
            return value
        return datetime.strptime(str(value), "%Y-%m-%d").date()

    @property
    def is_via(self) -> bool:
        """True when this entry covers a package flagged only through a dependency."""
        return self.id.startswith("via:")

    def key(self) -> Tuple[str, str]:
        return (self.ecosystem, self.id if not self.is_via else f"pkg:{self.package}")


def load_register(path: Path) -> Tuple[List[Entry], Dict[str, Any]]:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        print("ERROR: PyYAML is required to read the triage register "
              "(pip install pyyaml).", file=sys.stderr)
        raise SystemExit(EXIT_TOOLING)

    if not path.exists():
        print(f"ERROR: triage register not found at {path}", file=sys.stderr)
        raise SystemExit(EXIT_TOOLING)

    doc = yaml.safe_load(path.read_text()) or {}
    policy = doc.get("policy") or {}
    entries: List[Entry] = []
    seen: set = set()
    for i, raw in enumerate(doc.get("entries") or [], start=1):
        entry = Entry(raw, i)
        k = (entry.ecosystem, entry.id, entry.package)
        if k in seen:
            raise ValueError(f"duplicate register entry: {k}")
        seen.add(k)
        entries.append(entry)
    return entries, policy


# --------------------------------------------------------------------------- #
# Auditors                                                                     #
# --------------------------------------------------------------------------- #
def _run(cmd: List[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a command and return the completed process.

    Deliberately NOT piped through anything: the caller reads `returncode` from
    the process object, so no shell pipeline can mask a failure.
    """
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


def audit_python(requirement_files: Iterable[Path]) -> List[Dict[str, str]]:
    """Every pip-audit finding across the given requirement files.

    Run with no `--ignore-vuln`: the register decides what is acceptable, not
    the auditor's command line. `--strict` makes an unresolvable package an
    error rather than a silent pass.
    """
    findings: List[Dict[str, str]] = []
    for req in requirement_files:
        if not req.exists():
            continue
        proc = _run([sys.executable, "-m", "pip_audit", "--strict", "--progress-spinner", "off",
                     "--timeout", "120", "-f", "json", "-r", str(req)], cwd=REPO_ROOT)
        if not proc.stdout.strip():
            print(f"ERROR: pip-audit produced no output for {req.name}.\n"
                  f"{proc.stderr[-2000:]}", file=sys.stderr)
            raise SystemExit(EXIT_TOOLING)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            print(f"ERROR: pip-audit output for {req.name} was not JSON.\n"
                  f"{proc.stdout[:500]}", file=sys.stderr)
            raise SystemExit(EXIT_TOOLING)
        for dep in data.get("dependencies", []):
            for vuln in dep.get("vulns", []):
                findings.append({
                    "id": vuln["id"],
                    "package": dep["name"],
                    "version": dep.get("version", "?"),
                    "fix": ", ".join(vuln.get("fix_versions") or []) or "none",
                    "source": req.name,
                })
    # De-duplicate: requirements-dev.txt includes requirements.txt via `-r`, so
    # every runtime package is audited twice. Reporting each finding once keeps
    # the register from needing duplicate entries for the same advisory.
    unique: Dict[Tuple[str, str], Dict[str, str]] = {}
    for f in findings:
        unique.setdefault((f["id"], f["package"]), f)
    return list(unique.values())


def audit_npm(min_severity: List[str]) -> List[Dict[str, str]]:
    """Every npm advisory at or above the configured severities.

    npm audit exits non-zero when it finds anything, so its return code says
    nothing about whether the *tool* worked. Absence of parseable JSON is the
    tooling-failure signal instead.
    """
    proc = _run(["npm", "audit", "--json"], cwd=FRONTEND)
    if not proc.stdout.strip():
        print(f"ERROR: npm audit produced no output.\n{proc.stderr[-2000:]}",
              file=sys.stderr)
        raise SystemExit(EXIT_TOOLING)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"ERROR: npm audit output was not JSON.\n{proc.stdout[:500]}",
              file=sys.stderr)
        raise SystemExit(EXIT_TOOLING)
    if "vulnerabilities" not in data:
        print("ERROR: npm audit JSON has no 'vulnerabilities' key — unexpected "
              "schema; refusing to report a pass.", file=sys.stderr)
        raise SystemExit(EXIT_TOOLING)

    findings: List[Dict[str, str]] = []
    for pkg, info in data["vulnerabilities"].items():
        if info.get("severity") not in min_severity:
            continue
        own = [v for v in info.get("via", []) if isinstance(v, dict)]
        if own:
            for v in own:
                ident = (v.get("url") or "").rstrip("/").split("/")[-1] or f"npm:{pkg}"
                findings.append({
                    "id": ident,
                    "package": pkg,
                    "version": info.get("range", "?"),
                    "fix": "yes" if info.get("fixAvailable") is True else "breaking/none",
                    "source": "package-lock.json",
                })
        else:
            # Flagged only because a dependency is vulnerable — no advisory of
            # its own, so it is keyed by package.
            findings.append({
                "id": f"pkg:{pkg}",
                "package": pkg,
                "version": info.get("range", "?"),
                "fix": "yes" if info.get("fixAvailable") is True else "breaking/none",
                "source": "package-lock.json",
            })
    return findings


# --------------------------------------------------------------------------- #
# Policy                                                                       #
# --------------------------------------------------------------------------- #
def evaluate(findings: List[Dict[str, str]], entries: List[Entry], ecosystem: str,
             today: date, warn_within: int) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings) for one ecosystem."""
    errors: List[str] = []
    warnings: List[str] = []

    by_key: Dict[Tuple[str, str], Entry] = {}
    for e in entries:
        if e.ecosystem == ecosystem:
            by_key[e.key()] = e

    for f in findings:
        lookup = (ecosystem, f["id"])
        entry = by_key.get(lookup)
        if entry is None:
            errors.append(
                f"UNTRIAGED  [{ecosystem}] {f['package']} {f['version']} — {f['id']}\n"
                f"           fix: {f['fix']}  (seen in {f['source']})\n"
                f"           Add an entry to .github/dependency-triage.yml with "
                f"reachability evidence and an expiry, or upgrade the package.")
            continue
        entry.matched = True
        if entry.expires < today:
            errors.append(
                f"EXPIRED    [{ecosystem}] {f['package']} — {f['id']}\n"
                f"           expired {entry.expires.isoformat()} "
                f"(owner: {entry.owner})\n"
                f"           Re-argue the case and update the entry, or remediate. "
                f"Moving the date without re-arguing is what this check exists to stop.")
        elif (entry.expires - today).days <= warn_within:
            warnings.append(
                f"EXPIRING   [{ecosystem}] {f['package']} — {f['id']} "
                f"expires {entry.expires.isoformat()} "
                f"({(entry.expires - today).days}d, owner: {entry.owner})")

    # Stale entries: suppressing something that no longer exists. This is the
    # check that would have caught the 8 dead litellm/ecdsa suppressions.
    for e in entries:
        if e.ecosystem == ecosystem and not e.matched:
            errors.append(
                f"STALE      [{ecosystem}] {e.package} — {e.id}\n"
                f"           The register suppresses this, but the auditor no longer "
                f"reports it.\n"
                f"           Delete the entry: a suppression for a finding that does "
                f"not exist hides nothing and rots.")
    return errors, warnings


def render_summary(path: Path, sections: List[Tuple[str, List[Dict[str, str]], List[Entry]]],
                   errors: List[str], warnings: List[str], today: date) -> None:
    lines = ["## Dependency supply chain", ""]
    if errors:
        lines.append(f"**FAILED** — {len(errors)} policy violation(s).")
    else:
        lines.append("**PASSED** — every advisory is either fixed or triaged with an "
                     "unexpired, evidenced entry.")
    lines.append("")
    for name, findings, entries in sections:
        eco_entries = [e for e in entries if e.ecosystem == name]
        lines += [f"### {name}", "",
                  f"- advisories reported: **{len(findings)}**",
                  f"- triaged entries: **{len(eco_entries)}**", ""]
        if eco_entries:
            lines += ["| Package | Advisory | Class | Owner | Expires |",
                      "|---|---|---|---|---|"]
            for e in sorted(eco_entries, key=lambda x: (x.package, x.id)):
                flag = " ⚠️" if (e.expires - today).days <= 30 else ""
                lines.append(f"| `{e.package}` | {e.id} | {e.classification} | "
                             f"{e.owner} | {e.expires.isoformat()}{flag} |")
            lines.append("")
    if warnings:
        lines += ["### Expiring soon", ""] + [f"- {w}" for w in warnings] + [""]
    if errors:
        lines += ["### Violations", "", "```"] + errors + ["```", ""]
    lines.append("Register: `.github/dependency-triage.yml` · "
                 "policy: `.claude/SECRETS.md` §7–8")
    with path.open("a") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ecosystem", choices=["python", "npm", "all"], default="all")
    ap.add_argument("--summary", type=Path, default=None,
                    help="append a Markdown report here (e.g. $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--today", default=None, help="override today's date (testing)")
    args = ap.parse_args()

    today = (datetime.strptime(args.today, "%Y-%m-%d").date()
             if args.today else date.today())

    try:
        entries, policy = load_register(REGISTER)
    except ValueError as exc:
        print(f"ERROR: invalid triage register — {exc}", file=sys.stderr)
        return EXIT_TOOLING

    warn_within = int(policy.get("warn_within_days", 30))
    npm_sevs = list(policy.get("npm_fail_severities") or ["high", "critical"])

    all_errors: List[str] = []
    all_warnings: List[str] = []
    sections: List[Tuple[str, List[Dict[str, str]], List[Entry]]] = []

    if args.ecosystem in ("python", "all"):
        findings = audit_python([BACKEND / "requirements.txt",
                                 BACKEND / "requirements-dev.txt"])
        errs, warns = evaluate(findings, entries, "python", today, warn_within)
        all_errors += errs
        all_warnings += warns
        sections.append(("python", findings, entries))
        print(f"python: {len(findings)} advisories reported")

    if args.ecosystem in ("npm", "all"):
        findings = audit_npm(npm_sevs)
        errs, warns = evaluate(findings, entries, "npm", today, warn_within)
        all_errors += errs
        all_warnings += warns
        sections.append(("npm", findings, entries))
        print(f"npm: {len(findings)} advisories reported "
              f"(severities: {', '.join(npm_sevs)})")

    for w in all_warnings:
        print(f"::warning::{w}" if _in_actions() else f"WARN  {w}")

    if args.summary:
        render_summary(args.summary, sections, all_errors, all_warnings, today)

    if all_errors:
        print("\n" + "=" * 78)
        print(f"DEPENDENCY AUDIT FAILED — {len(all_errors)} violation(s)")
        print("=" * 78)
        for e in all_errors:
            print(f"\n{e}")
        print("\nRegister: .github/dependency-triage.yml")
        return EXIT_POLICY

    print("\nOK — every advisory is fixed or covered by an unexpired, evidenced "
          "register entry, and every entry still matches a real finding.")
    return EXIT_OK


def _in_actions() -> bool:
    import os
    return os.environ.get("GITHUB_ACTIONS") == "true"


if __name__ == "__main__":
    sys.exit(main())

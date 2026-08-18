"""The build context excludes test artifacts (PH3.12 finding C-1).

WHAT THIS GUARDS
----------------
PH3.12 certification found `backend/test-results/junit.xml` — an untracked,
git-ignored file written by the project's own CI command
(`backend-ci.yml`: `pytest --junit-xml=test-results/junit.xml`) — baked into a
certified release image at `/app/test-results/junit.xml`.

`backend/.dockerignore` excluded the test *inputs* (`tests/`, `test_*.py`,
`conftest.py`) but not the test *outputs*. Because Docker does not read
`.gitignore`, a file can be invisible to `git status`, absent from every commit,
and still land in the image via `COPY . .`. The measurable consequence: a build
from a working directory produced 117 files under `/app`, while a build from a
clean `git archive` of the SAME commit produced 116. The image was not a
function of the commit.

WHY A TEST AND NOT JUST THE FIX
-------------------------------
The fix is nine lines of `.dockerignore`. What made C-1 survive three sprints
was not difficulty — it was that nothing would ever notice. The same is true of
its recurrence: the next runner that writes `report.xml`, or the next engineer
who adds a generated directory to `.gitignore` and stops there, reintroduces it
silently. These tests fail loudly instead.

They are deliberately hermetic — they parse `.dockerignore` and never invoke
Docker — so they run in the default suite on every push, where a
Docker-dependent test could not.

ON THE MATCHING MODEL
---------------------
`_excluded` implements the subset of Docker's `.dockerignore` semantics this
file relies on, and the part that actually bit us: **a bare pattern anchors to
the build-context root**. `test-results/` does not exclude
`sub/test-results/`; only `**/test-results/` does. That was verified empirically
against Docker 29.4.0 (a probe context in which `sub/test-results/junit.xml`
was copied in under the bare pattern and excluded under the `**/` form) before
this file was written, so the model below reflects observed behaviour rather
than a reading of the documentation.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest

#: The backend build context — the directory `docker build ... backend` is given.
CONTEXT_ROOT = Path(__file__).resolve().parent.parent
DOCKERIGNORE = CONTEXT_ROOT / ".dockerignore"


def _patterns() -> list[str]:
    """Every effective (non-comment, non-blank) rule in `.dockerignore`."""
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [s for line in lines if (s := line.strip()) and not s.startswith("#")]


def _excluded(path: str, patterns: list[str]) -> bool:
    """Whether `path` (relative to the context root, `/`-separated) is excluded.

    A directory pattern (`foo/`) excludes the directory and everything beneath
    it. A `**/`-prefixed pattern matches at any depth. A bare pattern matches
    only at the context root — the anchoring rule that C-1 turned on.
    """
    for raw in patterns:
        if raw.startswith("!"):          # negation — not used in this file
            continue
        pattern = raw.rstrip("/")
        is_dir_rule = raw.endswith("/")

        if pattern.startswith("**/"):
            tail = pattern[3:]
            # Match the pattern against the basename at any depth, and against
            # every ancestor segment when the rule names a directory.
            segments = path.split("/")
            candidates = segments if is_dir_rule else segments[-1:]
            if any(fnmatch.fnmatch(seg, tail) for seg in candidates):
                return True
        else:
            if fnmatch.fnmatch(path, pattern):
                return True
            if is_dir_rule and (path == pattern or path.startswith(pattern + "/")):
                return True
    return False


# --------------------------------------------------------------------------- #
# The artifact that actually leaked                                             #
# --------------------------------------------------------------------------- #

def test_the_c1_artifact_is_excluded():
    """`test-results/junit.xml` — the exact path found inside a release image."""
    assert _excluded("test-results/junit.xml", _patterns()), (
        "backend/test-results/junit.xml would be copied into the production "
        "image. This is PH3.12 finding C-1 reopening."
    )


def test_the_c1_directory_itself_is_excluded():
    assert _excluded("test-results", _patterns())


# --------------------------------------------------------------------------- #
# The class, not just the instance                                              #
# --------------------------------------------------------------------------- #

#: Paths a common Python test runner writes into the working directory. Each
#: would have leaked exactly the way `test-results/junit.xml` did.
LEAKY_ARTIFACTS = [
    "test-results/junit.xml",
    "test_reports/iteration_1.json",
    "junit.xml",
    "junit-backend.xml",
    "results.junit.xml",
    "coverage.xml",
    "nosetests.xml",
    "report.xml",
    ".coverage",
    ".pytest_cache/CACHEDIR.TAG",
    "htmlcov/index.html",
    ".benchmarks/results.json",
    ".hypothesis/examples/abc",
]


@pytest.mark.parametrize("artifact", LEAKY_ARTIFACTS)
def test_test_result_artifacts_are_excluded(artifact):
    assert _excluded(artifact, _patterns()), (
        f"{artifact!r} is not excluded by backend/.dockerignore and would be "
        f"baked into the production image."
    )


@pytest.mark.parametrize("artifact", LEAKY_ARTIFACTS)
def test_test_result_artifacts_are_excluded_at_any_depth(artifact):
    """Docker anchors bare patterns to the context root.

    `test-results/` alone leaves `services/test-results/` exposed. Every rule in
    the C-1 block therefore ships a `**/` twin, and this test is what keeps the
    twin from being dropped as redundant — it is not.
    """
    nested = f"services/{artifact}"
    assert _excluded(nested, _patterns()), (
        f"{nested!r} is not excluded. A bare .dockerignore pattern matches only "
        f"at the build-context root; add a '**/'-prefixed twin."
    )


# --------------------------------------------------------------------------- #
# Test INPUTS stay excluded too (the rules that already worked)                 #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", [
    "tests/conftest.py",
    "tests/test_build_context.py",
    "conftest.py",
    "pytest.ini",
    "requirements-dev.txt",
    ".env",
    "venv/bin/python",
])
def test_test_inputs_and_secrets_stay_excluded(path):
    assert _excluded(path, _patterns()), f"{path!r} must never enter the image."


# --------------------------------------------------------------------------- #
# The guard must not over-reach                                                 #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", [
    "server.py",
    "models.py",
    "requirements.txt",
    "security/api_docs.py",
    "services/paper_trade.py",
    "analytics/registry.py",
    "docker/entrypoint.sh",
    "docker/healthcheck.sh",
])
def test_application_source_is_still_included(path):
    """A too-broad exclusion is an outage, not a hardening win.

    `*.md` and `docs/` are excluded by design, but nothing in the C-1 block may
    catch a module the container needs to boot.
    """
    assert not _excluded(path, _patterns()), (
        f"{path!r} is excluded from the build context — the image would be "
        f"missing code it needs to run."
    )


def test_gitignored_generated_dirs_are_also_dockerignored():
    """Being git-ignored is not protection: Docker never reads `.gitignore`.

    That asymmetry is the root cause of C-1 — `test-results/` was git-ignored,
    therefore invisible to `git status` and absent from every commit, and went
    into the image anyway. For each generated directory the repo declares in
    `.gitignore`, this asserts the Docker build context excludes it too.

    Only directories actually named in `.gitignore` are checked, so the test
    describes the real coupling instead of asserting what a sibling file ought
    to contain.
    """
    gitignore = (CONTEXT_ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    patterns = _patterns()
    candidates = ("test-results/", "htmlcov/", "coverage.xml", ".coverage")

    declared = [d for d in candidates if d in gitignore]
    assert "test-results/" in declared, (
        "test-results/ is no longer git-ignored; the C-1 premise has changed."
    )
    for entry in declared:
        probe = f"{entry}probe" if entry.endswith("/") else entry
        assert _excluded(probe, patterns), (
            f"{entry} is git-ignored but NOT excluded from the Docker build "
            f"context — exactly the PH3.12 C-1 shape."
        )

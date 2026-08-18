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

ON THE MATCHING MODEL (REWRITTEN — PH3.12 finding C-3)
------------------------------------------------------
These tests need to know what Docker *would* do, without running Docker. Until
C-3 they answered that with `fnmatch`, and `fnmatch` is the wrong model: its `*`
crosses `/`, Docker's does not. The guard therefore evaluated
`analytics/registry.pyc` against the root-anchored `*.py[cod]` rule, concluded
it was excluded, and stayed green while Docker copied 109 host `.pyc` files into
a certified release image. **110 paths on the release tree were reported
excluded when Docker in fact copied them in.**

Matching now runs through `tests/_dockerignore.py`, a port of the Go code Docker
actually executes (`dockerignore.ReadAll` + `moby/patternmatcher`). It is not
assumed to be correct: `tests/test_dockerignore_semantics.py` builds real
contexts with `docker buildx` and requires the port and Docker to agree on every
cell of a pattern/path matrix, including this repository's own `.dockerignore`.

The lesson C-3 encodes, and the reason the port exists rather than a tidier
approximation: **a guard that models another tool's matcher must be checked
against that tool**, or it becomes a way to certify the thing it was written to
prevent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._dockerignore import DockerIgnoreMatcher, read_dockerignore

#: The backend build context — the directory `docker build ... backend` is given.
CONTEXT_ROOT = Path(__file__).resolve().parent.parent
DOCKERIGNORE = CONTEXT_ROOT / ".dockerignore"


def _patterns() -> list[str]:
    """Every effective rule in `.dockerignore`, parsed the way Docker parses it."""
    return read_dockerignore(DOCKERIGNORE.read_text(encoding="utf-8"))


def _excluded(path: str, patterns: list[str]) -> bool:
    """Whether Docker would keep `path` out of the build context.

    `path` is relative to the context root and `/`-separated, exactly as
    BuildKit's `fsutil` presents it.
    """
    return DockerIgnoreMatcher(patterns).excluded(path)


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


# --------------------------------------------------------------------------- #
# C-3 — host bytecode must not enter the context at ANY depth                   #
# --------------------------------------------------------------------------- #

#: The paths measured inside the pre-fix build context of the release tree. Each
#: is a real file that Docker copied in while the guard reported it excluded.
C3_BYTECODE_PATHS = [
    "__pycache__/server.cpython-311.pyc",
    "analytics/__pycache__/registry.cpython-311.pyc",
    "analytics/__pycache__/registry.cpython-314.pyc",
    "analytics/__pycache__/__init__.cpython-314.pyc",
    "infrastructure/__pycache__/redis_client.cpython-311.pyc",
    "observability/__pycache__/logging.cpython-311.pyc",
    "security/__pycache__/api_docs.cpython-311.pyc",
    "services/__pycache__/paper_trade.cpython-311.pyc",
    "services/brokers/__pycache__/zerodha.cpython-311.pyc",
    "services/market_engine/__pycache__/gateway.cpython-311.pyc",
    "services/realtime/__pycache__/event_bridge.cpython-311.pyc",
    "a/b/c/d/e/__pycache__/arbitrarily_deep.cpython-311.pyc",
    "analytics/stray.pyc",
    "analytics/stray.pyo",
    "services/market_engine/stray.pyd",
    "services/x$py.class",
]


@pytest.mark.parametrize("artifact", C3_BYTECODE_PATHS)
def test_host_bytecode_is_excluded_at_any_depth(artifact):
    """PH3.12 finding C-3.

    `__pycache__/` and `*.py[cod]` were bare, therefore root-anchored, so every
    nested `__pycache__` was copied into the image: 109 host `.pyc` files on the
    release tree, 5 of them compiled by a Python 3.14 the container cannot load,
    and 100 of them carrying an absolute developer path in `co_filename` that
    would surface in a production traceback.
    """
    assert _excluded(artifact, _patterns()), (
        f"{artifact!r} would be copied into the production image. This is "
        f"PH3.12 finding C-3 reopening: the image stops being a function of the "
        f"commit and ships developer-machine paths."
    )


@pytest.mark.parametrize("depth", range(1, 7))
def test_bytecode_exclusion_is_depth_independent(depth):
    """Not "deep enough" — independent of depth, which is a different claim."""
    nested = "/".join(["pkg"] * depth) + "/__pycache__/mod.cpython-311.pyc"
    assert _excluded(nested, _patterns()), (
        f"bytecode at depth {depth} ({nested!r}) enters the build context."
    )


@pytest.mark.parametrize("artifact", [
    "services/.env",
    "services/.env.production",
    "services/market_engine/provider.env",
    "services/broker.key",
    "security/server.pem",
    "a/b/credentials.json",
    "services/brokers/zerodha_token.json",
])
def test_secrets_are_excluded_at_any_depth(artifact):
    """The same anchoring bug applied to the secret rules, which is worse.

    `.env`, `*.key` and `*.pem` were root-anchored too. No nested secret file
    exists in the tree today — this is the rule that guarantees one could never
    be copied into an image layer, where deleting it later does not remove it.
    """
    assert _excluded(artifact, _patterns()), (
        f"{artifact!r} would be baked into an image layer. Secrets in a layer "
        f"are permanent and readable by anyone who pulls the image."
    )


def test_nested_test_inputs_and_docs_are_excluded():
    """The remaining root-anchored rules, twinned in the same pass."""
    patterns = _patterns()
    for path in (
        "services/tests/test_thing.py",
        "services/conftest.py",
        "analytics/test_registry.py",
        "services/AI_WORKSPACE.md",
        "services/docs/design.md",
        "services/node_modules/pkg/index.js",
        "services/app.log",
        "services/local.sqlite3",
    ):
        assert _excluded(path, patterns), (
            f"{path!r} enters the build context despite a rule that was meant "
            f"to keep its whole class out."
        )


# --------------------------------------------------------------------------- #
# The guard must not over-reach — an over-exclusion is an outage                 #
# --------------------------------------------------------------------------- #

#: Every production entry point the container needs to boot and serve. Named
#: explicitly (rather than only discovered by walking) so that deleting one from
#: the tree is a test failure rather than a silently smaller check.
REQUIRED_PRODUCTION_FILES = [
    "server.py",
    "models.py",
    "requirements.txt",
    "docker/entrypoint.sh",
    "docker/healthcheck.sh",
    "security/api_docs.py",
    "security/jwt.py",
    "security/sessions.py",
    "services/paper_trade.py",
    "services/trading_engine.py",
    "services/market_engine/gateway.py",
    "services/brokers/zerodha.py",
    "services/realtime/event_bridge.py",
    "analytics/registry.py",
    "observability/logging.py",
    "infrastructure/redis_client.py",
]


@pytest.mark.parametrize("path", REQUIRED_PRODUCTION_FILES)
def test_required_production_files_are_present_and_included(path):
    """Both halves matter: the file exists in the tree AND survives the filter.

    C-3's fix twinned ~50 rules with `**/` forms. Every one of those is a chance
    to delete a module from the image, and a missing module is a container that
    will not boot. This is the check that makes that impossible to do quietly.
    """
    assert (CONTEXT_ROOT / path).exists(), (
        f"{path!r} is named as a required production file but is not in the "
        f"tree — update this list or restore the file."
    )
    assert not _excluded(path, _patterns()), (
        f"{path!r} is excluded from the build context. The image would be "
        f"missing code it needs to run."
    )


#: Directories whose contents are excluded on purpose and must not be counted
#: as production source when walking the tree.
_DELIBERATELY_EXCLUDED_DIRS = {"venv", ".venv", "tests", "__pycache__", "docs"}


def _production_python_modules() -> list[str]:
    """Every `.py` file in the tree that the running container is expected to have."""
    modules = []
    for path in sorted(CONTEXT_ROOT.rglob("*.py")):
        relative = path.relative_to(CONTEXT_ROOT)
        if _DELIBERATELY_EXCLUDED_DIRS.intersection(relative.parts):
            continue
        if relative.name == "conftest.py" or relative.name.startswith("test_"):
            continue
        modules.append(relative.as_posix())
    return modules


def test_no_production_module_in_the_tree_is_excluded():
    """Driven by the tree, not by a hand-written list, so it cannot go stale.

    Walks every application `.py` file that is not in a deliberately-excluded
    directory and asserts the build context keeps it. A future `**/`-twin that
    is one character too broad fails here, naming the module it would have
    deleted.
    """
    patterns = _patterns()
    modules = _production_python_modules()

    assert len(modules) > 50, (
        f"only {len(modules)} production modules discovered — the walk is "
        f"broken and this test would pass vacuously."
    )

    dropped = [m for m in modules if _excluded(m, patterns)]
    assert not dropped, (
        f"{len(dropped)} production module(s) are excluded from the build "
        f"context by backend/.dockerignore:\n  " + "\n  ".join(dropped)
    )


def test_the_docker_entrypoint_directory_survives_in_full():
    """`Dockerfile` chmods these two by path; losing either breaks the image."""
    patterns = _patterns()
    scripts = sorted(p.name for p in (CONTEXT_ROOT / "docker").glob("*.sh"))
    assert scripts, "backend/docker/ has no shell scripts — premise changed."
    for name in scripts:
        assert not _excluded(f"docker/{name}", patterns), (
            f"docker/{name} is excluded; `chmod 0755 docker/{name}` in the "
            f"Dockerfile would fail the build."
        )

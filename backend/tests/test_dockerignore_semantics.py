"""`tests/_dockerignore.py` predicts Docker exactly (PH3.12 finding C-3).

WHY THIS FILE EXISTS
--------------------
`test_build_context.py` protects the production image by answering "would Docker
copy this in?". It answers that question with a *model* of Docker's matcher,
because a hermetic test cannot start a daemon on every push.

C-3 is what happens when that model is wrong. The previous one used `fnmatch`,
whose `*` crosses `/`, so it reported 110 files as excluded that Docker copied
into a certified release image. The guard was not merely incomplete — it was
confidently wrong, in the direction that reports a leak as clean.

So the model is not trusted here. It is *tested against the real thing*:

* The hermetic tests below pin each individual semantic rule — root anchoring,
  `*` versus `/`, `**`, `**/`, directory patterns, negation, nesting — and run
  everywhere, including in CI.
* The differential tests build an actual context with `docker buildx` and
  require Docker's answer and the model's answer to agree on **every cell** of
  the matrix. They are marked `requires_docker` and skip when no daemon is
  reachable.

The second group is the one that makes the first group honest. Without it these
are assertions about what we believe Docker does; with it they are assertions
about what Docker was observed to do.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests._dockerignore import DockerIgnoreMatcher, clean, read_dockerignore


def _matcher(*lines: str) -> DockerIgnoreMatcher:
    return DockerIgnoreMatcher(read_dockerignore("\n".join(lines)))


# --------------------------------------------------------------------------- #
# The matrix. Each case is (dockerignore lines, {path: expected_excluded}).      #
#                                                                               #
# This single structure drives BOTH the hermetic tests and the differential      #
# tests, so there is no way to satisfy one and quietly drift from the other.     #
# --------------------------------------------------------------------------- #

CASES: dict[str, tuple[list[str], dict[str, bool]]] = {
    # ── Root anchoring: the property C-3 turned on ─────────────────────────── #
    "root-anchored-plain": (
        ["secret.txt"],
        {
            "secret.txt": True,
            "sub/secret.txt": False,
            "a/b/secret.txt": False,
            "keep.txt": False,
        },
    ),
    # ── `*` must not cross a path separator ────────────────────────────────── #
    # This is the exact cell the fnmatch model got wrong. `fnmatch` says
    # "sub/x.pyc" matches "*.py[cod]"; Docker says it does not.
    "star-does-not-cross-separator": (
        ["*.py[cod]"],
        {
            "x.pyc": True,
            "x.pyo": True,
            "x.pyd": True,
            "x.py": False,
            "sub/x.pyc": False,          # ← C-3 in one line
            "a/b/x.pyc": False,
            "analytics/__pycache__/registry.cpython-311.pyc": False,
        },
    ),
    "question-mark-does-not-cross-separator": (
        ["a?c"],
        {"abc": True, "a/c": False, "abbc": False},
    ),
    # ── Directory patterns ─────────────────────────────────────────────────── #
    # `filepath.Clean` erases the trailing slash, so `dir/` and `dir` are the
    # SAME rule to Docker: both exclude the directory, its whole subtree, and a
    # plain file of that name. The old model treated them as different.
    "directory-pattern-trailing-slash": (
        ["build/"],
        {
            "build/out.js": True,
            "build/deep/out.js": True,
            "build": True,               # a plain FILE named build, also excluded
            "sub/build/out.js": False,   # still root-anchored
            "builder/out.js": False,
        },
    ),
    "directory-pattern-without-slash-is-identical": (
        ["build"],
        {
            "build/out.js": True,
            "build/deep/out.js": True,
            "build": True,
            "sub/build/out.js": False,
        },
    ),
    # ── `**/` — the depth-independent form ─────────────────────────────────── #
    "double-star-slash-prefix": (
        ["**/__pycache__/"],
        {
            "__pycache__/x.pyc": True,           # `**/` matches ZERO segments too
            "analytics/__pycache__/x.pyc": True,
            "a/b/c/__pycache__/x.pyc": True,
            "analytics/__pycache__": True,
            "analytics/registry.py": False,
            "pycache/x.pyc": False,
        },
    ),
    "double-star-slash-with-wildcard": (
        ["**/*.py[cod]"],
        {
            "x.pyc": True,
            "sub/x.pyc": True,
            "a/b/c/x.pyc": True,
            "sub/x.py": False,
        },
    ),
    # ── `**` in other positions ────────────────────────────────────────────── #
    "double-star-trailing": (
        ["logs/**"],
        {"logs/a.log": True, "logs/deep/a.log": True, "logsx/a.log": False},
    ),
    "double-star-in-middle": (
        ["a/**/z.txt"],
        {
            "a/z.txt": True,             # `**` can match zero segments
            "a/b/z.txt": True,
            "a/b/c/z.txt": True,
            "b/z.txt": False,
        },
    ),
    # ── Nested path patterns ───────────────────────────────────────────────── #
    "explicit-nested-path": (
        ["services/cache/"],
        {
            "services/cache/a.bin": True,
            "services/cache": True,
            "services/paper_trade.py": False,
            "other/services/cache/a.bin": False,
        },
    ),
    # ── Negation ───────────────────────────────────────────────────────────── #
    # Last matching rule wins, so ordering is load-bearing.
    "negation-reincludes": (
        ["*.md", "!README.md"],
        {"NOTES.md": True, "README.md": False, "sub/NOTES.md": False},
    ),
    "negation-inside-excluded-directory": (
        ["docs/", "!docs/KEEP.md"],
        {"docs/other.md": True, "docs/KEEP.md": False},
    ),
    "negation-order-matters": (
        ["!README.md", "*.md"],
        {"README.md": True, "NOTES.md": True},
    ),
    # ── Leading slash is stripped by the reader ────────────────────────────── #
    "leading-slash-is-root-anchored": (
        ["/secret.txt"],
        {"secret.txt": True, "sub/secret.txt": False},
    ),
    # ── The real defect, end to end ────────────────────────────────────────── #
    "c3-bare-versus-twinned": (
        ["__pycache__/", "*.py[cod]", "**/__pycache__/", "**/*.py[cod]"],
        {
            "__pycache__/registry.cpython-311.pyc": True,
            "analytics/__pycache__/registry.cpython-311.pyc": True,
            "analytics/__pycache__/registry.cpython-314.pyc": True,
            "services/market_engine/__pycache__/gateway.cpython-311.pyc": True,
            "analytics/x.pyc": True,
            "analytics/registry.py": False,
            "server.py": False,
        },
    ),
}

#: Flattened to one assertion per (case, path) so a failure names the exact cell.
MATRIX = [
    (case, patterns, path, expected)
    for case, (patterns, paths) in CASES.items()
    for path, expected in paths.items()
]


# --------------------------------------------------------------------------- #
# Hermetic: the model implements each rule                                      #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "case,patterns,path,expected",
    MATRIX,
    ids=[f"{c}::{p}" for c, _, p, _ in MATRIX],
)
def test_model_implements_docker_semantics(case, patterns, path, expected):
    actual = _matcher(*patterns).excluded(path)
    verb = "EXCLUDED" if expected else "INCLUDED"
    assert actual == expected, (
        f"[{case}] {path!r} under {patterns!r}: Docker treats this as {verb}, "
        f"the model says {'EXCLUDED' if actual else 'INCLUDED'}."
    )


# --------------------------------------------------------------------------- #
# Hermetic: the parser matches dockerignore.ReadAll                             #
# --------------------------------------------------------------------------- #

def test_reader_drops_comments_and_blanks():
    assert read_dockerignore("# c\n\nfoo\n\n# d\nbar\n") == ["foo", "bar"]


def test_reader_only_treats_column_zero_hash_as_a_comment():
    """A Docker quirk, reproduced on purpose.

    `dockerignore.ReadAll` tests `strings.HasPrefix(line, "#")` on the RAW line,
    before trimming. An indented `#` line is therefore a *pattern*, not a
    comment. The previous guard stripped first and disagreed with Docker about
    which lines were even rules.
    """
    assert read_dockerignore("   # indented\n") == ["# indented"]


def test_reader_cleans_patterns_and_strips_leading_slash():
    assert read_dockerignore("/a/b/\n") == ["a/b"]
    assert read_dockerignore("a//b/./c/\n") == ["a/b/c"]
    assert read_dockerignore("!  /docs/keep.md\n") == ["!docs/keep.md"]


def test_clean_erases_the_trailing_slash():
    """The single fact that makes `tests/` and `tests` the same rule."""
    assert clean("tests/") == "tests"
    assert clean("**/test-results/") == "**/test-results"
    assert clean("") == "."


def test_illegal_bare_negation_is_rejected():
    with pytest.raises(ValueError):
        DockerIgnoreMatcher(["!"])


# --------------------------------------------------------------------------- #
# Differential: the model agrees with the Docker that is installed here          #
# --------------------------------------------------------------------------- #

_SCRATCH_DOCKERFILE = "FROM scratch\nCOPY . /\n"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "buildx", "version"],
            capture_output=True, timeout=60,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="needs a reachable Docker daemon with buildx",
)


def _partition(paths: list[str]) -> list[list[str]]:
    """Split `paths` so that no batch contains a path that is a parent of another.

    `build` and `build/out.js` are both legitimate probes — Docker treats a
    directory rule as excluding a plain file of that name too, and that is worth
    asserting — but no filesystem can hold both at once. Each batch becomes its
    own build context, and the results are unioned.
    """
    batches: list[list[str]] = []
    for path in paths:
        for batch in batches:
            if not any(
                other.startswith(path + "/") or path.startswith(other + "/")
                for other in batch
            ):
                batch.append(path)
                break
        else:
            batches.append([path])
    return batches


def _docker_keeps(patterns: list[str], paths: list[str]) -> set[str]:
    """The ground truth: which of `paths` does Docker put in the build context?

    Built with `FROM scratch` and `--output type=local`, so the answer is the
    build context itself — no base image is pulled, nothing is executed, and the
    result is a directory we can simply list. That keeps the oracle a direct
    observation of BuildKit's own filter rather than an inference from a built
    image.
    """
    batches = _partition(paths)
    if len(batches) > 1:
        return set().union(*(_docker_keeps_one(patterns, b) for b in batches))
    return _docker_keeps_one(patterns, paths)


def _docker_keeps_one(patterns: list[str], paths: list[str]) -> set[str]:
    """One build context, one `docker buildx build`, one observation."""
    with tempfile.TemporaryDirectory() as workdir:
        context = Path(workdir) / "ctx"
        output = Path(workdir) / "out"
        context.mkdir()
        output.mkdir()

        for path in paths:
            target = context / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"content-of:{path}\n", encoding="utf-8")

        # The build's own two files are named in .dockerignore so they cannot be
        # confused with the probe paths.
        (context / ".dockerignore").write_text(
            "\n".join([*patterns, "Dockerfile", ".dockerignore"]) + "\n",
            encoding="utf-8",
        )
        (context / "Dockerfile").write_text(_SCRATCH_DOCKERFILE, encoding="utf-8")

        result = subprocess.run(
            ["docker", "buildx", "build", "--no-cache",
             "-f", str(context / "Dockerfile"),
             "--output", f"type=local,dest={output}", str(context)],
            capture_output=True, text=True, timeout=600,
        )
        assert result.returncode == 0, f"docker build failed:\n{result.stderr}"

        return {
            str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()
        }


@requires_docker
@pytest.mark.parametrize("case", sorted(CASES), ids=sorted(CASES))
def test_model_matches_real_docker(case):
    """Every cell of the matrix, re-measured against the installed Docker.

    If Docker's semantics ever change, or the model drifts from them, this is
    what fails — and it fails naming the pattern and the path.
    """
    patterns, expectations = CASES[case]
    paths = sorted(expectations)

    kept = _docker_keeps(patterns, paths)
    matcher = _matcher(*patterns)

    disagreements = []
    for path in paths:
        docker_excluded = path not in kept
        model_excluded = matcher.excluded(path)
        if docker_excluded != model_excluded:
            disagreements.append(
                f"  {path!r}: docker="
                f"{'EXCLUDED' if docker_excluded else 'INCLUDED'} "
                f"model={'EXCLUDED' if model_excluded else 'INCLUDED'}"
            )
        # The table in CASES is also asserted against reality, so a wrong
        # expectation cannot hide behind a matching wrong model.
        assert docker_excluded == expectations[path], (
            f"[{case}] the expectation for {path!r} under {patterns!r} does not "
            f"match what Docker actually did."
        )

    assert not disagreements, (
        f"[{case}] the model disagrees with Docker under {patterns!r}:\n"
        + "\n".join(disagreements)
    )


@requires_docker
def test_model_matches_real_docker_on_the_real_dockerignore():
    """The whole matrix is synthetic; this one is the file we actually ship.

    Runs the repository's own `backend/.dockerignore` over a context containing
    the production entry points, the artifacts C-1 and C-3 were raised for, and
    a handful of near-miss paths, then requires Docker and the model to agree
    on all of them.
    """
    from tests.test_build_context import CONTEXT_ROOT

    patterns = read_dockerignore(
        (CONTEXT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    )
    paths = [
        # Production source that must survive.
        "server.py", "models.py", "requirements.txt",
        "docker/entrypoint.sh", "docker/healthcheck.sh",
        "security/api_docs.py", "services/paper_trade.py",
        "analytics/registry.py", "services/market_engine/gateway.py",
        # C-3: host bytecode at every depth.
        "__pycache__/server.cpython-311.pyc",
        "analytics/__pycache__/registry.cpython-311.pyc",
        "analytics/__pycache__/registry.cpython-314.pyc",
        "services/market_engine/__pycache__/gateway.cpython-311.pyc",
        "a/b/c/d/__pycache__/deep.cpython-311.pyc",
        "analytics/stray.pyc", "analytics/stray.pyo",
        # C-1: test result artifacts at every depth.
        "test-results/junit.xml", "services/test-results/junit.xml",
        "services/.coverage", "services/htmlcov/index.html",
        # Secrets at depth.
        "services/.env", "services/broker.key", "a/b/credentials.json",
        # Near-misses that must NOT be swept up.
        "services/pycache_helper.py", "analytics/notpyc.py",
    ]

    kept = _docker_keeps(patterns, paths)
    matcher = _matcher(*patterns)

    disagreements = [
        f"  {p!r}: docker={'EXCLUDED' if p not in kept else 'INCLUDED'} "
        f"model={'EXCLUDED' if matcher.excluded(p) else 'INCLUDED'}"
        for p in paths
        if (p not in kept) != matcher.excluded(p)
    ]
    assert not disagreements, (
        "backend/.dockerignore is interpreted differently by the model and by "
        "Docker:\n" + "\n".join(disagreements)
    )

    # And, independently of the model: Docker itself must keep the production
    # entry points and drop every artifact.
    for required in ("server.py", "models.py", "docker/entrypoint.sh"):
        assert required in kept, f"Docker drops {required!r} — the image cannot boot."
    for artifact in (
        "analytics/__pycache__/registry.cpython-311.pyc",
        "analytics/stray.pyc",
        "services/test-results/junit.xml",
        "services/.env",
    ):
        assert artifact not in kept, f"Docker still copies {artifact!r} into the image."

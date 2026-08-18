"""A faithful model of Docker's `.dockerignore` matching (PH3.12 finding C-3).

WHY THIS MODULE EXISTS
----------------------
`test_build_context.py` guards the single auditable boundary between this
repository and the production image. To do that it has to answer one question —
*would Docker copy this path into the build context?* — and for three sprints it
answered that question with `fnmatch`.

`fnmatch` is the wrong model, and not in a subtle way. Its `*` crosses `/`;
Docker's does not. So the guard evaluated `analytics/registry.pyc` against the
root-anchored `*.py[cod]` rule, saw a match, and reported the file **excluded**
— while Docker copied it straight into the image. That is finding C-3: not a
missing rule, but a guard whose model of the tool disagreed with the tool, in
the exact direction that reports a leak as clean.

A guard that models another tool's matcher is only as good as that model. This
module therefore does not approximate: it is a line-for-line port of the Go code
Docker actually runs.

WHAT DOCKER ACTUALLY RUNS
-------------------------
Two components, in order:

1. `github.com/moby/buildkit/frontend/dockerfile/dockerignore.ReadAll` — the
   client-side parser. Reads the file, drops comments and blanks, `filepath.
   Clean`s each pattern and strips a leading `/`.
2. `github.com/moby/patternmatcher` — the matcher. Compiles each cleaned
   pattern to a regexp and evaluates it with `MatchesOrParentMatches`, which
   tests the path *and every one of its ancestor directories*.

`read_dockerignore()` and `DockerIgnoreMatcher` below correspond exactly to
those two, including their quirks. The quirks are the point: a "cleaned up"
model would be a different tool, and a guard is only useful if it is wrong in
the same places the real thing is.

THE FIVE SEMANTICS THAT MATTER HERE
-----------------------------------
=========================  ====================================================
`*`                        matches any run of characters **except `/`**. This is
                           the one that caused C-3.
`**`                       matches any number of path segments, including zero.
                           A leading `**/` is what makes a rule depth-
                           independent.
bare pattern               is anchored to the build-context root. `venv` does
                           not exclude `sub/venv`.
trailing `/`               is **erased** by `filepath.Clean`. `tests/` and
                           `tests` are the same rule to Docker; both exclude the
                           directory, everything under it, *and* a plain file
                           of that name.
`!pattern`                 re-includes. Order matters: the last pattern to match
                           decides, and the matcher skips patterns that cannot
                           change the current verdict.
=========================  ====================================================

EQUIVALENCE IS PROVEN, NOT ASSERTED
-----------------------------------
`test_dockerignore_semantics.py` runs this module and a real `docker buildx`
build over the same pattern/path matrix and requires the two to agree on every
cell. If Docker changes, that test fails — which is the only way a model like
this one stays honest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["read_dockerignore", "DockerIgnoreMatcher", "clean"]


# --------------------------------------------------------------------------- #
# Go's `path.Clean`                                                             #
# --------------------------------------------------------------------------- #

def clean(path: str) -> str:
    """Port of Go's `path.Clean` (`filepath.Clean` on a `/`-separated OS).

    Collapses `//`, resolves `.` and `..`, and — the behaviour that matters for
    `.dockerignore` — **removes a trailing slash**. Docker applies this to every
    pattern as it is read, which is why a directory rule written `tests/` is
    indistinguishable from `tests` by the time the matcher sees it.
    """
    if path == "":
        return "."
    rooted = path[0] == "/"
    out: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if out and out[-1] != "..":
                out.pop()
            elif not rooted:
                out.append("..")
            continue
        out.append(segment)
    result = "/".join(out)
    if rooted:
        result = "/" + result
    return result or "."


def _dir(path: str) -> str:
    """Port of Go's `filepath.Dir`. `"server.py"` -> `"."`, `"a/b/c"` -> `"a/b"`."""
    index = path.rfind("/")
    return clean("" if index < 0 else path[: index + 1])


# --------------------------------------------------------------------------- #
# The parser — dockerignore.ReadAll                                             #
# --------------------------------------------------------------------------- #

def read_dockerignore(text: str) -> list[str]:
    """Port of BuildKit's `dockerignore.ReadAll`.

    Note the one genuinely surprising rule, reproduced deliberately: the comment
    check runs on the **raw** line, *before* whitespace is stripped. A line
    written `    # not a comment` is therefore not a comment to Docker — it
    becomes the pattern `# not a comment`. The previous guard stripped first and
    so disagreed with Docker about which lines were even rules.
    """
    patterns: list[str] = []
    for index, raw in enumerate(text.splitlines()):
        if index == 0:
            raw = raw.lstrip("﻿")          # UTF-8 BOM
        if raw.startswith("#"):                  # raw line, not stripped
            continue
        pattern = raw.strip()
        if not pattern:
            continue
        inverted = pattern[0] == "!"
        if inverted:
            pattern = pattern[1:].strip()
        if pattern:
            pattern = clean(pattern)
            if len(pattern) > 1 and pattern[0] == "/":
                pattern = pattern[1:]
        patterns.append("!" + pattern if inverted else pattern)
    return patterns


# --------------------------------------------------------------------------- #
# The matcher — moby/patternmatcher                                             #
# --------------------------------------------------------------------------- #

def _compile(pattern: str) -> re.Pattern[str]:
    """Port of `patternmatcher.Pattern.compile` for a `/` separator.

    Docker does not use `fnmatch` and does not use Go's `filepath.Match` at
    match time either — it translates the pattern to a regexp once, with these
    rules:

    * `**` (optionally followed by `/`) -> `(.*/)?`, or `.*` when it ends the
      pattern. Both cross `/`, and both can match zero segments.
    * `*`  -> `[^/]*`   — **cannot cross a path separator.** C-3 lives here.
    * `?`  -> `[^/]`
    * `.` and `$` are escaped; `[`, `]` and every other character are emitted
      verbatim, so character classes such as `[cod]` survive as regexp classes.

    The verbatim pass-through means a pattern containing `+` or `(` is
    interpreted as a regexp operator by Docker. That is a real upstream quirk,
    reproduced here rather than corrected, because this module's job is to
    predict Docker rather than to be a better matcher than Docker.
    """
    out = "^"
    index, length = 0, len(pattern)
    while index < length:
        char = pattern[index]
        index += 1
        if char == "*":
            if index < length and pattern[index] == "*":
                index += 1
                if index < length and pattern[index] == "/":
                    index += 1                       # "**/" is treated as "**"
                out += ".*" if index >= length else "(.*/)?"
            else:
                out += "[^/]*"
        elif char == "?":
            out += "[^/]"
        elif char in (".", "$"):
            out += "\\" + char
        elif char == "\\":
            if index < length:
                out += "\\" + pattern[index]
                index += 1
            else:
                out += "\\"
        else:
            out += char
    return re.compile(out + "$")


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern[str]
    exclusion: bool


class DockerIgnoreMatcher:
    """Port of `patternmatcher.PatternMatcher` + `MatchesOrParentMatches`.

    Usage::

        matcher = DockerIgnoreMatcher(read_dockerignore(text))
        matcher.excluded("analytics/__pycache__/registry.cpython-311.pyc")

    Paths are relative to the build-context root and `/`-separated, exactly as
    BuildKit's `fsutil` presents them.
    """

    def __init__(self, patterns: list[str]) -> None:
        compiled: list[_Pattern] = []
        for raw in patterns:
            pattern = raw.strip()
            if not pattern:
                continue
            pattern = clean(pattern)
            exclusion = pattern[0] == "!"
            if exclusion:
                if len(pattern) == 1:
                    raise ValueError('illegal exclusion pattern: "!"')
                pattern = clean(pattern[1:]).lstrip("/")
            compiled.append(_Pattern(_compile(pattern), exclusion))
        self._patterns = compiled

    def excluded(self, path: str) -> bool:
        """Whether Docker would keep `path` OUT of the build context.

        Implements `MatchesOrParentMatches`. Two behaviours here are easy to get
        wrong and are both load-bearing:

        1. **Ancestors are tested too.** A rule matching `services` excludes
           `services/paper_trade.py`, because the walk checks every prefix of
           the path. This is what makes a directory rule work at all, given that
           `filepath.Clean` has already thrown the trailing slash away.
        2. **The skip is not an optimisation.** `pattern.exclusion != matched`
           means an ordinary rule is only consulted while the verdict is
           "included", and a `!` rule only while it is "excluded" — so the
           **last** matching rule wins, which is how a negation can re-include a
           file that an earlier rule excluded.
        """
        matched = False
        parent = _dir(path)
        segments = parent.split("/")

        for pattern in self._patterns:
            if pattern.exclusion != matched:
                continue

            hit = pattern.regex.match(path) is not None
            if not hit and parent != ".":
                for depth in range(len(segments)):
                    if pattern.regex.match("/".join(segments[: depth + 1])):
                        hit = True
                        break

            if hit:
                matched = not pattern.exclusion
        return matched

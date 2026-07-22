# Continuous Integration — GitHub Actions

**Sprint:** PH2.4 — Production GitHub Actions CI
**Status:** Implemented
**Scope:** Everything that happens automatically between `git push` and a merge decision — the workflows, what each one proves, how they are triggered, how they are cached, and how to debug them. **Continuous *Deployment* is deliberately not covered and not implemented**: nothing here pushes an image, touches a registry, or contacts a server. §10 documents the CD integration path without building it.

**Authoritative companions:** [`.claude/TESTING.md`](../../.claude/TESTING.md) is the testing strategy this pipeline mechanises. [`.claude/SECRETS.md`](../../.claude/SECRETS.md) §7–8 owns the dependency-advisory triage policy the audit workflow enforces. [`DOCKER.md`](DOCKER.md) describes the image that `docker-build.yml` verifies. The workflow files themselves are the single source of truth if any of these disagree.

---

## 1. What problem this solves

Before PH2.4 the repository had one workflow (`security-audit.yml`) covering dependency advisories and secret hygiene. Everything else — does it build, does it import, do the tests pass, does the container start — was verified by whoever remembered to check.

That is not a process. It is a habit, and habits fail exactly when the pressure is highest: the Friday hotfix, the 200-line refactor, the dependency bump nobody expected to matter. The failure mode is not that engineers are careless; it is that "run the tests before pushing" has no enforcement point, so the one time it is skipped is indistinguishable from the hundred times it was not.

The pipeline converts four verification habits into mechanical properties of the repository:

| Question | Previously | Now |
|---|---|---|
| Does the code parse and import? | Discovered at `docker run`, or in production | `backend-ci` / `build` |
| Do the tests pass? | Whoever remembered | `backend-ci` / `test` — 695 hermetic tests |
| Does the *artifact* work, not just the source? | Discovered on deploy | `docker-build` — builds and **starts** the real image |
| Is the dependency set still safe? | Weekly, manually | `dependency-audit` — every PR and every Monday |

There is a fifth property, and it is the one teams forget: the pipeline verifies that the system **refuses to start when it should**. A gate that only tests the happy path passes just as happily after someone accidentally reduces the configuration validator to `return True`. See §5, Smoke A.

---

## 2. Architecture

Five workflows and one shared composite action. Each workflow owns one question, and no check appears in two of them.

```
                             ┌──────────────────────┐
   push to main ────────────▶│                      │
   pull request ────────────▶│   GitHub Actions     │
   weekly cron ─────────────▶│                      │
   manual dispatch ─────────▶└──────────┬───────────┘
                                        │
        ┌───────────────────┬───────────┴────┬─────────────────┬──────────────┐
        ▼                   ▼                ▼                 ▼              ▼
 ┌─────────────┐    ┌──────────────┐  ┌─────────────┐  ┌──────────────┐ ┌──────────┐
 │ backend-ci  │    │ docker-build │  │ dependency- │  │  security-   │ │  codeql  │
 │             │    │              │  │   audit     │  │   audit      │ │          │
 │ ┌─────────┐ │    │ ┌──────────┐ │  │ ┌─────────┐ │  │ ┌──────────┐ │ │ ┌──────┐ │
 │ │ quality │ │    │ │ hadolint │ │  │ │pip-audit│ │  │ │ gitleaks │ │ │ │python│ │
 │ ├─────────┤ │    │ ├──────────┤ │  │ ├─────────┤ │  │ ├──────────┤ │ │ ├──────┤ │
 │ │  build  │ │    │ │  build   │ │  │ │npm audit│ │  │ │config    │ │ │ │ js/ts│ │
 │ ├─────────┤ │    │ │    +     │ │  │ └─────────┘ │  │ │drift     │ │ │ └──────┘ │
 │ │  test   │ │    │ │ smoke ABC│ │  │             │  │ └──────────┘ │ │          │
 │ └─────────┘ │    │ └──────────┘ │  │             │  │              │ │          │
 └──────┬──────┘    └──────┬───────┘  └──────┬──────┘  └──────┬───────┘ └────┬─────┘
        │                  │                 │                │              │
        ▼                  ▼                 ▼                ▼              ▼
   backend-ci ✓      docker-build ✓   dependency-audit ✓  (per-job)      codeql ✓
        └──────────────────┴─────────────────┴────────────────┴──────────────┘
                                        │
                                        ▼
                            merge allowed / blocked
```

| Workflow | Question it answers | Blocking |
|---|---|---|
| `backend-ci.yml` | Is the **source** correct? | Yes |
| `docker-build.yml` | Is the **artifact** correct? | Yes |
| `dependency-audit.yml` | Is **someone else's code** safe? | Yes |
| `security-audit.yml` | Did we **leak or drift** configuration? | Yes |
| `codeql.yml` | Is **our own code** vulnerable? | Gated — see §8 |

### The shared composite action

`.github/actions/setup-backend/` provides the seven steps that four jobs across three workflows all need: pick an interpreter, restore a cache, build a virtualenv, install pinned dependencies, put it on `PATH`, verify the graph with `pip check`.

It is a **composite action**, not a reusable workflow (`workflow_call`), and the distinction is worth internalising:

- A **reusable workflow** is a shared *job*. It gets its own runner, its own checkout, and its own scheduling latency, and it cannot contribute steps to the caller.
- A **composite action** is shared *steps*. It inlines into the calling job.

This is shared steps. Using a reusable workflow here would have added a runner and a checkout to four jobs for no benefit.

The value is not the saved typing — it is that the Python version, the cache key and the install command exist in exactly **one** place. Four copies do not merely cost four edits; they create four things that can silently disagree, and a pipeline whose lint job runs on 3.11 while its test job runs on 3.12 will eventually pass a lint the tests contradict.

---

## 3. Trigger events

Every workflow uses the same trigger set, with one deliberate difference.

```yaml
on:
  push:
    branches: [main]     # the merged result — main must be known-good
  pull_request:          # the proposed change — before it becomes main's problem
  workflow_dispatch:     # manual re-run, without an empty commit
  schedule:              # dependency-audit, security-audit, codeql only
```

**Why both `push: [main]` and `pull_request`, when a PR is what gets merged.** They test different commits. A `pull_request` run tests the *merge result* of your branch and the current base. A `push` run tests what actually landed. Between the two, base can move — someone else merges a change that conflicts semantically (not textually) with yours, and both PRs were green in isolation. Only the `push` run catches it. This is the single most common cause of "main is broken but every PR was green".

**Why `schedule` only on the three security workflows.** A cron trigger earns its cost only when the answer can change without the code changing. Advisory databases publish new CVEs against unchanged dependencies; gitleaks ships new detection rules for unchanged history; CodeQL's query packs are updated continuously. Re-running the test suite on a schedule would burn runner minutes to re-derive an answer that cannot have changed.

The three schedules are deliberately staggered — `dependency-audit` and `security-audit` on Monday 06:00 UTC, `codeql` on Wednesday 04:00 UTC. Firing three heavy workflows into the same minute makes them contend for the same concurrent-runner budget, and all three finish later than any one of them would alone.

### There are no `paths:` filters, on purpose

It is tempting to write `paths: [backend/**]` so a frontend-only PR skips the backend pipeline. **Do not.** A path-filtered workflow does not run at all when nothing matches — and GitHub reports a never-started *required* check as "Expected — waiting for status", permanently. The PR is blocked with no way to clear it short of an admin override. The documented workaround (a second stub workflow declaring the same job names, which reports success) is more machinery than the problem justifies here: the virtualenv cache makes a no-op backend run cost well under a minute.

### Concurrency

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

Superseded PR runs are cancelled — pushing three times in five minutes should not queue three full pipelines when only the last matters. Runs on `main` are **never** cancelled: every commit on the default branch must carry its own complete verdict, or bisecting a regression later means bisecting through commits that were never actually tested.

---

## 4. Job order and the aggregate gate

Within `backend-ci`, the three working jobs run **in parallel**, not in a chain.

```
   quality ─┐
   build   ─┼──▶ backend-ci (aggregate gate)
   test    ─┘
```

A serial chain (lint → build → test) is the more common layout and it is a mistake for a suite this size. A developer with both a lint error and a broken test learns about the lint error, fixes it, pushes, waits another four minutes, and only then learns about the test. Parallel jobs report every category of failure in one round trip. The cost is a few runner-minutes; the saving is developer hours.

### Why every workflow ends in an aggregate job

Each workflow terminates in a job named identically to the workflow (`backend-ci`, `docker-build`, `dependency-audit`, `codeql`) whose only purpose is to assert that its dependencies succeeded.

Two reasons, and the second is a security property:

1. **Branch protection needs a stable check name.** Requiring `quality`, `build` and `test` individually means editing the repository's protection settings every time a job is added or renamed — a settings change nobody remembers to make, which silently un-gates the new job. One aggregate name survives any amount of internal restructuring.

2. **`needs:` alone does not fail closed.** A needed job that is *skipped* or *cancelled* leaves the dependent job skipped too — and **GitHub counts a skipped required check as passing**. That is a real hole: it is how a cancelled test job merges a broken PR. The aggregate job uses `if: always()` to run regardless, then asserts on the literal result strings:

```yaml
for r in "$quality" "$build" "$test"; do
  [ "$r" = "success" ] || { echo "::error::..."; exit 1; }
done
```

`success` is required. `skipped`, `cancelled` and `failure` all fail the gate.

---

## 5. What each stage actually verifies

### `backend-ci` / `quality` — formatting, imports, linting, static analysis

See §6 for the adoption model. Blocking today: the flake8 correctness subset repo-wide, and the full standard on files *added* by a pull request.

### `backend-ci` / `build` — three widening circles

Python has no compile step, which tempts teams to skip "build" and rely on the tests. That leaves a real gap: a syntax error or a bad top-level import in a module **no test imports** is invisible to pytest and fatal at deploy time.

| Circle | Check | Catches |
|---|---|---|
| 1 | `python -m compileall` over every shipped source | Syntax errors in unexercised modules |
| 2 | `import server` with **runtime dependencies only** | A module that imports a dev-only package (would crash the production image); any broken module-scope side effect |
| 3 | `security.secrets.validate_config()` across all three environments, **plus a negative case** | A regression in the startup validator |

Circle 2 installs `requirements.txt` and not `requirements-dev.txt` deliberately. The production image is built from the runtime set alone (PH1.11 finding M14); installing the dev set here would let a module `import pytest` at runtime and still pass CI.

Circle 3's negative case is the important half. Every value is CSPRNG-generated in-process — no real credential is needed to test a validator, which is also what lets this workflow run unchanged on a fork's pull request.

```
✓ development  accepted — [secrets] env=development configured=11 ... errors=0
✓ staging      accepted — [secrets] env=staging     configured=11 ... errors=0
✓ production   accepted — [secrets] env=production  configured=11 ... warnings=8 errors=0
✓ production   rejected an empty configuration, naming every missing core secret
```

### `backend-ci` / `test` — the hermetic suite

```
pytest -m "not integration" --junit-xml=test-results/junit.xml --durations=10 -q
→ 695 passed, 98 deselected
```

The 98 deselected tests are the older `requests`/`websockets` suites, which drive a *running* backend over HTTP at `$REACT_APP_BACKEND_URL` and, through it, a real MongoDB and real market-data providers.

**The marker is applied in `backend/tests/conftest.py`, not in the workflow file.** That placement is deliberate. Listing the excluded filenames in YAML would put the rule far away from the tests it governs, where the next person to add a live-server suite would never find it — and their suite would then run in CI, reach no server, and fail for a reason that looks nothing like the cause. Adding a live-server suite now means adding its filename to `_LIVE_SERVER_SUITES`; nothing in `.github/` changes.

Those suites are not abandoned: PH3.1 owns converting them to hermetic equivalents, and the roadmap's PH2.6 stage boots the real Compose stack and runs `pytest -m integration` against it.

JUnit XML is uploaded as a build artifact with `if: always()` — an artifact you only get when the build is green is an artifact you never need.

### `docker-build` — the artifact, actually started

`backend-ci` proves the source is good. It says nothing about the artifact, and the gap between them is where the most embarrassing incidents live: a dependency needing a compiler the runtime stage does not have, a file excluded by `.dockerignore` that the app imports at startup, a `chmod` that was only correct on the author's laptop. Every one of those passes pytest perfectly and fails at `docker run`.

After hadolint and the buildx build, the image is asserted against the security posture PH2.1 established — non-root uid 10001, no `pip`, no compiler, `/app` not writable by the app user, `HEALTHCHECK` declared, only port 8000 exposed, interpreter version matching CI, revision label carrying the building commit's SHA. A property nobody re-checks is a property that regresses the first time someone refactors the file "harmlessly".

Then three smoke tests:

| | Test | What it proves |
|---|---|---|
| **A** | Run with an empty configuration | The container **refuses to start** and names every missing core secret |
| **B** | Full synthetic production env + command override | The strictest configuration profile validates — *and no secret value appears in the log* |
| **C** | Staging env + real MongoDB and Redis | Boots, serves `/api` with the expected payload, its own health-check script reports healthy, and `SIGTERM` produces a graceful exit 0 |

**Smoke A is the most important test in this file** and the one teams skip. Everything else asks "does it start?". A asks "does it refuse to start when it should?" — because the failure that reaches customers is not a container that will not boot; it is a container that boots happily with a missing `JWT_SECRET` and only misbehaves under real traffic.

**Why B and C are separate.** B exercises the full production validator (cross-field invariants: Mongo credentials present, Redis password present, valid Fernet key, an AI provider configured) via the entrypoint's documented `exec "$@"` escape hatch, without booting the server. C boots against real datastores under `APP_ENV=staging`, which exercises the same fail-closed startup path without demanding a third-party AI credential that CI has no business holding. Conflating them would mean a production-only validator change could only be caught by a full stack boot.

C polls rather than sleeps. A fixed `sleep 15` is either too short (a flaky pipeline nobody trusts) or too long (minutes burned on every run, forever).

**Nothing is pushed.** There is no registry login, no `push: true`, and the workflow token cannot write packages. An artifact CI can push is an artifact a compromised pull request can push.

---

## 6. The lint adoption model — why some checks are advisory

This is the part of the pipeline most likely to be misread as sloppiness, so it is documented in full.

The backend predates its linters. Measured at the time this pipeline was written:

| Check | Findings | Blocking? |
|---|---|---|
| `flake8 --select=E9,F63,F7,F82,F811,F632` (correctness) | **0** | **Yes, repo-wide** |
| `black`/`isort`/`flake8` on files **added** by a PR | n/a | **Yes** |
| `black --check` repo-wide | 116 of 119 files | Advisory |
| `isort --check-only` repo-wide | 70 files | Advisory |
| `flake8` full style repo-wide (120 cols) | 462 findings | Advisory |
| `mypy` on `backend/security/` | 2 findings | Advisory |

There were three options and only one of them is honest:

- **(a) Land a 116-file mechanical reformat in the same PR as the CI pipeline.** Unreviewable, and it destroys `git blame` for every security module PH1 just hardened.
- **(b) Turn the gates on anyway and accept a permanently red `main`.** A red build everyone ignores is worse than no build: it trains the team that red means nothing, and the *next* failure — a real one — gets ignored too.
- **(c) Gate what can genuinely be held at zero, measure the rest in the open, and hold new files to the full standard so the backlog can only shrink.**

The pipeline implements **(c)**.

**Why the correctness subset can be strict on day one.** Every code in it is a defect, not a preference: `E9` syntax errors, `F82` undefined names (a typo'd variable that raises `NameError` at runtime, on whichever branch nobody tested), `F811` a function redefined over an earlier one (silently killing the first definition and any test written against it), `F632` `is` against a literal (works by accident today via interning, breaks without warning on other values). The backend has zero of these. It stays that way.

**Why modified files are exempt from the full standard.** Forcing a contributor to reformat 400 unrelated lines because they fixed a typo is how a style policy gets a reputation for obstruction — and it buries the actual change where no reviewer can see it. Only *added* files (`--diff-filter=A`) are gated.

**Advisory means `continue-on-error` on the step, not the job.** The step shows a warning annotation in the UI and the counts land in the job summary, so the debt is impossible to miss while remaining impossible to trip over.

### The exit path

Advisory is a transitional state with an end condition, not a permanent excuse:

1. One dedicated PR, no functional changes: `black . && isort .` across the backend. Reviewed by confirming the test suite is unchanged, not by reading the diff.
2. Flip `black --check` and `isort --check-only` to blocking in the same PR.
3. Work the 462 flake8 findings down by category (`F401` unused imports first — 49 of them, mechanical and safe), then flip the full lint to blocking.
4. Expand `[tool.mypy] files` in `backend/pyproject.toml` package by package as annotations land.

Step 1 is a strong candidate for the next sprint. It is a large diff but a trivial review, and it is strictly cheaper the sooner it happens.

---

## 7. Supply chain and secret hygiene

### `dependency-audit.yml`

`pip-audit --strict` against both requirement files, and `npm audit --audit-level=high` against the frontend lockfile.

`--strict` is what makes this a gate rather than a report. Without it, pip-audit exits 0 when it merely **fails to resolve** a package — so a typo, a yanked release or a registry outage presents as a clean audit. Silent success is the worst possible failure mode for a security check.

#### ⚠️ 15 advisories are currently suppressed

This is real accepted risk and it is documented here rather than buried in a flag:

| Package | Advisories | Fix available | Remediation |
|---|---|---|---|
| `starlette 0.37.2` | 7 (`PYSEC-2026-161`, `-249`, `-248`, `-1943`, `-1941`, `-2281`, `-2280`) | 0.40.0 → 1.3.1 | **Highest priority.** Starlette is the ASGI layer every request traverses. The pin is held by `fastapi==0.110.1`, so this is a coordinated FastAPI + Starlette upgrade, not a version bump. |
| `litellm 1.80.0` | 7 (`PYSEC-2026-390`, `-388`, `-2597`, `-2599`, `-2598`, `-2600`, `GHSA-69x8-hrgq-fjj8`) | 1.83.0 → 1.84.0 | **Remove, do not upgrade.** PH2.1's image analysis found litellm is not imported by any application code — 55 MB of unused dependency carrying seven advisories. |
| `ecdsa 0.19.2` | 1 (`PYSEC-2026-1325`) | none released | Not reachable. Transitive via `python-jose`; this application signs JWTs with HS256 (HMAC) and never performs an ECDSA curve operation. |

Suppression is **not** remediation. It exists so the gate can distinguish "debt we have already decided about" from "something new arrived today" — a gate that is red for fifteen old reasons cannot report the sixteenth, new one, which is the entire reason the gate exists.

**The suppression cannot become permanent.** A `SUPPRESSION_REVIEW_BY` date (currently `2026-08-22`) is enforced by a job step: from that date every run emits a warning, and 30 days later the build **fails** until someone re-argues the case. Without a mechanism like this, `--ignore-vuln` is where vulnerabilities go to be forgotten — the engineer who added the flag remembers why, leaves, and three years later nobody can say whether the risk was ever real.

`npm audit --audit-level=high` fails on high and critical only. Not laziness: the npm database reports a large volume of low/moderate findings in build-time-only transitive packages, and a gate that is red every morning for reasons nobody can act on is a gate the team learns to bypass. Lower severities are still counted in the job summary and in Dependabot's queue.

### `security-audit.yml`

Kept separate from `backend-ci` for a concrete reason: `secret-scan` needs `fetch-depth: 0` — the **full** git history, because a credential deleted in a later commit is still readable from the object store. Every other job wants a shallow clone for speed.

Two layers, deliberately: a deterministic guard that no `.env` file is tracked (unambiguous, and by far the most common way a real credential enters a repository), then gitleaks' probabilistic entropy and pattern matching over all history.

Test fixtures containing deliberately fake-but-realistic credentials carry an inline `# gitleaks:allow`. That is the right way to silence a true-positive-shaped false positive: the exemption sits on the line it exempts, where a reviewer cannot miss it — never as a broad path exclusion, which would also hide a real leak added to the same file later.

`config-sync` runs `scripts/generate_env_example.py --check`: `backend/.env.example` must still match the registry in `security/secrets.py`. Documentation drift is a security problem, not a tidiness problem — an operator provisioning a new environment from a stale template ships without a variable the code now requires and finds out in production.

### What moved in PH2.4, and why

| Check | Was | Now | Why |
|---|---|---|---|
| `pip-audit`, `npm audit` | `security-audit.yml` | `dependency-audit.yml` | Sprint-named file; one workflow per question |
| `pip check` | `security-audit.yml` (own job, own install) | `backend-ci` / `build`, via the composite action | The runtime set is installed there anyway — a dedicated job was a duplicate install for one command |

No check runs twice anywhere in the pipeline.

---

## 8. CodeQL — and why it is gated

flake8 asks "is this written well?". pip-audit asks "is someone else's code vulnerable?". Neither answers the question that ends up in an incident report: **is our code vulnerable?**

CodeQL compiles the repository into a relational database of its own control and data flow, then queries it for the shapes of real vulnerability classes — a request parameter reaching a database query unsanitised, user input reaching `subprocess`, an HTTP handler reaching the filesystem, a hard-coded credential used in a comparison. It is **taint tracking**, not pattern matching: it follows a value across function and module boundaries, which is why it finds the injection a grep for `eval(` never will.

That matters more than average here. The backend handles brokerage credentials, executes trades, and processes payment webhooks. The classes CodeQL is best at — injection, SSRF, path traversal, unsafe deserialization, missing authorization on a route — are precisely the ones with financial consequences.

**Availability.** CodeQL is free for public repositories. On a **private** repository it requires GitHub Advanced Security. This repository is currently private, so the `eligibility` job checks `github.event.repository.private` at run time and skips the analysis cleanly rather than failing every run with a licensing error — a permanently red check trains people to ignore red checks.

To enable it:

1. Enable GitHub Advanced Security (Settings → Code security).
2. Set the repository variable `ENABLE_CODEQL=true` (Settings → Secrets and variables → Actions → Variables).

Or make the repository public, in which case it activates automatically — the eligibility check reads the flag at run time rather than assuming, precisely so no human has to remember.

`security-events: write` is the only write scope granted anywhere in this pipeline, and it is the narrowest that exists: it permits writing SARIF results to the Security tab and nothing else. It cannot push code, publish packages, or edit issues.

---

## 9. Caching

Two caches, chosen for two different cost profiles.

### Python — cache the virtualenv, not pip's download cache

`actions/setup-python` offers `cache: pip`, which caches `~/.cache/pip`. That saves the **download** but still pays for resolution, wheel unpacking and the ~1.2 GB of writes that pandas/numpy/grpcio/protobuf cost — roughly 60–70 % of the wall clock. The composite action caches the **built venv** instead, turning a ~2 minute install into a ~10 second restore.

```yaml
key: venv-${{ runner.os }}-${{ runner.arch }}-py${{ inputs.python-version }}-${{ inputs.requirements }}-${{ hashFiles('backend/requirements.txt', 'backend/requirements-dev.txt') }}
```

Both requirement files are hashed regardless of which set is installed, because `requirements-dev.txt` opens with `-r requirements.txt` — hashing only one would let a runtime bump be served a stale dev venv.

**There is deliberately no `restore-keys` fallback.** A partial-match restore hands a job an environment that does not match its lockfile, and the resulting failure ("this worked yesterday") is one of the most expensive kinds of CI bug there is. A cache miss costs two minutes; a wrong hit costs an afternoon. The pins are exact, so identical requirements files mean a byte-identical dependency set — which is the entire safety argument for caching a venv at all.

### Docker — `type=gha`, `mode=max`

Ephemeral runners start with an empty daemon, so without an external cache backend the ~120-package dependency layer is rebuilt on every run. `cache-to: type=gha,mode=max,scope=backend` stores layers in the same Actions cache.

`mode=max` exports **intermediate** layers, not just the final stage's. For a multi-stage build that distinction is the whole point: all the expensive work (apt, `pip install`, the prune) happens in the discarded `builder` stage, whose layers `mode=min` would not store at all — leaving the cache technically working and practically useless. This is a common and well-hidden misconfiguration.

`load: true` imports the result into the local daemon so the smoke tests can run it. It requires a single platform; multi-arch builds are a CD concern (PH2.7) and would double this job's runtime for no CI signal.

### Cache limits

GitHub gives a repository 10 GB of Actions cache, evicting least-recently-used entries. Caches are also **branch-scoped**: a PR branch can read `main`'s caches but writes its own, so the first run on a new branch is a partial miss by design. Do not treat a cold first PR run as a bug.

---

## 10. Secrets used by CI

**The pipeline requires no configured repository secrets.** That is a design goal, not a coincidence, and it has three consequences worth stating:

1. A pull request **from a fork** runs the full pipeline unchanged. Workflows that need secrets cannot, which is why teams with secret-dependent CI end up unable to accept outside contributions.
2. There is no credential for a compromised dependency in the test suite to exfiltrate.
3. Nothing has to be rotated when someone leaves the team.

| Secret | Source | Used by | Notes |
|---|---|---|---|
| `GITHUB_TOKEN` | Injected automatically | `gitleaks` | Scoped `contents: read` by the top-level `permissions` block |

Every credential the jobs need is **generated at run time** with `secrets.token_urlsafe(48)` or `Fernet.generate_key()` and dies with the runner. No real API key, database password or signing secret is ever needed to test a *validator* — and Smoke B additionally asserts that no secret **value** ever appears in the container's startup log, since Actions logs are retained for 90 days and readable by anyone with repository access.

### Permissions

Every workflow declares `permissions: contents: read` at the top level, so every job inherits least privilege. The default for a repository without this block can be read/**write**, which hands a compromised dependency a token that can push to `main`. The single exception is CodeQL's `analyze` job (`security-events: write`), scoped to that job alone.

---

## 11. Verification and measured results

Verified locally before the workflows were committed:

| Check | Result |
|---|---|
| All 6 workflow/action YAML files parse | ✅ |
| Every `run:` block is valid bash (`bash -n`, GH expressions neutralised) | ✅ |
| Hermetic suite via the new marker selection | ✅ **695 passed, 98 deselected**, 62 s (local, Python 3.11) |
| `build` job's startup-validation script, executed verbatim | ✅ accepts dev/staging/prod, rejects an empty prod config |
| JUnit summary parser, against a real report | ✅ renders pass counts and per-test failure lines |
| Intentional failing test → `pytest` exit 1, failure rendered in summary | ✅ |
| flake8 correctness subset, repo-wide | ✅ 0 findings |
| `pip-audit` against `requirements.txt` | 15 advisories, 3 packages (§7) |

**Not verified locally: the Docker workflow.** The Docker daemon was unavailable on the development machine during this sprint, so `docker-build.yml` is verified by YAML parse, shell syntax check, and review against the PH2.1 Dockerfile contract — but its image build and three smoke tests have not been executed. **Its first real run will be on the first push.** See §12 for what to check if it fails.

**Runtime figures below are estimates, not measurements** — GitHub Actions was not exercised from the development machine.

| Job | Cold (cache miss) | Warm (cache hit) |
|---|---|---|
| `quality` | ~3 min | ~1 min |
| `build` | ~3 min | ~1 min |
| `test` | ~4 min | ~2 min |
| `docker-build` | ~8 min | ~3 min |
| `dependency-audit` | ~2 min | ~1.5 min |
| **Wall clock (parallel)** | **~8 min** | **~3–4 min** |

The roadmap's PH2.5 acceptance criterion is a pipeline under 10 minutes. Record the real numbers here after the first few runs and delete this note.

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `pip check` fails in the composite action | Incompatible pins installed together | Read the reported conflict; fix the pin in `requirements.txt`. Do not suppress — the same conflict is in the production image. |
| Cache never hits | `requirements*.txt` changed, or it is the first run on a new branch | Expected. Caches are branch-scoped; a new branch reads `main`'s but writes its own. |
| `test` passes locally, fails in CI | A test depends on local state (a file, a port, `~/.aws`, a real `.env`) | Reproduce with `cd backend && pytest -m "not integration"` in a clean checkout with no `.env` present. |
| A live-server suite ran in CI and failed to connect | Its filename is missing from `_LIVE_SERVER_SUITES` | Add it to that tuple in `backend/tests/conftest.py`. Never add `--ignore` to the workflow. |
| `--strict-markers` collection error | A marker was typo'd or is not declared | Declare it under `[tool.pytest.ini_options] markers` in `backend/pyproject.toml`. This error is the config working. |
| Smoke A fails ("FAIL-OPEN") | The container started with **no configuration** | A genuine security regression in `security/secrets.py` or `docker/entrypoint.sh`. Do not skip the test. |
| Smoke C times out at 90 s | Slow cold start, or the container exited during boot | The step dumps `docker logs` on failure. Check the entrypoint's validation output first. |
| Smoke B fails on the secret-leak check | A code path now logs a secret **value** | Fix the logging. Actions logs are retained 90 days and readable by anyone with repo access. |
| hadolint fails | A new `error`-level rule was hit | Fix the Dockerfile. Ignore a rule only by adding it to `.hadolint.yaml` **with a written reason** — an unexplained ignore is a defect. |
| `dependency-audit` fails on a *new* advisory | A CVE was published, or Dependabot bumped something | Triage per `.claude/SECRETS.md` §7. Suppress only with a `SECRETS.md` §8 entry. |
| `dependency-audit` fails with "suppressions passed their hard stop" | The 30-day grace after `SUPPRESSION_REVIEW_BY` elapsed | Re-triage. Remediate, or move the date **with a written justification**. |
| `codeql` reports skipped | Private repository without Advanced Security | Expected — see §8. |
| A required check is stuck "Expected — waiting for status" | A `paths:` filter was added to a required workflow | Remove it. See §3. |

**Reproducing a CI failure locally** — the commands are identical, which is the point of `backend/pyproject.toml`:

```bash
cd backend
python -m venv .venv-ci && ./.venv-ci/bin/pip install -r requirements-dev.txt
source .venv-ci/bin/activate

flake8 --select=E9,F63,F7,F82,F811,F632 .   # the blocking lint gate
pytest -m "not integration" -q              # the blocking test gate
python -m compileall -q -q -x '(venv|\.venv-ci|__pycache__|tests)' .
```

For the Docker stage:

```bash
docker build -t stockassist-backend:local ./backend
docker run --rm --env APP_ENV=production stockassist-backend:local   # must FAIL
```

---

## 13. Known limitations

| # | Limitation | Impact | Owner |
|---|---|---|---|
| L1 | `docker-build.yml` has never been executed — no Docker daemon during the sprint | The first push may need a fix-up commit | Next push |
| L2 | Formatting and full-style lint are advisory (§6) | 462 findings tracked, not enforced | §6 exit path |
| L3 | 98 integration tests excluded (§5) | The live-server layer is unverified by CI | PH3.1 / PH2.6 |
| L4 | No frontend build, lint or test job | A broken frontend build reaches `main` | PH3.3 |
| L5 | No test-coverage measurement | The 90 % target in TESTING.md is unmeasured | Needs `pytest-cov` pinned into `requirements-dev.txt` |
| L6 | Branch protection is not configured — the gates exist but nothing *requires* them | A red pipeline can still be merged | PH2.5 |
| L7 | No PR template with the PRODUCTION_HARDENING.md §15 checklist | Manual verification stays ad hoc | PH2.5 |
| L8 | 15 suppressed advisories (§7) | Real, dated, tracked accepted risk | `SUPPRESSION_REVIEW_BY` 2026-08-22 |
| L9 | Third-party actions are pinned by tag, not by commit SHA | A compromised tag could execute in CI | Hardening follow-up |
| L10 | No image vulnerability scan (Trivy/Grype) on the built image | Base-image CVEs are unmeasured | PH2.6 |

**L6 is the important one.** Every gate in this document is advisory until branch protection requires it. PH2.5 should require `backend-ci`, `docker-build` and `dependency-audit` on `main` — the aggregate job names exist precisely so that configuration never needs to change again.

---

## 14. Future: CD integration (PH2.7 — not implemented)

Nothing here deploys. The path from this pipeline to continuous deployment, recorded so the boundary stays deliberate:

```
  TODAY (PH2.4)                         FUTURE (PH2.7 — not built)
  ─────────────                         ──────────────────────────
  push / PR                             git tag v1.2.3
      │                                     │
      ▼                                     ▼
  backend-ci ─┐                         reuse docker-build's image
  docker-build ┼─▶ merge allowed  ────▶  push to GHCR (needs packages: write)
  dependency-audit ─┘                       │
  (image built, verified, DISCARDED)        ▼
                                        deploy staging (automatic)
                                            │
                                            ▼
                                        manual approval gate
                                            │
                                            ▼
                                        deploy production
                                            │
                                            ▼
                                        post-deploy health verification
                                            │
                                        auto-rollback on failure
```

What PH2.7 will need to add, and what it must **not** change:

- **Add:** a registry login and `push: true`, on `release`/tag triggers only; a GitHub Environment with required reviewers for the production gate; the deploy and rollback scripts named in the roadmap.
- **Keep:** `permissions: contents: read` on every CI workflow. The publish scope belongs to the release workflow alone. A CI workflow that can push images is a CI workflow a malicious pull request can use to push images.
- **Reuse:** `docker-build.yml`'s build step and cache scope, so the artifact that ships is built the same way as the artifact that was verified.

PH2.6 (extended CI) sits between: integration tests against the booted Compose stack, image vulnerability scanning (L10), and the env-drift check.

---

## 15. Related documentation

- [`DOCKER.md`](DOCKER.md) — the image `docker-build.yml` verifies, and the security posture its assertions encode
- [`DOCKER_COMPOSE.md`](DOCKER_COMPOSE.md) — the stack PH2.6's integration job will boot
- [`SECRETS.md`](SECRETS.md) — the resolution and validation mechanism the `build` job and Smoke A/B exercise
- [`.claude/TESTING.md`](../../.claude/TESTING.md) — the testing strategy this pipeline mechanises
- [`.claude/SECRETS.md`](../../.claude/SECRETS.md) §7–8 — advisory triage policy and the accepted-risk register
- [`.claude/PRODUCTION_ROADMAP.md`](../../.claude/PRODUCTION_ROADMAP.md) — PH2.5 (branch protection), PH2.6 (extended CI), PH2.7 (CD)
- [`docs/engineering/MERGE_POLICY.md`](../engineering/MERGE_POLICY.md) — the human half of the merge decision

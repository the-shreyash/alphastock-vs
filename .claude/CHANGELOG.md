# StockAssist AI
## Changelog

This file records documentation-system versions and, from v1.0 launch onward, product release notes. Documentation versions apply to the `.claude/` documentation set as a whole.

---

# Sprint PH2.4 — Production GitHub Actions CI — 2026-07-22

**Every push and every pull request is now verified by a machine. Five
workflows answer five separate questions — is the source correct, is the
*artifact* correct, is someone else's code safe, did we leak configuration, is
our own code vulnerable — and no check runs in more than one of them. CI only:
nothing in this sprint pushes an image, touches a registry, or contacts a
server.**

> **Design note — why some lint gates are advisory, and why that is not
> sloppiness.** The backend predates its linters: `black` would reformat 116 of
> 119 files, `isort` disagrees with 70, full `flake8` reports 462 findings.
> There were three options. Land a 116-file mechanical reformat in the same PR
> as the pipeline — unreviewable, and it destroys `git blame` across every
> security module PH1 just hardened. Turn the gates on anyway and accept a
> permanently red `main` — worse than no build, because a red build everyone
> ignores trains the team that red means nothing, and the *next* failure, a real
> one, gets ignored too. Or: gate what can genuinely be held at zero, measure
> the rest in the open, and hold new files to the full standard so the backlog
> can only shrink. The third is what shipped. The **correctness** subset
> (`E9,F63,F7,F82,F811,F632` — syntax errors, undefined names, redefinitions,
> `is` against a literal) is blocking repo-wide and sits at **zero findings**;
> files *added* by a pull request are blocking under the full standard;
> everything else reports into the job summary with a documented exit path.

Added

- **`.github/workflows/backend-ci.yml`** — three parallel jobs behind one
  aggregate gate. `quality` (lint/format/static analysis, per the adoption
  model above). `build` — three widening circles: `compileall` over every
  shipped source, `import server` **with runtime dependencies only** (so a
  module that imports a dev-only package fails here rather than in the
  production image), and the startup validator exercised across all three
  environments *including a negative case*. That negative case is the half
  teams omit: a validator accidentally reduced to `return True` passes every
  positive test perfectly. `test` — `pytest -m "not integration"`, 695 tests,
  with JUnit XML uploaded `if: always()`.
- **`.github/workflows/docker-build.yml`** — builds the real production image
  and then does the one thing a build never does: **starts it**. hadolint,
  buildx with a GHA layer cache, static assertions on the artifact (non-root
  uid 10001, no `pip`, no compiler, `/app` unwritable by the app user,
  `HEALTHCHECK` present, revision label carrying the building SHA), then three
  smoke tests — **A** the container refuses to start with no configuration and
  names every missing secret; **B** a full synthetic production configuration
  validates via the entrypoint's `exec "$@"` escape hatch, and no secret *value*
  appears in the log; **C** it boots against real MongoDB and Redis, serves
  `/api` with the expected payload, passes its own health-check script, and
  exits 0 on SIGTERM. Nothing is pushed and the token cannot write packages.
- **`.github/workflows/dependency-audit.yml`** — `pip-audit --strict` and
  `npm audit`, moved out of `security-audit.yml`, plus a **suppression-expiry
  ratchet**. The 15 currently-suppressed advisories (see below) now carry a
  review date: from `2026-08-22` every run warns, and 30 days later the build
  fails until someone re-argues the case. Without a mechanism like this,
  `--ignore-vuln` is where vulnerabilities go to be forgotten.
- **`.github/workflows/codeql.yml`** — taint-tracking SAST for Python and
  JavaScript/TypeScript. An `eligibility` job reads
  `github.event.repository.private` at run time and **skips cleanly** on a
  private repository without Advanced Security, rather than failing every run
  with a licensing error — a permanently red check trains people to ignore red
  checks. It activates automatically if the repository is made public.
- **`.github/actions/setup-backend/`** — composite action providing the seven
  steps four jobs share. A composite action rather than a reusable workflow
  because this is shared *steps*, not a shared *job*: a `workflow_call` would
  add a runner and a checkout to four jobs for nothing. It caches the **built
  virtualenv** keyed on the requirements hash, not pip's download cache — the
  latter still pays for resolution and unpacking, roughly 60-70% of the wall
  clock. There is deliberately **no `restore-keys` fallback**: a partial-match
  restore hands a job an environment that does not match its lockfile, and a
  miss costs two minutes where a wrong hit costs an afternoon.
- **`backend/pyproject.toml` + `backend/.flake8`** — pytest, black, isort and
  mypy configuration. The point is not tidiness: CI cannot be built on an
  invocation that lives in someone's shell history, and a pipeline that selects
  tests differently from the developer who wrote them will disagree with that
  developer at the worst possible moment. `--strict-markers` turns a typo'd
  marker into a collection error rather than a silently-ignored decorator.
- **`.hadolint.yaml`** — Dockerfile lint policy. Two rules ignored, each with a
  written reason; an unexplained ignore is a defect.
- **`docs/deployment/GITHUB_ACTIONS.md`** — workflow architecture, triggers,
  job order, the lint adoption model and its exit path, cache strategy, the
  accepted-risk register, verification results, troubleshooting table, known
  limitations L1-L10, and the CD boundary.

Changed

- **`backend/tests/conftest.py`** — a `pytest_collection_modifyitems` hook marks
  the six live-server suites (`test_backend.py`, `test_phase2.py`,
  `test_phase4-7.py`) as `integration`, so CI selects with
  `-m "not integration"`. The list lives **in the test tree, not in YAML**: put
  it in a workflow file and the next person to add a live-server suite never
  finds it, their suite runs in CI, reaches no server, and fails for a reason
  that looks nothing like the cause.
- **`.github/workflows/security-audit.yml`** — re-scoped to secret and
  configuration hygiene. `pip-audit`/`npm audit` moved to `dependency-audit.yml`;
  the standalone `pip check` job was deleted because the composite action runs
  `pip check` in every job that installs dependencies. No check now runs twice
  anywhere in the pipeline. `config-sync` uses the composite action, which also
  fixes its missing-dependency install.
- **`.claude/TESTING.md`** — the CI/CD section now separates the target pipeline
  from what is actually implemented, with owners for each gap.
- **`.gitignore`** — `.venv-ci/`, `.mypy_cache/`, `test-results/`.

Fixed

- **`backend/tests/test_trading_engine.py::test_run_cycle_trails_and_books_targets`**
  — a stale exact-equality assertion. `run_cycle` gained a `closed_trades` key
  in its return contract and the test was never updated, so the hermetic suite
  had a standing failure (694 passed / 1 failed, carried since PH2.3). Landing
  CI over a known-failing suite would have meant a red `main` on day one. The
  product code was correct; the assertion was not. Suite is now **695 passed**.

Known limitations

- **L1 — `docker-build.yml` has never been executed.** The Docker daemon was
  unavailable on the development machine during this sprint. The workflow is
  verified by YAML parse, `bash -n` over every `run:` block, and review against
  the PH2.1 Dockerfile contract, but its build and three smoke tests will run
  for the first time on the first push. Treat a fix-up commit as expected.
- **L2 — 15 dependency advisories are suppressed**, and this is the sprint's
  most significant *finding* rather than its work: `starlette 0.37.2` (7
  advisories, fixes available, held by the `fastapi==0.110.1` pin — highest
  priority, it is the ASGI layer every request traverses), `litellm 1.80.0` (7
  advisories; PH2.1 established it is **not imported by any application code**,
  so the fix is removal, not an upgrade), `ecdsa 0.19.2` (1, no fix released,
  not reachable — JWTs are HS256). Previously suppressed as a bare flag list;
  now each carries a written reachability argument and a dated review.
- **L3** — 98 integration tests excluded (PH3.1 / PH2.6 own the conversion).
- **L4** — no frontend build/lint/test job (PH3.3).
- **L5** — no coverage measurement; needs `pytest-cov` pinned into
  `requirements-dev.txt`.
- **L6** — **branch protection is not configured.** Every gate in this sprint is
  advisory until `main` requires it; a red pipeline can still be merged today.
  PH2.5 owns it, and the aggregate job names (`backend-ci`, `docker-build`,
  `dependency-audit`, `codeql`) exist so that configuration never needs to
  change again.
- **L7-L10** — no PR template, actions pinned by tag rather than commit SHA, no
  image vulnerability scan. Full table in `docs/deployment/GITHUB_ACTIONS.md`
  §13.

---

# Sprint PH2.3 — Production Secrets Management — 2026-07-22

**Credentials no longer have to be plaintext environment variables. `security/
secrets.py` gained a source-resolution layer — Docker/Swarm/Kubernetes secrets,
the `<NAME>_FILE` convention, and plaintext env as the development fallback —
applied through one precedence order to every variable, materialized into
`os.environ` once at boot. No application logic changed, no call site changed,
and no existing deployment breaks: the migration is opt-in and per-secret.**

> **Design note — why the loader hydrates `os.environ` instead of introducing an
> accessor.** About thirty modules already read configuration through call-time
> `os.environ.get(...)` resolvers. Routing them all through a new accessor would
> have meant a thirty-file refactor of security-critical code, and would have
> created a rule that a future module can forget — the first one that forgets it
> silently loses file support. Resolving centrally and writing the result into
> the environment *before any of them is imported* gives every consumer file
> support for free, keeps exactly one place that knows how to find a secret, and
> — because those resolvers read at call time rather than capturing at import —
> is what makes in-place rotation propagate to live code. The honest cost: after
> hydration a secret is in the process environment, so `/proc/<pid>/environ`
> inside the container still shows it. What goes away is the far larger
> host-side surface — `docker inspect`, container metadata, child-process
> inheritance. Recorded as limitation L1.

Added

- **Source resolution in `backend/security/secrets.py`** — `resolve_all()` /
  `resolve_secret()` apply one precedence order to every variable: (1) a
  `<NAME>_FILE` pointer, (2) a discovered `$SECRETS_DIR/<name>` mount, (3) a
  plaintext environment variable. `load_secrets()` materializes the result into
  `os.environ`; `validate_config()` calls it first, so validation and the running
  application can never see different values. Two properties are load-bearing:
  **an unreadable pointer never falls back to the plaintext variable** (the
  silent downgrade is exactly how a rotation appears to succeed while the old key
  stays in use), and **two sources for one secret is a boot error, not a merge**
  (two sources means two owners; guessing lets a rotated value be shadowed by a
  stale one). Discovery checks both `JWT_SECRET` and `jwt_secret` because the
  ecosystem is split — Compose/Swarm names are conventionally lowercase,
  Kubernetes keys usually are not — and is limited to registered names so a stray
  file in the mount cannot invent an environment variable.
- **`reload_secrets()`** — re-reads file sources and reports what changed by
  keyed-HMAC fingerprint, never by value. Works because rotated Docker configs
  and Kubernetes projected volumes rewrite the *same path* in place. Also drops a
  revoked secret from `os.environ` rather than leaving the loader's own stale
  write behind: a deleted secret must stop working. Nothing calls it
  automatically yet (L5).
- **`docker-compose.secrets.yml`** — an opt-in overlay delivering `mongo_url`,
  `redis_url`, `jwt_secret` and the MongoDB root credentials as file-mounted
  Docker secrets. Applied over `-f docker-compose.yml` (the explicit production
  path, not the dev default). Retracts the base file's plaintext values with an
  explicit empty string rather than null: `KEY: ~` means "inherit from the
  invoking shell", so an operator with `MONGO_URL` exported in their terminal
  would have it injected; `KEY: ""` is unconditional and identical on every
  machine. Verified against a hostile host environment.
- **`secrets/generate.sh`** + `secrets/README.md` + a deny-by-default
  `secrets/.gitignore` — generates 48-byte CSPRNG values at `chmod 600` (umask
  set *before* the write, never tightened after), composes `mongo_url`/`redis_url`
  from the passwords it just wrote so a URI and its credential cannot drift, and
  emits a real Fernet key for `BROKER_TOKEN_KEY`. It deliberately **refuses to
  invent third-party credentials** — a placeholder Anthropic key is
  indistinguishable from a real one to everything downstream. `--check` and
  `--rotate` modes.
- **`docs/deployment/SECRETS.md`** — the mechanism: architecture diagram,
  precedence table, the full validation matrix, workflows for
  development/Compose/Docker-Secrets, per-secret rotation blast radius, the
  Swarm/Kubernetes/Vault migration paths (documented, not implemented), seven
  known limitations, and a troubleshooting table.

Changed

- **Validation now covers credential *shape*, not only presence and length**
  (the sprint brief's Mongo/Redis/OAuth/API-key/encryption-key requirements).
  New in production: `MONGO_URL` must carry `user:password` (a credential-free
  URI means the database is unauthenticated or every query fails auth — the 2017
  ransom-wave configuration), `REDIS_URL` must carry a password (Redis has no
  user model, and `CONFIG SET dir` + `SAVE` makes an open instance an RCE
  primitive, not merely a cache leak), and provider key shapes are warned on.
  New in **every** environment: `BROKER_TOKEN_KEY` must be a valid Fernet key —
  an invalid one does not fail at boot, it fails the first time a user connects a
  broker, weeks later.
- **Low-entropy secret detection** (`looks_weak`) — the length check is gameable:
  `aaaaaaaa…` clears "≥ 32 characters" and does not survive ten seconds of
  offline attack. Rules are deliberately conservative (< 8 chars, ≤ 4 distinct
  characters, < 8 distinct at ≥ 16 chars, keyboard/digit runs) because a false
  positive blocks a production boot; a real `token_urlsafe(48)` clears all of
  them by a wide margin. Error in production, warning in development.
- **Delivery-posture reporting** — a *sensitive* secret arriving as plaintext is
  a warning at every production boot, listing exactly which ones. Development is
  deliberately not nagged: a warning that fires on every laptop boot is one
  nobody reads in production either. `REQUIRE_FILE_SECRETS=true` promotes it to a
  boot error, so a completed migration cannot silently regress.
- `ConfigReport` gained `sources` / `from_file()` / `from_env()`; the startup
  summary now reports `file-backed=N plaintext=N`. `docker/entrypoint.sh` prints
  the file-backed names — names only, never values.
- `server.py` — the boot comment now documents that hydration must precede every
  other import, and why. No behavioural change beyond what `validate_config()`
  now does.
- `production.env.example`, `compose.env.example`, `.claude/SECRETS.md`,
  `docs/deployment/README.md`, `docs/deployment/DOCKER_COMPOSE.md` (L2/L3 and §11
  corrected), `backend/security/__init__.py`, `backend/.env.example`
  (regenerated).

Fixed (found during verification, in this sprint's own code)

- **A `_FILE` pointer aimed inside `$SECRETS_DIR` conflicted with itself.** The
  first end-to-end run against a real mount failed: `JWT_SECRET_FILE=/run/secrets/
  jwt_secret` names precisely the path discovery scans, so the pointer and the
  discovery were counted as two rival sources and the boot was refused — meaning
  the configuration this sprint documents would have failed on every deploy.
  Comparing paths would still have been wrong on a case-insensitive filesystem
  (macOS, Windows), where `JWT_SECRET` and `jwt_secret` are one file under two
  names. Fixed by having a successful pointer *suppress* discovery: an explicit
  instruction leaves nothing to disambiguate. The conflict rule keeps its teeth
  where ambiguity is real — a file source competing with a plaintext env var.

Testing

- `backend/tests/test_secret_loading.py` — **68 new hermetic tests** covering
  every item on the sprint's testing checklist: env loading, Docker secret
  discovery (lowercase and exact), the `_FILE` convention, precedence, conflicts,
  missing/empty/oversized/binary/directory sources, placeholder and weak-value
  rejection, production boot failure, development fallback, rotation, and four
  tests asserting that no error message, summary line or `repr` ever contains a
  secret value. File reads go through an injected reader; the cases that are
  specifically *about* the filesystem use `tmp_path` and the real reader.
- `backend/tests/test_secrets.py` — `base_prod_env` now uses a credentialed
  `MONGO_URL`, since a credential-free one is a production error under the new
  rule. 38 tests, unchanged otherwise.
- Full hermetic backend suite: **694 passed, 1 failed** — the failure is the
  documented pre-existing `test_trading_engine::test_run_cycle_trails_and_books_targets`
  (unrelated, owned by PH3.1). Baseline was 626 passed with the same single
  failure.
- End-to-end verification against a real Docker-style mount: secrets resolve,
  `security.jwt` (an unmodified consumer) reads the file-backed value, no secret
  appears in the `docker inspect` view, in-place rotation propagates to live code,
  and `REQUIRE_FILE_SECRETS=true` refuses the boot with a value-free message.
- `docker compose -f docker-compose.yml -f docker-compose.secrets.yml config`
  validates; retraction verified against a shell exporting hostile values.

Known limitations

- **L1** — after hydration, secrets are in the process environment (by design;
  see the design note above).
- **L2** — MongoDB's *app-user* credentials remain plaintext env vars.
  `docker/mongodb/init-app-user.js` runs under `mongosh` and can only reach
  `process.env`. Bounded exposure: consumed only on first initialization of an
  empty volume, and the account it creates holds `readWrite` on one database. The
  **root** password is fully file-backed.
- **L3** — Redis's password is only partially covered. The official image has no
  `_FILE` support; the overlay's `sh -c` wrapper removes it from `docker
  inspect`'s `Cmd` but leaves it in `redis-server`'s argv inside that container,
  and `REDISCLI_AUTH` must stay an env var because the healthcheck runs as a bare
  exec. Closing both needs a generated `redis.conf`.
- **L4** — rotating `BROKER_TOKEN_KEY` is destructive: no re-encryption migration
  exists, so stored broker tokens become undecryptable.
- **L5** — no automatic reload trigger. `reload_secrets()` exists and is tested,
  but nothing calls it; rotation in practice still means restart.
- **L6** — under plain Compose the secret files are plaintext on the host disk.
  Inherent to file-based Compose secrets; Swarm removes it with no file change.
- **L7** — no CI assertion that nothing under `secrets/` was force-added. Belongs
  in PH2.5.

Not in scope (unchanged, per the sprint brief): Vault, AWS/Azure/GCP secret
managers, Kubernetes manifests, CI/CD, authentication, business logic, trading,
AI. PH2.2's limitation **L2** (in-container hot reload) also remains open — this
sprint sharpened rather than resolved it, since a bind-mounted `.env` would now
be a competing source the loader refuses to boot on.

---

# Sprint PH2.2 — Production Docker Compose — 2026-07-22

**The backend stack now starts with one command. PH2.1 produced a deployable
image; PH2.2 turns it into a running system — backend, MongoDB and Redis, with
network segmentation, named volumes, health-gated startup ordering and no
credential of any kind hardcoded. Orchestration only: no CI, no CD, no
Kubernetes, no reverse proxy, no TLS, and no application code changed.**

> **Sprint numbering note.** `PRODUCTION_ROADMAP.md` v1.2 assigns PH2.2 to the
> *frontend* production Dockerfile and PH2.3 to the compose split. The sprint as
> commissioned re-sequenced these: compose orchestration was pulled forward to
> PH2.2 and secrets management became PH2.3. The roadmap document has not been
> renumbered — the dependency graph is unchanged and the frontend image sprint
> is simply still outstanding. See "Known limitations" L6.

Added

- `docker-compose.yml` (replaces the previous file) — the **production-shaped
  base stack**. Three services and nothing else: `backend` (the PH2.1 image,
  built from `./backend` with the OCI provenance build args — build logic is
  *not* duplicated, the Dockerfile stays the single authority), `mongo` and
  `redis`. Explicit project `name: stockassist` so volume and container names do
  not shift with the checkout directory. Cross-cutting policy is defined once
  through YAML anchors: `restart: unless-stopped`, `no-new-privileges:true`, and
  bounded json-file logging (10 MB × 3 — the default driver is unbounded, and a
  full disk takes down every container on the host, not just the noisy one).
  Deliberately contains **no `healthcheck:` block for the backend**: the image
  already declares one, and restating it here would fork the definition so that
  `docker run` and `docker compose up` probe the same image differently.
  `stop_grace_period: 30s` because the entrypoint gives uvicorn 20s to drain and
  Docker's default grace period is 10s — without it every `down` would `SIGKILL`
  the server mid-drain. Backend port publishes to **127.0.0.1 by default**:
  Docker writes published ports into the `DOCKER-USER` iptables chain, which is
  evaluated *before* ufw/firewalld, so a `0.0.0.0` bind on a cloud VM is
  internet-reachable regardless of the host firewall.
- `docker-compose.override.yml` — the **development overlay**, auto-merged by
  Compose so `docker compose up` is the developer path and
  `-f docker-compose.yml` is the explicit production path (base = strict,
  overlay = relaxed; forgetting a flag can never *add* a database browser to a
  production stack). Adds Mongo Express, Redis Insight (`--profile tools`), n8n
  (`--profile automation`), loopback-published database ports and
  `APP_ENV=development`. Deliberately **no source bind mount and no
  `--reload`** — mounting `./backend` over `/app` would reintroduce
  `backend/.env` inside the container, and `server.py` calls
  `load_dotenv(override=True)`, so that file would silently override every
  variable Compose injects.
- `docker/mongodb/init-app-user.js` — first-boot provisioning of a
  **least-privilege application account** (`readWrite` on the application
  database only). The mongo image creates a *cluster root* user; handing those
  credentials to the application would mean an injection or a leaked container
  environment carries the ability to drop every database and create users. The
  root password never enters the backend container. Verified: the app user is
  denied `admin` reads, `createUser` and `dropDatabase`, and `listDatabases`
  returns only its own database.
- `compose.env.example` — template for the project-root `.env` that **Compose
  itself** reads. This is the second half of a deliberate two-file split:
  infrastructure credentials (Mongo/Redis passwords, host ports, image tags) are
  handed to the specific service that needs them, while application secrets
  reach the backend through `production.env`. A least-privilege boundary and a
  rotation boundary, not bookkeeping.
- `docs/deployment/DOCKER_COMPOSE.md` — full stack documentation: architecture
  and network diagrams, per-service rationale, network/volume design, the
  environment split, operator guide, measured startup timings, a 16-point
  verification table, seven known limitations, a troubleshooting matrix, and the
  handoff to PH2.3.

Changed

- `production.env.example` — documents the PH2.2 two-file model and records that
  `MONGO_URL` and `REDIS_URL` are **overridden by Compose** (a service's
  `environment:` outranks its `env_file`), so a stale value cannot break the
  stack's own wiring.
- `docs/deployment/README.md` — indexes the new document and both env templates.

Security findings closed or recorded

- **MongoDB now requires authentication.** The previous compose file ran mongo
  with no credentials and published 27017 on `0.0.0.0` — the exact configuration
  behind the 2017–2020 MongoDB ransom wave. The production stack now enables
  `--auth`, publishes no host port at all, and sits on an `internal: true`
  network with no route to or from the internet.
- **Redis is password-protected.** An unauthenticated Redis is a remote code
  execution primitive, not merely a data leak (`CONFIG SET dir` + `SAVE` writes
  arbitrary files). Verified: unauthenticated commands are refused with `NOAUTH`.
- **n8n basic auth was already dead.** The previous compose file set
  `N8N_BASIC_AUTH_ACTIVE` / `_USER` / `_PASSWORD`; those variables were removed
  upstream in n8n 1.0 and are silently ignored by 1.70. Verified during this
  sprint: with all three set, `GET http://127.0.0.1:5678/` answered **200 with
  no credentials**. They are not carried forward — a control that does nothing
  is worse than a documented absence. Access now rests on the loopback-only
  binding plus n8n's own owner-account setup, both documented.
- **`internal: true` silently disables port publishing.** A container attached
  only to an internal network accepts a `ports:` entry, records the binding, and
  never listens. Found while verifying the overlay; fixed by attaching mongo and
  redis to `edge` in the development overlay only.

Verified

- 16 checks, all passing: health-gated startup ordering; least-privilege
  database access (5 probes); network egress isolation (data tier has none, edge
  tier does); no host ports for the databases in the production stack; named
  volumes surviving a full `down` + `up` (14 collections, probe document, Redis
  key); restart policy and AOF recovery after a Redis crash; and three
  fail-closed paths — missing `production.env`, missing `REDIS_PASSWORD`, and a
  weak `JWT_SECRET` under `APP_ENV=production` (backend exits 1, restart-loops,
  never reports healthy, never serves traffic).
- Timings (Apple silicon, image cached): cold start to all-healthy **13–32 s**,
  warm start **12–14 s**, development stack with tools **16 s**, graceful
  `down` **2 s**.

Known limitations

- **L1** — the Redis pub/sub listener stops after ~3 s
  (`backend/services/cache.py:47` shares one client with `socket_timeout=3`,
  and `pubsub.listen()` blocks longer by design). A pre-existing application
  defect that was unreachable until this sprint put a Redis in the stack. No
  functional regression at `WEB_CONCURRENCY=1` with one replica — the in-process
  event bus carries every event and the cache path works normally. The fix
  belongs to **PH2.8**, which owns cross-process fan-out and can test it.
- **L2** — no in-container hot reload (PH2.3). **L3** — secrets are plaintext on
  disk and visible in `docker inspect` (PH2.3). **L4** — single-node MongoDB, no
  replica set, so no transactions or change streams. **L5** — no automated
  backups (PH2.10). **L6** — the frontend is not in the stack: it has no
  Dockerfile, and the service the previous compose file declared referenced
  `frontend/Dockerfile`, which has never existed in this repository, so it could
  never have built. **L7** — `read_only: true` is not enabled for the backend
  (data-science dependencies write to a `$HOME` cache on first use).

---

# Sprint PH2.1 — Backend Production Dockerfile — 2026-07-22

**First PH2 sprint. The backend is now a reproducible, non-root, fail-closed
container image. This is the deployment primitive every later PH2 sprint builds
on: Compose (PH2.3), CI image builds (PH2.6), and CD (PH2.7) all consume it.
Backend containerization only — no Compose, no CI, no Redis, no Kubernetes.**

Added

- `backend/Dockerfile` — two-stage production build.
  **Stage 1 (builder):** `python:3.11-slim-bookworm` + `build-essential`,
  installs `requirements.txt` (runtime deps only — `requirements-dev.txt` is
  never installed, per PH1.11/M14) into a relocatable venv at `/opt/venv`, then
  prunes bundled library test suites and `pip`.
  **Stage 2 (runtime):** the same slim base with **no compiler, no headers, no
  package installer**; copies `/opt/venv` and the application source; creates a
  dedicated `appuser` (fixed uid/gid **10001**, `nologin`, no password); source
  and venv stay **root-owned** so the running process can read and execute its
  code but **cannot modify it**; OCI provenance labels (`APP_VERSION`,
  `VCS_REF`, `BUILD_DATE` build args); `EXPOSE 8000`; `HEALTHCHECK`;
  exec-form `ENTRYPOINT` with empty `CMD`.
  `slim` over `alpine` is deliberate: musl invalidates the manylinux wheels for
  pandas/numpy/cryptography/grpcio, turning a ~1-minute dependency install into
  15–30 minutes of source compilation for a slower runtime.
- `backend/.dockerignore` — the single auditable boundary between repo and
  image. Excludes **every** dotenv file, `venv/`, `tests/`, `.git/`, caches and
  build output. The dotenv exclusion is a correctness control as much as a
  secret-leak control: `server.py` calls `load_dotenv(..., override=True)`, so a
  `.env` inside the image would silently **override the environment variables
  injected by the orchestrator at runtime**. Reduces the shipped build context
  from **620 MB → 2.5 MB**.
- `backend/docker/entrypoint.sh` — PID 1. POSIX `sh` (the runtime image has no
  bash). Reads all configuration from the environment with production-safe
  defaults; performs structural validation (`APP_ENV`, `PORT`,
  `WEB_CONCURRENCY`); then delegates full validation to the application's own
  authority, `security/secrets.py` `validate_config()` (PH1.8), printing its
  aggregated, value-free report and **exiting 1** rather than starting
  misconfigured — no second source of truth in shell. Runs any executable in
  `docker/pre-start.d/` (the `postgres`/`nginx` convention) as a working
  extension point for future migrations, then **`exec`s** uvicorn so the server
  becomes PID 1 and receives `SIGTERM` directly. Explicit-args mode
  (`docker run <image> <cmd>`) runs one-shot jobs against the same validated
  configuration. Launches with `--no-server-header`, `--proxy-headers`,
  `--forwarded-allow-ips`, `--timeout-graceful-shutdown`; **never** `--reload`.
- `backend/docker/healthcheck.sh` — liveness probe honouring Docker's exit-code
  contract (0 healthy / 1 unhealthy, nothing else). Written against the Python
  **standard library** specifically so `curl`/`wget` never enter the runtime
  image. Probes `127.0.0.1` (measures the application, not the network path to
  it) at `/api` — unauthenticated, already rate-limit-exempt in
  `security/rate_limit.py`, and **dependency-free by design**: a Mongo round-trip
  in a *liveness* probe would mark every replica unhealthy during a database
  blip and restart the whole fleet. Validates the response body, not just the
  status code.
- `production.env.example` (repo root) — operator-facing runtime environment
  template; the deployment-time counterpart to the developer-facing,
  registry-generated `backend/.env.example`. Placeholders only. Documents the
  container-runtime variables, the required core set, and — explicitly, commented
  out — the variables that must **not** appear in production.
- `docs/deployment/DOCKER.md` — full container architecture document: rationale,
  diagrams, multi-stage strategy, layer-caching and size analysis with measured
  numbers, security decisions, runtime configuration, health-check design,
  operator guide, known limitations, PH2.2–PH2.10 forward path, and a
  troubleshooting matrix.
- `docs/deployment/README.md`, and a `deployment/` entry in `docs/README.md`.

Changed

- `.claude/PRODUCTION_ROADMAP.md`, `.claude/TASK.md` — PH2.1 marked COMPLETE
  with delivered/met/missed detail.

Verification

- Cold build **2m 44s** (first build, incl. base-image pull) / **3m 21s**
  (`--no-cache`); **4.5s** rebuild after a source-only change — the dependency
  layer cache holds, which is the whole point of `COPY requirements.txt` before
  `COPY . .`. Final image **1.03 GB**, `/app` 2.8 MB.
- Boots healthy against live MongoDB in **2.5s**; Docker reports `healthy`.
- Fail-closed proven: missing secrets, placeholder-looking secrets, invalid
  `APP_ENV` and non-numeric `PORT` each abort with a clean operator-facing
  message and **exit 1**.
- Non-root confirmed (`uid=10001`); the process **cannot** write `/app` or
  `/opt/venv`; `pip`, `curl`, `wget` and `gcc` all absent; no `.env`, `tests/`
  or `venv/` in the image (`/app` totals 2.8 MB).
- Graceful shutdown: `docker stop` → **exit 0 in 1.2s**, FastAPI `shutdown` event
  ran (scheduler stopped, Mongo client closed) — signals reach PID 1.
- Runs healthy under `--read-only --tmpfs /tmp --cap-drop=ALL
  --security-opt no-new-privileges:true`.
- PH1.4 security headers verified intact through the container; no `server:`
  header leaked.

Known limitations

- **Image is 1.03 GB against the roadmap's < 400 MB target.** Every image-level
  lever was applied and measured (multi-stage −300 MB, bundled test suites
  −66 MB, `pip` −16 MB; `strip` measured at **0 MB** because manylinux wheels
  ship pre-stripped, and `--no-compile` was rejected because it trades 158 MB of
  image for a permanent per-start compile cost). The residual is the dependency
  set, not the Dockerfile: `googleapiclient` (97 MB), `litellm` (55 MB),
  `boto3`/`botocore`/`s3transfer` (~32 MB), `stripe` (24 MB) and `s5cmd` (15 MB)
  are **imported by zero application code** — ≈220 MB, plus the CVE surface.
  `s5cmd` in particular is a standalone S3 CLI, an ideal exfiltration tool.
  **A dependency-pruning sprint is the recommended follow-up.**
- **`pytz` is missing from `requirements.txt`** — `services/market_engine/`
  `validator.py` imports it, so the Market Engine fails to initialize
  (`Market Engine init error: No module named 'pytz'`). **Pre-existing and
  equally broken outside Docker**; surfaced by the clean-room build. Left unfixed
  as an application dependency change outside this sprint's scope. Should be
  fixed before any production deploy.
- **`WEB_CONCURRENCY` must remain 1** until PH2.8: the in-process APScheduler,
  heartbeat engine and in-memory WebSocket registry are not multi-process safe
  (N workers = N duplicate scheduled jobs; broadcasts reach only one worker's
  clients). Scale by replicas, not workers. The entrypoint warns loudly.
- Base image pinned by **tag**, not digest (picks up Debian patches on rebuild;
  digest pinning lands with image scanning in PH2.6). No vulnerability scanning
  yet (PH2.6). Built and verified on **linux/arm64** only — multi-arch in PH2.6.
- The existing root `docker-compose.yml` remains **development-oriented** (bind
  mounts, `--reload`, overrides the entrypoint) and is intentionally untouched;
  splitting dev/prod is PH2.3.

---

# Sprint PH1.12 — Security Certification (PHASE 1 EXIT GATE) — 2026-07-22

**Production Hardening PH1.12 complete — and with it, PH1.11. This is the final
Phase 1 sprint: the three PH1.11 verification residuals (F-1, F-2, F-3) are
fixed, the security verification checklist is executed, the security categories
are re-scored over the ≥ 8.0 gate, and Phase 1 (Production Security Hardening) is
formally CERTIFIED COMPLETE. Overall production deployment remains NO-GO — blocked
now by infrastructure (PH2) and QA (PH3), not by security.**

Added

- `backend/security/roles.py` (**F-1** — privilege escalation) — the single
  source of truth for the role taxonomy and who may grant each role.
  `ASSIGNABLE_ROLES` allowlists every legitimate `users.role` value;
  `validate_role_assignment(new_role, actor_role)` rejects unknown roles (400)
  and permits the admin-tier roles (`admin`, `super_admin`) **only** for a
  `super_admin` actor (403 otherwise). Closes the escalation path where
  `PUT /api/admin/users/{id}` accepted `role` as an unchecked passthrough field
  (any admin could promote anyone — including themselves — to admin/super_admin).
- `backend/security/identifiers.py` (**F-2** — unhandled id parsing) —
  `parse_object_id(value, resource)`, the single boundary where an untrusted
  identifier becomes a `bson.ObjectId`. Malformed input returns a clean **400**
  ("Invalid `<resource>` id", never echoing the value) instead of the previous
  uncaught `InvalidId` → HTTP **500**. Also fixes the surprising `ObjectId(None)`
  → *new random id* behavior by rejecting non-strings.
- `backend/tests/test_roles.py` + `backend/tests/test_identifiers.py` — **48
  hermetic tests**: role allowlist/least-privilege unit coverage plus end-to-end
  regression proving an `admin` cannot escalate a user or self-promote (403,
  stored role unchanged), a `super_admin` can, plan roles stay grantable by any
  admin, and a malformed user id returns 400 (not 500); id parsing across
  valid/passthrough/malformed/non-string/no-echo cases.
- `.github/dependabot.yml` (**F-3**) — weekly dependency-update PRs for `pip`
  (`/backend`), `npm` (`/frontend`), and `github-actions`; `docker` staged
  (commented) for PH2.1/2.2. Non-security minor/patch bumps grouped per
  ecosystem; security updates arrive as their own PRs.
- `backend/requirements-dev.txt` (**F-3 / finding M14**) — dev/CI tooling
  (`pytest`, `black`, `flake8`, `isort`, `mypy` + their exclusively-dev transitive
  deps, each verified dev-only via `pip show … Required-by`) split out of the
  runtime set; begins with `-r requirements.txt`. The production image now ships
  no dev tooling.
- `docs/security/PH1_CERTIFICATION.md` — the PH1 Security Certification Report:
  sprint inventory, F-1/F-2/F-3 detail, controls-verification matrix, OWASP Top 10
  posture, test summary, re-score, known limitations, and the signed certification
  decision.

Changed

- `backend/server.py` — wired in `parse_object_id` at every user-facing id
  boundary (admin user/ticket/feature-flag/announcement editors; trade
  update/exit/coaching/live-tip, trade-review, notification mark-read, paper
  close) and `validate_role_assignment` in `admin_update_user`. Trusted ids
  (verified JWT `sub`, `_id` read back from Mongo) intentionally stay raw.
- `.github/workflows/security-audit.yml` — audits **both** requirements files and
  runs `pip check` on the **runtime-only** install (which also proves the M14
  split is complete).
- `backend/requirements.txt` — dev tooling removed (moved to `requirements-dev.txt`).
- `backend/security/__init__.py` — documents the new `roles` and `identifiers`
  tenants.
- `.claude/SECRETS.md §7` — runtime/dev split, Dependabot cadence, and the
  severity triage SLA (critical blocks release · high 7d · medium 30d · low 90d).
- `.claude/TESTING.md` — new "Dependency Vulnerability Triage" section mirroring
  the SLA.
- `.claude/PRODUCTION_HARDENING.md` — §17 Security row **signed off**; readiness
  re-score (composite 4.2 → ~6.4; authn 9.0, API sec 8.5, secrets 8.5,
  observability 7.0).
- `.claude/PRODUCTION_ROADMAP.md` — PH1.11 and PH1.12 marked COMPLETE.
- `PRODUCTION_READINESS_REPORT.md` — PH1.12 update prepended (release decision,
  blocker status, final architecture, operational prerequisites, deployment/
  rollback/backup/recovery checklists); Sprint-12 baseline preserved below.
- `.claude/TASK.md` — PH1.11/PH1.12 marked complete; **Phase 1 marked COMPLETE**;
  next phase set to PH2.1.

Security posture / re-score

- No open **critical or high** security findings. F-1 (high) / F-2 (medium) /
  F-3 (medium) closed.
- **Authentication & Authorization 2.0 → 9.0**; **API & Transport Security
  3.0 → 8.5** — both clear the PH1.12 ≥ 8.0 exit gate.
- Verification checklist: no debug mode, no auth backdoors, no hardcoded/test
  secrets; cookies (HttpOnly always, Secure forced in prod) / CORS (no
  wildcard-with-credentials) / HSTS+CSP headers / CSRF / rate limiting / audit
  logging / fail-closed boot config all confirmed.

Tests

- Hermetic backend suite: **626 passed, 1 failed** — the one failure
  (`test_trading_engine::test_run_cycle_trails_and_books_targets`) is a
  pre-existing, documented engine-math test unrelated to this sprint (PH3.1).
- Legacy `requests`-based integration files (`test_backend.py`, `test_phase*.py`)
  require a live dev server; their failures/errors in a full run are environmental
  (ConnectionError), not regressions. Hermetic migration is PH3.1.

Decision

> **Phase 1 (Production Security Hardening): CERTIFIED COMPLETE.** Overall
> production deployment: **NO-GO** pending Phase 2 (Infrastructure & DevOps) and
> Phase 3 (Quality Assurance). Recommend transition to **PH2.1 — Backend
> Production Dockerfile**.

Deferred within PH1 (non-blocking, tracked): PH1.9 Real-Time/WebSocket Security
(R-15) and PH1.10b Admin Hardening & Session Management.

---

# Sprint PH1.10 — Audit Logging & Security Monitoring — 2026-07-22

**Production Hardening PH1.10 complete. Security-event observability is now
centralized: every security-relevant event flows through one module with one
taxonomy, one redacted schema, and one pluggable, SIEM-ready sink. Secrets can
never reach an audit log, and audit logging can never break a security flow.
Zero frontend, business-logic, JWT, or OAuth behavior change.**

Before this sprint there was no centralized audit log: security writes were
scattered across three ad-hoc writers with three record shapes (`log_auth_event`
→ `security_audit_logs`, `log_admin_action` → `admin_audit_logs`, broker `_audit`
→ `audit_logs`), each making its own "which fields are safe to log" judgement,
with no shared severity axis and no structured event model — an incident was a
cross-collection archaeology project.

Added

- `backend/security/audit.py` — the single source of truth for security-event
  logging. A **closed event taxonomy** (authentication / identity / session /
  security / administration) mapping every event to a `category` + default
  `severity` (info / notice / warning / critical); an unknown event fails safe to
  `security`/`warning`. A **versioned structured schema** (`schema_version=1`):
  event, category, severity, outcome, email, user_id, session_id, reason, ip,
  user_agent, request_id, target, redacted `details`, timestamp. **Recursive
  secret redaction** blanks any sensitive-keyed value (password, token, secret,
  authorization, code, state, csrf, hash, api_key, cookie, signature, …) before
  storage — defense-in-depth over careful call sites; depth-bounded against
  cyclic payloads. A **pluggable `AuditSink`** interface with a default composite
  of `MongoAuditSink` (durable `security_audit_logs`) + `LoggingAuditSink` (one
  JSON line/event — the SIEM/log-shipper seam); each sink isolated so one outage
  never starves the other. A **fail-safe `AuditLogger`**: emitting can never
  raise into the caller. Lazy DB provider (`audit.configure(lambda: db)`) so the
  live handle and the test `FakeDB` are both honored.
- `backend/tests/test_audit.py` — 20 hermetic tests: taxonomy classification,
  full-schema records, redaction (flat / nested / listed / depth-bounded),
  Mongo + logging + composite sinks, fail-safe emission, and live-app
  integration (login ±, registration, logout→session-revocation, invalid-JWT,
  refresh rotation, token-replay→CRITICAL, rate-limit trigger), plus a
  backward-compatibility test pinning the legacy `log_auth_event` record shape.

Changed

- `backend/server.py` — `log_auth_event` is now a thin backward-compatible
  facade over `security.audit` (identical signature; historical record fields are
  a strict subset of the new schema, so every existing caller, query, index, and
  test is unaffected). New fail-safe, control-flow-neutral audit hooks added to
  the auth surface: `login_success` / `login_failure`, `registration`,
  `session_created` (in `_issue_session`, covering login/register/OAuth),
  `logout` + `session_revoked`, `logout_all`, `refresh_rotation`,
  `token_replay_detected` vs. `invalid_refresh`, and `invalid_jwt` (only for
  genuinely tampered tokens — ordinary expiry is left unaudited to avoid noise).
  Startup extends `security_audit_logs` indexes (`category`, `severity`,
  `user_id`, `session_id`).
- `backend/security/csrf.py` — the middleware audits `csrf_validation_failure`
  before the 403 (lazy import, fail-safe).
- `backend/security/rate_limit.py` — `_trip` audits `rate_limit_triggered` at the
  single choke point covering inline and middleware limiters (lazy import,
  fail-safe).

Documentation

- `SECURITY_ARCHITECTURE.md` — new **§31b** (audit taxonomy, schema, redaction,
  sinks, fail-safe, instrumentation points); §32 plan, §33 rule 4, Architecture
  Summary, and Implementation Status updated.
- `SECURITY.md` — Audit Logging section expanded with the centralized model;
  Monitoring section gains operational alerting + retention guidance.
- `PRODUCTION_HARDENING.md`, `PRODUCTION_ROADMAP.md`, `TASK.md` — PH1.10 marked
  complete with the audit-event matrix.

Tests

- Full hermetic backend suite: **578 passed** (1 pre-existing, unrelated
  `test_trading_engine` failure deselected). All security suites (auth, OAuth,
  CSRF, rate-limit, JWT/sessions, recovery, cookies, headers) green — no
  regression from the facade migration.

---

# Sprint PH1.9 — Secrets & Supply Chain Security — 2026-07-22

**Production Hardening PH1.9 complete. Configuration is now centralized and
validated: a misconfigured or weakly-configured production refuses to boot, the
dependency set is fully pinned and continuously audited, and no hard-coded
secret remains in the repository. Zero frontend or business-logic change.**

This sprint delivers the roadmap's PH1.8 (Secrets & Environment Hardening)
content plus the supply-chain core of PH1.11, executed under the "PH1.9" label
(Identity Recovery had consumed the PH1.8 slot). Before it, the app read ~40 env
vars ad hoc with no boot-time validation (a missing `JWT_SECRET` only surfaced
at the first token operation), `docker-compose.yml` shipped a weak `JWT_SECRET`
fallback and a hard-coded n8n password, `.env.example` was git-ignored (so no
shareable template existed), and four dependencies floated on `>=` bounds.

Added

- `backend/security/secrets.py` — the single source of truth for the
  configuration surface. `SECRET_REGISTRY` declares every variable (category,
  `sensitive`, `required_in` environments, `min_length`, example). Boot-time
  `validate_config()` **fails closed**: it aggregates every problem into one
  value-free error and is called from `server.py` *before* the Mongo client or
  any router. Severity is environment-aware — the core trio (`MONGO_URL`,
  `DB_NAME`, `JWT_SECRET`) is fatal everywhere; production additionally makes
  fatal any missing required secret, a signing key < 32 chars, any
  placeholder/weak value, a half-configured OAuth or broker pair,
  `ENABLE_AUTO_LOGIN=true`, a weak `ADMIN_PASSWORD`, and the absence of any AI
  provider. No secret value is ever logged (`redact()`, presence-only summary).
  Reuses `security.cookies.is_production` so environment semantics never drift.
- `backend/.env.example` + `frontend/.env.example` — committed, placeholder-only
  templates. The backend template is **generated** from the registry by
  `backend/scripts/generate_env_example.py` (with a `--check` mode CI enforces),
  so code and template can never drift.
- `.github/workflows/security-audit.yml` — `pip-audit --strict` + `pip check`
  (backend), `npm audit` (frontend), `gitleaks` history scan + a tracked-`.env`
  guard, and the `.env.example` sync check. Runs on push/PR and weekly.
- `backend/scripts/audit_dependencies.py` — local `pip check` + `pip-audit`
  runner (degrades gracefully when pip-audit isn't installed).
- `.claude/SECRETS.md` — the secrets & supply-chain runbook: inventory,
  environment strategy, rotation policy (per secret class), dependency-update
  policy, accepted-advisory backlog, and leaked-credential incident response.
- `backend/tests/test_secrets.py` — 38 hermetic tests (env-aware validation,
  core-trio enforcement, cross-field invariants, no-secret-in-output,
  registry/example-sync integrity, accessors).

Changed

- `backend/server.py` — calls `security.secrets.validate_config()` immediately
  after `load_dotenv`; logs the presence-only summary + any warnings. This is
  the only server change (additive, non-breaking).
- `backend/requirements.txt` — now **fully exact-pinned**: locked the 4 floating
  `>=` bounds (aiohappyeyeballs, psutil, anthropic, litellm) and applied 7 in-pin
  security patches (aiohttp 3.13.5→3.14.1, cryptography 48.0.0→48.0.1, httplib2
  0.31.2→0.32.0, pillow 12.2.0→12.3.0, pyasn1 0.6.3→0.6.4, pymongo 4.5.0→4.6.3,
  python-multipart 0.0.29→0.0.31). Verified to co-resolve; starlette/litellm/
  ecdsa advisories deferred (framework-locked / AI-scope / unfixed) — see
  SECRETS.md §8.
- `docker-compose.yml` — removed the weak `JWT_SECRET` fallback (now required via
  `${JWT_SECRET:?…}`) and the hard-coded n8n password `alphapartner123` (now
  required `${N8N_BASIC_AUTH_PASSWORD:?…}`); added `APP_ENV`.
- `.gitignore` — negations (`!.env.example`, `!**/.env.example`) so example
  templates are committable while every real `.env` stays ignored.
- `backend/security/__init__.py` — documents the new `security.secrets` tenant.

Security notes

- **Fail-fast, fail-closed:** `APP_ENV=production` with any missing/weak required
  variable now exits before serving a single request, with a named-variable
  error listing all problems at once.
- **No secret in git history:** verified via `git log --all -S <value>` that no
  real provider key or `JWT_SECRET` was ever committed and no `.env` was ever
  tracked. One committed value existed — the n8n dev password `alphapartner123`
  in `docker-compose.yml` (5 commits) — now externalized; low severity
  (local-only editor basic-auth), documented in SECRETS.md §9.
- **Rotation reminder:** live provider keys currently in local `.env` files have
  existed in plaintext dev files and must be rotated before production launch
  (SECRETS.md §9).

Tests

- `backend/tests/test_secrets.py` — 38/38 passing. Full backend hermetic suite
  regression-checked (the pre-existing `requests`-based integration files and
  one pre-existing `test_trading_engine` failure are unrelated and unchanged by
  this sprint). Manual verification: prod-missing-secret aborts startup;
  valid-prod loads clean; the real dev `.env` boots.

---

# Sprint PH1.8 — Identity Recovery — 2026-07-22

**Production Hardening PH1.8 complete. The identity lifecycle is now
recoverable: users can verify their email, change their password, and reset a
forgotten one — each single-use, expiring, and safe against enumeration — with
zero frontend break and no change to the JWT/CSRF/rate-limit/OAuth layers.**

Before this sprint an account, once created, had no recovery path: no email
verification, no password change, no forgotten-password reset, and no way to
force-sign-out after a credential rotation. PH1.8 closes all four as a single
reusable `backend/security/recovery.py` module composed by new `/api/auth`
endpoints.

Added

- `backend/security/recovery.py` — the single source of truth for
  identity-recovery tokens. A **signed handle backed by an authoritative
  record**: the token handed to the user is `<token_id>.<HMAC(secret,
  "prefix|purpose|user_id|token_id")>` (unforgeable, bound to exactly one user +
  one purpose), while a `recovery_tokens` document carries `issued_at` /
  `expires_at` / `used_at` so **expiry and single-use are enforced
  authoritatively**. `consume()` burns a token with an atomic compare-and-set
  (`used_at: None → now`) — replay-safe. Issuing a fresh token invalidates the
  user's outstanding unused ones of that purpose (one live link at a time).
  - Purposes & lifetimes (env-overridable): **email verification 24h**
    (`RECOVERY_VERIFY_TTL_SECONDS`), **password reset 30 min**
    (`RECOVERY_RESET_TTL_SECONDS`).
  - HMAC key: `RECOVERY_SECRET` or the required `JWT_SECRET` (domain-separated by
    a versioned prefix — no weak default, fail-closed). Never logs a token.
- New `/api/auth` endpoints (all recovery logic centralized, never inline):
  - **`POST /verify-email`** — redeem a verification token (public; single-use).
  - **`POST /verify-email/request`** — resend the verification link
    (authenticated; generic response; no-op if already verified).
  - **`POST /forgot-password`** — start reset (public; **always** a generic
    response — no email enumeration; OAuth-only accounts silently skipped).
  - **`POST /reset-password`** — reset with a token + new password (public;
    enforces the PH1.5 policy; revokes every session; stamps
    `password_changed_at`).
  - **`POST /change-password`** — authenticated; requires the **current**
    password, rejects an unchanged one, enforces the PH1.5 policy, then revokes
    every session and stamps `password_changed_at` (signed out everywhere).
- `backend/tests/test_recovery.py` (28 tests) — hermetic: token
  mint/verify/consume, single-use/replay, expiry, purpose-binding, signature
  tamper, reissue invalidation; and the full endpoint matrix (verify success /
  expired / replay, forgot-password generic response, reset single-use / expiry /
  policy / session-revocation, change-password current-password /
  unchanged-password / policy / sign-out, and the untouched register→login→me
  lifecycle).

Changed

- **User model gains email-verification status:** `email_verified` (bool),
  `email_verified_at`, `verified_by` (`"email"` | `"google"`). New
  email/password registrations start **unverified** and are emailed a
  verification link (out-of-band via `BackgroundTasks`; a slow/failed mailer
  never delays sign-up). **Login is deliberately NOT blocked on this flag** —
  backward-compatible; it is the hook a future verified-only gate flips on.
- **Google OAuth accounts are verified on creation/link.** Google already
  asserts (and we already enforce) a verified email, so a Google-native or
  Google-linked account is marked `verified_by: "google"` with no separate
  verification email; pre-PH1.8 Google accounts self-heal the flag on next login.
- **`security.csrf`** default exempt paths extended with the three *public*
  recovery entrypoints (`/verify-email`, `/forgot-password`, `/reset-password`)
  — they carry their own single-use authorization or are anonymous, so they rely
  on no ambient cookie authority. The *authenticated* recovery actions
  (`change-password`, `verify-email/request`) stay CSRF-protected for cookie
  clients.
- **`services.email_service`** gains three branded templates:
  `EMAIL_VERIFICATION`, `PASSWORD_RESET`, `PASSWORD_CHANGED` (security
  confirmation).
- **Register response** gains the additive `email_verified` field so the SPA can
  surface a "verify your email" prompt; the rest of the contract is unchanged.
- Startup creates the `recovery_tokens` indexes (`token_id` unique,
  `(user_id, purpose)`, and a TTL index on `expires_at`).

Security properties

- **No enumeration:** forgot-password and verify-email/request return an
  identical generic message whether or not the account exists; recovery reads run
  through the existing rate limiter (`PASSWORD` policy, 5 / hour).
- **Single-use + replay-safe:** every recovery token is burned atomically on
  redemption; a replayed link is a generic 400.
- **Full session invalidation:** a reset OR change revokes every refresh family
  (`SessionStore.revoke_all_for_user`) and bumps `password_changed_at`, so
  outstanding access tokens also go stale on next use — the user re-logs in
  everywhere.

Out of scope (unchanged): JWT crypto, rate limiting, CSRF enforcement model,
OAuth login, cookie/header policy, trading engine, AI, frontend.

---

# Sprint PH1.7 — CSRF Protection & Rate Limiting — 2026-07-21

**Production Hardening PH1.7 complete. The two remaining abuse-surface gaps —
an unowned CSRF token layer and login-only rate limiting — are closed, without
any frontend change or public-API break.**

Before this sprint, cross-site state-changing requests were defended only by
`SameSite=Lax` (a real baseline, but no token layer), and rate limiting was a
single inline `db.login_attempts` lockout on `/api/auth/login`. PH1.7 adds a
signed, session-bound CSRF token layer and a centralized, progressive rate
limiter with a platform-wide flooding backstop — both as reusable
`backend/security/` modules.

Added

- `backend/security/csrf.py` — the single source of truth for CSRF. A **signed
  double-submit cookie bound to the session** (OWASP pattern): a non-HttpOnly
  `csrf_token` cookie carrying `<nonce>.<HMAC(secret, "prefix|sid|nonce")>`,
  echoed by the client in `X-CSRF-Token`.
  - **`CSRFMiddleware`** enforces on a request iff it is a mutating method, a
    non-exempt path, carries **no** `Authorization: Bearer` header, and is
    cookie-authenticated. Validation = header==cookie (double-submit) **and** the
    HMAC verifying against the cookie session's `sid` (binding). Failure → **403**
    (`code: CSRF_FAILED`), fail-closed.
  - **Bearer requests exempt by construction** — the SPA's `Authorization: Bearer`
    path cannot be forged cross-site and carries no ambient cookie authority, so
    enforcement targets exactly the cookie-only attack surface. This is what makes
    the rollout require **zero frontend changes**.
  - HMAC key: `CSRF_SECRET` or the required `JWT_SECRET` (domain-separated);
    cookie `Secure`/`SameSite`/`Domain` resolved through `security.cookies`.
- `backend/security/rate_limit.py` — the single limiter. Named per-endpoint
  policies, a pluggable `RateLimitStore` interface (shipped `MongoRateLimitStore`,
  Redis-ready), fixed-window counting, and **progressive lockout** with automatic
  expiry and `Retry-After`.
  - Policies (env-overridable via `RATE_LIMIT_<NAME>`): **login 5 / 15 min** per
    `ip:account` (failures only; escalating lockout), **register 5 / hour** per IP,
    **refresh 20 / min** per session, **password 5 / hour**, **authenticated API
    120 / min** per user, **public API 60 / min** per IP.
  - **`RateLimitMiddleware`** — platform-wide flooding backstop over all `/api`
    traffic (per-user when authenticated, per-IP otherwise); emits
    `X-RateLimit-*`; a storage error fails **open** (logged) so the throttle can
    never take the API down.
- `backend/tests/test_csrf.py` (18 tests) and `backend/tests/test_rate_limit.py`
  (26 tests) — hermetic: token mint/verify/bind, every middleware
  exempt/enforce/reject path, store/limiter semantics, lockout + escalation,
  `Retry-After`, and the real auth-endpoint integrations.

Changed

- `backend/server.py`:
  - `_issue_session` and `/refresh` now plant/re-mint the CSRF cookie;
    `logout`/`logout-all` clear it.
  - `login` replaces the inline `login_attempts` block with the centralized
    limiter (`peek` → `record_failure` → `reset`); `register` and `refresh` gain
    inline limits. Observable lockout behavior is preserved byte-for-byte.
  - Middleware wired: `apply_csrf_protection` + `apply_rate_limiting` registered
    **before** CORS/headers so a 403/429 still carries CORS + security headers
    (execution order: Security Headers → CORS → Rate Limiter → CSRF → route).
  - Startup drops the `login_attempts` index; adds `rate_limits` indexes
    (compound `(key, kind, window_start)` + TTL on `expires_at`).
- `backend/security/__init__.py` — tenant index lists `csrf` and `rate_limit`.
- `backend/tests/test_password_policy.py` — two tests that asserted the internal
  `login_attempts` collection now assert the new limiter's observable behavior
  (`rate_limits`); the login-compatibility guarantees are unchanged.

Migration

- **No data migration, no API break.** The `login_attempts` collection is simply
  no longer written (a Mongo TTL/manual drop can retire it). Existing Bearer-based
  clients are unaffected by CSRF (exempt); a future cookie-only client reads the
  `csrf_token` cookie and sets `X-CSRF-Token`. All limits are env-tunable.

Threat-model rows "Cross-site state-changing request via cookie auth (CSRF
proper)", "Credential stuffing / brute force", and "Endpoint flooding / token
abuse" move to ✅ Closed. Remaining PH1 work: PH1.8 (secrets/env validator),
PH1.9 (WebSocket authorization), PH1.10–PH1.12.

---

# Sprint PH1.6 — JWT Lifecycle & Session Security — 2026-07-20

**Production Hardening PH1.6 complete. The two highest-value open authentication
risks — long-lived access tokens (R-06) and refresh-token replay — are closed.**

Before this sprint, access tokens lived 24 hours, refresh tokens never rotated
or revoked, and logout only deleted cookies (the JWTs stayed cryptographically
valid until natural expiry). A stolen token was usable for a day; a captured
refresh token for a week, undetectably. PH1.6 centralizes all JWT logic, shortens
the access token to 15 minutes, rotates refresh tokens on every use with theft
detection, and adds a durable server-side revocation store — without changing any
public API contract.

Added

- `backend/security/jwt.py` — the single source of truth for JWT issuance and
  verification (pure crypto, no FastAPI/DB). The only place a token is encoded or
  decoded.
  - **Hardened claim set on every token:** `iat`, `jti` (unique id — the handle
    the session store rotates/revokes), `aud`, `iss`, `sid` (owning session), and
    `ver` (token schema version), alongside the existing `sub`/`email`/`type`/`exp`.
  - **Strict, fail-closed verification** (`decode_token`) — validates signature,
    `exp`, `aud`, `iss`, requires every claim, and checks `type`/`ver`. Raises
    typed, framework-neutral `TokenExpired`/`TokenInvalid` (never a raw `pyjwt`
    error), which the web layer maps to a generic 401.
  - **Configurable lifetimes** — `JWT_ACCESS_TTL_SECONDS` (default **900 / 15 min**)
    and `JWT_REFRESH_TTL_SECONDS` (default **604800 / 7 days**), plus `JWT_ISSUER` /
    `JWT_AUDIENCE`. `TOKEN_VERSION` is a pinned-in-code global kill-switch.
  - **`password_changed_at` support** (`token_issued_before`) — the anchor a future
    password change / forced-logout uses to invalidate every outstanding token by
    `iat`, for both access and refresh.
- `backend/security/sessions.py` — `SessionStore`, the DB-backed session (refresh-
  token family) store. One MongoDB `sessions` document per login/device.
  - **Rotation on every refresh** — the presented refresh token is single-use; a
    new `jti` becomes current and the old one is dead.
  - **Reuse detection** — replaying an already-rotated refresh token (its `jti` no
    longer current) is treated as theft and **revokes the entire family**, so both
    attacker and victim are forced to re-login (closes refresh-replay).
  - **Revocation** — `revoke` (single session / logout) and `revoke_all_for_user`
    (logout-all-devices); durable, TTL-reaped at `expires_at`.
  - **PH1.10 groundwork** — captures `user_agent`, `ip`, created/last-used
    timestamps, and exposes `list_for_user` for the future active-sessions screen.
- `backend/tests/test_jwt_sessions.py` — 34 hermetic tests: claim set, every
  rejection path (expired / wrong-aud / wrong-iss / bad-signature / wrong-type /
  stale-version / missing-claim / garbage), rotation, reuse→family-revoke, revoke,
  revoke-all, `password_changed_at`, and the full HTTP lifecycle.
- `POST /api/auth/logout-all` — authenticated "sign out of all devices" endpoint.

Changed

- `backend/server.py` — `create_access_token`/`get_current_user`/`refresh`/`logout`
  now delegate to `security.jwt` + `security.sessions`; the inline `pyjwt`
  encode/decode and the old 24h/7d helpers are gone. Login/register/OAuth open a
  session via a shared `_issue_session` helper (captures device/IP). Refresh
  rotates **both** cookies. Startup provisions `sessions` indexes (unique
  `session_id`, `user_id`, TTL on `expires_at`). `import jwt as pyjwt` removed.
- `backend/security/__init__.py` — tenant index lists `jwt` and `sessions`.
- `backend/tests/test_cookie_security.py` — the PH1.3 test that asserted refresh
  does *not* rotate the refresh cookie (explicitly deferred to PH1.6) now asserts
  the rotated refresh cookie carries the hardened flags.

Migration

- **Clean cutover.** Strict validation rejects pre-PH1.6 tokens (no `aud`/`ver`),
  so active users re-authenticate once via the normal 401 → login flow on deploy.
  No data migration. `cookies.py` (cookie `Max-Age`) is unchanged — the access
  cookie's 24h Max-Age harmlessly outlives the 15-min JWT (expired token →
  silent refresh); aligning them is a cosmetic PH1.3-owned follow-up.

Risk R-06 closed. Threat-model rows "Stolen long-lived access token" and "Refresh
token replay" move to ✅ Closed. Note: the roadmap's placeholder `tokens.py` was
realized as two cohesive modules (`jwt.py` pure crypto + `sessions.py` stateful
store) and refresh defaults to 7 days (env-tunable to the SECURITY.md 30-day
target); both deviations are recorded in PRODUCTION_ROADMAP.md PH1.6.

---

# Sprint PH1.4b — HTTP Security Headers — 2026-07-20

**Production Hardening PH1.4b complete. The "no security headers" gap (flagged in
the PH0 audit and de-scoped from the CORS-only PH1.4) is closed.**

Before this sprint the API emitted **no** security response headers — every
response could be framed, MIME-sniffed, and leaked referrers, with no transport
pinning and no content policy. PH1.4b adds the full defensive header set in one
centralized, environment-driven place, wired *after* CORS so even CORS
preflight and rejected-origin responses carry the headers. API contracts and
payloads are unchanged; only response headers are added.

Added

- `backend/security/headers.py` — the single source of truth for HTTP response
  security headers. No security header may be set anywhere else.
  - **Middleware** (`SecurityHeadersMiddleware`, `apply_security_headers`) — a
    pure-ASGI middleware (not `BaseHTTPMiddleware`) chosen so it never buffers
    the body (safe for streaming/SSE), touches only the `http` scope
    (WebSocket upgrades pass through), and **enforces** its values (overwriting
    any inner-handler value so the posture cannot be weakened downstream).
  - **Emitted on every response:** `X-Content-Type-Options: nosniff`,
    `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
    a locked-down `Permissions-Policy` (camera/mic/geolocation/USB/… disabled),
    `Cross-Origin-Opener-Policy: same-origin`,
    `Cross-Origin-Resource-Policy: same-origin`, `X-XSS-Protection: 0` (the
    deprecated, buggy legacy auditor neutralized), and a strict
    `Content-Security-Policy` (`default-src 'none'; base-uri 'none';
    form-action 'none'; frame-ancestors 'none'`) — **no `unsafe-inline` /
    `unsafe-eval` anywhere.**
  - **Conditional:** `Strict-Transport-Security`
    (`max-age=63072000; includeSubDomains`) is emitted **only** over HTTPS or in
    production (honors `X-Forwarded-Proto` behind a TLS-terminating proxy;
    `preload` opt-in) so a plain-HTTP dev origin never pins itself.
    `Cross-Origin-Embedder-Policy: require-corp` is implemented but **opt-in**
    (`CROSS_ORIGIN_EMBEDDER_POLICY`) — it protects the API's own JSON not at all
    yet would break same-origin HTML tooling (Swagger UI) pulling cross-origin
    subresources without CORP.
  - **Environment-driven & nonce-capable:** every header value is overridable
    via environment variable (`CONTENT_SECURITY_POLICY`, `PERMISSIONS_POLICY`,
    `REFERRER_POLICY`, `X_FRAME_OPTIONS`, `CROSS_ORIGIN_*`, and the `HSTS_*`
    family). A `{nonce}` placeholder in the CSP is replaced per request with a
    fresh `secrets.token_urlsafe(16)` nonce, also exposed on
    `request.state.csp_nonce` for a future HTML handler to stamp onto
    `<script nonce=…>` tags.
- `backend/tests/test_security_headers.py` — 35 hermetic tests (no network, no
  Mongo): HSTS enablement/value and HTTPS/production gating, the strict default
  CSP and its nonce substitution, the cross-origin isolation family, every
  environment override, and real wire behavior through the middleware on
  success, error, CORS-preflight, and nonce-based responses.

Changed

- `backend/server.py` — wires `apply_security_headers(app)` immediately after
  `apply_cors(app)` (so headers wrap CORS responses too). No other change.
- `backend/security/__init__.py` — records `security.headers` in the tenant index.

Notes

- **CORP is safe with the credentialed CORS frontend:** `Cross-Origin-Resource-Policy`
  only governs *no-cors* cross-origin loads, so the frontend's `mode: cors`
  requests (governed by `security.cors`) are unaffected while the API can no
  longer be embedded as an opaque subresource.
- **Swagger UI (`/docs`) and any HTML served from the API origin** will be
  restricted by the strict `default-src 'none'` CSP; a deployment that needs it
  relaxes `CONTENT_SECURITY_POLICY` (or, preferably, disables docs in
  production). No production JSON API endpoint is affected.

Verification

- `pytest backend/tests/test_security_headers.py backend/tests/test_cors_hardening.py
  backend/tests/test_cookie_security.py backend/tests/test_password_policy.py`
  → **128 passed** (33 new + 95 regression). Manual: real-app smoke check on
  `/api` in production mode confirmed all headers present (including HSTS) and
  the CORS 400 rejection still carries `X-Frame-Options`.

Scope note

- Out of scope and untouched per the sprint definition: JWT/refresh, cookies,
  OAuth, password policy, CSRF, rate limiting, email verification,
  infrastructure/Docker, logging, database, and the frontend. Deferred:
  request-scoped nonce propagation into rendered HTML templates (the header/state
  plumbing exists now; no HTML is rendered by the API yet).

---

# Sprint PH1.5 — Password Policy & Account Protection — 2026-07-19

**Production Hardening PH1.5 complete. Finding H10 (password half) closed; risk
R-05 partially mitigated (password-policy half — the rate-limiting half remains
PH1.7).**

Replaced the accept-anything password handling (`password: str`, no validator,
implicit bcrypt cost) with a production-grade, centralized password policy.
Enforcement is at the model layer, so weak passwords are rejected with 422
before they ever reach hashing. Existing users, login, and OAuth are unchanged:
the policy applies to **new** passwords only, and the register/login API
contracts (payloads, success shapes, generic 401, `ip:email` lockout) are
byte-for-byte preserved.

Added

- `backend/security/passwords.py` — the single source of truth for password
  policy, hashing, and verification. No password may be validated, hashed, or
  verified outside this module.
  - **Policy** (`validate_new_password`, returns every violated rule at once):
    12–64 characters (and ≤72 UTF-8 bytes — the bcrypt truncation boundary);
    uppercase + lowercase + number + special character required; rejects
    common passwords, email-/name-derived passwords, repeated-character
    passwords (<5 unique chars), and sequential runs (alphabet/digits/qwerty
    rows, forward or reversed). Leading/trailing whitespace is normalized away
    before validation *and* hashing.
  - **Hashing** — bcrypt with an explicit, pinned cost factor
    (`BCRYPT_ROUNDS = 12`); previously the cost was the silent library default.
  - **Verification** — constant-time (`bcrypt.checkpw`) and never raises:
    empty/malformed stored hashes return `False` after a dummy-hash comparison,
    which also **timing-equalizes** login failures (unknown email, OAuth-only
    account, and wrong password all cost one bcrypt comparison).
- `backend/security/data/common_passwords.txt` — bundled, curated common-password
  blocklist (~450 lowercase entries; padding-resistant matching strips trailing
  digits/punctuation, so `Monkey987654!!` still matches `monkey`). No new
  dependencies.
- `backend/tests/test_password_policy.py` — 40 hermetic tests: every policy rule
  (boundaries, character classes, common/sequential/repeated/identity-derived,
  whitespace, multibyte length), hashing primitives (explicit cost, round-trip,
  never-raises, delegation), register-endpoint enforcement (422 + no user
  created, clean error contract, unchanged success shape), and the sprint's
  compatibility guarantees (legacy weak-password login works, OAuth-native
  401-not-500, indistinguishable failures, lockout preserved/cleared).

Changed

- `backend/models.py` — `UserCreate` gained a `model_validator` that normalizes
  the password and enforces the policy (422 with actionable rule messages;
  cross-field checks against the user's own email/name). `UserLogin` is
  deliberately unvalidated so existing accounts keep working.
- `backend/server.py` — removed the inline `hash_password`/`verify_password`
  definitions (and the now-unused `bcrypt` import); both are imported from
  `security.passwords`. Login now always runs exactly one bcrypt comparison
  (timing-equalization) — this also fixed a real bug where password login
  against a Google-OAuth-native account (`password_hash: ""`) raised
  `ValueError` → 500 instead of the generic 401. Added a sanitizing
  `RequestValidationError` handler: 422 bodies now carry only `loc`/`msg`/`type`
  — FastAPI's default handler echoed the submitted input (including raw
  passwords) back in every validation error.
- `backend/scripts/seed_dev_admin.py` — hashes via `security.passwords`
  (consistent cost factor; still dev-only, still no policy on seeded creds).
- `backend/tests/_fakedb.py` — `update_one` now supports `$inc` (match and
  upsert), making the login-lockout counter hermetically testable for the
  first time.
- `frontend/src/pages/Register.jsx` — client-side minimum raised 6 → 12 to
  mirror the server policy; full rule feedback comes from the API's 422
  messages, which the existing `formatApiError` already renders.

Security outcome

- No weak password can enter the system through any current registration path;
  policy logic exists in exactly one module (no per-endpoint drift).
- bcrypt cost is an explicit, reviewed constant; verification can no longer
  500 on hostile or legacy data.
- Login failures are generic **and** timing-equalized; validation errors no
  longer reflect submitted values. Password-hash exposure re-verified: no
  endpoint returns `password_hash` (covered by tests).
- Brute-force lockout (5 attempts / 15 min per `ip:email`) preserved unchanged
  and now under test.

Not in scope (deferred, unchanged)

- Password change endpoint and password reset flow (reviewed: neither exists;
  no reset tokens are generated anywhere) — deferred with `EmailStr` and email
  verification to an unscheduled PH1.5b (SMTP provider decision OR-6 moves with
  them). Platform-wide rate limiting remains PH1.7; JWT lifetime/rotation and
  session revocation remain PH1.6. Audit-logging of password-login events
  remains a tracked gap (SECURITY_ARCHITECTURE.md §22, PH1.6/PH1.7 candidate).

---

# Sprint PH1.4 — CORS Hardening — 2026-07-18

**Production Hardening PH1.4 complete. Risk R-03 / finding B3 closed.**

Replaced the development-friendly, unsafe CORS configuration with a
production-safe, environment-driven, exact-match origin allowlist, and
centralized the whole policy into a single module. The prior configuration
defaulted to `Access-Control-Allow-Origin: *` **with `allow_credentials=True`**
— a combination the Fetch standard forbids (the browser refuses to expose a
credentialed response to a wildcard origin) and a security hole (any origin was
trusted with the session cookie). Frontend communication is unchanged.

Added

- `backend/security/cors.py` — the single source of truth for CORS. Resolves an
  exact-match origin allowlist from the environment and assembles the
  `CORSMiddleware` configuration. Exposes `allowed_origins()`, `cors_kwargs()`,
  and `apply_cors(app)`.
  - **Origins** — `CORS_ALLOWED_ORIGINS` is canonical (comma-separated, exact
    scheme+host+port, trailing slash normalized away). Legacy `CORS_ORIGINS`
    and `FRONTEND_URL` are still honored as inputs (backward compatible), merged
    and de-duplicated. A literal `*` is stripped from **every** source, so a
    wildcard can never enter the allowlist or pair with credentials.
  - **Development fallback** — when nothing is configured and `APP_ENV` is not
    `production`, the local dev origins `http://localhost:3000` and
    `http://localhost:5173` are assumed, so the app runs with zero config.
  - **Production fail-closed** — nothing is assumed in production; an
    unconfigured allowlist is empty and every cross-origin request is rejected.
  - **Credentials** allowed (cookie-based auth) — safe by construction because
    origins are always an exact list, never the wildcard.
  - **Methods** restricted to `GET, POST, PUT, PATCH, DELETE, OPTIONS`;
    **request headers** restricted to `Authorization, Content-Type, Accept,
    Origin, X-Requested-With`; **no response headers exposed**. Preflight cached
    for 10 minutes.
- `backend/tests/test_cors_hardening.py` — 30 hermetic tests: allowlist
  resolution (canonical var, legacy inputs, trailing-slash/whitespace
  normalization, merge+dedupe, dev defaults, production fail-closed, wildcard
  stripped from every source), assembled-kwargs invariants (never wildcard,
  credentials on, methods/headers restricted, nothing exposed), and real wire
  behavior on a live middleware (allowed-origin preflight + simple request
  reflect ACAO and `Allow-Credentials: true`; unknown origin gets no grant;
  disallowed method/header preflight rejected; localhost works out of the box;
  production rejects unconfigured localhost).
- Documented CORS env vars (`CORS_ALLOWED_ORIGINS`) in `backend/.env` and removed
  the unsafe `CORS_ORIGINS=*` line.

Changed — `backend/server.py`

- Removed the inline wildcard-defaulting `app.add_middleware(CORSMiddleware, …)`
  block (and the now-unused `CORSMiddleware` import); CORS is now wired in via
  `apply_cors(app)` from `security.cors`.

Security outcome

- No wildcard origin remains anywhere; credentials are only ever granted to
  approved, exact-match origins (R-03 / B3 closed).
- CORS configuration is centralized — no duplicated or drifting CORS logic.
- Local development continues to work unchanged; the frontend on `localhost:3000`
  is allowed with credentials.

Not in scope (deferred, unchanged)

- Security **headers** (HSTS, X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy, CSP) were de-scoped from this
  CORS-only sprint and are carried forward as PH1.4b. The Google OAuth
  redirect-URI allowlist (`_allowed_google_redirect_uris`, PH1.2 scope) is
  untouched and continues to derive from `FRONTEND_URL` / `CORS_ORIGINS`.

---

# Sprint PH1.3 — Cookie Security Hardening — 2026-07-18

**Production Hardening PH1.3 complete. Risk R-04 closed.**

Hardened every authentication-related cookie for production and centralized the
cookie policy into a single module. No change to API contracts; login, logout,
refresh and the Google OAuth flow behave identically for clients — the cookies
they receive are now consistently and safely configured. Email/password and
Google auth are functionally unchanged.

Added

- `backend/security/` package + `backend/security/cookies.py` — the single
  source of truth for issuing and clearing every auth cookie. Resolves the
  Secure/HttpOnly/SameSite/Path/Max-Age/Domain posture from the environment and
  exposes `set_auth_cookies`, `set_access_cookie`, `set_refresh_cookie`,
  `set_oauth_state_cookie`, `clear_auth_cookies`, `clear_oauth_state_cookie`.
  - **Secure** is env-driven (`COOKIE_SECURE`) and **forced `True` when
    `APP_ENV=production`** regardless of the override — a production build can
    never ship an insecure auth cookie (closes R-04). Local dev defaults to
    `False` so cookies work over plain-HTTP `localhost`.
  - **HttpOnly** always `True` on all three cookies (JS never reads them).
  - **SameSite** defaults to `Lax` (CSRF baseline); configurable via
    `COOKIE_SAMESITE` (`lax`/`strict`/`none`). `None` is auto-degraded to `Lax`
    when the cookie would not also be `Secure` (browsers drop `None` without
    `Secure`). The OAuth-state cookie is never `Strict` so it survives the
    top-level redirect back from Google.
  - **Path** — session cookies at `/` (single-shot logout, no duplicate-path
    cookies); OAuth-state cookie scoped to `/api/auth`.
  - **Domain** — optional `COOKIE_DOMAIN` for subdomain session sharing;
    host-only when unset.
  - **Clearing** mirrors the exact key + path + domain + security attributes so
    the browser reliably deletes the cookie.
- `backend/tests/test_cookie_security.py` — 24 hermetic tests: policy resolution
  (prod forces Secure; dev default/override; SameSite default/invalid/`none`
  degrade/honored; domain), login/register cookie flags, production Secure
  enforcement, dev Secure override, configured Domain, logout clears both
  cookies with matching path, refresh re-issues a hardened access cookie (and
  does **not** rotate refresh — PH1.6 owns that), session-fixation overwrite,
  and the OAuth-state cookie (hardened, scoped to `/api/auth`, never `Strict`,
  Secure in prod, burned after a successful exchange).
- Documented cookie env vars (`COOKIE_SECURE`, `COOKIE_SAMESITE`, `COOKIE_DOMAIN`)
  in `backend/.env`.

Changed — `backend/server.py`

- Removed the local `set_auth_cookies` helper (which hardcoded `secure=False`)
  and the inline `set_cookie`/`delete_cookie` literals at all four call sites;
  they now delegate to `security.cookies`:
  - `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/google/session`
    → `set_auth_cookies` (hardened flags).
  - `POST /api/auth/logout` → `clear_auth_cookies` (clears both cookies with
    attributes that match how they were set).
  - `POST /api/auth/refresh` → `set_access_cookie` (was a raw `secure=False`
    `set_cookie`).
  - `_set_oauth_state_cookie` / `_clear_oauth_state_cookie` → delegate to
    `set_oauth_state_cookie` / `clear_oauth_state_cookie`; the state cookie now
    shares the unified Secure/SameSite/Domain posture instead of a hardcoded
    `secure=False`.

Security outcome

- Every auth cookie (`access_token`, `refresh_token`, `g_oauth_state`) carries
  `HttpOnly; SameSite` always, and `Secure` in production — no token can be sent
  over plain HTTP in production (R-04 closed).
- Logout removes every authentication cookie; refresh remains functional;
  session fixation is mitigated (fresh tokens overwrite on every login/register/
  OAuth); OAuth state is burned after use.
- Cookie policy is centralized — no duplicated cookie logic remains.

Not in scope (deferred, unchanged)

- CSRF **token** middleware for cookie-authenticated state-changing routes
  (SameSite=Lax provides the cookie-layer CSRF baseline; token middleware is
  tracked as the next hardening item). Refresh-token **rotation** and JWT
  lifetime changes remain PH1.6. CORS/security headers remain PH1.4.

---

# Sprint PH1.2 — Google OAuth Production Hardening — 2026-07-17

**Production Hardening PH1.2 complete. Risk R-02 fully closed.**

Hardened the legitimate Google OAuth flow (PH1.1 had removed the backdoors; this
sprint makes the remaining real flow production-safe). All changes preserve
email/password register and login unchanged.

Added

- `GET /api/auth/google/login-url` (`backend/server.py`) — server-side flow
  initiation. Generates a cryptographically random OAuth `state`, stores a
  **single-use server-side state record** (via `services/cache.py`: Redis when
  `REDIS_URL` is set, bounded in-memory fallback otherwise) bound to the chosen
  `redirect_uri` with an authoritative 600s TTL, **and** binds the state to the
  browser via a short-lived httponly `g_oauth_state` cookie. Validates the
  requested `redirect_uri` against an allowlist and returns the Google
  authorization URL. Fail-closed (401) when `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
  are unset. The client no longer constructs the Google URL.
- Single-use state consumption on callback (fetch-and-delete) → **replay
  protection** and cross-process, TTL-authoritative expiry; the callback
  `redirect_uri` must equal the one bound at flow start.
- `log_auth_event()` + immutable `db.security_audit_logs` collection (indexed at
  startup) — records every OAuth outcome (`oauth_login_success` with
  new_account/linked flags; `oauth_login_failure` with a `reason`: invalid_state,
  replayed_or_expired_state, invalid_redirect_uri, unverified_email,
  invalid_id_token, bad_issuer, sub_conflict, missing_id_token,
  token_exchange_failed, google_unavailable). Logs ip/user-agent/outcome, never
  tokens, codes, or state values (SECURITY.md logging rule).
- `google_sub` persisted on the user document and used as the **primary external
  identity**: accounts resolve by `google_sub` first (stable across Google
  profile/email changes), then by verified email for safe linking. An email
  already bound to a different `google_sub` is rejected (`sub_conflict`).
- `frontend/src/services/googleAuth.js` — shared `startGoogleLogin()` used by
  the Login and Register pages; calls the backend for the URL (with credentials
  so the state cookie is stored) and redirects.
- `frontend/src/pages/AuthCallback.jsx` — a proper `/auth/google/callback` route
  (added in `App.js`); forwards `code` **and** `state` to the session exchange.
- `backend/tests/test_oauth_hardening.py` — 26 hermetic tests: state issuance/
  randomness, missing/forged/mismatched state, **single-use/replay rejection,
  expired-or-absent server-side record**, unverified-email rejection,
  invalid/absent id_token, bad issuer, **id_token verified with client_id as
  audience**, redirect_uri allowlist + binding, token-exchange failure, Google
  network error (502), new-user creation, safe linking of an existing password
  account (password login still works), no duplicate accounts, role/capital
  preserved, **`sub`-primary identity across an email change, `sub_conflict`
  rejection**, and **audit-log assertions** (success/linked/invalid_state/
  unverified_email; no code or state value ever persisted).

Changed — `POST /api/auth/google/session` (`backend/server.py`)

- **CSRF + replay protection:** now requires `state`, validates it (constant-time)
  against the `g_oauth_state` cookie (per-browser binding), then consumes the
  single-use server-side record (fetch-and-delete). Rejects missing/mismatched
  state and replayed/expired state with 400.
- **Identity verification:** verifies the OIDC `id_token` (signature via Google's
  public keys, audience = our client_id, expiry) using `google-auth`, checks the
  issuer, and derives identity from the verified token instead of the `/userinfo`
  endpoint. **Rejects unverified Google emails (`email_verified != true`) with 401** —
  they never create or link an account (account-takeover guard).
- **redirect_uri allowlisting:** removed the hardcoded
  `http://localhost:3000/...` fallback; the redirect_uri must be allowlisted.
- **`sub`-primary identity + safe linking:** resolves by `google_sub` first
  (stable identity), then by verified email to link an existing email/password
  account (stores `google_sub`; leaves `password_hash`/`auth_provider` intact)
  rather than silently taking it over. Email stays the unique key (no duplicates);
  an email already bound to a different `google_sub` is rejected.
- Removed the dead legacy `session_id=` hash short-circuit in `App.js` and
  `AuthContext.jsx` (leftover from the flow removed in PH1.1).

Notes

- Two PH1.1 regression assertions in `test_auth_hardening.py` were updated:
  with `state` now mandatory and checked first, a lone forged code is rejected
  with 400 (still fail-closed, still no user created); the "not configured" 401
  contract moved to `GET /api/auth/google/login-url`.
- The `g_oauth_state` cookie deliberately mirrors the existing auth cookies'
  `secure=False` posture; unifying the secure/SameSite flags across all cookies
  is PH1.3's scope, not this sprint's.

---

# Sprint PH1.1 — Authentication Backdoor Removal — 2026-07-17

**Production Hardening PH1.1 complete. Findings B1 and B2 closed; risks R-01 and R-02 closed.**

Removed

- `GET /api/auth/auto-login` endpoint and the `ENABLE_AUTO_LOGIN` switch (`backend/server.py`) — finding B1, risk R-01. Admin sessions can no longer be obtained without credentials.
- Google OAuth demo-user fallback, `mock-code-for-testing` path, and the legacy fail-open `session_id` exchange against `demobackend.emergentagent.com` (`backend/server.py`) — finding B2, risk R-02. `/api/auth/google/session` is now fail-closed: it accepts only a Google authorization code and returns 401 when `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are unset. The orphan `session_token` cookie and `user_sessions` write (never validated anywhere) are gone.
- Startup admin seeding: the server no longer creates an admin with default password `admin123`, no longer force-resets the admin password on every boot, and no longer writes plaintext credentials to `memory/test_credentials.md` (same finding class as B1; closed under PH1.1).
- Frontend callers of removed paths: `autoLogin` in `AuthContext`, the "Quick Demo Login (Dev Mode)" button on the login page, and the legacy `session_id` exchange in `AuthCallback`.

Added

- `backend/scripts/seed_dev_admin.py` — idempotent dev-only admin seeding; refuses to run when `APP_ENV=production`; never resets an existing password.
- `backend/tests/test_auth_hardening.py` — 11 hermetic tests asserting the backdoors stay removed (404 on auto-login, 401/400 on all OAuth fallback payloads, no demo user ever created) and that register → login → me → refresh → logout still works.

Changed

- Live-server test fixtures (`test_phase5/6/7.py`) authenticate via `POST /api/auth/login` with env-driven admin credentials instead of auto-login, matching `test_backend`/`test_phase2`/`test_phase4`.
- `/api/auth/google/session` response now reports the user's actual role instead of hardcoded `"user"`.

---

# Documentation v1.2 — 2026-07-17

**Feature freeze. Production Hardening program introduced.**

Added

- `PRODUCTION_HARDENING.md` — master hardening architecture document: engineering audit baseline, risk matrix (R-01…R-15), production readiness score (4.2/10), priority matrix, security/infrastructure/deployment/testing/performance/documentation/monitoring/recovery strategies, operational·launch·certification checklists, open risks report (OR-1…OR-8), engineering standards addendum, and the Definition of Production Ready.
- `PRODUCTION_ROADMAP.md` — 36-sprint implementation roadmap: PH1 Production Security Hardening, PH2 Production Infrastructure & DevOps, PH3 Production Quality Assurance (12 sprints each), with per-sprint objective, scope, deliverables, expected files, dependencies, acceptance criteria, validation steps, rollback plan, difficulty, time, and success metrics; implementation sequencing and dependency graph.
- `CHANGELOG.md` — this file.
- ADR-027 in `DECISIONS.md` — Feature Freeze & Production Hardening Program; acknowledges the as-built FastAPI + CRA stack pending PH3.10 reconciliation.

Changed

- `INDEX.md` — added Production Hardening document category, documentation-map entries, and a "Production Hardening (Current Phase)" reading guide; version 1.2.
- `ROADMAP.md` — Phase 1 and Phase 2 marked COMPLETE; Production Hardening Interlude (PH1–PH3) inserted as the current phase; product Phases 3–9 blocked until Production Certification; version 1.2.
- `TASKS.md` — Current Focus replaced with the feature freeze and PH1.1 as next sprint; full PH1–PH3 status tracker added; version 1.2.
- `DECISIONS.md` — ADR-027 added; version 1.2.

Baseline

- `PRODUCTION_READINESS_REPORT.md` (Sprint 12 audit, 2026-07-17): verdict NOT READY FOR PRODUCTION. Six critical blockers (auth backdoors ×2, CORS wildcard + credentials, insecure cookies, broken Docker packaging, no CI/CD), five high-priority findings, five medium-priority findings.

---

# Documentation v1.1 — 2026-07-16

- Introduced `MARKET_DATA_ARCHITECTURE.md`; provider-independent market data architecture (ADR-026): Market Gateway, Source Manager, provider adapters, priority and failover strategy.
- Separated Connected Broker experience from Premium AI features.
- All affected documentation synchronized.

---

# Documentation v1.0

- Initial documentation system.

---

# End of Changelog

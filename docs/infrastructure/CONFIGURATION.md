# Configuration & Environment

**Sprint:** PH2.8 — Production Configuration & Environment Optimization
**Status:** Production-ready
**Owns:** the single configuration entry point, environment profiles, fail-closed
validation, and the runtime dependency footprint

---

## 1. The one rule

> **There is exactly one place that knows the shape of this application's
> configuration: `backend/security/secrets.py`.** Every variable the app reads,
> which environments require it, where its value may come from, and whether the
> process is allowed to start given what is set — all of it lives in the
> `SECRET_REGISTRY` in that module. `backend/.env.example`, `.claude/SECRETS.md`,
> and this document are all *generated from* or *describe* that registry. They
> never lead it.

If you add an `os.environ.get("NEW_THING")` anywhere in the codebase and do **not**
add `NEW_THING` to the registry, you have created undocumented configuration: the
next operator finds it only by reading source, it never appears in `.env.example`,
and the drift test (`test_example_file_is_in_sync_with_registry`) does not protect
it. Register it instead.

---

## 2. Why production systems centralize configuration

A growing service accumulates configuration the way a house accumulates keys: one
per feature, added where the feature was written, forgotten the moment it works.
Six months later nobody can answer three questions that decide whether a
deployment is safe:

1. **What does this app actually read?** — needed to provision a new environment.
2. **Which of those are secrets, and which are required here?** — needed to know
   whether a boot is safe or a landmine.
3. **Where did this value come from?** — needed at 3am when a rotated secret is
   still using its old value.

Scattered `os.environ.get()` calls answer none of them. A **registry** answers all
three by construction: it is a single, readable, testable description of the
configuration surface. Large teams converge on this pattern under many names
(12-factor "config", Django `settings`, Spring `@ConfigurationProperties`,
Kubernetes `ConfigMap`+`Secret`) but the invariant is identical — *one declarative
description, validated once at the edge, before any business logic runs.* This
codebase's version is `SECRET_REGISTRY` + `validate_config()`.

**Common mistakes this design rules out:**

- **Reading config at request time and crashing deep in a handler.** Validation
  runs once at startup and fails the *boot*, not the 4,000th request.
- **A half-configured feature that looks configured.** Cross-field invariants
  (OAuth both-or-neither, broker key+secret pairs, "at least one AI provider")
  are checked, not assumed.
- **A secret in a log.** The validator reports variable *names* and *presence*,
  never values.
- **Two owners for one secret.** Supplying a value from two sources is an error,
  not a silent merge — so a rotation can never be shadowed by a stale copy.

---

## 3. Configuration flow

```
   ┌──────────────────────── Configuration Sources ────────────────────────┐
   │                                                                        │
   │   <NAME>_FILE  ──▶ explicit path      (Docker/K8s secret pointer)      │  highest
   │   $SECRETS_DIR/<name> ──▶ discovered mount  (/run/secrets/…)           │    │
   │   <NAME>       ──▶ plaintext environment variable / .env               │  lowest
   │                                                                        │
   └────────────────────────────────┬───────────────────────────────────────┘
                                     │
                                     ▼
              ┌───────────────────────────────────────────────┐
              │  Central Configuration Loader                  │
              │  security.secrets.load_secrets()               │
              │   • one precedence order for EVERY variable    │
              │   • two sources for one variable → error       │
              │   • file-backed values materialized into       │
              │     os.environ ONCE, before anything reads it  │
              └────────────────────────┬──────────────────────┘
                                       │
                                       ▼
              ┌───────────────────────────────────────────────┐
              │  Validation                                    │
              │  security.secrets.validate_config()            │
              │   • required-per-environment presence          │
              │   • signing-key length, placeholder/weak       │
              │   • cross-field invariants (OAuth, brokers, AI) │
              │   • Mongo/Redis credential shape               │
              │   • FAIL CLOSED: aggregate every problem,      │
              │     raise SecretValidationError, stop the boot │
              └────────────────────────┬──────────────────────┘
                                       │  (only if valid)
                                       ▼
              ┌───────────────────────────────────────────────┐
              │  Application                                   │
              │  server.py → routers, Mongo client, scheduler  │
              │  every module reads os.environ via             │
              │  security.secrets.get() — one resolver         │
              └───────────────────────────────────────────────┘
```

Two entry points reach this flow, and they run the **same** code so validation and
the running app can never disagree:

| Entry point | When | Path |
|---|---|---|
| `docker/entrypoint.sh` | container start, before `uvicorn` | calls `validate_config()` → boot aborts *before* the server process if config is invalid |
| `server.py` | process import, after `load_dotenv` | `validate_config()` again (idempotent) → same result |

---

## 4. Configuration precedence

For **every** registered variable, `resolve_secret()` applies one order, highest
first:

1. **`<NAME>_FILE`** — an explicit path (e.g. `JWT_SECRET_FILE=/run/secrets/jwt_secret`).
   Highest because it is an unambiguous operator instruction: "the value is *there*,
   not here." A successful `_FILE` pointer suppresses discovery (step 2) entirely,
   so the normal Docker-secrets layout does not trip the two-owners rule.
2. **`$SECRETS_DIR/<name>`** — a discovered mount (default `/run/secrets`, both
   `NAME` and `name` case checked for Docker/Swarm/K8s portability).
3. **`<NAME>`** — a plaintext environment variable (or `.env` in development).

> **Two sources for the same variable is an error, not a merge.** A file source
> competing with a plaintext environment variable means two owners; the boot fails
> and tells the operator to delete one. This is what makes rotation trustworthy —
> a new file value can never be silently shadowed by a stale env value.

**Why files beat environment variables for secrets:** an env var is inherited by
every child process, is readable at `/proc/<pid>/environ`, is printed verbatim by
`docker inspect`, and is captured by most crash reporters. A file is readable only
by a process that opens that path, is not inherited, and — for Docker secrets —
lives in a tmpfs that never touches disk. Set `REQUIRE_FILE_SECRETS=true` once a
deployment has migrated, and plaintext delivery of any *sensitive* value becomes a
boot error rather than a warning, so the regression cannot creep back.

Full rationale and the rotation runbook: [deployment/SECRETS.md](../deployment/SECRETS.md)
and `.claude/SECRETS.md`.

---

## 5. Environment profiles

`APP_ENV` selects one of four profiles. Everything that is not `production` is
non-production by construction — every production gate keys on
`env == "production"` (`security.cookies.is_production`), so no other value can
accidentally relax cookie flags, CORS, or secret-strength enforcement.

| Profile | `APP_ENV` | Validation posture | Cookies / CORS | Typical use |
|---|---|---|---|---|
| **development** | `development` (default) | Lenient — missing optionals are warnings | relaxed (no `Secure`, localhost CORS) | a laptop with a half-filled `.env` |
| **testing** | `testing` | Lenient (mirrors development) | relaxed | automated suites, CI runners |
| **staging** | `staging` | Medium — `FRONTEND_URL` required; most other findings are warnings | production-like | pre-production rehearsal |
| **production** | `production` | **Strict — fail closed** | hardened | the real thing |

**The core trio — `MONGO_URL`, `DB_NAME`, `JWT_SECRET` — is a hard requirement in
*every* profile** (the server literally cannot construct its Mongo client or sign a
token without them), and `JWT_SECRET` must meet the 32-character minimum everywhere.

**Why `testing` is first-class and not an alias for `development`** (PH2.8): a CI
job or a test suite should be able to label its environment honestly. When
`APP_ENV=testing`, the logs, the `/api/diagnostics` `environment` field, and any
future env-scoped behaviour all say `testing` — instead of masquerading as a
developer's laptop, or (before PH2.8) tripping the "unknown `APP_ENV`" error and
coercing to `development`. It shares development's leniency (`LENIENT_ENVIRONMENTS`
in `secrets.py`) so a test environment with minimal config still boots.

**How large teams manage this:** the same registry drives all four profiles — there
is no per-environment fork of the validation logic, only a per-environment
*severity*. That is the discipline that keeps four environments honest: they differ
in strictness, never in which variables exist. A staging that reads a variable
production does not is how "works in staging, breaks in prod" is born.

---

## 6. Validation — fail closed

`validate_config()` collects **every** problem into one `ConfigReport` and, at
startup, raises `SecretValidationError` with an aggregated, value-free message if
any error was found. An operator fixes the whole environment in one pass, not one
variable per crash-loop. What it checks:

- **Presence** — required-in-this-environment variables must be set and non-blank.
- **Unknown `APP_ENV`** — a value outside the four profiles is a hard error; we do
  not guess.
- **Signing-key strength** — `min_length` (hard error in production and for the
  core trio) and a low-entropy check (`aaaa…`, keyboard runs) for generated secrets.
- **Placeholders / weak defaults** — `changeme`, `your_…_here`, `admin123`, etc.
  are rejected in production and for any required value.
- **Delivery posture** — a sensitive value arriving as plaintext env is a warning
  in production, an error under `REQUIRE_FILE_SECRETS`.
- **Datastore credential shape** — a `MONGO_URL` without `user:pass` (the 2017
  ransom-wave configuration) and a `REDIS_URL` without a password (an unauthenticated
  Redis is an RCE primitive via `CONFIG SET dir` + `SAVE`) are errors in production.
- **Encryption-key validity** — `BROKER_TOKEN_KEY` must be a well-formed Fernet key
  in *every* environment, because an invalid one fails not at boot but the first
  time a user connects a broker.
- **Cross-field invariants** — at least one AI provider in production; Google OAuth
  both-or-neither; each broker key+secret is a pair; `ENABLE_AUTO_LOGIN` off in prod.

Verify locally without a real environment:

```bash
cd backend
APP_ENV=production python -c "from security import secrets as s; s.validate_config()"
```

---

## 7. Dependency management

Configuration is not only environment variables — the set of packages the runtime
carries is configuration too, and it had rotted. PH2.8 rebuilt it.

### 7.1 The problem

`requirements.txt` was a raw `pip freeze`: 118 fully-pinned packages with no record
of which the application actually imports. PH2.1 measured the cost — **~220 MB+ of
the runtime image was packages no application module imports** (`litellm`, `boto3`,
`botocore`, `stripe`, `s5cmd`, the *old* `google-generativeai` SDK plus its
`grpcio`/`protobuf` tail, `pandas`, `numpy`, `openai`, `huggingface_hub`,
`tiktoken`, …), because a freeze cannot tell a live dependency from a fossil.

### 7.2 The method (how to re-derive the set)

1. **Enumerate** every third-party module the application imports — including lazy
   imports inside functions and `try/except` blocks (`anthropic`, `google.genai`,
   `twilio`, `websockets`, `pytz` are all imported lazily).
2. **Map** those to their distributions → the **direct** set.
3. **Compute the closure** of the direct set from installed metadata
   (`importlib.metadata.requires`, evaluating environment markers, excluding
   `extra ==` optional deps) → everything in the closure and nothing else is the
   **transitive** set. `requirements.txt` now documents the two sets separately.
4. **Prove it**, two ways, both offline:
   - *Closure is closed* — every core requirement of every kept package is also
     kept, so `pip install` yields exactly this set with nothing missing.
   - *Nothing hard-imports a removed package* — with every removed module blocked
     at import (raising `ModuleNotFoundError`, what genuine absence raises), the
     entire runtime module graph still imports.

Result: **118 → 58 packages.**

### 7.3 Removal ledger

Every removed package fell into one of these buckets. None is imported by
application code (verified by grep across `backend/` excluding `venv`/`tests`, and
by the import-block proof).

| Removed | Why it was there | Why it is safe to drop |
|---|---|---|
| `litellm`, `openai`, `tiktoken`, `tokenizers`, `huggingface_hub`, `hf-xet` | an abandoned multi-provider AI abstraction | the app calls `anthropic` and `google-genai` SDKs directly |
| `google-generativeai`, `google-ai-generativelanguage`, `google-api-python-client`, `google-api-core`, `google-auth-httplib2`, `googleapis-common-protos`, `grpcio`, `grpcio-status`, `proto-plus`, `protobuf`, `uritemplate`, `httplib2` | the **old** Gemini SDK (`google.generativeai`) and its gRPC transport | the app uses the **new** `google.genai` SDK (HTTP, no gRPC) |
| `boto3`, `botocore`, `s3transfer`, `jmespath`, `s5cmd` | AWS SDK + S3 tooling (an `anthropic[bedrock]` extra + unused backup exploration) | no AWS integration; Bedrock is not used |
| `stripe` | payment SDK for an unbuilt billing feature | no billing code imports it |
| `pandas`, `numpy` | pulled in transitively by the above; the Dockerfile's "pandas/numpy-heavy" note was stale | zero application imports; the only would-be consumer, `yfinance`, is optional and unpinned (see §7.5) |
| `python-jose`, `ecdsa`, `rsa`, `oauthlib`, `requests-oauthlib` | an alternate JWT/OAuth stack | the app uses `PyJWT` + `google-auth` |
| `passlib` | alternate password hashing | the app calls `bcrypt` directly |
| `pillow`, `jq`, `PyYAML`, `Jinja2`, `MarkupSafe`, `python-dateutil`, `regex`, `rich`, `typer`, `shellingham`, `markdown-it-py`, `mdurl`, `Pygments`, `jsonschema`, `referencing`, `rpds-py`, `fsspec`, `filelock`, `six`, `packaging`, `annotated-doc`, `ast_serialize`, `fastuuid`, `librt` | transitive tails of the removed trees, or freeze noise | nothing kept requires them |
| `email-validator` | pydantic `[email]` / fastapi `[all]` extra | no `EmailStr` field in `models.py` |
| `python-multipart` | Starlette form parsing | degrades gracefully when absent; the app has no `Form`/`UploadFile` route (re-add if one is introduced) |
| `watchfiles` | `uvicorn --reload` file watcher | **dev-only** — moved to `requirements-dev.txt`; the container entrypoint never uses `--reload` |

**Two additions**, not removals:

- **`pytz==2025.2`** — imported by `services/market_engine/validator.py` for NSE
  session math but pinned in **neither** requirements file, so the Market Engine
  validator failed to initialize (PH2.1 defect). Now pinned. This is a **fix**, not
  an optimization.
- **`docstring_parser==0.18.0`** — a *core* (non-extra) dependency of `anthropic`
  that the freeze happened to include but never pinned as a first-class line. Now
  explicit, so the closure is complete.

### 7.4 How to add a dependency correctly

```
# Wrong — re-buries the structure and re-imports the fossils:
pip freeze > requirements.txt

# Right:
#  1. import it in the code that needs it
#  2. add ONE pinned line to the DIRECT section of requirements.txt,
#     with a comment naming the module that imports it
#  3. add its new transitive deps (pinned) to the TRANSITIVE section
#  4. re-run the suite; if it's dev/CI-only, it goes in requirements-dev.txt instead
```

### 7.5 Known limitation — `yfinance`

`services/backtest_engine.py` imports `yfinance` lazily and **falls back to
synthetic data when it is absent** — which it is, because `yfinance` is pinned in
neither requirements file. PH2.8 preserved this behaviour deliberately: adding
`yfinance` drags `pandas` + `numpy` back in (~105 MB) *and* would route market data
outside the Market Gateway, which `MARKET_DATA_ARCHITECTURE.md` forbids. Wiring
backtesting through the gateway is a product/data-architecture decision, out of
scope for an infrastructure sprint. Until then, backtests run on synthetic data.

---

## 8. Image optimization results

The runtime image copies the built virtualenv wholesale, so the dependency
footprint *is* the dominant image-size lever (the Dockerfile structure — two-stage
build, discarded toolchain, pruned test suites, removed `pip` — was already optimal
after PH2.1; PH2.8 changed only what goes *into* the venv).

| Metric | Before (PH2.1) | After (PH2.8) |
|---|---|---|
| Packages in `requirements.txt` | 118 | 58 |
| `site-packages` on disk | 569 MB | ~192 MB |
| Dependency footprint removed | — | **377 MB (−66%)** |
| Projected runtime image | 1.03 GB | ~650 MB |

The 377 MB is a **measured** floor (sum of the on-disk size of every removed
distribution in the resolved venv). The projected image size is baseline minus that
delta; the exact end-to-end number is produced by the Docker build in CI (the build
itself is structurally unchanged). Largest single wins: `google-api-python-client`
(94 MB), `pandas` (71 MB), `litellm` (47 MB), `numpy` (34 MB), `botocore` (21 MB),
`stripe` (18 MB).

Beyond size, a smaller runtime is a smaller **attack surface** and a shorter
**CVE-exposure list**: every package that is not installed is a vulnerability that
can never apply to this image.

---

## 9. Migration guidance

For anyone pulling this change:

```bash
# Development / CI — refresh the venv so it matches the pruned set (and gains pytz):
cd backend
pip install -r requirements-dev.txt        # pulls requirements.txt too

# Production — nothing to do beyond a normal image rebuild:
docker build -t stockassist-backend ./backend
```

- **`pytz` now installs** — an existing dev venv created before this change lacks
  it, which is why the Market Engine validator's timezone path was silently broken.
  A `pip install -r requirements-dev.txt` fixes it.
- **Local `uvicorn --reload`** still works after installing `requirements-dev.txt`
  (which now carries `watchfiles`); a venv built from `requirements.txt` alone falls
  back to stat-polling reload.
- **No application code changed.** This is a dependency-set and configuration-model
  change only; behaviour is identical (934 non-integration tests green before and
  after).

---

## 10. Future cloud compatibility

The design is already cloud-portable and needs no rework to move off Docker Compose:

- **Config precedence is provider-agnostic.** The `_FILE` pointer and
  `$SECRETS_DIR` discovery are exactly how Kubernetes projected-volume secrets and
  a mounted Secrets-Manager/Vault CSI volume present values. A move from Compose
  `secrets:` to a K8s `Secret` or an AWS/GCP secret store changes *where the file is
  mounted*, not a line of application code.
- **Env profiles map to deployment targets.** `APP_ENV` is the one switch a
  Helm values file, an ECS task definition, or a Cloud Run env var sets per target.
- **Fail-closed validation is the readiness contract.** A misconfigured pod exits
  at boot with an aggregated error instead of serving a broken page — exactly what a
  Kubernetes `CrashLoopBackOff` or a failed ECS health check should key on.
- **The pinned, minimal dependency set** is what makes image scanning (PH2.6) and
  digest-pinned reproducible builds tractable — fewer packages, fewer advisories,
  faster pulls on every autoscale event.

---

## 11. Where the code and docs live

| Path | Role |
|---|---|
| `backend/security/secrets.py` | the registry, the loader, the validator — the single source of truth |
| `backend/requirements.txt` | production runtime dependencies (direct + pinned transitive) |
| `backend/requirements-dev.txt` | dev/CI-only tooling (never in the runtime image) |
| `backend/.env.example` | generated from the registry by `scripts/generate_env_example.py` |
| `backend/docker/entrypoint.sh` | runs `validate_config()` before `uvicorn` |
| [deployment/SECRETS.md](../deployment/SECRETS.md) | secret sources, rotation runbook, supply-chain policy |
| `.claude/SECRETS.md` | the secret inventory and incident response |
| [deployment/DOCKER.md](../deployment/DOCKER.md) | the image build and its size levers |

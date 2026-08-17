# Backend Docker Architecture

**Sprint:** PH2.1 — Backend Production Dockerfile
**Status:** Implemented
**Scope:** Backend container image only. Docker Compose (PH2.3), CI (PH2.5/2.6), the frontend image (PH2.2), Redis (PH2.8) and monitoring (PH2.10) are separate sprints and are deliberately *not* covered here.

---

## 1. Why Docker

The backend previously ran only on a developer's machine, against that machine's Python, that machine's system libraries, and that machine's `.env`. That arrangement has four failure modes that no amount of care eliminates:

| Problem | What it looks like in practice |
|---|---|
| **No reproducibility** | The dev laptop has a package the server doesn't. It works locally and 500s in production. *(This sprint found exactly that — see §10.)* |
| **No isolation** | The service inherits whatever OpenSSL, glibc and locale the host happens to have. A host upgrade silently changes application behaviour. |
| **No standard runtime** | Every deploy target — Railway, AWS, DigitalOcean, a colleague's machine — needs bespoke setup instructions that drift from reality. |
| **No deployment primitive** | Nothing to hand a load balancer, an autoscaler or a rolling-deploy controller. There is no artifact to roll *back to*. |

A container image fixes all four by making the unit of deployment a **single immutable artifact that carries its own interpreter, its own system libraries and its own pinned dependency set**. The image CI tests is bit-for-bit the image production runs. The only thing that varies between environments is the injected environment — never the build.

This is the "build once, deploy many" principle. If an artifact has to be rebuilt to move from staging to production, it is not the artifact that was tested, and the staging sign-off means nothing.

---

## 2. Architecture

```
                       backend/  (repo)
                            │
                            │  filtered by .dockerignore
                            │  620 MB on disk → 2.5 MB shipped
                            ▼
   ┌────────────────────────────────────────────────────────────────┐
   │                        docker build                            │
   ├────────────────────────────────────────────────────────────────┤
   │                                                                │
   │  ┌── STAGE 1: builder ─────────────┐                           │
   │  │  python:3.11-slim-bookworm      │                           │
   │  │  + build-essential (gcc, g++)   │                           │
   │  │  + pip download / compile        │                          │
   │  │                                  │                          │
   │  │  python -m venv /opt/venv        │                          │
   │  │  pip install -r requirements.txt │                          │
   │  │  prune: test suites, pip         │                          │
   │  │                                  │                          │
   │  │      ⛔ DISCARDED ⛔              │                          │
   │  └──────────────┬───────────────────┘                          │
   │                 │  only /opt/venv crosses the boundary         │
   │                 ▼                                              │
   │  ┌── STAGE 2: runtime ─────────────────────────────────────┐   │
   │  │  python:3.11-slim-bookworm   (no compiler, no headers)  │   │
   │  │                                                          │  │
   │  │   /opt/venv     ← copied from builder     root:root      │  │
   │  │   /app          ← application source      root:root      │  │
   │  │   uid/gid 10001 appuser  (nologin, no password)          │  │
   │  │                                                          │  │
   │  │   EXPOSE 8000                                            │  │
   │  │   HEALTHCHECK → docker/healthcheck.sh                    │  │
   │  │   ENTRYPOINT  → docker/entrypoint.sh                     │  │
   │  └──────────────────────────────────────────────────────────┘  │
   └────────────────────────────────────────────────────────────────┘
                            │
                            ▼
                  stockassist-backend:<tag>
```

### Runtime flow

```
docker run
    │
    ▼
PID 1 = docker/entrypoint.sh          (exec form — receives signals directly)
    │
    ├─ 1. read config from environment    (no file, no baked-in defaults)
    ├─ 2. structural validation           APP_ENV / PORT / WEB_CONCURRENCY
    ├─ 3. delegate to security/secrets.py validate_config()
    │        └─ any error → print aggregated report → exit 1  (NEVER starts)
    ├─ 4. run docker/pre-start.d/*        (migrations hook — PH2.x)
    │
    └─ 5. exec uvicorn  ──────► PID 1 becomes uvicorn
                                    │
                                    ├─ serves :8000
                                    ├─ SIGTERM → drain → FastAPI shutdown → exit 0
                                    │
                          every 30s: docker/healthcheck.sh
                                    └─ GET 127.0.0.1:8000/api → exit 0 | 1
```

---

## 3. Files

| File | Responsibility |
|---|---|
| `backend/Dockerfile` | Two-stage build definition. Declarative only — no startup logic. |
| `backend/.dockerignore` | The single auditable boundary between repo and image. |
| `backend/docker/entrypoint.sh` | PID 1. Validation → hooks → `exec` server. |
| `backend/docker/healthcheck.sh` | Liveness probe. Stdlib-only, exit 0/1. |
| `production.env.example` | Operator-facing runtime env template (repo root). |

Each file carries its full design rationale in its own header comments. This document covers the cross-cutting decisions and the operator workflow.

---

## 4. Multi-stage build strategy

Two stages, one shipped.

The **builder** installs the dependency set into a virtualenv at `/opt/venv`. It needs `build-essential` because a few pinned packages (notably `jq`, plus anything without a prebuilt wheel for the target architecture) compile from source. It is free to be as fat as it needs to be, because it is thrown away.

The **runtime** copies `/opt/venv` and nothing else from the builder. It has no compiler, no development headers, no package installer.

**Why a virtualenv rather than a system install?** It makes the entire dependency set *one relocatable directory*. `COPY --from=builder /opt/venv /opt/venv` then reproduces the environment exactly, with no risk of interleaving application packages into the base image's system `site-packages`.

**What this buys:**

- **~300 MB** of build toolchain never ships.
- **Post-exploitation is harder.** An attacker with code execution finds no `gcc` to compile a native payload, no `pip` to fetch a second stage, and no `curl`/`wget` to download one. (See §6.)

### Base image choice: `slim`, not `alpine`

| | Default (`python:3.11`) | **`-slim`** ✅ | `-alpine` |
|---|---|---|---|
| Base size | ~350 MB | **~45 MB** | ~20 MB |
| libc | glibc | **glibc** | musl |
| manylinux wheels | ✅ | **✅** | ❌ compiles from source |
| `pip install` time | ~1 min | **~1 min** | 15–30 min |

Alpine is smaller but uses musl, so the manylinux wheels for `pandas`, `numpy`, `cryptography`, `grpcio` and `tokenizers` do not apply — pip falls back to compiling them, which reintroduces a compiler dependency, multiplies build time by ~20×, and yields a *slower* runtime (musl's allocator underperforms glibc on numeric workloads). For a pandas/numpy-heavy service, **slim-glibc is the correct trade.**

---

## 5. Layer caching and build optimization

Docker invalidates a layer when its inputs change, and every layer *after* an invalidated one rebuilds too. So the ordering rule is: **least-frequently-changed first.**

```dockerfile
COPY requirements.txt ./          #  changes rarely
RUN pip install -r requirements.txt   #  expensive — cached across code edits
...
COPY . .                          #  changes constantly — cheap, and last
```

The classic beginner mistake is `COPY . .` before `pip install`, which re-resolves and re-downloads ~120 pinned packages on every single-character source change.

**Measured effect on this repo:**

| Build | Time |
|---|---|
| Cold — first build, incl. base-image pull | 2m 44s |
| Cold — `--no-cache` | **3m 21s** |
| Warm — dependency change | ~45s |
| Warm — **source-code change only** | **4.5s** |

*(Two cold builds, arm64, on a loaded developer machine; the spread is host load, not build variance.)*

That 4.5s number is the entire point of the ordering.

### Image size

| Optimization | Saved | Kept? |
|---|---|---|
| Multi-stage (toolchain discarded) | ~300 MB | ✅ |
| Bundled library test suites removed (`pandas/tests` alone = 40 MB) | 66 MB | ✅ |
| `pip` removed from both the venv and the system python | 16 MB | ✅ |
| `strip --strip-unneeded` over 147 MB of `.so` | **0 MB** | ❌ rejected |
| `pip install --no-compile` | ~158 MB | ❌ rejected |

Two rejections worth recording so they are not re-litigated:

- **`strip` saved literally zero bytes.** Modern manylinux wheels are already stripped by the `auditwheel` toolchain. The step is pure build time.
- **`--no-compile`** would drop 158 MB of `.pyc`, but the venv is read-only to the app user with `PYTHONDONTWRITEBYTECODE=1` set, so *every container start* would re-parse the whole dependency tree. That trades a one-off image cost for a permanent startup cost on every deploy and every autoscale event. Bytecode stays; it is also precompiled at build time (`compileall`) so cold start does not pay for it.

**Final: 1.03 GB.** This misses the roadmap's < 400 MB target, and the Dockerfile is not the reason — see §10.

### The `.dockerignore` payoff

`backend/` is **620 MB** on disk. The build context actually shipped is **2.5 MB** — a 99.6% reduction, dominated by excluding the 600 MB local `venv/`. Without it, every build would ship 620 MB to the daemon and the `COPY` layer would invalidate on any file change anywhere.

---

## 6. Security decisions

### Non-root execution

Containers run as root by default and **that default is wrong**. Root inside a container is root in the kernel's eyes; combined with a container-escape CVE (runc and kernel namespace bugs ship regularly) it becomes host root.

```dockerfile
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser \
        --home-dir /app --no-create-home --shell /usr/sbin/nologin appuser
USER appuser
```

- **Fixed** UID so bind-mounted volume ownership is stable across rebuilds.
- **High** (>10000) so it cannot collide with a host system account and inherit that account's file permissions through a mount.
- `nologin` shell, no password — the account exists to own a process, not to be logged into.

### The application cannot modify its own code

`COPY --chown=root:root . .` while the process runs as `appuser`. The result: read + execute on its own source, **no write**. This closes the most common post-exploitation move there is — writing a webshell or patching a route handler in place.

### No secrets in the image

`.dockerignore` excludes every dotenv file. This is not merely a leak control, it is a **correctness** control:

> `server.py` calls `load_dotenv(ROOT_DIR/'.env', override=True)`. With `override=True`, a `.env` present inside the image would **silently win over the environment variables injected by Docker/Kubernetes/Railway at runtime**. With no `.env` in the image, `load_dotenv` is a no-op and the container's real environment is authoritative.

Note that deleting a file in a later layer does **not** remove it — every layer is retained and readable by anyone who pulls the image. The only reliable control is keeping it out of the build context.

### Minimal attack surface

Verified absent from the runtime image: `pip`, `curl`, `wget`, `gcc`. No `apt-get install` runs in the runtime stage at all. The health check is written against the Python standard library **specifically so that `curl` never has to be installed** — a general-purpose HTTP client on a production server is a well-known convenience for an attacker staging a payload.

The base image's *system* pip (`/usr/local`) is removed too: the application runs entirely out of `/opt/venv` and never uses it, but it is a fully working, network-capable package installer sitting on a production server.

### Fail-closed startup

The container refuses to start rather than starting misconfigured. A server that boots with a missing `JWT_SECRET` and fails on the first login is far more expensive to diagnose than one that never reports Ready — and the orchestrator will halt the rollout and keep the previous healthy revision serving.

### Verified hardened runtime

The image runs healthy under the full restrictive flag set:

```bash
docker run --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
           --cap-drop=ALL --security-opt no-new-privileges:true ...
```

Nothing in the application writes to disk at runtime, so an immutable root filesystem costs nothing.

---

## 7. Runtime configuration

**All configuration arrives through environment variables. Nothing is baked into the image.**

`backend/security/secrets.py` (`SECRET_REGISTRY`) is the single source of truth for which variables exist and which are mandatory in which environment. The entrypoint runs that exact validator before starting the server, so the operator sees a clean aggregated report instead of a Python traceback from inside uvicorn's startup.

### Container-level variables (consumed by `entrypoint.sh`)

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `production` | `development` \| `staging` \| `production`. Drives cookie flags, HSTS, CORS strictness, secret-strength enforcement. The image defaults to the **most restrictive** posture — running it in development requires an explicit downgrade. |
| `HOST` | `0.0.0.0` | Bind address inside the container's own network namespace. Do not change. |
| `PORT` | `8000` | |
| `WEB_CONCURRENCY` | `1` | **Keep at 1.** See the warning below. |
| `LOG_LEVEL` | `info` | `debug` in production can put request bodies into logs. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Whose `X-Forwarded-For` uvicorn trusts. |
| `TIMEOUT_GRACEFUL_SHUTDOWN` | `20` | Keep below the orchestrator's kill grace period. |
| `TIMEOUT_KEEP_ALIVE` | `5` | |

Application variables (`MONGO_URL`, `JWT_SECRET`, `CORS_ALLOWED_ORIGINS`, …) are documented in `production.env.example` and defined in `security/secrets.py`.

> ### ⚠ `WEB_CONCURRENCY` must stay at 1
>
> The application runs an **in-process APScheduler**, a **heartbeat engine**, and an **in-memory WebSocket registry**. Each uvicorn worker is a separate OS process, so:
> - N workers fire every scheduled job **N times** (N morning reports, N EOD emails);
> - a WebSocket broadcast reaches only the clients attached to the *publishing* worker.
>
> **PH3.10 correction — replicas are not a workaround.** This paragraph previously said to scale with additional container replicas instead of workers. That is wrong and unsafe. WebSocket fan-out *was* moved to Redis in PH2.7, but **no scheduler leader is elected**: `server.py` calls `setup_scheduler()` unconditionally at startup, so every replica runs the full cron set — including `trade_monitor`, which runs every 60 s during market hours and calls `trading_engine.run_cycle` to place **real broker exit orders** on stop-loss and target hits. Two processes means two exit orders for one position, in a live brokerage account.
>
> Until a single-leader scheduler ships, the supported production topology is **exactly one backend process: one worker, one replica.** Horizontal scaling is blocked on leader election, not on load balancing.
>
> The entrypoint warns loudly rather than silently capping — the operator stays in control, but cannot claim they were not told.

> ### ⚠ `FORWARDED_ALLOW_IPS` is a security control
>
> The rate limiter (`security/rate_limit.py`) keys anonymous traffic on the client IP. Trusting `X-Forwarded-For` from *any* source lets a caller forge its own IP and walk straight through per-IP throttling. Set it to your reverse proxy's address only.

---

## 8. Health checks

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["/app/docker/healthcheck.sh"]
```

Docker considers a container "up" as long as PID 1 has not exited — a uselessly low bar for a web service, which can be alive and deadlocked, still starting, or unable to bind. The health check converts *"the process exists"* into *"the service answers"*, which is what every layer above consumes: `docker ps` status, Compose's `depends_on: condition: service_healthy` (PH2.3), and rolling-deploy gating in Swarm/ECS/Kubernetes.

**Exit codes are Docker's contract, not ours:** `0` = healthy, `1` = unhealthy. Every other code is reserved. Every failure path exits exactly 1.

### Why the probe targets `/api`

- **Unauthenticated** — a probe holds no credentials.
- **Rate-limit exempt** — `/api` is in `_MIDDLEWARE_EXEMPT_PATHS` (`security/rate_limit.py`), so a 30-second probe cadence can never consume the anonymous budget or get itself throttled into a false "unhealthy".
- **Touches no external dependency** — and this is the important design decision:

> This is a **liveness** probe: *"should this container be restarted?"* A MongoDB round-trip is deliberately **excluded**. If the database has a blip, a DB-coupled liveness probe marks every replica unhealthy simultaneously and the orchestrator restarts the entire fleet — turning a recoverable dependency outage into a total outage. Dependency health belongs in a separate **readiness** probe (PH2.10).

The probe validates the response *body*, not just the status code: a misrouted proxy can return `200` with an unrelated payload.

### Why the timing values

| Flag | Value | Reason |
|---|---|---|
| `--interval` | 30s | Steady-state cadence. |
| `--timeout` | 5s | Above the probe's own 4s HTTP timeout, so a slow response is reported *with a reason in the health log* rather than killed silently by the daemon. |
| `--start-period` | 45s | Grace window while the app validates config, imports pandas/numpy/grpc, connects to Mongo and creates ~20 indexes. Failures here do **not** count toward `--retries`, so a slow cold start is not mistaken for a crash loop. |
| `--retries` | 3 | ~90s of sustained failure before "unhealthy" — absorbs a GC pause without triggering a restart. |

---

## 9. Operator guide

### Build

```bash
# From the repository root
docker build -t stockassist-backend:local ./backend

# With provenance metadata (what CI does in PH2.6)
docker build -t stockassist-backend:$(git rev-parse --short HEAD) \
  --build-arg APP_VERSION=1.0.0 \
  --build-arg VCS_REF=$(git rev-parse --short HEAD) \
  --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  ./backend
```

### Run

```bash
cp production.env.example production.env   # git-ignored; fill in real values

docker run -d --name stockassist-api \
  -p 8000:8000 \
  --env-file production.env \
  --restart unless-stopped \
  stockassist-backend:local
```

Recommended hardened form (verified working):

```bash
docker run -d --name stockassist-api \
  -p 8000:8000 --env-file production.env \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop=ALL --security-opt no-new-privileges:true \
  --restart unless-stopped \
  stockassist-backend:local
```

### One-shot jobs

Any arguments passed to `docker run` are exec'd *after* configuration validation, so a job can never run against a broken config:

```bash
docker run --rm --env-file production.env stockassist-backend:local \
    python scripts/seed_dev_admin.py

docker run --rm -it --env-file production.env stockassist-backend:local sh
```

### Observe

```bash
docker ps                                     # STATUS column shows (healthy)
docker logs -f stockassist-api
docker inspect stockassist-api --format '{{json .State.Health}}' | jq
docker exec stockassist-api /app/docker/healthcheck.sh   # run the probe manually
```

### Stop

```bash
docker stop stockassist-api      # SIGTERM → drain → clean exit 0 (~1.2s measured)
```

### Extending startup (migrations)

Drop an executable into `backend/docker/pre-start.d/`. It runs, in lexical order, after validation and before the server starts — the same convention used by the official `postgres` and `nginx` images. A failing hook aborts startup by design: a half-applied migration must not be followed by a server that starts serving against it.

For multi-replica deployments, prefer running migrations as a **separate one-shot job** via the `exec "$@"` contract above, so N replicas do not race to migrate the same database.

---

## 10. Known limitations

### 1. Image is 1.03 GB against a < 400 MB target — driven by unused dependencies, not by the Dockerfile

Every image-level lever available has been pulled (§5). The remaining size is the dependency set itself. Measured inside the image:

| Package | Size | Imported by application code? |
|---|---|---|
| `googleapiclient` | **97 MB** (96 MB is `discovery_cache`) | **No** |
| `pandas` | 39 MB | Yes |
| `litellm` | **55 MB** | **No** |
| `twilio` | **50 MB** | Only via `services/whatsapp_service` |
| `numpy` (+`numpy.libs`) | 57 MB | Yes |
| `botocore` + `boto3` + `s3transfer` | **~32 MB** | **No** |
| `stripe` | **24 MB** | **No** |
| `s5cmd` | **15 MB** | **No** |

A `grep` across `backend/` (excluding `venv/`) finds **zero** application imports of `googleapiclient`, `litellm`, `boto3`, `stripe` or `s5cmd`. That is roughly **220 MB of unused dependencies**, and the fix belongs in `requirements.txt`, not in the Dockerfile.

`s5cmd` deserves a specific note: it is a standalone S3 CLI binary sitting in a production image. It is not imported by anything, and it is precisely the tool an attacker wants for bulk data exfiltration.

**Recommendation:** a dependency-pruning sprint (natural companion to PH1.11's audit tooling) should verify and remove these. Expected result: **~1.03 GB → ~600 MB**, plus a materially smaller CVE surface. Reaching < 400 MB likely also requires trimming `googleapiclient`'s discovery cache or dropping the package.

### 2. `pytz` is missing from `requirements.txt` — the Market Engine fails to initialize

```
server - ERROR - Market Engine init error: No module named 'pytz'
```

`services/market_engine/validator.py` imports `pytz` (lines 192, 212), but `pytz` is pinned in neither `requirements.txt` nor `requirements-dev.txt`, and is present in neither local venv. **This is pre-existing and equally broken outside Docker** — containerizing simply made it visible in the logs. It was left unfixed because it is an application dependency change, outside this sprint's scope. It should be fixed before any production deploy.

### 3. Single-worker only

See the `WEB_CONCURRENCY` warning in §7. Resolved by PH2.8.

### 4. Base image pinned by tag, not digest

`python:3.11-slim-bookworm` picks up Debian security patches automatically on rebuild — the right default while there is no CI. Once PH2.6 adds image scanning, pin by digest (`@sha256:…`) for byte-identical rebuilds. Dependabot's `docker` ecosystem is already configured in `.github/dependabot.yml` to propose digest bumps.

### 5. No image vulnerability scanning yet

Trivy/Grype gating lands in PH2.6.

### 6. Verified on `linux/arm64` only

Built and tested on Apple Silicon. Multi-arch (`buildx --platform linux/amd64,linux/arm64`) arrives with the CI pipeline in PH2.6. Size and timing figures here are arm64.

---

## 11. Forward path

| Sprint | How it builds on this |
|---|---|
| **PH2.2** | Frontend image — same two-stage pattern (node build → nginx runtime). |
| **PH2.3** | Compose split. `docker-compose.prod.yml` consumes this image as a `build:` target, uses the `HEALTHCHECK` for `depends_on: condition: service_healthy`, and sets `FORWARDED_ALLOW_IPS` to the proxy's address on the compose network. **The existing root `docker-compose.yml` is development-oriented** (bind mounts `./backend:/app`, `--reload`, overrides the entrypoint) and must not be pointed at this image unchanged — splitting it is exactly PH2.3's job. |
| **PH2.4** | Environment framework. `production.env.example` becomes one of the per-environment templates, with drift-checking against `SECRET_REGISTRY`. |
| **PH2.6** | CI builds this image with cache mounts, passes `VCS_REF`/`BUILD_DATE`, scans it, and pushes to a registry. Digest pinning lands here. |
| **PH2.8** | Redis fan-out + leader-elected scheduler → `WEB_CONCURRENCY > 1` becomes safe. |
| **PH2.10** | A dedicated **readiness** endpoint (dependency-aware) alongside this **liveness** probe. |

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Container exits immediately, prints a boxed `configuration invalid` report | Fail-closed startup validation. Working as designed. | Read the `✗` lines — each names the missing/weak variable. Fix `production.env`. |
| `FATAL: APP_ENV='...' is not one of` | Typo, or an unexpanded `${VAR}` template. | Use `development`, `staging`, or `production`. |
| `looks like a placeholder / weak default value` | A real-looking-but-fake value (e.g. `...-placeholder-...`, `changeme`). The detector is intentionally aggressive. | Use a genuine secret. |
| Status stuck at `health: starting` for >45s | Slow cold start, or the app is not binding. | `docker logs` — look for `Application startup complete`. |
| `unhealthy: cannot reach ... Connection refused` | Server not listening on `PORT`. | Check `HOST=0.0.0.0` (not `127.0.0.1`) and that `PORT` matches the published port mapping. |
| Container healthy but unreachable from the host | Port not published. | Add `-p 8000:8000`. `EXPOSE` alone publishes nothing. |
| `docker stop` takes 10s and exit code is `137` | SIGKILL — signals are not reaching the server. | Confirm the entrypoint is exec form and ends in `exec`. Do not wrap it in `sh -c`. |
| Permission denied writing inside the container | By design — `/app` and `/opt/venv` are root-owned, the process is `appuser`. | Write to `/tmp`. If a real writable path is needed, mount a volume with the right ownership (uid 10001). |
| `exec /app/docker/entrypoint.sh: no such file or directory` | CRLF line endings — the shebang becomes `#!/bin/sh\r`. | `git config core.autocrlf input`; ensure `.gitattributes` keeps `*.sh` as LF. |
| Build very slow on every code change | The dependency layer is being invalidated. | Confirm `COPY requirements.txt` precedes `COPY . .`, and that `.dockerignore` excludes `venv/`. |
| `Market Engine init error: No module named 'pytz'` | Known limitation §10.2. | Add a `pytz` pin to `requirements.txt`. |

---

**References:** `.claude/SECRETS.md` (configuration surface) · `.claude/SECURITY_ARCHITECTURE.md` (PH1 controls) · `.claude/PRODUCTION_ROADMAP.md` (PH2 plan) · `docs/operations/production-checklist.md`

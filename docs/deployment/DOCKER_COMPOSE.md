# Docker Compose Stack

**Sprint:** PH2.2 — Production Docker Compose
**Status:** Implemented and verified (2026-07-22)
**Audience:** Platform / DevOps engineers, backend engineers, anyone running the stack outside a development machine
**Prerequisite reading:** [DOCKER.md](DOCKER.md) — the PH2.1 backend image this stack orchestrates

---

## 1. What this document covers

PH2.1 produced a single deployable artifact: a non-root, fail-closed backend container image. An image is not a system. The backend needs a database, a cache, a network to reach them over, somewhere to put data that survives a restart, and a defined startup order so it does not race its own dependencies.

PH2.2 supplies exactly that, and nothing beyond it. One command brings up the whole backend stack:

```bash
docker compose -f docker-compose.yml up -d --wait
```

Out of scope, deliberately: CI/CD, Kubernetes, NGINX, TLS, load balancing, cloud deployment, and any application change. Those are later PH2 sprints.

### Files

| File | Role |
|---|---|
| `docker-compose.yml` | **Base stack.** Production-shaped: backend, MongoDB, Redis, networks, volumes. Safe to run in a production-like environment. |
| `docker-compose.override.yml` | **Development overlay.** Auto-merged by Compose. Adds Mongo Express, Redis Insight, n8n, host-published database ports, relaxed `APP_ENV`. |
| `docker/mongodb/init-app-user.js` | First-boot provisioning of the least-privilege application database user. |
| `compose.env.example` | Template for the project-root `.env` — the variables **Compose itself** reads. |
| `production.env.example` | Template for `production.env` — the variables the **backend container** receives. |

---

## 2. Architecture

```
                                  HOST
                    ┌───────────────────────────────────┐
                    │  127.0.0.1:8000   → backend       │
                    │  127.0.0.1:8081   → mongo-express │  dev overlay only
                    │  127.0.0.1:5540   → redisinsight  │  dev overlay only
                    │  127.0.0.1:5678   → n8n           │  dev overlay only
                    │  127.0.0.1:27017  → mongo         │  dev overlay only
                    │  127.0.0.1:6379   → redis         │  dev overlay only
                    └─────────────────┬─────────────────┘
                                      │  published ports
    ══════════════════════════════════╪══════════════════════════════════
                                      │
    ┌─────────────────────────────────┴──────────────────────────────────┐
    │  network: stockassist_edge          bridge · egress allowed        │
    │                                                                     │
    │   ┌───────────────┐         ┌───────────────┐   ┌───────────────┐  │
    │   │   backend     │         │ mongo-express │   │      n8n      │  │
    │   │  FastAPI      │         │    (dev)      │   │    (dev)      │  │
    │   │  uid 10001    │         └───────┬───────┘   └───────────────┘  │
    │   │  :8000        │                 │                              │
    │   └───┬───────┬───┘                 │      ↕ Anthropic · Gemini ·  │
    │       │       │                     │        Yahoo · brokers        │
    └───────┼───────┼─────────────────────┼──────────────────────────────┘
            │       │                     │
    ┌───────┼───────┼─────────────────────┼──────────────────────────────┐
    │  network: stockassist_data      internal: true · NO route in or out │
    │       │       │                     │                              │
    │       ▼       ▼                     ▼                              │
    │  ┌─────────┐  ┌─────────┐    (mongo-express reaches mongo here)     │
    │  │  mongo  │  │  redis  │                                          │
    │  │  :27017 │  │  :6379  │    ┌───────────────┐                     │
    │  │  --auth │  │requirepass│  │  redisinsight │  (dev)              │
    │  └────┬────┘  └────┬────┘    └───────┬───────┘                     │
    └───────┼────────────┼─────────────────┼──────────────────────────────┘
            │            │
       ┌────▼─────┐ ┌────▼──────┐
       │mongo_data│ │redis_data │   named volumes — survive `down`
       │mongo_cfg │ └───────────┘   destroyed only by `down -v`
       └──────────┘
```

**Startup order** is enforced by health, not by wall-clock guessing:

```
  mongo   ──┐
            ├── both report healthy ──▶ backend starts ──▶ backend reports healthy
  redis   ──┘        (~10s)                                       (~3s)
```

---

## 3. Services

### 3.1 backend

The PH2.1 image. Compose does **not** restate how it is built — `build.context: ./backend` points at the Dockerfile, which remains the single authority. In a real deployment `image:` names a registry tag and `build:` never runs:

```bash
BACKEND_IMAGE=ghcr.io/the-shreyash/stockassist-backend BACKEND_IMAGE_TAG=1.4.0 \
  docker compose -f docker-compose.yml pull && docker compose -f docker-compose.yml up -d
```

Three integration details are worth calling out because each one is a bug if omitted:

**No `healthcheck:` block.** The image already declares one, tuned in PH2.1 (30s interval, 45s start period, probes `/api` over loopback with no database round-trip). Restating it in Compose would fork the definition: `docker run` and `docker compose up` would then probe the same image differently, and one copy would rot. The image is the authority on whether the image is healthy.

**`stop_grace_period: 30s`.** `docker/entrypoint.sh` gives uvicorn 20 seconds to drain in-flight requests after `SIGTERM`. Docker's default grace period is 10 seconds. Without this line every `compose down` and every redeploy would `SIGKILL` the server mid-drain, severing open requests and WebSockets. **The orchestrator's patience must always exceed the application's timeout** — a rule worth applying anywhere the two are configured separately.

**Port binding defaults to `127.0.0.1`.** Docker writes published ports into the `DOCKER-USER` iptables chain, which is evaluated *before* `ufw` and `firewalld`. A `0.0.0.0` bind on a cloud VM is reachable from the internet even when the host firewall is configured to deny it — a genuinely common and genuinely serious mistake. Change `BACKEND_BIND_ADDR` only when the network boundary is enforced somewhere Docker cannot bypass (a cloud security group, or a reverse proxy in front — PH2.11).

### 3.2 mongo

Authentication is on: setting `MONGO_INITDB_ROOT_USERNAME` / `_PASSWORD` makes the official entrypoint start `mongod --auth`. There is no anonymous mode and no default password anywhere in the stack.

**Two accounts, not one.** The image creates a cluster root user. Handing those credentials to the application would be a least-privilege violation with real consequences — an injection or a leaked container environment would carry the ability to drop every database and create users. So `docker/mongodb/init-app-user.js` creates a second account with `readWrite` on the application database only, and that is the identity the backend authenticates with. The root password never enters the backend container.

Verified during this sprint, connected as the application user:

| Operation | Result |
|---|---|
| CRUD + `createIndex` on `alpha_stock` | allowed (backend created 14 collections on first boot) |
| `listDatabases` | returns `alpha_stock` and nothing else |
| read `admin.system.users` | `not authorized on admin` |
| `createUser` | `not authorized on alpha_stock` |
| `dropDatabase` | `not authorized on alpha_stock` |
| any command with no credentials | `requires authentication` |

The health check runs `db.adminCommand('ping')`, one of the few commands MongoDB answers before authentication — so the probe needs no credentials and no password appears in the process table or in `docker inspect`. Its output is piped through `grep -q 1` because `mongosh` can exit 0 while reporting a failed command.

### 3.3 redis

Redis is in the **base** stack, not an optional extra, and the reason is a correctness one. `backend/services/cache.py` degrades to a per-process in-memory dict when `REDIS_URL` is unset. That is right for one process and silently wrong for two: two replicas would hold divergent caches, and a WebSocket broadcast published by replica A would never reach clients connected to replica B. Including Redis locally means the local stack has the same cross-process semantics as a scaled deployment, so the class of bug that only appears above one replica is reachable here too.

- **Password required** (`--requirepass`, no default). An unauthenticated Redis is not merely a data leak — `CONFIG SET dir` + `SAVE` writes arbitrary files as the redis user, which is a standard path to remote code execution.
- **AOF persistence** (`appendonly yes`, `appendfsync everysec`, RDB disabled). An AOF loses at most one second of writes; an RDB snapshot loses everything since the last checkpoint.
- **Memory ceiling** (`maxmemory` + `allkeys-lru`). A cache with no ceiling consumes everything it is given and is then OOM-killed, taking the AOF's clean shutdown with it. Everything stored here is reconstructible, so evicting the least-recently-used key always beats refusing a write.
- The health check authenticates through `REDISCLI_AUTH` (read automatically by `redis-cli`) rather than a `-a` flag, keeping the password off the command line. It pipes through `grep -q PONG` because `redis-cli` exits 0 even when the server answers `NOAUTH Authentication required` — testing the exit code alone would report a locked-out Redis as healthy.

### 3.4 Development-only services

Defined **only** in `docker-compose.override.yml`. Nothing in the base stack references them, nothing waits on their health, and the stack is fully functional with all of them absent — which is the property that lets the production stack simply not include the file.

| Service | Started by | URL | Notes |
|---|---|---|---|
| Mongo Express | default (`docker compose up`) | http://127.0.0.1:8081 | Holds **root** credentials. Basic auth required, no default password. |
| Redis Insight | `--profile tools` | http://127.0.0.1:5540 | Add the connection manually (host `redis`, port `6379`, password = `REDIS_PASSWORD`). |
| n8n | `--profile automation` | http://127.0.0.1:5678 | Carried over from the pre-PH2.2 compose file. See the warning below. |

> **⚠ n8n authentication — a correction, not a carry-over.**
> The previous `docker-compose.yml` set `N8N_BASIC_AUTH_ACTIVE` / `_USER` / `_PASSWORD`. Those variables were **removed upstream in n8n 1.0** and are silently ignored by 1.70. Verified during this sprint: with all three set, `GET http://127.0.0.1:5678/` returned **200 with no credentials**. They are not carried forward — a security control that does nothing is worse than a documented absence. What actually protects the service is the loopback-only port binding plus n8n's own user management: the first browser visit forces creation of an owner account. **Complete that setup immediately after the first `up`.**

---

## 4. Networks

Two tiers, not one flat network. A flat network makes every service reachable from every other service, including from anything added later for an unrelated reason. Segmentation is what stops "we added a small internal tool" from quietly becoming "the small internal tool can reach the database".

| Network | Driver | `internal` | Members |
|---|---|---|---|
| `edge` | bridge | no | backend (+ dev tools) |
| `data` | bridge | **yes** | backend, mongo, redis |

`internal: true` removes the gateway from the network entirely. Containers attached **only** to `data` — mongo and redis in the production stack — can neither reach the internet nor be reached from it, whatever else is misconfigured. A database container cannot exfiltrate to a remote host and cannot pull down a payload. Inter-container DNS and traffic inside the network are unaffected.

Verified:

```
backend (edge + data)  → https://example.com   200
redis   (data only)    → https://example.com   wget: bad address   ✅ no egress
backend                → mongo  172.18.0.3, redis 172.18.0.2       ✅ service DNS
```

> **⚠ A container attached only to an `internal: true` network cannot publish a port.**
> Docker has no NAT path to it. It does not error — it accepts the `ports:` entry, records the binding in the container config, and silently never listens; `docker ps` shows no mapping. This was found and fixed during the sprint: the development overlay attaches mongo and redis to `edge` **as well**, purely so their loopback port publication works. That is an explicit development-only relaxation. The production stack keeps both databases isolated with no egress and no published port.

---

## 5. Volumes

All named, never host bind mounts for database storage. A bind mount inherits the host filesystem's semantics, and on macOS and Windows that path goes through a file-sharing layer whose locking and `fsync` behaviour MongoDB explicitly does not support — it corrupts data or refuses to start. Named volumes live in the Docker VM's native filesystem on every platform, so behaviour is identical everywhere.

| Volume | Mounted at | Contents |
|---|---|---|
| `stockassist_mongo_data` | `mongo:/data/db` | Databases, indexes, journal |
| `stockassist_mongo_config` | `mongo:/data/configdb` | Cluster metadata (matches the image's own `VOLUME`; without it a fresh anonymous volume accumulates on every `up`) |
| `stockassist_redis_data` | `redis:/data` | AOF |
| `stockassist_backend_uploads` | *declared, not mounted* | Reserved for future user uploads |
| `stockassist_redisinsight_data` | `redisinsight:/data` | Saved connections (dev) |
| `stockassist_n8n_data` | `n8n:/home/node/.n8n` | Workflows and credentials (dev) |

The one bind mount in the stack is `docker/mongodb/init-app-user.js`, mounted `:ro`. It is a bind mount by necessity — the file is versioned source that must track the repository — and read-only so the database container cannot alter its own provisioning logic.

`backend_uploads` is declared but **not** mounted. Mounting it today would create `/app/uploads` owned by `root:root` inside a container whose process runs as uid 10001, so the application could not write to it — a subtle, confusing failure. Wiring it up requires the image to pre-create the directory with the right ownership, which is a PH2.1 change and out of scope here. Declaring it reserves the name and records the intent.

**`name: stockassist`** is set at the top of the base file. Without an explicit project name Compose derives one from the checkout directory, so renaming the directory or making a second checkout silently produces a *second* set of volumes — the classic "my data disappeared" incident.

Verified: after a full `docker compose down` (no `-v`) and a fresh `up`, all 14 MongoDB collections, a probe document, and a Redis key were still present. `down -v` is the only command here that destroys data.

---

## 6. Environment configuration

**Two files, because they are two different trust domains.**

```
  .env  (project root, git-ignored)          production.env  (git-ignored)
  ─────────────────────────────────          ────────────────────────────────
  read by DOCKER COMPOSE for ${...}          injected into the BACKEND
  interpolation                              container via env_file

  MONGO_ROOT_PASSWORD  ─┐                    JWT_SECRET
  MONGO_APP_PASSWORD    │ handed to the      ANTHROPIC_API_KEY
  REDIS_PASSWORD        │ specific service   CORS_ALLOWED_ORIGINS
  host ports, image tags│ that needs it      broker / email / … secrets
                        │
  template:             │                    template:
  compose.env.example   ┘                    production.env.example
```

The MongoDB **root** password lives in `.env` and is handed only to the `mongo` service. The backend container never receives it, so application-level code execution cannot escalate to database administration. It is also a rotation boundary: infrastructure credentials are owned by whoever runs the stack, application secrets by whoever owns the product integrations.

**Nothing is hardcoded.** Every credential uses the `${VAR:?message}` form, so a missing value is a startup error with a specific remedy rather than a silent default:

```
$ docker compose config
error while interpolating services.backend.environment.REDIS_URL:
required variable REDIS_PASSWORD is missing a value:
set REDIS_PASSWORD in .env — see compose.env.example
```

**Compose owns the wiring.** `MONGO_URL`, `REDIS_URL`, `DB_NAME`, `PORT` and `APP_ENV` are set in the service's `environment:` block, which takes precedence over `env_file`. So the stack's internal wiring cannot be broken by a stale `MONGO_URL` left in an operator's `production.env` — that value is simply overridden by the composed one pointing at the in-stack database.

**Passwords must be URL-safe.** Three of them are interpolated into connection URIs, where an unescaped `@`, `:`, `/` or `#` truncates the URI and produces an authentication failure that looks nothing like its cause. Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, which emits only `[A-Za-z0-9_-]`.

---

## 7. Operator guide

### First run

```bash
# 1. Compose stack variables
cp compose.env.example .env
python3 - <<'EOF' >> .env
import secrets
for k in ("MONGO_ROOT_PASSWORD", "MONGO_APP_PASSWORD", "REDIS_PASSWORD",
          "MONGO_EXPRESS_PASSWORD"):
    print(f"{k}={secrets.token_urlsafe(32)}")
EOF
#    then delete the REPLACE_… placeholder lines that these override

# 2. Backend application secrets
cp production.env.example production.env
$EDITOR production.env        # JWT_SECRET, an AI provider key, FRONTEND_URL, CORS_ALLOWED_ORIGINS

# 3. Boot
docker compose -f docker-compose.yml up -d --wait     # production-like
docker compose up -d --wait                           # + development tooling
```

> Already have a project-root `.env` from the pre-PH2.2 compose file? **Append** to it rather than overwriting. The application secrets it contains are no longer read by Compose — move them into `production.env`.

### Everyday commands

```bash
docker compose ps                                   # health of every service
docker compose logs -f backend                      # follow application logs
docker compose --profile tools up -d                # + Redis Insight
docker compose --profile automation up -d           # + n8n
docker compose restart backend                      # restart one service
docker compose down                                 # stop everything, KEEP data
docker compose down -v                              # ⚠ stop everything, DESTROY data
docker compose -f docker-compose.yml config         # render the production config
```

### Running one-shot jobs

The PH2.1 entrypoint validates configuration and then `exec`s any arguments it is given, so one-off jobs reuse the exact image and the exact validated configuration:

```bash
docker compose run --rm backend python scripts/seed_dev_admin.py
```

---

## 8. Measured results

Apple silicon, Docker 29.4.0 / Compose v5.1.1, image already built.

| Scenario | Time to all services healthy |
|---|---|
| Cold start (`down -v`, empty volumes — includes MongoDB init + 14 collections) | 13–32 s |
| Warm start (existing volumes) | 12–14 s |
| `restart` → healthy | 13 s |
| Development stack, `--profile tools` | 16 s |
| Redis crash → restarted → healthy | 15 s |
| `down` (graceful) | 2 s |

Verification performed:

| # | Check | Result |
|---|---|---|
| 1 | `docker compose up --wait` — backend, mongo, redis all healthy | ✅ |
| 2 | Backend waits for both dependencies (`service_healthy`) | ✅ ordering deterministic |
| 3 | Backend connects as the least-privilege app user; creates 14 collections | ✅ |
| 4 | Redis cache + pub/sub activated (`Cache layer: Redis connected`) | ✅ (see limitation L1) |
| 5 | No host ports for mongo/redis in the production stack | ✅ |
| 6 | Data network has no egress; edge network does | ✅ |
| 7 | Anonymous MongoDB access refused | ✅ |
| 8 | App user denied `admin` reads, `createUser`, `dropDatabase` | ✅ |
| 9 | Unauthenticated Redis command refused (`NOAUTH`) | ✅ |
| 10 | Named volumes survive `down` + `up` (14 collections, probe doc, Redis key) | ✅ |
| 11 | Restart policy: Redis crash → `RestartCount=1` → healthy | ✅ |
| 12 | AOF survived the crash (value readable after restart) | ✅ |
| 13 | Missing `production.env` → Compose refuses to start | ✅ |
| 14 | Missing `REDIS_PASSWORD` → interpolation error naming the fix | ✅ |
| 15 | Weak `JWT_SECRET` + `APP_ENV=production` → backend exits 1, never healthy | ✅ |
| 16 | Development overlay: Mongo Express, Redis Insight, n8n, loopback DB ports | ✅ |

---

## 9. Known limitations

**L1 — Redis pub/sub listener stops after ~3 seconds.** With `REDIS_URL` set for the first time, the backend logs `Cache layer: Redis pub/sub listening on 'sa:events'` and then, three seconds later, `Pubsub sa:events listener stopped: Timeout reading from redis:6379`. Root cause is in the application, not the stack: `backend/services/cache.py:47` builds one shared client with `socket_timeout=3`, and `pubsub.listen()` blocks longer than that by design. It is a pre-existing defect that was unreachable until this sprint put a Redis in the stack. **No functional regression today** — at `WEB_CONCURRENCY=1` with one replica, the in-process event bus carries every event and the cache path (which uses short-lived commands) works normally. The fix — a dedicated pub/sub connection without a read timeout, plus a reconnect loop with backoff — belongs to **PH2.8**, which owns cross-process fan-out and can test it properly. Fixing it blind here would be an untested behaviour change in the realtime path.

**L2 — No in-container hot reload.** Deliberate. Bind-mounting `./backend` over `/app` would reintroduce `backend/.env` inside the container, and `server.py` calls `load_dotenv(..., override=True)`, so that file would silently override every environment variable Compose injects. It would also defeat the PH2.1 property that application source is root-owned and unwritable by the running process. Develop against a host-run uvicorn, or rebuild. *Still open after PH2.3* — that sprint's scope was secret delivery, and it made the dotenv interaction sharper rather than resolving it: a bind-mounted `.env` would now be a competing source that the loader refuses to boot on. The reload workflow needs the dotenv precedence question answered on its own terms.

**L3 — Secrets are plaintext on disk and visible in `docker inspect`.** ✅ **Addressed by PH2.3.** `docker-compose.secrets.yml` is an opt-in overlay that delivers credentials as file-mounted Docker secrets, so `docker inspect` shows `JWT_SECRET_FILE=/run/secrets/jwt_secret` — a path — rather than the credential. The base stack described in this document still uses `.env` + `production.env` and is unchanged, deliberately: the migration is gradual and nothing breaks on upgrade. Residual exposure under plain Compose (secret files are still plaintext on the host disk) and two partial cases (MongoDB's app-user credentials, Redis's `requirepass`) are documented as L1–L3 in [SECRETS.md](SECRETS.md) §8. Swarm removes the disk exposure with no file change.

**L4 — Single-node MongoDB, no replica set.** Transactions and change streams are unavailable, and `retryWrites` / `w=majority` are deliberately omitted from `MONGO_URL` rather than advertising semantics the deployment cannot honour. Production uses a managed cluster; this stack is the local equivalent, not the production topology.

**L5 — No automated backups.** `down -v` destroys everything with no recovery path. Backup and restore is **PH2.10**.

**L6 — The frontend is not in the stack.** It has no Dockerfile — the pre-PH2.2 compose file referenced `frontend/Dockerfile`, which has never existed in this repository, so that service could never have built. The frontend production image is its own roadmap sprint. The previous file is recoverable with `git show HEAD:docker-compose.yml`.

**L7 — `read_only: true` is not enabled** for the backend, though the container would otherwise support it. Several data-science dependencies write to a cache directory under `$HOME` (`/app`) on first use, and a failure there would surface as a market-data outage at runtime rather than a startup error — the worst possible failure shape. Enabling it requires enumerating the writable paths and mounting a tmpfs for each, then soaking it.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `required variable X is missing a value` | `.env` incomplete | Add `X`; see `compose.env.example` |
| `env file …/production.env not found` | Backend env file missing | `cp production.env.example production.env` and fill it |
| Backend restart-loops; logs show `✗ JWT_SECRET …` | Config rejected by `security/secrets.py` | Fix the listed variables. The container is *supposed* to fail closed here — it never reports healthy and never serves traffic |
| `bind: address already in use` on 27017 | A MongoDB already runs on the host | Set `MONGO_HOST_PORT=27018` in `.env` (this is why the port is a variable) |
| Backend: `Authentication failed` against mongo | Password changed after the volume was initialized — the init script runs **only on an empty `/data/db`** | Either rotate in place (below) or `docker compose down -v` and start clean ⚠ destroys data |
| `docker ps` shows no host mapping for a service that declares `ports:` | The container is attached only to the `internal: true` network | Attach it to `edge` as well (see §4) |
| Dev tools still running after switching to `-f docker-compose.yml` | Compose only manages services defined in the files you pass | `docker compose down` first, then bring up the production stack |
| Editing `docker/mongodb/init-app-user.js` changes nothing | Init scripts run only on first initialization | `docker compose down -v` ⚠ destroys data |
| Redis: `NOAUTH Authentication required` | Client has no password | `redis-cli -a "$REDIS_PASSWORD"`, or set `REDISCLI_AUTH` |

**Rotating the application database password without losing data:**

```bash
docker compose exec mongo mongosh --quiet \
  -u "$MONGO_ROOT_USERNAME" -p "$MONGO_ROOT_PASSWORD" --authenticationDatabase admin \
  --eval 'db.getSiblingDB("alpha_stock").changeUserPassword("stockassist_app", "<new-password>")'
# then update MONGO_APP_PASSWORD in .env and:
docker compose up -d backend
```

---

## 11. How this prepared for PH2.3 (Secrets Management) — ✅ delivered

**PH2.3 is complete; see [SECRETS.md](SECRETS.md).** The five predictions below held, with one correction worth recording: item 3 was wrong about Redis. The official redis image has **no** `_FILE` support and cannot read `requirepass` from a file — only `mongo` does. PH2.3 worked around it with a `sh -c` wrapper that reads the secret at container start, which removes the password from `docker inspect`'s `Cmd` but leaves it in `redis-server`'s argv inside that container (SECRETS.md §8, L3).

PH2.2 deliberately stopped one step short of secret management, and it did so having put the seams in the right places.

1. **The trust boundary already exists.** Infrastructure credentials and application secrets are already separate files reaching separate containers. PH2.3 changes *how each is delivered*, not who receives what — the least-privilege topology is already correct and does not need to be redesigned.

2. **Every secret is already externalized.** There is no credential anywhere in `docker-compose.yml`, only `${VAR:?}` references. Swapping the source from an env file to Docker secrets, Swarm secrets, or a platform secret manager is a change of *mechanism* at a small number of well-marked points.

3. **The `_FILE` convention is now within reach.** Both `mongo` and `redis` upstream images support reading credentials from a file path (`MONGO_INITDB_ROOT_PASSWORD_FILE`), which is what Docker secrets mount. The backend does not yet — adding `*_FILE` support to `security/secrets.py` is a concrete, well-scoped PH2.3 task, and limitation **L3** above is its acceptance criterion.

4. **The failure mode is already correct.** The stack refuses to start when a secret is missing, at two independent layers: Compose interpolation, and the application's own `validate_config()`. PH2.3 must preserve that property, and it now has verified test cases (checks 13–15 above) to preserve it against.

5. **The environment matrix is visible.** `compose.env.example` and `production.env.example` together enumerate every variable the stack consumes. PH2.4's config-drift check has a concrete pair of documents to diff against `SECRET_REGISTRY`.

---

## 12. Related documentation

- [DOCKER.md](DOCKER.md) — the backend image this stack runs (PH2.1)
- [SECRETS.md](SECRETS.md) — how credentials reach this stack: Docker Secrets, the `_FILE` convention, the central loader, rotation (PH2.3)
- [`.claude/SECRETS.md`](../../.claude/SECRETS.md) — secret inventory, rotation policy, `SECRET_REGISTRY`
- [`.claude/PRODUCTION_ROADMAP.md`](../../.claude/PRODUCTION_ROADMAP.md) — the PH2 infrastructure plan
- [`.claude/SYSTEM_ARCHITECTURE.md`](../../.claude/SYSTEM_ARCHITECTURE.md) — what is being deployed
- [Operations](../operations/README.md) — runbooks, incident response

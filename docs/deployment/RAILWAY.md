# Railway — Backend, MongoDB and Redis

> **Status: preparation only.** Nothing in this document has been deployed. Every
> step below is an instruction for an operator; no Railway project, service,
> domain or credential exists as a result of this repository change.

This deploys the **existing** PH2.1/PH3.12 backend container
(`backend/Dockerfile` → `backend/docker/entrypoint.sh` → uvicorn) unchanged.
Railway builds the same Dockerfile CI builds; the only Railway-specific file is
`backend/railway.toml`, which describes that container and adds no behaviour.

Platform facts this document relies on were checked against Railway's
documentation on 2026-09-28 (links in §14). Re-check them if much time passes.

---

## 1. Topology

```
Railway project "stockassist" (one environment: production)
├── backend   ← GitHub repo, Root Directory = backend, Dockerfile builder
│              public domain: api.<your-domain>  (HTTPS, WSS via Railway edge)
├── MongoDB   ← Railway database template (private only, no public TCP proxy)
└── Redis     ← Railway database template (private only, no public TCP proxy)

backend ──(private network: *.railway.internal)──► MongoDB, Redis
```

**One backend service, one replica, one worker.** The backend is a single
process that serves REST + the `/api/ws` WebSocket *and* runs every background
job in-process (§10). There is no separate worker, scheduler or queue service to
create. Do not scale replicas or `WEB_CONCURRENCY` above 1 (LIM-D6.7-3: each
process opens its own broker WebSocket per connected account, and brokers cap
concurrent connections).

## 2. Create the project

1. Railway dashboard → **New Project** → **Empty project**. Name it.
2. Keep one environment (`production`). A separate `staging` environment is
   recommended before public beta (§12) but is not required to deploy.

## 3. MongoDB service

1. In the project: **+ Create → Database → MongoDB**. Rename the service to
   `MongoDB` (the variable references below use this name).
2. **Do not enable a public TCP proxy.** The backend reaches it over the private
   network. Enable a proxy only temporarily for an operator task (backup/restore,
   §11) and remove it afterwards.
3. Open the service's **Variables** tab and confirm the name of the private
   connection-string variable. At the time of writing the template exposes
   `MONGO_URL` (private host `*.railway.internal`) and `MONGO_PUBLIC_URL`. Use
   the **private** one.
4. Confirm the service has a **volume** attached (the template creates one).
   Enable volume backups if your plan offers them (§11).

What the application needs from it (verified in code):

* **Connection:** `MONGO_URL` (must contain `user:password`; boot fails
  otherwise) + `DB_NAME` (the database inside the server; created on first write).
* **Topology:** a single `mongod` is sufficient. The code uses no transactions,
  sessions or change streams (`grep start_session|with_transaction|.watch(` →
  none), so a replica set is not required.
* **Initialization:** none by hand. On every boot `ensure_indexes()`
  (`server.py`) idempotently creates all indexes (~20, including the PH3.4 hot
  -path indexes and the audit-log indexes) and seeds default `feature_flags`
  when the collection is empty. The broker-account migration also runs at boot
  and is idempotent.
* **Auth source:** the Railway root user lives in `admin`; a URI with no path
  authenticates against `admin` by default, which is what the template provides.
  Do not append `/<DB_NAME>` to `MONGO_URL` — the database is selected by `DB_NAME`.

## 4. Redis service

1. **+ Create → Database → Redis**. Rename the service to `Redis`.
2. **No public TCP proxy.**
3. Confirm the private URL variable name in its **Variables** tab (at the time of
   writing: `REDIS_URL`, of the form `redis://default:<password>@<host>.railway.internal:6379`).
   It carries a password, which production validation requires.

What Redis is used for (all degrade to an in-process fallback if Redis is
unreachable — Redis is **not** a hard boot dependency, but it is a readiness
check when `REDIS_URL` is set):

| Consumer | Module |
|---|---|
| Quote / market cache (60 s TTL, `MGET` batching) | `services/cache.py`, `services/real_market.py` |
| Cross-process realtime pub/sub → WebSocket fan-out | `infrastructure/redis_pubsub.py`, `services/realtime/event_bridge.py` |
| Shared provider/feed health state | `infrastructure/health_state.py` |
| Analytics registry | `analytics/registry.py` |
| Readiness probe + `/api/diagnostics/redis` | `observability/health.py`, `observability/routes.py` |

**Not** in Redis: rate-limit counters and lockouts (MongoDB, `rate_limits`
collection — PH3.5 L-6), sessions (MongoDB), the scheduler leader lease
(MongoDB, `infrastructure/leader.py`).

Set `REDIS_MAX_CONNECTIONS=100` on the backend (PH3.5 L-1 — the default 24 is
below the app's own fan-out and trips a process-wide circuit breaker under load).

## 5. Backend service

1. **+ Create → GitHub Repo** → select this repository (the same repository
   Vercel uses; do not split it). Rename the service to `backend`.
2. **Settings → Source**
   * **Root Directory:** `backend`
   * **Branch:** the release branch (normally `main`).
3. **Settings → Config-as-code → Railway config file:** `/backend/railway.toml`
   (absolute path — Railway does *not* resolve this file relative to the Root
   Directory).
4. **Settings → Build:** builder = Dockerfile (set by `railway.toml`). Railway
   finds `Dockerfile` in the Root Directory; the build context is `backend/`, so
   `backend/.dockerignore` applies exactly as in CI. Leave "Custom Start Command"
   **empty** — the image's ENTRYPOINT validates configuration before starting
   uvicorn, and a start command would bypass that.
5. **Settings → Deploy:** replicas = **1**. Healthcheck, restart policy and
   draining are set by `railway.toml` (§8).
6. Add variables (§6), then **Deploy**.

What `backend/railway.toml` sets and why:

| Key | Value | Why |
|---|---|---|
| `build.builder` | `DOCKERFILE` | Use the certified multi-stage image, not Nixpacks/Railpack. |
| `build.watchPatterns` | `/backend/**` | Frontend-only commits do not redeploy the API. |
| `deploy.healthcheckPath` | `/api/health/ready` | See §8. |
| `deploy.healthcheckTimeout` | `300` | Cold boot builds indexes and restores broker sessions. |
| `deploy.drainingSeconds` | `30` | **Railway's default is 0 (instant SIGKILL)**, which would skip the FastAPI shutdown handler — scheduler-lease release, heartbeat stop, broker-stream close — and cut in-flight requests. 30 > uvicorn's 20 s graceful timeout. |
| `deploy.restartPolicyType` / `MaxRetries` | `ON_FAILURE` / `10` | A config error exits non-zero; ten retries then stop, rather than crash-looping forever. |

`railway.toml` is excluded from the image by `backend/.dockerignore`, so the
image's `/app` tree is identical to a build without it.

## 6. Backend variables

Authoritative list with every variable: [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md).
Minimum set for a working beta (placeholders only — generate real values locally,
paste into Railway, never commit):

```bash
# --- core (boot fails without these) ---
MONGO_URL=${{MongoDB.MONGO_URL}}          # Railway reference; confirm the var name in the MongoDB service
DB_NAME=alpha_stock
JWT_SECRET=<python -c "import secrets;print(secrets.token_urlsafe(48))">
FRONTEND_URL=https://stockassist.com
CORS_ALLOWED_ORIGINS=https://stockassist.com,https://www.stockassist.com
ANTHROPIC_API_KEY=<secret>                # and/or GOOGLE_GEMINI_KEY

# --- split-host topology (sessions do not work without these) ---
COOKIE_DOMAIN=stockassist.com
API_PUBLIC_ORIGIN=https://api.stockassist.com
TRUSTED_CLIENT_IP_HEADER=X-Real-IP

# --- Redis ---
REDIS_URL=${{Redis.REDIS_URL}}
REDIS_MAX_CONNECTIONS=100

# --- dedicated keys (warnings if absent) ---
CSRF_SECRET=<token_urlsafe(48)>
RECOVERY_SECRET=<token_urlsafe(48)>
BROKER_TOKEN_KEY=<Fernet.generate_key()>
METRICS_TOKEN=<token_urlsafe(32)>

# --- email: SendGrid (HTTPS). Outbound SMTP is blocked on Railway Free/Trial/Hobby. ---
SENDGRID_API_KEY=<secret>
EMAIL_FROM=alerts@stockassist.com
EMAIL_FROM_NAME=StockAssist
```

Do **not** set `PORT`, `HOST`, `APP_ENV`, `WEB_CONCURRENCY`, `FORWARDED_ALLOW_IPS`,
`SECRETS_DIR`, `METRICS_ALLOW_UNAUTHENTICATED`, `ENABLE_AUTO_LOGIN`,
`ADMIN_EMAIL`/`ADMIN_PASSWORD`, `MARKET_DATA_YAHOO_BASE`, `ANTHROPIC_BASE_URL`
or `DISABLE_BACKGROUND_ENGINE`.

**Why `TRUSTED_CLIENT_IP_HEADER=X-Real-IP`.** Railway's edge *appends* the real
client address to any `X-Forwarded-For` the client sent, so the leftmost hop is
attacker-controlled; Railway overwrites `X-Real-IP` and a client cannot set it.
Without this variable the rate limiter keys on the leftmost hop, and because the
login policy is `ip:account`, rotating a forged header yields unlimited password
guesses against any account (PH3.5 S-1). Verify after deploy (§9, step 6).

## 7. Private networking

* The backend reaches `MongoDB` and `Redis` at `<service>.railway.internal`,
  resolved inside the project only. The references `${{MongoDB.MONGO_URL}}` and
  `${{Redis.REDIS_URL}}` already contain those hosts.
* pymongo and redis-py resolve both IPv4 and IPv6 via `getaddrinfo`; no client
  flags are needed (Railway's `family=0` note applies to Node's ioredis only).
* The backend itself only receives traffic through Railway's public edge, so it
  keeps binding `0.0.0.0:$PORT` (entrypoint default).
* The private network is not available during the Docker **build**. Nothing in
  the build contacts a database, so this is not a constraint.

## 8. Health checks

| Endpoint | Meaning | Used by |
|---|---|---|
| `GET /api/health/live` | Process alive, event loop turning. No dependency. | Humans, external uptime monitors |
| `GET /api/health/ready` | Startup complete **and** MongoDB (+ Redis when configured) answering. 503 while booting/draining or when a dependency is down. | **Railway deploy healthcheck** (`railway.toml`) |
| `GET /api/health/startup` | Boot sequence finished. | — |
| `GET /api/health` | Human summary (status follows readiness). | Operators |
| `GET /api` | Legacy liveness (`{"status":"running"}`). | Docker `HEALTHCHECK`, CI smoke |

Railway calls the healthcheck **only while bringing up a new deployment**; it is
not continuous monitoring and the image's Docker `HEALTHCHECK` is not used by
Railway. Consequences:

* A deployment that cannot reach MongoDB/Redis never takes traffic; the previous
  deployment keeps serving. That is why the gate is `ready`, not `live`.
* After go-live, add an external uptime monitor on `/api/health/live` (and an
  alert on `/api/health/ready`) — see PRODUCTION_CHECKLIST.md.
* Railway sends the probe with `Host: healthcheck.railway.app` to `$PORT`. The
  backend has no host allowlist (no `TrustedHostMiddleware`), so this works; if
  one is ever added, allow that host.
* Health payloads are value-free in production (dependency names and pass/fail
  only — `observability.health._safe_detail`). Health paths are exempt from rate
  limiting.

## 9. Domain and verification

1. **backend → Settings → Networking → Generate Domain** first (gives
   `*.up.railway.app` for smoke-testing the API alone).
2. **Custom domain:** add `api.<your-domain>`; create the CNAME Railway shows at
   your DNS provider; wait for the certificate. Railway serves TLS 1.2+, HTTP/2,
   and WebSockets over HTTP/1.1 with no idle/duration limit on sockets.
3. Why a custom domain is **required** for browser sessions (not just nice): see
   [README.md §Domains, cookies and CORS](README.md#domains-cookies-and-cors).
4. Verify (replace the host):

```bash
API=https://api.stockassist.com
curl -fsS $API/api/health/live                 # 200 {"status":"ok",...}
curl -fsS $API/api/health/ready                # 200, checks[] all pass
curl -s -o /dev/null -w '%{http_code}\n' $API/docs          # 404 — docs hidden in production
curl -s -o /dev/null -w '%{http_code}\n' $API/openapi.json  # 404
curl -s -o /dev/null -w '%{http_code}\n' $API/api/metrics   # 403 (401 if METRICS_TOKEN set, no token)
curl -sI -H 'Origin: https://evil.example' $API/api | grep -i access-control-allow-origin  # no output
curl -sI -H 'Origin: https://stockassist.com' $API/api | grep -i access-control-allow-origin # the origin
```

5. **Logs:** Deploy logs must show `[entrypoint] Configuration OK.`, then
   `Scheduler leader election: ... ACQUIRED`, then `AlphaPartner API started
   successfully`. Any `Cookie/CORS topology:` warning line means §6's
   split-host variables are wrong — fix before pointing the frontend at it.
6. **Client-IP check** (confirms `TRUSTED_CLIENT_IP_HEADER`): send 61 anonymous
   requests to a non-exempt path with a *different* forged `X-Forwarded-For` each
   time; the 61st must be `429`. If every one succeeds, the variable is missing.

```bash
for i in $(seq 1 61); do curl -s -o /dev/null -w '%{http_code} ' \
  -H "X-Forwarded-For: 198.51.100.$i" $API/api/market/overview; done; echo
```

## 10. Background processes (all in the one backend process)

| Process | Started by | Needs | Separate Railway service? |
|---|---|---|---|
| REST API + `/api/ws` WebSocket | uvicorn (entrypoint) | Mongo, Redis (opt.) | No — this is the service |
| APScheduler: `morning_analysis` 08:30, `market_scanner` */5 9–15, `trade_monitor` * 9–15, `exit_reminder` 15:10, `eod_report` 16:00, `portfolio_snapshot` 16:05 (Asia/Kolkata, Mon–Fri) | `startup()` → `setup_scheduler`, gated by a MongoDB leader lease | Mongo, AI key | No |
| AI heartbeat engine | `startup()` | Mongo, AI key | No |
| Market broadcast loop, AI monitoring loop | `task_registry.spawn` in `startup()` | Redis (opt.) | No |
| Redis→WebSocket event bridge, Redis stats sampler | `startup()` | Redis | No |
| Broker session restore + live broker streams | `broker_engine.load_sessions()` | Mongo, broker keys | No — **must** stay single-process |
| Market gateway / Source Manager | `market_gateway.initialize()` | outbound HTTPS | No |
| n8n workflows (`/n8n`) | external, optional | `WEBHOOK_API_KEY` | **Not needed for beta.** The in-process scheduler already runs the equivalent jobs; n8n only calls the webhook routes. Leave it out initially. |

Rolling deploys: during the overlap window two processes run briefly. The
scheduler lease (MongoDB CAS) and `trading_engine.claim_exit` prevent duplicate
jobs/exit orders; broker streams may briefly be opened twice, which brokers
tolerate for seconds. This is why `drainingSeconds` matters (§5).

## 11. Backups and restore

* **Railway volume backups** (if your plan provides them) are the first line;
  schedule them on the MongoDB volume and record the restore steps in your
  runbook. Test a restore into a scratch service before beta.
* **Logical backups** with the existing tooling (`scripts/backup/`,
  `docs/operations/BACKUP_AND_RESTORE.md`) require reaching MongoDB from outside
  Railway: enable the MongoDB service's TCP proxy **temporarily**, run the backup
  against `MONGO_PUBLIC_URL` from an operator machine, then disable the proxy.
  Never leave the database publicly reachable.
* Redis holds only rebuildable cache/pub-sub state — no backup required.
* Secrets (JWT/CSRF/recovery/Fernet keys) must be backed up **separately** in a
  password manager: losing `BROKER_TOKEN_KEY` orphans every stored broker token;
  losing `JWT_SECRET` logs everyone out.

## 12. Environments and previews

Railway PR environments are **not** recommended against this backend: each
would need its own database, and broker/AI keys must not be shared with previews.
If a staging environment is wanted, create a second Railway environment with its
own MongoDB/Redis and its own secrets, and a Vercel preview origin in its
`CORS_ALLOWED_ORIGINS`.

## 13. Rollback

* **Application rollback:** backend → **Deployments** → pick the last good
  deployment → **Redeploy** (or "Rollback"). The previous image is reused; no
  rebuild. Health-gated like any deploy.
* **Configuration rollback:** variable changes create a new deployment; revert
  the variable and redeploy.
* **Data:** schema changes are additive (indexes are idempotent, no migrations
  drop fields), so rolling the app back does not require a data rollback. A data
  restore (§11) is a separate, deliberate operation.
* **Secret rotation** is not a rollback tool: rotating `JWT_SECRET` logs everyone
  out; rotating `BROKER_TOKEN_KEY` requires every user to reconnect brokers.

## 14. Known platform constraints

| Constraint | Impact | Action |
|---|---|---|
| Outbound SMTP blocked on Free/Trial/Hobby | SMTP email fails silently for users | Use `SENDGRID_API_KEY` (HTTPS) |
| Static outbound IPs are Pro-only, and are **three load-balanced, possibly shared** IPv4s | Broker **order** APIs that whitelist a single static IP may reject live orders | Paper trading is unaffected. Before live order placement: enable Static Outbound IPs and confirm with each broker that all three can be whitelisted, or egress through a dedicated static-IP proxy. |
| Healthcheck is deploy-time only | Post-deploy outages are not auto-detected | External uptime monitor |
| `drainingSeconds` default 0 | Ungraceful shutdown | Set in `railway.toml` (done) |
| Config file does not follow Root Directory | `railway.toml` ignored unless path set | Set `/backend/railway.toml` (§5.3) |
| Railway does not pass `VCS_REF`/`APP_VERSION` build args | `/api/diagnostics` reports `unknown` revision | Cosmetic. Identify the running commit from the Railway deployment page. |

Sources: Railway docs — [config as code](https://docs.railway.com/reference/config-as-code),
[monorepo](https://docs.railway.com/guides/monorepo),
[healthchecks](https://docs.railway.com/reference/healthchecks),
[deployment teardown](https://docs.railway.com/deployments/deployment-teardown),
[public networking specs](https://docs.railway.com/networking/public-networking/specs-and-limits),
[private networking](https://docs.railway.com/reference/private-networking),
[outbound networking](https://docs.railway.com/networking/outbound-networking),
[static outbound IPs](https://docs.railway.com/networking/static-outbound-ips);
Railway staff on `X-Forwarded-For`/`X-Real-IP`:
[Central Station thread](https://station.railway.com/questions/edge-proxy-x-forwarded-for-and-x-real-ip-c5a50049).

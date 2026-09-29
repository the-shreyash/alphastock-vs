# Troubleshooting — Vercel + Railway

Symptom first. Each entry names the check that distinguishes the cause.

## Backend (Railway)

### Deployment never becomes healthy; deploy log ends in `[entrypoint] FATAL` or a `[secrets]` error list
Startup validation refused the configuration (by design — the previous
deployment keeps serving). The message names every problem at once, never a
value. Common ones:

| Message | Fix |
|---|---|
| `MONGO_URL is required …` / `JWT_SECRET is required …` | Variable missing, or the `${{MongoDB.MONGO_URL}}` reference names a service/variable that does not exist (check exact service name and case). |
| `MONGO_URL carries no username:password` | Used a URL without credentials. Use the template's private URL variable. |
| `REDIS_URL has no password` | Same, for Redis. |
| `No AI provider configured` | Set `ANTHROPIC_API_KEY` and/or `GOOGLE_GEMINI_KEY`. |
| `… is half-configured` | Google OAuth / Kite / Upstox need both halves, or neither. |
| `BROKER_TOKEN_KEY is not a valid Fernet key` | Generate with `Fernet.generate_key()` (44 chars). |
| `… too short` / `looks like a placeholder` | Generate with `secrets.token_urlsafe(48)`. |
| `APP_ENV='…' is not one of …` | Remove the `APP_ENV` override (image default is `production`). |
| `LOG_LEVEL='…'` | Use `info` / `warning` / `debug`. |

### Deploy log shows many `warning: X is delivered as a plaintext environment variable`
**Expected on Railway.** The secrets loader prefers file-backed secrets (Docker
secrets), which Railway does not provide; these are advisory. Railway encrypts
variables at rest. Not an error.

### Healthcheck times out although the log says `started successfully`
* `/api/health/ready` returns 503 when a critical dependency fails. Check the log
  for Mongo/Redis connection errors; confirm both services are in the **same
  project and environment** and that the references resolve to `*.railway.internal`.
* Someone set a custom Start Command or `PORT` — remove both.

### Every deploy: scheduler silent for ~1 minute after the switch
Fixed in this preparation (`infrastructure/tasks.cancel` was missing, so the
outgoing process never released the lease). If it reappears, the deploy log of
the *old* deployment will contain `Releasing the scheduler lease failed`.
Also confirm `drainingSeconds = 30` is in effect (config path set to
`/backend/railway.toml`); with Railway's default of 0 the old process is killed
before its shutdown handler runs.

### `railway.toml` settings are ignored (healthcheck path, draining)
Railway does not look for the config file inside the Root Directory. Set
**Config file path = `/backend/railway.toml`** in the service settings.

### Build fails with `COPY requirements.txt: not found` or builds the whole repo
Root Directory is not `backend`. The Dockerfile expects `backend/` as its build
context.

### Rate-limited users who did nothing wrong / one abuser locks out everyone
`TRUSTED_CLIENT_IP_HEADER` is wrong or missing. Unset: forged `X-Forwarded-For`
values each get a fresh bucket (bypass). Set to a header Railway does not send:
every request falls back to the edge's socket address and all anonymous users
share one bucket. On Railway it must be exactly `X-Real-IP`. Verify with
[RAILWAY.md §9.6](RAILWAY.md#9-domain-and-verification).

### Session list shows the same IP for every login
Expected with the default `FORWARDED_ALLOW_IPS=127.0.0.1` — the session record
uses the socket peer (Railway's edge). Rate limiting and the audit log use
`TRUSTED_CLIENT_IP_HEADER` and are correct. Do not set `FORWARDED_ALLOW_IPS=*`
to "fix" it: that makes the recorded IP client-controlled.

### Broker orders rejected with "IP not allowed"
Broker order APIs whitelist a static IPv4. Railway egress IPs are dynamic unless
Static Outbound IPs (Pro) are enabled — and then there are three. See
[RAILWAY.md §14](RAILWAY.md#14-known-platform-constraints). Paper trading is
unaffected.

### Emails never arrive
On Railway Free/Trial/Hobby outbound SMTP is blocked. Use `SENDGRID_API_KEY`.

### Broker live feed reconnect loops / refused handshakes
More than one backend process is running (`WEB_CONCURRENCY>1` or replicas > 1).
Set both to 1 (LIM-D6.7-3).

## Frontend (Vercel)

### Build fails: `Treating warnings as errors because process.env.CI = true`
The dashboard overrides the build command, or a `CI=true` variable was added.
Remove the override (use `frontend/vercel.json`) and delete any `CI` variable.

### Build fails: `ERESOLVE unable to resolve dependency tree`
Install command overridden without `--legacy-peer-deps`. Remove the override.

### Build fails: `[verify-deploy-env] ERROR: REACT_APP_BACKEND_URL …`
The guard refused an unset / non-https / non-origin backend URL, or a
secret-shaped `REACT_APP_*` name. Fix the variable in the Vercel environment the
build ran in (Production vs Preview are separate) and redeploy.

### Refreshing a page or returning from Google sign-in shows Vercel's 404
The SPA rewrite is not applied: Root Directory is not `frontend`, so
`frontend/vercel.json` was not read.

### App loads but every request fails; Network tab shows `…/undefined/api/…`
Built without `REACT_APP_BACKEND_URL` (only possible if the guard was bypassed by
a dashboard build-command override). Set it and redeploy.

### Sign-in succeeds, then the next action logs the user out / mutations return 403 `CSRF`
The cookie topology is wrong. In order of likelihood:
1. Frontend and API are on different registrable domains (e.g. `*.vercel.app` +
   `*.up.railway.app`) — use custom domains on one domain. See
   [README.md §Domains, cookies and CORS](README.md#domains-cookies-and-cors).
2. `COOKIE_DOMAIN` unset or not a parent of both hosts.
3. The Railway deploy log has a `Cookie/CORS topology:` warning — it names the
   exact problem. Set `API_PUBLIC_ORIGIN` if it says the check could not run.

### Browser console: `blocked by CORS policy`
The page's exact origin (scheme + host, no trailing slash, `www` vs apex) is not
in `CORS_ALLOWED_ORIGINS`. Wildcards are rejected by design; preview URLs are
intentionally not allowed.

### WebSocket closes immediately (code 1006 or 1008)
* 1008: unauthenticated handshake — the session cookie did not reach
  `api.<domain>` (same causes as the CSRF entry above).
* Mixed content: `REACT_APP_BACKEND_URL` is `http://` — the guard prevents this
  on Vercel builds.

## Local verification

### `tests/test_entrypoint_log_level.py` fails with `python: command not found`
Those tests execute `docker/entrypoint.sh` on the host, which calls `python`.
Run with the venv first on `PATH`:
`PATH="$PWD/venv/bin:$PATH" ./venv/bin/python -m pytest tests/test_entrypoint_log_level.py`.

### Frontend tests time out (`Exceeded timeout of 5000 ms`) only in a full run
CPU contention (e.g. the backend suite running at the same time). Re-run alone.

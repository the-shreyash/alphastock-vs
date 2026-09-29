# Deployment

## Purpose
To document how StockAssist AI is packaged, containerized, configured and shipped to a running environment. This folder covers the *mechanics* of deployment — build artifacts, container architecture, runtime configuration and health contracts — and the **target hosted topology: frontend on Vercel, backend + MongoDB + Redis on Railway**, from one GitHub repository.

> Nothing in this folder claims a deployment exists. It is preparation: what to create, in what order, with which settings, and how to verify each step.

## Contents

**Hosted deployment (Vercel + Railway)**
- [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) — Authoritative production variable inventory, derived from the source: required/optional, secret or public, who supplies it (Railway reference, manual, Vercel), and what must never be set.
- [RAILWAY.md](RAILWAY.md) — Backend, MongoDB and Redis on Railway: service settings, `backend/railway.toml`, private networking, health gate, domain, verification, background processes, backups, rollback, platform constraints.
- [VERCEL.md](VERCEL.md) — Frontend on Vercel: what `frontend/vercel.json` pins and why, variables, domains, previews, verification, rollback.
- [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md) — Go-live checklist split into *before*, *after* and *optional for beta*.
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Symptom → cause → fix for the hosted topology.
- [DEPLOYMENT_READINESS_AUDIT.md](DEPLOYMENT_READINESS_AUDIT.md) — The audit behind this preparation: findings, severities, what was changed and what remains.

**Container and pipeline (existing, PH2/PH3)**
- [DOCKER.md](DOCKER.md) — Backend production container architecture: multi-stage build strategy, container security posture, runtime configuration, entrypoint and health-check design, build/run instructions, troubleshooting.
- [DOCKER_COMPOSE.md](DOCKER_COMPOSE.md) — Service orchestration: the production-shaped backend/MongoDB/Redis stack and its development overlay.
- [SECRETS.md](SECRETS.md) — Production secrets architecture: Docker Secrets, the `_FILE` convention, the central loader, boot-time validation, rotation.
- [GITHUB_ACTIONS.md](GITHUB_ACTIONS.md) — Continuous Integration: the workflows that verify every push and pull request. Continuous *deployment* is not implemented (see "CI/CD" below).

## Deployment order

Each step links to where it is specified. Do not skip the verification at the end of a step — every later step assumes it.

| # | Step | Where |
|---|---|---|
| 1 | **GitHub** — release commit on `main`; CI green (backend, frontend, security, dependency audit, docker build) | [PRODUCTION_CHECKLIST.md §A](PRODUCTION_CHECKLIST.md) |
| 2 | **Railway project** — create, one `production` environment | [RAILWAY.md §2](RAILWAY.md#2-create-the-project) |
| 3 | **MongoDB** — database template, private only, volume backups | [RAILWAY.md §3](RAILWAY.md#3-mongodb-service) |
| 4 | **Redis** — database template, private only | [RAILWAY.md §4](RAILWAY.md#4-redis-service) |
| 5 | **Backend service** — GitHub source, Root Directory `backend`, config path `/backend/railway.toml`, 1 replica | [RAILWAY.md §5](RAILWAY.md#5-backend-service) |
| 6 | **Environment variables** — core + split-host + Redis + dedicated keys | [RAILWAY.md §6](RAILWAY.md#6-backend-variables), [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) |
| 7 | **Healthcheck** — deploy; `/api/health/ready` gates the rollout; read deploy logs | [RAILWAY.md §8](RAILWAY.md#8-health-checks) |
| 8 | **Backend domain** — `api.<domain>` CNAME, TLS, curl verification incl. client-IP check | [RAILWAY.md §9](RAILWAY.md#9-domain-and-verification) |
| 9 | **Vercel** — import the same repo, Root Directory `frontend`, Node 22.x | [VERCEL.md §2](VERCEL.md#2-create-the-project) |
| 10 | **Frontend variables** — `REACT_APP_BACKEND_URL` (+ Google client id) | [VERCEL.md §3](VERCEL.md#3-environment-variables) |
| 11 | **CORS** — backend `CORS_ALLOWED_ORIGINS` / `FRONTEND_URL` = the Vercel custom origin(s) | [below](#domains-cookies-and-cors) |
| 12 | **Custom domains** — apex on Vercel, `api.` on Railway, same registrable domain | [below](#domains-cookies-and-cors) |
| 13 | **WebSocket** — `wss://api.<domain>/api/ws` opens (101) from the deployed SPA | [VERCEL.md §6](VERCEL.md#6-verification-after-deploy) |
| 14 | **Smoke test** — sign-up, sign-in, mutation, paper trade, AI analysis, market data, sign-out | [PRODUCTION_CHECKLIST.md §B](PRODUCTION_CHECKLIST.md) |
| 15 | **Backup verification** — a backup taken *and restored* into scratch | [RAILWAY.md §11](RAILWAY.md#11-backups-and-restore) |
| 16 | **Beta release** — invite users; uptime monitor + alert live | [PRODUCTION_CHECKLIST.md §B](PRODUCTION_CHECKLIST.md) |

OAuth providers (Google, brokers) are configured after step 12, because their registered redirect URIs must be the final custom-domain URLs.

## Architecture as deployed

```
                    INTERNET
                       │
         ┌─────────────┴──────────────┐
         ▼                            ▼
  VERCEL (frontend/)            RAILWAY edge (TLS, HTTP/2, WS)
  https://stockassist.com       https://api.stockassist.com
  static CRA bundle             │
  REACT_APP_BACKEND_URL ───────►│  backend  (backend/Dockerfile, 1 replica, 1 worker)
         browser: HTTPS + WSS   │  uvicorn → FastAPI: REST + /api/ws
                                │  in-process: APScheduler (leader lease), AI heartbeat,
                                │  broadcast loops, Redis event bridge, broker streams
                                │
                   private network (*.railway.internal)
                        ┌───────┴────────┐
                        ▼                ▼
                     MongoDB           Redis
               (system of record)  (cache, pub/sub; optional,
                                    in-memory fallback)
```

There is no separate worker, scheduler, queue or n8n service for beta — see [RAILWAY.md §10](RAILWAY.md#10-background-processes-all-in-the-one-backend-process).

## Domains, cookies and CORS

This is the one part of the topology that silently decides whether anyone can sign in, so it is spelled out.

**How the SPA authenticates (verified in code).** Login sets three cookies from the API: `access_token` and `refresh_token` (HttpOnly) and `csrf_token` (deliberately **not** HttpOnly, because the SPA must read it and echo it in `X-CSRF-Token` — `security/csrf.py`). The SPA sends requests with `withCredentials: true` (`frontend/src/services/api.js`). After the first refresh the SPA is purely cookie-authenticated, so every mutation needs the CSRF header.

**Consequence.** The SPA's page must be able to read a cookie the API set. `document.cookie` only shows cookies whose `Domain` covers the page's host. So:

| Frontend | API | Works? | Why |
|---|---|---|---|
| `stockassist.com` | `api.stockassist.com` | **Yes**, with `COOKIE_DOMAIN=stockassist.com` | Cookie scoped to the shared parent; same-site, so `SameSite=Lax` cookies flow on XHR and the WebSocket handshake |
| `app.stockassist.com` | `api.stockassist.com` | **Yes**, same settings | same |
| `<x>.vercel.app` | `<y>.up.railway.app` | **No** | Both suffixes are on the Public Suffix List, so each host is its own site: no shared cookie domain is possible (browsers reject `Domain=vercel.app`); `SameSite=Lax` cookies are withheld cross-site; `SameSite=None` would still leave `csrf_token` unreadable by the SPA, and is blocked by third-party-cookie protections in several browsers |
| `stockassist.com` | `<y>.up.railway.app` | **No** | Different registrable domains — same reasons |

The platform default hostnames are therefore fine for **smoke-testing the API alone** (curl, health, docs-hidden checks) but **custom domains on one registrable domain are required before any browser sign-in**. The backend logs a `Cookie/CORS topology:` warning at startup when these variables are incoherent (`security/cookies.py::cookie_policy_warnings`) — set `API_PUBLIC_ORIGIN` so that check can run fully.

**Backend settings for the target domains** (environment-driven; nothing is hardcoded):

```
FRONTEND_URL=https://stockassist.com
CORS_ALLOWED_ORIGINS=https://stockassist.com,https://www.stockassist.com
COOKIE_DOMAIN=stockassist.com
API_PUBLIC_ORIGIN=https://api.stockassist.com
COOKIE_SAMESITE            (unset → lax; keep it)
```

**Everything else stays as certified:** CORS is an exact-match allowlist with credentials and never `*` (`security/cors.py`); cookies are forced `Secure` in production; HSTS is on in production; API docs are 404 in production; the WebSocket authenticates by cookie or `Sec-WebSocket-Protocol` token, never a query string. There is no `TrustedHostMiddleware`; host validation is left to the edge (Railway routes only configured domains to the service).

**OAuth redirect URIs** (register exactly these after domains exist):

| Provider | Redirect URI | Lands on |
|---|---|---|
| Google | `https://stockassist.com/auth/google/callback` | SPA route (needs the Vercel rewrite) → SPA posts the code to the API |
| Zerodha | `https://api.stockassist.com/api/zerodha/callback` (`KITE_REDIRECT_URL`) | API exchanges the token server-side |
| Upstox / Angel One / Fyers / Dhan | `https://api.stockassist.com/api/brokers/<broker>/callback` (`<BROKER>_REDIRECT_URL`) | API exchanges the code, then redirects to `FRONTEND_URL/settings?broker=<broker>&…` |

Alternatively a broker app's redirect may point at the SPA route `https://stockassist.com/broker/callback` (`BrokerCallback.jsx`), which posts the token/code to the API. Either works; the API form keeps the code out of browser history. Whichever you choose, the URL registered with the broker must equal the `<BROKER>_REDIRECT_URL` the backend sends.

## CI/CD

Existing workflows (`.github/workflows/`: backend-ci, frontend-ci, security-audit, dependency-audit, docker-build, codeql) are compatible with this topology and are unchanged. **No automatic production deployment was added.** Railway and Vercel each build from GitHub themselves on push to the connected branch; that is the deployment trigger. To make "CI green" a precondition:

- **Railway:** service → Settings → enable **"Wait for CI"** (deploys only after GitHub checks pass on the commit).
- **Vercel:** keep production on `main` only, and protect `main` with required status checks in GitHub so nothing reaches it red.

No workflow holds a Railway or Vercel token, so no deployment credential exists in CI to leak.

## Who should read it
- Platform and DevOps Engineers
- Backend Engineers preparing a release
- Anyone running the stack outside a development machine

## Related documentation
- [Operations](../operations/README.md) — production checklist, release checklist, runbooks, incident response, backup and restore, monitoring
- [Architecture](../architecture/README.md) — what is being deployed
- [Security](../security/PH1_CERTIFICATION.md) — the application-level controls the container carries
- [PH3.12 certification](../production/PH3.12_PRODUCTION_CERTIFICATION.md) — the certified baseline this preparation builds on
- `production.env.example` (repository root) — the backend container's runtime environment template
- `compose.env.example` (repository root) — the Docker Compose stack's own variable template
- `secrets/README.md` (repository root) — the host-side Docker secret files and their generator
- `.claude/SECRETS.md` — the secret *inventory*; [SECRETS.md](SECRETS.md) here is the *mechanism*

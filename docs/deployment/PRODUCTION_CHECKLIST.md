# Production Checklist — Vercel + Railway Beta

Tick each item only when it has been **observed**, not assumed. Items marked
✅ were verified during deployment preparation on 2026-09-28 against the working
tree (see [DEPLOYMENT_READINESS_AUDIT.md](DEPLOYMENT_READINESS_AUDIT.md)); they
must be re-verified on the actual release commit.

## A. READY BEFORE DEPLOYMENT (repository and local verification)

- [ ] Git repository clean — `git status` empty on the release commit (the working tree currently also carries an unrelated `anyio` bump in `backend/requirements.txt` and an untracked `docs/business/` file: commit or drop them deliberately)
- [ ] Release commit identified and recorded (SHA)
- [x] No secrets committed — tracked files and full history swept; only synthetic load-test values exist (`scripts/load/env/loadtest.env`) ✅
- [x] Backend Docker build passes (context `backend/`, as Railway builds it) ✅
- [x] Image contains no `.env`, tests, `railway.toml`, private keys or pip; runs as uid 10001 ✅
- [x] Frontend production build passes ✅
- [x] Backend tests pass ✅ (entrypoint tests need `python` on PATH — see TROUBLESHOOTING)
- [x] Security tests pass (`pytest -m security`) ✅
- [x] Frontend tests pass ✅
- [ ] **Dependency gate passes** — Python ✅; **npm ❌ untriaged GHSA-4vpr-x523-8j87 (svgo)**. Triage or upgrade before release (audit finding F-14).
- [ ] CI green on the release commit (all six workflows)
- [x] Production-mode container boots with Railway-style injected `PORT`, reaches `/api/health/ready` = 200 with MongoDB + Redis ✅
- [x] Graceful shutdown releases the scheduler lease; a successor acquires it immediately ✅
- [x] Forged `X-Forwarded-For` cannot bypass rate limiting with `TRUSTED_CLIENT_IP_HEADER` set ✅
- [ ] Secrets generated **locally** for production (JWT, CSRF, recovery, Fernet, metrics) and stored in a password manager — never in git, chat or tickets
- [ ] Domain purchased; DNS access confirmed

## B. REQUIRED AFTER DEPLOYMENT (on the live environment)

Infrastructure
- [ ] MongoDB configured — private only, volume attached, no public TCP proxy
- [ ] Redis configured — private only, password in `REDIS_URL`
- [ ] Backend service: Root Directory `backend`, config path `/backend/railway.toml`, replicas **1**, no custom start command
- [ ] Environment variables configured per [RAILWAY.md §6](RAILWAY.md#6-backend-variables); none of the "must not be set" list present
- [ ] Deploy log shows `Configuration OK`, `ACQUIRED the lease`, `started successfully`, and **no** `Cookie/CORS topology:` warning
- [ ] Healthcheck verified — `/api/health/ready` 200 with `mongodb`, `redis`, `configuration` all `pass`
- [ ] HTTPS configured — `api.<domain>` (Railway) and apex/`www` (Vercel) certificates issued
- [ ] Backend deployed; `/docs`, `/redoc`, `/openapi.json` → 404; `/api/metrics` → 401/403 without token
- [ ] CORS configured — allowed origin gets `Access-Control-Allow-Origin` + credentials; foreign origin gets none
- [ ] Client-IP check passes (61 forged-XFF requests → final 429; [RAILWAY.md §9.6](RAILWAY.md#9-domain-and-verification))
- [ ] Frontend deployed; build log shows `[verify-deploy-env] build environment OK`
- [ ] Deep links load (`/login`, `/auth/google/callback`) — no Vercel 404

Product smoke test (on the custom domains, in a real browser)
- [ ] End-to-end authentication — register, sign in, reload (session persists), sign out; cookies on `.<domain>`
- [ ] A mutation succeeds (proves CSRF cookie readable) — e.g. add to watchlist
- [ ] OAuth configured — Google sign-in round trip (redirect URI registered at the custom domain)
- [ ] Email configured — verification / recovery email received (SendGrid on non-Pro Railway plans)
- [ ] WebSocket verified — `wss://api.<domain>/api/ws` → 101; realtime indicator connected; survives a token refresh
- [ ] Market data verified — indices/quotes populate during market hours; labelled unavailable (not zeros) outside them
- [ ] Paper trading tested — place, monitor, close a paper trade; hostile payload (negative/`Infinity` quantity) rejected with 422
- [ ] AI analysis tested — an analysis returns with provider provenance
- [ ] Background workers verified — logs show scheduler jobs firing at their IST times on a weekday; heartbeat engine running
- [ ] Monitoring/logging checked — Railway logs are JSON; an external uptime monitor on `/api/health/live` and an alert on `/api/health/ready` exist
- [ ] Backup configured — MongoDB volume backups scheduled (or logical backup procedure run)
- [ ] **Restore tested** — a backup restored into a scratch service and inspected
- [ ] Rollback procedure documented and rehearsed once (Railway redeploy of previous deployment; Vercel promote previous)

## C. OPTIONAL FOR BETA

- [ ] Broker integrations (live) — requires broker app credentials, redirect URIs, and for **order placement** a whitelisted static IPv4 (Railway Pro static outbound IPs: three, possibly shared — confirm broker acceptance). Paper trading does not need this.
- [ ] n8n automation — not required; the in-process scheduler runs the core jobs
- [ ] WhatsApp / Telegram notifications
- [ ] Alpha Vantage secondary market data
- [ ] Staging environment (separate Railway environment + DBs; Vercel preview on a staging subdomain)
- [ ] Prometheus scrape of `/api/metrics` with `METRICS_TOKEN`
- [ ] Content-Security-Policy for the SPA document (needs its own reviewed change)
- [x] CI Node 20 → 22 to match Vercel (`frontend-ci.yml`, `dependency-audit.yml`)

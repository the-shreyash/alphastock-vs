# Deployment Readiness Audit — Vercel + Railway (2026-09-28)

**Baseline:** branch `d61-security-p0` at `b328e79` (post-PH3.12 work through
D6.10-P0), working tree. **Scope:** preparation for frontend-on-Vercel,
backend+MongoDB+Redis-on-Railway from one repository. No cloud resource was
created; no deployment was performed.

**Method:** inspect → identify → minimal change → test. Platform behaviour was
checked against current Railway/Vercel documentation (sources in RAILWAY.md /
VERCEL.md), and every runtime claim below was either read in code or observed on
a production-mode container built from this tree.

## 1. Deployment map (as discovered)

| Item | Value |
|---|---|
| Frontend entry / build | CRA 5 via craco (`frontend/src/index.js`); `craco build` → `frontend/build/` (static) |
| Frontend config | `REACT_APP_BACKEND_URL` (REST base + derived WSS URL), `REACT_APP_GOOGLE_CLIENT_ID`; build-time only |
| Backend entry | `backend/docker/entrypoint.sh` → validate config (`security.secrets.validate_config`) → `uvicorn server:app` |
| Backend Dockerfile / context | `backend/Dockerfile`, multi-stage, context `backend/`, `backend/.dockerignore` |
| Bind / port | `0.0.0.0:${PORT:-8000}`; one worker (`WEB_CONCURRENCY=1`) |
| MongoDB | Motor client from `MONGO_URL` + `DB_NAME`; indexes idempotently at startup; no transactions/change streams |
| Redis | `infrastructure/redis_client.py` from `REDIS_URL` (optional; in-memory fallback; readiness check when set) |
| Workers / schedulers | None separate. APScheduler (6 IST cron jobs, MongoDB leader lease), heartbeat engine, broadcast/monitoring loops, Redis event bridge, broker streams — all in the API process |
| WebSocket | `GET /api/ws` on the same process; auth by cookie or `Sec-WebSocket-Protocol` |
| Health | `/api/health/{live,ready,startup}`, `/api/health`, legacy `/api` |
| CORS | `security/cors.py` exact-match allowlist from `CORS_ALLOWED_ORIGINS` (+ legacy `CORS_ORIGINS`, `FRONTEND_URL`), credentials, never `*` |
| Cookies / CSRF | `security/cookies.py` (`COOKIE_DOMAIN`, `COOKIE_SAMESITE`, Secure forced in prod), signed double-submit CSRF |
| Secrets | `security/secrets.py` registry; env, `<NAME>_FILE`, or `/run/secrets`; fail-fast validation |
| Compose services | `backend`, `mongo`, `redis` (`docker-compose.yml` + override + secrets overlay) — local/self-host only |
| CI | backend-ci, frontend-ci, security-audit, dependency-audit, docker-build, codeql; no CD |

## 2. Findings

Status: **FIXED** (code/config change in this pass, tested) · **CONFIG** (resolved by
documented operator configuration) · **OPEN** (remains; action in §4).

| ID | Sev. | Finding | Status |
|---|---|---|---|
| F-01 | BLOCKER | No SPA fallback on Vercel: direct loads of client routes 404, including `/auth/google/callback` and `/broker/callback` → Google sign-in and SPA broker callback broken. | **FIXED** — `frontend/vercel.json` rewrite (excludes `/static/`) |
| F-02 | BLOCKER | Vercel sets `CI=1`; react-scripts then fails the build on 62 pre-existing ESLint warnings (CI builds with `CI` unset for this reason). | **FIXED** — `buildCommand … CI=false npm run build` |
| F-03 | BLOCKER | Default install fails: npm peer conflict (eslint 9 vs react-scripts' eslint 8) needs `--legacy-peer-deps`; both `package-lock.json` and `yarn.lock` present → package-manager ambiguity. | **FIXED** — `installCommand: npm ci --legacy-peer-deps` |
| F-04 | BLOCKER | PH3.5 S-1 on Railway: `client_ip()` trusts the leftmost `X-Forwarded-For`; Railway appends to client-supplied XFF, so the leftmost hop is attacker-controlled. Login is keyed `ip:account` → unlimited password guessing per account by rotating the header. `FORWARDED_ALLOW_IPS` does not help (the raw header is read). | **FIXED (opt-in)** — `TRUSTED_CLIENT_IP_HEADER` in `security/rate_limit.py`, shared by `security/audit.py`; inert when unset. **Must be set to `X-Real-IP` on Railway.** Verified live: 62 rotated forged XFF → 60× 404, 2× 429. |
| F-05 | HIGH | Graceful shutdown never released the scheduler lease: `LeaderLease.stop()` called `infrastructure.tasks.cancel`, which did not exist (AttributeError swallowed as a warning). Every rolling deploy left up to ~60 s with no scheduler (`trade_monitor` included). Found by stopping a production container. | **FIXED** — module-level `tasks.cancel`; hermetic regression test (red on old code); verified live: successor ACQUIRES on boot |
| F-06 | HIGH | Railway `drainingSeconds` defaults to 0 → SIGKILL right after SIGTERM → shutdown handler (lease release, heartbeat stop, broker stream close) never runs; in-flight requests cut. | **FIXED** — `backend/railway.toml` `drainingSeconds = 30` (> uvicorn 20 s) |
| F-07 | HIGH | Unset `REACT_APP_BACKEND_URL` builds successfully into a bundle calling `undefined/api`; nothing prevents a secret in a `REACT_APP_*` variable. | **FIXED** — `frontend/scripts/verify-deploy-env.js` (hosted builds only) + tests |
| F-08 | HIGH | Browser sessions cannot work on platform default domains (`*.vercel.app` + `*.up.railway.app`): no shared cookie domain is possible, the SPA cannot read `csrf_token`, `SameSite=Lax` cookies are withheld cross-site. Architectural property of the certified cookie/CSRF design, not a bug. | **CONFIG** — custom domains on one registrable domain + `COOKIE_DOMAIN` + `API_PUBLIC_ORIGIN` (README §Domains) |
| F-09 | HIGH | `production.env.example` lacked `COOKIE_DOMAIN`, `API_PUBLIC_ORIGIN`, `TRUSTED_CLIENT_IP_HEADER`, `METRICS_TOKEN`. | **FIXED** — template section added (commented, placeholders) |
| F-10 | HIGH | npm dependency gate red: GHSA-4vpr-x523-8j87 (svgo 1.3.2 / 2.8.3, via react-scripts' build toolchain) is untriaged. New advisory; lockfile untouched. CI `dependency-audit` will fail. | **OPEN** |
| F-11 | MEDIUM | Railway config-as-code file does not follow Root Directory. | **CONFIG** — config path `/backend/railway.toml` (RAILWAY.md §5) |
| F-12 | MEDIUM | Outbound SMTP blocked on Railway Free/Trial/Hobby. | **CONFIG** — use `SENDGRID_API_KEY` |
| F-13 | MEDIUM | Broker order APIs whitelist a static IPv4; Railway static egress is Pro-only and is three load-balanced, possibly shared IPs. | **OPEN** (live orders only; paper trading unaffected) |
| F-14 | MEDIUM | Node 20 (CI) is deprecated on Vercel from 2026-10-01; Vercel default is 24.x. | **CONFIG** — Vercel Node 22.x; **CLOSED** — CI moved to Node 22 (`frontend-ci.yml`, `dependency-audit.yml`) |
| F-15 | MEDIUM | WebSocket handshake has no `Origin` allowlist; relies on `SameSite=Lax` to prevent cross-site socket hijacking. Safe with the documented config; unsafe if `COOKIE_SAMESITE=none` is ever set. | **CLOSED** — `/api/ws` now validates `Origin` against the CORS allowlist (`security.cors.is_allowed_websocket_origin`) before authentication; foreign and `null` origins close 1008. Tests: `backend/tests/test_ws_origin.py` (9/9 mutants killed). |
| F-16 | MEDIUM | Railway healthcheck is deploy-time only; no continuous liveness monitoring. | **CONFIG** — external uptime monitor (checklist) |
| F-17 | LOW | `REDIS_MAX_CONNECTIONS` default 24 (PH3.5 L-1). | **CONFIG** — set 100 |
| F-18 | LOW | Session records store `request.client.host` = Railway edge IP with default `FORWARDED_ALLOW_IPS`. Cosmetic; `*` would make it spoofable. | Documented |
| F-19 | LOW | No CSP / security headers on the SPA document. | Partially **FIXED** (nosniff, frame-deny, referrer policy in `vercel.json`); CSP **OPEN** (optional) |
| F-20 | INFO | Railway does not pass `VCS_REF`/`APP_VERSION` build args → `/api/diagnostics` revision `unknown`. | Documented |
| F-21 | INFO | `docs/infrastructure/PH2_CERTIFICATION.md` states `FORWARDED_ALLOW_IPS=127.0.0.1` prevents XFF-based rate-limit bypass; it never did (F-04). | Documented here |
| F-22 | INFO | Angel One adapter sends `X-ClientLocalIP`/`X-ClientPublicIP: 127.0.0.1`. May matter to SmartAPI order acceptance; outside deployment scope. | Documented |
| F-23 | INFO | Uncommitted pre-existing change in the tree: `backend/requirements.txt` anyio 4.13.0 → 4.14.2 (not from this pass). All verification ran with it. | Owner decision |

**Checked and clean:** no hardcoded production URLs; every `localhost` in runtime
code is dev-gated or unreachable in production (§3); no committed secrets in
tracked files or history (only synthetic `scripts/load/env/loadtest.env`);
`.gitignore` covers `.env`, `.env.*`, `*.env`, `*.pem`, `*.key`, credentials,
backups, with example templates re-included; image contains no `.env`, tests,
dev requirements, pip, `railway.toml` or private keys, runs as uid 10001; API
docs 404 in production; metrics gated; health payloads value-free; CORS exact
match; cookies Secure/HttpOnly/SameSite=Lax with correct `Domain`; HSTS on;
`Server` header suppressed; no debug mode or `--reload`; binds `0.0.0.0` and
honours injected `PORT`; no `TrustedHostMiddleware` to trip Railway's
`healthcheck.railway.app` probe; no test-only services loaded in production
(`DISABLE_BACKGROUND_ENGINE`/`MARKET_DATA_YAHOO_BASE` unset by default).

## 3. URL / port audit (runtime code; tests and docs are allowed and unchanged)

| Occurrence | Class | Verdict |
|---|---|---|
| `security/cors.py` `DEFAULT_DEV_ORIGINS` localhost:3000/5173 | dev default | Only when not production and nothing configured |
| `server.py` Google redirect allowlist adds `http://localhost:3000` | dev default | Only when `APP_ENV != production` |
| `server.py::_frontend_base` fallback `http://localhost:3000` | fallback | Unreachable in production (`FRONTEND_URL` required) |
| `security/secrets.py` registry `example=` localhost values | documentation | Never used as values |
| `security/secrets.py` `LOOPBACK_HOSTS` | validation | Warns if production Mongo is loopback |
| `docker/entrypoint.sh` `HOST=0.0.0.0`, `PORT=8000` defaults | runtime | Correct; Railway overrides `PORT` |
| `docker/healthcheck.sh` `127.0.0.1:${PORT}` | in-container probe | Correct (Docker only; Railway uses HTTP path) |
| `services/brokers/angelone.py` `127.0.0.1` headers | broker API header | F-22 |
| `frontend/.env.example` `http://localhost:8000` | dev template | Correct |
| `railway.app` / `vercel.app` | — | No occurrences in runtime code |

## 4. Remaining actions

See the readiness report delivered with this change and PRODUCTION_CHECKLIST.md.
In short: triage or upgrade F-10 before release; set the Railway/Vercel
configuration exactly as documented (F-04, F-06, F-08, F-11, F-12, F-14, F-17);
F-13 before enabling live broker orders; F-14 CI follow-up; F-15 and F-19 CSP as
security backlog items.

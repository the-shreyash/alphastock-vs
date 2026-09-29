# Environment Variables — Production Reference (Railway + Vercel)

**Scope.** Every environment variable the shipped code reads, established by
sweeping the source (not by copying `.env.example`): `os.environ` / `os.getenv`
reads, helper-function reads (`_mongo_int`, `_env_flag`, `_policy`, broker
`redirect_url_env=`), `*_ENV = "NAME"` constants, the secrets registry
(`backend/security/secrets.py::SECRET_REGISTRY`), the entrypoint and health
scripts, and every `process.env.*` in `frontend/`. Test-only, CI-only and
load-test-only variables are excluded (§8 lists them so their absence is
deliberate).

**Rules that apply to every row.**

* Never put a real value in this file, in any `*.example`, or in git.
* **Secret** = the value grants access to something. Secrets live only in the
  Railway service's Variables (or a Docker secret file); never in Vercel.
* **Anything named `REACT_APP_*` is published to every browser.** The hosted
  build refuses secret-shaped `REACT_APP_*` names (`frontend/scripts/verify-deploy-env.js`).
* The backend **refuses to start** in production if a "Required" row is
  missing or invalid — the entrypoint runs `security.secrets.validate_config()`
  before uvicorn and prints an aggregated, value-free report. That is the
  intended failure mode: the Railway deployment never becomes healthy and the
  previous one keeps serving.
* Any secret may alternatively be supplied as `<NAME>_FILE=/path` (Docker
  secrets). On Railway, use plain variables.

Legend — **Source**: `Manual` (you paste it), `Railway ref` (a `${{Service.VAR}}`
reference to another Railway service), `Railway` (injected by the platform),
`Image` (baked into the Docker image default), `Vercel` (set in the Vercel project).

---

## 1. Backend — required in production (boot fails without them)

| Variable | Secret | Purpose | Safe example / format | Source | Consumer |
|---|---|---|---|---|---|
| `APP_ENV` | no | Environment posture. `production` forces Secure cookies, hides API docs, gates metrics, enforces all rules below. | `production` | Image (default `production`) — do not override | everything |
| `MONGO_URL` | **yes** | MongoDB connection URI. Must carry `user:password` (boot error otherwise). | `mongodb://USER:CHANGE_ME@mongodb.railway.internal:27017` | Railway ref `${{MongoDB.MONGO_URL}}` | `server.py` Motor client, backup scripts |
| `DB_NAME` | no | Database name inside the cluster. | `alpha_stock` | Manual | `server.py` |
| `JWT_SECRET` | **yes** | HS256 signing key for access/refresh JWTs; fallback HMAC key for CSRF and recovery tokens. ≥ 32 chars, entropy-checked. | generate: `python -c "import secrets;print(secrets.token_urlsafe(48))"` | Manual | `security/jwt.py`, `csrf.py`, `recovery.py` |
| `FRONTEND_URL` | no | Public SPA origin: OAuth redirect allowlist, links in email, broker callback return. Exact origin, no trailing slash. | `https://stockassist.com` | Manual | `server.py`, `security/cors.py` |
| `CORS_ALLOWED_ORIGINS` | no | Exact-match CORS allowlist, comma-separated. `*` is stripped. **Also the WebSocket `Origin` allowlist** — a browser handshake to `/api/ws` from an origin not listed here (or in `FRONTEND_URL`) is closed with 1008. | `https://stockassist.com,https://www.stockassist.com` | Manual | `security/cors.py` |
| `ANTHROPIC_API_KEY` **or** `GOOGLE_GEMINI_KEY` | **yes** | At least one AI provider is required in production. | `CHANGE_ME` | Manual | AI services |

## 2. Backend — required for a working split (Vercel + Railway) deployment

Not enforced at boot, but browser sessions do not work without them when the
SPA and API are on different hosts. See [README.md §Domains, cookies and CORS](README.md#domains-cookies-and-cors).

| Variable | Secret | Purpose | Example | Source | Consumer |
|---|---|---|---|---|---|
| `COOKIE_DOMAIN` | no | Parent domain for auth + CSRF cookies so the SPA host can read `csrf_token` and the API host receives the session. | `stockassist.com` | Manual | `security/cookies.py`, `csrf.py` |
| `API_PUBLIC_ORIGIN` | no | This API's public origin. Declarative; enables the full startup cookie/CORS topology check (`cookie_policy_warnings`). | `https://api.stockassist.com` | Manual | `security/cookies.py` |
| `TRUSTED_CLIENT_IP_HEADER` | no | Header the edge proxy overwrites and a client cannot forge. On Railway: `X-Real-IP`. Without it, rate-limit identity comes from the client-controlled leftmost `X-Forwarded-For` hop (PH3.5 S-1). | `X-Real-IP` | Manual | `security/rate_limit.py`, `security/audit.py` |
| `REDIS_URL` | **yes** | Shared cache, cross-process pub/sub, event bus. Optional in code (in-memory fallback), **recommended**. Must include a password in production. | `redis://default:CHANGE_ME@redis.railway.internal:6379` | Railway ref `${{Redis.REDIS_URL}}` | `infrastructure/redis_client.py` |

## 3. Backend — strongly recommended secrets (warnings when missing)

| Variable | Secret | Purpose | Example | Source |
|---|---|---|---|---|
| `CSRF_SECRET` | **yes** | Dedicated CSRF HMAC key (else derived from `JWT_SECRET`). ≥ 32. | generate like `JWT_SECRET` | Manual |
| `RECOVERY_SECRET` | **yes** | Dedicated identity-recovery token key. ≥ 32. | generate like `JWT_SECRET` | Manual |
| `BROKER_TOKEN_KEY` | **yes** | Fernet key encrypting stored broker tokens. Invalid format = boot error. **Rotating it orphans every stored broker token.** | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` | Manual |
| `METRICS_TOKEN` | **yes** | Bearer token for `/api/metrics`, `/api/diagnostics*` (403 in production until set). ≥ 32. | `python -c "import secrets;print(secrets.token_urlsafe(32))"` | Manual |
| `WEBHOOK_API_KEY` | **yes** | Authenticates inbound automation webhooks (n8n). Unset = webhooks disabled. | `CHANGE_ME` | Manual |

## 4. Backend — integrations (all optional; each pair must be both-or-neither)

| Variable | Secret | Purpose | Example |
|---|---|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | id: no / secret: **yes** | Google sign-in. Half-configured = boot error in production. Redirect URI registered in Google Cloud: `${FRONTEND_URL}/auth/google/callback`. | `123.apps.googleusercontent.com` / `CHANGE_ME` |
| `ALPHA_VANTAGE_KEY` | **yes** | Secondary market-data provider. | `CHANGE_ME` |
| `KITE_API_KEY` / `KITE_API_SECRET` / `KITE_REDIRECT_URL` | **yes** / **yes** / no | Zerodha. Redirect is an **API** URL. | `…/`, `https://api.stockassist.com/api/zerodha/callback` |
| `UPSTOX_API_KEY` / `UPSTOX_API_SECRET` / `UPSTOX_REDIRECT_URL` | **yes** / **yes** / no | Upstox. | `https://api.stockassist.com/api/brokers/upstox/callback` |
| `ANGELONE_API_KEY` / `ANGELONE_REDIRECT_URL` | **yes** / no | Angel One SmartAPI. | `https://api.stockassist.com/api/brokers/angelone/callback` |
| `FYERS_APP_ID` / `FYERS_SECRET_ID` / `FYERS_REDIRECT_URL` | **yes** / **yes** / no | Fyers. | `https://api.stockassist.com/api/brokers/fyers/callback` |
| `DHAN_PARTNER_ID` / `DHAN_PARTNER_SECRET` / `DHAN_REDIRECT_URL` | **yes** / **yes** / no | Dhan. | `https://api.stockassist.com/api/brokers/dhan/callback` |
| `BROKER_FORCE_IPV4` | no | Pin broker egress to IPv4 (order APIs whitelist IPv4). Default `true`. | `true` |
| `SENDGRID_API_KEY` | **yes** | Email over HTTPS. **Use this on Railway Free/Trial/Hobby — outbound SMTP is blocked on those plans.** | `CHANGE_ME` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | user+password **yes** | SMTP email (Railway Pro plan only). | `smtp.example.com` / `587` |
| `EMAIL_FROM` / `EMAIL_FROM_NAME` | no | Sender identity. | `alerts@stockassist.com` / `StockAssist` |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM` / `USER_WHATSAPP_TO` | SID+token **yes** | WhatsApp notifications. | — |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | token **yes** | Telegram notifications. | — |

## 5. Backend — runtime / process (defaults are production-correct)

| Variable | Default | Railway guidance |
|---|---|---|
| `PORT` | `8000` (image) | **Injected by Railway**; the entrypoint binds to it. Do not set. |
| `HOST` | `0.0.0.0` (image) | Do not set. |
| `WEB_CONCURRENCY` | `1` | **Must stay `1`** (LIM-D6.7-3: broker WebSockets are per process). Keep replicas at 1 too. |
| `LOG_LEVEL` | `info` | `info` (case-insensitive; validated by the entrypoint). |
| `LOG_FORMAT` | `json` in production | Leave default. Railway ingests stdout. |
| `LOG_TO_FILES` | `0` | Leave `0` — Railway has no persistent log volume; stdout is the log. |
| `LOG_SCRUB_MESSAGES`, `LOG_HEALTH_REQUESTS`, `LOG_DIR`, `LOG_FILE_*`, `LOG_RETENTION_DAYS`, `LOG_QUEUE_SIZE` | see `docs/operations/LOGGING.md` | Leave defaults. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Leave default. Setting `*` would make uvicorn's `request.client` the client-controlled leftmost hop. Rate limiting and audit use `TRUSTED_CLIENT_IP_HEADER` instead. Consequence: session-list IPs show Railway's edge address (LOW, cosmetic). |
| `TIMEOUT_GRACEFUL_SHUTDOWN` | `20` | Keep below `drainingSeconds` in `backend/railway.toml` (30). |
| `TIMEOUT_KEEP_ALIVE` | `5` | Default. |
| `SECRETS_DIR`, `REQUIRE_FILE_SECRETS` | `/run/secrets`, `false` | Leave unset on Railway (plain variables). |
| `MONGO_MAX_POOL_SIZE`, `MONGO_MIN_POOL_SIZE`, `MONGO_MAX_IDLE_TIME_MS`, `MONGO_SERVER_SELECTION_TIMEOUT_MS`, `MONGO_CONNECT_TIMEOUT_MS`, `MONGO_SOCKET_TIMEOUT_MS` | pymongo defaults (100 / 0 / 60000 / 30000 / 20000 / none) | Defaults. |
| `MONGO_COMMAND_METRICS` | `1` | Default. |
| `REDIS_MAX_CONNECTIONS` | `24` | **Set `100`**. PH3.5 L-1: 24 is below the app's own fan-out; exhausting it opens a process-wide breaker. |
| `REDIS_CONNECT_TIMEOUT_SECONDS`, `REDIS_SOCKET_TIMEOUT_SECONDS`, `REDIS_HEALTH_CHECK_INTERVAL_SECONDS`, `REDIS_RETRY_ATTEMPTS`, `REDIS_CIRCUIT_FAILURE_THRESHOLD`, `REDIS_CIRCUIT_RESET_SECONDS`, `REDIS_STATS_INTERVAL_SECONDS` | 1.5 / 2.0 / 30 / 2 / 5 / 10 / 30 | Defaults. |
| `HEALTH_PROBE_TIMEOUT_SECONDS`, `HEALTH_CACHE_TTL_SECONDS` | 2.0 / 2.0 | Defaults. |
| `METRICS_ALLOW_UNAUTHENTICATED` | unset | **Never set on Railway** — the API is public. |
| `METRICS_MAX_SERIES` | `500` | Default. |
| `RATE_LIMIT_LOGIN`, `RATE_LIMIT_REGISTER`, `RATE_LIMIT_REFRESH`, `RATE_LIMIT_PASSWORD`, `RATE_LIMIT_API_USER`, `RATE_LIMIT_API_IP` | 5/900, 5/3600, 20/60, 5/3600, 120/60, 60/60 (`limit/window_seconds`) | Defaults. |
| `JWT_ACCESS_TTL_SECONDS`, `JWT_REFRESH_TTL_SECONDS`, `JWT_REFRESH_GRACE_SECONDS`, `JWT_ISSUER`, `JWT_AUDIENCE` | see `security/jwt.py` | Defaults. Changing issuer/audience invalidates live sessions. |
| `RECOVERY_VERIFY_TTL_SECONDS`, `RECOVERY_RESET_TTL_SECONDS` | see `security/recovery.py` | Defaults. |
| `COOKIE_SECURE` | forced `true` in production | Ignored in production. |
| `COOKIE_SAMESITE` | `lax` | **Keep `lax`** with a shared registrable domain. `none` is only for a genuinely cross-site split and weakens the WebSocket's SameSite protection. |
| `HSTS_ENABLE`, `HSTS_MAX_AGE`, `HSTS_INCLUDE_SUBDOMAINS`, `HSTS_PRELOAD` | on in production | Defaults. Do **not** enable `HSTS_PRELOAD` until every subdomain of the apex serves HTTPS. |
| `CONTENT_SECURITY_POLICY`, `X_FRAME_OPTIONS`, `REFERRER_POLICY`, `PERMISSIONS_POLICY`, `CROSS_ORIGIN_OPENER_POLICY`, `CROSS_ORIGIN_EMBEDDER_POLICY`, `CROSS_ORIGIN_RESOURCE_POLICY` | hardened defaults (`security/headers.py`) | Defaults. |
| `CORS_ORIGINS` | — | Legacy alias of `CORS_ALLOWED_ORIGINS`. Do not use. |
| `APP_VERSION`, `VCS_REF`, `BUILD_DATE` | `0.0.0-dev`, `unknown`, `unknown` | Build args. Railway does not pass them; see RAILWAY.md §Provenance. |
| `MARKET_DATA_YAHOO_BASE` | unset | **Never set in production** (load-test mock origin). |
| `DISABLE_BACKGROUND_ENGINE` | unset | **Never set in production** (test-suite switch that disables the AI heartbeat engine). |

## 6. Backend — must NOT be set in production

| Variable | Why |
|---|---|
| `ENABLE_AUTO_LOGIN` | Bypasses authentication. Boot error when truthy in production. |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Development seed only. A weak `ADMIN_PASSWORD` is a boot error in production. |
| `ANTHROPIC_BASE_URL`, `MARKET_DATA_YAHOO_BASE` | Redirect provider traffic to a mock (PH3.5 load harness). |
| `PYTHON_DOTENV_DISABLED` | Unnecessary: the image contains no `.env` (`.dockerignore`). |

## 7. Frontend (Vercel) — all public

| Variable | Required | Purpose | Example | Source |
|---|---|---|---|---|
| `REACT_APP_BACKEND_URL` | **yes** | API origin. Also derives the WebSocket URL (`https→wss`, `/api/ws`). Bare origin, https, no trailing slash — enforced by the hosted-build guard. **Baked in at build time.** | `https://api.stockassist.com` | Vercel (per environment) |
| `REACT_APP_GOOGLE_CLIENT_ID` | no | Google client **id** (public). Must equal the backend's `GOOGLE_CLIENT_ID`. | `123.apps.googleusercontent.com` | Vercel |
| `REACT_APP_VERSION` | no | Release label attached to client error telemetry. | `2026.09.0` | Vercel (optional) |
| `CI` | — | Vercel sets `CI=1`; `vercel.json` builds with `CI=false` (see VERCEL.md). Do not add a `CI` variable. | — | Vercel system |
| `NODE_ENV` | — | Set by `react-scripts`. | — | automatic |
| `ENABLE_HEALTH_CHECK` | no | Dev-server-only webpack health plugin (`craco.config.js`). Irrelevant to builds. | unset | — |

## 8. Deliberately excluded

* **Test / CI only:** everything in `backend/tests/_testenv.py`, `frontend/src/setupTests.js`, and `.github/workflows/*` `env:` blocks.
* **Load test only:** `scripts/load/env/loadtest.env` (committed, synthetic, never sourced by the app).
* **Docker Compose only:** `compose.env.example` (`MONGO_ROOT_*`, `REDIS_PASSWORD`, host ports, image tags). Railway manages its own database credentials.
* **Backup scripts:** `scripts/backup/*` read their own variables; see `docs/operations/BACKUP_AND_RESTORE.md`.

## 9. Keeping this file true

`security/secrets.py::SECRET_REGISTRY` is the authoritative list for secrets
and boot-time requirements; `backend/.env.example` is generated from it
(`backend/scripts/generate_env_example.py`). Variables in §2 and §5 that are not
in the registry (`COOKIE_*`, `API_PUBLIC_ORIGIN`, `TRUSTED_CLIENT_IP_HEADER`,
`HSTS_*`, `JWT_*_TTL`, `MONGO_*` pool, `RATE_LIMIT_*`) are read directly by
their owning module. To re-derive the list, run the sweep described at the top
of this file.

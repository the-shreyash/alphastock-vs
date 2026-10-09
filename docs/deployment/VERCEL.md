# Vercel — Frontend

> **Status: preparation only.** No Vercel project or domain has been created by
> this repository change.

## 1. What is being deployed

| Fact | Value (verified in the repository) |
|---|---|
| Framework | **Create React App** (`react-scripts` 5) via **craco** — *not* Vite. Client variables are `REACT_APP_*`, not `VITE_*`. |
| Root | `frontend/` in the same repository as the backend |
| Package manager | **npm** (`package-lock.json`). A stale `yarn.lock` also exists; `vercel.json` pins `npm ci` so Vercel cannot pick yarn. |
| Install | `npm ci --legacy-peer-deps` (same as `frontend-ci.yml`) |
| Build | `node scripts/verify-deploy-env.js && CI=false npm run build` |
| Output | `build/` (static; no serverless functions) |
| Routing | `BrowserRouter`; every unknown path must serve `index.html` |
| Backend URL | `REACT_APP_BACKEND_URL`, baked in at build time; WebSocket URL derived from it |

All of the above is committed in **`frontend/vercel.json`**, so the Vercel
project needs almost no dashboard configuration and the settings are reviewed in
git. `src/__tests__/deployConfig.test.js` pins each property.

### Why each `vercel.json` property exists

| Property | Without it |
|---|---|
| `installCommand: npm ci --legacy-peer-deps` | npm's strict peer resolution rejects eslint 9 alongside react-scripts' eslint 8 and the install fails; or Vercel detects `yarn.lock` and installs different versions from CI. |
| `buildCommand: … CI=false npm run build` | Vercel sets `CI=1`; react-scripts then treats the 62 pre-existing ESLint warnings as errors and the build fails. `frontend-ci.yml` builds with `CI` unset for the same reason. |
| `node scripts/verify-deploy-env.js &&` | A missing `REACT_APP_BACKEND_URL` builds "successfully" into a bundle that calls `undefined/api`. A secret in a `REACT_APP_*` variable is published to every visitor. The guard refuses both (also: non-https, trailing slash, path). |
| `rewrites: /((?!static/).*) → /index.html` | Direct loads/refreshes of client routes 404 — including the OAuth landing routes `/auth/google/callback` and `/broker/callback`, i.e. Google sign-in and broker connect break. `/static/*` is excluded so a missing hashed chunk 404s instead of returning HTML. |
| `headers` | Immutable caching for hashed `/static/*`; `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy` on the document. (A CSP for the SPA document is deliberately *not* added here — `public/index.html` has an inline script and Google Fonts; a CSP needs its own reviewed change.) |

The guard runs **only** from `vercel.json`; `npm run build`, local development
and CI are unchanged.

## 2. Create the project

1. Vercel → **Add New… → Project** → import **this** GitHub repository.
2. **Root Directory:** `frontend` (Vercel reads `frontend/vercel.json` from here).
3. **Framework Preset:** Create React App (also declared in `vercel.json`).
4. Leave Install / Build / Output **un-overridden** in the dashboard — the values
   come from `vercel.json`. A dashboard override silently wins over the file.
5. **Node.js version:** Settings → Build and Deployment → **22.x**.
   Rationale: Vercel deprecates Node 20 on **2026-10-01**, and 24.x (Vercel's
   default) has not been exercised against react-scripts 5 in this repository.
   22.x is the conservative LTS choice, and it is what CI runs
   (`frontend-ci.yml` `NODE_VERSION`, and the npm job in `dependency-audit.yml`),
   so the build Vercel produces is the build CI tested. Keep the three in step.
6. **Ignored Build Step** (optional, saves builds): `git diff --quiet HEAD^ HEAD -- .`
   (run from `frontend/`, so backend-only commits skip the frontend build).

## 3. Environment variables

All are **public** — they end up in the JavaScript bundle. Never add a backend
secret here. Full reference: [ENVIRONMENT_VARIABLES.md §7](ENVIRONMENT_VARIABLES.md#7-frontend-vercel--all-public).

| Name | Production | Preview | Notes |
|---|---|---|---|
| `REACT_APP_BACKEND_URL` | `https://api.stockassist.com` | a **staging** API origin, or leave previews unusable (see §5) | Required. Bare https origin, no trailing slash. |
| `REACT_APP_GOOGLE_CLIENT_ID` | the public client id | same or a staging client id | Optional. Must match backend `GOOGLE_CLIENT_ID`. |
| `REACT_APP_VERSION` | release label | — | Optional (client error telemetry). |

Do **not** create a `CI` variable, and never put `MONGO_URL`, `REDIS_URL`,
`JWT_SECRET`, `*_API_SECRET`, `SENDGRID_API_KEY`, `SMTP_PASSWORD`, AI keys or
broker secrets in Vercel — the build guard refuses secret-shaped `REACT_APP_*`
names, but a secret under a non-`REACT_APP_` name is simply unused, not safe.

Changing a variable requires a **redeploy** (values are compiled in).

## 4. Domains

1. Settings → Domains → add `stockassist.com` (and `www.stockassist.com`,
   redirecting to the apex or vice versa — pick one canonical origin).
2. Both origins you serve must appear in the backend's `CORS_ALLOWED_ORIGINS`;
   the canonical one is the backend's `FRONTEND_URL`.
3. The frontend **must** share a registrable domain with the API
   (`stockassist.com` + `api.stockassist.com`). See
   [README.md §Domains, cookies and CORS](README.md#domains-cookies-and-cors) for
   why a `*.vercel.app` frontend cannot hold a session against the API.

## 5. Preview deployments

Preview URLs (`<project>-<hash>-<team>.vercel.app`) are unique per deployment and
on a different registrable domain from the API. Consequently:

* They are **not** in `CORS_ALLOWED_ORIGINS` (exact match; wildcards are
  rejected by design), so they cannot call the production API. Keep it that way —
  do not add preview origins to production CORS.
* Previews are useful for UI review of logged-out pages. For authenticated
  previews, deploy a staging backend and give previews a staging custom domain
  on the same registrable domain (e.g. `preview.stockassist.com` +
  `api-staging.stockassist.com`).

## 6. Verification after deploy

1. Open `https://stockassist.com` → landing page renders.
2. Hard-refresh `https://stockassist.com/login` and
   `https://stockassist.com/auth/google/callback` → the SPA loads (not a Vercel 404).
3. Browser DevTools → Network: API calls go to `https://api.stockassist.com/api/…`
   (never `undefined/api` or the Vercel host).
4. Sign in → Application → Cookies: `access_token`, `refresh_token`
   (HttpOnly, Secure) and `csrf_token` (not HttpOnly) under domain
   `.stockassist.com`.
5. Perform a mutation (e.g. add to watchlist) → 2xx, not 403 (proves the CSRF
   cookie is readable by the SPA).
6. WebSocket: Network → WS → `wss://api.stockassist.com/api/ws` → status 101,
   messages flowing; the realtime indicator shows connected.
7. Build log shows `[verify-deploy-env] build environment OK`.

## 7. Rollback

Deployments → select the previous production deployment → **Promote to
Production** (instant; no rebuild). Because `REACT_APP_BACKEND_URL` is baked in,
a promoted older deployment calls whatever API origin it was built with — keep
the API origin stable across releases.

Sources: [Vercel — Create React App](https://vercel.com/docs/frameworks/frontend/create-react-app),
[vercel.json](https://vercel.com/docs/project-configuration/vercel-json),
[Node.js versions](https://vercel.com/docs/functions/runtimes/node-js/node-js-versions),
[`process.env.CI = true` builds](https://vercel.com/kb/guide/how-do-i-resolve-a-process-env-ci-true-error).

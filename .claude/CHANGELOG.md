# StockAssist AI
## Changelog

This file records documentation-system versions and, from v1.0 launch onward, product release notes. Documentation versions apply to the `.claude/` documentation set as a whole.

---

# Sprint PH1.4 — CORS Hardening — 2026-07-18

**Production Hardening PH1.4 complete. Risk R-03 / finding B3 closed.**

Replaced the development-friendly, unsafe CORS configuration with a
production-safe, environment-driven, exact-match origin allowlist, and
centralized the whole policy into a single module. The prior configuration
defaulted to `Access-Control-Allow-Origin: *` **with `allow_credentials=True`**
— a combination the Fetch standard forbids (the browser refuses to expose a
credentialed response to a wildcard origin) and a security hole (any origin was
trusted with the session cookie). Frontend communication is unchanged.

Added

- `backend/security/cors.py` — the single source of truth for CORS. Resolves an
  exact-match origin allowlist from the environment and assembles the
  `CORSMiddleware` configuration. Exposes `allowed_origins()`, `cors_kwargs()`,
  and `apply_cors(app)`.
  - **Origins** — `CORS_ALLOWED_ORIGINS` is canonical (comma-separated, exact
    scheme+host+port, trailing slash normalized away). Legacy `CORS_ORIGINS`
    and `FRONTEND_URL` are still honored as inputs (backward compatible), merged
    and de-duplicated. A literal `*` is stripped from **every** source, so a
    wildcard can never enter the allowlist or pair with credentials.
  - **Development fallback** — when nothing is configured and `APP_ENV` is not
    `production`, the local dev origins `http://localhost:3000` and
    `http://localhost:5173` are assumed, so the app runs with zero config.
  - **Production fail-closed** — nothing is assumed in production; an
    unconfigured allowlist is empty and every cross-origin request is rejected.
  - **Credentials** allowed (cookie-based auth) — safe by construction because
    origins are always an exact list, never the wildcard.
  - **Methods** restricted to `GET, POST, PUT, PATCH, DELETE, OPTIONS`;
    **request headers** restricted to `Authorization, Content-Type, Accept,
    Origin, X-Requested-With`; **no response headers exposed**. Preflight cached
    for 10 minutes.
- `backend/tests/test_cors_hardening.py` — 30 hermetic tests: allowlist
  resolution (canonical var, legacy inputs, trailing-slash/whitespace
  normalization, merge+dedupe, dev defaults, production fail-closed, wildcard
  stripped from every source), assembled-kwargs invariants (never wildcard,
  credentials on, methods/headers restricted, nothing exposed), and real wire
  behavior on a live middleware (allowed-origin preflight + simple request
  reflect ACAO and `Allow-Credentials: true`; unknown origin gets no grant;
  disallowed method/header preflight rejected; localhost works out of the box;
  production rejects unconfigured localhost).
- Documented CORS env vars (`CORS_ALLOWED_ORIGINS`) in `backend/.env` and removed
  the unsafe `CORS_ORIGINS=*` line.

Changed — `backend/server.py`

- Removed the inline wildcard-defaulting `app.add_middleware(CORSMiddleware, …)`
  block (and the now-unused `CORSMiddleware` import); CORS is now wired in via
  `apply_cors(app)` from `security.cors`.

Security outcome

- No wildcard origin remains anywhere; credentials are only ever granted to
  approved, exact-match origins (R-03 / B3 closed).
- CORS configuration is centralized — no duplicated or drifting CORS logic.
- Local development continues to work unchanged; the frontend on `localhost:3000`
  is allowed with credentials.

Not in scope (deferred, unchanged)

- Security **headers** (HSTS, X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy, CSP) were de-scoped from this
  CORS-only sprint and are carried forward as PH1.4b. The Google OAuth
  redirect-URI allowlist (`_allowed_google_redirect_uris`, PH1.2 scope) is
  untouched and continues to derive from `FRONTEND_URL` / `CORS_ORIGINS`.

---

# Sprint PH1.3 — Cookie Security Hardening — 2026-07-18

**Production Hardening PH1.3 complete. Risk R-04 closed.**

Hardened every authentication-related cookie for production and centralized the
cookie policy into a single module. No change to API contracts; login, logout,
refresh and the Google OAuth flow behave identically for clients — the cookies
they receive are now consistently and safely configured. Email/password and
Google auth are functionally unchanged.

Added

- `backend/security/` package + `backend/security/cookies.py` — the single
  source of truth for issuing and clearing every auth cookie. Resolves the
  Secure/HttpOnly/SameSite/Path/Max-Age/Domain posture from the environment and
  exposes `set_auth_cookies`, `set_access_cookie`, `set_refresh_cookie`,
  `set_oauth_state_cookie`, `clear_auth_cookies`, `clear_oauth_state_cookie`.
  - **Secure** is env-driven (`COOKIE_SECURE`) and **forced `True` when
    `APP_ENV=production`** regardless of the override — a production build can
    never ship an insecure auth cookie (closes R-04). Local dev defaults to
    `False` so cookies work over plain-HTTP `localhost`.
  - **HttpOnly** always `True` on all three cookies (JS never reads them).
  - **SameSite** defaults to `Lax` (CSRF baseline); configurable via
    `COOKIE_SAMESITE` (`lax`/`strict`/`none`). `None` is auto-degraded to `Lax`
    when the cookie would not also be `Secure` (browsers drop `None` without
    `Secure`). The OAuth-state cookie is never `Strict` so it survives the
    top-level redirect back from Google.
  - **Path** — session cookies at `/` (single-shot logout, no duplicate-path
    cookies); OAuth-state cookie scoped to `/api/auth`.
  - **Domain** — optional `COOKIE_DOMAIN` for subdomain session sharing;
    host-only when unset.
  - **Clearing** mirrors the exact key + path + domain + security attributes so
    the browser reliably deletes the cookie.
- `backend/tests/test_cookie_security.py` — 24 hermetic tests: policy resolution
  (prod forces Secure; dev default/override; SameSite default/invalid/`none`
  degrade/honored; domain), login/register cookie flags, production Secure
  enforcement, dev Secure override, configured Domain, logout clears both
  cookies with matching path, refresh re-issues a hardened access cookie (and
  does **not** rotate refresh — PH1.6 owns that), session-fixation overwrite,
  and the OAuth-state cookie (hardened, scoped to `/api/auth`, never `Strict`,
  Secure in prod, burned after a successful exchange).
- Documented cookie env vars (`COOKIE_SECURE`, `COOKIE_SAMESITE`, `COOKIE_DOMAIN`)
  in `backend/.env`.

Changed — `backend/server.py`

- Removed the local `set_auth_cookies` helper (which hardcoded `secure=False`)
  and the inline `set_cookie`/`delete_cookie` literals at all four call sites;
  they now delegate to `security.cookies`:
  - `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/google/session`
    → `set_auth_cookies` (hardened flags).
  - `POST /api/auth/logout` → `clear_auth_cookies` (clears both cookies with
    attributes that match how they were set).
  - `POST /api/auth/refresh` → `set_access_cookie` (was a raw `secure=False`
    `set_cookie`).
  - `_set_oauth_state_cookie` / `_clear_oauth_state_cookie` → delegate to
    `set_oauth_state_cookie` / `clear_oauth_state_cookie`; the state cookie now
    shares the unified Secure/SameSite/Domain posture instead of a hardcoded
    `secure=False`.

Security outcome

- Every auth cookie (`access_token`, `refresh_token`, `g_oauth_state`) carries
  `HttpOnly; SameSite` always, and `Secure` in production — no token can be sent
  over plain HTTP in production (R-04 closed).
- Logout removes every authentication cookie; refresh remains functional;
  session fixation is mitigated (fresh tokens overwrite on every login/register/
  OAuth); OAuth state is burned after use.
- Cookie policy is centralized — no duplicated cookie logic remains.

Not in scope (deferred, unchanged)

- CSRF **token** middleware for cookie-authenticated state-changing routes
  (SameSite=Lax provides the cookie-layer CSRF baseline; token middleware is
  tracked as the next hardening item). Refresh-token **rotation** and JWT
  lifetime changes remain PH1.6. CORS/security headers remain PH1.4.

---

# Sprint PH1.2 — Google OAuth Production Hardening — 2026-07-17

**Production Hardening PH1.2 complete. Risk R-02 fully closed.**

Hardened the legitimate Google OAuth flow (PH1.1 had removed the backdoors; this
sprint makes the remaining real flow production-safe). All changes preserve
email/password register and login unchanged.

Added

- `GET /api/auth/google/login-url` (`backend/server.py`) — server-side flow
  initiation. Generates a cryptographically random OAuth `state`, stores a
  **single-use server-side state record** (via `services/cache.py`: Redis when
  `REDIS_URL` is set, bounded in-memory fallback otherwise) bound to the chosen
  `redirect_uri` with an authoritative 600s TTL, **and** binds the state to the
  browser via a short-lived httponly `g_oauth_state` cookie. Validates the
  requested `redirect_uri` against an allowlist and returns the Google
  authorization URL. Fail-closed (401) when `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
  are unset. The client no longer constructs the Google URL.
- Single-use state consumption on callback (fetch-and-delete) → **replay
  protection** and cross-process, TTL-authoritative expiry; the callback
  `redirect_uri` must equal the one bound at flow start.
- `log_auth_event()` + immutable `db.security_audit_logs` collection (indexed at
  startup) — records every OAuth outcome (`oauth_login_success` with
  new_account/linked flags; `oauth_login_failure` with a `reason`: invalid_state,
  replayed_or_expired_state, invalid_redirect_uri, unverified_email,
  invalid_id_token, bad_issuer, sub_conflict, missing_id_token,
  token_exchange_failed, google_unavailable). Logs ip/user-agent/outcome, never
  tokens, codes, or state values (SECURITY.md logging rule).
- `google_sub` persisted on the user document and used as the **primary external
  identity**: accounts resolve by `google_sub` first (stable across Google
  profile/email changes), then by verified email for safe linking. An email
  already bound to a different `google_sub` is rejected (`sub_conflict`).
- `frontend/src/services/googleAuth.js` — shared `startGoogleLogin()` used by
  the Login and Register pages; calls the backend for the URL (with credentials
  so the state cookie is stored) and redirects.
- `frontend/src/pages/AuthCallback.jsx` — a proper `/auth/google/callback` route
  (added in `App.js`); forwards `code` **and** `state` to the session exchange.
- `backend/tests/test_oauth_hardening.py` — 26 hermetic tests: state issuance/
  randomness, missing/forged/mismatched state, **single-use/replay rejection,
  expired-or-absent server-side record**, unverified-email rejection,
  invalid/absent id_token, bad issuer, **id_token verified with client_id as
  audience**, redirect_uri allowlist + binding, token-exchange failure, Google
  network error (502), new-user creation, safe linking of an existing password
  account (password login still works), no duplicate accounts, role/capital
  preserved, **`sub`-primary identity across an email change, `sub_conflict`
  rejection**, and **audit-log assertions** (success/linked/invalid_state/
  unverified_email; no code or state value ever persisted).

Changed — `POST /api/auth/google/session` (`backend/server.py`)

- **CSRF + replay protection:** now requires `state`, validates it (constant-time)
  against the `g_oauth_state` cookie (per-browser binding), then consumes the
  single-use server-side record (fetch-and-delete). Rejects missing/mismatched
  state and replayed/expired state with 400.
- **Identity verification:** verifies the OIDC `id_token` (signature via Google's
  public keys, audience = our client_id, expiry) using `google-auth`, checks the
  issuer, and derives identity from the verified token instead of the `/userinfo`
  endpoint. **Rejects unverified Google emails (`email_verified != true`) with 401** —
  they never create or link an account (account-takeover guard).
- **redirect_uri allowlisting:** removed the hardcoded
  `http://localhost:3000/...` fallback; the redirect_uri must be allowlisted.
- **`sub`-primary identity + safe linking:** resolves by `google_sub` first
  (stable identity), then by verified email to link an existing email/password
  account (stores `google_sub`; leaves `password_hash`/`auth_provider` intact)
  rather than silently taking it over. Email stays the unique key (no duplicates);
  an email already bound to a different `google_sub` is rejected.
- Removed the dead legacy `session_id=` hash short-circuit in `App.js` and
  `AuthContext.jsx` (leftover from the flow removed in PH1.1).

Notes

- Two PH1.1 regression assertions in `test_auth_hardening.py` were updated:
  with `state` now mandatory and checked first, a lone forged code is rejected
  with 400 (still fail-closed, still no user created); the "not configured" 401
  contract moved to `GET /api/auth/google/login-url`.
- The `g_oauth_state` cookie deliberately mirrors the existing auth cookies'
  `secure=False` posture; unifying the secure/SameSite flags across all cookies
  is PH1.3's scope, not this sprint's.

---

# Sprint PH1.1 — Authentication Backdoor Removal — 2026-07-17

**Production Hardening PH1.1 complete. Findings B1 and B2 closed; risks R-01 and R-02 closed.**

Removed

- `GET /api/auth/auto-login` endpoint and the `ENABLE_AUTO_LOGIN` switch (`backend/server.py`) — finding B1, risk R-01. Admin sessions can no longer be obtained without credentials.
- Google OAuth demo-user fallback, `mock-code-for-testing` path, and the legacy fail-open `session_id` exchange against `demobackend.emergentagent.com` (`backend/server.py`) — finding B2, risk R-02. `/api/auth/google/session` is now fail-closed: it accepts only a Google authorization code and returns 401 when `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are unset. The orphan `session_token` cookie and `user_sessions` write (never validated anywhere) are gone.
- Startup admin seeding: the server no longer creates an admin with default password `admin123`, no longer force-resets the admin password on every boot, and no longer writes plaintext credentials to `memory/test_credentials.md` (same finding class as B1; closed under PH1.1).
- Frontend callers of removed paths: `autoLogin` in `AuthContext`, the "Quick Demo Login (Dev Mode)" button on the login page, and the legacy `session_id` exchange in `AuthCallback`.

Added

- `backend/scripts/seed_dev_admin.py` — idempotent dev-only admin seeding; refuses to run when `APP_ENV=production`; never resets an existing password.
- `backend/tests/test_auth_hardening.py` — 11 hermetic tests asserting the backdoors stay removed (404 on auto-login, 401/400 on all OAuth fallback payloads, no demo user ever created) and that register → login → me → refresh → logout still works.

Changed

- Live-server test fixtures (`test_phase5/6/7.py`) authenticate via `POST /api/auth/login` with env-driven admin credentials instead of auto-login, matching `test_backend`/`test_phase2`/`test_phase4`.
- `/api/auth/google/session` response now reports the user's actual role instead of hardcoded `"user"`.

---

# Documentation v1.2 — 2026-07-17

**Feature freeze. Production Hardening program introduced.**

Added

- `PRODUCTION_HARDENING.md` — master hardening architecture document: engineering audit baseline, risk matrix (R-01…R-15), production readiness score (4.2/10), priority matrix, security/infrastructure/deployment/testing/performance/documentation/monitoring/recovery strategies, operational·launch·certification checklists, open risks report (OR-1…OR-8), engineering standards addendum, and the Definition of Production Ready.
- `PRODUCTION_ROADMAP.md` — 36-sprint implementation roadmap: PH1 Production Security Hardening, PH2 Production Infrastructure & DevOps, PH3 Production Quality Assurance (12 sprints each), with per-sprint objective, scope, deliverables, expected files, dependencies, acceptance criteria, validation steps, rollback plan, difficulty, time, and success metrics; implementation sequencing and dependency graph.
- `CHANGELOG.md` — this file.
- ADR-027 in `DECISIONS.md` — Feature Freeze & Production Hardening Program; acknowledges the as-built FastAPI + CRA stack pending PH3.10 reconciliation.

Changed

- `INDEX.md` — added Production Hardening document category, documentation-map entries, and a "Production Hardening (Current Phase)" reading guide; version 1.2.
- `ROADMAP.md` — Phase 1 and Phase 2 marked COMPLETE; Production Hardening Interlude (PH1–PH3) inserted as the current phase; product Phases 3–9 blocked until Production Certification; version 1.2.
- `TASKS.md` — Current Focus replaced with the feature freeze and PH1.1 as next sprint; full PH1–PH3 status tracker added; version 1.2.
- `DECISIONS.md` — ADR-027 added; version 1.2.

Baseline

- `PRODUCTION_READINESS_REPORT.md` (Sprint 12 audit, 2026-07-17): verdict NOT READY FOR PRODUCTION. Six critical blockers (auth backdoors ×2, CORS wildcard + credentials, insecure cookies, broken Docker packaging, no CI/CD), five high-priority findings, five medium-priority findings.

---

# Documentation v1.1 — 2026-07-16

- Introduced `MARKET_DATA_ARCHITECTURE.md`; provider-independent market data architecture (ADR-026): Market Gateway, Source Manager, provider adapters, priority and failover strategy.
- Separated Connected Broker experience from Premium AI features.
- All affected documentation synchronized.

---

# Documentation v1.0

- Initial documentation system.

---

# End of Changelog

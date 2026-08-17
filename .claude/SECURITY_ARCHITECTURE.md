# StockAssist AI
## Security Architecture — Engineering Blueprint

Version: 1.0

Status: Authoritative — Single Source of Truth for all security decisions

Date: 2026-07-18

Owner: Engineering (CTO)

Companion Documents: SECURITY.md (operational guide) · PRODUCTION_HARDENING.md (program strategy) · PRODUCTION_ROADMAP.md (sprint execution)

---

# Overview

This document is the permanent engineering blueprint for every security-relevant decision in StockAssist AI: what is built, why it is built that way, and what remains to be built. It exists because the Production Hardening program (PH1) produces real, load-bearing security code — cookie policy, CORS policy, OAuth hardening — and that code needs one place where its design is recorded at engineering depth, instead of being scattered across sprint changelogs.

Where SECURITY.md tells an operator or reviewer *what the rules are*, this document tells an engineer *how the system enforces them, why it was built this way, and what sequence future work must follow*. When the two disagree, this document is derived from the actual codebase as of the date above and SECURITY.md should be read as the operational summary of it.

---

# Purpose

- Give every future engineer and AI assistant one canonical description of the authentication, authorization, session, and transport-security architecture.
- Make the completed PH1.1–PH1.4 hardening work legible as *architecture*, not just as a list of closed findings.
- Provide the blueprint that PH1.4b–PH1.12 implement against, so each remaining sprint extends a coherent design rather than improvising in isolation.
- Give security reviewers, auditors, and penetration testers (PH1.12) one document that reflects the deployed system.

---

# Responsibilities

This document owns:

- The threat model and trust boundaries for the platform.
- The full authentication and authorization architecture (current and planned).
- The cookie, JWT, and OAuth lifecycle designs.
- The CORS, CSRF, security-header, and rate-limiting strategy.
- The audit-logging and secrets-management model.
- The security module layout and middleware pipeline.
- Sequence diagrams for the request lifecycle, OAuth login, session refresh, and logout.

This document does **not** own: infrastructure hardening (PH2, DEPLOYMENT.md), test execution detail (TESTING.md), or day-to-day operational runbooks (SECURITY.md §Incident Response, §Backup Strategy) — those remain in their existing homes and are referenced, not duplicated.

---

# 1. Security Philosophy

Security is not a feature bolted onto StockAssist AI — it is a property every feature must have before it ships (CLAUDE.md, SECURITY.md). Three ideas govern every decision in this document:

1. **Fail closed.** Any code path that cannot verify identity, origin, or configuration returns an error rather than a session. There is no "demo mode," "fallback account," or silently-permissive default reachable outside a dev-only, `APP_ENV`-gated code path.
2. **Centralize, don't duplicate.** Security-relevant behavior that must be identical everywhere (cookie flags, CORS rules) lives in exactly one module under `backend/security/`. Two places that can each independently decide "is this cookie secure?" will eventually disagree; PH1.1's two backdoors and PH1.3/PH1.4's pre-hardening state are both examples of what duplication costs.
3. **Design now for the platform this becomes.** StockAssist AI will hold brokerage credentials, payment metadata, and AI-generated trading guidance for paying customers. Every control in this document is sized for that platform, not for the current MVP traffic level.

---

# 2. Security Design Principles

Inherited from SECURITY.md §Security Principles, made concrete:

| Principle | Concrete meaning in this codebase |
|---|---|
| Least Privilege | `require_admin` gates the entire `/api/admin/*` surface; `super_admin`-only actions (e.g. user deletion) add a second check on top of `admin` |
| Zero Trust | The frontend's role/permission checks are UX only — every mutating route re-verifies identity and role server-side via `Depends(get_current_user)` / `Depends(require_admin)` |
| Defense in Depth | Planned pipeline: rate limiter → CORS → cookie/JWT auth → role check → input validation → business logic → audit log (see §27) |
| Fail Secure | `get_jwt_secret()` reads `os.environ["JWT_SECRET"]` with no default — a missing secret crashes the process rather than issuing forgeable tokens |
| Secure by Default | `cookie_secure()` forces `Secure=True` whenever `APP_ENV=production`, ignoring any environment override (`backend/security/cookies.py`) |
| Audit Everything | Two immutable, append-only collections: `security_audit_logs` (auth/OAuth outcomes) and `admin_audit_logs` (admin mutations) |
| Encrypt Sensitive Data | Passwords hashed with bcrypt; broker/refresh token encryption is scoped to PH1.6/PH2.8 (not yet implemented — see §35) |
| Never Trust Client Input | Pydantic models validate all request bodies; password policy enforced at the model layer (PH1.5); `email: str` (not `EmailStr`) remains a known, tracked gap (deferred to PH1.5b) |
| Never Expose Secrets | No `.env` committed; `JWT_SECRET`, `GOOGLE_CLIENT_SECRET`, broker secrets all environment-sourced |
| Validate Everything | Enforced inconsistently today — full API contract validation is PH3.5 scope |

---

# 3. Threat Model

## Actors

- **Anonymous internet user** — the default-hostile actor. Every unauthenticated endpoint must assume this actor is present.
- **Authenticated free/paid user** — trusted for their own data only. Must never reach another user's portfolio, trades, sessions, or broker connection.
- **Admin / super_admin** — trusted for platform operations. Every mutation is audited; `super_admin` is a strictly higher trust tier than `admin` (e.g., only `super_admin` may delete a user).
- **Google (OAuth IdP)** — trusted only after cryptographic verification of its tokens; never trusted based on an unauthenticated redirect or client-supplied claim.
- **Broker platforms (Zerodha, Upstox, …)** — trusted as data/execution sources for the specific user who authorized the connection; never a source of cross-user data (SECURITY.md §Broker Security).
- **Malicious browser extension / XSS payload** — the reason auth tokens are `HttpOnly` and never placed in `localStorage` or read by JavaScript.
- **Malicious third-party site** — the reason CORS is an exact-match allowlist and cookies carry `SameSite`.

## Primary threats this architecture defends against

| Threat | Defense | Status |
|---|---|---|
| Unauthenticated session issuance (backdoor) | Removed entirely (PH1.1); no code path issues a session without verified credentials | ✅ Closed |
| OAuth CSRF / state replay | Double-submit `state` cookie + single-use server-side record (Redis/in-memory) | ✅ Closed (PH1.2) |
| OAuth account takeover via unverified email | `email_verified` gate rejects unverified Google accounts outright | ✅ Closed (PH1.2) |
| Token theft via XSS | `HttpOnly` on all three auth cookies; JS never reads them | ✅ Closed (PH1.3) |
| Token theft via plaintext transport | `Secure` forced true in production | ✅ Closed (PH1.3) |
| Cross-site credentialed requests (CSRF via CORS) | Wildcard-with-credentials removed; exact-match allowlist, fail-closed in production | ✅ Closed (PH1.4) |
| Cross-site state-changing request via cookie auth (CSRF proper) | `SameSite=Lax` baseline **plus** a signed double-submit CSRF token bound to the session, enforced on cookie-authenticated mutations (`security/csrf.py`); Bearer requests exempt by construction | ✅ Closed (PH1.7), see §18 |
| Credential stuffing / brute force | Centralized limiter (`security/rate_limit.py`): login 5 / 15 min per `ip:account` with progressive lockout, plus platform-wide per-user / per-IP tiers; production password policy + timing-equalized failures (PH1.5) | ✅ Closed (PH1.7), see §21 |
| Endpoint flooding / token abuse | Platform-wide `RateLimitMiddleware` (authenticated 120/min per user, public 60/min per IP) + per-endpoint limits (register 5/h, refresh 20/min) with `Retry-After` | ✅ Closed (PH1.7), see §21 |
| Stolen long-lived access token | 15-min access token; global invalidation via `password_changed_at` + token `ver` kill-switch | ✅ Closed (PH1.6) |
| Refresh token replay | Rotation on every use + reuse detection that revokes the whole family; server-side revocation store | ✅ Closed (PH1.6) |
| Cross-user Socket.IO event leakage | No connection/room authorization yet | ⚠️ Open — PH1.9 |
| Weak/missing production secrets | No boot-time validator yet (`JWT_SECRET` merely required, not strength-checked) | ⚠️ Open — PH1.8 |
| Vulnerable dependency | No scanning yet | ⚠️ Open — PH1.11 |

---

# 4. Trust Boundaries

```
                        ┌─────────────────────────────┐
                        │      Untrusted Internet      │
                        └───────────────┬──────────────┘
                                        │ HTTPS (prod) / HTTP (dev only)
                        ┌───────────────▼──────────────┐
                        │  Cloudflare / Reverse Proxy   │  ← PH2 scope, not yet deployed
                        └───────────────┬──────────────┘
                                        │
                        ┌───────────────▼──────────────┐
                        │   FastAPI app (server.py)     │  ← TRUST BOUNDARY 1
                        │   CORS middleware (cors.py)   │     origin allowlist enforced here
                        └───────────────┬──────────────┘
                                        │
                        ┌───────────────▼──────────────┐
                        │  Auth layer (get_current_user,│  ← TRUST BOUNDARY 2
                        │  require_admin, OAuth routes)  │     identity established here
                        └───────────────┬──────────────┘
                                        │
                        ┌───────────────▼──────────────┐
                        │   Business logic / routers     │  ← TRUST BOUNDARY 3
                        │   (per-user data scoping)       │     authorization enforced here
                        └───────────────┬──────────────┘
                                        │
                        ┌───────────────▼──────────────┐
                        │  MongoDB / Redis (internal)    │  ← TRUST BOUNDARY 4
                        │  never internet-exposed         │
                        └───────────────┬──────────────┘
                                        │
                        ┌───────────────▼──────────────┐
                        │  External APIs (Google, broker,│  ← TRUST BOUNDARY 5
                        │  Claude, Gemini, market data)   │     verified before trusted
                        └────────────────────────────────┘
```

Nothing crosses a boundary without the boundary's own verification: CORS does not imply authentication; authentication does not imply authorization for a specific resource; a verified Google identity does not imply account linkage without the `email_verified` + `sub`-matching checks in §13.

---

# 5. Authentication Architecture

StockAssist AI supports two authentication methods today, both converging on the same session primitive (a JWT access/refresh pair delivered as `HttpOnly` cookies):

1. **Email & password** (`POST /api/auth/register`, `POST /api/auth/login`) — bcrypt-hashed passwords, brute-force lockout on login.
2. **Google OAuth 2.0 / OIDC** (`GET /api/auth/google/login-url`, `POST /api/auth/google/session`) — fail-closed, server-side code exchange (§13).

Both paths call the same token issuance primitives (`create_access_token`, `create_refresh_token`) and the same cookie-setting primitive (`set_auth_cookies`, `backend/security/cookies.py`), so a session looks identical to the rest of the system regardless of how it was established. This convergence is deliberate: authorization code never needs to know which method produced the session.

**Session lookup** (`get_current_user`, `backend/server.py`): reads `access_token` from the cookie first, falls back to an `Authorization: Bearer` header (supports non-browser/API clients), decodes and verifies the JWT, confirms `type == "access"`, and loads the user from MongoDB. Any failure (missing token, expired, wrong type, user deleted) returns `401`.

**Future methods** (SECURITY.md): GitHub OAuth, Passkeys, biometric auth, magic links — none implemented; any addition must converge on the same session primitive described above.

---

# 6. Authorization Architecture

Authorization in StockAssist AI is **coarse-grained and role-based today**, not the fine-grained permission model SECURITY.md describes aspirationally (§8 below explains the gap). Two enforcement points exist:

1. **`get_current_user`** — establishes *who* the caller is. Used by nearly every non-public route via `Depends(get_current_user)`.
2. **`require_admin`** — establishes *that the caller is staff*. A thin wrapper that calls `get_current_user` then checks `user.role in ("admin", "super_admin")`, else `403`. Used by every route under `admin_router` (`/api/admin/*`).

A small number of routes add a third, ad-hoc check for `super_admin` specifically (e.g. `admin_delete_user`) rather than a general permission system — this is the concrete gap described in §8.

3. **`security.roles.validate_role_assignment`** (PH1.12 / F-1) — establishes *what role a staff member may write to another account*. `require_admin` guards who may reach the admin surface; this guards **privilege elevation through it**. Before PH1.12, `PUT /api/admin/users/{id}` accepted `role` as an unchecked passthrough, so any `admin` could promote any account — including themselves — to `admin`/`super_admin`. The helper now (a) allowlists the value against `ASSIGNABLE_ROLES` (unknown → `400`) and (b) permits the admin-tier roles (`admin`, `super_admin`) **only** for a `super_admin` actor (`403` otherwise). It is the single place a role write is authorized; `backend/security/roles.py`.

**Malformed-identifier handling** (PH1.12 / F-2): every untrusted id (path/query/body) is parsed through `security.identifiers.parse_object_id`, which returns a clean `400` instead of the previous uncaught `bson.InvalidId` → `500`. Trusted ids (a verified JWT `sub`, an `_id` read back from Mongo) stay raw. `backend/security/identifiers.py`.

**Resource-level authorization** (a user may only see their own portfolio/trades/watchlist) is enforced implicitly by scoping every query to `user["_id"]` inside each router — there is no central resource-ownership check layer. This is adequate for REST today; it is explicitly **not yet true for Socket.IO** (§9, §29), which is why PH1.9 exists.

---

# 7. Role Based Access Control

Roles, as stored on `users.role` and checked throughout `server.py`:

| Role | Granted by | Typical access |
|---|---|---|
| `user` | Default on registration/OAuth signup | Own portfolio, trades, watchlist, AI features (free tier limits) |
| `pro` / `premium` | Subscription upgrade (`admin_grant_plan` or payment flow) | Unlimited AI, portfolio review, paper trading, backtesting |
| `elite` | Subscription upgrade | 24/7 AI monitoring, real-time trade alerts, broker automation |
| `admin` | Assignable **only by a `super_admin`** (PH1.12 / F-1); no self-service path | Full `/api/admin/*` surface except `super_admin`-gated mutations |
| `super_admin` | Assignable **only by a `super_admin`** (PH1.12 / F-1) | Everything `admin` has, plus destructive operations (user deletion) |

`Guest` (SECURITY.md's unauthenticated tier) exists implicitly as "no session" — there is no `role="guest"` value stored anywhere; unauthenticated requests simply never pass `get_current_user`.

The **assignable-role allowlist** and the **least-privilege rule on elevation** now live in one place — `backend/security/roles.py` (`ASSIGNABLE_ROLES`, `ADMIN_TIER_ROLES`, `validate_role_assignment`) — rather than scattered `role in (...)` tuples at the write sites (PH1.12 / F-1). The read-side checks (`require_admin`, ad-hoc `super_admin` gates) remain explicit tuple tests; a full `ROLE_HIERARCHY` / fine-grained permission system is still future work (§35).

---

# 8. Permission System

**Current state: does not exist as a distinct system.** SECURITY.md's "Fine-Grained Permissions" section (View Portfolio, Trade Stocks, Connect Broker, Manage Users, Manage Payments, Manage AI, Manage Feature Flags — "each permission is individually configurable") describes a target design that has no corresponding code. What exists instead is the two-tier RBAC in §6–7.

This is recorded here explicitly (rather than silently left as a doc/code mismatch) because PH1.10 ("Admin Hardening & Session Management") is the nearest sprint that touches admin authorization scope, and any future permission system should be scoped as its own ADR rather than folded silently into an unrelated sprint. **No PH1 sprint currently owns building a fine-grained permission system.** This is a documentation gap, not a critical launch blocker — v1.0 can ship on role-based checks — but SECURITY.md should not describe a system as if it exists. This is corrected in the §4 (Step 4) documentation sync.

---

# 9. Session Architecture

A "session" is a **refresh-token family**: a durable server-side record (one MongoDB `sessions` document, `backend/security/sessions.py`) that tracks the single refresh token currently valid for one login on one device. Access-token verification stays **stateless** — no per-request session lookup — so the hot path is unchanged; the session store is consulted only at the refresh boundary (rotation + theft detection) and at logout. Immediate, global invalidation of *access* tokens (which the stateless path cannot revoke individually) rides on the `password_changed_at` marker and the in-code token `ver` kill-switch.

| Property | Value (PH1.6) |
|---|---|
| Access token lifetime | 15 minutes (`JWT_ACCESS_TTL_SECONDS`, default 900) |
| Refresh token lifetime | 7 days (`JWT_REFRESH_TTL_SECONDS`, default 604800), sliding on use |
| Refresh rotation | Rotate on every use — new `jti` issued, old token single-use/dead |
| Reuse detection | Replay of a rotated token revokes the **entire family** |
| Server-side revocation store | MongoDB `sessions` collection (durable; TTL-reaped at expiry) |
| Access-token global invalidation | `password_changed_at` (per-user) + token `ver` (platform-wide) |
| Session listing (device/IP/last-activity) | Captured now (`user_agent`/`ip`); `GET /api/auth/sessions` UI is PH1.10 |
| Logout / logout-all | `POST /api/auth/logout` revokes the current family; `POST /api/auth/logout-all` revokes every family for the user |

**Why MongoDB, not the Redis cache layer:** rotation with reuse detection needs an *authoritative, durable* record of which token is current. The `services.cache` layer is best-effort and evictable — losing a record there would silently drop reuse detection or log users out on a flush. Sessions live in Mongo beside their users, survive restarts, and are auto-purged by a TTL index on `expires_at`.

**Migration note (existing sessions):** PH1.6 validation is strict and fail-closed (every token must carry `aud`/`iss`/`jti`/`ver`/`sid`), so tokens minted before the upgrade fail verification. On deploy, every active user re-authenticates once via the normal 401 → login flow — a deliberate clean cutover, not a regression. No data migration is required.

The `access_token`/`refresh_token` cookie **names are session-scoped** (`Path=/`), so a single logout call clears the entire session in one shot (no per-path cookie duplication is possible — a deliberate PH1.3 design choice, see `backend/security/cookies.py` docstring). The access cookie's `Max-Age` (24h, PH1.3) intentionally outlives the 15-min JWT: the browser simply presents an expired token, which is rejected and triggers a silent refresh — harmless. Aligning cookie `Max-Age` to token lifetime is a cosmetic PH1.3-owned follow-up, out of scope for PH1.6.

---

# 10. Cookie Architecture

Centralized in `backend/security/cookies.py` (PH1.3) — the single place every authentication cookie is set or cleared. No other module may call `response.set_cookie`/`delete_cookie` for an auth-related cookie.

| Cookie | Purpose | HttpOnly | Secure | SameSite | Path | Max-Age |
|---|---|---|---|---|---|---|
| `access_token` | Session credential (JWT) | ✓ always | forced `True` in prod, env-driven (`COOKIE_SECURE`) in dev | `Lax` default, env-configurable | `/` | 86400s (24h) |
| `refresh_token` | Refresh credential (JWT) | ✓ always | same as above | same as above | `/` | 604800s (7d) |
| `g_oauth_state` | OAuth CSRF state, single-use | ✓ always | same as above | never `Strict` (must survive top-level redirect from Google) | `/api/auth` | 600s (10m) |

Key design decisions (see file docstring for full rationale):
- **`Secure` cannot be disabled in production** — the environment override is ignored when `APP_ENV=production`, closing R-04 permanently rather than by convention.
- **Clearing mirrors setting** — `clear_auth_cookies`/`clear_oauth_state_cookie` reuse the exact key/path/domain/security attributes used when the cookie was set, because browsers silently no-op a delete whose attributes don't match.
- **Session fixation is structurally prevented** — login, registration, and OAuth all *mint fresh tokens* and overwrite cookies in place; there is no code path that promotes a pre-authentication cookie value into an authenticated one.
- **`COOKIE_DOMAIN`** is optional, for future subdomain session sharing (e.g. `app.` / `api.` split); host-only cookies are the default and safest for the current single-host deployment.

---

# 11. JWT Lifecycle

All token crypto is centralized in `backend/security/jwt.py` (PH1.6) — the only place a JWT is encoded or decoded. It is pure and framework-agnostic (no FastAPI, no DB); `server.py` composes it with the `SessionStore` (§12).

```
Issuance (security.jwt)              Verification (decode_token)        Expiry
──────────────────────────────────  ─────────────────────────────────  ──────────────
create_access_token(uid, email, sid) read cookie or Bearer header       15m → TokenExpired → 401
  → {sub,email,type:access,sid,jti,  pyjwt.decode(secret, HS256,          "Token expired"
     iat,exp,aud,iss,ver}              audience, issuer, require=[…])
create_refresh_token(uid, sid, jti)  assert type / ver                   7d  → 401 at
  → {sub,type:refresh,sid,jti,        (get_current_user also loads user   /api/auth/refresh
     iat,exp,aud,iss,ver}              + checks password_changed_at)
```

- **Algorithm:** HS256, single shared secret (`JWT_SECRET`, required env var, no default).
- **Claims:** `sub` (user id), `email` (access only), `type` (`access`/`refresh`), `sid` (owning session/family), `jti` (unique id — the handle the store rotates/revokes), `iat` (anchor for `password_changed_at`), `exp`, `aud`, `iss`, `ver` (token schema version).
- **`aud`/`iss` are validated** on decode — a token minted for another audience/issuer (or a stray HS256 token sharing the secret) is rejected.
- **`ver` is a global kill-switch** — pinned in code (`TOKEN_VERSION`, like the bcrypt cost); bumping it in a reviewed diff invalidates every token in circulation at once.
- **Strict, fail-closed validation** — `decode_token` requires all of `exp/iat/aud/iss/sub/jti/type/ver/sid`; a token missing any (e.g. a pre-PH1.6 token) is rejected (see §9 migration note).
- **Typed errors** — verification raises `TokenExpired` / `TokenInvalid` (never a raw `pyjwt` type or an `HTTPException`); the web layer maps both to a generic 401 that never reveals which check failed.
- **Verification is symmetric** for access and refresh (same secret/algorithm/aud/iss) — only the required `type` and the reading endpoint differ.

---

# 12. Refresh Token Lifecycle

`POST /api/auth/refresh` (PH1.6): reads the `refresh_token` cookie → `decode_token(expected_type="refresh")` → checks `password_changed_at` → **rotates** the token via `SessionStore.rotate(sid, presented_jti, new_jti)` → issues a **new access AND new refresh** pair via `set_auth_cookies`. Every refresh changes the refresh token; the presented one is single-use.

```
Client                          Server (server.py + SessionStore)
  │  POST /api/auth/refresh       │
  │  (refresh_token cookie: jti=J0)│
  ├───────────────────────────────►
  │                                │ decode refresh_token (aud/iss/exp/ver/type)
  │                                │ reject if iat < password_changed_at
  │                                │ rotate(sid, J0, J1):
  │                                │   J0 == current_jti ? → current_jti = J1  (ROTATED)
  │                                │   J0 != current_jti ? → revoke family     (REUSE → 401)
  │                                │   revoked / expired / unknown? → 401
  │                                │ mint access(sid) + refresh(sid, jti=J1)
  │  ◄─────────────────────────────┤ set_auth_cookies (BOTH rotated)
  │  200 + new access & refresh   │
```

**Reuse detection (the core of R-06):** the family stores only `current_jti`. Replaying an already-rotated refresh token (its `jti` no longer current, but the family still live) is the fingerprint of a stolen token used after the legitimate client already rotated — it **revokes the whole family**, so both the thief's and the victim's tokens stop working and the compromise surfaces as a forced re-login instead of a silent takeover. A revoked or expired family refreshes to nothing (401).

**Sliding expiry:** each rotation extends the family's absolute expiry by a full refresh lifetime, so an actively-used session is never logged out mid-use (an absolute session cap is a future enhancement). Rotation, revocation, and reuse detection are implemented in `backend/security/sessions.py` and covered by `backend/tests/test_jwt_sessions.py`.

---

# 13. Google OAuth Architecture

Implemented in `server.py` under `google_auth_router` (`/api/auth/google/*`) — a candidate for extraction into `backend/security/oauth.py` in a future sprint (not yet done; still inline).

**Design principle:** every step fails closed. There is no code path that issues a session from an OAuth callback without completing all of: CSRF state validation → single-use state consumption → server-side code exchange → id_token cryptographic verification → issuer check → `email_verified` check → safe account resolution.

Full sequence in §29. Key architectural points:

- **State is validated twice**, independently: (1) double-submit against the `g_oauth_state` httponly cookie (constant-time compare via `secrets.compare_digest`), and (2) a single-use server-side record (Redis when configured, in-memory fallback otherwise) that is fetch-and-deleted — this is what actually defeats replay, since the cookie alone only proves same-browser, not first-use.
- **Identity comes only from the verified `id_token`**, never from an unauthenticated endpoint or client-supplied claim. Verification checks signature (against Google's published keys), audience (`= GOOGLE_CLIENT_ID`), and issuer (`accounts.google.com`).
- **`email_verified` is a hard gate.** An unverified email can neither create an account nor link to an existing one — this is the primary defense against account-takeover via a spoofed-but-technically-valid Google token for an email the attacker doesn't actually control.
- **Redirect URI is allowlisted and bound to the state.** `_allowed_google_redirect_uris()` derives the allowlist from `FRONTEND_URL`/`CORS_ORIGINS` (no hardcoded fallback in production); the callback's `redirect_uri` must both be in that allowlist *and* match the one recorded when the state was minted — defense in depth against redirect substitution.
- **Account resolution priority:** `google_sub` (primary, stable identity) → verified email (secondary, used only to link a pre-existing email/password account). An email already bound to a *different* `sub` is rejected outright (`sub_conflict`), never silently re-linked.
- **Every outcome is audited** via `log_auth_event` into `security_audit_logs`: success (with `new_account`/`linked` flags) or failure (with a machine-readable `reason`: `invalid_state`, `replayed_or_expired_state`, `invalid_redirect_uri`, `google_unavailable`, `token_exchange_failed`, `missing_id_token`, `invalid_id_token`, `bad_issuer`, `incomplete_identity`, `unverified_email`, `sub_conflict`). Never logs tokens, codes, or state values.
- **Unconfigured OAuth fails closed:** missing `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` → `401` at both the login-url and session endpoints, not a silent skip.

---

# 14. Future MFA Architecture

Not implemented. SECURITY.md requires MFA for admin accounts before general availability; PRODUCTION_HARDENING.md tracks this as open risk **OR-4** ("MFA for admin accounts designed but not enforced at launch — enforce before Closed Beta ends"). PH1.10 scope includes an **MFA design ADR** (TOTP-based, recorded in DECISIONS.md as ADR-028) with implementation explicitly deferred past PH1 — MFA is a launch-checklist item for Closed Beta, not for v1.0 Production Certification. Interim mitigation: strong passwords (once PH1.5 lands) + admin audit-log review.

---

# 15. Password Security Architecture

**Current (PH1.5 — implemented):** all password behavior is centralized in `backend/security/passwords.py`, the only module that validates, hashes, or verifies a password. Enforcement is at the model layer: a `model_validator` on `UserCreate` (`backend/models.py`) rejects weak passwords with `422` before they ever reach `hash_password`, reporting every violated rule at once (rule names only — the submitted value is never echoed; a sanitizing `RequestValidationError` handler in `server.py` strips FastAPI's default `input` reflection from all 422 bodies).

Policy for **new** passwords (registration; any future change/reset flow must call the same module):

- Minimum 12 characters; maximum 64 characters and 72 UTF-8 bytes (the bcrypt truncation boundary — over-long input would make distinct passwords verify as equal).
- Must contain uppercase, lowercase, number, and special character (any non-alphanumeric, including space; interior whitespace is allowed).
- Rejected: entries in the bundled common-password blocklist (`backend/security/data/common_passwords.txt`, curated ~450 entries, matched exactly and with trailing digits/punctuation stripped to catch `Password1234!`-style padding); passwords equal to or containing the user's email/local-part or name tokens (≥5 chars); fewer than 5 unique characters (repeated/alternating padding); any 4-character run from the alphabet, digits, or qwerty keyboard rows, forward or reversed.
- Leading/trailing whitespace is stripped before validation *and* hashing (the model validator rewrites the field, so the two can never disagree). No unicode normalization — deliberate; the user must type the same codepoints at login.

Hashing and verification:

- bcrypt with an **explicit** cost factor (`BCRYPT_ROUNDS = 12`) — pinned in code rather than inherited from the library default, so a dependency upgrade can never silently weaken hashing.
- `verify_password` is constant-time (`bcrypt.checkpw`) and **never raises**: empty or malformed stored hashes (OAuth-native accounts store `password_hash: ""`) return `False` after a comparison against a fixed dummy hash. This closed a real bug — password login against a Google-native account previously raised `ValueError` → 500; it is now an ordinary generic 401.
- Login failures are **timing-equalized**: exactly one bcrypt comparison runs whether the email is unknown, the account is OAuth-only, or the password is wrong, so response timing cannot enumerate accounts. The 401 body is identical in all cases.

**Compatibility guarantees:** the policy applies to new passwords only — `UserLogin` is deliberately unvalidated, so pre-PH1.5 accounts (and their weaker passwords) authenticate unchanged; existing bcrypt hashes verify as before regardless of the cost factor they were minted with. The `ip:email` login lockout (§21) is preserved byte-for-byte.

**Password change / reset now implemented (PH1.8):** the change-password endpoint and the forgotten-password reset flow both route their new-password through this same module (`normalize_password` + `validate_new_password` with the resolved account's email/name, then `hash_password`), so recovery can never enforce a weaker policy than registration. See §16 / §17.

---

# 16. Email Verification Architecture

**Implemented — PH1.8.** All recovery-token logic is centralized in `backend/security/recovery.py` (the only module that mints, verifies, or burns a verification/reset token); the `/api/auth` endpoints compose it.

**Token model:** the value emailed to the user is `<token_id>.<HMAC(secret, "stockassist.recovery.v1|purpose|user_id|token_id")>`. The signature makes it unforgeable without the server key and binds it to exactly one user **and** one purpose (a verification token can never redeem as a reset token). A `recovery_tokens` document (`token_id`, `user_id`, `purpose`, `issued_at`, `expires_at`, `used_at`) is the **authoritative** record: expiry and single-use are enforced against it, not against the stateless signature. HMAC key is `RECOVERY_SECRET` or the required `JWT_SECRET` (domain-separated, no weak default). Nothing logs a token.

**Verification flow:** new email/password registrations are inserted with `email_verified: False` / `email_verified_at: None` / `verified_by: None`, and a verification link (24h, `RECOVERY_VERIFY_TTL_SECONDS`) is emailed **out-of-band via `BackgroundTasks`** so a slow/failed mailer never delays sign-up. `POST /api/auth/verify-email` (public) consumes the token — an atomic compare-and-set (`used_at: None → now`) burns it single-use, replay-safe — and sets `email_verified: True`, `email_verified_at`, `verified_by: "email"`. `POST /api/auth/verify-email/request` (authenticated) resends: issuing a new token invalidates the user's prior unused one (one live link at a time), and it is a silent no-op for an already-verified account (still returns the generic message). Both are rate-limited via the existing `PASSWORD` policy (5 / hour).

**Google accounts** are marked `verified_by: "google"` on creation/link (Google already asserts, and §13 already enforces, a verified email — no separate verification email); pre-PH1.8 Google accounts self-heal the flag on next login.

**Product decision (deliberate):** login is **NOT** blocked on `email_verified` — this keeps every pre-PH1.8 account working (backward-compatible) and avoids a hard lockout when SMTP is not yet provisioned (open risk **OR-6**). The flag is the enforcement hook a future verified-only gate flips on (and can already gate individual sensitive features). Email format is still `email: str` (not `EmailStr`) — tightening to `EmailStr` remains a candidate follow-up.

---

# 17. Password Reset Architecture

**Implemented — PH1.8.** Two entrypoints, both centralizing all token logic in `security.recovery` (see §16 for the token model).

**Forgot → reset:** `POST /api/auth/forgot-password` (public) **always** returns an identical generic message (`"If an account matches, we've sent an email…"`) so it cannot enumerate registered emails; a reset link (30 min, `RECOVERY_RESET_TTL_SECONDS`) is sent only when the account exists **and** has a password to reset (OAuth-only accounts, which store an empty `password_hash`, are silently skipped). `POST /api/auth/reset-password` (public) consumes the single-use token, enforces the §15 password policy against the resolved account's identity, then rotates the credential.

**Change:** `POST /api/auth/change-password` (authenticated) requires the **current** password (re-authentication — a stolen session alone cannot rotate the credential), rejects an unchanged new password, and enforces the §15 policy.

**Shared rotation primitive (`_apply_password_change`):** reset and change both call one function so they can never diverge on the security-critical steps — hash the new password, stamp `password_changed_at` (the global token kill-switch honored by every access/refresh check, §11/§12), retire any outstanding reset tokens, **revoke every session** (`SessionStore.revoke_all_for_user`), and send a `PASSWORD_CHANGED` confirmation email. Net effect: after a reset or change the user is signed out on **every** device — refresh families are dead immediately and outstanding access tokens go stale on next use. All three endpoints are rate-limited via the `PASSWORD` policy.

**Recovery token matrix:**

| Token | Purpose | Lifetime | Single-use | Bound to | Revoked by |
|-------|---------|----------|------------|----------|-----------|
| Email verification | Prove address ownership | 24h (`RECOVERY_VERIFY_TTL_SECONDS`) | Yes (atomic burn) | user_id + purpose (HMAC) | reissue / redemption / TTL reap |
| Password reset | Authorize credential change | 30 min (`RECOVERY_RESET_TTL_SECONDS`) | Yes (atomic burn) | user_id + purpose (HMAC) | reissue / redemption / password change / TTL reap |

---

# 18. CSRF Protection Strategy

**Implemented — PH1.7.** Centralized in `backend/security/csrf.py`; wired via `apply_csrf_protection(app)`. A **signed double-submit cookie bound to the session** (OWASP "signed double-submit cookie") layered on top of the `SameSite=Lax` baseline (`security/cookies.py`).

**Baseline (unchanged):** `SameSite=Lax` on all auth cookies withholds them on the classic cross-site sub-request vectors (`<img>`, `<form>` auto-submit, cross-site `fetch`). This is real and effective — but `Lax` still attaches cookies on *top-level* cross-site navigations (a lured link/redirect performing a `POST`), and a misconfigured `SameSite=None` deployment loses the baseline entirely. The token layer closes that residual surface independently of the cookie's `SameSite` value.

**Token model:** on session issuance (login / register / OAuth) and on every refresh, the server plants a `csrf_token` cookie — **not** `HttpOnly`, so a same-origin script can read it and echo it in the `X-CSRF-Token` header. The token is `<nonce>.<HMAC(secret, "prefix|sid|nonce")>`: it carries no secret, is unforgeable without the server key, and is **bound to the session id (`sid`)**. The HMAC key is `CSRF_SECRET` when set, else the already-required `JWT_SECRET` (domain-separated by a fixed prefix so a CSRF MAC can never be confused with a JWT). Cookie `Secure`/`SameSite`/`Domain` are resolved through `security.cookies` so the CSRF cookie shares the auth cookies' production posture.

**Enforcement rule (`CSRFMiddleware`):** a request must present a valid token **iff** it is (a) a mutating method (not `GET`/`HEAD`/`OPTIONS`/`TRACE`), (b) not an exempt auth-bootstrap path (login/register/refresh/logout/OAuth — these establish or rotate the very session the token binds to), (c) carries **no** `Authorization: Bearer` header, and (d) is genuinely cookie-authenticated (a valid `access_token` cookie). Validation requires header == cookie (double-submit) **and** the token's HMAC verifying against the cookie session's `sid` (binding). Any failure → **`403` (`code: CSRF_FAILED`)**, fail-closed.

**Why Bearer requests are exempt (and why this is non-breaking):** the StockAssist SPA authenticates with an `Authorization: Bearer` token from `localStorage` (`server.get_current_user`). A cross-site attacker cannot read that token (same-origin policy) nor attach a custom `Authorization` header cross-site without a CORS preflight the exact-match allowlist (§19) denies — so a Bearer request carries no ambient cookie authority to forge and is inherently CSRF-safe. Enforcement therefore targets exactly the cookie-only attack surface (`get_current_user` also accepts the `access_token` cookie). This is also what makes the layer a **zero-frontend-change** rollout: the existing Bearer client is unaffected, while a future cookie-only client is already protected. 18 hermetic tests in `backend/tests/test_csrf.py`.

**Token lifecycle:** minted on login/register/OAuth (`_issue_session`) and re-minted on every `/refresh` (same `sid` → stable binding); cleared on logout / logout-all. Cookie `Max-Age` matches the 7-day refresh horizon.

---

# 19. CORS Strategy

Centralized in `backend/security/cors.py` (PH1.4) — the single place CORS is configured; `server.py` wires it in via `apply_cors(app)`.

- **Allowlist source:** `CORS_ALLOWED_ORIGINS` (canonical, comma-separated, exact scheme+host+port). Legacy `CORS_ORIGINS`/`FRONTEND_URL` still honored as inputs, merged and de-duplicated.
- **Wildcard is structurally impossible:** a literal `*` is stripped from every input source at parse time, so it can never reach the allowlist regardless of which env var supplied it — this is what makes `allow_credentials=True` safe by construction rather than by discipline.
- **Fail-closed in production:** an unconfigured allowlist in production resolves to empty — every cross-origin request is rejected, and a warning is logged so the misconfiguration is visible rather than silently broken.
- **Fail-open only in dev:** `http://localhost:3000` / `http://localhost:5173` are assumed *only* when `APP_ENV != production` and nothing else is configured.
- **Methods/headers are enumerated, not wildcarded:** `GET, POST, PUT, PATCH, DELETE, OPTIONS`; `Authorization, Content-Type, Accept, Origin, X-Requested-With`. No response headers are exposed (cookie auth needs none).
- **Preflight cached 10 minutes** (`PREFLIGHT_MAX_AGE`).

Full origin-resolution logic, precedence, and rationale are documented in the module docstring — this section summarizes; the module is authoritative for exact behavior.

---

# 20. Security Headers Strategy

**Implemented — PH1.4b** (2026-07-20), split out of PH1.4 (which delivered CORS only). All HTTP response security headers are centralized in `backend/security/headers.py` and applied by a single pure-ASGI `SecurityHeadersMiddleware` (`apply_security_headers(app)`). A pure-ASGI middleware is used deliberately — it sets headers on the `http.response.start` message without buffering the body (safe for streaming/SSE), touches only the `http` scope (WebSocket upgrades pass through), and *enforces* its values (overwriting any inner-handler value so the posture cannot be weakened downstream). It is wired **after** CORS so it wraps and decorates even CORS preflight and rejected-origin responses.

**Headers emitted on every response:**

| Header | Value (default) | Purpose |
| --- | --- | --- |
| `X-Content-Type-Options` | `nosniff` | Stops MIME sniffing. |
| `X-Frame-Options` | `DENY` | Anti-clickjacking for pre-CSP browsers (defense-in-depth with `frame-ancestors`). |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Never leaks path/query cross-origin; nothing on HTTPS→HTTP downgrade. |
| `Permissions-Policy` | powerful features disabled `()` | Denies camera/mic/geolocation/USB/etc. the API never needs. |
| `Cross-Origin-Opener-Policy` | `same-origin` | Isolates the browsing-context group. |
| `Cross-Origin-Resource-Policy` | `same-origin` | Blocks *no-cors* cross-origin embedding of API responses (the credentialed CORS frontend uses `mode: cors` and is unaffected — CORP only governs no-cors loads). |
| `X-XSS-Protection` | `0` | Neutralizes the deprecated, buggy legacy auditor (superseded by CSP). |
| `Content-Security-Policy` | `default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'` | Strict API lockdown — **no `unsafe-inline`/`unsafe-eval`**. |

**Conditional headers:**

- `Strict-Transport-Security` (`max-age=63072000; includeSubDomains`) — emitted **only** over HTTPS or in production (mirrors how `security.cookies` forces `Secure` in production). `X-Forwarded-Proto: https` is honored behind a TLS-terminating proxy so a plain-HTTP dev origin never pins itself. `preload` is opt-in (`HSTS_PRELOAD`) since it is a hard-to-reverse public commitment.
- `Cross-Origin-Embedder-Policy: require-corp` — implemented but **opt-in** (`CROSS_ORIGIN_EMBEDDER_POLICY`); it offers no protection to the API's own JSON yet would break same-origin HTML tooling (Swagger UI) pulling cross-origin subresources without CORP.

**Environment-driven & nonce-capable:** every header value is overridable by environment variable (`CONTENT_SECURITY_POLICY`, `PERMISSIONS_POLICY`, `REFERRER_POLICY`, `X_FRAME_OPTIONS`, `CROSS_ORIGIN_OPENER_POLICY`, `CROSS_ORIGIN_RESOURCE_POLICY`, `CROSS_ORIGIN_EMBEDDER_POLICY`, `HSTS_ENABLE`/`HSTS_MAX_AGE`/`HSTS_INCLUDE_SUBDOMAINS`/`HSTS_PRELOAD`), so a deployment can relax a single directive without a code change. For future nonce-based CSP, a `{nonce}` placeholder in the policy is replaced with a fresh per-request `secrets.token_urlsafe(16)` nonce that is also exposed on `request.state.csp_nonce` for a downstream HTML handler to stamp onto `<script nonce=…>`. 35 hermetic tests in `backend/tests/test_security_headers.py`.

Acceptance target: an A grade on an external header scan (e.g. securityheaders.com) against staging.

---

# 21. Rate Limiting Strategy

**Implemented — PH1.7.** Centralized in `backend/security/rate_limit.py` — one limiter, one storage model, one set of policies. The prior inline `db.login_attempts` lockout is **folded in** (removed as a standalone mechanism), closing the "two lockout systems on one endpoint" footgun this section previously flagged.

**Storage — pluggable, MongoDB now, Redis-ready.** A `RateLimitStore` interface abstracts counting/lockout; the shipped `MongoRateLimitStore` uses the `rate_limits` collection (a fixed-window counter document per `(key, window_start)` plus one lockout document per key, both reaped by a TTL index). MongoDB — not an in-memory counter — is used deliberately: it is durable across restarts and shared across worker processes (an in-memory counter would silently reset on deploy and never see sibling workers), matching the pre-existing persistence choice for `login_attempts`. The interface is the exact shape a Redis `INCR`/`EXPIRE` store implements, so it can be swapped **without touching a single caller**.

**Two enforcement surfaces:**

1. **Inline (auth endpoints)** — login/register/refresh call the limiter directly (their identity comes from the request body / token, and login must count *failures only* so a successful login never consumes budget): `peek` → `record_failure` → `reset`.
2. **Platform-wide middleware (`RateLimitMiddleware`)** — the endpoint-flooding backstop over all `/api` traffic: authenticated requests limited per user, anonymous per client IP.

**Policy matrix (defaults; every policy env-overridable via `RATE_LIMIT_<NAME>="limit/window_seconds"`):**

| Policy | Limit | Scope | Penalty | Endpoint(s) |
|---|---|---|---|---|
| `login` | 5 / 15 min | `ip:account` | Progressive lockout (escalates ×2 per repeat trip, cap 1h) | `POST /api/auth/login` (failures only) |
| `register` | 5 / hour | `ip` | Lockout until window reset | `POST /api/auth/register` |
| `refresh` | 20 / min | `session` (`sid`) | Lockout until window reset | `POST /api/auth/refresh` |
| `password` | 5 / hour | `ip:account` | Lockout until window reset | future password change/reset endpoints |
| `api_user` | 120 / min | `user` | Lockout until window reset | all authenticated `/api/*` (middleware) |
| `api_ip` | 60 / min | `ip` | Lockout until window reset | all anonymous `/api/*` (middleware) |

**Progressive penalties:** exceeding a policy arms a temporary lockout with an automatic expiry; `escalate` policies (login) double the lockout on each *successive* trip (trip history persists past the block), up to `max_block_seconds`. An *active* lockout is never re-escalated by a client that keeps hammering — only a fresh trip after the prior block expired escalates. Every rejection carries a `Retry-After` header; throttled tiers also emit `X-RateLimit-Limit/Remaining/Reset`.

**Fail posture:** every *policy* decision is strict, but a *storage* error in the middleware degrades to "allow" (logged) so the throttle layer can never take the whole API down — availability is chosen over a hard fail for this layer specifically, while auth/CSRF/token layers remain fail-closed.

**Concurrency note:** the Mongo counter is an increment-then-read (non-atomic), so simultaneous requests can momentarily under/over-count by at most the concurrency width — benign for a throttle and closed by an atomic Redis `INCR` store later. (The role-tiered Guest/Free/Pro/Elite quotas sketched in SECURITY.md remain future work; PH1.7 delivers the authenticated-vs-public split and the abuse-critical per-endpoint limits.)

---

# 22. Audit Logging Strategy

Two immutable, append-only MongoDB collections, indexed at startup:

| Collection | Populated by | Records | Never records |
|---|---|---|---|
| `security_audit_logs` | `log_auth_event()` | OAuth/auth event name, email, user_id, reason, ip, user-agent, timestamp, details | tokens, authorization codes, state values |
| `admin_audit_logs` | `log_admin_action()` | admin_id, action, target, details, timestamp | — (admin actions are inherently non-sensitive to log) |

Both are write-only from the application's perspective — no endpoint updates or deletes an audit record, satisfying SECURITY.md's "Audit logs are immutable" rule structurally (no code path to mutate them) rather than by database permission (DB-level immutability/WORM storage is a PH2 infrastructure concern, not yet configured).

**Gap:** password-based login *successes* are not currently written to `security_audit_logs` (only OAuth events call `log_auth_event`) — only failures are tracked via the separate `login_attempts` lockout collection, which is not the audit log. Recommended fix: extend `log_auth_event` calls to the password login/register/refresh/logout paths for a complete authentication audit trail (candidate for PH1.6 or PH1.7 scope, alongside the token/rate-limit work already planned there).

---

# 23. Secret Management

**As of PH1.9, secret management is centralized in `backend/security/secrets.py`** — the single source of truth for the app's configuration surface.

- **The registry.** `SECRET_REGISTRY` declares every environment variable the app reads: category, `sensitive` flag, the environments it is `required_in`, a `min_length` for signing keys, and a safe example. It is the authoritative inventory (mirrored to `backend/.env.example` and `.claude/SECRETS.md`) — adding an `os.environ[...]` read without a registry entry is a review defect.
- **Boot-time validation (fail-closed, fail-informative).** `validate_config()` runs from `server.py` immediately after `load_dotenv` and *before* the Mongo client. It aggregates **every** problem into one value-free error. The old failure mode — `os.environ["JWT_SECRET"]` raising a bare `KeyError` at first request — is replaced by a named, up-front abort. Severity is environment-aware: the core trio (`MONGO_URL`/`DB_NAME`/`JWT_SECRET`) is fatal everywhere; production additionally rejects any missing required secret, a signing key < 32 chars, placeholder/weak values, half-configured OAuth/broker pairs, `ENABLE_AUTO_LOGIN=true`, a weak `ADMIN_PASSWORD`, and the absence of any AI provider.
- **No secret is ever logged.** The startup summary is presence-only (names + counts); `redact()` collapses any value that must appear in diagnostics.
- No `.env` files are committed (verified: no real provider secret in git history via `git log --all -S`; example templates are the only committed `.env.*`). Google, broker, AI-provider, and payment secrets are all environment-sourced.
- **Resolved PH1.9 weak points:** `docker-compose.yml`'s placeholder `change_this_in_production_min_32_chars` `JWT_SECRET` default and hard-coded n8n password `alphapartner123` are removed — both are now required env vars with no baked default.
- A secret-rotation runbook now exists: `.claude/SECRETS.md` (lifecycle, per-class rotation cadence, and leaked-credential incident response).

---

# 24. Environment Security

- `APP_ENV` is the single environment discriminator used consistently across `security/cookies.py`, `security/cors.py`, and now `security/secrets.py` (`is_production()` is the shared primitive all three import from `security.cookies`; `secrets.app_env()` adds the dev/staging/prod tri-state — deliberate, so the modules can never disagree about what "production" means).
- **PH1.9 closed the boot-time gap:** `security/secrets.py`'s `validate_config()` hard-fails startup on any missing/weak required production value (weak `JWT_SECRET`, missing `CORS_ALLOWED_ORIGINS`, absent AI provider, etc.), naming every offending variable at once. Misconfiguration is now caught before the first request, not at request time or never.
- Three-environment strategy (dev/staging/prod) drives validation severity (§SECRETS.md §2); staging still does not yet exist as a running environment (PH2.12).

---

# 25. Dependency Security

**As of PH1.9, `backend/requirements.txt` is fully exact-pinned** (`==` on every line — the last four floating `>=` bounds were locked), giving reproducible builds and a stable audit surface. Automated scanning now runs in CI (`.github/workflows/security-audit.yml`): `pip-audit --strict` + `pip check` (backend), `npm audit` (frontend), and `gitleaks` on every push/PR and weekly; `backend/scripts/audit_dependencies.py` runs the backend checks locally. The PH1.9 audit applied 7 in-pin CVE patches (aiohttp, cryptography, httplib2, pillow, pyasn1, pymongo, python-multipart); the remaining advisories are framework-locked (`starlette` under `fastapi==0.110.1`), AI-scope (`litellm`), or unfixed (`ecdsa`), tracked in SECRETS.md §8. **Remaining PH1.11 work:** Dependabot config, the `requirements.txt` → `requirements-dev.txt` split (finding M14 — `black`/`flake8`/etc. still ship in the runtime image), and a triage-SLA policy.

---

# 26. Security Module Layout

```
backend/
├── security/                     ← all cross-cutting security primitives live here
│   ├── __init__.py               package docstring: lists current + planned tenants
│   ├── cookies.py         (PH1.3) cookie policy — the only place cookies are set/cleared
│   ├── cors.py             (PH1.4) CORS policy — the only place CORS is configured
│   ├── passwords.py        (PH1.5) password policy + bcrypt primitives — the only
│   │                                place passwords are validated, hashed, or verified
│   ├── data/
│   │   └── common_passwords.txt  (PH1.5) bundled common-password blocklist
│   ├── headers.py          (PH1.4b) HTTP security-header policy + middleware —
│   │                                the only place security response headers are set
│   ├── csrf.py             (PH1.7) signed double-submit CSRF token bound to the
│   │                                session — the only place a CSRF token is
│   │                                minted, its cookie set/cleared, or validated
│   ├── jwt.py              (PH1.6) JWT issuance/verification — the only place a
│   │                                token is encoded or decoded (pure crypto)
│   ├── sessions.py         (PH1.6) SessionStore: refresh-token families, rotation,
│   │                                reuse detection, revocation (logout / logout-all)
│   ├── rate_limit.py       (PH1.7) centralized rate limiting: named policies,
│   │                                pluggable RateLimitStore (Mongo now, Redis-
│   │                                ready), progressive lockout, middleware
│   ├── recovery.py         (PH1.8) identity-recovery tokens — the only place a
│   │                                single-use email-verify / password-reset token
│   │                                is minted, verified, or burned (signed handle +
│   │                                authoritative recovery_tokens record)
│   ├── secrets.py          (PH1.9) secret & config management — SECRET_REGISTRY of
│   │                                every env var + boot-time validate_config()
│   │                                (fail-closed); the config surface's single truth
│   └── oauth.py            (not yet extracted) candidate home for the Google OAuth
│                                    logic currently inline in server.py — extraction
│                                    is not itself a scheduled sprint; noted as a
│                                    housekeeping opportunity for whichever sprint
│                                    next touches OAuth code.
├── scripts/
│   └── seed_dev_admin.py  (PH1.1) dev-only admin seeding, guarded on APP_ENV
├── server.py                      auth_router, google_auth_router, require_admin,
│                                    get_current_user, log_auth_event, log_admin_action
└── tests/
    ├── test_auth_hardening.py    (PH1.1) 11 tests — backdoors stay removed
    ├── test_oauth_hardening.py   (PH1.2) 26 tests — OAuth flow correctness
    ├── test_cookie_security.py   (PH1.3) 24 tests — cookie policy
    ├── test_cors_hardening.py    (PH1.4) 30 tests — CORS policy
    ├── test_security_headers.py  (PH1.4b) 35 tests — HTTP security headers
    ├── test_password_policy.py   (PH1.5) 40 tests — password policy + login compatibility
    ├── test_jwt_sessions.py      (PH1.6) tests — JWT lifecycle + session rotation
    ├── test_csrf.py              (PH1.7) 18 tests — signed double-submit CSRF
    ├── test_rate_limit.py        (PH1.7) 26 tests — limiter, lockout, middleware
    ├── test_recovery.py          (PH1.8) 28 tests — identity-recovery tokens
    └── test_secrets.py           (PH1.9) 38 tests — config validation + registry
```

Convention established by PH1.1–PH1.4 and binding on every future security sprint: **one module per concern under `backend/security/`, one test file per module under `backend/tests/`, named `test_<concern>.py`.**

---

# 27. Security Middleware Pipeline

Current pipeline (as wired in `server.py` today, PH1.7):

```
Request
   │
   ▼
SecurityHeadersMiddleware (security/headers.py, apply_security_headers)  ── stamps
   │                                                security headers on every response
   ▼
CORSMiddleware (security/cors.py, apply_cors)   ── origin check, preflight
   │
   ▼
RateLimitMiddleware (security/rate_limit.py, apply_rate_limiting)  ── per-user /
   │                                       per-IP flooding backstop; 429 + Retry-After
   ▼
CSRFMiddleware (security/csrf.py, apply_csrf_protection)  ── signed double-submit
   │                            check on cookie-authenticated mutations; 403 on failure
   ▼
Route dispatch (FastAPI router matching)
   │
   ▼
Depends(get_current_user) / Depends(require_admin)   ── identity + role
   │                            (auth endpoints additionally call the limiter inline)
   ▼
Pydantic model validation (route parameters/body)
   │
   ▼
Route handler (business logic, per-user data scoping)
   │
   ▼
Response (+ security headers via security/headers.py; set/clear cookies via security/cookies.py and the CSRF cookie via security/csrf.py where applicable)
```

**Middleware ordering rationale (deliberate deviation from the earlier idealized "rate limiter first" sketch):** Starlette runs middleware last-registered-first, so registering CSRF and the rate limiter *before* CORS/headers places them **inside** the CORS and header stampers. A `403`/`429` they emit therefore still flows back out through CORS (gaining `Access-Control-Allow-Origin`) and the header middleware — so a browser SPA can actually *read* the rejection and the response stays consistently hardened. They still reject *before* route dispatch, auth, and any handler/DB work; only the two cheap header/origin stampers run ahead of them. Putting the limiter truly outermost (as the original target diagram drew it) would strip CORS/security headers off exactly the error responses clients most need to interpret — hence this order.

**Remaining planned additions** (PH1.8–PH1.9): an audit-log write on auth-relevant handlers, and Socket.IO connection authorization.

---

# 28. Request Authentication Flow

```
Client                                    Server
  │  Request + access_token cookie          │
  │  (or Authorization: Bearer)             │
  ├─────────────────────────────────────────►
  │                                          │ get_current_user(request):
  │                                          │   token = cookie or Bearer header
  │                                          │   if none → 401 "Not authenticated"
  │                                          │   decode(token, JWT_SECRET, HS256)
  │                                          │     ExpiredSignatureError → 401 "Token expired"
  │                                          │     InvalidTokenError     → 401 "Invalid token"
  │                                          │   assert payload.type == "access" → else 401
  │                                          │   user = db.users.find_one(sub)
  │                                          │     not found → 401 "User not found"
  │                                          │   strip password_hash, return user
  │  ◄───────────────────────────────────────┤
  │  200 + response body                     │  (or 401/403 per above)
```

For admin routes, `require_admin` wraps this exact flow and adds one more check: `role in ("admin", "super_admin")` → else `403`.

---

# 29. OAuth Login Sequence

```
Browser                    StockAssist Backend                 Google
  │  GET /api/auth/google/login-url                             │
  ├──────────────────────────►                                  │
  │                           │ validate client configured        │
  │                           │ resolve+validate redirect_uri      │
  │                           │ state = token_urlsafe(32)          │
  │                           │ store server-side (redirect_uri)   │
  │                           │ set_oauth_state_cookie(state)      │
  │  ◄──────────────────────────┤ {url, redirect_uri}              │
  │  redirect browser to Google auth URL                          │
  ├─────────────────────────────────────────────────────────────►│
  │                                                                │ user consents
  │  ◄─────────────────────────────────────────────────────────────┤
  │  redirect to redirect_uri?code=...&state=...                  │
  │  POST /api/auth/google/session {code, state, redirect_uri}     │
  ├──────────────────────────►                                  │
  │                           │ compare state == cookie state       │
  │                           │   (constant-time) → else 400        │
  │                           │ consume server-side state record     │
  │                           │   (fetch-and-delete) → else 400      │
  │                           │   (missing = expired or replayed)    │
  │                           │ burn oauth-state cookie              │
  │                           │ verify redirect_uri allowlisted      │
  │                           │   AND matches state record → else 400│
  │                           │ POST token exchange (code, secret)   │
  │                           ├──────────────────────────────────────►
  │                           │  ◄─────────────────────────────────────┤
  │                           │   {id_token, access_token, ...}       │
  │                           │ verify id_token signature/aud/exp     │
  │                           │ check issuer ∈ valid issuers          │
  │                           │ check email_verified == true → else 401│
  │                           │ resolve account by sub, then email    │
  │                           │   (reject sub_conflict)                │
  │                           │ create_access/refresh_token            │
  │                           │ set_auth_cookies                       │
  │                           │ log_auth_event(success, new/linked)    │
  │  ◄──────────────────────────┤ 200 {user, ...} + session cookies    │
```

Every `else` branch above logs a `oauth_login_failure` event with a specific `reason` before returning its error response (§13, §22).

---

# 30. Session Refresh Sequence

```
Client                                    Server (server.py + SessionStore)
  │  POST /api/auth/refresh                 │
  │  (refresh_token cookie, jti=J0)         │
  ├─────────────────────────────────────────►
  │                                          │ token = cookie["refresh_token"]
  │                                          │   missing → 401 "No refresh token"
  │                                          │ decode_token(token, "refresh")
  │                                          │   bad sig/aud/iss/exp/ver/type → 401
  │                                          │ reject if iat < password_changed_at → 401
  │                                          │ rotate(sid, J0, new J1):
  │                                          │   J0==current → current=J1        (ROTATED)
  │                                          │   J0!=current → revoke family      (REUSE→401)
  │                                          │   revoked/expired/unknown          (→401)
  │                                          │ access = create_access_token(sub, sid)
  │                                          │ refresh = create_refresh_token(sub, sid, J1)
  │                                          │ set_auth_cookies(access, refresh)
  │  ◄───────────────────────────────────────┤   BOTH cookies rotated
  │  200 + new access & refresh cookies      │
```

---

# 31. Logout Sequence

```
Client                                    Server (server.py + SessionStore)
  │  POST /api/auth/logout                  │
  │  (refresh_token cookie)                 │
  ├─────────────────────────────────────────►
  │                                          │ if refresh cookie present & valid:
  │                                          │   SessionStore.revoke(sid)  (best-effort)
  │                                          │ clear_auth_cookies(response):
  │                                          │   delete access_token  (path=/, matching attrs)
  │                                          │   delete refresh_token (path=/, matching attrs)
  │  ◄───────────────────────────────────────┤
  │  200 {"message": "Logged out"}           │

POST /api/auth/logout-all (authenticated): SessionStore.revoke_all_for_user(uid)
  → revokes every refresh-token family for the user, then clears the current cookies.
```

Logout now revokes the current session's refresh-token family server-side (PH1.6), so a captured refresh token is dead immediately, not merely removed from this browser. Outstanding **access** tokens (≤15 min) drain on their own; for immediate global access-token invalidation, a password change / security event sets `password_changed_at`, which `get_current_user` and `refresh` both honor (§9, §11). This closes the stateless-logout gap called out in §9.

---

# 31b. Security Audit Logging & Monitoring (PH1.10)

**Module:** `backend/security/audit.py`. **Collection:** `security_audit_logs`. This is the one place a security-relevant event is shaped, redacted, and emitted. Before PH1.10, audit writes were scattered across three ad-hoc writers with three record shapes: `log_auth_event` (auth/OAuth/recovery → `security_audit_logs`), `log_admin_action` (admin → `admin_audit_logs`), and the broker engine's `_audit` (broker → `audit_logs`). PH1.10 centralizes the *security-event* path; the prior `log_auth_event` is now a thin backward-compatible facade delegating to this module (its historical record fields are a strict subset of the new schema, so existing queries, indexes, and tests are unaffected). Admin- and broker-action logs are domain audit trails and remain where they are — a future sprint may route them through the same sink.

**Why centralize.** Three writers meant three independent "which fields are safe to log" judgements (one accidental token write is a breach), no shared taxonomy (an investigation is cross-collection archaeology), and no shared severity axis to alert on. One module fixes all three and gives a future SIEM a single, stable contract to read.

**Event taxonomy (closed set).** Every event is one of the named constants in `audit.py`, each mapped to a `category` and default `severity`. An unrecognized event string still records but is classified `SECURITY`/`WARNING` (fail-safe — an unknown security signal is never silently trivialized).

| Category | Events | Default severity |
|---|---|---|
| `authentication` | `login_success`, `login_failure`, `logout`, `logout_all` | INFO / WARNING / INFO / NOTICE |
| `identity` | `registration`, `email_verification_{requested,success,failure}`, `password_change_{success,failure}`, `password_reset_{requested,success,failure}`, `oauth_login_{success,failure}` | INFO / NOTICE / WARNING |
| `session` | `session_created`, `session_revoked`, `session_expired`, `refresh_rotation`, `token_replay_detected` | INFO / NOTICE / **CRITICAL** (replay) |
| `security` | `rate_limit_triggered`, `csrf_validation_failure`, `invalid_jwt`, `invalid_refresh`, `invalid_password`, `permission_denied`, `suspicious_activity` | WARNING / **CRITICAL** (suspicious) |
| `administration` | `admin_login`, `user_role_changed`, `account_disabled`, `account_enabled` | NOTICE |

**Record schema (versioned, `schema_version=1`).** `event`, `category`, `severity`, `outcome`, `email`, `user_id`, `session_id`, `reason`, `ip`, `user_agent`, `request_id`, `target`, `details` (redacted metadata), `timestamp`. `ip` honors the first `X-Forwarded-For` hop (matching `rate_limit.client_ip`); `request_id` reads `X-Request-ID` — the correlation key that joins an audit record to its access log. Indexes: `timestamp`, `event`, `email`, `category`, `severity`, `user_id`, `session_id`.

**Never log a secret.** `_redact` walks the metadata recursively and blanks any value whose key matches a sensitive marker (`password`, `token`, `secret`, `authorization`, `code`, `state`, `csrf`, `hash`, `api_key`, `cookie`, `signature`, …) → `[REDACTED]`. This is defense-in-depth on top of careful call sites: even if an authorization code, OAuth state, refresh token, or password hash is passed by mistake, it can never reach storage (asserted by `test_audit.py`). Depth is bounded so a cyclic/pathological payload degrades instead of spinning.

**Pluggable storage.** An `AuditSink` interface abstracts *where* records go. The configured default is a `CompositeAuditSink` of (1) `MongoAuditSink` — durable, queryable `security_audit_logs`, and (2) `LoggingAuditSink` — one JSON line per event at a severity-mapped log level, the seam a Fluent Bit / Vector / CloudWatch agent tails into a SIEM. Each sink is isolated: a Mongo outage still leaves the structured log line. Swapping backends (syslog, Kafka, a dedicated audit service) is a sink change, not a caller change. The DB handle is resolved lazily via a zero-arg provider (`audit.configure(lambda: db)` at import), the same discipline as `RateLimitMiddleware`.

**Fail safe.** Audit logging is observability, never a gate. Every emit path is wrapped (`AuditLogger.record`) so a storage error degrades to a logged warning and the calling security flow proceeds untouched — losing an audit record must never lock a user out or 500 a request.

**Instrumentation points.** Endpoints in `server.py` (login ±, register, logout/logout-all, refresh rotation, replay detection, invalid/expired-vs-tampered JWT distinction, the recovery flows via the facade) and the security middleware (`csrf.py` → `csrf_validation_failure`; `rate_limit.py` `_trip` → `rate_limit_triggered`, the single choke point covering inline and middleware limiters). Ordinary token *expiry* is deliberately **not** audited (it is routine and would drown the log); a structurally invalid / bad-signature token **is** (`invalid_jwt`).

---

# 32. Future Production Hardening Plan

This section is the authoritative forward-looking security sequence; PRODUCTION_ROADMAP.md owns sprint-level acceptance criteria and PRODUCTION_HARDENING.md owns program-level strategy — this section is what ties them to the architecture described above.

| Sprint | Architectural change this document will need to reflect |
|---|---|
| PH1.4b Security Headers | ✅ Done — `security/headers.py` in §26; §20 now "current"; pipeline updated in §27 |
| PH1.5 Password/Email/Verification | ✅ Password portion done (§15 now "current"). §16, §17 and `EmailStr` deferred to unscheduled PH1.5b |
| PH1.6 JWT & Refresh Rotation | §9, §11, §12, §30 rewritten for rotation + revocation store; `jti` claim added |
| PH1.7 CSRF & Rate Limiting | ✅ Done — §18 rewritten (signed double-submit CSRF, `security/csrf.py`); §21 rewritten (centralized limiter, `security/rate_limit.py`, login-lockout folded in); §26 layout + §27 pipeline updated |
| PH1.8 Secrets & Env Hardening | ✅ Done (as the "PH1.9 — Secrets & Supply Chain" sprint) — §23, §24 rewritten for `security/secrets.py` + boot-time `validate_config`; §26 layout + tests updated; SECRETS.md added |
| PH1.9 WebSocket Security | (next) New §"Real-Time Authorization Architecture"; §4 trust-boundary diagram gains a Socket.IO lane |
| PH1.10 Audit Logging & Monitoring | ✅ Done — new §31b (centralized `security/audit.py`: taxonomy, versioned schema, secret redaction, pluggable sinks, fail-safe); §33 rule 4 + Implementation Status updated; `security_audit_logs` indexes extended |
| PH1.10b Admin & Sessions | §9 session-listing moves to "current"; ADR-028 MFA design referenced from §14 (unscheduled follow-on) |
| PH1.11 Dependency Scanning | 🟡 Partial — §25 updated with CI scan tooling + full pinning (PH1.9); Dependabot/split/triage still pending |
| PH1.12 Security Certification | This document is the primary evidence artifact reviewed against the pen-test checklist |
| Unscheduled: Password reset flow | §17 — recommended to be folded into PH1.5 |
| Unscheduled: OAuth module extraction | §26 — housekeeping, no urgency |

**Rule for future sprints:** every PH1.4b–PH1.12 sprint that changes authentication, authorization, session, cookie, CORS, or transport-security behavior updates the relevant numbered section(s) of this document in the same PR (PRODUCTION_HARDENING.md §15 verification checklist already requires "authoritative documentation updated in the same PR" — this document is that authority for security).

---

# 33. Security Coding Standards

In addition to CODING_STANDARDS.md and PRODUCTION_HARDENING.md §19 (Engineering Standards addendum):

1. No new authentication, session, cookie, or CORS logic is written outside `backend/security/`. If a route needs cookie or origin behavior, it imports from `security.cookies` / `security.cors` — it does not call `response.set_cookie` or configure `CORSMiddleware` itself.
2. Every new endpoint declares its auth dependency explicitly (`Depends(get_current_user)` or `Depends(require_admin)`) — there is no "implicitly public" route; public routes are public by the *absence* of a dependency, which must be a deliberate, reviewed choice.
3. Every new security module ships with a docstring explaining the *why*, matching the style of `cookies.py`/`cors.py` — future engineers should be able to read the module and understand the threat it defends against without external context.
4. Every security-relevant outcome (auth success/failure, session lifecycle, CSRF/rate-limit/JWT rejection, admin action) is recorded through `security.audit` — via the `log_auth_event` facade or `audit.log_event` directly — with an event from the closed taxonomy (§31b) and, on failures, a specific machine-readable `reason`. Never build an ad-hoc audit write or pass a raw secret in `metadata`/`detail` (the redactor is a backstop, not a licence). Vague or missing failure reasons defeat the audit trail's purpose.
5. No security-relevant environment variable ships without a documented default policy (fail-closed vs. dev-convenience) — see §24; PH1.8 will make this mechanically enforced, but the discipline starts now.

---

# 34. Testing Strategy

Authoritative reference: TESTING.md. Security-specific addenda:

- Every `backend/security/*.py` module has a corresponding `backend/tests/test_*.py` file, hermetic (no external services), per the pattern in §26.
- Security tests assert **behavior**, not implementation — e.g. `test_cors_hardening.py` asserts that a disallowed `Origin` header receives no `Access-Control-Allow-Origin`, not that a specific internal function returns a specific list.
- Removed backdoors are asserted **absent** by tests that expect `404`/`401` (`test_auth_hardening.py`) — a regression that silently re-introduces one is a test failure, not a manual-review catch.
- PH1.12 (Security Certification) executes the SECURITY.md penetration-testing checklist against staging and treats this document as the map of what "correct" looks like for each check.

---

# 35. Future Security Roadmap

Beyond PH1 (post-certification, informed by SECURITY.md's "Future" markers and this sprint's architectural review):

- Fine-grained permission system (§8) — replacing role-tuple checks with a declared permission registry, if/when the role list grows beyond what tuple membership checks can cleanly express.
- MFA implementation (TOTP) for admin accounts, per ADR-028 (§14) — targeted before Closed Beta ends (OR-4).
- Broker token / refresh token field-level encryption at rest (SECURITY.md §Encryption) — not yet implemented; scoped to intersect with PH1.6 (token service) and PH2.8 (data-tier hardening).
- GitHub OAuth, Passkeys, magic links (SECURITY.md §Authentication "Future") — each converges on the session primitive in §5 when built.
- CQRS/event-sourcing implications for audit logging (SYSTEM_ARCHITECTURE.md Part 5) — out of scope for PH1; noted for whoever designs the eventual audit/event-store convergence.
- OAuth module extraction (`backend/security/oauth.py`) — housekeeping, not scheduled.

---

# Architecture Summary

StockAssist AI's security architecture, as of PH1.9 completion, is **production-hardened across the transport, session-integrity, abuse-prevention, identity-lifecycle, and configuration layers**: cookies and CORS are centrally owned (§10, §19); OAuth is fail-closed with real cryptographic verification (§13); JWTs rotate with reuse detection and revocation (§12); a signed, session-bound CSRF token layer guards cookie-authenticated mutations (§18); centralized, progressive rate limiting protects against brute force and endpoint flooding (§21); the identity lifecycle is recoverable — single-use, expiring, enumeration-safe email verification and password reset/change, each forcing a full sign-out on credential rotation (§16–§17); configuration is now centralized and validated at boot — `security/secrets.py` fails the process closed on a missing/weak critical secret (§23–§24), with the dependency set fully pinned and continuously audited in CI (§25); and security-event observability is centralized — `security/audit.py` gives every security-relevant event one taxonomy, one redacted schema, and one pluggable, SIEM-ready sink, so an incident is investigable and a secret can never reach a log (§31b, PH1.10). The remaining thin spots are authorization-layer (binary rather than fine-grained roles, §8; no Socket.IO connection authorization) and the deferred framework-locked CVEs (SECRETS.md §8). PH1.9 (Real-Time)–PH1.12 close these in a defined order; this document keeps that sequence coherent instead of ad hoc.

---

# Implementation Status

| Area | Status | Reference |
|---|---|---|
| Auth backdoor removal | ✅ Complete | §Threat Model, PH1.1 |
| Google OAuth hardening | ✅ Complete | §13, §29, PH1.2 |
| Cookie security | ✅ Complete | §10, PH1.3 |
| CORS hardening | ✅ Complete | §19, PH1.4 |
| Security headers | ✅ Complete | §20, PH1.4b |
| Password policy | ✅ Complete | §15, PH1.5 |
| Email verification | ✅ Complete | §16, PH1.8 |
| Password change / reset (identity recovery) | ✅ Complete | §17, PH1.8 |
| JWT lifecycle/rotation | ✅ Complete | §11–§12, PH1.6 |
| CSRF token layer | ✅ Complete | §18, PH1.7 |
| Rate limiting | ✅ Complete | §21, PH1.7 |
| Secrets/env validation | ✅ Complete | §23–§24, PH1.9 (`security/secrets.py`, boot-time `validate_config`) |
| Security audit logging & monitoring | ✅ Complete | §31b, PH1.10 (`security/audit.py`: taxonomy, redaction, pluggable sinks, fail-safe) |
| WebSocket security | 🟡 **Connection authentication complete (PH3.10)** | §32. `/api/ws` authenticates the handshake before `accept()`: identity is the verified `sub` of a valid access token (cookie or `Sec-WebSocket-Protocol`, **never** a query string — uvicorn logs those verbatim), and the client-supplied `user_id` is ignored. Same `password_changed_at`, account-state and token-type checks as `get_current_user`. **This closed a live authorization bypass** — the identity used for per-user event fan-out was previously an unauthenticated query parameter, so any anonymous caller could read any account's realtime stream (tracked as "S-2" since PH1.9). **Still open:** per-channel subscription authorization (any authenticated socket may subscribe to any channel, including `"*"`) and per-connection rate limiting |
| Admin/session management | ❌ Not started | §9, PH1.10b |
| Dependency scanning | 🟡 Partial | §25, PH1.9 (CI pip-audit/npm audit/gitleaks + full pinning); PH1.11 for Dependabot/split |
| Security certification | ❌ Not started | §34, PH1.12 |
| Fine-grained permissions | ❌ Not started, unscheduled | §8 |

---

# Related Documents

- **SECURITY.md** — operational security guide; the checklist-and-policy summary of this document.
- **PRODUCTION_HARDENING.md** — program strategy, risk matrix, readiness scoring, certification gates.
- **PRODUCTION_ROADMAP.md** — sprint-level execution plan (PH1.1–PH1.12 and beyond).
- **SYSTEM_ARCHITECTURE.md** — overall system architecture; its Authentication/Authorization sections defer to this document (see documentation synchronization, Step 4).
- **DECISIONS.md** — ADR-022 (Security First), ADR-027 (Feature Freeze & Production Hardening), future ADR-028 (MFA design).
- **CHANGELOG.md** — per-sprint implementation detail (PH1.1–PH1.4 entries) that this document synthesizes into architecture.
- **backend/security/** — the source of truth for exact current behavior; this document explains it, the code enforces it.

---

# Version History

| Version | Date | Change |
|---|---|---|
| 1.3 | 2026-07-22 | PH1.8 (Identity Recovery) implemented. §16 (Email Verification) and §17 (Password Reset) rewritten from "does not exist / target" to "current": single-use, expiring, signed recovery tokens centralized in `security/recovery.py` (`<token_id>.<HMAC>` + authoritative `recovery_tokens` record; atomic single-use burn); new `/api/auth` endpoints (`verify-email`, `verify-email/request`, `forgot-password`, `reset-password`, `change-password`); user model gains `email_verified`/`email_verified_at`/`verified_by`; reset & change force a full sign-out (`revoke_all_for_user` + `password_changed_at`). Recovery token matrix added. §15 "still open" note, §26 module tree (+`recovery.py`), Architecture Summary, and Implementation Status table updated. 28 new hermetic tests (`test_recovery.py`). Login is deliberately not blocked on `email_verified` (backward-compatible). |
| 1.2 | 2026-07-21 | PH1.7 (CSRF Protection & Rate Limiting) implemented. §18 rewritten from "unowned gap" to "current" — signed double-submit CSRF token bound to the session (`security/csrf.py`, `CSRFMiddleware`), enforced on cookie-authenticated mutations, Bearer requests exempt by construction (zero-frontend-change rollout). §21 rewritten from "login-only" to "current" — centralized limiter (`security/rate_limit.py`) with pluggable `RateLimitStore` (MongoDB now, Redis-ready), named per-endpoint policies, progressive lockout with `Retry-After`, and a platform-wide `RateLimitMiddleware`; the inline `db.login_attempts` lockout was folded in and removed. §26 module tree + test list, §27 middleware pipeline (with ordering rationale), the threat matrix, §32 forward table, and the Implementation Status table all updated. 44 new hermetic tests (`test_csrf.py` 18, `test_rate_limit.py` 26). |
| 1.1 | 2026-07-19 | PH1.5 (Password Policy & Account Protection) implemented: §15 rewritten from target to current (centralized `security/passwords.py`, model-layer 422 enforcement, explicit bcrypt cost 12, timing-equalized/never-raising verification, sanitized validation errors); §26 module tree and §3 threat table updated. §16/§17 (email verification, password reset) deliberately deferred to an unscheduled PH1.5b. |
| 1.0 | 2026-07-18 | Initial document. Synthesizes PH1.1–PH1.4 implementation (auth backdoor removal, Google OAuth hardening, cookie security, CORS hardening) into the permanent security architecture blueprint. Establishes the CSRF-token-layer and password-reset-flow documentation gaps identified during this synchronization sprint. |

---

# End of Security Architecture Document

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
| Cross-site state-changing request via cookie auth (CSRF proper) | `SameSite=Lax` baseline only; dedicated CSRF token layer not yet built | ⚠️ Partial — gap, see §18 |
| Credential stuffing / brute force | Per-`ip:email` lockout on login (5 attempts / 15 min); production password policy + timing-equalized failures (PH1.5) | ⚠️ Partial — platform-wide rate limiting is PH1.7 |
| Stolen long-lived access token | 24h access token, no revocation | ⚠️ Open — PH1.6 |
| Refresh token replay | No rotation, no reuse detection | ⚠️ Open — PH1.6 |
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

**Resource-level authorization** (a user may only see their own portfolio/trades/watchlist) is enforced implicitly by scoping every query to `user["_id"]` inside each router — there is no central resource-ownership check layer. This is adequate for REST today; it is explicitly **not yet true for Socket.IO** (§9, §29), which is why PH1.9 exists.

---

# 7. Role Based Access Control

Roles, as stored on `users.role` and checked throughout `server.py`:

| Role | Granted by | Typical access |
|---|---|---|
| `user` | Default on registration/OAuth signup | Own portfolio, trades, watchlist, AI features (free tier limits) |
| `pro` / `premium` | Subscription upgrade (`admin_grant_plan` or payment flow) | Unlimited AI, portfolio review, paper trading, backtesting |
| `elite` | Subscription upgrade | 24/7 AI monitoring, real-time trade alerts, broker automation |
| `admin` | Manually assigned (no self-service path) | Full `/api/admin/*` surface except `super_admin`-gated mutations |
| `super_admin` | Manually assigned | Everything `admin` has, plus destructive operations (user deletion) |

`Guest` (SECURITY.md's unauthenticated tier) exists implicitly as "no session" — there is no `role="guest"` value stored anywhere; unauthenticated requests simply never pass `get_current_user`.

There is no formal role hierarchy object in code (no `ROLE_HIERARCHY` map) — every check is an explicit tuple membership test (`role in (...)`). This is simple and auditable today; it will not scale past a handful of roles without becoming error-prone (tracked as a future-work item, §35).

---

# 8. Permission System

**Current state: does not exist as a distinct system.** SECURITY.md's "Fine-Grained Permissions" section (View Portfolio, Trade Stocks, Connect Broker, Manage Users, Manage Payments, Manage AI, Manage Feature Flags — "each permission is individually configurable") describes a target design that has no corresponding code. What exists instead is the two-tier RBAC in §6–7.

This is recorded here explicitly (rather than silently left as a doc/code mismatch) because PH1.10 ("Admin Hardening & Session Management") is the nearest sprint that touches admin authorization scope, and any future permission system should be scoped as its own ADR rather than folded silently into an unrelated sprint. **No PH1 sprint currently owns building a fine-grained permission system.** This is a documentation gap, not a critical launch blocker — v1.0 can ship on role-based checks — but SECURITY.md should not describe a system as if it exists. This is corrected in the §4 (Step 4) documentation sync.

---

# 9. Session Architecture

A "session" is a JWT access/refresh pair, not a server-side session object — StockAssist AI is stateless with respect to *valid* sessions (no session table to check on every request) but is gaining server-side state for *revocation* in PH1.6.

| Property | Current | Target (SECURITY.md / PH1.6) |
|---|---|---|
| Access token lifetime | 24 hours | 15 minutes |
| Refresh token lifetime | 7 days | 30 days |
| Refresh rotation | None — same refresh token reusable until expiry | Rotate on every use |
| Reuse detection | None | Revoke token family on replay |
| Server-side revocation store | None | Redis-backed revocation list |
| Session listing (device/IP/last-activity) | None | `GET /api/auth/sessions` (PH1.10) |
| Logout-specific-session / logout-all | Only logout-current (clears cookies) | Both, once revocation store exists |

The `access_token`/`refresh_token` cookie **names are session-scoped** (`Path=/`), so a single logout call clears the entire session in one shot (no per-path cookie duplication is possible — a deliberate PH1.3 design choice, see `backend/security/cookies.py` docstring).

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

```
Issuance                          Verification                    Expiry
────────────────────────────────  ───────────────────────────────  ──────────────
create_access_token(user_id,      get_current_user():               24h → 401
  email)                            - read cookie or Bearer header    "Token expired"
  → {sub, email, exp, type:access}  - pyjwt.decode(secret, HS256)
create_refresh_token(user_id)       - assert type == "access"       7d → 401
  → {sub, exp, type:refresh}        - load user from MongoDB          "No refresh token"
                                     - 401 on any failure              at /api/auth/refresh
```

- **Algorithm:** HS256, single shared secret (`JWT_SECRET`, required env var, no default).
- **Claims:** `sub` (user id), `email` (access only), `exp`, `type` (`access`/`refresh` — prevents a refresh token being used as an access token or vice versa).
- **No `iat`, `jti`, or `aud` claims today** — `jti` is required for the reuse-detection/revocation design in §12; this is a known PH1.6 prerequisite, not an oversight.
- **Verification is symmetric** for access and refresh (same secret, same algorithm) — only the `type` claim and the endpoint that reads them differ.

---

# 12. Refresh Token Lifecycle

Current (`POST /api/auth/refresh`): reads `refresh_token` cookie → decodes → confirms `type == "refresh"` → issues a **new access token only** via `set_access_cookie`. The refresh token itself is **not rotated or re-issued** — the same refresh token remains valid, unchanged, until its own 7-day expiry.

```
Client                          Server
  │  POST /api/auth/refresh       │
  │  (refresh_token cookie)       │
  ├───────────────────────────────►
  │                                │ decode refresh_token
  │                                │ verify type == "refresh"
  │                                │ create_access_token(sub)
  │                                │ set_access_cookie (new)
  │  ◄─────────────────────────────┤ refresh_token cookie UNCHANGED
  │  200 + new access cookie      │
```

**Target design (PH1.6):** rotate the refresh token on every use (issue a new one, invalidate the old), detect reuse of an already-rotated token as a signal of theft (revoke the entire token family), and back both by a Redis-resident revocation store so a compromised token can be killed server-side before its natural expiry. This is the single highest-value remaining authentication sprint (Risk R-06).

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

**Still open (unscheduled — "PH1.5b" candidate):** password change endpoint, password reset flow (§17), and `EmailStr`/email verification (§16) were deliberately excluded from PH1.5, which shipped as a password-policy-only sprint.

---

# 16. Email Verification Architecture

**Current:** does not exist. `email: str` (not `EmailStr`) means malformed addresses are accepted at registration; there is no `email_verified` flag on password-based accounts (only OAuth accounts get a verification signal, and only from Google — see §13). A user can register with an email they do not own and use the platform immediately.

**Target (PH1.5):** `EmailStr` validation at the model layer; a verification-token email flow (send → click → mark `email_verified=True`); a resend endpoint; an SMTP provider decision (tracked as open risk **OR-6**); and a product decision (recorded in USER_FLOWS.md) on whether unverified accounts are fully blocked or restricted-but-functional pending verification.

---

# 17. Password Reset Architecture

**Current:** does not exist — no `forgot-password` or `reset-password` endpoint found in `server.py`. This is not explicitly named as a PH1 finding in PRODUCTION_HARDENING.md, but it is a real gap for a platform requiring individual account credentials at scale. Flagged here as a **documentation gap** (§ Documentation Gaps in the final report) — it should be scoped as part of PH1.5 (identity hygiene) rather than discovered late.

---

# 18. CSRF Protection Strategy

**Current baseline:** `SameSite=Lax` on all auth cookies (`backend/security/cookies.py`) — cookies are withheld on cross-site sub-requests (the classic CSRF vector: `<img>`, `<form>` auto-submit, cross-site `fetch`) and only sent on top-level, same-site navigation. This is a real, effective baseline, not a placeholder.

**Known gap:** no dedicated CSRF *token* layer (double-submit token or synchronizer token pattern) for state-changing cookie-authenticated routes. PH1.3's own completion notes call this out as "deferred to a follow-up," but — as identified in the architectural review for this document — **no PH1 sprint number currently owns it.** PH1.4b is headers-only; PH1.6 is JWT/refresh; neither is CSRF tokens. This document records the gap explicitly so it is not lost: a `backend/security/csrf.py` module, applied to all cookie-authenticated `POST`/`PUT`/`PATCH`/`DELETE` routes, is the recommended next-sprint candidate (see the Documentation Synchronization Report at the end of this sprint's summary).

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

**Not yet implemented — PH1.4b**, explicitly split out of PH1.4 (which delivered CORS only). Target middleware (`backend/security/headers.py`): HSTS, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, and a CSP compatible with the CRA frontend build. Acceptance target: an A grade on an external header scan (e.g. securityheaders.com) against staging.

---

# 21. Rate Limiting Strategy

**Current:** one narrow, real mechanism — login brute-force lockout (`db.login_attempts`, keyed `f"{ip}:{email}"`): 5 failed attempts locks that identifier for 15 minutes (`429`). This is MongoDB-backed (not Redis), scoped to `/api/auth/login` only, and resets on any successful login.

**Target (SECURITY.md, PH1.7):** tiered, platform-wide rate limiting by subscription role — Guest 30/min, Free 120/min, Pro 300/min, Elite 600/min, Admin configurable — via an ASGI limiter (slowapi or equivalent) backed by Redis, with strict, separate limits on `/api/auth/*` regardless of tier. **PH1.7 should treat the existing login-lockout logic as prior art to fold into the new limiter, not as a competing mechanism to leave running in parallel** — two independent lockout systems on the same endpoint is itself a future footgun.

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

- No `.env` files committed to the repository; no hardcoded API keys found in source (verified as part of PH1.1's audit baseline and unchanged since).
- `JWT_SECRET` is read via `os.environ["JWT_SECRET"]` — a bare KeyError (hard crash) if unset, which is fail-secure but not fail-*informative*: there is no boot-time validator yet that names the missing variable before the process dies on first request (PH1.8).
- Google, broker, AI provider, and payment secrets are all environment-sourced; none observed hardcoded.
- **Known weak points, both PH1.8 scope:** `docker-compose.yml` carries a placeholder `change_this_in_production_min_32_chars` default and a hardcoded n8n password — these must be removed, not just documented as "don't use the default."
- No secret-rotation runbook exists yet (PH1.8 deliverable).

---

# 24. Environment Security

- `APP_ENV` is the single environment discriminator used consistently across `security/cookies.py` and `security/cors.py` (`is_production()` is the shared primitive both modules import from `security.cookies` — deliberate, so the two modules can never disagree about what "production" means).
- No boot-time configuration validator exists yet — a misconfigured production deployment (weak `JWT_SECRET`, missing `CORS_ALLOWED_ORIGINS`) is only caught at request time (e.g., empty CORS allowlist) or not caught at all (weak but present `JWT_SECRET`). PH1.8 closes this with a typed env-var registry that hard-fails startup on any missing/weak required production value.
- Three-environment strategy (dev/staging/prod) is designed in PRODUCTION_HARDENING.md §5 but staging does not yet exist as a running environment (PH2.12).

---

# 25. Dependency Security

`backend/requirements.txt` currently pins the relevant security-critical libraries: `bcrypt==4.1.3`, `PyJWT==2.13.0`, `google-auth==2.53.0`, `email-validator==2.3.0`, `redis==5.0.8`, `httpx==0.28.1`. No automated vulnerability scanning exists yet (`pip-audit`/`npm audit`, Dependabot) — PH1.11 scope. Dev tooling (`black`, `flake8`) is still pinned in the runtime `requirements.txt` rather than a separate `requirements-dev.txt` (finding M14), meaning the production image currently ships tooling it doesn't need.

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
│   ├── csrf.py             (planned, unscheduled) CSRF token middleware — see §18
│   ├── headers.py          (planned, PH1.4b) security-header middleware
│   ├── tokens.py           (planned, PH1.6) JWT issuance/rotation/revocation service
│   ├── rate_limit.py       (planned, PH1.7) tiered rate limiter
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
    └── test_password_policy.py   (PH1.5) 40 tests — password policy + login compatibility
```

Convention established by PH1.1–PH1.4 and binding on every future security sprint: **one module per concern under `backend/security/`, one test file per module under `backend/tests/`, named `test_<concern>.py`.**

---

# 27. Security Middleware Pipeline

Current pipeline (as wired in `server.py` today):

```
Request
   │
   ▼
CORSMiddleware (security/cors.py, apply_cors)   ── origin check, preflight
   │
   ▼
Route dispatch (FastAPI router matching)
   │
   ▼
Depends(get_current_user) / Depends(require_admin)   ── identity + role
   │
   ▼
Pydantic model validation (route parameters/body)
   │
   ▼
Route handler (business logic, per-user data scoping)
   │
   ▼
Response (+ set/clear cookies via security/cookies.py where applicable)
```

**Target pipeline** once PH1.4b–PH1.9 land (additions in bold):

```
Request → **Rate Limiter (PH1.7)** → CORS → **Security Headers (PH1.4b)** →
Route dispatch → **CSRF token check on state-changing routes (unscheduled, §18)** →
Auth (get_current_user/require_admin) → Model validation → Handler →
**Audit log write (where applicable)** → Response (+ security headers, cookies)
```

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
Client                                    Server
  │  POST /api/auth/refresh                 │
  │  (refresh_token cookie)                 │
  ├─────────────────────────────────────────►
  │                                          │ token = cookie["refresh_token"]
  │                                          │   missing → 401 "No refresh token"
  │                                          │ decode(token, JWT_SECRET, HS256)
  │                                          │ assert type == "refresh" → else 401
  │                                          │ access = create_access_token(sub)
  │                                          │ set_access_cookie(access)
  │  ◄───────────────────────────────────────┤   NOTE: refresh_token cookie is
  │  200 + new access_token cookie           │   NOT rotated (PH1.6 will change this)
```

---

# 31. Logout Sequence

```
Client                                    Server
  │  POST /api/auth/logout                  │
  ├─────────────────────────────────────────►
  │                                          │ clear_auth_cookies(response):
  │                                          │   delete access_token  (path=/, matching attrs)
  │                                          │   delete refresh_token (path=/, matching attrs)
  │  ◄───────────────────────────────────────┤
  │  200 {"message": "Logged out"}           │
```

No server-side revocation occurs — the JWTs remain cryptographically valid until their natural expiry even after logout; only the cookie is removed from the browser. This is a direct consequence of the stateless-JWT design in §9 and is closed by the PH1.6 revocation store (a "logout" will additionally revoke the token family server-side once that exists).

---

# 32. Future Production Hardening Plan

This section is the authoritative forward-looking security sequence; PRODUCTION_ROADMAP.md owns sprint-level acceptance criteria and PRODUCTION_HARDENING.md owns program-level strategy — this section is what ties them to the architecture described above.

| Sprint | Architectural change this document will need to reflect |
|---|---|
| PH1.4b Security Headers | New `security/headers.py` module in §26; pipeline update in §27 |
| PH1.5 Password/Email/Verification | ✅ Password portion done (§15 now "current"). §16, §17 and `EmailStr` deferred to unscheduled PH1.5b |
| PH1.6 JWT & Refresh Rotation | §9, §11, §12, §30 rewritten for rotation + revocation store; `jti` claim added |
| PH1.7 Rate Limiting | §21 rewritten; login-lockout folded into the new limiter per the note in §21 |
| PH1.8 Secrets & Env Hardening | §23, §24 updated with the boot-time validator's actual registry |
| PH1.9 WebSocket Security | New §"Real-Time Authorization Architecture"; §4 trust-boundary diagram gains a Socket.IO lane |
| PH1.10 Admin & Sessions | §9 session-listing moves to "current"; ADR-028 MFA design referenced from §14 |
| PH1.11 Dependency Scanning | §25 updated with scan tooling and triage policy |
| PH1.12 Security Certification | This document is the primary evidence artifact reviewed against the pen-test checklist |
| Unscheduled: CSRF token layer | §18 — recommended to be its own sprint (see Documentation Synchronization Report) |
| Unscheduled: Password reset flow | §17 — recommended to be folded into PH1.5 |
| Unscheduled: OAuth module extraction | §26 — housekeeping, no urgency |

**Rule for future sprints:** every PH1.4b–PH1.12 sprint that changes authentication, authorization, session, cookie, CORS, or transport-security behavior updates the relevant numbered section(s) of this document in the same PR (PRODUCTION_HARDENING.md §15 verification checklist already requires "authoritative documentation updated in the same PR" — this document is that authority for security).

---

# 33. Security Coding Standards

In addition to CODING_STANDARDS.md and PRODUCTION_HARDENING.md §19 (Engineering Standards addendum):

1. No new authentication, session, cookie, or CORS logic is written outside `backend/security/`. If a route needs cookie or origin behavior, it imports from `security.cookies` / `security.cors` — it does not call `response.set_cookie` or configure `CORSMiddleware` itself.
2. Every new endpoint declares its auth dependency explicitly (`Depends(get_current_user)` or `Depends(require_admin)`) — there is no "implicitly public" route; public routes are public by the *absence* of a dependency, which must be a deliberate, reviewed choice.
3. Every new security module ships with a docstring explaining the *why*, matching the style of `cookies.py`/`cors.py` — future engineers should be able to read the module and understand the threat it defends against without external context.
4. Every OAuth/auth outcome that can fail must call `log_auth_event` with a specific, machine-readable `reason` before returning an error — vague or missing failure reasons defeat the audit trail's purpose.
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

StockAssist AI's security architecture, as of PH1.4 completion, is **strong at the transport/session-integrity layer and thin at the authorization/lifecycle layer**: cookies and CORS are production-hardened and centrally owned (§10, §19); OAuth is genuinely fail-closed with real cryptographic verification (§13); but JWTs don't rotate or revoke (§12), rate limiting is a single narrow mechanism (§21), authorization is binary rather than fine-grained (§8), and a CSRF token layer has no owner (§18). PH1.5–PH1.12 closes these in a defined order; this document is what keeps that sequence coherent instead of ad hoc.

---

# Implementation Status

| Area | Status | Reference |
|---|---|---|
| Auth backdoor removal | ✅ Complete | §Threat Model, PH1.1 |
| Google OAuth hardening | ✅ Complete | §13, §29, PH1.2 |
| Cookie security | ✅ Complete | §10, PH1.3 |
| CORS hardening | ✅ Complete | §19, PH1.4 |
| Security headers | ❌ Not started | §20, PH1.4b |
| Password policy | ✅ Complete | §15, PH1.5 |
| Email policy/verification | ❌ Not started (deferred from PH1.5 → PH1.5b) | §16–§17 |
| JWT lifecycle/rotation | ❌ Not started | §11–§12, PH1.6 |
| Rate limiting | ⚠️ Partial (login only) | §21, PH1.7 |
| Secrets/env validation | ❌ Not started | §23–§24, PH1.8 |
| WebSocket security | ❌ Not started | §32, PH1.9 |
| Admin/session management | ❌ Not started | §9, PH1.10 |
| Dependency scanning | ❌ Not started | §25, PH1.11 |
| Security certification | ❌ Not started | §34, PH1.12 |
| CSRF token layer | ❌ Not started, unscheduled | §18 |
| Password reset flow | ❌ Not started, unscheduled | §17 |
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
| 1.1 | 2026-07-19 | PH1.5 (Password Policy & Account Protection) implemented: §15 rewritten from target to current (centralized `security/passwords.py`, model-layer 422 enforcement, explicit bcrypt cost 12, timing-equalized/never-raising verification, sanitized validation errors); §26 module tree and §3 threat table updated. §16/§17 (email verification, password reset) deliberately deferred to an unscheduled PH1.5b. |
| 1.0 | 2026-07-18 | Initial document. Synthesizes PH1.1–PH1.4 implementation (auth backdoor removal, Google OAuth hardening, cookie security, CORS hardening) into the permanent security architecture blueprint. Establishes the CSRF-token-layer and password-reset-flow documentation gaps identified during this synchronization sprint. |

---

# End of Security Architecture Document

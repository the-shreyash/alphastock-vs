# StockAssist AI — PH3.10 Final Production Audit

**Sprint:** PH3.10 — Final Production Audit
**Date:** 2026-08-17
**Auditor:** Principal Release & Security Engineer
**Baseline commit:** `fe70d13` + the uncommitted PH3.9 working tree
**Environment:** macOS (darwin 25.5.0), Docker 29.4.0 (arm64), Python 3.11.16, Node 23.11.0
**Preceding certifications:** `docs/security/PH1_CERTIFICATION.md` (CERTIFIED),
`docs/infrastructure/PH2_CERTIFICATION.md` (CONDITIONALLY CERTIFIED, 8.0/10),
`docs/architecture/ANALYTICS.md` §11 (PH3.9 mock removal)

> **Naming note.** The brief labels this sprint "PH3.10 — Final Production Audit".
> `PRODUCTION_ROADMAP.md`'s PH3.10 is *Documentation Synchronization*. This is the
> same brief-label drift the roadmap already records for PH3.2–PH3.9 and is called
> out here so the two documents can be reconciled rather than silently disagreeing.

---

## 1. Executive Summary

This audit set out to answer one question: **is StockAssist AI genuinely ready to
proceed to PH3.11 regression and PH3.12 certification?**

The answer is **yes, conditionally** — but only after four defects were found and
fixed during the sprint, two of which were production blockers that every
preceding phase had missed.

**The finding that matters most is a complete authorization failure in the
realtime layer.** `/api/ws` took the identity it fans per-user events out on —
notifications, portfolio updates, trade-engine events, broker order updates —
directly from an unauthenticated query parameter. Any anonymous client on the
internet could connect as `wss://host/api/ws?user_id=<victim>` and receive that
account's private stream in real time. This was **reproduced live against the
production container** before it was fixed. It is not a new regression: it was
identified as "S-2, tracked to PH1.9" in a PH3.6 code comment, deferred, and then
never scheduled. PH1 certified security with this open, because PH1 explicitly
scoped WebSocket authorization out (`PH1_CERTIFICATION.md` §8, "PH1.9(rt)").

**The second blocker is that the frontend has not built since 2026-08-03.**
Commit `930432d` added `frontend/.eslintrc.json` extending `react-app` without
adding `eslint-config-react-app` to the project's dependencies. `npm run build`
has exited 1 for fourteen days. There was no deployable frontend artifact — and
`frontend/build/` contained a single stray `logo.svg`.

**These two are connected by a third finding, and that connection is the real
lesson of this sprint.** No CI job has ever built or tested the frontend.
`backend-ci` covers the backend, `docker-build` covers the backend image,
`dependency-audit` and `codeql` read frontend files without executing them. So a
repository with 395 passing frontend tests and a completely broken production
build reported green on every check for two weeks. **A gate that does not exist
cannot fail, and an audit is the wrong instrument for catching a broken build.**

**A methodological note, because it changes how the preceding reports should be
read.** Every category this audit found a defect in was previously reported as
passing, and each report was accurate within its own scope. PH1 certified
security but scoped WebSocket authorization out. PH2 certified infrastructure and
correctly recorded "no frontend production image", which is a different statement
from "the frontend does not build". PH3.3 inventoried 201 routes by resolved
dependency graph — an excellent technique that classified `/api/ws` as neither
protected nor admin, because it is a WebSocket route and the sweep only walks
`APIRoute`. **The gap was never inside any sprint's scope; it was between them.**
That is what a final audit is for, and it is the argument for PH3.12 re-verifying
across boundaries rather than aggregating per-phase verdicts.

**Recommendation: GO TO PH3.11**, with seven documented conditions (§35) that are
deployment prerequisites rather than code defects, and one hard architectural
constraint: **the platform supports exactly one backend process.**

---

## 2. Audit Scope

All 35 categories in the brief were examined. Verification was **direct against
the current tree and a live production container**, not by reading prior
completion reports — those were used only to establish what was previously
claimed, so that divergence could be identified.

Coverage of the brief's categories:

| Examined by direct code/config inspection | Examined against a live production container |
|---|---|
| Security, authn, authz, OAuth, CSRF, JWT/session, rate limiting, password policy, email verification, API security, WebSocket, background tasks, trading engine, market data, AI, analytics, frontend, secrets, docs | Docker build/runtime, config fail-closed, health/readiness, security headers, CORS, cookies, auth flows, authorization boundaries, rate limiting, WebSocket authorization, Mongo connectivity + indexes, Redis connectivity + circuit breaker, log redaction, graceful shutdown |

**Not verified in this sprint, and deliberately so:** multi-day soak, multi-worker
resource behaviour (unsupported topology — see F-4), off-host backup transport (no
remote configured), real SMTP delivery (no provider provisioned), and a browser
device matrix. Each is listed as a condition in §35 rather than assumed.

---

## 3. Repository Baseline

| Measure | Value |
|---|---|
| Backend routes (from the live app object) | **201** — 97 user-authenticated, 29 admin, 75 public |
| Backend tests at audit start | **2,534 passed**, 4 xfailed, 95 deselected (2m47s) |
| Backend tests at audit end | **2,559 passed**, 4 xfailed, 0 failed (2m54s) |
| Frontend tests | **395 passed**, 22 suites (both before and after) |
| Frontend production build at audit start | **FAILED (exit 1)** |
| Frontend production build at audit end | **PASSES (exit 0)** — 48 JS bundles, 14 MB |
| Backend image | 424 MB disk / 82.1 MB content, non-root uid 10001, no pip |
| Mongo indexes created live | **62** across 20 collections, incl. TTL on `sessions`/`rate_limits` |
| `server.py` | 6,845 → 6,954 lines |

---

## 4. Security Results — PH1 Re-Audit

Every original PH1 critical finding was searched for directly in source. **None
have returned.**

| Original finding | Verified by | Result |
|---|---|---|
| Admin auto-login backdoor | grep `auto-login`, `auto_login`, `ENABLE_AUTO_LOGIN` | **CLEAN** — only remaining hits are in `security/secrets.py`, which *rejects* the variable in production |
| Google OAuth demo-user bypass | grep `demo-user`, `demo_user`, `mock-code-for-testing`, `demobackend` | **CLEAN** — zero hits |
| Wildcard CORS with credentials | `security/cors.py` + live `Origin:` probe | **CLEAN** — exact allowlist; wildcard stripped at parse; disallowed origin receives no `Access-Control-Allow-Origin` |
| Insecure cookies | live cookie jar inspection | **CLEAN** — `access_token`/`refresh_token` HttpOnly + Secure; `csrf_token` Secure, readable by design |
| Excessive JWT lifetime | live token `exp - iat` | **CLEAN** — access exactly 900s, refresh 7 days |
| Non-rotating refresh / replay | `server.py:1281-1298`, `security/sessions.py` | **CLEAN** — single-use rotation, reuse detection revokes the family, audited as CRITICAL |
| Missing rate limiting | live: 7 rapid bad logins | **CLEAN** — `401 401 401 401 401 429 429` |
| Weak password policy | live: `password` at registration | **CLEAN** — 422 |
| Missing security headers | live `curl -I` | **CLEAN** — see §8 |
| Privilege escalation | `security/roles.py`, `test_roles.py` | **CLEAN** — F-1 fix intact |
| Unsafe ObjectId handling | `security/identifiers.py` | **CLEAN** — F-2 fix intact |
| Sensitive info leakage | live log scan against real secrets | **CLEAN** — 0 hits; Redis/Mongo URLs redacted to `redis://***@host` |
| Fail-open authentication | `get_current_user` | **CLEAN** — every path raises |
| Debug mode in production | grep `debug=True`, `reload=True` | **CLEAN** |

**Conclusion: no PH1 regression.** The security package remains the single place
each concern is enforced.

---

## 5. Authentication Results

Every path traced end to end and exercised live.

| Path | Result |
|---|---|
| Registration | **PASS** — rate limited 5/hr/IP, duplicate email 400, password policy at the model layer, verification email dispatched out-of-band so a slow mailer cannot fail signup |
| Login | **PASS** — constant-time-ish (one bcrypt comparison always runs, padded for missing accounts), identical 401 for unknown-email and wrong-password, failure budget counted only on failure |
| Logout | **PASS** — server-side session revoke + central cookie clear; tolerant of a missing/invalid refresh cookie |
| Logout-all | **PASS** — `revoke_all_for_user`, audited with a revoked count |
| Refresh | **PASS** — rotation, single use, reuse detection → family revocation, 20/min/session limit, `password_changed_at` honoured |
| Password reset / change | **PASS** — single-use expiring tokens, session invalidation via `password_changed_at` |
| Google OAuth | **PASS** — see §7 |
| Session management | **PASS** — `sessions` collection with a live TTL index |

**Negative paths tested live:** anonymous → 401; forged signature → 401 *and*
audited as `INVALID_JWT`; refresh token presented as an access token → rejected;
token for a deleted account → rejected; token older than `password_changed_at` →
rejected.

**Account enumeration** is minimised deliberately and the code documents why —
including that the audit record itself keeps the same discretion so the security
log does not become the oracle the API refuses to be.

---

## 6. Authorization Results

`tests/_routes.py` classifies all 201 routes by **resolved dependency graph**
rather than URL — a route that merely looks administrative is not trusted to be
one. Three suites parametrise over that classification, so a new route is covered
automatically.

| Boundary | Result |
|---|---|
| Anonymous → 126 authenticated routes | **PASS** — all 401 |
| Forged token → authenticated routes | **PASS** — all 401 |
| Non-admin → 29 admin routes | **PASS** — all 403 (verified live: `/api/admin/dashboard` → 403) |
| Horizontal (cross-user resources) | **PASS** — asserted against the *stored document*, not just status |
| admin → super_admin escalation | **PASS** — `validate_role_assignment` (F-1) |
| Self-role escalation | **PASS** |
| **WebSocket subscription authorization** | **WAS FAIL → FIXED** (F-1, §31) |

**The 75 public routes were individually reviewed.** All are intentional. Three
warranted scrutiny and each is correctly protected by a mechanism outside the
dependency graph:

* `/api/metrics`, `/api/diagnostics*` — gated by `METRICS_TOKEN` in production and
  **fail closed** when unset (403 naming the variable). Verified live: **401**
  without the token.
* `/api/webhooks/*` — `X-Webhook-Key` with `secrets.compare_digest`, fail-closed
  when `WEBHOOK_API_KEY` is unset.
* `/api/analysis/explain`, `/api/analysis/full-report` — genuinely public and
  LLM-backed. See P2-1 (§33): a cost-amplification surface, bounded but not closed.

---

## 7. OAuth Results

Attempted tampering at each step; the flow is **the strongest-implemented surface
in the codebase**.

| Control | Result |
|---|---|
| State generation, cookie, single-use, expiry | **PASS** — HttpOnly cookie + server-side record consumed by fetch-and-delete; cookie burned regardless of outcome |
| Redirect URI allowlist **and** binding to the state record | **PASS** — defence in depth against redirect substitution |
| `id_token` verification with audience | **PASS** — `client_id` passed as audience |
| Issuer validation | **PASS** — `GOOGLE_VALID_ISSUERS` |
| `email_verified` enforcement | **PASS** — fail-closed; an unverified Google email can neither create nor link an account |
| Stable Google subject as primary identity | **PASS** — resolved by `sub` first; email never mutated off a `sub` match |
| Duplicate-email / sub-conflict | **PASS** — never silently re-links; 401 + audit |
| Unconfigured credentials | **PASS** — 401, no fallback |
| Network failure / malformed exchange | **PASS** — 502 / typed 401s, each audited with a distinct reason |

---

## 8. Cookie / CORS / Header Results

Measured against the **live production container**.

**Cookies** — `access_token` and `refresh_token` HttpOnly + Secure; `csrf_token`
Secure and script-readable by design; correct path; no duplicate auth cookies;
deletion attributes resolved through the same policy that set them.

**CORS** — disallowed origin receives **no** `Access-Control-Allow-Origin`;
allowed origin receives the exact origin plus
`Access-Control-Allow-Credentials: true`; methods and request headers are
enumerated rather than reflected; production fails closed on an empty allowlist.

**Security headers** — all present on a live response:

```
strict-transport-security: max-age=63072000; includeSubDomains
content-security-policy:   default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
x-content-type-options:    nosniff
x-frame-options:           DENY
referrer-policy:           strict-origin-when-cross-origin
permissions-policy:        accelerometer=(), autoplay=(), camera=(), ... (17 features denied)
```

`server:` is suppressed (`--no-server-header`). **No reverse-proxy override is
possible to verify** — there is no frontend/edge image in the repository (PH2 H8);
re-check after that ships.

---

## 9. CSRF Results

Architecture: **signed double-submit bound to the session**. The token is a nonce
plus an HMAC over `session_id + nonce`, so a token minted for another session is
rejected — not merely a value-equality check.

**Bearer requests are exempt by design**, and the reasoning is sound: a cross-site
attacker cannot read a `localStorage` token and cannot attach an `Authorization`
header cross-site without a preflight the allowlist denies. Enforcement therefore
targets exactly the cookie-only ambient-authority surface.

**OAuth state protection and general CSRF are correctly separate mechanisms** —
`security/csrf.py` and the OAuth state cookie in `server.py` are independent, and
the brief's concern about conflating them does not apply here.

Missing / incorrect / wrong-session tokens all → **403, fail closed**.

---

## 10. JWT / Session Results

Access token claims verified on a live token: `sub`, `type`, `sid`, `jti`, `iat`,
`exp`, `aud=stockassist-ai-app`, `iss=stockassist-ai`, `ver=1`, `email`.
`exp - iat = 900s` exactly. Refresh: 7 days, `type=refresh`.

`decode_token` requires **all** of `exp/iat/aud/iss/sub/jti/type/ver/sid` and
rejects anything missing one — a deliberate clean cutover with **no legacy-token
acceptance path**. Verified: wrong type rejected, forged signature rejected,
expired rejected, `password_changed_at` invalidation honoured on access **and**
refresh. `TOKEN_VERSION` is a pinned in-code global kill switch.

---

## 11. Rate Limiting Results

| Surface | Policy | Verified |
|---|---|---|
| Login | 5 / 15 min per `ip:account`, escalating lockout | **live** — 429 after 5 |
| Register | 5 / hr per IP | code |
| Refresh | 20 / min per session | code |
| Password reset/change | 5 / hr per `ip:account` | code |
| Authenticated API | 120 / min per user | middleware |
| Anonymous API | 60 / min per IP | middleware |
| Health/metrics/diagnostics | **exempt, deliberately** | code — documented: probe cadence would otherwise consume the anonymous budget and the orchestrator would read the resulting 429 as unhealthy and restart a working container |

Storage is **MongoDB-backed and therefore shared across processes**; the lockout
from a login test survived a full container restart, which is direct evidence the
limiter is durable rather than in-memory.

Storage failure **degrades to allow** (logged) so the throttle can never take the
API down.

**Gap: WebSocket connection establishment is not rate limited.** The middleware
returns early on non-HTTP scopes. Materially reduced by F-1 (a handshake now
requires a valid access token, so an anonymous flood is rejected at
authentication), but a valid token can still open unbounded sockets. See P2-2.

---

## 12. Password / Email Verification Results

Centralised in `security/passwords.py`: length floor and ceiling, complexity,
blocklist, and rejection of passwords derived from the user's own email or name.
Enforced at the **model layer**, so a weak password is 422 before it reaches
hashing. bcrypt with a pinned cost. Login is deliberately unvalidated so existing
accounts keep working.

**Repository credential scan: clean.** A high-signal pattern sweep
(`sk-ant-*`, `AIza*`, `sk_live_*`, `rzp_live_*`, AWS keys, PEM private keys) across
all source, config, docs and env templates returned **four hits, all synthetic
fixtures inside `backend/tests/`** — no live credential is committed. `.env` is
git-ignored with `!.env.example` re-included. No password appears in any log.

**Email delivery is still simulated** — `email_service.py` reports
`mode: "simulated"` with no SMTP/SendGrid provider. Per the brief this is
classified explicitly as a **deployment prerequisite, not a working feature**
(condition C-1, §35). The recovery flow itself is provider-agnostic and complete;
tokens expire, are single-use, and fail closed.

**`email_verified` is not enforced at login** — deliberate and documented as the
hook a future hard gate would flip.

---

## 13. API Security Results

The PH3.3 mechanical sweeps remain green across all 201 routes: pagination bounds
are declarative (`Query` constraints, closing both the negative-skip 500 and an
unbounded full-collection scan), `ObjectId` parsing goes through
`security/identifiers.py` at every trust boundary, a central `JSONDecodeError`
handler covers the 18 routes that read raw bodies, and every 4xx carries a
parseable `detail`.

**No response leaks** `Traceback`, filesystem paths, `motor`, `pymongo`,
`site-packages` or `server.py` — asserted mechanically across the whole route
table.

Mongo access is via Motor with structured queries; **no string-built queries and
no user-controlled sort/projection/filter passthrough** were found.

---

## 14. WebSocket Results — the P0

### What was wrong

```python
# backend/server.py, before this sprint
user_id = websocket.query_params.get("user_id", "anonymous")
await ws_manager.connect(websocket, user_id)
```

`ConnectionManager.send_to_user(user_id, …)` is the delivery path for
notifications, per-user portfolio and trade updates
(`services/realtime/event_bridge.py:95`), broker order and connection events
(`services/broker_engine.py`), trade-engine lifecycle events
(`services/trading_engine.py:466`) and heartbeats
(`services/heartbeat_engine.py:54`). Keying that on a caller-supplied string is a
total authorization failure for the realtime surface.

### Reproduction, against the live production container

```
CONNECTED anonymously as user_id=6a82c5b67e9abca6f4437d83
SERVER: {"type":"subscribed","channels":["*"]}
[attacker] RECEIVED VICTIM EVENT: {"type":"event","event":"sector.updated",...}
[attacker] RECEIVED VICTIM EVENT: {"type":"market_update",...}
```

No credential of any kind was presented.

### The fix

Authentication is now **required, and resolved before `accept()`** so a rejected
caller never occupies a connection slot or enters any tracking map. Identity is
the verified `sub` of a valid access token; the `user_id` parameter is **ignored
entirely**. Validation mirrors `get_current_user` exactly — same token type, same
`password_changed_at` kill switch, same account-state check — because a socket is
a long-lived private feed and a password reset must close it.

**The credential travels by cookie or `Sec-WebSocket-Protocol`, never a query
string.** The first implementation used `?token=`; the live container proved that
uvicorn logs the request line verbatim, writing a live 15-minute credential into
container logs on every handshake. That transport was removed and is now
explicitly rejected by a regression test.

### Verification, live

```
anonymous + spoofed user_id (the P0)              REJECTED
token in query string (log-leaking transport)     REJECTED
authenticated via subprotocol                      CONNECTED
```

17 hermetic tests in `backend/tests/test_ws_authentication.py`. **Confirmed
non-vacuous**: run against the pre-fix `server.py` from `HEAD`, the suite hangs
on the anonymous connect (it succeeds and blocks awaiting a frame) rather than
passing.

---

## 15. Background Task / Resource Results

Verified on a live shutdown:

```
Shutdown initiated — readiness now reports draining
AI heartbeat engine stopped
Cancelled 2 background task(s)
Redis pub/sub subscriber stopped on 'sa:events'
Redis client closed
Closed 1 pooled HTTP client(s).
Application shutdown complete.
```

**Clean stop in 1–2 s, exit code 0** (no SIGKILL). Every loop, subscriber, client
and pool has explicit lifecycle ownership via `infrastructure/tasks.py`, and the
readiness probe reports `draining` before teardown so a load balancer stops
sending traffic first.

PH3.6/3.7b bounds hold: the `user_connections` map is emptied on both the clean
and reaped paths, the AI context cache enforces its TTL, fan-out iterates
snapshots, and six gauges expose the counts. `websocket_connections_total{outcome="rejected"}`
— a metric that existed but which nothing had ever emitted — is now wired to the
authentication rejection path.

---

## 16. Trading Engine Results

Reviewed as a financial system, not via HTTP tests.

**Correct:** trailing-stop ratchet never places an order; partial exits recompute
remaining quantity and booked P&L; three-outcome classification (not two);
short/long sign conventions centralised; auto-exit failure is surfaced to the user
as an explicit "exit manually NOW" instead of being swallowed; `is_paper` trades
are excluded from the live cycle; broker exits go through the adapter boundary.

**Simulated behaviour is correctly separated.** Paper trading is labelled
throughout; the `MOCK_` order-id guard in `zerodha_service.py` is a defensive
vestige that fails closed; every broker path returns an explicit `error` when the
account is not connected rather than fabricating a fill. **No simulated data can
present as real trading data.**

**Two findings:**

* **P2-3 — the monitoring cycle is capped at 200 open trades platform-wide**
  (`db.trades.find(...).to_list(200)`). Beyond that, trades are silently
  unmonitored: no stop-loss, no target. Not reachable at current scale, and the
  fix (cursor iteration) changes memory behaviour under load, so it is documented
  rather than changed during an audit.
* **F-4 — duplicate exit orders under any multi-process topology.** See §21; this
  is the most consequential non-security finding in the audit.

---

## 17. Market Data Results

The gateway/source-manager boundary holds: no business logic, AI module or
frontend code calls a provider directly, and no provider identity leaks to the
client. Containment lives at the transport boundary — a provider timeout reaches a
route as `None`, never as an exception. (PH3.3 records three suites that initially
"found" a HIGH provider-timeout defect that did not exist for exactly this reason;
those tests were rewritten rather than the application.)

Stale/failed provider data is **never presented as current**: `stock_details.py`
returns `{"available": False, "note": …}` and the frontend renders an explicit
unavailable state. `POST /api/backtest` returns **503, not a fabricated result** —
PH3.9 removed a fallback that invented twenty trades with a `randint(10,16)` win
count.

---

## 18. AI Results

API keys are secret-managed and boot validation **rejects placeholder-shaped
values** — verified live: a key of `sk-ant-api03-AUDIT-PLACEHOLDER-…` failed the
container's startup check.

The `SimulatedProvider` fallback is **honest**: it returns an explicit "AI
services are currently offline… check that ANTHROPIC_API_KEY and
GOOGLE_GEMINI_KEY are configured" message, never fabricated analysis, and
increments a counter described in-code as "the most important AI counter in the
application" precisely because this path makes a broken product look healthy.
**No hallucinated financial content can be presented as verified market data.**

AI recommendations are structurally distinct from executed trades (separate
collections, separate endpoints, separate UI surfaces).

---

## 19. Analytics Results

The PH3.9 mock scan was re-run: **75/75 pass**. Every metric resolves to REAL,
DERIVED, or UNAVAILABLE. Eleven metrics that cannot be computed return explicit
`UNAVAILABLE` with a reason rather than a plausible number; `mock_metrics: []`
appears on every analytics response as a machine-checkable assertion.

Revenue remains structurally unavailable (no payment system), and is reported as
such rather than as `role_count × hardcoded_price`. The PH3.3 refund stub (D-4)
that returned `success: true` while writing an audit record for a refund that
never happened is confirmed fixed — **501, and no audit record**.

Admin and user analytics were verified independently for authorization and data
isolation.

---

## 20. Frontend Results

| Check | Result |
|---|---|
| Production build | **WAS FAIL → FIXED** (F-2) |
| Tests | **PASS** — 395 / 22 suites |
| Hardcoded API URLs | **CLEAN** — zero |
| `console.log` / `debug` / `warn` in production source | **CLEAN** — zero (32 `console.error`, all legitimate) |
| Secrets in the built bundle | **CLEAN** — zero |
| Demo/dev login UI | **CLEAN** — zero |
| Error boundaries, loading/empty/unavailable states | **PASS** — covered by tests |
| WebSocket reconnect, timer/listener cleanup | **PASS** — backoff with jitter, heartbeat/pong, all timers cleared on unmount |

**P1-3 — the SPA's `localStorage` token goes stale and is never refreshed.**
`POST /api/auth/refresh` returns `{"message": "Token refreshed"}` with **no token
in the body** (verified live), and the axios interceptor discards the response. The
app keeps working only because `get_current_user` checks the **cookie first** and
the browser sends it. But the axios instance does **not** set
`withCredentials: true`, so cookies are only sent same-origin. **The deployment
must therefore be same-origin (reverse proxy); a cross-origin deployment breaks
15 minutes after login.** This is pre-existing, not introduced here, and the
remedy is an auth-architecture decision (return the token from `/auth/refresh`, or
adopt cookies fully with `withCredentials`) — deferred rather than chosen
unilaterally.

---

## 21. Docker / Deployment Results

**The container was built and run, not merely inspected.**

| Check | Result |
|---|---|
| Build | **PASS** |
| Non-root | **PASS** — uid/gid 10001, source owned by root (read-only to the runtime user) |
| No pip in the final image | **PASS** |
| No `--reload`, no debug | **PASS** |
| Healthcheck | **PASS** — bundled script; container reports `(healthy)` |
| Startup | **PASS** — `startup_complete` in **0.68 s** |
| Mongo connects | **PASS** — 62 indexes created, unique constraints live |
| Redis connects | **PASS** — plus an unplanned circuit-breaker demonstration: it opened under connection pressure, degraded to in-process fallback, and closed on recovery, all logged |
| Config fails closed | **PASS** — refused to boot on missing `FRONTEND_URL`, missing AI provider, **and a placeholder-shaped API key** |
| Graceful shutdown | **PASS** — 1–2 s, exit 0, every resource released |
| Frontend serves | **BLOCKED** — no frontend image exists (PH2 H8) |

**F-4 — the "scale with replicas" guidance was wrong, and dangerously so.**
Four documents (`entrypoint.sh`, `docker-compose.yml`, `docs/deployment/DOCKER.md`,
`production.env.example`) told operators to scale with additional container
replicas instead of workers. `server.py` calls `setup_scheduler()`
**unconditionally at startup with no leader election** — verified by grep: no
advisory lock, no `SETNX`, no leader anywhere in the tree. A replica is another
process running the same scheduler. `trade_monitor` fires every 60 s during market
hours and calls `run_cycle`, which places **real broker exit orders**. Two
processes means two exit orders for one position in a live brokerage account.

Redis pub/sub (PH2.7) fixed the *WebSocket* half of multi-process, which is very
likely why replicas looked safe; the scheduler half was never addressed. All four
documents are corrected, and the boot warning now names the actual consequence —
verified live at `WEB_CONCURRENCY=2`:

```
WARNING: multi-process safe: each process runs the trade monitor, which
WARNING: places real broker exit orders. Duplicate orders are possible.
WARNING: Run exactly ONE backend process (1 worker, 1 replica) until a
WARNING: single-leader scheduler ships. Do NOT scale with replicas either.
```

**The supported production topology is one backend process. Horizontal scaling is
blocked on leader election, not on load balancing.**

---

## 22. CI/CD Results

| Workflow | Covers | Verdict |
|---|---|---|
| `backend-ci` | quality, build, test, aggregate gate | **PASS** |
| `docker-build` | hadolint, build, live smoke, graceful-shutdown assertion | **PASS** |
| `security-audit` | gitleaks, tracked-`.env` guard, config sync | **PASS** |
| `dependency-audit` | pip-audit + npm audit | **PASS** (advisories open — §33) |
| `codeql` | static analysis | **PASS** |
| **frontend** | — | **WAS ABSENT → ADDED** (F-3) |
| **deployment / CD** | — | **ABSENT** (PH2 H7, deferred) |

CI does not silently ignore failures. `continue-on-error` appears only on
explicitly-labelled ADVISORY steps, and `backend-ci.yml` documents at length why
the pre-existing formatting backlog is *measured* rather than *gated* — a red
build everyone ignores is worse than no build.

**`frontend-ci.yml` was added**, mirroring the backend's structure including the
single aggregate check name for branch protection. Both jobs are blocking; ESLint
warnings are advisory against a recorded baseline of 62, for the same documented
reason. The gate was validated by running its exact recipe locally: build exit 0,
62 warnings, `index.html` present, 48 JS bundles, and `npm ci --legacy-peer-deps`
confirmed consistent with the lockfile.

---

## 23. Secrets / Configuration Results

**PASS.** Scan clean (§12). `.env` ignored, `.env.example` placeholders only,
production fails closed with a per-variable diagnostic, no development defaults.
`validate_config()` runs as a dry run in a throwaway process *before* uvicorn
starts, so a misconfiguration is a clean refusal rather than a half-started app.

Plaintext env delivery is warned about per-variable with the file-based
alternative named. `CSRF_SECRET`, `RECOVERY_SECRET` and `BROKER_TOKEN_KEY` fall
back to `JWT_SECRET` (domain-separated) with a warning — acceptable, but
condition C-2.

---

## 24. Database Results

**PASS with conditions.** Authenticated Mongo, no published port (internal
network), 62 indexes created at startup, unique constraints verified live
(duplicate email → 400; `watchlist(user_id, symbol)` unique), TTL indexes present
on `sessions` and `rate_limits`, connection pool bounded with idle reaping
(PH3.6).

`MONGO_SOCKET_TIMEOUT_MS` remains deliberately unset and **must be baselined in
staging** (carried from PH3.7b; condition C-3). TLS is a deployment concern, not
configured in the local stack.

---

## 25. Redis Results

**PASS.** Authenticated, no published port, pool of 24 with a 30 s health check,
pub/sub cross-process fan-out active, and a circuit breaker that was observed
opening and closing correctly under real connection pressure. Degradation to the
in-process fallback is graceful and logged.

---

## 26. Backup / DR Results

**BLOCKED — unchanged from PH2.12, and correctly classified as such.**

The scripts exist (`scripts/backup/`: mongo, uploads, config, verify, restore) and
PH2.12 drilled a destructive restore successfully in seconds. But **no off-host
copy is configured** (PH2 H6), which leaves the host-loss and ransomware runbooks
unexecutable — a backup on the compromised host must be assumed compromised.

Per the brief: **this is BLOCKED, not complete, and documentation existing is not
the same as the capability being operational.**

---

## 27. Monitoring / Logging Results

**PASS with one gap.** Structured JSON logs with request correlation
(`request_id`), a liveness/readiness/startup probe split that is textbook-correct
under dependency failure, a metrics registry gated by token in production, and
audit logs with recursive redaction at the sink.

**Measured secret leakage: zero.** Scanned live container logs against the actual
Mongo password, Redis password and API key in use — no hits; connection strings
appear redacted as `redis://***@host`.

**Gap: no alerting** (PH2 M1). Detection is entirely manual, which per PH2.10's own
RTO decomposition is the dominant term in recovery time. Condition C-4.

---

## 28. Performance Results

PH3.4–PH3.6 fixes verified present: concurrent `gather` on the eleven-query admin
dashboard, range comparisons replacing unindexed regex prefix matching, pooled
outbound HTTP clients, bounded caches, snapshot iteration on fan-out.

The full backend suite runs in **2m54s with 2,559 tests**, and the container
reaches `startup_complete` in **0.68 s**.

**No new performance claim is made.** Multi-day soak, multi-worker behaviour
(unsupported topology) and TTL reaping under sustained write rate remain
unmeasured, exactly as PH3.7b recorded.

---

## 29. Testing Results

| Suite | Count | Classification | Status |
|---|---|---|---|
| Backend hermetic | **2,559** | hermetic — no server, DB, network or credentials | **PASS** |
| Backend live-server | 95 | integration — `-m "not integration"` by default | deselected by design |
| Frontend | **395** (22 suites) | hermetic | **PASS** |
| PH1 security regression | included | security | **PASS** |
| PH3.9 mock scan | 75 | data integrity | **PASS** |
| xfail | 4 | intentional, documented | correct |

The 4 xfails are all **D-10** (registration performs no email-format validation) —
pinned so they XPASS the moment it is fixed. That is the right use of xfail: a
tracked defect with a test that will announce its own resolution.

**No test is skipped, disabled or weakened to produce a green build.** The
`-m "not integration"` default is a documented selection boundary with a
mechanical classifier in `conftest.py`, not a suppression.

**One test-quality defect was found and fixed during this sprint**: three
WebSocket doubles implemented `accept()` without the `subprotocol` parameter that
Starlette's real signature carries. This is the "stub-agrees-with-bug" pattern
PH2.12 identified — a double that diverges from the real interface will keep
passing after the real call breaks. Fixed in all three (`test_event_bridge.py`,
`test_resource_lifecycle.py`, `scripts/resource_probe.py`).

---

## 30. Documentation Results

**PASS with corrections applied.** The `.claude/` corpus is unusually accurate —
architecture docs match implementation, `SECURITY_ARCHITECTURE.md` matches the
security package, and the deployment docs match the compose topology.

Corrected this sprint: the **replica-scaling guidance in four documents** (§21),
which described a topology that would duplicate live financial orders. That is the
most important documentation defect found, because it was *actionable* and wrong.

Noted for reconciliation: the PH3.10 label drift (§ header), and PH2 M5's
"Scale by replicas" mitigation which is now superseded.

---

## 31. Complete GO / NO-GO Matrix

| Category | Status | Evidence | Blocker | Owner / next phase |
|---|---|---|---|---|
| Security | **PASS** | PH1 re-audit: 14/14 original findings clean | — | — |
| Authentication | **PASS** | Live: register/login/refresh/logout/logout-all + negative paths | — | — |
| Authorization | **PASS** | 201 routes classified by dependency graph; live 401/403 | — | — |
| OAuth | **PASS** | State single-use, redirect binding, aud/iss, `email_verified` | — | — |
| Cookies | **PASS** | Live jar: HttpOnly + Secure | — | — |
| CORS | **PASS** | Live: no ACAO for disallowed origin | — | — |
| CSRF | **PASS** | Signed double-submit bound to session; 403 fail-closed | — | — |
| JWT | **PASS** | Live: 900 s access, full claim set, strict decode | — | — |
| Sessions | **PASS** | Rotation, reuse detection, family revocation, TTL index | — | — |
| Rate limiting | **PASS WITH CONDITIONS** | Live 429 after 5; durable across restart | WS handshake unlimited (P2-2) | PH3.11 |
| Password security | **PASS** | Live 422; centralised policy; scan clean | — | — |
| Email verification | **BLOCKED** | `email_service` mode = `simulated` | No SMTP provider | Deployment (C-1) |
| API security | **PASS** | PH3.3 sweeps green; no leakage across 201 routes | — | — |
| WebSocket security | **PASS** | **Fixed in-sprint (F-1)**; live re-test; 17 tests | — | — |
| Backend reliability | **PASS** | Live boot 0.68 s, clean shutdown exit 0 | — | — |
| Frontend reliability | **PASS** | **Build fixed (F-2)**; 395 tests | — | — |
| Trading engine | **PASS WITH CONDITIONS** | Logic reviewed; simulation separated | 200-trade cap (P2-3); single-process only (F-4) | PH3.11 / roadmap |
| Market data | **PASS** | Gateway boundary intact; 503 not fabrication | — | — |
| AI integration | **PASS** | Honest offline fallback; keys validated at boot | — | — |
| Analytics | **PASS** | 75/75 mock scan; no fabricated values | — | — |
| Docker | **PASS** | Built and run; non-root; fails closed | — | — |
| Compose | **PASS WITH CONDITIONS** | Topology verified | Must stay 1 replica (F-4) | Roadmap |
| CI/CD | **PASS WITH CONDITIONS** | **frontend-ci added (F-3)** | No CD pipeline (PH2 H7) | Roadmap |
| Secrets | **PASS** | Scan clean; fails closed on placeholders | — | — |
| Database | **PASS WITH CONDITIONS** | 62 indexes live; unique constraints verified | `MONGO_SOCKET_TIMEOUT_MS` unset (C-3) | Staging |
| Redis | **PASS** | Live connect; circuit breaker observed | — | — |
| Backups | **BLOCKED** | Scripts exist; PH2 drilled restore | No off-host copy (PH2 H6) | Deployment (C-5) |
| Disaster recovery | **BLOCKED** | Runbooks written | Host-loss path unexecutable | Deployment (C-5) |
| Monitoring | **PASS WITH CONDITIONS** | Probes, metrics, correlation IDs | No alerting (PH2 M1) | Deployment (C-4) |
| Logging | **PASS** | Zero secret leakage measured against real secrets | — | — |
| Performance | **PASS WITH CONDITIONS** | PH3.4–3.6 fixes present; suite 2m54s | Soak unmeasured | PH3.11 |
| Memory | **PASS WITH CONDITIONS** | PH3.6 bounds hold; gauges live | Multi-day soak unmeasured | PH3.11 |
| Testing | **PASS** | 2,559 + 395; nothing disabled | — | — |
| Documentation | **PASS** | Corrected replica guidance in 4 files | — | — |

**Totals: 24 PASS · 8 PASS WITH CONDITIONS · 3 BLOCKED · 0 FAIL.**

All three BLOCKED items are **unbuilt operational capabilities** (email delivery,
off-host backup, DR execution), not defects in code. None is newly discovered;
each carries forward from PH2.12 with the same disposition.

---

## 32. P0 Findings

### F-1 — WebSocket authorization bypass · **FIXED & VERIFIED**

Anonymous clients could bind to any account and receive its private realtime
stream. Reproduced live; fixed; re-verified live; 17 regression tests confirmed
non-vacuous against the pre-fix code. Full detail in §14.

### F-2 — Frontend production build broken for 14 days · **FIXED & VERIFIED**

`frontend/.eslintrc.json` extended `react-app` without the project depending on
`eslint-config-react-app`; the package existed only nested under
`react-scripts/node_modules` (npm nested it because the project pins eslint 9
while the config peer-requires eslint 8), so ESLint could not resolve it from the
project root. Fixed by adding the exact dependency. Build now exits 0 and emits
48 JS bundles; 395 tests still pass.

---

## 33. P1 Findings

### F-3 — No CI coverage of the frontend · **FIXED**

Root cause of F-2 remaining invisible. `frontend-ci.yml` added with blocking test
and build jobs plus an aggregate gate. §22.

### F-4 — "Scale with replicas" guidance would duplicate live broker orders · **FIXED (documentation)**

Corrected in four files; boot warning now states the real consequence and was
verified live. **The underlying capability gap — no scheduler leader election —
is deferred**, since building it is architectural work explicitly out of scope for
an audit. §21.

### P1-1 — Administrative account blocking did nothing · **FIXED & VERIFIED**

`POST /api/admin/users/{id}/block` wrote `blocked: True`, wrote an audit record,
and drove the admin list's `status=blocked` filter — but **no authentication path
ever read the flag**. A blocked user's tokens kept working, their refresh token
kept minting new access for seven more days, and they could log straight back in,
while the console reported success.

Same shape as the PH3.3 refund stub, and worse: it is the control an operator
reaches for during an active incident. Fixed with one central predicate
(`account_is_active`) read at all four points an identity is established —
`get_current_user`, `login`, `refresh`, and the new WebSocket handshake — so
revocation takes effect within the access token's 15-minute life. The login check
runs *after* the password comparison so it cannot become an enumeration oracle.
8 regression tests asserting the *consequence*, not the 200.

### P1-2 — `react-router` open redirect in the shipped bundle · **FIXED**

High-severity advisory (open redirect via backslash in `<Link>`/`useNavigate`) in
a **runtime** dependency reaching users. Fixed by a non-breaking patch bump within
the declared range (7.17.0 → 7.18.2); advisory cleared, 395 tests and the
production build green.

### P1-3 — SPA token staleness constrains deployment to same-origin · **DOCUMENTED**

§20. Pre-existing; remedy is an auth-architecture decision, deferred per the fix
policy rather than chosen unilaterally.

---

## 34. P2 / P3 Findings

| ID | Finding | Severity | Disposition |
|---|---|---|---|
| P2-1 | `/api/analysis/explain` and `/api/analysis/full-report` are public and invoke two LLM providers per call. `force: true` bypasses the cache. Bounded by 60 req/min/IP but trivially multiplied across IPs — a cost-amplification surface | P2 | Documented. Adding auth changes the public API contract; needs a product decision |
| P2-2 | WebSocket connection establishment is not rate limited (middleware returns early on non-HTTP scopes) | P2 | Documented. Materially reduced by F-1 |
| P2-3 | Trade monitoring cycle capped at 200 open trades platform-wide; beyond that, positions are silently unmonitored | P2 | Documented. Not reachable at current scale |
| P2-4 | 18 high-severity npm advisories remain, all in the CRA/webpack **build chain**, not shipped to browsers. Fully clearing them requires migrating off `react-scripts` | P2 | Documented (PH2 H5) |
| P2-5 | 62 ESLint warnings across 20 files (59 unused vars, 3 exhaustive-deps) | P3 | Baselined in `frontend-ci`; a warning-count rise now annotates the run |
| P2-6 | D-10: registration performs no email-format validation (`email: str`, not `EmailStr`) — a signup typo creates a permanently unrecoverable account | P2 | Pre-existing, pinned by 4 xfail tests. Fix in the next auth-touching sprint |
| P2-7 | **`frontend/` tracks both `package-lock.json` and `yarn.lock`.** Two competing descriptions of the dependency tree: whichever tool an engineer or a pipeline reaches for produces a different `node_modules`. This is a plausible contributing factor to F-2 — a tree installed by one tool while the other lockfile was authoritative is exactly how a declared dependency ends up nested instead of hoisted. The new `frontend-ci` uses `npm ci`, making npm the de facto authority; the repository should say so and delete the other | P2 | Documented. Deleting a tracked lockfile is a project decision, not an audit's |
| P3-1 | `__pycache__` present in the container image | P3 | `.dockerignore` hygiene |
| P3-2 | FastAPI `@app.on_event` deprecation warnings (startup/shutdown) | P3 | Migrate to lifespan handlers before a FastAPI upgrade |

---

## 35. Fixes Implemented & Conditions

### Fixes implemented in PH3.10

| # | Fix | Files | Tests |
|---|---|---|---|
| 1 | WebSocket handshake authentication + identity binding | `backend/server.py`, `frontend/src/context/RealtimeProvider.jsx` | 17 new |
| 2 | Credential moved off the query string onto `Sec-WebSocket-Protocol` | `backend/server.py`, `RealtimeProvider.jsx` | 5 of the 17 |
| 3 | `blocked` flag enforced at all four identity points | `backend/server.py` | 8 new |
| 4 | Frontend build repaired | `frontend/package.json`, `package-lock.json` | build gate |
| 5 | `frontend-ci` workflow added | `.github/workflows/frontend-ci.yml` | recipe validated locally |
| 6 | `react-router` advisory patched | `frontend/package.json`, lockfile | 395 existing |
| 7 | Replica-scaling guidance corrected | `entrypoint.sh`, `docker-compose.yml`, `DOCKER.md`, `production.env.example` | live boot warning |
| 8 | WebSocket test doubles aligned to Starlette's signature | 3 files | 66 affected tests |
| 9 | Dead `error` state removed | `AIQuickAction.jsx` | 395 existing |

**Net test change: 2,534 → 2,559 backend (+25), 395 frontend (unchanged).** No
trading logic, AI decision logic, prompt, model selection, rate-limit policy or
API contract was changed. One API *behaviour* change is intentional and is the
point of the sprint: an unauthenticated WebSocket handshake is now refused.

### Conditions on proceeding (deployment prerequisites, not code defects)

| # | Condition |
|---|---|
| C-1 | Provision a real SMTP/SendGrid provider. Email delivery is **simulated**; account recovery does not work in production without it |
| C-2 | Set dedicated `CSRF_SECRET`, `RECOVERY_SECRET`, `BROKER_TOKEN_KEY` rather than the `JWT_SECRET` fallbacks |
| C-3 | Baseline `MONGO_SOCKET_TIMEOUT_MS` in staging (carried from PH3.7b) |
| C-4 | Configure alerting. Detection is manual and dominates RTO |
| C-5 | Configure an encrypted off-host backup copy. Until then the host-loss and ransomware runbooks are unexecutable |
| C-6 | Deploy **one backend process only** (1 worker, 1 replica) until a single-leader scheduler ships |
| C-7 | Deploy same-origin (reverse proxy) until P1-3 is resolved |
| C-8 | Pick one frontend package manager and delete the other lockfile (P2-7). `frontend-ci` assumes npm |

> **Note on `frontend/yarn.lock`.** It appears in this sprint's diff as a side
> effect of `npm install`: npm rewrites a `yarn.lock` when it finds one. The
> rewrite makes it *consistent* with the change rather than stale, so it was left
> in place — but the fact that a routine npm command silently rewrites a second,
> competing lockfile is the argument for C-8, not against it.

### Deferred with owners

| Item | Owner |
|---|---|
| Scheduler leader election (unblocks horizontal scaling) | Roadmap — architectural |
| Frontend production image + CD pipeline | PH2 H7/H8 |
| D-10 email-format validation | Next auth-touching sprint |
| P2-1 public LLM endpoint authorization | Product decision |
| CRA migration (clears P2-4) | Roadmap |

---

## 36. Evidence & Commands

```bash
# Backend suite (hermetic)
cd backend && venv/bin/python -m pytest -q
#   → 2559 passed, 95 deselected, 4 xfailed in 174.21s

# Frontend suite
cd frontend && CI=true npx craco test --watchAll=false
#   → Test Suites: 22 passed, Tests: 395 passed

# Frontend production build
cd frontend && REACT_APP_BACKEND_URL="https://ci.invalid" npm run build
#   → exit 0, build/index.html + 48 JS bundles, 14M

# Route inventory
cd backend && venv/bin/python -c "from tests._routes import _classify; ..."
#   → 97 protected, 29 admin, 75 public = 201

# Image build + production run
docker build -f backend/Dockerfile -t stockassist-audit:final backend
docker run -d --name ph310-backend --network ph310net -p 18000:8000 -e APP_ENV=production ...
#   → Up (healthy); startup_complete in 0.68s

# Live security smoke
curl -sI http://127.0.0.1:18000/api                       # all 6 security headers
curl -sI -H "Origin: https://evil.example.com" .../api    # no ACAO
curl -s -o /dev/null -w '%{http_code}' .../api/metrics    # 401
for i in $(seq 7); do ... /api/auth/login (bad password); done   # 401×5 then 429×2

# WebSocket authorization re-test
#   anonymous + spoofed user_id  → REJECTED
#   token in query string        → REJECTED
#   subprotocol-authenticated    → CONNECTED

# Database
docker exec ph310-mongo mongosh --eval '...getIndexes()...'
#   → 20 collections, 62 indexes, TTL on sessions + rate_limits, email unique:true

# Log leakage (against the real secrets in use)
docker logs ph310-backend | grep -icE "rootpass123|redispass123|sk-ant-api03-[0-9a-f]{20}"
#   → 0

# Graceful shutdown
docker stop --timeout 30 ph310-backend
#   → 1-2s, exit code 0

# Non-vacuity check
git show HEAD:backend/server.py > backend/server.py && pytest tests/test_ws_authentication.py
#   → hangs on anonymous connect (pre-fix behaviour); file restored and re-verified
```

### Failure classification

Every failure observed during the audit, classified per the brief:

| Failure | Classification |
|---|---|
| WebSocket authorization bypass | **PRE-EXISTING** — known as S-2, deferred from PH1.9, never scheduled |
| Frontend build failure | **PRE-EXISTING** — introduced by `930432d` (2026-08-03) |
| `blocked` flag unenforced | **PRE-EXISTING** — since the admin portal shipped |
| `react-router` advisory | **DEPENDENCY** |
| Replica-scaling guidance | **PRE-EXISTING** documentation defect |
| 14 test failures mid-sprint | **NEW REGRESSION — introduced and fixed within this sprint.** Adding the `subprotocol` argument to `ConnectionManager.connect` broke three test doubles whose `accept()` did not match Starlette's signature. Caught by the full suite, fixed, re-verified |
| 4 xfail | **INTENTIONAL** — D-10, pinned |
| 95 deselected | **INTENTIONAL** — live-server integration suites |

---

## 37. Final Recommendation

> ## **GO TO PH3.11**

The system is genuinely ready to proceed to final regression, subject to the seven
conditions in §35.

**Why GO.** No unresolved P0 or P1 remains. Both production blockers were found,
fixed, tested, and re-verified against a live production container. The security
posture certified in PH1 holds with no regression across fourteen re-checked
findings. Infrastructure behaves correctly under live test — it fails closed on
misconfiguration, boots in under a second, connects to authenticated datastores,
degrades gracefully when Redis falters, and shuts down cleanly with every resource
released. The test suites are substantial, hermetic, and nothing is disabled to
manufacture green.

**Why not an unconditional GO.** Three categories are BLOCKED, and all three are
capabilities that were never built rather than things that are broken: email
delivery is simulated, backups have no off-host copy, and disaster recovery is
therefore unexecutable for its most important scenario. Calling those complete
because scripts and runbooks exist would repeat the exact error this audit found
elsewhere — mistaking a written artifact for a working capability. And the
platform is architecturally limited to a single backend process; that is a real
constraint on launch scale, not a footnote.

**What PH3.11 should carry forward, beyond the test plan.** The three defects this
audit found were each invisible to a well-run sprint that was doing its job
correctly, because each fell between two scopes. The route-authorization sweep was
excellent and missed `/api/ws` because it walks `APIRoute` and a WebSocket is not
one. The infrastructure certification was thorough and recorded "no frontend
image", which is true and is not the same statement as "the frontend does not
build". **PH3.11 should verify across boundaries rather than aggregating per-phase
verdicts** — and PH3.12 should not certify any capability whose working state has
not been observed, as opposed to documented.

**Do not begin PH3.11 until this report is reviewed and approved.**

---

**Signed:** Principal Release & Security Engineer — 2026-08-17
**Next review:** PH3.12 (Production Certification), when the composite readiness
score is re-evaluated against the ≥ 9.0 / no-category-< 8.0 launch definition.

# StockAssist AI — PH3.11 Release Candidate Report

**Sprint:** PH3.11 — Final Regression & Release Candidate Verification
**Date:** 2026-08-17
**Engineer:** Principal Release Engineer
**Release candidate commit:** `32437e858970505db70201ddc0174afd85bd19be` (`main`)
**Preceding gate:** `docs/production/PH3.10_FINAL_PRODUCTION_AUDIT.md` — **GO TO PH3.11**

> **STATUS — superseded 2026-08-17.** This report's verdict was **BLOCKED** on a
> single supply-chain gate failure (B-1). That blocker has been remediated and
> the candidate is now **READY FOR PH3.12 CERTIFICATION** — see
> **`docs/production/PH3.11_REMEDIATION_REPORT.md`**. This document is preserved
> as the record of what the candidate looked like when the blocker was found;
> only §25 (scorecard) and §26 (verdict) carry supersession notes.

---

## 1. Executive Summary

This sprint froze the product and re-verified it end to end as an integrated
release candidate. **No code was changed.** The working tree is byte-identical
to the commit it started from, because no confirmed regression was found that
required a fix.

**The regression result is clean, and unusually so.** Every headline number
reproduced the PH3.10 baseline exactly: 2,559 backend tests pass, 395 frontend
tests pass, the production build emits the same 48 bundles, and the
authorization surface classifies to precisely the same 97 protected / 29 admin /
75 public routes. The WebSocket authorization fix that PH3.10 landed as its P0
holds under live re-attack. Fault injection against a live production container
produced controlled degradation in every case, with zero process restarts.

**One release blocker was found, and it is not a regression.** The repository's
own `dependency-audit` CI workflow is **red on both of its jobs** — and has been
for some time without anyone recording it. PH3.10 reported CI/CD as "PASS WITH
CONDITIONS" after adding `frontend-ci`, but never executed the supply-chain gate.
Running it locally, as this brief requires (§20, "ensure local results and CI
results agree"), it exits 1:

* **Backend** — 6 advisories against pinned *runtime* dependencies
  (`cryptography` ×3, `aiohttp` ×3) that postdate the suppression list.
* **Frontend** — 18 high-severity npm advisories with **no triage mechanism at
  all**. The Python gate has a documented `--ignore-vuln` allowlist with a
  mechanical expiry; the npm gate was never given an equivalent, so it fails
  unconditionally.

**Reachability was analysed rather than assumed, and the news is good.** None of
the six backend advisories is confirmed exploitable in this deployment:
`cryptography` is used only for Fernet broker-token encryption — the codebase
contains no `pkcs7`, no `x509.verification`, no `PolicyBuilder` — and `aiohttp`
is client-only, which rules out the request-smuggling advisory outright. Two
client-side `aiohttp` advisories remain theoretically reachable through a
malformed upstream provider response. The 18 npm advisories live entirely in the
CRA build chain: **zero** of the vulnerable packages appear in the shipped
bundle, verified by grep against `build/static/js/`.

**So the product is not known to be vulnerable, but a required gate is red.**
Those are different statements and this report keeps them apart. Declaring the
candidate ready while a blocking CI job fails would be exactly the error PH3.10
warned against — mistaking a written verdict for a working capability.

**A second observation compounds it:** `SUPPRESSION_REVIEW_BY` is set to
**2026-08-22 — five days from this report**. From that date the backend audit
job warns on every run and fails 30 days later. The accepted-risk register is
about to expire on its own terms.

**Verdict: BLOCKED — see §26.** The blocker is bounded, understood, and does not
touch application code.

---

## 2. Release Candidate Baseline

| Measure | Value |
|---|---|
| Commit | `32437e858970505db70201ddc0174afd85bd19be` |
| Branch | `main` |
| Working tree | **clean** — no tracked or untracked changes, before and after |
| Backend version | `0.0.0-dev` (reported by the running app; no release tag applied) |
| Frontend version | `0.1.0` (`frontend/package.json`) |
| `server.py` | 6,998 lines |
| Python (host venv) | 3.11.15 |
| Python (image) | 3.11-slim (Debian), per `backend/Dockerfile` |
| Node / npm | v23.11.0 / 10.9.2 |
| Docker / Compose | 29.4.0 / v5.1.1 |
| MongoDB | `mongo:7.0` |
| Redis | `redis:7.2-alpine` |
| Backend lock state | `requirements.txt` (pinned `==`), `requirements-dev.txt` |
| Frontend lock state | **`package-lock.json` *and* `yarn.lock` both tracked** — PH3.10 C-8, unresolved |
| RC image | `stockassist-rc:ph311`, **424 MB**, built `--no-cache --pull` |

**Deltas from the PH3.10 baseline, each explained:**

| Item | PH3.10 | PH3.11 | Explanation |
|---|---|---|---|
| Python | 3.11.16 | 3.11.15 | Host interpreter differs; the *image* Python is what ships and is unchanged. Not a product delta. |
| `server.py` | 6,954 | 6,998 | PH3.10's figure was measured mid-sprint; commit `32437e8` added +163 lines to `server.py`. The report predates its own commit. |
| Routed endpoints | 201 | 201 | **Identical** — 97 protected / 29 admin / 75 public, by the same dependency-graph classifier. |
| Mongo collections / indexes | 20 / 62 | 19 / 61 | `ensure_indexes()` declares **42** indexes across **18** collections; 19 collections × 1 implicit `_id_` = 19. 42 + 19 = 61 ✓. The extra collection in PH3.10 was created lazily by that session's traffic. The declared index set is identical. |

Environment configuration contract verified against `security/secrets.py`:
13 variables configured, 0 file-backed, 8 warnings, 0 errors at production boot.

---

## 3. Complete Test Inventory

**Total collected: 2,658.**

| Classification | Selector | Count | Result |
|---|---|---|---|
| Hermetic (default run) | `pytest` | 2,559 | **2,559 passed, 0 failed** |
| Security | `-m security` | 452 | **452 passed** (35.74s) |
| Integration / live-server | `-m integration` | 95 | deselected by design (`-m "not integration"`) |
| Live | `-m live` | 95 | same 95 suites |
| Slow | `-m slow` | 6 | included in default run |
| E2E marker | `-m e2e` | 0 | marker registered, unused |
| `requires_db` / `requires_redis` / `allow_network` | — | 0 | registered, unused — **no test claims a network exemption** |
| xfail | — | 4 | intentional (D-10, email-format validation) |
| Frontend | `craco test` | 395 (22 suites) | **395 passed** |

Arithmetic: 2,559 passed + 95 deselected + 4 xfailed = **2,658** ✓

**Comparison to PH3.10: identical on every axis** — 2,559 / 95 / 4 backend, 395 /
22 frontend. Runtime 187.63s vs 174.21s (host load variance, not a regression).

**Nothing is skipped, disabled, weakened, or newly xfailed.** The 4 xfails are
the same 4 D-10 pins, and they remain pinned so they XPASS the moment
registration gains email validation.

---

## 4. Backend Regression — Per-Suite Results

All green. Counts recorded for future baselining.

| Suite | Tests | Suite | Tests |
|---|---|---|---|
| `test_api_authz` | 307 | `test_password_policy` | 40 |
| `test_analytics` | 154 | `test_backup_restore` | 39 |
| `test_ph39_mock_removal` | 75 | `test_perf_regression` | 37 |
| `test_api_market_data` | 68 | `test_trading_engine` | 36 |
| `test_redis_infrastructure` | 50 | `test_api_trading` | 35 |
| `test_api_ai` | 48 | `test_jwt_sessions` | 34 |
| `test_secrets` | 43 | `test_roles` | 31 |
| `test_disaster_recovery` | 43 | `test_cors_hardening` | 30 |
| `test_resource_lifecycle` | 28 | `test_oauth_hardening` | 28 |
| `test_rate_limit` | 26 | `test_cookie_security` | 23 |
| `test_csrf` | 18 | `test_identifiers` | 17 |
| `test_ws_authentication` | 17 | `test_account_blocking` | 8 |
| `test_advisor` | 7 | `test_paper_trading` | 6 |
| `test_webhooks` | 5 | | |

---

## 5. Frontend Regression

| Check | Result |
|---|---|
| Test suite | **395 passed / 22 suites**, exit 0 |
| Production build | **exit 0** — `REACT_APP_BACKEND_URL="https://ci.invalid" npm run build` |
| Build output | `index.html` + **48 JS bundles**, **14 MB** — identical to PH3.10 |
| Secret leakage in bundle | **0** — no API-key, JWT or Mongo-URI pattern in `build/` |

PH3.10's F-2 (a 14-day-broken build) remains fixed.

---

## 6. Authentication E2E — Live Production Container

Executed against `stockassist-rc:ph311` running with `APP_ENV=production`,
authenticated MongoDB and Redis.

| Flow | Result |
|---|---|
| Register (new user) | **200** |
| Register — password containing the user's name | **422** `password_policy` — policy enforced |
| Login | **200**, identity + `token` in body, cookies set |
| Authenticated request (cookie jar) | **200** |
| Authenticated request (Bearer) | **200** |
| Unauthenticated request | **401** |
| Refresh #1 | **200**, refresh token **rotated** (value changed) |
| **Replay of the consumed refresh token** | **401** |
| Rotated token after the replay | **401** — **family revoked** |
| Logout | **200**; refresh afterwards **401** |
| Login wrong password / unknown user / malformed JWT | **401 / 401 / 401** |
| Logout-all across 3 concurrent sessions | `sessions_revoked: 6`; **all three** subsequent refreshes **401**, including the caller's |

**Token and cookie posture (live):**

| Property | Observed |
|---|---|
| Access token lifetime | **exactly 900 s** |
| Claims | `aud, email, exp, iat, iss, jti, sid, sub, type, ver` |
| `access_token` cookie | HttpOnly ✓ Secure ✓ |
| `refresh_token` cookie | HttpOnly ✓ Secure ✓ |
| `csrf_token` cookie | Secure ✓, readable by design |

> **A correction to my own method, recorded because it changes how the evidence
> should be read.** My first logout-all probe reported the caller's session
> surviving. It was a test artifact twice over: the earlier session had been
> refreshed without persisting the rotated cookie (so its 401 was replay
> detection, not revocation), and the logout-all call itself carried no CSRF
> header and was rejected 403. Re-run cleanly with three untouched sessions and
> a proper CSRF token, revocation is total. **The confounded run looked like a
> security finding and was not one** — which is the argument for re-running a
> suspicious result before reporting it.

**OAuth** was verified by code and hermetic suite only (`test_oauth_hardening`,
28 tests: state single-use, redirect binding, `aud`/`iss`, `email_verified`).
A live Google authorization round trip requires real OAuth credentials and a
registered redirect URI; none is provisioned. Recorded as a limitation (§24),
not claimed as verified.

**Password reset** likewise: `test_recovery` and `test_password_policy` cover
token issue/consume/expiry and session invalidation hermetically. The live path
cannot be exercised because email delivery is **simulated** (PH3.10 C-1).

---

## 7. Authorization E2E

Identities created through the real registration API; the admin was promoted by
a direct operator DB action — `scripts/seed_dev_admin.py` **correctly refuses to
run when `APP_ENV=production`**, which was verified as a control in its own right.

| Endpoint | anon | user | admin |
|---|---|---|---|
| `/api/admin/users` | **401** | **403** | **200** |
| `/api/metrics` | **403** | **403** | **403** (fails closed — no `METRICS_TOKEN` configured) |
| `/api/portfolio`, `/trades`, `/notifications`, `/watchlist` | — | **200** (own data) | — |
| `/api/admin/users/{other_id}` as ordinary user | — | **403** | — |

**Malformed identifiers — every one 4xx, none 5xx:**

| Input | Status |
|---|---|
| `notanobjectid` | **400** |
| `000000000000000000000000` | **404** |
| `%20` | **400** |
| `../../etc/passwd` | **404** |
| `$ne` | **400** |

---

## 8. Security Regression

**PH1 backdoor re-scan — every original finding still closed.**

| Backdoor | Scan result |
|---|---|
| Auto-login | No route. Only hits are `security/secrets.py` (which **rejects** `ENABLE_AUTO_LOGIN` in production) and the tests asserting its absence. |
| Demo login / demo user | Zero hits outside `test_auth_hardening.py`, which asserts non-existence. |
| Mock OAuth code | Only in `test_auth_hardening.py`, asserting rejection. |
| Legacy Emergent auth | **Zero** hits in application code. Two comments in provider modules note the wrapper is *not* used. |
| Wildcard CORS | Stripped at parse — verified live (§9). |
| Insecure production cookies | Forced `Secure` in production regardless of env (§9). |
| Long-lived access tokens | Live: exactly 900 s. |
| Non-rotating refresh | Live: rotation + replay detection + family revocation. |
| Privilege escalation | 307 `test_api_authz` tests; live matrix above. |

**Credential scan across all tracked files:** the only matches are two synthetic
fixtures in `tests/test_secret_loading.py` (one carries an explicit
`# gitleaks:allow`) used to assert the weak-secret detector, plus event-name
constants (`INVALID_PASSWORD`, `PURPOSE_RESET_PASSWORD`) and a shell variable
reference. **No real credential is committed.**

`backend/.env` contains `ENABLE_AUTO_LOGIN=true` but is **untracked and
gitignored** (`.gitignore:101`), is a developer-local file, and the production
validator rejects the flag outright. Classified benign.

**Live log leakage — measured against the real secrets in use:**

| Secret | Occurrences in container logs |
|---|---|
| Mongo password | **0** |
| Redis password | **0** |
| User password | **0** |
| Anthropic API key | **0** |
| JWT signing secret | **0** |

Redis URIs are redacted at source (`redis://***@ph311-redis:6379/0`).

Two JWT-shaped strings *do* appear — both in query strings, both from **this
sprint's own negative test** that deliberately sent `?token=`. Zero appear in the
application's JSON logs. See P3-2 for the residual observation.

---

## 9. Cookies / CORS / CSRF / Headers / Rate Limiting — Live

| Control | Evidence |
|---|---|
| CSRF — no header | **403** |
| CSRF — forged header | **403** |
| CORS — disallowed origin | **no** `Access-Control-Allow-Origin` |
| CORS — allowed origin | `access-control-allow-origin: https://app.stockassist.ai` |
| Wildcard CORS in production | Stripped at parse with a warning; `"*"` → `[]`, `"https://a,*"` → `["https://a"]` |
| `COOKIE_SECURE=false` in production | **Overridden to `True`** — `cookies.py::cookie_secure()` ignores the env in production |
| Rate limiting — 8 rapid bad logins | `401 401 401 401 401 429 429 429` |
| Security headers | all 6 present: HSTS (`max-age=63072000; includeSubDomains`), `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, CSP (`default-src 'none'`) |
| `Server` header | suppressed |
| OpenAPI docs in production | **404** |

> **Method note.** A first pass drove all of these through `validate_config()`
> and reported wildcard-CORS, insecure-cookie, debug-mode and plaintext-origin
> as "not rejected". That was the wrong instrument: three of the four are
> enforced at their own layer (`cors.py` strips, `cookies.py` forces) exactly as
> the brief permits ("fails/stripped", "forced Secure"), and **debug mode has no
> code path to reject** — there is no `--reload`, no `app.debug`, no debug flag
> anywhere in `server.py`, `entrypoint.sh` or the Dockerfile. Only the fourth
> survives as a genuine gap (P3-1).

---

## 10. WebSocket Regression

PH3.10's P0 re-attacked live. The credential marker is `stockassist.auth`.

| Attempt | Result |
|---|---|
| Anonymous, no credential | **REJECTED 403** |
| **Spoofed `?user_id=<victim>` — the original exploit** | **REJECTED 403** |
| Token in query string | **REJECTED 403** |
| Forged token via subprotocol | **REJECTED 403** |
| Valid token via `Sec-WebSocket-Protocol` | **CONNECTED**, negotiated `stockassist.auth` |
| Valid token via cookie | **CONNECTED**, echoes no subprotocol |
| Valid token **+** spoofed `?user_id=<victim>` | **CONNECTED as the token's subject** — query parameter ignored |

**Isolation:** two users held concurrent sockets; each drained for 4 s.
**Zero foreign-user identifiers** appeared in either stream. Reinforced by
`test_ws_authentication`'s 17 hermetic tests, including
`test_events_for_a_victim_never_reach_an_impersonating_socket`.

**Churn / cleanup:** 40 sequential connect-disconnect cycles + 20 concurrent
sockets = **60 accepted**. After settle:

| Gauge | Before | After |
|---|---|---|
| `websocket_connections` | 0 | **0** |
| `websocket_tracked_users` | 0 | **0** |
| `websocket_channel_subscriptions` | 0 | **0** |
| `background_tasks_running` | 4 | **4** |
| `app_cache_entries{*}` (4 caches) | 0 | **0** |
| `redis_pool_connections{in_use}` | 0 | **0** |
| Container restarts | — | **0** |

Every socket accepted was tracked and released. No leak, no duplicate
subscription, no retained identity.

---

## 11. Market Data Regression

`/api/stocks/RELIANCE` on the production container returns live data with
`"source": "yahoo_finance"`, `"market_state": "CLOSED"`, and a full technical
block. An unknown symbol returns **404 `Stock not found`** — **not** a fabricated
quote. Repeat calls are consistent (cached), and `/api/market/*` paths that do
not exist return 404 rather than an empty-but-successful payload.

The gateway boundary holds: business logic never names a provider, and with
Alpha Vantage and Zerodha both `OFF` the platform degrades to the free provider
rather than inventing numbers. This is the behaviour PH3.10 recorded as
"503 not fabrication".

68 `test_api_market_data` tests cover stale data, malformed payloads, provider
failure, missing symbols and market-closed state hermetically.

---

## 12. Trading Regression — including the chartered stale test

The brief singles out `test_run_cycle_trails_and_books_targets`. **It is
genuinely fixed, and the fix was in the correct direction.** Verified three ways
rather than by reading the prior report:

1. **The assertion is exact equality, not a weakened subset:**
   `assert stats == {"checked": 1, "trailed": 1, "targets_hit": 1, "sl_exits": 0,
   "auto_orders": 0, "closed_trades": []}`. The repair added the new
   `closed_trades` key to the *expectation*; it did not relax the comparison.
2. **It is backed by consequence assertions, not just the return value** —
   trailed stop `106.4` (112 × 0.95), `best_price` 112.0, target level 1 booked,
   status still `OPEN` (alert-only, no auto-exit), both `TARGET_HIT` and
   `TRAILING_SL` events emitted, exactly 1 notification, and a second cycle at
   the same price firing nothing (dedup).
3. **Mutation-checked for non-vacuity.** Injecting a spurious key into
   `run_cycle`'s return contract in `services/trading_engine.py` made the test
   **FAIL**. The mutation was reverted and `git diff` confirmed clean; the suite
   returned to 36 passed.

**Classification: F — DOCUMENTATION/EXPECTATION MISMATCH, already resolved in
PH3.1.** Not a product defect and not a blocker.

Trading suites: `test_trading_engine` 36, `test_api_trading` 35,
`test_paper_trading` 6 — all green. Paper trading remains separated from live
execution.

---

## 13. AI Regression

`/api/ai/status` reports per-provider configuration honestly
(`claude: configured`, `gemini: not configured`). With a synthetic key the
platform degrades rather than fabricating. 48 `test_api_ai` tests plus
`test_advisor` (7) cover provider timeout, provider failure, malformed response,
retry limits, fallback, prompt construction and user-data isolation with
deterministic stubs.

**No AI output is represented as an executed trade.** Recommendations remain
structurally distinct from market data, broker execution and portfolio state —
re-confirmed against the analytics provenance contract (§14), which classifies
every surfaced number independently of the AI layer.

`test_api_ai` runs in 8.00 s with the socket guard armed and **zero** network
escapes, so the AI paths are exercised against stubs, not live providers.

---

## 14. Analytics Regression (PH3.9)

Read directly from the live registry rather than from a report:

```
{'real': 4, 'derived': 32, 'mock': 0, 'unavailable': 17}   total = 53
MOCK metrics: []
```

**Zero MOCK metrics.** The 17 that PH3.9 converted are now `UNAVAILABLE` with
operator-readable reasons, not zeros masquerading as facts. 75/75
`test_ph39_mock_removal` tests pass; `test_analytics` adds 154.

**Payments/revenue integrity** verified in source: `analytics/sources.py` defines
`CAPTURED_STATUSES = ("captured", "paid", "succeeded", "settled")` and documents
that `created` and `pending` are *intents*, `authorized` is a hold, and only a
captured payment has moved money. Refunds are tracked separately so they can
reverse revenue. **Pending, created, failed and abandoned payments cannot be
counted as revenue.**

No live payment-provider flow was exercised — no provider is provisioned, and
the `payments` collection is empty by design, which is precisely why the revenue
metrics report `UNAVAILABLE` rather than `₹0`.

---

## 15. Database Regression

| Check | Result |
|---|---|
| Collections | 19 |
| Total indexes | **61** = 42 declared by `ensure_indexes()` + 19 implicit `_id_` |
| TTL indexes | **3** |
| Unique constraints | `users.email`, `sessions.session_id`, `recovery_tokens.token_id`, `feature_flags.key`, `watchlist.user_id+symbol`, `broker_accounts.user_id+broker`, `portfolio_snapshots.user_id+date` |
| `ensure_indexes()` | idempotent, awaited first in `startup()` |
| ObjectId validation | 4xx on every malformed identifier (§7) |
| Connection lifecycle | `client.close()` at `server.py:6998` on shutdown |
| Unbounded queries | none introduced; the trade monitor's `.to_list(200)` cap is the known P2-3 |

---

## 16. Redis Regression

**Redis stopped mid-flight:**

| Observation | Result |
|---|---|
| Process | **alive**, 0 restarts |
| `/api/health` | 200 |
| `/api/health/ready` | **200**, with `redis: fail, critical: false` |
| Authenticated requests | **200** — unaffected |
| Market data | **200** — unaffected |
| Login | 401 (correct; the rate-limit store is MongoDB) |
| Recovery after restart | automatic, circuit closed, reconnected |

Exactly the controlled degradation the brief requires: Redis is a **non-critical**
dependency and its loss narrows capability without taking the API down.

**Startup transient (P3-3).** During the boot burst the client pool (24) is
briefly exhausted — six `ConnectionError: Too many connections`, the circuit
opens, and it closes ~7 ms later. Redis-side `maxclients` is 10,000 with 7
connected, so this is the *application's* pool, not the server's. Self-healing,
non-recurring, and PH3.10 observed the same circuit-breaker behaviour.

---

## 17. Background Tasks, Memory & Graceful Shutdown

`docker stop --timeout 30` → **1 s, exit code 0, `OOMKilled: false`.**

Ordered teardown, from the logs:

1. `shutdown_started` — readiness flips to draining **first**, so a balancer
   stops routing before resources close
2. AI heartbeat engine stopped (owns 2 of the 4 loops)
3. `background_tasks_cancelled count=2` (the remaining 2) — **all 4 accounted for**
4. Redis pub/sub unsubscribed from `sa:events`
5. Redis client closed
6. Pooled HTTP clients closed
7. Mongo client closed (`server.py:6998`)
8. `Application shutdown complete` → `Finished server process [1]`

After shutdown: no task, no connection, no timer left running. Combined with the
churn gauges in §10, the PH3.6 leak fixes hold.

**Not measured:** multi-day soak. Carried forward from PH3.10 as a limitation.

---

## 18. Performance Regression

`test_perf_regression` (37 tests) passes, including its assertions that the
declared index set covers the query shapes the routes actually issue. Live
observations: **startup 0.534 s**, shutdown 1 s, image 424 MB. Backend suite
187.63 s vs PH3.10's 174.21 s — host-load variance on a machine concurrently
building a Docker image, not a code regression.

**No dedicated load run was executed this sprint.** PH3.4/PH3.5 harnesses exist
(`test_load_harness`, `scripts/seed_load_fixtures.py`); re-running them against a
provisioned environment is a staging activity. Recorded as a limitation, not
claimed as verified.

---

## 19. Docker Release Candidate

Built **from scratch** — `docker build --no-cache --pull` — not from a local
development image.

| Check | Result |
|---|---|
| Build | **exit 0**, 424 MB |
| User | `appuser`, **uid 10001** (non-root) |
| `pip` in runtime image | **ABSENT** |
| `.env` baked into image | **none** |
| `--reload` | absent, and `entrypoint.sh` documents that it must never be added |
| Entrypoint | exec form → PID 1 receives SIGTERM directly |
| Boot (production) | **healthy**, `startup_complete` in **0.534 s** |
| Mongo / Redis connectivity | authenticated, both healthy |
| Clean shutdown | 1 s, exit 0 |
| Healthcheck target | `/api` — deliberately **not** `/health/ready`; the script documents why (a readiness failure must drain, not restart). Verified correct during the Mongo outage. |
| `__pycache__` in image | **present** — PH3.10 P3-1, unresolved |

---

## 20. CI/CD Release Candidate

| Gate | Command | Result |
|---|---|---|
| Correctness lint (BLOCKING) | `flake8 --select=E9,F63,F7,F82,F811,F632` | **PASS** — zero findings |
| Compile all sources | `compileall` | **PASS** |
| Import application | with CI's synthetic env | **PASS** — 205 routed endpoints + 1 WebSocket route |
| Startup config validation | fail-closed matrix | **PASS** (§21) |
| Backend tests | `pytest` | **PASS** — 2,559 |
| Frontend tests | `craco test` | **PASS** — 395 |
| Frontend build | `npm run build` | **PASS** — exit 0 |
| **Backend advisories** | `pip-audit` + CI's 15 `--ignore-vuln` | **FAIL — exit 1** |
| **Frontend advisories** | `npm audit --audit-level=high` | **FAIL — exit 1** |
| **`dependency-audit` aggregate** | requires both | **FAIL** |

The import gate also fails closed without configuration — importing `server` with
no environment raises `SecretValidationError` rather than importing a
misconfigured app. Correct, and worth noting as a control.

---

## 21. Production Configuration — Fail-Closed Matrix

`APP_ENV=production`, exercised against `security.secrets.validate_config`:

| Scenario | Expected | Actual |
|---|---|---|
| Valid production config | accept | **accept** |
| Missing `JWT_SECRET` | reject | **reject** |
| Missing `MONGO_URL` | reject | **reject** |
| Weak secret (`changeme`) | reject | **reject** — "looks like a placeholder / weak default" |
| Placeholder secret | reject | **reject** |
| Short secret | reject | **reject** — under 32 chars |
| `ENABLE_AUTO_LOGIN=true` | reject | **reject** |
| Mongo URL without credentials | reject | **reject** |
| Wildcard CORS | strip | **stripped** at parse (`cors.py`) |
| `COOKIE_SECURE=false` | force Secure | **forced True** (`cookies.py`) |
| Debug mode | reject | **no such code path exists** |
| Plain-HTTP `FRONTEND_URL` | reject/warn | **accepted silently** → P3-1 |

Missing database and missing Redis produce controlled failure — verified live in
§16 and §22. No production test was run against real production credentials.

---

## 22. Failure-Injection Regression

| Injection | Status | Message safety | Process | Recovery |
|---|---|---|---|---|
| **Redis unavailable** | readiness 200, `redis: fail, critical:false` | no leakage | alive, 0 restarts | automatic |
| **Mongo unavailable** | `/health/live` **200**, `/health/ready` **503** (`mongodb: fail, critical:true, timeout after 2s`) | body is bare `Internal Server Error` — no stack trace, no URI | alive, 0 restarts | automatic, full |
| Invalid JWT | **401** | generic | — | — |
| Malformed JWT | **401** | generic | — | — |
| Revoked refresh (replay) | **401** | generic | — | — |
| Refresh after logout-all | **401** | generic | — | — |
| Forged WebSocket credential | **403** | no body | — | — |
| Unknown market symbol | **404** | `Stock not found` | — | — |
| Missing `METRICS_TOKEN` | **403** | fails closed | — | — |

**Observation (P3-4):** during the Mongo outage, in-flight API calls surface as
**500**, not 503. Readiness correctly reports 503 so an orchestrator drains the
instance, and nothing leaks — but 503 would be the semantically correct status
for a transient dependency loss, and it is what a retrying client acts on.
Pre-existing; not a regression.

AI-provider, market-provider, payment-provider and email-provider failure paths
are covered hermetically (§13, §11, §14) rather than by live injection, since no
such provider is provisioned.

---

## 23. Deployment Smoke Test

**No staging environment is configured**, so the smoke test was run against the
locally-hosted production-mode container described throughout this report.

**This is explicitly not production verification.** Per the brief, that label is
reserved for a run in the actual production environment. What was verified is
that a from-scratch production image, given production configuration and
authenticated datastores, serves health, readiness, registration, login,
authenticated API, refresh, logout, logout-all, WebSocket, market data, AI status
and analytics, then shuts down cleanly.

---

## 24. Failure Classification

Every failure and deviation observed, classified per the brief.

| # | Observation | Class | Disposition |
|---|---|---|---|
| 1 | `dependency-audit` red — 6 backend advisories | **D — DEPENDENCY/TOOLING** | **BLOCKER B-1** |
| 2 | `dependency-audit` red — 18 npm high advisories, no triage path | **D — DEPENDENCY/TOOLING** | **BLOCKER B-1** |
| 3 | `SUPPRESSION_REVIEW_BY` expires 2026-08-22 | **D — DEPENDENCY/TOOLING** | **BLOCKER B-1** (5 days) |
| 4 | `test_run_cycle_trails_and_books_targets` | **F — EXPECTATION MISMATCH** | Already fixed in PH3.1; proven non-vacuous |
| 5 | 4 xfail (D-10 email format) | **E — INTENTIONAL** | Pinned; XPASS on fix |
| 6 | 95 deselected | **E — INTENTIONAL** | Documented live-server boundary |
| 7 | Plain-HTTP `FRONTEND_URL` accepted in production | **B — PRE-EXISTING** | P3-1 |
| 8 | JWT echoed in access log when a *caller* puts it in the query string | **B — PRE-EXISTING** | P3-2 |
| 9 | Redis pool-exhaustion transient at boot | **C — ENVIRONMENTAL** | P3-3, self-healing |
| 10 | 500 (not 503) during Mongo outage | **B — PRE-EXISTING** | P3-4 |
| 11 | `source: yahoo_finance` disclosed to the client | **B — PRE-EXISTING** | P3-5 |
| 12 | Python 3.11.15 vs 3.11.16 | **C — ENVIRONMENTAL** | Host only; image unchanged |
| 13 | 19/61 vs 20/62 collections/indexes | **C — ENVIRONMENTAL** | Lazy collection creation; declared set identical |
| 14 | `server.py` 6,998 vs 6,954 | **F — EXPECTATION MISMATCH** | PH3.10 measured mid-sprint |
| 15 | Two confounded probes of mine (logout-all, config layer) | **method error** | Corrected in-sprint; §6, §9 |

**NEW REGRESSIONS: zero.** No unexplained failure remains.

---

## 25. Release Candidate Scorecard

| Category | Status | Tests | Result | Evidence | Regression | Blocker |
|---|---|---|---|---|---|---|
| Security | **PASS** | 452 | green | PH1 backdoor re-scan 9/9 clean; live | none | — |
| Authentication | **PASS** | 34+40 | green | Live journey §6 | none | — |
| Authorization | **PASS** | 307 | green | 201-route classify; live matrix §7 | none | — |
| OAuth | **PASS (hermetic)** | 28 | green | `test_oauth_hardening` | none | live round trip unverified |
| Cookies | **PASS** | 23 | green | Live jar: HttpOnly+Secure | none | — |
| CORS | **PASS** | 30 | green | Live ACAO probe; wildcard stripped | none | — |
| CSRF | **PASS** | 18 | green | Live 403 missing + forged | none | — |
| JWT | **PASS** | 34 | green | Live 900 s, full claims | none | — |
| Sessions | **PASS** | 34 | green | Rotation, replay, family revoke, logout-all | none | — |
| Rate limiting | **PASS WITH CONDITIONS** | 26 | green | Live 401×5→429×3 | none | WS handshake unlimited (P2-2) |
| Password security | **PASS** | 40 | green | Live 422 on policy violation | none | — |
| Email verification | **BLOCKED** | — | n/a | `email_service` = simulated | none | No SMTP provider (C-1) |
| API | **PASS** | 2,559 | green | Full suite | none | — |
| WebSocket | **PASS** | 17 | green | Live re-attack §10 | none | — |
| Market data | **PASS** | 68 | green | Live provenance; 404 not fabrication | none | — |
| Trading | **PASS WITH CONDITIONS** | 77 | green | Mutation-checked §12 | none | 200-trade cap (P2-3); single process (C-6) |
| AI | **PASS** | 55 | green | Honest degradation; 0 net escapes | none | — |
| Payments | **PASS (by contract)** | 154 | green | Captured-only revenue | none | No provider provisioned |
| Analytics | **PASS** | 229 | green | Registry: 0 MOCK of 53 | none | — |
| MongoDB | **PASS** | — | green | 61 indexes = 42+19; clean close | none | `MONGO_SOCKET_TIMEOUT_MS` (C-3) |
| Redis | **PASS** | 50 | green | Live outage → degradation §16 | none | — |
| Memory | **PASS** | 28 | green | 60 sockets → 0 retained §10 | none | Soak unmeasured |
| Performance | **PASS WITH CONDITIONS** | 37 | green | 0.534 s boot | none | No load run this sprint |
| Frontend | **PASS** | 395 | green | 22 suites; build exit 0, 48 bundles | none | — |
| Docker | **PASS** | — | green | `--no-cache` build; non-root; fails closed | none | `__pycache__` (P3-1 of PH3.10) |
| **CI/CD** | **FAIL** → **PASS** | 8 gate tests | **red** → **green** | `dependency-audit` exit 1, both jobs → **exit 0** after remediation | none (pre-existing) | **B-1 — CLOSED** |
| **Secrets** | **PASS WITH CONDITIONS** | 43 | green | Scan clean; 0 leakage in live logs | none | Suppressions re-argued; register expiries now 2026-11-15 / 2027-02-15 |
| Monitoring | **PASS WITH CONDITIONS** | — | green | Probes, gauges, correlation IDs | none | No alerting (C-4) |
| Logging | **PASS** | — | green | 0 secrets in live logs | none | P3-2 |
| Backups | **BLOCKED** | 39 | green | Scripts + drill pass | none | No off-host copy (C-5) |
| Disaster recovery | **BLOCKED** | 43 | green | Runbooks; suites pass | none | Host-loss unexecutable (C-5) |
| Testing | **PASS** | 2,658 | green | Nothing skipped or weakened | none | — |
| Documentation | **PASS** | — | green | Updated this sprint | none | — |

**Totals as found: 24 PASS · 5 PASS WITH CONDITIONS · 3 BLOCKED · 1 FAIL.**
**Totals after remediation: 25 PASS · 5 PASS WITH CONDITIONS · 3 BLOCKED · 0 FAIL.**
The three BLOCKED categories (email verification, backups, disaster recovery) are
unchanged — they are unbuilt operational capabilities, not defects, and are
deployment prerequisites rather than certification blockers.

---

## 26. Release Candidate Verdict

> ## **BLOCKED — REGRESSION REMEDIATION REQUIRED**
>
> ### ✅ SUPERSEDED 2026-08-17 — blocker B-1 resolved
>
> This verdict was the state of the candidate **before** remediation. B-1 has
> since been closed and the candidate is **READY FOR PH3.12 CERTIFICATION**.
> See **`docs/production/PH3.11_REMEDIATION_REPORT.md`**.
>
> The blocker was fixed rather than suppressed: all 6 Python advisories were
> cleared by upgrading `aiohttp` 3.14.1 → 3.14.3 and `cryptography`
> 48.0.1 → 50.0.0 (both fully verified, including decryption of Fernet tokens
> written under the old version); 7 of the 18 npm advisories were cleared by
> patch-level `overrides` plus a `postcss` devDependency bump (18 high → 11);
> and 8 dead suppressions naming packages no longer in the tree were deleted.
> The remainder are triaged in `.github/dependency-triage.yml` with re-runnable
> evidence, named owners and mechanically enforced expiry dates, and the gate
> itself is covered by eight negative tests.
>
> The record below is left unedited. It is what the candidate looked like at
> the moment the blocker was found, and rewriting it would erase the finding.

**What is *not* blocking.** No code regression was found. Backend, frontend,
security, authorization, WebSocket, trading, AI, analytics, database, Redis,
memory and shutdown all reproduce or improve on the PH3.10 baseline, verified
against a from-scratch production image and a live container rather than against
prior reports. Not one line of application code needed to change.

**What is blocking — B-1: the `dependency-audit` CI gate is red.**

The brief makes running CI's own gates a stop condition and requires local and CI
results to agree. They do agree: both fail. A required, blocking workflow does
not pass on this commit, so this candidate cannot be certified as one that clears
its own pipeline.

Three parts, in priority order:

1. **6 backend advisories against pinned runtime dependencies** —
   `cryptography` 48.0.1 (PYSEC-2026-3552/3553/3554) and `aiohttp` 3.14.1
   (PYSEC-2026-3545/3546/3547), all published after the suppression list was
   written. **Reachability analysed: none confirmed exploitable here.**
   `cryptography` is used only for Fernet; the codebase has no `pkcs7`, no
   `x509.verification`, no `PolicyBuilder`. `aiohttp` is client-only, which rules
   out the server-side smuggling advisory. Two client-side `aiohttp` advisories
   remain theoretically reachable via a malformed upstream response, and their
   fix is a **patch-level bump within the existing pin** (3.14.1 → 3.14.3) — the
   same class of "safe in-pin security patch" `SECRETS.md` §8 records applying
   before. `cryptography` 48 → 49/50 is a **major** bump and must not be done
   inside a freeze.

2. **18 high-severity npm advisories with no triage path.** All in the CRA build
   chain — **verified not shipped**: zero references to `webpack-dev-server`,
   `sockjs`, `postcss` or `nth-check` in `build/static/js/` (the `svgo` hits are
   minifier-generated variable names, not the library). `npm audit --omit=dev`
   does not filter them only because `react-scripts` sits in `dependencies`,
   which is CRA's own layout. The Python gate has a documented allowlist with a
   mechanical expiry; **the npm gate was never given one**, so it fails
   unconditionally with nothing an engineer can act on.

3. **The suppression register expires 2026-08-22 — five days out.** From that
   date the backend job warns on every run and fails 30 days later.

**Why this is deliberately not fixed in this sprint.** PH3.11's core rule permits
only confirmed regressions, release-blocking defects, deterministic test fixes,
and documentation corrections. Bumping `cryptography` across a major version, or
migrating off `react-scripts`, is architectural work during a freeze — precisely
what the rule forbids. Suppressing the advisories to turn the gate green would be
worse: it is the "mark a failure as passed" outcome the brief prohibits, and it
would retire an accepted-risk register five days before it is due to be
re-argued.

**Recommended remediation, for approval before PH3.12:**

| # | Action | Risk |
|---|---|---|
| R-1 | Bump `aiohttp` 3.14.1 → 3.14.3 (patch-level, in-pin) and re-run the full suite | Low — precedent exists |
| R-2 | Evaluate `cryptography` 48 → 49/50 in a dedicated sprint with regression coverage; the reachable surface is Fernet only | Medium — major bump |
| R-3 | Give the npm gate the same triage mechanism the Python gate has (documented allowlist + expiry), or schedule the CRA migration | Low — tooling |
| R-4 | Re-argue or extend `SUPPRESSION_REVIEW_BY` before 2026-08-22 | Low — decision |

Once R-1, R-3 and R-4 land and `dependency-audit` is green, **every other stop
condition in this brief is already met** and the candidate is ready for PH3.12.

**Carried conditions from PH3.10, all still open:** C-1 SMTP, C-2 dedicated
secrets, C-3 `MONGO_SOCKET_TIMEOUT_MS`, C-4 alerting, C-5 off-host backup,
C-6 single backend process, C-7 same-origin deployment, C-8 one lockfile.

---

## 27. Findings Register

| ID | Finding | Severity | Class | Disposition |
|---|---|---|---|---|
| **B-1** | `dependency-audit` CI gate red on both jobs | **Blocker** | D | §26 |
| P3-1 | Plain-HTTP `FRONTEND_URL` accepted in production. Mitigated in practice: cookies are forced `Secure`, so a plaintext origin breaks the session rather than downgrading it silently | P3 | B | Documented |
| P3-2 | uvicorn's access log records caller-supplied query strings verbatim, so a client that sends `?token=` writes a live 15-min credential to logs. The platform's own client never does — PH3.10 moved it to the subprotocol | P3 | B | Documented |
| P3-3 | Redis client-pool exhaustion transient during the boot burst; circuit opens and closes in ~7 ms | P3 | C | Documented |
| P3-4 | Mongo outage surfaces as 500 rather than 503 on in-flight calls | P3 | B | Documented |
| P3-5 | `source: yahoo_finance` disclosed in quote payloads. `MARKET_DATA_ARCHITECTURE.md` forbids provider names in *error* surfaces (§611) and is silent on success payloads | P3 | B | Documented |

Carried unchanged from PH3.10: P2-1 (public LLM endpoints), P2-2 (WS handshake
rate limit), P2-3 (200-trade cap), P2-4 (npm build chain — now **B-1**), P2-5
(ESLint warnings), P2-6 (D-10), P2-7 (two lockfiles), P3-1 (`__pycache__`),
P3-2 (`on_event` deprecation).

---

## 28. Known Environmental Limitations

Stated rather than assumed:

* No staging environment — the smoke test ran locally in production mode (§23)
* No SMTP provider — email verification and password-reset delivery unexercised
* No OAuth credentials — live Google round trip unexercised
* No payment provider — live payment callbacks unexercised
* No off-host backup target — host-loss DR unexecutable
* No multi-day soak, no load run this sprint
* No browser device matrix
* Single-process topology only (C-6)

---

## 29. Evidence Commands

```bash
# Backend suite
cd backend && venv/bin/python -m pytest -q
#   → 2559 passed, 95 deselected, 4 xfailed in 187.63s

cd backend && venv/bin/python -m pytest -m security -q
#   → 452 passed, 2206 deselected in 35.74s

# Frontend
cd frontend && CI=true npx craco test --watchAll=false     # 395 passed, 22 suites
cd frontend && REACT_APP_BACKEND_URL="https://ci.invalid" npm run build   # exit 0, 48 bundles

# Route surface (identical to PH3.10)
cd backend && python -c "from tests._routes import *; ..."
#   → protected=97 admin=29 public=75 total=201

# Analytics provenance
cd backend && venv/bin/python -c "from analytics import registry; print(registry.summary())"
#   → {'real': 4, 'derived': 32, 'mock': 0, 'unavailable': 17}

# Trading test non-vacuity (mutation reverted; git diff clean afterwards)
#   inject "MUTANT" into run_cycle's return dict → test FAILS ✓

# RC image, from scratch
docker build --no-cache --pull -f backend/Dockerfile -t stockassist-rc:ph311 backend
#   → 424 MB, USER appuser (uid 10001), pip ABSENT, no .env baked

# Live production container
docker run -d --name ph311-backend -e APP_ENV=production ... stockassist-rc:ph311
#   → healthy, startup_complete in 0.534s

# Live security smoke
curl -sI $API                                   # 6 security headers, no Server:
curl -sI -H "Origin: https://evil.example.com"  # no ACAO
for i in $(seq 8); do ... bad login; done       # 401×5 then 429×3
curl -b jar -X POST $API/auth/logout-all        # 403 without CSRF header

# WebSocket re-attack
#   anonymous / ?user_id=<victim> / ?token= / forged  → 403 REJECTED
#   Sec-WebSocket-Protocol: stockassist.auth,<token>  → CONNECTED
#   valid token + spoofed ?user_id                    → CONNECTED as token subject

# Fault injection
docker stop ph311-redis    # ready 200, redis fail/non-critical, API serving, 0 restarts
docker stop ph311-mongo    # live 200, ready 503 critical, 500s, no leakage, 0 restarts
docker stop --timeout 30 ph311-backend   # 1s, exit 0, all 4 tasks stopped

# CI gates — the blocker
cd frontend && npm audit --audit-level=high ; echo $?      # → 1  (18 high)
cd backend  && pip-audit <15 CI ignores> -r requirements.txt ; echo $?
#   → 1 — cryptography×3, aiohttp×3
```

---

## 30. What PH3.12 Should Carry Forward

PH3.10 asked PH3.11 to **verify across boundaries rather than aggregate per-phase
verdicts**. Doing that produced this sprint's only blocker, and the shape is
familiar: *the supply-chain gate was inside no sprint's scope.* PH1 audited
security, PH2 infrastructure, PH3.10 added the missing frontend CI job — and
nobody ran `dependency-audit` and looked at its exit code. It is the same lesson
PH3.10 drew from a frontend that had not built for fourteen days: **a gate nobody
executes reports nothing, and a gate nobody watches is indistinguishable from a
gate that passes.**

Two method notes worth keeping, both from mistakes made and corrected here:

* **Two of this sprint's apparent findings were artifacts of my own probes** — a
  logout-all test confounded by an unsaved rotated cookie, and a fail-closed
  matrix aimed at the wrong layer. Both looked like security defects. Re-running
  before reporting is what separated them from the real one.
* **Reachability analysis changed the severity of the blocker but not its
  status.** None of the six backend advisories is exploitable in this
  deployment, and none of the eighteen npm ones ships to a browser. That is worth
  knowing, and it is still not the same as a green gate. PH3.12 should certify
  capabilities that were *observed working*, and should treat "no known
  exploit path" and "the pipeline passes" as two separate claims.

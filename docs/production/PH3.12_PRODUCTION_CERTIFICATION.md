# PH3.12 Production Certification

**Sprint:** PH3.12 — Production Certification & Final Release Decision
**Date:** 2026-08-17
**Engineer:** Principal Release Engineer
**Preceding gate:** `docs/production/PH3.11_REMEDIATION_REPORT.md` — **READY FOR PH3.12 CERTIFICATION**

---

## 1. Certification Scope

This is the final production certification of the frozen release candidate. **No
application code was changed.** The working tree is byte-identical before and
after: the tracked diff hashes to
`b2f4921d913c7bf5c4c34e6e5590c72c989caae8be26fac99f277bb249b32725` at both the
start and the end of this sprint.

The brief required verifying PH3.11's evidence directly rather than trusting the
report. That instruction is the reason this certification reaches a different
verdict than the one it was expected to confirm: **two findings were discovered
that no previous sprint recorded, one of which PH3.11 explicitly certified as
closed when it is not.**

Certification separates three claims throughout, and never conflates them:

1. **CODE READY** — the artifact behaves correctly.
2. **PRODUCTION INFRASTRUCTURE READY** — the environment exists and is configured.
3. **PRODUCTION OPERATIONALLY VERIFIED** — it has been observed working in production.

Nothing in this document claims #3. No production environment exists.

---

## 2. Certified Release Candidate

| Measure | Value |
|---|---|
| Commit | `32437e858970505db70201ddc0174afd85bd19be` |
| Branch | `main` |
| Working tree | **`32437e8` + the PH3.11 remediation working tree** (uncommitted) |
| Tracked diff SHA-256 | `b2f4921d913c7bf5c4c34e6e5590c72c989caae8be26fac99f277bb249b32725` |
| Untracked RC files | `.github/dependency-triage.yml`, `.github/scripts/dependency_audit.py`, `docs/production/PH3.11_*.md`, `docs/qa/RELEASE_TEST_PROTOCOL.md` |
| Backend version | `0.0.0-dev` (no release tag applied) |
| Frontend version | `0.1.0` |
| Python (host) | 3.11.15 · **image** 3.11-slim (Debian bookworm) |
| Node / npm | v23.11.0 / 10.9.2 |
| Docker | 29.4.0 |
| MongoDB / Redis | `mongo:7.0` / `redis:7.2-alpine` |
| Backend pins | `aiohttp==3.14.3`, `cryptography==50.0.0`, `fastapi==0.110.1`, `starlette==0.37.2` |
| Frontend lock state | **`package-lock.json` *and* `yarn.lock` both tracked** — C-8, unresolved |
| RC image (fresh) | `stockassist-rc:ph312`, **425 MB**, `sha256:f373296b87cdc…638b142`, built `--no-cache --pull` |

> **The release candidate is an uncommitted working tree, not a commit.** The
> PH3.11 remediation is unstaged. Certifying a tree that no commit identifies is
> a real operational risk: it cannot be checked out, tagged, or reproduced by
> anyone else. This is recorded as **L-1** and must be resolved before deploy.

---

## 3. Executive Summary

**The release candidate reproduced every headline baseline exactly.** 2,559
backend tests, 452 security tests, 395 frontend tests, 48 production bundles,
201 routes classifying to 97 protected / 29 admin / 75 public, 0 MOCK analytics
metrics of 53, 19 collections / 61 indexes / 3 TTL. The `dependency-audit` gate
that blocked PH3.11 is **green and independently proven to bite** — seven
distinct negative tests, all reproduced from scratch here.

**The live security posture is strong and was verified against a from-scratch
production image, not against prior reports.** The WebSocket P0 attack matrix is
fully closed. Refresh tokens rotate, replays are rejected, and a replay revokes
the whole family. Logout-all revoked all four sessions including the caller's.
CSRF rejects both missing and forged tokens. Rate limiting fires at the
documented threshold. Zero occurrences of any of seven configured secrets in 442
lines of live container logs. Redis loss degrades without an outage; Mongo loss
keeps liveness at 200 and flips readiness to 503; both recover automatically
with zero restarts. Shutdown is clean in 2 seconds with all four background
tasks accounted for.

**Two findings were discovered that no previous sprint recorded.**

* **B-1 — the paper-trading endpoint has no input validation at all.** A
  negative `quantity` produces a negative cost that is *credited* to the paper
  balance. One request raised a user's balance from ₹86,840 to ₹1,086,840.
  Negative prices and arbitrary trade types are also accepted. The canonical
  `TradeCreate` model enforces every one of these constraints; the paper model,
  declared inline in `server.py` rather than beside it in `models.py`, enforces
  none.

* **B-2 — the interactive API documentation is publicly exposed in production.**
  `/docs`, `/redoc` and `/openapi.json` all return **200** to an anonymous
  caller under `APP_ENV=production`, disclosing 188 documented paths including
  23 admin routes and 26 schemas. **PH3.11 §9 recorded this as 404.** That
  evidence was wrong — almost certainly probed at `/api/docs`, which *is* 404,
  rather than at `/docs`, where FastAPI actually mounts them.

**Neither finding is a security compromise, and this report is careful about
that distinction.** No authentication is bypassed, no authorization boundary
fails, no secret is disclosed, no real money or broker path is touched, and no
user can reach another user's data — all re-verified live after both findings
were found. B-1 is confined to the acting user's own paper data. B-2 is
reconnaissance value only: every one of the 188 disclosed routes still enforces
its credential check, confirmed by probing them anonymously.

**Verdict: NO-GO — see §30.** Both blockers are bounded, understood, and fixable
in a single file each. Neither was fixed here, because the brief forbids
silently repairing a newly-found blocker during certification.

---

## 4. Security Certification

**PH1 critical/high findings — all re-scanned, all still closed.**

| Original finding | Evidence |
|---|---|
| Authentication backdoors | No auto-login route. `ENABLE_AUTO_LOGIN` appears only as a production **rejection** (`security/secrets.py:1317-1318`) and in tests asserting its absence. |
| Demo login / demo user | Zero hits outside `test_auth_hardening.py`, which asserts non-existence. |
| Mock OAuth | Only in tests asserting rejection. |
| Legacy Emergent auth | Zero in application code; two comments noting the wrapper is *not* used. |
| OAuth CSRF / state | `test_oauth_hardening` — 28 tests: state single-use, redirect binding, `aud`/`iss`, `email_verified`. |
| Insecure cookies | `cookies.py::cookie_secure()` returns `True` in production **regardless of env**. Live: all three cookies `Secure`. |
| Wildcard CORS | Stripped at parse with a warning (`cors.py:124-130`). Live: disallowed origin gets **no** ACAO. |
| JWT lifetime | Live: **exactly 900 s**. |
| JWT issuer/audience | Live claims include `iss: stockassist-ai`, `aud: stockassist-ai-app`; `jwt.py:223-224` validates both on decode. |
| Refresh rotation | Live: token value changed on refresh. |
| Refresh replay | Live: replay → **401**; rotated token afterwards → **401** (family revoked). |
| Session revocation | Live: logout-all revoked **4** sessions including the caller's. |
| Password policy | Live: password containing the user's name → **422**. |
| Password-change invalidation | `server.py:1577` `revoke_all_for_user(reason="password_changed")` + `password_changed_at` staleness. |
| Rate limiting | Live: `401 401 401 401 401 429 429 429`. |
| Authorization / role escalation | 307 `test_api_authz` tests; `test_roles` asserts admin cannot escalate a user **and** cannot self-promote, with stored-state assertions. |
| Malformed ObjectId | 4xx on every malformed identifier; no 5xx. |
| Dependency/supply-chain gate | §21 — green, and proven to fail on expiry, untriaged and stale entries. |

**No production secrets are committed.** No tracked `.env`. A credential sweep
across all tracked files returns only CI throwaways (`ci:ci`), generated tokens
(`{token()}`), shell interpolations (`${MONGO_PASSWORD}`) and documentation
placeholders. `backend/.env.example` is in sync with the secret registry.

**Status: PASS.**

---

## 5. Authentication Certification

End-to-end against the live from-scratch production container.

| Flow | Result |
|---|---|
| Register | **200** |
| Register — password containing the user's name | **422** |
| Login | **200**, identity + token, cookies set |
| Authenticated (cookie jar) | **200** |
| Authenticated (Bearer) | **200** |
| Unauthenticated | **401** |
| Refresh | **200**, refresh token **rotated** |
| Replay of consumed refresh token | **401** |
| Rotated token after replay | **401** — family revoked |
| Logout-all across 3 sessions | `sessions_revoked: 4`; **all three** refreshes → **401**, caller included |
| CSRF missing / forged | **403 / 403** |
| Rate limit (8 bad logins) | `401×5 → 429×3` |

**Cookie posture (live):** `access_token` HttpOnly+Secure, `refresh_token`
HttpOnly+Secure, `csrf_token` Secure and readable by design (double-submit). All
`SameSite=lax`.

**Token posture (live):** lifetime exactly 900 s; claims
`aud, email, exp, iat, iss, jti, sid, sub, type, ver`.

> **A method correction, recorded because it nearly became a false finding.** A
> first multi-session logout-all probe showed all three sessions unauthenticated
> *before* revocation. That was my own rate limiter tripping from eight
> deliberate bad logins earlier in the run, not a defect — the logins returned
> 429 and never set cookies, and I had not captured their status codes. Re-run
> with a fresh identity and explicit status capture, all three sessions
> authenticated 200 and revocation was total. PH3.11 documented the identical
> class of error on the identical test; re-running a suspicious result before
> reporting it is what separates the two findings in this report from noise.

**OAuth** was verified hermetically only (28 tests). No Google credentials are
provisioned, so the live round trip is **not operationally verified**.

**Email verification** cannot be exercised — `email_service` is simulated (C-1).

**Status: PASS** (OAuth and email delivery carried as environmental limitations).

---

## 6. Authorization Certification

| Endpoint | anon | user | admin |
|---|---|---|---|
| `/api/admin/users` | **401** | 403 | 200 |
| `/api/admin/dashboard` | **401** | — | — |
| `/api/admin/payments` | **401** | — | — |
| `/api/portfolio`, `/trades`, `/notifications`, `/watchlist`, `/auth/me` | **401** | **200** (own data) | — |
| `/api/metrics` | **401** | — | 401 without token; fails closed |

Route classification by dependency graph — a route is protected iff
`get_current_user` appears in its dependency tree, admin iff `require_admin`
does: **97 protected / 29 admin / 75 public = 201**, identical to PH3.10 and
PH3.11.

**No privilege-escalation path exists.** `test_roles` proves an admin cannot
escalate a user to admin and cannot self-promote to super-admin, asserting the
*stored role is unchanged* rather than only the status code.

**User isolation holds under the B-1 defect.** After a user corrupted their own
paper balance to ₹1,086,840, a second user's balance was still exactly
₹100,000.00. The defect cannot cross a user boundary.

**Status: PASS.**

---

## 7. API Certification

2,559 hermetic backend tests pass. Malformed identifiers return 4xx, never 5xx.
Unknown market symbols return 404 rather than a fabricated quote. Error bodies
carry no stack trace or connection string, verified during a live Mongo outage.

**One control is absent: production API documentation exposure (B-2).**

| Path | Expected (per PH3.11 §9) | Actual |
|---|---|---|
| `/docs` | 404 | **200** — Swagger UI |
| `/redoc` | 404 | **200** |
| `/openapi.json` | 404 | **200** — 121 KB, 188 paths, 23 admin routes, 26 schemas |
| `/api/docs` | — | 404 |

Root cause, in one line: `server.py:357` is
`app = FastAPI(title="AlphaPartner API")` — no `docs_url=None`, no
`redoc_url=None`, no `openapi_url=None`, and no environment-conditional gating
anywhere in application code. The only `docs_url` references in the tree are
inside the vendored library.

**What it does and does not mean.** No secret values appear in the schema
(checked). No route loses its credential check — all 23 disclosed admin paths
return 401 anonymously. The exposure is attack-surface mapping, not access. It
nevertheless contradicts a posture the same codebase enforces elsewhere
(suppressed `Server` header, `default-src 'none'` CSP, 404 on unknown paths),
and it was certified closed on evidence that was never true.

**Status: BLOCKED** — see B-2.

---

## 8. WebSocket Certification

PH3.10's P0 re-attacked live against the fresh image.

| Attempt | Result |
|---|---|
| Anonymous, no credential | **REJECTED 403** |
| **Spoofed `?user_id=<victim>` — the original exploit** | **REJECTED 403** |
| Token in query string | **REJECTED 403** |
| Forged token via subprotocol | **REJECTED 403** |
| Valid token via `Sec-WebSocket-Protocol` | **CONNECTED**, negotiated `stockassist.auth` |
| Valid token **+** spoofed `?user_id=<victim>` | **CONNECTED as the token's subject** |

Structurally confirmed in code: `authenticate_websocket` (`server.py:3393`)
derives identity **only** from a signed token, ignores query parameters
entirely, and rejects **before** `accept()` so an anonymous caller never
occupies a connection slot. It mirrors `get_current_user` exactly —
`expected_type="access"`, `password_changed_at` invalidation, and account-state
check — so a socket cannot outlive a password reset or an account block.

**Churn / leak:** 40 sequential connect-disconnect cycles + 20 concurrent
sockets = **60 accepted, 60 released**.

| Gauge | After churn |
|---|---|
| `websocket_connections` | **0** |
| `websocket_channel_subscriptions` | **0** |
| `background_tasks_running` | **4** (stable) |
| `redis_pool_connections{in_use}` | **0** |
| `app_cache_entries{*}` (4 caches) | **0** |
| Container restarts | **0** |

Counters corroborate the attack matrix exactly: `rejected 4`, `accepted 62`
(60 churn + 2 matrix).

**Status: PASS.**

---

## 9. Market Data Certification

`/api/stocks/RELIANCE` on the production container returns live data —
`price 1316.0`, `market_state "CLOSED"`, `source "yahoo_finance"`, with a full
technical block (RSI, VWAP, MACD). An unknown symbol returns **404**, not a
fabricated quote. The gateway boundary holds: business logic never names a
provider, and with paid providers off the platform degrades to the free source
rather than inventing numbers.

68 `test_api_market_data` tests cover stale data, malformed payloads, provider
failure, missing symbols and market-closed state hermetically.

**Carried observation (P3-5):** `source: yahoo_finance` is disclosed in success
payloads. `MARKET_DATA_ARCHITECTURE.md` forbids provider names in *error*
surfaces and is silent on success payloads.

**Status: PASS.**

---

## 10. Trading Certification

The full paper pipeline works: order intent → validation → execution simulation
→ position → portfolio → P&L. A valid BUY of 10 RELIANCE at ₹1,316 produced
`total_cost 13160.0`, `status OPEN`, `is_paper true`, and debited the balance
100,000 → 86,840 exactly.

**AI suggestions cannot silently become executed trades — verified structurally.**
Every AI module (`ai_activity`, `ai_context_builder`, `ai_debate_engine`,
`ai_memory`, `ai_provider`, `claude_provider`, `gemini_provider`) contains **zero**
references to `broker_engine`, `place_order` or `auto_exit`. Live broker exits
additionally require per-trade opt-in: `auto_exit: bool = False` on
`TradeCreate`, and `trading_engine.py:392` requires `auto_exit` **and** a linked
broker before placing anything.

**B-1 — `PaperTradeCreate` performs no input validation.**

`server.py:5125`:

```python
class PaperTradeCreate(BaseModel):
    symbol: str
    stock_name: str = ""
    quantity: int          # ← no bound
    entry_price: float     # ← no bound
    type: str = "BUY"      # ← no pattern
    stop_loss: float       # ← no bound
    target1: float         # ← no bound
```

`models.py:124` — the canonical model, for the same domain:

```python
class TradeCreate(BaseModel):
    type: str = Field(default="BUY", pattern="^(BUY|SELL)$")
    entry_price: float = Field(gt=0)
    quantity: int = Field(gt=0, le=100000)
    stop_loss: float = Field(gt=0)
    target1: float = Field(gt=0)
```

Observed consequences, all live:

| Input | `/api/trades` (real) | `/api/paper/trade` |
|---|---|---|
| `quantity: -1000` | **422** "Input should be greater than 0" | **200** — `total_cost -1000000`, **credited** |
| `quantity: 0` | 422 | **200** |
| `entry_price: -100` | 422 | **200** |
| `type: "NONSENSE"` | 422 (pattern) | **200** |

A single request moved a paper balance from ₹86,840 to **₹1,086,840**. The
inflation is unbounded and repeatable.

**Scope, stated precisely:** paper trading only; the acting user's own data only
(a second user's balance was untouched); no real money; no broker order; no
authorization boundary crossed. What it does corrupt is paper P&L, the trade
journal and per-user performance analytics — the numbers PH3.9 spent a sprint
making truthful.

**Status: BLOCKED** — see B-1.

---

## 11. AI Certification

`/api/ai/status` reports per-provider configuration honestly. 48 `test_api_ai`
tests plus `test_advisor` (7) cover provider timeout, provider failure, malformed
response, retry limits, fallback, prompt construction and user-data isolation
against deterministic stubs, with the socket guard armed and **zero** network
escapes. AI output is structurally distinct from market data, broker execution
and portfolio state (§10).

No live AI provider was exercised — the certification key was synthetic.

**Status: PASS** (live provider behaviour not operationally verified).

---

## 12. Payment Certification

No payment provider is provisioned, so the live contract could not be exercised.
What was verified is the integrity rule in source: `analytics/sources.py` defines
`CAPTURED_STATUSES = ("captured", "paid", "succeeded", "settled")` and documents
that `created`/`pending` are intents and `authorized` is a hold. **Pending,
created, failed and abandoned payments cannot be counted as revenue.** Refunds
are tracked separately so they can reverse it.

The `payments` collection is empty by design, which is exactly why revenue
metrics report `UNAVAILABLE` rather than `₹0`.

**External prerequisite, stated rather than waived:** live order creation,
signature validation, duplicate-callback handling, failed/pending payment
handling and entitlement creation require real provider credentials and a
registered webhook endpoint. None is provisioned.

**Status: NOT OPERATIONALLY VERIFIED.**

---

## 13. Analytics Certification

Read from the live registry, not from a report:

```
{'real': 4, 'derived': 32, 'mock': 0, 'unavailable': 17}   total = 53
```

**Zero MOCK metrics.** Every surfaced number is REAL, DERIVED or UNAVAILABLE;
the 17 PH3.9 converted carry operator-readable reasons rather than zeros
masquerading as facts. 75/75 `test_ph39_mock_removal` and 154 `test_analytics`
pass.

**Caveat introduced by B-1:** per-user paper P&L feeds DERIVED metrics, and B-1
lets a user inject arbitrary values into that input. The provenance *labelling*
remains correct; the underlying datum is corruptible.

**Status: PASS** (provenance), with the B-1 caveat recorded.

---

## 14. Database Certification

| Check | Result |
|---|---|
| Collections | **19** |
| Total indexes | **61** = 42 declared + 19 implicit `_id_` |
| TTL indexes | **3** |
| `ensure_indexes()` | idempotent, awaited first in `startup()` |
| ObjectId validation | 4xx on every malformed identifier |
| Connection lifecycle | `client.close()` on shutdown, observed in teardown logs |
| Outage behaviour | liveness 200, readiness 503, 0 restarts, full automatic recovery |

Identical to the PH3.11 baseline on every axis.

**Status: PASS** (`MONGO_SOCKET_TIMEOUT_MS`, C-3, remains open).

---

## 15. Redis Certification

Redis stopped mid-flight:

| Observation | Result |
|---|---|
| Process | **alive**, 0 restarts |
| `/api/health` | 200 |
| `/api/health/ready` | **200** with `redis: fail, critical: false` |
| Market data | **200** — unaffected |
| Recovery after restart | automatic; `redis: pass`, circuit closed |

Exactly the controlled degradation required: Redis is non-critical and its loss
narrows capability without taking the API down.

**Startup transient (P3-3) reproduced:** during the boot burst the client pool
briefly exhausts (`ConnectionError: Too many connections`), the circuit opens and
closes shortly after. Self-healing; observed in PH3.10 and PH3.11 too.

**Status: PASS.**

---

## 16. Memory & Resource Certification

PH3.6 fixes hold. 60 WebSocket connections accepted and fully released, with
connection, subscription and cache gauges all returning to **0**; background
tasks stable at 4; Redis pool `in_use` back to 0; zero restarts; `OOMKilled:
false`. Shutdown cancels all four background tasks and closes every pool
(§17).

**Not measured:** multi-day soak. Carried forward.

**Status: PASS** (soak unmeasured).

---

## 17. Performance Certification

| Measure | Value |
|---|---|
| Startup (production image) | **0.242 s** (`startup_complete`) |
| Graceful shutdown | **2 s**, exit 0, `OOMKilled: false` |
| Image size | 425 MB |
| Backend suite | 2,559 passed in 177.30 s |
| Security suite | 452 passed in 31.77 s |
| Frontend suite | 395 passed in 9.44 s |

Ordered teardown from the logs: readiness flips to draining **first**, then the
AI heartbeat engine (2 loops), then `background_tasks_cancelled count=2` (the
other 2 — all four accounted for), Redis pub/sub unsubscribed, Redis client
closed, pooled HTTP clients closed, Mongo client closed, `Application shutdown
complete` → `Finished server process [1]`.

`test_perf_regression` (37 tests) passes, including assertions that the declared
index set covers the query shapes the routes issue.

**No load run was executed this sprint.** PH3.4/PH3.5 harnesses exist;
re-running them against a provisioned environment is a staging activity.

**Status: PASS WITH CONDITIONS** (no load run; no soak).

---

## 18. Frontend Certification

| Check | Result |
|---|---|
| Test suite | **395 passed / 22 suites**, exit 0 |
| Production build | **exit 0** with `REACT_APP_BACKEND_URL="https://ci.invalid"` |
| Bundles | **48 JS bundles**, 14 MB — identical to PH3.10/PH3.11 |
| Secret leakage in bundle | **0** — no API-key, JWT or Mongo-URI pattern in `build/` |

**Status: PASS.**

---

## 19. Docker Certification

Built **from scratch** — `docker build --no-cache --pull` — and reproduced the
PH3.11 remediation image byte-for-byte in size and content.

| Check | Result |
|---|---|
| Build | **exit 0**, 425 MB, `sha256:f373296b87cdc…638b142` |
| Installed pins in image | `aiohttp 3.14.3`, `cryptography 50.0.0`, `fastapi 0.110.1`, `starlette 0.37.2` |
| User | `appuser`, **uid 10001** (non-root) |
| `pip` in runtime image | **ABSENT** |
| `.env` baked into image | **none** |
| `--reload` | **absent** — appears only in a comment forbidding it; the real invocation is `exec python -m uvicorn` with `--workers`, `--proxy-headers`, `--timeout-graceful-shutdown` |
| Entrypoint | exec form → PID 1 receives SIGTERM directly |
| Boot (production) | **healthy**, `startup_complete` in **0.242 s** |
| Mongo / Redis connectivity | authenticated, both healthy |
| Clean shutdown | 2 s, exit 0 |
| Config validation | **fails closed** — verified twice (§20) |
| `__pycache__` in image | **present** — deliberate (`compileall` at build), carried as P3-1 |

**Status: PASS.**

---

## 20. CI/CD Certification

Six workflows: `backend-ci`, `frontend-ci`, `dependency-audit`, `docker-build`,
`codeql`, `security-audit`. Coverage is complete against the brief: backend
tests, frontend tests, security tests, dependency audit, secret scanning, build,
Docker build and deployment validation are all present.

**Failure propagation is correct — and verified, not assumed.** Five of six
workflows terminate in an aggregate gate job that is `needs:`-dependent and
`if: always()`, then explicitly re-checks each dependency's `result` and
`exit 1`s unless it is `success`. That pattern is load-bearing: without
`if: always()` the aggregate would be *skipped* on failure, and a skipped
required check reports to branch protection as neutral rather than failing.

| Workflow | Aggregate gate | Fails on dependency failure |
|---|---|---|
| `backend-ci` | ✓ | ✓ |
| `frontend-ci` | ✓ | ✓ |
| `dependency-audit` | ✓ | ✓ |
| `docker-build` | ✓ | ✓ |
| `codeql` | ✓ | ✓ *when available* |
| `security-audit` | **none** | n/a — both jobs must be required individually |

Two honest qualifications:

* **`codeql` reports success when CodeQL is unavailable** on the repository. That
  is a deliberate, documented choice so branch protection can require the check
  today. It also means CodeQL is **not currently an enforcing gate**.
* **`security-audit` has no aggregate gate**, unlike the other five. Branch
  protection must therefore require `secret-scan` and `config-sync` by name; a
  reviewer copying the aggregate-name pattern from the other workflows would
  silently leave secret scanning unenforced.

Local gate reproduction:

| Gate | Result |
|---|---|
| Backend tests | **PASS** — 2,559 |
| Security tests | **PASS** — 452 |
| Frontend tests | **PASS** — 395 |
| Frontend build | **PASS** — 48 bundles |
| Tracked `.env` guard | **PASS** — none tracked |
| `.env.example` drift | **PASS** — in sync with the registry |
| `dependency-audit` | **PASS** — exit 0 |

**No CI run was executed on GitHub for this candidate**; all gates were
reproduced locally. Recorded as a limitation.

**Status: PASS WITH CONDITIONS.**

---

## 21. Dependency / Supply-Chain Certification

The blocker that stopped PH3.11 is closed, and the fix was independently
re-verified here rather than accepted from the report.

| Ecosystem | Advisories reported | Disposition |
|---|---|---|
| Python | **7** | all `starlette 0.37.2`, all triaged with re-runnable evidence |
| npm | **16** | all Create React App build chain, all triaged |
| **Aggregate gate** | — | **exit 0** |

Python pins confirmed in the tree *and* inside the built image: `aiohttp
3.14.3`, `cryptography 50.0.0` — the upgrades that cleared all six original
advisories.

**Every accepted advisory carries the required fields** — `id`, `ecosystem`,
`package`, `affected`, `severity`, `classification`, `reason`, `reachability`,
`evidence`, `mitigation`, `fixed_in`, `blocked_by`, `owner`, `expires`. The
register rejects a `not-reachable` entry that lacks re-runnable `evidence`, so
the stronger claim cannot be made without proof.

**The gate was proven to bite — seven independent negative tests, run here:**

| # | Mutation | Expected | Observed |
|---|---|---|---|
| 1 | Run past first expiry (`--today 2026-11-16`) | fail | **exit 1**, 2 × `EXPIRED` |
| 2 | Run **on** the expiry date (`2026-11-15`) | pass | **exit 0** |
| 3 | Run inside the 30-day warn window | pass + warn | **exit 0**, 2 × `EXPIRING` |
| 4 | Run past all expiries (`2027-03-01`) | fail | **exit 1**, **23** × `EXPIRED` |
| 5 | Delete a register entry | fail | **exit 1**, `UNTRIAGED starlette PYSEC-2026-2281` |
| 6 | Add an entry matching nothing | fail | **exit 1**, `STALE package-that-does-not-exist` |
| 7 | Auditor unavailable (`pip-audit` absent) | **non-success** | **exit 2** — distinct from both pass and policy failure |

Test 7 was observed accidentally before it was designed, which makes it better
evidence: the gate refused to report success when it could not perform the
check. **The register was restored byte-identical after every mutation**
(SHA-256 `6868a4e2…dcaa4cc` before and after).

Two real calendar commitments now exist: **2026-11-15** (npm build chain +
starlette PYSEC-2026-161/248) and **2027-02-15** (structurally unreachable
starlette). No grace period.

**Status: PASS.**

---

## 22. Secrets Certification

| Check | Result |
|---|---|
| Tracked `.env` files | **none** |
| Credential sweep over tracked files | **0 real credentials** — only CI throwaways, generated tokens, shell interpolations, doc placeholders |
| Secrets in frontend bundle | **0** |
| Secrets in image | **0** — no `.env` baked |
| Secrets in live logs | **0** across all 7 configured secrets + the user password, over 442 log lines |
| `.env.example` drift | in sync |
| Redis URI in logs | redacted at source — `redis://***@ph312-redis:6379/0` |
| `MONGO_URL` in logs | never logged — read once at `server.py:240` |

Redaction was tested empirically, not read: structured fields `password`,
`api_key`, `jwt_secret`, `access_token` all emit `[REDACTED]`. A synthetic probe
that embedded a credential in a free-text message *did* survive, but that probe
constructed a log call the application never makes — message scrubbing is
deliberately restricted to `key=value` forms because an earlier broader version
corrupted legitimate prose, and URI credentials are handled by source-level
redaction instead. The residual is the documented **P3-2**: uvicorn's access log
records caller-supplied query strings verbatim, so a *client* that sends
`?token=` writes its own credential to the log. The platform's own client never
does.

**C-2 remains open:** all 13 production variables were delivered as plaintext
environment variables (0 file-backed) during certification; the validator warns
on each and recommends `*_FILE` / Docker secrets.

**Status: PASS WITH CONDITIONS.**

---

## 23. Monitoring & Logging Certification

Structured JSON logging in production with `service`, `environment`, `version`
and `request_id` on every line. Health, readiness and liveness probes all behave
correctly under fault injection (§14, §15). `/api/metrics` **fails closed** —
401 without a token and 401 with a wrong token — and exposes the gauges used
throughout this certification. Security events, audit logging and critical
failures are visible; the Redis circuit breaker narrates open/close transitions
with structured events.

**C-4 remains open: there is no alerting.** Probes and metrics exist; nothing
watches them. Per PH3.11's own lesson — *a gate nobody watches is
indistinguishable from a gate that passes* — this matters more to RTO than any
change to the recovery procedure.

**Status: PASS WITH CONDITIONS.**

---

## 24. Backup & Disaster Recovery

Documentation is complete and, notably, honest about its own limits.

| Item | Status |
|---|---|
| Backup strategy | **DOCUMENTED** — `scripts/backup/` (mongo, config, uploads, restore, verify) |
| Retention | **DOCUMENTED** — 7 daily + 4 weekly + 6 monthly = 17 artifacts |
| Restore procedure | **DOCUMENTED** — R4 runbook + `restore_mongo.sh` |
| Verification | **DOCUMENTED** — three levels (checksum 0.12 s → full restore) |
| Encryption | **DOCUMENTED** |
| RPO | **DOCUMENTED** — ≤ 24 h (nightly 03:15 UTC) |
| RTO | **DOCUMENTED** — ≤ 4 h total loss; ≤ 15 min application-only |
| Credential compromise | **DOCUMENTED** — R9 |
| Secret rotation | **DOCUMENTED** — R8 |
| Rollback | **DOCUMENTED** — R1 + `deploy_rollback.sh` with a ledger and automatic revert |
| **Off-host copy** | **NOT OPERATIONALLY PROVEN** — the docs say plainly "you must add this" |
| **Tested off-host restore** | **NOT OPERATIONALLY PROVEN** |
| Host-loss drill | **NOT EXECUTABLE** — no second host |

39 `test_backup_restore` and 43 `test_disaster_recovery` tests pass, exercising
the scripts' logic hermetically.

**There is no operational backup.** The scripts write to a local `BACKUP_ROOT` on
the same host whose loss they exist to survive. Claiming otherwise would be the
exact error this certification is written to avoid.

**Status: NOT OPERATIONALLY VERIFIED.**

---

## 25. Rollback Procedure

Documented and scripted: `scripts/dr/deploy_rollback.sh` maintains a deployment
ledger (`record`/`list`/`current`), verifies the target image is present before
switching, and automatically reverts if the rolled-back version is also
unhealthy. §8.2 of `DISASTER_RECOVERY.md` documents what it deliberately refuses
to do; §8.3 covers configuration rollback; R5 covers the case where neither
version is healthy.

**Assumption A6 is load-bearing:** the previous image must still be on the host
or rebuildable from a recorded commit. **This interacts directly with L-1** —
the release candidate is an uncommitted working tree, so there is currently no
commit to roll *forward* to or rebuild from.

**Status: DOCUMENTED / NOT OPERATIONALLY PROVEN.**

---

## 26. Known Limitations

Stated rather than assumed:

* No staging environment — smoke tests ran locally in production mode
* No SMTP provider — email verification and password-reset delivery unexercised (C-1)
* No OAuth credentials — live Google round trip unexercised
* No payment provider — live payment callbacks unexercised
* No off-host backup target — host-loss DR unexecutable (C-5)
* No alerting (C-4)
* No multi-day soak; no load run this sprint
* No browser device matrix
* Single-process topology only (C-6) — enforced and warned about by the entrypoint
* All secrets delivered as plaintext env vars, 0 file-backed (C-2)
* No CI run executed on GitHub for this candidate; gates reproduced locally
* Two lockfiles still tracked (C-8)

---

## 27. Outstanding Non-Blocking Items

| ID | Item | Severity |
|---|---|---|
| L-1 | **Release candidate is an uncommitted working tree** — cannot be checked out, tagged or reproduced | High (process) |
| C-1…C-8 | SMTP, dedicated secrets, `MONGO_SOCKET_TIMEOUT_MS`, alerting, off-host backup, single process, same-origin, one lockfile | Deployment prerequisites |
| P2-1 | Public LLM endpoints | P2 |
| P2-2 | WebSocket handshake unrate-limited | P2 |
| P2-3 | 200-trade cap in the trade monitor | P2 |
| P3-1 | Plain-HTTP `FRONTEND_URL` accepted in production — **independently reproduced** (8/9 fail-closed matrix rows pass; this is the one that does not) | P3 |
| P3-2 | uvicorn access log echoes caller-supplied query strings | P3 |
| P3-3 | Redis pool-exhaustion transient at boot — **reproduced**, self-healing | P3 |
| P3-4 | Mongo outage surfaces as 500 on in-flight calls rather than 503 | P3 |
| P3-5 | `source: yahoo_finance` disclosed in success payloads | P3 |
| — | `security-audit` has no aggregate gate job | Low (CI hygiene) |
| — | `codeql` passes when unavailable — not currently enforcing | Low |
| — | `__pycache__` in image (deliberate `compileall`) | P3 |
| — | CRA migration clears 16 npm register entries | Roadmap |
| — | FastAPI + Starlette coordinated upgrade clears 7 python entries | Roadmap |
| — | `TrustedHostMiddleware` for PYSEC-2026-161/248 | Recommended |

---

## 28. Production Launch Checklist

**Blocking (must close before certification can pass):**

1. **B-1** — add `Field` constraints to `PaperTradeCreate` (`server.py:5125`)
   mirroring `TradeCreate` (`models.py:124`); add regression tests for negative
   and zero quantity, negative prices and invalid trade type; reconcile any
   already-corrupted paper balances.
2. **B-2** — disable `/docs`, `/redoc` and `/openapi.json` in production, or
   place them behind authentication.

**Before deploy:**

3. **L-1** — commit and tag the release candidate; record the digest of the image
   built from that tag.
4. Provision SMTP (C-1) and verify the email journey end to end.
5. Move secrets to file/Docker-secret sources (C-2); rotate anything used in
   development.
6. Configure an off-host backup target and **execute a restore drill** (C-5).
7. Configure alerting on readiness, error rate and the Redis circuit (C-4).
8. Set `MONGO_SOCKET_TIMEOUT_MS` (C-3).
9. Deploy behind a Host-validating reverse proxy (C-7; also mitigates
   PYSEC-2026-161/248).
10. Enforce exactly one backend process/replica (C-6).
11. Require `secret-scan` and `config-sync` by name in branch protection.
12. Resolve the two-lockfile ambiguity (C-8).
13. Provision OAuth and payment credentials, then re-run §5 and §12 live.

---

## 29. Final Scorecard

| # | Category | Status |
|---|---|---|
| 1 | Security | **PASS** |
| 2 | Authentication | **PASS** |
| 3 | Authorization | **PASS** |
| 4 | API | **BLOCKED** (B-2) |
| 5 | WebSocket | **PASS** |
| 6 | Market Data | **PASS** |
| 7 | Trading | **BLOCKED** (B-1) |
| 8 | AI | **PASS** |
| 9 | Payments | **NOT OPERATIONALLY VERIFIED** |
| 10 | Analytics | **PASS** |
| 11 | Database | **PASS** |
| 12 | Redis | **PASS** |
| 13 | Memory & Resource | **PASS** |
| 14 | Performance | **PASS WITH CONDITIONS** |
| 15 | Frontend | **PASS** |
| 16 | Docker | **PASS** |
| 17 | CI/CD | **PASS WITH CONDITIONS** |
| 18 | Dependency / Supply-Chain | **PASS** |
| 19 | Secrets | **PASS WITH CONDITIONS** |
| 20 | Monitoring & Logging | **PASS WITH CONDITIONS** |
| 21 | Backup & Disaster Recovery | **NOT OPERATIONALLY VERIFIED** |
| 22 | Rollback | **NOT OPERATIONALLY VERIFIED** |
| 23 | Testing | **PASS** |
| 24 | Documentation | **PASS** |

**Totals: 15 PASS · 4 PASS WITH CONDITIONS · 2 BLOCKED · 3 NOT OPERATIONALLY VERIFIED.**

No CONDITIONAL was promoted to PASS. No NOT OPERATIONALLY VERIFIED was promoted
to PASS. Nothing was marked PASS on the strength of a previous report.

---

## 30. Final Release Decision

> ## **NO-GO — PRODUCTION CERTIFICATION BLOCKED**

### Blockers

**B-1 — `PaperTradeCreate` performs no input validation (`server.py:5125`).**
Any authenticated user can mint unbounded paper capital in a single request: a
`quantity` of `-1000` produced a `total_cost` of `-1000000` which was credited,
moving a balance from ₹86,840 to ₹1,086,840. Negative prices and arbitrary trade
types are likewise accepted. The canonical `TradeCreate` model enforces every one
of these constraints and correctly returns 422; the paper model, declared inline
in `server.py` rather than beside it in `models.py`, was never given them.
Confined to paper trading and to the acting user's own data — no real money, no
broker order, no cross-user impact — but it falsifies paper P&L, the trade
journal and per-user analytics, which is the product's core claim.

**B-2 — production API documentation is publicly exposed.** `/docs`, `/redoc`
and `/openapi.json` return 200 anonymously under `APP_ENV=production`,
disclosing 188 paths, 23 admin routes and 26 schemas. **PH3.11 §9 certified this
as 404.** No secrets are exposed and no authorization boundary fails — all 23
admin paths still return 401 anonymously — so its intrinsic severity is moderate
and the team may reasonably choose to accept it. It is listed as a blocker
because it was certified closed on evidence that was never true, and because it
contradicts a hardening posture the same codebase enforces everywhere else.

### Why this is a NO-GO rather than a conditional GO

Everything else passed, much of it comprehensively and much of it verified live
against a from-scratch image rather than against a prior report. The candidate is
close. But a financial product cannot launch with an endpoint that lets users
fabricate their own performance numbers, and a certification cannot pass a
control it has just proven absent after a previous sprint declared it present.

Both fixes are small and well-understood — one is copying five `Field`
constraints from a model that already exists in the same repository. Neither was
applied here, because the brief forbids silently repairing a newly-discovered
blocker mid-certification, and because a fix applied during certification
invalidates the artifact being certified.

### What is *not* blocking

No code regression was found anywhere. Backend, security, frontend, WebSocket,
authorization, market data, AI, analytics, database, Redis, memory, shutdown,
Docker and the supply-chain gate all reproduce or improve on the PH3.11
baseline. The three NOT OPERATIONALLY VERIFIED categories — payments, backup/DR,
rollback — are unbuilt or unprovisioned operational capabilities, not defects,
and are deployment prerequisites rather than certification blockers.

### Path to GO

Fix B-1 and B-2, add regression tests that fail without each fix, commit and tag
the candidate (L-1), then re-run §7, §10 and §29. Nothing else in this
certification needs to be repeated: the remaining evidence is tied to a working
tree whose hash is recorded in §2 and which was verified unchanged at the end of
this sprint.

### The method note worth carrying

PH3.11 closed by warning that *a gate nobody executes reports nothing, and a
gate nobody watches is indistinguishable from a gate that passes.* Both findings
here are that same lesson one level deeper. B-2 was reported as verified because
it was probed at the wrong path — the evidence existed, and it was evidence for
a different question. B-1 was never found because no sprint had sent hostile
input to the paper-trading surface; every trading test used well-formed data.
**A control is only certified if the probe that tested it could have failed.**
That is the standard the next certification should hold this one to.

---

## Evidence Commands

```bash
# Baseline integrity (identical before and after this sprint)
git diff | shasum -a 256          # b2f4921d…b32725

# Suites
cd backend && venv/bin/python -m pytest -q              # 2559 passed, 95 deselected, 4 xfailed
cd backend && venv/bin/python -m pytest -m security -q  # 452 passed
cd frontend && CI=true npx craco test --watchAll=false  # 395 passed, 22 suites
cd frontend && REACT_APP_BACKEND_URL="https://ci.invalid" npm run build   # exit 0, 48 bundles

# Route surface / analytics
protected=97 admin=29 public=75 total=201
{'real': 4, 'derived': 32, 'mock': 0, 'unavailable': 17}

# Dependency gate (+ 7 negative tests)
python .github/scripts/dependency_audit.py --ecosystem all              # exit 0
python .github/scripts/dependency_audit.py --ecosystem python --today 2026-11-16   # exit 1 EXPIRED
#   register SHA-256 6868a4e2…dcaa4cc — identical before and after every mutation

# Image, from scratch
docker build --no-cache --pull -f backend/Dockerfile -t stockassist-rc:ph312 backend
#   425 MB, sha256:f373296b…638b142, uid 10001, pip ABSENT, no .env

# B-1 reproduction
curl -X POST $API/paper/trade -H "Authorization: Bearer $TOK" \
  -d '{"symbol":"INFY","action":"BUY","quantity":-1000,"entry_price":1000,"stop_loss":900,"target1":1200}'
#   → 200 ; balance 86840 → 1086840
curl -X POST $API/trades ... '"quantity":-1000'   # → 422 "Input should be greater than 0"

# B-2 reproduction
curl -o /dev/null -w '%{http_code}' http://localhost:8312/docs          # → 200
curl -o /dev/null -w '%{http_code}' http://localhost:8312/openapi.json  # → 200 (188 paths, 23 admin)
curl -o /dev/null -w '%{http_code}' http://localhost:8312/api/admin/users  # → 401 (authz intact)

# Fault injection
docker stop ph312-redis   # ready 200, redis fail/non-critical, serving, 0 restarts, auto-recovery
docker stop ph312-mongo   # live 200, ready 503 critical, 0 restarts, full recovery
docker stop --timeout 30 ph312-backend   # 2s, exit 0, all 4 tasks stopped
```

---

# PH3.12R — Blocker Remediation Addendum

**Date:** 2026-08-18
**Scope:** close B-1 and B-2 and resolve L-1. Nothing else.
**Status:** remediation implemented and verified. **PH3.12 certification is NOT
passed.** This addendum records the fixes and their evidence so that a fresh
certification run has something to certify; the go/no-go in §30 stands until
that run is executed.

Both blockers shared one property, and it is the reason this addendum exists in
the certification report rather than in a separate document: **neither was a
newly-introduced defect.** Both had been present for the whole of PH3, and both
survived because the probe that should have caught them could not have failed —
B-2 was measured at a path the application never served, and B-1 was never
measured at all because every trading test in the suite sent well-formed input.
The fixes below are therefore judged on the same standard: each one is
accompanied by tests that were **run against the pre-fix code and observed to
fail**, and the counts are recorded.

---

## R-1. B-1 — `PaperTradeCreate` input validation

### Root cause

Not "a missing `Field(...)` call". The contract for a trade entry — what a
quantity, a price and a side are allowed to be — was **written down twice**:

* `models.py:124`, `TradeCreate`, with `gt=0` on quantity/entry_price/
  stop_loss/target1, `le=100000` on quantity, and `^(BUY|SELL)$` on type.
* `server.py:5125`, `PaperTradeCreate`, declared inline next to its route
  handler, ~5,000 lines away, with bare annotations and no constraint at all.

Two declarations of one contract, with nothing linking them. Nobody editing
`TradeCreate` could see that a second model described the same domain, so the
bounds were added to one and never to the other. `execute_paper_trade` then
computed `total_cost = entry_price * quantity`, and for `quantity = -1000` the
BUY branch called `update_paper_balance(user_id, -total_cost)` — subtracting a
negative, i.e. **crediting** ₹10,00,000. The balance check `balance < total_cost`
passed trivially because `total_cost` was negative.

A **second, latent instance of the same class** was found while fixing it and is
closed by the same change: Python's `json.loads` — which is what Starlette parses
request bodies with — accepts the non-standard literals `Infinity`, `-Infinity`
and `NaN`, and a plain `gt=0` float **admits `Infinity`** (`inf > 0` is True;
only `NaN` fails the comparison). `entry_price: Infinity` therefore passed every
bound `TradeCreate` had, on the **real** trade endpoint as well as the paper one,
and would have written an infinite `total_cost` into the trade journal. Verified
empirically before being claimed.

### Fix

`backend/models.py` — the contract now exists **once**, as named constrained
types, and every trade-entry model is spelled in terms of them:

```python
TradeSide          = Annotated[str,   Field(pattern="^(BUY|SELL)$")]
TradeQuantity      = Annotated[int,   Field(gt=0, le=100000)]
TradePrice         = Annotated[float, Field(gt=0, allow_inf_nan=False)]
OptionalTradePrice = Annotated[float, Field(ge=0, allow_inf_nan=False)]
TradeSymbol        = Annotated[str,   Field(min_length=1, max_length=32,
                                            pattern=r"^[A-Za-z0-9][A-Za-z0-9&.\-]{0,31}$")]
```

`PaperTradeCreate` moved from `server.py` into `models.py`, directly beneath
`TradeCreate`, and rewritten in those types. Adjacency is part of the fix: the
divergence is now visible at a glance and cannot be reintroduced without
deleting a shared type. `TradeCreate` was rewritten in the same aliases —
constraint-for-constraint identical to what it already enforced, **plus**
`allow_inf_nan=False`, which is the second defect above and is the one
intentional behaviour change to `/api/trades` in this sprint (see *Scope
deviations* below).

Three additional constraints apply to the paper model only, because its `symbol`
and free-text fields reach the trade journal, the activity log and the analytics
group-by keys: a bounded character-restricted `symbol` (`M&M`, `BAJAJ-AUTO` and
lowercase input all still accepted), `max_length` on `stock_name`/`setup_type`/
`notes`, and `extra="forbid"` so a paper payload cannot name live-execution
fields such as `broker` or `auto_exit`.

**Ordering.** Validation is model-layer, so it completes before the route
handler body executes and therefore before any balance, position, trade or P&L
write is reachable. Malformed input answers **422** and mutates nothing.

**Defence in depth.** `services/paper_trade.execute_paper_trade` now re-validates
its own arguments **against the same model** (not a hand-written copy of its
rules) before touching anything, raising `ValueError` → 400. It is an importable
function, and "the caller already validated it" is precisely the assumption that
produced B-1.

### Regression tests

`backend/tests/test_paper_trade_validation.py` — **132 tests**.

| Group | Covers |
|---|---|
| `TestPH312Exploit` | the literal finding: `quantity=-1000` → 422 **reported against `quantity`**, balance identical after 3 repeats, no trade document written |
| `TestHostileInputRejected` | 28-case matrix: negative/zero/over-ceiling/non-integer quantity, negative/zero/non-numeric price, negative/zero stop-loss and targets, four invalid sides, seven malformed symbols (empty, whitespace, HTML, path traversal, Mongo operator, newline suffix, overlong), unknown keys, overlong free text — plus `Infinity`/`-Infinity`/`NaN` sent as raw text, malformed JSON (not a 500), missing fields, and the PH1.5 no-echo guarantee |
| `TestRejectedRequestsMutateNothing` | every one of the 28 cases run twice — once on a fresh account, once on an account **holding an open position** so balance, positions, unrealised P&L and journal all carry non-trivial values — asserting a full account snapshot is identical afterwards; plus "no rejected request may leave the balance above where it started" |
| `TestValidTradesUnaffected` | valid BUY/SELL, seven real-world symbol shapes, omitted optionals, explicit `target2: 0`, **all eleven setup types the UI dropdown offers**, quantity exactly at the 100,000 ceiling, and insufficient-capital still 400 (not 422) |
| `TestServiceLayerGuard` | direct `execute_paper_trade` calls bypassing HTTP |
| `TestCanonicalConstraintsAreShared` | paper and real models share **the same** field metadata objects; the aliases are the ones actually in use; the route is still behind `get_current_user` |

**Falsifiability — measured, not asserted.** With `PaperTradeCreate` reverted to
its pre-fix form and the service guard removed: **94 failed, 38 passed.** The 38
that passed are the valid-input cases, which is the correct result. Fix
restored: **132 passed.**

---

## R-2. B-2 — production API documentation exposure

### Root cause

`server.py:357` was `app = FastAPI(title="AlphaPartner API")` — every
documentation URL left at its framework default, with no environment gating
anywhere in application code.

The reason it survived is separate from the reason it existed. **PH3.11 §9
probed `/api/docs`, observed 404, and recorded the control as verified.**
`/api/docs` was never a route this application served; the 404 came from the
generic unknown-path handler. The probe could not have failed, so it certified
nothing, and the real paths went unmeasured for two sprints.

### Fix

New module **`backend/security/api_docs.py`**, and `server.py` now constructs
`FastAPI(title="AlphaPartner API", **api_docs.docs_kwargs())`.

| `APP_ENV` | `/docs` | `/redoc` | `/openapi.json` |
|---|---|---|---|
| `development` | 200 | 200 | 200 |
| `testing` | 200 | 200 | 200 |
| `staging` | 200 | 200 | 200 |
| **`production`** | **404** | **404** | **404** |

Four design decisions, each made against a specific failure mode:

1. **All three switch together**, returned as one dict from one function.
   Disabling `docs_url` alone leaves `/openapi.json` serving the whole schema —
   the half that actually matters, because it is the machine-readable one. The
   API is structurally incapable of the partial fix.
2. **`None`, not a guard returning 403.** The routes are never registered, so a
   disabled path returns the ordinary unknown-path 404 and discloses nothing by
   the difference.
3. **The environment is read through `security.secrets.app_env()`** — the one
   existing primitive — so documentation exposure cannot disagree with the
   cookie policy or the diagnostics endpoint about which environment this is.
4. **There is no variable that can enable docs in production.**
   `API_DOCS_ENABLED=false` only ever *tightens*, and only outside production;
   production is forced off regardless, mirroring `cookies.cookie_secure()`. An
   enable-flag would mean one mistyped variable reopens exactly this hole, and
   it would be rediscovered exactly the way B-2 was.

`app.openapi()` still works with `openapi_url=None` — the schema is
**unpublished, not ungenerable** — so deployment tooling and the route-inventory
sweeps are unaffected. Verified.

### Regression tests

`backend/tests/test_api_docs_exposure.py` — **52 tests** — plus the harness
`backend/tests/_prod_app_probe.py`.

The real paths are written as **literals** (`/docs`, `/redoc`,
`/openapi.json`), never derived from the constant under test — a probe that
computes the path it requests from the value being tested agrees with any bug in
that value, which is how `/api/docs` came to be certified.

* `TestPolicy` — production disables all three; each non-production environment
  enables all three; **the partial fix is impossible**; no value of
  `API_DOCS_ENABLED` re-enables production; the override tightens outside it; an
  unrecognised `APP_ENV` gets docs (fails open on a typo, not silently hardened);
  an empty mapping does not fall back to the host environment.
* `TestProductionRoutes` / `TestDevelopmentRoutes` / `TestTheSwitchIsDeterministic`
  — real `TestClient` requests to the real paths under both environments,
  `[404,404,404]` vs `[200,200,200]`, response bodies checked for schema leakage,
  normal routes unaffected. Includes an explicit test that **`/api/docs` answers
  404 in both environments** — the assertion documenting why PH3.11's evidence
  was empty.
* **`TestTheShippedApplicationInProduction`** — the class that would have caught
  B-2. It boots the **real `server` module in a clean interpreter with
  `APP_ENV=production`** and asserts the real routes answer 404 on the real app.
  A subprocess is required because `server` builds `app` at import time and
  `conftest.py` has already imported it as `testing`; without it, a regressed
  `FastAPI(title=...)` would be **indistinguishable from the fix** under the
  suite's own environment, since both serve docs there. The probe boots from the
  suite's synthetic environment plus the three values production validation
  requires (fake CORS origin, fake AI key, credentialed **loopback** Mongo URL —
  loopback specifically so no DNS lookup occurs), requests only the three doc
  paths, touches no database, and completes in ~0.7 s.
* `TestNoCollateralDamage` — health endpoints, anonymous 401, authenticated 200,
  admin 403, security headers, unknown-path 404 all unchanged.

**Falsifiability — measured, not asserted.** Two mutations were applied and
observed:

| Mutation | Result |
|---|---|
| `FastAPI(title="AlphaPartner API")` (the pre-fix line) | **5 failed** — all three doc paths 200 in production, schema leaked, URLs set |
| `FastAPI(..., docs_url=None, redoc_url=None)` — **the half-fix**, Swagger hidden but `/openapi.json` still served | **7 failed** — `/openapi.json` 200 in production, and development broken |
| Fix restored | **52 passed** |

The half-fix mutation is recorded deliberately: it is the shape this repair would
most plausibly have taken, and the suite rejects it.

---

## R-3. L-1 — release reproducibility

See the *Release Reproducibility* block in `.claude/CHANGELOG.md` for the
recorded commit SHA, image tag and image identifier. The working tree was
verified clean after the release commit, and the image was rebuilt from that
commit and its application source compared against the committed tree.

**Reproducibility caveat, stated rather than glossed:** `--no-cache --pull`
builds are **not bit-reproducible** — pip resolution and layer timestamps vary,
so two builds of the same commit yield different image IDs. The verifiable
property, and the one that was verified, is that the **application source inside
the image matches the committed source**. An image ID is a build identity, not a
source identity, and treating it as the latter would be another control that
cannot fail.

---

## Scope deviations, stated explicitly

Two changes fall marginally outside a literal reading of "fix B-1 and B-2", and
are recorded here rather than left for a reviewer to find:

1. **`TradeCreate` also gained `allow_inf_nan=False`** (via the shared
   `TradePrice` alias). This changes `/api/trades` behaviour: `entry_price:
   Infinity` now returns 422 where it previously returned 200. It is the same
   defect class as B-1 on the same field, `Infinity` is not valid JSON in the
   first place, and no legitimate client can be affected. Keeping the real
   endpoint deliberately weaker than the paper one, purely to stay inside the
   letter of the brief, was judged the worse call. All other `TradeCreate`
   constraints are constraint-for-constraint identical to before.

2. **`extra="forbid"` on `PaperTradeCreate`.** Verified against
   `frontend/src/pages/PaperTrading.jsx`, which submits exactly the ten declared
   fields. Note for the next certification run: the PH3.12 reproduction `curl`
   in *Evidence Commands* above sends `"action":"BUY"`, an unknown key, and will
   now be rejected for **that** reason rather than for the quantity bound. Use a
   payload valid in every field except `quantity`; the regression suite asserts
   the error is reported against `quantity` specifically, for exactly this
   reason.

**Nothing else was touched.** JWT architecture, refresh rotation, cookie policy,
CORS, CSRF, rate limiting, OAuth, Redis, the trading engine beyond the shared
model aliases, payments and analytics are all unchanged.

---

## Verification — full battery

| Gate | Result |
|---|---|
| Backend hermetic suite | **2,743 passed** / 0 failed / 4 xfailed / 95 deselected (baseline 2,559 + 184 new) |
| `pytest -m security` (PH1 controls) | **452 passed** — unchanged |
| Paper trading (both suites) | **138 passed** |
| API documentation configuration | **52 passed** |
| WebSocket security matrix | **17 passed** |
| Authz / route-inventory / validation sweeps | **867 passed** |
| Health & readiness | **210 passed** |
| Trading engine + trading API | **71 passed** |
| Frontend suite | **395 passed** / 22 suites |
| Frontend production build | exit 0 |
| Dependency audit (`--ecosystem all`) | **exit 0** — 7 python + 16 npm, all triaged |

**Route inventory — the documentation change removes exactly four routes and
nothing else:**

```
routes (development/testing): 193
routes (production):          189
removed in production:        /docs, /docs/oauth2-redirect, /openapi.json, /redoc
added in production:          (none)
/api route count:             unchanged
OpenAPI paths in schema:      188   ← matches this report's §7 measurement
```

`/docs/oauth2-redirect` is Swagger UI's own OAuth2 helper, registered by FastAPI
alongside `docs_url`; it disappears with the page it belongs to.

---

## Evidence commands — PH3.12R

```bash
# Falsifiability (each mutation applied, measured, reverted)
pytest tests/test_paper_trade_validation.py   # pre-fix: 94 failed / 38 passed → fixed: 132 passed
pytest tests/test_api_docs_exposure.py        # pre-fix: 5 failed, half-fix: 7 failed → fixed: 52 passed

# The shipped application, booted as production
python -m tests._prod_app_probe
#   {"environment":"production","docs_url":null,"redoc_url":null,"openapi_url":null,
#    "schema_generable":true,"statuses":{"/docs":404,"/redoc":404,"/openapi.json":404}}

# Full battery
pytest -q                       # 2743 passed, 4 xfailed, 95 deselected
pytest -m security -q           # 452 passed
yarn test --watchAll=false      # 395 passed / 22 suites
yarn build                      # exit 0
python .github/scripts/dependency_audit.py --ecosystem all   # exit 0

# Release image
docker build --no-cache --pull -f backend/Dockerfile -t stockassist-rc:ph312r backend
```

---

## Remaining blockers

**None from B-1, B-2 or L-1.** All three are closed with falsifiable evidence.

Unchanged and still open, none of them introduced or affected by this sprint:
the eight PH3.10 deployment conditions (C-1…C-8), and the three categories §29
records as **NOT OPERATIONALLY VERIFIED** — payments, backup/off-host DR, and
rollback. Those are unbuilt operational capabilities rather than defects, and
they are the reason this addendum does not and cannot convert §30 into a GO.

**Recommendation: the project is ready for a fresh PH3.12 certification rerun.**
That rerun — not this addendum — decides the release.

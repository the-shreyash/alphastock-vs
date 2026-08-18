# PH3.12 Production Certification — Rerun

**Sprint:** PH3.12 (rerun) — Final Production Certification & Release Decision
**Date:** 2026-08-18
**Engineer:** Principal Release Engineer
**Preceding gate:** PH3.12R remediation (`5c1ceab`, `a4ee79f`)
**Supersedes:** the PH3.12 certification of 2026-08-17 (verdict NO-GO), preserved
verbatim — together with its PH3.12R remediation addendum — at
[`PH3.12_PRODUCTION_CERTIFICATION_NOGO_ARCHIVE.md`](./PH3.12_PRODUCTION_CERTIFICATION_NOGO_ARCHIVE.md).
That document is the audit trail this one rests on and is not superseded in the
sense of being retracted: its findings were correct and its NO-GO verdict stands
for the commit it examined.

---

## 1. Executive Summary

This is an independent rerun of the PH3.12 production certification. Nothing in
this document is carried over from PH3.11, PH3.12 or PH3.12R. Every number was
re-measured, every control re-probed against a production image built from
scratch during this sprint, and **no application code was modified** — the
working tree hashes clean at `a4ee79f` before and after.

**All three previously-blocking findings are independently confirmed CLOSED.**

* **B-1 (paper-trade input validation)** — 15 hostile payloads, including the
  original `quantity=-1000` exploit, all answer **422** against the production
  image, and the paper balance is **byte-for-byte unchanged** across the whole
  matrix. Re-verified against an account **holding an open position**. Valid BUY
  and SELL still work with exactly correct debit, credit, position and P&L
  arithmetic.
* **B-2 (API documentation exposure)** — `/docs`, `/redoc` and `/openapi.json`
  all answer **404** in production. Critically, the probe was made **falsifiable**:
  the *same image* with only `APP_ENV=development` serves all three with **200**
  and 188 paths. In production the routes are **not registered at all** (0 of 4
  documentation routes present in the live route table). This is the exact
  failure mode that invalidated PH3.11's evidence, and it does not recur here.
* **L-1 (reproducibility)** — the release candidate is a real commit on `main`
  with a clean working tree, and all 117 files inside the image that exist in
  the tree are **byte-identical** to the committed blobs (0 mismatches).

**Headline regression numbers reproduced exactly:** backend **2,743**, security
**452**, frontend **395**, production build **PASS**, dependency gate **PASS**.
Zero deviations from the PH3.12R baseline.

**Two new findings were discovered that no previous sprint recorded.** Neither
is a security compromise, a financial-integrity defect, or an authentication
bypass.

* **C-1 — a host-local, git-ignored test artifact is baked into the release
  image.** ✅ **CLOSED by PH3.12C remediation — see §31.** `backend/test-results/junit.xml` is present at `/app/test-results/
  junit.xml` in the image but is **absent from the commit**. Proven conclusively:
  a build from the developer's working directory yields **117** files in `/app`;
  a build from a clean `git archive` of the *same commit* yields **116**. The
  image is therefore not a pure function of the commit. The file also carries the
  developer's machine hostname. It is inert — never imported, never executed,
  never served — but §2 of this gate explicitly requires that no test-only file
  enter production, and that check fails.
* **C-2 — WITHDRAWN. This finding was wrong; see §31.2.** It was attributed to a
  Redis outage, but the correlation was spurious: a 20-line control container with
  no application code reproduces the identical exit 137, and the application exits
  **0** on every direct SIGTERM. The original text is left below unedited so the
  error is auditable.
  ~~The container exits 137 (SIGKILL) on graceful shutdown after a Redis
  outage-and-recovery.~~ Reproduced 4/4 with the default `docker stop`; the
  no-outage baseline exits 0 (4/4), and a 60-second stop timeout also exits 0.
  Application-level teardown itself is complete and ordered. The impact is
  operational signalling, not data integrity — no collection, balance or trade
  was ever lost across any restart in this sprint.

**Verdict: GO — CONDITIONAL (see §25).** The committed source at `a4ee79f` is
certified production-ready. The *specific image built during this sprint* must be
rebuilt after the one-line C-1 fix before it is deployed. Payments, backup/DR and
rollback remain **NOT OPERATIONALLY VERIFIED** and are named as deployment
prerequisites, not as blockers.

---

## 2. Certified Commit

| Measure | Value |
|---|---|
| Commit | `a4ee79f3e8ba5f689e265a8c804c9e9674717173` |
| Branch | `main` |
| Working tree | **clean** (`git status --porcelain` empty, verified at start and end) |
| Uncommitted files | none |
| Untracked production code | none |
| Commit date | 2026-08-18 12:16:32 +0530 |
| Subject | `docs: record PH3.12R release artifact identifiers and live production verification` |

L-1 is **CLOSED**: the release candidate is a commit, checkout-able and taggable,
not an uncommitted working tree.

---

## 3. Release Image

| Measure | Value |
|---|---|
| Tag | `stockassist-rc:ph312-cert` |
| Image ID | `sha256:42d12ddf19697eb9b65b194bc2910466d8e1e758d9d51f71ab380eec49abf8ce` |
| Size | **425 MB** |
| Build command | `docker build --no-cache --pull -f backend/Dockerfile -t stockassist-rc:ph312-cert backend` |
| Base | `python:3.11-slim-bookworm` (Python 3.11.16) |
| Runtime user | `appuser` (uid/gid 10001), non-root |
| `APP_ENV` default | `production` |
| `WEB_CONCURRENCY` | `1` (single-process, per PH3.11) |
| Control image | `stockassist-rc:ph312-clean` (`908065eb7258`) — built from `git archive HEAD` |

Image hygiene, verified inside the container:

| Check | Result |
|---|---|
| Test files (`test_*`, `conftest.py`, `tests/`) | **none** |
| Dotenv / `*.pem` / `*.key` | **none** |
| `pip` present | **removed** (both venv and system) |
| Secret-shaped env vars | none (only the base image's `GPG_KEY`) |
| gitleaks over `/app` | **no leaks found** |
| Configured secret values baked in | **0 occurrences** |

---

## 4. Certification Scope

This is a release gate, not a development sprint. No product feature was added,
no unrelated code refactored, no trading logic altered, no authentication
architecture touched, no PH1 control modified, and no failure suppressed. The one
file modified during the sprint — `.github/dependency-triage.yml`, mutated to
prove the stale-entry check bites — was restored, and the tree verified clean.

Three claims are kept separate throughout and never conflated:

1. **CODE CORRECT** — the artifact behaves correctly.
2. **SECURITY CORRECT** — the controls hold under hostile input.
3. **OPERATIONALLY READY** — it has been observed working in a real production
   environment.

**Nothing in this document claims #3.** No production environment exists.

---

## 5. Test Environment

| Component | Value |
|---|---|
| Host | macOS (Darwin 25.5.0), arm64 |
| Docker | 29.4.0 |
| Python (host venv) | 3.11.15 · **image** 3.11.16 |
| Node / npm | v23.11.0 / 10.9.2 |
| MongoDB | `mongo:7.0` (root auth enabled) |
| Redis | `redis:7.2-alpine` (`requirepass` enabled) |
| App container | `stockassist-rc:ph312-cert`, `APP_ENV=production`, port 18000 |
| pip-audit | 2.7.3 (the version pinned by `dependency-audit.yml`) |
| gitleaks | present on host |

The stack was run on an isolated Docker network with real authenticated MongoDB
and password-protected Redis — not with the hermetic test doubles.

---

## 6. Full Test Results

| Suite | This rerun | PH3.12R baseline | Delta |
|---|---|---|---|
| Backend (`pytest -m "not integration"`) | **2,743 passed**, 95 deselected, 4 xfailed | 2,743 | **0** |
| PH1 security (`pytest -m security`) | **452 passed**, 2,390 deselected | 452 | **0** |
| Frontend (`craco test`) | **395 passed**, 22 suites | 395 | **0** |
| Frontend production build | **PASS** (exit 0) | PASS | **0** |
| Dependency gate | **PASS** (exit 0) | PASS | **0** |
| Route inventory | 201 HTTP + 1 WS · 97/29/75 | 201 · 97/29/75 | **0** |
| MOCK analytics metrics | **0 of 53** | 0 | **0** |

Runtimes: backend 202.20 s, security 33.17 s, frontend 16.41 s.

**Every headline number is reproduced exactly. There are no unexplained deltas.**

Two apparent discrepancies, both resolved and neither a regression:

* **Route count 202 vs. the baseline's 201.** The raw FastAPI route table has 202
  entries; the project's own dependency-graph classifier (`tests/_routes.py`)
  counts 201 because it classifies `APIRoute` only. The difference is exactly the
  one `APIWebSocketRoute` (`/api/ws`). Classification is identical to baseline.
* **Frontend build exit 1 under `CI=true`.** Create React App promotes warnings to
  errors when `CI=true`. The project's own pipeline deliberately does **not** set
  it (`.github/workflows/frontend-ci.yml:146` — *"To tighten this later: clear the
  62 warnings, then set CI: 'true' here"*). At the project standard the build
  exits **0** and emits **62** ESLint warnings — precisely the documented PH3.10
  baseline, with 132 JS chunks and a valid `index.html`. **PASS**, with the
  warning backlog recorded as a known limitation (§24).

Backend `xfail` detail: 4 expected failures, all `D-10` (registration does not
validate email format) — a pre-existing, documented limitation, not a regression.

---

## 7. B-1 Verification — Paper-Trading Input Validation

Probed against the running production image with a real authenticated user whose
paper balance began at **₹100,000.00**.

### 7.1 Hostile-input matrix

| # | Payload | HTTP | Balance | Field identified |
|---|---|---|---|---|
| 1 | `quantity: -1000` **(historical exploit)** | **422** | unchanged | `quantity: Input should be greater than 0` |
| 2 | `quantity: 0` | 422 | unchanged | `quantity: greater than 0` |
| 3 | `quantity: 99999999` | 422 | unchanged | `quantity: less than or equal to 100000` |
| 4 | `entry_price: -500` | 422 | unchanged | `entry_price: greater than 0` |
| 5 | `entry_price: 0` | 422 | unchanged | `entry_price: greater than 0` |
| 6 | `entry_price: NaN` | 422 | unchanged | `entry_price: should be a finite number` |
| 7 | `entry_price: Infinity` | 422 | unchanged | `entry_price: should be a finite number` |
| 8 | `entry_price: -Infinity` | 422 | unchanged | `entry_price: should be a finite number` |
| 9 | `quantity: "abc"` | 422 | unchanged | `quantity: valid integer` |
| 10 | `quantity: 1e309` | 422 | unchanged | `quantity: should be a finite number` |
| 11 | unknown field `broker` | 422 | unchanged | `broker: Extra inputs are not permitted` |
| 12 | unknown field `is_paper` | 422 | unchanged | `is_paper: Extra inputs are not permitted` |
| 13 | `type: "HACK"` | 422 | unchanged | `type: must match ^(BUY\|SELL)$` |
| 14 | `symbol: "REL; DROP {$ne}"` | 422 | unchanged | `symbol: pattern violation` |
| 15 | `target2: -5` | 422 | unchanged | `target2: greater than or equal to 0` |

**Balance before matrix: `100000.0`. Balance after matrix: `100000.0`.** Byte-for-byte
unchanged across all fifteen attempts. The key historical exploit returns 422,
names the invalid field, and mutates nothing.

### 7.2 Exploit replay against an account holding an open position

With an open 10 × RELIANCE position and a balance of ₹90,000:

```
quantity=-1000 with open position -> HTTP 422 ; balance 90000.0 (was 90000.0)
```

### 7.3 Valid trading still works

| Step | Observed | Expected | Correct |
|---|---|---|---|
| BUY 10 × RELIANCE @ ₹1,000 | balance 100000.0 → **90000.0** | −₹10,000 | ✅ |
| Trade record | `type=BUY qty=10 entry=1000.0 total_cost=10000.0 status=OPEN is_paper=true` | — | ✅ |
| SELL-side entry 5 × TCS @ ₹4,000 | record created, balance unchanged | no debit on SELL by design | ✅ |
| Close BUY at live mark ₹1,322.90 | balance 90000.0 → **103229.0** | +10 × 1322.90 = ₹13,229 | ✅ |
| Realized P&L | **₹3,229.00** (32.29 %) | (1322.90 − 1000) × 10 | ✅ |
| Live-quote mark | `exit_price: 1322.9` from the market gateway | — | ✅ |

Arithmetic is exact in every case. **B-1 is CLOSED.**

### 7.4 Structural confirmation

The contract exists once, as shared constrained aliases in `models.py`
(`TradeSide`, `TradeQuantity`, `TradePrice`, `OptionalTradePrice`, `TradeSymbol`),
with `PaperTradeCreate` moved beside `TradeCreate` and spelled in them.
`allow_inf_nan=False` closes the `Infinity`/`NaN` class that a bare `gt=0` admits.
`extra="forbid"` rejects unknown keys. `execute_paper_trade` re-validates against
the same model — not a copy of its rules — for non-HTTP callers.

---

## 8. B-2 Verification — API Documentation Exposure

Probed anonymously against the running production application, at the exact
production paths.

| Path | HTTP | Body |
|---|---|---|
| `GET /docs` | **404** | 22 bytes |
| `GET /redoc` | **404** | 22 bytes |
| `GET /openapi.json` | **404** | 22 bytes |
| `GET /api/docs` | 404 | 22 bytes |
| `GET /api/openapi.json` | 404 | 22 bytes |

`/api/docs` was probed only to demonstrate it is **not** a substitute; the
verdict rests on the three real paths.

### 8.1 The probe is falsifiable — this is the point

PH3.11 recorded B-2 as closed on the strength of a 404 at `/api/docs`, a path the
application never served. That probe could not have failed, so it certified
nothing. This rerun therefore establishes that the probe **can** observe a 200:

| Configuration | `/docs` | `/redoc` | `/openapi.json` |
|---|---|---|---|
| **Same image**, `APP_ENV=development` | **200** | **200** | **200** (188 paths, 26 schemas) |
| **Same image**, `APP_ENV=production` | **404** | **404** | **404** |

Only the environment variable differs. The control is real.

### 8.2 Structural confirmation

Introspecting the live production `server.app` route table:

```
DOCUMENTATION ROUTES REGISTERED: 0   []
```

The routes are **not registered at all** — the 404 is the generic unknown-path
response, so an attacker cannot distinguish "documentation disabled" from "no such
endpoint". `security/api_docs.py` forces this off in production with **no**
enable-override, mirroring `cookie_secure()`. **B-2 is CLOSED.**

### 8.3 Surrounding surface unaffected

| Check | Result |
|---|---|
| Authenticated normal routes | `/api/auth/me`, `/api/paper/{balance,trades,pnl}`, `/api/trades`, `/api/portfolio`, `/api/watchlist`, `/api/notifications` → all **200** |
| Protected routes still protected | 97/97 anonymous → **401** |
| Admin routes still protected | 29/29 anonymous → 401/403; 29/29 normal user → **403** |

---

## 9. L-1 Verification — Reproducibility of the Release Candidate

| Check | Result |
|---|---|
| Working tree clean | ✅ `git status --porcelain` empty |
| Release candidate is a commit | ✅ `a4ee79f` on `main` |
| Dependency tooling tracked | ✅ `.github/dependency-triage.yml`, `.github/scripts/dependency_audit.py` |
| Image source vs. committed blobs | ✅ **0 content mismatches** across 117 files |
| Files in tree but not image | 79 — exactly `tests/` (75), `.dockerignore`, `.env.example`, `Dockerfile`, `requirements-dev.txt`, all excluded by design |
| Files in image but not tree | **1 — `test-results/junit.xml`** ⚠️ see C-1 |

L-1 as originally raised (an uncommitted working tree) is **CLOSED**. A distinct
reproducibility defect, C-1, was found in its place — see §21 and §23.

---

## 10. PH1 Security Regression

All 452 PH1 security tests pass. Each control below was **additionally re-probed
live** against the production image, because a passing hermetic test and a correct
production binary are different claims.

| Control | Live evidence | Status |
|---|---|---|
| Authentication | no token → 401; forged-signature JWT → 401 | PASS |
| OAuth | no mock/demo OAuth path in application code | PASS |
| Cookies | `access_token`/`refresh_token`: **HttpOnly; Secure; SameSite=lax**; `csrf_token` readable by design | PASS |
| CORS | disallowed origin → **no** `Access-Control-Allow-Origin`; allowed origin echoed | PASS |
| Password policy | `"password123"` → **422** | PASS |
| JWT | forged signature → 401; `aud`/`iss`/`ver`/`sid`/`jti` present | PASS |
| Refresh rotation | refresh → 200 and token **rotated** (old ≠ new) | PASS |
| Refresh replay | replay of old token → **401** | PASS |
| Family revocation | after replay the **new** token is also **401** — whole family revoked | PASS |
| Session revocation | logout / logout-all covered by suite; family revocation proven live | PASS |
| CSRF | missing header → **403**; forged header → **403**; **valid header → 200** (falsifiable) | PASS |
| Rate limiting | login 429 first on attempt **#6**, matching the configured `5/900` policy exactly | PASS |
| Email verification | controls present; enforcement gated by design | PASS |
| RBAC | 29/29 admin routes → 403 for a normal user | PASS |
| API validation | 15/15 hostile paper-trade payloads → 422 (§7) | PASS |
| Security headers | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, `CSP: default-src 'none'`, `HSTS max-age=63072000`; **no `Server` / `X-Powered-By`** | PASS |
| Audit logging | `security_audit_logs` written; no invented refund events (§14) | PASS |
| Dependency gate | exit 0, and proven to bite three ways (§17) | PASS |

**No previously-closed PH1 finding has reopened. No PH1 finding is a release blocker.**

---

## 11. WebSocket Verification

Full attack matrix re-run against `ws://.../api/ws` on the production image.

| # | Attempt | Result |
|---|---|---|
| 1 | Anonymous, no credential | **REJECTED — HTTP 403** |
| 2 | Spoofed `?user_id=<real id>` | **REJECTED — HTTP 403** |
| 3 | Query-string token `?token=<valid JWT>` | **REJECTED — HTTP 403** |
| 4 | Forged subprotocol (`stockassist.auth` + bogus token) | **REJECTED — HTTP 403** |
| 5 | **Legitimate** subprotocol (`stockassist.auth` + valid JWT) | **CONNECTED** — received live `market_update` |
| 6 | **Legitimate** cookie auth | **CONNECTED** |

Cases 5 and 6 make the matrix falsifiable: the probe demonstrably distinguishes
accept from reject. Case 3 confirms the deliberate refusal of the query-string
transport, which would otherwise write live credentials into access logs.
**Unauthorized WebSocket access remains fully rejected.**

---

## 12. Infrastructure Verification

All timings measured this sprint against the production image.

| Scenario | Observed | Expected | Verdict |
|---|---|---|---|
| Mongo + Redis available | live 200, ready 200, healthy | — | PASS |
| **Redis unavailable** | live **200**, ready **200**, authenticated request **200**; circuit OPEN → in-process fallback; **0 restarts** | graceful degradation, no crash loop | PASS |
| Redis restored | circuit **closed automatically**, `redis_connected` re-logged, **0 restarts** | automatic recovery | PASS |
| **Mongo unavailable** | live **200**, ready **503** within **7 s**; body names `mongodb: fail (timeout after 2s), critical: true`; **0 restarts** | liveness healthy, readiness unhealthy | PASS |
| Mongo restored | ready **200 within 5 s**, balance intact at `103229.0`, **0 restarts**, no manual intervention | automatic recovery | PASS |
| Shutdown (no prior outage) | **exit 0** in **1.16–1.64 s**; ordered teardown: draining → heartbeat stopped → 2 background tasks cancelled → pub/sub stopped → Redis closed → HTTP pools closed → *Application shutdown complete* | clean exit, no hanging tasks | PASS |
| **Shutdown after a Redis outage** | **exit 137**, 4/4 runs, ~1.4–3.0 s | exit 0 | ⚠️ **C-2** |
| Restart | starts healthy, data intact | — | PASS |

Redis credentials are masked in logs (`redis://***@cert-redis:6379/0`).

### C-2 detail

| Condition | `docker stop` timeout | Exit code | Runs |
|---|---|---|---|
| No Redis outage | default (10 s) | **0** | 4/4 |
| After Redis outage + recovery | default (10 s) | **137** | 4/4 |
| After Redis outage + recovery | `-t 60` | **0** | 1/1 |

Application-level teardown completes and logs `Finished server process [1]` in
most 137 runs; one run truncated after *"Cancelled 2 background task(s)"*.
Container was **not** OOM-killed (`OOMKilled: false`, `Error: ""`).

**Impact is operational signalling, not integrity.** Orchestrators read a non-zero
exit as abnormal termination: restart-backoff, deployment health and alerting all
key off it, and a genuine crash becomes indistinguishable from a routine stop. No
data was lost in any scenario — the paper balance and all 19 collections survived
every outage and restart intact.

---

## 13. Trading Verification

| Check | Result |
|---|---|
| Trade-engine regression tests | green within the 2,743 (no trading test modified) |
| Paper-trading exploit | **closed** — 15/15 hostile payloads → 422, balance unchanged (§7) |
| Valid BUY debit / SELL / close credit / P&L | **exact** (§7.3) |
| Authorization boundaries | unchanged — 97 protected, 29 admin, all enforced (§8.3) |
| Broker / real-money behaviour | **untouched** — no broker configured, no live order path exercised or altered |
| Cross-user data access | none — paper data scoped to the acting user |

**No financial-integrity issue was discovered.** Trading logic was not redesigned
and no trading code was modified during certification.

---

## 14. Payment Status

**NOT OPERATIONALLY VERIFIED — and, more precisely, not implemented.**

There is **no payment provider integration in the codebase at all**. This is not a
defect that was hidden; it is a gap the code reports honestly:

```
payments_integration() -> {
  'integrated': False, 'provider': None, 'collection': 'db.payments',
  'reason': "The platform has no payment integration: nothing in the codebase
             writes to `db.payments` ... This is not '₹0 of revenue' — it is the
             absence of a revenue source."
}
```

`POST /api/admin/payments/{id}/refund` returns **501** with an explanatory detail
and — importantly — writes **no** audit record. PH3.5 (D-4) found this endpoint
previously returned `{"success": true}` for any string while writing a
`payment.refunded` entry to the immutable audit log; that behaviour is gone.

Payments are **not promoted to PASS**. No provider, no webhook, no verified
payment record, no operational evidence.

---

## 15. Backup / DR Status

**Tooling: PASS (exercised end-to-end). Disaster recovery as an operational
capability: NOT OPERATIONALLY VERIFIED.**

A real backup and a real restore were performed this sprint against the live
authenticated MongoDB — not inspected, executed:

| Step | Result |
|---|---|
| Backup | **exit 0** — `mongo-alpha_stock_cert-20260818T074116Z-daily.archive.gz.enc`, 5.1 KB, in 1 s |
| Encryption | `openssl-aes-256-cbc-pbkdf2-600000` (mandatory in `APP_ENV=production`) |
| Integrity | SHA-256 `49d25d66…e28f4d`; checksum **OK**; structural verify **OK** (decrypted, CRC verified, mongodump archive confirmed) |
| Restore | **exit 0** into `alpha_stock_restored` in 2 s |
| Reconciliation | **19 collections matched exactly, 0 differ** |
| Data spot-check | `users 1 / trades 2 / sessions 2 / security_audit_logs 5` — identical to source; trade documents byte-accurate |

**Why this is still not "DR is operational":** `BACKUP_ROOT` defaults to
`${REPO_ROOT}/backups`, an **on-host** path, and the tooling contains **no
off-host shipping** — no S3, rsync or scp push. The documentation names off-host
storage as a manual operator step. §11 of this gate requires an actual off-host
target to be exercised before backup/DR may be called operational. It has not
been. There is also no scheduled job and no production environment.

---

## 16. Rollback Status

**NOT OPERATIONALLY VERIFIED.**

`scripts/dr/deploy_rollback.sh` exists and is functional — it correctly reported
the absence of a ledger, read the current tag from `.env`, and resolved git state
as `a4ee79f (clean)`. But:

```
WARN  no deployment ledger at .../backups/deployments.tsv
INFO  image : (not running)
```

There is no deployment ledger because there has never been a deployment. A
rollback has therefore never been exercised against a real deployed release. The
script's own documentation is explicit that there is no CD pipeline and no image
registry yet.

---

## 17. Dependency Status

**PASS — exit 0**, and the gate is proven to bite in all three ways §13 requires.

```
python: 7 advisories reported
npm:   16 advisories reported (severities: high, critical)
OK — every advisory is fixed or covered by an unexpired, evidenced register
     entry, and every entry still matches a real finding.
```

Negative tests, each of which actually failed the gate:

| Probe | Expected | Observed |
|---|---|---|
| Suppression expiry (`--today 2030-01-01`) | policy failure | **exit 1** — 7 violations, `EXPIRED [python] starlette — PYSEC-2026-161`, `PYSEC-2026-249` |
| Unmatched / stale register entry (synthetic `PYSEC-9999-99999` injected) | policy failure | **exit 1** — `STALE [python] nonexistent-package-cert-probe` |
| Auditor unavailable (`pip-audit` not installed) | distinct non-zero | **exit 2** — `EXIT_TOOLING`, distinct from `EXIT_POLICY=1` |

Exit semantics are explicit in the script: `EXIT_OK, EXIT_POLICY, EXIT_TOOLING = 0, 1, 2`.

**No advisory was suppressed during certification.** The register was restored
byte-for-byte after the stale-entry probe and the tree re-verified clean.

---

## 18. Secret / Configuration Audit

| Check | Result |
|---|---|
| `.env` tracked in git | **No** — untracked and ignored (`.gitignore:101 *.env`) |
| Real secrets in git | **None** |
| Secrets in Docker image | **None** — gitleaks over `/app`: *no leaks found* |
| Secrets in logs | **0 occurrences** of 7 configured secrets across 71 live log lines, including the user password and the access JWT |
| `secrets/` directory | only `.gitignore`, `README.md`, `generate.sh` tracked |
| Only safe example config tracked | ✅ `*.env.example`, `production.env.example`, `compose.env.example` |

**Repository-wide scans.** gitleaks over the working tree reported 214 raw
findings; scoped to **git-tracked** files only **5** remain, and all five are
synthetic redaction-test fixtures:

| File | Value | Nature |
|---|---|---|
| `backend/tests/_testenv.py:62` | `c3RvY2thc3Npc3Q…` | base64 of `stockassist-ph31-test-fernet-key` |
| `backend/tests/test_log_infrastructure.py:615` | `sk-live-abcdef123456` | asserts the log scrubber **redacts** it |
| `backend/tests/test_observability.py:825` | `eyJhbGciOiJIUzI1NiJ9` | redaction assertion |
| `frontend/src/services/__tests__/telemetry.test.js:221` | `…SECRETJWT.sig` | redaction assertion |
| `scripts/load/env/loadtest.env:72` | `c3RvY2thc3Npc3Q…` | base64 of `stockassist-ph35-load-fernet-key` |

The remaining 209 are in untracked, git-ignored paths (the real local `.env` and
k6 load-test result JSON) and reach neither the repository nor the image. An
independent regex sweep over all 593 tracked files found 3 hits — the same class,
plus the `REPLACE_USER:REPLACE_PASSWORD` placeholder in `production.env.example`.

### Production configuration fails closed — verified by making it fail

This was not asserted from source; the container was **made to refuse to start**,
three times:

| Injected weakness | Result |
|---|---|
| Unauthenticated `MONGO_URL`, passwordless `REDIS_URL`, no AI provider | **startup refused** — *"the process was stopped before startup because required secrets are missing or misconfigured"* |
| Placeholder-shaped `ANTHROPIC_API_KEY` | **startup refused** — *"looks like a placeholder / weak default value"* |
| Valid production configuration | starts, reports healthy |

`APP_ENV=production` is the image default, so a forgotten variable cannot relax
cookie flags, CORS or secret-strength enforcement. `ENABLE_AUTO_LOGIN` appears in
application code **only** as a production rejection
(`security/secrets.py:1317-1318`). Debug/development defaults cannot silently
activate.

---

## 19. Mock / Development-Data Audit

| Check | Result |
|---|---|
| MOCK production analytics | **0 of 53** — provenance distribution `derived 32 / real 4 / unavailable 17` |
| Demo authentication | **none** — 0 hits for `demo_user`, `demo@`, `DEMO_` outside tests |
| Auto-login | **none** — `ENABLE_AUTO_LOGIN` exists only as a production *rejection* |
| Default production admin | **none** — 0 hits for `DEV_ADMIN`, `default_admin` |
| Development auth bypass | **none** — 0 hits for `bypass_auth`, `skip_auth` in application code (38 `skip_auth` hits are in the vendored `anthropic` SDK under `backend/venv/`, which is `.dockerignore`d and never reaches the image) |
| Legacy Emergent auth | **none** — 0 hits |
| Mock OAuth | **none** outside tests asserting rejection |
| Hardcoded credentials / test secrets | none in application code (§18) |
| Fake production trading data | none — market marks come from the live gateway |

`test_ph39_mock_removal.py` + `test_auth_hardening.py`: **86 passed**.

---

## 20. Route Inventory

Generated by introspecting the **running production application**, not from
source inspection.

| Class | Count |
|---|---|
| Total routes in the live table | **202** (201 HTTP + 1 WebSocket) |
| User-protected | **97** |
| Admin | **29** |
| Public | **75** |
| WebSocket | 1 (`/api/ws`) |
| **Documentation routes registered** | **0** |

Classification is by **dependency graph** (`get_current_user` / `require_admin` in
the resolved dependency tree), not by URL shape.

Live probe results:

| Probe | Result |
|---|---|
| `/docs`, `/redoc`, `/openapi.json` anonymous | **404 / 404 / 404** |
| 97 user-protected routes, anonymous | **97/97 denied**; all 44 mutating routes → **401** with the limiter cleared; **0 responses in the 2xx range** |
| 29 admin routes, anonymous | **29/29** → 401/403 |
| 29 admin routes, normal authenticated user | **29/29 → 403** |
| 8 authenticated user routes, valid token | **8/8 → 200** |

An initial sweep returned 429 on 37 protected routes because the rate limiter
fires ahead of authentication. That is still a denial, and after clearing the
limiter every one answered 401. It is recorded here rather than glossed, because
"429 counted as pass" is exactly the kind of ambiguous evidence this gate exists
to reject.

---

## 21. Reproducibility

| Check | Result |
|---|---|
| Git clean | ✅ |
| Release candidate is a commit | ✅ `a4ee79f` |
| Committed source matches image | ✅ **0 content mismatches** over 117 files |
| Image digest recorded | ✅ `sha256:42d12ddf…abf8ce` |
| No local-only patch needed to pass tests | ✅ all suites pass from the clean tree |
| No untracked production **code** | ✅ |
| Image is a pure function of the commit | ❌ **C-1** |

### C-1 — proven by construction

| Build source | Files in `/app` | `test-results/junit.xml` |
|---|---|---|
| Developer working directory (`backend/`) | **117** | **present** |
| Clean `git archive HEAD` of the same commit | **116** | absent |

`backend/test-results/junit.xml` (2,585 bytes) is untracked and git-ignored
(`.gitignore:69 test-results/`), but `backend/.dockerignore` excludes `tests/`,
`test_*.py`, `*_test.py`, `conftest.py` and `pytest.ini` — **not `test-results/`**.
`COPY . .` therefore sweeps it into the image.

Its contents: a pytest JUnit report dated 2026-07-22 recording a deliberately
failing probe test, and the attribute
`hostname="shreyashs-MacBook-Air.local"` — a developer machine name shipped in a
production artifact.

**Severity: low impact, real defect.** The file is inert — never imported, never
executed, and not served by any route (nothing maps static files out of `/app`).
No secret is exposed. But three properties this gate requires are violated:

1. §2 — *"no test-only files accidentally enter production"* fails literally.
2. §16 — the artifact is not reproducible from the commit; two engineers building
   "the same release" get different images.
3. The Dockerfile's own stated guarantee — *"the thing CI tests is bit-for-bit the
   thing production runs"* — does not hold.

**The general class matters more than this one file:** `.dockerignore` does not
mirror `.gitignore`, so *any* host-local ignored artifact under `backend/` enters
the image. Today exactly one exists.

**Why PH3.12R missed it:** its verification checked *"all 107 `.py` files inside
the image"*. A non-`.py` file could not have been caught by that probe — the same
scoping failure that let B-2 survive PH3.11. This certification compared **all**
files, of every type, in both directions.

---

## 22. Scorecard

| Category | Verdict |
|---|---|
| Security | **PASS** |
| Authentication | **PASS** |
| Authorization | **PASS** |
| API validation | **PASS** |
| CORS | **PASS** |
| Cookies | **PASS** |
| JWT / session | **PASS** |
| CSRF | **PASS** |
| Rate limiting | **PASS** |
| OAuth | **PASS** |
| WebSocket security | **PASS** |
| Trading integrity | **PASS** |
| Payments | **NOT OPERATIONALLY VERIFIED** |
| Infrastructure | **PASS** (C-2 withdrawn — see §31.2) |
| Database | **PASS** |
| Redis | **PASS** |
| Observability | **PASS** |
| Logging | **PASS** |
| Dependencies | **PASS** |
| Backup / DR | **NOT OPERATIONALLY VERIFIED** (tooling PASS) |
| Rollback | **NOT OPERATIONALLY VERIFIED** |
| Frontend | **PASS** |
| Build | **PASS** |
| Testing | **PASS** |
| Reproducibility | **PASS** (C-1 closed — see §31.1) |
| Mock / development-data removal | **PASS** |

---

## 23. Remaining Conditions

**Mandatory before deploy:**

| ID | Condition | Action |
|---|---|---|
| **C-1** | Release image contains an untracked host artifact | Add `test-results/` to `backend/.dockerignore`, **rebuild**, and re-verify the image contains exactly 116 files with no image-only entries. The image certified here must **not** be the deployed artifact. |
| **C-2** | Exit 137 on shutdown after a Redis outage | Either fix the lingering shutdown path, or set an explicit stop grace period (`stopGracePeriod` / `terminationGracePeriodSeconds` ≥ 60 s) and document that 137 after a Redis flap is expected, so real crashes stay distinguishable. |

**Operational prerequisites (not blockers, but required before the platform is
run for real):**

| Item | Requirement |
|---|---|
| Backup / DR | Configure and exercise an **off-host** backup target; schedule the job; run a restore drill against it. |
| Rollback | Record a deployment ledger entry at first deploy; exercise one rollback. |
| Payments | Not implemented. Required only if monetization is in scope for this release. |
| Secrets delivery | Move `MONGO_URL`, `JWT_SECRET`, `CSRF_SECRET`, `RECOVERY_SECRET`, `REDIS_URL`, AI keys to **file-based** secrets (`*_FILE` / Docker secrets); the app warns about plaintext env delivery on every start. |
| `BROKER_TOKEN_KEY` | Set a dedicated Fernet key rather than deriving from `JWT_SECRET`. |
| Image provenance | Build with `APP_VERSION` / `VCS_REF` / `BUILD_DATE` set; they are currently `0.0.0-dev` / `unknown` / `unknown`. |

---

## 24. Known Limitations

* **62 ESLint warnings** in the frontend build — exactly the PH3.10 baseline. The
  pipeline reports and caps them but does not fail on them, by documented choice.
* **D-10** — registration does not validate email format; 4 tests are `xfail`
  against it.
* **Two lockfiles tracked** (`package-lock.json` and `yarn.lock`) — C-8, carried
  from PH3.12, still unresolved.
* `TradeCreate.target2` / `target3` remain unconstrained `Optional[float]`, so
  `Infinity` is still admissible there on `/api/trades`. It cannot affect a
  balance (only `entry_price × quantity` is charged) and is out of this gate's
  scope, but it is the same class B-1 closed elsewhere and should be tightened.
* `--no-cache --pull` builds are not bit-reproducible; two builds of one commit
  yield different image IDs. Source-state parity, not image-ID equality, is the
  reproducibility property checked here.
* No production environment exists; **nothing in this document is operational
  evidence**.

---

## 25. Release Decision

### **GO — CONDITIONAL**

Applying the stated rule:

| GO criterion | Result |
|---|---|
| Zero unresolved critical/high **security** findings | ✅ none — C-1 is low-impact and unreachable; C-2 is not a security finding |
| Zero **financial-integrity** blockers | ✅ none — B-1 closed and re-proven; trading arithmetic exact |
| Zero **reproducibility** blockers | ⚠️ **C-1** — source parity is exact (0 mismatches); the defect is one inert, non-executable file |
| Zero **regression failures** | ✅ 2,743 / 452 / 395, all baselines matched exactly |
| Zero known production **authentication bypasses** | ✅ none — 97/97, 29/29, WebSocket 4/4 rejections |
| Production build succeeds | ✅ exit 0 at the project standard |
| Dependency gate passes | ✅ exit 0, proven to bite three ways |

**The committed source at `a4ee79f` is certified production-ready.** All three
prior blockers (B-1, B-2, L-1) are independently confirmed closed with probes that
could have failed and demonstrably did fail under inverted conditions.

**The specific image built during this sprint is not the artifact to deploy.** C-1
means the image carries content that no commit describes and no reviewer approved.
The fix is one line plus a rebuild.

This is recorded as **conditional** rather than **NO-GO** deliberately, and the
reasoning is stated so it can be disputed: C-1 contaminates the artifact in one
direction only — a clean-checkout build produces a *strictly cleaner* image that is
functionally identical, and every application file in the shipped image already
matches the commit byte-for-byte. No functional, security or financial gate failed.
Blocking the release outright over an inert 2.5 KB file would misrepresent the
risk; shipping it unremarked would be negligent. It is therefore a **gating
pre-deploy condition**, not a certification failure.

**If the release policy requires that the certified image be the deployed image
byte-for-byte, then C-1 is a blocker and this verdict becomes NO-GO** until the
rebuild is done. That call belongs to the release owner, and the evidence for it
is in §21.

Nothing was deployed. No remediation was performed during certification. PH3.13
was not started.

---

## 26. Evidence / Commands

```bash
# Baseline
git rev-parse HEAD && git status --porcelain

# Fresh release artifact
docker build --no-cache --pull -f backend/Dockerfile -t stockassist-rc:ph312-cert backend
docker inspect stockassist-rc:ph312-cert --format '{{.Id}}'

# Reproducibility control
mkdir -p clean1 && git archive --format=tar HEAD | tar -x -C clean1
docker build --pull -f clean1/backend/Dockerfile -t stockassist-rc:ph312-clean clean1/backend
docker run --rm --entrypoint /bin/sh stockassist-rc:ph312-clean -c 'find /app -type f ! -name "*.pyc" | wc -l'

# Regression
cd backend && ./venv/bin/python -m pytest -q                 # 2743 passed
cd backend && ./venv/bin/python -m pytest -q -m security     # 452 passed
cd frontend && CI=true npx craco test --watchAll=false       # 395 passed
cd frontend && npx craco build                               # exit 0, 62 warnings

# Dependency gate (+ negative tests)
python .github/scripts/dependency_audit.py --ecosystem all              # exit 0
python .github/scripts/dependency_audit.py --ecosystem python --today 2030-01-01   # exit 1 EXPIRED
python .github/scripts/dependency_audit.py --ecosystem python           # exit 1 STALE (synthetic entry)

# B-2, both directions
curl -s -o /dev/null -w '%{http_code}' http://localhost:18000/docs        # 404 (APP_ENV=production)
curl -s -o /dev/null -w '%{http_code}' http://localhost:18001/docs        # 200 (APP_ENV=development)

# B-1
curl -X POST .../api/paper/trade -d '{"symbol":"RELIANCE","quantity":-1000,...}'   # 422, balance unchanged

# Secrets
gitleaks detect --no-git --source . --redact
docker export <container> | ... && gitleaks detect --no-git --source imgfs/app     # no leaks found

# Backup / restore drill
./scripts/backup/backup_mongo.sh --tier daily      # exit 0, encrypted, verified
./scripts/backup/restore_mongo.sh <artifact> --target-db alpha_stock_restored --yes
# → 19 collections matched exactly, 0 differ
```

---

## 27. Deviations

* **Frontend build run twice.** First under `CI=true` (exit 1, warnings-as-errors),
  then at the project standard (exit 0). Both are reported; the project standard is
  the verdict. The stricter run is disclosed rather than discarded.
* **`.github/dependency-triage.yml` was temporarily modified** to inject a synthetic
  stale entry and prove the check bites. Restored immediately; tree verified clean.
* **A socat proxy container** was used to expose the cert MongoDB to the host for
  the `BACKUP_MODE=direct` drill, because `BACKUP_MODE=docker` targets the compose
  stack, which was not used here.
* **AI provider key was synthetic.** No real AI provider call was made; the key
  satisfies the fail-closed config validator only.
* **Rate limiter state was cleared** between authorization sweeps so that 401
  evidence was not masked by 429. Both the masked and unmasked results are reported.
* **Payments, backup off-host, and rollback were not operationally exercised** —
  there is no production environment, no provider, and no off-host target.

---

## 28. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| C-1 — unreviewed content in the release artifact | **Medium** | One-line `.dockerignore` fix + rebuild; verify 116 files |
| C-2 — 137 exit masks real crashes from orchestrators | **Medium** | Grace period ≥ 60 s, or fix the shutdown path |
| No off-host backup | **High (operational)** | Configure and drill an off-host target before go-live |
| Rollback never exercised | **High (operational)** | Record ledger at first deploy; drill a rollback |
| Secrets delivered as plaintext env vars | **Medium** | Switch to `*_FILE` / Docker secrets |
| `BROKER_TOKEN_KEY` derived from `JWT_SECRET` | **Medium** | Set a dedicated Fernet key; rotating JWT_SECRET would otherwise orphan broker tokens |
| Image provenance is `0.0.0-dev` / `unknown` | **Low** | Pass `APP_VERSION` / `VCS_REF` / `BUILD_DATE` at build |
| Two lockfiles (C-8) | **Low** | Choose one package manager |

---

## 29. Rollback Reference

* Tooling: `scripts/dr/deploy_rollback.sh` — `record` / `list` / `current` / `rollback`
* Ledger: `${BACKUP_ROOT}/deployments.tsv` — **does not yet exist**
* Verification: `scripts/dr/dr_verify.sh`
* Restore: `scripts/backup/restore_mongo.sh <artifact> [--target-db X] [--drop] [--yes]`
  — default is **non-destructive merge**; `--drop` is required to replace.
* Runbook: `docs/operations/BACKUP_AND_RESTORE.md`, `docs/runbooks/`
* Data restore is **proven working** (§15). Deployment rollback is **not** (§16).

---

## 30. Final Certification Statement

The release candidate at commit `a4ee79f3e8ba5f689e265a8c804c9e9674717173` on
`main`, with a clean working tree, has been independently re-certified against a
production image built from scratch during this sprint.

**Code correctness:** verified. 2,743 backend, 452 security and 395 frontend tests
pass, matching the PH3.12R baseline exactly, with no test weakened, skipped or
suppressed.

**Security correctness:** verified. All three prior blockers are closed, confirmed
with probes constructed so they could fail — and which did fail under inverted
conditions. Seventeen PH1 controls were re-probed live against the production
binary; none regressed. The WebSocket attack matrix is fully rejected. Zero
authentication or authorization bypasses exist across 202 routes.

**Infrastructure correctness:** verified with one condition. Redis loss degrades
gracefully, Mongo loss flips readiness while liveness holds, both recover
automatically with zero restarts, and shutdown is clean — except that a shutdown
following a Redis outage terminates with exit 137 (C-2).

**Operational readiness:** **not established, and not claimed.** No production
environment exists. Payments are not implemented. Backup tooling works end-to-end
but has no off-host target. Rollback has never been exercised.

**Production certification: GO — CONDITIONAL.** The source is production-ready.
The image built here must be rebuilt after the C-1 fix before deployment, and the
operational prerequisites in §23 are a precondition for running the platform for
real — not for accepting this commit.

No code was modified. No blocker was silently repaired. No deployment was
performed. No new sprint was started.

---

*Certified 2026-08-18 against commit `a4ee79f`, image `sha256:42d12ddf…abf8ce`.*

---

# 31. PH3.12C — Conditional Remediation Addendum (2026-08-18)

Scope: close C-1 and investigate C-2. No product feature was added, no unrelated
code touched, no test weakened, no exit code masked. PH3.13 was not started.

## 31.1 C-1 — CLOSED

**Fix:** `backend/.dockerignore` — the block now excludes test *outputs*, not only
test *inputs*.

An important detail was established empirically before the fix was written, and
it changed the fix: **a `.dockerignore` pattern is anchored to the build-context
root.** A probe context built against Docker 29.4.0 showed that with a bare
`test-results/` rule, `sub/test-results/junit.xml` and `sub/junit.xml` were both
still copied into the image; only the `**/`-prefixed form excluded them. Every
rule is therefore written twice — bare *and* `**/`-prefixed — because `**/foo`
does not match a root-level `foo`, so dropping either form reopens the hole. The
same depth-independent twins were added for the generated tool artifacts already
listed in the caches block (`.pytest_cache/`, `.coverage`, `htmlcov/`, `.tox/`,
`.mypy_cache/`, …), which had the identical root-anchoring gap.

**Regression guard:** `backend/tests/test_build_context.py`, **44 tests**,
hermetic (parses `.dockerignore`; never invokes Docker), so it runs in the
default suite on every push.

The guard was proven able to fail before it was trusted — the mistake that let
C-1 and B-2 survive earlier sprints:

| `.dockerignore` under test | Result |
|---|---|
| `HEAD` (pre-fix) | **26 failed**, 18 passed — including the exact C-1 path |
| Remediated | **44 passed** |

It also asserts the guard does not over-reach: `server.py`, `models.py`,
`requirements.txt`, `security/api_docs.py`, `docker/entrypoint.sh` and peers must
remain *included*, because an over-broad exclusion is an outage rather than a
hardening win.

**Proof the image is now a function of the source.** Both images built
`--no-cache --pull`; the offending `backend/test-results/junit.xml` and
`backend/.coverage` were deliberately **left on disk** so the test could not pass
vacuously:

| Build | Files in `/app` |
|---|---|
| A — from the working directory (untracked artifacts present) | **116** |
| B — from a clean `git archive` export | **116** |
| `diff A B` | **identical, 0 differences** |
| Previously certified image | 117 (`test-results/junit.xml` present) |

| Check | Result |
|---|---|
| Content mismatches vs source of record | **0** |
| Files in image but not in source of record | **0** |
| `/app/test-results`, `/app/test_reports`, `/app/.coverage`, `/app/htmlcov`, `/app/.pytest_cache` | **all absent** |
| Any `junit*.xml` / `coverage.xml` / `report.xml` under `/app` | **none** |
| Remediated image | `stockassist-rc:ph312-c1fix`, 425 MB, `sha256:cdfcd0b3d2b77ea47344ac7bad7370d6e0a7835c71ac20a6aa4f07eee0a9af03` |

**C-1 status: CLOSED.**

## 31.2 C-2 — WITHDRAWN: the finding was wrong

**No application defect exists, and no application code was changed.**

The certification reported "the container exits 137 on graceful shutdown after a
Redis outage-and-recovery," reproduced 4/4. The reproduction was real; the
*attribution* was not.

**What the investigation found.** A control container was built — 20 lines of
Python, a textbook `SIGTERM` handler, a 1.2 s simulated teardown, no Redis, no
asyncio, no application code of any kind:

| Control container | Result |
|---|---|
| `docker stop` (no `-t`) | **exit 137 in 6/6 runs**, "clean exit" never logged |
| `docker stop -t 10` / `-t 30` / `-t 60` | **exit 0 in 3/3**, clean exit logged |
| Teardown shortened to 0.2 s, `docker stop` (no `-t`) | **exit 0 in 3/3** |

A container with no application code reproduces the finding exactly. On this host
(Docker Desktop 29.4.0, macOS), **`docker stop` without an explicit `--timeout`
SIGKILLs the container roughly 1.3 s after SIGTERM**, well short of the documented
10 s grace period. Passing *any* explicit `-t` — including `-t 10`, the same value
as the default — restores correct behaviour.

**Why the Redis correlation looked convincing, and why it was spurious.** The
application's teardown takes **1.5–2.3 s**, which straddles that ~1.3 s kill
window. A Redis outage lengthens teardown slightly (an extra pool close and
pub/sub unsubscribe), pushing it over the edge more often. The certification
compounded this by using bare `docker stop` in the flap runs and an explicit
`-t 30`/`-t 60` in the baseline runs — so **every 137 came from a bare
`docker stop` and every 0 from an explicit `-t`.** The variable under test was
never Redis.

**The application's actual shutdown contract, measured by sending SIGTERM
directly and bypassing `docker stop` entirely:**

| Condition | SIGTERM → exit | Exit code | Teardown complete | asyncio warnings |
|---|---|---|---|---|
| No Redis outage | 1.94 s | **0** | ✅ | 0 |
| After Redis outage | 1.47 s | **0** | ✅ | 0 |
| After Redis outage | 2.28 s | **0** | ✅ | 0 |

Ordered teardown every time: draining → heartbeat stopped → background tasks
cancelled → pub/sub stopped → Redis client closed → HTTP pools closed →
*Application shutdown complete* → *Finished server process [1]*. Zero
`Task was destroyed but it is pending`, zero unretrieved-exception and zero
never-awaited-coroutine warnings, so the fire-and-forget pool-reset task in
`infrastructure/redis_client.py` is not leaking either.

**Why nothing was changed.** The instruction was to implement the smallest
production-safe lifecycle fix for the process, task or resource causing the
SIGKILL. There is none: the SIGKILL originates in the host's `docker stop`
client behaviour, not in the application, and the application already exits 0
with a complete teardown on the signal an orchestrator actually sends. Adding a
lifecycle change here would be modifying production code to chase a test-harness
artifact. Masking the exit code was never an option and was not done.

**C-2 status: WITHDRAWN — not a defect. No code change.**

**Operational note (unchanged advice, different reason):** still set an explicit
termination grace period in the deployment manifest
(`stop_grace_period` / `terminationGracePeriodSeconds`, ≥ 30 s). Not to hide a
defect, but because the app's 1.5–2.3 s teardown deserves a stated budget, and
because operators reproducing shutdown behaviour should always pass an explicit
`-t` rather than trust the CLI default.

## 31.3 Verification after remediation

| Check | Result |
|---|---|
| `test_build_context.py` (new) | **44 passed** (26 failed pre-fix) |
| Targeted regression (build-context, paper-trade, api-docs, recovery, observability) | **379 passed** |
| B-2 on remediated image — `/docs`, `/redoc`, `/openapi.json` | **404 / 404 / 404** |
| B-1 on remediated image — `quantity=-1000` | **422**, balance `100000.0` → `100000.0` |
| Valid BUY on remediated image | **200**, balance `100000.0` → **`90000.0`** |
| Image parity (working dir vs clean checkout) | **identical, 116 = 116** |

## 31.4 Files changed

| File | Change |
|---|---|
| `backend/.dockerignore` | C-1 fix — exclude test/CI result artifacts, root-anchored **and** `**/`-prefixed |
| `backend/tests/test_build_context.py` | **new** — 44-test hermetic regression guard |

No application module, dependency, workflow or configuration file was modified.

## 31.5 Effect on the release decision

C-1 is closed and C-2 is withdrawn, so **both conditions attached to the §25
verdict are discharged** and the decision becomes an unconditional **GO** on
those grounds.

**One new condition replaces them, and it is procedural:** the remediation is
**uncommitted**. The working tree is no longer clean, so L-1's property — "the
release candidate is a commit anyone can check out" — does not currently hold.
The two files above must be committed and the image rebuilt from that commit
before deploy.

The operational items are unchanged and still gate go-live rather than
certification: payments **not implemented**, backup/DR has **no off-host target**,
rollback has **no deployment ledger**.

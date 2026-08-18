# StockAssist AI — Release Test Protocol

**Status:** Executed end-to-end in PH3.11 (2026-08-17) and again after the
PH3.11 dependency remediation.
**Owner:** Release engineering
**Evidence of the last full run:** `docs/production/PH3.11_RELEASE_CANDIDATE_REPORT.md`
and `docs/production/PH3.11_REMEDIATION_REPORT.md`

> **What this document is.** The repeatable pre-release verification procedure,
> written down so release verification does not depend on anyone's memory. Every
> step below has been executed at least once; nothing here is aspirational. Where
> a step could not be executed in this environment, it says so and says why,
> rather than being quietly omitted.

---

## 0. Entry criteria

Do not start until all of these hold:

| # | Criterion |
|---|---|
| E-1 | The candidate commit is on `main` and the working tree is clean |
| E-2 | The preceding phase's audit report exists and recommends proceeding |
| E-3 | Docker is running and has ~5 GB free for a no-cache image build |
| E-4 | No unrelated work is in flight — this is a freeze protocol |

**Freeze rule.** During execution the only permitted code changes are: confirmed
regressions, release-blocking defects, deterministic test/environment fixes that
do not hide failures, and documentation corrections caused by verified
implementation changes. Every change must be traceable to a finding.

---

## 1. Record the release-candidate baseline

```bash
git rev-parse HEAD && git branch --show-current && git status --porcelain
backend/venv/bin/python --version ; node --version ; npm --version
docker --version ; docker compose version
grep -E "image:\s*(mongo|redis)" docker-compose.yml
```

Record: commit, branch, tree status, backend/frontend versions, Python, Node,
Docker, Compose, MongoDB, Redis, dependency lock state, environment contract.

**Every difference from the previous baseline must be explained before
proceeding.** A number that moved for an unknown reason is a finding.

Two deltas that recur and are *not* findings: the host Python may differ from the
image Python (only the image ships), and Mongo collection/index counts vary with
which endpoints the previous run exercised, because collections are created
lazily. Verify the *declared* index set instead:

```bash
awk 'NR>=6546 && NR<=6790' backend/server.py | grep -oE 'db\.[a-z_]+\.create_index' | sort -u | wc -l
```

---

## 2. Test inventory

```bash
cd backend
venv/bin/python -m pytest --collect-only -q -m "" | tail -1        # total collected
for m in security integration live e2e slow requires_db requires_redis allow_network; do
  venv/bin/python -m pytest -m "$m" --collect-only -q | tail -1
done
```

Classify every suite and reconcile the arithmetic:
`passed + deselected + xfailed = total collected`. An unreconciled total means a
test disappeared.

**Confirm nothing is suppressed:** no new `skip`, no new `xfail`, no weakened
assertion, no deleted test. The `-m "not integration"` default is a documented
selection boundary, not a suppression.

---

## 3. Backend regression

```bash
cd backend && venv/bin/python -m pytest -q
cd backend && venv/bin/python -m pytest -m security -q
```

Covers authentication, OAuth, cookies, CORS, CSRF, JWT, sessions, rate limiting,
password policy, email verification, authorization, roles, identifiers, API
validation, WebSockets, trading engine, market data, AI, analytics, payments,
notifications, background workers, Redis and MongoDB.

---

## 4. Frontend regression

```bash
cd frontend && npm ci --legacy-peer-deps
cd frontend && CI=true npx craco test --watchAll=false
cd frontend && REACT_APP_BACKEND_URL="https://ci.invalid" npm run build
ls frontend/build/static/js/*.js | wc -l ; du -sh frontend/build
```

`--legacy-peer-deps` is required: the project pins eslint 9 while
`eslint-config-react-app@7.0.1` peer-requires eslint 8. Bare `npm ci` fails with
ERESOLVE on this tree — that is expected, not a finding.

The build must exit 0 and emit the expected bundle count. Also scan the output:

```bash
grep -roEI "(sk-ant-api03-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|mongodb(\+srv)?://[^\"' ]+)" frontend/build/
```

---

## 5. Dependency and supply-chain gate

```bash
python .github/scripts/dependency_audit.py --ecosystem all
```

Exit 0 clean · 1 policy violation · 2 the audit could not run. **Exit 2 must
never be treated as a pass** — it means the check did not happen.

Verify the gate still bites (see §12 for the full negative-test set). At minimum
confirm the register has no entry expiring inside 30 days that nobody has
re-argued.

---

## 6. Authorization surface

```bash
cd backend && APP_ENV=development MONGO_URL="mongodb://ci:ci@127.0.0.1:27017" \
  DB_NAME=ci JWT_SECRET="ci-import-check-only-not-a-real-secret-000000000000" \
  PYTHON_DOTENV_DISABLED=1 venv/bin/python -c \
  "from tests._routes import *; print(len(USER_PROTECTED_ROUTES), len(ADMIN_ROUTES), len(PUBLIC_ROUTES))"
```

Classifies every `/api` route by resolved dependency graph. **A change in these
three numbers without a corresponding intentional change is a regression** — a
deleted guard moves a route from protected to public silently otherwise.

Known limit: the sweep walks `APIRoute` only, so WebSocket routes are invisible
to it. That gap is why §9 exists as a separate step.

---

## 7. Build the release-candidate image from scratch

```bash
docker build --no-cache --pull -f backend/Dockerfile -t stockassist-rc:<tag> backend
docker inspect stockassist-rc:<tag> --format 'User={{.Config.User}}'
docker run --rm --entrypoint sh stockassist-rc:<tag> -c \
  'command -v pip || echo "pip ABSENT"; id; find /app -maxdepth 2 -name "*.env"'
```

`--no-cache --pull` is mandatory: reusing local layers can hide a dependency that
no longer installs. Verify non-root uid, absent pip, no `.env` baked, no
`--reload` in the entrypoint.

---

## 8. Boot the stack and run the live journeys

```bash
docker network create rcnet
docker run -d --name rc-mongo --network rcnet -e MONGO_INITDB_ROOT_USERNAME=root \
  -e MONGO_INITDB_ROOT_PASSWORD=<pw> mongo:7.0
docker run -d --name rc-redis --network rcnet redis:7.2-alpine redis-server --requirepass <pw>
docker run -d --name rc-backend --network rcnet -p 18011:8000 -e APP_ENV=production \
  -e MONGO_URL=... -e REDIS_URL=... -e JWT_SECRET=... -e CSRF_SECRET=... \
  -e RECOVERY_SECRET=... -e FRONTEND_URL=... -e CORS_ALLOWED_ORIGINS=... \
  stockassist-rc:<tag>
```

**Authentication journey** — register → login → authenticated request → refresh →
replay the consumed refresh token → logout → logout-all.

Required outcomes:

| Step | Expected |
|---|---|
| Refresh | 200, refresh token **value changes** (rotation) |
| Replay of the consumed token | **401** |
| The rotated token after a replay | **401** — the family is revoked |
| Logout-all from one of N sessions | all N refreshes 401, **including the caller's** |
| Wrong password / unknown user / malformed JWT | 401 / 401 / 401 |
| Access-token lifetime | exactly 900 s |
| `access_token` + `refresh_token` cookies | HttpOnly **and** Secure |

> **Two traps that produce false findings here, both hit during PH3.11.**
> (1) Refreshing with `curl -b jar` but no `-c jar` does not save the rotated
> cookie, so the *next* call replays a consumed token and returns 401 for a
> reason that has nothing to do with what you are testing. (2) A cookie-
> authenticated `POST` without `X-CSRF-Token` is rejected 403, so the state
> change you think you triggered never happened. Always re-run a suspicious
> result with fresh sessions and a proper CSRF header before reporting it.

**Authorization matrix** — for each of anon / user / admin, confirm
`/api/admin/*` returns 401 / 403 / 200, and that user A cannot read user B's
resources. Then confirm malformed identifiers (`notanobjectid`, a valid-shaped
but absent ObjectId, `%20`, `../../etc/passwd`, `$ne`) all return **4xx, never
5xx**.

**Security posture** — 6 security headers present, no `Server:` header, no ACAO
for a disallowed origin, correct ACAO for an allowed one, OpenAPI 404 in
production, CSRF 403 on both missing and forged tokens, and rate limiting
observed (`401×5 → 429`).

---

## 9. WebSocket attack matrix

The credential marker is `stockassist.auth`. Required outcomes:

| Attempt | Expected |
|---|---|
| Anonymous, no credential | **403** |
| Spoofed `?user_id=<victim>` (the original P0) | **403** |
| Token in the query string | **403** |
| Forged token via subprotocol | **403** |
| Valid token via `Sec-WebSocket-Protocol: stockassist.auth,<token>` | **CONNECTED** |
| Valid token via cookie | **CONNECTED** |
| Valid token **+** spoofed `?user_id=` | **CONNECTED as the token's subject** |

Then hold two users' sockets concurrently and drain both: **zero** foreign-user
identifiers may appear in either stream.

---

## 10. Resource, fault-injection and shutdown checks

**Churn:** 40 sequential connect/disconnect cycles plus 20 concurrent sockets,
then read the gauges from `/api/metrics` (needs `METRICS_TOKEN`;
without it the endpoint correctly fails closed with 403).
`websocket_connections`, `websocket_tracked_users`,
`websocket_channel_subscriptions`, every `app_cache_entries` and
`redis_pool_connections{in_use}` must all return to **0**, with
`background_tasks_running` unchanged.

**Fault injection:**

| Injection | Expected |
|---|---|
| `docker stop` Redis | process alive, readiness 200 with `redis: fail, critical:false`, API still serving, 0 restarts |
| `docker stop` Mongo | `/health/live` 200, `/health/ready` **503** (`critical:true`), errors leak nothing, 0 restarts |
| Restart either | automatic recovery |

The container healthcheck deliberately targets `/api`, not `/health/ready` — a
readiness failure must drain traffic, not restart the container. A container that
stays "healthy" while Mongo is down is correct behaviour.

**Graceful shutdown:** `docker stop --timeout 30` must complete in seconds with
**exit code 0**, and the log must show readiness draining *first*, then every
background task stopped, then Redis pub/sub, Redis client, HTTP pools and the
Mongo client closed.

**Log leakage:** with both streams captured (`docker logs c > f 2>&1` — note the
order; `2>&1 >f` sends stderr to the terminal and silently measures half the
output), grep for every configured secret. Expected count: **0**.

---

## 11. Data-integrity checks

**Analytics provenance** — no metric may be fabricated:

```bash
cd backend && venv/bin/python -c "from analytics import registry; print(registry.summary())"
# mock must be 0
```

**Market data** — a known symbol returns real provider data with a `source`
field; an unknown symbol returns **404**, never a fabricated quote.

**Trading engine mutation check** — a green test proves nothing until you have
watched it go red. Inject a spurious key into `run_cycle`'s return contract in
`services/trading_engine.py`, confirm
`test_run_cycle_trails_and_books_targets` **fails**, then revert and confirm
`git diff` is clean and the suite is green again.

---

## 12. Gate negative tests

The supply-chain gate is itself verified, because a gate nobody tests is a gate
nobody can trust:

| # | Mutation | Expected |
|---|---|---|
| 1 | Run with a date past an entry's `expires` | **exit 1**, `EXPIRED` |
| 2 | Run on the expiry date exactly | exit 0 (valid through the stated day) |
| 3 | Run inside the 30-day window | exit 0 **with** an `EXPIRING` warning |
| 4 | Delete a register entry | **exit 1**, `UNTRIAGED` |
| 5 | Add an entry matching nothing | **exit 1**, `STALE` |
| 6 | Downgrade a runtime pin to a vulnerable version | **exit 1**, `UNTRIAGED` |
| 7 | Remove an npm `override` and regenerate the lockfile | **exit 1**, `UNTRIAGED` |

**Every mutation must be reverted and the revert verified** — `git diff` clean,
and the gate green again — before moving on.

---

## 13. Failure classification

Every failure observed during a run must be classified as exactly one of:

| Class | Meaning |
|---|---|
| **A** | New regression |
| **B** | Pre-existing bug |
| **C** | Environmental |
| **D** | Dependency / tooling |
| **E** | Intentional counter-test |
| **F** | Documentation / expectation mismatch |

For every **A**: reproduce, find the cause, fix it if safe, add a regression
test, re-run the affected suite.

**Never** delete a test, weaken an assertion, skip, xfail, or hide an error to
obtain a green result.

---

## 14. Exit criteria

| # | Criterion |
|---|---|
| X-1 | Baseline recorded and every delta explained |
| X-2 | Backend and frontend suites green; totals reconcile |
| X-3 | Production build exits 0 with expected artifacts |
| X-4 | Supply-chain gate exits 0, and its negative tests pass |
| X-5 | Authorization surface unchanged (or changes intended and reviewed) |
| X-6 | Auth, authorization and WebSocket journeys verified against a live production container |
| X-7 | Fault injection controlled; 0 restarts; clean shutdown exit 0 |
| X-8 | Resource gauges return to baseline after churn |
| X-9 | Analytics provenance shows 0 MOCK |
| X-10 | Every failure classified; no unexplained class-A regression |
| X-11 | Report written with an unambiguous verdict |

The verdict is exactly one of **READY** or **BLOCKED**. Phrases like "mostly
ready" or "should be fine" are not verdicts.

---

## 15. Known limitations of this protocol

Stated so they are not mistaken for coverage:

* **No staging environment.** The live journeys run against a locally-hosted
  production-mode container. That is *not* production verification and must
  never be labelled as such.
* **No SMTP, OAuth or payment provider provisioned** — those live paths are
  covered hermetically only. Email delivery is simulated.
* **No multi-day soak and no load run** in the standard pass; the PH3.4/PH3.5
  harnesses exist and require a provisioned environment.
* **No browser device matrix.**
* **Single-process topology only** — the platform supports exactly one backend
  process until scheduler leader election ships.

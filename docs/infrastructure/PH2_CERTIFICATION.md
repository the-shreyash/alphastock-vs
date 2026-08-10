# StockAssist AI — PH2 Infrastructure Certification

**Sprint:** PH2.12 — Infrastructure Certification & Release Readiness
**Phase:** PH2 — Deployment & Infrastructure
**Date:** 2026-08-09
**Certified commit:** `04e4f57` (plus the four in-sprint remediations listed in §24)
**Certifier:** Principal Platform Engineer (PH2.12)
**Supersedes:** nothing. This is the first infrastructure certification.

---

## 1. Executive Summary

PH2.1–PH2.11 built the infrastructure. This sprint asked one question about it:
**does it actually work, on a real machine, when nobody is being generous?**

That question could not be asked before now. Every PH2 sprint from PH2.7 onward
recorded the same limitation — *no Docker daemon in the sprint environment* — so
the container stack, the compose topology, the backup transport, the DR verifier
and the rollback script were all shipped on the strength of hermetic tests and
careful reading. This sprint had a working Docker daemon (29.4.0) for the first
time, and so ran the certification against a live stack: images built, containers
started, dependencies killed and revived, a database destroyed and restored, and
a bad release deployed and rolled back.

**The infrastructure is substantially better than its documentation claimed in
some places and materially broken in others.** Both halves matter.

What proved genuinely strong, with evidence rather than assertion: the image
(423 MB, non-root, no pip, no secrets, read-only source tree), the compose
topology (internal data network, no published database ports, authenticated
datastores), configuration validation that fails closed on eleven distinct
misconfigurations, structured logging with zero secret leakage measured against
real live secrets, a liveness/readiness split that is textbook-correct under
dependency failure, and a backup/restore path that recovered a deliberately
destroyed database in under a second.

What proved broken: **the deployment rollback did not roll back.** It rewrote the
configuration file, ran compose, recreated nothing, and reported
`rollback verified` while the release being rolled away from continued to serve
traffic. This is the worst available failure shape for a recovery tool — it fails
silently, in the direction of false confidence, during an incident. It was found
only because a live daemon was available; the hermetic suite stubs `docker`, so
the Compose variable-precedence rule the defect lives in was never executed. Two
further defects of the same family — a probe that could never pass, and a
blocking CI gate that has been red on every run since PH2.4 — were found the same
way.

All four in-sprint defects are fixed, verified against the live stack, and
covered by regression tests that fail without the fix.

**The residual gaps are real and none of them are infrastructure defects.** They
are unbuilt things: no continuous deployment, no image registry, no off-host
backup copy, no frontend production image, no alerting. Detection of an incident
is entirely manual, which — per PH2.10's own RTO decomposition — is the dominant
term in recovery time and therefore the highest-value remaining work.

---

## 2. Certification Decision

> ## **CONDITIONALLY CERTIFIED**

PH2 infrastructure is certified for a **single-host production deployment**,
conditional on the six Required Actions in §24 being completed before real user
traffic and real user money are involved.

**Basis for the decision:**

| Criterion | Result |
|---|---|
| Unresolved **Critical** findings | **0** — the one Critical (C1) was found, fixed and verified in-sprint |
| Unresolved **High** findings | **5** — all explicitly accepted with documented mitigation (§19), none of which is a defect in what PH2 built |
| Mandatory production controls | **PASS** — see the matrix in §4 |
| Deployment executed end to end | **YES** — fresh environment → healthy stack → smoke tests → shutdown → restart → recovery |
| Rollback executed and verified | **YES** — after the C1 fix; independently confirmed at the application layer |
| Backup restore drilled | **YES** — destructive test, full recovery |
| PH1 security regression | **PASS** — no control regressed under PH2 infrastructure |

**Why not CERTIFIED:** five High findings remain open. Three of them (H4, H5, H7)
are things that were never built rather than things that are broken, and one (H6)
is a known carry-forward that leaves an entire DR runbook unexecutable. Calling
this fully certified would require either building them or pretending they do not
matter. Neither is honest.

**Why not NOT CERTIFIED:** there is no unresolved Critical finding, every
mandatory control passes under live test, and each open High has a written,
specific mitigation and owner. The stack can be run in production today by an
operator who has read §24 and accepted the constraints in §21.

---

## 3. Infrastructure Architecture (as certified)

```
                        ┌───────────────────────────────────┐
                        │  host                             │
                        │                                   │
   operator ──────────▶ │  127.0.0.1:8000 ──┐               │
                        │                   │               │
                        │  ┌────────────────┼─────────────┐ │
                        │  │ network: edge  │  bridge     │ │
                        │  │                ▼             │ │
                        │  │        ┌───────────────┐     │ │  egress ──▶ Anthropic
                        │  │        │   backend     │     │ │             Gemini
                        │  │        │  uid 10001    │     │ │             Yahoo
                        │  │        │  423 MB       │     │ │             brokers
                        │  │        └───────┬───────┘     │ │
                        │  └────────────────┼─────────────┘ │
                        │                   │  (sole member │
                        │  ┌────────────────┼───of both)────┐│
                        │  │ network: data  │ internal:true ││
                        │  │  NO EGRESS, NO INGRESS         ││
                        │  │      ┌─────────┴────────┐      ││
                        │  │      ▼                  ▼      ││
                        │  │  ┌────────┐       ┌─────────┐  ││
                        │  │  │ mongo  │       │  redis  │  ││
                        │  │  │ authed │       │ authed  │  ││
                        │  │  │ no port│       │ no port │  ││
                        │  │  └───┬────┘       └────┬────┘  ││
                        │  └──────┼─────────────────┼───────┘│
                        │         ▼                 ▼        │
                        │   mongo_data         redis_data    │
                        │   mongo_config       backend_logs  │
                        └───────────────────────────────────┘
                                  │
                                  ▼
                    scripts/backup ──▶ AES-256 encrypted archives
                    scripts/dr     ──▶ verify · rollback · ledger
```

**Configuration is split across two files by audience, and this was verified to
be a real boundary rather than bookkeeping:**

| File | Read by | Contains | Verified |
|---|---|---|---|
| `.env` | Docker Compose (interpolation) | infra credentials, host ports, image tags | Mongo root password never reaches the backend container |
| `production.env` | the backend container (`env_file`) | application secrets | injected as a file; never enumerated in compose |

---

## 4. Certification Matrix

Legend — **Result**: PASS / FAIL / PARTIAL / N/A. **Evidence** is the command run
during this sprint, not a document reference.

### 4.1 Docker

| Component | Requirement | Implementation | Verification | Result | Evidence | Risk |
|---|---|---|---|---|---|---|
| Production image | Builds reproducibly | `backend/Dockerfile`, two-stage | `docker build` cold + warm | **PASS** | cold build OK; warm rebuild **13.6 s** | Low |
| Image size | Minimised | dep prune (PH2.8) | `docker images` | **PASS** | **423 MB** (was 1.03 GB, −59%) | Low |
| Non-root | Unprivileged runtime | uid/gid 10001 | `docker exec … id` | **PASS** | `uid=10001(appuser)` | Low |
| Immutable source | App cannot rewrite itself | `COPY --chown=root:root` | `touch /app/pwned.py` | **PASS** | `DENIED — /app not writable` | Low |
| No dev server | No `--reload`, no debug | entrypoint | repo-wide grep | **PASS** | zero hits outside comments | Low |
| No debug mode | `DEBUG` inert | — | grep + live boot | **PASS** | `DEBUG=true` read by nothing | Low |
| Post-exploit tooling | pip removed | both stages | `command -v pip` | **PASS** | `none — pip removed` | Low |
| Healthcheck | Works | `docker/healthcheck.sh` | container health | **PASS** | healthy in **8 s** | Low |
| Secrets in image | None | `.dockerignore` | layer history + FS grep for the live JWT | **PASS** | 0 hits, no `.env` in image | Low |
| Graceful shutdown | Clean drain | `exec` + `stop_grace_period` | `compose stop` | **PASS** | **exit 0**, drain logged | Low |
| Restart | Recovers | `unless-stopped` | stop → up | **PASS** | healthy in **13 s** | Low |
| Provenance | Build readable at runtime | OCI labels + ENV | `/api/diagnostics` | **PASS** | `2.12.0-cert / 04e4f57` | Low |
| `no-new-privileges` | Applied | compose anchor | `docker inspect` | **PASS** | `[no-new-privileges:true]` | Low |
| Read-only rootfs | Optional hardening | documented, off | — | **PARTIAL** | deliberately deferred (yfinance cache writes) | Medium |

### 4.2 Compose

| Component | Requirement | Verification | Result | Evidence | Risk |
|---|---|---|---|---|---|
| All services start | 3 services healthy | `compose up -d` | **PASS** | all healthy, **8 s** from fresh volumes | Low |
| Dependency ordering | `service_healthy` | startup order | **PASS** | backend started only after mongo+redis healthy | Low |
| Healthchecks | All three | `docker inspect` | **PASS** | mongo/redis/backend all report health | Low |
| Networking | Two tiers | `network inspect` | **PASS** | `data internal=true`; backend sole dual member | Low |
| DB port exposure | None | `docker ps` | **PASS** | only `127.0.0.1:8099->8000` | Low |
| Volumes persist | Across `down`/`up` | destructive cycle | **PASS** | 12 001 docs survived full `down` → `up` | Low |
| Restart policy | `unless-stopped` | inspect | **PASS** | applied via YAML anchor | Low |
| Dev≠prod | No silent bleed | `config --services` | **PASS** | dev overlay fails closed without its own creds | Low |
| Log driver caps | Bounded | inspect | **PASS** | `json-file 10m×3 non-blocking` | Low |
| Secrets overlay | Parses | `-f … secrets.yml config` | **PASS** | valid | Low |

### 4.3 Secrets

| Requirement | Verification | Result | Evidence | Risk |
|---|---|---|---|---|
| No secrets in git | `gitleaks` full history (70 commits) | **PASS** | 4 hits, all synthetic test fixtures | Low |
| `.env` ignored | `git ls-files` + history | **PASS** | never committed, any branch | Low |
| `.env.example` placeholders only | manual + CI drift gate | **PASS** | `generate_env_example.py --check` in sync | Low |
| Production requires secrets | live boot, no config | **PASS** | 6 errors, refused to start | Low |
| Startup fails closed | 5-case matrix | **PASS** | see §7 | Low |
| Secrets not in logs | grep live secrets in both sinks | **PASS** | **0 hits** across 4 secrets × 2 sinks | Low |
| Secrets not in image | history + filesystem | **PASS** | 0 hits | Low |
| CI exposes no secrets | workflow review | **PASS** | ephemeral CI creds only | Low |
| Inline `gitleaks:allow` discipline | fixture review | **PARTIAL** | 2 of 4 fixtures lack the marker (L1) | Low |

### 4.4 CI/CD

| Requirement | Verification | Result | Evidence | Risk |
|---|---|---|---|---|
| Install deps | composite action + cache | **PASS** | cache hit observed in run logs | Low |
| Run tests | `pytest -m "not integration"` | **PASS** | **1014 passed, 0 failed** locally under CI's exact selection | Low |
| Security checks | gitleaks, CodeQL, config drift | **PASS** | `security-audit`, `codeql` green | Low |
| Build frontend | craco build | **PASS** | exit 0, code-split | Low |
| Build backend image | `docker-build` workflow | **PASS** | green on last 3 runs | Low |
| **Blocking lint gate** | `flake8 --select=E9,…` | **FAIL → FIXED** | red on *every* run since PH2.4 (H1) | High |
| Dependency audit gate | pip-audit / npm audit | **FAIL** | 6 runtime CVEs + npm high (H4/H5) | High |
| Deployment workflow | — | **N/A** | not built (H7) | High |
| Rollback in CD | — | **N/A** | script-based only, no CD | High |
| Branch protection | — | **UNVERIFIED** | cannot confirm gates are required (M6) | Medium |

### 4.5 Monitoring / Logging / Redis / Config

| Requirement | Verification | Result | Evidence | Risk |
|---|---|---|---|---|
| Liveness | `/api/health/live` | **PASS** | 200; stays 200 when Mongo dies | Low |
| Readiness | `/api/health/ready` | **PASS** | 503 + `not_ready` when Mongo dies | Low |
| Mongo health | critical dependency | **PASS** | `mongodb fail critical=True timeout after 2s` | Low |
| Redis health | non-critical | **PASS** | app stayed `ready`; auto-recovered | Low |
| Metrics | Prometheus, gated | **PASS** | 401 without token, 200 with; 20+ families | Low |
| Alerting | notify a human | **FAIL** | none exists (M1) | Medium |
| Structured logs | JSON to stdout | **PASS** | every field present incl. `version` | Low |
| Request IDs | generated + echoed | **PASS** | supplied ID returned verbatim | Low |
| Secret redaction | file sink | **PASS** | 0 hits (see §4.3) | Low |
| Log rotation | size + retention | **PASS** | 5 streams separated on disk | Low |
| Redis auth | required | **PASS** | `NOAUTH Authentication required` | Low |
| Redis persistence | AOF | **PASS** | `appendonly yes` | Low |
| Redis eviction | bounded | **PASS** | `allkeys-lru` | Low |
| Cache failure degrades | not fatal | **PASS** | verified live | Low |
| `APP_ENV=production` | default | **PASS** | image default; explicit downgrade needed | Low |
| Wildcard CORS | impossible | **PASS** | `*` discarded; `allow_origins=['https://app.example.com']` | Low |
| Weak JWT rejected | entropy check | **PASS** | 2 distinct errors | Low |

### 4.6 Backup / DR / Deployment / Rollback

| Requirement | Verification | Result | Evidence | Risk |
|---|---|---|---|---|
| Mongo backup | `backup_mongo.sh` | **PASS** | 114 KB encrypted, **1 s**, **docker mode** | Low |
| **Docker-mode transport** | previously unverified | **PASS** | closes PH2.9 L6 | Low |
| Backup encryption | AES-256 mandatory in prod | **PASS** | `openssl-aes-256-cbc-pbkdf2-600000` | Low |
| Backup verification | 3 levels | **PASS** | checksum + structural + drill | Low |
| Restore drill | scratch DB | **PASS** | **16 collections matched** | Low |
| **Destructive restore** | real data loss | **PASS** | 3 collections dropped → **fully recovered** | Low |
| Retention | GFS count-based | **PASS** | code-verified; not time-drilled (L2) | Low |
| Config recovery | `backup_config.sh` | **PASS** | 14 files, round-trip verified, **0.75 s** | Low |
| Redis recovery | classified disposable | **PASS** | see §11 | Low |
| Off-host copy | required for R7 | **FAIL** | documented, not implemented (H6) | High |
| DR runbooks | R1–R10 | **PASS** | present and specific | Low |
| DR verification tool | `dr_verify.sh` | **FAIL → FIXED** | running-build probe never worked (H2) | High |
| Deployment (fresh env) | end to end | **PASS** | see §14 | Low |
| **Rollback** | actually rolls back | **FAIL → FIXED** | silent no-op + false success (C1) | Critical |
| Rollback ledger | append-only, off-host | **PASS** | records before-state and result | Low |
| RPO / RTO | measured | **PARTIAL** | mechanical time measured; detection unbounded (M1) | Medium |

---

## 5. Docker Verification

Built with provenance args, on the certified commit:

```
docker build -t stockassist-backend:cert \
  --build-arg APP_VERSION=2.12.0-cert --build-arg VCS_REF=04e4f57 ./backend
```

**Image: 423 MB.** PH2.1 shipped 1.03 GB and recorded the miss against a
< 400 MB target, attributing ~220 MB to declared-but-unimported dependencies.
PH2.8 pruned `requirements.txt` 118 → 58 packages and projected ~650 MB. The
measured result is **423 MB** — better than the projection and within 6 % of the
original target. The PH2.8 estimate was conservative; the target is effectively
met.

**Warm rebuild: 13.6 s** on an application-code change, confirming the
dependency-layer cache ordering behaves as the Dockerfile's comments claim.

Runtime posture, all verified by execution rather than inspection:

```
uid=10001(appuser) gid=10001(appuser)          ← non-root
touch /app/pwned.py   → DENIED                  ← source tree immutable
command -v pip        → none                    ← no package installer
grep <live JWT> /app  → (no hits)               ← no secrets baked in
ls -a /app | grep env → no env files in image
SecurityOpt           → [no-new-privileges:true]
```

**Lifecycle:** healthy 8 s from cold; `compose stop` drained and exited **0** on
all three services with the shutdown sequence logged (`shutdown_started` →
Redis pub/sub stopped → client closed → `Application shutdown complete`);
restarted healthy in 13 s.

**Not certified:** `read_only: true` remains off. The compose file documents why
(data-science dependencies write to a `$HOME` cache on first use, so a failure
would surface as a market-data outage rather than a startup error). Recorded as
technical debt, not a defect.

---

## 6. Compose Verification

Full lifecycle on a machine with **no pre-existing stockassist volumes or
containers** — a genuine fresh-environment test.

```
up -d      → mongo+redis healthy → backend started → all healthy      8 s
stop       → exit 0 / 0 / 0                                        2 050 ms
up -d      → all healthy                                              13 s
down       → networks removed, 4 volumes preserved
up -d      → all healthy                                              13 s
   data check → users=4001 trades=4000 holdings=4000 cert_marker=1
```

Data survived a **full `down` → `up`**, which is the cycle that distinguishes
named volumes from the anonymous-volume mistake.

**Isolation, verified adversarially:**

```
docker ps          → only 127.0.0.1:8099->8000 published
network inspect    → data internal=true, edge internal=false
redis-cli PING     → NOAUTH Authentication required     (with REDISCLI_AUTH cleared)
mongosh --eval …   → DENIED: Unauthorized               (unauthenticated)
```

The Redis check matters methodologically: the naive probe returns `PONG`, because
compose injects `REDISCLI_AUTH` into the container and `redis-cli` reads it
automatically. Auth is only demonstrated by clearing that variable first. A
certification that ran the naive probe would have recorded a pass for the wrong
reason.

**Dev/prod separation:** `docker compose --env-file <prod> config` (bare, dev
override auto-merged) **fails closed** —
`required variable MONGO_EXPRESS_USERNAME is missing a value`. A production
credential set cannot silently start the dev tooling, and the base file defaults
`APP_ENV` to `production` while the override defaults it to `development`.

---

## 7. Secrets Verification

**Repository scan — `gitleaks` 8.30.1 over all 70 commits, full history:**

4 findings, **all synthetic test fixtures** proving redaction works
(`hunter2`, `sk-live-abcdef123456`, a fake JWT, a fake Fernet key). No real
credential has ever been committed on any branch. Independent checks agree: no
`.env` has ever been added, and a pattern sweep across all tracked files returned
only CI ephemeral credentials and template-construction strings.

**Fail-closed matrix — executed against the real image:**

| Case | Result | Behaviour |
|---|---|---|
| No configuration at all | **exit 1** | 6 aggregated errors, value-free |
| `JWT_SECRET=short` | **exit 1** | length *and* entropy errors |
| `APP_ENV=bogus` | **exit 1** | rejected before Python starts |
| `ANTHROPIC_API_KEY=…placeholder…` | **exit 1** | placeholder detection fired |
| `CORS_ALLOWED_ORIGINS=*` | **fails safe** | wildcard discarded, not accepted |
| `DEBUG=true` | inert | read by no code path |

The placeholder rejection was found by accident — the certification's own first
compose run crash-looped because the stand-in API key contained the word
"placeholder". The validator was right and the certifier was wrong, which is the
correct direction for that error.

**Leakage:** with a real 86-char JWT secret, a real Fernet key, and real Mongo and
Redis passwords in play, all four were grepped for across `docker logs` **and**
the persisted file sinks: **zero hits**.

---

## 8. CI/CD Verification

Five workflows: `backend-ci`, `docker-build`, `security-audit`,
`dependency-audit`, `codeql`. The pipeline shape asked for in the brief is
present up to Deploy:

```
PR ─▶ CI ─▶ Tests ─▶ Security ─▶ Build ─▶ ✗ Deploy ─▶ ✗ Verify
```

**The completion reports said CI worked. It did not.** `gh run list` shows
`backend-ci` **failing on every run** on `main`, and `dependency-audit` likewise.

**H1 — blocking lint gate red since PH2.4 (FIXED).** The `Code quality` job's
BLOCKING step is `flake8 --select=E9,F63,F7,F82,F811,F632 .`. The CI composite
action builds its virtualenv at `backend/.venv-ci`; `backend/.flake8` excludes
`venv` and `.venv`, and flake8 matches these against the path **basename**, so
`.venv-ci` was never excluded. CI therefore linted its own `site-packages`, where
third-party libraries legitimately trip `F811` (conditional re-exports,
`@overload`, property/setter pairs) — 30+ findings from `anthropic` and `_pytest`
alone. It passes locally because the developer virtualenv is named `venv`.

This is worse than an inconvenience: a blocking gate that is *always* red for
reasons outside the repository trains a team to ignore it, and the gate's own
config file asserts "the backend has ZERO of them today … must stay at zero
forever" — which was true of project code the whole time.

Fixed by adding `.venv-ci` to the exclude list. Verified with a controlled
before/after over a tree containing a `.venv-ci`:

```
pre-fix config  → F811 … EXIT=1     ← the CI failure, reproduced
post-fix config → EXIT=0            ← excluded
control: real defect in services/ → F821 … EXIT=1   ← gate still live
```

**H4/H5 — dependency audit failing (NOT fixed, out of sprint scope).**
`pip-audit --strict` on **runtime** requirements reports 6 vulnerabilities:

| Package | Version | Advisories | Fix |
|---|---|---|---|
| `cryptography` | 48.0.1 | PYSEC-2026-3552/3553/3554 | 50.0.0 / 49.0.0 |
| `aiohttp` | 3.14.1 | PYSEC-2026-3545/3546/3547 | 3.14.3 / 3.14.2 |

`npm audit` reports high-severity findings, predominantly in the
`react-scripts`/`jest` transitive chain (`nth-check`→`svgo`, `jsdom`,
`brace-expansion`, `fast-uri`).

These are dependency-currency problems, not infrastructure defects. `cryptography`
is load-bearing for broker-token encryption (Fernet), so a 48 → 50 major bump
needs its own change with the broker suite exercised. Deliberately **not** done
inside a certification sprint. Required before production — §24.

**Also noted:** 15 pip-audit advisories are suppressed with a documented review
date of 2026-08-22 and a hard stop of 2026-09-21. The suppression list has an
expiry mechanism and it is working; the deadline is inside the next sprint window.

---

## 9. Monitoring Verification

Three endpoints, correctly differentiated — and the difference was proven by
killing dependencies rather than by reading code.

| Scenario | `/live` | `/ready` | container health | Operator can tell? |
|---|---|---|---|---|
| Healthy | 200 | 200 `ready` | healthy | — |
| **Redis down** | 200 | **200 `ready`** | healthy | yes — `redis fail critical=False` |
| **Mongo down** | **200** | **503 `not_ready`** | healthy | yes — `mongodb fail critical=True timeout after 2s` |
| Redis restored | 200 | 200 | healthy | auto-recovered, no restart |
| Mongo restored | 200 | 200 (**~6 s**) | healthy | auto-recovered, no restart |

Liveness staying 200 while readiness goes 503 is the correct and frequently
botched behaviour: it drains the instance from a load balancer without triggering
a restart loop for a fault the container cannot fix by restarting.

**Metrics** at `/api/metrics`, token-gated (401 without, 200 with), exposing 20+
families including `app_info`, `dependency_up`, `http_request_duration_seconds`,
full `redis_*` instrumentation (circuit state, pool occupancy, pub/sub
disposition) and `log_records_dropped_total`. `app_info` carried
`version="2.12.0-cert" revision="04e4f57"` — the build actually running.

**The gap is not observability, it is notification.** Every signal an operator
needs exists and is correct. Nothing watches any of it. There is no Prometheus
server, no Grafana, no error tracking, no uptime check, no alert delivery.

| Condition | Detectable | Alerted |
|---|---|---|
| Application down | ✅ | ❌ |
| Database down | ✅ | ❌ |
| Redis down | ✅ | ❌ |
| High error rate | ✅ | ❌ |
| High latency | ✅ | ❌ |
| Container crash | ✅ | ❌ |
| Backup failed | ✅ (exit code) | ❌ |

Per PH2.10's own RTO decomposition, detection dominates recovery time — the
mechanical recovery work measured here is **under 5 seconds**. Closing this is
worth more than any further optimisation of the restore path.

---

## 10. Logging Verification

Verified on the live container with `LOG_TO_FILES=true`.

Structured JSON on stdout, one object per line, every field populated:

```json
{"timestamp":"2026-08-08T18:39:51.904+00:00","level":"INFO","logger":"server",
 "message":"Shutdown initiated — readiness now reports draining",
 "service":"stockassist-backend","environment":"production","version":"2.12.0-cert",
 "request_id":"-","event":"shutdown_started","uptime_seconds":97.953}
```

- **Stream separation on disk:** `access.log`, `application.log`, `security.log`
  written as separate files owned by uid 10001, mode 0750 — so retention can
  differ per stream.
- **Request IDs:** generated per request (`x-request-id` on every response) and a
  supplied `X-Request-ID: cert-trace-12345` was echoed back verbatim.
- **Version stamping:** every record carries the build, so logs from two
  revisions are distinguishable during a rollout.
- **Redaction:** zero hits for four real secrets across both sinks (§7).
- **Container compatibility:** `PYTHONUNBUFFERED=1`, `mode: non-blocking`,
  `10 MB × 3` cap. `docker logs` is correctly positioned as a triage tool, not an
  archive.

**Gap:** log **shipping** is not implemented. The 30 MB cap is minutes of history
at load, and `docker compose down -v` destroys the log volume along with the
databases — which for audit records under a retention obligation is a real
consideration (M3).

---

## 11. Redis Verification

| Property | Verified | Result |
|---|---|---|
| Connection | `REDIS_URL` composed by compose | ✅ |
| Authentication | `NOAUTH` with `REDISCLI_AUTH` cleared | ✅ required |
| Persistence | `CONFIG GET appendonly` | ✅ `yes` (AOF) |
| Eviction | `CONFIG GET maxmemory-policy` | ✅ `allkeys-lru` |
| Health check | container healthcheck | ✅ `PONG`-matched, not exit-code-matched |
| Reconnect | stop → start | ✅ recovered without app restart |
| Cache failure | app behaviour | ✅ stayed `ready`, degraded gracefully |
| Config immutability | `:ro` mount + `enable-protected-configs no` | ✅ |
| Pub/Sub | shutdown log | ✅ subscriber stopped cleanly on `sa:events` |

**Data classification (confirmed correct):**

| Class | Contents | Backed up? | Rationale |
|---|---|---|---|
| **Critical** | *(none)* | — | nothing in Redis is a system of record |
| **Recoverable** | market quotes, computed cache | No | reconstructible from upstream APIs |
| **Disposable** | pub/sub fan-out, in-flight events | No | meaningless once delivered |

Redis is deliberately not backed up. AOF is a warm-start optimisation, not a
backup. The monthly no-TTL tripwire documented in PH2.9 is the control that keeps
this classification honest — if durable state ever lands in Redis, that check is
what catches it.

---

## 12. Backup Verification

All measurements in **`BACKUP_MODE=docker`** against the live containerised stack
— **the transport PH2.9 could not verify** (its L6: "no Docker daemon in the
sprint environment; every measurement taken in `direct` mode"). **L6 is now
closed.**

Dataset: 20 001 documents across 6 collections, 3.1 MB.

| Operation | Result | Time |
|---|---|---|
| Encrypted backup | 114.3 KB artifact | **~1 s** |
| Checksum verify | OK | < 0.2 s |
| Structural verify | decrypted, CRC, archive magic confirmed | < 0.3 s |
| **Drill** | **16 collections matched** | **~1 s** |
| **Destructive restore** | **full recovery** | **~1 s** |
| Config archive | 14 files, round-trip verified | **0.75 s** |

**The destructive test is the one that counts.** Three collections
(`trades`, `holdings`, `cert_marker`) were dropped outright, then:

```
after loss : users=4001 trades=0 holdings=0 cert_marker=0
restore    → 16 collections matched exactly, 0 differ
after      : users=4001 trades=4000 holdings=4000 notifications=4000
             audit_logs=4000 cert_marker=1     marker: PH2.12
readiness  : ready (mongodb pass, redis pass)
```

Encryption is mandatory in production and enforced — the script refuses to write
a plaintext dump rather than warning. Publication is `.partial` → checksum →
rename → checksum again.

**Not verified here:** retention pruning across tier boundaries over real time
(code-verified only), and the off-host copy, which does not exist (H6).

---

## 13. Disaster Recovery Verification

`dr_verify.sh --level full` against the healthy live stack:

```
Layer 1 — host        docker daemon ✓   compose file parses ✓
Layer 2 — containers  backend ✓  mongo ✓  redis ✓   (running/healthy)
Layer 3 — data        mongo reachable ✓  has data ✓ (17 collections)  redis ✓
Layer 4 — application live ✓  ready ✓  startup ✓  running build ✓ 2.12.0-cert (04e4f57)

VERIFIED — 12 checks passed          pass=12 fail=0 skip=0
```

**H2 — the running-build probe never worked (FIXED).** It parsed
`"app_version"` and `"vcs_ref"` out of `/api/diagnostics`. The endpoint has never
emitted those keys; it returns them nested as `build.version` and
`build.revision`. So `RUNNING_VERSION` was always empty and the check could only
ever SKIP — or, once `--expect-version` was supplied, **FAIL a perfectly healthy
correctly-deployed stack while blaming `DR_OPS_TOKEN`**:

```
BEFORE:  FAIL  running build   could not read /api/diagnostics (gated? set DR_OPS_TOKEN)
         ERROR NOT VERIFIED — 1 failed          ← on a stack that was entirely fine
```

The hermetic suite stubbed a `{"app_version","vcs_ref"}` response — the same wrong
shape — so the tests agreed with the bug. **When a probe and its test share an
assumption, only the real endpoint can settle it.** Both were corrected; the stub
now mirrors the real payload, including a `process.python_version` decoy the
parser must not match.

```
AFTER:   PASS  running build   2.12.0-cert (04e4f57)        ← correct version
         FAIL  running build   expected 9.9.9…, serving 2.12.0-cert   ← non-vacuous
         FAIL  running build   could not read … (gated?)    ← token genuinely withheld
```

**Runbook status** (R1–R10 exist in `docs/operations/DISASTER_RECOVERY.md`):

| | Runbook | Status |
|---|---|---|
| R1 | Failed deployment | ✅ **executed** (§15) |
| R2 | Container failure | ✅ **executed** (stop/start, restart policy) |
| R3 | Redis loss | ✅ **executed** (§9) |
| R4 | MongoDB corruption / data loss | ✅ **executed** (§12 destructive restore) |
| R5 | Failed rollback | ⚠️ auto-revert path code-verified + unit-tested, not live-drilled |
| R6 | Storage / volume loss | ⚠️ partially — `down -v` + restore path proven |
| R7 | **Complete server loss** | ❌ **unexecutable — no off-host copy (H6)** |
| R8 | Configuration corruption | ✅ **executed** (config archive round-trip) |
| R9 | Suspected compromise | ⚠️ procedural, not drillable here |
| R10 | Silently failing backup job | ⚠️ detection exists; **no alerting** (M1) |

**RPO / RTO:**

| | Target | Measured | Assessment |
|---|---|---|---|
| RPO | ≤ 24 h | = backup interval | Met by policy; bounded by cron frequency |
| RTO | ≤ 4 h | **mechanical work < 15 s** | Met with enormous margin — *provided a human already knows* |

The RTO figure is honest only with that caveat. Restore is ~1 s, rollback ~10 s,
verification ~5 s. **Detection is unbounded and manual.** RTO is therefore
dominated entirely by how long it takes someone to notice.

---

## 14. Deployment Verification

Executed on a machine with no pre-existing project state.

| Step | Result |
|---|---|
| Fresh environment (no volumes/containers) | ✅ confirmed empty |
| Configure production variables | ✅ two-file split from templates |
| Build | ✅ 423 MB |
| Start infrastructure | ✅ mongo + redis healthy first |
| Start application | ✅ **8 s** to healthy |
| Health checks | ✅ live / ready / startup |
| **Authentication** | ✅ `GET /api/portfolio` → **401 Not authenticated** |
| Database connectivity | ✅ `mongodb pass` |
| Redis connectivity | ✅ `redis pass` |
| Frontend build | ✅ exit 0, code-split |
| API smoke tests | ✅ headers, request IDs, metrics gate, auth gate |
| Shutdown | ✅ exit 0 × 3, graceful drain |
| Restart | ✅ **13 s** |
| Recovery | ✅ data intact through full `down`/`up` |

**Not certified:** there is no deployment *automation*. This was a
human-in-the-loop compose deployment. No CD pipeline, no image registry, no
zero-downtime strategy (a single replica means a redeploy is a brief outage), and
no frontend production image — the frontend is a static bundle with no container
or documented hosting path (H7, H8).

---

## 15. Rollback Verification

> **This section contains the sprint's Critical finding.**

### C1 — the rollback did not roll back, and said it did

**Drill:** deploy `v2-bad` (`2.13.0-badrelease`), then
`deploy_rollback.sh rollback --to cert --yes`.

**Result before the fix:**

```
[INFO] applying cert
 Container stockassist-backend Running          ← recreated NOTHING
[INFO] rollback verified: stockassist-backend:cert is healthy
```

Independent confirmation, taken immediately after:

```
.env says            : BACKEND_IMAGE_TAG=cert         ← intent recorded
container is running : stockassist-backend:v2-bad     ← the BAD release
application reports  : 2.13.0-badrelease / deadbee    ← still serving it
```

**Root cause.** `scripts/backup/lib.sh::bk_load_env_file` **exports every key it
parses out of `.env`**. `deploy_rollback.sh` sources it at startup, so
`BACKEND_IMAGE_TAG=v2-bad` — the tag being rolled *away from* — was already in the
process environment. Docker Compose resolves **shell environment variables at
higher precedence than the `.env` file**. The script's careful atomic rewrite of
`.env` was therefore silently outranked by its own configuration loader; compose
resolved `v2-bad`, correctly concluded nothing had changed, and did nothing.

Verification then passed because it ran `dr_verify --level quick`, which checks
**health** — and the bad release was perfectly healthy. It was serving wrong
behaviour, not failing a probe. That is the normal case for a rollback.

**Why nothing caught it:** the hermetic suite stubs `docker`, so compose's
precedence rules — where the entire defect lives — were never executed. The stub
also returned a *fixed* running image, so it could not represent "the container
did not change" as distinct from "the container changed". PH2.10 had no Docker
daemon and shipped it.

**Impact if it had reached production:** during an incident, an operator runs the
rollback, sees `rollback verified`, and closes the incident while the failing
release continues serving traffic — with the ledger recording a rollback that
never happened. For a trading platform this is a direct route to real financial
loss.

### The fix

Two changes, both minimal and both required:

1. **Pass the tag explicitly to compose** —
   `BACKEND_IMAGE_TAG="${tag}" docker compose … up -d --no-deps backend` — so the
   intended tag outranks whatever the process inherited.
2. **Assert the running build, not just health.** Health and "the intended build
   is running" are two claims; only the second is a rollback. On mismatch the
   script now fails loudly, records `FAILED rollback` in the ledger, and tells the
   operator not to close the incident.

### Post-fix drill (live)

```
serving: 2.13.0-badrelease
[INFO] applying cert
 Container stockassist-backend Recreated        ← actually recreated
[INFO] rollback verified: … is healthy and is the running build
WALL CLOCK: 10s

container image: stockassist-backend:cert
app reports    : 2.12.0-cert 04e4f57            ← independently confirmed
```

**Regression tests added** (`test_disaster_recovery.py`, 41 → 43):

- `test_a_rollback_that_changed_nothing_is_reported_as_a_failure`
- `test_the_intended_tag_is_passed_to_compose_not_only_written_to_the_env_file`

Both **fail without the fix and pass with it** (verified by reverting the fix via
`git stash` and re-running: `2 failed` → restored → `43 passed`). The docker stub
was also corrected to model a running image that *changes* when `up -d` succeeds,
with `STUB_UP_IS_NOOP=1` to represent the silent no-op.

### Remaining rollback limitations

- Depends on the previous image still being on the host — **no registry** (H7).
- Does not reverse database migrations. It asks the question and refuses to
  decide; correct, and it means a rollback across a migration is a restore.
- Requires the tag to be typed; `--previous` exists but the ledger only knows
  what went through the script.
- Single replica ⇒ a rollback is a brief outage, not a rolling replacement.

---

## 16. Production Configuration Verification

| Check | Result | Evidence |
|---|---|---|
| `APP_ENV=production` default | ✅ | image `ENV`; downgrade must be explicit |
| `DEBUG=true` | ✅ inert | read by no code path |
| Development defaults | ✅ | dev overlay fails closed without its own creds |
| Mock credentials | ✅ rejected | placeholder detection fired live |
| Test accounts | ✅ | none in the production path |
| `localhost` production URLs | ⚠️ | `FRONTEND_URL` not required to be TLS/non-local (L2) |
| Weak JWT secrets | ✅ rejected | length + entropy |
| Wildcard CORS | ✅ impossible | `*` discarded from the allowlist |
| Insecure cookies | ✅ | PH1.3 policy intact (§17) |
| Dev OAuth fallback | ✅ | removed in PH1.1/PH1.2, still absent |
| Fail closed | ✅ | container refuses to start; `restart: unless-stopped` retries rather than serving broken |

**L2 (Low):** production accepts `http://` and `localhost` origins without
complaint. The container refuses to start on eleven other classes of
misconfiguration, so this is an inconsistency in an otherwise strict validator
rather than an exposure — TLS is terminated upstream and CORS remains an exact
allowlist. Worth a one-line warning in a future sprint.

---

## 17. Security Regression Check (PH1 controls under PH2 infrastructure)

PH1 was certified separately; this checks only for **regression** caused by
containerisation. Every check ran against the live container.

| Control | Status | Evidence |
|---|---|---|
| Security headers | ✅ **no regression** | HSTS `max-age=63072000; includeSubDomains`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, CSP `default-src 'none'`, `Referrer-Policy`, `Permissions-Policy`, COOP, CORP — all present |
| Server header suppressed | ✅ | no `server:` header (`--no-server-header`) |
| CORS | ✅ **no regression** | wildcard discarded; `allow_credentials=True` safe against an exact allowlist |
| JWT configuration | ✅ | length + entropy enforced at startup |
| Secrets | ✅ | fail-closed; zero leakage into logs or image |
| Auth enforcement | ✅ | `GET /api/portfolio` → 401 |
| CSRF | ✅ | `CSRF_SECRET` registered; JWT fallback warns |
| Rate limiting | ✅ | `FORWARDED_ALLOW_IPS=127.0.0.1` — proxy headers trusted only from the immediate peer, so per-IP throttling cannot be bypassed by a forged `X-Forwarded-For` |
| OAuth | ✅ | no development fallback path |
| Password policy | ✅ | untouched by PH2 |
| Operational endpoints | ✅ **hardened** | `/api/metrics` and `/api/diagnostics` token-gated in production |

**No PH1 control regressed.** Containerisation *improved* the posture: non-root
execution, an immutable source tree, no package installer, `no-new-privileges`,
and a database reachable only from an internal network with no host port.

---

## 18. Operational Readiness

Assessed as: *can an engineer who did not build this system do the thing?*

| Task | Doc | Verdict |
|---|---|---|
| Deploy | `docs/deployment/DOCKER_COMPOSE.md` | ✅ followed successfully this sprint |
| Restart | same | ✅ |
| Inspect health | `docs/operations/MONITORING.md` | ✅ |
| Inspect logs | `docs/operations/LOGGING.md` | ✅ |
| Roll back | `scripts/dr/deploy_rollback.sh --help` | ✅ **now that it works** |
| Restore MongoDB | `BACKUP_AND_RESTORE.md` §9 | ✅ followed successfully |
| Recover Redis | `DISASTER_RECOVERY.md` R3 | ✅ |
| Recover from server loss | R7 | ❌ **not executable** (H6) |
| Rotate secrets | `docs/deployment/SECRETS.md` | ⚠️ documented; not drilled |
| Respond to an incident | `incident-response.md`, `POSTMORTEM_TEMPLATE.md` | ✅ |

The documentation is unusually good — specific, with commands, and honest about
its own limitations. Two gaps found by using it:

- **D1:** No single "deploy from nothing" runbook. The steps are correct but
  spread across `DOCKER.md`, `DOCKER_COMPOSE.md`, `SECRETS.md` and
  `CONFIGURATION.md`. An operator at 3 a.m. needs one page.
- **D2:** `scripts/backup/*` in `docker` mode invokes `docker compose` **without**
  `--env-file`, so it depends on the compose variables living in the repo-root
  `.env`. That is the documented production layout, so it is correct in
  production — but it is undocumented as a *requirement*, and it silently breaks
  any operator using a non-default env file (as this certification did).

---

## 19. Risk Register

| ID | Risk | Sev | Likelihood | Impact | Status | Mitigation |
|---|---|---|---|---|---|---|
| **C1** | Rollback silently no-ops and reports success | **Critical** | Certain | Incident closed while broken code serves | **FIXED + tested** | Tag passed to compose; running build asserted |
| **H1** | Blocking CI lint gate red on every run | High | Certain | Gate ignored; real defects land | **FIXED + verified** | `.venv-ci` excluded |
| **H2** | DR running-build probe can never pass | High | Certain | Rollback unverifiable; false "NOT VERIFIED" | **FIXED + tested** | Parse `build.version`/`build.revision` |
| **H3** | `backend-ci` red on `main` | High | Certain | No trustworthy gate | **RESOLVED via H1** | Confirm green on next push |
| **H4** | 6 CVEs in runtime deps (`cryptography`, `aiohttp`) | High | Certain | Known-vulnerable crypto in prod | **OPEN** | Upgrade + run broker suite — §24 |
| **H5** | npm high-severity advisories | High | Certain | Build-chain exposure | **OPEN** | Mostly dev-chain; audit + upgrade — §24 |
| **H6** | No off-host backup copy | High | Medium | **R7 unexecutable**; host loss = total data loss | **OPEN** | Encrypted remote sync — §24 |
| **H7** | No CD, no image registry | High | Certain | Manual deploys; rollback depends on host-local image | **OPEN** | Roadmap PH2.7b |
| **H8** | No frontend production image | High | Certain | No container path for the frontend | **OPEN** | Roadmap PH2.2 |
| **M1** | No alerting — detection is manual | Medium | Certain | RTO unbounded in practice | **OPEN** | Highest-value next item |
| **M2** | Backup failure not alerted | Medium | Medium | Silent backup rot | **OPEN** | Folds into M1 |
| **M3** | Logs destroyed by `down -v`; no shipping | Medium | Medium | Audit records lost | **OPEN** | Ship off-host |
| **M4** | No point-in-time recovery | Medium | Low | Loss bounded by backup interval | **ACCEPTED** | Needs replica set + `--oplog` |
| **M5** | `WEB_CONCURRENCY` must stay 1 | Medium | Certain | Vertical scaling capped | **ACCEPTED** | Scale by replicas |
| **M6** | Branch protection unverified | Medium | Unknown | Gates advisory, not required | **OPEN** | Confirm in settings |
| **M7** | Healthcheck probes `/api`, not `/api/health/live` | Medium | Certain | Coarser signal than available | **ACCEPTED** | Documented; `HEALTHCHECK_PATH` configurable |
| **M8** | AES-CBC is unauthenticated | Medium | Low | Tampering not cryptographically detected | **ACCEPTED** | Checksums + manifest; format recorded per artifact |
| **M9** | Single host, no failover | Medium | Certain | Every recovery is an outage | **ACCEPTED** | Explicit architectural choice |
| **L1** | 2 test fixtures lack `gitleaks:allow` | Low | Low | Future gitleaks bump could turn CI red | **OPEN** | Add the markers |
| **L2** | `http://`/localhost origins accepted in prod | Low | Low | Validator inconsistency | **OPEN** | Add a warning |
| **L3** | `read_only: true` not enabled | Low | Low | Writable container FS | **ACCEPTED** | Needs a soak sprint |
| **L4** | `backend_uploads` declared, not mounted | Low | Low | Uploads would hit the container layer | **OPEN** | Wire when uploads ship |
| **L5** | Node 20 deprecation warnings in Actions | Low | Certain | Future breakage | **OPEN** | Bump action versions |

---

## 20. Technical Debt

1. **Tests that agree with the code instead of the contract.** Both H2 and C1
   survived because their hermetic tests encoded the implementation's assumption
   rather than the real interface. Any stub standing in for a system boundary
   (`docker`, `curl`, an HTTP payload shape) needs at least one test that pins it
   to reality.
2. **`bk_load_env_file` exports everything it parses.** Convenient, and the direct
   cause of C1. It should export a documented allowlist, or the DR scripts should
   stop inheriting it.
3. **Roadmap vs sprint-track numbering drift** (PH2.2–PH2.11). Documented in
   `TASK.md`, but it costs real time on every cross-reference. Freeze one scheme.
4. **`@app.on_event` → lifespan migration** (finding M15) still outstanding.
5. **`docs/infra/` vs `docs/infrastructure/`** — the roadmap points at the former;
   this document lives in the latter, matching the existing tree.
6. **The `advisory` flake8 backlog** (462 findings) has no burn-down plan.
7. **No integration test job actually boots the stack in CI** — the capability
   this certification exercised by hand is exactly what should be automated.

---

## 21. Known Limitations

The certified system is a **single-host, single-replica deployment**:

- Every deployment is a brief outage. No zero-downtime path.
- Every recovery is an outage. No failover, no standby.
- `WEB_CONCURRENCY=1` is mandatory (in-process scheduler + in-memory WebSocket
  registry are not multi-process safe).
- Data durability is bounded by one host's disks until H6 is closed.
- No point-in-time recovery; a standalone `mongod` is per-collection consistent
  only.
- Nothing watches anything. Every failure is found by a human looking.
- Backups are drilled on a ~20 000-document dataset. Restore time at production
  scale is extrapolated, not measured.
- The certification ran on `darwin/arm64` Docker Desktop. Production is presumed
  `linux/amd64`; the image is architecture-portable but has not been certified on
  that platform.

---

## 22. Final Infrastructure Score

| Category | Score | Rationale |
|---|---:|---|
| Docker | **9** / 10 | Excellent. 423 MB, non-root, immutable, no secrets. −1: `read_only` off |
| Compose | **9** / 10 | Full lifecycle verified; real network isolation. −1: no uploads volume |
| Secrets | **9** / 10 | Clean history, fail-closed, zero leakage. −1: 2 unmarked fixtures |
| CI/CD | **6** / 10 | Strong gates, but red on main until this sprint; no CD; dependency gate failing |
| Monitoring | **6** / 10 | Outstanding instrumentation, **zero alerting** |
| Logging | **9** / 10 | Structured, separated, redacted, bounded. −1: no shipping |
| Redis | **9** / 10 | Authed, persistent, bounded, degrades gracefully |
| Configuration | **9** / 10 | Fails closed on 11 classes. −1: accepts `http://` in prod |
| Backup | **8** / 10 | Encrypted, verified, **destructive restore drilled**. −2: no off-host, no PITR |
| Disaster Recovery | **7** / 10 | Runbooks + working tooling. −3: R7 unexecutable, detection manual |
| Deployment | **7** / 10 | Verified end to end by hand. −3: no CD, no registry, no frontend image |
| Operations | **8** / 10 | Genuinely good docs. −2: no single deploy runbook, undocumented env-file coupling |

> ### **Overall Infrastructure Score: 8.0 / 10**

For context, `PRODUCTION_HARDENING.md` recorded **4.2 → ~6.4** post-PH1. PH2 moves
infrastructure to **8.0**. The remaining 2.0 is concentrated in four unbuilt
things — alerting, CD, off-host backup, and a frontend image — rather than in
anything that is wrong with what exists.

---

## 23. Certification Decision

**CONDITIONALLY CERTIFIED** — see §2 for the basis.

Signed off for a single-host production deployment, conditional on §24 and
subject to the limitations in §21.

The condition is not a formality. **H4 (six CVEs in runtime cryptography and HTTP
dependencies) and H6 (no off-host backup) should be closed before real user data
is stored**, and **M1 (no alerting) determines whether the measured 15-second RTO
means anything at all** — a four-hour RTO target is met by the mechanics and
missed entirely by a system nobody is watching.

---

## 24. Required Actions Before Production

| # | Action | Severity | Why | Est. |
|---|---|---|---|---|
| **1** | Upgrade `cryptography` → 50.0.0 and `aiohttp` → 3.14.3; re-run `pip-audit --strict`; exercise the broker suite (Fernet token encryption) | **High** | Known-vulnerable crypto in the runtime image | 0.5 d |
| **2** | Triage `npm audit` high findings; upgrade what is reachable in the production bundle | **High** | Build-chain exposure | 0.5 d |
| **3** | Wire the **off-host encrypted backup copy** | **High** | R7 (complete server loss) is unexecutable without it; a backup on the host it protects survives only the failures that do not matter | 1 d |
| **4** | Stand up **alerting**: uptime check on the public URL, error tracking, and alerts for app/Mongo/Redis down, error rate, and **backup failure** | **Medium→High** | Detection dominates RTO; without it the measured recovery times are theoretical | 2 d |
| **5** | Confirm **branch protection** requires `backend-ci`, `security-audit`, `dependency-audit`, `codeql` on `main` | **Medium** | Every gate PH2.4–PH2.6 built is advisory until this is on | 0.5 h |
| **6** | Push the four PH2.12 fixes and **confirm `backend-ci` goes green** | **High** | The fix is verified locally; CI must prove it | 0.5 h |

**Strongly recommended, not blocking:** a single "deploy from nothing" runbook
(D1); document the `.env` coupling in the backup scripts (D2); add the two
`gitleaks:allow` markers (L1); bump Node-20 actions (L5).

---

## 25. Phase 3 Handoff

**PH2 is complete.** The infrastructure is verified, its defects are fixed, and
its gaps are written down with owners and estimates.

Handoff to **PH3 — Production Hardening & Quality Assurance**, with these
carry-ins:

| PH3 sprint | Carry-in from PH2.12 |
|---|---|
| **PH3.1** Test Infrastructure | **The C1/H2 lesson is PH3.1's charter.** Both defects were shipped by tests that stubbed a boundary and then agreed with the implementation. PH3.1 should add an **integration job that boots the real compose stack in CI** — the capability this certification had to exercise by hand |
| **PH3.2** Frontend Tests | No frontend test job exists; the CI placeholder is waiting |
| **PH3.3** Backend Tests | 1014 hermetic tests pass. The `test_phase*`/`test_backend` suites still need a live server — migrate or mark them |
| **PH3.4** Performance | No baseline. Warm rebuild 13.6 s, cold start 8 s are the only figures |
| **PH3.5** Load Testing | k6/staging deferred since PH1; still no staging environment |
| **PH3.6** Memory | Redis capped at 256 MB; container sizing (2× `maxmemory` for AOF rewrite) unvalidated under load |
| **PH3.7** Monitoring | **Inherits M1** — the alerting half of roadmap PH2.10 is unbuilt and is the single highest-value remaining item |
| **PH3.8** Analytics | Not started |
| **PH3.9** Mock Removal | Market data is already live-or-unavailable; needs an audit pass |
| **PH3.10** Final Production Audit | Must re-check H4/H5 dependency currency |
| **PH3.11** Regression | Full PH1 + PH2 regression, including the four PH2.12 fixes |
| **PH3.12** Production Certification | The gate this document is the infrastructure half of |

**Do not start PH3 without** closing §24 items 1, 3, 4 and 6 — they are
production prerequisites, not PH3 scope.

---

## Appendix — Certification Environment

| | |
|---|---|
| Host | darwin 25.5.0, arm64 |
| Docker | 29.4.0 (Docker Desktop) |
| Compose | v5.1.1 |
| Python | 3.11.15 (container), 3.11 (local) |
| gitleaks | 8.30.1 |
| Repo commit | `04e4f57` |
| Image | `stockassist-backend:cert` — 423 MB, `2.12.0-cert` / `04e4f57` |
| Stack | 3 services, 2 networks, 5 volumes, fresh volumes |
| Dataset | 20 001 documents / 6 collections / 3.1 MB |
| Tests | 1014 hermetic passed; DR suite 41 → **43** |
| Cleanup | stack torn down, volumes removed, cert images deleted, `.env` restored byte-identical (sha256 verified) |

**Files modified by this sprint** (4 — all remediation, no feature work):

```
backend/.flake8                        H1 — exclude .venv-ci
scripts/dr/dr_verify.sh                H2 — parse build.version / build.revision
scripts/dr/deploy_rollback.sh          C1 — pass tag to compose; assert running build
backend/tests/test_disaster_recovery.py  regression tests + honest docker stub
```

*No trading logic, AI logic, product functionality or architecture was modified.*

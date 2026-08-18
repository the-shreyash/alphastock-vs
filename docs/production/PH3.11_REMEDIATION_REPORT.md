# StockAssist AI — PH3.11 Dependency Remediation Report

**Sprint:** PH3.11 remediation (blocker B-1)
**Date:** 2026-08-17
**Engineer:** Principal Release Engineer
**Candidate commit:** `32437e8` + this working tree
**Predecessor:** `docs/production/PH3.11_RELEASE_CANDIDATE_REPORT.md` — verdict **BLOCKED**

---

## 1. Blocker status before remediation

PH3.11 passed every regression check and then failed on one thing: **the
repository's own `dependency-audit` workflow was red on both jobs**, and no
previous sprint had ever executed it.

| Job | Command | Result |
|---|---|---|
| `backend-advisories` | `pip-audit --strict -r requirements.txt <15 ignores>` | **exit 1** — 6 advisories |
| `backend-advisories` | `pip-audit --strict -r requirements-dev.txt` (no ignores) | **exit 1** — 13 advisories |
| `frontend-advisories` | `npm audit --audit-level=high` | **exit 1** — 18 high |
| `dependency-audit` (aggregate) | requires both | **FAIL** |

Analysis during remediation found the gate was not merely red — it was
**structurally incapable of going green**, for three independent reasons:

1. **The npm job had no triage mechanism at all.** A bare
   `npm audit --audit-level=high` with no suppression path, failing on advisories
   in the Create React App build chain that nobody could act on without replacing
   the entire build system.

2. **8 of the 15 Python suppressions were dead.** They named `litellm` (7) and
   `ecdsa` (1) — packages already **removed** from `requirements.txt`. They
   suppressed nothing. Nothing checked that a suppression still matched a live
   finding, so the rot was invisible. The CI job summary still advertised them as
   pending remediation.

3. **The dev-requirements step contradicted the runtime step.**
   `requirements-dev.txt` line 17 is `-r requirements.txt`, so it re-audits every
   runtime package — and CI ran it with **no suppressions**, on the stated premise
   that "dev tooling never reaches production". That premise is false for a file
   that transitively includes the entire runtime set, so the 7 deliberately
   accepted starlette advisories failed there unconditionally. **This job could
   never have passed since the day the suppression policy was written.**

---

## 2. Python advisories — disposition

Every advisory was analysed for installed version, fixed version,
direct/transitive status, runtime reachability, and compatibility impact before
any decision was taken.

| Advisory | Package | Installed | Fixed in | Dep type | Reachable? | Disposition |
|---|---|---|---|---|---|---|
| PYSEC-2026-3545 | aiohttp | 3.14.1 | **3.14.3** | transitive (aiohttp-retry, twilio, google-auth) | client-side only | **A — FIXED** |
| PYSEC-2026-3546 | aiohttp | 3.14.1 | 3.14.2 | transitive | **no** — server-side smuggling; aiohttp is client-only here | **A — FIXED** |
| PYSEC-2026-3547 | aiohttp | 3.14.1 | 3.14.2 | transitive | client-side only | **A — FIXED** |
| PYSEC-2026-3552 | cryptography | 48.0.1 | **50.0.0** | direct | **no** — PKCS#7 decrypt; not used | **A — FIXED** |
| PYSEC-2026-3553 | cryptography | 48.0.1 | 49.0.0 | direct | **no** — X.509 chain building; not used | **A — FIXED** |
| PYSEC-2026-3554 | cryptography | 48.0.1 | 49.0.0 | direct | **no** — X.509 name constraints; not used | **A — FIXED** |
| PYSEC-2026-249 | starlette | 0.37.2 | 1.3.1 | direct | **no** — no form parsing anywhere | **B — NOT REACHABLE** |
| PYSEC-2026-1941 | starlette | 0.37.2 | 0.47.2 | direct | **no** — no multipart endpoints | **B — NOT REACHABLE** |
| PYSEC-2026-1943 | starlette | 0.37.2 | 0.40.0 | direct | **no** — no multipart endpoints | **B — NOT REACHABLE** |
| PYSEC-2026-2280 | starlette | 0.37.2 | 1.1.0 | direct | **no** — no `HTTPEndpoint` | **B — NOT REACHABLE** |
| PYSEC-2026-2281 | starlette | 0.37.2 | 1.1.0 | direct | **no** — no `StaticFiles`; Windows-only defect, Linux image | **B — NOT REACHABLE** |
| PYSEC-2026-161 | starlette | 0.37.2 | 1.0.1 | direct | not provably prevented | **C — TEMPORARILY ACCEPTED** (expires 2026-11-15) |
| PYSEC-2026-248 | starlette | 0.37.2 | 1.3.0 | direct | not provably prevented | **C — TEMPORARILY ACCEPTED** (expires 2026-11-15) |
| PYSEC-2026-1325 | ecdsa | — | — | **package absent** | n/a | **suppression DELETED (dead)** |
| PYSEC-2026-388/390/2597-2600, GHSA-69x8-hrgq-fjj8 | litellm | — | — | **package absent** | n/a | **7 suppressions DELETED (dead)** |

**No advisory is category D.** Nothing remains that requires an architectural
upgrade *and* is reachable.

### Reachability evidence (re-runnable)

```bash
# no form / multipart parsing anywhere → PYSEC-2026-249, 1941, 1943 unreachable
grep -rnE "UploadFile|File\(|Form\(|request\.form\(" backend/ --include=*.py
#   → only email.mime.multipart (outbound SMTP), unrelated to request parsing

grep -rn "HTTPEndpoint" backend/ --include=*.py    # → none  (PYSEC-2026-2280)
grep -rn "StaticFiles"  backend/ --include=*.py    # → none  (PYSEC-2026-2281)

# cryptography surface is Fernet only → PYSEC-2026-3552/3553/3554 unreachable
grep -rn "from cryptography" backend/ --include=*.py
#   → services/brokers/crypto.py: from cryptography.fernet import Fernet, InvalidToken
grep -rn "pkcs7\|x509\|PolicyBuilder\|load_pem_x509" backend/ --include=*.py   # → none

# aiohttp is a client, never a server → PYSEC-2026-3546 unreachable
grep -rn "aiohttp" backend/services backend/infrastructure backend/server.py | grep -iE "web\.|run_app"
#   → none
```

### Why 161 and 248 are accepted rather than dismissed

Both concern `request.url` being rebuilt from an unvalidated Host header and
path. The application reads **only `request.url.path`** — CSRF exempt-path
matching (`security/csrf.py:256`, an exact-match set) and rate-limit keying
(`security/rate_limit.py:441`) — and never the reconstructed absolute URL:
`str(request.url)`, `request.base_url` and `url_for` appear nowhere, and every
`RedirectResponse` target is built from the `FRONTEND_URL` environment variable.

That makes them unreachable *as the code is written today*, but the safety rests
on a coding convention rather than a control, and there is no
`TrustedHostMiddleware`. Classifying that as "not reachable" would overstate the
evidence, so they carry the shortest expiry in the register and a named
follow-up: add Host validation.

### Compatibility verification of the two upgrades

**aiohttp 3.14.1 → 3.14.3** (patch-level, same minor)

| Check | Result |
|---|---|
| Dependent constraints (`twilio>=3.8.4`, `aiohttp-retry`, `google-auth<4.0.0`) | all satisfied |
| `pip check` | No broken requirements found |
| Application usage | one string reference in `observability/errors.py`; used transitively |
| Backend suite | **2,559 passed / 0 failed / 4 xfailed** |

**cryptography 48.0.1 → 50.0.0** (two majors — analysed, not assumed)

| Check | Result |
|---|---|
| Dependent constraints | **all lower bounds** (`>=45`, `>=38.0.3`, `>=3.4.0`, `>=2.5`, `>=36.0.1`); nothing caps below 50 |
| API surface used | exactly one import: `from cryptography.fernet import Fernet, InvalidToken` |
| **Backward compatibility of existing data** | a Fernet token encrypted under **48.0.0 decrypts correctly under 50.0.0** — verified directly, because existing broker tokens in a production database must remain readable |
| `pip check` | No broken requirements found |
| Broker integration tests | 34 passed |
| Backend suite | **2,559 passed / 0 failed / 4 xfailed** |
| Compiled wheel in the Linux image | installs; Fernet round-trip verified **inside the container** |

The major-version jump was taken because the analysis showed it was safe and
testable, not because the advisories demanded it — all three were already
unreachable. Fixing beats suppressing when the fix is verifiable, and this one
removes three register entries permanently.

---

## 3. npm advisories — disposition

**18 high → 11 high.** Seven were fixed outright; the rest are triaged.

### Fixed (A) — patch-level `overrides`, all within the same major

| Package | Was | Now | Advisory |
|---|---|---|---|
| brace-expansion | 1.1.16 | **1.1.18** | GHSA-mh99-v99m-4gvg, GHSA-rgw5-rvv9-x895 |
| fast-uri | 3.1.4 | **3.1.5** | GHSA-7p8r-x3mc-p8w7 |
| js-yaml | 4.3.0 | **4.3.1** | GHSA-5p4m-2wfm-xmqj |
| nanoid | 3.3.12 | **3.3.18** | GHSA-28wg-ghj8-5hjv, GHSA-2v37-7h3g-55p8 |
| underscore | 1.13.6 | **1.13.8** | GHSA-qpx9-hpmf-5gmw |
| bfj | — | — | cleared transitively (was flagged via jsonpath → underscore) |
| jsonpath | — | — | cleared transitively (was flagged via underscore) |
| **postcss** (direct devDependency) | ^8.4.49 → 8.5.15 | **^8.5.26** | 5 GHSAs; now reports `isDirect: false` |

`overrides` (npm) and `resolutions` (yarn) are both declared. `package.json`
carries `packageManager: yarn@1.22.22` while CI uses npm, and **yarn 1 ignores
`overrides` entirely** — a fix that silently fails to apply under the declared
package manager would be worse than no fix, so both keys are set.

### Triaged (B — not reachable) — 11 packages, 16 advisory rows

`@svgr/plugin-svgo`, `@svgr/webpack`, `css-select`, `nth-check`, `postcss`
(nested 7.0.39 only), `react-scripts`, `rollup-plugin-terser`,
`serialize-javascript`, `svgo`, `workbox-build`, `workbox-webpack-plugin`.

All are Create React App build tooling. Three independent lines of evidence:

1. **No application import.** For all eighteen originally-flagged packages:
   `grep -rE "from '<pkg>'|require\('<pkg>'\)" frontend/src/` → **0 matches**.
2. **Reached only through the build toolchain.** Walking
   `npm ls --omit=dev --all --json`, every path passes through `react-scripts`.
   The one apparent exception, `ajv > fast-uri`, was investigated: `ajv` is a
   declared dependency but is imported nowhere in `src/`; it is a resolution pin
   whose only real consumers are build-time `schema-utils`.
3. **Absent from the shipped bundle.** `grep -r "<pkg>" frontend/build/static/js/`
   → 0 matches. The historical `svgo` "hit" was a minifier-generated variable
   name (`svgo = target.getAttribute(...)`), not the library — which is why this
   report argues from dependency paths rather than name-matching alone.

`npm audit --omit=dev` does **not** filter this group, because `react-scripts`
sits under `dependencies` — Create React App's own layout, not a project error.
That is why reachability had to be argued from the graph and the bundle.

**Why they are not fixed:** npm reports the fix for each as
`react-scripts@0.0.0` with `isSemVerMajor: true` — i.e. "remove it". Clearing
them means migrating off CRA, which replaces the entire build pipeline and is a
roadmap item (PH2 H5 / PH3.10 P2-4), not a dependency bump. `postcss@7.0.39`
specifically cannot be overridden to 8: `resolve-url-loader@4.0.0` uses the
postcss 7 plugin API, and 7 → 8 is a breaking change.

### Frontend verification after every change

| Check | Result |
|---|---|
| `npm ci --legacy-peer-deps` | exit 0 |
| Test suite | **395 passed / 22 suites** |
| Production build | **exit 0**, 48 bundles, 14 MB — byte-for-byte the same shape as baseline |

The riskiest override was `js-yaml`, forced from a tree containing both 3.15.1
and 4.3.0 to a single 4.3.1 (js-yaml 3 → 4 removed `safeLoad`). The build
exercises every consumer and passes, which is the evidence that matters.

---

## 4. Dependency versions changed

| File | Change |
|---|---|
| `backend/requirements.txt` | `aiohttp==3.14.1` → **`3.14.3`** |
| `backend/requirements.txt` | `cryptography==48.0.1` → **`50.0.0`** |
| `frontend/package.json` | devDependency `postcss` `^8.4.49` → **`^8.5.26`** |
| `frontend/package.json` | added `overrides` + `resolutions` (5 packages) |
| `frontend/package-lock.json` | regenerated (`npm install --package-lock-only --legacy-peer-deps`) |
| `frontend/yarn.lock` | rewritten consistently by npm — the known C-8 two-lockfile issue |

**No application code was changed.** The only source files touched are
dependency manifests, the CI workflow, and new tooling/documentation.

---

## 5. CI gate changes

`.github/workflows/dependency-audit.yml` was rebuilt around a register.

| Before | After |
|---|---|
| Suppressions in a YAML `env` string, Python only | `.github/dependency-triage.yml`, both ecosystems |
| npm had no triage path | npm judged by the same register |
| Dev-requirements audited with no suppressions, contradicting the runtime step | both requirement files audited in one pass, findings de-duplicated |
| Expiry checked only for Python, two-stage with a 30-day grace period | enforced per entry, no grace period |
| Nothing detected a suppression that matched nothing | **stale entries fail the build** |
| `npm audit --json > f 2>/dev/null \|\| true` | auditor exit codes read from the process, never through a pipe |
| Pass/fail only | 0 clean · 1 policy violation · **2 audit could not run** |

The third exit code matters: "the check could not be performed" must never be
reported as "the check passed". `--strict` is retained so an unresolvable package
is an error rather than a silent clean audit.

---

## 6. Triage mechanism and schema

**Register:** `.github/dependency-triage.yml`
**Enforcer:** `.github/scripts/dependency_audit.py`

Every entry carries: `id`, `ecosystem`, `package`, `affected`, `severity`,
`classification`, `reason`, `reachability`, `evidence`, `mitigation`, `fixed_in`,
`blocked_by`, `owner`, `expires`.

Two classifications only:

* **`not-reachable`** — the vulnerable code path cannot execute here.
  **Requires `evidence`: a command a reviewer can re-run.** The enforcer rejects
  the register outright if this field is missing, so the stronger claim cannot be
  made without proof.
* **`temporarily-accepted`** — reachable, or not provably unreachable. Requires a
  mitigation and gets a shorter expiry.

Enforcement rules:

| Rule | Effect |
|---|---|
| Finding with no entry | **FAIL** (`UNTRIAGED`) |
| Entry past `expires` | **FAIL** (`EXPIRED`) — no grace period |
| Entry matching no live finding | **FAIL** (`STALE`) |
| Entry expiring within 30 days | warn, do not fail |
| Auditor unusable | **exit 2**, distinct from both pass and policy failure |

The `STALE` rule is the one that would have caught the litellm/ecdsa rot, and it
is the reason this register cannot decay the way the previous list did.

### Expiry dates, and why they are not a silent extension

The old `SUPPRESSION_REVIEW_BY: 2026-08-22` is **not** carried forward. It was
re-argued, and the outcome differs per advisory:

| Group | Old | New | Why |
|---|---|---|---|
| litellm ×7, ecdsa ×1 | 2026-08-22 | **deleted** | packages no longer in the dependency set |
| aiohttp ×3, cryptography ×3 | (not listed) | **fixed** | upgraded, no entry needed |
| starlette ×5 (structurally unreachable) | 2026-08-22 | **2027-02-15** | re-triaged against the codebase; unreachability is structural (no form parsing, no `HTTPEndpoint`, no `StaticFiles`), so a short leash buys nothing |
| starlette ×2 (161, 248) | 2026-08-22 | **2026-11-15** | safety rests on a convention, not a control — the shortest leash in the register |
| npm build chain ×16 | (none existed) | **2026-11-15** | tied to the CRA migration decision |

The previous blanket justification was "pinned by fastapi". That is why all seven
starlette advisories carried one date. Re-arguing them produced a 5/2 split, and
the two that could not be dismissed got a **shorter** deadline than before, not a
longer one.

---

## 7. Verification results

### Before / after

| Check | Before | After |
|---|---|---|
| `pip-audit` runtime | **exit 1**, 6 advisories | **exit 0**, 0 untriaged |
| `pip-audit` dev | **exit 1**, 13 advisories | folded into one pass, 0 untriaged |
| `npm audit --audit-level=high` | **exit 1**, 18 high | 11 high, **all triaged** |
| `dependency_audit.py --ecosystem all` | n/a | **exit 0** |
| Python suppressions | 15 (8 dead) | **7, all live and evidenced** |
| npm triage entries | 0 (no mechanism) | **16** |
| Backend suite | 2,559 passed | **2,559 passed / 0 failed / 4 xfailed** |
| Security suite | 452 passed | **452 passed** |
| Frontend suite | 395 passed | **395 passed / 22 suites** |
| Production build | exit 0, 48 bundles, 14 MB | **exit 0, 48 bundles, 14 MB** |
| Docker image | 424 MB | **425 MB** (newer cryptography wheel) |

### Gate negative tests — proving the gate bites

| # | Mutation | Expected | Observed |
|---|---|---|---|
| 1 | Run dated past an entry's expiry | fail | **exit 1**, 2 × `EXPIRED` |
| 2 | Run **on** the expiry date | pass | **exit 0** |
| 3 | Run one day later | fail | **exit 1** |
| 4 | Run inside the 30-day window | pass + warn | **exit 0**, 2 × `EXPIRING` |
| 5 | Delete a register entry | fail | **exit 1**, `UNTRIAGED starlette PYSEC-2026-2281` |
| 6 | Add an entry matching nothing | fail | **exit 1**, `STALE package-that-was-removed` |
| 7 | Downgrade `aiohttp` to 3.14.1 | fail | **exit 1**, 3 × `UNTRIAGED` |
| 8 | Remove the `underscore` override, regenerate lock | fail | **exit 1**, `UNTRIAGED bfj` |

**Every mutation was reverted and the revert verified.** After tests 5 and 6 the
register was confirmed byte-identical (`diff -q`); after tests 7 and 8 the gate
returned **exit 0** and `git diff` showed only the intended changes.

### PH3.11 release checks, re-run after remediation

| Check | Result |
|---|---|
| Route inventory | **97 protected / 29 admin / 75 public = 201** — unchanged |
| Analytics provenance | `{real: 4, derived: 32, mock: 0, unavailable: 17}` — **0 MOCK** |
| Trading-engine mutation check | injected key → test **FAILS**; reverted clean; **36 passed** |
| Live boot (production image) | healthy, **startup 0.44 s** |
| JWT | exactly **900 s**, full claim set |
| Cookies | `access_token`/`refresh_token`/`csrf_token` all **Secure** |
| Rate limiting | `401×5 → 429 429` |
| Security headers | **6 of 6**; no ACAO for a disallowed origin |
| **WebSocket P0 matrix** | anonymous / spoofed `user_id` / query token / forged subprotocol → **all 403**; cookie and `stockassist.auth` subprotocol → **CONNECTED**; valid token + spoofed `user_id` → **binds to the token's subject** |
| WebSocket isolation | 2 users concurrent, **0 foreign-id references** |
| Redis outage | API serving, readiness `redis: fail, critical:false`, **0 restarts** |
| Mongo outage | liveness 200, readiness **503**, no leakage, **0 restarts**, full recovery |
| Log leakage | **0** occurrences of all 6 configured secrets |
| Graceful shutdown | **2 s, exit 0**, all 4 tasks stopped, every pool closed |

One incidental confirmation: the first live boot attempt **failed closed**
because the `METRICS_TOKEN` supplied was 20 characters and the validator requires
32. That is the configuration gate working, not a regression.

---

## 8. Remaining blockers

**None.**

The `dependency-audit` gate is green, and green here means every advisory is
either fixed or covered by an unexpired entry carrying re-runnable evidence — not
that the advisories were silenced.

Open items, none release-blocking, all carried from PH3.10 with unchanged
disposition:

| Item | Status |
|---|---|
| C-1…C-8 (SMTP, dedicated secrets, Mongo timeout, alerting, off-host backup, single process, same-origin, one lockfile) | open, deployment prerequisites |
| CRA migration | roadmap — clears the 16 npm entries |
| FastAPI + Starlette coordinated upgrade | roadmap — clears the 7 python entries |
| `TrustedHostMiddleware` | recommended follow-up for PYSEC-2026-161/248 |
| P3-1…P3-5 from the RC report | documented observations |

Two register deadlines are now real calendar commitments: **2026-11-15** (npm
build chain + starlette 161/248) and **2027-02-15** (structurally unreachable
starlette). The gate fails on those dates with no grace period.

---

## 9. PH3.11 final verdict

> ## **READY FOR PH3.12 CERTIFICATION**

The single blocker identified by PH3.11 is resolved, and resolved by fixing what
could be fixed rather than by suppressing it:

* **6 of 6 Python advisories fixed** — `aiohttp` 3.14.1 → 3.14.3 and
  `cryptography` 48.0.1 → 50.0.0, both verified against the full suite, the
  broker-token path (including decryption of data written under the old version)
  and the production image.
* **7 of 18 npm advisories fixed** by patch-level overrides, plus the direct
  `postcss` devDependency — 18 high → 11.
* **8 dead suppressions deleted.**
* The remaining 23 advisory rows are triaged with re-runnable evidence, named
  owners and enforced expiry dates.
* The gate is now **satisfiable, meaningful and tested** — eight negative tests
  prove it fails on expiry, on an untriaged finding, and on a suppression that
  has rotted.

Every PH3.11 regression check was re-run after the changes and every one
reproduced its baseline: 2,559 backend, 452 security, 395 frontend, 48 bundles,
201 routes, 0 MOCK metrics, the WebSocket P0 matrix fully closed, controlled
degradation under fault injection with zero restarts, and a clean shutdown.

**Recommend proceeding to PH3.12.** The eight PH3.10 deployment conditions remain
open and are prerequisites for launch, not for certification.

---

## 10. Evidence commands

```bash
# The gate
python .github/scripts/dependency_audit.py --ecosystem all          # → exit 0
python .github/scripts/dependency_audit.py --ecosystem python --today 2026-11-16   # → exit 1 EXPIRED

# Python audits (CI-pinned tool)
pip-audit==2.7.3 --strict -r backend/requirements.txt       # → no untriaged findings
#   before: cryptography×3 + aiohttp×3

# npm
cd frontend && npm ci --legacy-peer-deps && npm audit --json
#   high: 18 → 11 ; low/moderate unchanged

# Compatibility
backend/venv/bin/pip check                                   # No broken requirements found
docker run --rm --entrypoint python stockassist-rc:ph311r -c \
  "import aiohttp,cryptography; print(aiohttp.__version__, cryptography.__version__)"
#   → 3.14.3 50.0.0 ; Fernet round-trip ok

# Suites
cd backend && venv/bin/python -m pytest -q                   # 2559 passed, 4 xfailed
cd backend && venv/bin/python -m pytest -m security -q       # 452 passed
cd frontend && CI=true npx craco test --watchAll=false       # 395 passed, 22 suites
cd frontend && REACT_APP_BACKEND_URL="https://ci.invalid" npm run build   # exit 0, 48 bundles

# Release checks
#   routes 97/29/75/201 · analytics 0 MOCK · trading mutation FAILS then reverts clean
#   WS: anonymous/spoofed/query/forged → 403 ; cookie & subprotocol → CONNECTED
#   Redis down → serving ; Mongo down → ready 503, live 200 ; stop → 2s exit 0
```
